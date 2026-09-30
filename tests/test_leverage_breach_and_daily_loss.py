"""Regression: hard leverage breach + daily_loss ignores open MTM noise."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.insurance import InsuranceGuard
from engine.order import OrderIntent, OrderManager, OrderSide, OrderStatus
from engine.risk import RiskLimits, RiskManager
from engine.runtime import TradingRuntime


def test_leverage_breach_blocks_new_risk_allows_reduce() -> None:
    runtime = TradingRuntime.from_config(
        {
            "mode": "paper",
            "equity": 10_000,
            "risk": {"max_position_pct": 5.0, "max_leverage": 5.0},
        }
    )
    # Build a 2x book while limits allow it, then tighten max_leverage.
    opened = runtime.submit_signal(
        ticker="BTC/USDT",
        action="BUY",
        quantity=200,
        class_code="CRYPTO",
        mark_price=100,
        apply_sizing=False,
    )
    assert opened.status == OrderStatus.FILLED
    assert opened.filled_qty == 200
    runtime.risk.limits.max_leverage = 1.0
    assert runtime.orders.gross_exposure > runtime.orders.equity * 1.0

    blocked = runtime.submit_signal(
        ticker="BTC/USDT",
        action="BUY",
        quantity=1,
        class_code="CRYPTO",
        mark_price=100,
        apply_sizing=False,
    )
    assert blocked.status == OrderStatus.REJECTED
    assert "no_leverage_headroom" in (blocked.reason or "")
    # Soft block — must not kill-switch the runtime (reduces / later headroom).
    assert not runtime.risk.halted

    reduce = runtime.submit_signal(
        ticker="BTC/USDT",
        action="SELL",
        quantity=1,
        class_code="CRYPTO",
        mark_price=100,
        reduce_only=True,
        apply_sizing=False,
    )
    assert reduce.status == OrderStatus.FILLED


def test_daily_loss_uses_realized_equity_not_mtm_swing() -> None:
    risk = RiskManager(
        RiskLimits(max_portfolio_drawdown=0.99, max_leverage=1.0, max_position_pct=1.0)
    )
    guard = InsuranceGuard(risk, daily_loss_pct=0.05, day_start_equity=10_000.0)
    # MTM equity crashed 9% on a huge open position, but realized book is flat/up.
    decision = guard.check(
        equity=9_100.0,
        daily_equity=10_200.0,
        gross_exposure=0.0,
    )
    assert decision.allowed
    assert not risk.halted


def test_insurance_leverage_breach() -> None:
    risk = RiskManager(RiskLimits(max_leverage=1.0, max_portfolio_drawdown=0.99))
    guard = InsuranceGuard(risk, daily_loss_pct=0.5, day_start_equity=10_000.0)
    decision = guard.check(
        equity=10_000.0, daily_equity=10_000.0, gross_exposure=500_000.0
    )
    assert not decision.allowed
    assert "no_leverage_headroom" in decision.reason
    assert not risk.halted


def test_risk_qty_scales_with_headroom() -> None:
    manager = OrderManager(
        RiskManager(RiskLimits(max_position_pct=1.0, max_leverage=1.0)),
        equity=10_000,
    )
    first = manager.submit(
        OrderIntent(
            symbol="BTC/USDT", side=OrderSide.BUY, quantity=200, class_code="CRYPTO"
        ),
        mark_price=100,
    )
    assert first.status == OrderStatus.FILLED
    assert first.filled_qty == 100  # capped to 1x equity
    assert abs(manager.gross_exposure - 10_000) < 1e-6


def test_consilium_long_short_book_stays_within_leverage() -> None:
    """Long+short multi-symbol book cannot accumulate past max_leverage=1."""
    runtime = TradingRuntime.from_config(
        {
            "mode": "paper",
            "equity": 10_000,
            "risk": {"max_position_pct": 0.25, "max_leverage": 1.0},
        }
    )
    for ticker, action in (
        ("BTC/USDT", "BUY"),
        ("ETH/USDT", "SELL"),
        ("SOL/USDT", "BUY"),
        ("BNB/USDT", "SELL"),
    ):
        result = runtime.submit_signal(
            ticker=ticker,
            action=action,
            quantity=100,
            class_code="CRYPTO",
            mark_price=100,
            strategy_id="consilium",
            apply_sizing=False,
        )
        assert result.status == OrderStatus.FILLED
        assert runtime.orders.gross_exposure <= runtime.orders.equity + 1e-6
    assert abs(runtime.orders.gross_exposure - 10_000) < 1e-6
    # Fifth risk-increasing leg must be blocked (old bug: max_position skipped leverage).
    extra = runtime.submit_signal(
        ticker="XRP/USDT",
        action="BUY",
        quantity=100,
        class_code="CRYPTO",
        mark_price=100,
        strategy_id="consilium",
        apply_sizing=False,
    )
    assert extra.status == OrderStatus.REJECTED
    assert "no_leverage_headroom" in (extra.reason or "") or extra.reason == "max_leverage"
    assert runtime.orders.gross_exposure <= runtime.orders.equity + 1e-6
    assert not runtime.risk.halted


def test_mtm_over_leverage_soft_blocks_without_halt_spam() -> None:
    """Adverse MTM on a short pushes lev>1; new risk soft-skips, reduces still work."""
    runtime = TradingRuntime.from_config(
        {
            "mode": "paper",
            "equity": 10_000,
            "risk": {"max_position_pct": 1.0, "max_leverage": 1.0},
        }
    )
    opened = runtime.submit_signal(
        ticker="ETH/USDT",
        action="SELL",
        quantity=100,
        class_code="CRYPTO",
        mark_price=100,
        apply_sizing=False,
    )
    assert opened.status == OrderStatus.FILLED
    # Short marked against us: gross up, equity down → lev > 1 without a new fill.
    runtime.orders.mark_to_market("CRYPTO:ETH/USDT", 135)
    assert runtime.orders.gross_exposure / runtime.orders.equity > 1.0

    reasons = []
    for _ in range(20):
        blocked = runtime.submit_signal(
            ticker="BTC/USDT",
            action="BUY",
            quantity=10,
            class_code="CRYPTO",
            mark_price=100,
            strategy_id="consilium",
            apply_sizing=False,
        )
        assert blocked.status == OrderStatus.REJECTED
        reasons.append(blocked.reason or "")
    assert all("no_leverage_headroom" in r for r in reasons)
    assert not runtime.risk.halted

    reduce = runtime.submit_signal(
        ticker="ETH/USDT",
        action="BUY",
        quantity=10,
        class_code="CRYPTO",
        mark_price=135,
        reduce_only=True,
        apply_sizing=False,
    )
    assert reduce.status == OrderStatus.FILLED


def test_headroom_clamp_rejects_dust_qty() -> None:
    """Near-full book must not open 1e-13 slices from leftover headroom."""
    runtime = TradingRuntime.from_config(
        {
            "mode": "paper",
            "equity": 10_000,
            "risk": {"max_position_pct": 1.0, "max_leverage": 1.0},
            "sizing": {"min_qty": 1e-8},
        }
    )
    opened = runtime.submit_signal(
        ticker="BTC/USDT",
        action="BUY",
        quantity=99.999999999,
        class_code="CRYPTO",
        mark_price=100,
        apply_sizing=False,
    )
    assert opened.status == OrderStatus.FILLED
    dust = runtime.submit_signal(
        ticker="ETH/USDT",
        action="BUY",
        quantity=1.0,
        class_code="CRYPTO",
        mark_price=100,
        strategy_id="consilium",
        apply_sizing=False,
    )
    assert dust.status == OrderStatus.REJECTED
    assert "no_leverage_headroom" in (dust.reason or "") or dust.reason == "qty<=0"
    assert runtime.orders.gross_exposure <= runtime.orders.equity + 1.0


def test_paper_fill_rejects_below_lot_size() -> None:
    from engine.instruments import InstrumentRegistry, InstrumentSpec
    from engine.order import OrderIntent, OrderManager, OrderSide
    from engine.risk import RiskLimits, RiskManager

    registry = InstrumentRegistry()
    registry.register(
        InstrumentSpec(
            venue="BINANCE",
            asset_class="crypto",
            symbol="BTC/USDT",
            currency="USDT",
            class_code="CRYPTO",
            lot_size="0.0001",
            tick_size="0.01",
        )
    )
    manager = OrderManager(
        RiskManager(RiskLimits(max_position_pct=1.0, max_leverage=5.0)),
        equity=10_000,
        instrument_registry=registry,
    )
    result = manager.submit(
        OrderIntent(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            quantity=1e-12,
            class_code="CRYPTO",
        ),
        mark_price=100,
    )
    assert result.status == OrderStatus.REJECTED
    assert result.reason == "qty_below_lot_size"
