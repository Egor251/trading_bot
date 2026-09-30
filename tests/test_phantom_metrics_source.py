"""Phantom PnL must come from runtime book, not single-position fill approx."""

from __future__ import annotations

from types import SimpleNamespace

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.runtime import TradingRuntime
from services.ops_monitor_service import OpsMonitorService
from services.phantom_service import PhantomService


def test_phantom_metrics_prefers_runtime_over_fills_approx() -> None:
    core = SimpleNamespace(service_registry={}, config=SimpleNamespace())
    phantom = PhantomService(core, "phantom_service")
    phantom.config = {"start_equity": 10_000.0}
    phantom.runtime = TradingRuntime.from_config(
        {
            "mode": "phantom",
            "equity": 10_000,
            "risk": {"max_position_pct": 1.0, "max_leverage": 1.0},
        }
    )
    # Poison fills that would invent huge multi-symbol PnL via equity_pnl().
    phantom._fills = [  # noqa: SLF001
        {"action": "BUY", "quantity": 10, "price": 100.0, "ticker": "BTC/USDT"},
        {"action": "BUY", "quantity": 500, "price": 50.0, "ticker": "ETH/USDT"},
        {"action": "SELL", "quantity": 10, "price": 200.0, "ticker": "BTC/USDT"},
    ]
    # Runtime book untouched → pnl ~ 0
    metrics = phantom._metrics()  # noqa: SLF001
    assert metrics["source"] == "runtime"
    assert abs(metrics["pnl"]) < 1.0


def test_ops_reads_runtime_pnl_not_fill_approx() -> None:
    core = SimpleNamespace(service_registry={}, config=SimpleNamespace(), phantom=None)
    phantom = PhantomService(core, "phantom_service")
    phantom.config = {"start_equity": 10_000.0}
    phantom.runtime = TradingRuntime.from_config(
        {
            "mode": "phantom",
            "equity": 10_000,
            "risk": {"max_position_pct": 1.0, "max_leverage": 1.0},
        }
    )
    phantom.runtime.orders.equity = 9_195.0
    phantom._fills = [  # noqa: SLF001
        {"action": "BUY", "quantity": 1000, "price": 100.0},
        {"action": "SELL", "quantity": 1000, "price": 268.0},
    ]
    core.service_registry["phantom_service"] = phantom
    core.phantom = phantom.runtime
    ops = OpsMonitorService(core, "ops_monitor_service")
    pnl = ops._phantom_day_pnl()  # noqa: SLF001
    assert pnl is not None
    assert abs(pnl - (9_195.0 - 10_000.0)) < 1e-6
    assert pnl < 0  # not the invented +168k from fills
