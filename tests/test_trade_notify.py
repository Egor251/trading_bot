"""Toast formatting / settings / batching for Operator notifications."""

from __future__ import annotations

import time

from ui.notify_settings import NotifySettings
from ui.trade_notify import TradeNotifyWatcher, format_trade_notification
from ui.i18n import set_lang


def _exec(t: float, action: str, ticker: str) -> dict:
    return {
        "t": t,
        "type": "execution.result",
        "payload": {
            "success": True,
            "order": {
                "action": action,
                "ticker": ticker,
                "quantity": 1,
                "status": "filled",
            },
        },
    }


def test_format_execution_ok() -> None:
    set_lang("ru", persist=False)
    title, body = format_trade_notification(_exec(1, "BUY", "SBER"))
    assert "Сделка" in title or "Fill" in title
    assert "BUY" in body and "SBER" in body


def test_format_titles_follow_locale() -> None:
    set_lang("en", persist=False)
    title, _body = format_trade_notification(_exec(1, "BUY", "AAPL"))
    assert title == "Fill"
    set_lang("ru", persist=False)
    title_ru, _ = format_trade_notification(_exec(1, "BUY", "AAPL"))
    assert title_ru == "Сделка"


def test_format_ops_keeps_title_body_split() -> None:
    set_lang("en", persist=False)
    title, body = format_trade_notification(
        {
            "type": "ops.alert",
            "payload": {"kind": "latency", "summary": "p99 high"},
        }
    )
    assert title.startswith("Ops:")
    assert body == "p99 high"


def test_watcher_silent_when_ui_open_by_default() -> None:
    out: list[tuple[str, str]] = []
    cfg = NotifySettings(when_ui_open=False, when_in_tray=True, batch_enabled=False)
    w = TradeNotifyWatcher(lambda t, b: out.append((t, b)), settings=cfg)
    w.process_snapshot({"recent_notify": []})
    w.process_snapshot({"recent_notify": [_exec(2, "SELL", "B")]})
    assert out == []


def test_no_toast_storm_when_closing_ui_to_tray() -> None:
    """Events during open UI must not flush as a storm on hide-to-tray."""
    out: list[tuple[str, str]] = []
    cfg = NotifySettings(
        when_ui_open=False,
        when_in_tray=True,
        batch_enabled=False,
        min_interval_sec=0.0,
        urgent_bypass_batch=False,
    )
    w = TradeNotifyWatcher(lambda t, b, urgent=False: out.append((t, b)), settings=cfg)
    backlog = [_exec(i, "BUY", f"T{i}") for i in range(1, 16)]
    w.process_snapshot({"recent_notify": []})
    w.process_snapshot({"recent_notify": list(reversed(backlog))})
    assert out == []
    assert w._pending == []
    assert w._buffer == []

    w.set_tray_mode(True)
    w.acknowledge_snapshot({"recent_notify": list(reversed(backlog))})
    assert out == []
    assert w._pending == []
    assert w._buffer == []

    # Same backlog on next snapshot must stay silent; only brand-new events notify.
    w.process_snapshot({"recent_notify": list(reversed(backlog))})
    assert out == []
    w.process_snapshot(
        {"recent_notify": [_exec(99, "SELL", "NEW")] + list(reversed(backlog))}
    )
    assert len(out) == 1
    assert "NEW" in out[0][1]


def test_channel_off_discards_queues_not_defers() -> None:
    out: list[tuple[str, str]] = []
    cfg = NotifySettings(
        when_ui_open=True,
        when_in_tray=True,
        batch_enabled=False,
        min_interval_sec=999.0,
        urgent_bypass_batch=False,
    )
    w = TradeNotifyWatcher(lambda t, b, urgent=False: out.append((t, b)), settings=cfg)
    w.process_snapshot({"recent_notify": []})
    w.process_snapshot(
        {
            "recent_notify": [
                _exec(2, "SELL", "B"),
                _exec(1, "BUY", "A"),
            ]
        }
    )
    assert len(out) == 1
    assert len(w._pending) == 1
    # Switch to "UI open but notifications off for this mode".
    w.settings = NotifySettings(
        when_ui_open=False,
        when_in_tray=True,
        batch_enabled=False,
        min_interval_sec=0.0,
    )
    assert w.tick() == 0
    assert w._pending == []
    assert out == out[:1]


def test_suppress_blocks_all() -> None:
    out: list[tuple[str, str]] = []
    cfg = NotifySettings(
        when_ui_open=True,
        when_in_tray=True,
        batch_enabled=False,
        min_interval_sec=0.0,
    )
    w = TradeNotifyWatcher(lambda t, b, urgent=False: out.append((t, b)), settings=cfg)
    w.process_snapshot({"recent_notify": []})
    w.suppress()
    w.process_snapshot({"recent_notify": [_exec(1, "BUY", "X")]})
    assert out == []
    assert w.tick() == 0


def test_watcher_emits_when_ui_open_enabled() -> None:
    out: list[tuple[str, str]] = []
    cfg = NotifySettings(
        when_ui_open=True,
        when_in_tray=True,
        batch_enabled=False,
        min_interval_sec=0.0,
        urgent_bypass_batch=False,
    )
    w = TradeNotifyWatcher(lambda t, b: out.append((t, b)), settings=cfg)
    w.set_tray_mode(False)
    w.process_snapshot({"recent_notify": []})
    w.process_snapshot({"recent_notify": [_exec(3, "BUY", "A")]})
    assert out and "BUY" in out[0][1]


def test_watcher_emits_in_tray_mode() -> None:
    out: list[tuple[str, str]] = []
    cfg = NotifySettings(
        when_ui_open=False,
        when_in_tray=True,
        batch_enabled=False,
        min_interval_sec=0.0,
        urgent_bypass_batch=False,
    )
    w = TradeNotifyWatcher(lambda t, b: out.append((t, b)), settings=cfg)
    w.set_tray_mode(True)
    w.process_snapshot({"recent_notify": []})
    w.process_snapshot({"recent_notify": [_exec(12, "SELL", "B")]})
    assert out and out[0][1].startswith("SELL")


def test_prefers_recent_notify_over_recent() -> None:
    out: list[tuple[str, str]] = []
    cfg = NotifySettings(
        when_in_tray=True,
        batch_enabled=False,
        min_interval_sec=0.0,
        urgent_bypass_batch=False,
    )
    w = TradeNotifyWatcher(lambda t, b: out.append((t, b)), settings=cfg)
    w.set_tray_mode(True)
    w.process_snapshot({"recent_notify": [], "recent": []})
    w.process_snapshot(
        {
            "recent": [_exec(99, "BUY", "IGNORE")],
            "recent_notify": [_exec(100, "SELL", "KEEP")],
        }
    )
    assert len(out) == 1
    assert "KEEP" in out[0][1]
    assert "IGNORE" not in out[0][1]


def test_batch_flush_by_max_lines_fifo() -> None:
    out: list[tuple[str, str]] = []
    cfg = NotifySettings(
        when_in_tray=True,
        batch_enabled=True,
        batch_window_sec=60.0,
        batch_max_lines=2,
        min_interval_sec=0.0,
        urgent_bypass_batch=False,
    )
    w = TradeNotifyWatcher(lambda t, b: out.append((t, b)), settings=cfg)
    w.set_tray_mode(True)
    w.process_snapshot({"recent_notify": []})
    w.process_snapshot(
        {
            "recent_notify": [
                _exec(102, "SELL", "T2"),
                _exec(101, "BUY", "T1"),
            ]
        }
    )
    assert len(out) == 1
    body = out[0][1]
    assert body.index("BUY") < body.index("SELL")


def test_batch_flush_by_time_window() -> None:
    out: list[tuple[str, str]] = []
    cfg = NotifySettings(
        when_in_tray=True,
        batch_enabled=True,
        batch_window_sec=0.05,
        batch_max_lines=8,
        min_interval_sec=0.0,
        urgent_bypass_batch=False,
    )
    w = TradeNotifyWatcher(lambda t, b: out.append((t, b)), settings=cfg)
    w.set_tray_mode(True)
    w.process_snapshot({"recent_notify": []})
    w.process_snapshot({"recent_notify": [_exec(1, "BUY", "A")]})
    assert out == []
    time.sleep(0.06)
    assert w.tick() == 1
    assert out and "BUY" in out[0][1]


def test_rate_limit_queues_instead_of_dropping() -> None:
    out: list[tuple[str, str]] = []
    cfg = NotifySettings(
        when_in_tray=True,
        batch_enabled=False,
        min_interval_sec=999.0,
        urgent_bypass_batch=False,
    )
    w = TradeNotifyWatcher(lambda t, b: out.append((t, b)), settings=cfg)
    w.set_tray_mode(True)
    w.process_snapshot({"recent_notify": []})
    w.process_snapshot(
        {
            "recent_notify": [
                _exec(20, "SELL", "B"),
                _exec(19, "BUY", "A"),
            ]
        }
    )
    assert len(out) == 1
    assert len(w._pending) == 1
    w._last_emit_ts = 0.0
    assert w.tick() == 1
    assert len(out) == 2
    assert w._pending == []


def test_emit_false_keeps_pending() -> None:
    out: list[tuple[str, str]] = []

    def emit(title: str, body: str) -> bool:
        if len(out) >= 1:
            return False
        out.append((title, body))
        return True

    cfg = NotifySettings(
        when_in_tray=True,
        batch_enabled=False,
        min_interval_sec=0.0,
        urgent_bypass_batch=False,
    )
    w = TradeNotifyWatcher(emit, settings=cfg)
    w.set_tray_mode(True)
    w.process_snapshot({"recent_notify": []})
    w.process_snapshot(
        {
            "recent_notify": [
                _exec(2, "SELL", "B"),
                _exec(1, "BUY", "A"),
            ]
        }
    )
    assert len(out) == 1
    assert len(w._pending) == 1


def test_urgent_bypasses_batch() -> None:
    out: list[tuple[str, str]] = []
    set_lang("ru", persist=False)
    cfg = NotifySettings(
        when_in_tray=True,
        batch_enabled=True,
        batch_window_sec=60.0,
        batch_max_lines=8,
        min_interval_sec=0.0,
        urgent_bypass_batch=True,
        urgent_events=["system.halted"],
    )
    w = TradeNotifyWatcher(lambda t, b: out.append((t, b)), settings=cfg)
    w.set_tray_mode(True)
    w.process_snapshot({"recent_notify": []})
    w.process_snapshot(
        {
            "recent_notify": [
                {
                    "t": 50,
                    "type": "system.halted",
                    "payload": {"reason": "kill"},
                },
                _exec(49, "BUY", "X"),
            ]
        }
    )
    assert any("остановлена" in t for t, _ in out)


def test_configure_clears_buffers() -> None:
    out: list[tuple[str, str]] = []
    cfg = NotifySettings(
        when_in_tray=True,
        batch_enabled=True,
        batch_window_sec=60.0,
        batch_max_lines=8,
        min_interval_sec=0.0,
        urgent_bypass_batch=False,
    )
    w = TradeNotifyWatcher(lambda t, b: out.append((t, b)), settings=cfg)
    w.set_tray_mode(True)
    w.process_snapshot({"recent_notify": []})
    w.process_snapshot({"recent_notify": [_exec(1, "BUY", "A")]})
    assert w._buffer
    w.configure(cfg)
    assert w._buffer == []
    assert w._pending == []


def test_event_toggle_disables_type() -> None:
    out: list[tuple[str, str]] = []
    cfg = NotifySettings(
        when_in_tray=True,
        batch_enabled=False,
        min_interval_sec=0.0,
        events={"execution.result": False, "risk.reject": True},
    )
    w = TradeNotifyWatcher(lambda t, b: out.append((t, b)), settings=cfg)
    w.set_tray_mode(True)
    w.process_snapshot({"recent_notify": []})
    w.process_snapshot({"recent_notify": [_exec(5, "BUY", "X")]})
    assert out == []


def test_settings_roundtrip(tmp_path, monkeypatch) -> None:
    from ui import notify_settings as ns

    monkeypatch.setattr(ns, "_PATH", tmp_path / "notifications.json")
    cfg = NotifySettings(
        enabled=True,
        batch_enabled=False,
        batch_window_sec=3.5,
        show_tray_hint=False,
        urgent_bypass_batch=True,
        urgent_events=["system.halted"],
        channel_telegram=True,
        telegram_bot_token="tok",
        telegram_chat_id="123",
        sound_enabled=False,
        sound_urgent_only=True,
        toast_duration_sec=7.0,
        ticker_include=["SBER", "GAZP"],
        strategy_exclude=["noise"],
        events={"execution.result": True, "ops.alert": True},
    )
    ns.save(cfg)
    loaded = ns.load()
    assert loaded.batch_enabled is False
    assert abs(loaded.batch_window_sec - 3.5) < 1e-6
    assert loaded.events["ops.alert"] is True
    assert loaded.show_tray_hint is False
    assert loaded.urgent_events == ["system.halted"]
    assert loaded.channel_telegram is True
    assert loaded.telegram_chat_id == "123"
    assert loaded.sound_enabled is False
    assert loaded.ticker_include == ["SBER", "GAZP"]
    assert loaded.strategy_exclude == ["noise"]
    assert abs(loaded.toast_duration_sec - 7.0) < 1e-6


def test_ticker_include_filter() -> None:
    out: list[tuple[str, str]] = []
    cfg = NotifySettings(
        when_in_tray=True,
        batch_enabled=False,
        min_interval_sec=0.0,
        urgent_bypass_batch=False,
        ticker_include=["SBER"],
    )
    w = TradeNotifyWatcher(lambda t, b: out.append((t, b)), settings=cfg)
    w.set_tray_mode(True)
    w.process_snapshot({"recent_notify": []})
    w.process_snapshot(
        {
            "recent_notify": [
                _exec(2, "BUY", "GAZP"),
                _exec(1, "BUY", "SBER"),
            ]
        }
    )
    assert len(out) == 1
    assert "SBER" in out[0][1]


def test_ticker_exclude_filter() -> None:
    out: list[tuple[str, str]] = []
    cfg = NotifySettings(
        when_in_tray=True,
        batch_enabled=False,
        min_interval_sec=0.0,
        urgent_bypass_batch=False,
        ticker_exclude=["GAZP"],
    )
    w = TradeNotifyWatcher(lambda t, b: out.append((t, b)), settings=cfg)
    w.set_tray_mode(True)
    w.process_snapshot({"recent_notify": []})
    w.process_snapshot(
        {
            "recent_notify": [
                _exec(2, "BUY", "GAZP"),
                _exec(1, "BUY", "SBER"),
            ]
        }
    )
    assert len(out) == 1
    assert "SBER" in out[0][1]


def test_halt_passes_ticker_include() -> None:
    out: list[tuple[str, str]] = []
    set_lang("ru", persist=False)
    cfg = NotifySettings(
        when_in_tray=True,
        batch_enabled=False,
        min_interval_sec=0.0,
        ticker_include=["SBER"],
        events={"system.halted": True, "execution.result": True},
        urgent_bypass_batch=False,
    )
    w = TradeNotifyWatcher(lambda t, b: out.append((t, b)), settings=cfg)
    w.set_tray_mode(True)
    w.process_snapshot({"recent_notify": []})
    w.process_snapshot(
        {
            "recent_notify": [
                {"t": 1, "type": "system.halted", "payload": {"reason": "x"}},
            ]
        }
    )
    assert out and "остановлена" in out[0][0]


def test_dispatch_telegram_only(monkeypatch) -> None:
    from ui import notify_dispatch as nd

    sent: list[tuple[str, str]] = []

    def fake_tg(**kwargs):
        sent.append((kwargs["title"], kwargs["body"]))
        return True

    monkeypatch.setattr(nd, "send_telegram_async", lambda **kw: fake_tg(**kw))
    monkeypatch.setattr(nd, "play_notify_sound", lambda **kw: None)
    cfg = NotifySettings(
        channel_windows=False,
        channel_telegram=True,
        telegram_bot_token="t",
        telegram_chat_id="1",
        sound_enabled=False,
    )
    ok = nd.dispatch("Hi", "there", cfg, tray_notify=None, win_notify=None)
    assert ok is True
    assert sent == [("Hi", "there")]


def test_notify_events_constant() -> None:
    from services.ui_bridge_service import _NOTIFY_EVENTS

    assert "execution.result" in _NOTIFY_EVENTS
    assert "market.candle" not in _NOTIFY_EVENTS
