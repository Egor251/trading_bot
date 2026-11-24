"""The standing feedback loop: run every data-backed check in one command.

There is no market feedback in this project -- no live fills, no reference
prices, no calibrated fee schedule. The only signal that a change did not
break the instruments is this suite. Run it after every change:

    python tools/run_verification.py            # offline: synthetic + fixtures
    python tools/run_verification.py --live     # + real BTC/ETF historical data

Offline runs the pytest modules whose inputs are synthetic or on-disk fixtures,
plus each instrument's known-answer self-test. `--live` additionally re-fetches
BTC 1h klines (order flow + real-data ML) and re-audits the fill journal.
Network failures in --live are reported, never swallowed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PYTEST_MODULES = (
    "tests/test_neuroevolution_count.py",
    "tests/test_portfolio_held_null.py",
    "tests/test_portfolio_cross_null.py",
    "tests/test_orderflow_imbalance.py",
    "tests/test_fill_audit.py",
    "tests/test_gate_fail_closed.py",
    "tests/test_gate_wiring.py",
    "tests/test_batch_verdict.py",
    "tests/test_research_gate_enforced.py",
    "tests/test_research_multiplicity.py",
    "tests/test_simulation.py",
    "tests/test_reality_check_spa_pbo.py",
)

OFFLINE_STEPS: tuple[tuple[str, ...], ...] = (
    ("tools/neuroevolution_count.py", "--stage", "known"),
    ("tools/portfolio_trend.py", "--selftest"),
    ("tools/portfolio_trend.py", "--selftest2"),
)

LIVE_STEPS: tuple[tuple[str, ...], ...] = (
    ("tools/neuroevolution_count.py", "--stage", "main", "--data", "btc1h", "--seed", "41"),
    ("tools/orderflow_imbalance.py",),
    ("tools/fill_audit.py",),
)


#: Some tools use a non-zero exit to report a *registered finding*, not a
#: crash. `fill_audit` returning 1 means "NOT EVALUABLE: no real fills" -- the
#: expected answer today. Treating that as a verification failure would train
#: us to ignore the harness, which is worse than the hole it reports.
EXPECTED_CODES: dict[str, tuple[int, ...]] = {
    "tools/fill_audit.py": (0, 1),
}


def _run(args: tuple[str, ...]) -> bool:
    print("\n" + "=" * 72)
    print("RUN:", " ".join(args))
    print("=" * 72)
    result = subprocess.run([sys.executable, *args], cwd=ROOT, check=False)
    allowed = EXPECTED_CODES.get(args[0], (0,))
    if result.returncode not in allowed:
        print(f"FAILED ({result.returncode}): {' '.join(args)}")
        return False
    if result.returncode != 0:
        print(f"EXPECTED FINDING ({result.returncode}): {' '.join(args)}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    ok = _run(("-m", "pytest", "-q", *PYTEST_MODULES))
    for step in OFFLINE_STEPS:
        ok = _run(step) and ok
    if args.live:
        for step in LIVE_STEPS:
            ok = _run(step) and ok

    print("\n" + "=" * 72)
    print("VERIFICATION:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
