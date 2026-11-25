"""Chaos features must be causal and finite on real-shaped return series."""

from __future__ import annotations

import numpy as np

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from tools.chaos_features import (  # noqa: E402
    box_dimension,
    chaos_vector,
    delay_embed,
    largest_lyapunov,
    spectral_entropy,
)


def test_delay_embedding_is_causal_and_shaped():
    values = np.arange(10, dtype=float)
    points = delay_embed(values, dim=3, tau=1)
    assert points.shape == (8, 3)
    assert list(points[0]) == [0.0, 1.0, 2.0]


def test_features_are_finite_on_noise_and_signal():
    rng = np.random.default_rng(7)
    for values in (rng.normal(size=400), np.sin(np.arange(400) / 7.0)):
        v = chaos_vector(values, 300)
        assert v.shape == (4,)
        assert np.all(np.isfinite(v))


def test_future_return_cannot_change_a_past_feature():
    rng = np.random.default_rng(11)
    values = rng.normal(size=400)
    before = chaos_vector(values, 300).copy()
    changed = values.copy()
    changed[350] += 5.0
    after = chaos_vector(changed, 300)
    assert np.allclose(before, after)


def test_degenerate_window_reads_zero_not_nan():
    flat = np.zeros(400)
    assert largest_lyapunov(delay_embed(flat)) == 0.0
    assert box_dimension(delay_embed(flat)) == 0.0
    assert spectral_entropy(flat) == 0.0
