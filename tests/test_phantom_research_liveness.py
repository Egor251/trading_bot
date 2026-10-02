"""Phantom research measured an empty book — four defects, one visible symptom.

The user-visible symptom was "not one of 25 strategies shows a profit on
phantom". They were not unprofitable: **phantom never traded**. Four
independent defects stacked up, and the last one hid the first three.

**1. History was capped at 600 bars regardless of the request.** A single
``fetch_ohlcv`` returns far less than the ``limit`` it was given — measured
300 daily bars against requests of 500/1000/2000/5000 — and ccxt's Bitget
``since`` is *ignored* for daily bars, so the second page came back identical to
the first. 600 bars of 1m is five hours: too little to measure any edge.

**2. `run_grid` read state the bus had not delivered yet.** `EventBus.publish`
is fire-and-forget onto a queue drained by one worker. On a 1000-bar replay
``run_grid`` returned with **815 events still queued** and only 229 candles
dispatched; the strategies' 113 signals arrived *after* the metrics row was
built and the ledger reset. There was no `wait_for_idle` to ask.

**3. Bars and signals could not fill each other.** With a single FIFO worker,
publishing the whole series up front dispatches every signal *after* the last
candle — and a `next_open` fill needs the bar after the signal. Every fill of
the run was discarded, so the strategy's decisions could never become trades.

**4. Rejections were invisible.** Only FILLED/PARTIAL/ACCEPTED reached
`_fills`, so a run whose every signal fell below `strength_floor` reported
``trades=0`` with no indication a decision was ever made. This is what made
phantom look broken rather than merely unprofitable.

The ordering fix is the subtle one. Polling ``queue.qsize()`` looks right and is
wrong: ``qsize`` drops to zero when the worker *dequeues*, not when handlers
finish, so a slow handler chain reads as an idle bus. The regression below pins
the behaviour that actually matters — a signal dispatched during bar N's
processing must be fillable at bar N+1 — with a deliberately slow handler, which
is the case that made the naive version report zero trades for 20 of 20
strategies while a single extra ``print`` made them all trade again.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from microkernel_core import KernelEvent  # noqa: E402


# ---------------------------------------------------------------------------
# bus settle — exact, not heuristic
# ---------------------------------------------------------------------------


def test_quiescence_waits_for_handlers_not_just_the_queue() -> None:
    """`qsize() == 0` is not idle; the marker event is.

    A handler that outlives several event-loop turns must be finished before the
    settle returns. With queue-size polling this returns early, the next bar is
    published while bar N is still being processed, and the signal it produces
    has no bar left to fill against.
    """

    async def scenario() -> dict:
        from microkernel_core import EventBus

        from host.event_bus_settle import wait_for_bus_quiescence

        bus = EventBus()
        await bus.start()
        order: list[str] = []
        try:

            async def slow(event: KernelEvent) -> None:
                # Stands in for a strategy chain deep enough that a queue-size
                # check sees an empty queue while this is still running.
                for _ in range(6):
                    await asyncio.sleep(0)
                order.append("slow-done")

            await bus.subscribe("market.candle", slow)

            await bus.publish(
                KernelEvent(event_type="market.candle", source_id="t", payload={})
            )
            # Queue is now empty (worker dequeued), but `slow` has not finished.
            diagnostics = await wait_for_bus_quiescence(bus, timeout_sec=5.0)
            order.append("settled")
            return {"diagnostics": diagnostics, "order": order}
        finally:
            await bus.stop()

    result = asyncio.run(scenario())
    assert result["diagnostics"]["settled"] is True
    assert result["order"][-1] == "settled"
    assert "slow-done" in result["order"], (
        "the settle returned while the handler was still running"
    )
    assert result["order"].index("slow-done") < result["order"].index("settled")


def test_quiescence_reports_dropped_events_instead_of_hiding_them() -> None:
    """A bounded queue that overflows silently truncates the measured history.

    The marker itself can be among the dropped events, so `settled` may be
    False here — that is honest. What must never happen is losing events and
    reporting a clean settle, or hanging.
    """

    async def scenario() -> dict:
        from microkernel_core import EventBus

        from host.event_bus_settle import wait_for_bus_quiescence

        bus = EventBus(max_queue_size=2)
        await bus.start()
        try:
            for i in range(200):
                await bus.publish(
                    KernelEvent(
                        event_type="market.candle", source_id="t", payload={"i": i}
                    )
                )
            return await wait_for_bus_quiescence(bus, timeout_sec=5.0)
        finally:
            await bus.stop()

    diagnostics = asyncio.run(scenario())
    assert diagnostics["dropped"] > 0, (
        "the bus discarded queued events and the settle did not report it"
    )
    if not diagnostics["settled"]:
        assert diagnostics["reason"], "an unsettled wait must say why"


def test_quiescence_on_a_stopped_bus_does_not_hang() -> None:
    """A settle that blocks forever would wedge every caller, including replay."""

    async def scenario() -> dict:
        from microkernel_core import EventBus

        from host.event_bus_settle import wait_for_bus_quiescence

        bus = EventBus()
        # Never started: `publish` raises, and the settle must survive that.
        return await wait_for_bus_quiescence(bus, timeout_sec=1.0)

    diagnostics = asyncio.run(scenario())
    assert diagnostics["settled"] is False
    assert diagnostics["reason"]


# ---------------------------------------------------------------------------
# rejection visibility — a run that traded nothing must say why
# ---------------------------------------------------------------------------


def test_rejected_shadow_signals_are_counted_and_named() -> None:
    """`trades=0` must never be indistinguishable from "no decision was made"."""

    import tempfile
    from pathlib import Path
    from unittest.mock import MagicMock

    from engine.state_store import StateStore
    from services.phantom_service import PhantomService

    async def scenario() -> tuple[dict, dict]:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            core = MagicMock()
            core.config.get = MagicMock(return_value="test")
            svc = PhantomService(core, "phantom_service")
            svc.config = {**svc.default_config(), "state_path": str(tmp / "s.json")}
            svc.runtime = MagicMock()
            svc.runtime.open_positions = MagicMock(return_value=[])  # type: ignore[method-assign]
            svc.state_store = StateStore(tmp / "s.json")
            svc.restore_state()

            svc.reset_ledger()
            weak = {"strength": 0.05, "strategy_id": "s", "ticker": "BTC/USDT",
                    "class_code": "CRYPTO", "action": "BUY"}
            # The floor rejects it and returns without recording anything.
            await svc._shadow_fill(dict(weak), run_id="r")  # noqa: SLF001
            summary = svc.shadow_reject_summary()
            assert svc._fills == [], "a rejected signal must not become a fill"  # noqa: SLF001
            return summary, {"fills": list(svc._fills)}  # noqa: SLF001

    summary, _ = asyncio.run(scenario())
    assert summary["rejected"] == 1
    assert "strength_below_floor" in summary["reject_reasons"]


def test_reset_ledger_clears_the_reject_counters() -> None:
    """Otherwise counters accumulate across variants and misreport each run."""

    import tempfile
    from pathlib import Path
    from unittest.mock import MagicMock

    from engine.state_store import StateStore
    from services.phantom_service import PhantomService

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        core = MagicMock()
        core.config.get = MagicMock(return_value="test")
        svc = PhantomService(core, "phantom_service")
        svc.config = {**svc.default_config(), "state_path": str(tmp / "s.json")}
        svc.runtime = MagicMock()
        svc.runtime.open_positions = MagicMock(return_value=[])  # type: ignore[method-assign]
        svc.state_store = StateStore(tmp / "s.json")
        svc.restore_state()

        svc._note_shadow_reject("strength_below_floor")  # noqa: SLF001
        assert svc.shadow_reject_summary()["rejected"] == 1
        svc.reset_ledger()
        assert svc.shadow_reject_summary()["rejected"] == 0


def test_reject_reasons_are_normalised() -> None:
    """Broker reasons carry free-form detail; one key per message is unreadable."""

    import tempfile
    from pathlib import Path
    from unittest.mock import MagicMock

    from engine.state_store import StateStore
    from services.phantom_service import PhantomService

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        core = MagicMock()
        core.config.get = MagicMock(return_value="test")
        svc = PhantomService(core, "phantom_service")
        svc.config = {**svc.default_config(), "state_path": str(tmp / "s.json")}
        svc.runtime = MagicMock()
        svc.state_store = StateStore(tmp / "s.json")
        svc.restore_state()

        svc._note_shadow_reject("strength_below_floor=0.05<0.25")  # noqa: SLF001
        svc._note_shadow_reject("strength_below_floor=0.11<0.25")  # noqa: SLF001
        assert svc.shadow_reject_summary()["reject_reasons"] == {
            "strength_below_floor": 2
        }


# ---------------------------------------------------------------------------
# next_open ordering — the signal/fill contract the whole run depends on
# ---------------------------------------------------------------------------


def _live_phantom(tmp_path: Path):
    """A phantom with a real runtime, so `_shadow_fill` can actually execute."""
    from unittest.mock import AsyncMock, MagicMock

    from engine.runtime import TradingRuntime
    from engine.state_store import StateStore
    from services.phantom_service import PhantomService

    core = MagicMock()
    # `_shadow_fill` awaits `core.config.get(...)` and then publishes the fill on
    # `core.event_bus`; plain MagicMocks return un-awaitable values.
    core.config.get = AsyncMock(return_value="test")
    core.event_bus.publish = AsyncMock()
    svc = PhantomService(core, "phantom_service")
    svc.config = {**svc.default_config(), "state_path": str(tmp_path / "s.json")}
    svc.state_store = StateStore(tmp_path / "s.json")
    svc.runtime = TradingRuntime.from_config(
        {"mode": "phantom", "equity": 10_000.0, "risk": {}}
    )
    svc.runtime.mode = "phantom"
    svc.apply_sizing_config()
    svc.restore_state()
    return svc


def test_next_open_fills_a_signal_on_the_following_bars_open() -> None:
    """A signal raised on bar N must fill at bar N+1's open — never bar N's close.

    Filling at the close of the bar that produced the signal is optimistic: the
    strategy acted *because* of that close, and in live trading the first
    reachable price is the next bar's open. It also means a signal is only ever
    fillable if a later bar arrives, which is precisely what a replay that
    dispatched all its signals after all its bars never allowed.
    """
    import tempfile
    from pathlib import Path

    from microkernel_core import KernelEvent

    def _candle(idx: int, open_px: float) -> KernelEvent:
        return KernelEvent(
            event_type="market.candle",
            source_id="test",
            payload={
                "ticker": "BTC/USDT",
                "class_code": "CRYPTO",
                "open": open_px,
                "high": open_px + 5.0,
                "low": open_px - 5.0,
                "close": open_px + 1.0,
                "volume": 1.0,
                "ts": 1_700_000_000 + idx * 3600,
            },
        )

    async def scenario() -> list[dict]:
        with tempfile.TemporaryDirectory() as raw:
            svc = _live_phantom(Path(raw))
            svc.reset_ledger()
            assert svc._fill_timing() == "next_open"  # noqa: SLF001

            # Bar 1 ingested. A signal now has no later bar to fill against.
            await svc._on_candle(_candle(1, 100.0))  # noqa: SLF001

            # Bar 2: no pending signal yet, so nothing may fill.
            await svc._on_candle(_candle(2, 200.0))  # noqa: SLF001
            assert svc._fills == []  # noqa: SLF001

            # Signal raised against bar 2, queued for bar 3.
            signal = {
                "strategy_id": "dual_thrust",
                "action": "BUY",
                "ticker": "BTC/USDT",
                "class_code": "CRYPTO",
                "strength": 0.9,
                "quantity": 0.01,
            }
            await svc._dispatch_shadow_fill(signal, run_id="r")  # noqa: SLF001
            assert svc._fills == []  # noqa: SLF001
            assert len(svc._pending_fills) == 1  # noqa: SLF001

            # Bar 3's open is the first reachable price.
            await svc._on_candle(_candle(3, 300.0))  # noqa: SLF001
            return list(svc._fills)  # noqa: SLF001

    fills = asyncio.run(scenario())
    assert len(fills) == 1, f"expected exactly one fill, got {len(fills)}"
    assert fills[0]["price"] == 300.0, (
        f"filled at {fills[0]['price']}, not at the next bar's open (300.0)"
    )
    assert fills[0]["action"] == "BUY"


def test_a_signal_on_the_final_bar_is_reported_not_silently_dropped() -> None:
    """One unfillable signal per run must be visible, not just absent."""

    import tempfile
    from pathlib import Path

    from microkernel_core import KernelEvent

    async def scenario() -> list[dict]:
        with tempfile.TemporaryDirectory() as raw:
            svc = _live_phantom(Path(raw))
            svc.reset_ledger()
            await svc._on_candle(  # noqa: SLF001
                KernelEvent(
                    event_type="market.candle",
                    source_id="test",
                    payload={
                        "ticker": "BTC/USDT",
                        "class_code": "CRYPTO",
                        "open": 100.0,
                        "high": 105.0,
                        "low": 95.0,
                        "close": 101.0,
                        "volume": 1.0,
                        "ts": 1_700_003_600,
                    },
                )
            )
            await svc._dispatch_shadow_fill(  # noqa: SLF001
                {
                    "strategy_id": "dual_thrust",
                    "action": "BUY",
                    "ticker": "BTC/USDT",
                    "class_code": "CRYPTO",
                    "strength": 0.9,
                    "quantity": 0.01,
                },
                run_id="r",
            )
            return svc.flush_pending_fills()

    dropped = asyncio.run(scenario())
    assert len(dropped) == 1
    assert dropped[0]["status"] == "unfilled"
    assert dropped[0]["reason"] == "no_next_bar"


# ---------------------------------------------------------------------------
# run_grid returns one shape — the mixing hid every real result
# ---------------------------------------------------------------------------


def test_grid_results_are_one_row_per_variant() -> None:
    """Unfilled-signal notices must not be interleaved with variant rows.

    They used to be appended into the same list, giving them a second shape: no
    `feed`, no `pnl`, `trades: 0`. Every positional consumer — the research
    report, the UI grid, the REPL table, the sweep tool — then read one of those
    as a variant and reported a run that traded nothing while the real row beside
    it held the actual trades. That is how `adx_di_trend` showed `trades=0`
    directly beside its own row reporting 7 trades.
    """
    import tempfile
    from pathlib import Path
    from unittest.mock import MagicMock

    async def scenario() -> tuple[list[dict], list[dict]]:
        from microkernel_core import EventBus

        with tempfile.TemporaryDirectory() as raw:
            bus = EventBus()
            await bus.start()
            core = MagicMock()
            core.event_bus = bus

            async def _cfg(key, default=None):
                return {} if key == "trading" else "test"

            core.config.get = _cfg
            # run_grid stops a running Consilium; there is none in this harness.
            core.service_registry.get = MagicMock(return_value=None)

            svc = _live_phantom(Path(raw))
            svc.core = core
            svc.is_running = True
            await svc.start()  # subscribes `_on_candle` first

            plugin = MagicMock()
            plugin.config = {}
            plugin.isolated_config = MagicMock(return_value={})
            core.plugin_manager.get = MagicMock(return_value=plugin)

            # Raise a signal on the *last* candle of every variant. It is queued
            # behind the final `_on_candle` drain, so no later bar exists to fill
            # it — exactly the "unfilled" notice this test is about.
            async def _tail_signal(event: KernelEvent) -> None:
                await svc._dispatch_shadow_fill(  # noqa: SLF001
                    {
                        "strategy_id": "dual_thrust",
                        "action": "BUY",
                        "ticker": "BTC/USDT",
                        "class_code": "CRYPTO",
                        "strength": 0.9,
                        "quantity": 0.01,
                    },
                    run_id="tail",
                )

            await bus.subscribe("market.candle", _tail_signal)
            try:
                results = await svc.run_grid(
                    "BTC/USDT",
                    class_code="CRYPTO",
                    feed="patterns",
                    limit=30,
                    strategy_id="dual_thrust",
                    variants=[{}],
                )
            finally:
                await bus.stop()
            return results, list(svc._last_unfilled_signals)  # noqa: SLF001

    results, unfilled = asyncio.run(scenario())

    assert results, "run_grid produced no rows at all"
    for row in results:
        assert "variant" in row, f"a non-variant row leaked into results: {row}"
        assert "pnl" in row, f"a row without metrics leaked into results: {row}"
        assert "unfilled_signals" in row
    # The notices are still reported — just not as variant rows. Without this the
    # assertion above would pass vacuously on a run that raised no signals.
    assert unfilled, "the harness failed to produce an unfilled-signal notice"
    assert all(r.get("reason") == "no_next_bar" for r in unfilled)


# ---------------------------------------------------------------------------
# the replay must run on the timeframe the research run asked for
# ---------------------------------------------------------------------------


def test_grid_replays_the_requested_timeframe() -> None:
    """`run_grid` omitted `timeframe`, so every replay silently ran on 1m.

    Measured on `BTC/USDT` with `walk_forward.timeframe: 1h`: the replay
    published 60-second bars starting from the *tail* of the requested window,
    while the result was still labelled 1h. momentum ROC collapsed from
    ±0.036..0.059 to ±0.008..0.005, so 89 threshold crossings became 0 and the
    strategy reported no signals at all — with nothing in the output to say the
    series was not the one requested.
    """
    import tempfile
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock

    async def scenario() -> list[str]:
        with tempfile.TemporaryDirectory() as raw:
            core = MagicMock()
            core.event_bus.publish = AsyncMock()

            async def _cfg(key, default=None):
                return {} if key == "trading" else "test"

            core.config.get = _cfg
            hist = MagicMock()
            seen: list[str | None] = []

            async def _replay(_ticker, *, timeframe=None, **_kw):
                seen.append(timeframe)
                return {"bars": 10}

            hist.replay = _replay
            hist.fetch = AsyncMock(return_value=[])
            core.service_registry.get = MagicMock(
                side_effect=lambda name: hist if name == "historical_data_service" else None
            )

            svc = _live_phantom(Path(raw))
            svc.core = core
            svc.is_running = True
            svc.config["walk_forward"] = {"timeframe": "4h"}
            plugin = MagicMock()
            plugin.config = {}
            plugin.isolated_config = MagicMock(return_value={})
            core.plugin_manager.get = MagicMock(return_value=plugin)

            await svc.run_grid(
                "BTC/USDT",
                class_code="CRYPTO",
                feed="history",
                limit=10,
                strategy_id="dual_thrust",
                variants=[{}],
            )
            return seen

    seen = asyncio.run(scenario())
    assert seen, "replay was never called"
    assert seen[0] == "4h", (
        f"replay ran without the requested timeframe (got {seen[0]!r}); "
        "it silently falls back to the service default"
    )


def test_replay_reports_the_timeframe_it_actually_served() -> None:
    """A caller that asks for nothing gets honest reporting, not a failure.

    Equities legitimately serve 1d where the crypto default is 1m, so only an
    explicit request is a contract; the rest must still be visible.
    """
    from microkernel_core import KernelEvent

    from unittest.mock import AsyncMock, MagicMock

    from engine.market import Candle
    from plugins.market.historical_data_service import HistoricalDataService

    hourly = [
        Candle(
            symbol="BTC/USDT",
            class_code="CRYPTO",
            timeframe="1h",
            ts=_ts(i),
            open=1.0,
            high=2.0,
            low=0.5,
            close=1.5,
            volume=1.0,
        )
        for i in range(6)
    ]
    assert HistoricalDataService._observe_timeframe(hourly) == "1h"

    svc = object.__new__(HistoricalDataService)
    svc.config = {"intraday_timeframe": "1m"}

    async def _fetch(*_a, **_k):
        return hourly

    svc.fetch = _fetch  # type: ignore[method-assign]

    async def _publish(_bus, *, source_id, payload, instance_id="local", **_kw):
        return KernelEvent(event_type="market.candle", source_id=source_id, payload=payload)

    import plugins.market.historical_data_service as mod

    original = mod.publish_market_candle
    mod.publish_market_candle = _publish
    try:
        raised = None
        try:
            asyncio.run(
                svc.replay("BTC/USDT", class_code="CRYPTO", timeframe="4h", limit=6)
            )
        except ValueError as exc:
            raised = str(exc)
        assert raised and "timeframe_mismatch" in raised, (
            "an explicit 1h request served with hourly bars must be rejected"
        )

        # No explicit timeframe: reported, not raised.
        svc.core = MagicMock()
        svc.component_id = "historical_data_service"
        svc.core.event_bus.publish = AsyncMock()

        async def _cfg(key, default=None):
            return "local"

        svc.core.config.get = _cfg
        result = asyncio.run(svc.replay("BTC/USDT", class_code="CRYPTO", limit=6))
        assert result["timeframe_served"] == "1h"
        assert result["timeframe_requested"] is None
    finally:
        mod.publish_market_candle = original


def _ts(i: int):
    from datetime import UTC, datetime, timedelta

    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=i)

def test_run_grid_reports_the_timeframe_that_produced_the_bars() -> None:
    """A silent timeframe fallback already cost this project one session.

    `run_grid` resolves the replay interval from the walk_forward config block
    and falls back to "1d" when the caller set nothing. A new tool missed that,
    replayed a different series than it named, got zero trades, and would have
    reported "no strategy trades in any window" -- indistinguishable from a
    real finding about the strategies.

    The result must therefore carry the interval and whether it was chosen or
    defaulted. Disclosure, not prohibition: the 1d fallback is deliberate for
    some callers, and `tools/strategy_sweep.py` sets the block explicitly.
    """
    import inspect

    from services.phantom_service import PhantomService

    src = inspect.getsource(PhantomService.run_grid)
    assert '"timeframe"' in src, "the result does not report its timeframe"
    assert "timeframe_defaulted" in src, (
        "a defaulted interval must be distinguishable from a requested one"
    )
