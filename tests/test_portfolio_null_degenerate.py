"""The portfolio null is degenerate: shuffling cannot change the answer.

`tools/portfolio_trend.py` tests H1 by comparing the portfolio's window P&L
against the p90 of a null built by shuffling that same window. The shuffle was
assumed to redistribute returns across time. It does not, because `_costed`
uses only two properties of the window:

    gross  = sum(legs)                     -- invariant under permutation
    active = count(legs where leg != 0)    -- invariant under permutation

so `_costed(shuffled) == _costed(window)` for **every** permutation. The 40
null draws are the same number. p90 of a constant equals that constant, and
`w >= p90` is then true by arithmetic for any portfolio whatsoever.

H1 could not have failed. It reported 4/4 while the portfolio total was
-1.7005 with all fifteen members negative.

These tests pin the defect. They are known-answer tests in the sense that rule
2 of the register requires: the answer for a degenerate null is a variance of
exactly zero, and that is not in doubt.
"""

from __future__ import annotations

import itertools
import math
import random

import pytest

# Mirrors tools/portfolio_trend.py. Copied rather than imported because
# `_costed` is nested inside `main()` and therefore not reachable. If it is
# ever hoisted, this must be replaced by the import -- a copy that drifts from
# the original is the same defect class as a placeholder schema.
COST_BPS = 7.0


def _costed(legs: list[float]) -> float:
    gross = sum(legs)
    active = sum(1 for x in legs if x != 0.0)
    return gross - active * COST_BPS / 1e4


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = max(0.0, min(1.0, q)) * (len(ordered) - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return float(ordered[lo])
    w = pos - lo
    return float(ordered[lo]) * (1 - w) + float(ordered[hi]) * w


WINDOW = [0.0] * 400 + [0.01, -0.008, 0.004, -0.013, 0.006] * 60


def test_costed_is_invariant_under_every_permutation():
    """The whole defect in one assertion: the statistic the null is built from
    cannot see the shuffle."""
    baseline = _costed(WINDOW)
    # Exhaustive over a short window; sampled over the full one.
    short = WINDOW[:8]
    for perm in itertools.permutations(short):
        assert _costed(list(perm)) == pytest.approx(_costed(short))

    rng = random.Random(7)
    for _ in range(500):
        shuffled = list(WINDOW)
        rng.shuffle(shuffled)
        assert _costed(shuffled) == pytest.approx(baseline)


def test_null_draws_have_zero_variance():
    pool = []
    rng = random.Random(20261013)
    for _ in range(40):
        shuffled = list(WINDOW)
        rng.shuffle(shuffled)
        pool.append(_costed(shuffled))

    assert len(pool) == 40
    assert max(pool) - min(pool) == 0.0
    assert all(v == pytest.approx(pool[0]) for v in pool)


def test_h1_is_unfalsifiable_even_for_a_deliberately_bad_portfolio():
    """The registered criterion passes a strategy engineered to lose.

    This is the property that matters. A criterion that cannot distinguish a
    good strategy from a bad one is not a weak test, it is no test.
    """
    catastrophic = [-0.5] * 749          # loses 50% per bar, flat
    portfolio = _costed(catastrophic)

    pool = []
    rng = random.Random(20261013)
    for _ in range(40):
        shuffled = list(catastrophic)
        rng.shuffle(shuffled)
        pool.append(_costed(shuffled))

    p90 = _percentile(pool, 90.0)
    assert portfolio >= p90, "a portfolio losing 374x its capital 'beats random timing'"
    assert p90 == pytest.approx(portfolio)


def test_the_four_window_result_in_the_log_is_reproducible_as_an_identity():
    """4/4 does not encode information about the portfolio. Any window count
    gives 4/4, including for a null built from a different seed."""
    def cleared(portfolio_windows, seed):
        rng = random.Random(seed)
        out = []
        for w in portfolio_windows:
            pool = []
            for _ in range(40):
                shuffled = list(w)
                rng.shuffle(shuffled)
                pool.append(_costed(shuffled))
            out.append(_costed(w) >= _percentile(pool, 90.0))
        return sum(out)

    windows = [[0.001, -0.002] * 375] * 4
    assert cleared(windows, 20261013) == 4
    assert cleared(windows, 1) == 4
    assert cleared(windows, 999999) == 4