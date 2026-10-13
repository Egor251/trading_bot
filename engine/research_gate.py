"""Research clearance + quantitative live-readiness gate for Consilium votes."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence
from engine.paths import data_path


DEFAULT_CLEARANCE_PATH = data_path("phantom", "research_clearance.json")

GRADE_RESEARCH = "research_only"
GRADE_OBSERVE = "observe_ok"
GRADE_LIVE = "live_ok"


def normalize_research_ticker(ticker: str | None) -> str:
    """Canonical clearance key: ETH/USDT → ETHUSDT (uppercase, no separators)."""
    t = str(ticker or "").strip().upper()
    for ch in ("/", "\\", "-", ":", " "):
        t = t.replace(ch, "")
    return t


def _key(strategy_id: str, ticker: str) -> str:
    return f"{str(strategy_id).strip()}::{normalize_research_ticker(ticker)}"


def _f(x: Any, default: float | None = None) -> float | None:
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _i(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return default
        return int(x)
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class LiveReadinessPolicy:
    """Thresholds for observe_ok / live_ok grades (YAML: trading.live_readiness)."""

    require_robust_sign: bool = True
    require_ci_excludes_zero: bool = True
    min_holdout_trades: int = 8
    min_oos_trades: int = 20
    min_profit_factor: float = 1.15
    min_mean_oos_pnl: float = 0.0
    min_tickers_robust: int = 2
    require_params_applied: bool = True
    max_age_days: int = 30
    canary_quantity_scale: float = 0.25
    # observe_ok can pass with a single robust ticker; live needs min_tickers_robust
    observe_min_tickers_robust: int = 1
    observe_require_ci_excludes_zero: bool = False
    observe_min_holdout_trades: int = 1
    #: Whether the gate actually *gates*. When true (the default) a member that
    #: does not reach ``live_ok`` is demoted from the live lineup to observe at
    #: Consilium start-up, instead of merely being reported as unready.
    #:
    #: This defaulted to advisory because nothing on the decision path ever
    #: called the gate: `evaluate_live_readiness` was reachable only from an
    #: operator UI command and as an LLM tool, and the LLM was merely *asked*
    #: not to promote an uncleared strategy. The shipped config therefore armed
    #: `dual_thrust` for live votes while its own research snapshot cleared 0 of
    #: 28 entries — 28/28 had `ci_crosses_zero: true`, the best holdout had 8
    #: trades against a required 20, and the snapshot had been stale since
    #: 2026-08-06. Set false to restore the previous advisory-only behaviour.
    enforce: bool = True
    #: Whether the batch of strategies judged together is corrected for its own
    #: size. Separate from ``require_ci_excludes_zero`` on purpose: that asks "is
    #: this one interval clear of zero", this asks "given how many strategies were
    #: measured at once, would the best of them clear zero by chance".
    #:
    #: At 95% per strategy and 28 strategies in a batch, ~1.4 false positives are
    #: expected even if every strategy is pure noise. The gate had no way to express
    #: that, so a batch of mediocre strategies looks like a batch containing winners.
    require_multiplicity_correction: bool = True
    #: Bonferroni / holm / benjamini-hochberg. Holm by default: same family-wise
    #: error as Bonferroni but it does not assume independence between strategies,
    #: which is false here -- strategies on one ticker share price data, and shared
    #: data means shared errors.
    multiplicity_method: str = "holm"
    #: Alpha applied to the *corrected* p-value. Not a separate threshold: the
    #: correction already accounts for N, so this stays at the conventional 0.05
    #: and does not compound a second, harsher penalty on top.
    multiplicity_alpha: float = 0.05
    #: Whether the batch is judged by the whole family of corrections -- Holm, White's
    #: Reality Check, Hansen's SPA and Bailey/Lopez de Prado's PBO -- rather than by
    #: one of them.
    #:
    #: A batch passes only if it survives all of them. Taking the most favourable of
    #: four tests would be the same mistake as picking the best of twenty-one
    #: strategies, one level up.
    require_batch_verdict: bool = True
    #: Bootstrap size for the batch tests. They run once per batch rather than per
    #: strategy, so this can be generous; it is the slowest thing on the gate.
    batch_bootstrap_samples: int = 500
    #: Folds the CSCV enumeration splits the series into. Fixed at 8 by the paper's
    #: convention and kept as a knob only so a short series can be reconciled; the
    #: PBO threshold below is the part that matters and is not tuned from data.
    pbo_splits: int = 8
    #: PBO above this means the selection is worse than picking at random and the
    #: in-sample winner finished out-of-sample below where it started, more often
    #: than not. Not 0.5 by accident -- that is the coin-flip point, and it is the
    #: one value that cannot be tuned in the strategy's favour.
    max_pbo: float = 0.5
    #: Which denominator the universe is computed over: ``available`` uses the
    #: configuration space the schema permits, ``explored`` uses only what was
    #: actually searched.
    #:
    #: ``available`` is the default because the reported configuration is a maximum
    #: over a search whose reach was arbitrary. These strategies declare 4-6 numeric
    #: parameters and walk-forward evaluates 3 combinations out of the 16 or 36
    #: available, so an analyst running the same pipeline with ``max_variants=36``
    #: gets a different winner. Correcting over the explored count is lenient by
    #: exactly that ratio, measured at about 6x on this repo.
    #:
    #: This is a judgement rather than a fact, which is why it is a setting rather
    #: than a constant: the narrower reading stays available and is named in the
    #: report so a reader can see which one produced a number.
    universe_basis: str = "available"

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> LiveReadinessPolicy:
        if not isinstance(raw, Mapping):
            return cls()
        known = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for k, v in raw.items():
            if k not in known or v is None:
                continue
            kwargs[k] = v
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def policy_from_config(raw_cfg: Mapping[str, Any] | None) -> LiveReadinessPolicy:
    trading = dict((raw_cfg or {}).get("trading") or {})
    return LiveReadinessPolicy.from_mapping(trading.get("live_readiness"))


#: Parsed `research_clearance.json`, keyed by (path, mtime_ns, size).
#:
#: `build_snapshot()` calls `evaluate_many()` for every UI frame, which calls
#: `evaluate_live_readiness()` per strategy, which calls `load_clearance()`. That
#: was an `is_file()` + `read_text()` + `json.loads()` *per strategy per frame* —
#: ~21 strategies at 4 Hz is ~84 synchronous disk reads a second on the event loop
#: that also serves `/api/*` commands and the broadcast. A cache keyed on mtime
#: and size makes the file read once and still invalidate the instant the research
#: service rewrites it.
_CLEARANCE_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}
_CLEARANCE_CACHE_MAX = 8


def clear_clearance_cache() -> None:
    """Drop the parsed-snapshot cache. Used by tests and after a rewrite."""
    _CLEARANCE_CACHE.clear()


def load_clearance(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path or DEFAULT_CLEARANCE_PATH)
    try:
        stat = p.stat()
    except OSError:
        _CLEARANCE_CACHE.pop(str(p), None)
        return {"entries": {}, "updated_at": None}
    key = str(p)
    cached = _CLEARANCE_CACHE.get(key)
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"entries": {}, "updated_at": None}
    if not isinstance(raw, dict):
        return {"entries": {}, "updated_at": None}
    entries = raw.get("entries")
    if not isinstance(entries, dict):
        entries = {}
    out: dict[str, Any] = {"entries": entries, "updated_at": raw.get("updated_at")}
    if len(_CLEARANCE_CACHE) >= _CLEARANCE_CACHE_MAX:
        _CLEARANCE_CACHE.clear()
    _CLEARANCE_CACHE[key] = (stat.st_mtime_ns, stat.st_size, out)
    return out


def save_clearance(store: Mapping[str, Any], path: str | Path | None = None) -> str:
    p = Path(path or DEFAULT_CLEARANCE_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "entries": dict(store.get("entries") or {}),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return str(p)


def record_digest_clearance(
    digest: Mapping[str, Any] | None,
    *,
    path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Persist a WF digest when holdout was used (eligibility for live votes)."""
    if not isinstance(digest, Mapping) or not digest.get("ok"):
        return None
    holdout_bars = int(digest.get("holdout_bars") or 0)
    if holdout_bars <= 0:
        return None
    sid = str(digest.get("strategy_id") or "").strip()
    ticker = normalize_research_ticker(digest.get("ticker"))
    if not sid or not ticker:
        return None
    store = load_clearance(path)
    entries = dict(store.get("entries") or {})
    # This assignment used to be a plain overwrite, so each run discarded every
    # prior evaluation of the same strategy/ticker. The entry then reported one
    # run's `variants_count` as though it were the whole research history -- and
    # the multiple-comparisons bias grows with the number of attempts actually
    # made, not the number surviving in the file. A strategy evaluated thirty
    # times looked identical to one evaluated once, which is precisely the
    # illusion a research report must not tell.
    #
    # The prior bias already happened and cannot be undone by bookkeeping; what
    # this restores is the ability to *see its size*. Carried forward rather
    # than recomputed, because the earlier runs left no record to recompute from.
    key = _key(sid, ticker)
    prior = entries.get(key) if isinstance(entries.get(key), Mapping) else {}
    evaluations = _i(prior.get("evaluations_count")) + 1
    lifetime_variants = _i(prior.get("variants_tried_lifetime")) + _i(
        digest.get("variants_count")
    )
    lifetime_available = _i(prior.get("variants_available_lifetime")) + _i(
        digest.get("variants_available") or digest.get("variants_count")
    )
    entries[key] = {
        "strategy_id": sid,
        "ticker": ticker,
        "holdout_bars": holdout_bars,
        "holdout_pnl": digest.get("holdout_pnl"),
        "holdout_trades": _i(digest.get("holdout_trades")),
        "robust_sign_match": digest.get("robust_sign_match"),
        "mean_oos_pnl": digest.get("mean_oos_pnl"),
        "ci_crosses_zero": digest.get("ci_crosses_zero"),
        "ci_low": digest.get("ci_low"),
        "ci_high": digest.get("ci_high"),
        # Carried so the batch-level multiplicity pass has a per-strategy p-value
        # to correct. Without it the only evidence reaching the gate is the boolean
        # above, and a boolean cannot be corrected -- which is the entire problem.
        "p_value": digest.get("p_value"),
        # Per-fold OOS P&L. The batch-level tests bootstrap these; a record without
        # them can be judged one strategy at a time and nothing else.
        "oos_pnl_by_fold": [
            float(v) for v in (digest.get("oos_pnl_by_fold") or [])
        ],
        "profit_factor": digest.get("profit_factor"),
        "total_oos_trades": _i(digest.get("total_oos_trades")),
        "timeframe": digest.get("timeframe"),
        "bar_interval_sec": digest.get("bar_interval_sec"),
        "provider_resolved": digest.get("provider_resolved"),
        "variant_mode": digest.get("variant_mode"),
        # This run's own grid size -- per-run provenance, correct as it always
        # was.
        "variants_count": digest.get("variants_count"),
        # Cumulative across every evaluation on record. This is the denominator
        # a deflation argument needs; `variants_count` alone understates it by
        # the number of discarded attempts.
        "evaluations_count": evaluations,
        "variants_tried_lifetime": lifetime_variants,
        # This run's available space. It was computed in `phantom_service` and read
        # by `research_digest`, and then never written here — so every stored entry
        # had `variants_available: None` while `variants_available_lifetime` silently
        # fell back to the explored count and accumulated it. The field looked alive
        # in the code and was absent from the data, which is the same failure as
        # `adjust_pvalues`: correct, reachable, and not connected to where the number
        # is actually read from.
        "variants_available": _i(digest.get("variants_available")),
        # Cumulative *available* configuration space, which is the number the
        # multiplicity correction should use. `variants_tried_lifetime` is kept
        # because it answers a different and still useful question: how much was
        # actually looked at.
        "variants_available_lifetime": lifetime_available,
        "chosen": dict(digest.get("chosen") or {}),
        "recorded_at": datetime.now(UTC).isoformat(),
        # When the evidence was *produced*, i.e. the last bar the analysis saw.
        # `recorded_at` alone was misleading: re-running walk-forward today on a
        # two-month-old cache stamped the entry with today's date and satisfied
        # the 30-day freshness rule, so a strategy could be cleared on stale
        # prices simply by re-running the analysis.
        "data_start": digest.get("data_start"),
        "data_end": digest.get("data_end"),
        "report_path": digest.get("report_path"),
    }
    store = {"entries": entries}
    save_clearance(store, path)
    return entries[_key(sid, ticker)]


def clearance_hits_for_strategy(
    strategy_id: str,
    *,
    ticker: str | None = None,
    tickers: Sequence[str] | None = None,
    path: str | Path | None = None,
    entries: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    sid = str(strategy_id or "").strip()
    store_entries = dict(
        entries if entries is not None else load_clearance(path).get("entries") or {}
    )
    want_set: set[str] = set()
    if tickers:
        want_set = {
            normalize_research_ticker(t) for t in tickers if str(t or "").strip()
        }
    want = normalize_research_ticker(ticker)
    if want:
        want_set.add(want)
    hits: list[dict[str, Any]] = []
    for _k, v in store_entries.items():
        if not isinstance(v, dict):
            continue
        if str(v.get("strategy_id") or "").strip() != sid:
            continue
        t = normalize_research_ticker(v.get("ticker"))
        if want_set and t not in want_set:
            continue
        row = dict(v)
        row["ticker"] = t or str(v.get("ticker") or "")
        hits.append(row)
    return hits


def _parse_recorded_at(row: Mapping[str, Any], field: str = "recorded_at") -> datetime | None:
    raw = row.get(field)
    if not raw:
        return None
    try:
        # Bare timestamps are bar opens in the market feed, so a naive value is
        # UTC by definition.
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None


def params_match_chosen(
    chosen: Mapping[str, Any] | None,
    plugin_params: Mapping[str, Any] | None,
    *,
    rtol: float = 1e-6,
) -> tuple[bool, list[str]]:
    """Return (ok, mismatch_keys) comparing clearance chosen vs plugin config."""
    if not chosen:
        return True, []
    plug = dict(plugin_params or {})
    mismatches: list[str] = []
    for key, want in chosen.items():
        if key not in plug:
            mismatches.append(str(key))
            continue
        got = plug.get(key)
        try:
            wf = float(want)
            gf = float(got)
            if abs(wf - gf) > max(rtol, abs(wf) * rtol):
                mismatches.append(str(key))
        except (TypeError, ValueError):
            if str(want) != str(got):
                mismatches.append(str(key))
    return (not mismatches), mismatches


def plugin_params_from_config(
    raw_cfg: Mapping[str, Any] | None,
    strategy_id: str,
) -> dict[str, Any]:
    """Best-effort plugin block under ``plugins.<sid>`` (YAML) or trading.*."""
    sid = str(strategy_id or "").strip()
    if not sid or not isinstance(raw_cfg, Mapping):
        return {}
    # Canonical persist path used by phantom_apply_chosen / SET_STRATEGY_PARAMS.
    top_plugins = raw_cfg.get("plugins")
    if isinstance(top_plugins, Mapping) and isinstance(top_plugins.get(sid), Mapping):
        return dict(top_plugins[sid])  # type: ignore[index]
    if sid in raw_cfg and isinstance(raw_cfg.get(sid), Mapping):
        return dict(raw_cfg[sid])  # type: ignore[arg-type]
    trading = dict(raw_cfg.get("trading") or {})
    plugins = trading.get("plugins")
    if isinstance(plugins, Mapping) and isinstance(plugins.get(sid), Mapping):
        return dict(plugins[sid])  # type: ignore[index]
    for key in ("strategies", "strategy_plugins"):
        block = trading.get(key)
        if isinstance(block, Mapping) and isinstance(block.get(sid), Mapping):
            return dict(block[sid])  # type: ignore[index]
    return {}


def evaluate_live_readiness(
    strategy_id: str,
    *,
    ticker: str | None = None,
    tickers: Sequence[str] | None = None,
    path: str | Path | None = None,
    policy: LiveReadinessPolicy | None = None,
    plugin_params: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Score a strategy for observe vs live Consilium promotion."""
    pol = policy or LiveReadinessPolicy()
    sid = str(strategy_id or "").strip()
    instant = now or datetime.now(UTC)
    hits = clearance_hits_for_strategy(
        sid, ticker=ticker, tickers=tickers, path=path
    )
    checks: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []

    def _check(
        name: str,
        ok: bool,
        *,
        detail: Any = None,
        live: bool = True,
        observe: bool = True,
    ) -> None:
        checks.append(
            {"name": name, "ok": ok, "detail": detail, "live": live, "observe": observe}
        )
        if not ok:
            blockers.append(
                {
                    "strategy_id": sid,
                    "reason": name,
                    "detail": detail,
                    "live": live,
                    "observe": observe,
                }
            )

    if not hits:
        _check(
            "no_holdout_clearance",
            False,
            detail="Run phantom_walk_forward with holdout_bars>0 first.",
        )
        return {
            "ok": True,
            "strategy_id": sid,
            "grade": GRADE_RESEARCH,
            "score": 0.0,
            "live_ok": False,
            "observe_ok": False,
            "checks": checks,
            "blockers": blockers,
            "entries": [],
            "policy": pol.to_dict(),
        }

    robust_hits = [h for h in hits if h.get("robust_sign_match")]
    if pol.require_robust_sign:
        _check(
            "robust_sign_match",
            bool(robust_hits),
            detail={"robust_tickers": [h.get("ticker") for h in robust_hits]},
        )
    else:
        robust_hits = list(hits)
        _check("robust_sign_match", True, detail="skipped")

    metric_pool = robust_hits or hits
    best = max(
        metric_pool,
        key=lambda h: (_f(h.get("mean_oos_pnl"), -1e18) or -1e18),
    )

    holdout_trades = max(_i(h.get("holdout_trades")) for h in metric_pool)
    oos_trades = max(_i(h.get("total_oos_trades")) for h in metric_pool)
    pfs = [_f(h.get("profit_factor")) for h in metric_pool]
    pf = max((p for p in pfs if p is not None), default=None)
    means = [_f(h.get("mean_oos_pnl")) for h in metric_pool]
    mean_oos = max((m for m in means if m is not None), default=None)
    any_ci_cross = any(bool(h.get("ci_crosses_zero")) for h in metric_pool)
    ci_ok = not any_ci_cross
    # Worst p-value across the pool, matching `any_ci_cross` above: this strategy
    # is only as supported as its least supportive ticker. Taking the best would
    # mean a strategy cleared on the ticker that happened to suit it, which is the
    # selection this gate exists to prevent. Tickers with no p-value on record
    # yield None, and None is later treated as failing rather than skipped.
    pool_p = [_f(h.get("p_value")) for h in metric_pool]
    known_pool_p = [v for v in pool_p if v is not None and 0.0 <= v <= 1.0]
    p_value = max(known_pool_p) if known_pool_p else None

    age_ok = True
    oldest_evidence: list[dict[str, Any]] = []
    for h in metric_pool:
        # Judge the *evidence*, not the moment the analysis was run. Re-running
        # walk-forward on a stale cache used to satisfy the freshness rule on its
        # own, which meant clearance could be renewed indefinitely without ever
        # looking at a new bar.
        ts = _parse_recorded_at(h, "data_end") or _parse_recorded_at(h)
        if ts is None:
            continue
        age = instant - ts
        if age > timedelta(days=max(0, int(pol.max_age_days))):
            age_ok = False
        oldest_evidence.append(
            {
                "ticker": h.get("ticker"),
                "evidence_at": ts.isoformat(),
                "age_days": round(age.total_seconds() / 86400.0, 1),
                "data_end": h.get("data_end"),
            }
        )
    _check(
        "clearance_fresh",
        age_ok,
        detail={
            "max_age_days": pol.max_age_days,
            "evidence": sorted(
                oldest_evidence, key=lambda r: -float(r["age_days"])
            )[:6],
        },
    )

    _check(
        "ci_excludes_zero",
        (ci_ok if pol.require_ci_excludes_zero else True),
        detail={
            "ci_crosses_zero": any_ci_cross,
            "required": pol.require_ci_excludes_zero,
        },
        observe=bool(pol.observe_require_ci_excludes_zero),
    )

    _check(
        "holdout_trades",
        holdout_trades >= int(pol.min_holdout_trades),
        detail={"holdout_trades": holdout_trades, "min": pol.min_holdout_trades},
        observe=False,
    )
    _check(
        "holdout_trades_observe",
        holdout_trades >= int(pol.observe_min_holdout_trades),
        detail={
            "holdout_trades": holdout_trades,
            "min": pol.observe_min_holdout_trades,
        },
        live=False,
    )
    _check(
        "oos_trades",
        oos_trades >= int(pol.min_oos_trades),
        detail={"total_oos_trades": oos_trades, "min": pol.min_oos_trades},
        observe=False,
    )
    pf_ok = pf is not None and pf >= float(pol.min_profit_factor)
    _check(
        "profit_factor",
        pf_ok if pf is not None else False,
        detail={"profit_factor": pf, "min": pol.min_profit_factor},
        observe=False,
    )
    mean_ok = mean_oos is not None and mean_oos > float(pol.min_mean_oos_pnl)
    _check(
        "mean_oos_pnl",
        mean_ok if mean_oos is not None else False,
        detail={"mean_oos_pnl": mean_oos, "min": pol.min_mean_oos_pnl},
    )

    robust_tickers = {
        str(h.get("ticker") or "").strip().upper()
        for h in robust_hits
        if str(h.get("ticker") or "").strip()
    }
    _check(
        "tickers_robust",
        len(robust_tickers) >= int(pol.min_tickers_robust),
        detail={
            "count": len(robust_tickers),
            "min": pol.min_tickers_robust,
            "tickers": sorted(robust_tickers),
        },
        observe=False,
    )
    _check(
        "tickers_robust_observe",
        len(robust_tickers) >= int(pol.observe_min_tickers_robust),
        detail={
            "count": len(robust_tickers),
            "min": pol.observe_min_tickers_robust,
            "tickers": sorted(robust_tickers),
        },
        live=False,
    )

    chosen = dict(best.get("chosen") or {})
    if pol.require_params_applied and plugin_params is not None:
        params_ok, mismatches = params_match_chosen(chosen, plugin_params)
        _check(
            "params_applied",
            params_ok or not chosen,
            detail={
                "chosen": chosen,
                "mismatches": mismatches,
                "hint": "phantom_apply_chosen" if mismatches else None,
            },
        )
    else:
        _check(
            "params_applied",
            True,
            detail="skipped" if plugin_params is None else "policy_off",
        )

    live_blockers = [b for b in blockers if b.get("live", True)]
    observe_blockers = [b for b in blockers if b.get("observe", True)]
    live_ok = not live_blockers
    observe_ok = not observe_blockers
    if live_ok:
        grade = GRADE_LIVE
    elif observe_ok:
        grade = GRADE_OBSERVE
    else:
        grade = GRADE_RESEARCH

    live_checks = [c for c in checks if c.get("live", True)]
    score = (
        sum(1.0 for c in live_checks if c.get("ok")) / len(live_checks)
        if live_checks
        else 0.0
    )

    return {
        "ok": True,
        "strategy_id": sid,
        "grade": grade,
        "score": round(score, 4),
        "live_ok": live_ok,
        "observe_ok": observe_ok,
        "checks": checks,
        "blockers": live_blockers if not live_ok else [],
        "observe_blockers": observe_blockers if not observe_ok else [],
        "entries": hits,
        "best_entry": best,
        "robust_tickers": sorted(robust_tickers),
        "policy": pol.to_dict(),
        # The per-strategy evidence the batch-level multiplicity pass corrects. It
        # sits at the top level rather than inside `metrics` because it is an input
        # to the batch decision, not a descriptive statistic of this row.
        "p_value": p_value,
        # Two numbers about how much was tried: this run's grid, and the cumulative
        # count across every evaluation on record. The second is the honest
        # denominator for a correction -- the rules were drawn from a space that
        # includes the attempts that were discarded -- and it existed in the store
        # being read by nobody.
        "variants_count": _i(best.get("variants_count")),
        "variants_tried_lifetime": _i(best.get("variants_tried_lifetime")),
        "variants_available_lifetime": _i(best.get("variants_available_lifetime")),
        "oos_pnl_by_fold": [
            float(v) for v in (best.get("oos_pnl_by_fold") or [])
        ],
        "metrics": {
            "holdout_trades": holdout_trades,
            "total_oos_trades": oos_trades,
            "profit_factor": pf,
            "mean_oos_pnl": mean_oos,
            "ci_crosses_zero": any_ci_cross,
            "p_value": p_value,
            "chosen": chosen,
        },
    }


def live_promotion_blockers(
    strategy_ids: list[str],
    *,
    ticker: str | None = None,
    tickers: Sequence[str] | None = None,
    path: str | Path | None = None,
    require_robust_sign: bool = True,
    policy: LiveReadinessPolicy | None = None,
    plugin_params_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    raw_cfg: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return blockers for strategies that are not live_ok under the readiness policy."""
    base = policy or LiveReadinessPolicy()
    if not require_robust_sign:
        data = base.to_dict()
        data["require_robust_sign"] = False
        base = LiveReadinessPolicy.from_mapping(data)
    out: list[dict[str, Any]] = []
    for sid in strategy_ids:
        sid_s = str(sid or "").strip()
        if not sid_s:
            continue
        params = None
        if plugin_params_by_id and sid_s in plugin_params_by_id:
            params = plugin_params_by_id[sid_s]
        elif raw_cfg is not None:
            params = plugin_params_from_config(raw_cfg, sid_s)
        result = evaluate_live_readiness(
            sid_s,
            ticker=ticker,
            tickers=tickers,
            path=path,
            policy=base,
            plugin_params=params,
        )
        if result.get("live_ok"):
            continue
        blockers = list(result.get("blockers") or [])
        if not blockers:
            blockers = [
                {
                    "strategy_id": sid_s,
                    "reason": "not_live_ok",
                    "grade": result.get("grade"),
                }
            ]
        for b in blockers:
            b.setdefault("grade", result.get("grade"))
            b.setdefault("score", result.get("score"))
            out.append(b)
    return out


def batch_research_verdict(
    rows: Sequence[Mapping[str, Any]],
    *,
    alpha: float = 0.05,
    samples: int = 500,
    seed: int = 42,
    pbo_splits: int = 8,
    universe_basis: str = "available",
) -> dict[str, Any]:
    """Run the whole family of corrections over one batch, and report each verdict.

    Four tests, four questions, and the batch is judged on all four rather than on
    the one that happens to be implemented:

    - **Holm-corrected p-values** (`apply_multiplicity`, applied by the caller) ask
      "is this strategy significant after paying for having looked at N?"
    - **Reality Check** asks "would the best of N have beaten zero by chance?"
      (White 2000).
    - **SPA** asks the same question with the statistic studentized and the erratic
      models trimmed, which is more powerful and less sensitive to a single hopeless
      alternative (Hansen 2005).
    - **PBO** asks something the other three cannot: is the *ranking* stable? A
      corrected p-value can pass while the winner is arbitrary, because the
      in-sample best was chosen out of strategies that are statistically
      indistinguishable from it. In that case every reported number is correct and
      the selection is still noise.

    **The universe, not the batch (Sullivan, Timmermann & White 1999).** The
    denominator here is the strategies *actually present in the batch*, inflated by
    each record's `variants_tried_lifetime`. That is the point of the Sullivan paper
    in one line: the correction must cover the whole space the rules were drawn
    from, including the ones never reported, or it is a correction over a subset and
    is always too lenient.

    `variants_tried_lifetime` already existed in this store, described in its own
    comment as "the denominator a deflation argument needs", and nothing read it.
    It is read here.

    Every method is reported even when it does not apply, with a reason. A silent
    omission reads as a pass, which is how an unavailable test becomes an
    acceptable one.
    """
    items = [dict(r) for r in rows]
    series: list[list[float]] = []
    names: list[str] = []
    universe = 0
    for row in items:
        sid = str(row.get("strategy_id") or "")
        # The space the rules were drawn from, not the number that survived into
        # this report, and not the number that happened to be explored either.
        #
        # The reported configuration is a maximum over a search whose reach was
        # arbitrary: these strategies declare 4-6 numeric parameters, and walk-forward
        # evaluates 3 combinations out of the 16 or 36 the schema permits. An analyst
        # running the same pipeline with `max_variants=36` gets a different winner,
        # so the reproducible denominator is the *available* space. Measured on this
        # repo it is about 6x the explored one.
        #
        # `universe_basis` exists because this is a judgement and not a fact: set it
        # to "explored" for the narrower, more permissive reading.
        if universe_basis == "explored":
            per_strategy = (
                _i(row.get("variants_tried_lifetime"))
                or _i(row.get("variants_count"))
                or 1
            )
        else:
            # `max`, not a chain of `or`: a record whose available count is stale
            # or simply wrong must never make the correction *smaller* than what was
            # demonstrably explored, or a bad field would loosen the gate rather
            # than tightening it.
            per_strategy = max(
                1,
                _i(row.get("variants_available_lifetime")),
                _i(row.get("variants_tried_lifetime")),
                _i(row.get("variants_count")),
            )
        universe += max(1, per_strategy)
        folds = [
            float(v)
            for v in (row.get("oos_pnl_by_fold") or [])
            if v is not None
        ]
        if len(folds) >= 4:
            series.append(folds)
            names.append(sid)

    out: dict[str, Any] = {
        "strategies_in_batch": len(items),
        "strategies_with_series": len(series),
        "universe": universe,
    }

    if len(series) < 2:
        out["reality_check"] = {
            "ok": False,
            "reason": "fewer_than_two_series_on_record",
        }
        out["spa"] = dict(out["reality_check"])
        out["pbo"] = dict(out["reality_check"])
        out["verdict"] = "not_evaluable"
        return out

    from .simulation import cscv_pbo, reality_check, spa_test

    rc = reality_check(series, samples=samples, seed=seed, alpha=alpha)
    spa = spa_test(series, samples=samples, seed=seed, alpha=alpha)
    pbo = cscv_pbo(series, splits=pbo_splits)
    out["reality_check"] = rc
    out["spa"] = spa
    out["pbo"] = pbo

    if rc.get("best_strategy_index") is not None and names:
        idx = _i(rc["best_strategy_index"])
        if 0 <= idx < len(names):
            out["best_strategy"] = names[idx]

    # A batch passes only if the best result survives the whole family. Taking the
    # most favourable of four tests would be the same mistake as picking the best
    # of twenty-one strategies, one level up.
    #
    # `reject` means the *null* was rejected, i.e. an edge was found. It is named from
    # the test's point of view, not the strategy's, and reading it the other way round
    # inverts the whole gate: it declares a batch of noise to have an edge and
    # declares a dominant strategy to have none. That happened here once and is
    # pinned by `test_a_dominant_strategy_passes_and_a_noise_batch_does_not`.
    unstable = bool(pbo.get("ok")) and float(pbo.get("pbo") or 0.0) > 0.5
    passes = bool(rc.get("ok") and spa.get("ok")) and bool(
        rc.get("reject") is True and spa.get("reject") is True
    )
    if not passes:
        out["verdict"] = "no_batch_edge"
    elif unstable:
        # Significance without a stable ranking: the edge may be real and the
        # *choice* of which strategy carries it may still be noise.
        out["verdict"] = "unstable_selection"
    else:
        out["verdict"] = "batch_edge_stable"

    out["notes"] = {
        "universe_uses_lifetime_variants": True,
        "universe_basis": universe_basis,
        "series_granularity": "per_fold_oos_pnl",
        "pbo_worse_than_random": unstable,
    }
    return out


def apply_multiplicity(
    rows: Sequence[Mapping[str, Any]],
    *,
    alpha: float = 0.05,
    method: str = "holm",
) -> dict[str, Any]:
    """Correct a batch of per-strategy verdicts for how many were measured.

    Each row is judged alone at first -- its interval either clears zero or it does
    not. That is a valid test, and it is also exactly the test you must not run N
    times without paying for it. At a 95% level, twenty-eight independent strategies
    all built from noise produce about 1.4 that clear zero on average, and the
    unlucky run produces several. Ranking them then promoting the best is selecting
    on noise, which is why the winner's interval looks convincing and is not.

    Correcting is the only honest response, and it has to happen here rather than
    inside the per-strategy evaluation: multiplicity is a property of a *set*, and
    no single strategy can know how many others were measured alongside it.

    A row whose p-value is unknown -- a clearance written before p-values existed,
    or a strategy with too few trades to resample -- is treated as failing. That is
    deliberate. Missing evidence is not weak evidence for promotion, and the
    alternative is a set of records that quietly pass because they predate the
    field. Such rows get ``p_value = None`` in the output rather than a fabricated
    number, so the report cannot be read as "measured and insignificant".

    Returns a summary carrying the corrected p-value, the batch size and the
    correction method, and mutates each row's ``checks``/``blockers`` in place to
    record the outcome -- the same shape every other gate check uses, so existing
    consumers of ``live_ok`` need no changes.
    """
    from .simulation import adjust_pvalues

    items = [dict(row) for row in rows]
    if not items:
        # Same keys as the populated case, so a consumer never has to branch on
        # whether there was anything to correct.
        return {
            "rows": [],
            "multiplicity_corrected": False,
            "multiplicity_batch_size": 0,
            "multiplicity_method": str(method or "holm"),
            "multiplicity_alpha": float(alpha),
            "multiplicity_measured": 0,
            "multiplicity_unknown": 0,
            "multiplicity_ok": [],
            "multiplicity_fail": [],
        }

    known: list[int] = []
    raw_p: list[float] = []
    for index, row in enumerate(items):
        value = _f(row.get("p_value"))
        if value is None or not (0.0 <= value <= 1.0):
            continue
        known.append(index)
        raw_p.append(value)

    method_norm = str(method or "holm").lower().replace("_", "-")
    adjusted: dict[int, float] = {}
    if raw_p:
        try:
            corrected = adjust_pvalues(raw_p, method_norm)
        except ValueError:
            corrected = adjust_pvalues(raw_p, "holm")
            method_norm = "holm"
        for index, value in zip(known, corrected):
            adjusted[index] = float(value)

    alpha_f = float(alpha)
    ok_ids: list[str] = []
    fail_ids: list[str] = []
    unknown = 0

    for index, row in enumerate(items):
        sid = str(row.get("strategy_id") or "")
        if index in adjusted:
            p_adjusted = adjusted[index]
            passed = p_adjusted < alpha_f
            detail: dict[str, Any] = {
                "p_value": _f(row.get("p_value")),
                "p_adjusted": p_adjusted,
                "method": method_norm,
                "batch_size": len(items),
                "alpha": alpha_f,
                "known_p_values": len(raw_p),
            }
            known_reason = "multiplicity"
        else:
            p_adjusted = None
            passed = False
            unknown += 1
            detail = {
                "p_value": None,
                "p_adjusted": None,
                "method": method_norm,
                "batch_size": len(items),
                "alpha": alpha_f,
                "known_p_values": len(raw_p),
                "reason_detail": "no_p_value_on_record",
            }
            known_reason = "multiplicity_unknown_p_value"

        row["p_value_adjusted"] = p_adjusted
        row["multiplicity_batch_size"] = len(items)

        checks = list(row.get("checks") or [])
        checks.append(
            {
                # Two different failures that share a fix. Naming them apart matters
                # on a real snapshot: every clearance written before p-values
                # existed reports the unknown case, and if that printed as
                # `multiplicity` on all twenty rows the name would stop carrying
                # information and a genuine failure later would be lost in it.
                "name": known_reason if not passed and p_adjusted is None
                else "multiplicity",
                "ok": passed,
                "detail": detail,
                "live": True,
                "observe": True,
            }
        )
        row["checks"] = checks

        blockers = list(row.get("blockers") or [])
        if not passed:
            blockers.append(
                {
                    "reason": known_reason,
                    "detail": detail,
                    "grade": row.get("grade"),
                    "score": row.get("score"),
                }
            )
            # Failing here can never promote a row, only withhold one. Demoting both
            # grades is the conservative direction: a strategy that cannot be shown
            # significant after correcting for its own batch has not earned live
            # voting, and observe exists to watch, not to trade.
            row["live_ok"] = False
            row["observe_ok"] = False
        row["blockers"] = blockers

        if passed:
            ok_ids.append(sid)
        else:
            fail_ids.append(sid)

    return {
        "rows": items,
        "multiplicity_corrected": True,
        "multiplicity_batch_size": len(items),
        "multiplicity_method": method_norm,
        "multiplicity_alpha": alpha_f,
        "multiplicity_measured": len(raw_p),
        "multiplicity_unknown": unknown,
        "multiplicity_ok": ok_ids,
        "multiplicity_fail": fail_ids,
    }


def evaluate_many(
    strategy_ids: Sequence[str],
    *,
    path: str | Path | None = None,
    policy: LiveReadinessPolicy | None = None,
    raw_cfg: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    pol = policy or policy_from_config(raw_cfg)
    rows = []
    for sid in strategy_ids:
        sid_s = str(sid or "").strip()
        if not sid_s:
            continue
        rows.append(
            evaluate_live_readiness(
                sid_s,
                path=path,
                policy=policy,
                plugin_params=plugin_params_from_config(raw_cfg, sid_s)
                if raw_cfg is not None
                else None,
            )
        )
    multiplicity: dict[str, Any] = {}
    if pol.require_multiplicity_correction and rows:
        corrected_rows = apply_multiplicity(
            rows,
            alpha=pol.multiplicity_alpha,
            method=pol.multiplicity_method,
        )
        rows = list(corrected_rows.get("rows") or rows)
        multiplicity = {k: v for k, v in corrected_rows.items() if k != "rows"}

    batch: dict[str, Any] = {}
    if pol.require_batch_verdict and rows:
        batch = batch_research_verdict(
            rows,
            alpha=pol.multiplicity_alpha,
            samples=pol.batch_bootstrap_samples,
            pbo_splits=int(pol.pbo_splits or 8),
            universe_basis=str(pol.universe_basis or "available"),
        )
        verdict = str(batch.get("verdict") or "not_evaluable")
        passing = verdict == "batch_edge_stable"
        # Three distinct outcomes, three distinct names. Reporting "not evaluable"
        # as "no edge" is how a clearance store written before this field existed
        # turns into twenty negative research results -- the tests were never run,
        # and the label claims they were and found nothing. It fails closed either
        # way, but it says what actually happened.
        reason = {
            "batch_edge_stable": None,
            "unstable_selection": "batch_unstable_selection",
            "no_batch_edge": "batch_no_edge",
        }.get(verdict, "batch_not_evaluable")
        detail = {
            "verdict": verdict,
            "universe": batch.get("universe"),
            "strategies_with_series": batch.get("strategies_with_series"),
            "pbo": (batch.get("pbo") or {}).get("pbo"),
            "max_pbo": pol.max_pbo,
            "reality_check_p": (batch.get("reality_check") or {}).get("p_value"),
            "spa_p": (batch.get("spa") or {}).get("p_value"),
            "best_strategy": batch.get("best_strategy"),
        }
        for row in rows:
            checks = list(row.get("checks") or [])
            checks.append(
                {
                    "name": reason or "batch_edge_stable",
                    "ok": passing,
                    "detail": detail,
                    "live": True,
                    "observe": True,
                }
            )
            row["checks"] = checks
            if not passing:
                row["live_ok"] = False
                row["observe_ok"] = False
                blockers = list(row.get("blockers") or [])
                blockers.append(
                    {
                        "reason": reason,
                        "detail": detail,
                        "grade": row.get("grade"),
                        "score": row.get("score"),
                    }
                )
                row["blockers"] = blockers
        batch["rows_adjusted"] = len(rows)

    rows.sort(
        key=lambda r: (
            bool(r.get("live_ok")),
            bool(r.get("observe_ok")),
            float(r.get("score") or 0),
        ),
        reverse=True,
    )
    return {
        "ok": True,
        "count": len(rows),
        "live_ok": [r["strategy_id"] for r in rows if r.get("live_ok")],
        "observe_ok": [
            r["strategy_id"]
            for r in rows
            if r.get("observe_ok") and not r.get("live_ok")
        ],
        "research_only": [r["strategy_id"] for r in rows if not r.get("observe_ok")],
        "multiplicity": multiplicity,
        "batch": batch,
        "rows": rows,
    }


def apply_canary_quantity(
    raw_cfg: dict[str, Any],
    strategy_ids: Sequence[str],
    *,
    scale: float,
) -> dict[str, Any]:
    """Scale plugin quantity for newly promoted live strategies; return applied map."""
    applied: dict[str, Any] = {}
    scale_f = float(scale)
    if scale_f <= 0 or scale_f >= 1.0:
        return applied
    for sid in strategy_ids:
        sid_s = str(sid or "").strip()
        if not sid_s or sid_s not in raw_cfg:
            continue
        block = raw_cfg.get(sid_s)
        if not isinstance(block, dict):
            continue
        try:
            qty = float(block.get("quantity"))
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        new_qty = qty * scale_f
        block["quantity"] = new_qty
        raw_cfg[sid_s] = block
        applied[sid_s] = {"quantity_before": qty, "quantity_after": new_qty, "scale": scale_f}
    return applied
