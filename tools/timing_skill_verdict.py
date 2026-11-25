"""R-58: market-neutral timing skill, drift removed by construction.

Registered S31: held bars are scored against their own window's mean return,
so being long in a rising market cannot produce a positive score. Four arms,
500-draw null, RANDOM/SHUFFLED controls, 2 bps power injection.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from tools.composite_portfolio_control import _random_decisions  # noqa: E402
from tools.composite_strategy_verdict import (  # noqa: E402
    DRAWS, FEE_BPS, LENGTHS, N_BARS, NOTIONAL, PAIRS, REPS, SEED, TRAIN, VAL,
    _actions, _composite,
)
from tools.neuroevolution_count import build_real_rows  # noqa: E402
from tools.timeframe_verdict import _klines, _series  # noqa: E402


def _held(decisions, n: int) -> list[bool]:
    out = [False] * n
    pos = False
    for i, d in enumerate(decisions[:n]):
        a = str(d.action).upper()
        if a in {"BUY", "LONG"}:
            pos = True
        elif a in {"SELL", "SHORT"}:
            pos = False
        out[i] = pos
    return out


def _pair_skill(
    decisions, rets: list[float], mu: float, seed: int, edge_bps: float = 0.0,
    q_override: float | None = None,
) -> tuple[float, list[float]]:
    n = len(rets)
    held = _held(decisions, n)
    q = q_override if q_override is not None else sum(held) / max(1, n)
    demeaned = [r - mu for r in rets]
    flips = sum(1 for i in range(1, n) if held[i] != held[i - 1]) + int(held[0])
    skill = sum(demeaned[i] for i in range(n) if held[i]) * NOTIONAL
    skill -= flips * FEE_BPS / 1e4 * NOTIONAL
    if edge_bps:
        skill += edge_bps / 1e4 * NOTIONAL * sum(held)
    rng = random.Random(seed)
    nulls: list[float] = []
    for _ in range(DRAWS):
        state, gross, nf = False, 0.0, 0
        for i in range(n):
            want = rng.random() < q
            if want != state:
                nf += 1
                state = want
            if state:
                gross += demeaned[i] * NOTIONAL
        nulls.append(gross - nf * FEE_BPS / 1e4 * NOTIONAL)
    return skill, nulls


def _p(observed: float, nulls: list[float]) -> float:
    return (1 + sum(1 for v in nulls if v >= observed)) / (len(nulls) + 1)


async def _run(symbol: str, block: str) -> tuple[dict[str, int], int]:
    rows = await _klines("1d", N_BARS * 2, symbol=symbol)
    rows = rows[:N_BARS] if block == "earlier" else rows[N_BARS:]
    raw = [float(r[4]) for r in rows]
    X, y, R = build_real_rows(raw)
    N = len(y)
    counts = {k: 0 for k in ("REAL", "RANDOM", "SHUFFLED", "POWER")}
    print(f"\n{symbol} {block}: {len(rows)} bars ({rows[0][0]}..{rows[-1][0]})")
    for length in LENGTHS:
        iva_end = N - length
        itr_end = iva_end - VAL
        s = _series(rows[itr_end - TRAIN + 7:N + 7])
        closes = s["close"]
        rets = [0.0] + [math.log(closes[i] / closes[i - 1])
                        for i in range(1, len(closes))]
        mu = sum(rets) / len(rets)
        acts = {k: _actions(REPS[k], s) for k in REPS}
        pairs = [_composite(acts[a], acts[b]) for a, b in PAIRS]
        tail = rets[1:].copy()
        random.Random(SEED + length).shuffle(tail)  # in place; a shuffled slice is discarded
        shuffled_rets = [0.0] + tail
        shuffled_mu = sum(shuffled_rets) / len(shuffled_rets)

        row = {}
        for arm in ("REAL", "RANDOM", "SHUFFLED", "POWER"):
            stats = []
            for pi, d in enumerate(pairs):
                if arm == "REAL":
                    skill, nulls = _pair_skill(d, rets, mu, SEED + pi)
                elif arm == "RANDOM":
                    q = sum(_held(d, len(rets))) / len(rets)
                    rd = _random_decisions(d, q, SEED + pi)
                    skill, nulls = _pair_skill(rd, rets, mu, SEED + pi, q_override=q)
                elif arm == "SHUFFLED":
                    skill, nulls = _pair_skill(d, shuffled_rets, shuffled_mu, SEED + pi)
                else:
                    skill, nulls = _pair_skill(d, rets, mu, SEED + pi, edge_bps=2.0)
                stats.append((skill, nulls))
            observed = sum(v[0] for v in stats) / len(stats)
            nulls = [sum(v[1][k] for v in stats) / len(stats) for k in range(DRAWS)]
            p = _p(observed, nulls)
            ok = observed > 0 and p < 0.05
            counts[arm] += int(ok)
            row[arm] = (observed, p, ok)
        print(f"  window {length:3d}: " + " | ".join(
            f"{a} {row[a][0]:>+8.2f} p{row[a][1]:.3f}{'*' if row[a][2] else ''}"
            for a in ("REAL", "RANDOM", "SHUFFLED", "POWER")
        ))
    return counts, DRAWS


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--block", choices=("earlier", "later"), default="earlier")
    args = ap.parse_args()
    counts, _ = await _run(args.symbol, args.block)
    adequate = counts["POWER"] >= 3
    real = counts["REAL"] >= 3
    controls = counts["RANDOM"] <= 1 and counts["SHUFFLED"] <= 1
    verdict = "HOLDS" if adequate and real and controls else "FAILS"
    print(f"counts: {counts}; adequate={adequate} real={real} controls_clean={controls}")
    print(f"R-58 {verdict} ({args.symbol}, {args.block})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
