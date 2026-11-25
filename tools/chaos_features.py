"""Causal chaos-derived features from past returns (registered S24).

The aphina-server numeric engine operates on defined maps/ODE systems, not on
a market series, so a direct copy cannot produce a feature. This module is the
honest bridge: delay embedding of a trailing return window, nearest-neighbour
log-divergence (Lyapunov-style), box-counting dimension and spectral entropy.
Every value uses ONLY bars at or before the decision bar.
"""

from __future__ import annotations

import math

import numpy as np

WINDOW = 120
DIM = 3
TAU = 1
NEIGHBOURS = 5


def delay_embed(values: np.ndarray, dim: int = DIM, tau: int = TAU) -> np.ndarray:
    """Delay-coordinate points from a causal window."""
    n = len(values) - (dim - 1) * tau
    if n <= 0:
        return np.empty((0, dim))
    return np.stack([values[i * tau : i * tau + n] for i in range(dim)], axis=1)


def largest_lyapunov(points: np.ndarray, neighbours: int = NEIGHBOURS) -> float:
    """Mean log divergence to nearest neighbours over one embedding step.

    Rosenstein-style estimate: for each point, find nearby points in the past,
    measure how their separation evolves one step later. Non-positive or
    non-finite divergences contribute nothing, so a degenerate window reads 0.
    """
    if len(points) < neighbours + 2:
        return 0.0
    terms: list[float] = []
    for i in range(1, len(points) - 1):
        d = np.linalg.norm(points[:i] - points[i], axis=1)
        idx = np.argsort(d)[:neighbours]
        for j in idx:
            if d[j] <= 0:
                continue
            later = np.linalg.norm(points[j + 1] - points[i + 1])
            if later > 0 and math.isfinite(later):
                terms.append(math.log(later / d[j]))
    return float(np.mean(terms)) if terms else 0.0


def box_dimension(points: np.ndarray) -> float:
    """Lower-bound box-counting dimension over three grid resolutions."""
    if len(points) < 8:
        return 0.0
    lo = np.min(points, axis=0)
    hi = np.max(points, axis=0)
    span = np.where(hi - lo > 0, hi - lo, 1.0)
    counts: list[float] = []
    scales: list[float] = []
    for boxes in (4, 8, 16):
        grid = np.floor((points - lo) / span * boxes).astype(int)
        keys = {tuple(row) for row in grid}
        counts.append(math.log(max(1, len(keys))))
        scales.append(math.log(1.0 / boxes))
    xs = np.array(scales)
    ys = np.array(counts)
    denom = float(np.sum((xs - xs.mean()) ** 2))
    if denom <= 0:
        return 0.0
    return float(np.sum((xs - xs.mean()) * (ys - ys.mean())) / denom)


def spectral_entropy(values: np.ndarray) -> float:
    """Shannon entropy of the normalized power spectrum of a causal window."""
    if len(values) < 8:
        return 0.0
    power = np.abs(np.fft.rfft(values - np.mean(values))) ** 2
    total = float(np.sum(power))
    if total <= 0:
        return 0.0
    p = power / total
    p = p[p > 0]
    return float(-np.sum(p * np.log(p)) / math.log(len(p))) if len(p) > 1 else 0.0


def chaos_vector(rets: np.ndarray, index: int, window: int = WINDOW) -> np.ndarray:
    """Four features at bar `index`, using returns strictly up to that bar."""
    start = max(0, index - window + 1)
    w = rets[start : index + 1]
    points = delay_embed(w)
    return np.array(
        [
            largest_lyapunov(points),
            box_dimension(points),
            spectral_entropy(w),
            float(np.mean(w)) if len(w) else 0.0,
        ],
        dtype=float,
    )
