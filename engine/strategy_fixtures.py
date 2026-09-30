"""Deterministic OHLC fixtures for Strategy SDK tests."""

from __future__ import annotations

from typing import Any


def rising_trend(*, n: int = 40, start: float = 100.0, step: float = 1.0) -> dict[str, list[float]]:
    closes = [start + i * step for i in range(n)]
    return {
        "open": [c - 0.2 for c in closes],
        "high": [c + 0.5 for c in closes],
        "low": [c - 0.5 for c in closes],
        "close": closes,
        "volume": [10.0 + i for i in range(n)],
    }


def mean_reverting(*, n: int = 60, mid: float = 100.0, amp: float = 8.0) -> dict[str, list[float]]:
    """Sine-like path around mid for z-score entries."""
    import math

    closes: list[float] = []
    for i in range(n):
        closes.append(mid + amp * math.sin(i / 4.0))
    return {
        "open": list(closes),
        "high": [c + 0.3 for c in closes],
        "low": [c - 0.3 for c in closes],
        "close": closes,
        "volume": [20.0] * n,
    }


def breakout_up(*, flat: int = 25, jump: float = 10.0, start: float = 100.0) -> dict[str, list[float]]:
    closes = [start] * flat + [start + jump, start + jump + 1.0, start + jump + 2.0]
    n = len(closes)
    return {
        "open": list(closes),
        "high": [c + 0.2 for c in closes],
        "low": [c - 0.2 for c in closes],
        "close": closes,
        "volume": [15.0] * n,
    }


def volume_spike_up(*, n: int = 30, start: float = 100.0) -> dict[str, list[float]]:
    closes = [start + i * 0.1 for i in range(n - 1)]
    closes.append(closes[-1] * 1.02)
    volumes = [10.0] * (n - 1) + [50.0]
    return {
        "open": list(closes),
        "high": [c + 0.2 for c in closes],
        "low": [c - 0.2 for c in closes],
        "close": closes,
        "volume": volumes,
    }


def append_future(series: dict[str, list[float]], *, bars: int = 5, step: float = 1.0) -> dict[str, list[float]]:
    """Extend series with future bars (for no-lookahead checks)."""
    out = {k: list(v) for k, v in series.items()}
    last = float(out["close"][-1])
    for i in range(1, bars + 1):
        c = last + step * i
        out["open"].append(c)
        out["high"].append(c + 0.5)
        out["low"].append(c - 0.5)
        out["close"].append(c)
        out["volume"].append(10.0 + i)
    return out


def decisions_summary(decisions: list[Any]) -> dict[str, int]:
    counts = {"BUY": 0, "SELL": 0, "HOLD": 0, "NONE": 0}
    for d in decisions:
        if d is None:
            counts["NONE"] += 1
        else:
            action = str(getattr(d, "action", "HOLD")).upper()
            counts[action] = counts.get(action, 0) + 1
    return counts
