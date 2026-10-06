"""Configurable walk-forward research: config, selection, bootstrap, provenance."""

from __future__ import annotations

import json
import math
import random
import statistics
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from engine.paths import data_path
from engine.research import (
    buy_hold_oos_metrics,
    expectancy_metrics,
    offline_variant_metrics,
    pick_best_variant,
    slice_series,
    walk_forward_windows,
)


class WalkForwardTooShortError(ValueError):
    """Series cannot produce ``min_folds`` windows with the given train/test/step."""

    def __init__(
        self,
        *,
        bars: int,
        train_bars: int,
        test_bars: int,
        step: int,
        min_folds: int,
        folds_available: int,
        bars_needed: int,
    ) -> None:
        self.bars = bars
        self.train_bars = train_bars
        self.test_bars = test_bars
        self.step = step
        self.min_folds = min_folds
        self.folds_available = folds_available
        self.bars_needed = bars_needed
        super().__init__(
            f"walk_forward_too_short: bars={bars} yield {folds_available} folds "
            f"(need min_folds={min_folds}); need ≥{bars_needed} bars for "
            f"train={train_bars} test={test_bars} step={step}"
        )


@dataclass(slots=True)
class WalkForwardConfig:
    """All knobs for a reproducible walk-forward run (YAML / UI / AI)."""

    feed: str = "history"  # history | patterns
    seed: int = 42
    bars: int = 500
    train_bars: int = 60
    test_bars: int = 20
    step: int | None = None
    min_folds: int = 5
    auto_fit_folds: bool = False
    selection_mode: str = "top_k_stable"  # best | top_k_stable
    top_k: int = 3
    min_fold_wins: int = 1
    bootstrap_samples: int = 1000
    bootstrap_ci: float = 0.95
    score_key: str = "pnl"
    persist_report: bool = True
    report_dir: str = field(default_factory=lambda: str(data_path("phantom", "walk_forward")))
    pattern: str = "sharp_rise"
    # Research default 1d — crypto auto-fetch otherwise silently uses 1m.
    timeframe: str = "1d"
    include_baselines: bool = True

    def __post_init__(self) -> None:
        feed = str(self.feed or "history").strip().lower()
        if feed not in {"history", "patterns"}:
            feed = "history"
        object.__setattr__(self, "feed", feed)
        mode = str(self.selection_mode or "top_k_stable").strip().lower()
        if mode not in {"best", "top_k_stable"}:
            mode = "top_k_stable"
        object.__setattr__(self, "selection_mode", mode)
        object.__setattr__(self, "seed", int(self.seed))
        object.__setattr__(self, "bars", max(40, int(self.bars)))
        object.__setattr__(self, "train_bars", max(10, int(self.train_bars)))
        object.__setattr__(self, "test_bars", max(5, int(self.test_bars)))
        if self.step is not None:
            object.__setattr__(self, "step", max(1, int(self.step)))
        object.__setattr__(self, "min_folds", max(1, int(self.min_folds)))
        object.__setattr__(self, "auto_fit_folds", bool(self.auto_fit_folds))
        object.__setattr__(self, "top_k", max(1, int(self.top_k)))
        object.__setattr__(self, "min_fold_wins", max(1, int(self.min_fold_wins)))
        object.__setattr__(
            self, "bootstrap_samples", max(0, int(self.bootstrap_samples))
        )
        ci = float(self.bootstrap_ci)
        object.__setattr__(self, "bootstrap_ci", min(0.999, max(0.5, ci)))
        object.__setattr__(self, "score_key", str(self.score_key or "pnl"))
        object.__setattr__(self, "persist_report", bool(self.persist_report))
        object.__setattr__(
            self, "report_dir", str(self.report_dir or data_path("phantom", "walk_forward"))
        )
        object.__setattr__(self, "pattern", str(self.pattern or "sharp_rise"))
        tf = str(self.timeframe or "1d").strip().lower() or "1d"
        object.__setattr__(self, "timeframe", tf)
        object.__setattr__(self, "include_baselines", bool(self.include_baselines))

    @property
    def effective_step(self) -> int:
        return int(self.step if self.step is not None else self.test_bars)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "WalkForwardConfig":
        if not data:
            return cls()
        known = {f.name for f in fields(cls)}
        raw = {k: v for k, v in dict(data).items() if k in known and v is not None}
        # Aliases from bridge / AI payloads.
        if "pattern_seed" in data and "seed" not in raw:
            raw["seed"] = data["pattern_seed"]
        if "limit" in data and "bars" not in raw:
            raw["bars"] = data["limit"]
        return cls(**raw)

    def merged(self, overrides: Mapping[str, Any] | None) -> "WalkForwardConfig":
        base = self.to_dict()
        if overrides:
            for k, v in dict(overrides).items():
                if v is None:
                    continue
                if k == "pattern_seed":
                    base["seed"] = v
                elif k == "limit":
                    base["bars"] = v
                elif k in base or k in {f.name for f in fields(type(self))}:
                    base[k] = v
        return WalkForwardConfig.from_dict(base)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def bars_needed_for_folds(
    *,
    train_bars: int,
    test_bars: int,
    step: int,
    min_folds: int,
) -> int:
    """Minimum series length to realize ``min_folds`` rolling windows."""
    train = max(1, int(train_bars))
    test = max(1, int(test_bars))
    stride = max(1, int(step))
    folds = max(1, int(min_folds))
    # fold i needs train + test + i*step bars ending at test_end
    return train + test + (folds - 1) * stride


def walk_forward_request_bars_needed(
    *,
    train_bars: int = 60,
    test_bars: int = 20,
    step: int | None = None,
    min_folds: int = 5,
    holdout_bars: int = 0,
) -> int:
    """Minimum ``bars``/limit for a WF request including optional holdout tail."""
    stride = int(step) if step not in (None, 0) else max(1, int(test_bars))
    research = bars_needed_for_folds(
        train_bars=train_bars,
        test_bars=test_bars,
        step=stride,
        min_folds=min_folds,
    )
    return int(research) + max(0, int(holdout_bars or 0))


def walk_forward_bars_preflight(
    bars: int | None,
    *,
    train_bars: int = 60,
    test_bars: int = 20,
    step: int | None = None,
    min_folds: int = 5,
    holdout_bars: int = 0,
) -> dict[str, Any] | None:
    """
    Fail-fast check before launching single/batch WF.

    Returns an error dict when ``bars`` cannot yield ``min_folds``, else None.
    Prefer :func:`resolve_walk_forward_bars` for auto-bump.
    """
    resolved = resolve_walk_forward_bars(
        bars,
        train_bars=train_bars,
        test_bars=test_bars,
        step=step,
        min_folds=min_folds,
        holdout_bars=holdout_bars,
    )
    if resolved.get("ok") and not resolved.get("bumped"):
        return None
    if resolved.get("ok") and resolved.get("bumped"):
        # Still "short" relative to request — callers that refuse bump use this.
        return {
            "error": "walk_forward_too_short",
            "bars": int(resolved["bars_requested"]),
            "bars_needed": int(resolved["bars_needed"]),
            "train_bars": int(train_bars),
            "test_bars": int(test_bars),
            "step": int(resolved["step"]),
            "min_folds": int(min_folds),
            "holdout_bars": max(0, int(holdout_bars or 0)),
            "hint": (
                f"Pass bars≥{resolved['bars_needed']} (default 500), or lower "
                "min_folds/train_bars, or set auto_fit_folds=true."
            ),
        }
    return None


def resolve_walk_forward_bars(
    bars: int | None,
    *,
    train_bars: int = 60,
    test_bars: int = 20,
    step: int | None = None,
    min_folds: int = 5,
    holdout_bars: int = 0,
    default_bars: int = 500,
) -> dict[str, Any]:
    """
    Ensure a WF request has enough bars.

    Returns ``{ok, bars, bars_requested, bars_needed, bumped, step, ...}``.
    When too short, ``bars`` is raised to ``bars_needed`` (science > stubbornness).
    """
    stride = int(step) if step not in (None, 0) else max(1, int(test_bars))
    needed = walk_forward_request_bars_needed(
        train_bars=train_bars,
        test_bars=test_bars,
        step=stride,
        min_folds=min_folds,
        holdout_bars=holdout_bars,
    )
    if bars is None:
        requested = int(default_bars)
    else:
        try:
            requested = int(bars)
        except (TypeError, ValueError):
            requested = int(default_bars)
    bumped = requested < needed
    final = needed if bumped else requested
    return {
        "ok": True,
        "bars": int(final),
        "bars_requested": int(requested),
        "bars_needed": int(needed),
        "bumped": bool(bumped),
        "bars_bumped_from": int(requested) if bumped else None,
        "train_bars": int(train_bars),
        "test_bars": int(test_bars),
        "step": stride,
        "min_folds": int(min_folds),
        "holdout_bars": max(0, int(holdout_bars or 0)),
    }


def fit_step_for_min_folds(
    n_bars: int,
    *,
    train_bars: int,
    test_bars: int,
    min_folds: int,
) -> int:
    """Largest step that still yields ≥ min_folds (falls back to 1)."""
    train = max(1, int(train_bars))
    test = max(1, int(test_bars))
    folds = max(1, int(min_folds))
    n = int(n_bars)
    if folds <= 1:
        return test
    # train + test + (folds-1)*step <= n  →  step <= (n - train - test) / (folds-1)
    slack = n - train - test
    if slack < 0:
        return 1
    step = max(1, slack // (folds - 1))
    return max(1, min(test, step))


def config_fingerprint(cfg: Mapping[str, Any] | None) -> str:
    """Stable key for grouping identical strategy configs across folds."""
    data = dict(cfg or {})
    return json.dumps(data, sort_keys=True, default=str)


def _optimal_block_length(n: int) -> int:
    """Block length for a moving-block bootstrap of a short series.

    The standard `n ** (1/3)` rule balances the two ways the method fails: too
    short and the resample keeps the serial dependence it was meant to break,
    too long and there are too few blocks to estimate anything. For the handful
    of folds a walk-forward produces this lands in the low single digits, which
    is also where resampling conservatively sits.
    """
    if n <= 1:
        return 1
    return max(1, min(n, int(round(n ** (1.0 / 3.0)))))


def _null_bootstrap_p(null_means: Sequence[float], observed: float) -> float:
    """Two-sided p-value from a resample of the series *recentered on zero*.

    The tempting shortcut -- ask where the observed mean falls inside the bootstrap
    distribution -- does not work, and it fails silently rather than loudly. A
    bootstrap distribution is centred on the observed statistic by construction, so
    the observed mean sits at its midpoint and the answer comes out near 1 for
    every input, including series with an enormous signal. It reported p = 0.975
    for a mean of 3.0 with a CI of [2.93, 3.09] before this was caught.

    A p-value is the probability of the data *under the null*, so the null has to be
    baked into the resample. Recentering on zero imposes "mean is zero" while the
    block structure keeps the autocorrelation intact, and the question becomes the
    honest one: how often does a world with no edge produce a mean this large?

    ``(1 + count) / (1 + n)`` rather than ``count / n`` so a strong signal reports a
    small number instead of exactly zero -- a p-value of 0 asserts more than any
    finite sample can support, and the correction downstream multiplies by N.
    """
    target = abs(float(observed))
    if not null_means:
        return 1.0
    if target == 0.0:
        return 1.0
    count = sum(1 for value in null_means if abs(value) >= target)
    return min(1.0, (1.0 + count) / (1.0 + len(null_means)))


def block_bootstrap_mean_ci(
    values: Sequence[float],
    *,
    samples: int = 1000,
    ci: float = 0.95,
    seed: int = 42,
    block_length: int | None = None,
) -> dict[str, float]:
    """CI for the mean of a serially correlated series.

    The plain bootstrap resamples i.i.d., which is only valid when the samples
    are independent. Walk-forward folds overlap -- consecutive test windows share
    bars and the returns inside them are autocorrelated -- so treating them as
    independent understates the spread. The interval came out narrow, and
    `ci_excludes_zero` then cleared strategies that a correctly correlated
    interval would have kept out.

    A moving block bootstrap resamples contiguous runs of the series instead, so
    the local dependence survives into the resample and the interval widens by
    roughly the amount the dependence actually contributes.
    """
    xs = [float(v) for v in values]
    n = len(xs)
    if n == 0:
        return {
            "ci_low": 0.0,
            "ci_high": 0.0,
            "ci_level": float(ci),
            "bootstrap_mean": 0.0,
            "bootstrap_samples": 0,
            "block_length": 0,
            "p_value": 1.0,
        }
    if n == 1 or samples <= 0:
        # A single observation has no resample to build a distribution from, so
        # there is no evidence either way. Reporting 1.0 rather than guessing is the
        # whole point: an unwinnable record must not be corrected into significance
        # by the multiplicity pass downstream.
        m = float(xs[0])
        return {
            "ci_low": m,
            "ci_high": m,
            "ci_level": float(ci),
            "bootstrap_mean": m,
            "bootstrap_samples": 0,
            "block_length": 0,
            "p_value": 1.0,
        }

    block = int(block_length or _optimal_block_length(n))
    block = max(1, min(block, n))
    rng = random.Random(int(seed))
    blocks_needed = -(-n // block)  # ceil
    starts_max = n - block
    observed = sum(xs) / n
    # Two resamples from one loop, one stream of randomness: the interval from the
    # raw series, the p-value from the same series recentered on zero. Sharing the
    # stream keeps the two numbers describing the same draws, which matters when a
    # reader is trying to work out why they disagree.
    centered = [v - observed for v in xs]
    means: list[float] = []
    null_means: list[float] = []
    for _ in range(int(samples)):
        picks: list[float] = []
        picks_c: list[float] = []
        for _ in range(blocks_needed):
            start = rng.randint(0, starts_max) if starts_max > 0 else 0
            picks.extend(xs[start : start + block])
            picks_c.extend(centered[start : start + block])
        means.append(sum(picks[:n]) / n)
        null_means.append(sum(picks_c[:n]) / n)
    means.sort()
    alpha = 1.0 - float(ci)
    lo = means[max(0, int((alpha / 2.0) * len(means)))]
    hi = means[min(len(means) - 1, int(((1.0 - alpha / 2.0) * len(means))) - 1)]
    return {
        "ci_low": lo,
        "ci_high": hi,
        "ci_level": float(ci),
        "bootstrap_mean": observed,
        "bootstrap_samples": len(means),
        "block_length": block,
        # A CI says only whether the interval cleared a line; a p-value is the thing
        # that can be corrected for how many strategies were measured. Correcting is
        # the entire purpose of the gate this feeds, so the number has to exist in
        # the record before the batch size is known.
        "p_value": _null_bootstrap_p(null_means, observed),
    }


def bootstrap_mean_ci(
    values: Sequence[float],
    *,
    samples: int = 1000,
    ci: float = 0.95,
    seed: int = 42,
) -> dict[str, float]:
    """Percentile bootstrap CI for the mean of ``values``."""
    xs = [float(v) for v in values]
    if not xs:
        return {
            "ci_low": 0.0,
            "ci_high": 0.0,
            "ci_level": float(ci),
            "bootstrap_mean": 0.0,
            "bootstrap_samples": 0,
        }
    if len(xs) == 1 or samples <= 0:
        m = float(xs[0]) if xs else 0.0
        return {
            "ci_low": m,
            "ci_high": m,
            "ci_level": float(ci),
            "bootstrap_mean": m,
            "bootstrap_samples": 0,
        }
    rng = random.Random(int(seed))
    n = len(xs)
    means: list[float] = []
    for _ in range(int(samples)):
        draw = [xs[rng.randrange(n)] for _ in range(n)]
        means.append(sum(draw) / n)
    means.sort()
    alpha = 1.0 - float(ci)
    lo_i = int(math.floor(alpha / 2.0 * (len(means) - 1)))
    hi_i = int(math.ceil((1.0 - alpha / 2.0) * (len(means) - 1)))
    lo_i = max(0, min(len(means) - 1, lo_i))
    hi_i = max(0, min(len(means) - 1, hi_i))
    return {
        "ci_low": float(means[lo_i]),
        "ci_high": float(means[hi_i]),
        "ci_level": float(ci),
        "bootstrap_mean": float(sum(means) / len(means)),
        "bootstrap_samples": int(samples),
    }


def pick_top_k_variants(
    results: Sequence[dict[str, Any]],
    *,
    key: str = "pnl",
    k: int = 3,
) -> list[dict[str, Any]]:
    ranked = sorted(
        results,
        key=lambda r: float(r.get(key) or 0.0),
        reverse=True,
    )
    return list(ranked[: max(1, int(k))])


def rank_stable_family(
    fold_top_configs: Sequence[Sequence[Mapping[str, Any]]],
    fold_oos_by_fp: Mapping[str, list[float]],
    *,
    top_k: int = 3,
    min_fold_wins: int = 1,
    score_key: str = "pnl",
) -> list[dict[str, Any]]:
    """
    Rank configs by stability across folds.

    ``fold_top_configs``: per fold list of config dicts (best → k-th).
    ``fold_oos_by_fp``: fingerprint → list of OOS pnls when that config was scored.
    """
    win_counts: dict[str, int] = {}
    cfg_by_fp: dict[str, dict[str, Any]] = {}
    for fold_cfgs in fold_top_configs:
        seen: set[str] = set()
        for cfg in fold_cfgs:
            fp = config_fingerprint(cfg)
            cfg_by_fp[fp] = dict(cfg)
            if fp not in seen:
                win_counts[fp] = win_counts.get(fp, 0) + 1
                seen.add(fp)

    family: list[dict[str, Any]] = []
    for fp, cfg in cfg_by_fp.items():
        wins = int(win_counts.get(fp, 0))
        if wins < int(min_fold_wins):
            continue
        oos = list(fold_oos_by_fp.get(fp) or [])
        mean_oos = float(sum(oos) / len(oos)) if oos else 0.0
        std_oos = float(statistics.pstdev(oos)) if len(oos) >= 2 else 0.0
        family.append(
            {
                "config": cfg,
                "fingerprint": fp,
                "fold_wins": wins,
                "oos_samples": len(oos),
                "mean_oos_pnl": mean_oos,
                "oos_pnl_std": std_oos,
                "score_key": score_key,
            }
        )
    family.sort(
        key=lambda r: (
            -int(r["fold_wins"]),
            -float(r["mean_oos_pnl"]),
            float(r["oos_pnl_std"]),
        )
    )
    return family[: max(1, int(top_k))]


def enrich_oos_summary(
    oos_pnls: Sequence[float],
    *,
    cfg: WalkForwardConfig,
) -> dict[str, Any]:
    values = [float(x) for x in oos_pnls]
    summary = expectancy_metrics(values)
    summary["folds"] = len(values)
    summary["mean_oos_pnl"] = summary["avg_pnl"]
    summary["total_oos_pnl"] = summary["total_pnl"]
    summary["fold_pnl_mean"] = float(sum(values) / len(values)) if values else 0.0
    summary["fold_pnl_std"] = (
        float(statistics.pstdev(values)) if len(values) >= 2 else 0.0
    )
    summary["fold_pnl_min"] = float(min(values)) if values else 0.0
    summary["fold_pnl_max"] = float(max(values)) if values else 0.0
    # Block bootstrap, not i.i.d.: walk-forward folds overlap and their returns
    # are autocorrelated, so the plain version understates the spread and hands
    # the research gate a confidence interval that is too narrow to trust.
    boot = block_bootstrap_mean_ci(
        values,
        samples=cfg.bootstrap_samples,
        ci=cfg.bootstrap_ci,
        seed=cfg.seed,
    )
    summary.update(boot)
    return summary


def build_provenance(
    cfg: WalkForwardConfig,
    *,
    strategy_id: str,
    ticker: str,
    class_code: str,
    provider: str | None,
    source: str,
    folds_realized: int,
    bars: int,
    step: int,
) -> dict[str, Any]:
    return {
        "feed": cfg.feed,
        "seed": cfg.seed,
        "bars": int(bars),
        "train_bars": cfg.train_bars,
        "test_bars": cfg.test_bars,
        "step": int(step),
        "min_folds": cfg.min_folds,
        "folds_realized": int(folds_realized),
        "selection_mode": cfg.selection_mode,
        "top_k": cfg.top_k,
        "bootstrap_samples": cfg.bootstrap_samples,
        "bootstrap_ci": cfg.bootstrap_ci,
        "auto_fit_folds": cfg.auto_fit_folds,
        "timeframe": cfg.timeframe,
        "include_baselines": cfg.include_baselines,
        "strategy_id": strategy_id,
        "ticker": ticker,
        "class_code": class_code,
        "provider": provider or "",
        "source": source,
        "pattern": cfg.pattern if cfg.feed == "patterns" else "",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def persist_walk_forward_report(report: Mapping[str, Any], cfg: WalkForwardConfig) -> str | None:
    if not cfg.persist_report:
        return None
    root = Path(cfg.report_dir)
    root.mkdir(parents=True, exist_ok=True)
    prov = report.get("provenance") if isinstance(report.get("provenance"), dict) else {}
    stamp = str(prov.get("generated_at") or datetime.now(timezone.utc).isoformat())
    safe = stamp.replace(":", "").replace("+", "_")
    sid = str(prov.get("strategy_id") or "strategy")
    # Slash tickers (ETH/USDT) must not create nested paths on Windows/Unix.
    ticker = (
        str(prov.get("ticker") or "TICK")
        .replace("/", "")
        .replace("\\", "")
        .replace(":", "")
    )
    path = root / f"wf_{sid}_{ticker}_{safe}.json"
    path.write_text(json.dumps(dict(report), indent=2, default=str), encoding="utf-8")
    return str(path)


def run_walk_forward_offline(
    plugin: Any,
    series: dict[str, list[float]],
    variants: Sequence[dict[str, Any]],
    *,
    train_bars: int | None = None,
    test_bars: int | None = None,
    step: int | None = None,
    quantity: float | None = 1.0,
    fee_bps: float = 0.0,
    start_equity: float = 10_000.0,
    config: WalkForwardConfig | Mapping[str, Any] | None = None,
    provenance_extra: Mapping[str, Any] | None = None,
    sizing: Any | None = None,
    atr: float | None = None,
    atr_series: Sequence[float | None] | None = None,
    position_mode: str = "long_flat",
) -> dict[str, Any]:
    """
    Walk-forward with configurable selection, stability ranking, and bootstrap CI.

    For each fold: score variants on train, pick best (or top-k), score OOS on
    train+test with fills only in the test region.
    """
    cfg = (
        config
        if isinstance(config, WalkForwardConfig)
        else WalkForwardConfig.from_dict(config)
    )
    if train_bars is not None:
        cfg = cfg.merged({"train_bars": train_bars})
    if test_bars is not None:
        cfg = cfg.merged({"test_bars": test_bars})
    if step is not None:
        cfg = cfg.merged({"step": step})

    closes = list(series.get("close") or [])
    n = len(closes)
    stride = cfg.effective_step
    if cfg.auto_fit_folds:
        stride = fit_step_for_min_folds(
            n,
            train_bars=cfg.train_bars,
            test_bars=cfg.test_bars,
            min_folds=cfg.min_folds,
        )

    windows = walk_forward_windows(
        n,
        train_bars=cfg.train_bars,
        test_bars=cfg.test_bars,
        step=stride,
    )
    if len(windows) < cfg.min_folds:
        needed = bars_needed_for_folds(
            train_bars=cfg.train_bars,
            test_bars=cfg.test_bars,
            step=stride,
            min_folds=cfg.min_folds,
        )
        raise WalkForwardTooShortError(
            bars=n,
            train_bars=cfg.train_bars,
            test_bars=cfg.test_bars,
            step=stride,
            min_folds=cfg.min_folds,
            folds_available=len(windows),
            bars_needed=needed,
        )

    variants_list = [dict(v or {}) for v in variants] or [{}]
    folds: list[dict[str, Any]] = []
    oos_pnls: list[float] = []
    fold_top_configs: list[list[dict[str, Any]]] = []
    fold_oos_by_fp: dict[str, list[float]] = {}
    buy_hold_pnls: list[float] = []
    fee_zero_pnls: list[float] = []
    fixed_default_pnls: list[float] = []

    for win in windows:
        train = slice_series(series, win.train_start, win.train_end)
        train_atr = (
            list(atr_series[win.train_start : win.train_end])
            if atr_series is not None
            else None
        )
        train_results: list[dict[str, Any]] = []
        for var in variants_list:
            m = offline_variant_metrics(
                plugin,
                train,
                config=var,
                quantity=quantity,
                fee_bps=fee_bps,
                start_equity=start_equity,
                sizing=sizing,
                atr=atr,
                atr_series=train_atr if train_atr is not None else atr_series,
                position_mode=position_mode,
            )
            train_results.append(m)

        if cfg.selection_mode == "top_k_stable":
            top = pick_top_k_variants(
                train_results, key=cfg.score_key, k=cfg.top_k
            )
        else:
            best_one = pick_best_variant(train_results, key=cfg.score_key)
            top = [best_one] if best_one else []

        best = top[0] if top else pick_best_variant(train_results, key=cfg.score_key)
        best_cfg = dict((best or {}).get("config") or {})
        top_cfgs = [dict(r.get("config") or {}) for r in top if r]
        fold_top_configs.append(top_cfgs)

        combo = slice_series(series, win.train_start, win.test_end)
        train_len = win.train_end - win.train_start
        combo_atr = (
            list(atr_series[win.train_start : win.test_end])
            if atr_series is not None
            else None
        )
        atr_for_combo = combo_atr if combo_atr is not None else atr_series
        oos = offline_variant_metrics(
            plugin,
            combo,
            config=best_cfg,
            quantity=quantity,
            fee_bps=fee_bps,
            score_from=train_len,
            start_equity=start_equity,
            sizing=sizing,
            atr=atr,
            atr_series=atr_for_combo,
            position_mode=position_mode,
        )
        oos_pnl = float(oos.get("pnl") or 0.0)
        oos_pnls.append(oos_pnl)

        for tcfg in top_cfgs:
            fp = config_fingerprint(tcfg)
            if tcfg == best_cfg:
                fold_oos_by_fp.setdefault(fp, []).append(oos_pnl)
                continue
            oos_k = offline_variant_metrics(
                plugin,
                combo,
                config=tcfg,
                quantity=quantity,
                fee_bps=fee_bps,
                score_from=train_len,
                start_equity=start_equity,
                sizing=sizing,
                atr=atr,
                atr_series=atr_for_combo,
                position_mode=position_mode,
            )
            fold_oos_by_fp.setdefault(fp, []).append(float(oos_k.get("pnl") or 0.0))

        fold_row: dict[str, Any] = {
            **win.to_dict(),
            "best_config": best_cfg,
            "top_configs": top_cfgs,
            "train_pnl": float((best or {}).get("pnl") or 0.0),
            "train_trades": int((best or {}).get("trades") or 0),
            "oos_pnl": oos_pnl,
            "oos_trades": int(oos.get("trades") or 0),
            "oos_max_drawdown": float(oos.get("max_drawdown") or 0.0),
            "variants_tried": len(train_results),
        }

        if cfg.include_baselines:
            closes_combo = list(combo.get("close") or [])
            bh = buy_hold_oos_metrics(
                closes_combo,
                score_from=train_len,
                quantity=quantity,
                fee_bps=fee_bps,
                start_equity=start_equity,
                sizing=sizing,
                atr=atr,
                atr_series=atr_for_combo,
            )
            fee0 = offline_variant_metrics(
                plugin,
                combo,
                config=best_cfg,
                quantity=quantity,
                fee_bps=0.0,
                score_from=train_len,
                start_equity=start_equity,
                sizing=sizing,
                atr=atr,
                atr_series=atr_for_combo,
                position_mode=position_mode,
            )
            fixed = offline_variant_metrics(
                plugin,
                combo,
                config={},
                quantity=quantity,
                fee_bps=fee_bps,
                score_from=train_len,
                start_equity=start_equity,
                sizing=sizing,
                atr=atr,
                atr_series=atr_for_combo,
                position_mode=position_mode,
            )
            bh_pnl = float(bh.get("pnl") or 0.0)
            fee0_pnl = float(fee0.get("pnl") or 0.0)
            fixed_pnl = float(fixed.get("pnl") or 0.0)
            buy_hold_pnls.append(bh_pnl)
            fee_zero_pnls.append(fee0_pnl)
            fixed_default_pnls.append(fixed_pnl)
            fold_row["baselines"] = {
                "buy_hold_pnl": bh_pnl,
                "fee_zero_selected_pnl": fee0_pnl,
                "fixed_default_pnl": fixed_pnl,
                "vs_buy_hold": oos_pnl - bh_pnl,
            }

        folds.append(fold_row)

    summary = enrich_oos_summary(oos_pnls, cfg=cfg)
    # The per-fold series behind those aggregates. Without it the batch-level tests
    # (Reality Check, SPA, PBO) have nothing to bootstrap: a p-value cannot be
    # corrected across strategies, only a series can, and one per strategy is the
    # minimum any of them needs. Fold-level rather than bar-level is deliberate --
    # a fold is the granularity at which the strategy was actually measured, and
    # inventing bar-level resolution here would be precision the analysis does not
    # have.
    summary["oos_pnl_by_fold"] = [float(v) for v in oos_pnls]
    # How many configurations were tried before one was reported. Picking the
    # best of N noisy estimates is optimistically biased, and the bias grows
    # with N -- so the number itself is evidence about how much to trust the
    # result, and it has to reach the digest or a reader cannot see it.
    summary["variants_tried_total"] = sum(
        int(f.get("variants_tried") or 0) for f in folds
    )
    summary["folds_evaluated"] = len(folds)
    stable_family = rank_stable_family(
        fold_top_configs,
        fold_oos_by_fp,
        top_k=cfg.top_k,
        min_fold_wins=cfg.min_fold_wins,
        score_key=cfg.score_key,
    )
    chosen = (
        dict(stable_family[0]["config"])
        if cfg.selection_mode == "top_k_stable" and stable_family
        else dict((folds[-1].get("best_config") if folds else {}) or {})
    )
    if cfg.selection_mode == "best" and folds:
        freq: dict[str, int] = {}
        for f in folds:
            fp = config_fingerprint(f.get("best_config"))
            freq[fp] = freq.get(fp, 0) + 1
        best_fp = max(freq, key=lambda k: (freq[k], -len(k))) if freq else ""
        for f in folds:
            if config_fingerprint(f.get("best_config")) == best_fp:
                chosen = dict(f.get("best_config") or {})
                break

    extra = dict(provenance_extra or {})
    provenance = build_provenance(
        cfg,
        strategy_id=str(extra.get("strategy_id") or ""),
        ticker=str(extra.get("ticker") or ""),
        class_code=str(extra.get("class_code") or ""),
        provider=extra.get("provider"),
        source=str(extra.get("source") or cfg.feed),
        folds_realized=len(folds),
        bars=n,
        step=stride,
    )
    _prov_skip = {"strategy_id", "ticker", "class_code", "provider", "source"}
    for key, val in extra.items():
        if key not in _prov_skip and key not in provenance:
            provenance[key] = val

    baselines: dict[str, Any] | None = None
    if cfg.include_baselines and buy_hold_pnls:
        bh_sum = enrich_oos_summary(buy_hold_pnls, cfg=cfg)
        fee0_sum = enrich_oos_summary(fee_zero_pnls, cfg=cfg)
        fixed_sum = enrich_oos_summary(fixed_default_pnls, cfg=cfg)
        mean_sel = float(summary.get("mean_oos_pnl") or 0.0)
        mean_bh = float(bh_sum.get("mean_oos_pnl") or 0.0)
        baselines = {
            "buy_hold": bh_sum,
            "fee_zero_selected": fee0_sum,
            "fixed_default": fixed_sum,
            "edge_vs_buy_hold": mean_sel - mean_bh,
            "cost_drag": float(fee0_sum.get("mean_oos_pnl") or 0.0) - mean_sel,
        }

    report: dict[str, Any] = {
        "folds": folds,
        "oos": summary,
        "train_bars": cfg.train_bars,
        "test_bars": cfg.test_bars,
        "step": int(stride),
        "variants": len(variants_list),
        "bars": n,
        "selection_mode": cfg.selection_mode,
        "stable_family": stable_family,
        "chosen": chosen,
        "config": cfg.to_dict(),
        "provenance": provenance,
    }
    if baselines is not None:
        report["baselines"] = baselines
    from engine.research_digest import enrich_fold_aggregates

    enrich_fold_aggregates(report)
    try:
        path = persist_walk_forward_report(report, cfg)
    except OSError:
        path = None
    if path:
        report["report_path"] = path
        provenance["report_path"] = path
    return report
