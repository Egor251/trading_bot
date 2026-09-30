"""Persisted Operator notification preferences (no GUI)."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
_PATH = ROOT / "data" / "ui" / "notifications.json"


def parse_csv_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw or "").replace(";", ",")
    return [p.strip() for p in text.split(",") if p.strip()]


EVENT_CATALOG: tuple[tuple[str, str], ...] = (
    ("execution.result", "sys.notify.ev.execution"),
    ("risk.reject", "sys.notify.ev.risk_reject"),
    ("ensemble.decision", "sys.notify.ev.ensemble_decision"),
    ("ensemble.reject", "sys.notify.ev.ensemble_reject"),
    ("ops.alert", "sys.notify.ev.ops_alert"),
    ("system.halted", "sys.notify.ev.halted"),
    ("system.resumed", "sys.notify.ev.resumed"),
    ("reconciliation.incident", "sys.notify.ev.recon"),
    ("phantom.fills", "sys.notify.ev.phantom"),
)

_DEFAULT_EVENTS: dict[str, bool] = {
    "execution.result": True,
    "risk.reject": True,
    "ensemble.decision": False,
    "ensemble.reject": False,
    "ops.alert": False,
    "system.halted": True,
    "system.resumed": False,
    "reconciliation.incident": True,
    "phantom.fills": False,
}

_DEFAULT_URGENT: tuple[str, ...] = (
    "system.halted",
    "risk.reject",
    "reconciliation.incident",
)


@dataclass
class NotifySettings:
    enabled: bool = True
    when_ui_open: bool = False
    when_in_tray: bool = True
    show_tray_hint: bool = True
    channel_windows: bool = True
    channel_telegram: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    sound_enabled: bool = True
    sound_urgent_only: bool = False
    toast_duration_sec: float = 5.0
    batch_enabled: bool = True
    batch_window_sec: float = 8.0
    batch_max_lines: int = 8
    min_interval_sec: float = 2.5
    urgent_bypass_batch: bool = True
    urgent_events: list[str] = field(default_factory=lambda: list(_DEFAULT_URGENT))
    ticker_include: list[str] = field(default_factory=list)
    ticker_exclude: list[str] = field(default_factory=list)
    strategy_include: list[str] = field(default_factory=list)
    strategy_exclude: list[str] = field(default_factory=list)
    events: dict[str, bool] = field(default_factory=lambda: dict(_DEFAULT_EVENTS))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "NotifySettings":
        data = dict(raw or {})
        events = dict(_DEFAULT_EVENTS)
        incoming = data.get("events")
        if isinstance(incoming, dict):
            for k, v in incoming.items():
                events[str(k)] = bool(v)
        urgent = list(_DEFAULT_URGENT)
        raw_urgent = data.get("urgent_events")
        if isinstance(raw_urgent, list):
            urgent = [str(x) for x in raw_urgent if str(x)]
        return cls(
            enabled=bool(data.get("enabled", True)),
            when_ui_open=bool(data.get("when_ui_open", False)),
            when_in_tray=bool(data.get("when_in_tray", True)),
            show_tray_hint=bool(data.get("show_tray_hint", True)),
            channel_windows=bool(data.get("channel_windows", True)),
            channel_telegram=bool(data.get("channel_telegram", False)),
            telegram_bot_token=str(data.get("telegram_bot_token") or ""),
            telegram_chat_id=str(data.get("telegram_chat_id") or ""),
            sound_enabled=bool(data.get("sound_enabled", True)),
            sound_urgent_only=bool(data.get("sound_urgent_only", False)),
            toast_duration_sec=max(
                1.0, min(30.0, float(data.get("toast_duration_sec") or 5.0))
            ),
            batch_enabled=bool(data.get("batch_enabled", True)),
            batch_window_sec=max(0.5, float(data.get("batch_window_sec") or 8.0)),
            batch_max_lines=max(1, min(30, int(data.get("batch_max_lines") or 8))),
            min_interval_sec=max(0.0, float(data.get("min_interval_sec") or 2.5)),
            urgent_bypass_batch=bool(data.get("urgent_bypass_batch", True)),
            urgent_events=urgent,
            ticker_include=parse_csv_list(data.get("ticker_include")),
            ticker_exclude=parse_csv_list(data.get("ticker_exclude")),
            strategy_include=parse_csv_list(data.get("strategy_include")),
            strategy_exclude=parse_csv_list(data.get("strategy_exclude")),
            events=events,
        )


_settings = NotifySettings()
_on_apply: Callable[[NotifySettings], None] | None = None
_on_test: Callable[[], Any] | None = None


def get_settings() -> NotifySettings:
    return deepcopy(_settings)


def set_on_apply(cb: Callable[[NotifySettings], None] | None) -> None:
    global _on_apply
    _on_apply = cb


def set_on_test(cb: Callable[[], Any] | None) -> None:
    global _on_test
    _on_test = cb


def load() -> NotifySettings:
    global _settings
    if _PATH.is_file():
        try:
            raw = json.loads(_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                _settings = NotifySettings.from_dict(raw)
                return deepcopy(_settings)
        except Exception:
            pass
    _settings = NotifySettings()
    return deepcopy(_settings)


def save(settings: NotifySettings | None = None) -> NotifySettings:
    global _settings
    cfg = settings or _settings
    _settings = NotifySettings.from_dict(cfg.to_dict())
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(
        json.dumps(_settings.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return deepcopy(_settings)
