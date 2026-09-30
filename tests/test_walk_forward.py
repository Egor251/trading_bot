"""Walk-forward config, stability selection, bootstrap, too-short errors."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.strategy_fixtures import rising_trend  # noqa: E402
from engine.walk_forward import (  # noqa: E402
    WalkForwardConfig,
    WalkForwardTooShortError,
    bars_needed_for_folds,
    bootstrap_mean_ci,
    config_fingerprint,
    resolve_walk_forward_bars,
    run_walk_forward_offline,
    walk_forward_bars_preflight,
    walk_forward_request_bars_needed,
)
from strategies.ma_cross_example.plugin import MaCrossExamplePlugin  # noqa: E402


def test_bars_needed_and_config_merge() -> None:
    assert bars_needed_for_folds(train_bars=60, test_bars=20, step=20, min_folds=5) == 160
    assert walk_forward_request_bars_needed(
        train_bars=60, test_bars=20, min_folds=5, holdout_bars=40
    ) == 200
    assert walk_forward_bars_preflight(60, train_bars=60, test_bars=20, min_folds=5) is not None
    assert walk_forward_bars_preflight(500, train_bars=60, test_bars=20, min_folds=5) is None
    short = walk_forward_bars_preflight(60, train_bars=60, test_bars=20, min_folds=5)
    assert short is not None
    assert short["bars_needed"] == 160
    assert "hint" in short
    bumped = resolve_walk_forward_bars(60, train_bars=60, test_bars=20, min_folds=5)
    assert bumped["bumped"] is True
    assert bumped["bars"] == 160
    assert bumped["bars_bumped_from"] == 60
    ok = resolve_walk_forward_bars(500, train_bars=60, test_bars=20, min_folds=5)
    assert ok["bumped"] is False
    assert ok["bars"] == 500
    base = WalkForwardConfig()
    assert base.feed == "history"
    assert base.min_folds == 5
    merged = base.merged({"feed": "patterns", "seed": 7, "limit": 200})
    assert merged.feed == "patterns"
    assert merged.seed == 7
    assert merged.bars == 200


def test_bootstrap_ci_bounds() -> None:
    boot = bootstrap_mean_ci([1.0, 2.0, 3.0, 4.0], samples=200, ci=0.9, seed=1)
    assert boot["ci_low"] <= boot["bootstrap_mean"] <= boot["ci_high"]
    assert boot["bootstrap_samples"] == 200


def test_walk_forward_reproducible_and_provenance() -> None:
    core = MagicMock()
    plugin = MaCrossExamplePlugin(core, "ma_cross_example", MagicMock())
    plugin.config = {}
    series = rising_trend(n=200)
    cfg = WalkForwardConfig(
        train_bars=40,
        test_bars=15,
        step=15,
        min_folds=5,
        selection_mode="top_k_stable",
        top_k=2,
        bootstrap_samples=50,
        persist_report=False,
        seed=11,
    )
    a = run_walk_forward_offline(
        plugin,
        series,
        [{"fast": 3, "slow": 8}, {"fast": 5, "slow": 15}],
        config=cfg,
        provenance_extra={
            "strategy_id": "ma_cross_example",
            "ticker": "X",
            "class_code": "SYNTH",
            "source": "history",
        },
    )
    b = run_walk_forward_offline(
        plugin,
        series,
        [{"fast": 3, "slow": 8}, {"fast": 5, "slow": 15}],
        config=cfg,
        provenance_extra={
            "strategy_id": "ma_cross_example",
            "ticker": "X",
            "class_code": "SYNTH",
            "source": "history",
        },
    )
    assert a["oos"]["folds"] >= 5
    assert a["provenance"]["seed"] == 11
    assert a["provenance"]["feed"] == "history"
    assert "fold_pnl_std" in a["oos"]
    assert "ci_low" in a["oos"]
    assert a["stable_family"] is not None
    assert a["oos"]["mean_oos_pnl"] == b["oos"]["mean_oos_pnl"]
    assert a["oos"]["ci_low"] == b["oos"]["ci_low"]
    assert config_fingerprint(a["chosen"]) == config_fingerprint(b["chosen"])


def test_walk_forward_too_short() -> None:
    core = MagicMock()
    plugin = MaCrossExamplePlugin(core, "ma_cross_example", MagicMock())
    plugin.config = {}
    series = rising_trend(n=80)
    cfg = WalkForwardConfig(
        train_bars=60,
        test_bars=20,
        step=20,
        min_folds=5,
        persist_report=False,
        auto_fit_folds=False,
    )
    try:
        run_walk_forward_offline(plugin, series, [{"fast": 3, "slow": 8}], config=cfg)
        raise AssertionError("expected WalkForwardTooShortError")
    except WalkForwardTooShortError as exc:
        assert exc.bars_needed >= 160
        assert "walk_forward_too_short" in str(exc)


def test_auto_fit_folds_allows_short_series() -> None:
    core = MagicMock()
    plugin = MaCrossExamplePlugin(core, "ma_cross_example", MagicMock())
    plugin.config = {}
    series = rising_trend(n=120)
    cfg = WalkForwardConfig(
        train_bars=40,
        test_bars=20,
        step=20,
        min_folds=5,
        auto_fit_folds=True,
        persist_report=False,
        bootstrap_samples=20,
    )
    out = run_walk_forward_offline(
        plugin, series, [{"fast": 3, "slow": 8}], config=cfg
    )
    assert out["oos"]["folds"] >= 5


def test_btc_quantity_one_vs_sized_fee() -> None:
    """qty=1 BTC @ 63k × 5 bps ≈ 31.5/fill; 10% equity ≈ 0.5/fill — not a 3% fee bug."""
    from engine.position_sizing import SizingConfig, SizingMode
    from engine.research import decisions_to_fills

    class _D:
        def __init__(self, action: str) -> None:
            self.action = action

    closes = [63_000.0, 63_100.0]
    decisions = [_D("BUY"), _D("SELL")]
    fat = decisions_to_fills(decisions, closes, quantity=1.0, fee_bps=5.0)
    assert abs(fat[0]["cost"] - 31.5) < 1e-9

    sizing = SizingConfig(mode=SizingMode.PERCENT_OF_EQUITY, value=10.0)
    thin = decisions_to_fills(
        decisions,
        closes,
        quantity=None,
        fee_bps=5.0,
        sizing=sizing,
        start_equity=10_000.0,
    )
    expected_qty = 10_000.0 * 0.10 / 63_000.0
    assert abs(thin[0]["quantity"] - expected_qty) < 1e-12
    assert abs(thin[0]["cost"] - 0.5) < 1e-9


def test_oos_carry_side_hold_pnl() -> None:
    """Always-in-market with no OOS flip must still earn hold PnL via side carry."""
    from unittest.mock import MagicMock

    from engine.position_sizing import SizingConfig, SizingMode
    from engine.research import offline_variant_metrics, slice_series, walk_forward_windows
    from engine.strategy_fixtures import rising_trend
    from strategies.ma_cross_example.plugin import MaCrossExamplePlugin

    core = MagicMock()
    plugin = MaCrossExamplePlugin(core, "ma_cross_example", MagicMock())
    plugin.config = {}
    series = rising_trend(n=200, start=100.0, step=0.5)
    win = list(walk_forward_windows(200, train_bars=60, test_bars=20, step=20))[5]
    combo = slice_series(series, win.train_start, win.test_end)
    train_len = win.train_end - win.train_start
    sizing = SizingConfig(mode=SizingMode.PERCENT_OF_EQUITY, value=10.0)
    m = offline_variant_metrics(
        plugin,
        combo,
        config={"fast": 3, "slow": 8},
        quantity=None,
        fee_bps=0.0,
        score_from=train_len,
        sizing=sizing,
        start_equity=10_000.0,
    )
    assert m["trades"] == 0  # carry open is not a trade
    assert m["pnl"] > 20.0  # ~20 bars * 0.5 on ~10% equity


def test_offline_metrics_mark_open_to_last_close() -> None:
    """OOS single open must include unrealized move to window end close."""
    from unittest.mock import MagicMock

    from engine.position_sizing import SizingConfig, SizingMode
    from engine.research import offline_variant_metrics
    from strategies.ma_cross_example.plugin import MaCrossExamplePlugin

    core = MagicMock()
    plugin = MaCrossExamplePlugin(core, "ma_cross_example", MagicMock())
    plugin.config = {}

    # Strong uptrend: fast MA stays above slow → often a single lasting BUY.
    closes = [100.0 + i for i in range(40)]
    series = {
        "open": list(closes),
        "high": list(closes),
        "low": list(closes),
        "close": list(closes),
        "volume": [1.0] * len(closes),
    }
    sizing = SizingConfig(mode=SizingMode.PERCENT_OF_EQUITY, value=10.0)
    m = offline_variant_metrics(
        plugin,
        series,
        config={"fast": 3, "slow": 8},
        quantity=None,
        fee_bps=7.0,
        sizing=sizing,
        start_equity=10_000.0,
    )
    # Must not collapse to pure −fee on an open that rode the trend.
    assert m["trades"] >= 1
    assert m["pnl"] > 0.0


def test_walk_forward_baselines_and_timeframe_provenance() -> None:
    core = MagicMock()
    plugin = MaCrossExamplePlugin(core, "ma_cross_example", MagicMock())
    plugin.config = {}
    series = rising_trend(n=200)
    cfg = WalkForwardConfig(
        train_bars=40,
        test_bars=15,
        step=15,
        min_folds=5,
        selection_mode="best",
        bootstrap_samples=30,
        persist_report=False,
        seed=3,
        timeframe="1d",
        include_baselines=True,
    )
    out = run_walk_forward_offline(
        plugin,
        series,
        [{"fast": 3, "slow": 8}],
        config=cfg,
        fee_bps=7.0,
        provenance_extra={"strategy_id": "ma_cross_example", "ticker": "X"},
    )
    assert out["provenance"]["timeframe"] == "1d"
    assert "baselines" in out
    assert "buy_hold" in out["baselines"]
    assert "fee_zero_selected" in out["baselines"]
    assert "fixed_default" in out["baselines"]
    assert "edge_vs_buy_hold" in out["baselines"]
    assert "baselines" in out["folds"][0]
    # Rising series: buy-hold OOS should be positive on average.
    assert out["baselines"]["buy_hold"]["mean_oos_pnl"] > 0


def test_persist_report_sanitizes_slash_ticker(tmp_path) -> None:
    from engine.walk_forward import WalkForwardConfig, persist_walk_forward_report

    cfg = WalkForwardConfig(persist_report=True, report_dir=str(tmp_path))
    path = persist_walk_forward_report(
        {
            "provenance": {
                "strategy_id": "bollinger_reversion",
                "ticker": "ETH/USDT",
                "generated_at": "2026-08-04T00:00:00+00:00",
            }
        },
        cfg,
    )
    assert path is not None
    assert "ETHUSDT" in path
    assert "/" not in Path(path).name
    assert Path(path).is_file()


def test_atr_series_is_causal() -> None:
    from engine.position_sizing import atr_series_from_bars

    highs = [10, 11, 12, 13, 14, 100]  # last spike
    lows = [9, 10, 11, 12, 13, 14]
    closes = [9.5, 10.5, 11.5, 12.5, 13.5, 50]
    series = atr_series_from_bars(highs, lows, closes, period=3)
    # ATR at index before spike must not see the spike bar.
    assert series[4] is not None
    assert series[5] is not None
    assert series[5] > series[4]


if __name__ == "__main__":
    test_bars_needed_and_config_merge()
    test_bootstrap_ci_bounds()
    test_walk_forward_reproducible_and_provenance()
    test_walk_forward_too_short()
    test_auto_fit_folds_allows_short_series()
    test_btc_quantity_one_vs_sized_fee()
    test_offline_metrics_mark_open_to_last_close()
    test_walk_forward_baselines_and_timeframe_provenance()
    test_atr_series_is_causal()
    print("walk_forward OK")
