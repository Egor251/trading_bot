"""Deterministic, Decimal-only trading cost schedules.

The module deliberately has no dependency on the order/runtime layers.  It can
therefore be used by backtests, paper trading, and live adapters without
creating different fee arithmetic in each path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from fnmatch import fnmatchcase
import hashlib
import json
from typing import Any, Mapping, Protocol, runtime_checkable


ZERO = Decimal("0")
TEN_THOUSAND = Decimal("10000")


def _decimal(value: object, name: str, *, positive: bool = False) -> Decimal:
    """Convert an exact input to Decimal, rejecting binary floats."""
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"{name} must be Decimal, int, or a decimal string")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, (int, str)):
        try:
            result = Decimal(value)
        except Exception as exc:
            raise ValueError(f"{name} is not a valid decimal") from exc
    else:
        raise TypeError(f"{name} must be Decimal, int, or a decimal string")
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    if positive and result <= ZERO:
        raise ValueError(f"{name} must be greater than zero")
    return result


def _optional_decimal(value: object | None, name: str) -> Decimal | None:
    return None if value is None else _decimal(value, name)


def _datetime(value: datetime | str | None, name: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{name} must be an ISO-8601 datetime") from exc
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime or ISO-8601 string")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_text(value: object | None, name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, name)


def _decimal_text(value: Decimal) -> str:
    if value == ZERO:
        return "0"
    # Equivalent Decimals (1, 1.0, 1.00) must have identical wire forms and
    # therefore identical schedule hashes.
    return format(value.normalize(), "f")


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@runtime_checkable
class InstrumentLike(Protocol):
    """Minimal structural contract accepted by :meth:`TradeContext.for_instrument`."""

    symbol: str
    asset_class: str
    contract_multiplier: Decimal


class CostKind(str, Enum):
    BPS = "bps"
    FIXED = "fixed"
    PER_UNIT = "per_unit"
    PER_CONTRACT = "per_contract"
    SPREAD = "spread"
    SLIPPAGE = "slippage"
    FUNDING = "funding"
    BORROW = "borrow"
    TAX = "tax"


_NOTIONAL_KINDS = {
    CostKind.BPS,
    CostKind.SPREAD,
    CostKind.SLIPPAGE,
    CostKind.FUNDING,
    CostKind.BORROW,
    CostKind.TAX,
}


@dataclass(frozen=True, slots=True)
class TradeContext:
    venue: str
    account: str
    asset: str
    asset_class: str
    symbol: str
    side: str
    position_effect: str
    order_type: str
    liquidity: str
    trade_time: datetime
    quantity: Decimal
    price: Decimal
    contract_multiplier: Decimal = Decimal("1")
    tier_value: Decimal = ZERO
    currency: str = "USD"

    def __post_init__(self) -> None:
        for name in (
            "venue",
            "account",
            "asset",
            "asset_class",
            "symbol",
            "side",
            "position_effect",
            "order_type",
            "liquidity",
            "currency",
        ):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(self, "side", self.side.lower())
        if self.side not in {"buy", "sell"}:
            raise ValueError("side must be 'buy' or 'sell'")
        object.__setattr__(self, "trade_time", _datetime(self.trade_time, "trade_time"))
        object.__setattr__(self, "quantity", _decimal(self.quantity, "quantity", positive=True))
        object.__setattr__(self, "price", _decimal(self.price, "price", positive=True))
        object.__setattr__(
            self,
            "contract_multiplier",
            _decimal(self.contract_multiplier, "contract_multiplier", positive=True),
        )
        tier = _decimal(self.tier_value, "tier_value")
        if tier < ZERO:
            raise ValueError("tier_value cannot be negative")
        object.__setattr__(self, "tier_value", tier)

    @property
    def gross_notional(self) -> Decimal:
        return self.quantity * self.price * self.contract_multiplier

    @classmethod
    def for_instrument(
        cls,
        instrument: InstrumentLike,
        *,
        quantity: Decimal,
        price: Decimal,
        **values: Any,
    ) -> TradeContext:
        return cls(
            symbol=instrument.symbol,
            asset_class=instrument.asset_class,
            contract_multiplier=instrument.contract_multiplier,
            quantity=quantity,
            price=price,
            **values,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "venue": self.venue,
            "account": self.account,
            "asset": self.asset,
            "asset_class": self.asset_class,
            "symbol": self.symbol,
            "side": self.side,
            "position_effect": self.position_effect,
            "order_type": self.order_type,
            "liquidity": self.liquidity,
            "trade_time": _iso(self.trade_time) or "",
            "quantity": _decimal_text(self.quantity),
            "price": _decimal_text(self.price),
            "contract_multiplier": _decimal_text(self.contract_multiplier),
            "tier_value": _decimal_text(self.tier_value),
            "currency": self.currency,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TradeContext:
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class CostMatch:
    venue: str | None = None
    account: str | None = None
    asset: str | None = None
    asset_class: str | None = None
    symbol: str = "*"
    side: str | None = None
    position_effect: str | None = None
    order_type: str | None = None
    liquidity: str | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    tier_min: Decimal | None = None
    tier_max: Decimal | None = None

    def __post_init__(self) -> None:
        for name in (
            "venue",
            "account",
            "asset",
            "asset_class",
            "side",
            "position_effect",
            "order_type",
            "liquidity",
        ):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))
        object.__setattr__(self, "symbol", _required_text(self.symbol, "symbol"))
        start = _datetime(self.effective_from, "effective_from")
        end = _datetime(self.effective_to, "effective_to")
        if start is not None and end is not None and end <= start:
            raise ValueError("effective_to must be after effective_from")
        object.__setattr__(self, "effective_from", start)
        object.__setattr__(self, "effective_to", end)
        minimum = _optional_decimal(self.tier_min, "tier_min")
        maximum = _optional_decimal(self.tier_max, "tier_max")
        if minimum is not None and minimum < ZERO:
            raise ValueError("tier_min cannot be negative")
        if maximum is not None and maximum <= ZERO:
            raise ValueError("tier_max must be greater than zero")
        if minimum is not None and maximum is not None and maximum <= minimum:
            raise ValueError("tier_max must be greater than tier_min")
        object.__setattr__(self, "tier_min", minimum)
        object.__setattr__(self, "tier_max", maximum)

    def matches(self, context: TradeContext) -> bool:
        for name in (
            "venue",
            "account",
            "asset",
            "asset_class",
            "side",
            "position_effect",
            "order_type",
            "liquidity",
        ):
            expected = getattr(self, name)
            if expected is not None and expected.casefold() != getattr(context, name).casefold():
                return False
        if not fnmatchcase(context.symbol.casefold(), self.symbol.casefold()):
            return False
        # Date intervals and tiers are half-open. Adjacent rules cannot both match.
        if self.effective_from is not None and context.trade_time < self.effective_from:
            return False
        if self.effective_to is not None and context.trade_time >= self.effective_to:
            return False
        if self.tier_min is not None and context.tier_value < self.tier_min:
            return False
        if self.tier_max is not None and context.tier_value >= self.tier_max:
            return False
        return True

    def to_dict(self) -> dict[str, str | None]:
        return {
            "venue": self.venue,
            "account": self.account,
            "asset": self.asset,
            "asset_class": self.asset_class,
            "symbol": self.symbol,
            "side": self.side,
            "position_effect": self.position_effect,
            "order_type": self.order_type,
            "liquidity": self.liquidity,
            "effective_from": _iso(self.effective_from),
            "effective_to": _iso(self.effective_to),
            "tier_min": None if self.tier_min is None else _decimal_text(self.tier_min),
            "tier_max": None if self.tier_max is None else _decimal_text(self.tier_max),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CostMatch:
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class CostComponent:
    kind: CostKind | str
    rate: Decimal
    name: str = ""
    minimum: Decimal | None = None
    maximum: Decimal | None = None

    def __post_init__(self) -> None:
        try:
            kind = self.kind if isinstance(self.kind, CostKind) else CostKind(self.kind)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unsupported cost kind: {self.kind!r}") from exc
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "name", self.name.strip() or kind.value)
        rate = _decimal(self.rate, "rate")
        if rate < ZERO:
            raise ValueError("rate cannot be negative")
        object.__setattr__(self, "rate", rate)
        minimum = _optional_decimal(self.minimum, "minimum")
        maximum = _optional_decimal(self.maximum, "maximum")
        if minimum is not None and minimum < ZERO:
            raise ValueError("minimum cannot be negative")
        if maximum is not None and maximum < ZERO:
            raise ValueError("maximum cannot be negative")
        if minimum is not None and maximum is not None and maximum < minimum:
            raise ValueError("maximum cannot be less than minimum")
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)

    def amount_for(self, context: TradeContext) -> Decimal:
        if self.kind in _NOTIONAL_KINDS:
            amount = context.gross_notional * self.rate / TEN_THOUSAND
        elif self.kind is CostKind.FIXED:
            amount = self.rate
        elif self.kind in {CostKind.PER_UNIT, CostKind.PER_CONTRACT}:
            amount = context.quantity * self.rate
        else:  # Exhaustive guard for future enum members.
            raise ValueError(f"unsupported cost kind: {self.kind}")
        if self.minimum is not None:
            amount = max(amount, self.minimum)
        if self.maximum is not None:
            amount = min(amount, self.maximum)
        return amount

    def to_dict(self) -> dict[str, str | None]:
        return {
            "kind": self.kind.value,
            "rate": _decimal_text(self.rate),
            "name": self.name,
            "minimum": None if self.minimum is None else _decimal_text(self.minimum),
            "maximum": None if self.maximum is None else _decimal_text(self.maximum),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CostComponent:
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class CostRule:
    rule_id: str
    match: CostMatch
    components: tuple[CostComponent, ...]
    priority: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_id", _required_text(self.rule_id, "rule_id"))
        if not isinstance(self.match, CostMatch):
            raise TypeError("match must be a CostMatch")
        components = tuple(self.components)
        if not components or not all(isinstance(item, CostComponent) for item in components):
            raise ValueError("components must contain at least one CostComponent")
        object.__setattr__(self, "components", components)
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise TypeError("priority must be an integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "priority": self.priority,
            "match": self.match.to_dict(),
            "components": [item.to_dict() for item in self.components],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CostRule:
        data = dict(value)
        data["match"] = CostMatch.from_dict(data["match"])
        data["components"] = tuple(CostComponent.from_dict(item) for item in data["components"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class CostLine:
    rule_id: str
    component: str
    kind: CostKind
    amount: Decimal
    priority: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_id", _required_text(self.rule_id, "rule_id"))
        object.__setattr__(self, "component", _required_text(self.component, "component"))
        if not isinstance(self.kind, CostKind):
            object.__setattr__(self, "kind", CostKind(self.kind))
        amount = _decimal(self.amount, "amount")
        if amount < ZERO:
            raise ValueError("amount cannot be negative")
        object.__setattr__(self, "amount", amount)
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise TypeError("priority must be an integer")

    def to_dict(self) -> dict[str, str | int]:
        return {
            "rule_id": self.rule_id,
            "component": self.component,
            "kind": self.kind.value,
            "amount": _decimal_text(self.amount),
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CostLine:
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    lines: tuple[CostLine, ...] = field(default_factory=tuple)
    schedule_version: str = ""
    schedule_hash: str = ""

    def __post_init__(self) -> None:
        lines = tuple(self.lines)
        if not all(isinstance(item, CostLine) for item in lines):
            raise TypeError("lines must contain only CostLine values")
        object.__setattr__(self, "lines", lines)
        if not isinstance(self.schedule_version, str) or not isinstance(self.schedule_hash, str):
            raise TypeError("schedule_version and schedule_hash must be strings")
        if bool(self.schedule_version) != bool(self.schedule_hash):
            raise ValueError("schedule_version and schedule_hash must both be set or both be empty")
        if self.schedule_hash:
            is_sha256 = len(self.schedule_hash) == 64 and all(
                character in "0123456789abcdef" for character in self.schedule_hash
            )
            if not is_sha256:
                raise ValueError("schedule_hash must be a lowercase SHA-256 hex digest")

    @property
    def total(self) -> Decimal:
        return sum((line.amount for line in self.lines), ZERO)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lines": [line.to_dict() for line in self.lines],
            "total": _decimal_text(self.total),
            "schedule_version": self.schedule_version,
            "schedule_hash": self.schedule_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CostBreakdown:
        data = dict(value)
        reported_total = data.pop("total", None)
        data["lines"] = tuple(CostLine.from_dict(item) for item in data.get("lines", ()))
        result = cls(**data)
        if reported_total is not None and _decimal(reported_total, "total") != result.total:
            raise ValueError("serialized total does not equal the sum of cost lines")
        return result


@dataclass(frozen=True, slots=True)
class CostSchedule:
    version: str
    rules: tuple[CostRule, ...]
    effective_from: datetime | None = None
    effective_to: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "version", _required_text(self.version, "version"))
        rules = tuple(self.rules)
        if not all(isinstance(item, CostRule) for item in rules):
            raise TypeError("rules must contain only CostRule values")
        ids = [item.rule_id for item in rules]
        if len(ids) != len(set(ids)):
            raise ValueError("rule_id values must be unique")
        object.__setattr__(self, "rules", rules)
        start = _datetime(self.effective_from, "effective_from")
        end = _datetime(self.effective_to, "effective_to")
        if start is not None and end is not None and end <= start:
            raise ValueError("effective_to must be after effective_from")
        object.__setattr__(self, "effective_from", start)
        object.__setattr__(self, "effective_to", end)

    def _active(self, at: datetime) -> bool:
        if self.effective_from is not None and at < self.effective_from:
            return False
        return self.effective_to is None or at < self.effective_to

    def calculate(self, context: TradeContext) -> CostBreakdown:
        if not isinstance(context, TradeContext):
            raise TypeError("context must be a TradeContext")
        if not self._active(context.trade_time):
            raise ValueError("cost schedule is not effective at trade_time")
        lines: list[CostLine] = []
        # Additive rules: no winner-takes-all behavior.  Sorting makes equivalent
        # schedules deterministic regardless of construction order.
        for rule in sorted(self.rules, key=lambda item: (item.priority, item.rule_id)):
            if rule.match.matches(context):
                for component in rule.components:
                    lines.append(
                        CostLine(
                            rule_id=rule.rule_id,
                            component=component.name,
                            kind=component.kind,
                            amount=component.amount_for(context),
                            priority=rule.priority,
                        )
                    )
        return CostBreakdown(
            lines=tuple(lines),
            schedule_version=self.version,
            schedule_hash=self.schedule_hash,
        )

    evaluate = calculate

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "effective_from": _iso(self.effective_from),
            "effective_to": _iso(self.effective_to),
            "rules": [
                rule.to_dict()
                for rule in sorted(self.rules, key=lambda item: (item.priority, item.rule_id))
            ],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @property
    def schedule_hash(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    @property
    def hash(self) -> str:
        return self.schedule_hash

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CostSchedule:
        data = dict(value)
        data["rules"] = tuple(CostRule.from_dict(item) for item in data.get("rules", ()))
        return cls(**data)

    @classmethod
    def from_json(cls, value: str) -> CostSchedule:
        try:
            data = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid cost schedule JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("cost schedule JSON must contain an object")
        return cls.from_dict(data)


__all__ = [
    "CostBreakdown",
    "CostComponent",
    "CostKind",
    "CostLine",
    "CostMatch",
    "CostRule",
    "CostSchedule",
    "InstrumentLike",
    "TradeContext",
]
