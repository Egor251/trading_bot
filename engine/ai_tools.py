"""Trading-domain tools for Code_base ai_analyst.

Read tools: analyst+. Control tools: operator+ (via ``source_plugin`` RBAC).
Operator may place/close/cancel via gated tools (``confirm_live`` in live/quik).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ai_analyst.tools.base import BaseAITool

from engine.cost_workflow import (
    CostScheduleDraft,
    CostScheduleStore,
    SourceDocument,
    validate_cost_draft,
)
from engine.strategy_plugin import StrategyPlugin
from host.config_patch import operator_config_slice, redact_secrets

# RBAC: unlisted tools need source_plugin so analyst/operator plugin-tool rules apply.
_SOURCE = "trading_bot"


class EmptyParams(BaseModel):
    """No parameters."""


def _bridge(core: Any) -> Any:
    if core is None:
        return None
    return core.service_registry.get("ui_bridge_service")


def _nothing_to_set(hint: str) -> dict[str, Any]:
    return {
        "success": False,
        "error": "nothing_to_set",
        "hint": hint,
    }


def _non_empty_patch(data: dict[str, Any], *, ignore: set[str] | None = None) -> dict[str, Any]:
    skip = ignore or set()
    return {
        k: v
        for k, v in data.items()
        if k not in skip and v is not None and v != "" and v != {} and v != []
    }


async def _call_bridge(core: Any, cmd: str, **payload: Any) -> dict[str, Any]:
    """Dispatch a UI-bridge command with Operator auth + normalized result.

    Always returns ``success`` / ``ok`` / ``cmd``. Failures always include ``error``
    so digests cannot collapse into a bare ``ok``.
    """
    bridge = _bridge(core)
    if bridge is None or not hasattr(bridge, "handle_command"):
        return {"success": False, "ok": False, "error": "ui_bridge_service missing", "cmd": cmd}
    body = {"cmd": cmd, **payload}
    # Same HMAC/local-dev attachment as the Operator UI bridge client.
    try:
        from ui.control_auth_client import attach_control_auth

        body = attach_control_auth(body)
    except Exception:
        pass
    try:
        result = await bridge.handle_command(body)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "ok": False, "error": str(exc), "cmd": cmd}
    if not isinstance(result, dict):
        return {
            "success": False,
            "ok": False,
            "error": "bridge_non_dict_result",
            "cmd": cmd,
            "raw": result,
        }
    ok = bool(result.get("ok", False))
    out = {"cmd": cmd, **result, "ok": ok, "success": ok}
    if "success" in result:
        out["success"] = bool(result["success"]) and ok
    if not out["success"]:
        out["ok"] = False
        out.setdefault("error", result.get("error") or "command_failed")
    return out


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


class GetRuntimeStatusTool(BaseAITool):
    name = "get_runtime_status"
    description = (
        "Trading runtime snapshot: mode, halted, equity, exposures, positions, "
        "open_orders (includes client_id for cancel_order), risk."
    )
    category = "trading"
    parameters_schema = EmptyParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        runtime = getattr(core, "trading", None) if core is not None else None
        if runtime is None:
            return {"success": False, "error": "trading_runtime not ready"}
        snap = runtime.snapshot() if hasattr(runtime, "snapshot") else {}
        open_orders = list(snap.get("open_orders") or [])
        return {
            "success": True,
            "runtime": snap,
            "open_orders_count": len(open_orders),
            "open_order_client_ids": [
                str(o.get("client_id"))
                for o in open_orders
                if str(o.get("client_id") or "").strip()
            ],
            "cancel_hint": (
                "cancel_all_orders(confirm_cancel_all=true, symbol='SBER') "
                "for bulk; else cancel_order(client_id=...) per id"
                if open_orders
                else None
            ),
        }


class GetUniverseParams(BaseModel):
    include_active: bool = True


class GetUniverseTool(BaseAITool):
    name = "get_universe"
    description = "Universe selection: mode, catalog size, active keys/instruments."
    category = "trading"
    parameters_schema = GetUniverseParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        uni = core.service_registry.get("universe_service")
        if uni is None:
            return {"success": False, "error": "universe_service missing"}
        st = await uni.get_status() if hasattr(uni, "get_status") else {}
        out: dict[str, Any] = {
            "success": True,
            "mode": st.get("mode"),
            "catalog_size": st.get("catalog_size"),
            "listings_size": st.get("listings_size"),
            "active_keys": st.get("active_keys"),
            "passes": st.get("passes"),
        }
        if kwargs.get("include_active", True):
            snap = getattr(uni, "last_snapshot", None)
            if snap is not None and hasattr(snap, "to_payload"):
                payload = snap.to_payload()
                out["active"] = payload.get("active") or []
                out["counts"] = payload.get("counts") or {}
        return out


class QueryJournalParams(BaseModel):
    limit: int = Field(15, ge=1, le=100)
    event_type: str | None = Field(
        None,
        description=(
            "Optional filter, e.g. strategy.signal, ensemble.decision, ensemble.reject, "
            "risk.reject, execution.result, portfolio.snapshot, ops.alert, "
            "config.lineup_changed, reconciliation.incident"
        ),
    )


class QueryJournalTool(BaseAITool):
    name = "query_journal"
    description = "Query recent durable events from storage_service journal."
    category = "trading"
    parameters_schema = QueryJournalParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        stor = core.service_registry.get("storage_service")
        if stor is None or not hasattr(stor, "query_journal"):
            return {"success": False, "error": "storage_service missing"}
        event_type = kwargs.get("event_type")
        event_types = [event_type] if event_type else None
        rows = await stor.query_journal(
            event_types=event_types, limit=int(kwargs.get("limit") or 15)
        )
        return {"success": True, "rows": rows, "count": len(rows)}


class GetStrategyStatsParams(BaseModel):
    strategy_id: str | None = Field(
        None, description="Optional filter to one strategy id"
    )
    limit: int = Field(500, ge=20, le=5000, description="Journal rows to scan")


class GetStrategyStatsTool(BaseAITool):
    name = "get_strategy_stats"
    description = (
        "Expectancy / contribution aggregates from durable journal "
        "(by strategy, by regime, strategy×regime), plus recent fills/signals."
    )
    category = "trading"
    parameters_schema = GetStrategyStatsParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        stor = core.service_registry.get("storage_service")
        if stor is None or not hasattr(stor, "strategy_stats"):
            return {"success": False, "error": "storage_service missing"}
        sid = kwargs.get("strategy_id")
        sid_s = str(sid).strip() if sid else None
        limit = int(kwargs.get("limit") or 500)
        stats = await stor.strategy_stats(strategy_id=sid_s, limit=limit)
        recent: list[dict[str, Any]] = []
        if hasattr(stor, "query_journal"):
            try:
                from engine.research import JOURNAL_STAT_EVENT_TYPES

                rows = await stor.query_journal(
                    event_types=list(JOURNAL_STAT_EVENT_TYPES),
                    limit=min(limit, 200),
                )
                for row in reversed(list(rows or [])):
                    et = str(row.get("event_type") or "")
                    if et not in {
                        "execution.result",
                        "phantom.fills",
                        "strategy.signal",
                        "ensemble.decision",
                    }:
                        continue
                    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                    order = payload.get("order") if isinstance(payload.get("order"), dict) else {}
                    row_sid = str(
                        payload.get("strategy_id")
                        or order.get("strategy_id")
                        or row.get("source_id")
                        or ""
                    )
                    if sid_s and row_sid and row_sid != sid_s and sid_s not in str(
                        payload.get("contributors") or ""
                    ):
                        continue
                    recent.append(
                        {
                            "event_type": et,
                            "ts": row.get("ts") or row.get("timestamp"),
                            "strategy_id": row_sid,
                            "ticker": payload.get("ticker") or order.get("ticker"),
                            "action": payload.get("action") or order.get("action"),
                            "quantity": payload.get("quantity") or order.get("quantity"),
                            "price": payload.get("price") or order.get("price"),
                            "success": payload.get("success"),
                            "status": order.get("status") or payload.get("status"),
                        }
                    )
                    if len(recent) >= 40:
                        break
            except Exception as exc:  # noqa: BLE001
                recent = [{"error": str(exc)}]
        runtime = getattr(core, "trading", None)
        live_trades = []
        if runtime is not None and hasattr(runtime, "recent_trades"):
            try:
                live_trades = list(runtime.recent_trades(limit=40) or [])
                if sid_s:
                    live_trades = [
                        t
                        for t in live_trades
                        if sid_s in str(t.get("strategy_id") or "")
                        or not t.get("strategy_id")
                    ]
            except Exception:
                live_trades = []
        return {
            "success": True,
            "stats": stats,
            "recent_journal": recent,
            "recent_runtime_trades": live_trades,
        }


class HistorySummaryParams(BaseModel):
    ticker: str = Field(..., description="Symbol, e.g. SBER")
    class_code: str = "TQBR"
    provider: str | None = Field(None, description="yahoo or moex")
    limit: int = Field(40, ge=5, le=200)


class HistorySummaryTool(BaseAITool):
    name = "history_summary"
    description = (
        "Fetch OHLCV summary (price, dollar_volume, atr_pct) via historical_data_service."
    )
    category = "trading"
    parameters_schema = HistorySummaryParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        hist = core.service_registry.get("historical_data_service")
        if hist is None or not hasattr(hist, "metrics_for"):
            return {"success": False, "error": "historical_data_service missing"}
        try:
            metrics = await hist.metrics_for(
                str(kwargs["ticker"]),
                class_code=str(kwargs.get("class_code") or "TQBR"),
                provider=kwargs.get("provider"),
                limit=int(kwargs.get("limit") or 40),
            )
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}
        return {"success": bool(metrics.get("ok")), "metrics": metrics}


class ListStrategiesTool(BaseAITool):
    name = "list_strategies"
    description = (
        "List strategy plugins with running/observe/live mode, health, and last signal."
    )
    category = "trading"
    parameters_schema = EmptyParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        pm = getattr(core, "plugin_manager", None)
        if pm is None:
            return {"success": False, "error": "plugin_manager missing"}
        ens = core.service_registry.get("ensemble_service")
        ens_cfg = dict(getattr(ens, "_ensemble_cfg", {}) or {}) if ens else {}
        live = {str(x) for x in (ens_cfg.get("strategies") or [])}
        observe = {str(x) for x in (ens_cfg.get("observe") or [])}
        members = {}
        if ens is not None and hasattr(ens, "members_for_ui"):
            try:
                members = {
                    str(m.get("strategy_id")): m for m in (ens.members_for_ui() or [])
                }
            except Exception:
                members = {}
        rows = []
        for plugin_id, instance in getattr(pm, "plugins", {}).items():
            if not isinstance(instance, StrategyPlugin):
                continue
            sid = str(plugin_id)
            observe_only = bool(
                getattr(instance, "is_observe_only", lambda: False)()
                or sid in observe
            )
            if sid in live:
                mode = "live"
            elif observe_only or sid in observe:
                mode = "observe"
            elif getattr(instance, "is_running", False):
                mode = "running"
            else:
                mode = "off"
            health = None
            if hasattr(instance, "health"):
                try:
                    h = instance.health()
                    health = h.__dict__ if hasattr(h, "__dict__") else dict(h or {})
                except Exception:
                    health = None
            desc = instance.describe() if hasattr(instance, "describe") else []
            rows.append(
                {
                    "id": sid,
                    "running": bool(getattr(instance, "is_running", False)),
                    "mode": mode,
                    "observe_only": observe_only,
                    "describe": desc,
                    "health": health,
                    "operator_view": (
                        instance.operator_view()
                        if hasattr(instance, "operator_view")
                        else {}
                    ),
                    "member": members.get(sid),
                    "config": dict(getattr(instance, "config", {}) or {}),
                }
            )
        return {
            "success": True,
            "strategies": rows,
            "lineup": sorted(live),
            "observe": sorted(observe),
        }


class GetEnsembleStatusTool(BaseAITool):
    name = "get_ensemble_status"
    description = (
        "Consilium status: lineup, observe list, weights, last decision, member votes."
    )
    category = "trading"
    parameters_schema = EmptyParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        ens = core.service_registry.get("ensemble_service")
        if ens is None:
            return {"success": False, "error": "ensemble_service missing"}
        st = await ens.get_status() if hasattr(ens, "get_status") else {}
        cfg = dict(getattr(ens, "_ensemble_cfg", {}) or {})
        return {
            "success": True,
            "enabled": cfg.get("enabled", True),
            "strategies": list(cfg.get("strategies") or []),
            "observe": list(cfg.get("observe") or []),
            "weight_mode": cfg.get("weight_mode") or st.get("weight_mode"),
            "weights": st.get("weights")
            or getattr(getattr(ens, "manager", None), "weights", {}),
            "decisions": st.get("decisions"),
            "last_decision": st.get("last_decision"),
            "members": st.get("members") or [],
            "stats": st.get("stats")
            or (
                ens.manager.stats.snapshot(list(cfg.get("strategies") or []))
                if getattr(ens, "manager", None) is not None
                and hasattr(ens.manager, "stats")
                else {}
            ),
            "regime": st.get("regime") or getattr(ens, "_last_regime", "unknown"),
        }


class GetOpsStatusTool(BaseAITool):
    name = "get_ops_status"
    description = "Ops monitor: heartbeats, recent alerts, performance snapshot."
    category = "trading"
    parameters_schema = EmptyParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        ops = core.service_registry.get("ops_monitor_service")
        if ops is None:
            return {"success": False, "error": "ops_monitor_service missing"}
        st = await ops.get_status() if hasattr(ops, "get_status") else {}
        alerts = []
        bus = getattr(ops, "alerts", None)
        if bus is not None and hasattr(bus, "recent"):
            try:
                alerts = [a.to_dict() for a in bus.recent(20)]
            except Exception:
                alerts = []
        return {"success": True, "status": st, "recent_alerts": alerts}


class GetStorageStatusTool(BaseAITool):
    name = "get_storage_status"
    description = "Durable storage routing, backends health, sink counters."
    category = "trading"
    parameters_schema = EmptyParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        stor = core.service_registry.get("storage_service")
        if stor is None or not hasattr(stor, "operator_snapshot"):
            return {"success": False, "error": "storage_service missing"}
        snap = await stor.operator_snapshot()
        return {"success": True, "storage": snap}


class GetPhantomStatusTool(BaseAITool):
    name = "get_phantom_status"
    description = "Phantom/shadow ledger status: fills, metrics, capture flag, last run."
    category = "trading"
    parameters_schema = EmptyParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        ph = core.service_registry.get("phantom_service")
        if ph is None:
            return {"success": False, "error": "phantom_service missing"}
        st = await ph.get_status() if hasattr(ph, "get_status") else {}
        return {"success": True, "phantom": st}


class GetRiskLimitsTool(BaseAITool):
    name = "get_risk_limits"
    description = (
        "Current global risk limits, per-strategy overrides, protection profiles, "
        "and insurance/halt state."
    )
    category = "trading"
    parameters_schema = EmptyParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        runtime = getattr(core, "trading", None) if core is not None else None
        if runtime is None:
            return {"success": False, "error": "trading_runtime not ready"}
        risk = getattr(runtime, "risk", None)
        insurance = getattr(runtime, "insurance", None)
        profiles = {}
        for sid, prof in (getattr(runtime, "strategy_profiles", None) or {}).items():
            if hasattr(prof, "to_dict"):
                profiles[str(sid)] = prof.to_dict()
            else:
                profiles[str(sid)] = {
                    "archetype": str(getattr(prof, "archetype", "")),
                    "max_loss_fraction": str(getattr(prof, "max_loss_fraction", "")),
                }
        return {
            "success": True,
            "halted": bool(getattr(runtime, "halted", False)),
            "halt_reason": getattr(runtime, "_halt_reason", "") or "",
            "risk": risk.snapshot() if risk is not None and hasattr(risk, "snapshot") else {},
            "strategy_profiles": profiles,
            "insurance": (
                insurance.snapshot()
                if insurance is not None and hasattr(insurance, "snapshot")
                else {}
            ),
        }


class GetStrategyRiskLimitsParams(BaseModel):
    strategy_id: str | None = Field(
        None, description="If set, return effective merged limits for this strategy"
    )


class GetStrategyRiskLimitsTool(BaseAITool):
    name = "get_strategy_risk_limits"
    description = (
        "Per-strategy risk limit overrides and effective merged limits "
        "(global ⊕ strategy). Use before diagnosing 130x-style sizing failures."
    )
    category = "trading"
    parameters_schema = GetStrategyRiskLimitsParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        from dataclasses import asdict

        core = self.get_dependency("core", required=False)
        runtime = getattr(core, "trading", None) if core is not None else None
        if runtime is None:
            return {"success": False, "error": "trading_runtime not ready"}
        risk = getattr(runtime, "risk", None)
        if risk is None:
            return {"success": False, "error": "risk manager missing"}
        sid = str(kwargs.get("strategy_id") or "").strip() or None
        snap = risk.snapshot() if hasattr(risk, "snapshot") else {}
        overrides = dict(snap.get("strategy_limits") or {})
        if sid:
            effective = risk.limits_for(sid) if hasattr(risk, "limits_for") else risk.limits
            return {
                "success": True,
                "strategy_id": sid,
                "override": overrides.get(sid) or {},
                "effective": asdict(effective),
                "global": asdict(risk.limits),
            }
        return {
            "success": True,
            "global": asdict(risk.limits),
            "overrides": overrides,
        }


class SetStrategyRiskLimitsParams(BaseModel):
    strategy_id: str
    max_position_pct: float | None = None
    max_leverage: float | None = None
    max_strategy_drawdown: float | None = None
    max_strategy_drawdown_abs: float | None = None
    max_portfolio_drawdown: float | None = None
    max_daily_loss_pct: float | None = None
    clear: bool = Field(False, description="Remove override for this strategy")
    persist: bool = True


class SetStrategyRiskLimitsTool(BaseAITool):
    name = "set_strategy_risk_limits"
    description = (
        "Set or clear per-strategy risk limit overrides (merged over global). "
        "Key lever against oversized positions for one strategy."
    )
    category = "trading"
    parameters_schema = SetStrategyRiskLimitsParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        sid = str(kwargs.get("strategy_id") or "").strip()
        if not sid:
            return {"success": False, "error": "strategy_id_required"}
        persist = bool(kwargs.pop("persist", True))
        clear = bool(kwargs.pop("clear", False))
        patch = _non_empty_patch(kwargs, ignore={"strategy_id", "persist", "clear"})
        if not clear and not patch:
            return _nothing_to_set(
                "Pass at least one limit field, or clear=true to remove overrides."
            )
        payload: dict[str, Any] = {"strategy_id": sid, "clear": clear, **patch}
        result = await _call_bridge(
            core, "SET_TRADING", strategy_risk=payload, persist=persist
        )
        if result.get("success"):
            result["strategy_id"] = sid
            result["clear"] = clear
            result["patch"] = patch
        return result


class GetReconciliationStatusTool(BaseAITool):
    name = "get_reconciliation_status"
    description = "Broker reconciliation health and active incidents."
    category = "trading"
    parameters_schema = EmptyParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        exe = core.service_registry.get("execution_service")
        if exe is None:
            return {"success": False, "error": "execution_service missing"}
        st = await exe.get_status() if hasattr(exe, "get_status") else {}
        recon = getattr(exe, "reconciliation", None)
        incidents: list[dict[str, Any]] = []
        if recon is not None and hasattr(recon, "active_incidents"):
            try:
                for item in recon.active_incidents:
                    if hasattr(item, "to_dict"):
                        incidents.append(item.to_dict())
                    else:
                        incidents.append({"repr": str(item)})
            except Exception:
                incidents = []
        return {
            "success": True,
            "status": st,
            "active_incidents": incidents,
        }


class GetConfigSliceTool(BaseAITool):
    name = "get_config_slice"
    description = (
        "Redacted operator config slice (trading/universe/ensemble/storage/plugins)."
    )
    category = "trading"
    parameters_schema = EmptyParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        raw = getattr(getattr(core, "config", None), "raw", None)
        if not isinstance(raw, dict):
            return {"success": False, "error": "no_config"}
        return {
            "success": True,
            "config": redact_secrets(operator_config_slice(raw)),
        }


class ListServicesStatusTool(BaseAITool):
    name = "list_services_status"
    description = "Running state of key engine services (runtime, ensemble, storage, …)."
    category = "trading"
    parameters_schema = EmptyParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        names = [
            "trading_runtime_service",
            "universe_service",
            "ensemble_service",
            "storage_service",
            "ops_monitor_service",
            "phantom_service",
            "execution_service",
            "ui_bridge_service",
            "live_safety_service",
        ]
        rows = []
        for name in names:
            svc = core.service_registry.get(name)
            row: dict[str, Any] = {
                "id": name,
                "present": svc is not None,
                "running": bool(svc and getattr(svc, "is_running", False)),
            }
            if svc is not None and hasattr(svc, "get_status"):
                try:
                    st = await svc.get_status()
                    # keep compact
                    row["status_keys"] = sorted(st.keys())[:20]
                except Exception as exc:  # noqa: BLE001
                    row["status_error"] = str(exc)
            rows.append(row)
        return {"success": True, "services": rows}


def _cost_store(core: Any) -> CostScheduleStore:
    raw = getattr(getattr(core, "config", None), "raw", {}) or {}
    configured = ((raw.get("costs") or {}).get("store_path")) if isinstance(raw, dict) else None
    return CostScheduleStore(Path(configured or "data/cost_schedules"))


class ReadCostScheduleTool(BaseAITool):
    name = "read_cost_schedule"
    description = (
        "Read the activated cost schedule and the schedule actually used by "
        "TradingRuntime (including legacy-bps fallback when no draft is active). "
        "Includes a human-readable summary of rules by instrument/class match."
    )
    category = "trading"
    parameters_schema = EmptyParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        from engine.trade_ledger import summarize_cost_schedule

        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        store = _cost_store(core)
        active = store.active_record()
        schedule = store.load_active_schedule()
        runtime_effective = store.load_runtime_effective()
        runtime = getattr(core, "trading", None)
        live_schedule = getattr(getattr(runtime, "orders", None), "cost_schedule", None)
        if live_schedule is not None and hasattr(live_schedule, "to_dict"):
            live_dict = live_schedule.to_dict()
            live_version = getattr(live_schedule, "version", None)
        else:
            live_dict, live_version = None, None
        source = (
            "store_active"
            if schedule is not None
            else "runtime_effective"
            if runtime_effective is not None or live_dict is not None
            else None
        )
        schedule_dict = schedule.to_dict() if schedule else None
        effective_dict = (
            runtime_effective.to_dict()
            if runtime_effective is not None
            else live_dict
        )
        summary_src = schedule_dict or effective_dict
        return {
            "success": True,
            "active": active.model_dump(mode="json") if active is not None else None,
            "schedule": schedule_dict,
            "runtime_effective": effective_dict,
            "runtime_version": (
                getattr(runtime_effective, "version", None)
                if runtime_effective is not None
                else live_version
            ),
            "source": source,
            "summary": summarize_cost_schedule(summary_src, source=source),
        }


class ProposeCostScheduleParams(BaseModel):
    source: SourceDocument
    draft: CostScheduleDraft
    impact_contexts: list[dict[str, Any]] = Field(default_factory=list, max_length=100)

    model_config = {"extra": "forbid"}


class ProposeCostScheduleTool(BaseAITool):
    name = "propose_cost_schedule"
    description = (
        "Propose and deterministically validate grounded trading-cost rules. "
        "Does not save or activate configuration."
    )
    category = "trading"
    parameters_schema = ProposeCostScheduleParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        try:
            source = SourceDocument.model_validate(kwargs["source"])
            draft = CostScheduleDraft.model_validate(kwargs["draft"])
            active = _cost_store(core).load_active_schedule()
            report = validate_cost_draft(
                source,
                draft,
                active=active,
                impact_contexts=kwargs.get("impact_contexts") or (),
            )
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}
        return {
            "success": True,
            "proposal_only": True,
            "validation": report.model_dump(mode="json"),
            "draft": draft.model_dump(mode="json"),
        }


# ---------------------------------------------------------------------------
# Control (operator+)
# ---------------------------------------------------------------------------


class GetTradeHistoryParams(BaseModel):
    strategy_id: str | None = Field(None, description="Optional strategy filter")
    symbol: str | None = Field(None, description="Optional CLASS:TICKER or ticker")
    limit: int = Field(50, ge=1, le=500)


class GetTradeHistoryTool(BaseAITool):
    name = "get_trade_history"
    description = (
        "Recent fills / execution results from journal (not aggregates). "
        "Includes strategy_id, best-effort pnl/costs. "
        "For closed entry/exit rounds use get_strategy_trades."
    )
    category = "trading"
    parameters_schema = GetTradeHistoryParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        from engine.research import estimate_fill_pnl
        from engine.trade_ledger import normalize_fill_row

        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        stor = core.service_registry.get("storage_service")
        if stor is None or not hasattr(stor, "query_journal"):
            return {"success": False, "error": "storage_service missing"}
        limit = int(kwargs.get("limit") or 50)
        sid = str(kwargs.get("strategy_id") or "").strip() or None
        symbol = str(kwargs.get("symbol") or "").strip() or None
        rows = await stor.query_journal(
            event_types=["execution.result", "phantom.fills", "order.fill"],
            limit=min(max(limit * 4, 50), 2000),
        )
        trades: list[dict[str, Any]] = []
        for row in reversed(list(rows or [])):
            norm = normalize_fill_row(row if isinstance(row, dict) else {})
            if norm is None:
                continue
            if sid and norm.get("strategy_id") != sid:
                continue
            key = str(norm.get("symbol") or "")
            ticker = key.split(":")[-1]
            if symbol and symbol not in {ticker, key}:
                if not (ticker and symbol.endswith(ticker)):
                    continue
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            if norm.get("pnl") is None:
                norm["pnl"] = estimate_fill_pnl(payload if isinstance(payload, dict) else {})
            trades.append(norm)
            if len(trades) >= limit:
                break
        runtime = getattr(core, "trading", None)
        book_fills: list[dict[str, Any]] = []
        if runtime is not None and hasattr(runtime, "orders"):
            for fill in list(
                getattr(runtime.orders, "fill_ledger", None)
                or getattr(runtime.orders, "fills_ledger", None)
                or []
            )[-limit:]:
                if hasattr(fill, "to_dict"):
                    d = fill.to_dict()
                    d.setdefault("cash_delta", str(getattr(fill, "cash_delta", "")))
                    d.setdefault("total_cost", str(getattr(fill, "total_cost", "")))
                    book_fills.append(d)
        return {
            "success": True,
            "trades": trades,
            "count": len(trades),
            "runtime_fills": book_fills[-limit:],
        }


class GetStrategyTradesParams(BaseModel):
    strategy_id: str | None = Field(None, description="Optional strategy filter")
    symbol: str | None = Field(None, description="Optional CLASS:TICKER or ticker")
    limit: int = Field(50, ge=1, le=500)


class GetStrategyTradesTool(BaseAITool):
    name = "get_strategy_trades"
    description = (
        "Round-trip trades (entry/exit/qty/realized_pnl) paired from fills by "
        "strategy_id+symbol FIFO. Use for anomaly audits (e.g. oversized PnL)."
    )
    category = "trading"
    parameters_schema = GetStrategyTradesParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        from engine.trade_ledger import normalize_fill_row, pair_round_trips

        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        stor = core.service_registry.get("storage_service")
        if stor is None or not hasattr(stor, "query_journal"):
            return {"success": False, "error": "storage_service missing"}
        limit = int(kwargs.get("limit") or 50)
        sid = str(kwargs.get("strategy_id") or "").strip() or None
        symbol = str(kwargs.get("symbol") or "").strip() or None
        rows = await stor.query_journal(
            event_types=["execution.result", "phantom.fills", "order.fill"],
            limit=min(max(limit * 8, 100), 4000),
        )
        fills: list[dict[str, Any]] = []
        for row in reversed(list(rows or [])):
            norm = normalize_fill_row(row if isinstance(row, dict) else {})
            if norm is not None:
                fills.append(norm)
        paired = pair_round_trips(
            fills, strategy_id=sid, symbol=symbol, limit=limit
        )
        return {"success": True, **paired}


class GetRiskRejectsParams(BaseModel):
    strategy_id: str | None = None
    symbol: str | None = None
    limit: int = Field(50, ge=1, le=500)


class GetRiskRejectsTool(BaseAITool):
    name = "get_risk_rejects"
    description = (
        "History of risk.reject journal events (order blocked and why). "
        "Primary tool for diagnosing sizing / leverage / drawdown rejects."
    )
    category = "trading"
    parameters_schema = GetRiskRejectsParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        stor = core.service_registry.get("storage_service")
        if stor is None or not hasattr(stor, "query_journal"):
            return {"success": False, "error": "storage_service missing"}
        limit = int(kwargs.get("limit") or 50)
        sid = str(kwargs.get("strategy_id") or "").strip() or None
        symbol = str(kwargs.get("symbol") or "").strip() or None
        rows = await stor.query_journal(
            event_types=["risk.reject"],
            limit=min(max(limit * 3, 50), 2000),
        )
        out: list[dict[str, Any]] = []
        for row in reversed(list(rows or [])):
            if not isinstance(row, dict):
                continue
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            order = payload.get("order") if isinstance(payload.get("order"), dict) else {}
            raw = order.get("raw") if isinstance(order.get("raw"), dict) else {}
            row_sid = str(
                payload.get("strategy_id")
                or order.get("strategy_id")
                or raw.get("strategy_id")
                or ""
            )
            ticker = str(
                payload.get("ticker")
                or payload.get("symbol")
                or order.get("symbol")
                or ""
            )
            class_code = str(payload.get("class_code") or order.get("class_code") or "")
            key = f"{class_code}:{ticker}" if class_code and ticker else ticker
            if sid and row_sid != sid:
                continue
            if symbol and symbol not in {ticker, key, class_code}:
                if not (ticker and symbol.endswith(ticker)):
                    continue
            out.append(
                {
                    "ts": row.get("ts") or row.get("created_at"),
                    "strategy_id": row_sid,
                    "symbol": key or ticker,
                    "action": payload.get("action") or order.get("side") or "",
                    "quantity": payload.get("quantity") or order.get("quantity"),
                    "reason": payload.get("reason") or order.get("reason") or "",
                    "requested_qty": payload.get("requested_qty")
                    or payload.get("qty_requested"),
                    "mark_price": payload.get("mark_price") or payload.get("price"),
                }
            )
            if len(out) >= limit:
                break
        return {"success": True, "rejects": out, "count": len(out)}


class DryRunRiskParams(BaseModel):
    ticker: str = Field(..., description="Ticker or CLASS:TICKER")
    side: str = Field(..., description="buy|sell")
    quantity: float = Field(..., gt=0)
    class_code: str | None = None
    mark_price: float | None = None
    reduce_only: bool = False
    order_type: str = "market"
    limit_price: float | None = None


class DryRunRiskCheckTool(BaseAITool):
    name = "dry_run_risk_check"
    description = (
        "Preview risk gates for a hypothetical order without placing it. "
        "Returns allow/reject reason, sized qty, and recent risk_trace."
    )
    category = "trading"
    parameters_schema = DryRunRiskParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        payload = {k: v for k, v in kwargs.items() if v is not None}
        return await _call_bridge(core, "DRY_RUN_RISK", **payload)


class SystemHaltParams(BaseModel):
    reason: str = Field("ai_operator", description="Halt reason")


class SystemHaltTool(BaseAITool):
    name = "system_halt"
    description = "Emergency kill switch: halt new orders (does not flatten)."
    category = "trading"
    parameters_schema = SystemHaltParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        reason = str(kwargs.get("reason") or "ai_operator")
        result = await _call_bridge(core, "KILL", reason=reason)
        if result.get("success"):
            runtime = getattr(core, "trading", None)
            halted = bool(
                result.get("halted")
                if result.get("halted") is not None
                else getattr(runtime, "halted", True)
            )
            result["halted"] = halted
            result["halt_reason"] = str(
                result.get("halt_reason")
                or getattr(runtime, "_halt_reason", "")
                or reason
            )
            if not halted:
                result["success"] = False
                result["ok"] = False
                result["error"] = "halt_not_applied"
        return result


class SystemResumeTool(BaseAITool):
    name = "system_resume"
    description = "Clear kill switch / resume trading after halt."
    category = "trading"
    parameters_schema = EmptyParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        result = await _call_bridge(core, "RESUME")
        if result.get("success"):
            runtime = getattr(core, "trading", None)
            halted = bool(
                result.get("halted")
                if result.get("halted") is not None
                else getattr(runtime, "halted", False)
            )
            result["halted"] = halted
            if halted:
                result["success"] = False
                result["ok"] = False
                result["error"] = "resume_not_applied"
        return result


class PlaceOrderParams(BaseModel):
    ticker: str = Field(..., description="Ticker or CLASS:TICKER")
    side: str = Field(..., description="buy|sell")
    quantity: float = Field(..., gt=0)
    class_code: str | None = None
    order_type: str = Field("market", description="market|limit")
    limit_price: float | None = Field(
        None, description="Limit price for better entry/exit; also used as mark fallback"
    )
    mark_price: float | None = None
    reduce_only: bool = False
    reason: str = "ai_operator"
    confirm_live: bool = Field(
        False,
        description="Required when runtime mode is live/quik",
    )


class PlaceOrderTool(BaseAITool):
    name = "place_order"
    description = (
        "Operator emergency order: buy/sell (optionally reduce_only or limit). "
        "Use to enter, scale, or exit at a chosen price. "
        "Live/quik requires confirm_live=true. Prefer dry_run_risk_check first."
    )
    category = "trading"
    parameters_schema = PlaceOrderParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        runtime = getattr(core, "trading", None)
        mode = str(getattr(runtime, "mode", "") or "").lower()
        if mode in {"live", "quik"} and not bool(kwargs.get("confirm_live")):
            return {
                "success": False,
                "error": "confirm_live_required",
                "hint": "Pass confirm_live=true only after explicit operator confirmation.",
            }
        payload = {k: v for k, v in kwargs.items() if v is not None}
        result = await _call_bridge(core, "PLACE_ORDER", **payload)
        if result.get("success"):
            order = result.get("order") if isinstance(result.get("order"), dict) else {}
            result.setdefault("ticker", order.get("ticker") or payload.get("ticker"))
            result.setdefault("side", order.get("side") or payload.get("side"))
            result.setdefault(
                "quantity", order.get("quantity") or payload.get("quantity")
            )
            result.setdefault("status", order.get("status") or order.get("state"))
            if order.get("filled_qty") is not None:
                result.setdefault("filled_qty", order.get("filled_qty"))
        return result


class ClosePositionParams(BaseModel):
    symbol: str = Field(..., description="CLASS:TICKER or ticker")
    quantity: float | None = Field(None, description="Partial close; omit = full")
    order_type: str = "market"
    limit_price: float | None = None
    mark_price: float | None = None
    reason: str = "ai_close"
    confirm_live: bool = False


class ClosePositionTool(BaseAITool):
    name = "close_position"
    description = (
        "Reduce-only close of one open position (full or partial). "
        "Supports limit_price for a better exit. Live/quik needs confirm_live=true."
    )
    category = "trading"
    parameters_schema = ClosePositionParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        runtime = getattr(core, "trading", None)
        mode = str(getattr(runtime, "mode", "") or "").lower()
        if mode in {"live", "quik"} and not bool(kwargs.get("confirm_live")):
            return {
                "success": False,
                "error": "confirm_live_required",
                "hint": "Pass confirm_live=true only after explicit operator confirmation.",
            }
        payload = {k: v for k, v in kwargs.items() if v is not None}
        result = await _call_bridge(core, "CLOSE_POSITION", **payload)
        if result.get("success"):
            order = result.get("order") if isinstance(result.get("order"), dict) else {}
            result.setdefault("ticker", order.get("ticker") or payload.get("symbol"))
            result.setdefault("status", order.get("status") or order.get("state"))
            result.setdefault(
                "quantity", order.get("quantity") or payload.get("quantity")
            )
        return result


class CloseAllParams(BaseModel):
    order_type: str = "market"
    limit_price: float | None = None
    mark_price: float | None = None
    reason: str = "ai_close_all"
    confirm_close_all: bool = False
    confirm_live: bool = False


class CloseAllTool(BaseAITool):
    name = "close_all"
    description = (
        "Flatten the whole book with reduce-only closes. "
        "Requires confirm_close_all=true; live/quik also confirm_live=true. "
        "Optional limit_price to exit at a chosen level."
    )
    category = "trading"
    parameters_schema = CloseAllParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        if not bool(kwargs.get("confirm_close_all")):
            return {
                "success": False,
                "error": "confirm_close_all_required",
                "hint": "Pass confirm_close_all=true after explicit confirmation.",
            }
        runtime = getattr(core, "trading", None)
        mode = str(getattr(runtime, "mode", "") or "").lower()
        if mode in {"live", "quik"} and not bool(kwargs.get("confirm_live")):
            return {
                "success": False,
                "error": "confirm_live_required",
                "hint": "Pass confirm_live=true only after explicit operator confirmation.",
            }
        payload = {k: v for k, v in kwargs.items() if v is not None}
        result = await _call_bridge(core, "CLOSE_ALL", **payload)
        if result.get("success"):
            result.setdefault("count", len(result.get("orders") or []))
        return result


class CancelOrderParams(BaseModel):
    client_id: str = Field(
        ...,
        description=(
            "Client id from open_orders / get_runtime_status. "
            "For unknown local orphans (no broker_order_id) this still works — "
            "they are purged from the durable store. Prefer cancel_all_orders "
            "when cancelling many."
        ),
    )


class CancelOrderTool(BaseAITool):
    name = "cancel_order"
    description = (
        "Cancel one working order by client_id (from open_orders). "
        "Unknown local orphans without broker_order_id are purged locally. "
        "For dozens of orders use cancel_all_orders instead."
    )
    category = "trading"
    parameters_schema = CancelOrderParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        client_id = str(kwargs.get("client_id") or "").strip()
        if not client_id:
            return {"success": False, "error": "client_id_required"}
        result = await _call_bridge(core, "CANCEL_ORDER", client_id=client_id)
        if result.get("success"):
            result["client_id"] = client_id
        return result


class CancelAllOrdersParams(BaseModel):
    symbol: str | None = Field(
        None,
        description="Optional filter, e.g. SBER or TQBR:SBER",
    )
    confirm_cancel_all: bool = Field(
        False,
        description="Required true — cancels every matching working/open order",
    )


class CancelAllOrdersTool(BaseAITool):
    name = "cancel_all_orders"
    description = (
        "Bulk-cancel all working/open orders (optional symbol filter). "
        "Use when open_orders has many rows and calling cancel_order N times "
        "is impractical. Local orphans (state=unknown, no broker id) are purged "
        "from data/execution/orders.json. Requires confirm_cancel_all=true."
    )
    category = "trading"
    parameters_schema = CancelAllOrdersParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        if not bool(kwargs.get("confirm_cancel_all")):
            return {
                "success": False,
                "error": "confirm_cancel_all_required",
                "hint": "Pass confirm_cancel_all=true after explicit confirmation.",
            }
        payload = {
            "confirm_cancel_all": True,
            "symbol": str(kwargs.get("symbol") or "").strip() or None,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        result = await _call_bridge(core, "CANCEL_ALL_ORDERS", **payload)
        if result.get("success"):
            result.setdefault("cancelled", result.get("cancelled"))
            result.setdefault("remaining_open", result.get("remaining_open"))
        return result


class ReleaseExitParams(BaseModel):
    slice_id: str | None = None
    symbol: str | None = None
    client_id: str | None = None
    force: bool = Field(
        False, description="If true with no selectors, clear all EXIT_PENDING"
    )


class ReleaseExitTool(BaseAITool):
    name = "release_exit_pending"
    description = (
        "Clear stuck protective EXIT_PENDING reservations so new exits can proceed."
    )
    category = "trading"
    parameters_schema = ReleaseExitParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        payload = {k: v for k, v in kwargs.items() if v is not None}
        force = bool(payload.get("force"))
        has_selector = any(
            payload.get(k) for k in ("slice_id", "symbol", "client_id")
        )
        if not force and not has_selector:
            return _nothing_to_set(
                "Pass slice_id=/symbol=/client_id=, or force=true to clear all."
            )
        result = await _call_bridge(core, "RELEASE_EXIT", **payload)
        if result.get("success"):
            result.setdefault("count", len(result.get("released") or []))
        return result


class ActivateCostScheduleParams(BaseModel):
    expected_hash: str = Field(..., description="Exact 64-char draft hash to activate")
    draft_hash: str | None = Field(
        None, description="Optional; must match expected_hash if set"
    )
    confirm_cost_schedule: bool = False


class ActivateCostScheduleTool(BaseAITool):
    name = "activate_cost_schedule"
    description = (
        "Activate a validated cost-schedule draft by exact hash. "
        "Requires confirm_cost_schedule=true."
    )
    category = "trading"
    parameters_schema = ActivateCostScheduleParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        if not bool(kwargs.get("confirm_cost_schedule")):
            return {
                "success": False,
                "error": "confirm_cost_schedule_required",
                "hint": "Pass confirm_cost_schedule=true after operator confirmation.",
            }
        expected = str(kwargs.get("expected_hash") or "").strip()
        if not expected:
            return {"success": False, "error": "expected_hash_required"}
        payload = {
            "expected_hash": expected,
            "confirm_cost_schedule": True,
        }
        if kwargs.get("draft_hash") is not None:
            payload["draft_hash"] = str(kwargs["draft_hash"])
        result = await _call_bridge(core, "COST_ACTIVATE", **payload)
        if result.get("success"):
            result.setdefault("status", "activated")
            result.setdefault("applied", expected)
        return result


class ReconcileNowParams(BaseModel):
    venue: str | None = None
    account: str | None = None


class ReconcileNowTool(BaseAITool):
    name = "reconcile_now"
    description = "Run broker reconciliation now (all registered venues if venue omitted)."
    category = "trading"
    parameters_schema = ReconcileNowParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        payload = {k: v for k, v in kwargs.items() if v is not None}
        return await _call_bridge(core, "RECONCILE_NOW", **payload)


class PhantomSyncMetricsTool(BaseAITool):
    name = "phantom_sync_metrics"
    description = (
        "Refresh/read canonical phantom metrics from runtime equity "
        "(does not reset fills)."
    )
    category = "trading"
    parameters_schema = EmptyParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        return await _call_bridge(core, "PHANTOM_SYNC_METRICS")


class SetEnsembleLineupParams(BaseModel):
    strategies: list[str] | None = Field(
        None, description="Live Consilium lineup (votes + may execute)"
    )
    observe: list[str] | None = Field(
        None, description="Observe/shadow strategies (signals only, no vote)"
    )
    off: list[str] | None = Field(
        None, description="Strategies to stop (silent). Sent with observe apply."
    )
    weight_mode: str | None = Field(
        None, description="equal | manual | risk_parity | bandit_ucb"
    )
    weights: dict[str, float] | None = None
    enabled: bool | None = None
    persist: bool = True
    confirm_live_without_holdout: bool = Field(
        False,
        description=(
            "Allow promoting strategies to live votes without holdout clearance "
            "(not recommended)"
        ),
    )
    research_ticker: str | None = Field(
        None,
        description="Ticker used for holdout clearance lookup (e.g. BTCUSDT)",
    )


class SetEnsembleLineupTool(BaseAITool):
    name = "set_ensemble_lineup"
    description = (
        "Replace Consilium roster: pass full live ``strategies``, ``observe``, and ``off`` lists. "
        "New live members require live_ok from evaluate_live_readiness (holdout + robust_sign + "
        "CI excludes zero + min trades + multi-ticker + params applied) unless "
        "confirm_live_without_holdout=true. Canary quantity scale is applied on promote. "
        "Omitting all three is an error (no silent no-op). After success, verify with "
        "get_ensemble_status — UI Strategy roster shows live/observe/off."
    )
    category = "trading"
    parameters_schema = SetEnsembleLineupParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        payload = {k: v for k, v in kwargs.items() if v is not None}
        if not any(
            k in payload
            for k in (
                "strategies",
                "observe",
                "off",
                "weights",
                "enabled",
                "weight_mode",
            )
        ):
            return _nothing_to_set(
                "Pass strategies=/observe=/off= (full roster), or weights=/enabled=/weight_mode=."
            )
        # Atomic roster replace: if strategies provided, always send observe/off lists
        # (default empty) so old observe members cannot linger by omission.
        if "strategies" in payload:
            payload.setdefault("observe", [])
            payload.setdefault("off", [])
        result = await _call_bridge(core, "SET_ENSEMBLE", **payload)
        # Echo effective roster so digests/history cannot hide a no-op "ok".
        if result.get("success"):
            ens = core.service_registry.get("ensemble_service") if core else None
            cfg = dict(getattr(ens, "_ensemble_cfg", {}) or {}) if ens else {}
            raw = getattr(getattr(core, "config", None), "raw", None) or {}
            file_cfg = dict(((raw.get("trading") or {}).get("ensemble") or {}))
            effective_live = [
                str(x)
                for x in (
                    result.get("strategies")
                    if result.get("strategies") is not None
                    else cfg.get("strategies")
                    or []
                )
            ]
            effective_obs = [
                str(x)
                for x in (
                    result.get("observe")
                    if result.get("observe") is not None
                    else cfg.get("observe")
                    or []
                )
            ]
            result["strategies"] = effective_live
            result["observe"] = effective_obs
            result["persisted_strategies"] = [
                str(x) for x in (file_cfg.get("strategies") or [])
            ]
            result["persisted_observe"] = [
                str(x) for x in (file_cfg.get("observe") or [])
            ]
            if "strategies" in payload:
                requested = [str(x) for x in payload["strategies"]]
                result["requested_strategies"] = requested
                if requested != effective_live:
                    result["success"] = False
                    result["error"] = "ensemble_not_applied"
                    result["ok"] = False
                elif bool(payload.get("persist", True)):
                    # Always compare (even empty→empty). Skipping when
                    # persisted_strategies was empty hid failed saves.
                    if result["persisted_strategies"] != requested:
                        result["success"] = False
                        result["error"] = "ensemble_not_persisted"
                        result["ok"] = False
            if result.get("success") and "observe" in payload:
                # Bridge drops observe ids that are also in live.
                live_set = set(effective_live)
                requested_obs = [str(x) for x in payload["observe"]]
                expected_obs = [x for x in requested_obs if x and x not in live_set]
                result["requested_observe"] = requested_obs
                if expected_obs != effective_obs:
                    result["success"] = False
                    result["error"] = "ensemble_observe_not_applied"
                    result["ok"] = False
                elif bool(payload.get("persist", True)):
                    if result["persisted_observe"] != expected_obs:
                        result["success"] = False
                        result["error"] = "ensemble_observe_not_persisted"
                        result["ok"] = False
            if result.get("success") and "strategies" not in payload and "observe" not in payload:
                # weights/enabled/weight_mode-only: say so explicitly.
                result["roster_unchanged"] = True
        return result


class SetStrategyModeParams(BaseModel):
    strategy_id: str
    mode: str = Field(..., description="live | observe | off")
    persist: bool = True


class SetStrategyModeTool(BaseAITool):
    name = "set_strategy_mode"
    description = (
        "Set one strategy to live (Consilium), observe (signals only), or off (stopped)."
    )
    category = "trading"
    parameters_schema = SetStrategyModeParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        sid = str(kwargs.get("strategy_id") or "").strip()
        mode = str(kwargs.get("mode") or "").strip().lower()
        if not sid:
            return {"success": False, "error": "missing_strategy_id"}
        if mode not in {"live", "observe", "off"}:
            return {"success": False, "error": "mode_must_be_live_observe_or_off"}
        ens = core.service_registry.get("ensemble_service")
        cfg = dict(getattr(ens, "_ensemble_cfg", {}) or {}) if ens else {}
        # Prefer persisted config when service cache is stale / missing.
        if not cfg and core is not None:
            raw = getattr(getattr(core, "config", None), "raw", None) or {}
            cfg = dict(((raw.get("trading") or {}).get("ensemble") or {}))
        live = [str(x) for x in (cfg.get("strategies") or []) if str(x) != sid]
        observe = [str(x) for x in (cfg.get("observe") or []) if str(x) != sid]
        off = [sid] if mode == "off" else []
        if mode == "live":
            live.append(sid)
        elif mode == "observe":
            observe.append(sid)
        result = await _call_bridge(
            core,
            "SET_ENSEMBLE",
            strategies=live,
            observe=observe,
            off=off,
            persist=bool(kwargs.get("persist", True)),
        )
        if not result.get("success"):
            return result
        # Prefer bridge echo; fall back to live ensemble_service cfg after apply.
        ens_after = core.service_registry.get("ensemble_service")
        cfg_after = (
            dict(getattr(ens_after, "_ensemble_cfg", {}) or {}) if ens_after else {}
        )
        got_live = {
            str(x)
            for x in (
                result.get("strategies")
                if result.get("strategies") is not None
                else cfg_after.get("strategies")
                or []
            )
        }
        got_obs = {
            str(x)
            for x in (
                result.get("observe")
                if result.get("observe") is not None
                else cfg_after.get("observe")
                or []
            )
        }
        if mode == "live" and sid not in got_live:
            return {
                "success": False,
                "error": "mode_not_applied",
                "strategy_id": sid,
                "mode": mode,
                "strategies": list(got_live),
                "observe": list(got_obs),
            }
        if mode == "observe" and sid not in got_obs:
            return {
                "success": False,
                "error": "mode_not_applied",
                "strategy_id": sid,
                "mode": mode,
                "strategies": list(got_live),
                "observe": list(got_obs),
            }
        if mode == "off" and (sid in got_live or sid in got_obs):
            return {
                "success": False,
                "error": "mode_not_applied",
                "strategy_id": sid,
                "mode": mode,
                "strategies": list(got_live),
                "observe": list(got_obs),
            }
        result["strategy_id"] = sid
        result["mode"] = mode
        return result


class SetStrategyParamsParams(BaseModel):
    strategy_id: str = Field(..., description="Plugin / strategy id")
    params: dict[str, Any] = Field(default_factory=dict)
    persist: bool = True


class SetStrategyParamsTool(BaseAITool):
    name = "set_strategy_params"
    description = "Patch strategy plugin params (fast/slow/window/tickers/quantity/…)."
    category = "trading"
    parameters_schema = SetStrategyParamsParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        sid = str(kwargs.get("strategy_id") or "").strip()
        params = dict(kwargs.get("params") or {})
        if not sid:
            return {"success": False, "error": "strategy_id_required"}
        if not params:
            return _nothing_to_set("Pass non-empty params={...}.")
        result = await _call_bridge(
            core,
            "SET_STRATEGY_PARAMS",
            plugin_id=sid,
            params=params,
            persist=bool(kwargs.get("persist", True)),
        )
        if result.get("success"):
            merged = (
                result.get("params") if isinstance(result.get("params"), dict) else {}
            )
            result["strategy_id"] = sid
            result["params"] = params
            result["applied"] = {k: merged.get(k, params.get(k)) for k in params}
            # Fail only when bridge explicitly reports no live apply and empty merge.
            if (
                result.get("applied_live") is False
                and result.get("needs_restart") is not True
                and not merged
            ):
                result["success"] = False
                result["ok"] = False
                result["error"] = "params_not_applied"
        return result


class SetRiskLimitsParams(BaseModel):
    max_portfolio_drawdown: float | None = None
    max_portfolio_drawdown_abs: float | None = None
    max_strategy_drawdown: float | None = None
    max_strategy_drawdown_abs: float | None = None
    max_position_pct: float | None = None
    max_leverage: float | None = None
    max_daily_loss_pct: float | None = None
    max_daily_turnover_pct: float | None = None
    max_orders_per_minute: int | None = None
    max_concentration_pct: float | None = None
    max_margin_utilization: float | None = None
    max_data_age_seconds: float | None = None
    persist: bool = True


class SetRiskLimitsTool(BaseAITool):
    name = "set_risk_limits"
    description = "Update trading risk limits (fractions 0–1 for pct fields unless noted)."
    category = "trading"
    parameters_schema = SetRiskLimitsParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        persist = bool(kwargs.pop("persist", True))
        risk = _non_empty_patch(kwargs, ignore={"persist"})
        if not risk:
            return _nothing_to_set("Pass at least one risk field (e.g. max_leverage).")
        result = await _call_bridge(core, "SET_TRADING", risk=risk, persist=persist)
        if result.get("success"):
            result["risk"] = risk
            runtime = getattr(core, "trading", None)
            limits = getattr(getattr(runtime, "risk", None), "limits", None)
            if limits is not None:
                result["applied"] = {
                    name: getattr(limits, name, None)
                    for name in risk
                    if hasattr(limits, name)
                }
        return result


class SetTradingParams(BaseModel):
    mode: str | None = Field(
        None, description="paper | live | quik — live/quik require confirm_live=true"
    )
    confirm_live: bool = False
    equity: float | None = None
    persist: bool = True


class SetTradingTool(BaseAITool):
    name = "set_trading"
    description = (
        "Set trading mode and/or starting equity. "
        "Switching to live/quik requires confirm_live=true (and live safety when installed)."
    )
    category = "trading"
    parameters_schema = SetTradingParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        mode = kwargs.get("mode")
        if mode is not None and str(mode).lower() in {"live", "quik"}:
            if not bool(kwargs.get("confirm_live")):
                return {
                    "success": False,
                    "error": "confirm_live_required",
                    "hint": "Pass confirm_live=true only after explicit operator confirmation.",
                }
        payload: dict[str, Any] = {"persist": bool(kwargs.get("persist", True))}
        if mode is not None:
            payload["mode"] = str(mode).lower()
            payload["confirm_live"] = bool(kwargs.get("confirm_live"))
        if kwargs.get("equity") is not None:
            payload["equity"] = float(kwargs["equity"])
        if "mode" not in payload and "equity" not in payload:
            return _nothing_to_set("Pass mode= and/or equity=.")
        result = await _call_bridge(core, "SET_TRADING", **payload)
        if result.get("success"):
            runtime = getattr(core, "trading", None)
            result["mode"] = str(
                getattr(runtime, "mode", None) or payload.get("mode") or ""
            )
            if "equity" in payload:
                result["equity"] = float(
                    getattr(getattr(runtime, "orders", None), "starting_equity", None)
                    or payload["equity"]
                )
        return result


class SetUniverseParams(BaseModel):
    mode: str | None = Field(None, description="manual | screen | …")
    profile: str | None = None
    static_active: list[str] | None = Field(
        None, description="Active instrument keys for manual mode"
    )
    persist: bool = True


class SetUniverseTool(BaseAITool):
    name = "set_universe"
    description = "Change universe mode/profile/static_active and reselect now."
    category = "trading"
    parameters_schema = SetUniverseParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        payload = _non_empty_patch(kwargs)
        if not any(k in payload for k in ("mode", "profile", "static_active")):
            return _nothing_to_set("Pass mode=, profile=, and/or static_active=[...].")
        result = await _call_bridge(core, "SET_UNIVERSE", **payload)
        if result.get("success"):
            result.setdefault("active_keys", result.get("active_keys") or [])
            result.setdefault("applied", result.get("applied"))
        return result


class SetStorageParams(BaseModel):
    action: str = Field(
        "reroute",
        description="reroute | backend | reload",
    )
    default_backend: str | None = None
    backend_by_class: dict[str, str] | None = None
    backend_id: str | None = None
    enabled: bool | None = None
    path: str | None = None
    url: str | None = None
    persist: bool = False


class SetStorageTool(BaseAITool):
    name = "set_storage"
    description = (
        "Hot-change storage routing (action=reroute) or backend enable/path "
        "(action=backend) or reload backends."
    )
    category = "trading"
    parameters_schema = SetStorageParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        payload = _non_empty_patch(kwargs)
        action = str(payload.get("action") or "reroute").lower()
        payload["action"] = action
        if action == "reroute" and not any(
            k in payload for k in ("default_backend", "backend_by_class")
        ):
            return _nothing_to_set(
                "action=reroute needs default_backend= and/or backend_by_class=."
            )
        if action == "backend" and not payload.get("backend_id"):
            return _nothing_to_set("action=backend needs backend_id=.")
        return await _call_bridge(core, "SET_STORAGE", **payload)


class SetPluginParams(BaseModel):
    plugin_id: str
    enabled: bool | None = None
    action: str | None = Field(None, description="start | stop")
    persist: bool = True


class SetPluginTool(BaseAITool):
    name = "set_plugin"
    description = "Enable/disable or start/stop a plugin (strategies or extensions)."
    category = "trading"
    parameters_schema = SetPluginParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        payload = _non_empty_patch(kwargs)
        pid = str(payload.get("plugin_id") or "").strip()
        if not pid:
            return {"success": False, "error": "plugin_id_required"}
        if payload.get("enabled") is None and not payload.get("action"):
            return _nothing_to_set("Pass enabled=true/false and/or action=start|stop.")
        result = await _call_bridge(core, "SET_PLUGIN", **payload)
        if result.get("success"):
            result["plugin_id"] = pid
            if "enabled" in payload:
                result["enabled"] = bool(payload["enabled"])
            if payload.get("action"):
                result["action"] = str(payload["action"])
            result.setdefault("running", result.get("running"))
            result.setdefault("needs_restart", result.get("needs_restart"))
        return result


class ResetPaperParams(BaseModel):
    symbol: str | None = Field(
        None, description="Optional: clear only this CLASS:TICKER / ticker"
    )
    strategy_id: str | None = Field(
        None, description="Optional: clear open exposure attributed to this strategy"
    )
    confirm_scoped: bool = Field(
        False,
        description="Required true when symbol/strategy_id set (avoids accidental partial wipe)",
    )


class ResetPaperTool(BaseAITool):
    name = "reset_paper"
    description = (
        "Reset paper/sim book. Without filters — full wipe. "
        "With symbol and/or strategy_id — scoped clear (requires confirm_scoped=true). "
        "Forbidden in live/quik; use close_position there."
    )
    category = "trading"
    parameters_schema = ResetPaperParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        symbol = str(kwargs.get("symbol") or "").strip() or None
        strategy_id = str(kwargs.get("strategy_id") or "").strip() or None
        if (symbol or strategy_id) and not bool(kwargs.get("confirm_scoped")):
            return {
                "success": False,
                "error": "confirm_scoped_required",
                "hint": "Pass confirm_scoped=true for partial reset.",
            }
        payload: dict[str, Any] = {}
        if symbol:
            payload["symbol"] = symbol
        if strategy_id:
            payload["strategy_id"] = strategy_id
        result = await _call_bridge(core, "RESET_PAPER", **payload)
        if result.get("success"):
            runtime = getattr(core, "trading", None)
            opens = []
            if runtime is not None and hasattr(runtime, "open_positions"):
                try:
                    opens = list(runtime.open_positions() or [])
                except Exception:
                    opens = list(result.get("open_positions") or [])
            result["open_positions"] = opens
            result["open_count"] = len(opens)
            eq = getattr(getattr(runtime, "orders", None), "equity", None)
            if eq is not None:
                result["equity"] = float(eq)
            gross = getattr(getattr(runtime, "orders", None), "gross_exposure", None)
            if gross is not None:
                result["gross_exposure"] = float(gross)
            if not symbol and not strategy_id and result["open_count"] > 0:
                result["success"] = False
                result["ok"] = False
                result["error"] = "reset_incomplete"
        return result


class PhantomCaptureParams(BaseModel):
    capturing: bool = True


class PhantomCaptureTool(BaseAITool):
    name = "phantom_capture"
    description = "Enable/disable live signal capture into phantom shadow ledger."
    category = "trading"
    parameters_schema = PhantomCaptureParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        capturing = bool(kwargs.get("capturing", True))
        result = await _call_bridge(core, "PHANTOM_CAPTURE", capturing=capturing)
        if result.get("success"):
            result["capturing"] = capturing
            ph = core.service_registry.get("phantom_service")
            if ph is not None:
                result["applied_capturing"] = bool(getattr(ph, "_capturing", capturing))
                if bool(getattr(ph, "_capturing", capturing)) != capturing:
                    result["success"] = False
                    result["ok"] = False
                    result["error"] = "capture_not_applied"
        return result


class PhantomRunParams(BaseModel):
    ticker: str
    class_code: str = "CRYPTO"
    strategy_id: str = "ma_cross_example"
    bars: int | None = Field(None, ge=10, le=5000)
    limit: int | None = Field(None, ge=10, le=5000)
    mode: str = Field(
        "grid",
        description="grid (few presets) | optimize (dense in-sample search)",
    )
    max_variants: int = Field(24, ge=1, le=120)
    feed: str = Field("history", description="history | patterns | both")
    pattern: str | None = None
    provider: str | None = None
    start_equity: float | None = None
    timeframe: str | None = Field(
        None,
        description="OHLC TF for history (defaults via phantom/walk_forward config)",
    )


class PhantomRunTool(BaseAITool):
    name = "phantom_run"
    description = (
        "Phantom grid/optimize: replay history (or patterns) across param variants. "
        "mode=optimize uses a denser search space (in-sample ranking — prefer "
        "phantom_walk_forward with variant_mode=optimize for OOS selection). "
        "Shadow ledger only, not live."
    )
    category = "trading"
    parameters_schema = PhantomRunParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        ticker = str(kwargs.get("ticker") or "").strip()
        if not ticker:
            return {"success": False, "error": "ticker_required"}
        payload = {k: v for k, v in kwargs.items() if v is not None}
        if "bars" in payload and "limit" not in payload:
            payload["limit"] = payload.pop("bars")
        result = await _call_bridge(core, "PHANTOM_RUN", **payload)
        if result.get("success"):
            result.setdefault("ticker", ticker)
            result.setdefault("status", result.get("status") or "ran")
            grid = result.get("grid")
            if isinstance(grid, list):
                result.setdefault("variants_count", len(grid))
        return result


class PhantomWalkForwardParams(BaseModel):
    ticker: str
    class_code: str = "CRYPTO"
    strategy_id: str = "ma_cross_example"
    bars: int = Field(500, ge=40, le=5000)
    train_bars: int = Field(60, ge=10, le=4000)
    test_bars: int = Field(20, ge=5, le=2000)
    step: int | None = Field(None, ge=1, le=2000)
    feed: str = Field(
        "history",
        description="history (comparable) or patterns (synthetic only)",
    )
    pattern: str = "sharp_rise"
    seed: int = Field(42, description="Reproducibility seed (patterns + bootstrap)")
    min_folds: int = Field(5, ge=1, le=50)
    auto_fit_folds: bool = False
    selection_mode: str = Field(
        "top_k_stable", description="best | top_k_stable"
    )
    top_k: int = Field(3, ge=1, le=20)
    bootstrap_samples: int = Field(1000, ge=0, le=20000)
    bootstrap_ci: float = Field(0.95, ge=0.5, le=0.999)
    persist_report: bool = True
    timeframe: str = Field(
        "1d",
        description="OHLC timeframe for history feed (default 1d; use 1m only for intraday probes)",
    )
    include_baselines: bool = Field(
        True,
        description="Include buy-hold / fee=0 / fixed-default baselines in the report",
    )
    variant_mode: str = Field(
        "optimize",
        description=(
            "grid = small presets (~3); optimize = dense param search under OOS "
            "fold selection (preferred for strategy optimization)"
        ),
    )
    max_variants: int = Field(
        36,
        ge=1,
        le=120,
        description="Cap on param combinations when variant_mode=optimize",
    )
    holdout_bars: int = Field(
        0,
        ge=0,
        le=2000,
        description=(
            "Reserve last N bars untouched: WF optimizes on prefix, then scores "
            "chosen once on holdout (robustness). 0 = disabled."
        ),
    )


class PhantomWalkForwardTool(BaseAITool):
    name = "phantom_walk_forward"
    description = (
        "Configurable walk-forward: train-select variants, score OOS folds. "
        "Default feed=history, bars=500, timeframe=1d, variant_mode=optimize (dense search). "
        "Use variant_mode=grid for a quick 3-point check. "
        "If bars is too short for min_folds it is auto-bumped (bars_auto_bumped in result). "
        "feed=patterns is synthetic and not comparable to live phantom. "
        "Always pass seed for reproducibility; min_folds defaults to 5. "
        "Returns provenance (timeframe, bar_interval_sec, variant_mode, variants_count), "
        "baselines, stable_family, bootstrap CI."
    )
    category = "trading"
    parameters_schema = PhantomWalkForwardParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        ticker = str(kwargs.get("ticker") or "").strip()
        if not ticker:
            return {"success": False, "error": "ticker_required"}
        from engine.walk_forward import resolve_walk_forward_bars

        plan = resolve_walk_forward_bars(
            kwargs.get("bars", 500),
            train_bars=int(kwargs.get("train_bars") or 60),
            test_bars=int(kwargs.get("test_bars") or 20),
            step=kwargs.get("step"),
            min_folds=int(kwargs.get("min_folds") or 5),
            holdout_bars=int(kwargs.get("holdout_bars") or 0),
        )
        kwargs = {**kwargs, "bars": int(plan["bars"])}
        payload = {k: v for k, v in kwargs.items() if v is not None}
        result = await _call_bridge(core, "PHANTOM_WALK_FORWARD", **payload)
        if plan.get("bumped"):
            result["bars_auto_bumped"] = True
            result["bars_bumped_from"] = plan.get("bars_bumped_from")
            result["bars_needed"] = plan.get("bars_needed")
            result["bars"] = plan.get("bars")
        if result.get("success"):
            result.setdefault("ticker", ticker)
            result.setdefault("strategy_id", payload.get("strategy_id"))
            result.setdefault("status", result.get("status") or "ran")
            wf = result.get("walk_forward") if isinstance(result.get("walk_forward"), dict) else {}
            prov = result.get("provenance") or wf.get("provenance") or {}
            if isinstance(prov, dict):
                result.setdefault("feed", prov.get("feed"))
                result.setdefault("seed", prov.get("seed"))
                result.setdefault("folds", prov.get("folds_realized"))
                result.setdefault("timeframe", prov.get("timeframe"))
                result.setdefault("variant_mode", prov.get("variant_mode"))
                result.setdefault("variants_count", prov.get("variants_count"))
                result.setdefault("bar_interval_sec", prov.get("bar_interval_sec"))
            oos = result.get("oos") or wf.get("oos") or {}
            if isinstance(oos, dict):
                result.setdefault("mean_oos_pnl", oos.get("mean_oos_pnl"))
                result.setdefault("fold_pnl_std", oos.get("fold_pnl_std"))
                result.setdefault("ci_low", oos.get("ci_low"))
                result.setdefault("ci_high", oos.get("ci_high"))
                result.setdefault("win_rate", oos.get("win_rate"))
                result.setdefault("profit_factor", oos.get("profit_factor"))
                result.setdefault("mean_oos_max_drawdown", oos.get("mean_oos_max_drawdown"))
                result.setdefault("total_oos_trades", oos.get("total_oos_trades"))
            dig = result.get("digest") or wf.get("digest")
            if isinstance(dig, dict):
                result.setdefault("digest", dig)
            hold = result.get("holdout") or wf.get("holdout")
            if isinstance(hold, dict):
                result.setdefault("holdout", hold)
                result.setdefault("holdout_pnl", hold.get("pnl"))
                result.setdefault("robust_sign_match", dig.get("robust_sign_match") if isinstance(dig, dict) else None)
            baselines = result.get("baselines") or wf.get("baselines")
            if isinstance(baselines, dict):
                result.setdefault("baselines", baselines)
                result.setdefault("edge_vs_buy_hold", baselines.get("edge_vs_buy_hold"))
                result.setdefault("cost_drag", baselines.get("cost_drag"))
        return result


class PhantomWalkForwardBatchParams(BaseModel):
    ticker: str | None = Field(
        None, description="Primary ticker (optional if tickers= is set)"
    )
    tickers: list[str] | None = Field(
        None,
        description="Multi-ticker batch, e.g. [BTCUSDT, ETH/USDT]",
    )
    strategy_ids: list[str] = Field(
        ...,
        description="Strategies to compare, e.g. [bollinger_reversion, rsi_reversion]",
    )
    class_code: str = "CRYPTO"
    bars: int = Field(500, ge=40, le=5000)
    timeframe: str = "1d"
    seed: int = 42
    variant_mode: str = Field("optimize", description="grid | optimize")
    max_variants: int = Field(36, ge=1, le=120)
    holdout_bars: int = Field(0, ge=0, le=2000)
    train_bars: int = Field(60, ge=10, le=4000)
    test_bars: int = Field(20, ge=5, le=2000)
    min_folds: int = Field(5, ge=1, le=50)
    feed: str = "history"


class PhantomWalkForwardBatchTool(BaseAITool):
    name = "phantom_walk_forward_batch"
    description = (
        "Batch walk-forward across many strategies on one ticker. "
        "Returns ranked digest rows (mean OOS, CI, PF, win-rate, DD, holdout). "
        "Prefer variant_mode=optimize; optional holdout_bars for robustness. "
        "Default bars=500; too-short bars are auto-bumped once for the whole batch "
        "(bars_auto_bumped) instead of N duplicate walk_forward_too_short errors."
    )
    category = "trading"
    parameters_schema = PhantomWalkForwardBatchParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        ticker = str(kwargs.get("ticker") or "").strip()
        tickers = kwargs.get("tickers")
        ids = kwargs.get("strategy_ids") or []
        if not ticker and not tickers:
            return {"success": False, "error": "ticker_required"}
        if not isinstance(ids, list) or not ids:
            return {"success": False, "error": "strategy_ids_required"}
        from engine.walk_forward import resolve_walk_forward_bars

        plan = resolve_walk_forward_bars(
            kwargs.get("bars", 500),
            train_bars=int(kwargs.get("train_bars") or 60),
            test_bars=int(kwargs.get("test_bars") or 20),
            step=kwargs.get("step"),
            min_folds=int(kwargs.get("min_folds") or 5),
            holdout_bars=int(kwargs.get("holdout_bars") or 0),
        )
        kwargs = {**kwargs, "bars": int(plan["bars"])}
        payload = {k: v for k, v in kwargs.items() if v is not None}
        result = await _call_bridge(core, "PHANTOM_WALK_FORWARD_BATCH", **payload)
        if plan.get("bumped"):
            result["bars_auto_bumped"] = True
            result["bars_bumped_from"] = plan.get("bars_bumped_from")
            result["bars_needed"] = plan.get("bars_needed")
            result["bars"] = plan.get("bars")
        if result.get("success"):
            rows = result.get("rows") if isinstance(result.get("rows"), list) else []
            result.setdefault("ticker", ticker or (tickers[0] if tickers else ""))
            result.setdefault("n_strategies", len(ids))
            result.setdefault("n_rows", len(rows))
            result.setdefault("top", rows[0] if rows else None)
        return result


class PhantomApplyChosenParams(BaseModel):
    strategy_id: str | None = Field(
        None, description="Defaults to last walk-forward strategy_id"
    )
    params: dict[str, Any] | None = Field(
        None, description="Explicit params; default = last WF chosen"
    )
    persist: bool = True
    from_last: bool = Field(
        True, description="Use last walk-forward chosen when params omitted"
    )
    sync_research_ticker: bool = Field(
        True,
        description=(
            "Set plugin tickers to the WF research symbol (avoids ETH/SOL live "
            "after BTCUSDT optimization)"
        ),
    )


class PhantomApplyChosenTool(BaseAITool):
    name = "phantom_apply_chosen"
    description = (
        "Write walk-forward chosen params into strategy plugin config "
        "(and YAML if persist=true). Prefers top-ranked OK row from last "
        "walk_forward_batch digest when from_last=true; else last single WF. "
        "Ack includes readiness grade/blockers/next_hint after apply."
    )
    category = "trading"
    parameters_schema = PhantomApplyChosenParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        payload = {k: v for k, v in kwargs.items() if v is not None}
        result = await _call_bridge(core, "PHANTOM_APPLY_CHOSEN", **payload)
        if result.get("success"):
            result.setdefault("strategy_id", payload.get("strategy_id"))
            result.setdefault("applied", result.get("params"))
        return result


class PhantomResetParams(BaseModel):
    strategy_id: str | None = Field(None, description="Scoped clear by strategy")
    symbol: str | None = Field(None, description="Scoped clear by CLASS:TICKER / ticker")
    confirm_scoped: bool = Field(
        False, description="Required when strategy_id/symbol set"
    )


class PhantomResetTool(BaseAITool):
    name = "phantom_reset"
    description = (
        "Reset phantom shadow ledger / timeline. "
        "Full wipe when no filters. Scoped by strategy_id/symbol needs confirm_scoped=true."
    )
    category = "trading"
    parameters_schema = PhantomResetParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        symbol = str(kwargs.get("symbol") or "").strip() or None
        strategy_id = str(kwargs.get("strategy_id") or "").strip() or None
        if (symbol or strategy_id) and not bool(kwargs.get("confirm_scoped")):
            return {
                "success": False,
                "error": "confirm_scoped_required",
                "hint": "Pass confirm_scoped=true for partial phantom reset.",
            }
        payload: dict[str, Any] = {}
        if symbol:
            payload["symbol"] = symbol
        if strategy_id:
            payload["strategy_id"] = strategy_id
        if payload:
            payload["confirm_scoped"] = True
        result = await _call_bridge(core, "PHANTOM_RESET", **payload)
        if result.get("success"):
            ph = core.service_registry.get("phantom_service")
            fills = list(getattr(ph, "_fills", []) or []) if ph is not None else []
            result["fills_count"] = len(fills)
            result.setdefault("scope", result.get("scope") or ("partial" if payload else "all"))
            if not symbol and not strategy_id and result.get("fills_count", 0) > 0:
                result["success"] = False
                result["ok"] = False
                result["error"] = "phantom_reset_incomplete"
        return result


class GetPhantomAuditParams(BaseModel):
    strategy_id: str | None = None
    limit: int = Field(100, ge=1, le=500)


class GetPhantomAuditTool(BaseAITool):
    name = "get_phantom_audit"
    description = (
        "Phantom shadow audit: rejected fills (reason/strength) and round-trips "
        "by strategy — for WF/phantom diagnosis, not live journal."
    )
    category = "trading"
    parameters_schema = GetPhantomAuditParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        ph = core.service_registry.get("phantom_service")
        if ph is None or not hasattr(ph, "phantom_audit"):
            return {"success": False, "error": "phantom_service missing"}
        audit = ph.phantom_audit(
            strategy_id=str(kwargs.get("strategy_id") or "").strip() or None,
            limit=int(kwargs.get("limit") or 100),
        )
        return {"success": True, **audit}


class EvaluateLiveReadinessParams(BaseModel):
    strategy_id: str | None = Field(
        None, description="Single strategy id (optional if strategy_ids set)"
    )
    strategy_ids: list[str] | None = Field(
        None, description="Batch of strategy ids; default = ensemble live+observe"
    )
    ticker: str | None = None
    tickers: list[str] | None = None


class EvaluateLiveReadinessTool(BaseAITool):
    name = "evaluate_live_readiness"
    description = (
        "Score strategy live-readiness grades: research_only / observe_ok / live_ok. "
        "Checks holdout clearance, robust_sign_match, CI excludes zero, min holdout/OOS "
        "trades, profit factor, multi-ticker robust count, and chosen params applied. "
        "Call before set_ensemble_lineup. Bollinger with CI-through-zero or 1 ticker "
        "will not be live_ok."
    )
    category = "trading"
    parameters_schema = EvaluateLiveReadinessParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        from engine.research_gate import (
            evaluate_live_readiness,
            evaluate_many,
            plugin_params_from_config,
            policy_from_config,
        )
        from engine.strategy_ids import resolve_strategy_id_via_core

        core = self.get_dependency("core", required=False)
        raw = getattr(getattr(core, "config", None), "raw", None)
        raw_cfg = raw if isinstance(raw, dict) else {}
        policy = policy_from_config(raw_cfg)
        ids: list[str] = []
        if kwargs.get("strategy_ids"):
            ids.extend(str(x) for x in kwargs["strategy_ids"] if str(x).strip())
        if kwargs.get("strategy_id"):
            ids.append(str(kwargs["strategy_id"]).strip())
        if not ids and core is not None:
            ens = core.service_registry.get("ensemble_service")
            cfg = dict(getattr(ens, "_ensemble_cfg", {}) or {}) if ens else {}
            ids = [
                str(x)
                for x in list(cfg.get("strategies") or [])
                + list(cfg.get("observe") or [])
                if str(x).strip()
            ]
        if not ids:
            return {
                "success": False,
                "error": "strategy_id_required",
                "hint": "Pass strategy_id= or strategy_ids=",
            }
        if core is not None:
            ids = [resolve_strategy_id_via_core(core, s) for s in ids]
        seen: set[str] = set()
        uniq: list[str] = []
        for s in ids:
            if s not in seen:
                seen.add(s)
                uniq.append(s)
        if len(uniq) == 1 and not kwargs.get("strategy_ids"):
            out = evaluate_live_readiness(
                uniq[0],
                ticker=kwargs.get("ticker"),
                tickers=kwargs.get("tickers"),
                policy=policy,
                plugin_params=plugin_params_from_config(raw_cfg, uniq[0])
                if raw_cfg
                else None,
            )
            return {"success": True, **out}
        batch = evaluate_many(uniq, policy=policy, raw_cfg=raw_cfg or None)
        return {"success": True, **batch}


class SaveConfigTool(BaseAITool):
    name = "save_config"
    description = "Persist current in-memory engine config to boot YAML."
    category = "trading"
    parameters_schema = EmptyParams
    is_modifying = True
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        core = self.get_dependency("core", required=False)
        if core is None:
            return {"success": False, "error": "core missing"}
        result = await _call_bridge(core, "SAVE_CONFIG")
        if result.get("success"):
            result.setdefault("status", "saved")
            path = getattr(getattr(core, "config", None), "path", None)
            if path is not None:
                result["applied"] = str(path)
        return result


# ---------------------------------------------------------------------------
# Research reports (filesystem, sandboxed under data/ai_reports)
# ---------------------------------------------------------------------------


class WriteResearchReportParams(BaseModel):
    title: str = Field(..., description="Short report title (folder slug source)")
    body_markdown: str = Field(
        "",
        description=(
            "SHORT markdown narrative only (prefer <=3000 chars). For long reports use "
            "begin_research_report + append_research_report + finalize_research_report "
            "so tool JSON is not truncated."
        ),
    )
    kind: str = Field(
        "research",
        description="Report kind: research|walk_forward|batch|strategy|ops|consilium",
    )
    summary: str | None = Field(
        None, description="1-3 sentence executive summary shown under the title"
    )
    tags: list[str] | None = Field(
        None, description="Free-form tags, e.g. holdout, btc, optimize"
    )
    strategy_ids: list[str] | None = None
    tickers: list[str] | None = None
    sections: dict[str, str] | None = Field(
        None,
        description="Named markdown sections (e.g. Data, Method, Results, Risks)",
    )
    metrics: dict[str, Any] | None = Field(
        None, description="Key numeric/string metrics rendered as a table"
    )
    conclusions: list[str] | None = None
    next_steps: list[str] | None = None
    attachments: dict[str, Any] | None = Field(
        None,
        description="Extra JSON blobs saved under attachments/ (digests, tables, etc.)",
    )
    source_tool_results: list[dict[str, Any]] | None = Field(
        None,
        description="Optional raw tool payloads to archive (auto-truncated if huge)",
    )
    include_last_walk_forward: bool = Field(
        False, description="Attach phantom_service last walk-forward report JSON"
    )
    include_last_batch: bool = Field(
        False, description="Attach last walk-forward batch digest rows"
    )
    include_phantom_status: bool = False
    include_ensemble_status: bool = False
    include_clearance: bool = Field(
        False, description="Attach research_clearance.json snapshot"
    )
    wf_report_paths: list[str] | None = Field(
        None,
        description=(
            "Existing WF JSON paths under data/phantom/… to bundle into attachments"
        ),
    )


class WriteResearchReportTool(BaseAITool):
    name = "write_research_report"
    description = (
        "ONE-SHOT short research report under data/ai_reports/ (keep body_markdown "
        "small, <=3000 chars). For deep-dives prefer begin_research_report → "
        "append_research_report (chunks) → finalize_research_report to avoid truncated "
        "tool JSON. Analyst-safe. Optional include_last_* attaches phantom payloads."
    )
    category = "trading"
    parameters_schema = WriteResearchReportParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        from engine.ai_research_report import (
            collect_runtime_attachments,
            default_report_root,
            write_research_report,
        )

        core = self.get_dependency("core", required=False)
        title = str(kwargs.get("title") or "").strip()
        body = str(kwargs.get("body_markdown") or "")
        if not title:
            return {"success": False, "error": "title_required"}
        if (
            not body.strip()
            and not kwargs.get("sections")
            and not kwargs.get("metrics")
            and not kwargs.get("summary")
        ):
            return {
                "success": False,
                "error": "empty_report",
                "hint": (
                    "Provide summary/body_markdown, or use begin_research_report + "
                    "append_research_report for long text"
                ),
            }
        try:
            auto = collect_runtime_attachments(
                core,
                include_last_walk_forward=bool(kwargs.get("include_last_walk_forward")),
                include_last_batch=bool(kwargs.get("include_last_batch")),
                include_phantom_status=bool(kwargs.get("include_phantom_status")),
                include_ensemble_status=bool(kwargs.get("include_ensemble_status")),
                include_clearance=bool(kwargs.get("include_clearance")),
                wf_report_paths=kwargs.get("wf_report_paths"),
            )
            attachments = dict(kwargs.get("attachments") or {})
            attachments.update(auto)
            meta = write_research_report(
                title=title,
                body_markdown=body,
                root=default_report_root(core),
                kind=str(kwargs.get("kind") or "research"),
                summary=kwargs.get("summary"),
                tags=kwargs.get("tags"),
                strategy_ids=kwargs.get("strategy_ids"),
                tickers=kwargs.get("tickers"),
                sections=kwargs.get("sections"),
                metrics=kwargs.get("metrics"),
                conclusions=kwargs.get("conclusions"),
                next_steps=kwargs.get("next_steps"),
                attachments=attachments or None,
                source_tool_results=kwargs.get("source_tool_results"),
                author="ai_analyst",
            )
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}
        return {
            "success": True,
            "ok": True,
            "report_id": meta.get("id"),
            "folder": meta.get("folder"),
            "report_md": meta.get("report_md"),
            "attachments": meta.get("attachments") or {},
            "bytes_md": meta.get("bytes_md"),
            "tool_digest": {
                "report_id": meta.get("id"),
                "title": meta.get("title"),
                "kind": meta.get("kind"),
                "strategies": meta.get("strategy_ids") or [],
                "tickers": meta.get("tickers") or [],
            },
        }


class BeginResearchReportParams(BaseModel):
    title: str
    kind: str = "research"
    summary: str | None = None
    tags: list[str] | None = None
    strategy_ids: list[str] | None = None
    tickers: list[str] | None = None
    metrics: dict[str, Any] | None = None


class BeginResearchReportTool(BaseAITool):
    name = "begin_research_report"
    description = (
        "Start a draft report folder under data/ai_reports/ (title/summary/metrics only). "
        "Then call append_research_report repeatedly with short markdown chunks, then "
        "finalize_research_report. Preferred for long strategy/WF write-ups."
    )
    category = "trading"
    parameters_schema = BeginResearchReportParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        from engine.ai_research_report import begin_research_report, default_report_root

        core = self.get_dependency("core", required=False)
        title = str(kwargs.get("title") or "").strip()
        if not title:
            return {"success": False, "error": "title_required"}
        try:
            meta = begin_research_report(
                title=title,
                root=default_report_root(core),
                kind=str(kwargs.get("kind") or "research"),
                summary=kwargs.get("summary"),
                tags=kwargs.get("tags"),
                strategy_ids=kwargs.get("strategy_ids"),
                tickers=kwargs.get("tickers"),
                metrics=kwargs.get("metrics"),
            )
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}
        return {
            "success": True,
            "ok": True,
            "report_id": meta.get("id"),
            "folder": meta.get("folder"),
            "status": "draft",
            "hint": "Call append_research_report(report_id, markdown_chunk) next",
        }


class AppendResearchReportParams(BaseModel):
    report_id: str
    markdown_chunk: str = Field(
        ...,
        description="Markdown chunk (<=8000 chars recommended) to append",
    )
    section: str | None = Field(
        None, description="Optional ## section heading for this chunk"
    )


class AppendResearchReportTool(BaseAITool):
    name = "append_research_report"
    description = (
        "Append a short markdown chunk to a draft report started with "
        "begin_research_report. Keep markdown_chunk small to avoid JSON truncation."
    )
    category = "trading"
    parameters_schema = AppendResearchReportParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        from engine.ai_research_report import append_research_report, default_report_root

        core = self.get_dependency("core", required=False)
        try:
            out = append_research_report(
                str(kwargs.get("report_id") or ""),
                str(kwargs.get("markdown_chunk") or ""),
                root=default_report_root(core),
                section=kwargs.get("section"),
            )
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}
        return {"success": True, **out}


class FinalizeResearchReportParams(BaseModel):
    report_id: str
    conclusions: list[str] | None = None
    next_steps: list[str] | None = None
    include_last_walk_forward: bool = False
    include_last_batch: bool = False
    include_phantom_status: bool = False
    include_ensemble_status: bool = False
    include_clearance: bool = False
    wf_report_paths: list[str] | None = None
    attachments: dict[str, Any] | None = None


class FinalizeResearchReportTool(BaseAITool):
    name = "finalize_research_report"
    description = (
        "Finalize a draft research report: optional conclusions/next_steps and "
        "attach last WF/batch/clearance JSON snapshots."
    )
    category = "trading"
    parameters_schema = FinalizeResearchReportParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        from engine.ai_research_report import (
            collect_runtime_attachments,
            default_report_root,
            finalize_research_report,
        )

        core = self.get_dependency("core", required=False)
        try:
            auto = collect_runtime_attachments(
                core,
                include_last_walk_forward=bool(kwargs.get("include_last_walk_forward")),
                include_last_batch=bool(kwargs.get("include_last_batch")),
                include_phantom_status=bool(kwargs.get("include_phantom_status")),
                include_ensemble_status=bool(kwargs.get("include_ensemble_status")),
                include_clearance=bool(kwargs.get("include_clearance")),
                wf_report_paths=kwargs.get("wf_report_paths"),
            )
            attachments = dict(kwargs.get("attachments") or {})
            attachments.update(auto)
            out = finalize_research_report(
                str(kwargs.get("report_id") or ""),
                root=default_report_root(core),
                conclusions=kwargs.get("conclusions"),
                next_steps=kwargs.get("next_steps"),
                attachments=attachments or None,
            )
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}
        return {"success": True, **out}


class ListResearchReportsParams(BaseModel):
    limit: int = Field(30, ge=1, le=200)
    kind: str | None = None
    strategy_id: str | None = None
    tag: str | None = None


class ListResearchReportsTool(BaseAITool):
    name = "list_research_reports"
    description = (
        "List AI research reports previously written to data/ai_reports/ "
        "(id, title, kind, strategies, tickers, summary)."
    )
    category = "trading"
    parameters_schema = ListResearchReportsParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        from engine.ai_research_report import default_report_root, list_research_reports

        core = self.get_dependency("core", required=False)
        out = list_research_reports(
            root=default_report_root(core),
            limit=int(kwargs.get("limit") or 30),
            kind=kwargs.get("kind"),
            strategy_id=kwargs.get("strategy_id"),
            tag=kwargs.get("tag"),
        )
        return {"success": True, **out}


class ReadResearchReportParams(BaseModel):
    report_id: str = Field(..., description="Report folder id from list_research_reports")
    include_body: bool = True
    include_attachments: bool = False
    max_body_chars: int = Field(80_000, ge=1000, le=400_000)


class ReadResearchReportTool(BaseAITool):
    name = "read_research_report"
    description = (
        "Read a previously written AI research report (markdown body + meta; "
        "optionally JSON attachments)."
    )
    category = "trading"
    parameters_schema = ReadResearchReportParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        from engine.ai_research_report import default_report_root, read_research_report

        core = self.get_dependency("core", required=False)
        out = read_research_report(
            str(kwargs.get("report_id") or ""),
            root=default_report_root(core),
            include_body=bool(kwargs.get("include_body", True)),
            include_attachments=bool(kwargs.get("include_attachments", False)),
            max_body_chars=int(kwargs.get("max_body_chars") or 80_000),
        )
        if not out.get("ok"):
            return {"success": False, **out}
        return {"success": True, **out}


class ListWalkForwardReportsParams(BaseModel):
    limit: int = Field(20, ge=1, le=100)
    strategy_id: str | None = None
    ticker: str | None = None


class ListWalkForwardReportsTool(BaseAITool):
    name = "list_walk_forward_reports"
    description = (
        "List persisted walk-forward JSON reports under data/phantom/walk_forward/ "
        "(machine digests from phantom_walk_forward). Use with write_research_report "
        "wf_report_paths= to cite them in a narrative AI report."
    )
    category = "trading"
    parameters_schema = ListWalkForwardReportsParams
    is_modifying = False
    source_plugin = _SOURCE

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        root = Path("data") / "phantom" / "walk_forward"
        if not root.exists():
            return {"success": True, "ok": True, "reports": [], "count": 0, "root": str(root)}
        sid = str(kwargs.get("strategy_id") or "").strip().lower()
        ticker = str(kwargs.get("ticker") or "").strip().upper()
        files = sorted(root.glob("wf_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        rows: list[dict[str, Any]] = []
        for path in files:
            name = path.name
            if sid and sid not in name.lower():
                continue
            if ticker and ticker not in name.upper():
                continue
            rows.append(
                {
                    "path": str(path.as_posix()),
                    "name": name,
                    "mtime": path.stat().st_mtime,
                    "bytes": path.stat().st_size,
                }
            )
            if len(rows) >= int(kwargs.get("limit") or 20):
                break
        return {
            "success": True,
            "ok": True,
            "root": str(root.as_posix()),
            "count": len(rows),
            "reports": rows,
        }


TRADING_AI_TOOL_CLASSES: list[type[BaseAITool]] = [
    # read
    GetRuntimeStatusTool,
    GetUniverseTool,
    QueryJournalTool,
    GetStrategyStatsTool,
    GetTradeHistoryTool,
    GetStrategyTradesTool,
    GetRiskRejectsTool,
    HistorySummaryTool,
    ListStrategiesTool,
    GetEnsembleStatusTool,
    GetOpsStatusTool,
    GetStorageStatusTool,
    GetPhantomStatusTool,
    GetPhantomAuditTool,
    GetRiskLimitsTool,
    GetStrategyRiskLimitsTool,
    GetReconciliationStatusTool,
    GetConfigSliceTool,
    ListServicesStatusTool,
    ReadCostScheduleTool,
    ProposeCostScheduleTool,
    DryRunRiskCheckTool,
    PhantomSyncMetricsTool,
    EvaluateLiveReadinessTool,
    WriteResearchReportTool,
    BeginResearchReportTool,
    AppendResearchReportTool,
    FinalizeResearchReportTool,
    ListResearchReportsTool,
    ReadResearchReportTool,
    ListWalkForwardReportsTool,
    # control
    SystemHaltTool,
    SystemResumeTool,
    PlaceOrderTool,
    ClosePositionTool,
    CloseAllTool,
    CancelOrderTool,
    CancelAllOrdersTool,
    ReleaseExitTool,
    ActivateCostScheduleTool,
    ReconcileNowTool,
    SetEnsembleLineupTool,
    SetStrategyModeTool,
    SetStrategyParamsTool,
    SetRiskLimitsTool,
    SetStrategyRiskLimitsTool,
    SetTradingTool,
    SetUniverseTool,
    SetStorageTool,
    SetPluginTool,
    ResetPaperTool,
    PhantomCaptureTool,
    PhantomResetTool,
    PhantomRunTool,
    PhantomWalkForwardTool,
    PhantomWalkForwardBatchTool,
    PhantomApplyChosenTool,
    SaveConfigTool,
]
