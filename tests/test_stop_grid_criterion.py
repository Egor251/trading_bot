"""Pin the R-53 criterion: different winners per window are not a pass."""

from __future__ import annotations

from tools.stop_grid_verdict import configuration_passing_all


def test_one_configuration_must_clear_every_window():
    grid = [(0.01, 10), (0.01, 20), (0.02, 10), (0.02, 20)]
    windows = [
        {(0.01, 10), (0.01, 20)},
        {(0.01, 10)},
        {(0.01, 20), (0.02, 20)},
    ]
    # The first implementation reported HOLDS here; the intersection is empty.
    assert configuration_passing_all(windows, grid) == set()


def test_common_configuration_is_found():
    grid = [(0.01, 10), (0.02, 20)]
    windows = [
        {(0.01, 10), (0.02, 20)},
        {(0.02, 20)},
        {(0.02, 20)},
    ]
    assert configuration_passing_all(windows, grid) == {(0.02, 20)}


def test_empty_windows_pass_nothing():
    assert configuration_passing_all([]) == set()
