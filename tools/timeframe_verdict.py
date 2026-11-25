"""R-43: does a 1m research run find an edge that the 1d run missed?

Registered S17 before running. Same strategies, same default configs, same
`decisions_to_fills` and cost model; only the bar timeframe changes. The daily
control is a separate 1d series, not an aggregation of the 1m window.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from engine.indicators import equity_pnl  # noqa: E402
from engine.research import decisions_to_fills  # noqa: E402
from engine.strategy_plugin import StrategyPlugin  # noqa: E402
from tools.gate_status import LINEUP  # noqa: E402

N_BARS = 3000
TRAIN, VAL, TEST = 1800, 600, 600
FEE_BPS = 7.0


async def _klines(timeframe: str, n: int, *, symbol: str = "BTCUSDT") -> list[list]:
    import ccxt.async_support as ccxt

    ex = ccxt.binance({"enableRateLimit": True})
    try:
        by_ts: dict[int, list] = {}
        cursor: int | None = None
        while len(by_ts) < n:
            params = {
                "symbol": symbol,
                "interval": timeframe,
                "limit": 1000,
            }
            if cursor is not None:
                params["endTime"] = cursor
            page = await ex.public_get_klines(params)
            if not page:
                break
            fresh = [r for r in page if int(r[0]) not in by_ts]
            for row in page:
                by_ts[int(row[0])] = row
            if not fresh:
                break
            cursor = min(int(r[0]) for r in page) - 1
        rows = [by_ts[k] for k in sorted(by_ts)]
        return rows[-n:]
    finally:
        await ex.close()


def _series(rows: list[list]) -> dict[str, list[float]]:
    return {
        "open": [float(r[1]) for r in rows],
        "high": [float(r[2]) for r in rows],
        "low": [float(r[3]) for r in rows],
        "close": [float(r[4]) for r in rows],
        "volume": [float(r[5]) for r in rows],
    }


def _plugin(name: str):
    mod = importlib.import_module(f"strategies.{name}.plugin")
    for obj in vars(mod).values():
        if (
            isinstance(obj, type)
            and obj is not StrategyPlugin
            and issubclass(obj, StrategyPlugin)
            and obj.__module__ == mod.__name__
        ):
            return obj
    return None


def _net(name: str, series: dict[str, list[float]]) -> tuple[float, int]:
    cls = _plugin(name)
    if cls is None:
        return 0.0, -1
    plugin = cls.__new__(cls)  # evaluate_at is stateless under default params
    cfg = plugin.default_params()
    closes = series["close"]
    decisions = [plugin.evaluate_at(series, i, config=cfg) for i in range(len(closes))]
    fills = decisions_to_fills(
        decisions,
        closes,
        fee_bps=FEE_BPS,
        score_from=TRAIN,
        ticker="BTCUSDT",
        position_mode="long_flat",
    )
    out = equity_pnl(fills, start_equity=10_000.0, mark_price=closes[-1])
    return float(out["pnl"]), len(fills)


async def main() -> int:
    minute = await _klines("1m", N_BARS)
    daily = await _klines("1d", N_BARS)
    print(f"fetched {len(minute)} 1m and {len(daily)} 1d klines")
    if len(minute) < TRAIN + VAL + TEST or len(daily) < TRAIN + VAL + TEST:
        print("NOT EVALUABLE: fewer bars than the registered window")
        return 1
    s1, sd = _series(minute), _series(daily)

    print(f"{'strategy':26} {'1m net':>10} {'1d net':>10} {'1m fills':>9}")
    print("-" * 60)
    positive: list[str] = []
    for name in LINEUP:
        n1, f1 = _net(name, s1)
        nd, _ = _net(name, sd)
        if n1 > 0:
            positive.append(name)
        print(f"{name:26} {n1:>+10.2f} {nd:>+10.2f} {f1:>9d}")
    print()
    print(f"strategies with positive 1m net: {len(positive)} {positive}")
    print(
        f"R-44 {'FAILS' if positive else 'HOLDS'}: "
        f"{'a timeframe change produced an edge' if positive else 'no strategy clears on 1m'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
