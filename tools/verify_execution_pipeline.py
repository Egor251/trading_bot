"""Answer the outstanding audit question on a clean slate.

The audit's evidence for "0 successful executions since 2026-08-04" came from
`data/storage/records.db`, and that journal was subsequently shown to contain E2E
test artifacts — fills at the test-only price 159.0, `reason: "crypto_rule_e2e"`,
SBER candles during a crypto run. So the number is not trustworthy in either
direction: it may have under-reported real trades, or the pollution may have been
masking a genuinely dead pipeline.

This boots the real core in a throwaway data directory, feeds deterministic
historical bars through the real strategy -> Consilium -> RuleEngine -> paper
order path, and reports what actually came out. It writes nothing to `data/`.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from host.trading_bot_core import TradingBotCore  # noqa: E402
from microkernel_core import KernelEvent  # noqa: E402
from tests.e2e_sandbox import _seed_sandbox_rules, build_sandbox_config  # noqa: E402

WATCH = (
    "strategy.signal",
    "ensemble.decision",
    "ensemble.reject",
    "rule.triggered",
    "execution.result",
    "risk.reject",
    "system.halt",
    "research_gate_demoted",
)


def synthetic_bars(n: int = 600, start: float = 100.0) -> list[dict]:
    """Deterministic series with trends, reversals and range breakouts.

    The first attempt used a smooth sine wave, which produced no crossover *and*
    no breakout signals — dual_thrust needs an actual range expansion, so the
    generator now walks between trend legs with expanding ranges.
    """
    import math
    import random

    rng = random.Random(20261001)
    rows = []
    price = start
    drift = 0.35
    for i in range(n):
        if i % 45 == 0:
            # Reverse and widen: makes breakouts and mean reversion meaningful.
            drift = -drift * 1.4
        vol = 0.6 + (i % 45) * 0.06
        price += drift + rng.gauss(0.0, vol)
        price = max(5.0, price)
        o = price - drift * 0.5 + rng.gauss(0.0, vol * 0.3)
        h = max(price, o) + abs(rng.gauss(0.0, vol * 0.8))
        low = min(price, o) - abs(rng.gauss(0.0, vol * 0.8))
        rows.append(
            {
                "t": 1_700_000_000 + i * 60,
                "o": round(o, 4),
                "h": round(h, 4),
                "l": round(low, 4),
                "c": round(price, 4),
                "v": 1000 + (i % 50) * 3,
                "closed": True,
                "_drift": drift,
                "_sin": math.sin(i / 7.0),
            }
        )
    return rows


ALL_STRATEGIES = (
    "ma_cross_example",
    "mean_reversion_example",
    "macd_trend",
    "rsi_reversion",
    "bollinger_reversion",
    "momentum_roc",
    "ema_cross",
    "vwap_reversion",
)


async def main() -> int:
    # Lineup size matters: a lone contributor out of N carries strength 1/N, and
    # `sizing.strength_floor` (0.25) then rejects the order. That is intended —
    # one strategy out of eight should not move the portfolio — but it means the
    # number of live members decides whether the system can trade at all.
    lineup_ids = tuple(sys.argv[1:]) or ALL_STRATEGIES
    counts: Counter[str] = Counter()
    results: list[dict] = []
    samples: dict[str, list[dict]] = {}
    strength_hist: Counter[tuple] = Counter()
    strength_sum: Counter[str] = Counter()
    strength_n: Counter[str] = Counter()

    with tempfile.TemporaryDirectory() as raw_tmp:
        work = Path(raw_tmp)
        (work / "config").mkdir(parents=True, exist_ok=True)
        (work / "data").mkdir(parents=True, exist_ok=True)
        # `sandbox_core` normally seeds the ruleset; calling `build_sandbox_config`
        # directly bypasses that, and an empty rules dir means the RuleEngine
        # loads nothing and no decision can ever reach an order.
        seeded = _seed_sandbox_rules(work / "data")
        cfg_path = build_sandbox_config(
            work / "config" / "trading_bot.yaml",
            data_root=work / "data",
            enable_plugins=tuple(lineup_ids),
            extra_overrides={
                "storage.enabled": False,
                "services.storage_service.enabled": False,
                "services.storage_service.auto_start": False,
                "services.phantom_service.enabled": False,
                "services.phantom_service.auto_start": False,
                "services.ops_monitor_service.enabled": False,
                "services.ops_monitor_service.auto_start": False,
                "services.historical_data_service.enabled": False,
                "services.historical_data_service.auto_start": False,
                "services.ccxt_market_service.enabled": False,
                "services.ccxt_market_service.auto_start": False,
                "services.quik_market_service.enabled": False,
                "services.quik_market_service.auto_start": False,
                "services.alpaca_market_service.enabled": False,
                "services.alpaca_market_service.auto_start": False,
                "services.mock_market_service.enabled": False,
                "services.mock_market_service.auto_start": False,
            },
        )
        os.environ["TRADING_DATA_DIR"] = str(work / "data")
        print(f"seeded rule files: {seeded}")

        core = TradingBotCore(cfg_path)
        await core.boot()

        ens = core.service_registry.get("ensemble_service")
        lineup = {
            "enabled": True,
            "strategies": list(lineup_ids),
            "observe": [],
            "weight_mode": "equal",
        }
        ens._ensemble_cfg = dict(lineup)  # noqa: SLF001
        raw_cfg = getattr(core.config, "raw", None)
        if isinstance(raw_cfg, dict):
            trading = dict(raw_cfg.get("trading") or {})
            trading["ensemble"] = dict(lineup)
            raw_cfg["trading"] = trading
        if ens.manager is not None:
            ens.manager.set_equal_weights(lineup["strategies"])
        ens.config["debounce_sec"] = 0.05
        ens.config["route_execution"] = False

        # The shipped config parks nearly the whole fleet in observe_only, and
        # `build_sandbox_config` only sets `enabled` for the plugins under test —
        # it does not clear observe_only. A strategy that is observe_only returns
        # from `_on_signal` before buffering, so without this the Consilium sees
        # nothing at all and the harness measures an empty bus rather than the
        # consensus logic.
        for sid in lineup_ids:
            inst = core.plugin_manager.get(sid) if core.plugin_manager else None
            if inst is not None and hasattr(inst, "config"):
                inst.config["enabled"] = True
                inst.config["observe_only"] = False

        async def on_event(event: KernelEvent) -> None:
            et = event.event_type
            counts[et] += 1
            if et == "strategy.signal":
                p = event.payload or {}
                strength_hist[
                    (str(p.get("strategy_id")), str(p.get("reason")), str(p.get("action")))
                ] += 1
                strength_sum[str(p.get("strategy_id"))] += float(p.get("strength") or 0.0)
                strength_n[str(p.get("strategy_id"))] += 1
            if et == "execution.result":
                results.append(dict(event.payload or {}))
            if et in {"ensemble.decision", "ensemble.reject", "rule.triggered", "risk.reject"}:
                samples.setdefault(et, []).append(dict(event.payload or {}))

        for et in WATCH:
            await core.event_bus.subscribe(et, on_event)

        core.trading.mode = "paper"
        core.trading.orders.mode = "paper"
        core.trading.orders.equity = 100_000.0

        await core.start()

        # Feed the bars directly; every other service is off. Every instrument the
        # configured strategies subscribe to is published, because `dual_thrust`
        # listens to ETH/USDT only and BTC alone would leave it silent.
        runtime = core.trading
        from host.events import publish_market_candle

        tickers = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
        series = synthetic_bars()
        for row in series:
            for ticker in tickers:
                payload = {
                    "ticker": ticker,
                    "class_code": "CRYPTO",
                    "open": row["o"],
                    "high": row["h"],
                    "low": row["l"],
                    "close": row["c"],
                    "volume": row["v"],
                    "closed": True,
                    "source": "verify",
                }
                await publish_market_candle(
                    core.event_bus, source_id="verify", payload=payload
                )
            # Pace the bars the way a market actually delivers them. Feeding them
            # 15000x faster than the 1m timeframe the strategies assume makes the
            # Consilium debounce starve, which says nothing about the pipeline.
            await asyncio.sleep(0.01)
            if counts["execution.result"] >= 40:
                break

        # Let the last debounce flush.
        deadline = asyncio.get_event_loop().time() + 6.0
        while asyncio.get_event_loop().time() < deadline and not counts["execution.result"]:
            await asyncio.sleep(0.1)

        ok = [r for r in results if r.get("success")]
        print("=== event counts ===")
        for et in WATCH:
            print(f"  {et:26} {counts.get(et, 0)}")
        print()
        print("=== execution results ===")
        print(f"  total        {len(results)}")
        print(f"  success      {len(ok)}")
        by_reason = Counter(str(r.get("reason") or "") for r in results)
        for reason, n in by_reason.most_common(8):
            print(f"    {reason:34} {n}")
        print()
        print("=== book ===")
        print(f"  equity        {runtime.orders.equity:.2f}")
        print(f"  open positions {len(runtime.orders.positions)}")
        for key, pos in list(runtime.orders.positions.items())[:5]:
            print(f"    {key}: qty={pos.quantity} avg={pos.avg_price}")
        print(f"  protective slices {len(runtime.protection.ledger.all())}")
        print(f"  halted        {runtime.halted} {runtime._halt_reason}")  # noqa: SLF001
        print()
        print(f"lineup ({len(lineup_ids)}): {', '.join(lineup_ids)}")
        print()
        print("=== signal strength by strategy/reason ===")
        for sid in sorted(strength_n):
            n = strength_n[sid]
            print(f"  {sid:24} n={n:4d}  mean_strength={strength_sum[sid] / n:.4f}")
        for (sid, reason, action), n in strength_hist.most_common(10):
            print(f"      {sid:22} {reason:22} {action:5} x{n}")
        print()
        print("VERDICT:", "PIPELINE ALIVE" if ok else "PIPELINE DEAD")
        print()
        print("=== samples ===")
        for et, rows in samples.items():
            for row in rows[:4]:
                print(f"  [{et}] {json.dumps(row, default=str)[:400]}")

        await core.shutdown()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))