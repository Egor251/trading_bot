"""ADX-gated DI+/DI- trend follower."""

from __future__ import annotations

import asyncio
from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.indicators import adx

from engine.strategy_emit import (
    emit_on_side_change,
    init_side_state,
    restore_side_state,
    snapshot_side_state,
)
from engine.strategy_plugin import StrategyDecision, StrategyPlugin


class AdxDiTrendPlugin(StrategyPlugin):
    PLUGIN_ID = "adx_di_trend"
    PLUGIN_NAME = "ADX DI Trend"
    RUN_MODE = PluginRunMode.CONTINUOUS
    STRATEGY_VERSION = "1.0.0"
    RISK_PROFILE = {"archetype": "trend"}
    CONFIG_SCHEMA = {
        "period": {"type": "int", "default": 14, "min": 5, "max": 50},
        "adx_min": {"type": "float", "default": 25.0, "min": 10.0, "max": 50.0},
        "quantity": {"type": "float", "default": 0.01},
        "class_code": {"type": "str", "default": "CRYPTO"},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        init_side_state(self)

    def warmup_bars(self) -> int:
        return max(20, int(self.isolated_config().get("period") or 14) * 2 + 2)

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
        period = max(5, int(cfg.get("period", 14)))
        adx_min = float(cfg.get("adx_min", 25.0))
        highs = list(series.get("high") or [])[: index + 1]
        lows = list(series.get("low") or [])[: index + 1]
        closes = list(series.get("close") or [])[: index + 1]
        if len(closes) < period * 2 + 1:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        adx_s, plus_di, minus_di = adx(highs, lows, closes, period)
        a, p, m = adx_s[-1], plus_di[-1], minus_di[-1]
        if a is None or p is None or m is None:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        close = float(closes[-1])
        metrics = {
            "adx": round(float(a), 3),
            "plus_di": round(float(p), 3),
            "minus_di": round(float(m), 3),
            "adx_min": adx_min,
            "close": close,
        }
        if float(a) < adx_min:
            return StrategyDecision(
                action="HOLD", reason="adx_weak", metrics=metrics, intent="flat"
            )
        if float(p) > float(m):
            strength = min(1.0, float(a) / 50.0)
            return StrategyDecision(
                action="BUY",
                strength=strength,
                reason="di_bull",
                metrics=metrics,
                intent="long",
            )
        strength = min(1.0, float(a) / 50.0)
        return StrategyDecision(
            action="SELL",
            strength=strength,
            reason="di_bear",
            metrics=metrics,
            intent="short",
        )

    async def _on_candle(self, event: KernelEvent) -> None:
        decision = await self.handle_candle(event)
        await emit_on_side_change(self, event, decision)

    def describe(self) -> list[str]:
        return ["ADX DI trend", "+DI/-DI when ADX above threshold"]
