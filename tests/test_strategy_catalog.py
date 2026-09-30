"""Tests for Consilium strategy catalog discovery."""

from __future__ import annotations

from ui.strategy_catalog import (
    active_consilium_ids,
    catalog_strategy_ids,
    is_strategy_plugin_row,
    looks_like_strategy_id,
)


def test_looks_like_strategy_id():
    assert looks_like_strategy_id("ma_cross_example")
    assert looks_like_strategy_id("mean_reversion_example")
    assert looks_like_strategy_id("test_strategy")
    assert not looks_like_strategy_id("ai_analyst")
    assert not looks_like_strategy_id("order_execution")


def test_catalog_merges_plugins_and_ensemble():
    snap = {
        "plugins": [
            {"id": "ma_cross_example", "is_strategy": True},
            {"id": "ai_analyst", "is_strategy": False},
            {"id": "mean_reversion_example", "kind": "business"},
        ],
        "ensemble": {"strategies": ["extra_strategy_x"]},
        "config": {"plugins": {"momentum_bot": {"fast": 3}}},
    }
    ids = catalog_strategy_ids(snap)
    assert "ma_cross_example" in ids
    assert "mean_reversion_example" in ids
    assert "extra_strategy_x" in ids
    assert "momentum_bot" in ids
    assert "ai_analyst" not in ids
    assert is_strategy_plugin_row({"id": "x", "is_strategy": True})


def test_active_consilium_ids():
    snap = {
        "plugins": [{"id": "ma_cross_example", "is_strategy": True}],
        "ensemble": {"strategies": ["ma_cross_example"]},
        "config": {},
    }
    assert active_consilium_ids(snap) == ["ma_cross_example"]
    snap2 = {
        "plugins": [
            {"id": "ma_cross_example", "is_strategy": True},
            {"id": "mean_reversion_example", "is_strategy": True},
        ],
        "ensemble": {"strategies": []},
        "config": {},
    }
    # empty lineup ⇒ all catalog (engine default)
    assert set(active_consilium_ids(snap2)) == {
        "ma_cross_example",
        "mean_reversion_example",
    }
