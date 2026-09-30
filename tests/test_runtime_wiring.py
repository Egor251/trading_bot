"""Integration tests for live-path wiring of foundation modules."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

from engine.candle_store import StoredBar
from engine.costs import CostComponent, CostKind, CostMatch, CostRule, CostSchedule
from engine.order import OrderStatus
from engine.protection import ExitState
from engine.runtime import TradingRuntime


def test_open_fill_auto_installs_max_loss_protection() -> None:
    runtime = TradingRuntime.from_config(
        {
            "mode": "paper",
            "default_risk_archetype": "trend",
            "risk": {"max_position_pct": 1, "max_leverage": 2},
        }
    )
    result = runtime.submit_signal(
        ticker="TEST",
        action="BUY",
        quantity=2,
        strategy_id="trend",
        mark_price=100,
    )
    assert result.status is OrderStatus.FILLED
    position = runtime.protection.ledger.all()[0]
    stops = runtime.protection.stops.for_slice(position.slice_id)
    assert stops
    assert position.state is ExitState.OPEN
    assert any(rule.trigger_price is not None for rule in stops)


def test_process_bar_executes_protective_exit() -> None:
    runtime = TradingRuntime.from_config(
        {
            "mode": "paper",
            "default_risk_archetype": "mean_reversion",
            "risk": {"max_position_pct": 1, "max_leverage": 2},
        }
    )
    runtime.submit_signal(
        ticker="TEST",
        action="BUY",
        quantity=2,
        strategy_id="mean",
        mark_price=100,
    )
    position = runtime.protection.ledger.all()[0]
    stop = next(
        rule
        for rule in runtime.protection.stops.for_slice(position.slice_id)
        if rule.trigger_price is not None
    )
    trigger = float(stop.trigger_price)
    exits = runtime.process_bar(
        StoredBar(
            symbol="TEST",
            class_code="",
            ts=None,
            open=trigger - 1,
            high=trigger,
            low=trigger - 2,
            close=trigger - 1,
            volume=1,
            key="TEST",
        )
    )
    assert exits
    assert runtime.orders.positions["TEST"].quantity == 0
    assert runtime.protection.ledger.get(position.slice_id).state is ExitState.CLOSED


def test_live_rejects_without_preflight() -> None:
    async def scenario() -> None:
        runtime = TradingRuntime.from_config(
            {
                "mode": "live",
                "account_by_venue": {"binance": "default"},
                "venue_by_class": {"CRYPTO": "binance"},
                "risk": {"max_position_pct": 1, "max_leverage": 2},
            }
        )
        result = await runtime.submit_signal_async(
            ticker="BTC/USDT",
            class_code="CRYPTO",
            action="BUY",
            quantity=1,
            mark_price=100,
        )
        assert result.status is OrderStatus.REJECTED
        assert result.reason == "live_preflight_required"

    asyncio.run(scenario())


def test_cost_store_and_instruments_wire_into_order_manager(tmp_path: Path) -> None:
    schedule = CostSchedule(
        version="wired-v1",
        rules=(
            CostRule(
                rule_id="fee",
                match=CostMatch(),
                components=(
                    CostComponent(CostKind.BPS, Decimal("10"), name="fee"),
                ),
            ),
        ),
    )
    store_root = tmp_path / "costs"
    store_root.mkdir()
    # Persist via OrderManager path by injecting schedule directly through config dict.
    runtime = TradingRuntime.from_config(
        {
            "mode": "paper",
            "cost_schedule": schedule,
            "instruments": [
                {
                    "venue": "BINANCE",
                    "asset_class": "crypto",
                    "symbol": "BTC/USDT",
                    "currency": "USDT",
                    "class_code": "CRYPTO",
                    "lot_size": "0.001",
                    "tick_size": "0.1",
                }
            ],
            "risk": {"max_position_pct": 1, "max_leverage": 2},
        }
    )
    assert runtime.orders.cost_schedule is not None
    assert runtime.orders.cost_schedule.version == "wired-v1"
    assert runtime.orders.instrument_registry is not None
    spec = runtime.orders.instrument_registry.resolve(
        "BTC/USDT", class_code="CRYPTO"
    )
    assert spec.currency == "USDT"
    runtime.submit_signal(
        ticker="BTC/USDT",
        class_code="CRYPTO",
        action="BUY",
        quantity=1,
        mark_price=100,
    )
    assert runtime.orders.fill_ledger
    assert runtime.orders.fill_ledger[0].total_cost > 0


def test_open_orders_and_recent_trades_in_snapshot() -> None:
    from decimal import Decimal
    from pathlib import Path
    import tempfile

    from engine.execution import (
        BrokerOrder,
        ExecutionRouter,
        OrderSide as BrokerSide,
        OrderState,
        OrderStore,
    )

    with tempfile.TemporaryDirectory() as tmp:
        store = OrderStore(Path(tmp) / "orders.json")
        router = ExecutionRouter(store)
        runtime = TradingRuntime.from_config(
            {
                "mode": "paper",
                "execution_router": router,
                "risk": {"max_position_pct": 1, "max_leverage": 2},
            }
        )
        runtime.submit_signal(
            ticker="TEST", action="BUY", quantity=1, mark_price=100, strategy_id="s1"
        )
        trades = runtime.recent_trades()
        assert trades
        assert trades[-1]["symbol"] == "TEST"
        assert float(trades[-1]["price"]) == 100

        order = BrokerOrder(
            symbol="TEST",
            side=BrokerSide.SELL,
            quantity=Decimal("1"),
            venue="paper",
            account="default",
            client_id="ord-open-1",
            order_type="limit",
            limit_price=Decimal("110"),
            state=OrderState.ACCEPTED,
            raw={"class_code": "", "strategy_id": "s1"},
        )
        store.save(order)
        opens = runtime.open_orders()
        assert any(row["client_id"] == "ord-open-1" for row in opens)
        snap = runtime.snapshot()
        assert snap["recent_trades"]
        assert any(row["client_id"] == "ord-open-1" for row in snap["open_orders"])


def test_open_positions_include_insurance_and_optional_target() -> None:
    runtime = TradingRuntime.from_config(
        {
            "mode": "paper",
            "default_risk_archetype": "trend",
            "risk": {"max_position_pct": 1, "max_leverage": 2},
        }
    )
    runtime.submit_signal(
        ticker="TEST",
        action="BUY",
        quantity=2,
        strategy_id="trend",
        mark_price=100,
    )
    position = runtime.protection.ledger.all()[0]
    runtime.protection.set_target(position.slice_id, "112")
    rows = runtime.open_positions()
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "TEST"
    assert row["side"] == "long"
    assert row["target"]["price"] == "112"
    assert row["insurance"] is not None
    assert float(row["insurance"]["price"]) < 100
    snap = runtime.snapshot()
    assert snap["open_positions"][0]["insurance"]["price"] == row["insurance"]["price"]


def test_set_cost_schedule_updates_future_fills() -> None:
    runtime = TradingRuntime.from_config(
        {
            "mode": "paper",
            "legacy_fee_bps": 0,
            "risk": {"max_position_pct": 1, "max_leverage": 2},
        }
    )
    runtime.submit_signal(ticker="A", action="BUY", quantity=1, mark_price=100)
    before = runtime.orders.total_costs
    runtime.set_cost_schedule(
        CostSchedule(
            version="later",
            rules=(
                CostRule(
                    rule_id="fee",
                    match=CostMatch(),
                    components=(
                        CostComponent(CostKind.FIXED, Decimal("5"), name="fixed"),
                    ),
                ),
            ),
        )
    )
    runtime.submit_signal(ticker="A", action="SELL", quantity=1, mark_price=100)
    assert runtime.orders.total_costs == before + Decimal("5")
