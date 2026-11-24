"""Order intents and paper/live routing (tech doc §3.1 OrderManager)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
import math
from typing import Any
from uuid import uuid4

from .cost_calibration import ExecutionObservation
from .costs import (
    CostBreakdown,
    CostComponent,
    CostKind,
    CostLine,
    CostMatch,
    CostRule,
    CostSchedule,
    TradeContext,
)
from .fills import FillRecord
from .risk import RiskManager


def _log():
    import logging

    return logging.getLogger("order")


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    #: The broker's answer is not known: the send failed after the request may
    #: have been transmitted, or the reply could not be parsed. This is
    #: deliberately distinct from REJECTED (definitely not working) and from
    #: ACCEPTED (definitely working), because treating it as either one loses
    #: money — REJECTED frees the EXIT_PENDING reservation on a protective exit
    #: that may in fact be live, and ACCEPTED invents a fill. An UNKNOWN result
    #: must hold every reservation and force reconciliation instead.
    UNKNOWN = "unknown"


@dataclass(slots=True)
class OrderIntent:
    symbol: str
    side: OrderSide
    quantity: float
    strategy_id: str = ""
    order_type: str = "market"
    limit_price: float | None = None
    class_code: str = ""
    venue: str = ""
    account: str = ""
    reduce_only: bool = False
    exit_reason: str = ""
    #: Market price when the decision was taken. Slippage is measured against
    #: this, not against the previous close, because the strategy acted *because*
    #: of this price. Falls back to `limit_price` when the caller does not say.
    reference_price: float | None = None
    client_id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def slippage_reference(self) -> float | None:
        for candidate in (self.reference_price, self.limit_price):
            try:
                value = float(candidate)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return None

    @property
    def exposure_key(self) -> str:
        if self.class_code:
            return f"{self.class_code}:{self.symbol}"
        return self.symbol


@dataclass(slots=True)
class OrderResult:
    client_id: str
    status: OrderStatus
    filled_qty: float = 0.0
    avg_price: float | None = None
    reason: str = ""
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Position:
    """Signed position with average cost and mark-to-market PnL."""

    symbol: str
    quantity: float = 0.0
    avg_price: float = 0.0
    mark_price: float = 0.0
    realized_pnl: float = 0.0
    contract_multiplier: float = 1.0
    currency: str = "USD"

    @property
    def notional(self) -> float:
        return self.quantity * self.mark_price * self.contract_multiplier

    @property
    def unrealized_pnl(self) -> float:
        if abs(self.quantity) <= 1e-12:
            return 0.0
        return self.quantity * (self.mark_price - self.avg_price) * self.contract_multiplier

    def apply_fill(self, signed_qty: float, price: float) -> None:
        if abs(signed_qty) <= 1e-12:
            return
        old_qty = self.quantity
        new_qty = old_qty + signed_qty
        same_direction = old_qty == 0 or math.copysign(1.0, old_qty) == math.copysign(
            1.0, signed_qty
        )

        if same_direction:
            old_cost = abs(old_qty) * self.avg_price
            fill_cost = abs(signed_qty) * price
            self.avg_price = (old_cost + fill_cost) / abs(new_qty)
        else:
            closing_qty = min(abs(old_qty), abs(signed_qty))
            self.realized_pnl += (
                closing_qty
                * (price - self.avg_price)
                * math.copysign(1.0, old_qty)
                * self.contract_multiplier
            )
            if abs(new_qty) <= 1e-12:
                new_qty = 0.0
                self.avg_price = 0.0
            elif math.copysign(1.0, new_qty) != math.copysign(1.0, old_qty):
                # The fill crossed through flat; the remainder opens at fill price.
                self.avg_price = price

        self.quantity = new_qty
        self.mark_price = price


class OrderManager:
    """
    Validates intents via RiskManager and records paper / broker fills.

    ``mode=paper`` — synthetic fill. ``mode=quik|live`` — risk check then broker.
    """

    def __init__(
        self,
        risk: RiskManager,
        *,
        mode: str = "paper",
        equity: float = 10_000.0,
        #: Callable(symbol) -> recent traded notional, used by the liquidity
        #: gate. Without it the gate has no data and resolves to "allow".
        volume_feed: Any = None,
        cost_schedule: CostSchedule | None = None,
        instrument_registry: Any | None = None,
        account: str = "default",
        base_currency: str = "USD",
        legacy_fee_bps: float = 0.0,
        legacy_slippage_bps: float = 0.0,
    ) -> None:
        self.risk = risk
        self.mode = mode
        self.starting_equity = float(equity)
        self.equity = float(equity)
        self.peak_equity = float(equity)
        self.positions: dict[str, Position] = {}
        self.history: list[OrderResult] = []
        self.fill_ledger: list[FillRecord] = []
        #: Periodic carry charged against equity (perpetual funding, borrow).
        #: Deliberately separate from `total_costs`, which is a sum over the
        #: fill ledger — funding is not a fill and never appears there.
        self._carry_charged: float = 0.0
        #: Recent executions with expected-versus-actual costs. Kept here rather
        #: than derived later: the fill is the only moment both the reference
        #: price and the realised price still exist.
        self._exec_observations: list[ExecutionObservation] = []
        self._exec_observation_limit = 5000
        #: Optional callable(symbol) -> recent traded notional, wired by the
        #: runtime to its candle store. None means "no liquidity data", which
        #: the risk layer treats as unknown rather than as zero.
        self._volume_feed: Any = volume_feed
        self._broker_applied_qty: dict[str, float] = {}
        self._broker_applied_notional: dict[str, float] = {}
        self.cost_schedule = cost_schedule or self._legacy_cost_schedule(
            legacy_fee_bps=legacy_fee_bps,
            legacy_slippage_bps=legacy_slippage_bps,
        )
        self.instrument_registry = instrument_registry
        self.account = str(account or "default")
        self.base_currency = str(base_currency or "USD").upper()
        self.cash = Decimal(str(equity))
        self.risk_trace: deque[dict[str, Any]] = deque(maxlen=200)
        self._daily_turnover: float = 0.0
        #: Count of fills whose cost breakdown could not be computed. Surfaced in
        #: the snapshot so a stale cost schedule is visible instead of silent.
        self.cost_schedule_errors: int = 0
        # Day-scoped risk state. Both were previously tied to the *process*
        # lifetime:
        #   * daily loss was measured from `starting_equity`, so one 5% unrealized
        #     dip on an open book halted trading permanently (and, because
        #     max_daily_loss_pct < max_portfolio_drawdown in the shipped config,
        #     made the real drawdown gate unreachable);
        #   * turnover was never reset at all, so once the process had traded
        #     `max_daily_turnover_pct * equity` of notional *ever*, every
        #     subsequent order was rejected forever.
        self._day_key: str = self._current_day_key()
        self._day_start_equity: float = float(equity)

    def _current_day_key(self) -> str:
        """Trading-day key in UTC.

        Crypto trades continuously, so the UTC date is the session boundary for
        the venues this engine defaults to; equity venues can override
        ``day_key_hook``.
        """
        return datetime.now(UTC).date().isoformat()

    def _roll_day_if_needed(self) -> None:
        """Reset day-scoped counters when the session key changes."""
        key = self._current_day_key()
        if key == self._day_key:
            return
        _log().info(
            "risk day rolled %s -> %s (turnover %.2f -> 0)",
            self._day_key,
            key,
            self._daily_turnover,
        )
        self._day_key = key
        self._daily_turnover = 0.0
        # Day-start equity for the loss gate is the *realised* equity at the
        # boundary, not the boot capital.
        self._day_start_equity = float(self.equity_ex_unrealized)

    def note_day_start_equity(self, equity: float) -> None:
        """Seed the day-start equity used by the daily-loss gate."""
        try:
            value = float(equity)
        except (TypeError, ValueError):
            return
        if value > 0:
            self._day_start_equity = value

    @property
    def day_key(self) -> str:
        return self._day_key

    def _record_risk_trace(self, row: dict[str, Any]) -> None:
        self.risk_trace.append(dict(row))

    @staticmethod
    def _legacy_cost_schedule(
        *, legacy_fee_bps: float, legacy_slippage_bps: float
    ) -> CostSchedule | None:
        components: list[CostComponent] = []
        if legacy_fee_bps:
            components.append(
                CostComponent(
                    CostKind.BPS,
                    Decimal(str(legacy_fee_bps)),
                    name="legacy_fee_bps",
                )
            )
        if legacy_slippage_bps:
            components.append(
                CostComponent(
                    CostKind.SLIPPAGE,
                    Decimal(str(legacy_slippage_bps)),
                    name="legacy_slippage_bps",
                )
            )
        if not components:
            return None
        return CostSchedule(
            version="legacy-bps",
            rules=(
                CostRule(
                    rule_id="legacy-bps-fallback",
                    match=CostMatch(),
                    components=tuple(components),
                ),
            ),
        )

    @property
    def total_costs(self) -> Decimal:
        return sum((fill.total_cost for fill in self.fill_ledger), Decimal("0"))

    @property
    def carry_costs(self) -> float:
        """Net carry charged (positive = paid out, negative = received)."""
        return float(self._carry_charged)

    def _record_execution_observation(self, intent: Any, record: Any, *, raw: Any = None) -> None:
        """Capture what this fill actually cost, so the model can be checked.

        Without this the cost model is never tested against reality: every fill
        books the configured constants whether or not the venue agrees, and the
        error propagates into every PnL derived from it without ever surfacing.
        An observation that cannot be measured is dropped rather than recorded as
        zero, since a fabricated zero is worse than a gap -- it would read as
        free execution.
        """
        reference = getattr(intent, "slippage_reference", None)
        if not reference:
            return
        try:
            multiplier = float(getattr(record, "contract_multiplier", 1.0) or 1.0)
            notional = abs(float(record.quantity) * float(record.price) * multiplier)
            if notional <= 0:
                return
            broker_fee = (raw or {}).get("fee")
            observation = ExecutionObservation(
                fill_id=str(record.fill_id),
                venue=str(record.venue),
                symbol=str(record.symbol),
                side=str(record.side),
                quantity=abs(float(record.quantity)),
                reference_price=float(reference),
                fill_price=float(record.price),
                notional=notional,
                expected_cost=float(record.total_cost),
                actual_fee=float(broker_fee) if broker_fee is not None else None,
            )
        except Exception:  # noqa: BLE001 - measurement must never break a fill
            return
        self._exec_observations.append(observation)
        # Bounded: calibration needs a recent window, and an unbounded list
        # here would grow for the life of the process.
        if len(self._exec_observations) > self._exec_observation_limit:
            del self._exec_observations[
                : len(self._exec_observations) - self._exec_observation_limit
            ]

    def execution_observations(self) -> list[ExecutionObservation]:
        """Recent fills with expected-versus-actual costs, for calibration."""
        return list(self._exec_observations)

    @property
    def exposures(self) -> dict[str, float]:
        """Signed marked notionals, retained for snapshot compatibility."""
        return {
            key: position.notional
            for key, position in self.positions.items()
            if abs(position.quantity) > 1e-12
        }

    @property
    def gross_exposure(self) -> float:
        return sum(abs(value) for value in self.exposures.values())

    @property
    def leverage(self) -> float:
        eq = float(self.equity or 0.0)
        if eq <= 1e-12:
            return float("inf") if self.gross_exposure > 0 else 0.0
        return self.gross_exposure / eq

    @property
    def equity_ex_unrealized(self) -> float:
        """Equity without open MTM — used for daily-loss (avoids leverage noise)."""
        realized = sum(float(p.realized_pnl) for p in self.positions.values())
        return (
            float(self.starting_equity)
            + realized
            - float(self.total_costs)
            - float(self.carry_costs)
        )

    def apply_carry(self, symbol: str, rate: float) -> float | None:
        """Charge periodic carry (perpetual funding, borrow) against equity.

        A carry charge is not a fill. `total_costs` sums the fill ledger, so
        funding recorded there — or applied through `Portfolio`, which the live
        runtime does not use — never reached equity, and risk limits were
        computed from a base that ignored it. A position that pays funding all
        day must see equity fall.

        Sign convention matches the venue: a positive rate charges longs and
        credits shorts. Returns the amount charged (negative = credited), or
        ``None`` when nothing is held in ``symbol``.
        """
        position = self.positions.get(symbol)
        if position is None or abs(position.quantity) <= 1e-12:
            return None
        rate_value = float(rate)
        if not rate_value:
            return None
        mark = float(position.mark_price or position.avg_price or 0.0)
        if mark <= 0:
            return None
        charge = position.quantity * mark * rate_value
        # Long pays when the rate is positive; a short receives it.
        self._carry_charged += charge
        return charge

    def mark_to_market(self, symbol: str, price: float) -> float:
        if price <= 0:
            return self.equity
        position = self.positions.get(symbol)
        if position is not None:
            position.mark_price = float(price)
        realized = sum(p.realized_pnl for p in self.positions.values())
        unrealized = sum(p.unrealized_pnl for p in self.positions.values())
        self.equity = (
            self.starting_equity
            + realized
            + unrealized
            - float(self.total_costs)
            - float(self.carry_costs)
        )
        self.peak_equity = max(self.peak_equity, self.equity)
        return self.equity

    def set_starting_equity(self, equity: float) -> None:
        """Reset capital only when flat; avoids erasing live PnL accidentally."""
        if any(abs(p.quantity) > 1e-12 for p in self.positions.values()):
            raise ValueError("cannot reset equity with open positions")
        self.starting_equity = float(equity)
        self.equity = float(equity)
        self.peak_equity = float(equity)
        self.cash = Decimal(str(equity))
        self._daily_turnover = 0.0
        self._day_key = self._current_day_key()
        self._day_start_equity = float(equity)

    def clear_book(self, *, equity: float | None = None) -> None:
        """Wipe simulated positions/fills and restore cash (paper/phantom only)."""
        start = float(self.starting_equity if equity is None else equity)
        self.positions.clear()
        self.history.clear()
        self.fill_ledger.clear()
        self._broker_applied_qty.clear()
        self._broker_applied_notional.clear()
        self.starting_equity = start
        self.equity = start
        self.peak_equity = start
        self.cash = Decimal(str(start))
        self._daily_turnover = 0.0
        self._day_key = self._current_day_key()
        self._day_start_equity = start

    def clear_symbol(self, symbol_key: str) -> dict[str, Any]:
        """Remove one simulated position and related fills (paper/phantom only)."""
        key = str(symbol_key or "").strip()
        if not key:
            return {"cleared": False, "reason": "symbol_required"}
        targets = [k for k in list(self.positions) if k == key or k.endswith(f":{key}")]
        if not targets:
            targets = [key]
        removed: list[str] = []
        for k in targets:
            if self.positions.pop(k, None) is not None:
                removed.append(k)
            self._broker_applied_qty.pop(k, None)
            self._broker_applied_notional.pop(k, None)
        sym_tail = key.split(":")[-1]

        def _fill_symbol(fill: Any) -> str:
            if hasattr(fill, "symbol"):
                return str(getattr(fill, "symbol") or "")
            if isinstance(fill, dict):
                return str(fill.get("symbol") or "")
            return ""

        ledger = list(getattr(self, "fill_ledger", None) or [])
        kept = [
            f
            for f in ledger
            if _fill_symbol(f) not in set(targets) and _fill_symbol(f).split(":")[-1] != sym_tail
        ]
        self.fill_ledger = kept
        pruned_hist: list[Any] = []
        for h in self.history:
            h_sym = str(
                getattr(h, "symbol", "") or (h.get("symbol") if isinstance(h, dict) else "") or ""
            )
            if h_sym in set(targets) or h_sym.split(":")[-1] == sym_tail:
                continue
            pruned_hist.append(h)
        self.history = pruned_hist
        # Identity, not equality against a set of ints: the old predicate
        # compared each fill object with `id()` values, was always true, and
        # reversed the cash of fills belonging to positions that stayed open.
        kept_ids = {id(x) for x in kept}
        removed_fills = [f for f in ledger if id(f) not in kept_ids]
        # `cash` is a Decimal running total of every fill's cash_delta, while
        # `equity` is recomputed from starting_equity + realised + unrealized -
        # costs. Dropping fills without returning their cash deltas left
        # snapshot()["cash"] permanently wrong after a per-symbol reset, and
        # `test_paper_reset.py` only covered the full-wipe path.
        for fill in removed_fills:
            try:
                self.cash -= fill.cash_delta
            except AttributeError:  # pragma: no cover - defensive
                pass
        realized = sum(float(p.realized_pnl) for p in self.positions.values())
        unrealized = sum(float(p.unrealized_pnl) for p in self.positions.values())
        self.equity = (
            float(self.starting_equity)
            + realized
            + unrealized
            - float(self.total_costs)
            - float(self.carry_costs)
        )
        self.peak_equity = max(float(self.peak_equity), float(self.equity))
        return {
            "cleared": bool(removed),
            "symbols": removed,
            "equity": float(self.equity),
            "cash": float(self.cash),
        }

    def _instrument_for(self, intent: OrderIntent) -> Any | None:
        registry = self.instrument_registry
        if registry is None:
            return None
        try:
            return registry.resolve(
                intent.symbol,
                class_code=intent.class_code or None,
            )
        except Exception:
            return None

    def _calculate_costs(self, context: Any) -> CostBreakdown:
        """Cost breakdown that cannot abort a fill.

        ``CostSchedule.calculate`` raises when the schedule is not effective at
        ``trade_time`` — routine, because ``activate`` never re-checks the window
        at trade time. Unguarded, that exception propagated out of
        ``_record_fill`` *after* ``router.place()`` had already executed, so the
        order was live at the broker while the local book recorded nothing and no
        protective slice was opened.
        """
        if self.cost_schedule is None:
            return CostBreakdown()
        try:
            return self.cost_schedule.calculate(context)
        except Exception as exc:  # noqa: BLE001
            _log().error(
                "cost schedule unusable for %s (%s); recording zero costs and "
                "alerting. Refresh or reactivate the schedule.",
                getattr(context, "symbol", "?"),
                exc,
            )
            self.cost_schedule_errors += 1
            return CostBreakdown()

    def _notional_multiplier(self, intent: OrderIntent) -> float:
        """Contract multiplier used for risk notional (must match Position.notional)."""
        instrument = self._instrument_for(intent)
        if instrument is not None:
            try:
                return max(float(getattr(instrument, "contract_multiplier", 1) or 1), 1e-12)
            except (TypeError, ValueError):
                pass
        position = self.positions.get(intent.exposure_key)
        if position is not None:
            return max(float(position.contract_multiplier or 1.0), 1e-12)
        return 1.0

    def _record_fill(
        self,
        intent: OrderIntent,
        *,
        quantity: float,
        price: float,
        trade_time: datetime | None = None,
        raw: dict[str, Any] | None = None,
    ) -> FillRecord:
        """Apply one execution through the shared Decimal cost/fill pipeline."""
        instrument = self._instrument_for(intent)
        multiplier = Decimal(str(getattr(instrument, "contract_multiplier", Decimal("1"))))
        currency = str(getattr(instrument, "currency", self.base_currency))
        venue = str(getattr(instrument, "venue", self.mode or "paper"))
        asset_class = str(
            getattr(
                instrument,
                "asset_class",
                "future" if "FUT" in intent.class_code.upper() else "equity",
            )
        )
        position = self.positions.get(intent.exposure_key)
        signed = quantity if intent.side == OrderSide.BUY else -quantity
        old_qty = position.quantity if position is not None else 0.0
        position_effect = (
            "close"
            if old_qty and math.copysign(1.0, old_qty) != math.copysign(1.0, signed)
            else "open"
        )
        context = TradeContext(
            venue=venue,
            account=self.account,
            asset=intent.symbol,
            asset_class=asset_class,
            symbol=intent.symbol,
            side=intent.side.value,
            position_effect=position_effect,
            order_type=intent.order_type,
            liquidity=str((raw or {}).get("liquidity") or "unknown"),
            trade_time=trade_time or datetime.now(UTC),
            quantity=Decimal(str(quantity)),
            price=Decimal(str(price)),
            contract_multiplier=multiplier,
            tier_value=Decimal(str((raw or {}).get("tier_value") or "0")),
            currency=currency,
        )
        costs = self._calculate_costs(context)
        actual_fee = (raw or {}).get("actual_fee")
        if actual_fee not in (None, ""):
            # The broker's real fee is *added* to the modelled lines, not a
            # replacement for them. Replacing discarded commission and slippage,
            # so venues that report only an exchange fee booked zero slippage and
            # realised PnL silently diverged from the model the research gate
            # validated on. Sign and currency are normalised because a rebate or a
            # BNB-denominated fee is routine and `CostLine` rejects negatives —
            # which used to abort `_record_fill` and leave the position
            # unrecorded even though the broker had filled it.
            try:
                broker_fee = abs(Decimal(str(actual_fee)))
            except Exception:  # noqa: BLE001 - never let a fee break a fill
                broker_fee = None
            if broker_fee is not None:
                try:
                    costs = CostBreakdown(
                        lines=(
                            *costs.lines,
                            CostLine(
                                rule_id="broker-actual",
                                component="actual_fee",
                                kind=CostKind.FIXED,
                                amount=broker_fee,
                                priority=-1,
                            ),
                        ),
                        schedule_version=costs.schedule_version,
                        schedule_hash=costs.schedule_hash,
                    )
                except Exception:  # noqa: BLE001
                    _log().warning(
                        "could not attach broker fee %r for %s; using modelled costs",
                        actual_fee,
                        intent.exposure_key,
                    )
        record = FillRecord.from_trade(
            context,
            fill_id=str((raw or {}).get("fill_id") or uuid4().hex),
            order_id=intent.client_id,
            costs=costs,
        )
        self.fill_ledger.append(record)
        self._record_execution_observation(intent, record, raw=raw)
        self.cash += record.cash_delta
        self._daily_turnover += abs(float(quantity) * float(price) * float(multiplier))
        position = self.positions.setdefault(
            intent.exposure_key,
            Position(
                symbol=intent.exposure_key,
                contract_multiplier=float(multiplier),
                currency=currency,
            ),
        )
        position.apply_fill(signed, price)
        return record

    def _risk_qty(
        self,
        intent: OrderIntent,
        *,
        mark_price: float,
        dry_run: bool = False,
    ) -> tuple[OrderResult | None, float]:
        """Return (reject_result, qty). reject_result is set when blocked.

        ``dry_run=True`` skips history/halt side-effects (for operator preview).
        """
        if mark_price <= 0:
            result = OrderResult(
                client_id=intent.client_id,
                status=OrderStatus.REJECTED,
                reason="mark_price<=0",
            )
            if not dry_run:
                self.history.append(result)
            self._record_risk_trace(
                {
                    "symbol": intent.exposure_key,
                    "side": intent.side.value,
                    "qty_requested": intent.quantity,
                    "mark_price": mark_price,
                    "allowed": False,
                    "reason": "mark_price<=0",
                    "dry_run": dry_run,
                }
            )
            return result, 0.0

        key = intent.exposure_key
        if not dry_run:
            self.mark_to_market(key, mark_price)
            current_exposure = self.exposures.get(key, 0.0)
        else:
            # Genuinely read-only. This used to write `position.mark_price`,
            # which changes Position.notional -> gross_exposure -> leverage, and
            # the next real mark_to_market then recomputes equity and peak from
            # that preview mark. A hypothetical order could therefore trip or
            # clear no_leverage_headroom / portfolio_dd for subsequent real
            # orders — the preview changed the thing it was previewing.
            position = self.positions.get(key)
            if position is not None:
                held = abs(float(position.quantity))
                current_exposure = (
                    held * float(mark_price) * float(position.contract_multiplier or 1.0)
                ) * (1.0 if position.quantity > 0 else -1.0)
            else:
                current_exposure = 0.0
        gross = 0.0 if dry_run else float(self.gross_exposure)
        current_exposure = float(current_exposure)
        multiplier = self._notional_multiplier(intent)
        unit_notional = abs(float(mark_price) * multiplier)
        notional = abs(float(intent.quantity) * unit_notional)
        signed_notional = notional if intent.side == OrderSide.BUY else -notional
        proposed_exposure = current_exposure + signed_notional
        reduces_risk = abs(proposed_exposure) <= abs(current_exposure) + 1e-9
        max_lev = float(self.risk.limits.max_leverage or 0.0)
        equity = float(self.equity or 0.0)
        current_lev = (gross / equity) if equity > 0 else 0.0

        def _reject(reason: str) -> tuple[OrderResult, float]:
            result = OrderResult(
                client_id=intent.client_id,
                status=OrderStatus.REJECTED,
                reason=reason,
            )
            if not dry_run:
                self.history.append(result)
            self._record_risk_trace(
                {
                    "symbol": key,
                    "side": intent.side.value,
                    "qty_requested": intent.quantity,
                    "mark_price": mark_price,
                    "notional": notional,
                    "current_exposure": current_exposure,
                    "proposed_exposure": proposed_exposure,
                    "gross": gross,
                    "equity": equity,
                    "leverage": current_lev,
                    "max_leverage": max_lev,
                    "reduces_risk": reduces_risk,
                    "allowed": False,
                    "reason": reason,
                    "dry_run": dry_run,
                }
            )
            return result, 0.0

        # Book already at/over max leverage — only risk-reducing orders.
        # Soft reject (no emergency_halt): MTM can breach without a bad fill;
        # kill-switch spam blocks the consilium from normal reduce/rebalance.
        if max_lev > 0 and equity > 0 and not reduces_risk:
            if current_lev > max_lev + 1e-9:
                return _reject(f"no_leverage_headroom={current_lev:.3f}>{max_lev:.3f}")

        # Snapshot halt without calling check_portfolio_drawdown (it can halt).
        if self.risk.halted and not reduces_risk:
            return _reject(getattr(self.risk, "_halt_reason", "halted") or "halted")

        peak = float(self.peak_equity or equity)
        if peak > 0 and not reduces_risk:
            dd = max(0.0, (peak - equity) / peak)
            if dd >= float(self.risk.limits.max_portfolio_drawdown):
                reason = f"portfolio_dd={dd:.3f}"
                if not dry_run:
                    self.risk.emergency_halt(reason)
                return _reject(reason)

        # Use a non-halting clone of check_order gates via temporary halt flag.
        was_halted = self.risk.halted
        halt_reason = getattr(self.risk, "_halt_reason", "")
        if dry_run:
            self.risk._halted = False  # noqa: SLF001
        # Roll day-scoped counters *before* reading them, and never let a dry-run
        # preview mutate them.
        if not dry_run:
            self._roll_day_if_needed()
        try:
            decision = self.risk.check_order(
                equity=equity,
                symbol=key,
                notional=notional,
                current_symbol_exposure=current_exposure,
                proposed_symbol_exposure=proposed_exposure,
                current_gross_exposure=gross,
                reduces_risk=reduces_risk,
                strategy_id=str(intent.strategy_id or ""),
                dry_run=bool(dry_run),
                # Day-start equity, not the boot capital. See _roll_day_if_needed.
                day_start_equity=float(
                    self._day_start_equity
                    if self._day_start_equity > 0
                    else self.equity_ex_unrealized
                ),
                daily_turnover=float(self._daily_turnover),
                # Traded notional the risk gate needs to judge whether this
                # order is a small share of the market. Previously this was
                # never passed, so the liquidity gate resolved to "allow"
                # whatever the limit said -- a control that looked like
                # protection and provided none.
                average_daily_volume=self._volume_feed(key)
                if self._volume_feed is not None
                else None,
            )
        finally:
            if dry_run:
                self.risk._halted = was_halted  # noqa: SLF001
                self.risk._halt_reason = halt_reason  # noqa: SLF001

        if not decision.allowed:
            return _reject(decision.reason or "risk_reject")
        # NOTE: `check_order` → `check_order_rate` records the order in the local
        # rolling window, so there is deliberately no extra `record_order()` here
        # (that would double-count every accepted order).

        qty = intent.quantity
        if decision.scaled_size is not None and unit_notional > 0:
            qty = abs(float(decision.scaled_size) / unit_notional)
        self._record_risk_trace(
            {
                "symbol": key,
                "side": intent.side.value,
                "qty_requested": intent.quantity,
                "qty_allowed": qty,
                "mark_price": mark_price,
                "notional": notional,
                "scaled": decision.reason or "",
                "current_exposure": current_exposure,
                "proposed_exposure": proposed_exposure,
                "gross": gross,
                "equity": equity,
                "leverage": current_lev,
                "max_leverage": max_lev,
                "leverage_after": (
                    (gross - abs(current_exposure) + abs(proposed_exposure)) / equity
                    if equity > 0
                    else None
                ),
                "reduces_risk": reduces_risk,
                "allowed": True,
                "reason": decision.reason or "ok",
                "dry_run": dry_run,
            }
        )
        return None, qty

    def preview_risk(self, intent: OrderIntent, *, mark_price: float) -> dict[str, Any]:
        """Dry-run risk check — no fill, no emergency halt persistence."""
        reject, qty = self._risk_qty(intent, mark_price=mark_price, dry_run=True)
        last = self.risk_trace[-1] if self.risk_trace else {}
        return {
            "allowed": reject is None,
            "reason": "" if reject is None else reject.reason,
            "qty_requested": float(intent.quantity),
            "qty_allowed": float(qty),
            "trace": last,
        }

    def submit(
        self,
        intent: OrderIntent,
        *,
        mark_price: float,
    ) -> OrderResult:
        rejected, qty = self._risk_qty(intent, mark_price=mark_price)
        if rejected is not None:
            return rejected

        # Paper fills must respect instrument lot grid — otherwise headroom
        # clamp can leave 1e-13 dust that never exists on a real venue.
        instrument = self._instrument_for(intent)
        if instrument is not None and hasattr(instrument, "normalize_quantity"):
            try:
                lot_qty = float(instrument.normalize_quantity(qty))
            except Exception:
                lot_qty = float(qty)
            if lot_qty + 1e-18 < float(qty) and lot_qty <= 0:
                result = OrderResult(
                    client_id=intent.client_id,
                    status=OrderStatus.REJECTED,
                    reason="qty_below_lot_size",
                )
                self.history.append(result)
                return result
            if lot_qty <= 0:
                result = OrderResult(
                    client_id=intent.client_id,
                    status=OrderStatus.REJECTED,
                    reason="qty_below_lot_size",
                )
                self.history.append(result)
                return result
            qty = lot_qty

        key = intent.exposure_key
        fill = self._record_fill(intent, quantity=qty, price=mark_price)
        self.mark_to_market(key, mark_price)

        result = OrderResult(
            client_id=intent.client_id,
            status=OrderStatus.FILLED,
            filled_qty=qty,
            avg_price=mark_price,
            reason=self.mode,
            raw={
                "strategy_id": intent.strategy_id,
                "class_code": intent.class_code,
                "fill": fill.to_dict(),
                "applied_fill_qty": qty,
                "applied_fill_price": mark_price,
            },
        )
        self.history.append(result)
        return result

    def record_broker_result(
        self,
        intent: OrderIntent,
        broker_result: OrderResult,
        *,
        mark_price: float,
        qty: float,
    ) -> OrderResult:
        """Apply actual broker fills; ACCEPTED without fill never changes exposure."""
        if broker_result.status not in {
            OrderStatus.ACCEPTED,
            OrderStatus.FILLED,
            OrderStatus.PARTIAL,
        }:
            self.history.append(broker_result)
            return broker_result

        key = intent.exposure_key
        reported_qty = float(broker_result.filled_qty or 0.0)
        reported_px = float(broker_result.avg_price or mark_price)
        previous_qty = self._broker_applied_qty.get(intent.client_id, 0.0)
        previous_notional = self._broker_applied_notional.get(intent.client_id, 0.0)
        is_delta = bool((broker_result.raw or {}).get("fill_is_delta"))
        cumulative_qty = previous_qty + reported_qty if is_delta else reported_qty
        cumulative_notional = (
            previous_notional + reported_qty * reported_px
            if is_delta
            else cumulative_qty * reported_px
        )
        fill_qty = max(0.0, cumulative_qty - previous_qty)
        fill_px = (
            (cumulative_notional - previous_notional) / fill_qty if fill_qty > 0 else reported_px
        )
        if fill_qty > 0:
            fill = self._record_fill(
                intent,
                quantity=fill_qty,
                price=fill_px,
                trade_time=broker_result.ts,
                raw=broker_result.raw,
            )
            self.mark_to_market(key, fill_px)
            self._broker_applied_qty[intent.client_id] = cumulative_qty
            self._broker_applied_notional[intent.client_id] = cumulative_notional
        else:
            fill = None

        result = OrderResult(
            client_id=intent.client_id or broker_result.client_id,
            status=broker_result.status,
            filled_qty=reported_qty,
            avg_price=reported_px,
            reason=broker_result.reason or self.mode,
            raw={
                **(broker_result.raw or {}),
                "strategy_id": intent.strategy_id,
                "class_code": intent.class_code,
                "applied_fill_qty": fill_qty,
                "applied_fill_price": fill_px,
                **({"fill": fill.to_dict()} if fill is not None else {}),
            },
        )
        self.history.append(result)
        return result
