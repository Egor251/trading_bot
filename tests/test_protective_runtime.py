from datetime import UTC, datetime
from decimal import Decimal

from engine.protection import ExitState, StrategyRiskProfile
from engine.protective_runtime import ProtectiveLayer
from engine.runtime import TradingRuntime


def _bars() -> list[dict[str, str]]:
    return [
        {"high": str(100 + i), "low": str(98 + i), "close": str(99 + i)}
        for i in range(16)
    ]


def test_watchdog_and_strategy_race_emit_one_exit() -> None:
    layer = ProtectiveLayer()
    position = layer.open_slice(
        slice_id="s1",
        strategy_id="trend",
        symbol="TEST",
        side="buy",
        quantity="2",
        entry_price="110",
        opened_at=datetime.now(UTC),
    )
    layer.protect(
        "s1",
        StrategyRiskProfile.for_archetype("trend"),
        _bars(),
    )
    stop = next(rule for rule in layer.stops.for_slice("s1") if rule.trigger_price)
    exits = layer.on_bar(
        symbol="TEST",
        open_price=stop.trigger_price - Decimal("2"),
        high=stop.trigger_price,
        low=stop.trigger_price - Decimal("3"),
        close=stop.trigger_price - Decimal("2"),
    )
    assert len(exits) == 1
    assert exits[0].side == "sell"
    assert layer.request_strategy_exit(
        "s1", order_id="strategy-order"
    ) is None
    assert position.state is ExitState.EXIT_PENDING


def test_partial_exit_and_restart_recovery() -> None:
    layer = ProtectiveLayer()
    layer.open_slice(
        slice_id="s1",
        strategy_id="mean",
        symbol="TEST",
        side="sell",
        quantity="3",
        entry_price="100",
    )
    instruction = layer.request_strategy_exit("s1", order_id="exit-1")
    assert instruction is not None
    layer.apply_exit_fill(
        "s1", order_id="exit-1", quantity="1", price="99"
    )
    recovered = ProtectiveLayer.recover(layer.snapshot())
    position = recovered.ledger.get("s1")
    assert position.remaining_quantity == Decimal("2")
    assert position.state is ExitState.EXIT_PENDING
    recovered.apply_exit_fill(
        "s1", order_id="exit-1", quantity="2", price="98"
    )
    assert recovered.ledger.get("s1").state is ExitState.CLOSED


def test_runtime_protective_exit_is_reduce_only_and_attributed() -> None:
    runtime = TradingRuntime.from_config(
        {
            "mode": "paper",
            "risk": {"max_position_pct": 1, "max_leverage": 2},
        }
    )
    opened = runtime.submit_signal(
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
    assert opened.filled_qty == 2
    closed = runtime.execute_protective_exit(
        instructions[0], mark_price=float(stop.trigger_price - Decimal("2"))
    )
    assert closed.filled_qty == 2
    assert runtime.orders.positions["TEST"].quantity == 0
    assert runtime.protection.ledger.get(position.slice_id).state is ExitState.CLOSED
    assert runtime.protective_instructions(
        symbol="TEST", open_price=1, high=1, low=1, close=1
    ) == ()
