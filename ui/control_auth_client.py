"""Attach control-plane auth tokens for local Operator → Engine bridge commands.

When ``TRADING_CONTROL_HMAC_SECRET`` (≥32 bytes) is set, every mutating command
gets a short-lived session token + unique nonce.  Without a secret the engine
may still accept the explicit local-dev authorizer (paper/operator UI).

The command set is imported from :mod:`engine.control_commands` rather than
duplicated.  The previous hand-maintained copy had already drifted: it was
missing ``CANCEL_ALL_ORDERS``, ``PHANTOM_APPLY_CHOSEN`` and
``PHANTOM_WALK_FORWARD_BATCH``, so with HMAC auth enabled those three commands
— including the operator's cancel-all button — were rejected every time.
"""

from __future__ import annotations

import os
import secrets
import sys
import time
from typing import Any

_ROOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, _ROOT)

from engine.control_commands import (  # noqa: E402
    READ_ONLY_COMMANDS,
    requires_authorization,
)

_cached_token: str | None = None
_cached_exp: float = 0.0
_cached_secret: str = ""
_cached_roles: tuple[str, ...] = ()


def control_secret() -> str:
    return str(os.environ.get("TRADING_CONTROL_HMAC_SECRET") or "")


def _role_key(roles: list[Any] | None) -> tuple[str, ...]:
    if roles is None:
        return ("__default__",)
    return tuple(sorted(str(getattr(r, "value", r)) for r in roles))


def reset_token_cache() -> None:
    """Drop the cached session. Used by tests and on secret rotation."""
    global _cached_token, _cached_exp, _cached_secret, _cached_roles
    _cached_token = None
    _cached_exp = 0.0
    _cached_secret = ""
    _cached_roles = ()


def attach_control_auth(
    payload: dict[str, Any], *, roles: list[Any] | None = None
) -> dict[str, Any]:
    """Mutate a copy of *payload* with auth_token/nonce when secret is available.

    ``roles`` lets a caller request a *narrower* token than the default
    operator set.  The AI tool surface passes ``[Role.ANALYST]`` so a prompt
    cannot reach CONFIG / RESUME / LIVE commands.
    """
    out = dict(payload)
    raw = str(out.get("cmd") or "").strip().upper()
    if raw and not requires_authorization(raw):
        return out
    if raw in READ_ONLY_COMMANDS:
        return out
    if out.get("auth_token") or out.get("token"):
        if not out.get("nonce"):
            out["nonce"] = secrets.token_urlsafe(16)
        return out
    secret = control_secret()
    if len(secret.encode("utf-8")) < 32:
        return out
    token = _session_token(secret, roles)
    if not token:
        return out
    out["auth_token"] = token
    out["nonce"] = secrets.token_urlsafe(16)
    return out


def _session_token(secret: str, roles: list[Any] | None = None) -> str | None:
    global _cached_token, _cached_exp, _cached_secret, _cached_roles
    now = time.time()
    role_key = _role_key(roles)
    # The cache must be keyed by role set as well as by secret: sharing one
    # cached session between the operator UI and the AI surface would hand a
    # narrow (ANALYST) caller the wide operator token, or vice versa.
    if (
        _cached_token
        and _cached_secret == secret
        and _cached_roles == role_key
        and now < _cached_exp - 60
    ):
        # Revalidate before reuse. The cache is process-global while the
        # verifier may not be (tests use injected clocks), and a stale cached
        # session is exactly the kind of cross-role leak the role key guards
        # against — so confirm it still verifies before handing it back.
        try:
            from engine.control_auth import HMACTokenAuth

            HMACTokenAuth(secret).verify(_cached_token)
        except Exception:
            _cached_token = None
        else:
            return _cached_token
    try:
        from engine.control_auth import HMACTokenAuth, Role
    except Exception:
        return None
    try:
        auth = HMACTokenAuth(secret)
        token = auth.issue(
            "local_operator_ui",
            roles if roles is not None else [Role.ADMIN, Role.RISK, Role.OPERATOR],
            ttl_seconds=900,
        )
    except Exception:
        return None
    _cached_token = token
    _cached_secret = secret
    _cached_roles = role_key
    _cached_exp = now + 900
    return token
