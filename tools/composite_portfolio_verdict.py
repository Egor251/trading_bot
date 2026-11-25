"""R-55: the six-pair composite PORTFOLIO as one statistic, with power check.

Registered S28: equal-weight mean net over the six pre-fixed pairs, null of
independently randomized timing per pair, 0/2/5 bps injection into evaluated
aggregate net only, four windows on the earlier disjoint BTCUSDT block.
"""

from __future__ import annotations

import asyncio
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.indicators import equity_pnl  # noqa: E402
from engine.research import decisions_to_fills  # noqa: E402
from tools.composite_strategy_verdict import (  # noqa: E402
    DRAWS, FEE_BPS, LENGTHS, N_BARS, NOTIONAL, PAIRS, REPS, SEED, SIZING,
    TRAIN, VAL, _actions, _composite,
)
from tools.neuroevolution_count import build_real_rows  # noqa: E402
from tools.timeframe_verdict import _klines, _series  # noqa: E402

EDGES = (0.0, 2.0, 5.0)


def _pair_net_and_null(
    decisions, closes: list[float], seed: int
) -> tuple[float, int, list[float]]:
    fills = decisions_to_fills(
        decisions, closes, sizing=SIZING, fee_bps=FEE_BPS,
        score_from=0, ticker="BTCUSDT", position_mode="long_flat",
    )
    net = float(equity_pnl(fills, start_equity=10_000.0, mark_price=closes[-1])["pnl"])
    held = [False] * len(closes)
    pos = False
    by_bar = {int(f["bar_index"]): str(f["action"]) for f in fills}
    for i in range(len(closes)):
        act = by_bar.get(i)
        if act == "BUY":
            pos = True
        elif act == "SELL":
            pos = False
        held[i] = pos
    rets = [0.0] + [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    q = sum(held[1:]) / max(1, len(held) - 1)
    rng = random.Random(seed)
    nulls: list[float] = []
    for _ in range(DRAWS):
        state, gross, flips = False, 0.0, 0
        for i in range(1, len(closes)):
            want = rng.random() < q
            if want != state:
                flips += 1
                state = want
            if state:
                gross += rets[i] * NOTIONAL
        nulls.append(gross - flips * FEE_BPS / 1e4 * NOTIONAL)
    return net, sum(held), nulls


async def main() -> int:
    rows = await _klines("1d", N_BARS * 2)
    rows = rows[:N_BARS]
    closes = [float(r[4]) for r in rows]
    X, y, R = build_real_rows(closes)
    N = len(y)
    print(f"earlier block {len(rows)} bars ({rows[0][0]}..{rows[-1][0]})")
    summary: dict[float, list[bool]] = {}
    for edge in EDGES:
        passed: list[bool] = []
        print(f"\nedge {edge:.0f} bps per held bar:")
        for length in LENGTHS:
            iva_end = N - length
            itr_end = iva_end - VAL
            s = _series(rows[itr_end - TRAIN + 7:N + 7])
            wcloses = s["close"]
            acts = {k: _actions(REPS[k], s) for k in REPS}
            pair_stats = [
                _pair_net_and_null(_composite(acts[a], acts[b]), wcloses, SEED + pi)
                for pi, (a, b) in enumerate(PAIRS)
            ]
            observed = sum(
                net + edge / 1e4 * NOTIONAL * held
                for net, held, _ in pair_stats
            ) / len(pair_stats)
            null_portfolio = [
                sum(v[d] for _, _, v in pair_stats) / len(pair_stats)
                for d in range(DRAWS)
            ]
            p = (1 + sum(1 for v in null_portfolio if v >= observed)) / (DRAWS + 1)
            ok = observed > 0 and p < 0.05
            passed.append(ok)
            print(f"  window {length:3d}: portfolio net {observed:>+9.2f} "
                  f"p {p:.4f} {'HOLDS' if ok else '.'}")
        summary[edge] = passed
    control_ok = not any(summary[0.0])
    adequate = sum(summary[2.0]) >= 3
    real = sum(summary[0.0]) >= 3
    verdict = "HOLDS" if adequate and control_ok and real else (
        "INCONCLUSIVE" if not adequate else "FAILS"
    )
    print(f"\ncontrol clean: {control_ok}; 2bps detection: {sum(summary[2.0])}/4; "
          f"real aggregate: {sum(summary[0.0])}/4")
    print(f"R-55 {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
