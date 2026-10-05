"""Is the p90 threshold actually producing ~10% false positives?

Every negative result in this project rests on a number nobody has checked. We
compared "12 of 84 windows cleared p90 of their own null" against "8.4
expected", where the expectation comes from treating the 84 windows as
independent trials. They are not. They are adjacent stretches of one market,
sharing a trend, a volatility regime and a set of participants.

That cuts both ways and we do not know which way:

  - if correlated windows *inflate* the false-positive rate, then the strategies
    that looked notable were mostly noise and the verdicts stand, with room to
    spare;
  - if the real rate is *lower* than nominal, the whole method is too soft and
    every "finding" this project ever reported has to be re-read.

So the expectation gets measured instead of assumed. Synthetic series are the
right instrument for exactly this, and only for this: a random walk has no edge
by construction, so any strategy that clears p90 against it is a false positive
*by definition*. Generating prices cannot find an edge -- but it can measure
whether the measuring works.

The point of the synthetic draw is the correlation, not the randomness. Three
structures are sampled independently:

  `independent`  windows share nothing, strategies share nothing. The nominal
                 10% should reproduce, and if it does not, the percentile maths
                 is wrong rather than the assumption.
  `market`       all strategies share one factor per window, so within a window
                 the cross-section moves together -- the regime effect that made
                 window three on BTC reward every strategy at once.
  `serial`       windows are also correlated with each other, as real windows are.

The first is a check on the arithmetic. The other two are the actual question,
and `market` is the one observed in the data rather than assumed.

Reported as a rate, with the deviation in percentage points, because "12 of 84"
against an assumed 8.4 is a difference that has to be stated numerically before
any verdict built on it can be trusted.
"""

from __future__ import annotations

import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from tools.window_stability import _percentile  # noqa: E402

TRIALS = int(os.environ.get("CAL_TRIALS") or 200)
SEEDS = int(os.environ.get("CAL_SEEDS") or 40)
STRATEGIES = int(os.environ.get("CAL_STRATEGIES") or 21)
WINDOWS = int(os.environ.get("CAL_WINDOWS") or 4)
PER_WINDOW = 64


@dataclass(slots=True)
class Result:
    label: str
    cleared: int
    total: int

    @property
    def rate(self) -> float:
        return 100.0 * self.cleared / self.total if self.total else 0.0


def _draw(
    rng: random.Random,
    *,
    structure: str,
    strategies: int,
    windows: int,
    per_window: int,
) -> int:
    """Count strategy-windows clearing p90 of a random-timing null.

    Returns are per-bar log returns. `market` gives every strategy one shared
    factor per window, which is what makes strategies inside a window move
    together. `serial` additionally carries a factor across windows.
    """
    # Window-level drift: the regime effect. Under `independent` each window is
    # centred; under the correlated structures it is not, because real windows
    # are not.
    window_drift = [0.0] * windows
    if structure in {"market", "serial"}:
        window_drift = [rng.gauss(0, 0.0025) for _ in range(windows)]
    serial_carry = [rng.gauss(0, 0.0015) for _ in range(windows)] if (
        structure == "serial"
    ) else None

    # The null: random timing, same windows, same costs. Drawn first because the
    # comparison is against it.
    null: list[list[float]] = []
    for w in range(windows):
        pool = []
        for _ in range(SEEDS):
            carry = serial_carry[w] if serial_carry else 0.0
            pool.append(sum(rng.gauss(window_drift[w] + carry, 0.02) for _ in range(per_window)))
        null.append(pool)

    cleared = 0
    for w in range(windows):
        factor = (
            sum(rng.gauss(window_drift[w], 0.02) for _ in range(per_window))
            if structure in {"market", "serial"}
            else 0.0
        )
        for _s in range(strategies):
            # A strategy with no edge earns exactly what the market factor earns
            # plus its own noise. Independence by construction.
            idiosyncratic = sum(
                rng.gauss(0, 0.02) for _ in range(per_window)
            )
            net = factor + idiosyncratic
            if _percentile(net, null[w]) >= 90.0:
                cleared += 1
    return cleared


def main() -> int:
    print(
        f"calibrating the threshold: {TRIALS} trials x {STRATEGIES} strategies "
        f"x {WINDOWS} windows, null of {SEEDS} seeds"
    )
    print("nominal false-positive rate at p90: 10.0%\n")

    rows: list[Result] = []
    for structure in ("independent", "market", "serial"):
        rng = random.Random(20261005)
        total = 0
        for _ in range(TRIALS):
            total += _draw(
                rng,
                structure=structure,
                strategies=STRATEGIES,
                windows=WINDOWS,
                per_window=PER_WINDOW,
            )
        row = Result(structure, total, TRIALS * STRATEGIES * WINDOWS)
        rows.append(row)
        expected = row.total * 0.10
        print(
            f"  {structure:12s} cleared {row.cleared:5d} / {row.total:5d}  "
            f"= {row.rate:5.1f}%   expected {expected:7.1f}   "
            f"deviation {row.rate - 10.0:+5.1f} pp"
        )

    print()
    print(
        "Read: `independent` checks the arithmetic. The other two are the real\n"
        "case -- a shared factor per window is what made every strategy look\n"
        "good in the same window on real data, and adjacent windows share a\n"
        "regime the same way. A rate above 10% means the p90 threshold reports\n"
        "noise as signal and every prior 'finding' must be discounted by it."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())