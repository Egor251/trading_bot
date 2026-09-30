"""Deterministic paper runtime fill → mark-to-market → halt E2E."""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.order import OrderStatus
from engine.runtime import TradingRuntime


def test_paper_path_e2e() -> None:
    runtime = TradingRuntime.from_config(
        {
            "mode": "paper",
            "equity": 10_000,
            "risk": {"max_position_pct": 0.2, "max_leverage": 1.0},
        }
    )
    fill = runtime.submit_signal(
        ticker="BTC/USDT",
        action="BUY",
        quantity=1,
        strategy_id="consilium",
        class_code="CRYPTO",
        mark_price=100,
    )
    assert fill.status == OrderStatus.FILLED
    assert runtime.open_position_keys() == {"CRYPTO:BTC/USDT"}

    runtime.orders.mark_to_market("CRYPTO:BTC/USDT", 110)
    assert runtime.orders.equity == 10_010

    runtime.halt("e2e_test")
    blocked = runtime.submit_signal(
        ticker="BTC/USDT",
        action="BUY",
        quantity=1,
        class_code="CRYPTO",
        mark_price=110,
    )
    assert blocked.status == OrderStatus.REJECTED

    close = runtime.submit_signal(
        ticker="BTC/USDT",
        action="SELL",
        quantity=1,
        class_code="CRYPTO",
        mark_price=110,
    )
    assert close.status == OrderStatus.FILLED
    assert runtime.open_position_keys() == set()
