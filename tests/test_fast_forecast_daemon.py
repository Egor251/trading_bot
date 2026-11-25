"""Fast forecast daemon cycle pins: no execution, checkpoint on due step."""

from __future__ import annotations

import asyncio

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

import tools.fast_forecast_daemon as daemon  # noqa: E402
from engine.forecast_learning import ForecastConfig, ForecastLearner  # noqa: E402
from engine.online_checkpoint import load  # noqa: E402


def test_cycle_writes_decision_and_checkpoint(tmp_path, monkeypatch):
    ticks = iter(range(8))

    async def fake_collect():
        i = next(ticks, 7)
        return [{
            "symbol": "BTC/USDT", "ts": 1000.0 + i,
            "bid": 100.0 + i, "ask": 100.1 + i, "bid_size": 2.0, "ask_size": 3.0,
            "spread_bps": 1.0,
        }]

    monkeypatch.setattr(daemon, "collect_once", fake_collect)
    learner = ForecastLearner(ForecastConfig(horizon_sec=0.0, min_observations=6,
                                             update_every=6, lags=2))
    class _FastOpt:
        def run(self, X, y, tr, va, te, seed):
            return {"pred": 0.0001, "loss": 0.001, "baseline": 0.002}

        def weights(self):
            return {"s0": [[[0.1]]]}

    results = []
    for _ in range(8):
        results.append(asyncio.run(daemon.cycle(
            learner, _FastOpt(), tmp_path / "d.jsonl", tmp_path / "w.json"
        )))
    stepped = [d for d in results if d is not None]
    assert stepped and all(d["observe_only"] is True for d in stepped)
    got = load(tmp_path / "w.json")
    assert got is not None
    assert got.last_decision["observe_only"] is True
