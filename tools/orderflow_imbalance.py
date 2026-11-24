"""Signed taker imbalance vs next-bar returns (registered raw-record S6-1).

The adapter path (`ccxt.fetch_ohlcv`) carries six columns; Binance taker-buy
volume (raw kline fields 9-10) never reaches it. This tool goes to the raw
klines endpoint directly — research only, not the product path.

Order: C0 field validation on live data (contemporaneous corr > 0.3, else the
field mapping is misread and nothing else runs), K1/K2 synthetic known answers,
then H on trailing-mean imbalance over L in {1, 4} vs next-bar log returns,
t = r*sqrt(n-2)/sqrt(1-r^2), bar |t| > 3.00.

Usage:
  python tools/orderflow_imbalance.py --selftest   # K1/K2, no network
  python tools/orderflow_imbalance.py              # live: C0 then H
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

SYMBOL = "BTCUSDT"
TIMEFRAME = "1h"
N_BARS = 3000
PAGE = 1000
LAGS = (1, 4)
TBAR = 3.00

#: Raw Binance kline field indices (spot klines, 12 fields).
F_OPEN_TIME = 0
F_OPEN = 1
F_HIGH = 2
F_LOW = 3
F_CLOSE = 4
F_BASE_VOL = 5
F_TAKER_BUY_BASE_VOL = 9


def imbalance(rows: list[list]) -> tuple[list[float], list[float]]:
    """Per-bar (signed imbalance, log return). Imbalance in [-1, 1]."""
    imb: list[float] = []
    rets: list[float] = []
    prev: float | None = None
    for r in rows:
        vol = float(r[F_BASE_VOL])
        tb = float(r[F_TAKER_BUY_BASE_VOL])
        assert vol > 0, "zero-volume bar in spot klines"
        imb.append(2.0 * tb / vol - 1.0)
        close = float(r[F_CLOSE])
        if prev is not None:
            rets.append(math.log(close / prev))
        prev = close
    return imb[1:], rets  # align: imb[t] pairs with ret[t]


def corr(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        raise ValueError("predictor or target has no variance")
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


def tstat(r: float, n: int) -> float:
    if abs(r) >= 1.0:
        return math.inf
    return r * math.sqrt(n - 2) / math.sqrt(1.0 - r * r)


def trailing_mean(xs: list[float], length: int) -> list[float]:
    return [sum(xs[i - length + 1 : i + 1]) / length for i in range(length - 1, len(xs))]


def _selftest() -> int:
    import random

    rng = random.Random(913)
    n = 3000
    imb = [rng.uniform(-1.0, 1.0) for _ in range(n)]
    # K1: injected slope recovered.
    ret = [0.02 * v + rng.gauss(0.0, 0.01) for v in imb]
    t1 = tstat(corr(imb, ret), n)
    print(f"K1 injected slope: t={t1:+.2f} (need > +3)")
    # K2: shuffled predictor is null.
    sh = list(imb)
    rng.shuffle(sh)
    t2 = tstat(corr(sh, ret), n)
    print(f"K2 shuffled predictor: t={t2:+.2f} (need |t| < 2)")
    # Spread gate: constant predictor refuses.
    try:
        corr([0.5] * n, ret)
        print("GATE FAIL: constant predictor scored")
        return 1
    except ValueError:
        print("GATE OK: constant predictor refused")
    ok = t1 > 3.0 and abs(t2) < 2.0
    print("SELFTEST PASS" if ok else "SELFTEST FAIL")
    return 0 if ok else 1


async def _fetch_klines(n_bars: int) -> list[list]:
    import ccxt.async_support as ccxt

    ex = ccxt.binance({"enableRateLimit": True})
    try:
        by_ts: dict[int, list] = {}
        cursor: int | None = None
        while len(by_ts) < n_bars:
            params = {"symbol": SYMBOL, "interval": TIMEFRAME, "limit": PAGE}
            if cursor is not None:
                params["endTime"] = cursor
            page = await ex.public_get_klines(params)
            if not page:
                break
            fresh = [row for row in page if int(row[0]) not in by_ts]
            for row in page:
                by_ts[int(row[0])] = row
            if not fresh:
                break
            cursor = min(int(row[0]) for row in page) - 1
        rows = [by_ts[k] for k in sorted(by_ts)]
        return rows[-n_bars:]
    finally:
        await ex.close()


async def _live() -> int:
    rows = await _fetch_klines(N_BARS)
    print(f"fetched {len(rows)} raw klines {SYMBOL} {TIMEFRAME} ({len(rows[0])} fields/row)")
    if len(rows) < 500:
        print("NOT EVALUABLE: too few bars")
        return 1
    imb, rets = imbalance(rows)
    n = len(rets)
    print(f"aligned bars: {n}")

    r0 = corr(imb, rets)
    print(f"C0 contemporaneous corr(I, r) = {r0:+.4f} (need > +0.30)")
    if not r0 > 0.30:
        print("STOP: field mapping suspect, H not run")
        return 1

    print(f"{'lag':>5} {'n':>6} {'corr':>9} {'t':>8}  verdict")
    held = False
    for lag in LAGS:
        pred = trailing_mean(imb[:-1], lag)
        tgt = rets[lag:]
        r = corr(pred, tgt)
        t = tstat(r, len(tgt))
        ok = abs(t) > TBAR
        held = held or ok
        print(f"{lag:>5} {len(tgt):>6} {r:>+9.4f} {t:>+8.2f}  {'HOLDS' if ok else '.'}")
    print(
        f"\nH trailing imbalance predicts next-bar return: "
        f"{'HOLDS' if held else 'FAILS'} (bar |t| > {TBAR:.2f}, lags {list(LAGS)})"
    )
    return 0


def main() -> int:
    import asyncio

    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    return asyncio.run(_live())


if __name__ == "__main__":
    raise SystemExit(main())
