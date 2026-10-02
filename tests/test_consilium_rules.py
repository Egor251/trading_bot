"""Consilium RuleEngine path + mean-reversion smoke helpers."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from microkernel_core import KernelEvent  # noqa: E402
from services.ensemble_service import EnsembleService  # noqa: E402


async def _ensemble_publishes_without_direct_fill() -> None:
    core = MagicMock()

    async def cfg_get(key, default=None):
        if key == "trading":
            return {
                "ensemble": {
                    "enabled": True,
                    "strategies": ["ma_cross_example", "mean_reversion_example"],
                }
            }
        if key == "instance.id":
            return "local"
        return default

    core.config.get = cfg_get
    published: list = []

    async def pub(event):
        published.append(event)

    core.event_bus.publish = pub
    core.event_bus.subscribe = AsyncMock()
    core.plugin_manager._instances = {}
    core.rule_engine = MagicMock()
    core.rule_engine.register_check = AsyncMock()
    core.service_registry.get = MagicMock(return_value=None)
    core.trading = None

    svc = EnsembleService(core, "ensemble_service")
    svc.config = svc.default_config()
    assert svc.config.get("route_execution") is False
    await svc.on_initialize()
    core.rule_engine.register_check.assert_awaited()
    svc.manager.set_equal_weights(["ma_cross_example", "mean_reversion_example"])
    svc.is_running = True

    await svc._on_signal(
        KernelEvent(
            event_type="strategy.signal",
            source_id="ma_cross_example",
            payload={
                "action": "BUY",
                "ticker": "SBER",
                "strategy_id": "ma_cross_example",
                "quantity": 5,
                "price": 100,
                "class_code": "TQBR",
            },
        )
    )
    await svc._on_signal(
        KernelEvent(
            event_type="strategy.signal",
            source_id="mean_reversion_example",
            payload={
                "action": "BUY",
                "ticker": "SBER",
                "strategy_id": "mean_reversion_example",
                "quantity": 5,
                "price": 100,
                "class_code": "TQBR",
            },
        )
    )
    await _await_event(published, "ensemble.decision")
    types = [e.event_type for e in published]
    assert "execution.result" not in types  # RuleEngine path, not direct




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
def test_ensemble_rule_path_no_direct_fill() -> None:
    asyncio.run(_ensemble_publishes_without_direct_fill())


def test_mean_reversion_import() -> None:
    from strategies.mean_reversion_example.plugin import MeanReversionExamplePlugin

    assert MeanReversionExamplePlugin.PLUGIN_ID == "mean_reversion_example"


if __name__ == "__main__":
    test_ensemble_rule_path_no_direct_fill()
    test_mean_reversion_import()
    print("consilium_rules OK")
