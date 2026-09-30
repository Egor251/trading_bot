"""Event Contract helpers — normalize + typed publish (v0.1)."""

from __future__ import annotations

from typing import Any

from microkernel_core import KernelEvent

SCHEMA_VERSION = 1


class EventPayloadError(ValueError):
    pass


def require_keys(payload: dict[str, Any], keys: tuple[str, ...], event_type: str) -> None:
    missing = [k for k in keys if k not in payload or payload[k] in (None, "")]
    if missing:
        raise EventPayloadError(f"{event_type}: missing {missing}")


def normalize_market_candle(payload: dict[str, Any]) -> dict[str, Any]:
    require_keys(payload, ("ticker", "close"), "market.candle")
    out = dict(payload)
    out["ticker"] = str(out["ticker"])
    out["class_code"] = str(out.get("class_code") or "")
    for k in ("open", "high", "low", "close", "volume"):
        if k in out and out[k] is not None:
            out[k] = float(out[k])
    if "interval_min" in out and out["interval_min"] is not None:
        out["interval_min"] = int(out["interval_min"])
    out.setdefault("schema_version", SCHEMA_VERSION)
    return out


def normalize_strategy_signal(payload: dict[str, Any]) -> dict[str, Any]:
    require_keys(payload, ("action", "ticker"), "strategy.signal")
    out = dict(payload)
    out["action"] = str(out["action"]).upper()
    if out["action"] not in {"BUY", "SELL", "CLOSE", "HOLD", "LONG", "SHORT"}:
        raise EventPayloadError(f"strategy.signal: bad action {out['action']}")
    out["ticker"] = str(out["ticker"])
    out["class_code"] = str(out.get("class_code") or "")
    out["strategy_id"] = str(out.get("strategy_id") or "")
    if "quantity" in out and out["quantity"] is not None:
        out["quantity"] = float(out["quantity"])
    if "price" in out and out["price"] not in (None, ""):
        out["price"] = float(out["price"])
    if "strength" in out and out["strength"] not in (None, ""):
        try:
            out["strength"] = float(out["strength"])
        except (TypeError, ValueError):
            out.pop("strength", None)
    out.setdefault("order_type", "limit")
    out["observe_only"] = bool(out.get("observe_only"))
    out.setdefault("schema_version", SCHEMA_VERSION)
    return out


def normalize_execution_result(payload: dict[str, Any]) -> dict[str, Any]:
    require_keys(payload, ("success",), "execution.result")
    out = dict(payload)
    out["success"] = bool(out["success"])
    out.setdefault("schema_version", SCHEMA_VERSION)
    return out


def normalize_universe_changed(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out["added"] = list(out.get("added") or [])
    out["removed"] = list(out.get("removed") or [])
    out["active_keys"] = list(out.get("active_keys") or [])
    out.setdefault("schema_version", SCHEMA_VERSION)
    return out


def normalize_system_halt(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    out = dict(payload or {})
    out.setdefault("reason", "kill_switch")
    out.setdefault("schema_version", SCHEMA_VERSION)
    return out


def normalize_ensemble_decision(payload: dict[str, Any]) -> dict[str, Any]:
    require_keys(payload, ("action", "ticker"), "ensemble.decision")
    out = dict(payload)
    out["action"] = str(out["action"]).upper()
    out["ticker"] = str(out["ticker"])
    out["strategy_id"] = str(out.get("strategy_id") or "consilium")
    out.setdefault("schema_version", SCHEMA_VERSION)
    return out


def normalize_phantom_fill(payload: dict[str, Any]) -> dict[str, Any]:
    require_keys(payload, ("action", "ticker"), "phantom.fill")
    out = dict(payload)
    out["action"] = str(out["action"]).upper()
    out["ticker"] = str(out["ticker"])
    out.setdefault("shadow", True)
    out.setdefault("schema_version", SCHEMA_VERSION)
    return out


async def publish_market_candle(
    bus: Any,
    *,
    source_id: str,
    payload: dict[str, Any],
    instance_id: str = "local",
    event_type: str = "market.candle",
) -> KernelEvent:
    event = KernelEvent(
        event_type=event_type,
        source_id=source_id,
        payload=normalize_market_candle(payload),
        instance_id=instance_id,
    )
    await bus.publish(event)
    return event


async def publish_market_history_candle(
    bus: Any,
    *,
    source_id: str,
    payload: dict[str, Any],
    instance_id: str = "local",
) -> KernelEvent:
    body = dict(payload)
    body.setdefault("source", body.get("exchange") or "history")
    return await publish_market_candle(
        bus,
        source_id=source_id,
        payload=body,
        instance_id=instance_id,
        event_type="market.history.candle",
    )


async def publish_strategy_signal(
    bus: Any,
    *,
    source_id: str,
    payload: dict[str, Any],
    instance_id: str = "local",
) -> KernelEvent:
    event = KernelEvent(
        event_type="strategy.signal",
        source_id=source_id,
        payload=normalize_strategy_signal(payload),
        instance_id=instance_id,
    )
    await bus.publish(event)
    return event


async def publish_execution_result(
    bus: Any,
    *,
    source_id: str,
    payload: dict[str, Any],
    instance_id: str = "local",
) -> KernelEvent:
    event = KernelEvent(
        event_type="execution.result",
        source_id=source_id,
        payload=normalize_execution_result(payload),
        instance_id=instance_id,
    )
    await bus.publish(event)
    return event


async def publish_ensemble_decision(
    bus: Any,
    *,
    source_id: str,
    payload: dict[str, Any],
    instance_id: str = "local",
) -> KernelEvent:
    event = KernelEvent(
        event_type="ensemble.decision",
        source_id=source_id,
        payload=normalize_ensemble_decision(payload),
        instance_id=instance_id,
    )
    await bus.publish(event)
    return event


async def publish_phantom_fill(
    bus: Any,
    *,
    source_id: str,
    payload: dict[str, Any],
    instance_id: str = "local",
) -> KernelEvent:
    event = KernelEvent(
        event_type="phantom.fill",
        source_id=source_id,
        payload=normalize_phantom_fill(payload),
        instance_id=instance_id,
    )
    await bus.publish(event)
    return event


async def publish_phantom_run(
    bus: Any,
    *,
    source_id: str,
    payload: dict[str, Any],
    instance_id: str = "local",
    finished: bool = False,
) -> KernelEvent:
    event = KernelEvent(
        event_type="phantom.run_finished" if finished else "phantom.run_started",
        source_id=source_id,
        payload={**payload, "schema_version": SCHEMA_VERSION},
        instance_id=instance_id,
    )
    await bus.publish(event)
    return event


def normalize_reject(payload: dict[str, Any], event_type: str) -> dict[str, Any]:
    require_keys(payload, ("reason",), event_type)
    out = dict(payload)
    out["reason"] = str(out["reason"])
    out.setdefault("schema_version", SCHEMA_VERSION)
    return out


async def publish_ensemble_reject(
    bus: Any,
    *,
    source_id: str,
    payload: dict[str, Any],
    instance_id: str = "local",
) -> KernelEvent:
    event = KernelEvent(
        event_type="ensemble.reject",
        source_id=source_id,
        payload=normalize_reject(payload, "ensemble.reject"),
        instance_id=instance_id,
    )
    await bus.publish(event)
    return event


async def publish_ensemble_weights_changed(
    bus: Any,
    *,
    source_id: str,
    payload: dict[str, Any],
    instance_id: str = "local",
) -> KernelEvent:
    out = dict(payload)
    out["weights"] = dict(out.get("weights") or {})
    out["strategies"] = list(out.get("strategies") or [])
    out.setdefault("weight_mode", "equal")
    out.setdefault("schema_version", SCHEMA_VERSION)
    event = KernelEvent(
        event_type="ensemble.weights_changed",
        source_id=source_id,
        payload=out,
        instance_id=instance_id,
    )
    await bus.publish(event)
    return event


async def publish_market_regime(
    bus: Any,
    *,
    source_id: str,
    payload: dict[str, Any],
    instance_id: str = "local",
) -> KernelEvent:
    require_keys(payload, ("regime",), "market.regime")
    out = dict(payload)
    out["regime"] = str(out["regime"])
    out.setdefault("schema_version", SCHEMA_VERSION)
    event = KernelEvent(
        event_type="market.regime",
        source_id=source_id,
        payload=out,
        instance_id=instance_id,
    )
    await bus.publish(event)
    return event


async def publish_risk_reject(
    bus: Any,
    *,
    source_id: str,
    payload: dict[str, Any],
    instance_id: str = "local",
) -> KernelEvent:
    event = KernelEvent(
        event_type="risk.reject",
        source_id=source_id,
        payload=normalize_reject(payload, "risk.reject"),
        instance_id=instance_id,
    )
    await bus.publish(event)
    return event


async def publish_portfolio_snapshot(
    bus: Any,
    *,
    source_id: str,
    payload: dict[str, Any],
    instance_id: str = "local",
) -> KernelEvent:
    out = dict(payload)
    out.setdefault("schema_version", SCHEMA_VERSION)
    event = KernelEvent(
        event_type="portfolio.snapshot",
        source_id=source_id,
        payload=out,
        instance_id=instance_id,
    )
    await bus.publish(event)
    return event


async def publish_lineup_changed(
    bus: Any,
    *,
    source_id: str,
    payload: dict[str, Any],
    instance_id: str = "local",
) -> KernelEvent:
    out = dict(payload)
    out["strategies"] = list(out.get("strategies") or [])
    out["observe"] = list(out.get("observe") or [])
    out["weights"] = dict(out.get("weights") or {})
    out.setdefault("schema_version", SCHEMA_VERSION)
    event = KernelEvent(
        event_type="config.lineup_changed",
        source_id=source_id,
        payload=out,
        instance_id=instance_id,
    )
    await bus.publish(event)
    return event


async def publish_ops_alert(
    bus: Any,
    *,
    source_id: str,
    payload: dict[str, Any],
    instance_id: str = "local",
) -> KernelEvent:
    require_keys(payload, ("name", "summary"), "ops.alert")
    out = dict(payload)
    out["name"] = str(out["name"])
    out["summary"] = str(out["summary"])
    out.setdefault("severity", "info")
    out.setdefault("schema_version", SCHEMA_VERSION)
    event = KernelEvent(
        event_type="ops.alert",
        source_id=source_id,
        payload=out,
        instance_id=instance_id,
    )
    await bus.publish(event)
    return event
