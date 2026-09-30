"""Invariants for research fills, OOS carry, sizing, grids, and strategy contracts.

These tests encode *expected* behaviour first — if walk-forward says every
strategy is bankrupt, one of these should fail and point at the accounting bug
rather than the market.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.indicators import equity_pnl  # noqa: E402
from engine.position_sizing import SizingConfig, SizingMode  # noqa: E402
from engine.research import decisions_to_fills, offline_variant_metrics  # noqa: E402
from engine.strategy_fixtures import mean_reverting, rising_trend  # noqa: E402
from engine.strategy_plugin import StrategyDecision  # noqa: E402
from host.phantom_variants import default_grid_variants  # noqa: E402
from strategies.adx_di_trend.plugin import AdxDiTrendPlugin  # noqa: E402
from strategies.bollinger_reversion.plugin import BollingerReversionPlugin  # noqa: E402
from strategies.cci_reversion.plugin import CciReversionPlugin  # noqa: E402
from strategies.dual_thrust.plugin import DualThrustPlugin  # noqa: E402
from strategies.ema_cross.plugin import EmaCrossPlugin  # noqa: E402
from strategies.heikin_ashi_trend.plugin import HeikinAshiTrendPlugin  # noqa: E402
from strategies.keltner_breakout.plugin import KeltnerBreakoutPlugin  # noqa: E402
from strategies.ma_cross_example.plugin import MaCrossExamplePlugin  # noqa: E402
from strategies.macd_trend.plugin import MacdTrendPlugin  # noqa: E402
from strategies.mean_reversion_example.plugin import MeanReversionExamplePlugin  # noqa: E402
from strategies.momentum_roc.plugin import MomentumRocPlugin  # noqa: E402
from strategies.parabolic_sar_follow.plugin import ParabolicSarFollowPlugin  # noqa: E402
from strategies.rsi_reversion.plugin import RsiReversionPlugin  # noqa: E402
from strategies.stochastic_reversion.plugin import StochasticReversionPlugin  # noqa: E402
from strategies.supertrend_follow.plugin import SupertrendFollowPlugin  # noqa: E402
from strategies.turtle_breakout.plugin import TurtleBreakoutPlugin  # noqa: E402
from strategies.volatility_breakout_example.plugin import (  # noqa: E402
    VolatilityBreakoutExamplePlugin,
)
from strategies.volume_impulse_example.plugin import VolumeImpulseExamplePlugin  # noqa: E402
from strategies.vwap_reversion.plugin import VwapReversionPlugin  # noqa: E402
from strategies.williams_r_reversion.plugin import WilliamsRReversionPlugin  # noqa: E402


def _bind(cls):
    p = cls(MagicMock(), cls.PLUGIN_ID, MagicMock())
    p.config = {}
    return p


def _D(action: str, intent: str | None = None) -> StrategyDecision:
    if intent is None:
        intent = {"BUY": "long", "SELL": "short", "HOLD": "flat"}.get(action, "flat")
    return StrategyDecision(action=action, intent=intent)


# ---------------------------------------------------------------------------
# Research accounting contracts
# ---------------------------------------------------------------------------


def test_long_to_short_reverses_not_flatten_only() -> None:
    """SELL after BUY must reach a short book, not flatten and sit in cash."""
    closes = [100.0, 110.0, 120.0]
    decisions = [_D("BUY"), _D("SELL"), _D("SELL")]
    fills = decisions_to_fills(
        decisions, closes, quantity=1.0, fee_bps=0.0, position_mode="net_flip"
    )
    m = equity_pnl(fills, start_equity=10_000.0, mark_price=120.0)
    assert m["position"] < 0
    assert abs(m["position"] + 1.0) < 1e-9


def test_long_flat_sell_exits_without_short() -> None:
    """Default paper forbid_net_flip: SELL flattens, never opens short."""
    closes = [100.0, 90.0, 80.0]
    decisions = [_D("BUY"), _D("SELL"), _D("SELL")]
    fills = decisions_to_fills(
        decisions, closes, quantity=1.0, fee_bps=0.0, position_mode="long_flat"
    )
    m = equity_pnl(fills, start_equity=10_000.0, mark_price=80.0)
    assert abs(m["position"]) < 1e-12
    # Long 100→90 exit = −10
    assert abs(m["pnl"] + 10.0) < 1e-9


def test_hold_flat_clears_carry_into_oos() -> None:
    """Mean-reversion style: BUY then HOLD/flat before OOS must not carry inventory."""
    decisions = [_D("BUY"), _D("HOLD", "flat"), _D("HOLD", "flat"), _D("HOLD", "flat")]
    closes = [100.0, 101.0, 102.0, 150.0]
    fills = decisions_to_fills(
        decisions, closes, quantity=1.0, fee_bps=0.0, score_from=3, carry_side=True
    )
    assert fills == []
    m = equity_pnl(fills, start_equity=10_000.0, mark_price=150.0)
    assert m["pnl"] == 0.0


def test_always_in_carry_earns_hold_pnl() -> None:
    decisions = [_D("BUY")] * 5
    closes = [100.0, 101.0, 102.0, 103.0, 110.0]
    fills = decisions_to_fills(
        decisions, closes, quantity=10.0, fee_bps=0.0, score_from=3, carry_side=True
    )
    assert len(fills) == 1 and fills[0].get("carry") is True
    assert fills[0]["cost"] == 0.0
    m = equity_pnl(fills, start_equity=10_000.0, mark_price=110.0)
    # 10 * (110 - 102) — carry opens at closes[score_from-1]
    assert abs(m["pnl"] - 80.0) < 1e-9
    assert m["trades"] == 0


def test_percent_sizing_reverse_leaves_no_dust() -> None:
    sizing = SizingConfig(mode=SizingMode.PERCENT_OF_EQUITY, value=10.0)
    decisions = [_D("BUY"), _D("SELL")]
    closes = [100.0, 200.0]
    fills = decisions_to_fills(
        decisions,
        closes,
        quantity=None,
        fee_bps=0.0,
        sizing=sizing,
        start_equity=10_000.0,
        position_mode="net_flip",
    )
    m = equity_pnl(fills, start_equity=10_000.0, mark_price=200.0)
    assert abs(m["position"] + (10_000.0 * 0.10 / 200.0)) < 1e-9


def test_hold_flat_in_oos_flattens_open() -> None:
    decisions = [_D("BUY"), _D("HOLD", "flat")]
    closes = [100.0, 110.0]
    fills = decisions_to_fills(decisions, closes, quantity=2.0, fee_bps=0.0)
    m = equity_pnl(fills, start_equity=10_000.0, mark_price=110.0)
    assert abs(m["position"]) < 1e-12
    assert abs(m["pnl"] - 20.0) < 1e-9


def test_research_config_ignores_live_plugin_config() -> None:
    p = _bind(MaCrossExamplePlugin)
    p.config = {"fast": 2, "slow": 99}  # poisoned live knobs
    series = rising_trend(n=40)
    # Explicit research config must win without merging live slow=99
    d = p.evaluate_bars(series, config={"fast": 3, "slow": 8})
    assert any(x and x.action == "BUY" for x in d)
    # Alien keys must not silently become the only config change
    a = p.evaluate_bars(series, config={"threshold": 1.0})
    b = p.evaluate_bars(series, config={})
    assert [x.action if x else None for x in a] == [x.action if x else None for x in b]


# ---------------------------------------------------------------------------
# Grid ↔ schema
# ---------------------------------------------------------------------------


_ALL_STRATEGIES = (
    AdxDiTrendPlugin,
    BollingerReversionPlugin,
    CciReversionPlugin,
    DualThrustPlugin,
    EmaCrossPlugin,
    HeikinAshiTrendPlugin,
    KeltnerBreakoutPlugin,
    MaCrossExamplePlugin,
    MacdTrendPlugin,
    MeanReversionExamplePlugin,
    MomentumRocPlugin,
    ParabolicSarFollowPlugin,
    RsiReversionPlugin,
    StochasticReversionPlugin,
    SupertrendFollowPlugin,
    TurtleBreakoutPlugin,
    VolatilityBreakoutExamplePlugin,
    VolumeImpulseExamplePlugin,
    VwapReversionPlugin,
    WilliamsRReversionPlugin,
)


def test_every_grid_key_is_in_schema() -> None:
    for cls in _ALL_STRATEGIES:
        schema = set(cls.CONFIG_SCHEMA or {})
        for row in default_grid_variants(cls.PLUGIN_ID):
            assert set(row) <= schema, (cls.PLUGIN_ID, row, schema)


def test_grid_warmup_fits_default_train_bars() -> None:
    train_bars = 60
    for cls in _ALL_STRATEGIES:
        p = _bind(cls)
        for row in default_grid_variants(cls.PLUGIN_ID):
            p.config = dict(row)
            warm = p.warmup_bars()
            p.config = {}
            assert warm < train_bars, (cls.PLUGIN_ID, row, warm)


# ---------------------------------------------------------------------------
# Strategy behavioural contracts on fixtures
# ---------------------------------------------------------------------------


def test_trend_followers_buy_rising_trend() -> None:
    series = rising_trend(n=100, step=1.0)
    for cls, cfg in (
        (MaCrossExamplePlugin, {"fast": 3, "slow": 8}),
        (EmaCrossPlugin, {"fast": 5, "slow": 13}),
        (MacdTrendPlugin, {"fast": 5, "slow": 13, "signal": 5}),
        (HeikinAshiTrendPlugin, {"min_streak": 1}),
        (ParabolicSarFollowPlugin, {}),
        (AdxDiTrendPlugin, {"period": 7, "adx_min": 10}),
        (SupertrendFollowPlugin, {"atr_period": 7, "multiplier": 2.0}),
    ):
        d = _bind(cls).evaluate_bars(series, config=cfg)
        post = [x for x in d[20:] if x and x.action != "HOLD"]
        assert post, cls.PLUGIN_ID
        buy_share = sum(1 for x in post if x.action == "BUY") / len(post)
        assert buy_share >= 0.6, (cls.PLUGIN_ID, buy_share)


def test_momentum_roc_defaults_fire_on_unit_trend() -> None:
    """Default grid must not be mute on a clear +1%/bar-ish rise."""
    series = rising_trend(n=80, start=100.0, step=1.0)
    d = _bind(MomentumRocPlugin).evaluate_bars(series, config={})
    assert any(x and x.action == "BUY" for x in d)


def test_momentum_roc_grid_includes_sensitive_entry() -> None:
    entries = [row["entry"] for row in default_grid_variants("momentum_roc")]
    assert min(entries) <= 0.01


def test_mean_reverters_trade_sine() -> None:
    series = mean_reverting(n=120, amp=14.0)
    for cls, cfg in (
        (RsiReversionPlugin, {"period": 8, "oversold": 35, "overbought": 65}),
        (MeanReversionExamplePlugin, {"window": 12, "z_entry": 1.0}),
        (CciReversionPlugin, {"period": 10, "entry": 80}),
        (StochasticReversionPlugin, {"k_period": 8, "d_period": 3, "oversold": 30, "overbought": 70}),
        (WilliamsRReversionPlugin, {"period": 8, "oversold": -70, "overbought": -30}),
        (VwapReversionPlugin, {"window": 12, "entry_pct": 0.005}),
        (BollingerReversionPlugin, {"window": 12, "num_std": 1.5}),
    ):
        d = _bind(cls).evaluate_bars(series, config=cfg)
        actions = {x.action for x in d if x and x.action != "HOLD"}
        assert actions & {"BUY", "SELL"}, cls.PLUGIN_ID


def test_turtle_holds_between_entry_and_exit() -> None:
    """After breakout, bars that are not exit breaches must keep long intent."""
    # Steep rise then mild pullback that stays above exit channel.
    closes = [100.0] * 25 + [130.0, 129.0, 128.5, 128.0, 127.5]
    n = len(closes)
    series = {
        "open": list(closes),
        "high": [c + 0.2 for c in closes],
        "low": [c - 0.2 for c in closes],
        "close": closes,
        "volume": [10.0] * n,
    }
    d = _bind(TurtleBreakoutPlugin).evaluate_bars(
        series, config={"entry_lookback": 20, "exit_lookback": 10}
    )
    # Find first BUY then ensure a later HOLD is intent=long (not flat wipe).
    idx_buy = next(i for i, x in enumerate(d) if x and x.action == "BUY")
    after = [x for x in d[idx_buy + 1 :] if x]
    assert after, "expected bars after entry"
    # At least one maintain-long HOLD before any flat exit
    assert any(x.action == "HOLD" and x.intent == "long" for x in after), [
        (x.action, x.intent, x.reason) for x in after[:8]
    ]


def test_ma_cross_positive_on_rising_with_fees() -> None:
    sizing = SizingConfig(mode=SizingMode.PERCENT_OF_EQUITY, value=10.0)
    m = offline_variant_metrics(
        _bind(MaCrossExamplePlugin),
        rising_trend(n=120, step=0.5),
        config={"fast": 3, "slow": 8},
        quantity=None,
        fee_bps=7.0,
        sizing=sizing,
        start_equity=10_000.0,
    )
    assert m["pnl"] > 50.0
    assert m["trades"] >= 1


def test_rsi_neutral_maintains_through_mean_reversion() -> None:
    """RSI must not flatten on neutral bars — that killed winners vs live."""
    series = mean_reverting(n=120, amp=14.0)
    p = _bind(RsiReversionPlugin)
    cfg = {"period": 8, "oversold": 35, "overbought": 65}
    d = p.evaluate_bars(series, config=cfg)
    idx = next(i for i, x in enumerate(d) if x and x.action == "BUY")
    after = [x for x in d[idx + 1 : idx + 15] if x]
    assert any(x.action == "HOLD" and x.intent == "long" for x in after), [
        (x.action, x.intent, x.reason) for x in after
    ]
    sizing = SizingConfig(mode=SizingMode.PERCENT_OF_EQUITY, value=10.0)
    # Both-side fade (net_flip) with maintain-through-neutral must be profitable
    # on a clean sine — previously HOLD+flat scalped every neutral bar to death.
    m = offline_variant_metrics(
        p,
        series,
        config=cfg,
        quantity=None,
        fee_bps=0.0,
        sizing=sizing,
        position_mode="net_flip",
    )
    assert m["pnl"] > 50.0
    assert m["trades"] < 40


def test_volume_impulse_maintains_between_pulses() -> None:
    """No-impulse bars must keep side (live held; research used to flatten)."""
    from strategies.volume_impulse_example.plugin import VolumeImpulseExamplePlugin

    # Quiet volume then a spike up, then quiet again.
    n = 40
    closes = [100.0 + (0.1 if i == 25 else 0.0) for i in range(n)]
    closes[25] = 101.5  # strong up return with volume spike
    volumes = [10.0] * n
    volumes[25] = 100.0
    series = {
        "open": list(closes),
        "high": [c + 0.1 for c in closes],
        "low": [c - 0.1 for c in closes],
        "close": closes,
        "volume": volumes,
    }
    d = _bind(VolumeImpulseExamplePlugin).evaluate_bars(
        series, config={"vol_window": 10, "vol_mult": 2.0, "min_return": 0.001}
    )
    idx = next((i for i, x in enumerate(d) if x and x.action == "BUY"), None)
    assert idx is not None, [(i, x.action, x.reason) for i, x in enumerate(d) if x]
    after = [x for x in d[idx + 1 : idx + 8] if x]
    assert any(x.action == "HOLD" and x.intent == "long" for x in after), [
        (x.action, x.intent, x.reason) for x in after
    ]


def test_dual_thrust_documented_mute_on_smooth_rise() -> None:
    """Defaults are breakout-oriented: smooth rising_trend may stay HOLD — OK.

    Sensitive k must still fire so the plugin is not dead code.
    """
    series = rising_trend(n=80, step=1.0)
    quiet = _bind(DualThrustPlugin).evaluate_bars(series, config={})
    loud = _bind(DualThrustPlugin).evaluate_bars(
        series, config={"lookback": 4, "k1": 0.05, "k2": 0.05}
    )
    assert any(x and x.action in {"BUY", "SELL"} for x in loud)
    # Document current default behaviour (may be all HOLD).
    _ = quiet
