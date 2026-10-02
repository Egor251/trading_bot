"""Resting orders, maker fees, and carry costs.

Three gaps found by walking the live path rather than the phantom, each of which
the system could not currently express at all.

**Stale orders never came back.** The only cleanup purged *local* orphans —
records with no broker order id. An order the venue was genuinely holding was
never reclaimed, so on a venue that ignores ``time_in_force`` a signal could
fill an hour after it stopped mattering. `ExecutionRouter.sweep_stale` now
reclaims them, and deliberately exempts protective orders: a protective stop
left resting is the one order whose expiry would strip protection from a live
position.

**Post-only could not be requested.** `CcxtExecutionAdapter.place` forwarded
only `clientOrderId` and `stopPrice`, so every order went out as a plain limit
and the venue charged the taker fee. Maker 0–2 bps against taker 5 bps decides
whether a strategy earns at all.

**Funding is modelled but never accrued.** `Portfolio.apply_funding` and
`settle_variation_margin` exist and are covered by the portfolio ledger test, yet
nothing in the live loop calls them. `OrderManager.equity_ex_unrealized` is
`starting_equity + realized − total_costs`, and `total_costs` is a sum over the
*fill* ledger — funding is not a fill, so even wiring the existing method would
not have reached equity. It is a separate carry line, and equity must subtract it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.execution import (  # noqa: E402
    BrokerOrder,
    CcxtExecutionAdapter,
    ExecutionRouter,
    OrderSide,
    OrderState,
    OrderStore,
)


def _router(tmp_path: Path, adapter) -> ExecutionRouter:
    router = ExecutionRouter(OrderStore(tmp_path / "orders.json"))
    router.register("bitget", "default", adapter)  # type: ignore[attr-defined]
    return router


class _RecordingAdapter:
    """Minimal port that records what the router asked the venue to do."""

    capabilities = None

    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.cancel_error: Exception | None = None

    async def place(self, order: BrokerOrder) -> BrokerOrder:
        order.state = OrderState.ACCEPTED
        order.broker_order_id = "v1"
        return order

    async def cancel(self, order: BrokerOrder, *, idempotency_key: str) -> BrokerOrder:
        if self.cancel_error is not None:
            raise self.cancel_error
        self.cancelled.append(order.client_id)
        order.state = OrderState.CANCELLED
        order.broker_order_id = order.broker_order_id or "v1"
        return order

    async def open_orders(self) -> list[BrokerOrder]:
        return []

    async def positions(self) -> list:
        return []

    async def balances(self) -> list:
        return []

    async def fills(self, since=None) -> list:  # noqa: ANN001
        return []

    async def status(self, order: BrokerOrder) -> BrokerOrder:
        return order


def _order(client_id: str, *, age_sec: float, **kw) -> BrokerOrder:
    stamp = datetime.now(UTC) - timedelta(seconds=age_sec)
    return BrokerOrder(
        symbol=kw.pop("symbol", "BTC/USDT"),
        side=OrderSide.BUY,
        quantity=kw.pop("quantity", "0.01"),
        venue="bitget",
        account="default",
        client_id=client_id,
        order_type=kw.pop("order_type", "limit"),
        limit_price=kw.pop("limit_price", "50000"),
        state=kw.pop("state", OrderState.ACCEPTED),
        created_at=stamp,
        updated_at=stamp,
        **kw,
    )


# ---------------------------------------------------------------------------
# TTL sweep
# ---------------------------------------------------------------------------


def test_stale_order_is_cancelled_after_its_ttl(tmp_path: Path) -> None:
    async def scenario() -> list[str]:
        adapter = _RecordingAdapter()
        router = _router(tmp_path, adapter)
        router.store.save(_order("old", age_sec=3600))
        router.store.save(_order("young", age_sec=5))
        cancelled = await router.sweep_stale(900)
        return [o.client_id for o in cancelled], adapter.cancelled

    cancelled, venue = asyncio.run(scenario())
    assert cancelled == ["old"], "only the expired order may be swept"
    assert venue == ["old"], "the venue must actually be asked to cancel"


def test_ttl_sweep_is_off_when_ttl_is_zero(tmp_path: Path) -> None:
    async def scenario() -> list[str]:
        adapter = _RecordingAdapter()
        router = _router(tmp_path, adapter)
        router.store.save(_order("old", age_sec=100_000))
        return [o.client_id for o in await router.sweep_stale(0)]

    assert asyncio.run(scenario()) == []


def test_protective_orders_are_not_swept(tmp_path: Path) -> None:
    """A protective stop is the one order whose expiry removes protection."""

    async def scenario() -> list[str]:
        adapter = _RecordingAdapter()
        router = _router(tmp_path, adapter)
        router.store.save(
            _order("stop", age_sec=10_000, order_type="stop", stop_price="40000")
        )
        return [o.client_id for o in await router.sweep_stale(900)]

    assert asyncio.run(scenario()) == []


def test_a_venue_failure_does_not_record_a_cancellation(tmp_path: Path) -> None:
    """Recording a cancel that never reached the venue is the bug UNKNOWN fixed.

    Marking an order cancelled because the cancel call raised would release the
    reservation while the venue may still hold the order.
    """

    async def scenario() -> tuple[list[str], str | None]:
        adapter = _RecordingAdapter()
        adapter.cancel_error = TimeoutError("venue unreachable")
        router = _router(tmp_path, adapter)
        router.store.save(_order("old", age_sec=3600))
        cancelled = await router.sweep_stale(900)
        stored = router.store.get("old")
        return [o.client_id for o in cancelled], (
            stored.state.value if stored is not None else None
        )

    cancelled, state = asyncio.run(scenario())
    assert cancelled == []
    assert state == "accepted", "an unreachable venue must leave the order live"


# ---------------------------------------------------------------------------
# post-only
# ---------------------------------------------------------------------------


class _FakeCcxtClient:
    def __init__(self) -> None:
        self.last_params: dict | None = None

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):  # noqa: ANN001
        self.last_params = dict(params or {})
        return {
            "id": "1",
            "clientOrderId": (params or {}).get("clientOrderId"),
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "price": price,
            "status": "open",
        }


def test_post_only_is_requested_so_the_maker_fee_is_reachable() -> None:
    async def scenario() -> dict:
        client = _FakeCcxtClient()
        adapter = CcxtExecutionAdapter(client, venue="bitget", account="default")
        await adapter.place(
            _order("po", age_sec=0, time_in_force="post_only")
        )
        return client.last_params or {}

    params = asyncio.run(scenario())
    assert params.get("postOnly") is True
    assert str(params.get("timeInForce", "")).upper() == "GTX", (
        "post-only without GTX is advisory; the venue may still cross the book"
    )


def test_a_plain_limit_order_is_not_made_post_only() -> None:
    async def scenario() -> dict:
        client = _FakeCcxtClient()
        adapter = CcxtExecutionAdapter(client, venue="bitget", account="default")
        await adapter.place(_order("plain", age_sec=0, time_in_force="day"))
        return client.last_params or {}

    params = asyncio.run(scenario())
    assert "postOnly" not in params
    assert "timeInForce" not in params


# ---------------------------------------------------------------------------
# carry costs reach equity
# ---------------------------------------------------------------------------


def _open(om, symbol: str, qty: float, price: float):  # noqa: ANN001
    """Put an on-book position on the manager the way a fill would."""
    from engine.order import Position

    position = om.positions.get(symbol) or Position(
        symbol=symbol, quantity=0.0, avg_price=0.0
    )
    om.positions[symbol] = position
    position.apply_fill(qty, price)
    return position


def test_funding_is_a_carry_cost_not_a_fill_cost() -> None:
    """`total_costs` sums fills, so funding must be a separate line.

    Wiring `Portfolio.apply_funding` alone would not have reached equity: a
    funding charge is not a fill and never appears in the fill ledger.
    """
    from engine.order import OrderManager
    from engine.risk import RiskLimits, RiskManager

    om = OrderManager(RiskManager(RiskLimits()), equity=10_000.0)
    _open(om, "BTC/USDT", 0.01, 50_000.0)
    equity_before = float(om.equity_ex_unrealized)

    om.apply_carry("BTC/USDT", 0.01)
    equity_after = float(om.equity_ex_unrealized)

    assert equity_after < equity_before, "funding must reduce equity"
    charged = equity_before - equity_after
    assert charged == 5.0, f"notional 500 x rate 0.01 = 5.0 expected, got {charged}"
    assert float(om.total_costs) == 0.0, "carry is not a fill cost"
    assert float(om.carry_costs) == 5.0


def test_carry_charges_a_short_holder() -> None:
    """Positive funding pays shorts; the sign must follow the position."""
    from engine.order import OrderManager
    from engine.risk import RiskLimits, RiskManager

    om = OrderManager(RiskManager(RiskLimits()), equity=10_000.0)
    _open(om, "BTC/USDT", -0.01, 50_000.0)
    before = float(om.equity_ex_unrealized)
    om.apply_carry("BTC/USDT", "0.01")
    assert float(om.equity_ex_unrealized) > before, "a short should be credited"


def test_carry_for_an_unknown_symbol_is_ignored_not_fatal() -> None:
    """A settlement sweep must not die on a position that just closed."""
    from engine.order import OrderManager
    from engine.risk import RiskLimits, RiskManager

    om = OrderManager(RiskManager(RiskLimits()), equity=10_000.0)
    before = float(om.equity_ex_unrealized)
    assert om.apply_carry("ETH/USDT", "0.01") is None
    assert float(om.equity_ex_unrealized) == before