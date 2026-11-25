"""Product CLI for autonomous strategy selection.

Runs the drift-neutral timing evidence on a symbol/block, then hands it to
`engine.strategy_lab.evaluate_lab`. The lab can only select when power, both
controls and Holm over the whole candidate set all pass. Report is JSON.

    python tools/strategy_lab.py --symbol BTCUSDT --block earlier
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.strategy_lab import evaluate_lab  # noqa: E402
from tools.timing_skill_verdict import _run  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--block", choices=("earlier", "later"), default="earlier")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    _counts, aggregate = await _run(args.symbol, args.block)
    # R-58's power and controls were demonstrated for the AGGREGATE, not for
    # individual pairs. The product candidate is therefore the aggregate.
    report = evaluate_lab({"composite_aggregate": aggregate})
    report["symbol"] = args.symbol
    report["block"] = args.block
    print(json.dumps(report, indent=2, default=str))
    if args.out:
        from engine.paths import data_path

        out = Path(args.out) if Path(args.out).is_absolute() else data_path("research", args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"report written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
