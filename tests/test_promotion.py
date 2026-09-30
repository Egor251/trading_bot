from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.promotion import (
    PromotionDenied,
    PromotionPolicy,
    PromotionStage,
    PromotionStateMachine,
    ReportSigner,
    SignedReport,
)


NOW = datetime(2026, 2, 1, tzinfo=UTC)


def report(
    signer: ReportSigner,
    *,
    stage: PromotionStage = PromotionStage.RESEARCH,
    report_type: str = "research",
    passed: bool = True,
    created_at: datetime = NOW,
) -> SignedReport:
    unsigned = SignedReport(
        strategy_id="alpha",
        stage=stage,
        report_type=report_type,
        passed=passed,
        created_at=created_at,
        payload_hash="a" * 64,
        signer="risk-ci",
        provenance={"run": "42"},
    )
    return signer.sign(unsigned)


def test_signed_report_detects_tampering() -> None:
    signer = ReportSigner("risk-ci", "secret")
    signed = report(signer)
    assert signer.verify(signed)
    tampered = SignedReport(
        strategy_id=signed.strategy_id,
        stage=signed.stage,
        report_type=signed.report_type,
        passed=False,
        created_at=signed.created_at,
        payload_hash=signed.payload_hash,
        signer=signed.signer,
        signature=signed.signature,
    )
    assert not signer.verify(tampered)


def test_promotion_requires_soak_and_valid_signed_report() -> None:
    signer = ReportSigner("risk-ci", "secret")
    policy = PromotionPolicy(
        minimum_soak={PromotionStage.RESEARCH: timedelta(days=2)},
        trusted_signers=frozenset({"risk-ci"}),
    )
    machine = PromotionStateMachine(
        "alpha",
        policy=policy,
        verifier=signer.verify,
        entered_at=NOW,
    )
    machine.add_report(report(signer))

    early = machine.evaluate(now=NOW + timedelta(days=1))
    assert not early.allowed
    assert "minimum soak period has not elapsed" in early.reasons

    transition = machine.promote(now=NOW + timedelta(days=2))
    assert transition.from_stage is PromotionStage.RESEARCH
    assert machine.current_stage is PromotionStage.REPLAY


def test_fail_closed_without_verifier_or_with_failed_report() -> None:
    signer = ReportSigner("risk-ci", "secret")
    machine = PromotionStateMachine("alpha", entered_at=NOW)
    machine.add_report(report(signer))
    with pytest.raises(PromotionDenied, match="missing valid signed report"):
        machine.promote(now=NOW)

    verified = PromotionStateMachine(
        "alpha", verifier=signer.verify, entered_at=NOW
    )
    verified.add_report(report(signer, passed=False))
    with pytest.raises(PromotionDenied, match="research"):
        verified.promote(now=NOW)


def test_only_adjacent_forward_transitions_and_rollback_is_audited() -> None:
    signer = ReportSigner("risk-ci", "secret")
    machine = PromotionStateMachine(
        "alpha", verifier=signer.verify, entered_at=NOW
    )
    machine.add_report(report(signer))

    with pytest.raises(PromotionDenied, match="exactly one"):
        machine.promote(PromotionStage.PAPER, now=NOW)

    machine.promote(now=NOW)
    rollback = machine.rollback(
        PromotionStage.RESEARCH,
        now=NOW + timedelta(hours=1),
        reason="drawdown breached",
    )
    assert rollback.rollback
    assert rollback.reason == "drawdown breached"
    assert machine.current_stage is PromotionStage.RESEARCH
    assert machine.entered_at == NOW + timedelta(hours=1)


def test_emergency_fail_closed_rolls_back_from_live_stage() -> None:
    machine = PromotionStateMachine(
        "alpha",
        initial_stage=PromotionStage.CANARY_LIVE,
        entered_at=NOW,
    )
    transition = machine.fail_closed(
        now=NOW + timedelta(minutes=1), reason="kill switch"
    )
    assert transition.to_stage is PromotionStage.RESEARCH
    assert transition.rollback
