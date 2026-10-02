"""Waiting for the event bus to actually deliver what was published.

``microkernel_core.EventBus.publish`` is fire-and-forget: it hands the event to a
bounded queue and a single background worker dispatches events one at a time,
in FIFO order, awaiting each one. There is no ``wait_for_idle``, so nothing in
the product could ask "has everything I published been handled yet?".

That gap is not theoretical. It is why phantom research reported zero trades
for every strategy. Measured on a 1000-bar replay of ``ma_cross_example``:

    immediately after run_grid returned:
        market.candle dispatched   229 of 1000
        events still queued        815
    three seconds later:
        market.candle dispatched   1000
        strategy.signal dispatched 155
        plugin._signals_sent       113

The strategies were emitting all along — 113 signals — but ``run_grid`` had
already assembled its metrics row from an empty ledger and then called
``reset_ledger()``, discarding the fills that arrived afterwards. So phantom
reported ``trades=0, fills=0, pnl=0.0`` while the book it had just built was
full of real fills.

Order matters just as much as delivery. Because there is a single FIFO worker,
publishing every bar up front means every signal is dispatched *after* the last
candle — and a ``next_open`` fill needs the bar after the signal, so every fill
of the run is discarded. Replay therefore settles each bar before publishing the
next, reproducing live ordering: a decision on bar N is dispatched, becomes a
signal, and only then does bar N+1 arrive to fill it.

Why a sentinel, and why more than one
------------------------------------
Polling ``queue.qsize()`` alone is the obvious approach and it is wrong.
``qsize()`` drops to zero the moment the worker *dequeues* an event, not when its
handlers have finished, so a slow handler chain reads as an idle bus. That bug
was not subtle: adding a single ``print`` to the sweep loop made 20 of 20
strategies start trading again — the signature of a timing race, not a logic
error.

A single marker event is exact about the wrong thing. Publishing a marker and
waiting for it proves only that everything queued *before the marker* finished —
and a candle's own handler publishes its follow-up signal *while it runs*, so
that signal lands behind the marker and is still pending when the marker comes
back. One marker therefore returns while the signal it was meant to cover is
undelivered, which silently reinstates the original ordering bug.

So the marker is used as a *pass*, not as a proof: publish one, wait for it to
be dispatched, then require the queue to be empty afterwards. Events published
during that pass show up behind the marker and are caught by the next one. The
bus is declared settled only after the queue has been observed empty on two
consecutive passes, which means nothing is queued and no handler can still be
about to enqueue.

The bounded queue is the other half of the risk: ``publish`` uses
``put_nowait`` and silently increments a dropped counter on overflow, so a long
replay can lose candles outright. That count is reported here so a caller can
detect it instead of measuring a history that is quietly incomplete.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

__all__ = ["wait_for_bus_quiescence"]

#: Bus is single-worker and FIFO; see module docstring for why that matters.
_PREFIX = "bus.quiescence."


def _pending(bus: Any) -> int | None:
    """Queue depth, or None when the bus does not expose one (best effort)."""
    queue = getattr(bus, "_queue", None)
    qsize = getattr(queue, "qsize", None)
    if not callable(qsize):
        return None
    try:
        return int(qsize())
    except Exception:  # noqa: BLE001 - a closed queue must not break a caller
        return None


def _dropped(bus: Any) -> int:
    try:
        return int(getattr(bus, "_dropped_events", 0) or 0)
    except Exception:  # noqa: BLE001
        return 0


async def wait_for_bus_quiescence(
    bus: Any,
    *,
    timeout_sec: float = 30.0,
    quiet_passes: int = 2,
    marker_prefix: str = _PREFIX,
) -> dict[str, Any]:
    """Wait until the bus has fully dispatched everything queued before the call.

    Repeats a marker pass until the queue has been seen empty on
    ``quiet_passes`` consecutive passes. See the module docstring: one pass only
    covers what was queued ahead of the marker, and a candle's handler enqueues
    its own follow-up signal behind it.

    Args:
        bus: the ``EventBus`` to settle.
        timeout_sec: ceiling on the wait, so a handler that keeps publishing
            cannot hang the caller forever.
        quiet_passes: consecutive passes that must observe an empty queue.
        marker_prefix: namespace for the internal marker events.

    Returns:
        Diagnostics. ``dropped`` counts events the bus itself discarded on queue
        overflow — non-zero means the caller measured an incomplete history.
    """
    if bus is None:
        return {
            "settled": False,
            "queued": None,
            "queued_at_call": None,
            "dropped": 0,
            "elapsed_sec": 0.0,
            "passes": 0,
            "reason": "no_bus",
        }

    from microkernel_core import KernelEvent

    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + max(0.0, float(timeout_sec))
    dropped_before = _dropped(bus)
    token = f"{marker_prefix}{uuid4().hex}"
    seen = asyncio.Event()

    async def _marker(event: Any) -> None:
        seen.set()

    queued_at_call = _pending(bus)
    reason = ""
    quiet = 0
    passes = 0
    queued: int | None = queued_at_call
    try:
        await bus.subscribe(token, _marker)
        while quiet < max(1, quiet_passes):
            seen = asyncio.Event()
            passes += 1
            await bus.publish(
                KernelEvent(event_type=token, source_id="bus-settle", payload={})
            )
            remaining = deadline - loop.time()
            if remaining <= 0:
                reason = reason or "timeout"
                break
            await asyncio.wait_for(seen.wait(), timeout=remaining)
            queued = _pending(bus)
            # The marker came back, so everything ahead of it has been fully
            # dispatched. Anything left was enqueued during that dispatch.
            quiet = quiet + 1 if queued == 0 else 0
    except asyncio.TimeoutError:
        reason = "marker_not_dispatched"
    except Exception as exc:  # noqa: BLE001 - a non-running bus must not hang us
        reason = f"{type(exc).__name__}: {exc}"[:120]
    finally:
        try:
            await bus.unsubscribe(token, _marker)
        except Exception:  # noqa: BLE001
            pass

    return {
        "settled": quiet >= max(1, quiet_passes),
        "queued": queued,
        "queued_at_call": queued_at_call,
        "dropped": _dropped(bus) - dropped_before,
        "elapsed_sec": round(loop.time() - started, 4),
        "passes": passes,
        "reason": reason,
    }


def settle_unsatisfied(diagnostics: dict[str, Any]) -> str | None:
    """Return a short reason string when a settle failed, else None."""
    if diagnostics.get("settled"):
        return None
    return str(diagnostics.get("reason") or "not_settled")[:120]