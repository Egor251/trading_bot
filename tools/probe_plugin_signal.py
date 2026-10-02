"""Isolate the last link: decision -> signal, with no event bus involved.

Everything upstream is now measured as working (candles published, phantom book
fed, plugin reached). This drives one plugin's handler directly with the exact
payload shape the replay publishes, so the only thing under test is whether
`emit_on_side_change` decides to publish.

No product code is patched and no bus is involved.
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

TICKER = sys.argv[1] if len(sys.argv) > 1 else "BTC/USDT"
STRATEGY = sys.argv[2] if len(sys.argv) > 2 else "ma_cross_example"


async def main() -> int:
    from microkernel_core import KernelEvent

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
            enable_plugins=(STRATEGY,),
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

        bars = await hist.fetch(
            TICKER, class_code="CRYPTO", provider="ccxt", timeframe="1h", limit=400
        )
        inst = core.plugin_manager.get(STRATEGY)
        inst.config["enabled"] = True
        inst.config["observe_only"] = False
        inst.config["tickers"] = [TICKER]
        inst.config["class_code"] = "CRYPTO"

        published: list = []
        all_events: Counter[str] = Counter()

        async def _on_signal(event):
            published.append(dict(event.payload or {}))

        async def _on_any(event):
            all_events[event.event_type] += 1

        await core.event_bus.subscribe("strategy.signal", _on_signal)
        await core.event_bus.subscribe("*", _on_any)

        # Sanity: can this bus deliver a hand-built strategy.signal at all?
        before = len(published)
        await core.event_bus.publish(
            KernelEvent(
                event_type="strategy.signal",
                source_id="probe",
                payload={
                    "strategy_id": "probe",
                    "action": "BUY",
                    "ticker": TICKER,
                    "class_code": "CRYPTO",
                    "strength": 0.9,
                    "reason": "probe_direct",
                },
            )
        )
        print(f"  bus self-test delivered: {len(published) - before} of 1")

        # Reproduce exactly what run_grid does: open a research session, feed the
        # bars through the phantom's own ingest, then drive the plugin handler.
        rid = "testrid0001"
        inst.begin_research(
            run_id=rid,
            config=inst.isolated_config({}),
        )
        phantom.reset_ledger()
        phantom._bar_index = -1  # noqa: SLF001

        print(f"\nstrategy={STRATEGY} ticker={TICKER} bars={len(bars)}")
        print(f"  research run_id set   : {getattr(inst, '_research', {}).get('run_id')!r}")
        print(f"  allowed_tickers      : {inst.allowed_tickers()}")
        print(f"  config['tickers']    : {inst.config.get('tickers')!r}")
        print("  --- identity ---")
        print(f"  plugin.core is core          : {inst.core is core}")
        print(f"  plugin.core.event_bus is bus : {getattr(inst.core, 'event_bus', None) is core.event_bus}")
        print(f"  plugin.host.event_bus is bus : {getattr(inst.host, 'event_bus', None) is core.event_bus}")
        print(f"  phantom.core.event_bus is bus: {phantom.core.event_bus is core.event_bus}")
        print(f"  bus id core={id(core.event_bus)} plugin={id(getattr(inst.core, 'event_bus', None))}")

        actions: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        emitted: list[bool] = []
        last_side_after: list = []
        side_seq: list[str] = []
        strengths: list[float] = []
        intents: list[str] = []
        for b in bars:
            payload = {
                "ticker": TICKER,
                "class_code": "CRYPTO",
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": float(getattr(b, "volume", 0) or 0),
                "run_id": rid,
                "source": "replay:ccxt",
                "ts": b.ts.isoformat() if b.ts is not None else None,
            }
            event = KernelEvent(
                event_type="market.candle", source_id="probe", payload=payload
            )
            # Same order the real path uses: phantom ingests, then the strategy
            # reacts to the same event.
            await phantom._on_candle(event)  # noqa: SLF001
            decision = await inst.handle_candle(event)
            if decision is not None:
                actions[str(decision.action).upper()] += 1
                reasons[str(decision.reason)] += 1
                side_seq.append(str(decision.action).upper()[:1])
                strengths.append(round(float(decision.strength or 0.0), 4))
                intents.append(str(decision.intent or ""))
            from engine.strategy_emit import emit_on_side_change

            emitted.append(await emit_on_side_change(inst, event, decision))
            last_side_after.append(getattr(inst, "_last_side", None))

        print("\n=== decisions from handle_candle ===")
        print(f"  actions  : {dict(actions)}")
        print(f"  top reason: {reasons.most_common(3)}")
        print(f"  emit_on_side_change True={emitted.count(True)} False={emitted.count(False)}")
        print(f"  _signals_sent      : {getattr(inst, '_signals_sent', '?')}")
        print(f"  _last_side (final) : {getattr(inst, '_last_side', '?')!r}")
        print(f"  first 24 sides     : {''.join(side_seq[:24])}")
        if strengths:
            srt = sorted(strengths)
            mid = len(srt) // 2
            print(f"  strength min/med/max: {srt[0]:.4f} / {srt[mid]:.4f} / {srt[-1]:.4f}")
            print(f"  strength max      : {max(strengths):.4f}   "
                  f"(phantom strength_floor = 0.25)")
            print(f"  above floor 0.25  : {sum(1 for s in srt if s >= 0.25)}/{len(srt)}")
        print(f"  intents           : {dict(Counter(intents))}")
        print(f"  last_side trace    : {last_side_after[:14]}")
        print(f"  ALL events seen    : {dict(all_events)}")
        print(f"  signals published: {len(published)}")
        if published:
            for p in published[:6]:
                print(f"    {p.get('action')} {p.get('strength')} {p.get('reason')!r}")
        print(f"  phantom._bar_index  : {phantom._bar_index}")  # noqa: SLF001
        rt = getattr(phantom, "runtime", None)
        if rt is not None:
            keys = list(getattr(rt.candles, "_bars", {}) or {})
            print(f"  phantom book keys   : {keys[:4]}")
            for k in keys[:3]:
                print(f"      {k!r}: {len(rt.candles.bars(k))}")

        await core.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))