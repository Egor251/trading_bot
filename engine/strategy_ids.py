"""Resolve short / aliased strategy ids to loaded plugin ids."""

from __future__ import annotations

from typing import Any, Callable

# Common short names used in AI/batch prompts → real PLUGIN_ID.
_ALIASES: dict[str, str] = {
    "ma_cross": "ma_cross_example",
    "ma": "ma_cross_example",
    "mean_reversion": "mean_reversion_example",
    "mean_rev": "mean_reversion_example",
    "vol_breakout": "volatility_breakout_example",
    "volatility_breakout": "volatility_breakout_example",
    "volume_impulse": "volume_impulse_example",
    "vol_impulse": "volume_impulse_example",
}


def normalize_strategy_id(strategy_id: str | None) -> str:
    return str(strategy_id or "").strip()


def resolve_strategy_id(
    strategy_id: str | None,
    *,
    is_loaded: Callable[[str], bool] | None = None,
    loaded_ids: list[str] | None = None,
) -> str:
    """
    Map operator/AI shorthand to a concrete plugin id.

    Order: exact → alias → ``*_example`` → unique prefix among loaded ids.
    """
    raw = normalize_strategy_id(strategy_id)
    if not raw:
        return raw

    def _loaded(sid: str) -> bool:
        if is_loaded is not None:
            return bool(is_loaded(sid))
        if loaded_ids is not None:
            return sid in loaded_ids
        return True  # no registry ⇒ trust alias map only

    if _loaded(raw) and (is_loaded is not None or loaded_ids is not None):
        return raw
    if is_loaded is None and loaded_ids is None:
        # Offline / no plugin manager: still apply aliases.
        aliased = _ALIASES.get(raw.lower())
        if aliased:
            return aliased
        if not raw.endswith("_example"):
            return f"{raw}_example"
        return raw

    if _loaded(raw):
        return raw

    aliased = _ALIASES.get(raw.lower())
    if aliased and _loaded(aliased):
        return aliased

    if not raw.endswith("_example"):
        candidate = f"{raw}_example"
        if _loaded(candidate):
            return candidate

    # Unique prefix match among loaded plugins (e.g. "bollinger" → bollinger_reversion).
    pool = list(loaded_ids or [])
    if not pool and is_loaded is not None:
        # Cannot enumerate; stop after alias/_example.
        return aliased or raw
    key = raw.lower()
    hits = [sid for sid in pool if sid.lower() == key or sid.lower().startswith(key + "_")]
    if len(hits) == 1:
        return hits[0]
    return aliased or raw


def resolve_strategy_id_via_core(core: Any, strategy_id: str | None) -> str:
    """Resolve using ``core.plugin_manager`` when available."""
    pm = getattr(core, "plugin_manager", None) if core is not None else None
    loaded: list[str] = []
    if pm is not None:
        try:
            if hasattr(pm, "list_ids"):
                loaded = [str(x) for x in (pm.list_ids() or [])]
            elif hasattr(pm, "_instances"):
                loaded = [str(k) for k in getattr(pm, "_instances", {})]
        except Exception:
            loaded = []

        def _is_loaded(sid: str) -> bool:
            if hasattr(pm, "get") and pm.get(sid) is not None:
                return True
            return sid in loaded

        return resolve_strategy_id(
            strategy_id, is_loaded=_is_loaded, loaded_ids=loaded
        )
    return resolve_strategy_id(strategy_id)
