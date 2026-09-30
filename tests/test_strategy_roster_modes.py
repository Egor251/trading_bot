"""Roster must refresh live/observe/off when catalog ids stay the same."""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from ui.strategy_modes import _modes_from_snap  # noqa: E402


def test_modes_from_snap_prefers_ensemble_lists() -> None:
    modes = _modes_from_snap(
        ids=["macd_trend", "ma_cross_example", "ema_cross", "mean_reversion"],
        live=["macd_trend"],
        observe=["ma_cross_example", "ema_cross"],
        snap={"plugins": [{"id": "mean_reversion", "enabled": False}]},
    )
    assert modes["macd_trend"] == "live"
    assert modes["ma_cross_example"] == "observe"
    assert modes["ema_cross"] == "observe"
    assert modes["mean_reversion"] == "off"


def test_mode_fingerprint_changes_without_catalog_change() -> None:
    ids = ["macd_trend", "ma_cross_example"]
    before = _modes_from_snap(
        ids=ids,
        live=["macd_trend", "ma_cross_example"],
        observe=[],
        snap={},
    )
    after = _modes_from_snap(
        ids=ids,
        live=["macd_trend"],
        observe=["ma_cross_example"],
        snap={},
    )
    fp_before = "|".join(f"{sid}={before[sid]}" for sid in ids)
    fp_after = "|".join(f"{sid}={after[sid]}" for sid in ids)
    assert fp_before != fp_after
    assert "ma_cross_example=observe" in fp_after
