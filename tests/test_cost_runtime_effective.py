"""Legacy-bps schedule must be inspectable when no draft is activated."""

from __future__ import annotations

from pathlib import Path

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.cost_workflow import CostScheduleStore
from engine.runtime import TradingRuntime


def test_from_config_publishes_runtime_effective_legacy_schedule(tmp_path: Path) -> None:
    store_path = tmp_path / "cost_schedules"
    runtime = TradingRuntime.from_config(
        {
            "mode": "paper",
            "equity": 10_000,
            "legacy_fee_bps": 5,
            "legacy_slippage_bps": 2,
            "cost_store_path": str(store_path),
            "risk": {"max_position_pct": 1.0, "max_leverage": 1.0},
        }
    )
    assert runtime.orders.cost_schedule is not None
    assert runtime.orders.cost_schedule.version == "legacy-bps"
    store = CostScheduleStore(store_path)
    assert store.load_active_schedule() is None
    effective = store.load_runtime_effective()
    assert effective is not None
    assert effective.version == "legacy-bps"
