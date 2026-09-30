"""Portfolio / strategy risk checks (tech doc §7)."""

from __future__ import annotations

from dataclasses import dataclass, fields
import math
import time
from collections import deque
from collections.abc import Mapping
from typing import Any


@dataclass(slots=True)
class RiskLimits:
    """Default limits from the technical document."""

    max_portfolio_drawdown: float = 0.10  # −10% → full stop
    max_strategy_drawdown: float = 0.05  # −5% → pause strategy
    max_position_pct: float = 0.20  # 20% of equity per asset
    max_leverage: float = 1.0
    # Absolute drawdown caps in account currency (None / ≤0 = off).
    max_portfolio_drawdown_abs: float | None = None
    max_strategy_drawdown_abs: float | None = None
    # Advanced controls are opt-in to preserve the historical API and behavior.
    max_daily_loss_pct: float | None = None
    max_daily_turnover_pct: float | None = None
    max_orders_per_minute: int | None = None
    max_concentration_pct: float | None = None
    max_correlation: float | None = None
    max_order_adv_pct: float | None = None
    max_margin_utilization: float | None = None
    max_data_age_seconds: float | None = None


_RISK_LIMIT_KEYS = tuple(f.name for f in fields(RiskLimits))


@dataclass(slots=True)
class RiskDecision:
    allowed: bool
    reason: str = ""
    scaled_size: float | None = None


class RiskManager:
    """
    Checks against host-injected state, plus an optional local order-rate window.

    Host injects current equity and positions; this module does not talk to brokers.
    """

    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()
        # strategy_id → partial field overrides (merged over global limits).
        self.strategy_limit_overrides: dict[str, dict[str, Any]] = {}
        self._halted = False
        self._halt_reason = ""
        self._order_times: deque[float] = deque()

    def set_strategy_overrides(
        self, strategy_id: str, patch: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        """Set/replace per-strategy limit overrides. Empty/None clears the entry."""
        sid = str(strategy_id or "").strip()
        if not sid:
            raise ValueError("strategy_id required")
        if not patch:
            self.strategy_limit_overrides.pop(sid, None)
            return {}
        clean: dict[str, Any] = {}
        for key, value in patch.items():
            if key not in _RISK_LIMIT_KEYS:
                continue
            if value is None:
                clean[key] = None
                continue
            if key == "max_orders_per_minute":
                clean[key] = int(value)
            else:
                clean[key] = float(value)
                if key.endswith("_abs") or key in {
                    "max_daily_loss_pct",
                    "max_daily_turnover_pct",
                    "max_concentration_pct",
                    "max_correlation",
                    "max_order_adv_pct",
                    "max_margin_utilization",
                    "max_data_age_seconds",
                }:
                    if float(value) <= 0:
                        clean[key] = None
        if clean:
            self.strategy_limit_overrides[sid] = clean
        else:
            self.strategy_limit_overrides.pop(sid, None)
        return dict(self.strategy_limit_overrides.get(sid) or {})

    def limits_for(self, strategy_id: str | None = None) -> RiskLimits:
        """Global limits with optional per-strategy overlay."""
        sid = str(strategy_id or "").strip()
        ov = self.strategy_limit_overrides.get(sid) if sid else None
        if not ov:
            return self.limits
        kwargs = {name: getattr(self.limits, name) for name in _RISK_LIMIT_KEYS}
        for key, value in ov.items():
            if key in kwargs:
                kwargs[key] = value
        return RiskLimits(**kwargs)

    @property
    def halted(self) -> bool:
        return self._halted

    def emergency_halt(self, reason: str = "kill_switch") -> None:
        self._halted = True
        self._halt_reason = reason

    def resume(self) -> None:
        self._halted = False
        self._halt_reason = ""

    def check_daily_loss(
        self, day_start_equity: float, equity: float
    ) -> RiskDecision:
        limit = self.limits.max_daily_loss_pct
        if limit is None:
            return RiskDecision(True)
        if day_start_equity <= 0 or not self._finite(day_start_equity, equity):
            return RiskDecision(False, reason="daily_loss_inputs_invalid")
        loss = max(0.0, (day_start_equity - equity) / day_start_equity)
        if loss >= limit:
            self.emergency_halt(f"daily_loss={loss:.3f}")
            return RiskDecision(False, reason=self._halt_reason)
        return RiskDecision(True)

    def check_turnover(self, turnover: float, equity: float) -> RiskDecision:
        limit = self.limits.max_daily_turnover_pct
        if limit is None:
            return RiskDecision(True)
        if equity <= 0 or turnover < 0 or not self._finite(turnover, equity):
            return RiskDecision(False, reason="turnover_inputs_invalid")
        ratio = turnover / equity
        if ratio > limit:
            return RiskDecision(False, reason=f"daily_turnover={ratio:.3f}")
        return RiskDecision(True)

    def check_order_rate(
        self,
        orders_in_last_minute: int | None = None,
        *,
        now: float | None = None,
        record: bool = False,
    ) -> RiskDecision:
        """Check an injected count or the manager's local rolling counter."""
        limit = self.limits.max_orders_per_minute
        if limit is None:
            return RiskDecision(True)
        if orders_in_last_minute is not None:
            if orders_in_last_minute < 0:
                return RiskDecision(False, reason="order_rate_input_invalid")
            count = orders_in_last_minute
        else:
            current = time.monotonic() if now is None else float(now)
            if not math.isfinite(current):
                return RiskDecision(False, reason="order_rate_input_invalid")
            cutoff = current - 60.0
            while self._order_times and self._order_times[0] <= cutoff:
                self._order_times.popleft()
            count = len(self._order_times)
            if record and count < limit:
                self._order_times.append(current)
                count += 1
        if count >= limit and not (record and count == limit):
            return RiskDecision(False, reason=f"order_rate={count}/min")
        return RiskDecision(True)

    def record_order(self, *, now: float | None = None) -> RiskDecision:
        return self.check_order_rate(now=now, record=True)

    def check_concentration(
        self,
        exposures: Mapping[str, float],
        equity: float,
        *,
        proposed_symbol: str | None = None,
        proposed_exposure: float | None = None,
    ) -> RiskDecision:
        limit = self.limits.max_concentration_pct
        if limit is None:
            return RiskDecision(True)
        if equity <= 0 or not self._finite(equity, *exposures.values()):
            return RiskDecision(False, reason="concentration_inputs_invalid")
        values = dict(exposures)
        if proposed_symbol is not None and proposed_exposure is not None:
            values[proposed_symbol] = proposed_exposure
        if not values:
            return RiskDecision(True)
        symbol, largest = max(values.items(), key=lambda item: abs(item[1]))
        ratio = abs(largest) / equity
        if ratio > limit:
            return RiskDecision(False, reason=f"concentration={symbol}:{ratio:.3f}")
        return RiskDecision(True)

    def check_correlation(
        self,
        symbol: str,
        exposures: Mapping[str, float],
        correlations: Mapping[str, float],
    ) -> RiskDecision:
        limit = self.limits.max_correlation
        if limit is None:
            return RiskDecision(True)
        if not correlations and any(abs(value) > 0 for value in exposures.values()):
            return RiskDecision(False, reason="correlation_unknown")
        for other, exposure in exposures.items():
            if other == symbol or abs(exposure) <= 1e-12:
                continue
            correlation = correlations.get(other)
            if correlation is None or not self._finite(correlation):
                return RiskDecision(False, reason=f"correlation_unknown:{other}")
            if abs(correlation) > limit:
                return RiskDecision(
                    False, reason=f"correlation={symbol}:{other}:{correlation:.3f}"
                )
        return RiskDecision(True)

    def check_liquidity(
        self, order_notional: float, average_daily_volume: float
    ) -> RiskDecision:
        limit = self.limits.max_order_adv_pct
        if limit is None:
            return RiskDecision(True)
        if (
            average_daily_volume <= 0
            or order_notional < 0
            or not self._finite(order_notional, average_daily_volume)
        ):
            return RiskDecision(False, reason="liquidity_unknown")
        ratio = abs(order_notional) / average_daily_volume
        if ratio > limit:
            return RiskDecision(False, reason=f"liquidity_adv={ratio:.3f}")
        return RiskDecision(True)

    def check_margin(
        self, margin_used: float, equity: float, *, required_margin: float = 0.0
    ) -> RiskDecision:
        limit = self.limits.max_margin_utilization
        if limit is None:
            return RiskDecision(True)
        if (
            equity <= 0
            or margin_used < 0
            or required_margin < 0
            or not self._finite(margin_used, equity, required_margin)
        ):
            return RiskDecision(False, reason="margin_inputs_invalid")
        utilization = (margin_used + required_margin) / equity
        if utilization > limit:
            return RiskDecision(False, reason=f"margin_utilization={utilization:.3f}")
        return RiskDecision(True)

    def check_stale_data(
        self,
        *,
        data_age_seconds: float | None = None,
        data_timestamp: float | None = None,
        now: float | None = None,
    ) -> RiskDecision:
        limit = self.limits.max_data_age_seconds
        if limit is None:
            return RiskDecision(True)
        if data_age_seconds is None:
            if data_timestamp is None:
                return RiskDecision(False, reason="market_data_age_unknown")
            current = time.time() if now is None else float(now)
            data_age_seconds = current - data_timestamp
        if data_age_seconds < 0 or not self._finite(data_age_seconds):
            return RiskDecision(False, reason="market_data_age_invalid")
        if data_age_seconds > limit:
            return RiskDecision(False, reason=f"stale_data={data_age_seconds:.3f}s")
        return RiskDecision(True)

    def check_portfolio_drawdown(self, equity: float, peak_equity: float) -> RiskDecision:
        if peak_equity <= 0:
            return RiskDecision(True)
        loss = max(0.0, peak_equity - equity)
        dd = loss / peak_equity
        if dd >= self.limits.max_portfolio_drawdown:
            self._halted = True
            return RiskDecision(False, reason=f"portfolio_dd={dd:.3f}")
        abs_lim = self.limits.max_portfolio_drawdown_abs
        if abs_lim is not None and abs_lim > 0 and loss >= abs_lim:
            self._halted = True
            return RiskDecision(False, reason=f"portfolio_dd_abs={loss:.2f}")
        return RiskDecision(True)

    def check_strategy_drawdown(
        self, strategy_id: str, equity_curve_peak: float, equity_now: float
    ) -> RiskDecision:
        if equity_curve_peak <= 0:
            return RiskDecision(True)
        limits = self.limits_for(strategy_id)
        loss = max(0.0, equity_curve_peak - equity_now)
        dd = loss / equity_curve_peak
        if dd >= limits.max_strategy_drawdown:
            return RiskDecision(False, reason=f"strategy_dd={strategy_id}:{dd:.3f}")
        abs_lim = limits.max_strategy_drawdown_abs
        if abs_lim is not None and abs_lim > 0 and loss >= abs_lim:
            return RiskDecision(
                False, reason=f"strategy_dd_abs={strategy_id}:{loss:.2f}"
            )
        return RiskDecision(True)

    def check_order(
        self,
        *,
        equity: float,
        symbol: str,
        notional: float,
        current_symbol_exposure: float = 0.0,
        proposed_symbol_exposure: float | None = None,
        current_gross_exposure: float = 0.0,
        reduces_risk: bool = False,
        strategy_id: str = "",
        day_start_equity: float | None = None,
        daily_turnover: float | None = None,
        orders_in_last_minute: int | None = None,
        portfolio_exposures: Mapping[str, float] | None = None,
        correlations: Mapping[str, float] | None = None,
        average_daily_volume: float | None = None,
        margin_used: float | None = None,
        required_margin: float = 0.0,
        data_age_seconds: float | None = None,
    ) -> RiskDecision:
        if self._halted and not reduces_risk:
            return RiskDecision(False, reason=getattr(self, "_halt_reason", "halted"))
        if equity <= 0:
            return RiskDecision(False, reason="equity<=0")
        requested_delta = abs(notional)
        # Exits and other explicitly risk-reducing orders must remain available
        # during stale feeds, limit breaches, and emergency halts.
        if reduces_risk:
            return RiskDecision(True, scaled_size=requested_delta)

        limits = self.limits_for(strategy_id)
        # Enabled advanced gates are fail-closed when the required metric is absent.
        advanced = (
            self.check_daily_loss(day_start_equity, equity)
            if day_start_equity is not None
            else self._required(limits.max_daily_loss_pct, "daily_loss_unknown")
        )
        if not advanced.allowed:
            return advanced
        advanced = (
            self.check_turnover(daily_turnover + abs(notional), equity)
            if daily_turnover is not None
            else self._required(limits.max_daily_turnover_pct, "turnover_unknown")
        )
        if not advanced.allowed:
            return advanced
        # Order-rate uses the injected count when provided; otherwise the
        # manager's local rolling window (never fail-closed as "unknown").
        if orders_in_last_minute is not None:
            advanced = self.check_order_rate(orders_in_last_minute)
        elif self.limits.max_orders_per_minute is not None:
            advanced = self.check_order_rate()
        else:
            advanced = RiskDecision(True)
        if not advanced.allowed:
            return advanced
        advanced = (
            self.check_liquidity(abs(notional), average_daily_volume)
            if average_daily_volume is not None
            else self._required(limits.max_order_adv_pct, "liquidity_unknown")
        )
        if not advanced.allowed:
            return advanced
        advanced = (
            self.check_margin(
                margin_used, equity, required_margin=required_margin
            )
            if margin_used is not None
            else self._required(limits.max_margin_utilization, "margin_unknown")
        )
        if not advanced.allowed:
            return advanced
        advanced = self.check_stale_data(data_age_seconds=data_age_seconds)
        if not advanced.allowed:
            return advanced

        proposed = (
            float(proposed_symbol_exposure)
            if proposed_symbol_exposure is not None
            else current_symbol_exposure + requested_delta
        )
        # Temporarily swap limits for concentration helpers that read self.limits.
        saved = self.limits
        self.limits = limits
        try:
            if limits.max_concentration_pct is not None:
                if portfolio_exposures is None:
                    return RiskDecision(False, reason="concentration_unknown")
                concentration = self.check_concentration(
                    portfolio_exposures,
                    equity,
                    proposed_symbol=symbol,
                    proposed_exposure=proposed,
                )
                if not concentration.allowed:
                    return concentration
            if limits.max_correlation is not None:
                if portfolio_exposures is None or correlations is None:
                    return RiskDecision(False, reason="correlation_unknown")
                correlation = self.check_correlation(
                    symbol, portfolio_exposures, correlations
                )
                if not correlation.allowed:
                    return correlation
        finally:
            self.limits = saved

        # Position and leverage caps must compose: a max_position scale must not
        # skip the portfolio leverage gate (that previously let N×max_position
        # exceed max_leverage on a multi-symbol book).
        reason = ""
        scaled_size = float(requested_delta)
        max_notional = equity * limits.max_position_pct
        if abs(proposed) > max_notional + 1e-9 and not reduces_risk:
            capped = math.copysign(max_notional, proposed)
            scaled = abs(capped - current_symbol_exposure)
            if scaled <= 0:
                return RiskDecision(False, reason=f"max_position {symbol}")
            proposed = float(capped)
            scaled_size = float(scaled)
            reason = "scaled"

        # Account ceiling stays on global limits; strategy overlay may only tighten.
        global_lev = float(self.limits.max_leverage or 0.0)
        strat_lev = float(limits.max_leverage or 0.0)
        if global_lev > 0 and strat_lev > 0:
            max_lev = min(global_lev, strat_lev)
        else:
            max_lev = global_lev or strat_lev
        gross_after = (
            max(0.0, current_gross_exposure - abs(current_symbol_exposure))
            + abs(proposed)
        )
        max_gross = equity * max_lev if max_lev > 0 else float("inf")
        if max_lev > 0 and gross_after > max_gross + 1e-9 and not reduces_risk:
            available = max(
                0.0,
                max_gross
                - max(0.0, current_gross_exposure - abs(current_symbol_exposure)),
            )
            capped = math.copysign(available, proposed)
            scaled = abs(capped - current_symbol_exposure)
            if scaled <= 0:
                return RiskDecision(False, reason="max_leverage")
            return RiskDecision(True, reason="scaled_leverage", scaled_size=scaled)
        return RiskDecision(True, reason=reason, scaled_size=scaled_size)

    def snapshot(self) -> dict[str, Any]:
        return {
            "halted": self._halted,
            "limits": {
                name: getattr(self.limits, name) for name in _RISK_LIMIT_KEYS
            },
            "strategy_limits": {
                sid: dict(ov) for sid, ov in self.strategy_limit_overrides.items()
            },
        }

    @staticmethod
    def _finite(*values: float) -> bool:
        try:
            return all(math.isfinite(float(value)) for value in values)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _required(limit: Any, reason: str) -> RiskDecision:
        return RiskDecision(limit is None, reason="" if limit is None else reason)
