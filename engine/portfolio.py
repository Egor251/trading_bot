"""Broker-independent, Decimal based portfolio accounting.

The ledger deliberately has no execution side effects.  Callers record fills
and periodic costs after they have happened; the resulting state can then be
used by risk and allocation code.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable


DecimalLike = Decimal | int | float | str
ZERO = Decimal("0")
ONE = Decimal("1")


class PortfolioError(ValueError):
    """Raised when an accounting operation would make the ledger invalid."""


def _decimal(value: DecimalLike, name: str = "value") -> Decimal:
    if isinstance(value, bool):
        raise PortfolioError(f"{name} must be numeric")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PortfolioError(f"{name} must be a finite decimal") from exc
    if not result.is_finite():
        raise PortfolioError(f"{name} must be a finite decimal")
    return result


def _currency(value: str) -> str:
    result = str(value).strip().upper()
    if not result:
        raise PortfolioError("currency must not be empty")
    return result


@dataclass(slots=True)
class Position:
    symbol: str
    currency: str
    quantity: Decimal = ZERO
    average_price: Decimal = ZERO
    mark_price: Decimal = ZERO
    multiplier: Decimal = ONE
    strategy_id: str = "unattributed"
    asset_class: str = "equity"

    @property
    def market_value(self) -> Decimal:
        return self.quantity * self.mark_price * self.multiplier

    @property
    def unrealized_pnl(self) -> Decimal:
        return (
            self.quantity
            * (self.mark_price - self.average_price)
            * self.multiplier
        )


@dataclass(slots=True)
class FuturesPosition:
    symbol: str
    currency: str
    quantity: Decimal
    settlement_price: Decimal
    mark_price: Decimal
    multiplier: Decimal
    initial_margin_per_contract: Decimal
    maintenance_margin_per_contract: Decimal
    strategy_id: str = "unattributed"

    @property
    def initial_margin(self) -> Decimal:
        return abs(self.quantity) * self.initial_margin_per_contract

    @property
    def maintenance_margin(self) -> Decimal:
        return abs(self.quantity) * self.maintenance_margin_per_contract

    @property
    def unrealized_pnl(self) -> Decimal:
        return (
            self.quantity
            * (self.mark_price - self.settlement_price)
            * self.multiplier
        )


@dataclass(slots=True)
class StrategyAttribution:
    strategy_id: str
    realized_pnl: Decimal = ZERO
    unrealized_pnl: Decimal = ZERO
    trading_costs: Decimal = ZERO
    funding_costs: Decimal = ZERO
    borrow_costs: Decimal = ZERO
    capital_base: Decimal = ZERO

    @property
    def total_costs(self) -> Decimal:
        return self.trading_costs + self.funding_costs + self.borrow_costs

    @property
    def net_pnl(self) -> Decimal:
        return self.realized_pnl + self.unrealized_pnl - self.total_costs

    @property
    def net_return(self) -> Decimal:
        return self.net_pnl / self.capital_base if self.capital_base else ZERO


@dataclass(frozen=True, slots=True)
class MarginStatus:
    initial_requirement: Decimal
    maintenance_requirement: Decimal
    collateral: Decimal
    initial_headroom: Decimal
    maintenance_headroom: Decimal
    margin_call: bool


class PortfolioLedger:
    """Multi-currency cash, positions, derivatives and strategy attribution."""

    def __init__(self, base_currency: str = "USD") -> None:
        self.base_currency = _currency(base_currency)
        self.cash: dict[str, Decimal] = defaultdict(Decimal)
        self._fx: dict[tuple[str, str], Decimal] = {}
        self.positions: dict[tuple[str, str], Position] = {}
        self.futures: dict[tuple[str, str], FuturesPosition] = {}
        self._attribution: dict[str, StrategyAttribution] = {}

    def set_fx_rate(
        self, from_currency: str, to_currency: str, rate: DecimalLike
    ) -> None:
        source, target = _currency(from_currency), _currency(to_currency)
        value = _decimal(rate, "rate")
        if value <= ZERO:
            raise PortfolioError("FX rate must be greater than zero")
        self._fx[source, target] = value
        self._fx[target, source] = ONE / value

    def fx_rate(self, from_currency: str, to_currency: str) -> Decimal:
        source, target = _currency(from_currency), _currency(to_currency)
        if source == target:
            return ONE
        direct = self._fx.get((source, target))
        if direct is not None:
            return direct
        queue: deque[tuple[str, Decimal]] = deque([(source, ONE)])
        visited = {source}
        while queue:
            current, accumulated = queue.popleft()
            for (left, right), rate in self._fx.items():
                if left != current or right in visited:
                    continue
                converted = accumulated * rate
                if right == target:
                    return converted
                visited.add(right)
                queue.append((right, converted))
        raise PortfolioError(f"missing FX path {source}/{target}")

    def convert(
        self,
        amount: DecimalLike,
        from_currency: str,
        to_currency: str | None = None,
    ) -> Decimal:
        target = self.base_currency if to_currency is None else to_currency
        return _decimal(amount, "amount") * self.fx_rate(from_currency, target)

    def deposit(self, amount: DecimalLike, currency: str | None = None) -> Decimal:
        ccy = self.base_currency if currency is None else _currency(currency)
        value = _decimal(amount, "amount")
        if value < ZERO:
            raise PortfolioError("deposit amount must not be negative")
        self.cash[ccy] += value
        return self.cash[ccy]

    def withdraw(
        self, amount: DecimalLike, currency: str | None = None
    ) -> Decimal:
        ccy = self.base_currency if currency is None else _currency(currency)
        value = _decimal(amount, "amount")
        if value < ZERO or self.cash[ccy] < value:
            raise PortfolioError("insufficient cash")
        self.cash[ccy] -= value
        return self.cash[ccy]

    def cash_value(self, currency: str | None = None) -> Decimal:
        target = self.base_currency if currency is None else _currency(currency)
        return sum(
            (self.convert(amount, ccy, target) for ccy, amount in self.cash.items()),
            ZERO,
        )

    def exchange_cash(
        self,
        amount: DecimalLike,
        from_currency: str,
        to_currency: str,
        *,
        fee: DecimalLike = ZERO,
        strategy_id: str | None = None,
    ) -> Decimal:
        """Exchange cash at the recorded FX path and return the amount received.

        ``fee`` is denominated in the source currency.  The operation is
        atomic: validation happens before either balance is changed.
        """
        source, target = _currency(from_currency), _currency(to_currency)
        value, cost = _decimal(amount, "amount"), _decimal(fee, "fee")
        if source == target:
            raise PortfolioError("FX exchange currencies must differ")
        if value <= ZERO or cost < ZERO:
            raise PortfolioError("invalid FX exchange amount or fee")
        received = self.convert(value, source, target)
        if self.cash[source] < value + cost:
            raise PortfolioError("insufficient cash")
        self.cash[source] -= value + cost
        self.cash[target] += received
        if strategy_id is not None:
            self._strategy(strategy_id).trading_costs += self.convert(cost, source)
        return received

    convert_cash = exchange_cash

    def _strategy(self, strategy_id: str) -> StrategyAttribution:
        sid = str(strategy_id).strip() or "unattributed"
        if sid not in self._attribution:
            self._attribution[sid] = StrategyAttribution(sid)
        return self._attribution[sid]

    def trade_equity(
        self,
        symbol: str,
        quantity: DecimalLike,
        price: DecimalLike,
        currency: str,
        *,
        strategy_id: str = "unattributed",
        fees: DecimalLike = ZERO,
        multiplier: DecimalLike = ONE,
        asset_class: str = "equity",
    ) -> Position:
        """Record a signed fill (positive buy, negative sell)."""
        qty, px = _decimal(quantity, "quantity"), _decimal(price, "price")
        fee, mult = _decimal(fees, "fees"), _decimal(multiplier, "multiplier")
        ccy = _currency(currency)
        if qty == ZERO or px <= ZERO or fee < ZERO or mult <= ZERO:
            raise PortfolioError("invalid fill quantity, price, fee, or multiplier")
        sid = str(strategy_id).strip() or "unattributed"
        key = (str(symbol).strip().upper(), sid)
        position = self.positions.get(
            key,
            Position(
                symbol=key[0],
                currency=ccy,
                multiplier=mult,
                strategy_id=sid,
                asset_class=asset_class,
            ),
        )
        if position.currency != ccy or position.multiplier != mult:
            raise PortfolioError("position currency or multiplier changed")

        old_qty = position.quantity
        closing_qty = (
            min(abs(old_qty), abs(qty))
            if old_qty and old_qty * qty < ZERO
            else ZERO
        )
        realized = ZERO
        if closing_qty:
            direction = ONE if old_qty > ZERO else -ONE
            realized = closing_qty * (px - position.average_price) * direction * mult

        new_qty = old_qty + qty
        if old_qty == ZERO or old_qty * qty > ZERO:
            total_units = abs(old_qty) + abs(qty)
            position.average_price = (
                abs(old_qty) * position.average_price + abs(qty) * px
            ) / total_units
        elif new_qty == ZERO:
            position.average_price = ZERO
        elif old_qty * new_qty < ZERO:
            position.average_price = px

        position.quantity = new_qty
        position.mark_price = px
        self.positions[key] = position
        self.cash[ccy] -= qty * px * mult + fee

        attribution = self._strategy(sid)
        attribution.realized_pnl += self.convert(realized, ccy)
        attribution.trading_costs += self.convert(fee, ccy)
        opened_qty = max(ZERO, abs(qty) - closing_qty)
        attribution.capital_base += self.convert(opened_qty * px * mult, ccy)
        return position

    trade_crypto = trade_equity

    def mark_position(
        self, symbol: str, price: DecimalLike, *, strategy_id: str | None = None
    ) -> None:
        px = _decimal(price, "price")
        if px <= ZERO:
            raise PortfolioError("price must be greater than zero")
        symbol = str(symbol).strip().upper()
        matched = False
        for (item_symbol, sid), position in self.positions.items():
            if item_symbol == symbol and (strategy_id is None or sid == strategy_id):
                position.mark_price = px
                matched = True
        if not matched:
            raise PortfolioError(f"position not found: {symbol}")

    def open_futures(
        self,
        symbol: str,
        quantity: DecimalLike,
        price: DecimalLike,
        currency: str,
        *,
        initial_margin: DecimalLike,
        maintenance_margin: DecimalLike,
        multiplier: DecimalLike = ONE,
        strategy_id: str = "unattributed",
        fees: DecimalLike = ZERO,
    ) -> FuturesPosition:
        qty, px = _decimal(quantity, "quantity"), _decimal(price, "price")
        initial = _decimal(initial_margin, "initial_margin")
        maintenance = _decimal(maintenance_margin, "maintenance_margin")
        mult, fee = _decimal(multiplier, "multiplier"), _decimal(fees, "fees")
        ccy, sid = _currency(currency), str(strategy_id).strip() or "unattributed"
        if (
            qty == ZERO
            or px <= ZERO
            or mult <= ZERO
            or initial <= ZERO
            or maintenance <= ZERO
            or maintenance > initial
            or fee < ZERO
        ):
            raise PortfolioError("invalid futures contract parameters")
        key = (str(symbol).strip().upper(), sid)
        if key in self.futures:
            raise PortfolioError("use close_futures before replacing a futures position")
        proposed = FuturesPosition(
            key[0], ccy, qty, px, px, mult, initial, maintenance, sid
        )
        required = self.convert(proposed.initial_margin + fee, ccy)
        if self.margin_status().initial_headroom < required:
            raise PortfolioError("insufficient initial margin headroom")
        self.cash[ccy] -= fee
        self.futures[key] = proposed
        attribution = self._strategy(sid)
        attribution.trading_costs += self.convert(fee, ccy)
        attribution.capital_base += self.convert(proposed.initial_margin, ccy)
        return proposed

    def mark_futures(
        self, symbol: str, price: DecimalLike, *, strategy_id: str | None = None
    ) -> None:
        px = _decimal(price, "price")
        if px <= ZERO:
            raise PortfolioError("price must be greater than zero")
        symbol = str(symbol).strip().upper()
        matched = False
        for (item_symbol, sid), position in self.futures.items():
            if item_symbol == symbol and (strategy_id is None or sid == strategy_id):
                position.mark_price = px
                matched = True
        if not matched:
            raise PortfolioError(f"futures position not found: {symbol}")

    def settle_variation_margin(
        self, symbol: str, *, strategy_id: str | None = None
    ) -> Decimal:
        symbol = str(symbol).strip().upper()
        settled_base = ZERO
        matched = False
        for (item_symbol, sid), position in self.futures.items():
            if item_symbol != symbol or (strategy_id is not None and sid != strategy_id):
                continue
            pnl = position.unrealized_pnl
            self.cash[position.currency] += pnl
            self._strategy(sid).realized_pnl += self.convert(pnl, position.currency)
            position.settlement_price = position.mark_price
            settled_base += self.convert(pnl, position.currency)
            matched = True
        if not matched:
            raise PortfolioError(f"futures position not found: {symbol}")
        return settled_base

    def close_futures(
        self,
        symbol: str,
        *,
        strategy_id: str = "unattributed",
        price: DecimalLike | None = None,
        fees: DecimalLike = ZERO,
    ) -> Decimal:
        key = (str(symbol).strip().upper(), strategy_id)
        if key not in self.futures:
            raise PortfolioError(f"futures position not found: {key[0]}")
        position = self.futures[key]
        if price is not None:
            position.mark_price = _decimal(price, "price")
        pnl = self.settle_variation_margin(key[0], strategy_id=strategy_id)
        fee = _decimal(fees, "fees")
        if fee < ZERO:
            raise PortfolioError("fees must not be negative")
        self.cash[position.currency] -= fee
        self._strategy(strategy_id).trading_costs += self.convert(
            fee, position.currency
        )
        del self.futures[key]
        return pnl - self.convert(fee, position.currency)

    def apply_funding(
        self,
        symbol: str,
        rate: DecimalLike,
        *,
        strategy_id: str | None = None,
    ) -> Decimal:
        """Apply perpetual funding; positive rates charge longs and pay shorts."""
        funding_rate = _decimal(rate, "rate")
        symbol = str(symbol).strip().upper()
        total_base = ZERO
        matched = False
        candidates: Iterable[Position | FuturesPosition] = (
            list(self.positions.values()) + list(self.futures.values())
        )
        for position in candidates:
            if position.symbol != symbol or (
                strategy_id is not None and position.strategy_id != strategy_id
            ):
                continue
            mark = position.mark_price
            charge = position.quantity * mark * position.multiplier * funding_rate
            self.cash[position.currency] -= charge
            converted = self.convert(charge, position.currency)
            self._strategy(position.strategy_id).funding_costs += converted
            total_base += converted
            matched = True
        if not matched:
            raise PortfolioError(f"position not found: {symbol}")
        return total_base

    def accrue_borrow_fee(
        self,
        symbol: str,
        rate: DecimalLike,
        *,
        strategy_id: str | None = None,
    ) -> Decimal:
        """Charge borrow only on short cash/crypto positions."""
        borrow_rate = _decimal(rate, "rate")
        if borrow_rate < ZERO:
            raise PortfolioError("borrow rate must not be negative")
        symbol = str(symbol).strip().upper()
        total_base = ZERO
        matched = False
        for position in self.positions.values():
            if position.symbol != symbol or (
                strategy_id is not None and position.strategy_id != strategy_id
            ):
                continue
            matched = True
            if position.quantity >= ZERO:
                continue
            fee = abs(position.quantity) * position.mark_price * position.multiplier * borrow_rate
            self.cash[position.currency] -= fee
            converted = self.convert(fee, position.currency)
            self._strategy(position.strategy_id).borrow_costs += converted
            total_base += converted
        if not matched:
            raise PortfolioError(f"position not found: {symbol}")
        return total_base

    def margin_status(self) -> MarginStatus:
        initial = sum(
            (self.convert(p.initial_margin, p.currency) for p in self.futures.values()),
            ZERO,
        )
        maintenance = sum(
            (
                self.convert(p.maintenance_margin, p.currency)
                for p in self.futures.values()
            ),
            ZERO,
        )
        collateral = self.equity()
        return MarginStatus(
            initial_requirement=initial,
            maintenance_requirement=maintenance,
            collateral=collateral,
            initial_headroom=collateral - initial,
            maintenance_headroom=collateral - maintenance,
            margin_call=collateral < maintenance,
        )

    @property
    def margin_headroom(self) -> Decimal:
        return self.margin_status().initial_headroom

    @property
    def margin_call(self) -> bool:
        return self.margin_status().margin_call

    def equity(self) -> Decimal:
        cash = self.cash_value()
        securities = sum(
            (self.convert(p.market_value, p.currency) for p in self.positions.values()),
            ZERO,
        )
        futures_pnl = sum(
            (self.convert(p.unrealized_pnl, p.currency) for p in self.futures.values()),
            ZERO,
        )
        return cash + securities + futures_pnl

    def strategy_attribution(self, strategy_id: str) -> StrategyAttribution:
        source = self._strategy(strategy_id)
        unrealized = sum(
            (
                self.convert(p.unrealized_pnl, p.currency)
                for p in self.positions.values()
                if p.strategy_id == source.strategy_id
            ),
            ZERO,
        ) + sum(
            (
                self.convert(p.unrealized_pnl, p.currency)
                for p in self.futures.values()
                if p.strategy_id == source.strategy_id
            ),
            ZERO,
        )
        return StrategyAttribution(
            strategy_id=source.strategy_id,
            realized_pnl=source.realized_pnl,
            unrealized_pnl=unrealized,
            trading_costs=source.trading_costs,
            funding_costs=source.funding_costs,
            borrow_costs=source.borrow_costs,
            capital_base=source.capital_base,
        )

    def attribution(self) -> dict[str, StrategyAttribution]:
        strategy_ids = set(self._attribution)
        strategy_ids.update(p.strategy_id for p in self.positions.values())
        strategy_ids.update(p.strategy_id for p in self.futures.values())
        return {sid: self.strategy_attribution(sid) for sid in sorted(strategy_ids)}

    def strategy_net_return(self, strategy_id: str) -> Decimal:
        return self.strategy_attribution(strategy_id).net_return

    def assert_invariants(self) -> None:
        values = list(self.cash.values())
        for position in self.positions.values():
            values.extend(
                [
                    position.quantity,
                    position.average_price,
                    position.mark_price,
                    position.multiplier,
                ]
            )
        for position in self.futures.values():
            values.extend(
                [
                    position.quantity,
                    position.settlement_price,
                    position.mark_price,
                    position.multiplier,
                    position.initial_margin,
                    position.maintenance_margin,
                ]
            )
            if position.maintenance_margin > position.initial_margin:
                raise AssertionError("maintenance margin exceeds initial margin")
        if any(not value.is_finite() for value in values):
            raise AssertionError("ledger contains a non-finite Decimal")


Portfolio = PortfolioLedger

__all__ = [
    "DecimalLike",
    "FuturesPosition",
    "MarginStatus",
    "Portfolio",
    "PortfolioError",
    "PortfolioLedger",
    "Position",
    "StrategyAttribution",
]
