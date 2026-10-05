"""Is there signal here at all, or was one window enough to look busy?

The 400-bar and 1000-bar sweeps disagreed: Spearman -0.337, exact permutation
p = 0.159, 12 of 19 strategies changing sign. That result says a single window
cannot separate strategies -- it does not say the strategies are worthless, and
it certainly does not say which ones are.

This measures the thing a selection decision actually requires: *consistency*.
Each strategy is run over K disjoint time windows of the same series. A strategy
with real signal keeps its sign across most of them. A strategy fitted to one
regime flips, which is what 12 of 19 did between two windows.

Disjointness is the whole point. Varying `limit` only produces nested tails, so
every window would share its most recent bars and the windows would not be
independent evidence -- which is exactly the mistake a single-window sweep makes
once, quietly. Hence `start`/`end` passthrough on `run_grid`.

What this tool will not do:

  - declare a winner. Consistency across four windows on one asset is a
    necessary condition, not a sufficient one, and the tool says so.
  - average the windows into a single number. A strategy that is +100, +100,
    -100, -100 averages to zero, and so does one that is +1, +1, -1, -1. The
    per-window nets are printed for exactly that reason.
  - adjust for the number of strategies measured. Twenty strategies across four
    windows is eighty numbers, and the best of eighty noisy numbers is
    optimistically biased. The ranking is printed in full so the operator can
    see how many were tried before the winner appeared.

Usage:
    python -m tools.window_stability 1h BTC/USDT 1000 4
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: Duplicated from tools/strategy_sweep.py rather than imported. That module
#: parses sys.argv at import time, so importing it under pytest put
#: `-p no:cacheprovider` in its LIMIT slot and raised on `int()` -- which made
#: this tool untestable, and an untestable tool is how the collapsed-window guard
#: below would have shipped unverified. Keep the two lists in step; a test
#: asserts equality.
STRATEGIES = [
    "adx_di_trend",
    "bollinger_reversion",
    "cci_reversion",
    "dual_thrust",
    "ema_cross",
    "heikin_ashi_trend",
    "keltner_breakout",
    "macd_trend",
    "ma_cross_example",
    "mean_reversion_example",
    "momentum_roc",
    "parabolic_sar_follow",
    "rsi_reversion",
    "stochastic_reversion",
    "supertrend_follow",
    "turtle_breakout",
    "volatility_breakout_example",
    "volume_impulse_example",
    "vwap_reversion",
    "williams_r_reversion",
    # The hypothesis under test. Kept in the same measurement as everything
    # else so it is judged by the same null, not by a separate run that might
    # be configured more kindly.
    "time_series_momentum",
]

def _arg(index: int, default: str) -> str:
    """argv lookup that survives import from a test runner.

    This module parses argv at import time, so under pytest `--no-cacheprovider`
    landed in the BARS slot and raised `invalid literal for int()`. A tool that
    cannot be imported cannot be tested, which is how the collapsed-window guard
    below would have gone in untested.
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


TIMEFRAME = _arg(1, "1h")
TICKER = _arg(2, "BTC/USDT")
BARS = _arg_int(3, 1000)
WINDOWS = _arg_int(4, 4)

#: Seeds for the null. Each is a full independent run of the same bars through
#: the same machinery, so the spread between them is the noise floor a strategy
#: has to clear before its P&L means anything. Twenty is a floor, not a
#: measurement: with twenty samples the tail is coarse, and a strategy sitting
#: near the edge of it is not distinguishable from one inside it.
NULL_SEEDS = _arg_int(5, 20)
BASELINE = "random_baseline"

_OVERRIDES = {
    # Research-only exchange override. Measured daily depth: binance 3000,
    # bitget ~1200, bybit 1000, okx 1000 -- bitget and the rest ignore `since`
    # on daily bars, so the backward walk exhausts after one page and `MAX_PAGES`
    # never gets a chance. Binance is the only source that pages.
    #
    # Scoped to this tool on purpose. `services.ccxt_market_service.exchange`
    # is the *live* market connector, and silently repointing the bot at a
    # different venue for the sake of a backtest would be the wrong kind of
    # convenience.
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


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def window_bounds(
    bars: list[dict[str, Any]], windows: int
) -> list[tuple[str, str, int]]:
    """Split the series into `windows` contiguous, non-overlapping ranges."""
    if windows < 1:
        raise ValueError("windows must be >= 1")
    if not bars:
        return []
    first = float(bars[0]["ts"])
    last = float(bars[-1]["ts"])
    span = (last - first) / windows
    out: list[tuple[str, str, int]] = []
    for index in range(windows):
        # End the window one interval early so consecutive windows do not both
        # claim the boundary bar.
        #
        # The real interval, not "does it end in h". A 4h series also ends in
        # "h", and treating its step as one hour put the window boundaries four
        # times too close together and claimed ~1998 bars for a 1000-bar
        # window -- which then became the fetch limit.
        from engine.ohlc_timeframe import timeframe_to_seconds  # noqa: PLC0415

        step = float(timeframe_to_seconds(TIMEFRAME) or 3600.0)
        start = datetime.fromtimestamp(first + index * span, tz=timezone.utc)
        end = datetime.fromtimestamp(
            first + (index + 1) * span - step, tz=timezone.utc
        )
        out.append((_iso(start), _iso(end), int(span // step)))
    return out


async def main() -> int:
    from host.trading_bot_core import TradingBotCore
    from tests.e2e_sandbox import _seed_sandbox_rules, build_sandbox_config

    with tempfile.TemporaryDirectory(prefix="window_stability_") as raw:
        work = Path(raw).resolve()
        (work / "config").mkdir(parents=True, exist_ok=True)
        (work / "data").mkdir(parents=True, exist_ok=True)
        _seed_sandbox_rules(work / "data")
        cfg = build_sandbox_config(
            work / "config" / "trading_bot.yaml",
            data_root=work / "data",
            enable_plugins=tuple(STRATEGIES) + (BASELINE,),
            extra_overrides=_OVERRIDES,
        )
        os.environ["TRADING_DATA_DIR"] = str(work / "data")
        core = TradingBotCore(cfg)
        await core.boot()
        phantom = core.service_registry.get("phantom_service")
        hist = core.service_registry.get("historical_data_service")
        # boot() builds the graph; start() runs the services. Without it nothing
        # subscribes, no signal reaches execution, and every window measures
        # zero trades -- which looks exactly like "the strategy does not trade".
        await core.start()
        if not phantom.is_running:
            await phantom.start()
        print(
            f"fill_timing = {phantom.config.get('fill_timing')!r} "
            f"capture_source = {phantom.config.get('capture_source')!r} "
            f"strength_floor = {phantom.sizing.strength_floor}"
        )

        # `run_grid` resolves the replay timeframe from the walk_forward config
        # block. Left unset it silently falls back to the service default of 1m,
        # so this tool was replaying one-minute bars under a 1h label: not enough
        # history for any hourly strategy's warmup, hence zero trades in every
        # window. `tools/strategy_sweep.py` sets this too -- it is a caller
        # obligation nobody is forced to remember.
        phantom.config["walk_forward"] = {
            **dict(phantom.config.get("walk_forward") or {}),
            "timeframe": TIMEFRAME,
        }

        series = await hist.fetch(
            TICKER,
            class_code="CRYPTO",
            provider="ccxt",
            timeframe=TIMEFRAME,
            limit=BARS,
        )
        bars = [{"ts": _epoch(getattr(bar, "ts", bar))} for bar in series]
        if not bars:
            print("no bars fetched; cannot measure")
            await core.shutdown()
            return 2
        bounds = window_bounds(bars, WINDOWS)
        print(
            f"{TICKER} {TIMEFRAME}: {len(bars)} bars split into {WINDOWS} "
            f"disjoint windows"
        )
        for start, end, count in bounds:
            print(f"  {start[:19]} .. {end[:19]}  (~{count} bars)")

        if len(series) < BARS:
            # Requested BARS, got fewer, and the shortfall is silent. Four
            # windows over half the intended series are four windows over a
            # range nobody asked to measure.
            print(
                f"WARNING: asked for {BARS} bars, fetched {len(series)}. The "
                f"windows below cover a shorter range than requested; treat "
                f"the split as {len(series)}-bar, not {BARS}-bar."
            )

        measured = int(os.environ.get("WINDOW_LIMIT") or len(STRATEGIES))
        targets = STRATEGIES[:measured]
        # Only the strategy under test may see candles; the rest are parked on a
        # sentinel ticker. Otherwise each candle fans out to all twenty and the
        # per-bar settle pays for handlers nobody is measuring.
        for sid in targets:
            for other in STRATEGIES:
                if other == sid:
                    continue
                inst = core.plugin_manager.get(other)
                if inst is not None:
                    # Disabled, not parked on a sentinel ticker. Parking only
                    # stops them trading; the plugin still receives every candle
                    # and still touches the one shared phantom runtime. Measured
                    # directly: three strategies run alone gave +12.37 / -17.13 /
                    # +10.41 with no agreement, and in the full run the same
                    # three gave -7.45 / -8.48 / -9.01 sharing 232.25 to the
                    # cent. The contamination was the nineteen bystanders.
                    inst.config["enabled"] = False
                    inst.config["tickers"] = ["__not_this_symbol__"]

        header = (
            f"{'strategy':24} " + " ".join(f"{f'w{i+1}':>10}" for i in range(WINDOWS))
            + f" {'pos':>4} {'consistency':>11}"
        )
        print()
        print(header)
        print("-" * len(header))

        rows: list[tuple[str, list[float], list[int], int, int]] = []
        costs_by_sid: dict[str, list[float]] = {}
        exposure_by_sid: dict[str, list[float]] = {}
        notional_by_sid: dict[str, list[float]] = {}
        for sid in targets:
            inst = core.plugin_manager.get(sid)
            if inst is None:
                continue
            nets: list[float] = []
            trades: list[int] = []
            costs: list[float] = []
            bars_in_market: list[float] = []
            notional: list[float] = []
            for start, end, count in bounds:
                inst.config["enabled"] = True
                # Re-enable only the strategy under test; every other plugin
                # stays off for the whole run.
                for other in STRATEGIES:
                    if other != sid:
                        _other = core.plugin_manager.get(other)
                        if _other is not None:
                            _other.config["enabled"] = False
                inst.config["observe_only"] = False
                inst.config["tickers"] = [TICKER]
                inst.config["class_code"] = "CRYPTO"
                out = await phantom.run_grid(
                    TICKER,
                    class_code="CRYPTO",
                    feed="history",
                    provider="ccxt",
                    # Must be the window's own size. Left unset, `limit` falls
                    # back to the service default and the fetch takes the *tail*
                    # of the requested range -- every window then replayed the
                    # same 120 bars and the windows were not independent at all.
                    # Identical `bars=` across windows is the tell.
                    limit=max(30, int(count)),
                    start=start if not os.environ.get("WINDOW_NO_RANGE") else None,
                    end=end if not os.environ.get("WINDOW_NO_RANGE") else None,
                    strategy_id=sid,
                    variants=[{}],
                )
                nets.append(_net_of(out))
                trades.append(int(_row_field(out, "trades") or 0))
                costs.append(_cost_of(out))
                bars_in_market.append(_bars_in_market_of(out))
                notional.append(_notional_of(out))
                if os.environ.get("WINDOW_DEBUG"):
                    print(f"    [debug] {sid} w{len(nets)} rows={len(out or [])} "
                          f"first={_first_row(out)}")

            positives = sum(1 for value in nets if value > 0)
            total = WINDOWS
            consistent = positives / total
            rows.append((sid, nets, trades, positives, total))
            costs_by_sid[sid] = costs
            exposure_by_sid[sid] = bars_in_market
            notional_by_sid[sid] = notional
            print(
                f"{sid:24} "
                + " ".join(f"{v:>10.2f}" for v in nets)
                + f" {positives:>4} {consistent:>10.0%}"
            )

        print()
        # Refuse to publish a table built on windows that are not independent.
        # `start`/`end` reached run_grid but did not reach the fetch, so every
        # window replayed the same bars and produced bit-identical P&L. That
        # printed as "0 of 20 strategies positive in every window", which reads
        # as a devastating result and means nothing at all. Identical numbers
        # across disjoint windows are proof of a broken measurement, not a
        # finding about the strategies.
        # Zero trades must mean exactly zero P&L. This is the only guard that matters
        # and the three before it were all too narrow: they looked for repeated
        # *values*, which is one symptom of state leaking between runs rather
        # than the thing itself. `volatility_breakout_example` never trades, and
        # it reported +229.51 in a window it did not trade in -- while the
        # repeat-detecting guard stayed quiet, because those windows were not
        # quite identical.
        #
        # A strategy that placed no order cannot have earned money under any
        # leak. Stated in terms of trades rather than values, it catches every
        # form of contamination at once.
        impossible = [
            (sid, nets, trades)
            for sid, nets, trades, _p, _t in rows
            if any(t == 0 and abs(v) > 1e-6 for v, t in zip(nets, trades))
        ]
        if impossible and WINDOWS > 1:
            print(
                f"ABORT: {len(impossible)} strategies report P&L in a window "
                f"where they placed zero trades."
            )
            for sid, nets, trades in impossible[:5]:
                print(f"  {sid}: pnl={nets} trades={trades}")
            print(
                "\nThat is impossible under any isolated run, so state is "
                "leaking between windows. Refusing to print a ranking."
            )
            await core.shutdown()
            return 3

        collapsed = [
            (sid, nets)
            for sid, nets, trades, _p, _t in rows
            # Identical results only mean "the windows are not disjoint" in
            # windows where the strategy actually traded. A strategy that does
            # nothing is legitimately 0.00 there, and flagging that aborted the
            # run twice before the null was ever measured: first for a never-
            # trading strategy at exactly 0.00, then for `ema_cross`, which
            # places roughly one order in four windows and is 0.00 in three of
            # them. Silence is only a symptom when something happened.
            if WINDOWS > 1
            and any(t > 0 for t in trades)
            and len(
                {
                    round(v, 6)
                    for v, t in zip(nets, trades)
                    if t > 0
                }
            )
            < sum(1 for t in trades if t > 0)
        ]
        if collapsed:
            print(
                f"ABORT: {len(collapsed)} of {len(rows)} strategies returned "
                f"identical results across all {WINDOWS} windows."
            )
            for sid, nets in collapsed[:5]:
                print(f"  {sid}: {nets}")
            print(
                "\nThe windows are not independent -- the same bars were "
                "measured more than once. This is a broken measurement, not a "
                "result about the strategies. Refusing to print a ranking."
            )
            await core.shutdown()
            return 3

        print(f"measurements taken: {len(rows)} strategies x {WINDOWS} windows "
              f"= {len(rows) * WINDOWS} numbers")
        ranked = sorted(rows, key=lambda r: -r[3] / max(1, r[4]))
        print("\nby sign consistency (positives / windows):")
        for sid, nets, _tr, positives, total in ranked:
            print(f"  {sid:26} {positives}/{total}   "
                  f"{[round(v, 2) for v in nets]}")

        # The null: same bars, same runtime, same costs, random timing. One run
        # per seed per window, so a strategy is compared against a distribution
        # rather than a single number -- and against a null built from the same
        # window it was measured in, because windows on one asset are not
        # interchangeable. Comparing against a pooled null would let a window
        # that simply went up flatter every strategy in it.
        print()
        print(f"measuring the null: {NULL_SEEDS} random-timing seeds per window")
        null_by_window: list[list[float]] = [[] for _ in range(WINDOWS)]
        null_trades: list[list[int]] = [[] for _ in range(WINDOWS)]
        null_costs: list[list[float]] = [[] for _ in range(WINDOWS)]
        null_exposure: list[list[float]] = [[] for _ in range(WINDOWS)]
        null_notional: list[list[float]] = [[] for _ in range(WINDOWS)]
        # phantom's capture source is the ensemble: fills come from ensemble
        # decisions, so a strategy outside the lineup emits signals that never
        # reach the book. That is why the baseline produced zero trades while
        # every real strategy traded -- it was not disabled, it was unheard.
        ens = core.service_registry.get("ensemble_service")
        lineup_before = dict(getattr(ens, "_ensemble_cfg", {}) or {})
        if ens is not None:
            ens._ensemble_cfg = {  # noqa: SLF001
                **lineup_before,
                "enabled": True,
                "strategies": [BASELINE],
            }
        base = core.plugin_manager.get(BASELINE)
        for seed in range(NULL_SEEDS):
            for widx, (start, end, count) in enumerate(bounds):
                for other in STRATEGIES:
                    _o = core.plugin_manager.get(other)
                    if _o is not None:
                        _o.config["enabled"] = False
                if base is not None:
                    base.config["enabled"] = True
                    base.config["observe_only"] = False
                    base.config["tickers"] = [TICKER]
                    base.config["class_code"] = "CRYPTO"
                    base.config["seed"] = seed
                out = await phantom.run_grid(
                    TICKER,
                    class_code="CRYPTO",
                    feed="history",
                    provider="ccxt",
                    limit=max(30, int(count)),
                    start=start,
                    end=end,
                    strategy_id=BASELINE,
                    variants=[{}],
                )
                null_by_window[widx].append(_net_of(out))
                null_trades[widx].append(int(_row_field(out, "trades") or 0))
                null_costs[widx].append(_cost_of(out))
                null_exposure[widx].append(_bars_in_market_of(out))
                null_notional[widx].append(_notional_of(out))

        # A null that never trades produces an all-zero distribution, against which
        # every strategy beats it -- 33 of 80 cleared p90 of a baseline that
        # placed no orders at all. That is the failure this project spent twenty
        # commits removing, and it had been left in the one place with no guard:
        # the strategy check counted trades, the null check did not. A null that
        # is not trading is a broken instrument, not a result.
        silent = [
            (widx, len(null_trades[widx]), sum(null_trades[widx]))
            for widx in range(WINDOWS)
            if null_trades[widx] and sum(null_trades[widx]) == 0
        ]
        if silent:
            print(
                f"ABORT: the random-timing null placed zero trades in "
                f"{len(silent)} of {WINDOWS} windows."
            )
            for widx, n, total in silent:
                print(f"  w{widx + 1}: {n} runs, {total} trades")
            print(
                "\nA null that never trades wins against nobody, so every "
                "strategy would beat it. Refusing to compare."
            )
            await core.shutdown()
            return 3

        print("\nrandom-timing null per window (same bars, same costs):")
        for widx, values in enumerate(null_by_window):
            if not values:
                continue
            ordered = sorted(values)
            print(
                f"  w{widx + 1}  n={len(ordered):3d}  "
                f"min={ordered[0]:9.2f}  median={_median(ordered):9.2f}  "
                f"max={ordered[-1]:9.2f}"
            )

        # The null is meant to be the strategies minus their decisions. Same
        # machinery -- yes, and that is what the docstring claims guarantees
        # comparability. Same *turnover* -- never checked, and turnover sets both
        # the cost bill and the width of the P&L distribution. A null trading
        # five times as often pays five times the bill and spreads five times as
        # wide, so the p90 bar it defines says as much about the null's own
        # trading frequency as about the market. `null_trades` was already
        # collected for the zero-trade guard and used for nothing else ever.
        print("\nturnover, exposure and cost drag (what P&L is actually built out of):")
        print(f"  {'strategy':24} " + " ".join(
            f"{'w' + str(i + 1):>26}" for i in range(WINDOWS)))
        for sid, nets, trades, _p, _t in rows:
            mine = costs_by_sid.get(sid, [])
            bars = exposure_by_sid.get(sid, [])
            notional = notional_by_sid.get(sid, [])
            cells = [
                f"{trades[widx]:4d}t "
                f"{(_exposure_of(bars, widx)):5.0f}b "
                f"{(_notional_of_w(notional, widx)):9.0f}n "
                f"{(mine[widx] if widx < len(mine) else 0.0):7.2f}c"
                for widx in range(len(nets))
            ]
            print(f"  {sid:24} " + " ".join(f"{c:>26}" for c in cells))
        for widx in range(WINDOWS):
            nt, nc, nb = null_trades[widx], null_costs[widx], null_exposure[widx]
            if not nt:
                continue
            print(f"  {'NULL mean':24} w{widx + 1}: {sum(nt) / len(nt):4.1f}t"
                  f" {sum(nb) / len(nb):5.0f}b {sum(nc) / len(nc):7.2f}c")

        print("\nis the null exchangeable with the strategies it judges?  If it")
        print("trades far more often than they do, the p90 it defines is mostly a")
        print("statement about the null's own frequency:")
        for widx in range(WINDOWS):
            st = [tr[widx] for _s, _n, tr, *_ in rows if widx < len(tr)]
            sc = [
                costs_by_sid[s][widx]
                for s, _n, _tr, *_ in rows
                if s in costs_by_sid and widx < len(costs_by_sid[s])
            ]
            nt, nc = null_trades[widx], null_costs[widx]
            if not st or not nt:
                continue
            s_tr = sum(st) / len(st)
            n_tr = sum(nt) / len(nt)
            s_c = sum(sc) / len(sc) if sc else 0.0
            n_c = sum(nc) / len(nc)
            sb = [
                _exposure_of(exposure_by_sid.get(s, []), widx)
                for s, _n, _tr, *_ in rows
            ]
            nb = null_exposure[widx]
            s_ba, n_ba = (sum(sb) / len(sb) if sb else 0.0), (
                sum(nb) / len(nb) if nb else 0.0
            )
            ratio = (n_tr / s_tr) if s_tr else float("inf")
            b_ratio = (n_ba / s_ba) if s_ba else float("inf")
            flag = "ok" if 0.5 <= ratio <= 2.0 else "MISMATCH"
            b_flag = "ok" if 0.8 <= b_ratio <= 1.25 else "MISMATCH"
            print(
                f"  w{widx + 1}  trades: strategy {s_tr:6.2f}  null {n_tr:6.2f}"
                f" ({ratio:5.2f}x) {flag}")
            print(
                f"        bars in market: strategy {s_ba:6.1f}  null {n_ba:6.1f}"
                f" ({b_ratio:5.2f}x) {b_flag}"
                f"   costs: strategy {s_c:8.2f}  null {n_c:8.2f}"
            )

        # The pre-registered test, kept in the instrument so it is reproducible
        # and so nobody can quietly change the criterion after seeing the answer.
        #
        # Registered before the run: a shared market factor exists and is worth
        # removing only if P&L actually varies with exposure. If it does not, the
        # factor is not identifiable from this data and subtracting any scalar
        # from it is decoration. The threshold was 0.6 in w3.
        #
        # Two exposures are tested because they answer different questions.
        # Time-in-market asks whether holding longer earned more. Notional asks
        # whether being bigger earned more. The first was measured and failed:
        # r = 0.04 in w3. This is the second.
        print("\ndoes P&L vary with exposure?  (pre-registered threshold: r>0.6)")
        print("  a shared factor is only removable if one of these is high:")
        for widx in range(WINDOWS):
            nets = [
                float(n[widx]) for _s, n, *_ in rows if widx < len(n)
            ]
            bars = [
                _exposure_of(exposure_by_sid.get(s, []), widx)
                for s, _n, *_ in rows
            ]
            notional = [
                _notional_of_w(notional_by_sid.get(s, []), widx)
                for s, _n, *_ in rows
            ]
            r_bars = _pearson(bars, nets)
            r_not = _pearson(notional, nets)
            verdict = "FACTOR IDENTIFIED" if max(r_bars, r_not) > 0.6 else "no factor"
            print(
                f"  w{widx + 1}  r(bars, p&l)={r_bars:6.3f}   "
                f"r(notional, p&l)={r_not:6.3f}   {verdict}"
            )

        print("\neach strategy against the null of its own window:")
        beats: list[tuple[str, int, float, float]] = []
        for sid, nets, _tr, _p, _t in rows:
            for widx, value in enumerate(nets):
                pool = (
                    null_by_window[widx] if widx < len(null_by_window) else []
                )
                if not pool:
                    continue
                pct = _percentile(value, pool)
                if pct >= 90.0:
                    beats.append((sid, widx + 1, value, pct))
        total_windows = sum(
            1
            for sid, nets, _tr, _p, _t in rows
            for widx in range(len(nets))
            if widx < len(null_by_window) and null_by_window[widx]
        )
        # Same comparison, market factor removed. Reported alongside rather than
        # instead of, because the two answer different questions: the raw number
        # asks "did this make money", the residual asks "did it make money for
        # any reason other than being in the market".
        if os.environ.get("WINDOW_RESIDUAL"):
            print("\nafter removing the per-window market factor:")
            # The denominator, printed. A residual is unreadable without knowing
            # what was subtracted, and a factor estimated from nothing is
            # indistinguishable from a factor estimated from real exposure.
            for widx in range(WINDOWS):
                bars_here = [
                    _exposure_of(exposure_by_sid.get(s, []), widx)
                    for s, _n, *_ in rows
                ]
                print(
                    f"  w{widx + 1}  bars in market={sum(bars_here):9.0f}  "
                    f"net={sum(n[widx] for _s, n, *_ in rows if widx < len(n)):11.2f}"
                    f"  factor={_market_factor(rows, exposure_by_sid, widx):8.4f}/bar"
                )
            residuals = _demean(rows, notional_by_sid)
            resid_by_window = _residual_pool(
                null_by_window, null_notional, rows, notional_by_sid
            )
            # Pre-registered success criterion, written before this was run:
            # after the factor is removed, what is left must not still track
            # exposure. In the two windows where a factor was identified at all
            # (w2 -0.722, w3 +0.673), the residual's correlation with notional
            # must fall below 0.2. If it does not, the linear model is still
            # wrong and subtracting more scalars will not help.
            print("\n  did it work?  (criterion: |r(notional, residual)| < 0.2)")
            for widx in range(WINDOWS):
                net_resid = [
                    float(r[widx]) for s, r in residuals if widx < len(r)
                ]
                weights = [
                    _notional_of_w(notional_by_sid.get(s, []), widx)
                    for s, _r in residuals
                ]
                after = _pearson(weights, net_resid)
                print(
                    f"  w{widx + 1}  r(notional, residual)={after:6.3f}"
                    f"   {'clean' if abs(after) < 0.2 else 'STILL TRACKS EXPOSURE'}"
                )
            kept = 0
            for sid, residual in residuals:
                marks = []
                for widx, value in enumerate(residual):
                    pool = resid_by_window[widx]
                    pct = _percentile(value, pool) if pool else 0.0
                    if pct >= 90.0:
                        kept += 1
                    marks.append(f"w{widx + 1}:{value:8.2f}(p{pct:.0f})")
                print(f"  {sid:24} " + " ".join(marks))
            print(
                f"\nwindows at or above p90 after demeaning: {kept} "
                f"of {total_windows}"
            )

        print(f"\nwindows at or above p90 of their own null: {len(beats)} "
              f"of {total_windows}")
        for sid, w, value, pct in beats:
            print(f"  {sid:24} w{w}  {value:10.2f}  p{pct:.0f}")
        print(
            "\nRead as a screening result, not a verdict. With "
            f"{NULL_SEEDS} null runs per window the tail is coarse: a strategy "
            "near the edge is not distinguishable from one inside it. And 20 "
            "strategies x 4 windows is 80 chances to find one, which is the "
            "whole reason the comparison exists."
        )
        await core.shutdown()
    return 0


def _demean(
    rows: list[tuple[str, list[float], list[int], int, int]],
    exposure: dict[str, list[float]],
) -> list[tuple[str, list[float]]]:
    """Strip the per-window market factor out of every strategy, by exposure.

    Window three on BTC made every strategy look strong at once, with the
    random-timing null's own median positive in the same window. That is one
    market, not twenty discoveries, and measuring raw P&L credits each strategy
    with a factor it did not earn. So take the factor out.

    Take out the right thing, though. The factor is a *market*: it pays whoever
    holds, in proportion to how much they hold. So the share of it that belongs
    to a strategy is its own exposure over the total exposure, times the total:

        residual = net - (exposure_i / sum_exposure) * sum_net

    The previous version subtracted the median across strategies, which silently
    assumed every strategy was exposed to the market equally -- exposure 1 for all
    of them. `volatility_breakout_example` places zero trades on this series, so
    its exposure is 0 and its net is 0, and median subtraction handed it
    `0 - median`: a market position it never held. Measured on the 3000-bar BTC
    daily run, that defect gave a strategy with a raw P&L of 0.00 in all four
    windows residuals of -934.94, +901.76, -5319.45 and +188.87 -- non-zero
    everywhere, sign following the peer median, and up to 5319 of apparent
    skill-or-luck attributed to a strategy that never placed an order. It now
    reads exactly 0.00 in every window. Note what that is and is not: the old
    numbers were a wrong *level*, not a manufactured winner -- half of them were
    negative. The bug was never "the denoiser promoted an inactive strategy", it
    was "the denoiser reported a market position for a strategy that had none",
    which makes the whole column unreadable rather than flattering.

    The caller passes the weights, and it passes *notional per bar* rather than
    trade count or bars held. Both earlier choices were measured and rejected.
    Trade count is not exposure: a strategy holding for two years and one holding
    for two weeks place the same handful of orders, and weighting by it removed
    0-5% of the cross-sectional spread. Bars held was better but still wrong --
    r = 0.103 against P&L in window three, because it counts time and ignores
    size. Notional per bar correlates 0.673 in that same window. Weights are
    passed in rather than derived here so that a silent fallback to trade count
    cannot reappear inside this function.

    What this is *not*, any more, is a common shift. It moves each strategy by a
    different amount, so both the levels and the ranking can change -- and that is
    the point, because a common shift cannot tell a strategy that avoided the
    market from one that simply held less of it by luck. What survives from the
    old safety argument is the part that was actually load-bearing: this cannot
    manufacture a winner out of a strategy that never traded. Exposure 0 gives a
    residual of exactly 0.0 by construction, not approximately, so no search over
    filters can turn inactivity into a result.

    And the honest limit of the whole thing: this does not work. Registered
    before it was run, the criterion was that the residual must stop tracking
    exposure -- |r| < 0.2. It reached -0.067 in window two and 0.165 in window
    one, but only 0.435 in window three and 0.504 in window four, where it was
    worse than before the subtraction. One scalar times one exposure cannot
    represent this structure. The function is kept because the factor it removes
    is real and now measurable, not because it has been shown to remove it.
    """
    out: list[tuple[str, list[float]]] = []
    for sid, nets, _tr, _p, _t in rows:
        bars = exposure.get(sid) or []
        residual: list[float] = []
        for widx, value in enumerate(nets):
            residual.append(
                float(value)
                - _exposure_of(bars, widx) * _market_factor(rows, exposure, widx)
            )
        out.append((sid, residual))
    return out


def _notional_of(out: list[dict[str, Any]] | None) -> float:
    """Mean notional held per bar of the window -- exposure with the size in it.

    `bars_in_position` says a book was in the market; this says how big it was
    while it was there. A percent-of-equity book that tripled its equity has also
    tripled its size, so the two strategies that differed most in the raw P&L were
    the two that differed most in size, not in decisions.
    """
    if not out:
        return 0.0
    try:
        block = out[-1].get("notional_exposure") or {}
        return float(block.get("notional_per_bar") or 0.0)
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _notional_of_w(notional: list[float], widx: int) -> float:
    """Per-window notional, same never-negative guard as `_exposure_of`."""
    if widx < 0 or widx >= len(notional):
        return 0.0
    return max(0.0, float(notional[widx]))


def _pearson(xs: list[float], ys: list[float]) -> float:
    """Correlation. Zero when either series is flat, which is itself the answer."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = (
        sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)
    ) ** 0.5
    return (num / den) if den else 0.0


def _bars_in_market_of(out: list[dict[str, Any]] | None) -> float:
    """Bars this run spent in the market -- the exposure the denoiser divides by.

    Read from the run's own metrics rather than reconstructed from its fills here:
    the walk-forward evaluator already knows every bar and the position on it, and
    re-deriving it in the reporting tool would be a second implementation of the
    same idea, which is how the last three faults in this file happened.
    """
    if not out:
        return 0.0
    try:
        return float((out[-1].get("exposure") or {}).get("bars_in_position") or 0.0)
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _exposure_of(bars: list[float], widx: int) -> float:
    """Bars in market for one window, which is that window's exposure.

    Never negative and never negative-by-omission: a window whose exposure is
    missing counts as no exposure, which is the conservative direction. Exposure
    zero earns nothing from the market, so nothing is subtracted from it -- and a
    strategy that never traded therefore reports exactly 0.0 rather than a
    rounding artefact of whatever its peers did.
    """
    if widx < 0 or widx >= len(bars):
        return 0.0
    return max(0.0, float(bars[widx]))


def _market_factor(
    rows: list[tuple[str, list[float], list[int], int, int]],
    exposure: dict[str, list[float]],
    widx: int,
) -> float:
    """The window's own P&L per bar in market: what holding paid.

    Zero when nobody held anything in that window. There is no factor to remove
    from a window in which no strategy had a position, and inventing one -- as the
    peer median did -- is how a single trade in a window becomes twenty apparent
    discoveries.
    """
    total_exposure = 0.0
    total = 0.0
    for sid, nets, _tr, *_rest in rows:
        if widx < 0 or widx >= len(nets):
            continue
        total_exposure += _exposure_of(exposure.get(sid) or [], widx)
        total += float(nets[widx])
    if total_exposure <= 0.0:
        return 0.0
    return total / total_exposure


def _residual_pool(
    null_by_window: list[list[float]],
    null_exposure: list[list[float]],
    rows: list[tuple[str, list[float], list[int], int, int]],
    exposure: dict[str, list[float]],
) -> list[list[float]]:
    """The null's own residuals, stripped with the factor measured above.

    Comparing a de-meaned strategy against a *raw* null compares two different
    quantities and flatters whichever side kept the market. Each null run is
    stripped with its own exposure -- the runs are independent of one another, so
    each carried its own time in the market -- while the factor itself is the one
    measured from the strategies in that window. A null run that was never in the
    market is left untouched, for the same reason a strategy that never was: it
    held nothing, so it earned nothing from the market to remove.
    """
    pools: list[list[float]] = []
    for widx, values in enumerate(null_by_window):
        factor = _market_factor(rows, exposure, widx)
        exposures = null_exposure[widx] if 0 <= widx < len(null_exposure) else []
        pools.append([
            float(value) - _exposure_of(exposures, i) * factor
            for i, value in enumerate(values)
        ])
    return pools


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _percentile(value: float, pool: list[float]) -> float:
    """Share of null runs beaten. Ties count as half, so an exactly-median
    value reads 50 rather than 100."""
    if not pool:
        return 0.0
    below = sum(1 for v in pool if v < value)
    equal = sum(1 for v in pool if v == value)
    return 100.0 * (below + 0.5 * equal) / len(pool)


def _epoch(value: Any) -> float:
    """Candle timestamps arrive as datetimes, floats or seconds strings."""
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    if text.isdigit():
        return float(text)
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def _row_field(rows: list[dict[str, Any]] | None, name: str) -> Any:
    if not rows:
        return None
    return rows[-1].get(name)


def _net_of(rows: list[dict[str, Any]] | None) -> float:
    """Net pnl from a run_grid result.

    Mirrors `tools/strategy_sweep.py`, which reads `out[-1]["pnl"]`. Taking the
    max across rows would be wrong: with one variant they coincide, but a max
    silently becomes a best-of-N pick the moment a caller passes a grid, which
    is precisely the selection bias this whole tool exists to avoid measuring.
    """
    if not rows:
        return 0.0
    row = rows[-1]
    try:
        return float(row.get("pnl") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _cost_of(rows: list[dict[str, Any]] | None) -> float:
    """Total costs paid on a run_grid result.

    The decomposition the tool was missing. Net P&L is one number with three
    contributions -- the market, the strategy's own decisions, and the bill for
    making those decisions -- and only the second is worth trading for. Without
    this, "the strategy lost" cannot be separated from "the broker took it", and
    the two call for opposite responses.
    """
    if not rows:
        return 0.0
    try:
        return float(rows[-1].get("total_costs") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _first_row(rows: list[dict[str, Any]] | None) -> str:
    if not rows:
        return "<none>"
    row = rows[-1]
    keys = ("bars", "trades", "fills", "pnl", "error", "reason")
    return " ".join(f"{k}={row.get(k)!r}" for k in keys)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))