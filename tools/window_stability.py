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

_OVERRIDES = {
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
        step = 3600.0 if TIMEFRAME.endswith("h") else 86400.0
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
            enable_plugins=tuple(STRATEGIES),
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
                    inst.config["tickers"] = ["__not_this_symbol__"]

        header = (
            f"{'strategy':24} " + " ".join(f"{f'w{i+1}':>10}" for i in range(WINDOWS))
            + f" {'pos':>4} {'consistency':>11}"
        )
        print()
        print(header)
        print("-" * len(header))

        rows: list[tuple[str, list[float], list[int], int, int]] = []
        for sid in targets:
            inst = core.plugin_manager.get(sid)
            if inst is None:
                continue
            nets: list[float] = []
            trades: list[int] = []
            for start, end, count in bounds:
                inst.config["enabled"] = True
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
                if os.environ.get("WINDOW_DEBUG"):
                    print(f"    [debug] {sid} w{len(nets)} rows={len(out or [])} "
                          f"first={_first_row(out)}")

            positives = sum(1 for value in nets if value > 0)
            total = WINDOWS
            consistent = positives / total
            rows.append((sid, nets, trades, positives, total))
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
            for sid, nets, _tr, _p, _t in rows
            if WINDOWS > 1 and len(set(nets)) < WINDOWS
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
        ranked = sorted(rows, key=lambda r: -r[2] / max(1, r[3]))
        print("\nby sign consistency (positives / windows):")
        for sid, nets, positives, total in ranked:
            print(f"  {sid:26} {positives}/{total}   "
                  f"{[round(v, 2) for v in nets]}")
        stable = [r for r in rows if r[2] == r[3]]
        print()
        print(
            f"positive in every window: {len(stable)}/{len(rows)}"
        )
        print(
            "NOTE: consistency across windows on one asset is necessary, not "
            "sufficient. None of this clears the research gate, which requires "
            "walk-forward plus an untouched holdout."
        )
        await core.shutdown()
    return 0


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


def _first_row(rows: list[dict[str, Any]] | None) -> str:
    if not rows:
        return "<none>"
    row = rows[-1]
    keys = ("bars", "trades", "fills", "pnl", "error", "reason")
    return " ".join(f"{k}={row.get(k)!r}" for k in keys)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))