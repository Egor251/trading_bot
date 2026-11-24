"""Fail-closed evidence in the research gate (B2/B3/B6/B8).

One premise produced four defects: "a statistic that could not be computed may be
replaced by a neutral default". At the gate that default is a PASS: p=0.0 is the
most significant value, ci_crosses_zero=False is a clean interval, and
pbo.ok=False was read as "not unstable". All three turn missing evidence into
promotion. These tests pin the opposite: unknown stays unknown and blocks.

Test behavior, not implementation: build a digest the way walk-forward does,
hand it to the gate, and check the verdict the caller would see.
"""

from __future__ import annotations

import random

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.research_digest import digest_walk_forward  # noqa: E402
from engine.research_gate import apply_multiplicity, batch_research_verdict  # noqa: E402


def _report(**oos_overrides) -> dict:
    oos = {
        "mean_oos_pnl": 1.0,
        "total_oos_trades": 60,
        "profit_factor": 2.0,
        "ci_low": 0.5,
        "ci_high": 1.5,
        "p_value": 0.01,
        "oos_pnl_by_fold": [0.1] * 12,
    }
    oos.update(oos_overrides)
    return {
        "provenance": {"strategy_id": "legacy", "ticker": "BTCUSDT"},
        "oos": oos,
        "holdout": {"pnl": 1.0, "trades": 14, "bars": 120},
    }


def _rows(n: int = 8, folds: int = 12, drift: float = 0.5, seed: int = 11) -> list[dict]:
    rng = random.Random(seed)
    return [
        {
            "strategy_id": f"s{i}",
            "p_value": 0.001,
            "variants_tried_lifetime": 4,
            "oos_pnl_by_fold": [rng.gauss(drift, 1.0) for _ in range(folds)],
            "live_ok": True,
            "observe_ok": True,
            "checks": [],
            "blockers": [],
        }
        for i in range(n)
    ]


def test_missing_p_value_stays_unknown_through_digest_and_multiplicity():
    oos = _report()
    oos["oos"].pop("p_value")
    digest = digest_walk_forward(oos)

    assert digest["p_value"] is None, (
        "a missing p-value must survive as unknown; 0.0 is the most significant "
        "possible value and promotes a record that has no evidence"
    )
    out = apply_multiplicity(
        [
            {
                "strategy_id": "legacy",
                "p_value": digest["p_value"],
                "live_ok": True,
                "observe_ok": True,
                "checks": [],
                "blockers": [],
            }
        ]
    )
    assert out["multiplicity_unknown"] == 1
    assert out["rows"][0]["live_ok"] is False


def test_missing_ci_is_reported_as_crossing_zero():
    report = _report()
    report["oos"].pop("ci_low")
    report["oos"].pop("ci_high")

    assert digest_walk_forward(report)["ci_crosses_zero"] is True, (
        "an unknown interval cannot be reported as one that excludes zero"
    )


def test_unmeasurable_pbo_never_yields_batch_edge_stable():
    # 12 folds but 16 requested splits: cscv_pbo cannot run. The batch still has
    # two usable series, so it reaches the verdict instead of the early return.
    out = batch_research_verdict(_rows(), samples=200, seed=3, pbo_splits=16)
    assert out["pbo"]["ok"] is False
    assert out["verdict"] == "not_evaluable"
    assert out["notes"]["pbo_available"] is False


def test_max_pbo_threshold_is_honored():
    rows = _rows()
    strict = batch_research_verdict(rows, samples=200, seed=3, max_pbo=0.5)
    loose = batch_research_verdict(rows, samples=200, seed=3, max_pbo=0.99)
    assert strict["pbo"]["ok"] is True
    assert strict["pbo"]["pbo"] > 0.5
    assert strict["verdict"] == "unstable_selection"
    assert loose["verdict"] == "batch_edge_stable"
