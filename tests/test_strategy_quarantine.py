"""A degraded strategy must stop trading, and a quiet one must not be punished.

`PerformanceMonitor` already classifies `parameter_drift` and
`structural_break`, but its only effect was soft: `risk_scale` 0.5 and a 0.25
weight multiplier. A member at 0.25 weight is still a member and keeps trading
indefinitely, and the diagnosis is a portfolio judgement that cannot reliably
name which strategy degraded.

These tests pin the properties that make the breaker safe to enable, which are
mostly about what it must *not* do:

* two losing trades are noise, not a structural break
* an absent outcome is not a failure -- silence is not degradation
* a borderline member must not flap in and out on every evaluation
* a member quarantined on bad evidence must be restorable on good evidence
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.strategy_quarantine import (  # noqa: E402
    QuarantinePolicy,
    StrategyQuarantine,
)


def _policy(**kw) -> QuarantinePolicy:
    base = {
        "quarantine_below": 0.0,
        "restore_above": 0.05,
        "consecutive_failures": 3,
        "min_samples": 10,
    }
    base.update(kw)
    return QuarantinePolicy(**base)


def _evaluate(brk: StrategyQuarantine, data: dict[str, tuple[float, int]], ids=None):
    return brk.evaluate(ids or sorted(data), data)


def test_a_degraded_strategy_is_quarantined_after_enough_failures() -> None:
    brk = StrategyQuarantine(_policy())
    data = {"good": (1.0, 50), "bad": (-2.0, 50)}

    first = _evaluate(brk, data)
    assert not brk.is_quarantined("bad"), "one failing evaluation must not act"
    assert any(v.strategy_id == "bad" and "1/3" in v.reason for v in first)

    _evaluate(brk, data)
    assert not brk.is_quarantined("bad"), "two must not act either"

    third = _evaluate(brk, data)
    assert brk.is_quarantined("bad"), f"third failure must act: {[v.to_dict() for v in third]}"
    assert not brk.is_quarantined("good")


def test_a_thin_sample_is_never_judged() -> None:
    """Two losses are noise. Quarantifying here would be a coin flip."""
    brk = StrategyQuarantine(_policy(min_samples=20))
    data = {"newcomer": (-5.0, 2)}
    for _ in range(10):
        _evaluate(brk, data)
    assert not brk.is_quarantined("newcomer")


def test_absent_outcomes_fail_open() -> None:
    """A strategy the outcome feed knows nothing about must not be demoted.

    Silence is not degradation. If the feed breaks, every strategy looks silent,
    and a breaker that treated that as failure would take out the whole lineup
    at precisely the moment the system can least explain itself.
    """
    brk = StrategyQuarantine(_policy())
    for _ in range(5):
        verdicts = brk.evaluate(["ghost"], {})
    assert not brk.is_quarantined("ghost")
    assert verdicts[0].reason == "insufficient_samples"
    assert verdicts[0].mean_return is None


def test_silence_does_not_clear_an_existing_quarantine() -> None:
    """Losing the evidence feed is not grounds for re-admitting a member."""
    brk = StrategyQuarantine(_policy())
    for _ in range(3):
        brk.evaluate(["x"], {"x": (-1.0, 99)})
    assert brk.is_quarantined("x")

    verdicts = brk.evaluate(["x"], {})
    assert brk.is_quarantined("x"), "quarantine was lifted by missing data"
    assert verdicts[0].reason == "insufficient_samples"


def test_hysteresis_stops_flapping() -> None:
    """A member sitting on the line must not toggle every evaluation."""
    brk = StrategyQuarantine(_policy(quarantine_below=0.0, restore_above=0.05))
    for _ in range(3):
        _evaluate(brk, {"edge": (-0.01, 50)})
    assert brk.is_quarantined("edge")

    # Recovering to merely acceptable is not recovery: still quarantined.
    _evaluate(brk, {"edge": (0.01, 50)})
    assert brk.is_quarantined("edge"), "0.01 is below restore_above=0.05"

    _evaluate(brk, {"edge": (0.06, 50)})
    assert not brk.is_quarantined("edge"), "cleared restore_above"


def test_a_recovered_member_can_trade_again() -> None:
    brk = StrategyQuarantine(_policy())
    for _ in range(3):
        _evaluate(brk, {"x": (-1.0, 40)})
    assert brk.is_quarantined("x")
    verdicts = _evaluate(brk, {"x": (2.0, 40)})
    assert not brk.is_quarantined("x")
    assert verdicts[0].reason == "recovered"


def test_a_policy_that_cannot_restore_is_rejected() -> None:
    """restore_above <= quarantine_below is a permanent lockout, not a safety."""
    for bad in (
        {"quarantine_below": 0.0, "restore_above": 0.0},
        {"quarantine_below": 0.1, "restore_above": -1.0},
    ):
        try:
            _policy(**bad).validated()
        except ValueError:
            continue
        raise AssertionError(f"accepted an un-restorable policy: {bad}")


def test_a_disabled_breaker_demotes_nothing() -> None:
    brk = StrategyQuarantine(_policy(enabled=False))
    for _ in range(5):
        _evaluate(brk, {"bad": (-9.0, 99)})
    assert not brk.is_quarantined("bad")


def test_state_starts_clean_after_restart() -> None:
    """Evidence that outlived its process must not keep a member quarantined."""
    brk = StrategyQuarantine(_policy())
    for _ in range(3):
        _evaluate(brk, {"x": (-1.0, 40)})
    assert brk.is_quarantined("x")

    restarted = StrategyQuarantine(_policy())
    assert not restarted.is_quarantined("x"), "quarantine must not survive a restart"


def test_the_breaker_reads_the_real_stats_object() -> None:
    """It must work against StrategyStats, not only a dict."""
    from engine.ensemble import StrategyStats

    stats = StrategyStats()
    for _ in range(30):
        stats.record_outcome("loser", -1.0)
        stats.record_outcome("winner", 1.0)
    assert stats.count("loser") == 30

    brk = StrategyQuarantine(_policy())
    for _ in range(3):
        brk.evaluate(["loser", "winner"], stats)
    assert brk.is_quarantined("loser")
    assert not brk.is_quarantined("winner")