"""Volatility breakout — Donchian channel + ATR buffer (causal)."""

from __future__ import annotations

import asyncio
from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.indicators import atr_pct
from engine.strategy_emit import (
    emit_on_side_change,
    init_side_state,
    restore_side_state,
    snapshot_side_state,
)
from engine.strategy_hold import maintain_side_hold
from engine.strategy_plugin import StrategyDecision, StrategyPlugin


class VolatilityBreakoutExamplePlugin(StrategyPlugin):
    PLUGIN_ID = "volatility_breakout_example"
    PLUGIN_NAME = "Volatility Breakout Example"
    RUN_MODE = PluginRunMode.CONTINUOUS
    STRATEGY_VERSION = "1.1.0"
    CONFIG_SCHEMA = {
        "lookback": {"type": "int", "default": 20, "min": 5, "max": 200},
        "atr_mult": {"type": "float", "default": 0.5, "min": 0.0, "max": 5.0},
        "atr_window": {"type": "int", "default": 14, "min": 5, "max": 100},
        "quantity": {"type": "float", "default": 0.01},
        "class_code": {"type": "str", "default": "CRYPTO"},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        init_side_state(self)

    def warmup_bars(self) -> int:
        cfg = self.isolated_config()
        return max(int(cfg.get("lookback") or 20), int(cfg.get("atr_window") or 14)) + 1

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
        lookback = max(5, int(cfg.get("lookback", 20)))
        atr_mult = float(cfg.get("atr_mult", 0.5))
        atr_window = max(5, int(cfg.get("atr_window", 14)))
        highs = list(series.get("high") or [])[: index + 1]
        lows = list(series.get("low") or [])[: index + 1]
        closes = list(series.get("close") or [])[: index + 1]
        need = max(lookback, atr_window) + 1
        if len(closes) < need:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")

        # Past channel only (exclude current bar) — no lookahead.
        prior_h = highs[-(lookback + 1) : -1]
        prior_l = lows[-(lookback + 1) : -1]
        if len(prior_h) < lookback:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")

        upper = max(prior_h)
        lower = min(prior_l)
        atr = atr_pct(highs, lows, closes, window=atr_window) or 0.0
        close = float(closes[-1])
        buf = atr_mult * atr * close
        metrics = {
            "lookback": lookback,
            "atr_mult": atr_mult,
            "atr_pct": round(float(atr), 6),
            "upper": round(float(upper), 4),
            "lower": round(float(lower), 4),
            "close": close,
        }
        if close > upper + buf:
            return StrategyDecision(
                action="BUY",
                strength=min(1.0, (close - upper) / max(close, 1e-9)),
                reason="vol_breakout_up",
                metrics=metrics,
                intent="long",
            )
        if close < lower - buf:
            return StrategyDecision(
                action="SELL",
                strength=min(1.0, (lower - close) / max(close, 1e-9)),
                reason="vol_breakout_down",
                metrics=metrics,
                intent="short",
            )
        return maintain_side_hold(self, reason="inside_channel", metrics=metrics)

    async def _on_candle(self, event: KernelEvent) -> None:
        decision = await self.handle_candle(event)
        await emit_on_side_change(self, event, decision)

    def describe(self) -> list[str]:
        return [
            "Volatility breakout",
            "Donchian + ATR buffer → hold until opposite breakout",
        ]
