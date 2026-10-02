"""Can we reach further back at all?

Bitget's ccxt klines call caps at 300 bars for daily bars and ignores `since`.
The underlying REST endpoint takes `endTime`, which is the standard way to walk
backwards. This probes every route ccxt exposes, plus the raw HTTP endpoint, so
the answer is "recoverable" or "genuinely capped" on evidence.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

sys.path.insert(0, r"C:\Users\Egor\PycharmProjects\trading_bot")
from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.adapters.ccxt_adapter import normalize_ccxt_symbol


def ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


async def main() -> None:
    import ccxt.async_support as ccxt

    exchange = ccxt.bitget({"enableRateLimit": True})
    market = normalize_ccxt_symbol("BTC/USDT", class_code="CRYPTO")

    base = await exchange.fetch_ohlcv(market, timeframe="1d", limit=300)
    oldest = min(int(r[0]) for r in base)
    print(f"baseline 1d: {len(base)} bars, oldest={ts(oldest)}\n")

    print("--- params={'endTime': ...} ---")
    end = oldest - 1
    try:
        rows = await exchange.fetch_ohlcv(
            market, timeframe="1d", limit=300, params={"endTime": end}
        )
        if rows:
            print(
                f"  {len(rows)} bars  {ts(min(int(r[0]) for r in rows))}"
                f" .. {ts(max(int(r[0]) for r in rows))}"
            )
        else:
            print("  0 bars")
    except Exception as exc:  # noqa: BLE001
        print(f"  error {type(exc).__name__}: {str(exc)[:140]}")

    print("\n--- params={'until': ...} ---")
    try:
        rows = await exchange.fetch_ohlcv(
            market, timeframe="1d", limit=300, params={"until": oldest - 1}
        )
        if rows:
            print(
                f"  {len(rows)} bars  {ts(min(int(r[0]) for r in rows))}"
                f" .. {ts(max(int(r[0]) for r in rows))}"
            )
        else:
            print("  0 bars")
    except Exception as exc:  # noqa: BLE001
        print(f"  error {type(exc).__name__}: {str(exc)[:140]}")

    print("\n--- does `since` work on 1m? ---")
    m = await exchange.fetch_ohlcv(market, timeframe="1m", limit=1000)
    if m:
        o = min(int(r[0]) for r in m)
        print(f"  baseline 1m oldest={ts(o)}")
        rows = await exchange.fetch_ohlcv(
            market, timeframe="1m", limit=1000, since=o - 60_000
        )
        if rows:
            ro = min(int(r[0]) for r in rows)
            print(f"  since -> oldest={ts(ro)} moved_backwards={ro < o}")
        else:
            print("  0 bars")

    print("\n--- per-timeframe cap with limit=1000 ---")
    for tf in ("1m", "3m", "5m", "15m", "30m", "1h", "4h", "6h", "12h", "1d", "1w"):
        try:
            rows = await exchange.fetch_ohlcv(market, timeframe=tf, limit=1000)
            print(f"  {tf:4} -> {len(rows):5} bars  {ts(int(rows[0][0]))} .. {ts(int(rows[-1][0]))}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {tf:4} -> error {type(exc).__name__}: {str(exc)[:60]}")

    await exchange.close()


if __name__ == "__main__":
    asyncio.run(main())