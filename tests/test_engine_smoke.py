"""Smoke tests for engine risk / ensemble helpers."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from engine import (  # noqa: E402
    EnsembleManager,
    OrderIntent,
    OrderManager,
    OrderSide,
    RiskLimits,
    RiskManager,
    StrategySignal,
)


def test_risk_blocks_oversize() -> None:
    risk = RiskManager(RiskLimits(max_position_pct=0.2))
    om = OrderManager(risk, equity=10_000)
    # 30% of equity → reject or scale
    result = om.submit(
        OrderIntent(symbol="BTC/USDT", side=OrderSide.BUY, quantity=3, strategy_id="trend"),
        mark_price=1000,  # notional 3000 = 30%
    )
    assert result.status.value in {"filled", "rejected"}
    if result.status.value == "filled":
        assert result.filled_qty * 1000 <= 2000 + 1e-6


def test_ensemble_aggregate() -> None:
    ens = EnsembleManager(["trend", "meanrev"])
    decisions = ens.aggregate(
        [
            StrategySignal("trend", "SBER", "buy", 1.0),
            StrategySignal("meanrev", "SBER", "sell", 0.5),
        ]
    )
    assert len(decisions) == 1
    assert decisions[0].side in {"buy", "sell", "flat"}


def test_risk_parity_and_ucb() -> None:
    w = EnsembleManager.risk_parity_weights({"a": 0.1, "b": 0.2})
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["a"] > w["b"]
    scores = EnsembleManager.ucb_scores({"a": 0.1, "b": 0.05}, {"a": 10, "b": 2}, 100)
    assert "a" in scores and "b" in scores


if __name__ == "__main__":
    test_risk_blocks_oversize()
    test_ensemble_aggregate()
    test_risk_parity_and_ucb()
    print("engine smoke OK")
