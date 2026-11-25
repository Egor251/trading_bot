"""The research window must show the lab report, not a fabricated pass."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.paths import data_path  # noqa: E402
from services.ui_bridge_service import UiBridgeService  # noqa: E402


def _service():
    core = MagicMock()
    core.service_registry.get = MagicMock(return_value=None)
    return UiBridgeService(core, "ui_bridge_service")


def test_missing_report_is_unavailable_not_a_pass():
    svc = _service()
    out = svc._strategy_research_snapshot()  # noqa: SLF001
    assert out["ok"] is False
    assert out["reason"] == "no_report"


def test_report_is_read_with_evidence_and_blockers():
    report = {
        "ok": True,
        "symbol": "BTCUSDT",
        "block": "earlier",
        "selected": None,
        "blockers": ["no_eligible_candidate"],
        "config": {"alpha": 0.05, "draws": 500},
        "candidates": [{
            "strategy": "composite_aggregate",
            "real_wins": 4, "random_wins": 0, "shuffled_wins": 2,
            "adjusted_p": 0.01,
            "blockers": ["shuffled_control_dirty"],
            "window_nets": [1.0, 2.0, 3.0, 4.0],
            "window_p": [0.002, 0.002, 0.002, 0.002],
            "window_power_nets": [2.0, 3.0, 4.0, 5.0],
        }],
    }
    path = data_path("research", "strategy_lab.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report), encoding="utf-8")
    svc = _service()
    out = svc._strategy_research_snapshot()  # noqa: SLF001
    assert out["ok"] is True
    assert out["selected"] is None
    assert out["candidates"][0]["shuffled_wins"] == 2
    assert out["candidates"][0]["window_nets"] == [1.0, 2.0, 3.0, 4.0]


def test_malformed_report_is_unavailable():
    path = data_path("research", "strategy_lab.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"selected": "anything"}', encoding="utf-8")
    svc = _service()
    out = svc._strategy_research_snapshot()  # noqa: SLF001
    assert out["ok"] is False
    assert out["reason"] == "malformed_report"
