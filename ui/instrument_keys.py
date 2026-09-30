"""Pure instrument-key helpers (no Dear PyGui)."""

from __future__ import annotations

from typing import Any

DEFAULT_LIMIT = 80


def parse_instrument_key(key: str, *, default_class: str = "CRYPTO") -> tuple[str, str]:
    raw = str(key or "").strip()
    if ":" in raw:
        cc, sym = raw.split(":", 1)
        return cc.strip().upper(), sym.strip().upper()
    return default_class.upper(), raw.upper()


def search_haystack(key: str) -> str:
    cc, sym = parse_instrument_key(key)
    return f"{key} {cc} {sym}".lower()


def catalog_keys_from_snap(
    snap: dict[str, Any],
    *,
    prefer_active: bool = True,
    extra: list[str] | None = None,
) -> list[str]:
    seen: set[str] = set()
    keys: list[str] = []

    def _add(raw: Any) -> None:
        key = str(raw or "").strip()
        if not key or key in seen:
            return
        seen.add(key)
        keys.append(key)

    active = [str(k) for k in (snap.get("active_keys") or []) if str(k).strip()]
    if prefer_active:
        for key in active:
            _add(key)
    for row in snap.get("catalog") or []:
        if isinstance(row, dict):
            key = row.get("key")
            if not key:
                cc = str(row.get("class_code") or "").strip()
                sym = str(row.get("symbol") or "").strip()
                key = f"{cc}:{sym}" if cc and sym else sym
            _add(key)
        else:
            _add(row)
    for key in extra or []:
        _add(key)
    if not prefer_active:
        for key in active:
            _add(key)
    rest = [k for k in keys if k not in set(active)]
    return (active + rest) if prefer_active else keys


def filter_keys(
    keys: list[str],
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[str]:
    q = str(query or "").strip().lower()
    if not q:
        return list(keys[:limit])
    out: list[str] = []
    for key in keys:
        if q in search_haystack(key):
            out.append(key)
            if len(out) >= limit:
                break
    return out
