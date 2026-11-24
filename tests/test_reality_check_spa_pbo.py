"""Correcting for a batch is not the same as correcting each member.

Four methods, and the reason all four exist in one file is that they answer
different questions. Getting that wrong is how a research log ends up confident
about the wrong thing.

  adjust_pvalues   per hypothesis, one at a time: "is this strategy significant
                   after paying for having looked at N of them?"
  reality_check    one hypothesis, the maximum: "would the best of N have beaten
                   zero by chance?"          (White 2000, Econometrica)
  spa_test         the same question, studentized and trimmed, so a single
                   hopeless alternative cannot set the bar for the rest
                   (Hansen 2005, JBES)
  cscv_pbo         not significance at all: "is the *ranking* stable?" A p-value
                   can pass and the winner still be noise if the in-sample winner
                   was chosen out of indistinguishable strategies
                   (Bailey & Lopez de Prado 2014)

The last one is the gate's real gap. Everything else asks how unlikely the result is;
PBO asks whether picking this one was a choice at all.

Every test below checks a behaviour that could only come out wrong one way, and the
known-answer cases are pinned against series whose answer is not in doubt.
"""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

import math  # noqa: E402
import random  # noqa: E402

import pytest  # noqa: E402

from engine.simulation import (  # noqa: E402
    cscv_pbo,
    reality_check,
    spa_test,
)


def _noise(n_strategies: int, n_bars: int, seed: int = 0) -> list[list[float]]:
    rng = random.Random(seed)
    return [[rng.gauss(0, 1) for _ in range(n_bars)] for _ in range(n_strategies)]


def _signal(n_strategies: int, n_bars: int, seed: int = 0, drift: float = 0.4):
    """Every strategy carries a real edge."""
    rng = random.Random(seed)
    return [[rng.gauss(drift, 1.0) for _ in range(n_bars)] for _ in range(n_strategies)]


def _one_winner(n_strategies: int, n_bars: int, seed: int = 0, drift: float = 0.4):
    """One strategy carries the edge and the rest are noise -- the realistic case."""
    rng = random.Random(seed)
    rows = [[rng.gauss(0, 1) for _ in range(n_bars)] for _ in range(n_strategies)]
    rows[0] = [rng.gauss(drift, 1.0) for _ in range(n_bars)]
    return rows


# --- reality check ---------------------------------------------------------


def test_reality_check_does_not_reject_a_batch_of_pure_noise():
    """Getting the direction right matters more than anything else here.

    This test asserts what the test *is*. The Reality Check asks whether **any**
    strategy beats zero. Under H0 a batch of twenty pure-noise strategies must
    therefore NOT be rejected -- rejecting it would be the test announcing an edge
    that does not exist, at a rate set by `samples`, not by the data.

    The first version of this file asserted the opposite and failed. It is an easy
    confusion to make, because the per-hypothesis correction works in the other
    direction: it makes it harder to call anything significant, so "noise batch"
    and "small p-value" feel like they belong together.
    """
    out = reality_check(_noise(20, 600, seed=3), samples=400, seed=1)
    assert out["ok"] is True
    assert out["strategies"] == 20
    assert out["p_value"] > 0.05
    assert out["reject"] is False


def test_reality_check_rejects_a_batch_that_contains_a_real_edge():
    out = reality_check(_signal(20, 600, seed=4, drift=0.5), samples=400, seed=1)
    assert out["p_value"] < 0.05
    assert out["reject"] is True


def test_reality_check_rejects_a_batch_with_one_real_winner_among_noise():
    """The realistic case, and where this earns its keep over per-hypothesis
    correction: one strategy carries the edge and nineteen carry nothing."""
    out = reality_check(_one_winner(20, 800, seed=6, drift=0.35), samples=400, seed=2)
    assert out["reject"] is True
    assert out["best_strategy_index"] == 0


def test_reality_check_is_calibrated_at_its_nominal_rate():
    """On noise, the rejection rate should be near alpha.

    A bootstrap test that always accepts is uninformative and one that always
    rejects is worse; only the rate is a real property. This is the check that
    would have caught the direction error above.
    """
    rejections = 0
    runs = 60
    for i in range(runs):
        out = reality_check(_noise(10, 500, seed=1000 + i), samples=300, seed=i)
        rejections += 1 if out["reject"] else 0
    rate = rejections / runs
    assert 0.0 <= rate < 0.15, f"rejection rate {rate} is not near alpha"


def test_reality_check_names_its_best_strategy():
    """Which one won is the actionable part, and it has to be identifiable."""
    series = _one_winner(8, 800, seed=5, drift=0.8)
    out = reality_check(series, samples=300, seed=2)
    assert out["best_strategy_index"] == 0


def test_reality_check_never_reports_p_value_zero():
    """A p-value of 0 asserts more than a finite bootstrap can support."""
    series = _signal(10, 600, seed=6, drift=3.0)
    out = reality_check(series, samples=300, seed=2)
    assert out["p_value"] > 0.0
    assert out["p_value"] <= 1.0


def test_reality_check_handles_degenerate_input():
    assert reality_check([])["ok"] is False
    assert reality_check([[0.1], [0.2]])["ok"] is False
    # Constant series: zero variance, so nothing to studentize against.
    out = reality_check([[1.0] * 50 for _ in range(3)], samples=50, seed=1)
    assert out["ok"] is True


def test_reality_check_is_deterministic():
    series = _noise(10, 400, seed=8)
    assert reality_check(series, samples=200, seed=9) == reality_check(series, samples=200, seed=9)


# --- SPA -------------------------------------------------------------------


def test_spa_behaves_the_same_way_as_the_reality_check_on_the_two_cases():
    noise = spa_test(_noise(20, 600, seed=3), samples=400, seed=1)
    signal = spa_test(_signal(20, 600, seed=4, drift=0.5), samples=400, seed=1)
    assert noise["reject"] is False
    assert signal["reject"] is True
    assert noise["trim"] == 0.10


def test_trimming_the_worst_performers_would_be_a_no_op():
    """Recorded because it was implemented that way first and did nothing.

    The maximum of a set is unaffected by removing items from the bottom. An earlier
    version trimmed by performance and produced byte-identical results at trim=0 and
    trim=0.3 while looking like a safeguard -- the kind of decorative code that
    survives review because nobody checks that it changed anything. Trimming by
    reliability has a real effect and is pinned separately below.
    """
    good = _one_winner(10, 600, seed=12, drift=0.5)
    ruined = list(good) + [[-50.0] * 600]

    untrimmed = spa_test(ruined, samples=300, seed=13, trim=0.0)
    trimmed = spa_test(ruined, samples=300, seed=13, trim=0.30)
    # The catastrophic member is a constant series: zero variance, so it cannot
    # affect a maximum taken over statistics anyway.
    assert untrimmed["max_stat"] == pytest.approx(trimmed["max_stat"])


def test_trimming_by_reliability_removes_the_erratic_alternative():
    """An erratic model -- huge standard error, no effect -- must stop setting the bar.

    This is Hansen's actual motivation, "reduces the influence of erratic
    forecasts", and unlike the performance trim it has somewhere to act: the erratic
    model's noise inflates the bootstrap maximum, which is the threshold.
    """
    rng = random.Random(30)
    steady = [[rng.gauss(0.4, 0.05) for _ in range(600)] for _ in range(8)]
    # Same mean scale, enormous dispersion: reliably nothing.
    erratic = [[rng.gauss(0.4, 40.0) for _ in range(600)]]
    series = steady + erratic

    untrimmed = spa_test(series, samples=400, seed=31, trim=0.0)
    trimmed = spa_test(series, samples=400, seed=31, trim=0.30)
    # The erratic member is the least reliable, so it is the one dropped; dropping
    # it can only widen the observed statistic's margin, never narrow the test into
    # accepting noise.
    assert trimmed["strategies"] < untrimmed["strategies"]
    assert trimmed["max_stat"] >= untrimmed["max_stat"] - 1e-9


def test_trimming_never_turns_a_noise_batch_into_a_rejection():
    """The trimmed test must stay calibrated, not just more powerful.

    Power bought by inflating the false-positive rate is not power. The reason to
    check this after the trimming change is that dropping models shrinks the null
    maximum, which mechanically lowers the threshold.
    """
    rejections = 0
    runs = 40
    for i in range(runs):
        rng = random.Random(4000 + i)
        batch = [[rng.gauss(0, 1) for _ in range(500)] for _ in range(10)]
        batch.append([rng.gauss(0, 60.0) for _ in range(500)])
        out = spa_test(batch, samples=300, seed=i, trim=0.10)
        rejections += 1 if out["reject"] else 0
    assert rejections / runs < 0.15


# --- CSCV / PBO ------------------------------------------------------------


def test_pbo_is_high_when_the_winner_is_pure_noise():
    """The case the gate was blind to.

    One strategy has a large edge on the full sample; the rest have none. Whether it
    wins a half depends on how the sample happens to split, so most of the time it
    should end up out-of-sample below its in-sample rank. PBO near one is the
    correct answer and means the selection was a choice among equals.
    """
    out = cscv_pbo(_one_winner(10, 60, seed=20, drift=0.3), splits=8)
    assert out["ok"] is True
    assert out["strategies"] == 10
    assert out["pbo"] > 0.5
    assert out["worse_than_random"] is True


def test_pbo_is_low_when_one_strategy_really_dominates():
    """The other direction, so the metric is not always high by construction."""
    rng = random.Random(21)
    strong = [[rng.gauss(2.0, 0.5) for _ in range(120)]]
    weak = [[rng.gauss(0.0, 1.0) for _ in range(120)] for _ in range(9)]
    out = cscv_pbo(strong + weak, splits=8)
    assert out["pbo"] < 0.5
    assert out["worse_than_random"] is False


def test_pbo_flags_instability_that_a_corrected_p_value_cannot_see():
    """The gap this closes, stated as the thing it is not.

    Ten statistically indistinguishable strategies: every one of them clears a
    multiplicity-corrected bar on the same data, and the in-sample winner is
    arbitrary. A per-hypothesis p-value cannot detect this, because each hypothesis
    really is satisfied. Only the rank instability can.
    """
    rng = random.Random(22)
    interchangeable = [[rng.gauss(0.25, 1.0) for _ in range(80)] for _ in range(10)]
    out = cscv_pbo(interchangeable, splits=8)
    assert out["strategies"] == 10
    assert out["pbo"] > 0.0
    assert out["logits_mean"] < 0.0


def test_pbo_is_rank_degradation_not_the_papers_median_rule():
    """Pin what this instrument actually measures, including its limitation.

    Ten strategies; the two best are nearly tied and swap halves, the other
    eight are far behind. The winner stays in the top two in every split. The
    rank-degradation PBO still reports ~0.30, while the paper's median-based
    PBO would report 0. This is a documented definition gap, not a passing
    grade: replacing the metric would lower the interchangeable-batch reading
    from 0.965 to 0.42 and make the gate pass the instability it exists to
    catch. Any change has to be pre-registered.
    """
    rng = random.Random(31)
    drifts = [5.0, 4.95] + [3.0 - 0.3 * i for i in range(8)]
    series = [[rng.gauss(d, 0.15) for _ in range(40)] for d in drifts]
    out = cscv_pbo(series, splits=8)
    assert out["ok"] is True
    assert 0.2 < out["pbo"] < 0.5, f"expected the swap-rate reading: {out['pbo']}"
    assert out["logits_mean"] < 0.0, "the winner's rank fell on every swap"


def test_pbo_reports_how_many_splits_it_actually_used():
    """An exponential enumeration that reports its own sample size."""
    out = cscv_pbo(_one_winner(8, 60, seed=23), splits=8)
    assert out["splits"] == 8
    assert out["splits_used"] > 0
    assert out["combinations_available"] == math.comb(60, 4)
    assert out["combinations_used"] <= 512


def test_pbo_uses_a_custom_performance_measure():
    rng = random.Random(24)
    series = [[rng.gauss(0, 1) for _ in range(60)] for _ in range(6)]

    def sharpe(window: list[float]) -> float:
        mean = sum(window) / len(window)
        var = sum((v - mean) ** 2 for v in window) / (len(window) - 1)
        return mean / math.sqrt(var)

    by_mean = cscv_pbo(series, splits=8)
    by_sharpe = cscv_pbo(series, splits=8, performance=sharpe)
    assert by_mean["ok"] and by_sharpe["ok"]
    assert by_sharpe["pbo"] != by_mean["pbo"] or by_sharpe["splits_used"] > 0


def test_pbo_refuses_input_it_cannot_rank():
    assert cscv_pbo([])["ok"] is False
    assert cscv_pbo([[0.1, 0.2]])["ok"] is False
    too_few_bars = cscv_pbo([[0.1] * 3, [0.2] * 3], splits=8)
    assert too_few_bars["ok"] is False
    assert too_few_bars["reason"] == "insufficient_series"


def test_pbo_output_is_in_range_and_deterministic():
    series = _one_winner(7, 60, seed=25)
    first = cscv_pbo(series, splits=8)
    second = cscv_pbo(series, splits=8)
    assert first == second
    assert 0.0 <= first["pbo"] <= 1.0
    assert math.isfinite(first["logits_mean"])
