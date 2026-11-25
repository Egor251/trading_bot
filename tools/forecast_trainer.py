"""Regression forecaster with GA over the registered 50-architecture space.

The network outputs a scalar log-price forecast. Loss is distance-based with a
registered asymmetric factor for downside surprises. Whole-matrix gradients,
no hand-rolled layer indices; finite-difference pinned before any run.
"""

from __future__ import annotations

import math

import numpy as np

from tools.neuroevolution_count import Arch, enumerate_architectures

DOWNSIDE_FACTOR = 2.0


def _act(z: np.ndarray, name: str) -> np.ndarray:
    if name == "tanh":
        return np.tanh(z)
    if name == "relu":
        return np.maximum(z, 0.0)
    return z


def _dact(a: np.ndarray, name: str) -> np.ndarray:
    if name == "tanh":
        return 1.0 - a * a
    if name == "relu":
        return (a > 0.0).astype(float)
    return np.ones_like(a)


def _asym_weights(error: np.ndarray) -> np.ndarray:
    # error = y - pred; adverse (realized below forecast) costs twice as much.
    return np.where(error < 0.0, DOWNSIDE_FACTOR, 1.0)


def loss(pred: np.ndarray, y: np.ndarray) -> float:
    e = y - pred
    return float(np.mean(np.abs(e) * _asym_weights(e)))


def fit_regressor(
    Xtr: np.ndarray, ytr: np.ndarray, arch: Arch, seed: int,
    epochs: int = 500, lr: float = 0.05,
) -> dict:
    cols = list(range(Xtr.shape[1])) if arch.fs == 0 else [0, 1]
    X = Xtr[:, cols]
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd = np.where(sd < 1e-8, 1.0, sd)
    Xs = (X - mu) / sd
    y = ytr.reshape(-1, 1).astype(float)
    n = Xs.shape[0]
    rng = np.random.default_rng(seed)
    k = Xs.shape[1]
    depths = 0 if arch.h == 0 else arch.d
    params: list[np.ndarray] = []
    widths = [k] + [arch.h] * depths + [1]
    for fi, fo in zip(widths[:-1], widths[1:]):
        scale = math.sqrt(2.0 / fi) if arch.act == "relu" else math.sqrt(1.0 / fi)
        params.extend([rng.normal(0.0, scale, size=(fi, fo)), np.zeros((1, fo))])
    m = [np.zeros_like(p) for p in params]
    v = [np.zeros_like(p) for p in params]
    b1, b2, eps = 0.9, 0.999, 1e-8
    for t in range(1, epochs + 1):
        acts = [Xs]
        a = Xs
        for i in range(depths):
            a = _act(a @ params[2 * i] + params[2 * i + 1], arch.act)
            acts.append(a)
        pred = a @ params[-2] + params[-1]
        e = y - pred
        g = -_asym_weights(e) * np.sign(e) / n
        grads: list[np.ndarray] = [None] * len(params)  # type: ignore[list-item]
        grads[-2] = acts[-1].T @ g
        grads[-1] = g.sum(axis=0, keepdims=True)
        for i in range(depths - 1, -1, -1):
            g = (g @ params[2 * i + 2].T) * _dact(acts[i + 1], arch.act)
            grads[2 * i] = acts[i].T @ g
            grads[2 * i + 1] = g.sum(axis=0, keepdims=True)
        for j in range(len(params)):
            m[j] = b1 * m[j] + (1 - b1) * grads[j]
            v[j] = b2 * v[j] + (1 - b2) * grads[j] ** 2
            params[j] -= lr * (m[j] / (1 - b1**t)) / (np.sqrt(v[j] / (1 - b2**t)) + eps)
    return {"params": params, "mu": mu, "sd": sd, "cols": cols, "arch": arch}


def predict(model: dict, X: np.ndarray) -> np.ndarray:
    a = (X[:, model["cols"]] - model["mu"]) / model["sd"]
    depths = 0 if model["arch"].h == 0 else model["arch"].d
    for i in range(depths):
        a = _act(a @ model["params"][2 * i] + model["params"][2 * i + 1], model["arch"].act)
    return (a @ model["params"][-2] + model["params"][-1]).ravel()


def gradient_check(seed: int = 62) -> float:
    """Finite-difference check on the symmetric region (realized above forecast)."""
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(12, 3))
    y = 0.5 * X[:, 0] + 0.2
    arch = Arch(h=2, d=2, act="tanh", fs=0)
    model = fit_regressor(X[:10], y[:10], arch, seed, epochs=1, lr=0.0)

    def objective(params: list[np.ndarray]) -> float:
        a = X[:10]
        for i in range(arch.d):
            a = _act(a @ params[2 * i] + params[2 * i + 1], arch.act)
        pred = a @ params[-2] + params[-1]
        e = y[:10].reshape(-1, 1) - pred
        return float(np.mean(np.where(e < 0, DOWNSIDE_FACTOR, 1.0) * np.abs(e)))

    # Analytic gradient at the last output weight from one Adam-free forward.
    a = X[:10]
    for i in range(arch.d):
        a = _act(a @ model["params"][2 * i] + model["params"][2 * i + 1], arch.act)
    e = y[:10].reshape(-1, 1) - (a @ model["params"][-2] + model["params"][-1])
    g = -_asym_weights(e) * np.sign(e) / len(e)
    analytic = float((a.T @ g)[-1, 0])
    w = model["params"][-2].copy()
    h = 1e-6
    original = w[-1, 0]
    w[-1, 0] = original + h
    model["params"][-2] = w
    up = objective(model["params"])
    w[-1, 0] = original - h
    model["params"][-2] = w
    down = objective(model["params"])
    model["params"][-2][-1, 0] = original
    numeric = (up - down) / (2 * h)
    return abs(analytic - numeric) / max(1.0, abs(numeric))


def run_forecast_evolution(
    X: np.ndarray, y: np.ndarray, itr: np.ndarray, iva: np.ndarray,
    archs: list[Arch] | None = None, seed: int = 62,
    pop: int = 12, gens: int = 6,
) -> dict:
    archs = archs or enumerate_architectures()
    idx = {a.key: a for a in archs}
    rng = np.random.default_rng(seed)
    population = [archs[rng.integers(0, len(archs))] for _ in range(pop)]
    evaluations = 0
    distinct: dict[str, dict] = {}
    fit_seed = seed * 1000

    def fit(a: Arch) -> dict:
        nonlocal fit_seed, evaluations
        fit_seed += 1
        evaluations += 1
        model = fit_regressor(X[itr], y[itr], a, fit_seed)
        val = loss(predict(model, X[iva]), y[iva])
        if a.key not in distinct or val < distinct[a.key]["val"]:
            distinct[a.key] = {"model": model, "val": val}
        return val

    scores = {a.key: fit(a) for a in population}
    for _ in range(gens - 1):
        order = sorted(scores, key=lambda k: scores[k])[:2]
        keys = list(idx)
        children = [idx[keys[rng.integers(0, len(keys))]] for _ in range(pop - 2)]
        population = [idx[k] for k in order] + children
        scores = {a.key: scores[a.key] if a.key in order else fit(a) for a in population}
    best = min(scores, key=lambda k: scores[k])
    return {"best": best, "best_val": scores[best], "model": distinct[best]["model"],
            "evaluations": evaluations, "distinct": distinct}
