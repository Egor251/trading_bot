"""Stage E: durable event_type → DataClass routing prefixes."""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from cb_storage.classify import classify_event_type  # noqa: E402
from cb_storage.models import DataClass  # noqa: E402


def test_audit_prefixes() -> None:
    for et in (
        "ensemble.decision",
        "ensemble.reject",
        "ensemble.weights_changed",
        "risk.reject",
        "ops.alert",
        "reconciliation.incident",
        "reconciliation.unhealthy",
        "reconciliation.retry_exhausted",
        "portfolio.snapshot",
        "config.lineup_changed",
        "cost.schedule_activated",
        "execution.result",
        "system.halt",
    ):
        assert classify_event_type(et) == DataClass.AUDIT, et


def test_finding_and_semantic() -> None:
    assert classify_event_type("strategy.signal") == DataClass.FINDING
    assert classify_event_type("market.candle") == DataClass.SEMANTIC_STREAM
    assert classify_event_type("market.regime") == DataClass.SEMANTIC_STREAM
    assert classify_event_type("phantom.fills") == DataClass.SEMANTIC_STREAM
    assert classify_event_type("backtest.run_finished") == DataClass.SEMANTIC_STREAM
    assert classify_event_type("ai.analyst.response") == DataClass.SEMANTIC_STREAM
    assert classify_event_type("universe.changed") == DataClass.STATE


if __name__ == "__main__":
    test_audit_prefixes()
    test_finding_and_semantic()
    print("storage classify OK")
