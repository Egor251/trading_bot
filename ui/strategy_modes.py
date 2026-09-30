"""Strategy roster mode derivation from snapshot (no GUI)."""

from __future__ import annotations

from typing import Any


def modes_from_snap(
    *,
    ids: list[str],
    live: list[str],
    observe: list[str],
    snap: dict[str, Any],
) -> dict[str, str]:
    live_set = set(live)
    obs_set = {s for s in observe if s not in live_set}
    next_modes: dict[str, str] = {}
    for sid in ids:
        if sid in live_set:
            next_modes[sid] = "live"
        elif sid in obs_set:
            next_modes[sid] = "observe"
        else:
            mode = "off"
            for p in snap.get("plugins") or []:
                if str(p.get("id") or "") != sid:
                    continue
                if p.get("observe_only"):
                    mode = "observe"
                elif p.get("enabled") and p.get("running"):
                    mode = "observe" if sid not in live_set else "live"
                break
            next_modes[sid] = mode
    return next_modes


# Back-compat alias used by older tests
_modes_from_snap = modes_from_snap
