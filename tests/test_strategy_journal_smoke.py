"""Smoke: StrategyPlugin + storage journal query helper shape."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CB = ROOT.parent / "Code_base" / "cb_storage"
if CB.is_dir():
    sys.path.insert(0, str(CB))

from engine.strategy_plugin import StrategyPlugin  # noqa: E402
from host.events import normalize_strategy_signal  # noqa: E402


def test_strategy_plugin_importable() -> None:
    assert issubclass(StrategyPlugin, object)
    sig = normalize_strategy_signal(
        {"action": "sell", "ticker": "GAZP", "strategy_id": "x", "quantity": 1}
    )
    assert sig["action"] == "SELL"


async def _journal_smoke() -> None:
    from cb_storage import StorageKit, StorageQuery

    data = Path(tempfile.mkdtemp(prefix="tb_journal_"))
    kit = StorageKit(
        {
            "default_backend": "sqlite",
            "backends": {
                "sqlite": {"enabled": True, "path": str(data / "records.db")},
            },
            "routing": {"backend_by_class": {"semantic_stream": "sqlite", "audit": "sqlite"}},
            "hot_buffer": {"enabled": True},
            "sink": {"batch_size": 1, "flush_interval_seconds": 0.05},
        }
    )
    await kit.start()
    await kit.submit(
        {
            "event_id": "j1",
            "event_type": "execution.result",
            "source_id": "test",
            "payload": {"success": True},
        }
    )
    await kit.flush()
    rows = await kit.query("sqlite", StorageQuery(event_types=["execution.result"], limit=5))
    assert any(r.get("event_id") == "j1" for r in rows), rows
    await kit.stop()


def test_journal_smoke() -> None:
    asyncio.run(_journal_smoke())


if __name__ == "__main__":
    test_strategy_plugin_importable()
    test_journal_smoke()
    print("strategy/journal smoke OK")
