"""Target-performance monitor and degradation diagnostics (docs/todo2.md §2)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from math import sqrt
from typing import Any, Callable, Mapping


Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DiagnosisKind(str, Enum):
    PARAMETER_DRIFT = "parameter_drift"
    REGIME_CHANGE = "regime_change"
    STRUCTURAL_BREAK = "structural_break"
    MARKET_WEAK = "market_weak"
    HEALTHY = "healthy"


@dataclass(frozen=True, slots=True)
class PerformanceTargets:
    daily_target: float = 200.0
    daily_minimum: float = 100.0
    underperform_days: int = 3
    target_sharpe: float = 1.5
    target_win_rate: float = 0.55
    target_profit_factor: float = 1.8
    lookback_days: int = 7


@dataclass(slots=True)
class DayPnL:
    day: date
    pnl: float


@dataclass(frozen=True, slots=True)
class Diagnosis:
    kind: DiagnosisKind
    summary: str
    actions: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "summary": self.summary,
            "actions": list(self.actions),
            "details": dict(self.details),
        }


@dataclass(slots=True)
class PerformanceMonitor:
    """Tracks daily PnL vs targets and triggers diagnostic classification."""

    targets: PerformanceTargets = field(default_factory=PerformanceTargets)
    clock: Clock = _utc_now
    _days: deque[DayPnL] = field(default_factory=lambda: deque(maxlen=90))
    _returns: deque[float] = field(default_factory=lambda: deque(maxlen=90))
    _wins: int = 0
    _losses: int = 0
    _gross_profit: float = 0.0
    _gross_loss: float = 0.0
    last_diagnosis: Diagnosis | None = None

    def record_day(self, pnl: float, *, day: date | None = None) -> None:
        d = day or self.clock().date()
        # Replace same-day entry if present.
        existing = [item for item in self._days if item.day != d]
        self._days.clear()
        self._days.extend(existing)
        self._days.append(DayPnL(day=d, pnl=float(pnl)))
        self._returns.append(float(pnl))

    def record_trade(self, pnl: float) -> None:
        value = float(pnl)
        if value >= 0:
            self._wins += 1
            self._gross_profit += value
        else:
            self._losses += 1
            self._gross_loss += abs(value)

    def underperform_streak(self) -> int:
        minimum = float(self.targets.daily_minimum)
        streak = 0
        for item in reversed(self._days):
            if item.pnl < minimum:
                streak += 1
            else:
                break
        return streak

    def rolling_metrics(self) -> dict[str, float]:
        window = list(self._days)[-int(self.targets.lookback_days) :]
        rets = [item.pnl for item in window]
        n = len(rets)
        mean = sum(rets) / n if n else 0.0
        if n >= 2:
            var = sum((r - mean) ** 2 for r in rets) / (n - 1)
            std = sqrt(var) if var > 0 else 0.0
            sharpe = (mean / std) * sqrt(n) if std > 1e-12 else 0.0
        else:
            sharpe = 0.0
        trades = self._wins + self._losses
        win_rate = (self._wins / trades) if trades else 0.0
        pf = (
            self._gross_profit / self._gross_loss
            if self._gross_loss > 1e-12
            else (float("inf") if self._gross_profit > 0 else 0.0)
        )
        return {
            "days": float(n),
            "mean_daily_pnl": mean,
            "sharpe": float(sharpe if sharpe != float("inf") else 0.0),
            "win_rate": win_rate,
            "profit_factor": float(pf if pf != float("inf") else 999.0),
            "underperform_streak": float(self.underperform_streak()),
        }

    def needs_diagnostics(self) -> bool:
        return self.underperform_streak() >= int(self.targets.underperform_days)

    def classify(
        self,
        *,
        phantom_pnl: float | None = None,
        live_pnl: float | None = None,
        regime_changed: bool = False,
    ) -> Diagnosis:
        """todo2 §2.3–2.4: compare phantom vs live and classify."""
        if not self.needs_diagnostics() and phantom_pnl is None:
            diagnosis = Diagnosis(
                DiagnosisKind.HEALTHY,
                "performance within acceptable band",
            )
            self.last_diagnosis = diagnosis
            return diagnosis

        if regime_changed:
            diagnosis = Diagnosis(
                DiagnosisKind.REGIME_CHANGE,
                "market regime appears changed",
                actions=(
                    "switch_to_reserve_strategy",
                    "request_manual_review",
                ),
            )
        elif phantom_pnl is not None and live_pnl is not None:
            if phantom_pnl > live_pnl + abs(self.targets.daily_minimum):
                diagnosis = Diagnosis(
                    DiagnosisKind.PARAMETER_DRIFT,
                    "phantom outperforms live — parameters likely stale",
                    actions=(
                        "propose_bayesian_reopt",
                        "call_ai_diagnost",
                    ),
                    details={"phantom_pnl": phantom_pnl, "live_pnl": live_pnl},
                )
            elif phantom_pnl < self.targets.daily_minimum and live_pnl < self.targets.daily_minimum:
                diagnosis = Diagnosis(
                    DiagnosisKind.MARKET_WEAK,
                    "phantom and live both weak — likely market regime",
                    actions=("pause_risk_parity_rebalance", "call_ai_diagnost"),
                    details={"phantom_pnl": phantom_pnl, "live_pnl": live_pnl},
                )
            else:
                diagnosis = Diagnosis(
                    DiagnosisKind.STRUCTURAL_BREAK,
                    "live/phantom mismatch without clear edge",
                    actions=("disable_strategy", "request_manual_review"),
                    details={"phantom_pnl": phantom_pnl, "live_pnl": live_pnl},
                )
        else:
            diagnosis = Diagnosis(
                DiagnosisKind.PARAMETER_DRIFT,
                "underperformance streak — run phantom recheck",
                actions=("run_phantom_lookback_30d", "call_ai_diagnost"),
            )
        self.last_diagnosis = diagnosis
        return diagnosis

    def snapshot(self) -> dict[str, Any]:
        return {
            "targets": {
                "daily_target": self.targets.daily_target,
                "daily_minimum": self.targets.daily_minimum,
                "underperform_days": self.targets.underperform_days,
                "target_sharpe": self.targets.target_sharpe,
                "target_win_rate": self.targets.target_win_rate,
                "target_profit_factor": self.targets.target_profit_factor,
            },
            "metrics": self.rolling_metrics(),
            "needs_diagnostics": self.needs_diagnostics(),
            "last_diagnosis": (
                self.last_diagnosis.to_dict() if self.last_diagnosis else None
            ),
        }
