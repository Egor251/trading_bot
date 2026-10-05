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
    "monte_carlo_stress",
    "multiple_testing_correction",
    "plateau_score",
    "purged_cv_splits",
    "purged_kfold_splits",
    "resolvable_phi",
    "sensitivity_plateau",
    "variance_ratio",
    "stable_hash",
    "walk_forward_splits",
]
