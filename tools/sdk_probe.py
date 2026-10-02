"""Isolate: does the strategy SDK produce decisions on this series at all?

The sweep showed 0 fills across every strategy with 1000 bars. That is either a
strategy-side problem (warm-up never satisfied, ticker mismatch, defaults tuned
for a different timeframe) or a harness-side problem (decisions produced but never
converted to fills). This asks the SDK directly, bypassing phantom entirely.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(r"C:\Users\Egor\PycharmProjects\trading_bot")
sys.path.insert(0, str(ROOT))
from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

TIMEFRAME = sys.argv[1] if len(sys.argv) > 1 else "1h"
TICKER = sys.argv[2] if len(sys.argv) > 2 else "BTC/USDT"
LIMIT = int(sys.argv[3]) if len(sys.argv) > 3 else 1000

STRATEGIES = [
    "adx_di_trend", "bollinger_reversion", "cci_reversion", "dual_thrust",
    "ema_cross", "heikin_ashi_trend", "keltner_breakout", "macd_trend",
    "ma_cross_example", "mean_reversion_example", "momentum_roc",
    "parabolic_sar_follow", "rsi_reversion", "stochastic_reversion",
    "supertrend_follow", "turtle_breakout", "volatility_breakout_example",
    "volume_impulse_example", "vwap_reversion", "williams_r_reversion",
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
                "services.historical_data_service.enabled": True,
                "services.historical_data_service.auto_start": True,
                "services.historical_data_service.disk_cache_ttl_sec": 1,
                "services.phantom_service.enabled": False,
                "services.phantom_service.auto_start": False,
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
        await core.start()
        hist = core.service_registry.get("historical_data_service")
        bars = await hist.fetch(
            TICKER, class_code="CRYPTO", provider="ccxt", timeframe=TIMEFRAME, limit=LIMIT
        )
        print(f"\n{TICKER} {TIMEFRAME}: {len(bars)} bars "
              f"{bars[0].ts:%Y-%m-%d %H:%M} .. {bars[-1].ts:%Y-%m-%d %H:%M}\n")

        print(f"{'strategy':26} {'bars_in':>8} {'decis':>6} {'BUY':>5} {'SELL':>5} {'HOLD':>6}  detail")
        print("-" * 100)
        for sid in STRATEGIES:
            inst = core.plugin_manager.get(sid)
            if inst is None:
                print(f"{sid:26} MISSING")
                continue
            inst.config["enabled"] = True
            inst.config["observe_only"] = False
            inst.config["tickers"] = [TICKER]
            inst.config["class_code"] = "CRYPTO"
            try:
                evaluate = inst.evaluate_bars
            except AttributeError:
                print(f"{sid:26} NO evaluate_bars")
                continue
            series = {
                "open": [float(b.open) for b in bars],
                "high": [float(b.high) for b in bars],
                "low": [float(b.low) for b in bars],
                "close": [float(b.close) for b in bars],
                "volume": [float(getattr(b, "volume", 0) or 0) for b in bars],
            }
            try:
                decisions = list(evaluate(series) or [])
            except Exception as exc:  # noqa: BLE001
                print(f"{sid:26} ERROR {type(exc).__name__}: {str(exc)[:60]}")
                continue
            acts = Counter(str(getattr(d, "action", "none")).upper() for d in decisions if d)
            reasons = Counter(str(getattr(d, "reason", "")) for d in decisions if d)
            top = reasons.most_common(1)[0] if reasons else ("", 0)
            first_side = next(
                (
                    str(getattr(d, "reason", ""))
                    for d in decisions
                    if d and str(getattr(d, "action", "")).upper() in {"BUY", "SELL"}
                ),
                "-",
            )
            print(
                f"{sid:26} {len(bars):8d} {sum(acts.values()):5d} "
                f"{acts.get('BUY', 0):5d} {acts.get('SELL', 0):5d} {acts.get('HOLD', 0):6d}"
                f"  first_side={first_side[:22]:22} top={top[0][:20]} (x{top[1]})"
            )

        await core.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))