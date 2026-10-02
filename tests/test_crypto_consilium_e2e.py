"""Deterministic CCXT candle → Consilium → crypto paper fill E2E."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.adapters.ccxt_adapter import CcxtMarketDataAdapter  # noqa: E402
from engine.market import candle_to_market_payload  # noqa: E402
from engine.runtime import TradingRuntime  # noqa: E402
from microkernel_core import KernelEvent  # noqa: E402
from services.ensemble_service import EnsembleService  # noqa: E402


class _FakeCcxtExchange:
    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int):
        assert symbol == "BTC/USDT"
        assert timeframe == "1m"
        return [[1_700_000_000_000, 100, 102, 99, 101, 42]][-limit:]


async def _run() -> None:
    adapter = CcxtMarketDataAdapter(exchange_id="binance")
    adapter._exchange = _FakeCcxtExchange()  # noqa: SLF001
    bars = await adapter.fetch_ohlcv(
        "CRYPTO:BTCUSDT", "1m", limit=1, class_code="CRYPTO"
    )
    assert len(bars) == 1

    runtime = TradingRuntime.from_config(
        {
            "mode": "paper",
            "equity": 10_000,
            "risk": {"max_position_pct": 0.20, "max_leverage": 1.0},
        }
    )
    runtime.candles.ingest_payload(candle_to_market_payload(bars[0]))

    core = MagicMock()
    core.trading = runtime
    core.config.get = AsyncMock(
        side_effect=lambda key, default=None: (
            {
                "ensemble": {
                    "enabled": True,
                    "strategies": ["trend", "momentum"],
                    "weight_mode": "equal",
                },
                # This test exercises Consilium aggregation, not research
                # validation; "trend" and "momentum" have no clearance on file,
                # so the gate would demote them and no decision could be formed.
                # Gate behaviour itself is covered in
                # tests/test_research_gate_enforced.py.
                "live_readiness": {"enforce": False},
            }
            if key == "trading"
            else "test"
            if key == "instance.id"
            else default
        )
    )
    core.plugin_manager._instances = {}
    core.rule_engine = None
    core.event_bus = MagicMock()
    published: list[KernelEvent] = []

    async def _publish(event: KernelEvent) -> None:
        published.append(event)

    core.event_bus.publish = _publish
    core.event_bus.subscribe = AsyncMock()
    core.event_bus.unsubscribe = AsyncMock()

    consilium = EnsembleService(core, "ensemble_service")
    consilium.config = {
        **consilium.default_config(),
        "route_execution": True,
        "debounce_sec": 0,
    }
    await consilium.on_initialize()
    await consilium.start()

    for strategy_id, strength in (("trend", 1.0), ("momentum", 0.8)):
        await consilium._on_signal(  # noqa: SLF001
            KernelEvent(
                event_type="strategy.signal",
                source_id=strategy_id,
                payload={
                    "strategy_id": strategy_id,
                    "action": "BUY",
                    "ticker": "BTC/USDT",
                    "class_code": "CRYPTO",
                    "quantity": 1,
                    "price": bars[0].close,
                    "strength": strength,
                },
            )
        )
    await consilium._flush()  # noqa: SLF001

    assert any(event.event_type == "ensemble.decision" for event in published)
    executions = [
        event for event in published if event.event_type == "execution.result"
    ]
    assert executions and executions[-1].payload["success"] is True
    position = runtime.positions if hasattr(runtime, "positions") else runtime.orders.positions
    assert position["CRYPTO:BTC/USDT"].quantity == 1
    assert runtime.orders.exposures["CRYPTO:BTC/USDT"] == 101
    await consilium.stop()


def test_crypto_consilium_paper_e2e() -> None:
    asyncio.run(_run())

