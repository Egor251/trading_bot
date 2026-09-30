"""Lightweight Operator i18n — JSON catalogs, no Qt dependency."""

from __future__ import annotations

import json
import locale
import os
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
_LOCALES_DIR = Path(__file__).resolve().parent / "locales"
_PREFS_PATH = ROOT / "data" / "ui" / "prefs.json"

_SUPPORTED = ("en", "ru")
_lang = "en"
_catalog: dict[str, str] = {}
_fallback: dict[str, str] = {}
_on_change: list[Callable[[str], None]] = []


def supported_langs() -> tuple[str, ...]:
    return _SUPPORTED


def current_lang() -> str:
    return _lang


def _detect_os_lang() -> str:
    for key in ("APHINA_UI_LANG", "UI_LANG", "LANG"):
        raw = (os.environ.get(key) or "").strip().lower()
        if raw.startswith("ru"):
            return "ru"
        if raw.startswith("en"):
            return "en"
    try:
        loc = (locale.getdefaultlocale()[0] or "").lower()
    except Exception:
        loc = ""
    if loc.startswith("ru"):
        return "ru"
    return "en"


def _load_json(code: str) -> dict[str, str]:
    path = _LOCALES_DIR / f"{code}.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _read_prefs() -> dict[str, Any]:
    try:
        if _PREFS_PATH.is_file():
            raw = json.loads(_PREFS_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
    except Exception:
        pass
    return {}


def _write_prefs(data: dict[str, Any]) -> None:
    try:
        _PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        cur = _read_prefs()
        cur.update(data)
        _PREFS_PATH.write_text(json.dumps(cur, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def load(lang: str | None = None) -> str:
    """Load language catalog. Prefer saved prefs, then explicit, then OS."""
    global _lang, _catalog, _fallback
    prefs = _read_prefs()
    saved = str(prefs.get("lang") or "").strip().lower()
    code = (lang or saved or _detect_os_lang()).strip().lower()
    if code not in _SUPPORTED:
        code = "en"
    _fallback = _load_json("en")
    _catalog = _load_json(code) if code != "en" else dict(_fallback)
    if code != "en":
        # fill missing keys from English
        for k, v in _fallback.items():
            _catalog.setdefault(k, v)
    _lang = code
    return _lang


def set_lang(code: str, *, persist: bool = True) -> str:
    code = (code or "en").strip().lower()
    if code not in _SUPPORTED:
        code = "en"
    load(code)
    if persist:
        _write_prefs({"lang": _lang})
    for cb in list(_on_change):
        try:
            cb(_lang)
        except Exception:
            pass
    return _lang


def on_change(cb: Callable[[str], None]) -> None:
    _on_change.append(cb)


def tr(key: str, **kwargs: Any) -> str:
    if not _catalog and not _fallback:
        load()
    text = _catalog.get(key) or _fallback.get(key) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text


# Boot with saved / OS language
load()
