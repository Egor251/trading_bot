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

from tools.strategy_sweep import STRATEGIES  # noqa: E402

TIMEFRAME = sys.argv[1] if len(sys.argv) > 1 else "1h"
TICKER = sys.argv[2] if len(sys.argv) > 2 else "BTC/USDT"
BARS = int(sys.argv[3]) if len(sys.argv) > 3 else 1000
WINDOWS = int(sys.argv[4]) if len(sys.argv) > 4 else 4

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

        rows: list[tuple[str, list[float], int, int]] = []
        for sid in targets:
            inst = core.plugin_manager.get(sid)
            if inst is None:
                continue
            nets: list[float] = []
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
                if os.environ.get("WINDOW_DEBUG"):
                    print(f"    [debug] {sid} w{len(nets)} rows={len(out or [])} "
                          f"first={_first_row(out)}")

            positives = sum(1 for value in nets if value > 0)
            total = WINDOWS
            consistent = positives / total
            rows.append((sid, nets, positives, total))
            print(
                f"{sid:24} "
                + " ".join(f"{v:>10.2f}" for v in nets)
                + f" {positives:>4} {consistent:>10.0%}"
            )

        print()
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