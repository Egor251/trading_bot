"""Durable runtime state snapshots for crash recovery (todo2 §5.2)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class StateStore:
    """Atomic JSON snapshot writer/reader for trading runtime state."""

    path: Path

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, payload: Mapping[str, Any], *, label: str = "runtime") -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "saved_at": _now(),
            "label": str(label),
            "payload": dict(payload),
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(body, indent=2, sort_keys=True, default=str), encoding="utf-8")
        tmp.replace(self.path)
        return self.path

    def load(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        payload = raw.get("payload")
        return dict(payload) if isinstance(payload, dict) else None

    def meta(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        return {
            "saved_at": raw.get("saved_at"),
            "label": raw.get("label"),
            "path": str(self.path),
        }
