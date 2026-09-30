"""Shared side-change emit helper for public StrategyPlugin packages."""

from __future__ import annotations

from typing import Any

from microkernel_core import KernelEvent

from engine.strategy_plugin import StrategyDecision, StrategyPlugin


async def emit_on_side_change(
    plugin: StrategyPlugin,
    event: KernelEvent,
    decision: StrategyDecision | None,
    *,
    default_qty: float = 0.01,
) -> bool:
    """Emit strategy.signal when actionable side changes. Returns True if emitted.

    ``HOLD`` + ``intent=flat`` after an open side emits the opposite action so
    event-driven exits (turtle/ADX/ROC) actually flatten — previously HOLD was
    ignored and positions stayed open forever.
    """
    if decision is None:
        return False
    action = str(decision.action or "").upper()
    intent = str(decision.intent or "").lower()
    last = getattr(plugin, "_last_side", None)

    emit_action: str | None = None
    if action in {"BUY", "SELL"}:
        if action == last:
            return False
        emit_action = action
    elif action == "HOLD" and intent == "flat" and last in {"BUY", "SELL"}:
        emit_action = "SELL" if last == "BUY" else "BUY"
    else:
        return False

    plugin._last_side = (  # noqa: SLF001
        None if (action == "HOLD" and intent == "flat") else emit_action
    )
    close = float(
        (event.payload or {}).get("close") or decision.metrics.get("close") or 0
    )
    fields = decision.as_signal_fields()
    fields["action"] = emit_action
    if action == "HOLD" and intent == "flat":
        fields["intent"] = "flat"
        fields["reason"] = decision.reason or "exit_flat"
    await plugin.emit_signal(
        {
            **fields,
            "class_code": (event.payload or {}).get(
                "class_code", plugin.config.get("class_code", "CRYPTO")
            ),
            "ticker": (event.payload or {}).get("ticker"),
            "quantity": float(plugin.config.get("quantity", default_qty)),
            "order_type": "limit",
            "price": close,
        }
    )
    sent = int(getattr(plugin, "_signals_sent", 0) or 0)
    plugin._signals_sent = sent + 1  # noqa: SLF001
    return True


def init_side_state(plugin: StrategyPlugin) -> None:
    plugin._signals_sent = 0  # noqa: SLF001
    plugin._last_side = None  # noqa: SLF001


def snapshot_side_state(plugin: StrategyPlugin) -> dict[str, Any]:
    return {
        "last_side": getattr(plugin, "_last_side", None),
        "signals_sent": int(getattr(plugin, "_signals_sent", 0) or 0),
    }


def restore_side_state(plugin: StrategyPlugin, extra: dict[str, Any]) -> None:
    plugin._last_side = extra.get("last_side")  # noqa: SLF001
    plugin._signals_sent = int(extra.get("signals_sent") or 0)  # noqa: SLF001
