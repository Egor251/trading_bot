"""Metasploit-style REPL поверх TradingBotCore."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from prettytable import PrettyTable

from ui.numfmt import fmt_price, fmt_qty

if TYPE_CHECKING:
    from host.trading_bot_core import TradingBotCore


class TradingRepl:
    """Консоль оператора — UX как legacy UI.py, без eval."""

    ACTIONS = frozenset(
        {
            "use",
            "run",
            "set",
            "help",
            "exit",
            "show",
            "optimize",
            "status",
            "halt",
            "resume",
            "history",
            "metrics",
            "listings",
            "replay",
            "ask",
            "phantom",
        }
    )

    MODULE_TYPES = {
        "strategy": "plugins",
        "driver": "services",
        "service": "services",
    }

    COLUMN_NAMES = {
        "strategy": ["module name", "algorithm", "description"],
        "driver": ["module name", "description"],
        "service": ["module name", "description"],
        "help": ["key", "explanation"],
        "options": ["Parameter", "Value"],
        "storage": ["event_type", "event_id", "source", "when"],
        "plugins": ["id", "kind", "mode", "running", "path"],
        "universe": ["key", "symbol", "class"],
        "history": ["date", "open", "high", "low", "close", "vol"],
    }

    def __init__(self, core: TradingBotCore) -> None:
        self.core = core
        self.prompt = "Command"
        self.active_plugin_id: str | None = None
        self.active_module_type: str | None = None

    async def interact(self) -> None:
        """Interactive command loop (not the ``run`` verb — that is strategy/history)."""
        print("System check")
        await asyncio.sleep(0.3)
        print("Microkernel connected")
        print('Type "help" for commands')

        while True:
            try:
                line = await asyncio.get_event_loop().run_in_executor(
                    None, input, f"{self.prompt}: "
                )
            except (EOFError, KeyboardInterrupt):
                print("\nfinishing program")
                break

            parts = str(line).strip().split()
            if not parts:
                continue

            verb = parts[0].lower()
            args = parts[1:]

            if verb == "exit":
                print("finishing program")
                break
            if verb not in self.ACTIONS:
                print("Invalid command")
                continue

            handler = getattr(self, verb, None)
            if handler is None:
                print("Invalid command")
                continue
            await handler(args)

    async def help(self, _args: list[str]) -> None:
        rows = [
            [
                "show",
                "strategy | plugins | driver | status | storage | universe | history | quik | ai | ensemble | phantom",
            ],
            ["use", "use strategy <id> | use driver <mock|quik|history>"],
            ["run", "run TQBR SBER (strategy) | run history SBER [start]"],
            ["ask", "ask <text> — AI Analyst (extension, mock by default)"],
            ["history", "history SBER [TQBR] [yahoo|moex] [limit] [start]"],
            ["metrics", "refresh catalog metrics via history (network)"],
            ["listings", "refresh MOEX ISS board listings into catalog"],
            ["replay", "replay SBER [limit] [yahoo|moex] — history as market.candle"],
            ["phantom", "phantom SBER [limit] — historical multi-variant shadow grid"],
            ["halt", "kill switch: halt [reason]"],
            ["resume", "clear kill switch"],
            ["status", "kernel status summary"],
            ["optimize", "optimize <TICKER> [CLASS] [bars] — WF + dense param search (OOS)"],
            ["help", "this help"],
            ["exit", "shutdown"],
        ]
        self._print_table(self.COLUMN_NAMES["help"], rows)

    async def show(self, args: list[str]) -> None:
        if not args:
            print(
                "Usage: show strategy|plugins|driver|options|status|storage|universe|history|quik|ai|ensemble|phantom"
            )
            return

        kind = args[0].lower()
        if kind == "options":
            await self._show_options()
        elif kind == "status":
            await self.status([])
        elif kind == "storage":
            await self._show_storage(args[1:])
        elif kind == "universe":
            await self._show_universe()
        elif kind == "history":
            await self._show_history()
        elif kind == "quik":
            await self._show_quik()
        elif kind == "ai":
            await self._show_ai()
        elif kind == "ensemble":
            await self._show_ensemble()
        elif kind == "phantom":
            await self._show_phantom()
        elif kind == "plugins":
            await self._show_plugins(args[1:])
        elif kind in {"strategy", "driver", "service"}:
            await self._show_modules(kind if kind != "service" else "driver")
        else:
            print("Unknown show target")

    async def _show_plugins(self, args: list[str]) -> None:
        """Extensions / actuators — not strategies (those are ``show strategy``)."""
        from engine.strategy_plugin import StrategyPlugin

        kind_filter = args[0] if args else None
        rows_data = []
        for r in self.core.list_plugins(kind=kind_filter):
            inst = self.core.plugin_manager.get(r["id"])
            if isinstance(inst, StrategyPlugin):
                continue
            rows_data.append(r)
        if not rows_data:
            print("(no extension plugins; strategies → show strategy)")
            return
        rows = [
            [
                r.get("id", ""),
                r.get("kind", ""),
                r.get("run_mode", ""),
                "yes" if r.get("running") else "no",
                str(r.get("path", ""))[-40:],
            ]
            for r in rows_data
        ]
        self._print_table(self.COLUMN_NAMES["plugins"], rows)

    async def _show_storage(self, args: list[str]) -> None:
        svc = self.core.service_registry.get("storage_service")
        if svc is None or not hasattr(svc, "query_journal"):
            print("storage_service not available")
            return
        limit = 15
        event_types: list[str] | None = None
        if args:
            if args[0].isdigit():
                limit = int(args[0])
            else:
                event_types = [args[0]]
                if len(args) > 1 and args[1].isdigit():
                    limit = int(args[1])
        rows_data = await svc.query_journal(event_types=event_types, limit=limit)
        if not rows_data:
            print("(empty journal)")
            return
        table_rows = []
        for r in rows_data:
            table_rows.append(
                [
                    r.get("event_type", ""),
                    str(r.get("event_id", ""))[:12],
                    r.get("source_id", ""),
                    str(r.get("timestamp", ""))[:19],
                ]
            )
        self._print_table(self.COLUMN_NAMES["storage"], table_rows)
        kit = getattr(self.core, "storage", None)
        if kit is not None:
            stats = kit.sink.stats()
            print(
                f"sink received={stats.get('received')} persisted={stats.get('persisted')} "
                f"pending={stats.get('pending')}"
            )

    async def _show_universe(self) -> None:
        svc = self.core.service_registry.get("universe_service")
        snap = None
        if svc is not None:
            snap = getattr(svc, "last_snapshot", None)
        if snap is None:
            mgr = getattr(self.core, "universe", None)
            if mgr is not None and hasattr(mgr, "last"):
                snap = mgr.last
        if snap is None:
            print("universe not ready")
            return
        print(
            f"mode={getattr(snap, 'mode', '?')} profile={getattr(snap, 'profile', '?')} "
            f"active={len(getattr(snap, 'active', []) or [])}"
        )
        rows = []
        for inst in list(getattr(snap, "active", []) or [])[:30]:
            if isinstance(inst, dict):
                rows.append(
                    [
                        inst.get("key", ""),
                        inst.get("symbol") or inst.get("ticker", ""),
                        inst.get("class_code", ""),
                    ]
                )
            else:
                rows.append(
                    [
                        getattr(inst, "key", ""),
                        getattr(inst, "symbol", ""),
                        getattr(inst, "class_code", ""),
                    ]
                )
        if rows:
            self._print_table(self.COLUMN_NAMES["universe"], rows)

    async def _show_modules(self, kind: str) -> None:
        if kind == "strategy":
            from engine.strategy_plugin import StrategyPlugin

            rows = []
            for plugin_id, instance in self.core.plugin_manager.plugins.items():
                if not isinstance(instance, StrategyPlugin):
                    continue
                manifest = self.core.plugin_manager._records[plugin_id].manifest  # noqa: SLF001
                desc_parts = instance.describe() if hasattr(instance, "describe") else []
                algo = desc_parts[0] if desc_parts else manifest.kind.value
                desc = desc_parts[1] if len(desc_parts) > 1 else manifest.description
                rows.append([plugin_id, algo, desc])
            if not rows:
                print("(no strategies loaded)")
                return
            self._print_table(self.COLUMN_NAMES["strategy"], rows)
        else:
            rows = []
            for sid, svc in self.core.service_registry.all_services().items():
                note = svc.config.get("description", svc.__class__.__doc__ or "")
                note = (note or "").strip().split("\n")[0]
                rows.append([sid, note])
            self._print_table(self.COLUMN_NAMES["driver"], rows)

    async def _show_options(self) -> None:
        if not self.active_plugin_id:
            print("Unable to show options, no module was selected")
            return

        cfg = self.config_for_plugin(self.active_plugin_id)
        rows = [[key, str(value)] for key, value in sorted(cfg.items())]
        if not rows:
            print("There no options to set")
            return
        print(f"Parameters for {self.active_plugin_id}")
        self._print_table(self.COLUMN_NAMES["options"], rows)

    def config_for_plugin(self, plugin_id: str) -> dict[str, Any]:
        instance = self.core.plugin_manager.get(plugin_id)
        if instance is not None and instance.config:
            return dict(instance.config)
        return {}

    async def use(self, args: list[str]) -> None:
        if len(args) < 2:
            if len(args) == 1:
                print("You missed last argument")
                print(f'Running "show {args[0]}" instead')
                await self.show(args)
            else:
                print("Usage: use strategy <name> | use driver <name>")
            return

        kind, name = args[0].lower(), args[1]
        if kind == "strategy":
            if self.core.plugin_manager.get(name) is None:
                print("Unknown module")
                return
            self.active_plugin_id = name
            self.active_module_type = "strategy"
            self.prompt = f"strategies/{name}"
            from engine.strategy_plugin import StrategyPlugin

            inst = self.core.plugin_manager.get(name)
            if inst is not None and not isinstance(inst, StrategyPlugin):
                self.prompt = f"plugins/{name}"
        elif kind in {"driver", "service"}:
            service_id = self._resolve_service_id(name)
            if service_id is None:
                print("Unknown module")
                return
            self.active_plugin_id = None
            self.active_module_type = "driver"
            self.prompt = f"services/{service_id}"
        else:
            print("Unknown module type")

    def _resolve_service_id(self, name: str) -> str | None:
        services = self.core.service_registry.all_services()
        candidates = [
            name,
            f"{name}_service",
            f"{name}_market_service",
            "mock_market_service" if name in {"mock", "test", "TestDriver"} else None,
            "quik_market_service" if name.lower() == "quik" else None,
            "historical_data_service"
            if name.lower() in {"history", "historical", "yahoo", "moex"}
            else None,
        ]
        for cid in candidates:
            if cid and cid in services:
                return cid
        return None

    async def history(self, args: list[str]) -> None:
        """Fetch OHLCV via historical_data_service and print a short summary."""
        svc = self.core.service_registry.get("historical_data_service")
        if svc is None or not hasattr(svc, "fetch"):
            print("historical_data_service not available")
            return
        if not args:
            print("Usage: history <TICKER> [CLASS] [yahoo|moex] [limit] [YYYY-MM-DD]")
            return

        ticker = args[0].upper()
        class_code = "TQBR"
        provider: str | None = None
        limit: int | None = None
        start: str | None = None
        for token in args[1:]:
            low = token.lower()
            if low in {"yahoo", "moex"}:
                provider = low
            elif token.isdigit():
                limit = int(token)
            elif len(token) == 10 and token[4] == "-":
                start = token
            elif token.isalpha() and token.isupper() and len(token) <= 8:
                class_code = token
            else:
                class_code = token

        try:
            bars = await svc.fetch(
                ticker,
                class_code=class_code,
                provider=provider,
                limit=limit,
                start=start,
            )
        except Exception as exc:  # noqa: BLE001 — operator-facing
            print(f"history fetch failed: {exc}")
            return

        if not bars:
            print(f"(no bars for {class_code}:{ticker})")
            return

        summary = svc.summarize(bars) if hasattr(svc, "summarize") else {"bars": len(bars)}
        print(
            f"{class_code}:{ticker} bars={summary.get('bars')} "
            f"last={summary.get('last_date')} close={summary.get('last_close')} "
            f"ret={summary.get('return_pct')} atr%={summary.get('atr_pct')}"
        )
        if hasattr(svc, "publish_bars") and bool(
            getattr(svc, "config", {}).get("publish_events", True)
        ):
            await svc.publish_bars(
                bars, class_code=class_code, provider=provider, force=True
            )

        rows = []
        for b in bars[-12:]:
            rows.append(
                [
                    b.ts.strftime("%Y-%m-%d"),
                    f"{fmt_price(b.open)}",
                    f"{fmt_price(b.high)}",
                    f"{fmt_price(b.low)}",
                    f"{fmt_price(b.close)}",
                    f"{fmt_qty(b.volume)}",
                ]
            )
        self._print_table(self.COLUMN_NAMES["history"], rows)

    async def _show_history(self) -> None:
        svc = self.core.service_registry.get("historical_data_service")
        if svc is None:
            print("historical_data_service not available")
            return
        st = await svc.get_status() if hasattr(svc, "get_status") else {}
        last = st.get("last_fetch") or {}
        print(
            f"yahoo={st.get('yahoo')} moex={st.get('moex')} "
            f"default={st.get('default_provider')} cache={st.get('cache_entries')} "
            f"last={last}"
        )

    async def _show_quik(self) -> None:
        svc = self.core.service_registry.get("quik_market_service")
        if svc is None:
            print("quik_market_service not available")
            return
        st = await svc.get_status() if hasattr(svc, "get_status") else {}
        runtime = getattr(self.core, "trading", None)
        print(
            f"enabled={svc.config.get('enabled')} transport={st.get('transport')} "
            f"connected={st.get('connected')} broker={st.get('broker')} "
            f"error={st.get('last_error') or st.get('error')}"
        )
        print(
            f"runtime.mode={getattr(runtime, 'mode', None)} "
            f"runtime.broker={type(getattr(runtime, 'broker', None)).__name__ if getattr(runtime, 'broker', None) else None}"
        )
        print(st.get("note") or "")

    async def _show_ai(self) -> None:
        plugin = self.core.plugin_manager.get("ai_analyst")
        if plugin is None:
            print("ai_analyst plugin not loaded")
            return
        cfg = getattr(plugin, "config", {}) or {}
        tools = plugin.tool_names() if hasattr(plugin, "tool_names") else []
        last = getattr(plugin, "_last_ask", {}) or {}
        print(
            f"provider={cfg.get('default_provider')} ready={plugin.service is not None} "
            f"tools={len(tools)} err={getattr(plugin, '_last_error', None)}"
        )
        if tools:
            print("tools:", ", ".join(tools[:20]))
        if last:
            print(f"last_ask: {last}")

    async def _show_ensemble(self) -> None:
        svc = self.core.service_registry.get("ensemble_service")
        if svc is None:
            print("ensemble_service not available")
            return
        st = await svc.get_status()
        print(
            f"enabled={st.get('ensemble_enabled')} decisions={st.get('decisions')} "
            f"weights={st.get('weights')}"
        )
        last = st.get("last_decision")
        if last:
            print(
                f"last: {last.get('action')} {last.get('ticker')} "
                f"strength={last.get('strength')} contributors={last.get('contributors')}"
            )

    async def _show_phantom(self) -> None:
        svc = self.core.service_registry.get("phantom_service")
        if svc is None:
            print("phantom_service not available")
            return
        st = await svc.get_status()
        m = st.get("metrics") or {}
        print(
            f"fills={st.get('fills')} capturing={st.get('capturing')} "
            f"pnl={m.get('pnl')} dd={m.get('max_drawdown')} trades={m.get('trades')}"
        )
        grid = st.get("last_grid") or []
        if grid:
            rows = [
                [
                    str(r.get("variant")),
                    str(r.get("pnl")),
                    str(r.get("max_drawdown")),
                    str(r.get("trades")),
                    str(r.get("bars")),
                ]
                for r in grid
            ]
            self._print_table(
                ["variant", "pnl", "max_dd", "trades", "bars"],
                rows,
            )

    async def phantom(self, args: list[str]) -> None:
        """Run historical multi-variant phantom grid on replay."""
        svc = self.core.service_registry.get("phantom_service")
        if svc is None or not hasattr(svc, "run_grid"):
            print("phantom_service not available")
            return
        if not args:
            print("Usage: phantom <TICKER> [CLASS] [limit] [yahoo|moex]")
            return
        ticker = args[0].upper()
        class_code = "TQBR"
        limit = 60
        provider = None
        for a in args[1:]:
            al = a.lower()
            if al in {"yahoo", "moex"}:
                provider = al
            elif a.isdigit():
                limit = int(a)
            elif a.isalpha() and a.isupper() and len(a) <= 6:
                class_code = a
        try:
            results = await svc.run_grid(
                ticker,
                class_code=class_code,
                provider=provider,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"phantom failed: {exc}")
            return
        rows = [
            [
                str(r.get("variant")),
                f"{r.get('pnl'):.2f}" if r.get("pnl") is not None else "",
                f"{r.get('max_drawdown'):.4f}" if r.get("max_drawdown") is not None else "",
                str(r.get("trades")),
                str(r.get("bars")),
            ]
            for r in results
        ]
        self._print_table(["variant", "pnl", "max_dd", "trades", "bars"], rows)

    async def ask(self, args: list[str]) -> None:
        """Ask the AI Analyst extension (not a strategy)."""
        if not args:
            print("Usage: ask <message…>")
            return
        plugin = self.core.plugin_manager.get("ai_analyst")
        if plugin is None or not hasattr(plugin, "ask"):
            print("ai_analyst plugin not available")
            return
        message = " ".join(args)
        use_tools = True
        if message.startswith("--no-tools "):
            use_tools = False
            message = message[len("--no-tools ") :].strip()
        try:
            result = await plugin.ask(message, use_tools=use_tools)
        except Exception as exc:  # noqa: BLE001
            print(f"ask failed: {exc}")
            return
        if not getattr(result, "success", False):
            print(f"AI error: {getattr(result, 'error', result)}")
            return
        content = getattr(result, "content", "") or ""
        print(
            f"[{getattr(result, 'provider_id', '?')}/{getattr(result, 'model', '?')}] "
            f"{content}"
        )

    async def metrics(self, args: list[str]) -> None:
        """Refresh catalog metrics from historical_data_service, then reselect."""
        uni = self.core.service_registry.get("universe_service")
        if uni is None or not hasattr(uni, "refresh_catalog_metrics"):
            print("universe_service not available")
            return
        if args and args[0].lower() in {"no", "skip"}:
            print("usage: metrics  # force refresh + reselect")
            return
        stats = await uni.refresh_catalog_metrics(force=True)
        print(f"metrics: {stats}")
        snap = await uni.reselect_now(reason="metrics")
        if snap is not None:
            print(
                f"reselect mode={snap.mode} active={len(snap.active)} "
                f"keys={snap.active_keys()[:8]}"
            )

    async def listings(self, args: list[str]) -> None:
        """Refresh MOEX ISS listings into catalog, then reselect."""
        uni = self.core.service_registry.get("universe_service")
        if uni is None or not hasattr(uni, "refresh_listings"):
            print("universe_service not available")
            return
        boards = [a.upper() for a in args] if args else None
        if boards:
            uni.config["refresh_listings_boards"] = boards
        stats = await uni.refresh_listings(force=True)
        print(f"listings: {stats}")
        snap = await uni.reselect_now(reason="listings")
        if snap is not None:
            print(
                f"catalog={stats.get('catalog_size')} mode={snap.mode} "
                f"active={len(snap.active)}"
            )

    async def replay(self, args: list[str]) -> None:
        """Replay OHLCV as live ``market.candle`` stream (backtest feed)."""
        svc = self.core.service_registry.get("historical_data_service")
        if svc is None or not hasattr(svc, "replay"):
            print("historical_data_service not available")
            return
        if not args:
            print("Usage: replay <TICKER> [CLASS] [yahoo|moex] [limit] [YYYY-MM-DD]")
            return

        ticker = args[0].upper()
        class_code = "TQBR"
        provider: str | None = None
        limit: int | None = None
        start: str | None = None
        for token in args[1:]:
            low = token.lower()
            if low in {"yahoo", "moex"}:
                provider = low
            elif token.isdigit():
                limit = int(token)
            elif len(token) == 10 and token[4] == "-":
                start = token
            elif token.isalpha() and token.isupper() and len(token) <= 8:
                class_code = token
            else:
                class_code = token

        strategy_id = self.active_plugin_id or ""
        try:
            result = await svc.replay(
                ticker,
                class_code=class_code,
                provider=provider,
                limit=limit,
                start=start,
                strategy_id=strategy_id,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"replay failed: {exc}")
            return
        summary = result.get("summary") or {}
        print(
            f"replay run_id={result.get('run_id')} bars={result.get('bars')} "
            f"last={summary.get('last_date')} close={summary.get('last_close')} "
            f"ret={summary.get('return_pct')}"
        )

    async def optimize(self, args: list[str]) -> None:
        """Walk-forward with dense param search (OOS selection)."""
        svc = self.core.service_registry.get("phantom_service")
        if svc is None or not hasattr(svc, "run_walk_forward"):
            print("phantom_service.run_walk_forward not available")
            return
        if not args:
            print(
                "Usage: optimize <TICKER> [CLASS|CRYPTO] [bars] "
                "[strategy_id] — dense WF search (variant_mode=optimize)"
            )
            return
        ticker = args[0].upper()
        class_code = "CRYPTO" if "/" in ticker or ticker.endswith("USDT") else "TQBR"
        bars = 500
        strategy_id = self.active_plugin_id or "ma_cross_example"
        for a in args[1:]:
            al = a.lower()
            if a.isdigit():
                bars = int(a)
            elif al in {"yahoo", "moex", "ccxt", "runtime", "auto"}:
                continue
            elif a.isupper() and len(a) <= 8 and a.isalpha():
                class_code = a
            elif "_" in a or a.endswith("_example") or "reversion" in al or "trend" in al:
                strategy_id = a
        try:
            result = await svc.run_walk_forward(
                ticker,
                class_code=class_code,
                limit=bars,
                strategy_id=strategy_id,
                variant_mode="optimize",
                max_variants=36,
                feed="history",
                timeframe="1d",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"optimize failed: {exc}")
            return
        oos = result.get("oos") or {}
        prov = result.get("provenance") or {}
        print(
            f"optimize strategy={strategy_id} ticker={ticker} "
            f"variant_mode={prov.get('variant_mode')} "
            f"variants={prov.get('variants_count')} "
            f"tf={prov.get('timeframe')} bar_dt={prov.get('bar_interval_sec')} "
            f"folds={len(result.get('folds') or [])} "
            f"mean_oos={oos.get('mean_oos_pnl')} "
            f"CI=[{oos.get('ci_low')}, {oos.get('ci_high')}] "
            f"chosen={result.get('chosen')}"
        )

    async def run(self, args: list[str]) -> None:
        if args and args[0].lower() == "history":
            await self.history(args[1:])
            return
        if args and args[0].lower() == "replay":
            await self.replay(args[1:])
            return

        if self.active_module_type != "strategy" or not self.active_plugin_id:
            print("Select strategy first: use strategy test_strategy")
            print("Or: history SBER | replay SBER | run history SBER")
            return

        plugin = self.core.plugin_manager.get(self.active_plugin_id)
        if plugin is None:
            print("Unknown module")
            return

        class_code = args[0] if len(args) > 0 else "TQBR"
        ticker = args[1] if len(args) > 1 else "SBER"

        if hasattr(plugin, "run_manual"):
            result = await plugin.run_manual(class_code, ticker)
            print(result)
        else:
            print("Plugin does not support run")

    async def set(self, args: list[str]) -> None:
        if not self.active_plugin_id:
            print("No strategy selected")
            return
        if len(args) < 2:
            print("Usage: set <param> <value>")
            return
        plugin = self.core.plugin_manager.get(self.active_plugin_id)
        if plugin is None:
            return
        key, value = args[0], " ".join(args[1:])
        plugin.config[key] = value
        print(f"Set {key}={value} (runtime only)")

    async def status(self, _args: list[str]) -> None:
        st = self.core.status()
        print(
            f"state={st.get('state')} plugins={st.get('plugins')} "
            f"rules={st.get('rules_loaded')} halted={st.get('halted')}"
        )
        print(f"event_bus subscriptions: {st.get('event_bus')}")

    async def halt(self, args: list[str]) -> None:
        reason = " ".join(args) if args else "repl"
        await self.core.halt(reason)
        print(f"halted: {reason}")

    async def resume(self, _args: list[str]) -> None:
        await self.core.resume()
        print("resumed")

    @staticmethod
    def _print_table(columns: list[str], rows: list[list[Any]]) -> None:
        table = PrettyTable(columns)
        table.add_rows(rows)
        table.align = "l"
        table.align[columns[0]] = "c"
        table.padding_width = 3
        print(table)
