"""SQLite-backed operator secrets store."""

from __future__ import annotations

import os

from host import secrets_env


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
