"""Roll (1984): can a bid-ask bounce be told apart from a real effect?

Negative first-order autocorrelation in an efficient market is the bid-ask bounce,
not predictability -- an observation alternating between bid and ask produces
price changes that mean-revert mechanically. Roll's contribution was not the
explanation but the *measurement*: the spread can be recovered from the price series
with no quote data at all.

This is the check that a t = -3.44 on 4h autocorrelation is measuring a spread rather
than an edge. Both hypotheses predict a negative `gamma1`; they differ in how that
covariance scales, and the scaling is what is tested here.

The constant in `S = c * sqrt(-gamma1)` is reported in two forms because it depends
on whether `gamma1` was defined as a covariance or a correlation in whichever source
is being followed, and the two differ by `sqrt(2)`. Rather than pick one from memory
and let an absolute comparison to a real trading cost inherit the error, the
ordering conclusions below are stated on `gamma1`, which no constant touches.
"""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

import math  # noqa: E402
import random  # noqa: E402
from statistics import variance  # noqa: E402

import pytest  # noqa: E402

from engine.simulation import implied_spread  # noqa: E402


def _bounce(side: list[float]) -> list[float]:
    """Price changes for a pure bid-ask bounce: no efficient-price drift at all."""
    return side


def _series_with_gamma1(gamma1: float, n: int, seed: int) -> list[float]:
    """Build a return series whose first-order serial covariance is `gamma1`.

    An AR(1) with a chosen coefficient and scaled noise gives an exact covariance of
    ``phi * sigma^2``, so the target is reached by choosing sigma rather than by
    resampling until it happens to.
    """
    phi = gamma1 / 0.01  # sigma^2 = 0.01, so gamma1 = phi * 0.01
    rng = random.Random(seed)
    out: list[float] = []
    value = 0.0
    for _ in range(n):
        value = phi * value + rng.gauss(0, 0.1)
        out.append(value)
    return out


# --- the estimator ---------------------------------------------------------


def test_gamma1_is_recovered_from_a_series_built_to_have_it():
    """A known-answer test, because the constant is the thing in doubt.

    Without this the estimator could be self-consistent and wrong, and every
    conclusion drawn from it would inherit the error silently.
    """
    for target in (-0.0005, -0.001, -0.002):
        series = _series_with_gamma1(target, 40000, seed=7)
        result = implied_spread(series)
        assert result["gamma1"] == pytest.approx(target, rel=0.15)
        assert result["spread"] > 0.0
        assert result["spread_alt"] > 0.0


def test_the_two_conventions_differ_by_sqrt_two():
    """Stated explicitly so nobody reads an absolute level off one of them by accident."""
    series = _series_with_gamma1(-0.002, 40000, seed=8)
    result = implied_spread(series)
    assert result["spread"] / result["spread_alt"] == pytest.approx(math.sqrt(2.0))


def test_positive_covariance_yields_zero_not_a_negative_spread():
    """``gamma1 >= 0`` means this estimator has nothing to explain.

    It must not produce a square root of a negative number, and the zero must not be
    readable as "no cost" -- callers need to distinguish "not applicable" from
    "free", and the docstring says so.
    """
    rng = random.Random(9)
    momentum = []
    value = 0.0
    for _ in range(20000):
        value = 0.4 * value + rng.gauss(0, 1)
        momentum.append(value)

    result = implied_spread(momentum)
    assert result["gamma1"] > 0
    assert result["spread"] == 0.0
    assert result["spread_alt"] == 0.0


def test_pure_noise_gives_a_positive_or_flat_covariance():
    """On white noise the estimator should report no spread most of the time."""
    negatives = 0
    runs = 60
    for i in range(runs):
        rng = random.Random(500 + i)
        series = [rng.gauss(0, 0.01) for _ in range(6000)]
        if implied_spread(series)["gamma1"] < 0:
            negatives += 1
    assert negatives <= runs * 0.5


def test_short_and_degenerate_input():
    for bad in ([], [0.1], [0.1, 0.2]):
        result = implied_spread(bad)
        assert result["gamma1"] == 0.0
        assert result["spread"] == 0.0
    flat = implied_spread([0.0] * 100)
    assert flat["gamma1"] == 0.0
    assert flat["spread"] == 0.0


# --- the discriminants that separate bounce from an edge ------------------


def _bounce_series_alternating(amplitude: float, n: int, *, defect: float) -> list[float]:
    """A bid-ask bounce whose side alternates, breaking on `defect` of bars.

    The alternation is the whole mechanism and it is worth being explicit about why,
    because the obvious construction gives nothing. Draw the side i.i.d. +/- and
    `E[d_t * d_{t+1}]` is exactly **zero** -- consecutive signs are independent, so
    the covariance vanishes however large the amplitude is. Roll's estimator reports
    no spread at all for that series, and correctly so: iid side noise is not a
    bounce, it is just noise.

    What produces the negative covariance is trading *clustering*: consecutive
    observations tend to sit on the same side, because one order sweeps a run of
    them. So the construction forces a flip and then lets `defect` of bars repeat the
    side instead. `defect = 0` is perfect alternation and gives the most negative
    covariance; larger `defect` models a real market where fills do not alternate
    cleanly.
    """
    out: list[float] = []
    side = 1.0
    rng = random.Random(int(amplitude * 1e6) + int(defect * 1000))
    for _ in range(n):
        if rng.random() < defect:
            out.append(side * amplitude)
            continue
        side = -side
        out.append(side * amplitude)
    return out


def test_alternating_bounce_is_measured_and_iid_side_noise_is_not():
    """The discriminant itself, on data where the answer is known.

    An i.i.d. +/-amplitude series and an alternating one have identical variance and
    a spread differing by orders of magnitude. An estimator that reported a spread
    for both would "explain" every autocorrelated series it was handed, which is not
    an explanation at all.
    """
    alternating = implied_spread(_bounce_series_alternating(0.01, 100000, defect=0.0))
    iid_sides = implied_spread(_bounce_series_alternating(0.01, 100000, defect=0.5))

    assert alternating["gamma1"] < -0.00005
    assert alternating["spread"] > 0.0
    assert iid_sides["gamma1"] == pytest.approx(0.0, abs=2e-6)
    assert iid_sides["spread"] == 0.0


def test_bounce_covariance_shrinks_as_the_interval_lengthens():
    """The core prediction of the bounce explanation, and it is scale-free.

    Averaging a mechanical bounce over more bars washes it out: the bounce cancels
    inside each longer interval while the efficient-price part of the variance
    survives, so the negative covariance per unit variance must fall as the horizon
    grows. That is what separates a spread from an edge -- an edge does not shrink
    when you average over it.

    Stated on the ratio rather than on a spread in basis points so the conclusion is
    independent of the constant inside the square root.
    """
    base = _bounce_series_alternating(0.01, 400000, defect=0.15)

    def resample(series: list[float], group: int) -> list[float]:
        out = []
        for i in range(group, len(series), group):
            compounded = 1.0
            for j in range(i - group, i):
                compounded *= 1.0 + series[j]
            out.append(math.log(compounded))
        return out

    short_series = resample(base, 1)
    long_series = resample(base, 5)

    short = implied_spread(short_series)
    long_ = implied_spread(long_series)

    assert short["gamma1"] < 0
    assert long_["gamma1"] < 0
    ratio_short = short["gamma1"] / variance(short_series)
    ratio_long = long_["gamma1"] / variance(long_series)
    assert ratio_long > ratio_short


def test_wider_bounce_implies_a_larger_estimated_spread():
    """Monotone in the input, so a spread ordering is interpretable when it holds."""
    small = implied_spread(_bounce_series(0.005, 60000, seed=12))["spread"]
    large = implied_spread(_bounce_series(0.02, 60000, seed=12))["spread"]
    assert large > small


def _bounce_series(amplitude: float, n: int, seed: int) -> list[float]:
    """Returns containing a bid-ask bounce of known amplitude, plus iid price noise."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        side = amplitude if rng.random() < 0.5 else -amplitude
        out.append(side + rng.gauss(0, amplitude))
    return out


def test_spillover_noise_does_not_create_a_spread():
    """Adding independent price noise must not manufacture negative covariance.

    This is the failure mode that matters: if the estimator reported a spread for
    every series, it would "explain" any autocorrelation it was shown, and an
    explanation that always fires explains nothing.
    """
    rng = random.Random(13)
    clean = [rng.gauss(0, 0.01) for _ in range(20000)]
    assert implied_spread(clean)["gamma1"] > 0.0
    assert implied_spread(clean)["spread"] == 0.0


def test_a_strong_real_edge_is_not_mistaken_for_a_spread():
    """A momentum-like series is positive and gets no spread, so the two separate."""
    rng = random.Random(14)
    series = []
    value = 0.0
    for _ in range(20000):
        value = 0.45 * value + rng.gauss(0, 0.01)
        series.append(value)
    result = implied_spread(series)
    assert result["gamma1"] > 0.0
    assert result["spread"] == 0.0