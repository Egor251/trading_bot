"""Operator place/close + dry-run risk preview."""

from __future__ import annotations

import asyncio

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.candle_store import CandleStore  # noqa: E402
from engine.order import OrderManager  # noqa: E402
from engine.risk import RiskLimits, RiskManager  # noqa: E402
from engine.runtime import TradingRuntime  # noqa: E402


def _runtime(equity: float = 10_000.0) -> TradingRuntime:
    risk = RiskManager(
        RiskLimits(
            max_portfolio_drawdown=0.5,
            max_strategy_drawdown=0.5,
            max_position_pct=1.0,
            max_leverage=5.0,
        )
    )
    orders = OrderManager(risk, mode="paper", equity=equity)
    return TradingRuntime(
        risk=risk,
        orders=orders,
        candles=CandleStore(),
        mode="paper",
    )


def test_preview_risk_records_trace_without_fill() -> None:
    rt = _runtime()
    before = len(rt.orders.history)
    preview = rt.preview_operator_risk(
        ticker="SBER",
        side="buy",
        quantity=1,
        class_code="TQBR",
        mark_price=250.0,
    )
    assert preview["allowed"] is True
    assert preview["qty_allowed"] > 0
    assert len(rt.orders.history) == before
    assert rt.orders.risk_trace
    assert rt.orders.risk_trace[-1]["dry_run"] is True


def test_operator_place_and_close_roundtrip() -> None:
    async def _run() -> None:
        rt = _runtime()
        bought = await rt.operator_place_order(
            ticker="SBER",
            side="buy",
            quantity=10,
            class_code="TQBR",
            mark_price=100.0,
        )
        assert bought.status.value == "filled"
        key = "TQBR:SBER"
        assert abs(rt.orders.positions[key].quantity - 10) < 1e-9

        closed = await rt.operator_close_position(
            symbol=key,
            order_type="limit",
            limit_price=110.0,
            mark_price=110.0,
        )
        assert closed.status.value == "filled"
        assert abs(rt.orders.positions[key].quantity) < 1e-9

    asyncio.run(_run())


def test_close_all_flattens_book() -> None:
    async def _run() -> None:
        rt = _runtime()
        await rt.operator_place_order(
            ticker="SBER", side="buy", quantity=5, class_code="TQBR", mark_price=100
        )
        await rt.operator_place_order(
            ticker="GAZP", side="buy", quantity=3, class_code="TQBR", mark_price=200
        )
        results = await rt.operator_close_all(mark_price=150)
        assert len(results) == 2
        assert all(abs(p.quantity) < 1e-9 for p in rt.orders.positions.values())

    asyncio.run(_run())
