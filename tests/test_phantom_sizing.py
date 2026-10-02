"""Phantom shadow fills use PositionSizer (not plugin quantity=0.001)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.position_sizing import SizingMode  # noqa: E402
from services.phantom_service import PhantomService  # noqa: E402


async def _phantom_percent_equity_notional() -> None:
    core = MagicMock()
    core.config.get = AsyncMock(
        side_effect=lambda key, default=None: {
            "trading": {
                "equity": 10_000,
                "legacy_fee_bps": 0,
                "legacy_slippage_bps": 0,
                "risk": {"max_position_pct": 1.0, "max_leverage": 5.0},
            },
            "instance.id": "local",
            "costs": {},
        }.get(key, default)
    )
    core.event_bus = MagicMock()
    core.event_bus.publish = AsyncMock()
    core.event_bus.subscribe = AsyncMock()
    core.event_bus.unsubscribe = AsyncMock()
    core.service_registry.get = MagicMock(return_value=None)

    svc = PhantomService(core, "phantom_service")
    svc.config = svc.default_config()
    svc.config["fee_bps"] = 0.0
    svc.config["slippage_bps"] = 0.0
    svc.config["start_equity"] = 10_000.0
    await svc.on_initialize()
    svc.apply_sizing_config(
        {
            "mode": SizingMode.PERCENT_OF_EQUITY.value,
            "value": 10,
            "max_position_pct": 0.5,
            "scale_by_strength": False,
        }
    )
    assert svc.runtime is not None
    svc.runtime.candles.ingest_payload(
        {
            "ticker": "PH",
            "class_code": "SYNTH",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 10,
            "date": "2026-01-01",
            "time": "00:00:00",
        }
    )
    svc._bar_index = 0
    svc._capturing = True
    fill = await svc._shadow_fill(
        {
            "action": "BUY",
            "ticker": "PH",
            "class_code": "SYNTH",
            "quantity": 0.001,  # crypto leftover — must be overridden
            "price": 100,
            "strategy_id": "ma_cross_example",
            "strength": 1.0,
        }
    )
    assert abs(float(fill["quantity"]) - 10.0) < 1e-6
    assert fill.get("sizing_mode") == "percent_of_equity"


def test_phantom_percent_equity_notional() -> None:
    asyncio.run(_phantom_percent_equity_notional())


if __name__ == "__main__":
    test_phantom_percent_equity_notional()
    print("phantom sizing OK")
