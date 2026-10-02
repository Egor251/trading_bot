"""ATR Supertrend — widely used trend-following trail."""

from __future__ import annotations

import asyncio
from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.indicators import atr
from engine.strategy_emit import (
    emit_on_side_change,
    init_side_state,
    restore_side_state,
    snapshot_side_state,
)
from engine.strategy_plugin import StrategyDecision, StrategyPlugin


class SupertrendFollowPlugin(StrategyPlugin):
    PLUGIN_ID = "supertrend_follow"
    PLUGIN_NAME = "Supertrend Follow"
    RUN_MODE = PluginRunMode.CONTINUOUS
    STRATEGY_VERSION = "1.0.0"
    RISK_PROFILE = {"archetype": "trend"}
    CONFIG_SCHEMA = {
        "atr_period": {"type": "int", "default": 10, "min": 5, "max": 50},
        "multiplier": {"type": "float", "default": 3.0, "min": 1.0, "max": 10.0},
        "quantity": {"type": "float", "default": 0.01},
        "class_code": {"type": "str", "default": "CRYPTO"},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        init_side_state(self)

    def warmup_bars(self) -> int:
        return max(10, int(self.isolated_config().get("atr_period") or 10)) + 2

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
        period = max(5, int(cfg.get("atr_period", 10)))
        mult = float(cfg.get("multiplier", 3.0))
        highs = list(series.get("high") or [])[: index + 1]
        lows = list(series.get("low") or [])[: index + 1]
        closes = list(series.get("close") or [])[: index + 1]
        if len(closes) < period + 2:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        atr_s = atr(highs, lows, closes, period)
        direction = 1
        final_upper = final_lower = None
        prev_close = float(closes[0])
        for i in range(len(closes)):
            a = atr_s[i]
            if a is None:
                continue
            hl2 = (float(highs[i]) + float(lows[i])) / 2.0
            basic_upper = hl2 + mult * float(a)
            basic_lower = hl2 - mult * float(a)
            if final_upper is None or final_lower is None:
                final_upper, final_lower = basic_upper, basic_lower
            else:
                final_upper = (
                    basic_upper
                    if basic_upper < final_upper or prev_close > final_upper
                    else final_upper
                )
                final_lower = (
                    basic_lower
                    if basic_lower > final_lower or prev_close < final_lower
                    else final_lower
                )
            close = float(closes[i])
            if direction == 1 and close < final_lower:
                direction = -1
            elif direction == -1 and close > final_upper:
                direction = 1
            prev_close = close
        if final_upper is None or final_lower is None:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        close = float(closes[-1])
        side = "BUY" if direction == 1 else "SELL"
        trail = final_lower if direction == 1 else final_upper
        dist = abs(close - float(trail)) / max(close, 1e-9)
        metrics = {
            "atr_period": period,
            "multiplier": mult,
            "supertrend": round(float(trail), 4),
            "direction": direction,
            "close": close,
        }
        return StrategyDecision(
            action=side,
            strength=min(1.0, dist / 0.02 + 0.25),
            reason=f"supertrend_{side.lower()}",
            metrics=metrics,
            intent="long" if side == "BUY" else "short",
        )

    async def _on_candle(self, event: KernelEvent) -> None:
        decision = await self.handle_candle(event)
        await emit_on_side_change(self, event, decision)

    def describe(self) -> list[str]:
        return [
            "ATR Supertrend trend follower",
            "Flip when price crosses Supertrend trail",
        ]
