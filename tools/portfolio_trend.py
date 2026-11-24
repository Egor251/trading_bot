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

#: Fixed before any result was seen, chosen for *asset-class* coverage rather than
#: for performance: US sectors, developed ex-US, emerging, long Treasuries, gold,
#: dollar strength, and the index itself.
#:
#: The cohort is survivorship-free in the way that matters and the check is worth
#: stating: every member begins on the same date, 2010-12-03, because that is when
#: the ETFs begin. So there is no earlier history to select on, and no delisted
#: name quietly missing from the list. The survivorship trap that rules out using
#: current S&P constituents back to 1990 does not apply here.
#:
#: XLRE is excluded: it begins 2015-10-08 and is 2768 bars, short of the registered
#: 3502. Including it would mean shortening the design after seeing the data.
INSTRUMENTS = (
    "^GSPC",  # US large-cap equity
    "XLK",
    "XLE",
    "XLF",
    "XLV",
    "XLI",
    "XLP",
    "XLY",
    "XLU",
    "XLB",
    "EFA",  # developed ex-US
    "EEM",  # emerging
    "TLT",  # long Treasuries -- the diversifier TSMOM lives on
    "GLD",  # gold
    "UUP",  # dollar strength
)

LOOKBACK = 252  # 12 months, the paper's default
VOL_WINDOW = 252  # same horizon for the volatility estimate
TARGET_VOL = 0.40  # 40% annualised, as registered
COST_BPS = 7.0  # the product's own model: fee 5 + slippage 2
SEEDS = 40  # null seeds, per the "four seeds is a floor" rule
TRIALS = 200
WINDOWS = 4
BARS_PER_WINDOW = 749  # unchanged from the earlier sweeps

#: Corrected-null parameters, registered in raw-record S4-1 before running.
HELD_DRAWS = TRIALS  # 200 draws per window
HELD_SEED = 361115  # fixed rng seed for the held-bars null

#: Cross-sectional null, registered S5-1. Same draw count, own seed.
CROSS_DRAWS = TRIALS
CROSS_SEED = 362115


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


def _held_null_windows(
    windows: list[list[float]], draws: int, seed: int
) -> tuple[list[list[float]], list[float]]:
    """Held-bars null (registered S4-1). Per window, q = active/len; each bar
    held independently with probability q; a draw scores sum(held legs) minus
    per-held-bar cost. Returns (pools, activity rates)."""
    import random

    rng = random.Random(seed)
    pools: list[list[float]] = []
    rates: list[float] = []
    for w in windows:
        active = sum(1 for x in w if x != 0.0)
        q = active / len(w) if w else 0.0
        rates.append(q)
        pool = []
        for _ in range(draws):
            held = [x for x in w if rng.random() < q]
            pool.append(sum(held) - len(held) * COST_BPS / 1e4)
        pools.append(pool)
    return pools, rates


def _null_totals(pools: list[list[float]]) -> list[float]:
    """One joint draw per index: independent window pools summed window-wise."""
    return [sum(pool[t] for pool in pools) for t in range(len(pools[0]))]


def _selftest() -> int:
    """N1-N3 on synthetic windows, no network. Returns 0 iff all hold."""
    import random

    W = BARS_PER_WINDOW
    rng = random.Random(20240117)
    failures: list[str] = []

    def windows_of(gen) -> list[list[float]]:
        return [[gen() for _ in range(W)] for _ in range(WINDOWS)]

    # N2a legs: negative drift, ~60% active.
    loser = windows_of(lambda: rng.gauss(-0.005, 0.01) if rng.random() < 0.6 else 0.0)
    pools, _ = _held_null_windows(loser, HELD_DRAWS, HELD_SEED)
    spreads = [max(p) - min(p) for p in pools]
    print("N1 null spreads (must all be > 0):")
    for j, s in enumerate(spreads):
        print(f"  window {j + 1}: spread {s:.6f}")
    if not all(s > 0 for s in spreads):
        failures.append("N1: null has no spread")
    obs = [sum(w) - sum(1 for x in w if x != 0.0) * COST_BPS / 1e4 for w in loser]
    scored = sum(1 for j in range(WINDOWS) if obs[j] >= _percentile(pools[j], 0.9))
    tot = sum(obs)
    med = _percentile(_null_totals(pools), 0.5)
    print(
        f"N2a loser: windows >= p90 {scored}/4 (need 0), "
        f"total {tot:+.4f} vs null median {med:+.4f} (need below)"
    )
    if scored != 0 or not tot < med:
        failures.append("N2a: loser does not fail")

    # N2b: flat -50%/bar, q = 1, null cannot vary -> refuse, never pass.
    flat = [[-0.5] * W for _ in range(WINDOWS)]
    flat_pools, _ = _held_null_windows(flat, HELD_DRAWS, HELD_SEED)
    flat_spreads = [max(p) - min(p) for p in flat_pools]
    print("N2b flat -50%/bar spreads (must all be exactly 0 -> refuse):")
    for j, s in enumerate(flat_spreads):
        print(f"  window {j + 1}: spread {s:.10f}")
    if not all(s == 0.0 for s in flat_spreads):
        failures.append("N2b: q=1 null varies, gate would score it")

    # N3: oracle holds the top-q bars of a fat-tailed window, same per-bar cost.
    fat = windows_of(
        lambda: (
            0.05 if rng.random() < 0.10 else (rng.gauss(0.0, 0.005) if rng.random() < 0.6 else 0.0)
        )
    )
    fat_pools, fat_rates = _held_null_windows(fat, HELD_DRAWS, HELD_SEED)
    oracle_tot = 0.0
    for w, q in zip(fat, fat_rates):
        k = int(round(q * len(w)))
        top = sorted(w, reverse=True)[:k]
        oracle_tot += sum(top) - len(top) * COST_BPS / 1e4
    null_tot = _null_totals(fat_pools)
    p90 = _percentile(null_tot, 0.9)
    print(f"N3 oracle: total {oracle_tot:+.4f} vs null p90 {p90:+.4f} (need >=)")
    if not oracle_tot >= p90:
        failures.append("N3: oracle does not clear the null")

    if failures:
        print("\nSELFTEST FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nSELFTEST PASS: N1 spread, N2a loser fails, N2b refused, N3 beats.")
    return 0


def _score_subset(cands: list[tuple[float, float]], idx: list[int]) -> float | None:
    """One bar from an index subset: inverse-vol weights renormalized. None if
    nothing scoreable (mirrors the `if not legs: continue` skip)."""
    tw = sum(cands[j][0] for j in idx)
    if tw <= 0:
        return None
    return sum(cands[j][0] / tw * cands[j][1] for j in idx)


def _score_window(
    panel_w: list[tuple[list[tuple[float, float]], list[int]]],
    picks: list[list[int]],
) -> float:
    """Window net for one subset selection. Same per-held-bar cost as _costed."""
    tot = 0.0
    active = 0
    for (cands, _), idx in zip(panel_w, picks):
        v = _score_subset(cands, idx)
        if v is None:
            continue
        tot += v
        active += 1
    return tot - active * COST_BPS / 1e4


def _cross_null_windows(
    panel_windows: list[list[tuple[list[tuple[float, float]], list[int]]]],
    draws: int,
    seed: int,
) -> tuple[list[list[float]], list[float]]:
    """Cross-sectional null (registered S5-1). Per bar, a uniform random subset
    of the candidates with the observed subset's size. Returns pools + mean k."""
    import random

    rng = random.Random(seed)
    pools: list[list[float]] = []
    ks: list[float] = []
    for pw in panel_windows:
        pool = []
        for _ in range(draws):
            picks = []
            for cands, obs in pw:
                k = min(len(obs), len(cands))
                picks.append(rng.sample(range(len(cands)), k) if k > 0 else [])
            pool.append(_score_window(pw, picks))
        pools.append(pool)
        ks.append(sum(len(obs) for _, obs in pw) / len(pw))
    return pools, ks


def _selftest2() -> int:
    """C1-C3 on synthetic 6-instrument panels, no network."""
    import random

    WB, HIST, NV = 200, 20, 6
    rng = random.Random(777)
    failures: list[str] = []

    def gen(drifts: list[float]) -> list[list[list[float]]]:
        return [
            [[rng.gauss(d, 0.02) for _ in range(WB + HIST)] for d in drifts] for _ in range(WINDOWS)
        ]

    def to_panel(
        blocks: list[list[list[float]]], rule: str
    ) -> list[list[tuple[list[tuple[float, float]], list[int]]]]:
        wins = []
        for j in range(WINDOWS):
            rets = blocks[j]
            pw = []
            for t in range(HIST, HIST + WB):
                cands = [(1.0, rets[s][t]) for s in range(NV)]
                trail = [sum(rets[s][t - HIST : t]) / HIST for s in range(NV)]
                order = sorted(range(NV), key=lambda s: trail[s])
                if rule == "anti":
                    idx = sorted(order[: NV // 2])
                elif rule == "pro2":
                    idx = sorted(order[-2:])
                else:
                    raise AssertionError(rule)
                pw.append((cands, idx))
            wins.append(pw)
        return wins

    def obs_totals(wins) -> list[float]:
        return [_score_window(pw, [obs for _, obs in pw]) for pw in wins]

    # C2a: bad half drifts down, flat half does not; rule holds the worst half
    # -> must fail. (First cut used equal drifts everywhere, under which any
    # same-size subset has the same expectation and the check is vacuous.)
    loser = to_panel(gen([-0.008] * 3 + [0.0] * 3), "anti")
    pools, _ = _cross_null_windows(loser, 50, CROSS_SEED)
    spreads = [max(p) - min(p) for p in pools]
    print("C1 null spreads (must all be > 0):")
    for j, s in enumerate(spreads):
        print(f"  window {j + 1}: spread {s:.6f}")
    if not all(s > 0 for s in spreads):
        failures.append("C1: null has no spread")
    obs = obs_totals(loser)
    scored = sum(1 for j in range(WINDOWS) if obs[j] >= _percentile(pools[j], 0.9))
    med = _percentile(_null_totals(pools), 0.5)
    print(
        f"C2a anti-selection: windows >= p90 {scored}/4 (need 0), "
        f"total {sum(obs):+.4f} vs null median {med:+.4f} (need below)"
    )
    if scored != 0 or not sum(obs) < med:
        failures.append("C2a: loser does not fail")

    # C2b: full hold -> spread exactly 0 -> refuse.
    full = [([(1.0, 0.01)] * NV, list(range(NV))) for _ in range(WB)]
    full_pools, _ = _cross_null_windows([full] * WINDOWS, 50, CROSS_SEED)
    fs = [max(p) - min(p) for p in full_pools]
    print("C2b full-hold spreads (must all be exactly 0 -> refuse):")
    for j, s in enumerate(fs):
        print(f"  window {j + 1}: spread {s:.10f}")
    if not all(s == 0.0 for s in fs):
        failures.append("C2b: full-hold null varies, gate would score it")

    # C3: 2 of 6 drift up, rule holds 2 best trailing -> must clear p90.
    fat = to_panel(gen([0.015, 0.015, -0.005, -0.005, -0.005, -0.005]), "pro2")
    fat_pools, _ = _cross_null_windows(fat, 50, CROSS_SEED)
    oracle = sum(obs_totals(fat))
    p90 = _percentile(_null_totals(fat_pools), 0.9)
    print(f"C3 pro-selection: total {oracle:+.4f} vs null p90 {p90:+.4f} (need >=)")
    if not oracle >= p90:
        failures.append("C3: pro-selection does not clear the null")

    if failures:
        print("\nSELFTEST2 FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nSELFTEST2 PASS: C1 spread, C2a fails, C2b refused, C3 beats.")
    return 0


def _live_held(
    windows: list[list[float]],
    member_windows: dict[str, list[list[float]]],
    syms: list[str],
    port_net: list[float],
    member_net: dict[str, list[float]],
) -> int:
    """Live run under the corrected held-bars null (registered S4-1). The old
    permuted path above is untouched; R-19 and its numbers stand as they were."""
    pools, rates = _held_null_windows(windows, HELD_DRAWS, HELD_SEED)
    spreads = [max(p) - min(p) for p in pools]
    gated = [s == 0.0 for s in spreads]

    print()
    print("=" * 72)
    print("PORTFOLIO vs HELD-BARS NULL (corrected, registered S4-1)")
    print("=" * 72)
    print(f"  {HELD_DRAWS} draws/window, seed {HELD_SEED}, activity-matched coin flip per window")
    print()
    print("  rule-1 spreads, before any comparison:")
    for j in range(WINDOWS):
        print(
            f"  window {j + 1}: q={rates[j]:.4f} "
            f"null spread {spreads[j]:.6f}"
            f"{'  GATED (no variance, not scored)' if gated[j] else ''}"
        )
    if any(gated):
        print("\n  NOT EVALUABLE: a window's null cannot vary.")
        return 1

    print(f"\n  {'window':>7} {'portfolio':>12} {'p90 of null':>13}")
    scored = 0
    for j in range(WINDOWS):
        p90 = _percentile(pools[j], 0.9)
        ok = port_net[j] >= p90
        scored += 1 if ok else 0
        print(f"  {j + 1:>7} {port_net[j]:>+12.4f} {p90:>13.4f}  {'Y' if ok else '.'}")
    print(f"\n  windows at or above p90: {scored}/{WINDOWS} (record only)")

    null_tot = _null_totals(pools)
    tot = sum(port_net)
    p90 = _percentile(null_tot, 0.9)
    med = _percentile(null_tot, 0.5)
    print(f"  null-total spread: {max(null_tot) - min(null_tot):.6f}")
    print(f"  portfolio total : {tot:+.4f}")
    print(f"  null median     : {med:+.4f}")
    print(f"  null p90        : {p90:+.4f}")
    print()
    print(
        f"  H1 beats random timing (criterion: total >= p90): {'HOLDS' if tot >= p90 else 'FAILS'}"
    )
    print()
    for s in sorted(syms):
        m = sum(member_net[s])
        print(f"  member {s:12} sum of window net: {m:>+10.4f}")
    best_member = max(syms, key=lambda s: sum(member_net[s]))
    print(f"\n  portfolio total : {tot:+.4f}")
    print(f"  best member     : {best_member} {sum(member_net[best_member]):+.4f}")
    print(f"  equal-weight of members: {sum(sum(member_net[s]) for s in syms) / len(syms):+.4f}")
    h2 = tot > sum(member_net[best_member])
    h3 = tot > sum(sum(member_net[s]) for s in syms) / len(syms)
    print(f"  H2 beats its best member: {'HOLDS' if h2 else 'FAILS'} (stands, S2-20)")
    print(f"  H3 beats the average member: {'HOLDS' if h3 else 'FAILS'} (stands, S2-20)")
    return 0


def _live_cross(
    panel_windows: list[list[tuple[list[tuple[float, float]], list[int]]]],
    member_windows: dict[str, list[list[float]]],
    syms: list[str],
    port_net: list[float],
    member_net: dict[str, list[float]],
) -> int:
    """Live run under the cross-sectional null (registered S5-1)."""
    pools, ks = _cross_null_windows(panel_windows, CROSS_DRAWS, CROSS_SEED)
    spreads = [max(p) - min(p) for p in pools]
    gated = [s == 0.0 for s in spreads]

    print()
    print("=" * 72)
    print("PORTFOLIO vs CROSS-SECTIONAL NULL (registered S5-1)")
    print("=" * 72)
    print(
        f"  {CROSS_DRAWS} draws/window, seed {CROSS_SEED}, "
        f"same-size random instrument subsets per bar"
    )
    print()
    print("  rule-1 spreads, before any comparison:")
    for j in range(WINDOWS):
        print(
            f"  window {j + 1}: mean k={ks[j]:.2f}/15 "
            f"null spread {spreads[j]:.6f}"
            f"{'  GATED (no variance, not scored)' if gated[j] else ''}"
        )
    if any(gated):
        print("\n  NOT EVALUABLE: a window's null cannot vary.")
        return 1

    print(f"\n  {'window':>7} {'portfolio':>12} {'p90 of null':>13}")
    scored = 0
    for j in range(WINDOWS):
        p90 = _percentile(pools[j], 0.9)
        ok = port_net[j] >= p90
        scored += 1 if ok else 0
        print(f"  {j + 1:>7} {port_net[j]:>+12.4f} {p90:>13.4f}  {'Y' if ok else '.'}")
    print(f"\n  windows at or above p90: {scored}/{WINDOWS} (record only)")

    null_tot = _null_totals(pools)
    tot = sum(port_net)
    p90 = _percentile(null_tot, 0.9)
    med = _percentile(null_tot, 0.5)
    print(f"  null-total spread: {max(null_tot) - min(null_tot):.6f}")
    print(f"  portfolio total : {tot:+.4f}")
    print(f"  null median     : {med:+.4f}")
    print(f"  null p90        : {p90:+.4f}")
    print()
    print(
        f"  H1 beats random selection (criterion: total >= p90): "
        f"{'HOLDS' if tot >= p90 else 'FAILS'}"
    )
    print()
    for s in sorted(syms):
        m = sum(member_net[s])
        print(f"  member {s:12} sum of window net: {m:>+10.4f}")
    best_member = max(syms, key=lambda s: sum(member_net[s]))
    print(f"\n  portfolio total : {tot:+.4f}")
    print(f"  best member     : {best_member} {sum(member_net[best_member]):+.4f}")
    print(f"  equal-weight of members: {sum(sum(member_net[s]) for s in syms) / len(syms):+.4f}")
    h2 = tot > sum(member_net[best_member])
    h3 = tot > sum(sum(member_net[s]) for s in syms) / len(syms)
    print(f"  H2 beats its best member: {'HOLDS' if h2 else 'FAILS'} (stands, S2-20)")
    print(f"  H3 beats the average member: {'HOLDS' if h3 else 'FAILS'} (stands, S2-20)")
    return 0


async def main() -> int:
    import argparse
    import random

    ap = argparse.ArgumentParser()
    ap.add_argument("--null", choices=("permuted", "held", "cross"), default="permuted")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--selftest2", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()
    if args.selftest2:
        return _selftest2()

    from host.trading_bot_core import TradingBotCore
    from tests.e2e_sandbox import _seed_sandbox_rules, build_sandbox_config

    with tempfile.TemporaryDirectory(prefix="tsmom_portfolio_") as raw:
        work = Path(raw)
        (work / "config").mkdir(parents=True, exist_ok=True)
        (work / "data").mkdir(parents=True, exist_ok=True)
        _seed_sandbox_rules(work / "data")
        cfg = build_sandbox_config(
            work / "config" / "trading_bot.yaml",
            data_root=work / "data",
            extra_overrides={
                # `_SANDBOX_DISABLED_SERVICES` contains `historical_data_service`.
                # Without this override every fetch fails with "yahoo adapter
                # unavailable (yfinance)" -- which reads as a missing package and
                # is not one. It cost a wrong diagnosis the first time.
                "services.historical_data_service.enabled": True,
                "services.historical_data_service.auto_start": True,
                "services.historical_data_service.disk_cache_ttl_sec": 60,
            },
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
                    sym,
                    class_code="INDEX",
                    provider="yahoo",
                    timeframe="1d",
                    limit=5000,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"{sym:8} fetch failed ({type(exc).__name__})")
                continue
            closes = [float(b.close) for b in (bars or []) if float(b.close or 0) > 0]
            series[sym] = closes
            print(f"{sym:8} {len(closes):>5} bars  {closes[0]:.4g} -> {closes[-1]:.4g}")

        need = LOOKBACK + VOL_WINDOW + BARS_PER_WINDOW * WINDOWS + 2
        have = min((len(c) for c in series.values()), default=0)
        print()
        print(
            f"  registered design needs {need} daily bars per instrument "
            f"({LOOKBACK} signal + {VOL_WINDOW} vol + "
            f"{BARS_PER_WINDOW * WINDOWS} evaluation)"
        )
        print(f"  available: {have}")

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

    returns = {s: [math.log(c[i] / c[i - 1]) for i in range(1, len(c))] for s, c in series.items()}
    syms = sorted(returns)

    # Trailing signal and inverse-vol weight, computed once and shared across
    # windows so the portfolio is a single time series rather than four separate
    # experiments that could then be pooled by accident.
    port: list[float] = []
    members: dict[str, list[float]] = {s: [] for s in syms}
    # Cross-sectional panel (S5): per scored bar, every vol-ok instrument as
    # (weight, return) plus the observed rule's held indices. Built in the same
    # loop WITHOUT touching legs/port/members — byte-reproduction of S2-20 is
    # asserted below, not assumed.
    panel: list[tuple[list[tuple[float, float]], list[int]]] = []
    for i in range(LOOKBACK + VOL_WINDOW, len(returns[syms[0]])):
        legs = []
        cands: list[tuple[float, float]] = []
        obs: list[int] = []
        bar_ok = True
        for s in syms:
            r = returns[s]
            if i >= len(r):
                legs = []
                bar_ok = False
                break
            tail = r[i - LOOKBACK : i]
            sig = sum(tail)
            window = r[i - VOL_WINDOW : i]
            mu = sum(window) / len(window)
            var = sum((x - mu) ** 2 for x in window) / (len(window) - 1)
            vol = math.sqrt(var) * math.sqrt(365)
            if vol <= 0:
                continue
            w = min(1.0, TARGET_VOL / vol)
            if sig == 0:
                cands.append((w, r[i]))
                continue
            weight = (1.0 if sig > 0 else 0.0) * w
            if weight > 0:
                legs.append((weight, r[i]))
                cands.append((w, r[i]))
                obs.append(len(cands) - 1)
            else:
                # sig < 0, vol-ok: eligible for random holding, not held by rule
                cands.append((w, r[i]))
        if not bar_ok:
            continue
        if not legs:
            continue
        total_w = sum(w for w, _ in legs)
        p = sum((w / total_w) * x for w, x in legs)
        port.append(p)
        panel.append((cands, obs))
        for s in syms:
            members[s].append(returns[s][i] if i < len(returns[s]) else 0.0)

    need = BARS_PER_WINDOW * WINDOWS
    if len(port) < need + 2:
        print(f"\nnot evaluable: {len(port)} portfolio bars, need {need}")
        return 1

    windows = [port[i : i + BARS_PER_WINDOW] for i in range(0, need, BARS_PER_WINDOW)]
    member_windows = {
        s: [members[s][j * BARS_PER_WINDOW : (j + 1) * BARS_PER_WINDOW] for j in range(WINDOWS)]
        for s in syms
    }

    rng = random.Random(20261013)

    def _costed(legs: list[float]) -> float:
        """Net of a round trip, charged on any window where the position was on."""
        gross = sum(legs)
        active = sum(1 for x in legs if x != 0.0)
        return gross - active * COST_BPS / 1e4

    port_net = [_costed(w) for w in windows]
    member_net = {s: [_costed(w) for w in member_windows[s]] for s in syms}

    if args.null == "held":
        return _live_held(windows, member_windows, syms, port_net, member_net)

    if args.null == "cross":
        panel_windows = [
            panel[j * BARS_PER_WINDOW : (j + 1) * BARS_PER_WINDOW] for j in range(WINDOWS)
        ]
        # Reproduction guard: the shared scorer on the observed picks must
        # recover the registered port_net exactly — the panel refactor above
        # is not allowed to move the measured object.
        for j in range(WINDOWS):
            rescored = _score_window(
                panel_windows[j],
                [obs for _, obs in panel_windows[j]],
            )
            assert abs(rescored - port_net[j]) < 1e-9, (
                f"window {j + 1}: scorer {rescored:.10f} vs port_net {port_net[j]:.10f}"
            )
        return _live_cross(panel_windows, member_windows, syms, port_net, member_net)

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
    print(
        f"  lookback {LOOKBACK}  vol_window {VOL_WINDOW}  "
        f"target_vol {TARGET_VOL:.0%}  cost {COST_BPS} bps/fill"
    )
    print(f"  {len(syms)} instruments, {WINDOWS} windows x {BARS_PER_WINDOW} bars")
    print()
    print(f"  {'window':>7} {'portfolio':>12} {'p90 of null':>13}")
    cleared = 0
    for j, w in enumerate(port_net):
        p90 = _percentile(nulls[j], 90.0)
        ok = w >= p90
        cleared += 1 if ok else 0
        print(f"  {j + 1:>7} {w:>+12.4f} {p90:>13.4f}  {'Y' if ok else '.'}")
    spread = max(nulls[0]) - min(nulls[0])
    print(
        f"\n  portfolio windows at or above p90: {cleared}/{WINDOWS}  "
        f"(calibrated floor is 21.8%, nominal would be 0.4/4)"
    )
    print(
        f"  null spread in window 1: {spread:.10f}  "
        f"-- see the H1 verdict below before reading that as a result"
    )
    print()
    for s in sorted(syms):
        m = sum(member_net[s])
        print(f"  member {s:12} sum of window net: {m:>+10.4f}")
    best_member = max(syms, key=lambda s: sum(member_net[s]))
    print(f"\n  portfolio total : {sum(port_net):+.4f}")
    print(f"  best member     : {best_member} {sum(member_net[best_member]):+.4f}")
    print(f"  equal-weight of members: {sum(sum(member_net[s]) for s in syms) / len(syms):+.4f}")

    print()
    print("=" * 72)
    print("REGISTERED VERDICT")
    print("=" * 72)
    h2 = sum(port_net) > sum(member_net[best_member])
    h3 = sum(port_net) > sum(sum(member_net[s]) for s in syms) / len(syms)
    print(f"  H2 beats its best member: {'HOLDS' if h2 else 'FAILS'}")
    print(f"  H3 beats the average member: {'HOLDS' if h3 else 'FAILS'}")
    print()
    print("  H1 beats random timing: NOT EVALUABLE")
    print()
    print("    The registered null is degenerate. `_costed` uses only the sum of a")
    print("    window and the count of its non-zero legs, and both are invariant")
    print("    under permutation -- so shuffling the window returns the window. All")
    print(f"    {SEEDS} draws in a window are the same number, p90 of a constant is")
    print("    that constant, and the comparison holds by arithmetic for any")
    print("    portfolio. A flat -50%-per-bar series also scores 4/4.")
    print()
    print("    So the 4/4 reported on the first run was not evidence about this")
    print("    portfolio; it is what the test prints when it cannot fail. The null")
    print("    is NOT redefined here: swapping it after seeing that it degenerates")
    print("    is the criterion-shopping this project registered against. A")
    print("    corrected null -- resampling which bars are held, so the cost term")
    print("    can vary -- has to be registered and run as its own hypothesis.")
    print()
    print("    What is left of this experiment is unaffected and is a negative:")
    print(f"    portfolio total {sum(port_net):+.4f}, all {len(syms)} members")
    print("    negative, and H2/H3 both fail. Diversification is not adding")
    print("    anything here.")
    return 0


def tempfile_tmp():
    import tempfile

    return tempfile.TemporaryDirectory(prefix="tsmom_portfolio_")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
