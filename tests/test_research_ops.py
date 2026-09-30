"""Tests for walk-forward digests, holdout, apply-chosen helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.research_digest import (  # noqa: E402
    digest_walk_forward,
    enrich_fold_aggregates,
    rank_batch_rows,
)
from engine.strategy_fixtures import rising_trend  # noqa: E402
from engine.walk_forward import WalkForwardConfig, run_walk_forward_offline  # noqa: E402
from host.bootstrap import ensure_code_base_path  # noqa: E402
from strategies.bollinger_reversion.plugin import BollingerReversionPlugin  # noqa: E402
from strategies.ma_cross_example.plugin import MaCrossExamplePlugin  # noqa: E402

ensure_code_base_path()


def test_enrich_and_digest() -> None:
    report = {
        "folds": [
            {"oos_pnl": 1.0, "oos_trades": 2, "oos_max_drawdown": 0.1},
            {"oos_pnl": -0.5, "oos_trades": 1, "oos_max_drawdown": 0.2},
        ],
        "oos": {
            "mean_oos_pnl": 0.25,
            "win_rate": 0.5,
            "profit_factor": 2.0,
            "folds": 2,
            "ci_low": -1.0,
            "ci_high": 1.5,
        },
        "chosen": {"window": 10, "num_std": 2.5},
        "provenance": {
            "strategy_id": "bollinger_reversion",
            "ticker": "BTCUSDT",
            "timeframe": "1d",
            "variant_mode": "optimize",
            "variants_count": 20,
            "bar_interval_sec": 86400,
        },
        "baselines": {"edge_vs_buy_hold": 1.0, "cost_drag": 0.1},
        "holdout": {"bars": 40, "pnl": 0.5, "trades": 3, "max_drawdown": 0.05},
    }
    enrich_fold_aggregates(report)
    assert report["oos"]["total_oos_trades"] == 3
    assert abs(report["oos"]["mean_oos_max_drawdown"] - 0.15) < 1e-9
    dig = digest_walk_forward(report)
    assert dig["ok"] is True
    assert dig["mean_oos_pnl"] == 0.25
    assert dig["ci_crosses_zero"] is True
    assert dig["holdout_pnl"] == 0.5
    assert dig["robust_sign_match"] is True
    assert dig["chosen"]["window"] == 10


def test_rank_batch_rows() -> None:
    rows = rank_batch_rows(
        [
            {"strategy_id": "a", "mean_oos_pnl": -1},
            {"strategy_id": "b", "mean_oos_pnl": 2},
        ]
    )
    assert rows[0]["strategy_id"] == "b"
    assert rows[0]["rank"] == 1


def test_walk_forward_digest_fields_present() -> None:
    core = MagicMock()
    plugin = MaCrossExamplePlugin(core, "ma_cross_example", MagicMock())
    plugin.config = {}
    series = rising_trend(n=200)
    cfg = WalkForwardConfig(
        train_bars=40,
        test_bars=15,
        step=15,
        min_folds=5,
        bootstrap_samples=20,
        persist_report=False,
        seed=3,
    )
    out = run_walk_forward_offline(
        plugin,
        series,
        [{"fast": 3, "slow": 8}, {"fast": 5, "slow": 15}],
        config=cfg,
        provenance_extra={"strategy_id": "ma_cross_example", "ticker": "X"},
    )
    assert "total_oos_trades" in out["oos"]
    assert "mean_oos_max_drawdown" in out["oos"]
    assert "win_rate" in out["oos"]
    assert "profit_factor" in out["oos"]


def test_apply_chosen_filters_schema() -> None:
    from services.phantom_service import PhantomService

    core = MagicMock()
    plugin = BollingerReversionPlugin(core, "bollinger_reversion", MagicMock())
    plugin.config = {"window": 20, "num_std": 2.0}
    core.plugin_manager.get = MagicMock(return_value=plugin)
    raw = {"plugins": {"bollinger_reversion": {"window": 20}}}
    core.config.raw = raw
    core.config.save = MagicMock()
    svc = PhantomService(core, "phantom_service")
    svc._last_walk_forward = {  # noqa: SLF001
        "strategy_id": "bollinger_reversion",
        "ticker": "BTCUSDT",
        "chosen": {"window": 10, "num_std": 2.5, "quantity": 99, "junk": 1},
    }
    out = svc.apply_chosen_params(persist=False)
    assert out["ok"] is True
    assert out["params"]["window"] == 10
    assert out["params"]["num_std"] == 2.5
    assert out["params"]["tickers"] == ["BTC/USDT"]
    assert plugin.config["window"] == 10
    assert "junk" not in out["params"]
    assert out["source"] == "last_walk_forward"


def test_apply_chosen_prefers_batch_top_over_last_wf() -> None:
    from services.phantom_service import PhantomService

    core = MagicMock()
    plugin = BollingerReversionPlugin(core, "bollinger_reversion", MagicMock())
    plugin.config = {"window": 20, "num_std": 2.0}
    core.plugin_manager.get = MagicMock(return_value=plugin)
    core.config.raw = {"plugins": {"bollinger_reversion": {"window": 20}}}
    svc = PhantomService(core, "phantom_service")
    svc._last_walk_forward = {  # noqa: SLF001
        "strategy_id": "bollinger_reversion",
        "ticker": "ETHUSDT",
        "chosen": {"window": 99, "num_std": 9.0},
    }
    svc._last_batch = [  # noqa: SLF001
        {
            "ok": True,
            "rank": 1,
            "strategy_id": "bollinger_reversion",
            "ticker": "BTCUSDT",
            "chosen": {"window": 14, "num_std": 1.5},
            "mean_oos_pnl": 10.0,
        },
        {
            "ok": True,
            "rank": 2,
            "strategy_id": "bollinger_reversion",
            "ticker": "ETHUSDT",
            "chosen": {"window": 30, "num_std": 3.0},
            "mean_oos_pnl": 1.0,
        },
    ]
    out = svc.apply_chosen_params(persist=False, from_last=True)
    assert out["ok"] is True
    assert out["source"] == "last_batch_top"
    assert out["params"]["window"] == 14
    assert out["params"]["num_std"] == 1.5
    assert out["params"]["tickers"] == ["BTC/USDT"]
    assert plugin.config["window"] == 14


if __name__ == "__main__":
    test_enrich_and_digest()
    test_rank_batch_rows()
    test_walk_forward_digest_fields_present()
    test_apply_chosen_filters_schema()
    test_apply_chosen_prefers_batch_top_over_last_wf()
    print("research_ops OK")
