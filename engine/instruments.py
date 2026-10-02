"""Canonical, broker-independent instrument specifications.

The objects in this module deliberately contain no adapter or runtime state.
They are suitable for sharing between market-data, risk, and execution layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import (
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_UP,
    ROUND_UP,
    Decimal,
    InvalidOperation,
)
from types import MappingProxyType
from typing import Any, Iterable, Mapping

DecimalLike = Decimal | int | float | str

_ROUNDING = {
    "down": ROUND_DOWN,
    "floor": ROUND_FLOOR,
    "up": ROUND_UP,
    "ceiling": ROUND_CEILING,
    "nearest": ROUND_HALF_UP,
    "half_up": ROUND_HALF_UP,
}


class InstrumentError(ValueError):
    """Base exception for invalid instrument data or lookup."""


#: Cross margin shares collateral across positions; isolated does not. `unknown`
#: is a real answer rather than a placeholder -- the venue decides how an account
#: is configured, and a public instrument spec cannot tell us.
_MARGIN_MODES = {"isolated", "cross", "unknown"}


class InstrumentNotFoundError(InstrumentError, LookupError):
    """Raised when an instrument cannot be resolved."""


class AmbiguousInstrumentError(InstrumentError, LookupError):
    """Raised when a partial identity matches more than one instrument."""


def to_decimal(value: DecimalLike, *, name: str = "value") -> Decimal:
    """Convert a numeric value without introducing binary-float artefacts."""
    if isinstance(value, bool):
        raise InstrumentError(f"{name} must be a number, not bool")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InstrumentError(f"{name} must be a valid decimal number") from exc
    if not result.is_finite():
        raise InstrumentError(f"{name} must be finite")
    return result


def _positive_decimal(value: DecimalLike, name: str) -> Decimal:
    result = to_decimal(value, name=name)
    if result <= 0:
        raise InstrumentError(f"{name} must be greater than zero")
    return result


def _required_text(value: Any, name: str, *, upper: bool = False) -> str:
    if not isinstance(value, str):
        raise InstrumentError(f"{name} must be a string")
    result = value.strip()
    if not result:
        raise InstrumentError(f"{name} must not be empty")
    return result.upper() if upper else result


def _optional_text(value: Any, name: str, *, upper: bool = False) -> str | None:
    if value is None:
        return None
    return _required_text(value, name, upper=upper)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


def _rounding_mode(rounding: str) -> str:
    try:
        return _ROUNDING[rounding.lower()]
    except (AttributeError, KeyError) as exc:
        choices = ", ".join(sorted(_ROUNDING))
        raise InstrumentError(f"rounding must be one of: {choices}") from exc


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    """Strict immutable economic and routing definition of an instrument."""

    venue: str
    asset_class: str
    symbol: str
    currency: str
    lot_size: DecimalLike = Decimal("1")
    tick_size: DecimalLike = Decimal("0.01")
    tick_value: DecimalLike | None = None
    contract_multiplier: DecimalLike = Decimal("1")
    class_code: str | None = None
    margin_model: str = "cash"
    #: `isolated` | `cross` | `unknown`. Only meaningful for `derivative`.
    #: `unknown` is the default because an adapter reading a public instrument
    #: spec cannot know how the account is configured -- and defaulting to
    #: `isolated` would quietly claim a guarantee nobody verified.
    margin_mode: str | None = None
    calendar: str | None = None
    synthetic: bool = False
    source: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        venue = _required_text(self.venue, "venue", upper=True)
        asset_class = _required_text(self.asset_class, "asset_class").lower()
        if not asset_class.replace("_", "").replace("-", "").isalnum():
            raise InstrumentError("asset_class must be an alphanumeric slug")
        symbol = _required_text(self.symbol, "symbol", upper=True)
        currency = _required_text(self.currency, "currency", upper=True)
        class_code = _optional_text(self.class_code, "class_code", upper=True)
        margin_model = _required_text(self.margin_model, "margin_model").lower()
        # Cross vs isolated was not represented at all, so a perpetual account
        # running cross margin was indistinguishable from one running isolated.
        # That distinction decides whether a stop bounds one position or the
        # whole account: in cross, margin is shared, so simultaneous losses
        # elsewhere consume the collateral this position's stop was counting on.
        # Adapters that cannot know the account setting must say so rather than
        # assert a guarantee; `unknown` is the honest default.
        margin_mode = _optional_text(self.margin_mode, "margin_mode")
        margin_mode = (margin_mode or "unknown").lower()
        if margin_mode not in _MARGIN_MODES:
            raise InstrumentError(
                f"margin_mode must be one of {sorted(_MARGIN_MODES)}: {margin_mode}"
            )
        if margin_model != "derivative" and margin_mode not in {"isolated", "unknown"}:
            raise InstrumentError(
                f"margin_mode={margin_mode} is meaningless for "
                f"margin_model={margin_model}"
            )
        calendar = _optional_text(self.calendar, "calendar")
        source = _optional_text(self.source, "source")
        if not isinstance(self.synthetic, bool):
            raise InstrumentError("synthetic must be bool")
        if not isinstance(self.metadata, Mapping):
            raise InstrumentError("metadata must be a mapping")

        lot_size = _positive_decimal(self.lot_size, "lot_size")
        tick_size = _positive_decimal(self.tick_size, "tick_size")
        multiplier = _positive_decimal(self.contract_multiplier, "contract_multiplier")
        tick_value = (
            tick_size * multiplier
            if self.tick_value is None
            else _positive_decimal(self.tick_value, "tick_value")
        )

        object.__setattr__(self, "venue", venue)
        object.__setattr__(self, "asset_class", asset_class)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "class_code", class_code)
        object.__setattr__(self, "margin_model", margin_model)
        object.__setattr__(self, "margin_mode", margin_mode)
        object.__setattr__(self, "calendar", calendar)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "lot_size", lot_size)
        object.__setattr__(self, "tick_size", tick_size)
        object.__setattr__(self, "contract_multiplier", multiplier)
        object.__setattr__(self, "tick_value", tick_value)
        object.__setattr__(self, "metadata", _freeze(self.metadata))

    @property
    def key(self) -> str:
        """Stable venue-qualified identity."""
        parts = (self.venue, self.class_code, self.symbol)
        return ":".join(part for part in parts if part)

    @property
    def instrument_id(self) -> str:
        return self.key

    def normalize_quantity(
        self, quantity: DecimalLike, *, rounding: str = "down"
    ) -> Decimal:
        """Round quantity to a valid lot multiple (toward zero by default)."""
        value = to_decimal(quantity, name="quantity")
        lots = (value / self.lot_size).to_integral_value(
            rounding=_rounding_mode(rounding)
        )
        return lots * self.lot_size

    def round_price(self, price: DecimalLike, *, rounding: str = "nearest") -> Decimal:
        """Round a price to the instrument tick grid."""
        value = to_decimal(price, name="price")
        ticks = (value / self.tick_size).to_integral_value(
            rounding=_rounding_mode(rounding)
        )
        return ticks * self.tick_size

    def round_to_tick(
        self, price: DecimalLike, *, rounding: str = "nearest"
    ) -> Decimal:
        return self.round_price(price, rounding=rounding)

    def notional(self, quantity: DecimalLike, price: DecimalLike) -> Decimal:
        """Return signed economic notional in the settlement currency."""
        return (
            to_decimal(quantity, name="quantity")
            * to_decimal(price, name="price")
            * self.contract_multiplier
        )

    def calculate_notional(self, price: DecimalLike, quantity: DecimalLike) -> Decimal:
        """Price-first spelling for callers that use market-data conventions."""
        return self.notional(quantity, price)

    def value_for_ticks(
        self, ticks: DecimalLike, quantity: DecimalLike = Decimal("1")
    ) -> Decimal:
        """Return P/L value for a tick move and contract quantity."""
        return (
            to_decimal(ticks, name="ticks")
            * to_decimal(quantity, name="quantity")
            * self.tick_value
        )

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> InstrumentSpec:
        if not isinstance(values, Mapping):
            raise InstrumentError("instrument values must be a mapping")
        return cls(**dict(values))


class InstrumentRegistry:
    """Registry and deterministic resolver for canonical instrument specs."""

    def __init__(self, instruments: Iterable[InstrumentSpec] = ()) -> None:
        self._by_key: dict[str, InstrumentSpec] = {}
        self._aliases: dict[str, str] = {}
        for instrument in instruments:
            self.register(instrument)

    def __len__(self) -> int:
        return len(self._by_key)

    def __iter__(self):
        return iter(self._by_key.values())

    def register(
        self, instrument: InstrumentSpec, *, aliases: Iterable[str] = ()
    ) -> InstrumentSpec:
        if not isinstance(instrument, InstrumentSpec):
            raise TypeError("instrument must be an InstrumentSpec")
        key = self._token(instrument.key)
        if key in self._by_key:
            raise InstrumentError(f"instrument already registered: {instrument.key}")
        self._by_key[key] = instrument
        try:
            for alias in aliases:
                token = self._token(alias)
                if token in self._aliases or token in self._by_key:
                    raise InstrumentError(f"instrument alias already registered: {alias}")
                self._aliases[token] = key
        except Exception:
            self._by_key.pop(key, None)
            for alias, target in tuple(self._aliases.items()):
                if target == key:
                    self._aliases.pop(alias)
            raise
        return instrument

    def get(self, key: str) -> InstrumentSpec | None:
        token = self._token(key)
        token = self._aliases.get(token, token)
        return self._by_key.get(token)

    def resolve(
        self,
        symbol_or_key: str,
        *,
        venue: str | None = None,
        class_code: str | None = None,
        asset_class: str | None = None,
    ) -> InstrumentSpec:
        token = self._token(symbol_or_key)
        direct = self.get(token)
        if direct is not None:
            return direct

        symbol = token
        candidates = [
            item
            for item in self._by_key.values()
            if item.symbol == symbol
            and (venue is None or item.venue == self._token(venue))
            and (
                class_code is None
                or (item.class_code or "") == self._token(class_code)
            )
            and (
                asset_class is None
                or item.asset_class == asset_class.strip().lower()
            )
        ]
        if not candidates:
            raise InstrumentNotFoundError(f"instrument not found: {symbol_or_key}")
        if len(candidates) > 1:
            keys = ", ".join(sorted(item.key for item in candidates))
            raise AmbiguousInstrumentError(
                f"ambiguous instrument {symbol_or_key!r}; matches: {keys}"
            )
        return candidates[0]

    def require(self, symbol_or_key: str, **filters: str | None) -> InstrumentSpec:
        return self.resolve(symbol_or_key, **filters)

    @staticmethod
    def _token(value: str) -> str:
        return _required_text(value, "instrument identity", upper=True)


class InstrumentResolver:
    """Read-only resolver facade over an :class:`InstrumentRegistry`."""

    def __init__(self, registry: InstrumentRegistry) -> None:
        if not isinstance(registry, InstrumentRegistry):
            raise TypeError("registry must be an InstrumentRegistry")
        self.registry = registry

    def resolve(
        self,
        symbol_or_key: str,
        *,
        venue: str | None = None,
        class_code: str | None = None,
        asset_class: str | None = None,
    ) -> InstrumentSpec:
        return self.registry.resolve(
            symbol_or_key,
            venue=venue,
            class_code=class_code,
            asset_class=asset_class,
        )


Resolver = InstrumentResolver

__all__ = [
    "AmbiguousInstrumentError",
    "DecimalLike",
    "InstrumentError",
    "InstrumentNotFoundError",
    "InstrumentRegistry",
    "InstrumentResolver",
    "InstrumentSpec",
    "Resolver",
    "to_decimal",
]
