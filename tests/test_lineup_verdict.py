"""The lineup experiment must not quietly stop measuring the lineup.

The value of `tools/lineup_verdict.py` is that it judges the strategies the gate
actually judges. Two ways that breaks silently:

  - the hardcoded `DEFAULT_LINEUP` drifts away from what the repo ships, so the tool
    measures a subset and reports it as the lineup;
  - `FOLDS` drops below what CSCV needs, and PBO returns `insufficient_series` while
    the run still prints a verdict block that looks like a result.

The second one is what actually happened: at 5 folds H3 could not be tested at all,
and the tool reported "PBO: not measured" in a section formatted exactly like the ones
that were measured. A test that cannot run must not look like a test that did.
"""

from __future__ import annotations

from pathlib import Path

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

import pytest  # noqa: E402
import yaml  # noqa: E402

from engine.simulation import cscv_pbo  # noqa: E402
from tools.lineup_verdict import DEFAULT_LINEUP, FOLDS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "trading_bot.yaml"


def _plugin_ids() -> set[str]:
    """Every strategy plugin the repo ships, from the strategies directory."""
    root = ROOT / "strategies"
    if not root.is_dir():
        return set()
    return {p.name for p in root.iterdir() if p.is_dir() and (p / "plugin.py").exists()}


def test_every_named_strategy_ships_as_a_plugin():
    """A name in the lineup that no plugin provides measures nothing."""
    shipped = _plugin_ids()
    if not shipped:
        pytest.skip("strategy plugin directory not found")
    missing = [name for name in DEFAULT_LINEUP if name not in shipped]
    assert not missing, f"lineup names with no plugin: {missing}"


def test_the_live_roster_is_covered_by_the_lineup():
    """Anything the engine would arm live must be inside what gets judged.

    `ensemble.strategies` ships with one member while the gate judges twenty, so the
    reverse direction -- roster inside lineup -- is the one that matters: a strategy
    that can be armed must never be outside the batch that decides whether it can.
    """
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    roster = ((raw.get("trading") or {}).get("ensemble") or {}).get("strategies") or []
    uncovered = [s for s in roster if s not in DEFAULT_LINEUP]
    assert not uncovered, f"armed but never judged: {uncovered}"


def test_folds_are_enough_for_cscv_to_run():
    """The failure that cost H3 its first run, pinned as a property of the tool.

    CSCV needs at least as many observations as it splits into. Below that it returns
    `insufficient_series` and the verdict block prints "PBO: not measured" in the same
    format as a measured one.
    """
    assert FOLDS >= 8, f"{FOLDS} folds cannot be split for CSCV"
    assert cscv_pbo(
        [[0.1 * i for i in range(FOLDS)] for _ in range(4)], splits=8
    )["ok"] is True


def test_the_research_window_leaves_a_holdout():
    """More folds must not consume the window the gate judges freshness on.

    A run that spends everything on research leaves no holdout, and the gate needs
    holdout trades before it will clear anything -- so a FOLDS bump that quietly
    zeroes the holdout would turn the experiment into a different, weaker one.
    """
    train, test, folds = 200, 50, FOLDS
    bars = 1200
    research = train + test * folds
    holdout = bars - research - 1
    assert research < bars
    assert holdout > 100, f"holdout collapsed to {holdout} bars at {folds} folds"