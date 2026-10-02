"""Stochastic %K/%D mean reversion — fade oversold/overbought."""

from __future__ import annotations

import asyncio
from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.indicators import stochastic

from engine.strategy_emit import (
    emit_on_side_change,
    init_side_state,
    restore_side_state,
    snapshot_side_state,
)
from engine.strategy_hold import neutral_hold
from engine.strategy_plugin import StrategyDecision, StrategyPlugin


class StochasticReversionPlugin(StrategyPlugin):
    PLUGIN_ID = "stochastic_reversion"
    PLUGIN_NAME = "Stochastic Reversion"
    RUN_MODE = PluginRunMode.CONTINUOUS
    STRATEGY_VERSION = "1.0.0"
    RISK_PROFILE = {"archetype": "mean_reversion"}
    CONFIG_SCHEMA = {
        "k_period": {"type": "int", "default": 14, "min": 3, "max": 100},
        "d_period": {"type": "int", "default": 3, "min": 1, "max": 20},
        "oversold": {"type": "float", "default": 20.0, "min": 5.0, "max": 40.0},
        "overbought": {"type": "float", "default": 80.0, "min": 60.0, "max": 95.0},
        "quantity": {"type": "float", "default": 0.01},
        "class_code": {"type": "str", "default": "CRYPTO"},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        init_side_state(self)

    def warmup_bars(self) -> int:
        cfg = self.isolated_config()
        return max(5, int(cfg.get("k_period") or 14) + int(cfg.get("d_period") or 3) + 2)

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
        k_period = max(3, int(cfg.get("k_period", 14)))
        d_period = max(1, int(cfg.get("d_period", 3)))
        lo = float(cfg.get("oversold", 20.0))
        hi = float(cfg.get("overbought", 80.0))
        if hi <= lo:
            hi = lo + 20.0
        highs = list(series.get("high") or [])[: index + 1]
        lows = list(series.get("low") or [])[: index + 1]
        closes = list(series.get("close") or [])[: index + 1]
        if len(closes) < k_period + d_period + 1:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        k_s, d_s = stochastic(highs, lows, closes, k_period=k_period, d_period=d_period)
        k, d = k_s[-1], d_s[-1]
        if k is None or d is None:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        close = float(closes[-1])
        metrics = {
            "k": round(float(k), 3),
            "d": round(float(d), 3),
            "oversold": lo,
            "overbought": hi,
            "close": close,
        }
        if float(k) <= lo and float(d) <= lo:
            strength = min(1.0, (lo - float(k)) / max(lo, 1e-9) + 0.35)
            return StrategyDecision(
                action="BUY",
                strength=strength,
                reason="stoch_oversold",
                metrics=metrics,
                intent="long",
            )
        if float(k) >= hi and float(d) >= hi:
            strength = min(1.0, (float(k) - hi) / max(100.0 - hi, 1e-9) + 0.35)
            return StrategyDecision(
                action="SELL",
                strength=strength,
                reason="stoch_overbought",
                metrics=metrics,
                intent="short",
            )
        return neutral_hold(self, reason="stoch_neutral", metrics=metrics)

    async def _on_candle(self, event: KernelEvent) -> None:
        decision = await self.handle_candle(event)
        await emit_on_side_change(self, event, decision)

    def describe(self) -> list[str]:
        return ["Stochastic mean reversion", "Buy %K/%D oversold / sell overbought"]
