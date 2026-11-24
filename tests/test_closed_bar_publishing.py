"""Forming bars must not reach strategy evaluation.

The CCXT watch stream emits every partial update of the current 1m bar.
`CandleStore._put` replaces the last bar in the same bucket, so strategies
re-evaluated on an incomplete close and could flip twice inside one bar,
paying two fills. A bar is publishable only when the NEXT bucket opens.
"""

from __future__ import annotations

from datetime import UTC, datetime

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.market import Candle, closed_bar_arrived  # noqa: E402


def _bar(minute: int, second: int = 0, close: float = 100.0) -> Candle:
    return Candle(
        symbol="BTC/USDT",
        timeframe="1m",
        ts=datetime(2026, 10, 13, 12, minute, second, tzinfo=UTC),
        open=100.0,
        high=101.0,
        low=99.0,
        close=close,
        volume=1.0,
    )


def test_first_bar_is_held_until_the_next_one_opens():
    assert closed_bar_arrived(None, _bar(0, 0)) is False


def test_updates_inside_the_same_minute_are_forming_bars():
    first = _bar(0, 0, 100.0)
    for second, close in ((10, 100.5), (30, 99.8), (59, 100.2)):
        assert closed_bar_arrived(first, _bar(0, second, close)) is False


def test_next_minute_closes_the_previous_bar():
    assert closed_bar_arrived(_bar(0, 59), _bar(1, 0)) is True


def test_out_of_order_or_stale_feed_does_not_republish():
    assert closed_bar_arrived(_bar(1, 0), _bar(0, 59)) is False
    assert closed_bar_arrived(_bar(1, 0), _bar(1, 10)) is False
