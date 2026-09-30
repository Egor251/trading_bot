from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from engine.instruments import InstrumentSpec
from engine.simulation import (
    ExperimentManifest,
    FillSimulator,
    MarketEvent,
    SimOrder,
    SimOrderStatus,
    adjust_pvalues,
    bootstrap_stress,
    monte_carlo_stress,
    purged_cv_splits,
    sensitivity_plateau,
    walk_forward_splits,
)


NOW = datetime(2026, 1, 1, tzinfo=UTC)


def instrument() -> InstrumentSpec:
    return InstrumentSpec(
        venue="TEST",
        asset_class="equity",
        symbol="XYZ",
        currency="USD",
        lot_size="1",
        tick_size="0.01",
        contract_multiplier="1",
    )


class FixedCost:
    def calculate(self, instrument, side, quantity, price):
        return quantity * Decimal("0.10")


def test_quote_fills_are_partial_deterministic_and_decimal() -> None:
    simulator = FillSimulator(
        instrument(),
        FixedCost(),
        participation_rate="0.5",
        market_impact_bps="10",
    )
    order = simulator.submit(SimOrder("one", "buy", "8", NOW))

    first = simulator.process(
        MarketEvent(NOW, bid="99", ask="101", volume="10")
    )
    second = simulator.process(
        MarketEvent(NOW + timedelta(seconds=1), bid="100", ask="102", volume="10")
    )

    assert first[0].quantity == Decimal("5")
    assert first[0].price == Decimal("101.06")
    assert first[0].costs == Decimal("0.50")
    assert first[0].cash_delta == Decimal("-505.80")
    assert first[0].status is SimOrderStatus.PARTIAL
    assert second[0].quantity == Decimal("3")
    assert second[0].status is SimOrderStatus.FILLED
    assert order.remaining_quantity == Decimal("0")


def test_latency_and_submission_order_share_event_liquidity() -> None:
    simulator = FillSimulator(instrument(), participation_rate="0.5")
    delayed = simulator.submit(
        SimOrder("delayed", "buy", 5, NOW, latency=timedelta(seconds=2))
    )
    simulator.submit(SimOrder("first", "buy", 4, NOW))
    simulator.submit(SimOrder("second", "buy", 4, NOW))

    fills = simulator.process(MarketEvent(NOW, bid=9, ask=10, volume=10))
    assert [(fill.order_id, fill.quantity) for fill in fills] == [
        ("first", Decimal("4")),
        ("second", Decimal("1")),
    ]
    assert delayed.status is SimOrderStatus.PENDING
    assert simulator.process(
        MarketEvent(NOW + timedelta(seconds=1), bid=9, ask=10, volume=10)
    )[0].order_id == "second"
    assert simulator.process(
        MarketEvent(NOW + timedelta(seconds=2), bid=9, ask=10, volume=10)
    )[0].order_id == "delayed"


def test_bar_range_triggers_limit_and_stop_gap_is_pessimistic() -> None:
    simulator = FillSimulator(instrument(), synthetic_spread_bps=0)
    simulator.submit(
        SimOrder("limit", "buy", 1, NOW, order_type="limit", limit_price="95")
    )
    simulator.submit(
        SimOrder("stop", "buy", 1, NOW, order_type="stop", stop_price="105")
    )

    fills = simulator.process(
        MarketEvent.bar(NOW, open=110, high=112, low=90, close=100, volume=10)
    )

    assert [(fill.order_id, fill.price) for fill in fills] == [
        ("limit", Decimal("95")),
        ("stop", Decimal("110")),
    ]


def test_rejects_out_of_order_events_and_sub_lot_orders() -> None:
    spec = InstrumentSpec(
        venue="TEST",
        asset_class="equity",
        symbol="LOTS",
        currency="USD",
        lot_size=10,
        tick_size=1,
    )
    simulator = FillSimulator(spec)
    assert simulator.submit(SimOrder("tiny", "buy", 5, NOW)).status is SimOrderStatus.REJECTED
    simulator.process(MarketEvent(NOW, bid=9, ask=10, volume=10))
    with pytest.raises(ValueError, match="chronological"):
        simulator.process(MarketEvent(NOW - timedelta(seconds=1), bid=9, ask=10))


def test_manifest_hashes_inputs_and_has_stable_identity() -> None:
    manifest = ExperimentManifest.create(
        code={"commit": "abc"},
        config={"risk": 1},
        data=b"rows",
        costs={"fee": "0.1"},
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=2),
        seed=42,
        provenance={"host": "ci"},
    )
    repeat = ExperimentManifest.create(
        code={"commit": "abc"},
        config={"risk": 1},
        data=b"rows",
        costs={"fee": "0.1"},
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=2),
        seed=42,
        provenance={"host": "ci"},
    )
    assert len(manifest.code_hash) == 64
    assert manifest.experiment_id == repeat.experiment_id
    assert manifest.to_dict()["seed"] == 42


def test_validation_and_stress_helpers_are_deterministic() -> None:
    walk = walk_forward_splits(10, train_size=4, test_size=2)
    assert walk[0].train == (0, 1, 2, 3)
    assert walk[0].test == (4, 5)
    assert walk[-1].test == (8, 9)

    folds = purged_cv_splits(10, 2, purge=1, embargo=1)
    assert folds[0].test == (0, 1, 2, 3, 4)
    assert folds[0].train == (7, 8, 9)
    assert not set(folds[1].train) & set(folds[1].test)

    assert adjust_pvalues([0.01, 0.04], "bonferroni") == [0.02, 0.08]
    assert adjust_pvalues([0.01, 0.04], "holm") == [0.02, 0.04]
    assert bootstrap_stress([0.01, -0.02], samples=5, seed=7) == bootstrap_stress(
        [0.01, -0.02], samples=5, seed=7
    )
    assert monte_carlo_stress([0.01, -0.02], simulations=5, seed=9) == monte_carlo_stress(
        [0.01, -0.02], simulations=5, seed=9
    )
    assert sensitivity_plateau(
        {1.0: 90, 2.0: 99, 3.0: 100, 4.0: 98, 5.0: 80},
        tolerance=0.02,
    ) == (2.0, 3.0, 4.0)
