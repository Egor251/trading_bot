"""Two-sided capability: short votes and reversal permission, opt-in only."""

from __future__ import annotations

from unittest.mock import MagicMock

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

import engine.runtime as runtime_mod  # noqa: E402
from engine.ensemble import EnsembleManager, StrategySignal  # noqa: E402
from engine.order import OrderIntent, OrderSide  # noqa: E402
from engine.runtime import TradingRuntime  # noqa: E402


def _signal(side: str) -> StrategySignal:
    return StrategySignal(strategy_id="s", symbol="BTCUSDT", side=side, strength=1.0)


def test_short_vote_is_opt_in():
    mgr = EnsembleManager(["s"])
    assert mgr.aggregate([_signal("sell")])[0].side == "sell"
    mgr.allow_short = True
    assert mgr.aggregate([_signal("sell")])[0].side == "short"
    assert mgr.aggregate([_signal("buy")])[0].side == "buy"


def test_close_and_reverse_intent_reaches_position_policy(monkeypatch):
    seen: dict = {}

    def fake_evaluate(self, **kwargs):
        seen.update(kwargs)
        return MagicMock(allowed=True, adjusted_qty=None, reason="")

    monkeypatch.setattr(runtime_mod.PositionBook, "evaluate_intent", fake_evaluate)
    rt = TradingRuntime.__new__(TradingRuntime)
    rt.insurance = None
    rt.orders = MagicMock()
    rt.orders.equity = 10_000.0
    rt.orders.peak_equity = 10_000.0
    rt.orders.equity_ex_unrealized = 10_000.0
    rt.protection = MagicMock()
    rt.forbid_net_flip = True
    intent = OrderIntent(
        symbol="BTCUSDT", side=OrderSide.SELL, quantity=1.0,
        strategy_id="consilium", close_and_reverse=True,
    )
    assert rt._gate_consilium_intent(intent) is None  # noqa: SLF001
    assert seen["close_and_reverse"] is True


def test_default_intent_never_asks_for_reversal():
    intent = OrderIntent(symbol="BTCUSDT", side=OrderSide.SELL, quantity=1.0)
    assert intent.close_and_reverse is False
