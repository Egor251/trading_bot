"""Durable, broker-neutral execution lifecycle.

The module is intentionally independent from runtime/configuration.  Broker SDK
objects are translated at the edge and the persisted model remains portable.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable
from uuid import uuid4


ZERO = Decimal("0")


def _log():
    import logging

    return logging.getLogger("execution")


def _decimal(value: Any, default: Decimal | None = ZERO) -> Decimal | None:
    if value is None or value == "":
        return default
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime | str | None) -> datetime:
    if value is None:
        return _now()
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class OrderState(str, Enum):
    CREATED = "created"
    SUBMITTING = "submitting"
    ACCEPTED = "accepted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCEL_PENDING = "cancel_pending"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


LEGAL_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.CREATED: frozenset({OrderState.SUBMITTING, OrderState.REJECTED}),
    OrderState.SUBMITTING: frozenset(
        {OrderState.ACCEPTED, OrderState.PARTIAL, OrderState.FILLED, OrderState.REJECTED, OrderState.UNKNOWN}
    ),
    OrderState.ACCEPTED: frozenset(
        {OrderState.PARTIAL, OrderState.FILLED, OrderState.CANCEL_PENDING, OrderState.CANCELLED, OrderState.REJECTED, OrderState.UNKNOWN}
    ),
    OrderState.PARTIAL: frozenset(
        {OrderState.PARTIAL, OrderState.FILLED, OrderState.CANCEL_PENDING, OrderState.CANCELLED, OrderState.UNKNOWN}
    ),
    OrderState.CANCEL_PENDING: frozenset(
        {OrderState.CANCELLED, OrderState.PARTIAL, OrderState.FILLED, OrderState.ACCEPTED, OrderState.UNKNOWN}
    ),
    OrderState.UNKNOWN: frozenset(
        {OrderState.ACCEPTED, OrderState.PARTIAL, OrderState.FILLED, OrderState.CANCEL_PENDING, OrderState.CANCELLED, OrderState.REJECTED}
    ),
    OrderState.FILLED: frozenset(),
    OrderState.CANCELLED: frozenset(),
    OrderState.REJECTED: frozenset(),
}


class IllegalOrderTransition(ValueError):
    pass


class DuplicateClientId(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BrokerCapabilities:
    native_stop: bool = False
    native_oco: bool = False
    native_replace: bool = False
    client_order_id: bool = True
    fetch_fills: bool = True

    @property
    def native_stop_orders(self) -> bool:
        return self.native_stop

    @property
    def native_oco_orders(self) -> bool:
        return self.native_oco

    @property
    def supports_native_stop(self) -> bool:
        return self.native_stop

    @property
    def supports_native_oco(self) -> bool:
        return self.native_oco


@dataclass(slots=True)
class BrokerOrder:
    symbol: str
    side: OrderSide | str
    quantity: Decimal | int | float | str
    venue: str
    account: str
    client_id: str = field(default_factory=lambda: uuid4().hex)
    order_type: str = "market"
    limit_price: Decimal | int | float | str | None = None
    stop_price: Decimal | int | float | str | None = None
    time_in_force: str = "day"
    state: OrderState | str = OrderState.CREATED
    broker_order_id: str | None = None
    filled_quantity: Decimal | int | float | str = ZERO
    average_fill_price: Decimal | int | float | str | None = None
    revision: int = 0
    idempotency_key: str | None = None
    parent_order_id: str | None = None
    oco_group_id: str | None = None
    created_at: datetime | str = field(default_factory=_now)
    updated_at: datetime | str = field(default_factory=_now)
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.side = OrderSide(self.side)
        self.state = OrderState(self.state)
        self.quantity = _decimal(self.quantity)
        self.limit_price = _decimal(self.limit_price, None)
        self.stop_price = _decimal(self.stop_price, None)
        self.filled_quantity = _decimal(self.filled_quantity)
        self.average_fill_price = _decimal(self.average_fill_price, None)
        self.created_at, self.updated_at = _utc(self.created_at), _utc(self.updated_at)
        self.idempotency_key = self.idempotency_key or self.client_id
        if not all((self.symbol, self.venue, self.account, self.client_id)):
            raise ValueError("symbol, venue, account and client_id are required")
        if self.quantity is None or self.quantity <= ZERO:
            raise ValueError("quantity must be positive")
        if self.filled_quantity is None or not ZERO <= self.filled_quantity <= self.quantity:
            raise ValueError("filled quantity must be within order quantity")

    @property
    def remaining_quantity(self) -> Decimal:
        return self.quantity - self.filled_quantity

    def transition(self, state: OrderState | str) -> "BrokerOrder":
        target = OrderState(state)
        if target == self.state:
            return self
        if target not in LEGAL_TRANSITIONS[self.state]:
            raise IllegalOrderTransition(f"{self.state.value} -> {target.value}")
        self.state, self.updated_at = target, _now()
        return self

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("quantity", "limit_price", "stop_price", "filled_quantity", "average_fill_price"):
            data[key] = str(data[key]) if data[key] is not None else None
        data["side"], data["state"] = self.side.value, self.state.value
        data["created_at"], data["updated_at"] = self.created_at.isoformat(), self.updated_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BrokerOrder":
        return cls(**dict(data))


@dataclass(frozen=True, slots=True)
class Fill:
    fill_id: str
    broker_order_id: str
    client_id: str
    venue: str
    account: str
    symbol: str
    side: OrderSide | str
    quantity: Decimal | int | float | str
    price: Decimal | int | float | str
    timestamp: datetime | str = field(default_factory=_now)
    fee: Decimal | int | float | str = ZERO
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "side", OrderSide(self.side))
        object.__setattr__(self, "quantity", _decimal(self.quantity))
        object.__setattr__(self, "price", _decimal(self.price))
        object.__setattr__(self, "fee", _decimal(self.fee))
        object.__setattr__(self, "timestamp", _utc(self.timestamp))
        if self.quantity <= ZERO or self.price <= ZERO:
            raise ValueError("fill quantity and price must be positive")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update(
            side=self.side.value,
            quantity=str(self.quantity),
            price=str(self.price),
            fee=str(self.fee),
            timestamp=self.timestamp.isoformat(),
            raw=dict(self.raw),
        )
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Fill":
        return cls(**dict(data))


BrokerFill = Fill


@dataclass(frozen=True, slots=True)
class Balance:
    venue: str
    account: str
    currency: str
    total: Decimal | int | float | str
    available: Decimal | int | float | str
    timestamp: datetime | str = field(default_factory=_now)
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "total", _decimal(self.total))
        object.__setattr__(self, "available", _decimal(self.available))
        object.__setattr__(self, "timestamp", _utc(self.timestamp))

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "total": str(self.total), "available": str(self.available), "timestamp": self.timestamp.isoformat(), "raw": dict(self.raw)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Balance":
        return cls(**dict(data))


BrokerBalance = Balance


@dataclass(frozen=True, slots=True)
class Position:
    venue: str
    account: str
    symbol: str
    quantity: Decimal | int | float | str
    average_price: Decimal | int | float | str | None = None
    timestamp: datetime | str = field(default_factory=_now)
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "quantity", _decimal(self.quantity))
        object.__setattr__(self, "average_price", _decimal(self.average_price, None))
        object.__setattr__(self, "timestamp", _utc(self.timestamp))

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "quantity": str(self.quantity), "average_price": str(self.average_price) if self.average_price is not None else None, "timestamp": self.timestamp.isoformat(), "raw": dict(self.raw)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Position":
        return cls(**dict(data))


BrokerPosition = Position


@runtime_checkable
class ExecutionPort(Protocol):
    capabilities: BrokerCapabilities

    async def place(self, order: BrokerOrder) -> BrokerOrder: ...
    async def cancel(self, order: BrokerOrder, *, idempotency_key: str) -> BrokerOrder: ...
    async def replace(self, order: BrokerOrder, *, quantity: Decimal | None = None, limit_price: Decimal | None = None, stop_price: Decimal | None = None, idempotency_key: str) -> BrokerOrder: ...
    async def status(self, order: BrokerOrder) -> BrokerOrder: ...
    async def open_orders(self) -> list[BrokerOrder]: ...
    async def positions(self) -> list[Position]: ...
    async def balances(self) -> list[Balance]: ...
    async def fills(self, since: datetime | None = None) -> list[Fill]: ...


BrokerExecutionPort = ExecutionPort


class OrderStore:
    """Atomic JSON store; every mutation is fsync'ed then atomically replaced.

    A corrupt or truncated file used to raise out of ``__init__``, which killed
    ``ExecutionService.on_initialize`` and with it *both* reconciliation and the
    new-risk gate — i.e. the checks that stop trading when state disagrees with
    the broker. The file is now quarantined and the store starts empty, with a
    loud error, so the engine boots in a visible fail-safe state instead of a
    silently unprotected one.
    """

    #: Terminal order states that may be pruned once they exceed this many.
    _TERMINAL = frozenset({"filled", "cancelled", "rejected", "expired", "unknown"})
    #: Keep at most this many terminal orders / fills in memory + on disk.
    retention_orders: int = 5000
    retention_fills: int = 20000

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = RLock()
        self._data: dict[str, Any] = {"version": 1, "orders": {}, "fills": {}, "metadata": {}}
        self._idempotency_index: dict[str, str] = {}
        self.corrupt: bool = False
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError("order store root is not an object")
                self._data.update(loaded)
                self._rebuild_idempotency_index()
            except (OSError, ValueError) as exc:
                self.corrupt = True
                backup = self.path.with_name(f"{self.path.name}.corrupt")
                try:
                    os.replace(self.path, backup)
                except OSError:
                    pass
                _log().critical(
                    "order store at %s is unreadable (%s); quarantined to %s and "
                    "started EMPTY. Reconciliation and the new-risk gate are the "
                    "only thing protecting the account — reconcile manually before "
                    "trading.",
                    self.path,
                    exc,
                    backup,
                )

    def _prune_locked(self) -> None:
        """Bound the store so per-order flush cost does not grow without limit."""
        orders = self._data["orders"]
        if len(orders) <= self.retention_orders:
            return
        terminal = [
            (str(item.get("updated_at") or item.get("created_at") or ""), cid)
            for cid, item in orders.items()
            if str(item.get("state") or "").lower() in self._TERMINAL
        ]
        terminal.sort()
        excess = len(orders) - self.retention_orders
        for _ts, cid in terminal[:excess]:
            orders.pop(cid, None)
            pruned = self._idempotency_index
            for key, owner in list(pruned.items()):
                if owner == cid:
                    pruned.pop(key, None)

        fills = self._data["fills"]
        if len(fills) > self.retention_fills:
            ordered = sorted(
                fills.items(), key=lambda kv: str((kv[1] or {}).get("timestamp") or "")
            )
            for key, _value in ordered[: len(fills) - self.retention_fills]:
                fills.pop(key, None)

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(self._data, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self.path)

    def save(self, order: BrokerOrder, *, allow_existing: bool = True) -> BrokerOrder:
        with self._lock:
            if not allow_existing and order.client_id in self._data["orders"]:
                raise DuplicateClientId(order.client_id)
            previous = self._data["orders"].get(order.client_id) or {}
            stale_key = str(previous.get("idempotency_key") or "")
            self._data["orders"][order.client_id] = order.to_dict()
            key = str(order.idempotency_key or "")
            if key:
                self._idempotency_index[key] = order.client_id
            if stale_key and stale_key != key:
                self._idempotency_index.pop(stale_key, None)
            self._prune_locked()
            self._flush()
        return order

    save_order = save

    def get(self, client_id: str) -> BrokerOrder | None:
        with self._lock:
            data = self._data["orders"].get(client_id)
            return BrokerOrder.from_dict(data) if data else None

    get_order = get

    def find_idempotency_key(self, key: str) -> BrokerOrder | None:
        # O(1) via the maintained index. The previous implementation was a linear
        # scan of every order ever placed, executed on the hot `place()` path.
        with self._lock:
            client_id = self._idempotency_index.get(str(key))
            if client_id is None:
                return None
            data = self._data["orders"].get(client_id)
            return BrokerOrder.from_dict(data) if data else None

    def _rebuild_idempotency_index(self) -> None:
        self._idempotency_index = {}
        for client_id, data in self._data["orders"].items():
            raw = str(data.get("idempotency_key") or "")
            if raw:
                self._idempotency_index[raw] = client_id

    def orders(self) -> list[BrokerOrder]:
        with self._lock:
            return [BrokerOrder.from_dict(item) for item in self._data["orders"].values()]

    list_orders = orders

    def add_fill(self, fill: Fill) -> bool:
        key = f"{fill.venue}:{fill.account}:{fill.fill_id}"
        with self._lock:
            if key in self._data["fills"]:
                return False
            self._data["fills"][key] = fill.to_dict()
            self._flush()
            return True

    def apply_fill(self, fill: Fill, order: BrokerOrder) -> None:
        """Record *fill* and update *order* in a single durable write.

        ``add_fill`` followed by ``save`` is not atomic: the fill is fsynced
        first, and if the cumulative check or the state transition then raises,
        the order is never updated while the fill is permanently on disk. The
        broker watermark still advances, so the fill is never re-fetched, and
        ``_resolve_absent`` then *resolves* the FILL_BREAK that reported it —
        a real broker fill is dropped from the order state and the incident that
        flagged it is auto-cleared.
        """
        key = f"{fill.venue}:{fill.account}:{fill.fill_id}"
        with self._lock:
            existing = self._data["fills"].get(key)
            if existing is not None:
                return
            previous = [
                Fill.from_dict(item)
                for item in self._data["fills"].values()
                if str(Fill.from_dict(item).client_id) == fill.client_id
            ]
            cumulative = sum((item.quantity for item in previous), ZERO) + fill.quantity
            if cumulative > order.quantity:
                raise ValueError("cumulative fills exceed order quantity")
            notional = (
                sum((item.quantity * item.price for item in previous), ZERO)
                + fill.quantity * fill.price
            )
            # Validate the transition *before* touching the store so a late fill
            # on a terminal order cannot half-apply.
            target = OrderState.FILLED if cumulative == order.quantity else OrderState.PARTIAL
            order.transition(target)

            self._data["fills"][key] = fill.to_dict()
            order.filled_quantity = cumulative
            order.average_fill_price = notional / cumulative if cumulative else None
            self._data["orders"][order.client_id] = order.to_dict()
            self._flush()

    def fills(self, *, client_id: str | None = None) -> list[Fill]:
        with self._lock:
            values = [Fill.from_dict(item) for item in self._data["fills"].values()]
        return [fill for fill in values if client_id is None or fill.client_id == client_id]

    list_fills = fills

    def set_metadata(self, key: str, value: Any) -> None:
        with self._lock:
            self._data["metadata"][key] = value
            self._flush()

    def get_metadata(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data["metadata"].get(key, default)


class ExecutionRouter:
    def __init__(self, store: OrderStore) -> None:
        self.store = store
        self._ports: dict[tuple[str, str], ExecutionPort] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def register(self, venue: str, account: str, port: ExecutionPort) -> None:
        self._ports[(venue, account)] = port

    def port(self, venue: str, account: str) -> ExecutionPort:
        try:
            return self._ports[(venue, account)]
        except KeyError as exc:
            raise KeyError(f"no execution port for {venue}/{account}") from exc

    def capabilities(self, venue: str, account: str) -> BrokerCapabilities:
        return self.port(venue, account).capabilities

    async def place(self, order: BrokerOrder) -> BrokerOrder:
        # One lock per order is correct for serialising *the same* order, but the
        # dict grew one entry per order ever placed and was never pruned. Bound it
        # by dropping the lock for orders that already reached a terminal state.
        lock = self._locks.get(order.client_id)
        if lock is None:
            if len(self._locks) > 4096:
                self._locks = {
                    cid: existing
                    for cid, existing in self._locks.items()
                    if not self._is_terminal(cid)
                }
            lock = self._locks.setdefault(order.client_id, asyncio.Lock())
        async with lock:
            prior = self.store.get(order.client_id)
            idem = self.store.find_idempotency_key(order.idempotency_key or order.client_id)
            if prior or idem:
                existing = prior or idem
                if not self._same_request(existing, order):
                    raise DuplicateClientId(order.client_id)
                return existing
            self.store.save(order, allow_existing=False)
            order.transition(OrderState.SUBMITTING)
            self.store.save(order)
            try:
                result = await self.port(order.venue, order.account).place(order)
            except Exception:
                order.transition(OrderState.UNKNOWN)
                self.store.save(order)
                raise
            merged = self._merge(order, result)
            self.store.save(merged)
            return merged

    async def cancel(self, client_id: str, *, idempotency_key: str | None = None) -> BrokerOrder:
        order = self._required(client_id)
        if order.state in {OrderState.CANCELLED, OrderState.FILLED}:
            return order
        # Place never reached the broker (UNKNOWN + no broker id) — purge locally.
        if order.state is OrderState.UNKNOWN and not order.broker_order_id:
            order.transition(OrderState.CANCELLED)
            self.store.save(order)
            return order
        order.transition(OrderState.CANCEL_PENDING)
        self.store.save(order)
        result = await self.port(order.venue, order.account).cancel(
            order, idempotency_key=idempotency_key or f"{order.idempotency_key}:cancel:{order.revision}"
        )
        merged = self._merge(order, result)
        self.store.save(merged)
        return merged

    async def replace(
        self,
        client_id: str,
        *,
        quantity: Decimal | int | float | str | None = None,
        limit_price: Decimal | int | float | str | None = None,
        stop_price: Decimal | int | float | str | None = None,
        idempotency_key: str | None = None,
    ) -> BrokerOrder:
        order = self._required(client_id)
        if order.state not in {OrderState.ACCEPTED, OrderState.PARTIAL}:
            raise IllegalOrderTransition(f"cannot replace {order.state.value}")
        revision = order.revision + 1
        key = idempotency_key or f"{order.idempotency_key}:replace:{revision}"
        result = await self.port(order.venue, order.account).replace(
            order,
            quantity=_decimal(quantity, None),
            limit_price=_decimal(limit_price, None),
            stop_price=_decimal(stop_price, None),
            idempotency_key=key,
        )
        result.revision = revision
        result.idempotency_key = key
        merged = self._merge(order, result)
        merged.revision, merged.idempotency_key = revision, key
        self.store.save(merged)
        return merged

    async def status(self, client_id: str) -> BrokerOrder:
        order = self._required(client_id)
        result = await self.port(order.venue, order.account).status(order)
        merged = self._merge(order, result)
        self.store.save(merged)
        return merged

    async def sweep_stale(
        self,
        ttl_sec: float,
        *,
        now: datetime | None = None,
        protect_reasons: Sequence[str] = ("protective", "stop", "oco"),
    ) -> list[BrokerOrder]:
        """Cancel resting orders that have outlived their TTL.

        A working order left on the venue is live risk nobody is watching: it can
        fill long after the signal that justified it is irrelevant, and on a
        venue that ignores ``time_in_force`` nothing else would ever remove it.
        Previously the only cleanup purged *local* orphans
        (``runtime.purge_local_orphans``) — records with no broker id — so an
        order the venue was genuinely holding was never reclaimed.

        Protective orders are exempt by default: a protective stop left resting
        is the one order whose expiry would remove protection from a live
        position. Callers can override the exemption via ``protect_reasons``.
        """
        ttl = max(0.0, float(ttl_sec))
        if ttl <= 0:
            return []
        reference = _utc(now) if now is not None else _now()
        cancelled: list[BrokerOrder] = []
        for order in list(self.store.orders()):
            if order.state not in {OrderState.ACCEPTED, OrderState.PARTIAL}:
                continue
            age = (reference - _utc(order.updated_at)).total_seconds()
            if age < ttl:
                continue
            tags = " ".join(
                str(v).lower()
                for v in (
                    order.symbol,
                    order.order_type,
                    str(order.raw.get("reason") or ""),
                    str(order.raw.get("purpose") or ""),
                )
            )
            if any(tag and tag in tags for tag in protect_reasons):
                continue
            try:
                result = await self.port(order.venue, order.account).cancel(
                    order, idempotency_key=f"{order.idempotency_key}:ttl:{ttl:.0f}"
                )
            except Exception:
                # A venue we cannot reach leaves the order live; reconciliation
                # will surface the difference on the next pass rather than us
                # recording a cancellation that never happened.
                continue
            merged = self._merge(order, result)
            if merged.state not in OrderStore._TERMINAL:
                merged = merged.transition(OrderState.CANCELLED)
            self.store.save(merged)
            cancelled.append(merged)
        return cancelled

    async def record_fill(self, fill: Fill) -> BrokerOrder:
        order = self._required(fill.client_id)
        # Atomic: validate, then persist the fill and the order together.
        self.store.apply_fill(fill, order)
        return order

    async def open_orders(self, venue: str, account: str) -> list[BrokerOrder]:
        return await self.port(venue, account).open_orders()

    async def positions(self, venue: str, account: str) -> list[Position]:
        return await self.port(venue, account).positions()

    async def balances(self, venue: str, account: str) -> list[Balance]:
        return await self.port(venue, account).balances()

    async def fills(self, venue: str, account: str, since: datetime | None = None) -> list[Fill]:
        return await self.port(venue, account).fills(since)

    def _required(self, client_id: str) -> BrokerOrder:
        order = self.store.get(client_id)
        if order is None:
            raise KeyError(f"unknown order: {client_id}")
        return order

    def _is_terminal(self, client_id: str) -> bool:
        order = self.store.get(client_id)
        if order is None:
            return True
        return order.state in {
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.REJECTED,
            OrderState.EXPIRED,
        }

    @staticmethod
    def _same_request(left: BrokerOrder, right: BrokerOrder) -> bool:
        fields = (
            "client_id",
            "venue",
            "account",
            "symbol",
            "side",
            "quantity",
            "order_type",
            "limit_price",
            "stop_price",
            "time_in_force",
            "parent_order_id",
            "oco_group_id",
        )
        return all(getattr(left, name) == getattr(right, name) for name in fields)

    @staticmethod
    def _merge(current: BrokerOrder, broker: BrokerOrder) -> BrokerOrder:
        if broker.state != current.state:
            current.transition(broker.state)
        for name in (
            "broker_order_id", "filled_quantity", "average_fill_price", "quantity",
            "limit_price", "stop_price", "time_in_force", "raw",
        ):
            value = getattr(broker, name)
            if value is not None:
                setattr(current, name, value)
        current.updated_at = _now()
        return current


async def _invoke(callable_: Any, *args: Any, **kwargs: Any) -> Any:
    value = callable_(*args, **kwargs)
    return await value if inspect.isawaitable(value) else value


def _state(value: Any) -> OrderState:
    text = str(getattr(value, "value", value) or "").lower()
    aliases = {
        "new": OrderState.ACCEPTED, "open": OrderState.ACCEPTED, "accepted": OrderState.ACCEPTED,
        "partially_filled": OrderState.PARTIAL, "partial": OrderState.PARTIAL,
        "filled": OrderState.FILLED, "closed": OrderState.FILLED,
        "canceled": OrderState.CANCELLED, "cancelled": OrderState.CANCELLED,
        "rejected": OrderState.REJECTED, "expired": OrderState.CANCELLED,
        "pending_cancel": OrderState.CANCEL_PENDING,
    }
    return aliases.get(text, OrderState.UNKNOWN)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "_raw"):
        return dict(value._raw)
    return dict(vars(value))


def _is_post_only(order: "BrokerOrder") -> bool:
    """Whether this order asks to be maker-only.

    Read from the order's ``time_in_force`` (``post_only``/``GTX``) so the flag
    travels with the order through idempotency, persistence and replay instead
    of living in adapter state that a restart would silently drop.
    """
    tif = str(getattr(order, "time_in_force", "") or "").strip().lower()
    if tif in {"post_only", "postonly", "gtx", "maker"}:
        return True
    raw = getattr(order, "raw", None)
    if isinstance(raw, dict) and raw.get("post_only"):
        return True
    return False


class CcxtExecutionAdapter:
    capabilities = BrokerCapabilities(native_stop=True, native_oco=False, native_replace=False)

    #: Whether this venue should receive post-only requests at all. Set from the
    #: execution service config so an operator can turn maker fees off for a
    #: venue that would reject a GTX/post-only order outright.
    post_only_enabled: bool = True

    def __init__(self, client: Any, *, venue: str, account: str) -> None:
        self.client, self.venue, self.account = client, venue, account

    async def place(self, order: BrokerOrder) -> BrokerOrder:
        params: dict[str, Any] = {"clientOrderId": order.idempotency_key}
        if order.stop_price is not None:
            params["stopPrice"] = float(order.stop_price)
        # `time_in_force` and post-only were dropped here, so every order went
        # out as a plain limit/GTC and the venue charged the taker fee. With
        # maker at 0-2 bps against taker 5 bps that is the difference between a
        # strategy that earns and one that does not. Post-only is sent as the
        # venue flag `postOnly`, with `GTX` as the documented fallback for
        # venues that spell it that way.
        tif = str(order.time_in_force or "").strip()
        if tif and tif.lower() not in {"day", "gtc", "gfd"}:
            params["timeInForce"] = tif
        if _is_post_only(order):
            params["postOnly"] = True
            # Must be GTX, not the order's own TIF: a post-only request with a
            # crossing TIF would still take liquidity while claiming not to.
            params["timeInForce"] = "GTX"
        raw = await _invoke(self.client.create_order, order.symbol, order.order_type, order.side.value, float(order.quantity), float(order.limit_price) if order.limit_price is not None else None, params)
        return self._order(raw, order)

    async def cancel(self, order: BrokerOrder, *, idempotency_key: str) -> BrokerOrder:
        raw = await _invoke(self.client.cancel_order, order.broker_order_id, order.symbol, {"clientOrderId": idempotency_key})
        result = self._order(raw, order)
        result.state = OrderState.CANCELLED
        return result

    async def replace(self, order: BrokerOrder, **changes: Any) -> BrokerOrder:
        if hasattr(self.client, "edit_order"):
            raw = await _invoke(self.client.edit_order, order.broker_order_id, order.symbol, order.order_type, order.side.value, float(changes.get("quantity") or order.quantity), float(changes.get("limit_price") or order.limit_price) if (changes.get("limit_price") or order.limit_price) is not None else None, {"clientOrderId": changes["idempotency_key"]})
            return self._order(raw, order)
        await self.cancel(order, idempotency_key=f"{changes['idempotency_key']}:cancel")
        replacement = replace(order, quantity=changes.get("quantity") or order.quantity, limit_price=changes.get("limit_price") if changes.get("limit_price") is not None else order.limit_price, stop_price=changes.get("stop_price") if changes.get("stop_price") is not None else order.stop_price, state=OrderState.SUBMITTING, idempotency_key=changes["idempotency_key"])
        return await self.place(replacement)

    async def status(self, order: BrokerOrder) -> BrokerOrder:
        return self._order(await _invoke(self.client.fetch_order, order.broker_order_id, order.symbol), order)

    async def open_orders(self) -> list[BrokerOrder]:
        """Some venues (Binance spot) require a symbol for fetchOpenOrders."""
        try:
            rows = await _invoke(self.client.fetch_open_orders)
        except Exception:
            rows = []
            symbols: list[str] = []
            markets = getattr(self.client, "markets", None) or {}
            if isinstance(markets, dict) and markets:
                symbols = [str(s) for s in list(markets)[:80]]
            elif getattr(self.client, "symbols", None):
                symbols = [str(s) for s in list(self.client.symbols)[:80]]
            for sym in symbols:
                try:
                    rows.extend(await _invoke(self.client.fetch_open_orders, sym))
                except Exception:
                    continue
            if not rows and not symbols:
                # Last resort: empty book rather than a permanent BROKER_ERROR.
                return []
        return [self._order(raw) for raw in rows]

    async def positions(self) -> list[Position]:
        rows = await _invoke(self.client.fetch_positions)
        return [Position(self.venue, self.account, str(row.get("symbol")), row.get("contracts", row.get("amount", 0)), row.get("entryPrice"), raw=row) for row in rows]

    async def balances(self) -> list[Balance]:
        raw = await _invoke(self.client.fetch_balance)
        currencies = set(raw.get("total", {})) | set(raw.get("free", {}))
        return [Balance(self.venue, self.account, c, raw.get("total", {}).get(c, 0), raw.get("free", {}).get(c, 0), raw=raw.get(c, {})) for c in currencies]

    async def fills(self, since: datetime | None = None) -> list[Fill]:
        rows = await _invoke(self.client.fetch_my_trades, None, int(since.timestamp() * 1000) if since else None)
        return [Fill(str(r["id"]), str(r.get("order", "")), str(r.get("clientOrderId", "")), self.venue, self.account, str(r["symbol"]), r["side"], r["amount"], r["price"], datetime.fromtimestamp(r["timestamp"] / 1000, UTC), r.get("fee", {}).get("cost", 0), r) for r in rows]

    def _order(self, value: Any, original: BrokerOrder | None = None) -> BrokerOrder:
        raw = _mapping(value)
        return BrokerOrder(
            symbol=str(raw.get("symbol") or (original.symbol if original else "")),
            side=raw.get("side") or (original.side if original else "buy"),
            quantity=raw.get("amount") or (original.quantity if original else 1),
            venue=self.venue, account=self.account,
            client_id=str(raw.get("clientOrderId") or (original.client_id if original else raw.get("id"))),
            order_type=str(raw.get("type") or (original.order_type if original else "market")),
            limit_price=raw.get("price"), stop_price=raw.get("stopPrice"),
            state=_state(raw.get("status")), broker_order_id=str(raw.get("id", "")) or None,
            filled_quantity=raw.get("filled", 0), average_fill_price=raw.get("average"),
            idempotency_key=(original.idempotency_key if original else raw.get("clientOrderId")), raw=raw,
        )


class AlpacaExecutionAdapter:
    capabilities = BrokerCapabilities(native_stop=True, native_oco=True, native_replace=True)

    def __init__(self, client: Any, *, account: str, venue: str = "alpaca") -> None:
        self.client, self.venue, self.account = client, venue, account

    async def place(self, order: BrokerOrder) -> BrokerOrder:
        request = {"symbol": order.symbol, "qty": float(order.quantity), "side": order.side.value, "type": order.order_type, "time_in_force": order.time_in_force, "client_order_id": order.idempotency_key}
        if order.limit_price is not None:
            request["limit_price"] = float(order.limit_price)
        if order.stop_price is not None:
            request["stop_price"] = float(order.stop_price)
        if order.oco_group_id:
            request["order_class"] = "oco"
        return self._order(await _invoke(self.client.submit_order, request), order)

    async def cancel(self, order: BrokerOrder, *, idempotency_key: str) -> BrokerOrder:
        await _invoke(self.client.cancel_order_by_id, order.broker_order_id)
        result = replace(order)
        result.state = OrderState.CANCELLED
        return result

    async def replace(self, order: BrokerOrder, **changes: Any) -> BrokerOrder:
        request = {"qty": float(changes["quantity"])} if changes.get("quantity") is not None else {}
        if changes.get("limit_price") is not None:
            request["limit_price"] = float(changes["limit_price"])
        if changes.get("stop_price") is not None:
            request["stop_price"] = float(changes["stop_price"])
        request["client_order_id"] = changes["idempotency_key"]
        return self._order(await _invoke(self.client.replace_order_by_id, order.broker_order_id, request), order)

    async def status(self, order: BrokerOrder) -> BrokerOrder:
        return self._order(await _invoke(self.client.get_order_by_id, order.broker_order_id), order)

    async def open_orders(self) -> list[BrokerOrder]:
        return [self._order(row) for row in await _invoke(self.client.get_orders, {"status": "open"})]

    async def positions(self) -> list[Position]:
        return [Position(self.venue, self.account, str(_mapping(r).get("symbol")), _mapping(r).get("qty", 0), _mapping(r).get("avg_entry_price"), raw=_mapping(r)) for r in await _invoke(self.client.get_all_positions)]

    async def balances(self) -> list[Balance]:
        raw = _mapping(await _invoke(self.client.get_account))
        return [Balance(self.venue, self.account, "USD", raw.get("equity", 0), raw.get("cash", 0), raw=raw)]

    async def fills(self, since: datetime | None = None) -> list[Fill]:
        rows = await _invoke(self.client.get_activities, {"activity_types": ["FILL"], "after": since})
        out = []
        for row in rows:
            r = _mapping(row)
            out.append(Fill(str(r.get("id")), str(r.get("order_id")), str(r.get("client_order_id", "")), self.venue, self.account, str(r["symbol"]), r["side"], r["qty"], r["price"], r.get("transaction_time"), r.get("net_amount", 0), r))
        return out

    def _order(self, value: Any, original: BrokerOrder | None = None) -> BrokerOrder:
        raw = _mapping(value)
        return BrokerOrder(
            symbol=str(raw.get("symbol") or (original.symbol if original else "")),
            side=raw.get("side") or (original.side if original else "buy"),
            quantity=raw.get("qty") or (original.quantity if original else 1),
            venue=self.venue, account=self.account,
            client_id=str(raw.get("client_order_id") or (original.client_id if original else raw.get("id"))),
            order_type=str(raw.get("type") or (original.order_type if original else "market")),
            limit_price=raw.get("limit_price"), stop_price=raw.get("stop_price"),
            state=_state(raw.get("status")), broker_order_id=str(raw.get("id", "")) or None,
            filled_quantity=raw.get("filled_qty", 0), average_fill_price=raw.get("filled_avg_price"),
            idempotency_key=(original.idempotency_key if original else raw.get("client_order_id")), raw=raw,
        )


class QuikExecutionAdapter:
    capabilities = BrokerCapabilities(native_stop=True, native_oco=False, native_replace=True)

    def __init__(self, client: Any, *, account: str, class_code: str = "TQBR", venue: str = "quik", client_code: str = "") -> None:
        self.client, self.account, self.class_code, self.venue, self.client_code = client, account, class_code, venue, client_code

    async def place(self, order: BrokerOrder) -> BrokerOrder:
        tx = {"ACTION": "NEW_STOP_ORDER" if order.stop_price is not None else "NEW_ORDER", "CLASSCODE": self.class_code, "SECCODE": order.symbol, "ACCOUNT": self.account, "CLIENT_CODE": self.client_code, "OPERATION": "B" if order.side is OrderSide.BUY else "S", "QUANTITY": str(order.quantity), "PRICE": str(order.limit_price or 0), "STOPPRICE": str(order.stop_price or 0), "TRANS_ID": order.idempotency_key}
        return self._order(await _invoke(self.client.send_transaction, tx), order)

    async def cancel(self, order: BrokerOrder, *, idempotency_key: str) -> BrokerOrder:
        raw = await _invoke(self.client.send_transaction, {"ACTION": "KILL_ORDER", "CLASSCODE": self.class_code, "SECCODE": order.symbol, "ACCOUNT": self.account, "ORDER_KEY": order.broker_order_id, "TRANS_ID": idempotency_key})
        result = self._order(raw, order)
        result.state = OrderState.CANCELLED if _state(_mapping(raw).get("status")) in {OrderState.ACCEPTED, OrderState.UNKNOWN} else result.state
        return result

    async def replace(self, order: BrokerOrder, **changes: Any) -> BrokerOrder:
        raw = await _invoke(self.client.send_transaction, {"ACTION": "MOVE_ORDERS", "CLASSCODE": self.class_code, "SECCODE": order.symbol, "ACCOUNT": self.account, "FIRST_ORDER_NUMBER": order.broker_order_id, "FIRST_ORDER_NEW_QUANTITY": str(changes.get("quantity") or order.quantity), "FIRST_ORDER_NEW_PRICE": str(changes.get("limit_price") or order.limit_price or 0), "TRANS_ID": changes["idempotency_key"]})
        result = self._order(raw, order)
        result.quantity = changes.get("quantity") or order.quantity
        result.limit_price = changes.get("limit_price") if changes.get("limit_price") is not None else order.limit_price
        return result

    async def status(self, order: BrokerOrder) -> BrokerOrder:
        raw = await _invoke(self.client.get_order, order.broker_order_id)
        return self._order(raw, order)

    async def open_orders(self) -> list[BrokerOrder]:
        return [self._order(row) for row in await _invoke(self.client.get_orders)]

    async def positions(self) -> list[Position]:
        return [Position(self.venue, self.account, str(r.get("sec_code") or r.get("symbol")), r.get("currentbal", r.get("quantity", 0)), r.get("avrposnprice"), raw=r) for r in await _invoke(self.client.get_positions)]

    async def balances(self) -> list[Balance]:
        return [Balance(self.venue, self.account, str(r.get("currency", "RUB")), r.get("currentbal", r.get("total", 0)), r.get("available", r.get("currentbal", 0)), raw=r) for r in await _invoke(self.client.get_balances)]

    async def fills(self, since: datetime | None = None) -> list[Fill]:
        rows = await _invoke(self.client.get_trades, since)
        return [Fill(str(r.get("trade_num") or r.get("id")), str(r.get("order_num") or r.get("order_id")), str(r.get("client_id", "")), self.venue, self.account, str(r.get("sec_code") or r.get("symbol")), "buy" if str(r.get("flags", r.get("side", "buy"))).lower() in {"buy", "b", "0"} else "sell", r.get("qty", r.get("quantity")), r.get("price"), r.get("datetime") or r.get("timestamp"), r.get("fee", 0), r) for r in rows]

    def _order(self, value: Any, original: BrokerOrder | None = None) -> BrokerOrder:
        raw = _mapping(value)
        status = _state(raw.get("status") or raw.get("state"))
        if status is OrderState.UNKNOWN and str(raw.get("result", "")).lower() in {"ok", "success", "sent"}:
            status = OrderState.ACCEPTED
        return BrokerOrder(
            symbol=str(raw.get("sec_code") or raw.get("symbol") or (original.symbol if original else "")),
            side=raw.get("side") or (original.side if original else "buy"),
            quantity=raw.get("qty") or raw.get("quantity") or (original.quantity if original else 1),
            venue=self.venue, account=self.account,
            client_id=str(raw.get("client_id") or (original.client_id if original else raw.get("trans_id"))),
            order_type=(original.order_type if original else "limit"),
            limit_price=raw.get("price") if raw.get("price") is not None else (original.limit_price if original else None),
            stop_price=raw.get("stop_price") if raw.get("stop_price") is not None else (original.stop_price if original else None),
            state=status, broker_order_id=str(raw.get("order_num") or raw.get("order_id") or "") or (original.broker_order_id if original else None),
            filled_quantity=raw.get("filled_qty", 0), average_fill_price=raw.get("avg_price"),
            idempotency_key=(original.idempotency_key if original else raw.get("trans_id")), raw=raw,
        )


CCXTExecutionAdapter = CcxtExecutionAdapter
QUIKExecutionAdapter = QuikExecutionAdapter
