"""Attach control-plane auth tokens for local Operator → Engine bridge commands.

When ``TRADING_CONTROL_HMAC_SECRET`` (≥32 bytes) is set, every mutating command
gets a short-lived admin/risk/operator session token + unique nonce.  Without a
secret the engine may still accept local-dev authorizer (paper/operator UI).
"""

from __future__ import annotations

import os
import secrets
import time
from typing import Any

_AUTH_CMDS = frozenset(
    {
        "KILL",
        "HALT",
        "STOP",
        "EMERGENCY",
        "RESUME",
        "START",
        "SAVE_CONFIG",
        "SET_UNIVERSE",
        "SET_TRADING",
        "SET_ENSEMBLE",
        "SET_PLUGIN",
        "SET_STRATEGY_PARAMS",
        "SET_AI",
        "SET_SECRETS",
        "SET_STORAGE",
        "COST_SAVE_DRAFT",
        "COST_ACTIVATE",
        "RESET_PAPER",
        "CLEAR_PAPER",
        "RESET_SIM",
        "PHANTOM_RESET",
        "PHANTOM_CAPTURE",
        "PHANTOM_RUN",
        "PHANTOM_WALK_FORWARD",
        "PHANTOM_SYNC_METRICS",
        "PLACE_ORDER",
        "CLOSE_POSITION",
        "CLOSE_ALL",
        "CANCEL_ORDER",
        "RELEASE_EXIT",
        "RECONCILE_NOW",
    }
)

_cached_token: str | None = None
_cached_exp: float = 0.0
_cached_secret: str = ""


def control_secret() -> str:
    return str(os.environ.get("TRADING_CONTROL_HMAC_SECRET") or "")


def attach_control_auth(payload: dict[str, Any]) -> dict[str, Any]:
    """Mutate a copy of *payload* with auth_token/nonce when secret is available."""
    out = dict(payload)
    raw = str(out.get("cmd") or "").strip().upper()
    if raw and raw not in _AUTH_CMDS:
        return out
    if out.get("auth_token") or out.get("token"):
        if not out.get("nonce"):
            out["nonce"] = secrets.token_urlsafe(16)
        return out
    secret = control_secret()
    if len(secret.encode("utf-8")) < 32:
        return out
    token = _session_token(secret)
    if not token:
        return out
    out["auth_token"] = token
    out["nonce"] = secrets.token_urlsafe(16)
    return out


def _session_token(secret: str) -> str | None:
    global _cached_token, _cached_exp, _cached_secret
    now = time.time()
    if (
        _cached_token
        and _cached_secret == secret
        and now < _cached_exp - 60
    ):
        return _cached_token
    try:
        from engine.control_auth import HMACTokenAuth, Role
    except Exception:
        return None
    try:
        auth = HMACTokenAuth(secret)
        token = auth.issue(
            "local_operator_ui",
            [Role.ADMIN, Role.RISK, Role.OPERATOR],
            ttl_seconds=900,
        )
    except Exception:
        return None
    _cached_token = token
    _cached_secret = secret
    _cached_exp = now + 900
    return token
