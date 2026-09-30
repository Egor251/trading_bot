"""Minimal example strategy — threshold on candle count (public)."""

from __future__ import annotations

import asyncio
from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.strategy_plugin import StrategyPlugin


class TestStrategyPlugin(StrategyPlugin):
    PLUGIN_ID = "test_strategy"
    PLUGIN_NAME = "Test Strategy"
    RUN_MODE = PluginRunMode.CONTINUOUS

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._candle_count = 0
        self._signals_sent = 0

    async def on_initialize(self) -> None:
        await self.subscribe_event("market.candle", self._on_candle)
        await self._register_rule_checks()

    async def _register_rule_checks(self) -> None:
        async def is_buy_signal(_params: dict[str, Any], payload: dict[str, Any]) -> bool:
            return (
                payload.get("event_type") == "strategy.signal"
                and payload.get("action") == "BUY"
            )

        re = getattr(self.core, "rule_engine", None)
        if re is None:
            return
        await re.register_check(self.component_id, "is_buy_signal", is_buy_signal)

    async def run_once(self) -> None:
        await asyncio.sleep(0.5)

    async def _on_candle(self, event: KernelEvent) -> None:
        ticker = event.payload.get("ticker")
        if not self.ticker_allowed(ticker):
            return

        self._candle_count += 1
        threshold = int(self.config.get("candles_before_signal", 3))
        if self._candle_count < threshold or self._candle_count % threshold != 0:
            return

        params = self.config.get("parameters") or {}
        await self.emit_signal(
            {
                "action": "BUY",
                "class_code": event.payload.get("class_code", "TQBR"),
                "ticker": ticker or "SBER",
                "quantity": float(self.config.get("quantity", 10)),
                "order_type": "limit",
                "price": event.payload.get("close"),
                "reason": "test_threshold",
                "params_snapshot": params,
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
                "order_type": "limit",
                "price": None,
                "reason": "repl_manual",
                "params_snapshot": self.config.get("parameters") or {},
            }
        )
        self._signals_sent += 1
        return {"success": True, "ticker": ticker, "signals_sent": self._signals_sent}

    def describe(self) -> list[str]:
        return [
            "Event-driven test",
            "Subscribes to market.candle, publishes strategy.signal",
        ]
