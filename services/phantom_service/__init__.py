"""Historical phantom / shadow ledger — separate from paper TradingRuntime."""

from __future__ import annotations

import asyncio
import logging
import uuid
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from microkernel_core import BaseService, KernelEvent

from engine.costs import TradeContext
from engine.indicators import equity_pnl, sma
from engine.market import Candle, candle_to_market_payload
from engine.order import OrderStatus
from engine.phantom_patterns import (
    PatternSpec,
    default_pattern_specs,
    generate_pattern_candles,
    list_patterns,
    ohlc_dict_from_candles,
)
from engine.phantom_timeline import PhantomTimeline
from engine.position_sizing import (
    SizingConfig,
    SizingMode,
    atr_from_bars,
    size_quantity,
)
from engine.runtime import TradingRuntime
from engine.state_store import StateStore
from host.events import publish_market_candle, publish_phantom_fill, publish_phantom_run
from engine.paths import data_dir, data_path


def _log() -> logging.Logger:
    return logging.getLogger("phantom")


def _series_bound_iso(series: Any, *, first: bool) -> str | None:
    """First or last timestamp of a bar series, as an ISO string.

    Used to stamp research digests with the window they were computed over.
    """
    if not isinstance(series, dict):
        return None
    ts = series.get("ts") or series.get("timestamp") or series.get("time")
    if not isinstance(ts, (list, tuple)) or not ts:
        return None
    value = ts[0] if first else ts[-1]
    try:
        if isinstance(value, (int, float)):
            from datetime import UTC, datetime

            seconds = float(value)
            # Bars carry milliseconds when the value is implausibly large.
            if seconds > 1e11:
                seconds /= 1000.0
            return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
        return str(value)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _reanchor(raw: str) -> str:
    """Map a relative ``data/...`` path onto the shared data root."""
    candidate = Path(raw)
    if candidate.is_absolute():
        return raw
    parts = candidate.parts
    if parts and parts[0] == "data":
        return str(data_dir().joinpath(*parts[1:]))
    return raw

if TYPE_CHECKING:
    from microkernel_core import MicrokernelCore

    # Imported at runtime inside `walk_forward_config` (to keep the walk-forward
    # import off the boot path); declared here so the quoted annotation resolves.
    from engine.walk_forward import WalkForwardConfig


class PhantomService(BaseService):
    """
    Shadow OrderManager on ``core.phantom``.

    Captures ``strategy.signal`` into phantom fills (does not touch live/paper runtime).
    ``run_grid``: sequential replay with ma_cross param variants → metrics.
    Honours active cost schedule / legacy fee_bps the same way as paper trading.
    """

    def __init__(self, core: MicrokernelCore, component_id: str) -> None:
        super().__init__(core, component_id)
        self.runtime: TradingRuntime | None = None
        self._fills: list[dict[str, Any]] = []
        self._last_run: dict[str, Any] | None = None
        self._last_grid: list[dict[str, Any]] = []
        self._last_walk_forward: dict[str, Any] | None = None
        self._last_batch: list[dict[str, Any]] = []
        self._last_ohlc: dict[str, Any] | None = None
        self._capturing = True
        self._fee_bps = 0.0
        self._slippage_bps = 0.0
        self._suppress_ensemble = False
        self._grid_running = False
        self._runtime_cfg: dict[str, Any] = {}
        self.timeline: PhantomTimeline | None = None
        self.state_store: StateStore | None = None
        # 0-based index of the last ingested bar; -1 means no bars yet.
        self._bar_index = -1
        #: Signals waiting for the next bar's open (fill_timing: next_open).
        self._pending_fills: list[tuple[dict[str, Any], str]] = []
        #: Signals raised on a final bar with no next bar to fill against, from the
        #: last grid run. Kept out of `results` so that list stays one shape.
        self._last_unfilled_signals: list[dict[str, Any]] = []
        #: Rejected shadow signals by normalised reason. Declared here as well as
        #: in `on_initialize` so any caller touching the counters before boot —
        #: a status readout, a test harness — cannot hit an AttributeError.
        self._shadow_rejects: dict[str, int] = {}
        #: Set when the previous process died mid-run with open phantom
        #: positions. Surfaced in `get_status` so an operator can see that
        #: research metrics spanning a restart are not comparable.
        self._interrupted_run: dict[str, Any] | None = None
        self._last_pattern: str | None = None
        self.sizing = SizingConfig()

    def default_config(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "auto_start": True,
            "fee_bps": 5.0,
            "slippage_bps": 2.0,
            "start_equity": 10_000.0,
            "capture_signals": True,
            # ensemble = shadow live consilium path; signals = raw strategy votes;
            # both = ensemble preferred, signals only when no ensemble payload.
            "capture_source": "ensemble",
            # next_open = a signal raised on bar N fills at bar N+1's open, which
            # is the first price a live run could actually trade at. bar_close
            # fills at bar N's close — the close the strategy just reacted to —
            # which flatters every walk-forward result. Keep bar_close only to
            # reproduce historical numbers; do not gate live promotion on it.
            "fill_timing": "next_open",
            "timeline_path": str(data_path("phantom", "timeline.jsonl")),
            "state_path": str(data_path("phantom", "state.json")),
            "default_feed": "history",  # history | patterns | both
            "pattern_bars": 80,
            "pattern_start_price": 100.0,
            "sizing_mode": SizingMode.PERCENT_OF_EQUITY.value,
            "sizing_value": 10.0,
            "stop_pct": 0.02,
            "atr_period": 14,
            "atr_mult": 2.0,
            "win_rate": 0.55,
            "reward_risk": 1.5,
            "kelly_fraction": 0.5,
            "max_position_pct": 0.25,
            "scale_by_strength": True,
            "strength_floor": 0.25,
            "strategy_sizing": {},
            "walk_forward": {
                "feed": "history",
                "timeframe": "1d",
                "seed": 42,
                "bars": 500,
                "train_bars": 60,
                "test_bars": 20,
                "min_folds": 5,
                "auto_fit_folds": False,
                "selection_mode": "top_k_stable",
                "top_k": 3,
                "min_fold_wins": 1,
                "bootstrap_samples": 1000,
                "bootstrap_ci": 0.95,
                "persist_report": True,
                "report_dir": str(data_path("phantom", "walk_forward")),
                "pattern": "sharp_rise",
            },
        }

    def sizing_from_config(
        self, overrides: dict[str, Any] | None = None
    ) -> SizingConfig:
        raw = {
            "mode": self.config.get("sizing_mode", SizingMode.PERCENT_OF_EQUITY.value),
            "value": self.config.get("sizing_value", 10.0),
            "stop_pct": self.config.get("stop_pct", 0.02),
            "atr_period": self.config.get("atr_period", 14),
            "atr_mult": self.config.get("atr_mult", 2.0),
            "win_rate": self.config.get("win_rate", 0.55),
            "reward_risk": self.config.get("reward_risk", 1.5),
            "kelly_fraction": self.config.get("kelly_fraction", 0.5),
            "max_position_pct": self.config.get("max_position_pct", 0.25),
            "scale_by_strength": self.config.get("scale_by_strength", True),
            "strength_floor": self.config.get("strength_floor", 0.25),
        }
        if overrides:
            # Accept both flat UI keys and nested sizing dict.
            nested = overrides.get("sizing") if isinstance(overrides.get("sizing"), dict) else None
            src = dict(nested or {})
            for k, v in overrides.items():
                if k == "sizing":
                    continue
                if k in {
                    "sizing_mode",
                    "mode",
                    "sizing_value",
                    "value",
                    "stop_pct",
                    "atr_period",
                    "atr_mult",
                    "win_rate",
                    "reward_risk",
                    "kelly_fraction",
                    "max_position_pct",
                    "scale_by_strength",
                    "strength_floor",
                }:
                    src[k] = v
            if "sizing_mode" in src and "mode" not in src:
                src["mode"] = src.pop("sizing_mode")
            if "sizing_value" in src and "value" not in src:
                src["value"] = src.pop("sizing_value")
            raw.update({k: v for k, v in src.items() if v is not None})
        return SizingConfig.from_dict(raw)

    def apply_sizing_config(self, overrides: dict[str, Any] | None = None) -> SizingConfig:
        self.sizing = self.sizing_from_config(overrides)
        if self.runtime is not None:
            self.runtime.sizing_config = self.sizing
        return self.sizing

    def walk_forward_config(
        self, overrides: dict[str, Any] | None = None
    ) -> "WalkForwardConfig":
        from engine.walk_forward import WalkForwardConfig

        base = dict(self.config.get("walk_forward") or {})
        return WalkForwardConfig.from_dict(base).merged(overrides)

    def sizing_for_strategy(self, strategy_id: str | None) -> SizingConfig:
        """Global phantom sizing merged with optional per-strategy overrides."""
        sid = str(strategy_id or "").strip()
        block = self.config.get("strategy_sizing") if isinstance(
            self.config.get("strategy_sizing"), dict
        ) else {}
        override = block.get(sid) if sid and isinstance(block, dict) else None
        if isinstance(override, dict) and override:
            return self.sizing_from_config(override)
        return self.sizing

    def _atr_for_key(self, key: str, ticker: str) -> float | None:
        if self.runtime is None:
            return None
        bars = self.runtime.candles.bars(key) or self.runtime.candles.bars(ticker) or []
        if len(bars) < 2:
            return None
        return atr_from_bars(
            [b.high for b in bars],
            [b.low for b in bars],
            [b.close for b in bars],
            period=int(self.sizing.atr_period),
        )

    async def _resolve_cost_store_path(self, trading_cfg: dict[str, Any]) -> str:
        # Relative "data/..." values used to be resolved against the process CWD
        # (or the repo root here), which split the cost schedule across two
        # locations depending on how the engine was started. Re-anchor them on
        # the shared data root so phantom, paper and the AI tools all read the
        # same schedule.
        path = str(trading_cfg.get("cost_store_path") or "").strip()
        if path:
            return _reanchor(path)
        costs = await self.core.config.get("costs", {}) or {}
        configured = (costs or {}).get("store_path") if isinstance(costs, dict) else None
        if configured:
            return _reanchor(str(configured))
        return str(data_path("cost_schedules"))

    async def _build_runtime_cfg(self) -> dict[str, Any]:
        trading_cfg = dict(await self.core.config.get("trading", {}) or {})
        fee = self.config.get("fee_bps")
        if fee is None:
            fee = trading_cfg.get("legacy_fee_bps", 0)
        slip = self.config.get("slippage_bps")
        if slip is None:
            slip = trading_cfg.get("legacy_slippage_bps", 0)
        self._fee_bps = float(fee or 0)
        self._slippage_bps = float(slip or 0)
        cost_store = await self._resolve_cost_store_path(trading_cfg)
        cfg = {
            **trading_cfg,
            "mode": "phantom",
            "equity": float(self.config.get("start_equity") or 10_000.0),
            # Deprecated knobs remain as fallback when no active CostSchedule exists.
            "legacy_fee_bps": self._fee_bps,
            "legacy_slippage_bps": self._slippage_bps,
            "cost_store_path": cost_store,
        }
        self._runtime_cfg = cfg
        return cfg

    async def on_initialize(self) -> None:
        cfg = await self._build_runtime_cfg()
        self.runtime = TradingRuntime.from_config(cfg)
        self.runtime.mode = "phantom"
        self._capturing = bool(self.config.get("capture_signals", True))
        path = str(self.config.get("timeline_path") or "").strip() or None
        self.timeline = PhantomTimeline(path=path)
        self.runtime.phantom_timeline = self.timeline
        self._bar_index = -1
        self._pending_fills: list[tuple[dict[str, Any], str]] = []
        # Rejected signals used to vanish: only FILLED/PARTIAL/ACCEPTED reached
        # `_fills`, so a run whose every signal fell below `strength_floor`
        # reported `trades=0` with no indication that any decision was ever made.
        # That is what made phantom look broken rather than merely unprofitable.
        self._shadow_rejects: dict[str, int] = {}
        self.apply_sizing_config()
        state_path = str(self.config.get("state_path") or data_path("phantom", "state.json"))
        root = Path(__file__).resolve().parents[2]
        self.state_store = StateStore(root / state_path)
        # The previous run's fills/last_run were persisted but never read back:
        # `restore_state` had no caller, so after any restart the phantom ledger
        # and the "last run" panel started empty and an interrupted research run
        # disappeared without a trace.
        self.restore_state()
        setattr(self.core, "phantom", self.runtime)

    def _capture_source(self) -> str:
        # Research grid stops ensemble_service; listening only to ensemble.decision
        # would record zero fills while the chart still draws strategy_viz markers.
        if getattr(self, "_grid_running", False):
            return "signals"
        raw = str(self.config.get("capture_source") or "ensemble").strip().lower()
        if raw not in {"ensemble", "signals", "both"}:
            raw = "ensemble"
        if raw == "ensemble":
            # No live consilium → fall back to raw strategy.signal (ad-hoc / tests /
            # replay outside run_grid). Otherwise shadow ledger stays empty.
            ens = None
            try:
                ens = self.core.service_registry.get("ensemble_service")
            except Exception:
                ens = None
            if ens is None or not getattr(ens, "is_running", False):
                return "signals"
        return raw

    async def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        await self.core.event_bus.subscribe("market.candle", self._on_candle)
        await self.core.event_bus.subscribe("strategy.signal", self._on_signal)
        await self.core.event_bus.subscribe("ensemble.decision", self._on_ensemble)
        await self.core.event_bus.subscribe(
            "cost.schedule_activated", self._on_cost_activated
        )

    async def stop(self) -> None:
        self.is_running = False
        for et, handler in (
            ("market.candle", self._on_candle),
            ("strategy.signal", self._on_signal),
            ("ensemble.decision", self._on_ensemble),
            ("cost.schedule_activated", self._on_cost_activated),
        ):
            try:
                await self.core.event_bus.unsubscribe(et, handler)
            except Exception:
                pass

    async def _on_cost_activated(self, event: KernelEvent) -> None:
        if self.runtime is None:
            return
        self.runtime.reload_active_cost_schedule()

    def _metrics(self) -> dict[str, Any]:
        """Canonical phantom PnL — always from shadow runtime book when present.

        ``equity_pnl(self._fills)`` is single-position and invents huge PnL on
        multi-symbol books; never use it when ``self.runtime`` exists.
        """
        start_eq = float(self.config.get("start_equity") or 10_000.0)
        if self.runtime is not None:
            snap = self.runtime.snapshot()
            final = float(snap.get("equity") or start_eq)
            peak = float(
                getattr(self.runtime.orders, "peak_equity", None) or final or start_eq
            )
            dd = 0.0
            if peak > 0:
                dd = max(0.0, (peak - final) / peak)
            return {
                "start_equity": start_eq,
                "final_equity": final,
                "pnl": final - start_eq,
                "trades": len(self._fills),
                "max_drawdown": dd,
                "total_costs": float(snap.get("total_costs") or 0),
                "gross_exposure": float(snap.get("gross_exposure") or 0),
                "leverage": (
                    float(snap.get("gross_exposure") or 0) / final if final > 0 else 0.0
                ),
                "source": "runtime",
            }
        metrics = equity_pnl(self._fills, start_equity=start_eq)
        metrics["source"] = "fills_fallback"
        return metrics

    async def get_status(self) -> dict[str, Any]:
        status = await super().get_status()
        metrics = self._metrics()
        open_rows: list[dict[str, Any]] = []
        exit_pending = 0
        if self.runtime is not None:
            try:
                open_rows = list(self.runtime.open_positions() or [])
            except Exception:
                open_rows = []
            for row in open_rows:
                if str(row.get("state") or "").lower() == "exit_pending":
                    exit_pending += 1
        status.update(
            {
                "fills": len(self._fills),
                "capturing": self._capturing,
                "grid_running": self._grid_running,
                "last_run": self._last_run,
                "interrupted_run": dict(self._interrupted_run)
                if self._interrupted_run
                else None,
                "fill_timing": self._fill_timing(),
                "last_grid": self._last_grid,
                "metrics": {
                    "pnl": metrics["pnl"],
                    "trades": metrics["trades"],
                    "max_drawdown": metrics["max_drawdown"],
                    "final_equity": metrics["final_equity"],
                    "total_costs": metrics.get("total_costs"),
                },
                "ledger": {
                    "open_positions": len(open_rows),
                    "exit_pending": exit_pending,
                    "fills": len(self._fills),
                    "stale": bool(open_rows) and not self._grid_running,
                    "hint": (
                        "phantom_reset"
                        if open_rows and not self._grid_running
                        else None
                    ),
                },
                "fee_bps": self._fee_bps,
                "slippage_bps": self._slippage_bps,
                "cost_schedule": bool(
                    self.runtime and self.runtime.orders.cost_schedule is not None
                ),
                "timeline": (
                    self.timeline.export_ui()
                    if self.timeline is not None and hasattr(self.timeline, "export_ui")
                    else (self.timeline.snapshot() if self.timeline else None)
                ),
                "patterns": list_patterns(),
                "last_pattern": self._last_pattern,
                "state": self.state_store.meta() if self.state_store else None,
            }
        )
        if self.runtime:
            status["runtime"] = self.runtime.snapshot()
        return status

    def reset_ledger(
        self,
        *,
        strategy_id: str | None = None,
        symbol: str | None = None,
        confirm_scoped: bool = False,
    ) -> dict[str, Any]:
        """Full wipe, or scoped clear of fills matching strategy/symbol."""
        sid = str(strategy_id or "").strip() or None
        sym = str(symbol or "").strip() or None
        if (sid or sym) and not confirm_scoped:
            return {
                "ok": False,
                "error": "confirm_scoped_required",
                "hint": "Pass confirm_scoped=true for partial phantom reset.",
            }
        if not sid and not sym:
            self._fills.clear()
            self._shadow_rejects = {}
            self._leverage_skip_noted = False
            cfg = dict(self._runtime_cfg) if self._runtime_cfg else {
                "mode": "phantom",
                "equity": float(self.config.get("start_equity") or 10_000.0),
                "risk": {},
                "legacy_fee_bps": self._fee_bps,
                "legacy_slippage_bps": self._slippage_bps,
            }
            cfg["mode"] = "phantom"
            cfg["equity"] = float(self.config.get("start_equity") or 10_000.0)
            cfg["legacy_fee_bps"] = self._fee_bps
            cfg["legacy_slippage_bps"] = self._slippage_bps
            self.runtime = TradingRuntime.from_config(cfg)
            self.runtime.mode = "phantom"
            self.runtime.sizing_config = self.sizing
            setattr(self.core, "phantom", self.runtime)
            if self.timeline is not None and hasattr(self.timeline, "clear"):
                try:
                    self.timeline.clear()
                except Exception:
                    pass
            return {"ok": True, "scope": "all", "fills_remaining": 0}

        def _match(fill: dict[str, Any]) -> bool:
            if sid and str(fill.get("strategy_id") or "") != sid:
                return False
            if sym:
                key = str(fill.get("ticker") or "")
                cc = str(fill.get("class_code") or "")
                full = f"{cc}:{key}" if cc and key else key
                if sym not in {key, full, cc}:
                    if not (key and sym.endswith(key)):
                        return False
            return True

        before = len(self._fills)
        self._fills = [f for f in self._fills if not _match(f)]
        removed = before - len(self._fills)
        return {
            "ok": True,
            "scope": "partial",
            "strategy_id": sid,
            "symbol": sym,
            "removed": removed,
            "fills_remaining": len(self._fills),
        }

    def phantom_audit(
        self,
        *,
        strategy_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Compact reject + fill audit for phantom shadow ledger."""
        from engine.trade_ledger import pair_round_trips

        sid = str(strategy_id or "").strip() or None
        lim = max(1, min(500, int(limit)))
        rejects: list[dict[str, Any]] = []
        fills: list[dict[str, Any]] = []
        for row in self._fills:
            if not isinstance(row, dict):
                continue
            row_sid = str(row.get("strategy_id") or "")
            if sid and row_sid != sid:
                continue
            status = str(row.get("status") or "").lower()
            entry = {
                "strategy_id": row_sid,
                "ticker": row.get("ticker"),
                "class_code": row.get("class_code"),
                "action": row.get("action"),
                "quantity": row.get("quantity"),
                "price": row.get("price"),
                "status": status,
                "reason": row.get("reason") or "",
                "strength": row.get("strength"),
                "bar_index": row.get("bar_index"),
            }
            if status in {"rejected", "reject"} or row.get("reason"):
                if status in {"rejected", "reject"} or str(row.get("reason") or "").startswith(
                    ("strength_below", "no_leverage", "qty")
                ):
                    rejects.append(entry)
            if status in {"filled", "partial", "accepted"}:
                fills.append(
                    {
                        **entry,
                        "symbol": (
                            f"{row.get('class_code')}:{row.get('ticker')}"
                            if row.get("class_code") and row.get("ticker")
                            else row.get("ticker")
                        ),
                        "side": str(row.get("action") or "").lower(),
                        "pnl": row.get("pnl"),
                    }
                )
        paired = pair_round_trips(
            [
                {
                    "strategy_id": f.get("strategy_id"),
                    "symbol": f.get("symbol"),
                    "side": f.get("side") or str(f.get("action") or "").lower(),
                    "quantity": f.get("quantity"),
                    "price": f.get("price"),
                    "ts": f.get("bar_index"),
                }
                for f in fills
            ],
            strategy_id=sid,
            limit=lim,
        )
        return {
            "strategy_id": sid,
            "rejects": rejects[-lim:],
            "reject_count": len(rejects),
            "fills": fills[-lim:],
            "fill_count": len(fills),
            "round_trips": paired,
        }

    def _bar_index_for_payload(self, payload: dict[str, Any] | None = None) -> int:
        """0-based index of the active bar (matches chart / strategy_viz)."""
        if self.runtime is None:
            return max(0, int(self._bar_index))
        p = payload or {}
        ticker = str(p.get("ticker") or p.get("symbol") or "")
        class_code = str(p.get("class_code") or "")
        key = f"{class_code}:{ticker}" if class_code and ticker else ticker
        bars = []
        if key:
            bars = self.runtime.candles.bars(key) or []
        if not bars and ticker:
            bars = self.runtime.candles.bars(ticker) or []
        if bars:
            return max(0, len(bars) - 1)
        return max(0, int(self._bar_index))

    async def _on_candle(self, event: KernelEvent) -> None:
        if self.runtime is None:
            return
        self.runtime.candles.ingest_payload(event.payload)
        # Keep 0-based index; signals are queued and fill after this handler.
        self._bar_index = self._bar_index_for_payload(event.payload)
        # Signals raised against the *previous* bar become fillable now, at this
        # bar's open. See `_fill_timing`.
        if self._pending_fills:
            queued = self._pending_fills
            self._pending_fills = []
            for signal, run_id in queued:
                await self._shadow_fill(
                    signal, run_id=run_id, price_override=self._bar_open(event.payload)
                )

    async def _on_signal(self, event: KernelEvent) -> None:
        if not self._capturing or self.runtime is None:
            return
        source = self._capture_source()
        if source == "ensemble":
            return
        p = event.payload or {}
        if str(p.get("strategy_id") or "") == "consilium":
            return
        await self._dispatch_shadow_fill(p, run_id=str(p.get("run_id") or ""))

    async def _on_ensemble(self, event: KernelEvent) -> None:
        """Shadow the live consilium decision (same side/ticker as execution path)."""
        if not self._capturing or self.runtime is None:
            return
        source = self._capture_source()
        if source == "signals":
            return
        p = dict(event.payload or {})
        if str(p.get("action") or "").upper() in {"", "FLAT", "HOLD"}:
            return
        p.setdefault("strategy_id", "consilium")
        await self._dispatch_shadow_fill(p, run_id=str(p.get("run_id") or "ensemble"))

    def _bar_open(self, payload: Any) -> float | None:
        raw = (payload or {}).get("open") if isinstance(payload, dict) else None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    async def _dispatch_shadow_fill(
        self, signal: dict[str, Any], *, run_id: str
    ) -> None:
        """Either fill now, or queue for the next bar's open.

        Filling at the close of the bar that produced the signal is optimistic:
        the strategy acted *because* of that close, and in live trading the first
        price it can trade at is the next bar's open. The walk-forward results
        that gate live promotion were produced that way, so the gate was clearing
        strategies on numbers no live run could reproduce.
        """
        if self._fill_timing() == "next_open":
            self._pending_fills.append((dict(signal), run_id))
            return
        await self._shadow_fill(signal, run_id=run_id)

    def _fill_timing(self) -> str:
        return str(self.config.get("fill_timing") or "next_open").lower()

    def flush_pending_fills(self) -> list[dict[str, Any]]:
        """Drop queued signals at end of run; report them rather than lose them.

        A signal raised on the final bar has no next bar, so it cannot fill under
        `next_open`. Silently dropping it would flatter the results by one trade
        per bar of the run; the count is returned so the caller can log it.
        """
        dropped = [
            {
                "strategy_id": str(sig.get("strategy_id") or "phantom"),
                "ticker": sig.get("ticker"),
                "action": sig.get("action"),
                "bar_index": self._bar_index,
                "status": "unfilled",
                "reason": "no_next_bar",
                "run_id": run_id,
            }
            for sig, run_id in self._pending_fills
        ]
        self._pending_fills = []
        return dropped

    def _record_timeline(
        self,
        signal: dict[str, Any],
        fill: dict[str, Any],
        *,
        run_id: str = "",
    ) -> None:
        if self.timeline is None:
            return
        ticker = str(signal.get("ticker") or fill.get("ticker") or "")
        mark = float(fill.get("price") or signal.get("price") or 0)
        status = str(fill.get("status") or "unknown").lower()
        bar_index = int(fill.get("bar_index", self._bar_index_for_payload(signal)))
        self.timeline.record(
            bar_index=bar_index,
            symbol=ticker,
            market={"price": mark, "ticker": ticker},
            strategies={
                str(signal.get("strategy_id") or "phantom"): {
                    "action": signal.get("action"),
                    "quantity": signal.get("quantity"),
                }
            },
            signal={
                "side": str(signal.get("action") or "").lower(),
                "strategy_id": signal.get("strategy_id"),
                "quantity": fill.get("quantity") or signal.get("quantity"),
                "strength": signal.get("strength"),
            },
            decision={
                "status": (
                    "executed"
                    if status in {"filled", "partial", "accepted"}
                    else "rejected"
                ),
                "reason": fill.get("reason") or "",
                "side": str(signal.get("action") or "").lower(),
            },
            meta={
                "run_id": run_id or self.timeline.run_id,
                "sizing": (self.sizing.to_dict() if self.sizing else None),
            },
        )

    def _adjust_price(self, price: float, action: str) -> float:
        """Deprecated compatibility hook; execution price is never fee-adjusted."""
        _ = action
        return price

    def _fill_cost(self, fill_record: dict[str, Any] | None) -> float:
        if not isinstance(fill_record, dict):
            return 0.0
        costs = fill_record.get("costs")
        if isinstance(costs, dict):
            try:
                return float(costs.get("total") or 0)
            except (TypeError, ValueError):
                return 0.0
        try:
            return float(fill_record.get("total_cost") or 0)
        except (TypeError, ValueError):
            return 0.0

    async def _shadow_fill(
        self,
        signal: dict[str, Any],
        *,
        run_id: str = "",
        price_override: float | None = None,
    ) -> dict[str, Any]:
        assert self.runtime is not None
        action = str(signal.get("action") or "BUY")
        ticker = str(signal.get("ticker") or "")
        class_code = str(signal.get("class_code") or "")
        key = f"{class_code}:{ticker}" if class_code and ticker else ticker
        bar_index = self._bar_index_for_payload(signal)
        bar = None
        if key:
            bar = self.runtime.candles.last(key)
        if bar is None and ticker:
            bar = self.runtime.candles.last(ticker)
        raw_px = signal.get("price")
        if price_override is not None:
            # Next-bar open: the first price actually reachable after the signal.
            price = float(price_override)
        else:
            price = float(raw_px) if raw_px not in (None, "") else 0.0
        if price <= 0:
            price = self.runtime.candles.mark_price(key) or self.runtime.candles.mark_price(
                ticker
            )
        price = float(price or 0)
        # Candle-close fills must stay inside the active bar's traded range.
        # A next-bar *open* is already inside that bar's range by construction and
        # must not be clamped: doing so would let an unreachable price be nudged
        # to the bar's favourable extreme and silently manufacture profit.
        if bar is not None and price > 0 and price_override is None:
            lo = float(bar.low or price)
            hi = float(bar.high or price)
            if hi < lo:
                hi, lo = lo, hi
            price = min(hi, max(lo, price))
        try:
            strength = float(signal["strength"]) if signal.get("strength") is not None else None
        except (TypeError, ValueError):
            strength = None
        equity = float(
            getattr(self.runtime.orders, "equity", None)
            or self.config.get("start_equity")
            or 10_000.0
        )
        atr = self._atr_for_key(key, ticker)
        sizing = self.sizing_for_strategy(str(signal.get("strategy_id") or ""))
        qty = size_quantity(
            equity=equity,
            price=price,
            config=sizing,
            strength=strength,
            atr=atr,
            fixed_qty=float(signal.get("quantity") or sizing.value)
            if sizing.mode == SizingMode.FIXED
            else None,
        )
        if sizing.rejects_strength(strength):
            # Explicit reject — do not fall back to a percent-of-equity retry.
            # Counted, not silently dropped: a run where every signal falls
            # below the floor must say so instead of reporting trades=0.
            self._note_shadow_reject("strength_below_floor")
            fill = {
                "action": action.upper(),
                "ticker": signal.get("ticker"),
                "class_code": signal.get("class_code"),
                "quantity": 0.0,
                "price": float(price or 0),
                "status": "rejected",
                "reason": (
                    f"strength_below_floor={float(strength or 0):.6g}"
                    f"<{float(sizing.strength_floor):.4g}"
                ),
                "strategy_id": signal.get("strategy_id"),
                "run_id": run_id,
                "bar_index": int(bar_index),
                "strength": strength,
                "sizing_mode": (
                    sizing.mode.value
                    if hasattr(sizing.mode, "value")
                    else str(sizing.mode)
                ),
            }
            return fill
        if qty <= 0 and price > 0:
            # Fallback: cold ATR / empty qty — retry percent-of-equity, still
            # honoring strength_floor (reject stays reject).
            qty = size_quantity(
                equity=equity,
                price=price,
                config=SizingConfig(
                    mode=SizingMode.PERCENT_OF_EQUITY,
                    value=max(1.0, float(sizing.value or 10)),
                    max_position_pct=sizing.max_position_pct,
                    scale_by_strength=sizing.scale_by_strength,
                    strength_floor=sizing.strength_floor,
                ),
                strength=strength,
            )
        # Clamp into account leverage headroom before submit (same as live path).
        if qty > 0 and price > 0:
            prefer = {key: price} if key else {}
            self.runtime._sync_open_marks(prefer=prefer)  # noqa: SLF001
            mult = self.runtime._contract_multiplier(  # noqa: SLF001
                ticker=ticker, class_code=class_code
            )
            increases = self.runtime._order_increases_symbol_risk(  # noqa: SLF001
                ticker=ticker,
                class_code=class_code,
                action=action,
                quantity=float(qty),
            )
            qty = self.runtime._clamp_qty_to_leverage_headroom(  # noqa: SLF001
                float(qty),
                price=price,
                contract_multiplier=mult,
                increases_risk=increases,
            )
        result = self.runtime.submit_signal(
            ticker=ticker,
            action=action,
            quantity=float(qty),
            strategy_id=str(signal.get("strategy_id") or "phantom"),
            class_code=class_code,
            mark_price=price if price > 0 else None,
            strength=strength,
            # Pre-sized above; runtime still clamps leverage headroom on risk-increasing orders.
            apply_sizing=False,
        )
        ts = signal.get("ts") or signal.get("timestamp")
        if ts in (None, "") and bar is not None and bar.ts is not None:
            try:
                ts = float(bar.ts.timestamp())
            except (TypeError, ValueError, OSError, AttributeError):
                ts = None
        fill = {
            "action": action.upper(),
            "ticker": signal.get("ticker"),
            "class_code": signal.get("class_code"),
            "quantity": result.filled_qty or float(qty),
            "price": float(result.avg_price or price or 0),
            "status": result.status.value,
            "reason": result.reason,
            "strategy_id": signal.get("strategy_id"),
            "run_id": run_id,
            "bar_index": int(bar_index),
            "ts": ts,
            "shadow": True,
            "strength": strength,
            "sizing_mode": (
                self.sizing.mode.value
                if isinstance(self.sizing.mode, SizingMode)
                else str(self.sizing.mode)
            ),
        }
        fill_record = result.raw.get("fill") if isinstance(result.raw, dict) else None
        if isinstance(fill_record, dict):
            fill["cost"] = self._fill_cost(fill_record)
            fill["fill_record"] = fill_record
        # Full-book soft skips: note once per capture session, avoid timeline spam.
        soft_full = str(result.reason or "").startswith("no_leverage_headroom")
        if soft_full:
            if not getattr(self, "_leverage_skip_noted", False):
                self._leverage_skip_noted = True
                self._record_timeline(signal, fill, run_id=run_id)
            return fill
        self._record_timeline(signal, fill, run_id=run_id)
        if result.status in {OrderStatus.FILLED, OrderStatus.PARTIAL, OrderStatus.ACCEPTED}:
            self._fills.append(fill)
            instance_id = str(await self.core.config.get("instance.id", "local") or "local")
            await publish_phantom_fill(
                self.core.event_bus,
                source_id=self.component_id,
                payload=fill,
                instance_id=instance_id,
            )
        else:
            self._note_shadow_reject(str(result.reason or result.status.value or "rejected"))
        return fill

    def _note_shadow_reject(self, reason: str) -> None:
        """Record one rejected shadow signal under a normalised reason."""
        key = str(reason or "rejected").strip() or "rejected"
        # Broker/execution reasons carry free-form detail; keep the shape stable
        # so the counters stay readable instead of one key per message.
        head = key.split("=", 1)[0].split(":", 1)[0].strip() or "rejected"
        self._shadow_rejects[head] = int(self._shadow_rejects.get(head, 0)) + 1

    def shadow_reject_summary(self) -> dict[str, Any]:
        """How many shadow signals were dropped, and why.

        Without this a phantom run that rejected every signal is
        indistinguishable from a strategy that never traded.
        """
        rejects = dict(self._shadow_rejects or {})
        return {
            "rejected": int(sum(rejects.values())),
            "reject_reasons": rejects,
        }

    async def replay_candles(
        self,
        candles: list[Candle],
        *,
        class_code: str,
        run_id: str,
        strategy_id: str = "",
        source: str = "pattern",
        delay_sec: float = 0.0,
    ) -> dict[str, Any]:
        """Publish synthetic/historical candles as ``market.candle`` for shadow path."""
        instance_id = str(await self.core.config.get("instance.id", "local") or "local")
        published = 0
        for bar in candles:
            payload = candle_to_market_payload(
                bar, class_code=class_code, source=source, history=False
            )
            payload["source"] = source
            payload["run_id"] = run_id
            payload["strategy_id"] = strategy_id
            await publish_market_candle(
                self.core.event_bus,
                source_id=self.component_id,
                payload=payload,
                instance_id=instance_id,
            )
            published += 1
            if delay_sec > 0:
                await asyncio.sleep(delay_sec)
            else:
                await asyncio.sleep(0)
        return {"bars": published, "run_id": run_id, "source": source}

    def persist_state(self) -> str | None:
        if self.state_store is None or self.runtime is None:
            return None
        path = self.state_store.save(
            {
                "runtime": self.runtime.snapshot(),
                "fills": list(self._fills[-200:]),
                "last_run": self._last_run,
                "capturing": self._capturing,
            },
            label="phantom",
        )
        return str(path)

    def restore_state(self) -> bool:
        """Reload the persisted run summary, and flag an interrupted run.

        The *position book* is deliberately not restored. A phantom position only
        means something together with the bars that produced it, and those bars
        are not replayed on restart — so rehydrating positions from a snapshot
        would present marks that no longer correspond to any observed price, and
        the next result would silently absorb a book it never saw being opened.

        Silently starting empty was the other failure: `restore_state` had no
        caller at all, so the last run and recent fills vanished on every
        restart. Both are now explicit — the summary is restored, and an
        interrupted run with open positions is logged and surfaced.
        """
        if self.state_store is None or self.runtime is None:
            return False
        payload = self.state_store.load()
        if not payload:
            return False
        fills = payload.get("fills")
        if isinstance(fills, list):
            self._fills = list(fills)
        self._last_run = (
            payload.get("last_run") if isinstance(payload.get("last_run"), dict) else None
        )
        runtime_payload = payload.get("runtime")
        open_positions: dict[str, Any] = {}
        if isinstance(runtime_payload, dict):
            raw_positions = runtime_payload.get("positions")
            if isinstance(raw_positions, dict):
                open_positions = {
                    str(k): v
                    for k, v in raw_positions.items()
                    if isinstance(v, dict) and abs(float(v.get("quantity") or 0.0)) > 1e-12
                }
        if open_positions:
            self._interrupted_run = {
                "open_positions": open_positions,
                "restored": False,
                "reason": (
                    "phantom run was interrupted with open positions; the book was "
                    "not restored because the bars that opened it are not replayed"
                ),
            }
            _log().critical(
                "phantom recovered %d open position(s) from a previous run and "
                "did NOT restore them (%s). Research metrics that spanned the "
                "restart are not comparable to ones measured in a single run.",
                len(open_positions),
                self.state_store.path,
            )
        return True

    async def what_if(
        self,
        *,
        ticker: str,
        class_code: str = "SYNTH",
        strategy_id: str = "ma_cross_example",
        pattern: str = "sharp_rise",
        param_overrides: dict[str, Any] | None = None,
        bars: int | None = None,
        seed: int = 99,
    ) -> dict[str, Any]:
        """todo2 §4.3 What-if: one pattern run with alternate strategy params."""
        results = await self.run_grid(
            ticker,
            class_code=class_code,
            feed="patterns",
            limit=int(bars or self.config.get("pattern_bars") or 80),
            strategy_id=strategy_id,
            variants=[dict(param_overrides or {})],
            patterns=[pattern],
            pattern_seed=seed,
        )
        row = results[-1] if results else {}
        if self.timeline is not None:
            row = {
                **row,
                "timeline": (
                    self.timeline.export_ui()
                    if hasattr(self.timeline, "export_ui")
                    else self.timeline.snapshot()
                ),
                "markers": self.timeline.markers()[-50:],
            }
        return row

    async def run_walk_forward(
        self,
        ticker: str,
        *,
        class_code: str = "TQBR",
        provider: str | None = None,
        limit: int | None = None,
        strategy_id: str = "ma_cross_example",
        variants: list[dict[str, Any]] | None = None,
        variant_mode: str | None = None,
        max_variants: int | None = None,
        holdout_bars: int | None = None,
        train_bars: int | None = None,
        test_bars: int | None = None,
        step: int | None = None,
        feed: str | None = None,
        pattern: str | None = None,
        pattern_seed: int | None = None,
        seed: int | None = None,
        start_price: float | None = None,
        quantity: float | None = None,
        start_equity: float | None = None,
        walk_forward: dict[str, Any] | None = None,
        **wf_overrides: Any,
    ) -> dict[str, Any]:
        """
        Configurable walk-forward over history or a synthetic pattern.

        Knobs merge: YAML ``walk_forward`` ← ``walk_forward`` dict ← explicit kwargs.
        Uses Strategy SDK ``evaluate_bars`` offline (no live EventBus pollution).

        ``variant_mode``: ``grid`` (small presets) or ``optimize`` (dense search).
        Pass explicit ``variants`` to override. Defaults to grid for speed; use
        optimize when hunting parameters under OOS selection.

        ``holdout_bars``: reserve the last N bars; WF optimizes on the prefix,
        then scores ``chosen`` once on the untouched holdout (robustness check).

        Quantity defaults to phantom/paper ``SizingConfig`` (e.g. 10% equity),
        not a fixed ``1.0`` coin — otherwise BTC≈60k pays ~fee_bps on full coin
        notional (~31.5 at 5 bps) and every strategy looks uniformly bankrupt.
        """
        from engine.strategy_ids import resolve_strategy_id_via_core
        from engine.walk_forward import (
            WalkForwardTooShortError,
            resolve_walk_forward_bars,
            run_walk_forward_offline,
        )
        from host.phantom_variants import variants_for_mode

        strategy_id = resolve_strategy_id_via_core(self.core, strategy_id)
        plugin = self.core.plugin_manager.get(strategy_id)
        if plugin is None:
            raise RuntimeError(f"strategy plugin not loaded: {strategy_id}")

        overrides: dict[str, Any] = dict(walk_forward or {})
        overrides.update({k: v for k, v in wf_overrides.items() if v is not None})
        if feed is not None:
            overrides["feed"] = feed
        if pattern is not None:
            overrides["pattern"] = pattern
        if seed is not None:
            overrides["seed"] = seed
        if pattern_seed is not None:
            overrides.setdefault("seed", pattern_seed)
        if limit is not None:
            overrides["bars"] = limit
        if train_bars is not None:
            overrides["train_bars"] = train_bars
        if test_bars is not None:
            overrides["test_bars"] = test_bars
        if step is not None:
            overrides["step"] = step
        if holdout_bars is not None:
            overrides["holdout_bars"] = holdout_bars

        cfg = self.walk_forward_config(overrides)
        feed_mode = cfg.feed
        pattern_name = cfg.pattern
        seed_i = cfg.seed
        bars_limit = int(limit if limit is not None else cfg.bars)

        # Auto-bump bars when the request cannot realize min_folds (prefer science
        # over 21× walk_forward_too_short). Provenance records the bump.
        holdout_pre = 0
        try:
            holdout_pre = int(
                overrides.get("holdout_bars")
                if overrides.get("holdout_bars") is not None
                else self.config.get("holdout_bars")
                or 0
            )
        except (TypeError, ValueError):
            holdout_pre = 0
        step_pre = 1 if cfg.auto_fit_folds else cfg.effective_step
        bars_plan = resolve_walk_forward_bars(
            bars_limit,
            train_bars=cfg.train_bars,
            test_bars=cfg.test_bars,
            step=step_pre,
            min_folds=cfg.min_folds,
            holdout_bars=max(0, holdout_pre),
        )
        bars_limit = int(bars_plan["bars"])
        bars_bump_meta = {
            "bars_requested": bars_plan["bars_requested"],
            "bars_needed": bars_plan["bars_needed"],
            "bars_bumped_from": bars_plan.get("bars_bumped_from"),
            "bars_auto_bumped": bool(bars_plan.get("bumped")),
        }

        # Fresh ledger — never inherit grid/exit_pending tails into WF.
        try:
            self.reset_ledger()
        except Exception:
            pass

        # Resolve search space once (recorded in provenance).
        mode_raw = (
            variant_mode
            if variant_mode is not None
            else overrides.get("variant_mode")
            or self.config.get("variant_mode")
            or "grid"
        )
        resolved_variant_mode = str(mode_raw or "grid").lower()
        if resolved_variant_mode in {"opt", "search", "dense"}:
            resolved_variant_mode = "optimize"
        if resolved_variant_mode not in {"grid", "optimize"}:
            resolved_variant_mode = "grid"
        try:
            max_v = int(
                max_variants
                if max_variants is not None
                else overrides.get("max_variants")
                or self.config.get("max_variants")
                or (36 if resolved_variant_mode == "optimize" else 12)
            )
        except (TypeError, ValueError):
            max_v = 36 if resolved_variant_mode == "optimize" else 12
        max_v = max(1, min(120, max_v))
        schema = getattr(plugin, "CONFIG_SCHEMA", None)
        if not isinstance(schema, dict):
            schema = None
        if variants is not None:
            vars_ = [dict(v or {}) for v in variants]
            resolved_variant_mode = "explicit"
        else:
            vars_ = variants_for_mode(
                strategy_id,
                resolved_variant_mode,
                max_variants=max_v,
                schema=schema,
            )

        series: dict[str, list[float]]
        source = feed_mode
        resolved_tf = str(cfg.timeframe or "1d")
        provider_resolved = ""
        bar_interval_sec: float | None = None
        data_start_iso: str | None = None
        data_end_iso: str | None = None
        if feed_mode == "history":
            from engine.ohlc_timeframe import (
                TimeframeMismatchError,
                median_bar_interval_sec,
                timeframe_to_seconds,
            )

            hist = self.core.service_registry.get("historical_data_service")
            if hist is None or not hasattr(hist, "fetch"):
                raise RuntimeError("historical_data_service.fetch unavailable")
            # Research default: auto-resolve (BTCUSDT → ccxt). Explicit runtime/chart
            # is allowed but must survive interval verification after resample.
            try:
                bars = await hist.fetch(
                    ticker,
                    class_code=class_code,
                    provider=provider,
                    limit=bars_limit,
                    timeframe=resolved_tf,
                )
            except TimeframeMismatchError as exc:
                raise RuntimeError(str(exc)) from exc
            if not bars:
                raise RuntimeError("no history bars for walk-forward")
            # Build timestamps for interval proof (closes alone cannot prove TF).
            stamps = [getattr(b, "ts", None) for b in bars]
            bar_interval_sec = median_bar_interval_sec(
                [{"ts": t} for t in stamps if t is not None]
            )
            expected_sec = timeframe_to_seconds(resolved_tf)
            if (
                bar_interval_sec is not None
                and expected_sec is not None
                and abs(bar_interval_sec - float(expected_sec)) / float(expected_sec) > 0.35
            ):
                raise RuntimeError(
                    f"timeframe_mismatch: requested={resolved_tf} "
                    f"(~{expected_sec}s) but median bar gap is ~{bar_interval_sec:.0f}s "
                    f"(bars={len(bars)}). Refetch with provider=ccxt (crypto) or "
                    f"provider=yahoo (equities); do not use chart runtime 1m labeled as 1d."
                )
            series = {
                "open": [float(b.open) for b in bars],
                "high": [float(b.high) for b in bars],
                "low": [float(b.low) for b in bars],
                "close": [float(b.close) for b in bars],
                "volume": [float(getattr(b, "volume", 0) or 0) for b in bars],
                # Bar timestamps, so a research digest can record *which window*
                # it was computed over. The research gate judges clearance
                # freshness on the last bar seen, not on when the run happened.
                "ts": [
                    getattr(b, "ts", None).isoformat()
                    if getattr(b, "ts", None) is not None
                    else None
                    for b in bars
                ],
            }
            source = str(provider or "history")
            # Capture the full window before the holdout slice narrows `series`,
            # so the digest records the newest bar the analysis truly observed
            # (the holdout tail counts as evidence) rather than the end of the
            # research prefix.
            data_start_iso = _series_bound_iso(series, first=True)
            data_end_iso = _series_bound_iso(series, first=False)
            try:
                last = getattr(hist, "_last_fetch", None) or {}
                if isinstance(last, dict):
                    if last.get("timeframe"):
                        resolved_tf = str(last.get("timeframe"))
                    if last.get("provider"):
                        provider_resolved = str(last.get("provider"))
                    if last.get("bar_interval_sec") is not None:
                        bar_interval_sec = float(last["bar_interval_sec"])
            except Exception:
                pass
            if not provider_resolved:
                try:
                    provider_resolved = str(
                        hist._resolve_provider(  # noqa: SLF001
                            provider, ticker=ticker, class_code=class_code
                        )
                    )
                except Exception:
                    provider_resolved = str(provider or "")
        else:
            bars_n = int(bars_limit or self.config.get("pattern_bars") or 120)
            px = float(
                start_price
                if start_price is not None
                else self.config.get("pattern_start_price") or 100.0
            )
            specs = default_pattern_specs(
                bars=bars_n,
                start_price=px,
                base_seed=int(seed_i),
                patterns=[pattern_name],
            )
            candles = generate_pattern_candles(
                ticker, specs[0], class_code=class_code or "SYNTH"
            )
            series = {
                "open": [float(b.open) for b in candles],
                "high": [float(b.high) for b in candles],
                "low": [float(b.low) for b in candles],
                "close": [float(b.close) for b in candles],
                "volume": [float(getattr(b, "volume", 0) or 0) for b in candles],
            }
            source = f"pattern:{pattern_name}"
            data_start_iso = _series_bound_iso(series, first=True)
            data_end_iso = _series_bound_iso(series, first=False)

        # Optional untouched holdout tail (robustness): WF on prefix only.
        holdout_series: dict[str, list[float]] | None = None
        try:
            holdout_n = int(
                overrides.get("holdout_bars")
                if overrides.get("holdout_bars") is not None
                else self.config.get("holdout_bars")
                or 0
            )
        except (TypeError, ValueError):
            holdout_n = 0
        holdout_n = max(0, holdout_n)
        if holdout_n > 0:
            from engine.research import slice_series

            n_full = len(series.get("close") or [])
            min_research = int(cfg.train_bars) + int(cfg.test_bars) * max(1, int(cfg.min_folds))
            if n_full <= holdout_n + min_research:
                raise RuntimeError(
                    f"holdout_too_large: bars={n_full} holdout={holdout_n} "
                    f"need ≥{min_research} research bars for WF"
                )
            holdout_series = slice_series(series, n_full - holdout_n, n_full)
            series = slice_series(series, 0, n_full - holdout_n)

        eq = float(
            start_equity
            if start_equity is not None
            else self.config.get("start_equity")
            or 10_000.0
        )
        # Paper path charges fee + slippage; WF previously used fee only and
        # quantity=1.0 (full BTC), which looked like a "3% commission" bug.
        fee = float(self._fee_bps or 0.0)
        slip = float(self._slippage_bps or 0.0)
        cost_bps = fee + slip
        sizing = self.sizing_for_strategy(strategy_id)
        atr = None
        atr_series = None
        if sizing.mode == SizingMode.ATR_RISK:
            from engine.position_sizing import atr_series_from_bars

            atr_series = atr_series_from_bars(
                series.get("high") or [],
                series.get("low") or [],
                series.get("close") or [],
                period=int(sizing.atr_period),
            )
            # Hint qty uses last causal ATR only (no full-series lookahead).
            atr = next((a for a in reversed(atr_series) if a is not None), None)
        # Explicit quantity overrides sizing; otherwise size per fill from equity.
        use_sizing = quantity is None
        qty_arg: float | None = float(quantity) if quantity is not None else None
        closes_pos = [float(c) for c in (series.get("close") or []) if float(c) > 0]
        ref_px = (
            sorted(closes_pos)[len(closes_pos) // 2] if closes_pos else 0.0
        )
        if use_sizing and ref_px > 0:
            hint_qty = size_quantity(
                equity=eq,
                price=ref_px,
                config=sizing,
                atr=atr,
                fixed_qty=float(sizing.value) if sizing.mode == SizingMode.FIXED else None,
            )
            if hint_qty <= 0:
                hint_qty = size_quantity(
                    equity=eq,
                    price=ref_px,
                    config=SizingConfig(
                        mode=SizingMode.PERCENT_OF_EQUITY,
                        value=max(1.0, float(sizing.value or 10)),
                        max_position_pct=sizing.max_position_pct,
                        scale_by_strength=False,
                    ),
                )
        else:
            hint_qty = float(qty_arg or 1.0)
        # Match paper TradingRuntime: forbid_net_flip → research long/flat only.
        trading_cfg = {}
        try:
            raw = getattr(self.core.config, "raw", None)
            if isinstance(raw, dict) and isinstance(raw.get("trading"), dict):
                trading_cfg = dict(raw["trading"])
            else:
                trading_cfg = dict(await self.core.config.get("trading", {}) or {})
        except Exception:
            trading_cfg = {}
        forbid_flip = bool(
            trading_cfg.get(
                "forbid_net_flip",
                self.config.get("forbid_net_flip", True),
            )
        )
        position_mode = "long_flat" if forbid_flip else "net_flip"
        from engine.ohlc_timeframe import timeframe_to_seconds as _tf_sec

        expected_interval_sec = _tf_sec(resolved_tf)
        try:
            # Ensure resolved timeframe is recorded even if YAML omitted it.
            cfg = cfg.merged({"timeframe": resolved_tf})
            result = run_walk_forward_offline(
                plugin,
                series,
                vars_,
                quantity=qty_arg if not use_sizing else None,
                fee_bps=cost_bps,
                start_equity=eq,
                config=cfg,
                sizing=sizing if use_sizing else None,
                atr=atr if use_sizing else None,
                atr_series=atr_series if use_sizing else None,
                position_mode=position_mode,
                provenance_extra={
                    "strategy_id": strategy_id,
                    "ticker": ticker,
                    "class_code": class_code,
                    "provider": provider,
                    "provider_resolved": provider_resolved or str(provider or ""),
                    "source": source,
                    "timeframe": resolved_tf,
                    "bar_interval_sec": bar_interval_sec,
                    # The window of bars this analysis actually saw. The research
                    # gate judges freshness on this, not on when the run happened,
                    # so a stale cache cannot be re-dated into a valid clearance.
                    "data_start": data_start_iso,
                    "data_end": data_end_iso,
                    "expected_interval_sec": (
                        float(expected_interval_sec) if expected_interval_sec else None
                    ),
                    "variant_mode": resolved_variant_mode,
                    "variants_count": len(vars_),
                    "max_variants": max_v,
                    "quantity": hint_qty,
                    "quantity_source": "sizing" if use_sizing else "explicit",
                    "ref_price": ref_px,
                    "notional": float(hint_qty) * float(ref_px) if ref_px else 0.0,
                    "sizing": sizing.to_dict(),
                    "fee_bps": fee,
                    "slippage_bps": slip,
                    "cost_bps": cost_bps,
                    "start_equity": eq,
                    "position_mode": position_mode,
                    "forbid_net_flip": forbid_flip,
                    "holdout_bars": holdout_n,
                    "bars": bars_limit,
                    **bars_bump_meta,
                },
            )
        except WalkForwardTooShortError as exc:
            raise RuntimeError(str(exc)) from exc

        if holdout_series is not None and holdout_n > 0:
            from engine.research import offline_variant_metrics

            chosen_cfg = dict(result.get("chosen") or {})
            hold_atr = None
            hold_atr_series = None
            if use_sizing and sizing.mode == SizingMode.ATR_RISK:
                from engine.position_sizing import atr_series_from_bars

                hold_atr_series = atr_series_from_bars(
                    holdout_series.get("high") or [],
                    holdout_series.get("low") or [],
                    holdout_series.get("close") or [],
                    period=int(sizing.atr_period),
                )
                hold_atr = next(
                    (a for a in reversed(hold_atr_series) if a is not None), None
                )
            hold_m = offline_variant_metrics(
                plugin,
                holdout_series,
                config=chosen_cfg,
                quantity=qty_arg if not use_sizing else None,
                fee_bps=cost_bps,
                start_equity=eq,
                sizing=sizing if use_sizing else None,
                atr=hold_atr if use_sizing else None,
                atr_series=hold_atr_series if use_sizing else None,
                position_mode=position_mode,
            )
            result["holdout"] = {
                "bars": holdout_n,
                "pnl": float(hold_m.get("pnl") or 0.0),
                "trades": int(hold_m.get("trades") or 0),
                "max_drawdown": float(hold_m.get("max_drawdown") or 0.0),
                "profit_factor": float(hold_m.get("profit_factor") or 0.0),
                "config": chosen_cfg,
            }
            prov = result.get("provenance")
            if isinstance(prov, dict):
                prov["holdout_bars"] = holdout_n
                prov["holdout_pnl"] = result["holdout"]["pnl"]

        from engine.research_digest import digest_walk_forward

        result["digest"] = digest_walk_forward(result)
        try:
            from engine.research_gate import record_digest_clearance

            record_digest_clearance(result.get("digest"))
        except Exception:
            pass
        result.update(
            {
                "ticker": ticker,
                "class_code": class_code,
                "strategy_id": strategy_id,
                "source": source,
                "feed": feed_mode,
                "seed": seed_i,
                "quantity": hint_qty,
                "cost_bps": cost_bps,
            }
        )
        self._last_run = {
            "kind": "walk_forward",
            "ticker": ticker,
            "strategy_id": strategy_id,
            "oos": result.get("oos"),
            "folds": len(result.get("folds") or []),
            "feed": feed_mode,
            "seed": seed_i,
            "chosen": result.get("chosen"),
            "holdout": result.get("holdout"),
            "digest": result.get("digest"),
            "provenance": result.get("provenance"),
        }
        self._last_walk_forward = result
        return result

    async def run_walk_forward_batch(
        self,
        strategy_ids: list[str],
        ticker: str | None = None,
        *,
        tickers: list[str] | None = None,
        class_code: str = "TQBR",
        provider: str | None = None,
        limit: int | None = None,
        variant_mode: str = "optimize",
        max_variants: int | None = 36,
        holdout_bars: int | None = None,
        walk_forward: dict[str, Any] | None = None,
        **wf_overrides: Any,
    ) -> dict[str, Any]:
        """Run WF for many strategies × tickers; return ranked digest rows."""
        from engine.research_digest import rank_batch_rows
        from engine.strategy_ids import resolve_strategy_id_via_core

        ids = [
            resolve_strategy_id_via_core(self.core, s)
            for s in strategy_ids
            if str(s).strip()
        ]
        seen: set[str] = set()
        uniq: list[str] = []
        for sid in ids:
            if sid not in seen:
                seen.add(sid)
                uniq.append(sid)
        ids = uniq
        if not ids:
            raise RuntimeError("strategy_ids_required")

        symbols: list[str] = []
        if tickers:
            symbols = [str(t).strip() for t in tickers if str(t).strip()]
        if ticker and str(ticker).strip():
            t0 = str(ticker).strip()
            if t0 not in symbols:
                symbols.insert(0, t0)
        if not symbols:
            raise RuntimeError("ticker_required")

        # One bars plan for the whole batch — bump once, never N identical errors.
        from engine.walk_forward import resolve_walk_forward_bars

        wf_cfg = self.walk_forward_config(
            {
                **dict(walk_forward or {}),
                **{k: v for k, v in wf_overrides.items() if v is not None},
                **({"bars": limit} if limit is not None else {}),
                **({"holdout_bars": holdout_bars} if holdout_bars is not None else {}),
            }
        )
        bars_req = int(limit if limit is not None else wf_cfg.bars)
        holdout_req = int(holdout_bars if holdout_bars is not None else 0)
        if "holdout_bars" in (walk_forward or {}) and holdout_bars is None:
            try:
                holdout_req = int((walk_forward or {}).get("holdout_bars") or 0)
            except (TypeError, ValueError):
                holdout_req = 0
        step_req = 1 if wf_cfg.auto_fit_folds else wf_cfg.effective_step
        bars_plan = resolve_walk_forward_bars(
            bars_req,
            train_bars=wf_cfg.train_bars,
            test_bars=wf_cfg.test_bars,
            step=step_req,
            min_folds=wf_cfg.min_folds,
            holdout_bars=max(0, holdout_req),
        )
        limit = int(bars_plan["bars"])
        try:
            self.reset_ledger()
        except Exception:
            pass

        rows: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        full: list[dict[str, Any]] = []
        for sym in symbols:
            for sid in ids:
                try:
                    result = await self.run_walk_forward(
                        sym,
                        class_code=class_code,
                        provider=provider,
                        limit=limit,
                        strategy_id=sid,
                        variant_mode=variant_mode,
                        max_variants=max_variants,
                        holdout_bars=holdout_bars,
                        walk_forward=walk_forward,
                        **wf_overrides,
                    )
                    dig = dict(result.get("digest") or {})
                    dig["ok"] = True
                    rows.append(dig)
                    full.append(
                        {
                            "strategy_id": sid,
                            "ticker": sym,
                            "chosen": result.get("chosen"),
                            "oos": result.get("oos"),
                            "holdout": result.get("holdout"),
                            "digest": dig,
                            "provenance": result.get("provenance"),
                            "report_path": result.get("report_path"),
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    err = {
                        "ok": False,
                        "strategy_id": sid,
                        "ticker": sym,
                        "error": str(exc),
                        "mean_oos_pnl": None,
                    }
                    errors.append(err)
                    rows.append(err)
        ranked = rank_batch_rows(rows)
        out = {
            "ok": True,
            "ticker": symbols[0],
            "tickers": symbols,
            "class_code": class_code,
            "variant_mode": variant_mode,
            "holdout_bars": holdout_bars,
            "bars": limit,
            "bars_requested": bars_plan["bars_requested"],
            "bars_needed": bars_plan["bars_needed"],
            "bars_auto_bumped": bool(bars_plan.get("bumped")),
            "bars_bumped_from": bars_plan.get("bars_bumped_from"),
            "rows": ranked,
            "errors": errors,
            "runs": full,
        }
        self._last_batch = ranked
        self._last_run = {"kind": "walk_forward_batch", **out}
        return out

    def apply_chosen_params(
        self,
        *,
        strategy_id: str | None = None,
        params: dict[str, Any] | None = None,
        persist: bool = True,
        from_last: bool = True,
        sync_research_ticker: bool = True,
    ) -> dict[str, Any]:
        """Write chosen WF params into plugin config (+ optional YAML persist).

        When ``from_last`` and no explicit params: prefer the top-ranked OK row
        from the last walk-forward *batch* (digest), then fall back to the last
        single walk-forward run. Never silently apply the last loop iteration
        when a ranked batch digest exists.
        """
        from engine.strategy_ids import resolve_strategy_id_via_core

        sid = str(strategy_id or "").strip()
        chosen: dict[str, Any] = dict(params or {})
        research_ticker = ""
        source = "explicit" if chosen else ""
        if not chosen and from_last:
            batch_rows = list(self._last_batch or [])
            pick: dict[str, Any] | None = None
            if batch_rows:
                ok_rows = [
                    r
                    for r in batch_rows
                    if isinstance(r, dict)
                    and r.get("ok") is not False
                    and isinstance(r.get("chosen"), dict)
                    and r.get("chosen")
                ]
                if sid:
                    want = resolve_strategy_id_via_core(self.core, sid)
                    for r in ok_rows:
                        if resolve_strategy_id_via_core(
                            self.core, str(r.get("strategy_id") or "")
                        ) == want:
                            pick = r
                            break
                elif ok_rows:
                    pick = ok_rows[0]
                if pick is not None:
                    chosen = dict(pick.get("chosen") or {})
                    if not sid:
                        sid = str(pick.get("strategy_id") or "")
                    research_ticker = str(pick.get("ticker") or "")
                    source = "last_batch_top" if not strategy_id else "last_batch_match"
            if not chosen:
                last = self._last_walk_forward or {}
                chosen = dict(last.get("chosen") or {})
                if not sid:
                    sid = str(last.get("strategy_id") or "")
                research_ticker = str(last.get("ticker") or "")
                prov = (
                    last.get("provenance")
                    if isinstance(last.get("provenance"), dict)
                    else {}
                )
                if not research_ticker and prov:
                    research_ticker = str(prov.get("ticker") or "")
                if chosen:
                    source = "last_walk_forward"
        if sid:
            sid = resolve_strategy_id_via_core(self.core, sid)
        if not sid:
            return {"ok": False, "error": "strategy_id_required"}
        if not chosen:
            return {"ok": False, "error": "chosen_params_empty"}

        # Drop non-schema / bookkeeping keys. Never let "chosen" wipe tickers
        # unless sync_research_ticker explicitly sets them from the WF symbol.
        skip = {
            "quantity",
            "class_code",
            "ticker",
            "tickers",
            "symbol",
            "enabled",
            "fee_bps",
            "slippage_bps",
            "observe_only",
        }
        plugin = self.core.plugin_manager.get(sid)
        schema = getattr(plugin, "CONFIG_SCHEMA", None) if plugin is not None else None
        cleaned: dict[str, Any] = {}
        for k, v in chosen.items():
            key = str(k)
            if key in skip:
                continue
            if isinstance(schema, dict) and schema and key not in schema:
                continue
            cleaned[key] = v
        if not cleaned:
            cleaned = {k: v for k, v in chosen.items() if str(k) not in skip}
        if not cleaned and not (sync_research_ticker and research_ticker):
            return {"ok": False, "error": "no_applicable_params", "chosen": chosen}

        warnings: list[str] = []
        if sync_research_ticker and research_ticker:
            # Normalize BTCUSDT → BTC/USDT when slash form is conventional.
            t = research_ticker.strip().upper()
            if "/" not in t and t.endswith("USDT") and len(t) > 4:
                t = f"{t[:-4]}/{t[-4:]}"
            cleaned["tickers"] = [t]
            warnings.append(
                f"tickers set to [{t}] from research ticker (was optimized on this symbol)"
            )

        raw_cfg = getattr(self.core.config, "raw", None)
        if not isinstance(raw_cfg, dict):
            return {"ok": False, "error": "no_config"}

        from host.config_patch import deep_merge, set_path

        block = dict((raw_cfg.get("plugins") or {}).get(sid) or {})
        prev_tickers = list(block.get("tickers") or [])
        deep_merge(block, cleaned)
        set_path(raw_cfg, f"plugins.{sid}", block)

        applied_live = False
        if plugin is not None and isinstance(getattr(plugin, "config", None), dict):
            deep_merge(plugin.config, cleaned)
            applied_live = True

        if prev_tickers and cleaned.get("tickers") and prev_tickers != cleaned.get("tickers"):
            warnings.append(f"tickers changed {prev_tickers} → {cleaned.get('tickers')}")

        return {
            "ok": True,
            "strategy_id": sid,
            "params": cleaned,
            "applied_live": applied_live,
            "needs_restart": not applied_live,
            "persist_requested": bool(persist),
            "config_block": block,
            "warnings": warnings,
            "research_ticker": research_ticker or None,
            "source": source or "explicit",
        }

    async def run_grid(
        self,
        ticker: str,
        *,
        class_code: str = "TQBR",
        provider: str | None = None,
        limit: int | None = 60,
        strategy_id: str = "ma_cross_example",
        variants: list[dict[str, Any]] | None = None,
        feed: str | None = None,
        start: str | None = None,
        end: str | None = None,
        patterns: list[str] | None = None,
        pattern_seed: int = 42,
        start_price: float | None = None,
        sizing: dict[str, Any] | None = None,
        strategy_params: dict[str, Any] | None = None,
        start_equity: float | None = None,
        fee_bps: float | None = None,
        slippage_bps: float | None = None,
    ) -> list[dict[str, Any]]:
        """
        Multi-variant phantom over history and/or randomized patterns.

        ``feed``: history | patterns | both (default from config).
        ``strategy_params``: base overrides merged under each grid variant.
        ``sizing``: PositionSizer knobs for this run.
        """
        feed_mode = str(feed or self.config.get("default_feed") or "history").lower()
        if feed_mode not in {"history", "patterns", "both"}:
            feed_mode = "history"

        from engine.strategy_ids import resolve_strategy_id_via_core

        strategy_id = resolve_strategy_id_via_core(self.core, strategy_id)
        plugin = self.core.plugin_manager.get(strategy_id)
        if plugin is None:
            raise RuntimeError(f"strategy plugin not loaded: {strategy_id}")

        if start_equity is not None:
            try:
                self.config["start_equity"] = float(start_equity)
            except (TypeError, ValueError):
                pass
        if fee_bps is not None:
            try:
                self.config["fee_bps"] = float(fee_bps)
                self._fee_bps = float(fee_bps)
            except (TypeError, ValueError):
                pass
        if slippage_bps is not None:
            try:
                self.config["slippage_bps"] = float(slippage_bps)
                self._slippage_bps = float(slippage_bps)
            except (TypeError, ValueError):
                pass

        sizing_overrides = dict(sizing or {})
        self.apply_sizing_config(sizing_overrides)

        base_params = dict(strategy_params or {})
        # Drop non-strategy keys that UI may bundle.
        for junk in ("quantity",):
            # Keep quantity only for fixed mode as a hint; otherwise sizer owns size.
            if self.sizing.mode != SizingMode.FIXED:
                base_params.pop(junk, None)

        if variants is None:
            from host.phantom_variants import variants_for_mode

            schema = getattr(plugin, "CONFIG_SCHEMA", None)
            if not isinstance(schema, dict):
                schema = None
            variants = variants_for_mode(
                strategy_id,
                "grid",
                max_variants=12,
                schema=schema,
            )
        variants = [{**base_params, **dict(v or {})} for v in variants]

        hist = self.core.service_registry.get("historical_data_service")
        if feed_mode in {"history", "both"} and (
            hist is None or not hasattr(hist, "replay")
        ):
            if feed_mode == "history":
                raise RuntimeError("historical_data_service.replay unavailable")
            feed_mode = "patterns"

        self._runtime_cfg = await self._build_runtime_cfg()

        ens = self.core.service_registry.get("ensemble_service")
        ens_was_running = bool(ens and getattr(ens, "is_running", False))
        if ens_was_running:
            await ens.stop()

        orig_cfg = dict(getattr(plugin, "config", {}) or {})
        results: list[dict[str, Any]] = []
        unfilled: list[dict[str, Any]] = []
        instance_id = str(await self.core.config.get("instance.id", "local") or "local")
        self._grid_running = True
        # Always capture strategy.signal during research, even if the Operator
        # toggled live phantom capture off or capture_source=ensemble.
        was_capturing = bool(self._capturing)
        self._capturing = True
        self._last_ohlc = None

        pattern_specs: list[PatternSpec] = []
        # The timeframe the history feed will actually replay. `run_grid` used to
        # omit this from `hist.replay(...)`, so the replay silently fell back to
        # the service default (1m) no matter what the research run asked for.
        # Measured consequence on `BTC/USDT` "1h": the replay published 60-second
        # bars, and momentum ROC collapsed from ±0.036..0.059 to
        # ±0.008..0.005 — 89 threshold crossings became 0, so the strategy went
        # silent while every result was still labelled 1h.
        history_timeframe: str | None = None
        if feed_mode in {"patterns", "both"}:
            bars = int(limit or self.config.get("pattern_bars") or 80)
            px = float(
                start_price
                if start_price is not None
                else self.config.get("pattern_start_price")
                or 100.0
            )
            # Operator default: one scenario, not the full catalog.
            # Explicit non-empty list (REPL / what_if / UI multi-select) is honoured.
            selected = list(patterns) if patterns else ["sharp_rise"]
            pattern_specs = default_pattern_specs(
                bars=bars,
                start_price=px,
                base_seed=int(pattern_seed),
                patterns=selected,
            )

        # Resolved here so the result row can report it on both feed paths. Set
            # properly in the history branch below; a pattern-only run never used
            # a timeframe at all.
            history_timeframe = str(
                (self.config.get("walk_forward") or {}).get("timeframe") or ""
            )
            wf_tf_defaulted = not history_timeframe
        try:
            if feed_mode in {"history", "both"} and hist is not None and hasattr(hist, "fetch"):
                _requested_tf = (self.config.get("walk_forward") or {}).get("timeframe")
                wf_tf_defaulted = not _requested_tf
                wf_tf = str(_requested_tf or "1d")
                if wf_tf_defaulted:
                    _log().warning(
                        "run_grid: no walk_forward.timeframe configured; "
                        "falling back to %s. A result labelled %s would not "
                        "come from %s bars. Set "
                        "phantom.config['walk_forward']['timeframe'].",
                        wf_tf,
                        wf_tf,
                        wf_tf,
                    )
                history_timeframe = wf_tf
                bars_hist = await hist.fetch(
                    ticker,
                    class_code=class_code,
                    provider=provider,
                    limit=limit,
                    timeframe=wf_tf,
                )
                self._last_ohlc = ohlc_dict_from_candles(
                    bars_hist,
                    ticker=ticker,
                    class_code=class_code,
                    strategy_id=strategy_id,
                    provider=str(provider or "history"),
                )

            jobs: list[tuple[str, dict[str, Any], Any]] = []
            if feed_mode in {"history", "both"}:
                for var in variants:
                    jobs.append(("history", dict(var), None))
            if feed_mode in {"patterns", "both"}:
                for spec in pattern_specs:
                    for var in variants:
                        jobs.append(("pattern", dict(var), spec))

            for feed_label, var, spec in jobs:
                rid = uuid.uuid4().hex[:10]
                self.reset_ledger()
                if True:
                    path = str(self.config.get("timeline_path") or "").strip() or None
                    self.timeline = PhantomTimeline(run_id=rid, path=path)
                    if self.runtime is not None:
                        self.runtime.phantom_timeline = self.timeline
                self._bar_index = -1
                # A fresh run must not inherit the previous run's queued signals.
                self._pending_fills = []
                session_cfg = (
                    plugin.isolated_config(var)
                    if hasattr(plugin, "isolated_config")
                    else {**orig_cfg, **var}
                )
                if hasattr(plugin, "begin_research"):
                    plugin.begin_research(run_id=rid, config=session_cfg)
                else:
                    for k, v in var.items():
                        plugin.config[k] = v
                    if hasattr(plugin, "reset_state"):
                        plugin.reset_state()
                    elif hasattr(plugin, "_last_side"):
                        plugin._last_side = None  # noqa: SLF001

                pattern_name = None
                if isinstance(spec, PatternSpec):
                    pattern_name = spec.resolved_kind().value
                    self._last_pattern = pattern_name
                await publish_phantom_run(
                    self.core.event_bus,
                    source_id=self.component_id,
                    payload={
                        "run_id": rid,
                        "ticker": ticker,
                        "variant": var,
                        "strategy_id": strategy_id,
                        "feed": feed_label,
                        "pattern": pattern_name,
                    },
                    instance_id=instance_id,
                    finished=False,
                )

                try:
                    if feed_label == "history":
                        assert hist is not None
                        replay_result = await hist.replay(
                            ticker,
                            class_code=class_code,
                            provider=provider,
                            timeframe=history_timeframe,
                            limit=limit,
                            # Disjoint-window measurement needs a time range, not
                            # a bar count: varying `limit` only ever yields
                            # nested tails, so every window would share its most
                            # recent bars and the results would not be
                            # independent. Previously unreachable from run_grid,
                            # which is why the whole-sweep measurement could
                            # only ever be one window.
                            start=start,
                            end=end,
                            run_id=rid,
                            strategy_id=strategy_id,
                            delay_sec=0,
                        )
                        bars_n = replay_result.get("bars")
                    else:
                        assert isinstance(spec, PatternSpec)
                        candles = generate_pattern_candles(
                            ticker, spec, class_code=class_code or "SYNTH"
                        )
                        self._last_ohlc = ohlc_dict_from_candles(
                            candles,
                            ticker=ticker,
                            class_code=class_code or "SYNTH",
                            strategy_id=strategy_id,
                            provider=f"pattern:{spec.resolved_kind().value}",
                        )
                        replay_result = await self.replay_candles(
                            candles,
                            class_code=class_code or "SYNTH",
                            run_id=rid,
                            strategy_id=strategy_id,
                            source=f"pattern:{spec.resolved_kind().value}",
                        )
                        bars_n = replay_result.get("bars")

                    await asyncio.sleep(0.15)
                    ens2 = self.core.service_registry.get("ensemble_service")
                    if ens2 and getattr(ens2, "_flush_task", None):
                        await asyncio.sleep(0.1)

                    metrics = self._metrics()
                    timeline_ui = (
                        self.timeline.export_ui() if self.timeline is not None else None
                    )
                    ohlc_snap = (
                        dict(self._last_ohlc)
                        if isinstance(self._last_ohlc, dict)
                        else None
                    )
                    row = {
                        "run_id": rid,
                        "variant": var,
                        "feed": feed_label,
                        "pattern": pattern_name,
                        "bars": bars_n,
                        # The timeframe that actually produced these bars.
                        # `run_grid` resolves it from the walk_forward config
                        # block and silently falls back to "1d" when the caller
                        # set nothing, so a result could be labelled 1h while
                        # being measured on a different series entirely. A new
                        # tool hit exactly that and reported zero trades as if
                        # the strategies had none. `timeframe_defaulted` says
                        # outright that nobody asked for this interval.
                        "timeframe": history_timeframe,
                        "timeframe_defaulted": bool(wf_tf_defaulted),
                        "fills": len(self._fills),
                        "recent_fills": list(self._fills[-80:]),
                        "timeline": timeline_ui,
                        "ohlc": ohlc_snap,
                        "pnl": metrics["pnl"],
                        "max_drawdown": metrics["max_drawdown"],
                        "trades": metrics["trades"],
                        "final_equity": metrics["final_equity"],
                        "total_costs": metrics.get("total_costs"),
                        # Surface why nothing happened when nothing happened.
                        **self.shadow_reject_summary(),
                    }
                    results.append(row)
                    await publish_phantom_run(
                        self.core.event_bus,
                        source_id=self.component_id,
                        payload=row,
                        instance_id=instance_id,
                        finished=True,
                    )
                    self.persist_state()
                finally:
                    if hasattr(plugin, "end_research"):
                        plugin.end_research()
        finally:
            self._grid_running = False
            self._capturing = was_capturing
            if hasattr(plugin, "end_research"):
                plugin.end_research()
            else:
                plugin.config.clear()
                plugin.config.update(orig_cfg)
            if ens_was_running and ens is not None:
                await ens.start()
            # Drop open/exit_pending leftovers from the last variant so the next
            # Operator session does not inherit stuck phantom positions.
            try:
                self.reset_ledger()
                self.persist_state()
            except Exception:
                pass
            # A signal on the final bar of a variant has no next bar to fill
            # against under `next_open`. Report it so the grid's trade count is
            # honest rather than quietly short by one per variant.
            for skipped in self.flush_pending_fills():
                unfilled.append(
                    {
                        "run_id": skipped.get("run_id") or "",
                        "status": "unfilled",
                        "reason": "no_next_bar",
                        "bars": self._bar_index,
                        "ticker": skipped.get("ticker"),
                        "action": skipped.get("action"),
                        "strategy_id": skipped.get("strategy_id"),
                    }
                )

        # `results` is one row per variant. The unfilled-signal notices used to be
        # appended into the same list, which gave them a second, incompatible
        # shape: no `feed`, no `pnl`, `trades: 0`. Anything that consumed the
        # grid positionally — a research report, a UI panel, the sweep tool —
        # then read one of those as a variant result and reported a run that
        # traded nothing, while the real row next to it held the actual trades.
        # They are reported as a count on each row and in full on
        # `last_unfilled_signals`, keeping `results` single-shaped.
        for row in results:
            row["unfilled_signals"] = len(unfilled)
        self._last_unfilled_signals = list(unfilled)
        self._last_grid = results
        self._last_run = results[-1] if results else None
        return results

    def _estimate_offline_cost(self, *, quantity: float, price: float, side: str) -> float:
        schedule = (
            self.runtime.orders.cost_schedule
            if self.runtime is not None
            else None
        )
        if schedule is not None:
            try:
                breakdown = schedule.calculate(
                    TradeContext(
                        venue="phantom",
                        account="phantom",
                        asset="OFFLINE",
                        asset_class="equity",
                        symbol="OFFLINE",
                        side=side.lower(),
                        position_effect="open",
                        order_type="market",
                        liquidity="unknown",
                        quantity=Decimal(str(quantity)),
                        price=Decimal(str(price)),
                    )
                )
                return float(breakdown.total)
            except Exception:
                pass
        return (
            abs(float(quantity) * float(price))
            * (self._fee_bps + self._slippage_bps)
            / 10_000.0
        )

    def offline_ma_cross_metrics(
        self,
        closes: list[float],
        *,
        fast: int = 3,
        slow: int = 8,
        quantity: float = 10.0,
    ) -> dict[str, Any]:
        """Deterministic offline phantom (no EventBus) — useful for unit tests."""
        if slow <= fast:
            slow = fast + 1
        fills: list[dict[str, Any]] = []
        last_side: str | None = None
        for i in range(len(closes)):
            window = closes[: i + 1]
            if len(window) < slow:
                continue
            f = sma(window, fast)[-1]
            s = sma(window, slow)[-1]
            if f is None or s is None:
                continue
            side = "BUY" if f > s else "SELL"
            if side == last_side:
                continue
            last_side = side
            px = float(closes[i])
            fills.append(
                {
                    "action": side,
                    "ticker": "OFFLINE",
                    "quantity": quantity,
                    "price": px,
                    "cost": self._estimate_offline_cost(
                        quantity=quantity, price=px, side=side
                    ),
                }
            )
        return equity_pnl(
            fills,
            start_equity=float(self.config.get("start_equity") or 10_000.0),
            mark_price=float(closes[-1]) if closes else None,
        )
