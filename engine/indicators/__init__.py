"""Shared series helpers + optional SymPy formula layer (cb_math)."""

from __future__ import annotations

import math
from typing import Any, Sequence

__all__ = [
    "sma",
    "ema",
    "rsi",
    "macd",
    "bollinger",
    "roc",
    "atr",
    "log_returns",
    "realized_vol",
    "atr_pct",
    "max_drawdown",
    "equity_pnl",
    "FormulaSeries",
    "stochastic",
    "cci",
    "williams_r",
    "adx",
    "parabolic_sar",
    "heikin_ashi",
    "keltner",
    "typical_price",
    "rolling_vwap",
]


def sma(values: Sequence[float], window: int) -> list[float | None]:
    """Simple moving average; leading None until window filled."""
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
    """Exponential moving average (alpha = 2/(window+1))."""
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


def log_returns(closes: Sequence[float]) -> list[float]:
    rets: list[float] = []
    for i in range(1, len(closes)):
        a, b = float(closes[i - 1]), float(closes[i])
        if a > 0 and b > 0:
            rets.append(math.log(b / a))
    return rets


def realized_vol(closes: Sequence[float], *, annualize: float = 252.0) -> float | None:
    rets = log_returns(closes)
    if len(rets) < 5:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
    return math.sqrt(var) * math.sqrt(annualize)


def rsi(values: Sequence[float], window: int = 14) -> list[float | None]:
    """Wilder RSI; leading None until ``window`` changes available."""
    n = max(2, int(window))
    out: list[float | None] = [None] * len(values)
    if len(values) < n + 1:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, n + 1):
        delta = float(values[i]) - float(values[i - 1])
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / n
    avg_loss = losses / n
    if avg_loss <= 1e-12:
        out[n] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[n] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(n + 1, len(values)):
        delta = float(values[i]) - float(values[i - 1])
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = (avg_gain * (n - 1) + gain) / n
        avg_loss = (avg_loss * (n - 1) + loss) / n
        if avg_loss <= 1e-12:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def macd(
    values: Sequence[float],
    *,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """MACD line, signal line, histogram (all aligned to ``values``)."""
    f = max(2, int(fast))
    s = max(f + 1, int(slow))
    sig = max(1, int(signal))
    fast_ema = ema(values, f)
    slow_ema = ema(values, s)
    macd_line: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        a, b = fast_ema[i], slow_ema[i]
        if a is None or b is None:
            continue
        macd_line[i] = float(a) - float(b)
    # EMA over macd_line, skipping leading Nones by seeding at first value.
    signal_line: list[float | None] = [None] * len(values)
    hist: list[float | None] = [None] * len(values)
    alpha = 2.0 / (sig + 1.0)
    prev: float | None = None
    seen = 0
    for i, v in enumerate(macd_line):
        if v is None:
            continue
        if prev is None:
            prev = float(v)
        else:
            prev = alpha * float(v) + (1.0 - alpha) * prev
        seen += 1
        if seen >= sig:
            signal_line[i] = prev
            hist[i] = float(v) - float(prev)
    return macd_line, signal_line, hist


def bollinger(
    values: Sequence[float],
    window: int = 20,
    *,
    num_std: float = 2.0,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Mid (SMA), upper, lower Bollinger bands."""
    n = max(2, int(window))
    k = float(num_std)
    mid = sma(values, n)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    for i in range(n - 1, len(values)):
        m = mid[i]
        if m is None:
            continue
        sample = [float(x) for x in values[i - n + 1 : i + 1]]
        avg = sum(sample) / len(sample)
        var = sum((x - avg) ** 2 for x in sample) / max(len(sample) - 1, 1)
        std = math.sqrt(var)
        upper[i] = float(m) + k * std
        lower[i] = float(m) - k * std
    return mid, upper, lower


def roc(values: Sequence[float], window: int = 12) -> list[float | None]:
    """Rate of change: (close - close_n) / close_n."""
    n = max(1, int(window))
    out: list[float | None] = [None] * len(values)
    for i in range(n, len(values)):
        prev = float(values[i - n])
        if abs(prev) < 1e-12:
            continue
        out[i] = (float(values[i]) - prev) / prev
    return out


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    window: int = 14,
) -> list[float | None]:
    """Wilder ATR series (absolute price units)."""
    n = max(1, int(window))
    length = min(len(highs), len(lows), len(closes))
    out: list[float | None] = [None] * length
    if length < 2:
        return out
    trs: list[float] = []
    for i in range(length):
        h = float(highs[i])
        l = float(lows[i])
        if i == 0:
            trs.append(max(h - l, 1e-9))
        else:
            prev_c = float(closes[i - 1])
            trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c), 1e-9))
    if length < n:
        return out
    avg = sum(trs[:n]) / n
    out[n - 1] = avg
    for i in range(n, length):
        avg = (avg * (n - 1) + trs[i]) / n
        out[i] = avg
    return out


def atr_pct(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    window: int = 14,
) -> float | None:
    series = atr(highs, lows, closes, window)
    if not series or series[-1] is None or not closes:
        return None
    last = float(closes[-1])
    return float(series[-1]) / max(last, 1e-9)


def typical_price(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
) -> list[float]:
    n = min(len(highs), len(lows), len(closes))
    return [(float(highs[i]) + float(lows[i]) + float(closes[i])) / 3.0 for i in range(n)]


def stochastic(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    k_period: int = 14,
    d_period: int = 3,
) -> tuple[list[float | None], list[float | None]]:
    """Slow Stochastic %K / %D (SMA of %K)."""
    k_n = max(2, int(k_period))
    d_n = max(1, int(d_period))
    length = min(len(highs), len(lows), len(closes))
    raw_k: list[float | None] = [None] * length
    for i in range(k_n - 1, length):
        hh = max(float(highs[j]) for j in range(i - k_n + 1, i + 1))
        ll = min(float(lows[j]) for j in range(i - k_n + 1, i + 1))
        span = hh - ll
        if span <= 1e-12:
            raw_k[i] = 50.0
        else:
            raw_k[i] = 100.0 * (float(closes[i]) - ll) / span
    # %D = SMA of %K; also use smoothed %K as output %K (slow stoch).
    k_out = sma([0.0 if v is None else v for v in raw_k], d_n)
    # Restore leading Nones where raw_k was None.
    for i, v in enumerate(raw_k):
        if v is None:
            k_out[i] = None
    d_out = sma([0.0 if v is None else v for v in k_out], d_n)
    for i, v in enumerate(k_out):
        if v is None:
            d_out[i] = None
    return k_out, d_out


def cci(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    window: int = 20,
) -> list[float | None]:
    """Commodity Channel Index on typical price."""
    n = max(2, int(window))
    tp = typical_price(highs, lows, closes)
    out: list[float | None] = [None] * len(tp)
    mid = sma(tp, n)
    for i in range(n - 1, len(tp)):
        m = mid[i]
        if m is None:
            continue
        sample = tp[i - n + 1 : i + 1]
        mean_dev = sum(abs(x - float(m)) for x in sample) / n
        if mean_dev <= 1e-12:
            out[i] = 0.0
        else:
            out[i] = (float(tp[i]) - float(m)) / (0.015 * mean_dev)
    return out


def williams_r(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    window: int = 14,
) -> list[float | None]:
    """Williams %R in [-100, 0]."""
    n = max(2, int(window))
    length = min(len(highs), len(lows), len(closes))
    out: list[float | None] = [None] * length
    for i in range(n - 1, length):
        hh = max(float(highs[j]) for j in range(i - n + 1, i + 1))
        ll = min(float(lows[j]) for j in range(i - n + 1, i + 1))
        span = hh - ll
        if span <= 1e-12:
            out[i] = -50.0
        else:
            out[i] = -100.0 * (hh - float(closes[i])) / span
    return out


def adx(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    window: int = 14,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Wilder ADX with +DI / -DI series."""
    n = max(2, int(window))
    length = min(len(highs), len(lows), len(closes))
    plus_di: list[float | None] = [None] * length
    minus_di: list[float | None] = [None] * length
    adx_out: list[float | None] = [None] * length
    if length < n + 1:
        return adx_out, plus_di, minus_di

    tr_list: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for i in range(length):
        h = float(highs[i])
        l = float(lows[i])
        if i == 0:
            tr_list.append(max(h - l, 1e-9))
            plus_dm.append(0.0)
            minus_dm.append(0.0)
            continue
        prev_h, prev_l, prev_c = float(highs[i - 1]), float(lows[i - 1]), float(closes[i - 1])
        up = h - prev_h
        down = prev_l - l
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        tr_list.append(max(h - l, abs(h - prev_c), abs(l - prev_c), 1e-9))

    atr_s = sum(tr_list[1 : n + 1]) / n
    plus_s = sum(plus_dm[1 : n + 1]) / n
    minus_s = sum(minus_dm[1 : n + 1]) / n
    dx_vals: list[float | None] = [None] * length

    def _di(i: int, atr_v: float, p: float, m: float) -> None:
        if atr_v <= 1e-12:
            plus_di[i] = 0.0
            minus_di[i] = 0.0
            dx_vals[i] = 0.0
            return
        pdi = 100.0 * p / atr_v
        mdi = 100.0 * m / atr_v
        plus_di[i] = pdi
        minus_di[i] = mdi
        denom = pdi + mdi
        dx_vals[i] = 0.0 if denom <= 1e-12 else 100.0 * abs(pdi - mdi) / denom

    _di(n, atr_s, plus_s, minus_s)
    for i in range(n + 1, length):
        atr_s = (atr_s * (n - 1) + tr_list[i]) / n
        plus_s = (plus_s * (n - 1) + plus_dm[i]) / n
        minus_s = (minus_s * (n - 1) + minus_dm[i]) / n
        _di(i, atr_s, plus_s, minus_s)

    # ADX = Wilder smooth of DX
    seed_dx = [v for v in dx_vals[n : n * 2] if v is not None]
    if len(seed_dx) < n:
        return adx_out, plus_di, minus_di
    adx_s = sum(seed_dx[:n]) / n
    adx_out[n * 2 - 1] = adx_s
    for i in range(n * 2, length):
        dx = dx_vals[i]
        if dx is None:
            continue
        adx_s = (adx_s * (n - 1) + float(dx)) / n
        adx_out[i] = adx_s
    return adx_out, plus_di, minus_di


def parabolic_sar(
    highs: Sequence[float],
    lows: Sequence[float],
    *,
    af_start: float = 0.02,
    af_step: float = 0.02,
    af_max: float = 0.2,
) -> tuple[list[float | None], list[int | None]]:
    """Parabolic SAR prices and trend (+1 long / -1 short)."""
    length = min(len(highs), len(lows))
    sar: list[float | None] = [None] * length
    trend: list[int | None] = [None] * length
    if length < 2:
        return sar, trend
    af0 = max(1e-6, float(af_start))
    step = max(1e-6, float(af_step))
    af_cap = max(af0, float(af_max))
    # Start long if second bar higher.
    bull = float(highs[1]) >= float(highs[0])
    ep = float(highs[0]) if bull else float(lows[0])
    sar_v = float(lows[0]) if bull else float(highs[0])
    af = af0
    sar[0] = sar_v
    trend[0] = 1 if bull else -1
    for i in range(1, length):
        h = float(highs[i])
        l = float(lows[i])
        prev_h = float(highs[i - 1])
        prev_l = float(lows[i - 1])
        sar_v = sar_v + af * (ep - sar_v)
        if bull:
            sar_v = min(sar_v, prev_l, float(lows[i - 2]) if i >= 2 else prev_l)
            if l < sar_v:
                bull = False
                sar_v = ep
                ep = l
                af = af0
            else:
                if h > ep:
                    ep = h
                    af = min(af_cap, af + step)
        else:
            sar_v = max(sar_v, prev_h, float(highs[i - 2]) if i >= 2 else prev_h)
            if h > sar_v:
                bull = True
                sar_v = ep
                ep = h
                af = af0
            else:
                if l < ep:
                    ep = l
                    af = min(af_cap, af + step)
        sar[i] = sar_v
        trend[i] = 1 if bull else -1
    return sar, trend


def heikin_ashi(
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
) -> tuple[list[float], list[float], list[float], list[float]]:
    """Heikin-Ashi OHLC series."""
    n = min(len(opens), len(highs), len(lows), len(closes))
    ha_o: list[float] = []
    ha_h: list[float] = []
    ha_l: list[float] = []
    ha_c: list[float] = []
    for i in range(n):
        o, h, l, c = float(opens[i]), float(highs[i]), float(lows[i]), float(closes[i])
        hc = (o + h + l + c) / 4.0
        ho = (o + c) / 2.0 if i == 0 else (ha_o[i - 1] + ha_c[i - 1]) / 2.0
        hh = max(h, ho, hc)
        hl = min(l, ho, hc)
        ha_o.append(ho)
        ha_h.append(hh)
        ha_l.append(hl)
        ha_c.append(hc)
    return ha_o, ha_h, ha_l, ha_c


def keltner(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    ema_period: int = 20,
    atr_period: int = 10,
    mult: float = 1.5,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Keltner channel: EMA mid ± ATR*mult."""
    mid = ema(closes, max(2, int(ema_period)))
    atr_s = atr(highs, lows, closes, max(1, int(atr_period)))
    k = float(mult)
    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    for i, m in enumerate(mid):
        a = atr_s[i] if i < len(atr_s) else None
        if m is None or a is None:
            continue
        upper[i] = float(m) + k * float(a)
        lower[i] = float(m) - k * float(a)
    return mid, upper, lower


def rolling_vwap(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float] | None = None,
    window: int = 20,
) -> list[float | None]:
    """Rolling VWAP; if volume missing, equal-weight typical price SMA."""
    n = max(2, int(window))
    tp = typical_price(highs, lows, closes)
    length = len(tp)
    out: list[float | None] = [None] * length
    vols = list(volumes) if volumes is not None else [1.0] * length
    for i in range(n - 1, length):
        num = 0.0
        den = 0.0
        for j in range(i - n + 1, i + 1):
            v = float(vols[j]) if j < len(vols) else 1.0
            v = max(v, 0.0)
            num += tp[j] * v
            den += v
        out[i] = num / den if den > 1e-12 else tp[i]
    return out


def max_drawdown(equity: Sequence[float]) -> float:
    """Max drawdown as positive fraction (0.1 = −10%)."""
    if not equity:
        return 0.0
    peak = float(equity[0])
    worst = 0.0
    for v in equity:
        x = float(v)
        peak = max(peak, x)
        if peak > 0:
            worst = max(worst, (peak - x) / peak)
    return worst


def equity_pnl(
    fills: Sequence[dict[str, Any]],
    *,
    start_equity: float = 10_000.0,
    mark_price: float | None = None,
) -> dict[str, Any]:
    """
    Rough cash PnL from fills: action BUY/SELL, quantity, price.
    Tracks position and marks open inventory to ``mark_price`` (preferred)
    or the last fill price.

    Important: research windows often end mid-trade. Marking only at the last
    fill price zeroes unrealized PnL (pnl == −fees for a single open), which
    makes every strategy look uniformly fee-bankrupt on short OOS folds.
    """
    cash = float(start_equity)
    qty = 0.0
    curve = [cash]
    trades = 0
    last_fill_px = 0.0
    for f in fills:
        action = str(f.get("action") or "").upper()
        q = float(f.get("quantity") or 0)
        px = float(f.get("price") or 0)
        cost = float(f.get("cost") or f.get("total_cost") or 0)
        if q <= 0 or px <= 0:
            continue
        if action in {"BUY", "LONG"}:
            cash -= q * px
            qty += q
            if not f.get("carry"):
                trades += 1
        elif action in {"SELL", "SHORT", "CLOSE"}:
            cash += q * px
            qty -= q
            if not f.get("carry"):
                trades += 1
        else:
            continue
        cash -= max(0.0, cost)
        last_fill_px = px
        # Path marks at trade price; open inventory is revalued at window end below.
        curve.append(cash + qty * px)
    if mark_price is not None and float(mark_price) > 0:
        last_px = float(mark_price)
    else:
        last_px = last_fill_px
    final = cash + qty * last_px if last_px else cash
    if abs(qty) > 1e-12 and last_px > 0 and (
        not curve or abs(curve[-1] - final) > 1e-9
    ):
        curve.append(final)
    return {
        "start_equity": start_equity,
        "final_equity": final,
        "pnl": final - start_equity,
        "trades": trades,
        "position": qty,
        "max_drawdown": max_drawdown(curve),
        "equity_curve": curve,
        "mark_price": last_px,
    }


class FormulaSeries:
    """
    Evaluate a SymPy expression over aligned series via cb_math / lambdify.

    Example::

        FormulaSeries("a - b").eval({"a": closes, "b": sma(closes, 5)})
    """

    def __init__(self, expression: str) -> None:
        self.expression = expression.strip()
        self._fn = None

    def _compile(self, var_names: list[str]):
        try:
            from sympy import lambdify, symbols
            from sympy.parsing.sympy_parser import (
                convert_xor,
                implicit_multiplication_application,
                parse_expr,
                standard_transformations,
            )
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("sympy required for FormulaSeries") from exc

        transformations = standard_transformations + (
            implicit_multiplication_application,
            convert_xor,
        )
        syms = symbols(" ".join(var_names))
        if not isinstance(syms, tuple):
            syms = (syms,)
        local = {name: sym for name, sym in zip(var_names, syms)}
        expr = parse_expr(self.expression, local_dict=local, transformations=transformations)
        return lambdify(syms, expr, modules="math")

    def derive(self, variable: str = "x") -> str:
        """Return differentiated expression string via cb_math."""
        from cb_math import run_math_operation

        out = run_math_operation(
            domain="calculus",
            operation="diff",
            expression=self.expression,
            variable=variable,
        )
        if not out.get("success"):
            raise ValueError(out.get("error") or "diff failed")
        return str(out["result"])

    def eval(self, series: dict[str, Sequence[float | None]]) -> list[float | None]:
        if not series:
            return []
        names = sorted(series.keys())
        length = min(len(series[n]) for n in names)
        if self._fn is None:
            self._fn = self._compile(names)
        out: list[float | None] = []
        for i in range(length):
            args = []
            skip = False
            for n in names:
                v = series[n][i]
                if v is None:
                    skip = True
                    break
                args.append(float(v))
            if skip:
                out.append(None)
                continue
            try:
                out.append(float(self._fn(*args)))
            except Exception:
                out.append(None)
        return out

    def eval_point(self, variables: dict[str, float]) -> float:
        from cb_math import run_math_operation

        out = run_math_operation(
            domain="calculate",
            operation="eval",
            expression=self.expression,
            variables=variables,
        )
        if not out.get("success"):
            raise ValueError(out.get("error") or "eval failed")
        return float(out["numeric"])
