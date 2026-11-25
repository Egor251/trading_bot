"""Durable multi-broker execution routing and reconciliation service."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from microkernel_core import BaseService, KernelEvent

from engine.execution import (
    AlpacaExecutionAdapter,
    CcxtExecutionAdapter,
    ExecutionRouter,
    OrderStore,
    QuikExecutionAdapter,
)
from engine.paths import data_path
from engine.reconciliation import (
    IncidentKind,
    ReconciliationIncident,
    ReconciliationService,
)

if TYPE_CHECKING:
    from microkernel_core import MicrokernelCore


class ExecutionService(BaseService):
    """Owns the durable router; adapters are attached as broker clients appear."""

    def __init__(self, core: MicrokernelCore, component_id: str) -> None:
        super().__init__(core, component_id)
        self.store: OrderStore | None = None
        self.router: ExecutionRouter | None = None
        self.reconciliation: ReconciliationService | None = None
        self._task: asyncio.Task[None] | None = None
        self._registered: set[tuple[str, str]] = set()
        self._unhealthy_since: float | None = None
        self._last_unhealthy_alert: float = 0.0

    def default_config(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "auto_start": True,
            "store_path": str(data_path("execution", "orders.json")),
            "attach_interval_sec": 2,
            "reconcile_interval_sec": 30,
            "reconcile_retry_sec": 5,
            "reconcile_retry_attempts": 3,
            "unhealthy_alert_sec": 300,
            # A working order left resting on the venue is live risk: it can fill
            # long after its signal stopped mattering, and venues that ignore
            # `time_in_force` never remove it. 0 disables the sweep.
            "stale_order_ttl_sec": 900,
            "stale_order_sweep_interval_sec": 60,
            # Post-only is opt-in at the decision path; false disables maker routing
            # for venues that reject GTX/post-only orders.
            "post_only": False,
            "accounts": {"quik": "default", "alpaca": "default"},
        }

    async def on_initialize(self) -> None:
        path = Path(
            str(self.config.get("store_path") or data_path("execution", "orders.json"))
        )
        self.store = OrderStore(path)
        self.router = ExecutionRouter(self.store)
        self.reconciliation = ReconciliationService(
            self.router,
            self.store,
            incident_sink=self._publish_incident,
        )
        setattr(self.core, "execution_router", self.router)
        setattr(self.core, "reconciliation", self.reconciliation)
        self._wire_runtime()

    def _wire_runtime(self) -> None:
        runtime = getattr(self.core, "trading", None)
        if runtime is not None:
            runtime.execution_router = self.router
            runtime.reconciliation = self.reconciliation

    async def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self._wire_runtime()
        await self._attach_available()
        await self._startup_reconcile()
        self._task = asyncio.create_task(self._maintenance_loop())

    async def stop(self) -> None:
        self.is_running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _maintenance_loop(self) -> None:
        last_reconcile = 0.0
        loop = asyncio.get_running_loop()
        while self.is_running:
            await self._attach_available()
            now = loop.time()
            if now - last_reconcile >= float(
                self.config.get("reconcile_interval_sec") or 30
            ):
                await self._reconcile_all()
                await self._maybe_alert_unhealthy(now)
                last_reconcile = now
            await self._sweep_stale_orders(now)
            await asyncio.sleep(
                max(0.5, float(self.config.get("attach_interval_sec") or 2))
            )

    async def _sweep_stale_orders(self, now: float) -> None:
        """Reclaim resting orders that outlived their TTL."""
        if self.router is None:
            return
        ttl = float(self.config.get("stale_order_ttl_sec") or 0)
        if ttl <= 0:
            return
        interval = max(1.0, float(self.config.get("stale_order_sweep_interval_sec") or 60))
        if now - float(getattr(self, "_last_sweep", 0.0)) < interval:
            return
        self._last_sweep = now
        try:
            cancelled = await self.router.sweep_stale(ttl)
        except Exception:  # noqa: BLE001 - a sweep failure must not stop trading
            return
        for order in cancelled:
            self._log().info(
                "stale order cancelled: %s %s %s ttl=%ss",
                order.venue,
                order.symbol,
                order.client_id,
                ttl,
            )

    async def _attach_available(self) -> None:
        if self.router is None:
            return
        accounts = dict(self.config.get("accounts") or {})

        quik = self.core.service_registry.get("quik_market_service")
        quik_broker = getattr(quik, "broker", None)
        transport = getattr(quik_broker, "transport", None)
        if transport is not None:
            account = str(
                (getattr(quik, "config", {}) or {}).get("account")
                or accounts.get("quik")
                or "default"
            )
            self._register(
                "quik",
                account,
                QuikExecutionAdapter(
                    transport,
                    account=account,
                    class_code=str(
                        (getattr(quik, "config", {}) or {}).get("class_code")
                        or "TQBR"
                    ),
                ),
            )

        ccxt = self.core.service_registry.get("ccxt_market_service")
        ccxt_adapter = getattr(ccxt, "_ccxt", None)
        client = getattr(ccxt_adapter, "_exchange", None)
        if client is not None:
            venue = str(getattr(ccxt_adapter, "exchange_id", "ccxt"))
            account = str(accounts.get(venue) or accounts.get("ccxt") or "default")
            adapter = CcxtExecutionAdapter(client, venue=venue, account=account)
            adapter.post_only_enabled = bool(self.config.get("post_only", True))
            self._register(venue, account, adapter)

        alpaca = self.core.service_registry.get("alpaca_market_service")
        alpaca_adapter = getattr(alpaca, "_alpaca", None)
        client = getattr(alpaca_adapter, "_trading_client", None)
        if client is not None:
            account = str(accounts.get("alpaca") or "default")
            self._register(
                "alpaca",
                account,
                AlpacaExecutionAdapter(client, account=account),
            )
        self._wire_runtime()

    def _register(self, venue: str, account: str, adapter: Any) -> None:
        key = (venue, account)
        if key in self._registered or self.router is None:
            return
        self.router.register(venue, account, adapter)
        self._registered.add(key)

    def _live_recon_mode(self) -> bool:
        runtime = getattr(self.core, "trading", None)
        mode = str(getattr(runtime, "mode", "") or "").lower()
        return mode in {"live", "quik"}

    def _clear_stale_broker_incidents(self) -> int:
        """Drop leftover BROKER_ERROR rows from paper/fake sessions."""
        if self.reconciliation is None:
            return 0
        cleared = 0
        for item in list(self.reconciliation.incidents):
            if item.active and item.kind is IncidentKind.BROKER_ERROR:
                try:
                    self.reconciliation.resolve(item.incident_id)
                    cleared += 1
                except Exception:
                    item.resolved_at = datetime.now(UTC)
                    cleared += 1
        if cleared and hasattr(self.reconciliation, "_persist_incidents"):
            self.reconciliation._persist_incidents()  # noqa: SLF001
        return cleared

    async def _startup_reconcile(self) -> None:
        if self.reconciliation is None:
            return
        if not self._live_recon_mode():
            # Paper/sim must not keep multi-day FakeQuik/binance snapshot breaks.
            self._clear_stale_broker_incidents()
            return
        for venue, account in sorted(self._registered):
            await self.reconciliation.startup(venue, account)

    async def _reconcile_all(self) -> None:
        if self.reconciliation is None:
            return
        if not self._live_recon_mode():
            if self.reconciliation.new_risk_blocked:
                self._clear_stale_broker_incidents()
            return
        attempts = max(1, int(self.config.get("reconcile_retry_attempts") or 3))
        retry_sec = max(0.1, float(self.config.get("reconcile_retry_sec") or 5))
        for venue, account in sorted(self._registered):
            last_error: Exception | None = None
            for attempt in range(attempts):
                try:
                    result = await self.reconciliation.reconcile(venue, account)
                    # Retry only hard broker snapshot failures; other breaks stay.
                    broker_errors = [
                        i
                        for i in result.incidents
                        if i.kind is IncidentKind.BROKER_ERROR and i.active
                    ]
                    if not broker_errors:
                        last_error = None
                        break
                    last_error = RuntimeError(
                        broker_errors[0].message or "broker snapshot failed"
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(retry_sec)
            if last_error is not None and self.reconciliation.new_risk_blocked:
                await self.core.event_bus.publish(
                    KernelEvent(
                        event_type="reconciliation.retry_exhausted",
                        source_id=self.component_id,
                        payload={
                            "venue": venue,
                            "account": account,
                            "error": str(last_error),
                            "attempts": attempts,
                        },
                        instance_id=str(
                            await self.core.config.get("instance.id", "local")
                            or "local"
                        ),
                    )
                )

    async def _maybe_alert_unhealthy(self, now: float) -> None:
        if self.reconciliation is None:
            return
        unhealthy = bool(self.reconciliation.new_risk_blocked)
        if not unhealthy:
            self._unhealthy_since = None
            return
        if self._unhealthy_since is None:
            self._unhealthy_since = now
        alert_after = max(30.0, float(self.config.get("unhealthy_alert_sec") or 300))
        if now - self._unhealthy_since < alert_after:
            return
        if now - self._last_unhealthy_alert < alert_after:
            return
        self._last_unhealthy_alert = now
        await self.core.event_bus.publish(
            KernelEvent(
                event_type="reconciliation.unhealthy",
                source_id=self.component_id,
                payload={
                    "unhealthy_for_sec": round(now - self._unhealthy_since, 1),
                    "incidents": [
                        item.to_dict()
                        for item in self.reconciliation.active_incidents
                    ],
                },
                instance_id=str(
                    await self.core.config.get("instance.id", "local") or "local"
                ),
            )
        )

    async def _publish_incident(self, incident: ReconciliationIncident) -> None:
        await self.core.event_bus.publish(
            KernelEvent(
                event_type="reconciliation.incident",
                source_id=self.component_id,
                payload=incident.to_dict(),
                instance_id=str(
                    await self.core.config.get("instance.id", "local") or "local"
                ),
            )
        )

    async def get_status(self) -> dict[str, Any]:
        status = await super().get_status()
        status.update(
            {
                "routes": [
                    {"venue": venue, "account": account}
                    for venue, account in sorted(self._registered)
                ],
                "orders": len(self.store.orders()) if self.store else 0,
                "reconciliation_healthy": bool(
                    self.reconciliation
                    and not self.reconciliation.new_risk_blocked
                ),
                "incidents": (
                    len(self.reconciliation.active_incidents)
                    if self.reconciliation
                    else 0
                ),
            }
        )
        return status
