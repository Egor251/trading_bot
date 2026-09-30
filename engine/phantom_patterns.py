"""Synthetic market pattern generators for phantom stress tests (todo2 §4 / diagnostics).

Patterns are deterministic given ``seed`` so grids are reproducible.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Iterable, Sequence

from .market import Candle


class MarketPattern(str, Enum):
    SHARP_RISE = "sharp_rise"  # импульс вверх
    SHARP_DROP = "sharp_drop"  # резкий отвал / dump
    REBOUND = "rebound"  # V: падение → отскок
    BULL_TRAP = "bull_trap"  # рост → обвал
    CHOP = "chop"  # боковик / шум
    TREND_UP = "trend_up"  # устойчивый аптренд
    TREND_DOWN = "trend_down"  # устойчивый даунтренд
    GAP_UP = "gap_up"  # гэп вверх + продолжение
    GAP_DOWN = "gap_down"  # гэп вниз
    VOLATILITY_SPIKE = "volatility_spike"  # сжатие → взрыв волатильности


DEFAULT_PATTERNS: tuple[MarketPattern, ...] = (
    MarketPattern.SHARP_RISE,
    MarketPattern.SHARP_DROP,
    MarketPattern.REBOUND,
    MarketPattern.BULL_TRAP,
    MarketPattern.CHOP,
    MarketPattern.TREND_UP,
    MarketPattern.TREND_DOWN,
    MarketPattern.VOLATILITY_SPIKE,
)


@dataclass(frozen=True, slots=True)
class PatternSpec:
    kind: MarketPattern | str
    bars: int = 80
    start_price: float = 100.0
    seed: int = 42
    noise: float = 0.003
    magnitude: float = 0.12  # typical move size (fraction)
    timeframe: str = "1m"

    def resolved_kind(self) -> MarketPattern:
        if isinstance(self.kind, MarketPattern):
            return self.kind
        raw = getattr(self.kind, "value", self.kind)
        return MarketPattern(str(raw))


def list_patterns() -> list[str]:
    return [p.value for p in MarketPattern]


def _clamp_ohlc(
    open_: float, close: float, *, noise_high: float, noise_low: float
) -> tuple[float, float, float, float]:
    high = max(open_, close) * (1.0 + abs(noise_high))
    low = min(open_, close) * (1.0 - abs(noise_low))
    if low <= 0:
        low = min(open_, close) * 0.5
    if high < low:
        high, low = low, high
    return open_, high, low, close


def _path_for(
    kind: MarketPattern,
    n: int,
    *,
    start: float,
    magnitude: float,
    rng: random.Random,
) -> list[float]:
    """Return close path of length n starting near ``start``."""
    closes = [float(start)]
    mag = abs(float(magnitude))

    def step(frac: float, wobble: float = 0.15) -> None:
        prev = closes[-1]
        shock = 1.0 + frac + rng.uniform(-wobble, wobble) * abs(frac)
        closes.append(max(0.01, prev * shock))

    if kind is MarketPattern.SHARP_RISE:
        warm = max(5, n // 5)
        for _ in range(warm - 1):
            step(rng.uniform(-0.002, 0.003))
        spike = max(3, n // 8)
        for i in range(spike):
            step(mag / spike * (1.2 - i / spike))
        while len(closes) < n:
            step(rng.uniform(-0.004, 0.006))
    elif kind is MarketPattern.SHARP_DROP:
        warm = max(5, n // 5)
        for _ in range(warm - 1):
            step(rng.uniform(-0.002, 0.004))
        dump = max(3, n // 8)
        for i in range(dump):
            step(-mag / dump * (1.3 - i / dump))
        while len(closes) < n:
            step(rng.uniform(-0.006, 0.003))
    elif kind is MarketPattern.REBOUND:
        mid = n // 2
        for i in range(1, mid):
            step(-mag / mid * (1.0 + 0.3 * math.sin(i)))
        while len(closes) < n:
            step(mag / max(1, n - mid) * 1.1)
    elif kind is MarketPattern.BULL_TRAP:
        mid = n // 2
        for _ in range(1, mid):
            step(mag / mid)
        while len(closes) < n:
            step(-mag / max(1, n - mid) * 1.2)
    elif kind is MarketPattern.CHOP:
        while len(closes) < n:
            step(rng.uniform(-0.004, 0.004), wobble=0.5)
    elif kind is MarketPattern.TREND_UP:
        drift = mag / max(1, n)
        while len(closes) < n:
            step(drift + rng.uniform(-0.002, 0.003))
    elif kind is MarketPattern.TREND_DOWN:
        drift = -mag / max(1, n)
        while len(closes) < n:
            step(drift + rng.uniform(-0.003, 0.002))
    elif kind is MarketPattern.GAP_UP:
        warm = max(4, n // 6)
        for _ in range(warm - 1):
            step(rng.uniform(-0.002, 0.002))
        closes.append(max(0.01, closes[-1] * (1.0 + mag)))
        while len(closes) < n:
            step(rng.uniform(-0.003, 0.005))
    elif kind is MarketPattern.GAP_DOWN:
        warm = max(4, n // 6)
        for _ in range(warm - 1):
            step(rng.uniform(-0.002, 0.002))
        closes.append(max(0.01, closes[-1] * (1.0 - mag)))
        while len(closes) < n:
            step(rng.uniform(-0.005, 0.003))
    elif kind is MarketPattern.VOLATILITY_SPIKE:
        calm = max(8, n // 3)
        for _ in range(calm - 1):
            step(rng.uniform(-0.001, 0.001), wobble=0.2)
        while len(closes) < n:
            step(rng.choice([-1, 1]) * mag / 6 + rng.uniform(-0.01, 0.01), wobble=0.8)
    else:
        while len(closes) < n:
            step(rng.uniform(-0.003, 0.003))

    return closes[:n]


def generate_pattern_candles(
    symbol: str,
    spec: PatternSpec | MappingLike | None = None,
    *,
    class_code: str = "SYNTH",
    start_ts: datetime | None = None,
) -> list[Candle]:
    """Build OHLCV candles for one pattern spec."""
    if spec is None:
        spec = PatternSpec(kind=MarketPattern.CHOP)
    elif not isinstance(spec, PatternSpec):
        spec = PatternSpec(
            kind=spec.get("kind") or spec.get("pattern") or MarketPattern.CHOP,  # type: ignore[union-attr]
            bars=int(spec.get("bars", 80)),  # type: ignore[union-attr]
            start_price=float(spec.get("start_price", 100.0)),  # type: ignore[union-attr]
            seed=int(spec.get("seed", 42)),  # type: ignore[union-attr]
            noise=float(spec.get("noise", 0.003)),  # type: ignore[union-attr]
            magnitude=float(spec.get("magnitude", 0.12)),  # type: ignore[union-attr]
            timeframe=str(spec.get("timeframe", "1m")),  # type: ignore[union-attr]
        )
    kind = spec.resolved_kind()
    n = max(10, int(spec.bars))
    rng = random.Random(int(spec.seed))
    closes = _path_for(
        kind, n, start=float(spec.start_price), magnitude=float(spec.magnitude), rng=rng
    )
    ts0 = start_ts or datetime(2024, 1, 1, tzinfo=UTC)
    if ts0.tzinfo is None:
        ts0 = ts0.replace(tzinfo=UTC)
    interval = timedelta(minutes=1)
    tf = str(spec.timeframe or "1m")
    if tf.endswith("m") and tf[:-1].isdigit():
        interval = timedelta(minutes=max(1, int(tf[:-1])))
    elif tf.endswith("h") and tf[:-1].isdigit():
        interval = timedelta(hours=max(1, int(tf[:-1])))

    candles: list[Candle] = []
    prev_close = closes[0]
    for i, close in enumerate(closes):
        open_ = prev_close if i else close * (1.0 - rng.uniform(0, spec.noise))
        hi_n = rng.uniform(0, spec.noise * 2)
        lo_n = rng.uniform(0, spec.noise * 2)
        o, h, l, c = _clamp_ohlc(open_, close, noise_high=hi_n, noise_low=lo_n)
        # Gap patterns: force open away from previous close on the gap bar.
        if kind in {MarketPattern.GAP_UP, MarketPattern.GAP_DOWN} and i == max(4, n // 6):
            o = close
            h = max(o, c) * (1.0 + hi_n)
            l = min(o, c) * (1.0 - lo_n)
        vol = 1_000.0 * (1.0 + abs(c - o) / max(o, 1e-9) * 20 + rng.random())
        candles.append(
            Candle(
                symbol=symbol,
                timeframe=tf,
                ts=ts0 + interval * i,
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                volume=float(vol),
                exchange="synth",
                class_code=class_code,
                raw={"pattern": kind.value, "seed": spec.seed, "bar": i},
            )
        )
        prev_close = c
    return candles


# typing helper without importing Mapping at runtime cycle risk
MappingLike = Any


def default_pattern_specs(
    *,
    bars: int = 80,
    start_price: float = 100.0,
    base_seed: int = 42,
    patterns: Sequence[str | MarketPattern] | None = None,
) -> list[PatternSpec]:
    kinds = list(patterns) if patterns else list(DEFAULT_PATTERNS)
    out: list[PatternSpec] = []
    for i, kind in enumerate(kinds):
        out.append(
            PatternSpec(
                kind=kind,
                bars=bars,
                start_price=start_price,
                seed=base_seed + i * 17,
            )
        )
    return out


def candle_unix_ts(ts: Any) -> float:
    if hasattr(ts, "timestamp"):
        try:
            return float(ts.timestamp())
        except (TypeError, ValueError, OSError):
            pass
    try:
        return float(ts)
    except (TypeError, ValueError):
        return 0.0


def ohlc_dict_from_candles(
    candles: Iterable[Candle],
    *,
    ticker: str,
    class_code: str,
    strategy_id: str = "",
    provider: str = "pattern",
) -> dict[str, Any]:
    bars = list(candles)
    return {
        "ticker": ticker,
        "class_code": class_code,
        "strategy_id": strategy_id,
        "provider": provider,
        "opens": [float(b.open) for b in bars],
        "highs": [float(b.high) for b in bars],
        "lows": [float(b.low) for b in bars],
        "closes": [float(b.close) for b in bars],
        "volumes": [float(b.volume or 0.0) for b in bars],
        "dates": [candle_unix_ts(b.ts) for b in bars],
        "bars": len(bars),
        "pattern": (bars[0].raw or {}).get("pattern") if bars else None,
    }
