"""Tests for phantom grid / optimize variant generators."""

from __future__ import annotations

from host.phantom_variants import (
    default_grid_variants,
    optimize_variants,
    schema_search_variants,
    variants_for_mode,
)

# Strategy ids that must NEVER fall back to MA fast/slow grids.
_NON_MA_IDS = (
    "adx_di_trend",
    "cci_reversion",
    "dual_thrust",
    "heikin_ashi_trend",
    "keltner_breakout",
    "parabolic_sar_follow",
    "stochastic_reversion",
    "turtle_breakout",
    "vwap_reversion",
    "williams_r_reversion",
)


def test_default_grid_ma_and_mr():
    ma = default_grid_variants("ma_cross_example")
    assert len(ma) == 3
    assert ma[0]["fast"] < ma[0]["slow"]
    mr = default_grid_variants("mean_reversion_example")
    assert "window" in mr[0] and "z_entry" in mr[0]


def test_optimize_denser_than_grid():
    grid = default_grid_variants("ma_cross_example")
    opt = optimize_variants("ma_cross_example", max_variants=20)
    assert len(opt) > len(grid)
    assert all(v["slow"] > v["fast"] for v in opt)
    mr = optimize_variants("mean_reversion_example", max_variants=15)
    assert len(mr) == 15
    assert variants_for_mode("ma_cross_example", "optimize", max_variants=10) == optimize_variants(
        "ma_cross_example", max_variants=10
    )
    assert variants_for_mode("ma_cross_example", "grid") == grid


def test_popular_strategies_have_native_grids():
    expected_key = {
        "adx_di_trend": "adx_min",
        "cci_reversion": "entry",
        "dual_thrust": "k1",
        "heikin_ashi_trend": "min_streak",
        "keltner_breakout": "ema_period",
        "parabolic_sar_follow": "af_start",
        "stochastic_reversion": "k_period",
        "turtle_breakout": "entry_lookback",
        "vwap_reversion": "entry_pct",
        "williams_r_reversion": "oversold",
    }
    for sid, key in expected_key.items():
        grid = default_grid_variants(sid)
        assert len(grid) >= 2, sid
        assert key in grid[0], (sid, grid[0])
        assert "fast" not in grid[0] and "slow" not in grid[0], sid


def test_unknown_strategy_does_not_get_ma_fallback():
    grid = default_grid_variants("totally_unknown_plugin_xyz")
    assert grid == [{}]
    for sid in _NON_MA_IDS:
        for row in default_grid_variants(sid):
            assert set(row).isdisjoint({"fast", "slow"}), (sid, row)


def test_schema_search_variants_cartesian():
    schema = {
        "window": {"type": "int", "default": 20, "min": 10, "max": 30},
        "z_entry": {"type": "float", "default": 1.5, "min": 1.0, "max": 2.0},
        "quantity": {"type": "float", "default": 10.0, "min": 1, "max": 100},
    }
    rows = schema_search_variants(schema, dense=True, max_variants=12)
    assert 2 <= len(rows) <= 12
    assert all("window" in r and "z_entry" in r for r in rows)
    assert all("quantity" not in r for r in rows)
    opt = optimize_variants("brand_new_plugin_abc", max_variants=8, schema=schema)
    assert len(opt) == 8
    assert opt[0].get("window") is not None
