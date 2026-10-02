from __future__ import annotations

from pathlib import Path

import pytest

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.control_auth import (
    AuthorizationDenied,
    CommandRateLimiter,
    ControlAuthorizer,
    ExpiredToken,
    HMACTokenAuth,
    InvalidToken,
    ReplayDetected,
    Role,
)


SECRET = b"a-production-shaped-secret-that-is-long-enough"


def test_signed_session_expiry_and_tamper_detection() -> None:
    clock = [1_000.0]
    auth = HMACTokenAuth(SECRET, clock=lambda: clock[0])
    token = auth.issue("alice", [Role.OPERATOR], ttl_seconds=30)
    assert auth.verify(token).subject == "alice"

    with pytest.raises(InvalidToken):
        auth.verify(token[:-1] + ("A" if token[-1] != "A" else "B"))
    clock[0] = 1_031.0
    with pytest.raises(ExpiredToken):
        auth.verify(token)


def test_halt_is_unconditionally_available_but_resume_requires_risk_role() -> None:
    auth = HMACTokenAuth(SECRET)
    authorizer = ControlAuthorizer(auth)
    assert authorizer.authorize("halt").emergency

    operator = auth.issue("operator", [Role.OPERATOR])
    with pytest.raises(AuthorizationDenied):
        authorizer.authorize("resume", token=operator, nonce="resume-1")

    risk = auth.issue("risk", [Role.RISK])
    assert authorizer.authorize("resume", token=risk, nonce="resume-2").allowed
    with pytest.raises(ReplayDetected):
        authorizer.authorize("resume", token=risk, nonce="resume-2")


def test_command_rate_limit_is_per_session() -> None:
    auth = HMACTokenAuth(SECRET)
    limiter = CommandRateLimiter(max_commands=1, window_seconds=60)
    authorizer = ControlAuthorizer(auth, rate_limiter=limiter)
    token = auth.issue("admin", [Role.ADMIN])
    authorizer.authorize("config", token=token, nonce="first")
    with pytest.raises(AuthorizationDenied):
        authorizer.authorize("config", token=token, nonce="second")


def test_local_dev_authorizer_allows_config_but_blocks_live_and_resume() -> None:
    """Local-dev is opt-in and must never be able to re-arm halted trading.

    The previous behaviour granted RESUME to an unauthenticated peer with a
    fabricated ADMIN/RISK principal, so anything that could reach the control
    socket could clear a risk-management halt and resume trading real money.
    """
    from services.live_safety_service import (
        LocalDevControlAuthorizer,
        UnavailableControlAuthorizer,
    )

    local = LocalDevControlAuthorizer()
    assert local.authorize("halt").emergency
    assert local.authorize("config").allowed
    assert local.authorize("config").principal.subject == "local_operator"
    with pytest.raises(AuthorizationDenied, match="live mode requires"):
        local.authorize("live")
    with pytest.raises(AuthorizationDenied, match="resuming trading requires"):
        local.authorize("resume")

    closed = UnavailableControlAuthorizer()
    assert closed.authorize("halt").emergency
    with pytest.raises(AuthorizationDenied, match="not configured"):
        closed.authorize("config")


def test_live_safety_defaults_to_fail_closed() -> None:
    """No HMAC secret + no explicit opt-in must refuse everything but HALT."""
    from services.live_safety_service import LiveSafetyService

    assert LiveSafetyService.default_config(None)["allow_unauthenticated_local"] is False


def test_analyst_role_cannot_reach_privileged_commands() -> None:
    """The AI surface must be read-only plus HALT (see operator_ui.md)."""
    auth = HMACTokenAuth(SECRET)
    authorizer = ControlAuthorizer(auth)
    analyst = auth.issue("ai", [Role.ANALYST])
    assert authorizer.authorize("halt").emergency
    for command in ("config", "resume", "live"):
        with pytest.raises(AuthorizationDenied):
            authorizer.authorize(command, token=analyst, nonce=f"n-{command}")


def test_ai_bridge_calls_attach_a_restricted_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``engine.ai_tools`` must not mint itself an admin session."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from engine.ai_tools import _call_bridge
    from ui.control_auth_client import reset_token_cache

    monkeypatch.setenv("TRADING_CONTROL_HMAC_SECRET", SECRET.decode())
    reset_token_cache()
    core = MagicMock()
    bridge = MagicMock()
    bridge.handle_command = AsyncMock(return_value={"ok": True})
    core.service_registry.get = MagicMock(return_value=bridge)

    out = asyncio.run(_call_bridge(core, "SET_TRADING", mode="paper"))
    assert out["success"] is True
    session = HMACTokenAuth(SECRET).verify(
        str(bridge.handle_command.await_args.args[0]["auth_token"])
    )
    assert session.roles == frozenset({Role.ANALYST})
    assert Role.ADMIN not in session.roles
    reset_token_cache()


def test_token_cache_is_keyed_by_role_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A narrow (ANALYST) caller must never receive the wide operator session."""
    from ui.control_auth_client import (
        attach_control_auth,
        reset_token_cache,
    )

    monkeypatch.setenv("TRADING_CONTROL_HMAC_SECRET", SECRET.decode())
    reset_token_cache()
    auth = HMACTokenAuth(SECRET)
    narrow = attach_control_auth({"cmd": "SET_TRADING"}, roles=[Role.ANALYST])
    wide = attach_control_auth({"cmd": "SET_TRADING"})
    assert auth.verify(str(narrow["auth_token"])).roles == frozenset({Role.ANALYST})
    assert Role.ADMIN in auth.verify(str(wide["auth_token"])).roles
    assert narrow["auth_token"] != wide["auth_token"]
    reset_token_cache()


def test_command_classification_is_shared_and_fail_closed() -> None:
    """Unknown commands must be treated as privileged, not as open."""
    from engine.control_commands import (
        CONFIG_COMMANDS,
        CONTROL_COMMANDS,
        READ_ONLY_COMMANDS,
        classify_control_command,
        requires_authorization,
    )
    from engine.control_auth import ControlCommand

    # The three commands whose absence from the old client allow-list broke
    # cancel-all and the phantom batch under HMAC auth.
    for cmd in (
        "CANCEL_ALL_ORDERS",
        "PHANTOM_APPLY_CHOSEN",
        "PHANTOM_WALK_FORWARD_BATCH",
        "ASK",
    ):
        assert cmd in CONTROL_COMMANDS
        assert cmd not in READ_ONLY_COMMANDS
        assert classify_control_command(cmd) is ControlCommand.CONFIG

    assert requires_authorization("TOTALLY_MADE_UP") is True
    assert classify_control_command("TOTALLY_MADE_UP") is ControlCommand.CONFIG
    assert requires_authorization("STATUS") is False
    assert classify_control_command("STATUS") is None
    assert classify_control_command("SET_TRADING", requested_mode="live") is ControlCommand.LIVE
    assert classify_control_command("PLACE_ORDER", live_mode=True) is ControlCommand.LIVE
    assert classify_control_command("PLACE_ORDER", live_mode=False) is ControlCommand.CONFIG
    assert classify_control_command("KILL") is ControlCommand.HALT
    assert "CANCEL_ALL_ORDERS" in CONFIG_COMMANDS


def test_operator_session_token_roundtrip() -> None:
    from engine.operator_session import OperatorSession, extract_bearer, token_from_query

    session = OperatorSession(
        token="t" * 40, token_path=Path("data/ui/_test_operator_token")
    )
    assert session.verify("t" * 40)
    assert not session.verify("t" * 39)
    assert not session.verify("")
    assert not session.verify(None)
    assert extract_bearer("Bearer " + "t" * 40) == "t" * 40
    assert token_from_query("operator_token=" + "t" * 40) == "t" * 40
    # Exact origin match: a hostile origin and any other localhost port fail.
    assert session.origin_allowed("http://127.0.0.1:3000")
    assert not session.origin_allowed("http://evil.example")
    assert not session.origin_allowed("http://localhost:9999")


def test_attach_control_auth_mints_token_when_secret_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ui.control_auth_client import attach_control_auth

    monkeypatch.setenv("TRADING_CONTROL_HMAC_SECRET", SECRET.decode())
    out = attach_control_auth({"cmd": "SET_ENSEMBLE", "enabled": True})
    assert out.get("auth_token")
    assert out.get("nonce")
    auth = HMACTokenAuth(SECRET)
    session = auth.verify(str(out["auth_token"]))
    assert Role.ADMIN in session.roles
