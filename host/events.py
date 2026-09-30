"""Хелперы для публикации доменных событий trading_bot."""

from __future__ import annotations

from typing import Any

from microkernel_core import KernelEvent, MicrokernelCore


async def publish_market_event(
    core: MicrokernelCore,
    event_type: str,
    source_id: str,
    payload: dict[str, Any],
) -> None:
    instance_id = str(await core.config.get("instance.id", "local") or "local")
    await core.event_bus.publish(
        KernelEvent(
            event_type=event_type,
            source_id=source_id,
            payload=payload,
            instance_id=instance_id,
        )
    )
