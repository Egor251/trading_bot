"""SQLite-backed operator secrets store.

Values are encrypted at rest (DPAPI on Windows, AES-256-GCM elsewhere) — the
table used to hold cleartext exchange keys inside the same 3+ GB file as the
trade journal.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from host import secret_store, secrets_env


def test_upsert_and_load_roundtrip(tmp_path, monkeypatch) -> None:
    db = tmp_path / "records.db"
    monkeypatch.setattr(secrets_env, "sqlite_path", lambda: db)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    secrets_env.upsert_secret("DEEPSEEK_API_KEY", "sk-test-123")
    assert os.environ.get("DEEPSEEK_API_KEY") == "sk-test-123"
    assert secrets_env.has_secret("DEEPSEEK_API_KEY")

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    loaded = secrets_env.load_secrets_into_environ()
    assert "DEEPSEEK_API_KEY" in loaded
    assert os.environ.get("DEEPSEEK_API_KEY") == "sk-test-123"


def test_reject_invalid_name(tmp_path, monkeypatch) -> None:
    db = tmp_path / "records.db"
    monkeypatch.setattr(secrets_env, "sqlite_path", lambda: db)
    secrets_env.upsert_secret("bad-name!", "x")
    assert secrets_env._read_all(db) == {}


def test_stored_value_is_not_plaintext(tmp_path, monkeypatch) -> None:
    """The DB must not contain the credential in the clear."""
    db = tmp_path / "records.db"
    monkeypatch.setattr(secrets_env, "sqlite_path", lambda: db)
    secrets_env.upsert_secret("BINANCE_KEY", "sk-super-secret-value")

    with sqlite3.connect(str(db)) as conn:
        stored = str(conn.execute("SELECT value FROM operator_secrets").fetchone()[0])
    assert "sk-super-secret-value" not in stored
    assert stored.startswith("v1:")
    assert secret_store.is_encrypted(stored)
    assert secret_store.decrypt_secret(stored) == "sk-super-secret-value"


def test_legacy_cleartext_row_is_read_and_upgraded(tmp_path, monkeypatch) -> None:
    """Rows written by older builds stay readable and get re-encrypted."""
    db = tmp_path / "records.db"
    monkeypatch.setattr(secrets_env, "sqlite_path", lambda: db)
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "CREATE TABLE operator_secrets ("
            "name TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO operator_secrets VALUES ('OLD_KEY', 'legacy-plain', 'now')"
        )
        conn.commit()

    assert secrets_env._read_all(db) == {"OLD_KEY": "legacy-plain"}

    secrets_env.upsert_secret("OLD_KEY", "legacy-plain")
    with sqlite3.connect(str(db)) as conn:
        stored = str(conn.execute("SELECT value FROM operator_secrets").fetchone()[0])
    assert stored != "legacy-plain"
    assert secret_store.decrypt_secret(stored) == "legacy-plain"


def test_persistence_fails_closed_without_a_backend(tmp_path, monkeypatch) -> None:
    """No encryption backend and no explicit opt-in must not write cleartext."""
    db = tmp_path / "records.db"
    monkeypatch.setattr(secrets_env, "sqlite_path", lambda: db)
    monkeypatch.setattr(secret_store, "_cached", None, raising=False)
    monkeypatch.delenv(secret_store.KEY_ENV, raising=False)
    monkeypatch.delenv(secret_store.PLAIN_ENV, raising=False)
    monkeypatch.setattr(secret_store.os, "name", "posix")
    monkeypatch.setattr(
        secret_store, "_AesGcmCipher", lambda key: (_ for _ in ()).throw(ImportError())
    )

    def _no_backend():
        raise secret_store.SecretBackendUnavailable("no backend in this test")

    monkeypatch.setattr(secret_store, "get_cipher", _no_backend)
    with pytest.raises(secret_store.SecretBackendUnavailable):
        secrets_env.upsert_secret("ANY_KEY", "sk-value")
    # Nothing was written — not even the table.
    assert b"sk-value" not in (db.read_bytes() if db.exists() else b"")


def test_backend_mismatch_is_reported_not_silently_ignored() -> None:
    """A token written by another backend must fail loudly, not return junk."""
    cipher = secret_store._AesGcmCipher(secret_store._derive_key("k" * 32))
    token = f"v1:{cipher.name}:{cipher.encrypt('value')}"

    class _Other:
        name = "other"

        def decrypt(self, _t):  # pragma: no cover - never reached
            raise AssertionError("must not decrypt with a different backend")

    original = secret_store.get_cipher
    secret_store.get_cipher = lambda: _Other()
    try:
        with pytest.raises(secret_store.SecretBackendUnavailable, match="other"):
            secret_store.decrypt_secret(token)
    finally:
        secret_store.get_cipher = original


def test_legacy_env_file_is_deleted_not_renamed(tmp_path, monkeypatch) -> None:
    """Migration must not leave a plaintext copy next to the database."""
    db = tmp_path / "records.db"
    legacy = tmp_path / "secrets.env"
    legacy.write_text("LEGACY_KEY=sk-legacy-value\n", encoding="utf-8")
    monkeypatch.setattr(secrets_env, "sqlite_path", lambda: db)
    monkeypatch.setattr(secrets_env, "LEGACY_SECRETS_PATH", legacy)

    secrets_env._migrate_legacy_file()

    assert not legacy.exists()
    assert not legacy.with_suffix(".env.migrated").exists()
    assert secrets_env._read_all(db) == {"LEGACY_KEY": "sk-legacy-value"}
