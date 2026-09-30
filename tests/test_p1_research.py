"""Disk OHLC cache + live readiness gate tests."""

from __future__ import annotations

import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.market import Candle  # noqa: E402
from engine.ohlc_disk_cache import (  # noqa: E402
    cache_file_path,
    read_disk_cache,
    write_disk_cache,
)
from engine.research_gate import (  # noqa: E402
    LiveReadinessPolicy,
    evaluate_live_readiness,
    live_promotion_blockers,
    load_clearance,
    record_digest_clearance,
)


def _bars(n: int = 5) -> list[Candle]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Candle(
            symbol="BTCUSDT",
            timeframe="1d",
            ts=base + timedelta(days=i),
            open=100 + i,
            high=101 + i,
            low=99 + i,
            close=100.5 + i,
            volume=1.0,
            exchange="ccxt",
            class_code="CRYPTO",
        )
        for i in range(n)
    ]


def test_disk_cache_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = cache_file_path(
            tmp,
            provider="ccxt",
            ticker="BTCUSDT",
            class_code="CRYPTO",
            timeframe="1d",
            limit=5,
            start=None,
            end=None,
        )
        write_disk_cache(path, _bars(), meta={"ticker": "BTCUSDT"})
        hit = read_disk_cache(path, ttl_sec=3600)
        assert hit is not None
        assert len(hit) == 5
        assert hit[-1].close == 104.5
        assert read_disk_cache(path, ttl_sec=0) is None
        stale = read_disk_cache(path, ttl_sec=0, allow_stale=True)
        assert stale is not None
        assert len(stale) == 5


def _digest(**overrides: object) -> dict:
    base = {
        "ok": True,
        "strategy_id": "bollinger_reversion",
        "ticker": "BTCUSDT",
        "holdout_bars": 60,
        "holdout_pnl": 54.0,
        "holdout_trades": 3,
        "robust_sign_match": True,
        "mean_oos_pnl": 8.2,
        "ci_crosses_zero": True,
        "profit_factor": 1.3,
        "total_oos_trades": 25,
        "chosen": {"window": 14, "num_std": 1.5},
    }
    base.update(overrides)
    return base


def test_research_gate_holdout_required() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "clearance.json"
        blockers = live_promotion_blockers(
            ["bollinger_reversion"], ticker="BTCUSDT", path=path
        )
        assert blockers and any(b["reason"] == "no_holdout_clearance" for b in blockers)
        record_digest_clearance(_digest(), path=path)
        store = load_clearance(path)
        assert "bollinger_reversion::BTCUSDT" in store["entries"]
        # wrong sign blocked
        record_digest_clearance(
            _digest(
                strategy_id="rsi_reversion",
                holdout_pnl=-1.0,
                robust_sign_match=False,
                mean_oos_pnl=1.0,
                chosen={},
            ),
            path=path,
        )
        bad = live_promotion_blockers(
            ["rsi_reversion"], ticker="BTCUSDT", path=path
        )
        assert bad and any(b["reason"] == "robust_sign_match" for b in bad)


def test_bollinger_like_not_live_ok() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "clearance.json"
        record_digest_clearance(_digest(), path=path)
        ev = evaluate_live_readiness(
            "bollinger_reversion",
            path=path,
            plugin_params={"window": 14, "num_std": 1.5},
        )
        assert ev["observe_ok"] is True
        assert ev["live_ok"] is False
        reasons = {b["reason"] for b in ev["blockers"]}
        assert "ci_excludes_zero" in reasons
        assert "holdout_trades" in reasons
        assert "tickers_robust" in reasons


def test_live_ok_two_tickers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "clearance.json"
        good = dict(
            holdout_trades=10,
            total_oos_trades=40,
            ci_crosses_zero=False,
            profit_factor=1.5,
            mean_oos_pnl=5.0,
            robust_sign_match=True,
            chosen={"window": 14, "num_std": 1.5},
        )
        record_digest_clearance(_digest(ticker="BTCUSDT", **good), path=path)
        record_digest_clearance(
            _digest(ticker="ETHUSDT", **good), path=path
        )
        ev = evaluate_live_readiness(
            "bollinger_reversion",
            path=path,
            plugin_params={"window": 14, "num_std": 1.5},
        )
        assert ev["live_ok"] is True
        assert ev["grade"] == "live_ok"
        assert not live_promotion_blockers(
            ["bollinger_reversion"],
            path=path,
            plugin_params_by_id={
                "bollinger_reversion": {"window": 14, "num_std": 1.5}
            },
        )


def test_params_mismatch_blocker() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "clearance.json"
        good = dict(
            holdout_trades=10,
            total_oos_trades=40,
            ci_crosses_zero=False,
            profit_factor=1.5,
            mean_oos_pnl=5.0,
        )
        record_digest_clearance(_digest(ticker="BTCUSDT", **good), path=path)
        record_digest_clearance(_digest(ticker="ETHUSDT", **good), path=path)
        ev = evaluate_live_readiness(
            "bollinger_reversion",
            path=path,
            plugin_params={"window": 10, "num_std": 2.5},
        )
        assert ev["live_ok"] is False
        assert any(b["reason"] == "params_applied" for b in ev["blockers"])


def test_plugin_params_from_config_reads_plugins_block() -> None:
    from engine.research_gate import plugin_params_from_config

    raw = {
        "plugins": {"bollinger_reversion": {"window": 14, "num_std": 1.5}},
        "trading": {"plugins": {"bollinger_reversion": {"window": 99}}},
    }
    got = plugin_params_from_config(raw, "bollinger_reversion")
    assert got == {"window": 14, "num_std": 1.5}
    assert plugin_params_from_config({}, "bollinger_reversion") == {}
    nested = {"trading": {"plugins": {"ma_cross_example": {"fast": 5}}}}
    assert plugin_params_from_config(nested, "ma_cross_example") == {"fast": 5}


def test_normalize_research_ticker() -> None:
    from engine.research_gate import normalize_research_ticker, _key

    assert normalize_research_ticker("ETH/USDT") == "ETHUSDT"
    assert normalize_research_ticker("btc-usdt") == "BTCUSDT"
    assert _key("bollinger_reversion", "ETH/USDT") == _key(
        "bollinger_reversion", "ETHUSDT"
    )


def test_relaxed_policy_live_ok_single_ticker() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "clearance.json"
        record_digest_clearance(
            _digest(
                holdout_trades=10,
                total_oos_trades=40,
                ci_crosses_zero=False,
                profit_factor=1.5,
            ),
            path=path,
        )
        pol = LiveReadinessPolicy(min_tickers_robust=1, require_params_applied=False)
        ev = evaluate_live_readiness(
            "bollinger_reversion", path=path, policy=pol
        )
        assert ev["live_ok"] is True


if __name__ == "__main__":
    test_disk_cache_roundtrip()
    test_research_gate_holdout_required()
    test_bollinger_like_not_live_ok()
    test_live_ok_two_tickers()
    test_params_mismatch_blocker()
    test_plugin_params_from_config_reads_plugins_block()
    test_relaxed_policy_live_ok_single_ticker()
    print("p1_research OK")
