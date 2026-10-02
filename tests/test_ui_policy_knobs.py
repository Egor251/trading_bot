"""Risk and execution policy must be settable from the UI, and bounded.

Every policy decision an operator owns was previously reachable only by editing
YAML: `strength_floor` decides whether a strategy's signal can open a position
at all, and there was no command for it — the one dial with no affordance. The
new `SET_RISK` / `SET_EXECUTION` commands close that, and every value is range
checked, because a limit set to a meaningless number (negative daily loss,
concentration above 1.0) is worse than an unset one: it reads as deliberate.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from services.ui_bridge_service import _EXECUTION_KNOBS, _RISK_KNOBS  # noqa: E402
from services.ui_bridge_service import UiBridgeService  # noqa: E402


def _bridge(tmp_path: Path, *, live_runtime: bool = False):
    core = MagicMock()
    core.config.raw = {"trading": {"risk": {}, "mode": "paper"}}
    core.config.get = AsyncMock(return_value="local")
    core.config.save = AsyncMock()

    risk = MagicMock()
    limits = MagicMock()
    risk.limits_for = MagicMock(return_value=limits)

    runtime = MagicMock()
    runtime.risk = risk
    runtime.mode = "live" if live_runtime else "paper"
    runtime.open_orders = MagicMock(return_value=[])

    phantom = MagicMock()
    phantom.sizing.strength_floor = 0.25
    phantom.apply_sizing_config = MagicMock()

    core.service_registry = MagicMock()
    core.service_registry.get = MagicMock(
        side_effect=lambda name: phantom if name == "phantom_service" else None
    )
    core.trading = runtime
    vars(core)["control_authorizer"] = MagicMock()

    svc = UiBridgeService(core, "ui_bridge_service")
    return svc, core, phantom


def test_strength_floor_is_settable_from_the_ui() -> None:
    """The one policy dial with no UI affordance."""
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        svc, core, phantom = _bridge(Path(raw))

        async def scenario():
            return await svc._cmd_set_risk({"strength_floor": 0.5})

        result = asyncio.run(scenario())
        assert result["ok"], result
        assert result["applied"]["strength_floor"] == 0.5
        assert phantom.sizing.strength_floor == 0.5, (
            "the live sizer must follow the UI, not just the stored config"
        )
        assert (
            core.config.raw["trading"]["risk"]["strength_floor"] == 0.5
        ), "the value must be persisted in config"


def test_an_out_of_range_risk_value_is_rejected() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        svc, _core, _phantom = _bridge(Path(raw))
        result = asyncio.run(svc._cmd_set_risk({"max_daily_loss": -0.5}))
        assert not result["ok"]
        assert "max_daily_loss" in result["rejected"]
        assert "out_of_range" in result["rejected"]["max_daily_loss"]


def test_a_non_numeric_risk_value_is_rejected() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        svc, _core, _phantom = _bridge(Path(raw))
        result = asyncio.run(svc._cmd_set_risk({"max_leverage": "lots"}))
        assert not result["ok"]
        assert result["rejected"]["max_leverage"] == "not_a_number"


def test_an_empty_request_lists_what_can_be_set() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        svc, _core, _phantom = _bridge(Path(raw))
        result = asyncio.run(svc._cmd_set_risk({}))
        assert result["error"] == "nothing_to_set"
        assert "strength_floor" in result["editable"]


def test_execution_ttl_is_settable_and_bounded() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        svc, core, _phantom = _bridge(Path(raw))
        ok = asyncio.run(svc._cmd_set_execution({"stale_order_ttl_sec": 300}))
        assert ok["ok"], ok
        assert (
            core.config.raw["services"]["execution_service"]["stale_order_ttl_sec"] == 300
        )

        bad = asyncio.run(svc._cmd_set_execution({"stale_order_ttl_sec": 10_000_000}))
        assert not bad["ok"]
        assert "out_of_range" in bad["rejected"]["stale_order_ttl_sec"]


def test_post_only_is_toggleable_from_the_ui() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        svc, core, _phantom = _bridge(Path(raw))
        result = asyncio.run(svc._cmd_set_execution({"post_only": False}))
        assert result["ok"], result
        assert core.config.raw["services"]["execution_service"]["post_only"] is False


def test_both_commands_require_authorization() -> None:
    """Changing risk policy is an ADMIN mutation, not an analyst action."""
    from engine.control_commands import requires_authorization

    assert requires_authorization("SET_RISK")
    assert requires_authorization("SET_EXECUTION")


def test_every_editable_knob_has_a_sane_range() -> None:
    """A bound that admits nonsense is not a bound."""
    for table in (_RISK_KNOBS, _EXECUTION_KNOBS):
        assert table, "knob table must not be empty"
        for key, (low, high) in table.items():
            assert low < high, f"{key} has an empty range"
            assert low >= 0.0, f"{key} admits a negative value"
            assert high <= 100_000.0, f"{key} admits an absurd value"