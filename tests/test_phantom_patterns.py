"""Synthetic phantom patterns + state store (todo2 continuation)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.ops_safety import AlertBus, AlertSeverity, TelegramAlertSink  # noqa: E402
from engine.phantom_patterns import (  # noqa: E402
    MarketPattern,
    PatternSpec,
    default_pattern_specs,
    generate_pattern_candles,
    list_patterns,
)
from engine.state_store import StateStore  # noqa: E402


def test_pattern_catalog_and_shapes() -> None:
    assert "sharp_rise" in list_patterns()
    assert "sharp_drop" in list_patterns()
    assert "rebound" in list_patterns()
    specs = default_pattern_specs(bars=40, base_seed=7)
    assert len(specs) >= 6
    candles = generate_pattern_candles(
        "BTC/USDT",
        PatternSpec(kind=MarketPattern.SHARP_RISE, bars=40, seed=1, start_price=100),
        class_code="CRYPTO",
    )
    assert len(candles) == 40
    assert candles[-1].close > candles[0].close * 1.02

    dump = generate_pattern_candles(
        "BTC/USDT",
        PatternSpec(kind="sharp_drop", bars=40, seed=2, start_price=100),
    )
    assert dump[-1].close < dump[0].close * 0.98

    rebound = generate_pattern_candles(
        "ETH/USDT",
        PatternSpec(kind=MarketPattern.REBOUND, bars=50, seed=3, start_price=200),
    )
    mid = rebound[len(rebound) // 2].close
    assert mid < rebound[0].close
    assert rebound[-1].close > mid


def test_pattern_deterministic_seed() -> None:
    a = generate_pattern_candles(
        "X", PatternSpec(kind="chop", bars=30, seed=123, start_price=50)
    )
    b = generate_pattern_candles(
        "X", PatternSpec(kind="chop", bars=30, seed=123, start_price=50)
    )
    assert [c.close for c in a] == [c.close for c in b]


def test_state_store_roundtrip(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    store.save({"equity": 12345.0, "mode": "phantom"}, label="t")
    loaded = store.load()
    assert loaded == {"equity": 12345.0, "mode": "phantom"}
    meta = store.meta()
    assert meta and meta["label"] == "t"


def test_telegram_alert_sink_records() -> None:
    bus = AlertBus()
    sink = TelegramAlertSink(bot_token="", chat_id="")
    bus.sink = sink
    bus.emit("stop_loss", "SL hit", severity=AlertSeverity.WARNING)
    assert len(sink.sent) == 1
    assert "SL hit" in sink.format_message(sink.sent[0])
