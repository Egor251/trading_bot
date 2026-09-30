"""Pure position-protection primitives.

The module deliberately has no dependency on the order manager, runtime, or
configuration layer.  It can therefore be used by live adapters, backtests,
and recovery code without importing application state.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from threading import RLock
from typing import Any, Iterable, Mapping, Sequence


ZERO = Decimal("0")


def _decimal(value: Decimal | int | float | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class StrategyArchetype(str, Enum):
    TREND = "trend"
    MEAN_REVERSION = "mean_reversion"
    BREAKOUT = "breakout"


class PositionSide(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> Decimal:
        return Decimal("1") if self is PositionSide.LONG else Decimal("-1")


class RuleKind(str, Enum):
    INITIAL_STOP = "initial_stop"
    MAX_LOSS = "max_loss"
    STRUCTURE = "structure"
    CHANDELIER = "chandelier"
    TIME = "time"


class ExitState(str, Enum):
    OPEN = "open"
    EXIT_PENDING = "exit_pending"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class StrategyRiskProfile:
    """Validated policy inputs for a strategy archetype."""

    archetype: StrategyArchetype | str
    atr_period: int = 14
    initial_atr_multiple: Decimal = Decimal("2")
    chandelier_atr_multiple: Decimal = Decimal("3")
    max_loss_fraction: Decimal = Decimal("0.02")
    structure_lookback: int = 10
    structure_buffer_atr: Decimal = Decimal("0.1")
    max_holding_period: timedelta | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "archetype", StrategyArchetype(self.archetype))
        for name in (
            "initial_atr_multiple",
            "chandelier_atr_multiple",
            "max_loss_fraction",
            "structure_buffer_atr",
        ):
            object.__setattr__(self, name, _decimal(getattr(self, name)))
        if self.atr_period < 1 or self.structure_lookback < 1:
            raise ValueError("ATR period and structure lookback must be positive")
        if self.initial_atr_multiple <= ZERO or self.chandelier_atr_multiple <= ZERO:
            raise ValueError("ATR multiples must be positive")
        if not ZERO < self.max_loss_fraction < Decimal("1"):
            raise ValueError("max_loss_fraction must be between zero and one")
        if self.structure_buffer_atr < ZERO:
            raise ValueError("structure_buffer_atr cannot be negative")
        if self.max_holding_period is not None and self.max_holding_period <= timedelta(0):
            raise ValueError("max_holding_period must be positive")

    @classmethod
    def for_archetype(
        cls, archetype: StrategyArchetype | str, **overrides: Any
    ) -> "StrategyRiskProfile":
        archetype = StrategyArchetype(archetype)
        defaults: dict[str, Any] = {
            StrategyArchetype.TREND: {
                "initial_atr_multiple": Decimal("2"),
                "chandelier_atr_multiple": Decimal("3"),
                "max_loss_fraction": Decimal("0.025"),
                "max_holding_period": None,
            },
            StrategyArchetype.MEAN_REVERSION: {
                "initial_atr_multiple": Decimal("1.25"),
                "chandelier_atr_multiple": Decimal("1.75"),
                "max_loss_fraction": Decimal("0.015"),
                "max_holding_period": timedelta(days=5),
            },
            StrategyArchetype.BREAKOUT: {
                "initial_atr_multiple": Decimal("2.5"),
                "chandelier_atr_multiple": Decimal("3.5"),
                "max_loss_fraction": Decimal("0.03"),
                "max_holding_period": timedelta(days=20),
            },
        }[archetype]
        return cls(archetype=archetype, **{**defaults, **overrides})

    def to_dict(self) -> dict[str, Any]:
        return {
            "archetype": self.archetype.value,
            "atr_period": self.atr_period,
            "initial_atr_multiple": str(self.initial_atr_multiple),
            "chandelier_atr_multiple": str(self.chandelier_atr_multiple),
            "max_loss_fraction": str(self.max_loss_fraction),
            "structure_lookback": self.structure_lookback,
            "structure_buffer_atr": str(self.structure_buffer_atr),
            "max_holding_seconds": (
                self.max_holding_period.total_seconds()
                if self.max_holding_period is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StrategyRiskProfile":
        seconds = data.get("max_holding_seconds")
        return cls(
            archetype=data["archetype"],
            atr_period=int(data.get("atr_period", 14)),
            initial_atr_multiple=_decimal(data.get("initial_atr_multiple", "2")),
            chandelier_atr_multiple=_decimal(
                data.get("chandelier_atr_multiple", "3")
            ),
            max_loss_fraction=_decimal(data.get("max_loss_fraction", "0.02")),
            structure_lookback=int(data.get("structure_lookback", 10)),
            structure_buffer_atr=_decimal(data.get("structure_buffer_atr", "0.1")),
            max_holding_period=(
                timedelta(seconds=float(seconds)) if seconds is not None else None
            ),
        )


@dataclass(slots=True)
class PositionSlice:
    """One independently protected entry slice."""

    slice_id: str
    strategy_id: str
    symbol: str
    side: PositionSide | str
    entry_price: Decimal
    quantity: Decimal
    opened_at: datetime
    remaining_quantity: Decimal | None = None
    highest_price: Decimal | None = None
    lowest_price: Decimal | None = None
    state: ExitState | str = ExitState.OPEN
    revision: int = 0
    exit_order_id: str | None = None
    exit_reason: str | None = None
    exited_quantity: Decimal = ZERO
    exit_notional: Decimal = ZERO
    target_price: Decimal | None = None
    exit_requested_at: datetime | None = None

    def __post_init__(self) -> None:
        self.side = PositionSide(self.side)
        self.state = ExitState(self.state)
        self.entry_price = _decimal(self.entry_price)
        self.quantity = _decimal(self.quantity)
        self.remaining_quantity = (
            self.quantity
            if self.remaining_quantity is None
            else _decimal(self.remaining_quantity)
        )
        self.highest_price = _decimal(self.highest_price or self.entry_price)
        self.lowest_price = _decimal(self.lowest_price or self.entry_price)
        self.exited_quantity = _decimal(self.exited_quantity)
        self.exit_notional = _decimal(self.exit_notional)
        if self.target_price is not None:
            self.target_price = _decimal(self.target_price)
            if self.target_price <= ZERO:
                raise ValueError("target_price must be positive")
        self.opened_at = _utc(self.opened_at)
        if self.exit_requested_at is not None:
            self.exit_requested_at = _utc(self.exit_requested_at)
        if not self.slice_id or not self.symbol:
            raise ValueError("slice_id and symbol are required")
        if self.entry_price <= ZERO or self.quantity <= ZERO:
            raise ValueError("entry price and quantity must be positive")
        if not ZERO <= self.remaining_quantity <= self.quantity:
            raise ValueError("remaining quantity must be within position quantity")

    @property
    def average_exit_price(self) -> Decimal | None:
        if self.exited_quantity == ZERO:
            return None
        return self.exit_notional / self.exited_quantity

    def observe(self, high: Decimal | int | float, low: Decimal | int | float) -> None:
        high_value, low_value = _decimal(high), _decimal(low)
        if high_value < low_value:
            raise ValueError("high cannot be below low")
        self.highest_price = max(self.highest_price, high_value)
        self.lowest_price = min(self.lowest_price, low_value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slice_id": self.slice_id,
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "entry_price": str(self.entry_price),
            "quantity": str(self.quantity),
            "opened_at": self.opened_at.isoformat(),
            "remaining_quantity": str(self.remaining_quantity),
            "highest_price": str(self.highest_price),
            "lowest_price": str(self.lowest_price),
            "state": self.state.value,
            "revision": self.revision,
            "exit_order_id": self.exit_order_id,
            "exit_reason": self.exit_reason,
            "exited_quantity": str(self.exited_quantity),
            "exit_notional": str(self.exit_notional),
            "target_price": (
                str(self.target_price) if self.target_price is not None else None
            ),
            "exit_requested_at": (
                self.exit_requested_at.isoformat()
                if self.exit_requested_at is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PositionSlice":
        raw = dict(data)
        raw["opened_at"] = datetime.fromisoformat(str(raw["opened_at"]))
        exit_at = raw.get("exit_requested_at")
        if exit_at not in (None, ""):
            raw["exit_requested_at"] = datetime.fromisoformat(str(exit_at))
        else:
            raw["exit_requested_at"] = None
        return cls(**raw)


@dataclass(frozen=True, slots=True)
class Trigger:
    rule_id: str
    kind: RuleKind
    trigger_price: Decimal | None
    execution_price: Decimal
    gap: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ProtectiveRule:
    rule_id: str
    slice_id: str
    kind: RuleKind | str
    side: PositionSide | str
    trigger_price: Decimal | None = None
    expires_at: datetime | None = None
    reason: str = ""
    revision: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", RuleKind(self.kind))
        object.__setattr__(self, "side", PositionSide(self.side))
        if self.trigger_price is not None:
            price = _decimal(self.trigger_price)
            if price <= ZERO:
                raise ValueError("trigger price must be positive")
            object.__setattr__(self, "trigger_price", price)
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", _utc(self.expires_at))
        if self.trigger_price is None and self.expires_at is None:
            raise ValueError("a price trigger or expiry is required")

    def evaluate(
        self,
        *,
        open_price: Decimal | int | float,
        high: Decimal | int | float,
        low: Decimal | int | float,
        now: datetime | None = None,
        close: Decimal | int | float | None = None,
    ) -> Trigger | None:
        """Evaluate a bar; a gap executes at the adverse opening price."""
        opening, bar_high, bar_low = map(_decimal, (open_price, high, low))
        if bar_high < bar_low:
            raise ValueError("high cannot be below low")
        if self.expires_at is not None and now is not None and _utc(now) >= self.expires_at:
            execution = _decimal(close if close is not None else opening)
            return Trigger(
                self.rule_id,
                self.kind,
                self.trigger_price,
                execution,
                False,
                self.reason or "time_stop",
            )
        if self.trigger_price is None:
            return None
        if self.side is PositionSide.LONG and bar_low <= self.trigger_price:
            gap = opening < self.trigger_price
            return Trigger(
                self.rule_id,
                self.kind,
                self.trigger_price,
                opening if gap else self.trigger_price,
                gap,
                self.reason or self.kind.value,
            )
        if self.side is PositionSide.SHORT and bar_high >= self.trigger_price:
            gap = opening > self.trigger_price
            return Trigger(
                self.rule_id,
                self.kind,
                self.trigger_price,
                opening if gap else self.trigger_price,
                gap,
                self.reason or self.kind.value,
            )
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "slice_id": self.slice_id,
            "kind": self.kind.value,
            "side": self.side.value,
            "trigger_price": (
                str(self.trigger_price) if self.trigger_price is not None else None
            ),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "reason": self.reason,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProtectiveRule":
        values = dict(data)
        if values.get("expires_at"):
            values["expires_at"] = datetime.fromisoformat(values["expires_at"])
        return cls(**values)


class PositionSliceLedger:
    """Thread-safe in-memory ledger with deterministic recovery snapshots."""

    def __init__(self, positions: Iterable[PositionSlice] = ()) -> None:
        self._positions = {position.slice_id: position for position in positions}
        self._lock = RLock()

    def add(self, position: PositionSlice) -> None:
        with self._lock:
            if position.slice_id in self._positions:
                raise ValueError(f"duplicate slice_id: {position.slice_id}")
            self._positions[position.slice_id] = position

    def get(self, slice_id: str) -> PositionSlice:
        with self._lock:
            try:
                return self._positions[slice_id]
            except KeyError as exc:
                raise KeyError(f"unknown position slice: {slice_id}") from exc

    def all(self) -> tuple[PositionSlice, ...]:
        with self._lock:
            return tuple(self._positions.values())

    def remove(self, slice_id: str) -> bool:
        with self._lock:
            return self._positions.pop(str(slice_id), None) is not None

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "positions": [
                    item.to_dict()
                    for item in sorted(self._positions.values(), key=lambda p: p.slice_id)
                ]
            }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PositionSliceLedger":
        return cls(PositionSlice.from_dict(item) for item in data.get("positions", ()))


class StopRegistry:
    """Stores active rules and enforces one-way stop ratchets."""

    def __init__(self, rules: Iterable[ProtectiveRule] = ()) -> None:
        self._rules: dict[str, dict[str, ProtectiveRule]] = {}
        for rule in rules:
            self.put(rule)

    def put(self, rule: ProtectiveRule) -> ProtectiveRule:
        bucket = self._rules.setdefault(rule.slice_id, {})
        old = bucket.get(rule.rule_id)
        if old and old.trigger_price is not None and rule.trigger_price is not None:
            if rule.side is PositionSide.LONG and rule.trigger_price < old.trigger_price:
                return old
            if rule.side is PositionSide.SHORT and rule.trigger_price > old.trigger_price:
                return old
        if old and rule.revision <= old.revision:
            rule = replace(rule, revision=old.revision + 1)
        bucket[rule.rule_id] = rule
        return rule

    upsert = put

    def for_slice(self, slice_id: str) -> tuple[ProtectiveRule, ...]:
        return tuple(self._rules.get(slice_id, {}).values())

    def remove_slice(self, slice_id: str) -> None:
        self._rules.pop(slice_id, None)

    def evaluate(
        self, slice_id: str, **bar: Any
    ) -> Trigger | None:
        triggers = [
            trigger
            for rule in self.for_slice(slice_id)
            if (trigger := rule.evaluate(**bar)) is not None
        ]
        if not triggers:
            return None
        # A worse execution price is the conservative result if multiple rules fire.
        side = self.for_slice(slice_id)[0].side
        if side is PositionSide.LONG:
            return min(triggers, key=lambda trigger: trigger.execution_price)
        return max(triggers, key=lambda trigger: trigger.execution_price)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rules": [
                rule.to_dict()
                for slice_id in sorted(self._rules)
                for rule in sorted(
                    self._rules[slice_id].values(), key=lambda item: item.rule_id
                )
            ]
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StopRegistry":
        return cls(ProtectiveRule.from_dict(item) for item in data.get("rules", ()))


class ProtectivePolicyResolver:
    """Builds initial, structure, max-loss, time, and trailing rules."""

    @staticmethod
    def wilder_atr(
        highs: Sequence[Decimal | int | float],
        lows: Sequence[Decimal | int | float],
        closes: Sequence[Decimal | int | float],
        period: int = 14,
    ) -> Decimal:
        if period < 1:
            raise ValueError("period must be positive")
        if not (len(highs) == len(lows) == len(closes)):
            raise ValueError("OHLC series lengths differ")
        if len(closes) < period + 1:
            raise ValueError("Wilder ATR requires at least period + 1 bars")
        h, low, close = (
            tuple(_decimal(value) for value in values)
            for values in (highs, lows, closes)
        )
        true_ranges = [
            max(h[index] - low[index], abs(h[index] - close[index - 1]), abs(low[index] - close[index - 1]))
            for index in range(1, len(close))
        ]
        atr = sum(true_ranges[:period], ZERO) / Decimal(period)
        for true_range in true_ranges[period:]:
            atr = ((atr * Decimal(period - 1)) + true_range) / Decimal(period)
        return atr

    @staticmethod
    def _prices(
        bars: Sequence[Mapping[str, Any] | Sequence[Any]],
    ) -> tuple[list[Decimal], list[Decimal], list[Decimal]]:
        highs: list[Decimal] = []
        lows: list[Decimal] = []
        closes: list[Decimal] = []
        for bar in bars:
            if isinstance(bar, Mapping):
                high, low, close = bar["high"], bar["low"], bar["close"]
            else:
                if len(bar) == 4:
                    _, high, low, close = bar
                elif len(bar) >= 5:
                    _, _, high, low, close = bar[:5]
                else:
                    raise ValueError("bar must be OHLC or timestamp+OHLC")
            highs.append(_decimal(high))
            lows.append(_decimal(low))
            closes.append(_decimal(close))
        return highs, lows, closes

    def resolve(
        self,
        position: PositionSlice,
        profile: StrategyRiskProfile,
        bars: Sequence[Mapping[str, Any] | Sequence[Any]],
    ) -> tuple[ProtectiveRule, ...]:
        highs, lows, closes = self._prices(bars)
        atr = self.wilder_atr(highs, lows, closes, profile.atr_period)
        lookback = min(profile.structure_lookback, len(bars))
        if position.side is PositionSide.LONG:
            atr_stop = position.entry_price - atr * profile.initial_atr_multiple
            max_loss = position.entry_price * (Decimal("1") - profile.max_loss_fraction)
            structure = min(lows[-lookback:]) - atr * profile.structure_buffer_atr
            selected = max(atr_stop, max_loss, structure)
        else:
            atr_stop = position.entry_price + atr * profile.initial_atr_multiple
            max_loss = position.entry_price * (Decimal("1") + profile.max_loss_fraction)
            structure = max(highs[-lookback:]) + atr * profile.structure_buffer_atr
            selected = min(atr_stop, max_loss, structure)
        rules = [
            ProtectiveRule(
                f"{position.slice_id}:stop",
                position.slice_id,
                RuleKind.INITIAL_STOP,
                position.side,
                selected,
                reason=f"{profile.archetype.value}_initial_stop",
            )
        ]
        if profile.max_holding_period is not None:
            rules.append(
                ProtectiveRule(
                    f"{position.slice_id}:time",
                    position.slice_id,
                    RuleKind.TIME,
                    position.side,
                    expires_at=position.opened_at + profile.max_holding_period,
                    reason=f"{profile.archetype.value}_time_stop",
                )
            )
        return tuple(rules)

    def chandelier(
        self,
        position: PositionSlice,
        profile: StrategyRiskProfile,
        atr: Decimal | int | float,
        *,
        previous: ProtectiveRule | None = None,
    ) -> ProtectiveRule:
        distance = _decimal(atr) * profile.chandelier_atr_multiple
        candidate = (
            position.highest_price - distance
            if position.side is PositionSide.LONG
            else position.lowest_price + distance
        )
        if previous is not None and previous.trigger_price is not None:
            candidate = (
                max(candidate, previous.trigger_price)
                if position.side is PositionSide.LONG
                else min(candidate, previous.trigger_price)
            )
        return ProtectiveRule(
            f"{position.slice_id}:chandelier",
            position.slice_id,
            RuleKind.CHANDELIER,
            position.side,
            candidate,
            reason=f"{profile.archetype.value}_chandelier",
            revision=(previous.revision + 1 if previous else 0),
        )


@dataclass(frozen=True, slots=True)
class ExitInstruction:
    slice_id: str
    order_id: str
    side: str
    quantity: Decimal
    reason: str
    revision: int


class ExitArbiter:
    """Atomic, idempotent owner of exit lifecycle transitions."""

    def __init__(self, ledger: PositionSliceLedger) -> None:
        self.ledger = ledger
        self._lock = RLock()

    def request_exit(
        self,
        slice_id: str,
        *,
        order_id: str,
        reason: str,
        expected_revision: int | None = None,
    ) -> ExitInstruction | None:
        with self._lock:
            position = self.ledger.get(slice_id)
            if expected_revision is not None and position.revision != expected_revision:
                raise ValueError("stale position revision")
            if position.state is ExitState.CLOSED:
                return None
            if position.state is ExitState.EXIT_PENDING:
                if position.exit_order_id == order_id:
                    return ExitInstruction(
                        slice_id,
                        order_id,
                        "sell" if position.side is PositionSide.LONG else "buy",
                        position.remaining_quantity,
                        position.exit_reason or reason,
                        position.revision,
                    )
                return None
            position.state = ExitState.EXIT_PENDING
            position.exit_order_id = order_id
            position.exit_reason = reason
            position.exit_requested_at = datetime.now(UTC)
            position.revision += 1
            return ExitInstruction(
                slice_id,
                order_id,
                "sell" if position.side is PositionSide.LONG else "buy",
                position.remaining_quantity,
                reason,
                position.revision,
            )

    def apply_fill(
        self,
        slice_id: str,
        *,
        order_id: str,
        fill_quantity: Decimal | int | float | str,
        fill_price: Decimal | int | float | str,
        expected_revision: int | None = None,
    ) -> PositionSlice:
        with self._lock:
            position = self.ledger.get(slice_id)
            if position.state is not ExitState.EXIT_PENDING:
                raise ValueError("position has no pending exit")
            if position.exit_order_id != order_id:
                raise ValueError("fill belongs to a different exit order")
            if expected_revision is not None and position.revision != expected_revision:
                raise ValueError("stale position revision")
            quantity, price = _decimal(fill_quantity), _decimal(fill_price)
            if quantity <= ZERO or price <= ZERO:
                raise ValueError("fill quantity and price must be positive")
            if quantity > position.remaining_quantity:
                raise ValueError("fill exceeds remaining position")
            position.remaining_quantity -= quantity
            position.exited_quantity += quantity
            position.exit_notional += quantity * price
            position.revision += 1
            if position.remaining_quantity == ZERO:
                position.state = ExitState.CLOSED
            return position

    record_fill = apply_fill
