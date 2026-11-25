"""Maker/taker cost schedule and rebate evidence — known-answer pins."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.cost_calibration import cost_model_from_config  # noqa: E402
from engine.execution import CcxtExecutionAdapter  # noqa: E402
from host.events import KernelEvent  # noqa: E402
from services.ops_monitor_service import OpsMonitorService  # noqa: E402


def _cfg():
    return {
        "services": {
            "phantom_service": {"fee_bps": 5, "slippage_bps": 2},
            "ops_monitor_service": {"journal_path": "ops/trades.jsonl"},
        },
        "maker_cost_schedule": {
            "maker_fee_bps": 0, "maker_slippage_bps": 0,
            "taker_fee_bps": 5, "taker_slippage_bps": 2,
        },
    }


def test_taker_model_is_unchanged_and_maker_is_free():
    assert cost_model_from_config(_cfg())["total_bps"] == 7.0
    maker = cost_model_from_config(_cfg(), liquidity="maker")
    assert maker["fee_bps"] == 0.0
    assert maker["slippage_bps"] == 0.0
    assert maker["total_bps"] == 0.0


def test_venue_echo_carries_maker_flag_and_tif():
    adapter = CcxtExecutionAdapter(object(), venue="bitget", account="default")
    order = adapter._order({  # noqa: SLF001
        "id": "1", "symbol": "BTC/USDT", "side": "buy", "amount": 1,
        "price": 100, "status": "open", "takerOrMaker": "maker",
    })
    assert order.raw["liquidity"] == "maker"
    original = adapter._order(  # noqa: SLF001
        {"id": "2", "order": "1", "symbol": "BTC/USDT", "side": "buy",
         "amount": 1, "price": 100, "status": "filled"},
        original=order,
    )
    assert original.time_in_force == order.time_in_force


def _service(tmp_path):
    core = MagicMock()
    core.service_registry.get = MagicMock(return_value=None)
    core.config.get = AsyncMock(side_effect=lambda key, default=None: default)
    core.config.raw = _cfg()
    ops = OpsMonitorService(core, "ops_monitor_service")
    ops.config = ops.default_config()
    ops.config["journal_path"] = str(tmp_path / "trades.jsonl")
    ops.config["alerts_path"] = str(tmp_path / "alerts.jsonl")
    return ops


def test_negative_fee_is_retained_as_calibration_evidence(tmp_path):
    ops = _service(tmp_path)
    asyncio.run(ops.on_initialize())
    payload = {
        "success": True, "ticker": "BTC/USDT", "action": "BUY",
        "quantity": 1.0, "price": 100.0, "fee": -0.02,
        "reference_price": 99.9, "liquidity": "maker", "strategy_id": "a",
    }
    asyncio.run(ops._on_fill(KernelEvent(  # noqa: SLF001
        event_type="execution.result", source_id="test", payload=payload
    )))
    assert ops._fill_samples and ops._fill_samples[-1].actual_fee == -0.02  # noqa: SLF001
    row = json.loads((tmp_path / "trades.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["meta"]["actual_fee"] == -0.02
