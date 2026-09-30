"""Insurance layer presets and daily/portfolio halt guards (docs/todo2.md §1).

Builds on ``RiskManager`` / ``ProtectiveLayer``. Presets map the product
conservative / moderate / aggressive tables onto ``RiskLimits`` and default
``StrategyRiskProfile`` overrides (stop %, trailing via chandelier, time-stop).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, Mapping

from .protection import StrategyRiskProfile
from .risk import RiskDecision, RiskLimits, RiskManager


@dataclass(frozen=True, slots=True)
class InsurancePreset:
    name: str
    stop_loss_pct: float
    trailing_stop_pct: float
    time_stop_hours: float
    daily_loss_pct: float
    max_strategy_dd: float
    max_portfolio_dd: float


PRESETS: dict[str, InsurancePreset] = {
    "conservative": InsurancePreset(
        name="conservative",
        stop_loss_pct=0.01,
        trailing_stop_pct=0.01,
        time_stop_hours=12,
        daily_loss_pct=0.03,
        max_strategy_dd=0.03,
        max_portfolio_dd=0.06,
    ),
    "moderate": InsurancePreset(
        name="moderate",
        stop_loss_pct=0.02,
        trailing_stop_pct=0.015,
        time_stop_hours=24,
        daily_loss_pct=0.05,
        max_strategy_dd=0.05,
        max_portfolio_dd=0.10,
    ),
    "aggressive": InsurancePreset(
        name="aggressive",
        stop_loss_pct=0.03,
        trailing_stop_pct=0.02,
        time_stop_hours=48,
        daily_loss_pct=0.07,
        max_strategy_dd=0.08,
        max_portfolio_dd=0.15,
    ),
}


def resolve_preset(name: str | None) -> InsurancePreset:
    key = str(name or "moderate").strip().lower()
    if key not in PRESETS:
        raise ValueError(f"unknown insurance preset: {name!r}")
    return PRESETS[key]


def limits_from_preset(
    preset: InsurancePreset | str,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> RiskLimits:
    """Map preset → RiskLimits.

    Daily loss is enforced by ``InsuranceGuard`` (has day-start equity). It is
    only written onto ``RiskLimits.max_daily_loss_pct`` when explicitly present
    in ``overrides``, so OrderManager's fail-closed advanced gates stay quiet.
    """
    p = resolve_preset(preset) if isinstance(preset, str) else preset
    raw = dict(overrides or {})
    return RiskLimits(
        max_portfolio_drawdown=float(
            raw.get("max_portfolio_drawdown", p.max_portfolio_dd)
        ),
        max_strategy_drawdown=float(
            raw.get("max_strategy_drawdown", p.max_strategy_dd)
        ),
        max_position_pct=float(raw.get("max_position_pct", 0.20)),
        max_leverage=float(raw.get("max_leverage", 1.0)),
        max_portfolio_drawdown_abs=raw.get("max_portfolio_drawdown_abs"),
        max_strategy_drawdown_abs=raw.get("max_strategy_drawdown_abs"),
        max_daily_loss_pct=raw.get("max_daily_loss_pct"),
        max_daily_turnover_pct=raw.get("max_daily_turnover_pct"),
        max_orders_per_minute=raw.get("max_orders_per_minute"),
        max_concentration_pct=raw.get("max_concentration_pct"),
        max_correlation=raw.get("max_correlation"),
        max_order_adv_pct=raw.get("max_order_adv_pct"),
        max_margin_utilization=raw.get("max_margin_utilization"),
        max_data_age_seconds=raw.get("max_data_age_seconds"),
    )


def profile_from_preset(
    preset: InsurancePreset | str,
    *,
    archetype: str = "trend",
) -> StrategyRiskProfile:
    """Map stop/trailing/time-stop table onto StrategyRiskProfile knobs."""
    p = resolve_preset(preset) if isinstance(preset, str) else preset
    # chandelier_atr_multiple ≈ trailing; max_loss_fraction ≈ hard stop.
    # Without live ATR we still get max-loss + time rules from ProtectiveLayer.
    return StrategyRiskProfile.for_archetype(
        archetype,
        max_loss_fraction=Decimal(str(p.stop_loss_pct)),
        chandelier_atr_multiple=Decimal(str(max(p.trailing_stop_pct * 100, 1.0))),
        max_holding_period=timedelta(hours=float(p.time_stop_hours)),
    )


class InsuranceGuard:
    """Independent halt checks: daily loss + portfolio drawdown (todo2 §1.2)."""

    def __init__(
        self,
        risk: RiskManager,
        *,
        daily_loss_pct: float | None = None,
        day_start_equity: float | None = None,
        peak_equity: float | None = None,
    ) -> None:
        self.risk = risk
        # Prefer explicit daily limit; fall back to RiskLimits only if set by overrides.
        limit = daily_loss_pct
        if limit is None:
            limit = risk.limits.max_daily_loss_pct
        self.daily_loss_pct = float(limit if limit is not None else 0.05)
        self.day_start_equity = float(day_start_equity or 0.0)
        self.peak_equity = float(peak_equity or day_start_equity or 0.0)

    def note_equity(self, equity: float) -> None:
        eq = float(equity)
        if self.day_start_equity <= 0:
            self.day_start_equity = eq
        if self.peak_equity <= 0:
            self.peak_equity = eq
        self.peak_equity = max(self.peak_equity, eq)

    def reset_day(self, equity: float) -> None:
        self.day_start_equity = float(equity)
        self.peak_equity = max(self.peak_equity, float(equity))

    def check(
        self,
        equity: float,
        *,
        daily_equity: float | None = None,
        gross_exposure: float | None = None,
    ) -> RiskDecision:
        """Halt checks.

        ``equity`` — mark-to-market (portfolio DD / peak).
        ``daily_equity`` — realized-only equity for daily_loss (ignores open MTM
        swings from oversized books). Falls back to ``equity`` when omitted.
        """
        self.note_equity(equity)
        if gross_exposure is not None and equity > 0:
            max_lev = float(self.risk.limits.max_leverage or 0.0)
            if max_lev > 0:
                lev = float(gross_exposure) / float(equity)
                if lev > max_lev + 1e-9:
                    # Soft block only: MTM/equity drift can push leverage over the
                    # ceiling without a new fill. Kill-switching here freezes the
                    # book and turns every consilium tick into leverage_breach spam.
                    # New risk is rejected; reduce_only exits stay available.
                    return RiskDecision(
                        False,
                        reason=f"no_leverage_headroom={lev:.3f}>{max_lev:.3f}",
                    )
        day_eq = float(equity if daily_equity is None else daily_equity)
        if self.day_start_equity > 0 and self.daily_loss_pct > 0:
            loss = max(0.0, (self.day_start_equity - day_eq) / self.day_start_equity)
            if loss >= self.daily_loss_pct:
                self.risk.emergency_halt(f"daily_loss={loss:.3f}")
                return RiskDecision(False, reason=self.risk._halt_reason)  # noqa: SLF001
        portfolio = self.risk.check_portfolio_drawdown(equity, self.peak_equity)
        if not portfolio.allowed:
            return portfolio
        return RiskDecision(True)

    def snapshot(self) -> dict[str, Any]:
        return {
            "day_start_equity": self.day_start_equity,
            "peak_equity": self.peak_equity,
            "daily_loss_pct": self.daily_loss_pct,
            "halted": self.risk.halted,
            "halt_reason": getattr(self.risk, "_halt_reason", ""),
            "limits": {
                "max_daily_loss_pct": self.daily_loss_pct,
                "max_portfolio_drawdown": self.risk.limits.max_portfolio_drawdown,
                "max_strategy_drawdown": self.risk.limits.max_strategy_drawdown,
            },
        }
