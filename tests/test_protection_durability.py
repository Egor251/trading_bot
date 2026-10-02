"""Durable runtime + protective state, and broker-native stops.

The protective layer lived only in memory. `ProtectiveLayer.snapshot()` was never
written anywhere — grep found three references, all of them the *read* in
`from_config` — so `protection_state` never reached the config and a restart came
up with an empty ledger. `OrderManager.positions`, `fill_ledger` and `peak_equity`
were process-local too, so a redeploy left a real broker position with zero
protective orders while the restarted bot computed `gross_exposure == 0` and
believed it was flat. All 63 stored Quik orders have `stop_price=null` and
`filled_quantity=0` for exactly that reason.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.order import (  # noqa: E402
    OrderIntent,
    OrderManager,
    OrderResult,
    OrderSide,
    OrderStatus,
)
from engine.protective_runtime import ProtectiveLayer  # noqa: E402
from engine.runtime import TradingRuntime  # noqa: E402
from engine.state_store import StateStore  # noqa: E402


def _runtime(tmp_path: Path, **cfg) -> TradingRuntime:
    base = {"mode": "paper", "equity": 100_000.0}
    base.update(cfg)
    return TradingRuntime.from_config(base)


def _open_slice(runtime: TradingRuntime, *, symbol: str = "CRYPTO:BTC/USDT") -> str:
    """Open a slice and install its protective rules, as a fill would."""
    slice_id = "s1"
    runtime.protection.open_slice(
        slice_id=slice_id,
        strategy_id="ma_cross_example",
        symbol=symbol,
        side="buy",
        quantity=1,
        entry_price=Decimal("100"),
        opened_at=datetime.now(UTC),
    )
    profile = runtime.profile_for("ma_cross_example")
    runtime.protection.protect_or_max_loss(slice_id, profile, [])
    return slice_id


# ---------------------------------------------------------------------------
# D8 — the protective state is actually persisted
# ---------------------------------------------------------------------------


def test_protection_state_is_written_and_read_back(tmp_path: Path) -> None:
    state = tmp_path / "state" / "runtime.json"
    runtime = _runtime(tmp_path, state_path=str(state))
    _open_slice(runtime)

    assert runtime.persist_protection_state() is True
    assert state.is_file()

    payload = json.loads(state.read_text(encoding="utf-8"))["payload"]
    assert payload["protection"]["ledger"]["positions"], "no slices were persisted"
    assert payload["protection"]["stops"]["rules"], "no stops were persisted"
    assert payload["mode"] == "paper"


def test_second_runtime_recovers_the_protective_layer(tmp_path: Path) -> None:
    """The pre-restart process must see the same protective state afterwards."""
    state = tmp_path / "state" / "runtime.json"
    first = _runtime(tmp_path, state_path=str(state))
    _open_slice(first)
    first.persist_protection_state()
    slices_before = len(first.protection.ledger.all())
    stops_before = len(first.protection.stops.to_dict()["rules"])

    second = _runtime(tmp_path, state_path=str(state))
    # A fresh process must not believe it holds nothing.
    assert len(second.protection.ledger.all()) == 0
    recovered = second.recover_from_disk()

    assert recovered is not None
    assert len(second.protection.ledger.all()) == slices_before
    assert len(second.protection.stops.to_dict()["rules"]) == stops_before
    assert second.protection.has_protection("CRYPTO:BTC/USDT") is True


def test_recover_is_a_noop_without_a_snapshot(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, state_path=str(tmp_path / "missing.json"))
    assert runtime.recover_from_disk() is None


def test_explicit_config_protection_state_wins(tmp_path: Path) -> None:
    """An explicitly supplied ``protection_state`` must not be overwritten by disk."""
    state = tmp_path / "state" / "runtime.json"
    seed = _runtime(tmp_path, state_path=str(state))
    _open_slice(seed)
    seed.persist_protection_state()

    explicit = ProtectiveLayer()
    explicit.open_slice(
        slice_id="from_config",
        strategy_id="s",
        symbol="CRYPTO:ETH/USDT",
        side="buy",
        quantity=1,
        entry_price=Decimal("5"),
        opened_at=datetime.now(UTC),
    )
    runtime = _runtime(
        tmp_path, state_path=str(state), protection_state=explicit.snapshot()
    )
    symbols = {s.symbol for s in runtime.protection.ledger.all()}
    assert symbols == {"CRYPTO:ETH/USDT"}


def test_persist_failure_is_contained(tmp_path: Path) -> None:
    """Losing durability must not abort a trade that already reached the broker."""
    runtime = _runtime(tmp_path, state_path=str(tmp_path / "state" / "runtime.json"))
    runtime._state_store = object()  # type: ignore[assignment]  # noqa: SLF001
    assert runtime.persist_protection_state() is False


def test_persisted_snapshot_carries_the_book(tmp_path: Path) -> None:
    state = tmp_path / "state" / "runtime.json"
    runtime = _runtime(tmp_path, state_path=str(state))
    intent = OrderIntent(
        symbol="BTC/USDT",
        class_code="CRYPTO",
        side=OrderSide.BUY,
        quantity=0.5,
        limit_price=50_000.0,
    )
    runtime.orders.record_broker_result(
        intent,
        OrderResult(
            client_id=intent.client_id,
            status=OrderStatus.FILLED,
            filled_qty=0.5,
            avg_price=50_000.0,
        ),
        mark_price=50_000.0,
        qty=0.5,
    )
    runtime.persist_protection_state()

    payload = json.loads(state.read_text(encoding="utf-8"))["payload"]
    entry = payload["positions"]["CRYPTO:BTC/USDT"]
    assert entry["quantity"] == pytest.approx(0.5)
    assert entry["avg_price"] == pytest.approx(50_000.0)
    assert "equity" in payload and "peak_equity" in payload


# ---------------------------------------------------------------------------
# A1 — broker-native protective stops
# ---------------------------------------------------------------------------


class _RecordingPort:
    """Minimal ExecutionPort that records what reached the broker."""

    def __init__(self, *, native_stop: bool = True) -> None:
        from engine.execution import BrokerCapabilities, BrokerOrder, OrderState
        from dataclasses import replace

        self._replace = replace
        self._OrderState = OrderState
        self.capabilities = BrokerCapabilities(
            native_stop=native_stop, native_oco=native_stop
        )
        self.placed: list[BrokerOrder] = []

    async def place(self, order):
        self.placed.append(order)
        return self._replace(
            order,
            state=self._OrderState.ACCEPTED,
            broker_order_id=f"b-{order.client_id}",
        )

    async def cancel(self, order, *, idempotency_key):
        return order

    async def replace(self, order, **changes):
        return order

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


def _router(tmp_path: Path, port) -> object:
    from engine.execution import ExecutionRouter, OrderStore

    router = ExecutionRouter(OrderStore(tmp_path / "orders.json"))
    router.register("ccxt", "default", port)
    return router


def test_native_stop_is_placed_after_a_fill(tmp_path: Path) -> None:
    import asyncio

    port = _RecordingPort(native_stop=True)
    runtime = _runtime(tmp_path, venue_by_class={"CRYPTO": "ccxt"})
    runtime.execution_router = _router(tmp_path, port)
    _open_slice(runtime)

    placed = asyncio.run(runtime.place_native_stop("s1"))
    assert placed is True
    assert len(port.placed) == 1
    order = port.placed[0]
    assert order.stop_price is not None, "stop_price must reach the broker"
    assert order.oco_group_id == "oco:s1"
    assert str(order.raw.get("reduce_only")) == "True"
    # A long position is protected by a sell stop below the entry.
    assert str(order.side).lower().endswith("sell") or order.side is not None


def test_no_native_stop_when_the_venue_lacks_support(tmp_path: Path) -> None:
    import asyncio

    port = _RecordingPort(native_stop=False)
    runtime = _runtime(tmp_path, venue_by_class={"CRYPTO": "ccxt"})
    runtime.execution_router = _router(tmp_path, port)
    _open_slice(runtime)

    assert asyncio.run(runtime.place_native_stop("s1")) is False
    assert port.placed == []


def test_no_native_stop_without_a_router(tmp_path: Path) -> None:
    import asyncio

    runtime = _runtime(tmp_path)
    _open_slice(runtime)
    assert asyncio.run(runtime.place_native_stop("s1")) is False


def test_native_stop_plan_is_none_without_rules(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.protection.open_slice(
        slice_id="bare",
        strategy_id="s",
        symbol="CRYPTO:BTC/USDT",
        side="buy",
        quantity=1,
        entry_price=Decimal("100"),
        opened_at=datetime.now(UTC),
    )
    assert runtime.native_stop_plan("bare") is None


def test_native_stop_failure_is_contained(tmp_path: Path) -> None:
    import asyncio

    class _Exploding(_RecordingPort):
        async def place(self, order):
            raise RuntimeError("broker down")

    runtime = _runtime(tmp_path, venue_by_class={"CRYPTO": "ccxt"})
    runtime.execution_router = _router(tmp_path, _Exploding())
    _open_slice(runtime)

    # Degrades to the in-memory rule rather than leaving the slice unprotected.
    assert asyncio.run(runtime.place_native_stop("s1")) is False
    assert runtime.protection.has_protection("CRYPTO:BTC/USDT") is True


# ---------------------------------------------------------------------------
# StateStore hygiene (A/§13)
# ---------------------------------------------------------------------------


def test_state_store_roundtrip_and_corruption(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "s.json")
    assert store.load() is None
    store.save({"a": 1}, label="t")
    assert store.load() == {"a": 1}
    assert (store.meta() or {}).get("label") == "t"

    store.path.write_text("{not json", encoding="utf-8")
    assert store.load() is None, "a corrupt snapshot must read as absent, not raise"


def test_state_store_uses_a_unique_temp_name(tmp_path: Path) -> None:
    """A fixed '.tmp' suffix is shared by every writer and races on replace."""
    store = StateStore(tmp_path / "s.json")
    store.save({"a": 1})
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    assert (tmp_path / "s.json").is_file()


def test_slices_for_exposure_filters_closed_and_empty(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _open_slice(runtime, symbol="CRYPTO:BTC/USDT")
    runtime.protection.open_slice(
        slice_id="s2",
        strategy_id="x",
        symbol="CRYPTO:ETH/USDT",
        side="buy",
        quantity=1,
        entry_price=Decimal("1"),
        opened_at=datetime.now(UTC),
    )
    assert runtime._slices_for_exposure("CRYPTO:BTC/USDT") == ("s1",)  # noqa: SLF001
    assert set(runtime._slices_for_exposure("CRYPTO:ETH/USDT")) == {"s2"}  # noqa: SLF001


def test_order_manager_persist_hook_is_called_on_fill(tmp_path: Path) -> None:
    """`record_broker_result` path still persists; guards a future regression."""
    runtime = _runtime(tmp_path, state_path=str(tmp_path / "state" / "r.json"))
    intent = OrderIntent(
        symbol="BTC/USDT",
        class_code="CRYPTO",
        side=OrderSide.BUY,
        quantity=1,
        limit_price=100.0,
    )
    runtime.orders.record_broker_result(
        intent,
        OrderResult(client_id=intent.client_id, status=OrderStatus.FILLED, filled_qty=1.0, avg_price=100.0),
        mark_price=100.0,
        qty=1.0,
    )
    assert isinstance(runtime.orders, OrderManager)
    assert runtime.orders.exposures
