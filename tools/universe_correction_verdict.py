"""R-42: does the available-space multiplicity correction change the gate?

Registered S16 before running. Same store, same strategies, same Holm at
0.05; only the family size changes (batch N vs available U). The criterion is
fixed: HOLDS iff live_ok drops when corrected over U.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from engine.research_gate import evaluate_many, policy_from_config  # noqa: E402
from tools.gate_status import LINEUP  # noqa: E402

import yaml  # noqa: E402


def main() -> int:
    raw = yaml.safe_load((ROOT / "config" / "trading_bot.yaml").read_text(encoding="utf-8"))
    policy = policy_from_config(raw)
    narrow = evaluate_many(LINEUP, policy=policy)
    wide = evaluate_many(LINEUP, policy=policy, use_universe_correction=True)

    def summary(out, label):
        rows = out.get("rows") or []
        adjusted = [r["p_value_adjusted"] for r in rows if r.get("p_value_adjusted") is not None]
        print(
            f"{label}: live_ok={len(out.get('live_ok') or [])} "
            f"universe={out['multiplicity'].get('multiplicity_universe')} "
            f"p_adj min={min(adjusted):.5f} max={max(adjusted):.5f}"
        )
        return out.get("live_ok") or []

    n_live = summary(narrow, "batch N   ")
    w_live = summary(wide, "available U")
    held = len(w_live) < len(n_live)
    print(f"\nR-42 {'HOLDS' if held else 'FAILS'}: live_ok {len(n_live)} -> {len(w_live)}")
    print("dropped:", sorted(set(n_live) - set(w_live)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
