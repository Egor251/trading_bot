"""Maker routing and rebate: known-answer pins.

Real algos often earn from the liquidity they provide, not from predicting
direction. Before this change the product could not express post-only at the
decision layer and destroyed the sign of maker rebates.
"""

from __future__ import annotations

from decimal import Decimal

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.execution import BrokerOrder, _is_post_only  # noqa: E402
from engine.fills import FillRecord  # noqa: E402
from engine.order import OrderIntent, OrderSide  # noqa: E402
from engine.order import OrderSide as BrokerOrderSide  # noqa: E402
from tests.test_risk_semantics_fixes import _intent, _manager  # noqa: E402


def test_post_only_intent_survives_into_broker_order():
    intent = OrderIntent(symbol="BTC/USDT", side=OrderSide.BUY,
                         quantity=1.0, post_only=True)
    order = BrokerOrder(symbol=intent.symbol, side=BrokerOrderSide.BUY,
                        quantity=Decimal("1"), venue="bitget", account="default",
                        client_id=intent.client_id, time_in_force="post_only")
    assert intent.post_only is True
    assert _is_post_only(order) is True


def test_rebate_credits_cash_instead_of_being_booked_as_cost():
    no_rebate = _manager()
    rebate_mgr = _manager()
    intent = _intent("BTC/USDT", "CRYPTO", OrderSide.BUY, 1, 100.0)
    no_rebate._record_fill(intent, quantity=1, price=100.0, raw={})  # noqa: SLF001
    rebate_mgr._record_fill(  # noqa: SLF001
        intent, quantity=1, price=100.0,
        raw={"actual_fee": -0.5},
    )
    assert rebate_mgr.fill_ledger[-1].rebate == Decimal("0.5")
    assert rebate_mgr.cash > no_rebate.cash


def test_fill_record_serialization_keeps_rebate():
    m = _manager()
    intent = _intent("BTC/USDT", "CRYPTO", OrderSide.BUY, 1, 100.0)
    m._record_fill(intent, quantity=1, price=100.0,  # noqa: SLF001
                   raw={"actual_fee": -0.25})
    rec = m.fill_ledger[-1]
    restored = FillRecord.from_dict(rec.to_dict())
    assert restored.rebate == Decimal("0.25")
    assert restored.cash_delta == rec.cash_delta
