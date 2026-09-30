"""Cost-aware adaptive strategy capital allocation.

This module produces sizing proposals only.  It intentionally knows nothing
about signals, orders or execution, so an allocator score cannot bypass the
normal trading and risk path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import fmean, stdev
from typing import Iterable, Mapping


class AllocationError(ValueError):
    """Raised for invalid observations or infeasible allocation constraints."""


@dataclass(frozen=True, slots=True)
class AllocatorConfig:
    method: str = "risk_parity"  # risk_parity | ucb | blended
    lookback: int = 60
    min_observations: int = 2
    exploration: float = 1.0
    ucb_temperature: float = 1.0
    correlation_threshold: float = 0.6
    correlation_penalty: float = 1.0
    max_turnover: float = 0.20
    max_weight_delta: float = 0.10
    hysteresis: float = 0.01
    strategy_caps: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        method = self.method.strip().lower()
        if method not in {"risk_parity", "ucb", "blended"}:
            raise AllocationError("method must be risk_parity, ucb, or blended")
        object.__setattr__(self, "method", method)
        if self.lookback < 2 or self.min_observations < 1:
            raise AllocationError("lookback and min_observations must be positive")
        if self.exploration < 0 or self.ucb_temperature <= 0:
            raise AllocationError("invalid UCB configuration")
        if not 0 <= self.correlation_threshold <= 1:
            raise AllocationError("correlation_threshold must be in [0, 1]")
        if self.correlation_penalty < 0:
            raise AllocationError("correlation_penalty must not be negative")
        for name in ("max_turnover", "max_weight_delta", "hysteresis"):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise AllocationError(f"{name} must be in [0, 1]")
        caps = {str(k): float(v) for k, v in self.strategy_caps.items()}
        if any(not math.isfinite(v) or not 0 <= v <= 1 for v in caps.values()):
            raise AllocationError("strategy caps must be finite values in [0, 1]")
        object.__setattr__(self, "strategy_caps", caps)


@dataclass(frozen=True, slots=True)
class SizingProposal:
    strategy_id: str
    current_weight: float
    proposed_weight: float
    delta: float
    score: float
    reason: str


@dataclass(frozen=True, slots=True)
class AllocationProposal:
    weights: dict[str, float]
    proposals: tuple[SizingProposal, ...]
    turnover: float
    changed: bool
    method: str


def _normalise(values: Mapping[str, float], strategy_ids: Iterable[str]) -> dict[str, float]:
    ids = tuple(strategy_ids)
    clean = {sid: max(0.0, float(values.get(sid, 0.0))) for sid in ids}
    if any(not math.isfinite(value) for value in clean.values()):
        raise AllocationError("weights must be finite")
    total = sum(clean.values())
    if total <= 0:
        return {sid: 1.0 / len(ids) for sid in ids}
    return {sid: value / total for sid, value in clean.items()}


def _correlation(left: list[float], right: list[float]) -> float:
    length = min(len(left), len(right))
    if length < 2:
        return 0.0
    x, y = left[-length:], right[-length:]
    mean_x, mean_y = fmean(x), fmean(y)
    numerator = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
    sum_x = sum((a - mean_x) ** 2 for a in x)
    sum_y = sum((b - mean_y) ** 2 for b in y)
    denominator = math.sqrt(sum_x * sum_y)
    return numerator / denominator if denominator > 1e-18 else 0.0


def _project_bounded_simplex(
    target: Mapping[str, float],
    lower: Mapping[str, float],
    upper: Mapping[str, float],
) -> dict[str, float]:
    ids = tuple(target)
    if sum(lower.values()) > 1.0 + 1e-12 or sum(upper.values()) < 1.0 - 1e-12:
        raise AllocationError("allocation constraints are infeasible")
    low_lambda, high_lambda = -2.0, 2.0
    for _ in range(100):
        midpoint = (low_lambda + high_lambda) / 2
        total = sum(
            min(upper[sid], max(lower[sid], target[sid] + midpoint))
            for sid in ids
        )
        if total < 1.0:
            low_lambda = midpoint
        else:
            high_lambda = midpoint
    shift = (low_lambda + high_lambda) / 2
    result = {
        sid: min(upper[sid], max(lower[sid], target[sid] + shift))
        for sid in ids
    }
    # Remove floating residue without violating bounds.
    residue = 1.0 - sum(result.values())
    if abs(residue) > 1e-12:
        candidates = [
            sid
            for sid in ids
            if (residue > 0 and result[sid] < upper[sid])
            or (residue < 0 and result[sid] > lower[sid])
        ]
        if candidates:
            sid = candidates[0]
            result[sid] += residue
    return result


class AdaptiveAllocator:
    """Risk-parity/UCB allocator with hard portfolio-level guardrails."""

    def __init__(
        self,
        strategy_ids: Iterable[str],
        config: AllocatorConfig | None = None,
        *,
        initial_weights: Mapping[str, float] | None = None,
    ) -> None:
        ids = tuple(dict.fromkeys(str(item).strip() for item in strategy_ids))
        if not ids or any(not item for item in ids):
            raise AllocationError("at least one non-empty strategy id is required")
        self.strategy_ids = ids
        self.config = config or AllocatorConfig()
        unknown_caps = set(self.config.strategy_caps) - set(ids)
        if unknown_caps:
            raise AllocationError(f"caps contain unknown strategies: {unknown_caps}")
        self._caps = {
            sid: float(self.config.strategy_caps.get(sid, 1.0)) for sid in ids
        }
        if sum(self._caps.values()) < 1.0 - 1e-12:
            raise AllocationError("strategy caps cannot hold a fully invested portfolio")
        equal = {sid: 1.0 / len(ids) for sid in ids}
        starting = _normalise(initial_weights or equal, ids)
        self._weights = _project_bounded_simplex(
            starting, {sid: 0.0 for sid in ids}, self._caps
        )
        self._net_returns: dict[str, list[float]] = {sid: [] for sid in ids}
        self._total_observations = 0

    @property
    def weights(self) -> dict[str, float]:
        return dict(self._weights)

    @property
    def net_returns(self) -> dict[str, tuple[float, ...]]:
        return {sid: tuple(values) for sid, values in self._net_returns.items()}

    def record_net_return(self, strategy_id: str, net_return: float) -> None:
        """Record a return after all commissions, funding and borrow costs."""
        if strategy_id not in self._net_returns:
            raise AllocationError(f"unknown strategy: {strategy_id}")
        value = float(net_return)
        if not math.isfinite(value):
            raise AllocationError("net_return must be finite")
        history = self._net_returns[strategy_id]
        history.append(value)
        del history[: max(0, len(history) - self.config.lookback)]
        self._total_observations += 1

    observe_net_return = record_net_return

    def update_net_returns(self, observations: Mapping[str, float]) -> None:
        for strategy_id, net_return in observations.items():
            self.record_net_return(strategy_id, net_return)

    def _risk_parity(self) -> tuple[dict[str, float], dict[str, float]]:
        volatilities: dict[str, float] = {}
        for sid, history in self._net_returns.items():
            if len(history) < self.config.min_observations:
                volatilities[sid] = 1.0
            else:
                volatilities[sid] = max(stdev(history), 1e-9)
        scores = {sid: 1.0 / volatility for sid, volatility in volatilities.items()}
        return _normalise(scores, self.strategy_ids), scores

    def _ucb(self) -> tuple[dict[str, float], dict[str, float]]:
        total = max(2, self._total_observations)
        scores: dict[str, float] = {}
        for sid, history in self._net_returns.items():
            count = len(history)
            if count == 0:
                scores[sid] = self.config.exploration * math.sqrt(math.log(total))
            else:
                scores[sid] = fmean(history) + self.config.exploration * math.sqrt(
                    math.log(total) / count
                )
        maximum = max(scores.values())
        exponentials = {
            sid: math.exp(
                max(-700.0, (score - maximum) / self.config.ucb_temperature)
            )
            for sid, score in scores.items()
        }
        return _normalise(exponentials, self.strategy_ids), scores

    def correlation_matrix(self) -> dict[str, dict[str, float]]:
        return {
            left: {
                right: (
                    1.0
                    if left == right
                    else _correlation(
                        self._net_returns[left], self._net_returns[right]
                    )
                )
                for right in self.strategy_ids
            }
            for left in self.strategy_ids
        }

    def _correlation_adjust(
        self, target: Mapping[str, float]
    ) -> dict[str, float]:
        matrix = self.correlation_matrix()
        adjusted: dict[str, float] = {}
        for sid in self.strategy_ids:
            excess = [
                max(0.0, matrix[sid][other] - self.config.correlation_threshold)
                for other in self.strategy_ids
                if other != sid and target[other] > 0
            ]
            crowding = fmean(excess) if excess else 0.0
            adjusted[sid] = target[sid] / (
                1.0 + self.config.correlation_penalty * crowding
            )
        return _normalise(adjusted, self.strategy_ids)

    def _unconstrained_target(self) -> tuple[dict[str, float], dict[str, float]]:
        risk_weights, risk_scores = self._risk_parity()
        if self.config.method == "risk_parity":
            return risk_weights, risk_scores
        ucb_weights, ucb_scores = self._ucb()
        if self.config.method == "ucb":
            return ucb_weights, ucb_scores
        target = {
            sid: math.sqrt(risk_weights[sid] * ucb_weights[sid])
            for sid in self.strategy_ids
        }
        scores = {
            sid: 0.5 * risk_scores[sid] + 0.5 * ucb_scores[sid]
            for sid in self.strategy_ids
        }
        return _normalise(target, self.strategy_ids), scores

    def propose(
        self, current_weights: Mapping[str, float] | None = None
    ) -> AllocationProposal:
        """Return bounded sizing proposals without changing live allocation."""
        current = _normalise(current_weights or self._weights, self.strategy_ids)
        target, scores = self._unconstrained_target()
        target = self._correlation_adjust(target)

        delta_limit = self.config.max_weight_delta
        lower = {
            sid: max(0.0, current[sid] - delta_limit)
            for sid in self.strategy_ids
        }
        upper = {
            sid: min(self._caps[sid], current[sid] + delta_limit)
            for sid in self.strategy_ids
        }
        constrained = _project_bounded_simplex(target, lower, upper)

        turnover = 0.5 * sum(
            abs(constrained[sid] - current[sid]) for sid in self.strategy_ids
        )
        if turnover > self.config.max_turnover and turnover > 0:
            scale = self.config.max_turnover / turnover
            constrained = {
                sid: current[sid] + scale * (constrained[sid] - current[sid])
                for sid in self.strategy_ids
            }
            turnover = self.config.max_turnover

        largest_change = max(
            abs(constrained[sid] - current[sid]) for sid in self.strategy_ids
        )
        if turnover < self.config.hysteresis or largest_change < self.config.hysteresis:
            constrained = current
            turnover = 0.0
            changed = False
            reason = "held by hysteresis"
        else:
            changed = turnover > 1e-12
            reason = "bounded adaptive sizing"

        proposals = tuple(
            SizingProposal(
                strategy_id=sid,
                current_weight=current[sid],
                proposed_weight=constrained[sid],
                delta=constrained[sid] - current[sid],
                score=scores[sid],
                reason=reason,
            )
            for sid in self.strategy_ids
        )
        return AllocationProposal(
            weights=dict(constrained),
            proposals=proposals,
            turnover=turnover,
            changed=changed,
            method=self.config.method,
        )

    def accept(self, proposal: AllocationProposal) -> None:
        """Commit an accepted sizing proposal; this still submits no orders."""
        if set(proposal.weights) != set(self.strategy_ids):
            raise AllocationError("proposal strategies do not match allocator")
        weights = _normalise(proposal.weights, self.strategy_ids)
        if any(weights[sid] > self._caps[sid] + 1e-12 for sid in self.strategy_ids):
            raise AllocationError("proposal exceeds a strategy cap")
        self._weights = weights

    def rebalance(self) -> AllocationProposal:
        """Compatibility spelling: propose, but never execute, a rebalance."""
        return self.propose()


AdaptiveConsilium = AdaptiveAllocator
AdaptiveConsiliumAllocator = AdaptiveAllocator

__all__ = [
    "AdaptiveAllocator",
    "AdaptiveConsilium",
    "AdaptiveConsiliumAllocator",
    "AllocationError",
    "AllocationProposal",
    "AllocatorConfig",
    "SizingProposal",
]
