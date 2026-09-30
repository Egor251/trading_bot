"""Phantom time-travel event sourcing (docs/todo2.md §4)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Mapping, Sequence
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class PhantomTimelineEvent:
    sequence: int
    run_id: str
    bar_index: int
    timestamp: str
    symbol: str
    market: Mapping[str, Any]
    strategies: Mapping[str, Any]
    signal: Mapping[str, Any] | None
    decision: Mapping[str, Any]
    meta: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PhantomTimeline:
    """Append-only decision log for a phantom run (JSONL optional)."""

    def __init__(
        self,
        *,
        run_id: str | None = None,
        path: str | Path | None = None,
        max_events: int = 50_000,
    ) -> None:
        self.run_id = run_id or uuid4().hex
        self.path = Path(path) if path is not None else None
        self.max_events = int(max_events)
        self._events: list[PhantomTimelineEvent] = []
        self._lock = RLock()
        self._seq = 0

    def record(
        self,
        *,
        bar_index: int,
        symbol: str,
        market: Mapping[str, Any],
        strategies: Mapping[str, Any],
        decision: Mapping[str, Any],
        signal: Mapping[str, Any] | None = None,
        meta: Mapping[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> PhantomTimelineEvent:
        with self._lock:
            self._seq += 1
            event = PhantomTimelineEvent(
                sequence=self._seq,
                run_id=self.run_id,
                bar_index=int(bar_index),
                timestamp=timestamp or _now(),
                symbol=str(symbol),
                market=dict(market),
                strategies=dict(strategies),
                signal=dict(signal) if signal else None,
                decision=dict(decision),
                meta=dict(meta or {}),
            )
            self._events.append(event)
            if len(self._events) > self.max_events:
                self._events = self._events[-self.max_events :]
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
            return event

    def at(self, bar_index: int) -> list[PhantomTimelineEvent]:
        with self._lock:
            return [e for e in self._events if e.bar_index == bar_index]

    def range(
        self, start: int, end: int
    ) -> list[PhantomTimelineEvent]:
        lo, hi = int(start), int(end)
        with self._lock:
            return [e for e in self._events if lo <= e.bar_index <= hi]

    def events(self) -> list[PhantomTimelineEvent]:
        with self._lock:
            return list(self._events)

    def markers(self) -> list[dict[str, Any]]:
        """UI-friendly signal markers (green/red/gray)."""
        out: list[dict[str, Any]] = []
        for event in self.events():
            decision = event.decision or {}
            status = str(decision.get("status") or "unknown").lower()
            side = str(
                (event.signal or {}).get("side")
                or decision.get("side")
                or ""
            ).lower()
            if status in {"rejected", "blocked", "risk_rejected"}:
                color = "gray"
            elif side in {"buy", "long"}:
                color = "green"
            elif side in {"sell", "short"}:
                color = "red"
            else:
                color = "gray"
            out.append(
                {
                    "bar_index": event.bar_index,
                    "symbol": event.symbol,
                    "color": color,
                    "status": status,
                    "side": side,
                    "sequence": event.sequence,
                    "reason": decision.get("reason") or decision.get("summary"),
                }
            )
        return out

    def snapshot(self) -> dict[str, Any]:
        ev = self.events()
        return {
            "run_id": self.run_id,
            "path": str(self.path) if self.path else None,
            "count": len(ev),
            "bar_min": ev[0].bar_index if ev else None,
            "bar_max": ev[-1].bar_index if ev else None,
        }

    def export_ui(self, *, max_events: int = 500) -> dict[str, Any]:
        """Compact payload for Operator timeline scrubber."""
        ev = self.events()
        if len(ev) > max_events:
            ev = ev[-max_events:]
        return {
            **self.snapshot(),
            "markers": self.markers()[-max_events:],
            "events": [e.to_dict() for e in ev],
        }

    @classmethod
    def load_jsonl(cls, path: str | Path) -> "PhantomTimeline":
        p = Path(path)
        timeline = cls(path=None)
        if not p.is_file():
            return timeline
        with p.open(encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                timeline.run_id = str(raw.get("run_id") or timeline.run_id)
                timeline._seq = max(timeline._seq, int(raw.get("sequence") or 0))
                timeline._events.append(
                    PhantomTimelineEvent(
                        sequence=int(raw["sequence"]),
                        run_id=str(raw["run_id"]),
                        bar_index=int(raw["bar_index"]),
                        timestamp=str(raw["timestamp"]),
                        symbol=str(raw["symbol"]),
                        market=dict(raw.get("market") or {}),
                        strategies=dict(raw.get("strategies") or {}),
                        signal=dict(raw["signal"]) if raw.get("signal") else None,
                        decision=dict(raw.get("decision") or {}),
                        meta=dict(raw.get("meta") or {}),
                    )
                )
        return timeline
