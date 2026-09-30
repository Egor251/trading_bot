"""Strategy visual recipes — MA cross, mean-reversion bands, etc."""

from __future__ import annotations

from typing import Any

from ui.chart_math import sma
from ui.i18n import tr


def plugin_params(snap: dict[str, Any], strategy_id: str) -> dict[str, Any]:
    plugins = (snap.get("config") or {}).get("plugins") or {}
    block = plugins.get(strategy_id)
    return dict(block) if isinstance(block, dict) else {}


def member_by_id(
    snap: dict[str, Any], strategy_id: str
) -> dict[str, Any] | None:
    ens = snap.get("ensemble") or {}
    for m in ens.get("members") or []:
        if str(m.get("strategy_id") or "") == strategy_id:
            return dict(m)
    return None


def describe_lines(strategy_id: str, metrics: dict[str, Any] | None = None) -> list[str]:
    """Plain-language copy for the Strategy page (i18n)."""
    sid = strategy_id.lower()
    metrics = metrics or {}
    if "ma_cross" in sid or ("fast" in metrics and "slow" in metrics):
        fast = metrics.get("fast") or "—"
        slow = metrics.get("slow") or "—"
        return [
            tr("strat.desc.ma_cross.1"),
            tr("strat.desc.ma_cross.2", fast=fast, slow=slow),
            tr("strat.desc.ma_cross.3"),
            tr("strat.desc.ma_cross.4"),
        ]
    if "mean_reversion" in sid or "z" in metrics or "window" in metrics:
        window = metrics.get("window") or "—"
        z = metrics.get("z") or metrics.get("z_entry") or "—"
        return [
            tr("strat.desc.mr.1"),
            tr("strat.desc.mr.2", window=window),
            tr("strat.desc.mr.3", z=z),
            tr("strat.desc.mr.4"),
        ]
    if "test_strategy" in sid:
        return [tr("strat.desc.test")]
    return [tr("strat.desc.generic")]


def has_chart_visuals(strategy_id: str, metrics: dict[str, Any] | None = None) -> bool:
    sid = strategy_id.lower()
    metrics = metrics or {}
    if "ma_cross" in sid or ("fast" in metrics and "slow" in metrics):
        return True
    if "mean_reversion" in sid or "window" in metrics:
        return True
    return False


def overlay_series(
    *,
    strategy_id: str,
    closes: list[float],
    params: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return line overlays: [{key, label, ys}]. ys aligned with closes (None allowed)."""
    if not closes:
        return []
    params = params or {}
    metrics = metrics or {}
    sid = strategy_id.lower()
    out: list[dict[str, Any]] = []

    if "ma_cross" in sid or (
        ("fast" in params or "fast" in metrics)
        and ("slow" in params or "slow" in metrics)
    ):
        try:
            fast = int(params.get("fast") or metrics.get("fast") or 3)
        except (TypeError, ValueError):
            fast = 3
        try:
            slow = int(params.get("slow") or metrics.get("slow") or 8)
        except (TypeError, ValueError):
            slow = 8
        if slow <= fast:
            slow = fast + 1
        out.append(
            {
                "key": "fast",
                "label": tr("strat.overlay.sma_fast", id=strategy_id, n=fast),
                "ys": sma(closes, fast),
            }
        )
        out.append(
            {
                "key": "slow",
                "label": tr("strat.overlay.sma_slow", id=strategy_id, n=slow),
                "ys": sma(closes, slow),
            }
        )
        return out

    if "mean_reversion" in sid or "window" in params or "window" in metrics:
        try:
            window = int(params.get("window") or metrics.get("window") or 20)
        except (TypeError, ValueError):
            window = 20
        try:
            z_entry = float(
                params.get("z_entry")
                or metrics.get("z_entry")
                or metrics.get("z")
                or 1.2
            )
        except (TypeError, ValueError):
            z_entry = 1.2
        mean = sma(closes, window)
        out.append(
            {
                "key": "mean",
                "label": tr("strat.overlay.mean", id=strategy_id, n=window),
                "ys": mean,
            }
        )
        up: list[float | None] = [None] * len(closes)
        lo: list[float | None] = [None] * len(closes)
        for i in range(len(closes)):
            if i < window - 1 or mean[i] is None:
                continue
            chunk = closes[i - window + 1 : i + 1]
            m = float(mean[i])
            var = sum((float(x) - m) ** 2 for x in chunk) / max(len(chunk), 1)
            std = var**0.5
            up[i] = m + z_entry * std
            lo[i] = m - z_entry * std
        out.append(
            {
                "key": "band_up",
                "label": tr("strat.overlay.band_up", id=strategy_id, z=f"{z_entry:g}"),
                "ys": up,
            }
        )
        out.append(
            {
                "key": "band_lo",
                "label": tr("strat.overlay.band_lo", id=strategy_id, z=f"{z_entry:g}"),
                "ys": lo,
            }
        )
        return out

    return out


def find_cross_indices(
    fast: list[float | None], slow: list[float | None]
) -> list[tuple[int, str]]:
    """Return (index, BUY|SELL) where fast crosses slow."""
    crosses: list[tuple[int, str]] = []
    prev: str | None = None
    n = min(len(fast), len(slow))
    for i in range(n):
        a, b = fast[i], slow[i]
        if a is None or b is None:
            continue
        side = "BUY" if float(a) > float(b) else "SELL"
        if prev is not None and side != prev:
            crosses.append((i, side))
        prev = side
    return crosses


def signal_points(
    *,
    strategy_id: str,
    closes: list[float],
    params: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, list[tuple[int, float]]]:
    """BUY/SELL marker positions for explaining strategy behaviour.

    Returns ``{"BUY": [(bar_index, price), ...], "SELL": [...]}``.
    """
    params = params or {}
    metrics = metrics or {}
    sid = strategy_id.lower()
    buys: list[tuple[int, float]] = []
    sells: list[tuple[int, float]] = []
    if not closes:
        return {"BUY": buys, "SELL": sells}

    if "ma_cross" in sid or (
        ("fast" in params or "fast" in metrics)
        and ("slow" in params or "slow" in metrics)
    ):
        try:
            fast = int(params.get("fast") or metrics.get("fast") or 3)
        except (TypeError, ValueError):
            fast = 3
        try:
            slow = int(params.get("slow") or metrics.get("slow") or 8)
        except (TypeError, ValueError):
            slow = 8
        if slow <= fast:
            slow = fast + 1
        f_s = sma(closes, fast)
        s_s = sma(closes, slow)
        for i, side in find_cross_indices(f_s, s_s):
            px = float(closes[i])
            if side == "BUY":
                buys.append((i, px))
            else:
                sells.append((i, px))
        return {"BUY": buys, "SELL": sells}

    if "mean_reversion" in sid or "window" in params or "window" in metrics:
        try:
            window = int(params.get("window") or metrics.get("window") or 20)
        except (TypeError, ValueError):
            window = 20
        try:
            z_entry = float(
                params.get("z_entry")
                or metrics.get("z_entry")
                or metrics.get("z")
                or 1.2
            )
        except (TypeError, ValueError):
            z_entry = 1.2
        mean = sma(closes, window)
        prev: str | None = None
        for i, close in enumerate(closes):
            if i < window - 1 or mean[i] is None:
                continue
            chunk = closes[i - window + 1 : i + 1]
            m = float(mean[i])
            var = sum((float(x) - m) ** 2 for x in chunk) / max(len(chunk), 1)
            std = var**0.5
            up = m + z_entry * std
            lo = m - z_entry * std
            px = float(close)
            if px <= lo:
                side = "BUY"
            elif px >= up:
                side = "SELL"
            else:
                side = "HOLD"
            if side in {"BUY", "SELL"} and side != prev:
                if side == "BUY":
                    buys.append((i, px))
                else:
                    sells.append((i, px))
                prev = side
            elif side == "HOLD":
                prev = "HOLD"
        return {"BUY": buys, "SELL": sells}

    return {"BUY": buys, "SELL": sells}


def markers_in_view(
    points: list[tuple[int, float]],
    *,
    start: int,
    end: int,
    dates: list[float],
) -> tuple[list[float], list[float]]:
    """Map absolute bar indices to visible plot X (same coords as candles)."""
    xs: list[float] = []
    ys: list[float] = []
    for i, price in points:
        if i < start or i >= end:
            continue
        local = i - start
        if 0 <= local < len(dates):
            xs.append(float(dates[local]))
        else:
            xs.append(float(i))
        ys.append(float(price))
    return xs, ys


def stance_label(stance: str) -> str:
    key = str(stance or "neutral").lower()
    mapped = {
        "for": "strat.stance.for",
        "against": "strat.stance.against",
        "neutral": "strat.stance.neutral",
    }
    return tr(mapped.get(key, "strat.stance.neutral"))


def param_label(key: str) -> str:
    mapped = {
        "fast": "strat.param.fast",
        "slow": "strat.param.slow",
        "window": "strat.param.window",
        "z_entry": "strat.param.z_entry",
        "quantity": "strat.param.quantity",
        "class_code": "strat.param.class_code",
        "tickers": "strat.param.tickers",
    }
    return tr(mapped[key]) if key in mapped else key


def metric_label(key: str) -> str:
    mapped = {
        "fast": "strat.metric.fast",
        "slow": "strat.metric.slow",
        "fast_ma": "strat.metric.fast_ma",
        "slow_ma": "strat.metric.slow_ma",
        "close": "strat.metric.close",
        "window": "strat.metric.window",
        "z": "strat.metric.z",
        "z_entry": "strat.metric.z_entry",
        "mean": "strat.metric.mean",
    }
    return tr(mapped[key]) if key in mapped else key
