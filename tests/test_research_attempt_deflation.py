"""Thirty research runs, and the report said one.

`record_digest_clearance` assigned into `entries[key]` outright, so each run
discarded every prior evaluation of the same strategy/ticker. The surviving entry
reported that run's `variants_count` as if it were the entire research history.

The multiple-comparisons bias grows with the number of attempts actually made --
picking the best of N noisy estimates is optimistically biased, and the bias
grows with N. Discarding the attempts therefore did not merely lose a number, it
made a strategy evaluated thirty times indistinguishable from one evaluated
once. That is the specific illusion a research report must not tell.

What this cannot do is undo the bias. The earlier runs left no record to
recompute from, so the honest scope is disclosure: restore the ability to see
how large it got.

The second half of the file is the digest-side check. A counter that accumulates
in a JSON file but never reaches a reader is the same defect as everything else
in this lineage -- a number computed and read by nobody.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.research_gate import (  # noqa: E402
    clearance_hits_for_strategy,
    load_clearance,
    record_digest_clearance,
)


def _digest(**overrides):
    base = {
        "ok": True,
        "strategy_id": "supertrend_follow",
        "ticker": "BTC/USDT",
        "holdout_bars": 30,
        "holdout_pnl": 12.5,
        "holdout_trades": 9,
        "robust_sign_match": True,
        "mean_oos_pnl": 4.2,
        "ci_crosses_zero": False,
        "ci_low": 1.1,
        "ci_high": 7.3,
        "profit_factor": 1.4,
        "total_oos_trades": 40,
        "timeframe": "1h",
        "variant_mode": "optimize",
        "variants_count": 20,
        "chosen": {"window": 10, "num_std": 2.5},
        "data_start": "2026-09-01T00:00:00+00:00",
        "data_end": "2026-10-02T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def test_a_first_evaluation_starts_at_one(tmp_path: Path) -> None:
    path = tmp_path / "clearance.json"
    record_digest_clearance(_digest(), path=path)
    entry = load_clearance(path)["entries"]["supertrend_follow::BTCUSDT"]
    assert entry["evaluations_count"] == 1
    assert entry["variants_tried_lifetime"] == 20


def test_repeated_evaluation_accumulates_instead_of_overwriting(
    tmp_path: Path,
) -> None:
    """The defect. Three runs, and the file must admit there were three."""
    path = tmp_path / "clearance.json"
    for index in range(3):
        record_digest_clearance(
            _digest(holdout_pnl=float(index)), path=path
        )
    entry = load_clearance(path)["entries"]["supertrend_follow::BTCUSDT"]
    assert entry["evaluations_count"] == 3, (
        "the earlier evaluations were discarded, so a thrice-tested strategy "
        "was indistinguishable from a once-tested one"
    )
    assert entry["variants_tried_lifetime"] == 60


def test_the_latest_run_still_wins_for_its_own_fields(tmp_path: Path) -> None:
    """Accumulation must not turn the entry into a running average."""
    path = tmp_path / "clearance.json"
    record_digest_clearance(_digest(holdout_pnl=1.0), path=path)
    record_digest_clearance(_digest(holdout_pnl=99.0), path=path)
    entry = load_clearance(path)["entries"]["supertrend_follow::BTCUSDT"]
    assert entry["holdout_pnl"] == 99.0
    assert entry["evaluations_count"] == 2


def test_per_run_provenance_is_not_conflated_with_the_lifetime(
    tmp_path: Path,
) -> None:
    """`variants_count` is this run's grid; that has not changed."""
    path = tmp_path / "clearance.json"
    record_digest_clearance(_digest(variants_count=8), path=path)
    record_digest_clearance(_digest(variants_count=12), path=path)
    entry = load_clearance(path)["entries"]["supertrend_follow::BTCUSDT"]
    assert entry["variants_count"] == 12
    assert entry["variants_tried_lifetime"] == 20


def test_separate_strategies_do_not_share_a_budget(tmp_path: Path) -> None:
    path = tmp_path / "clearance.json"
    record_digest_clearance(_digest(), path=path)
    record_digest_clearance(_digest(strategy_id="momentum_roc"), path=path)
    entries = load_clearance(path)["entries"]
    assert entries["supertrend_follow::BTCUSDT"]["evaluations_count"] == 1
    assert entries["momentum_roc::BTCUSDT"]["evaluations_count"] == 1


def test_the_counter_reaches_a_reader(tmp_path: Path) -> None:
    """Accumulating in a file nobody reads is the same defect, one layer up."""
    path = tmp_path / "clearance.json"
    for _ in range(4):
        record_digest_clearance(_digest(), path=path)
    hits = clearance_hits_for_strategy(
        "supertrend_follow", ticker="BTC/USDT", path=path
    )
    assert len(hits) == 1
    assert hits[0]["evaluations_count"] == 4
    assert hits[0]["variants_tried_lifetime"] == 80


def test_a_hand_written_legacy_entry_does_not_crash_the_gate(
    tmp_path: Path,
) -> None:
    """Entries written before these fields existed must still load."""
    path = tmp_path / "clearance.json"
    path.write_text(
        json.dumps(
            {
                "entries": {
                    "old::BTCUSDT": {
                        "strategy_id": "old",
                        "ticker": "BTCUSDT",
                        "holdout_bars": 30,
                        "robust_sign_match": False,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    record_digest_clearance(_digest(strategy_id="old"), path=path)
    entry = load_clearance(path)["entries"]["old::BTCUSDT"]
    assert entry["evaluations_count"] == 1, "a legacy entry must count as one"
    assert entry["variants_tried_lifetime"] == 20


def test_an_unusable_prior_entry_is_replaced_not_trusted(
    tmp_path: Path,
) -> None:
    path = tmp_path / "clearance.json"
    path.write_text(
        json.dumps({"entries": {"x::BTCUSDT": "not-a-mapping"}}),
        encoding="utf-8",
    )
    record_digest_clearance(_digest(strategy_id="x"), path=path)
    entry = load_clearance(path)["entries"]["x::BTCUSDT"]
    assert entry["evaluations_count"] == 1


def test_a_digest_without_a_variant_count_does_not_inflate_the_total(
    tmp_path: Path,
) -> None:
    """Missing provenance must read as zero, not as an unknown added silently."""
    path = tmp_path / "clearance.json"
    record_digest_clearance(_digest(variants_count=None), path=path)
    record_digest_clearance(_digest(variants_count=None), path=path)
    entry = load_clearance(path)["entries"]["supertrend_follow::BTCUSDT"]
    assert entry["evaluations_count"] == 2
    assert entry["variants_tried_lifetime"] == 0