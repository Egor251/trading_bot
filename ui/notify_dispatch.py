"""Deliver Operator notifications to Windows toast / sound / Telegram."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from ui.notify_settings import NotifySettings


def play_notify_sound(*, urgent: bool = False) -> None:
    """Best-effort system beep (Windows)."""
    try:
        import winsound

        # Asterisk for normal, Hand for urgent-ish.
        flag = winsound.MB_ICONHAND if urgent else winsound.MB_ICONASTERISK
        winsound.MessageBeep(flag)
    except Exception:
        pass


def send_telegram(
    *,
    bot_token: str,
    chat_id: str,
    title: str,
    body: str,
    timeout: float = 5.0,
) -> bool:
    token = str(bot_token or "").strip()
    chat = str(chat_id or "").strip()
    if not token or not chat:
        return False
    text = f"{title}\n{body}".strip()
    if len(text) > 3500:
        text = text[:3490] + "…"
    try:
        import requests

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": chat, "text": text, "disable_web_page_preview": True},
            timeout=timeout,
        )
        return bool(resp.ok)
    except Exception:
        return False


def send_telegram_async(
    *,
    bot_token: str,
    chat_id: str,
    title: str,
    body: str,
    on_done: Callable[[bool], None] | None = None,
) -> None:
    def _run() -> None:
        ok = send_telegram(
            bot_token=bot_token, chat_id=chat_id, title=title, body=body
        )
        if on_done is not None:
            try:
                on_done(ok)
            except Exception:
                pass

    threading.Thread(target=_run, name="notify-telegram", daemon=True).start()


def dispatch(
    title: str,
    body: str,
    settings: "NotifySettings",
    *,
    tray_notify: Callable[[str, str], bool] | None = None,
    win_notify: Callable[..., bool] | None = None,
    urgent: bool = False,
    force_windows: bool = False,
) -> bool:
    """Fan-out to configured channels. Returns True if any local channel accepted."""
    ok = False
    if settings.channel_windows or force_windows:
        duration_ms = int(max(1.0, float(settings.toast_duration_sec or 5.0)) * 1000)
        if tray_notify is not None:
            try:
                if tray_notify(title, body):
                    ok = True
            except Exception:
                pass
        if not ok and win_notify is not None:
            try:
                if win_notify(title, body, timeout_ms=duration_ms):
                    ok = True
            except TypeError:
                try:
                    if win_notify(title, body):
                        ok = True
                except Exception:
                    pass
            except Exception:
                pass

    if settings.sound_enabled and (not settings.sound_urgent_only or urgent):
        play_notify_sound(urgent=urgent)

    if settings.channel_telegram and settings.telegram_bot_token and settings.telegram_chat_id:
        send_telegram_async(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            title=title,
            body=body,
        )
        # Async — count as attempted delivery for UX when Windows channel off.
        if not settings.channel_windows and not force_windows:
            ok = True

    return ok


def item_passes_filters(item: dict[str, Any], settings: "NotifySettings") -> bool:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    order = payload.get("order") if isinstance(payload.get("order"), dict) else {}
    ticker = str(payload.get("ticker") or order.get("ticker") or "").strip().upper()
    strategy = str(
        payload.get("strategy_id") or order.get("strategy_id") or ""
    ).strip().lower()

    inc_t = {x.upper() for x in settings.ticker_include}
    exc_t = {x.upper() for x in settings.ticker_exclude}
    if ticker:
        if inc_t and ticker not in inc_t:
            return False
        if ticker in exc_t:
            return False

    inc_s = {x.lower() for x in settings.strategy_include}
    exc_s = {x.lower() for x in settings.strategy_exclude}
    if strategy:
        if inc_s and strategy not in inc_s:
            return False
        if strategy in exc_s:
            return False
    elif inc_s:
        # Strategy filter active but event has no strategy — allow system-level events.
        pass
    return True
