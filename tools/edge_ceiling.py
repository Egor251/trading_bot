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