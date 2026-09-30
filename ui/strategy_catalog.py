"""Discover strategy plugins for Operator Consilium / Phantom."""

from __future__ import annotations

from typing import Any


_NAME_HINTS = (
    "cross",
    "reversion",
    "strategy",
    "ma_",
    "mean_",
    "momentum",
    "trend",
    "volatility",
    "breakout",
    "volume",
    "impulse",
    "rsi",
    "macd",
    "bollinger",
    "ema",
    "roc",
    "supertrend",
)


def looks_like_strategy_id(pid: str) -> bool:
    s = str(pid or "").lower()
    if not s or s in {"ai_analyst", "order_execution"}:
        return False
    return any(h in s for h in _NAME_HINTS)


def is_strategy_plugin_row(row: dict[str, Any]) -> bool:
    if row.get("is_strategy") is True:
        return True
    if row.get("is_strategy") is False:
        return False
    kind = str(row.get("kind") or "").lower()
    pid = str(row.get("id") or "")
    if "strategy" in kind:
        return True
    # BUSINESS plugins that look like strategies
    if kind in {"business", "pluginkind.business"} and looks_like_strategy_id(pid):
        return True
    return looks_like_strategy_id(pid)


def catalog_strategy_ids(snap: dict[str, Any]) -> list[str]:
    """Full strategy roster for Consilium (sorted, unique)."""
    seen: set[str] = set()
    out: list[str] = []

    def add(pid: Any) -> None:
        s = str(pid or "").strip()
        if not s or s in seen:
            return
        seen.add(s)
        out.append(s)

    for row in snap.get("plugins") or []:
        if isinstance(row, dict) and is_strategy_plugin_row(row):
            add(row.get("id"))

    ens = snap.get("ensemble") or {}
    cfg = ((snap.get("config") or {}).get("trading") or {}).get("ensemble") or {}
    for sid in ens.get("strategies") or cfg.get("strategies") or []:
        add(sid)
    for m in ens.get("members") or []:
        if isinstance(m, dict):
            add(m.get("strategy_id"))

    plugins_cfg = (snap.get("config") or {}).get("plugins") or {}
    if isinstance(plugins_cfg, dict):
        for pid in plugins_cfg:
            if looks_like_strategy_id(str(pid)):
                add(pid)

    return out


def active_consilium_ids(snap: dict[str, Any]) -> list[str]:
    """Strategies currently in the Consilium lineup (config / live)."""
    ens = snap.get("ensemble") or {}
    cfg = ((snap.get("config") or {}).get("trading") or {}).get("ensemble") or {}
    ids = [str(x) for x in (ens.get("strategies") or cfg.get("strategies") or []) if x]
    if ids:
        return ids
    # Engine treats empty list as “all strategies” — mirror that for UI defaults.
    return catalog_strategy_ids(snap)
