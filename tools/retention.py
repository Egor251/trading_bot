"""Retention for the `storage_records` journal.

The SQLite journal had no retention of any kind: 5,168,858 rows in 3.7 GB,
`freelist_count = 0` (so VACUUM had never reclaimed anything), and a
distribution in which most rows carried no information:

    market.candle            1,576,848
    strategy.signal          1,228,870
    market.regime            1,080,883   (identical payload repeated per candle)
    ensemble.weights_changed   897,653   (2 distinct payloads in the last 400)
    ensemble.decision          154,833
    execution.result           134,396   (90.6% failures)
    risk.reject                 61,550

Growth is roughly 1.5 M rows/week, so the failure mode is disk exhaustion on the
machine that also runs the broker connection. The runbook set assumes the journal
is available for post-mortem; deleting rows the operator may need is worse than
the disk, so this tool:

* **never deletes** `audit`, `state`, `finding` or `risk.*` / `execution.*` rows
  below the age floor;
* deletes by *age* per data class, with conservative defaults;
* dry-runs by default and requires an explicit ``--apply``;
* runs `VACUUM`` separately (and only when asked) because it needs the free
    space to rewrite the file.

    python -m tools.retention --status
    python -m tools.retention --apply
    python -m tools.retention --apply --vacuum
"""

from __future__ import annotations

import argparse
import json as _json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Retention keyed by ``event_type`` — *not* by ``data_class``.
#:
#: The first draft of this tool was keyed by data_class and pruned nothing: the
#: real classification is `semantic_stream` (2.75 M), `finding` (1.23 M),
#: `audit` (1.18 M) and `state` (7 k), i.e. every single row lands in a class
#: that looks like "audit trail". Those names come from `cb_storage` and say
#: nothing about how long the row is worth keeping.
#:
#: Days to keep, by event type. Everything not listed falls back to
#: ``DEFAULT_RETENTION_DAYS_FALLBACK``.
DEFAULT_RETENTION_DAYS: dict[str, int] = {
    # Re-fetchable and the largest slices by a wide margin. The last 400
    # `ensemble.weights_changed` rows contained 2 distinct payloads.
    "market.candle": 30,
    "market.history.candle": 30,
    "market.regime": 7,
    "strategy.signal": 14,
    "ensemble.weights_changed": 7,
    "ensemble.decision": 30,
    "ensemble.reject": 14,
    "universe.snapshot": 7,
    "portfolio.snapshot": 7,
    "phantom.run_started": 90,
    "phantom.run_finished": 90,
}

#: Applied to any event type not named above.
DEFAULT_RETENTION_DAYS_FALLBACK = 30

#: The audit trail. Never pruned regardless of age, because
#: `docs/runbooks/reconciliation-break.md` treats the journal as the source of
#: truth for an incident and `emergency-halt-flatten.md` requires the full
#: command/order/fill timeline to be retained.
NEVER_PRUNE = frozenset(
    {
        "system.halt",
        "system.resumed",
        "system.halted",
        "ops.alert",
        "reconciliation.unhealthy",
        "cost.schedule_activated",
        "trading.live_enabled",
        "trading.live_disabled",
        "audit.operator_command",
        "audit.evidence",
    }
)

#: A floor for the trading record, applied on top of the per-type mapping.
#: Fills and rejects stay for a year even if something later maps them lower.
MIN_RETENTION_DAYS: dict[str, int] = {
    "execution.result": 365,
    "execution.fill": 365,
    "risk.reject": 365,
    "risk.halt": 365,
}


def _data_root() -> Path:
    """Resolve the data root without importing the engine package.

    ``engine.paths`` is the canonical resolver, but importing it pulls in
    ``engine/__init__`` → ``microkernel_core``, which an offline maintenance tool
    should not require. `TRADING_DATA_DIR` is honoured the same way.
    """
    override = str(os.environ.get("TRADING_DATA_DIR") or "").strip()
    if override:
        return Path(override)
    return ROOT / "data"


def _db_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    return _data_root() / "storage" / "records.db"


def _connect(path: Path, *, readonly: bool = True) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30.0)
    else:
        conn = sqlite3.connect(str(path), timeout=30.0)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def status(path: Path) -> dict[str, object]:
    conn = _connect(path)
    try:
        if not _table_exists(conn, "storage_records"):
            return {"error": "storage_records not found", "path": str(path)}
        total = conn.execute("SELECT COUNT(*) FROM storage_records").fetchone()[0]
        oldest, newest = conn.execute(
            "SELECT MIN(timestamp), MAX(timestamp) FROM storage_records"
        ).fetchone()
        by_class = conn.execute(
            "SELECT data_class, COUNT(*) c FROM storage_records "
            "GROUP BY 1 ORDER BY c DESC"
        ).fetchall()
        by_event = conn.execute(
            "SELECT event_type, COUNT(*) c FROM storage_records "
            "GROUP BY 1 ORDER BY c DESC LIMIT 40"
        ).fetchall()
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
        indices = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='storage_records'"
        ).fetchall()
        return {
            "path": str(path),
            "rows": total,
            "oldest": oldest,
            "newest": newest,
            "size_mb": round(page_count * page_size / 1_048_576, 1),
            "free_mb": round(freelist * page_size / 1_048_576, 1),
            "by_class": dict(by_class),
            "top_events": dict(by_event),
            "indexes": [i[0] for i in indices],
        }
    finally:
        conn.close()


def _cutoff(days: int, now: datetime) -> str:
    return (now - timedelta(days=int(days))).isoformat()


def _effective_days(event_type: str, retention: dict[str, int]) -> int:
    days = int(retention.get(event_type, DEFAULT_RETENTION_DAYS_FALLBACK))
    floor = MIN_RETENTION_DAYS.get(event_type)
    if floor is not None:
        days = max(days, floor)
    return days


def plan(
    path: Path, retention: dict[str, int], now: datetime
) -> list[tuple[str, str, int]]:
    """Return ``(event_type, cutoff_iso, deletable_rows)`` per event type."""
    conn = _connect(path)
    try:
        if not _table_exists(conn, "storage_records"):
            return []
        types = [
            row[0] for row in conn.execute("SELECT DISTINCT event_type FROM storage_records")
        ]
        out: list[tuple[str, str, int]] = []
        for event_type in types:
            if event_type in NEVER_PRUNE:
                continue
            cutoff = _cutoff(_effective_days(event_type, retention), now)
            count = conn.execute(
                "SELECT COUNT(*) FROM storage_records "
                "WHERE event_type=? AND timestamp < ?",
                (event_type, cutoff),
            ).fetchone()[0]
            if count:
                out.append((event_type, cutoff, count))
        out.sort(key=lambda row: row[2], reverse=True)
        return out
    finally:
        conn.close()


def apply(
    path: Path, retention: dict[str, int], now: datetime, *, batch: int = 5_000
) -> dict[str, int]:
    """Delete in batches so the journal file is never locked for long.

    ``data_class`` alone is not a safe index for this — the existing indices are
    on ``event_type``/``data_class``/``timestamp`` individually — so the cutoff
    filter runs per class and each delete is bounded.
    """
    conn = _connect(path, readonly=False)
    removed: dict[str, int] = {}
    try:
        for event_type, cutoff, _expected in plan(path, retention, now):
            total = 0
            while True:
                rows = conn.execute(
                    "SELECT record_id FROM storage_records "
                    "WHERE event_type=? AND timestamp < ? LIMIT ?",
                    (event_type, cutoff, batch),
                ).fetchall()
                if not rows:
                    break
                conn.executemany(
                    "DELETE FROM storage_records WHERE record_id=?",
                    [(r[0],) for r in rows],
                )
                conn.commit()
                total += len(rows)
            removed[event_type] = total
        return removed
    finally:
        conn.close()


def vacuum(path: Path) -> None:
    """Reclaim free pages. Needs the free space to rewrite the file."""
    conn = _connect(path, readonly=False)
    try:
        conn.isolation_level = None
        conn.execute("VACUUM")
    finally:
        conn.close()


def add_index_if_missing(path: Path) -> list[str]:
    """Add the composite index the retention query actually needs.

    Three separate single-column indices cannot serve a
    ``data_class = ? AND timestamp < ?`` range scan efficiently; this is why the
    prune was never attempted.
    """
    conn = _connect(path, readonly=False)
    created: list[str] = []
    try:
        for name, ddl in (
            (
                "ix_storage_class_timestamp",
                "CREATE INDEX IF NOT EXISTS ix_storage_class_timestamp "
                "ON storage_records(data_class, timestamp)",
            ),
            (
                "ix_storage_type_timestamp",
                "CREATE INDEX IF NOT EXISTS ix_storage_type_timestamp "
                "ON storage_records(event_type, timestamp)",
            ),
        ):
            conn.execute(ddl)
            created.append(name)
        conn.commit()
    finally:
        conn.close()
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None, help="path to records.db")
    parser.add_argument("--status", action="store_true", help="report only")
    parser.add_argument("--apply", action="store_true", help="actually delete")
    parser.add_argument("--vacuum", action="store_true", help="VACUUM afterwards")
    parser.add_argument("--index", action="store_true", help="ensure pruning indices")
    parser.add_argument(
        "--retention",
        default=None,
        help='JSON override, e.g. \'{"market": 10}\'',
    )
    args = parser.parse_args(argv)

    path = _db_path(args.db)
    if not path.is_file():
        print(f"no journal at {path}")
        return 1

    info = status(path)
    print(f"journal: {info['path']}")
    print(f"  rows={info['rows']}  size={info['size_mb']} MB  free={info['free_mb']} MB")
    print(f"  range: {info['oldest']} .. {info['newest']}")
    print("  by data_class:")
    for name, count in (info.get("by_class") or {}).items():
        print(f"    {name:24} {count:>10,}")
    print("  event types:")
    for name, count in (info.get("top_events") or {}).items():
        flag = "  (never pruned)" if name in NEVER_PRUNE else ""
        print(f"    {name:30} {count:>10,}{flag}")
    print(f"  indexes: {', '.join(info.get('indexes') or []) or 'none'}")

    if args.status:
        return 0

    retention = dict(DEFAULT_RETENTION_DAYS)
    if args.retention:
        retention.update(_json.loads(args.retention))
    now = datetime.now(timezone.utc)

    if args.index:
        print(f"created indices: {', '.join(add_index_if_missing(path))}")

    rows = plan(path, retention, now)
    if not rows:
        print("nothing eligible for pruning")
        return 0
    print("\nplan:")
    for event_type, cutoff, count in rows:
        print(f"  {event_type:30} older than {cutoff}: {count:>10,}")

    if not args.apply:
        print("\ndry run — pass --apply to delete")
        return 0

    removed = apply(path, retention, now)
    print("\ndeleted:")
    for name, count in removed.items():
        print(f"  {name:30} {count:>10,}")

    if args.vacuum:
        print("running VACUUM (may take a while)…")
        vacuum(path)
        after = status(path)
        print(f"  size now {after['size_mb']} MB (was {info['size_mb']} MB)")
    else:
        print("\nnote: free pages are not returned to the filesystem until VACUUM; "
              "re-run with --vacuum")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
