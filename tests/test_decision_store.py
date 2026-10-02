"""A strategy that starts bleeding cannot currently be interrogated.

The trade journal records fills: symbol, side, price, fee. What it cannot record
is the reasoning, and it never sees the decisions that did not fill -- including
every `observe_only` signal, which is the entire evidence base for a strategy on
probation. Meanwhile `StrategyDecision.metrics` computes the features every bar,
carries them one hop in `params_snapshot`, and is then dropped in the ensemble
service, which keeps only quantity/price/class_code/bar_ts.

So the features were computed, briefly existed, and vanished. This pins that they
are kept, and -- the part that makes keeping them worth anything -- that a
strategy operating outside the envelope it was validated on is *detected* rather
than inferred from a P&L chart.

The honest constraint these tests also pin: a strategy with no baseline is not
checked, and says so. Silence there must not be readable as "no drift", which is
the same failure shape as the fee reconciler that never ran.
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.decision_store import DecisionStore, FeatureProfile  # noqa: E402
from services.ensemble_service import (  # noqa: E402
    EnsembleService,
    _resolve_decision_path,
)


def _store(**kwargs) -> DecisionStore:
    return DecisionStore(**kwargs)


# -- the store itself ----------------------------------------------------


def test_a_decision_keeps_the_features_behind_it() -> None:
    store = _store()
    store.record(
        symbol="BTCUSDT",
        strategy_id="supertrend",
        action="BUY",
        strength=0.8,
        reason="trend flip",
        features={"rsi": 61.2, "atr_pct": 0.031, "slope": -0.004},
    )
    rows = store.rows()
    assert len(rows) == 1
    assert rows[0].features["rsi"] == 61.2
    assert rows[0].reason == "trend flip"


def test_non_numeric_features_are_dropped_not_stored_as_junk() -> None:
    """A profile built on strings and bools is not a profile."""
    store = _store()
    store.record(
        symbol="BTCUSDT",
        strategy_id="s",
        action="BUY",
        features={
            "rsi": 55.0,
            "label": "overbought",
            "warm": True,
            "missing": None,
        },
    )
    assert list(store.rows()[0].features) == ["rsi"]


def test_nan_cannot_poison_the_envelope() -> None:
    """min/max live in memory for the process lifetime; one NaN would linger."""
    store = _store()
    for value in (10.0, float("nan"), float("inf"), 20.0):
        store.record(
            symbol="S",
            strategy_id="s",
            action="BUY",
            features={"rsi": value},
        )
    profile = store.feature_profile(strategy_id="s")["rsi"]
    assert profile.minimum == 10.0
    assert profile.maximum == 20.0


def test_holds_are_sampled_but_the_sampling_is_honest() -> None:
    """A store that drops records silently is the fee reconciler's shape."""
    store = _store(hold_sample_rate=0.1)
    for _ in range(100):
        store.record(symbol="S", strategy_id="s", action="HOLD")
    stats = store.stats()
    assert stats["held_seen"] == 100
    assert stats["recorded"] == stats["in_memory_rows"]
    assert stats["sampled_away"] == 100 - stats["recorded"]
    assert stats["recorded"] >= 1, "sampling must not mean discarding"


def test_sampling_is_deterministic() -> None:
    """A replay of the same bars must sample the same rows."""
    runs = []
    for _ in range(2):
        store = _store(hold_sample_rate=0.25)
        for i in range(40):
            store.record(
                symbol="S", strategy_id="s", action="HOLD", features={"i": i}
            )
        runs.append([r.features["i"] for r in store.rows()])
    assert runs[0] == runs[1]


def test_the_row_cap_is_reported_not_hidden() -> None:
    store = _store(max_rows=10)
    for i in range(25):
        store.record(symbol="S", strategy_id="s", action="BUY", ts=float(i))
    stats = store.stats()
    assert stats["in_memory_rows"] == 10
    assert stats["dropped_after_cap"] == 15
    assert store.rows()[-1].features == {}


def test_it_survives_a_disk_that_refuses() -> None:
    """A decision log must never be the reason trading stops."""
    store = _store(path=Path("Z:/definitely/not/a/drive/decisions.jsonl"))
    row = store.record(symbol="S", strategy_id="s", action="BUY")
    assert row is not None


def test_the_file_is_append_only_jsonl() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "nested" / "decisions.jsonl"
        store = _store(path=path)
        store.record(symbol="BTCUSDT", strategy_id="s", action="BUY",
                     features={"rsi": 50.0})
        store.record(symbol="BTCUSDT", strategy_id="s", action="SELL",
                     features={"rsi": 40.0})
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["action"] == "BUY"


# -- the reader: drift against the validated envelope -------------------


def test_in_range_features_report_no_drift() -> None:
    store = _store()
    rng = random.Random(5)
    for _ in range(200):
        store.record(symbol="S", strategy_id="s", action="BUY",
                     features={"rsi": rng.uniform(30, 70)})
    baseline = store.freeze_baseline(strategy_id="s")
    assert store.drift_against(baseline, strategy_id="s") == []


def test_a_regime_shift_is_detected() -> None:
    """The whole reason for keeping the decisions."""
    store = _store()
    rng = random.Random(5)
    for _ in range(200):
        store.record(symbol="S", strategy_id="s", action="BUY",
                     features={"rsi": rng.uniform(30, 70)})
    baseline = store.freeze_baseline(strategy_id="s")
    for _ in range(40):
        store.record(symbol="S", strategy_id="s", action="BUY",
                     features={"rsi": rng.uniform(90, 99)})
    findings = store.drift_against(baseline, strategy_id="s")
    assert len(findings) == 1, "the regime shift was not detected"
    assert findings[0]["feature"] == "rsi"
    assert findings[0]["live_max"] > findings[0]["baseline_max"]
    assert findings[0]["meaning"]


def test_a_feature_absent_from_the_baseline_is_not_drift() -> None:
    """New instrumentation is not evidence of regime change."""
    store = _store()
    for _ in range(50):
        store.record(symbol="S", strategy_id="s", action="BUY",
                     features={"rsi": 50.0})
    baseline = store.freeze_baseline(strategy_id="s")
    for _ in range(10):
        store.record(symbol="S", strategy_id="s", action="BUY",
                     features={"rsi": 50.0, "brand_new": 1.0})
    findings = store.drift_against(baseline, strategy_id="s")
    assert [f["feature"] for f in findings] == []


def test_an_empty_baseline_says_nothing_rather_than_all_clear() -> None:
    store = _store()
    store.record(symbol="S", strategy_id="s", action="BUY", features={"rsi": 50.0})
    assert store.drift_against({}, strategy_id="s") == []


def test_the_envelope_respects_tolerance() -> None:
    envelope = FeatureProfile(feature="rsi", count=100, minimum=0.0, maximum=10.0)
    assert not envelope.contains(10.5)
    assert envelope.contains(11.0, tolerance=0.1)
    assert not envelope.contains(11.5, tolerance=0.1)


# -- end to end through the service --------------------------------------


def _service(ticker: str = "BTCUSDT", ensemble_cfg: dict | None = None) -> EnsembleService:
    core = MagicMock()

    async def _cfg_get(path, default=None):
        if path == "trading":
            return {
                "ensemble": {
                    "enabled": True,
                    "strategies": ["supertrend"],
                    "weight_mode": "equal",
                    **(ensemble_cfg or {}),
                }
            }
        if path == "instance.id":
            return "test"
        return default

    core.config.get = _cfg_get
    core.event_bus = MagicMock()
    core.event_bus.publish = AsyncMock()
    core.event_bus.subscribe = AsyncMock()
    core.event_bus.unsubscribe = AsyncMock()
    core.plugin_manager._instances = {}
    core.rule_engine = None

    svc = EnsembleService(core, "ensemble_service")
    svc.config = svc.default_config()
    svc.config["rebalance_sec"] = 0
    return svc


async def _ready() -> EnsembleService:
    """`_on_signal` short-circuits unless the service is actually running."""
    svc = _service()
    await svc.on_initialize()
    svc.is_running = True
    return svc


def _signal(ticker: str, *, action: str, features: dict, observe: bool = False):
    from host.events import KernelEvent

    return KernelEvent(
        event_type="strategy.signal",
        source_id="supertrend",
        payload={
            "strategy_id": "supertrend",
            "ticker": ticker,
            "action": action,
            "strength": 0.7,
            "reason": "flip",
            "params_snapshot": features,
            "observe_only": observe,
        },
    )


def test_the_service_records_decisions_it_used_to_discard() -> None:
    async def _run() -> None:
        svc = await _ready()
        await svc._on_signal(  # noqa: SLF001
            _signal("BTCUSDT", action="BUY", features={"rsi": 55.0, "atr": 1.2})
        )
        rows = svc.decisions_store.rows()
        assert len(rows) == 1, "the features were dropped again"
        assert rows[0].features["rsi"] == 55.0

    asyncio.run(_run())


def test_an_observe_signal_is_recorded_even_though_it_never_trades() -> None:
    """Probation evidence, or probation is unmeasurable."""

    async def _run() -> None:
        svc = await _ready()
        await svc._on_signal(  # noqa: SLF001
            _signal(
                "BTCUSDT", action="BUY", features={"rsi": 61.0}, observe=True
            )
        )
        rows = svc.decisions_store.rows()
        assert len(rows) == 1, (
            "observe-only signals leave no trace, which is exactly the gap"
        )
        assert rows[0].observe_only is True

    asyncio.run(_run())


def test_the_service_reports_no_baseline_as_unexamined() -> None:
    async def _run() -> None:
        svc = await _ready()
        await svc._on_signal(  # noqa: SLF001
            _signal("BTCUSDT", action="BUY", features={"rsi": 55.0})
        )
        result = svc.check_feature_drift("supertrend")
        assert result["checked"] is False
        assert result["reason"] == "no_baseline"

    asyncio.run(_run())


def test_the_store_path_config_actually_takes_effect() -> None:
    """A knob that configures nothing is the same defect, wearing a YAML file.

    `on_initialize` has to rebuild the store: `__init__` runs before `self.config`
    is populated, so a path set in config and never re-read would leave the store
    in-memory and lose all provenance on restart.
    """

    async def _run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svc = _service()
            path = Path(tmp) / "ops" / "decisions.jsonl"
            svc.config = dict(svc.default_config())
            svc.config["rebalance_sec"] = 0
            svc.config["decision_store_path"] = str(path)
            await svc.on_initialize()
            svc.is_running = True
            await svc._on_signal(  # noqa: SLF001
                _signal("BTCUSDT", action="BUY", features={"rsi": 55.0})
            )
            assert svc.decisions_store.stats()["persisted"] is True
            assert path.exists(), "the configured path was ignored"
            assert '"rsi": 55.0' in path.read_text(encoding="utf-8")

    asyncio.run(_run())


def test_the_trading_ensemble_config_block_also_binds() -> None:
    """The knob was shipped under `trading.ensemble` and read from the wrong place.

    Service config lives under `plugins.services.<component_id>`, while
    `corr_cap` and friends are read from either location. The store path was
    initially read only from `self.config`, so the shipped YAML entry did
    nothing at all and no file was ever created -- a knob that configures
    nothing, which is the recurring defect of this repository.
    """

    async def _run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ops" / "decisions.jsonl"
            svc = _service(ensemble_cfg={"decision_store_path": str(path)})
            svc.config = dict(svc.default_config())
            svc.config["rebalance_sec"] = 0
            svc.config.pop("decision_store_path", None)
            await svc.on_initialize()
            assert svc.decisions_store.stats()["persisted"] is True
            assert svc.decisions_store.path == path

    asyncio.run(_run())


def test_a_relative_path_follows_the_data_dir_redirect(tmp_path, monkeypatch) -> None:
    """A research sweep must not write into the live trading data.

    Research tools redirect `TRADING_DATA_DIR` to a scratch directory. A
    CWD-relative `data/ops/decisions.jsonl` would ignore that and let a phantom
    run contaminate the production store, which is the whole reason the redirect
    exists.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("TRADING_DATA_DIR", str(scratch))
    # data_dir() is read at call time, so re-import is unnecessary.
    from engine.paths import data_dir

    assert Path(data_dir()).resolve() == scratch.resolve()

    resolved = _resolve_decision_path("data/ops/decisions.jsonl")
    assert scratch.resolve() in Path(resolved).resolve().parents
    assert Path("C:/") not in [str(Path(resolved))[:3]]


def test_an_absolute_path_is_left_alone(tmp_path, monkeypatch) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("TRADING_DATA_DIR", str(scratch))
    target = tmp_path / "elsewhere.jsonl"
    assert _resolve_decision_path(str(target)) == target


def test_the_shipped_config_actually_carries_the_knobs() -> None:
    """Reads the real `config/trading_bot.yaml`.

    The bug this pins: the knobs were added under `trading.ensemble` but read
    from the service section `plugins.services.ensemble_service`, so the shipped
    YAML did nothing. A test using a hand-built config would never notice --
    every synthetic config had the keys in the right place.
    """
    import yaml

    repo = Path(__file__).resolve().parents[1]
    raw = yaml.safe_load((repo / "config" / "trading_bot.yaml").read_text("utf-8"))
    ensemble_cfg = raw["trading"]["ensemble"]
    assert ensemble_cfg.get("decision_store_path"), (
        "trading.ensemble.decision_store_path is missing from the shipped config"
    )
    assert "decision_hold_sample_rate" in ensemble_cfg

    async def _run() -> None:
        core = MagicMock()

        async def _cfg_get(path, default=None):
            if path == "trading":
                return raw["trading"]
            if path == "instance.id":
                return "test"
            return default

        core.config.get = _cfg_get
        core.event_bus = MagicMock()
        core.event_bus.publish = AsyncMock()
        core.event_bus.subscribe = AsyncMock()
        core.event_bus.unsubscribe = AsyncMock()
        core.plugin_manager._instances = {}
        core.rule_engine = None

        svc = EnsembleService(core, "ensemble_service")
        # The service section from the same file, exactly as the kernel builds it.
        svc.config = dict(raw["services"]["ensemble_service"])
        await svc.on_initialize()
        assert svc.decisions_store.stats()["persisted"] is True, (
            "the shipped config did not bind the store"
        )
        assert svc.decisions_store.hold_sample_rate == float(
            ensemble_cfg["decision_hold_sample_rate"]
        )

    asyncio.run(_run())


def test_the_service_detects_drift_after_a_baseline_is_frozen() -> None:
    async def _run() -> None:
        svc = await _ready()
        rng = random.Random(2)
        for _ in range(120):
            await svc._on_signal(  # noqa: SLF001
                _signal(
                    "BTCUSDT",
                    action="BUY",
                    features={"rsi": rng.uniform(30, 70)},
                )
            )
        svc.freeze_decision_baseline("supertrend")
        assert svc.check_feature_drift("supertrend")["drifted"] is False

        for _ in range(30):
            await svc._on_signal(  # noqa: SLF001
                _signal(
                    "BTCUSDT",
                    action="BUY",
                    features={"rsi": rng.uniform(90, 99)},
                )
            )
        after = svc.check_feature_drift("supertrend")
        assert after["drifted"] is True
        assert after["findings"][0]["feature"] == "rsi"

        status = await svc.get_status()
        assert status["feature_drift"]["supertrend"] is True
        assert status["decision_store"]["recorded"] > 0

    asyncio.run(_run())