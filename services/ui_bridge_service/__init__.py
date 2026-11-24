"""Engine → UI bridge: non-blocking snapshots + operator control commands.

Primary IPC: TCP JSON-lines on ``ws_host:ws_port`` (default 127.0.0.1:8765).
Optional: ZeroMQ PUB/SUB on ``ipc.pub_port`` / ``ipc.sub_port`` when ``pyzmq`` is installed.

UI process connects here — GUI never runs in Engine.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from pathlib import Path
import time
from collections import deque
from typing import TYPE_CHECKING, Any

from microkernel_core import BaseService, KernelEvent

from host.config_patch import (
    deep_merge,
    operator_config_slice,
    redact_secrets,
    resolve_env_secrets,
    set_path,
)
from host.logging_setup import get_log
from engine.cost_workflow import (
    CostScheduleDraft,
    CostScheduleStore,
    SourceDocument,
    validate_cost_draft,
)
from engine.risk_patch_guard import (
    guard_service_config_patch as _guard_service_config_patch,
)
from engine.risk_patch_guard import validate_risk_patch as _validate_risk_patch

if TYPE_CHECKING:
    from microkernel_core import MicrokernelCore


def _log():
    return get_log("Engine", "UiBridge")


_RECENT_EVENTS = (
    "market.candle",
    "strategy.signal",
    "ensemble.decision",
    "ensemble.reject",
    "ensemble.weights_changed",
    "execution.result",
    "risk.reject",
    "portfolio.snapshot",
    "config.lineup_changed",
    "ops.alert",
    "reconciliation.incident",
    "system.halted",
    "system.resumed",
    "phantom.fills",
    "backtest.run_finished",
)

# Separate feed for Operator toasts — not flooded by candles / portfolio ticks.
_NOTIFY_EVENTS = frozenset(
    {
        "execution.result",
        "risk.reject",
        "ensemble.decision",
        "ensemble.reject",
        "ops.alert",
        "reconciliation.incident",
        "system.halted",
        "system.resumed",
        "phantom.fills",
    }
)


#: Risk knobs an operator may set from the UI, with bounds that keep a value
#: meaningful. `strength_floor` is here because it decides whether a strategy
#: signal can open a position at all, and until now it was only reachable by
#: editing YAML - the one policy dial with no UI affordance.
_RISK_KNOBS: dict[str, tuple[float, float]] = {
    "max_daily_loss": (0.0, 1.0),
    "max_daily_turnover": (0.0, 1000.0),
    "max_order_rate": (0.0, 10_000.0),
    "max_position_pct": (0.0, 1.0),
    "max_leverage": (0.0, 10.0),
    "max_concentration": (0.0, 1.0),
    "max_portfolio_drawdown": (0.0, 1.0),
    "max_strategy_drawdown": (0.0, 1.0),
    "max_data_age_seconds": (0.0, 86_400.0),
    "strength_floor": (0.0, 1.0),
}

#: Execution knobs: resting-order lifetime and maker-only behaviour.
_EXECUTION_KNOBS: dict[str, tuple[float, float]] = {
    "stale_order_ttl_sec": (0.0, 86_400.0),
    "stale_order_sweep_interval_sec": (1.0, 3600.0),
}

#: Per-strategy circuit breaker. `restore_above` must exceed
#: `quarantine_below` or a borderline strategy can never resume -- a permanent
#: lockout, which is the opposite of what a breaker is for. That relationship is
#: enforced in the command handler, not merely documented here.
_QUARANTINE_KNOBS: dict[str, tuple[float, float]] = {
    "quarantine_below": (-100.0, 100.0),
    "restore_above": (-100.0, 100.0),
    "consecutive_failures": (1.0, 30.0),
    "min_samples": (1.0, 10_000.0),
}


class UiBridgeService(BaseService):
    """Publishes a read-model snapshot for the operator UI (non-blocking)."""

    def __init__(self, core: MicrokernelCore, component_id: str) -> None:
        super().__init__(core, component_id)
        self._recent: deque[dict[str, Any]] = deque(maxlen=40)
        self._notify_recent: deque[dict[str, Any]] = deque(maxlen=40)
        self._writers: set[asyncio.StreamWriter] = set()
        self._server: asyncio.AbstractServer | None = None
        self._pub_task: asyncio.Task[None] | None = None
        self._cmd_task: asyncio.Task[None] | None = None
        self._zmq_pub: Any = None
        self._zmq_sub: Any = None
        #: Outcomes of commands that arrived over a transport with no return path
        #: (ZMQ SUB is one-way). Keyed by `request_id`, surfaced in the snapshot.
        self._command_acks: dict[str, Any] = {}
        self._last_snapshot: dict[str, Any] = {}
        self._hz = 10.0
        self._equity_curve: deque[float] = deque(maxlen=300)
        self._last_equity: float | None = None
        self._storage_cache: dict[str, Any] = {"ready": False}
        self._storage_cache_tick: int = 0
        #: Seconds a connected client may take to absorb one snapshot frame.
        #: Bounded because `StreamWriter.drain()` waits on the peer's TCP
        #: window and will not return while a client is not reading.
        self._drain_timeout_sec = float(self.config.get("drain_timeout_sec") or 5.0)
        self._secrets_presence_cache: dict[str, Any] = {"present": {}}
        self._secrets_presence_ts: float = 0.0

    def default_config(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "auto_start": True,
            "hz": 2.0,
            # Cap OHLC bars in each snapshot — full ring (1000+) was choking TCP/UI.
            "ohlc_bars": 360,
            "ws_host": "127.0.0.1",
            "ws_port": 8765,
            "zmq_enabled": True,
            # Bound on how long one client may stall the broadcast. See
            # `_broadcast`: an unbounded `drain()` let a single non-reading
            # client freeze snapshots for everybody.
            "drain_timeout_sec": 5.0,
        }

    async def on_initialize(self) -> None:
        ipc = await self.core.config.get("ipc", {}) or {}
        self.config.setdefault("pub_port", int(ipc.get("pub_port") or 5555))
        self.config.setdefault("sub_port", int(ipc.get("sub_port") or 5556))
        self._hz = float(self.config.get("hz") or 2.0)
        setattr(self.core, "ui_bridge", self)

    async def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        try:
            for et in _RECENT_EVENTS:
                await self.core.event_bus.subscribe(et, self._on_bus_event)
            await self._start_tcp()
            if self.config.get("zmq_enabled", True):
                self._start_zmq()
            self._pub_task = asyncio.create_task(self._publish_loop())
            if self._zmq_sub is not None:
                self._cmd_task = asyncio.create_task(self._zmq_command_loop())
        except Exception:
            self.is_running = False
            raise

    async def stop(self) -> None:
        self.is_running = False
        for et in _RECENT_EVENTS:
            try:
                await self.core.event_bus.unsubscribe(et, self._on_bus_event)
            except Exception:
                pass
        for task in (self._pub_task, self._cmd_task):
            if task and not task.done():
                task.cancel()
        pending = [task for task in (self._pub_task, self._cmd_task) if task is not None]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._pub_task = self._cmd_task = None
        writers = list(self._writers)
        self._writers.clear()
        for writer in writers:
            try:
                writer.close()
            except Exception:
                pass
        for writer in writers:
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
            except Exception:
                pass
        if self._server is not None:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=1.0)
            except TimeoutError:
                pass
            self._server = None
        self._close_zmq()

    async def get_status(self) -> dict[str, Any]:
        st = await super().get_status()
        st.update(
            {
                "tcp": f"tcp://{self.config.get('ws_host')}:{self.config.get('ws_port')}",
                "clients": len(self._writers),
                "zmq": self._zmq_pub is not None,
                "hz": self._hz,
                "recent": len(self._recent),
            }
        )
        return st

    async def _on_bus_event(self, event: KernelEvent) -> None:
        item = {
            "t": time.time(),
            "type": event.event_type,
            "source": event.source_id,
            "payload": _slim_payload(event.payload),
        }
        self._recent.appendleft(item)
        if event.event_type in _NOTIFY_EVENTS:
            self._notify_recent.appendleft(item)

    def build_snapshot(self) -> dict[str, Any]:
        runtime = getattr(self.core, "trading", None)
        ens = self.core.service_registry.get("ensemble_service")
        phantom = self.core.service_registry.get("phantom_service")
        uni_svc = self.core.service_registry.get("universe_service")

        trading = (
            runtime.snapshot(ohlc_limit=int(self.config.get("ohlc_bars") or 360))
            if runtime and hasattr(runtime, "snapshot")
            else {}
        )
        # Refresh the Prometheus gauges and ship the exposition with the
        # snapshot, so the BFF's /metrics endpoint has something to serve.
        metrics_text = ""
        if runtime is not None and hasattr(runtime, "publish_metrics"):
            try:
                runtime.publish_metrics()
                obs = getattr(self.core, "operations_observability", None)
                if obs is not None and hasattr(obs, "prometheus_text"):
                    metrics_text = str(obs.prometheus_text())
            except Exception:
                _log().exception("metrics publication failed")
        if metrics_text:
            trading = {**trading, "metrics_text": metrics_text}
        ensemble: dict[str, Any] = {}
        if ens is not None:
            mgr = getattr(ens, "manager", None)
            members = []
            if hasattr(ens, "members_for_ui"):
                try:
                    members = list(ens.members_for_ui() or [])
                except Exception:
                    members = []
            ens_cfg = getattr(ens, "_ensemble_cfg", {}) or {}
            readiness: dict[str, Any] = {}
            try:
                from engine.research_gate import evaluate_many, policy_from_config

                ids = list(
                    dict.fromkeys(
                        [str(x) for x in (ens_cfg.get("strategies") or [])]
                        + [str(x) for x in (ens_cfg.get("observe") or [])]
                    )
                )
                raw_for_ready = getattr(self.core.config, "raw", None)
                if isinstance(raw_for_ready, dict) and ids:
                    readiness = evaluate_many(
                        ids,
                        policy=policy_from_config(raw_for_ready),
                        raw_cfg=raw_for_ready,
                    )
                    # compact for snapshot
                    readiness = {
                        "live_ok": readiness.get("live_ok") or [],
                        "observe_ok": readiness.get("observe_ok") or [],
                        "research_only": readiness.get("research_only") or [],
                        "rows": [
                            {
                                "strategy_id": r.get("strategy_id"),
                                "grade": r.get("grade"),
                                "score": r.get("score"),
                                "live_ok": r.get("live_ok"),
                                "observe_ok": r.get("observe_ok"),
                                "blockers": [b.get("reason") for b in (r.get("blockers") or [])],
                            }
                            for r in (readiness.get("rows") or [])
                        ],
                    }
            except Exception:
                readiness = {}
            ensemble = {
                "weights": getattr(mgr, "weights", {}) if mgr else {},
                "decisions": getattr(ens, "_decisions", 0),
                "last": getattr(ens, "_last_decision", None),
                "strategies": list(ens_cfg.get("strategies") or []),
                "observe": list(ens_cfg.get("observe") or []),
                "weight_mode": ens_cfg.get("weight_mode", "equal"),
                "enabled": bool(ens._enabled()) if hasattr(ens, "_enabled") else True,
                "members": members,
                "readiness": readiness,
                "stats": (
                    ens.manager.stats.snapshot(list(ens_cfg.get("strategies") or []))
                    if (
                        mgr is not None
                        and getattr(ens.manager, "stats", None) is not None
                        and hasattr(getattr(ens.manager, "stats", None), "snapshot")
                        and not type(getattr(ens.manager, "stats")).__name__.endswith("Mock")
                    )
                    else {}
                ),
            }
        phantom_st = self._phantom_snapshot(phantom)

        active: list[str] = []
        catalog: list[dict[str, Any]] = []
        universe_meta: dict[str, Any] = {
            "mode": "manual",
            "profile": "manual",
            "reason": "",
            "rank_by": "",
            "pinned_keys": [],
        }
        if uni_svc is not None:
            active = list(getattr(uni_svc, "_last_active_keys", None) or [])
            if not active and hasattr(uni_svc, "active_symbols"):
                active = list(uni_svc.active_symbols or [])
            for inst in getattr(uni_svc, "_catalog", None) or []:
                catalog.append(
                    {
                        "key": getattr(inst, "key", ""),
                        "symbol": getattr(inst, "symbol", ""),
                        "class_code": getattr(inst, "class_code", ""),
                        "exchange": getattr(inst, "exchange", ""),
                        "asset_class": getattr(inst, "asset_class", ""),
                        "price": getattr(inst, "price", 0),
                        "dollar_volume": getattr(inst, "dollar_volume", 0),
                    }
                )
            mgr = getattr(uni_svc, "_manager", None)
            if mgr is not None:
                setattr(self.core, "universe", mgr)
                last = getattr(mgr, "last", None)
                # Skip MagicMock / stubs — truthy mocks poison snapshot JSON.
                if last is not None and not type(last).__name__.endswith("Mock"):
                    mode = getattr(last, "mode", "manual")
                    if isinstance(mode, str):
                        universe_meta["mode"] = mode
                    profile = getattr(last, "profile", "")
                    if isinstance(profile, str):
                        universe_meta["profile"] = profile
                    reason = getattr(last, "reason", "")
                    if isinstance(reason, str):
                        universe_meta["reason"] = reason
                    rank_by = getattr(last, "rank_by", "")
                    if isinstance(rank_by, str):
                        universe_meta["rank_by"] = rank_by
                    books = getattr(last, "books", None)
                    if isinstance(books, dict) and books:
                        universe_meta["books"] = dict(books)
            if runtime is not None and hasattr(runtime, "open_position_keys"):
                try:
                    universe_meta["pinned_keys"] = sorted(runtime.open_position_keys())
                except Exception:
                    universe_meta["pinned_keys"] = []

        plugins = self._plugins_snapshot()
        equity = trading.get("equity")
        if equity is not None:
            try:
                eq_f = float(equity)
                if self._last_equity is None or eq_f != self._last_equity:
                    self._equity_curve.append(eq_f)
                    self._last_equity = eq_f
            except (TypeError, ValueError):
                pass

        raw = getattr(self.core.config, "raw", {}) or {}
        out = {
            "schema": "ui.snapshot.v2",
            "ts": time.time(),
            "halted": bool(trading.get("halted")),
            "halt_reason": trading.get("halt_reason") or "",
            "mode": trading.get("mode") or "paper",
            "equity": equity,
            "gross_exposure": trading.get("gross_exposure", 0.0),
            "positions": trading.get("positions") or {},
            "open_positions": trading.get("open_positions") or [],
            "open_orders": trading.get("open_orders") or [],
            "recent_trades": trading.get("recent_trades") or trading.get("fill_ledger") or [],
            "protection": trading.get("protection") or {},
            "fill_ledger": trading.get("fill_ledger") or [],
            "equity_curve": list(self._equity_curve),
            "fills": trading.get("fills"),
            "exposures": trading.get("exposures") or {},
            "risk": trading.get("risk") or {},
            "candles": trading.get("candles") or {},
            "active_keys": active,
            "catalog": catalog,
            "universe": universe_meta,
            "config": operator_config_slice(raw if isinstance(raw, dict) else {}),
            "ensemble": ensemble,
            "phantom": phantom_st,
            "plugins": plugins,
            "ai": self._ai_snapshot(),
            "costs": self._costs_snapshot(),
            "secrets": self._secrets_presence_snapshot(),
            "market": self._market_feed_snapshot(),
            "storage": dict(self._storage_cache),
            # Outcomes for transports with no return path (ZMQ SUB is one-way,
            # so a denied command would otherwise be indistinguishable from a
            # delivered one).
            "command_acks": dict(self._command_acks),
            "control": self._control_auth_snapshot(),
            "recent": list(self._recent)[:20],
            "recent_notify": list(self._notify_recent)[:30],
        }
        self._last_snapshot = out
        return out

    def _market_feed_snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "feed_source": "none",
            "exchange": "",
            "service": "",
            "error": "",
        }
        registry = getattr(self.core, "service_registry", None)
        if registry is None or not hasattr(registry, "get"):
            return out
        for sid in (
            "ccxt_market_service",
            "mock_market_service",
            "alpaca_market_service",
            "quik_market_service",
        ):
            try:
                svc = registry.get(sid)
            except Exception:
                continue
            if svc is None:
                continue
            # Skip test doubles / stubs that are not real market services.
            if not hasattr(svc, "_adapter") and not hasattr(svc, "_feed_source"):
                continue
            src = str(getattr(svc, "_feed_source", "") or "").strip()
            if src.startswith("<"):  # MagicMock repr
                src = ""
            if not src:
                adapter = getattr(svc, "_adapter", None)
                if adapter is not None:
                    name = type(adapter).__name__.lower()
                    if name == "mockmarketdataadapter" or name.endswith("mock"):
                        src = "mock"
                    elif "ccxt" in name:
                        src = "api"
            running = bool(getattr(svc, "is_running", False))
            if isinstance(running, bool) is False:
                running = False
            if not src and not running:
                continue
            cfg = getattr(svc, "config", None)
            exchange = ""
            if isinstance(cfg, dict):
                exchange = str(cfg.get("exchange") or "")
            out["feed_source"] = src or "none"
            out["service"] = sid
            out["exchange"] = exchange
            err = getattr(svc, "_last_error", None)
            out["error"] = str(err) if isinstance(err, str) else ""
            if src:
                break
        return out

    def _secrets_presence_snapshot(self) -> dict[str, Any]:
        """Presence flags only — never return secret values to the UI.

        Cached briefly so snapshot hz cannot open SQLite on every frame (and so a
        locked records.db cannot stall TCP ACKs).
        """
        now = time.monotonic()
        if (
            self._secrets_presence_cache.get("present")
            and (now - float(self._secrets_presence_ts or 0.0)) < 5.0
        ):
            return dict(self._secrets_presence_cache)
        names = (
            "BITGET_API_KEY",
            "BITGET_SECRET",
            "BITGET_PASSWORD",
            "CCXT_API_KEY",
            "CCXT_SECRET",
            "CCXT_PASSWORD",
            "ALPACA_API_KEY",
            "ALPACA_SECRET_KEY",
        )
        present: dict[str, bool] = {}
        try:
            from host.secrets_env import secrets_present

            present = secrets_present(names)
        except Exception:
            for name in names:
                present[name] = bool(str(os.environ.get(name) or "").strip())
        out = {"present": present}
        self._secrets_presence_cache = out
        self._secrets_presence_ts = now
        return dict(out)

    def _control_auth_snapshot(self) -> dict[str, Any]:
        authorizer = vars(self.core).get("control_authorizer")
        if authorizer is None:
            return {"auth_mode": "none", "auth_configured": False}
        try:
            from engine.control_auth import ControlAuthorizer
        except Exception:
            ControlAuthorizer = type(None)  # type: ignore[misc,assignment]
        if isinstance(authorizer, ControlAuthorizer):
            mode = "hmac"
            configured = True
        else:
            mode = str(getattr(authorizer, "mode", "unknown") or "unknown")
            configured = False
        return {
            "auth_mode": mode,
            "auth_configured": configured,
            "live_preflight": vars(self.core).get("live_preflight") is not None,
        }

    def _storage_service(self) -> Any:
        return self.core.service_registry.get("storage_service")

    async def _refresh_storage_cache(self) -> None:
        svc = self._storage_service()
        if svc is None or not hasattr(svc, "operator_snapshot"):
            self._storage_cache = {"ready": False, "error": "storage_service_missing"}
            return
        try:
            self._storage_cache = await svc.operator_snapshot()
        except Exception as exc:  # noqa: BLE001
            self._storage_cache = {"ready": False, "error": str(exc)}

    def _plugins_snapshot(self) -> list[dict[str, Any]]:
        plugins: list[dict[str, Any]] = []
        pm = getattr(self.core, "plugin_manager", None)
        if pm is None:
            return plugins
        raw = getattr(self.core.config, "raw", {}) or {}
        plugin_cfg = (raw.get("plugins") or {}) if isinstance(raw, dict) else {}
        records = getattr(pm, "_records", {}) or {}
        instances = getattr(pm, "_instances", {}) or {}
        ids = sorted(set(records.keys()) | set(instances.keys()))
        try:
            from engine.strategy_plugin import StrategyPlugin
        except Exception:  # noqa: BLE001
            StrategyPlugin = None  # type: ignore[misc, assignment]
        for pid in ids:
            inst = instances.get(pid)
            rec = records.get(pid)
            enabled = True
            if rec is not None:
                enabled = bool(getattr(rec, "enabled", True))
            block = plugin_cfg.get(pid) if isinstance(plugin_cfg, dict) else None
            if isinstance(block, dict) and block.get("enabled") is False:
                enabled = False
            kind = ""
            if inst is not None:
                kind = str(
                    getattr(
                        getattr(inst, "PLUGIN_KIND", None),
                        "value",
                        getattr(inst, "PLUGIN_KIND", ""),
                    )
                )
            elif rec is not None:
                kind = str(
                    getattr(
                        getattr(getattr(rec, "manifest", None), "kind", None),
                        "value",
                        "",
                    )
                )
            is_strategy = False
            if StrategyPlugin is not None and inst is not None:
                try:
                    is_strategy = isinstance(inst, StrategyPlugin)
                except Exception:
                    is_strategy = False
            if not is_strategy:
                pl = str(pid).lower()
                is_strategy = any(
                    h in pl
                    for h in (
                        "cross",
                        "reversion",
                        "strategy",
                        "ma_",
                        "mean_",
                        "momentum",
                        "trend",
                    )
                ) and pl not in {"ai_analyst", "order_execution"}
            cfg_brief: dict[str, Any] = {}
            observe_only = False
            if isinstance(block, dict):
                for k in (
                    "class_code",
                    "tickers",
                    "fast",
                    "slow",
                    "window",
                    "z_entry",
                    "quantity",
                    "mode",
                    "enabled",
                    "observe_only",
                ):
                    if k in block:
                        cfg_brief[k] = block[k]
                observe_only = bool(block.get("observe_only"))
            if inst is not None and hasattr(inst, "is_observe_only"):
                try:
                    observe_only = bool(inst.is_observe_only())
                except Exception:
                    pass
            plugins.append(
                {
                    "id": pid,
                    "running": bool(inst and getattr(inst, "is_running", False)),
                    "enabled": enabled,
                    "kind": kind,
                    "is_strategy": bool(is_strategy),
                    "observe_only": observe_only,
                    "config": cfg_brief,
                    "error": getattr(rec, "error", None) if rec else None,
                }
            )
        return plugins

    def _costs_snapshot(self) -> dict[str, Any]:
        try:
            active = self._cost_store().active_record()
            return {
                "active": active.model_dump(mode="json") if active else None,
            }
        except Exception as exc:  # Corrupt state must not take down UI snapshots.
            return {"active": None, "error": str(exc)}

    def _ai_snapshot(self) -> dict[str, Any]:
        pm = getattr(self.core, "plugin_manager", None)
        plugin = None
        if pm is not None:
            plugin = pm.get("ai_analyst") if hasattr(pm, "get") else None
            if plugin is None:
                plugin = getattr(pm, "_instances", {}).get("ai_analyst")
        raw = getattr(self.core.config, "raw", {}) or {}
        raw_cfg = (
            dict((raw.get("plugins") or {}).get("ai_analyst") or {})
            if isinstance(raw, dict)
            else {}
        )
        live_cfg = (
            dict(plugin.config)
            if plugin is not None and isinstance(getattr(plugin, "config", None), dict)
            else {}
        )
        # Shallow ``{**raw, **live}`` used to drop newly-saved profiles when the
        # live plugin still held a stale ``profiles`` blob — merge explicitly.
        cfg = {**raw_cfg, **live_cfg}
        raw_profiles = raw_cfg.get("profiles") if isinstance(raw_cfg.get("profiles"), dict) else {}
        live_profiles = (
            live_cfg.get("profiles") if isinstance(live_cfg.get("profiles"), dict) else {}
        )
        profiles = {**live_profiles, **raw_profiles}
        cfg["profiles"] = profiles
        profile_ids = list(profiles.keys())
        chat_profiles: list[dict[str, Any]] = []
        try:
            from ai_analyst.config.profiles import list_all_profiles_public

            # Resolve env-backed secrets so api_key_env profiles count as ready.
            resolved = dict(cfg)
            try:
                from host.secrets_env import load_secrets_into_environ

                load_secrets_into_environ()
            except Exception:
                pass
            try:
                from plugins.ai_analyst.plugin import _resolve_secrets

                resolved = _resolve_secrets(resolved)
            except Exception:
                pass
            chat_profiles = list_all_profiles_public(resolved)
        except Exception:
            chat_profiles = [
                {
                    "id": pid,
                    "display_name": str((block or {}).get("display_name") or pid),
                    "provider_type": str((block or {}).get("provider_type") or pid),
                    "enabled": bool((block or {}).get("enabled", True)),
                    "ready": True,
                }
                for pid, block in profiles.items()
                if isinstance(block, dict)
            ]
        tools: list[str] = []
        last: dict[str, Any] = {}
        ready = False
        err = None
        if plugin is not None:
            ready = getattr(plugin, "service", None) is not None
            err = getattr(plugin, "_last_error", None)
            last = dict(getattr(plugin, "_last_ask", None) or {})
            if hasattr(plugin, "tool_names"):
                try:
                    tools = list(plugin.tool_names() or [])
                except Exception:
                    tools = []
        return {
            "enabled": cfg.get("enabled", True) is not False,
            "provider": cfg.get("default_provider") or "mock",
            "ready": ready,
            "tools": tools,
            "tools_count": len(tools),
            "last": last,
            "error": err,
            "profiles": profile_ids,
            "chat_profiles": chat_profiles,
            "max_tool_iterations": cfg.get("max_tool_iterations", 5),
            "config": {
                "default_provider": cfg.get("default_provider"),
                "enabled": cfg.get("enabled", True),
                "max_tool_iterations": cfg.get("max_tool_iterations", 5),
                "profiles": redact_secrets(profiles) if isinstance(profiles, dict) else {},
            },
        }

    async def handle_command(self, cmd: dict[str, Any] | str) -> dict[str, Any]:
        if isinstance(cmd, str):
            raw = cmd.strip().upper()
            payload: dict[str, Any] = {"cmd": raw}
        else:
            payload = dict(cmd)
            raw = str(payload.get("cmd") or payload.get("action") or "").upper()

        request_id = payload.get("request_id")
        instance_id = str(await self.core.config.get("instance.id", "local") or "local")
        _log().info("cmd start raw={} rid={}", raw, request_id)

        try:
            self._authorize_command(raw, payload)
            result = await self._dispatch(raw, payload, instance_id)
        except Exception as exc:  # noqa: BLE001
            _log().exception("cmd crash raw={} err={}", raw, exc)
            result = {"ok": False, "cmd": raw, "error": str(exc)}

        if request_id is not None:
            result["request_id"] = request_id
        # AI tools call handle_command in-process (no UI ACK socket). Push a fresh
        # snapshot so Desk/Consilium/Strategy see lineup + book resets immediately.
        if result.get("ok") and raw in {
            "SET_ENSEMBLE",
            "SET_PLUGIN",
            "SET_STRATEGY_PARAMS",
            "SET_RISK",
            "SET_EXECUTION",
            "SET_QUARANTINE",
            "SET_TRADING",
            "RESET_PAPER",
            "CLEAR_PAPER",
            "RESET_SIM",
            "PHANTOM_RESET",
            "KILL",
            "HALT",
            "STOP",
            "EMERGENCY",
            "RESUME",
            "START",
        }:
            try:
                snap = self.build_snapshot()
                if raw == "SET_ENSEMBLE" and "ensemble" not in result:
                    result["ensemble"] = dict(snap.get("ensemble") or {})
                if raw in {"RESET_PAPER", "CLEAR_PAPER", "RESET_SIM"}:
                    result.setdefault(
                        "snapshot",
                        {
                            "equity": snap.get("equity"),
                            "gross_exposure": snap.get("gross_exposure"),
                            "positions": snap.get("positions") or {},
                            "open_positions": snap.get("open_positions") or [],
                            "exposures": snap.get("exposures") or {},
                            "halted": snap.get("halted"),
                            "halt_reason": snap.get("halt_reason") or "",
                        },
                    )
                if raw == "PHANTOM_RESET":
                    result.setdefault("phantom", snap.get("phantom"))
                await self._broadcast(snap)
                self._zmq_publish(snap)
            except Exception:
                _log().exception("post-cmd snapshot push failed raw={}", raw)
        _log().info(
            "cmd done raw={} ok={} err={}",
            raw,
            result.get("ok"),
            result.get("error"),
        )
        return result

    def _authorize_command(self, raw: str, payload: dict[str, Any]) -> None:
        """Authorize control mutations once; emergency HALT always remains open.

        Fails **closed**: if the control authorizer is not installed (safety
        service disabled, discovery error, start-order race) every privileged
        command is refused.  The previous behaviour — return early and let the
        command through — meant that losing one service silently turned the
        whole control plane into an unauthenticated one.
        """
        from engine.control_commands import (
            classify_control_command,
            is_live_mode,
            requires_authorization,
        )

        name = str(raw or "").strip().upper()
        if not requires_authorization(name):
            return

        runtime = getattr(self.core, "trading", None)
        command = classify_control_command(
            name,
            live_mode=is_live_mode(runtime),
            requested_mode=str(payload.get("mode") or "") if name == "SET_TRADING" else None,
        )
        if command is None:
            return

        authorizer = vars(self.core).get("control_authorizer")
        if authorizer is None:
            # Fail closed — but never at the cost of blocking risk reduction.
            if command.value == "halt":
                payload["_authorized_subject"] = "unauthenticated_emergency_halt"
                return
            from engine.control_auth import AuthorizationDenied

            raise AuthorizationDenied(
                f"control authorizer is not installed; refusing {name} (fail-closed)"
            )

        authorization = authorizer.authorize(
            command,
            token=payload.get("auth_token") or payload.get("token"),
            nonce=payload.get("nonce"),
        )
        principal = authorization.principal
        if principal is not None:
            payload["_authorized_subject"] = principal.subject
            payload["_authorized_roles"] = sorted(r.value for r in principal.roles)

    async def _dispatch(
        self, raw: str, payload: dict[str, Any], instance_id: str
    ) -> dict[str, Any]:
        if raw in {"KILL", "HALT", "STOP", "EMERGENCY"}:
            reason = str(payload.get("reason") or "ui_kill_switch")
            await self.core.event_bus.publish(
                KernelEvent(
                    event_type="system.halt",
                    source_id=self.component_id,
                    payload={"reason": reason},
                    instance_id=instance_id,
                )
            )
            return {
                "ok": True,
                "cmd": "KILL",
                "halted": True,
                "halt_reason": reason,
            }
        if raw in {"RESUME", "START"}:
            await self.core.event_bus.publish(
                KernelEvent(
                    event_type="system.resume",
                    source_id=self.component_id,
                    payload={},
                    instance_id=instance_id,
                )
            )
            return {"ok": True, "cmd": "RESUME", "halted": False}
        if raw in {"RESET_PAPER", "CLEAR_PAPER", "RESET_SIM"}:
            return await self._cmd_reset_paper(payload)
        if raw in {"PING", "STATUS"}:
            return {"ok": True, "cmd": "STATUS", "snapshot": self.build_snapshot()}
        if raw == "GET_CONFIG":
            return {
                "ok": True,
                "cmd": "GET_CONFIG",
                "config": operator_config_slice(getattr(self.core.config, "raw", {}) or {}),
            }
        if raw == "SAVE_CONFIG":
            await self.core.config.save()
            return {"ok": True, "cmd": "SAVE_CONFIG"}
        if raw == "SET_UNIVERSE":
            return await self._cmd_set_universe(payload)
        if raw == "SET_TRADING":
            return await self._cmd_set_trading(payload)
        if raw == "SET_ENSEMBLE":
            return await self._cmd_set_ensemble(payload)
        if raw == "SET_PLUGIN":
            return await self._cmd_set_plugin(payload)
        if raw == "SET_STRATEGY_PARAMS":
            return await self._cmd_set_strategy_params(payload)
        if raw == "SET_RISK":
            return await self._cmd_set_risk(payload)
        if raw == "SET_EXECUTION":
            return await self._cmd_set_execution(payload)
        if raw == "SET_QUARANTINE":
            return await self._cmd_set_quarantine(payload)
        if raw == "ASK":
            return await self._cmd_ask(payload)
        if raw == "SET_AI":
            return await self._cmd_set_ai(payload)
        if raw == "SET_SECRETS":
            return await self._cmd_set_secrets(payload)
        if raw == "SET_STORAGE":
            return await self._cmd_set_storage(payload)
        if raw == "COST_EXTRACT":
            return await self._cmd_cost_extract(payload)
        if raw == "COST_VALIDATE":
            return await self._cmd_cost_validate(payload)
        if raw == "COST_SAVE_DRAFT":
            return await self._cmd_cost_save_draft(payload)
        if raw == "COST_ACTIVATE":
            return await self._cmd_cost_activate(payload, instance_id)
        if raw == "PHANTOM_RUN":
            return await self._cmd_phantom_run(payload)
        if raw == "PHANTOM_WALK_FORWARD":
            return await self._cmd_phantom_walk_forward(payload)
        if raw == "PHANTOM_WALK_FORWARD_BATCH":
            return await self._cmd_phantom_walk_forward_batch(payload)
        if raw == "PHANTOM_APPLY_CHOSEN":
            return await self._cmd_phantom_apply_chosen(payload)
        if raw == "PHANTOM_RESET":
            return await self._cmd_phantom_reset(payload)
        if raw == "PHANTOM_CAPTURE":
            return await self._cmd_phantom_capture(payload)
        if raw == "PHANTOM_SYNC_METRICS":
            return await self._cmd_phantom_sync_metrics(payload)
        if raw == "PLACE_ORDER":
            return await self._cmd_place_order(payload)
        if raw == "CLOSE_POSITION":
            return await self._cmd_close_position(payload)
        if raw == "CLOSE_ALL":
            return await self._cmd_close_all(payload)
        if raw == "CANCEL_ORDER":
            return await self._cmd_cancel_order(payload)
        if raw == "CANCEL_ALL_ORDERS":
            return await self._cmd_cancel_all_orders(payload)
        if raw == "RELEASE_EXIT":
            return await self._cmd_release_exit(payload)
        if raw == "DRY_RUN_RISK":
            return await self._cmd_dry_run_risk(payload)
        if raw == "RECONCILE_NOW":
            return await self._cmd_reconcile_now(payload)
        return {"ok": False, "error": f"unknown_cmd:{raw}"}

    async def _cmd_reset_paper(self, payload: dict[str, Any]) -> dict[str, Any]:
        runtime = getattr(self.core, "trading", None)
        if runtime is None or not hasattr(runtime, "reset_sim_book"):
            return {"ok": False, "cmd": "RESET_PAPER", "error": "trading_unavailable"}
        mode = str(getattr(runtime, "mode", "") or "").lower()
        if mode not in {"paper", "sim", "simulation", "phantom", "replay"}:
            return {
                "ok": False,
                "cmd": "RESET_PAPER",
                "error": f"forbidden_in_mode:{mode}",
            }
        symbol = str(payload.get("symbol") or "").strip() or None
        strategy_id = str(payload.get("strategy_id") or "").strip() or None
        try:
            detail = runtime.reset_sim_book(symbol=symbol, strategy_id=strategy_id)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "cmd": "RESET_PAPER", "error": str(exc)}
        return {
            "ok": True,
            "cmd": "RESET_PAPER",
            "mode": mode,
            "detail": detail if isinstance(detail, dict) else {},
            "open_positions": runtime.open_positions()
            if hasattr(runtime, "open_positions")
            else [],
        }

    @staticmethod
    def _order_result_dict(result: Any) -> dict[str, Any]:
        return {
            "client_id": getattr(result, "client_id", ""),
            "status": getattr(
                getattr(result, "status", None), "value", str(getattr(result, "status", ""))
            ),
            "filled_qty": float(getattr(result, "filled_qty", 0) or 0),
            "avg_price": getattr(result, "avg_price", None),
            "reason": getattr(result, "reason", "") or "",
        }

    def _require_live_confirm(self, payload: dict[str, Any], cmd: str) -> dict[str, Any] | None:
        runtime = getattr(self.core, "trading", None)
        mode = str(getattr(runtime, "mode", "") or "").lower()
        if mode in {"live", "quik"} and payload.get("confirm_live") is not True:
            return {
                "ok": False,
                "cmd": cmd,
                "error": "confirm_live_required",
                "hint": "Pass confirm_live=true after explicit operator confirmation.",
            }
        return None

    async def _cmd_place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_live_confirm(payload, "PLACE_ORDER")
        if denied is not None:
            return denied
        runtime = getattr(self.core, "trading", None)
        if runtime is None or not hasattr(runtime, "operator_place_order"):
            return {"ok": False, "cmd": "PLACE_ORDER", "error": "trading_unavailable"}
        ticker = str(payload.get("ticker") or payload.get("symbol") or "").strip()
        class_code = str(payload.get("class_code") or "").strip()
        if ":" in ticker and not class_code:
            class_code, ticker = ticker.split(":", 1)
        side = str(payload.get("side") or payload.get("action") or "").strip()
        try:
            qty = float(payload.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if not ticker or not side or qty <= 0:
            return {
                "ok": False,
                "cmd": "PLACE_ORDER",
                "error": "ticker_side_quantity_required",
            }
        mark = payload.get("mark_price")
        limit = payload.get("limit_price")
        try:
            result = await runtime.operator_place_order(
                ticker=ticker,
                side=side,
                quantity=qty,
                class_code=class_code,
                mark_price=float(mark) if mark is not None else None,
                reduce_only=bool(payload.get("reduce_only")),
                order_type=str(payload.get("order_type") or "market"),
                limit_price=float(limit) if limit is not None else None,
                exit_reason=str(payload.get("reason") or "ai_operator"),
                client_id=str(payload["client_id"]) if payload.get("client_id") else None,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "cmd": "PLACE_ORDER", "error": str(exc)}
        row = self._order_result_dict(result)
        ok = str(row.get("status") or "").lower() in {"filled", "partial", "accepted"}
        return {"ok": ok, "cmd": "PLACE_ORDER", "order": row}

    async def _cmd_close_position(self, payload: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_live_confirm(payload, "CLOSE_POSITION")
        if denied is not None:
            return denied
        runtime = getattr(self.core, "trading", None)
        if runtime is None or not hasattr(runtime, "operator_close_position"):
            return {"ok": False, "cmd": "CLOSE_POSITION", "error": "trading_unavailable"}
        symbol = str(payload.get("symbol") or payload.get("ticker") or "").strip()
        if not symbol:
            return {"ok": False, "cmd": "CLOSE_POSITION", "error": "symbol_required"}
        qty = payload.get("quantity")
        mark = payload.get("mark_price")
        limit = payload.get("limit_price")
        try:
            result = await runtime.operator_close_position(
                symbol=symbol,
                quantity=float(qty) if qty is not None else None,
                mark_price=float(mark) if mark is not None else None,
                order_type=str(payload.get("order_type") or "market"),
                limit_price=float(limit) if limit is not None else None,
                exit_reason=str(payload.get("reason") or "ai_close"),
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "cmd": "CLOSE_POSITION", "error": str(exc)}
        row = self._order_result_dict(result)
        ok = str(row.get("status") or "").lower() in {"filled", "partial", "accepted"}
        return {"ok": ok, "cmd": "CLOSE_POSITION", "order": row}

    async def _cmd_close_all(self, payload: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_live_confirm(payload, "CLOSE_ALL")
        if denied is not None:
            return denied
        if payload.get("confirm_close_all") is not True:
            return {
                "ok": False,
                "cmd": "CLOSE_ALL",
                "error": "confirm_close_all_required",
            }
        runtime = getattr(self.core, "trading", None)
        if runtime is None or not hasattr(runtime, "operator_close_all"):
            return {"ok": False, "cmd": "CLOSE_ALL", "error": "trading_unavailable"}
        mark = payload.get("mark_price")
        limit = payload.get("limit_price")
        try:
            results = await runtime.operator_close_all(
                mark_price=float(mark) if mark is not None else None,
                order_type=str(payload.get("order_type") or "market"),
                limit_price=float(limit) if limit is not None else None,
                exit_reason=str(payload.get("reason") or "ai_close_all"),
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "cmd": "CLOSE_ALL", "error": str(exc)}
        orders = [self._order_result_dict(r) for r in results]
        ok = (
            all(
                str(o.get("status") or "").lower() in {"filled", "partial", "accepted"}
                for o in orders
            )
            if orders
            else True
        )
        return {"ok": ok, "cmd": "CLOSE_ALL", "orders": orders, "count": len(orders)}

    async def _cmd_cancel_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        runtime = getattr(self.core, "trading", None)
        if runtime is None or not hasattr(runtime, "operator_cancel_order"):
            return {"ok": False, "cmd": "CANCEL_ORDER", "error": "trading_unavailable"}
        client_id = str(payload.get("client_id") or payload.get("order_id") or "").strip()
        if not client_id:
            return {"ok": False, "cmd": "CANCEL_ORDER", "error": "client_id_required"}
        try:
            result = await runtime.operator_cancel_order(client_id)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "cmd": "CANCEL_ORDER", "error": str(exc)}
        return {"ok": bool(result.get("ok")), "cmd": "CANCEL_ORDER", **result}

    async def _cmd_cancel_all_orders(self, payload: dict[str, Any]) -> dict[str, Any]:
        runtime = getattr(self.core, "trading", None)
        if runtime is None or not hasattr(runtime, "operator_cancel_all_working"):
            return {
                "ok": False,
                "cmd": "CANCEL_ALL_ORDERS",
                "error": "trading_unavailable",
            }
        if payload.get("confirm_cancel_all") is not True:
            return {
                "ok": False,
                "cmd": "CANCEL_ALL_ORDERS",
                "error": "confirm_cancel_all_required",
            }
        try:
            result = await runtime.operator_cancel_all_working(
                symbol=str(payload.get("symbol") or "").strip() or None,
                confirm_cancel_all=True,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "cmd": "CANCEL_ALL_ORDERS", "error": str(exc)}
        return {"ok": bool(result.get("ok")), "cmd": "CANCEL_ALL_ORDERS", **result}

    async def _cmd_release_exit(self, payload: dict[str, Any]) -> dict[str, Any]:
        runtime = getattr(self.core, "trading", None)
        if runtime is None or not hasattr(runtime, "operator_release_exit"):
            return {"ok": False, "cmd": "RELEASE_EXIT", "error": "trading_unavailable"}
        released = runtime.operator_release_exit(
            slice_id=str(payload["slice_id"]) if payload.get("slice_id") else None,
            symbol=str(payload["symbol"]) if payload.get("symbol") else None,
            client_id=str(payload.get("client_id") or payload.get("order_id") or "") or None,
            force=bool(payload.get("force")),
        )
        return {
            "ok": True,
            "cmd": "RELEASE_EXIT",
            "released": released,
            "count": len(released),
        }

    async def _cmd_dry_run_risk(self, payload: dict[str, Any]) -> dict[str, Any]:
        runtime = getattr(self.core, "trading", None)
        if runtime is None or not hasattr(runtime, "preview_operator_risk"):
            return {"ok": False, "cmd": "DRY_RUN_RISK", "error": "trading_unavailable"}
        ticker = str(payload.get("ticker") or payload.get("symbol") or "").strip()
        class_code = str(payload.get("class_code") or "").strip()
        if ":" in ticker and not class_code:
            class_code, ticker = ticker.split(":", 1)
        side = str(payload.get("side") or payload.get("action") or "").strip()
        try:
            qty = float(payload.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if not ticker or not side or qty <= 0:
            return {
                "ok": False,
                "cmd": "DRY_RUN_RISK",
                "error": "ticker_side_quantity_required",
            }
        mark = payload.get("mark_price")
        limit = payload.get("limit_price")
        preview = runtime.preview_operator_risk(
            ticker=ticker,
            side=side,
            quantity=qty,
            class_code=class_code,
            mark_price=float(mark) if mark is not None else None,
            reduce_only=bool(payload.get("reduce_only")),
            order_type=str(payload.get("order_type") or "market"),
            limit_price=float(limit) if limit is not None else None,
        )
        recent = []
        orders = getattr(runtime, "orders", None)
        if orders is not None and hasattr(orders, "risk_trace"):
            recent = list(orders.risk_trace)[-20:]
        return {
            "ok": True,
            "cmd": "DRY_RUN_RISK",
            "preview": preview,
            "recent_risk_trace": recent,
        }

    async def _cmd_reconcile_now(self, payload: dict[str, Any]) -> dict[str, Any]:
        exe = self.core.service_registry.get("execution_service")
        if exe is None:
            return {"ok": False, "cmd": "RECONCILE_NOW", "error": "execution_service_missing"}
        recon = getattr(exe, "reconciliation", None)
        if recon is None:
            return {"ok": False, "cmd": "RECONCILE_NOW", "error": "reconciliation_unavailable"}
        venue = str(payload.get("venue") or getattr(exe, "default_venue", "") or "").strip()
        account = str(
            payload.get("account") or getattr(exe, "default_account", "") or "default"
        ).strip()
        if hasattr(exe, "_reconcile_all") and not venue:
            try:
                await exe._reconcile_all()  # noqa: SLF001
                return {"ok": True, "cmd": "RECONCILE_NOW", "scope": "all"}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "cmd": "RECONCILE_NOW", "error": str(exc)}
        if not venue:
            return {"ok": False, "cmd": "RECONCILE_NOW", "error": "venue_required"}
        try:
            result = await recon.reconcile(venue, account)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "cmd": "RECONCILE_NOW", "error": str(exc)}
        payload_out: dict[str, Any] = {
            "ok": bool(getattr(result, "healthy", True)),
            "cmd": "RECONCILE_NOW",
            "venue": venue,
            "account": account,
        }
        if hasattr(result, "to_dict"):
            payload_out["result"] = result.to_dict()
        else:
            payload_out["healthy"] = bool(getattr(result, "healthy", True))
        return payload_out

    async def _cmd_phantom_sync_metrics(self, payload: dict[str, Any]) -> dict[str, Any]:
        phantom = self.core.service_registry.get("phantom_service")
        snap = self._phantom_snapshot(phantom)
        return {
            "ok": True,
            "cmd": "PHANTOM_SYNC_METRICS",
            "metrics": snap.get("metrics") or {},
            "fills": snap.get("fills"),
            "source": (snap.get("metrics") or {}).get("source"),
        }

    def _cost_store(self) -> CostScheduleStore:
        raw = getattr(self.core.config, "raw", {}) or {}
        configured = ((raw.get("costs") or {}).get("store_path")) if isinstance(raw, dict) else None
        root = (
            Path(configured)
            if configured
            else Path(__file__).resolve().parents[2] / "data" / "cost_schedules"
        )
        return CostScheduleStore(root)

    @staticmethod
    def _source_from_payload(payload: dict[str, Any]) -> SourceDocument:
        raw = payload.get("source")
        if isinstance(raw, dict):
            return SourceDocument.model_validate(raw)
        # COST_EXTRACT accepts document fields, never chat text/message fields.
        allowed = {
            key: payload[key]
            for key in ("document_id", "title", "text", "source_uri", "retrieved_at", "sha256")
            if key in payload
        }
        return SourceDocument.model_validate(allowed)

    async def _cmd_cost_extract(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Register a hash-grounded source envelope; extraction never changes config."""
        try:
            source = self._source_from_payload(payload)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "cmd": "COST_EXTRACT", "error": str(exc)}
        return {
            "ok": True,
            "cmd": "COST_EXTRACT",
            "source": source.model_dump(mode="json"),
            "proposal_only": True,
        }

    async def _cmd_cost_validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            source = self._source_from_payload(payload)
            draft_raw = payload.get("draft")
            if not isinstance(draft_raw, dict):
                raise ValueError("structured draft object is required")
            draft = CostScheduleDraft.model_validate(draft_raw)
            report = validate_cost_draft(
                source,
                draft,
                active=self._cost_store().load_active_schedule(),
                impact_contexts=payload.get("impact_contexts") or (),
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "cmd": "COST_VALIDATE", "error": str(exc)}
        return {
            "ok": True,
            "cmd": "COST_VALIDATE",
            "validation": report.model_dump(mode="json"),
            "draft": draft.model_dump(mode="json"),
        }

    async def _cmd_cost_save_draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            source = self._source_from_payload(payload)
            draft_raw = payload.get("draft")
            if not isinstance(draft_raw, dict):
                raise ValueError("structured draft object is required")
            draft = CostScheduleDraft.model_validate(draft_raw)
            created_by = str(
                payload.get("operator_id") or payload.get("created_by") or "local_operator"
            )
            stored = self._cost_store().save_draft(
                source,
                draft,
                created_by=created_by,
                impact_contexts=payload.get("impact_contexts") or (),
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "cmd": "COST_SAVE_DRAFT", "error": str(exc)}
        return {
            "ok": True,
            "cmd": "COST_SAVE_DRAFT",
            "draft_hash": stored.draft_hash,
            "schedule_hash": stored.schedule_hash,
            "validation": stored.validation.model_dump(mode="json"),
            "active": False,
        }

    def _cost_operator(self, payload: dict[str, Any]) -> str:
        """Use the control-plane principal when that facility is installed."""
        if payload.get("_authorized_subject"):
            return str(payload["_authorized_subject"])
        authorizer = vars(self.core).get("control_authorizer")
        if authorizer is None:
            return str(payload.get("operator_id") or "local_operator")
        from engine.control_auth import ControlCommand

        authorization = authorizer.authorize(
            ControlCommand.CONFIG,
            token=payload.get("auth_token") or payload.get("token"),
            nonce=payload.get("nonce"),
        )
        principal = authorization.principal
        if principal is None:
            raise PermissionError("authenticated operator context required")
        return principal.subject

    async def _cmd_cost_activate(self, payload: dict[str, Any], instance_id: str) -> dict[str, Any]:
        if payload.get("confirm_cost_schedule") is not True:
            return {
                "ok": False,
                "cmd": "COST_ACTIVATE",
                "error": "confirm_cost_schedule_required",
            }
        expected_hash = str(payload.get("expected_hash") or "")
        if len(expected_hash) != 64:
            return {"ok": False, "cmd": "COST_ACTIVATE", "error": "exact_expected_hash_required"}
        requested_hash = payload.get("draft_hash")
        if requested_hash is not None and str(requested_hash) != expected_hash:
            return {"ok": False, "cmd": "COST_ACTIVATE", "error": "stale_expected_hash"}
        try:
            operator = self._cost_operator(payload)
            record = self._cost_store().activate(expected_hash, activated_by=operator)
            runtime = getattr(self.core, "trading", None)
            if runtime is not None and hasattr(runtime, "reload_active_cost_schedule"):
                runtime.reload_active_cost_schedule()
            await self.core.event_bus.publish(
                KernelEvent(
                    event_type="cost.schedule_activated",
                    source_id=self.component_id,
                    payload={
                        "draft_hash": record.draft_hash,
                        "schedule_hash": record.schedule_hash,
                        "operator": operator,
                    },
                    instance_id=instance_id,
                )
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "cmd": "COST_ACTIVATE", "error": str(exc)}
        return {
            "ok": True,
            "cmd": "COST_ACTIVATE",
            "activation": record.model_dump(mode="json"),
        }

    def _phantom_snapshot(self, phantom: Any) -> dict[str, Any]:
        if phantom is None:
            return {
                "fills": 0,
                "capturing": False,
                "grid_running": False,
                "metrics": {},
                "last_grid": [],
                "last_run": None,
                "recent_fills": [],
            }
        fills = list(getattr(phantom, "_fills", []) or [])
        start_eq = float((getattr(phantom, "config", {}) or {}).get("start_equity") or 10_000.0)
        metrics: dict[str, Any] = {}
        try:
            if hasattr(phantom, "_metrics"):
                metrics = dict(phantom._metrics() or {})  # noqa: SLF001
            else:
                from engine.indicators import equity_pnl

                metrics = equity_pnl(fills, start_equity=start_eq)
        except Exception:
            metrics = {}
        recent = []
        for f in fills[-40:]:
            recent.append(
                {
                    "action": f.get("action"),
                    "ticker": f.get("ticker"),
                    "quantity": f.get("quantity"),
                    "price": f.get("price"),
                    "status": f.get("status"),
                    "strategy_id": f.get("strategy_id"),
                    "reason": f.get("reason"),
                    "bar_index": f.get("bar_index"),
                    "ts": f.get("ts"),
                }
            )
        grid = list(getattr(phantom, "_last_grid", None) or [])
        ohlc = getattr(phantom, "_last_ohlc", None)
        timeline = None
        tl_obj = getattr(phantom, "timeline", None)
        if tl_obj is not None and hasattr(tl_obj, "export_ui"):
            try:
                timeline = tl_obj.export_ui()
            except Exception:
                timeline = None
        if timeline is None:
            last_run = getattr(phantom, "_last_run", None) or {}
            if isinstance(last_run, dict) and isinstance(last_run.get("timeline"), dict):
                timeline = dict(last_run["timeline"])
        strategy_schemas: dict[str, Any] = {}
        pm = getattr(self.core, "plugin_manager", None)
        plugins = getattr(pm, "plugins", None) or {}
        if isinstance(plugins, dict):
            for pid, plugin in plugins.items():
                if hasattr(plugin, "config_schema") and callable(plugin.config_schema):
                    try:
                        schema = plugin.config_schema()
                    except Exception:
                        schema = None
                    if isinstance(schema, dict) and schema:
                        strategy_schemas[str(pid)] = schema
        sizing = None
        if hasattr(phantom, "sizing") and phantom.sizing is not None:
            try:
                sizing = phantom.sizing.to_dict()
            except Exception:
                sizing = None
        return {
            "fills": len(fills),
            "capturing": bool(getattr(phantom, "_capturing", True)),
            "grid_running": bool(getattr(phantom, "_grid_running", False)),
            "metrics": {
                "pnl": metrics.get("pnl"),
                "trades": metrics.get("trades"),
                "max_drawdown": metrics.get("max_drawdown"),
                "final_equity": metrics.get("final_equity"),
                "position": metrics.get("position"),
                "gross_exposure": metrics.get("gross_exposure"),
                "leverage": metrics.get("leverage"),
                "source": metrics.get("source"),
            },
            "last_grid": grid,
            "last_run": getattr(phantom, "_last_run", None),
            "last_ohlc": dict(ohlc) if isinstance(ohlc, dict) else None,
            "recent_fills": recent,
            "timeline": timeline,
            "start_equity": start_eq,
            "strategy_schemas": strategy_schemas,
            "sizing": sizing,
        }

    async def _cmd_phantom_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        phantom = self.core.service_registry.get("phantom_service")
        if phantom is None or not hasattr(phantom, "run_grid"):
            return {"ok": False, "cmd": "PHANTOM_RUN", "error": "phantom_unavailable"}
        if getattr(phantom, "_grid_running", False):
            return {"ok": False, "cmd": "PHANTOM_RUN", "error": "already_running"}
        ticker = str(payload.get("ticker") or "").strip()
        if not ticker:
            return {"ok": False, "cmd": "PHANTOM_RUN", "error": "missing_ticker"}
        class_code = str(payload.get("class_code") or "TQBR").strip() or "TQBR"
        provider = payload.get("provider")
        if provider in ("", None, "auto"):
            provider = None
        else:
            provider = str(provider)
        try:
            limit = int(payload.get("limit") or 60)
        except (TypeError, ValueError):
            limit = 60
        strategy_id = str(payload.get("strategy_id") or "ma_cross_example").strip()
        mode = str(payload.get("mode") or "grid").lower()
        feed = str(payload.get("feed") or "").strip().lower() or None
        patterns = payload.get("patterns")
        if isinstance(patterns, str) and patterns.strip():
            patterns = [p.strip() for p in patterns.split(",") if p.strip()]
        elif not isinstance(patterns, list):
            patterns = None
        variants = payload.get("variants")
        if not isinstance(variants, list) or not variants:
            try:
                max_v = int(payload.get("max_variants") or (36 if mode == "optimize" else 12))
            except (TypeError, ValueError):
                max_v = 36 if mode == "optimize" else 12
            from host.phantom_variants import variants_for_mode

            plugin = self.core.plugin_manager.get(strategy_id)
            schema = getattr(plugin, "CONFIG_SCHEMA", None) if plugin is not None else None
            if not isinstance(schema, dict):
                schema = None
            variants = variants_for_mode(strategy_id, mode, max_variants=max_v, schema=schema)
        try:
            sizing = payload.get("sizing") if isinstance(payload.get("sizing"), dict) else None
            strategy_params = (
                payload.get("strategy_params")
                if isinstance(payload.get("strategy_params"), dict)
                else None
            )

            def _opt_float(key: str) -> float | None:
                raw = payload.get(key)
                if raw in (None, ""):
                    return None
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    return None

            results = await phantom.run_grid(
                ticker,
                class_code=class_code,
                provider=provider,
                limit=limit,
                strategy_id=strategy_id,
                variants=list(variants),
                feed=feed,
                patterns=patterns,
                sizing=sizing,
                strategy_params=strategy_params,
                start_equity=_opt_float("start_equity"),
                fee_bps=_opt_float("fee_bps"),
                slippage_bps=_opt_float("slippage_bps"),
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "cmd": "PHANTOM_RUN", "error": str(exc)}
        snap = self._phantom_snapshot(phantom)
        return {
            "ok": True,
            "cmd": "PHANTOM_RUN",
            "grid": results,
            "ohlc": snap.get("last_ohlc"),
            "strategy_id": strategy_id,
            "mode": mode,
            "phantom": snap,
        }

    async def _cmd_phantom_walk_forward(self, payload: dict[str, Any]) -> dict[str, Any]:
        phantom = self.core.service_registry.get("phantom_service")
        if phantom is None or not hasattr(phantom, "run_walk_forward"):
            return {
                "ok": False,
                "cmd": "PHANTOM_WALK_FORWARD",
                "error": "phantom_unavailable",
            }
        ticker = str(payload.get("ticker") or "").strip()
        if not ticker:
            return {
                "ok": False,
                "cmd": "PHANTOM_WALK_FORWARD",
                "error": "missing_ticker",
            }

        def _int(key: str, default: int | None = None) -> int | None:
            raw = payload.get(key)
            if raw in (None, ""):
                return default
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default

        def _float(key: str) -> float | None:
            raw = payload.get(key)
            if raw in (None, ""):
                return None
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None

        step_raw = payload.get("step")
        step: int | None
        try:
            step = int(step_raw) if step_raw not in (None, "") else None
        except (TypeError, ValueError):
            step = None
        variants = payload.get("variants")
        if not isinstance(variants, list):
            variants = None
        variant_mode = payload.get("variant_mode") or payload.get("search_mode")
        if variant_mode in ("", None):
            variant_mode = None
        else:
            variant_mode = str(variant_mode).strip().lower()
        try:
            max_variants = (
                int(payload["max_variants"])
                if payload.get("max_variants") not in (None, "")
                else None
            )
        except (TypeError, ValueError):
            max_variants = None
        nested = payload.get("walk_forward")
        if not isinstance(nested, dict):
            nested = {}
        # Flat payload keys override nested walk_forward block.
        flat_keys = (
            "feed",
            "seed",
            "pattern_seed",
            "bars",
            "limit",
            "train_bars",
            "test_bars",
            "step",
            "min_folds",
            "auto_fit_folds",
            "selection_mode",
            "top_k",
            "min_fold_wins",
            "bootstrap_samples",
            "bootstrap_ci",
            "score_key",
            "persist_report",
            "report_dir",
            "pattern",
            "timeframe",
            "include_baselines",
            "variant_mode",
            "max_variants",
            "holdout_bars",
        )
        for key in flat_keys:
            if key in payload and payload[key] is not None:
                nested[key] = payload[key]
        qty_raw = payload.get("quantity")
        quantity = None
        if qty_raw is not None:
            try:
                quantity = float(qty_raw)
            except (TypeError, ValueError):
                quantity = None
        try:
            holdout_bars = (
                int(payload["holdout_bars"])
                if payload.get("holdout_bars") not in (None, "")
                else None
            )
        except (TypeError, ValueError):
            holdout_bars = None
        try:
            result = await phantom.run_walk_forward(
                ticker,
                class_code=str(payload.get("class_code") or "TQBR").strip() or "TQBR",
                provider=payload.get("provider"),
                limit=_int("bars", _int("limit")),
                strategy_id=str(payload.get("strategy_id") or "ma_cross_example").strip(),
                variants=variants,
                variant_mode=variant_mode,
                max_variants=max_variants,
                holdout_bars=holdout_bars,
                train_bars=_int("train_bars"),
                test_bars=_int("test_bars"),
                step=step,
                feed=str(payload["feed"]).strip() if payload.get("feed") else None,
                pattern=str(payload["pattern"]).strip() if payload.get("pattern") else None,
                seed=_int("seed", _int("pattern_seed")),
                start_equity=_float("start_equity"),
                quantity=quantity,
                walk_forward=nested,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "cmd": "PHANTOM_WALK_FORWARD", "error": str(exc)}
        prov = result.get("provenance") if isinstance(result.get("provenance"), dict) else {}
        oos = result.get("oos") if isinstance(result.get("oos"), dict) else {}
        digest = result.get("digest") if isinstance(result.get("digest"), dict) else {}
        return {
            "ok": True,
            "cmd": "PHANTOM_WALK_FORWARD",
            "walk_forward": result,
            "provenance": prov,
            "digest": digest,
            "holdout": result.get("holdout"),
            "feed": result.get("feed") or prov.get("feed"),
            "seed": result.get("seed") if result.get("seed") is not None else prov.get("seed"),
            "folds": len(result.get("folds") or []),
            "chosen": result.get("chosen"),
            "stable_family": result.get("stable_family"),
            "oos": oos,
            "baselines": result.get("baselines"),
            "mean_oos_pnl": oos.get("mean_oos_pnl"),
            "fold_pnl_std": oos.get("fold_pnl_std"),
            "ci_low": oos.get("ci_low"),
            "ci_high": oos.get("ci_high"),
            "win_rate": oos.get("win_rate"),
            "profit_factor": oos.get("profit_factor"),
            "mean_oos_max_drawdown": oos.get("mean_oos_max_drawdown"),
            "total_oos_trades": oos.get("total_oos_trades"),
            "phantom": self._phantom_snapshot(phantom),
        }

    async def _cmd_phantom_walk_forward_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        phantom = self.core.service_registry.get("phantom_service")
        if phantom is None or not hasattr(phantom, "run_walk_forward_batch"):
            return {
                "ok": False,
                "cmd": "PHANTOM_WALK_FORWARD_BATCH",
                "error": "phantom_unavailable",
            }
        ticker = str(payload.get("ticker") or "").strip()
        raw_tickers = payload.get("tickers")
        tickers: list[str] | None = None
        if isinstance(raw_tickers, str) and raw_tickers.strip():
            tickers = [p.strip() for p in raw_tickers.split(",") if p.strip()]
        elif isinstance(raw_tickers, list):
            tickers = [str(x).strip() for x in raw_tickers if str(x).strip()]
        if not ticker and not tickers:
            return {
                "ok": False,
                "cmd": "PHANTOM_WALK_FORWARD_BATCH",
                "error": "missing_ticker",
            }
        raw_ids = payload.get("strategy_ids") or payload.get("strategies")
        if isinstance(raw_ids, str):
            ids = [p.strip() for p in raw_ids.split(",") if p.strip()]
        elif isinstance(raw_ids, list):
            ids = [str(x).strip() for x in raw_ids if str(x).strip()]
        else:
            ids = []
        if not ids:
            return {
                "ok": False,
                "cmd": "PHANTOM_WALK_FORWARD_BATCH",
                "error": "strategy_ids_required",
            }
        nested = payload.get("walk_forward")
        if not isinstance(nested, dict):
            nested = {}
        for key in (
            "feed",
            "seed",
            "bars",
            "train_bars",
            "test_bars",
            "step",
            "min_folds",
            "timeframe",
            "selection_mode",
            "top_k",
            "variant_mode",
            "max_variants",
            "holdout_bars",
            "include_baselines",
        ):
            if key in payload and payload[key] is not None:
                nested[key] = payload[key]
        try:
            max_v = (
                int(payload["max_variants"])
                if payload.get("max_variants") not in (None, "")
                else 36
            )
        except (TypeError, ValueError):
            max_v = 36
        try:
            holdout = (
                int(payload["holdout_bars"])
                if payload.get("holdout_bars") not in (None, "")
                else None
            )
        except (TypeError, ValueError):
            holdout = None
        try:
            limit = (
                int(payload["bars"])
                if payload.get("bars") not in (None, "")
                else (int(payload["limit"]) if payload.get("limit") not in (None, "") else None)
            )
        except (TypeError, ValueError):
            limit = None
        try:
            result = await phantom.run_walk_forward_batch(
                ids,
                ticker or None,
                tickers=tickers,
                class_code=str(payload.get("class_code") or "CRYPTO").strip() or "CRYPTO",
                provider=payload.get("provider"),
                limit=limit,
                variant_mode=str(
                    payload.get("variant_mode") or nested.get("variant_mode") or "optimize"
                ),
                max_variants=max_v,
                holdout_bars=holdout,
                walk_forward=nested,
            )
        except Exception as exc:  # noqa: BLE001
            from engine.walk_forward import WalkForwardTooShortError

            err: dict[str, Any] = {
                "ok": False,
                "cmd": "PHANTOM_WALK_FORWARD_BATCH",
                "error": str(exc),
            }
            if isinstance(exc, WalkForwardTooShortError):
                err.update(
                    {
                        "bars": exc.bars,
                        "bars_needed": exc.bars_needed,
                        "train_bars": exc.train_bars,
                        "test_bars": exc.test_bars,
                        "step": exc.step,
                        "min_folds": exc.min_folds,
                        "hint": (
                            f"Pass bars≥{exc.bars_needed} (default 500), or lower "
                            "min_folds/train_bars."
                        ),
                    }
                )
            return err
        return {
            "ok": True,
            "cmd": "PHANTOM_WALK_FORWARD_BATCH",
            **result,
            "phantom": self._phantom_snapshot(phantom),
        }

    async def _cmd_phantom_apply_chosen(self, payload: dict[str, Any]) -> dict[str, Any]:
        phantom = self.core.service_registry.get("phantom_service")
        if phantom is None or not hasattr(phantom, "apply_chosen_params"):
            return {
                "ok": False,
                "cmd": "PHANTOM_APPLY_CHOSEN",
                "error": "phantom_unavailable",
            }
        params = payload.get("params") if isinstance(payload.get("params"), dict) else None
        persist = bool(payload.get("persist", True))
        result = phantom.apply_chosen_params(
            strategy_id=str(payload.get("strategy_id") or "").strip() or None,
            params=params,
            persist=persist,
            from_last=bool(payload.get("from_last", True)),
            sync_research_ticker=bool(payload.get("sync_research_ticker", True)),
        )
        if not result.get("ok"):
            return {"ok": False, "cmd": "PHANTOM_APPLY_CHOSEN", **result}
        if persist:
            try:
                await self.core.config.save()
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "cmd": "PHANTOM_APPLY_CHOSEN",
                    "error": f"persist_failed:{exc}",
                    **result,
                }
        # Re-score readiness with the params we just wrote (plugins.<sid>).
        readiness: dict[str, Any] | None = None
        try:
            from engine.research_gate import (
                evaluate_live_readiness,
                plugin_params_from_config,
                policy_from_config,
            )

            raw_cfg = getattr(self.core.config, "raw", None)
            sid = str(result.get("strategy_id") or "")
            ticker = str(result.get("research_ticker") or "").strip() or None
            plugged = plugin_params_from_config(raw_cfg if isinstance(raw_cfg, dict) else None, sid)
            readiness = evaluate_live_readiness(
                sid,
                ticker=ticker,
                policy=policy_from_config(raw_cfg if isinstance(raw_cfg, dict) else None),
                plugin_params=plugged or result.get("params"),
            )
            grade = str(readiness.get("grade") or "research_only")
            if grade == "live_ok":
                next_hint = "set_ensemble_lineup(strategies=[...]) when ready for canary live"
            elif readiness.get("observe_ok"):
                next_hint = "set_ensemble_lineup(observe=[...]) — observe_ok, not live_ok yet"
            else:
                next_hint = "research_only — fix blockers before observe/live"
            result["readiness"] = readiness
            result["grade"] = grade
            result["observe_ok"] = bool(readiness.get("observe_ok"))
            result["live_ok"] = bool(readiness.get("live_ok"))
            result["blockers"] = list(readiness.get("blockers") or [])
            result["next_hint"] = next_hint
        except Exception as exc:  # noqa: BLE001
            result["readiness_error"] = str(exc)
        return {"ok": True, "cmd": "PHANTOM_APPLY_CHOSEN", **result}

    async def _cmd_phantom_reset(self, payload: dict[str, Any]) -> dict[str, Any]:
        phantom = self.core.service_registry.get("phantom_service")
        if phantom is None:
            return {"ok": False, "cmd": "PHANTOM_RESET", "error": "phantom_unavailable"}
        sid = str(payload.get("strategy_id") or "").strip() or None
        symbol = str(payload.get("symbol") or "").strip() or None
        confirm = bool(payload.get("confirm_scoped"))
        if hasattr(phantom, "reset_ledger"):
            result = phantom.reset_ledger(strategy_id=sid, symbol=symbol, confirm_scoped=confirm)
            if isinstance(result, dict) and result.get("ok") is False:
                return {"ok": False, "cmd": "PHANTOM_RESET", **result}
        else:
            result = {"ok": True, "scope": "all"}
        if not sid and not symbol:
            phantom._last_grid = []  # noqa: SLF001
            phantom._last_run = None  # noqa: SLF001
            phantom._last_ohlc = None  # noqa: SLF001
        return {
            "ok": True,
            "cmd": "PHANTOM_RESET",
            **(result if isinstance(result, dict) else {}),
            "phantom": self._phantom_snapshot(phantom),
        }

    async def _cmd_phantom_capture(self, payload: dict[str, Any]) -> dict[str, Any]:
        phantom = self.core.service_registry.get("phantom_service")
        if phantom is None:
            return {"ok": False, "cmd": "PHANTOM_CAPTURE", "error": "phantom_unavailable"}
        if "capturing" in payload:
            phantom._capturing = bool(payload.get("capturing"))  # noqa: SLF001
            if isinstance(getattr(phantom, "config", None), dict):
                phantom.config["capture_signals"] = phantom._capturing  # noqa: SLF001
        return {
            "ok": True,
            "cmd": "PHANTOM_CAPTURE",
            "capturing": bool(getattr(phantom, "_capturing", False)),
            "phantom": self._phantom_snapshot(phantom),
        }

    async def _cmd_set_universe(self, payload: dict[str, Any]) -> dict[str, Any]:
        from engine.universe import normalize_universe_mode, resolve_screen_policy

        raw_cfg = getattr(self.core.config, "raw", None)
        if not isinstance(raw_cfg, dict):
            return {"ok": False, "cmd": "SET_UNIVERSE", "error": "no_config"}

        mode = payload.get("mode")
        static_active = payload.get("static_active")
        profile = payload.get("profile")
        persist = bool(payload.get("persist", True))
        mode_norm: str | None = None
        if mode is not None:
            mode_norm = normalize_universe_mode(str(mode))
            set_path(raw_cfg, "universe.mode", mode_norm)
        if static_active is not None:
            keys = [str(x) for x in list(static_active)]
            set_path(raw_cfg, "universe.static_active", keys)
        if profile is not None:
            set_path(raw_cfg, "universe.profile", str(profile))

        uni_svc = self.core.service_registry.get("universe_service")
        applied = False
        active: list[str] = []
        reason = ""
        snap_profile = ""
        if uni_svc is not None:
            mgr = getattr(uni_svc, "_manager", None)
            if mgr is not None:
                if mode_norm is not None:
                    mgr.config.mode = mode_norm
                if static_active is not None:
                    mgr.config.static_active = tuple(str(x) for x in list(static_active))
                if profile is not None:
                    uni_block = dict((raw_cfg.get("universe") or {}))
                    uni_block["profile"] = str(profile)
                    mgr.config.screen = resolve_screen_policy(uni_block)
                setattr(self.core, "universe", mgr)
            snap = await uni_svc.reselect_now(reason="ui_set_universe")
            applied = True
            if snap is not None:
                active = list(snap.active_keys())
                reason = str(getattr(snap, "reason", "") or "")
                snap_profile = str(getattr(snap, "profile", "") or "")

        if persist:
            await self.core.config.save()

        pinned: list[str] = []
        runtime = getattr(self.core, "trading", None)
        if runtime is not None and hasattr(runtime, "open_position_keys"):
            try:
                pinned = sorted(runtime.open_position_keys())
            except Exception:
                pinned = []

        return {
            "ok": True,
            "cmd": "SET_UNIVERSE",
            "applied": applied,
            "mode": mode_norm
            or str(
                ((raw_cfg.get("universe") or {}) if isinstance(raw_cfg, dict) else {}).get("mode")
                or ""
            ),
            "active_keys": active,
            "profile": snap_profile or (str(profile) if profile is not None else ""),
            "reason": reason,
            "pinned_keys": pinned,
            "needs_restart": False,
        }

    async def _live_lineup_blocker(self, mode_s: str) -> dict[str, Any] | None:
        """Why live trading cannot be armed, or ``None`` if it can.

        Returns a payload with ``blockers`` naming each strategy and the specific
        research-gate reasons it is not cleared, so the operator sees *what to fix*
        rather than a generic refusal.
        """
        ens = self.core.service_registry.get("ensemble_service")
        if ens is None:
            return {
                "detail": "ensemble_service is not available, so no lineup can be "
                "verified as cleared for live trading",
                "blockers": [],
            }
        lineup = [str(x) for x in (ens.lineup_ids() if hasattr(ens, "lineup_ids") else ()) if x]
        if not lineup:
            return {
                "detail": "the live Consilium lineup is empty. Every member was "
                "demoted by the research gate, or none was configured.",
                "blockers": [],
            }
        try:
            from engine.research_gate import (
                LiveReadinessPolicy,
                evaluate_live_readiness,
            )

            trading_cfg = dict(await self.core.config.get("trading", {}) or {})
            policy = LiveReadinessPolicy.from_mapping(trading_cfg.get("live_readiness"))
        except Exception:
            # Fail closed: an unreadable gate must not authorise live trading.
            return {
                "detail": "the research gate could not be evaluated, so no strategy "
                "can be confirmed as cleared for live trading",
                "blockers": [],
            }

        blockers: list[dict[str, Any]] = []
        for sid in lineup:
            try:
                verdict = evaluate_live_readiness(sid, policy=policy)
            except Exception as exc:  # noqa: BLE001
                blockers.append(
                    {"strategy_id": sid, "reasons": [f"gate_error:{type(exc).__name__}"]}
                )
                continue
            if verdict.get("live_ok"):
                continue
            reasons = [
                str(b.get("reason")) for b in (verdict.get("blockers") or []) if b.get("reason")
            ] or [f"grade={verdict.get('grade')}"]
            blockers.append({"strategy_id": sid, "reasons": reasons})

        if blockers:
            return {
                "detail": (
                    f"{len(blockers)} of {len(lineup)} configured live "
                    f"strateg{'y is' if len(lineup) == 1 else 'ies are'} not "
                    "cleared for live trading. Run walk_forward with "
                    "holdout_bars > 0, or set trading.live_readiness.enforce: false "
                    "to trade unvalidated strategies deliberately."
                ),
                "blockers": blockers,
                "lineup": lineup,
            }
        return None

    async def _cmd_set_risk(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Set risk limits and the strength floor without editing YAML.

        Every value is range-checked here. A limit set to a meaningless number
        (a negative daily loss, a concentration above 1.0) is worse than an
        unset one, because it reads as a deliberate choice.
        """
        raw_cfg = getattr(self.core.config, "raw", None)
        if not isinstance(raw_cfg, dict):
            return {"ok": False, "cmd": "SET_RISK", "error": "no_config"}

        applied: dict[str, float] = {}
        rejected: dict[str, str] = {}
        for key, (low, high) in _RISK_KNOBS.items():
            if key not in payload or payload[key] is None:
                continue
            try:
                value = float(payload[key])
            except (TypeError, ValueError):
                rejected[key] = "not_a_number"
                continue
            if value < low or value > high:
                rejected[key] = f"out_of_range[{low},{high}]"
                continue
            set_path(raw_cfg, f"trading.risk.{key}", value)
            applied[key] = value

        if not applied and not rejected:
            return {
                "ok": False,
                "cmd": "SET_RISK",
                "error": "nothing_to_set",
                "editable": sorted(_RISK_KNOBS),
            }

        runtime = getattr(self.core, "trading", None)
        risk = getattr(runtime, "risk", None)
        if risk is not None:
            limits = risk.limits_for(None)
            for key, value in applied.items():
                if key == "strength_floor":
                    continue
                if hasattr(limits, key):
                    setattr(limits, key, value)

        phantom = self.core.service_registry.get("phantom_service")
        sizing = getattr(phantom, "sizing", None) if phantom is not None else None
        if sizing is not None and "strength_floor" in applied:
            sizing.strength_floor = applied["strength_floor"]
            if hasattr(phantom, "apply_sizing_config"):
                phantom.apply_sizing_config()

        if bool(payload.get("persist", True)):
            await self.core.config.save()

        return {
            "ok": not rejected,
            "cmd": "SET_RISK",
            "applied": applied,
            "rejected": rejected,
            "note": ("limits apply to new orders; positions already open are not resized"),
        }

    async def _cmd_set_execution(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Set resting-order lifetime and sweep cadence."""
        raw_cfg = getattr(self.core.config, "raw", None)
        if not isinstance(raw_cfg, dict):
            return {"ok": False, "cmd": "SET_EXECUTION", "error": "no_config"}

        applied: dict[str, float] = {}
        rejected: dict[str, str] = {}
        for key, (low, high) in _EXECUTION_KNOBS.items():
            if key not in payload or payload[key] is None:
                continue
            try:
                value = float(payload[key])
            except (TypeError, ValueError):
                rejected[key] = "not_a_number"
                continue
            if value < low or value > high:
                rejected[key] = f"out_of_range[{low},{high}]"
                continue
            set_path(raw_cfg, f"services.execution_service.{key}", value)
            applied[key] = value

        post_only = payload.get("post_only")
        if post_only is not None:
            set_path(raw_cfg, "services.execution_service.post_only", bool(post_only))
            applied["post_only"] = bool(post_only)

        if not applied and not rejected:
            return {
                "ok": False,
                "cmd": "SET_EXECUTION",
                "error": "nothing_to_set",
                "editable": sorted(_EXECUTION_KNOBS) + ["post_only"],
            }

        service = self.core.service_registry.get("execution_service")
        if service is not None:
            for key, value in applied.items():
                if key in _EXECUTION_KNOBS:
                    service.config[key] = value
                elif key == "post_only" and hasattr(service, "post_only"):
                    service.post_only = value

        if bool(payload.get("persist", True)):
            await self.core.config.save()
        return {
            "ok": not rejected,
            "cmd": "SET_EXECUTION",
            "applied": applied,
            "rejected": rejected,
        }

    async def _cmd_set_quarantine(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Set the per-strategy circuit breaker's thresholds.

        This decides when a strategy is stopped from trading on its own live
        results, so the thresholds are an operator policy rather than an
        implementation detail.
        """
        raw_cfg = getattr(self.core.config, "raw", None)
        if not isinstance(raw_cfg, dict):
            return {"ok": False, "cmd": "SET_QUARANTINE", "error": "no_config"}

        applied: dict[str, Any] = {}
        rejected: dict[str, str] = {}
        for key, (low, high) in _QUARANTINE_KNOBS.items():
            if key not in payload or payload[key] is None:
                continue
            try:
                value = float(payload[key])
            except (TypeError, ValueError):
                rejected[key] = "not_a_number"
                continue
            if value < low or value > high:
                rejected[key] = f"out_of_range[{low},{high}]"
                continue
            applied[key] = value

        if "enabled" in payload and payload["enabled"] is not None:
            applied["enabled"] = bool(payload["enabled"])

        if not applied and not rejected:
            return {
                "ok": False,
                "cmd": "SET_QUARANTINE",
                "error": "nothing_to_set",
                "editable": sorted(_QUARANTINE_KNOBS) + ["enabled"],
            }

        ops = self.core.service_registry.get("ops_monitor_service")
        current = (
            dict(getattr(ops, "quarantine", None).policy.__dict__)
            if (getattr(ops, "quarantine", None) is not None)
            else {}
        )
        merged = {**current, **applied}

        # Validate by construction: an unusable policy must never reach the loop.
        try:
            from engine.strategy_quarantine import QuarantinePolicy

            QuarantinePolicy(
                enabled=bool(merged.get("enabled", True)),
                quarantine_below=float(merged.get("quarantine_below", 0.0)),
                restore_above=float(merged.get("restore_above", 0.05)),
                consecutive_failures=max(1, int(float(merged.get("consecutive_failures", 3)))),
                min_samples=max(1, int(float(merged.get("min_samples", 20)))),
            ).validated()
        except ValueError as exc:
            return {"ok": False, "cmd": "SET_QUARANTINE", "error": str(exc)}

        for key, value in applied.items():
            set_path(raw_cfg, f"trading.performance.quarantine.{key}", value)

        if ops is not None and getattr(ops, "quarantine", None) is not None:
            from engine.strategy_quarantine import QuarantinePolicy

            ops.quarantine.policy = QuarantinePolicy(
                enabled=bool(merged.get("enabled", True)),
                quarantine_below=float(merged.get("quarantine_below", 0.0)),
                restore_above=float(merged.get("restore_above", 0.05)),
                consecutive_failures=max(1, int(float(merged.get("consecutive_failures", 3)))),
                min_samples=max(1, int(float(merged.get("min_samples", 20)))),
            )

        if bool(payload.get("persist", True)):
            await self.core.config.save()
        return {
            "ok": True,
            "cmd": "SET_QUARANTINE",
            "applied": applied,
            "rejected": rejected,
            "note": (
                "thresholds apply to the next evaluation; strategies already "
                "quarantined are re-judged on the new policy"
            ),
        }

    async def _cmd_set_trading(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_cfg = getattr(self.core.config, "raw", None)
        if not isinstance(raw_cfg, dict):
            return {"ok": False, "cmd": "SET_TRADING", "error": "no_config"}

        persist = bool(payload.get("persist", True))
        needs_restart = False
        runtime = getattr(self.core, "trading", None)

        mode = payload.get("mode")
        if mode is not None:
            mode_s = str(mode).lower()
            current = str(
                ((raw_cfg.get("trading") or {}).get("mode") if isinstance(raw_cfg, dict) else "")
                or getattr(runtime, "mode", "paper")
                or "paper"
            ).lower()
            if mode_s in {"live", "quik"} and mode_s != current:
                # Refuse to arm live when the research gate leaves nothing cleared
                # to trade. Live mode arming used to succeed with an empty live
                # Consilium lineup: every preflight gate checks plumbing (operator,
                # market data, broker, reconciliation) and none of them asks
                # whether a single strategy is actually cleared for live. The
                # result was a silent no-op — live armed, zero orders, and nothing
                # anywhere saying why, which reads exactly like a broker fault.
                #
                # Checked *after* `confirm_live_required`: whether the request is
                # authorised is a question about the operator, which has to be
                # settled before it makes sense to discuss the lineup.
                safety_installed = "control_authorizer" in vars(self.core)
                preflight = vars(self.core).get("live_preflight")
                if not safety_installed and not bool(payload.get("confirm_live")):
                    return {
                        "ok": False,
                        "cmd": "SET_TRADING",
                        "error": "confirm_live_required",
                        "mode": mode_s,
                    }
                lineup_problem = await self._live_lineup_blocker(mode_s)
                if lineup_problem is not None:
                    return {
                        "ok": False,
                        "cmd": "SET_TRADING",
                        "error": "live_lineup_not_tradeable",
                        "mode": mode_s,
                        **lineup_problem,
                    }
                if safety_installed:
                    if preflight is None:
                        return {
                            "ok": False,
                            "cmd": "SET_TRADING",
                            "error": "live_preflight_unavailable",
                            "mode": mode_s,
                        }
                    try:
                        from engine.live_safety import (
                            LivePreflightInputs,
                            SafetyEvidence,
                        )

                        evidence = payload.get("preflight") or {}

                        def one(name: str):
                            value = evidence.get(name)
                            return SafetyEvidence(**value) if isinstance(value, dict) else None

                        report = preflight.evaluate(
                            LivePreflightInputs(
                                operator=one("operator"),
                                confirmations=tuple(
                                    SafetyEvidence(**item)
                                    for item in evidence.get("confirmations", ())
                                    if isinstance(item, dict)
                                ),
                                cost_schedule=one("cost_schedule"),
                                reconciliation=one("reconciliation"),
                                market_data=one("market_data"),
                                instrument_spec=one("instrument_spec"),
                                broker_capability=one("broker_capability"),
                                sandbox_acceptance=one("sandbox_acceptance"),
                            )
                        )
                        report.require_allowed()
                    except Exception as exc:  # noqa: BLE001
                        return {
                            "ok": False,
                            "cmd": "SET_TRADING",
                            "error": str(exc),
                            "mode": mode_s,
                        }
                    if runtime is not None and hasattr(runtime, "authorize_live"):
                        runtime.authorize_live(report)
            set_path(raw_cfg, "trading.mode", mode_s)
            if runtime is not None:
                runtime.mode = mode_s
                orders = getattr(runtime, "orders", None)
                if orders is not None and hasattr(orders, "mode"):
                    orders.mode = mode_s
            # switching away from paper typically needs market service restart
            if mode_s not in {"paper", "sim", "simulation"}:
                needs_restart = True

        if "equity" in payload and payload["equity"] is not None:
            eq = float(payload["equity"])
            set_path(raw_cfg, "trading.equity", eq)
            if runtime is not None:
                orders = getattr(runtime, "orders", None)
                if orders is not None and hasattr(orders, "equity"):
                    try:
                        if hasattr(orders, "set_starting_equity"):
                            orders.set_starting_equity(eq)
                        else:
                            orders.equity = eq
                    except ValueError as exc:
                        return {
                            "ok": False,
                            "cmd": "SET_TRADING",
                            "error": str(exc),
                        }

        risk = payload.get("risk")
        if isinstance(risk, dict):
            risk_cfg = dict((raw_cfg.get("trading") or {}).get("risk") or {})
            _OPT_NONE = {
                "max_portfolio_drawdown_abs",
                "max_strategy_drawdown_abs",
                "max_daily_loss_pct",
                "max_daily_turnover_pct",
                "max_orders_per_minute",
                "max_concentration_pct",
                "max_correlation",
                "max_order_adv_pct",
                "max_margin_utilization",
                "max_data_age_seconds",
            }
            patched: dict[str, Any] = {}
            for k, v in risk.items():
                if v is None:
                    if k in _OPT_NONE:
                        patched[k] = None
                    continue
                if k == "max_orders_per_minute":
                    patched[k] = int(v)
                else:
                    patched[k] = float(v)
                    # Absolute / advanced caps: 0 means disabled.
                    if k in _OPT_NONE and float(v) <= 0:
                        patched[k] = None
            _validate_risk_patch(risk, patched)
            deep_merge(risk_cfg, patched)
            for k, v in patched.items():
                if v is None:
                    risk_cfg[k] = None
            set_path(raw_cfg, "trading.risk", risk_cfg)
            if runtime is not None:
                rm = getattr(runtime, "risk", None)
                limits = getattr(rm, "limits", None) if rm else None
                if limits is not None:
                    for k, v in risk_cfg.items():
                        if hasattr(limits, k):
                            if v is None:
                                setattr(limits, k, None)
                            elif k == "max_orders_per_minute":
                                setattr(limits, k, int(v))
                            else:
                                setattr(limits, k, float(v))

        strategy_risk = payload.get("strategy_risk")
        if isinstance(strategy_risk, dict):
            # strategy_risk: {strategy_id: {max_position_pct: …}} or
            # {strategy_id: "x", clear: true} / patch fields at top with strategy_id.
            sid = str(strategy_risk.get("strategy_id") or "").strip()
            clear = bool(strategy_risk.get("clear"))
            patch = {
                k: v
                for k, v in strategy_risk.items()
                if k not in {"strategy_id", "clear"} and not isinstance(v, dict)
            }
            nested = {
                k: v
                for k, v in strategy_risk.items()
                if isinstance(v, dict) and k not in {"strategy_id", "clear"}
            }
            store = dict((raw_cfg.get("trading") or {}).get("strategy_risk") or {})
            if nested:
                for nsid, npatch in nested.items():
                    if not isinstance(npatch, dict):
                        continue
                    if not npatch:
                        store.pop(str(nsid), None)
                    else:
                        cur = dict(store.get(str(nsid)) or {})
                        cur.update(npatch)
                        store[str(nsid)] = cur
                    if runtime is not None:
                        rm = getattr(runtime, "risk", None)
                        if rm is not None and hasattr(rm, "set_strategy_overrides"):
                            rm.set_strategy_overrides(str(nsid), npatch if npatch else None)
            elif sid:
                if clear or not patch:
                    store.pop(sid, None)
                    if runtime is not None:
                        rm = getattr(runtime, "risk", None)
                        if rm is not None and hasattr(rm, "set_strategy_overrides"):
                            rm.set_strategy_overrides(sid, None)
                else:
                    cur = dict(store.get(sid) or {})
                    cur.update(patch)
                    store[sid] = cur
                    if runtime is not None:
                        rm = getattr(runtime, "risk", None)
                        if rm is not None and hasattr(rm, "set_strategy_overrides"):
                            rm.set_strategy_overrides(sid, cur)
            set_path(raw_cfg, "trading.strategy_risk", store)

        # market service toggles → config; try live CCXT reload when possible
        services = payload.get("services")
        reloaded_ccxt = False
        if isinstance(services, dict):
            for sid, block in services.items():
                if not isinstance(block, dict):
                    continue
                _guard_service_config_patch(str(sid), block)
                for k, v in block.items():
                    set_path(raw_cfg, f"services.{sid}.{k}", v)
                needs_restart = True
            ccxt_patch = services.get("ccxt_market_service")
            if isinstance(ccxt_patch, dict):
                svc = self.core.service_registry.get("ccxt_market_service")
                if svc is not None:
                    if isinstance(getattr(svc, "config", None), dict):
                        for k, v in ccxt_patch.items():
                            svc.config[k] = v
                    if hasattr(svc, "reload_credentials") and (
                        "exchange" in ccxt_patch
                        or "enabled" in ccxt_patch
                        or "api_key_env" in ccxt_patch
                    ):
                        try:
                            if ccxt_patch.get("enabled") is False:
                                if getattr(svc, "is_running", False):
                                    await svc.stop()
                                reloaded_ccxt = True
                                needs_restart = False
                            else:
                                await svc.reload_credentials()
                                reloaded_ccxt = True
                                needs_restart = False
                        except Exception:
                            _log().exception("SET_TRADING ccxt reload failed")
                            needs_restart = True

        if "log_level" in payload and payload["log_level"] is not None:
            set_path(raw_cfg, "core.log_level", str(payload["log_level"]))
            needs_restart = True

        if persist:
            await self.core.config.save()

        return {
            "ok": True,
            "cmd": "SET_TRADING",
            "needs_restart": needs_restart,
            "reloaded_ccxt": reloaded_ccxt,
        }

    async def _cmd_set_ensemble(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_cfg = getattr(self.core.config, "raw", None)
        if not isinstance(raw_cfg, dict):
            return {"ok": False, "cmd": "SET_ENSEMBLE", "error": "no_config"}

        persist = bool(payload.get("persist", True))
        ens = self.core.service_registry.get("ensemble_service")
        trading = dict(raw_cfg.get("trading") or {})
        ens_cfg = dict(trading.get("ensemble") or {})
        prev_live = {str(x) for x in (ens_cfg.get("strategies") or []) if x}
        prev_observe = {str(x) for x in (ens_cfg.get("observe") or []) if x}
        canary_applied: dict[str, Any] = {}

        if "strategies" in payload and payload["strategies"] is not None:
            new_live = [str(x) for x in list(payload["strategies"])]
            added = {str(x) for x in new_live if x} - prev_live
            if added and not bool(payload.get("confirm_live_without_holdout")):
                from engine.research_gate import (
                    live_promotion_blockers,
                    policy_from_config,
                )
                from engine.strategy_ids import resolve_strategy_id_via_core

                resolved = [resolve_strategy_id_via_core(self.core, sid) for sid in sorted(added)]
                policy = policy_from_config(raw_cfg)
                blockers = live_promotion_blockers(
                    resolved,
                    ticker=str(payload.get("research_ticker") or "").strip() or None,
                    policy=policy,
                    raw_cfg=raw_cfg,
                )
                if blockers:
                    return {
                        "ok": False,
                        "cmd": "SET_ENSEMBLE",
                        "error": "live_requires_readiness",
                        "blockers": blockers,
                        "hint": (
                            "Need live_ok from evaluate_live_readiness "
                            "(holdout, CI, trades, multi-ticker, params applied), "
                            "or pass confirm_live_without_holdout=true (not recommended)."
                        ),
                    }
                # Per-strategy readiness cannot see the set it is joining. With
                # two or more live members, Holm and RC/SPA/PBO have to be
                # applied to the whole resulting lineup, not just the addition.
                if policy.require_batch_verdict and len(new_live) >= 2:
                    from engine.research_gate import evaluate_many

                    decision = evaluate_many(new_live, policy=policy, raw_cfg=raw_cfg)
                    batch = decision.get("batch") or {}
                    if batch.get("verdict") not in (
                        "batch_edge_stable",
                        "not_applicable",
                    ):
                        blockers = [
                            {
                                "strategy_id": sid,
                                "reason": f"batch_{batch.get('verdict')}",
                                "detail": batch,
                            }
                            for sid in new_live
                        ]
                        return {
                            "ok": False,
                            "cmd": "SET_ENSEMBLE",
                            "error": "live_requires_readiness",
                            "blockers": blockers,
                            "hint": (
                                "The live lineup's ranking is not stable "
                                "(Holm / Reality Check / SPA / PBO). "
                                "Use confirm_live_without_holdout=true to override."
                            ),
                        }
            ens_cfg["strategies"] = new_live
            # Canary size for newly promoted live members
            if added and not bool(payload.get("skip_canary_quantity")):
                from engine.research_gate import (
                    apply_canary_quantity,
                    policy_from_config,
                )

                scale = float(policy_from_config(raw_cfg).canary_quantity_scale)
                canary_applied = apply_canary_quantity(raw_cfg, sorted(added), scale=scale)
        if "observe" in payload and payload["observe"] is not None:
            live = {str(x) for x in (ens_cfg.get("strategies") or [])}
            ens_cfg["observe"] = [
                str(x) for x in list(payload["observe"]) if str(x) and str(x) not in live
            ]
        elif "strategies" in payload:
            # Consilium-only apply: drop observe ids that moved into live.
            live = {str(x) for x in (ens_cfg.get("strategies") or [])}
            ens_cfg["observe"] = [
                str(x) for x in (ens_cfg.get("observe") or []) if str(x) not in live
            ]
        if "weight_mode" in payload and payload["weight_mode"] is not None:
            ens_cfg["weight_mode"] = str(payload["weight_mode"])
        if "enabled" in payload and payload["enabled"] is not None:
            ens_cfg["enabled"] = bool(payload["enabled"])
        if "weights" in payload and isinstance(payload["weights"], dict):
            weights = {str(k): float(v) for k, v in payload["weights"].items()}
            ens_cfg["weight_mode"] = "manual"
            ens_cfg["weights"] = weights

        if ens is not None:
            ens._ensemble_cfg = ens_cfg  # noqa: SLF001
            apply = getattr(ens, "apply_weight_mode", None)
            # Prefer real async apply_weight_mode; MagicMock auto-attrs must not
            # steal the manager.set_weights fallback used by unit tests.
            if callable(apply) and inspect.iscoroutinefunction(apply):
                await apply(publish=True)
            elif getattr(ens, "manager", None) is not None:
                ids = list(ens_cfg.get("strategies") or [])
                mode = str(ens_cfg.get("weight_mode") or "equal").lower()
                if mode == "manual" and isinstance(ens_cfg.get("weights"), dict):
                    ens.manager.set_weights(
                        {str(k): float(v) for k, v in ens_cfg["weights"].items()}
                    )
                elif ids:
                    ens.manager.set_equal_weights(ids)

        trading["ensemble"] = ens_cfg
        set_path(raw_cfg, "trading.ensemble", ens_cfg)

        # Roster apply may send off/observe/live lifecycle for strategy plugins.
        if "observe" in payload or "off" in payload or "strategies" in payload:
            new_live = {str(x) for x in (ens_cfg.get("strategies") or []) if x}
            new_obs = {str(x) for x in (ens_cfg.get("observe") or []) if x}
            explicit_off = {str(x) for x in list(payload.get("off") or []) if x}
            # Anyone dropped from the previous roster is stopped even if ``off`` omitted.
            implied_off = (prev_live | prev_observe) - new_live - new_obs
            await self._sync_strategy_modes(
                live=list(ens_cfg.get("strategies") or []),
                observe=list(ens_cfg.get("observe") or []),
                off=sorted(explicit_off | implied_off),
                raw_cfg=raw_cfg,
            )

        if persist:
            await self.core.config.save()

        strategies = list(ens_cfg.get("strategies") or [])
        observe = list(ens_cfg.get("observe") or [])
        weights = getattr(getattr(ens, "manager", None), "weights", {}) if ens else {}
        try:
            from host.events import publish_lineup_changed

            await publish_lineup_changed(
                self.core.event_bus,
                source_id=self.component_id,
                payload={
                    "strategies": strategies,
                    "observe": observe,
                    "weights": dict(weights or {}),
                    "weight_mode": str(ens_cfg.get("weight_mode") or "equal"),
                    "enabled": bool(ens_cfg.get("enabled", True)),
                    "persist": persist,
                },
                instance_id=str(await self.core.config.get("instance.id", "local") or "local"),
            )
        except Exception:
            pass

        return {
            "ok": True,
            "cmd": "SET_ENSEMBLE",
            "strategies": strategies,
            "observe": observe,
            "weights": weights,
            "canary_applied": canary_applied,
            "needs_restart": False,
        }

    async def _sync_strategy_modes(
        self,
        *,
        live: list[str],
        observe: list[str],
        off: list[str],
        raw_cfg: dict[str, Any],
    ) -> None:
        """Start observe/live strategies; stop those explicitly marked off."""
        pm = getattr(self.core, "plugin_manager", None)
        if pm is None:
            return
        try:
            from engine.strategy_plugin import StrategyPlugin
        except Exception:
            StrategyPlugin = None  # type: ignore[misc, assignment]

        mode_by_id: dict[str, str] = {}
        for sid in off:
            mode_by_id[str(sid)] = "off"
        for sid in observe:
            mode_by_id[str(sid)] = "observe"
        for sid in live:
            mode_by_id[str(sid)] = "live"

        for sid, mode in mode_by_id.items():
            inst = None
            if hasattr(pm, "get"):
                inst = pm.get(sid)
            if inst is None:
                inst = getattr(pm, "_instances", {}).get(sid)
            if StrategyPlugin is not None and inst is not None:
                if not isinstance(inst, StrategyPlugin):
                    continue
            elif inst is None and mode == "off":
                # Still persist plugin flag when instance missing.
                pass
            elif inst is None:
                continue

            block = dict((raw_cfg.get("plugins") or {}).get(sid) or {})
            if mode == "off":
                block["enabled"] = False
                block["observe_only"] = False
                if hasattr(pm, "disable_plugin"):
                    try:
                        pm.disable_plugin(sid)
                    except Exception:
                        pass
                if inst is not None and getattr(inst, "is_running", False):
                    try:
                        await inst.stop()
                    except Exception:
                        pass
            else:
                block["enabled"] = True
                block["observe_only"] = mode == "observe"
                if hasattr(pm, "enable_plugin"):
                    try:
                        pm.enable_plugin(sid)
                    except Exception:
                        pass
                if inst is not None:
                    try:
                        inst.config["observe_only"] = mode == "observe"
                    except Exception:
                        pass
                    if not getattr(inst, "is_running", False):
                        try:
                            await inst.start()
                        except Exception:
                            pass
            set_path(raw_cfg, f"plugins.{sid}", block)

    async def _cmd_set_plugin(self, payload: dict[str, Any]) -> dict[str, Any]:
        pid = str(payload.get("plugin_id") or payload.get("id") or "").strip()
        if not pid:
            return {"ok": False, "cmd": "SET_PLUGIN", "error": "missing_plugin_id"}

        pm = getattr(self.core, "plugin_manager", None)
        if pm is None:
            return {"ok": False, "cmd": "SET_PLUGIN", "error": "no_plugin_manager"}

        raw_cfg = getattr(self.core.config, "raw", None)
        persist = bool(payload.get("persist", True))
        enabled = payload.get("enabled")
        action = str(payload.get("action") or "").lower()  # start | stop
        needs_restart = False

        inst = None
        if hasattr(pm, "get"):
            inst = pm.get(pid)
        if inst is None:
            inst = getattr(pm, "_instances", {}).get(pid)

        if enabled is not None:
            en = bool(enabled)
            if en:
                if hasattr(pm, "enable_plugin"):
                    pm.enable_plugin(pid)
            else:
                if hasattr(pm, "disable_plugin"):
                    pm.disable_plugin(pid)
                if inst is not None and getattr(inst, "is_running", False):
                    await inst.stop()
            if isinstance(raw_cfg, dict):
                block = dict((raw_cfg.get("plugins") or {}).get(pid) or {})
                block["enabled"] = en
                set_path(raw_cfg, f"plugins.{pid}", block)
            if en and inst is None:
                needs_restart = True

        if action == "start" and inst is not None:
            if hasattr(pm, "enable_plugin"):
                pm.enable_plugin(pid)
            await inst.start()
        elif action == "stop" and inst is not None:
            await inst.stop()

        if persist and isinstance(raw_cfg, dict):
            await self.core.config.save()

        return {
            "ok": True,
            "cmd": "SET_PLUGIN",
            "plugin_id": pid,
            "running": bool(inst and getattr(inst, "is_running", False)),
            "needs_restart": needs_restart,
        }

    async def _cmd_set_strategy_params(self, payload: dict[str, Any]) -> dict[str, Any]:
        pid = str(payload.get("plugin_id") or payload.get("id") or "").strip()
        if not pid:
            return {"ok": False, "cmd": "SET_STRATEGY_PARAMS", "error": "missing_plugin_id"}

        raw_cfg = getattr(self.core.config, "raw", None)
        if not isinstance(raw_cfg, dict):
            return {"ok": False, "cmd": "SET_STRATEGY_PARAMS", "error": "no_config"}

        persist = bool(payload.get("persist", True))
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        # also accept flat fields
        for k in ("class_code", "tickers", "fast", "slow", "window", "z_entry", "quantity"):
            if k in payload and payload[k] is not None:
                params[k] = payload[k]

        block = dict((raw_cfg.get("plugins") or {}).get(pid) or {})
        deep_merge(block, params)
        set_path(raw_cfg, f"plugins.{pid}", block)

        # live-patch plugin.config when present
        pm = getattr(self.core, "plugin_manager", None)
        applied_live = False
        if pm is not None:
            inst = pm.get(pid) if hasattr(pm, "get") else None
            if inst is None:
                inst = getattr(pm, "_instances", {}).get(pid)
            cfg_obj = getattr(inst, "config", None) if inst else None
            if isinstance(cfg_obj, dict):
                deep_merge(cfg_obj, params)
                applied_live = True

        if persist:
            await self.core.config.save()

        return {
            "ok": True,
            "cmd": "SET_STRATEGY_PARAMS",
            "plugin_id": pid,
            "applied_live": applied_live,
            "needs_restart": not applied_live,
            "params": block,
        }

    @staticmethod
    def _caller_role_for(payload: dict[str, Any]) -> str:
        """Map the verified principal onto the AI tool role vocabulary.

        Only two outcomes are possible.  A caller holding ADMIN/RISK (a real
        operator, either signed or the explicit local-dev allow-list) may drive
        the full toolset.  Everyone else is an ``analyst``: read-only plus
        ``system_halt``.  That is the invariant documented in
        ``docs/architecture/operator_ui.md`` — the analyst does not place
        orders — and it is now enforced instead of assumed.
        """
        roles = {str(r).strip().lower() for r in payload.get("_authorized_roles") or ()}
        if {"admin", "risk"} & roles:
            return "operator"
        return "analyst"

    async def _cmd_ask(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text") or payload.get("message") or "").strip()
        if not text:
            return {"ok": False, "cmd": "ASK", "error": "empty_message"}
        use_tools = bool(payload.get("use_tools", True))
        provider_id = payload.get("provider_id")
        # The role is derived from the *verified* principal, never from the
        # request body.  Trusting `payload["caller_role"]` would let any caller
        # promote itself to "operator" and reach the order-placing tools.
        caller_role = self._caller_role_for(payload)
        history = payload.get("history")
        if not isinstance(history, list):
            history = None
        hist_len = len(history or [])
        _log().info(
            "ASK begin role={} provider={} tools={} hist={} text_len={} preview={!r}",
            caller_role,
            provider_id,
            use_tools,
            hist_len,
            len(text),
            text[:80],
        )
        pm = getattr(self.core, "plugin_manager", None)
        plugin = None
        if pm is not None:
            plugin = pm.get("ai_analyst") if hasattr(pm, "get") else None
            if plugin is None:
                plugin = getattr(pm, "_instances", {}).get("ai_analyst")
        if plugin is None or not hasattr(plugin, "ask"):
            _log().warning("ASK ai_analyst_not_loaded")
            return {"ok": False, "cmd": "ASK", "error": "ai_analyst_not_loaded"}
        try:
            result = await plugin.ask(
                text,
                use_tools=use_tools,
                provider_id=str(provider_id) if provider_id else None,
                caller_role=caller_role,
                history=history,
            )
        except Exception as exc:  # noqa: BLE001
            _log().exception("ASK plugin exception err={}", exc)
            detail = str(exc).strip() or f"{type(exc).__name__}"
            return {
                "ok": False,
                "cmd": "ASK",
                "error": detail,
                "answer": "",
                "tools": [],
                "tool_trace": [],
            }
        tools_used: list[str] = []
        trace = getattr(result, "tool_trace", None) or []
        for row in trace:
            if isinstance(row, dict) and row.get("name"):
                tools_used.append(str(row["name"]))
        ok = bool(getattr(result, "success", False))
        err = getattr(result, "error", None)
        answer = getattr(result, "content", "") or ""
        if not ok:
            detail = str(err or "").strip()
            if not detail or detail.lower() == "none":
                detail = "ask_failed_no_detail"
                # Prefer exception type / partial content over a blank ERROR line.
                if answer.strip():
                    detail = f"{detail}; partial_content_len={len(answer)}"
                err = detail
            _log().warning(
                "ASK failed detail={!r} provider={} model={} tools={} answer_len={}",
                err,
                getattr(result, "provider_id", ""),
                getattr(result, "model", ""),
                tools_used,
                len(answer),
            )
        _log().info(
            "ASK end ok={} role={} provider={} model={} hist={} tools={} latency={} err={}",
            ok,
            caller_role,
            getattr(result, "provider_id", ""),
            getattr(result, "model", ""),
            hist_len,
            tools_used,
            getattr(result, "latency", 0),
            err,
        )
        return {
            "ok": ok,
            "cmd": "ASK",
            "answer": answer,
            "provider_id": getattr(result, "provider_id", "") or "",
            "model": getattr(result, "model", "") or "",
            "error": err,
            "tools": tools_used,
            "tool_trace": list(trace)[:20],
            "latency": float(getattr(result, "latency", 0) or 0),
            "caller_role": caller_role,
        }

    async def _cmd_set_secrets(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist market API keys into SQLite ``operator_secrets`` + process env.

        Values are never echoed back. Empty / masked (``********``) fields are skipped.
        Allowed names are fixed allowlist only.
        """
        allowed = {
            "BITGET_API_KEY",
            "BITGET_SECRET",
            "BITGET_PASSWORD",
            "CCXT_API_KEY",
            "CCXT_SECRET",
            "CCXT_PASSWORD",
            "ALPACA_API_KEY",
            "ALPACA_SECRET_KEY",
        }
        raw_map = payload.get("secrets")
        if not isinstance(raw_map, dict):
            # Flat payload also accepted: { "BITGET_API_KEY": "..." }
            raw_map = {
                k: v for k, v in payload.items() if k in allowed or str(k).upper() in allowed
            }
        try:
            from host.secrets_env import upsert_secret
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "cmd": "SET_SECRETS", "error": f"secrets_env:{exc}"}

        updated: list[str] = []
        skipped: list[str] = []
        for name, value in raw_map.items():
            key = str(name or "").strip().upper()
            if key not in allowed:
                skipped.append(key or str(name))
                continue
            val = str(value or "").strip()
            if not val or val == "********":
                continue
            try:
                upsert_secret(key, val)
                updated.append(key)
            except Exception:
                _log().exception("persist secret failed env={}", key)
                return {
                    "ok": False,
                    "cmd": "SET_SECRETS",
                    "error": f"upsert_failed:{key}",
                    "updated": updated,
                }

        # Force presence flags to refresh on next snapshot / response.
        self._secrets_presence_ts = 0.0

        if not updated:
            return {
                "ok": True,
                "cmd": "SET_SECRETS",
                "updated": [],
                "skipped": skipped,
                "secrets": self._secrets_presence_snapshot(),
                "hint": "No new values (empty fields keep existing).",
            }

        reloaded = False
        reload_info: dict[str, Any] = {}
        ccxt_names = {
            "BITGET_API_KEY",
            "BITGET_SECRET",
            "BITGET_PASSWORD",
            "CCXT_API_KEY",
            "CCXT_SECRET",
            "CCXT_PASSWORD",
        }
        if any(k in ccxt_names for k in updated):
            svc = self.core.service_registry.get("ccxt_market_service")
            if svc is not None and hasattr(svc, "reload_credentials"):
                try:
                    reload_info = await svc.reload_credentials()
                    reloaded = bool(reload_info.get("reloaded"))
                except Exception as exc:  # noqa: BLE001
                    _log().exception("SET_SECRETS ccxt reload failed")
                    return {
                        "ok": False,
                        "cmd": "SET_SECRETS",
                        "error": f"reload_failed:{exc}",
                        "updated": updated,
                        "needs_restart": True,
                    }

        try:
            await self._broadcast(self.build_snapshot())
        except Exception:
            _log().exception("SET_SECRETS broadcast failed")

        return {
            "ok": True,
            "cmd": "SET_SECRETS",
            "updated": updated,
            "skipped": skipped,
            "secrets": self._secrets_presence_snapshot(),
            "reloaded": reloaded,
            "reload": reload_info,
            "needs_restart": not reloaded,
            "hint": (
                "Keys stored; CCXT adapter reloaded."
                if reloaded
                else (
                    "Keys stored in SQLite operator_secrets and process env. "
                    "Restart Engine if a market adapter was already running without keys."
                )
            ),
        }

    async def _cmd_set_ai(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_cfg = getattr(self.core.config, "raw", None)
        if not isinstance(raw_cfg, dict):
            return {"ok": False, "cmd": "SET_AI", "error": "no_config"}

        persist = bool(payload.get("persist", True))
        block = dict((raw_cfg.get("plugins") or {}).get("ai_analyst") or {})
        if "enabled" in payload and payload["enabled"] is not None:
            block["enabled"] = bool(payload["enabled"])
        if payload.get("default_provider"):
            block["default_provider"] = str(payload["default_provider"])
        if "max_tool_iterations" in payload and payload["max_tool_iterations"] is not None:
            block["max_tool_iterations"] = int(payload["max_tool_iterations"])
        profiles_patch = payload.get("profiles")
        if isinstance(profiles_patch, dict):
            import os

            normalized_profiles: dict[str, Any] = {}
            for provider_id, raw_profile in profiles_patch.items():
                if not isinstance(raw_profile, dict):
                    continue
                pid_key = str(provider_id).strip().lower()
                if not pid_key:
                    continue
                profile = dict(raw_profile)
                settings = dict(profile.get("settings") or {})
                provider_type = str(profile.get("provider_type") or pid_key).strip().lower()
                profile["provider_type"] = provider_type
                if not profile.get("display_name"):
                    profile["display_name"] = pid_key
                api_key = str(settings.pop("api_key", "") or "").strip()
                if api_key and api_key != "********":
                    env_name = str(
                        settings.get("api_key_env")
                        or {
                            "cursor": "CURSOR_API_KEY",
                            "openai": "OPENAI_API_KEY",
                            "openai_compatible": "OPENAI_API_KEY",
                            "anthropic": "ANTHROPIC_API_KEY",
                            "deepseek": "DEEPSEEK_API_KEY",
                            "grok": "XAI_API_KEY",
                            "openrouter": "OPENROUTER_API_KEY",
                            "gemini": "GEMINI_API_KEY",
                            "cerebras": "CEREBRAS_API_KEY",
                        }.get(provider_type)
                        or f"{provider_type.upper()}_API_KEY"
                    )
                    os.environ[env_name] = api_key
                    settings["api_key_env"] = env_name
                    try:
                        from host.secrets_env import has_secret, upsert_secret

                        upsert_secret(env_name, api_key)
                        if has_secret(env_name):
                            _log().info("persisted AI secret env={}", env_name)
                        else:
                            _log().warning("AI secret upsert returned empty env={}", env_name)
                    except Exception:
                        _log().exception("persist AI secret failed env={}", env_name)
                profile["settings"] = settings
                normalized_profiles[pid_key] = profile
            # Operator UI sends the full profile map — replace, don't deep-merge
            # into a stale blob (that hid newly added profiles in the chat picker).
            block["profiles"] = normalized_profiles
        delete_profiles = payload.get("delete_profiles")
        if isinstance(delete_profiles, (list, tuple)):
            profiles = dict(block.get("profiles") or {})
            for pid in delete_profiles:
                profiles.pop(str(pid).strip().lower(), None)
                profiles.pop(str(pid), None)
            block["profiles"] = profiles
            default = str(block.get("default_provider") or "")
            if default and default not in (block.get("profiles") or {}):
                remaining = list((block.get("profiles") or {}).keys())
                block["default_provider"] = remaining[0] if remaining else "mock"
        # Keep default_provider pointing at an existing profile id.
        default = str(block.get("default_provider") or "").strip().lower()
        if default and default not in (block.get("profiles") or {}):
            remaining = list((block.get("profiles") or {}).keys())
            block["default_provider"] = remaining[0] if remaining else "mock"
        elif default:
            block["default_provider"] = default
        set_path(raw_cfg, "plugins.ai_analyst", block)

        # Optional Alpaca keys from Keys window (process env + config hint)
        ak = str(payload.get("_alpaca_api_key") or "").strip()
        ask = str(payload.get("_alpaca_secret_key") or "").strip()
        if ak or ask:
            import os

            try:
                from host.secrets_env import upsert_secret
            except Exception:
                upsert_secret = None  # type: ignore[assignment]
            if ak:
                os.environ["ALPACA_API_KEY"] = ak
                if upsert_secret is not None:
                    try:
                        upsert_secret("ALPACA_API_KEY", ak)
                    except Exception:
                        _log().exception("persist alpaca api key failed")
            if ask:
                os.environ["ALPACA_SECRET_KEY"] = ask
                if upsert_secret is not None:
                    try:
                        upsert_secret("ALPACA_SECRET_KEY", ask)
                    except Exception:
                        _log().exception("persist alpaca secret failed")
            _log().info("alpaca keys set in process env + sqlite")

        pm = getattr(self.core, "plugin_manager", None)
        applied_live = False
        plugin = None
        if pm is not None:
            plugin = pm.get("ai_analyst") if hasattr(pm, "get") else None
            if plugin is None:
                plugin = getattr(pm, "_instances", {}).get("ai_analyst")
        if plugin is not None:
            live_block = resolve_env_secrets(block)
            if isinstance(getattr(plugin, "config", None), dict):
                deep_merge(plugin.config, live_block)
                # Force-replace profiles so the live plugin cannot keep a stale map.
                plugin.config["profiles"] = dict(live_block.get("profiles") or {})
                if "default_provider" in live_block:
                    plugin.config["default_provider"] = live_block["default_provider"]
            svc = getattr(plugin, "service", None)
            if svc is not None and hasattr(svc, "refresh_settings"):
                try:
                    try:
                        from plugins.ai_analyst.plugin import _resolve_secrets

                        refresh_cfg = _resolve_secrets(
                            dict(plugin.config if isinstance(plugin.config, dict) else live_block)
                        )
                    except Exception:
                        refresh_cfg = dict(
                            plugin.config if isinstance(plugin.config, dict) else live_block
                        )
                    svc.refresh_settings(refresh_cfg)
                    applied_live = True
                except Exception:
                    _log().exception("SET_AI refresh_settings failed")
                    applied_live = False

        if persist:
            await self.core.config.save()

        return {
            "ok": True,
            "cmd": "SET_AI",
            "applied_live": applied_live,
            "needs_restart": not applied_live,
            "ai": self._ai_snapshot(),
            "profiles": list((block.get("profiles") or {}).keys()),
        }

    async def _cmd_set_storage(self, payload: dict[str, Any]) -> dict[str, Any]:
        svc = self._storage_service()
        if svc is None:
            return {"ok": False, "cmd": "SET_STORAGE", "error": "storage_service_missing"}
        action = str(payload.get("action") or "reroute").strip().lower()
        persist = bool(payload.get("persist", False))
        try:
            if action in {"reroute", "routing", "apply_routing"}:
                by_class = payload.get("backend_by_class")
                if by_class is not None and not isinstance(by_class, dict):
                    return {
                        "ok": False,
                        "cmd": "SET_STORAGE",
                        "error": "backend_by_class_must_be_object",
                    }
                result = await svc.apply_reroute(
                    default_backend=payload.get("default_backend"),
                    backend_by_class=dict(by_class) if isinstance(by_class, dict) else None,
                    persist=persist,
                    flush=bool(payload.get("flush", True)),
                )
            elif action in {"backend", "set_backend", "configure_backend"}:
                bid = str(payload.get("backend_id") or "").strip()
                if not bid:
                    return {
                        "ok": False,
                        "cmd": "SET_STORAGE",
                        "error": "backend_id_required",
                    }
                result = await svc.apply_backend_config(
                    bid,
                    enabled=payload.get("enabled"),
                    path=payload.get("path"),
                    url=payload.get("url"),
                    host=payload.get("host"),
                    port=payload.get("port"),
                    persist=persist,
                    reload=bool(payload.get("reload", True)),
                )
            elif action in {"reload", "reload_backends"}:
                await svc.reload_backends()
                result = {"ok": True, "reloaded": True}
            else:
                return {
                    "ok": False,
                    "cmd": "SET_STORAGE",
                    "error": f"unknown_action:{action}",
                }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "cmd": "SET_STORAGE", "error": str(exc)}

        await self._refresh_storage_cache()
        out = {
            "ok": bool(result.get("ok", True)),
            "cmd": "SET_STORAGE",
            "action": action,
            "persisted": bool(result.get("persisted", persist)),
            "storage": dict(self._storage_cache),
        }
        if result.get("error"):
            out["error"] = result["error"]
            out["ok"] = False
        if result.get("routing"):
            out["routing"] = result["routing"]
        return out

    async def _publish_loop(self) -> None:
        interval = 1.0 / max(self._hz, 0.2)
        while self.is_running:
            try:
                self._storage_cache_tick += 1
                # Refresh storage health ~1 Hz (not every UI snapshot frame).
                if self._storage_cache_tick >= max(1, int(self._hz)):
                    self._storage_cache_tick = 0
                    await self._refresh_storage_cache()
                # Keep the Prometheus gauges current. Without this the exporter
                # had no data and every alert in ops/prometheus/alerts.yml was
                # unreachable.
                if not self._storage_cache_tick:
                    runtime = getattr(self.core, "trading", None)
                    if runtime is not None and hasattr(runtime, "publish_metrics"):
                        runtime.publish_metrics()
                snap = self.build_snapshot()
                await self._broadcast(snap)
                self._zmq_publish(snap)
            except Exception:
                _log().exception("ui_bridge publish_loop failed")
            await asyncio.sleep(interval)

    async def _broadcast(self, snap: dict[str, Any]) -> None:
        if not self._writers:
            return
        line = (json.dumps(snap, default=str) + "\n").encode("utf-8")
        dead: list[asyncio.StreamWriter] = []
        for w in list(self._writers):
            try:
                w.write(line)
                # `drain()` with no timeout blocks forever once a client's TCP
                # window fills — a sleeping laptop or a throttled browser tab is
                # enough. Because this runs in the publish loop, one stalled
                # client froze snapshots for every other client *and* stalled the
                # command path behind it. A client that cannot absorb a frame
                # within the budget is disconnected instead.
                await asyncio.wait_for(w.drain(), timeout=self._drain_timeout_sec)
            except (asyncio.TimeoutError, TimeoutError):
                dead.append(w)
            except Exception:
                dead.append(w)
        for w in dead:
            self._writers.discard(w)
            try:
                w.close()
            except Exception:
                pass

    async def _start_tcp(self) -> None:
        host = str(self.config.get("ws_host") or "127.0.0.1")
        port = int(self.config.get("ws_port") or 8765)

        async def client_cb(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            self._writers.add(writer)
            peer = "?"
            try:
                peer = str(writer.get_extra_info("peername") or "?")
            except Exception:
                pass
            try:
                writer.write(
                    (json.dumps(self.build_snapshot(), default=str) + "\n").encode("utf-8")
                )
                await writer.drain()
                while self.is_running:
                    raw = await reader.readline()
                    if not raw:
                        break
                    text = raw.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    try:
                        data = json.loads(text)
                    except json.JSONDecodeError:
                        data = {"cmd": text}
                    result = await self.handle_command(data)
                    ack = json.dumps({"schema": "ui.ack.v1", **result}, default=str) + "\n"
                    writer.write(ack.encode("utf-8"))
                    await writer.drain()
            except (
                ConnectionResetError,
                ConnectionAbortedError,
                BrokenPipeError,
                asyncio.IncompleteReadError,
            ) as exc:
                _log().info("ui client disconnected peer={} err={}", peer, exc)
            except OSError as exc:
                # WinError 64 / network name unavailable — client closed abruptly.
                _log().info("ui client socket closed peer={} err={}", peer, exc)
            except Exception:
                _log().exception("ui client handler crashed peer={}", peer)
            finally:
                self._writers.discard(writer)
                try:
                    writer.close()
                except Exception:
                    pass
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

        # Match Operator client: seeded multi-symbol OHLC snapshots are large.
        try:
            self._server = await asyncio.start_server(
                client_cb, host, port, limit=8 * 1024 * 1024, reuse_address=True
            )
        except OSError as exc:
            code = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
            # Windows may report an occupied reusable TCP port as either
            # WSAEADDRINUSE (10048) or WSAEACCES (10013).
            if code in {10013, 10048, 98, 48}:
                raise RuntimeError(
                    f"UI bridge port {host}:{port} is already in use or unavailable. "
                    "Stop the other Engine instance, or change "
                    "services.ui_bridge_service.ws_port in config/trading_bot.yaml"
                ) from exc
            raise

    def _start_zmq(self) -> None:
        try:
            import zmq
        except ImportError:
            return
        ctx = zmq.Context.instance()
        pub = ctx.socket(zmq.PUB)
        sub = ctx.socket(zmq.SUB)
        pub.setsockopt(zmq.LINGER, 0)
        sub.setsockopt(zmq.LINGER, 0)
        sub.setsockopt_string(zmq.SUBSCRIBE, "")
        pub_port = int(self.config.get("pub_port") or 5555)
        sub_port = int(self.config.get("sub_port") or 5556)
        try:
            pub.bind(f"tcp://127.0.0.1:{pub_port}")
            sub.bind(f"tcp://127.0.0.1:{sub_port}")
        except zmq.ZMQError:
            pub.close(0)
            sub.close(0)
            return
        self._zmq_pub = pub
        self._zmq_sub = sub

    def _zmq_publish(self, snap: dict[str, Any]) -> None:
        if self._zmq_pub is None:
            return
        try:
            import zmq

            self._zmq_pub.send_string(json.dumps(snap, default=str), zmq.NOBLOCK)
        except Exception:
            pass

    async def _zmq_command_loop(self) -> None:
        import zmq

        assert self._zmq_sub is not None
        poller = zmq.Poller()
        poller.register(self._zmq_sub, zmq.POLLIN)
        while self.is_running:
            events = dict(poller.poll(50))
            if self._zmq_sub in events:
                try:
                    raw = self._zmq_sub.recv_string(zmq.NOBLOCK)
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        data = raw
                    result = await self.handle_command(data)
                    # The loop is a one-way pipe: there is nowhere to return the
                    # ACK to. Discarding it made a *denied* command look exactly
                    # like a delivered one — a RESUME refused by the authorizer
                    # produced no error anywhere the caller could observe. Surface
                    # it keyed by `request_id` in the next snapshot instead.
                    self._record_command_ack(data, result)
                except zmq.Again:
                    pass
            await asyncio.sleep(0.05)

    def _record_command_ack(self, cmd: Any, result: dict[str, Any] | None) -> None:
        """Keep the outcome of a transport that cannot return an ACK."""
        payload = cmd if isinstance(cmd, dict) else {}
        request_id = payload.get("request_id")
        ok = bool((result or {}).get("ok"))
        if not ok:
            _log().warning(
                "zmq command failed cmd={} rid={} error={}",
                str(payload.get("cmd") or payload.get("action") or "")[:40],
                request_id,
                str((result or {}).get("error") or "")[:200],
            )
        if request_id is None:
            return
        self._command_acks[str(request_id)] = {
            "request_id": request_id,
            "ok": ok,
            "cmd": str(payload.get("cmd") or payload.get("action") or ""),
            "result": result or {},
            "at": time.time(),
        }
        if len(self._command_acks) > 64:
            # Evict in insertion order. Sorting the keys would evict the *newest*
            # entry for any id that sorts early ("r199" < "r9"), which is the one
            # a caller is about to ask for.
            for stale in list(self._command_acks)[: len(self._command_acks) - 64]:
                self._command_acks.pop(stale, None)

    def _close_zmq(self) -> None:
        for sock in (self._zmq_pub, self._zmq_sub):
            if sock is not None:
                try:
                    sock.close(0)
                except Exception:
                    pass
        self._zmq_pub = self._zmq_sub = None


def _slim_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    keys = (
        "ticker",
        "action",
        "success",
        "reason",
        "mode",
        "strategy_id",
        "quantity",
        "price",
        "close",
        "run_id",
        "via",
        "message",
        "error",
        "pnl",
        "strength",
        "status",
        "cost",
        "summary",
        "kind",
        "code",
    )
    out = {k: payload[k] for k in keys if k in payload}
    order = payload.get("order")
    if isinstance(order, dict):
        okeys = (
            "ticker",
            "action",
            "quantity",
            "price",
            "status",
            "reason",
            "strategy_id",
            "client_id",
            "cost",
            "mode",
        )
        slim_order = {k: order[k] for k in okeys if k in order}
        if slim_order:
            out["order"] = slim_order
            # Flatten common fields for UI toasts when top-level is empty.
            for k in ("ticker", "action", "quantity", "price", "status"):
                if k not in out and k in slim_order:
                    out[k] = slim_order[k]
    return out
