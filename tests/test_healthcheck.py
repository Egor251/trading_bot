"""Tests for the dependency-free container health check."""

from __future__ import annotations

import json

from tools.healthcheck import check_pid_command, check_state_file


def test_state_file_health_and_staleness(tmp_path) -> None:
    path = tmp_path / "health.json"
    path.write_text(
        json.dumps({"status": "ready", "timestamp": 100.0}), encoding="utf-8"
    )

    assert check_state_file(path, now=105.0, max_age=10.0)[0]
    healthy, reason = check_state_file(path, now=111.0, max_age=10.0)
    assert not healthy
    assert "stale" in reason


def test_process_health_uses_proc_cmdline(tmp_path) -> None:
    process = tmp_path / "7"
    process.mkdir()
    (process / "cmdline").write_bytes(b"python\x00host/main.py\x00--headless")

    assert check_pid_command(7, "host/main.py", proc_root=tmp_path)[0]
    assert not check_pid_command(7, "other.py", proc_root=tmp_path)[0]
