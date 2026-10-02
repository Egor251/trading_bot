"""E2E: fake CCXT → candle → ma_cross → Consilium → RuleEngine → paper fill."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from host.trading_bot_core import TradingBotCore  # noqa: E402
from microkernel_core import KernelEvent  # noqa: E402
from tests.e2e_sandbox import sandbox_core  # noqa: E402


class _FakeCcxtExchange:
    """Deterministic rising OHLCV — MA cross emits BUY after seed."""

    def __init__(self, *, n: int = 60, start: float = 100.0) -> None:
        base_ms = 1_700_000_000_000
        self._rows: list[list[float]] = []
        for i in range(n):
            close = start + float(i)
            self._rows.append(
                [
                    base_ms + i * 60_000,
                    close - 0.5,
                    close + 0.5,
                    close - 1.0,
                    close,
                    25.0 + i,
                ]
            )

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int | None = None,
        since: int | None = None,  # noqa: ARG002
    ) -> list[list[float]]:
        assert "BTC" in str(symbol).upper().replace("/", "")
        assert timeframe
        rows = list(self._rows)
        if limit is not None:
            rows = rows[-int(limit) :]
        return rows

    async def close(self) -> None:
        return None


async def _run(config_path) -> None:
    core = TradingBotCore(config_path)
    await core.boot()

    assert core.rule_engine is not None, "expected cb_rule_engine attached"

    for service_id in (
        "mock_market_service",
        "quik_market_service",
        "alpaca_market_service",
        "historical_data_service",
        "ui_bridge_service",
        "phantom_service",
    ):
        service = core.service_registry.get(service_id)
        if service is not None:
            service.config["enabled"] = False
            service.config["auto_start"] = False

    ens = core.service_registry.get("ensemble_service")
    assert ens is not None
    ens.config["enabled"] = True
    ens.config["auto_start"] = True
    ens.config["route_execution"] = False
    ens.config["debounce_sec"] = 0.05
    # Pin live roster: YAML may park ma_cross in observe with macd live.
    ens_lineup = {
        "strategies": ["ma_cross_example"],
        "observe": [],
        "weight_mode": "equal",
        "enabled": True,
    }
    ens._ensemble_cfg = dict(ens_lineup)  # noqa: SLF001
    raw_cfg = getattr(core.config, "raw", None)
    if isinstance(raw_cfg, dict):
        trading = dict(raw_cfg.get("trading") or {})
        trading["ensemble"] = dict(ens_lineup)
        raw_cfg["trading"] = trading
    if ens.manager is not None:
        ens.manager.set_equal_weights(["ma_cross_example"])

    ccxt = core.service_registry.get("ccxt_market_service")
    assert ccxt is not None, "ccxt_market_service missing"
    ccxt.config["enabled"] = True
    ccxt.config["auto_start"] = True
    ccxt.config["follow_universe"] = False
    ccxt.config["use_watch"] = False
    ccxt.config["interval_sec"] = 0.2
    ccxt.config["seed_bars"] = 60
    ccxt.config["class_code"] = "CRYPTO"
    ccxt.config["tickers"] = ["BTC/USDT"]
    ccxt.config["exchange"] = "binance"
    # Match MarketAdapterServiceBase.start() re-read (post-boot mutations).
    ccxt._follow_universe = False  # noqa: SLF001
    ccxt._default_class_code = "CRYPTO"  # noqa: SLF001
    ccxt._active = [("CRYPTO", "BTC/USDT")]  # noqa: SLF001

    # Inject fake exchange before start (connect must not overwrite).
    assert ccxt._ccxt is not None  # noqa: SLF001
    ccxt._ccxt._exchange = _FakeCcxtExchange()  # noqa: SLF001
    ccxt._ccxt._watch_supported = False  # noqa: SLF001

    ma = core.plugin_manager.get("ma_cross_example")
    assert ma is not None
    ma.config["class_code"] = "CRYPTO"
    ma.config["tickers"] = ["BTC/USDT"]
    ma.config["fast"] = 3
    ma.config["slow"] = 8
    ma.config["quantity"] = 0.01
    ma.config["observe_only"] = False
    ma.config["enabled"] = True

    # Silence other crypto strategies so consilium sees only ma_cross BUY.
    for pid, plugin in list(getattr(core.plugin_manager, "_instances", {}).items()):
        if pid in {"ma_cross_example", "ai_analyst"}:
            continue
        if hasattr(plugin, "config"):
            plugin.config["observe_only"] = True
            plugin.config["tickers"] = ["ETH/USDT"]

    mr = core.plugin_manager.get("mean_reversion_example")
    if mr is not None:
        # Keep off BTC so it cannot cancel the consilium BUY.
        mr.config["tickers"] = ["ETH/USDT"]

    assert core.trading is not None
    core.trading.mode = "paper"
    core.trading.orders.mode = "paper"

    fills: list[dict] = []
    decisions: list[dict] = []
    candles: list[str] = []
    rules: list[str] = []

    async def on_fill(event: KernelEvent) -> None:
        fills.append(dict(event.payload or {}))

    async def on_decision(event: KernelEvent) -> None:
        decisions.append(dict(event.payload or {}))

    async def on_candle(event: KernelEvent) -> None:
        candles.append(str((event.payload or {}).get("ticker") or ""))

    async def on_rule(event: KernelEvent) -> None:
        rules.append(str((event.payload or {}).get("rule_id") or ""))

    await core.event_bus.subscribe("execution.result", on_fill)
    await core.event_bus.subscribe("ensemble.decision", on_decision)
    await core.event_bus.subscribe("market.candle", on_candle)
    await core.event_bus.subscribe("rule.triggered", on_rule)

    await core.start()

    if not ccxt.is_running:
        await ccxt.start()

    deadline = asyncio.get_event_loop().time() + 12.0
    while asyncio.get_event_loop().time() < deadline:
        if any(f.get("success") for f in fills):
            break
        await asyncio.sleep(0.1)

    assert candles, "expected market.candle from fake CCXT seed/poll"
    assert decisions, f"expected ensemble.decision, got none; candles={len(candles)}"
    assert any(f.get("success") for f in fills), f"expected paper fill, got {fills!r}"
    ok_fills = [f for f in fills if f.get("success")]
    assert any(f.get("mode") == "paper" for f in ok_fills)
    assert any(f.get("via") == "rule_engine" for f in ok_fills), (
        f"expected via=rule_engine (not route_execution), got {ok_fills!r}"
    )
    assert "paper_consilium_on_decision" in rules or rules, (
        f"expected rule.triggered, got {rules!r}"
    )

    positions = core.trading.orders.positions
    assert "CRYPTO:BTC/USDT" in positions
    assert float(positions["CRYPTO:BTC/USDT"].quantity) > 0

    storage = core.service_registry.get("storage_service")
    assert storage is not None
    journal = await storage.query_journal(event_types=["execution.result"], limit=10)
    assert journal, "expected execution.result in storage journal"

    # Snapshot without binding TCP (ui_bridge disabled).
    ui = core.service_registry.get("ui_bridge_service")
    if ui is None:
        from services.ui_bridge_service import UiBridgeService

        ui = UiBridgeService(core.kernel, "ui_bridge_service")
    snap = ui.build_snapshot()
    assert snap.get("positions"), "snapshot positions empty"
    assert (snap.get("ensemble") or {}).get("members") is not None

    # Halt: risk-increasing path must fail via RuleEngine → order_execution.
    #
    # Assert the halt *rejected the manual run*, not that every later result
    # failed. An order already past the risk gate when the halt landed still
    # settles — a kill switch cannot un-send it — so the wider assertion was
    # flaky under load rather than strict. See test_quik_path_e2e.py.
    had_position = "CRYPTO:BTC/USDT" in core.trading.orders.positions
    assert had_position, "expected an open position before the halt"
    await core.halt("crypto_rule_e2e")
    before = len(fills)
    await ma.run_manual("CRYPTO", "BTC/USDT")
    # Poll instead of a fixed sleep — see the note in test_quik_path_e2e.py.
    halt_deadline = asyncio.get_event_loop().time() + 5.0
    while asyncio.get_event_loop().time() < halt_deadline:
        if len(fills) > before:
            break
        await asyncio.sleep(0.05)
    new_fills = fills[before:]
    assert new_fills, "expected halted execution.result"
    assert any(str(f.get("error") or "") == "halted" for f in new_fills), (
        f"halt did not reject the manual run, got {new_fills!r}"
    )

    await core.shutdown()
    print("e2e crypto RuleEngine paper path OK")


def test_crypto_rule_engine_path_e2e() -> None:
    """Strategy -> Consilium -> RuleEngine -> paper fill, end to end.

    This was marked ``xfail(strict=True)`` with the diagnosis "the Consilium ->
    RuleEngine -> order path produces no fill". That diagnosis was wrong, and the
    defect it named was in the harness rather than in the engine: `sandbox_core`
    redirected `rules.path` into an empty temp directory, so the rule engine
    loaded *zero* rules, and it also set `order_execution: enabled=false` because
    that plugin was not in `enable_plugins`. Both are fixed; the assertion is now
    unconditional so a real regression fails the build.
    """
    with tempfile.TemporaryDirectory() as raw_tmp:
        with sandbox_core(
            Path(raw_tmp),
            enable_plugins=("test_strategy", "ma_cross_example"),
            # The journal assertion below needs a live storage service. Every
            # backend path is rewritten into the sandbox by `build_sandbox_config`,
            # so enabling it keeps the persistence coverage *and* the isolation.
            extra_overrides={
                "storage.enabled": True,
                "services.storage_service.enabled": True,
                "services.storage_service.auto_start": True,
            },
        ) as config_path:
            asyncio.run(_run(config_path))


if __name__ == "__main__":
    test_crypto_rule_engine_path_e2e()
