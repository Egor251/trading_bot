"""Williams %R mean reversion — fade extremes."""

from __future__ import annotations

import asyncio
from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.indicators import williams_r

from engine.strategy_emit import (
    emit_on_side_change,
    init_side_state,
    restore_side_state,
    snapshot_side_state,
)
from engine.strategy_hold import neutral_hold
from engine.strategy_plugin import StrategyDecision, StrategyPlugin


class WilliamsRReversionPlugin(StrategyPlugin):
    PLUGIN_ID = "williams_r_reversion"
    PLUGIN_NAME = "Williams R Reversion"
    RUN_MODE = PluginRunMode.CONTINUOUS
    STRATEGY_VERSION = "1.0.0"
    RISK_PROFILE = {"archetype": "mean_reversion"}
    CONFIG_SCHEMA = {
        "period": {"type": "int", "default": 14, "min": 3, "max": 100},
        "oversold": {"type": "float", "default": -80.0, "min": -100.0, "max": -55.0},
        "overbought": {"type": "float", "default": -20.0, "min": -45.0, "max": 0.0},
        "quantity": {"type": "float", "default": 0.01},
        "class_code": {"type": "str", "default": "CRYPTO"},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        init_side_state(self)

    def warmup_bars(self) -> int:
        return max(5, int(self.isolated_config().get("period") or 14) + 1)

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
        period = max(3, int(cfg.get("period", 14)))
        lo = float(cfg.get("oversold", -80.0))
        hi = float(cfg.get("overbought", -20.0))
        if hi <= lo:
            hi = lo + 20.0
        highs = list(series.get("high") or [])[: index + 1]
        lows = list(series.get("low") or [])[: index + 1]
        closes = list(series.get("close") or [])[: index + 1]
        if len(closes) < period + 1:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        vals = williams_r(highs, lows, closes, period)
        cur = vals[-1]
        if cur is None:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        close = float(closes[-1])
        metrics = {
            "williams_r": round(float(cur), 3),
            "oversold": lo,
            "overbought": hi,
            "close": close,
        }
        if float(cur) <= lo:
            strength = min(1.0, (lo - float(cur)) / max(abs(lo), 1e-9) + 0.35)
            return StrategyDecision(
                action="BUY",
                strength=strength,
                reason="wr_oversold",
                metrics=metrics,
                intent="long",
            )
        if float(cur) >= hi:
            strength = min(1.0, (float(cur) - hi) / max(abs(hi) + 1e-9, 1e-9) + 0.35)
            return StrategyDecision(
                action="SELL",
                strength=strength,
                reason="wr_overbought",
                metrics=metrics,
                intent="short",
            )
        return neutral_hold(self, reason="wr_neutral", metrics=metrics)

    async def _on_candle(self, event: KernelEvent) -> None:
        decision = await self.handle_candle(event)
        await emit_on_side_change(self, event, decision)

    def describe(self) -> list[str]:
        return ["Williams %R reversion", "Buy oversold / sell overbought"]
