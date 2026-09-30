"""Pure chart math: aggregate TF, Heikin Ashi, overlays, oscillators.

Self-contained (no ``engine`` import) so Operator UI can load without
microkernel_core on sys.path during early imports.
"""

from __future__ import annotations

import math
from typing import Sequence

# Minutes per bar when source feed is 1m (our live default).
TF_MINUTES: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "1h": 60,
    "4h": 240,
    "1D": 1440,
}

TF_LABELS = tuple(TF_MINUTES.keys())
CHART_TYPES = ("Candles", "Line", "Heikin")
BOTTOM_MODES = ("Volume", "RSI", "MACD", "ΔP")
# Persisted aliases → canonical chart types.
CHART_TYPE_ALIASES: dict[str, str] = {
    "OHLC Range": "Candles",
    "Area": "Line",
}


def sma(values: Sequence[float], window: int) -> list[float | None]:
    n = max(1, int(window))
    out: list[float | None] = [None] * len(values)
    if n <= 0 or not values:
        return out
    total = 0.0
    for i, v in enumerate(values):
        total += float(v)
        if i >= n:
            total -= float(values[i - n])
        if i >= n - 1:
            out[i] = total / n
    return out


def ema(values: Sequence[float], window: int) -> list[float | None]:
    n = max(1, int(window))
    out: list[float | None] = [None] * len(values)
    if not values:
        return out
    alpha = 2.0 / (n + 1.0)
    prev: float | None = None
    for i, v in enumerate(values):
        x = float(v)
        if prev is None:
            prev = x
        else:
            prev = alpha * x + (1.0 - alpha) * prev
        if i >= n - 1:
            out[i] = prev
    return out


def _as_float_lists(ohlc: dict[str, list[float]]) -> dict[str, list[float]]:
    n = len(ohlc.get("closes") or [])
    opens = list(ohlc.get("opens") or [])
    highs = list(ohlc.get("highs") or [])
    lows = list(ohlc.get("lows") or [])
    closes = list(ohlc.get("closes") or [])
    vols = list(ohlc.get("volumes") or [])
    dates = list(ohlc.get("dates") or [])
    if len(opens) < n:
        opens = list(closes)
    if len(highs) < n:
        highs = list(closes)
    if len(lows) < n:
        lows = list(closes)
    if len(vols) < n:
        vols = [0.0] * n
    opens, highs, lows, closes, vols = (
        opens[:n],
        highs[:n],
        lows[:n],
        closes[:n],
        vols[:n],
    )
    if len(dates) < n:
        # Synthetic 1m unix timeline ending "now" when feed has no stamps
        import time

        end = time.time()
        dates = [end - (n - 1 - i) * 60.0 for i in range(n)]
    else:
        dates = [float(x) for x in dates[:n]]
    return {
        "dates": dates,
        "opens": [float(x) for x in opens],
        "highs": [float(x) for x in highs],
        "lows": [float(x) for x in lows],
        "closes": [float(x) for x in closes],
        "volumes": [float(x) for x in vols],
    }


def aggregate_ohlc(ohlc: dict[str, list[float]], *, factor: int) -> dict[str, list[float]]:
    """Collapse every ``factor`` source bars into one higher-TF candle."""
    src = _as_float_lists(ohlc)
    f = max(1, int(factor))
    if f == 1:
        return src
    n = len(src["closes"])
    if n == 0:
        return src
    out: dict[str, list[float]] = {
        "dates": [],
        "opens": [],
        "highs": [],
        "lows": [],
        "closes": [],
        "volumes": [],
    }
    for i in range(0, n, f):
        chunk = slice(i, min(i + f, n))
        o = src["opens"][chunk]
        h = src["highs"][chunk]
        l = src["lows"][chunk]
        c = src["closes"][chunk]
        v = src["volumes"][chunk]
        d = src["dates"][chunk]
        out["dates"].append(d[-1])
        out["opens"].append(o[0])
        out["highs"].append(max(h))
        out["lows"].append(min(l))
        out["closes"].append(c[-1])
        out["volumes"].append(sum(v))
    return out


def heikin_ashi(ohlc: dict[str, list[float]]) -> dict[str, list[float]]:
    src = _as_float_lists(ohlc)
    n = len(src["closes"])
    if n == 0:
        return src
    ha_o: list[float] = []
    ha_h: list[float] = []
    ha_l: list[float] = []
    ha_c: list[float] = []
    for i in range(n):
        o, h, l, c = src["opens"][i], src["highs"][i], src["lows"][i], src["closes"][i]
        close = (o + h + l + c) / 4.0
        if i == 0:
            open_ = (o + c) / 2.0
        else:
            open_ = (ha_o[i - 1] + ha_c[i - 1]) / 2.0
        high = max(h, open_, close)
        low = min(l, open_, close)
        ha_o.append(open_)
        ha_h.append(high)
        ha_l.append(low)
        ha_c.append(close)
    return {
        "dates": list(src["dates"]),
        "opens": ha_o,
        "highs": ha_h,
        "lows": ha_l,
        "closes": ha_c,
        "volumes": list(src["volumes"]),
    }


def ensure_visible_wicks(ohlc: dict[str, list[float]]) -> dict[str, list[float]]:
    """When feed is flat close-only, invent tiny wicks so candles render."""
    src = _as_float_lists(ohlc)
    n = len(src["closes"])
    if n < 2:
        return src
    if any(abs(h - l) > 1e-12 for h, l in zip(src["highs"], src["lows"])):
        return src
    opens = list(src["opens"])
    highs = list(src["highs"])
    lows = list(src["lows"])
    closes = src["closes"]
    for i in range(n):
        pad = max(abs(closes[i]) * 0.0015, 0.01)
        opens[i] = closes[i - 1] if i else closes[i]
        highs[i] = max(opens[i], closes[i]) + pad
        lows[i] = min(opens[i], closes[i]) - pad
    return {
        "dates": list(src["dates"]),
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "closes": closes,
        "volumes": list(src["volumes"]),
    }


def aligned_series(
    values: Sequence[float | None],
    dates: Sequence[float],
) -> tuple[list[float], list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    for x, v in zip(dates, values):
        if v is None:
            continue
        try:
            y = float(v)
        except (TypeError, ValueError):
            continue
        if math.isnan(y) or math.isinf(y):
            continue
        xs.append(float(x))
        ys.append(y)
    return xs, ys


def bollinger(
    closes: Sequence[float],
    *,
    window: int = 20,
    k: float = 2.0,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    mid = sma(closes, window)
    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    n = max(1, int(window))
    for i in range(len(closes)):
        if mid[i] is None or i < n - 1:
            continue
        window_vals = [float(x) for x in closes[i - n + 1 : i + 1]]
        mean = float(mid[i])
        var = sum((x - mean) ** 2 for x in window_vals) / n
        std = math.sqrt(var)
        upper[i] = mean + k * std
        lower[i] = mean - k * std
    return mid, upper, lower


def rsi(closes: Sequence[float], *, period: int = 14) -> list[float | None]:
    """Wilder RSI; leading None until warm-up."""
    n = max(1, int(period))
    out: list[float | None] = [None] * len(closes)
    if len(closes) < n + 1:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, n + 1):
        d = float(closes[i]) - float(closes[i - 1])
        if d >= 0:
            gains += d
        else:
            losses -= d
    avg_gain = gains / n
    avg_loss = losses / n
    if avg_loss <= 1e-12:
        out[n] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[n] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(n + 1, len(closes)):
        d = float(closes[i]) - float(closes[i - 1])
        gain = max(d, 0.0)
        loss = max(-d, 0.0)
        avg_gain = (avg_gain * (n - 1) + gain) / n
        avg_loss = (avg_loss * (n - 1) + loss) / n
        if avg_loss <= 1e-12:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def macd(
    closes: Sequence[float],
    *,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    fast_e = ema(closes, fast)
    slow_e = ema(closes, slow)
    line: list[float | None] = [None] * len(closes)
    for i, (a, b) in enumerate(zip(fast_e, slow_e)):
        if a is None or b is None:
            continue
        line[i] = float(a) - float(b)
    # EMA of MACD line — seed with first non-null
    sig: list[float | None] = [None] * len(closes)
    hist: list[float | None] = [None] * len(closes)
    alpha = 2.0 / (max(1, signal) + 1.0)
    prev: float | None = None
    warm = 0
    for i, v in enumerate(line):
        if v is None:
            continue
        if prev is None:
            prev = float(v)
        else:
            prev = alpha * float(v) + (1.0 - alpha) * prev
        warm += 1
        if warm >= signal:
            sig[i] = prev
            hist[i] = float(v) - prev
    return line, sig, hist


def price_change_pct(closes: Sequence[float]) -> float | None:
    if len(closes) < 2:
        return None
    a, b = float(closes[-2]), float(closes[-1])
    if abs(a) < 1e-12:
        return None
    return (b - a) / a * 100.0


# Lookback windows for Quotes Chg% (seconds).
CHG_PERIOD_SEC: dict[str, float] = {
    "1h": 3600.0,
    "4h": 4 * 3600.0,
    "1D": 24 * 3600.0,
    "1W": 7 * 24 * 3600.0,
    "1M": 30 * 24 * 3600.0,
}
CHG_PERIODS = tuple(CHG_PERIOD_SEC.keys())


def price_change_pct_over(
    closes: Sequence[float],
    dates: Sequence[float] | None = None,
    *,
    lookback_sec: float,
) -> float | None:
    """% change from the bar nearest to ``now - lookback`` (or oldest usable)."""
    if len(closes) < 2:
        return None
    last = float(closes[-1])
    if dates is None or len(dates) != len(closes):
        # Fallback: approximate bars as 1m
        bars = max(1, int(lookback_sec / 60.0))
        idx = max(0, len(closes) - 1 - bars)
        base = float(closes[idx])
    else:
        t_end = float(dates[-1])
        t_target = t_end - float(lookback_sec)
        span = t_end - float(dates[0])
        # Allow partial windows (seed may be shorter than 1D/1M) once we have
        # a meaningful stretch of history.
        if span < min(lookback_sec * 0.15, 15 * 60.0):
            return None
        base = float(closes[0])
        for i in range(len(dates) - 1, -1, -1):
            if float(dates[i]) <= t_target:
                base = float(closes[i])
                break
    if abs(base) < 1e-12:
        return None
    return (last - base) / base * 100.0


def slice_ohlc(
    ohlc: dict[str, list[float]],
    *,
    start: int,
    end: int,
) -> dict[str, list[float]]:
    src = _as_float_lists(ohlc)
    n = len(src["closes"])
    if n == 0:
        return src
    s = max(0, min(int(start), n))
    e = max(s, min(int(end), n))
    return {k: v[s:e] for k, v in src.items()}


def y_limits(lows: Sequence[float], highs: Sequence[float], *, pad: float = 0.004) -> tuple[float, float]:
    if not lows or not highs:
        return 0.0, 1.0
    lo = min(float(x) for x in lows)
    hi = max(float(x) for x in highs)
    if hi <= lo:
        hi = lo + max(abs(lo) * 0.01, 1.0)
    span = hi - lo
    return lo - span * pad, hi + span * pad
