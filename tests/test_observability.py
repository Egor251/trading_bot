"""Deterministic tests for production observability and fault helpers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

try:
    from engine import observability as obs  # noqa: E402
except ModuleNotFoundError as exc:
    if exc.name != "microkernel_core":
        raise
    spec = importlib.util.spec_from_file_location(
        "standalone_observability",
        Path(__file__).parents[1] / "engine" / "observability.py",
    )
    assert spec is not None and spec.loader is not None
    obs = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = obs
    spec.loader.exec_module(obs)

AlertThresholds = obs.AlertThresholds
AuditStage = obs.AuditStage
AuditTimeline = obs.AuditTimeline
FaultInjector = obs.FaultInjector
FeeLine = obs.FeeLine
MetricsRegistry = obs.MetricsRegistry
OperationsObservability = obs.OperationsObservability
ServiceUnavailableError = obs.ServiceUnavailableError
nightly_fee_reconciliation = obs.nightly_fee_reconciliation
write_health_snapshot = obs.write_health_snapshot


class ManualClock:
    def __init__(self, value: float = 1_700_000_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def test_prometheus_counter_gauge_and_histogram_text_is_deterministic() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("ops_events_total", "Events", ("kind",))
    gauge = registry.gauge("ops_ready", "Ready")
    histogram = registry.histogram(
        "ops_latency_seconds", "Latency", (0.1, 1.0), ("stage",)
    )

    counter.inc(2, {"kind": "fill"})
    gauge.set(1)
    histogram.observe(0.05, {"stage": "risk"})
    histogram.observe(2.0, {"stage": "risk"})

    assert registry.render() == (
        "# HELP ops_events_total Events\n"
        "# TYPE ops_events_total counter\n"
        'ops_events_total{kind="fill"} 2\n'
        "# HELP ops_latency_seconds Latency\n"
        "# TYPE ops_latency_seconds histogram\n"
        'ops_latency_seconds_bucket{stage="risk",le="0.1"} 1\n'
        'ops_latency_seconds_bucket{stage="risk",le="1"} 1\n'
        'ops_latency_seconds_bucket{stage="risk",le="+Inf"} 2\n'
        'ops_latency_seconds_sum{stage="risk"} 2.05\n'
        'ops_latency_seconds_count{stage="risk"} 2\n'
        "# HELP ops_ready Ready\n"
        "# TYPE ops_ready gauge\n"
        "ops_ready 1\n"
    )


def test_metrics_reject_bad_labels_and_negative_counter() -> None:
    counter = MetricsRegistry().counter("requests_total", "Requests", ("status",))
    with pytest.raises(ValueError, match="requires labels"):
        counter.inc(labels={"wrong": "ok"})
    with pytest.raises(ValueError, match="cannot decrease"):
        counter.inc(-1, {"status": "ok"})


def test_audit_timeline_preserves_full_execution_chain(tmp_path) -> None:
    clock = ManualClock()
    path = tmp_path / "audit.jsonl"
    timeline = AuditTimeline(path, clock=clock)
    stages = [
        AuditStage.SIGNAL,
        AuditStage.RISK,
        AuditStage.ORDER_REVISION,
        AuditStage.FILL,
        AuditStage.COST,
        AuditStage.PROTECTION,
        AuditStage.RECONCILIATION,
    ]

    for index, stage in enumerate(stages, start=1):
        timeline.record("trace-7", stage, f"event-{index}", details={"index": index})
        clock.value += 1

    assert [event.stage for event in timeline.trace("trace-7")] == [
        stage.value for stage in stages
    ]
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["sequence"] for row in rows] == list(range(1, 8))
    assert all(row["trace_id"] == "trace-7" for row in rows)


def test_alert_rules_cover_required_operational_failures() -> None:
    clock = ManualClock()
    ops = OperationsObservability(
        clock=clock,
        thresholds=AlertThresholds(
            stale_feed_seconds=10,
            fee_mismatch=0.5,
            slippage_mismatch_bps=2,
            drawdown_ratio=0.1,
        ),
    )
    ops.mark_feed("moex", observed_at=clock.value - 11)
    ops.mark_broker("quik", False)
    ops.mark_reconciliation("main", False)
    ops.mark_stop_coverage("main", covered=1, open_positions=2)
    ops.record_order("quik", "rejected", duplicate=True)
    ops.mark_cost_mismatch("main", fee_amount=1.0, slippage_bps=3.0)
    ops.mark_drawdown("portfolio", 0.1)
    ops.record_command(
        "flatten all", actor="operator", audited=False, trace_id="command-1"
    )

    assert {alert.name for alert in ops.alerts()} == {
        "stale_feed",
        "broker_disconnect",
        "reconciliation_break",
        "stop_coverage",
        "rejected_orders",
        "duplicate_orders",
        "fee_mismatch",
        "slippage_mismatch",
        "drawdown",
        "command_audit",
    }
    text = ops.prometheus_text()
    assert 'trading_broker_connected{broker="quik"} 0' in text
    assert 'trading_orders_total{broker="quik",status="rejected"} 1' in text


def test_command_audit_and_stage_latency_are_recorded() -> None:
    clock = ManualClock()
    ops = OperationsObservability(clock=clock)
    ops.record_stage(
        "trace-1", AuditStage.RISK, "approved", latency_seconds=0.02
    )
    ops.record_command(
        "emergency halt",
        actor="alice",
        audited=True,
        trace_id="trace-1",
        details={"ticket": "INC-9"},
    )

    assert [event.stage for event in ops.audit.trace("trace-1")] == ["risk", "command"]
    assert "trading_stage_latency_seconds_count{stage=\"risk\"} 1" in ops.prometheus_text()
    assert not any(alert.name == "command_audit" for alert in ops.alerts())


def test_nightly_fee_reconciliation_only_drafts_actions() -> None:
    report = nightly_fee_reconciliation(
        [
            FeeLine("fill-1", expected_fee=1.0, actual_fee=1.7),
            FeeLine(
                "fill-2",
                expected_fee=0.5,
                actual_fee=0.5,
                expected_slippage_bps=1,
                actual_slippage_bps=9,
            ),
        ],
        fee_tolerance=0.1,
        slippage_tolerance_bps=2,
        as_of=datetime(2026, 7, 29, tzinfo=UTC),
    )

    assert not report.matched
    assert report.fee_delta == pytest.approx(0.7)
    assert report.max_slippage_delta_bps == 8
    assert [action.action for action in report.draft_actions] == [
        "investigate_cost_model",
        "halt_new_risk",
    ]
    assert all(not action.active for action in report.draft_actions)
    assert all(action.requires_operator_approval for action in report.draft_actions)
    assert report.schedule_activated is False


def test_fault_injection_feed_stale_restart_and_mismatch() -> None:
    clock = ManualClock(100)
    faults = FaultInjector(clock=clock)
    faults.lose_feed()
    assert faults.pass_feed({"price": 10}) is None
    assert faults.make_stale(31) == 69
    assert faults.restart("engine") == 1
    assert faults.restart("engine") == 2
    assert faults.restart("ui") == 1
    faults.mismatch(fee_offset=0.25, slippage_offset_bps=4)
    assert faults.apply_mismatch(1.0, 2.0) == (1.25, 6.0)


def test_fault_injection_broker_timeout_and_503_are_one_shot() -> None:
    faults = FaultInjector()
    faults.broker_timeout()
    faults.broker_503()

    async def scenario() -> None:
        with pytest.raises(TimeoutError, match="injected_broker_timeout"):
            await faults.broker_call(lambda: "not-called")
        with pytest.raises(ServiceUnavailableError) as exc:
            await faults.broker_call(lambda: "not-called")
        assert exc.value.status_code == 503
        assert await faults.broker_call(lambda: "ok") == "ok"

    asyncio.run(scenario())


def test_health_snapshot_is_atomic_and_machine_readable(tmp_path) -> None:
    path = tmp_path / "health.json"
    write_health_snapshot(
        path, status="ready", clock=lambda: 123.0, details={"broker": "connected"}
    )
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "details": {"broker": "connected"},
        "status": "ready",
        "timestamp": 123.0,
    }
    assert not path.with_suffix(".json.tmp").exists()
