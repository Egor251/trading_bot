"""Journal expectancy + walk-forward skeleton."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.research import (  # noqa: E402
    aggregate_journal,
    expectancy_metrics,
    run_walk_forward_offline,
    walk_forward_windows,
)
from engine.strategy_fixtures import rising_trend  # noqa: E402
from strategies.ma_cross_example.plugin import MaCrossExamplePlugin  # noqa: E402


def test_expectancy_metrics_basic() -> None:
    m = expectancy_metrics([1.0, -0.5, 1.0, -0.5])
    assert m["n"] == 4
    assert m["wins"] == 2
    assert m["losses"] == 2
    assert abs(m["expectancy"] - 0.25) < 1e-9
    assert abs(m["win_rate"] - 0.5) < 1e-9
    assert abs(m["profit_factor"] - 2.0) < 1e-9


def test_walk_forward_windows() -> None:
    wins = walk_forward_windows(100, train_bars=40, test_bars=10, step=10)
    assert wins
    assert wins[0].train_start == 0
    assert wins[0].train_end == 40
    assert wins[0].test_start == 40
    assert wins[0].test_end == 50
    assert wins[1].train_start == 10
    assert all(w.test_end <= 100 for w in wins)


def test_aggregate_journal_attribution_and_regime() -> None:
    rows = [
        {
            "event_type": "market.regime",
            "timestamp": 1,
            "payload": {"regime": "strong_trend"},
        },
        {
            "event_type": "execution.result",
            "timestamp": 2,
            "payload": {
                "success": True,
                "action": "BUY",
                "quantity": 10,
                "price": 100,
                "contributors": [
                    {"strategy_id": "a"},
                    {"strategy_id": "b"},
                ],
            },
        },
        {
            "event_type": "market.regime",
            "timestamp": 3,
            "payload": {"regime": "high_vol"},
        },
        {
            "event_type": "execution.result",
            "timestamp": 4,
            "payload": {
                "success": True,
                "order": {
                    "action": "SELL",
                    "quantity": 10,
                    "price": 100,
                    "strategy_id": "a",
                },
            },
        },
        {
            "event_type": "ensemble.reject",
            "timestamp": 5,
            "payload": {"reason": "zero_weight", "strategy_id": "b"},
        },
    ]
    stats = aggregate_journal(rows)
    assert stats["counts"]["execution.result"] == 2
    assert "a" in stats["by_strategy"]
    assert "b" in stats["by_strategy"]
    # first fill split a/b; second fill only a
    assert stats["by_strategy"]["a"]["n"] == 2
    assert stats["by_strategy"]["b"]["n"] == 1
    assert "a|strong_trend" in stats["by_strategy_regime"]
    assert "a|high_vol" in stats["by_strategy_regime"]
    assert stats["rejects"].get("b") == 1
    assert stats["overall"]["n"] == 3


def test_walk_forward_offline_ma() -> None:
    core = MagicMock()
    plugin = MaCrossExamplePlugin(core, "ma_cross_example", MagicMock())
    plugin.config = {}
    series = rising_trend(n=120)
    from engine.walk_forward import WalkForwardConfig

    out = run_walk_forward_offline(
        plugin,
        series,
        [{"fast": 3, "slow": 8}, {"fast": 5, "slow": 15}],
        train_bars=40,
        test_bars=15,
        step=15,
        config=WalkForwardConfig(
            min_folds=5,
            selection_mode="best",
            bootstrap_samples=20,
            persist_report=False,
        ),
    )
    assert out["folds"]
    assert out["oos"]["folds"] == len(out["folds"])
    assert out["bars"] == 120
    assert "provenance" in out
    for fold in out["folds"]:
        assert "best_config" in fold
        assert "oos_pnl" in fold


def test_get_strategy_stats_tool() -> None:
    async def _run() -> None:
        from engine.ai_tools import GetStrategyStatsTool

        core = MagicMock()
        stor = MagicMock()

        async def _stats(**kwargs):
            return {"overall": {"n": 1, "expectancy": 0.1}, "by_strategy": {"a": {}}}

        stor.strategy_stats = _stats
        core.service_registry.get = MagicMock(return_value=stor)
        tool = GetStrategyStatsTool(core=core)
        res = await tool.execute(limit=100)
        assert res["success"] is True
        assert res["stats"]["overall"]["n"] == 1

    asyncio.run(_run())


if __name__ == "__main__":
    test_expectancy_metrics_basic()
    test_walk_forward_windows()
    test_aggregate_journal_attribution_and_regime()
    test_walk_forward_offline_ma()
    test_get_strategy_stats_tool()
    print("research OK")
