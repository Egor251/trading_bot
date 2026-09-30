"""Обёртка MicrokernelCore для trading_bot."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from microkernel_core import CoreSettings, MicrokernelCore
from microkernel_core.lifecycle import ComponentLifecycle

ROOT = Path(__file__).resolve().parent.parent

# Start order for stable paper path (others follow via kernel.start)
_PRIORITY_SERVICES = (
    "trading_runtime_service",
    "universe_service",
    "ccxt_market_service",  # seed OHLCV into CandleStore before UI bridge
    "storage_service",
    "ops_monitor_service",
    "ensemble_service",
    "phantom_service",
    "ui_bridge_service",
)


class TradingBotCore:
    """Хост ядра: microkernel + пути trading_bot."""

    # Market connectors live under plugins/market (not core infra services/).
    MARKET_PLUGINS_DIR = ROOT / "plugins" / "market"

    def __init__(self, config_path: str | Path | None = None) -> None:
        cfg = config_path or ROOT / "config" / "trading_bot.yaml"
        self.root = ROOT
        self.kernel = MicrokernelCore(
            CoreSettings(
                config_path=str(cfg),
                services_dir=ROOT / "services",
            )
        )

    async def boot(self) -> None:
        # Discover market connector packages from plugins/market via ServiceRegistry.
        registry = self.service_registry
        original = registry.discover_and_initialize

        async def _discover(extra_paths: list[Path] | None = None) -> None:
            paths = list(extra_paths or [])
            if self.MARKET_PLUGINS_DIR.is_dir():
                paths.append(self.MARKET_PLUGINS_DIR)
            await original(extra_paths=paths or None)

        registry.discover_and_initialize = _discover  # type: ignore[method-assign]
        try:
            await self.kernel.boot()
        finally:
            registry.discover_and_initialize = original  # type: ignore[method-assign]

    async def start(self) -> None:
        """Start runtime/universe first, then remaining services + plugins."""
        for sid in _PRIORITY_SERVICES:
            await self._start_service(sid)
        await self.kernel.start()

    async def _start_service(self, component_id: str) -> None:
        svc = self.service_registry.get(component_id)
        if svc is None:
            return
        if not svc.config.get("enabled", True):
            return
        if svc.is_running:
            return
        await svc.start()
        svc.is_running = True
        svc._lifecycle = ComponentLifecycle.STARTED  # noqa: SLF001

    async def shutdown(self) -> None:
        await self.kernel.shutdown()

    def status(self) -> dict[str, Any]:
        st = self.kernel.status()
        runtime = getattr(self.kernel, "trading", None)
        if runtime is not None:
            st["trading"] = runtime.snapshot()
            st["halted"] = runtime.halted
        pm = self.plugin_manager
        records = []
        for pid, rec in getattr(pm, "_records", {}).items():
            manifest = rec.manifest
            inst = pm.get(pid)
            records.append(
                {
                    "id": pid,
                    "name": getattr(manifest, "name", pid),
                    "kind": getattr(getattr(manifest, "kind", None), "value", str(getattr(manifest, "kind", ""))),
                    "run_mode": getattr(
                        getattr(manifest, "run_mode", None),
                        "value",
                        str(getattr(manifest, "run_mode", "")),
                    ),
                    "path": getattr(rec, "path", ""),
                    "lifecycle": getattr(rec, "lifecycle", ""),
                    "running": bool(inst and getattr(inst, "is_running", False)),
                    "error": getattr(rec, "error", None),
                }
            )
        stats = pm.stats() if hasattr(pm, "stats") else {}
        st["plugin_manager"] = {
            **stats,
            "ids": sorted(pm.plugins.keys()),
            "records": records,
        }
        return st

    def list_plugins(self, *, kind: str | None = None) -> list[dict[str, Any]]:
        """Introspection helper for REPL / UI (Code_base plugin_manager records)."""
        rows = (self.status().get("plugin_manager") or {}).get("records") or []
        if kind:
            want = kind.lower()
            rows = [r for r in rows if str(r.get("kind", "")).lower() == want]
        return rows

    async def halt(self, reason: str = "kill_switch") -> None:
        from microkernel_core import KernelEvent

        await self.event_bus.publish(
            KernelEvent(
                event_type="system.halt",
                source_id="trading_bot_core",
                payload={"reason": reason},
                instance_id=str(
                    await self.config.get("instance.id", "local") or "local"
                ),
            )
        )

    async def resume(self) -> None:
        from microkernel_core import KernelEvent

        await self.event_bus.publish(
            KernelEvent(
                event_type="system.resume",
                source_id="trading_bot_core",
                payload={},
                instance_id=str(
                    await self.config.get("instance.id", "local") or "local"
                ),
            )
        )

    @property
    def event_bus(self):
        return self.kernel.event_bus

    @property
    def plugin_manager(self):
        return self.kernel.plugin_manager

    @property
    def service_registry(self):
        return self.kernel.service_registry

    @property
    def rule_engine(self):
        return self.kernel.rule_engine

    @property
    def config(self):
        return self.kernel.config

    @property
    def trading(self):
        return getattr(self.kernel, "trading", None)
