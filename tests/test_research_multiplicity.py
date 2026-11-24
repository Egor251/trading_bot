"""A batch of strategies judged one at a time is a batch of multiple tests.

`evaluate_live_readiness` asks one question per strategy: does its confidence
interval clear zero? That is a correct test and it is also exactly the test you must
not run twenty-eight times without paying for it. At a 95% level a batch of 28
strategies built from pure noise produces ~1.4 intervals clear of zero on average,
and the unlucky run produces several. Ranking them and promoting the best is
selection on noise: the winner's interval looks convincing and means nothing.

`adjust_pvalues` sat in `engine/simulation.py` the whole time -- implemented,
tested, exported -- with no caller anywhere in the codebase. These tests pin it to
the one place that can see the whole set, because a correction applied per-strategy
is not a correction at all.

The property under test throughout: *identical evidence, opposite verdict* depending
only on how many other strategies were measured alongside it.
"""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

import json  # noqa: E402
import random  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402

from engine.research_digest import digest_walk_forward  # noqa: E402
from engine.research_gate import (  # noqa: E402
    LiveReadinessPolicy,
    apply_multiplicity,
    evaluate_live_readiness,
    evaluate_many,
    record_digest_clearance,
)
from engine.simulation import adjust_pvalues  # noqa: E402
from engine.walk_forward import block_bootstrap_mean_ci  # noqa: E402


def _row(strategy_id: str, p_value: float | None) -> dict:
    """A gate row shaped like the real thing, with every other check passing."""
    return {
        "strategy_id": strategy_id,
        "p_value": p_value,
        "live_ok": True,
        "observe_ok": True,
        "grade": "live",
        "score": 0.9,
        "checks": [],
        "blockers": [],
    }


def _batch(n: int, p: float, *, best: str = "lucky") -> list[dict]:
    """`n` noise rows at `p`, plus one more row also at `p`."""
    rows = [_row(f"noise{i}", p) for i in range(n)]
    rows.append(_row(best, p))
    return rows


# --- the correction itself -------------------------------------------------


def test_marginal_result_is_rejected_once_the_batch_size_is_accounted_for():
    """p = 0.04 is a pass alone and a failure among twenty.

    This is the whole point. Without the correction the gate reports a clear
    winner; with it, the same row is blocked with the reason recorded.
    """
    rows = _batch(20, 0.30, best="lucky")
    rows[-1]["p_value"] = 0.04
    out = apply_multiplicity(rows, alpha=0.05, method="holm")

    lucky = next(r for r in out["rows"] if r["strategy_id"] == "lucky")
    assert lucky["p_value"] == 0.04
    assert lucky["p_value_adjusted"] >= 0.05
    assert lucky["live_ok"] is False
    assert lucky["observe_ok"] is False
    assert "lucky" in out["multiplicity_fail"]


def test_identical_evidence_flips_on_batch_size_alone():
    """The same row, unchanged, judged alone and judged among nineteen others."""
    alone = apply_multiplicity([_row("solo", 0.04)], alpha=0.05, method="holm")
    crowded_rows = _batch(19, 0.30, best="solo")
    crowded_rows[-1]["p_value"] = 0.04
    crowded = apply_multiplicity(crowded_rows, alpha=0.05, method="holm")

    solo_row = alone["rows"][0]
    crowded_row = next(r for r in crowded["rows"] if r["strategy_id"] == "solo")

    assert solo_row["p_value"] == crowded_row["p_value"] == 0.04
    assert alone["multiplicity_ok"] == ["solo"]
    assert crowded["multiplicity_ok"] == []
    assert crowded["multiplicity_batch_size"] == 20
    assert alone["multiplicity_batch_size"] == 1
    # Same evidence, opposite verdict, and the only difference between the two runs
    # is how many other strategies were measured at the same time.
    assert solo_row["p_value_adjusted"] == 0.04
    assert crowded_row["p_value_adjusted"] > 0.05


def test_correction_never_promotes_a_row_that_failed():
    """The pass can only withhold a verdict, never grant one.

    Demoting a row the per-strategy pass had already cleared is safe. Promoting one
    would mean the correction is doing something other than being more demanding,
    and that is the failure mode that matters: a correction that quietly rescues
    weak evidence is worse than no correction, because it looks rigorous.
    """
    out = apply_multiplicity(_batch(20, 0.30), alpha=0.05, method="holm")
    for row in out["rows"]:
        was_live = row["live_ok"]
        assert was_live is False
        adjusted = row["p_value_adjusted"]
        assert adjusted is None or adjusted >= 0.05


def test_strong_evidence_survives_a_large_batch():
    """Correction is more demanding, not prohibitive.

    A p-value of 0.0001 among 28 strategies still clears the corrected bar. If this
    fails, the correction has been made so harsh that real findings cannot pass, and
    the right response is to reject less often elsewhere -- not to weaken this.
    """
    rows = [_row(f"s{i}", 0.80) for i in range(27)]
    rows.append(_row("strong", 0.0001))
    out = apply_multiplicity(rows, alpha=0.05, method="holm")

    assert out["multiplicity_ok"] == ["strong"]
    strong = out["rows"][-1]
    assert strong["live_ok"] is True
    assert strong["p_value_adjusted"] < 0.05


def test_unknown_p_value_blocks_rather_than_passing():
    """A clearance written before p-values existed has unknown evidence.

    Treating missing evidence as a pass would let every legacy record through
    untouched, which is precisely the population that cannot be corrected. It is
    reported as unknown rather than given a fabricated number, so the report cannot
    be misread as "measured and insignificant".
    """
    out = apply_multiplicity([_row("legacy", None)], alpha=0.05)

    row = out["rows"][0]
    assert row["live_ok"] is False
    assert row["p_value_adjusted"] is None
    assert out["multiplicity_unknown"] == 1
    assert "multiplicity_unknown_p_value" in {b["reason"] for b in row["blockers"]}


def test_out_of_range_p_value_counts_as_unknown():
    """A malformed p-value is not evidence and is not silently clipped into range."""
    for bad in (-0.1, 1.5):
        out = apply_multiplicity([_row("bad", bad)], alpha=0.05)
        assert out["multiplicity_unknown"] == 1
        assert out["rows"][0]["live_ok"] is False


def test_measured_and_unmeasured_failures_are_named_apart():
    """A name that covers both stops carrying information.

    Every clearance written before p-values existed reports the unknown case, so
    collapsing it into `multiplicity` prints that word on all twenty rows of a real
    snapshot -- and a genuine measured failure later has nowhere to stand out.
    """
    measured = apply_multiplicity([_row("measured", 0.30)])
    unknown = apply_multiplicity([_row("unknown", None)])

    measured_names = {c["name"] for c in measured["rows"][0]["checks"]}
    unknown_names = {c["name"] for c in unknown["rows"][0]["checks"]}

    assert measured_names == {"multiplicity"}
    assert unknown_names == {"multiplicity_unknown_p_value"}
    # Both still block, and both record a blocker under the same reason.
    assert measured["rows"][0]["live_ok"] is False
    assert unknown["rows"][0]["live_ok"] is False
    assert {b["reason"] for b in unknown["rows"][0]["blockers"]} == {"multiplicity_unknown_p_value"}


def test_unknown_rows_do_not_inflate_the_correction_for_the_rest():
    """Only measured strategies count toward N.

    Counting unmeasurable rows would penalise a batch for its own missing data --
    and would make the correction tighter precisely when the evidence is thinnest.
    """
    known = [_row(f"k{i}", 0.0001) for i in range(10)]
    with_ghosts = known + [_row(f"g{i}", None) for i in range(10)]
    all_measured = [_row(f"k{i}", 0.0001) for i in range(10)] + [
        _row(f"m{i}", 0.0001) for i in range(10)
    ]

    tight = apply_multiplicity(known, alpha=0.05, method="bonferroni")
    loose = apply_multiplicity(with_ghosts, alpha=0.05, method="bonferroni")
    double = apply_multiplicity(all_measured, alpha=0.05, method="bonferroni")

    k0_tight = next(r for r in tight["rows"] if r["strategy_id"] == "k0")
    k0_loose = next(r for r in loose["rows"] if r["strategy_id"] == "k0")
    k0_double = next(r for r in double["rows"] if r["strategy_id"] == "k0")

    assert k0_tight["multiplicity_batch_size"] == 10
    assert k0_loose["multiplicity_batch_size"] == 20
    # Ten unmeasurable rows change nothing; ten additional *measured* rows double
    # the penalty. The batch size reported in the row still reflects the full set,
    # so a reader can see what was in the batch even though only N of it counted.
    assert k0_loose["p_value_adjusted"] == k0_tight["p_value_adjusted"]
    assert k0_double["p_value_adjusted"] == pytest.approx(0.0001 * 20)
    assert tight["multiplicity_measured"] == loose["multiplicity_measured"] == 10


def test_holm_is_no_more_harsh_than_bonferroni():
    """Holm shares Bonferroni's family-wise error and assumes less.

    The strategies in a batch share price data, so independence is false, and Holm
    is the correction that does not need it. It is also never the more conservative
    of the two, which is the property that makes it the better default here.
    """
    ps = [0.041, 0.048, 0.012, 0.30]
    bonf = apply_multiplicity([_row(f"b{i}", p) for i, p in enumerate(ps)], method="bonferroni")
    holm = apply_multiplicity([_row(f"b{i}", p) for i, p in enumerate(ps)], method="holm")

    for b_row, h_row in zip(bonf["rows"], holm["rows"]):
        assert h_row["p_value_adjusted"] <= b_row["p_value_adjusted"] + 1e-12


def test_unknown_method_falls_back_to_holm_and_reports_it():
    """A typo in config must not disable the correction.

    Falling back silently would leave a gate that looks protected and is not; the
    report says which method actually ran so the discrepancy is visible.
    """
    out = apply_multiplicity([_row("x", 0.001)], method="not-a-method")
    assert out["multiplicity_method"] == "holm"
    assert out["rows"][0]["p_value_adjusted"] is not None


def test_empty_batch_reports_the_same_keys():
    """A consumer should not have to branch on whether there was anything to correct."""
    out = apply_multiplicity([])
    assert out["multiplicity_corrected"] is False
    assert out["multiplicity_batch_size"] == 0
    assert out["multiplicity_ok"] == []
    assert out["multiplicity_fail"] == []
    assert out["rows"] == []


def test_correction_is_recorded_as_a_normal_gate_check():
    """Same shape as every other check, so existing consumers need no changes."""
    out = apply_multiplicity(_batch(20, 0.30))
    row = out["rows"][0]
    checks = {c["name"]: c for c in row["checks"]}
    assert "multiplicity" in checks
    check = checks["multiplicity"]
    assert check["ok"] is False
    assert check["live"] is True and check["observe"] is True
    detail = check["detail"]
    assert detail["batch_size"] == 21
    assert detail["method"] == "holm"
    assert detail["alpha"] == 0.05
    assert detail["known_p_values"] == 21


def test_corrected_p_agrees_with_adjust_pvalues():
    """The gate must not reimplement the correction; it must call the tested one."""
    ps = [0.041, 0.048, 0.012, 0.30, 0.9]
    rows = [_row(f"s{i}", p) for i, p in enumerate(ps)]
    out = apply_multiplicity(rows, method="bonferroni")
    expected = adjust_pvalues(ps, "bonferroni")
    actual = [r["p_value_adjusted"] for r in out["rows"]]
    assert actual == expected


# --- the p-value the correction consumes -----------------------------------


def test_p_value_is_small_for_a_real_signal_and_large_for_noise():
    """The null-recentred bootstrap must separate signal from noise.

    Both sides matter and they fail independently: a p-value that is always small
    would reject every strategy, one that is always large would accept every
    strategy, and both would leave the multiplicity arithmetic looking fine.
    """
    strong = block_bootstrap_mean_ci([1.0, 2, 3, 4, 5] * 40, samples=600, seed=1)
    rng = random.Random(7)
    noise = block_bootstrap_mean_ci([rng.gauss(0, 1) for _ in range(4000)], samples=600, seed=2)

    assert strong["bootstrap_mean"] == 3.0
    assert strong["p_value"] < 0.01
    assert noise["p_value"] > 0.5


def test_p_value_is_calibrated_at_its_nominal_rate():
    """On iid noise, p < 0.05 about 5% of the time.

    Not a regression test against a previous number -- a check that the p-value is
    a probability and not merely a monotone transform of the mean. A shortcut like
    asking where the observed mean falls inside its own bootstrap distribution
    passes the signal test above and fails this one badly, because that
    distribution is centred on the observed mean by construction.
    """
    rejects = 0
    runs = 300
    for i in range(runs):
        rng = random.Random(1000 + i)
        series = [rng.gauss(0, 1) for _ in range(1500)]
        if block_bootstrap_mean_ci(series, samples=200, seed=i)["p_value"] < 0.05:
            rejects += 1
    rate = rejects / runs
    assert 0.01 < rate < 0.12, f"false-positive rate {rate} is not near 0.05"


def test_p_value_detects_a_shifted_mean():
    rate = sum(
        1
        for i in range(200)
        if block_bootstrap_mean_ci(
            [random.Random(5000 + i).gauss(0.35, 1.0) for _ in range(1500)],
            samples=200,
            seed=i,
        )["p_value"]
        < 0.05
    )
    assert rate / 200 > 0.95


def test_p_value_floor_is_never_exactly_zero():
    """A p-value of zero asserts more than any finite sample supports.

    It also corrupts the correction downstream, which multiplies by N. The
    `(1 + count) / (1 + n)` form keeps a strong signal small without ever claiming
    certainty.
    """
    result = block_bootstrap_mean_ci([5.0] * 300, samples=500, seed=3)
    assert result["p_value"] > 0.0


def test_degenerate_series_report_no_evidence():
    """Zero variance and empty input are not opportunities to pass.

    A single observation produces a zero-width interval that does not cross zero --
    an interval that clears `require_ci_excludes_zero` while carrying no information
    at all. It has to be stopped by the p-value instead.
    """
    single = block_bootstrap_mean_ci([5.0], samples=10)
    assert single["ci_low"] == single["ci_high"] == 5.0
    assert single["p_value"] == 1.0

    zeros = block_bootstrap_mean_ci([0.0] * 500, samples=200, seed=3)
    assert zeros["p_value"] == 1.0

    empty = block_bootstrap_mean_ci([], samples=10)
    assert empty["p_value"] == 1.0


def test_single_observation_is_blocked_after_correction():
    """End to end: the one case where CI and p-value disagree, in the right direction."""
    rows = apply_multiplicity([_row("one_trade", 1.0)])
    assert rows["rows"][0]["live_ok"] is False


# --- the wiring ------------------------------------------------------------


def _store_record(store, strategy_id: str, ticker: str, *, p_value: float, mean: float):
    digest = digest_walk_forward(
        {
            "provenance": {
                "strategy_id": strategy_id,
                "ticker": ticker,
                "class_code": "crypto",
                "timeframe": "4h",
                "variants_count": 8,
                # Relative to now: a hardcoded date turns into an expired clearance
                # after max_age_days, and these tests assert live_ok. That is a
                # time-bomb, not a failure of the multiplicity logic under test.
                "data_end": datetime.now(timezone.utc).date().isoformat(),
                "data_start": "2024-01-01",
            },
            "oos": {
                "mean_oos_pnl": mean,
                "ci_low": mean * 0.5,
                "ci_high": mean * 1.5,
                "p_value": p_value,
                "folds": 4,
                "total_oos_trades": 60,
                "win_rate": 0.55,
                "profit_factor": 1.4,
            },
            "holdout": {"pnl": mean * 5, "trades": 14, "bars": 120},
            "chosen": {},
        }
    )
    assert digest.get("ok") is True
    assert digest.get("p_value") == p_value
    assert record_digest_clearance(digest, path=store) is not None


def test_p_value_survives_the_digest_and_the_clearance_store(tmp_path):
    """A value that does not reach the gate is a value that cannot correct anything."""
    store = tmp_path / "clearance.json"
    _store_record(store, "s1", "BTCUSDT", p_value=0.0033, mean=0.85)

    row = evaluate_live_readiness("s1", ticker="BTCUSDT", path=store)
    assert row["p_value"] == 0.0033
    assert row["metrics"]["p_value"] == 0.0033


def test_record_without_p_value_yields_unknown_not_a_default(tmp_path):
    """Old clearance files must not be invented a p-value of 0.05.

    This is what a clearance written before the field existed looks like on disk,
    and it is the population that most needs correcting: those records predate any
    way to judge them.
    """
    store = tmp_path / "clearance.json"
    _store_record(store, "s1", "BTCUSDT", p_value=0.0033, mean=0.85)

    payload = json.loads(store.read_text(encoding="utf-8"))
    for entry in payload["entries"].values():
        entry["p_value"] = None
        entry.pop("p_value", None)
    store.write_text(json.dumps(payload), encoding="utf-8")

    row = evaluate_live_readiness("s1", ticker="BTCUSDT", path=store)
    assert row["p_value"] is None


def test_evaluate_many_corrects_the_batch(tmp_path):
    """The batch path is the only place that knows N, so that is where it happens."""
    store = tmp_path / "clearance.json"
    names = [f"s{i}" for i in range(12)]
    for i, name in enumerate(names):
        for ticker in ("BTCUSDT", "ETHUSDT"):
            p = 0.0033 if i == 3 else 0.60
            _store_record(store, name, ticker, p_value=p, mean=0.85 if i == 3 else 0.01)

    batch = evaluate_many(
        names,
        path=store,
        # The batch verdict runs on the same rows and also blocks. This test is about
        # the per-hypothesis correction, so the other mechanism is switched off --
        # the same isolation `require_multiplicity_correction=False` uses below.
        policy=LiveReadinessPolicy(require_batch_verdict=False),
    )

    assert batch["multiplicity"]["multiplicity_batch_size"] == 12
    assert batch["multiplicity"]["multiplicity_measured"] == 12
    assert batch["multiplicity"]["multiplicity_method"] == "holm"
    assert batch["multiplicity"]["multiplicity_ok"] == ["s3"]

    s3 = next(r for r in batch["rows"] if r["strategy_id"] == "s3")
    assert s3["p_value_adjusted"] < 0.05
    assert s3["live_ok"] is True

    for name in names:
        if name == "s3":
            continue
        row = next(r for r in batch["rows"] if r["strategy_id"] == name)
        assert row["p_value_adjusted"] >= 0.05
        assert row["live_ok"] is False
        assert "multiplicity" in {b["reason"] for b in row["blockers"]}


def test_evaluate_many_can_skip_the_correction(tmp_path):
    """The escape hatch stays available and is visible in the output when used."""
    store = tmp_path / "clearance.json"
    names = [f"s{i}" for i in range(12)]
    for i, name in enumerate(names):
        for ticker in ("BTCUSDT", "ETHUSDT"):
            p = 0.02 if i == 3 else 0.60
            _store_record(store, name, ticker, p_value=p, mean=0.5 if i == 3 else 0.01)

    skipped = evaluate_many(
        names,
        path=store,
        policy=LiveReadinessPolicy(
            require_multiplicity_correction=False,
            require_batch_verdict=False,
        ),
    )
    assert skipped["multiplicity"].get("multiplicity_corrected") is None
    s3 = next(r for r in skipped["rows"] if r["strategy_id"] == "s3")
    assert s3.get("p_value_adjusted") is None
    assert s3["live_ok"] is True


def test_policy_round_trips_the_multiplicity_settings():
    """These are YAML knobs (`trading.live_readiness`), so they must survive config."""
    pol = LiveReadinessPolicy.from_mapping(
        {
            "require_multiplicity_correction": False,
            "multiplicity_method": "bonferroni",
            "multiplicity_alpha": 0.01,
        }
    )
    assert pol.require_multiplicity_correction is False
    assert pol.multiplicity_method == "bonferroni"
    assert pol.multiplicity_alpha == 0.01
    assert pol.to_dict()["multiplicity_method"] == "bonferroni"


def test_default_policy_corrects_with_holm():
    """The shipped default has to be the corrected one.

    A multiplicity feature that defaults to off is an opt-in footnote, and an
    agent driving this system will never turn it on.
    """
    pol = LiveReadinessPolicy()
    assert pol.require_multiplicity_correction is True
    assert pol.multiplicity_method == "holm"
    assert pol.multiplicity_alpha == 0.05
