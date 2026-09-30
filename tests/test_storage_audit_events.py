"""Durable audit topics: rejects, portfolio, lineup, ops, storage subscribe."""

from __future__ import annotations

import asyncio
from typing import Any

from host.events import (
    publish_ensemble_reject,
    publish_lineup_changed,
    publish_ops_alert,
    publish_portfolio_snapshot,
    publish_risk_reject,
)
from services.storage_service import _SUBSCRIBE


class _Bus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


def test_storage_subscribe_covers_audit_topics() -> None:
    topics = set(_SUBSCRIBE)
    for required in (
        "ensemble.reject",
        "ensemble.weights_changed",
        "risk.reject",
        "portfolio.snapshot",
        "config.lineup_changed",
        "ops.alert",
        "reconciliation.incident",
        "reconciliation.unhealthy",
        "reconciliation.retry_exhausted",
        "universe.snapshot",
        "cost.schedule_activated",
        "system.halted",
        "system.resumed",
        "ai.analyst.request",
        "ai.analyst.response",
    ):
        assert required in topics, required


def test_publish_audit_helpers() -> None:
    async def _run() -> None:
        bus = _Bus()
        await publish_ensemble_reject(
            bus,
            source_id="ensemble_service",
            payload={"reason": "flat_consensus", "ticker": "SBER"},
        )
        await publish_risk_reject(
            bus,
            source_id="order_execution",
            payload={"reason": "halted", "stage": "halted", "ticker": "SBER"},
        )
        await publish_portfolio_snapshot(
            bus,
            source_id="trading_runtime_service",
            payload={"equity": 10000.0, "positions": {}},
        )
        await publish_lineup_changed(
            bus,
            source_id="ui_bridge_service",
            payload={
                "strategies": ["ma_cross_example"],
                "weights": {"ma_cross_example": 1.0},
            },
        )
        await publish_ops_alert(
            bus,
            source_id="ops_monitor_service",
            payload={
                "name": "market_data_anomaly",
                "summary": "SBER: gap",
                "severity": "warning",
            },
        )
        types = [e.event_type for e in bus.events]
        assert types == [
            "ensemble.reject",
            "risk.reject",
            "portfolio.snapshot",
            "config.lineup_changed",
            "ops.alert",
        ]
        assert bus.events[0].payload["reason"] == "flat_consensus"
        assert bus.events[4].payload["name"] == "market_data_anomaly"

    asyncio.run(_run())
