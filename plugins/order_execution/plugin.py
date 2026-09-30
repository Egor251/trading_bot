"""Исполнение заявок — TRIGGERED plugin через TradingRuntime."""

from __future__ import annotations

from typing import Any

from microkernel_core import BasePlugin, PluginKind, PluginRunMode

from engine.fills import extract_result_cost
from engine.order import OrderStatus
from engine.runtime import get_runtime, resolve_quik_broker
from host.events import (
    publish_ensemble_reject,
    publish_execution_result,
    publish_risk_reject,
)


class OrderExecutionPlugin(BasePlugin):
    PLUGIN_ID = "order_execution"
    PLUGIN_NAME = "Order Execution"
    PLUGIN_KIND = PluginKind.ACTUATOR
    RUN_MODE = PluginRunMode.TRIGGERED

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._orders: list[dict[str, Any]] = []

    async def execute_action(
        self,
        action_name: str,
        params: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if action_name != "place_order":
            return await super().execute_action(action_name, params, context)

        signal = self._extract_signal(context)
        if not signal:
            return {"success": False, "error": "no strategy.signal in context"}

        # Consilium path: only ensemble.decision (or strategy_id=consilium) executes
        trading_cfg = {}
        try:
            trading_cfg = await self.core.config.get("trading", {}) or {}
        except Exception:
            trading_cfg = {}
        ens = trading_cfg.get("ensemble") or {}
        et = str(signal.get("event_type") or "")
        sid = str(signal.get("strategy_id") or "")
        instance_id = "local"
        try:
            instance_id = str(
                await self.core.config.get("instance.id", "local") or "local"
            )
        except Exception:
            pass

        if ens.get("enabled", True):
            if et == "ensemble.decision" or sid == "consilium" or "weights" in signal:
                pass  # allow
            elif et in {"", "strategy.signal"}:
                skip = {
                    "success": False,
                    "error": "ensemble_enabled_skip_direct",
                    "skipped": True,
                }
                await publish_ensemble_reject(
                    self.core.event_bus,
                    source_id=self.component_id,
                    payload={
                        "reason": "ensemble_enabled_skip_direct",
                        "ticker": signal.get("ticker"),
                        "action": signal.get("action"),
                        "strategy_id": sid,
                        "stage": "order_execution",
                    },
                    instance_id=instance_id,
                )
                return skip

        runtime = get_runtime(self.core)
        mode = str(
            (runtime.mode if runtime else None)
            or self.config.get("mode", "paper")
        ).lower()

        if runtime is None:
            return {"success": False, "error": "trading_runtime not ready"}

        if runtime.halted:
            reason = runtime._halt_reason or "halted"
            payload = {
                "success": False,
                "mode": mode,
                "error": "halted",
                "reason": reason,
            }
            await publish_execution_result(
                self.core.event_bus,
                source_id=self.component_id,
                payload=payload,
                instance_id=instance_id,
            )
            await publish_risk_reject(
                self.core.event_bus,
                source_id=self.component_id,
                payload={
                    "reason": reason,
                    "stage": "halted",
                    "ticker": signal.get("ticker"),
                    "action": signal.get("action"),
                    "strategy_id": sid,
                },
                instance_id=instance_id,
            )
            return payload

        # Keep broker handle fresh when QUIK service is (re)configured
        if mode in {"quik", "live"} and runtime.broker is None:
            runtime.broker = resolve_quik_broker(self.core)

        qty = float(signal.get("quantity") or params.get("quantity") or 1)
        try:
            strength = (
                float(signal["strength"])
                if signal.get("strength") is not None
                else None
            )
        except (TypeError, ValueError):
            strength = None
        sizing = getattr(runtime, "sizing_config", None)
        if (
            sizing is not None
            and getattr(sizing, "rejects_strength", None) is not None
            and sizing.rejects_strength(strength)
        ):
            reason = (
                f"strength_below_floor={float(strength or 0):.6g}"
                f"<{float(sizing.strength_floor):.4g}"
            )
            await publish_risk_reject(
                self.core.event_bus,
                source_id=self.component_id,
                payload={
                    "reason": reason,
                    "stage": "order_execution_strength_floor",
                    "ticker": signal.get("ticker"),
                    "action": signal.get("action"),
                    "quantity": qty,
                    "strength": strength,
                    "strategy_id": sid or signal.get("strategy_id") or "consilium",
                },
                instance_id=instance_id,
            )
            return {
                "success": False,
                "mode": mode,
                "via": "order_execution",
                "error": reason,
                "message": f"rejected: {reason}",
            }
        result = await runtime.submit_signal_async(
            ticker=str(signal.get("ticker") or ""),
            action=str(signal.get("action") or "BUY"),
            quantity=qty,
            strategy_id=str(signal.get("strategy_id") or ""),
            class_code=str(signal.get("class_code") or ""),
            mark_price=(
                float(signal["price"])
                if signal.get("price") not in (None, "")
                else None
            ),
            strength=strength,
        )

        cost = extract_result_cost(result, runtime)
        order = {
            "mode": mode,
            "ticker": signal.get("ticker"),
            "class_code": signal.get("class_code"),
            "action": signal.get("action"),
            "quantity": result.filled_qty or qty,
            "price": result.avg_price,
            "status": result.status.value,
            "reason": result.reason,
            "client_id": result.client_id,
            "strategy_id": sid or signal.get("strategy_id"),
            "cost": cost,
        }
        self._orders.append(order)

        ok = result.status in {
            OrderStatus.FILLED,
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIAL,
        }
        payload = {
            "success": ok,
            "mode": mode,
            "via": (
                "rule_engine"
                if et == "ensemble.decision" or sid == "consilium" or "weights" in signal
                else mode
            ),
            "order": order,
            "cost": cost,
            "contributors": list(signal.get("contributors") or []),
            "weights": dict(signal.get("weights") or {})
            if isinstance(signal.get("weights"), dict)
            else {},
            "error": None if ok else result.reason,
            "message": (
                f"{mode} {order['action']} {order['quantity']} {order['ticker']}"
                if ok
                else f"rejected: {result.reason}"
            ),
        }
        await publish_execution_result(
            self.core.event_bus,
            source_id=self.component_id,
            payload=payload,
            instance_id=instance_id,
        )
        soft_full = str(result.reason or "").startswith("no_leverage_headroom")
        if not ok and not soft_full:
            await publish_risk_reject(
                self.core.event_bus,
                source_id=self.component_id,
                payload={
                    "reason": str(result.reason or "rejected"),
                    "stage": "order_execution",
                    "ticker": signal.get("ticker"),
                    "action": signal.get("action"),
                    "strategy_id": sid or "consilium",
                    "status": result.status.value,
                    "mode": mode,
                },
                instance_id=instance_id,
            )
        return payload

    @staticmethod
    def _extract_signal(context: dict[str, Any]) -> dict[str, Any] | None:
        for _component_id, data in context.items():
            if not isinstance(data, dict):
                continue
            if data.get("event_type") in {"strategy.signal", "ensemble.decision"}:
                return data
            if data.get("action") in {"BUY", "SELL", "CLOSE"}:
                return data
        return None

    def describe(self) -> list[str]:
        runtime = get_runtime(self.core)
        mode = runtime.mode if runtime else self.config.get("mode", "paper")
        halted = runtime.halted if runtime else False
        broker = type(runtime.broker).__name__ if runtime and runtime.broker else "none"
        return [
            "Execution",
            f"mode={mode}, broker={broker}, orders={len(self._orders)}, halted={halted}",
        ]
