#!/usr/bin/env python3
"""Container/process health check using only the Python standard library."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any


HEALTHY_STATES = {"ok", "ready", "healthy", "running"}


def check_state_file(path: Path, *, now: float, max_age: float) -> tuple[bool, str]:
    try:
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        state = str(payload["status"]).lower()
        age = now - float(payload["timestamp"])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return False, f"invalid health state: {exc}"
    if state not in HEALTHY_STATES:
        return False, f"unhealthy state: {state}"
    if age < -5:
        return False, "health timestamp is in the future"
    if age > max_age:
        return False, f"health state is stale: {age:.1f}s"
    return True, f"state={state} age={age:.1f}s"


def check_pid_command(pid: int, pattern: str, *, proc_root: Path = Path("/proc")) -> tuple[bool, str]:
    try:
        raw = (proc_root / str(pid) / "cmdline").read_bytes()
    except OSError as exc:
        return False, f"cannot inspect pid {pid}: {exc}"
    command = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    if pattern not in command:
        return False, f"pid {pid} command does not contain {pattern!r}"
    return True, f"pid={pid} command matched"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--max-age", type=float, default=120.0)
    parser.add_argument("--pid", type=int, default=1)
    parser.add_argument("--process-pattern", default="host/main.py")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.state_file is not None:
        healthy, reason = check_state_file(
            args.state_file, now=time.time(), max_age=args.max_age
        )
    else:
        healthy, reason = check_pid_command(args.pid, args.process_pattern)
    print(reason)
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
