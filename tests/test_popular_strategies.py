"""Smoke tests for popular public strategies (classic + new families)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from engine.strategy_fixtures import (  # noqa: E402
    breakout_up,
    mean_reverting,
    rising_trend,
)
from engine.strategy_plugin import StrategyPlugin  # noqa: E402
from strategies.adx_di_trend.plugin import AdxDiTrendPlugin  # noqa: E402
from strategies.bollinger_reversion.plugin import BollingerReversionPlugin  # noqa: E402
from strategies.cci_reversion.plugin import CciReversionPlugin  # noqa: E402
from strategies.dual_thrust.plugin import DualThrustPlugin  # noqa: E402
from strategies.ema_cross.plugin import EmaCrossPlugin  # noqa: E402
from strategies.heikin_ashi_trend.plugin import HeikinAshiTrendPlugin  # noqa: E402
from strategies.keltner_breakout.plugin import KeltnerBreakoutPlugin  # noqa: E402
from strategies.macd_trend.plugin import MacdTrendPlugin  # noqa: E402
from strategies.momentum_roc.plugin import MomentumRocPlugin  # noqa: E402
from strategies.parabolic_sar_follow.plugin import ParabolicSarFollowPlugin  # noqa: E402
from strategies.rsi_reversion.plugin import RsiReversionPlugin  # noqa: E402
from strategies.stochastic_reversion.plugin import StochasticReversionPlugin  # noqa: E402
from strategies.supertrend_follow.plugin import SupertrendFollowPlugin  # noqa: E402
from strategies.turtle_breakout.plugin import TurtleBreakoutPlugin  # noqa: E402
from strategies.vwap_reversion.plugin import VwapReversionPlugin  # noqa: E402
from strategies.williams_r_reversion.plugin import WilliamsRReversionPlugin  # noqa: E402


def _bind(cls) -> StrategyPlugin:
    core = MagicMock()
    registry = MagicMock()
    plugin = cls(core, cls.PLUGIN_ID, registry)
    plugin.config = {}
    return plugin


def test_rsi_reversion_both_sides() -> None:
    p = _bind(RsiReversionPlugin)
    series = mean_reverting(n=100, amp=15.0)
    decisions = p.evaluate_bars(
        series, config={"period": 8, "oversold": 35, "overbought": 65}
    )
    actions = {d.action for d in decisions if d and d.action != "HOLD"}
    assert "BUY" in actions
    assert "SELL" in actions


def test_macd_and_ema_buy_on_uptrend() -> None:
    series = rising_trend(n=80)
    macd = _bind(MacdTrendPlugin)
    ema = _bind(EmaCrossPlugin)
    macd_d = macd.evaluate_bars(series, config={"fast": 5, "slow": 13, "signal": 5})
    ema_d = ema.evaluate_bars(series, config={"fast": 5, "slow": 13})
    assert any(d and d.action == "BUY" for d in macd_d)
    assert any(d and d.action == "BUY" for d in ema_d)


def test_bollinger_fade_on_mean_reverting() -> None:
    p = _bind(BollingerReversionPlugin)
    series = mean_reverting(n=90, amp=12.0)
    decisions = p.evaluate_bars(series, config={"window": 12, "num_std": 1.5})
    actions = {d.action for d in decisions if d and d.action != "HOLD"}
    assert actions & {"BUY", "SELL"}


def test_momentum_roc_uptrend() -> None:
    p = _bind(MomentumRocPlugin)
    series = rising_trend(n=40)
    decisions = p.evaluate_bars(series, config={"period": 5, "entry": 0.01})
    assert any(d and d.action == "BUY" for d in decisions)


def test_supertrend_on_breakout() -> None:
    p = _bind(SupertrendFollowPlugin)
    series = breakout_up(flat=30, jump=20.0)
    decisions = p.evaluate_bars(
        series, config={"atr_period": 7, "multiplier": 2.0}
    )
    assert any(d and d.action in {"BUY", "SELL"} for d in decisions)


def test_new_mean_reversion_family() -> None:
    series = mean_reverting(n=120, amp=14.0)
    for cls, cfg in (
        (
            StochasticReversionPlugin,
            {"k_period": 8, "d_period": 3, "oversold": 30, "overbought": 70},
        ),
        (CciReversionPlugin, {"period": 10, "entry": 80}),
        (
            WilliamsRReversionPlugin,
            {"period": 8, "oversold": -70, "overbought": -30},
        ),
        (VwapReversionPlugin, {"window": 12, "entry_pct": 0.005}),
    ):
        p = _bind(cls)
        decisions = p.evaluate_bars(series, config=cfg)
        actions = {d.action for d in decisions if d and d.action != "HOLD"}
        assert actions & {"BUY", "SELL"}, cls.PLUGIN_ID


def test_new_trend_and_breakout_family() -> None:
    up = rising_trend(n=100)
    br = breakout_up(flat=25, jump=25.0)
    assert any(
        d and d.action == "BUY"
        for d in _bind(AdxDiTrendPlugin).evaluate_bars(
            up, config={"period": 7, "adx_min": 10}
        )
    )
    assert any(
        d and d.action in {"BUY", "SELL"}
        for d in _bind(ParabolicSarFollowPlugin).evaluate_bars(up)
    )
    assert any(
        d and d.action in {"BUY", "SELL"}
        for d in _bind(HeikinAshiTrendPlugin).evaluate_bars(
            up, config={"min_streak": 1}
        )
    )
    assert any(
        d and d.action == "BUY"
        for d in _bind(KeltnerBreakoutPlugin).evaluate_bars(
            br, config={"ema_period": 8, "atr_period": 5, "mult": 1.0}
        )
    )
    assert any(
        d and d.action == "BUY"
        for d in _bind(TurtleBreakoutPlugin).evaluate_bars(
            br, config={"entry_lookback": 10, "exit_lookback": 5}
        )
    )
    assert any(
        d and d.action in {"BUY", "SELL"}
        for d in _bind(DualThrustPlugin).evaluate_bars(
            br, config={"lookback": 3, "k1": 0.3, "k2": 0.3}
        )
    )


def test_indicator_helpers() -> None:
    from engine.indicators import (
        adx,
        bollinger,
        cci,
        heikin_ashi,
        keltner,
        macd,
        parabolic_sar,
        roc,
        rsi,
        stochastic,
        williams_r,
    )

    closes = [float(i) for i in range(1, 40)]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    opens = closes[:]
    assert any(v is not None for v in rsi(closes, 14))
    line, sig, hist = macd(closes, fast=5, slow=13, signal=5)
    assert any(v is not None for v in hist)
    mid, up, lo = bollinger(closes, 10, num_std=2.0)
    assert mid[-1] is not None and up[-1] is not None and lo[-1] is not None
    assert roc(closes, 5)[-1] is not None
    k, d = stochastic(highs, lows, closes, k_period=5, d_period=2)
    assert k[-1] is not None and d[-1] is not None
    assert cci(highs, lows, closes, 8)[-1] is not None
    assert williams_r(highs, lows, closes, 8)[-1] is not None
    a, p, m = adx(highs, lows, closes, 5)
    assert any(v is not None for v in a)
    assert p[-1] is not None and m[-1] is not None
    sar, trend = parabolic_sar(highs, lows)
    assert sar[-1] is not None and trend[-1] in (-1, 1)
    ho, hh, hl, hc = heikin_ashi(opens, highs, lows, closes)
    assert len(hc) == len(closes)
    km, ku, kl = keltner(highs, lows, closes, ema_period=5, atr_period=5, mult=1.0)
    assert km[-1] is not None and ku[-1] is not None and kl[-1] is not None


if __name__ == "__main__":
    test_rsi_reversion_both_sides()
    test_macd_and_ema_buy_on_uptrend()
    test_bollinger_fade_on_mean_reverting()
    test_momentum_roc_uptrend()
    test_supertrend_on_breakout()
    test_new_mean_reversion_family()
    test_new_trend_and_breakout_family()
    test_indicator_helpers()
    print("popular strategies OK")
