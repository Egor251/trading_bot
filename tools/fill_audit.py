"""Census the fill journal: is there anything real to calibrate against?

Registered S10-1. Every row of `data/ops/trades.jsonl` is counted; fill-like
rows are checked against DOCUMENTED test markers (strategy 'a'/100.0,
test-price 159.0, FakeQuik 285.0, reason 'paper'); usability as an
`ExecutionObservation` additionally needs a reference (decision) price, which
journal rows structurally lack.

Usage:
  python tools/fill_audit.py              # live census, read-only
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

JOURNAL = ROOT / "data" / "ops" / "trades.jsonl"
ORDERS = ROOT / "data" / "execution" / "orders.json"

#: Prices that appear in the repo's own tests as synthetic, never venue data.
MOCK_PRICES = {100.0, 159.0, 285.0}
TEST_STRATEGIES = {"a", "test_strategy"}
FILL_REASONS = {"fill", "quik_fill", "executed"}


def markers(row: dict) -> list[str]:
    """Documented test markers present on a row (empty = no marker)."""
    out = []
    if row.get("strategy_id") in TEST_STRATEGIES:
        out.append("test-strategy")
    try:
        if float(row.get("price", 0.0)) in MOCK_PRICES:
            out.append("mock-price")
    except (TypeError, ValueError):
        pass
    if row.get("reason") == "paper":
        out.append("paper-sim")
    return out


def is_fill_like(row: dict) -> bool:
    return str(row.get("reason") or "") in FILL_REASONS or (
        row.get("fee") not in (None, 0, 0.0)
    )


def has_reference_price(row: dict) -> bool:
    meta = row.get("meta") or {}
    for key in ("reference_price", "decision_price", "ref_price"):
        try:
            if float(row.get(key, 0.0) or 0.0) > 0:
                return True
            if float(meta.get(key, 0.0) or 0.0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def main() -> int:
    recs = [
        json.loads(line)
        for line in JOURNAL.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(f"journal rows: {len(recs)}")
    fill_like = [r for r in recs if is_fill_like(r)]
    print(f"fill-like rows (fill reason or nonzero fee): {len(fill_like)}")
    marked = [(r, markers(r)) for r in fill_like]
    clean = [r for r, m in marked if not m]
    print(f"  with >=1 documented test marker: {len(fill_like) - len(clean)}")
    print(f"  marker-free: {len(clean)}")
    for r, m in marked:
        if m:
            print(f"    {r.get('timestamp')} {r.get('symbol')} {r.get('reason')} "
                  f"price={r.get('price')} fee={r.get('fee')} -> {','.join(m)}")
    for r in clean:
        print(f"    CLEAN? {r}")
    usable = [r for r in fill_like if not markers(r) and has_reference_price(r)]
    print(f"usable as ExecutionObservation (marker-free + reference price): "
          f"{len(usable)} (calibrate needs >= 30)")
    try:
        orders = json.loads(ORDERS.read_text(encoding="utf-8"))
        print(f"orders.json fills object: "
              f"{'EMPTY' if not orders.get('fills') else 'NON-EMPTY'}")
    except OSError as exc:
        print(f"orders.json unreadable: {exc}")
    print()
    if len(usable) >= 30:
        print("R-42: HOLDS — calibration report follows in a next step")
        return 0
    print("R-42: NOT EVALUABLE — no real fills with decision prices; "
          "unblocked by the first such fill (operational, not research)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
