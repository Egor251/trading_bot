"""How predictable is the market itself? That is the ceiling on every strategy.

Every negative result in this project says the strategies found nothing. There is
a second explanation that had never been excluded: the *market* might be
unpredictable, in which case no strategy could ever have found anything and the
measurement was never the problem.

`tools.null_calibration.py` measured the other half -- the false-positive rate --
and then, later, the power: it injects an edge of a known size and reports the
fraction of windows that clear p90. That gave the axis this tool reads against.
An injected `phi` is the share of each bar's move knowable in advance from the
previous bar, so it is an autocorrelation, and an autocorrelation can be estimated
on the real series without running a single strategy.

That makes the question cheap and decisive:

  - if the market's own `phi` sits above the smallest edge the method detects,
    then "nothing was found" means there was nothing to find;
  - if it sits below, then the method was being asked to see something finer than
    the market offers, and every negative result needs restating in those terms.

It also bounds the search from above, which matters more than it sounds. If daily
BTC returns are close to uncorrelated, then no amount of tuning produces an edge
that is not fitted noise -- the ceiling is a property of the price series, not of
the strategy list, and it can be measured before anyone spends another day on it.

Two estimators, because they fail differently and disagreeing would itself be
informative. AR(1) is the direct read of the `phi` axis the power sweep is written
in. The Lo-MacKinlay variance ratio is the standard test for a random walk and
catches predictability at longer lags that AR(1) is blind to. Standard errors are
Newey-West: returns are heteroskedastic and mildly autocorrelated, and an
i.i.d. standard error on such data is optimistic by exactly the amount that makes
a small estimate look significant.

Usage:
    python -m tools.edge_ceiling 1d BTC/USDT,ETH/USDT 3000 4
"""

from __future__ import annotations

import asyncio
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: The smallest injected edge the power sweep detected above the noise floor,
#: registered there before it was run: phi = 0.05, snr 0.32.
DETECTION_FLOOR = 0.05

#: Lags for the variance-ratio ladder. 2 is the shortest lag that can differ from
#: a random walk; the rest are where trend-following claims usually live.
VR_LAGS = (2, 5, 10)


def resample(closes: list[float], group: int) -> list[float]:
    """Aggregate a close series into every `group`-th bar.

    The honest way to reach a longer horizon without asking an exchange for a
    timeframe it may not serve: weekly returns are five daily bars compounded, not
    a different price series, and computing them this way means the long-horizon
    number is measured on exactly the same data as the daily one.

    The last partial group is dropped rather than treated as a full bar. Including
    it would shorten one interval and put a spurious jump into a return series.
    """
    usable = (len(closes) // group) * group
    return [closes[i] for i in range(group - 1, usable, group)]


def _arg(index: int, default: str) -> str:
    """argv lookup that survives import from a test runner.

    Copied rather than imported from `window_stability` for the reason that
    module documents about its own STRATEGIES list: a tool that parses argv at
    import time is a tool that cannot be unit-tested.
    """
    try:
        value = sys.argv[index]
    except IndexError:
        return default
    return value if not value.startswith("-") else default


def _arg_int(index: int, default: int) -> int:
    try:
        return int(_arg(index, str(default)))
    except ValueError:
        return default


TIMEFRAME = _arg(1, "1d")
TICKERS = [t.strip() for t in _arg(2, "BTC/USDT,ETH/USDT").split(",") if t.strip()]
BARS = _arg_int(3, 3000)
WINDOWS = _arg_int(4, 4)

_OVERRIDES = {
    "services.ccxt_market_service.exchange": os.environ.get(
        "WINDOW_EXCHANGE", "binance"
    ),
    "services.phantom_service.enabled": True,
    "services.phantom_service.auto_start": True,
    "services.historical_data_service.enabled": True,
    "services.historical_data_service.auto_start": True,
    "services.historical_data_service.disk_cache_ttl_sec": 1,
    "services.ccxt_market_service.enabled": False,
    "services.ccxt_market_service.auto_start": False,
    "services.mock_market_service.enabled": False,
    "services.mock_market_service.auto_start": False,
    "services.quik_market_service.enabled": False,
    "services.quik_market_service.auto_start": False,
    "services.alpaca_market_service.enabled": False,
    "services.alpaca_market_service.auto_start": False,
    "services.ops_monitor_service.enabled": False,
    "services.ops_monitor_service.auto_start": False,
    "storage.enabled": False,
    "services.storage_service.enabled": False,
    "services.storage_service.auto_start": False,
}


def log_returns(closes: list[float]) -> list[float]:
    """Bar-to-bar log returns, skipping non-positive closes rather than raising.

    A single bad bar would otherwise abort the whole estimate, and the bars come
    from a live fetch.
    """
    out: list[float] = []
    for prev, cur in zip(closes, closes[1:]):
        if prev > 0 and cur > 0:
            out.append(math.log(cur / prev))
    return out


def ar1(returns: list[float], *, lags: int | None = None) -> dict[str, float]:
    """OLS of `r_t` on `r_{t-1}`, with a Newey-West standard error.

    Newey-West because an i.i.d. standard error is the wrong one here and errs in
    the convenient direction: returns are heteroskedastic (a quiet week and a
    violent one produce very different squared returns), so assuming independence
    understates the true variance of the estimate and inflates apparent
    significance. Lag default is Newey-West's own rule of thumb, which grows very
    slowly with n and lands near 5 at this sample size.
    """
    n = len(returns)
    if n < 20:
        return {"phi": 0.0, "se": 0.0, "t": 0.0, "n": float(n)}
    x = returns[:-1]
    y = returns[1:]
    m = len(x)
    mx = sum(x) / m
    my = sum(y) / m
    sxx = sum((v - mx) ** 2 for v in x)
    if sxx <= 0.0:
        return {"phi": 0.0, "se": 0.0, "t": 0.0, "n": float(m)}
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    phi = sxy / sxx
    alpha = my - phi * mx
    resid = [b - (alpha + phi * a) for a, b in zip(x, y)]

    if lags is None:
        lags = int(math.ceil(4.0 * (m / 100.0) ** (2.0 / 9.0)))
    u = [r * (v - mx) for r, v in zip(resid, x)]
    long_run = sum(v * v for v in u)
    for lag in range(1, max(1, lags) + 1):
        weight = 1.0 - lag / (max(1, lags) + 1.0)
        long_run += weight * 2.0 * sum(u[i] * u[i - lag] for i in range(lag, m))
    variance = long_run / (sxx * sxx)
    se = math.sqrt(variance) if variance > 0.0 else 0.0
    return {"phi": phi, "se": se, "t": (phi / se if se > 0 else 0.0), "n": float(m)}


def variance_ratio(returns: list[float], lag: int) -> dict[str, float]:
    """Lo-MacKinlay variance ratio at one lag.

    VR(q) = Var(q-bar sum) / (q * Var(1-bar)). A random walk gives 1. Above 1 means
    positive autocorrelation, below 1 means the opposite -- and on real markets the
    sign is frequently negative at short lags, which is a fact about the instrument
    as much as about the market.

    The z statistic is the asymptotic normal approximation, with the overlapping
    sums accounted for by the (2q-1)(q-1)/q correction, which is why a naive
    variance of overlapping q-sums understates the error.
    """
    n = len(returns)
    if n < 4 * lag + 10:
        return {"vr": 1.0, "z": 0.0, "n": float(n)}
    mean = sum(returns) / n
    dev = [r - mean for r in returns]
    var1 = sum(v * v for v in dev) / (n - 1)
    if var1 <= 0.0:
        return {"vr": 1.0, "z": 0.0, "n": float(n)}
    sums = [
        sum(dev[i : i + lag]) for i in range(n - lag + 1)
    ]
    m = len(sums)
    mean_q = sum(sums) / m
    var_q = sum((s - mean_q) ** 2 for s in sums) / (m - 1)
    vr = var_q / (lag * var1)
    denom = math.sqrt(2.0 * (2.0 * lag - 1.0) * (lag - 1.0) / lag)
    z = math.sqrt(n) * (vr - 1.0) / denom if denom > 0 else 0.0
    return {"vr": vr, "z": z, "n": float(n)}


def resolvable_phi(n: int, *, confidence: float = 1.96) -> float:
    """Smallest `phi` this sample size could tell apart from zero.

    The standard error of an AR(1) coefficient behaves like 1/sqrt(n), so the
    answer is confidence/sqrt(n). Stated explicitly because it is the reason the
    per-window numbers are so much weaker than the pooled one, and it is the
    reason a null result here is never proof of absence at the per-window scale.
    """
    if n <= 0:
        return float("inf")
    return confidence / math.sqrt(n)


def lag_sensitivity(returns: list[float], lags: tuple[int, ...]) -> list[dict[str, float]]:
    """Re-estimate the same AR(1) at increasing Newey-West lags.

    A dependency that is real has a standard error that settles as the lag grows.
    One that only reaches |t| > 2 at small lags and decays back towards the i.i.d.
    value is short-lived microstructure -- bid-ask bounce, stale quotes, the
    leverage effect -- and it is not something a position can hold. This is the
    difference between "statistically significant" and "tradeable", and only one
    of the two is worth acting on.
    """
    return [
        {"lags": float(lag), **ar1(returns, lags=lag)}
        for lag in lags
    ]


def lag_rule_pnl(
    opens: list[float],
    closes: list[float],
    *,
    cost_bps: float,
    lean: float = -1.0,
) -> dict[str, float]:
    """A perfect one-bar contrarian rule, traded the way it could actually be.

    The rule is the sign of the measured autocorrelation and nothing else: with the
    default `lean = -1` it goes short after an up bar and long after a down bar,
    which is what a negative phi calls for, and with `lean = +1` it is a momentum
    rule, which is what a positive phi calls for. No threshold, no period, no search
    -- the whole point is that it is fully determined by the phi measured above, so
    the result cannot be tuned.

    The signal is taken from the *close* of bar t, which is only known at the end
    of t, so the position is held from the open of t+1 to the open of t+2. That one
    bar of lag is not a detail: the autocorrelation was measured close-to-close, and
    a rule that can only see a close after it has happened and trade at the next
    open is not the same bet as the coefficient suggests. Filling on the signal bar
    would be lookahead, and would report the coefficient back to itself.

    Costs are charged on |change in position|, so a flip from long to short pays two
    fills and going flat pays one. A flipper changes position on roughly half of all
    bars, which is where the turnover comes from.
    """
    n = min(len(opens), len(closes))
    if n < 10:
        return {"gross": 0.0, "net": 0.0, "trades": 0.0, "bars": float(n)}
    gross = 0.0
    cost_total = 0.0
    position = 0.0
    trades = 0.0
    for t in range(1, n - 2):
        move = closes[t] - closes[t - 1]
        wanted = 0.0 if move == 0 else lean * (1.0 if move > 0 else -1.0)
        turnover = abs(wanted - position)
        if turnover > 0:
            trades += turnover
        entry, exit_ = opens[t + 1], opens[t + 2]
        if entry <= 0:
            continue
        # `wanted`, not the previous position. The signal is known at the close of
        # bar t, the trade happens at the open of t+1, and the bar it has to earn
        # is t+1's own move. Applying the position decided on t-1 instead skips a
        # bar entirely, and on a series with known negative autocorrelation that
        # inverts the result -- a rule that loses money where it must win.
        gross += wanted * (exit_ - entry) / entry
        cost_total += turnover * cost_bps / 10_000.0
        position = wanted
    net = gross - cost_total
    return {
        "gross": gross,
        "net": net,
        "cost": cost_total,
        "trades": trades,
        "bars": float(n),
    }


def breakeven_cost_bps(
    *, phi: float, sigma: float, flip_frequency: float = 0.5
) -> float:
    """Cost per fill at which trading the measured autocorrelation breaks even.

    A position that is short after an up bar earns phi * sigma * E|z| per bar when
    phi is negative. Dividing by the fraction of bars on which the position
    changes gives the cost per unit of turnover that exactly cancels it. Stated as
    a breakeven rather than applied with an assumed cost, because the honest
    question is not "what does it make at 10 bps" but "would this have to be free".
    """
    edge_per_bar = abs(phi) * sigma * math.sqrt(2.0 / math.pi)
    if flip_frequency <= 0:
        return float("inf")
    return edge_per_bar / flip_frequency * 10_000.0


def out_of_sample_rule(
    opens: list[float],
    closes: list[float],
    *,
    train_fraction: float = 0.5,
    cost_bps: float = 10.0,
    reverse: bool = False,
) -> dict[str, float]:
    """Take the sign of the dependence from one stretch and trade the other.

    The in-sample gross is not evidence of anything. The coefficient is estimated
    on the same bars the rule is then run over, so the rule is guaranteed to
    capture whatever the estimate happened to be fitted to -- and a +220% gross
    from a rule that flips position daily is the signature of exactly that, not of
    a free lunch nobody noticed.

    So the sign of phi is measured on one half and only then applied to the other,
    in both directions. If the dependence is real, both out-of-sample halves pay.
    If it is a fitted artefact, the second half does not, and that is the answer.
    """
    n = min(len(opens), len(closes))
    if n < 200:
        return {"gross": 0.0, "net": 0.0, "trades": 0.0, "phi_train": 0.0}
    cut = int(n * train_fraction)
    lo, hi = (cut, n) if reverse else (0, cut)
    train = [
        math.log(closes[i] / closes[i - 1])
        for i in range(lo + 1, hi)
        if closes[i] > 0 and closes[i - 1] > 0
    ]
    phi_train = ar1(train)["phi"] if len(train) >= 20 else 0.0
    # Only the sign is carried across. Magnitude is a fitted quantity; a rule that
    # only knows "which way" is the weakest claim the evidence can support.
    lean = -1.0 if phi_train < 0 else 1.0
    if phi_train == 0.0:
        lean = 0.0

    trade_lo = 0 if reverse else cut
    trade_hi = cut if reverse else n
    gross = 0.0
    cost_total = 0.0
    trades = 0.0
    position = 0.0
    for t in range(max(1, trade_lo), min(trade_hi, n - 2)):
        move = closes[t] - closes[t - 1]
        # lean carries only *which way* the dependence points; the bar's own sign
        # still decides the side. Dropping it here leaves the rule permanently
        # short -- a position that changes once in fifteen hundred bars and
        # silently reports a real dependence as worthless.
        wanted = 0.0 if move == 0 else lean * (1.0 if move > 0 else -1.0)
        turnover = abs(wanted - position)
        trades += turnover
        entry, exit_ = opens[t + 1], opens[t + 2]
        if entry <= 0:
            continue
        gross += wanted * (exit_ - entry) / entry
        cost_total += turnover * cost_bps / 10_000.0
        position = wanted
    return {
        "gross": gross,
        "net": gross - cost_total,
        "cost": cost_total,
        "trades": trades,
        "phi_train": phi_train,
    }


#: The grid, and the reason for each row, fixed before anything was measured.
#:
#: Positive return autocorrelation is not expected anywhere on daily crypto -- it
#: was measured at -0.07 there, and short of that nothing exists to find. So the
#: a priori case for looking elsewhere is the managed-futures result: trend pays at
#: horizons of weeks to months, and it pays across asset classes rather than in
#: one. That predicts two things this grid can fail: positive phi at weekly
#: horizons, and positive phi outside crypto.
#:
#: Registered before the sweep, so a positive reading cannot be turned into a
#: hypothesis after the fact:
#:
#:   grid          6 series x 2 horizons = 12 tests
#:   gate 1        phi > 0 with t above the multiplicity bar for 12 tests
#:   gate 2        the sign must carry out of sample, both directions
#:   gate 3        breakeven cost per fill must sit under 10 bps, and the rule
#:                 must be low turnover -- a trend rule pays for itself, a
#:                 bar-flipper cannot
#:
#: A result that clears gate 1 alone is expected: the maximum of twelve noisy
#: estimates lands around +0.06 at this sample size, which is exactly why gates 2
#: and 3 exist and why the bar is corrected at all.
SWEEP_GRID: tuple[tuple[str, str, str, str], ...] = (
    ("BTC/USDT", "CRYPTO", "ccxt", "crypto, measured already"),
    ("ETH/USDT", "CRYPTO", "ccxt", "crypto, measured already"),
    ("^GSPC", "EQUITY", "yahoo", "US equity index"),
    ("GC=F", "COMMODITY", "yahoo", "gold future -- a textbook trend asset"),
    ("EURUSD=X", "FOREX", "yahoo", "FX, a carry-and-drift market"),
    ("AAPL", "EQUITY", "yahoo", "single equity, idiosyncratic"),
)

SWEEP_HORIZONS: tuple[tuple[str, int], ...] = (("1d", 1), ("1w", 5))

#: One-sided Bonferroni for `tests` at alpha 0.05, two-sided normal.
GATE1_T = 2.70

#: Momentum horizons, registered before the sweep.
#:
#: Time-series momentum is the most robustly documented anomaly and the reason the
#: trend strategies in this project exist at all: the sign of the past return
#: predicts the sign of the next one. Measured here as AR(1) on *non-overlapping*
#: h-bar returns, which is the same statement without the overlapping-return bias
#: that a lead-lag correlation on daily returns would carry.
#:
#: Registered before the sweep, so a positive reading cannot become the hypothesis
#: after the fact:
#:
#:   grid    6 series x 7 horizons = 42 tests
#:   gate 1  phi > 0 with t above the multiplicity bar for 42 tests
#:   gate 2  the sign carries out of sample, both directions
#:   gate 3  the rule must still pay after 10 bps per fill
#:
#: Gate 3 is reachable here, unlike in the one-bar version. A rule that holds h bars
#: changes position about once per h bars, so its cost per bar falls as 1/h and a
#: slow rule has enormous cost headroom where a bar-flipper had none. That is the
#: whole reason a horizon sweep is worth running separately from the class sweep:
#: the first says whether an edge exists, this one says whether it could be paid for.
#:
#: Two candidates were considered and rejected as unmeasurable with the data this
#: project has. Carry is the forward premium and the interest-rate differential;
#: there is no forward curve or rate feed here, and standing in a long-run drift
#: measured on one sample is the fit this whole tool exists to refuse. Value needs
#: fundamentals -- P/E, book value -- and there is none; a cheap-versus-growth ETF
#: pair is measurable but tests a drift on a single sample, which is a well-known
#: way to find nothing and a famous way to fake something.
MOMENTUM_HORIZONS: tuple[int, ...] = (1, 5, 10, 21, 63, 126, 252)

#: One-sided Bonferroni for 42 tests at alpha 0.05.
MOMENTUM_GATE1_T = 3.10


#: Separate bar budget for the momentum sweep, and the reason it exists.
#:
#: The first registration reused `BARS` = 3000 because that is what the crypto runs
#: were fetching. It is also, exactly, Binance's daily ceiling. Applying it to every
#: series in the grid made 18 of the 42 registered tests structurally impossible: at
#: h = 63, 126 and 252 the resampled series held 47, 23 and 11 observations, and no
#: conclusion can be drawn from 11 points. Those are the horizons where time-series
#: momentum is supposed to live, so the first run did not test the hypothesis -- it
#: demonstrated that the budget could not.
#:
#: The ceiling is per provider, not per market. Asked for more, Yahoo returns 24 807
#: daily bars of S&P 500 back to 1927, which is 393 observations at h = 63 and 98 at
#: h = 252. So the horizon test is executable, just not at a crypto budget.
#:
#: This is a correction to the registration's *execution*, made before any momentum
#: result on the long history had been seen. Widening a grid after seeing which of its
#: cells came out positive is the trap this tool exists to catch, and that is not what
#: this is: the correction follows from which cells could not run at all.
MOMENTUM_BARS = int(os.environ.get("EDGE_MOMENTUM_BARS") or 30000)

#: The Elliott Wave Oscillator rule, registered from an explainer before running.
#:
#: The source is an RBK Investments explainer from June 2022, not a study. Most of
#: what it describes -- impulse waves, corrective A-B-C, the wave count -- is
#: subjective, and the article says so itself, quoting the standard objection that
#: one may see many things in a price chart. None of that is used.
#:
#: What it does supply is the method's own non-subjective substitute, stated
#: mechanically: the EWO is the difference of a 5-bar and a 34-bar moving average,
#: which the article is explicit "does not use the wave-detection rules", and the
#: trade is to be long while it is above zero on a trough-to-peak leg and short
#: while it is below zero on a peak-to-trough leg.
#:
#: So the honest description of what is being tested is a moving-average crossover
#: on 5 and 34 bars -- the periods are the article's, not chosen here -- and any
#: result belongs to that rule, not to wave theory. It is worth one test because
#: the rule has a strong prior and because its profile is the one that survived the
#: previous registered sweep: slow, low turnover, few fills to pay for.
#:
#: Registered with nothing seen. This is the seventh registered test of a trading
#: rule in this repository, and the multiplicity bar accounts for it.
EWO_FAST = 5
EWO_SLOW = 34
EWO_TIMEFRAMES: tuple[str, ...] = ("1d", "4h", "1h")
EWO_GATE1_T = 2.70

#: The mechanism test, registered before it is run.
#:
#: The claim being tested is not the rule. It is the explanation for the rule's daily
#: result: that MA(5) vs MA(34) earns its money from the *significantly negative*
#: return autocorrelation measured independently (-0.0677 BTC, -0.0721 ETH), and from
#: nothing else. The rule was blind to a 36-fold uptrend (+2.2% net), so drift cannot
#: be the source -- but drift has to be ruled out per series anyway, because an index
#: that rose 4 000-fold hands a long-biased rule a large number for free.
#:
#: Four predictions, all falsifiable, over ten observations:
#:
#:   1  every observation with phi t < -2.0 has rule net > 0
#:   2  no observation with phi t > -1.0 has rule net > 0
#:   3  Spearman rank correlation of phi against rule net is positive
#:   4  on at least one series where buy-and-hold is not positive, the rule is net
#:      positive -- the only place drift cannot explain anything
#:
#: Prediction 4 is the one that carries weight. Predictions 1 to 3 can all hold on a
#: set of instruments that only went up. Money made on a falling market cannot be
#: drift, and that is what a reversal claim has to show.
#:
#: One test with ten observations, not ten tests: the rule itself was already
#: registered, and this asks whether its result follows from a number measured by a
#: different method on the same bars.
#: Prediction 4 demanded a down market and there was none in the sample, so the
#: periods are named here rather than searched for. Fixed in advance:
#:
#:   1929-08 .. 1932-11   the S&P fell about 86% peak to trough
#:   2007-10 .. 2009-03   it fell about 57%
#:   2018-01 .. 2018-12   a crypto bear market
#:   2021-11 .. 2022-11   a larger crypto bear market
#:
#: Registered criterion: the rule must be net positive at 10 bps in at least two of
#: them. Money made while the market falls is the only thing in this whole sequence
#: that drift cannot manufacture, and it is what would separate a reversal effect
#: from directional exposure. If the rule loses in every named down period, then the
#: +334% and +406% are drift with extra steps, and the daily result should be struck
#: from the record rather than re-explained.
#: The confirmation test, on data that formed no part of the impression.
#:
#: Everything so far -- the +334% and +406% on BTC and ETH, the four of four down
#: periods -- came from two instruments whose daily autocorrelation had already been
#: measured and discussed before the rule was run. Whatever those results are worth,
#: they are the results that were *expected* to look like that, so they cannot also
#: confirm it.
#:
#: Two fresh axes, neither used anywhere above:
#:
#:   instruments   SOL, XRP and DOGE have never appeared in any test here, and the
#:                 Nasdaq 100 and Russell 2000 have not either. Same rule, same
#:                 5/34 periods from the source article, same 10 bps.
#:   periods       the 1929 crash, 2008 and the 2021-22 bear market were used. These
#:                 were not: the 1968-82 secular bear, the 2000-02 collapse, and the
#:                 2020 COVID drawdown.
#:
#: Registered criteria, both required:
#:
#:   A  net positive after 10 bps on at least 4 of the 5 fresh instruments, and
#:      t > 2.70 on at least one of them
#:   B  net positive after 10 bps on at least 2 of the 3 fresh bear periods, each of
#:      which must have a negative buy-and-hold
#:
#: Nothing here was looked at before this was written. Note the honest asymmetry:
#: SOL, XRP and DOGE are younger and far more volatile than BTC, so they carry fewer
#: bars and more of them in filings. That cuts both ways and is why criterion A asks
#: for four of five rather than all five.
#: The indicator panel, registered before it is run.
#:
#: The request is to add more oscillators. Taken literally -- try them all and keep
#: the best -- that is best-of-N, and the size of the effect is already measured in
#: this repository: with a t-bar of 2.70 across 12 tests, about 0.04 pass by chance.
#: A panel of 20 with a looser bar would return a winner every time, and nothing
#: here would be able to tell it from luck.
#:
#: So this is framed as a panel study rather than a search, which answers the
#: question actually being asked -- is there signal in technical indicators at all --
#: and answers it in a way that survives the answer being no.
#:
#: Three properties make it a measurement rather than a sweep:
#:
#:   1  every oscillator is used through the same functional form: its output is
#:      turned into a trailing 60-bar z-score, and the position is the sign of that
#:      score. Nothing about the rule depends on which indicator it is.
#:   2  **both signs are tested for every oscillator.** Whether an indicator is a
#:      trend tool or a reversion tool is not known in advance, so guessing and
#:      scoring only the favourable direction would smuggle the answer in. This
#:      doubles the panel and makes the multiplicity explicit rather than hidden.
#:   3  every parameter is the library default. No per-oscillator tuning -- tuning
#:      each of twenty indicators turns a twenty-test panel into a search over
#:      thousands of configurations.
#:
#: Registered criteria:
#:
#:   grid   13 oscillators x 2 signs x 2 instruments = 52 tests
#:   bar    t > 3.10, one-sided Bonferroni for 52 tests at alpha 0.05
#:   pass   net positive after 10 bps on at least 2 instruments, t above the bar on
#:          at least one, and both signs agreeing in direction
#:
#: Expected outcome, stated first so it cannot be argued with afterwards: nothing
#: passes, and roughly one to two will look positive without clearing the bar. The
#: value of the study is that corrected count, not a winner.
#: The library turned out to hold fourteen usable oscillators rather than the
#: thirteen registered, so the grid is 56 tests rather than 52. Recorded rather than
#: quietly rounded: the bar moves from 3.10 to 3.11, which changes no verdict here.
#: Volatility-expansion reversal, registered before it is run.
#:
#: This is the one direction the measurements actually point at, and the reason it
#: has not paid in anything above is frequency rather than absence.
#:
#: What is measured, not hoped for:
#:
#:   lag-1 return autocorrelation is significantly negative -- -0.0677 on BTC and
#:     -0.0721 on ETH, t = -2.26 and -2.61, stable across Newey-West lags 1 to 30,
#:     with variance ratios of 0.933 and 0.928
#:   magnitude is strongly persistent -- corr(|r_t|, |r_t+1|) = 0.165 and 0.161, and the
#:     next bar is 1.28x to 1.54x larger after a large one
#:
#: A contrarian rule aimed at the first and triggered by the second is therefore not
#: a guess. What has killed every version so far is that it was run at one decision per
#: bar, where turnover is 1.07 flips per bar and the breakeven cost is 35 bps against
#: a market charging 4 to 10. Holding longer is the only lever that changes that
#: arithmetic, so holding period is the parameter that matters -- and it is set by the
#: cost arithmetic, not searched.
#:
#: Two holding periods are tested, and they are a genuine fork between the two
#: mechanisms rather than a search:
#:
#:   H = 2   trusts the measured 1-bar autocorrelation and nothing else
#:   H = 10  trusts the persistence of magnitude -- a volatility expansion implies
#:           more volatility, and a reversion that plays out over several bars is
#:           the same effect read at a coarser scale
#:
#: Trigger is a 2-sigma move, the conventional definition of a volatility expansion,
#: not a fitted threshold. Direction is contrarian, taken from the sign of the
#: measured phi rather than from testing both ways.
#:
#:   grid   2 instruments x 2 horizons x 2 holding periods = 8 tests
#:   bar    t > 2.90, one-sided Bonferroni for 8 at alpha 0.05
#:
#: The primitives are printed regardless of the verdict, because the useful output
#: here is the breakeven cost: if it sits below what the venue charges, no amount of
#: cleverness helps, and that is worth knowing without having to infer it from a P&L.
EXPANSION_K_SIGMA = 2.0
EXPANSION_HOLDS: tuple[int, ...] = (2, 10)
EXPANSION_TIMEFRAMES: tuple[str, ...] = ("4h", "1h")
EXPANSION_GATE_T = 2.90

#: Three follow-ups, all registered before any of them is run.
#:
#: (1) The breakeven curve. The effect is worth about 15 bps per trade and a round
#:     trip costs about 20, so it loses by a margin. That number is worth pinning
#:     exactly rather than approximately, because it is the difference between an
#:     effect the market has already arbitraged and one it has not.
#:
#: (2) Severity filtering. The conditional reversion after a *large* move is bigger
#:     than after a merely ordinary one -- measured, -0.00047 after 1.5 sigma against
#:     -0.00472 after 2 sigma. If edge per trade keeps growing with severity while the
#:     cost per trade is fixed at 20 bps, fewer and larger trades win. Three
#:     severities x two holding periods x two instruments = 12 tests, bar t > 3.00.
#:
#: (3) **Is it the bid-ask bounce?** A lag-1 autocorrelation of -0.044 at 4h is
#:     almost certainly microstructure, not a tradable reversal, and this is the test
#:     that decides whether the 15 bps is capturable at all. Bid-ask bounce is a fixed
#:     fraction of the spread, so it predicts three things, all registered:
#:
#:       - the effect scales with the *spread*, which is largest as a fraction on the
#:         cheapest, least liquid pair. DOGE and XRP carry it; a BTC/ETH pair where
#:         BTC costs roughly three orders of magnitude more does not, proportionally.
#:       - the conditional reversion should be roughly the spread itself, so its
#:         magnitude in return units should be similar across assets once expressed
#:         as a fraction of the spread rather than as a raw return
#:       - it should weaken or vanish on the most liquid pair, which has the tightest
#:         relative spread
#:
#:     Registered criterion, and it is a real test either way: the conditional mean
#:     after a 2 sigma move, as a fraction of daily sigma, must be at least 30% larger
#:     on the cheapest asset than on BTC. If it is, the effect is the spread and the
#:     15 bps is not harvestable by anything. If the ordering does not hold, bounce is
#:     not the whole story and the edge deserves one more look.
#:
#:     If this fails, the honest reading is that we have measured a real statistical
#:     effect which happens to be worth less than the cost of trading it. That is a
#:     margin, not a money-maker, and it should be described in those words.
SEVERITY_K: tuple[float, ...] = (2.0, 2.5, 3.0)
SEVERITY_HOLDS: tuple[int, ...] = (2, 5)
SEVERITY_GATE_T = 3.00
BOUNCE_RATIO_MIN = 1.30

PANEL_OSCILLATORS = 14
PANEL_SIGNS = 2
PANEL_INSTRUMENTS = 2
PANEL_TESTS = PANEL_OSCILLATORS * PANEL_SIGNS * PANEL_INSTRUMENTS
PANEL_GATE_T = 3.11

FRESH_INSTRUMENTS: tuple[tuple[str, str, str], ...] = (
    ("SOL/USDT", "CRYPTO", "ccxt"),
    ("XRP/USDT", "CRYPTO", "ccxt"),
    ("DOGE/USDT", "CRYPTO", "ccxt"),
    ("^NDX", "EQUITY", "yahoo"),
    ("^RUT", "EQUITY", "yahoo"),
)

FRESH_BEARS: tuple[tuple[str, str, str, str], ...] = (
    ("^GSPC", "EQUITY", "yahoo", "1968-01-01/1982-12-31"),
    ("^GSPC", "EQUITY", "yahoo", "2000-03-01/2002-10-09"),
    ("^GSPC", "EQUITY", "yahoo", "2018-10-01/2020-03-23"),
)

FRESH_MIN_INSTRUMENTS = 4
FRESH_MIN_T = 2.70
FRESH_MIN_BEARS = 2

DOWN_PERIODS: tuple[tuple[str, str, str, str], ...] = (
    ("^GSPC", "EQUITY", "yahoo", "1929-08-01/1932-11-30"),
    ("^GSPC", "EQUITY", "yahoo", "2007-10-01/2009-03-31"),
    ("BTC/USDT", "CRYPTO", "ccxt", "2018-01-01/2018-12-31"),
    ("BTC/USDT", "CRYPTO", "ccxt", "2021-11-01/2022-11-30"),
)
DOWN_MIN_WINS = 2

MECHANISM_SERIES: tuple[tuple[str, str, str], ...] = (
    ("BTC/USDT", "CRYPTO", "ccxt"),
    ("ETH/USDT", "CRYPTO", "ccxt"),
    ("^GSPC", "EQUITY", "yahoo"),
    ("GC=F", "COMMODITY", "yahoo"),
    ("EURUSD=X", "FOREX", "yahoo"),
    ("AAPL", "EQUITY", "yahoo"),
)

#: The range-breakout test, registered before it was run.
#:
#: The hypothesis has a source that is not this repository. A 2024 note in
#: "Progressive Economy" (Kusaev, DOI 10.54861/27131211_2024_4_83) describes a
#: head-and-shoulders on BTC and states the tradeable part mechanically: the signal
#: is the neckline break, and the neckline is the range boundary. What is taken here
#: is that *rule*. None of the note's conclusions are used, because it has none that
#: can be checked -- it contains no returns, no sample and no test, and its central
#: claim is that the patterns "do not contradict" wave theory, which is a statement
#: about interpretability rather than about edge. The author notes the patterns can
#: be read several ways, which is the reason they need measuring rather than reading.
#:
#: Rule: long when the close exceeds the highest high of the previous `LOOKBACK`
#: bars, short when it falls below the lowest low, flat otherwise, holding each
#: position for `HOLD` bars. Both directions, because the research engine's default
#: long/flat mode would make half of every breakout unreachable.
#:
#: Parameters are taken by convention and are not tuned -- 20 and 5 bars -- and are
#: applied unchanged at every timeframe. Only the timeframe varies, so the grid is
#: three tests, not thirty.
#:
#:   grid    1 series x 3 timeframes = 3 tests
#:   gate 1  net positive after 10 bps per fill, and t > 2.70 (Bonferroni, 3 tests)
#:   gate 2  the first and second halves of the series must both be net positive
#:
#: Registered with no result seen. A lookback sweep would be a parameter search with
#: one parameter's clothing on, and the best of thirty lookbacks on a single sample
#: is the exact illusion this repository was assembled to dismantle.
BREAKOUT_LOOKBACK = 20
BREAKOUT_HOLD = 5
BREAKOUT_TIMEFRAMES: tuple[str, ...] = ("1d", "4h", "1h")
BREAKOUT_GATE1_T = 2.70


async def momentum_sweep(
    fetch: Any, grid: tuple[tuple[str, str, str, str], ...], bars: int
) -> list[dict[str, Any]]:
    """Time-series momentum at registered horizons, same gates as the class sweep.

    Non-overlapping h-bar returns, then AR(1) on them: whether a trend of length h
    predicts the next trend of length h. Returns are the traded rule at 10 bps per
    fill, which is the gate that can actually bind here -- a rule holding 63 bars
    pays costs sixty-three times less often than a bar-flipper, so this is where a
    real edge would finally survive the arithmetic.
    """
    rows: list[dict[str, Any]] = []
    for ticker, class_code, provider, why in grid:
        try:
            series = await fetch(ticker, class_code, provider)
        except Exception as exc:  # noqa: BLE001
            rows.append(
                {"ticker": ticker, "h": "-", "note": f"unavailable: {type(exc).__name__}"}
            )
            continue
        if not series or len(series) < 120:
            rows.append(
                {"ticker": ticker, "h": "-", "note": f"only {len(series or [])} bars"}
            )
            continue
        closes = [float(getattr(b, "close", 0.0) or 0.0) for b in series]
        opens = [float(getattr(b, "open", 0.0) or 0.0) for b in series]
        if any(c <= 0 for c in closes):
            rows.append({"ticker": ticker, "h": "-", "note": "non-positive closes"})
            continue
        for h in MOMENTUM_HORIZONS:
            cl = resample(closes, h) if h > 1 else closes
            op = resample(opens, h) if h > 1 else opens
            if len(cl) < 60:
                rows.append(
                    {"ticker": ticker, "h": h, "note": f"{len(cl)} bars at h={h}"}
                )
                continue
            rets = log_returns(cl)
            est = ar1(rets)
            a = out_of_sample_rule(op, cl, cost_bps=10.0)
            b = out_of_sample_rule(op, cl, cost_bps=10.0, reverse=True)
            traded = lag_rule_pnl(op, cl, cost_bps=10.0, lean=1.0)
            rows.append(
                {
                    "ticker": ticker,
                    "class": class_code,
                    "h": h,
                    "why": why,
                    "n": len(rets),
                    "phi": est["phi"],
                    "t": est["t"],
                    "net_at_10bp": traded["net"],
                    "turnover_per_bar": traded["trades"] / max(1, len(cl)),
                    "gate1": est["phi"] > 0 and est["t"] > MOMENTUM_GATE1_T,
                    "gate2": a["gross"] > 0 and b["gross"] > 0,
                    "gate3": traded["net"] > 0,
                }
            )
    return rows


async def sweep(
    fetch: Any, grid: tuple[tuple[str, str, str, str], ...], bars: int
) -> list[dict[str, Any]]:
    """Run the grid, apply the three gates, and return one row per test.

    `gate 1` is the only gate that can be satisfied by luck, which is the whole
    reason for the correction: twelve independent estimates at this sample size
    will produce one comfortably significant positive phi even when the true value
    is zero everywhere. Gates 2 and 3 cannot be passed by accident, because both
    require the dependence to survive being measured on data that did not produce
    it, and to be worth more than what the market charges to trade it.
    """
    rows: list[dict[str, Any]] = []
    for ticker, class_code, provider, why in grid:
        try:
            series = await fetch(ticker, class_code, provider)
        except Exception as exc:  # noqa: BLE001 -- a source being down is data
            rows.append(
                {"ticker": ticker, "horizon": "-", "why": why,
                 "note": f"unavailable: {type(exc).__name__}"}
            )
            continue
        if not series or len(series) < 120:
            rows.append(
                {"ticker": ticker, "horizon": "-", "why": why,
                 "note": f"only {len(series or [])} bars"}
            )
            continue
        closes = [float(getattr(b, "close", 0.0) or 0.0) for b in series]
        opens = [float(getattr(b, "open", 0.0) or 0.0) for b in series]
        if any(c <= 0 for c in closes):
            rows.append(
                {"ticker": ticker, "horizon": "-", "why": why,
                 "note": "non-positive closes"}
            )
            continue
        for label, group in SWEEP_HORIZONS:
            if len(closes) // group < 120:
                rows.append(
                    {"ticker": ticker, "horizon": label, "why": why,
                     "note": "too few bars after resampling"}
                )
                continue
            cl = resample(closes, group) if group > 1 else closes
            op = resample(opens, group) if group > 1 else opens
            rets = log_returns(cl)
            est = ar1(rets)
            sigma = math.sqrt(sum(r * r for r in rets) / len(rets))
            # Gate 2: the sign alone, carried across a half-sample, both ways.
            a = out_of_sample_rule(op, cl, cost_bps=10.0)
            b = out_of_sample_rule(op, cl, cost_bps=10.0, reverse=True)
            oos_pays = a["gross"] > 0 and b["gross"] > 0
            turnovers = (a["trades"] + b["trades"]) / max(1, len(cl))
            be = breakeven_cost_bps(phi=est["phi"], sigma=sigma)
            gate1 = est["phi"] > 0 and est["t"] > GATE1_T
            rows.append(
                {
                    "ticker": ticker,
                    "class": class_code,
                    "horizon": label,
                    "why": why,
                    "n": len(rets),
                    "phi": est["phi"],
                    "se": est["se"],
                    "t": est["t"],
                    "sigma": sigma,
                    "gate1": gate1,
                    "oos_a": a["gross"],
                    "oos_b": b["gross"],
                    "gate2": oos_pays,
                    "breakeven_bps": be,
                    "turnover": turnovers,
                    "gate3": be < 10.0 and turnovers < 0.5,
                }
            )
    return rows


def volatility_structure(returns: list[float]) -> dict[str, float]:
    """Is the *magnitude* of the next move predictable, if its sign is not?

    Everything negative this tool has reported so far was measured on the sign of
    the return. That is the wrong question for a breakout or gap strategy, which
    does not need to know which way the next bar goes -- it needs to know that the
    next bar is going to be a large one. Those are separate properties, and
    volatility clustering is one of the most robust regularities in finance, so a
    sign-based sweep could have returned nothing while the tradable structure sat
    unmeasured next to it.

    Four readings, from the crude to the formal:

      `rho_abs`     corr(|r_t|, |r_{t+1}|) -- the plain persistence of big moves
      `arch1`       OLS of r^2_t on r^2_{t-1} -- the ARCH(1) coefficient, the
                    formal statement that large moves follow large moves
      `vol_ratio`   E[r^2_{t+1} | r^2_t above median] divided by the same below
                    -- how much bigger the next move is after a big one, in the
                    units a position actually cares about
      `sign_to_size` corr(r_t, |r_{t+1}|) -- does today's *direction* predict
                    tomorrow's *magnitude*. This is the breakout premise stated as
                    a single correlation, and it is the one the sign-based work
                    never looked at.
    """
    n = len(returns)
    out = {"rho_abs": 0.0, "arch1": 0.0, "vol_ratio": 0.0, "sign_to_size": 0.0}
    if n < 30:
        return out

    def _corr(xs: list[float], ys: list[float]) -> float:
        m = len(xs)
        if m < 3:
            return 0.0
        mx, my = sum(xs) / m, sum(ys) / m
        num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
        den = (
            sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys)
        ) ** 0.5
        return (num / den) if den else 0.0

    abs_rets = [abs(r) for r in returns]
    squares = [r * r for r in returns]
    out["rho_abs"] = _corr(abs_rets[:-1], abs_rets[1:])
    out["arch1"] = _corr(squares[:-1], squares[1:])
    ordered = sorted(squares)
    median_sq = ordered[len(ordered) // 2]
    high = [
        b for a, b in zip(squares[:-1], squares[1:]) if a > median_sq
    ]
    low = [
        b for a, b in zip(squares[:-1], squares[1:]) if a <= median_sq
    ]
    if high and low and sum(low) > 0:
        out["vol_ratio"] = (sum(high) / len(high)) / (sum(low) / len(low))
    out["sign_to_size"] = _corr(returns[:-1], abs_rets[1:])
    return out


def breakout_expectancy(
    returns: list[float], *, threshold: float, sigma: float | None = None
) -> dict[str, float]:
    """Mean next-bar return after a move of more than `threshold` sigma.

    This is the breakout premise stated as the thing a position actually needs:
    volatility clustering says the next move will be *large* after a large one,
    which is necessary but not sufficient -- clustering raises the variance of
    returns, not their mean, and a strategy with more variance and no mean has no
    expectancy. What pays the costs is whether the move is *directionally* worth
    being in, which is this number and nothing else.

    `side` = +1 measures a long breakout (enter after an up bar), -1 a short one.
    Reported in units of the series' own volatility, so it can be compared against
    a cost expressed per bar.
    """
    out = {"n": 0.0, "mean": 0.0, "t": 0.0, "mean_long": 0.0, "mean_short": 0.0}
    if sigma is None:
        if len(returns) < 2:
            return out
        sigma = math.sqrt(sum(r * r for r in returns) / len(returns))
    if sigma <= 0:
        return out
    cut = threshold * sigma
    nxt = returns[1:]
    lead = returns[:-1]
    for side, key in ((1.0, "mean_long"), (-1.0, "mean_short")):
        picks = [
            side * b
            for a, b in zip(lead, nxt)
            if side * a > cut
        ]
        if not picks:
            continue
        mean = sum(picks) / len(picks)
        out[key] = mean
        if key == "mean_long":
            m = len(picks)
            var = sum((p - mean) ** 2 for p in picks) / (m - 1) if m > 1 else 0.0
            se = (var / m) ** 0.5 if var > 0 else 0.0
            out["n"] = float(m)
            out["mean"] = mean
            out["t"] = (mean / se) if se > 0 else 0.0
    return out


def breakout_rule_pnl(
    highs: list[float],
    lows: list[float],
    opens: list[float],
    closes: list[float],
    *,
    lookback: int,
    hold: int,
    cost_bps: float,
) -> dict[str, float]:
    """A range breakout: long above the recent high, short below the recent low.

    This is the operational content of the pattern a 2024 note describes -- a
    head-and-shoulders reduced to the part that can be measured without a human
    looking at it: the neckline is the range boundary, and the trade is the break.
    The note's *conclusions* are not used. It contains no returns, no sample and no
    test, and its central claim is that the patterns "do not contradict" wave
    theory, which is a statement about ambiguity rather than about edge.

    The rule takes its parameters by convention and is not tuned: lookback 20, hold
    5, in bars, applied unchanged across every timeframe. A version swept over
    lookbacks would be a parameter search wearing one parameter's clothing, and the
    best of thirty lookbacks on one sample is exactly the illusion this repository
    was assembled to remove.

    Long and short are both allowed here. The research engine runs long/flat by
    default (`forbid_net_flip`), which would make half of every breakout
    unreachable -- so this is computed from OHLC directly, which is also why it
    needs no engine change to be measured. Integrating a winner as a plugin
    strategy would still require opening up the position mode.
    """
    n = min(len(opens), len(closes), len(highs), len(lows))
    out = {"gross": 0.0, "cost": 0.0, "net": 0.0, "trades": 0.0,
           "signals": 0.0, "bars": float(max(0, n - lookback)),
           "t": 0.0, "n_pos": 0.0}
    if n <= lookback + 2 or lookback < 2 or hold < 1:
        return out
    position = 0.0
    entry_index = -10 ** 6
    gross = 0.0
    cost_total = 0.0
    trades = 0.0
    signals = 0.0
    per_bar: list[float] = []
    for t in range(lookback, n - 2):
        want = position
        if position == 0.0 or (t - entry_index) >= hold:
            window_hi = max(highs[t - lookback : t])
            window_lo = min(lows[t - lookback : t])
            if closes[t] > window_hi:
                want = 1.0
            elif closes[t] < window_lo:
                want = -1.0
            else:
                want = 0.0
            if want != 0.0:
                signals += 1.0
        turnover = abs(want - position)
        trades += turnover
        entry, leave = opens[t + 1], opens[t + 2]
        bar_ret = 0.0
        if entry > 0:
            bar_ret = want * (leave - entry) / entry
            gross += bar_ret
        cost = turnover * cost_bps / 10_000.0
        cost_total += cost
        if want != 0.0:
            per_bar.append(bar_ret - cost)
        if want != position:
            entry_index = t
        position = want
    out["gross"] = gross
    out["cost"] = cost_total
    out["net"] = gross - cost_total
    out["trades"] = trades
    out["signals"] = signals
    # Significance of the per-bar returns earned while in a position. Approximate:
    # these overlap when a position lasts more than one bar, so the standard error
    # is understated. Reported because a rule that pays only by luck still has a
    # mean, and the mean alone is not evidence.
    if len(per_bar) > 5:
        m = len(per_bar)
        avg = sum(per_bar) / m
        var = sum((v - avg) ** 2 for v in per_bar) / (m - 1)
        se = (var / m) ** 0.5 if var > 0 else 0.0
        out["t"] = (avg / se) if se > 0 else 0.0
        out["n_pos"] = float(m)
    return out


def ewo_rule_pnl(
    opens: list[float],
    closes: list[float],
    *,
    fast: int,
    slow: int,
    cost_bps: float,
) -> dict[str, float]:
    """Long above zero on the EWO, short below it. The moving-average crossover.

    `slow` has to have warmed up before the rule can say anything at all, and the
    bars used to warm it are not counted as flat performance -- they are excluded,
    because measuring a moving average over the bars that produced it is how a rule
    appears to trade during the period it was still being defined.
    """
    n = min(len(opens), len(closes))
    out = {
        "gross": 0.0, "cost": 0.0, "net": 0.0, "trades": 0.0,
        "t": 0.0, "n_pos": 0.0, "bars": 0.0,
    }
    if n <= slow + 2 or slow < 2 or fast >= slow:
        return out

    def _ma(end: int, span: int) -> float:
        return sum(closes[end - span : end]) / span

    position = 0.0
    gross = 0.0
    cost_total = 0.0
    trades = 0.0
    per_bar: list[float] = []
    for t in range(slow, n - 2):
        ewo = _ma(t, fast) - _ma(t, slow)
        want = 1.0 if ewo > 0 else (-1.0 if ewo < 0 else 0.0)
        turnover = abs(want - position)
        trades += turnover
        entry, leave = opens[t + 1], opens[t + 2]
        bar_ret = 0.0
        if entry > 0:
            bar_ret = want * (leave - entry) / entry
            gross += bar_ret
        c = turnover * cost_bps / 10_000.0
        cost_total += c
        if want != 0.0:
            per_bar.append(bar_ret - c)
        position = want
    out["gross"] = gross
    out["cost"] = cost_total
    out["net"] = gross - cost_total
    out["trades"] = trades
    out["bars"] = float(n - slow)
    if len(per_bar) > 5:
        m = len(per_bar)
        avg = sum(per_bar) / m
        var = sum((v - avg) ** 2 for v in per_bar) / (m - 1)
        se = (var / m) ** 0.5 if var > 0 else 0.0
        out["t"] = (avg / se) if se > 0 else 0.0
        out["n_pos"] = float(m)
    return out


def spearman(xs: list[float], ys: list[float]) -> float:
    """Rank correlation, ties averaged. Order only, which is what the claim is about."""
    def _ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            shared = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[order[k]] = shared
            i = j + 1
        return ranks

    if len(xs) != len(ys) or len(xs) < 3:
        return 0.0
    rx, ry = _ranks(xs), _ranks(ys)
    n = len(rx)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (
        sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)
    ) ** 0.5
    return (num / den) if den else 0.0


def panel_signals(
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> dict[str, list[float | None]]:
    """Every indicator in the library, as one signed number per bar.

    Each output is centred so that a positive number means "leaning up" on its own
    terms -- RSI at 50, %K at 50, Bollinger %b at 0.5 -- so that the panel does not
    need a separate rule per indicator. None of these are z-scored here; that
    happens downstream, uniformly, because a raw RSI and a raw CCI have different
    scales and the position rule must not depend on which one it is reading.

    Anything that fails to compute is omitted rather than silently filled, and the
    caller reports what it actually got instead of what it expected.
    """
    from engine.indicators import (
        cci,
        macd,
        roc,
        rsi,
        stochastic,
        williams_r,
    )

    out: dict[str, list[float | None]] = {}
    failures: dict[str, str] = {}

    def _safe(name: str, fn) -> None:
        """Record the failure instead of swallowing it.

        The first version of this function caught the exception and passed, and the
        consequence was a panel that reported 24 of 52 registered rows with no
        explanation: eight indicators had failed on a wrong keyword argument and a
        wrong tuple unpack, and a half-panel that looks complete is worse than no
        panel, because the missing half is exactly the part nobody checks.
        """
        try:
            out[name] = list(fn())
        except Exception as exc:  # noqa: BLE001
            failures[name] = f"{type(exc).__name__}: {exc}"

    _safe("rsi-14", lambda: [(v - 50.0) if v is not None else None
                             for v in rsi(closes, 14)])
    _safe("macd-12-26-9", lambda: [
        (h if h is not None else None) for h in macd(closes)[2]
    ])
    _safe("bollinger-20-2", lambda: _bollinger_signed(closes))
    _safe("stoch-14-3", lambda: [
        (k - 50.0) if k is not None else None
        for k in stochastic(highs, lows, closes)[0]
    ])
    _safe("cci-20", lambda: [(v / 100.0) if v is not None else None
                             for v in cci(highs, lows, closes, 20)])
    _safe("williams-14", lambda: [
        (v + 50.0) if v is not None else None
        for v in williams_r(highs, lows, closes, 14)
    ])
    _safe("roc-12", lambda: [(v / 10.0) if v is not None else None
                            for v in roc(closes, 12)])
    _safe("adx-14", lambda: _adx_signed(highs, lows, closes, 14))
    _safe("sar", lambda: _sar_signed(highs, lows))
    _safe("keltner-20", lambda: _keltner_signed(highs, lows, closes))
    _safe("heikin-ashi", lambda: _ha_signed(highs, lows, closes))
    _safe("vwap-20", lambda: _vwap_signed(highs, lows, closes))
    _safe("sma-20", lambda: _sma_signed(closes, 20))
    _safe("ema-50", lambda: _sma_signed(closes, 50))
    if failures:
        for name, why in failures.items():
            print(f"  INDICATOR FAILED: {name} -- {why}")
    return out


def _bollinger_signed(closes: list[float]) -> list[float | None]:
    from engine.indicators import bollinger

    _mid, upper, lower = bollinger(closes, 20, num_std=2.0)
    out: list[float | None] = []
    for i, u in enumerate(upper):
        l = lower[i]
        if u is None or l is None or u == l:
            out.append(None)
        else:
            out.append((closes[i] - l) / (u - l) - 0.5)
    return out


def _sma_signed(closes: list[float], window: int) -> list[float | None]:
    from engine.indicators import sma

    line = sma(closes, window)
    return [
        (closes[i] - line[i]) if line[i] is not None else None
        for i in range(len(closes))
    ]


def _adx_signed(
    highs: list[float], lows: list[float], closes: list[float], period: int
) -> list[float | None]:
    from engine.indicators import adx

    _adx_v, plus_di, minus_di = adx(highs, lows, closes, period)
    return [
        (p - m) if (p is not None and m is not None) else None
        for p, m in zip(plus_di, minus_di)
    ]


def _sar_signed(
    highs: list[float], lows: list[float]
) -> list[float | None]:
    from engine.indicators import parabolic_sar

    _sar, direction = parabolic_sar(highs, lows)
    return [
        (float(d) if d is not None else None) for d in direction
    ]


def _keltner_signed(
    highs: list[float], lows: list[float], closes: list[float]
) -> list[float | None]:
    from engine.indicators import keltner

    mid, _upper, _lower = keltner(highs, lows, closes)
    return [
        (closes[i] - m) if m is not None else None for i, m in enumerate(mid)
    ]


def _ha_signed(
    highs: list[float], lows: list[float], closes: list[float]
) -> list[float | None]:
    from engine.indicators import heikin_ashi

    ho, hh, hl, hc = heikin_ashi(closes, highs, lows, closes)
    return [
        (c - o) for c, o in zip(hc, closes)
    ]


def _vwap_signed(
    highs: list[float], lows: list[float], closes: list[float]
) -> list[float | None]:
    from engine.indicators import rolling_vwap

    vwap = rolling_vwap(highs, lows, closes, None, 20)
    return [
        (closes[i] - v) if v is not None else None for i, v in enumerate(vwap)
    ]


def rolling_zscore(
    values: list[float | None], window: int
) -> list[float | None]:
    """Trailing z-score, skipping bars where the indicator has no value yet.

    Warmup matters here and is not a detail: an oscillator that emits nothing for its
    first fifty bars has not been "flat" during them, it has not existed. Scoring it
    as flat would quietly buy the indicator the easiest part of the series.
    """
    out: list[float | None] = []
    clean: list[float] = []
    for v in values:
        if v is None:
            out.append(None)
            continue
        clean.append(float(v))
        if len(clean) < window:
            out.append(None)
            continue
        chunk = clean[-window:]
        mean = sum(chunk) / window
        var = sum((c - mean) ** 2 for c in chunk) / (window - 1)
        sd = var ** 0.5
        out.append(((clean[-1] - mean) / sd) if sd > 0 else 0.0)
    return out


def signal_rule_pnl(
    opens: list[float],
    signal: list[float | None],
    *,
    cost_bps: float,
) -> dict[str, float]:
    """Position = the given sign, held until it changes. One rule for all signals.

    Deliberately identical for every indicator in the panel, so that any difference
    between rows is a property of the indicator and not of the trading rule wrapped
    around it. Costs are charged on |change in position|, so an indicator that flips
    every bar is charged for it.
    """
    n = min(len(opens), len(signal))
    out = {"net": 0.0, "gross": 0.0, "cost": 0.0, "trades": 0.0, "t": 0.0}
    position = 0.0
    gross = 0.0
    cost_total = 0.0
    trades = 0.0
    per_bar: list[float] = []
    for t in range(n - 2):
        s = signal[t]
        want = 0.0 if s is None else (1.0 if s > 0 else (-1.0 if s < 0 else 0.0))
        turnover = abs(want - position)
        trades += turnover
        entry, leave = opens[t + 1], opens[t + 2]
        bar_ret = 0.0
        if entry > 0:
            bar_ret = want * (leave - entry) / entry
            gross += bar_ret
        c = turnover * cost_bps / 10_000.0
        cost_total += c
        if want != 0.0:
            per_bar.append(bar_ret - c)
        position = want
    out["gross"] = gross
    out["cost"] = cost_total
    out["net"] = gross - cost_total
    out["trades"] = trades
    if len(per_bar) > 5:
        m = len(per_bar)
        avg = sum(per_bar) / m
        var = sum((v - avg) ** 2 for v in per_bar) / (m - 1)
        se = (var / m) ** 0.5 if var > 0 else 0.0
        out["t"] = (avg / se) if se > 0 else 0.0
    return out


def expansion_reversal_pnl(
    opens: list[float],
    closes: list[float],
    *,
    k_sigma: float,
    hold: int,
    cost_bps: float,
) -> dict[str, float]:
    """Fade a move larger than k sigma, for `hold` bars. Both directions.

    The trigger is volatility expansion and the direction is taken from the measured
    sign of the autocorrelation, so this is one rule rather than two guesses. Position
    size is flat, there is no stop and no target: a stop would add a second parameter
    to justify, and the arithmetic under test is whether the first bar of reversion is
    worth more than the fill costs.
    """
    n = min(len(opens), len(closes))
    out = {
        "net": 0.0, "gross": 0.0, "cost": 0.0, "trades": 0.0,
        "t": 0.0, "signals": 0.0, "turnover": 0.0, "bars": 0.0,
    }
    if n < 100 or hold < 1 or k_sigma <= 0:
        return out
    sigma = math.sqrt(sum(r * r for r in log_returns(closes)) / max(1, n - 1))
    if sigma <= 0:
        return out
    cut = k_sigma * sigma
    position = 0.0
    opened_at = -10 ** 6
    may_open_from = 0
    gross = 0.0
    cost_total = 0.0
    trades = 0.0
    signals = 0.0
    per_trade: list[float] = []
    trade_pnl = 0.0
    for t in range(1, n - 2):
        want = position
        if position == 0.0:
            if t >= may_open_from:
                move = closes[t] - closes[t - 1]
                if abs(move) > cut:
                    want = -1.0 if move > 0 else 1.0
                    opened_at = t
                    trade_pnl = 0.0
                    signals += 1.0
        elif t - opened_at >= hold:
            want = 0.0
        turnover = abs(want - position)
        trades += turnover
        entry, leave = opens[t + 1], opens[t + 2]
        bar_ret = 0.0
        if entry > 0:
            bar_ret = want * (leave - entry) / entry
            gross += bar_ret
        c = turnover * cost_bps / 10_000.0
        # The running total is never reset. An earlier version cleared it on every
        # open, which left `net` equal to `gross`: a rule with no signal at all
        # reported +120% because the fills it was being charged for had been
        # forgotten. The self-check on a zero-autocorrelation series is what caught it.
        cost_total += c
        if position != 0.0 or want != 0.0:
            trade_pnl += bar_ret - c
        if position != 0.0 and want == 0.0:
            per_trade.append(trade_pnl)
            trade_pnl = 0.0
            may_open_from = t + 1
        position = want
    out["gross"] = gross
    out["cost"] = cost_total
    out["net"] = gross - cost_total
    out["trades"] = trades
    out["signals"] = signals
    out["turnover"] = trades / max(1, n)
    out["bars"] = float(n)
    if len(per_trade) > 5:
        m = len(per_trade)
        avg = sum(per_trade) / m
        var = sum((v - avg) ** 2 for v in per_trade) / (m - 1)
        se = (var / m) ** 0.5 if var > 0 else 0.0
        out["t"] = (avg / se) if se > 0 else 0.0
    return out


def _fmt_ci(est: float, se: float) -> str:
    return f"[{est - 1.96 * se:+.4f}, {est + 1.96 * se:+.4f}]"


def _split(items: list[float], windows: int) -> list[list[float]]:
    """Contiguous non-overlapping slices, matching how windows are cut elsewhere."""
    if windows < 1 or not items:
        return [items]
    size = len(items) // windows
    return [items[i * size : (i + 1) * size] for i in range(windows)]


async def main() -> int:
    from host.trading_bot_core import TradingBotCore
    from tests.e2e_sandbox import _seed_sandbox_rules, build_sandbox_config

    with tempfile.TemporaryDirectory(prefix="edge_ceiling_") as raw:
        work = Path(raw).resolve()
        (work / "config").mkdir(parents=True, exist_ok=True)
        (work / "data").mkdir(parents=True, exist_ok=True)
        _seed_sandbox_rules(work / "data")
        cfg = build_sandbox_config(
            work / "config" / "trading_bot.yaml",
            data_root=work / "data",
            extra_overrides=_OVERRIDES,
        )
        os.environ["TRADING_DATA_DIR"] = str(work / "data")
        core = TradingBotCore(cfg)
        await core.boot()
        await core.start()
        hist = core.service_registry.get("historical_data_service")

        print(
            f"predictability of the market itself: {TIMEFRAME}, {BARS} bars, "
            f"pooled and cut into {WINDOWS} windows"
        )
        print(
            f"comparison axis from tools.null_calibration: the smallest injected\n"
            f"edge the method detects is phi = {DETECTION_FLOOR:.2f}\n"
        )

        for ticker in TICKERS:
            # A venue being unreachable is a fact about the run, not a reason to
            # lose the series that did come back. Exchange metadata calls fail
            # transiently, and an unhandled one turned a partial measurement into
            # no measurement at all.
            try:
                series = await hist.fetch(
                    ticker,
                    class_code="CRYPTO",
                    provider="ccxt",
                    timeframe=TIMEFRAME,
                    limit=BARS,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"\n{ticker}: fetch failed, skipped ({type(exc).__name__})")
                continue
            closes = [
                float(getattr(b, "close", 0.0) or 0.0) for b in series
            ]
            closes = [c for c in closes if c > 0.0]
            if len(closes) < 60:
                print(f"\n{ticker}: only {len(closes)} usable bars, skipped")
                continue
            returns = log_returns(closes)
            closes_all = list(series)

            print("=" * 72)
            print(f"{ticker}  {len(returns)} returns")
            print("=" * 72)

            pooled = ar1(returns)
            floor = resolvable_phi(len(returns))
            print(
                f"  pooled AR(1)  phi={pooled['phi']:+.4f}  "
                f"se={pooled['se']:.4f}  t={pooled['t']:+.2f}  "
                f"95% CI {_fmt_ci(pooled['phi'], pooled['se'])}"
            )
            print(
                f"               resolvable at this n: |phi| >= {floor:.4f}"
            )
            ci_lo = pooled["phi"] - 1.96 * pooled["se"]
            ci_hi = pooled["phi"] + 1.96 * pooled["se"]
            print(
                f"               sign: {'positive' if pooled['phi'] > 0 else 'NEGATIVE'}"
            )
            if ci_lo > DETECTION_FLOOR:
                verdict = (
                    "ABOVE the detection floor -- an edge this size is findable"
                )
            elif ci_hi < DETECTION_FLOOR:
                verdict = (
                    "BELOW the detection floor in magnitude -- no edge this "
                    "method could notice exists here"
                )
            else:
                verdict = "OVERLAPS the detection floor -- not resolvable at n"
            print(f"               {verdict}")
            if pooled["phi"] < 0 and abs(pooled["t"]) > 2.0:
                print(
                    "               NOTE: the only lag-1 dependency measured is of\n"
                    "               the WRONG SIGN for a long/flat trend lineup, which\n"
                    "               can only be long. Such a lineup is not missing an\n"
                    "               edge of the size this method detects -- it is on the\n"
                    "               adverse side of the one dependency that is there."
                )

            print()
            print(
                f"  {'lag':>5} {'variance ratio':>16} {'z':>8}   reading"
            )
            for q in VR_LAGS:
                res = variance_ratio(returns, q)
                if res["z"] > 2.0:
                    reading = "positive autocorrelation"
                elif res["z"] < -2.0:
                    reading = "NEGATIVE autocorrelation -- mean reversion at this lag"
                else:
                    reading = "consistent with a random walk"
                print(
                    f"  {q:>5} {res['vr']:>16.4f} {res['z']:>+8.2f}   {reading}"
                )

            vol = volatility_structure(returns)
            print()
            print("  VOLATILITY STRUCTURE -- what a breakout or gap strategy lives on.")
            print("  Sign of the next move is a different question from its size:")
            print(f"    corr(|r_t|, |r_t+1|)   = {vol['rho_abs']:+.4f}   "
                  "big moves follow big moves?")
            print(f"    ARCH(1) on r^2         = {vol['arch1']:+.4f}   "
                  "the formal version of the same claim")
            print(f"    vol ratio hi/lo        = {vol['vol_ratio']:.2f}x   "
                  "how much bigger is the next move after a big one")
            print(f"    corr(r_t, |r_t+1|)     = {vol['sign_to_size']:+.4f}   "
                  "does today's direction predict tomorrow's size")
            print("    (this is the breakout premise; the sign sweeps above never")
            print("     asked it, and a negative result there said nothing here)")
            print()
            print("  BREAKOUT EXPECTANCY -- mean next-bar return after a big move,")
            print("  in units of the next bar's own volatility:")
            print(f"    {'cut':>6} {'n':>6} {'long/short':>12} {'t':>7}   reading")
            for k in (1.0, 1.5, 2.0, 3.0):
                be = breakout_expectancy(returns, threshold=k)
                if be["n"] < 30:
                    print(f"    {k:>5.1f}s {int(be['n']):>6} {'-':>12} {'-':>7}"
                          "   too few events")
                    continue
                verdict = (
                    "LONGS pay"
                    if be["t"] > 2.0
                    else "shorts pay" if be["t"] < -2.0 else "neither"
                )
                print(
                    f"    {k:>5.1f}s {int(be['n']):>6} {be['mean']:>12.5f} "
                    f"{be['t']:>+7.2f}   {verdict}"
                )
            print("    A t beyond +-2 is the first thing that would make a breakout")
            print("    strategy's expectancy positive rather than merely volatile.")

            print()
            print(
                f"  {'window':>8} {'n':>7} {'phi':>9} {'t':>7} "
                f"{'resolvable':>11}"
            )
            for index, chunk in enumerate(_split(returns, WINDOWS)):
                res = ar1(chunk)
                print(
                    f"  {index + 1:>8} {int(res['n']):>7} {res['phi']:>+9.4f} "
                    f"{res['t']:>+7.2f} "
                    f"{resolvable_phi(int(res['n'])):>11.4f}"
                )
            print(
                "  A per-window phi cannot be called different from zero unless it\n"
                "  exceeds `resolvable`, which is why these are the weak numbers."
            )

            _tradability(returns, closes_all, ticker)

        if os.environ.get("EDGE_SWEEP"):
            async def _fetch(
                ticker: str, class_code: str, provider: str
            ) -> Any:
                return await hist.fetch(
                    ticker,
                    class_code=class_code,
                    provider=provider,
                    timeframe=TIMEFRAME,
                    limit=BARS,
                )

            print()
            print("=" * 72)
            print("SWEEP: other asset classes and horizons, pre-registered")
            print("=" * 72)
            print(
                f"  {len(SWEEP_GRID)} series x {len(SWEEP_HORIZONS)} horizons "
                f"= {len(SWEEP_GRID) * len(SWEEP_HORIZONS)} tests"
            )
            print(
                f"  gate 1: phi > 0 and t > {GATE1_T} (one-sided Bonferroni)\n"
                "  gate 2: the sign carries out of sample, both directions\n"
                "  gate 3: breakeven under 10 bps per fill and under 0.5 flips/bar"
            )
            rows = await sweep(_fetch, SWEEP_GRID, BARS)
            usable = [r for r in rows if "note" not in r]
            short = [
                r for r in usable if r["n"] < BARS * 0.9
            ]
            if short:
                # Same rule as `window_stability`: asked for N, got fewer, and the
                # gap is silent. A correlation computed on two thirds of the
                # intended sample is a different measurement wearing the same name.
                print()
                print(
                    f"  WARNING: asked for {BARS} bars; the series below came "
                    "back short. Their phi describes a smaller sample than the "
                    "one requested, and the difference is not in the number."
                )
                for row in short:
                    print(f"    {row['ticker']:10} {row['horizon']:>4} n={row['n']}")
            print()
            print(
                f"  {'series':10} {'hz':>4} {'n':>6} {'phi':>9} {'t':>6} "
                f"{'g1':>3} {'g2':>3} {'g3':>3}  {'breakeven':>10}  why"
            )
            for row in rows:
                if "note" in row:
                    print(
                        f"  {row['ticker']:10} {row['horizon']:>4} "
                        f"{'-':>6} {'-':>9} {'-':>6} {'-':>3} {'-':>3} {'-':>3}"
                        f"  {'-':>10}  {row['note']}"
                    )
                    continue
                flags = "   ".join(
                    [
                        "Y" if row["gate1"] else ".",
                        "Y" if row["gate2"] else ".",
                        "Y" if row["gate3"] else ".",
                    ]
                )
                print(
                    f"  {row['ticker']:10} {row['horizon']:>4} {row['n']:>6} "
                    f"{row['phi']:>+9.4f} {row['t']:>+6.2f} {flags}  "
                    f"{row['breakeven_bps']:>9.1f}b  {row['why']}"
                )
            winners = [
                r for r in usable if r["gate1"] and r["gate2"] and r["gate3"]
            ]
            print()
            print(f"  tests run: {len(usable)}   passing all three gates: "
                  f"{len(winners)}")
            for row in winners:
                print(
                    f"    CANDIDATE: {row['ticker']} {row['horizon']} "
                    f"phi={row['phi']:+.4f} t={row['t']:+.2f} "
                    f"breakeven={row['breakeven_bps']:.1f}bps"
                )
            if not winners:
                print(
                    "    None. The grid was registered in advance, so this is the\n"
                    "    answer rather than a reason to widen the search."
                )

        if os.environ.get("EDGE_MOMENTUM"):
            async def _fetch_m(
                ticker: str, class_code: str, provider: str
            ) -> Any:
                return await hist.fetch(
                    ticker,
                    class_code=class_code,
                    provider=provider,
                    timeframe=TIMEFRAME,
                    limit=MOMENTUM_BARS,
                )

            print()
            print("=" * 72)
            print("MOMENTUM: registered horizons, pre-registered")
            print("=" * 72)
            print(
                f"  {len(SWEEP_GRID)} series x {len(MOMENTUM_HORIZONS)} horizons "
                f"= {len(SWEEP_GRID) * len(MOMENTUM_HORIZONS)} tests"
            )
            print(
                f"  gate 1: phi > 0 and t > {MOMENTUM_GATE1_T}\n"
                "  gate 2: sign carries out of sample, both directions\n"
                "  gate 3: the rule still pays after 10 bps per fill"
            )
            mrows = await momentum_sweep(_fetch_m, SWEEP_GRID, MOMENTUM_BARS)
            usable_m = [r for r in mrows if "note" not in r]
            print()
            print(
                f"  {'series':10} {'h':>5} {'n':>6} {'phi':>9} {'t':>6} "
                f"{'g1':>3} {'g2':>3} {'g3':>3}  {'net@10bp':>10} {'turn/bar':>9}"
            )
            for row in mrows:
                if "note" in row:
                    print(
                        f"  {row['ticker']:10} {str(row['h']):>5} {'-':>6} "
                        f"{'-':>9} {'-':>6} {'-':>3} {'-':>3} {'-':>3} "
                        f"{'-':>10} {row['note']}"
                    )
                    continue
                flags = "   ".join(
                    [
                        "Y" if row["gate1"] else ".",
                        "Y" if row["gate2"] else ".",
                        "Y" if row["gate3"] else ".",
                    ]
                )
                print(
                    f"  {row['ticker']:10} {row['h']:>5} {row['n']:>6} "
                    f"{row['phi']:>+9.4f} {row['t']:>+6.2f} {flags}  "
                    f"{100 * row['net_at_10bp']:>9.1f}% "
                    f"{row['turnover_per_bar']:>9.2f}"
                )
            winners_m = [
                r for r in usable_m if r["gate1"] and r["gate2"] and r["gate3"]
            ]
            print()
            print(f"  tests run: {len(usable_m)}   passing all three gates: "
                  f"{len(winners_m)}")
            for row in winners_m:
                print(
                    f"    CANDIDATE: {row['ticker']} h={row['h']} "
                    f"phi={row['phi']:+.4f} t={row['t']:+.2f} "
                    f"net={100 * row['net_at_10bp']:.1f}%"
                )
            if not winners_m:
                print("    None.")

        if os.environ.get("EDGE_BREAKOUT"):
            async def _fetch_b(
                ticker: str, timeframe: str, bars: int
            ) -> Any:
                return await hist.fetch(
                    ticker,
                    class_code="CRYPTO",
                    provider="ccxt",
                    timeframe=timeframe,
                    limit=bars,
                )

            print()
            print("=" * 72)
            print("RANGE BREAKOUT: registered, untuned, both directions")
            print("=" * 72)
            print(
                f"  lookback={BREAKOUT_LOOKBACK}  hold={BREAKOUT_HOLD}  "
                f"cost=10 bps per fill"
            )
            print(
                f"  gate 1: net > 0 and t > {BREAKOUT_GATE1_T} "
                f"(Bonferroni, {len(BREAKOUT_TIMEFRAMES)} tests)\n"
                "  gate 2: both halves of the series net positive"
            )
            print()
            print(
                f"  {'series':10} {'tz':>4} {'n':>7} {'signals':>8} "
                f"{'gross':>9} {'net@10bp':>10} {'t':>7} {'g1':>3} {'g2':>3}"
            )
            winners_b: list[dict[str, Any]] = []
            tested_b = 0
            for ticker in ("BTC/USDT", "ETH/USDT"):
                for tf in BREAKOUT_TIMEFRAMES:
                    want = BARS if tf == "1d" else MOMENTUM_BARS
                    try:
                        series = await _fetch_b(ticker, tf, want)
                    except Exception as exc:  # noqa: BLE001
                        print(f"  {ticker:10} {tf:>4} fetch failed "
                              f"({type(exc).__name__})")
                        continue
                    if not series or len(series) < 200:
                        print(f"  {ticker:10} {tf:>4} only "
                              f"{len(series or [])} bars")
                        continue
                    highs = [float(getattr(b, 'high', 0) or 0) for b in series]
                    lows = [float(getattr(b, 'low', 0) or 0) for b in series]
                    opens = [float(getattr(b, 'open', 0) or 0) for b in series]
                    closes = [float(getattr(b, 'close', 0) or 0) for b in series]
                    res = breakout_rule_pnl(
                        highs, lows, opens, closes,
                        lookback=BREAKOUT_LOOKBACK,
                        hold=BREAKOUT_HOLD,
                        cost_bps=10.0,
                    )
                    half = len(closes) // 2
                    a = breakout_rule_pnl(
                        highs[:half], lows[:half], opens[:half], closes[:half],
                        lookback=BREAKOUT_LOOKBACK, hold=BREAKOUT_HOLD,
                        cost_bps=10.0,
                    )
                    b_half = breakout_rule_pnl(
                        highs[half:], lows[half:], opens[half:], closes[half:],
                        lookback=BREAKOUT_LOOKBACK, hold=BREAKOUT_HOLD,
                        cost_bps=10.0,
                    )
                    tested_b += 1
                    g1 = res["net"] > 0 and res["t"] > BREAKOUT_GATE1_T
                    g2 = a["net"] > 0 and b_half["net"] > 0
                    flags = "   ".join(["Y" if g1 else ".", "Y" if g2 else "."])
                    print(
                        f"  {ticker:10} {tf:>4} {len(closes):>7} "
                        f"{int(res['signals']):>8} {100 * res['gross']:>8.1f}% "
                        f"{100 * res['net']:>9.1f}% {res['t']:>+7.2f} {flags}"
                    )
                    if g1 and g2:
                        winners_b.append(
                            {"ticker": ticker, "tf": tf, "r": res}
                        )
            print()
            print(f"  tests run: {tested_b}   passing both gates: "
                  f"{len(winners_b)}")
            for w in winners_b:
                print(
                    f"    CANDIDATE: {w['ticker']} {w['tf']} "
                    f"net={100 * w['r']['net']:.1f}% t={w['r']['t']:+.2f}"
                )
            if not winners_b:
                print("    None.")

        if os.environ.get("EDGE_EWO"):
            async def _fetch_e(
                ticker: str, timeframe: str, bars: int
            ) -> Any:
                return await hist.fetch(
                    ticker,
                    class_code="CRYPTO",
                    provider="ccxt",
                    timeframe=timeframe,
                    limit=bars,
                )

            print()
            print("=" * 72)
            print("EWO / MA(5) vs MA(34): registered from an explainer")
            print("=" * 72)
            print(
                f"  fast={EWO_FAST} slow={EWO_SLOW} cost=10 bps per fill, "
                "both directions"
            )
            print(
                f"  gate 1: net > 0 and t > {EWO_GATE1_T}; gate 2: both halves "
                "net positive"
            )
            print()
            print(
                f"  {'series':10} {'tz':>4} {'n':>7} {'fills':>7} {'gross':>9} "
                f"{'net@10bp':>10} {'t':>7} {'g1':>3} {'g2':>3}"
            )
            winners_e: list[dict[str, Any]] = []
            tested_e = 0
            for ticker in ("BTC/USDT", "ETH/USDT"):
                for tf in EWO_TIMEFRAMES:
                    want = BARS if tf == "1d" else MOMENTUM_BARS
                    try:
                        series = await _fetch_e(ticker, tf, want)
                    except Exception as exc:  # noqa: BLE001
                        print(f"  {ticker:10} {tf:>4} fetch failed "
                              f"({type(exc).__name__})")
                        continue
                    if not series or len(series) < 200:
                        print(f"  {ticker:10} {tf:>4} only "
                              f"{len(series or [])} bars")
                        continue
                    opens = [float(getattr(b, 'open', 0) or 0) for b in series]
                    closes = [float(getattr(b, 'close', 0) or 0) for b in series]
                    res = ewo_rule_pnl(
                        opens, closes, fast=EWO_FAST, slow=EWO_SLOW,
                        cost_bps=10.0,
                    )
                    half = len(closes) // 2
                    a = ewo_rule_pnl(
                        opens[:half], closes[:half], fast=EWO_FAST,
                        slow=EWO_SLOW, cost_bps=10.0,
                    )
                    b_half = ewo_rule_pnl(
                        opens[half:], closes[half:], fast=EWO_FAST,
                        slow=EWO_SLOW, cost_bps=10.0,
                    )
                    tested_e += 1
                    g1 = res["net"] > 0 and res["t"] > EWO_GATE1_T
                    g2 = a["net"] > 0 and b_half["net"] > 0
                    flags = "   ".join(["Y" if g1 else ".", "Y" if g2 else "."])
                    print(
                        f"  {ticker:10} {tf:>4} {len(closes):>7} "
                        f"{res['trades']:>7.0f} {100 * res['gross']:>8.1f}% "
                        f"{100 * res['net']:>9.1f}% {res['t']:>+7.2f} {flags}"
                    )
                    if g1 and g2:
                        winners_e.append({"ticker": ticker, "tf": tf, "r": res})
            print()
            print(f"  tests run: {tested_e}   passing both gates: "
                  f"{len(winners_e)}")
            for w in winners_e:
                print(
                    f"    CANDIDATE: {w['ticker']} {w['tf']} "
                    f"net={100 * w['r']['net']:.1f}% t={w['r']['t']:+.2f}"
                )
            if not winners_e:
                print("    None.")

        if os.environ.get("EDGE_MECHANISM"):
            async def _fetch_m2(
                ticker: str, class_code: str, provider: str, tf: str, bars: int
            ) -> Any:
                return await hist.fetch(
                    ticker, class_code=class_code, provider=provider,
                    timeframe=tf, limit=bars,
                )

            print()
            print("=" * 72)
            print("MECHANISM: does the rule's P&L follow the measured phi?")
            print("=" * 72)
            print(f"  fast={EWO_FAST} slow={EWO_SLOW}, 10 bps per fill")
            print("  registered predictions:")
            print("    1  phi t < -2.0  =>  rule net > 0")
            print("    2  phi t > -1.0  =>  rule net <= 0")
            print("    3  Spearman(phi, rule net) > 0")
            print("    4  rule net > 0 on some series where buy-and-hold is not")
            print()
            print(
                f"  {'series':10} {'tz':>4} {'n':>7} {'phi':>9} {'t_phi':>7} "
                f"{'rule net':>10} {'t_rule':>7} {'buy&hold':>10}"
            )
            obs_phi: list[float] = []
            obs_net: list[float] = []
            p1_ok = p2_ok = True
            p4_ok = False
            n_obs = 0
            for ticker, class_code, provider in MECHANISM_SERIES:
                for tf in ("1d",):
                    want = BARS if tf == "1d" else MOMENTUM_BARS
                    try:
                        series = await _fetch_m2(
                            ticker, class_code, provider, tf, want
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(f"  {ticker:10} {tf:>4} fetch failed "
                              f"({type(exc).__name__})")
                        continue
                    if not series or len(series) < 200:
                        print(f"  {ticker:10} {tf:>4} only "
                              f"{len(series or [])} bars")
                        continue
                    opens = [float(getattr(b, 'open', 0) or 0) for b in series]
                    closes = [float(getattr(b, 'close', 0) or 0) for b in series]
                    rets = log_returns(closes)
                    est = ar1(rets)
                    res = ewo_rule_pnl(
                        opens, closes, fast=EWO_FAST, slow=EWO_SLOW,
                        cost_bps=10.0,
                    )
                    bh = (closes[-1] / closes[0] - 1.0) if closes[0] > 0 else 0.0
                    n_obs += 1
                    obs_phi.append(est["phi"])
                    obs_net.append(res["net"])
                    if est["t"] < -2.0 and res["net"] <= 0:
                        p1_ok = False
                    if est["t"] > -1.0 and res["net"] > 0:
                        p2_ok = False
                    if res["net"] > 0 and bh <= 0:
                        p4_ok = True
                    print(
                        f"  {ticker:10} {tf:>4} {len(rets):>7} "
                        f"{est['phi']:>+9.4f} {est['t']:>+7.2f} "
                        f"{100 * res['net']:>9.1f}% {res['t']:>+7.2f} "
                        f"{100 * bh:>9.1f}%"
                    )
            rho = spearman(obs_phi, obs_net)
            p3_ok = rho > 0
            print()
            print(f"  observations: {n_obs}")
            print(f"  Spearman(phi, rule net) = {rho:+.3f}   "
                  f"prediction 3 {'HOLDS' if p3_ok else 'FAILS'}")
            print(f"  prediction 1 (significant negative phi pays)   "
                  f"{'HOLDS' if p1_ok else 'FAILS'}")
            print(f"  prediction 2 (non-negative phi does not pay)   "
                  f"{'HOLDS' if p2_ok else 'FAILS'}")
            print(f"  prediction 4 (pays where market fell)          "
                  f"{'HOLDS' if p4_ok else 'FAILS'}")
            print()
            if p1_ok and p2_ok and p3_ok and p4_ok:
                print("  MECHANISM SUPPORTED. The rule's gain tracks the measured")
                print("  negative autocorrelation and is not explained by drift.")
            else:
                print("  MECHANISM NOT SUPPORTED. The daily result is not explained by")
                print("  the measured phi, and is something else.")

        if os.environ.get("EDGE_DOWN"):
            async def _fetch_d(
                ticker: str, class_code: str, provider: str,
                start: str, end: str, bars: int
            ) -> Any:
                return await hist.fetch(
                    ticker, class_code=class_code, provider=provider,
                    timeframe="1d", limit=bars, start=start, end=end,
                )

            print()
            print("=" * 72)
            print("PREDICTION 4: the rule must pay in named down markets")
            print("=" * 72)
            print(f"  criterion: net positive at 10 bps in at least "
                  f"{DOWN_MIN_WINS} of {len(DOWN_PERIODS)} named periods")
            print()
            print(
                f"  {'series':10} {'period':>24} {'n':>6} {'rule net':>10} "
                f"{'t':>7} {'buy&hold':>10}"
            )
            wins_d = 0
            for ticker, class_code, provider, span in DOWN_PERIODS:
                start, end = span.split("/")
                want = BARS if provider == "ccxt" else MOMENTUM_BARS
                try:
                    series = await _fetch_d(
                        ticker, class_code, provider, start, end, want
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"  {ticker:10} {span:>24} fetch failed "
                          f"({type(exc).__name__})")
                    continue
                if not series or len(series) < 200:
                    print(f"  {ticker:10} {span:>24} only "
                          f"{len(series or [])} bars, skipped")
                    continue
                opens = [float(getattr(b, 'open', 0) or 0) for b in series]
                closes = [float(getattr(b, 'close', 0) or 0) for b in series]
                res = ewo_rule_pnl(
                    opens, closes, fast=EWO_FAST, slow=EWO_SLOW, cost_bps=10.0
                )
                bh = (closes[-1] / closes[0] - 1.0) if closes[0] > 0 else 0.0
                if res["net"] > 0:
                    wins_d += 1
                print(
                    f"  {ticker:10} {span:>24} {len(closes):>6} "
                    f"{100 * res['net']:>9.1f}% {res['t']:>+7.2f} "
                    f"{100 * bh:>9.1f}%"
                )
            print()
            print(f"  periods won: {wins_d} of {len(DOWN_PERIODS)}  "
                  f"criterion: {'HOLDS' if wins_d >= DOWN_MIN_WINS else 'FAILS'}")
            if wins_d >= DOWN_MIN_WINS:
                print("  Money made while the market falls is not drift. The")
                print("  reversal claim is worth a proper registered test.")
            else:
                print("  The rule does not survive a falling market. The +334% and")
                print("  +406% are directional exposure to two rising assets, and")
                print("  should not be counted as a finding.")

        if os.environ.get("EDGE_FRESH"):
            async def _fetch_f(
                ticker: str, class_code: str, provider: str,
                bars: int, start: str | None = None, end: str | None = None
            ) -> Any:
                return await hist.fetch(
                    ticker, class_code=class_code, provider=provider,
                    timeframe="1d", limit=bars, start=start, end=end,
                )

            def _run(series: Any) -> tuple[dict[str, float], float]:
                opens = [float(getattr(b, 'open', 0) or 0) for b in series]
                closes = [float(getattr(b, 'close', 0) or 0) for b in series]
                res = ewo_rule_pnl(
                    opens, closes, fast=EWO_FAST, slow=EWO_SLOW, cost_bps=10.0
                )
                bh = (closes[-1] / closes[0] - 1.0) if closes[0] > 0 else 0.0
                return res, bh

            print()
            print("=" * 72)
            print("CONFIRMATION: instruments and periods that formed no impression")
            print("=" * 72)
            print(
                f"  A: net > 0 on >= {FRESH_MIN_INSTRUMENTS} of "
                f"{len(FRESH_INSTRUMENTS)} instruments, t > {FRESH_MIN_T} on >= 1"
            )
            print(
                f"  B: net > 0 on >= {FRESH_MIN_BEARS} of "
                f"{len(FRESH_BEARS)} unused bear periods (buy&hold negative)"
            )
            print()
            print("  -- fresh instruments --")
            print(
                f"  {'series':10} {'n':>7} {'rule net':>10} {'t':>7} "
                f"{'buy&hold':>10}"
            )
            a_ok = 0
            a_sig = 0
            for ticker, class_code, provider in FRESH_INSTRUMENTS:
                want = BARS if provider == "ccxt" else MOMENTUM_BARS
                try:
                    series = await _fetch_f(ticker, class_code, provider, want)
                except Exception as exc:  # noqa: BLE001
                    print(f"  {ticker:10} fetch failed ({type(exc).__name__})")
                    continue
                if not series or len(series) < 200:
                    print(f"  {ticker:10} only {len(series or [])} bars, skipped")
                    continue
                res, bh = _run(series)
                if res["net"] > 0:
                    a_ok += 1
                if res["net"] > 0 and res["t"] > FRESH_MIN_T:
                    a_sig += 1
                print(
                    f"  {ticker:10} {len(series):>7} {100 * res['net']:>9.1f}% "
                    f"{res['t']:>+7.2f} {100 * bh:>9.1f}%"
                )
            gate_a = a_ok >= FRESH_MIN_INSTRUMENTS and a_sig >= 1

            print()
            print("  -- fresh bear periods --")
            print(
                f"  {'period':>24} {'n':>6} {'rule net':>10} {'t':>7} "
                f"{'buy&hold':>10}"
            )
            b_ok = 0
            for ticker, class_code, provider, span in FRESH_BEARS:
                start, end = span.split("/")
                try:
                    series = await _fetch_f(
                        ticker, class_code, provider, MOMENTUM_BARS, start, end
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"  {span:>24} fetch failed ({type(exc).__name__})")
                    continue
                if not series or len(series) < 200:
                    print(f"  {span:>24} only {len(series or [])} bars, skipped")
                    continue
                res, bh = _run(series)
                if res["net"] > 0 and bh <= 0:
                    b_ok += 1
                print(
                    f"  {span:>24} {len(series):>6} "
                    f"{100 * res['net']:>9.1f}% {res['t']:>+7.2f} "
                    f"{100 * bh:>9.1f}%"
                )
            gate_b = b_ok >= FRESH_MIN_BEARS

            print()
            print(f"  A: {a_ok}/{len(FRESH_INSTRUMENTS)} positive, "
                  f"{a_sig} significant   {'HOLDS' if gate_a else 'FAILS'}")
            print(f"  B: {b_ok}/{len(FRESH_BEARS)} bear periods   "
                  f"{'HOLDS' if gate_b else 'FAILS'}")
            print()
            if gate_a and gate_b:
                print("  CONFIRMED on data that formed no part of the impression.")
            else:
                print("  NOT CONFIRMED. The daily result came from two known assets whose")
                print("  drift was already known, and it does not transfer to")
                print("  instruments or periods that were never part of it.")

        if os.environ.get("EDGE_PANEL"):
            async def _fetch_p(
                ticker: str, class_code: str, provider: str, bars: int
            ) -> Any:
                return await hist.fetch(
                    ticker, class_code=class_code, provider=provider,
                    timeframe="1d", limit=bars,
                )

            print()
            print("=" * 72)
            print("PANEL: every indicator in the library, both signs")
            print("=" * 72)
            print(
                "  z-score window 60, 10 bps per fill, position = sign of score"
            )
            print(
                f"  bar: t > {PANEL_GATE_T} (Bonferroni, {PANEL_TESTS} tests "
                f"= {PANEL_OSCILLATORS} x {PANEL_SIGNS} x {PANEL_INSTRUMENTS})"
            )
            panel_rows: list[dict[str, Any]] = []
            names_seen: list[str] = []
            for ticker, class_code, provider in (
                ("BTC/USDT", "CRYPTO", "ccxt"),
                ("ETH/USDT", "CRYPTO", "ccxt"),
            ):
                try:
                    series = await _fetch_p(
                        ticker, class_code, provider, BARS
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"  {ticker} fetch failed ({type(exc).__name__})")
                    continue
                if not series or len(series) < 400:
                    print(f"  {ticker}: {len(series or [])} bars, skipped")
                    continue
                highs = [float(getattr(b, 'high', 0) or 0) for b in series]
                lows = [float(getattr(b, 'low', 0) or 0) for b in series]
                opens = [float(getattr(b, 'open', 0) or 0) for b in series]
                closes = [float(getattr(b, 'close', 0) or 0) for b in series]
                signals = panel_signals(highs, lows, closes)
                for name, raw_signal in signals.items():
                    if name not in names_seen:
                        names_seen.append(name)
                    scored = rolling_zscore(raw_signal, 60)
                    for sign in (1.0, -1.0):
                        flipped = [
                            None if s is None else s * sign for s in scored
                        ]
                        res = signal_rule_pnl(opens, flipped, cost_bps=10.0)
                        panel_rows.append(
                            {
                                "ticker": ticker,
                                "indicator": name,
                                "sign": sign,
                                "net": res["net"],
                                "t": res["t"],
                                "trades": res["trades"],
                            }
                        )
            print()
            print(
                f"  {'indicator':14} {'sign':>5} "
                + "  ".join(
                    f"{tk:>22}" for tk in ("BTC/USDT", "ETH/USDT")
                )
            )
            for name in names_seen:
                cells = []
                for ticker in ("BTC/USDT", "ETH/USDT"):
                    picks = [
                        r for r in panel_rows
                        if r["indicator"] == name and r["ticker"] == ticker
                    ]
                    if not picks:
                        cells.append(f"{'-':>22}")
                        continue
                    line = "  ".join(
                        f"{100 * r['net']:>+9.1f}% t{r['t']:>+6.2f}"
                        for r in sorted(picks, key=lambda r: -r["sign"])
                    )
                    cells.append(f"{line:>22}")
                print(f"  {name:14} {'both':>5} " + "  ".join(cells))
            positives = [r for r in panel_rows if r["net"] > 0]
            clear = [r for r in panel_rows if r["net"] > 0 and r["t"] > PANEL_GATE_T]
            print()
            print(f"  rows run           : {len(panel_rows)} "
                  f"of {PANEL_TESTS} registered")
            print(f"  net positive       : {len(positives)}")
            print(f"  cleared the bar    : {len(clear)}")
            for r in sorted(clear, key=lambda r: -r["t"])[:10]:
                print(f"    {r['ticker']:10} {r['indicator']:14} "
                      f"sign {r['sign']:+.0f}  net {100 * r['net']:+.1f}%  "
                      f"t {r['t']:+.2f}")
            print()
            print(f"  Expected to look positive by chance at this bar: "
                  f"about {len(panel_rows) * 0.0005:.2f}")
            print("  Expected to clear by chance: under one, since the bar is a")
            print("  Bonferroni correction for the whole panel.")
            if not clear:
                print()
                print("  PANEL RESULT: nothing clears. Technical indicators in this")
                print("  library carry no measurable signal on daily crypto, in either")
                print("  direction, at library-default parameters.")

        if os.environ.get("EDGE_EXPANSION"):
            async def _fetch_x(
                ticker: str, class_code: str, provider: str,
                timeframe: str, bars: int
            ) -> Any:
                return await hist.fetch(
                    ticker, class_code=class_code, provider=provider,
                    timeframe=timeframe, limit=bars,
                )

            print()
            print("=" * 72)
            print("EXPANSION REVERSAL: fade a 2-sigma move")
            print("=" * 72)
            print("  primitives first, because they decide whether any rule can pay:")
            print()
            print(
                f"  {'series':10} {'tz':>4} {'n':>7} {'phi':>9} {'t':>7} "
                f"{'cond mean':>11} {'t':>7} {'breakeven':>11}"
            )
            for ticker, class_code, provider in (
                ("BTC/USDT", "CRYPTO", "ccxt"),
                ("ETH/USDT", "CRYPTO", "ccxt"),
                ("^GSPC", "EQUITY", "yahoo"),
            ):
                for tf in EXPANSION_TIMEFRAMES:
                    want = MOMENTUM_BARS if provider == "ccxt" else MOMENTUM_BARS
                    try:
                        series = await _fetch_x(
                            ticker, class_code, provider, tf, want
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(f"  {ticker:10} {tf:>4} fetch failed "
                              f"({type(exc).__name__})")
                        continue
                    if not series or len(series) < 500:
                        print(f"  {ticker:10} {tf:>4} only "
                              f"{len(series or [])} bars, skipped")
                        continue
                    closes = [float(getattr(b, 'close', 0) or 0) for b in series]
                    rets = log_returns(closes)
                    est = ar1(rets)
                    sigma = math.sqrt(
                        sum(r * r for r in rets) / max(1, len(rets))
                    )
                    cond = breakout_expectancy(
                        rets, threshold=EXPANSION_K_SIGMA, sigma=sigma
                    )
                    be = breakeven_cost_bps(
                        phi=est["phi"], sigma=sigma, flip_frequency=0.5
                    )
                    print(
                        f"  {ticker:10} {tf:>4} {len(rets):>7} "
                        f"{est['phi']:>+9.4f} {est['t']:>+7.2f} "
                        f"{cond['mean']:>+11.5f} {cond['t']:>+7.2f} "
                        f"{be:>9.1f}b"
                    )

            print()
            print("  the rule, 10 bps per fill:")
            print(
                f"  {'series':10} {'tz':>4} {'hold':>5} {'signals':>8} "
                f"{'turn/bar':>9} {'gross':>9} {'net':>9} {'t':>7} {'g1':>3}"
            )
            exp_tested = 0
            exp_clear = 0
            for ticker, class_code, provider in (
                ("BTC/USDT", "CRYPTO", "ccxt"),
                ("ETH/USDT", "CRYPTO", "ccxt"),
            ):
                for tf in EXPANSION_TIMEFRAMES:
                    try:
                        series = await _fetch_x(
                            ticker, class_code, provider, tf, MOMENTUM_BARS
                        )
                    except Exception:  # noqa: BLE001
                        continue
                    if not series or len(series) < 500:
                        continue
                    opens = [float(getattr(b, 'open', 0) or 0) for b in series]
                    closes = [float(getattr(b, 'close', 0) or 0) for b in series]
                    for hold in EXPANSION_HOLDS:
                        res = expansion_reversal_pnl(
                            opens, closes,
                            k_sigma=EXPANSION_K_SIGMA, hold=hold, cost_bps=10.0,
                        )
                        exp_tested += 1
                        ok = res["net"] > 0 and res["t"] > EXPANSION_GATE_T
                        if ok:
                            exp_clear += 1
                        print(
                            f"  {ticker:10} {tf:>4} {hold:>5} "
                            f"{int(res['signals']):>8} "
                            f"{res['turnover']:>9.3f} "
                            f"{100 * res['gross']:>8.1f}% "
                            f"{100 * res['net']:>8.1f}% {res['t']:>+7.2f} "
                            f"{'Y' if ok else '.'}"
                        )
            print()
            print(f"  tests run: {exp_tested}   clearing the bar: {exp_clear}")

        if os.environ.get("EDGE_BOUNCE"):
            async def _fetch_b2(
                ticker: str, class_code: str, provider: str,
                timeframe: str, bars: int
            ) -> Any:
                return await hist.fetch(
                    ticker, class_code=class_code, provider=provider,
                    timeframe=timeframe, limit=bars,
                )

            print()
            print("=" * 72)
            print("(1) BREAKEVEN CURVE -- what the effect costs to have")
            print("=" * 72)
            print(
                "  This is arithmetic, not a search. It says where net crosses"
            )
            print("  zero, which is the only number here that generalises.")
            print()
            print(
                f"  {'series':10} {'tz':>4} {'hold':>5} "
                + "  ".join(
                    f"{int(c):>8}bp" for c in (0, 2, 5, 7.5, 10, 15)
                )
            )
            grid: dict[tuple[str, str, int], list[float]] = {}
            series_map: dict[tuple[str, str], Any] = {}
            for ticker in ("BTC/USDT", "ETH/USDT"):
                for tf in ("4h",):
                    try:
                        series = await _fetch_b2(
                            ticker, "CRYPTO", "ccxt", tf, MOMENTUM_BARS
                        )
                    except Exception:  # noqa: BLE001
                        continue
                    if not series or len(series) < 500:
                        continue
                    series_map[(ticker, tf)] = series
                    opens = [float(getattr(b, 'open', 0) or 0) for b in series]
                    closes = [float(getattr(b, 'close', 0) or 0) for b in series]
                    for hold in (2, 10):
                        row = []
                        for cost in (0.0, 2.0, 5.0, 7.5, 10.0, 15.0):
                            res = expansion_reversal_pnl(
                                opens, closes, k_sigma=EXPANSION_K_SIGMA,
                                hold=hold, cost_bps=cost,
                            )
                            row.append(100 * res["net"])
                        grid[(ticker, tf, hold)] = row
                        print(
                            f"  {ticker:10} {tf:>4} {hold:>5} "
                            + "  ".join(f"{v:>9.1f}%" for v in row)
                        )
            print()
            print("  net turns positive below roughly 7 bps per fill on the 2-bar")
            print("  hold. The market charges 4 to 10. That is the whole margin.")
            print()

            print("=" * 72)
            print("(3) IS IT THE BID-ASK BOUNCE?")
            print("=" * 72)
            print(
                f"  registered criterion: the conditional mean after a 2 sigma"
                f" move,\n  as a fraction of daily sigma, must be at least"
                f" {BOUNCE_RATIO_MIN:.2f}x larger on the\n  cheapest asset than on"
                " BTC. Bounce predicts it does."
            )
            print()
            print(
                f"  {'series':10} {'bars':>7} {'phi':>9} {'t':>7} "
                f"{'cond mean':>11} {'t':>7} {'|mean|/sigma':>13}"
            )
            bounce: dict[str, tuple[float, float]] = {}
            for ticker in (
                "DOGE/USDT", "XRP/USDT", "SOL/USDT", "ETH/USDT", "BTC/USDT",
            ):
                try:
                    series = await _fetch_b2(
                        ticker, "CRYPTO", "ccxt", "4h", MOMENTUM_BARS
                    )
                except Exception:  # noqa: BLE001
                    print(f"  {ticker:10} fetch failed")
                    continue
                if not series or len(series) < 500:
                    print(f"  {ticker:10} only {len(series or [])} bars")
                    continue
                closes = [float(getattr(b, 'close', 0) or 0) for b in series]
                rets = log_returns(closes)
                est = ar1(rets)
                sigma = math.sqrt(
                    sum(r * r for r in rets) / max(1, len(rets))
                )
                cond = breakout_expectancy(
                    rets, threshold=EXPANSION_K_SIGMA, sigma=sigma
                )
                ratio = (
                    abs(cond["mean"]) / sigma if sigma > 0 else 0.0
                )
                bounce[ticker] = (ratio, abs(est["phi"]))
                print(
                    f"  {ticker:10} {len(rets):>7} {est['phi']:>+9.4f} "
                    f"{est['t']:>+7.2f} {cond['mean']:>+11.5f} "
                    f"{cond['t']:>+7.2f} {ratio:>13.5f}"
                )
            print()
            cheapest = min(
                (t for t in ("DOGE/USDT", "XRP/USDT", "SOL/USDT")
                 if t in bounce),
                key=lambda t: {"DOGE/USDT": 1, "XRP/USDT": 2, "SOL/USDT": 3}[t],
                default=None,
            )
            if cheapest and "BTC/USDT" in bounce:
                ratio = bounce[cheapest][0] / bounce["BTC/USDT"][0] if bounce["BTC/USDT"][0] else 0.0
                held = ratio >= BOUNCE_RATIO_MIN
                print(
                    f"  {cheapest} vs BTC: {bounce[cheapest][0]:.5f} / "
                    f"{bounce['BTC/USDT'][0]:.5f} = {ratio:.2f}x   "
                    f"{'HOLDS' if held else 'FAILS'}"
                )
                if held:
                    print("  BOUNCE CONFIRMED as at least a major component. The")
                    print("  conditional reversion tracks the spread, which means")
                    print("  the 15 bps is the bid-ask spread crossing and is not")
                    print("  harvestable by any strategy.")
                else:
                    print("  Bounce does not explain the whole effect. The edge is")
                    print("  worth one more look -- registered, not improvised.")
            print()

            print("=" * 72)
            print("(2) SEVERITY -- do fewer, larger reversions pay better?")
            print("=" * 72)
            print(
                f"  bar t > {SEVERITY_GATE_T} for "
                f"{len(SEVERITY_K) * len(SEVERITY_HOLDS) * 2} tests"
            )
            print()
            print(
                f"  {'series':10} {'k':>5} {'hold':>5} {'signals':>8} "
                f"{'gross':>9} {'net@10bp':>10} {'t':>7} {'g1':>3}"
            )
            sev_clear = 0
            sev_tested = 0
            for ticker in ("BTC/USDT", "ETH/USDT"):
                series = series_map.get((ticker, "4h"))
                if series is None:
                    continue
                opens = [float(getattr(b, 'open', 0) or 0) for b in series]
                closes = [float(getattr(b, 'close', 0) or 0) for b in series]
                for k in SEVERITY_K:
                    for hold in SEVERITY_HOLDS:
                        res = expansion_reversal_pnl(
                            opens, closes, k_sigma=k, hold=hold, cost_bps=10.0
                        )
                        sev_tested += 1
                        ok = res["net"] > 0 and res["t"] > SEVERITY_GATE_T
                        if ok:
                            sev_clear += 1
                        print(
                            f"  {ticker:10} {k:>5.1f} {hold:>5} "
                            f"{int(res['signals']):>8} "
                            f"{100 * res['gross']:>8.1f}% "
                            f"{100 * res['net']:>9.1f}% {res['t']:>+7.2f} "
                            f"{'Y' if ok else '.'}"
                        )
            print()
            print(f"  tests run: {sev_tested}   clearing the bar: {sev_clear}")

        await core.shutdown()
    return 0


def _tradability(
    returns: list[float], bars: list[Any], ticker: str
) -> None:
    """Is the measured dependency something a position could hold?"""
    sigma = math.sqrt(sum(r * r for r in returns) / len(returns))
    pooled = ar1(returns)
    phi = pooled["phi"]

    print()
    print("-" * 72)
    print("Is it tradable, or is it microstructure?")
    print("-" * 72)

    print(
        "  significance against Newey-West lag -- a real dependence settles, a\n"
        "  microstructure artefact only reaches |t|>2 at short lags:"
    )
    print(f"  {'lags':>6} {'phi':>9} {'se':>8} {'t':>7}")
    for row in lag_sensitivity(returns, (1, 2, 3, 5, 8, 12, 20, 30)):
        print(
            f"  {int(row['lags']):>6} {row['phi']:>+9.4f} {row['se']:>8.4f} "
            f"{row['t']:>+7.2f}"
        )

    print()
    print(f"  daily sigma: {sigma:.4f}  ({100 * sigma:.2f}% per bar)")
    be = breakeven_cost_bps(phi=phi, sigma=sigma)
    print(f"  breakeven cost for the perfect one-bar rule: {be:8.1f} bps per fill")
    print(
        "  below this the rule pays for itself, above it the costs take it. A\n"
        "  microstructure artefact implies a breakeven far below any real\n"
        "  spread, which is the tell."
    )

    opens = [float(getattr(b, "open", 0.0) or 0.0) for b in bars]
    closes = [float(getattr(b, "close", 0.0) or 0.0) for b in bars]
    if not any(o > 0 for o in opens):
        print("\n  no opens available, skipping the traded-rule simulation")
        return

    print()
    print("  the same rule, traded with one bar of lag and real costs:")
    print(f"  {'cost/fill':>10} {'gross':>10} {'cost':>10} {'net':>10} "
          f"{'turnover':>9}  verdict")
    for cost_bps in (0.0, 2.0, 5.0, 10.0, 25.0):
        res = lag_rule_pnl(opens, closes, cost_bps=cost_bps)
        verdict = "pays" if res["net"] > 0 else "costs eat it"
        print(
            f"  {cost_bps:>9.1f}b {100 * res['gross']:>9.1f}% "
            f"{100 * res['cost']:>9.1f}% {100 * res['net']:>9.1f}% "
            f"{res['trades']:>9.0f}  {verdict}"
        )
    print(
        "\n  Zero cost is the upper bound and it is already the honest test: if\n"
        "  the rule does not work even for free, no amount of cleverness makes\n"
        "  it work, and the dependency is not a tradable edge."
    )

    print()
    print("  OUT OF SAMPLE -- sign of phi from one half, traded on the other.")
    print("  The gross above is fitted to the same bars it is measured on; this is")
    print("  the only version of the number that means anything:")
    print(f"  {'direction':>22} {'phi on train':>13} {'gross':>10} "
          f"{'cost@10bp':>10} {'net':>10}")
    for label, reverse in (("first -> second half", False), ("second -> first half", True)):
        oos = out_of_sample_rule(opens, closes, cost_bps=10.0, reverse=reverse)
        print(
            f"  {label:>22} {oos['phi_train']:>+13.4f} "
            f"{100 * oos['gross']:>9.1f}% {100 * oos['cost']:>9.1f}% "
            f"{100 * oos['net']:>9.1f}%"
        )
    print(
        "\n  Both halves must pay for this to be a dependency rather than a fit."
    )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))