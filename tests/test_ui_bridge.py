"""UI bridge TCP protocol smoke (no browser)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from host.config_patch import operator_config_slice, set_path  # noqa: E402
from services.ui_bridge_service import UiBridgeService  # noqa: E402


def test_config_patch_set_path() -> None:
    cfg: dict = {}
    set_path(cfg, "universe.static_active", ["TQBR:SBER"])
    set_path(cfg, "trading.mode", "paper")
    assert cfg["universe"]["static_active"] == ["TQBR:SBER"]
    assert cfg["trading"]["mode"] == "paper"
    slice_ = operator_config_slice(
        {
            "trading": {"mode": "paper", "ensemble": {"strategies": ["a"]}},
            "universe": {"mode": "manual"},
            "market": {"default_class_code": "TQBR"},
            "core": {"log_level": "INFO"},
            "plugins": {
                "paths": ["plugins"],
                "ma_cross_example": {"tickers": ["SBER"]},
                "ai_analyst": {
                    "profiles": {
                        "cursor": {"settings": {"api_key": "must-not-leak"}}
                    }
                },
            },
            "services": {"mock_market_service": {"enabled": True}},
        }
    )
    assert "ma_cross_example" in slice_["plugins"]
    assert "paths" not in slice_["plugins"]
    assert slice_["services"]["mock_market_service"]["enabled"] is True
    assert (
        slice_["plugins"]["ai_analyst"]["profiles"]["cursor"]["settings"]["api_key"]
        == "********"
    )


async def _tcp_roundtrip() -> None:
    core = MagicMock()
    raw_cfg: dict = {
        "instance": {"id": "local"},
        "trading": {
            "mode": "paper",
            "equity": 10000.0,
            "risk": {"max_position_pct": 0.2},
            "ensemble": {"strategies": ["ma_cross_example"], "weight_mode": "equal"},
        },
        "universe": {"mode": "manual", "static_active": ["TQBR:SBER"]},
        "plugins": {"ma_cross_example": {"tickers": ["SBER"], "class_code": "TQBR"}},
        "services": {"mock_market_service": {"enabled": True}},
        "core": {"log_level": "INFO"},
        "market": {},
    }

    async def cfg_get(key, default=None):
        if key == "ipc":
            return {"pub_port": 5555, "sub_port": 5556}
        if key == "instance.id":
            return "local"
        cur = raw_cfg
        for part in str(key).split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    async def cfg_save():
        return None

    core.config.get = cfg_get
    core.config.save = cfg_save
    core.config.raw = raw_cfg
    published: list = []

    async def pub(event):
        published.append(event)

    core.event_bus.publish = pub
    core.event_bus.subscribe = AsyncMock()
    core.event_bus.unsubscribe = AsyncMock()

    uni_svc = MagicMock()
    uni_svc._last_active_keys = ["TQBR:SBER"]
    uni_svc._catalog = []
    uni_svc._manager = MagicMock()
    uni_svc._manager.config = MagicMock()
    uni_svc._manager.config.mode = "manual"
    uni_svc._manager.config.static_active = ("TQBR:SBER",)

    async def reselect_now(reason="manual"):
        snap = MagicMock()
        snap.active_keys = lambda: list(uni_svc._manager.config.static_active)
        uni_svc._last_active_keys = list(uni_svc._manager.config.static_active)
        return snap

    uni_svc.reselect_now = reselect_now

    ens = MagicMock()
    ens.manager = MagicMock()
    ens.manager.weights = {"ma_cross_example": 0.5, "mean_reversion_example": 0.5}
    ens.manager.set_weights = MagicMock()
    ens.manager.set_equal_weights = MagicMock()
    ens._ensemble_cfg = {
        "strategies": ["ma_cross_example", "mean_reversion_example"],
        "weight_mode": "equal",
    }
    ens._decisions = 1
    ens._last_decision = {"action": "BUY", "ticker": "SBER", "strength": 0.8}
    ens.members_for_ui = MagicMock(
        return_value=[
            {
                "strategy_id": "ma_cross_example",
                "action": "BUY",
                "stance": "for",
                "weight": 0.5,
                "ticker": "SBER",
                "strength": 1.0,
                "metrics": {"fast_ma": 1.0, "slow_ma": 0.9},
            },
            {
                "strategy_id": "mean_reversion_example",
                "action": "SELL",
                "stance": "against",
                "weight": 0.5,
                "ticker": "SBER",
                "strength": 0.6,
                "metrics": {"z": 1.5},
            },
        ]
    )

    ai_plugin = MagicMock()
    ai_svc = MagicMock()
    ai_svc.refresh_settings = MagicMock()
    ai_plugin.service = ai_svc
    ai_plugin.config = {"default_provider": "mock", "profiles": {"mock": {}}}
    ai_plugin._last_error = None
    ai_plugin._last_ask = {}
    ai_plugin.tool_names = MagicMock(return_value=["get_runtime_status"])

    async def ai_ask(message, use_tools=True, provider_id=None, **kwargs):
        result = MagicMock()
        result.success = True
        result.content = f"echo:{message}"
        result.provider_id = "mock"
        result.model = "mock"
        result.error = None
        result.tool_trace = [{"name": "get_runtime_status"}]
        result.latency = 0.01
        return result

    ai_plugin.ask = ai_ask

    def registry_get(name):
        if name == "universe_service":
            return uni_svc
        if name == "ensemble_service":
            return ens
        return None

    core.service_registry.get = registry_get
    core.plugin_manager = MagicMock()
    core.plugin_manager.get = MagicMock(
        side_effect=lambda pid: ai_plugin if pid == "ai_analyst" else None
    )
    core.plugin_manager._instances = {"ai_analyst": ai_plugin}
    core.plugin_manager._records = {}
    core.trading = MagicMock()
    core.trading.snapshot = MagicMock(
        return_value={
            "mode": "paper",
            "halted": False,
            "equity": 10000.0,
            "fills": 0,
            "exposures": {},
            "candles": {},
            "risk": {"halted": False, "limits": raw_cfg["trading"]["risk"]},
        }
    )
    core.trading.mode = "paper"
    core.trading.orders = MagicMock()
    core.trading.orders.mode = "paper"
    core.trading.orders.equity = 10000.0
    core.trading.orders.set_starting_equity = MagicMock(
        side_effect=lambda value: setattr(core.trading.orders, "equity", value)
    )
    core.trading.risk = MagicMock()
    core.trading.risk.limits = MagicMock(
        max_portfolio_drawdown=0.1,
        max_strategy_drawdown=0.05,
        max_position_pct=0.2,
        max_leverage=1.0,
    )

    svc = UiBridgeService(core, "ui_bridge_service")
    svc.config = svc.default_config()
    svc.config["ws_port"] = 18766
    svc.config["zmq_enabled"] = False
    # A real authorizer: the whole privileged command surface is exercised
    # below, so it must run through real HMAC verification, not a bypass.
    from engine.control_auth import ControlAuthorizer, HMACTokenAuth

    secret = "ui-bridge-test-secret-that-is-long-enough"
    authorizer = ControlAuthorizer(HMACTokenAuth(secret))
    vars(core)["control_authorizer"] = authorizer
    nonce = [0]

    def signed(body: dict) -> bytes:
        nonce[0] += 1
        payload = {
            **body,
            "auth_token": HMACTokenAuth(secret).issue(
                "operator", ["admin", "risk"], ttl_seconds=600
            ),
            "nonce": f"n{nonce[0]}",
        }
        return (json.dumps(payload) + "\n").encode()

    await svc.on_initialize()
    await svc.start()

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", 18766)
        line = await asyncio.wait_for(reader.readline(), timeout=2)
        snap = json.loads(line.decode())
        assert snap["schema"] == "ui.snapshot.v2"
        assert "config" in snap
        assert "catalog" in snap
        assert "ai" in snap
        assert "gross_exposure" in snap
        assert "positions" in snap
        members = (snap.get("ensemble") or {}).get("members") or []
        assert len(members) == 2
        assert members[0]["stance"] == "for"
        assert members[1]["stance"] == "against"

        pending: list[dict] = []

        async def read_ack(request_id: str) -> dict:
            deadline = asyncio.get_running_loop().time() + 5.0
            while asyncio.get_running_loop().time() < deadline:
                for idx, msg in enumerate(pending):
                    if (
                        msg.get("schema") == "ui.ack.v1"
                        and msg.get("request_id") == request_id
                    ):
                        return pending.pop(idx)
                remaining = deadline - asyncio.get_running_loop().time()
                raw = await asyncio.wait_for(reader.readline(), timeout=remaining)
                message = json.loads(raw.decode())
                if message.get("schema") == "ui.ack.v1" and (
                    message.get("request_id") == request_id
                ):
                    return message
                pending.append(message)
            raise TimeoutError(
                f"ack not received: {request_id}; pending="
                f"{[(m.get('schema'), m.get('request_id')) for m in pending]}"
            )

        # HALT stays available without any token — risk reduction must never be
        # gated on credentials.
        writer.write(b'{"cmd":"KILL","reason":"test","request_id":"r1"}\n')
        await writer.drain()
        ack = await read_ack("r1")
        assert ack.get("ok") is True
        assert ack.get("request_id") == "r1"
        assert any(e.event_type == "system.halt" for e in published)

        # An unsigned privileged command is refused.
        writer.write(b'{"cmd":"RESUME","request_id":"r-unsigned"}\n')
        await writer.drain()
        ack = await read_ack("r-unsigned")
        assert ack.get("ok") is False
        assert "requires authentication" in (ack.get("error") or "")

        writer.write(signed({"cmd": "RESUME", "request_id": "r2"}))
        await writer.drain()
        ack = await read_ack("r2")
        assert ack.get("ok") is True

        writer.write(
            signed(
                {
                    "cmd": "SET_UNIVERSE",
                    "mode": "manual",
                    "static_active": ["TQBR:SBER", "TQBR:GAZP"],
                    "persist": False,
                    "request_id": "r3",
                }
            )
        )
        await writer.drain()
        ack = await read_ack("r3")
        assert ack.get("ok") is True
        assert "TQBR:GAZP" in (ack.get("active_keys") or [])

        writer.write(
            signed(
                {
                    "cmd": "SET_TRADING",
                    "mode": "paper",
                    "equity": 12000,
                    "risk": {"max_position_pct": 0.15},
                    "persist": False,
                    "request_id": "r4",
                }
            )
        )
        await writer.drain()
        ack = await read_ack("r4")
        assert ack.get("ok") is True
        assert core.trading.orders.equity == 12000.0

        writer.write(
            signed(
                {
                    "cmd": "SET_ENSEMBLE",
                    "strategies": ["ma_cross_example"],
                    "weights": {"ma_cross_example": 1.0},
                    "persist": False,
                    "request_id": "r5",
                }
            )
        )
        await writer.drain()
        ack = await read_ack("r5")
        assert ack.get("ok") is True
        ens.manager.set_weights.assert_called()

        writer.write(
            signed(
                {
                    "cmd": "SET_STRATEGY_PARAMS",
                    "plugin_id": "ma_cross_example",
                    "params": {"fast": 5},
                    "persist": False,
                    "request_id": "r6",
                }
            )
        )
        await writer.drain()
        ack = await read_ack("r6")
        assert ack.get("ok") is True
        assert raw_cfg["plugins"]["ma_cross_example"]["fast"] == 5

        writer.write(signed({"cmd": "SAVE_CONFIG", "request_id": "r7"}))
        await writer.drain()
        ack = await read_ack("r7")
        assert ack.get("ok") is True

        writer.write(
            signed(
                {
                    "cmd": "ASK",
                    "text": "status?",
                    "use_tools": True,
                    "request_id": "r8",
                }
            )
        )
        await writer.drain()
        ack = await read_ack("r8")
        assert ack.get("ok") is True
        assert "echo:status?" in (ack.get("answer") or "")

        writer.write(
            signed(
                {
                    "cmd": "SET_AI",
                    "default_provider": "mock",
                    "max_tool_iterations": 3,
                    "persist": False,
                    "request_id": "r9",
                }
            )
        )
        await writer.drain()
        ack = await read_ack("r9")
        assert ack.get("ok") is True

        # `caller_role` is derived from the verified principal, never from the
        # request body (an unauthenticated peer could otherwise promote itself).
        writer.write(
            signed(
                {
                    "cmd": "ASK",
                    "text": "who am i",
                    "caller_role": "operator",
                    "request_id": "r10",
                }
            )
        )
        await writer.drain()
        ack = await read_ack("r10")
        assert ack.get("ok") is True

        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=1)
        except TimeoutError:
            pass
    finally:
        await svc.stop()


def test_bridge_fails_closed_without_authorizer_but_keeps_halt() -> None:
    """Losing the safety service must not open the control plane (A5).

    Previously an absent ``control_authorizer`` meant "no checks at all", so a
    disabled or mis-ordered ``live_safety_service`` silently turned SET_SECRETS
    and paper PLACE_ORDER into unauthenticated commands.
    """
    from engine.control_auth import AuthorizationDenied
    from services.ui_bridge_service import UiBridgeService

    core = MagicMock()

    async def _cfg_get(key, default=None):
        return {"pub_port": 5555, "sub_port": 5556}.get(key, default)

    core.config.get = _cfg_get
    core.event_bus.publish = AsyncMock()
    vars(core).pop("control_authorizer", None)

    svc = UiBridgeService(core, "ui_bridge_service")
    svc.config = svc.default_config()

    async def _run() -> None:
        await svc.on_initialize()
        halt = await svc.handle_command({"cmd": "KILL", "reason": "t"})
        assert halt.get("ok") is True, "HALT must never be gated on credentials"

        for cmd in ("SET_SECRETS", "SET_TRADING", "RESET_PAPER", "RESUME", "ASK"):
            out = await svc.handle_command({"cmd": cmd, "text": "x"})
            assert out.get("ok") is False, cmd
            assert "fail-closed" in (out.get("error") or ""), cmd

        # Read-only commands stay open so the Operator can still inspect state.
        status = await svc.handle_command({"cmd": "STATUS"})
        assert status.get("ok") is True

    asyncio.run(_run())
    assert isinstance(AuthorizationDenied("x"), PermissionError)


def test_ui_bridge_tcp_kill_resume() -> None:
    asyncio.run(_tcp_roundtrip())


async def _confirm_live_roundtrip() -> None:
    core = MagicMock()
    raw_cfg: dict = {
        "instance": {"id": "local"},
        "trading": {"mode": "paper", "equity": 10000.0, "risk": {}},
        "universe": {"mode": "manual", "static_active": ["CRYPTO:BTC/USDT"]},
        "plugins": {},
        "services": {},
        "core": {"log_level": "INFO"},
        "market": {},
    }

    async def cfg_get(key, default=None):
        if key == "instance.id":
            return "local"
        cur = raw_cfg
        for part in str(key).split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    core.config.get = cfg_get
    core.config.save = AsyncMock()
    core.config.raw = raw_cfg
    core.event_bus.publish = AsyncMock()
    core.event_bus.subscribe = AsyncMock()
    core.event_bus.unsubscribe = AsyncMock()
    # Live arming refuses when the research gate leaves nothing cleared to trade,
    # so this stub has to present a lineup that *is* cleared; the refusal path has
    # its own tests.
    _ens = MagicMock()
    _ens.lineup_ids = MagicMock(return_value=["cleared_strategy"])
    core.service_registry.get = MagicMock(
        side_effect=lambda sid: _ens if sid == "ensemble_service" else None
    )
    core.plugin_manager = MagicMock()
    core.plugin_manager.get_plugin = MagicMock(return_value=None)
    runtime = MagicMock()
    runtime.mode = "paper"
    runtime.orders = MagicMock()
    runtime.orders.mode = "paper"
    runtime.orders.equity = 10000.0
    runtime.open_position_keys = MagicMock(return_value=set())
    core.trading = runtime

    svc = UiBridgeService(core, {"host": "127.0.0.1", "port": 0})
    import engine.research_gate as _gate

    _saved = _gate.evaluate_live_readiness
    _gate.evaluate_live_readiness = lambda sid, **kw: {
        "live_ok": True,
        "blockers": [],
        "grade": "live_ok",
        "score": 1.0,
    }
    try:
        denied = await svc._cmd_set_trading({"mode": "live", "persist": False})
        assert denied.get("ok") is False
        assert denied.get("error") == "confirm_live_required"
        assert raw_cfg["trading"]["mode"] == "paper"

        ok = await svc._cmd_set_trading(
            {"mode": "live", "confirm_live": True, "persist": False}
        )
        assert ok.get("ok") is True
        assert raw_cfg["trading"]["mode"] == "live"
        assert runtime.mode == "live"

        # Same mode again does not require confirm.
        again = await svc._cmd_set_trading({"mode": "live", "persist": False})
        assert again.get("ok") is True
    finally:
        _gate.evaluate_live_readiness = _saved


def test_set_trading_confirm_live() -> None:
    asyncio.run(_confirm_live_roundtrip())


if __name__ == "__main__":
    test_config_patch_set_path()
    test_ui_bridge_tcp_kill_resume()
    test_set_trading_confirm_live()
    print("ui_bridge OK")
