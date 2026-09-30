"""Tests for the self-contained position protection engine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.protection import (  # noqa: E402
    ExitArbiter,
    ExitState,
    PositionSide,
    PositionSlice,
    PositionSliceLedger,
    ProtectivePolicyResolver,
    ProtectiveRule,
    RuleKind,
    StopRegistry,
    StrategyArchetype,
    StrategyRiskProfile,
)


D = Decimal
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _position(side: PositionSide = PositionSide.LONG) -> PositionSlice:
    return PositionSlice(
        slice_id=f"slice-{side.value}",
        strategy_id="strategy-1",
        symbol="TEST",
        side=side,
        entry_price=D("100"),
        quantity=D("10"),
        opened_at=NOW,
    )


def _bars(count: int = 16) -> list[dict[str, Decimal]]:
    return [
        {
            "open": D("100"),
            "high": D("102"),
            "low": D("98"),
            "close": D("100"),
        }
        for _ in range(count)
    ]


def test_wilder_atr_uses_recursive_smoothing() -> None:
    highs = [D("10"), D("12"), D("13"), D("15"), D("14")]
    lows = [D("8"), D("9"), D("10"), D("11"), D("10")]
    closes = [D("9"), D("11"), D("12"), D("12"), D("11")]

    atr = ProtectivePolicyResolver.wilder_atr(highs, lows, closes, period=3)

    # Initial TR average = (3 + 3 + 4) / 3; final TR=4 is Wilder-smoothed.
    assert atr == D("3.555555555555555555555555557")


@pytest.mark.parametrize(
    ("side", "expected"),
    [(PositionSide.LONG, D("98.00")), (PositionSide.SHORT, D("102.00"))],
)
def test_max_loss_caps_initial_stop_for_long_and_short(
    side: PositionSide, expected: Decimal
) -> None:
    profile = StrategyRiskProfile(
        archetype=StrategyArchetype.TREND,
        atr_period=3,
        initial_atr_multiple=D("4"),
        max_loss_fraction=D("0.02"),
        structure_lookback=3,
        structure_buffer_atr=D("0"),
    )

    rules = ProtectivePolicyResolver().resolve(_position(side), profile, _bars(5))

    assert rules[0].trigger_price == expected
    assert rules[0].kind is RuleKind.INITIAL_STOP


def test_structure_can_tighten_long_and_short_initial_stops() -> None:
    bars = _bars(5)
    bars[-1]["low"] = D("99")
    bars[-1]["high"] = D("101")
    profile = StrategyRiskProfile(
        archetype="trend",
        atr_period=3,
        initial_atr_multiple=D("5"),
        max_loss_fraction=D("0.20"),
        structure_lookback=1,
        structure_buffer_atr=D("0"),
    )
    resolver = ProtectivePolicyResolver()

    assert resolver.resolve(_position(PositionSide.LONG), profile, bars)[
        0
    ].trigger_price == D("99")
    assert resolver.resolve(_position(PositionSide.SHORT), profile, bars)[
        0
    ].trigger_price == D("101")


def test_archetype_defaults_are_distinct_and_mean_reversion_has_time_stop() -> None:
    trend = StrategyRiskProfile.for_archetype("trend")
    mean = StrategyRiskProfile.for_archetype("mean_reversion")
    breakout = StrategyRiskProfile.for_archetype("breakout")

    assert mean.initial_atr_multiple < trend.initial_atr_multiple
    assert breakout.initial_atr_multiple > trend.initial_atr_multiple
    assert mean.max_holding_period == timedelta(days=5)
    rules = ProtectivePolicyResolver().resolve(
        _position(), mean, _bars(mean.atr_period + 1)
    )
    assert [rule.kind for rule in rules] == [RuleKind.INITIAL_STOP, RuleKind.TIME]


@pytest.mark.parametrize(
    ("side", "opening", "high", "low", "expected", "is_gap"),
    [
        (PositionSide.LONG, "95", "99", "94", "95", True),
        (PositionSide.LONG, "101", "102", "97", "98", False),
        (PositionSide.SHORT, "105", "106", "101", "105", True),
        (PositionSide.SHORT, "99", "103", "97", "102", False),
    ],
)
def test_price_trigger_is_gap_aware(
    side: PositionSide,
    opening: str,
    high: str,
    low: str,
    expected: str,
    is_gap: bool,
) -> None:
    trigger_price = D("98") if side is PositionSide.LONG else D("102")
    rule = ProtectiveRule(
        "stop", "slice", RuleKind.MAX_LOSS, side, trigger_price=trigger_price
    )

    trigger = rule.evaluate(open_price=opening, high=high, low=low)

    assert trigger is not None
    assert trigger.execution_price == D(expected)
    assert trigger.gap is is_gap


def test_chandelier_ratchets_only_toward_profit_for_long_and_short() -> None:
    resolver = ProtectivePolicyResolver()
    profile = StrategyRiskProfile.for_archetype(
        "trend", chandelier_atr_multiple=D("2")
    )
    long = _position(PositionSide.LONG)
    long.observe(D("110"), D("99"))
    first_long = resolver.chandelier(long, profile, D("3"))
    long.highest_price = D("105")  # stale/recovered market observation
    second_long = resolver.chandelier(long, profile, D("3"), previous=first_long)

    short = _position(PositionSide.SHORT)
    short.observe(D("101"), D("90"))
    first_short = resolver.chandelier(short, profile, D("3"))
    short.lowest_price = D("95")
    second_short = resolver.chandelier(short, profile, D("3"), previous=first_short)

    assert first_long.trigger_price == second_long.trigger_price == D("104")
    assert first_short.trigger_price == second_short.trigger_price == D("96")


def test_stop_registry_also_rejects_backward_ratchet() -> None:
    registry = StopRegistry()
    first = ProtectiveRule(
        "trail", "slice", "chandelier", "long", trigger_price=D("95")
    )
    registry.put(first)

    retained = registry.put(
        ProtectiveRule(
            "trail", "slice", "chandelier", "long", trigger_price=D("93")
        )
    )

    assert retained is first
    assert registry.for_slice("slice")[0].trigger_price == D("95")


def test_time_stop_fires_at_expiry_and_uses_close() -> None:
    rule = ProtectiveRule(
        "time",
        "slice",
        RuleKind.TIME,
        PositionSide.LONG,
        expires_at=NOW + timedelta(hours=1),
    )

    assert (
        rule.evaluate(
            open_price=100,
            high=101,
            low=99,
            close=100,
            now=NOW + timedelta(minutes=59),
        )
        is None
    )
    trigger = rule.evaluate(
        open_price=100,
        high=101,
        low=99,
        close=D("100.5"),
        now=NOW + timedelta(hours=1),
    )
    assert trigger is not None
    assert trigger.execution_price == D("100.5")


def test_exit_arbiter_partial_fills_then_closes() -> None:
    position = _position()
    ledger = PositionSliceLedger([position])
    arbiter = ExitArbiter(ledger)

    instruction = arbiter.request_exit(
        position.slice_id, order_id="exit-1", reason="max_loss"
    )
    assert instruction is not None
    assert instruction.quantity == D("10")
    assert position.state is ExitState.EXIT_PENDING
    assert position.revision == 1

    arbiter.apply_fill(
        position.slice_id,
        order_id="exit-1",
        fill_quantity=D("4"),
        fill_price=D("98"),
    )
    assert position.remaining_quantity == D("6")
    assert position.state is ExitState.EXIT_PENDING
    assert position.revision == 2

    arbiter.apply_fill(
        position.slice_id,
        order_id="exit-1",
        fill_quantity=D("6"),
        fill_price=D("97"),
    )
    assert position.state is ExitState.CLOSED
    assert position.remaining_quantity == D("0")
    assert position.average_exit_price == D("97.4")


def test_exit_request_is_idempotent_and_prevents_double_exit() -> None:
    position = _position()
    arbiter = ExitArbiter(PositionSliceLedger([position]))

    first = arbiter.request_exit(
        position.slice_id, order_id="same", reason="structure"
    )
    retry = arbiter.request_exit(
        position.slice_id, order_id="same", reason="structure"
    )
    competing = arbiter.request_exit(
        position.slice_id, order_id="other", reason="time_stop"
    )

    assert retry == first
    assert competing is None
    assert position.revision == 1

    arbiter.apply_fill(
        position.slice_id,
        order_id="same",
        fill_quantity=D("10"),
        fill_price=D("99"),
    )
    assert (
        arbiter.request_exit(
            position.slice_id, order_id="late", reason="duplicate"
        )
        is None
    )


def test_stale_revision_and_wrong_order_fill_are_rejected() -> None:
    position = _position()
    arbiter = ExitArbiter(PositionSliceLedger([position]))
    arbiter.request_exit(position.slice_id, order_id="exit", reason="stop")

    with pytest.raises(ValueError, match="different exit order"):
        arbiter.apply_fill(
            position.slice_id,
            order_id="wrong",
            fill_quantity=1,
            fill_price=99,
        )
    with pytest.raises(ValueError, match="stale"):
        arbiter.apply_fill(
            position.slice_id,
            order_id="exit",
            fill_quantity=1,
            fill_price=99,
            expected_revision=0,
        )


def test_recovery_serialization_preserves_pending_state_and_decimal_values() -> None:
    position = _position(PositionSide.SHORT)
    ledger = PositionSliceLedger([position])
    arbiter = ExitArbiter(ledger)
    arbiter.request_exit(position.slice_id, order_id="recover-me", reason="trail")
    arbiter.apply_fill(
        position.slice_id,
        order_id="recover-me",
        fill_quantity=D("2.5"),
        fill_price=D("101.25"),
    )
    registry = StopRegistry(
        [
            ProtectiveRule(
                "trail",
                position.slice_id,
                RuleKind.CHANDELIER,
                PositionSide.SHORT,
                trigger_price=D("102.125"),
                revision=3,
            )
        ]
    )

    restored_ledger = PositionSliceLedger.from_dict(ledger.to_dict())
    restored_registry = StopRegistry.from_dict(registry.to_dict())
    restored = restored_ledger.get(position.slice_id)

    assert restored.state is ExitState.EXIT_PENDING
    assert restored.exit_order_id == "recover-me"
    assert restored.remaining_quantity == D("7.5")
    assert restored.exit_notional == D("253.125")
    assert restored_registry.for_slice(position.slice_id)[0].trigger_price == D(
        "102.125"
    )
    assert (
        ExitArbiter(restored_ledger).request_exit(
            position.slice_id, order_id="another", reason="duplicate"
        )
        is None
    )


def test_profile_round_trip() -> None:
    profile = StrategyRiskProfile.for_archetype(
        "breakout", max_holding_period=timedelta(hours=7)
    )
    assert StrategyRiskProfile.from_dict(profile.to_dict()) == profile
