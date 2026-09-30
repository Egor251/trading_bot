"""Tests for docs/todo2.md critical layers."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.insurance import (  # noqa: E402
    InsuranceGuard,
    limits_from_preset,
    profile_from_preset,
    resolve_preset,
)
from engine.ops_safety import (  # noqa: E402
    AlertBus,
    DataGapDetector,
    HeartbeatWatchdog,
    TradeJournal,
)
from engine.order import OrderStatus  # noqa: E402
from engine.performance_monitor import DiagnosisKind, PerformanceMonitor  # noqa: E402
from engine.phantom_timeline import PhantomTimeline  # noqa: E402
from engine.position_book import PositionBook  # noqa: E402
from engine.protection import PositionSide, PositionSlice  # noqa: E402
from engine.protective_runtime import ProtectiveLayer  # noqa: E402
from engine.risk import RiskManager  # noqa: E402
from engine.runtime import TradingRuntime  # noqa: E402


def test_insurance_presets_map_daily_and_stops() -> None:
    moderate = resolve_preset("moderate")
    assert moderate.daily_loss_pct == 0.05
    limits = limits_from_preset("conservative")
    assert limits.max_daily_loss_pct is None  # enforced by InsuranceGuard
    assert limits.max_portfolio_drawdown == 0.06
    profile = profile_from_preset("aggressive", archetype="trend")
    assert float(profile.max_loss_fraction) == 0.03
    assert profile.max_holding_period == timedelta(hours=48)


def test_insurance_guard_halts_on_daily_loss() -> None:
    risk = RiskManager(limits_from_preset("moderate"))
    guard = InsuranceGuard(
        risk, daily_loss_pct=0.05, day_start_equity=10_000.0, peak_equity=10_000.0
    )
    ok = guard.check(9_800.0)
    assert ok.allowed
    bad = guard.check(9_400.0)  # -6% > 5%
    assert not bad.allowed
    assert risk.halted


def test_position_book_net_visibility_and_no_flip() -> None:
    layer = ProtectiveLayer()
    layer.open_slice(
        slice_id="a1",
        strategy_id="strat_a",
        symbol="BTC/USDT",
        side="buy",
        quantity=1,
        entry_price=100,
        opened_at=datetime.now(UTC),
    )
    book = PositionBook(layer.ledger, forbid_net_flip=True)
    view = book.view("strat_b", "BTC/USDT")
    assert float(view.net_qty) == 1.0
    assert float(view.strategy_qty) == 0.0

    # Reducing short-side against long net is OK (Approach B)
    reduce = book.evaluate_intent(
        strategy_id="strat_b",
        symbol="BTC/USDT",
        side="sell",
        quantity=0.4,
    )
    assert reduce.allowed

    # Flipping through zero without close_and_reverse is blocked
    flip = book.evaluate_intent(
        strategy_id="strat_b",
        symbol="BTC/USDT",
        side="sell",
        quantity=1.5,
    )
    assert not flip.allowed
    assert flip.reason == "net_flip_forbidden"
    assert flip.would_flip

    allowed = book.evaluate_intent(
        strategy_id="strat_b",
        symbol="BTC/USDT",
        side="sell",
        quantity=1.5,
        close_and_reverse=True,
    )
    assert allowed.allowed


def test_runtime_rejects_net_flip_via_gate() -> None:
    runtime = TradingRuntime.from_config(
        {
            "equity": 100_000,
            "mode": "paper",
            "insurance": {"preset": "moderate"},
            "position_policy": {"forbid_net_flip": True},
            "risk": {"max_position_pct": 1.0, "max_leverage": 5.0},
        }
    )
    runtime.candles.ingest_payload(
        {
            "ticker": "BTC/USDT",
            "class_code": "CRYPTO",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 1,
            "ts": 1_700_000_000,
        }
    )
    first = runtime.submit_signal(
        ticker="BTC/USDT",
        action="BUY",
        quantity=1,
        strategy_id="strat_a",
        class_code="CRYPTO",
        mark_price=100,
    )
    assert first.status == OrderStatus.FILLED
    flip = runtime.submit_signal(
        ticker="BTC/USDT",
        action="SELL",
        quantity=2,
        strategy_id="strat_b",
        class_code="CRYPTO",
        mark_price=100,
    )
    assert flip.status == OrderStatus.REJECTED
    assert flip.reason == "net_flip_forbidden"


def test_performance_monitor_triggers_diagnosis() -> None:
    mon = PerformanceMonitor()
    base = datetime.now(UTC).date()
    for i in range(3):
        mon.record_day(50.0, day=base - timedelta(days=2 - i))
    assert mon.needs_diagnostics()
    diagnosis = mon.classify(phantom_pnl=400.0, live_pnl=40.0)
    assert diagnosis.kind == DiagnosisKind.PARAMETER_DRIFT


def test_phantom_timeline_markers_and_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "timeline.jsonl"
    tl = PhantomTimeline(run_id="run1", path=path)
    tl.record(
        bar_index=3,
        symbol="ETH/USDT",
        market={"price": 2000},
        strategies={"ma": {"action": "BUY"}},
        signal={"side": "buy"},
        decision={"status": "executed", "side": "buy"},
    )
    tl.record(
        bar_index=4,
        symbol="ETH/USDT",
        market={"price": 1990},
        strategies={"ma": {"action": "SELL"}},
        signal={"side": "sell"},
        decision={"status": "rejected", "reason": "risk", "side": "sell"},
    )
    markers = tl.markers()
    assert markers[0]["color"] == "green"
    assert markers[1]["color"] == "gray"
    loaded = PhantomTimeline.load_jsonl(path)
    assert loaded.snapshot()["count"] == 2
    assert loaded.at(3)[0].symbol == "ETH/USDT"


def test_ops_gap_watchdog_alerts_journal(tmp_path: Path) -> None:
    gaps = DataGapDetector(expected_interval_sec=60, max_gap_factor=1.5)
    assert gaps.observe("BTC/USDT", 1000.0) == []
    assert "gap" in gaps.observe("BTC/USDT", 1000.0 + 200)
    assert "duplicate" in gaps.observe("BTC/USDT", 1000.0 + 200, volume=1.0)

    wd = HeartbeatWatchdog(timeout_sec=1.0, clock=lambda: 10.0)
    wd.beat("engine", now=8.0)
    assert wd.dead(now=10.0) == ["engine"]

    alerts = AlertBus(path=tmp_path / "alerts.jsonl")
    alerts.emit("test", "hello", severity="warning")
    assert alerts.recent(1)[0].name == "test"

    journal = TradeJournal(tmp_path / "trades.jsonl")
    journal.record(
        symbol="BTC/USDT",
        side="buy",
        quantity=0.1,
        price=100.0,
        strategy_id="ma",
        reason="signal",
    )
    assert len(journal.entries()) == 1
