"""R-56: the composite portfolio against two constructed no-edge controls.

Registered S29: REAL vs RANDOM (same activity by construction) vs SHUFFLED
(return time order destroyed), same aggregate statistic, null and windows.
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

from tools.composite_portfolio_verdict import (  # noqa: E402
    DRAWS, LENGTHS, N_BARS, PAIRS, REPS, SEED, TRAIN, VAL, _pair_net_and_null,
)
from tools.composite_strategy_verdict import _actions, _composite  # noqa: E402
from tools.neuroevolution_count import build_real_rows  # noqa: E402
from engine.strategy_plugin import StrategyDecision  # noqa: E402
from tools.timeframe_verdict import _klines, _series  # noqa: E402


def _random_decisions(real: list[StrategyDecision], q: float, seed: int) -> list[StrategyDecision]:
    """Random held/unheld with the real activity rate, same fill path."""
    rng = random.Random(seed)
    out: list[StrategyDecision] = []
    long = False
    for _ in real:
        if rng.random() < q:
            if not long:
                out.append(StrategyDecision(action="BUY", strength=1.0))
                long = True
            else:
                out.append(StrategyDecision(action="HOLD", strength=1.0, intent="long"))
        else:
            if long:
                out.append(StrategyDecision(action="SELL", strength=1.0))
                long = False
            else:
                out.append(StrategyDecision(action="HOLD", strength=0.0))
    return out


def _shuffled_closes(closes: list[float], seed: int) -> list[float]:
    rng = random.Random(seed)
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    rng.shuffle(rets)
    out = [closes[0]]
    for r in rets:
        out.append(out[-1] * math.exp(r))
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--block", choices=("earlier", "later"), default="earlier")
    args = ap.parse_args()
    rows = await _klines("1d", N_BARS * 2, symbol=args.symbol)
    rows = rows[:N_BARS] if args.block == "earlier" else rows[N_BARS:]
    raw_closes = [float(r[4]) for r in rows]
    X, y, R = build_real_rows(raw_closes)
    N = len(y)
    print(f"{args.block} block {args.symbol} {len(rows)} bars "
          f"({rows[0][0]}..{rows[-1][0]})")
    pass_counts = {"REAL": 0, "RANDOM": 0, "SHUFFLED": 0}

    for length in LENGTHS:
        iva_end = N - length
        itr_end = iva_end - VAL
        s = _series(rows[itr_end - TRAIN + 7:N + 7])
        wcloses = s["close"]
        acts = {k: _actions(REPS[k], s) for k in REPS}
        pair_decisions = [_composite(acts[a], acts[b]) for a, b in PAIRS]
        shuffled = _shuffled_closes(wcloses, SEED + length)

        for arm in ("REAL", "RANDOM", "SHUFFLED"):
            stats = []
            for pi, decisions in enumerate(pair_decisions):
                if arm == "REAL":
                    d, px = decisions, wcloses
                elif arm == "RANDOM":
                    q = sum(1 for x in decisions if x.action in {"BUY", "HOLD"}
                            and x.intent == "long") / len(decisions)
                    d, px = _random_decisions(decisions, q, SEED + pi), wcloses
                else:
                    d, px = decisions, shuffled
                stats.append(_pair_net_and_null(d, px, SEED + pi))
            observed = sum(n for n, _, _ in stats) / len(stats)
            nulls = [sum(v[k] for _, _, v in stats) / len(stats) for k in range(DRAWS)]
            p = (1 + sum(1 for v in nulls if v >= observed)) / (DRAWS + 1)
            ok = observed > 0 and p < 0.05
            pass_counts[arm] += int(ok)
            print(f"  window {length:3d} {arm:8} net {observed:>+9.2f} p {p:.4f} "
                  f"{'HOLDS' if ok else '.'}")
    real_ok = pass_counts["REAL"] >= 3
    controls_ok = pass_counts["RANDOM"] <= 1 and pass_counts["SHUFFLED"] <= 1
    verdict = "HOLDS" if real_ok and controls_ok else "FAILS"
    print(f"\npass counts: {pass_counts}")
    print(f"R-57 {verdict} ({args.symbol}, {args.block})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
