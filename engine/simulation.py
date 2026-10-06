"""Deterministic execution simulation and experiment validation helpers.

The module is deliberately independent of the live runtime.  A simulator is
driven only by submitted orders and timestamped market events, making a run
reproducible and straightforward to audit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
import hashlib
import json
import math
import random
from statistics import mean
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from .instruments import InstrumentSpec, to_decimal


ZERO = Decimal("0")
ONE = Decimal("1")
BPS = Decimal("10000")


def _decimal(value: Any, name: str) -> Decimal:
    result = to_decimal(value, name=name)
    return result


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> Decimal:
        return ONE if self is Side.BUY else Decimal("-1")


class SimOrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class SimOrderStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class MarketEvent:
    """A quote, trade/bar, or combined market event.

    OHLC fields are optional so quote-only events remain inexpensive.  For bar
    events, ``volume`` is the total executable volume before participation is
    applied.  Explicit bid/ask always takes precedence over synthetic spread.
    """

    timestamp: datetime
    bid: Decimal | int | float | str | None = None
    ask: Decimal | int | float | str | None = None
    open: Decimal | int | float | str | None = None
    high: Decimal | int | float | str | None = None
    low: Decimal | int | float | str | None = None
    close: Decimal | int | float | str | None = None
    volume: Decimal | int | float | str = ZERO

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _utc(self.timestamp))
        for name in ("bid", "ask", "open", "high", "low", "close"):
            value = getattr(self, name)
            if value is not None:
                converted = _decimal(value, name)
                if converted <= ZERO:
                    raise ValueError(f"{name} must be positive")
                object.__setattr__(self, name, converted)
        volume = _decimal(self.volume, "volume")
        if volume < ZERO:
            raise ValueError("volume must be non-negative")
        object.__setattr__(self, "volume", volume)
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ValueError("bid must not exceed ask")
        if self.high is not None and self.low is not None and self.high < self.low:
            raise ValueError("high must not be below low")

    @classmethod
    def bar(
        cls,
        timestamp: datetime,
        open: Any,
        high: Any,
        low: Any,
        close: Any,
        volume: Any,
    ) -> "MarketEvent":
        return cls(timestamp, open=open, high=high, low=low, close=close, volume=volume)


@dataclass(slots=True)
class SimOrder:
    order_id: str
    side: Side | str
    quantity: Decimal | int | float | str
    submitted_at: datetime
    order_type: SimOrderType | str = SimOrderType.MARKET
    limit_price: Decimal | int | float | str | None = None
    stop_price: Decimal | int | float | str | None = None
    latency: timedelta | int | float = timedelta(0)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    status: SimOrderStatus = field(default=SimOrderStatus.PENDING, init=False)
    filled_quantity: Decimal = field(default=ZERO, init=False)
    triggered: bool = field(default=False, init=False)
    sequence: int = field(default=-1, init=False)

    def __post_init__(self) -> None:
        self.side = Side(self.side)
        self.order_type = SimOrderType(self.order_type)
        self.submitted_at = _utc(self.submitted_at)
        self.quantity = _decimal(self.quantity, "quantity")
        if self.quantity <= ZERO:
            raise ValueError("quantity must be positive")
        if isinstance(self.latency, timedelta):
            latency = self.latency
        else:
            latency = timedelta(milliseconds=float(self.latency))
        if latency < timedelta(0):
            raise ValueError("latency must be non-negative")
        self.latency = latency
        if self.limit_price is not None:
            self.limit_price = _decimal(self.limit_price, "limit_price")
        if self.stop_price is not None:
            self.stop_price = _decimal(self.stop_price, "stop_price")
        if self.order_type in {SimOrderType.LIMIT, SimOrderType.STOP_LIMIT} and self.limit_price is None:
            raise ValueError("limit_price is required")
        if self.order_type in {SimOrderType.STOP, SimOrderType.STOP_LIMIT} and self.stop_price is None:
            raise ValueError("stop_price is required")

    @property
    def active_at(self) -> datetime:
        return self.submitted_at + self.latency

    @property
    def remaining_quantity(self) -> Decimal:
        return self.quantity - self.filled_quantity


class CostCalculator(Protocol):
    """Injected transaction-cost port."""

    def calculate(
        self,
        instrument: InstrumentSpec,
        side: Side,
        quantity: Decimal,
        price: Decimal,
    ) -> Decimal: ...


class ZeroCostCalculator:
    def calculate(
        self,
        instrument: InstrumentSpec,
        side: Side,
        quantity: Decimal,
        price: Decimal,
    ) -> Decimal:
        return ZERO


@dataclass(frozen=True, slots=True)
class Fill:
    order_id: str
    timestamp: datetime
    side: Side
    quantity: Decimal
    price: Decimal
    notional: Decimal
    costs: Decimal
    cash_delta: Decimal
    remaining_quantity: Decimal
    status: SimOrderStatus

    @property
    def fee(self) -> Decimal:
        return self.costs


class FillSimulator:
    """Event-driven deterministic fill model.

    Orders compete for each event's volume in submission order.  Triggering
    uses bid/ask for quotes and full high/low ranges for bars.  Stops that gap
    through their trigger execute at the adverse opening price.
    """

    def __init__(
        self,
        instrument: InstrumentSpec,
        cost_calculator: CostCalculator | Callable[..., Decimal] | None = None,
        *,
        participation_rate: Decimal | int | float | str = "1",
        market_impact_bps: Decimal | int | float | str = ZERO,
        synthetic_spread_bps: Decimal | int | float | str = ZERO,
        default_event_liquidity: Decimal | int | float | str = "1E+50",
    ) -> None:
        self.instrument = instrument
        self.cost_calculator = cost_calculator or ZeroCostCalculator()
        self.participation_rate = _decimal(participation_rate, "participation_rate")
        self.market_impact_bps = _decimal(market_impact_bps, "market_impact_bps")
        self.synthetic_spread_bps = _decimal(synthetic_spread_bps, "synthetic_spread_bps")
        self.default_event_liquidity = _decimal(default_event_liquidity, "default_event_liquidity")
        if not ZERO < self.participation_rate <= ONE:
            raise ValueError("participation_rate must be in (0, 1]")
        if self.market_impact_bps < ZERO or self.synthetic_spread_bps < ZERO:
            raise ValueError("impact and spread must be non-negative")
        if self.default_event_liquidity <= ZERO:
            raise ValueError("default_event_liquidity must be positive")
        self._orders: list[SimOrder] = []
        self._sequence = 0
        self._last_timestamp: datetime | None = None
        self.fills: list[Fill] = []
        self.cash = ZERO

    @property
    def open_orders(self) -> tuple[SimOrder, ...]:
        return tuple(
            order
            for order in self._orders
            if order.status not in {SimOrderStatus.FILLED, SimOrderStatus.CANCELLED, SimOrderStatus.REJECTED}
        )

    def submit(self, order: SimOrder) -> SimOrder:
        if any(existing.order_id == order.order_id for existing in self._orders):
            raise ValueError(f"duplicate order_id: {order.order_id}")
        normalized = self.instrument.normalize_quantity(order.quantity, rounding="down")
        if normalized <= ZERO:
            order.status = SimOrderStatus.REJECTED
            return order
        order.quantity = normalized
        if order.limit_price is not None:
            order.limit_price = self.instrument.round_price(order.limit_price)
        if order.stop_price is not None:
            order.stop_price = self.instrument.round_price(order.stop_price)
        order.sequence = self._sequence
        self._sequence += 1
        self._orders.append(order)
        return order

    submit_order = submit

    def cancel(self, order_id: str) -> bool:
        for order in self._orders:
            if order.order_id == order_id and order in self.open_orders:
                order.status = SimOrderStatus.CANCELLED
                return True
        return False

    def process(self, event: MarketEvent) -> list[Fill]:
        if self._last_timestamp is not None and event.timestamp < self._last_timestamp:
            raise ValueError("market events must be chronological")
        self._last_timestamp = event.timestamp
        event_liquidity = (
            event.volume * self.participation_rate
            if event.volume > ZERO
            else self.default_event_liquidity
        )
        event_liquidity = self.instrument.normalize_quantity(event_liquidity, rounding="down")
        produced: list[Fill] = []

        for order in sorted(self.open_orders, key=lambda item: (item.active_at, item.sequence)):
            if event_liquidity <= ZERO:
                break
            if event.timestamp < order.active_at:
                continue
            order.status = SimOrderStatus.ACTIVE
            base_price = self._eligible_price(order, event)
            if base_price is None:
                continue
            quantity = min(order.remaining_quantity, event_liquidity)
            quantity = self.instrument.normalize_quantity(quantity, rounding="down")
            if quantity <= ZERO:
                continue
            price = self._impacted_price(order.side, base_price, quantity, event)
            if order.order_type in {SimOrderType.LIMIT, SimOrderType.STOP_LIMIT}:
                price = (
                    min(price, order.limit_price)
                    if order.side is Side.BUY
                    else max(price, order.limit_price)
                )
            order.filled_quantity += quantity
            event_liquidity -= quantity
            order.status = (
                SimOrderStatus.FILLED
                if order.remaining_quantity == ZERO
                else SimOrderStatus.PARTIAL
            )
            notional = abs(self.instrument.notional(quantity, price))
            costs = self._calculate_cost(order.side, quantity, price)
            cash_delta = -(order.side.sign * notional) - costs
            self.cash += cash_delta
            fill = Fill(
                order.order_id,
                event.timestamp,
                order.side,
                quantity,
                price,
                notional,
                costs,
                cash_delta,
                order.remaining_quantity,
                order.status,
            )
            self.fills.append(fill)
            produced.append(fill)
        return produced

    on_event = process

    def run(self, events: Iterable[MarketEvent]) -> list[Fill]:
        output: list[Fill] = []
        for event in events:
            output.extend(self.process(event))
        return output

    def _quotes(self, event: MarketEvent) -> tuple[Decimal | None, Decimal | None]:
        bid, ask = event.bid, event.ask
        reference = event.open if event.open is not None else event.close
        if reference is not None:
            half = reference * self.synthetic_spread_bps / (BPS * Decimal("2"))
            bid = bid if bid is not None else reference - half
            ask = ask if ask is not None else reference + half
        return bid, ask

    def _stop_triggered(self, order: SimOrder, event: MarketEvent) -> bool:
        if order.triggered:
            return True
        bid, ask = self._quotes(event)
        if order.side is Side.BUY:
            trigger = (
                (ask is not None and ask >= order.stop_price)
                or (event.high is not None and event.high >= order.stop_price)
            )
        else:
            trigger = (
                (bid is not None and bid <= order.stop_price)
                or (event.low is not None and event.low <= order.stop_price)
            )
        order.triggered = bool(trigger)
        return order.triggered

    def _market_price(self, side: Side, event: MarketEvent, stop_price: Decimal | None = None) -> Decimal | None:
        bid, ask = self._quotes(event)
        quote = ask if side is Side.BUY else bid
        opening = event.open
        if stop_price is not None and opening is not None:
            # Pessimistic gap handling: never improve on a triggered stop.
            gap = max(opening, stop_price) if side is Side.BUY else min(opening, stop_price)
            if quote is None:
                return gap
            return max(quote, gap) if side is Side.BUY else min(quote, gap)
        return quote or event.close or event.open

    def _eligible_price(self, order: SimOrder, event: MarketEvent) -> Decimal | None:
        kind = order.order_type
        if kind in {SimOrderType.STOP, SimOrderType.STOP_LIMIT}:
            if not self._stop_triggered(order, event):
                return None
            if kind is SimOrderType.STOP:
                return self._market_price(order.side, event, order.stop_price)
            kind = SimOrderType.LIMIT

        if kind is SimOrderType.MARKET:
            return self._market_price(order.side, event)

        bid, ask = self._quotes(event)
        if order.side is Side.BUY:
            touched = (
                (ask is not None and ask <= order.limit_price)
                or (event.low is not None and event.low <= order.limit_price)
            )
        else:
            touched = (
                (bid is not None and bid >= order.limit_price)
                or (event.high is not None and event.high >= order.limit_price)
            )
        # Limit fills are kept at the limit, intentionally avoiding optimistic
        # assumptions about the unknowable intrabar path.
        return order.limit_price if touched else None

    def _impacted_price(
        self,
        side: Side,
        price: Decimal,
        quantity: Decimal,
        event: MarketEvent,
    ) -> Decimal:
        if event.volume > ZERO:
            fraction = min(ONE, quantity / event.volume)
        else:
            fraction = ONE
        multiplier = ONE + side.sign * self.market_impact_bps * fraction / BPS
        raw = price * multiplier
        rounding = "ceiling" if side is Side.BUY else "floor"
        return self.instrument.round_price(raw, rounding=rounding)

    def _calculate_cost(self, side: Side, quantity: Decimal, price: Decimal) -> Decimal:
        calculator = self.cost_calculator
        method = getattr(calculator, "calculate", None)
        if method is None:
            method = getattr(calculator, "calculate_cost", None)
        if method is not None:
            value = method(self.instrument, side, quantity, price)
        elif callable(calculator):
            value = calculator(self.instrument, side, quantity, price)
        else:
            raise TypeError("cost_calculator must be callable or provide calculate()")
        cost = _decimal(value, "cost")
        if cost < ZERO:
            raise ValueError("transaction costs must be non-negative")
        return cost


def stable_hash(value: Any) -> str:
    """SHA-256 of canonical JSON or raw bytes."""
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=lambda item: item.isoformat() if isinstance(item, datetime) else str(item),
        ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class ExperimentManifest:
    code_hash: str
    config_hash: str
    data_hash: str
    cost_hash: str
    started_at: datetime
    finished_at: datetime
    seed: int
    provenance: Mapping[str, Any]
    experiment_id: str = ""

    def __post_init__(self) -> None:
        for name in ("code_hash", "config_hash", "data_hash", "cost_hash"):
            if not getattr(self, name):
                raise ValueError(f"{name} is required")
        started, finished = _utc(self.started_at), _utc(self.finished_at)
        if finished < started:
            raise ValueError("finished_at must not precede started_at")
        object.__setattr__(self, "started_at", started)
        object.__setattr__(self, "finished_at", finished)
        object.__setattr__(self, "provenance", dict(self.provenance))
        if not self.experiment_id:
            object.__setattr__(self, "experiment_id", stable_hash(self.to_dict(include_id=False)))

    @classmethod
    def create(
        cls,
        *,
        code: Any,
        config: Any,
        data: Any,
        costs: Any,
        started_at: datetime,
        finished_at: datetime,
        seed: int,
        provenance: Mapping[str, Any],
    ) -> "ExperimentManifest":
        return cls(
            stable_hash(code),
            stable_hash(config),
            stable_hash(data),
            stable_hash(costs),
            started_at,
            finished_at,
            seed,
            provenance,
        )

    def to_dict(self, *, include_id: bool = True) -> dict[str, Any]:
        result = asdict(self)
        result["started_at"] = self.started_at.isoformat()
        result["finished_at"] = self.finished_at.isoformat()
        if not include_id:
            result.pop("experiment_id", None)
        return result


@dataclass(frozen=True, slots=True)
class DataSplit:
    train: tuple[int, ...]
    test: tuple[int, ...]


def walk_forward_splits(
    n_samples: int,
    train_size: int,
    test_size: int,
    *,
    step: int | None = None,
    expanding: bool = False,
) -> list[DataSplit]:
    if min(n_samples, train_size, test_size) <= 0:
        raise ValueError("sample and window sizes must be positive")
    stride = test_size if step is None else step
    if stride <= 0:
        raise ValueError("step must be positive")
    result: list[DataSplit] = []
    test_start = train_size
    while test_start + test_size <= n_samples:
        train_start = 0 if expanding else test_start - train_size
        result.append(
            DataSplit(
                tuple(range(train_start, test_start)),
                tuple(range(test_start, test_start + test_size)),
            )
        )
        test_start += stride
    return result


def purged_cv_splits(
    n_samples: int,
    n_splits: int,
    *,
    purge: int = 0,
    embargo: int = 0,
) -> list[DataSplit]:
    if n_samples <= 1 or not 2 <= n_splits <= n_samples:
        raise ValueError("n_splits must be between 2 and n_samples")
    if purge < 0 or embargo < 0:
        raise ValueError("purge and embargo must be non-negative")
    base, remainder = divmod(n_samples, n_splits)
    result: list[DataSplit] = []
    cursor = 0
    all_indices = tuple(range(n_samples))
    for fold in range(n_splits):
        size = base + (1 if fold < remainder else 0)
        start, stop = cursor, cursor + size
        blocked_start = max(0, start - purge)
        blocked_stop = min(n_samples, stop + purge + embargo)
        train = tuple(i for i in all_indices if not blocked_start <= i < blocked_stop)
        result.append(DataSplit(train, tuple(range(start, stop))))
        cursor = stop
    return result


purged_kfold_splits = purged_cv_splits


def adjust_pvalues(pvalues: Sequence[float], method: str = "holm") -> list[float]:
    """Bonferroni, Holm, or Benjamini-Hochberg adjusted p-values."""
    values = [float(value) for value in pvalues]
    if any(not 0 <= value <= 1 for value in values):
        raise ValueError("p-values must be in [0, 1]")
    n = len(values)
    if n == 0:
        return []
    method = method.lower().replace("_", "-")
    if method == "bonferroni":
        return [min(1.0, value * n) for value in values]
    order = sorted(range(n), key=values.__getitem__)
    adjusted = [0.0] * n
    if method == "holm":
        running = 0.0
        for rank, index in enumerate(order):
            running = max(running, (n - rank) * values[index])
            adjusted[index] = min(1.0, running)
        return adjusted
    if method in {"bh", "fdr-bh", "benjamini-hochberg"}:
        running = 1.0
        for rank in range(n - 1, -1, -1):
            index = order[rank]
            running = min(running, values[index] * n / (rank + 1))
            adjusted[index] = min(1.0, running)
        return adjusted
    raise ValueError("method must be bonferroni, holm, or benjamini-hochberg")


multiple_testing_correction = adjust_pvalues


def ar1(
    values: Sequence[float], *, lags: int | None = None
) -> dict[str, float]:
    """OLS of ``x_t`` on ``x_{t-1}`` with a Newey-West standard error.

    Lives here rather than in a research tool because the question it answers -- *is
    this series predictable at all* -- is prior to any strategy question, and every
    backtest that reports a P&L should be answerable against it. A strategy set can
    only be as good as the predictability of the series it trades.

    Newey-West rather than an i.i.d. error, and the reason is not pedantry: an
    i.i.d. standard error on autocorrelated data understates the true variance of
    the estimate and inflates apparent significance, which is the one direction of
    error that manufactures findings. The default lag is the Newey-West rule of
    thumb, which grows slowly with n and lands near 5 at these sample sizes.
    """
    xs = [float(v) for v in values]
    n = len(xs)
    if n < 20:
        return {"phi": 0.0, "se": 0.0, "t": 0.0, "n": float(n)}
    lead, lag_ = xs[:-1], xs[1:]
    m = len(lead)
    mx = sum(lead) / m
    my = sum(lag_) / m
    sxx = sum((v - mx) ** 2 for v in lead)
    if sxx <= 0.0:
        return {"phi": 0.0, "se": 0.0, "t": 0.0, "n": float(m)}
    sxy = sum((a - mx) * (b - my) for a, b in zip(lead, lag_))
    phi = sxy / sxx
    alpha = my - phi * mx
    resid = [b - (alpha + phi * a) for a, b in zip(lead, lag_)]

    if lags is None:
        lags = int(math.ceil(4.0 * (m / 100.0) ** (2.0 / 9.0)))
    band = max(1, int(lags))
    u = [r * (v - mx) for r, v in zip(resid, lead)]
    long_run = sum(v * v for v in u)
    for offset in range(1, band + 1):
        weight = 1.0 - offset / (band + 1.0)
        long_run += weight * 2.0 * sum(
            u[i] * u[i - offset] for i in range(offset, m)
        )
    variance = long_run / (sxx * sxx)
    se = math.sqrt(variance) if variance > 0.0 else 0.0
    return {"phi": phi, "se": se, "t": (phi / se if se > 0 else 0.0), "n": float(m)}


def variance_ratio(values: Sequence[float], lag: int) -> dict[str, float]:
    """Lo-MacKinlay variance ratio at one lag.

    ``VR(q) = Var(q-bar sum) / (q * Var(1-bar))``. A random walk gives 1. Above 1
    means positive autocorrelation and below 1 the reverse, and on real markets the
    sign at short lags is frequently negative -- which is a fact about markets, and
    is exactly the kind of thing a strategy should be told about rather than
    discovered by losing money to it.

    The z statistic uses the overlapping-sums correction ``(2q-1)(q-1)/q``, because
    the naive variance of overlapping q-bar sums understates the error badly enough
    to make a random walk look significant.
    """
    xs = [float(v) for v in values]
    n = len(xs)
    if lag < 2 or n < 4 * lag + 10:
        return {"vr": 1.0, "z": 0.0, "n": float(n)}
    mu = sum(xs) / n
    dev = [x - mu for x in xs]
    var1 = sum(v * v for v in dev) / (n - 1)
    if var1 <= 0.0:
        return {"vr": 1.0, "z": 0.0, "n": float(n)}
    sums = [sum(dev[i : i + lag]) for i in range(n - lag + 1)]
    m = len(sums)
    mean_q = sum(sums) / m
    var_q = sum((s - mean_q) ** 2 for s in sums) / (m - 1)
    vr = var_q / (lag * var1)
    denom = math.sqrt(2.0 * (2.0 * lag - 1.0) * (lag - 1.0) / lag)
    return {
        "vr": vr,
        "z": (math.sqrt(n) * (vr - 1.0) / denom) if denom > 0 else 0.0,
        "n": float(n),
    }


def implied_spread(values: Sequence[float]) -> dict[str, float]:
    """Roll (1984): the effective bid-ask spread implied by a price series.

    Negative first-order autocorrelation in an *efficient* market is not
    predictability, it is the bid-ask bounce -- an observation that alternates
    between bid and ask produces price changes that mean-revert mechanically. Roll
    showed the bounce is not merely an explanation but a measurement: the spread can
    be read back out of the price series itself, with no quote data at all.

    That is why this matters here rather than being a curiosity. A -3.44 t-statistic
    on 4h autocorrelation is exactly what a spread looks like, and the two have to be
    told apart before the autocorrelation is called an edge. The registered test
    lives in ``tools/edge_ceiling.py``; this is only the estimator.

    ``gamma1`` is the *unnormalized* first-order serial covariance
    ``E(d_t * d_{t+1})``, in the same units as ``values``. Keeping it raw matters:
    the spread is a square root of it, so every downstream comparison should be made
    on ``gamma1`` and the ordering conclusions do not depend on the constant below.

    The constant is reported in two forms on purpose. ``spread`` uses Roll's
    ``2*sqrt(-gamma1)``; ``spread_alt`` uses the ``sqrt(-2*gamma1)`` that appears in
    several restatements of the paper. The two differ by a factor of ``sqrt(2)``, and
    which one is canonical depends on whether ``gamma1`` was defined as a covariance
    or a correlation in the source being followed. Rather than pick one from memory
    and have an absolute comparison to a real cost inherit the error, both are
    returned and any claim that turns on the absolute level has to survive either.

    Returns ``spread = 0.0`` when ``gamma1 >= 0``: there is no negative covariance,
    so there is nothing for this estimator to explain. That is "not applicable", not
    "zero cost", and callers must not read it as the latter.
    """
    xs = [float(v) for v in values]
    n = len(xs)
    if n < 3:
        return {
            "gamma1": 0.0, "spread": 0.0, "spread_alt": 0.0,
            "t": 0.0, "n": float(n),
        }
    lead, lag_ = xs[:-1], xs[1:]
    m = len(lead)
    gamma1 = sum(a * b for a, b in zip(lead, lag_)) / (m - 1)

    # Newey-West on the products z_t = d_t * d_{t+1}: same reasoning as `ar1`, and
    # the same error direction if omitted. `gamma1` is itself autocorrelated in the
    # squared series even when `d_t` is not, so an i.i.d. error here would
    # understate and manufacture significance -- precisely for the statistic being
    # used to decide whether a real effect exists.
    mean_z = gamma1
    z = [a * b for a, b in zip(lead, lag_)]
    dev_z = [v - mean_z for v in z]
    bands = max(1, int(math.ceil(4.0 * (m / 100.0) ** (2.0 / 9.0))))
    long_run = sum(v * v for v in dev_z)
    for offset in range(1, bands + 1):
        weight = 1.0 - offset / (bands + 1.0)
        long_run += weight * 2.0 * sum(
            dev_z[i] * dev_z[i - offset] for i in range(offset, m - 1)
        )
    variance = long_run / ((m - 1) * (m - 1)) if m > 1 else 0.0
    se = math.sqrt(variance) if variance > 0.0 else 0.0
    t = gamma1 / se if se > 0 else 0.0

    if gamma1 >= 0.0:
        return {
            "gamma1": gamma1,
            "spread": 0.0,
            "spread_alt": 0.0,
            "t": t,
            "n": float(m),
        }
    neg = -gamma1
    return {
        "gamma1": gamma1,
        "spread": 2.0 * math.sqrt(neg),
        "spread_alt": math.sqrt(2.0 * neg),
        "t": t,
        "n": float(m),
    }


def _block_resample_means(
    values: Sequence[float], samples: int, rng: random.Random, block: int
) -> list[float]:
    """Block-bootstrap means of one series, for an empirical null distribution."""
    n = len(values)
    if n == 0:
        return []
    block = max(1, min(int(block), n))
    need = -(-n // block)
    top = n - block
    means: list[float] = []
    for _ in range(int(samples)):
        total = 0.0
        for _ in range(need):
            start = rng.randint(0, top) if top > 0 else 0
            total += sum(values[start : start + block])
        means.append(total / n)
    return means


def _joint_null_maxima(
    series: Sequence[Sequence[float]],
    *,
    samples: int,
    seed: int,
    block: int | None,
    trim: float,
) -> tuple[float, list[float], list[float]]:
    """Observed and bootstrap distribution of the studentized maximum statistic.

    The statistic is ``max_k (mean_k / se_k)`` over strategies, which is the whole
    question the multiplicity correction exists to answer: not "did any single
    strategy work" but "would the best of them beat zero by chance".

    Studentizing before taking the maximum is not cosmetic. A strategy with a tiny
    mean and a tiny variance can otherwise dominate the maximum and decide the
    answer on its own, which is Hansen's objection to the raw statistic and the
    reason SPA beats Reality Check.

    Each strategy is recentred on its own mean to impose the null "this strategy has
    no edge", then resampled in blocks so autocorrelation survives into the resample.
    The standard error comes from the *uncentred* series: the null must not be
    allowed to manufacture its own dispersion.

    ``trim`` drops that fraction of the **most erratic** models -- the ones whose
    standard error is largest relative to their effect -- before the maximum is
    taken. This is Hansen's stated motivation, "reduces the influence of erratic
    forecasts", and it is worth being precise about what trimming cannot do:
    dropping the *worst performers* from a maximum is arithmetically a no-op,
    because removing items from the bottom cannot change the largest. That was
    measured here rather than assumed, and the first version of this function
    trimmed by performance and therefore did nothing at all while appearing to.
    Trimming by reliability does something, because it changes which models are
    allowed to set the threshold.
    """
    rows = [[float(v) for v in s] for s in series]
    rows = [r for r in rows if len(r) >= 3]
    if not rows:
        return 0.0, [], []

    se_list: list[float] = []
    obs_means: list[float] = []
    for row in rows:
        n = len(row)
        mean = sum(row) / n
        obs_means.append(mean)
        deviations = [v - mean for v in row]
        b = int(block or max(1, int(math.ceil(n ** (1.0 / 3.0)))))
        b = max(1, min(b, n))
        rng = random.Random(seed + len(se_list))
        draws = _block_resample_means(deviations, samples, rng, b)
        # Centre the bootstrap on zero so the null distribution is of the mean, not
        # of the dispersion; then take its spread as the denominator.
        var = sum((d - sum(draws) / len(draws)) ** 2 for d in draws) / max(
            1, len(draws) - 1
        )
        se_list.append(math.sqrt(max(var, 0.0)) if var > 0 else 0.0)

    stats = [
        (m / s) if s > 0 else 0.0 for m, s in zip(obs_means, se_list)
    ]

    # Trim by reliability, not by performance. See the docstring: dropping the worst
    # performers cannot change a maximum, so a performance trim is a no-op dressed
    # up as a safeguard. Erratic models -- large standard error, small effect -- are
    # the ones whose noise inflates the null maximum and therefore the bar.
    keep_idx = list(range(len(rows)))
    if trim > 0.0 and len(rows) > 2:
        reliability = sorted(
            range(len(rows)),
            key=lambda i: (obs_means[i] * obs_means[i]) / max(se_list[i], 1e-18),
        )
        drop = int(len(rows) * float(trim))
        dropped = set(reliability[:drop])
        remaining = [i for i in keep_idx if i not in dropped]
        if remaining:
            keep_idx = remaining
            obs_means = [obs_means[i] for i in keep_idx]
            se_list = [se_list[i] for i in keep_idx]
            stats = [stats[i] for i in keep_idx]
            rows = [rows[i] for i in keep_idx]

    def _max_of(values: Sequence[float]) -> float:
        if not values:
            return 0.0
        return max(values)

    observed = _max_of(stats)

    # Bootstrap the whole panel together so the cross-strategy dependence the gate
    # already models (they share price data) is not broken by resampling in
    # isolation: each resample index set is reused across strategies.
    n_bars = min(len(r) for r in rows)
    b = int(block or max(1, int(math.ceil(n_bars ** (1.0 / 3.0)))))
    b = max(1, min(b, n_bars))
    need = -(-n_bars // b)
    top = n_bars - b
    means_by_strategy = [sum(r) / n_bars for r in rows]
    centred = [
        [v - means_by_strategy[i] for v in rows[i][:n_bars]] for i in range(len(rows))
    ]

    null_stats: list[float] = []
    for _ in range(int(samples)):
        picks: list[tuple[int, int]] = []
        for _ in range(need):
            start = rng.randint(0, top) if top > 0 else 0
            picks.append((start, start + b))
        per_strategy = []
        for i in range(len(centred)):
            row = centred[i]
            total = 0.0
            for lo, hi in picks:
                total += sum(row[lo:hi])
            per_strategy.append(total / n_bars)
        null_stats.append(
            _max_of([(m / s) if s > 0 else 0.0 for m, s in zip(per_strategy, se_list)])
        )
    return observed, null_stats, stats


def reality_check(
    series: Sequence[Sequence[float]],
    *,
    samples: int = 1000,
    seed: int = 42,
    block: int | None = None,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """White (2000), Econometrica 68(5):1097-1121 -- the test that is asked here.

    Corrects for the whole search at once rather than per hypothesis. `adjust_pvalues`
    answers "is this strategy significant after a penalty"; this answers "given
    everything that was tried together, would the best result have appeared by
    chance". Those come apart, and the second is the question that matches a research
    log running twelve registered searches.

    The difference shows up in what happens when everything fails. Per-hypothesis
    correction leaves each of twenty strategies with a 5% chance of clearing; the
    Reality Check tests one hypothesis -- the maximum -- and controls its own size,
    so a batch of pure noise is rejected as a batch rather than yielding one or two
    accidental winners to promote.
    """
    observed, nulls, stats = _joint_null_maxima(
        series, samples=samples, seed=seed, block=block, trim=0.0
    )
    if not nulls:
        return {
            "ok": False,
            "reason": "insufficient_series",
            "strategies": 0,
        }
    exceed = sum(1 for v in nulls if v >= observed)
    p = (1.0 + exceed) / (1.0 + len(nulls))
    return {
        "ok": True,
        "strategies": len(stats),
        "max_stat": observed,
        "best_strategy_index": (
            max(range(len(stats)), key=lambda i: stats[i])
            if stats
            else None
        ),
        "p_value": p,
        "reject": p < alpha,
        "bootstrap_samples": len(nulls),
    }


def spa_test(
    series: Sequence[Sequence[float]],
    *,
    samples: int = 1000,
    seed: int = 42,
    block: int | None = None,
    alpha: float = 0.05,
    trim: float = 0.10,
) -> dict[str, Any]:
    """Hansen (2005), JBES 23(4):365-376 -- SPA, the stronger form.

    Two modifications to the Reality Check, both aimed at the same problem: a single
    hopeless alternative dragging the maximum and with it the threshold.

    1. **Studentization.** The statistic is divided by its own standard error before
       the maximum, so a strategy with a tiny mean and a tiny variance cannot decide
       the answer alone.
    2. **Trimming.** The worst `trim` fraction is dropped before the maximum is
       taken, so the threshold is set by the strategies that could plausibly be
       real rather than by the one that is furthest from it.

    The result is more power and less sensitivity to irrelevant alternatives -- which
    matters in exactly this setting, where a batch of twenty-one strategies almost
    certainly contains a few genuinely bad ones and the untrimmed test lets them set
    the bar for the rest.
    """
    observed, nulls, stats = _joint_null_maxima(
        series, samples=samples, seed=seed, block=block, trim=trim
    )
    if not nulls:
        return {"ok": False, "reason": "insufficient_series", "strategies": 0}
    exceed = sum(1 for v in nulls if v >= observed)
    p = (1.0 + exceed) / (1.0 + len(nulls))
    return {
        "ok": True,
        "strategies": len(stats),
        "max_stat": observed,
        "p_value": p,
        "reject": p < alpha,
        "bootstrap_samples": len(nulls),
        "trim": trim,
    }


def cscv_pbo(
    series: Sequence[Sequence[float]],
    *,
    splits: int = 8,
    performance=None,
) -> dict[str, Any]:
    """Bailey & Lopez de Prado (2014) -- probability of backtest overfitting.

    Combinatorially Symmetric Cross-Validation. Every combination of the S/2 in-sample
    and S/2 out-of-sample halves is used, each strategy's performance ranked in both,
    and the logits of the rank degradation are recorded. The PBO is the share of
    splits where the in-sample winner was out-of-sample *last* -- an OOS logit below
    zero.

    This measures something none of the other tests do, and it is the one this gate
    was blind to. A corrected p-value can pass and the ranking still be meaningless:
    if the in-sample winner is chosen out of a set of statistically indistinguishable
    strategies, *any* of them would have won, and the winner would change if the
    sample were split differently. That is selection on noise with every reported
    number intact -- p-value significant, Sharpe positive, PBO near one.

    A PBO above 0.5 means the selection procedure is worse than random; 1.0 means the
    in-sample winner landed out-of-sample dead last every time.

    ``performance`` maps a series index to a performance measure, so the same code
    ranks on Sharpe, on net P&L or on anything else. It defaults to mean return
    because that is what a P&L report already has.
    """
    rows = [[float(v) for v in s] for s in series]
    rows = [r for r in rows if len(r) >= 4]
    n_strategies = len(rows)
    n_bars = min((len(r) for r in rows), default=0)
    if n_strategies < 2 or n_bars < splits:
        return {
            "ok": False,
            "reason": "insufficient_series",
            "strategies": n_strategies,
            "bars": n_bars,
        }

    half = splits // 2
    if half < 1:
        return {"ok": False, "reason": "splits_too_small", "strategies": n_strategies}
    n_combinations = math.comb(n_bars, half)
    if n_combinations <= 1:
        return {
            "ok": False,
            "reason": "no_combinations",
            "strategies": n_strategies,
        }

    # Cap the enumeration: this is exponential and would otherwise be the slowest
    # thing in the gate. Subsampling combinations is unbiased for the PBO estimate
    # and the count actually used is reported.
    rng = random.Random(7)
    cap = 512
    combos = list(_combinations(n_bars, half))
    sampled = len(combos)
    if sampled > cap:
        combos = rng.sample(combos, cap)
        sampled = cap

    def _score(subset: Sequence[int]) -> list[float]:
        out = []
        for row in rows:
            window = [row[i] for i in subset]
            if performance is not None:
                out.append(float(performance(window)))
            else:
                out.append(sum(window) / len(window))
        return out

    logits: list[float] = []
    for combo in combos:
        inside = list(combo)
        outside = [i for i in range(n_bars) if i not in set(inside)]
        if not outside:
            continue
        in_scores = _score(inside)
        out_scores = _score(outside)
        in_order = sorted(range(n_strategies), key=lambda i: in_scores[i])
        out_order = sorted(range(n_strategies), key=lambda i: out_scores[i])
        in_rank = {s: r for r, s in enumerate(in_order)}
        out_rank = {s: r for r, s in enumerate(out_order)}
        worst = in_order[-1]
        rank_is = in_rank[worst]
        rank_oos = out_rank[worst]
        denominator = float(n_strategies)
        # Logit of the relative rank, the measure the paper uses: a winner that ends
        # up mid-pack gives a negative logit, and dead last gives the extreme one.
        rel_is = (rank_is + 1) / (denominator + 1)
        rel_oos = (rank_oos + 1) / (denominator + 1)
        logits.append(math.log(rel_oos / (1 - rel_oos)) - math.log(rel_is / (1 - rel_is)))

    if not logits:
        return {
            "ok": False,
            "reason": "no_splits",
            "strategies": n_strategies,
        }
    overfit = sum(1 for v in logits if v < 0.0) / len(logits)
    return {
        "ok": True,
        "strategies": n_strategies,
        "bars": n_bars,
        "splits": splits,
        "splits_used": len(logits),
        "combinations_available": n_combinations,
        "combinations_used": sampled,
        "pbo": overfit,
        "logits_mean": sum(logits) / len(logits),
        # Above 0.5 the procedure is worse than picking at random; at 1.0 the
        # in-sample winner finished last out-of-sample every single time.
        "worse_than_random": overfit > 0.5,
    }


def _combinations(n: int, k: int) -> Any:
    from itertools import combinations

    return combinations(range(n), k)


def decompose_cost(
    fee_bps: float,
    slippage_bps: float,
    measured_spread_bps: float,
) -> dict[str, float]:
    """Split a per-fill cost model into the parts that behave differently.

    Almgren et al. (2005) found that execution cost separates into a *permanent*
    part, driven by the size of the order and persisting after it completes, and a
    *temporary* part, driven by how fast it is executed and vanishing when it is
    done. The split matters operationally because only one of them is reducible:
    a temporary cost can be traded away by scheduling, a permanent one cannot be
    traded away at any speed.

    That decomposition is the wrong frame for this system's cost model, and
    applying it is what surfaced the problem. A round trip here is charged as
    ``fee_bps + slippage_bps`` per fill, and **the fee is not an impact at all** --
    it is a transfer to the venue that no execution schedule can influence.
    Splitting a cost model that is mostly fees into "permanent impact versus
    temporary impact" produces a confident number about a quantity that does not
    exist.

    So this reports the three parts as they actually behave:

    - ``fee`` -- a transfer. Neither permanent nor temporary. Not reducible.
    - ``crossing`` -- the measured bid-ask spread, the only part that is a genuine
      market-impact cost and the part Roll (1984) can measure from prices alone.
    - ``residual_slippage`` -- whatever slippage is charged beyond the measured
      spread. This is where unmodelled impact would live if it existed, so its size
      is the honest upper bound on the impact we have not accounted for.

    ``impact_bps`` is the share a strategy's edge is actually competing against,
    and it is the number every "the edge does not cover the cost" claim should be
    measured against. A conclusion drawn against the full model overstates the
    barrier by whatever share is fees.

    Returns the split plus ``fee_share``, which is the fraction of the charged cost
    that no execution improvement can touch.
    """
    fee = float(fee_bps)
    slippage = float(slippage_bps)
    crossing = float(measured_spread_bps)
    residual = max(0.0, slippage - crossing)
    total = fee + slippage
    impact = crossing + residual
    return {
        "total_bps": total,
        "fee_bps": fee,
        "crossing_bps": crossing,
        "residual_slippage_bps": residual,
        "impact_bps": impact,
        "fee_share": (fee / total) if total > 0 else 0.0,
        "impact_share": (impact / total) if total > 0 else 0.0,
    }


def breakeven_vs_cost(
    edge_bps_per_trade: float,
    cost_bps_per_fill: float,
    *,
    fee_bps: float | None = None,
    measured_spread_bps: float | None = None,
) -> dict[str, float]:
    """Shortfall ratio of a per-trade edge against a per-fill cost, both ways.

    The two arguments are in *different* units on purpose and the conversion is done
    here, because mixing them is an easy and quiet error: an edge is naturally
    measured per round trip while a cost schedule is quoted per fill, and comparing
    them directly understates the barrier by exactly a factor of two. The first
    version of this function took both at face value and reported 12.8x where the
    round-trip comparison gives 25.6x -- a number already stated in a research log.

    Returns both ratios because they answer different questions. The full model is
    what the account is actually charged. The impact-only figure excludes exchange
    fees, which no execution schedule can influence, and is the barrier a strategy
    can do anything about.

    ``fee_bps`` is needed to separate the two; without it the impact-only ratio is
    reported as equal to the full one rather than guessed.
    """
    edge = float(edge_bps_per_trade)
    per_fill = float(cost_bps_per_fill)
    round_trip = 2.0 * per_fill
    impact_round_trip = round_trip
    if fee_bps is not None and measured_spread_bps is not None:
        split = decompose_cost(
            float(fee_bps), per_fill - float(fee_bps), measured_spread_bps
        )
        impact_round_trip = 2.0 * split["impact_bps"]
    return {
        "edge_bps": edge,
        "cost_bps_full_round_trip": round_trip,
        "shortfall_vs_full": (round_trip / edge) if edge > 0 else float("inf"),
        "cost_bps_impact_round_trip": impact_round_trip,
        "shortfall_vs_impact": (
            impact_round_trip / edge) if edge > 0 else float("inf"),
    }


def resolvable_phi(n: int, *, confidence: float = 1.96) -> float:
    """Smallest autocorrelation this sample size could tell apart from zero.

    The standard error of an AR(1) coefficient behaves like ``1/sqrt(n)``, so the
    answer is ``confidence/sqrt(n)``. This is the arithmetic behind every "not
    significant" in a research log, and it is the reason a per-window number cannot
    be called anything: 749 bars cannot resolve 0.072, so a 748-bar window can never
    distinguish a true 0.05 from zero no matter what the market does.
    """
    if n <= 0:
        return float("inf")
    return confidence / math.sqrt(n)


def bootstrap_stress(
    returns: Sequence[float],
    *,
    samples: int = 1000,
    horizon: int | None = None,
    seed: int = 0,
    statistic: Callable[[Sequence[float]], float] = sum,
) -> list[float]:
    if not returns or samples <= 0:
        raise ValueError("returns and positive samples are required")
    size = len(returns) if horizon is None else horizon
    if size <= 0:
        raise ValueError("horizon must be positive")
    rng = random.Random(seed)
    source = tuple(float(item) for item in returns)
    return [float(statistic([rng.choice(source) for _ in range(size)])) for _ in range(samples)]


def monte_carlo_stress(
    returns: Sequence[float],
    *,
    simulations: int = 1000,
    horizon: int | None = None,
    seed: int = 0,
    block_size: int = 1,
) -> list[float]:
    """Deterministic block-bootstrap Monte Carlo terminal compounded returns."""
    if not returns or simulations <= 0 or block_size <= 0:
        raise ValueError("returns, simulations, and block_size must be positive")
    source = tuple(float(item) for item in returns)
    size = len(source) if horizon is None else horizon
    if size <= 0:
        raise ValueError("horizon must be positive")
    rng = random.Random(seed)
    output: list[float] = []
    for _ in range(simulations):
        path: list[float] = []
        while len(path) < size:
            start = rng.randrange(len(source))
            path.extend(source[(start + offset) % len(source)] for offset in range(block_size))
        output.append(math.prod(1.0 + item for item in path[:size]) - 1.0)
    return output


def sensitivity_plateau(
    scores: Mapping[float, float],
    *,
    tolerance: float = 0.05,
    min_points: int = 2,
) -> tuple[float, ...]:
    """Largest contiguous parameter region near the global optimum."""
    if not scores or tolerance < 0 or min_points <= 0:
        raise ValueError("scores, non-negative tolerance, and min_points are required")
    ordered = sorted((float(parameter), float(score)) for parameter, score in scores.items())
    best = max(score for _, score in ordered)
    threshold = best - abs(best) * tolerance
    regions: list[list[float]] = []
    current: list[float] = []
    for parameter, score in ordered:
        if score >= threshold:
            current.append(parameter)
        elif current:
            regions.append(current)
            current = []
    if current:
        regions.append(current)
    eligible = [region for region in regions if len(region) >= min_points]
    if not eligible:
        return ()
    winner = max(eligible, key=lambda region: (len(region), -ordered.index(next(x for x in ordered if x[0] == region[0]))))
    return tuple(winner)


def plateau_score(scores: Mapping[float, float], **kwargs: Any) -> float:
    region = sensitivity_plateau(scores, **kwargs)
    return mean(scores[item] for item in region) if region else float("nan")


__all__ = [
    "CostCalculator",
    "DataSplit",
    "ExperimentManifest",
    "Fill",
    "FillSimulator",
    "MarketEvent",
    "Side",
    "SimOrder",
    "SimOrderStatus",
    "SimOrderType",
    "ZeroCostCalculator",
    "adjust_pvalues",
    "ar1",
    "bootstrap_stress",
    "breakeven_vs_cost",
    "cscv_pbo",
    "decompose_cost",
    "implied_spread",
    "monte_carlo_stress",
    "multiple_testing_correction",
    "plateau_score",
    "purged_cv_splits",
    "purged_kfold_splits",
    "reality_check",
    "resolvable_phi",
    "sensitivity_plateau",
    "spa_test",
    "variance_ratio",
    "stable_hash",
    "walk_forward_splits",
]
