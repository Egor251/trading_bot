"""Protective EXIT_PENDING must not stick forever after reject/timeout."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.order import OrderStatus
from engine.protection import ExitState, StrategyRiskProfile
from engine.runtime import TradingRuntime


def _bars() -> list[dict[str, str]]:
    return [
        {"high": str(100 + i), "low": str(98 + i), "close": str(99 + i)}
        for i in range(16)
    ]


def test_protective_reject_releases_exit_pending() -> None:
    runtime = TradingRuntime.from_config(
        {
            "mode": "paper",
            "equity": 10_000,
            "risk": {"max_position_pct": 1.0, "max_leverage": 2.0},
        }
    )
    runtime.submit_signal(
        ticker="TEST",
        action="BUY",
        quantity=2,
        strategy_id="trend",
        mark_price=110,
    )
    position = runtime.protection.ledger.all()[0]
    runtime.protection.protect(
        position.slice_id,
        StrategyRiskProfile.for_archetype("trend"),
        _bars(),
    )
    stop = next(
        rule
        for rule in runtime.protection.stops.for_slice(position.slice_id)
        if rule.trigger_price is not None
    )
    instructions = runtime.protective_instructions(
        symbol="TEST",
        open_price=float(stop.trigger_price - Decimal("2")),
        high=float(stop.trigger_price),
        low=float(stop.trigger_price - Decimal("3")),
        close=float(stop.trigger_price - Decimal("2")),
    )
    assert instructions
    # Force reject by zeroing mark after reservation exists — submit with bad price.
    # Reservation was already created by protective_instructions; execute with mark 0
    # is rejected by build_intent as no_mark_price / mark_price<=0 path.
    result = runtime.execute_protective_exit(instructions[0], mark_price=0.0)
    assert result.status == OrderStatus.REJECTED
    assert runtime.protection.ledger.get(position.slice_id).state is ExitState.OPEN


def test_stale_exit_pending_released_on_timeout() -> None:
    runtime = TradingRuntime.from_config(
        {
            "mode": "paper",
            "equity": 10_000,
            "exit_pending_timeout_sec": 1.0,
            "risk": {"max_position_pct": 1.0, "max_leverage": 2.0},
        }
    )
    runtime.submit_signal(
        ticker="TEST",
        action="BUY",
        quantity=1,
        strategy_id="trend",
        mark_price=100,
    )
    position = runtime.protection.ledger.all()[0]
    instruction = runtime.protection.request_strategy_exit(
        position.slice_id, order_id="stuck-exit", reason="manual"
    )
    assert instruction is not None
    assert position.state is ExitState.EXIT_PENDING
    position.exit_requested_at = datetime.now(UTC) - timedelta(seconds=5)
    released = runtime.release_stale_protective_exits()
    assert position.slice_id in released
    assert runtime.protection.ledger.get(position.slice_id).state is ExitState.OPEN
