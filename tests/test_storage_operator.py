"""Storage operator snapshot + hot reroute (no JSON migration)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from cb_storage import StorageKit  # noqa: E402
from services.storage_service import StorageService  # noqa: E402


class _FakeConfig:
    def __init__(self, raw: dict) -> None:
        self.raw = raw

    async def get(self, path: str, default=None):
        cur = self.raw
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    async def save(self) -> None:
        return None


class _FakeCore:
    def __init__(self, tmp: Path) -> None:
        db = tmp / "records.db"
        self.config = _FakeConfig(
            {
                "instance": {"id": "test"},
                "storage": {
                    "enabled": True,
                    "default_backend": "sqlite",
                    "backends": {
                        "sqlite": {"enabled": True, "path": str(db)},
                        "duckdb_local": {
                            "enabled": False,
                            "path": str(tmp / "events.duckdb"),
                        },
                    },
                    "routing": {
                        "backend_by_class": {
                            "audit": "sqlite",
                            "state": "sqlite",
                        }
                    },
                },
            }
        )
        self.event_bus = None
        self.service_registry = {}


def test_operator_reroute_hot(tmp_path: Path) -> None:
    async def _run() -> None:
        core = _FakeCore(tmp_path)
        svc = StorageService(core, "storage_service")  # type: ignore[arg-type]
        await svc.on_initialize()
        assert isinstance(svc.kit, StorageKit)
        snap = await svc.operator_snapshot()
        assert snap["ready"] is True
        assert snap["default_backend"] == "sqlite"
        result = await svc.apply_reroute(
            default_backend="sqlite",
            backend_by_class={"semantic_stream": "sqlite", "audit": "sqlite"},
            persist=False,
        )
        assert result["ok"] is True
        route = svc.kit.router.snapshot()
        assert route["backend_by_class"]["semantic_stream"] == "sqlite"
        await svc.kit.stop()

    asyncio.run(_run())
