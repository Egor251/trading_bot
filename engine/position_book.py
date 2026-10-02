"""Cross-strategy position visibility and net-exposure policy (docs/todo2.md §3).

Approach B (recommended): strategies see own slice + symbol net; flips that would
reverse net without ``close_and_reverse`` are rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping

from .protection import ExitState, PositionSide, PositionSlice, PositionSliceLedger


ZERO = Decimal("0")


def _dec(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass(frozen=True, slots=True)
class ExposureView:
    symbol: str
    strategy_id: str
    strategy_qty: Decimal  # signed
    net_qty: Decimal  # signed aggregate across strategies
    slices: tuple[PositionSlice, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "strategy_id": self.strategy_id,
            "strategy_qty": str(self.strategy_qty),
            "net_qty": str(self.net_qty),
            "slice_ids": [s.slice_id for s in self.slices],
        }


@dataclass(frozen=True, slots=True)
class IntentDecision:
    allowed: bool
    reason: str = ""
    adjusted_qty: Decimal | None = None
    would_flip: bool = False


def _signed_remaining(slice_: PositionSlice) -> Decimal:
    if slice_.state is ExitState.CLOSED:
        return ZERO
    qty = slice_.remaining_quantity or ZERO
    return qty if slice_.side is PositionSide.LONG else -qty


class PositionBook:
    """Read-model over ``PositionSliceLedger`` for consilium visibility."""

    def __init__(
        self,
        ledger: PositionSliceLedger,
        *,
        forbid_net_flip: bool = True,
    ) -> None:
        self.ledger = ledger
        self.forbid_net_flip = bool(forbid_net_flip)

    def open_slices(self) -> tuple[PositionSlice, ...]:
        return tuple(
            s
            for s in self.ledger.all()
            if s.state is not ExitState.CLOSED and (s.remaining_quantity or ZERO) > ZERO
        )

    def slices_for(
        self, *, symbol: str | None = None, strategy_id: str | None = None
    ) -> tuple[PositionSlice, ...]:
        out = []
        for s in self.open_slices():
            if symbol is not None and s.symbol != symbol:
                continue
            if strategy_id is not None and s.strategy_id != strategy_id:
                continue
            out.append(s)
        return tuple(out)

    def net_qty(self, symbol: str) -> Decimal:
        return sum((_signed_remaining(s) for s in self.slices_for(symbol=symbol)), ZERO)

    def strategy_qty(self, strategy_id: str, symbol: str) -> Decimal:
        return sum(
            (
                _signed_remaining(s)
                for s in self.slices_for(symbol=symbol, strategy_id=strategy_id)
            ),
            ZERO,
        )

    def view(self, strategy_id: str, symbol: str) -> ExposureView:
        slices = self.slices_for(symbol=symbol)
        return ExposureView(
            symbol=symbol,
            strategy_id=strategy_id,
            strategy_qty=self.strategy_qty(strategy_id, symbol),
            net_qty=self.net_qty(symbol),
            slices=slices,
        )

    def evaluate_intent(
        self,
        *,
        strategy_id: str,
        symbol: str,
        side: str,
        quantity: Decimal | float | int | str,
        close_and_reverse: bool = False,
        reduce_only: bool = False,
    ) -> IntentDecision:
        """Approach B + optional ban on net flip without close_and_reverse."""
        qty = abs(_dec(quantity))
        if qty <= ZERO:
            return IntentDecision(False, reason="qty_non_positive")
        side_l = str(side or "").lower()
        if side_l in {"buy", "long"}:
            signed = qty
        elif side_l in {"sell", "short"}:
            signed = -qty
        else:
            return IntentDecision(False, reason="side_invalid")

        net = self.net_qty(symbol)
        # Reduce-only is a *strategy-scoped* operation. Using the cross-strategy
        # net made protective exits structurally unable to fire in a hedged book
        # (strategy A long 1, strategy B short 1 -> net == 0 -> every exit
        # rejected "reduce_only_flat" while real broker exposure stayed open),
        # and let one strategy close another strategy's slice.
        own = self.strategy_qty(strategy_id, symbol)
        if reduce_only:
            # Fall back to the cross-strategy net only when this strategy has no
            # slice at all — e.g. an operator-driven flatten of the whole book.
            reference = own if own != ZERO else net
            if reference == ZERO:
                return IntentDecision(False, reason="reduce_only_flat")
            # Must move toward flat.
            if reference > ZERO and signed >= ZERO:
                return IntentDecision(False, reason="reduce_only_wrong_side")
            if reference < ZERO and signed <= ZERO:
                return IntentDecision(False, reason="reduce_only_wrong_side")
            max_close = abs(reference)
            if qty > max_close:
                return IntentDecision(
                    True,
                    reason="qty_capped_to_net",
                    adjusted_qty=max_close,
                )
            return IntentDecision(True, adjusted_qty=qty)

        projected = net + signed
        would_flip = net != ZERO and (
            (net > ZERO and projected < ZERO) or (net < ZERO and projected > ZERO)
        )
        if would_flip and self.forbid_net_flip and not close_and_reverse:
            return IntentDecision(
                False,
                reason="net_flip_forbidden",
                would_flip=True,
            )
        return IntentDecision(True, adjusted_qty=qty, would_flip=would_flip)

    def snapshot(self) -> dict[str, Any]:
        by_symbol: dict[str, dict[str, Any]] = {}
        for s in self.open_slices():
            bucket = by_symbol.setdefault(
                s.symbol,
                {"net_qty": "0", "by_strategy": {}, "slices": []},
            )
            signed = _signed_remaining(s)
            bucket["slices"].append(s.to_dict())
            strat = bucket["by_strategy"].setdefault(s.strategy_id, "0")
            bucket["by_strategy"][s.strategy_id] = str(_dec(strat) + signed)
            bucket["net_qty"] = str(_dec(bucket["net_qty"]) + signed)
        return {"forbid_net_flip": self.forbid_net_flip, "symbols": by_symbol}


def book_from_mapping(rows: Iterable[Mapping[str, Any]], **kwargs: Any) -> PositionBook:
    ledger = PositionSliceLedger(PositionSlice.from_dict(dict(row)) for row in rows)
    return PositionBook(ledger, **kwargs)
