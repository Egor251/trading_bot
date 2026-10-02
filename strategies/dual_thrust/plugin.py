"""Dual Thrust range breakout from open +/- k * range."""

from __future__ import annotations

import asyncio
from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.strategy_emit import (
    emit_on_side_change,
    init_side_state,
    restore_side_state,
    snapshot_side_state,
)
from engine.strategy_plugin import StrategyDecision, StrategyPlugin


class DualThrustPlugin(StrategyPlugin):
    PLUGIN_ID = "dual_thrust"
    PLUGIN_NAME = "Dual Thrust"
    RUN_MODE = PluginRunMode.CONTINUOUS
    STRATEGY_VERSION = "1.0.0"
    RISK_PROFILE = {"archetype": "breakout"}
    CONFIG_SCHEMA = {
        "lookback": {"type": "int", "default": 4, "min": 2, "max": 40},
        "k1": {"type": "float", "default": 0.5, "min": 0.1, "max": 2.0},
        "k2": {"type": "float", "default": 0.5, "min": 0.1, "max": 2.0},
        "quantity": {"type": "float", "default": 0.01},
        "class_code": {"type": "str", "default": "CRYPTO"},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        init_side_state(self)

    def warmup_bars(self) -> int:
        return max(3, int(self.isolated_config().get("lookback") or 4) + 2)

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
        lookback = max(2, int(cfg.get("lookback", 4)))
        k1 = float(cfg.get("k1", 0.5))
        k2 = float(cfg.get("k2", 0.5))
        highs = list(series.get("high") or [])[: index + 1]
        lows = list(series.get("low") or [])[: index + 1]
        closes = list(series.get("close") or [])[: index + 1]
        if len(closes) < lookback + 2:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        ph = highs[-(lookback + 1) : -1]
        pl = lows[-(lookback + 1) : -1]
        pc = closes[-(lookback + 1) : -1]
        if len(ph) < lookback:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        hh = max(ph)
        lc = min(pc)
        hc = max(pc)
        ll = min(pl)
        rng = max(hh - lc, hc - ll, 1e-9)
        # Session Dual Thrust uses today's open; on continuous crypto bars the
        # open often gaps with the move, so prior close is the stable base.
        base = float(closes[-2])
        close = float(closes[-1])
        buy_line = base + k1 * rng
        sell_line = base - k2 * rng
        metrics = {
            "range": round(float(rng), 4),
            "base": round(base, 4),
            "buy_line": round(buy_line, 4),
            "sell_line": round(sell_line, 4),
            "close": close,
        }
        if close > buy_line:
            return StrategyDecision(
                action="BUY",
                strength=min(1.0, (close - buy_line) / max(close, 1e-9) + 0.35),
                reason="dual_thrust_up",
                metrics=metrics,
                intent="long",
            )
        if close < sell_line:
            return StrategyDecision(
                action="SELL",
                strength=min(1.0, (sell_line - close) / max(close, 1e-9) + 0.35),
                reason="dual_thrust_down",
                metrics=metrics,
                intent="short",
            )
        return StrategyDecision(
            action="HOLD", reason="dual_thrust_inside", metrics=metrics, intent="flat"
        )

    async def _on_candle(self, event: KernelEvent) -> None:
        decision = await self.handle_candle(event)
        await emit_on_side_change(self, event, decision)

    def describe(self) -> list[str]:
        return ["Dual Thrust", "Prior-close +/- k*prior-range breakout"]
