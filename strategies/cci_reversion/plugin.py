"""CCI mean reversion — fade +/- threshold extremes."""

from __future__ import annotations

import asyncio
from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.indicators import cci

from engine.strategy_emit import (
    emit_on_side_change,
    init_side_state,
    restore_side_state,
    snapshot_side_state,
)
from engine.strategy_hold import neutral_hold
from engine.strategy_plugin import StrategyDecision, StrategyPlugin


class CciReversionPlugin(StrategyPlugin):
    PLUGIN_ID = "cci_reversion"
    PLUGIN_NAME = "CCI Reversion"
    RUN_MODE = PluginRunMode.CONTINUOUS
    STRATEGY_VERSION = "1.0.0"
    RISK_PROFILE = {"archetype": "mean_reversion"}
    CONFIG_SCHEMA = {
        "period": {"type": "int", "default": 20, "min": 5, "max": 100},
        "entry": {"type": "float", "default": 100.0, "min": 50.0, "max": 300.0},
        "quantity": {"type": "float", "default": 0.01},
        "class_code": {"type": "str", "default": "CRYPTO"},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        init_side_state(self)

    def warmup_bars(self) -> int:
        return max(5, int(self.isolated_config().get("period") or 20) + 1)

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
        period = max(5, int(cfg.get("period", 20)))
        entry = abs(float(cfg.get("entry", 100.0)))
        highs = list(series.get("high") or [])[: index + 1]
        lows = list(series.get("low") or [])[: index + 1]
        closes = list(series.get("close") or [])[: index + 1]
        if len(closes) < period + 1:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        vals = cci(highs, lows, closes, period)
        cur = vals[-1]
        if cur is None:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        close = float(closes[-1])
        metrics = {"cci": round(float(cur), 3), "entry": entry, "close": close}
        if float(cur) <= -entry:
            strength = min(1.0, abs(float(cur)) / max(entry * 2.0, 1e-9))
            return StrategyDecision(
                action="BUY",
                strength=strength,
                reason="cci_oversold",
                metrics=metrics,
                intent="long",
            )
        if float(cur) >= entry:
            strength = min(1.0, abs(float(cur)) / max(entry * 2.0, 1e-9))
            return StrategyDecision(
                action="SELL",
                strength=strength,
                reason="cci_overbought",
                metrics=metrics,
                intent="short",
            )
        return neutral_hold(self, reason="cci_neutral", metrics=metrics)

    async def _on_candle(self, event: KernelEvent) -> None:
        decision = await self.handle_candle(event)
        await emit_on_side_change(self, event, decision)

    def describe(self) -> list[str]:
        return ["CCI mean reversion", "Fade CCI beyond entry threshold"]
