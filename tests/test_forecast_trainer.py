"""Known-answer checks for the distance-loss forecast trainer."""

from __future__ import annotations

import numpy as np

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from tools.forecast_trainer import (  # noqa: E402
    fit_regressor, gradient_check, loss, predict, run_forecast_evolution,
)
from tools.neuroevolution_count import enumerate_architectures  # noqa: E402


def test_gradient_matches_finite_differences():
    assert gradient_check() < 1e-5


def test_linear_price_path_is_learnable():
    rng = np.random.default_rng(7)
    X = rng.normal(size=(400, 3))
    y = 0.01 + 0.02 * X[:, 0] - 0.01 * X[:, 1]
    arch = enumerate_architectures()[0]
    untrained = fit_regressor(X[:280], y[:280], arch, seed=7, epochs=1, lr=0.0)
    before = loss(predict(untrained, X[:280]), y[:280])
    trained = fit_regressor(X[:280], y[:280], arch, seed=7, epochs=500)
    after = loss(predict(trained, X[:280]), y[:280])
    assert after < before


def test_downside_error_costs_more_than_upside():
    pred = np.array([0.0])
    assert loss(pred, np.array([-0.01])) > loss(pred, np.array([0.01]))


def test_evolution_reports_both_counts():
    rng = np.random.default_rng(11)
    X = rng.normal(size=(200, 3))
    y = 0.02 * X[:, 0]
    out = run_forecast_evolution(
        X, y, np.arange(140), np.arange(140, 200),
        archs=enumerate_architectures()[:6], pop=6, gens=2,
    )
    assert out["evaluations"] >= len(out["distinct"])
    assert len(out["distinct"]) <= 6
