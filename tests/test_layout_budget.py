"""Pure regression tests for horizontal layout budgeting."""

from __future__ import annotations

from ui.layout_budget import compute_flex_width


def test_flex_width_consumes_exact_remaining_budget() -> None:
    assert compute_flex_width(1200, [280, 300], gaps=2, spacing=4, extra=12) == 600


def test_flex_width_is_idempotent_and_does_not_feed_back() -> None:
    first = compute_flex_width(1000, [320], gaps=1)
    for _ in range(100):
        assert compute_flex_width(1000, [320], gaps=1) == first


def test_flex_width_tracks_left_pane_resize_without_gap() -> None:
    wide_left = compute_flex_width(1000, [500], gaps=1)
    narrow_left = compute_flex_width(1000, [260], gaps=1)
    assert narrow_left - wide_left == 240


def test_flex_width_clamps_on_tiny_viewports() -> None:
    assert compute_flex_width(160, [140], gaps=1, minimum=80) == 80

