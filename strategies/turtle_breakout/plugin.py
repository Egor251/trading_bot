"""Classic Turtle: N-day Donchian entry / M-day exit channel."""

from __future__ import annotations

import asyncio
from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.strategy_emit import (
    emit_on_side_change,
    init_side_state,
    restore_side_state,
    snapshot_side_state,
)
from engine.strategy_plugin import StrategyDecision, StrategyPlugin


class TurtleBreakoutPlugin(StrategyPlugin):
    PLUGIN_ID = "turtle_breakout"
    PLUGIN_NAME = "Turtle Breakout"
    RUN_MODE = PluginRunMode.CONTINUOUS
    STRATEGY_VERSION = "1.0.0"
    RISK_PROFILE = {"archetype": "breakout"}
    CONFIG_SCHEMA = {
        "entry_lookback": {"type": "int", "default": 20, "min": 5, "max": 100},
        "exit_lookback": {"type": "int", "default": 10, "min": 3, "max": 50},
        "quantity": {"type": "float", "default": 0.01},
        "class_code": {"type": "str", "default": "CRYPTO"},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        init_side_state(self)

    def warmup_bars(self) -> int:
        cfg = self.isolated_config()
        return max(int(cfg.get("entry_lookback") or 20), int(cfg.get("exit_lookback") or 10)) + 2

    def required_data(self) -> tuple[str, ...]:
        return ("high", "low", "close")

    def _snapshot_extra_state(self) -> dict[str, Any]:
        return snapshot_side_state(self)

    def _restore_extra_state(self, extra: dict[str, Any]) -> None:
        restore_side_state(self, extra)

    def reset_state(self) -> None:
        super().reset_state()
        self._last_side = None

    async def on_initialize(self) -> None:
        await self.subscribe_event("market.candle", self._on_candle)

    async def run_once(self) -> None:
        await asyncio.sleep(0.5)

    def evaluate_at(
        self,
        series: dict[str, list[float]],
        index: int,
        *,
        config: dict[str, Any] | None = None,
    ) -> StrategyDecision | None:
        cfg = config or self.isolated_config()
        entry_n = max(5, int(cfg.get("entry_lookback", 20)))
        exit_n = max(3, int(cfg.get("exit_lookback", 10)))
        highs = list(series.get("high") or [])[: index + 1]
        lows = list(series.get("low") or [])[: index + 1]
        closes = list(series.get("close") or [])[: index + 1]
        need = max(entry_n, exit_n) + 1
        if len(closes) < need:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        prior_h = highs[-(entry_n + 1) : -1]
        prior_l = lows[-(entry_n + 1) : -1]
        exit_h = highs[-(exit_n + 1) : -1]
        exit_l = lows[-(exit_n + 1) : -1]
        if len(prior_h) < entry_n or len(exit_h) < exit_n:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        entry_up, entry_dn = max(prior_h), min(prior_l)
        exit_up, exit_dn = max(exit_h), min(exit_l)
        close = float(closes[-1])
        metrics = {
            "entry_up": round(float(entry_up), 4),
            "entry_dn": round(float(entry_dn), 4),
            "exit_up": round(float(exit_up), 4),
            "exit_dn": round(float(exit_dn), 4),
            "close": close,
        }
        if close > entry_up:
            return StrategyDecision(
                action="BUY",
                strength=min(1.0, (close - entry_up) / max(close, 1e-9) + 0.4),
                reason="turtle_entry_long",
                metrics=metrics,
                intent="long",
            )
        if close < entry_dn:
            return StrategyDecision(
                action="SELL",
                strength=min(1.0, (entry_dn - close) / max(close, 1e-9) + 0.4),
                reason="turtle_entry_short",
                metrics=metrics,
                intent="short",
            )
        # Exit channels (classic turtle): flatten only when the protective
        # channel is breached — do NOT flat while price sits between entry
        # and exit (that was wiping every hold bar).
        side = str(getattr(self, "_last_side", None) or "").upper()
        if side == "BUY":
            if close < exit_dn:
                return StrategyDecision(
                    action="HOLD",
                    reason="turtle_exit_long",
                    metrics=metrics,
                    intent="flat",
                )
            return StrategyDecision(
                action="HOLD",
                reason="turtle_hold_long",
                metrics=metrics,
                intent="long",
            )
        if side == "SELL":
            if close > exit_up:
                return StrategyDecision(
                    action="HOLD",
                    reason="turtle_exit_short",
                    metrics=metrics,
                    intent="flat",
                )
            return StrategyDecision(
                action="HOLD",
                reason="turtle_hold_short",
                metrics=metrics,
                intent="short",
            )
        return StrategyDecision(
            action="HOLD", reason="turtle_flat", metrics=metrics, intent="flat"
        )

    async def _on_candle(self, event: KernelEvent) -> None:
        decision = await self.handle_candle(event)
        await emit_on_side_change(self, event, decision)

    def describe(self) -> list[str]:
        return ["Turtle breakout", "N-day entry Donchian / M-day exit"]
