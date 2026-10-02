"""EMA crossover — popular trend-following golden/death cross variant."""

from __future__ import annotations

import asyncio
from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.indicators import ema
from engine.strategy_emit import (
    emit_on_side_change,
    init_side_state,
    restore_side_state,
    snapshot_side_state,
)
from engine.strategy_plugin import StrategyDecision, StrategyPlugin


class EmaCrossPlugin(StrategyPlugin):
    PLUGIN_ID = "ema_cross"
    PLUGIN_NAME = "EMA Cross"
    RUN_MODE = PluginRunMode.CONTINUOUS
    STRATEGY_VERSION = "1.0.0"
    RISK_PROFILE = {"archetype": "trend"}
    CONFIG_SCHEMA = {
        "fast": {"type": "int", "default": 12, "min": 2, "max": 50},
        "slow": {"type": "int", "default": 26, "min": 5, "max": 200},
        "quantity": {"type": "float", "default": 0.01},
        "class_code": {"type": "str", "default": "CRYPTO"},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        init_side_state(self)

    def warmup_bars(self) -> int:
        cfg = self.isolated_config()
        return max(int(cfg.get("slow") or 26), int(cfg.get("fast") or 12) + 1)

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
        fast = int(cfg.get("fast", 12))
        slow = int(cfg.get("slow", 26))
        if slow <= fast:
            slow = fast + 1
        closes = list(series.get("close") or [])[: index + 1]
        if len(closes) < slow:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        fast_ma = ema(closes, fast)[-1]
        slow_ma = ema(closes, slow)[-1]
        if fast_ma is None or slow_ma is None:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        side = "BUY" if float(fast_ma) > float(slow_ma) else "SELL"
        close = float(closes[-1])
        spread = abs(float(fast_ma) - float(slow_ma))
        strength = min(1.0, spread / max(close * 0.01, 1e-9))
        metrics = {
            "fast": fast,
            "slow": slow,
            "fast_ema": round(float(fast_ma), 4),
            "slow_ema": round(float(slow_ma), 4),
            "close": close,
        }
        return StrategyDecision(
            action=side,
            strength=float(strength),
            reason=f"ema_cross_{side.lower()}",
            metrics=metrics,
            intent="long" if side == "BUY" else "short",
        )

    async def _on_candle(self, event: KernelEvent) -> None:
        decision = await self.handle_candle(event)
        await emit_on_side_change(self, event, decision)

    def describe(self) -> list[str]:
        return ["EMA crossover trend", "Fast/slow EMA cross on closes"]
