"""Strategy SDK contract, isolation, no-lookahead, four crypto strategies."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from engine.strategy_fixtures import (  # noqa: E402
    append_future,
    breakout_up,
    decisions_summary,
    mean_reverting,
    rising_trend,
    volume_spike_up,
)
from engine.strategy_plugin import StrategyDecision, StrategyPlugin  # noqa: E402
from strategies.ma_cross_example.plugin import MaCrossExamplePlugin  # noqa: E402
from strategies.mean_reversion_example.plugin import MeanReversionExamplePlugin  # noqa: E402
from strategies.volatility_breakout_example.plugin import (  # noqa: E402
    VolatilityBreakoutExamplePlugin,
)
from strategies.volume_impulse_example.plugin import VolumeImpulseExamplePlugin  # noqa: E402


def _bind(cls, plugin_id: str | None = None) -> StrategyPlugin:
    core = MagicMock()
    registry = MagicMock()
    pid = plugin_id or getattr(cls, "PLUGIN_ID", "strategy")
    plugin = cls(core, pid, registry)
    plugin.config = {}
    return plugin


def test_config_schema_and_warmup() -> None:
    ma = _bind(MaCrossExamplePlugin)
    assert "fast" in ma.config_schema()
    assert ma.warmup_bars() >= 8
    h = ma.health()
    assert h.version
    assert h.ok is True


def test_ma_cross_emits_buy_on_rising() -> None:
    ma = _bind(MaCrossExamplePlugin)
    series = rising_trend(n=30)
    decisions = ma.evaluate_bars(series, config={"fast": 3, "slow": 8})
    assert any(d and d.action == "BUY" for d in decisions)
    # After warmup all actionable sides should be BUY on pure uptrend.
    actionable = [d.action for d in decisions if d and d.action != "HOLD"]
    assert actionable and all(a == "BUY" for a in actionable)


def test_mean_reversion_has_both_sides() -> None:
    mr = _bind(MeanReversionExamplePlugin)
    series = mean_reverting(n=80, amp=12.0)
    decisions = mr.evaluate_bars(series, config={"window": 12, "z_entry": 1.0})
    summary = decisions_summary(decisions)
    assert summary["BUY"] >= 1
    assert summary["SELL"] >= 1


def test_volatility_breakout_up() -> None:
    vb = _bind(VolatilityBreakoutExamplePlugin)
    series = breakout_up(flat=25, jump=15.0)
    decisions = vb.evaluate_bars(
        series, config={"lookback": 20, "atr_mult": 0.0, "atr_window": 10}
    )
    assert any(d and d.action == "BUY" for d in decisions)


def test_volume_impulse_up() -> None:
    vi = _bind(VolumeImpulseExamplePlugin)
    series = volume_spike_up(n=30)
    decisions = vi.evaluate_bars(
        series, config={"vol_window": 10, "vol_mult": 2.0, "min_return": 0.001}
    )
    assert any(d and d.action == "BUY" for d in decisions)


def test_no_lookahead_prefix_stable() -> None:
    ma = _bind(MaCrossExamplePlugin)
    base = rising_trend(n=25)
    cfg = {"fast": 3, "slow": 8}
    first = ma.evaluate_bars(base, config=cfg)
    extended = append_future(base, bars=8, step=1.0)
    second = ma.evaluate_bars(extended, config=cfg)
    for i, (a, b) in enumerate(zip(first, second)):
        a_action = None if a is None else a.action
        b_action = None if b is None else b.action
        assert a_action == b_action, f"lookahead at index {i}: {a_action} vs {b_action}"


def test_research_isolation_restores_config() -> None:
    ma = _bind(MaCrossExamplePlugin)
    ma.config.update({"fast": 3, "slow": 8, "tickers": ["BTC/USDT"]})
    ma._last_side = "BUY"
    with ma.research_session(run_id="r1", config=ma.isolated_config({"fast": 5, "slow": 15})):
        assert ma.config["fast"] == 5
        assert ma._last_side is None
        assert ma.health().research is True
        # Live candle without run_id must be ignored.
        from microkernel_core import KernelEvent

        ev = KernelEvent(
            event_type="market.candle",
            source_id="test",
            payload={"ticker": "BTC/USDT", "close": 1.0},
        )
        assert ma.candle_allowed(ev) is False
        ev2 = KernelEvent(
            event_type="market.candle",
            source_id="test",
            payload={"ticker": "BTC/USDT", "close": 1.0, "run_id": "r1"},
        )
        assert ma.candle_allowed(ev2) is True
    assert ma.config["fast"] == 3
    assert ma._last_side == "BUY"
    assert ma.health().research is False


def test_strategy_fault_isolated() -> None:
    class Boom(StrategyPlugin):
        PLUGIN_ID = "boom"

        def evaluate_at(self, series, index, *, config=None):
            raise RuntimeError("boom")

    boom = _bind(Boom, "boom")
    out = boom.evaluate_bars(rising_trend(n=5))
    assert all(d is None for d in out)
    # evaluate_bars restores prior state; faults must not escape the walk.
    assert boom.health().ok is True


def test_decision_dataclass() -> None:
    d = StrategyDecision(action="BUY", strength=0.5, intent="long")
    fields = d.as_signal_fields()
    assert fields["action"] == "BUY"
    assert fields["intent"] == "long"


if __name__ == "__main__":
    test_config_schema_and_warmup()
    test_ma_cross_emits_buy_on_rising()
    test_mean_reversion_has_both_sides()
    test_volatility_breakout_up()
    test_volume_impulse_up()
    test_no_lookahead_prefix_stable()
    test_research_isolation_restores_config()
    test_strategy_fault_isolated()
    test_decision_dataclass()
    print("strategy sdk OK")
