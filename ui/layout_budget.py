"""Pure Operator layout budget math (no GUI toolkit)."""

from __future__ import annotations

from dataclasses import dataclass

_DESK_GRIP_WIDTH = 8
_DESK_TICKER_MIN = 120
_DESK_TICKER_MAX = 800
_DESK_GRAPH_MIN = 200
_DESK_DOM_MIN = 180
_DESK_DOM_MAX = 800


def compute_flex_width(
    budget: int,
    fixed_widths: list[int] | tuple[int, ...],
    *,
    gaps: int = 0,
    spacing: int = 4,
    extra: int = 0,
    minimum: int = 80,
) -> int:
    """Pure, idempotent row math. Flex width is never measured from itself."""
    usable = int(budget) - sum(max(0, int(w)) for w in fixed_widths)
    usable -= max(0, int(gaps)) * max(0, int(spacing))
    usable -= max(0, int(extra))
    return max(int(minimum), min(int(budget), usable))


@dataclass(frozen=True)
class DeskWidths:
    ticker: int
    graph: int
    dom: int
    ticker_grip: int
    dom_grip: int

    @property
    def total(self) -> int:
        return self.ticker + self.graph + self.dom + self.ticker_grip + self.dom_grip


def _proportional_widths(total: int, weights: list[int]) -> list[int]:
    """Allocate every available pixel deterministically across visible panes."""
    if not weights:
        return []
    total = max(0, int(total))
    weight_sum = max(1, sum(max(1, int(w)) for w in weights))
    out: list[int] = []
    used = 0
    for i, weight in enumerate(weights):
        if i == len(weights) - 1:
            width = total - used
        else:
            width = int(total * max(1, int(weight)) / weight_sum)
            used += width
        out.append(max(0, width))
    return out


def _shrink_fixed_widths(
    ticker: int,
    dom: int,
    *,
    ticker_open: bool,
    dom_open: bool,
    maximum: int,
) -> tuple[int, int]:
    """Shrink fixed panes toward minima while preserving their relative slack."""
    values = [
        ticker if ticker_open else 0,
        dom if dom_open else 0,
    ]
    minima = [
        _DESK_TICKER_MIN if ticker_open else 0,
        _DESK_DOM_MIN if dom_open else 0,
    ]
    excess = max(0, sum(values) - max(0, int(maximum)))
    slacks = [max(0, value - minimum) for value, minimum in zip(values, minima)]
    slack_total = sum(slacks)
    if excess and slack_total:
        first = min(slacks[0], int(round(excess * slacks[0] / slack_total)))
        second = min(slacks[1], excess - first)
        remainder = excess - first - second
        if remainder:
            add = min(slacks[0] - first, remainder)
            first += add
            remainder -= add
        if remainder:
            second += min(slacks[1] - second, remainder)
        values[0] -= first
        values[1] -= second
    return values[0], values[1]


def compute_desk_widths(
    budget: int,
    *,
    ticker_width: int = 280,
    dom_width: int = 260,
    ticker_open: bool = True,
    graph_open: bool = True,
    dom_open: bool = True,
    grip_width: int = _DESK_GRIP_WIDTH,
) -> DeskWidths:
    """Resolve Desk columns without reading GUI state."""
    budget = max(0, int(budget))
    grip_width = max(0, int(grip_width))
    ticker_grip_open = ticker_open and (graph_open or dom_open)
    dom_grip_open = graph_open and dom_open
    requested_grips = [
        *([grip_width] if ticker_grip_open else []),
        *([grip_width] if dom_grip_open else []),
    ]
    if sum(requested_grips) > budget:
        allocated_grips = iter(_proportional_widths(budget, requested_grips))
        ticker_grip = next(allocated_grips) if ticker_grip_open else 0
        dom_grip = next(allocated_grips) if dom_grip_open else 0
    else:
        ticker_grip = grip_width if ticker_grip_open else 0
        dom_grip = grip_width if dom_grip_open else 0
    pane_budget = max(0, budget - ticker_grip - dom_grip)

    ticker_pref = (
        max(_DESK_TICKER_MIN, min(_DESK_TICKER_MAX, int(ticker_width)))
        if ticker_open
        else 0
    )
    dom_pref = (
        max(_DESK_DOM_MIN, min(_DESK_DOM_MAX, int(dom_width))) if dom_open else 0
    )

    if not any((ticker_open, graph_open, dom_open)):
        return DeskWidths(0, 0, 0, 0, 0)

    if graph_open:
        fixed_minimum = (_DESK_TICKER_MIN if ticker_open else 0) + (
            _DESK_DOM_MIN if dom_open else 0
        )
        minimum_total = fixed_minimum + _DESK_GRAPH_MIN
        if pane_budget < minimum_total:
            weights = [
                *([_DESK_TICKER_MIN] if ticker_open else []),
                _DESK_GRAPH_MIN,
                *([_DESK_DOM_MIN] if dom_open else []),
            ]
            allocated = iter(_proportional_widths(pane_budget, weights))
            ticker = next(allocated) if ticker_open else 0
            graph = next(allocated)
            dom = next(allocated) if dom_open else 0
        else:
            ticker, dom = _shrink_fixed_widths(
                ticker_pref,
                dom_pref,
                ticker_open=ticker_open,
                dom_open=dom_open,
                maximum=pane_budget - _DESK_GRAPH_MIN,
            )
            graph = pane_budget - ticker - dom
        return DeskWidths(ticker, graph, dom, ticker_grip, dom_grip)

    if ticker_open and dom_open:
        if pane_budget < _DESK_TICKER_MIN + _DESK_DOM_MIN:
            ticker, dom = _proportional_widths(
                pane_budget, [_DESK_TICKER_MIN, _DESK_DOM_MIN]
            )
        else:
            ticker = min(ticker_pref, pane_budget - _DESK_DOM_MIN)
            dom = pane_budget - ticker
        return DeskWidths(ticker, 0, dom, ticker_grip, 0)
    if ticker_open:
        return DeskWidths(pane_budget, 0, 0, 0, 0)
    return DeskWidths(0, 0, pane_budget, 0, 0)


def resize_desk_preference(edge: str, origin: int, delta_x: float) -> int:
    """Translate splitter motion into one persisted width preference."""
    if edge == "ticker_width":
        return max(
            _DESK_TICKER_MIN,
            min(_DESK_TICKER_MAX, int(round(origin + delta_x))),
        )
    if edge == "dom_width":
        return max(
            _DESK_DOM_MIN,
            min(_DESK_DOM_MAX, int(round(origin - delta_x))),
        )
    raise ValueError(f"unknown Desk resize edge: {edge}")
