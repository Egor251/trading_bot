"""E2E: quik fake transport → candle → signal → quik broker fill."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MICROKERNEL_ROOT = ROOT.parent / "Code_base" / "microkernel_core"
sys.path.insert(0, str(ROOT))
if MICROKERNEL_ROOT.exists():
    sys.path.insert(0, str(MICROKERNEL_ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from host.trading_bot_core import TradingBotCore  # noqa: E402
from tests.e2e_sandbox import sandbox_core  # noqa: E402


async def _run(config_path: Path) -> None:
    core = TradingBotCore(config_path)

    await core.boot()

    mock = core.service_registry.get("mock_market_service")
    assert mock is not None
    mock.config["enabled"] = False
    for service_id in (
        "ccxt_market_service",
        "alpaca_market_service",
        "ui_bridge_service",
        "execution_service",  # durable router/recon blocks fake-quik e2e
    ):
        service = core.service_registry.get(service_id)
        if service is not None:
            service.config["enabled"] = False
            service.config["auto_start"] = False
            if getattr(service, "is_running", False):
                await service.stop()

    # Direct strategy→execution rule is disabled; use Consilium → RuleEngine path.
    ens = core.service_registry.get("ensemble_service")
    assert ens is not None
    ens.config["enabled"] = True
    ens.config["auto_start"] = True
    ens.config["route_execution"] = False
    ens.config["debounce_sec"] = 0.05
    ens_lineup = {
        "strategies": ["test_strategy"],
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
        ens.manager.set_equal_weights(["test_strategy"])

    quik = core.service_registry.get("quik_market_service")
    assert quik is not None, "quik_market_service missing"
    quik.config["enabled"] = True
    quik.config["auto_start"] = True
    quik.config["transport"] = "fake"
    quik.config["transport_options"] = {"base_prices": {"SBER": 285.0, "GAZP": 165.0}}
    quik.config["interval_sec"] = 0.15
    quik.config["follow_universe"] = False
    quik.config["tickers"] = ["SBER", "GAZP"]
    quik.config["class_code"] = "TQBR"

    assert core.trading is not None
    core.trading.mode = "quik"
    core.trading.orders.mode = "quik"

    # Keep other strategies from voting / flooding the bus.
    for pid, plugin in list(getattr(core.plugin_manager, "_instances", {}).items()):
        if pid in {"test_strategy", "ai_analyst", "order_execution"}:
            continue
        if hasattr(plugin, "config"):
            plugin.config["enabled"] = False
            plugin.config["observe_only"] = True
            plugin.config["tickers"] = []

    strat = core.plugin_manager.get("test_strategy")
    assert strat is not None
    strat.config["candles_before_signal"] = 1
    strat.config["tickers"] = ["SBER"]
    strat.config["class_code"] = "TQBR"
    strat.config["observe_only"] = False
    strat.config["enabled"] = True
    strat.config["quantity"] = 1

    fills: list[dict] = []
    candles: list[str] = []

    async def on_fill(event) -> None:
        fills.append(dict(event.payload))

    async def on_candle(event) -> None:
        candles.append(event.payload.get("ticker", ""))

    await core.event_bus.subscribe("execution.result", on_fill)
    await core.event_bus.subscribe("market.candle", on_candle)

    # Start core (mock disabled; start quik explicitly after)
    await core.start()
    # mock may still have been started if enabled at start — stop it
    if mock.is_running:
        await mock.stop()
    if not quik.is_running:
        await quik.start()

    # Runtime may still hold router handles from boot — clear for fake broker path.
    core.trading.execution_router = None
    core.trading.reconciliation = None
    if core.trading.broker is None and quik.broker is not None:
        core.trading.broker = quik.broker

    assert core.trading.broker is not None, "expected QuikBrokerExecution on runtime"
    assert await quik.broker.transport.is_connected()  # type: ignore[union-attr]

    fills.clear()

    deadline = asyncio.get_event_loop().time() + 10.0
    while asyncio.get_event_loop().time() < deadline:
        if any(f.get("success") for f in fills):
            break
        await asyncio.sleep(0.1)

    assert candles, "expected market.candle from quik fake"
    assert any(f.get("success") for f in fills), f"expected quik fill, got {fills!r}"
    assert any(f.get("mode") == "quik" for f in fills if f.get("success"))

    tx = getattr(quik.broker.transport, "transactions", [])  # type: ignore[union-attr]
    assert tx, "expected send_transaction on fake transport"

    # Halt must block *new* risk.
    #
    # The previous assertion was "every execution.result after the halt failed",
    # which is not the property a kill switch can have: an order that had already
    # passed the risk gate when the halt landed still settles at the broker, and
    # nothing can un-send it. Under load that window widens and the assertion
    # failed intermittently while the halt was in fact working.
    #
    # The race-free statement is instead: no *new* transaction reached the broker
    # after the halt, and the manual run was rejected as halted.
    sent_before = len(getattr(quik.broker.transport, "transactions", []) or [])
    await core.halt("quik_e2e")
    before = len(fills)
    await strat.run_manual("TQBR", "SBER")
    halt_deadline = asyncio.get_event_loop().time() + 5.0
    while asyncio.get_event_loop().time() < halt_deadline:
        if len(fills) > before:
            break
        await asyncio.sleep(0.05)
    new_fills = fills[before:]
    assert new_fills, "expected an execution.result for the post-halt manual run"
    assert any(str(f.get("error") or "") == "halted" for f in new_fills), (
        f"halt did not reject the manual run, got {new_fills!r}"
    )
    sent_after = len(getattr(quik.broker.transport, "transactions", []) or [])
    assert sent_after == sent_before, (
        f"halt still let {sent_after - sent_before} order(s) reach the broker"
    )

    await core.shutdown()
    print("e2e quik fake path OK")


def test_quik_fake_path_e2e(tmp_path: Path | None = None) -> None:
    """Quik fake transport → candle → Consilium → RuleEngine → broker fill.

    Previously marked ``xfail(strict=True)`` on the belief that the Quik broker
    wire was dead. Same root cause as the crypto E2E: `sandbox_core` moved
    ``rules.path`` to an empty directory and disabled ``order_execution``, so
    nothing could execute regardless of the broker. The transport-level defects
    that marker also listed (a missing ``get_orders`` on the fake, all stored
    orders carrying ``filled_quantity=0``) are addressed separately; this
    assertion is now unconditional so a real broker regression fails the build.
    """
    with tempfile.TemporaryDirectory() as raw_tmp:
        work = Path(raw_tmp)
        with sandbox_core(work) as config_path:
            asyncio.run(_run(config_path))


if __name__ == "__main__":
    test_quik_fake_path_e2e()
