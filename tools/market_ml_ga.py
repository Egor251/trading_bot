"""R-59: richer historical market inputs, GA over the registered 50-arch space.

Registered S34: three instruments' 1h klines plus raw taker-buy imbalance,
21 columns, same optimiser/GA/costs/controls as S3. Order book and news are
NOT available historically and are not fabricated (S35 creates a forward tool).
"""

from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

import numpy as np  # noqa: E402

from engine.simulation import cscv_pbo  # noqa: E402
from tools.neuroevolution_count import (  # noqa: E402
    enumerate_architectures, net_long_flat, per_bar_correct, predict_proba,
    run_census, run_evolution, stage_known,
)
from tools.timeframe_verdict import _klines  # noqa: E402

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
N_BARS = 6000
TRAIN, VAL = 3600, 1200
SEED = 41


def _features(rows_by_symbol: dict[str, list[list]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    aligned: dict[int, dict[str, list]] = {}
    for sym, rows in rows_by_symbol.items():
        for r in rows:
            aligned.setdefault(int(r[0]), {})[sym] = r
    ts = sorted(aligned)
    closes = {s: [float(aligned[t][s][4]) for t in ts] for s in SYMBOLS}
    n = len(ts)
    X_rows, y, R = [], [], []
    for i in range(5, n - 1):
        cols = []
        for s in SYMBOLS:
            c = closes[s]
            rets = [math.log(c[j] / c[j - 1]) for j in range(i - 5, i + 1)]
            cols.extend(rets)
            vol = float(aligned[ts[i]][s][5])
            tb = float(aligned[ts[i]][s][9])
            cols.append(2.0 * tb / vol - 1.0 if vol > 0 else 0.0)
        X_rows.append(cols)
        btc = closes["BTCUSDT"]
        R.append(math.log(btc[i + 1] / btc[i]))
        y.append(int(R[-1] > 0))
    return np.array(X_rows), np.array(y), np.array(R)


async def main() -> int:
    stage_known()  # refuse to read results if the trainer instrument is broken
    fetched = {}
    for sym in SYMBOLS:
        fetched[sym] = (await _klines("1h", N_BARS, symbol=sym))[-N_BARS:]
        print(f"{sym}: {len(fetched[sym])} klines")
    X, y_sig, R = _features(fetched)
    N = len(y_sig)
    assert N >= TRAIN + VAL + 200, f"only {N} aligned bars"
    itr, iva = np.arange(TRAIN), np.arange(TRAIN, TRAIN + VAL)
    ite = np.arange(TRAIN + VAL, N)
    rng = np.random.default_rng(SEED)
    y_noise = rng.permutation(y_sig)
    print(f"aligned rows {N}, columns {X.shape[1]}, splits {TRAIN}/{VAL}/{len(ite)}")
    archs = enumerate_architectures()
    assert len(archs) == 50

    for tag, y in (("signal", y_sig), ("noise", y_noise)):
        census = run_census(X, y, itr, iva, ite, archs, seed0=SEED * 77 + 3)
        evo = run_evolution(X, y, itr, iva, archs, seed=SEED * 131 + 5)
        ens = sum(
            predict_proba(evo["distinct"][k]["model"], X[ite]) for k in evo["distinct"]
        ) / len(evo["distinct"])
        acc = float(((ens > 0.5).astype(int) == y[ite]).mean())
        net = net_long_flat(ens, R[ite])
        idx = np.array_split(np.arange(len(iva)), 16)
        series = [
            [float(np.array(per_bar_correct(evo["distinct"][k]["model"], X[iva], y[iva]))[b].mean())
             for b in idx]
            for k in evo["distinct"]
        ]
        pbo = cscv_pbo(series, splits=8)
        print(f"--- {tag} ---")
        print(f"  census test mean {np.mean([r['test'] for r in census]):.4f} "
              f"sd {np.std([r['test'] for r in census]):.4f}")
        print(f"  evaluations {evo['evaluations']} distinct {len(evo['distinct'])}")
        print(f"  ensemble acc {acc:.4f} net {net:+.4f} PBO {pbo['pbo']:.3f}")

    print("\nR-59 criterion: ensemble net > 0 and acc >= 0.53 on signal, "
          "and not both on noise.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
