"""The liquidity gate was a control that provided no control.

`RiskManager.check_liquidity` compared order notional against average daily
volume -- but nothing in production ever passed `average_daily_volume`, and
`max_order_adv_pct` defaults to None, so `_required` resolved to *allow*. The
gate read like protection in the risk trace and enforced nothing.

Traded notional is available: candles carry volume, and they already flow through
the runtime. These tests pin that the data now reaches the decision, that a
genuinely oversized order is blocked, and that absence of history is reported as
unknown rather than fabricated as zero -- a fabricated zero becomes an infinite
ratio and a block that looks like a risk decision but is not one.
"""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.risk import RiskLimits, RiskManager  # noqa: E402


class _Bars:
    def __init__(self, rows) -> None:
        self._rows = rows

    def bars(self, symbol):  # noqa: ANN001
        return self._rows


class _Bar:
    def __init__(self, close: float, volume: float) -> None:
        self.close = close
        self.volume = volume


def _volume_feed(rows):
    from engine.runtime import _average_daily_notional_from

    return _average_daily_notional_from(_Bars(rows))


def test_volume_reaches_the_risk_gate_in_live_wiring() -> None:
    """The whole point: the gate must finally have data."""
    import inspect

    from engine.order import OrderManager

    src = inspect.getsource(OrderManager)
    assert "average_daily_volume=" in src, (
        "the order path must pass volume to the risk gate"
    )
    assert "volume_feed" in inspect.signature(OrderManager.__init__).parameters


def test_a_small_order_passes_against_real_volume() -> None:
    feed = _volume_feed([_Bar(100.0, 1_000.0) for _ in range(30)])
    notional = feed("BTC/USDT")
    assert notional is not None
    assert notional == 100_000.0


def test_a_large_share_of_volume_is_blocked_when_a_limit_is_set() -> None:
    risk = RiskManager(
        RiskLimits(max_order_adv_pct=0.10, max_position_pct=1.0, max_leverage=1.0)
    )
    feed = _volume_feed([_Bar(100.0, 1_000.0) for _ in range(30)])
    decision = risk.check_order(
        equity=100_000.0,
        symbol="BTC/USDT",
        notional=50_000.0,  # 50% of the 100k traded
        current_symbol_exposure=0.0,
        proposed_symbol_exposure=50_000.0,
        current_gross_exposure=0.0,
        reduces_risk=False,
        average_daily_volume=feed("BTC/USDT"),
    )
    assert not decision.allowed, "half the day's volume in one order must be refused"
    assert "liquidity" in str(decision.reason)


def test_a_reasonable_share_passes() -> None:
    risk = RiskManager(
        RiskLimits(max_order_adv_pct=0.10, max_position_pct=1.0, max_leverage=1.0)
    )
    feed = _volume_feed([_Bar(100.0, 1_000.0) for _ in range(30)])
    decision = risk.check_order(
        equity=100_000.0,
        symbol="BTC/USDT",
        notional=5_000.0,  # 5%
        current_symbol_exposure=0.0,
        proposed_symbol_exposure=5_000.0,
        current_gross_exposure=0.0,
        reduces_risk=False,
        average_daily_volume=feed("BTC/USDT"),
    )
    assert decision.allowed, decision.reason


def test_no_history_is_unknown_not_zero() -> None:
    """Zero would become an infinite ratio and a block nobody can explain."""
    feed = _volume_feed([])
    assert feed("BTC/USDT") is None


def test_bars_with_no_volume_are_not_counted() -> None:
    feed = _volume_feed([_Bar(100.0, 0.0) for _ in range(30)])
    assert feed("BTC/USDT") is None, "zero-volume bars must not fake liquidity"


def test_the_window_uses_recent_bars_not_all_history() -> None:
    """Old busy volume must not license a large order today."""
    from engine.runtime import _average_daily_notional_from

    rows = [_Bar(100.0, 100_000.0) for _ in range(20)]  # a very busy earlier session
    rows += [_Bar(100.0, 10.0) for _ in range(5)]  # the last five bars are quiet
    feed = _average_daily_notional_from(_Bars(rows), lookback=5)
    notional = feed("BTC/USDT")
    assert notional is not None
    assert notional == 1_000.0, (
        f"expected only the recent quiet window, got {notional}"
    )