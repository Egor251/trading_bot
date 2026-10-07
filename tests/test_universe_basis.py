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
    """The CONFIG_SCHEMA a plugin exposes, however it exposes it."""
    mod = importlib.import_module(f"strategies.{name}.plugin")
    direct = getattr(mod, "CONFIG_SCHEMA", None)
    if isinstance(direct, dict):
        return direct
    for attr in dir(mod):
        obj = getattr(mod, attr)
        found = getattr(obj, "CONFIG_SCHEMA", None)
        if isinstance(found, dict) and found:
            return found
    return None


def test_the_schema_is_reachable_on_some_but_not_all_plugins():
    """The live gap, stated as a measurement rather than a suspicion.

    A previous inventory script reported 4-6 numeric parameters for 16 of 23
    plugins and 0 for the rest, and the 0s were read as "no parameter surface".
    They are not: those plugins simply do not expose the schema, so the available
    space cannot be computed for them at all.
    """
    with_schema = [n for n in PLUGINS if _schema_for(n)]
    without = [n for n in PLUGINS if not _schema_for(n)]
    assert with_schema, "no plugin exposes a schema at all -- worse than partial"
    assert without, (
        "every plugin now exposes a schema -- this test can be retired, and the "
        f"denominator is now honest for all of them. Currently without: {without}"
    )


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