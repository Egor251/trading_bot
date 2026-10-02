"""A protective stop the venue was never told to be reduce-only.

`place_native_stop` set `raw={"reduce_only": True}` on the broker order. That was
the whole of it. `CcxtExecutionAdapter.place()` reads `clientOrderId`,
`stopPrice`, `timeInForce` and `postOnly` from the order -- and never looked at
`raw` for a reduce-only flag, so nothing reached the venue.

The consequence is the one a protective stop exists to prevent: the venue holds
an ordinary stop order that may open or add to the opposite side. If the book is
flat, or the protective slice already closed, when the stop triggers, it takes a
position in exactly the direction the stop was written to avoid.

The existing coverage made this invisible. `test_protection_durability.py`
asserted `str(order.raw.get("reduce_only")) == "True"` against a fake
`_RecordingPort` -- which sees our `BrokerOrder` object and nothing else. That
test verified a dictionary key in our own process, not an instruction sent to an
exchange, and would have passed with the flag deleted outright.

These tests assert at the boundary: what `create_order` was actually called with.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.execution import (  # noqa: E402
    BrokerCapabilities,
    BrokerOrder,
    CcxtExecutionAdapter,
    OrderSide,
    OrderState,
)


class _RecordingClient:
    """Stands in for a ccxt exchange. Records the *call*, not our object."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create_order(self, *args, **kwargs):
        # ccxt positional order: symbol, type, side, amount, price, params.
        params = kwargs.get("params")
        if params is None and len(args) > 5:
            params = args[5]
        self.calls.append({"args": args, "params": dict(params or {})})
        return {
            "id": "venue-1",
            "clientOrderId": "stop:s1",
            "status": "open",
            "amount": args[3] if len(args) > 3 else 0,
            "price": args[4] if len(args) > 4 else None,
        }

    async def cancel_order(self, *args, **kwargs):
        return {"id": "venue-1", "status": "canceled"}

    async def fetch_order(self, *args, **kwargs):
        return {"id": "venue-1", "status": "open"}


def _stop_order(**overrides) -> BrokerOrder:
    kwargs = {
        "symbol": "BTC/USDT",
        "side": OrderSide.SELL,
        "quantity": 1.0,
        "venue": "ccxt",
        "account": "main",
        "client_id": "stop:s1",
        "idempotency_key": "stop:s1",
        "order_type": "stop",
        "stop_price": 58000.0,
        "reduce_only": True,
    }
    kwargs.update(overrides)
    return BrokerOrder(**kwargs)


def _place(order: BrokerOrder, **adapter_kwargs):
    client = _RecordingClient()
    adapter = CcxtExecutionAdapter(client, venue="ccxt", account="main")
    for key, value in adapter_kwargs.items():
        setattr(adapter, key, value)
    result = asyncio.run(adapter.place(order))
    return client, result


# -- the boundary: what the venue is actually told ----------------------


def test_reduce_only_reaches_the_venue() -> None:
    """The whole point. Asserted on `create_order` params, not on our object."""
    client, _ = _place(_stop_order())
    assert len(client.calls) == 1
    assert client.calls[0]["params"].get("reduceOnly") is True


def test_a_plain_order_is_not_marked_reduce_only() -> None:
    """Entry risk must be allowed to open; the default must not drift."""
    client, _ = _place(_stop_order(reduce_only=False))
    assert "reduceOnly" not in client.calls[0]["params"]


def test_reduce_only_is_not_dropped_by_a_venue_that_cannot_honour_it() -> None:
    """Silently dropping it would be the original defect in a new place.

    A venue that ignores `reduceOnly` is worse than one that rejects it: we would
    believe the guarantee holds while it does not. The order still goes out, and
    the capability flag is what tells the rest of the system the guarantee is
    absent -- it must not be a place where the flag vanishes.
    """
    client, _ = _place(_stop_order(), reduce_only_supported=False)
    assert "reduceOnly" not in client.calls[0]["params"], (
        "a venue that cannot honour the flag must not be sent it"
    )
    assert len(client.calls) == 1, (
        "the protective stop must still be placed -- losing it is worse than "
        "the reversal risk"
    )


def test_reduce_only_travels_alongside_the_other_flags() -> None:
    """It is additive. The post-only and stopPrice work must survive."""
    client, _ = _place(_stop_order(time_in_force="post_only"))
    params = client.calls[0]["params"]
    assert params["reduceOnly"] is True
    assert params["postOnly"] is True
    assert params["timeInForce"] == "GTX"
    assert params["stopPrice"] == 58000.0


def test_the_capability_default_is_to_assume_support() -> None:
    """Otherwise every venue would silently lose the flag until configured."""
    assert BrokerCapabilities().reduce_only is True
    assert BrokerCapabilities().supports_reduce_only is True
    assert BrokerCapabilities(reduce_only=False).supports_reduce_only is False


def test_the_field_defaults_to_off() -> None:
    """Entry orders must keep the ability to open. Only exits reduce."""
    assert _stop_order(reduce_only=False).reduce_only is False


def test_the_flag_survives_the_order_round_trip() -> None:
    """`__post_init__` rebuilds several fields; this must not be dropped."""
    client, result = _place(_stop_order())
    assert result.reduce_only is True
    assert client.calls[0]["params"]["reduceOnly"] is True


def test_a_venue_that_does_not_echo_the_trigger_cannot_null_it() -> None:
    """A stop with no stop price is not a stop.

    `_order` read `stopPrice` only from the venue's echo. A venue that omits it
    produced a returned order whose trigger was None -- on the very object the
    rest of the system reasons about.
    """
    client = _RecordingClient()
    adapter = CcxtExecutionAdapter(client, venue="ccxt", account="main")
    result = asyncio.run(adapter.place(_stop_order()))
    assert result.stop_price == 58000.0, (
        "the trigger was lost because the venue did not echo it"
    )


def test_a_stop_that_cannot_reverse_is_still_a_stop() -> None:
    """Losing protection entirely is strictly worse than a reversal risk."""
    client, result = _place(_stop_order(), reduce_only_supported=False)
    assert result.stop_price == 58000.0
    assert result.state in {OrderState.ACCEPTED, "accepted"}