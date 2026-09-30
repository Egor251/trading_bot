"""Dependency-light production observability and operational safety primitives.

The module intentionally has no imports from trading business modules.  It can
be used by the engine, adapters, tests, and one-shot operational jobs without
bringing in a metrics SDK or activating any background scheduler.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
import inspect
import json
import math
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Sequence


Clock = Callable[[], float]
Labels = tuple[tuple[str, str], ...]
_METRIC_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _labels(values: Mapping[str, object] | None) -> Labels:
    return tuple(sorted((str(key), str(value)) for key, value in (values or {}).items()))


def _escape(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _label_text(labels: Labels, extra: tuple[str, object] | None = None) -> str:
    values = list(labels)
    if extra is not None:
        values.append((extra[0], str(extra[1])))
    if not values:
        return ""
    return "{" + ",".join(f'{key}="{_escape(value)}"' for key, value in values) + "}"


def _number(value: float) -> str:
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    return f"{value:.15g}"


class Metric:
    """Base for a thread-safe metric family with explicit labels."""

    metric_type = "untyped"

    def __init__(self, name: str, description: str, label_names: Sequence[str] = ()) -> None:
        if not _METRIC_RE.match(name):
            raise ValueError(f"invalid metric name: {name!r}")
        if any(not _LABEL_RE.match(label) for label in label_names):
            raise ValueError("invalid metric label name")
        self.name = name
        self.description = description
        self.label_names = tuple(label_names)
        self._lock = threading.RLock()

    def _key(self, labels: Mapping[str, object] | None) -> Labels:
        key = _labels(labels)
        if tuple(name for name, _ in key) != tuple(sorted(self.label_names)):
            raise ValueError(
                f"{self.name} requires labels {sorted(self.label_names)}, "
                f"got {[name for name, _ in key]}"
            )
        return key

    def samples(self) -> list[str]:
        raise NotImplementedError


class Counter(Metric):
    metric_type = "counter"

    def __init__(self, name: str, description: str, label_names: Sequence[str] = ()) -> None:
        super().__init__(name, description, label_names)
        self._values: defaultdict[Labels, float] = defaultdict(float)

    def inc(self, amount: float = 1.0, labels: Mapping[str, object] | None = None) -> None:
        if amount < 0:
            raise ValueError("counter cannot decrease")
        key = self._key(labels)
        with self._lock:
            self._values[key] += float(amount)

    def value(self, labels: Mapping[str, object] | None = None) -> float:
        key = self._key(labels)
        with self._lock:
            return self._values[key]

    def samples(self) -> list[str]:
        with self._lock:
            return [
                f"{self.name}{_label_text(key)} {_number(value)}"
                for key, value in sorted(self._values.items())
            ]


class Gauge(Metric):
    metric_type = "gauge"

    def __init__(self, name: str, description: str, label_names: Sequence[str] = ()) -> None:
        super().__init__(name, description, label_names)
        self._values: defaultdict[Labels, float] = defaultdict(float)

    def set(self, value: float, labels: Mapping[str, object] | None = None) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = float(value)

    def inc(self, amount: float = 1.0, labels: Mapping[str, object] | None = None) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] += float(amount)

    def dec(self, amount: float = 1.0, labels: Mapping[str, object] | None = None) -> None:
        self.inc(-amount, labels)

    def value(self, labels: Mapping[str, object] | None = None) -> float:
        key = self._key(labels)
        with self._lock:
            return self._values[key]

    def samples(self) -> list[str]:
        with self._lock:
            return [
                f"{self.name}{_label_text(key)} {_number(value)}"
                for key, value in sorted(self._values.items())
            ]


@dataclass
class _HistogramValue:
    count: int = 0
    total: float = 0.0
    buckets: list[int] = field(default_factory=list)


class Histogram(Metric):
    metric_type = "histogram"

    def __init__(
        self,
        name: str,
        description: str,
        buckets: Sequence[float],
        label_names: Sequence[str] = (),
    ) -> None:
        super().__init__(name, description, label_names)
        finite = tuple(sorted(set(float(value) for value in buckets)))
        if not finite or finite[0] < 0 or any(math.isinf(value) for value in finite):
            raise ValueError("histogram needs finite non-negative buckets")
        self.buckets = finite
        self._values: dict[Labels, _HistogramValue] = {}

    def observe(self, value: float, labels: Mapping[str, object] | None = None) -> None:
        key = self._key(labels)
        number = float(value)
        with self._lock:
            state = self._values.setdefault(
                key, _HistogramValue(buckets=[0] * len(self.buckets))
            )
            state.count += 1
            state.total += number
            for index, upper in enumerate(self.buckets):
                if number <= upper:
                    state.buckets[index] += 1

    def samples(self) -> list[str]:
        output: list[str] = []
        with self._lock:
            for labels, state in sorted(self._values.items()):
                for upper, count in zip(self.buckets, state.buckets):
                    output.append(
                        f"{self.name}_bucket{_label_text(labels, ('le', _number(upper)))} "
                        f"{count}"
                    )
                output.append(
                    f'{self.name}_bucket{_label_text(labels, ("le", "+Inf"))} {state.count}'
                )
                output.append(f"{self.name}_sum{_label_text(labels)} {_number(state.total)}")
                output.append(f"{self.name}_count{_label_text(labels)} {state.count}")
        return output


class MetricsRegistry:
    """Small Prometheus text-format registry."""

    def __init__(self) -> None:
        self._metrics: dict[str, Metric] = {}
        self._lock = threading.RLock()

    def register(self, metric: Metric) -> Metric:
        with self._lock:
            if metric.name in self._metrics:
                raise ValueError(f"metric already registered: {metric.name}")
            self._metrics[metric.name] = metric
        return metric

    def counter(
        self, name: str, description: str, label_names: Sequence[str] = ()
    ) -> Counter:
        return self.register(Counter(name, description, label_names))  # type: ignore[return-value]

    def gauge(
        self, name: str, description: str, label_names: Sequence[str] = ()
    ) -> Gauge:
        return self.register(Gauge(name, description, label_names))  # type: ignore[return-value]

    def histogram(
        self,
        name: str,
        description: str,
        buckets: Sequence[float],
        label_names: Sequence[str] = (),
    ) -> Histogram:
        return self.register(  # type: ignore[return-value]
            Histogram(name, description, buckets, label_names)
        )

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            metrics = list(sorted(self._metrics.values(), key=lambda metric: metric.name))
        for metric in metrics:
            lines.append(f"# HELP {metric.name} {_escape(metric.description)}")
            lines.append(f"# TYPE {metric.name} {metric.metric_type}")
            lines.extend(metric.samples())
        return "\n".join(lines) + "\n"


class AuditStage(str, Enum):
    SIGNAL = "signal"
    RISK = "risk"
    ORDER_REVISION = "order_revision"
    FILL = "fill"
    COST = "cost"
    PROTECTION = "protection"
    RECONCILIATION = "reconciliation"
    COMMAND = "command"


@dataclass(frozen=True)
class AuditEvent:
    sequence: int
    timestamp: str
    trace_id: str
    stage: str
    event: str
    actor: str
    details: Mapping[str, Any]


class AuditTimeline:
    """Append-only structured timeline, optionally mirrored to JSON Lines."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        clock: Clock = time.time,
        max_events: int = 10_000,
    ) -> None:
        self.path = Path(path) if path is not None else None
        self.clock = clock
        self._events: deque[AuditEvent] = deque(maxlen=max_events)
        self._sequence = 0
        self._lock = threading.RLock()

    def record(
        self,
        trace_id: str,
        stage: AuditStage | str,
        event: str,
        *,
        actor: str = "engine",
        details: Mapping[str, Any] | None = None,
    ) -> AuditEvent:
        if not trace_id:
            raise ValueError("trace_id is required")
        clean_details = json.loads(
            json.dumps(dict(details or {}), sort_keys=True, default=str, allow_nan=False)
        )
        with self._lock:
            self._sequence += 1
            stamp = datetime.fromtimestamp(self.clock(), tz=UTC).isoformat()
            item = AuditEvent(
                sequence=self._sequence,
                timestamp=stamp,
                trace_id=trace_id,
                stage=stage.value if isinstance(stage, AuditStage) else str(stage),
                event=str(event),
                actor=str(actor),
                details=clean_details,
            )
            self._events.append(item)
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(asdict(item), sort_keys=True) + "\n")
            return item

    def trace(self, trace_id: str) -> list[AuditEvent]:
        with self._lock:
            return [item for item in self._events if item.trace_id == trace_id]

    def events(self) -> list[AuditEvent]:
        with self._lock:
            return list(self._events)


@dataclass(frozen=True)
class Alert:
    name: str
    severity: str
    summary: str
    value: float
    threshold: float
    labels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AlertThresholds:
    stale_feed_seconds: float = 30.0
    fee_mismatch: float = 0.01
    slippage_mismatch_bps: float = 5.0
    drawdown_ratio: float = 0.08


class OperationsObservability:
    """Metrics, audit trail, and deterministic operational alert evaluation."""

    LATENCY_BUCKETS = (0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0)

    def __init__(
        self,
        *,
        registry: MetricsRegistry | None = None,
        audit: AuditTimeline | None = None,
        clock: Clock = time.time,
        thresholds: AlertThresholds | None = None,
    ) -> None:
        self.registry = registry or MetricsRegistry()
        self.audit = audit or AuditTimeline(clock=clock)
        self.clock = clock
        self.thresholds = thresholds or AlertThresholds()

        self.feed_last_seen = self.registry.gauge(
            "trading_feed_last_seen_seconds", "Unix timestamp of latest market event", ("feed",)
        )
        self.broker_up = self.registry.gauge(
            "trading_broker_connected", "Broker connectivity (1 connected)", ("broker",)
        )
        self.reconcile_ok = self.registry.gauge(
            "trading_reconciliation_ok", "Latest reconciliation result (1 matched)", ("account",)
        )
        self.stop_coverage = self.registry.gauge(
            "trading_stop_coverage_ratio", "Protected open position ratio", ("account",)
        )
        self.orders = self.registry.counter(
            "trading_orders_total", "Orders by terminal status", ("broker", "status")
        )
        self.duplicates = self.registry.counter(
            "trading_duplicate_orders_total", "Duplicate orders suppressed", ("broker",)
        )
        self.fee_delta = self.registry.gauge(
            "trading_fee_mismatch_amount", "Absolute expected versus actual fee delta", ("account",)
        )
        self.slippage_delta = self.registry.gauge(
            "trading_slippage_mismatch_bps", "Absolute expected versus actual slippage delta", ("account",)
        )
        self.drawdown = self.registry.gauge(
            "trading_drawdown_ratio", "Current drawdown from peak", ("scope",)
        )
        self.commands = self.registry.counter(
            "trading_operator_commands_total", "Operator commands by audit result", ("audited", "command")
        )
        self.latency = self.registry.histogram(
            "trading_stage_latency_seconds",
            "Signal-to-stage processing latency",
            self.LATENCY_BUCKETS,
            ("stage",),
        )
        self._known_feeds: set[str] = set()
        self._connectivity_brokers: set[str] = set()
        self._order_brokers: set[str] = set()
        self._reconciled_accounts: set[str] = set()
        self._coverage_accounts: set[str] = set()
        self._cost_accounts: set[str] = set()
        self._known_scopes: set[str] = set()
        self._known_commands: set[str] = set()

    def record_stage(
        self,
        trace_id: str,
        stage: AuditStage | str,
        event: str,
        *,
        latency_seconds: float | None = None,
        actor: str = "engine",
        details: Mapping[str, Any] | None = None,
    ) -> AuditEvent:
        stage_name = stage.value if isinstance(stage, AuditStage) else str(stage)
        if latency_seconds is not None:
            self.latency.observe(latency_seconds, {"stage": stage_name})
        return self.audit.record(
            trace_id, stage, event, actor=actor, details=details
        )

    def mark_feed(self, feed: str, *, observed_at: float | None = None) -> None:
        self._known_feeds.add(feed)
        self.feed_last_seen.set(
            self.clock() if observed_at is None else observed_at, {"feed": feed}
        )

    def mark_broker(self, broker: str, connected: bool) -> None:
        self._connectivity_brokers.add(broker)
        self.broker_up.set(1 if connected else 0, {"broker": broker})

    def mark_reconciliation(self, account: str, matched: bool) -> None:
        self._reconciled_accounts.add(account)
        self.reconcile_ok.set(1 if matched else 0, {"account": account})

    def mark_stop_coverage(self, account: str, covered: int, open_positions: int) -> None:
        self._coverage_accounts.add(account)
        ratio = 1.0 if open_positions <= 0 else max(0.0, min(1.0, covered / open_positions))
        self.stop_coverage.set(ratio, {"account": account})

    def record_order(self, broker: str, status: str, *, duplicate: bool = False) -> None:
        self._order_brokers.add(broker)
        self.orders.inc(labels={"broker": broker, "status": status.lower()})
        if duplicate:
            self.duplicates.inc(labels={"broker": broker})

    def mark_cost_mismatch(
        self, account: str, *, fee_amount: float, slippage_bps: float
    ) -> None:
        self._cost_accounts.add(account)
        self.fee_delta.set(abs(fee_amount), {"account": account})
        self.slippage_delta.set(abs(slippage_bps), {"account": account})

    def mark_drawdown(self, scope: str, ratio: float) -> None:
        self._known_scopes.add(scope)
        self.drawdown.set(max(0.0, ratio), {"scope": scope})

    def record_command(
        self,
        command: str,
        *,
        actor: str,
        audited: bool,
        trace_id: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        normalized = command.strip().lower().replace(" ", "_") or "unknown"
        self._known_commands.add(normalized)
        self.commands.inc(
            labels={"audited": str(bool(audited)).lower(), "command": normalized}
        )
        if audited:
            self.record_stage(
                trace_id,
                AuditStage.COMMAND,
                normalized,
                actor=actor,
                details=details,
            )

    def alerts(self, *, now: float | None = None) -> list[Alert]:
        current = self.clock() if now is None else now
        output: list[Alert] = []

        for feed in sorted(self._known_feeds):
            age = current - self.feed_last_seen.value({"feed": feed})
            if age > self.thresholds.stale_feed_seconds:
                output.append(Alert("stale_feed", "critical", "Market feed is stale", age,
                                    self.thresholds.stale_feed_seconds, {"feed": feed}))
        for broker in sorted(self._connectivity_brokers):
            if self.broker_up.value({"broker": broker}) < 1:
                output.append(Alert("broker_disconnect", "critical", "Broker is disconnected", 0, 1,
                                    {"broker": broker}))
        for broker in sorted(self._order_brokers):
            rejected = self.orders.value({"broker": broker, "status": "rejected"})
            if rejected > 0:
                output.append(Alert("rejected_orders", "warning", "Orders were rejected", rejected, 0,
                                    {"broker": broker}))
            duplicates = self.duplicates.value({"broker": broker})
            if duplicates > 0:
                output.append(Alert("duplicate_orders", "critical", "Duplicate orders detected",
                                    duplicates, 0, {"broker": broker}))
        for account in sorted(self._reconciled_accounts):
            if self.reconcile_ok.value({"account": account}) < 1:
                output.append(Alert("reconciliation_break", "critical", "Positions or cash differ",
                                    0, 1, {"account": account}))
        for account in sorted(self._coverage_accounts):
            coverage = self.stop_coverage.value({"account": account})
            if coverage < 1:
                output.append(Alert("stop_coverage", "critical", "Open positions lack protection",
                                    coverage, 1, {"account": account}))
        for account in sorted(self._cost_accounts):
            fee = self.fee_delta.value({"account": account})
            if fee > self.thresholds.fee_mismatch:
                output.append(Alert("fee_mismatch", "warning", "Broker fee differs from model",
                                    fee, self.thresholds.fee_mismatch, {"account": account}))
            slippage = self.slippage_delta.value({"account": account})
            if slippage > self.thresholds.slippage_mismatch_bps:
                output.append(Alert("slippage_mismatch", "warning", "Slippage differs from model",
                                    slippage, self.thresholds.slippage_mismatch_bps,
                                    {"account": account}))
        for scope in sorted(self._known_scopes):
            dd = self.drawdown.value({"scope": scope})
            if dd >= self.thresholds.drawdown_ratio:
                output.append(Alert("drawdown", "critical", "Drawdown threshold reached", dd,
                                    self.thresholds.drawdown_ratio, {"scope": scope}))
        for command in sorted(self._known_commands):
            unaudited = self.commands.value({"audited": "false", "command": command})
            if unaudited > 0:
                output.append(Alert("command_audit", "critical", "Operator command lacked audit",
                                    unaudited, 0, {"command": command}))
        return sorted(output, key=lambda alert: (alert.name, sorted(alert.labels.items())))

    def prometheus_text(self) -> str:
        return self.registry.render()


@dataclass(frozen=True)
class FeeLine:
    execution_id: str
    expected_fee: float
    actual_fee: float
    expected_slippage_bps: float = 0.0
    actual_slippage_bps: float = 0.0


@dataclass(frozen=True)
class DraftAction:
    action: str
    reason: str
    active: bool = False
    requires_operator_approval: bool = True


@dataclass(frozen=True)
class FeeReconciliationReport:
    as_of: str
    checked: int
    fee_delta: float
    max_slippage_delta_bps: float
    matched: bool
    draft_actions: tuple[DraftAction, ...]
    schedule_activated: bool = False


def nightly_fee_reconciliation(
    lines: Iterable[FeeLine | Mapping[str, Any]],
    *,
    fee_tolerance: float = 0.01,
    slippage_tolerance_bps: float = 5.0,
    as_of: datetime | None = None,
) -> FeeReconciliationReport:
    """Build a nightly report and inert action drafts; never schedule or execute them."""

    rows = [line if isinstance(line, FeeLine) else FeeLine(**line) for line in lines]
    fee_delta = sum(row.actual_fee - row.expected_fee for row in rows)
    max_slippage = max(
        (abs(row.actual_slippage_bps - row.expected_slippage_bps) for row in rows),
        default=0.0,
    )
    matched = abs(fee_delta) <= fee_tolerance and max_slippage <= slippage_tolerance_bps
    actions: list[DraftAction] = []
    if not matched:
        actions.append(DraftAction("investigate_cost_model", "fee_or_slippage_mismatch"))
        actions.append(DraftAction("halt_new_risk", "cost_reconciliation_failed"))
    stamp = (as_of or datetime.now(UTC)).astimezone(UTC).isoformat()
    return FeeReconciliationReport(
        as_of=stamp,
        checked=len(rows),
        fee_delta=fee_delta,
        max_slippage_delta_bps=max_slippage,
        matched=matched,
        draft_actions=tuple(actions),
    )


class ServiceUnavailableError(RuntimeError):
    """Synthetic broker HTTP 503 used by deterministic fault tests."""

    status_code = 503


class FaultInjector:
    """Deterministic, opt-in fault helper.  It has no production side effects."""

    def __init__(self, *, clock: Clock = time.time) -> None:
        self.clock = clock
        self.feed_loss = False
        self.stale_by_seconds = 0.0
        self._broker_faults: deque[str] = deque()
        self.engine_generation = 0
        self.ui_generation = 0
        self.fee_offset = 0.0
        self.slippage_offset_bps = 0.0

    def lose_feed(self, enabled: bool = True) -> None:
        self.feed_loss = enabled

    def pass_feed(self, value: Any) -> Any | None:
        return None if self.feed_loss else value

    def make_stale(self, seconds: float) -> float:
        self.stale_by_seconds = max(0.0, float(seconds))
        return self.clock() - self.stale_by_seconds

    def broker_timeout(self, calls: int = 1) -> None:
        self._broker_faults.extend("timeout" for _ in range(max(0, calls)))

    def broker_503(self, calls: int = 1) -> None:
        self._broker_faults.extend("503" for _ in range(max(0, calls)))

    async def broker_call(self, operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if self._broker_faults:
            fault = self._broker_faults.popleft()
            if fault == "timeout":
                raise TimeoutError("injected_broker_timeout")
            raise ServiceUnavailableError("injected_broker_503")
        result = operation(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    def restart(self, component: str) -> int:
        if component == "engine":
            self.engine_generation += 1
            return self.engine_generation
        if component == "ui":
            self.ui_generation += 1
            return self.ui_generation
        raise ValueError("component must be 'engine' or 'ui'")

    def mismatch(self, *, fee_offset: float = 0.0, slippage_offset_bps: float = 0.0) -> None:
        self.fee_offset = float(fee_offset)
        self.slippage_offset_bps = float(slippage_offset_bps)

    def apply_mismatch(self, fee: float, slippage_bps: float) -> tuple[float, float]:
        return fee + self.fee_offset, slippage_bps + self.slippage_offset_bps


def write_health_snapshot(
    path: str | Path,
    *,
    status: str,
    clock: Clock = time.time,
    details: Mapping[str, Any] | None = None,
) -> None:
    """Atomically publish a small state file for an external health check."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "timestamp": clock(),
        "details": dict(details or {}),
    }
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    temporary.replace(target)
