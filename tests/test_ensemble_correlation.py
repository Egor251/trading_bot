"""Five strategies at rho=0.9 is one bet, and the system said five.

Correlation dampening existed and worked -- and was skipped in `equal` and
`manual` modes. `equal` is the shipped default (`weight_mode: equal`), so in the
configuration operators actually run, pairwise correlation was never consulted.
A lineup of highly correlated strategies each traded at full size, which is one
risk wearing five names.

The other half is visibility. Even where dampening is correctly skipped -- an
operator who set `manual` weights stated them deliberately -- the correlation
still has to be reported, because "diversified across five strategies" is a
claim the system should not quietly contradict.

These tests pin both: that the default mode now consults correlation, and that
the effective number of independent bets is published so the illusion cannot
survive as a roster size.
"""

from __future__ import annotations

import asyncio
import random
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.ensemble import EnsembleManager, StrategyStats  # noqa: E402
from services.ensemble_service import EnsembleService  # noqa: E402


def test_the_ui_read_model_reports_the_illusion() -> None:
    """Enforced in the engine is not the same as visible to the operator.

    This repository's recurring defect is a number computed and then read by
    nobody: stop coverage, the fee reconciler, the liquidity gate. Publishing the
    correlation into the status payload is what stops the effective-bet count
    from becoming the fourth one.
    """

    async def _run() -> None:
        core = MagicMock()

        async def _cfg_get(path, default=None):
            if path == "trading":
                return {
                    "ensemble": {
                        "enabled": True,
                        "strategies": ["a", "b"],
                        "weight_mode": "equal",
                    }
                }
            if path == "instance.id":
                return "test"
            return default

        core.config.get = _cfg_get
        core.event_bus = MagicMock()
        core.event_bus.publish = AsyncMock()
        core.event_bus.subscribe = AsyncMock()
        core.event_bus.unsubscribe = AsyncMock()
        core.plugin_manager._instances = {}
        core.rule_engine = None

        svc = EnsembleService(core, "ensemble_service")
        svc.config = svc.default_config()
        svc.config["rebalance_sec"] = 0
        await svc.on_initialize()
        svc.manager.stats = _correlated_stats()

        status = await svc.get_status()
        assert "effective_independent_bets" in status, (
            "the independent-bet count never reached the operator"
        )
        assert "correlation" in status, "the matrix itself was not published"
        assert status["effective_independent_bets"] < 3.0

    asyncio.run(_run())


def _correlated_stats(seed: int = 3, steps: int = 80) -> StrategyStats:
    """Three strategies driven by one factor, plus one genuinely independent."""
    stats = StrategyStats()
    rng = random.Random(seed)
    for _ in range(steps):
        factor = rng.gauss(0.0, 1.0)
        stats.record_outcome("a", factor)
        stats.record_outcome("b", factor * 0.98 + rng.gauss(0.0, 0.05))
        stats.record_outcome("loner", rng.gauss(0.0, 1.0))
    return stats


def test_correlation_is_computed_from_realised_returns() -> None:
    stats = _correlated_stats()
    assert stats.correlation("a", "b") > 0.9, "a driven pair must be correlated"
    assert abs(stats.correlation("a", "loner")) < 0.4, (
        "an unrelated strategy must not look correlated"
    )


def test_too_few_observations_gives_no_answer() -> None:
    """A correlation from two points is arithmetic, not evidence."""
    stats = StrategyStats()
    stats.record_outcome("a", 1.0)
    stats.record_outcome("b", 2.0)
    assert stats.correlation("a", "b") is None


def test_equal_mode_no_longer_ignores_correlation() -> None:
    """The default configuration is where the illusion was unguarded."""
    manager = EnsembleManager()
    manager.stats = _correlated_stats()
    weights = manager.rebalance("equal", ["a", "b", "loner"], corr_cap=0.5)

    assert weights["a"] != weights["b"], (
        "a perfectly correlated pair was given identical size in equal mode"
    )
    assert weights["b"] < weights["a"], "the weaker arm should be cut"


def test_manual_weights_are_left_alone() -> None:
    """An operator who stated weights deliberately gets them honoured."""
    manager = EnsembleManager()
    manager.stats = _correlated_stats()
    weights = manager.rebalance(
        "manual",
        ["a", "b", "loner"],
        manual_weights={"a": 1.0, "b": 1.0, "loner": 1.0},
        corr_cap=0.5,
    )
    assert abs(weights["a"] - weights["b"]) < 1e-9, "manual weights were overridden"


def test_manual_mode_still_reports_the_correlation() -> None:
    """Exempt from enforcement is not exempt from telling the truth."""
    manager = EnsembleManager()
    manager.stats = _correlated_stats()
    manager.rebalance(
        "manual",
        ["a", "b", "loner"],
        manual_weights={"a": 1.0, "b": 1.0, "loner": 1.0},
        corr_cap=0.5,
    )
    assert manager._last_correlation["a"]["b"] > 0.9, (  # noqa: SLF001
        "the operator must still be able to see the pair is one bet"
    )


def test_a_correlated_lineup_reports_fewer_independent_bets_than_members() -> None:
    """The number an operator sizing a book needs, which a roster hides."""
    manager = EnsembleManager()
    manager.stats = _correlated_stats()
    manager.rebalance("equal", ["a", "b", "loner"], corr_cap=0.5)
    effective = manager.effective_independent_bets()
    assert effective < 3.0, f"three correlated members reported as {effective}"
    assert effective > 1.0, "they are not perfectly identical either"


def test_an_independent_lineup_reports_nearly_all_of_its_bets() -> None:
    stats = StrategyStats()
    rng = random.Random(11)
    for _ in range(120):
        stats.record_outcome("a", rng.gauss(0, 1))
        stats.record_outcome("b", rng.gauss(0, 1))
        stats.record_outcome("c", rng.gauss(0, 1))
    manager = EnsembleManager()
    manager.stats = stats
    manager.rebalance("equal", ["a", "b", "c"], corr_cap=0.85)
    assert manager.effective_independent_bets() > 2.5


def test_an_empty_lineup_does_not_divide_by_zero() -> None:
    manager = EnsembleManager()
    assert manager.effective_independent_bets() == 0.0
    single = StrategyStats()
    single.record_outcome("only", 1.0)
    manager.stats = single
    assert manager.effective_independent_bets() == 1.0