"""Momentum via rate of change — ride strength / fade weakness."""

from __future__ import annotations

import asyncio
from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.indicators import roc
from engine.strategy_emit import (
    emit_on_side_change,
    init_side_state,
    restore_side_state,
    snapshot_side_state,
)
from engine.strategy_plugin import StrategyDecision, StrategyPlugin


class MomentumRocPlugin(StrategyPlugin):
    PLUGIN_ID = "momentum_roc"
    PLUGIN_NAME = "Momentum ROC"
    RUN_MODE = PluginRunMode.CONTINUOUS
    STRATEGY_VERSION = "1.0.0"
    RISK_PROFILE = {"archetype": "trend"}
    CONFIG_SCHEMA = {
        "period": {"type": "int", "default": 12, "min": 2, "max": 100},
        "entry": {"type": "float", "default": 0.02, "min": 0.001, "max": 0.5},
        "quantity": {"type": "float", "default": 0.01},
        "class_code": {"type": "str", "default": "CRYPTO"},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        init_side_state(self)

    def warmup_bars(self) -> int:
        return max(3, int(self.isolated_config().get("period") or 12)) + 1

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
        period = max(2, int(cfg.get("period", 12)))
        entry = float(cfg.get("entry", 0.02))
        closes = list(series.get("close") or [])[: index + 1]
        if len(closes) < period + 1:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        vals = roc(closes, period)
        cur = vals[-1]
        if cur is None:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        close = float(closes[-1])
        r = float(cur)
        metrics = {
            "period": period,
            "entry": entry,
            "roc": round(r, 6),
            "close": close,
        }
        if r >= entry:
            return StrategyDecision(
                action="BUY",
                strength=min(1.0, r / max(entry * 2, 1e-9)),
                reason="roc_momentum_up",
                metrics=metrics,
                intent="long",
            )
        if r <= -entry:
            return StrategyDecision(
                action="SELL",
                strength=min(1.0, abs(r) / max(entry * 2, 1e-9)),
                reason="roc_momentum_down",
                metrics=metrics,
                intent="short",
            )
        return StrategyDecision(
            action="HOLD", reason="roc_flat", metrics=metrics, intent="flat"
        )

    async def _on_candle(self, event: KernelEvent) -> None:
        decision = await self.handle_candle(event)
        await emit_on_side_change(self, event, decision)

    def describe(self) -> list[str]:
        return ["Momentum ROC", "Buy/sell when |ROC| exceeds entry threshold"]
