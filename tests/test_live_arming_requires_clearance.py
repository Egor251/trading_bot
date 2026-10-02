"""Arming live must fail loudly when nothing is cleared to trade.

Every `LivePreflight` gate checks plumbing — operator, confirmations, cost
schedule, reconciliation, market data, instrument spec, broker capability,
sandbox acceptance. Not one of them asks the only question that determines
whether the next minute can produce a trade: *is any strategy cleared for live?*

So arming live used to succeed with an empty live Consilium lineup. The research
gate demotes members that have not cleared holdout validation, and when the only
configured member is demoted the lineup is empty. Live was armed, no order was
ever placed, and nothing said why — which reads exactly like a broker or venue
fault and costs an afternoon of debugging the wrong subsystem.

`_live_lineup_blocker` makes the refusal explicit and names the specific gate
reasons per strategy, so the operator is told what to fix.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from services.ui_bridge_service import UiBridgeService  # noqa: E402


def _bridge(*, lineup: list[str] | None, ensemble: bool = True):
    core = MagicMock()
    raw_cfg: dict = {
        "instance": {"id": "local"},
        "trading": {"mode": "paper", "equity": 10_000.0, "risk": {}},
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
    core.plugin_manager = MagicMock()
    core.plugin_manager.get_plugin = MagicMock(return_value=None)
    runtime = MagicMock()
    runtime.mode = "paper"
    runtime.orders = MagicMock()
    runtime.orders.mode = "paper"
    core.trading = runtime

    ens = None
    if ensemble:
        ens = MagicMock()
        ens.lineup_ids = MagicMock(return_value=list(lineup or []))
    core.service_registry.get = MagicMock(
        side_effect=lambda sid: ens if sid == "ensemble_service" else None
    )
    # Authorizer installed so the request passes the confirm gate and reaches the
    # lineup check under test.
    core.__dict__["control_authorizer"] = MagicMock()
    svc = UiBridgeService(core, {"host": "127.0.0.1", "port": 0})
    return svc, raw_cfg


# ---------------------------------------------------------------------------
# the refusal itself
# ---------------------------------------------------------------------------


def test_empty_lineup_blocks_live_arming() -> None:
    svc, raw_cfg = _bridge(lineup=[])
    out = asyncio.run(svc._cmd_set_trading({"mode": "live", "confirm_live": True}))
    assert out["ok"] is False
    assert out["error"] == "live_lineup_not_tradeable"
    assert raw_cfg["trading"]["mode"] == "paper", "mode must not change on refusal"


def test_uncleared_member_blocks_with_its_reasons() -> None:
    import engine.research_gate as gate

    svc, raw_cfg = _bridge(lineup=["dual_thrust"])
    original = gate.evaluate_live_readiness
    gate.evaluate_live_readiness = lambda sid, **kw: {
        "live_ok": False,
        "grade": "observe_ok",
        "score": 0.5,
        "blockers": [{"reason": "ci_excludes_zero"}, {"reason": "clearance_fresh"}],
    }
    try:
        out = asyncio.run(svc._cmd_set_trading({"mode": "live", "confirm_live": True}))
    finally:
        gate.evaluate_live_readiness = original

    assert out["ok"] is False
    assert out["error"] == "live_lineup_not_tradeable"
    assert raw_cfg["trading"]["mode"] == "paper"
    # The operator must be told *which* strategy and *why*, not just "denied".
    assert out["blockers"] == [
        {"strategy_id": "dual_thrust", "reasons": ["ci_excludes_zero", "clearance_fresh"]}
    ]
    assert out["lineup"] == ["dual_thrust"]
    assert "holdout_bars" in out["detail"]


def test_missing_ensemble_service_fails_closed() -> None:
    svc, raw_cfg = _bridge(lineup=None, ensemble=False)
    out = asyncio.run(svc._cmd_set_trading({"mode": "live", "confirm_live": True}))
    assert out["ok"] is False
    assert out["error"] == "live_lineup_not_tradeable"
    assert raw_cfg["trading"]["mode"] == "paper"


def test_an_unreadable_gate_fails_closed(monkeypatch) -> None:
    """An exception while reading the gate must not fall open into live."""
    import engine.research_gate as gate

    svc, raw_cfg = _bridge(lineup=["dual_thrust"])
    monkeypatch.setattr(
        gate.LiveReadinessPolicy,
        "from_mapping",
        classmethod(lambda cls, raw: (_ for _ in ()).throw(RuntimeError("boom"))),
    )
    out = asyncio.run(svc._cmd_set_trading({"mode": "live", "confirm_live": True}))
    assert out["ok"] is False
    assert out["error"] == "live_lineup_not_tradeable"
    assert raw_cfg["trading"]["mode"] == "paper"


def test_a_gate_error_is_reported_per_strategy(monkeypatch) -> None:
    import engine.research_gate as gate

    svc, _ = _bridge(lineup=["broken"])

    def _boom(sid, **kw):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(gate, "evaluate_live_readiness", _boom)
    out = asyncio.run(svc._cmd_set_trading({"mode": "live", "confirm_live": True}))
    assert out["ok"] is False
    assert out["blockers"] == [
        {"strategy_id": "broken", "reasons": ["gate_error:RuntimeError"]}
    ]


# ---------------------------------------------------------------------------
# the happy path still works
# ---------------------------------------------------------------------------


def test_a_cleared_lineup_passes_the_lineup_check() -> None:
    """The refusal must not fire when every member is cleared.

    This stub has no `live_preflight`, so arming still stops at
    `live_preflight_unavailable` — which is the point: the request gets *past*
    the clearance check and on to the next gate instead of being blocked here.
    """
    import engine.research_gate as gate

    svc, raw_cfg = _bridge(lineup=["proven"])
    original = gate.evaluate_live_readiness
    gate.evaluate_live_readiness = lambda sid, **kw: {
        "live_ok": True,
        "grade": "live_ok",
        "score": 1.0,
        "blockers": [],
    }
    try:
        out = asyncio.run(svc._cmd_set_trading({"mode": "live", "confirm_live": True}))
    finally:
        gate.evaluate_live_readiness = original
    assert out.get("error") != "live_lineup_not_tradeable", (
        f"a cleared lineup must not be refused: {out}"
    )
    assert out.get("error") == "live_preflight_unavailable"
    assert raw_cfg["trading"]["mode"] == "paper"


def test_paper_mode_is_never_blocked() -> None:
    """The check must not interfere with switching *to* paper/sim."""
    svc, raw_cfg = _bridge(lineup=[])
    out = asyncio.run(svc._cmd_set_trading({"mode": "paper"}))
    assert out["ok"] is True


def test_confirm_gate_still_fires_before_the_lineup_check() -> None:
    """Authorisation of the request is settled before we discuss the lineup."""
    core = MagicMock()
    raw_cfg: dict = {
        "instance": {"id": "local"},
        "trading": {"mode": "paper", "equity": 10_000.0, "risk": {}},
        "plugins": {},
        "services": {},
        "core": {},
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
    core.plugin_manager = MagicMock()
    ens = MagicMock()
    ens.lineup_ids = MagicMock(return_value=[])
    core.service_registry.get = MagicMock(
        side_effect=lambda sid: ens if sid == "ensemble_service" else None
    )
    runtime = MagicMock()
    runtime.mode = "paper"
    core.trading = runtime
    svc = UiBridgeService(core, {"host": "127.0.0.1", "port": 0})

    out = asyncio.run(svc._cmd_set_trading({"mode": "live"}))
    assert out["error"] == "confirm_live_required", (
        "the empty lineup must not mask the missing operator confirmation"
    )