"""Adaptive Consilium: risk_parity / bandit_ucb rebalance."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.ensemble import EnsembleManager, StrategySignal  # noqa: E402
from microkernel_core import KernelEvent  # noqa: E402
from services.ensemble_service import EnsembleService  # noqa: E402



async def _await_event(published, event_type: str, *, timeout: float = 5.0) -> None:
    """Wait for *event_type* to appear, up to *timeout*.

    Replaces a fixed sleep. The sleep was a race with the Consilium debounce: on
    a loaded machine it could expire before the event was published, producing an
    intermittent failure that proved nothing about the code under test.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if any(e.event_type == event_type for e in published):
            return
        await asyncio.sleep(0.01)
    assert any(
        e.event_type == event_type for e in published
    ), f"{event_type} not published within {timeout}s"

def test_risk_parity_prefers_lower_vol() -> None:
    mgr = EnsembleManager(["a", "b"])
    for _ in range(20):
        mgr.stats.record_outcome("a", 0.01)  # stable
        mgr.stats.record_outcome("b", 0.20 if _ % 2 == 0 else -0.18)  # noisy
    weights = mgr.rebalance("risk_parity", ["a", "b"], apply_corr=False)
    assert weights["a"] > weights["b"]
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_bandit_ucb_boosts_better_mean() -> None:
    mgr = EnsembleManager(["good", "bad"])
    for _ in range(30):
        mgr.stats.record_outcome("good", 0.05)
        mgr.stats.record_outcome("bad", -0.02)
    weights = mgr.rebalance("bandit_ucb", ["good", "bad"], ucb_c=0.1, apply_corr=False)
    assert weights["good"] > weights["bad"]


def test_corr_dampen_cuts_weaker() -> None:
    mgr = EnsembleManager(["strong", "weak"])
    # Nearly identical series → high corr; strong has better mean.
    for i in range(20):
        r = 0.01 * ((-1) ** i)
        mgr.stats.record_outcome("strong", r + 0.02)
        mgr.stats.record_outcome("weak", r)
    weights = mgr.rebalance(
        "risk_parity", ["strong", "weak"], corr_cap=0.5, apply_corr=True
    )
    assert weights["strong"] >= weights["weak"]


def test_ensemble_service_rebalance_and_attribution() -> None:
    async def _run() -> None:
        core = MagicMock()
        core.config.get = AsyncMock(
            side_effect=lambda path, default=None: {
                "trading": {
                    "ensemble": {
                        "enabled": True,
                        "strategies": ["a", "b"],
                        "weight_mode": "bandit_ucb",
                    }
                },
                "instance.id": "test",
            }.get(path, default)
            if path != "instance.id"
            else "test"
        )
        # Fix config.get to handle both patterns used by service
        async def _cfg_get(path, default=None):
            if path == "trading":
                return {
                    "ensemble": {
                        "enabled": True,
                        "strategies": ["a", "b"],
                        "weight_mode": "bandit_ucb",
                    }
                }
            if path == "instance.id":
                return "test"
            return default

        core.config.get = _cfg_get
        published: list = []

        async def _pub(event):
            published.append(event)

        core.event_bus = MagicMock()
        core.event_bus.publish = _pub
        core.event_bus.subscribe = AsyncMock()
        core.event_bus.unsubscribe = AsyncMock()
        core.plugin_manager._instances = {}
        core.rule_engine = None

        svc = EnsembleService(core, "ensemble_service")
        svc.config = svc.default_config()
        svc.config["rebalance_sec"] = 0  # no loop
        svc.config["rebalance_every_fills"] = 1
        await svc.on_initialize()
        svc._ensemble_cfg = {
            "enabled": True,
            "strategies": ["a", "b"],
            "weight_mode": "bandit_ucb",
        }
        await svc.apply_weight_mode(publish=True)
        assert any(e.event_type == "ensemble.weights_changed" for e in published)

        svc.is_running = True
        svc._last_decision = {
            "action": "BUY",
            "ticker": "SBER",
            "contributors": [
                {"strategy_id": "a", "side": "buy", "strength": 1.0},
                {"strategy_id": "b", "side": "buy", "strength": 0.5},
            ],
        }
        before = dict(svc.manager.weights)
        await svc._on_execution(
            KernelEvent(
                event_type="execution.result",
                source_id="order_execution",
                payload={
                    "success": True,
                    "order": {
                        "action": "BUY",
                        "quantity": 10,
                        "price": 100,
                        "strategy_id": "consilium",
                    },
                },
            )
        )
        assert svc.manager.stats._counts["a"] >= 1
        assert svc.manager.stats._counts["b"] >= 1
        # rebalance after fill
        assert svc.manager.weights
        assert abs(sum(svc.manager.weights.values()) - 1.0) < 1e-9
        _ = before

        # signals still aggregate
        await svc._on_signal(
            KernelEvent(
                event_type="strategy.signal",
                source_id="a",
                payload={
                    "action": "BUY",
                    "ticker": "SBER",
                    "strategy_id": "a",
                    "strength": 1.0,
                    "quantity": 1,
                    "price": 100,
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
                    "strength": 1.0,
                    "quantity": 1,
                    "price": 100,
                },
            )
        )
        await _await_event(published, "ensemble.decision")

    asyncio.run(_run())


def test_equal_aggregate_unchanged() -> None:
    ens = EnsembleManager(["a", "b"])
    decs = ens.aggregate(
        [
            StrategySignal("a", "SBER", "buy", 1.0),
            StrategySignal("b", "SBER", "sell", 0.5),
        ]
    )
    assert decs[0].side == "buy"
