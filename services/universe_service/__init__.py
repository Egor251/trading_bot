"""Universe service — periodic selection and EventBus publish."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from microkernel_core import BaseService, KernelEvent

from engine.listings import fetch_moex_board_listings
from engine.universe import (
    Instrument,
    UniverseManager,
    UniverseSnapshot,
    instrument_from_dict,
)

if TYPE_CHECKING:
    from microkernel_core import MicrokernelCore

ROOT = Path(__file__).resolve().parents[2]

_METRIC_FIELDS = (
    "price",
    "volume",
    "dollar_volume",
    "atr_pct",
    "realized_vol",
)


class UniverseService(BaseService):
    """
    Owns catalog → candidates → active selection.

    Catalog composition:
      seed YAML/config ± MOEX ISS listings overlay ± history metrics overlay.
    """

    def __init__(self, core: MicrokernelCore, component_id: str) -> None:
        super().__init__(core, component_id)
        self._task: asyncio.Task[None] | None = None
        self._manager: UniverseManager | None = None
        self._catalog: list[Instrument] = []
        self._listings: list[Instrument] = []
        self._last_active_keys: list[str] = []
        self._passes = 0
        self._metric_overlay: dict[str, dict[str, Any]] = {}
        self._last_metrics_at = 0.0
        self._last_metrics_stats: dict[str, Any] = {}
        self._last_listings_at = 0.0
        self._last_listings_stats: dict[str, Any] = {}

    def default_config(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "auto_start": True,
            "reselect_sec": 60,
            "publish_snapshot_always": True,
            "catalog_path": "config/universe_catalog.yaml",
            "catalog": [],
            # history → metrics (network)
            "refresh_metrics": False,
            "refresh_metrics_sec": 3600,
            "refresh_metrics_provider": "yahoo",
            "refresh_metrics_asset_classes": ["equity", "etf"],
            "refresh_metrics_on_boot": False,
            # MOEX ISS listings (network; no QUIK)
            "refresh_listings": False,
            "refresh_listings_sec": 86400,
            "refresh_listings_boards": ["TQBR"],
            "refresh_listings_on_boot": False,
            "listings_mode": "merge",  # merge | replace
            "listings_include_marketdata": True,
        }

    async def on_initialize(self) -> None:
        uni_cfg = await self.core.config.get("universe", {}) or {}
        self._manager = UniverseManager.from_mapping(uni_cfg)
        self._catalog = self._compose_catalog()

    def _seed_catalog(self) -> list[Instrument]:
        rows: list[dict[str, Any]] = list(self.config.get("catalog") or [])
        path_raw = self.config.get("catalog_path")
        if path_raw:
            path = Path(path_raw)
            if not path.is_absolute():
                path = ROOT / path
            if path.exists():
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if isinstance(data, dict):
                    rows = list(data.get("instruments") or data.get("catalog") or rows)
                elif isinstance(data, list):
                    rows = data
        return [instrument_from_dict(r) for r in rows if r.get("symbol")]

    def _compose_catalog(self) -> list[Instrument]:
        seed = self._seed_catalog()
        mode = str(self.config.get("listings_mode") or "merge").lower()
        if self._listings:
            if mode == "replace":
                base = list(self._listings)
            else:
                by_key = {i.key: i for i in seed}
                for inst in self._listings:
                    by_key[inst.key] = inst
                base = list(by_key.values())
        else:
            base = seed
        self._apply_metric_overlay(base)
        return base

    def _apply_metric_overlay(self, catalog: list[Instrument]) -> None:
        for inst in catalog:
            overlay = self._metric_overlay.get(inst.key)
            if not overlay or not overlay.get("ok", True):
                continue
            for field in _METRIC_FIELDS:
                if field in overlay and overlay[field] is not None:
                    setattr(inst, field, float(overlay[field]))

    def _load_catalog(self) -> list[Instrument]:
        return self._compose_catalog()

    def _load_catalog_raw(self) -> list[Instrument]:
        """Catalog for metrics refresh: listings+seed, no history overlay."""
        seed = self._seed_catalog()
        mode = str(self.config.get("listings_mode") or "merge").lower()
        if not self._listings:
            return seed
        if mode == "replace":
            return list(self._listings)
        by_key = {i.key: i for i in seed}
        for inst in self._listings:
            by_key[inst.key] = inst
        return list(by_key.values())

    async def start(self) -> None:
        if self._task is not None:
            return
        self.is_running = True
        if self.config.get("refresh_listings") and self.config.get(
            "refresh_listings_on_boot"
        ):
            await self.refresh_listings(force=True)
        if self.config.get("refresh_metrics") and self.config.get(
            "refresh_metrics_on_boot"
        ):
            await self.refresh_catalog_metrics(force=True)
        self._catalog = self._compose_catalog()
        await self._run_pass(reason="boot")
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self.is_running = False
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def get_status(self) -> dict[str, Any]:
        status = await super().get_status()
        last = self._manager.last if self._manager else None
        status.update(
            {
                "mode": self._manager.config.mode if self._manager else None,
                "catalog_size": len(self._catalog),
                "listings_size": len(self._listings),
                "active_keys": list(self._last_active_keys),
                "passes": self._passes,
                "counts": last.to_payload()["counts"] if last else {},
                "refresh_metrics": bool(self.config.get("refresh_metrics")),
                "refresh_listings": bool(self.config.get("refresh_listings")),
                "metric_overlay_size": len(self._metric_overlay),
                "last_metrics": dict(self._last_metrics_stats),
                "last_listings": dict(self._last_listings_stats),
            }
        )
        return status

    @property
    def active_symbols(self) -> list[str]:
        last = self._manager.last if self._manager else None
        return last.active_symbols() if last else []

    @property
    def last_snapshot(self) -> UniverseSnapshot | None:
        return self._manager.last if self._manager else None

    async def reselect_now(self, reason: str = "manual") -> UniverseSnapshot | None:
        self._catalog = self._compose_catalog()
        return await self._run_pass(reason=reason)

    async def refresh_listings(self, *, force: bool = False) -> dict[str, Any]:
        """Fetch MOEX ISS board securities into in-memory listings catalog."""
        interval = float(self.config.get("refresh_listings_sec") or 86400)
        if (
            not force
            and self._last_listings_at
            and (time.monotonic() - self._last_listings_at) < interval
        ):
            self._catalog = self._compose_catalog()
            return {"skipped": True, "reason": "ttl", **self._last_listings_stats}

        boards = list(self.config.get("refresh_listings_boards") or ["TQBR"])
        include_md = bool(self.config.get("listings_include_marketdata", True))
        try:
            rows = await fetch_moex_board_listings(
                boards=boards,
                include_marketdata=include_md,
            )
        except Exception as exc:  # noqa: BLE001
            stats = {"ok": False, "error": str(exc), "boards": boards}
            self._last_listings_stats = stats
            return stats

        self._listings = rows
        self._catalog = self._compose_catalog()
        self._last_listings_at = time.monotonic()
        stats = {
            "ok": True,
            "count": len(rows),
            "boards": boards,
            "mode": self.config.get("listings_mode"),
            "catalog_size": len(self._catalog),
        }
        self._last_listings_stats = stats
        return stats

    async def refresh_catalog_metrics(self, *, force: bool = False) -> dict[str, Any]:
        """Pull OHLCV metrics into overlay; returns stats."""
        interval = float(self.config.get("refresh_metrics_sec") or 3600)
        if (
            not force
            and self._last_metrics_at
            and (time.monotonic() - self._last_metrics_at) < interval
        ):
            self._catalog = self._compose_catalog()
            return {"skipped": True, "reason": "ttl", **self._last_metrics_stats}

        hist = self.core.service_registry.get("historical_data_service")
        if hist is None or not hasattr(hist, "refresh_instruments"):
            stats = {"ok": False, "error": "historical_data_service missing"}
            self._last_metrics_stats = stats
            return stats

        provider = self.config.get("refresh_metrics_provider")
        asset_classes = self.config.get("refresh_metrics_asset_classes") or [
            "equity",
            "etf",
        ]
        rows = self._load_catalog_raw()
        overlays = await hist.refresh_instruments(
            rows,
            provider=provider,
            asset_classes=asset_classes,
        )
        updated = 0
        for key, payload in overlays.items():
            if payload.get("ok"):
                self._metric_overlay[key] = payload
                updated += 1
            elif key not in self._metric_overlay:
                self._metric_overlay[key] = payload

        self._catalog = self._compose_catalog()
        self._last_metrics_at = time.monotonic()
        stats = {
            "ok": True,
            "updated": updated,
            "attempted": len(overlays),
            "provider": provider,
            "overlay_size": len(self._metric_overlay),
        }
        self._last_metrics_stats = stats
        return stats

    async def _loop(self) -> None:
        interval = float(self.config.get("reselect_sec", 60))
        while self.is_running:
            try:
                await asyncio.sleep(interval)
                if not self.is_running:
                    break
                if self.config.get("refresh_listings"):
                    await self.refresh_listings(force=False)
                if self.config.get("refresh_metrics"):
                    await self.refresh_catalog_metrics(force=False)
                else:
                    self._catalog = self._compose_catalog()
                await self._run_pass(reason="timer")
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(interval)

    async def _run_pass(self, reason: str) -> UniverseSnapshot | None:
        if self._manager is None:
            return None

        original_include = self._manager.config.include
        runtime = getattr(self.core, "trading", None)
        if runtime is not None:
            pinned = sorted(runtime.open_position_keys())
            if pinned:
                self._manager.config.include = tuple(
                    dict.fromkeys([*original_include, *pinned])
                )

        try:
            snap = self._manager.select(self._catalog)
        finally:
            self._manager.config.include = original_include

        snap.reason = f"{snap.reason}:{reason}"
        self._passes += 1
        instance_id = str(await self.core.config.get("instance.id", "local") or "local")
        payload = snap.to_payload()
        payload["pass_reason"] = reason
        if runtime is not None:
            payload["pinned_keys"] = sorted(runtime.open_position_keys())

        if self.config.get("publish_snapshot_always", True):
            await self.core.event_bus.publish(
                KernelEvent(
                    event_type="universe.snapshot",
                    source_id=self.component_id,
                    payload=payload,
                    instance_id=instance_id,
                )
            )

        new_keys = snap.active_keys()
        if new_keys != self._last_active_keys:
            changed = {
                "added": [k for k in new_keys if k not in self._last_active_keys],
                "removed": [k for k in self._last_active_keys if k not in new_keys],
                "active_keys": new_keys,
                "active": payload["active"],
                "mode": snap.mode,
                "pass_reason": reason,
            }
            await self.core.event_bus.publish(
                KernelEvent(
                    event_type="universe.changed",
                    source_id=self.component_id,
                    payload=changed,
                    instance_id=instance_id,
                )
            )
            self._last_active_keys = list(new_keys)
        return snap
