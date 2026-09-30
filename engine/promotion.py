"""Fail-closed strategy promotion governance.

Promotion is intentionally separate from execution runtime.  Every forward
transition requires stage-specific, cryptographically signed evidence and a
completed soak period; rollback is always available and is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
import hashlib
import hmac
import json
from typing import Any, Callable, Iterable, Mapping


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class PromotionStage(str, Enum):
    RESEARCH = "research"
    REPLAY = "replay"
    PAPER = "paper"
    SHADOW = "shadow"
    SANDBOX = "sandbox"
    CANARY_LIVE = "canary_live"


STAGE_ORDER = tuple(PromotionStage)


class PromotionError(RuntimeError):
    pass


class PromotionDenied(PromotionError):
    pass


@dataclass(frozen=True, slots=True)
class SignedReport:
    strategy_id: str
    stage: PromotionStage | str
    report_type: str
    passed: bool
    created_at: datetime
    payload_hash: str
    signer: str
    signature: str = ""
    expires_at: datetime | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", PromotionStage(self.stage))
        object.__setattr__(self, "created_at", _utc(self.created_at))
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", _utc(self.expires_at))
        object.__setattr__(self, "provenance", dict(self.provenance))
        for name in ("strategy_id", "report_type", "payload_hash", "signer"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")

    def signing_payload(self) -> bytes:
        value = {
            "strategy_id": self.strategy_id,
            "stage": self.stage.value,
            "report_type": self.report_type,
            "passed": self.passed,
            "created_at": self.created_at.isoformat(),
            "payload_hash": self.payload_hash,
            "signer": self.signer,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "provenance": self.provenance,
        }
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()

    def is_current(self, now: datetime) -> bool:
        instant = _utc(now)
        return self.created_at <= instant and (
            self.expires_at is None or instant <= self.expires_at
        )


class ReportSigner:
    """HMAC signer suitable for local governance and deterministic tests.

    Production callers may instead inject any verifier callback into
    :class:`PromotionStateMachine`, e.g. an asymmetric/KMS verifier.
    """

    def __init__(self, signer: str, secret: bytes | str) -> None:
        if not signer:
            raise ValueError("signer is required")
        self.signer = signer
        self._secret = secret.encode() if isinstance(secret, str) else bytes(secret)
        if not self._secret:
            raise ValueError("secret is required")

    def sign(self, report: SignedReport) -> SignedReport:
        if report.signer != self.signer:
            raise ValueError("report signer does not match signer identity")
        signature = hmac.new(
            self._secret, report.signing_payload(), hashlib.sha256
        ).hexdigest()
        return replace(report, signature=signature)

    def verify(self, report: SignedReport) -> bool:
        if report.signer != self.signer or not report.signature:
            return False
        expected = hmac.new(
            self._secret, report.signing_payload(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, report.signature)


DEFAULT_REQUIRED_REPORTS: Mapping[PromotionStage, frozenset[str]] = {
    PromotionStage.REPLAY: frozenset({"research"}),
    PromotionStage.PAPER: frozenset({"replay"}),
    PromotionStage.SHADOW: frozenset({"paper"}),
    PromotionStage.SANDBOX: frozenset({"shadow"}),
    PromotionStage.CANARY_LIVE: frozenset({"sandbox", "risk"}),
}

DEFAULT_MINIMUM_SOAK: Mapping[PromotionStage, timedelta] = {
    PromotionStage.RESEARCH: timedelta(0),
    PromotionStage.REPLAY: timedelta(days=1),
    PromotionStage.PAPER: timedelta(days=7),
    PromotionStage.SHADOW: timedelta(days=7),
    PromotionStage.SANDBOX: timedelta(days=7),
    PromotionStage.CANARY_LIVE: timedelta(days=1),
}


@dataclass(frozen=True, slots=True)
class PromotionPolicy:
    minimum_soak: Mapping[PromotionStage | str, timedelta] = field(
        default_factory=lambda: DEFAULT_MINIMUM_SOAK
    )
    required_reports: Mapping[PromotionStage | str, Iterable[str]] = field(
        default_factory=lambda: DEFAULT_REQUIRED_REPORTS
    )
    trusted_signers: frozenset[str] = frozenset()
    fail_closed: bool = True

    def soak_for(self, stage: PromotionStage) -> timedelta:
        value = self.minimum_soak.get(stage, self.minimum_soak.get(stage.value, timedelta(0)))
        if value < timedelta(0):
            raise ValueError("minimum soak must be non-negative")
        return value

    def reports_for(self, target: PromotionStage) -> frozenset[str]:
        value = self.required_reports.get(
            target, self.required_reports.get(target.value, ())
        )
        return frozenset(value)


@dataclass(frozen=True, slots=True)
class PromotionTransition:
    strategy_id: str
    from_stage: PromotionStage
    to_stage: PromotionStage
    timestamp: datetime
    reason: str
    rollback: bool = False


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    allowed: bool
    current_stage: PromotionStage
    target_stage: PromotionStage
    reasons: tuple[str, ...] = ()


class PromotionStateMachine:
    """Linear, auditable promotion state machine with unrestricted rollback."""

    def __init__(
        self,
        strategy_id: str,
        *,
        policy: PromotionPolicy | None = None,
        verifier: Callable[[SignedReport], bool] | None = None,
        initial_stage: PromotionStage | str = PromotionStage.RESEARCH,
        entered_at: datetime | None = None,
    ) -> None:
        if not strategy_id:
            raise ValueError("strategy_id is required")
        self.strategy_id = strategy_id
        self.policy = policy or PromotionPolicy()
        self.verifier = verifier
        self.stage = PromotionStage(initial_stage)
        self.entered_at = _utc(entered_at or datetime.now(UTC))
        self.reports: list[SignedReport] = []
        self.history: list[PromotionTransition] = []

    @property
    def current_stage(self) -> PromotionStage:
        return self.stage

    @property
    def next_stage(self) -> PromotionStage | None:
        index = STAGE_ORDER.index(self.stage)
        return STAGE_ORDER[index + 1] if index + 1 < len(STAGE_ORDER) else None

    def add_report(self, report: SignedReport) -> None:
        if report.strategy_id != self.strategy_id:
            raise PromotionDenied("report belongs to another strategy")
        self.reports.append(report)

    record_report = add_report

    def evaluate(
        self,
        target: PromotionStage | str | None = None,
        *,
        now: datetime | None = None,
    ) -> PromotionDecision:
        target_stage = PromotionStage(target) if target is not None else self.next_stage
        if target_stage is None:
            return PromotionDecision(False, self.stage, self.stage, ("already at final stage",))
        instant = _utc(now or datetime.now(UTC))
        reasons: list[str] = []
        expected = self.next_stage
        if target_stage != expected:
            reasons.append("forward promotions must advance exactly one stage")
        required_soak = self.policy.soak_for(self.stage)
        if instant < self.entered_at + required_soak:
            reasons.append("minimum soak period has not elapsed")

        required_types = self.policy.reports_for(target_stage)
        for report_type in sorted(required_types):
            candidates = [
                report
                for report in self.reports
                if report.stage in {self.stage, target_stage}
                and report.report_type == report_type
                and report.passed
                and report.is_current(instant)
            ]
            valid = any(self._report_valid(report) for report in candidates)
            if not valid:
                reasons.append(f"missing valid signed report: {report_type}")
        return PromotionDecision(not reasons, self.stage, target_stage, tuple(reasons))

    can_promote = evaluate

    def promote(
        self,
        target: PromotionStage | str | None = None,
        *,
        now: datetime | None = None,
        reason: str = "policy gates passed",
    ) -> PromotionTransition:
        instant = _utc(now or datetime.now(UTC))
        decision = self.evaluate(target, now=instant)
        if not decision.allowed:
            raise PromotionDenied("; ".join(decision.reasons))
        previous = self.stage
        self.stage = decision.target_stage
        self.entered_at = instant
        transition = PromotionTransition(
            self.strategy_id, previous, self.stage, instant, reason
        )
        self.history.append(transition)
        return transition

    def rollback(
        self,
        target: PromotionStage | str | None = None,
        *,
        now: datetime | None = None,
        reason: str,
    ) -> PromotionTransition:
        if not reason.strip():
            raise ValueError("rollback reason is required")
        current_index = STAGE_ORDER.index(self.stage)
        target_stage = (
            STAGE_ORDER[max(0, current_index - 1)]
            if target is None
            else PromotionStage(target)
        )
        if STAGE_ORDER.index(target_stage) >= current_index:
            raise PromotionDenied("rollback target must be an earlier stage")
        instant = _utc(now or datetime.now(UTC))
        previous = self.stage
        self.stage = target_stage
        self.entered_at = instant
        transition = PromotionTransition(
            self.strategy_id,
            previous,
            target_stage,
            instant,
            reason,
            rollback=True,
        )
        self.history.append(transition)
        return transition

    def fail_closed(self, *, now: datetime | None = None, reason: str) -> PromotionTransition:
        """Emergency rollback to research, including from partially gated states."""
        if self.stage is PromotionStage.RESEARCH:
            raise PromotionDenied("already in fail-closed research stage")
        return self.rollback(PromotionStage.RESEARCH, now=now, reason=reason)

    def _report_valid(self, report: SignedReport) -> bool:
        if self.policy.trusted_signers and report.signer not in self.policy.trusted_signers:
            return False
        if self.verifier is None:
            # A signature without an available verifier cannot be trusted.
            return not self.policy.fail_closed and bool(report.signature)
        try:
            return bool(report.signature) and bool(self.verifier(report))
        except Exception:
            return False


PromotionMachine = PromotionStateMachine
Stage = PromotionStage


__all__ = [
    "DEFAULT_MINIMUM_SOAK",
    "DEFAULT_REQUIRED_REPORTS",
    "PromotionDecision",
    "PromotionDenied",
    "PromotionError",
    "PromotionMachine",
    "PromotionPolicy",
    "PromotionStage",
    "PromotionStateMachine",
    "PromotionTransition",
    "ReportSigner",
    "STAGE_ORDER",
    "SignedReport",
    "Stage",
]
