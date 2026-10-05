"""Time-series statistics belong in the engine, not in a research script.

`ar1`, `variance_ratio` and `resolvable_phi` were written into `tools/edge_ceiling.py`
because nothing in the codebase could answer "is this series predictable at all".
That is prior to any strategy question: a strategy set can only be as good as the
predictability of the series it trades, and every backtest that reports a P&L should
be answerable against it. Tools are for presentation; estimators that gate decisions
belong where the decisions are made.

Two of them carry a direction of error worth stating. `ar1` uses a Newey-West
standard error because an i.i.d. one understates the variance on autocorrelated data
and inflates apparent significance -- the one direction that manufactures findings.
`variance_ratio` uses the overlapping-sums correction because the naive variance of
overlapping q-bar sums understates the error badly enough to make a random walk look
significant.
"""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

import math  # noqa: E402
import random  # noqa: E402

import pytest  # noqa: E402

from engine.simulation import ar1, resolvable_phi, variance_ratio  # noqa: E402


def _ar1_series(phi: float, n: int, seed: int, noise: float = 1.0) -> list[float]:
    rng = random.Random(seed)
    out: list[float] = []
    value = 0.0
    for _ in range(n):
        value = phi * value + rng.gauss(0, noise)
        out.append(value)
    return out


# --- ar1 -------------------------------------------------------------------


def test_ar1_recovers_a_known_coefficient():
    series = _ar1_series(0.30, 6000, seed=1)
    result = ar1(series)
    assert abs(result["phi"] - 0.30) < 0.03
    assert result["t"] > 5.0


def test_ar1_finds_negative_autocorrelation():
    """The sign matters as much as the size, and the negative case is the one that
    showed up in real market data: a series that leans against its own last move."""
    series = _ar1_series(-0.10, 6000, seed=2)
    result = ar1(series)
    assert abs(result["phi"] + 0.10) < 0.02
    assert result["t"] < -4.0


def test_ar1_is_not_significant_on_iid_noise():
    """A false positive here would be the expensive kind.

    Newey-West widens the interval precisely so that noise does not read as signal.
    If this test fails, the standard error is too narrow and every downstream claim
    built on it is overstated.
    """
    rng = random.Random(3)
    series = [rng.gauss(0, 1) for _ in range(4000)]
    result = ar1(series)
    assert abs(result["phi"]) < 0.05
    assert abs(result["t"]) < 2.0


def _naive_se(values: list[float], phi: float) -> float:
    """The i.i.d. standard error, for comparison against the Newey-West one."""
    lead, lag_ = values[:-1], values[1:]
    mx = sum(lead) / len(lead)
    my = sum(lag_) / len(lag_)
    alpha = my - phi * mx
    resid = [b - (alpha + phi * a) for a, b in zip(lead, lag_)]
    sxx = sum((v - mx) ** 2 for v in lead)
    return math.sqrt(max(0.0, sum(e * e for e in resid) / (sxx * sxx)))


def test_newey_west_widens_the_interval_in_proportion_to_dependence():
    """The whole reason for the estimator, measured rather than asserted.

    Same series, same coefficient, same data -- only the standard error differs.
    The correction has to grow with the dependence it is correcting for: negligible
    on white noise, and substantial on a strongly autocorrelated series, which is
    exactly where an i.i.d. error would manufacture significance.
    """
    ratios = {}
    for phi in (0.0, 0.25, 0.50, 0.80):
        series = _ar1_series(phi, 5000, seed=4)
        result = ar1(series)
        naive = _naive_se(series, result["phi"])
        ratios[phi] = result["se"] / naive

    # No dependence to correct: the two estimators agree.
    assert 0.98 < ratios[0.0] < 1.02
    # And the correction grows with it.
    assert ratios[0.0] < ratios[0.25] < ratios[0.50] < ratios[0.80]
    assert ratios[0.80] > 1.3


def test_ar1_handles_short_and_degenerate_input():
    """Short series report no evidence instead of throwing or guessing."""
    for bad in ([], [1.0], [1.0, 2.0], [3.0] * 50):
        result = ar1(bad)
        assert result["n"] <= len(bad)
        assert math.isfinite(result["phi"])
        assert math.isfinite(result["se"])


def test_ar1_on_a_constant_series_does_not_divide_by_zero():
    result = ar1([2.5] * 200)
    assert result["phi"] == 0.0
    assert result["se"] == 0.0
    assert result["t"] == 0.0


# --- variance_ratio --------------------------------------------------------


def test_variance_ratio_is_one_on_a_random_walk():
    """The defining property. A driftless random walk has no autocorrelated
    increments, so the q-bar variance is q times the 1-bar variance exactly."""
    rng = random.Random(5)
    walk = [0.0]
    for _ in range(20000):
        walk.append(walk[-1] + rng.gauss(0, 1))
    increments = [walk[i + 1] - walk[i] for i in range(len(walk) - 1)]
    result = variance_ratio(increments, 5)
    assert abs(result["vr"] - 1.0) < 0.05
    assert abs(result["z"]) < 2.5


def test_variance_ratio_exceeds_one_under_positive_autocorrelation():
    series = _ar1_series(0.30, 20000, seed=6)
    assert variance_ratio(series, 5)["vr"] > 1.0


def test_variance_ratio_is_below_one_under_negative_autocorrelation():
    """Bounce is not drift. A series that leans against itself has VR(q) < 1, and
    reading that as momentum is the sign error that turns a real effect into a
    losing rule."""
    series = _ar1_series(-0.15, 20000, seed=7)
    result = variance_ratio(series, 5)
    assert result["vr"] < 1.0
    assert result["z"] < 0.0


def test_variance_ratio_rejects_too_short_input():
    """Below roughly 4q observations the z statistic is not defined.

    Returning vr = 1.0 is the honest answer: "cannot tell", not "no structure".
    """
    assert variance_ratio([0.1, 0.2, 0.3], 5)["vr"] == 1.0
    assert variance_ratio([0.1] * 500, 1)["vr"] == 1.0


def test_variance_ratio_on_a_constant_series():
    assert variance_ratio([0.0] * 2000, 4)["vr"] == 1.0


# --- resolvable_phi --------------------------------------------------------


def test_resolvable_phi_matches_the_sqrt_n_standard_error():
    assert resolvable_phi(100) == 1.96 / 10.0
    assert resolvable_phi(2500) == 1.96 / 50.0


def test_resolvable_phi_explains_why_a_short_window_cannot_prove_anything():
    """749 bars resolve about 0.072, so a 0.05 effect sits below the floor there.

    This is the arithmetic behind every "not significant" in a research log, and it
    is why a per-window autocorrelation number cannot be called anything on its own:
    the sample size decides whether the question is answerable before the market
    does. At 749 bars a true phi of 0.05 is indistinguishable from zero however the
    bars fall, so a window that reports 0.05 has reported nothing.
    """
    assert resolvable_phi(749) == pytest.approx(1.96 / math.sqrt(749))
    assert 0.07 < resolvable_phi(749) < 0.073
    assert resolvable_phi(749) > 0.05
    # The same 0.05 becomes resolvable once there are enough bars to see it.
    assert resolvable_phi(25000) < 0.013


def test_resolvable_phi_is_monotone_and_infinite_at_zero():
    assert resolvable_phi(10) > resolvable_phi(100) > resolvable_phi(1000)
    assert resolvable_phi(0) == float("inf")


def test_ar1_lags_argument_changes_only_the_width():
    """More lags widen the Newey-West band; the coefficient must not move.

    If `lags` leaked into the point estimate it would be a second, undocumented
    knob on the reported number, which is exactly the kind of thing that makes two
    runs of the same analysis disagree."""
    series = _ar1_series(0.20, 4000, seed=8)
    narrow = ar1(series, lags=1)
    wide = ar1(series, lags=20)
    assert narrow["phi"] == wide["phi"]
    assert wide["se"] >= narrow["se"]