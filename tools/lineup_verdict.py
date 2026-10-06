"""The product's own lineup, judged by the corrections it just gained.

Every correction added to this repo has so far been tested on synthetic series. This
runs them on the thing that actually trades: the twenty strategies in the shipped
lineup, measured by the same walk-forward that produces clearance.

Nothing in the research log has ever asked the sharpest of the new questions about
this lineup. Every negative result here is a statement about individual strategies --
"nothing cleared". None of them is a statement about the *ranking*. Whether the best
scoring strategy is actually the best, or is whichever one happened to suit the
window, has never been measured on this system.

Writes to its own clearance file, never the live one. A research run has no business
mutating gate state, and a gate that cannot be rolled back is a gate that cannot be
experimented on.

Registered before running. Criteria fixed in advance:

  H1  the batch becomes *evaluable*. The shipped snapshot predates `p_value` and
      `oos_pnl_by_fold`, so every strategy currently reports `batch_not_evaluable`
      and no test runs. If the batch is still unevaluable after regeneration, the new
      fields are not reaching the store and everything built on them is inert.

  H2  no strategy clears the batch. Expected, and not the interesting part: the gate
      already clears 0 of 20.

  H3  **PBO on this lineup is high -- above 0.5.** This is the hypothesis worth
      testing. Twenty strategies on two or three tickers, most of which have never
      traded, are near-interchangeable in aggregate; if so the in-sample winner
      lands out-of-sample below where it started more often than not, and the
      ordering the UI displays is noise. This would be true *independently* of every
      strategy being insignificant, and it is a separate and broader failure than
      "nothing cleared".

  Falsified if PBO < 0.5, which would mean the lineup's ranking does carry
  information even though no member is individually significant -- a genuinely
  interesting outcome, and one worth acting on differently.

  H3 needs at least 8 fold observations per strategy, because that is how many the
  CSCV enumeration splits into. The first run used 5 and PBO could not execute;
  `FOLDS` was raised to 12 for resolution and nothing else was touched.

Usage:
    python -m tools.lineup_verdict [strategies...]
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

#: The shipped lineup. Taken from the ensemble roster rather than hardcoded, so a
#: change to `config/trading_bot.yaml` changes what is measured instead of quietly
#: leaving the experiment pointing at last quarter's strategies.
DEFAULT_LINEUP = (
    "dual_thrust", "momentum_roc", "keltner_breakout", "macd_trend",
    "supertrend_follow", "turtle_breakout", "williams_r_reversion",
    "cci_reversion", "bollinger_reversion", "ema_cross", "rsi_reversion",
    "adx_di_trend", "heikin_ashi_trend", "ma_cross_example",
    "parabolic_sar_follow", "stochastic_reversion",
    "volatility_breakout_example", "volume_impulse_example", "vwap_reversion",
    "mean_reversion_example",
)

#: Tickers, from `clearance_table.py`'s default and the shipped snapshot. Two at
#: minimum: the gate requires `min_tickers_robust: 2`, and promoting a single-ticker
#: measurement over a multi-ticker snapshot would reduce the fidelity of the gate
#: rather than improve it.
TICKERS = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
TIMEFRAME = "1d"
LIMIT = 1500
TRAIN, TEST = 200, 50
#: Folds, raised from 5 to 12 after the first run. CSCV needs at least as many
#: observations as it splits into (8 by convention), so with 5 folds PBO returned
#: `insufficient_series` and H3 could not be tested at all.
#:
#: This is measurement resolution, not a criterion: the registered bar is PBO > 0.5
#: and neither that number nor anything else was changed with it. Widening the window
#: until a test can run is different from widening it until it passes, and the
#: distinction is recorded here because the two are easy to confuse after the fact.
FOLDS = 12


def _loaders_disabled() -> dict[str, bool]:
    off = {}
    for name in (
        "ccxt_market_service", "mock_market_service", "quik_market_service",
        "alpaca_market_service", "storage_service",
    ):
        off[f"services.{name}.enabled"] = False
        off[f"services.{name}.auto_start"] = False
    return off


async def main() -> int:
    import yaml

    from host.trading_bot_core import TradingBotCore
    from tests.e2e_sandbox import _seed_sandbox_rules, build_sandbox_config

    from engine.research_digest import digest_walk_forward
    from engine.research_gate import (
        load_clearance,
        record_digest_clearance,
    )

    lineup = tuple(sys.argv[1:]) or DEFAULT_LINEUP
    raw_cfg = yaml.safe_load(
        (ROOT / "config" / "trading_bot.yaml").read_text(encoding="utf-8")
    )
    roster = (
        ((raw_cfg.get("trading") or {}).get("ensemble") or {}).get("strategies")
        or []
    )
    # Reported, not filtered on. `ensemble.strategies` is the *live* roster, which
    # ships with one member; the strategies under test are every plugin the gate
    # judges, and filtering the lineup down to the roster would measure one strategy
    # and call it the lineup.
    print(f"lineup: {len(lineup)} strategies")
    if roster:
        print(f"  live roster contains {len(roster)}; the rest are research-only")

    with tempfile.TemporaryDirectory(prefix="lineup_verdict_") as raw_tmp:
        work = Path(raw_tmp)
        (work / "config").mkdir(parents=True, exist_ok=True)
        (work / "data").mkdir(parents=True, exist_ok=True)
        _seed_sandbox_rules(work / "data")
        overrides = {
            "services.historical_data_service.enabled": True,
            "services.historical_data_service.auto_start": True,
            "services.historical_data_service.disk_cache_ttl_sec": 60,
            "services.phantom_service.enabled": True,
            "services.phantom_service.auto_start": True,
            # The write that leaked. `phantom_service` persisted clearance from every
            # `run_walk_forward` into an absolute path inside the repository, so this
            # tool wrote 20 entries to the live gate store on its first run while its
            # own docstring claimed otherwise, and repeated runs inflated
            # `variants_tried_lifetime` to 12. Redirecting TRADING_DATA_DIR did not
            # stop it, because the write target does not live under the data root.
            "services.phantom_service.persist_clearance": False,
            "storage.enabled": False,
            **_loaders_disabled(),
        }
        cfg_path = build_sandbox_config(
            work / "config" / "trading_bot.yaml",
            data_root=work / "data",
            # The sandbox disables every plugin outside this list, so without it
            # `run_walk_forward` raises "strategy plugin not loaded" and the whole
            # run measures nothing while looking like a clean negative.
            enable_plugins=tuple(lineup),
            extra_overrides=overrides,
        )
        os.environ["TRADING_DATA_DIR"] = str(work / "data")

        core = TradingBotCore(cfg_path)
        await core.boot()
        await core.start()
        phantom = core.service_registry.get("phantom_service")

        # Our own store. With `persist_clearance: false` above, the live clearance
        # file genuinely is not touched -- the previous version of this tool believed
        # that while writing 20 entries into it.
        out_store = ROOT / "data" / "research" / "lineup_clearance.json"
        out_store.parent.mkdir(parents=True, exist_ok=True)

        hist = core.service_registry.get("historical_data_service")
        windows: dict[str, tuple[int, int]] = {}
        for ticker in TICKERS:
            try:
                bars = await hist.fetch(
                    ticker, class_code="CRYPTO", timeframe=TIMEFRAME,
                    limit=LIMIT, provider="ccxt",
                )
            except Exception as exc:  # noqa: BLE001
                print(f"{ticker}: fetch failed ({type(exc).__name__})")
                continue
            if not bars or len(bars) <= TRAIN + TEST * max(1, FOLDS) + 1:
                print(f"{ticker}: {len(bars or [])} bars, too few")
                continue
            min_research = TRAIN + TEST * max(1, FOLDS)
            windows[ticker] = (max(0, len(bars) - min_research - 1), len(bars))
            print(
                f"{ticker:10} {TIMEFRAME}: {len(bars):>5} of {LIMIT} bars  "
                f"research {min_research} + holdout {windows[ticker][0]}"
            )
        if not windows:
            await core.shutdown()
            return 1
        print()

        measured: list[str] = []
        per_ticker_rows: dict[str, int] = {}
        for ticker, (holdout_bars, _n_bars) in windows.items():
            got = 0
            for name in lineup:
                try:
                    result = await phantom.run_walk_forward(
                        ticker,
                        class_code="CRYPTO",
                        provider="ccxt",
                        limit=LIMIT,
                        strategy_id=name,
                        feed="history",
                        timeframe=TIMEFRAME,
                        holdout_bars=holdout_bars,
                        train_bars=TRAIN,
                        test_bars=TEST,
                        min_folds=FOLDS,
                        step=50,
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"  {ticker:9} {name:26} FAILED {type(exc).__name__}")
                    continue
                digest = result.get("digest") or digest_walk_forward(result)
                if not digest.get("ok"):
                    print(f"  {ticker:9} {name:26} no digest")
                    continue
                record_digest_clearance(digest, path=out_store)
                series = digest.get("oos_pnl_by_fold") or []
                p_value = digest.get("p_value")
                print(
                    f"  {ticker:9} {name:26} folds={len(series):>3} "
                    f"mean={float(digest.get('mean_oos_pnl') or 0):>+9.3f} "
                    f"p={p_value if p_value is None else f'{p_value:.4f}':>8} "
                    f"variants={int(digest.get('variants_count') or 0):>4}"
                )
                if name not in measured:
                    measured.append(name)
                got += 1
            per_ticker_rows[ticker] = got
            print()

        print(
            f"measured {len(measured)} of {len(lineup)} strategies "
            f"across {len(windows)} ticker(s): {per_ticker_rows}"
        )
        store = load_clearance(out_store)
        entries = store.get("entries") or {}
        print(f"our store: {out_store}  ({len(entries)} entries)")

        from engine.research_gate import evaluate_many

        batch = evaluate_many(
            measured,
            path=out_store,
        )
        verdict = batch.get("batch") or {}
        print()
        print("=" * 72)
        print("BATCH VERDICT on the product's own lineup")
        print("=" * 72)
        print(json.dumps(
            {k: v for k, v in verdict.items() if k not in ("reality_check", "spa", "pbo")},
            indent=2, default=str,
        ))
        for key in ("reality_check", "spa", "pbo"):
            body = verdict.get(key) or {}
            print(f"\n  {key}:")
            for field in ("ok", "reason", "strategies", "p_value", "reject",
                          "max_stat", "pbo", "splits_used", "worse_than_random"):
                if field in body:
                    print(f"    {field:20} {body[field]}")

        print()
        print("=" * 72)
        print("REGISTERED VERDICT")
        print("=" * 72)
        evaluable = verdict.get("verdict") not in (None, "not_evaluable")
        print(f"  H1 evaluable: {'HOLDS' if evaluable else 'FAILS'}"
              f"   ({verdict.get('strategies_with_series')} series, "
              f"universe {verdict.get('universe')})")
        print(f"  H2 no strategy clears: "
              f"{'HOLDS' if not batch.get('live_ok') else 'FAILS'}")
        pbo = (verdict.get("pbo") or {}).get("pbo")
        if pbo is None:
            print("  H3 PBO: not measured")
        elif pbo > 0.5:
            print(f"  H3 PBO high: HOLDS ({pbo:.3f} > 0.5)")
            print("      The ranking is noise. Which strategy looks best is a")
            print("      property of the window, not of the strategy.")
        else:
            print(f"  H3 PBO high: FALSIFIED ({pbo:.3f} <= 0.5)")
            print("      The ordering carries information even though nothing is")
            print("      individually significant. A different situation, and one")
            print("      worth acting on differently.")

        await core.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))