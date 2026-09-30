"""Consilium — aggregate strategy.signal via EnsembleManager."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from microkernel_core import BaseService, KernelEvent

from engine.ensemble import EnsembleManager, StrategySignal
from engine.order import OrderStatus
from engine.regime import RegimeDetector, apply_regime_bias
from engine.runtime import get_runtime
from host.events import (
    publish_ensemble_decision,
    publish_ensemble_reject,
    publish_ensemble_weights_changed,
    publish_execution_result,
    publish_market_regime,
    publish_risk_reject,
)

if TYPE_CHECKING:
    from microkernel_core import MicrokernelCore


def _coerce_signal_strength(raw: Any) -> float:
    """Parse signal strength: missing → 1.0; explicit 0 stays 0 (floor can reject)."""
    if raw is None or raw == "":
        return 1.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 1.0


class EnsembleService(BaseService):
    """
    Buffers ``strategy.signal``, aggregates with ``EnsembleManager``, publishes
    ``ensemble.decision``.

    Adaptive weight modes (risk_parity / bandit_ucb) rebalance from attributed
    fill outcomes. Execution goes through RuleEngine unless ``route_execution``.
    """

    def __init__(self, core: MicrokernelCore, component_id: str) -> None:
        super().__init__(core, component_id)
        self.manager = EnsembleManager()
        self._buffer: dict[str, list[StrategySignal]] = {}
        self._flush_task: asyncio.Task[None] | None = None
        self._rebalance_task: asyncio.Task[None] | None = None
        self._last_decision: dict[str, Any] | None = None
        self._decisions = 0
        self._fills_since_rebalance = 0
        self._ensemble_cfg: dict[str, Any] = {}
        self._last_signals: dict[str, dict[str, Any]] = {}
        self.regime = RegimeDetector()
        self._last_regime: str = "unknown"
        self._regime_symbol: str = ""
        self._rebalance_paused: bool = False
        self._risk_scale: float = 1.0
        self._last_diagnosis: dict[str, Any] | None = None

    def default_config(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "auto_start": True,
            "debounce_sec": 0.05,
            "route_execution": False,
            "rebalance_sec": 60.0,
            "rebalance_every_fills": 5,
            "ucb_c": 1.0,
            "corr_cap": 0.85,
            "regime_bias": True,
            "diagnosis_risk_scale": 0.5,
            "diagnosis_neg_weight_mult": 0.25,
        }

    async def on_initialize(self) -> None:
        trading = await self.core.config.get("trading", {}) or {}
        self._ensemble_cfg = dict(trading.get("ensemble") or {})
        ids = list(self._ensemble_cfg.get("strategies") or [])
        if ids:
            await self.apply_weight_mode(publish=False)
        setattr(self.core, "ensemble", self.manager)
        await self._register_rule_checks()

    async def _register_rule_checks(self) -> None:
        async def is_trade_decision(_params: dict[str, Any], payload: dict[str, Any]) -> bool:
            return (
                payload.get("event_type") == "ensemble.decision"
                and str(payload.get("action", "")).upper() in {"BUY", "SELL"}
            )

        re = getattr(self.core, "rule_engine", None)
        if re is None:
            return
        register = getattr(re, "register_check", None)
        if not callable(register):
            return
        result = register(self.component_id, "is_trade_decision", is_trade_decision)
        if asyncio.iscoroutine(result):
            await result

    def _enabled(self) -> bool:
        if self._ensemble_cfg.get("enabled") is False:
            return False
        return bool(self.config.get("enabled", True))

    def lineup_ids(self) -> list[str]:
        """Live Consilium members only. Empty list means observe-only (no live)."""
        return [str(x) for x in (self._ensemble_cfg.get("strategies") or []) if x]

    def weight_mode(self) -> str:
        return str(self._ensemble_cfg.get("weight_mode") or "equal").lower()

    async def apply_weight_mode(self, *, publish: bool = True) -> dict[str, float]:
        ids = self.lineup_ids()
        if not ids:
            self.manager.set_weights({})
            return {}
        mode = self.weight_mode()
        if self._rebalance_paused and mode in {"risk_parity", "bandit_ucb", "ucb", "bandit"}:
            return dict(self.manager.weights)
        manual = None
        if mode == "manual":
            raw = self._ensemble_cfg.get("weights")
            if isinstance(raw, dict):
                manual = {str(k): float(v) for k, v in raw.items()}
            elif self.manager.weights:
                manual = dict(self.manager.weights)
        weights = self.manager.rebalance(
            mode,
            ids,
            manual_weights=manual,
            ucb_c=float(self.config.get("ucb_c") or self._ensemble_cfg.get("ucb_c") or 1.0),
            corr_cap=float(
                self.config.get("corr_cap") or self._ensemble_cfg.get("corr_cap") or 0.85
            ),
            apply_corr=mode in {"risk_parity", "bandit_ucb"},
        )
        if bool(self.config.get("regime_bias", True)) and self._last_regime not in {
            "",
            "unknown",
        }:
            archetypes = self._archetypes_for(ids)
            biased = apply_regime_bias(weights, archetypes, self._last_regime)
            self.manager.set_weights(biased)
            weights = dict(self.manager.weights)
        self._fills_since_rebalance = 0
        if publish:
            await self._publish_weights(reason="rebalance")
        return weights

    async def apply_diagnosis(self, diagnosis: Any) -> dict[str, Any]:
        """
        Apply performance_monitor Diagnosis to Consilium risk / autoweight.

        - MARKET_WEAK → pause adaptive rebalance
        - PARAMETER_DRIFT / STRUCTURAL_BREAK → cut negative-mean members + risk_scale
        - HEALTHY → clear pause / restore risk_scale
        """
        from engine.performance_monitor import Diagnosis, DiagnosisKind

        if isinstance(diagnosis, Diagnosis):
            kind = diagnosis.kind
            actions = set(diagnosis.actions or ())
            body = diagnosis.to_dict()
        elif isinstance(diagnosis, dict):
            kind = DiagnosisKind(str(diagnosis.get("kind") or "healthy"))
            actions = {str(a) for a in (diagnosis.get("actions") or [])}
            body = dict(diagnosis)
        else:
            return {"ok": False, "error": "invalid_diagnosis"}

        self._last_diagnosis = body
        if kind == DiagnosisKind.HEALTHY:
            self._rebalance_paused = False
            self._risk_scale = 1.0
            return {
                "ok": True,
                "kind": kind.value,
                "rebalance_paused": False,
                "risk_scale": 1.0,
            }

        if (
            kind == DiagnosisKind.MARKET_WEAK
            or "pause_risk_parity_rebalance" in actions
        ):
            self._rebalance_paused = True

        changed = False
        if kind in {
            DiagnosisKind.PARAMETER_DRIFT,
            DiagnosisKind.STRUCTURAL_BREAK,
        } or "disable_strategy" in actions:
            self._risk_scale = float(
                self.config.get("diagnosis_risk_scale")
                or self._ensemble_cfg.get("diagnosis_risk_scale")
                or 0.5
            )
            mult = float(
                self.config.get("diagnosis_neg_weight_mult")
                or self._ensemble_cfg.get("diagnosis_neg_weight_mult")
                or 0.25
            )
            ids = self.lineup_ids()
            weights = dict(self.manager.weights) or {sid: 1.0 for sid in ids}
            for sid in ids:
                if float(self.manager.stats.mean_return(sid)) < 0.0:
                    weights[sid] = float(weights.get(sid, 0.0)) * mult
                    changed = True
            if changed:
                self.manager.set_weights(weights)
                await self._publish_weights(reason="diagnosis_autoweight")

        return {
            "ok": True,
            "kind": kind.value,
            "rebalance_paused": self._rebalance_paused,
            "risk_scale": self._risk_scale,
            "weights": dict(self.manager.weights),
            "actions": sorted(actions),
        }

    def _archetypes_for(self, ids: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        pm = getattr(self.core, "plugin_manager", None)
        instances = getattr(pm, "_instances", {}) if pm else {}
        for sid in ids:
            plug = instances.get(sid)
            arch = "trend"
            if plug is not None and hasattr(plug, "risk_profile"):
                try:
                    profile = plug.risk_profile()
                    arch = str(getattr(profile, "archetype", None) or "trend")
                except Exception:
                    arch = str((getattr(plug, "RISK_PROFILE", {}) or {}).get("archetype") or "trend")
            elif plug is not None:
                arch = str((getattr(plug, "RISK_PROFILE", {}) or {}).get("archetype") or "trend")
            out[sid] = arch.lower()
        return out

    async def _publish_weights(self, *, reason: str) -> None:
        instance_id = str(await self.core.config.get("instance.id", "local") or "local")
        await publish_ensemble_weights_changed(
            self.core.event_bus,
            source_id=self.component_id,
            payload={
                "weight_mode": self.weight_mode(),
                "strategies": self.lineup_ids(),
                "weights": self.manager.weights,
                "reason": reason,
                "stats": self.manager.stats.snapshot(self.lineup_ids()),
            },
            instance_id=instance_id,
        )

    async def start(self) -> None:
        if self.is_running:
            return
        if not self._enabled():
            self.is_running = False
            return
        # Respect explicit empty live lineup (observe-only Consilium).
        # Legacy auto-fill of *all* StrategyPlugin ids into live was unsafe:
        # YAML ``strategies: []`` after restart became ~20 live strategies.
        ids = [str(x) for x in (self._ensemble_cfg.get("strategies") or []) if x]
        self._ensemble_cfg["strategies"] = ids
        if not ids:
            self.manager.set_weights({})
        await self.apply_weight_mode(publish=True)
        self.is_running = True
        await self.core.event_bus.subscribe("strategy.signal", self._on_signal)
        await self.core.event_bus.subscribe("execution.result", self._on_execution)
        await self.core.event_bus.subscribe("market.candle", self._on_candle)
        sec = float(self.config.get("rebalance_sec") or 60.0)
        if sec > 0:
            self._rebalance_task = asyncio.create_task(
                self._rebalance_loop(sec), name="ensemble_rebalance"
            )

    async def stop(self) -> None:
        self.is_running = False
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        if self._rebalance_task and not self._rebalance_task.done():
            self._rebalance_task.cancel()
            try:
                await self._rebalance_task
            except asyncio.CancelledError:
                pass
            self._rebalance_task = None
        for et, handler in (
            ("strategy.signal", self._on_signal),
            ("execution.result", self._on_execution),
            ("market.candle", self._on_candle),
        ):
            try:
                await self.core.event_bus.unsubscribe(et, handler)
            except Exception:
                pass

    async def _on_candle(self, event: KernelEvent) -> None:
        if not self.is_running or not self._enabled():
            return
        runtime = get_runtime(self.core)
        if runtime is None:
            return
        p = event.payload or {}
        ticker = str(p.get("ticker") or "")
        class_code = str(p.get("class_code") or "")
        key = f"{class_code}:{ticker}" if class_code and ticker else ticker
        bars = []
        if key:
            bars = runtime.candles.bars(key) or []
        if not bars and ticker:
            bars = runtime.candles.bars(ticker) or []
        if len(bars) < 20:
            return
        closes = [float(b.close) for b in bars]
        highs = [float(b.high) for b in bars]
        lows = [float(b.low) for b in bars]
        snap = self.regime.observe(
            closes, highs=highs, lows=lows, symbol=ticker or key
        )
        if snap.regime == self._last_regime and (ticker or key) == self._regime_symbol:
            return
        prev = self._last_regime
        self._last_regime = snap.regime
        self._regime_symbol = ticker or key
        instance_id = str(await self.core.config.get("instance.id", "local") or "local")
        await publish_market_regime(
            self.core.event_bus,
            source_id=self.component_id,
            payload={
                **snap.to_dict(),
                "previous": prev,
            },
            instance_id=instance_id,
        )
        if bool(self.config.get("regime_bias", True)) and snap.regime != "unknown":
            await self.apply_weight_mode(publish=True)

    async def _rebalance_loop(self, interval: float) -> None:
        while self.is_running:
            try:
                await asyncio.sleep(interval)
                if not self.is_running:
                    return
                if self.weight_mode() in {"risk_parity", "bandit_ucb"}:
                    await self.apply_weight_mode(publish=True)
            except asyncio.CancelledError:
                return

    async def get_status(self) -> dict[str, Any]:
        status = await super().get_status()
        status.update(
            {
                "ensemble_enabled": self._enabled(),
                "weight_mode": self.weight_mode(),
                "weights": self.manager.weights,
                "decisions": self._decisions,
                "last_decision": self._last_decision,
                "buffered_symbols": list(self._buffer.keys()),
                "members": self.members_for_ui(),
                "stats": self.manager.stats.snapshot(self.lineup_ids()),
                "fills_since_rebalance": self._fills_since_rebalance,
                "regime": self._last_regime,
                "regime_symbol": self._regime_symbol,
                "rebalance_paused": self._rebalance_paused,
                "risk_scale": self._risk_scale,
                "last_diagnosis": self._last_diagnosis,
            }
        )
        return status

    def observe_ids(self) -> set[str]:
        return {str(x) for x in (self._ensemble_cfg.get("observe") or []) if x}

    def members_for_ui(self) -> list[dict[str, Any]]:
        live = [str(x) for x in (self._ensemble_cfg.get("strategies") or []) if x]
        observe = [str(x) for x in (self._ensemble_cfg.get("observe") or []) if x]
        ids = list(live)
        for sid in observe:
            if sid not in ids:
                ids.append(sid)
        if not ids:
            ids = list(self.manager.weights.keys()) or list(self._last_signals.keys())
        for sid in self._last_signals:
            if sid not in ids:
                ids.append(sid)

        decision = self._last_decision or {}
        decision_action = str(decision.get("action") or "").upper()
        weights = self.manager.weights
        observe_set = set(observe)
        live_set = set(live)
        stats = self.manager.stats.snapshot(ids).get("strategies") or {}
        out: list[dict[str, Any]] = []
        pm = getattr(self.core, "plugin_manager", None)
        instances = getattr(pm, "_instances", {}) if pm else {}

        for sid in ids:
            sig = dict(self._last_signals.get(sid) or {})
            plug = instances.get(sid) if instances else None
            view = {}
            if plug is not None and hasattr(plug, "operator_view"):
                try:
                    view = dict(plug.operator_view() or {})
                except Exception:
                    view = {}
            action = str(view.get("action") or sig.get("action") or "HOLD").upper()
            if action in {"FLAT", "", "NONE"}:
                action = "HOLD"
            metrics = view.get("metrics") if isinstance(view.get("metrics"), dict) else {}
            if not metrics and isinstance(sig.get("metrics"), dict):
                metrics = dict(sig["metrics"])
            ticker = str(
                view.get("ticker") or sig.get("ticker") or decision.get("ticker") or ""
            )
            strength = view.get("strength")
            if strength is None:
                strength = sig.get("strength", 0)
            observe_only = bool(
                sig.get("observe_only")
                or sid in observe_set
                or (
                    plug is not None
                    and hasattr(plug, "is_observe_only")
                    and plug.is_observe_only()
                )
            )
            if sid in live_set:
                mode = "live"
            elif observe_only or sid in observe_set:
                mode = "observe"
            else:
                mode = "off"
            if mode == "observe":
                stance = "observe"
            elif decision_action in {"BUY", "SELL"}:
                if action == "HOLD":
                    stance = "neutral"
                elif action == decision_action:
                    stance = "for"
                else:
                    stance = "against"
            else:
                stance = "neutral"
            out.append(
                {
                    "strategy_id": sid,
                    "action": action,
                    "ticker": ticker,
                    "strength": float(strength or 0),
                    "weight": float(weights.get(sid, 0) or 0),
                    "metrics": metrics,
                    "stance": stance,
                    "mode": mode,
                    "observe_only": bool(mode == "observe"),
                    "stats": stats.get(sid) or {},
                    "reason": view.get("reason") or sig.get("reason") or "",
                    "ts": view.get("ts") or sig.get("ts"),
                    "describe": (
                        list(plug.describe() or [])
                        if plug is not None and hasattr(plug, "describe")
                        else []
                    ),
                }
            )
        return out

    async def _on_execution(self, event: KernelEvent) -> None:
        if not self.is_running or not self._enabled():
            return
        p = event.payload or {}
        if not p.get("success"):
            return
        order = p.get("order") if isinstance(p.get("order"), dict) else {}
        qty = float(order.get("quantity") or p.get("quantity") or 0)
        price = float(order.get("price") or p.get("price") or 0)
        action = str(order.get("action") or p.get("action") or "").upper()
        if qty <= 0 or price <= 0:
            return
        direction = (
            1.0
            if action in {"BUY", "LONG"}
            else -1.0
            if action in {"SELL", "SHORT", "CLOSE"}
            else 0.0
        )
        if direction == 0.0:
            return
        gross = direction * abs(qty) * price * 1e-4
        contributors: list[str] = []
        if self._last_decision and isinstance(self._last_decision.get("contributors"), list):
            contributors = [
                str(c.get("strategy_id"))
                for c in self._last_decision["contributors"]
                if isinstance(c, dict) and c.get("strategy_id")
            ]
        if not contributors:
            sid = str(order.get("strategy_id") or "")
            if sid and sid != "consilium":
                contributors = [sid]
        if not contributors:
            return
        share = gross / len(contributors)
        for sid in contributors:
            self.manager.stats.record_outcome(sid, share)
        self._fills_since_rebalance += 1
        every = int(self.config.get("rebalance_every_fills") or 5)
        if (
            self.weight_mode() in {"risk_parity", "bandit_ucb"}
            and every > 0
            and self._fills_since_rebalance >= every
        ):
            await self.apply_weight_mode(publish=True)

    async def _on_signal(self, event: KernelEvent) -> None:
        if not self.is_running or not self._enabled():
            return
        p = event.payload or {}
        if str(p.get("strategy_id") or "") == "consilium":
            return
        sid = str(p.get("strategy_id") or event.source_id or "")
        side = str(p.get("action") or "flat").lower()
        if side in {"buy", "long"}:
            side = "buy"
        elif side in {"sell", "short", "close"}:
            side = "sell"
        else:
            side = "flat"
        observe_only = bool(p.get("observe_only")) or sid in self.observe_ids()
        action_ui = "BUY" if side == "buy" else "SELL" if side == "sell" else "HOLD"
        # Missing strength → 1.0 (manual/unsized). Explicit 0.0 must stay 0
        # so strength_floor can reject — never ``x or 1.0``.
        strength = _coerce_signal_strength(p.get("strength"))
        self._last_signals[sid] = {
            "strategy_id": sid,
            "action": action_ui,
            "ticker": str(p.get("ticker") or ""),
            "strength": strength,
            "metrics": dict(p.get("params_snapshot") or {}),
            "reason": str(p.get("reason") or ""),
            "ts": time.time(),
            "class_code": p.get("class_code"),
            "observe_only": observe_only,
            "mode": "observe" if observe_only else "live",
        }
        if observe_only:
            return
        sig = StrategySignal(
            strategy_id=sid,
            symbol=str(p.get("ticker") or ""),
            side=side,
            strength=strength,
            meta={
                "quantity": p.get("quantity"),
                "price": p.get("price"),
                "class_code": p.get("class_code"),
                "bar_ts": p.get("ts") or p.get("timestamp") or time.time(),
            },
        )
        if not sig.symbol:
            return
        self._buffer.setdefault(sig.symbol, []).append(sig)
        debounce = float(self.config.get("debounce_sec") or 0.05)
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        self._flush_task = asyncio.create_task(self._flush_later(debounce))

    async def _flush_later(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            await self._flush()
        except asyncio.CancelledError:
            return

    async def _flush(self) -> None:
        if not self._buffer:
            return
        pending = self._buffer
        self._buffer = {}
        all_signals: list[StrategySignal] = []
        for group in pending.values():
            by_sid: dict[str, StrategySignal] = {}
            for s in group:
                by_sid[s.strategy_id] = s
            all_signals.extend(by_sid.values())
        decisions = self.manager.aggregate(all_signals)
        instance_id = str(await self.core.config.get("instance.id", "local") or "local")
        lineup = set(self.manager.weights.keys())
        for sig in all_signals:
            if sig.strategy_id and sig.strategy_id not in lineup:
                await publish_ensemble_reject(
                    self.core.event_bus,
                    source_id=self.component_id,
                    payload={
                        "reason": "not_in_lineup",
                        "ticker": sig.symbol,
                        "strategy_id": sig.strategy_id,
                        "side": sig.side,
                        "strength": sig.strength,
                        "lineup": sorted(lineup),
                    },
                    instance_id=instance_id,
                )
            elif float(self.manager.weights.get(sig.strategy_id, 0.0) or 0.0) <= 0.0:
                await publish_ensemble_reject(
                    self.core.event_bus,
                    source_id=self.component_id,
                    payload={
                        "reason": "zero_weight",
                        "ticker": sig.symbol,
                        "strategy_id": sig.strategy_id,
                        "side": sig.side,
                        "strength": sig.strength,
                    },
                    instance_id=instance_id,
                )
        for dec in decisions:
            contributors = [
                {
                    "strategy_id": c.strategy_id,
                    "side": c.side,
                    "strength": c.strength,
                }
                for c in dec.contributors
            ]
            if dec.side == "flat":
                await publish_ensemble_reject(
                    self.core.event_bus,
                    source_id=self.component_id,
                    payload={
                        "reason": "flat_consensus",
                        "ticker": dec.symbol,
                        "strength": dec.strength,
                        "weights": dec.weights,
                        "contributors": contributors,
                    },
                    instance_id=instance_id,
                )
                continue
            qty = 1.0
            price = None
            class_code = ""
            for c in dec.contributors:
                if c.meta.get("quantity") is not None:
                    qty = float(c.meta["quantity"])
                if c.meta.get("price") not in (None, ""):
                    price = float(c.meta["price"])
                if c.meta.get("class_code"):
                    class_code = str(c.meta["class_code"])
            action = "BUY" if dec.side == "buy" else "SELL"
            strength = float(dec.strength) * max(0.0, min(1.0, float(self._risk_scale)))
            payload = {
                "action": action,
                "ticker": dec.symbol,
                "class_code": class_code,
                "quantity": qty,
                "price": price,
                "strength": strength,
                "risk_scale": self._risk_scale,
                "strategy_id": "consilium",
                "weights": dec.weights,
                "contributors": contributors,
            }
            await publish_ensemble_decision(
                self.core.event_bus,
                source_id=self.component_id,
                payload=payload,
                instance_id=instance_id,
            )
            self._last_decision = payload
            self._decisions += 1
            if self.config.get("route_execution", False):
                await self._execute(payload, instance_id=instance_id)

    async def _execute(self, payload: dict[str, Any], *, instance_id: str) -> None:
        runtime = get_runtime(self.core)
        if runtime is None:
            return
        if runtime.halted:
            await publish_risk_reject(
                self.core.event_bus,
                source_id=self.component_id,
                payload={
                    "reason": str(getattr(runtime, "_halt_reason", None) or "halted"),
                    "stage": "ensemble_direct",
                    "ticker": payload.get("ticker"),
                    "action": payload.get("action"),
                    "strategy_id": "consilium",
                },
                instance_id=instance_id,
            )
            return
        try:
            strength = (
                float(payload["strength"])
                if payload.get("strength") is not None
                else None
            )
        except (TypeError, ValueError):
            strength = None
        sizing = getattr(runtime, "sizing_config", None)
        if (
            sizing is not None
            and getattr(sizing, "rejects_strength", None) is not None
            and sizing.rejects_strength(strength)
        ):
            await publish_risk_reject(
                self.core.event_bus,
                source_id=self.component_id,
                payload={
                    "reason": (
                        f"strength_below_floor={float(strength or 0):.6g}"
                        f"<{float(sizing.strength_floor):.4g}"
                    ),
                    "stage": "ensemble_strength_floor",
                    "ticker": payload.get("ticker"),
                    "action": payload.get("action"),
                    "quantity": payload.get("quantity"),
                    "strength": strength,
                    "strategy_id": "consilium",
                },
                instance_id=instance_id,
            )
            return
        result = await runtime.submit_signal_async(
            ticker=str(payload.get("ticker") or ""),
            action=str(payload.get("action") or "BUY"),
            quantity=float(payload.get("quantity") or 1),
            strategy_id="consilium",
            class_code=str(payload.get("class_code") or ""),
            mark_price=(
                float(payload["price"])
                if payload.get("price") not in (None, "")
                else None
            ),
            strength=strength,
        )
        ok = result.status in {
            OrderStatus.FILLED,
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIAL,
        }
        from engine.fills import extract_result_cost

        cost = extract_result_cost(result, runtime)
        await publish_execution_result(
            self.core.event_bus,
            source_id=self.component_id,
            payload={
                "success": ok,
                "mode": runtime.mode,
                "via": "ensemble_direct",
                "order": {
                    "ticker": payload.get("ticker"),
                    "action": payload.get("action"),
                    "quantity": result.filled_qty,
                    "price": result.avg_price,
                    "status": result.status.value,
                    "reason": result.reason,
                    "strategy_id": "consilium",
                    "cost": cost,
                },
                "cost": cost,
                "contributors": list(payload.get("contributors") or []),
                "weights": dict(payload.get("weights") or {}),
                "error": None if ok else result.reason,
            },
            instance_id=instance_id,
        )
        # Full-book soft skips are expected when sizing hits max_leverage — do not
        # flood risk.reject (that was the leverage_breach spam on the timeline).
        soft_full = str(result.reason or "").startswith("no_leverage_headroom")
        if not ok and not soft_full:
            await publish_risk_reject(
                self.core.event_bus,
                source_id=self.component_id,
                payload={
                    "reason": str(result.reason or "rejected"),
                    "stage": "ensemble_direct",
                    "ticker": payload.get("ticker"),
                    "action": payload.get("action"),
                    "strategy_id": "consilium",
                    "status": result.status.value,
                },
                instance_id=instance_id,
            )
