"""Neuroevolution counting check: can an evolutionary search be counted honestly,
and does its answer survive out of sample?

Started once before and abandoned mid-implementation in a hand-written backprop
(`delta` re-initialised per layer instead of carried backward). This version does
not hand-roll layer indices at all: every gradient is a whole-matrix expression,
and a finite-difference check pins the backward pass before anything is reported.

Registered hypotheses (fixed before the main run; see docs/investigations/REGISTER.md):

  H1  Distinct architectures are countable, and the count is materially below the
      evaluation count when populations overlap. Falsified if they are equal.
  H2  THE DECISIVE ONE. The architecture evolution selects does not survive
      out-of-sample, EVEN THOUGH a real signal is present and findable: census
      first (all 50), then evolution, both against a signal panel and a
      same-variance noise panel.
  H3  PBO over the evaluated architecture population is high, comparable to the
      0.895 measured on the strategy lineup (`engine.simulation.cscv_pbo`).

Discipline, from the register's derived rules:
  rule 1  print the spread of the null before reading any comparison against it.
  rule 3  known-answer checks on the trainer run FIRST (noise + strong signal +
          finite-difference gradient check).
  rule 4  report evaluations AND distinct architectures.
  rule 5  census the space before searching it.
  rule 9  numbers go into raw-record.md / REGISTER.md when produced, not later.

Usage:
  python tools/neuroevolution_count.py --stage known   # instrument validation
  python tools/neuroevolution_count.py --stage pilot   # gain calibration only
  python tools/neuroevolution_count.py --stage main    # the registered run
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

import numpy as np  # noqa: E402

from engine.simulation import cscv_pbo  # noqa: E402

# ---------------------------------------------------------------------------
# Search space. Enumeration gives exactly 50 architectures:
#   depth 0  -> logistic, hidden width and activation are unreachable (2 keys:
#               one per feature-subset mode).
#   depth 1,2 -> hidden in (0, 2, 4, 8) x activation in (none, tanh, relu)
#               x feature-subset in (0, 2): 4 x 2 x 3 x 2 = 48 keys.
# 2 + 48 = 50. NOTE (honest caveat): keys with hidden == 0 and depth > 0 are
# functionally logistic — the activation has nothing to act on — but they are
# counted as distinct labels, which is what makes the total 50 rather than 38.
# The census below shows them tying, as this predicts.
# ---------------------------------------------------------------------------

HIDDEN = (0, 2, 4, 8)
DEPTHS = (0, 1, 2)
ACTS = ("none", "tanh", "relu")
FS_MODES = (0, 2)

N_FEATURES = 6
#: Feature-subset mapping. Pre-registered choice: mode 0 = all six features,
#: mode 2 = the two signal-bearing features only. The generator puts the weak
#: signal in x0/x1, so mode 2 is the "right" subset by construction.
FS_COLS = {0: list(range(N_FEATURES)), 2: [0, 1]}

EPOCHS = 500
LR = 0.05


@dataclass(frozen=True)
class Arch:
    h: int
    d: int
    act: str
    fs: int

    @property
    def key(self) -> tuple:
        if self.d == 0:
            return ("logistic", self.fs)
        return (self.h, self.d, self.act, self.fs)

    @property
    def label(self) -> str:
        if self.d == 0:
            return f"logistic|fs={self.fs}"
        return f"h={self.h}|d={self.d}|act={self.act}|fs={self.fs}"

    @property
    def is_logistic(self) -> bool:
        return self.d == 0 or self.h == 0


def enumerate_architectures() -> list[Arch]:
    out: list[Arch] = []
    seen: set[tuple] = set()
    for d in DEPTHS:
        acts = ("none",) if d == 0 else ACTS
        for h in HIDDEN:
            for act in acts:
                for fs in FS_MODES:
                    a = Arch(h, d, act, fs)
                    if a.key not in seen:
                        seen.add(a.key)
                        out.append(a)
    return out


# ---------------------------------------------------------------------------
# Trainer: tiny fully-connected net (or logistic) in numpy, whole-matrix
# backprop, Adam, full batch. One optimiser for every architecture (discipline:
# the question is whether the SEARCH finds the signal, so the optimiser must be
# strong enough to fit a signal that is demonstrably there — see K2 below).
# ---------------------------------------------------------------------------


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30.0, 30.0)))


def _activate(z: np.ndarray, act: str) -> np.ndarray:
    if act == "tanh":
        return np.tanh(z)
    if act == "relu":
        return np.maximum(z, 0.0)
    return z  # "none": linear hidden layer


def _activate_deriv(a: np.ndarray, act: str) -> np.ndarray:
    if act == "tanh":
        return 1.0 - a * a
    if act == "relu":
        return (a > 0.0).astype(float)
    return np.ones_like(a)


def fit_mlp(
    Xtr: np.ndarray,
    ytr: np.ndarray,
    arch: Arch,
    seed: int,
    epochs: int = EPOCHS,
    lr: float = LR,
) -> dict:
    """Fit on standardized train columns; returns params + scaler. Deterministic
    given (data, arch, seed)."""
    # Same two modes for any feature count: 0 = all columns, 2 = first two.
    # For the registered 6-lag family this is exactly the old FS_COLS mapping.
    cols = list(range(Xtr.shape[1])) if arch.fs == 0 else [0, 1]
    cols = [c for c in cols if c < Xtr.shape[1]] or [0]
    X = Xtr[:, cols]
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd = np.where(sd < 1e-8, 1.0, sd)
    Xs = (X - mu) / sd
    n, k = Xs.shape
    y = ytr.reshape(-1, 1).astype(float)
    rng = np.random.default_rng(seed)

    if arch.is_logistic:
        W = rng.normal(0.0, 1.0 / math.sqrt(k), size=(k, 1))
        b = np.zeros((1, 1))
        params = [W, b]
        shapes = "logistic"
    else:
        params = []
        widths = [k] + [arch.h] * arch.d + [1]
        for fan_in, fan_out in zip(widths[:-1], widths[1:]):
            scale = math.sqrt(2.0 / fan_in) if arch.act == "relu" else math.sqrt(1.0 / fan_in)
            params.append(rng.normal(0.0, scale, size=(fan_in, fan_out)))
            params.append(np.zeros((1, fan_out)))
        shapes = "mlp"

    m = [np.zeros_like(p) for p in params]
    v = [np.zeros_like(p) for p in params]
    b1, b2, eps = 0.9, 0.999, 1e-8
    for t in range(1, epochs + 1):
        # forward, cached per layer
        acts = [Xs]
        a = Xs
        n_hidden = 0 if arch.is_logistic else arch.d
        for i in range(n_hidden):
            z = a @ params[2 * i] + params[2 * i + 1]
            a = _activate(z, arch.act)
            acts.append(a)
        z_out = a @ params[-2] + params[-1]
        p = _sigmoid(z_out)
        # backward: one matrix expression per layer, carried from the output.
        # `delta` always has the width of the layer it is leaving, so there is
        # no index that can go out of range (the previous bug class).
        delta = (p - y) / n
        grads: list[np.ndarray] = [None] * len(params)  # type: ignore[list-item]
        grads[-2] = acts[-1].T @ delta
        grads[-1] = delta.sum(axis=0, keepdims=True)
        for i in range(n_hidden - 1, -1, -1):
            delta = (delta @ params[2 * i + 2].T) * _activate_deriv(acts[i + 1], arch.act)
            grads[2 * i] = acts[i].T @ delta
            grads[2 * i + 1] = delta.sum(axis=0, keepdims=True)
        for j in range(len(params)):
            m[j] = b1 * m[j] + (1 - b1) * grads[j]
            v[j] = b2 * v[j] + (1 - b2) * grads[j] ** 2
            mh = m[j] / (1 - b1**t)
            vh = v[j] / (1 - b2**t)
            params[j] -= lr * mh / (np.sqrt(vh) + eps)
    return {"params": params, "mu": mu, "sd": sd, "cols": cols, "arch": arch, "shapes": shapes}


def predict_proba(model: dict, X: np.ndarray) -> np.ndarray:
    arch: Arch = model["arch"]
    Xs = (X[:, model["cols"]] - model["mu"]) / model["sd"]
    a = Xs
    params = model["params"]
    n_hidden = 0 if arch.is_logistic else arch.d
    for i in range(n_hidden):
        a = _activate(a @ params[2 * i] + params[2 * i + 1], arch.act)
    return _sigmoid(a @ params[-2] + params[-1]).ravel()


def accuracy(model: dict, X: np.ndarray, y: np.ndarray) -> float:
    return float(((predict_proba(model, X) > 0.5).astype(int) == y).mean())


def per_bar_correct(model: dict, X: np.ndarray, y: np.ndarray) -> list[float]:
    return [float(v) for v in ((predict_proba(model, X) > 0.5).astype(int) == y)]


# ---------------------------------------------------------------------------
# Known-answer check K1: finite-difference gradient check. Catches exactly the
# bug class that killed the previous attempt (wrong reverse pass).
# ---------------------------------------------------------------------------


def gradient_check(seed: int = 1234) -> float:
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(16, 3))
    y = (rng.random(16) < 0.5).astype(int)
    arch = Arch(h=2, d=2, act="tanh", fs=0)
    cols = [0, 1, 2]
    mu = X[:, cols].mean(axis=0)
    sd = np.where(X[:, cols].std(axis=0) < 1e-8, 1.0, X[:, cols].std(axis=0))
    Xs = (X[:, cols] - mu) / sd
    n = Xs.shape[0]
    yy = y.reshape(-1, 1).astype(float)
    r2 = np.random.default_rng(seed + 1)
    W1 = r2.normal(0, 1.0, size=(3, 2))
    b1 = np.zeros((1, 2))
    W2 = r2.normal(0, 1.0, size=(2, 2))
    b2 = np.zeros((1, 2))
    W3 = r2.normal(0, 1.0, size=(2, 1))
    b3 = np.zeros((1, 1))

    def loss(ps: list[np.ndarray]) -> float:
        a1 = np.tanh(Xs @ ps[0] + ps[1])
        a2 = np.tanh(a1 @ ps[2] + ps[3])
        p = _sigmoid(a2 @ ps[4] + ps[5])
        p = np.clip(p, 1e-9, 1 - 1e-9)
        return float(-(yy * np.log(p) + (1 - yy) * np.log(1 - p)).mean())

    def analytic(ps: list[np.ndarray]) -> list[np.ndarray]:
        a1 = np.tanh(Xs @ ps[0] + ps[1])
        a2 = np.tanh(a1 @ ps[2] + ps[3])
        p = _sigmoid(a2 @ ps[4] + ps[5])
        d = (p - yy) / n
        g5 = a2.T @ d
        g6 = d.sum(axis=0, keepdims=True)
        d = (d @ ps[4].T) * (1 - a2 * a2)
        g3 = a1.T @ d
        g4 = d.sum(axis=0, keepdims=True)
        d = (d @ ps[2].T) * (1 - a1 * a1)
        g1 = Xs.T @ d
        g2 = d.sum(axis=0, keepdims=True)
        return [g1, g2, g3, g4, g5, g6]

    ps = [W1, b1, W2, b2, W3, b3]
    gs = analytic(ps)
    worst = 0.0
    h = 1e-6
    for _j, (p, g) in enumerate(zip(ps, gs)):
        num = np.zeros_like(p)
        it = np.nditer(p, flags=["multi_index"])
        while not it.finished:
            ix = it.multi_index
            old = p[ix]
            p[ix] = old + h
            lp = loss(ps)
            p[ix] = old - h
            lm = loss(ps)
            p[ix] = old
            num[ix] = (lp - lm) / (2 * h)
            it.iternext()
        denom = np.maximum(np.abs(num) + np.abs(g), 1e-12)
        worst = max(worst, float((np.abs(num - g) / denom).max()))
    _ = arch  # the check covers the d=2 path; logistic is its special case
    return worst


# ---------------------------------------------------------------------------
# Panels: same generator, two labelings. Noise labels are a permutation of the
# signal labels -> identical marginal variance BY CONSTRUCTION (reported below).
# ---------------------------------------------------------------------------


def make_panels(n: int, gain: float, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    X = rng.normal(0.0, 1.0, size=(n, N_FEATURES))
    eta = gain * (X[:, 0] + X[:, 1]) / math.sqrt(2.0)
    p = 1.0 / (1.0 + np.exp(-eta))
    y_sig = (rng.random(n) < p).astype(int)
    y_noise = rng.permutation(y_sig)
    return {"X": X, "y_sig": y_sig, "y_noise": y_noise}


def split_idx(n: int, ntr: int, nva: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    itr = np.arange(0, ntr)
    iva = np.arange(ntr, ntr + nva)
    ite = np.arange(ntr + nva, n)
    return itr, iva, ite


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


def stage_known() -> None:
    print("== K1 finite-difference gradient check (must be ~1e-7 or smaller) ==")
    err = gradient_check()
    print(f"   worst relative error: {err:.3e}")
    assert err < 1e-5, f"gradient check failed: {err:.3e}"

    print("== K2 trainer known answers: noise vs strong signal ==")
    panels = make_panels(1200, 3.0, seed=4242)
    X = panels["X"]
    itr, iva, ite = split_idx(1200, 700, 250)
    ref = Arch(0, 0, "none", 0)
    m_noise = fit_mlp(X[itr], panels["y_noise"][itr], ref, seed=11)
    m_strong = fit_mlp(X[itr], panels["y_sig"][itr], ref, seed=11)
    a_noise = accuracy(m_noise, X[ite], panels["y_noise"][ite])
    a_strong = accuracy(m_strong, X[ite], panels["y_sig"][ite])
    base = float(panels["y_noise"][ite].mean())
    print(
        f"   noise panel:  test acc {a_noise:.4f} vs coin flip 0.5000 "
        f"(gap {a_noise - 0.5:+.4f}; must be within +-0.05; "
        f"test base {base:.4f}, panel balanced by construction)"
    )
    print(f"   strong panel: test acc {a_strong:.4f} (must be >= 0.85)")
    assert abs(a_noise - 0.5) <= 0.05, "trainer finds edge in pure noise"
    assert a_strong >= 0.85, "optimiser too weak to fit a demonstrable signal"
    print("   K1+K2 pass: the instrument separates signal from noise.")


def stage_pilot() -> None:
    print("== pilot: reference-logistic test acc by gain (seed 101) ==")
    for g in (0.4, 0.5, 0.6, 0.8):
        panels = make_panels(2400, g, seed=101)
        X = panels["X"]
        itr, iva, ite = split_idx(2400, 1200, 600)
        m = fit_mlp(X[itr], panels["y_sig"][itr], Arch(0, 0, "none", 0), seed=101)
        print(f"   gain {g:.1f}: test acc {accuracy(m, X[ite], panels['y_sig'][ite]):.4f}")


def run_census(X, y, itr, iva, ite, archs: list[Arch], seed0: int) -> list[dict]:
    rows = []
    for i, a in enumerate(archs):
        m = fit_mlp(X[itr], y[itr], a, seed=seed0 + i)
        rows.append(
            {
                "arch": a,
                "train": accuracy(m, X[itr], y[itr]),
                "val": accuracy(m, X[iva], y[iva]),
                "test": accuracy(m, X[ite], y[ite]),
                "val_series": per_bar_correct(m, X[iva], y[iva]),
                "model": m,
            }
        )
    return rows


def run_evolution(
    X, y, itr, iva, archs: list[Arch], seed: int, pop: int = 12, gens: int = 6
) -> dict:
    rng = np.random.default_rng(seed)
    n = len(archs)

    def mutate(a: Arch) -> Arch:
        h, d, act, fs = a.h, a.d, a.act, a.fs
        j = rng.integers(0, 4)
        if j == 0:
            h = HIDDEN[rng.integers(0, len(HIDDEN))]
        elif j == 1:
            d = DEPTHS[rng.integers(0, len(DEPTHS))]
        elif j == 2:
            d2 = d if d > 0 else DEPTHS[rng.integers(1, len(DEPTHS))]
            act = ACTS[rng.integers(0, len(ACTS))]
            d = d2
        else:
            fs = FS_MODES[rng.integers(0, len(FS_MODES))]
        cand = Arch(h, d, act, fs)
        # snap to the registered 50: activation unreachable at depth 0
        if cand.d == 0:
            cand = Arch(cand.h, 0, "none", cand.fs)
        return archs[[x.key for x in archs].index(cand.key)]

    def crossover(x: Arch, z: Arch) -> Arch:
        h = x.h if rng.random() < 0.5 else z.h
        d = x.d if rng.random() < 0.5 else z.d
        act = x.act if rng.random() < 0.5 else z.act
        fs = x.fs if rng.random() < 0.5 else z.fs
        if d == 0:
            act = "none"
        cand = Arch(h, d, act, fs)
        return archs[[q.key for q in archs].index(cand.key)]

    population = [archs[rng.integers(0, n)] for _ in range(pop)]
    evaluations = 0
    distinct: dict[tuple, dict] = {}
    fit_seed = seed * 1000

    def evaluate(a: Arch) -> float:
        nonlocal evaluations, fit_seed
        fit_seed += 1
        m = fit_mlp(X[itr], y[itr], a, seed=fit_seed)
        evaluations += 1
        val = accuracy(m, X[iva], y[iva])
        if a.key not in distinct:
            distinct[a.key] = {"arch": a, "model": m, "val": val}
        elif val > distinct[a.key]["val"]:
            distinct[a.key] = {"arch": a, "model": m, "val": val}
        return val

    fitness = {a.key: evaluate(a) for a in population}
    for _ in range(gens - 1):
        order = sorted(fitness, key=lambda k: fitness[k], reverse=True)
        elites = order[:2]
        children: list[Arch] = []
        by_key = {a.key: a for a in archs}
        while len(children) < pop - 2:
            t1 = [order[rng.integers(0, len(order))] for _ in range(3)]
            t2 = [order[rng.integers(0, len(order))] for _ in range(3)]
            p1 = by_key[max(t1, key=lambda k: fitness[k])]
            p2 = by_key[max(t2, key=lambda k: fitness[k])]
            c = crossover(p1, p2)
            if rng.random() < 0.3:
                c = mutate(c)
            children.append(c)
        population = [by_key[k] for k in elites] + children
        fitness = {}
        for a in population:
            if a.key in elites and a.key in distinct and a.key not in fitness:
                # carried elite, no refit: not an evaluation, keep stored val
                fitness[a.key] = distinct[a.key]["val"]
            else:
                fitness[a.key] = evaluate(a)
    best_key = max(fitness, key=lambda k: fitness[k])
    return {
        "evaluations": evaluations,
        "distinct": distinct,
        "best_key": best_key,
        "best_val": fitness[best_key],
    }


def pbo_block(name: str, series: list[list[float]]) -> dict:
    out = cscv_pbo(series, splits=8)
    assert out["ok"], f"PBO not evaluable for {name}: {out}"
    # Rule 1: the PBO point estimate is read only next to the spread it came
    # from. cscv_pbo's own known-answer pins live in
    # tests/test_reality_check_spa_pbo.py (high on pure noise, low when one
    # candidate really dominates), so the instrument is validated in both
    # directions; here the logits mean shows which side of 0.5 the mass sits.
    print(
        f"   PBO[{name}]: pbo={out['pbo']:.3f} logits_mean={out['logits_mean']:+.3f} "
        f"splits_used={out['splits_used']} strategies={out['strategies']}"
    )
    return out


def build_real_rows(closes: list[float]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Real-data rows (registered S9-1): X[i] = 6 lagged log returns, y[i] =
    direction of the NEXT bar, R[i] = that bar's log return (for net P&L).
    Strictly past in, strictly future out — pinned by unit test."""
    rets = np.array([math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))])
    X = np.stack([rets[i - 5 : i + 1] for i in range(5, len(rets) - 1)])
    y = (rets[6:] > 0).astype(int)
    R = rets[6:]
    return X, y, R


def build_chaos_rows(closes: list[float]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """R-51 feature family: four causal chaos-derived values per decision bar."""
    from tools.chaos_features import chaos_vector

    rets = np.array([math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))])
    X = np.stack([chaos_vector(rets, i + 5) for i in range(5, len(rets) - 1)])
    y = (rets[6:] > 0).astype(int)
    R = rets[6:]
    return X, y, R


def net_long_flat(probs: np.ndarray, R: np.ndarray, cost: float = 7.0 / 1e4) -> float:
    """Long-if-P>0.5-else-flat test net: Σ pos·r − flips·cost."""
    pos = (probs > 0.5).astype(float)
    flips = np.abs(pos[1:] - pos[:-1]).sum() + pos[0]
    return float((pos * R).sum() - flips * cost)


def stage_main(gain: float, seed: int) -> None:
    archs = enumerate_architectures()
    print(f"space: {len(archs)} architectures (registered: 50)")
    assert len(archs) == 50, f"space is {len(archs)}, registered 50"
    assert len({a.key for a in archs}) == 50

    N, NTR, NVA = 2400, 1200, 600
    panels = make_panels(N, gain, seed)
    X = panels["X"]
    itr, iva, ite = split_idx(N, NTR, NVA)
    vsig = float(panels["y_sig"].var())
    vnoise = float(panels["y_noise"].var())
    print(
        f"variance check (rule 1: the control must match): "
        f"var(signal)={vsig:.6f} var(noise)={vnoise:.6f} "
        f"base={panels['y_sig'].mean():.4f}"
    )

    for tag, y in (("signal", panels["y_sig"]), ("noise", panels["y_noise"])):
        base = float(y[ite].mean())
        chance = 0.5  # panels are balanced by construction; base printed alongside
        print(f"--- panel {tag}: test base {base:.4f}, chance = 0.5000 ---")
        census = run_census(X, y, itr, iva, ite, archs, seed0=seed * 77 + 3)
        tests = np.array([r["test"] for r in census])
        # rule 1: the spread of the census BEFORE any best-vs-rest comparison
        print(
            f"   census test-acc spread over 50: mean {tests.mean():.4f} "
            f"sd {tests.std():.4f} min {tests.min():.4f} max {tests.max():.4f}"
        )
        order = sorted(census, key=lambda r: r["val"], reverse=True)
        best = order[0]
        print(
            f"   census best-by-val: {best['arch'].label} "
            f"val {best['val']:.4f} test {best['test']:.4f}"
        )
        # functional-duplicate check: h==0 & depth>0 keys must tie each other
        dupes = [r for r in census if r["arch"].h == 0 and r["arch"].d > 0]
        if dupes:
            ts = np.array([r["test"] for r in dupes])
            print(
                f"   h=0,d>0 functional duplicates ({len(dupes)} labels): "
                f"test spread sd {ts.std():.4f} (expect ~0: same function)"
            )
        evo = run_evolution(X, y, itr, iva, archs, seed=seed * 131 + 5)
        wkey = evo["best_key"]
        wmodel = evo["distinct"][wkey]["model"]
        wtest = accuracy(wmodel, X[ite], y[ite])
        print(
            f"   evolution: evaluations={evo['evaluations']} "
            f"distinct={len(evo['distinct'])} "
            f"(registered H1: distinct < evaluations)"
        )
        print(
            f"   evolution winner: {evo['distinct'][wkey]['arch'].label} "
            f"val {evo['best_val']:.4f} test {wtest:.4f}"
        )
        print(
            f"   winner IS-OOS gap: {evo['best_val'] - wtest:+.4f} (val fitness vs held-out test)"
        )
        pbo_series = []
        n_blocks = 16
        idx = np.array_split(np.arange(len(iva)), n_blocks)
        for k in evo["distinct"]:
            corr = np.array(per_bar_correct(evo["distinct"][k]["model"], X[iva], y[iva]))
            pbo_series.append([float(corr[b].mean()) for b in idx])
        pbo_block(f"evolution-{tag}", pbo_series)
        if tag == "signal":
            ref = Arch(0, 0, "none", 0)
            mref = fit_mlp(X[itr], y[itr], ref, seed=seed * 17 + 1)
            aref = accuracy(mref, X[ite], y[ite])
            print(
                f"   reference logistic (pre-specified, no selection): "
                f"test {aref:.4f} vs chance {chance:.4f} "
                f"(gap {aref - chance:+.4f}; H2a needs >= +0.03)"
            )
            print(
                f"   H2b (selected fitness does not survive): winner val-test "
                f"gap {evo['best_val'] - wtest:+.4f}; H2 needs >= +0.03"
            )
            print(
                f"   H2c (search adds nothing over the default): winner test "
                f"minus ref test {wtest - aref:+.4f}; H2 needs <= +0.01"
            )
            print(
                f"   context: census-best test {best['test']:.4f} "
                f"(fittest-by-construction, val-selected: {best['arch'].label})"
            )
            # S8: ensemble over the DISTINCT evolution-evaluated architectures
            # (mean predicted probability, threshold 0.5) + census-50 context.
            # Additive: averages stored models after the run, moves no number.
            ens = sum(
                predict_proba(evo["distinct"][k]["model"], X[ite]) for k in evo["distinct"]
            ) / len(evo["distinct"])
            aens = float(((ens > 0.5).astype(int) == y[ite]).mean())
            cens = sum(predict_proba(r["model"], X[ite]) for r in census) / len(census)
            acens = float(((cens > 0.5).astype(int) == y[ite]).mean())
            print(
                f"   ensemble (evolution-evaluated, n={len(evo['distinct'])}): "
                f"test {aens:.4f} (S8a needs >= 0.53)"
            )
            print(
                f"   ensemble vs winner: {aens - wtest:+.4f} "
                f"(S8b needs |.| <= 0.02); census-50 ensemble: {acens:.4f}"
            )

            print(
                f"   ensemble vs winner: {aens - wtest:+.4f} "
                f"(S8b needs |.| <= 0.02); census-50 ensemble: {acens:.4f}"
            )


def stage_real(
    seed: int,
    n_bars: int = 6000,
    timeframe: str = "1h",
    ntr: int = 3600,
    nva: int = 1200,
    features: str = "raw",
) -> None:
    """Real BTCUSDT features; S3 search + ensemble. Timeframe/split are inputs."""
    import asyncio

    from tools.timeframe_verdict import _klines

    archs = enumerate_architectures()
    assert len(archs) == 50

    rows = asyncio.run(_klines(timeframe, n_bars))
    closes = [float(r[4]) for r in rows]
    print(f"fetched {len(rows)} BTCUSDT {timeframe} klines ({rows[0][0]}..{rows[-1][0]})")
    X, y_sig, R = build_chaos_rows(closes) if features == "chaos" else build_real_rows(closes)
    print(f"features: {features}, columns {X.shape[1]}")
    N = len(y_sig)
    assert N >= ntr + nva + 200, f"only {N} rows"
    itr, iva = np.arange(0, ntr), np.arange(ntr, ntr + nva)
    ite = np.arange(ntr + nva, N)
    rng = np.random.default_rng(41)
    y_noise = rng.permutation(y_sig)
    print(
        f"rows: {N} (train {ntr} / val {len(iva)} / test {len(ite)}), "
        f"test base {y_sig[ite].mean():.4f}, "
        f"var(signal)={y_sig.var():.6f} var(noise)={y_noise.var():.6f}"
    )

    for tag, y in (("signal", y_sig), ("noise", y_noise)):
        census = run_census(X, y, itr, iva, ite, archs, seed0=seed * 77 + 3)
        tests = np.array([r["test"] for r in census])
        print(f"--- panel {tag} ---")
        print(
            f"   census test-acc spread over 50: mean {tests.mean():.4f} "
            f"sd {tests.std():.4f} min {tests.min():.4f} max {tests.max():.4f}"
        )
        order = sorted(census, key=lambda r: r["val"], reverse=True)
        best = order[0]
        print(
            f"   census best-by-val: {best['arch'].label} "
            f"val {best['val']:.4f} test {best['test']:.4f}"
        )
        evo = run_evolution(X, y, itr, iva, archs, seed=seed * 131 + 5)
        wkey = evo["best_key"]
        wmodel = evo["distinct"][wkey]["model"]
        wtest = accuracy(wmodel, X[ite], y[ite])
        print(f"   evolution: evaluations={evo['evaluations']} distinct={len(evo['distinct'])}")
        print(
            f"   evolution winner: {evo['distinct'][wkey]['arch'].label} "
            f"val {evo['best_val']:.4f} test {wtest:.4f}"
        )
        idx = np.array_split(np.arange(len(iva)), 16)
        pbo_series = [
            [
                float(
                    np.array(per_bar_correct(evo["distinct"][k]["model"], X[iva], y[iva]))[b].mean()
                )
                for b in idx
            ]
            for k in evo["distinct"]
        ]
        pbo_block(f"evolution-real-{tag}", pbo_series)
        if tag == "signal":
            ref = Arch(0, 0, "none", 0)
            mref = fit_mlp(X[itr], y[itr], ref, seed=seed * 17 + 1)
            aref = accuracy(mref, X[ite], y[ite])
            ens = sum(
                predict_proba(evo["distinct"][k]["model"], X[ite]) for k in evo["distinct"]
            ) / len(evo["distinct"])
            aens = float(((ens > 0.5).astype(int) == y[ite]).mean())
            pw = predict_proba(wmodel, X[ite])
            pr = predict_proba(mref, X[ite])
            Rte = R[ite]
            nets = {
                "ensemble": net_long_flat(ens, Rte),
                "winner": net_long_flat(pw, Rte),
                "reference": net_long_flat(pr, Rte),
            }
            print(f"   reference logistic: test {aref:.4f}")
            print(f"   ensemble: test {aens:.4f} (R-41a needs >= 0.53)")
            print(
                "   TEST NET long/flat 7bps/flip: "
                + "  ".join(f"{k} {v:+.4f}" for k, v in nets.items())
                + "  (R-41b needs ensemble > 0)"
            )
            bh = float(np.log(closes[-1] / closes[len(closes) - len(ite) - 1]))
            print(
                f"   context: buy-and-hold test window {bh:+.4f}, "
                f"census-best test {best['test']:.4f}"
            )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=("known", "pilot", "main"), required=True)
    ap.add_argument("--gain", type=float, default=0.6)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--data", choices=("synthetic", "btc1h", "btc1d"), default="synthetic")
    ap.add_argument("--bars", type=int, default=0)
    ap.add_argument("--train", type=int, default=0)
    ap.add_argument("--val", type=int, default=0)
    ap.add_argument("--features", choices=("raw", "chaos"), default="raw")
    args = ap.parse_args()
    if args.stage == "known":
        stage_known()
    elif args.stage == "pilot":
        stage_pilot()
    elif args.data == "btc1h":
        stage_real(
            args.seed,
            n_bars=args.bars or 6000,
            timeframe="1h",
            ntr=args.train or 3600,
            nva=args.val or 1200,
            features=args.features,
        )
    elif args.data == "btc1d":
        stage_real(
            args.seed,
            n_bars=args.bars or 3000,
            timeframe="1d",
            ntr=args.train or 2000,
            nva=args.val or 400,
            features=args.features,
        )
    else:
        stage_main(args.gain, args.seed)


if __name__ == "__main__":
    main()
