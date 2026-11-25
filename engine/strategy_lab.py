"""Autonomous strategy-selection instrument with the discipline built in.

The project kept inventing ad-hoc checks by hand. This module is the reusable
product-side core: given per-candidate per-window evidence, it applies the
multiplicity correction, the power requirement and the no-edge controls, and
returns a selection ONLY when every gate passes. It cannot be asked to relax a
gate after seeing results -- thresholds live in a frozen config object.

Pure data in, report out; no I/O, no event bus, no live trading path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .simulation import adjust_pvalues


@dataclass(frozen=True, slots=True)
class LabConfig:
    """Frozen before the run. Changing it is a new decision, not a tuning step."""

    alpha: float = 0.05
    method: str = "holm"
    draws: int = 500
    seed: int = 480117
    min_windows: int = 3
    power_edge_bps: float = 2.0
    max_control_wins: int = 1


@dataclass(frozen=True, slots=True)
class WindowEvidence:
    """One candidate in one window. `power_p` is the injected-edge arm."""

    net: float
    p: float
    power_net: float
    power_p: float
    random_p: float
    shuffled_p: float

    def real_ok(self, alpha: float) -> bool:
        return self.net > 0.0 and self.p < alpha

    def power_ok(self, alpha: float) -> bool:
        return self.power_net > 0.0 and self.power_p < alpha

    def random_ok(self, alpha: float) -> bool:
        return self.random_p < alpha

    def shuffled_ok(self, alpha: float) -> bool:
        return self.shuffled_p < alpha


def evaluate_lab(
    candidates: Mapping[str, Sequence[WindowEvidence]],
    cfg: LabConfig | None = None,
) -> dict:
    """Decide which candidates, if any, earned promotion.

    Gates, in order: power on the reference candidate, dirty-control check per
    candidate, Holm over ALL candidates using each candidate's worst-window
    p-value, then the ≥min_windows requirement. A candidate is returned only
    if it clears every gate; otherwise `selected` is None with named blockers.
    """
    cfg = cfg or LabConfig()
    names = [str(n) for n in candidates if candidates[n]]
    report: dict = {
        "candidates": [],
        "selected": None,
        "blockers": [],
        "config": {
            "alpha": cfg.alpha, "method": cfg.method, "draws": cfg.draws,
            "seed": cfg.seed, "min_windows": cfg.min_windows,
            "power_edge_bps": cfg.power_edge_bps,
            "max_control_wins": cfg.max_control_wins,
        },
    }
    if not names:
        report["blockers"].append("no_candidates")
        return report

    # Worst window decides a candidate: passing on easy windows is not enough.
    worst_p = {
        n: max(float(e.p) for e in candidates[n]) for n in names
    }
    adjusted = dict(zip(names, adjust_pvalues(
        [worst_p[n] for n in names], cfg.method
    )))

    reference = names[0]
    power_wins = sum(
        e.power_ok(cfg.alpha) for e in candidates[reference]
    )
    if power_wins < cfg.min_windows:
        report["blockers"].append("power_inadequate")

    for name in names:
        rows = list(candidates[name])
        real_wins = sum(e.real_ok(cfg.alpha) for e in rows)
        random_wins = sum(e.random_ok(cfg.alpha) for e in rows)
        shuffled_wins = sum(e.shuffled_ok(cfg.alpha) for e in rows)
        entry = {
            "strategy": name,
            "windows": len(rows),
            "real_wins": real_wins,
            "random_wins": random_wins,
            "shuffled_wins": shuffled_wins,
            "worst_p": worst_p[name],
            "adjusted_p": adjusted[name],
            "window_nets": [e.net for e in rows],
            "window_p": [e.p for e in rows],
            "window_power_nets": [e.power_net for e in rows],
        }
        reasons = []
        if real_wins < cfg.min_windows:
            reasons.append("real_below_min_windows")
        if adjusted[name] >= cfg.alpha:
            reasons.append("multiplicity")
        if random_wins > cfg.max_control_wins:
            reasons.append("random_control_dirty")
        if shuffled_wins > cfg.max_control_wins:
            reasons.append("shuffled_control_dirty")
        entry["blockers"] = reasons
        report["candidates"].append(entry)

    eligible = [
        c for c in report["candidates"]
        if not c["blockers"] and "power_inadequate" not in report["blockers"]
    ]
    if not eligible:
        report["blockers"].append("no_eligible_candidate")
        return report
    # The correction already paid for the number of candidates; ranking the
    # survivors by net would be a second selection. Return all survivors.
    report["selected"] = [c["strategy"] for c in eligible]
    return report
