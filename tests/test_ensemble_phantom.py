"""Ensemble service + phantom offline/grid smoke."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from engine.ensemble import EnsembleManager, StrategySignal  # noqa: E402
from host.events import (  # noqa: E402
    normalize_ensemble_decision,
    normalize_phantom_fill,
)
from services.phantom_service import PhantomService  # noqa: E402




async def _await_event(published, event_type: str, *, timeout: float = 5.0) -> None:
    """Wait for *event_type* to appear, up to *timeout*.

    Replaces a fixed sleep that was racing the Consilium debounce. See the note
    in tests/test_adaptive_consilium.py.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if any(e.event_type == event_type for e in published):
            return
        await asyncio.sleep(0.01)
    assert any(
        e.event_type == event_type for e in published
    ), f"{event_type} not published within {timeout}s"
def test_ensemble_manager_still_aggregates() -> None:
    ens = EnsembleManager(["a", "b"])
    decs = ens.aggregate(
        [
            StrategySignal("a", "SBER", "buy", 1.0),
            StrategySignal("b", "SBER", "sell", 0.5),
        ]
    )
    assert len(decs) == 1
    assert decs[0].side == "buy"


def test_empty_live_lineup_not_autofilled() -> None:
    """YAML strategies: [] must stay empty — never promote all plugins to live."""
    from unittest.mock import MagicMock, AsyncMock

    from services.ensemble_service import EnsembleService

    core = MagicMock()
    core.event_bus.subscribe = AsyncMock()
    core.plugin_manager._instances = {
        "a": MagicMock(),
        "b": MagicMock(),
    }
    svc = EnsembleService(core, "ensemble_service")
    svc._ensemble_cfg = {"enabled": True, "strategies": [], "observe": ["macd_trend"]}
    svc.manager.set_weights({"stale": 1.0})

    async def _run() -> None:
        await svc.start()

    asyncio.run(_run())
    assert svc.lineup_ids() == []
    assert svc._ensemble_cfg["strategies"] == []
    assert svc.manager.weights == {}
    assert svc.observe_ids() == {"macd_trend"}


def test_normalize_ensemble_and_phantom() -> None:
    d = normalize_ensemble_decision(
        {"action": "buy", "ticker": "SBER", "quantity": 1}
    )
    assert d["action"] == "BUY"
    assert d["strategy_id"] == "consilium"
    f = normalize_phantom_fill({"action": "sell", "ticker": "GAZP"})
    assert f["shadow"] is True


async def _ensemble_service_flush() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from microkernel_core import KernelEvent
    from services.ensemble_service import EnsembleService

    core = MagicMock()
    core.config.get = AsyncMock(
        return_value={"ensemble": {"enabled": True, "strategies": ["a", "b"]}}
    )
    core.event_bus = MagicMock()
    published: list = []

    async def _pub(event):
        published.append(event)

    core.event_bus.publish = _pub
    core.event_bus.subscribe = AsyncMock()
    core.plugin_manager._instances = {}
    core.rule_engine = None

    svc = EnsembleService(core, "ensemble_service")
    svc.config = svc.default_config()
    await svc.on_initialize()
    svc.manager.set_equal_weights(["a", "b"])
    svc.is_running = True

    await svc._on_signal(
        KernelEvent(
            event_type="strategy.signal",
            source_id="a",
            payload={
                "action": "BUY",
                "ticker": "SBER",
                "strategy_id": "a",
                "quantity": 5,
                "price": 100,
                "class_code": "TQBR",
            },
        )
    )
    await svc._on_signal(
        KernelEvent(
            event_type="strategy.signal",
            source_id="b",
            payload={
                "action": "BUY",
                "ticker": "SBER",
                "strategy_id": "b",
                "quantity": 5,
                "price": 100,
                "class_code": "TQBR",
            },
        )
    )
    await _await_event(published, "ensemble.decision")
    types = [e.event_type for e in published]
    assert "ensemble.decision" in types  # awaited above; kept as the explicit check


def test_ensemble_service_publishes_decision() -> None:
    asyncio.run(_ensemble_service_flush())


def test_phantom_offline_ma_cross() -> None:
    from unittest.mock import MagicMock

    core = MagicMock()
    svc = PhantomService(core, "phantom_service")
    svc.config = svc.default_config()
    closes = [100 + i * 0.5 for i in range(40)]
    # force a few crosses by making a step series
    closes = [10] * 10 + [20] * 10 + [5] * 10 + [25] * 10
    m = svc.offline_ma_cross_metrics(closes, fast=3, slow=5, quantity=1)
    assert m["trades"] >= 1
    assert "pnl" in m


if __name__ == "__main__":
    test_ensemble_manager_still_aggregates()
    test_normalize_ensemble_and_phantom()
    test_ensemble_service_publishes_decision()
    test_phantom_offline_ma_cross()
    print("ensemble_phantom OK")
