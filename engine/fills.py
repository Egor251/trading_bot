"""Immutable fill records with explicit cost attribution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping

from .costs import (
    CostBreakdown,
    InstrumentLike,
    TradeContext,
    _datetime,
    _decimal,
    _decimal_text,
    _iso,
    _required_text,
)


@dataclass(frozen=True, slots=True)
class FillRecord:
    """An execution whose cash impact includes a supplied cost breakdown.

    Costs are mandatory rather than inferred from a global schedule.  This makes
    replay/audit behavior independent of later schedule changes.
    """

    fill_id: str
    order_id: str
    venue: str
    account: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    trade_time: datetime
    costs: CostBreakdown
    contract_multiplier: Decimal = Decimal("1")
    currency: str = "USD"
    #: Venue rebate (maker fee credit). Positive values CREDIT cash; the cost
    #: breakdown cannot hold a negative line, so rebates live here explicitly.
    rebate: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        for name in ("fill_id", "order_id", "venue", "account", "symbol", "currency"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        side = _required_text(self.side, "side").lower()
        if side not in {"buy", "sell"}:
            raise ValueError("side must be 'buy' or 'sell'")
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "quantity", _decimal(self.quantity, "quantity", positive=True))
        object.__setattr__(self, "price", _decimal(self.price, "price", positive=True))
        object.__setattr__(
            self,
            "contract_multiplier",
            _decimal(self.contract_multiplier, "contract_multiplier", positive=True),
        )
        object.__setattr__(self, "trade_time", _datetime(self.trade_time, "trade_time"))
        object.__setattr__(self, "rebate", _decimal(self.rebate, "rebate"))
        if self.rebate < 0:
            raise ValueError("rebate must not be negative")
        if not isinstance(self.costs, CostBreakdown):
            raise TypeError("costs must be a CostBreakdown (use CostBreakdown() for no costs)")

    @property
    def gross_notional(self) -> Decimal:
        return self.quantity * self.price * self.contract_multiplier

    @property
    def total_cost(self) -> Decimal:
        return self.costs.total - self.rebate

    @property
    def cash_delta(self) -> Decimal:
        """Signed settlement cash movement; rebates credit it, costs reduce it."""
        gross_cash = self.gross_notional if self.side == "sell" else -self.gross_notional
        return gross_cash - self.total_cost

    @classmethod
    def from_trade(
        cls,
        context: TradeContext,
        *,
        fill_id: str,
        order_id: str,
        costs: CostBreakdown,
        rebate: Decimal = Decimal("0"),
    ) -> FillRecord:
        if not isinstance(context, TradeContext):
            raise TypeError("context must be a TradeContext")
        return cls(
            fill_id=fill_id,
            order_id=order_id,
            venue=context.venue,
            account=context.account,
            symbol=context.symbol,
            side=context.side,
            quantity=context.quantity,
            price=context.price,
            trade_time=context.trade_time,
            costs=costs,
            rebate=rebate,
            contract_multiplier=context.contract_multiplier,
            currency=context.currency,
        )

    @classmethod
    def for_instrument(
        cls,
        instrument: InstrumentLike,
        *,
        quantity: Decimal,
        price: Decimal,
        **values: Any,
    ) -> FillRecord:
        return cls(
            symbol=instrument.symbol,
            contract_multiplier=instrument.contract_multiplier,
            quantity=quantity,
            price=price,
            **values,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fill_id": self.fill_id,
            "order_id": self.order_id,
            "venue": self.venue,
            "account": self.account,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": _decimal_text(self.quantity),
            "price": _decimal_text(self.price),
            "trade_time": _iso(self.trade_time),
            "costs": self.costs.to_dict(),
            "contract_multiplier": _decimal_text(self.contract_multiplier),
            "currency": self.currency,
            "rebate": _decimal_text(self.rebate),
            "gross_notional": _decimal_text(self.gross_notional),
            "cash_delta": _decimal_text(self.cash_delta),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FillRecord:
        data = dict(value)
        reported_gross = data.pop("gross_notional", None)
        reported_cash = data.pop("cash_delta", None)
        data["costs"] = CostBreakdown.from_dict(data["costs"])
        result = cls(**data)
        if (
            reported_gross is not None
            and _decimal(reported_gross, "gross_notional") != result.gross_notional
        ):
            raise ValueError("serialized gross_notional does not match the fill")
        if (
            reported_cash is not None
            and _decimal(reported_cash, "cash_delta") != result.cash_delta
        ):
            raise ValueError("serialized cash_delta does not match the fill")
        return result


def extract_result_cost(result: Any, runtime: Any = None) -> float | None:
    """Best-effort fee/cost from OrderResult.raw or the runtime fills ledger."""
    raw = getattr(result, "raw", None)
    if isinstance(raw, dict):
        for key in ("cost", "total_cost", "fee"):
            if raw.get(key) not in (None, ""):
                try:
                    return float(raw[key])
                except (TypeError, ValueError):
                    pass
        costs = raw.get("costs")
        if isinstance(costs, dict) and costs.get("total") not in (None, ""):
            try:
                return float(costs["total"])
            except (TypeError, ValueError):
                pass
    orders = getattr(runtime, "orders", None) if runtime is not None else None
    # The attribute is `fill_ledger` (singular). Reading `fills_ledger` always
    # returned None, so every `execution.result` event carried `cost: null` and
    # the durable journal had no per-trade cost — silently deflating
    # expectancy in research and the cost columns in the UI.
    ledger = getattr(orders, "fill_ledger", None)
    if not ledger:
        return None
    # Prefer the fill for *this* order; `list(ledger)[-1]` attributed whichever
    # fill happened to be last, which is a different trade under concurrency.
    client_id = str(getattr(result, "client_id", "") or "")
    candidates = [
        item
        for item in ledger
        if not client_id or str(getattr(item, "order_id", "") or "") == client_id
    ]
    for item in reversed(candidates or list(ledger)):
        try:
            data = item.to_dict() if hasattr(item, "to_dict") else dict(item)
        except Exception:
            continue
        costs = data.get("costs") if isinstance(data.get("costs"), dict) else {}
        if costs.get("total") not in (None, ""):
            return float(costs["total"])
        if data.get("total_cost") not in (None, ""):
            return float(data["total_cost"])
    return None


__all__ = ["FillRecord", "extract_result_cost"]
