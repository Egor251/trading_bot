"""Keltner channel breakout — close outside EMA+/-ATR bands."""

from __future__ import annotations

import asyncio
from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.indicators import keltner

from engine.strategy_emit import (
    emit_on_side_change,
    init_side_state,
    restore_side_state,
    snapshot_side_state,
)
from engine.strategy_plugin import StrategyDecision, StrategyPlugin


class KeltnerBreakoutPlugin(StrategyPlugin):
    PLUGIN_ID = "keltner_breakout"
    PLUGIN_NAME = "Keltner Breakout"
    RUN_MODE = PluginRunMode.CONTINUOUS
    STRATEGY_VERSION = "1.0.0"
    RISK_PROFILE = {"archetype": "breakout"}
    CONFIG_SCHEMA = {
        "ema_period": {"type": "int", "default": 20, "min": 5, "max": 100},
        "atr_period": {"type": "int", "default": 10, "min": 3, "max": 50},
        "mult": {"type": "float", "default": 1.5, "min": 0.5, "max": 5.0},
        "quantity": {"type": "float", "default": 0.01},
        "class_code": {"type": "str", "default": "CRYPTO"},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        init_side_state(self)

    def warmup_bars(self) -> int:
        cfg = self.isolated_config()
        return max(int(cfg.get("ema_period") or 20), int(cfg.get("atr_period") or 10)) + 2

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
        ema_period = max(5, int(cfg.get("ema_period", 20)))
        atr_period = max(3, int(cfg.get("atr_period", 10)))
        mult = float(cfg.get("mult", 1.5))
        highs = list(series.get("high") or [])[: index + 1]
        lows = list(series.get("low") or [])[: index + 1]
        closes = list(series.get("close") or [])[: index + 1]
        if len(closes) < max(ema_period, atr_period) + 1:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        mid, up, lo = keltner(
            highs, lows, closes, ema_period=ema_period, atr_period=atr_period, mult=mult
        )
        m, u, l = mid[-1], up[-1], lo[-1]
        if m is None or u is None or l is None:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        close = float(closes[-1])
        metrics = {
            "mid": round(float(m), 4),
            "upper": round(float(u), 4),
            "lower": round(float(l), 4),
            "close": close,
        }
        if close > float(u):
            return StrategyDecision(
                action="BUY",
                strength=min(1.0, (close - float(u)) / max(close, 1e-9) + 0.35),
                reason="keltner_up",
                metrics=metrics,
                intent="long",
            )
        if close < float(l):
            return StrategyDecision(
                action="SELL",
                strength=min(1.0, (float(l) - close) / max(close, 1e-9) + 0.35),
                reason="keltner_down",
                metrics=metrics,
                intent="short",
            )
        return StrategyDecision(
            action="HOLD", reason="inside_keltner", metrics=metrics, intent="flat"
        )

    async def _on_candle(self, event: KernelEvent) -> None:
        decision = await self.handle_candle(event)
        await emit_on_side_change(self, event, decision)

    def describe(self) -> list[str]:
        return ["Keltner breakout", "Close beyond EMA+/-ATR channel"]
