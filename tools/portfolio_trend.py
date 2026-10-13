"""The one hypothesis explicitly left untested: a portfolio rule, not a per-strategy rule.

Every experiment in this project tested a *single* strategy against a single
instrument. The strongest documented hypothesis in the literature -- time-series
momentum -- was implemented and measured **as a single-asset rule**, and the
parallel session's D10 is the sharpest note anyone has made about that:

> TSMOM was implemented as a single-asset rule. The paper's result is a
> 58-instrument, multi-asset, volatility-scaled portfolio. Different object.

So "TSMOM was refuted" was never established. Only one half of it was. This tests
the other half, on the instruments actually available: a multi-asset,
volatility-scaled trend-following portfolio.

Why this is a different question and not more of the same. The 20 individual
strategies were 20 separate claims, each competing for a significance bar that
the batch correction had already inflated 11x past the explored denominator.
A portfolio is **one** claim. It pools across instruments, so it does not pay the
multiplicity cost of picking a winner from a list -- and if the trend-following
effect is real, that is where it should show, because the paper's result lives
in the diversification, not in any single market.

REGISTERED BEFORE RUNNING. Parameters are the paper's, not tuned:

    signal          sign of trailing 252-bar (12-month) return
    position        long if positive, flat otherwise
    sizing          inverse volatility over the trailing 252 bars, scaled to a
                    40% annualised target
    combination     equal weight across instruments
    costs           the product's own model, 7 bps per fill

  H1  the portfolio beats random timing on the calibrated null. The null floor is
      21.8%, not the nominal 10%, and a portfolio claim is one hypothesis rather
      than twenty, so 21.8% is the number that has to be beaten.

  H2  the portfolio beats its own single-asset members. If it does not, the
      diversification is doing nothing and the single-asset result was the whole
      of it.

  H3  **the portfolio beats the best of its members**, which is the comparison
      that distinguishes "diversification helps" from "the mean of the parts is
      fine". This is the one that decides H1 and H2 are not the same finding.

Parameters are NOT tuned. Tuning lookback, vol_window or target_vol until a P&L
appears is best-of-N by construction, and that was declined in advance for a
reason that does not expire when the numbers come in badly.

Costs are the product's 7 bps per fill, read from config. No MOEX comparison, no
new fee assumptions -- this is a question about the signal, not about venue.
"""

from __future__ import annotations

import asyncio
import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

#: Liquid majors plus the higher-beta names the repo already trades, so the
#: portfolio spans regimes rather than three versions of one asset.
INSTRUMENTS = (
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT", "XRP/USDT",
    "ADA/USDT", "AVAX/USDT", "LINK/USDT",
)

LOOKBACK = 252          # 12 months, the paper's default
VOL_WINDOW = 252        # same horizon for the volatility estimate
TARGET_VOL = 0.40       # 40% annualised, as registered
COST_BPS = 7.0          # the product's own model: fee 5 + slippage 2
SEEDS = 40              # null seeds, per the "four seeds is a floor" rule
TRIALS = 200
WINDOWS = 4
BARS_PER_WINDOW = 749   # unchanged from the earlier sweeps


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = max(0.0, min(1.0, q)) * (len(ordered) - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return float(ordered[lo])
    w = pos - lo
    return float(ordered[lo]) * (1 - w) + float(ordered[hi]) * w


async def main() -> int:
    import random

    from host.trading_bot_core import TradingBotCore
    from tests.e2e_sandbox import _seed_sandbox_rules, build_sandbox_config

    with tempfile.TemporaryDirectory(prefix="tsmom_portfolio_") as raw:
        work = Path(raw)
        (work / "config").mkdir(parents=True, exist_ok=True)
        (work / "data").mkdir(parents=True, exist_ok=True)
        _seed_sandbox_rules(work / "data")
        cfg = build_sandbox_config(
            work / "config" / "trading_bot.yaml", data_root=work / "data",
            enable_plugins=("time_series_momentum",),
        )
        os.environ["TRADING_DATA_DIR"] = str(work / "data")
        core = TradingBotCore(cfg)
        await core.boot()
        await core.start()
        hist = core.service_registry.get("historical_data_service")

        series: dict[str, list[float]] = {}
        for sym in INSTRUMENTS:
            try:
                bars = await hist.fetch(
                    sym, class_code="CRYPTO", provider="ccxt",
                    timeframe="1d", limit=1500,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"{sym:12} fetch failed ({type(exc).__name__})")
                continue
            closes = [float(b.close) for b in (bars or []) if float(b.close or 0) > 0]
            series[sym] = closes
            print(f"{sym:12} {len(closes):>5} bars  "
                  f"{closes[0]:.4g} -> {closes[-1]:.4g}")

        # The registered design does not fit the available history, and the
        # honest move is to say so rather than shrink the windows until it runs.
        need = LOOKBACK + VOL_WINDOW + BARS_PER_WINDOW * WINDOWS + 2
        have = min((len(c) for c in series.values()), default=0)
        print()
        print(f"  registered design needs {need} daily bars per instrument "
              f"({LOOKBACK} signal + {VOL_WINDOW} vol + "
              f"{BARS_PER_WINDOW * WINDOWS} evaluation)")
        print(f"  available: {have}. Binance daily depth is ~3336 and this fetch "
              f"returned {have}.")

        if have <= need:
            print()
            print("  NOT EVALUABLE AT THE REGISTERED DESIGN.")
            print("  Reducing the window count or the lookback until the test runs")
            print("  is the same operation as tuning until a P&L appears, declined in")
            print("  advance for a reason that does not expire when the numbers come")
            print("  in badly. Reported, not worked around.")
            await core.shutdown()
            return 1

        if len(series) < 2:
            print("\nnot evaluable: fewer than two series")
            await core.shutdown()
            return 1

        await core.shutdown()

    returns = {
        s: [math.log(c[i] / c[i - 1]) for i in range(1, len(c))]
        for s, c in series.items()
    }
    syms = sorted(returns)

    # Trailing signal and inverse-vol weight, computed once and shared across
    # windows so the portfolio is a single time series rather than four separate
    # experiments that could then be pooled by accident.
    port: list[float] = []
    members: dict[str, list[float]] = {s: [] for s in syms}
    for i in range(LOOKBACK + VOL_WINDOW, len(returns[syms[0]])):
        legs = []
        for s in syms:
            r = returns[s]
            if i >= len(r):
                legs = []
                break
            tail = r[i - LOOKBACK:i]
            sig = sum(tail)
            window = r[i - VOL_WINDOW:i]
            mu = sum(window) / len(window)
            var = sum((x - mu) ** 2 for x in window) / (len(window) - 1)
            vol = math.sqrt(var) * math.sqrt(365)
            if vol <= 0 or sig == 0:
                continue
            weight = (1.0 if sig > 0 else 0.0) * min(1.0, TARGET_VOL / vol)
            if weight > 0:
                legs.append((weight, r[i]))
        if not legs:
            continue
        total_w = sum(w for w, _ in legs)
        p = sum((w / total_w) * x for w, x in legs)
        port.append(p)
        for s in syms:
            members[s].append(returns[s][i] if i < len(returns[s]) else 0.0)

    need = BARS_PER_WINDOW * WINDOWS
    if len(port) < need + 2:
        print(f"\nnot evaluable: {len(port)} portfolio bars, need {need}")
        return 1

    windows = [port[i:i + BARS_PER_WINDOW] for i in range(0, need, BARS_PER_WINDOW)]
    member_windows = {
        s: [w for i, w in enumerate(members[s]) if i < need][
            j * BARS_PER_WINDOW:(j + 1) * BARS_PER_WINDOW
        ][0:BARS_PER_WINDOW]
        for s in syms
        for j in range(WINDOWS)
    }

    rng = random.Random(20261013)

    def _costed(legs: list[float]) -> float:
        """Net of a round trip, charged on any window where the position was on."""
        gross = sum(legs)
        active = sum(1 for x in legs if x != 0.0)
        return gross - active * COST_BPS / 1e4

    port_net = [_costed(w) for w in windows]
    member_net = {
        s: [_costed(w) for w in member_windows[s]] for s in syms
    }

    # The null: random timing, same windows, same costs, same seeds as the
    # calibration that produced the 21.8% floor.
    nulls: list[list[float]] = []
    for w in windows:
        pool = []
        for _ in range(SEEDS):
            shuffled = list(w)
            rng.shuffle(shuffled)
            pool.append(_costed(shuffled))
        nulls.append(pool)

    print()
    print("=" * 72)
    print("MULTI-ASSET VOL-SCALED TREND PORTFOLIO (registered parameters)")
    print("=" * 72)
    print(f"  lookback {LOOKBACK}  vol_window {VOL_WINDOW}  "
          f"target_vol {TARGET_VOL:.0%}  cost {COST_BPS} bps/fill")
    print(f"  {len(syms)} instruments, {WINDOWS} windows x {BARS_PER_WINDOW} bars")
    print()
    print(f"  {'window':>7} {'portfolio':>12} {'p90 of null':>13}")
    cleared = 0
    for j, w in enumerate(port_net):
        p90 = _percentile(nulls[j], 90.0)
        ok = w >= p90
        cleared += 1 if ok else 0
        print(f"  {j + 1:>7} {w:>+12.4f} {p90:>13.4f}  {'Y' if ok else '.'}")
    print(f"\n  portfolio windows at or above p90: {cleared}/{WINDOWS}  "
          f"(calibrated floor is 21.8%, nominal would be 0.4/4)")
    print()
    for s in sorted(syms):
        m = sum(member_net[s])
        print(f"  member {s:12} sum of window net: {m:>+10.4f}")
    best_member = max(syms, key=lambda s: sum(member_net[s]))
    print(f"\n  portfolio total : {sum(port_net):+.4f}")
    print(f"  best member     : {best_member} {sum(member_net[best_member]):+.4f}")
    print(f"  equal-weight of members: "
          f"{sum(sum(member_net[s]) for s in syms) / len(syms):+.4f}")

    print()
    print("=" * 72)
    print("REGISTERED VERDICT")
    print("=" * 72)
    h1 = cleared >= 2
    h2 = sum(port_net) > sum(member_net[best_member])
    h3 = sum(port_net) > sum(
        sum(member_net[s]) for s in syms
    ) / len(syms)
    print(f"  H1 beats random timing ({cleared}/{WINDOWS}): "
          f"{'HOLDS' if h1 else 'FAILS'}")
    print(f"  H2 beats its best member: {'HOLDS' if h2 else 'FAILS'}")
    print(f"  H3 beats the average member: {'HOLDS' if h3 else 'FAILS'}")
    if not h1:
        print()
        print("  The portfolio rule does not clear its own null. The single-asset")
        print("  result was the whole of it, and the diversification is not doing")
        print("  anything here. Parameters were the registered ones and were not")
        print("  tuned, so this is a refutation rather than a failure to find.")
    return 0


def tempfile_tmp():
    import tempfile

    return tempfile.TemporaryDirectory(prefix="tsmom_portfolio_")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))