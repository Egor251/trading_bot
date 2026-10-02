"""Fade deviation from rolling VWAP / typical-price band."""

from __future__ import annotations

import asyncio
from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.indicators import rolling_vwap

from engine.strategy_emit import (
    emit_on_side_change,
    init_side_state,
    restore_side_state,
    snapshot_side_state,
)
from engine.strategy_hold import neutral_hold
from engine.strategy_plugin import StrategyDecision, StrategyPlugin


class VwapReversionPlugin(StrategyPlugin):
    PLUGIN_ID = "vwap_reversion"
    PLUGIN_NAME = "VWAP Reversion"
    RUN_MODE = PluginRunMode.CONTINUOUS
    STRATEGY_VERSION = "1.0.0"
    RISK_PROFILE = {"archetype": "mean_reversion"}
    CONFIG_SCHEMA = {
        "window": {"type": "int", "default": 20, "min": 5, "max": 200},
        "entry_pct": {"type": "float", "default": 0.01, "min": 0.001, "max": 0.1},
        "quantity": {"type": "float", "default": 0.01},
        "class_code": {"type": "str", "default": "CRYPTO"},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        init_side_state(self)

    def warmup_bars(self) -> int:
        return max(5, int(self.isolated_config().get("window") or 20) + 1)

    def required_data(self) -> tuple[str, ...]:
        return ("high", "low", "close", "volume")

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
        entry_pct = float(cfg.get("entry_pct", 0.01))
        highs = list(series.get("high") or [])[: index + 1]
        lows = list(series.get("low") or [])[: index + 1]
        closes = list(series.get("close") or [])[: index + 1]
        volumes = list(series.get("volume") or [])[: index + 1] or None
        if len(closes) < window + 1:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        vwap = rolling_vwap(highs, lows, closes, volumes, window)
        v = vwap[-1]
        if v is None:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")
        close = float(closes[-1])
        dev = (close - float(v)) / max(float(v), 1e-9)
        metrics = {
            "vwap": round(float(v), 4),
            "dev": round(float(dev), 6),
            "entry_pct": entry_pct,
            "close": close,
        }
        if dev <= -entry_pct:
            return StrategyDecision(
                action="BUY",
                strength=min(1.0, abs(dev) / max(entry_pct, 1e-9) * 0.5),
                reason="vwap_oversold",
                metrics=metrics,
                intent="long",
            )
        if dev >= entry_pct:
            return StrategyDecision(
                action="SELL",
                strength=min(1.0, abs(dev) / max(entry_pct, 1e-9) * 0.5),
                reason="vwap_overbought",
                metrics=metrics,
                intent="short",
            )
        return neutral_hold(self, reason="vwap_neutral", metrics=metrics)

    async def _on_candle(self, event: KernelEvent) -> None:
        decision = await self.handle_candle(event)
        await emit_on_side_change(self, event, decision)

    def describe(self) -> list[str]:
        return ["VWAP reversion", "Fade rolling VWAP deviation"]
