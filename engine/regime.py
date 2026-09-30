"""Rule-based market regime detection (tech doc §4.5 soft version)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

RegimeName = Literal[
    "strong_trend",
    "weak_trend",
    "high_vol",
    "low_vol",
    "shock",
    "unknown",
]


@dataclass(slots=True)
class RegimeSnapshot:
    regime: RegimeName
    atr_pct: float
    atr_percentile: float
    trend_strength: float
    symbol: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime,
            "atr_pct": self.atr_pct,
            "atr_percentile": self.atr_percentile,
            "trend_strength": self.trend_strength,
            "symbol": self.symbol,
            "details": dict(self.details),
        }


@dataclass(slots=True)
class RegimeDetector:
    """
    Soft regime from close series + ATR%.

    - shock: ATR% jump vs recent median
    - high_vol / low_vol: ATR percentile vs history
    - strong_trend / weak_trend: |SMA_fast - SMA_slow| / price
    """

    atr_period: int = 14
    lookback: int = 100
    shock_mult: float = 2.5
    high_vol_pct: float = 0.75
    low_vol_pct: float = 0.25
    strong_trend_thr: float = 0.008
    _atr_history: deque[float] = field(default_factory=lambda: deque(maxlen=200))

    def observe(
        self,
        closes: list[float],
        *,
        highs: list[float] | None = None,
        lows: list[float] | None = None,
        symbol: str = "",
    ) -> RegimeSnapshot:
        if len(closes) < max(self.atr_period + 2, 20):
            return RegimeSnapshot(
                regime="unknown",
                atr_pct=0.0,
                atr_percentile=0.5,
                trend_strength=0.0,
                symbol=symbol,
            )
        atr = self._atr(closes, highs=highs, lows=lows)
        price = float(closes[-1]) or 1.0
        atr_pct = float(atr) / price
        self._atr_history.append(atr_pct)
        hist = sorted(self._atr_history)
        percentile = _percentile_rank(hist, atr_pct)
        median = hist[len(hist) // 2] if hist else atr_pct
        trend = self._trend_strength(closes)

        if median > 1e-12 and atr_pct >= median * float(self.shock_mult):
            regime: RegimeName = "shock"
        elif percentile >= float(self.high_vol_pct):
            regime = "high_vol"
        elif percentile <= float(self.low_vol_pct):
            regime = "low_vol" if trend < float(self.strong_trend_thr) else "weak_trend"
        elif trend >= float(self.strong_trend_thr):
            regime = "strong_trend"
        else:
            regime = "weak_trend"

        return RegimeSnapshot(
            regime=regime,
            atr_pct=atr_pct,
            atr_percentile=percentile,
            trend_strength=trend,
            symbol=symbol,
            details={"median_atr_pct": median, "bars": len(closes)},
        )

    def _atr(
        self,
        closes: list[float],
        *,
        highs: list[float] | None,
        lows: list[float] | None,
    ) -> float:
        period = max(2, int(self.atr_period))
        trs: list[float] = []
        for i in range(1, len(closes)):
            c0 = float(closes[i - 1])
            c1 = float(closes[i])
            hi = float(highs[i]) if highs and i < len(highs) else max(c0, c1)
            lo = float(lows[i]) if lows and i < len(lows) else min(c0, c1)
            tr = max(hi - lo, abs(hi - c0), abs(lo - c0))
            trs.append(tr)
        window = trs[-period:]
        if not window:
            return 0.0
        return sum(window) / len(window)

    def _trend_strength(self, closes: list[float]) -> float:
        fast_n, slow_n = 8, 21
        if len(closes) < slow_n:
            return 0.0
        fast = sum(closes[-fast_n:]) / fast_n
        slow = sum(closes[-slow_n:]) / slow_n
        price = float(closes[-1]) or 1.0
        return abs(fast - slow) / price


def _percentile_rank(sorted_vals: list[float], value: float) -> float:
    if not sorted_vals:
        return 0.5
    n = len(sorted_vals)
    below = sum(1 for v in sorted_vals if v <= value)
    return below / n


# Archetype bias multipliers under each regime (applied to live weights).
REGIME_ARCHETYPE_BIAS: dict[str, dict[str, float]] = {
    "strong_trend": {"trend": 1.35, "momentum": 1.25, "mean_reversion": 0.65, "volatility": 0.9},
    "weak_trend": {"trend": 1.05, "momentum": 1.0, "mean_reversion": 1.15, "volatility": 1.0},
    "high_vol": {"trend": 0.85, "momentum": 0.9, "mean_reversion": 0.8, "volatility": 1.3},
    "low_vol": {"trend": 0.95, "momentum": 0.95, "mean_reversion": 1.2, "volatility": 0.85},
    "shock": {"trend": 0.5, "momentum": 0.5, "mean_reversion": 0.5, "volatility": 0.7},
    "unknown": {},
}


def apply_regime_bias(
    weights: dict[str, float],
    archetypes: dict[str, str],
    regime: str,
) -> dict[str, float]:
    """Scale weights by strategy archetype for ``regime``, then renormalize."""
    bias = REGIME_ARCHETYPE_BIAS.get(str(regime), {}) or {}
    if not bias:
        return dict(weights)
    out: dict[str, float] = {}
    for sid, w in weights.items():
        arch = str(archetypes.get(sid) or "trend").lower()
        mult = float(bias.get(arch, 1.0))
        out[sid] = max(0.0, float(w) * mult)
    total = sum(out.values())
    if total <= 1e-12:
        n = len(out) or 1
        return {k: 1.0 / n for k in out}
    return {k: v / total for k, v in out.items()}
