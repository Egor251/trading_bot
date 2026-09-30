"""Durable event storage via Code_base cb_storage."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from microkernel_core import BaseService, KernelEvent

ROOT = Path(__file__).resolve().parents[2]
CB_STORAGE = ROOT.parent / "Code_base" / "cb_storage"
if CB_STORAGE.is_dir() and str(CB_STORAGE) not in sys.path:
    sys.path.insert(0, str(CB_STORAGE))

from cb_storage import StorageKit, StorageQuery  # noqa: E402
from cb_storage.backends.registry import build_backend_registry  # noqa: E402

if TYPE_CHECKING:
    from microkernel_core import MicrokernelCore

_DATA_CLASSES = ("audit", "state", "semantic_stream", "finding")
_BACKEND_IDS = (
    "sqlite",
    "duckdb_local",
    "jsonl_local",
    "parquet_local",
    "chdb_local",
    "clickhouse_main",
    "postgres",
    "cockroach",
)

_SUBSCRIBE = (
    # market / research
    "market.candle",
    "market.history.candle",
    "market.regime",
    "backtest.run_started",
    "backtest.run_finished",
    "phantom.fills",
    "phantom.run_started",
    "phantom.run_finished",
    "phantom.equity",
    # signals → decision → fill
    "strategy.signal",
    "ensemble.decision",
    "ensemble.reject",
    "ensemble.weights_changed",
    "execution.result",
    "risk.reject",
    # portfolio / universe / config context
    "portfolio.snapshot",
    "universe.changed",
    "universe.snapshot",
    "config.lineup_changed",
    "cost.schedule_activated",
    # ops / safety / audit
    "system.halt",
    "system.resume",
    "system.halted",
    "system.resumed",
    "ops.alert",
    "reconciliation.incident",
    "reconciliation.unhealthy",
    "reconciliation.retry_exhausted",
    # AI traces
    "ai.analyst.request",
    "ai.analyst.response",
)


class StorageService(BaseService):
    """
    Persists selected EventBus topics through ``cb_storage.StorageKit``.

    Hot reroute: config ``storage.routing`` or runtime ``kit.reroute(...)``.
    """

    def __init__(self, core: MicrokernelCore, component_id: str) -> None:
        super().__init__(core, component_id)
        self.kit: StorageKit | None = None

    def default_config(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "auto_start": True,
            "subscribe": list(_SUBSCRIBE),
        }

    async def on_initialize(self) -> None:
        storage_cfg = await self.core.config.get("storage", {}) or {}
        # Resolve relative paths against trading_bot root
        backends = dict(storage_cfg.get("backends") or {})
        for _name, bcfg in list(backends.items()):
            if isinstance(bcfg, dict) and bcfg.get("path"):
                p = Path(str(bcfg["path"]))
                if not p.is_absolute():
                    bcfg = {**bcfg, "path": str(ROOT / p)}
                    backends[_name] = bcfg
        cfg = {
            **storage_cfg,
            "backends": backends,
            "instance_id": str(
                await self.core.config.get("instance.id", "local") or "local"
            ),
        }
        if not cfg.get("backends"):
            cfg["backends"] = {
                "sqlite": {
                    "enabled": True,
                    "path": str(ROOT / "data" / "storage" / "records.db"),
                }
            }
        self.kit = StorageKit(cfg)
        setattr(self.core, "storage", self.kit)

    async def start(self) -> None:
        if self.is_running or self.kit is None:
            return
        self.is_running = True
        await self.kit.start()
        topics = list(self.config.get("subscribe") or _SUBSCRIBE)
        for topic in topics:
            await self.core.event_bus.subscribe(topic, self._on_event)

    async def stop(self) -> None:
        self.is_running = False
        topics = list(self.config.get("subscribe") or _SUBSCRIBE)
        for topic in topics:
            try:
                await self.core.event_bus.unsubscribe(topic, self._on_event)
            except Exception:
                pass
        if self.kit is not None:
            await self.kit.stop()

    async def get_status(self) -> dict[str, Any]:
        status = await super().get_status()
        if self.kit is not None:
            status["storage"] = await self.kit.health()
        return status

    def _resolve_backend_path(self, path: str) -> str:
        p = Path(str(path))
        if not p.is_absolute():
            p = ROOT / p
        return str(p)

    async def operator_snapshot(self) -> dict[str, Any]:
        """UI-safe view: routing, backends config, health."""
        if self.kit is None:
            return {"ready": False, "error": "storage_kit_missing"}
        health = await self.kit.health()
        route = self.kit.router.snapshot()
        backends_cfg = dict((self.kit.config.get("backends") or {}))
        backends_out: dict[str, Any] = {}
        registered = set((self.kit.backends.all() or {}).keys())
        for bid in _BACKEND_IDS:
            raw = backends_cfg.get(bid) if isinstance(backends_cfg.get(bid), dict) else {}
            raw = dict(raw or {})
            path = str(raw.get("path") or "")
            url = str(raw.get("url") or "")
            enabled = bool(raw.get("enabled", bid == "sqlite"))
            backends_out[bid] = {
                "enabled": enabled,
                "registered": bid in registered,
                "path": path,
                "url": url,
                "host": str(raw.get("host") or ""),
                "port": raw.get("port"),
                "health": (health.get("backends") or {}).get(bid),
            }
        by_class = dict(route.get("backend_by_class") or {})
        for dc in _DATA_CLASSES:
            by_class.setdefault(dc, str(route.get("default_backend") or "sqlite"))
        return {
            "ready": True,
            "default_backend": str(route.get("default_backend") or "sqlite"),
            "backend_by_class": by_class,
            "data_classes": list(_DATA_CLASSES),
            "backend_ids": list(_BACKEND_IDS),
            "backends": backends_out,
            "sink": health.get("sink") or {},
            "health": health,
        }

    async def apply_reroute(
        self,
        *,
        default_backend: str | None = None,
        backend_by_class: dict[str, str] | None = None,
        persist: bool = False,
        flush: bool = True,
    ) -> dict[str, Any]:
        if self.kit is None:
            return {"ok": False, "error": "storage_kit_missing"}
        snap = self.kit.reroute(
            default_backend=default_backend,
            backend_by_class=backend_by_class,
            merge=True,
        )
        # Keep kit.config in sync for later persist / reload.
        routing = dict(self.kit.config.get("routing") or {})
        routing["backend_by_class"] = dict(snap.get("backend_by_class") or {})
        self.kit.config["routing"] = routing
        self.kit.config["default_backend"] = str(
            snap.get("default_backend") or self.kit.config.get("default_backend") or "sqlite"
        )
        if flush:
            try:
                await self.kit.flush()
            except Exception:
                pass
        if persist:
            await self._persist_storage_config()
        return {"ok": True, "routing": snap, "persisted": bool(persist)}

    async def apply_backend_config(
        self,
        backend_id: str,
        *,
        enabled: bool | None = None,
        path: str | None = None,
        url: str | None = None,
        host: str | None = None,
        port: Any = None,
        persist: bool = False,
        reload: bool = True,
    ) -> dict[str, Any]:
        if self.kit is None:
            return {"ok": False, "error": "storage_kit_missing"}
        bid = str(backend_id or "").strip()
        if bid not in _BACKEND_IDS:
            return {"ok": False, "error": f"unknown_backend:{bid}"}
        backends = dict(self.kit.config.get("backends") or {})
        block = dict(backends.get(bid) or {})
        if enabled is not None:
            block["enabled"] = bool(enabled)
        if path is not None and str(path).strip():
            block["path"] = self._resolve_backend_path(str(path).strip())
        if url is not None:
            block["url"] = str(url).strip()
        if host is not None:
            block["host"] = str(host).strip()
        if port is not None and str(port).strip() != "":
            try:
                block["port"] = int(port)
            except (TypeError, ValueError):
                block["port"] = port
        backends[bid] = block
        self.kit.config["backends"] = backends
        if reload:
            await self.reload_backends()
        if persist:
            await self._persist_storage_config()
        return {
            "ok": True,
            "backend_id": bid,
            "config": {
                "enabled": bool(block.get("enabled", False)),
                "path": str(block.get("path") or ""),
                "url": str(block.get("url") or ""),
            },
            "persisted": bool(persist),
            "snapshot": await self.operator_snapshot(),
        }

    async def reload_backends(self) -> None:
        """Rebuild backend registry without process restart (GG-style reload)."""
        if self.kit is None:
            return
        try:
            await self.kit.flush()
        except Exception:
            pass
        try:
            await self.kit.backends.close_all()
        except Exception:
            pass
        # Re-resolve relative paths
        backends = dict(self.kit.config.get("backends") or {})
        for name, bcfg in list(backends.items()):
            if isinstance(bcfg, dict) and bcfg.get("path"):
                p = Path(str(bcfg["path"]))
                if not p.is_absolute():
                    backends[name] = {**bcfg, "path": str(ROOT / p)}
        self.kit.config["backends"] = backends
        new_reg = build_backend_registry(self.kit.config)
        self.kit.backends = new_reg
        self.kit.sink._backends = new_reg  # noqa: SLF001 — intentional hot swap

    async def _persist_storage_config(self) -> None:
        raw = getattr(self.core.config, "raw", None)
        if not isinstance(raw, dict) or self.kit is None:
            return
        storage = dict(raw.get("storage") or {})
        # Persist paths relative to ROOT when under project tree.
        backends_out: dict[str, Any] = {}
        for bid, block in dict(self.kit.config.get("backends") or {}).items():
            if not isinstance(block, dict):
                continue
            item = dict(block)
            path = item.get("path")
            if path:
                try:
                    rel = Path(str(path)).resolve().relative_to(ROOT.resolve())
                    item["path"] = str(rel).replace("\\", "/")
                except Exception:
                    item["path"] = str(path)
            backends_out[str(bid)] = item
        storage["backends"] = backends_out
        storage["default_backend"] = str(
            self.kit.config.get("default_backend") or "sqlite"
        )
        routing = dict(storage.get("routing") or {})
        routing["backend_by_class"] = dict(
            (self.kit.router.snapshot().get("backend_by_class") or {})
        )
        storage["routing"] = routing
        raw["storage"] = storage
        await self.core.config.save()

    async def query_journal(
        self,
        *,
        event_types: list[str] | None = None,
        limit: int = 20,
        backend_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Trade journal / event log from the default durable backend."""
        if self.kit is None:
            return []
        bid = backend_id or str(
            self.kit.config.get("default_backend") or "sqlite"
        )
        if self.kit.backends.get(bid) is None:
            # fall back to first registered backend
            all_b = self.kit.backends.all()
            if not all_b:
                return []
            bid = next(iter(all_b))
        await self.kit.flush()
        return await self.kit.query(
            bid, StorageQuery(event_types=event_types, limit=limit)
        )

    async def strategy_stats(
        self,
        *,
        strategy_id: str | None = None,
        limit: int = 500,
        backend_id: str | None = None,
    ) -> dict[str, Any]:
        """Expectancy / contribution aggregates from durable journal."""
        from engine.research import JOURNAL_STAT_EVENT_TYPES, aggregate_journal

        rows = await self.query_journal(
            event_types=list(JOURNAL_STAT_EVENT_TYPES),
            limit=int(limit),
            backend_id=backend_id,
        )
        return aggregate_journal(rows, strategy_id=strategy_id)

    async def _on_event(self, event: KernelEvent) -> None:
        if self.kit is None:
            return
        await self.kit.submit(event)
