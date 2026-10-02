"""Why did it trade? Nothing here can answer that today.

The trade journal writes outcomes -- symbol, side, price, fee. That is what an
accountant needs and it is exactly what an operator does *not* need when a
strategy starts bleeding: the journal has no record of the reasoning, so the
question "what was the input" cannot be answered after the CandleStore ring
buffer has rolled the bar away.

Worse, the journal only sees fills. The overwhelming majority of decisions do not
fill -- including every `observe_only` signal, which is the entire evidence base
for a strategy on probation. Those leave no trace at all, so the base rate of
"what would it have done" is unrecoverable.

Features are already computed. `StrategyDecision.metrics` carries them per bar;
they travel one hop in `params_snapshot` and are then dropped on the floor in the
ensemble service, which keeps only quantity/price/class_code/bar_ts. This module
is the place they are kept instead.

On retention and honesty about it
---------------------------------
Recording every decision on every bar for every strategy is a lot of JSONL, and
a feature store nobody can afford to run is a feature store nobody runs. So:

* non-HOLD actions are recorded in full -- these are the decisions that mattered
* HOLDs are sampled at a configurable rate -- individually uninteresting, but the
  base rate is what turns "it traded 400 times" into "it considered 40,000 bars
  and declined 39,600"

`recorded` and `sampled` are counted separately so a reader can tell a genuine
absence from a sampled one. A store that silently drops records and cannot say so
is the same failure shape as the fee reconciler that never ran.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Mapping
from uuid import uuid4

#: Features are arbitrary strategy-supplied dicts. Only numbers can be profiled,
#: and a NaN silently poisons a min/max for the rest of the process lifetime.
def _numeric_only(features: Mapping[str, Any] | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in (features or {}).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = float(value)
        if math.isfinite(number):
            out[str(key)] = number
    return out


@dataclass(slots=True)
class DecisionRecord:
    """One strategy decision and the features that produced it."""

    decision_id: str
    ts: float
    symbol: str
    strategy_id: str
    action: str
    strength: float
    reason: str
    features: dict[str, float] = field(default_factory=dict)
    observe_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "ts": self.ts,
            "symbol": self.symbol,
            "strategy_id": self.strategy_id,
            "action": self.action,
            "strength": self.strength,
            "reason": self.reason,
            "features": dict(self.features),
            "observe_only": self.observe_only,
        }


@dataclass(slots=True)
class FeatureProfile:
    """Observed range of one feature, used as the validated envelope."""

    feature: str
    count: int = 0
    minimum: float = 0.0
    maximum: float = 0.0
    mean: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "count": self.count,
            "min": self.minimum,
            "max": self.maximum,
            "mean": self.mean,
        }

    def contains(self, value: float, *, tolerance: float = 0.0) -> bool:
        slack = tolerance * max(1.0, abs(self.maximum - self.minimum))
        return (self.minimum - slack) <= value <= (self.maximum + slack)


class DecisionStore:
    """Append-only decision log with a drift reader.

    `features()`/`drift_against()` exist so this is not another number written and
    read by nobody: the envelope a strategy was validated on is worth recording
    precisely so that operating outside it can be detected.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        hold_sample_rate: float = 0.05,
        max_rows: int = 200_000,
    ) -> None:
        self.path = Path(path) if path is not None else None
        self.hold_sample_rate = max(0.0, min(1.0, float(hold_sample_rate)))
        self.max_rows = max(1, int(max_rows))
        self._rows: list[DecisionRecord] = []
        self._lock = RLock()
        #: Counts kept separately so "we decided nothing" and "we sampled it away"
        #: remain distinguishable.
        self.recorded = 0
        self.sampled = 0
        self.dropped_after_cap = 0
        self._held_seen = 0

    # -- writing ---------------------------------------------------------

    def _keep(self, action: str) -> bool:
        """Holds are sampled; anything that moved is kept."""
        if action != "HOLD":
            return True
        self._held_seen += 1
        if self.hold_sample_rate <= 0.0:
            self.sampled += 1
            return False
        # Deterministic thinning on the HOLD counter: no RNG, so a replay of the
        # same bar sequence samples exactly the same rows.
        keep_every = max(1, int(round(1.0 / self.hold_sample_rate)))
        if self._held_seen % keep_every == 0:
            return True
        self.sampled += 1
        return False

    def record(
        self,
        *,
        symbol: str,
        strategy_id: str,
        action: str,
        strength: float = 0.0,
        reason: str = "",
        features: Mapping[str, Any] | None = None,
        observe_only: bool = False,
        ts: float | None = None,
    ) -> DecisionRecord | None:
        action_u = str(action or "HOLD").upper()
        with self._lock:
            if not self._keep(action_u):
                return None
            if len(self._rows) >= self.max_rows:
                # Keep the ring bounded, and say so rather than pretending the
                # store is complete.
                self._rows.pop(0)
                self.dropped_after_cap += 1
            entry = DecisionRecord(
                decision_id=uuid4().hex,
                ts=float(ts if ts is not None else time.time()),
                symbol=str(symbol or ""),
                strategy_id=str(strategy_id or ""),
                action=action_u,
                strength=float(strength or 0.0),
                reason=str(reason or ""),
                features=_numeric_only(features),
                observe_only=bool(observe_only),
            )
            self._rows.append(entry)
            self.recorded += 1
            if self.path is not None:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with self.path.open("a", encoding="utf-8") as stream:
                        stream.write(
                            json.dumps(entry.to_dict(), sort_keys=True) + "\n"
                        )
                except OSError:
                    # A decision log must never be the reason trading stops.
                    pass
            return entry

    # -- reading ---------------------------------------------------------

    def rows(
        self, *, strategy_id: str | None = None, since: float | None = None
    ) -> list[DecisionRecord]:
        with self._lock:
            out = list(self._rows)
        if strategy_id is not None:
            out = [r for r in out if r.strategy_id == str(strategy_id)]
        if since is not None:
            out = [r for r in out if r.ts >= float(since)]
        return out

    def feature_profile(
        self, *, strategy_id: str | None = None, since: float | None = None
    ) -> dict[str, FeatureProfile]:
        """Min/max/mean/count per feature. The validated envelope."""
        buckets: dict[str, list[float]] = {}
        counts: dict[str, int] = {}
        sums: dict[str, float] = {}
        for row in self.rows(strategy_id=strategy_id, since=since):
            for name, value in row.features.items():
                buckets.setdefault(name, []).append(value)
                counts[name] = counts.get(name, 0) + 1
                sums[name] = sums.get(name, 0.0) + value
        out: dict[str, FeatureProfile] = {}
        for name, values in buckets.items():
            out[name] = FeatureProfile(
                feature=name,
                count=counts[name],
                minimum=min(values),
                maximum=max(values),
                mean=sums[name] / len(values),
            )
        return out

    def freeze_baseline(
        self, *, strategy_id: str | None = None, since: float | None = None
    ) -> dict[str, dict[str, Any]]:
        """A durable, serialisable envelope to check future decisions against."""
        return {
            name: profile.to_dict()
            for name, profile in self.feature_profile(
                strategy_id=strategy_id, since=since
            ).items()
        }

    def drift_against(
        self,
        baseline: Mapping[str, Mapping[str, Any]],
        *,
        strategy_id: str | None = None,
        since: float | None = None,
        tolerance: float = 0.1,
    ) -> list[dict[str, Any]]:
        """Live features sitting outside the envelope they were validated on.

        Returning findings rather than a boolean: an operator needs to know
        *which* feature drifted and by how much. An empty list means either no
        drift or no baseline -- callers must check `baseline` is non-empty before
        reading silence as comfort.
        """
        if not baseline:
            return []
        live = self.feature_profile(strategy_id=strategy_id, since=since)
        findings: list[dict[str, Any]] = []
        for name, current in live.items():
            ref = baseline.get(name)
            if ref is None:
                continue
            envelope = FeatureProfile(
                feature=name,
                count=int(ref.get("count", 0)),
                minimum=float(ref.get("min", 0.0)),
                maximum=float(ref.get("max", 0.0)),
                mean=float(ref.get("mean", 0.0)),
            )
            outside = [
                value
                for value in (
                    current.minimum if current.minimum < envelope.minimum else None,
                    current.maximum if current.maximum > envelope.maximum else None,
                )
                if value is not None
            ]
            if not outside:
                continue
            findings.append(
                {
                    "feature": name,
                    "baseline_min": envelope.minimum,
                    "baseline_max": envelope.maximum,
                    "live_min": current.minimum,
                    "live_max": current.maximum,
                    "exceeded": [float(v) for v in outside],
                    "tolerance": float(tolerance),
                    "meaning": "live values fall outside the validated envelope",
                }
            )
        return findings

    def stats(self) -> dict[str, Any]:
        """Row counts, including what was sampled away and what was capped."""
        with self._lock:
            return {
                "recorded": self.recorded,
                "sampled_away": self.sampled,
                "held_seen": self._held_seen,
                "dropped_after_cap": self.dropped_after_cap,
                "in_memory_rows": len(self._rows),
                "hold_sample_rate": self.hold_sample_rate,
                "max_rows": self.max_rows,
                "persisted": self.path is not None,
                "path": str(self.path) if self.path is not None else "",
                "note": (
                    "sampled_away rows were never written; a base rate computed "
                    "from recorded rows alone overstates activity"
                ),
            }


def default_store_path(root: str | Path) -> Path:
    """Where the store lives when the caller did not say."""
    return Path(root) / "ops" / "decisions.jsonl"


def store_bytes(path: str | Path | None) -> int:
    """Size on disk, or 0. Used by retention checks."""
    if path is None:
        return 0
    try:
        return int(os.path.getsize(Path(path)))
    except OSError:
        return 0