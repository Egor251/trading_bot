"""Risk notional must include contract_multiplier (same as Position.notional)."""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.instruments import InstrumentRegistry, InstrumentSpec
from engine.order import OrderIntent, OrderManager, OrderSide, OrderStatus
from engine.position_sizing import size_quantity
from engine.risk import RiskLimits, RiskManager


def test_risk_scales_using_contract_multiplier() -> None:
    registry = InstrumentRegistry()
    registry.register(
        InstrumentSpec(
            symbol="SI",
            class_code="SPBFUT",
            venue="MOEX",
            asset_class="future",
            currency="RUB",
            contract_multiplier="1000",
            # Fractional lot so risk headroom can scale to 0.1 contracts.
            lot_size="0.1",
            tick_size="1",
            margin_model="initial_variation",
        )
    )
    manager = OrderManager(
        RiskManager(RiskLimits(max_position_pct=1.0, max_leverage=1.0)),
        equity=10_000,
        instrument_registry=registry,
    )
    # Without multiplier: qty=5 * price=100 = 500 notional (OK).
    # With multiplier 1000: notional = 5 * 100 * 1000 = 500_000 → must scale to 10k.
    result = manager.submit(
        OrderIntent(
            symbol="SI",
            side=OrderSide.BUY,
            quantity=5,
            class_code="SPBFUT",
        ),
        mark_price=100,
    )
    assert result.status == OrderStatus.FILLED
    assert result.filled_qty == 0.1  # 10_000 / (100 * 1000)
    key = "SPBFUT:SI"
    assert abs(manager.gross_exposure - 10_000) < 1e-6
    assert abs(manager.positions[key].notional - 10_000) < 1e-6


def test_size_quantity_respects_contract_multiplier() -> None:
    qty = size_quantity(
        equity=10_000,
        price=100,
        config={"mode": "percent_of_equity", "value": 10, "max_position_pct": 1.0},
        contract_multiplier=50,
    )
    # 10% of equity = 1000 notional → qty = 1000 / (100*50) = 0.2
    assert abs(qty - 0.2) < 1e-9
