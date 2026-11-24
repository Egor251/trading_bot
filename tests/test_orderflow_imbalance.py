"""Pin the order-flow instrument (R-38, raw-record S6).

Properties, all synthetic and offline: imbalance maps taker-buy share to
[-1, 1]; correlation/t-stat recover an injected slope (K1) and stay null on a
shuffled predictor (K2); a constant predictor is refused, never scored. The live
design (3000 BTCUSDT 1h bars) is exercised by `tools/orderflow_imbalance.py`.
"""

from __future__ import annotations

import random

import pytest

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from tools.orderflow_imbalance import (  # noqa: E402
    corr,
    imbalance,
    trailing_mean,
    tstat,
)


def test_imbalance_maps_share_to_unit_interval():
    rows = [
        [0, "1", "1", "1", "1", "100.0", 0, "1", 0, tb, "1", "0"]
        for tb in ("0.0", "25.0", "50.0", "75.0", "100.0")
    ]
    imb, _ = imbalance(rows)
    # first bar dropped (no prior close); the last four survive, aligned
    assert imb == pytest.approx([-0.5, 0.0, 0.5, 1.0])
    assert len(imb) == 4


def test_k1_sees_injected_slope_and_k2_shuffled_is_null():
    rng = random.Random(913)
    n = 2000
    xs = [rng.uniform(-1.0, 1.0) for _ in range(n)]
    ys = [0.02 * v + rng.gauss(0.0, 0.01) for v in xs]
    assert tstat(corr(xs, ys), n) > 3.0
    sh = list(xs)
    rng.shuffle(sh)
    assert abs(tstat(corr(sh, ys), n)) < 2.5


def test_constant_predictor_is_refused():
    with pytest.raises(ValueError):
        corr([0.5] * 100, [0.1] * 100)


def test_trailing_mean_windows():
    assert trailing_mean([1.0, 2.0, 3.0, 4.0], 2) == pytest.approx([1.5, 2.5, 3.5])
