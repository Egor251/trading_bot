from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import sys
from types import ModuleType

import pytest

# The repository's engine package initializer eagerly imports optional plugin
# trees.  Load this isolated subsystem without requiring those integrations.
if "engine" not in sys.modules:
    engine_package = ModuleType("engine")
    engine_package.__path__ = [str(Path(__file__).resolve().parents[1] / "engine")]
    sys.modules["engine"] = engine_package

from engine.costs import (  # noqa: E402
    CostBreakdown,
    CostComponent,
    CostKind,
    CostMatch,
    CostRule,
    CostSchedule,
    TradeContext,
)
from engine.fills import FillRecord  # noqa: E402


D = Decimal
NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)


def context(**overrides: object) -> TradeContext:
    values: dict[str, object] = {
        "venue": "XNAS",
        "account": "main",
        "asset": "AAPL",
        "asset_class": "equity",
        "symbol": "AAPL",
        "side": "buy",
        "position_effect": "open",
        "order_type": "limit",
        "liquidity": "maker",
        "trade_time": NOW,
        "quantity": D("100"),
        "price": D("25"),
        "currency": "USD",
    }
    values.update(overrides)
    return TradeContext(**values)


def schedule(*rules: CostRule) -> CostSchedule:
    return CostSchedule(version="2026.07", rules=rules)


def rule(
    rule_id: str,
    component: CostComponent,
    *,
    match: CostMatch | None = None,
    priority: int = 0,
) -> CostRule:
    return CostRule(
        rule_id=rule_id,
        match=match or CostMatch(),
        components=(component,),
        priority=priority,
    )


@pytest.mark.parametrize(
    ("side", "expected_cash"),
    [("buy", D("-2503.5")), ("sell", D("2496.5"))],
)
def test_buy_sell_golden_cash_delta(side: str, expected_cash: Decimal) -> None:
    costs = schedule(
        rule("commission", CostComponent("bps", D("10"))),
        rule("ticket", CostComponent("fixed", D("1")), priority=10),
    ).calculate(context(side=side))

    fill = FillRecord.from_trade(
        context(side=side),
        fill_id=f"fill-{side}",
        order_id="order-1",
        costs=costs,
    )

    assert fill.gross_notional == D("2500")
    assert fill.total_cost == D("3.5")
    assert fill.cash_delta == expected_cash
    assert [line.rule_id for line in costs.lines] == ["commission", "ticket"]


def test_maker_taker_symbol_and_full_context_matching() -> None:
    costs = schedule(
        rule(
            "maker",
            CostComponent("bps", D("1")),
            match=CostMatch(
                venue="xnas",
                account="MAIN",
                asset="aapl",
                asset_class="EQUITY",
                symbol="AA*",
                side="BUY",
                position_effect="OPEN",
                order_type="LIMIT",
                liquidity="maker",
            ),
        ),
        rule(
            "taker",
            CostComponent("bps", D("3")),
            match=CostMatch(symbol="AA*", liquidity="taker"),
        ),
    )

    assert costs.calculate(context(liquidity="maker")).total == D("0.25")
    assert costs.calculate(context(liquidity="taker")).total == D("0.75")
    assert costs.calculate(context(symbol="MSFT", asset="MSFT")).total == D("0")


@pytest.mark.parametrize(
    ("tier_value", "expected"),
    [
        (D("0"), D("2.5")),
        (D("999.999"), D("2.5")),
        (D("1000"), D("1.25")),
        (D("1000000"), D("1.25")),
    ],
)
def test_tiers_use_half_open_boundaries(tier_value: Decimal, expected: Decimal) -> None:
    costs = schedule(
        rule(
            "tier-1",
            CostComponent("bps", D("10")),
            match=CostMatch(tier_min=D("0"), tier_max=D("1000")),
        ),
        rule(
            "tier-2",
            CostComponent("bps", D("5")),
            match=CostMatch(tier_min=D("1000")),
        ),
    )
    breakdown = costs.calculate(context(tier_value=tier_value))

    assert breakdown.total == expected
    assert len(breakdown.lines) == 1


def test_component_minimum_and_maximum_are_clamped() -> None:
    component = CostComponent(
        CostKind.BPS,
        D("100"),
        minimum=D("2"),
        maximum=D("5"),
    )
    costs = schedule(rule("clamped", component))

    assert costs.calculate(context(quantity=D("1"), price=D("10"))).total == D("2")
    assert costs.calculate(context(quantity=D("10"), price=D("30"))).total == D("3")
    assert costs.calculate(context(quantity=D("100"), price=D("10"))).total == D("5")


def test_futures_multiplier_and_per_contract_cost() -> None:
    trade = context(
        asset="ESU6",
        asset_class="future",
        symbol="ESU6",
        quantity=D("2"),
        price=D("100"),
        contract_multiplier=D("50"),
    )
    costs = schedule(
        rule("exchange", CostComponent("per_contract", D("1.25"))),
        rule("regulatory", CostComponent("bps", D("10"))),
    ).calculate(trade)
    fill = FillRecord.from_trade(
        trade,
        fill_id="future-fill",
        order_id="future-order",
        costs=costs,
    )

    assert fill.gross_notional == D("10000")
    assert costs.total == D("12.50")
    assert fill.cash_delta == D("-10012.50")


def test_effective_dates_are_half_open_for_rule_and_schedule() -> None:
    start = NOW
    end = NOW + timedelta(days=1)
    costs = CostSchedule(
        version="dated",
        effective_from=start,
        effective_to=end,
        rules=(
            rule(
                "old",
                CostComponent("fixed", D("1")),
                match=CostMatch(effective_to=start),
            ),
            rule(
                "current",
                CostComponent("fixed", D("2")),
                match=CostMatch(effective_from=start, effective_to=end),
            ),
        ),
    )

    assert costs.calculate(context(trade_time=start)).total == D("2")
    assert costs.calculate(context(trade_time=end - timedelta(microseconds=1))).total == D("2")
    with pytest.raises(ValueError, match="not effective"):
        costs.calculate(context(trade_time=end))


def test_all_cost_kinds_have_explicit_deterministic_arithmetic() -> None:
    rates = {
        CostKind.BPS: D("10"),
        CostKind.FIXED: D("4"),
        CostKind.PER_UNIT: D("0.2"),
        CostKind.PER_CONTRACT: D("0.3"),
        CostKind.SPREAD: D("2"),
        CostKind.SLIPPAGE: D("3"),
        CostKind.FUNDING: D("4"),
        CostKind.BORROW: D("5"),
        CostKind.TAX: D("6"),
    }
    rules = tuple(
        rule(kind.value, CostComponent(kind, rate), priority=index)
        for index, (kind, rate) in enumerate(rates.items())
    )

    result = schedule(*reversed(rules)).calculate(context(quantity=D("10"), price=D("100")))

    assert [line.kind for line in result.lines] == list(rates)
    assert result.total == D("1") + D("4") + D("2") + D("3") + D("0.2") + D("0.3") + D("0.4") + D("0.5") + D("0.6")


def test_schedule_serialization_round_trip_and_hash_are_stable() -> None:
    first = rule("z-last", CostComponent("fixed", D("1.00")), priority=20)
    second = rule("a-first", CostComponent("bps", D("2.500")), priority=10)
    left = schedule(first, second)
    right = schedule(
        rule("a-first", CostComponent("bps", D("2.5000")), priority=10),
        rule("z-last", CostComponent("fixed", D("1.0000")), priority=20),
    )

    assert left.schedule_hash == right.schedule_hash
    assert len(left.schedule_hash) == 64
    restored = CostSchedule.from_json(left.to_json())
    assert restored == right
    assert restored.schedule_hash == left.schedule_hash
    assert restored.calculate(context()).to_dict() == left.calculate(context()).to_dict()


def test_fill_serialization_round_trip_preserves_explicit_costs() -> None:
    trade = context()
    costs = schedule(rule("fee", CostComponent("fixed", D("1.20")))).calculate(trade)
    fill = FillRecord.from_trade(
        trade,
        fill_id="fill-1",
        order_id="order-1",
        costs=costs,
    )

    restored = FillRecord.from_dict(fill.to_dict())
    assert restored == fill
    assert restored.costs.schedule_hash == costs.schedule_hash

    tampered = fill.to_dict()
    tampered["cash_delta"] = "0"
    with pytest.raises(ValueError, match="cash_delta"):
        FillRecord.from_dict(tampered)


def test_models_are_immutable_and_decimal_inputs_are_strict() -> None:
    trade = context()
    with pytest.raises(FrozenInstanceError):
        trade.quantity = D("2")  # type: ignore[misc]
    with pytest.raises(TypeError, match="quantity"):
        context(quantity=1.5)
    with pytest.raises(ValueError, match="side"):
        context(side="hold")
    with pytest.raises(ValueError, match="timezone-aware"):
        context(trade_time=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="unsupported"):
        CostComponent("mystery", D("1"))
    with pytest.raises(TypeError, match="CostBreakdown"):
        FillRecord(
            fill_id="x",
            order_id="y",
            venue="XNAS",
            account="main",
            symbol="AAPL",
            side="buy",
            quantity=D("1"),
            price=D("1"),
            trade_time=NOW,
            costs=None,  # type: ignore[arg-type]
        )


def test_empty_cost_breakdown_must_be_explicit() -> None:
    fill = FillRecord.from_trade(
        context(),
        fill_id="free-fill",
        order_id="free-order",
        costs=CostBreakdown(),
    )
    assert fill.total_cost == D("0")
    assert fill.cash_delta == -fill.gross_notional
