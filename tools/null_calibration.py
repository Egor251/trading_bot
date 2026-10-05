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

import math
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


#: Per-bar log-return SD, shared by the null and the strategies so the comparison
#: is between two things that faced the same volatility.
SIGMA = 0.02


def _edge_per_window(phi: float, per_window: int) -> float:
    """Total return a perfect one-bar forecaster earns from autocorrelation phi.

    If ``r_t = phi * r_{t-1} + eps``, then knowing ``r_{t-1}`` makes
    ``phi * r_{t-1}`` of the next move knowable in advance. Taking the sign of the
    previous bar as the position earns ``phi * sigma * E|z|`` per bar, and for a
    standard normal ``E|z| = sqrt(2/pi) = 0.7979``. Against ``sigma`` of noise per
    bar, the window's signal-to-noise is therefore

        phi * sqrt(2/pi) * sqrt(per_window)

    which is what makes the sweep below readable in units of "how predictable the
    market would have to be", rather than in units of an arbitrary number of
    standard deviations that mean nothing outside this file.
    """
    return phi * SIGMA * math.sqrt(2.0 / math.pi) * per_window


def _snr(phi: float, per_window: int) -> float:
    """Signal-to-noise ratio of a perfect forecaster against the null."""
    return phi * math.sqrt(2.0 / math.pi) * math.sqrt(per_window)


def _draw(
    rng: random.Random,
    *,
    structure: str,
    strategies: int,
    windows: int,
    per_window: int,
    phi: float = 0.0,
) -> int:
    """Count strategy-windows clearing p90 of a random-timing null.

    Returns are per-bar log returns. `market` gives every strategy one shared
    factor per window, which is what makes strategies inside a window move
    together. `serial` additionally carries a factor across windows.

    `phi` injects a real, persistent edge into the strategies only. The null is
    random timing and cannot earn it, which is the whole point: phi = 0 is the
    false-positive calibration above, and phi > 0 asks whether the same comparison
    notices when there is genuinely something to find.
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
    edge = _edge_per_window(phi, per_window)

    # The null: random timing, same windows, same costs. Drawn first because the
    # comparison is against it.
    null: list[list[float]] = []
    for w in range(windows):
        pool = []
        for _ in range(SEEDS):
            carry = serial_carry[w] if serial_carry else 0.0
            pool.append(sum(rng.gauss(window_drift[w] + carry, SIGMA) for _ in range(per_window)))
        null.append(pool)

    cleared = 0
    for w in range(windows):
        factor = (
            sum(rng.gauss(window_drift[w], SIGMA) for _ in range(per_window))
            if structure in {"market", "serial"}
            else 0.0
        )
        for _s in range(strategies):
            # A strategy with no edge earns exactly what the market factor earns
            # plus its own noise. Independence by construction.
            idiosyncratic = sum(
                rng.gauss(0, SIGMA) for _ in range(per_window)
            )
            net = factor + edge + idiosyncratic
            if _percentile(net, null[w]) >= 90.0:
                cleared += 1
    return cleared


def power_table(trials: int) -> list[tuple[float, float, float]]:
    """Detection rate as a function of how predictable the market is.

    Registered before running, so the answer cannot be tuned afterwards:

      - the edge is measured in autocorrelation units, not in standard
        deviations, because only the former means anything about a real market;
      - `phi = 0` must reproduce the false-positive rate measured above, or the
        injection is not doing what it claims;
      - power counts as usable only if a rate clears the noise floor by more than
        5 pp at some `phi` that a real market could plausibly have.
    """
    rows: list[tuple[float, float, float]] = []
    for phi in (0.0, 0.05, 0.10, 0.20, 0.30, 0.50, 0.80):
        rng = random.Random(424242)
        cleared = sum(
            _draw(
                rng,
                structure="market",
                strategies=STRATEGIES,
                windows=WINDOWS,
                per_window=PER_WINDOW,
                phi=phi,
            )
            for _ in range(trials)
        )
        total = trials * STRATEGIES * WINDOWS
        rows.append((phi, _snr(phi, PER_WINDOW), 100.0 * cleared / total))
    return rows


def windows_table(trials: int) -> list[tuple[int, float]]:
    """Per-window detection against window count, at a fixed edge.

    This table is here to be *un*informative in a specific way, and the way it
    came out flat is the point. The p90 test is applied per window, so its power
    is a property of one window against one null and does not move with how many
    windows there are. More windows cannot buy detection.

    What more windows buy is the ability to make a claim at all: the p-value of a
    strategy that is right in every window falls as 2^-W while the threshold it
    has to clear falls as 0.05/S. That is exact arithmetic, not a simulation, and
    it is printed below for the window counts that matter.

    An earlier draft of this function claimed the opposite -- that power rises
    with windows -- and that was wrong. The first version of this tool reported
    it because it measured a family-level statistic under a per-window heading.
    """
    rows: list[tuple[int, float]] = []
    for windows in (4, 8, 16, 32):
        rng = random.Random(909090)
        cleared = sum(
            _draw(
                rng,
                structure="market",
                strategies=STRATEGIES,
                windows=windows,
                per_window=PER_WINDOW,
                phi=0.30,
            )
            for _ in range(trials)
        )
        total = trials * STRATEGIES * windows
        rows.append((windows, 100.0 * cleared / total))
    return rows


def reachability_table(strategies: int) -> list[tuple[int, float, bool]]:
    """Can any strategy clear Bonferroni at all, for a given window count?

    Exact. The best case a strategy can produce is being right in every window,
    which has probability 2^-W, and it must beat 0.05/S after correcting for the
    S strategies measured. No amount of data quality changes this line.
    """
    rows: list[tuple[int, float, bool]] = []
    for windows in (4, 8, 10, 16, 20, 32):
        best = 2.0 ** -windows
        rows.append((windows, best, best < 0.05 / strategies))
    return rows


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

    trials_str = str(TRIALS)
    power_rows = power_table(TRIALS)
    window_rows = windows_table(TRIALS)
    reachability_rows = reachability_table(STRATEGIES)

    print()
    print(
        "Read: `independent` checks the arithmetic. The other two are the real\n"
        "case -- a shared factor per window is what made every strategy look\n"
        "good in the same window on real data, and adjacent windows share a\n"
        "regime the same way. A rate above 10% means the p90 threshold reports\n"
        "noise as signal and every prior 'finding' must be discounted by it."
    )

    if os.environ.get("CAL_SKIP_POWER"):
        return 0

    print()
    print("=" * 72)
    print("POWER: can this comparison see a real edge at all?")
    print("=" * 72)
    print(
        f"{trials_str} trials x {STRATEGIES} strategies x {WINDOWS} windows, "
        f"null of {SEEDS} seeds, structure `market`\n"
    )
    print(
        "An edge is injected as predictable next-bar returns. `phi` is the share\n"
        "of each bar's move knowable in advance from the previous bar: the\n"
        "fraction of daily crypto return autocorrelation that would have to exist.\n"
    )
    print(f"  {'phi':>6} {'snr':>7} {'cleared':>9} {'vs noise':>10}")
    base = power_rows[0][2] if power_rows else 0.0
    for phi, snr, rate in power_rows:
        print(f"  {phi:6.2f} {snr:7.3f} {rate:8.1f}% {rate - base:+9.1f} pp")

    # The registered margin: 5 pp above the phi=0 rate is what "detected" means.
    detected = next(
        (row for row in power_rows if row[2] - base >= 5.0), None
    )
    if detected is None:
        print("\n  no phi in this sweep cleared the noise floor by 5 pp")
    else:
        print(
            f"\n  smallest edge detected above noise (+5 pp): phi = "
            f"{detected[0]:.2f}, snr {detected[1]:.2f}"
        )

    print()
    print("=" * 72)
    print("Per-window detection does NOT improve with more windows (phi = 0.30)")
    print("=" * 72)
    print(f"  {'windows':>8} {'cleared':>9}")
    for windows, rate in window_rows:
        print(f"  {windows:8d} {rate:8.1f}%")
    print(
        "\n  Flat, and it has to be: p90 is judged inside one window against one\n"
        "  null, so the number of windows is not in that calculation at all."
    )

    print()
    print("=" * 72)
    print("What more windows do buy: the ability to make a claim at all")
    print("=" * 72)
    print(f"  {'windows':>8} {'best p':>10} {'vs 0.05/S':>11}")
    for windows, best, ok in reachability_rows:
        print(
            f"  {windows:8d} {best:10.5f} "
            f"{'reachable' if ok else 'unreachable':>11}"
        )
    print(
        f"\n  best p = 2^-windows, threshold = 0.05/{STRATEGIES} = "
        f"{0.05 / STRATEGIES:.5f}. Exact, and no data quality changes it:\n"
        f"  with {WINDOWS} windows the best possible outcome is still "
        f"{2.0 ** -WINDOWS:.5f}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())