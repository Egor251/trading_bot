"""Observe-only online-learning core (R-61).

Pure state machine: bars and optional book snapshots go in, a deterministic
decision record comes out. No I/O, no execution path, no live-lineup mutation.
`tradable` cannot become true before the frozen observation floor and a costed
held-out check; even then the record is tagged observe_only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class OnlineConfig:
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    lags: int = 6
    window_bars: int = 600
    update_every: int = 60
    min_observations: int = 200
    fee_bps: float = 7.0
    acc_bar: float = 0.53
    seed: int = 61


@dataclass(frozen=True, slots=True)
class LiveBar:
    ts: float
    symbol: str
    close: float
    book_spread_bps: float | None = None
    book_imbalance: float | None = None

    def __post_init__(self) -> None:
        if self.close <= 0:
            raise ValueError("close must be positive")


@dataclass(slots=True)
class OnlineLearner:
    cfg: OnlineConfig = field(default_factory=OnlineConfig)
    features: list[list[float]] = field(default_factory=list)
    targets: list[int] = field(default_factory=list)
    returns: list[float] = field(default_factory=list)
    bars_seen: int = 0
    last_update: int = 0
    tradable: bool = False
    decisions: list[dict[str, Any]] = field(default_factory=list)
    _closes: dict[str, list[float]] = field(init=False, repr=False, default_factory=dict)
    _pending: dict[str, list[float]] = field(init=False, repr=False, default_factory=dict)
    _latest: dict[str, float] = field(init=False, repr=False, default_factory=dict)

    def __post_init__(self) -> None:
        self._closes = {s: [] for s in self.cfg.symbols}
        self._pending = {}
        self._latest = {}

    def observe(self, bar: LiveBar) -> None:
        """Ingest one live bar. Targets are filled when the NEXT BTC bar arrives."""
        if bar.symbol not in self._closes:
            return
        closes = self._closes[bar.symbol]
        if closes and float(bar.ts) <= self._latest.get(bar.symbol, float("-inf")):
            return  # out-of-order replay cannot move the state machine
        closes.append(float(bar.close))
        self._latest[bar.symbol] = float(bar.ts)
        self.bars_seen += 1

        # Finalize the previous BTC pending row: its target is this bar's return.
        if bar.symbol == self.cfg.symbols[0] and self._pending:
            prev_close = closes[-2]
            rets = [float(bar.close) / prev_close - 1.0]
            self.returns.append(rets[0])
            self.targets.append(int(rets[0] > 0))
            self.features.append(self._pending.pop("row"))

        ready = all(len(self._closes[s]) >= self.cfg.lags + 1 for s in self.cfg.symbols)
        if not ready:
            return
        row: list[float] = []
        for s in self.cfg.symbols:
            c = self._closes[s]
            row.extend(c[i] / c[i - 1] - 1.0 for i in range(len(c) - self.cfg.lags, len(c)))
        spread = bar.book_spread_bps if bar.symbol == self.cfg.symbols[0] else None
        imbalance = bar.book_imbalance if bar.symbol == self.cfg.symbols[0] else None
        row.extend(
            [
                float(spread) if spread is not None else 0.0,
                float(imbalance) if imbalance is not None else 0.0,
                1.0 if spread is not None and imbalance is not None else 0.0,
            ]
        )
        if bar.symbol == self.cfg.symbols[0]:
            self._pending["row"] = row

    def step(self, optimizer: Any) -> dict[str, Any] | None:
        """Run the frozen GA schedule; return a decision record or None."""
        n = len(self.features)
        if n < self.cfg.min_observations:
            return None
        if n - self.last_update < self.cfg.update_every:
            return None
        self.last_update = n
        start = max(0, n - self.cfg.window_bars)
        X = self.features[start:]
        y = self.targets[start:]
        R = self.returns[start:]
        m = len(X)
        tr = list(range(0, int(m * 0.70)))
        va = list(range(int(m * 0.70), int(m * 0.85)))
        te = list(range(int(m * 0.85), m))
        result = optimizer.run(X, y, R, tr, va, te, self.cfg.seed)
        acc = float(result.get("acc", 0.0))
        net = float(result.get("net", float("-inf")))
        self.tradable = bool(net > 0.0 and acc >= self.cfg.acc_bar)
        prob = float(result.get("prob", 0.5))
        decision = {
            "observe_only": True,
            "tradable": self.tradable,
            "action": "BUY" if prob > 0.5 else "SELL",
            "confidence": prob,
            "held_out_acc": acc,
            "held_out_net": net,
            "observations": n,
            "book_columns": 3,
            "rationale": (
                "observe-only online learner; tradable requires costed net > 0 "
                "and accuracy >= 0.53 on the held-out tail"
            ),
        }
        self.decisions.append(decision)
        return decision

    def last_ts(self, symbol: str) -> float:
        """Last accepted timestamp for a symbol; -inf before the first bar."""
        return self._latest.get(symbol, float("-inf"))

    def snapshot(self) -> Mapping[str, Any]:
        return {
            "observations": len(self.features),
            "bars_seen": self.bars_seen,
            "tradable": self.tradable,
            "last_update": self.last_update,
            "decisions": len(self.decisions),
            "observe_only": True,
        }
