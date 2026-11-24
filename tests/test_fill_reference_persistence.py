"""The fill journal must survive a restart with enough data to calibrate costs.

`ExecutionObservation` in memory already carries `reference_price`, but the
row written to `trades.jsonl` did not. After a restart, `calibrate` had zero
usable observations no matter how many real fills had occurred. Persist the
decision price and the venue fee in the journal's existing `meta` field;
a fill without a reference price stays unusable rather than being invented.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from host.events import KernelEvent  # noqa: E402
from services.ops_monitor_service import OpsMonitorService  # noqa: E402


def _event(reference: float | None) -> KernelEvent:
    order = {
        "status": "filled",
        "ticker": "BTC/USDT",
        "action": "BUY",
        "quantity": 1.0,
        "price": 50_100.0,
        "cost": 0.7,
    }
    if reference is not None:
        order["reference_price"] = reference
    return KernelEvent(
        event_type="execution.result",
        source_id="test",
        payload={"success": True, "order": order},
    )


def _service(tmp_path):
    core = MagicMock()
    core.service_registry.get = MagicMock(return_value=None)
    core.config.get = AsyncMock(side_effect=lambda key, default=None: default)
    ops = OpsMonitorService(core, "ops_monitor_service")
    ops.config = ops.default_config()
    ops.config["journal_path"] = str(tmp_path / "trades.jsonl")
    ops.config["alerts_path"] = str(tmp_path / "alerts.jsonl")
    return ops


def test_journal_persists_reference_price_and_actual_fee(tmp_path):
    ops = _service(tmp_path)
    asyncio.run(ops.on_initialize())
    asyncio.run(ops._on_fill(_event(50_000.0)))  # noqa: SLF001
    row = json.loads(
        (tmp_path / "trades.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1]
    )
    assert row["meta"]["reference_price"] == 50_000.0
    assert row["meta"]["reported_fee"] == 0.7


def test_fill_without_reference_price_is_recorded_but_unusable(tmp_path):
    ops = _service(tmp_path)
    asyncio.run(ops.on_initialize())
    asyncio.run(ops._on_fill(_event(None)))  # noqa: SLF001
    row = json.loads(
        (tmp_path / "trades.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1]
    )
    assert "reference_price" not in row["meta"]
    assert not list(getattr(ops, "_fill_samples", []))
