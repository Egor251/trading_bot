"""Heikin-Ashi color/streak trend filter."""

from __future__ import annotations

import asyncio
from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.indicators import heikin_ashi

from engine.strategy_emit import (
    emit_on_side_change,
    init_side_state,
    restore_side_state,
    snapshot_side_state,
)
from engine.strategy_plugin import StrategyDecision, StrategyPlugin


class HeikinAshiTrendPlugin(StrategyPlugin):
    PLUGIN_ID = "heikin_ashi_trend"
    PLUGIN_NAME = "Heikin Ashi Trend"
    RUN_MODE = PluginRunMode.CONTINUOUS
    STRATEGY_VERSION = "1.0.0"
    RISK_PROFILE = {"archetype": "trend"}
    CONFIG_SCHEMA = {
        "min_streak": {"type": "int", "default": 2, "min": 1, "max": 10},
        "quantity": {"type": "float", "default": 0.01},
        "class_code": {"type": "str", "default": "CRYPTO"},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        init_side_state(self)

    def warmup_bars(self) -> int:
        return max(5, int(self.isolated_config().get("min_streak") or 2) + 3)

    def required_data(self) -> tuple[str, ...]:
        return ("open", "high", "low", "close")

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
        need = max(1, int(cfg.get("min_streak", 2)))
        opens = list(series.get("open") or [])[: index + 1]
        highs = list(series.get("high") or [])[: index + 1]
        lows = list(series.get("low") or [])[: index + 1]
        closes = list(series.get("close") or [])[: index + 1]
        if len(closes) < need + 2:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        ha_o, _ha_h, _ha_l, ha_c = heikin_ashi(opens, highs, lows, closes)
        colors = [1 if ha_c[i] >= ha_o[i] else -1 for i in range(len(ha_c))]
        streak = 1
        for i in range(len(colors) - 2, -1, -1):
            if colors[i] == colors[-1]:
                streak += 1
            else:
                break
        close = float(closes[-1])
        metrics = {
            "ha_open": round(float(ha_o[-1]), 4),
            "ha_close": round(float(ha_c[-1]), 4),
            "streak": streak,
            "close": close,
        }
        if streak < need:
            return StrategyDecision(
                action="HOLD", reason="ha_weak_streak", metrics=metrics, intent="flat"
            )
        side = "BUY" if colors[-1] > 0 else "SELL"
        return StrategyDecision(
            action=side,
            strength=min(1.0, streak / 5.0),
            reason=f"ha_{side.lower()}",
            metrics=metrics,
            intent="long" if side == "BUY" else "short",
        )

    async def _on_candle(self, event: KernelEvent) -> None:
        decision = await self.handle_candle(event)
        await emit_on_side_change(self, event, decision)

    def describe(self) -> list[str]:
        return ["Heikin Ashi trend", "Follow HA color streak"]
