"""In-memory nested config helpers for operator UI (no Code_base change)."""

from __future__ import annotations

from copy import deepcopy
import os
from typing import Any

_SECRET_KEYS = {
    "api_key",
    "apikey",
    "secret",
    "secret_key",
    "password",
    "token",
    "access_token",
    "refresh_token",
}


def redact_secrets(value: Any) -> Any:
    """Return a UI-safe copy while preserving whether a secret is configured."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in _SECRET_KEYS:
                out[key] = "********" if item else ""
            else:
                out[key] = redact_secrets(item)
        return out
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return deepcopy(value)


def resolve_env_secrets(value: Any) -> Any:
    """Resolve ``*_env`` references in a copy for in-process consumers only."""
    if isinstance(value, dict):
        out = {key: resolve_env_secrets(item) for key, item in value.items()}
        for key, item in list(value.items()):
            if not str(key).endswith("_env") or not item:
                continue
            target = str(key)[: -len("_env")]
            if not out.get(target):
                out[target] = os.environ.get(str(item), "")
        return out
    if isinstance(value, list):
        return [resolve_env_secrets(item) for item in value]
    return deepcopy(value)


def get_path(cfg: dict[str, Any], path: str, default: Any = None) -> Any:
    cur: Any = cfg
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def set_path(cfg: dict[str, Any], path: str, value: Any) -> None:
    parts = [p for p in path.split(".") if p]
    if not parts:
        raise ValueError("empty config path")
    cur: dict[str, Any] = cfg
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def ensure_dict(cfg: dict[str, Any], path: str) -> dict[str, Any]:
    cur = get_path(cfg, path, None)
    if not isinstance(cur, dict):
        cur = {}
        set_path(cfg, path, cur)
    return cur


def deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = deepcopy(v)
    return dst


def operator_config_slice(raw: dict[str, Any]) -> dict[str, Any]:
    """Safe subset for UI snapshot (no secrets expansion beyond yaml)."""
    plugins_raw = raw.get("plugins") or {}
    plugin_blocks: dict[str, Any] = {}
    if isinstance(plugins_raw, dict):
        for k, v in plugins_raw.items():
            if k in {"paths", "installed_paths", "exclude"}:
                continue
            if isinstance(v, dict):
                plugin_blocks[k] = redact_secrets(v)

    services_raw = raw.get("services") or {}
    services_enabled: dict[str, Any] = {}
    if isinstance(services_raw, dict):
        for sid, block in services_raw.items():
            if sid in {"paths", "exclude"}:
                continue
            if isinstance(block, dict):
                services_enabled[sid] = {
                    "enabled": block.get("enabled", True),
                    "auto_start": block.get("auto_start", True),
                }
                if sid in {
                    "mock_market_service",
                    "ccxt_market_service",
                    "quik_market_service",
                    "alpaca_market_service",
                }:
                    for key in (
                        "exchange",
                        "class_code",
                        "tickers",
                        "transport",
                        "sandbox",
                        "feed",
                        "asset_class",
                        "paper",
                    ):
                        if key in block:
                            services_enabled[sid][key] = block[key]

    return {
        "trading": dict(raw.get("trading") or {}),
        "universe": dict(raw.get("universe") or {}),
        "market": dict(raw.get("market") or {}),
        "core": {
            "name": (raw.get("core") or {}).get("name"),
            "version": (raw.get("core") or {}).get("version"),
            "log_level": (raw.get("core") or {}).get("log_level"),
        },
        "plugins": plugin_blocks,
        "services": services_enabled,
    }
