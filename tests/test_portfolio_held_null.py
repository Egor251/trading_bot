"""Pin the held-bars null (R-36, raw-record S4).

The permuted null died because it could not vary; the held-bars null must prove,
on synthetic windows with no network, that it can — and that it refuses when it
cannot (q=1). Four properties:

  N1  spread > 0 on ordinary windows (rule 1: a null that cannot vary is read
      before any comparison against it).
  N2a a losing, partially active portfolio fails (0/4 windows, total below the
      null median).
  N2b a flat -50%-per-bar portfolio (q=1, holding forced) yields spread exactly
      0 — the gate must refuse it, never pass it. The old null printed 4/4 here.
  N3  an oracle holding the top-q bars clears p90 — a null nobody can beat is
      no test either.

Small windows for suite speed; the live design (749 x 200) is exercised by
`tools/portfolio_trend.py --selftest`.
"""

from __future__ import annotations

import random

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from tools.portfolio_trend import (  # noqa: E402
    _held_null_windows,
    _null_totals,
    _percentile,
)

W = 200
DRAWS = 50
SEED = 361115


def _gen(fn: str) -> list[list[float]]:
    rng = random.Random(20240117)
    if fn == "loser":
        return [
            [rng.gauss(-0.005, 0.01) if rng.random() < 0.6 else 0.0 for _ in range(W)]
            for _ in range(4)
        ]
    if fn == "fat":
        return [
            [
                0.05
                if rng.random() < 0.10
                else (rng.gauss(0.0, 0.005) if rng.random() < 0.6 else 0.0)
                for _ in range(W)
            ]
            for _ in range(4)
        ]
    raise AssertionError(fn)


def test_percentile_takes_a_fraction():
    ordered = [float(i) for i in range(100)]
    assert _percentile(ordered, 0.9) < 99.0
    assert _percentile(ordered, 0.9) > 89.0


def test_n1_null_varies_and_n2a_loser_fails():
    loser = _gen("loser")
    pools, _ = _held_null_windows(loser, DRAWS, SEED)
    assert all(max(p) - min(p) > 0 for p in pools)
    obs = [sum(w) - sum(1 for x in w if x != 0.0) * 7.0 / 1e4 for w in loser]
    assert sum(1 for j in range(4) if obs[j] >= _percentile(pools[j], 0.9)) == 0
    assert sum(obs) < _percentile(_null_totals(pools), 0.5)


def test_n2b_forced_holding_has_no_spread_to_score():
    flat = [[-0.5] * W for _ in range(4)]
    pools, rates = _held_null_windows(flat, DRAWS, SEED)
    assert rates == [1.0] * 4
    assert all(max(p) - min(p) == 0.0 for p in pools)


def test_n3_oracle_beats_the_null():
    fat = _gen("fat")
    pools, rates = _held_null_windows(fat, DRAWS, SEED)
    oracle = 0.0
    for w, q in zip(fat, rates):
        top = sorted(w, reverse=True)[: int(round(q * len(w)))]
        oracle += sum(top) - len(top) * 7.0 / 1e4
    assert oracle >= _percentile(_null_totals(pools), 0.9)
