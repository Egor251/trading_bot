"""Detect new trade/order events in UI snapshots and format toast text."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable

from ui.notify_dispatch import item_passes_filters
from ui.notify_settings import NotifySettings
from ui.i18n import tr

EmitFn = Callable[..., Any]
_SEEN_MAX = 500
_SEEN_KEEP = 250


def _fingerprint(item: dict[str, Any]) -> str:
    """Stable-ish id for dedupe without collapsing distinct fills."""
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    order = payload.get("order") if isinstance(payload.get("order"), dict) else {}
    parts = [
        str(item.get("type") or ""),
        str(payload.get("action") or order.get("action") or ""),
        str(payload.get("ticker") or order.get("ticker") or ""),
        str(order.get("status") or payload.get("status") or ""),
        str(payload.get("success")),
        str(order.get("client_id") or payload.get("client_id") or ""),
        str(payload.get("quantity") or order.get("quantity") or ""),
        str(payload.get("price") or order.get("price") or ""),
        str(payload.get("reason") or order.get("reason") or ""),
        # Millisecond bucket — distinct fills in the same second stay distinct.
        f"{float(item.get('t') or 0):.3f}",
    ]
    return "|".join(parts)


def format_trade_notification(item: dict[str, Any]) -> tuple[str, str] | None:
    """Return (title, body) or None if event type is unknown."""
    et = str(item.get("type") or "")
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    order = payload.get("order") if isinstance(payload.get("order"), dict) else {}

    if et == "execution.result":
        ok = payload.get("success")
        action = str(payload.get("action") or order.get("action") or "?").upper()
        ticker = str(payload.get("ticker") or order.get("ticker") or "?")
        qty = payload.get("quantity") or order.get("quantity") or ""
        price = payload.get("price") or order.get("price") or ""
        status = str(order.get("status") or ("ok" if ok else "reject")).lower()
        mode = str(payload.get("mode") or "")
        if ok is False:
            title = tr("sys.notify.toast.exec_reject")
        elif status in {"filled", "partial", "ok"} or ok is True:
            title = tr("sys.notify.toast.exec_fill")
        else:
            title = tr("sys.notify.toast.exec_order")
        body = f"{action} {qty} {ticker}".strip()
        if price not in ("", None):
            body += f" @ {price}"
        if mode:
            body += f" ({mode})"
        err = payload.get("error") or order.get("reason")
        if err and not ok:
            body += f" — {err}"
        return title, body

    if et == "risk.reject":
        reason = str(payload.get("reason") or "risk")
        ticker = str(payload.get("ticker") or "")
        action = str(payload.get("action") or "")
        return tr("sys.notify.toast.risk"), f"{action} {ticker} — {reason}".strip(" —")

    if et == "ensemble.decision":
        action = str(payload.get("action") or "?").upper()
        ticker = str(payload.get("ticker") or "?")
        qty = payload.get("quantity") or ""
        return tr("sys.notify.toast.ensemble"), f"{action} {qty} {ticker}".strip()

    if et == "ensemble.reject":
        reason = str(payload.get("reason") or "reject")
        ticker = str(payload.get("ticker") or "")
        return tr("sys.notify.toast.ensemble_reject"), f"{ticker} — {reason}".strip(" —")

    if et == "ops.alert":
        summary = str(payload.get("summary") or payload.get("message") or "alert")
        kind = str(payload.get("kind") or payload.get("code") or "ops")
        return tr("sys.notify.toast.ops", kind=kind), summary

    if et == "system.halted":
        reason = str(payload.get("reason") or "halt")
        return tr("sys.notify.toast.halted"), reason

    if et == "system.resumed":
        return tr("sys.notify.toast.resumed"), str(payload.get("reason") or "resume")

    if et == "reconciliation.incident":
        summary = str(payload.get("summary") or payload.get("message") or "incident")
        return tr("sys.notify.toast.recon"), summary

    if et == "phantom.fills":
        action = str(payload.get("action") or "?").upper()
        ticker = str(payload.get("ticker") or "?")
        return tr("sys.notify.toast.phantom"), f"{action} {ticker}".strip()

    return None


def _line(title: str, body: str) -> str:
    return f"{title}: {body}" if body else title


class TradeNotifyWatcher:
    """Watch snapshot notify feed and emit toasts according to NotifySettings."""

    def __init__(
        self,
        emit: EmitFn,
        *,
        settings: NotifySettings | None = None,
    ) -> None:
        self.emit = emit
        self.settings = settings or NotifySettings()
        self._lock = threading.RLock()
        # Ordered fingerprints — trim must drop oldest, not an arbitrary set slice.
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._primed = False
        self._last_emit_ts = 0.0
        self.tray_mode = False
        self._suppressed = False
        self._buffer: list[tuple[str, str]] = []
        self._buffer_started = 0.0
        self._pending: list[tuple[str, str, bool]] = []

    def configure(self, settings: NotifySettings) -> None:
        with self._lock:
            self.settings = settings
            self._discard_queues()

    def reset(self) -> None:
        with self._lock:
            self._seen.clear()
            self._primed = False
            self._last_emit_ts = 0.0
            self._suppressed = False
            self._discard_queues()

    def suppress(self) -> None:
        """Hard-stop notifications (UI quit). Drops any queued toasts."""
        with self._lock:
            self._suppressed = True
            self._discard_queues()

    def _discard_queues(self) -> None:
        self._buffer.clear()
        self._buffer_started = 0.0
        self._pending.clear()

    def _remember(self, fp: str) -> None:
        if fp in self._seen:
            self._seen.move_to_end(fp)
        else:
            self._seen[fp] = None
        if len(self._seen) > _SEEN_MAX:
            while len(self._seen) > _SEEN_KEEP:
                self._seen.popitem(last=False)

    def acknowledge_snapshot(self, snap: dict[str, Any] | None) -> None:
        """Mark current notify feed as seen without emitting (mode transitions)."""
        if not isinstance(snap, dict):
            return
        recent = snap.get("recent_notify")
        if not isinstance(recent, list):
            recent = snap.get("recent")
        if not isinstance(recent, list):
            return
        with self._lock:
            for item in recent:
                if isinstance(item, dict):
                    self._remember(_fingerprint(item))
            self._primed = True
            self._discard_queues()

    def set_tray_mode(self, active: bool) -> None:
        with self._lock:
            was = self.tray_mode
            self.tray_mode = bool(active)
            if self.tray_mode == was:
                return
            # Never deliver across UI↔tray boundary — backlog while the other
            # mode was active must not flush as a toast storm.
            self._discard_queues()

    def _channel_allowed(self) -> bool:
        if self._suppressed:
            return False
        cfg = self.settings
        if not cfg.enabled:
            return False
        if self.tray_mode:
            return bool(cfg.when_in_tray)
        return bool(cfg.when_ui_open)

    def _event_enabled(self, et: str) -> bool:
        return bool(self.settings.events.get(et, False))

    def _is_urgent(self, et: str) -> bool:
        if not self.settings.urgent_bypass_batch:
            return False
        return et in self.settings.urgent_events

    def _emit_now(
        self,
        title: str,
        body: str,
        *,
        ignore_gap: bool = False,
        urgent: bool = False,
    ) -> bool:
        now = time.time()
        gap = float(self.settings.min_interval_sec or 0.0)
        if not ignore_gap and gap > 0 and now - self._last_emit_ts < gap:
            return False
        try:
            result = self.emit(title, body, urgent=urgent)
        except TypeError:
            try:
                result = self.emit(title, body)
            except Exception:
                return False
        except Exception:
            return False
        if result is False:
            return False
        self._last_emit_ts = now
        return True

    def _queue_pending(self, title: str, body: str, *, urgent: bool = False) -> None:
        self._pending.append((title, body, urgent))
        max_pending = max(8, int(self.settings.batch_max_lines or 8) * 3)
        if len(self._pending) > max_pending:
            self._pending = self._pending[-max_pending:]

    def _drain_pending(self) -> int:
        if not self._pending:
            return 0
        if not self._channel_allowed():
            # Do not keep toasts for a later mode — that causes close/open storms.
            self._discard_queues()
            return 0
        n = 0
        while self._pending:
            title, body, urgent = self._pending[0]
            if not self._emit_now(title, body, urgent=urgent):
                break
            self._pending.pop(0)
            n += 1
        return n

    def _enqueue(self, title: str, body: str, *, urgent: bool = False) -> None:
        if not self._channel_allowed():
            return
        if urgent or not self.settings.batch_enabled:
            if self._emit_now(title, body, ignore_gap=urgent, urgent=urgent):
                return
            self._queue_pending(title, body, urgent=urgent)
            return
        if not self._buffer:
            self._buffer_started = time.time()
        self._buffer.append((title, body))
        self._maybe_flush(force=False)

    def _maybe_flush(self, *, force: bool) -> int:
        if not self._buffer:
            return 0
        if not self._channel_allowed():
            self._discard_queues()
            return 0
        max_lines = int(self.settings.batch_max_lines or 8)
        window = float(self.settings.batch_window_sec or 8.0)
        aged = (time.time() - self._buffer_started) >= window
        full = len(self._buffer) >= max_lines
        if not (force or aged or full):
            return 0
        chunk = self._buffer[:max_lines]
        leftover = self._buffer[max_lines:]
        if len(chunk) == 1:
            title, body = chunk[0]
        else:
            title = tr("sys.notify.toast_batch", n=len(chunk))
            body = "\n".join(_line(t, b) for t, b in chunk)
        if self._emit_now(title, body):
            self._buffer = leftover
            self._buffer_started = time.time() if leftover else 0.0
            return 1
        return 0

    def tick(self) -> int:
        """Call from the UI frame loop so batch windows / pending drain on time."""
        with self._lock:
            if self._suppressed:
                self._discard_queues()
                return 0
            if not self._channel_allowed():
                # Drop anything that cannot be delivered in this mode.
                self._discard_queues()
                return 0
            n = self._drain_pending()
            if self.settings.batch_enabled:
                n += self._maybe_flush(force=False)
                max_lines = int(self.settings.batch_max_lines or 8)
                while self._buffer and len(self._buffer) >= max_lines:
                    got = self._maybe_flush(force=True)
                    if not got:
                        break
                    n += got
            return n

    def process_snapshot(self, snap: dict[str, Any] | None) -> int:
        if not isinstance(snap, dict):
            return 0
        # Prefer dedicated notify feed (not flooded by candles).
        recent = snap.get("recent_notify")
        if not isinstance(recent, list):
            recent = snap.get("recent")
        if not isinstance(recent, list):
            return 0
        items = [x for x in recent if isinstance(x, dict)]
        items_chrono = list(reversed(items))

        with self._lock:
            if self._suppressed:
                for item in items_chrono:
                    self._remember(_fingerprint(item))
                self._primed = True
                self._discard_queues()
                return 0

            if not self._primed:
                for item in items_chrono:
                    self._remember(_fingerprint(item))
                self._primed = True
                self._discard_queues()
                return 0

            channel_ok = self._channel_allowed()
            if not channel_ok:
                # Mark seen so a later tray/open transition cannot replay them.
                for item in items_chrono:
                    self._remember(_fingerprint(item))
                self._discard_queues()
                return 0

            queued = 0
            for item in items_chrono:
                fp = _fingerprint(item)
                if fp in self._seen:
                    continue
                self._remember(fp)
                et = str(item.get("type") or "")
                if not self._event_enabled(et):
                    continue
                if not item_passes_filters(item, self.settings):
                    continue
                formatted = format_trade_notification(item)
                if formatted is None:
                    continue
                title, body = formatted
                urgent = self._is_urgent(et)
                if urgent and self.settings.batch_enabled and self._buffer:
                    self._maybe_flush(force=True)
                self._enqueue(title, body, urgent=urgent)
                queued += 1

            queued += self._drain_pending()
            queued += self._maybe_flush(force=False)
            return queued
