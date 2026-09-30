"""Persist operator API keys in the SQLite storage DB.

YAML keeps only ``api_key_env`` names. Values live in table ``operator_secrets``
inside the same SQLite file as ``cb_storage`` (default ``data/storage/records.db``).
Optional project ``.env`` still loads first (does not override non-empty env).
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOTENV_PATH = ROOT / ".env"
LEGACY_SECRETS_PATH = ROOT / "data" / "secrets.env"

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_DDL = """
CREATE TABLE IF NOT EXISTS operator_secrets (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_sqlite_path_cache: Path | None = None


def sqlite_path(*, refresh: bool = False) -> Path:
    """Resolve configured sqlite backend path (same DB as storage records)."""
    global _sqlite_path_cache
    if _sqlite_path_cache is not None and not refresh:
        return _sqlite_path_cache
    cfg = ROOT / "config" / "trading_bot.yaml"
    resolved = ROOT / "data" / "storage" / "records.db"
    try:
        import yaml

        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        path = (
            ((data.get("storage") or {}).get("backends") or {})
            .get("sqlite", {})
            .get("path")
        )
        if path:
            p = Path(str(path))
            resolved = p if p.is_absolute() else ROOT / p
    except Exception:
        pass
    _sqlite_path_cache = resolved
    return resolved


def _connect(
    path: Path | None = None,
    *,
    timeout: float = 30.0,
    busy_timeout_ms: int = 30_000,
) -> sqlite3.Connection:
    db = path or sqlite_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db), timeout=float(timeout))
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    conn.execute(_DDL)
    conn.commit()
    return conn


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not _KEY_RE.match(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def _read_all(path: Path | None = None) -> dict[str, str]:
    try:
        with _connect(path) as conn:
            rows = conn.execute("SELECT name, value FROM operator_secrets").fetchall()
    except sqlite3.Error:
        return {}
    return {str(k): str(v) for k, v in rows if k and v is not None}


def _migrate_legacy_file() -> None:
    """One-shot: import data/secrets.env into SQLite, then rename the file."""
    if not LEGACY_SECRETS_PATH.is_file():
        return
    legacy = _parse_env_file(LEGACY_SECRETS_PATH)
    if not legacy:
        return
    for key, value in legacy.items():
        upsert_secret(key, value, _skip_environ=True)
    try:
        LEGACY_SECRETS_PATH.replace(LEGACY_SECRETS_PATH.with_suffix(".env.migrated"))
    except OSError:
        pass


def load_secrets_into_environ(*, override: bool = False) -> list[str]:
    """Load ``.env`` then SQLite ``operator_secrets`` into ``os.environ``."""
    loaded: list[str] = []
    merged: dict[str, str] = {}
    if DOTENV_PATH.is_file():
        merged.update(_parse_env_file(DOTENV_PATH))
    _migrate_legacy_file()
    merged.update(_read_all())
    for key, value in merged.items():
        cur = str(os.environ.get(key) or "").strip()
        if cur and not override:
            continue
        if value == "" and cur:
            continue
        os.environ[key] = value
        loaded.append(key)
    return loaded


def upsert_secret(name: str, value: str, *, _skip_environ: bool = False) -> None:
    """Write/replace one key in SQLite and (unless skipped) process env."""
    key = (name or "").strip()
    val = (value or "").strip()
    if not _KEY_RE.match(key) or not val:
        return
    if not _skip_environ:
        os.environ[key] = val
    ts = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO operator_secrets(name, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at
            """,
            (key, val, ts),
        )
        conn.commit()


def has_secret(name: str) -> bool:
    key = (name or "").strip()
    if not key:
        return False
    if str(os.environ.get(key) or "").strip():
        return True
    try:
        # Presence checks must not stall UI snapshots when the storage DB is busy.
        with _connect(timeout=0.5, busy_timeout_ms=500) as conn:
            row = conn.execute(
                "SELECT 1 FROM operator_secrets WHERE name=? AND value!='' LIMIT 1",
                (key,),
            ).fetchone()
        return bool(row)
    except sqlite3.Error:
        return False


def secrets_present(names: list[str] | tuple[str, ...]) -> dict[str, bool]:
    """Batch presence map: env first, then one short SQLite lookup for misses."""
    present: dict[str, bool] = {}
    missing: list[str] = []
    for raw in names:
        key = (raw or "").strip()
        if not key:
            continue
        if str(os.environ.get(key) or "").strip():
            present[key] = True
        else:
            present[key] = False
            missing.append(key)
    if not missing:
        return present
    try:
        placeholders = ",".join("?" for _ in missing)
        with _connect(timeout=0.5, busy_timeout_ms=500) as conn:
            rows = conn.execute(
                f"SELECT name FROM operator_secrets "
                f"WHERE name IN ({placeholders}) AND value!=''",
                missing,
            ).fetchall()
        found = {str(r[0]) for r in rows if r and r[0]}
        for key in missing:
            present[key] = key in found
    except sqlite3.Error:
        pass
    return present
