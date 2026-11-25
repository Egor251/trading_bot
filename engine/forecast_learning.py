"""Fast price-forecast learner (R-62).

Observes top-of-book mids as often as the venue allows, emits a forecast of the
log-price at a fixed horizon, and scores it by distance to the realised future
price. Observe-only: no execution, no live-lineup mutation.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ForecastConfig:
    horizon_sec: float = 300.0
    poll_sec: float = 5.0
    lags: int = 6
    min_observations: int = 200
    update_every: int = 60
    window_bars: int = 600
    seed: int = 62


@dataclass(slots=True)
class ForecastLearner:
    cfg: ForecastConfig = field(default_factory=ForecastConfig)
    features: list[list[float]] = field(default_factory=list)
    targets: list[float] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    _mids: deque[float] = field(init=False, repr=False, default_factory=deque)
    _pending: deque[tuple[float, list[float], float]] = field(
        init=False, repr=False, default_factory=deque
    )
    _last_ts: float = field(init=False, repr=False, default_factory=lambda: float("-inf"))

    def __post_init__(self) -> None:
        self._mids = deque(maxlen=self.cfg.window_bars + self.cfg.lags)

    def observe(self, ts: float, mid: float, spread_bps: float | None,
                imbalance: float | None) -> None:
        if not (ts > self._last_ts) or mid <= 0:
            return  # duplicate/out-of-order tick cannot move the state
        self._last_ts = float(ts)
        self._mids.append(float(mid))
        lags = list(self._mids)[-self.cfg.lags:]
        if len(lags) < self.cfg.lags:
            return
        row = [lags[i] / lags[i - 1] - 1.0 for i in range(1, len(lags))]
        row.extend([
            float(spread_bps) if spread_bps is not None else 0.0,
            float(imbalance) if imbalance is not None else 0.0,
            1.0 if spread_bps is not None and imbalance is not None else 0.0,
        ])
        self._pending.append((float(ts) + self.cfg.horizon_sec, row, float(mid)))

        # Resolve forecasts whose horizon has arrived, using the current mid.
        while self._pending and self._pending[0][0] <= ts:
            _due, frow, mid0 = self._pending.popleft()
            self.features.append(frow)
            self.targets.append(float(mid) / mid0 - 1.0)

    def step(self, optimizer: Any) -> dict[str, Any] | None:
        n = len(self.targets)
        if n < self.cfg.min_observations or n % self.cfg.update_every != 0:
            return None
        start = max(0, n - self.cfg.window_bars)
        X = self.features[start:]
        y = self.targets[start:]
        m = len(X)
        tr = list(range(0, int(m * 0.70)))
        va = list(range(int(m * 0.70), int(m * 0.85)))
        te = list(range(int(m * 0.85), m))
        result = optimizer.run(X, y, tr, va, te, self.cfg.seed)
        d = {
            "observe_only": True,
            "action": "FORECAST",
            "forecast_log_return": float(result.get("pred", 0.0)),
            "loss": float(result.get("loss", float("inf"))),
            "baseline_loss": float(result.get("baseline", float("inf"))),
            "observations": n,
            "horizon_sec": self.cfg.horizon_sec,
            "rationale": "price forecast scored by distance; no execution path",
        }
        self.decisions.append(d)
        return d

    def snapshot(self) -> Mapping[str, Any]:
        return {
            "observations": len(self.targets),
            "pending": len(self._pending),
            "decisions": len(self.decisions),
            "observe_only": True,
        }
