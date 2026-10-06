"""Four corrections, one verdict, and a denominator that counts what was discarded.

Sullivan, Timmermann & White (1999) put the whole multiplicity problem in one
line: the correction must cover the *universe* the rules were drawn from, including
the ones never reported, or it is a correction over a subset and always too lenient.

That store already carried `variants_tried_lifetime`, described in its own comment as
"the denominator a deflation argument needs", and nothing read it. These assert it is
read now.

The second thing pinned here is that a batch passes only if it survives the whole
family. Taking the most favourable of four tests would be the same mistake as
picking the best of twenty-one strategies, one level up -- and it is the mistake a
system makes most easily, because it is the one that makes the report look good.

The third is PBO, which is the only one of the four that can fail a batch that every
other test passed: significance without a stable ranking.
"""

from __future__ import annotations

import json
import random

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.research_digest import digest_walk_forward  # noqa: E402
from engine.research_gate import (  # noqa: E402
    LiveReadinessPolicy,
    batch_research_verdict,
    evaluate_live_readiness,
    evaluate_many,
    record_digest_clearance,
)


def _folds(drift: float, n: int, seed: int, noise: float = 1.0) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(drift, noise) for _ in range(n)]


def _record(
    store,
    strategy_id: str,
    ticker: str,
    *,
    folds: list[float] | None = None,
    variants: int = 12,
    p_value: float = 0.20,
) -> None:
    series = folds if folds is not None else [0.1] * 12
    mean = sum(series) / len(series)
    digest = digest_walk_forward({
        "provenance": {
            "strategy_id": strategy_id,
            "ticker": ticker,
            "class_code": "crypto",
            "timeframe": "4h",
            "variants_count": variants,
            "data_end": "2026-10-01",
            "data_start": "2024-01-01",
        },
        "oos": {
            "mean_oos_pnl": mean,
            "ci_low": mean - 1.0,
            "ci_high": mean + 1.0,
            "p_value": p_value,
            "oos_pnl_by_fold": series,
            "folds": len(series),
            "total_oos_trades": 60,
            "win_rate": 0.55,
            "profit_factor": 1.4,
        },
        "holdout": {"pnl": mean * 5, "trades": 14, "bars": 120},
        "chosen": {},
    })
    assert digest.get("ok") is True
    assert record_digest_clearance(digest, path=store) is not None


def _rows(**overrides) -> list[dict]:
    rows = []
    for i in range(8):
        rows.append({
            "strategy_id": f"s{i}",
            "p_value": 0.30,
            "variants_tried_lifetime": 20,
            "oos_pnl_by_fold": _folds(0.0, 12, seed=100 + i),
            "live_ok": True,
            "observe_ok": True,
            "checks": [],
            "blockers": [],
            **overrides,
        })
    return rows


# --- the universe ----------------------------------------------------------


def test_the_denominator_counts_lifetime_variants_not_just_the_batch():
    """Sullivan's rule, and the whole reason this function exists.

    Eight strategies in the report, each of which was drawn from a grid of twenty.
    The space the rules came from is 160, not 8, and a correction computed over 8 is
    too lenient by a factor of twenty.
    """
    out = batch_research_verdict(_rows(), samples=200, seed=1)
    assert out["strategies_in_batch"] == 8
    assert out["universe"] == 160
    assert out["notes"]["universe_uses_lifetime_variants"] is True


def test_universe_falls_back_to_this_runs_grid_when_lifetime_is_absent():
    rows = _rows()
    for row in rows:
        row.pop("variants_tried_lifetime")
        row["variants_count"] = 7
    out = batch_research_verdict(rows, samples=200, seed=1)
    assert out["universe"] == 56


def test_every_strategy_counts_at_least_one():
    """A strategy with no recorded grid must not shrink the denominator to zero."""
    rows = _rows()
    for row in rows:
        row.pop("variants_tried_lifetime")
        row.pop("variants_count", None)
    out = batch_research_verdict(rows, samples=200, seed=1)
    assert out["universe"] == 8


# --- the four verdicts -----------------------------------------------------


def test_a_dominant_strategy_passes_and_a_noise_batch_does_not():
    """The direction, pinned in both directions at once.

    `reject` in these tests is named from the test's point of view: the *null* was
    rejected, which means an edge was found. Reading it as "the strategy was rejected"
    inverts the gate completely, and it did: a batch of noise came back as
    `unstable_selection` and a batch with one dominant strategy came back as
    `no_batch_edge`. Both wrong, both passing whatever assertions were written after
    the mistake rather than before it.
    """
    noise = batch_research_verdict(_rows(), samples=400, seed=2)
    assert noise["reality_check"]["reject"] is False
    assert noise["verdict"] == "no_batch_edge"

    dominant = _rows()
    dominant[0]["oos_pnl_by_fold"] = _folds(6.0, 40, seed=7, noise=0.2)
    good = batch_research_verdict(dominant, samples=400, seed=2)
    assert good["reality_check"]["reject"] is True
    assert good["spa"]["reject"] is True
    assert good["verdict"] == "batch_edge_stable"


def test_all_four_methods_are_reported():
    out = batch_research_verdict(_rows(), samples=200, seed=1)
    for key in ("reality_check", "spa", "pbo"):
        assert key in out, f"{key} missing -- a silent omission reads as a pass"
        assert out[key].get("ok") is True


def test_a_batch_of_noise_is_reported_as_having_no_edge():
    rows = _rows()
    out = batch_research_verdict(rows, samples=400, seed=2)
    assert out["verdict"] == "no_batch_edge"


def test_a_batch_with_a_real_and_dominating_edge_passes():
    """One strategy dominant enough that its in-sample rank survives every split."""
    rows = _rows()
    rows[0]["oos_pnl_by_fold"] = _folds(6.0, 40, seed=7, noise=0.2)
    out = batch_research_verdict(rows, samples=400, seed=2)
    assert out["verdict"] == "batch_edge_stable"
    assert out["best_strategy"] == "s0"
    assert out["pbo"]["pbo"] < 0.5


def test_significance_without_a_stable_ranking_is_caught_by_pbo():
    """The failure no other test can see.

    Every strategy carries a real edge, so the Reality Check and SPA both reject the
    null and Holm passes too. But they are interchangeable, so *which* one was chosen
    is arbitrary: the in-sample winner lands out-of-sample below its in-sample rank
    more often than not. Reporting that as "unstable selection" is the entire reason
    PBO is in the family -- a batch can be significant and still be a coin toss.
    """
    rng = random.Random(11)
    rows = []
    for i in range(8):
        rows.append({
            "strategy_id": f"s{i}",
            "p_value": 0.001,
            "variants_tried_lifetime": 4,
            "oos_pnl_by_fold": [rng.gauss(0.5, 1.0) for _ in range(40)],
            "live_ok": True,
            "observe_ok": True,
            "checks": [],
            "blockers": [],
        })
    out = batch_research_verdict(rows, samples=400, seed=3)
    assert out["reality_check"]["reject"] is True
    assert out["pbo"]["pbo"] > 0.5
    assert out["verdict"] == "unstable_selection"


# --- unavailable evidence --------------------------------------------------


def test_a_batch_with_one_series_missing_is_still_evaluated():
    """Seven usable series out of eight is enough; dropping the batch is not.

    The first version of this test expected `not_evaluable` and was wrong about its
    own premise -- `not_evaluable` is for fewer than *two* series, and seven is a
    perfectly good batch. The count is reported so a reader can see what was left
    out rather than having to infer it.
    """
    rows = _rows()
    rows[0].pop("oos_pnl_by_fold")
    out = batch_research_verdict(rows, samples=200, seed=4)
    assert out["strategies_in_batch"] == 8
    assert out["strategies_with_series"] == 7
    assert out["verdict"] != "not_evaluable"
    assert out["reality_check"]["ok"] is True


def test_unevaluable_is_not_labelled_as_a_negative_result(tmp_path):
    """A store written before this field existed must not read as twenty refutations.

    `not_evaluable` and `no_batch_edge` are different findings, and conflating them
    turns a broken data path into research output. This failed on the live clearance
    snapshot: every one of the twenty strategies came back reporting `batch_no_edge`
    when in fact none of them had a per-fold series on record and no test had been
    run at all. Both outcomes fail closed; only one of them is honest.
    """
    store = tmp_path / "clearance.json"
    names = [f"s{i}" for i in range(5)]
    for name in names:
        _record(store, name, "BTCUSDT", folds=None, variants=6)
    # Strip the series, as an older clearance store would have none.
    payload = json.loads(store.read_text(encoding="utf-8"))
    for entry in payload["entries"].values():
        entry.pop("oos_pnl_by_fold", None)
    store.write_text(json.dumps(payload), encoding="utf-8")

    out = evaluate_many(names, path=store, policy=LiveReadinessPolicy())
    assert out["batch"]["verdict"] == "not_evaluable"
    reasons = {b["reason"] for row in out["rows"] for b in row["blockers"]}
    assert "batch_not_evaluable" in reasons
    assert "batch_no_edge" not in reasons
    for row in out["rows"]:
        assert row["live_ok"] is False


def test_a_measured_negative_is_labelled_as_such(tmp_path):
    """And the label is not diluted: a batch that really was tested and found
    nothing still says exactly that."""
    store = tmp_path / "clearance.json"
    names = [f"s{i}" for i in range(8)]
    for i, name in enumerate(names):
        _record(store, name, "BTCUSDT", folds=_folds(0.0, 12, seed=400 + i))
    out = evaluate_many(names, path=store, policy=LiveReadinessPolicy())
    assert out["batch"]["verdict"] == "no_batch_edge"
    reasons = {b["reason"] for row in out["rows"] for b in row["blockers"]}
    assert "batch_no_edge" in reasons
    assert "batch_not_evaluable" not in reasons


def test_a_batch_with_fewer_than_two_series_reports_not_evaluable():
    """The genuine unevaluable case: every method says so rather than vanishing.

    A missing test must not read as a passed one, so each reports `ok: false` with a
    reason instead of being omitted. Six surviving series is still a batch -- only
    fewer than two makes the family inapplicable, because a maximum over one strategy
    is not a multiplicity question.
    """
    rows = _rows()
    for row in rows[1:]:
        row.pop("oos_pnl_by_fold")
    out = batch_research_verdict(rows, samples=100, seed=4)
    assert out["verdict"] == "not_evaluable"
    assert out["strategies_in_batch"] == 8
    assert out["strategies_with_series"] == 1
    for key in ("reality_check", "spa", "pbo"):
        assert out[key]["ok"] is False
        assert out[key]["reason"] == "fewer_than_two_series_on_record"


def test_too_few_folds_is_not_treated_as_a_series():
    rows = _rows()
    rows[0]["oos_pnl_by_fold"] = [0.1, 0.2]
    out = batch_research_verdict(rows, samples=100, seed=4)
    assert out["strategies_with_series"] == 7


# --- wiring ----------------------------------------------------------------


def test_fold_series_reaches_the_gate_row(tmp_path):
    """A batch test that cannot reach the data is a batch test that cannot run."""
    store = tmp_path / "clearance.json"
    _record(store, "s1", "BTCUSDT", folds=_folds(0.2, 16, seed=3))
    row = evaluate_live_readiness("s1", ticker="BTCUSDT", path=store)
    assert len(row["oos_pnl_by_fold"]) == 16
    assert row["variants_tried_lifetime"] > 0
    assert row["variants_count"] > 0


def test_evaluate_many_runs_the_batch_verdict_and_blocks_on_it(tmp_path):
    store = tmp_path / "clearance.json"
    names = [f"s{i}" for i in range(10)]
    for i, name in enumerate(names):
        for ticker in ("BTCUSDT", "ETHUSDT"):
            _record(store, name, ticker, folds=_folds(0.0, 12, seed=200 + i))
    batch = evaluate_many(names, path=store, policy=LiveReadinessPolicy())
    assert batch["batch"]["verdict"] == "no_batch_edge"
    assert batch["batch"]["rows_adjusted"] == 10
    reasons = {b["reason"] for row in batch["rows"] for b in row["blockers"]}
    assert "batch_no_edge" in reasons
    for row in batch["rows"]:
        assert row["live_ok"] is False
        check = next(
            c for c in row["checks"] if c["name"].startswith("batch_")
        )
        assert check["ok"] is False
        assert check["detail"]["universe"] > 0


def test_batch_verdict_can_be_switched_off_and_is_visible(tmp_path):
    store = tmp_path / "clearance.json"
    names = [f"s{i}" for i in range(6)]
    for i, name in enumerate(names):
        _record(store, name, "BTCUSDT", folds=_folds(0.0, 12, seed=300 + i))
    out = evaluate_many(
        names,
        path=store,
        policy=LiveReadinessPolicy(
            require_batch_verdict=False,
            require_multiplicity_correction=False,
        ),
    )
    assert out["batch"] == {}
    assert not any(
        c["name"].startswith("batch_") for row in out["rows"] for c in row["checks"]
    )


def test_policy_defaults_are_the_strict_ones():
    """An agent driving this system will not switch a safeguard on."""
    pol = LiveReadinessPolicy()
    assert pol.require_batch_verdict is True
    assert pol.require_multiplicity_correction is True
    assert pol.max_pbo == 0.5
    assert pol.pbo_splits == 8
    assert pol.batch_bootstrap_samples >= 500


def test_shipped_config_declares_the_new_knobs():
    from pathlib import Path

    import yaml

    config = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "config" / "trading_bot.yaml")
        .read_text(encoding="utf-8")
    )
    block = config["trading"]["live_readiness"]
    assert block["require_batch_verdict"] is True
    assert block["max_pbo"] == 0.5
    pol = LiveReadinessPolicy.from_mapping(block)
    assert pol.require_batch_verdict is True
    assert pol.max_pbo == 0.5