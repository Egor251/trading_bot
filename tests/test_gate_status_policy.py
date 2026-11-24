"""Pin that the operator report judges with the shipped policy, not defaults."""

from __future__ import annotations

import yaml

import tools.gate_status as gs


def test_gate_status_uses_the_shipped_policy(monkeypatch, tmp_path):
    cfg = {"trading": {"live_readiness": {"max_age_days": 7, "enforce": False}}}
    path = tmp_path / "trading_bot.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setattr(gs, "CONFIG_PATH", path)

    seen: dict = {}

    def _capture(ids, *, policy, **kwargs):
        seen["policy"] = policy
        return {"rows": [], "live_ok": [], "observe_ok": []}

    monkeypatch.setattr(gs, "evaluate_many", _capture)
    assert gs.main() == 0
    assert seen["policy"].max_age_days == 7
    assert seen["policy"].enforce is False
