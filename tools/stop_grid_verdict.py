"""R-53: is the stop mechanism robust across a fixed 3x3 parameter grid?

Registered S26: one pre-fixed pair, nine stop/cooldown configurations, the
earlier disjoint BTCUSDT daily block, four nested windows, Holm over nine.
HOLDS iff one configuration clears net>0, beats baseline and Holm p<0.05 in
all four windows. No configuration is promoted to live from this run.
"""

from __future__ import annotations

import asyncio
import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.simulation import adjust_pvalues  # noqa: E402
from tools.neuroevolution_count import build_real_rows  # noqa: E402
from tools.stop_loss_composite import (  # noqa: E402
    LENGTHS, N_BARS, REPS, SEED, TRAIN, VAL,
    _actions, _composite, _net_and_p, _with_stop,
)
from tools.timeframe_verdict import _klines, _series  # noqa: E402

STOPS = (0.01, 0.02, 0.03)
COOLDOWNS = (5, 10, 20)
GRID = list(itertools.product(STOPS, COOLDOWNS))


def configuration_passing_all(
    winners_per_window: list[set[tuple[float, int]]],
    grid: list[tuple[float, int]] = GRID,
) -> set[tuple[float, int]]:
    """The SAME configuration must clear every window.

    Known-answer defect caught on the first run: a window-by-window "any
    winner" check reported HOLDS while window 120 passed only (1%,10) and
    window 240 only (1%,20)/(2%,20) -- their intersection was empty.
    """
    if not winners_per_window:
        return set()
    passing = set(grid)
    for winners in winners_per_window:
        passing &= winners
    return passing


async def main() -> int:
    rows = await _klines("1d", N_BARS * 2)
    rows = rows[:N_BARS]
    closes = [float(r[4]) for r in rows]
    print(f"earlier block: {len(rows)} BTCUSDT 1d klines ({rows[0][0]}..{rows[-1][0]})")
    if len(rows) < N_BARS:
        print("NOT EVALUABLE")
        return 1
    X, y, R = build_real_rows(closes)
    N = len(y)
    all_ok = True
    passing: set[tuple[float, int]] = set(GRID)
    window_results: list[set[tuple[float, int]]] = []
    for length in LENGTHS:
        iva_end = N - length
        itr_end = iva_end - VAL
        s = _series(rows[itr_end - TRAIN + 7:N + 7])
        wcloses = s["close"]
        entry_acts = {k: _actions(REPS[k], s) for k in REPS}
        a, b = "breakout", "trend"  # dual_thrust -> adx_di_trend, fixed in S26
        base, _ = _net_and_p(_composite(entry_acts[a], entry_acts[b]), wcloses, SEED)
        results = []
        for gi, (stop, cd) in enumerate(GRID):
            net, p = _net_and_p(
                _with_stop(entry_acts[a], entry_acts[b], wcloses, stop, cd),
                wcloses, SEED + gi,
            )
            results.append((stop, cd, net, p))
        adjusted = adjust_pvalues([r[3] for r in results], "holm")
        print(f"\nholdout {length}: baseline net {base:+.2f}")
        for (stop, cd, net, _p), q in zip(results, adjusted):
            ok = net > 0 and net > base and q < 0.05
            print(f"  stop={stop:.0%} cd={cd:2d}  net {net:>+9.2f} holm {q:.4f} "
                  f"{'HOLDS' if ok else '.'}")
        winners = {(s, c) for (s, c, net, _), q in zip(results, adjusted)
                   if net > 0 and net > base and q < 0.05}
        window_results.append(winners)
        print(f"  winners: {sorted(winners) or 'NONE'}")
    passing = configuration_passing_all(window_results)
    all_ok = bool(passing)
    print(f"\nconfigurations passing all four windows: {sorted(passing) or 'NONE'}")
    print(f"R-53 {'HOLDS' if all_ok else 'FAILS'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
