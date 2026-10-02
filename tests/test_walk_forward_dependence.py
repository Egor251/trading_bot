"""The confidence interval was too narrow, so the gate cleared too much.

Walk-forward folds overlap: consecutive test windows share bars, and the returns
inside a series are autocorrelated. The plain bootstrap resamples i.i.d., which
is only valid when samples are independent, so it understated the spread. A
`ci_excludes_zero` check built on that interval passes more easily than it should
-- which is exactly the overfitting this gate exists to catch, working against
it.

These tests pin the correction: on correlated data the block interval must be
wider, on independent data it must be close to the naive one, and the block
length must stay in a sane range rather than silently degenerating to i.i.d.
again.
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.walk_forward import (  # noqa: E402
    _optimal_block_length,
    block_bootstrap_mean_ci,
    bootstrap_mean_ci,
)


def _correlated(n: int, seed: int = 7) -> list[float]:
    """A random walk: what overlapping windows of a price series look like."""
    rng = random.Random(seed)
    out, value = [], 0.0
    for _ in range(n):
        value += rng.gauss(0.0, 1.0)
        out.append(value)
    return out


def _independent(n: int, seed: int = 7) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(1.0, 1.0) for _ in range(n)]


def test_block_length_is_sane_for_short_and_long_series() -> None:
    assert _optimal_block_length(0) >= 1
    assert _optimal_block_length(1) == 1
    for n in (3, 5, 10, 30, 100, 1000):
        block = _optimal_block_length(n)
        assert 1 <= block <= n, f"n={n} gave block={block}"


def test_correlated_data_gets_a_wider_interval() -> None:
    """The whole point: i.i.d. resampling understates correlated spread."""
    values = _correlated(24)
    naive = bootstrap_mean_ci(values, samples=800, ci=0.95, seed=3)
    blocked = block_bootstrap_mean_ci(values, samples=800, ci=0.95, seed=3)
    naive_width = naive["ci_high"] - naive["ci_low"]
    blocked_width = blocked["ci_high"] - blocked["ci_low"]
    assert blocked_width > naive_width, (
        f"block {blocked_width:.4f} did not exceed iid {naive_width:.4f}"
    )


def test_independent_data_is_not_punished() -> None:
    """Blocks on uncorrelated data should cost little, not double the interval."""
    values = _independent(60)
    naive = bootstrap_mean_ci(values, samples=800, ci=0.95, seed=3)
    blocked = block_bootstrap_mean_ci(values, samples=800, ci=0.95, seed=3)
    naive_width = max(1e-9, naive["ci_high"] - naive["ci_low"])
    ratio = (blocked["ci_high"] - blocked["ci_low"]) / naive_width
    assert 0.5 < ratio < 3.0, f"block/iid width ratio {ratio:.2f} is unreasonable"


def test_the_interval_contains_the_mean() -> None:
    values = _correlated(30, seed=11)
    out = block_bootstrap_mean_ci(values, samples=600, ci=0.95, seed=5)
    mean = sum(values) / len(values)
    assert out["ci_low"] <= mean <= out["ci_high"], out
    assert out["bootstrap_samples"] == 600
    assert out["block_length"] >= 1


def test_degenerate_inputs_return_something_usable() -> None:
    empty = block_bootstrap_mean_ci([])
    assert empty["ci_low"] == 0.0 and empty["ci_high"] == 0.0
    assert empty["block_length"] == 0

    single = block_bootstrap_mean_ci([4.2])
    assert single["ci_low"] == single["ci_high"] == 4.2
    assert single["bootstrap_samples"] == 0


def test_block_longer_than_the_series_is_handled() -> None:
    out = block_bootstrap_mean_ci([1.0, 2.0, 3.0], samples=100, block_length=99)
    assert math.isfinite(out["ci_low"]) and math.isfinite(out["ci_high"])


def test_the_run_reports_how_many_variants_were_tried() -> None:
    """max-of-N is optimistically biased, so N is evidence about trust."""
    from engine.walk_forward import WalkForwardConfig, run_walk_forward_offline

    class _Plugin:
        def default_params(self):
            return {"fast": 5, "slow": 20}

        def isolated_config(self, overrides=None):
            return {"fast": 5, "slow": 20}

        def evaluate_bars(self, series, *, config=None):
            n = len(series.get("close") or [])
            close = [float(v) for v in series["close"]]
            out = []
            for i in range(n):
                if i < 21:
                    out.append(None)
                    continue
                fast = sum(close[i - 5 : i + 1]) / 6.0
                slow = sum(close[i - 20 : i + 1]) / 21.0
                # Every variant trades, but the winner differs by parameter.
                if fast > slow:
                    out.append(_D("BUY", 0.5))
                else:
                    out.append(_D("SELL", 0.5))
            return out

    class _D:
        def __init__(self, action: str, strength: float) -> None:
            self.action = action
            self.strength = strength
            self.intent = "long" if action == "BUY" else "short"
            self.metrics: dict = {}

    n = 320
    rng = random.Random(4)
    price = 100.0
    closes = []
    for _ in range(n):
        price *= 1.0 + rng.gauss(0.0004, 0.012)
        closes.append(price)
    series = {
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1.0] * n,
    }
    cfg = WalkForwardConfig(
        train_bars=80, test_bars=30, step=20, top_k=1,
        bootstrap_samples=200, min_folds=1,
    )
    result = run_walk_forward_offline(
        _Plugin(),
        series,
        [{"fast": 3}, {"fast": 5}, {"fast": 8}],
        config=cfg,
    )
    summary = result.get("oos") or result.get("summary") or {}
    assert summary.get("variants_tried_total", 0) >= 3, (
        "the number of trials must reach the summary, or a reader cannot judge "
        "how much the max-of-N selection flattered the result"
    )
    assert summary.get("folds_evaluated", 0) >= 1
    assert "block_length" in summary, (
        "the summary must record that the interval came from blocks"
    )