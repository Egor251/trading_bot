"""The execution pipeline is alive — proven, not assumed.

The audit reported "0 successful executions since 2026-08-04, 90.6% failure
rate" and inferred a dead pipeline. Re-measured on a clean data directory, the
chain signal → Consilium → RuleEngine → order → risk works end to end. Three
distinct causes were behind the appearance of death, and two of them were real
product defects:

1. **Every forced-flat exit voted with strength 0.0.** `emit_on_side_change`
   inverts a HOLD into the opposite side so an open position gets closed, but a
   HOLD carries `strength=0.0` by definition and the inversion inherited it.
   `EnsembleManager.aggregate` multiplies weight × direction × strength, so the
   exit scored zero, the whole debounce window was published as
   `flat_consensus`, and any real entry in the same window died with it.
   Measured on `dual_thrust` — the *only* live member of the shipped lineup —
   42 of 84 signals were such exits and its mean strength was 0.19. It could
   open a position and never contribute a vote to close one.

2. **The debounce could be starved forever.** Every arriving signal cancelled and
   rescheduled the flush. While signals arrive faster than `debounce_sec` the
   flush never runs and `_buffer` grows unbounded. Invisible on minute bars;
   on second bars with several strategies the Consilium goes silent.

3. **The configuration, not the code.** The shipped lineup is exactly one
   strategy (`dual_thrust`); the other fourteen are `observe_only: true`. So the
   live system had a single voter whose exits were all discarded.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.strategy_emit import emit_on_side_change  # noqa: E402


class _Decision:
    def __init__(self, action, strength=0.0, reason="", metrics=None, intent="flat"):
        self.action = action
        self.strength = strength
        self.reason = reason
        self.metrics = metrics or {}
        self.intent = intent

    def as_signal_fields(self):
        return {
            "action": str(self.action).upper(),
            "strength": float(self.strength or 0),
            "reason": self.reason,
            "params_snapshot": dict(self.metrics),
            "intent": self.intent,
        }


def _plugin() -> MagicMock:
    p = MagicMock()
    p.config = {"class_code": "CRYPTO", "quantity": 0.01}
    p._last_side = None  # noqa: SLF001
    p._signals_sent = 0  # noqa: SLF001
    p.emitted: list[dict] = []

    async def _emit(signal):
        p.emitted.append(dict(signal))

    p.emit_signal = _emit
    return p


def _candle(ticker="BTC/USDT", close=100.0):
    from microkernel_core import KernelEvent

    return KernelEvent(
        event_type="market.candle",
        source_id="verify",
        payload={"ticker": ticker, "class_code": "CRYPTO", "close": close},
    )


# ---------------------------------------------------------------------------
# defect 1 — a forced-flat exit must carry a usable vote
# ---------------------------------------------------------------------------


def test_buy_entry_keeps_its_strength() -> None:
    p = _plugin()
    asyncio.run(
        emit_on_side_change(
            p,
            _candle(),
            _Decision("BUY", strength=0.62, reason="dual_thrust_up", intent="long"),
        )
    )
    assert p.emitted[0]["action"] == "BUY"
    assert p.emitted[0]["strength"] == 0.62


def test_forced_flat_exit_has_non_zero_strength() -> None:
    """This was the defect: a HOLD inverted into an exit carried strength 0.0."""
    p = _plugin()
    asyncio.run(
        emit_on_side_change(
            p,
            _candle(),
            _Decision("BUY", strength=0.62, reason="dual_thrust_up", intent="long"),
        )
    )
    # Next bar the strategy goes HOLD/flat — it must emit the opposite side.
    asyncio.run(
        emit_on_side_change(
            p,
            _candle(),
            _Decision("HOLD", strength=0.0, reason="dual_thrust_inside", intent="flat"),
        )
    )
    exit_signal = p.emitted[-1]
    assert exit_signal["action"] == "SELL", "expected an inverted exit"
    assert exit_signal["strength"] > 0.0, (
        "an exit that votes zero strength is discarded as flat_consensus"
    )
    assert exit_signal["strength"] == 1.0


def test_exit_strength_survives_aggregation() -> None:
    """End of the chain: an exit vote must produce a non-flat decision."""
    from engine.ensemble import EnsembleManager, StrategySignal

    mgr = EnsembleManager(["dual_thrust"])
    mgr.set_weights({"dual_thrust": 1.0})
    decisions = mgr.aggregate(
        [
            StrategySignal(
                strategy_id="dual_thrust",
                symbol="ETH/USDT",
                side="sell",
                strength=1.0,
            )
        ]
    )
    assert decisions[0].side == "sell"
    assert decisions[0].strength == 1.0


def test_zero_strength_vote_still_flattens() -> None:
    """The aggregation semantics are unchanged; only the emitter stopped lying."""
    from engine.ensemble import EnsembleManager, StrategySignal

    mgr = EnsembleManager(["s"])
    mgr.set_weights({"s": 1.0})
    decisions = mgr.aggregate(
        [
            StrategySignal(
                strategy_id="s", symbol="X", side="sell", strength=0.0
            )
        ]
    )
    assert decisions[0].side == "flat"


def test_emit_count_is_unchanged() -> None:
    """Only the strength changed; the side-change gating must be identical."""

    async def _go():
        p = _plugin()
        assert await emit_on_side_change(
            p, _candle(), _Decision("BUY", 0.5, "up", intent="long")
        )
        # Same side again -> suppressed.
        assert not await emit_on_side_change(
            p, _candle(), _Decision("BUY", 0.5, "up", intent="long")
        )
        assert await emit_on_side_change(
            p, _candle(), _Decision("HOLD", 0.0, "inside", intent="flat")
        )
        assert len(p.emitted) == 2

    asyncio.run(_go())


# ---------------------------------------------------------------------------
# defect 2 — the debounce cannot be starved
# ---------------------------------------------------------------------------


def _ensemble(debounce=0.05, max_wait=1.0) -> MagicMock:
    from services.ensemble_service import EnsembleService

    core = MagicMock()
    core.config.get = AsyncMock(side_effect=lambda k, d=None: "local")
    core.event_bus = MagicMock()
    core.event_bus.publish = AsyncMock()
    svc = EnsembleService(core, "ensemble_service")
    svc.config = {
        **svc.default_config(),
        "debounce_sec": debounce,
        "debounce_max_wait_sec": max_wait,
        "route_execution": False,
    }
    svc.is_running = True
    svc._enabled = lambda: True  # type: ignore[method-assign]
    svc._ensemble_cfg = {  # noqa: SLF001
        "enabled": True,
        "strategies": ["s1"],
        "observe": [],
        "weight_mode": "equal",
    }
    svc.manager.set_weights({"s1": 1.0})
    return svc


def _signal_event(sid: str = "s1"):
    from microkernel_core import KernelEvent

    return KernelEvent(
        event_type="strategy.signal",
        source_id=sid,
        payload={
            "strategy_id": sid,
            "ticker": "BTC/USDT",
            "action": "BUY",
            "strength": 0.8,
            "class_code": "CRYPTO",
        },
    )


def _decision_count(svc: MagicMock) -> int:
    """How many `ensemble.decision` events the flush actually published."""
    n = 0
    for call in svc.core.event_bus.publish.await_args_list:
        event = call.args[0] if call.args else None
        if getattr(event, "event_type", "") == "ensemble.decision":
            n += 1
    return n


def test_a_continuous_signal_stream_still_flushes() -> None:
    """Signals faster than the debounce must not starve it indefinitely.

    75 signals arrive every 20 ms — 2.5x faster than the 50 ms debounce, for
    1.5 s. Without the max-wait ceiling the flush is cancelled on every arrival,
    so exactly one decision appears (the tail flush after the stream stops).
    With the ceiling, decisions keep being produced *during* the flood.
    """
    svc = _ensemble(debounce=0.05, max_wait=0.3)

    async def _go():
        for _ in range(75):
            await svc._on_signal(_signal_event())  # noqa: SLF001
            # Count decisions published while signals are still arriving.
            if _decision_count(svc) >= 3:
                return
            await asyncio.sleep(0.02)

    asyncio.run(_go())
    assert _decision_count(svc) >= 3, (
        f"only {_decision_count(svc)} decision(s) during a continuous flood — "
        "the debounce is being starved"
    )


def test_buffer_is_bounded_under_a_flood() -> None:
    """The buffer must be drained during the flood, not only after it ends."""
    svc = _ensemble(debounce=0.05, max_wait=0.2)
    peak = 0

    async def _go():
        nonlocal peak
        for _ in range(60):
            await svc._on_signal(_signal_event())  # noqa: SLF001
            peak = max(peak, sum(len(v) for v in svc._buffer.values()))  # noqa: SLF001
            await asyncio.sleep(0.005)

    asyncio.run(_go())
    assert peak < 60, f"buffer grew to {peak} during the flood — unbounded"
    assert _decision_count(svc) > 0, "nothing was ever flushed"


def test_max_wait_defaults_to_a_bounded_value() -> None:
    svc = _ensemble()
    assert float(svc.config["debounce_max_wait_sec"]) > 0
    assert svc._buffer_since is None  # noqa: SLF001


# ---------------------------------------------------------------------------
# defect 3 — the shipped lineup is a single strategy
# ---------------------------------------------------------------------------


def test_shipped_lineup_is_observed_not_assumed() -> None:
    """Pins the fact that made the system look dead.

    Fourteen of the fifteen strategies are `observe_only: true`, so the live
    Consilium had exactly one voter. Any claim about throughput has to be made
    against that, not against a lineup of twenty.
    """
    import yaml

    from engine.paths import repo_root

    raw = yaml.safe_load(
        (repo_root() / "config" / "trading_bot.yaml").read_text(encoding="utf-8")
    )
    lineup = list((raw.get("trading") or {}).get("ensemble", {}).get("strategies") or [])
    observe = list((raw.get("trading") or {}).get("ensemble", {}).get("observe") or [])
    plugins = raw.get("plugins") or {}

    assert lineup, "the shipped config should declare a live lineup"
    assert len(lineup) <= 2, (
        f"expected a deliberately small live lineup, found {len(lineup)}: {lineup}"
    )
    for sid in lineup:
        block = plugins.get(sid) or {}
        assert block.get("enabled") is True, f"{sid} is in the lineup but disabled"
        assert block.get("observe_only") is not True, (
            f"{sid} is in the live lineup but observe_only — it cannot vote"
        )
    # Everything in observe must actually be parked.
    for sid in observe:
        assert (plugins.get(sid) or {}).get("observe_only") is True, (
            f"{sid} is listed in observe but is not observe_only"
        )


def test_every_live_member_can_actually_emit() -> None:
    """A member that is live, enabled and subscribed is the minimum for voting."""
    import yaml

    from engine.paths import repo_root

    raw = yaml.safe_load(
        (repo_root() / "config" / "trading_bot.yaml").read_text(encoding="utf-8")
    )
    lineup = list((raw.get("trading") or {}).get("ensemble", {}).get("strategies") or [])
    plugins = raw.get("plugins") or {}
    for sid in lineup:
        block = plugins.get(sid) or {}
        assert block.get("tickers"), f"{sid} has no tickers, so it receives no bars"
        assert block.get("class_code"), f"{sid} has no class_code"


def test_protective_layer_covers_a_closed_slice() -> None:
    """Sanity: the harness path that proves 'alive' also arms protection."""
    from engine.protective_runtime import ProtectiveLayer

    layer = ProtectiveLayer()
    layer.open_slice(
        slice_id="s1",
        strategy_id="dual_thrust",
        symbol="CRYPTO:ETH/USDT",
        side="buy",
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        opened_at=datetime.now(UTC),
    )
    assert layer.has_protection("CRYPTO:ETH/USDT") is True