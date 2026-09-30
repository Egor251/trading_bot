"""Provider routing for historical fetch + phantom cost accounting."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from engine.candle_store import CandleStore, StoredBar  # noqa: E402
from engine.market import Candle  # noqa: E402
from engine.runtime import TradingRuntime  # noqa: E402
from plugins.market.historical_data_service import (  # noqa: E402
    HistoricalDataService,
    _infer_provider,
)
from services.phantom_service import PhantomService  # noqa: E402


def test_infer_provider_matches_chart_feeds() -> None:
    assert _infer_provider("BTC/USDT", "CRYPTO") == "ccxt"
    assert _infer_provider("ETHUSDT", "SPOT") == "ccxt"
    assert _infer_provider("BTCUSDT", "") == "ccxt"
    assert _infer_provider("AAPL", "US") == "alpaca"
    assert _infer_provider("SBER", "TQBR") == ""
    assert _infer_provider("MSFT", "NASDAQ") == "alpaca"


def test_resolve_provider_auto() -> None:
    core = MagicMock()
    svc = HistoricalDataService(core, "historical_data_service")
    svc.config = svc.default_config()
    assert svc._resolve_provider(None, ticker="BTC/USDT", class_code="CRYPTO") == "ccxt"
    assert svc._resolve_provider("auto", ticker="AAPL", class_code="US") == "alpaca"
    assert svc._resolve_provider(None, ticker="SBER", class_code="TQBR") == "yahoo"
    assert svc._resolve_provider("runtime", ticker="SBER", class_code="TQBR") == "runtime"
    assert svc._resolve_provider("moex", ticker="SBER", class_code="TQBR") == "moex"


async def _runtime_provider_fetch() -> None:
    core = MagicMock()
    store = CandleStore(maxlen=50)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [
        StoredBar(
            symbol="BTC/USDT",
            class_code="CRYPTO",
            ts=base + timedelta(minutes=i),
            open=100 + i,
            high=101 + i,
            low=99 + i,
            close=100.5 + i,
            volume=10,
        )
        for i in range(12)
    ]
    store.load_series("CRYPTO:BTC/USDT", bars)
    trading = MagicMock()
    trading.candles = store
    core.trading = trading
    core.service_registry.get = MagicMock(return_value=None)

    svc = HistoricalDataService(core, "historical_data_service")
    svc.config = svc.default_config()
    out = await svc.fetch(
        "BTC/USDT",
        class_code="CRYPTO",
        provider="runtime",
        limit=10,
        use_cache=False,
    )
    assert len(out) == 10
    assert out[-1].close == bars[-1].close
    assert all(isinstance(c, Candle) for c in out)


def test_runtime_provider_reads_chart_buffer() -> None:
    asyncio.run(_runtime_provider_fetch())


async def _runtime_resamples_to_1d() -> None:
    from engine.ohlc_timeframe import median_bar_interval_sec

    core = MagicMock()
    store = CandleStore(maxlen=5000)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    # Three full UTC days of 1m bars.
    n = 3 * 24 * 60
    bars = [
        StoredBar(
            symbol="BTCUSDT",
            class_code="CRYPTO",
            ts=base + timedelta(minutes=i),
            open=100 + (i % 10) * 0.01,
            high=101,
            low=99,
            close=100.5,
            volume=1,
        )
        for i in range(n)
    ]
    store.load_series("CRYPTO:BTCUSDT", bars)
    trading = MagicMock()
    trading.candles = store
    core.trading = trading
    core.service_registry.get = MagicMock(return_value=None)

    svc = HistoricalDataService(core, "historical_data_service")
    svc.config = svc.default_config()
    out = await svc.fetch(
        "BTCUSDT",
        class_code="CRYPTO",
        provider="runtime",
        timeframe="1d",
        limit=10,
        use_cache=False,
    )
    assert len(out) >= 2
    assert all(c.timeframe == "1d" for c in out)
    gap = median_bar_interval_sec(out)
    assert gap is not None and abs(gap - 86_400) / 86_400 <= 0.35
    last = svc._last_fetch  # noqa: SLF001
    assert last.get("bar_interval_sec") is not None
    assert last.get("timeframe") == "1d"


def test_runtime_1m_buffer_resamples_to_requested_1d() -> None:
    asyncio.run(_runtime_resamples_to_1d())


async def _phantom_applies_legacy_fees() -> None:
    core = MagicMock()
    core.config.get = AsyncMock(
        side_effect=lambda key, default=None: {
            "trading": {
                "equity": 10_000,
                "legacy_fee_bps": 0,
                "legacy_slippage_bps": 0,
                "risk": {"max_position_pct": 1.0, "max_leverage": 2.0},
            },
            "instance.id": "local",
            "costs": {},
        }.get(key, default)
    )
    core.event_bus = MagicMock()
    core.event_bus.publish = AsyncMock()
    core.event_bus.subscribe = AsyncMock()
    core.event_bus.unsubscribe = AsyncMock()
    core.service_registry.get = MagicMock(return_value=None)

    svc = PhantomService(core, "phantom_service")
    svc.config = svc.default_config()
    svc.config["fee_bps"] = 10.0
    svc.config["slippage_bps"] = 0.0
    svc.config["start_equity"] = 10_000.0
    await svc.on_initialize()
    assert svc.runtime is not None
    assert svc.runtime.orders.cost_schedule is not None

    fill = await svc._shadow_fill(
        {
            "action": "BUY",
            "ticker": "TEST",
            "class_code": "US",
            "quantity": 10,
            "price": 100,
            "strategy_id": "t",
        }
    )
    assert float(fill.get("cost") or 0) > 0
    assert len(svc._fills) == 1
    assert float(svc.runtime.orders.total_costs) > 0


def test_phantom_applies_configured_fees() -> None:
    asyncio.run(_phantom_applies_legacy_fees())


def test_phantom_offline_costs_reduce_pnl() -> None:
    core = MagicMock()
    svc = PhantomService(core, "phantom_service")
    svc.config = svc.default_config()
    svc.config["fee_bps"] = 50.0
    svc.config["slippage_bps"] = 0.0
    svc._fee_bps = 50.0
    svc._slippage_bps = 0.0
    # Rising then falling closes → at least one flip with costs
    closes = [100 + i for i in range(20)] + [120 - i for i in range(20)]
    with_cost = svc.offline_ma_cross_metrics(closes, fast=3, slow=8, quantity=10)
    svc._fee_bps = 0.0
    no_cost = svc.offline_ma_cross_metrics(closes, fast=3, slow=8, quantity=10)
    assert with_cost["trades"] == no_cost["trades"]
    assert with_cost["pnl"] < no_cost["pnl"]


def test_paper_and_phantom_cost_parity_with_schedule() -> None:
    cfg = {
        "equity": 10_000,
        "legacy_fee_bps": 5,
        "legacy_slippage_bps": 2,
        "risk": {"max_position_pct": 1.0, "max_leverage": 2.0},
    }
    paper = TradingRuntime.from_config({**cfg, "mode": "paper"})
    phantom = TradingRuntime.from_config({**cfg, "mode": "phantom"})
    for rt in (paper, phantom):
        rt.submit_signal(ticker="TEST", action="BUY", quantity=10, mark_price=100)
        rt.submit_signal(ticker="TEST", action="SELL", quantity=10, mark_price=110)
    assert paper.snapshot()["total_costs"] == phantom.snapshot()["total_costs"]
    assert paper.snapshot()["equity"] == phantom.snapshot()["equity"]


if __name__ == "__main__":
    test_infer_provider_matches_chart_feeds()
    test_resolve_provider_auto()
    test_runtime_provider_reads_chart_buffer()
    test_phantom_applies_configured_fees()
    test_phantom_offline_costs_reduce_pnl()
    test_paper_and_phantom_cost_parity_with_schedule()
    print("phantom providers/costs OK")
