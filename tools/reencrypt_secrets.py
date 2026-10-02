"""One-shot: re-encrypt cleartext rows in ``operator_secrets``.

Run after upgrading to the encrypted secret store. It rewrites every row whose
value is not already in the ``v1:<backend>:…`` form, so a database written by an
older build stops containing readable exchange credentials.

Safe to re-run: already-encrypted rows are left alone, and the transaction is
committed as a single unit, so a crash mid-run leaves the previous cleartext
rows intact rather than a half-converted table.

    python -m tools.reencrypt_secrets [--db data/storage/records.db] [--dry-run]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host.secret_store import (  # noqa: E402
    SecretBackendUnavailable,
    backend_name,
    decrypt_secret,
    encrypt_secret,
    is_encrypted,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None, help="path to records.db")
    parser.add_argument(
        "--dry-run", action="store_true", help="report only, do not write"
    )
    args = parser.parse_args(argv)

    if args.db:
        db = Path(args.db)
    else:
        from host.secrets_env import sqlite_path

        db = sqlite_path()

    if not db.is_file():
        print(f"no secret database at {db}")
        return 1

    try:
        backend = backend_name()
    except SecretBackendUnavailable as exc:
        print(f"ERROR: {exc}")
        return 2
    if backend in {"none", "plaintext"}:
        print(
            f"ERROR: refusing to run with backend {backend!r}. Configure DPAPI "
            "(Windows) or TRADING_SECRET_STORE_KEY first."
        )
        return 2

    conn = sqlite3.connect(str(db), timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        rows = conn.execute("SELECT name, value, updated_at FROM operator_secrets").fetchall()
    except sqlite3.Error as exc:
        print(f"ERROR: cannot read {db}: {exc}")
        return 1

    plain = [r for r in rows if not is_encrypted(str(r[1] or ""))]
    print(f"backend={backend} rows={len(rows)} cleartext={len(plain)}")
    for name, _value, _ts in plain:
        print(f"  would encrypt: {name}")
    if args.dry_run:
        print("dry run — nothing written")
        return 0
    if not plain:
        print("nothing to do")
        return 0

    now = datetime.now(timezone.utc).isoformat()
    try:
        with conn:
            for name, value, _ts in plain:
                conn.execute(
                    "UPDATE operator_secrets SET value=?, updated_at=? WHERE name=?",
                    (encrypt_secret(decrypt_secret(str(value))), now, str(name)),
                )
    except (sqlite3.Error, SecretBackendUnavailable) as exc:
        print(f"ERROR: migration failed, nothing committed: {exc}")
        return 1

    # Re-read to prove the table no longer contains the original plaintext.
    remaining = [
        r
        for r in conn.execute("SELECT name, value FROM operator_secrets").fetchall()
        if not is_encrypted(str(r[1] or ""))
    ]
    print(f"re-encrypted {len(plain)} row(s); cleartext remaining: {len(remaining)}")
    if remaining:
        print("ERROR: verification failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
