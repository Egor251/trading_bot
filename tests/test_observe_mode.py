"""Observe/shadow strategies: signals logged, not voted, not executed."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from host.events import normalize_strategy_signal  # noqa: E402
from microkernel_core import KernelEvent  # noqa: E402
from services.ensemble_service import EnsembleService  # noqa: E402


def test_normalize_observe_only_flag() -> None:
    body = normalize_strategy_signal(
        {"action": "BUY", "ticker": "SBER", "observe_only": True}
    )
    assert body["observe_only"] is True
    body2 = normalize_strategy_signal({"action": "SELL", "ticker": "GAZP"})
    assert body2["observe_only"] is False


def test_ensemble_skips_observe_signals() -> None:
    async def _run() -> None:
        core = MagicMock()
        core.config.get = AsyncMock(
            return_value={
                "ensemble": {
                    "enabled": True,
                    "strategies": ["live_a"],
                    "observe": ["shadow_b"],
                }
            }
        )
        published: list = []

        async def _pub(event):
            published.append(event)

        core.event_bus = MagicMock()
        core.event_bus.publish = _pub
        core.event_bus.subscribe = AsyncMock()
        core.plugin_manager._instances = {}
        core.rule_engine = None

        svc = EnsembleService(core, "ensemble_service")
        svc.config = svc.default_config()
        await svc.on_initialize()
        svc._ensemble_cfg = {
            "enabled": True,
            "strategies": ["live_a"],
            "observe": ["shadow_b"],
        }
        svc.manager.set_equal_weights(["live_a"])
        svc.is_running = True

        await svc._on_signal(
            KernelEvent(
                event_type="strategy.signal",
                source_id="shadow_b",
                payload={
                    "action": "BUY",
                    "ticker": "SBER",
                    "strategy_id": "shadow_b",
                    "observe_only": True,
                    "strength": 1.0,
                },
            )
        )
        await svc._on_signal(
            KernelEvent(
                event_type="strategy.signal",
                source_id="live_a",
                payload={
                    "action": "BUY",
                    "ticker": "SBER",
                    "strategy_id": "live_a",
                    "strength": 1.0,
                    "quantity": 1,
                    "price": 100,
                },
            )
        )
        await asyncio.sleep(0.12)
        types = [e.event_type for e in published]
        assert "ensemble.decision" in types
        assert svc._last_signals["shadow_b"]["observe_only"] is True
        assert "shadow_b" not in {
            c.get("strategy_id")
            for e in published
            if e.event_type == "ensemble.decision"
            for c in (e.payload or {}).get("contributors") or [{}]
        }
        members = {m["strategy_id"]: m for m in svc.members_for_ui()}
        assert members["shadow_b"]["mode"] == "observe"
        assert members["live_a"]["mode"] == "live"

    asyncio.run(_run())


def test_strategy_plugin_observe_stamp() -> None:
    async def _run() -> None:
        from engine.strategy_plugin import StrategyPlugin

        published: list = []

        class _P(StrategyPlugin):
            PLUGIN_ID = "obs_test"

            def __init__(self) -> None:
                # Bypass BasePlugin ctor — only need emit_signal / config surface.
                self.component_id = "obs_test"
                self.config = {"observe_only": True}
                self.core = MagicMock()
                self.core.config.get = AsyncMock(return_value="local")
                self.core.service_registry.get = MagicMock(return_value=None)

                async def _pub(event):
                    published.append(event)

                self.core.event_bus = MagicMock()
                self.core.event_bus.publish = _pub

        plug = _P()
        await plug.emit_signal({"action": "BUY", "ticker": "SBER", "quantity": 1})
        assert published
        assert published[0].payload.get("observe_only") is True
        assert plug.is_observe_only() is True

    asyncio.run(_run())
