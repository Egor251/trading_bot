"""Unit tests for candle store and event helpers."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import CandleStore  # noqa: E402
from host.events import (  # noqa: E402
    EventPayloadError,
    normalize_market_candle,
    normalize_strategy_signal,
    normalize_universe_changed,
)


def test_candle_store_mark() -> None:
    store = CandleStore(maxlen=3)
    store.ingest_payload(
        {
            "class_code": "TQBR",
            "ticker": "SBER",
            "date": "2026-07-26",
            "time": "12:00:00",
            "open": 1,
            "high": 2,
            "low": 0.5,
            "close": 1.5,
            "volume": 10,
        }
    )
    assert store.mark_price("TQBR:SBER") == 1.5
    assert store.mark_price("SBER") == 1.5
    # same minute → upsert, not grow
    store.ingest_payload(
        {
            "class_code": "TQBR",
            "ticker": "SBER",
            "date": "2026-07-26",
            "time": "12:00:30",
            "open": 1,
            "high": 2.2,
            "low": 0.5,
            "close": 1.8,
            "volume": 12,
        }
    )
    assert len(store.bars("TQBR:SBER")) == 1
    assert store.mark_price("TQBR:SBER") == 1.8


def test_candle_store_load_series() -> None:
    from datetime import UTC, datetime

    from engine.candle_store import StoredBar

    store = CandleStore(maxlen=100)
    bars = [
        StoredBar(
            symbol="BTC/USDT",
            class_code="CRYPTO",
            ts=datetime(2026, 7, 26, 12, i, tzinfo=UTC),
            open=1.0,
            high=2.0,
            low=0.5,
            close=float(i),
            volume=10.0,
        )
        for i in range(50)
    ]
    n = store.load_series("CRYPTO:BTC/USDT", bars)
    assert n == 50
    assert len(store.closes("CRYPTO:BTC/USDT")) == 50
    assert store.closes("CRYPTO:BTC/USDT")[-1] == 49.0
    snap = store.snapshot()
    assert "closes" not in snap
    assert snap["bar_counts"]["CRYPTO:BTC/USDT"] == 50
    assert len(snap["ohlc"]["CRYPTO:BTC/USDT"]["closes"]) == 50
    assert snap["last_closes"]["CRYPTO:BTC/USDT"] == 49.0
    dates = snap["ohlc"]["CRYPTO:BTC/USDT"]["dates"]
    assert len(dates) == 50
    # Real unix timestamps, not bar indices 0..n
    assert dates[-1] > 1_000_000_000
    assert dates[-1] > dates[0]
    capped = store.snapshot(ohlc_limit=10)
    assert len(capped["ohlc"]["CRYPTO:BTC/USDT"]["closes"]) == 10
    assert capped["ohlc"]["CRYPTO:BTC/USDT"]["closes"][-1] == 49.0
    # Unchanged store → cached snapshot object
    assert store.snapshot(ohlc_limit=10) is capped


def test_normalize_payloads() -> None:
    c = normalize_market_candle({"ticker": "SBER", "close": "10"})
    assert c["close"] == 10.0
    assert c.get("schema_version") == 1
    s = normalize_strategy_signal({"action": "buy", "ticker": "SBER", "quantity": "2"})
    assert s["action"] == "BUY" and s["quantity"] == 2.0
    u = normalize_universe_changed({"added": ["TQBR:X"], "active_keys": ["TQBR:X"]})
    assert u["added"] == ["TQBR:X"]
    try:
        normalize_market_candle({"close": 1})
        raise AssertionError("expected error")
    except EventPayloadError:
        pass


if __name__ == "__main__":
    test_candle_store_mark()
    test_normalize_payloads()
    print("runtime helpers OK")
