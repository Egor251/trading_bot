#!/usr/bin/env python3
"""Точка входа trading_bot v2 (microkernel_core + plugin_manager)."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path, plugin_manager_info  # noqa: E402
from host.logging_setup import get_log, setup_prod_logging  # noqa: E402

ensure_code_base_path()

from host.trading_bot_core import TradingBotCore  # noqa: E402


async def run_smoke(core: TradingBotCore, wait_sec: float = 5.0) -> None:
    """Paper path smoke: candles + optional fills, no REPL deps."""
    candles: list[str] = []
    fills: list[dict] = []

    async def on_candle(event) -> None:
        candles.append(event.payload.get("ticker", "?"))

    async def on_fill(event) -> None:
        fills.append(dict(event.payload))

    await core.event_bus.subscribe("market.candle", on_candle)
    await core.event_bus.subscribe("execution.result", on_fill)
    await asyncio.sleep(wait_sec)
    status = core.status()
    print("smoke status state:", status.get("state"))
    pm = status.get("plugin_manager") or {}
    print(
        f"smoke plugins loaded={pm.get('loaded')} running={pm.get('running')} "
        f"ids={pm.get('ids')}"
    )
    trading = status.get("trading") or {}
    print(
        f"smoke candles={len(candles)} fills={len(fills)} "
        f"halted={trading.get('halted')} candle_syms={trading.get('candles', {}).get('symbols')}"
    )
    if candles:
        print("smoke sample tickers:", candles[:6])
    if fills:
        print("smoke last fill:", fills[-1].get("message") or fills[-1])


async def async_main() -> None:
    parser = argparse.ArgumentParser(
        description="trading_bot — microkernel_core + Code_base/plugin_manager"
    )
    parser.add_argument(
        "--repl",
        action="store_true",
        help="Interactive REPL (default if no other mode)",
    )
    parser.add_argument("--headless", action="store_true", help="Run without REPL")
    parser.add_argument("--smoke", action="store_true", help="Smoke test and exit")
    parser.add_argument("--wait", type=float, default=6.0, help="Smoke wait seconds")
    args = parser.parse_args()

    repl_mode = args.repl or not (args.headless or args.smoke)

    await setup_prod_logging(process="Engine", file_name="engine.log")
    log = get_log("Engine", "Host")

    try:
        from host.secrets_env import load_secrets_into_environ, sqlite_path

        loaded = load_secrets_into_environ()
        if loaded:
            log.info(
                "loaded {} secret key(s) from .env / sqlite ({})",
                len(loaded),
                sqlite_path(),
            )
    except Exception:
        log.exception("secrets sqlite load failed")

    info = plugin_manager_info()
    log.info("plugin_manager file={}", info.get("file"))
    print(f"plugin_manager: {info.get('file')}", flush=True)

    core = TradingBotCore()
    try:
        await core.boot()
        await core.start()
        log.info(
            "engine started headless={} smoke={} repl={}",
            bool(args.headless),
            bool(args.smoke),
            bool(repl_mode),
        )

        if args.smoke:
            await run_smoke(core, args.wait)
        elif repl_mode:
            from host.repl.shell import TradingRepl  # lazy — optional prettytable

            repl = TradingRepl(core)
            await repl.interact()
        else:
            st = core.status()
            trading = st.get("trading") or {}
            candles = trading.get("candles") or {}
            log.info(
                "headless ready state={} mode={} equity={} candle_symbols={} "
                "last_closes={} bar_counts={}",
                st.get("state"),
                trading.get("mode"),
                trading.get("equity"),
                candles.get("symbols"),
                candles.get("last_closes"),
                candles.get("bar_counts"),
            )
            while True:
                await asyncio.sleep(3600)
    except RuntimeError as exc:
        msg = str(exc)
        trace = traceback.format_exc()
        log.error(
            "engine startup failed type={} error={}\n{}",
            type(exc).__name__,
            msg,
            trace,
        )
        print(f"ERROR [{type(exc).__name__}]: {msg}\n{trace}", file=sys.stderr, flush=True)
        raise SystemExit(2) from exc
    except Exception as exc:
        trace = traceback.format_exc()
        log.error(
            "engine crashed type={} error={}\n{}",
            type(exc).__name__,
            exc,
            trace,
        )
        print(
            f"ENGINE CRASH [{type(exc).__name__}]: {exc}\n{trace}",
            file=sys.stderr,
            flush=True,
        )
        raise
    finally:
        try:
            from utils.prod_logging import close_logging

            await close_logging()
        except Exception:
            pass
        try:
            await core.shutdown()
        except Exception as exc:
            log.error(
                "engine shutdown failed type={} error={}\n{}",
                type(exc).__name__,
                exc,
                traceback.format_exc(),
            )


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
