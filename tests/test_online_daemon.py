"""Daemon cycle pins: checkpoint written, observe-only preserved, restart-safe."""

from __future__ import annotations

import asyncio

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

import tools.online_daemon as daemon  # noqa: E402
from engine.online_checkpoint import load  # noqa: E402


class _Learner:
    features = [1, 2, 3]
    bars_seen = 9
    decisions = [{"observe_only": True, "tradable": False}]


class _Opt:
    def weights(self):
        return {"s0": [[[0.1, 0.2]]]}


def test_cycle_saves_observe_only_checkpoint(tmp_path, monkeypatch):
    async def fake_replay(bars, out, opt, learner=None):
        return _Learner(), opt

    monkeypatch.setattr(daemon, "replay", fake_replay)
    cp = tmp_path / "weights.json"
    asyncio.run(daemon.run_cycle("replay", 10, "decisions.jsonl", _Opt(), cp))
    got = load(cp)
    assert got is not None
    assert got.observations == 3
    assert got.bars_seen == 9
    assert got.last_decision["observe_only"] is True
    assert got.weights == {"s0": [[[0.1, 0.2]]]}
    assert len(got.config_hash) == 16
