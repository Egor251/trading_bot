"""Operator AI tools route through ui_bridge handle_command."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.ai_tools import (  # noqa: E402
    ActivateCostScheduleTool,
    CloseAllTool,
    PlaceOrderTool,
    ReleaseExitTool,
    ResetPaperTool,
    SetEnsembleLineupTool,
    SetRiskLimitsTool,
    SetStrategyModeTool,
    SetStrategyParamsTool,
    SetTradingTool,
    SystemHaltTool,
)


def _tool(cls, core):
    return cls(core=core)


def test_set_trading_requires_confirm_live() -> None:
    async def _run() -> None:
        core = MagicMock()
        bridge = MagicMock()
        bridge.handle_command = AsyncMock(return_value={"ok": True})
        core.service_registry.get = MagicMock(return_value=bridge)
        tool = _tool(SetTradingTool, core)
        denied = await tool.execute(mode="live", confirm_live=False)
        assert denied["success"] is False
        assert denied["error"] == "confirm_live_required"
        bridge.handle_command.assert_not_called()
        ok = await tool.execute(mode="paper")
        assert ok["success"] is True
        bridge.handle_command.assert_awaited()

    asyncio.run(_run())


def test_set_ensemble_and_strategy_mode() -> None:
    async def _run() -> None:
        core = MagicMock()
        ens = MagicMock()
        ens._ensemble_cfg = {
            "strategies": ["ma_cross_example"],
            "observe": [],
        }
        core.config = MagicMock()
        core.config.raw = {
            "trading": {
                "ensemble": {
                    "strategies": ["ma_cross_example"],
                    "observe": [],
                }
            }
        }

        async def _handle(body):
            # Simulate bridge apply so post-checks see the new roster.
            strategies = list(body.get("strategies") or ens._ensemble_cfg["strategies"])
            observe = list(
                body["observe"]
                if "observe" in body
                else ens._ensemble_cfg.get("observe") or []
            )
            ens._ensemble_cfg = {"strategies": strategies, "observe": observe}
            core.config.raw["trading"]["ensemble"] = dict(ens._ensemble_cfg)
            return {
                "ok": True,
                "cmd": "SET_ENSEMBLE",
                "strategies": strategies,
                "observe": observe,
            }

        bridge = MagicMock()
        bridge.handle_command = AsyncMock(side_effect=_handle)

        def _get(name):
            if name == "ui_bridge_service":
                return bridge
            if name == "ensemble_service":
                return ens
            return None

        core.service_registry.get = MagicMock(side_effect=_get)

        lineup = _tool(SetEnsembleLineupTool, core)
        empty = await lineup.execute()
        assert empty["success"] is False
        assert empty["error"] == "nothing_to_set"

        res = await lineup.execute(
            strategies=["ma_cross_example"],
            observe=["mean_reversion_example"],
            off=["test_strategy"],
        )
        assert res["success"] is True
        assert res["strategies"] == ["ma_cross_example"]
        assert res["observe"] == ["mean_reversion_example"]
        assert res["persisted_strategies"] == ["ma_cross_example"]
        assert res["persisted_observe"] == ["mean_reversion_example"]
        payload = bridge.handle_command.await_args.args[0]
        assert payload["cmd"] == "SET_ENSEMBLE"
        assert payload["observe"] == ["mean_reversion_example"]

        weights_only = await lineup.execute(weight_mode="equal")
        assert weights_only["success"] is True
        assert weights_only.get("roster_unchanged") is True

        mode_tool = _tool(SetStrategyModeTool, core)
        mode_res = await mode_tool.execute(
            strategy_id="mean_reversion_example", mode="observe"
        )
        assert mode_res["success"] is True
        assert mode_res["mode"] == "observe"
        payload2 = bridge.handle_command.await_args.args[0]
        assert "mean_reversion_example" in payload2["observe"]
        assert "mean_reversion_example" not in payload2["strategies"]

        # Persist must fail closed when in-memory config diverges from request.
        async def _handle_no_persist(body):
            return {
                "ok": True,
                "cmd": "SET_ENSEMBLE",
                "strategies": ["ema_cross"],
                "observe": [],
            }

        bridge.handle_command = AsyncMock(side_effect=_handle_no_persist)
        ens._ensemble_cfg = {"strategies": ["ema_cross"], "observe": []}
        # raw still has previous roster → not persisted
        bad = await lineup.execute(strategies=["ema_cross"], observe=[], persist=True)
        assert bad["success"] is False
        assert bad["error"] == "ensemble_not_persisted"

    asyncio.run(_run())


def test_empty_patch_mutators_reject_noop() -> None:
    async def _run() -> None:
        core = MagicMock()
        bridge = MagicMock()
        bridge.handle_command = AsyncMock(return_value={"ok": True})
        core.service_registry.get = MagicMock(return_value=bridge)

        risk = await _tool(SetRiskLimitsTool, core).execute()
        assert risk["error"] == "nothing_to_set"
        params = await _tool(SetStrategyParamsTool, core).execute(
            strategy_id="macd_trend", params={}
        )
        assert params["error"] == "nothing_to_set"
        trading = await _tool(SetTradingTool, core).execute()
        assert trading["error"] == "nothing_to_set"
        release = await _tool(ReleaseExitTool, core).execute()
        assert release["error"] == "nothing_to_set"
        bridge.handle_command.assert_not_called()

    asyncio.run(_run())


def test_halt_and_reset_verify_applied() -> None:
    async def _run() -> None:
        core = MagicMock()
        runtime = MagicMock()
        runtime.halted = True
        runtime._halt_reason = "ai_operator"
        runtime.open_positions = MagicMock(return_value=[])
        runtime.orders = MagicMock()
        runtime.orders.equity = 10000.0
        runtime.orders.gross_exposure = 0.0
        core.trading = runtime

        bridge = MagicMock()
        bridge.handle_command = AsyncMock(
            return_value={"ok": True, "cmd": "KILL", "halted": True, "halt_reason": "ai"}
        )
        core.service_registry.get = MagicMock(return_value=bridge)

        halt = await _tool(SystemHaltTool, core).execute(reason="ai")
        assert halt["success"] is True
        assert halt["halted"] is True

        bridge.handle_command = AsyncMock(
            return_value={
                "ok": True,
                "cmd": "RESET_PAPER",
                "open_positions": [],
            }
        )
        reset = await _tool(ResetPaperTool, core).execute()
        assert reset["success"] is True
        assert reset["open_count"] == 0

        runtime.open_positions = MagicMock(return_value=[{"symbol": "SBER"}])
        incomplete = await _tool(ResetPaperTool, core).execute()
        assert incomplete["success"] is False
        assert incomplete["error"] == "reset_incomplete"

    asyncio.run(_run())


def test_place_order_and_close_all_gates() -> None:
    async def _run() -> None:
        core = MagicMock()
        bridge = MagicMock()
        bridge.handle_command = AsyncMock(return_value={"ok": True, "cmd": "PLACE_ORDER"})
        runtime = MagicMock()
        runtime.mode = "live"
        core.trading = runtime
        core.service_registry.get = MagicMock(return_value=bridge)

        place = _tool(PlaceOrderTool, core)
        denied = await place.execute(ticker="SBER", side="buy", quantity=1)
        assert denied["error"] == "confirm_live_required"
        bridge.handle_command.assert_not_called()

        ok = await place.execute(
            ticker="SBER",
            side="buy",
            quantity=1,
            confirm_live=True,
            limit_price=250.0,
        )
        assert ok["success"] is True
        payload = bridge.handle_command.await_args.args[0]
        assert payload["cmd"] == "PLACE_ORDER"
        assert payload["confirm_live"] is True
        assert payload["limit_price"] == 250.0

        runtime.mode = "paper"
        close_all = _tool(CloseAllTool, core)
        denied_all = await close_all.execute()
        assert denied_all["error"] == "confirm_close_all_required"
        ok_all = await close_all.execute(
            confirm_close_all=True, order_type="limit", limit_price=100
        )
        assert ok_all["success"] is True
        payload2 = bridge.handle_command.await_args.args[0]
        assert payload2["cmd"] == "CLOSE_ALL"
        assert payload2["confirm_close_all"] is True

        activate = _tool(ActivateCostScheduleTool, core)
        denied_cost = await activate.execute(expected_hash="a" * 64)
        assert denied_cost["error"] == "confirm_cost_schedule_required"
        await activate.execute(expected_hash="a" * 64, confirm_cost_schedule=True)
        payload3 = bridge.handle_command.await_args.args[0]
        assert payload3["cmd"] == "COST_ACTIVATE"
        assert payload3["confirm_cost_schedule"] is True

    asyncio.run(_run())
