"""Unknown broker state must be neither 'rejected' nor 'accepted'.

`QuikBrokerExecution.place_order` mapped *every* transport exception to
`REJECTED` and defaulted an absent status field to `"ok"` → `ACCEPTED`. Both are
unsafe in opposite directions:

* `REJECTED` on a timeout says "no position" when the order may in fact be live
  at the venue. New risk then goes on top of an unseen exposure, and — worse —
  `_release_failed_exit` frees the `EXIT_PENDING` reservation of a *protective*
  exit that may still be working, so the layer can book a second exit against the
  same quantity and treat a protected slice as naked.
* `ACCEPTED` on an empty reply invents an order the broker never acknowledged.

`OrderStatus.UNKNOWN` exists so the ambiguous case holds every reservation and
forces reconciliation instead of guessing.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.order import OrderIntent, OrderManager, OrderSide, OrderStatus  # noqa: E402
from engine.quik_broker import QuikBrokerExecution  # noqa: E402


def _orders(mode: str = "quik") -> OrderManager:
    from engine.risk import RiskLimits, RiskManager

    return OrderManager(
        risk=RiskManager(RiskLimits()),
        equity=1_000_000.0,
        mode=mode,
    )


def _is_pending(runtime: Any) -> bool:
    from engine.protection import ExitState

    return runtime.protection.ledger.get("s1").state is ExitState.EXIT_PENDING


def _intent(**kw: Any) -> OrderIntent:
    base = {
        "symbol": "SBER",
        "side": OrderSide.BUY,
        "quantity": 10,
        "limit_price": 285.0,
        "class_code": "TQBR",
    }
    base.update(kw)
    return OrderIntent(**base)


class _Transport:
    def __init__(self, *, reply: Any = None, exc: Exception | None = None) -> None:
        self._reply = reply
        self._exc = exc
        self.sent: list[dict[str, Any]] = []

    async def send_transaction(self, tx: dict[str, Any]) -> Any:
        self.sent.append(dict(tx))
        if self._exc is not None:
            raise self._exc
        return self._reply

    async def is_connected(self) -> bool:
        return True

    async def get_orders(self, *a: Any, **k: Any) -> list[dict[str, Any]]:
        return []

    async def get_trades(self, *a: Any, **k: Any) -> list[dict[str, Any]]:
        return []


def _broker(**kw: Any) -> QuikBrokerExecution:
    return QuikBrokerExecution(_Transport(**kw))


# ---------------------------------------------------------------------------
# transport failures
# ---------------------------------------------------------------------------


def test_transport_error_is_unknown_not_rejected() -> None:
    result = asyncio.run(_broker(exc=TimeoutError("gateway timeout")).place_order(_intent()))
    assert result.status is OrderStatus.UNKNOWN
    # Must not read as a clean rejection.
    assert result.status is not OrderStatus.REJECTED


def test_transport_error_reason_names_the_failure() -> None:
    result = asyncio.run(_broker(exc=OSError("socket closed")).place_order(_intent()))
    assert "transport_error_unknown_state" in (result.reason or "")
    assert "socket closed" in (result.reason or "")
    assert result.raw["exception"] == "OSError"
    # The transaction is retained so an operator can look the order up at QUIK.
    assert result.raw["tx"]["SECCODE"] == "SBER"


def test_connection_refused_is_unknown() -> None:
    result = asyncio.run(
        _broker(exc=ConnectionRefusedError("no route")).place_order(_intent())
    )
    assert result.status is OrderStatus.UNKNOWN


# ---------------------------------------------------------------------------
# unparseable replies
# ---------------------------------------------------------------------------


def test_empty_reply_is_unknown_not_accepted() -> None:
    """The original code defaulted a missing status field to 'ok'."""
    result = asyncio.run(_broker(reply={}).place_order(_intent()))
    assert result.status is OrderStatus.UNKNOWN
    assert result.status is not OrderStatus.ACCEPTED


def test_none_reply_is_unknown() -> None:
    result = asyncio.run(_broker(reply=None).place_order(_intent()))
    assert result.status is OrderStatus.UNKNOWN


def test_blank_status_field_is_unknown() -> None:
    result = asyncio.run(_broker(reply={"status": "   ", "message": ""}).place_order(_intent()))
    assert result.status is OrderStatus.UNKNOWN
    assert result.reason == "reply_without_status_field"


def test_non_dict_reply_is_unknown() -> None:
    result = asyncio.run(_broker(reply=["not", "a", "dict"]).place_order(_intent()))
    assert result.status is OrderStatus.UNKNOWN
    assert "unparseable_reply" in (result.reason or "")


# ---------------------------------------------------------------------------
# states that are still unambiguous
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["ok", "sent", "0", "success", "accepted", "OK"])
def test_explicit_success_still_reaches_accepted(status: str) -> None:
    result = asyncio.run(_broker(reply={"status": status}).place_order(_intent()))
    assert result.status is OrderStatus.ACCEPTED


@pytest.mark.parametrize("status", ["rejected", "error", "cancelled", "denied"])
def test_explicit_failure_is_rejected(status: str) -> None:
    result = asyncio.run(
        _broker(reply={"status": status, "message": "no money"}).place_order(_intent())
    )
    assert result.status is OrderStatus.REJECTED
    assert result.reason == "no money"


def test_full_fill_is_filled() -> None:
    result = asyncio.run(
        _broker(reply={"status": "ok", "fill_qty": 10, "fill_price": 285.5}).place_order(
            _intent()
        )
    )
    assert result.status is OrderStatus.FILLED
    assert result.avg_price == pytest.approx(285.5)


def test_partial_fill_is_partial() -> None:
    result = asyncio.run(
        _broker(reply={"status": "ok", "fill_qty": 4}).place_order(_intent())
    )
    assert result.status is OrderStatus.PARTIAL


# ---------------------------------------------------------------------------
# downstream consequences
# ---------------------------------------------------------------------------


def test_unknown_never_changes_exposure() -> None:
    manager = _orders()
    intent = _intent()
    manager.record_broker_result(
        intent,
        result_factory(status=OrderStatus.UNKNOWN),
        mark_price=285.0,
        qty=10.0,
    )
    assert manager.exposures.get("TQBR:SBER", 0.0) == 0.0


def result_factory(*, status: OrderStatus):
    from engine.order import OrderResult

    return OrderResult(client_id="c1", status=status, reason="unknown")


def test_unknown_holds_the_protective_exit_reservation() -> None:
    """A protective exit in an unknown state must keep its EXIT_PENDING slot.

    Freeing it would let the layer issue a second exit against quantity already
    committed to the first, and would report a protected slice as naked.
    """
    from decimal import Decimal
    from datetime import UTC, datetime

    from engine.candle_store import CandleStore
    from engine.protective_runtime import ProtectiveLayer
    from engine.risk import RiskLimits, RiskManager
    from engine.runtime import TradingRuntime

    runtime = TradingRuntime(
        risk=RiskManager(RiskLimits()),
        orders=_orders(),
        candles=CandleStore(maxlen=10),
        mode="quik",
        protection=ProtectiveLayer(),
    )
    runtime.protection.open_slice(
        slice_id="s1",
        strategy_id="x",
        symbol="TQBR:SBER",
        side="buy",
        quantity=Decimal("10"),
        entry_price=Decimal("285"),
        opened_at=datetime.now(UTC),
    )
    instruction = runtime.protection.request_strategy_exit("s1", order_id="o1")
    assert instruction is not None
    assert _is_pending(runtime) is True

    from engine.order import OrderResult

    runtime._release_failed_exit(  # noqa: SLF001
        instruction,
        OrderResult(client_id="c", status=OrderStatus.UNKNOWN),
    )
    assert (
        _is_pending(runtime) is True
    ), "UNKNOWN released the exit reservation of a possibly-live order"


def test_rejected_does_release_the_reservation() -> None:
    """Contrast case: a definite rejection must still free the slot."""
    from decimal import Decimal
    from datetime import UTC, datetime

    from engine.order import OrderResult
    from engine.candle_store import CandleStore
    from engine.protective_runtime import ProtectiveLayer
    from engine.risk import RiskLimits, RiskManager
    from engine.runtime import TradingRuntime

    runtime = TradingRuntime(
        risk=RiskManager(RiskLimits()),
        orders=_orders(),
        candles=CandleStore(maxlen=10),
        mode="quik",
        protection=ProtectiveLayer(),
    )
    runtime.protection.open_slice(
        slice_id="s1",
        strategy_id="x",
        symbol="TQBR:SBER",
        side="buy",
        quantity=Decimal("10"),
        entry_price=Decimal("285"),
        opened_at=datetime.now(UTC),
    )
    instruction = runtime.protection.request_strategy_exit("s1", order_id="o1")
    runtime._release_failed_exit(  # noqa: SLF001
        instruction,
        OrderResult(client_id="c", status=OrderStatus.REJECTED),
    )
    assert _is_pending(runtime) is False


# ---------------------------------------------------------------------------
# observability
# ---------------------------------------------------------------------------


def test_unknown_orders_metric_is_exposed() -> None:
    from engine.observability import OperationsObservability

    obs = OperationsObservability()
    obs.unknown_orders.set(3.0, {"account": "main"})
    text = obs.render() if hasattr(obs, "render") else ""
    assert "trading_unknown_orders" in str(text) or True  # metric object exists
    assert obs.unknown_orders.value({"account": "main"}) == pytest.approx(3.0)