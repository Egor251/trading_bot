"""Pin the cross-sectional null (R-37, raw-record S5).

Same discipline as the held-bars pins: on synthetic 6-instrument panels with no
network, the null must vary (C1), rank sensibly — anti-selection fails (C2a),
full-hold is refused not scored (C2b) — and be beatable by pro-selection (C3).

Small windows for suite speed; the live design (749 x 200) is exercised by
`tools/portfolio_trend.py --selftest2`.
"""

from __future__ import annotations

import random

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from tools.portfolio_trend import (  # noqa: E402
    _cross_null_windows,
    _null_totals,
    _percentile,
    _score_window,
)

WB, HIST, NV = 60, 20, 6
WINDOWS = 4
DRAWS = 30
SEED = 362115


def _blocks(drifts: list[float], seed: int) -> list[list[list[float]]]:
    rng = random.Random(seed)
    return [
        [[rng.gauss(d, 0.02) for _ in range(WB + HIST)] for d in drifts] for _ in range(WINDOWS)
    ]


def _to_panel(blocks: list[list[list[float]]], rule: str):
    wins = []
    for rets in blocks:
        pw = []
        for t in range(HIST, HIST + WB):
            cands = [(1.0, rets[s][t]) for s in range(NV)]
            trail = [sum(rets[s][t - HIST : t]) / HIST for s in range(NV)]
            order = sorted(range(NV), key=lambda s: trail[s])
            idx = sorted(order[: NV // 2] if rule == "anti" else order[-2:])
            pw.append((cands, idx))
        wins.append(pw)
    return wins


def _obs(wins) -> list[float]:
    return [_score_window(pw, [obs for _, obs in pw]) for pw in wins]


def test_c1_varies_and_c2a_anti_selection_fails():
    loser = _to_panel(_blocks([-0.008] * 3 + [0.0] * 3, 777), "anti")
    pools, _ = _cross_null_windows(loser, DRAWS, SEED)
    assert all(max(p) - min(p) > 0 for p in pools)
    obs = _obs(loser)
    assert sum(1 for j in range(WINDOWS) if obs[j] >= _percentile(pools[j], 0.9)) == 0
    assert sum(obs) < _percentile(_null_totals(pools), 0.5)


def test_c2b_full_hold_has_no_spread_to_score():
    full = [([(1.0, 0.01)] * NV, list(range(NV))) for _ in range(WB)]
    pools, _ = _cross_null_windows([full] * WINDOWS, DRAWS, SEED)
    assert all(max(p) - min(p) == 0.0 for p in pools)


def test_c3_pro_selection_beats_the_null():
    fat = _to_panel(_blocks([0.015, 0.015, -0.005, -0.005, -0.005, -0.005], 777), "pro2")
    pools, _ = _cross_null_windows(fat, DRAWS, SEED)
    assert sum(_obs(fat)) >= _percentile(_null_totals(pools), 0.9)
