"""Observability: feed-gap detection, auto-resume, health state, metrics.

Each test names the production symptom it pins down. The gap detector used to be
fed `time.time()` (arrival time) instead of the exchange bar timestamp, so
ordinary network jitter in a 5 s poll loop was reported as a data gap, the ops
monitor froze risk, and nothing lifted the freeze — 3,536
`risk.reject{stage: halted, reason: market_data_gap}` in a month with zero orders
placed, and no alert anywhere.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.health_state import evaluate_health, write_health_state  # noqa: E402
from engine.market import candle_to_market_payload  # noqa: E402
from engine.ops_safety import DataGapDetector  # noqa: E402
from tools.healthcheck import check_state_file  # noqa: E402


# ---------------------------------------------------------------------------
# The payload must carry the exchange timestamp
# ---------------------------------------------------------------------------


def test_candle_payload_publishes_exchange_timestamp() -> None:
    """Without `ts` the detector cannot tell data staleness from poll jitter."""
    from engine.market import Candle
    from datetime import UTC, datetime

    candle = Candle(
        symbol="BTC/USDT",
        timeframe="1m",
        ts=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=10.0,
    )
    payload = candle_to_market_payload(candle, class_code="CRYPTO")
    assert isinstance(payload.get("ts"), float)
    assert payload["ts"] == candle.ts.timestamp()


# ---------------------------------------------------------------------------
# The detector must measure the data, not the loop
# ---------------------------------------------------------------------------


def test_detector_ignores_poll_jitter() -> None:
    """A 200 s arrival gap with bars 60 s apart is *not* a data gap."""
    det = DataGapDetector(expected_interval_sec=60.0)
    base = 1_700_000_000.0
    assert det.observe("BTC/USDT", base, now=base) == []
    # Arrival 200 s later, but the bar itself advanced by exactly one interval.
    assert det.observe("BTC/USDT", base + 60, now=base + 200) == []


def test_detector_reports_a_real_bar_gap() -> None:
    det = DataGapDetector(expected_interval_sec=60.0)
    base = 1_700_000_000.0
    det.observe("BTC/USDT", base, now=base)
    assert "gap" in det.observe("BTC/USDT", base + 600, now=base + 600)


def test_detector_duplicate_and_out_of_order_are_reachable() -> None:
    """Both branches were dead code while the detector was fed arrival times."""
    det = DataGapDetector(expected_interval_sec=60.0)
    base = 1_700_000_000.0
    det.observe("BTC/USDT", base, now=base)
    assert "duplicate" in det.observe("BTC/USDT", base, now=base + 1)
    det.observe("BTC/USDT", base + 600, now=base + 600)
    assert "out_of_order" in det.observe("BTC/USDT", base + 120, now=base + 601)


def test_detector_reports_stale_feed_after_tolerance() -> None:
    """Staleness — a feed whose newest bar is minutes old — is the real alarm."""
    det = DataGapDetector(expected_interval_sec=60.0, stale_after_sec=180.0, stale_tolerance=2)
    base = 1_700_000_000.0
    det.observe("BTC/USDT", base, now=base)
    # Fresh arrival, but the newest bar is 10 minutes old.
    assert "stale" not in det.observe("BTC/USDT", base, now=base + 600)
    assert "stale" in det.observe("BTC/USDT", base, now=base + 601)


def test_detector_reports_feed_age() -> None:
    det = DataGapDetector(stale_after_sec=180.0)
    base = 1_700_000_000.0
    assert det.feed_age("BTC/USDT") is None
    det.observe("BTC/USDT", base, now=base)
    assert det.feed_age("BTC/USDT", now=base + 30) == pytest.approx(30.0, abs=1.0)


# ---------------------------------------------------------------------------
# Health state file (A18)
# ---------------------------------------------------------------------------


def test_health_state_reflects_halt_and_degraded_feed(tmp_path: Path) -> None:
    path = tmp_path / "health.json"
    halted = SimpleNamespace(halted=True, _halt_reason="daily_loss=0.13", mode="paper")
    status, detail = evaluate_health(runtime=halted, now=time.time())
    assert status == "halted"
    assert "daily_loss" in detail

    stale_gaps = DataGapDetector(stale_after_sec=180.0)
    base = time.time() - 600
    stale_gaps.observe("BTC/USDT", base, now=base)
    healthy = SimpleNamespace(halted=False, _halt_reason="", mode="paper")
    status, detail = evaluate_health(
        runtime=healthy, gaps=stale_gaps, now=time.time(), stale_feed_sec=180.0
    )
    assert status == "degraded"
    assert "stale" in detail

    write_health_state(runtime=healthy, gaps=stale_gaps, path=path, now=time.time())
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "degraded"
    assert payload["halted"] is False


def test_health_state_reports_missing_runtime(tmp_path: Path) -> None:
    path = tmp_path / "health.json"
    status, _ = evaluate_health(runtime=None, now=time.time())
    assert status == "down"
    write_health_state(runtime=None, path=path, now=time.time())
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "down"


def test_health_state_write_is_atomic(tmp_path: Path) -> None:
    """A probe must never read a half-written file: a truncated health file is
    indistinguishable from a dead engine."""
    path = tmp_path / "health.json"
    runtime = SimpleNamespace(halted=False, _halt_reason="", mode="paper")
    write_health_state(runtime=runtime, path=path, now=time.time())
    first = path.read_text(encoding="utf-8")
    write_health_state(runtime=runtime, path=path, now=time.time() + 1)
    assert json.loads(path.read_text(encoding="utf-8"))["timestamp"] > json.loads(first)[
        "timestamp"
    ]
    # No temp files left behind.
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_healthcheck_fails_on_stale_writer(tmp_path: Path) -> None:
    path = tmp_path / "health.json"
    path.write_text(
        json.dumps({"status": "ok", "timestamp": time.time() - 9999, "detail": ""}),
        encoding="utf-8",
    )
    healthy, reason = check_state_file(path, now=time.time(), max_age=45.0)
    assert not healthy
    assert "stale" in reason


def test_healthcheck_does_not_restart_a_deliberate_halt(tmp_path: Path) -> None:
    """Bouncing the container because risk halted it would replace a correct
    refusal with an uncontrolled restart."""
    path = tmp_path / "health.json"
    path.write_text(
        json.dumps(
            {
                "status": "halted",
                "timestamp": time.time(),
                "detail": "risk halted (daily_loss=0.13)",
            }
        ),
        encoding="utf-8",
    )
    healthy, reason = check_state_file(path, now=time.time(), max_age=45.0)
    assert healthy, reason
    assert "halted" in reason


def test_healthcheck_fails_on_down_state(tmp_path: Path) -> None:
    path = tmp_path / "health.json"
    path.write_text(
        json.dumps({"status": "down", "timestamp": time.time(), "detail": "no runtime"}),
        encoding="utf-8",
    )
    healthy, reason = check_state_file(path, now=time.time(), max_age=45.0)
    assert not healthy
    assert "down" in reason


# ---------------------------------------------------------------------------
# Metrics reach the exporter (A16)
# ---------------------------------------------------------------------------


def test_publish_metrics_populates_alert_relevant_gauges() -> None:
    """All ten alerts in ops/prometheus/alerts.yml read gauges that nothing fed."""
    from engine.observability import OperationsObservability
    from engine.order import OrderManager
    from engine.risk import RiskLimits, RiskManager

    obs = OperationsObservability()
    core = SimpleNamespace(operations_observability=obs)
    runtime = SimpleNamespace(
        core=core,
        orders=OrderManager(
            risk=RiskManager(
                RiskLimits(max_leverage=1.0, max_position_pct=0.2)
            ),
            mode="paper",
            equity=100_000.0,
        ),
        broker=object(),
        reconciliation=None,
        protection=SimpleNamespace(has_protection=lambda _k: False),
        open_position_keys=lambda: [],
    )

    from engine.runtime import TradingRuntime

    fn = TradingRuntime.publish_metrics
    # Drive the real implementation against the stub state.
    TradingRuntime.publish_metrics(runtime)  # type: ignore[arg-type]

    text = obs.prometheus_text()
    samples = {
        line.split("{")[0].split(" ")[0]: line
        for line in text.split("\n")
        if line and not line.startswith("#") and " " in line
    }
    for name in (
        "trading_broker_connected",
        "trading_reconciliation_ok",
        "trading_drawdown_ratio",
        "trading_stop_coverage_ratio",
    ):
        assert name in samples, f"{name} has no sample; exposition was:\n{text}"
    assert samples["trading_broker_connected"].split()[-1] == "1"
    # Reconciliation has never run against this book -> the alert must read 0.
    assert samples["trading_reconciliation_ok"].split()[-1] == "0"
    # A flat book is trivially fully covered.
    assert samples["trading_stop_coverage_ratio"].split()[-1] == "1"
    assert fn is not None


def test_unprotected_position_reports_partial_stop_coverage() -> None:
    """`trading_stop_coverage_ratio` is the alert that would have caught the
    missing protective layer; with an open, unprotected position it must not
    read 1."""
    from engine.observability import OperationsObservability
    from engine.order import OrderManager, OrderIntent, OrderSide
    from engine.risk import RiskLimits, RiskManager
    from engine.runtime import TradingRuntime

    obs = OperationsObservability()
    orders = OrderManager(
        risk=RiskManager(RiskLimits(max_leverage=1.0, max_position_pct=0.2)),
        mode="paper",
        equity=100_000.0,
    )
    orders._record_fill(  # noqa: SLF001
        OrderIntent(
            symbol="BTC/USDT",
            class_code="CRYPTO",
            side=OrderSide.BUY,
            quantity=0.1,
            limit_price=50_000.0,
        ),
        quantity=0.1,
        price=50_000.0,
        raw={},
    )
    runtime = SimpleNamespace(
        core=SimpleNamespace(operations_observability=obs),
        orders=orders,
        broker=object(),
        reconciliation=None,
        protection=SimpleNamespace(has_protection=lambda _k: False),
        open_position_keys=lambda: [k for k, p in orders.positions.items()],
    )
    TradingRuntime.publish_metrics(runtime)  # type: ignore[arg-type]
    line = [
        ln
        for ln in obs.prometheus_text().split("\n")
        if ln.startswith("trading_stop_coverage_ratio")
    ][0]
    assert line.split()[-1] == "0", line


def test_publish_metrics_never_raises_on_partial_state() -> None:
    """Metrics must not be able to break the trading loop."""
    from engine.runtime import TradingRuntime

    TradingRuntime.publish_metrics(SimpleNamespace())  # type: ignore[arg-type]
    TradingRuntime.publish_metrics(SimpleNamespace(core=object()))  # type: ignore[arg-type]


def test_protective_layer_reports_missing_protection() -> None:
    from engine.protective_runtime import ProtectiveLayer

    layer = ProtectiveLayer()
    assert layer.has_protection("CRYPTO:BTC/USDT") is False
    assert layer.has_protection("") is False
