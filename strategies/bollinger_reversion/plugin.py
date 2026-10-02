"""Bollinger Band fade — classic short-term mean reversion."""

from __future__ import annotations

import asyncio
from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.indicators import bollinger
from engine.strategy_emit import (
    emit_on_side_change,
    init_side_state,
    restore_side_state,
    snapshot_side_state,
)
from engine.strategy_hold import neutral_hold
from engine.strategy_plugin import StrategyDecision, StrategyPlugin


class BollingerReversionPlugin(StrategyPlugin):
    PLUGIN_ID = "bollinger_reversion"
    PLUGIN_NAME = "Bollinger Reversion"
    RUN_MODE = PluginRunMode.CONTINUOUS
    STRATEGY_VERSION = "1.0.0"
    RISK_PROFILE = {"archetype": "mean_reversion"}
    CONFIG_SCHEMA = {
        "window": {"type": "int", "default": 20, "min": 5, "max": 200},
        "num_std": {"type": "float", "default": 2.0, "min": 0.5, "max": 4.0},
        "quantity": {"type": "float", "default": 0.01},
        "class_code": {"type": "str", "default": "CRYPTO"},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        init_side_state(self)

    def warmup_bars(self) -> int:
        return max(5, int(self.isolated_config().get("window") or 20))

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
        window = max(5, int(cfg.get("window", 20)))
        num_std = float(cfg.get("num_std", 2.0))
        closes = list(series.get("close") or [])[: index + 1]
        if len(closes) < window:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        mid, upper, lower = bollinger(closes, window, num_std=num_std)
        m, u, l = mid[-1], upper[-1], lower[-1]
        if m is None or u is None or l is None:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        close = float(closes[-1])
        band = max(float(u) - float(l), 1e-9)
        metrics = {
            "window": window,
            "num_std": num_std,
            "mid": round(float(m), 4),
            "upper": round(float(u), 4),
            "lower": round(float(l), 4),
            "close": close,
        }
        if close <= float(l):
            return StrategyDecision(
                action="BUY",
                strength=min(1.0, (float(l) - close) / band + 0.4),
                reason="bb_lower_fade",
                metrics=metrics,
                intent="long",
            )
        if close >= float(u):
            return StrategyDecision(
                action="SELL",
                strength=min(1.0, (close - float(u)) / band + 0.4),
                reason="bb_upper_fade",
                metrics=metrics,
                intent="short",
            )
        return neutral_hold(self, reason="inside_bands", metrics=metrics)

    async def _on_candle(self, event: KernelEvent) -> None:
        decision = await self.handle_candle(event)
        await emit_on_side_change(self, event, decision)

    def describe(self) -> list[str]:
        return ["Bollinger mean reversion", "Fade touches of upper/lower bands"]
