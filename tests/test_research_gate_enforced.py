"""The research gate must actually gate.

`evaluate_live_readiness` existed, was correct, and was unreachable from the
decision path: its only callers were an operator UI command and an LLM tool whose
prompt *asked* the model not to promote an uncleared strategy. So the only thing
standing between an unvalidated strategy and live Consilium votes was a YAML
line — and the shipped config had `dual_thrust: observe_only: false` armed while
its own research snapshot cleared 0 of 28 entries (28/28 `ci_crosses_zero: true`,
best holdout 8 trades against a required 20, stale since 2026-08-06).

The gate is now enforced at Consilium start-up. These tests pin that, and pin the
escape hatch, so it cannot silently regress back to advisory.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.research_gate import (  # noqa: E402
    LiveReadinessPolicy,
    evaluate_live_readiness,
)
from services.ensemble_service import EnsembleService  # noqa: E402


def _service(*, strategies: list[str], enforce: bool = True, observe: list[str] | None = None):
    core = MagicMock()
    core.trading = MagicMock()
    trading = {
        "ensemble": {
            "enabled": True,
            "strategies": list(strategies),
            "observe": list(observe or []),
            "weight_mode": "equal",
        },
        "live_readiness": {"enforce": enforce},
    }
    core.config.get = AsyncMock(
        side_effect=lambda key, default=None: (
            trading if key == "trading" else "test" if key == "instance.id" else default
        )
    )
    core.plugin_manager._instances = {}
    core.rule_engine = None
    core.event_bus = MagicMock()
    published: list = []

    async def _publish(event) -> None:
        published.append(event)

    core.event_bus.publish = _publish
    core.event_bus.subscribe = AsyncMock()
    core.event_bus.unsubscribe = AsyncMock()

    svc = EnsembleService(core, "ensemble_service")
    svc.config = dict(svc.default_config())
    svc.config["rebalance_sec"] = 0
    svc._ensemble_cfg = dict(trading["ensemble"])
    return svc, published


# ---------------------------------------------------------------------------
# policy defaults
# ---------------------------------------------------------------------------


def test_enforce_defaults_to_on() -> None:
    """Fail-closed is the whole point; the default must not be advisory."""
    assert LiveReadinessPolicy().enforce is True


def test_enforce_can_be_read_from_mapping() -> None:
    assert LiveReadinessPolicy.from_mapping({"enforce": False}).enforce is False
    assert LiveReadinessPolicy.from_mapping(None).enforce is True


# ---------------------------------------------------------------------------
# enforcement at Consilium start-up
# ---------------------------------------------------------------------------


def test_uncleared_members_are_demoted_to_observe(tmp_path) -> None:
    svc, published = _service(strategies=["trend", "momentum"], enforce=True)
    kept = asyncio.run(svc._enforce_research_gate(["trend", "momentum"]))  # noqa: SLF001

    assert kept == [], "nothing on disk has clearance, so nothing may trade live"
    assert set(svc._ensemble_cfg["observe"]) == {"trend", "momentum"}  # noqa: SLF001
    # Still evaluated, just not traded.
    assert svc._enabled() is True


def test_demotion_publishes_a_reject_with_reasons() -> None:
    svc, published = _service(strategies=["trend"], enforce=True)
    asyncio.run(svc._enforce_research_gate(["trend"]))  # noqa: SLF001

    rejects = [e for e in published if e.event_type == "ensemble.reject"]
    assert rejects, "a silent demotion is indistinguishable from a bug"
    payload = dict(rejects[0].payload)
    assert payload["reason"] == "research_gate_demoted"
    assert payload["demoted"][0]["strategy_id"] == "trend"
    assert payload["demoted"][0]["reason"]


def test_enforcement_can_be_disabled_explicitly() -> None:
    svc, _ = _service(strategies=["trend", "momentum"], enforce=False)
    kept = asyncio.run(svc._enforce_research_gate(["trend", "momentum"]))  # noqa: SLF001
    assert kept == ["trend", "momentum"]
    assert svc._ensemble_cfg["observe"] == []  # noqa: SLF001


def test_empty_lineup_is_a_noop() -> None:
    svc, published = _service(strategies=[], enforce=True)
    assert asyncio.run(svc._enforce_research_gate([])) == []  # noqa: SLF001
    assert published == []


def test_existing_observe_roster_is_preserved() -> None:
    svc, _ = _service(
        strategies=["trend"], observe=["bollinger_reversion"], enforce=True
    )
    asyncio.run(svc._enforce_research_gate(["trend"]))  # noqa: SLF001
    assert "bollinger_reversion" in svc._ensemble_cfg["observe"]  # noqa: SLF001
    assert "trend" in svc._ensemble_cfg["observe"]  # noqa: SLF001


def test_a_broken_gate_denies_rather_than_promotes(tmp_path, monkeypatch) -> None:
    """An unusable policy must not fall open."""
    import engine.research_gate as gate

    svc, _ = _service(strategies=["trend"], enforce=True)

    def _boom(cls, raw):
        raise RuntimeError("policy unreadable")

    monkeypatch.setattr(gate.LiveReadinessPolicy, "from_mapping", classmethod(_boom))
    assert asyncio.run(svc._enforce_research_gate(["trend"])) == []  # noqa: SLF001


def test_cleared_member_is_kept(tmp_path, monkeypatch) -> None:
    """The gate must not be a blanket 'demote everything'."""
    import engine.research_gate as gate

    svc, published = _service(strategies=["proven"], enforce=True)
    monkeypatch.setattr(
        gate,
        "evaluate_live_readiness",
        lambda sid, **kw: {
            "live_ok": True,
            "blockers": [],
            "grade": "live_ok",
            "score": 1.0,
        },
    )
    assert asyncio.run(svc._enforce_research_gate(["proven"])) == ["proven"]  # noqa: SLF001
    assert svc._ensemble_cfg.get("observe") == []  # noqa: SLF001
    assert published == [], "a clean pass must not emit a demotion"


def test_gate_run_is_actually_consulted(tmp_path, monkeypatch) -> None:
    """Guards against a future refactor dropping the call entirely."""
    import engine.research_gate as gate

    seen: list[str] = []

    def _spy(sid, **kw):
        seen.append(sid)
        return {"live_ok": True, "blockers": [], "grade": "live_ok", "score": 1.0}

    monkeypatch.setattr(gate, "evaluate_live_readiness", _spy)
    svc, _ = _service(strategies=["a", "b"], enforce=True)
    asyncio.run(svc._enforce_research_gate(["a", "b"]))  # noqa: SLF001
    assert sorted(seen) == ["a", "b"]


# ---------------------------------------------------------------------------
# the real gate verdict for the shipped research snapshot
# ---------------------------------------------------------------------------


def test_the_audit_finding_still_holds() -> None:
    """A strategy with no clearance must not report live_ok.

    Uses a strategy id that can never have clearance so the assertion is
    deterministic. An earlier version of this test read `dual_thrust`'s real
    clearance file and asserted on the blocker *name* — it started failing the
    moment real research was run, because the gate's job is precisely to change
    its verdict as evidence accumulates. Asserting on mutable production state
    made the test measure the research programme, not the gate.
    """
    verdict = evaluate_live_readiness(
        "strategy_that_does_not_exist", policy=LiveReadinessPolicy()
    )
    assert verdict["live_ok"] is False
    assert verdict["grade"] == "research_only"
    reasons = {b["reason"] for b in verdict["blockers"]}
    assert "no_holdout_clearance" in reasons


def test_fresh_evidence_can_clear_a_strategy(tmp_path) -> None:
    """The gate must be capable of saying yes, otherwise enforce means deny-all.

    Writes a synthetic clearance entry that satisfies every criterion and checks
    the gate flips to `live_ok`. This is the counterpart to the test above: the
    gate blocks by default and opens on evidence.
    """
    from datetime import datetime, timedelta

    import json

    now = datetime.now().astimezone()
    entry = {
        "strategy_id": "synthetic",
        "ticker": "SYNTHUSDT",
        "holdout_bars": 200,
        "holdout_pnl": 120.0,
        "holdout_trades": 24,
        "robust_sign_match": True,
        "mean_oos_pnl": 40.0,
        "ci_crosses_zero": False,
        "ci_low": 5.0,
        "ci_high": 75.0,
        "profit_factor": 1.8,
        "total_oos_trades": 40,
        "timeframe": "1d",
        "bar_interval_sec": 86400.0,
        "provider_resolved": "ccxt",
        "variant_mode": "grid",
        "variants_count": 3,
        "chosen": {"lookback": 8},
        "recorded_at": now.isoformat(),
        # Evidence ends today, so freshness holds.
        "data_end": (now - timedelta(hours=2)).isoformat(),
        "data_start": (now - timedelta(days=300)).isoformat(),
        "report_path": None,
    }
    entries = {
        f"synthetic::{ticker}": {**entry, "ticker": ticker}
        for ticker in ("SYNTHUSDT", "SYNTH2USDT")
    }
    path = tmp_path / "clearance.json"
    path.write_text(
        json.dumps({"updated_at": now.isoformat(), "entries": entries}),
        encoding="utf-8",
    )
    import engine.research_gate as gate

    gate.clear_clearance_cache()
    verdict = evaluate_live_readiness(
        "synthetic",
        tickers=["SYNTHUSDT", "SYNTH2USDT"],
        path=path,
        policy=LiveReadinessPolicy(),
    )
    assert verdict["live_ok"] is True, f"blockers: {verdict['blockers']}"


def test_stale_evidence_does_not_clear_even_when_recorded_today(tmp_path) -> None:
    """A clearance re-run today on two-month-old bars must still be refused.

    Freshness is judged on the last bar the analysis saw, not on when it ran —
    otherwise re-running walk-forward on a stale cache renews clearance forever
    without ever looking at a new price.
    """
    import json
    from datetime import datetime, timedelta

    now = datetime.now().astimezone()
    entry = {
        "strategy_id": "s",
        "ticker": "STALEUSDT",
        "holdout_bars": 200,
        "holdout_pnl": 120.0,
        "holdout_trades": 24,
        "robust_sign_match": True,
        "mean_oos_pnl": 40.0,
        "ci_crosses_zero": False,
        "ci_low": 5.0,
        "ci_high": 75.0,
        "profit_factor": 1.8,
        "total_oos_trades": 40,
        "recorded_at": now.isoformat(),
        "data_end": (now - timedelta(days=90)).isoformat(),
    }
    path = tmp_path / "stale.json"
    path.write_text(
        json.dumps({"updated_at": now.isoformat(), "entries": {"s::STALEUSDT": entry}}),
        encoding="utf-8",
    )
    import engine.research_gate as gate

    gate.clear_clearance_cache()
    verdict = evaluate_live_readiness(
        "s", tickers=["STALEUSDT"], path=path, policy=LiveReadinessPolicy()
    )
    assert verdict["live_ok"] is False
    reasons = {b["reason"] for b in verdict["blockers"]}
    assert "clearance_fresh" in reasons, f"got {reasons}"