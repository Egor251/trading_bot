"""Known-answer pins for the market-neutral timing statistic."""

from __future__ import annotations

import random

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from tools.timing_skill_verdict import _held, _pair_skill  # noqa: E402
from engine.strategy_plugin import StrategyDecision  # noqa: E402


def test_demeaning_removes_pure_drift():
    """Holding through a uniformly rising market must not score as skill."""
    rets = [0.01] * 200
    decisions = [StrategyDecision(action="BUY", strength=1.0)] + [
        StrategyDecision(action="HOLD", strength=1.0, intent="long")
    ] * 199
    skill, _ = _pair_skill(decisions, rets, sum(rets) / len(rets), 7)
    assert skill <= 0.0  # only the entry cost remains


def test_shuffled_returns_change_the_score():
    """A shuffled control must not silently equal REAL (in-place shuffle bug)."""
    rng = random.Random(3)
    rets = [rng.gauss(0.002, 0.01) for _ in range(200)]
    decisions = [
        StrategyDecision(
            action="BUY" if i % 20 == 0 else "SELL" if i % 20 == 10 else "HOLD",
            strength=1.0,
        )
        for i in range(200)
    ]
    mu = sum(rets) / len(rets)
    real, _ = _pair_skill(decisions, rets, mu, 7)
    tail = rets[1:].copy()
    random.Random(9).shuffle(tail)
    shuffled = [0.0] + tail
    other, _ = _pair_skill(decisions, shuffled, sum(shuffled) / len(shuffled), 7)
    assert abs(real - other) > 1e-9


def test_held_tracks_state_machine():
    decisions = [
        StrategyDecision(action="BUY"),
        StrategyDecision(action="HOLD", intent="long"),
        StrategyDecision(action="SELL"),
        StrategyDecision(action="HOLD"),
    ]
    assert _held(decisions, 4) == [True, True, False, False]
