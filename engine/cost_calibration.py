"""Measure what execution actually costs, instead of asserting what it should.

Costs in this system are constants: `fee_bps: 5`, `slippage_bps: 2`. They are
not measured. Every fill the system books charges those numbers whether or not
they match the venue, so the cost model drifts silently and every PnL derived
from it inherits the error in a direction nobody can see. The existing
`nightly_fee_reconciliation` would catch that -- except nothing ever calls it
outside tests, so it compares an expected fee against an expected fee.

This module supplies the missing half: the *observed* side of the comparison.

The quantity that matters is implementation shortfall -- how much worse the fill
was than the price at which the decision was made, expressed in basis points of
notional:

    buy  pays when the fill is above the reference
    sell pays when the fill is below it

so a single signed number is positive when the trade hurt, regardless of
direction. That is the figure a cost model has to reproduce, and the one a
strategy's edge has to survive.

Percentiles, not averages. Execution cost is a heavy-tailed distribution: the
mean is dominated by the occasional crossing a thin book, while what a
strategy experiences on a typical trade is the median and what it must survive
is the tail. Calibrating on the mean therefore guarantees the model is wrong
for most trades and badly wrong for the worst ones.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class ExecutionObservation:
    """One executed fill, with what was expected and what actually happened."""

    fill_id: str
    venue: str
    symbol: str
    side: str
    quantity: float
    #: Price the decision was made against. Slippage is measured against this,
    #: not against the previous close: the strategy acted because of this price.
    reference_price: float
    #: Price the venue actually gave.
    fill_price: float
    #: Notional in the base currency, used to turn a price gap into a cost.
    notional: float
    #: What the cost schedule predicted for this trade.
    expected_cost: float = 0.0
    #: What the venue actually charged. None when the venue reports no fee.
    actual_fee: float | None = None

    def __post_init__(self) -> None:
        side = str(self.side).lower()
        if side not in {"buy", "sell"}:
            raise ValueError(f"side must be buy or sell, got {self.side!r}")
        object.__setattr__(self, "side", side)
        if float(self.notional) <= 0:
            raise ValueError("notional must be positive to measure a cost")
        if float(self.reference_price) <= 0 or float(self.fill_price) <= 0:
            raise ValueError("prices must be positive to measure slippage")

    @property
    def slippage_bps(self) -> float:
        """Signed implementation shortfall in bps. Positive = worse than expected."""
        ref = float(self.reference_price)
        gap = (float(self.fill_price) - ref) / ref * 10_000.0
        # A seller is hurt by a lower fill, so the sign inverts for sells.
        return gap if self.side == "buy" else -gap

    @property
    def slippage_cost(self) -> float:
        return float(self.notional) * self.slippage_bps / 10_000.0

    @property
    def total_cost(self) -> float:
        """Everything this fill actually cost: fee plus adverse price movement."""
        return self.slippage_cost + float(self.actual_fee or 0.0)

    @property
    def total_cost_bps(self) -> float:
        return self.total_cost / float(self.notional) * 10_000.0

    def to_fee_line(self) -> dict:
        """Shape accepted by `nightly_fee_reconciliation`."""
        return {
            "execution_id": self.fill_id,
            "expected_fee": float(self.expected_cost),
            "actual_fee": float(self.actual_fee if self.actual_fee is not None
                                else self.expected_cost),
            "expected_slippage_bps": 0.0,
            "actual_slippage_bps": self.slippage_bps,
        }


def cost_model_from_config(raw_cfg: Mapping[str, Any] | None) -> dict[str, float]:
    """The per-fill cost the product actually charges, read from its own config.

    Two numbers for the same thing is the defect this exists to remove. The shipped
    config charges ``fee_bps: 5`` + ``slippage_bps: 2`` = 7 bps per fill, while
    ``tools/edge_ceiling.py`` hardcodes ``cost_bps=10.0`` at nineteen call sites.
    Research therefore ran 1.43x more pessimistic than the system it documents, and
    any reconciliation of fills against the model would have compared observed costs
    to the wrong number.

    A research tool that hardcodes its own cost is making a claim about the product
    without reading it, and the claim is wrong the moment the config moves. Single
    source first: this function is the only place that turns configuration into a
    cost, and both the engine and the research tools read it from here.

    The block lives at ``services.phantom_service`` -- it is a plugin's own config,
    which is how ``phantom_service`` reads it at runtime. ``trading.phantom_service``
    is also accepted because that is where a reader would reasonably look first, and
    silently returning the fallback from a misplaced path is the kind of quiet
    miss this module exists to prevent.

    Falls back to the shipped values if the block is absent, because a missing cost
    model should not make execution look free.
    """
    cfg = raw_cfg or {}
    # Passed the block itself, not the whole config. Supporting that matters more
    # than it looks: without it the caller reads `services.phantom_service`, hands
    # the block over, and gets the *fallback* -- which happens to equal the shipped
    # values, so the test passes while the wiring is quietly dead and any config
    # change is ignored.
    if "fee_bps" in cfg or "slippage_bps" in cfg:
        return _split_cost(cfg)
    block: Mapping[str, Any] = {}
    for path in (("services", "phantom_service"), ("trading", "phantom_service")):
        candidate = cfg.get(path[0])
        if isinstance(candidate, Mapping):
            found = candidate.get(path[1])
            if isinstance(found, Mapping):
                block = found
                break
    if not block:
        candidate = cfg.get("phantom_service")
        if isinstance(candidate, Mapping):
            block = candidate

    fee = block.get("fee_bps")
    slip = block.get("slippage_bps")
    return _split_cost({"fee_bps": fee, "slippage_bps": slip})


def _split_cost(block: Mapping[str, Any]) -> dict[str, float]:
    fee = block.get("fee_bps")
    slip = block.get("slippage_bps")
    fee_value = float(fee) if fee is not None else DEFAULT_FEE_BPS
    slip_value = float(slip) if slip is not None else DEFAULT_SLIPPAGE_BPS
    return {
        "fee_bps": fee_value,
        "slippage_bps": slip_value,
        "total_bps": fee_value + slip_value,
    }


#: What the shipped config charges. Named so the fallback above is a stated
#: assumption rather than a silent zero, and so a test can assert the config still
#: says what this module believes it says.
DEFAULT_FEE_BPS = 5.0
DEFAULT_SLIPPAGE_BPS = 2.0


@dataclass(frozen=True)
class CalibrationReport:
    """Observed cost against the model that predicted it."""

    samples: int
    model_slippage_bps: float
    observed_p50_bps: float
    observed_p90_bps: float
    observed_p99_bps: float
    mean_cost_bps: float
    p90_cost_bps: float
    #: True when the model understates what execution costs.
    understates: bool

    def to_dict(self) -> dict:
        return {
            "samples": self.samples,
            "model_slippage_bps": round(self.model_slippage_bps, 4),
            "observed_p50_bps": round(self.observed_p50_bps, 4),
            "observed_p90_bps": round(self.observed_p90_bps, 4),
            "observed_p99_bps": round(self.observed_p99_bps, 4),
            "mean_cost_bps": round(self.mean_cost_bps, 4),
            "p90_cost_bps": round(self.p90_cost_bps, 4),
            "understates": bool(self.understates),
        }


def _percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile; `q` in [0, 1]."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = max(0.0, min(1.0, q)) * (len(ordered) - 1)
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return float(ordered[low])
    weight = pos - low
    return float(ordered[low]) * (1.0 - weight) + float(ordered[high]) * weight


def calibrate(
    observations: Iterable[ExecutionObservation],
    *,
    model_slippage_bps: float = 0.0,
    minimum_samples: int = 30,
) -> CalibrationReport:
    """Compare realised execution cost against the configured model.

    `minimum_samples` guards against calibrating on a handful of fills. Ten
    trades is enough to produce a number and not enough to trust it, and acting
    on it would replace a wrong constant with a differently wrong one.
    """
    rows = list(observations)
    slippage = [o.slippage_bps for o in rows]
    costs = [o.total_cost_bps for o in rows]

    p50 = _percentile(slippage, 0.50)
    p90 = _percentile(slippage, 0.90)
    p99 = _percentile(slippage, 0.99)
    mean = (sum(costs) / len(costs)) if costs else 0.0

    # Both statistics, because they miss opposite failures.
    #
    # p90 catches a fat tail -- the realistic shape when crossing a thin book,
    # where one trade in five is terrible and the median still looks perfect.
    # But it is blind to a *thin* tail: a single disaster among thirty sits near
    # the 97th percentile, so p90 reads 0.0 and the gate stays silent.
    #
    # The mean is what catches that thin tail, and it is diluted in exactly the
    # case where p90 already covers it. So the union of the two flags every
    # shape, at the price of occasionally over-reacting on a lone outlier.
    #
    # Over-reacting here is the right way to be wrong. A needless recalibration
    # costs an operator's attention; a missed one costs money. `minimum_samples`
    # is what keeps the over-reaction bounded.
    enough = len(rows) >= minimum_samples
    understates = enough and (
        p90 > float(model_slippage_bps) or mean > float(model_slippage_bps)
    )

    return CalibrationReport(
        samples=len(rows),
        model_slippage_bps=float(model_slippage_bps),
        observed_p50_bps=p50,
        observed_p90_bps=p90,
        observed_p99_bps=p99,
        mean_cost_bps=mean,
        p90_cost_bps=_percentile(costs, 0.90),
        understates=understates,
    )