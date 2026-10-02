"""The ZMQ SUB command channel must be as safe as the HTTP one, and as legible.

The audit listed "ZMQ SUB command channel" as a leftover. Checked properly, the
authorisation part was already sound: `_zmq_command_loop` funnels through the same
`_authorize_command` that every other transport uses, that function is fail-closed,
and both sockets bind to `127.0.0.1` with no configuration to widen them. So ZMQ
was not an auth bypass.

What *was* broken is legibility. `_zmq_command_loop` discarded the result of
`handle_command`. PUB/SUB has no return path, so a command the authorizer
**refused** — a RESUME, an EMERGENCY flatten — produced no error anywhere the
caller or the operator could see. Denied and delivered were indistinguishable.

These tests pin both halves: the gate really does close, and the outcome is now
observable.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from services.ui_bridge_service import UiBridgeService  # noqa: E402


def _bridge() -> UiBridgeService:
    core = MagicMock()
    core.service_registry.get = MagicMock(return_value=None)
    core.config.get = AsyncMock(side_effect=lambda k, d=None: "local")
    core.trading = None
    core.event_bus = MagicMock()
    core.event_bus.publish = AsyncMock()
    svc = UiBridgeService(core, "ui_bridge_service")
    svc.config = {**svc.default_config(), "zmq_enabled": False}
    svc.core = core
    svc.is_running = True
    return svc


# ---------------------------------------------------------------------------
# the gate is transport-agnostic and closes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cmd", ["RESUME", "LIVE", "SET_TRADING", "CONFIG"])
def test_privileged_commands_are_refused_without_an_authorizer(cmd: str) -> None:
    """No authorizer installed → fail closed, for any transport."""
    from engine.control_auth import AuthorizationDenied

    svc = _bridge()
    payload: dict[str, Any] = {}
    with pytest.raises(AuthorizationDenied):
        svc._authorize_command(cmd, payload)  # noqa: SLF001


@pytest.mark.parametrize("cmd", ["HALT", "KILL", "STOP", "EMERGENCY"])
def test_emergency_halt_stays_available_without_an_authorizer(cmd: str) -> None:
    """Reducing risk must never be blocked by a missing authorizer.

    `EMERGENCY` is included deliberately: it flattens the book, so it is risk
    *reduction* even though it is not spelled like a halt.
    """
    svc = _bridge()
    payload: dict[str, Any] = {}
    svc._authorize_command(cmd, payload)  # noqa: SLF001  # must not raise
    assert payload["_authorized_subject"] == "unauthenticated_emergency_halt"


def test_zmq_and_http_share_one_gate() -> None:
    """The ZMQ loop must not reach `_dispatch` without passing the gate."""
    svc = _bridge()
    seen: list[str] = []
    svc._authorize_command = lambda raw, payload: seen.append(raw)  # type: ignore[method-assign]  # noqa: SLF001

    async def _dispatch(raw, payload, instance_id):
        return {"ok": True}

    svc._dispatch = _dispatch  # type: ignore[method-assign]
    asyncio.run(svc.handle_command({"cmd": "RESUME"}))
    assert seen == ["RESUME"], "command bypassed the authorization gate"


def test_zmq_binds_to_loopback_only() -> None:
    """No configuration may widen the bind address."""
    import inspect

    source = inspect.getsource(UiBridgeService._start_zmq)  # noqa: SLF001
    assert "tcp://127.0.0.1:" in source
    assert "tcp://*" not in source
    assert "0.0.0.0" not in source
    assert "tcp://{" not in source, "the bind address must not be configurable"


# ---------------------------------------------------------------------------
# the outcome is observable
# ---------------------------------------------------------------------------


def test_denied_command_is_recorded_as_an_ack() -> None:
    svc = _bridge()
    svc._record_command_ack(  # noqa: SLF001
        {"cmd": "RESUME", "request_id": "r1"},
        {"ok": False, "error": "AuthorizationDenied: no token"},
    )
    ack = svc._command_acks["r1"]
    assert ack["ok"] is False
    assert "AuthorizationDenied" in ack["result"]["error"]
    assert ack["cmd"] == "RESUME"


def test_successful_command_is_recorded_as_an_ack() -> None:
    svc = _bridge()
    svc._record_command_ack(  # noqa: SLF001
        {"cmd": "HALT", "request_id": "r2"}, {"ok": True}
    )
    assert svc._command_acks["r2"]["ok"] is True


def test_acks_appear_in_the_snapshot() -> None:
    """A caller with no return path reads the outcome from the snapshot."""
    svc = _bridge()
    svc._record_command_ack(  # noqa: SLF001
        {"cmd": "RESUME", "request_id": "r3"}, {"ok": False, "error": "denied"}
    )
    snap = svc.build_snapshot()
    assert snap["command_acks"]["r3"]["ok"] is False
    assert snap["command_acks"]["r3"]["result"]["error"] == "denied"


def test_command_without_a_request_id_is_not_retained() -> None:
    """No correlation id means nobody can read it; do not grow the map."""
    svc = _bridge()
    svc._record_command_ack({"cmd": "RESUME"}, {"ok": False, "error": "denied"})  # noqa: SLF001
    assert svc._command_acks == {}


def test_ack_history_is_bounded() -> None:
    """A long-running process must not accumulate acks forever."""
    svc = _bridge()
    for i in range(200):
        svc._record_command_ack(  # noqa: SLF001
            {"cmd": "HALT", "request_id": f"r{i}"}, {"ok": True}
        )
    assert len(svc._command_acks) <= 64
    assert "r199" in svc._command_acks, "the newest ack must survive"
    assert "r0" not in svc._command_acks, "the oldest should be evicted"


def test_denied_command_logs_a_warning(caplog) -> None:
    """A refusal must be visible in the log, not silently dropped."""
    import logging

    svc = _bridge()
    with caplog.at_level(logging.WARNING, logger="ui_bridge"):
        svc._record_command_ack(  # noqa: SLF001
            {"cmd": "RESUME", "request_id": "r4"}, {"ok": False, "error": "denied"}
        )
    assert any("RESUME" in r.message % r.args if r.args else "RESUME" in r.message
               for r in caplog.records)


def test_the_zmq_loop_actually_records_the_ack() -> None:
    """Wires the recorder into the loop.

    The loop cannot be executed here because `pyzmq` is an optional dependency
    and is not installed in CI, so this asserts the connection by inspection
    rather than pretending to cover the socket path.
    """
    import inspect

    loop = inspect.getsource(UiBridgeService._zmq_command_loop)  # noqa: SLF001
    assert "await self.handle_command(data)" in loop
    assert "_record_command_ack" in loop, (
        "the loop discards the command result again — a denied command would be "
        "invisible"
    )
    # And it must consume the result rather than drop it.
    assert "= await self.handle_command(data)" in loop