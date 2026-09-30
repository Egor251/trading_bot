"""Ops monitor — heartbeats, data gaps, alerts, trade journal hooks (todo2 §5)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from microkernel_core import BaseService, KernelEvent

from engine.ops_safety import (
    AlertBus,
    AlertSeverity,
    DataGapDetector,
    HeartbeatWatchdog,
    TradeJournal,
)
from engine.performance_monitor import PerformanceMonitor, PerformanceTargets
from engine.runtime import get_runtime
from host.events import publish_ops_alert

if TYPE_CHECKING:
    from microkernel_core import MicrokernelCore


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

    def default_config(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "auto_start": True,
            "heartbeat_timeout_sec": 45.0,
            "heartbeat_interval_sec": 10.0,
            "expected_candle_sec": 60.0,
            "freeze_on_data_anomaly": True,
            "diagnose_every_sec": 60.0,
            "alerts_path": "data/ops/alerts.jsonl",
            "journal_path": "data/ops/trades.jsonl",
            "telegram_enabled": False,
            "telegram_bot_token": "",
            "telegram_chat_id": "",
            "watch_components": [
                "trading_runtime_service",
                "universe_service",
                "ui_bridge_service",
            ],
        }

    async def on_initialize(self) -> None:
        root = Path(__file__).resolve().parents[2]
        alerts_path = root / str(self.config.get("alerts_path") or "data/ops/alerts.jsonl")
        journal_path = root / str(self.config.get("journal_path") or "data/ops/trades.jsonl")
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
            expected_interval_sec=float(self.config.get("expected_candle_sec") or 60)
        )
        self._freeze_on_anomaly = bool(self.config.get("freeze_on_data_anomaly", True))
        self._diagnose_every_sec = float(self.config.get("diagnose_every_sec") or 60.0)
        trading = dict(await self.core.config.get("trading", {}) or {})
        perf = dict(trading.get("performance") or {})
        self.performance = PerformanceMonitor(
            PerformanceTargets(
                daily_target=float(perf.get("daily_target", 200.0)),
                daily_minimum=float(perf.get("daily_minimum", 100.0)),
                underperform_days=int(perf.get("underperform_days", 3)),
            )
        )
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
            if now - self._last_diagnose_ts >= self._diagnose_every_sec:
                self._last_diagnose_ts = now
                try:
                    await self.evaluate_phantom_vs_live()
                except Exception:
                    pass
            await asyncio.sleep(interval)

    async def _publish_alert(self, alert: Any) -> None:
        try:
            instance_id = str(
                await self.core.config.get("instance.id", "local") or "local"
            )
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
        ts_raw = payload.get("ts") or payload.get("timestamp") or time.time()
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
        if not issues:
            return
        alert = self.alerts.emit(
            "market_data_anomaly",
            f"{symbol}: {', '.join(issues)}",
            severity=AlertSeverity.WARNING,
            details={"symbol": symbol, "issues": issues},
        )
        await self._publish_alert(alert)
        if self._freeze_on_anomaly and "gap" in issues:
            runtime = get_runtime(self.core)
            if runtime is not None and not runtime.halted:
                runtime.halt("market_data_gap")

    async def _on_fill(self, event: KernelEvent) -> None:
        payload = event.payload or {}
        order = payload.get("order") if isinstance(payload.get("order"), dict) else {}
        status = str(
            payload.get("status") or order.get("status") or ""
        ).lower()
        success = payload.get("success")
        if status not in {"filled", "partial", "accepted"} and success is not True:
            return
        symbol = str(
            payload.get("symbol")
            or payload.get("ticker")
            or order.get("ticker")
            or ""
        )
        side = str(
            payload.get("side")
            or payload.get("action")
            or order.get("action")
            or ""
        )
        try:
            qty = float(
                payload.get("filled_qty")
                or payload.get("quantity")
                or order.get("quantity")
                or 0
            )
            price = float(
                payload.get("avg_price")
                or payload.get("price")
                or order.get("price")
                or 0
            )
            fee = float(
                payload.get("fee")
                or payload.get("cost")
                or order.get("cost")
                or 0
            )
        except (TypeError, ValueError):
            qty, price, fee = 0.0, 0.0, 0.0
        sid = str(
            payload.get("strategy_id")
            or order.get("strategy_id")
            or ""
        )
        if self.journal is not None:
            self.journal.record(
                symbol=symbol,
                side=side,
                quantity=qty,
                price=price,
                strategy_id=sid,
                reason=str(payload.get("reason") or order.get("reason") or "fill"),
                fee=fee,
            )
        # Rough signed trade pnl proxy for performance monitor.
        from engine.research import estimate_fill_pnl

        pnl = estimate_fill_pnl(payload)
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
        if (
            not force
            and not self.performance.needs_diagnostics()
            and phantom_pnl is None
        ):
            return {"ok": True, "skipped": True}
        diagnosis = self.performance.classify(
            phantom_pnl=phantom_pnl,
            live_pnl=live_pnl,
            regime_changed=regime_changed,
        )
        applied: dict[str, Any] = {}
        if ens is not None and hasattr(ens, "apply_diagnosis"):
            applied = await ens.apply_diagnosis(diagnosis)
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
        }

    async def get_status(self) -> dict[str, Any]:
        status = await super().get_status()
        status.update(
            {
                "watchdog": self.watchdog.snapshot() if self.watchdog else None,
                "alerts": [a.to_dict() for a in (self.alerts.recent(20) if self.alerts else [])],
                "performance": self.performance.snapshot() if self.performance else None,
                "journal_rows": len(self.journal.entries()) if self.journal else 0,
                "day_pnl": self._day_pnl,
            }
        )
        return status
