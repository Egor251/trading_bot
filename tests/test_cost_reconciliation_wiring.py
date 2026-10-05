"""The cost model is now compared against what the venue actually charged.

`calibrate` and `nightly_fee_reconciliation` were both correct, fully tested, and
reachable from nowhere. Every fill charged the model number, so the model was never
checked — and a wrong cost model makes every P&L derived from it wrong in the same
direction, invisibly. This is the same defect `adjust_pvalues` had: correct code,
no caller.

The awkward part of the fix is what a fill can actually support. Implementation
shortfall is measured against the price the *decision* was made on, so a fill
without a reference price can supply a fee delta and nothing else. Treating the
missing half as zero would report slippage of exactly 0.0 for those fills and drag
p50 and p90 down with them — a broken or partial feed would read as a cheap one.
They are excluded instead, and the report says how many were usable.

`sufficient: false` is a real state, not an error. Ten fills produce a number and
not a trustworthy one, and acting on it replaces a wrong constant with a
differently wrong one.
"""

from __future__ import annotations

from collections import deque
from unittest.mock import AsyncMock, MagicMock

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

import pytest  # noqa: E402

from engine.cost_calibration import cost_model_from_config  # noqa: E402
from services.ops_monitor_service import OpsMonitorService  # noqa: E402

#: What the shipped config charges, so the comparison has a fixed reference.
MODEL = {"services": {"phantom_service": {"fee_bps": 5.0, "slippage_bps": 2.0}}}


def _service(**overrides) -> OpsMonitorService:
    core = MagicMock()
    config: dict = {
        "cost_reconcile_every_sec": 3600.0,
        "cost_reconcile_min_samples": 5,
        "cost_reconcile_window": 20,
    }
    config.update(overrides)
    core.config.get = AsyncMock(
        side_effect=lambda key, default=None: (
            config if key == "trading" else default
        )
    )
    service = OpsMonitorService(core, "ops_monitor_service")
    service.config = config
    service.alerts = None
    # Set directly rather than through `on_initialize`: the fields under test are
    # created there, and driving the whole service lifecycle would test the kernel
    # instead of the reconciliation.
    service._fill_samples = deque(maxlen=int(config["cost_reconcile_window"]))
    service._cost_model = cost_model_from_config(MODEL)
    service._cost_reconcile_min_samples = int(config["cost_reconcile_min_samples"])
    service._cost_report = None
    return service


def _fill(service: OpsMonitorService, **kwargs) -> None:
    payload = {"symbol": "BTCUSDT", "side": "buy", "quantity": 0.1, "price": 60000.0,
               "fee": 0.3, **kwargs.pop("payload", {})}
    order = kwargs.pop("order", {})
    service._sample_fill(
        symbol=payload["symbol"],
        side=payload["side"],
        quantity=payload["quantity"],
        price=payload["price"],
        fee=payload["fee"],
        payload=payload,
        order=order,
    )


def test_a_fill_with_a_reference_price_is_kept():
    service = _service()
    _fill(service, payload={"reference_price": 59990.0})
    assert len(service._fill_samples) == 1


def test_a_fill_without_a_reference_price_is_excluded_not_zeroed():
    """The core correctness point.

    Accepting it and defaulting the reference to the fill price would report
    slippage of exactly 0.0 bps for every such fill, and p50/p90 would be dragged
    toward zero by however many fills the feed could not price.
    """
    service = _service()
    _fill(service)
    assert len(service._fill_samples) == 0


def test_degenerate_fills_are_excluded():
    service = _service()
    for bad in (
        {"quantity": 0.0, "reference_price": 60000.0},
        {"price": 0.0, "reference_price": 60000.0},
        {"quantity": 0.1, "reference_price": 0.0},
    ):
        _fill(service, payload=bad)
    assert len(service._fill_samples) == 0


def test_sample_window_is_bounded():
    """An unbounded list in a long-running process is a leak, not a feature."""
    service = _service(cost_reconcile_window=3)
    for _ in range(10):
        _fill(service, payload={"reference_price": 59990.0})
    assert len(service._fill_samples) == 3


@pytest.mark.asyncio
async def test_report_declares_insufficiency_instead_of_a_confident_number():
    service = _service(cost_reconcile_min_samples=5)
    for _ in range(2):
        _fill(service, payload={"reference_price": 59990.0})

    report = await service.reconcile_costs()

    assert report["sufficient"] is False
    assert report["samples"] == 2
    assert report["understates"] is False
    assert "need 5" in report["note"]
    assert report["sampled_fills"] == 2


@pytest.mark.asyncio
async def test_report_compares_observed_against_the_model_when_sufficient():
    service = _service(cost_reconcile_min_samples=3)
    # Fills that slipped 60 bps against a 2 bps model.
    for _ in range(4):
        _fill(service, payload={"reference_price": 60000.0 - 360.0, "price": 60000.0})

    report = await service.reconcile_costs()

    assert report["sufficient"] is True
    assert report["samples"] == 4
    assert report["model_slippage_bps"] == pytest.approx(2.0)
    assert report["observed_p50_bps"] > 50.0
    assert report["understates"] is True
    assert report["alert"] == "cost_model_understates"


@pytest.mark.asyncio
async def test_model_in_force_is_reported_alongside_the_comparison():
    """An operator needs to see what was compared against what."""
    service = _service(cost_reconcile_min_samples=1)
    _fill(service, payload={"reference_price": 60000.0})
    report = await service.reconcile_costs()
    assert report["model"]["total_bps"] == pytest.approx(7.0)
    assert report["model"]["fee_bps"] == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_initialize_reads_the_real_config_block_not_the_fallback():
    """End to end through `on_initialize`, because that is where the wiring lives.

    The defaults equal the shipped values, so a unit test on the helper cannot
    distinguish "read the config" from "fell back to the same numbers". A config
    with different numbers makes the two distinguishable, which is the only way to
    prove the service is actually reading the file.
    """
    import yaml
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    raw = yaml.safe_load(
        (root / "config" / "trading_bot.yaml").read_text(encoding="utf-8")
    )
    block = dict(raw["services"]["phantom_service"])
    expected = float(block["fee_bps"]) + float(block["slippage_bps"])

    # Distinct from both the module defaults and the shipped values, so the only way
    # to pass is to have actually read the mapping handed in. Verified below by
    # mutation, which is what makes this a real check rather than a tautology.
    probe = {"fee_bps": 111.0, "slippage_bps": 7.0}

    async def _run(block_to_serve: dict):
        core = MagicMock()
        core.config.get = AsyncMock(
            side_effect=lambda key, default=None: (
                block_to_serve if key == "services.phantom_service" else default
            )
        )
        core.data_dir = root / "data"
        core.service_registry = {}
        service = OpsMonitorService(core, "ops_monitor_service")
        service.config = {
            "cost_reconcile_min_samples": 5,
            "cost_reconcile_window": 20,
            "cost_reconcile_every_sec": 3600.0,
            "freeze_on_data_anomaly": False,
            "expected_candle_sec": 60,
            "stale_feed_sec": 180,
            "stale_tolerance": 3,
        }
        await service.on_initialize()
        return service

    served = await _run(block)
    assert served._cost_model["fee_bps"] == pytest.approx(float(block["fee_bps"]))
    assert served._cost_model["total_bps"] == pytest.approx(expected)
    assert isinstance(served._fill_samples, deque)
    assert served._fill_samples.maxlen == 20

    # Mutation: a different config must produce a different model. Without this the
    # assertions above would also hold if the service ignored its input entirely.
    probed = await _run(probe)
    assert probed._cost_model["fee_bps"] == pytest.approx(111.0)
    assert probed._cost_model["total_bps"] == pytest.approx(118.0)