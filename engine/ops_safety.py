"""Operational safety primitives from docs/todo2.md §5 (gaps, heartbeat, alerts, journal)."""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Mapping
from uuid import uuid4


Clock = Callable[[], float]


def _iso(ts: float | None = None) -> str:
    value = datetime.now(UTC) if ts is None else datetime.fromtimestamp(ts, tz=UTC)
    return value.isoformat()


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class OpsAlert:
    alert_id: str
    name: str
    severity: AlertSeverity | str
    summary: str
    timestamp: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["severity"] = (
            self.severity.value if isinstance(self.severity, AlertSeverity) else str(self.severity)
        )
        return payload


class AlertBus:
    """In-memory alert fan-out with de-duplication; optional JSONL sink.

    Unconditional emission produced ~1,430 identical
    ``performance_diagnosis`` alerts per day for weeks (5.9 MB of
    ``alerts.jsonl``), which is how a real alert gets lost: anything that fires
    every minute is noise. Repeats of the same ``(name, severity)`` within
    ``cooldown_sec`` are counted rather than re-emitted, and the count is folded
    into the next alert's details so the signal is not silently dropped.
    """

    def __init__(
        self,
        *,
        path: str | Path | None = None,
        max_alerts: int = 2_000,
        sink: Callable[[OpsAlert], None] | None = None,
        cooldown_sec: float = 900.0,
    ) -> None:
        self.path = Path(path) if path is not None else None
        self.sink = sink
        self.cooldown_sec = float(cooldown_sec)
        self._alerts: deque[OpsAlert] = deque(maxlen=max_alerts)
        self._lock = RLock()
        self._last_emit: dict[tuple[str, str], float] = {}
        self._suppressed: dict[tuple[str, str], int] = {}

    def emit(
        self,
        name: str,
        summary: str,
        *,
        severity: AlertSeverity | str = AlertSeverity.INFO,
        details: Mapping[str, Any] | None = None,
    ) -> OpsAlert | None:
        """Emit an alert, or return ``None`` when it is a suppressed repeat."""
        level = (
            severity if isinstance(severity, AlertSeverity) else AlertSeverity(severity)
        )
        key = (str(name), level.value)
        now = time.time()
        with self._lock:
            last = self._last_emit.get(key)
            if last is not None and (now - last) < self.cooldown_sec:
                self._suppressed[key] = self._suppressed.get(key, 0) + 1
                return None
            suppressed = self._suppressed.pop(key, 0)
            self._last_emit[key] = now
        merged = dict(details or {})
        if suppressed:
            merged["suppressed_repeats"] = suppressed
        alert = OpsAlert(
            alert_id=uuid4().hex,
            name=str(name),
            severity=level,
            summary=str(summary),
            timestamp=_iso(),
            details=merged,
        )
        with self._lock:
            self._alerts.append(alert)
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(alert.to_dict(), sort_keys=True) + "\n")
        if self.sink is not None:
            try:
                self.sink(alert)
            except Exception:
                pass
        return alert

    def suppressed_count(self, name: str, severity: str = "info") -> int:
        return self._suppressed.get((str(name), str(severity)), 0)

    def recent(self, limit: int = 50) -> list[OpsAlert]:
        with self._lock:
            items = list(self._alerts)
        return items[-int(limit) :]


@dataclass(slots=True)
class HeartbeatWatchdog:
    """Component liveness: missing heartbeat → dead."""

    timeout_sec: float = 30.0
    clock: Clock = field(default_factory=lambda: __import__("time").time)
    _last: dict[str, float] = field(default_factory=dict)

    def beat(self, component: str, *, now: float | None = None) -> None:
        self._last[str(component)] = float(self.clock() if now is None else now)

    def dead(self, *, now: float | None = None) -> list[str]:
        current = float(self.clock() if now is None else now)
        return [
            name
            for name, ts in self._last.items()
            if (current - ts) > float(self.timeout_sec)
        ]

    def snapshot(self) -> dict[str, Any]:
        now = float(self.clock())
        return {
            "timeout_sec": self.timeout_sec,
            "components": {
                name: {"age_sec": now - ts, "alive": (now - ts) <= self.timeout_sec}
                for name, ts in self._last.items()
            },
        }


@dataclass(slots=True)
class DataGapDetector:
    """Detect missing / duplicate / out-of-order candle timestamps per symbol.

    Takes the **exchange** bar timestamp, not the arrival time. The ops monitor
    used to pass ``time.time()`` because candle payloads carried only
    ``date``/``time`` strings, so a >90 s hiccup in a 5 s poll loop was reported
    as a data gap, ``freeze_on_data_anomaly`` halted risk, and nothing ever
    resumed it. That combination is why the production journal shows a month of
    ``risk.reject{stage: halted, reason: market_data_gap}`` with zero orders.

    A *stale* feed is a separate condition and is reported as ``stale``: the last
    bar is older than the tolerance, which is the signal an operator needs, and
    it does not depend on inter-arrival timing at all.
    """

    expected_interval_sec: float = 60.0
    max_gap_factor: float = 1.5
    #: Bar age above which the feed counts as stale.
    stale_after_sec: float = 180.0
    #: Consecutive stale observations before escalating (a feed recovers).
    stale_tolerance: int = 3
    _last_ts: dict[str, float] = field(default_factory=dict)
    _last_fingerprint: dict[str, str] = field(default_factory=dict)
    _stale_streak: dict[str, int] = field(default_factory=dict)
    _max_seen: dict[str, float] = field(default_factory=dict)

    def feed_age(self, symbol: str, *, now: float | None = None) -> float | None:
        """Seconds since the newest bar seen for *symbol*."""
        last = self._max_seen.get(str(symbol))
        if last is None:
            return None
        current = __import__("time").time() if now is None else float(now)
        return max(0.0, current - last)

    def observe(
        self,
        symbol: str,
        timestamp: float,
        *,
        volume: float | None = None,
        now: float | None = None,
    ) -> list[str]:
        """Return anomaly codes: gap, duplicate, out_of_order, stale."""
        issues: list[str] = []
        key = str(symbol)
        try:
            ts = float(timestamp)
        except (TypeError, ValueError):
            return ["invalid_timestamp"]
        current = __import__("time").time() if now is None else float(now)
        self._max_seen[key] = max(self._max_seen.get(key, ts), ts)
        age = max(0.0, current - self._max_seen[key])
        if age > float(self.stale_after_sec):
            streak = self._stale_streak.get(key, 0) + 1
            self._stale_streak[key] = streak
            if streak >= int(self.stale_tolerance):
                issues.append("stale")
        else:
            self._stale_streak[key] = 0

        fp = f"{ts}:{volume if volume is not None else ''}"
        prev = self._last_ts.get(key)
        if prev is not None:
            delta = ts - prev
            # A bar whose *close* time is identical to the previous one is a
            # duplicate; a bar that moves backwards is out of order. Both are
            # only reachable with real exchange timestamps, which is why these
            # two branches used to be dead code.
            if abs(delta) < 1e-9:
                issues.append("duplicate")
            elif delta > self.expected_interval_sec * self.max_gap_factor:
                issues.append("gap")
            elif delta < -1e-6:
                issues.append("out_of_order")
                # Do not let a late bar rewind the watermark for everyone else.
                return issues
        self._last_ts[key] = ts
        self._last_fingerprint[key] = fp
        return issues


@dataclass(frozen=True, slots=True)
class TradeJournalEntry:
    trade_id: str
    timestamp: str
    symbol: str
    side: str
    quantity: float
    price: float
    fee: float
    strategy_id: str
    reason: str
    pnl: float | None = None
    currency: str = "USD"
    meta: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TradeJournal:
    """Append-only trade log for accounting / tax export later."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._rows: list[TradeJournalEntry] = []
        self._lock = RLock()

    def record(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        strategy_id: str = "",
        reason: str = "",
        fee: float = 0.0,
        pnl: float | None = None,
        currency: str = "USD",
        meta: Mapping[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> TradeJournalEntry:
        entry = TradeJournalEntry(
            trade_id=uuid4().hex,
            timestamp=timestamp or _iso(),
            symbol=str(symbol),
            side=str(side),
            quantity=float(quantity),
            price=float(price),
            fee=float(fee),
            strategy_id=str(strategy_id or ""),
            reason=str(reason or ""),
            pnl=None if pnl is None else float(pnl),
            currency=str(currency or "USD"),
            meta=dict(meta or {}),
        )
        with self._lock:
            self._rows.append(entry)
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(entry.to_dict(), sort_keys=True) + "\n")
        return entry

    def entries(self) -> list[TradeJournalEntry]:
        with self._lock:
            return list(self._rows)

    def export_rows(self) -> list[dict[str, Any]]:
        return [row.to_dict() for row in self.entries()]


@dataclass(slots=True)
class TelegramAlertSink:
    """Optional Telegram notifier (todo2 §5.3).

    Without ``bot_token`` / ``chat_id`` this is a no-op recorder used in tests
    and local paper mode. Wire real HTTP later via ``sender``.
    """

    bot_token: str = ""
    chat_id: str = ""
    sender: Callable[[str, str, OpsAlert], None] | None = None
    sent: list[OpsAlert] = field(default_factory=list)

    @property
    def configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def __call__(self, alert: OpsAlert) -> None:
        self.sent.append(alert)
        if self.sender is not None and self.configured:
            self.sender(self.bot_token, self.chat_id, alert)

    def format_message(self, alert: OpsAlert) -> str:
        sev = (
            alert.severity.value
            if isinstance(alert.severity, AlertSeverity)
            else str(alert.severity)
        )
        return f"[{sev.upper()}] {alert.name}: {alert.summary}"


def attach_telegram_sink(
    bus: AlertBus,
    *,
    bot_token: str = "",
    chat_id: str = "",
    sender: Callable[[str, str, OpsAlert], None] | None = None,
) -> TelegramAlertSink:
    sink = TelegramAlertSink(bot_token=bot_token, chat_id=chat_id, sender=sender)
    bus.sink = sink
    return sink
