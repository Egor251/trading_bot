"""Tests for market port normalize + mock adapter."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from engine.market import (  # noqa: E402
    Candle,
    candle_to_market_payload,
    timeframe_to_interval_min,
)
from engine.adapters import MockMarketDataAdapter  # noqa: E402
from host.events import normalize_market_candle  # noqa: E402


def test_timeframe_and_payload() -> None:
    assert timeframe_to_interval_min("5m") == 5
    assert timeframe_to_interval_min("1h") == 60
    c = Candle(
        symbol="SBER",
        timeframe="1m",
        ts=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        open=1,
        high=2,
        low=0.5,
        close=1.5,
        volume=10,
        class_code="TQBR",
        exchange="mock",
    )
    payload = candle_to_market_payload(c)
    assert payload["ticker"] == "SBER"
    assert payload["class_code"] == "TQBR"
    assert payload["close"] == 1.5
    norm = normalize_market_candle(payload)
    assert norm["close"] == 1.5


async def _mock_fetch() -> None:
    adapter = MockMarketDataAdapter(base_prices={"SBER": 100.0}, seed_bars=40)
    await adapter.connect()
    hist = await adapter.fetch_ohlcv("SBER", "1m", limit=40, class_code="TQBR")
    assert len(hist) >= 20
    assert hist[0].close != hist[-1].close or hist[0].high > hist[0].low
    # wicks / body exist
    assert any(b.high > b.low for b in hist)
    assert any(b.open != b.close for b in hist[-10:])
    one = await adapter.fetch_ohlcv("SBER", "1m", limit=1, class_code="TQBR")
    assert len(one) == 1
    assert one[0].close > 0
    await adapter.disconnect()


def test_mock_adapter() -> None:
    asyncio.run(_mock_fetch())


def test_ccxt_optional_import() -> None:
    from engine.adapters.ccxt_adapter import CcxtMarketDataAdapter, normalize_ccxt_symbol

    assert normalize_ccxt_symbol("CRYPTO:BTC/USDT") == "BTC/USDT"
    assert normalize_ccxt_symbol("BTCUSDT") == "BTC/USDT"
    assert normalize_ccxt_symbol("eth-usdt") == "ETH/USDT"

    a = CcxtMarketDataAdapter(exchange_id="binance")
    # may or may not be installed
    _ = a.available


def test_quik_transport_null() -> None:
    from engine.quik_transport import UnavailableQuikTransport

    t = UnavailableQuikTransport()

    async def _run() -> None:
        assert await t.is_connected() is False
        try:
            await t.connect()
            raise AssertionError("expected RuntimeError")
        except RuntimeError:
            pass

    asyncio.run(_run())


if __name__ == "__main__":
    test_timeframe_and_payload()
    test_mock_adapter()
    test_ccxt_optional_import()
    test_quik_transport_null()
    print("market adapters OK")
