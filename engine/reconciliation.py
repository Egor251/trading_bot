"""Broker-truth reconciliation and durable risk blocking."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Awaitable, Callable, Iterable, Mapping
from uuid import uuid4

from .execution import (
    Balance,
    BrokerOrder,
    ExecutionRouter,
    Fill,
    OrderState,
    OrderStore,
    Position,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


class IncidentKind(str, Enum):
    CASH_BREAK = "cash_break"
    POSITION_BREAK = "position_break"
    ORDER_BREAK = "order_break"
    FILL_BREAK = "fill_break"
    BROKER_ERROR = "broker_error"


class IncidentSeverity(str, Enum):
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(slots=True)
class ReconciliationIncident:
    kind: IncidentKind | str
    venue: str
    account: str
    message: str
    expected: Any = None
    actual: Any = None
    key: str = ""
    severity: IncidentSeverity | str = IncidentSeverity.CRITICAL
    incident_id: str = field(default_factory=lambda: uuid4().hex)
    detected_at: datetime | str = field(default_factory=_now)
    resolved_at: datetime | str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.kind = IncidentKind(self.kind)
        self.severity = IncidentSeverity(self.severity)
        if isinstance(self.detected_at, str):
            self.detected_at = datetime.fromisoformat(self.detected_at)
        if isinstance(self.resolved_at, str):
            self.resolved_at = datetime.fromisoformat(self.resolved_at)

    @property
    def active(self) -> bool:
        return self.resolved_at is None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["kind"], value["severity"] = self.kind.value, self.severity.value
        value["detected_at"] = self.detected_at.isoformat()
        value["resolved_at"] = self.resolved_at.isoformat() if self.resolved_at else None
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReconciliationIncident":
        return cls(**dict(value))


Incident = ReconciliationIncident


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    venue: str
    account: str
    checked_at: datetime
    incidents: tuple[ReconciliationIncident, ...]
    orders: tuple[BrokerOrder, ...]
    positions: tuple[Position, ...]
    balances: tuple[Balance, ...]
    fills: tuple[Fill, ...]

    @property
    def healthy(self) -> bool:
        return not self.incidents


class RiskBlocked(RuntimeError):
    pass


IncidentSink = Callable[[ReconciliationIncident], Awaitable[None] | None]


class ReconciliationService:
    """Compares durable intent with broker truth and fails closed on breaks.

    The expected cash/positions are supplied by the owning portfolio ledger via
    :meth:`set_expected_state`.  Broker snapshots are always persisted, making
    restart recovery deterministic without pretending the local cache is truth.
    """

    def __init__(
        self,
        router: ExecutionRouter,
        store: OrderStore,
        *,
        quantity_tolerance: Decimal | str | float = Decimal("0.00000001"),
        cash_tolerance: Decimal | str | float = Decimal("0.01"),
        incident_sink: IncidentSink | None = None,
    ) -> None:
        self.router = router
        self.store = store
        self.quantity_tolerance = _decimal(quantity_tolerance)
        self.cash_tolerance = _decimal(cash_tolerance)
        self.incident_sink = incident_sink
        self._incidents = [
            ReconciliationIncident.from_dict(item)
            for item in store.get_metadata("reconciliation.incidents", [])
        ]

    @property
    def incidents(self) -> tuple[ReconciliationIncident, ...]:
        return tuple(self._incidents)

    @property
    def active_incidents(self) -> tuple[ReconciliationIncident, ...]:
        return tuple(item for item in self._incidents if item.active)

    @property
    def new_risk_blocked(self) -> bool:
        return any(
            item.active and item.severity is IncidentSeverity.CRITICAL
            for item in self._incidents
        )

    risk_blocked = new_risk_blocked

    def assert_new_risk_allowed(self) -> None:
        if self.new_risk_blocked:
            kinds = ", ".join(sorted({item.kind.value for item in self.active_incidents}))
            raise RiskBlocked(f"new risk blocked by reconciliation: {kinds}")

    def set_expected_state(
        self,
        venue: str,
        account: str,
        *,
        positions: Iterable[Position] = (),
        balances: Iterable[Balance] = (),
    ) -> None:
        key = self._key(venue, account)
        self.store.set_metadata(
            f"reconciliation.expected.{key}",
            {
                "positions": [item.to_dict() for item in positions],
                "balances": [item.to_dict() for item in balances],
            },
        )

    async def startup(self, venue: str, account: str) -> ReconciliationResult:
        return await self.reconcile(venue, account, startup=True)

    async def reconcile(
        self, venue: str, account: str, *, startup: bool = False
    ) -> ReconciliationResult:
        checked_at = _now()
        try:
            orders, positions, balances, fills = await asyncio.gather(
                self.router.open_orders(venue, account),
                self.router.positions(venue, account),
                self.router.balances(venue, account),
                self.router.fills(venue, account, self._last_checked(venue, account)),
            )
        except Exception as exc:
            incident = await self._emit(
                IncidentKind.BROKER_ERROR,
                venue,
                account,
                "broker snapshot failed",
                actual=repr(exc),
                key="snapshot",
            )
            return ReconciliationResult(
                venue, account, checked_at, (incident,), (), (), (), ()
            )

        cycle: list[ReconciliationIncident] = []
        cycle.extend(await self._reconcile_fills(venue, account, fills))
        cycle.extend(await self._reconcile_orders(venue, account, orders))

        expected = self.store.get_metadata(
            f"reconciliation.expected.{self._key(venue, account)}"
        )
        if expected is not None:
            cycle.extend(
                await self._reconcile_positions(
                    venue,
                    account,
                    [Position.from_dict(item) for item in expected.get("positions", [])],
                    positions,
                )
            )
            cycle.extend(
                await self._reconcile_balances(
                    venue,
                    account,
                    [Balance.from_dict(item) for item in expected.get("balances", [])],
                    balances,
                )
            )

        snapshot = {
            "checked_at": checked_at.isoformat(),
            "startup": startup,
            "orders": [item.to_dict() for item in orders],
            "positions": [item.to_dict() for item in positions],
            "balances": [item.to_dict() for item in balances],
            "fills": [item.to_dict() for item in fills],
        }
        key = self._key(venue, account)
        self.store.set_metadata(f"reconciliation.snapshot.{key}", snapshot)
        self.store.set_metadata(f"reconciliation.last_checked.{key}", checked_at.isoformat())
        self._resolve_absent(venue, account, cycle)
        self._persist_incidents()
        return ReconciliationResult(
            venue,
            account,
            checked_at,
            tuple(cycle),
            tuple(orders),
            tuple(positions),
            tuple(balances),
            tuple(fills),
        )

    async def run_periodic(
        self,
        venue: str,
        account: str,
        *,
        interval: float = 30.0,
        stop: asyncio.Event | None = None,
    ) -> None:
        stop = stop or asyncio.Event()
        while not stop.is_set():
            await self.reconcile(venue, account)
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                pass

    async def _reconcile_fills(
        self, venue: str, account: str, broker_fills: list[Fill]
    ) -> list[ReconciliationIncident]:
        incidents: list[ReconciliationIncident] = []
        for fill in broker_fills:
            order = self.store.get(fill.client_id)
            if order is None:
                incidents.append(
                    await self._emit(
                        IncidentKind.FILL_BREAK,
                        venue,
                        account,
                        "broker fill has no local order",
                        actual=fill.to_dict(),
                        key=fill.fill_id,
                    )
                )
                continue
            try:
                await self.router.record_fill(fill)
            except ValueError as exc:
                incidents.append(
                    await self._emit(
                        IncidentKind.FILL_BREAK,
                        venue,
                        account,
                        "invalid cumulative broker fill",
                        expected=str(order.quantity),
                        actual=str(fill.quantity),
                        key=fill.fill_id,
                        details={"error": str(exc)},
                    )
                )
        return incidents

    async def _reconcile_orders(
        self, venue: str, account: str, broker_orders: list[BrokerOrder]
    ) -> list[ReconciliationIncident]:
        incidents: list[ReconciliationIncident] = []
        broker_by_client = {item.client_id: item for item in broker_orders}
        broker_by_id = {item.broker_order_id: item for item in broker_orders if item.broker_order_id}
        local = [
            item
            for item in self.store.orders()
            if item.venue == venue and item.account == account
        ]
        for order in broker_orders:
            if self.store.get(order.client_id) is None and not any(
                item.broker_order_id == order.broker_order_id for item in local
            ):
                incidents.append(
                    await self._emit(
                        IncidentKind.ORDER_BREAK,
                        venue,
                        account,
                        "unmanaged broker order",
                        actual=order.to_dict(),
                        key=order.broker_order_id or order.client_id,
                    )
                )
        active = {
            OrderState.SUBMITTING,
            OrderState.ACCEPTED,
            OrderState.PARTIAL,
            OrderState.CANCEL_PENDING,
            OrderState.UNKNOWN,
        }
        for order in local:
            if order.state in active and order.client_id not in broker_by_client and (
                not order.broker_order_id or order.broker_order_id not in broker_by_id
            ):
                incidents.append(
                    await self._emit(
                        IncidentKind.ORDER_BREAK,
                        venue,
                        account,
                        "local active order absent at broker",
                        expected=order.to_dict(),
                        key=order.broker_order_id or order.client_id,
                    )
                )
        return incidents

    async def _reconcile_positions(
        self,
        venue: str,
        account: str,
        expected: list[Position],
        actual: list[Position],
    ) -> list[ReconciliationIncident]:
        incidents: list[ReconciliationIncident] = []
        wanted = {item.symbol: item.quantity for item in expected if abs(item.quantity) > self.quantity_tolerance}
        found = {item.symbol: item.quantity for item in actual if abs(item.quantity) > self.quantity_tolerance}
        for symbol in sorted(set(wanted) | set(found)):
            if abs(wanted.get(symbol, Decimal("0")) - found.get(symbol, Decimal("0"))) > self.quantity_tolerance:
                incidents.append(
                    await self._emit(
                        IncidentKind.POSITION_BREAK,
                        venue,
                        account,
                        "position differs from portfolio ledger",
                        expected=str(wanted.get(symbol, Decimal("0"))),
                        actual=str(found.get(symbol, Decimal("0"))),
                        key=symbol,
                    )
                )
        return incidents

    async def _reconcile_balances(
        self,
        venue: str,
        account: str,
        expected: list[Balance],
        actual: list[Balance],
    ) -> list[ReconciliationIncident]:
        incidents: list[ReconciliationIncident] = []
        wanted = {item.currency: item.total for item in expected}
        found = {item.currency: item.total for item in actual}
        for currency in sorted(set(wanted) | set(found)):
            if abs(wanted.get(currency, Decimal("0")) - found.get(currency, Decimal("0"))) > self.cash_tolerance:
                incidents.append(
                    await self._emit(
                        IncidentKind.CASH_BREAK,
                        venue,
                        account,
                        "cash differs from portfolio ledger",
                        expected=str(wanted.get(currency, Decimal("0"))),
                        actual=str(found.get(currency, Decimal("0"))),
                        key=currency,
                    )
                )
        return incidents

    async def _emit(
        self,
        kind: IncidentKind,
        venue: str,
        account: str,
        message: str,
        *,
        expected: Any = None,
        actual: Any = None,
        key: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> ReconciliationIncident:
        fingerprint = (kind, venue, account, key)
        for existing in self._incidents:
            if existing.active and (
                existing.kind,
                existing.venue,
                existing.account,
                existing.key,
            ) == fingerprint:
                existing.expected, existing.actual = expected, actual
                existing.details = dict(details or {})
                return existing
        incident = ReconciliationIncident(
            kind,
            venue,
            account,
            message,
            expected,
            actual,
            key,
            details=dict(details or {}),
        )
        self._incidents.append(incident)
        self._persist_incidents()
        if self.incident_sink is not None:
            result = self.incident_sink(incident)
            if asyncio.iscoroutine(result):
                await result
        return incident

    def resolve(self, incident_id: str) -> None:
        for incident in self._incidents:
            if incident.incident_id == incident_id and incident.active:
                incident.resolved_at = _now()
                self._persist_incidents()
                return
        raise KeyError(f"unknown active incident: {incident_id}")

    acknowledge = resolve

    def _resolve_absent(
        self,
        venue: str,
        account: str,
        current: Iterable[ReconciliationIncident],
    ) -> None:
        active_fingerprints = {(item.kind, item.key) for item in current}
        for item in self._incidents:
            if (
                item.active
                and item.venue == venue
                and item.account == account
                and (item.kind, item.key) not in active_fingerprints
            ):
                # Successful broker snapshots clear prior BROKER_ERROR as well —
                # otherwise a transient outage blocks new risk forever.
                item.resolved_at = _now()

    def _persist_incidents(self) -> None:
        self.store.set_metadata(
            "reconciliation.incidents",
            [item.to_dict() for item in self._incidents],
        )

    def _last_checked(self, venue: str, account: str) -> datetime | None:
        value = self.store.get_metadata(
            f"reconciliation.last_checked.{self._key(venue, account)}"
        )
        return datetime.fromisoformat(value) if value else None

    @staticmethod
    def _key(venue: str, account: str) -> str:
        return f"{venue}:{account}"
