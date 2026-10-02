"""Measure how much history is actually reachable, per symbol and timeframe.

The complaint is that candle history is small and not fully loaded, which would
make phantom research meaningless. This walks the cache and then asks the live
fetcher what it can get, so the two are compared rather than assumed equal.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(r"C:\Users\Egor\PycharmProjects\trading_bot")
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()


def cache_summary() -> None:
    cache = ROOT / "data" / "ohlc_cache"
    by_key: dict[tuple[str, str], tuple[int, str, str]] = {}
    for path in sorted(cache.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = doc.get("candles") or []
        if not rows:
            continue
        sym = str(rows[0].get("symbol") or "")
        tf = str(rows[0].get("timeframe") or "")
        first = str(rows[0].get("ts") or "")[:10]
        last = str(rows[-1].get("ts") or "")[:10]
        key = (sym, tf)
        prev = by_key.get(key)
        if prev is None or len(rows) > prev[0]:
            by_key[key] = (len(rows), first, last)
    print("=== cached history (best file per symbol/timeframe) ===")
    for (sym, tf), (n, first, last) in sorted(by_key.items()):
        span = ""
        try:
            d0 = datetime.fromisoformat(first)
            d1 = datetime.fromisoformat(last)
            span = f"{(d1 - d0).days}d"
        except Exception:
            pass
        print(f"  {sym:12} {tf:4} bars={n:5}  {first} .. {last}  ({span})")


async def live_depth() -> None:
    from host.trading_bot_core import TradingBotCore
    from tests.e2e_sandbox import _seed_sandbox_rules, build_sandbox_config

    with tempfile.TemporaryDirectory() as raw:
        work = Path(raw)
        (work / "config").mkdir(parents=True, exist_ok=True)
        (work / "data").mkdir(parents=True, exist_ok=True)
        # Diagnostics must never fall back to the production data root.
        os.environ["TRADING_DATA_DIR"] = str(work / "data")
        _seed_sandbox_rules(work / "data")
        cfg = build_sandbox_config(
            work / "config" / "trading_bot.yaml",
            data_root=work / "data",
            enable_plugins=("ma_cross_example",),
            extra_overrides={
                "services.historical_data_service.enabled": True,
                "services.historical_data_service.auto_start": True,
                # Force a network fetch every time so we measure the venue, not
                # a cache that happens to be deep.
                "services.historical_data_service.disk_cache_ttl_sec": 1,
                "services.phantom_service.enabled": False,
                "services.phantom_service.auto_start": False,
                "services.ops_monitor_service.enabled": False,
                "services.ops_monitor_service.auto_start": False,
                "storage.enabled": False,
                "services.storage_service.enabled": False,
                "services.storage_service.auto_start": False,
            },
        )
        core = TradingBotCore(cfg)
        await core.boot()
        hist = core.service_registry.get("historical_data_service")
        if hist is None:
            print("historical_data_service missing")
            await core.shutdown()
            return

        print()
        print("=== what the venue will actually serve (cache disabled) ===")
        print(f"{'symbol':12} {'tf':4} {'asked':>6} {'got':>6}  range")
        for sym in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
            for tf, ask in (("1m", 1000), ("5m", 1000), ("15m", 1000), ("1h", 1000), ("4h", 1000), ("1d", 1000)):
                try:
                    bars = await hist.fetch(
                        sym, class_code="CRYPTO", provider="ccxt", timeframe=tf, limit=ask
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"  {sym:12} {tf:4} {ask:6} ERROR {type(exc).__name__}: {str(exc)[:50]}")
                    continue
                if not bars:
                    print(f"  {sym:12} {tf:4} {ask:6}     0")
                    continue
                print(
                    f"  {sym:12} {tf:4} {ask:6} {len(bars):6}  "
                    f"{str(bars[0].ts)[:16]} .. {str(bars[-1].ts)[:16]}"
                )
        await core.shutdown()


if __name__ == "__main__":
    cache_summary()
    asyncio.run(live_depth())