"""Runtime facade joining position attribution, stop policies, and exit arbitration."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Mapping, Sequence

from .protection import (
    ExitArbiter,
    ExitInstruction,
    ExitState,
    PositionSide,
    PositionSlice,
    PositionSliceLedger,
    ProtectivePolicyResolver,
    ProtectiveRule,
    RuleKind,
    StopRegistry,
    StrategyRiskProfile,
)


class ProtectiveLayer:
    """Independent protection watchdog shared by paper, replay, and live paths."""

    def __init__(
        self,
        *,
        ledger: PositionSliceLedger | None = None,
        stops: StopRegistry | None = None,
    ) -> None:
        self.ledger = ledger or PositionSliceLedger()
        self.stops = stops or StopRegistry()
        self.policies = ProtectivePolicyResolver()
        self.arbiter = ExitArbiter(self.ledger)
        self._profiles: dict[str, StrategyRiskProfile] = {}

    def set_target(
        self,
        slice_id: str,
        target_price: Decimal | int | float | str | None,
    ) -> PositionSlice:
        """Attach or clear an optional take-profit / target price for a slice."""
        position = self.ledger.get(slice_id)
        position.target_price = (
            None if target_price in (None, "") else Decimal(str(target_price))
        )
        if position.target_price is not None and position.target_price <= Decimal("0"):
            raise ValueError("target_price must be positive")
        position.revision += 1
        return position

    def open_slice(
        self,
        *,
        slice_id: str,
        strategy_id: str,
        symbol: str,
        side: str,
        quantity: Decimal | int | float | str,
        entry_price: Decimal | int | float | str,
        opened_at: datetime | None = None,
        target_price: Decimal | int | float | str | None = None,
    ) -> PositionSlice:
        position = PositionSlice(
            slice_id=slice_id,
            strategy_id=strategy_id,
            symbol=symbol,
            side=PositionSide.LONG if side.lower() in {"buy", "long"} else PositionSide.SHORT,
            quantity=Decimal(str(quantity)),
            entry_price=Decimal(str(entry_price)),
            opened_at=opened_at or datetime.now(UTC),
            target_price=(
                None if target_price in (None, "") else Decimal(str(target_price))
            ),
        )
        self.ledger.add(position)
        return position

    def protect(
        self,
        slice_id: str,
        profile: StrategyRiskProfile,
        bars: Sequence[Mapping[str, Any] | Sequence[Any]],
    ) -> tuple[Any, ...]:
        position = self.ledger.get(slice_id)
        rules = self.policies.resolve(position, profile, bars)
        self._profiles[slice_id] = profile
        return tuple(self.stops.put(rule) for rule in rules)

    def protect_or_max_loss(
        self,
        slice_id: str,
        profile: StrategyRiskProfile,
        bars: Sequence[Mapping[str, Any] | Sequence[Any]],
    ) -> tuple[Any, ...]:
        """Install full stops when ATR bars exist; otherwise max-loss only."""
        need = int(profile.atr_period) + 1
        if len(bars) >= need:
            try:
                return self.protect(slice_id, profile, bars)
            except ValueError:
                pass
        position = self.ledger.get(slice_id)
        self._profiles[slice_id] = profile
        trigger = (
            position.entry_price * (Decimal("1") - profile.max_loss_fraction)
            if position.side is PositionSide.LONG
            else position.entry_price * (Decimal("1") + profile.max_loss_fraction)
        )
        rule = ProtectiveRule(
            f"{position.slice_id}:stop",
            position.slice_id,
            RuleKind.INITIAL_STOP,
            position.side,
            trigger,
            reason=f"{profile.archetype.value}_max_loss_fallback",
        )
        return (self.stops.put(rule),)

    def request_strategy_exit(
        self, slice_id: str, *, order_id: str, reason: str = "strategy_exit"
    ) -> ExitInstruction | None:
        return self.arbiter.request_exit(
            slice_id, order_id=order_id, reason=reason
        )

    def on_bar(
        self,
        *,
        symbol: str,
        open_price: Decimal | int | float | str,
        high: Decimal | int | float | str,
        low: Decimal | int | float | str,
        close: Decimal | int | float | str,
        now: datetime | None = None,
        atr: Decimal | int | float | str | None = None,
    ) -> tuple[ExitInstruction, ...]:
        """Return at most one reduce-only exit per slice, even if rules race."""
        instructions: list[ExitInstruction] = []
        for position in self.ledger.all():
            if position.symbol != symbol or position.state is not ExitState.OPEN:
                continue
            position.observe(Decimal(str(high)), Decimal(str(low)))
            profile = self._profiles.get(position.slice_id)
            if profile is not None and atr is not None:
                previous = next(
                    (
                        rule
                        for rule in self.stops.for_slice(position.slice_id)
                        if rule.rule_id.endswith(":chandelier")
                    ),
                    None,
                )
                self.stops.put(
                    self.policies.chandelier(
                        position, profile, Decimal(str(atr)), previous=previous
                    )
                )
            trigger = self.stops.evaluate(
                position.slice_id,
                open_price=open_price,
                high=high,
                low=low,
                close=close,
                now=now or datetime.now(UTC),
            )
            if trigger is None:
                continue
            order_id = f"protect:{position.slice_id}:{position.revision + 1}"
            instruction = self.arbiter.request_exit(
                position.slice_id,
                order_id=order_id,
                reason=trigger.reason,
                expected_revision=position.revision,
            )
            if instruction is not None:
                instructions.append(instruction)
        return tuple(instructions)

    def apply_exit_fill(
        self,
        slice_id: str,
        *,
        order_id: str,
        quantity: Decimal | int | float | str,
        price: Decimal | int | float | str,
    ) -> PositionSlice:
        position = self.arbiter.apply_fill(
            slice_id,
            order_id=order_id,
            fill_quantity=quantity,
            fill_price=price,
        )
        if position.state is ExitState.CLOSED:
            self.stops.remove_slice(slice_id)
        return position

    def cancel_exit(self, slice_id: str, *, order_id: str) -> PositionSlice:
        """Release a reservation after broker rejection/cancel before any fill."""
        position = self.ledger.get(slice_id)
        if (
            position.state is ExitState.EXIT_PENDING
            and position.exit_order_id == order_id
            and position.exited_quantity == Decimal("0")
        ):
            position.state = ExitState.OPEN
            position.exit_order_id = None
            position.exit_reason = None
            position.exit_requested_at = None
            position.revision += 1
        return position

    def release_stale_exits(
        self,
        *,
        timeout_sec: float = 30.0,
        now: datetime | None = None,
    ) -> list[str]:
        """Re-open EXIT_PENDING slices that never filled within ``timeout_sec``.

        Returns slice_ids that were released so the caller can cancel broker orders.
        """
        current = now or datetime.now(UTC)
        released: list[str] = []
        limit = max(1.0, float(timeout_sec))
        for position in self.ledger.all():
            if position.state is not ExitState.EXIT_PENDING:
                continue
            if position.exited_quantity > Decimal("0"):
                continue
            started = position.exit_requested_at or position.opened_at
            age = (current - started).total_seconds()
            if age < limit:
                continue
            order_id = position.exit_order_id
            if not order_id:
                continue
            self.cancel_exit(position.slice_id, order_id=order_id)
            released.append(position.slice_id)
        return released

    def snapshot(self) -> dict[str, Any]:
        return {
            "ledger": self.ledger.to_dict(),
            "stops": self.stops.to_dict(),
            "profiles": {
                key: profile.to_dict() for key, profile in sorted(self._profiles.items())
            },
        }

    def has_protection(self, symbol: str) -> bool:
        """True when at least one live protective slice exists for *symbol*."""
        target = str(symbol or "").strip()
        if not target:
            return False
        tail = target.split(":")[-1]
        for slice_ in self.ledger.all():
            if getattr(slice_, "state", None) is ExitState.CLOSED:
                continue
            if float(getattr(slice_, "remaining_quantity", 0.0) or 0.0) <= 0:
                continue
            sym = str(getattr(slice_, "symbol", "") or "")
            if sym == target or sym.split(":")[-1] == tail:
                return True
        return False

    @classmethod
    def recover(cls, data: Mapping[str, Any]) -> "ProtectiveLayer":
        layer = cls(
            ledger=PositionSliceLedger.from_dict(data.get("ledger") or {}),
            stops=StopRegistry.from_dict(data.get("stops") or {}),
        )
        layer._profiles = {
            str(key): StrategyRiskProfile.from_dict(value)
            for key, value in (data.get("profiles") or {}).items()
        }
        return layer
