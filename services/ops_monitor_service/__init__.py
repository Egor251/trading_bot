"""Ops monitor — heartbeats, data gaps, alerts, trade journal hooks (todo2 §5)."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

from microkernel_core import BaseService, KernelEvent

from engine.cost_calibration import (
    ExecutionObservation,
    calibrate,
    cost_model_from_config,
)
from engine.ops_safety import (
    AlertBus,
    AlertSeverity,
    DataGapDetector,
    HeartbeatWatchdog,
    TradeJournal,
)
from engine.performance_monitor import PerformanceMonitor, PerformanceTargets
from engine.strategy_quarantine import QuarantinePolicy, StrategyQuarantine


def _quarantine_policy(raw: dict) -> QuarantinePolicy:
    """Build the breaker policy from config, falling back on nonsense.

    `restore_above` defaults above `quarantine_below` rather than equal to it:
    an equal pair is a silent permanent lockout, which is the opposite of what
    a circuit breaker is for.
    """
    below = float(raw.get("quarantine_below", 0.0))
    return QuarantinePolicy(
        enabled=bool(raw.get("enabled", True)),
        quarantine_below=below,
        restore_above=float(raw.get("restore_above", below + 0.05)),
        consecutive_failures=max(1, int(raw.get("consecutive_failures", 3))),
        min_samples=max(1, int(raw.get("min_samples", 20))),
    ).validated()


from engine.runtime import get_runtime
from host.events import publish_ops_alert
from engine.paths import data_dir, data_path


def _log():
    import logging

    return logging.getLogger("ops_monitor")


if TYPE_CHECKING:
    from microkernel_core import MicrokernelCore


def _shares_margin(runtime: Any, open_keys: list[str]) -> list[str]:
    """Derivative positions whose account collateral is shared, not isolated.

    Cross margin means simultaneous losses anywhere in the account consume the
    margin a stop was counting on, so a per-slice stop bounds one position only
    while the rest of the book is calm. `unknown` is treated as sharing: a
    guarantee nobody verified should not be reported as a guarantee, and the
    absence of the information is the riskier state to assume away.
    """
    registry = getattr(runtime, "instruments", None) or getattr(
        runtime, "instrument_registry", None
    )
    shared: list[str] = []
    for key in open_keys:
        symbol = str(key).split(":")[-1]
        spec = None
        if registry is not None and hasattr(registry, "get"):
            try:
                spec = registry.get(symbol)
            except Exception:  # noqa: BLE001 - absence is not an error here
                spec = None
        if spec is None:
            spec = _spec_from_instruments_file(symbol)
        if spec is None:
            continue
        model = str(getattr(spec, "margin_model", "") or "").lower()
        mode = str(getattr(spec, "margin_mode", "") or "unknown").lower()
        if model == "derivative" and mode != "isolated":
            shared.append(key)
    return shared


def _spec_from_instruments_file(symbol: str) -> Any | None:
    """Best-effort lookup so the audit works without a live registry."""
    from engine.paths import data_dir  # noqa: PLC0415

    try:
        raw = json.loads((data_dir() / "instruments.json").read_text("utf-8"))
    except Exception:  # noqa: BLE001 - absence is not an error here
        return None
    for row in raw if isinstance(raw, list) else []:
        if isinstance(row, dict) and str(row.get("symbol") or "") == symbol:
            return row
    return None


class OpsMonitorService(BaseService):
    """
    Lightweight operational layer:

    - component heartbeats (freeze trading on dead components when configured)
    - candle gap / duplicate detection
    - alert bus (JSONL; Telegram sink can be attached later)
    - optional day PnL feed into PerformanceMonitor on paper runtime
    """

    def __init__(self, core: MicrokernelCore, component_id: str) -> None:
        super().__init__(core, component_id)
        self.alerts: AlertBus | None = None
        self.watchdog: HeartbeatWatchdog | None = None
        self.gaps: DataGapDetector | None = None
        self.journal: TradeJournal | None = None
        self.performance: PerformanceMonitor | None = None
        self._task: asyncio.Task[None] | None = None
        self._freeze_on_anomaly = True
        self._day_pnl: float = 0.0
        self._day_key: str = ""
        self._diagnose_every_sec: float = 60.0
        self._last_diagnose_ts: float = 0.0
        #: How often to ask whether every open position still carries a stop.
        self._protection_audit_sec: float = 30.0
        self._last_protection_audit_ts: float = 0.0

    def default_config(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "auto_start": True,
            "heartbeat_timeout_sec": 45.0,
            "heartbeat_interval_sec": 10.0,
            "expected_candle_sec": 60.0,
            "freeze_on_data_anomaly": True,
            "diagnose_every_sec": 60.0,
            "alerts_path": str(data_path("ops", "alerts.jsonl")),
            "journal_path": str(data_path("ops", "trades.jsonl")),
            "telegram_enabled": False,
            "telegram_bot_token": "",
            "telegram_chat_id": "",
            "watch_components": [
                "trading_runtime_service",
                "universe_service",
                "ui_bridge_service",
                # The market connectors were missing here, so a dead feed had no
                # heartbeat at all — `component_dead` could not fire for the one
                # component whose death matters most.
                "ccxt_market_service",
                "quik_market_service",
                "alpaca_market_service",
                "mock_market_service",
            ],
            #: How long a feed may stay quiet before the anomaly is treated as
            #: real (and before we consider auto-resuming a data freeze).
            "stale_feed_sec": 180.0,
            #: Consecutive clean observations required before a data freeze is
            #: lifted. 0 disables auto-resume entirely (manual resume only).
            "auto_resume_after_clean_bars": 3,
            #: Emit `/metrics` from the UI bridge.
            "prometheus_enabled": True,
        }

    async def on_initialize(self) -> None:
        root = Path(__file__).resolve().parents[2]
        alerts_path = root / str(
            self.config.get("alerts_path")
            or data_path("ops", "alerts.jsonl").relative_to(data_dir())
        )
        journal_path = root / str(
            self.config.get("journal_path")
            or data_path("ops", "trades.jsonl").relative_to(data_dir())
        )
        self.alerts = AlertBus(path=alerts_path)
        tg_token = str(self.config.get("telegram_bot_token") or "").strip()
        tg_chat = str(self.config.get("telegram_chat_id") or "").strip()
        if tg_token or tg_chat or bool(self.config.get("telegram_enabled")):
            from engine.ops_safety import attach_telegram_sink

            attach_telegram_sink(
                self.alerts,
                bot_token=tg_token,
                chat_id=tg_chat,
            )
        self.journal = TradeJournal(journal_path)
        self.watchdog = HeartbeatWatchdog(
            timeout_sec=float(self.config.get("heartbeat_timeout_sec") or 45)
        )
        self.gaps = DataGapDetector(
            expected_interval_sec=float(self.config.get("expected_candle_sec") or 60),
            stale_after_sec=float(self.config.get("stale_feed_sec") or 180),
            stale_tolerance=max(1, int(self.config.get("stale_tolerance") or 3)),
        )
        self._freeze_on_anomaly = bool(self.config.get("freeze_on_data_anomaly", True))
        self._auto_resume_bars = int(self.config.get("auto_resume_after_clean_bars") or 0)
        self._clean_bars = 0
        self._frozen_for_data = False
        self._diagnose_every_sec = float(self.config.get("diagnose_every_sec") or 60.0)
        # Cost reconciliation on a schedule, which is the whole reason this module
        # exists: `nightly_fee_reconciliation` and `calibrate` were both correct,
        # tested, and unreachable, so every fill charged the model number and the
        # model's error was invisible. Same defect `adjust_pvalues` had.
        self._cost_reconcile_every_sec = float(
            self.config.get("cost_reconcile_every_sec") or 3600.0
        )
        self._last_cost_reconcile_ts = 0.0
        self._cost_reconcile_min_samples = int(self.config.get("cost_reconcile_min_samples") or 30)
        # The cost model is the plugin's own block, read by dotted path the way the
        # rest of this service reads config -- `config.get()` takes a key and
        # passing none is a TypeError, not "give me everything".
        self._cost_model = cost_model_from_config(
            dict((await self.core.config.get("services.phantom_service", {})) or {})
        )
        # Bounded, because this is a sample of recent fills and an unbounded list
        # in a long-running process is a leak dressed as a feature.
        self._fill_samples: deque[ExecutionObservation] = deque(
            maxlen=int(self.config.get("cost_reconcile_window") or 500)
        )
        self._cost_report: dict[str, Any] | None = None
        self._protection_audit_sec = float(self.config.get("protection_audit_sec") or 30.0)
        # The Prometheus exporter existed but nothing imported it, so all ten
        # alerts in ops/prometheus/alerts.yml could never fire.
        from engine.observability import OperationsObservability

        self.observability = OperationsObservability()
        setattr(self.core, "operations_observability", self.observability)
        trading = dict(await self.core.config.get("trading", {}) or {})
        perf = dict(trading.get("performance") or {})
        self.performance = PerformanceMonitor(
            PerformanceTargets(
                daily_target=float(perf.get("daily_target", 200.0)),
                daily_minimum=float(perf.get("daily_minimum", 100.0)),
                underperform_days=int(perf.get("underperform_days", 3)),
            )
        )
        # Per-strategy circuit breaker. The portfolio diagnosis above can only
        # cut weights and scale risk; a member at 0.25 weight is still trading.
        # This is the actuator that can actually take a degraded strategy out of
        # the live lineup while leaving it observable.
        self.quarantine = StrategyQuarantine(_quarantine_policy(dict(perf.get("quarantine") or {})))
        setattr(self.core, "ops", self)

    async def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        await self.core.event_bus.subscribe("market.candle", self._on_candle)
        await self.core.event_bus.subscribe("execution.result", self._on_fill)
        self._task = asyncio.create_task(self._heartbeat_loop(), name="ops_heartbeat")

    async def stop(self) -> None:
        self.is_running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        for et, handler in (
            ("market.candle", self._on_candle),
            ("execution.result", self._on_fill),
        ):
            try:
                await self.core.event_bus.unsubscribe(et, handler)
            except Exception:
                pass

    async def _heartbeat_loop(self) -> None:
        interval = float(self.config.get("heartbeat_interval_sec") or 10)
        components = list(self.config.get("watch_components") or [])
        while self.is_running:
            assert self.watchdog is not None and self.alerts is not None
            self.watchdog.beat("ops_monitor_service")
            for name in components:
                svc = self.core.service_registry.get(str(name))
                if svc is not None and getattr(svc, "is_running", False):
                    self.watchdog.beat(str(name))
            dead = self.watchdog.dead()
            if dead:
                alert = self.alerts.emit(
                    "component_dead",
                    f"components without heartbeat: {', '.join(dead)}",
                    severity=AlertSeverity.CRITICAL,
                    details={"dead": dead},
                )
                await self._publish_alert(alert)
                if self._freeze_on_anomaly:
                    runtime = get_runtime(self.core)
                    if runtime is not None and not runtime.halted:
                        runtime.halt("ops_watchdog_dead_component")
            now = time.time()
            if now - self._last_protection_audit_ts >= self._protection_audit_sec:
                self._last_protection_audit_ts = now
                try:
                    await self.audit_position_protection()
                except Exception:
                    pass
            if now - self._last_diagnose_ts >= self._diagnose_every_sec:
                self._last_diagnose_ts = now
                try:
                    await self.evaluate_phantom_vs_live()
                except Exception:
                    pass
            if now - self._last_cost_reconcile_ts >= self._cost_reconcile_every_sec:
                self._last_cost_reconcile_ts = now
                try:
                    report = await self.reconcile_costs()
                except Exception:
                    report = None
                if report and report.get("understates"):
                    # CRITICAL because every P&L derived from this cost model is
                    # wrong in the optimistic direction, invisibly.
                    alert = self.alerts.emit(
                        "cost_model_understates",
                        (
                            f"observed p90 slippage {report.get('observed_p90_bps')} bps "
                            f"vs model {report.get('model_slippage_bps')} bps over "
                            f"{report.get('samples')} fills"
                        ),
                        severity=AlertSeverity.HIGH
                        if report.get("sufficient")
                        else AlertSeverity.CRITICAL,
                        details={
                            "report": report,
                            "model": report.get("model"),
                        },
                    )
                    await self._publish_alert(alert)
            # Publish the health state file. This is what the container
            # HEALTHCHECK reads; without a writer the probe could only check
            # that PID 1 was still alive.
            try:
                from engine.health_state import write_health_state

                write_health_state(
                    runtime=get_runtime(self.core),
                    gaps=self.gaps,
                    now=now,
                    stale_feed_sec=float(self.config.get("stale_feed_sec") or 180),
                )
            except Exception:
                _log().exception("health state write failed")
            await asyncio.sleep(interval)

    async def _publish_alert(self, alert: Any) -> None:
        """Publish an alert. ``None`` means the bus suppressed a repeat."""
        if alert is None:
            return
        try:
            instance_id = str(await self.core.config.get("instance.id", "local") or "local")
        except Exception:
            instance_id = "local"
        body = alert.to_dict() if hasattr(alert, "to_dict") else dict(alert or {})
        await publish_ops_alert(
            self.core.event_bus,
            source_id=self.component_id,
            payload=body,
            instance_id=instance_id,
        )

    async def _on_candle(self, event: KernelEvent) -> None:
        if self.gaps is None or self.alerts is None:
            return
        payload = event.payload or {}
        symbol = str(payload.get("ticker") or payload.get("symbol") or "")
        if not symbol:
            return
        # Exchange bar timestamp. `market.candle` now publishes `ts`; without it
        # this fell back to `time.time()` and the detector measured the polling
        # loop, not the data.
        ts_raw = payload.get("ts") or payload.get("timestamp")
        if ts_raw is None:
            ts = time.time()
        else:
            try:
                ts = float(ts_raw)
                if ts > 1e12:
                    ts = ts / 1000.0
            except (TypeError, ValueError):
                ts = time.time()
        vol = payload.get("volume")
        try:
            volume = float(vol) if vol is not None else None
        except (TypeError, ValueError):
            volume = None
        issues = self.gaps.observe(symbol, ts, volume=volume)
        obs = getattr(self, "observability", None)
        if obs is not None:
            try:
                obs.feed_last_seen.set(
                    float(self.gaps.feed_age(symbol) or 0.0),
                    {"feed": str(payload.get("source") or "market")},
                )
            except Exception:  # noqa: BLE001 - metrics must never break the loop
                pass
        if not issues:
            if self._frozen_for_data:
                self._clean_bars += 1
                if self._auto_resume_bars and self._clean_bars >= self._auto_resume_bars:
                    await self._lift_data_freeze("feed_recovered")
            return
        alert = self.alerts.emit(
            "market_data_anomaly",
            f"{symbol}: {', '.join(issues)}",
            severity=AlertSeverity.WARNING,
            details={"symbol": symbol, "issues": issues},
        )
        await self._publish_alert(alert)
        # Freeze on a *stale* feed, not on an inter-bar gap. A gap between two
        # exchange bars is normal after a reconnect; a feed whose newest bar is
        # minutes old is the condition that actually makes trading unsafe.
        # The old code froze on "gap" and never lifted it, which is how the bot
        # ended up halted for a month with no alert.
        if self._freeze_on_anomaly and "stale" in issues:
            runtime = get_runtime(self.core)
            if runtime is not None and not runtime.halted:
                runtime.halt("market_data_gap")
                self._frozen_for_data = True
                self._clean_bars = 0
                _log().critical(
                    "froze risk: feed stale for %s (halt_reason=market_data_gap). "
                    "Auto-resume after %s clean bars.",
                    symbol,
                    self._auto_resume_bars or "never (manual resume required)",
                )

    async def _lift_data_freeze(self, reason: str) -> None:
        """Release a data-only halt once the feed has proven itself."""
        self._frozen_for_data = False
        self._clean_bars = 0
        runtime = get_runtime(self.core)
        if runtime is None or not runtime.halted:
            return
        if str(getattr(runtime, "_halt_reason", "") or "") != "market_data_gap":
            # Halted for a different reason (risk, operator) — leave it alone.
            return
        runtime.resume()
        _log().warning("resumed after data feed recovered (%s)", reason)
        alert = self.alerts.emit(
            "market_data_recovered",
            f"feed recovered ({reason}); risk resumed",
            severity=AlertSeverity.WARNING,
            details={"reason": reason},
        )
        await self._publish_alert(alert)

    def _sample_fill(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        fee: float,
        payload: dict[str, Any],
        order: dict[str, Any],
    ) -> None:
        """Keep this fill as evidence for the cost reconciliation.

        Only fills that carry a reference price and a real venue fee are kept.
        Implementation shortfall is measured against the price the decision was
        made on -- "the strategy acted because of this price" -- and a fill without
        one can only supply a fee delta. Storing it anyway and treating the missing
        half as zero would report slippage of exactly zero for those fills and drag
        every percentile down, so those fills are dropped instead and the sample
        count says how many were usable.
        """
        reference = payload.get("reference_price") or order.get("reference_price")
        if reference is None or quantity <= 0 or price <= 0:
            return
        _liquidity = str(
            payload.get("liquidity") or order.get("liquidity")
            or order.get("takerOrMaker") or ""
        ).strip().lower()
        try:
            from engine.cost_calibration import cost_model_from_config

            _maker_cost_model = cost_model_from_config(
                getattr(self.core.config, "raw", None), liquidity="maker"
            )
        except Exception:  # noqa: BLE001
            _maker_cost_model = self._cost_model
        notional = abs(quantity) * price
        if notional <= 0:
            return
        try:
            self._fill_samples.append(
                ExecutionObservation(
                    fill_id=str(
                        payload.get("fill_id") or payload.get("trade_id") or payload.get("id") or ""
                    ),
                    venue=str(payload.get("venue") or order.get("venue") or ""),
                    symbol=symbol,
                    side=side,
                    quantity=abs(quantity),
                    reference_price=float(reference),
                    fill_price=price,
                    notional=notional,
                    expected_cost=notional
                    * (
                        (_maker_cost_model if _liquidity in {"maker", "post_only", "postonly"}
                         else self._cost_model)["fee_bps"]
                        + (_maker_cost_model if _liquidity in {"maker", "post_only", "postonly"}
                           else self._cost_model)["slippage_bps"]
                    )
                    / 10_000.0,
                    actual_fee=fee,  # negative maker rebates are evidence, not noise
                )
            )
        except (TypeError, ValueError):
            return

    async def reconcile_costs(self) -> dict[str, Any]:
        """Compare what execution actually cost against the configured model.

        Runs on a schedule rather than never. The report states `sufficient: false`
        and explains itself when there is not enough evidence, instead of reporting a
        confident number built from a handful of fills -- acting on ten trades would
        replace a wrong constant with a differently wrong one.
        """
        rows = list(self._fill_samples)
        model_slip = self._cost_model["slippage_bps"]
        needed = self._cost_reconcile_min_samples
        report = calibrate(
            rows,
            model_slippage_bps=model_slip,
            minimum_samples=needed,
        )
        body = report.to_dict()
        body.update(
            {
                "sufficient": len(rows) >= needed,
                "min_samples": needed,
                "model": dict(self._cost_model),
                "observed_p90_slippage_bps": body.get("observed_p90_bps"),
                "sampled_fills": len(rows),
            }
        )
        if not body["sufficient"]:
            body["note"] = (
                f"{len(rows)} usable fill(s), need {needed}. "
                "Fills without a reference price cannot measure slippage and are "
                "excluded, so this stays silent rather than reporting zero."
            )
        self._cost_report = body
        if body.get("understates"):
            body["alert"] = "cost_model_understates"
        return body

    async def _on_fill(self, event: KernelEvent) -> None:
        payload = event.payload or {}
        order = payload.get("order") if isinstance(payload.get("order"), dict) else {}
        status = str(payload.get("status") or order.get("status") or "").lower()
        success = payload.get("success")
        if status not in {"filled", "partial", "accepted"} and success is not True:
            return
        symbol = str(payload.get("symbol") or payload.get("ticker") or order.get("ticker") or "")
        side = str(payload.get("side") or payload.get("action") or order.get("action") or "")
        try:
            qty = float(
                payload.get("filled_qty") or payload.get("quantity") or order.get("quantity") or 0
            )
            price = float(
                payload.get("avg_price") or payload.get("price") or order.get("price") or 0
            )
            fee = float(payload.get("fee") or payload.get("cost") or order.get("cost") or 0)
        except (TypeError, ValueError):
            qty, price, fee = 0.0, 0.0, 0.0
        sid = str(payload.get("strategy_id") or order.get("strategy_id") or "")
        # The in-memory observation has a reference price; the journal row did
        # not. Persist it in the existing meta field so calibration survives a
        # restart. A fill without one stays usable only for fee accounting,
        # never for a fabricated zero-slippage sample.
        meta: dict[str, Any] = {}
        reference = payload.get("reference_price") or order.get("reference_price")
        try:
            if reference is not None and float(reference) > 0:
                meta["reference_price"] = float(reference)
        except (TypeError, ValueError):
            pass
        if fee > 0:
            meta["reported_fee"] = float(fee)
        if fee != 0:
            meta["actual_fee"] = float(fee)  # negative maker rebates stay visible
        if self.journal is not None:
            self.journal.record(
                symbol=symbol,
                side=side,
                quantity=qty,
                price=price,
                strategy_id=sid,
                reason=str(payload.get("reason") or order.get("reason") or "fill"),
                fee=fee,
                meta=meta,
            )
        self._sample_fill(
            symbol=symbol,
            side=side,
            quantity=qty,
            price=price,
            fee=fee,
            payload=payload,
            order=order,
        )
        # Only an explicitly reported PnL belongs in the performance record.
        # The directional proxy used for display would net a round trip to
        # zero while the account paid both fills of fees.
        from engine.research import actual_fill_pnl

        pnl = actual_fill_pnl(payload)
        if pnl is not None and self.performance is not None:
            self.performance.record_trade(float(pnl))
            self._bump_day_pnl(float(pnl))

    def _bump_day_pnl(self, pnl: float) -> None:
        from datetime import UTC, datetime

        key = datetime.now(UTC).date().isoformat()
        if key != self._day_key:
            if self._day_key and self.performance is not None:
                self.performance.record_day(self._day_pnl)
            self._day_key = key
            self._day_pnl = 0.0
        self._day_pnl += float(pnl)

    def _phantom_day_pnl(self) -> float | None:
        """Single source of truth: phantom runtime equity (same as phantom status)."""
        phantom = self.core.service_registry.get("phantom_service")
        if phantom is None:
            return None
        try:
            # Prefer public status metrics (never the broken multi-symbol fill approx).
            if hasattr(phantom, "_metrics"):
                metrics = phantom._metrics()  # noqa: SLF001
                if (
                    isinstance(metrics, dict)
                    and metrics.get("pnl") is not None
                    and str(metrics.get("source") or "") != "fills_fallback"
                ):
                    return float(metrics["pnl"])
            runtime = getattr(phantom, "runtime", None) or getattr(self.core, "phantom", None)
            if runtime is not None and hasattr(runtime, "orders"):
                start_eq = float(
                    (getattr(phantom, "config", {}) or {}).get("start_equity")
                    or getattr(runtime.orders, "starting_equity", 0)
                    or 10_000
                )
                return float(runtime.orders.equity) - start_eq
            last = getattr(phantom, "_last_run", None) or {}
            if isinstance(last, dict) and last.get("pnl") is not None:
                return float(last["pnl"])
            return None
        except Exception:
            return None

    def _live_day_pnl(self) -> float | None:
        runtime = get_runtime(self.core)
        if runtime is None:
            return self._day_pnl if self._day_key else None
        snap = None
        if hasattr(runtime, "snapshot"):
            try:
                snap = runtime.snapshot()
            except Exception:
                snap = None
        if isinstance(snap, dict):
            for key in ("day_pnl", "realized_pnl", "pnl"):
                if snap.get(key) not in (None, ""):
                    try:
                        return float(snap[key])
                    except (TypeError, ValueError):
                        pass
            portfolio = snap.get("portfolio") if isinstance(snap.get("portfolio"), dict) else {}
            if portfolio.get("pnl") not in (None, ""):
                try:
                    return float(portfolio["pnl"])
                except (TypeError, ValueError):
                    pass
        return self._day_pnl if self._day_key else None

    async def evaluate_phantom_vs_live(self, *, force: bool = False) -> dict[str, Any]:
        """Classify phantom vs live and push Diagnosis into Consilium autoweight."""
        if self.performance is None:
            return {"ok": False, "error": "performance_missing"}
        phantom_pnl = self._phantom_day_pnl()
        live_pnl = self._live_day_pnl()
        regime_changed = False
        ens = self.core.service_registry.get("ensemble_service")
        if ens is not None:
            regime = str(getattr(ens, "_last_regime", "") or "")
            prev = str((getattr(ens, "_last_diagnosis", None) or {}).get("regime") or "")
            if regime and prev and regime != prev:
                regime_changed = True
        if not force and not self.performance.needs_diagnostics() and phantom_pnl is None:
            return {"ok": True, "skipped": True}
        diagnosis = self.performance.classify(
            phantom_pnl=phantom_pnl,
            live_pnl=live_pnl,
            regime_changed=regime_changed,
        )
        applied: dict[str, Any] = {}
        if ens is not None and hasattr(ens, "apply_diagnosis"):
            applied = await ens.apply_diagnosis(diagnosis)
        quarantined = await self._apply_quarantine(ens)
        if self.alerts is not None and diagnosis.kind.value != "healthy":
            alert = self.alerts.emit(
                "performance_diagnosis",
                diagnosis.summary,
                severity=AlertSeverity.WARNING,
                details=diagnosis.to_dict(),
            )
            await self._publish_alert(alert)
        return {
            "ok": True,
            "diagnosis": diagnosis.to_dict(),
            "phantom_pnl": phantom_pnl,
            "live_pnl": live_pnl,
            "ensemble": applied,
            "quarantine": quarantined,
        }

    async def audit_position_protection(self) -> dict[str, Any]:
        """Report, loudly, any open position that carries no protective slice.

        Stop coverage was already a gauge on the runtime snapshot -- a number for
        a dashboard, checked by nobody. A position without a stop is the single
        largest unbounded loss available to this system, so it is treated here as
        an incident rather than a metric.

        On a venue outage this deliberately does not try to close anything: the
        venue being unreachable is exactly why a market order cannot be placed,
        and pretending otherwise would convert a known exposure into an
        unverified one. What it can do is make the gap unmissable and durable.
        """
        runtime = get_runtime(self.core)
        if runtime is None:
            return {"skipped": "no_runtime"}
        try:
            open_keys = list(runtime.open_position_keys())
        except Exception:  # noqa: BLE001
            return {"skipped": "runtime_unavailable"}
        if not open_keys:
            return {"checked": 0, "unprotected": [], "coverage": 1.0}

        protection = getattr(runtime, "protection", None)
        if protection is None:
            return {"skipped": "no_protection_layer"}
        unprotected = [key for key in open_keys if not protection.has_protection(key)]
        # A stop the venue will not honour as reduce-only protects against loss
        # but can reverse the book if it triggers flat. That is not the same as
        # having no stop, and it is not the same as having a good one, so it is
        # counted separately rather than folded into coverage as if equal.
        weak = [key for key in open_keys if key in getattr(runtime, "_native_stop_weak_spots", {})]
        coverage = (len(open_keys) - len(unprotected)) / len(open_keys)
        # Strict coverage excludes weak stops. Reported next to the plain number
        # rather than instead of it: both are true, and the gap between them is
        # the size of the reversal risk.
        full_coverage = (
            (len(open_keys) - len(unprotected) - len(weak)) / len(open_keys) if open_keys else 1.0
        )
        # Cross margin shares collateral, so a per-slice stop bounds one
        # position only while the rest of the account is calm. The protection
        # layer reasons per slice, which quietly overstates the guarantee on a
        # multi-position derivative book. Surfaced rather than blocked: which
        # mode an account runs in is the venue's setting and the operator's
        # choice, and nothing here can change it from inside the bot.
        shared_margin = _shares_margin(runtime, open_keys)

        if unprotected and self.alerts is not None:
            alert = self.alerts.emit(
                "unprotected_position",
                f"{len(unprotected)} of {len(open_keys)} positions carry no stop",
                severity=AlertSeverity.CRITICAL,
                details={
                    "unprotected": unprotected,
                    "coverage": coverage,
                    "total_costs_note": "coverage is stop-bearing positions / open",
                },
            )
            await self._publish_alert(alert)
        if weak and self.alerts is not None:
            # A WARNING, not a CRITICAL: there *is* a stop, so the loss is still
            # bounded. What is missing is the guarantee it cannot reverse the
            # book. Saying CRITICAL would flatten the distinction between "no
            # stop at all" and "a stop with a known hole".
            alert = self.alerts.emit(
                "weak_protective_stop",
                f"{len(weak)} of {len(open_keys)} stops cannot guarantee reduce-only at the venue",
                severity=AlertSeverity.WARNING,
                details={
                    "weak": weak,
                    "reasons": {
                        key: getattr(runtime, "_native_stop_weak_spots", {})[key] for key in weak
                    },
                    "full_coverage": full_coverage,
                },
            )
            await self._publish_alert(alert)
        return {
            "checked": len(open_keys),
            "unprotected": unprotected,
            "coverage": coverage,
            "weak": weak,
            "full_coverage": full_coverage,
            "shared_margin": shared_margin,
        }

    async def _apply_quarantine(self, ens: Any) -> dict[str, Any]:
        """Judge each live member on its own outcomes and demote the degraded.

        Demotion sets `observe_only`, so the strategy keeps receiving candles
        and keeps accumulating evidence, and the decision is reversible when its
        mean recovers past the restore threshold.
        """
        if self.quarantine is None or ens is None:
            return {"skipped": True}
        manager = getattr(ens, "manager", None)
        stats = getattr(manager, "stats", None)
        lineup = list(getattr(ens, "lineup_ids", list)() or [])
        if not lineup or stats is None:
            return {"skipped": True}

        try:
            verdicts = self.quarantine.evaluate(lineup, stats)
        except Exception as exc:  # noqa: BLE001 - a breaker must never stop the loop
            return {"error": f"{type(exc).__name__}: {exc}"[:120]}

        changed: list[dict[str, Any]] = []
        for verdict in verdicts:
            sid = verdict.strategy_id
            if verdict.reason in {"degraded", "recovered"}:
                plugin = self.core.plugin_manager.get(sid) if self.core.plugin_manager else None
                if plugin is None:
                    continue
                config = getattr(plugin, "config", None)
                if not isinstance(config, dict):
                    continue
                if verdict.reason == "degraded":
                    config["observe_only"] = True
                elif config.get("observe_only") and self.quarantine.is_quarantined(sid):
                    continue
                else:
                    config["observe_only"] = False
                changed.append(verdict.to_dict())
                await self._publish_quarantine_alert(verdict, config["observe_only"])
        return {
            "state": self.quarantine.to_dict(),
            "changed": changed,
            "verdicts": [v.to_dict() for v in verdicts],
        }

    async def _publish_quarantine_alert(self, verdict: Any, observe_only: bool) -> None:
        """A strategy being stopped must never be a silent event."""
        if self.alerts is None:
            return
        alert = self.alerts.emit(
            "strategy_quarantined" if observe_only else "strategy_restored",
            (
                f"{verdict.strategy_id} "
                + ("stopped trading (degraded)" if observe_only else "resumed trading")
            ),
            severity=AlertSeverity.CRITICAL if observe_only else AlertSeverity.INFO,
            details=verdict.to_dict(),
        )
        await self._publish_alert(alert)

    async def get_status(self) -> dict[str, Any]:
        status = await super().get_status()
        status.update(
            {
                "watchdog": self.watchdog.snapshot() if self.watchdog else None,
                "alerts": [a.to_dict() for a in (self.alerts.recent(20) if self.alerts else [])],
                "performance": self.performance.snapshot() if self.performance else None,
                "journal_rows": len(self.journal.entries()) if self.journal else 0,
                "day_pnl": self._day_pnl,
                # Present from the first call, not only after a reconciliation has
                # run, so an operator can see the model in force and how much
                # evidence exists for it rather than an absent key.
                "cost_reconciliation": self._cost_report,
                "cost_model": dict(self._cost_model),
                "cost_samples": len(self._fill_samples),
            }
        )
        return status
