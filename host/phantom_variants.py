"""Phantom parameter grids / optimization search spaces."""

from __future__ import annotations

import itertools
from typing import Any, Mapping


def _tokens(strategy_id: str) -> set[str]:
    return {t for t in str(strategy_id or "").lower().replace("-", "_").split("_") if t}


def _ma_cross_grid() -> list[dict[str, Any]]:
    return [
        {"fast": 3, "slow": 8},
        {"fast": 5, "slow": 15},
        {"fast": 8, "slow": 21},
    ]


def _ema_cross_grid() -> list[dict[str, Any]]:
    return [
        {"fast": 8, "slow": 21},
        {"fast": 12, "slow": 26},
        {"fast": 20, "slow": 50},
    ]


_SKIP_SCHEMA_KEYS = frozenset(
    {
        "quantity",
        "class_code",
        "ticker",
        "symbol",
        "enabled",
        "name",
        "label",
    }
)


def _schema_levels(spec: Mapping[str, Any], *, dense: bool) -> list[Any] | None:
    """Discrete search levels for one CONFIG_SCHEMA field."""
    if not isinstance(spec, Mapping):
        return None
    typ = str(spec.get("type") or "").lower()
    if typ in {"str", "string", "bool", "boolean"}:
        return None
    if "enum" in spec and isinstance(spec["enum"], (list, tuple)) and spec["enum"]:
        return list(spec["enum"])
    if "choices" in spec and isinstance(spec["choices"], (list, tuple)) and spec["choices"]:
        return list(spec["choices"])

    default = spec.get("default")
    lo = spec.get("min")
    hi = spec.get("max")
    if lo is None or hi is None:
        if default is None:
            return None
        return [default]

    try:
        lo_f = float(lo)
        hi_f = float(hi)
    except (TypeError, ValueError):
        return [default] if default is not None else None
    if hi_f <= lo_f:
        return [default] if default is not None else [lo_f]

    n = 5 if dense else 3
    if typ in {"int", "integer"}:
        span = int(hi_f) - int(lo_f)
        if span <= 0:
            return [int(lo_f)]
        step = max(1, span // (n - 1))
        vals = list(range(int(lo_f), int(hi_f) + 1, step))
        if int(hi_f) not in vals:
            vals.append(int(hi_f))
        if default is not None:
            try:
                d = int(default)
                if d not in vals and lo_f <= d <= hi_f:
                    vals.append(d)
            except (TypeError, ValueError):
                pass
        return sorted(set(vals))[: max(n + 1, 3)]

    # float
    if n <= 1:
        return [float(default if default is not None else lo_f)]
    vals = [lo_f + (hi_f - lo_f) * i / (n - 1) for i in range(n)]
    if default is not None:
        try:
            d = float(default)
            if abs(d - min(vals, key=lambda x: abs(x - d))) > 1e-12 and lo_f <= d <= hi_f:
                vals.append(d)
        except (TypeError, ValueError):
            pass
    # keep readable precision
    out = []
    for v in sorted(vals):
        rounded = round(float(v), 6)
        if rounded not in out:
            out.append(rounded)
    return out[: max(n + 1, 3)]


def schema_search_variants(
    schema: Mapping[str, Any] | None,
    *,
    dense: bool = True,
    max_variants: int = 36,
) -> list[dict[str, Any]]:
    """Cartesian (truncated) search over numeric CONFIG_SCHEMA knobs."""
    if not isinstance(schema, Mapping) or not schema:
        return [{}]
    keys: list[str] = []
    axes: list[list[Any]] = []
    for key, spec in schema.items():
        if key in _SKIP_SCHEMA_KEYS:
            continue
        levels = _schema_levels(spec if isinstance(spec, Mapping) else {}, dense=dense)
        if not levels:
            continue
        keys.append(str(key))
        axes.append(levels)
    if not keys:
        return [{}]
    out: list[dict[str, Any]] = []
    for combo in itertools.product(*axes):
        out.append(dict(zip(keys, combo, strict=True)))
        if len(out) >= max(1, int(max_variants)):
            break
    return out or [{}]


def default_grid_variants(strategy_id: str) -> list[dict[str, Any]]:
    """Small discrete grid for walk-forward. Keys must match plugin CONFIG_SCHEMA."""
    sid = strategy_id.lower()
    tok = _tokens(sid)

    if "bollinger" in tok:
        return [
            {"window": 14, "num_std": 1.5},
            {"window": 20, "num_std": 2.0},
            {"window": 30, "num_std": 2.5},
        ]
    if "rsi" in tok:
        return [
            {"period": 10, "oversold": 25, "overbought": 75},
            {"period": 14, "oversold": 30, "overbought": 70},
            {"period": 21, "oversold": 35, "overbought": 65},
        ]
    if "macd" in tok:
        return [
            {"fast": 8, "slow": 17, "signal": 9},
            {"fast": 12, "slow": 26, "signal": 9},
            {"fast": 5, "slow": 35, "signal": 5},
        ]
    if "momentum" in tok or "roc" in tok:
        return [
            {"period": 8, "entry": 0.005},
            {"period": 12, "entry": 0.01},
            {"period": 20, "entry": 0.02},
        ]
    if "supertrend" in tok:
        return [
            {"atr_period": 7, "multiplier": 2.0},
            {"atr_period": 10, "multiplier": 3.0},
            {"atr_period": 14, "multiplier": 3.5},
        ]
    if "stochastic" in tok:
        return [
            {"k_period": 10, "d_period": 3, "oversold": 20.0, "overbought": 80.0},
            {"k_period": 14, "d_period": 3, "oversold": 20.0, "overbought": 80.0},
            {"k_period": 21, "d_period": 5, "oversold": 15.0, "overbought": 85.0},
        ]
    if "williams" in tok:
        return [
            {"period": 10, "oversold": -80.0, "overbought": -20.0},
            {"period": 14, "oversold": -80.0, "overbought": -20.0},
            {"period": 21, "oversold": -85.0, "overbought": -15.0},
        ]
    if "cci" in tok:
        return [
            {"period": 14, "entry": 100.0},
            {"period": 20, "entry": 100.0},
            {"period": 30, "entry": 150.0},
        ]
    if "vwap" in tok:
        return [
            {"window": 10, "entry_pct": 0.005},
            {"window": 20, "entry_pct": 0.01},
            {"window": 40, "entry_pct": 0.015},
        ]
    if "adx" in tok or "di_trend" in sid:
        return [
            {"period": 10, "adx_min": 20.0},
            {"period": 14, "adx_min": 25.0},
            {"period": 20, "adx_min": 30.0},
        ]
    if "parabolic" in tok or "sar" in tok:
        return [
            {"af_start": 0.02, "af_step": 0.02, "af_max": 0.2},
            {"af_start": 0.02, "af_step": 0.02, "af_max": 0.3},
            {"af_start": 0.01, "af_step": 0.01, "af_max": 0.2},
        ]
    if "heikin" in tok or "ashi" in tok:
        return [
            {"min_streak": 1},
            {"min_streak": 2},
            {"min_streak": 3},
        ]
    if "keltner" in tok:
        return [
            {"ema_period": 14, "atr_period": 10, "mult": 1.5},
            {"ema_period": 20, "atr_period": 10, "mult": 1.5},
            {"ema_period": 20, "atr_period": 14, "mult": 2.0},
        ]
    if "turtle" in tok:
        return [
            {"entry_lookback": 10, "exit_lookback": 5},
            {"entry_lookback": 20, "exit_lookback": 10},
            {"entry_lookback": 55, "exit_lookback": 20},
        ]
    if "dual" in tok and "thrust" in tok:
        return [
            {"lookback": 4, "k1": 0.5, "k2": 0.5},
            {"lookback": 8, "k1": 0.5, "k2": 0.5},
            {"lookback": 8, "k1": 0.7, "k2": 0.7},
        ]
    # mean_reversion before generic "reversion" tokens already handled above
    if "mean_reversion" in sid or ({"mean", "reversion"} <= tok):
        return [
            {"window": 10, "z_entry": 1.0},
            {"window": 20, "z_entry": 1.2},
            {"window": 30, "z_entry": 1.5},
        ]
    if "volatility_breakout" in sid or "vol_break" in sid:
        return [
            {"lookback": 10, "atr_mult": 0.25},
            {"lookback": 20, "atr_mult": 0.5},
            {"lookback": 30, "atr_mult": 1.0},
        ]
    if "volume_impulse" in sid or ("volume" in sid and "impulse" in sid):
        return [
            {"vol_window": 10, "vol_mult": 1.5},
            {"vol_window": 20, "vol_mult": 2.0},
            {"vol_window": 30, "vol_mult": 2.5},
        ]
    if "ema" in tok:
        return _ema_cross_grid()
    # Explicit MA / cross only — never use this as a silent fallback for
    # unrelated plugins (ADX/Keltner/… were getting fast/slow no-ops).
    if "ma" in tok or sid.endswith("_cross") or "cross" in tok:
        return _ma_cross_grid()

    # Unknown strategy: single default-config variant (empty overrides).
    return [{}]


def optimize_variants(
    strategy_id: str,
    *,
    max_variants: int = 36,
    schema: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Dense param search for one strategy (phantom optimization mode)."""
    sid = strategy_id.lower()
    out: list[dict[str, Any]] = []
    tok = _tokens(sid)

    def _push(item: dict[str, Any]) -> bool:
        out.append(item)
        return len(out) >= max_variants

    if "bollinger" in tok:
        for w in (10, 14, 20, 30, 40):
            for s in (1.5, 2.0, 2.5, 3.0):
                if _push({"window": int(w), "num_std": float(s)}):
                    return out
        return out

    if "rsi" in tok:
        for p in (7, 10, 14, 21):
            for lo, hi in ((20, 80), (25, 75), (30, 70), (35, 65)):
                if _push(
                    {
                        "period": int(p),
                        "oversold": float(lo),
                        "overbought": float(hi),
                    }
                ):
                    return out
        return out

    if "macd" in tok:
        for f, s, sig in ((8, 17, 9), (12, 26, 9), (5, 35, 5), (10, 30, 7)):
            if _push({"fast": f, "slow": s, "signal": sig}):
                return out
        return out

    if "momentum" in tok or "roc" in tok:
        for p in (5, 8, 12, 16, 20, 30):
            for e in (0.01, 0.015, 0.02, 0.03, 0.05):
                if _push({"period": int(p), "entry": float(e)}):
                    return out
        return out

    if "supertrend" in tok:
        for p in (7, 10, 14, 20):
            for m in (2.0, 2.5, 3.0, 3.5, 4.0):
                if _push({"atr_period": int(p), "multiplier": float(m)}):
                    return out
        return out

    if "stochastic" in tok:
        for k in (7, 10, 14, 21):
            for d in (3, 5):
                for lo, hi in ((20.0, 80.0), (15.0, 85.0)):
                    if _push(
                        {
                            "k_period": int(k),
                            "d_period": int(d),
                            "oversold": float(lo),
                            "overbought": float(hi),
                        }
                    ):
                        return out
        return out

    if "williams" in tok:
        for p in (7, 10, 14, 21, 28):
            for lo, hi in ((-80.0, -20.0), (-85.0, -15.0), (-75.0, -25.0)):
                if _push(
                    {
                        "period": int(p),
                        "oversold": float(lo),
                        "overbought": float(hi),
                    }
                ):
                    return out
        return out

    if "cci" in tok:
        for p in (10, 14, 20, 30):
            for e in (80.0, 100.0, 150.0, 200.0):
                if _push({"period": int(p), "entry": float(e)}):
                    return out
        return out

    if "vwap" in tok:
        for w in (10, 15, 20, 30, 40):
            for e in (0.005, 0.01, 0.015, 0.02):
                if _push({"window": int(w), "entry_pct": float(e)}):
                    return out
        return out

    if "adx" in tok or "di_trend" in sid:
        for p in (7, 10, 14, 20, 28):
            for m in (15.0, 20.0, 25.0, 30.0):
                if _push({"period": int(p), "adx_min": float(m)}):
                    return out
        return out

    if "parabolic" in tok or "sar" in tok:
        for start in (0.01, 0.02):
            for step in (0.01, 0.02):
                for mx in (0.15, 0.2, 0.3):
                    if _push(
                        {
                            "af_start": float(start),
                            "af_step": float(step),
                            "af_max": float(mx),
                        }
                    ):
                        return out
        return out

    if "heikin" in tok or "ashi" in tok:
        for streak in (1, 2, 3, 4, 5):
            if _push({"min_streak": int(streak)}):
                return out
        return out

    if "keltner" in tok:
        for ema_p in (10, 14, 20, 30):
            for atr_p in (10, 14):
                for mult in (1.0, 1.5, 2.0, 2.5):
                    if _push(
                        {
                            "ema_period": int(ema_p),
                            "atr_period": int(atr_p),
                            "mult": float(mult),
                        }
                    ):
                        return out
        return out

    if "turtle" in tok:
        for entry, exit_ in ((10, 5), (20, 10), (40, 15), (55, 20)):
            if _push(
                {
                    "entry_lookback": int(entry),
                    "exit_lookback": int(exit_),
                }
            ):
                return out
        return out

    if "dual" in tok and "thrust" in tok:
        for lb in (3, 4, 5, 8, 12):
            for k in (0.3, 0.5, 0.7, 1.0):
                if _push({"lookback": int(lb), "k1": float(k), "k2": float(k)}):
                    return out
        return out

    if "mean_reversion" in sid or ({"mean", "reversion"} <= tok):
        for w in (8, 12, 16, 20, 24, 30, 40):
            for z in (0.8, 1.0, 1.2, 1.5, 1.8, 2.0):
                if _push({"window": int(w), "z_entry": float(z)}):
                    return out
        return out

    if "volatility_breakout" in sid or "vol_break" in sid:
        for lb in (10, 15, 20, 30, 40):
            for m in (0.0, 0.25, 0.5, 0.75, 1.0, 1.5):
                if _push({"lookback": int(lb), "atr_mult": float(m)}):
                    return out
        return out

    if "volume_impulse" in sid or ("volume" in sid and "impulse" in sid):
        for w in (8, 12, 16, 20, 30, 40):
            for m in (1.5, 2.0, 2.5, 3.0, 4.0):
                if _push({"vol_window": int(w), "vol_mult": float(m)}):
                    return out
        return out

    if "ema" in tok:
        for f, s in ((8, 21), (12, 26), (20, 50), (5, 15), (10, 30)):
            if _push({"fast": int(f), "slow": int(s)}):
                return out
        return out

    if "ma" in tok or sid.endswith("_cross") or "cross" in tok:
        fasts = (2, 3, 4, 5, 6, 8, 10)
        slows = (6, 8, 10, 13, 15, 21, 34)
        for f in fasts:
            for s in slows:
                if s <= f:
                    continue
                if _push({"fast": int(f), "slow": int(s)}):
                    return out
        return out

    # Unknown strategy: schema-driven dense search when available.
    if schema:
        return schema_search_variants(schema, dense=True, max_variants=max_variants)
    return [{}]


def variants_for_mode(
    strategy_id: str,
    mode: str,
    *,
    max_variants: int = 36,
    schema: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    m = str(mode or "grid").lower()
    if m in {"optimize", "opt", "search", "dense"}:
        return optimize_variants(
            strategy_id, max_variants=max_variants, schema=schema
        )
    grid = default_grid_variants(strategy_id)
    if grid == [{}] and schema:
        return schema_search_variants(
            schema, dense=False, max_variants=min(12, max_variants)
        )
    return grid
