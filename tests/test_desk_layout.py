"""Regression coverage for deterministic Desk horizontal resizing."""

from __future__ import annotations

from copy import deepcopy

import pytest

from ui.layout_budget import compute_desk_widths, resize_desk_preference


@pytest.mark.parametrize(
    ("ticker_open", "graph_open", "dom_open"),
    [
        (ticker, graph, dom)
        for ticker in (False, True)
        for graph in (False, True)
        for dom in (False, True)
    ],
)
def test_desk_widths_consume_exact_budget_for_every_visibility_state(
    ticker_open: bool, graph_open: bool, dom_open: bool
) -> None:
    widths = compute_desk_widths(
        1200,
        ticker_open=ticker_open,
        graph_open=graph_open,
        dom_open=dom_open,
    )
    expected = 1200 if any((ticker_open, graph_open, dom_open)) else 0
    assert widths.total == expected
    assert (widths.ticker > 0) is ticker_open
    assert (widths.graph > 0) is graph_open
    assert (widths.dom > 0) is dom_open


def test_viewport_resize_changes_only_graph() -> None:
    narrow = compute_desk_widths(1000, ticker_width=300, dom_width=280)
    wide = compute_desk_widths(1375, ticker_width=300, dom_width=280)
    assert wide.ticker == narrow.ticker == 300
    assert wide.dom == narrow.dom == 280
    assert wide.graph - narrow.graph == 375


def test_ticker_and_dom_preferences_only_steal_from_graph() -> None:
    base = compute_desk_widths(1400, ticker_width=280, dom_width=260)
    ticker_drag = compute_desk_widths(1400, ticker_width=400, dom_width=260)
    dom_drag = compute_desk_widths(1400, ticker_width=280, dom_width=350)
    assert ticker_drag.dom == base.dom
    assert ticker_drag.graph == base.graph - 120
    assert dom_drag.ticker == base.ticker
    assert dom_drag.graph == base.graph - 90


@pytest.mark.parametrize("budget", [*range(0, 40), *range(40, 1001, 37)])
def test_narrow_windows_never_overflow_or_create_negative_widths(budget: int) -> None:
    widths = compute_desk_widths(
        budget,
        ticker_width=800,
        dom_width=800,
        ticker_open=True,
        graph_open=True,
        dom_open=True,
    )
    assert widths.total == budget
    assert min(widths.ticker, widths.graph, widths.dom) >= 0


def test_preferences_restore_after_temporary_narrow_window() -> None:
    preferred = {"ticker_width": 420, "dom_width": 360}
    narrow = compute_desk_widths(560, **preferred)
    restored = compute_desk_widths(1600, **preferred)
    assert narrow.ticker < preferred["ticker_width"]
    assert narrow.dom < preferred["dom_width"]
    assert restored.ticker == preferred["ticker_width"]
    assert restored.dom == preferred["dom_width"]


def test_resolver_is_idempotent_and_does_not_mutate_inputs() -> None:
    inputs = {
        "budget": 1234,
        "ticker_width": 333,
        "dom_width": 277,
        "ticker_open": True,
        "graph_open": True,
        "dom_open": True,
    }
    original = deepcopy(inputs)
    first = compute_desk_widths(**inputs)
    for _ in range(250):
        assert compute_desk_widths(**inputs) == first
    assert inputs == original


def test_splitter_direction_and_clamps() -> None:
    assert resize_desk_preference("ticker_width", 280, 40) == 320
    assert resize_desk_preference("dom_width", 260, 40) == 220
    assert resize_desk_preference("ticker_width", 280, -1000) == 120
    assert resize_desk_preference("dom_width", 260, -1000) == 800
    with pytest.raises(ValueError):
        resize_desk_preference("unknown", 100, 10)
