"""Probe exactly what the venue will serve, and whether `since` is honoured.

Everything in this repository caps at 600 bars per fetch regardless of the
requested limit. Before choosing a fix I need to know whether that is a local
loop bug or the venue refusing to page, because the remedies differ completely.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

sys.path.insert(0, r"C:\Users\Egor\PycharmProjects\trading_bot")
from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.adapters.ccxt_adapter import normalize_ccxt_symbol


async def main() -> None:
    import ccxt.async_support as ccxt

    exchange = ccxt.bitget({"enableRateLimit": True})
    market = normalize_ccxt_symbol("BTC/USDT", class_code="CRYPTO")
    print(f"exchange=bitget market={market}\n")

    print("--- raw fetch_ohlcv with growing limit (1d) ---")
    for limit in (100, 300, 500, 1000, 2000):
        rows = await exchange.fetch_ohlcv(market, timeframe="1d", limit=limit)
        span = ""
        if rows:
            span = (
                f"{datetime.fromtimestamp(rows[0][0] / 1000, tz=timezone.utc):%Y-%m-%d}"
                f" .. {datetime.fromtimestamp(rows[-1][0] / 1000, tz=timezone.utc):%Y-%m-%d}"
            )
        print(f"  limit={limit:5} -> {len(rows):5} bars  {span}")

    print("\n--- does `since` page backwards? (1d, 300 per page) ---")
    first = await exchange.fetch_ohlcv(market, timeframe="1d", limit=300)
    oldest = min(int(r[0]) for r in first)
    print(f"  page1 {len(first)} bars, oldest={datetime.fromtimestamp(oldest/1000, tz=timezone.utc):%Y-%m-%d}")
    for attempt in range(1, 5):
        since = oldest - 1
        page = await exchange.fetch_ohlcv(
            market, timeframe="1d", limit=300, since=since
        )
        if not page:
            print(f"  page{attempt + 1} with since={datetime.fromtimestamp(since/1000, tz=timezone.utc):%Y-%m-%d} -> 0 bars")
            break
        got_oldest = min(int(r[0]) for r in page)
        got_newest = max(int(r[0]) for r in page)
        moved = got_oldest < oldest
        print(
            f"  page{attempt + 1} {len(page):4} bars  "
            f"{datetime.fromtimestamp(got_oldest/1000, tz=timezone.utc):%Y-%m-%d}"
            f" .. {datetime.fromtimestamp(got_newest/1000, tz=timezone.utc):%Y-%m-%d}"
            f"  moved_backwards={moved}"
        )
        if not moved:
            print("    -> venue ignored `since`; paging is impossible")
            break
        oldest = got_oldest

    print("\n--- can we reach far back in one shot? (1d, limit 5000) ---")
    try:
        rows = await exchange.fetch_ohlcv(market, timeframe="1d", limit=5000)
        print(f"  got {len(rows)} bars")
    except Exception as exc:  # noqa: BLE001
        print(f"  error {type(exc).__name__}: {str(exc)[:120]}")

    print("\n--- 1m depth ---")
    rows = await exchange.fetch_ohlcv(market, timeframe="1m", limit=1000)
    print(f"  got {len(rows)} bars")
    if rows:
        print(
            f"  {datetime.fromtimestamp(rows[0][0]/1000, tz=timezone.utc):%Y-%m-%d %H:%M}"
            f" .. {datetime.fromtimestamp(rows[-1][0]/1000, tz=timezone.utc):%Y-%m-%d %H:%M}"
        )

    await exchange.close()


if __name__ == "__main__":
    asyncio.run(main())