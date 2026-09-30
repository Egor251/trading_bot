"""Regression tests for signed positions, drawdown and leverage."""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.order import (
    OrderIntent,
    OrderManager,
    OrderResult,
    OrderSide,
    OrderStatus,
)
from engine.risk import RiskLimits, RiskManager


def _intent(symbol: str, side: OrderSide, qty: float) -> OrderIntent:
    return OrderIntent(symbol=symbol, side=side, quantity=qty, class_code="CRYPTO")


def test_sell_reduces_position_without_false_max_position_reject() -> None:
    manager = OrderManager(
        RiskManager(RiskLimits(max_position_pct=0.20, max_leverage=1.0)),
        equity=10_000,
    )
    buy = manager.submit(_intent("BTC/USDT", OrderSide.BUY, 20), mark_price=100)
    assert buy.status == OrderStatus.FILLED
    assert manager.exposures["CRYPTO:BTC/USDT"] == 2_000

    sell = manager.submit(_intent("BTC/USDT", OrderSide.SELL, 10), mark_price=100)
    assert sell.status == OrderStatus.FILLED
    assert manager.exposures["CRYPTO:BTC/USDT"] == 1_000


def test_position_realized_and_unrealized_pnl_update_equity() -> None:
    manager = OrderManager(
        RiskManager(RiskLimits(max_position_pct=1.0, max_leverage=1.0)),
        equity=10_000,
    )
    manager.submit(_intent("BTC/USDT", OrderSide.BUY, 10), mark_price=100)
    assert manager.mark_to_market("CRYPTO:BTC/USDT", 90) == 9_900

    manager.submit(_intent("BTC/USDT", OrderSide.SELL, 5), mark_price=90)
    position = manager.positions["CRYPTO:BTC/USDT"]
    assert position.quantity == 5
    assert position.realized_pnl == -50
    assert manager.equity == 9_900


def test_absolute_drawdown_halts() -> None:
    risk = RiskManager(
        RiskLimits(
            max_portfolio_drawdown=0.99,
            max_portfolio_drawdown_abs=500.0,
            max_position_pct=1.0,
            max_leverage=1.0,
        )
    )
    decision = risk.check_portfolio_drawdown(equity=9_400, peak_equity=10_000)
    assert not decision.allowed
    assert risk.halted
    assert "portfolio_dd_abs" in decision.reason


def test_drawdown_halts_new_risk_but_allows_reduction() -> None:
    manager = OrderManager(
        RiskManager(
            RiskLimits(
                max_portfolio_drawdown=0.01,
                max_position_pct=1.0,
                max_leverage=1.0,
            )
        ),
        equity=10_000,
    )
    manager.submit(_intent("BTC/USDT", OrderSide.BUY, 10), mark_price=100)
    manager.mark_to_market("CRYPTO:BTC/USDT", 80)

    increase = manager.submit(_intent("BTC/USDT", OrderSide.BUY, 1), mark_price=80)
    assert increase.status == OrderStatus.REJECTED
    assert manager.risk.halted

    reduce = manager.submit(_intent("BTC/USDT", OrderSide.SELL, 1), mark_price=80)
    assert reduce.status == OrderStatus.FILLED
    assert manager.positions["CRYPTO:BTC/USDT"].quantity == 9


def test_leverage_caps_second_symbol() -> None:
    manager = OrderManager(
        RiskManager(RiskLimits(max_position_pct=1.0, max_leverage=0.5)),
        equity=10_000,
    )
    manager.submit(_intent("BTC/USDT", OrderSide.BUY, 30), mark_price=100)
    second = manager.submit(_intent("ETH/USDT", OrderSide.BUY, 30), mark_price=100)
    assert second.status == OrderStatus.FILLED
    assert second.filled_qty == 20
    assert manager.gross_exposure == 5_000


def test_max_position_scale_does_not_skip_leverage() -> None:
    """Regression: max_position early-return used to authorize N×25% > max_leverage."""
    manager = OrderManager(
        RiskManager(RiskLimits(max_position_pct=0.25, max_leverage=1.0)),
        equity=10_000,
    )
    for sym in ("A", "B", "C", "D"):
        filled = manager.submit(
            OrderIntent(symbol=sym, side=OrderSide.BUY, quantity=100, class_code="X"),
            mark_price=100,
        )
        assert filled.status == OrderStatus.FILLED
    assert abs(manager.gross_exposure - 10_000) < 1e-6
    fifth = manager.submit(
        OrderIntent(symbol="E", side=OrderSide.BUY, quantity=100, class_code="X"),
        mark_price=100,
    )
    assert fifth.status == OrderStatus.REJECTED
    assert fifth.reason == "max_leverage"
    assert manager.gross_exposure <= 10_000 + 1e-6


def test_broker_accepted_without_fill_does_not_change_position() -> None:
    manager = OrderManager(
        RiskManager(RiskLimits(max_position_pct=1.0, max_leverage=1.0)),
        mode="live",
        equity=10_000,
    )
    intent = _intent("BTC/USDT", OrderSide.BUY, 1)
    result = manager.record_broker_result(
        intent,
        OrderResult(client_id=intent.client_id, status=OrderStatus.ACCEPTED),
        mark_price=100,
        qty=1,
    )
    assert result.status == OrderStatus.ACCEPTED
    assert result.filled_qty == 0
    assert manager.exposures == {}

