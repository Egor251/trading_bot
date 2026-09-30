"""Human number formatting — never scientific notation (no 6.4e+04)."""

from __future__ import annotations

from typing import Any


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt_price(value: Any, *, dash: str = "—") -> str:
    """Price / mid / OHLC — fixed decimals, thousands separators, no ``e+``."""
    x = _as_float(value)
    if x is None:
        return dash
    ax = abs(x)
    if ax >= 1000:
        return f"{x:,.2f}"
    if ax >= 100:
        return f"{x:,.2f}"
    if ax >= 1:
        s = f"{x:,.4f}"
    elif ax >= 0.01:
        s = f"{x:,.6f}"
    else:
        s = f"{x:,.8f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def fmt_qty(value: Any, *, dash: str = "—") -> str:
    """Size / volume — integers when large, else trimmed decimals."""
    x = _as_float(value)
    if x is None:
        return dash
    ax = abs(x)
    if ax >= 1000:
        return f"{x:,.0f}"
    if ax >= 1:
        s = f"{x:,.2f}"
    else:
        s = f"{x:,.4f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def fmt_pct(value: Any, *, signed: bool = True, dash: str = "—") -> str:
    x = _as_float(value)
    if x is None:
        return dash
    if signed:
        return f"{x:+.2f}%"
    return f"{x:.2f}%"


def axis_tick_format(*, lo: float, hi: float) -> str:
    """printf tick format for Dear PyGui / ImPlot axes."""
    span = abs(float(hi) - float(lo))
    mid = max(abs(float(lo)), abs(float(hi)), span)
    if mid >= 1000 or span >= 50:
        return "%.2f"
    if mid >= 1 or span >= 0.5:
        return "%.2f"
    if mid >= 0.01 or span >= 0.001:
        return "%.4f"
    return "%.6f"
