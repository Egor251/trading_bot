"""Shared trading runtime: candles, risk, orders, kill switch."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .candle_store import CandleStore, StoredBar
from .costs import CostSchedule
from .cost_workflow import CostScheduleStore
from .execution import (
    BrokerOrder,
    ExecutionRouter,
    OrderSide as BrokerOrderSide,
    OrderState as BrokerOrderState,
)
from .instruments import InstrumentRegistry, InstrumentSpec
from .order import OrderIntent, OrderManager, OrderResult, OrderSide, OrderStatus
from .paths import data_dir, data_path
from .protection import ExitState, PositionSide, StrategyRiskProfile
from .protective_runtime import ProtectiveLayer
from .insurance import InsuranceGuard, limits_from_preset, resolve_preset
from .position_book import PositionBook
from .ops_safety import TradeJournal
from .performance_monitor import PerformanceMonitor, PerformanceTargets
from .phantom_timeline import PhantomTimeline
from .position_sizing import SizingConfig, SizingMode, atr_from_bars, size_quantity
from .risk import RiskLimits, RiskManager

if TYPE_CHECKING:
    from .market import LegacyBrokerExecutionPort
    from .reconciliation import ReconciliationService

_OPEN_BROKER_STATES = {
    "created",
    "submitting",
    "accepted",
    "partial",
    "cancel_pending",
    "unknown",
}


def _log():
    import logging

    return logging.getLogger("runtime")


def _best_insurance(
    side: PositionSide, rules: tuple[Any, ...]
) -> dict[str, Any] | None:
    priced = [
        rule
        for rule in rules
        if getattr(rule, "trigger_price", None) is not None
    ]
    if not priced:
        return None
    if side is PositionSide.LONG:
        chosen = max(priced, key=lambda rule: rule.trigger_price)
    else:
        chosen = min(priced, key=lambda rule: rule.trigger_price)
    return {
        "price": str(chosen.trigger_price),
        "kind": getattr(getattr(chosen, "kind", None), "value", chosen.kind),
        "reason": getattr(chosen, "reason", "") or "",
        "rule_id": getattr(chosen, "rule_id", ""),
    }


def _build_instrument_registry(cfg: dict[str, Any]) -> Any | None:
    existing = cfg.get("instrument_registry")
    if existing is not None:
        return existing
    raw = cfg.get("instruments") or []
    if not isinstance(raw, list) or not raw:
        return None
    registry = InstrumentRegistry()
    for item in raw:
        if not isinstance(item, dict):
            continue
        spec = InstrumentSpec.from_mapping(item)
        aliases: list[str] = []
        if spec.class_code:
            aliases.append(f"{spec.class_code}:{spec.symbol}")
        if registry.get(spec.symbol) is None:
            aliases.append(spec.symbol)
        registry.register(spec, aliases=aliases)
    return registry


def _resolve_cost_schedule(cfg: dict[str, Any]) -> CostSchedule | None:
    schedule = cfg.get("cost_schedule")
    if isinstance(schedule, CostSchedule):
        return schedule
    if isinstance(schedule, dict):
        return CostSchedule.from_dict(schedule)
    store_path = cfg.get("cost_store_path")
    if store_path:
        store = CostScheduleStore(Path(str(store_path)))
        active = store.load_active_schedule()
        if active is not None:
            return active
    return None


def _resolve_data_path(configured: Any, *default_parts: str) -> str:
    """Resolve a configured data path against the shared data root.

    A relative ``data/...`` value used to be interpreted against the process CWD.
    Anything under ``data/`` is now re-anchored on :func:`engine.paths.data_dir`,
    which honours ``TRADING_DATA_DIR``. Absolute paths are left alone.
    """
    raw = str(configured or "").strip()
    if not raw:
        return str(data_path(*default_parts))
    candidate = Path(raw)
    if candidate.is_absolute():
        return raw
    parts = candidate.parts
    if parts and parts[0] == "data":
        return str(data_dir().joinpath(*parts[1:]))
    return raw


def _publish_runtime_cost_schedule(
    store_path: str | Path | None, schedule: CostSchedule | None
) -> None:
    if not store_path or schedule is None:
        return
    try:
        CostScheduleStore(Path(str(store_path))).write_runtime_effective(schedule)
    except Exception:
        pass


@dataclass
class TradingRuntime:
    """
    Process-local trading state used by services/plugins.

    Attached to MicrokernelCore as ``core.trading`` by trading_runtime_service.
    """

    risk: RiskManager
    orders: OrderManager
    candles: CandleStore
    mode: str = "paper"
    broker: LegacyBrokerExecutionPort | None = None
    execution_router: ExecutionRouter | None = None
    reconciliation: ReconciliationService | None = None
    protection: ProtectiveLayer = field(default_factory=ProtectiveLayer)
    venue_by_class: dict[str, str] = field(default_factory=dict)
    account_by_venue: dict[str, str] = field(default_factory=dict)
    strategy_profiles: dict[str, StrategyRiskProfile] = field(default_factory=dict)
    default_risk_archetype: str = "trend"
    cost_store_path: str = ""
    live_authorized: bool = False
    live_preflight_report: Any | None = None
    pinned_keys: set[str] = field(default_factory=set)
    forbid_net_flip: bool = True
    insurance: InsuranceGuard | None = None
    performance: PerformanceMonitor | None = None
    trade_journal: TradeJournal | None = None
    phantom_timeline: PhantomTimeline | None = None
    sizing_config: SizingConfig | None = None
    exit_pending_timeout_sec: float = 30.0
    #: Durable snapshot of the book + protective layer. Injected by
    #: `from_config`; created lazily otherwise.
    _state_store: Any | None = None
    _halt_reason: str = ""

    @classmethod
    def from_config(cls, trading_cfg: dict[str, Any] | None = None) -> TradingRuntime:
        cfg = trading_cfg or {}
        risk_raw = dict(cfg.get("risk") or {})
        insurance_raw = dict(cfg.get("insurance") or {})
        preset_name = str(
            insurance_raw.get("preset") or risk_raw.pop("preset", None) or "moderate"
        )
        try:
            limits = limits_from_preset(preset_name, overrides=risk_raw)
        except ValueError:
            limits = RiskLimits(
                max_portfolio_drawdown=float(risk_raw.get("max_portfolio_drawdown", 0.10)),
                max_strategy_drawdown=float(risk_raw.get("max_strategy_drawdown", 0.05)),
                max_position_pct=float(risk_raw.get("max_position_pct", 0.20)),
                max_leverage=float(risk_raw.get("max_leverage", 1.0)),
                max_portfolio_drawdown_abs=risk_raw.get("max_portfolio_drawdown_abs"),
                max_strategy_drawdown_abs=risk_raw.get("max_strategy_drawdown_abs"),
                max_daily_loss_pct=risk_raw.get("max_daily_loss_pct"),
            )
        risk = RiskManager(limits)
        strategy_risk_raw = cfg.get("strategy_risk")
        if isinstance(strategy_risk_raw, dict):
            for sid, patch in strategy_risk_raw.items():
                if isinstance(patch, dict):
                    try:
                        risk.set_strategy_overrides(str(sid), patch)
                    except Exception:
                        pass
        equity = float(cfg.get("equity", 10_000.0))
        mode = str(cfg.get("mode", "paper"))
        maxlen = int((cfg.get("candle_store") or {}).get("maxlen", 720))
        schedule = _resolve_cost_schedule(cfg)
        if schedule is not None and not isinstance(schedule, CostSchedule):
            raise TypeError("trading.cost_schedule must be a CostSchedule or mapping")
        # A bare "data/..." string here resolved against the process CWD, so a
        # differently-started engine published its cost schedule somewhere other
        # than the rest of its state. Resolve relative paths against the shared
        # data root instead (TRADING_DATA_DIR aware).
        cost_store_path = _resolve_data_path(cfg.get("cost_store_path"), "cost_schedules")
        orders = OrderManager(
            risk,
            mode=mode,
            equity=equity,
            cost_schedule=schedule,
            instrument_registry=_build_instrument_registry(cfg),
            account=str(cfg.get("account") or "default"),
            base_currency=str(cfg.get("base_currency") or "USD"),
            legacy_fee_bps=float(cfg.get("legacy_fee_bps") or 0),
            legacy_slippage_bps=float(cfg.get("legacy_slippage_bps") or 0),
        )
        # OrderManager may materialize a legacy-bps schedule when store has no active.
        _publish_runtime_cost_schedule(cost_store_path, orders.cost_schedule)
        protection_state = cfg.get("protection_state")
        protection = (
            ProtectiveLayer.recover(protection_state)
            if isinstance(protection_state, dict)
            else ProtectiveLayer()
        )
        # When the config carries no protection state, fall back to the durable
        # snapshot. Previously `protection_state` was only ever *read* — nothing
        # ever wrote it — so this branch never fired and every restart started
        # with a naked book.
        runtime_state_store = None
        if not isinstance(protection_state, dict):
            from .state_store import StateStore

            runtime_state_store = StateStore(
                _resolve_data_path(cfg.get("state_path"), "state", "runtime.json")
            )
        profiles: dict[str, StrategyRiskProfile] = {}
        for strategy_id, raw_profile in (cfg.get("strategy_profiles") or {}).items():
            if isinstance(raw_profile, StrategyRiskProfile):
                profiles[str(strategy_id)] = raw_profile
            elif isinstance(raw_profile, dict):
                profiles[str(strategy_id)] = StrategyRiskProfile.from_dict(raw_profile)
            elif isinstance(raw_profile, str):
                profiles[str(strategy_id)] = StrategyRiskProfile.for_archetype(
                    raw_profile
                )
        position_policy = dict(cfg.get("position_policy") or {})
        forbid_flip = bool(position_policy.get("forbid_net_flip", True))
        perf_raw = dict(cfg.get("performance") or {})
        performance = PerformanceMonitor(
            PerformanceTargets(
                daily_target=float(perf_raw.get("daily_target", 200.0)),
                daily_minimum=float(perf_raw.get("daily_minimum", 100.0)),
                underperform_days=int(perf_raw.get("underperform_days", 3)),
                target_sharpe=float(perf_raw.get("target_sharpe", 1.5)),
                target_win_rate=float(perf_raw.get("target_win_rate", 0.55)),
                target_profit_factor=float(perf_raw.get("target_profit_factor", 1.8)),
                lookback_days=int(perf_raw.get("lookback_days", 7)),
            )
        )
        journal_path = str((cfg.get("trade_journal") or {}).get("path") or "")
        insurance = InsuranceGuard(
            risk,
            daily_loss_pct=float(
                insurance_raw.get("daily_loss_pct")
                or resolve_preset(preset_name).daily_loss_pct
            ),
            day_start_equity=equity,
            peak_equity=equity,
        )
        sizing_raw = cfg.get("sizing")
        sizing_cfg = (
            SizingConfig.from_dict(sizing_raw)
            if isinstance(sizing_raw, dict)
            else None
        )
        return cls(
            risk=risk,
            orders=orders,
            candles=CandleStore(maxlen=maxlen),
            mode=mode,
            protection=protection,
            _state_store=runtime_state_store,
            execution_router=cfg.get("execution_router"),
            reconciliation=cfg.get("reconciliation"),
            venue_by_class={
                str(k).upper(): str(v)
                for k, v in (cfg.get("venue_by_class") or {}).items()
            },
            account_by_venue={
                str(k): str(v)
                for k, v in (cfg.get("account_by_venue") or {}).items()
            },
            strategy_profiles=profiles,
            default_risk_archetype=str(
                cfg.get("default_risk_archetype") or "trend"
            ),
            cost_store_path=cost_store_path,
            forbid_net_flip=forbid_flip,
            insurance=insurance,
            performance=performance,
            trade_journal=TradeJournal(journal_path or None),
            sizing_config=sizing_cfg,
            exit_pending_timeout_sec=float(
                cfg.get("exit_pending_timeout_sec") or 30.0
            ),
        )

    def recover_from_disk(self) -> dict[str, Any] | None:
        """Rehydrate the protective layer from the durable snapshot.

        Called once after construction. Returns the recovered snapshot so the
        caller can decide whether to block new risk until reconciliation has run.
        """
        if self._state_store is None:
            return None
        snapshot = self.restored_book()
        if not isinstance(snapshot, dict):
            return None
        protection = snapshot.get("protection")
        if not isinstance(protection, dict):
            return None
        try:
            self.protection = ProtectiveLayer.recover(protection)
        except Exception:
            _log().exception("failed to recover protective state; starting flat")
            return None
        positions = snapshot.get("positions") or {}
        _log().critical(
            "recovered %d protective slice(s) and %d position(s) from %s. "
            "Broker positions are authoritative — reconcile before opening new "
            "risk.",
            len((protection.get("ledger") or {}).get("slices") or []),
            len(positions),
            self.state_path,
        )
        return snapshot

    @property
    def halted(self) -> bool:
        return self.risk.halted

    def halt(self, reason: str = "kill_switch") -> None:
        self.risk.emergency_halt(reason)
        self._halt_reason = reason

    def resume(self) -> None:
        self.risk.resume()
        self._halt_reason = ""

    def reset_sim_book(
        self,
        *,
        symbol: str | None = None,
        strategy_id: str | None = None,
    ) -> dict[str, Any]:
        """Clear paper/sim positions and protection. Forbidden in live/quik.

        Without filters — full book wipe. With ``symbol`` and/or ``strategy_id`` —
        scoped clear of matching open exposure.
        """
        mode = (self.mode or "paper").lower()
        if mode not in {"paper", "sim", "simulation", "phantom", "replay"}:
            raise PermissionError(f"reset_sim_book forbidden in mode={mode}")
        equity = float(self.orders.starting_equity or self.orders.equity or 10_000.0)
        sym = str(symbol or "").strip() or None
        sid = str(strategy_id or "").strip() or None
        if not sym and not sid:
            self.orders.clear_book(equity=equity)
            self.protection = ProtectiveLayer()
            self.pinned_keys.clear()
            if self.insurance is not None:
                self.insurance.reset_day(equity)
            return {"scope": "all", "mode": mode, "equity": equity}

        cleared_symbols: list[str] = []
        removed_slices: list[str] = []
        # Collect symbols from open positions / protection slices.
        try:
            for row in self.open_positions() if hasattr(self, "open_positions") else []:
                if not isinstance(row, dict):
                    continue
                row_sid = str(row.get("strategy_id") or "")
                row_sym = str(row.get("symbol") or "")
                if sid and row_sid and row_sid != sid:
                    continue
                if sid and not row_sid:
                    continue
                if sym and sym not in {row_sym, row_sym.split(":")[-1]} and not row_sym.endswith(
                    f":{sym}"
                ):
                    continue
                if row_sym:
                    cleared_symbols.append(row_sym)
                slice_id = str(row.get("slice_id") or "")
                if slice_id:
                    removed_slices.append(slice_id)
        except Exception:
            pass
        ledger = getattr(self.protection, "ledger", None)
        if ledger is not None and hasattr(ledger, "all"):
            try:
                for slice_ in list(ledger.all()):
                    if getattr(slice_, "state", None) is ExitState.CLOSED:
                        continue
                    if sid and str(getattr(slice_, "strategy_id", "") or "") != sid:
                        continue
                    slice_sym = str(getattr(slice_, "symbol", "") or "")
                    if sym and sym not in {
                        slice_sym,
                        slice_sym.split(":")[-1],
                    } and not slice_sym.endswith(f":{sym}"):
                        continue
                    slice_id = str(getattr(slice_, "slice_id", "") or "")
                    if slice_id and ledger.remove(slice_id):
                        removed_slices.append(slice_id)
                        stops = getattr(self.protection, "stops", None)
                        if stops is not None and hasattr(stops, "remove_slice"):
                            stops.remove_slice(slice_id)
                    if slice_sym:
                        cleared_symbols.append(slice_sym)
            except Exception:
                pass

        if sym:
            cleared_symbols.append(sym)
        seen: set[str] = set()
        uniq: list[str] = []
        for s in cleared_symbols:
            if s not in seen:
                seen.add(s)
                uniq.append(s)
        details = [self.orders.clear_symbol(s) for s in uniq]
        self.pinned_keys = {k for k in self.pinned_keys if k not in seen}
        return {
            "scope": "partial",
            "mode": mode,
            "symbol": sym,
            "strategy_id": sid,
            "cleared_symbols": uniq,
            "removed_slices": sorted(set(removed_slices)),
            "details": details,
            "equity": float(self.orders.equity),
        }

    def authorize_live(self, report: Any) -> None:
        """Accept only a complete report produced by the live preflight engine."""
        if report is None or not hasattr(report, "require_allowed"):
            raise PermissionError("valid live preflight report required")
        report.require_allowed()
        self.live_preflight_report = report
        self.live_authorized = True

    def register_strategy_profile(
        self, strategy_id: str, profile: StrategyRiskProfile
    ) -> None:
        self.strategy_profiles[str(strategy_id)] = profile

    def set_cost_schedule(self, schedule: CostSchedule | None) -> None:
        self.orders.cost_schedule = schedule

    def reload_active_cost_schedule(self) -> CostSchedule | None:
        if not self.cost_store_path:
            return self.orders.cost_schedule
        store = CostScheduleStore(Path(self.cost_store_path))
        active = store.load_active_schedule()
        if active is not None:
            self.set_cost_schedule(active)
        _publish_runtime_cost_schedule(self.cost_store_path, self.orders.cost_schedule)
        return active or self.orders.cost_schedule

    def profile_for(self, strategy_id: str) -> StrategyRiskProfile:
        key = str(strategy_id or "")
        if key in self.strategy_profiles:
            return self.strategy_profiles[key]
        return StrategyRiskProfile.for_archetype(self.default_risk_archetype)

    @property
    def position_book(self) -> PositionBook:
        return PositionBook(self.protection.ledger, forbid_net_flip=self.forbid_net_flip)

    def exposure_view(self, strategy_id: str, symbol: str) -> dict[str, Any]:
        return self.position_book.view(strategy_id, symbol).to_dict()

    def _gate_consilium_intent(
        self, intent: OrderIntent, *, close_and_reverse: bool = False
    ) -> OrderResult | None:
        """Insurance + net-exposure policy before submit (todo2 §1 / §3)."""
        if self.insurance is not None:
            # Keep insurance peak aligned with the order book peak so DD is not
            # computed against a stale day-start-only high-water mark.
            self.insurance.peak_equity = max(
                float(self.insurance.peak_equity or 0),
                float(self.orders.peak_equity or 0),
            )
            # reduce_only exits must stay available during leverage_breach / DD halt.
            decision = self.insurance.check(
                float(self.orders.equity),
                daily_equity=float(self.orders.equity_ex_unrealized),
                gross_exposure=(
                    None
                    if intent.reduce_only
                    else float(self.orders.gross_exposure)
                ),
            )
            if not decision.allowed and not intent.reduce_only:
                self._halt_reason = decision.reason
                return OrderResult(
                    client_id=intent.client_id,
                    status=OrderStatus.REJECTED,
                    reason=decision.reason or "insurance_halt",
                )
        meta = getattr(intent, "meta", None) or {}
        allow_reverse = bool(close_and_reverse or meta.get("close_and_reverse"))
        verdict = self.position_book.evaluate_intent(
            strategy_id=intent.strategy_id or "",
            symbol=intent.exposure_key,
            side=intent.side.value,
            quantity=intent.quantity,
            close_and_reverse=allow_reverse,
            reduce_only=bool(intent.reduce_only),
        )
        if not verdict.allowed:
            return OrderResult(
                client_id=intent.client_id,
                status=OrderStatus.REJECTED,
                reason=verdict.reason or "position_policy",
            )
        if verdict.adjusted_qty is not None and float(verdict.adjusted_qty) + 1e-12 < float(
            intent.quantity
        ):
            intent.quantity = float(verdict.adjusted_qty)
        return None

    def open_position_keys(self) -> set[str]:
        """Symbols/keys with non-zero exposure — pin in universe."""
        keys: set[str] = set()
        for sym, notional in self.orders.exposures.items():
            if abs(notional) > 1e-9:
                keys.add(sym)
        keys |= set(self.pinned_keys)
        return keys

    def open_positions(self) -> list[dict[str, Any]]:
        """Operator view: open slices with optional target and insurance stop."""
        rows: list[dict[str, Any]] = []
        covered: set[str] = set()
        for position in self.protection.ledger.all():
            if position.state is ExitState.CLOSED:
                continue
            remaining = float(position.remaining_quantity or 0)
            if remaining <= 1e-12:
                continue
            book = self.orders.positions.get(position.symbol)
            mark = float(book.mark_price) if book and book.mark_price > 0 else float(
                position.entry_price
            )
            entry = float(position.entry_price)
            mult = float(book.contract_multiplier) if book else 1.0
            signed = remaining if position.side is PositionSide.LONG else -remaining
            pnl = (mark - entry) * signed * mult
            insurance = _best_insurance(
                position.side, self.protection.stops.for_slice(position.slice_id)
            )
            target = (
                {
                    "price": str(position.target_price),
                    "kind": "take_profit",
                }
                if position.target_price is not None
                else None
            )
            rows.append(
                {
                    "slice_id": position.slice_id,
                    "symbol": position.symbol,
                    "side": position.side.value,
                    "quantity": str(position.remaining_quantity),
                    "entry": str(position.entry_price),
                    "mark": str(mark),
                    "pnl": str(round(pnl, 8)),
                    "strategy_id": position.strategy_id,
                    "state": position.state.value,
                    "target": target,
                    "insurance": insurance,
                }
            )
            covered.add(position.symbol)

        for key, book in self.orders.positions.items():
            if abs(book.quantity) <= 1e-12 or key in covered:
                continue
            side = "long" if book.quantity > 0 else "short"
            qty = abs(book.quantity)
            pnl = float(book.unrealized_pnl)
            rows.append(
                {
                    "slice_id": "",
                    "symbol": key,
                    "side": side,
                    "quantity": str(qty),
                    "entry": str(book.avg_price),
                    "mark": str(book.mark_price),
                    "pnl": str(round(pnl, 8)),
                    "strategy_id": "",
                    "state": "open",
                    "target": None,
                    "insurance": None,
                }
            )
        rows.sort(key=lambda row: (row["symbol"], row.get("slice_id") or ""))
        return rows

    def open_orders(self) -> list[dict[str, Any]]:
        """Working orders for operator UI (durable store + pending protective exits)."""
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        store = getattr(self.execution_router, "store", None)
        if store is not None:
            for order in store.orders():
                state = getattr(order.state, "value", order.state)
                if str(state).lower() not in _OPEN_BROKER_STATES:
                    continue
                client_id = str(order.client_id)
                seen.add(client_id)
                class_code = str((order.raw or {}).get("class_code") or "")
                symbol = (
                    f"{class_code}:{order.symbol}" if class_code else str(order.symbol)
                )
                price = order.limit_price or order.stop_price or order.average_fill_price
                rows.append(
                    {
                        "client_id": client_id,
                        "broker_order_id": order.broker_order_id,
                        "symbol": symbol,
                        "side": getattr(order.side, "value", order.side),
                        "quantity": str(order.quantity),
                        "filled": str(order.filled_quantity),
                        "remaining": str(order.remaining_quantity),
                        "price": str(price) if price is not None else None,
                        "limit_price": (
                            str(order.limit_price)
                            if order.limit_price is not None
                            else None
                        ),
                        "stop_price": (
                            str(order.stop_price)
                            if order.stop_price is not None
                            else None
                        ),
                        "order_type": order.order_type,
                        "state": str(state),
                        "venue": order.venue,
                        "account": order.account,
                        "strategy_id": str((order.raw or {}).get("strategy_id") or ""),
                        "reduce_only": bool((order.raw or {}).get("reduce_only")),
                        "exit_reason": str((order.raw or {}).get("exit_reason") or ""),
                        "updated_at": order.updated_at.isoformat(),
                        "kind": "broker",
                    }
                )
        for position in self.protection.ledger.all():
            if position.state is not ExitState.EXIT_PENDING or not position.exit_order_id:
                continue
            if position.exit_order_id in seen:
                continue
            rows.append(
                {
                    "client_id": position.exit_order_id,
                    "broker_order_id": None,
                    "symbol": position.symbol,
                    "side": "sell" if position.side is PositionSide.LONG else "buy",
                    "quantity": str(position.remaining_quantity),
                    "filled": str(position.exited_quantity),
                    "remaining": str(position.remaining_quantity),
                    "price": None,
                    "limit_price": None,
                    "stop_price": None,
                    "order_type": "market",
                    "state": "exit_pending",
                    "venue": self.mode,
                    "account": self.orders.account,
                    "strategy_id": position.strategy_id,
                    "reduce_only": True,
                    "exit_reason": position.exit_reason or "protective_exit",
                    "updated_at": position.opened_at.isoformat(),
                    "kind": "protective",
                }
            )
        rows.sort(key=lambda row: (row["symbol"], row.get("client_id") or ""))
        return rows

    def recent_trades(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Recent fills in a chart/UI-friendly shape."""
        ledger = self.orders.fill_ledger
        rows: list[dict[str, Any]] = []
        for fill in list(ledger)[-max(1, int(limit)) :]:
            data = fill.to_dict() if hasattr(fill, "to_dict") else dict(fill)
            rows.append(
                {
                    "id": data.get("fill_id") or "",
                    "order_id": data.get("order_id") or "",
                    "symbol": data.get("symbol") or "",
                    "side": data.get("side") or "",
                    "quantity": data.get("quantity"),
                    "price": data.get("price"),
                    "ts": data.get("trade_time"),
                    "venue": data.get("venue") or "",
                    "account": data.get("account") or "",
                    "cost": (
                        (data.get("costs") or {}).get("total")
                        if isinstance(data.get("costs"), dict)
                        else str(getattr(fill, "total_cost", "0"))
                    ),
                    "cash_delta": data.get("cash_delta"),
                }
            )
        return rows

    def _build_intent(
        self,
        *,
        ticker: str,
        action: str,
        quantity: float,
        strategy_id: str,
        class_code: str,
        mark_price: float,
        client_id: str | None = None,
        reduce_only: bool = False,
        exit_reason: str = "",
        venue: str = "",
        account: str = "",
        order_type: str = "market",
        limit_price: float | None = None,
    ) -> tuple[OrderIntent | None, OrderResult | None, float]:
        """Return (intent, reject, price)."""
        price = mark_price
        key = f"{class_code}:{ticker}" if class_code else ticker
        if price is None or price <= 0:
            price = self.candles.mark_price(key) or self.candles.mark_price(ticker)
        if (price is None or price <= 0) and limit_price is not None and float(limit_price) > 0:
            price = float(limit_price)
        if price is None or price <= 0:
            return (
                None,
                OrderResult(
                    client_id="",
                    status=OrderStatus.REJECTED,
                    reason="no_mark_price",
                ),
                0.0,
            )

        action_u = action.upper()
        if action_u in {"BUY", "LONG"}:
            side = OrderSide.BUY
        elif action_u in {"SELL", "SHORT", "CLOSE"}:
            side = OrderSide.SELL
        else:
            return (
                None,
                OrderResult(
                    client_id="",
                    status=OrderStatus.REJECTED,
                    reason=f"unsupported_action:{action}",
                ),
                0.0,
            )

        resolved_venue = venue or self.venue_by_class.get(
            (class_code or "").upper(),
            "ccxt"
            if (class_code or "").upper() in {"CRYPTO", "SPOT"}
            else "alpaca"
            if (class_code or "").upper() == "US"
            else "quik",
        )
        resolved_account = account or self.account_by_venue.get(
            resolved_venue, "default"
        )
        ot = str(order_type or "market").lower()
        lp = float(limit_price) if limit_price is not None and float(limit_price) > 0 else float(price)
        intent = OrderIntent(
            symbol=ticker,
            side=side,
            quantity=float(quantity),
            strategy_id=strategy_id,
            class_code=class_code or "",
            limit_price=lp,
            order_type=ot,
            reduce_only=reduce_only,
            exit_reason=exit_reason,
            venue=resolved_venue,
            account=resolved_account,
            **({"client_id": client_id} if client_id else {}),
        )
        return intent, None, float(price)

    def _has_pending_exit_conflict(self, intent: OrderIntent) -> bool:
        closing_side = (
            PositionSide.LONG if intent.side == OrderSide.SELL else PositionSide.SHORT
        )
        for position in self.protection.ledger.all():
            if (
                position.symbol == intent.exposure_key
                and position.side is closing_side
                and position.state is ExitState.EXIT_PENDING
                and position.exit_order_id != intent.client_id
            ):
                return True
        return False

    def _protection_bars(self, symbol: str) -> list[dict[str, float]]:
        stored = self.candles.bars(symbol)
        if not stored and ":" in symbol:
            stored = self.candles.bars(symbol.split(":", 1)[-1])
        return [
            {
                "high": float(bar.high or bar.close),
                "low": float(bar.low or bar.close),
                "close": float(bar.close),
            }
            for bar in stored
            if bar.close > 0
        ]

    def _atr_value(self, symbol: str, period: int = 14) -> float | None:
        bars = self._protection_bars(symbol)
        if len(bars) < period + 1:
            return None
        trs: list[float] = []
        for index in range(1, len(bars)):
            high = bars[index]["high"]
            low = bars[index]["low"]
            prev_close = bars[index - 1]["close"]
            trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        atr = sum(trs[:period]) / period
        for value in trs[period:]:
            atr = ((atr * (period - 1)) + value) / period
        return atr

    def _protect_opened_slice(
        self, *, slice_id: str, strategy_id: str, symbol: str
    ) -> None:
        profile = self.profile_for(strategy_id)
        self.protection.protect_or_max_loss(
            slice_id, profile, self._protection_bars(symbol)
        )

    def venue_for(self, symbol: str) -> str:
        """Resolve the venue for an exposure key (``CLASS:SYMBOL``) or symbol."""
        text = str(symbol or "")
        class_code = text.split(":")[0] if ":" in text else ""
        return self.venue_by_class.get(
            class_code.upper(),
            "ccxt"
            if class_code.upper() in {"CRYPTO", "SPOT"}
            else "alpaca"
            if class_code.upper() == "US"
            else "quik",
        )

    def account_for(self, symbol: str) -> str:
        return self.account_by_venue.get(self.venue_for(symbol), "default")

    def native_stop_plan(self, slice_id: str) -> dict[str, Any] | None:
        """The stop to mirror at the broker for *slice_id*, or ``None``.

        Returns ``{side, trigger_price, quantity, venue, account}`` for the
        tightest stop that has a trigger price. The in-memory rules are
        evaluated only by ``process_bar*`` inside this process, so a crash, a
        restart or a wedged engine leaves the position naked. Every one of the 63
        stored Quik orders has ``stop_price=null`` and ``filled_quantity=0``
        precisely because nothing ever placed one.
        """
        rules = [
            rule
            for rule in self.protection.stops.for_slice(slice_id)
            if rule.trigger_price is not None
        ]
        position = self.protection.ledger.get(slice_id)
        if not rules or position is None:
            return None
        side = position.side
        tightest = min(
            rules,
            key=lambda r: (
                Decimal(str(r.trigger_price))
                if side is PositionSide.LONG
                else -Decimal(str(r.trigger_price))
            ),
        )
        return {
            "slice_id": slice_id,
            "symbol": str(getattr(position, "symbol", "") or ""),
            "side": (
                BrokerOrderSide.SELL if side is PositionSide.LONG else BrokerOrderSide.BUY
            ),
            "trigger_price": Decimal(str(tightest.trigger_price)),
            "quantity": Decimal(str(position.remaining_quantity)),
            "venue": self.venue_for(str(getattr(position, "symbol", "") or "")),
            "account": self.account_for(str(getattr(position, "symbol", "") or "")),
        }

    async def place_native_stop(self, slice_id: str) -> bool:
        """Mirror a protective stop at the broker. Returns True when placed.

        Fire-and-forget safe: any failure is logged and the in-memory rule stays
        in place, so this degrades to the previous client-side-only behaviour
        rather than leaving the slice unprotected.
        """
        plan = self.native_stop_plan(slice_id)
        if plan is None or self.execution_router is None:
            return False
        try:
            capabilities = self.execution_router.capabilities(
                str(plan["venue"]), str(plan["account"])
            )
        except KeyError:
            return False
        if not getattr(capabilities, "native_stop", False):
            return False
        client_id = f"stop:{slice_id}"
        order = BrokerOrder(
            symbol=str(plan["symbol"] or slice_id.split(":")[-1]),
            side=plan["side"],
            quantity=plan["quantity"],
            venue=str(plan["venue"]),
            account=str(plan["account"]),
            client_id=client_id,
            idempotency_key=client_id,
            order_type="stop",
            stop_price=plan["trigger_price"],
            oco_group_id=f"oco:{slice_id}",
            raw={"reduce_only": True, "slice_id": slice_id, "native_stop": True},
        )
        try:
            await self.execution_router.place(order)
        except Exception:
            _log().exception("native stop placement failed for %s", slice_id)
            return False
        _log().info(
            "placed native stop %s @ %s for %s units of %s",
            client_id,
            plan["trigger_price"],
            plan["quantity"],
            slice_id,
        )
        return True

    # -- durable protection state ------------------------------------------
    #
    # The protective layer used to live only in memory: `ProtectiveLayer.snapshot()`
    # was never written anywhere, and `OrderManager.positions` / `fill_ledger` /
    # `peak_equity` were process-local too. A restart therefore left a real broker
    # position with *zero* protective orders and an empty local ledger, so the
    # restarted bot computed gross_exposure == 0, believed it was flat, and opened
    # fresh risk on top of the live exposure. All 63 stored Quik orders have
    # `stop_price=null` and `filled_quantity=0` for exactly this reason.

    @property
    def state_path(self) -> Path:
        if self._state_store is not None:
            return self._state_store.path
        from .paths import data_path

        return data_path("state", "runtime.json")

    def _state_store_or_create(self) -> Any:
        if self._state_store is None:
            from .state_store import StateStore

            self._state_store = StateStore(self.state_path)
        return self._state_store

    def persist_protection_state(self) -> bool:
        """Atomically publish the book + protective layer. Returns success.

        Called after every fill, exit fill and stop change. Failure is logged and
        swallowed: losing durability must not abort a trade that already reached
        the broker, but it is surfaced so the gap is visible.
        """
        store = self._state_store_or_create()
        try:
            orders_payload: dict[str, Any] = {}
            for key, position in self.orders.positions.items():
                if abs(float(position.quantity)) <= 1e-12:
                    continue
                orders_payload[key] = {
                    "quantity": float(position.quantity),
                    "avg_price": float(position.avg_price),
                    "mark_price": float(position.mark_price),
                    "realized_pnl": float(position.realized_pnl),
                    "contract_multiplier": float(position.contract_multiplier),
                    "currency": str(position.currency),
                }
            store.save(
                {
                    "mode": self.mode,
                    "protection": self.protection.snapshot(),
                    "positions": orders_payload,
                    "equity": float(self.orders.equity),
                    "peak_equity": float(self.orders.peak_equity),
                    "starting_equity": float(self.orders.starting_equity),
                    "pinned_keys": sorted(self.pinned_keys),
                    "halted": self.halted,
                    "halt_reason": self._halt_reason,
                },
                label="runtime",
            )
            return True
        except Exception:
            _log().exception("failed to persist runtime/protection state")
            return False

    def restored_book(self) -> dict[str, Any] | None:
        """Read the last durable snapshot, or ``None`` if there is none."""
        store = self._state_store_or_create()
        try:
            return store.load()
        except Exception:
            return None

    def _recover_protection(self, configured: Any) -> None:
        """Restore protective slices recovered from disk.

        Only *protective* state is restored. Rehydrating the position book from a
        local snapshot is a different (and unsafe) operation — the authoritative
        positions live at the broker, and the durable order store plus
        reconciliation are the right inputs for that. What is restored here is the
        set of stops the engine believed were live, so that a restarted process
        does not silently claim a naked position is protected.
        """
        source = configured if isinstance(configured, dict) else None
        if source is None:
            snapshot = self.restored_book()
            if isinstance(snapshot, dict) and isinstance(
                snapshot.get("protection"), dict
            ):
                source = snapshot["protection"]
                recovered_positions = snapshot.get("positions") or {}
                if recovered_positions:
                    _log().critical(
                        "recovered %d position(s) and %d protective slice(s) from "
                        "%s. The broker is the authority: reconcile before "
                        "opening new risk.",
                        len(recovered_positions),
                        len((source.get("ledger") or {}).get("slices") or []),
                        self.state_path,
                    )
        if not isinstance(source, dict):
            return
        try:
            self.protection = ProtectiveLayer.recover(source)
        except Exception:
            _log().exception("failed to recover protective state; starting flat")
            self.protection = ProtectiveLayer()

    def _attribute_fill(self, intent: OrderIntent, result: OrderResult) -> None:
        if result.status not in {OrderStatus.FILLED, OrderStatus.PARTIAL}:
            return
        remaining = Decimal(
            str((result.raw or {}).get("applied_fill_qty", result.filled_qty or 0))
        )
        if remaining <= 0:
            return
        price = Decimal(
            str(
                (result.raw or {}).get(
                    "applied_fill_price", result.avg_price or intent.limit_price or 0
                )
            )
        )
        closing_side = (
            PositionSide.LONG if intent.side == OrderSide.SELL else PositionSide.SHORT
        )
        candidates = [
            position
            for position in self.protection.ledger.all()
            if position.symbol == intent.exposure_key
            and position.side is closing_side
            and position.state is not ExitState.CLOSED
        ]
        for position in candidates:
            if remaining <= 0:
                break
            closed = min(remaining, position.remaining_quantity)
            if position.state is ExitState.OPEN:
                self.protection.request_strategy_exit(
                    position.slice_id,
                    order_id=intent.client_id,
                    reason=intent.exit_reason or "strategy_exit",
                )
            if position.exit_order_id != intent.client_id:
                continue
            self.protection.apply_exit_fill(
                position.slice_id,
                order_id=intent.client_id,
                quantity=closed,
                price=price,
            )
            remaining -= closed
        if remaining > 0 and not intent.reduce_only:
            slice_id = f"{intent.client_id}:{len(self.protection.ledger.all())}"
            self.protection.open_slice(
                slice_id=slice_id,
                strategy_id=intent.strategy_id or "unknown",
                symbol=intent.exposure_key,
                side=intent.side.value,
                quantity=remaining,
                entry_price=price,
                opened_at=result.ts,
            )
            self._protect_opened_slice(
                slice_id=slice_id,
                strategy_id=intent.strategy_id or "unknown",
                symbol=intent.exposure_key,
            )
        # Every book change is a point where a crash would leave the durable
        # state describing a different world than the broker.
        self.persist_protection_state()

    def _resolve_atr(self, *, ticker: str, class_code: str) -> float | None:
        cfg = self.sizing_config
        if cfg is None:
            return None
        key = f"{class_code}:{ticker}" if class_code else ticker
        bars = self.candles.bars(key) or self.candles.bars(ticker) or []
        if len(bars) < 2:
            return None
        return atr_from_bars(
            [b.high for b in bars],
            [b.low for b in bars],
            [b.close for b in bars],
            period=int(cfg.atr_period),
        )

    def _contract_multiplier(self, *, ticker: str, class_code: str) -> float:
        intent = OrderIntent(
            symbol=ticker,
            side=OrderSide.BUY,
            quantity=1.0,
            class_code=class_code or "",
        )
        instrument = self.orders._instrument_for(intent)  # noqa: SLF001
        if instrument is not None:
            try:
                return max(float(getattr(instrument, "contract_multiplier", 1) or 1), 1e-12)
            except (TypeError, ValueError):
                pass
        key = intent.exposure_key
        pos = self.orders.positions.get(key)
        if pos is not None:
            return max(float(pos.contract_multiplier or 1.0), 1e-12)
        return 1.0

    def _sync_open_marks(self, *, prefer: dict[str, float] | None = None) -> None:
        """Refresh marks for every open position before leverage/sizing math.

        Risk uses ``gross_exposure`` from position marks; if only the traded
        symbol is updated, other legs stay stale and new risk can overshoot
        ``max_leverage`` until a later bar marks them up.
        """
        prefer = prefer or {}
        for key, pos in list(self.orders.positions.items()):
            if abs(float(pos.quantity or 0.0)) <= 1e-12:
                continue
            px = prefer.get(key)
            if px is None or float(px) <= 0:
                px = self.candles.mark_price(key)
            if (px is None or float(px) <= 0) and ":" in key:
                px = self.candles.mark_price(key.split(":", 1)[1])
            if px is not None and float(px) > 0:
                self.orders.mark_to_market(key, float(px))

    def _order_increases_symbol_risk(
        self,
        *,
        ticker: str,
        class_code: str,
        action: str,
        quantity: float,
    ) -> bool:
        """True when the order grows |symbol exposure| (new risk), not a reduce."""
        key = f"{class_code}:{ticker}" if class_code else ticker
        pos = self.orders.positions.get(key)
        current = float(pos.quantity) if pos is not None else 0.0
        side = str(action or "").strip().lower()
        signed = float(quantity) if side in {"buy", "long", "b"} else -float(quantity)
        proposed = current + signed
        return abs(proposed) > abs(current) + 1e-9

    def _book_equity(self) -> float:
        """Mark equity for sizing/headroom. Do not use ``eq or starting`` — equity==0
        must mean no headroom (blown book), not a silent fall-back to start capital.
        """
        try:
            eq = float(self.orders.equity)
        except (TypeError, ValueError):
            eq = float("nan")
        if eq == eq and eq > 0:  # finite and positive
            return eq
        try:
            start = float(self.orders.starting_equity or 0)
        except (TypeError, ValueError):
            start = 0.0
        # Only fall back when equity is missing/NaN, not when it is a real zero.
        if eq != eq:  # NaN
            return max(0.0, start)
        return 0.0

    def _clamp_qty_to_leverage_headroom(
        self,
        qty: float,
        *,
        price: float,
        contract_multiplier: float = 1.0,
        reduce_only: bool = False,
        increases_risk: bool = True,
    ) -> float:
        """Cap qty so adding this notional cannot push gross past max_leverage."""
        if reduce_only or not increases_risk or qty <= 0:
            return float(qty)
        max_lev = float(self.risk.limits.max_leverage or 0.0)
        equity = self._book_equity()
        unit = float(price or 0) * max(float(contract_multiplier or 1.0), 1e-12)
        if max_lev <= 0 or equity <= 0 or unit <= 0:
            return float(qty) if equity > 0 else 0.0
        headroom = max(0.0, equity * max_lev - float(self.orders.gross_exposure))
        if headroom <= 1e-9:
            # Full book: size to zero so callers soft-skip instead of slamming
            # the risk gate with leverage_breach rejects every tick.
            return 0.0
        capped = float(min(qty, headroom / unit))
        # Tiny leftover headroom must not open dust slices that accumulate /
        # churn the fill ledger (1e-13 BTC etc.).
        min_qty = float(getattr(self.sizing_config, "min_qty", 1e-8) or 1e-8)
        if min_qty > 0 and capped < min_qty:
            return 0.0
        return capped

    def _apply_sizing(
        self,
        *,
        ticker: str,
        class_code: str,
        quantity: float,
        mark_price: float,
        reduce_only: bool,
        strength: float | None,
        apply_sizing: bool,
        action: str = "",
    ) -> float:
        """Resize entry/increase orders when ``sizing_config`` is active."""
        if reduce_only:
            return float(quantity)
        price = float(mark_price or 0)
        key = f"{class_code}:{ticker}" if class_code else ticker
        if price <= 0:
            price = self.candles.mark_price(key) or self.candles.mark_price(ticker) or 0
        mult = self._contract_multiplier(ticker=ticker, class_code=class_code)
        increases = self._order_increases_symbol_risk(
            ticker=ticker,
            class_code=class_code,
            action=action,
            quantity=float(quantity),
        )
        if not apply_sizing or self.sizing_config is None:
            # Still enforce leverage headroom on pre-sized paths (phantom/operator).
            return self._clamp_qty_to_leverage_headroom(
                float(quantity),
                price=float(price or 0),
                contract_multiplier=mult,
                increases_risk=increases,
            )
        cfg = self.sizing_config
        equity = self._book_equity()
        atr = self._resolve_atr(ticker=ticker, class_code=class_code)
        qty = size_quantity(
            equity=equity,
            price=price,
            config=cfg,
            strength=strength,
            atr=atr,
            fixed_qty=float(quantity)
            if cfg.mode == SizingMode.FIXED
            else None,
            contract_multiplier=mult,
        )
        # Never fall back to the caller's raw qty: that undoes strength_floor
        # rejects and max_position / ATR zero-sizes.
        if qty <= 0:
            return 0.0
        increases = self._order_increases_symbol_risk(
            ticker=ticker,
            class_code=class_code,
            action=action,
            quantity=float(qty),
        )
        return self._clamp_qty_to_leverage_headroom(
            float(qty),
            price=float(price or 0),
            contract_multiplier=mult,
            increases_risk=increases,
        )

    def submit_signal(
        self,
        *,
        ticker: str,
        action: str,
        quantity: float,
        strategy_id: str = "",
        class_code: str = "",
        mark_price: float | None = None,
        client_id: str | None = None,
        reduce_only: bool = False,
        exit_reason: str = "",
        venue: str = "",
        account: str = "",
        strength: float | None = None,
        apply_sizing: bool = True,
        order_type: str = "market",
        limit_price: float | None = None,
    ) -> OrderResult:
        """Paper (sync) path — used when ``mode=paper``."""
        priced = float(mark_price or 0)
        prefer: dict[str, float] = {}
        if priced > 0:
            key = f"{class_code}:{ticker}" if class_code else ticker
            prefer[key] = priced
        self._sync_open_marks(prefer=prefer)
        qty = self._apply_sizing(
            ticker=ticker,
            class_code=class_code,
            quantity=float(quantity),
            mark_price=priced,
            reduce_only=reduce_only,
            strength=strength,
            apply_sizing=apply_sizing,
            action=action,
        )
        if float(qty) <= 0:
            reason = "qty<=0"
            if (
                apply_sizing
                and self.sizing_config is not None
                and self.sizing_config.rejects_strength(strength)
            ):
                reason = (
                    f"strength_below_floor={float(strength or 0):.6g}"
                    f"<{float(self.sizing_config.strength_floor):.4g}"
                )
            elif not reduce_only and self._order_increases_symbol_risk(
                ticker=ticker,
                class_code=class_code,
                action=action,
                quantity=float(quantity),
            ):
                max_lev = float(self.risk.limits.max_leverage or 0.0)
                equity = self._book_equity()
                if max_lev > 0 and equity > 0:
                    lev = float(self.orders.gross_exposure) / equity
                    if lev >= max_lev - 1e-9:
                        reason = f"no_leverage_headroom={lev:.3f}>={max_lev:.3f}"
            return OrderResult(
                client_id=client_id or "",
                status=OrderStatus.REJECTED,
                reason=reason,
            )
        intent, rejected, price = self._build_intent(
            ticker=ticker,
            action=action,
            quantity=qty,
            strategy_id=strategy_id,
            class_code=class_code,
            mark_price=priced,
            client_id=client_id,
            reduce_only=reduce_only,
            exit_reason=exit_reason,
            venue=venue,
            account=account,
            order_type=order_type,
            limit_price=limit_price,
        )
        if rejected is not None:
            return rejected
        assert intent is not None
        gated = self._gate_consilium_intent(intent)
        if gated is not None:
            self.orders.history.append(gated)
            return gated
        if self._has_pending_exit_conflict(intent):
            return OrderResult(
                client_id=intent.client_id,
                status=OrderStatus.REJECTED,
                reason="exit_already_pending",
            )
        result = self.orders.submit(intent, mark_price=price)
        self._attribute_fill(intent, result)
        if result.status in {OrderStatus.FILLED, OrderStatus.PARTIAL} and self.trade_journal is not None:
            self.trade_journal.record(
                symbol=intent.exposure_key,
                side=intent.side.value,
                quantity=float(result.filled_qty or intent.quantity),
                price=float(result.avg_price or price),
                strategy_id=intent.strategy_id or "",
                reason=intent.exit_reason or "signal",
                fee=float((result.raw or {}).get("fee") or 0),
            )
        if result.status == OrderStatus.FILLED and class_code:
            if abs(self.orders.exposures.get(intent.exposure_key, 0.0)) > 1e-9:
                self.pinned_keys.add(intent.exposure_key)
            else:
                self.pinned_keys.discard(intent.exposure_key)
        return result

    async def submit_signal_async(
        self,
        *,
        ticker: str,
        action: str,
        quantity: float,
        strategy_id: str = "",
        class_code: str = "",
        mark_price: float | None = None,
        client_id: str | None = None,
        reduce_only: bool = False,
        exit_reason: str = "",
        venue: str = "",
        account: str = "",
        strength: float | None = None,
        apply_sizing: bool = True,
        order_type: str = "market",
        limit_price: float | None = None,
    ) -> OrderResult:
        """
        Route by mode: paper → sync fill; quik/live → broker after risk check.
        """
        mode = (self.mode or "paper").lower()
        if mode in {"paper", "sim", "simulation", "phantom", "replay"}:
            return self.submit_signal(
                ticker=ticker,
                action=action,
                quantity=quantity,
                strategy_id=strategy_id,
                class_code=class_code,
                mark_price=mark_price,
                client_id=client_id,
                reduce_only=reduce_only,
                exit_reason=exit_reason,
                venue=venue,
                account=account,
                strength=strength,
                apply_sizing=apply_sizing,
                order_type=order_type,
                limit_price=limit_price,
            )

        priced = float(mark_price or 0)
        prefer: dict[str, float] = {}
        if priced > 0:
            key = f"{class_code}:{ticker}" if class_code else ticker
            prefer[key] = priced
        self._sync_open_marks(prefer=prefer)
        qty = self._apply_sizing(
            ticker=ticker,
            class_code=class_code,
            quantity=float(quantity),
            mark_price=priced,
            reduce_only=reduce_only,
            strength=strength,
            apply_sizing=apply_sizing,
            action=action,
        )
        if float(qty) <= 0:
            reason = "qty<=0"
            if (
                apply_sizing
                and self.sizing_config is not None
                and self.sizing_config.rejects_strength(strength)
            ):
                reason = (
                    f"strength_below_floor={float(strength or 0):.6g}"
                    f"<{float(self.sizing_config.strength_floor):.4g}"
                )
            elif not reduce_only and self._order_increases_symbol_risk(
                ticker=ticker,
                class_code=class_code,
                action=action,
                quantity=float(quantity),
            ):
                max_lev = float(self.risk.limits.max_leverage or 0.0)
                equity = self._book_equity()
                if max_lev > 0 and equity > 0:
                    lev = float(self.orders.gross_exposure) / equity
                    if lev >= max_lev - 1e-9:
                        reason = f"no_leverage_headroom={lev:.3f}>={max_lev:.3f}"
            return OrderResult(
                client_id=client_id or "",
                status=OrderStatus.REJECTED,
                reason=reason,
            )
        intent, rejected, price = self._build_intent(
            ticker=ticker,
            action=action,
            quantity=qty,
            strategy_id=strategy_id,
            class_code=class_code,
            mark_price=priced,
            client_id=client_id,
            reduce_only=reduce_only,
            exit_reason=exit_reason,
            venue=venue,
            account=account,
            order_type=order_type,
            limit_price=limit_price,
        )
        if rejected is not None:
            return rejected
        assert intent is not None
        gated = self._gate_consilium_intent(intent)
        if gated is not None:
            self.orders.history.append(gated)
            return gated
        if self._has_pending_exit_conflict(intent):
            return OrderResult(
                client_id=intent.client_id,
                status=OrderStatus.REJECTED,
                reason="exit_already_pending",
            )
        if mode in {"live", "quik"} and not self.live_authorized:
            transport = getattr(self.broker, "transport", None)
            sandbox = intent.account.lower() in {"sandbox", "paper", "test"} or (
                "fake" in type(transport).__name__.lower()
            )
            if not sandbox:
                result = OrderResult(
                    client_id=intent.client_id,
                    status=OrderStatus.REJECTED,
                    reason="live_preflight_required",
                )
                self.orders.history.append(result)
                return result

        risk_reject, qty = self.orders._risk_qty(intent, mark_price=price)  # noqa: SLF001
        if risk_reject is not None:
            return risk_reject

        # The reconciliation gate must apply to *both* execution paths. It used to
        # sit inside the `durable_route` branch, so whenever `capabilities()` raised
        # KeyError (broker client not attached — i.e. exactly while the link is
        # flapping) execution fell through to the legacy broker path with no gate
        # at all, and an active CASH_BREAK / BROKER_ERROR block was ignored.
        if self.reconciliation is not None and not intent.reduce_only:
            try:
                self.reconciliation.assert_new_risk_allowed()
            except Exception as exc:
                result = OrderResult(
                    client_id=intent.client_id,
                    status=OrderStatus.REJECTED,
                    reason=str(exc),
                )
                self.orders.history.append(result)
                return result

        durable_route = False
        if self.execution_router is not None:
            try:
                self.execution_router.capabilities(intent.venue, intent.account)
                durable_route = True
            except KeyError:
                durable_route = False
        if durable_route and self.execution_router is not None:
            durable = BrokerOrder(
                symbol=intent.symbol,
                side=(
                    BrokerOrderSide.BUY
                    if intent.side == OrderSide.BUY
                    else BrokerOrderSide.SELL
                ),
                quantity=Decimal(str(qty)),
                venue=intent.venue,
                account=intent.account,
                client_id=intent.client_id,
                idempotency_key=intent.client_id,
                order_type=intent.order_type,
                limit_price=(
                    Decimal(str(intent.limit_price))
                    if intent.limit_price is not None
                    else None
                ),
                raw={
                    "class_code": intent.class_code,
                    "strategy_id": intent.strategy_id,
                    "reduce_only": intent.reduce_only,
                    "exit_reason": intent.exit_reason,
                },
            )
            placed = await self.execution_router.place(durable)
            status_map = {
                BrokerOrderState.ACCEPTED: OrderStatus.ACCEPTED,
                BrokerOrderState.PARTIAL: OrderStatus.PARTIAL,
                BrokerOrderState.FILLED: OrderStatus.FILLED,
                BrokerOrderState.CANCELLED: OrderStatus.CANCELLED,
                BrokerOrderState.REJECTED: OrderStatus.REJECTED,
            }
            broker_result = OrderResult(
                client_id=intent.client_id,
                status=status_map.get(placed.state, OrderStatus.REJECTED),
                filled_qty=float(placed.filled_quantity),
                avg_price=(
                    float(placed.average_fill_price)
                    if placed.average_fill_price is not None
                    else None
                ),
                reason=f"{placed.venue}:{placed.state.value}",
                raw={
                    **dict(placed.raw),
                    "broker_order_id": placed.broker_order_id,
                    "revision": placed.revision,
                },
            )
        elif self.broker is None:
            result = OrderResult(
                client_id=intent.client_id,
                status=OrderStatus.REJECTED,
                reason="no_broker",
            )
            self.orders.history.append(result)
            return result

        else:
            intent.quantity = qty
            broker_result = await self.broker.place_order(intent)
        result = self.orders.record_broker_result(
            intent, broker_result, mark_price=price, qty=qty
        )
        self._attribute_fill(intent, result)
        if (
            result.status in {OrderStatus.FILLED, OrderStatus.PARTIAL}
            and result.filled_qty > 0
            and class_code
        ):
            if abs(self.orders.exposures.get(intent.exposure_key, 0.0)) > 1e-9:
                self.pinned_keys.add(intent.exposure_key)
            else:
                self.pinned_keys.discard(intent.exposure_key)
        # Mirror the protective stop at the broker. The in-memory rule only lives
        # as long as this process, so without this every entry is naked the moment
        # the engine restarts, crashes or wedges — and a restart left the bot
        # believing it held no positions at all.
        if result.status in {OrderStatus.FILLED, OrderStatus.PARTIAL}:
            for slice_id in self._slices_for_exposure(intent.exposure_key):
                await self.place_native_stop(slice_id)
            self.persist_protection_state()
        return result

    def _slices_for_exposure(self, exposure_key: str) -> tuple[str, ...]:
        """Open slice ids matching an exposure key, in stable order."""
        target = str(exposure_key or "")
        tail = target.split(":")[-1]
        return tuple(
            slice_.slice_id
            for slice_ in self.protection.ledger.all()
            if getattr(slice_, "state", None) is not ExitState.CLOSED
            and float(getattr(slice_, "remaining_quantity", 0.0) or 0.0) > 0
            and str(getattr(slice_, "symbol", "")) in {target, tail}
        )

    def snapshot(self, *, ohlc_limit: int = 0) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "halted": self.halted,
            "halt_reason": self._halt_reason,
            "equity": self.orders.equity,
            "exposures": dict(self.orders.exposures),
            "gross_exposure": self.orders.gross_exposure,
            "positions": {
                key: {
                    "quantity": position.quantity,
                    "avg_price": position.avg_price,
                    "mark_price": position.mark_price,
                    "notional": position.notional,
                    "realized_pnl": position.realized_pnl,
                    "unrealized_pnl": position.unrealized_pnl,
                }
                for key, position in self.orders.positions.items()
                if abs(position.quantity) > 1e-12
            },
            "fills": len(self.orders.history),
            "fill_ledger": [fill.to_dict() for fill in self.orders.fill_ledger[-100:]],
            "total_costs": str(self.orders.total_costs),
            "cash": str(self.orders.cash),
            "risk": self.risk.snapshot(),
            "candles": self.candles.snapshot(ohlc_limit=ohlc_limit),
            "pinned_keys": sorted(self.open_position_keys()),
            "broker": type(self.broker).__name__ if self.broker else None,
            "execution_router": bool(self.execution_router),
            "reconciliation_healthy": (
                not self.reconciliation.new_risk_blocked
                if self.reconciliation is not None
                else False
            ),
            "live_authorized": self.live_authorized,
            "live_preflight_nonce": (
                getattr(self.live_preflight_report, "nonce", None)
                if self.live_preflight_report is not None
                else None
            ),
            "protection": self.protection.snapshot(),
            "position_book": self.position_book.snapshot(),
            "insurance": self.insurance.snapshot() if self.insurance else None,
            "performance": self.performance.snapshot() if self.performance else None,
            "phantom_timeline": (
                self.phantom_timeline.snapshot() if self.phantom_timeline else None
            ),
            "open_positions": self.open_positions(),
            "open_orders": self.open_orders(),
            "recent_trades": self.recent_trades(),
            "cost_schedule_version": getattr(
                self.orders.cost_schedule, "version", None
            ),
        }

    def publish_metrics(self) -> None:
        """Feed the Prometheus gauges the alert rules in ops/ actually read.

        The exporter existed and all ten alerts had matching metric names, but
        nothing ever called this, so the alerts could not fire — including
        ``trading_reconciliation_ok``, whose incident has been persisted in the
        order store since 2026-07-29. Called from the UI bridge snapshot loop.
        """
        try:
            obs = getattr(self.core, "operations_observability", None)
            if obs is None:
                return
            equity = float(self.orders.equity or 0.0)
            peak = float(self.orders.peak_equity or equity)
            drawdown = max(0.0, (peak - equity) / peak) if peak > 0 else 0.0
            obs.drawdown.set(drawdown, {"scope": "portfolio"})
            obs.broker_up.set(
                1.0 if self.broker is not None else 0.0,
                {"broker": str(getattr(self, "venue", "") or "default")},
            )
            obs.reconcile_ok.set(
                1.0
                if (self.reconciliation is not None and not self.reconciliation.new_risk_blocked)
                else 0.0,
                {"account": self.orders.account},
            )
            # Stop coverage: fraction of open positions that carry a live
            # protective slice. With no broker-native stops (see ProtectiveLayer)
            # this is the honest measure of how much of the book is unprotected.
            open_keys = set(self.open_position_keys())
            if open_keys:
                protected = {
                    key
                    for key in open_keys
                    if self.protection.has_protection(key)
                }
                obs.stop_coverage.set(
                    len(protected) / len(open_keys), {"account": self.orders.account}
                )
            else:
                obs.stop_coverage.set(1.0, {"account": self.orders.account})
            # Orders whose broker state is unknowable. This is the signal that
            # reconciliation is overdue: the send may or may not have reached the
            # venue, so neither "no position" nor "filled" can be assumed.
            unknown = sum(
                1
                for row in self.orders.history
                if getattr(row, "status", None) is OrderStatus.UNKNOWN
            )
            obs.unknown_orders.set(unknown, {"account": self.orders.account})
        except Exception:  # noqa: BLE001 - metrics must never break trading
            pass

    def protective_instructions(
        self,
        *,
        symbol: str,
        open_price: float,
        high: float,
        low: float,
        close: float,
        atr: float | None = None,
    ):
        return self.protection.on_bar(
            symbol=symbol,
            open_price=open_price,
            high=high,
            low=low,
            close=close,
            atr=atr,
        )

    def _exit_symbol_parts(self, instruction: Any) -> tuple[str, str]:
        symbol = str(self.protection.ledger.get(instruction.slice_id).symbol)
        if ":" in symbol:
            class_code, ticker = symbol.split(":", 1)
            return class_code, ticker
        return "", symbol

    def _release_failed_exit(self, instruction: Any, result: OrderResult) -> None:
        """If a protective exit never got a fill, free the EXIT_PENDING reservation."""
        if result.status in {OrderStatus.FILLED, OrderStatus.PARTIAL}:
            return
        if result.status in {OrderStatus.ACCEPTED, OrderStatus.UNKNOWN}:
            # Live path: keep reservation until fill or stale-timeout recovery.
            # UNKNOWN is grouped with ACCEPTED deliberately. The send failed in a
            # way that leaves the broker's state unknowable, so the protective
            # exit may well be live; releasing the reservation here would let the
            # layer book a second exit against the same quantity and, worse,
            # treat a still-protected slice as naked.
            return
        try:
            self.protection.cancel_exit(
                instruction.slice_id, order_id=instruction.order_id
            )
        except Exception:
            pass

    def release_stale_protective_exits(self) -> list[str]:
        return self.protection.release_stale_exits(
            timeout_sec=float(self.exit_pending_timeout_sec or 30.0)
        )

    def execute_protective_exit(
        self, instruction: Any, *, mark_price: float
    ) -> OrderResult:
        class_code, ticker = self._exit_symbol_parts(instruction)
        result = self.submit_signal(
            ticker=ticker,
            class_code=class_code,
            action=instruction.side,
            quantity=float(instruction.quantity),
            strategy_id="protective_layer",
            mark_price=mark_price,
            client_id=instruction.order_id,
            reduce_only=True,
            exit_reason=instruction.reason,
        )
        self._release_failed_exit(instruction, result)
        return result

    async def execute_protective_exit_async(
        self, instruction: Any, *, mark_price: float
    ) -> OrderResult:
        class_code, ticker = self._exit_symbol_parts(instruction)
        result = await self.submit_signal_async(
            ticker=ticker,
            class_code=class_code,
            action=instruction.side,
            quantity=float(instruction.quantity),
            strategy_id="protective_layer",
            mark_price=mark_price,
            client_id=instruction.order_id,
            reduce_only=True,
            exit_reason=instruction.reason,
        )
        self._release_failed_exit(instruction, result)
        return result

    def process_bar(self, bar: StoredBar) -> list[OrderResult]:
        """Evaluate protective stops on a candle and execute reduce-only exits."""
        if bar.close <= 0:
            return []
        self.release_stale_protective_exits()
        atr = self._atr_value(bar.key) or self._atr_value(bar.symbol)
        results: list[OrderResult] = []
        seen: set[str] = set()
        for symbol in {bar.key, bar.symbol}:
            if not symbol:
                continue
            instructions = self.protective_instructions(
                symbol=symbol,
                open_price=float(bar.open or bar.close),
                high=float(bar.high or bar.close),
                low=float(bar.low or bar.close),
                close=float(bar.close),
                atr=atr,
            )
            for instruction in instructions:
                if instruction.order_id in seen:
                    continue
                seen.add(instruction.order_id)
                results.append(
                    self.execute_protective_exit(
                        instruction, mark_price=float(bar.close)
                    )
                )
        return results

    async def process_bar_async(self, bar: StoredBar) -> list[OrderResult]:
        if bar.close <= 0:
            return []
        self.release_stale_protective_exits()
        atr = self._atr_value(bar.key) or self._atr_value(bar.symbol)
        results: list[OrderResult] = []
        seen: set[str] = set()
        for symbol in {bar.key, bar.symbol}:
            if not symbol:
                continue
            instructions = self.protective_instructions(
                symbol=symbol,
                open_price=float(bar.open or bar.close),
                high=float(bar.high or bar.close),
                low=float(bar.low or bar.close),
                close=float(bar.close),
                atr=atr,
            )
            for instruction in instructions:
                if instruction.order_id in seen:
                    continue
                seen.add(instruction.order_id)
                results.append(
                    await self.execute_protective_exit_async(
                        instruction, mark_price=float(bar.close)
                    )
                )
        return results

    @staticmethod
    def _split_symbol_key(symbol: str) -> tuple[str, str]:
        text = str(symbol or "").strip()
        if ":" in text:
            class_code, ticker = text.split(":", 1)
            return class_code.strip(), ticker.strip()
        return "", text

    def _resolve_book_key(self, symbol: str) -> str | None:
        """Match CLASS:TICKER or bare ticker against the open book."""
        text = str(symbol or "").strip()
        if not text:
            return None
        if text in self.orders.positions and abs(
            self.orders.positions[text].quantity
        ) > 1e-12:
            return text
        class_code, ticker = self._split_symbol_key(text)
        if class_code:
            key = f"{class_code}:{ticker}"
            if key in self.orders.positions and abs(
                self.orders.positions[key].quantity
            ) > 1e-12:
                return key
        for key, pos in self.orders.positions.items():
            if abs(pos.quantity) <= 1e-12:
                continue
            if key == text or key.endswith(f":{text}") or key.split(":")[-1] == ticker:
                return key
        return text if text in self.orders.positions else None

    def preview_operator_risk(
        self,
        *,
        ticker: str,
        side: str,
        quantity: float,
        class_code: str = "",
        mark_price: float | None = None,
        reduce_only: bool = False,
        order_type: str = "market",
        limit_price: float | None = None,
    ) -> dict[str, Any]:
        intent, rejected, price = self._build_intent(
            ticker=ticker,
            action=side,
            quantity=float(quantity),
            strategy_id="ai_operator",
            class_code=class_code,
            mark_price=float(mark_price or 0),
            reduce_only=reduce_only,
            exit_reason="dry_run",
            order_type=order_type,
            limit_price=limit_price,
        )
        if rejected is not None:
            return {
                "allowed": False,
                "reason": rejected.reason,
                "qty_requested": float(quantity),
                "qty_allowed": 0.0,
                "trace": {},
            }
        assert intent is not None
        return self.orders.preview_risk(intent, mark_price=price)

    async def operator_place_order(
        self,
        *,
        ticker: str,
        side: str,
        quantity: float,
        class_code: str = "",
        mark_price: float | None = None,
        reduce_only: bool = False,
        order_type: str = "market",
        limit_price: float | None = None,
        exit_reason: str = "ai_operator",
        client_id: str | None = None,
    ) -> OrderResult:
        """Operator / AI path — no Consilium sizing; still risk + insurance gated."""
        return await self.submit_signal_async(
            ticker=ticker,
            action=side,
            quantity=float(quantity),
            strategy_id="ai_operator",
            class_code=class_code,
            mark_price=mark_price,
            client_id=client_id,
            reduce_only=bool(reduce_only),
            exit_reason=exit_reason or "ai_operator",
            apply_sizing=False,
            order_type=order_type,
            limit_price=limit_price,
        )

    async def operator_close_position(
        self,
        *,
        symbol: str,
        quantity: float | None = None,
        mark_price: float | None = None,
        order_type: str = "market",
        limit_price: float | None = None,
        exit_reason: str = "ai_close",
    ) -> OrderResult:
        key = self._resolve_book_key(symbol)
        if key is None:
            return OrderResult(
                client_id="",
                status=OrderStatus.REJECTED,
                reason="position_not_found",
            )
        pos = self.orders.positions.get(key)
        if pos is None or abs(pos.quantity) <= 1e-12:
            return OrderResult(
                client_id="",
                status=OrderStatus.REJECTED,
                reason="flat",
            )
        class_code, ticker = self._split_symbol_key(key)
        qty_close = abs(float(pos.quantity))
        if quantity is not None and float(quantity) > 0:
            qty_close = min(qty_close, abs(float(quantity)))
        side = "SELL" if pos.quantity > 0 else "BUY"
        priced = float(mark_price or pos.mark_price or 0)
        return await self.operator_place_order(
            ticker=ticker,
            side=side,
            quantity=qty_close,
            class_code=class_code,
            mark_price=priced,
            reduce_only=True,
            order_type=order_type,
            limit_price=limit_price,
            exit_reason=exit_reason,
        )

    async def operator_close_all(
        self,
        *,
        mark_price: float | None = None,
        order_type: str = "market",
        limit_price: float | None = None,
        exit_reason: str = "ai_close_all",
    ) -> list[OrderResult]:
        keys = [
            key
            for key, pos in list(self.orders.positions.items())
            if abs(pos.quantity) > 1e-12
        ]
        results: list[OrderResult] = []
        for key in keys:
            results.append(
                await self.operator_close_position(
                    symbol=key,
                    mark_price=mark_price,
                    order_type=order_type,
                    limit_price=limit_price,
                    exit_reason=exit_reason,
                )
            )
        return results

    def operator_release_exit(
        self,
        *,
        slice_id: str | None = None,
        symbol: str | None = None,
        client_id: str | None = None,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        """Clear stuck EXIT_PENDING reservations.

        Requires a selector (slice_id / symbol / client_id) unless ``force=True``
        to release the entire exit queue.
        """
        if not force and not (slice_id or symbol or client_id):
            return []
        released: list[dict[str, Any]] = []
        for position in list(self.protection.ledger.all()):
            if position.state is not ExitState.EXIT_PENDING:
                continue
            if slice_id and position.slice_id != slice_id:
                continue
            if symbol:
                key = self._resolve_book_key(symbol) or str(symbol)
                if position.symbol != key and position.symbol != symbol:
                    continue
            order_id = position.exit_order_id
            if client_id and order_id != client_id:
                continue
            if not order_id:
                continue
            try:
                self.protection.cancel_exit(position.slice_id, order_id=order_id)
            except Exception:
                continue
            released.append(
                {
                    "slice_id": position.slice_id,
                    "symbol": position.symbol,
                    "order_id": order_id,
                }
            )
        return released

    async def operator_cancel_order(self, client_id: str) -> dict[str, Any]:
        """Cancel broker working order and release matching EXIT_PENDING if any."""
        cid = str(client_id or "").strip()
        if not cid:
            return {"ok": False, "error": "client_id_required"}
        broker_result: Any = None
        broker_error: str | None = None
        purged_local = False
        if self.execution_router is not None:
            try:
                before = self.execution_router.store.get(cid)
                broker_result = await self.execution_router.cancel(cid)
                if (
                    before is not None
                    and str(getattr(before.state, "value", before.state)).lower()
                    == "unknown"
                    and not before.broker_order_id
                    and broker_result is not None
                    and str(getattr(broker_result.state, "value", "")).lower()
                    == "cancelled"
                ):
                    purged_local = True
            except Exception as exc:  # noqa: BLE001
                broker_error = str(exc)
        elif self.broker is not None and hasattr(self.broker, "cancel_order"):
            try:
                broker_result = await self.broker.cancel_order(cid)
            except Exception as exc:  # noqa: BLE001
                broker_error = str(exc)
        else:
            broker_error = "cancel_unavailable"
        released = self.operator_release_exit(client_id=cid, force=True)
        return {
            "ok": broker_error is None or bool(released) or purged_local,
            "client_id": cid,
            "purged_local": purged_local,
            "broker_result": (
                broker_result.to_dict()
                if hasattr(broker_result, "to_dict")
                else (
                    broker_result
                    if not hasattr(broker_result, "__dict__")
                    else str(broker_result)
                )
            ),
            "broker_error": broker_error,
            "released_exits": released,
        }

    async def operator_cancel_all_working(
        self,
        *,
        symbol: str | None = None,
        confirm_cancel_all: bool = False,
    ) -> dict[str, Any]:
        """Cancel every working/open order (optional symbol filter).

        Local orphans (state=unknown, no broker_order_id) are purged from the
        durable store without a broker round-trip — cancel_order by client_id
        still works; this bulk path exists when the UI/AI only sees qty/price.
        """
        if not confirm_cancel_all:
            return {
                "ok": False,
                "error": "confirm_cancel_all_required",
                "hint": "Pass confirm_cancel_all=true after explicit confirmation.",
            }
        want = str(symbol or "").strip().upper()
        rows = list(self.open_orders())
        selected: list[dict[str, Any]] = []
        for row in rows:
            sym = str(row.get("symbol") or "").upper()
            if want and want not in sym and not sym.endswith(f":{want}"):
                continue
            selected.append(row)
        results: list[dict[str, Any]] = []
        for row in selected:
            cid = str(row.get("client_id") or "").strip()
            if not cid:
                results.append(
                    {
                        "ok": False,
                        "error": "missing_client_id",
                        "symbol": row.get("symbol"),
                        "state": row.get("state"),
                    }
                )
                continue
            results.append(await self.operator_cancel_order(cid))
        ok_n = sum(1 for r in results if r.get("ok"))
        return {
            "ok": ok_n == len(results) if results else True,
            "requested": len(selected),
            "cancelled": ok_n,
            "failed": len(results) - ok_n,
            "symbol_filter": want or None,
            "results": results,
            "remaining_open": len(self.open_orders()),
        }

    async def cancel_order(self, client_id: str) -> Any:
        if self.execution_router is None:
            raise RuntimeError("durable execution router is unavailable")
        return await self.execution_router.cancel(client_id)

    async def replace_order(self, client_id: str, **changes: Any) -> Any:
        if self.execution_router is None:
            raise RuntimeError("durable execution router is unavailable")
        return await self.execution_router.replace(client_id, **changes)

    async def refresh_order(self, client_id: str) -> Any:
        if self.execution_router is None:
            raise RuntimeError("durable execution router is unavailable")
        return await self.execution_router.status(client_id)


def get_runtime(core: Any) -> TradingRuntime | None:
    return getattr(core, "trading", None)


def resolve_quik_broker(core: Any) -> Any | None:
    """Pick QuikBrokerExecution from quik_market_service when transport is wired."""
    reg = getattr(core, "service_registry", None)
    if reg is None:
        return None
    quik = reg.get("quik_market_service")
    if quik is None:
        return None
    kind = str(getattr(quik, "config", {}).get("transport") or "null").lower()
    if kind in {"", "null", "none", "unavailable"}:
        return None
    return getattr(quik, "broker", None)
