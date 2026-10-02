"""Every fetch returned 600 bars, so research ran on ~5 hours of 1m data.

A single ``fetch_ohlcv`` returns far less than the ``limit`` it is given.
Measured against Bitget:

    limit= 500 -> 300 bars      limit=1000 -> 300 bars (1d)
    limit=2000 -> 300 bars      limit=5000 -> 300 bars (1d)
    1m/5m/15m/1h/4h/12h -> 1000 bars
    1w -> 100 bars

So every caller asking for more silently got a short window, and 600 bars of 1m
is five hours — far too little to measure any edge.

Two separate facts make backfill possible and both were got wrong first time:

* ``since`` is **ignored** for Bitget daily bars. The second page came back
  byte-identical to the first, so a ``since``-based walk terminates after two
  pages at 600 bars. Intraday ``since`` does work.
* A **short page is not exhaustion.** The venue caps below the size requested
  (300 daily bars against a request for 1000), so every page looks short and a
  loop that stops on a short page never walks anywhere.

The loop therefore pages with ``endTime``, notices a venue that ignores it and
falls back to ``since``, and stops only when a page yields no new timestamps.
"""

from __future__ import annotations

import asyncio

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.adapters.ccxt_adapter import CcxtMarketDataAdapter  # noqa: E402


class _FakeExchange:
    """Venue stub with Bitget's real quirks.

    * daily klines cap at 300 regardless of the requested ``limit``
    * ``endTime`` walks backwards
    * ``since`` is silently ignored for daily bars
    * intraday ``since`` works
    """

    #: What the venue will serve in one request, ignoring what was asked for.
    PAGE_CAP = {"1d": 300, "1w": 100}

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.total = 5_000
        self.step_ms = 3_600_000

    def _cap(self, timeframe: str, limit: int | None) -> int:
        return min(int(limit or 1000), self.PAGE_CAP.get(timeframe, 1000))

    async def fetch_ohlcv(self, market, *, timeframe, limit=None, since=None, params=None):
        step = self.step_ms
        newest = self.total * step
        count = self._cap(timeframe, limit)
        end = newest
        if params and params.get("endTime"):
            end = int(params["endTime"])
        elif timeframe == "1d":
            end = newest
        elif since is not None:
            end = int(since)
        self.calls.append({"timeframe": timeframe, "limit": limit, "params": params,
                           "since": since})
        if end >= newest:
            start = newest - step * count
        else:
            start = max(0, end - step * count)
        rows = [
            [start + i * step, 1.0, 2.0, 0.5, 1.5, 10.0] for i in range(count)
        ]
        return rows


def _adapter(exchange: _FakeExchange) -> CcxtMarketDataAdapter:
    adapter = CcxtMarketDataAdapter.__new__(CcxtMarketDataAdapter)
    adapter._exchange = exchange  # type: ignore[attr-defined]
    return adapter


def test_daily_history_walks_back_beyond_the_venue_cap() -> None:
    """The 600-bar ceiling came from `since`, which Bitget ignores."""

    async def scenario() -> list[list]:
        ex = _FakeExchange()
        adapter = _adapter(ex)
        return await adapter._fetch_ohlcv_paged("BTCUSDT", "1d", 1000)  # noqa: SLF001

    rows = asyncio.run(scenario())
    assert len(rows) == 1000, f"expected 1000 daily bars, got {len(rows)}"
    stamps = [int(r[0]) for r in rows]
    assert stamps == sorted(stamps), "rows must be chronological"
    assert len(set(stamps)) == len(stamps), "pages must not duplicate bars"


def test_a_short_page_is_not_treated_as_exhaustion() -> None:
    """300-bar pages against a 1000 request must not end the walk."""

    async def scenario() -> tuple[list[list], list[dict]]:
        ex = _FakeExchange()
        adapter = _adapter(ex)
        rows = await adapter._fetch_ohlcv_paged("BTCUSDT", "1d", 900)  # noqa: SLF001
        return rows, ex.calls

    rows, calls = asyncio.run(scenario())
    assert len(rows) == 900
    assert len(calls) >= 3, "300-bar pages cannot satisfy 900 bars in one request"
    assert any(c["params"] and "endTime" in c["params"] for c in calls), (
        "the walk must use endTime, which Bitget honours for daily bars"
    )


def test_a_venue_that_ignores_end_time_falls_back_to_since() -> None:
    """Intraday Bitget ignores endTime for some paths; `since` works there."""

    class _IgnoresEndTime(_FakeExchange):
        async def fetch_ohlcv(self, market, *, timeframe, limit=None, since=None, params=None):
            step = self.step_ms
            newest = self.total * step
            count = self._cap(timeframe, limit)
            self.calls.append({"timeframe": timeframe, "limit": limit,
                               "params": params, "since": since})
            if since is not None:
                # `since` works here.
                start = max(0, int(since) - step * count)
            else:
                # Ignores endTime entirely: always the same recent window.
                start = newest - step * count
            return [
                [start + i * step, 1.0, 2.0, 0.5, 1.5, 10.0]
                for i in range(count)
            ]

    async def scenario() -> tuple[list[list], list[dict]]:
        ex = _IgnoresEndTime()
        adapter = _adapter(ex)
        rows = await adapter._fetch_ohlcv_paged("BTCUSDT", "1h", 2000)  # noqa: SLF001
        return rows, ex.calls

    rows, calls = asyncio.run(scenario())
    assert len(rows) == 2000, "the walk must recover via `since`"
    assert any(c["since"] is not None for c in calls), "no `since` fallback was tried"


def test_the_loop_cannot_spin_on_a_venue_that_repeats_itself() -> None:
    """A venue replaying one window must terminate, not loop to MAX_PAGES."""

    class _AlwaysSame(_FakeExchange):
        async def fetch_ohlcv(self, market, *, timeframe, limit=None, since=None, params=None):
            self.calls.append({"timeframe": timeframe, "limit": limit,
                               "params": params, "since": since})
            step = self.step_ms
            newest = self.total * step
            return [
                [newest - step * 299 + i * step, 1.0, 2.0, 0.5, 1.5, 10.0]
                for i in range(300)
            ]

    async def scenario() -> tuple[list[list], int]:
        ex = _AlwaysSame()
        adapter = _adapter(ex)
        rows = await adapter._fetch_ohlcv_paged("BTCUSDT", "1d", 5000)  # noqa: SLF001
        return rows, len(ex.calls)

    rows, calls = asyncio.run(scenario())
    assert len(rows) == 300, "a stuck venue yields only what it gave"
    assert calls <= 3, f"expected an early exit, made {calls} requests"


def test_bar_gaps_expose_a_stitched_series() -> None:
    """Research on a silently gappy series describes a history that never existed."""

    rows = [[0, 1, 1, 1, 1, 1], [1000, 1, 1, 1, 1, 1], [5000, 1, 1, 1, 1, 1]]
    assert CcxtMarketDataAdapter.bar_gaps(rows) == [1000, 4000]
    assert CcxtMarketDataAdapter.bar_gaps([]) == []