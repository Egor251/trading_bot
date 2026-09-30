"""AI Analyst extension — Code_base ai_analyst wired to trading domain tools."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from microkernel_core import BasePlugin, KernelEvent, PluginKind, PluginRunMode

from engine.ai_tools import TRADING_AI_TOOL_CLASSES
from host.logging_setup import get_log


def _log():
    return get_log("Engine", "AiAnalyst")


ANALYST_SYSTEM_PROMPT = """You are the AI Analyst for trading_bot (adaptive strategy consilium).
Mode: ANALYST — investigate only.
Domain: algo trading — universe, paper/live, risk, consilium, observe signals, storage, phantom.
Be concise. Prefer facts from tools over guesses.

Meta: list_tools, list_tool_categories.
Read tools: get_runtime_status, get_universe, get_ensemble_status, list_strategies,
query_journal, get_strategy_stats, get_trade_history, get_strategy_trades, get_risk_rejects,
get_risk_limits, get_strategy_risk_limits, get_ops_status, get_storage_status,
get_phantom_status, get_phantom_audit, get_reconciliation_status, get_config_slice, list_services_status,
history_summary, read_cost_schedule, propose_cost_schedule, dry_run_risk_check,
phantom_sync_metrics, evaluate_live_readiness, write_research_report, begin_research_report, append_research_report,
finalize_research_report, list_research_reports, read_research_report, list_walk_forward_reports.
Do NOT call modifying tools (halt/resume/set_*/place_order/close_*) in analyst mode.
Never invent fills — use get_trade_history / get_strategy_trades / journal.
After non-trivial research, persist findings under data/ai_reports/:
prefer begin_research_report → append_research_report (short chunks) → finalize_research_report
(include_last_*). Avoid huge one-shot write_research_report bodies (JSON truncates).
Use evaluate_live_readiness before recommending live votes (grades: research_only/observe_ok/live_ok).
"""

OPERATOR_SYSTEM_PROMPT = """You are the AI Operator for trading_bot (adaptive strategy consilium).
Mode: OPERATOR — investigate + controlled actions across the whole system.
Domain: algo trading — universe, paper/live, risk, consilium lineup/observe, storage, phantom, ops.
Be concise. Prefer tools over guesses. Confirm ambiguous destructive actions.

Meta: list_tools, list_tool_categories.
Read: get_runtime_status, get_universe, get_ensemble_status, list_strategies, query_journal,
get_strategy_stats, get_trade_history, get_strategy_trades, get_risk_rejects,
get_risk_limits, get_strategy_risk_limits, get_ops_status, get_storage_status,
get_phantom_status, get_phantom_audit, get_reconciliation_status, get_config_slice, list_services_status,
history_summary, read_cost_schedule, propose_cost_schedule, dry_run_risk_check,
phantom_sync_metrics, evaluate_live_readiness, write_research_report, begin_research_report, append_research_report,
finalize_research_report, list_research_reports, read_research_report, list_walk_forward_reports.

Control (use when asked): system_halt, system_resume, place_order, close_position, close_all,
cancel_order, cancel_all_orders, release_exit_pending, activate_cost_schedule, reconcile_now,
set_ensemble_lineup, set_strategy_mode, set_strategy_params, set_risk_limits,
set_strategy_risk_limits, set_trading (live/quik needs confirm_live=true), set_universe,
set_storage, set_plugin, reset_paper (scoped needs confirm_scoped=true), phantom_capture,
phantom_run, phantom_walk_forward, phantom_walk_forward_batch, phantom_apply_chosen,
phantom_reset, save_config.

Execution safety:
- Prefer dry_run_risk_check before place_order.
- place_order / close_position / close_all in live/quik require confirm_live=true.
- close_all also requires confirm_close_all=true.
- activate_cost_schedule requires confirm_cost_schedule=true and exact expected_hash.
- Never invent fills; report tool results only.
- Mutating tools reject empty/no-op calls (nothing_to_set). Never claim success from a bare ok.
- After set_ensemble_lineup / set_strategy_mode / set_risk_limits / reset_paper /
  phantom_reset / set_trading: verify with the matching get_* tool
  (get_ensemble_status, get_risk_limits, get_runtime_status, get_phantom_status).
- Trust tool_digest fields (strategies, risk, halted, error) over free-text claims.

Walk-forward / phantom research:
- phantom_walk_forward defaults: feed=history, seed=42, bars=500, min_folds=5,
  selection_mode=top_k_stable, variant_mode=optimize (dense OOS param search).
- Prefer bars=500. Engine auto-bumps bars if below
  train + test + (min_folds-1)*step (≈160 at defaults) and reports bars_auto_bumped.
- For a quick check only, pass variant_mode=grid (~3 presets). Prefer optimize
  when hunting parameters; report provenance.variants_count and bar_interval_sec.
- holdout_bars>0 reserves an untouched tail; report holdout_pnl and robust_sign_match.
  holdout increases the minimum bars requirement.
- phantom_walk_forward_batch compares many strategies (optional tickers=[...]) → ranked digest rows.
  Bars are planned once for the batch (no 21× too_short spam).
- Grid and WF reset the phantom ledger automatically. get_phantom_status.ledger shows
  open_positions / exit_pending; if stale, call phantom_reset.
- phantom_apply_chosen writes top batch digest / last WF chosen into plugin
  config (persist=true); ack includes readiness grade + next_hint
  and syncs tickers to the research symbol by default.
- Do NOT promote to live until evaluate_live_readiness returns live_ok
  (CI excludes zero, min trades, multi-ticker robust, params applied).
  set_ensemble_lineup blocks otherwise unless confirm_live_without_holdout=true.
  observe_ok is enough for the observe roster only.
- Always report provenance (feed, seed, folds, timeframe, variant_mode). Do not compare patterns-WF to live phantom.
- Use get_phantom_audit for shadow rejects / round-trips; risk_parity/bandit_ucb are live consilium weights, not WF.
- phantom_run mode=optimize is in-sample ranking only — not a substitute for walk-forward OOS.
- Persist findings under data/ai_reports/. For long write-ups use
  begin_research_report → append_research_report (small chunks) → finalize_research_report
  (conclusions/next_steps + include_last_walk_forward/batch). Do not stuff a huge
  body_markdown into one write_research_report call (tool JSON gets truncated).

Observe mode: strategies emit strategy.signal with observe_only but do not vote/trade.
"""


def prompt_for_role(role: str | None) -> str:
    if str(role or "").lower() == "operator":
        return OPERATOR_SYSTEM_PROMPT
    return ANALYST_SYSTEM_PROMPT


def _resolve_profile_secrets(settings: dict[str, Any]) -> dict[str, Any]:
    """Resolve provider secrets from env without persisting them to YAML."""
    resolved = dict(settings)
    env_name = str(resolved.get("api_key_env") or "").strip()
    key = str(resolved.get("api_key") or "").strip()
    if (not key or key == "********") and env_name:
        env_val = str(os.environ.get(env_name) or "").strip()
        if env_val:
            resolved["api_key"] = env_val
        else:
            resolved.pop("api_key", None)
    return resolved


def _resolve_secrets(config: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(config)
    profiles = resolved.get("profiles")
    if not isinstance(profiles, dict):
        return resolved
    safe_profiles: dict[str, Any] = {}
    for profile_id, raw in profiles.items():
        block = dict(raw) if isinstance(raw, dict) else {}
        settings = block.get("settings")
        if isinstance(settings, dict):
            block["settings"] = _resolve_profile_secrets(settings)
        safe_profiles[str(profile_id)] = block
    resolved["profiles"] = safe_profiles
    return resolved


class TradingAiAnalystPlugin(BasePlugin):
    """Extension plugin (not a StrategyPlugin) — LLM ask + trading tools."""

    PLUGIN_ID = "ai_analyst"
    PLUGIN_NAME = "AI Analyst"
    PLUGIN_KIND = PluginKind.BUSINESS
    RUN_MODE = PluginRunMode.CONTINUOUS

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.service: Any = None
        self._last_error: str | None = None
        self._last_ask: dict[str, Any] = {}

    @property
    def plugin_meta(self) -> dict[str, Any]:
        meta = super().plugin_meta
        meta.update(
            {
                "description": "LLM analyst for trading_bot (extension, not a strategy)",
                "tags": ["ai", "analyst", "llm", "extension"],
                "provides_ai_tools": False,
                "ai_tools_kind": "none",
            }
        )
        return meta

    def describe(self) -> list[str]:
        provider = (self.config or {}).get("default_provider", "mock")
        return [
            "AI Analyst",
            f"provider={provider}, ready={self.service is not None}, err={self._last_error}",
        ]

    async def on_initialize(self) -> None:
        from ai_analyst import AIAnalystService

        settings = _resolve_secrets(dict(self.config or {}))
        settings.setdefault("default_provider", "mock")
        settings.setdefault(
            "rbac",
            {
                "enabled": True,
                "default_role": "analyst",
                "max_role": "operator",
                "ui_roles": ["analyst", "operator"],
                "ui_chat_role": "analyst",
                "allow_plugin_tools": True,
            },
        )
        if "profiles" not in settings:
            settings["profiles"] = {
                "mock": {
                    "display_name": "Mock",
                    "provider_type": "mock",
                    "enabled": True,
                    "settings": {},
                }
            }
        host = self.host
        self.service = AIAnalystService(
            settings=settings,
            logger=getattr(host, "logger", None),
            storage=getattr(host, "storage", None),
            core=host,
            extra_tool_classes=list(TRADING_AI_TOOL_CLASSES),
        )
        await self.service.initialize()
        self._last_error = None
        _log().info(
            "ai_analyst initialized provider={} tools={}",
            settings.get("default_provider"),
            self.tool_names(),
        )

    async def run_once(self) -> None:
        await asyncio.sleep(1.0)

    async def on_cleanup(self) -> None:
        if self.service is not None:
            await self.service.cleanup()
            self.service = None
            _log().info("ai_analyst cleaned up")

    async def ask(
        self,
        message: str,
        *,
        use_tools: bool = True,
        provider_id: str | None = None,
        system_prompt: str | None = None,
        caller_role: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> Any:
        if self.service is None:
            raise RuntimeError("AI Analyst not initialized")
        from ai_analyst.ai.data_models import AnalystRequest

        role = str(caller_role or "analyst").lower()
        if role not in ("analyst", "operator", "viewer", "admin"):
            role = "analyst"
        hist_len = len(history or [])
        _log().info(
            "ask begin role={} provider={} tools={} hist={} len={}",
            role,
            provider_id,
            use_tools,
            hist_len,
            len(message or ""),
        )
        await self._publish_request(
            message,
            role=role,
            provider_id=provider_id,
            use_tools=bool(use_tools),
        )
        req = AnalystRequest(
            message=message,
            use_tools=use_tools,
            include_context=False,
            system_prompt=system_prompt or prompt_for_role(role),
            provider_id=provider_id,
            caller_role=role,
            history=list(history or []),
        )
        result = await self.service.ask(req)
        tools_used: list[str] = []
        for row in getattr(result, "tool_trace", None) or []:
            if isinstance(row, dict) and row.get("name"):
                tools_used.append(str(row["name"]))
        self._last_ask = {
            "success": result.success,
            "provider_id": result.provider_id,
            "model": result.model,
            "error": result.error,
            "caller_role": role,
            "content_preview": (result.content or "")[:200],
            "tools_used": tools_used,
            "hist_len": hist_len,
        }
        if not result.success:
            detail = str(result.error or "").strip() or "ask_failed_no_detail"
            self._last_error = detail
            _log().warning(
                "ask failed role={} provider={} model={} hist={} tools={} err={!r}",
                role,
                result.provider_id,
                result.model,
                hist_len,
                tools_used,
                detail,
            )
        else:
            _log().info(
                "ask ok role={} provider={} model={} latency={} hist={} tools={} preview={!r}",
                role,
                result.provider_id,
                result.model,
                result.latency,
                hist_len,
                tools_used,
                (result.content or "")[:120],
            )
        await self._publish_response(message, result)
        return result

    async def _instance_id(self) -> str:
        cfg = getattr(self.host, "config", None)
        if cfg is not None and hasattr(cfg, "get"):
            try:
                return str(await cfg.get("instance.id", "local") or "local")
            except Exception:
                pass
        return "local"

    async def _publish_request(
        self,
        message: str,
        *,
        role: str,
        provider_id: str | None,
        use_tools: bool,
    ) -> None:
        bus = getattr(self.host, "event_bus", None)
        if bus is None:
            return
        await bus.publish(
            KernelEvent(
                event_type="ai.analyst.request",
                source_id=self.component_id,
                payload={
                    "caller_role": role,
                    "provider_id": provider_id or "",
                    "use_tools": bool(use_tools),
                    "message_preview": (message or "")[:120],
                    "message_len": len(message or ""),
                },
                instance_id=await self._instance_id(),
            )
        )

    async def _publish_response(self, message: str, result: Any) -> None:
        bus = getattr(self.host, "event_bus", None)
        if bus is None:
            return
        await bus.publish(
            KernelEvent(
                event_type="ai.analyst.response",
                source_id=self.component_id,
                payload={
                    "success": bool(result.success),
                    "provider_id": getattr(result, "provider_id", "") or "",
                    "model": getattr(result, "model", "") or "",
                    "message_preview": (message or "")[:120],
                    "content_preview": (getattr(result, "content", None) or "")[:240],
                    "error": getattr(result, "error", None),
                    "latency": float(getattr(result, "latency", 0) or 0),
                },
                instance_id=await self._instance_id(),
            )
        )

    def tool_names(self) -> list[str]:
        if self.service is None:
            return []
        registry = self.service.build_registry()
        return list(registry.get_available_tools())
