"""Position sizing modes, strength scale, Kelly/ATR helpers."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.position_sizing import (  # noqa: E402
    SizingConfig,
    SizingMode,
    atr_from_bars,
    kelly_fraction,
    size_quantity,
)
from engine.runtime import TradingRuntime  # noqa: E402
from strategies.ma_cross_example.plugin import MaCrossExamplePlugin  # noqa: E402
from tests.test_strategy_sdk import _bind  # noqa: E402


def test_percent_of_equity_default() -> None:
    qty = size_quantity(
        equity=10_000,
        price=100,
        config=SizingConfig(mode=SizingMode.PERCENT_OF_EQUITY, value=10),
    )
    assert abs(qty - 10.0) < 1e-9  # 10% of 10k = 1000 notional → 10 units


def test_cash_and_fixed() -> None:
    assert abs(
        size_quantity(
            equity=10_000,
            price=50,
            config=SizingConfig(mode=SizingMode.CASH, value=500),
        )
        - 10.0
    ) < 1e-9
    assert abs(
        size_quantity(
            equity=10_000,
            price=50,
            config=SizingConfig(mode=SizingMode.FIXED, value=3),
            fixed_qty=3,
        )
        - 3.0
    ) < 1e-9


def test_risk_percent() -> None:
    # risk 1% of 10k = 100; stop 2% of price 100 = 2 → qty = 50
    qty = size_quantity(
        equity=10_000,
        price=100,
        config=SizingConfig(
            mode=SizingMode.RISK_PERCENT,
            value=1,
            stop_pct=0.02,
            max_position_pct=1.0,
            scale_by_strength=False,
        ),
    )
    assert abs(qty - 50.0) < 1e-6


def test_atr_risk() -> None:
    qty = size_quantity(
        equity=10_000,
        price=100,
        atr=2.0,
        config=SizingConfig(
            mode=SizingMode.ATR_RISK,
            value=2,
            atr_mult=2.0,
            max_position_pct=1.0,
            scale_by_strength=False,
        ),
    )
    # risk cash 200 / stop 4 → 50
    assert abs(qty - 50.0) < 1e-6


def test_kelly_half() -> None:
    f = kelly_fraction(win_rate=0.55, reward_risk=1.5)
    assert 0.2 < f < 0.4
    qty = size_quantity(
        equity=10_000,
        price=100,
        config=SizingConfig(
            mode=SizingMode.KELLY,
            value=100,
            win_rate=0.55,
            reward_risk=1.5,
            kelly_fraction=0.5,
            max_position_pct=1.0,
            scale_by_strength=False,
        ),
    )
    expected = (10_000 * f * 0.5) / 100
    assert abs(qty - expected) < 1e-6


def test_strength_scales_and_missing_is_full() -> None:
    cfg = SizingConfig(
        mode=SizingMode.PERCENT_OF_EQUITY,
        value=10,
        scale_by_strength=True,
        strength_floor=0.25,
    )
    full = size_quantity(equity=10_000, price=100, config=cfg, strength=None)
    half = size_quantity(equity=10_000, price=100, config=cfg, strength=0.5)
    # Below floor → reject (qty 0), not boost to floor.
    below = size_quantity(equity=10_000, price=100, config=cfg, strength=0.01)
    tiny = size_quantity(equity=10_000, price=100, config=cfg, strength=0.000173)
    at_floor = size_quantity(equity=10_000, price=100, config=cfg, strength=0.25)
    assert abs(full - 10.0) < 1e-9
    assert abs(half - 5.0) < 1e-9
    assert below == 0.0
    assert tiny == 0.0
    assert abs(at_floor - 2.5) < 1e-9
    assert cfg.rejects_strength(0.000173) is True
    assert cfg.rejects_strength(0.5) is False
    assert cfg.rejects_strength(None) is False


def test_runtime_strength_floor_rejects_weak_signal() -> None:
    rt = TradingRuntime.from_config(
        {
            "equity": 10_000,
            "mode": "paper",
            "risk": {"max_position_pct": 1.0, "max_leverage": 5.0},
            "sizing": {
                "mode": "percent_of_equity",
                "value": 10,
                "max_position_pct": 0.5,
                "scale_by_strength": True,
                "strength_floor": 0.25,
            },
        }
    )
    rt.candles.ingest_payload(
        {
            "ticker": "X",
            "class_code": "SYNTH",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 1,
            "date": "2026-01-01",
            "time": "00:00:00",
        }
    )
    weak = rt.submit_signal(
        ticker="X",
        action="BUY",
        quantity=0.001,
        class_code="SYNTH",
        mark_price=100,
        strategy_id="t",
        strength=0.000173,
    )
    assert weak.status.value == "rejected"
    assert "strength_below_floor" in str(weak.reason)
    strong = rt.submit_signal(
        ticker="X",
        action="BUY",
        quantity=0.001,
        class_code="SYNTH",
        mark_price=100,
        strategy_id="t",
        strength=0.5,
    )
    assert strong.status.value == "filled"
    # 10% * 0.5 strength = 5 units at price 100
    assert abs(float(strong.filled_qty or 0) - 5.0) < 1e-6


def test_max_position_cap() -> None:
    qty = size_quantity(
        equity=10_000,
        price=100,
        config=SizingConfig(
            mode=SizingMode.PERCENT_OF_EQUITY,
            value=50,
            max_position_pct=0.25,
            scale_by_strength=False,
        ),
    )
    assert abs(qty - 25.0) < 1e-9


def test_atr_from_bars() -> None:
    closes = [10, 11, 12, 11, 13, 14, 13, 15, 16, 15, 17, 18, 17, 19, 20, 21]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    atr = atr_from_bars(highs, lows, closes, period=5)
    assert atr is not None and atr > 0


def test_runtime_submit_applies_sizing() -> None:
    rt = TradingRuntime.from_config(
        {
            "equity": 10_000,
            "mode": "paper",
            "risk": {"max_position_pct": 1.0, "max_leverage": 5.0},
            "sizing": {
                "mode": "percent_of_equity",
                "value": 10,
                "max_position_pct": 0.5,
                "scale_by_strength": False,
            },
        }
    )
    rt.candles.ingest_payload(
        {
            "ticker": "X",
            "class_code": "SYNTH",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 1,
            "date": "2026-01-01",
            "time": "00:00:00",
        }
    )
    res = rt.submit_signal(
        ticker="X",
        action="BUY",
        quantity=0.001,  # ignored when sizing active
        class_code="SYNTH",
        mark_price=100,
        strategy_id="t",
    )
    assert res.status.value == "filled"
    assert abs(float(res.filled_qty or 0) - 10.0) < 1e-6


def test_ma_cross_strength_not_constant() -> None:
    ma = _bind(MaCrossExamplePlugin)
    # Rising series → spread grows after warmup
    closes = [100.0 + i * 0.5 for i in range(30)]
    series = {"close": closes}
    d = ma.evaluate_at(series, len(closes) - 1, config={"fast": 3, "slow": 8})
    assert d is not None and d.action == "BUY"
    assert 0.0 < float(d.strength) <= 1.0


if __name__ == "__main__":
    test_percent_of_equity_default()
    test_cash_and_fixed()
    test_risk_percent()
    test_atr_risk()
    test_kelly_half()
    test_strength_scales_and_missing_is_full()
    test_runtime_strength_floor_rejects_weak_signal()
    test_max_position_cap()
    test_atr_from_bars()
    test_runtime_submit_applies_sizing()
    test_ma_cross_strength_not_constant()
    print("position sizing OK")
