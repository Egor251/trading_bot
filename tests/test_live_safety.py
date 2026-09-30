from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.live_safety import (
    EvidenceAuthority,
    EvidenceKind,
    KillMode,
    LivePreflight,
    LivePreflightInputs,
)


SECRET = b"another-production-shaped-secret-long-enough"


def _inputs(authority: EvidenceAuthority) -> LivePreflightInputs:
    issue = authority.issue
    return LivePreflightInputs(
        operator=issue(EvidenceKind.AUTHENTICATED_OPERATOR, "alice"),
        confirmations=(
            issue(EvidenceKind.OPERATOR_CONFIRMATION, "alice"),
            issue(EvidenceKind.OPERATOR_CONFIRMATION, "alice"),
        ),
        cost_schedule=issue(EvidenceKind.COST_SCHEDULE, "schedule-v4"),
        reconciliation=issue(EvidenceKind.RECONCILIATION, "account-1"),
        market_data=issue(EvidenceKind.MARKET_DATA, "feed-1"),
        instrument_spec=issue(EvidenceKind.INSTRUMENT_SPEC, "MOEX:SBER"),
        broker_capability=issue(EvidenceKind.BROKER_CAPABILITY, "broker-1"),
        sandbox_acceptance=issue(EvidenceKind.SANDBOX_ACCEPTANCE, "broker-1"),
    )


def test_preflight_passes_only_with_every_gate() -> None:
    authority = EvidenceAuthority(SECRET)
    preflight = LivePreflight(
        authority,
        environ={"TRADING_LIVE_ENABLED": "true"},
        kill_mode=KillMode.HALT_AND_FLATTEN,
    )
    report = preflight.evaluate(_inputs(authority))
    assert report.allowed
    assert report.kill_mode is KillMode.HALT_AND_FLATTEN
    assert len(report.gates) == 9


def test_preflight_is_fail_closed_for_missing_expired_or_duplicate_evidence() -> None:
    clock = [100.0]
    authority = EvidenceAuthority(SECRET, clock=lambda: clock[0])
    preflight = LivePreflight(
        authority,
        environ={},
        clock=lambda: clock[0],
    )
    inputs = _inputs(authority)
    duplicate = replace(inputs, confirmations=(inputs.confirmations[0],) * 2)
    report = preflight.evaluate(duplicate)
    assert not report.allowed
    assert "live feature flag disabled" in report.failures
    assert "two distinct valid confirmations required" in report.failures

    clock[0] = 1_000.0
    assert not preflight.evaluate(inputs).allowed


def test_evidence_and_report_are_immutable() -> None:
    authority = EvidenceAuthority(SECRET)
    inputs = _inputs(authority)
    with pytest.raises(FrozenInstanceError):
        inputs.operator.subject = "mallory"  # type: ignore[misc]
    with pytest.raises(TypeError):
        inputs.operator.metadata["changed"] = True  # type: ignore[index]
