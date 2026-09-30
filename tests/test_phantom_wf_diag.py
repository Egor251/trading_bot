"""Phantom scoped reset, strategy sizing, audit helpers."""

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


async def _svc() -> PhantomService:
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
    svc.config["strategy_sizing"] = {
        "macd_trend": {"max_position_pct": 0.05, "sizing_value": 5}
    }
    await svc.on_initialize()
    return svc


def test_sizing_for_strategy_override() -> None:
    async def _run() -> None:
        svc = await _svc()
        base = svc.sizing_for_strategy(None)
        assert base.max_position_pct == 0.25
        over = svc.sizing_for_strategy("macd_trend")
        assert over.max_position_pct == 0.05
        assert float(over.value) == 5.0

    asyncio.run(_run())


def test_scoped_reset_and_audit() -> None:
    async def _run() -> None:
        svc = await _svc()
        svc._fills = [
            {
                "strategy_id": "macd_trend",
                "ticker": "BTC",
                "class_code": "CRYPTO",
                "action": "BUY",
                "quantity": 1,
                "price": 100,
                "status": "filled",
            },
            {
                "strategy_id": "ema_cross",
                "ticker": "BTC",
                "class_code": "CRYPTO",
                "action": "BUY",
                "quantity": 1,
                "price": 100,
                "status": "rejected",
                "reason": "strength_below_floor=0.01<0.25",
            },
        ]
        denied = svc.reset_ledger(strategy_id="macd_trend")
        assert denied["ok"] is False
        ok = svc.reset_ledger(strategy_id="macd_trend", confirm_scoped=True)
        assert ok["ok"] is True
        assert ok["removed"] == 1
        assert len(svc._fills) == 1
        audit = svc.phantom_audit(strategy_id="ema_cross")
        assert audit["reject_count"] >= 1
        assert audit["rejects"][0]["reason"].startswith("strength_below_floor")

    asyncio.run(_run())


if __name__ == "__main__":
    test_sizing_for_strategy_override()
    test_scoped_reset_and_audit()
    print("phantom wf diag OK")
