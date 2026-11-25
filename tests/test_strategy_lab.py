"""Known-answer tests for the autonomous selection instrument.

A selection tool that can pass on no-edge data is worse than no tool. Each
case here has an answer that is obvious by construction.
"""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.strategy_lab import LabConfig, WindowEvidence, evaluate_lab  # noqa: E402


def _ev(net: float = 10.0, p: float = 0.002, *, power: bool = True,
        controls_clean: bool = True) -> WindowEvidence:
    return WindowEvidence(
        net=net, p=p,
        power_net=20.0 if power else -1.0,
        power_p=0.002 if power else 0.5,
        random_p=0.5 if controls_clean else 0.002,
        shuffled_p=0.5 if controls_clean else 0.002,
    )


def test_clean_candidate_is_selected():
    out = evaluate_lab({"a": [_ev() for _ in range(4)]})
    assert out["selected"] == ["a"]
    assert out["blockers"] == []


def test_no_edge_selects_nothing():
    out = evaluate_lab({"a": [_ev(net=-1.0, p=0.6, power=False) for _ in range(4)]})
    assert out["selected"] is None
    assert "no_eligible_candidate" in out["blockers"]


def test_dirty_control_blocks_even_with_a_positive_net():
    out = evaluate_lab({"a": [_ev(controls_clean=False) for _ in range(4)]})
    assert out["selected"] is None
    c = out["candidates"][0]
    assert "random_control_dirty" in c["blockers"]
    assert "shuffled_control_dirty" in c["blockers"]


def test_underpowered_reference_blocks_everyone():
    out = evaluate_lab({
        "a": [_ev(power=False) for _ in range(4)],
        "b": [_ev() for _ in range(4)],
    })
    assert out["selected"] is None
    assert "power_inadequate" in out["blockers"]


def test_holm_over_candidates_blocks_both():
    out = evaluate_lab({
        "a": [_ev(p=0.04) for _ in range(4)],
        "b": [_ev(p=0.04) for _ in range(4)],
    })
    assert out["selected"] is None
    assert all("multiplicity" in c["blockers"] for c in out["candidates"])


def test_two_survivors_are_returned_together_not_ranked():
    out = evaluate_lab({
        "a": [_ev(net=5.0) for _ in range(4)],
        "b": [_ev(net=50.0) for _ in range(4)],
    })
    assert out["selected"] == ["a", "b"]


def test_empty_input_is_not_a_pass():
    out = evaluate_lab({})
    assert out["selected"] is None
    assert "no_candidates" in out["blockers"]


def test_frozen_config_is_reported():
    cfg = LabConfig(alpha=0.01, min_windows=4)
    out = evaluate_lab({"a": [_ev(p=0.002) for _ in range(4)]}, cfg)
    assert out["config"]["alpha"] == 0.01
    assert out["config"]["min_windows"] == 4
