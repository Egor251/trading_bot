"""Fail-closed, dependency-free preflight gates for enabling live trading."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import hmac
import json
import math
import os
import secrets
import time
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence


class EvidenceError(ValueError):
    pass


class KillMode(str, Enum):
    HALT_ONLY = "halt_only"
    HALT_AND_FLATTEN = "halt_and_flatten"


class EvidenceKind(str, Enum):
    AUTHENTICATED_OPERATOR = "authenticated_operator"
    OPERATOR_CONFIRMATION = "operator_confirmation"
    COST_SCHEDULE = "cost_schedule_active_validated"
    RECONCILIATION = "reconciliation_healthy"
    MARKET_DATA = "market_data_fresh_non_synthetic"
    INSTRUMENT_SPEC = "instrument_spec_known_complete"
    BROKER_CAPABILITY = "broker_capability"
    SANDBOX_ACCEPTANCE = "sandbox_acceptance"


def _freeze_mapping(values: Mapping[str, Any]) -> Mapping[str, Any]:
    def freeze(value: Any) -> Any:
        if isinstance(value, Mapping):
            return MappingProxyType({str(k): freeze(v) for k, v in value.items()})
        if isinstance(value, (list, tuple)):
            return tuple(freeze(item) for item in value)
        if isinstance(value, (set, frozenset)):
            return frozenset(freeze(item) for item in value)
        if isinstance(value, (str, int, float, bool, type(None))):
            return value
        raise EvidenceError(f"unsupported evidence value: {type(value).__name__}")

    return MappingProxyType({str(key): freeze(value) for key, value in values.items()})


def _canonical_metadata(values: Mapping[str, Any]) -> bytes:
    def thaw(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(k): thaw(v) for k, v in value.items()}
        if isinstance(value, (tuple, frozenset)):
            return [thaw(item) for item in value]
        return value

    return json.dumps(
        thaw(values), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class SafetyEvidence:
    """Immutable, purpose-bound and expiring preflight evidence."""

    kind: EvidenceKind
    subject: str
    issued_at: float
    expires_at: float
    nonce: str
    signature: str
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", EvidenceKind(self.kind))
        if not self.subject.strip() or not self.nonce.strip() or not self.signature.strip():
            raise EvidenceError("evidence identity, nonce and signature are required")
        if not all(math.isfinite(float(v)) for v in (self.issued_at, self.expires_at)):
            raise EvidenceError("evidence timestamps must be finite")
        if self.expires_at <= self.issued_at:
            raise EvidenceError("evidence expiry must follow issuance")
        if not isinstance(self.metadata, Mapping):
            raise EvidenceError("evidence metadata must be a mapping")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    @property
    def expired(self) -> bool:
        return self.expires_at <= time.time()


class EvidenceAuthority:
    """Create and validate HMAC-authenticated safety evidence."""

    def __init__(
        self,
        secret: bytes | str,
        *,
        clock: Callable[[], float] = time.time,
        max_ttl_seconds: float = 15 * 60,
    ) -> None:
        key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        if len(key) < 32:
            raise ValueError("evidence secret must contain at least 32 bytes")
        if max_ttl_seconds <= 0:
            raise ValueError("max_ttl_seconds must be positive")
        self._secret = key
        self._clock = clock
        self.max_ttl_seconds = float(max_ttl_seconds)

    def issue(
        self,
        kind: EvidenceKind | str,
        subject: str,
        *,
        ttl_seconds: float = 300,
        nonce: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SafetyEvidence:
        kind = EvidenceKind(kind)
        if not isinstance(subject, str) or not subject.strip():
            raise EvidenceError("evidence subject is required")
        ttl = float(ttl_seconds)
        if not math.isfinite(ttl) or ttl <= 0 or ttl > self.max_ttl_seconds:
            raise EvidenceError("evidence TTL is outside the allowed range")
        issued = float(self._clock())
        evidence_nonce = nonce or secrets.token_urlsafe(18)
        frozen_metadata = _freeze_mapping(metadata or {})
        signature = self._sign(
            kind, subject.strip(), issued, issued + ttl, evidence_nonce, frozen_metadata
        )
        return SafetyEvidence(
            kind,
            subject.strip(),
            issued,
            issued + ttl,
            evidence_nonce,
            signature,
            frozen_metadata,
        )

    def verify(
        self,
        evidence: SafetyEvidence,
        *,
        expected_kind: EvidenceKind | str | None = None,
        now: float | None = None,
    ) -> bool:
        if not isinstance(evidence, SafetyEvidence):
            return False
        current = float(self._clock()) if now is None else float(now)
        if (
            not math.isfinite(current)
            or evidence.expires_at <= current
            or evidence.issued_at > current + 30
            or evidence.expires_at - evidence.issued_at > self.max_ttl_seconds
        ):
            return False
        if expected_kind is not None and evidence.kind is not EvidenceKind(expected_kind):
            return False
        expected = self._sign(
            evidence.kind,
            evidence.subject,
            evidence.issued_at,
            evidence.expires_at,
            evidence.nonce,
            evidence.metadata,
        )
        return hmac.compare_digest(expected, evidence.signature)

    def _sign(
        self,
        kind: EvidenceKind,
        subject: str,
        issued_at: float,
        expires_at: float,
        nonce: str,
        metadata: Mapping[str, Any],
    ) -> str:
        pieces = (
            kind.value.encode(),
            subject.encode(),
            repr(issued_at).encode(),
            repr(expires_at).encode(),
            nonce.encode(),
            _canonical_metadata(metadata),
        )
        return hmac.new(self._secret, b"\x00".join(pieces), hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class LivePreflightInputs:
    operator: SafetyEvidence | None = None
    confirmations: tuple[SafetyEvidence, ...] = ()
    cost_schedule: SafetyEvidence | None = None
    reconciliation: SafetyEvidence | None = None
    market_data: SafetyEvidence | None = None
    instrument_spec: SafetyEvidence | None = None
    broker_capability: SafetyEvidence | None = None
    sandbox_acceptance: SafetyEvidence | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "confirmations", tuple(self.confirmations))


@dataclass(frozen=True, slots=True)
class PreflightGate:
    name: str
    passed: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class LivePreflightReport:
    gates: tuple[PreflightGate, ...]
    evaluated_at: float
    nonce: str
    kill_mode: KillMode

    @property
    def allowed(self) -> bool:
        return bool(self.gates) and all(gate.passed for gate in self.gates)

    @property
    def failures(self) -> tuple[str, ...]:
        return tuple(gate.reason or gate.name for gate in self.gates if not gate.passed)

    @property
    def passed(self) -> bool:
        return self.allowed

    def require_allowed(self) -> None:
        if not self.allowed:
            raise PermissionError("live preflight denied: " + "; ".join(self.failures))


class LivePreflight:
    """Evaluate all live prerequisites; missing or malformed input always fails."""

    def __init__(
        self,
        authority: EvidenceAuthority,
        *,
        feature_flag_env: str = "TRADING_LIVE_ENABLED",
        accepted_flag_values: Sequence[str] = ("1", "true", "yes", "enabled"),
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.time,
        kill_mode: KillMode | str = KillMode.HALT_ONLY,
    ) -> None:
        self.authority = authority
        self.feature_flag_env = feature_flag_env
        self.accepted_flag_values = frozenset(
            value.strip().lower() for value in accepted_flag_values
        )
        self.environ = os.environ if environ is None else environ
        self._clock = clock
        self.kill_mode = KillMode(kill_mode)

    def evaluate(
        self,
        inputs: LivePreflightInputs | None = None,
        **evidence: Any,
    ) -> LivePreflightReport:
        """Return a complete report and never silently default a gate to success."""
        if inputs is not None and evidence:
            raise TypeError("pass either inputs or keyword evidence, not both")
        inputs = inputs or LivePreflightInputs(**evidence)
        now = float(self._clock())
        flag = str(self.environ.get(self.feature_flag_env, "")).strip().lower()
        gates = [
            PreflightGate(
                "env_feature_flag",
                flag in self.accepted_flag_values,
                "" if flag in self.accepted_flag_values else "live feature flag disabled",
            ),
            self._gate("authenticated_operator", inputs.operator, EvidenceKind.AUTHENTICATED_OPERATOR, now),
            self._confirmation_gate(inputs.confirmations, now),
            self._gate("active_validated_cost_schedule", inputs.cost_schedule, EvidenceKind.COST_SCHEDULE, now),
            self._gate("reconciliation_healthy", inputs.reconciliation, EvidenceKind.RECONCILIATION, now),
            self._gate("fresh_non_synthetic_data", inputs.market_data, EvidenceKind.MARKET_DATA, now),
            self._gate("known_complete_instrument_spec", inputs.instrument_spec, EvidenceKind.INSTRUMENT_SPEC, now),
            self._gate("broker_capability", inputs.broker_capability, EvidenceKind.BROKER_CAPABILITY, now),
            self._gate("sandbox_acceptance", inputs.sandbox_acceptance, EvidenceKind.SANDBOX_ACCEPTANCE, now),
        ]
        return LivePreflightReport(
            tuple(gates), now, secrets.token_urlsafe(18), self.kill_mode
        )

    run = evaluate

    def _gate(
        self,
        name: str,
        evidence: SafetyEvidence | None,
        kind: EvidenceKind,
        now: float,
    ) -> PreflightGate:
        valid = evidence is not None and self.authority.verify(
            evidence, expected_kind=kind, now=now
        )
        return PreflightGate(name, valid, "" if valid else f"{name} evidence missing or invalid")

    def _confirmation_gate(
        self, confirmations: tuple[SafetyEvidence, ...], now: float
    ) -> PreflightGate:
        valid = [
            item
            for item in confirmations
            if self.authority.verify(
                item, expected_kind=EvidenceKind.OPERATOR_CONFIRMATION, now=now
            )
        ]
        distinct_nonces = {item.nonce for item in valid}
        passed = len(valid) >= 2 and len(distinct_nonces) >= 2
        return PreflightGate(
            "two_distinct_confirmations",
            passed,
            "" if passed else "two distinct valid confirmations required",
        )


__all__ = [
    "EvidenceAuthority",
    "EvidenceError",
    "EvidenceKind",
    "KillMode",
    "LivePreflight",
    "LivePreflightInputs",
    "LivePreflightReport",
    "PreflightGate",
    "SafetyEvidence",
]
