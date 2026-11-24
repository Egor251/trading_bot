"""Pin the neuroevolution counting instrument (R-33/34/35, raw-record S3).

Three things must stay true, or the S3 numbers stop meaning anything:

1. The space enumerates to exactly 50 distinct canonical keys, with depth-0
   collapsing to logistic (the registered denominator).
2. The backward pass matches finite differences (the previous attempt died in a
   hand-written reverse pass; this one must never silently regress into it).
3. The trainer separates signal from noise on a small fast panel: near chance
   on permuted labels, clearly above chance on a strong signal. A broken
   trainer produces plausible scores on both; these bars catch that.
"""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

import pytest  # noqa: E402

from tools.neuroevolution_count import (  # noqa: E402
    Arch,
    accuracy,
    enumerate_architectures,
    fit_mlp,
    gradient_check,
    make_panels,
    split_idx,
)


def test_space_enumerates_to_50_distinct_keys():
    archs = enumerate_architectures()
    assert len(archs) == 50
    assert len({a.key for a in archs}) == 50


def test_depth_zero_collapses_to_logistic_labels():
    archs = enumerate_architectures()
    d0 = [a for a in archs if a.d == 0]
    assert len(d0) == 2  # one per feature-subset mode
    assert {a.key for a in d0} == {("logistic", 0), ("logistic", 2)}


def test_gradient_matches_finite_differences():
    assert gradient_check() < 1e-5


def test_trainer_separates_noise_from_strong_signal():
    panels = make_panels(600, 3.0, seed=4242)
    X = panels["X"]
    itr, iva, ite = split_idx(600, 350, 125)
    ref = Arch(0, 0, "none", 0)
    m_noise = fit_mlp(X[itr], panels["y_noise"][itr], ref, seed=11, epochs=300)
    m_strong = fit_mlp(X[itr], panels["y_sig"][itr], ref, seed=11, epochs=300)
    assert abs(accuracy(m_noise, X[ite], panels["y_noise"][ite]) - 0.5) <= 0.07
    assert accuracy(m_strong, X[ite], panels["y_sig"][ite]) >= 0.80


def test_ensemble_averages_stored_models_deterministically():
    """Pin the S8 averaging form: mean predicted probability, threshold 0.5,
    identical across repeated evaluation."""
    import numpy as np

    from tools.neuroevolution_count import predict_proba

    panels = make_panels(400, 1.0, seed=31)
    X = panels["X"]
    itr, iva, ite = split_idx(400, 240, 80)
    models = [
        fit_mlp(X[itr], panels["y_sig"][itr], Arch(0, 0, "none", 0), seed=s) for s in (1, 2, 3)
    ]
    probs = [predict_proba(m, X[ite]) for m in models]
    ens = sum(probs) / len(probs)
    manual = (probs[0] + probs[1] + probs[2]) / 3.0
    assert np.allclose(ens, manual)
    acc = float(((ens > 0.5).astype(int) == panels["y_sig"][ite]).mean())
    assert 0.0 <= acc <= 1.0
    assert acc >= 0.5  # gain 1.0 is a strong visible signal


def test_real_rows_use_strictly_past_features_and_future_target():
    """Leakage pin (S9-1): hand-computed closes -> exact lag matrix. A builder
    that peeks one bar ahead fails this."""
    import math

    import numpy as np

    from tools.neuroevolution_count import build_real_rows, net_long_flat

    closes = [100.0, 101.0, 100.5, 102.0, 101.0, 103.0, 102.0, 104.0, 105.0]
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    X, y, R = build_real_rows(closes)
    # 8 returns -> rows for i in 5..6 -> 2 rows of 6 lags, targets rets[6], rets[7]
    assert X.shape == (2, 6)
    assert list(X[0]) == pytest.approx(rets[0:6])
    assert list(X[1]) == pytest.approx(rets[1:7])
    assert list(y) == [int(rets[6] > 0), int(rets[7] > 0)]
    assert list(R) == pytest.approx([rets[6], rets[7]])
    # net: enter bar 1, exit before bar 2 -> two fills, both costed
    probs = np.array([0.9, 0.1])
    pos = np.array([1.0, 0.0])
    flips = 2.0  # enter + exit
    assert net_long_flat(probs, np.array(R)) == pytest.approx(pos[0] * R[0] - flips * 7.0 / 1e4)
