"""The null has to look like the market it is supposed to be a null for.

Every figure this tool produces is calibrated against a null of i.i.d. noise, which
fixes the null's autocorrelation at exactly zero. The real series does not: rho_1 is
-0.044 on BTC at 4h and -0.049 at 1d. So the comparison the research log describes
was never the comparison that was run, and in the one direction that matters — the
null was slightly *easy* to beat.

Two things have to be true of the injection, and they fail independently:

  - the dependence must actually enter the series, in the null, the market factor
    and the strategies alike;
  - the marginal variance must not move. Feeding the same innovation sd as the
    i.i.d. case would raise total variance alongside autocorrelation, and the
    recalibration would measure "more variance" and "more dependence" at once
    without being able to separate them.

The first is easy to check. The second is the one that produces a confident and
meaningless number, so it is pinned directly rather than inferred.
"""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

import math  # noqa: E402
import random  # noqa: E402
from statistics import variance  # noqa: E402

import pytest  # noqa: E402

from tools.null_calibration import (  # noqa: E402
    MEASURED_PHI,
    PER_WINDOW,
    SIGMA,
    _ar1_draw,
    measured_phi_table,
)


def _series(phi: float, n: int = 60000, seed: int = 1) -> list[float]:
    return _ar1_draw(random.Random(seed), n, phi, SIGMA)


def test_phi_zero_is_the_iid_case():
    series = _series(0.0)
    assert len(series) == 60000
    assert math.sqrt(variance(series)) == pytest.approx(SIGMA, rel=0.05)


def test_dependence_actually_enters_the_series():
    """A zero phi would make the whole recalibration a no-op that still reports."""
    series = _series(-0.044)
    rho1 = sum(a * b for a, b in zip(series[:-1], series[1:])) / (
        len(series) - 1
    ) / variance(series)
    assert rho1 == pytest.approx(-0.044, abs=0.01)


def test_marginal_variance_is_held_fixed():
    """The confounder, pinned directly.

    Without the `sqrt(1 - phi^2)` scaling the series gets both more dependent and
    more volatile, and any change in the false-positive rate cannot be attributed.
    """
    base = math.sqrt(variance(_series(0.0)))
    for phi in (-0.044, -0.049, -0.068, 0.050, -0.5, 0.5):
        sigma = math.sqrt(variance(_series(phi)))
        assert sigma == pytest.approx(base, rel=0.05), f"variance moved at phi={phi}"


def test_negative_and_positive_dependence_both_enter():
    for phi in (-0.3, 0.3):
        series = _series(phi)
        rho1 = sum(a * b for a, b in zip(series[:-1], series[1:])) / (
            len(series) - 1
        ) / variance(series)
        assert rho1 == pytest.approx(phi, abs=0.02)


def test_absurd_phi_is_bounded_rather_than_blowing_up():
    """`sqrt(1 - phi^2)` is imaginary past |phi| = 1 and a NaN propagates silently."""
    for phi in (-5.0, 5.0):
        series = _series(phi)
        assert all(math.isfinite(v) for v in series)


def test_empty_and_single_draws():
    assert _ar1_draw(random.Random(1), 0, -0.044, SIGMA) == []
    assert len(_ar1_draw(random.Random(1), 1, -0.044, SIGMA)) == 1


def test_measured_values_are_the_ones_actually_measured():
    """Registered before running; changing these silently is criterion-shopping.

    -0.044 and -0.049 are BTC rho_1 at 4h and 1d from EDGE_ROLL. -0.068 is the
    earlier AR(1) reading on a longer 1d window and is carried deliberately as the
    adverse case, because the two 1d estimates disagree and running only the
    flattering one would not be a recalibration.
    """
    assert 0.0 in MEASURED_PHI
    assert -0.044 in MEASURED_PHI
    assert -0.049 in MEASURED_PHI
    assert -0.068 in MEASURED_PHI
    # Positive control: nobody claims positive autocorrelation, which is precisely
    # why it shows whether the function keys on dependence or on the sign.
    assert 0.050 in MEASURED_PHI


def test_table_reports_a_standard_error_and_a_delta():
    """Without the SE a shift of two percentage points reads as a finding."""
    rows = measured_phi_table(trials=40)
    assert len(rows) == len(MEASURED_PHI)
    for null_phi, rate, se, delta in rows:
        assert 0.0 <= rate <= 100.0
        assert 0.0 < se < 5.0
        if null_phi == 0.0:
            assert delta == pytest.approx(0.0)
        else:
            assert rate - rows[0][1] == pytest.approx(delta)


def test_the_base_row_is_the_iid_null():
    rows = measured_phi_table(trials=40)
    assert rows[0][0] == 0.0
    assert rows[0][3] == pytest.approx(0.0)


def test_the_table_is_deterministic():
    assert measured_phi_table(trials=20) == measured_phi_table(trials=20)


def test_per_window_is_the_unit_the_injection_is_applied_over():
    """The AR(1) burn-in must not consume the window the test actually measures."""
    assert PER_WINDOW > 60