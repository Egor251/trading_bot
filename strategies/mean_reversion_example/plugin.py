"""Public example: mean-reversion on SMA z-score."""

from __future__ import annotations

import asyncio
import math
from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.indicators import sma
from engine.strategy_hold import neutral_hold
from engine.strategy_plugin import StrategyDecision, StrategyPlugin


class MeanReversionExamplePlugin(StrategyPlugin):
    PLUGIN_ID = "mean_reversion_example"
    PLUGIN_NAME = "Mean Reversion Example"
    RUN_MODE = PluginRunMode.CONTINUOUS
    STRATEGY_VERSION = "1.1.0"
    CONFIG_SCHEMA = {
        "window": {"type": "int", "default": 20, "min": 5, "max": 200},
        "z_entry": {"type": "float", "default": 1.5, "min": 0.5, "max": 5.0},
        "quantity": {"type": "float", "default": 10.0},
        "class_code": {"type": "str", "default": "CRYPTO"},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._signals_sent = 0
        self._last_side: str | None = None

    def warmup_bars(self) -> int:
        return max(5, int(self.isolated_config().get("window") or 20))

    def _snapshot_extra_state(self) -> dict[str, Any]:
        return {"last_side": self._last_side, "signals_sent": self._signals_sent}

    def _restore_extra_state(self, extra: dict[str, Any]) -> None:
        self._last_side = extra.get("last_side")
        self._signals_sent = int(extra.get("signals_sent") or 0)

    def reset_state(self) -> None:
        super().reset_state()
        self._last_side = None

    async def on_initialize(self) -> None:
        await self.subscribe_event("market.candle", self._on_candle)
        await self._register_rule_checks()

    async def _register_rule_checks(self) -> None:
        async def is_buy_signal(_params: dict[str, Any], payload: dict[str, Any]) -> bool:
            return (
                payload.get("event_type") == "strategy.signal"
                and str(payload.get("action", "")).upper() == "BUY"
            )

        re = getattr(self.core, "rule_engine", None)
        if re is None:
            return
        await re.register_check(self.component_id, "is_buy_signal", is_buy_signal)

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
        z_entry = float(cfg.get("z_entry", 1.5))
        closes = list(series.get("close") or [])[: index + 1]
        if len(closes) < window:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")

        mean = sma(closes, window)[-1]
        if mean is None or float(mean) <= 0:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")

        sample = [float(x) for x in closes[-window:]]
        avg = sum(sample) / len(sample)
        var = sum((x - avg) ** 2 for x in sample) / max(len(sample) - 1, 1)
        std = math.sqrt(var)
        if std < 1e-12:
            return StrategyDecision(action="HOLD", reason="flat_vol", intent="flat")

        close = float(closes[-1])
        z = (close - float(mean)) / std
        if z <= -z_entry:
            side = "BUY"
            intent = "long"
            reason = f"mean_rev_z={z:.2f}"
        elif z >= z_entry:
            side = "SELL"
            intent = "short"
            reason = f"mean_rev_z={z:.2f}"
        else:
            metrics = {
                "window": window,
                "z_entry": z_entry,
                "z": round(float(z), 4),
                "mean": round(float(mean), 4),
                "std": round(float(std), 4),
                "close": close,
            }
            return neutral_hold(self, reason="mean_rev_neutral", metrics=metrics)

        metrics = {
            "window": window,
            "z_entry": z_entry,
            "z": round(float(z), 4),
            "mean": round(float(mean), 4),
            "std": round(float(std), 4),
            "close": close,
        }
        strength = min(1.0, abs(z) / max(z_entry * 2, 1e-9))
        return StrategyDecision(
            action=side,
            strength=strength,
            reason=reason,
            metrics=metrics,
            intent=intent,
        )

    async def _on_candle(self, event: KernelEvent) -> None:
        decision = await self.handle_candle(event)
        if decision is None or decision.action == "HOLD":
            return
        if decision.action == self._last_side:
            return
        self._last_side = decision.action
        close = float((event.payload or {}).get("close") or decision.metrics.get("close") or 0)
        await self.emit_signal(
            {
                **decision.as_signal_fields(),
                "class_code": (event.payload or {}).get(
                    "class_code", self.config.get("class_code", "CRYPTO")
                ),
                "ticker": (event.payload or {}).get("ticker"),
                "quantity": float(self.config.get("quantity", 10)),
                "order_type": "limit",
                "price": close,
            }
        )
        self._signals_sent += 1

    async def run_manual(self, class_code: str, ticker: str) -> dict[str, Any]:
        await self.emit_signal(
            {
                "action": "BUY",
                "class_code": class_code,
                "ticker": ticker,
                "quantity": float(self.config.get("quantity", 10)),
                "reason": "repl_manual",
                "params_snapshot": {
                    "window": self.config.get("window", 20),
                    "z_entry": self.config.get("z_entry", 1.5),
                },
            }
        )
        self._signals_sent += 1
        return {"success": True, "ticker": ticker, "signals_sent": self._signals_sent}

    def describe(self) -> list[str]:
        return [
            "Mean reversion",
            "SMA z-score: buy oversold / sell overbought → strategy.signal",
        ]
