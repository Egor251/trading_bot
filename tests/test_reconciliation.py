from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.execution import (  # noqa: E402
    Balance,
    BrokerCapabilities,
    BrokerOrder,
    ExecutionRouter,
    Fill,
    OrderState,
    OrderStore,
    Position,
)
from engine.reconciliation import (  # noqa: E402
    IncidentKind,
    ReconciliationService,
    RiskBlocked,
)


class SnapshotBroker:
    capabilities = BrokerCapabilities()

    def __init__(self) -> None:
        self.order_rows: list[BrokerOrder] = []
        self.position_rows: list[Position] = []
        self.balance_rows: list[Balance] = []
        self.fill_rows: list[Fill] = []

    async def place(self, order: BrokerOrder) -> BrokerOrder:
        order.state = OrderState.ACCEPTED
        order.broker_order_id = f"b-{order.client_id}"
        self.order_rows.append(order)
        return order

    async def cancel(self, order: BrokerOrder, **_: object) -> BrokerOrder:
        order.state = OrderState.CANCELLED
        return order

    async def replace(self, order: BrokerOrder, **_: object) -> BrokerOrder:
        return order

    async def status(self, order: BrokerOrder) -> BrokerOrder:
        return order

    async def open_orders(self) -> list[BrokerOrder]:
        return list(self.order_rows)

    async def positions(self) -> list[Position]:
        return list(self.position_rows)

    async def balances(self) -> list[Balance]:
        return list(self.balance_rows)

    async def fills(self, since: datetime | None = None) -> list[Fill]:
        return list(self.fill_rows)


def _setup(tmp_path):
    store = OrderStore(tmp_path / "orders.json")
    router = ExecutionRouter(store)
    broker = SnapshotBroker()
    router.register("fake", "main", broker)
    return store, router, broker


def test_clean_startup_persists_broker_truth(tmp_path) -> None:
    async def scenario() -> None:
        store, router, broker = _setup(tmp_path)
        broker.position_rows = [Position("fake", "main", "AAPL", 2, 100)]
        broker.balance_rows = [Balance("fake", "main", "USD", 1000, 900)]
        service = ReconciliationService(router, store)
        result = await service.startup("fake", "main")
        assert result.healthy
        snapshot = store.get_metadata("reconciliation.snapshot.fake:main")
        assert snapshot["positions"][0]["symbol"] == "AAPL"
        assert snapshot["startup"] is True

    asyncio.run(scenario())


def test_cash_and_position_breaks_block_new_risk_and_recover(tmp_path) -> None:
    async def scenario() -> None:
        store, router, broker = _setup(tmp_path)
        service = ReconciliationService(router, store)
        service.set_expected_state(
            "fake",
            "main",
            positions=[Position("fake", "main", "AAPL", 5)],
            balances=[Balance("fake", "main", "USD", 1000, 1000)],
        )
        broker.position_rows = [Position("fake", "main", "AAPL", 4)]
        broker.balance_rows = [Balance("fake", "main", "USD", 800, 800)]
        result = await service.reconcile("fake", "main")
        assert {i.kind for i in result.incidents} == {
            IncidentKind.POSITION_BREAK,
            IncidentKind.CASH_BREAK,
        }
        assert service.new_risk_blocked
        with pytest.raises(RiskBlocked):
            service.assert_new_risk_allowed()

        recovered = ReconciliationService(router, OrderStore(store.path))
        assert recovered.new_risk_blocked
        broker.position_rows = [Position("fake", "main", "AAPL", 5)]
        broker.balance_rows = [Balance("fake", "main", "USD", 1000, 1000)]
        clean = await recovered.reconcile("fake", "main")
        assert clean.healthy
        assert not recovered.new_risk_blocked

    asyncio.run(scenario())


def test_unmanaged_order_and_fill_emit_structured_incidents(tmp_path) -> None:
    async def scenario() -> None:
        store, router, broker = _setup(tmp_path)
        broker.order_rows = [
            BrokerOrder(
                "AAPL",
                "buy",
                1,
                "fake",
                "main",
                client_id="external",
                broker_order_id="outside-1",
                state=OrderState.ACCEPTED,
            )
        ]
        broker.fill_rows = [
            Fill(
                "outside-fill",
                "outside-1",
                "external",
                "fake",
                "main",
                "AAPL",
                "buy",
                1,
                100,
            )
        ]
        emitted = []
        service = ReconciliationService(
            router, store, incident_sink=lambda incident: emitted.append(incident)
        )
        result = await service.reconcile("fake", "main")
        assert {i.kind for i in result.incidents} == {
            IncidentKind.ORDER_BREAK,
            IncidentKind.FILL_BREAK,
        }
        assert all(i.incident_id and i.key for i in emitted)

    asyncio.run(scenario())


def test_broker_snapshot_error_clears_after_successful_reconcile(tmp_path) -> None:
    async def scenario() -> None:
        store, router, _broker = _setup(tmp_path)
        service = ReconciliationService(router, store)
        fail = {"on": True}

        class Flaky:
            capabilities = BrokerCapabilities()

            async def place(self, order: BrokerOrder) -> BrokerOrder:
                return order

            async def cancel(self, order: BrokerOrder, **_: object) -> BrokerOrder:
                return order

            async def replace(self, order: BrokerOrder, **_: object) -> BrokerOrder:
                return order

            async def status(self, order: BrokerOrder) -> BrokerOrder:
                return order

            async def open_orders(self) -> list[BrokerOrder]:
                if fail["on"]:
                    raise RuntimeError("snapshot down")
                return []

            async def positions(self) -> list[Position]:
                if fail["on"]:
                    raise RuntimeError("snapshot down")
                return []

            async def balances(self) -> list[Balance]:
                if fail["on"]:
                    raise RuntimeError("snapshot down")
                return []

            async def fills(self, since: datetime | None = None) -> list[Fill]:
                if fail["on"]:
                    raise RuntimeError("snapshot down")
                return []

        router.register("down", "main", Flaky())
        broken = await service.reconcile("down", "main")
        assert any(i.kind is IncidentKind.BROKER_ERROR for i in broken.incidents)
        assert service.new_risk_blocked

        fail["on"] = False
        clean = await service.reconcile("down", "main")
        assert clean.healthy
        assert not any(
            i.active and i.kind is IncidentKind.BROKER_ERROR
            for i in service.active_incidents
        )
        assert not service.new_risk_blocked

    asyncio.run(scenario())


def test_broker_fill_repairs_local_cumulative_state_without_duplicates(tmp_path) -> None:
    async def scenario() -> None:
        store, router, broker = _setup(tmp_path)
        order = BrokerOrder(
            "AAPL", "buy", 2, "fake", "main", client_id="managed"
        )
        accepted = await router.place(order)
        broker.order_rows = [accepted]
        broker.fill_rows = [
            Fill(
                "f1",
                accepted.broker_order_id or "",
                "managed",
                "fake",
                "main",
                "AAPL",
                "buy",
                1,
                100,
            )
        ]
        service = ReconciliationService(router, store)
        assert (await service.reconcile("fake", "main")).healthy
        assert store.get("managed").filled_quantity == 1
        assert (await service.reconcile("fake", "main")).healthy
        assert store.get("managed").filled_quantity == 1

    asyncio.run(scenario())
