"""Ensemble / consilium coordinator (tech doc §3.2, §4.1, §4.3)."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Literal

WeightMode = Literal["equal", "manual", "risk_parity", "bandit_ucb"]


@dataclass(slots=True)
class StrategySignal:
    strategy_id: str
    symbol: str
    side: str  # buy | sell | flat
    strength: float = 1.0  # 0..1
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WeightedDecision:
    symbol: str
    side: str
    strength: float
    weights: dict[str, float]
    contributors: list[StrategySignal]


@dataclass(slots=True)
class StrategyStats:
    """Rolling per-strategy outcomes for adaptive weight modes."""

    window: int = 64
    min_vol: float = 1e-4
    _returns: dict[str, deque[float]] = field(default_factory=dict)
    _counts: dict[str, int] = field(default_factory=dict)
    _sum_returns: dict[str, float] = field(default_factory=dict)
    _total_steps: int = 0

    def ensure(self, strategy_ids: list[str]) -> None:
        for sid in strategy_ids:
            key = str(sid)
            if key not in self._returns:
                self._returns[key] = deque(maxlen=max(8, int(self.window)))
                self._counts.setdefault(key, 0)
                self._sum_returns.setdefault(key, 0.0)

    def record_outcome(self, strategy_id: str, pnl: float) -> None:
        sid = str(strategy_id or "")
        if not sid:
            return
        self.ensure([sid])
        ret = float(pnl)
        self._returns[sid].append(ret)
        self._counts[sid] = int(self._counts.get(sid, 0)) + 1
        self._sum_returns[sid] = float(self._sum_returns.get(sid, 0.0)) + ret
        self._total_steps += 1

    def mean_return(self, strategy_id: str) -> float:
        sid = str(strategy_id)
        n = int(self._counts.get(sid, 0))
        if n <= 0:
            return 0.0
        return float(self._sum_returns.get(sid, 0.0)) / n

    def count(self, strategy_id: str) -> int:
        """Outcomes recorded for a strategy.

        Public because a consumer must be able to tell "this member is losing"
        from "this member has barely traded" -- the two demand opposite
        responses, and the second is not a failure at all.
        """
        return int(self._counts.get(str(strategy_id), 0))

    def volatility(self, strategy_id: str) -> float:
        series = list(self._returns.get(str(strategy_id), ()))
        if len(series) < 2:
            return float(self.min_vol)
        mean = sum(series) / len(series)
        var = sum((x - mean) ** 2 for x in series) / max(1, len(series) - 1)
        return max(float(self.min_vol), math.sqrt(max(0.0, var)))

    def correlation(self, a: str, b: str) -> float | None:
        sa = list(self._returns.get(str(a), ()))
        sb = list(self._returns.get(str(b), ()))
        n = min(len(sa), len(sb))
        if n < 3:
            return None
        xa = sa[-n:]
        xb = sb[-n:]
        ma = sum(xa) / n
        mb = sum(xb) / n
        cov = sum((xa[i] - ma) * (xb[i] - mb) for i in range(n)) / max(1, n - 1)
        va = sum((x - ma) ** 2 for x in xa) / max(1, n - 1)
        vb = sum((x - mb) ** 2 for x in xb) / max(1, n - 1)
        denom = math.sqrt(max(0.0, va) * max(0.0, vb))
        if denom <= 1e-12:
            return None
        return max(-1.0, min(1.0, cov / denom))

    def snapshot(self, strategy_ids: list[str] | None = None) -> dict[str, Any]:
        ids = [str(x) for x in (strategy_ids or list(self._returns.keys()))]
        rows = {}
        for sid in ids:
            rows[sid] = {
                "count": int(self._counts.get(sid, 0)),
                "mean_return": self.mean_return(sid),
                "volatility": self.volatility(sid),
                "window": len(self._returns.get(sid, ())),
            }
        return {"total_steps": self._total_steps, "strategies": rows}


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    clipped = {k: max(0.0, float(v)) for k, v in weights.items()}
    total = sum(clipped.values())
    if total <= 1e-12:
        n = len(clipped) or 1
        return {k: 1.0 / n for k in clipped}
    return {k: v / total for k, v in clipped.items()}


def _apply_corr_dampen(
    weights: dict[str, float],
    stats: StrategyStats,
    *,
    corr_cap: float,
) -> dict[str, float]:
    """Cut weight of the weaker arm when pairwise |ρ| exceeds corr_cap."""
    out = dict(weights)
    ids = list(out.keys())
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            rho = stats.correlation(a, b)
            if rho is None or abs(rho) < float(corr_cap):
                continue
            # Prefer the higher mean-return arm; cut the other.
            if stats.mean_return(a) >= stats.mean_return(b):
                out[b] *= 0.5
            else:
                out[a] *= 0.5
    return _normalize(out)


class EnsembleManager:
    """
    Combines strategy signals with dynamic weights.

    Modes: equal | manual | risk_parity | bandit_ucb.
    """

    def __init__(self, strategy_ids: list[str] | None = None) -> None:
        self._weights: dict[str, float] = {}
        self.stats = StrategyStats()
        if strategy_ids:
            self.set_equal_weights(strategy_ids)

    def set_equal_weights(self, strategy_ids: list[str]) -> None:
        ids = [str(x) for x in strategy_ids if x]
        self.stats.ensure(ids)
        if not ids:
            self._weights = {}
            return
        w = 1.0 / len(ids)
        self._weights = {sid: w for sid in ids}

    def set_weights(self, weights: dict[str, float]) -> None:
        ids = [str(k) for k in weights.keys()]
        self.stats.ensure(ids)
        self._weights = _normalize({str(k): float(v) for k, v in weights.items()})

    @property
    def weights(self) -> dict[str, float]:
        return dict(self._weights)

    def rebalance(
        self,
        mode: str,
        strategy_ids: list[str],
        *,
        manual_weights: dict[str, float] | None = None,
        ucb_c: float = 1.0,
        corr_cap: float = 0.85,
        apply_corr: bool = True,
    ) -> dict[str, float]:
        """Compute and install weights for ``mode`` over ``strategy_ids``."""
        ids = [str(x) for x in strategy_ids if x]
        self.stats.ensure(ids)
        mode_l = str(mode or "equal").lower().strip()
        if mode_l in {"manual"} and manual_weights:
            base = {sid: float(manual_weights.get(sid, 0.0)) for sid in ids}
            weights = _normalize(base)
        elif mode_l in {"risk_parity", "risk-parity", "rp"}:
            vols = {sid: self.stats.volatility(sid) for sid in ids}
            weights = self.risk_parity_weights(vols)
            # Keep only lineup ids.
            weights = _normalize({sid: float(weights.get(sid, 0.0)) for sid in ids})
        elif mode_l in {"bandit_ucb", "ucb", "bandit"}:
            means = {sid: self.stats.mean_return(sid) for sid in ids}
            counts = {sid: int(self.stats._counts.get(sid, 0)) for sid in ids}  # noqa: SLF001
            scores = self.ucb_scores(
                means,
                counts,
                self.stats._total_steps,  # noqa: SLF001
                c=float(ucb_c),
            )
            # Softmax-ish via shifted positive scores → capital allocation.
            shifted = {
                sid: math.exp(max(-20.0, min(20.0, float(scores.get(sid, 0.0)))))
                for sid in ids
            }
            weights = _normalize(shifted)
        else:
            # equal (default)
            w = 1.0 / max(1, len(ids))
            weights = {sid: w for sid in ids}

        if apply_corr and mode_l not in {"equal", "manual"} and len(ids) > 1:
            weights = _apply_corr_dampen(weights, self.stats, corr_cap=float(corr_cap))

        self._weights = weights
        return dict(self._weights)

    def aggregate(self, signals: list[StrategySignal]) -> list[WeightedDecision]:
        by_symbol: dict[str, list[StrategySignal]] = defaultdict(list)
        for sig in signals:
            by_symbol[sig.symbol].append(sig)

        out: list[WeightedDecision] = []
        for symbol, group in by_symbol.items():
            score = 0.0
            used: dict[str, float] = {}
            for sig in group:
                w = self._weights.get(sig.strategy_id, 0.0)
                direction = 1.0 if sig.side == "buy" else -1.0 if sig.side == "sell" else 0.0
                score += w * direction * max(0.0, min(1.0, sig.strength))
                used[sig.strategy_id] = w
            if abs(score) < 1e-9:
                side = "flat"
            elif score > 0:
                side = "buy"
            else:
                side = "sell"
            out.append(
                WeightedDecision(
                    symbol=symbol,
                    side=side,
                    strength=min(1.0, abs(score)),
                    weights=used,
                    contributors=group,
                )
            )
        return out

    @staticmethod
    def risk_parity_weights(volatilities: dict[str, float]) -> dict[str, float]:
        """w_i = (1/σ_i) / Σ(1/σ_j)."""
        inv = {k: (1.0 / v if v > 1e-12 else 0.0) for k, v in volatilities.items()}
        total = sum(inv.values()) or 1.0
        return {k: v / total for k, v in inv.items()}

    @staticmethod
    def ucb_scores(
        mean_returns: dict[str, float],
        counts: dict[str, int],
        total_steps: int,
        *,
        c: float = 1.0,
    ) -> dict[str, float]:
        """UCB1 scores for bandit allocation (tech doc §4.1)."""
        t = max(1, total_steps)
        scores: dict[str, float] = {}
        for arm, q in mean_returns.items():
            n = max(1, counts.get(arm, 0))
            scores[arm] = q + c * math.sqrt(math.log(t) / n)
        return scores
