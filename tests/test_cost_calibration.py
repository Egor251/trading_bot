"""The cost model must be measured, not asserted.

`fee_bps: 5` and `slippage_bps: 2` are constants. Nothing checks them against
what a venue actually charges, so every fill books the configured number
whether or not it matches reality, and every PnL derived from it inherits the
error invisibly. `nightly_fee_reconciliation` exists to catch exactly this and
is never called outside tests, so it compared an expected fee against an
expected fee.

These tests pin the measurement. The properties that matter:

* slippage is signed so positive always means "this trade cost me", on both
  sides -- a seller hurt by a lower fill must not read as a gain
* the tail decides, not the mean: execution cost is heavy-tailed, so a model
  calibrated on the average is wrong for most trades and badly wrong for the
  worst
* calibration refuses to speak from a thin sample, because ten fills yield a
  number that is not yet a fact
"""

from __future__ import annotations

import math

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.cost_calibration import (  # noqa: E402
    ExecutionObservation,
    _percentile,
    calibrate,
)


def _obs(fill_id: str, *, side="buy", ref=100.0, fill=100.0, notional=10_000.0,
         fee=None, expected=0.0) -> ExecutionObservation:
    return ExecutionObservation(
        fill_id=fill_id,
        venue="bitget",
        symbol="BTC/USDT",
        side=side,
        quantity=1.0,
        reference_price=ref,
        fill_price=fill,
        notional=notional,
        expected_cost=expected,
        actual_fee=fee,
    )


def test_a_buy_above_its_reference_is_a_cost() -> None:
    obs = _obs("f1", side="buy", ref=100.0, fill=100.10)
    assert obs.slippage_bps > 0
    assert math.isclose(obs.slippage_bps, 10.0, rel_tol=1e-6)


def test_a_sell_below_its_reference_is_also_a_cost() -> None:
    """The sign must invert for sells, or a losing sell books as a gain."""
    obs = _obs("f2", side="sell", ref=100.0, fill=99.90)
    assert obs.slippage_bps > 0, "a lower fill hurts a seller"
    assert math.isclose(obs.slippage_bps, 10.0, rel_tol=1e-6)


def test_favourable_pricing_is_a_negative_cost() -> None:
    assert _obs("f3", side="buy", ref=100.0, fill=99.9).slippage_bps < 0
    assert _obs("f4", side="sell", ref=100.0, fill=100.1).slippage_bps < 0


def test_fee_and_slippage_both_count_toward_cost() -> None:
    obs = _obs("f5", ref=100.0, fill=100.05, notional=10_000.0, fee=5.0)
    assert math.isclose(obs.slippage_cost, 5.0, rel_tol=1e-6)
    assert math.isclose(obs.total_cost, 10.0, rel_tol=1e-6)


def test_a_malformed_observation_is_rejected() -> None:
    for kwargs in (
        {"side": "hold"},
        {"notional": 0.0},
        {"ref": 0.0},
        {"fill": -1.0},
    ):
        try:
            _obs("bad", **kwargs)
        except ValueError:
            continue
        raise AssertionError(f"accepted a nonsensical observation: {kwargs}")


def test_percentiles_are_ordered_and_bounded() -> None:
    values = [float(i) for i in range(101)]  # 0..100
    assert math.isclose(_percentile(values, 0.0), 0.0)
    assert math.isclose(_percentile(values, 1.0), 100.0)
    assert math.isclose(_percentile(values, 0.5), 50.0)
    p90 = _percentile(values, 0.9)
    assert 50.0 <= p90 <= 100.0
    assert _percentile([7.0], 0.9) == 7.0


def test_a_thin_sample_cannot_condemn_the_model() -> None:
    """Ten losing fills is a number, not a fact."""
    rows = [_obs(f"f{i}", ref=100.0, fill=101.0) for i in range(10)]
    report = calibrate(rows, model_slippage_bps=0.0, minimum_samples=30)
    assert report.samples == 10
    assert report.understates is False, "acted on too little evidence"
    assert report.observed_p90_bps > 0, "but the measurement is still reported"


def test_a_fat_tail_is_caught_where_the_mean_is_diluted() -> None:
    """Mostly clean, a fifth disastrous: the mean hides it, p90 must not.

    This is the realistic failure mode for crossing a thin book, and it is what
    the gate is calibrated for.
    """
    rows = [_obs(f"ok{i}", ref=100.0, fill=100.0) for i in range(24)]
    rows += [_obs(f"bad{i}", ref=100.0, fill=110.0) for i in range(6)]
    report = calibrate(rows, model_slippage_bps=5.0, minimum_samples=30)
    assert report.observed_p50_bps == 0.0, "typical trade was clean"
    assert report.observed_p90_bps > 5.0, "but the tail is what kills"
    assert report.understates is True, (
        "a model priced at 5bps is unsafe when one trade in five crosses the book"
    )


def test_a_thin_tail_is_caught_by_the_mean_where_p90_misses_it() -> None:
    """One catastrophic fill in thirty sits near p97, so p90 reads clean.

    The mean is the only statistic that sees it, which is why the gate uses
    both: each one is blind to a failure the other catches.
    """
    rows = [_obs(f"ok{i}", ref=100.0, fill=100.0) for i in range(29)]
    rows.append(_obs("bad", ref=100.0, fill=400.0))
    report = calibrate(rows, model_slippage_bps=5.0, minimum_samples=30)
    assert report.observed_p90_bps == 0.0, "p90 genuinely misses this shape"
    assert report.observed_p99_bps > 5.0
    assert report.mean_cost_bps > 5.0
    assert report.understates is True, "the gate must not go quiet on a thin tail"


def test_the_report_serialises_for_the_operator() -> None:
    rows = [_obs(f"f{i}", ref=100.0, fill=100.05, fee=5.0) for i in range(40)]
    body = calibrate(rows, model_slippage_bps=2.0).to_dict()
    for key in ("samples", "observed_p50_bps", "observed_p90_bps", "understates"):
        assert key in body
    assert body["samples"] == 40


def test_a_real_fill_produces_an_observation() -> None:
    """The module is worthless unless the execution path feeds it.

    This is the failure mode the existing `nightly_fee_reconciliation` has: it
    exists, it is tested, and nothing in production ever calls it, so it has
    been comparing an expected fee against an expected fee. This asserts the
    measurement actually happens on a fill.
    """
    from engine.order import OrderManager, OrderIntent, OrderSide
    from engine.risk import RiskLimits, RiskManager

    manager = OrderManager(RiskManager(RiskLimits()), equity=10_000.0)
    intent = OrderIntent(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=0.01,
        order_type="limit",
        limit_price=50_000.0,
        class_code="CRYPTO",
    )
    manager._record_fill(  # noqa: SLF001 - the one path a fill takes
        intent,
        quantity=0.01,
        price=50_050.0,
        raw={"fill_id": "f-1", "fee": 5.0},
    )

    observations = manager.execution_observations()
    assert len(observations) == 1, "the fill was not measured"
    obs = observations[0]
    assert obs.reference_price == 50_000.0
    assert obs.slippage_bps > 0, "a fill above the reference must cost"
    assert obs.actual_fee == 5.0

    report = calibrate(observations, model_slippage_bps=0.0, minimum_samples=1)
    assert report.samples == 1


def test_a_fill_without_a_reference_price_is_not_fabricated() -> None:
    """A missing reference must leave a gap, not a zero that reads as free."""
    from engine.order import OrderManager, OrderIntent, OrderSide
    from engine.risk import RiskLimits, RiskManager

    manager = OrderManager(RiskManager(RiskLimits()), equity=10_000.0)
    manager._record_fill(  # noqa: SLF001
        OrderIntent(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            quantity=0.01,
            order_type="market",
            class_code="CRYPTO",
        ),
        quantity=0.01,
        price=50_000.0,
        raw={"fill_id": "f-2"},
    )
    assert manager.execution_observations() == [], (
        "no reference price means no measurement, not a zero-cost fill"
    )


def test_observations_are_bounded() -> None:
    from engine.order import OrderManager
    from engine.risk import RiskLimits, RiskManager

    manager = OrderManager(RiskManager(RiskLimits()), equity=10_000.0)
    manager._exec_observation_limit = 5  # noqa: SLF001
    for i in range(20):
        manager._exec_observations.append(  # noqa: SLF001
            _obs(f"f{i}", fee=1.0)
        )
        manager._record_execution_observation(  # noqa: SLF001
            type("I", (), {"slippage_reference": 100.0})(),
            type(
                "R",
                (),
                {
                    "fill_id": f"f{i}",
                    "venue": "bitget",
                    "symbol": "BTC/USDT",
                    "side": "buy",
                    "quantity": 1.0,
                    "price": 100.0,
                    "contract_multiplier": 1.0,
                    "total_cost": 1.0,
                },
            )(),
        )
    assert len(manager.execution_observations()) <= 5


def test_an_observation_feeds_the_existing_reconciler() -> None:
    """The dead reconciler must be usable with real observations."""
    from engine.observability import FeeLine, nightly_fee_reconciliation

    obs = _obs("f1", ref=100.0, fill=100.10, fee=5.2, expected=5.0)
    report = nightly_fee_reconciliation([FeeLine(**obs.to_fee_line())])
    assert report.checked == 1
    assert math.isclose(report.fee_delta, 0.2, rel_tol=1e-6)
    assert math.isclose(report.max_slippage_delta_bps, 10.0, rel_tol=1e-6)
    assert report.matched is False, "a 10bps miss must not report as matched"