"""Per-strategy circuit breaker for a live lineup.

A strategy that has structurally degraded keeps trading. The system already
detects this — `PerformanceMonitor` classifies `parameter_drift` and
`structural_break` — but the only response is soft: `risk_scale` drops to 0.5
and negative-mean members have their weight multiplied by 0.25. A member at 0.25
weight is still a member, so it still trades, indefinitely. Worse, the diagnosis
is a *portfolio* judgement, so it cannot reliably attribute the degradation to a
single strategy.

This module is the missing actuator. It judges each lineup member on its own
realised outcomes and can demote it out of the live lineup while leaving it
observable, so evidence keeps accumulating and the decision stays reversible.

Three properties matter more than the threshold itself:

**Never judge on a thin sample.** Two losing trades are noise, not a structural
break. A member is skipped entirely until it has `min_samples` observations.

**Fail open on missing data.** A strategy with no recorded outcome is
unevaluated, not failing. Absent evidence is not evidence of degradation, and a
breaker that quarantines on silence would take out every strategy the moment the
outcome feed broke.

**Hysteresis.** Quarantine triggers at `quarantine_below` and clears only at
`restore_above`, which sits strictly above it. With one threshold a member
sitting exactly on the line flips in and out every evaluation, and a flapping
lineup is worse than either extreme.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


class OutcomeSource(Protocol):
    """The slice of `StrategyStats` this module needs."""

    def mean_return(self, strategy_id: str) -> float: ...

    def count(self, strategy_id: str) -> int: ...


@dataclass(frozen=True)
class QuarantinePolicy:
    """Thresholds for the breaker. Every field is a deliberate operator choice."""

    enabled: bool = True
    #: A member below this mean outcome is a quarantine candidate.
    quarantine_below: float = 0.0
    #: ...and stays out until it reaches this. Must exceed `quarantine_below`.
    restore_above: float = 0.0
    #: Consecutive failing evaluations before acting. 1 flaps; 3 reacts slowly.
    consecutive_failures: int = 3
    #: Observations required before a member may be judged at all.
    min_samples: int = 20

    def validated(self) -> "QuarantinePolicy":
        """Reject a policy whose own numbers cannot mean anything.

        A policy where `restore_above <= quarantine_below` can never restore a
        member that was legitimately borderline, which is a silent permanent
        lockout rather than a safety feature.
        """
        if self.consecutive_failures < 1:
            raise ValueError("consecutive_failures must be at least 1")
        if self.min_samples < 1:
            raise ValueError("min_samples must be at least 1")
        if self.enabled and self.restore_above <= self.quarantine_below:
            raise ValueError(
                f"restore_above ({self.restore_above}) must exceed "
                f"quarantine_below ({self.quarantine_below}); otherwise a "
                "borderline member can never be restored"
            )
        return self


@dataclass
class QuarantineVerdict:
    strategy_id: str
    quarantined: bool
    reason: str = ""
    mean_return: float | None = None
    samples: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "quarantined": self.quarantined,
            "reason": self.reason,
            "mean_return": self.mean_return,
            "samples": self.samples,
        }


@dataclass
class StrategyQuarantine:
    """Tracks quarantine state across evaluations.

    State is per-strategy and persistent in memory only: on restart a member is
    judged fresh, which is the correct default. A breaker whose state outlived
    the process that produced the evidence would quarantine on stale grounds.
    """

    policy: QuarantinePolicy = field(default_factory=QuarantinePolicy)
    _streaks: dict[str, int] = field(default_factory=dict)
    _quarantined: set[str] = field(default_factory=set)

    @property
    def quarantined(self) -> set[str]:
        return set(self._quarantined)

    def is_quarantined(self, strategy_id: str) -> bool:
        return str(strategy_id) in self._quarantined

    def reset(self, strategy_id: str | None = None) -> None:
        if strategy_id is None:
            self._streaks.clear()
            self._quarantined.clear()
        else:
            sid = str(strategy_id)
            self._streaks.pop(sid, None)
            self._quarantined.discard(sid)

    def evaluate(
        self,
        strategy_ids: list[str],
        outcomes: OutcomeSource | Mapping[str, tuple[float, int]],
    ) -> list[QuarantineVerdict]:
        """Evaluate every member and return what changed.

        The return value is the full verdict list, not just transitions, so a
        caller can surface the reasoning in the UI rather than only the effect.
        """
        policy = self.policy
        if not policy.enabled:
            return [
                QuarantineVerdict(str(sid), False, "breaker_disabled")
                for sid in strategy_ids
            ]

        def _lookup(sid: str) -> tuple[float | None, int]:
            if isinstance(outcomes, Mapping):
                value = outcomes.get(sid)
                if value is None:
                    return None, 0
                return float(value[0]), int(value[1])
            return (
                float(outcomes.mean_return(sid)),
                int(outcomes.count(sid)),
            )

        verdicts: list[QuarantineVerdict] = []
        for sid in strategy_ids:
            sid = str(sid)
            mean, samples = _lookup(sid)

            if mean is None or samples < policy.min_samples:
                # Not enough evidence to judge. Do not count it as a failure and
                # do not touch existing state — silence is not degradation.
                verdicts.append(
                    QuarantineVerdict(
                        sid,
                        self.is_quarantined(sid),
                        "insufficient_samples",
                        mean,
                        samples,
                    )
                )
                continue

            if self.is_quarantined(sid):
                if mean >= policy.restore_above:
                    self._quarantined.discard(sid)
                    self._streaks.pop(sid, None)
                    verdicts.append(
                        QuarantineVerdict(sid, False, "recovered", mean, samples)
                    )
                else:
                    verdicts.append(
                        QuarantineVerdict(sid, True, "still_underperforming", mean, samples)
                    )
                continue

            if mean < policy.quarantine_below:
                streak = int(self._streaks.get(sid, 0)) + 1
                self._streaks[sid] = streak
                if streak >= policy.consecutive_failures:
                    self._quarantined.add(sid)
                    self._streaks.pop(sid, None)
                    verdicts.append(
                        QuarantineVerdict(sid, True, "degraded", mean, samples)
                    )
                else:
                    verdicts.append(
                        QuarantineVerdict(
                            sid,
                            False,
                            f"below_threshold {streak}/{policy.consecutive_failures}",
                            mean,
                            samples,
                        )
                    )
            else:
                if sid in self._streaks:
                    self._streaks.pop(sid, None)
                verdicts.append(
                    QuarantineVerdict(sid, False, "healthy", mean, samples)
                )
        return verdicts

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.policy.enabled),
            "quarantine_below": float(self.policy.quarantine_below),
            "restore_above": float(self.policy.restore_above),
            "consecutive_failures": int(self.policy.consecutive_failures),
            "min_samples": int(self.policy.min_samples),
            "quarantined": sorted(self._quarantined),
            "pending": dict(self._streaks),
        }