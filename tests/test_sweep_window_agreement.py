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


def test_a_disjoint_range_reaches_the_adapter() -> None:
    """The fetch could not honour a time range at all.

    `CcxtAdapter.fetch_ohlcv` had no `start`/`end` parameters -- only `limit` --
    so it always walked back from "now". `historical_data_service._fetch_remote`
    accepted both and passed them to an adapter that had no such parameters, and
    the call went through anyway. Every window therefore received the same
    most-recent tail: `adx_di_trend` reported -15.047438940000575 four times, once
    per "disjoint" window.

    The adapter signature and the paging walk are what make a range honoured, so
    the test reads the source rather than calling a live exchange.
    """
    import pathlib

    src = (
        pathlib.Path(__file__).resolve().parents[1]
        / "engine"
        / "adapters"
        / "ccxt_adapter.py"
    ).read_text(encoding="utf-8")

    fetch_sig = src[src.index("async def fetch_ohlcv") : src.index("async def fetch_ohlcv") + 900]
    assert "start:" in fetch_sig, "fetch_ohlcv cannot express a start bound"
    assert "end:" in fetch_sig, "fetch_ohlcv cannot express an end bound"
    assert "_fetch_ohlcv_paged(" in src
    paged = src[src.index("async def _fetch_ohlcv_paged") :]
    assert "since_ms" in paged and "until_ms" in paged, (
        "the paging walk must accept the range it was asked for"
    )
    assert "until_ms" in paged.split("cursor:")[1][:200], (
        "the end bound must seed the walk, or it still starts from now"
    )


def test_a_disabled_strategy_is_not_allowed_to_handle_a_candle() -> None:
    """`enabled: False` was meaningless on the research path.

    `candle_allowed` returned True for any plugin not enrolled in the current
    research run, because "not in research" was treated as "live, and live
    plugins should trade". A `run_grid` enrolling one strategy therefore left the
    other nineteen answering True, all consuming every candle against one shared
    phantom runtime.

    Measured consequence: three strategies run alone gave +12.37 / -17.13 /
    +10.41; the same three in a full run gave -7.45 / -8.48 / -9.01 and agreed
    to the cent on a third window. The first strategy in the list was always
    clean, which is why the contamination looked like strategy behaviour.

    Parking on a sentinel ticker never prevented this: a filter on which symbol
    to act on is not the same thing as being switched off.
    """
    import inspect

    from engine.strategy_plugin import StrategyPlugin

    src = inspect.getsource(StrategyPlugin.candle_allowed)
    assert '"enabled"' in src, (
        "candle_allowed must honour the enabled flag, or bystanders run"
    )
    body = src.split("enabled")[-1]
    assert "return False" in body, (
        "a disabled plugin must be refused before the research check"
    )


def test_the_random_baseline_is_reproducible() -> None:
    """An unseeded null cannot be re-run, so it settles nothing.

    The schedule is derived from (seed, index) on every call rather than from a
    Random carried on the instance. That matters more than it looks: a stateful
    RNG makes the sequence depend on how many times the evaluator happened to be
    invoked, which differs between the baseline and the strategy it is being
    compared against -- and then the two P&Ls are not the same quantity.
    """
    from engine.strategy_plugin import StrategyDecision
    from strategies.random_baseline.plugin import RandomBaselinePlugin

    plugin = RandomBaselinePlugin.__new__(RandomBaselinePlugin)
    cfg = {"seed": 7, "trade_probability": 0.08}
    first = [
        plugin.evaluate_at({}, i, cfg).action for i in range(200)
    ]
    second = [
        plugin.evaluate_at({}, i, cfg).action for i in range(200)
    ]
    assert first == second, "the schedule moved between calls"
    # And a different seed must actually give a different schedule.
    other = [
        plugin.evaluate_at({}, i, {"seed": 8}).action for i in range(200)
    ]
    assert other != first, "the seed is not being used"
    assert isinstance(plugin.evaluate_at({}, 0, cfg), StrategyDecision)


def test_the_baseline_declares_no_warmup() -> None:
    """Otherwise it gets a different exposure profile to the strategies."""
    from strategies.random_baseline.plugin import RandomBaselinePlugin

    plugin = RandomBaselinePlugin.__new__(RandomBaselinePlugin)
    assert plugin.warmup_bars() == 0


def test_the_percentile_counts_ties_as_half() -> None:
    """An exactly-median value must not read as a perfect score.

    Ties are common here: with a small null most seeds land on the same net, and
    a plain `v < value` count would hand a strategy sitting exactly on the
    median a percentile of 100 -- a strategy that merely matched the null would
    be reported as the best result in the run.
    """
    from tools.window_stability import _percentile

    assert _percentile(5.0, [1.0, 2.0, 3.0, 4.0, 5.0]) == 90.0
    assert _percentile(0.0, [1.0, 2.0, 3.0]) == 0.0
    assert _percentile(99.0, [1.0, 2.0, 3.0]) == 100.0
    assert _percentile(1.0, []) == 0.0
    # Median of an odd-length null sits exactly on one member: half credit.
    assert _percentile(3.0, [1.0, 2.0, 3.0, 4.0, 5.0]) == 50.0


def test_the_baseline_matches_the_framework_evaluate_at_signature() -> None:
    """A reversed argument order fails silently, which is worse than loudly.

    `handle_candle` calls `evaluate_at(series, index, config=...)`. The baseline
    declared `(index, series, config)`, so `len(series.get("close"))` ran on an
    int, the exception was swallowed into `_last_error`, and the plugin emitted
    nothing -- while every diagnostic still showed it enabled, subscribed, and
    drawing its expected ~20 signals per window.

    The zero-trades guard caught it. Nothing else would have: the null came back
    as a clean distribution of 0.00 and produced a confident "33 of 80 windows
    beat their own baseline", which is what happens when a broken instrument is
    trusted.
    """
    import inspect

    from strategies.random_baseline.plugin import RandomBaselinePlugin

    params = list(inspect.signature(RandomBaselinePlugin.evaluate_at).parameters)
    assert params[:3] == ["self", "series", "index"], (
        f"evaluate_at signature drifted from the framework: {params}"
    )

    from engine.strategy_plugin import StrategyPlugin

    handler = inspect.getsource(StrategyPlugin.handle_candle)
    assert "evaluate_at(series, index" in handler, (
        "the framework calls it positionally; check the order still matches"
    )


def test_the_baseline_subscribes_to_candles() -> None:
    """No subscription means no schedule, and silence reads as a result."""
    import inspect

    from strategies.random_baseline.plugin import RandomBaselinePlugin

    assert "on_initialize" in dir(RandomBaselinePlugin)
    src = inspect.getsource(RandomBaselinePlugin.on_initialize)
    assert "market.candle" in src
    assert inspect.iscoroutinefunction(RandomBaselinePlugin._on_candle), (
        "a sync handler returning a coroutine is never awaited"
    )


def test_the_runbook_records_the_measured_verdict() -> None:
    """A verdict nobody can reproduce is a verdict nobody should act on.

    The tables in the runbook and the methodology behind them are the only
    durable output of this measurement. They must state the null, the window
    count and the multiplicity expectation -- otherwise a reader sees
    "9 of 80 windows cleared p90" and reasonably concludes something was found.
    """
    import pathlib

    doc = (
        pathlib.Path(__file__).resolve().parents[1]
        / "docs"
        / "runbooks"
        / "launch.md"
    ).read_text(encoding="utf-8")

    for needle in (
        "random_baseline",
        "window_stability",
        "expected under noise",
        "own null",
    ):
        assert needle in doc, f"the runbook must record: {needle}"

    # The honest framing, not just the tables.
    assert "distinguishable from chance" in doc
    assert "Expected overlap" in doc


def test_the_hypothesis_is_measured_in_the_same_run_as_everything_else() -> None:
    """A hypothesis tested in its own kinder configuration is not a test.

    `time_series_momentum` is in the shared list so it is judged by the same
    null, the same windows and the same guards as the other twenty. Giving it a
    separate run would have been the easiest way to manufacture a result, and
    the failure mode would have been invisible in the output.
    """
    from tools.window_stability import STRATEGIES

    assert "time_series_momentum" in STRATEGIES
    assert "random_baseline" not in STRATEGIES, (
        "the null must never be measured against itself"
    )


def test_window_bounds_use_the_real_bar_interval() -> None:
    """A 4h series also ends in "h", and it is not an hourly one.

    The step was inferred from the timeframe's last character, so 4h was
    treated as 3600s instead of 14400s. Window boundaries landed four times
    too close together and the run claimed ~1998 bars for a 1000-bar window --
    a number that then became the fetch limit for every window in the slice.

    Found by running it: the printed per-window bar count did not match the
    number of bars actually fetched, which is the sort of thing a reader has no
    reason to doubt.
    """
    import importlib

    from tools import window_stability

    module = importlib.reload(window_stability)
    original = module.TIMEFRAME
    try:
        for timeframe, seconds in (("1h", 3600), ("4h", 14400), ("1d", 86400)):
            module.TIMEFRAME = timeframe
            bars = [{"ts": 1600000000.0 + i * seconds} for i in range(1000)]
            bounds = module.window_bounds(bars, 2)
            span = (bars[-1]["ts"] - bars[0]["ts"]) / 2
            assert bounds[0][2] == int(span // seconds), (
                f"{timeframe} window claimed {bounds[0][2]} bars"
            )
    finally:
        module.TIMEFRAME = original


def test_the_null_calibration_measures_a_rate_near_the_nominal_when_independent() -> None:
    """A control on the control.

    Every verdict in this project compares an observed count against a nominal
    10%. If the percentile arithmetic itself is wrong, the calibration would
    report a number and nobody would notice. Under `independent` windows the
    nominal rate should roughly reproduce, so this is the check that makes the
    `market` and `serial` numbers mean something.
    """
    import random

    from tools.null_calibration import _draw

    rng = random.Random(11)
    cleared = sum(
        _draw(rng, structure="independent", strategies=6, windows=2, per_window=32)
        for _ in range(60)
    )
    total = 60 * 6 * 2
    rate = 100.0 * cleared / total
    assert 5.0 < rate < 20.0, f"independent-window rate {rate:.1f}% is not near nominal"


def test_a_shared_market_factor_inflates_the_false_positive_rate() -> None:
    """The reason the calibration exists, stated as an assertion.

    Real windows share a market factor -- measured on BTC, where window three
    made every strategy look strong at once. If that structure does not raise
    the false-positive rate above nominal, the whole justification for
    calibrating is wrong.
    """
    import random

    from tools.null_calibration import _draw

    rates = {}
    for structure in ("independent", "market", "serial"):
        rng = random.Random(2026)
        cleared = sum(
            _draw(rng, structure=structure, strategies=6, windows=2, per_window=32)
            for _ in range(60)
        )
        rates[structure] = 100.0 * cleared / (60 * 6 * 2)

    assert rates["market"] > rates["independent"], (
        f"a shared factor must inflate the rate: {rates}"
    )
    assert rates["serial"] > rates["market"], (
        f"correlated windows must inflate it further: {rates}"
    )


def test_the_contrarian_rule_makes_money_only_where_theres_something_to_exploit() -> None:
    """A rule tested on series built to have a known answer, both ways.

    The tradability check exists to say something about the real series, and it can
    only do that if it demonstrably profits on a series that *has* the dependency
    and demonstrably does not on one that has not. Both directions are pinned, so a
    bug that inverts the sign -- which is exactly what an off-by-one-bar execution
    lag produces -- fails here rather than producing a confident wrong answer about
    Bitcoin.
    """
    import math
    import random

    from tools.edge_ceiling import lag_rule_pnl

    def _series(phi: float, seed: int = 3, n: int = 3000) -> tuple[list[float], list[float]]:
        rng = random.Random(seed)
        opens, closes = [100.0], [100.0]
        prev = 0.0
        for _ in range(1, n):
            prev = phi * prev + rng.gauss(0, 0.02)
            closes.append(closes[-1] * math.exp(prev))
            opens.append(closes[-2])
        return opens, closes

    exploit = lag_rule_pnl(*_series(-0.15), cost_bps=0.0)
    assert exploit["net"] > 1.0, (
        f"a known phi of -0.15 must be profitable, got {exploit['net']:.3f}"
    )
    flat = lag_rule_pnl(*_series(0.0, seed=11), cost_bps=0.0)
    assert flat["net"] < 0.0, (
        "on a random walk the rule must lose -- if it wins on a series with no "
        "dependency, it is inventing an edge"
    )


def test_out_of_sample_split_carries_the_sign_and_not_the_fit() -> None:
    """A dependency must pay in a stretch that was not used to find it.

    The in-sample gross is not evidence of anything: the coefficient is fitted on
    the same bars the rule is run over. Both halves of a series built with a known
    phi must therefore pay when the sign is carried across, and a random walk must
    not -- which also catches the failure where the sign of the bar's own move is
    dropped and the rule degenerates into a permanent short.
    """
    import math
    import random

    from tools.edge_ceiling import out_of_sample_rule

    def _series(phi: float, seed: int = 3, n: int = 3000):
        rng = random.Random(seed)
        opens, closes = [100.0], [100.0]
        prev = 0.0
        for _ in range(1, n):
            prev = phi * prev + rng.gauss(0, 0.02)
            closes.append(closes[-1] * math.exp(prev))
            opens.append(closes[-2])
        return opens, closes

    o, c = _series(-0.15)
    first = out_of_sample_rule(o, c, cost_bps=10.0)
    second = out_of_sample_rule(o, c, cost_bps=10.0, reverse=True)
    assert first["trades"] > 100, (
        f"the rule must actually trade, got {first['trades']:.0f} position changes"
    )
    assert first["gross"] > 0.5 and second["gross"] > 0.5, (
        f"both halves must pay: {first['gross']:.3f} / {second['gross']:.3f}"
    )

    o2, c2 = _series(0.0, seed=11)
    flat = out_of_sample_rule(o2, c2, cost_bps=10.0)
    assert flat["gross"] < 0.5, (
        "a random walk must not pay out of sample, or the test measures nothing"
    )


def test_costs_decide_the_contrarian_rule_and_the_breakeven_says_where() -> None:
    """A free edge that cannot survive costs is not an edge, and the number is
    worth having rather than assumed.

    The check runs the rule at several cost levels, so it must be monotone in cost:
    more cost can only make the rule worse. And `breakeven_cost_bps` has to agree
    with the point where the net crosses zero.
    """
    import math
    import random

    from tools.edge_ceiling import breakeven_cost_bps, lag_rule_pnl

    rng = random.Random(3)
    opens, closes = [100.0], [100.0]
    prev = 0.0
    for _ in range(2999):
        prev = -0.15 * prev + rng.gauss(0, 0.02)
        closes.append(closes[-1] * math.exp(prev))
        opens.append(closes[-2])

    nets = [lag_rule_pnl(opens, closes, cost_bps=c)["net"] for c in (0.0, 5.0, 25.0)]
    assert nets[0] > nets[1] > nets[2], f"costs must be monotone: {nets}"
    assert breakeven_cost_bps(phi=0.07, sigma=0.04) > 0.0
    assert breakeven_cost_bps(phi=0.0, sigma=0.04) == 0.0, (
        "no dependency means the rule must be worth exactly nothing"
    )


def test_daily_lookback_is_expressed_in_calendar_days_not_trading_days() -> None:
    """The defect that silently fed this sweep two thirds of the sample it asked for.

    `_interval_days("1d")` returned 1.0, so a request for 300 daily bars fetched
    300 *calendar* days and came back with ~206 bars of equity data. Nothing errored;
    the correlation was simply computed on a smaller sample than the one the caller
    believed it had. This is the same shape as the nine other measurement faults in
    this project: a quantity that silently comes back wrong.

    The direction that matters is that every branch must over-request. Coming back
    short is a lie the caller cannot see; asking for slightly too much costs
    nothing but a wider range query.
    """
    from engine.adapters.yahoo_adapter import (
        CALENDAR_DAYS_PER_TRADING_DAY,
        _interval_days,
    )

    assert abs(CALENDAR_DAYS_PER_TRADING_DAY - 365.0 / 252.0) < 1e-9
    assert _interval_days("1d") > 1.0, (
        "a daily bar is one trading day, which is more than one calendar day"
    )
    assert abs(_interval_days("1d") - 365.0 / 252.0) < 1e-9
    # 300 daily bars must span enough calendar days to contain 300 sessions.
    assert 300 * _interval_days("1d") / CALENDAR_DAYS_PER_TRADING_DAY >= 300
    # Intraday: crypto trades 24h, equities 6.5h, so crypto needs fewer calendar
    # days for the same number of bars and must not be asked for more.
    assert _interval_days("1h", "CRYPTO") < _interval_days("1h", "EQUITY")
    assert abs(_interval_days("1w") - CALENDAR_DAYS_PER_TRADING_DAY / 5.0) < 1e-9


def test_resampling_aggregates_rather_than_samples_and_drops_the_ragged_tail() -> None:
    """Weekly returns must be five daily bars compounded, not five daily bars picked.

    Sampling every fifth close would manufacture a different series with different
    autocorrelation and no relation to what a weekly holder would have lived
    through. And an incomplete final group is not a shorter bar -- treating it as
    one puts a spurious jump into a return series.
    """
    from tools.edge_ceiling import log_returns, resample

    closes = [float(100 + i) for i in range(23)]
    weekly = resample(closes, 5)
    assert weekly == [104.0, 109.0, 114.0, 119.0], (
        "one close per completed group, taken at the LAST daily bar of the group -- "
        "a weekly bar closes on Friday, not on the next Monday"
    )
    assert len(resample(closes, 1)) == len(closes), "group=1 must be identity"
    assert resample(closes, 5)[-1] != closes[-1], (
        "the partial tail must be dropped, not reported as a final bar"
    )
    assert len(resample(closes, 5)) == 23 // 5, "only complete groups survive"
    # And the resampled series must still be usable as a price series.
    assert len(log_returns(weekly)) == len(weekly) - 1


def test_the_traded_rule_follows_the_sign_of_the_dependence_it_is_given() -> None:
    """One rule, both directions, so a positive phi can be tested by the same code.

    A sweep over asset classes is only worth running if the instrument can profit
    on a series built to have a *positive* dependence. If it only knows how to be
    contrarian, every positive reading in the sweep is unattributable.
    """
    import math
    import random

    from tools.edge_ceiling import lag_rule_pnl

    def _series(phi: float, seed: int = 5, n: int = 3000):
        rng = random.Random(seed)
        opens, closes = [100.0], [100.0]
        prev = 0.0
        for _ in range(1, n):
            prev = phi * prev + rng.gauss(0, 0.02)
            closes.append(closes[-1] * math.exp(prev))
            opens.append(closes[-2])
        return opens, closes

    o, c = _series(0.15)
    momentum = lag_rule_pnl(o, c, cost_bps=0.0, lean=1.0)
    contrarian = lag_rule_pnl(o, c, cost_bps=0.0, lean=-1.0)
    assert momentum["net"] > 1.0, (
        f"a positive dependence must be profitable to a momentum rule: "
        f"{momentum['net']:.3f}"
    )
    assert contrarian["net"] < momentum["net"], (
        "the same rule pointed the other way must not be the better one"
    )


def _garch_series(n: int = 6000, seed: int = 1) -> list[float]:
    """Returns with strong volatility clustering and *no* return autocorrelation.

    Two things this has to get right, and a naive construction gets both wrong.

    An AR(1) on returns with phi = 0.9 looks like clustering and is not: it is a
    trending series, and its magnitude persistence comes partly from return
    persistence. And an EWMA without a persistence term is not GARCH either -- it
    has no memory beyond the last shock and clusters by about 1.0x, which is how
    the first attempt at this test failed while looking perfectly reasonable.

    So: sign is independent of magnitude, and the conditional variance is a
    genuine AR(1) with alpha + beta = 0.97.
    """
    import math
    import random

    rng = random.Random(seed)
    omega, alpha, beta = 0.00005, 0.10, 0.87
    var = omega / (1.0 - alpha - beta)
    out: list[float] = []
    for _ in range(n):
        out.append(math.sqrt(var) * rng.gauss(0.0, 1.0))
        var = omega + alpha * out[-1] ** 2 + beta * var
    return out


def test_volatility_structure_detects_clustering_that_a_sign_sweep_cannot() -> None:
    """The gap that made six negative results read broader than they were.

    Every sign-based measurement in this project was about whether the next bar goes
    up or down. A breakout strategy does not ask that -- it asks whether the next bar
    is a *big* one. Those are separate properties, and this is the instrument for the
    second, pinned against a series built to have clustering and one that has none.
    """
    import random

    from tools.edge_ceiling import volatility_structure

    clustered = _garch_series(4000)
    rng = random.Random(1)
    white = [rng.gauss(0, 0.02) for _ in range(4000)]

    hot = volatility_structure(clustered)
    cold = volatility_structure(white)
    assert hot["rho_abs"] > 0.1, (
        f"persistence of magnitude must be found where it exists: {hot}"
    )
    assert hot["arch1"] > 0.1, "and formally, as an ARCH(1) coefficient"
    assert abs(cold["rho_abs"]) < 0.1, (
        f"and must not be manufactured where there is none: {cold}"
    )
    assert hot["rho_abs"] > abs(cold["rho_abs"]) + 0.05, (
        f"clustering must separate from white noise: {hot['rho_abs']} vs "
        f"{cold['rho_abs']}"
    )
    # The threshold is deliberately modest. Real markets sit around 1.3x -- measured
    # 1.28 on BTC and 1.54 on ETH -- so an assertion of 2x would be a test of a
    # fantasy series rather than of the instrument. What matters is that the
    # clustered series separates from white noise.
    assert hot["vol_ratio"] > 1.15, hot
    assert hot["vol_ratio"] > abs(cold["vol_ratio"] - 1.0) + 0.15, (
        f"clustering must be visible where it exists and absent where it does "
        f"not: {hot['vol_ratio']} vs {cold['vol_ratio']}"
    )
    assert abs(cold["vol_ratio"] - 1.0) < 0.15


def test_breakout_expectancy_reports_the_mean_and_not_the_variance() -> None:
    """Clustering raises variance; only a directional mean is an expectancy.

    A series can be strongly clustered and still have no directional edge after a
    big move. This pins the difference, because reading vol ratio as an edge is
    exactly the confusion that would make a breakout strategy look justified before
    it had earned anything.
    """

    from tools.edge_ceiling import breakout_expectancy, volatility_structure

    clustered = _garch_series(6000, seed=2)
    assert volatility_structure(clustered)["vol_ratio"] > 1.15, (
        "the series must actually cluster, or the rest of the test is vacuous"
    )

    be = breakout_expectancy(clustered, threshold=1.5)
    assert be["n"] > 100, "there should be plenty of events"
    assert abs(be["mean"]) < 0.01, (
        f"clustering alone must not show a directional edge: {be}"
    )
    assert abs(be["t"]) < 2.0, (
        f"and must not look significant in either direction: {be}"
    )


def _ohlc(closes: list[float], seed: int = 5):
    import random
    rng = random.Random(seed)
    highs = [c * (1.0 + abs(rng.gauss(0, 0.004))) for c in closes]
    lows = [c * (1.0 - abs(rng.gauss(0, 0.004))) for c in closes]
    opens = [closes[max(0, i - 1)] for i in range(len(closes))]
    return highs, lows, opens, closes


def test_range_breakout_finds_a_trend_and_does_not_invent_one() -> None:
    """The registered rule, pinned on both sides before it is trusted on BTC.

    A breakout rule that cannot find a trend is measuring nothing; one that also
    "finds" a random walk is measuring the wrong thing. Both directions are
    required, and costs must be monotone, because the whole negative result at
    short timeframes is an arithmetic consequence of turnover rather than of signal.
    """
    import math
    import random

    from tools.edge_ceiling import breakout_rule_pnl

    rng = random.Random(5)
    trending = [100.0]
    for _ in range(2999):
        trending.append(trending[-1] * math.exp(0.0012 + rng.gauss(0, 0.015)))
    walk = [100.0]
    for _ in range(2999):
        walk.append(walk[-1] * math.exp(rng.gauss(0, 0.015)))

    up = breakout_rule_pnl(*_ohlc(trending), lookback=20, hold=5, cost_bps=10.0)
    down = breakout_rule_pnl(*_ohlc(walk), lookback=20, hold=5, cost_bps=10.0)
    assert up["net"] > 0.5, f"a trend must be found: {up['net']:.3f}"
    assert down["net"] < 0, f"a random walk must not be: {down['net']:.3f}"
    assert up["signals"] > 50, "and it must actually be trading"

    free = breakout_rule_pnl(*_ohlc(trending), lookback=20, hold=5, cost_bps=0.0)
    dear = breakout_rule_pnl(*_ohlc(trending), lookback=20, hold=5, cost_bps=25.0)
    assert free["net"] > dear["net"], "costs must be monotone"


def test_edge_ceiling_estimators_are_calibrated_on_series_with_known_structure() -> None:
    """The ceiling measurement is only worth anything if its estimators are honest.

    Three series with known properties, so a broken estimator cannot quietly
    produce a confident-looking number:

      white noise      -- must read phi ~ 0 and VR ~ 1
      AR(1) with 0.20  -- must recover the coefficient and report VR > 1
      drift only       -- a constant mean is not predictability, and an estimator
                          that confuses the two would call every bull market an edge
    """
    import random

    from tools.edge_ceiling import ar1, variance_ratio

    rng = random.Random(7)

    white = [rng.gauss(0, 0.02) for _ in range(4000)]
    assert abs(ar1(white)["phi"]) < 0.05, "white noise has no autocorrelation"
    assert abs(ar1(white)["t"]) < 3.0, "and must not look significant anyway"
    assert abs(variance_ratio(white, 2)["vr"] - 1.0) < 0.10

    ar_series: list[float] = []
    prev = 0.0
    for _ in range(4000):
        prev = 0.20 * prev + rng.gauss(0, 0.02)
        ar_series.append(prev)
    recovered = ar1(ar_series)
    assert abs(recovered["phi"] - 0.20) < 0.05, (
        f"a known 0.20 must be recovered, got {recovered['phi']:.4f}"
    )
    assert recovered["t"] > 5.0
    assert variance_ratio(ar_series, 2)["vr"] > 1.05, (
        "positive autocorrelation must show up in the variance ratio too"
    )

    drift = [0.001 + rng.gauss(0, 0.02) for _ in range(4000)]
    assert abs(ar1(drift)["phi"]) < 0.05, (
        "a constant drift is a level, not predictability; calling it an edge "
        "would make every trending market look like an opportunity"
    )


def test_a_null_result_is_only_as_strong_as_the_sample_allows() -> None:
    """Why the per-window numbers in that tool cannot be called anything.

    The standard error of an AR(1) coefficient behaves like 1/sqrt(n), so a sample
    of 749 bars cannot distinguish a true 0.05 from zero -- only 0.072 and above.
    This is the arithmetic that makes "no signal here" a statement about the
    sample size before it is a statement about the market, and it is why the
    pooled figure is the one worth reading.
    """
    from tools.edge_ceiling import resolvable_phi

    assert abs(resolvable_phi(3000) - 0.0358) < 0.001
    assert abs(resolvable_phi(749) - 0.0716) < 0.001
    assert resolvable_phi(749) > 0.05, (
        "749 bars cannot resolve the smallest edge the power sweep detects"
    )
    assert resolvable_phi(3000) < 0.05, "3000 bars can"
    assert abs((1.96 / 0.05) ** 2 - 1536.6) < 1.0, (
        "resolving the smallest detectable edge needs about 1537 bars"
    )


def test_power_harness_reproduces_the_noise_rate_when_there_is_no_edge() -> None:
    """The injection harness must not change the answer when it injects nothing.

    Every negative result in this project is "nothing cleared p90 above the noise
    floor". That is only evidence of absence if the instrument would have noticed
    something real, and nothing here had ever checked. So the check has to be
    grounded: with `phi=0` the power sweep must land on the same rate the
    false-positive calibration already reports, and a large real edge must move it
    a long way. If the injection harness shifted the baseline, every number the
    sweep produces afterwards would be about the harness rather than the market.
    """
    import random

    from tools.null_calibration import _draw

    def _rate(phi: float, trials: int = 40) -> float:
        rng = random.Random(20261005)
        cleared = sum(
            _draw(
                rng,
                structure="market",
                strategies=6,
                windows=2,
                per_window=64,
                phi=phi,
            )
            for _ in range(trials)
        )
        return cleared / (trials * 6 * 2)

    none = _rate(0.0)
    strong = _rate(0.80)
    assert 0.05 < none < 0.35, (
        f"phi=0 must reproduce the calibrated noise rate, got {none:.3f}"
    )
    assert strong > 0.85, (
        f"a large real edge must be obvious or the instrument is blind: "
        f"{strong:.3f}"
    )
    assert strong > none + 0.4, "detection must respond monotonically to size"


def test_edge_maths_is_expressed_in_autocorrelation_not_arbitrary_units() -> None:
    """The sweep is only meaningful if its axis means something outside this repo.

    `phi` is the share of each bar's move knowable from the previous bar, so a
    reader can compare it against an estimated autocorrelation on real data. The
    conversion is exact: E|z| = sqrt(2/pi) for a standard normal, against per-bar
    noise of sigma, over per_window bars.
    """
    import math

    from tools.null_calibration import _edge_per_window, _snr

    assert _edge_per_window(0.0, 64) == 0.0
    expected_snr = 0.10 * math.sqrt(2.0 / math.pi) * math.sqrt(64)
    assert abs(_snr(0.10, 64) - expected_snr) < 1e-12
    # An edge with no noise would be all of the window's return.
    assert _edge_per_window(1.0, 1) > 0.0


def test_denoising_gives_a_strategy_that_never_traded_exactly_zero() -> None:
    """The defect this replaced, pinned so it cannot come back.

    The denoiser used to subtract the median across strategies, which quietly
    assumed every strategy was exposed to the market equally -- exposure 1 for all
    of them. `volatility_breakout_example` places zero trades on this series, so
    its exposure is 0 and its net is 0, and the median subtraction handed it
    `0 - median`: a market position it never held. On the 3000-bar BTC daily run
    that produced residuals of -934.94, +901.76, -5319.45 and +188.87 for a
    strategy whose raw P&L was 0.00 in all four windows.

    Worth being precise about the severity, because the version of this story that
    said "it reached p95 in every window" is not what the numbers do. Half of
    those residuals are negative, because the sign follows the peer median. So the
    bug did not promote an inactive strategy to a winner -- it gave a strategy
    with no position a non-zero market exposure in every window, up to 5319 of it,
    which makes the column unreadable. The fix is exposure weighting, and the
    guarantee it buys is exact: exposure 0 leaves residual exactly 0.0.

    The peers below are chosen so the median is firmly positive. Under the old
    formula this strategy would have read -50.0 instead of zero here.
    """
    import statistics

    from tools.window_stability import _demean

    rows = [
        ("quiet", [0.0, 0.0], [0, 0], 0, 2),
        ("a", [110.0, 10.0], [4, 2], 2, 2),
        ("b", [50.0, -20.0], [2, 2], 1, 2),
    ]
    assert statistics.median([n[0] for _s, n, *_ in rows]) == 50.0, (
        "the median must be non-zero here, or this test cannot tell the two "
        "formulas apart"
    )
    residuals = dict(_demean(rows, {
        "quiet": [0.0, 0.0],
        "a": [300.0, 150.0],
        "b": [120.0, 200.0],
    }))
    assert residuals["quiet"] == [0.0, 0.0], (
        "a strategy with no exposure earns nothing from the market and so has "
        "nothing to have it credited"
    )
    # Window two has a negative factor, which *raises* residuals -- the old
    # "can only lower an apparent result" argument does not survive exposure
    # weighting, and pretending otherwise would be the error this test is for.
    assert residuals["a"][1] > 10.0
    # Still not a winner. Exactly zero is the strongest guarantee available: no
    # filter search can turn inactivity into a result.
    assert all(v == 0.0 for v in residuals["quiet"])


def test_denoising_removes_the_market_share_a_position_actually_carried() -> None:
    """The definition, spelled out so the formula cannot drift.

    The market factor is the window's total P&L per unit of exposure. A strategy
    holds a share of the total exposure, so it carries that share of the factor,
    and what is left is what it did that the market did not do for it.
    """
    from tools.window_stability import _demean

    rows = [
        ("a", [110.0, 10.0], [4, 2], 2, 2),
        ("b", [50.0, -20.0], [2, 2], 1, 2),
        ("c", [-70.0, 5.0], [3, 1], 1, 2),
    ]
    bars = {"a": [300.0, 150.0], "b": [120.0, 200.0], "c": [80.0, 30.0]}
    residuals = dict(_demean(rows, bars))
    for widx in range(2):
        exposure = sum(bars[s][widx] for s in bars)
        net = sum(n[widx] for _s, n, *_ in rows)
        for sid, nets, _tr, *_ in rows:
            share = bars[sid][widx] / exposure
            assert residuals[sid][widx] == nets[widx] - share * net, (
                "the residual must be net minus this strategy's exposure share "
                "of the window's total P&L"
            )


def test_a_lone_trader_is_the_market_and_residual_reads_zero() -> None:
    """One strategy in a window leaves nothing behind after the factor.

    It is worth stating what the residual can and cannot distinguish. With a
    single position in the window, that position *is* the market, so its residual
    is exactly zero: skill and market are not separable there. This is why the
    null runs the same bars and is compared against, rather than the factor
    alone being trusted to answer the question.
    """
    from tools.window_stability import _demean

    rows = [
        ("solo", [100.0], [1], 1, 1),
        ("quiet", [0.0], [0], 0, 1),
    ]
    residuals = dict(_demean(rows, {"solo": [700.0], "quiet": [0.0]}))
    assert residuals["solo"] == [0.0]
    assert residuals["quiet"] == [0.0]


def test_equal_exposure_still_shifts_every_strategy_by_the_same_amount() -> None:
    """The part of the old safety argument that does survive.

    When every strategy holds the same share of the window, the factor is a
    common shift and the ranking is untouched -- levels move, order does not. That
    is the case in which "denoising" cannot flatter anyone. It is no longer the
    general case, and the test says so by being limited to it.
    """
    from tools.window_stability import _demean

    rows = [
        ("a", [10.0, 5.0], [1, 1], 1, 2),
        ("b", [-5.0, 20.0], [1, 1], 1, 2),
        ("c", [3.0, -8.0], [1, 1], 0, 2),
    ]
    residuals = dict(_demean(rows, {
        "a": [100.0, 100.0], "b": [100.0, 100.0], "c": [100.0, 100.0],
    }))
    for i in range(2):
        raw_order = [s for s, *_ in sorted(rows, key=lambda r: -r[1][i])]
        res_order = [
            s for s, *_ in sorted(rows, key=lambda r: -residuals[r[0]][i])
        ]
        assert raw_order == res_order
        shifts = {
            round(nets[i] - residuals[sid][i], 9)
            for sid, nets, _tr, *_ in rows
        }
        assert len(shifts) == 1, "equal exposure must mean an equal shift"


def test_the_null_is_stripped_with_the_same_factor() -> None:
    """Comparing a de-meaned strategy against a raw null compares two different
    quantities.

    Each null run is stripped with its own exposure, because the runs are
    independent of one another and each carried its own position size. A run that
    placed no trades is left alone: it held nothing, so there was nothing for the
    market to pay it.
    """
    from tools.window_stability import _residual_pool

    rows = [
        ("a", [100.0], [4], 1, 1),
        ("b", [50.0], [2], 1, 1),
    ]
    bars = {"a": [400.0], "b": [200.0]}
    # total net 150 over total exposure 600 bars -> 0.25 per bar in market
    pool = _residual_pool(
        [[40.0, 60.0, -20.0]], [[300.0, 400.0, 0.0]], rows, bars
    )
    assert pool == [[-35.0, -40.0, -20.0]], (
        "each null run loses only the factor its own exposure carried"
    )
    assert pool[0][2] == -20.0, (
        "a null run that was never in the market holds none and must not be "
        "credited with any"
    )
