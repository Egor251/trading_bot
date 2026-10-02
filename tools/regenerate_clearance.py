"""Regenerate research clearance from the cached history, offline.

The gate now enforces `live_ok` at Consilium start-up, and the shipped snapshot
was written 2026-08-06 — 56 days ago against a 30-day limit — so every member is
demoted and the system cannot trade. The legitimate way out is to run the research
that produces clearance, not to switch the gate off.

This runs walk-forward for `dual_thrust` (the only live member of the shipped
lineup) on the ETH/USDT daily history already in `data/ohlc_cache`, with a long
disk-cache TTL so no network call happens and the result is reproducible.

Whatever it produces is what it produces. If the strategy does not clear, that is
the answer and it stays demoted.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from host.trading_bot_core import TradingBotCore  # noqa: E402
from tests.e2e_sandbox import _seed_sandbox_rules, build_sandbox_config  # noqa: E402

STRATEGY = sys.argv[1] if len(sys.argv) > 1 else "dual_thrust"
TICKER = sys.argv[2] if len(sys.argv) > 2 else "ETH/USDT"
TIMEFRAME = sys.argv[3] if len(sys.argv) > 3 else "1d"
LIMIT = int(sys.argv[4]) if len(sys.argv) > 4 else 1500
CLASS = "CRYPTO"
TRAIN = int(sys.argv[5]) if len(sys.argv) > 5 else 200
TEST = int(sys.argv[6]) if len(sys.argv) > 6 else 50
FOLDS = int(sys.argv[7]) if len(sys.argv) > 7 else 3


async def main() -> int:
    import os

    with tempfile.TemporaryDirectory() as raw_tmp:
        work = Path(raw_tmp)
        (work / "config").mkdir(parents=True, exist_ok=True)
        (work / "data").mkdir(parents=True, exist_ok=True)
        _seed_sandbox_rules(work / "data")
        # Long TTL: research must run on the cached window, not on whatever the
        # exchange returns now. Also disables every market connector so the run
        # cannot silently depend on live prices.
        cfg_path = build_sandbox_config(
            work / "config" / "trading_bot.yaml",
            data_root=work / "data",
            enable_plugins=(STRATEGY,),
            extra_overrides={
                "services.historical_data_service.enabled": True,
                "services.historical_data_service.auto_start": True,
                "services.historical_data_service.disk_cache_ttl_sec": 60,
                "services.phantom_service.enabled": True,
                "services.phantom_service.auto_start": True,
                "services.ccxt_market_service.enabled": False,
                "services.ccxt_market_service.auto_start": False,
                "services.mock_market_service.enabled": False,
                "services.mock_market_service.auto_start": False,
                "services.quik_market_service.enabled": False,
                "services.quik_market_service.auto_start": False,
                "services.alpaca_market_service.enabled": False,
                "services.alpaca_market_service.auto_start": False,
                "storage.enabled": False,
                "services.storage_service.enabled": False,
                "services.storage_service.auto_start": False,
            },
        )
        os.environ.pop("TRADING_DATA_DIR", None)

        core = TradingBotCore(cfg_path)
        await core.boot()

        phantom = core.service_registry.get("phantom_service")
        if phantom is None:
            print("phantom_service missing")
            return 1
        inst = core.plugin_manager.get(STRATEGY)
        if inst is not None and hasattr(inst, "config"):
            inst.config["enabled"] = True
            inst.config["observe_only"] = False

        hist = core.service_registry.get("historical_data_service")
        bars = []
        if hist is not None:
            try:
                bars = await hist.fetch(
                    TICKER,
                    class_code=CLASS,
                    timeframe=TIMEFRAME,
                    limit=LIMIT,
                    provider="ccxt",
                )
            except Exception as exc:  # noqa: BLE001
                print(f"history fetch failed: {type(exc).__name__}: {exc}")
        print(f"bars available: {len(bars)} (requested {LIMIT})")
        if bars:
            print(f"  first {bars[0].ts}  last {bars[-1].ts}")
            print(f"  cache source: {(getattr(hist, '_last_fetch', {}) or {}).get('cache')}")

        if not bars:
            print("no history; cannot regenerate clearance")
            await core.shutdown()
            return 1

        # walk-forward needs `train + test * folds` research bars *in addition* to
        # the holdout, so the holdout is whatever the venue left over.
        min_research = TRAIN + TEST * max(1, FOLDS)
        holdout_bars = max(0, len(bars) - min_research - 1)
        if holdout_bars <= 0:
            print(
                f"not enough bars: {len(bars)} available, {min_research} needed for "
                f"research (train={TRAIN} test={TEST} folds={FOLDS})"
            )
            await core.shutdown()
            return 1
        print(
            f"research window {min_research} + holdout {holdout_bars} "
            f"= {min_research + holdout_bars} of {len(bars)}"
        )

        # The phantom service persists clearance into data/phantom/. Point it at
        # the real file so the gate can actually consume the result, and keep the
        # recorded snapshot for inspection.
        from engine.research_gate import DEFAULT_CLEARANCE_PATH, load_clearance

        print(f"\nclearance target: {DEFAULT_CLEARANCE_PATH}")
        print(f"entries before: {len(load_clearance().get('entries') or {})}")

        await core.start()
        if not phantom.is_running:
            await phantom.start()

        result = await phantom.run_walk_forward(
            TICKER,
            class_code=CLASS,
            provider="ccxt",
            limit=1000,
            strategy_id=STRATEGY,
            feed="history",
            timeframe=TIMEFRAME,
            # Walk-forward needs `train_bars + test_bars * min_folds` research bars *in
            # addition* to the holdout. At the defaults (400 + 100*5 = 900) a
            # 1000-bar window leaves almost nothing for the holdout, and the gate
            # then needs 8 holdout trades out of ~99 daily bars. Three folds give
            # a 600-bar research window and a 300-bar holdout, which is enough
            # daily bars to actually accumulate trades.
            holdout_bars=holdout_bars,
            train_bars=TRAIN,
            test_bars=TEST,
            min_folds=FOLDS,
            step=50,
        )

        print("\n=== walk_forward ===")
        for key in (
            "chosen",
            "holdout_pnl",
            "holdout_trades",
            "holdout_bars",
            "oos_trades",
            "oos_pnl",
            "ci_low",
            "ci_high",
            "ci_crosses_zero",
            "robust_sign_match",
            "profit_factor",
            "total_steps",
            "folds",
        ):
            if key in result:
                print(f"  {key:20} {result[key]}")

        store = load_clearance()
        entries = store.get("entries") or {}
        print(f"\nentries after (sandbox): {len(entries)}")

        # The sandbox redirected TRADING_DATA_DIR, so the run wrote its clearance
        # into the temp tree. Promote the real one so the gate can consume it.
        from engine.research_gate import record_digest_clearance

        digest = result.get("digest")
        written = record_digest_clearance(digest) if digest else None
        print(f"promoted to the live clearance file: {written}")

        store = load_clearance()
        entries = store.get("entries") or {}
        print(f"entries after (live): {len(entries)}  updated_at={store.get('updated_at')}")
        for key in sorted(entries):
            if STRATEGY in key:
                e = entries[key]
                picked = e.get("chosen") if isinstance(e.get("chosen"), dict) else e
                print(
                    f"  {key}: holdout_trades={picked.get('holdout_trades')} "
                    f"ci_crosses_zero={picked.get('ci_crosses_zero')} "
                    f"robust={picked.get('robust_sign_match')}"
                )

        print("\n=== gate verdict after the run ===")
        from engine.research_gate import LiveReadinessPolicy, evaluate_live_readiness

        verdict = evaluate_live_readiness(STRATEGY, policy=LiveReadinessPolicy())
        print(f"  live_ok = {verdict['live_ok']}  grade = {verdict['grade']}")
        for b in verdict["blockers"]:
            print(f"    BLOCKER {b['reason']}: {json.dumps(b.get('detail'), default=str)[:120]}")

        await core.shutdown()
        return 0 if verdict["live_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))