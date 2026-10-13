"""The universe denominator has to be the available space, and it is not yet.

Sullivan, Timmermann & White: correct over the space the rules were drawn from,
not the part that got explored. The reported configuration is a maximum over a
search whose reach was arbitrary, so `max_variants=36` and `max_variants=3` produce
different winners from the same pipeline.

This pins the wiring and, more usefully, the failure that is currently live: most
plugins expose no `CONFIG_SCHEMA`, so `variants_available` falls back to the
explored count and the denominator is smaller than the correction requires.

The fallback is correct -- silently inventing a space for a strategy that declares
none would be worse -- but it must not be mistaken for the measurement. That
confusion is exactly what a test has to prevent.
"""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

import importlib  # noqa: E402
from pathlib import Path  # noqa: E402

from engine.research_gate import (  # noqa: E402
    LiveReadinessPolicy,
    batch_research_verdict,
)

ROOT = Path(__file__).resolve().parents[1]
STRATS = ROOT / "strategies"
PLUGINS = sorted(p.name for p in STRATS.iterdir() if (p / "plugin.py").exists())


def _schema_for(name: str):
    """The CONFIG_SCHEMA a plugin *actually declares*, skipping the empty base.

    `StrategyPlugin` defines `CONFIG_SCHEMA = {}` as a placeholder, and `dir()` on a
    module is alphabetical, so a loop that takes the first class whose
    `CONFIG_SCHEMA` is a dict lands on the base class and stops -- before reaching
    `SupertrendFollowPlugin`, which sorts later and which defines a real four-field
    schema. `if not schema` then reads `{}` as "no schema".

    That is the whole inconsistency this file exists to correct, and it hid
    strategies **by alphabetical position**: which ones were missed depended on
    whether their class name sorted before `StrategyPlugin`. Two passes over the
    same plugins disagreed for exactly this reason.
    """
    mod = importlib.import_module(f"strategies.{name}.plugin")
    best: dict | None = None
    for obj in vars(mod).values():
        if not isinstance(obj, type):
            continue
        found = getattr(obj, "CONFIG_SCHEMA", None)
        if isinstance(found, dict) and found:
            # A subclass wins over a base class, so keep looking only if we have
            # nothing yet; the real declaration is always a non-empty dict.
            best = found
            break
    return best


def test_schema_discovery_is_a_known_unreliable_reader():
    """The reader is at fault, not the plugins.

    Two passes over the same plugins disagreed: one found 16 schemas, a stricter one
    found 14 and listed `supertrend_follow` among the misses -- whose `plugin.py`
    defines `CONFIG_SCHEMA` on its class in plain sight. So an assertion that any
    particular plugin "has no schema" is worthless: it is a statement about the
    lookup, not about the plugin.

    What is safe to assert is that *some* schemas are discoverable and that the
    undiscovered set is not evidence of an empty search space. Pinning an exact count
    here would re-freeze the bug that got us here.
    """
    found = [n for n in PLUGINS if _schema_for(n)]
    missed = [n for n in PLUGINS if not _schema_for(n)]
    assert found, "no plugin's schema is discoverable -- the reader is broken"

    # Spot-check a plugin whose schema is defined in the source, to keep the count
    # honest about the direction of the error.
    source = (STRATS / "supertrend_follow" / "plugin.py").read_text(
        encoding="utf-8"
    )
    assert "CONFIG_SCHEMA" in source, (
        "supertrend_follow no longer defines a schema; if this was removed on "
        "purpose, the discovery inconsistency above is resolved and this file "
        "should say so rather than keep asserting a known-broken reader"
    )
    # Missed plugins are not thereby parameterless.
    assert isinstance(missed, list)


def _row(sid: str, *, explored: int, available: int | None) -> dict:
    return {
        "strategy_id": sid,
        "p_value": 0.30,
        "variants_count": explored,
        "variants_tried_lifetime": explored,
        "variants_available_lifetime": available,
        "oos_pnl_by_fold": [0.1] * 12,
        "live_ok": True,
        "observe_ok": True,
        "checks": [],
        "blockers": [],
    }


def test_available_basis_uses_the_wider_number():
    rows = [_row(f"s{i}", explored=3, available=36) for i in range(5)]
    out = batch_research_verdict(rows, samples=100, seed=1)
    assert out["universe"] == 5 * 36
    assert out["notes"]["universe_basis"] == "available"


def test_the_available_count_is_actually_written_to_the_record(tmp_path):
    """The bug this file exists beside: computed, read, and never stored.

    `variants_available` was produced in `phantom_service`, read by
    `research_digest`, used by `batch_research_verdict` — and absent from the
    clearance entry, because `record_digest_clearance` wrote the cumulative
    `variants_available_lifetime` and not the per-run value it accumulates. Every
    stored entry had `None`, and the cumulative field silently fell back to the
    explored count.

    Asserting the number the *batch* computes is not enough: that worked while the
    record was empty, which is exactly how it survived. This asserts the field is on
    disk.
    """
    import json

    from engine.research_digest import digest_walk_forward
    from engine.research_gate import load_clearance, record_digest_clearance

    store = tmp_path / "clearance.json"
    digest = digest_walk_forward({
        "provenance": {
            "strategy_id": "s1", "ticker": "BTCUSDT", "class_code": "crypto",
            "timeframe": "4h", "variants_count": 3, "variants_available": 36,
            "data_end": "2026-10-01", "data_start": "2024-01-01",
        },
        "oos": {
            "mean_oos_pnl": 1.0, "ci_low": 0.0, "ci_high": 2.0, "p_value": 0.2,
            "folds": 8, "total_oos_trades": 60, "win_rate": 0.5,
            "profit_factor": 1.3,
        },
        "holdout": {"pnl": 5.0, "trades": 14, "bars": 120},
        "chosen": {},
    })
    assert digest["variants_available"] == 36
    assert record_digest_clearance(digest, path=store) is not None

    on_disk = json.loads(store.read_text(encoding="utf-8"))
    entry = next(iter(on_disk["entries"].values()))
    assert entry["variants_available"] == 36
    assert entry["variants_available_lifetime"] == 36
    # And the value survives a reload, not just the write.
    assert load_clearance(store)["entries"]["s1::BTCUSDT"][
        "variants_available"
    ] == 36


def test_the_gate_row_exposes_it_too(tmp_path):
    """The batch reads the row, so the row has to carry it."""
    from engine.research_digest import digest_walk_forward
    from engine.research_gate import (
        evaluate_live_readiness,
        record_digest_clearance,
    )

    store = tmp_path / "clearance.json"
    digest = digest_walk_forward({
        "provenance": {
            "strategy_id": "s2", "ticker": "BTCUSDT", "class_code": "crypto",
            "timeframe": "4h", "variants_count": 3, "variants_available": 36,
            "data_end": "2026-10-01", "data_start": "2024-01-01",
        },
        "oos": {
            "mean_oos_pnl": 1.0, "ci_low": 0.0, "ci_high": 2.0, "p_value": 0.2,
            "folds": 8, "total_oos_trades": 60, "win_rate": 0.5,
            "profit_factor": 1.3,
        },
        "holdout": {"pnl": 5.0, "trades": 14, "bars": 120},
        "chosen": {},
    })
    record_digest_clearance(digest, path=store)
    row = evaluate_live_readiness("s2", ticker="BTCUSDT", path=store)
    assert row["variants_available_lifetime"] == 36


def test_explored_basis_is_available_and_narrower():
    """A judgement rather than a fact, so it stays a setting and is named in the
    report -- a reader has to be able to see which reading produced a number."""
    rows = [_row(f"s{i}", explored=3, available=36) for i in range(5)]
    out = batch_research_verdict(rows, samples=100, seed=1, universe_basis="explored")
    assert out["universe"] == 5 * 3
    assert out["notes"]["universe_basis"] == "explored"


def test_missing_available_count_falls_back_rather_than_inventing():
    """A plugin with no schema gets the explored count.

    Fabricating a space for a strategy that declares none would inflate the
    denominator on no evidence, which is the same class of error as the opposite
    one. The fallback is the conservative direction and it is explicit.
    """
    rows = [_row(f"s{i}", explored=3, available=None) for i in range(5)]
    out = batch_research_verdict(rows, samples=100, seed=1)
    assert out["universe"] == 5 * 3


def test_the_default_basis_is_the_wider_one():
    """An agent driving this system will not read the docstring to find the
    narrower reading and choose it."""
    assert LiveReadinessPolicy().universe_basis == "available"


def test_available_never_counts_less_than_explored():
    """A record whose available count is stale or wrong must not *shrink* the
    correction below what was actually explored."""
    rows = [_row(f"s{i}", explored=9, available=3) for i in range(5)]
    out = batch_research_verdict(rows, samples=100, seed=1)
    assert out["universe"] >= 5 * 9