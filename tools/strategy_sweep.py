"""Strategy sweep on real, deep history with realistic costs.

Answers "why did no strategy make money on phantom" with numbers rather than
speculation. For each strategy this reports trades, win rate, gross PnL, costs,
and net PnL, plus the per-trade cost drag — because on intraday crypto a
strategy can look profitable gross and still lose everything to fees.

Run: python tools/strategy_sweep.py [timeframe] [ticker]
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"C:\Users\Egor\PycharmProjects\trading_bot")
sys.path.insert(0, str(ROOT))
from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

TIMEFRAME = sys.argv[1] if len(sys.argv) > 1 else "1h"
TICKER = sys.argv[2] if len(sys.argv) > 2 else "BTC/USDT"
LIMIT = int(sys.argv[3]) if len(sys.argv) > 3 else 1000

TRACE_SEEN: dict[str, dict] = {}

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
    "williams_williams" if False else "williams_r_reversion",
    "time_series_momentum",
]


async def main() -> int:
    from host.trading_bot_core import TradingBotCore
    from tests.e2e_sandbox import _seed_sandbox_rules, build_sandbox_config

    with tempfile.TemporaryDirectory() as raw:
        work = Path(raw)
        (work / "config").mkdir(parents=True, exist_ok=True)
        (work / "data").mkdir(parents=True, exist_ok=True)
        _seed_sandbox_rules(work / "data")
        cfg = build_sandbox_config(
            work / "config" / "trading_bot.yaml",
            data_root=work / "data",
            enable_plugins=tuple(STRATEGIES),
            extra_overrides={
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
            },
        )
        os.environ["TRADING_DATA_DIR"] = str(work / "data")
        core = TradingBotCore(cfg)
        await core.boot()
        phantom = core.service_registry.get("phantom_service")
        hist = core.service_registry.get("historical_data_service")

        await core.start()
        if not phantom.is_running:
            await phantom.start()
        # `run_grid` resolves the timeframe from the walk_forward config block.
        phantom.config["walk_forward"] = {
            **dict(phantom.config.get("walk_forward") or {}),
            "timeframe": TIMEFRAME,
        }
        # Decisive A/B on the fill-timing change.
        if os.environ.get("SWEEP_FILL_TIMING"):
            phantom.config["fill_timing"] = os.environ["SWEEP_FILL_TIMING"]
        if os.environ.get("SWEEP_STRENGTH_FLOOR"):
            phantom.sizing.strength_floor = float(os.environ["SWEEP_STRENGTH_FLOOR"])
        print(f"fill_timing = {phantom.config.get('fill_timing')!r} "
              f"capture_source = {phantom.config.get('capture_source')!r} "
              f"strength_floor = {phantom.sizing.strength_floor}")

        bars = await hist.fetch(
            TICKER, class_code="CRYPTO", provider="ccxt", timeframe=TIMEFRAME, limit=LIMIT
        )
        if os.environ.get("SWEEP_TRACE"):
            for _sid in ("ma_cross_example", "adx_di_trend"):
                _p = core.plugin_manager.get(_sid)
                if _p is None:
                    continue
                _orig = _p.handle_candle
                _seen = {"candle": 0, "allowed": 0, "ticker": 0, "decided": 0}

                async def _wrapped(event, _p=_p, _orig=_orig, _seen=_seen):
                    _seen["candle"] += 1
                    if _p.candle_allowed(event):
                        _seen["allowed"] += 1
                        if _p.ticker_allowed((event.payload or {}).get("ticker")):
                            _seen["ticker"] += 1
                            d = await _orig(event)
                            if d is not None:
                                _seen["decided"] += 1
                            return d
                    return await _orig(event)

                _p.handle_candle = _wrapped
                TRACE_SEEN[_sid] = _seen
                print(f"TRACE {_sid}: tickers={_p.config.get('tickers')} "
                      f"class={_p.config.get('class_code')}")

        counts: dict[str, int] = {}

        async def _tally(event):
            counts[event.event_type] = counts.get(event.event_type, 0) + 1

        for _et in ("market.candle", "strategy.signal", "ensemble.decision", "phantom.fill"):
            await core.event_bus.subscribe(_et, _tally)
        print(f"\n{TICKER} {TIMEFRAME}: {len(bars)} bars "
              f"{bars[0].ts:%Y-%m-%d} .. {bars[-1].ts:%Y-%m-%d}\n")

        # Prove the series is contiguous before trusting any metric computed on it.
        adapter = getattr(hist, "_ccxt", None)
        if adapter is not None:
            raw_rows = await adapter._fetch_ohlcv_paged(TICKER, TIMEFRAME, LIMIT)
            gaps = adapter.bar_gaps(raw_rows)
            from engine.ohlc_timeframe import timeframe_to_seconds

            expected = timeframe_to_seconds(TIMEFRAME) * 1000
            off_grid = sum(1 for g in gaps if g != expected)
            print(f"  series: {len(raw_rows)} bars, {len(gaps)} steps, "
                  f"expected={expected}ms, off-grid gaps={off_grid}")

        rows: list[tuple] = []
        if os.environ.get("SWEEP_DEBUG"):
            for sid in STRATEGIES[:1]:
                inst = core.plugin_manager.get(sid)
                inst.config["enabled"] = True
                inst.config["observe_only"] = False
                inst.config["tickers"] = [TICKER]
                inst.config["class_code"] = "CRYPTO"
                out = await phantom.run_grid(
                    TICKER, class_code="CRYPTO", feed="history", provider="ccxt",
                    limit=LIMIT, strategy_id=sid, variants=[{}],
                )
                print(f"\n=== RAW result for {sid} ===")
                for row in out:
                    for k in sorted(row):
                        v = str(row[k])
                        if len(v) > 160:
                            v = v[:160] + "..."
                        print(f"    {k}: {v}")
                    print("    ---")
            await core.shutdown()
            return 0
        header = (
            f"{'strategy':24} {'trades':>7} {'fills':>7} {'rejected':>9} "
            f"{'net':>11}  top reject reason"
        )
        print(header)
        print("-" * len(header))
        for sid in STRATEGIES[: int(os.environ.get("SWEEP_LIMIT") or len(STRATEGIES))]:
            # Measure one strategy at a time, the way `run_grid` is actually used.
            # With every plugin subscribed, each candle fans out to all of them
            # and the per-bar settle pays for handlers nobody is researching —
            # 20 strategies x 1000 bars did not finish in 10 minutes.
            for other in STRATEGIES:
                if other == sid:
                    continue
                _o = core.plugin_manager.get(other)
                if _o is not None:
                    # An empty `tickers` means "all symbols"; a sentinel that
                    # matches nothing is what actually switches it off.
                    _o.config["tickers"] = ["__not_this_symbol__"]
            inst = core.plugin_manager.get(sid)
            if inst is None:
                continue
            inst.config["enabled"] = True
            inst.config["observe_only"] = False
            inst.config["tickers"] = [TICKER]
            inst.config["class_code"] = "CRYPTO"
            try:
                out = await phantom.run_grid(
                    TICKER,
                    class_code="CRYPTO",
                    feed="history",
                    provider="ccxt",
                    limit=LIMIT,
                    strategy_id=sid,
                    variants=[{}],
                )
            except Exception as exc:  # noqa: BLE001
                print(f"{sid:24} ERROR {type(exc).__name__}: {str(exc)[:50]}")
                continue
            if not out:
                print(f"{sid:24} no result")
                continue
            if os.environ.get("SWEEP_TRACE"):
                for _i, _r in enumerate(out):
                    print(f"      row[{_i}] feed={_r.get('feed')} bars={_r.get('bars')} "
                          f"trades={_r.get('trades')} fills={_r.get('fills')} "
                          f"rejected={_r.get('rejected')} pnl={_r.get('pnl')}")
            r = out[-1]
            trades = int(r.get("trades") or 0)
            net = float(r.get("pnl") or 0.0)
            costs = float(r.get("total_costs") or 0.0)
            _gross = net + costs
            fills = int(r.get("fills") or 0)
            rejected = int(r.get("rejected") or 0)
            reasons = dict(r.get("reject_reasons") or {})
            rows.append((sid, trades, fills, rejected, net, reasons))
            top = max(reasons.items(), key=lambda kv: kv[1]) if reasons else ("-", 0)
            print(
                f"{sid:24} {trades:7d} {fills:7d} {rejected:7d} {net:11.2f} "
                f"{top[0][:22]:22} x{top[1]}"
            )
            if os.environ.get("SWEEP_TRACE"):
                print(f"      bus: {dict(counts)}")
                print(
                    f"      ledger: _fills={len(phantom._fills)} "  # noqa: SLF001
                    f"rejects={phantom.shadow_reject_summary()} "  # noqa: SLF001
                    f"pending={len(phantom._pending_fills)} "  # noqa: SLF001
                    f"runtime_pos={len(phantom.runtime.open_positions())}"
                    if phantom.runtime is not None
                    else "      ledger: no runtime"
                )

        print()
        profitable = [r for r in rows if r[4] > 0]
        traded = [r for r in rows if r[2] > 0]
        print(f"produced fills: {len(traded)}/{len(rows)}")
        print(f"profitable    : {len(profitable)}/{len(traded)} of those that traded")
        for r in sorted(rows, key=lambda x: -x[4]):
            print(f"    {r[0]:24} trades={r[1]:5d} fills={r[2]:5d} "
                  f"rejected={r[3]:5d} net={r[4]:10.2f}  {r[5]}")

        await core.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))