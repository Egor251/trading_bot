"""Trading runtime service — candle buffer, risk/orders, kill switch."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from microkernel_core import BaseService, KernelEvent

from engine.runtime import TradingRuntime, resolve_quik_broker
from host.events import publish_portfolio_snapshot

if TYPE_CHECKING:
    from microkernel_core import MicrokernelCore


class TradingRuntimeService(BaseService):
    """
    Owns ``core.trading`` (TradingRuntime).

    Subscribes to ``market.candle``, ``system.halt``, ``system.resume``,
    and ``cost.schedule_activated``.
    Publishes throttled ``portfolio.snapshot`` for durable equity/positions.
    """

    def __init__(self, core: MicrokernelCore, component_id: str) -> None:
        super().__init__(core, component_id)
        self.runtime: TradingRuntime | None = None
        self._last_portfolio_pub = 0.0
        self._last_equity_pub: float | None = None

    def default_config(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "auto_start": True,
            "portfolio_snapshot_sec": 60.0,
            "portfolio_equity_epsilon": 1.0,
        }

    async def on_initialize(self) -> None:
        trading_cfg = dict(await self.core.config.get("trading", {}) or {})
        root = Path(__file__).resolve().parents[2]
        if not trading_cfg.get("cost_store_path"):
            costs = await self.core.config.get("costs", {}) or {}
            configured = (costs or {}).get("store_path") if isinstance(costs, dict) else None
            trading_cfg["cost_store_path"] = str(
                Path(configured) if configured else root / "data" / "cost_schedules"
            )
        self.runtime = TradingRuntime.from_config(trading_cfg)
        setattr(self.core, "trading", self.runtime)
        self._attach_execution_handles()
        # Rehydrate the protective layer from disk. Without this a restart began
        # with a naked book: `protection_state` was only ever read, never written,
        # so the durable snapshot was ignored and the engine came up believing it
        # held no positions while the broker held a real one.
        try:
            self.recovered_state = self.runtime.recover_from_disk()
        except Exception:
            self.recovered_state = None

    def _attach_execution_handles(self) -> None:
        if self.runtime is None:
            return
        router = getattr(self.core, "execution_router", None)
        reconciliation = getattr(self.core, "reconciliation", None)
        if router is not None:
            self.runtime.execution_router = router
        if reconciliation is not None:
            self.runtime.reconciliation = reconciliation

    def _register_strategy_profiles(self) -> None:
        if self.runtime is None:
            return
        pm = getattr(self.core, "plugin_manager", None)
        if pm is None:
            return
        instances = getattr(pm, "_instances", {}) or {}
        for plugin_id, plugin in instances.items():
            risk_profile = getattr(plugin, "risk_profile", None)
            if callable(risk_profile):
                try:
                    self.runtime.register_strategy_profile(
                        str(plugin_id), risk_profile()
                    )
                except Exception:
                    continue

    async def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self._attach_broker()
        self._attach_execution_handles()
        self._register_strategy_profiles()
        await self.core.event_bus.subscribe("market.candle", self._on_candle)
        await self.core.event_bus.subscribe("system.halt", self._on_halt)
        await self.core.event_bus.subscribe("system.resume", self._on_resume)
        await self.core.event_bus.subscribe(
            "cost.schedule_activated", self._on_cost_activated
        )
        await self.core.event_bus.subscribe(
            "execution.result", self._on_execution_result
        )

    def _attach_broker(self) -> None:
        """Wire QuikBrokerExecution when quik_market_service has a real transport."""
        if self.runtime is None:
            return
        broker = resolve_quik_broker(self.core)
        if broker is not None:
            self.runtime.broker = broker

    async def stop(self) -> None:
        self.is_running = False
        for et, handler in (
            ("market.candle", self._on_candle),
            ("system.halt", self._on_halt),
            ("system.resume", self._on_resume),
            ("cost.schedule_activated", self._on_cost_activated),
            ("execution.result", self._on_execution_result),
        ):
            try:
                await self.core.event_bus.unsubscribe(et, handler)
            except Exception:
                pass

    async def get_status(self) -> dict[str, Any]:
        status = await super().get_status()
        if self.runtime:
            status.update(self.runtime.snapshot())
        return status

    async def _on_candle(self, event: KernelEvent) -> None:
        if self.runtime is None:
            return
        bar = self.runtime.candles.ingest_payload(event.payload)
        if bar.close > 0:
            self.runtime.orders.mark_to_market(bar.key, bar.close)
            mode = (self.runtime.mode or "paper").lower()
            if mode in {"paper", "sim", "simulation", "phantom", "replay"}:
                self.runtime.process_bar(bar)
            else:
                await self.runtime.process_bar_async(bar)
        await self._maybe_publish_portfolio(force=False)

    async def _on_execution_result(self, event: KernelEvent) -> None:
        payload = event.payload or {}
        if payload.get("success"):
            await self._maybe_publish_portfolio(force=True)

    async def _maybe_publish_portfolio(self, *, force: bool) -> None:
        if self.runtime is None:
            return
        now = time.time()
        interval = float(self.config.get("portfolio_snapshot_sec") or 60.0)
        equity = float(self.runtime.orders.equity or 0.0)
        eps = float(self.config.get("portfolio_equity_epsilon") or 1.0)
        equity_moved = (
            self._last_equity_pub is None
            or abs(equity - float(self._last_equity_pub)) >= eps
        )
        due = (now - self._last_portfolio_pub) >= interval
        if not force and not (due and equity_moved):
            return
        snap = self.runtime.snapshot()
        instance_id = str(
            await self.core.config.get("instance.id", "local") or "local"
        )
        await publish_portfolio_snapshot(
            self.core.event_bus,
            source_id=self.component_id,
            payload={
                "mode": snap.get("mode") or self.runtime.mode,
                "equity": snap.get("equity"),
                "cash": snap.get("cash"),
                "positions": snap.get("positions") or {},
                "open_positions": (
                    self.runtime.open_positions()
                    if hasattr(self.runtime, "open_positions")
                    else []
                ),
                "halted": bool(snap.get("halted")),
                "halt_reason": snap.get("halt_reason") or "",
                "force": bool(force),
            },
            instance_id=instance_id,
        )
        self._last_portfolio_pub = now
        self._last_equity_pub = equity

    async def _on_cost_activated(self, event: KernelEvent) -> None:
        if self.runtime is None:
            return
        self.runtime.reload_active_cost_schedule()

    async def _on_halt(self, event: KernelEvent) -> None:
        if self.runtime is None:
            return
        reason = str(event.payload.get("reason") or "kill_switch")
        self.runtime.halt(reason)
        await self.core.event_bus.publish(
            KernelEvent(
                event_type="system.halted",
                source_id=self.component_id,
                payload={"reason": reason},
                instance_id=str(
                    await self.core.config.get("instance.id", "local") or "local"
                ),
            )
        )

    async def _on_resume(self, event: KernelEvent) -> None:
        if self.runtime is None:
            return
        self.runtime.resume()
        await self.core.event_bus.publish(
            KernelEvent(
                event_type="system.resumed",
                source_id=self.component_id,
                payload={},
                instance_id=str(
                    await self.core.config.get("instance.id", "local") or "local"
                ),
            )
        )
