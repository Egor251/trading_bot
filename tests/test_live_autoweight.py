"""Stage D: phantom-vs-live diagnosis → Consilium autoweight / pause."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.performance_monitor import DiagnosisKind, PerformanceMonitor  # noqa: E402
from microkernel_core import KernelEvent  # noqa: E402
from services.ensemble_service import EnsembleService  # noqa: E402
from services.ops_monitor_service import OpsMonitorService  # noqa: E402


def test_apply_diagnosis_downweights_negative_and_scales() -> None:
    async def _run() -> None:
        core = MagicMock()
        core.config.get = AsyncMock(return_value={"ensemble": {"strategies": ["good", "bad"]}})
        core.event_bus = MagicMock()
        core.event_bus.publish = AsyncMock()
        core.event_bus.subscribe = AsyncMock()
        core.rule_engine = None
        core.plugin_manager = MagicMock()
        core.plugin_manager._instances = {}

        ens = EnsembleService(core, "ensemble_service")
        ens.config = ens.default_config()
        await ens.on_initialize()
        ens.manager.set_weights({"good": 0.5, "bad": 0.5})
        for _ in range(10):
            ens.manager.stats.record_outcome("good", 0.05)
            ens.manager.stats.record_outcome("bad", -0.05)

        from engine.performance_monitor import Diagnosis

        res = await ens.apply_diagnosis(
            Diagnosis(
                DiagnosisKind.PARAMETER_DRIFT,
                "phantom outperforms live",
                actions=("propose_bayesian_reopt",),
            )
        )
        assert res["ok"] is True
        assert ens._risk_scale == 0.5  # noqa: SLF001
        assert ens.manager.weights["bad"] < ens.manager.weights["good"]

        weak = await ens.apply_diagnosis(
            Diagnosis(
                DiagnosisKind.MARKET_WEAK,
                "both weak",
                actions=("pause_risk_parity_rebalance",),
            )
        )
        assert weak["rebalance_paused"] is True
        ens._ensemble_cfg["weight_mode"] = "bandit_ucb"
        before = dict(ens.manager.weights)
        await ens.apply_weight_mode(publish=False)
        assert ens.manager.weights == before

        healthy = await ens.apply_diagnosis(
            Diagnosis(DiagnosisKind.HEALTHY, "ok")
        )
        assert healthy["rebalance_paused"] is False
        assert ens._risk_scale == 1.0  # noqa: SLF001

    asyncio.run(_run())


def test_ops_evaluate_phantom_vs_live_wires_ensemble() -> None:
    async def _run() -> None:
        core = MagicMock()
        core.config.get = AsyncMock(
            return_value={"performance": {"daily_minimum": 100, "underperform_days": 3}}
        )
        core.event_bus = MagicMock()
        core.event_bus.publish = AsyncMock()
        core.event_bus.subscribe = AsyncMock()
        core.event_bus.unsubscribe = AsyncMock()
        core.service_registry = MagicMock()

        ens = MagicMock()
        ens.apply_diagnosis = AsyncMock(return_value={"ok": True})
        ens._last_regime = "strong_trend"
        ens._last_diagnosis = None

        phantom = MagicMock()
        phantom.config = {"start_equity": 10_000}
        phantom._fills = [
            {"action": "BUY", "quantity": 1, "price": 100, "cost": 0},
            {"action": "SELL", "quantity": 1, "price": 120, "cost": 0},
        ]
        phantom._last_run = {"pnl": 20.0}

        def _get(name):
            if name == "ensemble_service":
                return ens
            if name == "phantom_service":
                return phantom
            return None

        core.service_registry.get = MagicMock(side_effect=_get)

        ops = OpsMonitorService(core, "ops_monitor_service")
        ops.config = ops.default_config()
        await ops.on_initialize()
        assert ops.performance is not None
        base = datetime.now(UTC).date()
        for i in range(3):
            ops.performance.record_day(10.0, day=base - timedelta(days=2 - i))
        ops._day_pnl = 10.0  # noqa: SLF001
        ops._day_key = base.isoformat()  # noqa: SLF001

        out = await ops.evaluate_phantom_vs_live(force=True)
        assert out["ok"] is True
        assert out["diagnosis"]["kind"] in {
            DiagnosisKind.PARAMETER_DRIFT.value,
            DiagnosisKind.STRUCTURAL_BREAK.value,
            DiagnosisKind.MARKET_WEAK.value,
            DiagnosisKind.HEALTHY.value,
        }
        ens.apply_diagnosis.assert_awaited()

        # Nested execution.result shape is accepted for journal/perf.
        await ops._on_fill(  # noqa: SLF001
            KernelEvent(
                event_type="execution.result",
                source_id="test",
                payload={
                    "success": True,
                    "order": {
                        "status": "filled",
                        "ticker": "BTC/USDT",
                        "action": "BUY",
                        "quantity": 1,
                        "price": 100,
                        "strategy_id": "a",
                        "cost": 0.1,
                    },
                },
            )
        )
        assert ops.journal is not None
        assert len(ops.journal.entries()) >= 1

    asyncio.run(_run())


if __name__ == "__main__":
    test_apply_diagnosis_downweights_negative_and_scales()
    test_ops_evaluate_phantom_vs_live_wires_ensemble()
    print("live autoweight OK")
