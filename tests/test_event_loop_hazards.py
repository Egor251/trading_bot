"""Three event-loop hazards, all of which froze the operator UI.

**A30 — unbounded `drain()`.** `_broadcast` awaited `StreamWriter.drain()` with
no timeout. `drain()` waits on the peer's TCP window and does not return while
the peer is not reading, so one sleeping laptop or throttled browser tab stalled
the publish loop for *every* client and, because command handling shares the
loop, for the control path behind it.

**A31 — per-frame disk reads in `build_snapshot`.** The snapshot called
`evaluate_many()` for every frame, which called `evaluate_live_readiness()` per
strategy, which called `load_clearance()` — an `is_file()` + `read_text()` +
`json.loads()` each. With ~21 strategies at 2 Hz that is ~40 synchronous disk
reads a second on the event loop serving `/api/*`.

**Phantom restart.** `restore_state()` existed and had no caller, so the last run
and recent fills vanished on restart. Restoring the *book* would be worse than
either: a phantom position is only meaningful with the bars that opened it, and
those are not replayed. So the summary is restored and an interrupted run is
surfaced, never silently dropped.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

import engine.research_gate as gate  # noqa: E402
from engine.research_gate import load_clearance  # noqa: E402
from services.phantom_service import PhantomService  # noqa: E402
from services.ui_bridge_service import UiBridgeService  # noqa: E402


# ---------------------------------------------------------------------------
# A31 — the clearance snapshot is parsed once, not once per strategy per frame
# ---------------------------------------------------------------------------


def _write_clearance(path: Path, *, marker: str) -> None:
    path.write_text(
        json.dumps({"updated_at": marker, "entries": {"s": {"ticker": marker}}}),
        encoding="utf-8",
    )


def test_clearance_is_cached_across_calls(tmp_path: Path) -> None:
    path = tmp_path / "research_clearance.json"
    _write_clearance(path, marker="one")
    gate.clear_clearance_cache()

    first = load_clearance(path)
    # Rewrite with identical size and force the same mtime: only a cache can
    # survive this without re-reading.
    before = path.stat().st_mtime_ns
    _write_clearance(path, marker="two")
    import os

    os.utime(path, ns=(before, before))

    assert load_clearance(path) == first


def test_cache_invalidates_when_the_file_changes(tmp_path: Path) -> None:
    path = tmp_path / "research_clearance.json"
    _write_clearance(path, marker="one")
    gate.clear_clearance_cache()
    assert load_clearance(path)["updated_at"] == "one"

    _write_clearance(path, marker="a-much-longer-marker")
    assert load_clearance(path)["updated_at"] == "a-much-longer-marker"


def test_cache_survives_a_deleted_file(tmp_path: Path) -> None:
    path = tmp_path / "research_clearance.json"
    _write_clearance(path, marker="one")
    gate.clear_clearance_cache()
    assert load_clearance(path)["entries"]
    path.unlink()
    assert load_clearance(path)["entries"] == {}
    # Recreating the file must be picked up again.
    _write_clearance(path, marker="back")
    assert load_clearance(path)["updated_at"] == "back"


def test_repeated_evaluate_many_does_not_reread(tmp_path: Path, monkeypatch) -> None:
    """Counts actual reads to prove the frame loop stopped hitting the disk."""
    path = tmp_path / "research_clearance.json"
    _write_clearance(path, marker="one")
    gate.clear_clearance_cache()

    reads = {"n": 0}
    real_read = Path.read_text

    def _counting(self, *a, **k):
        if self == path:
            reads["n"] += 1
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", _counting)

    ids = [f"s{i}" for i in range(21)]
    for _ in range(50):  # 50 frames
        gate.evaluate_many(ids, path=path)
    assert reads["n"] == 1, f"{reads['n']} disk reads for 50 frames x 21 strategies"


# ---------------------------------------------------------------------------
# A30 — a stalled client cannot freeze the broadcast
# ---------------------------------------------------------------------------


class _StalledWriter:
    """A writer whose `drain()` never returns, like a peer that stopped reading."""

    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:  # pragma: no cover - replaced below
        raise AssertionError("must be wrapped in a timeout")

    def close(self) -> None:
        self.closed = True


def _bridge(**cfg) -> UiBridgeService:
    core = MagicMock()
    core.service_registry.get = MagicMock(return_value=None)
    svc = UiBridgeService(core, "ui_bridge_service")
    # `_drain_timeout_sec` is resolved in __init__, so the config has to be in
    # place *before* construction — assigning afterwards would silently leave the
    # 5s default in force.
    svc.config = {**svc.default_config(), **cfg}
    svc._drain_timeout_sec = float(svc.config["drain_timeout_sec"])  # noqa: SLF001
    svc.is_running = True
    return svc


def test_a_stalled_client_is_disconnected_not_waited_on() -> None:
    svc = _bridge(drain_timeout_sec=0.05)
    stalled = _StalledWriter()

    async def _never(*a, **k):
        await asyncio.sleep(3600)

    stalled.drain = _never  # type: ignore[method-assign]
    svc._writers.add(stalled)  # noqa: SLF001

    async def _go() -> None:
        await asyncio.wait_for(svc._broadcast({"a": 1}), timeout=2.0)  # noqa: SLF001

    asyncio.run(_go())
    assert stalled.closed is True
    assert stalled not in svc._writers  # noqa: SLF001


def test_a_healthy_client_stays_connected() -> None:
    svc = _bridge(drain_timeout_sec=1.0)
    good = _StalledWriter()

    async def _ok(*a, **k):
        return None

    good.drain = _ok  # type: ignore[method-assign]
    svc._writers.add(good)  # noqa: SLF001

    asyncio.run(svc._broadcast({"a": 1}))  # noqa: SLF001
    assert good.closed is False
    assert good in svc._writers  # noqa: SLF001
    assert good.written, "the frame should have been written"


def test_one_stalled_client_does_not_starve_the_others() -> None:
    svc = _bridge(drain_timeout_sec=0.05)
    stalled = _StalledWriter()
    healthy = _StalledWriter()

    async def _never(*a, **k):
        await asyncio.sleep(3600)

    async def _ok(*a, **k):
        return None

    stalled.drain = _never  # type: ignore[method-assign]
    healthy.drain = _ok  # type: ignore[method-assign]
    svc._writers.update({stalled, healthy})  # noqa: SLF001

    asyncio.run(asyncio.wait_for(svc._broadcast({"a": 1}), timeout=2.0))  # noqa: SLF001
    assert stalled.closed is True
    assert healthy.closed is False
    assert healthy.written, "the healthy client must still receive the frame"


# ---------------------------------------------------------------------------
# phantom restart
# ---------------------------------------------------------------------------


def _phantom(tmp_path: Path) -> PhantomService:
    from engine.state_store import StateStore

    core = MagicMock()
    core.config.get = MagicMock(return_value="test")
    svc = PhantomService(core, "phantom_service")
    svc.config = {**svc.default_config(), "state_path": str(tmp_path / "state.json")}
    svc.runtime = MagicMock()
    # `state_store` is normally created in on_initialize; wire it directly so the
    # test exercises persist/restore rather than the boot sequence.
    svc.state_store = StateStore(tmp_path / "state.json")
    svc.runtime.open_positions = MagicMock(return_value=[])  # type: ignore[method-assign]
    # `on_initialize` wires the store and then calls restore_state; do the same
    # here so the test covers what boot actually does.
    svc.restore_state()
    return svc


def test_restore_rehydrates_the_run_summary(tmp_path: Path) -> None:
    """The previous behaviour had no caller at all, so this silently emptied."""
    svc = _phantom(tmp_path)
    svc._fills = [{"action": "BUY", "price": 1.0}]  # noqa: SLF001
    svc._last_run = {"run_id": "r1", "trades": 3}  # noqa: SLF001
    svc.persist_state()

    fresh = _phantom(tmp_path)
    assert fresh._last_run == {"run_id": "r1", "trades": 3}  # noqa: SLF001
    assert len(fresh._fills) == 1  # noqa: SLF001
    assert fresh._interrupted_run is None  # noqa: SLF001


def test_an_interrupted_run_is_flagged_and_not_silently_dropped(tmp_path: Path) -> None:
    svc = _phantom(tmp_path)
    svc.runtime.snapshot = MagicMock(  # type: ignore[method-assign]
        return_value={
            "positions": {
                "SYNTH:BTC": {"quantity": 2.0, "avg_price": 100.0},
                "SYNTH:ETH": {"quantity": 0.0, "avg_price": 0.0},
            }
        }
    )
    svc.persist_state()

    fresh = _phantom(tmp_path)
    fresh.restore_state()
    assert fresh._interrupted_run is not None  # noqa: SLF001
    assert set(fresh._interrupted_run["open_positions"]) == {"SYNTH:BTC"}  # noqa: SLF001
    assert fresh._interrupted_run["restored"] is False  # noqa: SLF001
    # The book itself must not have been rehydrated.
    assert fresh.runtime.open_positions.return_value == []  # noqa: SLF001


def test_interrupted_run_is_visible_in_status(tmp_path: Path) -> None:
    svc = _phantom(tmp_path)
    svc.runtime.snapshot = MagicMock(  # type: ignore[method-assign]
        return_value={"positions": {"SYNTH:BTC": {"quantity": 1.0}}}
    )
    svc.persist_state()

    fresh = _phantom(tmp_path)
    fresh.restore_state()
    status = asyncio.run(fresh.get_status())
    assert status["interrupted_run"] is not None
    assert status["interrupted_run"]["restored"] is False
    assert status["fill_timing"] == "next_open"


def test_a_clean_snapshot_leaves_no_flag(tmp_path: Path) -> None:
    svc = _phantom(tmp_path)
    svc.runtime.snapshot = MagicMock(return_value={"positions": {}})  # type: ignore[method-assign]
    svc.persist_state()

    fresh = _phantom(tmp_path)
    fresh.restore_state()
    assert fresh._interrupted_run is None  # noqa: SLF001
    assert asyncio.run(fresh.get_status())["interrupted_run"] is None