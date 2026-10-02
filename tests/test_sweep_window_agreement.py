"""A 400-bar ranking told us nothing about the 1000-bar ranking.

Measured twice on the same `BTC/USDT` 1h data. Spearman rho between the two
rankings is -0.337, exact permutation p = 0.159, and the sign flips for 12 of 19
strategies. The top five by the short window summed +621.54 there and -337.58 on
the long one; the bottom five summed -1115.84 and +304.12.

p = 0.159 does *not* license "short windows are anti-predictive". This is one
asset with overlapping windows and n = 19. What it does establish is the weaker
and sufficient claim: a short-window ranking carries no demonstrable selection
information about the long-window ranking. Anyone selecting a lineup from the
400-bar table would have bought the losing group.

So the numbers below are pinned as a regression guard, not as a research result.
If a future sweep produces a different table, this test failing is the point --
it means the documented table and the pipeline have diverged, and the documented
table must be re-measured rather than quietly updated.

What must never follow from this: any of these strategies is "the best one".
Every one of them is `research_only`, and the reason is now stronger than a
failed holdout.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: Reproduced by `python tools/strategy_sweep.py 1h BTC/USDT 1000`.
SWEEP_1000 = {
    "dual_thrust": 338.58,
    "mean_reversion_example": 219.68,
    "momentum_roc": 218.44,
    "vwap_reversion": 217.88,
    "williams_r_reversion": 208.48,
    "cci_reversion": 198.20,
    "bollinger_reversion": 197.47,
    "supertrend_follow": 196.91,
    "rsi_reversion": 188.21,
    "macd_trend": 34.71,
    "ema_cross": -0.22,
    "ma_cross_example": -114.62,
    "turtle_breakout": -209.53,
    "adx_di_trend": -220.83,
    "heikin_ashi_trend": -220.91,
    "parabolic_sar_follow": -231.43,
    "volume_impulse_example": -232.98,
    "stochastic_reversion": -285.44,
    "keltner_breakout": -310.42,
}

#: Reproduced by `python tools/strategy_sweep.py 1h BTC/USDT 400`.
SWEEP_400 = {
    "adx_di_trend": 92.16,
    "bollinger_reversion": -109.12,
    "cci_reversion": -151.22,
    "dual_thrust": -363.31,
    "ema_cross": 0.00,
    "heikin_ashi_trend": -257.45,
    "keltner_breakout": 149.60,
    "macd_trend": 36.94,
    "ma_cross_example": 4.30,
    "mean_reversion_example": -115.07,
    "momentum_roc": 116.39,
    "parabolic_sar_follow": -228.79,
    "rsi_reversion": -40.21,
    "stochastic_reversion": -45.66,
    "supertrend_follow": 125.45,
    "turtle_breakout": 97.61,
    "volume_impulse_example": 132.49,
    "vwap_reversion": -29.93,
    "williams_r_reversion": -89.28,
}

RUNBOOK = Path(__file__).resolve().parents[1] / "docs" / "runbooks" / "launch.md"


def _ranks(values: dict[str, float], keys: list[str]) -> list[float]:
    order = sorted(keys, key=lambda k: -values[k])
    out = [0.0] * len(keys)
    i = 0
    while i < len(order):
        j = i
        while (
            j + 1 < len(order)
            and values[order[j + 1]] == values[order[i]]
        ):
            j += 1
        average = (i + j) / 2 + 1
        for t in range(i, j + 1):
            out[keys.index(order[t])] = average
        i = j + 1
    return out


def _spearman(a: dict[str, float], b: dict[str, float]) -> float:
    keys = sorted(set(a) & set(b))
    ra, rb = _ranks(a, keys), _ranks(b, keys)
    n = len(keys)
    mean_a = sum(ra) / n
    mean_b = sum(rb) / n
    num = sum((ra[i] - mean_a) * (rb[i] - mean_b) for i in range(n))
    den = (
        sum((ra[i] - mean_a) ** 2 for i in range(n))
        * sum((rb[i] - mean_b) ** 2 for i in range(n))
    ) ** 0.5
    return num / den if den else 0.0


def test_the_two_windows_genuinely_disagree() -> None:
    """The finding itself. If this ever becomes strongly positive, re-examine."""
    rho = _spearman(SWEEP_400, SWEEP_1000)
    assert rho < 0.0, f"windows now agree (rho={rho:+.3f}) -- revise the claim"


def test_selecting_on_the_short_window_would_have_bought_the_wrong_group() -> None:
    """Arithmetic, not inference -- this is why the finding matters."""
    keys = sorted(set(SWEEP_400) & set(SWEEP_1000))
    top5 = sorted(keys, key=lambda k: -SWEEP_400[k])[:5]
    assert sum(SWEEP_400[k] for k in top5) > 0, "premise: it looked good"
    assert sum(SWEEP_1000[k] for k in top5) < 0, (
        "the short window's winners should have lost money on the long one"
    )


def test_more_than_half_the_strategies_change_sign() -> None:
    keys = sorted(set(SWEEP_400) & set(SWEEP_1000))
    flips = sum(
        1 for k in keys if (SWEEP_400[k] > 0) != (SWEEP_1000[k] > 0)
    )
    assert flips >= 12, f"only {flips} sign flips -- the window is more stable now"


def test_the_runbook_carries_the_current_numbers() -> None:
    """A stale table is worse than no table: it looks like evidence."""
    text = RUNBOOK.read_text(encoding="utf-8")
    for name, net in SWEEP_1000.items():
        assert f"{net:+9.2f}" in text or f"{net:.2f}" in text, (
            f"{name} ({net:+.2f}) is not in the runbook -- re-measure and "
            f"update, do not leave the old figure in place"
        )


def test_the_superseded_table_is_gone() -> None:
    """It was wrong in three of five rows. It must not linger as history."""
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "+249.64" not in text, "the superseded top-5 table is still present"
    assert "+110.96" not in text

def test_the_stability_tool_refuses_to_rank_on_broken_windows() -> None:
    """It printed "0 of 20 positive in every window" and that meant nothing.

    `start`/`end` reached `run_grid` but not the bar fetch, so all four windows
    replayed the same bars and every window returned bit-identical PNL --
    -15.047438940000575 four times over. The tool then reported zero strategies
    positive in every window, which reads as a devastating result about the
    strategies and is in fact a broken measurement.

    Identical numbers across disjoint windows are *proof* the windows are not
    disjoint, so the tool must abort rather than publish a ranking. This is the
    sixth false-green of this family in this repository; the difference is that
    this one now refuses to produce output at all.
    """
    import inspect

    from tools import window_stability

    src = inspect.getsource(window_stability)
    assert "ABORT" in src, "a collapsed measurement must abort, not report"
    assert "Refusing to print a ranking" in src
    assert "identical" in src, "the abort must name the symptom it detected"


def test_the_stability_tool_flags_a_short_fetch() -> None:
    """Asked for 2000 bars, got 1000, and the shortfall was silent."""
    import inspect

    from tools import window_stability

    src = inspect.getsource(window_stability.main)
    assert "WARNING" in src, "a short fetch must be announced"
    assert "asked for" in src


def test_the_two_tools_agree_on_the_strategy_list() -> None:
    """A duplicated list is only safe while it is checked.

    `tools/window_stability.py` carries its own copy because importing
    `strategy_sweep` executes its argv parsing. Duplication is the lesser evil
    only if something notices when the two drift -- otherwise the stability tool
    silently stops measuring a strategy that was added or removed.
    """
    import pathlib

    sweep_path = (
        pathlib.Path(__file__).resolve().parents[1] / "tools" / "strategy_sweep.py"
    )
    source = sweep_path.read_text(encoding="utf-8")
    start = source.index("STRATEGIES = [")
    end = source.index("]", start)
    import re

    # Name extraction rather than ast.literal_eval: the source contains a
    # conditional expression (an `if False else` over two string literals),
    # which is an expression rather than a literal and would not evaluate.
    block = source[start : end + 1]
    declared = [
        name
        for name in re.findall('"([a-z_]+)"', block)
        if name != "williams_williams"
    ]
    from tools.window_stability import STRATEGIES as stability

    assert sorted(declared) == sorted(stability), (
        f"strategy lists drifted: {set(declared) ^ set(stability)}"
    )


def test_the_stability_tool_is_importable_without_argv() -> None:
    """It parses argv at import; a test runner must not be able to break it."""
    import importlib

    from tools import window_stability

    module = importlib.reload(window_stability)
    assert module.BARS > 0 and module.WINDOWS > 0
