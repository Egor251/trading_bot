import asyncio
from dataclasses import replace

from engine.execution import (
    Balance,
    BrokerCapabilities,
    BrokerOrder,
    ExecutionRouter,
    OrderState,
    OrderStore,
    Position,
)
from engine.order import OrderStatus
from engine.runtime import TradingRuntime


class ImmediateFillBroker:
    capabilities = BrokerCapabilities()

    async def place(self, order: BrokerOrder) -> BrokerOrder:
        return replace(
            order,
            state=OrderState.FILLED,
            broker_order_id=f"broker-{order.client_id}",
            filled_quantity=order.quantity,
            average_fill_price=order.limit_price,
        )

    async def cancel(self, order: BrokerOrder, **_: object) -> BrokerOrder:
        return replace(order, state=OrderState.CANCELLED)

    async def replace(self, order: BrokerOrder, **_: object) -> BrokerOrder:
        return order

    async def status(self, order: BrokerOrder) -> BrokerOrder:
        return order

    async def open_orders(self) -> list[BrokerOrder]:
        return []

    async def positions(self) -> list[Position]:
        return []

    async def balances(self) -> list[Balance]:
        return []

    async def fills(self, since=None) -> list:
        return []


def test_runtime_routes_by_venue_and_persists_idempotency(tmp_path) -> None:
    async def scenario() -> None:
        broker = ImmediateFillBroker()
        router = ExecutionRouter(OrderStore(tmp_path / "orders.json"))
        router.register("binance", "sandbox", broker)
        runtime = TradingRuntime.from_config(
            {
                "mode": "live",
                "execution_router": router,
                "venue_by_class": {"CRYPTO": "binance"},
                "account_by_venue": {"binance": "sandbox"},
                "risk": {"max_position_pct": 1, "max_leverage": 2},
            }
        )
        first = await runtime.submit_signal_async(
            ticker="BTC/USDT",
            class_code="CRYPTO",
            action="BUY",
            quantity=1,
            mark_price=100,
            client_id="same-intent",
        )
        repeated = await runtime.submit_signal_async(
            ticker="BTC/USDT",
            class_code="CRYPTO",
            action="BUY",
            quantity=1,
            mark_price=100,
            client_id="same-intent",
        )
        assert first.status is OrderStatus.FILLED
        assert repeated.status is OrderStatus.FILLED
        assert len(router.store.orders()) == 1

    asyncio.run(scenario())
