"""Pin the two gate wirings that were computed but never reached their target.

`apply_canary_quantity` looked up `raw_cfg[sid]`, while plugin parameters live
under `raw_cfg["plugins"][sid]` -- so it reported an empty applied map and no
order size ever changed. `adjust_pvalues` corrected over the batch size even
though the gate computes the available universe; rule 6 of the register exists
because that ratio was ignored once already.
"""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.research_gate import apply_multiplicity, apply_canary_quantity  # noqa: E402
from engine.simulation import adjust_pvalues  # noqa: E402


def test_canary_scales_the_canonical_plugins_block():
    cfg = {"plugins": {"s": {"quantity": 2.0, "fast": 5}}}
    applied = apply_canary_quantity(cfg, ["s"], scale=0.25)
    assert applied["s"]["quantity_before"] == 2.0
    assert applied["s"]["quantity_after"] == 0.5
    assert cfg["plugins"]["s"]["quantity"] == 0.5
    assert cfg["plugins"]["s"]["fast"] == 5


def test_canary_works_when_block_is_under_trading_plugins():
    cfg = {"trading": {"plugins": {"s": {"quantity": 4.0}}}}
    applied = apply_canary_quantity(cfg, ["s"], scale=0.25)
    assert applied["s"]["quantity_after"] == 1.0
    assert cfg["trading"]["plugins"]["s"]["quantity"] == 1.0


def test_canary_ignored_when_no_quantity_or_bad_scale():
    cfg = {"plugins": {"s": {"fast": 5}}}
    assert apply_canary_quantity(cfg, ["s"], scale=0.25) == {}
    assert apply_canary_quantity({"plugins": {"s": {"quantity": 2.0}}}, ["s"], scale=1.0) == {}


def test_holm_can_correct_over_a_larger_universe():
    """Sullivan: the family is the available space, not the batch that survived."""
    p = [0.01]
    assert adjust_pvalues(p, "holm", n_tests=1) == [0.01]
    assert adjust_pvalues(p, "holm", n_tests=20)[0] == 0.2


def test_multiplicity_uses_the_available_universe_when_supplied():
    rows = [
        {
            "strategy_id": "s",
            "p_value": 0.01,
            "variants_available_lifetime": 36,
            "variants_tried_lifetime": 3,
            "live_ok": True,
            "observe_ok": True,
            "checks": [],
            "blockers": [],
        }
    ]
    batch = apply_multiplicity(rows, alpha=0.05)
    wide = apply_multiplicity(rows, alpha=0.05, universe=36)
    assert batch["rows"][0]["p_value_adjusted"] < 0.05
    assert wide["rows"][0]["p_value_adjusted"] > 0.05
    assert wide["rows"][0]["live_ok"] is False
