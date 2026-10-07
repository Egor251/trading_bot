"""Flat digests for walk-forward reports (batch tables, AI, apply-chosen)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def _f(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def enrich_fold_aggregates(report: dict[str, Any]) -> dict[str, Any]:
    """Attach OOS trade/DD aggregates onto ``report['oos']`` (mutates report)."""
    folds = list(report.get("folds") or [])
    oos = dict(report.get("oos") or {})
    trades = [int(f.get("oos_trades") or 0) for f in folds]
    dds = [_f(f.get("oos_max_drawdown")) for f in folds]
    oos["total_oos_trades"] = int(sum(trades))
    oos["mean_oos_trades"] = float(sum(trades) / len(trades)) if trades else 0.0
    oos["mean_oos_max_drawdown"] = float(sum(dds) / len(dds)) if dds else 0.0
    oos["max_oos_max_drawdown"] = float(max(dds)) if dds else 0.0
    # Aliases operators already use in tables
    if "win_rate" in oos:
        oos["win_rate_pct"] = round(100.0 * _f(oos.get("win_rate")), 1)
    report["oos"] = oos
    return report


def digest_walk_forward(report: Mapping[str, Any] | None) -> dict[str, Any]:
    """One-row summary suitable for batch tables and AI tool_digest."""
    if not isinstance(report, Mapping):
        return {"ok": False, "error": "empty_report"}
    oos = report.get("oos") if isinstance(report.get("oos"), Mapping) else {}
    prov = report.get("provenance") if isinstance(report.get("provenance"), Mapping) else {}
    baselines = (
        report.get("baselines") if isinstance(report.get("baselines"), Mapping) else {}
    )
    holdout = report.get("holdout") if isinstance(report.get("holdout"), Mapping) else {}
    chosen = report.get("chosen") if isinstance(report.get("chosen"), Mapping) else {}
    mean = _f(oos.get("mean_oos_pnl"))
    ci_lo = oos.get("ci_low")
    ci_hi = oos.get("ci_high")
    ci_crosses_zero = False
    if ci_lo is not None and ci_hi is not None:
        try:
            ci_crosses_zero = float(ci_lo) <= 0.0 <= float(ci_hi)
        except (TypeError, ValueError):
            ci_crosses_zero = False
    holdout_pnl = holdout.get("pnl")
    robust_sign = None
    if holdout_pnl is not None:
        try:
            robust_sign = (mean > 0 and float(holdout_pnl) > 0) or (
                mean < 0 and float(holdout_pnl) < 0
            )
            if mean == 0 or float(holdout_pnl) == 0:
                robust_sign = abs(mean) < 1e-9 and abs(float(holdout_pnl)) < 1e-9
        except (TypeError, ValueError):
            robust_sign = None
    return {
        "ok": True,
        "strategy_id": str(prov.get("strategy_id") or report.get("strategy_id") or ""),
        "ticker": str(prov.get("ticker") or report.get("ticker") or ""),
        "class_code": str(prov.get("class_code") or report.get("class_code") or ""),
        "timeframe": str(prov.get("timeframe") or ""),
        "bar_interval_sec": prov.get("bar_interval_sec"),
        # The window of bars this analysis actually covered. The research gate
        # judges clearance freshness on the last bar seen rather than on when the
        # run happened, so a stale cache cannot be re-dated into a valid clearance.
        "data_start": prov.get("data_start"),
        "data_end": prov.get("data_end"),
        "provider_resolved": str(prov.get("provider_resolved") or ""),
        "variant_mode": str(prov.get("variant_mode") or ""),
        "variants_count": int(prov.get("variants_count") or report.get("variants") or 0),
        # How much configuration space was *available*, as against how much was
        # explored. The reported configuration is a maximum over a search whose reach
        # was arbitrary, so the Sullivan correction has to be computed over the
        # available space. Absent on reports written before this field existed, in
        # which case the gate falls back to the explored count and says so.
        "variants_available": int(prov.get("variants_available") or 0),
        "folds": int(oos.get("folds") or len(report.get("folds") or [])),
        "chosen": dict(chosen),
        "mean_oos_pnl": mean,
        "ci_low": _f(ci_lo) if ci_lo is not None else None,
        "ci_high": _f(ci_hi) if ci_hi is not None else None,
        "ci_crosses_zero": ci_crosses_zero,
        # Null-hypothesis p-value for the mean, from the recentred block bootstrap.
        # Absent on older reports rather than defaulted to 0.05: a record written
        # before this field existed has *unknown* evidence, and treating unknown as
        # "p = 0.05, fails the corrected bar" is the safe direction to be wrong in.
        "p_value": _f(oos.get("p_value")),
        # The per-fold OOS P&L series. A corrected p-value is not enough for the
        # batch-level tests -- those bootstrap a series per strategy -- and a
        # clearance record that carried only a p-value could never be re-examined
        # with a stronger method without being regenerated.
        "oos_pnl_by_fold": [
            _f(v) for v in (oos.get("oos_pnl_by_fold") or [])
        ],
        "win_rate": _f(oos.get("win_rate")),
        "profit_factor": _f(oos.get("profit_factor")),
        "total_oos_trades": int(oos.get("total_oos_trades") or 0),
        "mean_oos_trades": _f(oos.get("mean_oos_trades")),
        "mean_oos_max_drawdown": _f(oos.get("mean_oos_max_drawdown")),
        "max_oos_max_drawdown": _f(oos.get("max_oos_max_drawdown")),
        "edge_vs_buy_hold": _f(baselines.get("edge_vs_buy_hold"))
        if baselines
        else None,
        "cost_drag": _f(baselines.get("cost_drag")) if baselines else None,
        "holdout_bars": int(holdout.get("bars") or 0) if holdout else 0,
        "holdout_pnl": _f(holdout_pnl) if holdout_pnl is not None else None,
        "holdout_trades": int(holdout.get("trades") or 0) if holdout else 0,
        "holdout_max_drawdown": _f(holdout.get("max_drawdown"))
        if holdout and holdout.get("max_drawdown") is not None
        else None,
        "robust_sign_match": robust_sign,
        "report_path": report.get("report_path") or prov.get("report_path"),
    }


def rank_batch_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Sort digests by mean OOS PnL descending."""
    out = [dict(r) for r in rows if isinstance(r, Mapping)]
    out.sort(key=lambda r: _f(r.get("mean_oos_pnl")), reverse=True)
    for i, row in enumerate(out):
        row["rank"] = i + 1
    return out
