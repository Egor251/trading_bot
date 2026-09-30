"""Operator audit tools: trade pairing, risk rejects, strategy risk, scoped reset."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from engine.risk import RiskLimits, RiskManager  # noqa: E402
from engine.trade_ledger import (  # noqa: E402
    normalize_fill_row,
    pair_round_trips,
    summarize_cost_schedule,
)
from engine.ai_tools import TRADING_AI_TOOL_CLASSES  # noqa: E402
from engine.order import OrderManager  # noqa: E402
from engine.runtime import TradingRuntime  # noqa: E402


def test_new_tools_registered() -> None:
    names = {c.name for c in TRADING_AI_TOOL_CLASSES}
    for n in (
        "get_strategy_trades",
        "get_risk_rejects",
        "get_strategy_risk_limits",
        "set_strategy_risk_limits",
    ):
        assert n in names, n


def test_pair_round_trips_fifo_pnl() -> None:
    fills = [
        {
            "ts": 1,
            "strategy_id": "s1",
            "symbol": "TQBR:SBER",
            "side": "buy",
            "quantity": 10,
            "price": 100.0,
        },
        {
            "ts": 2,
            "strategy_id": "s1",
            "symbol": "TQBR:SBER",
            "side": "sell",
            "quantity": 10,
            "price": 110.0,
        },
    ]
    out = pair_round_trips(fills, strategy_id="s1")
    assert out["closed_count"] == 1
    trade = out["trades"][0]
    assert trade["entry_price"] == 100.0
    assert trade["exit_price"] == 110.0
    assert trade["realized_pnl"] == 100.0  # 10 * (110-100)
    assert out["open_count"] == 0


def test_normalize_fill_row_from_journal_shape() -> None:
    row = {
        "ts": 1.0,
        "event_type": "execution.result",
        "payload": {
            "success": True,
            "strategy_id": "mean_reversion_example",
            "ticker": "SBER",
            "class_code": "TQBR",
            "action": "BUY",
            "quantity": 5,
            "price": 250.0,
            "order": {"status": "filled"},
        },
    }
    norm = normalize_fill_row(row)
    assert norm is not None
    assert norm["strategy_id"] == "mean_reversion_example"
    assert norm["symbol"] == "TQBR:SBER"
    assert norm["side"] == "buy"


def test_summarize_cost_schedule() -> None:
    text = summarize_cost_schedule(
        {
            "version": "v1",
            "rules": [
                {
                    "match": {"class_code": "TQBR"},
                    "components": [{"kind": "commission_bps", "rate": "4"}],
                }
            ],
        },
        source="store_active",
    )
    assert "version=v1" in text
    assert "TQBR" in text
    assert "commission_bps" in text


def test_strategy_risk_limits_merge_scales_position() -> None:
    rm = RiskManager(RiskLimits(max_position_pct=0.20, max_leverage=2.0))
    rm.set_strategy_overrides("hot", {"max_position_pct": 0.05})
    # 10% of equity would violate strategy overlay (5%) but not global 20%.
    d = rm.check_order(
        equity=10_000,
        symbol="SBER",
        notional=1000,
        current_symbol_exposure=0,
        proposed_symbol_exposure=1000,
        current_gross_exposure=0,
        strategy_id="hot",
    )
    assert d.allowed
    assert d.reason == "scaled"
    assert d.scaled_size == 500.0  # 5% of 10k


def test_scoped_reset_symbol() -> None:
    rt = TradingRuntime.from_config({"mode": "paper", "equity": 10_000})
    # Seed a fake position directly on the book.
    from engine.order import Position

    rt.orders.positions["TQBR:SBER"] = Position(
        symbol="TQBR:SBER", quantity=10, avg_price=100, mark_price=100
    )
    rt.orders.positions["TQBR:GAZP"] = Position(
        symbol="TQBR:GAZP", quantity=5, avg_price=200, mark_price=200
    )
    detail = rt.reset_sim_book(symbol="SBER")
    assert detail["scope"] == "partial"
    assert "TQBR:SBER" not in rt.orders.positions or abs(
        rt.orders.positions.get("TQBR:SBER", Position(symbol="x")).quantity
    ) <= 1e-12
    assert "TQBR:GAZP" in rt.orders.positions


def test_reset_paper_tool_requires_confirm_scoped() -> None:
    from engine.ai_tools import ResetPaperTool
    import asyncio

    tool = ResetPaperTool(core=object())

    async def _run() -> None:
        # Without bridge call — confirm gate fires first.
        out = await tool.execute(symbol="SBER")
        assert out["error"] == "confirm_scoped_required"

    asyncio.run(_run())


if __name__ == "__main__":
    test_new_tools_registered()
    test_pair_round_trips_fifo_pnl()
    test_normalize_fill_row_from_journal_shape()
    test_summarize_cost_schedule()
    test_strategy_risk_limits_merge_scales_position()
    test_scoped_reset_symbol()
    test_reset_paper_tool_requires_confirm_scoped()
    print("operator_audit_tools OK")
