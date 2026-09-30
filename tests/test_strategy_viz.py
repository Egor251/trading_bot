"""Tests for strategy chart overlay recipes."""

from __future__ import annotations

from ui.strategy_viz import (
    describe_lines,
    find_cross_indices,
    has_chart_visuals,
    markers_in_view,
    overlay_series,
    signal_points,
)


def test_ma_cross_overlay_series():
    closes = [float(i) for i in range(1, 31)]
    series = overlay_series(
        strategy_id="ma_cross_example",
        closes=closes,
        params={"fast": 3, "slow": 8},
    )
    assert len(series) == 2
    assert series[0]["key"] == "fast"
    assert series[1]["key"] == "slow"
    assert series[0]["ys"][-1] is not None
    assert series[1]["ys"][-1] is not None
    assert has_chart_visuals("ma_cross_example")


def test_mean_reversion_bands():
    closes = [100.0 + ((-1) ** i) * (i % 5) for i in range(40)]
    series = overlay_series(
        strategy_id="mean_reversion_example",
        closes=closes,
        params={"window": 10, "z_entry": 1.5},
    )
    assert len(series) == 3
    assert {s["key"] for s in series} == {"mean", "band_up", "band_lo"}


def test_describe_and_crosses():
    from ui.i18n import load

    load("en")
    lines = describe_lines("ma_cross_example", {"fast": 3, "slow": 8})
    assert any("SMA" in x or "average" in x.lower() or "cross" in x.lower() for x in lines)
    load("ru")
    lines_ru = describe_lines("ma_cross_example", {"fast": 3, "slow": 8})
    assert any("SMA" in x or "средн" in x.lower() for x in lines_ru)
    fast = [None, None, 1.0, 2.0, 3.0, 2.0]
    slow = [None, None, 2.0, 2.0, 2.0, 2.5]
    crosses = find_cross_indices(fast, slow)
    assert crosses  # at least one flip


def test_signal_points_ma_cross():
    # Uptrend then drop so fast/slow cross both ways
    closes = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 14.0, 12.0, 10.0, 9.0, 8.0, 7.0]
    pts = signal_points(
        strategy_id="ma_cross_example",
        closes=closes,
        params={"fast": 2, "slow": 4},
    )
    assert pts["BUY"] or pts["SELL"]
    xs, ys = markers_in_view(
        pts["BUY"] + pts["SELL"],
        start=0,
        end=len(closes),
        dates=[float(i) for i in range(len(closes))],
    )
    assert len(xs) == len(ys)
    assert len(xs) == len(pts["BUY"]) + len(pts["SELL"])


def test_signal_points_mean_reversion():
    closes = [100.0] * 15 + [80.0, 81.0, 82.0] + [100.0] * 5 + [120.0, 119.0]
    pts = signal_points(
        strategy_id="mean_reversion_example",
        closes=closes,
        params={"window": 10, "z_entry": 1.0},
    )
    assert isinstance(pts["BUY"], list)
    assert isinstance(pts["SELL"], list)
