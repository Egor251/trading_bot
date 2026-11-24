"""R-47: the same model at four short nested horizons.

Registered S20 before running: BTCUSDT daily bars, 1200-bar span, four test
lengths 60/120/240/360 ending on the same last bar, with train 600 and
validation 100 immediately before each. HOLDS iff all four horizons clear
both bars (net > 0 and accuracy >= 0.53).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

import numpy as np  # noqa: E402

from engine.simulation import cscv_pbo  # noqa: E402
from tools.neuroevolution_count import (  # noqa: E402
    build_real_rows,
    enumerate_architectures,
    net_long_flat,
    per_bar_correct,
    predict_proba,
    run_census,
    run_evolution,
)
from tools.timeframe_verdict import _klines  # noqa: E402

N_BARS = 1200
TRAIN, VAL = 600, 100
LENGTHS = (60, 120, 240, 360)
SEED = 47


async def main() -> int:
    rows = await _klines("1d", N_BARS)
    closes = [float(r[4]) for r in rows]
    print(f"fetched {len(rows)} BTCUSDT 1d klines")
    if len(closes) < N_BARS:
        print("NOT EVALUABLE: fewer bars than the registered span")
        return 1
    X, y, R = build_real_rows(closes)
    N = len(y)
    archs = enumerate_architectures()
    assert len(archs) == 50

    all_ok = True
    print(f"{'holdout':>8} {'acc':>7} {'net':>9} {'eval':>5} {'distinct':>8} {'PBO':>6}")
    print("-" * 52)
    for length in LENGTHS:
        ite = np.arange(N - length, N)
        iva = np.arange(N - length - VAL, N - length)
        itr = np.arange(N - length - VAL - TRAIN, N - length - VAL)
        run_census(
            X, y, itr, iva, ite, archs, seed0=SEED * 77 + 3
        )  # fittest-by-construction census
        evo = run_evolution(X, y, itr, iva, archs, seed=SEED * 131 + 5)
        ens = sum(
            predict_proba(evo["distinct"][k]["model"], X[ite]) for k in evo["distinct"]
        ) / len(evo["distinct"])
        acc = float(((ens > 0.5).astype(int) == y[ite]).mean())
        net = net_long_flat(ens, R[ite])
        idx = np.array_split(np.arange(len(iva)), 12)
        series = [
            [
                float(
                    np.array(per_bar_correct(evo["distinct"][k]["model"], X[iva], y[iva]))[b].mean()
                )
                for b in idx
            ]
            for k in evo["distinct"]
        ]
        pbo = cscv_pbo(series, splits=8)
        ok = acc >= 0.53 and net > 0.0
        all_ok = all_ok and ok
        print(
            f"{length:>8} {acc:>7.4f} {net:>+9.4f} {evo['evaluations']:>5} "
            f"{len(evo['distinct']):>8} {pbo['pbo']:>6.3f} "
            f"{'HOLDS' if ok else 'FAILS'}"
        )
    print(
        f"\nR-47 {'HOLDS' if all_ok else 'FAILS'}: "
        f"{'all four horizons clear both bars' if all_ok else 'at least one horizon misses'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
