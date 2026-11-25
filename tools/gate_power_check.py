"""R-54: can the composite gate detect an injected weak edge at all?

Registered S27: same six pairs, four windows, 500-draw null, Holm over six,
on the earlier disjoint block. Edge is injected into evaluated P&L only, not
into the price series that drives the indicators. Ladder 0/1/2/5 bps per held
bar. ADEQUATE iff 2 bps produces a pair passing all four windows; the 0 row
must not pass.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.simulation import adjust_pvalues  # noqa: E402
from tools.composite_strategy_verdict import (  # noqa: E402
    LENGTHS, N_BARS, PAIRS, REPS, SEED, TRAIN, VAL,
    _actions, _composite, _net_and_p,
)
from tools.neuroevolution_count import build_real_rows  # noqa: E402
from tools.timeframe_verdict import _klines, _series  # noqa: E402

EDGES = (0.0, 1.0, 2.0, 5.0)


async def main() -> int:
    rows = await _klines("1d", N_BARS * 2)
    rows = rows[:N_BARS]
    closes = [float(r[4]) for r in rows]
    X, y, R = build_real_rows(closes)
    N = len(y)
    print(f"earlier block {len(rows)} bars ({rows[0][0]}..{rows[-1][0]})")

    results: dict[float, set[int]] = {}
    for edge in EDGES:
        passing = set(range(len(PAIRS)))
        detail = []
        for length in LENGTHS:
            iva_end = N - length
            itr_end = iva_end - VAL
            s = _series(rows[itr_end - TRAIN + 7:N + 7])
            wcloses = s["close"]
            acts = {k: _actions(REPS[k], s) for k in REPS}
            nets_ps = []
            for pi, (a, b) in enumerate(PAIRS):
                net, p = _net_and_p(
                    _composite(acts[a], acts[b]), wcloses, SEED + pi, edge_bps=edge
                )
                nets_ps.append((net, p))
            adjusted = adjust_pvalues([p for _, p in nets_ps], "holm")
            winners = {i for i, ((net, _), q) in enumerate(zip(nets_ps, adjusted))
                       if net > 0 and q < 0.05}
            passing &= winners
            detail.append(f"{length}:{len(winners)}")
        results[edge] = passing
        print(f"edge {edge:>4.0f} bps/held-bar -> passing all four: "
              f"{sorted(passing) or 'NONE'}  (winners/window {', '.join(detail)})")
    adequate = bool(results[2.0])
    control_ok = not results[0.0]
    verdict = "ADEQUATE" if adequate and control_ok else "INCONCLUSIVE"
    print(f"\ncontrol 0 bps clean: {control_ok}; 2 bps detected: {adequate}")
    print(f"R-54 {verdict}: "
          f"{'negatives from this gate carry information about weak edges' if adequate else 'negatives from this gate are inconclusive about weak edges'}")
    return 0 if control_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
