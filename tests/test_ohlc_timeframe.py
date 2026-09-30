"""OHLC timeframe verify + resample (1m must not be silently labeled 1d)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.market import Candle  # noqa: E402
from engine.ohlc_timeframe import (  # noqa: E402
    TimeframeMismatchError,
    ensure_timeframe,
    median_bar_interval_sec,
    normalize_timeframe,
    resample_candles,
    timeframe_to_seconds,
)
from plugins.market.historical_data_service import _infer_provider  # noqa: E402


def _minute_bars(n: int = 3 * 24 * 60) -> list[Candle]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    out: list[Candle] = []
    for i in range(n):
        px = 100.0 + (i % 60) * 0.01
        out.append(
            Candle(
                symbol="BTCUSDT",
                timeframe="1m",
                ts=base + timedelta(minutes=i),
                open=px,
                high=px + 0.1,
                low=px - 0.1,
                close=px + 0.05,
                volume=1.0,
                exchange="runtime",
                class_code="CRYPTO",
            )
        )
    return out


def test_normalize_and_seconds() -> None:
    assert normalize_timeframe("daily") == "1d"
    assert timeframe_to_seconds("1d") == 86_400
    assert timeframe_to_seconds("1m") == 60


def test_infer_btcusdt_without_slash() -> None:
    assert _infer_provider("BTCUSDT", "") == "ccxt"
    assert _infer_provider("btc-usdt", "") == "ccxt"
    assert _infer_provider("SBER", "TQBR") == ""


def test_resample_1m_to_1d() -> None:
    mins = _minute_bars()
    daily = resample_candles(mins, "1d")
    assert len(daily) >= 2
    gap = median_bar_interval_sec(daily)
    assert gap is not None
    assert abs(gap - 86_400) / 86_400 <= 0.35
    assert all(c.timeframe == "1d" for c in daily)


def test_ensure_rejects_wrong_coarser_without_resample() -> None:
    mins = _minute_bars(120)
    try:
        ensure_timeframe(mins, "1d", provider="runtime", allow_resample=False)
        raise AssertionError("expected TimeframeMismatchError")
    except TimeframeMismatchError as exc:
        assert exc.requested == "1d"
        assert exc.observed_sec < 120


def test_ensure_resamples_runtime_1m_as_1d() -> None:
    mins = _minute_bars()
    out = ensure_timeframe(mins, "1d", provider="runtime", allow_resample=True)
    assert len(out) >= 2
    assert all(c.timeframe == "1d" for c in out)
    gap = median_bar_interval_sec(out)
    assert gap is not None and abs(gap - 86_400) / 86_400 <= 0.35


if __name__ == "__main__":
    test_normalize_and_seconds()
    test_infer_btcusdt_without_slash()
    test_resample_1m_to_1d()
    test_ensure_rejects_wrong_coarser_without_resample()
    test_ensure_resamples_runtime_1m_as_1d()
    print("ohlc_timeframe OK")
