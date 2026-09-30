"""Public example: simple MA cross on CandleStore closes (trend/momentum)."""

from __future__ import annotations

import asyncio
from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.indicators import sma
from engine.strategy_plugin import StrategyDecision, StrategyPlugin


class MaCrossExamplePlugin(StrategyPlugin):
    PLUGIN_ID = "ma_cross_example"
    PLUGIN_NAME = "MA Cross Example"
    RUN_MODE = PluginRunMode.CONTINUOUS
    STRATEGY_VERSION = "1.1.0"
    CONFIG_SCHEMA = {
        "fast": {"type": "int", "default": 3, "min": 2, "max": 50},
        "slow": {"type": "int", "default": 8, "min": 3, "max": 200},
        "quantity": {"type": "float", "default": 10.0},
        "class_code": {"type": "str", "default": "CRYPTO"},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._signals_sent = 0
        self._last_side: str | None = None

    def warmup_bars(self) -> int:
        cfg = self.isolated_config()
        return max(int(cfg.get("slow") or 8), int(cfg.get("fast") or 3) + 1)

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
        fast = int(cfg.get("fast", 3))
        slow = int(cfg.get("slow", 8))
        if slow <= fast:
            slow = fast + 1
        closes = list(series.get("close") or [])[: index + 1]
        if len(closes) < slow:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")

        fast_ma = sma(closes, fast)[-1]
        slow_ma = sma(closes, slow)[-1]
        if fast_ma is None or slow_ma is None:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")

        side = "BUY" if float(fast_ma) > float(slow_ma) else "SELL"
        close = float(closes[-1])
        # Relative MA spread → conviction (1% of price ≈ full strength).
        spread = abs(float(fast_ma) - float(slow_ma))
        strength = min(1.0, spread / max(close * 0.01, 1e-9))
        metrics = {
            "fast": fast,
            "slow": slow,
            "fast_ma": round(float(fast_ma), 4),
            "slow_ma": round(float(slow_ma), 4),
            "close": close,
            "ma_spread": round(spread, 6),
        }
        intent = "long" if side == "BUY" else "short"
        return StrategyDecision(
            action=side,
            strength=float(strength),
            reason=f"ma_cross_{side.lower()}",
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
                    "fast": self.config.get("fast", 3),
                    "slow": self.config.get("slow", 8),
                },
            }
        )
        self._signals_sent += 1
        return {"success": True, "ticker": ticker, "signals_sent": self._signals_sent}

    def describe(self) -> list[str]:
        return [
            "MA cross (trend/momentum)",
            "Fast/slow SMA cross on candle closes → strategy.signal",
        ]
