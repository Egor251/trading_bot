"""Funding must reach equity, and must not invent itself where none exists.

The accounting was already in place and correct — `apply_carry` subtracts from
both equity paths — but nothing ever called it. `Portfolio.apply_funding` had
never been the answer either: `Portfolio` is not instantiated by the runtime,
which books through `OrderManager`.

The driver is the hard part, because the honest behaviours differ by venue:

* A perpetual venue quotes a rate and carry must be charged every interval.
* A spot venue quotes none, and `fetch_funding_rate` raises there rather than
  returning zero. A zero would be indistinguishable from "carry is free" and
  would let equity drift away from the base that risk limits are computed on.

So absence has to be returned explicitly and must never be coerced to `0.0`.

The task also sleeps for minutes between passes, so shutdown must cancel it
rather than drop the reference: a surviving task would keep accruing against a
runtime the operator believes is stopped.
"""

from __future__ import annotations

import asyncio

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.adapters.ccxt_adapter import CcxtMarketDataAdapter  # noqa: E402


class _Exchange:
    """Stands in for a ccxt exchange whose funding behaviour we choose."""

    def __init__(self, behaviour: str) -> None:
        self.behaviour = behaviour
        self.calls = 0

    async def fetch_funding_rate(self, market):  # noqa: ANN001
        self.calls += 1
        if self.behaviour == "raises":
            raise RuntimeError("does not have funding")
        if self.behaviour == "none":
            return None
        if self.behaviour == "zero":
            return {"fundingRate": 0}
        if self.behaviour == "none_returned":
            return {"fundingRate": None}
        return {"fundingRate": 0.0001}


def _adapter(exchange) -> CcxtMarketDataAdapter:
    adapter = CcxtMarketDataAdapter.__new__(CcxtMarketDataAdapter)
    adapter._exchange = exchange
    return adapter


def test_a_real_rate_is_returned() -> None:
    adapter = _adapter(_Exchange("rate"))
    assert asyncio.run(adapter.fetch_funding_rate("BTC/USDT")) == 0.0001


def test_spot_absence_is_none_not_zero() -> None:
    """Zero would read as 'carry is free' and hide equity drift."""
    for behaviour in ("raises", "none", "none_returned"):
        adapter = _adapter(_Exchange(behaviour))
        assert asyncio.run(adapter.fetch_funding_rate("BTC/USDT")) is None, (
            f"{behaviour}: expected None, got a number that looks like free carry"
        )


def test_a_venue_without_the_method_is_none() -> None:
    class _Bare:
        pass

    adapter = _adapter(_Bare())
    assert asyncio.run(adapter.fetch_funding_rate("BTC/USDT")) is None


# ---------------------------------------------------------------------------
# the driver
# ---------------------------------------------------------------------------


class _Orders:
    def __init__(self) -> None:
        from engine.order import Position

        self.positions = {}
        self.charged: list[tuple[str, float]] = []
        self.mode = "live"
        p = Position(symbol="BTC/USDT", quantity=0.01, avg_price=50_000.0)
        p.apply_fill(0.01, 50_000.0)
        self.positions["BTC/USDT"] = p

    def apply_carry(self, symbol: str, rate: float):  # noqa: ANN001
        self.charged.append((symbol, rate))
        return 5.0


def _service(orders, adapter, *, mode: str = "live"):
    from unittest.mock import MagicMock

    from services.trading_runtime_service import TradingRuntimeService

    core = MagicMock()
    runtime = MagicMock()
    runtime.orders = orders
    runtime.mode = mode
    ccxt = MagicMock()
    ccxt._ccxt = adapter
    core.service_registry.get = MagicMock(
        side_effect=lambda n: ccxt if n == "ccxt_market_service" else None
    )
    svc = TradingRuntimeService(core, "trading_runtime_service")
    svc.runtime = runtime
    svc.config = {"funding_interval_sec": 30}
    return svc


def test_carry_is_charged_for_an_open_position_on_a_perp() -> None:
    orders = _Orders()
    svc = _service(orders, _adapter(_Exchange("rate")))
    result = asyncio.run(svc._accrue_funding())
    assert orders.charged == [("BTC/USDT", 0.0001)], orders.charged
    assert result["charged"]["BTC/USDT"] == 5.0


def test_a_spot_venue_charges_nothing_and_says_why() -> None:
    orders = _Orders()
    svc = _service(orders, _adapter(_Exchange("raises")))
    result = asyncio.run(svc._accrue_funding())
    assert orders.charged == [], "spot must not be charged carry"
    assert "charged" not in result or not result["charged"]


def test_paper_mode_is_never_charged() -> None:
    """Charging a simulated book would corrupt the paper equity curve."""
    orders = _Orders()
    svc = _service(orders, _adapter(_Exchange("rate")), mode="paper")
    result = asyncio.run(svc._accrue_funding())
    assert orders.charged == [], "paper mode must not accrue carry"
    assert result["skipped"] == "mode_paper"


def test_shutdown_cancels_the_task_rather_than_dropping_it() -> None:
    """The loop sleeps for minutes; a leaked task outlives the service."""
    orders = _Orders()
    svc = _service(orders, _adapter(_Exchange("rate")))

    async def scenario() -> None:
        svc.is_running = True
        svc._funding_task = asyncio.create_task(svc._funding_loop())
        await asyncio.sleep(0)
        await svc.stop()

    asyncio.run(scenario())
    assert svc._funding_task is None