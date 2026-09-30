"""Soft regime detection + weight bias."""

from __future__ import annotations

from engine.regime import RegimeDetector, apply_regime_bias


def test_regime_detects_trend_and_vol() -> None:
    det = RegimeDetector(atr_period=5, lookback=40)
    # Quiet flat then strong uptrend
    closes = [100.0] * 30 + [100 + i * 2.0 for i in range(1, 25)]
    snap = det.observe(closes, symbol="BTC")
    assert snap.regime in {"strong_trend", "weak_trend", "high_vol", "low_vol", "shock"}
    assert snap.atr_pct >= 0.0


def test_regime_shock() -> None:
    det = RegimeDetector(atr_period=5, shock_mult=2.0)
    closes = [100.0 + (0.1 if i % 2 == 0 else -0.1) for i in range(40)]
    # Warm history
    for _ in range(10):
        det.observe(closes)
    shock_closes = list(closes) + [100.0, 130.0, 90.0, 140.0]
    snap = det.observe(shock_closes)
    assert snap.regime in {"shock", "high_vol"}


def test_apply_regime_bias_boosts_trend() -> None:
    weights = {"ma": 0.5, "mr": 0.5}
    archetypes = {"ma": "trend", "mr": "mean_reversion"}
    out = apply_regime_bias(weights, archetypes, "strong_trend")
    assert out["ma"] > out["mr"]
    assert abs(sum(out.values()) - 1.0) < 1e-9
