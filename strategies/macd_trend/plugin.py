"""Classic MACD trend — line vs signal crossover."""

from __future__ import annotations

import asyncio
from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.indicators import macd
from engine.strategy_emit import (
    emit_on_side_change,
    init_side_state,
    restore_side_state,
    snapshot_side_state,
)
from engine.strategy_plugin import StrategyDecision, StrategyPlugin


class MacdTrendPlugin(StrategyPlugin):
    PLUGIN_ID = "macd_trend"
    PLUGIN_NAME = "MACD Trend"
    RUN_MODE = PluginRunMode.CONTINUOUS
    STRATEGY_VERSION = "1.0.0"
    RISK_PROFILE = {"archetype": "trend"}
    CONFIG_SCHEMA = {
        "fast": {"type": "int", "default": 12, "min": 2, "max": 50},
        "slow": {"type": "int", "default": 26, "min": 5, "max": 200},
        "signal": {"type": "int", "default": 9, "min": 2, "max": 50},
        "quantity": {"type": "float", "default": 0.01},
        "class_code": {"type": "str", "default": "CRYPTO"},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        init_side_state(self)

    def warmup_bars(self) -> int:
        cfg = self.isolated_config()
        return max(int(cfg.get("slow") or 26), int(cfg.get("fast") or 12)) + int(
            cfg.get("signal") or 9
        )

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
        sig = int(cfg.get("signal", 9))
        if slow <= fast:
            slow = fast + 1
        closes = list(series.get("close") or [])[: index + 1]
        need = slow + sig
        if len(closes) < need:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        line, signal_line, hist = macd(closes, fast=fast, slow=slow, signal=sig)
        m = line[-1]
        s = signal_line[-1]
        h = hist[-1]
        if m is None or s is None or h is None:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        close = float(closes[-1])
        metrics = {
            "fast": fast,
            "slow": slow,
            "signal": sig,
            "macd": round(float(m), 6),
            "signal_line": round(float(s), 6),
            "hist": round(float(h), 6),
            "close": close,
        }
        side = "BUY" if float(m) > float(s) else "SELL"
        strength = min(1.0, abs(float(h)) / max(abs(close) * 0.002, 1e-9))
        return StrategyDecision(
            action=side,
            strength=float(strength),
            reason=f"macd_{side.lower()}",
            metrics=metrics,
            intent="long" if side == "BUY" else "short",
        )

    async def _on_candle(self, event: KernelEvent) -> None:
        decision = await self.handle_candle(event)
        await emit_on_side_change(self, event, decision)

    def describe(self) -> list[str]:
        return ["MACD trend/momentum", "MACD line vs signal → strategy.signal"]
