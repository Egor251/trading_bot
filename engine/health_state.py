"""Write the health state file the container HEALTHCHECK actually reads.

`tools/healthcheck.py` can validate a state file, and `docs/runbooks/launch.md`
documents one at `data/health.json` — but nothing in the repo ever wrote it, and
the Dockerfile omitted `--state-file`, so the probe degraded to "is PID 1 still
host/main.py". A halted book, a dead feed or a crashed market service therefore
reported `healthy` forever and the container was never restarted.

The ops monitor's heartbeat loop is the right owner: it already knows whether the
runtime, the feed and the reconciler are alive. It writes:

    {"status": "ok"|"degraded"|"halted"|"down", "timestamp": <epoch>,
     "detail": "...", "halted": bool, "feed_age_seconds": float|None}

`status` is deliberately not binary: a container orchestrator should not bounce a
process that is correctly refusing to trade, but the Operator has to be able to
tell "halted on purpose" from "wedged". The healthcheck script only treats
non-``ok`` as unhealthy when the file also goes stale, so a deliberate halt stays
visible without causing a restart loop.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

__all__ = ["HEALTH_FILE", "default_health_path", "write_health_state", "evaluate_health"]


def default_health_path() -> Path:
    from engine.paths import data_path

    override = str(os.environ.get("TRADING_HEALTH_FILE") or "").strip()
    if override:
        return Path(override)
    return data_path("health.json")


HEALTH_FILE = default_health_path()


def evaluate_health(
    *,
    runtime: Any = None,
    gaps: Any = None,
    now: float,
    stale_feed_sec: float = 180.0,
) -> tuple[str, str]:
    """Return ``(status, detail)`` for the current runtime + feed state."""
    if runtime is None:
        return "down", "trading runtime not initialised"
    halted = bool(getattr(runtime, "halted", False))
    reason = str(getattr(runtime, "_halt_reason", "") or "")
    feed_age: float | None = None
    if gaps is not None and hasattr(gaps, "feed_age"):
        try:
            age = gaps.feed_age("BTC/USDT")
            if age is None and hasattr(gaps, "feed_age"):
                # Try any symbol we have seen.
                seen = getattr(gaps, "_max_seen", {}) or {}
                if seen:
                    age = gaps.feed_age(next(iter(seen)))
            feed_age = None if age is None else float(age)
        except Exception:  # noqa: BLE001
            feed_age = None
    parts: list[str] = []
    if feed_age is not None:
        parts.append(f"feed_age={feed_age:.0f}s")
    if feed_age is not None and feed_age > float(stale_feed_sec):
        return "degraded", "market feed stale: " + " ".join(parts)
    if halted:
        return "halted", f"risk halted ({reason or 'no reason recorded'})"
    return "ok", " ".join(parts) or "nominal"


def write_health_state(
    *,
    runtime: Any = None,
    gaps: Any = None,
    path: Path | None = None,
    now: float | None = None,
    stale_feed_sec: float = 180.0,
) -> Path:
    """Atomically publish the current health state. Never raises."""
    import time

    target = Path(path) if path is not None else default_health_path()
    try:
        current = time.time() if now is None else float(now)
        status, detail = evaluate_health(
            runtime=runtime, gaps=gaps, now=current, stale_feed_sec=stale_feed_sec
        )
        payload = {
            "status": status,
            "timestamp": current,
            "detail": detail,
            "halted": bool(getattr(runtime, "halted", False)) if runtime else None,
            "mode": str(getattr(runtime, "mode", "") or "") if runtime else None,
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        # tmp + os.replace: a probe must never read a half-written file, and a
        # truncated health file is indistinguishable from a dead engine.
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        )
        try:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            handle.close()
        os.replace(handle.name, target)
    except Exception:  # noqa: BLE001 - health reporting must never break trading
        pass
    return target
