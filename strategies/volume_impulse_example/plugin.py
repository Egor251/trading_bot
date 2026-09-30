"""Volume impulse — volume spike + return direction (microstructure proxy)."""

from __future__ import annotations

import asyncio
from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.indicators import sma
from engine.strategy_emit import (
    emit_on_side_change,
    init_side_state,
    restore_side_state,
    snapshot_side_state,
)
from engine.strategy_hold import maintain_side_hold
from engine.strategy_plugin import StrategyDecision, StrategyPlugin


class VolumeImpulseExamplePlugin(StrategyPlugin):
    PLUGIN_ID = "volume_impulse_example"
    PLUGIN_NAME = "Volume Impulse Example"
    RUN_MODE = PluginRunMode.CONTINUOUS
    STRATEGY_VERSION = "1.1.0"
    CONFIG_SCHEMA = {
        "vol_window": {"type": "int", "default": 20, "min": 5, "max": 200},
        "vol_mult": {"type": "float", "default": 2.0, "min": 1.1, "max": 10.0},
        "min_return": {"type": "float", "default": 0.001, "min": 0.0, "max": 0.1},
        "quantity": {"type": "float", "default": 0.01},
        "class_code": {"type": "str", "default": "CRYPTO"},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        init_side_state(self)

    def warmup_bars(self) -> int:
        return max(5, int(self.isolated_config().get("vol_window") or 20)) + 1

    def required_data(self) -> tuple[str, ...]:
        return ("close", "volume")

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
        window = max(5, int(cfg.get("vol_window", 20)))
        vol_mult = float(cfg.get("vol_mult", 2.0))
        min_ret = float(cfg.get("min_return", 0.001))
        closes = list(series.get("close") or [])[: index + 1]
        volumes = list(series.get("volume") or [])[: index + 1]
        if len(closes) < window + 1 or len(volumes) < window + 1:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")

        # Average volume of prior window (exclude current) — causal.
        prior_vol = volumes[-(window + 1) : -1]
        avg_vol = sma(prior_vol, window)[-1]
        if avg_vol is None or float(avg_vol) <= 0:
            return maintain_side_hold(self, reason="no_vol", metrics={"close": float(closes[-1])})

        vol = float(volumes[-1])
        prev = float(closes[-2])
        close = float(closes[-1])
        ret = (close - prev) / max(abs(prev), 1e-9)
        metrics = {
            "vol_window": window,
            "vol_mult": vol_mult,
            "volume": vol,
            "avg_volume": round(float(avg_vol), 4),
            "return": round(float(ret), 6),
            "close": close,
        }
        if vol < float(avg_vol) * vol_mult:
            return maintain_side_hold(self, reason="no_impulse", metrics=metrics)
        if ret >= min_ret:
            return StrategyDecision(
                action="BUY",
                strength=min(1.0, abs(ret) / max(min_ret * 3, 1e-9)),
                reason="volume_impulse_up",
                metrics=metrics,
                intent="long",
            )
        if ret <= -min_ret:
            return StrategyDecision(
                action="SELL",
                strength=min(1.0, abs(ret) / max(min_ret * 3, 1e-9)),
                reason="volume_impulse_down",
                metrics=metrics,
                intent="short",
            )
        return maintain_side_hold(self, reason="weak_return", metrics=metrics)

    async def _on_candle(self, event: KernelEvent) -> None:
        decision = await self.handle_candle(event)
        await emit_on_side_change(self, event, decision)

    def describe(self) -> list[str]:
        return [
            "Volume impulse",
            "Volume spike vs prior SMA + signed return → hold until opposite impulse",
        ]
