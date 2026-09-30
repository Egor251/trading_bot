from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.execution import (  # noqa: E402
    AlpacaExecutionAdapter,
    Balance,
    BrokerCapabilities,
    BrokerOrder,
    CcxtExecutionAdapter,
    DuplicateClientId,
    ExecutionRouter,
    Fill,
    IllegalOrderTransition,
    OrderState,
    OrderStore,
    Position,
    QuikExecutionAdapter,
)


class FakeBroker:
    capabilities = BrokerCapabilities(
        native_stop=True, native_oco=True, native_replace=True
    )

    def __init__(self) -> None:
        self.orders: dict[str, BrokerOrder] = {}
        self.fill_rows: list[Fill] = []
        self.place_calls = 0

    async def place(self, order: BrokerOrder) -> BrokerOrder:
        self.place_calls += 1
        result = replace(
            order,
            state=OrderState.ACCEPTED,
            broker_order_id=f"broker-{order.client_id}",
        )
        self.orders[order.client_id] = result
        return result

    async def cancel(
        self, order: BrokerOrder, *, idempotency_key: str
    ) -> BrokerOrder:
        result = replace(order, state=OrderState.CANCELLED)
        self.orders[order.client_id] = result
        return result

    async def replace(self, order: BrokerOrder, **changes: object) -> BrokerOrder:
        result = replace(
            order,
            quantity=changes.get("quantity") or order.quantity,
            limit_price=(
                changes["limit_price"]
                if changes.get("limit_price") is not None
                else order.limit_price
            ),
            state=OrderState.ACCEPTED,
        )
        self.orders[order.client_id] = result
        return result

    async def status(self, order: BrokerOrder) -> BrokerOrder:
        return self.orders[order.client_id]

    async def open_orders(self) -> list[BrokerOrder]:
        return [
            item
            for item in self.orders.values()
            if item.state in {OrderState.ACCEPTED, OrderState.PARTIAL}
        ]

    async def positions(self) -> list[Position]:
        return []

    async def balances(self) -> list[Balance]:
        return []

    async def fills(self, since: datetime | None = None) -> list[Fill]:
        return list(self.fill_rows)


def _order(client_id: str = "client-1") -> BrokerOrder:
    return BrokerOrder(
        "BTC/USDT",
        "buy",
        "2",
        "fake",
        "paper",
        client_id=client_id,
        order_type="limit",
        limit_price="100",
    )


def test_fake_broker_satisfies_execution_contract(tmp_path) -> None:
    async def scenario() -> None:
        broker = FakeBroker()
        router = ExecutionRouter(OrderStore(tmp_path / "orders.json"))
        router.register("fake", "paper", broker)
        accepted = await router.place(_order())
        assert accepted.state is OrderState.ACCEPTED
        assert accepted.broker_order_id == "broker-client-1"
        assert router.capabilities("fake", "paper").native_oco
        assert (await router.open_orders("fake", "paper"))[0].client_id == accepted.client_id
        assert await router.positions("fake", "paper") == []
        assert await router.balances("fake", "paper") == []
        assert await router.fills("fake", "paper") == []

    asyncio.run(scenario())


def test_duplicate_client_id_is_idempotent_or_rejected(tmp_path) -> None:
    async def scenario() -> None:
        broker = FakeBroker()
        router = ExecutionRouter(OrderStore(tmp_path / "orders.json"))
        router.register("fake", "paper", broker)
        first = _order()
        assert await router.place(first) is first
        assert await router.place(first) is not None
        assert broker.place_calls == 1
        with pytest.raises(DuplicateClientId):
            await router.place(
                BrokerOrder(
                    "ETH/USDT", "buy", 1, "fake", "paper", client_id="client-1"
                )
            )

    asyncio.run(scenario())


def test_partial_fills_are_cumulative_and_deduplicated(tmp_path) -> None:
    async def scenario() -> None:
        store = OrderStore(tmp_path / "orders.json")
        router = ExecutionRouter(store)
        router.register("fake", "paper", FakeBroker())
        await router.place(_order())
        one = Fill(
            "fill-1",
            "broker-client-1",
            "client-1",
            "fake",
            "paper",
            "BTC/USDT",
            "buy",
            "0.5",
            "100",
        )
        partial = await router.record_fill(one)
        assert partial.state is OrderState.PARTIAL
        assert partial.filled_quantity == Decimal("0.5")
        duplicate = await router.record_fill(one)
        assert duplicate.filled_quantity == Decimal("0.5")
        filled = await router.record_fill(
            replace(one, fill_id="fill-2", quantity=Decimal("1.5"), price=Decimal("102"))
        )
        assert filled.state is OrderState.FILLED
        assert filled.average_fill_price == Decimal("101.5")
        assert len(store.fills(client_id="client-1")) == 2

    asyncio.run(scenario())


def test_cancel_and_revisioned_replace_survive_restart(tmp_path) -> None:
    async def scenario() -> None:
        path = tmp_path / "orders.json"
        broker = FakeBroker()
        router = ExecutionRouter(OrderStore(path))
        router.register("fake", "paper", broker)
        await router.place(_order("replace-me"))
        changed = await router.replace(
            "replace-me", quantity="3", limit_price="99"
        )
        assert changed.revision == 1
        assert changed.idempotency_key.endswith(":replace:1")
        assert changed.quantity == Decimal("3")
        recovered = OrderStore(path).get("replace-me")
        assert recovered is not None and recovered.revision == 1
        cancelled = await router.cancel("replace-me")
        assert cancelled.state is OrderState.CANCELLED
        with pytest.raises(IllegalOrderTransition):
            await router.replace("replace-me", quantity=4)

    asyncio.run(scenario())


def test_ccxt_translation_uses_client_id_and_stop() -> None:
    class Client:
        async def create_order(self, *args):
            self.args = args
            return {
                "id": "x1",
                "clientOrderId": args[-1]["clientOrderId"],
                "symbol": args[0],
                "side": args[2],
                "amount": args[3],
                "type": args[1],
                "status": "open",
                "filled": 0,
            }

    async def scenario() -> None:
        client = Client()
        adapter = CcxtExecutionAdapter(client, venue="binance", account="spot")
        order = BrokerOrder(
            "BTC/USDT",
            "sell",
            2,
            "binance",
            "spot",
            client_id="ccxt-id",
            order_type="stop",
            stop_price=90,
        )
        result = await adapter.place(order)
        assert result.state is OrderState.ACCEPTED
        assert client.args[-1] == {"clientOrderId": "ccxt-id", "stopPrice": 90.0}

    asyncio.run(scenario())


def test_alpaca_and_quik_translation_with_injected_clients() -> None:
    class AlpacaClient:
        def submit_order(self, request):
            self.request = request
            return {
                "id": "a1",
                "client_order_id": request["client_order_id"],
                "symbol": request["symbol"],
                "side": request["side"],
                "qty": request["qty"],
                "type": request["type"],
                "status": "new",
            }

    class QuikClient:
        async def send_transaction(self, tx):
            self.tx = tx
            return {"result": "ok", "order_num": "42"}

    async def scenario() -> None:
        alpaca_client = AlpacaClient()
        alpaca = AlpacaExecutionAdapter(alpaca_client, account="paper")
        result = await alpaca.place(
            BrokerOrder("AAPL", "buy", 1, "alpaca", "paper", client_id="a")
        )
        assert result.state is OrderState.ACCEPTED
        assert alpaca_client.request["client_order_id"] == "a"

        quik_client = QuikClient()
        quik = QuikExecutionAdapter(quik_client, account="L01")
        q_result = await quik.place(
            BrokerOrder(
                "SBER",
                "sell",
                3,
                "quik",
                "L01",
                client_id="q",
                limit_price=300,
            )
        )
        assert q_result.broker_order_id == "42"
        assert quik_client.tx["ACTION"] == "NEW_ORDER"
        assert quik_client.tx["OPERATION"] == "S"

    asyncio.run(scenario())


def test_cancel_purges_unknown_local_orphan(tmp_path) -> None:
    """UNKNOWN + no broker_order_id → local CANCELLED (no broker call)."""
    store = OrderStore(tmp_path / "orders.json")
    router = ExecutionRouter(store)
    broker = FakeBroker()
    router.register("quik", "L01", broker)
    orphan = BrokerOrder(
        "SBER",
        "buy",
        "17.5",
        "quik",
        "L01",
        client_id="orphan-1",
        limit_price="285.0",
        state=OrderState.UNKNOWN,
        broker_order_id=None,
    )
    store.save(orphan)

    async def scenario() -> None:
        out = await router.cancel("orphan-1")
        assert out.state is OrderState.CANCELLED
        assert store.get("orphan-1").state is OrderState.CANCELLED
        assert broker.place_calls == 0
        assert "orphan-1" not in broker.orders

    asyncio.run(scenario())
