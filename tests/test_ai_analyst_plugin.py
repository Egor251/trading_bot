"""AI Analyst plugin smoke — mock provider + trading tools registry."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from engine.ai_tools import (  # noqa: E402
    TRADING_AI_TOOL_CLASSES,
    GetRuntimeStatusTool,
    ListStrategiesTool,
)
from ai_analyst import AIAnalystService, AnalystRequest, build_tool_registry  # noqa: E402


def test_bootstrap_imports_ai_analyst() -> None:
    import ai_analyst

    assert ai_analyst.__file__


def test_trading_tools_in_registry() -> None:
    class _Core:
        trading = None
        service_registry = type("R", (), {"get": lambda self, n: None})()
        plugin_manager = type("P", (), {"plugins": {}})()

    registry = build_tool_registry(
        None,
        core=_Core(),
        extra_tool_classes=list(TRADING_AI_TOOL_CLASSES),
    )
    names = set(registry.get_available_tools())
    required = {
        "get_runtime_status",
        "get_universe",
        "get_ensemble_status",
        "list_strategies",
        "query_journal",
        "get_risk_limits",
        "get_ops_status",
        "get_storage_status",
        "get_phantom_status",
        "get_reconciliation_status",
        "get_config_slice",
        "list_services_status",
        "history_summary",
        "system_halt",
        "system_resume",
        "set_ensemble_lineup",
        "set_strategy_mode",
        "set_strategy_params",
        "set_risk_limits",
        "set_trading",
        "set_universe",
        "set_storage",
        "set_plugin",
        "reset_paper",
        "phantom_capture",
        "phantom_run",
        "phantom_reset",
        "save_config",
        "place_order",
        "close_position",
        "close_all",
        "cancel_order",
        "cancel_all_orders",
        "release_exit_pending",
        "dry_run_risk_check",
        "get_trade_history",
        "get_strategy_trades",
        "get_risk_rejects",
        "get_strategy_risk_limits",
        "set_strategy_risk_limits",
        "activate_cost_schedule",
        "reconcile_now",
        "phantom_sync_metrics",
    }
    missing = sorted(required - names)
    assert not missing, missing
    # Control tools must be marked modifying for operator RBAC.
    by_name = {t.name: t for t in TRADING_AI_TOOL_CLASSES}
    assert by_name["set_ensemble_lineup"].is_modifying is True
    assert by_name["get_ensemble_status"].is_modifying is False
    assert by_name["place_order"].is_modifying is True
    assert by_name["dry_run_risk_check"].is_modifying is False


async def _mock_ask() -> None:
    service = AIAnalystService(
        settings={
            "default_provider": "mock",
            "profiles": {
                "mock": {
                    "display_name": "Mock",
                    "provider_type": "mock",
                    "enabled": True,
                    "settings": {},
                }
            },
        },
        extra_tool_classes=[GetRuntimeStatusTool, ListStrategiesTool],
    )
    await service.initialize()
    result = await service.ask(
        AnalystRequest(
            message="Say hello briefly",
            use_tools=False,
            include_context=False,
            system_prompt="You are a test assistant.",
        )
    )
    assert result.success, result.error
    assert result.content
    await service.cleanup()


def test_mock_ask() -> None:
    asyncio.run(_mock_ask())


async def _plugin_loaded() -> None:
    from host.trading_bot_core import TradingBotCore

    core = TradingBotCore()
    await core.boot()
    plugin = core.plugin_manager.get("ai_analyst")
    assert plugin is not None, "ai_analyst plugin missing"
    from engine.strategy_plugin import StrategyPlugin

    assert not isinstance(plugin, StrategyPlugin)
    assert plugin.service is not None
    tools = plugin.tool_names()
    assert "get_universe" in tools
    result = await plugin.ask("Status check", use_tools=False, provider_id="mock")
    assert result.success, result.error
    await core.shutdown()


def test_plugin_in_core() -> None:
    asyncio.run(_plugin_loaded())


if __name__ == "__main__":
    test_bootstrap_imports_ai_analyst()
    test_trading_tools_in_registry()
    test_mock_ask()
    test_plugin_in_core()
    print("ai_analyst OK")
