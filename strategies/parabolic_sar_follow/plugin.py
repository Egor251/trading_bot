"""Parabolic SAR flip trend follower."""

from __future__ import annotations

import asyncio
from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.indicators import parabolic_sar

from engine.strategy_emit import (
    emit_on_side_change,
    init_side_state,
    restore_side_state,
    snapshot_side_state,
)
from engine.strategy_plugin import StrategyDecision, StrategyPlugin


class ParabolicSarFollowPlugin(StrategyPlugin):
    PLUGIN_ID = "parabolic_sar_follow"
    PLUGIN_NAME = "Parabolic SAR Follow"
    RUN_MODE = PluginRunMode.CONTINUOUS
    STRATEGY_VERSION = "1.0.0"
    RISK_PROFILE = {"archetype": "trend"}
    CONFIG_SCHEMA = {
        "af_start": {"type": "float", "default": 0.02, "min": 0.005, "max": 0.1},
        "af_step": {"type": "float", "default": 0.02, "min": 0.005, "max": 0.1},
        "af_max": {"type": "float", "default": 0.2, "min": 0.05, "max": 0.5},
        "quantity": {"type": "float", "default": 0.01},
        "class_code": {"type": "str", "default": "CRYPTO"},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        init_side_state(self)

    def warmup_bars(self) -> int:
        return 5

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
        highs = list(series.get("high") or [])[: index + 1]
        lows = list(series.get("low") or [])[: index + 1]
        closes = list(series.get("close") or [])[: index + 1]
        if len(closes) < 3:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        sar_s, trend = parabolic_sar(
            highs,
            lows,
            af_start=float(cfg.get("af_start", 0.02)),
            af_step=float(cfg.get("af_step", 0.02)),
            af_max=float(cfg.get("af_max", 0.2)),
        )
        s, t = sar_s[-1], trend[-1]
        if s is None or t is None:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        close = float(closes[-1])
        metrics = {"sar": round(float(s), 4), "trend": int(t), "close": close}
        side = "BUY" if int(t) > 0 else "SELL"
        dist = abs(close - float(s)) / max(close, 1e-9)
        return StrategyDecision(
            action=side,
            strength=min(1.0, dist * 20.0 + 0.3),
            reason=f"sar_{side.lower()}",
            metrics=metrics,
            intent="long" if side == "BUY" else "short",
        )

    async def _on_candle(self, event: KernelEvent) -> None:
        decision = await self.handle_candle(event)
        await emit_on_side_change(self, event, decision)

    def describe(self) -> list[str]:
        return ["Parabolic SAR follow", "Flip with SAR trail direction"]
