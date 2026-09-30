"""Authentication and authorization primitives for trading control commands.

The module deliberately has no runtime or UI dependencies.  Tokens are compact,
signed JSON envelopes; callers remain responsible for transporting them over an
authenticated and encrypted channel.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum
import base64
import hashlib
import hmac
import json
import math
import secrets
import threading
import time
from typing import Any, Callable, Iterable, Mapping


class AuthError(ValueError):
    """Base class for authentication failures."""


class InvalidToken(AuthError):
    pass


class ExpiredToken(AuthError):
    pass


class ReplayDetected(AuthError):
    pass


class AuthorizationDenied(PermissionError):
    pass


class RateLimitExceeded(AuthorizationDenied):
    pass


class Role(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    RISK = "risk"
    ADMIN = "admin"


class ControlCommand(str, Enum):
    HALT = "halt"
    RESUME = "resume"
    CONFIG = "config"
    LIVE = "live"


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    try:
        raw = value.encode("ascii")
        return base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4))
    except Exception as exc:
        raise InvalidToken("malformed base64 token") from exc


def _now(clock: Callable[[], float]) -> float:
    value = float(clock())
    if not math.isfinite(value):
        raise AuthError("clock returned a non-finite timestamp")
    return value


def _roles(values: Iterable[Role | str]) -> frozenset[Role]:
    try:
        result = frozenset(Role(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise AuthError("token contains an unknown role") from exc
    if not result:
        raise AuthError("at least one role is required")
    return result


@dataclass(frozen=True, slots=True)
class AuthSession:
    subject: str
    roles: frozenset[Role]
    issued_at: float
    expires_at: float
    session_id: str

    def has_any_role(self, allowed: Iterable[Role | str]) -> bool:
        return bool(self.roles.intersection(_roles(allowed)))


class NonceReplayCache:
    """Thread-safe bounded replay cache with expiry-based eviction."""

    def __init__(
        self,
        *,
        max_entries: int = 100_000,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self.max_entries = int(max_entries)
        self._clock = clock
        self._entries: dict[str, float] = {}
        self._lock = threading.Lock()

    def consume(
        self,
        nonce: str,
        *,
        expires_at: float,
        now: float | None = None,
    ) -> None:
        if not isinstance(nonce, str) or not nonce.strip():
            raise AuthError("nonce is required")
        current = _now(self._clock) if now is None else float(now)
        expiry = float(expires_at)
        if not math.isfinite(expiry) or expiry <= current:
            raise ExpiredToken("nonce has expired")
        with self._lock:
            self._evict(current)
            if nonce in self._entries:
                raise ReplayDetected("nonce has already been used")
            if len(self._entries) >= self.max_entries:
                # Fail closed rather than evicting a still-valid replay marker.
                raise AuthError("nonce replay cache is full")
            self._entries[nonce] = expiry

    def seen(self, nonce: str, *, now: float | None = None) -> bool:
        current = _now(self._clock) if now is None else float(now)
        with self._lock:
            self._evict(current)
            return nonce in self._entries

    def _evict(self, now: float) -> None:
        for nonce, expiry in tuple(self._entries.items()):
            if expiry <= now:
                self._entries.pop(nonce, None)


class HMACTokenAuth:
    """Issue and verify versioned HMAC-SHA256 operator session tokens."""

    def __init__(
        self,
        secret: bytes | str,
        *,
        issuer: str = "trading_bot",
        clock: Callable[[], float] = time.time,
        max_ttl_seconds: float = 8 * 60 * 60,
    ) -> None:
        key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        if len(key) < 32:
            raise ValueError("HMAC secret must contain at least 32 bytes")
        if not issuer.strip():
            raise ValueError("issuer is required")
        if max_ttl_seconds <= 0:
            raise ValueError("max_ttl_seconds must be positive")
        self._secret = key
        self.issuer = issuer
        self._clock = clock
        self.max_ttl_seconds = float(max_ttl_seconds)

    def issue(
        self,
        subject: str,
        roles: Iterable[Role | str],
        *,
        ttl_seconds: float = 900,
        session_id: str | None = None,
    ) -> str:
        if not isinstance(subject, str) or not subject.strip():
            raise AuthError("subject is required")
        ttl = float(ttl_seconds)
        if not math.isfinite(ttl) or ttl <= 0 or ttl > self.max_ttl_seconds:
            raise AuthError("token TTL is outside the allowed range")
        issued = _now(self._clock)
        role_values = sorted(role.value for role in _roles(roles))
        payload = {
            "v": 1,
            "iss": self.issuer,
            "sub": subject.strip(),
            "roles": role_values,
            "iat": issued,
            "exp": issued + ttl,
            "sid": session_id or secrets.token_urlsafe(18),
        }
        encoded = _b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        signature = _b64encode(
            hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest()
        )
        return f"{encoded}.{signature}"

    issue_token = issue

    def verify(self, token: str, *, now: float | None = None) -> AuthSession:
        try:
            encoded, supplied_signature = token.split(".", 1)
        except (AttributeError, ValueError) as exc:
            raise InvalidToken("malformed token") from exc
        expected = hmac.new(
            self._secret, encoded.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _b64decode(supplied_signature)):
            raise InvalidToken("invalid token signature")
        try:
            payload = json.loads(_b64decode(encoded))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise InvalidToken("invalid token payload") from exc
        if not isinstance(payload, Mapping) or payload.get("v") != 1:
            raise InvalidToken("unsupported token version")
        if payload.get("iss") != self.issuer:
            raise InvalidToken("unexpected token issuer")
        try:
            issued = float(payload["iat"])
            expiry = float(payload["exp"])
            subject = str(payload["sub"]).strip()
            session_id = str(payload["sid"]).strip()
            roles = _roles(payload["roles"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidToken("incomplete token payload") from exc
        current = _now(self._clock) if now is None else float(now)
        if not subject or not session_id:
            raise InvalidToken("empty token identity")
        if not all(math.isfinite(value) for value in (issued, expiry)):
            raise InvalidToken("invalid token timestamps")
        if expiry <= current:
            raise ExpiredToken("token has expired")
        if issued > current + 30:
            raise InvalidToken("token issued in the future")
        if expiry <= issued or expiry - issued > self.max_ttl_seconds:
            raise InvalidToken("invalid token lifetime")
        return AuthSession(subject, roles, issued, expiry, session_id)

    verify_token = verify


class CommandRateLimiter:
    """Per-principal sliding-window command limiter."""

    def __init__(
        self,
        *,
        max_commands: int = 20,
        window_seconds: float = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_commands <= 0 or window_seconds <= 0:
            raise ValueError("rate limit values must be positive")
        self.max_commands = int(max_commands)
        self.window_seconds = float(window_seconds)
        self._clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, *, now: float | None = None) -> bool:
        current = _now(self._clock) if now is None else float(now)
        cutoff = current - self.window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.max_commands:
                return False
            events.append(current)
            return True

    def check(self, key: str, *, now: float | None = None) -> None:
        if not self.allow(key, now=now):
            raise RateLimitExceeded("control command rate limit exceeded")


@dataclass(frozen=True, slots=True)
class CommandAuthorization:
    command: ControlCommand
    allowed: bool
    emergency: bool
    principal: AuthSession | None = None
    reason: str = ""


class ControlAuthorizer:
    """Authorize control-plane commands with replay and role enforcement.

    HALT is intentionally unauthenticated and bypasses rate limiting: loss of
    credentials or a saturated control plane must never prevent risk reduction.
    """

    DEFAULT_ROLES = {
        ControlCommand.RESUME: frozenset({Role.RISK, Role.ADMIN}),
        ControlCommand.CONFIG: frozenset({Role.ADMIN}),
        ControlCommand.LIVE: frozenset({Role.OPERATOR, Role.RISK, Role.ADMIN}),
    }

    def __init__(
        self,
        auth: HMACTokenAuth,
        *,
        replay_cache: NonceReplayCache | None = None,
        rate_limiter: CommandRateLimiter | None = None,
        command_roles: Mapping[ControlCommand | str, Iterable[Role | str]] | None = None,
        clock: Callable[[], float] = time.time,
        nonce_ttl_seconds: float = 120,
    ) -> None:
        self.auth = auth
        self.replay_cache = replay_cache or NonceReplayCache(clock=clock)
        self.rate_limiter = rate_limiter or CommandRateLimiter()
        self._clock = clock
        self.nonce_ttl_seconds = float(nonce_ttl_seconds)
        self.command_roles = dict(self.DEFAULT_ROLES)
        if command_roles:
            self.command_roles.update(
                {
                    ControlCommand(command): _roles(roles)
                    for command, roles in command_roles.items()
                }
            )

    def authorize(
        self,
        command: ControlCommand | str,
        *,
        token: str | None = None,
        nonce: str | None = None,
    ) -> CommandAuthorization:
        command = ControlCommand(command)
        if command is ControlCommand.HALT:
            return CommandAuthorization(command, True, True, reason="emergency_halt")
        if not token:
            raise AuthorizationDenied(f"{command.value} requires authentication")
        principal = self.auth.verify(token)
        allowed_roles = self.command_roles[command]
        if not principal.roles.intersection(allowed_roles):
            raise AuthorizationDenied(f"role cannot execute {command.value}")
        self.rate_limiter.check(principal.session_id)
        current = _now(self._clock)
        self.replay_cache.consume(
            nonce or "",
            expires_at=min(
                principal.expires_at, current + self.nonce_ttl_seconds
            ),
            now=current,
        )
        return CommandAuthorization(command, True, False, principal)


# Explicit compatibility spellings for integration code.
TokenAuthenticator = HMACTokenAuth
HMACSessionAuth = HMACTokenAuth
Command = ControlCommand


__all__ = [
    "AuthError",
    "AuthSession",
    "AuthorizationDenied",
    "Command",
    "CommandAuthorization",
    "CommandRateLimiter",
    "ControlAuthorizer",
    "ControlCommand",
    "ExpiredToken",
    "HMACSessionAuth",
    "HMACTokenAuth",
    "InvalidToken",
    "NonceReplayCache",
    "RateLimitExceeded",
    "ReplayDetected",
    "Role",
    "TokenAuthenticator",
]
