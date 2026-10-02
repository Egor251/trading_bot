"""Print the clearance entries for one strategy as a comparable table."""

from __future__ import annotations

import json
import sys
from pathlib import Path

CLEARANCE = Path("data/phantom/research_clearance.json")


def main() -> int:
    want = sys.argv[1] if len(sys.argv) > 1 else "dual_thrust"
    entries = json.loads(CLEARANCE.read_text(encoding="utf-8"))["entries"]
    rows = {k: v for k, v in entries.items() if v.get("strategy_id") == want}
    if not rows:
        print(f"no clearance entries for {want}")
        return 1
    print(
        f"{'entry':30}{'holdout':>10}{'trades':>8}{'robust':>8}"
        f"{'ci_zero':>9}{'mean_oos':>11}{'pf':>8}  ci"
    )
    for key in sorted(rows):
        v = rows[key]
        lo = v.get("ci_low")
        hi = v.get("ci_high")
        ci = f"[{lo:.1f},{hi:.1f}]" if lo is not None and hi is not None else "-"
        print(
            f"{key:30}{v.get('holdout_pnl') or 0:10.2f}"
            f"{v.get('holdout_trades') or 0:8}"
            f"{str(v.get('robust_sign_match')):>8}"
            f"{str(v.get('ci_crosses_zero')):>9}"
            f"{v.get('mean_oos_pnl') or 0:11.2f}"
            f"{(v.get('profit_factor') if v.get('profit_factor') is not None else 0):8.2f}"
            f"  {ci}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())