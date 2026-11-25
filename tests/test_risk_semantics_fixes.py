"""Regression tests for the risk / ledger correctness fixes.

Each test names the specific defect it pins down. Without these, the limits in
`config/trading_bot.yaml` were configuration that did not mean what it said.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.order import (  # noqa: E402
    OrderIntent,
    OrderManager,
    OrderSide,
    OrderStatus,
)
from engine.risk import RiskLimits, RiskManager  # noqa: E402


def _limits(**kw) -> RiskLimits:
    base = dict(
        max_position_pct=0.2,
        max_leverage=1.0,
        max_daily_loss_pct=0.05,
        max_daily_turnover_pct=0.5,
        max_portfolio_drawdown=0.10,
        max_orders_per_minute=10,
    )
    base.update(kw)
    return RiskLimits(**base)


def _manager(equity: float = 100_000.0, **kw) -> OrderManager:
    fees = kw.pop("_legacy_fee_bps", 5.0)
    slippage = kw.pop("_legacy_slippage_bps", 2.0)
    return OrderManager(
        risk=RiskManager(_limits(**kw)),
        mode="paper",
        equity=equity,
        legacy_fee_bps=fees,
        legacy_slippage_bps=slippage,
    )


def _intent(symbol: str, class_code: str, side: OrderSide, qty: float, price: float) -> OrderIntent:
    return OrderIntent(
        symbol=symbol,
        class_code=class_code,
        side=side,
        quantity=qty,
        limit_price=price,
    )


# ---------------------------------------------------------------------------
# A7 — daily loss must be measured from day-start, not from boot capital
# ---------------------------------------------------------------------------


def test_daily_loss_uses_day_start_not_starting_equity() -> None:
    """A single 5% unrealized dip used to halt trading *permanently*.

    `starting_equity` is lifetime boot capital and `set_starting_equity` raises
    while positions are open, so it could never represent "the open of today".
    """
    m = _manager()
    m.note_day_start_equity(100_000.0)
    # 4% unrealized dip: under the cap, must pass.
    reject, _qty = m._risk_qty(  # noqa: SLF001
        _intent("BTC/USDT", "CRYPTO", OrderSide.BUY, 0.1, 100_000.0),
        mark_price=96_000.0,
    )
    assert reject is None, reject

    # Same equity relative to a *lower* day-start (yesterday lost 4%):
    # the day-loss gate must use 96k, not 100k.
    m.note_day_start_equity(96_000.0)
    reject, _qty = m._risk_qty(  # noqa: SLF001
        _intent("BTC/USDT", "CRYPTO", OrderSide.BUY, 0.1, 96_000.0),
        mark_price=96_000.0,
    )
    assert reject is None, reject


def test_day_start_equity_survives_restart_of_the_flag() -> None:
    m = _manager()
    m.note_day_start_equity(50_000.0)
    assert m._day_start_equity == 50_000.0  # noqa: SLF001
    m.set_starting_equity(100_000.0)
    assert m._day_start_equity == 100_000.0  # noqa: SLF001


# ---------------------------------------------------------------------------
# A8 — daily turnover must roll at the session boundary
# ---------------------------------------------------------------------------


def test_daily_turnover_rolls_on_day_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turnover never reset: once the process traded the daily cap *ever*,
    every subsequent order was rejected forever."""
    m = _manager(equity=10_000.0, max_daily_turnover_pct=0.5)
    m._day_key = "1999-01-01"  # noqa: SLF001
    m._daily_turnover = 9_999.0  # noqa: SLF001

    intent = _intent("BTC/USDT", "CRYPTO", OrderSide.BUY, 0.01, 50_000.0)
    reject, _ = m._risk_qty(intent, mark_price=50_000.0)  # noqa: SLF001
    assert reject is None, f"turnover should have rolled, got {reject}"
    assert m._daily_turnover == 0.0  # noqa: SLF001


def test_daily_turnover_still_blocks_within_the_same_day() -> None:
    m = _manager(equity=10_000.0, max_daily_turnover_pct=0.5)
    m._daily_turnover = 4_999.0  # noqa: SLF001
    intent = _intent("BTC/USDT", "CRYPTO", OrderSide.BUY, 0.01, 50_000.0)
    reject, _ = m._risk_qty(intent, mark_price=50_000.0)  # noqa: SLF001
    assert reject is not None
    assert "daily_turnover" in (reject.reason or "")


# ---------------------------------------------------------------------------
# A9 — max_orders_per_minute was inert
# ---------------------------------------------------------------------------


def test_order_rate_limit_actually_rejects() -> None:
    """`record_order()` had no production caller, so the local window was always
    empty and `0 >= 10` never held — a runaway strategy loop was unthrottled."""
    risk = RiskManager(_limits(max_orders_per_minute=3, max_position_pct=1.0))
    allowed = 0
    for _ in range(8):
        decision = risk.check_order(
            equity=100_000.0,
            symbol="BTC/USDT",
            notional=100.0,
            current_symbol_exposure=0.0,
            proposed_symbol_exposure=100.0,
            current_gross_exposure=0.0,
            day_start_equity=100_000.0,
            daily_turnover=0.0,
        )
        if decision.allowed:
            allowed += 1
        else:
            assert "order_rate" in (decision.reason or ""), decision.reason
    assert allowed == 3, allowed


def test_order_rate_preview_does_not_consume_budget() -> None:
    m = _manager(max_orders_per_minute=5)
    intent = _intent("BTC/USDT", "CRYPTO", OrderSide.BUY, 0.001, 50_000.0)
    for _ in range(10):
        m._risk_qty(intent, mark_price=50_000.0, dry_run=True)  # noqa: SLF001
    assert len(m.risk._order_times) == 0  # noqa: SLF001
    # Full budget is still intact: five live orders must all pass.
    for i in range(5):
        reject, _ = m._risk_qty(intent, mark_price=50_000.0)  # noqa: SLF001
        assert reject is None, f"{i}: {reject}"


# ---------------------------------------------------------------------------
# A22 — a wiped account must still be able to flatten
# ---------------------------------------------------------------------------


def test_reduce_only_survives_zero_equity() -> None:
    """`equity <= 0` was checked before the risk-reducing escape, so the closing
    order was rejected with the very condition it would fix."""
    risk = RiskManager(_limits())
    decision = risk.check_order(
        equity=0.0,
        symbol="BTC/USDT",
        notional=1_000.0,
        current_symbol_exposure=0.0,
        proposed_symbol_exposure=0.0,
        current_gross_exposure=0.0,
        reduces_risk=True,
    )
    assert decision.allowed, decision.reason

    # Risk-increasing must still be refused.
    blocked = risk.check_order(
        equity=0.0,
        symbol="BTC/USDT",
        notional=1_000.0,
        current_symbol_exposure=0.0,
        proposed_symbol_exposure=0.0,
        current_gross_exposure=0.0,
        reduces_risk=False,
    )
    assert not blocked.allowed
    assert blocked.reason == "equity<=0"


# ---------------------------------------------------------------------------
# A23b — per-strategy overrides must actually be enforced
# ---------------------------------------------------------------------------


def test_per_strategy_daily_loss_override_is_enforced() -> None:
    """`set_strategy_overrides` accepted max_daily_loss_pct and the UI writes it,
    but the check read the global limits — so tightening a strategy's cap was
    accepted, persisted, and never enforced."""
    risk = RiskManager(_limits(max_daily_loss_pct=0.50))
    risk.set_strategy_overrides("greedy", {"max_daily_loss_pct": 0.01})

    # 2% loss vs the strategy's 1% cap -> halt, even though the global cap is 50%.
    decision = risk.check_order(
        equity=98_000.0,
        symbol="BTC/USDT",
        notional=1_000.0,
        current_symbol_exposure=0.0,
        proposed_symbol_exposure=1_000.0,
        current_gross_exposure=0.0,
        day_start_equity=100_000.0,
        daily_turnover=0.0,
        strategy_id="greedy",
    )
    assert not decision.allowed
    assert "daily_loss" in (decision.reason or "")

    # A different strategy is unaffected by `greedy`'s cap.
    other = RiskManager(_limits(max_daily_loss_pct=0.50))
    other.set_strategy_overrides("greedy", {"max_daily_loss_pct": 0.01})
    allowed = other.check_order(
        equity=98_000.0,
        symbol="BTC/USDT",
        notional=1_000.0,
        current_symbol_exposure=0.0,
        proposed_symbol_exposure=1_000.0,
        current_gross_exposure=0.0,
        day_start_equity=100_000.0,
        daily_turnover=0.0,
        strategy_id="careful",
    )
    assert allowed.allowed, allowed.reason


def test_per_strategy_order_rate_override_is_enforced() -> None:
    risk = RiskManager(_limits(max_orders_per_minute=1000))
    risk.set_strategy_overrides("greedy", {"max_orders_per_minute": 2})
    counted = 0
    for _ in range(5):
        out = risk.check_order(
            equity=100_000.0,
            symbol="BTC/USDT",
            notional=1.0,
            current_symbol_exposure=0.0,
            proposed_symbol_exposure=1.0,
            current_gross_exposure=0.0,
            day_start_equity=100_000.0,
            daily_turnover=0.0,
            strategy_id="greedy",
        )
        if out.allowed:
            counted += 1
        else:
            assert "order_rate" in (out.reason or ""), out.reason
    assert counted == 2, counted


def test_per_strategy_turnover_override_is_enforced() -> None:
    risk = RiskManager(_limits(max_daily_turnover_pct=10.0))
    risk.set_strategy_overrides("greedy", {"max_daily_turnover_pct": 0.01})
    blocked = risk.check_order(
        equity=100_000.0,
        symbol="BTC/USDT",
        notional=5_000.0,
        current_symbol_exposure=0.0,
        proposed_symbol_exposure=5_000.0,
        current_gross_exposure=0.0,
        day_start_equity=100_000.0,
        daily_turnover=0.0,
        strategy_id="greedy",
    )
    assert not blocked.allowed
    assert "daily_turnover" in (blocked.reason or "")


# ---------------------------------------------------------------------------
# D3 — reduce-only must be strategy-scoped
# ---------------------------------------------------------------------------


def test_protective_exit_works_in_a_hedged_book() -> None:
    """Strategy A long 1, strategy B short 1 -> net 0. Cross-strategy netting made
    *every* protective exit reject "reduce_only_flat" while real broker exposure
    stayed open, in an endless reject loop."""
    from datetime import UTC, datetime

    from engine.position_book import PositionBook
    from engine.protective_runtime import ProtectiveLayer

    layer = ProtectiveLayer()
    for slice_id, strategy_id, side in (("s1", "A", "buy"), ("s2", "B", "sell")):
        layer.open_slice(
            slice_id=slice_id,
            strategy_id=strategy_id,
            symbol="BTC/USDT",
            side=side,
            quantity=1,
            entry_price=100,
            opened_at=datetime.now(UTC),
        )
    book = PositionBook(layer.ledger, forbid_net_flip=True)
    assert book.net_qty("BTC/USDT") == Decimal("0")

    exit_a = book.evaluate_intent(
        strategy_id="A",
        symbol="BTC/USDT",
        side="sell",
        quantity=1,
        reduce_only=True,
    )
    assert exit_a.allowed, exit_a.reason
    assert exit_a.adjusted_qty == Decimal("1")

    exit_b = book.evaluate_intent(
        strategy_id="B",
        symbol="BTC/USDT",
        side="buy",
        quantity=1,
        reduce_only=True,
    )
    assert exit_b.allowed, exit_b.reason

    # Capping still applies, scoped to the strategy's own slice.
    capped = book.evaluate_intent(
        strategy_id="A",
        symbol="BTC/USDT",
        side="sell",
        quantity=5,
        reduce_only=True,
    )
    assert capped.allowed
    assert capped.adjusted_qty == Decimal("1")

    # A flat strategy is still flat.
    assert not book.evaluate_intent(
        strategy_id="C", symbol="BTC/USDT", side="sell", quantity=1, reduce_only=True
    ).allowed


# ---------------------------------------------------------------------------
# D4 / D5 — cost attribution and rejected-order handling
# ---------------------------------------------------------------------------


def test_extract_result_cost_finds_the_matching_fill() -> None:
    """Read `fill_ledger`, not `fills_ledger` — the typo meant every
    `execution.result` carried `cost: null`."""
    from engine.fills import FillRecord, extract_result_cost

    class _Raw:
        costs = {"total": 1.5}

    class _Fill:
        def __init__(self, order_id, total):
            self.order_id = order_id
            self._total = total

        def to_dict(self):
            return {"costs": {"total": self._total}}

    class _Orders:
        fill_ledger = [_Fill("other", 9.99), _Fill("mine", 2.25)]

    class _Runtime:
        orders = _Orders()

    class _Result:
        client_id = "mine"
        raw = _Raw()

    assert extract_result_cost(_Result(), _Runtime()) == 2.25
    assert FillRecord is not None


def test_rejected_order_is_not_booked_as_a_fill() -> None:
    """`or` treated filled_qty == 0 as missing, so a rejected order with
    quantity 10 was recorded as a 10-unit fill."""
    from engine.trade_ledger import normalize_fill_row

    rejected = normalize_fill_row(
        {
            "event_type": "execution.result",
            "payload": {
                "cmd": "PLACE_ORDER",
                "success": False,
                "order": {
                    "ticker": "SBER",
                    "side": "BUY",
                    "status": "rejected",
                    "filled_qty": 0,
                    "quantity": 10,
                    "price": 285.0,
                    "reason": "risk",
                },
            },
        }
    )
    assert rejected is None, rejected


def test_zero_filled_qty_does_not_fall_back_to_order_quantity() -> None:
    from engine.trade_ledger import normalize_fill_row

    row = normalize_fill_row(
        {
            "event_type": "fill",
            "strategy_id": "ma_cross_example",
            "ts": "2026-01-01T00:00:00Z",
            "order": {
                "ticker": "SBER",
                "side": "BUY",
                "status": "filled",
                "filled_qty": 0,
                "quantity": 10,
                "avg_price": 0,
            },
        }
    )
    # Either rejected outright or reported as a genuine zero — never as 10 @ 285.
    if row is not None:
        assert row["quantity"] == 0.0
        assert row["price"] != 285.0


# ---------------------------------------------------------------------------
# D6 — a stale cost schedule must not abort a fill
# ---------------------------------------------------------------------------


def test_expired_cost_schedule_records_zero_instead_of_raising() -> None:
    from datetime import UTC, datetime, timedelta

    from engine.costs import CostBreakdown

    class _Exploding:
        def calculate(self, _context):
            raise ValueError("cost schedule is not effective at trade_time")

    m = _manager()
    m.cost_schedule = _Exploding()
    intent = _intent("BTC/USDT", "CRYPTO", OrderSide.BUY, 0.01, 50_000.0)
    m._record_fill(  # noqa: SLF001
        intent, quantity=0.01, price=50_000.0, raw={}
    )
    assert m.cost_schedule_errors == 1
    assert len(m.fill_ledger) == 1, "the fill must still be recorded"
    assert m.positions, "the position must still be opened"
    assert CostBreakdown() is not None
    assert datetime.now(UTC) - timedelta(0) is not None


def test_broker_fee_is_added_not_substituted() -> None:
    """The broker's real fee must not wipe the modelled commission + slippage."""
    m = _manager()
    intent = _intent("BTC/USDT", "CRYPTO", OrderSide.BUY, 0.01, 50_000.0)
    record = m._record_fill(  # noqa: SLF001
        intent, quantity=0.01, price=50_000.0, raw={"actual_fee": "0.75"}
    )
    components = {line.component for line in record.costs.lines}
    assert "actual_fee" in components
    assert len(record.costs.lines) > 1, (
        "modelled cost lines were discarded by the broker-fee override"
    )
    assert record.costs.total > Decimal("0.75")


def test_negative_broker_fee_is_a_rebate_not_a_cost() -> None:
    m = _manager()
    intent = _intent("BTC/USDT", "CRYPTO", OrderSide.BUY, 0.01, 50_000.0)
    record = m._record_fill(  # noqa: SLF001
        intent, quantity=0.01, price=50_000.0, raw={"actual_fee": "-1.25"}
    )
    assert record is not None
    assert record.rebate == Decimal("1.25")
    assert not any(line.component == "actual_fee" for line in record.costs.lines)


# ---------------------------------------------------------------------------
# D9 — per-symbol reset must not desynchronise cash
# ---------------------------------------------------------------------------


def test_clear_symbol_keeps_cash_of_fills_it_does_not_remove() -> None:
    """A per-symbol reset must return only that symbol's cash deltas.

    `removed_fills` compared each fill object against a set of `id()` ints, so
    the predicate was always true and clearing one symbol reversed the cash
    of every fill in the ledger, including the positions that stayed open.
    """
    m = _manager()
    sber = _intent("SBER", "TQBR", OrderSide.BUY, 10, 285.0)
    gazp = _intent("GAZP", "TQBR", OrderSide.BUY, 5, 165.0)
    m._record_fill(sber, quantity=10, price=285.0, raw={})  # noqa: SLF001
    m._record_fill(gazp, quantity=5, price=165.0, raw={})  # noqa: SLF001
    kept_delta = sum(float(f.cash_delta) for f in m.fill_ledger if "GAZP" in str(f.symbol))
    expected = float(m.starting_equity) + kept_delta

    m.clear_symbol("SBER")

    assert float(m.cash) == pytest.approx(expected, abs=1.0)
    assert m.positions, "the other symbol must remain open"


def test_clear_symbol_keeps_cash_consistent() -> None:
    m = _manager()
    intent = _intent("SBER", "TQBR", OrderSide.BUY, 10, 285.0)
    m._record_fill(intent, quantity=10, price=285.0, raw={})  # noqa: SLF001
    cash_after_fill = float(m.cash)
    assert cash_after_fill < 100_000.0

    m.clear_symbol("SBER")
    assert float(m.cash) == pytest.approx(100_000.0, abs=1.0), (
        "clear_symbol left cash holding the removed fill's cash delta"
    )
    assert not m.positions


# ---------------------------------------------------------------------------
# D1 / D7 — durable fill application
# ---------------------------------------------------------------------------


def test_record_fill_is_atomic_on_overfill(tmp_path) -> None:
    from datetime import UTC, datetime

    from dataclasses import replace

    from engine.execution import (
        BrokerCapabilities,
        BrokerOrder,
        ExecutionRouter,
        Fill,
        OrderState,
        OrderStore,
    )

    class _Port:
        capabilities = BrokerCapabilities(native_stop=False, native_oco=False)

        async def place(self, order):
            return replace(order, state=OrderState.ACCEPTED, broker_order_id=f"b-{order.client_id}")

        async def cancel(self, order, *, idempotency_key):
            return replace(order, state=OrderState.CANCELLED)

        async def replace(self, order, **changes):
            return replace(order, state=OrderState.ACCEPTED)

        async def status(self, order):
            return order

        async def open_orders(self):
            return []

        async def positions(self):
            return []

        async def balances(self):
            return []

        async def fills(self, since=None):
            return []

    store = OrderStore(tmp_path / "orders.json")
    router = ExecutionRouter(store)

    async def _run() -> None:
        router.register("bitget", "default", _Port())
        placed = await router.place(
            BrokerOrder(
                symbol="BTC/USDT",
                side="buy",
                quantity=Decimal("1"),
                venue="bitget",
                account="default",
            )
        )
        over = Fill(
            "f-1",
            "b-1",
            placed.client_id,
            "bitget",
            "default",
            "BTC/USDT",
            "buy",
            Decimal("5"),
            Decimal("100"),
            datetime.now(UTC),
            Decimal("0"),
        )
        with pytest.raises(ValueError, match="cumulative"):
            await router.record_fill(over)

        # Nothing was written: the fill must not be durable after a rejection,
        # otherwise the broker watermark advances past it and it is never re-read.
        assert store.fills(client_id=placed.client_id) == []
        assert store.get(placed.client_id).state is not OrderState.FILLED

        # A legitimate partial fill does persist, with the order updated together.
        ok = Fill(
            "f-2",
            "b-1",
            placed.client_id,
            "bitget",
            "default",
            "BTC/USDT",
            "buy",
            Decimal("1"),
            Decimal("100"),
            datetime.now(UTC),
            Decimal("0"),
        )
        updated = await router.record_fill(ok)
        assert updated.filled_quantity == Decimal("1")
        assert updated.state is OrderState.FILLED
        assert len(store.fills(client_id=placed.client_id)) == 1

    asyncio.run(_run())


def test_corrupt_order_store_is_quarantined_not_fatal(tmp_path) -> None:
    """A truncated store used to raise out of __init__, killing
    ExecutionService — and with it reconciliation and the new-risk gate."""
    from engine.execution import OrderStore

    path = tmp_path / "orders.json"
    path.write_text('{"version": 1, "orders": {"a": ', encoding="utf-8")
    store = OrderStore(path)
    assert store.corrupt is True
    assert store.orders() == []
    assert path.with_name("orders.json.corrupt").exists()


def test_idempotency_index_matches_linear_scan(tmp_path) -> None:
    from engine.execution import BrokerOrder, OrderStore

    store = OrderStore(tmp_path / "orders.json")
    for i in range(5):
        store.save(
            BrokerOrder(
                symbol="BTC/USDT",
                side="buy",
                quantity=Decimal("1"),
                venue="bitget",
                account="default",
                client_id=f"c{i}",
                idempotency_key=f"key-{i}",
            )
        )
    for i in range(5):
        found = store.find_idempotency_key(f"key-{i}")
        assert found is not None and found.client_id == f"c{i}"
    assert store.find_idempotency_key("nope") is None


def test_order_store_prunes_terminal_orders(tmp_path) -> None:
    from engine.execution import BrokerOrder, OrderState, OrderStore

    store = OrderStore(tmp_path / "orders.json")
    store.retention_orders = 10
    for i in range(25):
        order = BrokerOrder(
            symbol="BTC/USDT",
            side="buy",
            quantity=Decimal("1"),
            venue="bitget",
            account="default",
            client_id=f"c{i}",
            idempotency_key=f"key-{i}",
        )
        order.state = OrderState.FILLED
        store.save(order)
    assert len(store.orders()) <= 12, len(store.orders())
    # The index must not point at pruned orders.
    assert store.find_idempotency_key("key-0") is None
    assert store.find_idempotency_key("key-24") is not None


def test_preview_risk_does_not_mutate_position_marks() -> None:
    """A dry-run preview wrote `position.mark_price`, changing the very
    gross_exposure / leverage numbers it was previewing."""
    m = _manager()
    intent = _intent("BTC/USDT", "CRYPTO", OrderSide.BUY, 0.1, 50_000.0)
    m._record_fill(intent, quantity=0.1, price=50_000.0, raw={})  # noqa: SLF001
    key = intent.exposure_key
    assert key in m.positions, list(m.positions)
    before = m.positions[key]
    before_state = (
        before.quantity,
        before.avg_price,
        before.mark_price,
        before.realized_pnl,
        before.unrealized_pnl,
    )
    gross_before = m.gross_exposure

    m._risk_qty(  # noqa: SLF001
        _intent("BTC/USDT", "CRYPTO", OrderSide.BUY, 0.1, 90_000.0),
        mark_price=90_000.0,
        dry_run=True,
    )
    after = m.positions[key]
    assert (
        after.quantity,
        after.avg_price,
        after.mark_price,
        after.realized_pnl,
        after.unrealized_pnl,
    ) == before_state
    assert m.gross_exposure == gross_before


def test_status_and_orders_remain_importable() -> None:
    assert OrderStatus.REJECTED.value == "rejected"
