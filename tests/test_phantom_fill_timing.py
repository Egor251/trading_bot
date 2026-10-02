"""Phantom research fills must not assume the price the signal already saw.

The walk-forward harness filled at the **close of the bar that produced the
signal**. That is the close the strategy had just reacted to, so every backtest
measured "decide on bar N, transact at bar N's close" — a price no live run can
get, since the decision is only available once that close prints. The number that
`evaluate_live_readiness` then gates live promotion on was systematically
flattered.

`fill_timing: next_open` (now the default) defers the fill to the following
bar's open, which is the first price actually reachable. `bar_close` remains for
reproducing historical results and is documented as unsuitable for gating.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.order import OrderResult, OrderStatus  # noqa: E402
from services.phantom_service import PhantomService  # noqa: E402


def _svc(**config: Any) -> PhantomService:
    core = MagicMock()
    core.config.get = AsyncMock(
        side_effect=lambda key, default=None: (
            config if key == "phantom" else "test" if key == "instance.id" else default
        )
    )
    core.event_bus = MagicMock()
    core.event_bus.publish = AsyncMock()
    core.event_bus.subscribe = AsyncMock()
    core.event_bus.unsubscribe = AsyncMock()
    svc = PhantomService(core, "phantom_service")
    svc.config = {**svc.default_config(), **config}
    svc.runtime = MagicMock()
    svc.runtime.mode = "phantom"
    svc.runtime.candles.last = MagicMock(return_value=None)
    svc.runtime.candles.mark_price = MagicMock(return_value=100.0)
    svc.runtime.candles.ingest_payload = MagicMock()
    svc._capturing = True
    return svc


def _bar(*, open_: float, close: float, low: float, high: float) -> dict[str, Any]:
    return {"open": open_, "close": close, "low": low, "high": high, "volume": 1.0}


def _signal(*, price: float = 100.0) -> dict[str, Any]:
    return {
        "action": "BUY",
        "ticker": "SBER",
        "class_code": "TQBR",
        "price": price,
        "strength": 1.0,
        "strategy_id": "ma_cross_example",
    }


# ---------------------------------------------------------------------------
# default timing
# ---------------------------------------------------------------------------


def test_default_is_next_open() -> None:
    assert _svc()._fill_timing() == "next_open"  # noqa: SLF001


def test_signal_does_not_fill_immediately() -> None:
    """The signal must not transact on the bar it just read."""
    svc = _svc()
    fills: list[Any] = []

    async def _fake_fill(signal, *, run_id="", price_override=None):
        fills.append(price_override)
        return {}

    svc._shadow_fill = _fake_fill  # noqa: SLF001
    asyncio.run(svc._dispatch_shadow_fill(_signal(), run_id="r"))  # noqa: SLF001
    assert fills == [], "filled on the signal bar"
    assert len(svc._pending_fills) == 1


def test_next_bar_open_is_used_as_the_fill_price() -> None:
    svc = _svc()
    fills: list[Any] = []

    async def _fake_fill(signal, *, run_id="", price_override=None):
        fills.append(price_override)
        return {}

    svc._shadow_fill = _fake_fill  # noqa: SLF001
    asyncio.run(svc._dispatch_shadow_fill(_signal(price=100.0), run_id="r"))  # noqa: SLF001

    # The next candle arrives with a different open.
    asyncio.run(
        svc._on_candle(MagicMock(payload=_bar(open_=101.5, close=103.0, low=99.0, high=104.0)))  # noqa: SLF001
    )
    assert fills == [101.5], "must fill at the next bar's open, not the signal's price"
    assert svc._pending_fills == []  # noqa: SLF001


def test_queue_is_drained_one_bar_at_a_time() -> None:
    svc = _svc()
    fills: list[Any] = []

    async def _fake_fill(signal, *, run_id="", price_override=None):
        fills.append(price_override)
        return {}

    svc._shadow_fill = _fake_fill  # noqa: SLF001
    asyncio.run(svc._dispatch_shadow_fill(_signal(), run_id="r1"))  # noqa: SLF001
    asyncio.run(svc._dispatch_shadow_fill(_signal(), run_id="r2"))  # noqa: SLF001
    assert len(svc._pending_fills) == 2  # noqa: SLF001

    asyncio.run(svc._on_candle(MagicMock(payload=_bar(open_=102.0, close=1, low=1, high=1))))  # noqa: SLF001
    assert len(fills) == 2
    assert svc._pending_fills == []  # noqa: SLF001


def test_signal_raised_on_a_bar_fills_on_the_following_one() -> None:
    """Ordering: candle N, signal N, candle N+1 → exactly one fill."""
    svc = _svc()
    fills: list[Any] = []

    async def _fake_fill(signal, *, run_id="", price_override=None):
        fills.append(price_override)
        return {}

    svc._shadow_fill = _fake_fill  # noqa: SLF001

    async def _scenario() -> None:
        await svc._on_candle(  # noqa: SLF001
            MagicMock(payload=_bar(open_=100.0, close=100.0, low=99.0, high=101.0))
        )
        await svc._dispatch_shadow_fill(_signal(price=100.0), run_id="r")  # noqa: SLF001
        await svc._on_candle(  # noqa: SLF001
            MagicMock(payload=_bar(open_=100.5, close=100.7, low=100.0, high=101.0))
        )

    asyncio.run(_scenario())
    assert fills == [100.5]


# ---------------------------------------------------------------------------
# bar_close remains available
# ---------------------------------------------------------------------------


def test_bar_close_mode_fills_immediately() -> None:
    svc = _svc(fill_timing="bar_close")
    fills: list[Any] = []

    async def _fake_fill(signal, *, run_id="", price_override=None):
        fills.append(price_override)
        return {}

    svc._shadow_fill = _fake_fill  # noqa: SLF001
    asyncio.run(svc._dispatch_shadow_fill(_signal(), run_id="r"))  # noqa: SLF001
    assert fills == [None], "bar_close fills inline with no override"
    assert svc._pending_fills == []  # noqa: SLF001


# ---------------------------------------------------------------------------
# the tail of a run
# ---------------------------------------------------------------------------


def test_signal_on_the_final_bar_is_reported_not_lost() -> None:
    """One unfillable signal per run must be visible, not silently dropped."""
    svc = _svc()

    async def _no_fill(*a, **k):
        return {}

    svc._shadow_fill = _no_fill  # noqa: SLF001
    asyncio.run(svc._dispatch_shadow_fill(_signal(), run_id="r"))  # noqa: SLF001
    asyncio.run(svc._on_candle(MagicMock(payload=_bar(open_=1, close=1, low=1, high=1))))  # noqa: SLF001
    asyncio.run(svc._dispatch_shadow_fill(_signal(), run_id="r"))  # noqa: SLF001

    dropped = svc.flush_pending_fills()  # noqa: SLF001
    assert len(dropped) == 1
    assert dropped[0]["reason"] == "no_next_bar"
    assert dropped[0]["strategy_id"] == "ma_cross_example"
    # Flushing is destructive and idempotent from the caller's view.
    assert svc._pending_fills == []  # noqa: SLF001
    assert svc.flush_pending_fills() == []  # noqa: SLF001


# ---------------------------------------------------------------------------
# the clamp must not apply to a next-bar open
# ---------------------------------------------------------------------------


def test_next_bar_open_is_not_clamped_into_the_previous_bar() -> None:
    """Clamping to the signal bar's range can manufacture profit.

    `price = min(hi, max(lo, price))` on the *signal* bar lets an unreachable
    price be nudged to that bar's favourable extreme. A next-bar open is already
    inside its own bar's range and must be used verbatim.
    """
    svc = _svc()
    bar = MagicMock()
    bar.low = 100.0
    bar.high = 101.0
    bar.close = 100.5
    svc.runtime.candles.last = MagicMock(return_value=bar)
    captured: list[float] = []

    def _submit_signal(**kwargs):
        captured.append(float(kwargs.get("mark_price") or 0.0))
        return OrderResult(
            client_id="c", status=OrderStatus.FILLED, filled_qty=1.0, avg_price=120.0
        )

    svc.runtime.submit_signal = _submit_signal
    svc.runtime._sync_open_marks = MagicMock()  # noqa: SLF001
    svc.runtime._contract_multiplier = MagicMock(return_value=1.0)  # noqa: SLF001
    svc.runtime._order_increases_symbol_risk = MagicMock(return_value=True)  # noqa: SLF001
    svc.runtime._clamp_qty_to_leverage_headroom = MagicMock(side_effect=lambda q, **k: q)  # noqa: SLF001
    svc.timeline = None
    from engine.position_sizing import SizingConfig, SizingMode

    svc.sizing = SizingConfig(mode=SizingMode.PERCENT_OF_EQUITY, value=10.0)

    # The next bar opened far above the signal bar's high.
    asyncio.run(svc._shadow_fill(_signal(price=100.5), run_id="r", price_override=120.0))  # noqa: SLF001
    assert captured, "expected a submit_signal call"
    assert captured[0] == 120.0, "next-bar open was clamped to the previous bar"