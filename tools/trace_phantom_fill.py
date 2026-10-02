"""Clean phantom measurement — no monkeypatching of any kind.

Earlier attempts wrapped `inst.candle_allowed` / `inst.ticker_allowed` and got
nonsense: assigning a wrapper onto the instance and then calling the same method
recursed forever, and `handle_candle` swallows exceptions, so every measurement
reported "no decisions" when the real cause was the probe itself.

This version touches nothing. It subscribes a counter to the bus (safe) and then
reads plugin and phantom state after the run.
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
TIMEFRAME = sys.argv[3] if len(sys.argv) > 3 else "1h"
LIMIT = int(sys.argv[4]) if len(sys.argv) > 4 else 1000


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
        await core.start()
        if not phantom.is_running:
            await phantom.start()

        seen: Counter[str] = Counter()
        samples: list = []

        async def _tally(event):
            seen[event.event_type] += 1
            if event.event_type == "market.candle" and len(samples) < 2:
                samples.append(event)

        for _et in ("market.candle", "strategy.signal", "ensemble.decision"):
            await core.event_bus.subscribe(_et, _tally)

        inst = core.plugin_manager.get(STRATEGY)
        inst.config["enabled"] = True
        inst.config["observe_only"] = False
        inst.config["tickers"] = [TICKER]
        inst.config["class_code"] = "CRYPTO"
        if hasattr(inst, "reset_state"):
            inst.reset_state()
        phantom.config["walk_forward"] = {
            **dict(phantom.config.get("walk_forward") or {}),
            "timeframe": TIMEFRAME,
        }

        stats = {"on_signal": 0, "dispatch": 0, "shadow": 0}
        reasons: Counter[str] = Counter()
        # NOTE: do not wrap `_on_signal`/`_shadow_fill`/`_on_candle` on the
        # instance. The bus captured those *original* bound methods at `start()`,
        # so an instance wrapper is never invoked — and calling the wrapper from
        # inside itself recursed once, which the swallowing handlers hid from
        # view. `evaluate_at` is safe: `handle_candle` resolves it on `self` at
        # call time.
        ev: Counter[str] = Counter()
        reasons_seen: Counter[str] = Counter()
        roc_vals: list[float] = []
        closes_seen: list[float] = []
        _orig_eval = type(inst).evaluate_at

        def _eval(self, series, index, *, config=None):
            d = _orig_eval(self, series, index, config=config)
            ev["calls"] += 1
            if len(closes_seen) < 1:
                closes_seen.extend(float(x) for x in (series.get("close") or [])[:5])
            if d is None:
                ev["None"] += 1
            else:
                ev[str(d.action).upper()] += 1
                reasons_seen[str(d.reason)] += 1
                m = getattr(d, "metrics", None) or {}
                if m.get("roc") is not None:
                    roc_vals.append(float(m["roc"]))
            return d

        inst.evaluate_at = _eval.__get__(inst, type(inst))

        print(f"\n{TICKER} {TIMEFRAME}  strategy={STRATEGY}")
        print(f"  phantom is_running={getattr(phantom, 'is_running', '?')} "
              f"capturing={phantom._capturing} runtime={phantom.runtime is not None}")  # noqa: SLF001
        try:
            print(f"  bus subs before    {core.event_bus.subscription_stats()}")
        except Exception as exc:  # noqa: BLE001
            print(f"  bus subs before    unavailable: {exc}")
        print(f"  plugin is_running={getattr(inst, 'is_running', '?')} "
              f"handlers={[e[0] for e in getattr(inst, '_event_handlers', [])]}")
        print(f"  phantom fill_timing={phantom.config.get('fill_timing')!r} "
              f"capture_source={phantom.config.get('capture_source')!r}")

        out = await phantom.run_grid(
            TICKER, class_code="CRYPTO", feed="history", provider="ccxt",
            limit=LIMIT, strategy_id=STRATEGY, variants=[{}],
        )
        print("\n=== immediately after run_grid returned ===")
        print(f"  bus events          {dict(seen)}")
        print(f"  bus queue size      {getattr(core.event_bus, '_queue', None) and core.event_bus._queue.qsize()}")
        print(f"  bus dropped         {getattr(core.event_bus, '_dropped_events', '?')}")

        # The bus is a queued worker: dispatch happens after publish returns.
        # Give it time to deliver everything run_grid queued.
        await asyncio.sleep(3.0)
        print("\n=== after letting the bus drain (3s) ===")
        print(f"  bus events          {dict(seen)}")
        print(f"  bus queue size      {getattr(core.event_bus, '_queue', None) and core.event_bus._queue.qsize()}")
        print(f"  bus dropped         {getattr(core.event_bus, '_dropped_events', '?')}")
        print(f"  plugin._signals_sent {getattr(inst, '_signals_sent', '?')}")

        print("\n=== observed on the bus ===")
        for k, v in seen.most_common():
            print(f"  {k:26} {v}")

        print("\n=== plugin + phantom state ===")
        print(f"  plugin._signals_sent   {getattr(inst, '_signals_sent', '?')}")
        print(f"  evaluate_at           calls={ev['calls']} "
              f"None={ev['None']} actions={ {k: v for k, v in ev.items() if k not in ('calls', 'None')} }")
        print(f"  top reasons           {reasons_seen.most_common(4)}")
        print(f"  first closes seen     {[round(c, 2) for c in closes_seen[:5]]}")
        if roc_vals:
            lo, hi = min(roc_vals), max(roc_vals)
            print(f"  roc range             min={lo:.6f} max={hi:.6f} "
                  f"|r|>=0.02: {sum(1 for v in roc_vals if abs(v) >= 0.02)}/{len(roc_vals)}")
            # Same indicator, same entry, on the series the fetcher returned —
            # if this disagrees with the live path, the phantom's candle book is
            # not the series the research run claims to measure.
            from engine.indicators import roc as _roc

            hist = core.service_registry.get("historical_data_service")
            fetched = [
                float(b.close)
                for b in await hist.fetch(
                    TICKER,
                    class_code="CRYPTO",
                    provider="ccxt",
                    timeframe=TIMEFRAME,
                    limit=LIMIT,
                )
            ]
            ref = [v for v in (_roc(fetched[: i + 1], 12)[-1] for i in range(len(fetched)))
                   if v is not None]
            print(f"  roc on fetched series min={min(ref):.6f} max={max(ref):.6f} "
                  f"|r|>=0.02: {sum(1 for v in ref if abs(v) >= 0.02)}/{len(ref)}")
            print(f"  fetched closes[0:3]  {[round(c, 2) for c in fetched[:3]]}")
            if samples:
                s0 = dict(samples[0].payload or {})
                print(f"  replay published 1st  close={s0.get('close')} ts={s0.get('ts')} "
                      f"date={s0.get('date')} time={s0.get('time')}")
                if len(samples) > 1:
                    s1 = dict(samples[1].payload or {})
                    print(f"  replay published 2nd  close={s1.get('close')} ts={s1.get('ts')}")
            print(f"  fetched ts range      {fetched[0]:.2f} .. {fetched[-1]:.2f}")
        try:
            print(f"  bus subs after     {core.event_bus.subscription_stats()}")
        except Exception as exc:  # noqa: BLE001
            print(f"  bus subs after     unavailable: {exc}")
        print(f"  phantom._on_signal    {stats['on_signal']}")
        print(f"  _dispatch called      {stats['dispatch']}")
        print(f"  _shadow_fill called   {stats['shadow']}")
        print(f"  shadow results        {dict(list(reasons.items())[:8])}")
        print(f"  plugin._bars_seen      {getattr(inst, '_bars_seen', '?')}")
        print(f"  plugin._last_error     {getattr(inst, '_last_error', None)!r}")
        print(f"  phantom._fills         {len(phantom._fills)}")
        print(f"  phantom._pending_fills {len(phantom._pending_fills)}")
        print(f"  phantom._bar_index     {phantom._bar_index}  "
              f"(-1 == _on_candle never ran)")
        rt = getattr(phantom, "runtime", None)
        if rt is not None:
            store = getattr(rt, "candles", None)
            if store is not None:
                keys = list(getattr(store, "_bars", {}) or {})
                print(f"  phantom candle keys    {keys[:8]}")
                for k in keys[:4]:
                    print(f"      {k!r}: {len(store.bars(k))} bars")
        print(f"  core.phantom is runtime: {getattr(phantom.core, 'phantom', None) is rt}")
        by_status = Counter(str(f.get("status")) for f in phantom._fills)
        print(f"  fill statuses          {dict(by_status)}")
        by_reason = Counter(str(f.get("reason")) for f in phantom._fills) 
        print(f"  fill reasons           {dict(list(by_reason.items())[:6])}")

        row = out[-1] if out else {}
        print("\n=== run_grid row ===")
        for k in sorted(row):
            v = str(row[k])
            if len(v) > 90:
                v = v[:90] + "..."
            print(f"  {k:18} {v}")

        print("\n=== a real market.candle payload from the replay ===")
        for ev in samples:
            p = dict(ev.payload or {})
            print(f"  keys        : {sorted(p)}")
            print(f"  run_id      : {p.get('run_id')!r}")
            print(f"  ticker      : {p.get('ticker')!r}")
            print(f"  source      : {p.get('source')!r}")
            print(f"  closed      : {p.get('closed')!r}")
            break

        await core.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))