"""Retention policy for the event journal.

The journal grew to 5.17 M rows / 3.7 GB with no retention and no VACUUM
(`freelist_count = 0`). ~88% of it was noise: 1.23 M `strategy.signal`, 1.20 M
`market.candle`, 1.08 M `market.regime` and 897 k `ensemble.weights_changed` — the
last of which held two distinct payloads across its most recent 400 rows.

The critical property is what is *not* deleted: `docs/runbooks/reconciliation-break.md`
treats the journal as the authority during an incident, and
`emergency-halt-flatten.md` requires the full command/order/fill timeline to be
retained. So the trading record has a 365-day floor and the halt/alert/reconcile
events are never pruned at any age.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.retention import (
    DEFAULT_RETENTION_DAYS,
    NEVER_PRUNE,
    _effective_days,
    add_index_if_missing,
    apply,
    plan,
    status,
    vacuum,
)

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _journal(tmp_path: Path) -> Path:
    path = tmp_path / "records.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE storage_records (
            record_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            data_class TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            ingested_at TEXT NOT NULL,
            tenant_id TEXT,
            instance_id TEXT,
            source_id TEXT NOT NULL,
            payload_json TEXT,
            payload_hash TEXT,
            effective_policy_json TEXT,
            severity TEXT
        )"""
    )
    conn.commit()
    return path


def _insert(path: Path, event_type: str, days_old: int, *, data_class: str = "semantic_stream") -> None:
    ts = (NOW - timedelta(days=days_old)).isoformat()
    conn = sqlite3.connect(str(path))
    conn.execute(
        "INSERT INTO storage_records "
        "(record_id, event_id, event_type, data_class, timestamp, ingested_at, source_id) "
        "VALUES (?,?,?,?,?,?,?)",
        (f"{event_type}-{days_old}-{id(event_type)}", f"e-{event_type}", event_type, data_class, ts, ts, "s"),
    )
    conn.commit()
    conn.close()


def test_status_reports_size_and_distribution(tmp_path: Path) -> None:
    path = _journal(tmp_path)
    _insert(path, "market.candle", 100)
    _insert(path, "ops.alert", 1)
    info = status(path)
    assert info["rows"] == 2
    assert info["size_mb"] >= 0
    assert info["top_events"]["market.candle"] == 1


def test_noisy_event_types_become_prunable(tmp_path: Path) -> None:
    path = _journal(tmp_path)
    _insert(path, "market.candle", 400)
    _insert(path, "strategy.signal", 400)
    _insert(path, "market.regime", 400)
    _insert(path, "ensemble.weights_changed", 400)
    rows = dict((et, n) for et, _c, n in plan(path, DEFAULT_RETENTION_DAYS, NOW))
    assert rows["market.candle"] == 1
    assert rows["strategy.signal"] == 1
    assert rows["market.regime"] == 1
    assert rows["ensemble.weights_changed"] == 1


def test_trading_record_has_a_year_floor(tmp_path: Path) -> None:
    """Fills and rejects are the audit trail; 30 days would be a data loss."""
    path = _journal(tmp_path)
    _insert(path, "execution.result", 300)
    _insert(path, "risk.reject", 300)
    rows = dict((et, n) for et, _c, n in plan(path, DEFAULT_RETENTION_DAYS, NOW))
    assert "execution.result" not in rows
    assert "risk.reject" not in rows
    assert _effective_days("execution.result", DEFAULT_RETENTION_DAYS) == 365
    assert _effective_days("risk.reject", DEFAULT_RETENTION_DAYS) == 365

    # Past a year they do become eligible.
    _insert(path, "execution.result", 400)
    rows = dict((et, n) for et, _c, n in plan(path, DEFAULT_RETENTION_DAYS, NOW))
    assert rows["execution.result"] == 1


def test_never_prune_events_survive_any_age(tmp_path: Path) -> None:
    path = _journal(tmp_path)
    for event_type in sorted(NEVER_PRUNE):
        _insert(path, event_type, 5_000)
    assert plan(path, DEFAULT_RETENTION_DAYS, NOW) == []

    removed = apply(path, DEFAULT_RETENTION_DAYS, NOW)
    assert removed == {}
    assert status(path)["rows"] == len(NEVER_PRUNE)


def test_unmapped_event_types_use_the_fallback(tmp_path: Path) -> None:
    assert _effective_days("something.new", DEFAULT_RETENTION_DAYS) == 30
    path = _journal(tmp_path)
    _insert(path, "something.new", 90)
    rows = dict((et, n) for et, _c, n in plan(path, DEFAULT_RETENTION_DAYS, NOW))
    assert rows["something.new"] == 1
    _insert(path, "something.new", 10)
    rows = dict((et, n) for et, _c, n in plan(path, DEFAULT_RETENTION_DAYS, NOW))
    assert rows["something.new"] == 1  # the recent one is kept


def test_apply_deletes_only_the_eligible_rows(tmp_path: Path) -> None:
    path = _journal(tmp_path)
    _insert(path, "market.candle", 400)
    _insert(path, "market.candle", 2)   # recent -> keep
    _insert(path, "ops.alert", 400)        # never pruned
    _insert(path, "execution.result", 300)  # inside the 365-day floor

    removed = apply(path, DEFAULT_RETENTION_DAYS, NOW)
    assert removed == {"market.candle": 1}
    remaining = status(path)["top_events"]
    assert remaining["market.candle"] == 1
    assert remaining["ops.alert"] == 1
    assert remaining["execution.result"] == 1


def test_apply_handles_more_rows_than_one_batch(tmp_path: Path) -> None:
    path = _journal(tmp_path)
    conn = sqlite3.connect(str(path))
    ts = (NOW - timedelta(days=400)).isoformat()
    conn.executemany(
        "INSERT INTO storage_records "
        "(record_id, event_id, event_type, data_class, timestamp, ingested_at, source_id) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            (f"bulk-{i}", f"e-{i}", "market.candle", "semantic_stream", ts, ts, "s")
            for i in range(25)
        ],
    )
    conn.commit()
    conn.close()

    removed = apply(path, DEFAULT_RETENTION_DAYS, NOW, batch=5)
    assert removed == {"market.candle": 25}
    assert status(path)["rows"] == 0


def test_pruning_index_is_created_and_idempotent(tmp_path: Path) -> None:
    path = _journal(tmp_path)
    created = add_index_if_missing(path)
    assert "ix_storage_type_timestamp" in created
    assert "ix_storage_class_timestamp" in created
    # Second call must not fail.
    add_index_if_missing(path)
    info = status(path)
    assert "ix_storage_type_timestamp" in info["indexes"]


def test_vacuum_runs(tmp_path: Path) -> None:
    path = _journal(tmp_path)
    _insert(path, "market.candle", 400)
    apply(path, DEFAULT_RETENTION_DAYS, NOW)
    vacuum(path)
    assert status(path)["rows"] == 0


def test_data_dir_override_is_honoured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tools.retention import _db_path

    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp_path))
    assert _db_path(None) == tmp_path / "storage" / "records.db"
    assert _db_path("/explicit/x.db").as_posix() == "/explicit/x.db"


def test_missing_table_is_reported_not_raised(tmp_path: Path) -> None:
    empty = tmp_path / "empty.db"
    sqlite3.connect(str(empty)).close()
    assert "error" in status(empty)
    assert plan(empty, DEFAULT_RETENTION_DAYS, NOW) == []
