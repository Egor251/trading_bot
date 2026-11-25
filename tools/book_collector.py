"""Forward order-book collector (S35).

There is no historical depth series in this repo. This tool can only start
collecting top-of-book snapshots from now on; it never fabricates past depth.
Writes a bounded, idempotent JSONL to research/order_book.jsonl.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.paths import data_path  # noqa: E402

SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
DEFAULT_MAX_LINES = 20_000


def snapshot_row(
    exchange: str, symbol: str, book: Mapping[str, Any], *, ts: float | None = None
) -> dict | None:
    """Normalize one CCXT order book; None if either side is missing."""
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        return None
    try:
        bid, bid_size = float(bids[0][0]), float(bids[0][1])
        ask, ask_size = float(asks[0][0]), float(asks[0][1])
    except (TypeError, ValueError, IndexError):
        return None
    if min(bid, bid_size, ask, ask_size) <= 0 or bid >= ask:
        return None
    return {
        "ts": float(ts if ts is not None else time.time()),
        "exchange": str(exchange),
        "symbol": str(symbol),
        "bid": bid, "bid_size": bid_size,
        "ask": ask, "ask_size": ask_size,
        "spread_bps": (ask - bid) / ((ask + bid) / 2.0) * 10_000.0,
    }


@dataclass(slots=True)
class BookCollector:
    path: Path
    max_lines: int = DEFAULT_MAX_LINES

    def append(self, rows: list[dict]) -> int:
        """Append unseen snapshots; return how many were actually written."""
        existing: set[tuple[str, str, float]] = set()
        kept: list[dict] = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                    existing.add((str(r.get("exchange")), str(r.get("symbol")), float(r.get("ts"))))
                    kept.append(r)
                except (ValueError, TypeError):
                    continue
        added = 0
        for r in rows:
            key = (str(r.get("exchange")), str(r.get("symbol")), float(r.get("ts")))
            if key in existing:
                continue
            existing.add(key)
            kept.append(r)
            added += 1
        kept = kept[-max(1, self.max_lines):]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            "\n".join(json.dumps(r, sort_keys=True) for r in kept) + "\n",
            encoding="utf-8",
        )
        return added


async def collect_once(exchange_id: str = "binance", symbols: tuple[str, ...] = SYMBOLS) -> list[dict]:
    import ccxt.async_support as ccxt

    ex = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    rows: list[dict] = []
    try:
        await ex.load_markets()
        for symbol in symbols:
            try:
                book = await ex.fetch_order_book(symbol, limit=5)
            except Exception:
                continue
            row = snapshot_row(exchange_id, symbol, book)
            if row is not None:
                rows.append(row)
    finally:
        await ex.close()
    return rows


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exchange", default="binance")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    collector = BookCollector(data_path("research", "order_book.jsonl"))
    rows = await collect_once(args.exchange)
    added = collector.append(rows)
    print(f"snapshots fetched={len(rows)} written={added} path={collector.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
