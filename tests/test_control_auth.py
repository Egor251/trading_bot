from __future__ import annotations

import pytest

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.control_auth import (
    AuthorizationDenied,
    CommandRateLimiter,
    ControlAuthorizer,
    ExpiredToken,
    HMACTokenAuth,
    ReplayDetected,
    Role,
)


SECRET = b"a-production-shaped-secret-that-is-long-enough"


def test_signed_session_expiry_and_tamper_detection() -> None:
    clock = [1_000.0]
    auth = HMACTokenAuth(SECRET, clock=lambda: clock[0])
    token = auth.issue("alice", [Role.OPERATOR], ttl_seconds=30)
    assert auth.verify(token).subject == "alice"

    with pytest.raises(Exception):
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


def test_local_dev_authorizer_allows_config_but_blocks_live() -> None:
    from services.live_safety_service import (
        LocalDevControlAuthorizer,
        UnavailableControlAuthorizer,
    )

    local = LocalDevControlAuthorizer()
    assert local.authorize("halt").emergency
    assert local.authorize("config").allowed
    assert local.authorize("resume").principal is not None
    assert local.authorize("config").principal.subject == "local_operator"
    with pytest.raises(AuthorizationDenied):
        local.authorize("live")

    closed = UnavailableControlAuthorizer()
    assert closed.authorize("halt").emergency
    with pytest.raises(AuthorizationDenied, match="not configured"):
        closed.authorize("config")


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
