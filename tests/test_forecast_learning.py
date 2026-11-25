"""Known-answer pins for fast price-forecast learning."""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.forecast_learning import ForecastConfig, ForecastLearner  # noqa: E402


class _Opt:
    calls = 0

    def run(self, X, y, tr, va, te, seed):
        ForecastLearner._Opt_calls = getattr(ForecastLearner, "_Opt_calls", 0) + 1
        return {"pred": 0.001, "loss": 0.01, "baseline": 0.02}


def _learner(**kw):
    return ForecastLearner(ForecastConfig(**kw))


def test_forecast_resolves_only_after_the_horizon():
    l = _learner(horizon_sec=10.0, lags=2)
    for i in range(12):
        l.observe(float(i), 100.0 * (1 + 0.001 * i), 2.0, 0.1)
    assert len(l.targets) > 0
    assert len(l._pending) > 0
    # target is the realised forward return at the horizon
    assert l.targets[0] > 0


def test_no_decision_before_the_observation_floor():
    l = _learner(horizon_sec=1.0, min_observations=50, update_every=10, lags=2)
    for i in range(30):
        l.observe(float(i), 100.0 + i, 2.0, 0.1)
        l.observe(float(i) + 0.5, 101.0 + i, 2.0, 0.1)
    assert l.step(_Opt()) is None
    assert l.decisions == []


def test_duplicate_tick_is_ignored():
    l = _learner(horizon_sec=5.0, lags=2)
    l.observe(1.0, 100.0, 2.0, 0.1)
    l.observe(1.0, 999.0, 2.0, 0.1)
    assert l.snapshot()["observations"] == 0
    assert l._mids[-1] == 100.0


def test_absent_book_is_flagged_not_invented():
    l = _learner(horizon_sec=1.0, lags=2)
    for i in range(6):
        l.observe(float(i), 100.0 + i, None, None)
    l.observe(10.0, 110.0, None, None)
    assert l.features and l.features[0][-1] == 0.0
    assert l.features[0][-2] == 0.0
