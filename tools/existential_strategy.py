"""R-45: is there at least one strategy that beats random timing in EVERY window?

Registered S18 before running. 20 shipped strategies at default params, five
fixed 600-bar BTCUSDT 1h test windows, 7 bps per fill, fixed 1000 USD notional.
Two counts: raw positives (expected by luck, not evidence) and gated positives
(net above p90 of a same-activity random-timing null). Criterion: HOLDS iff
every window has at least one GATED strategy.
"""

from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.indicators import equity_pnl  # noqa: E402
from engine.research import decisions_to_fills  # noqa: E402
from tools.timeframe_verdict import _klines, _plugin, _series  # noqa: E402
from tools.gate_status import LINEUP  # noqa: E402

N_BARS = 6000
WINDOW = 600
N_WINDOWS = 5
FEE_BPS = 7.0
NOTIONAL = 1000.0
SEED = 450117
DRAWS = 200


def beats_null_and_profitable(net: float, p90: float) -> bool:
    """A gated strategy must be PROFITABLE and beat random timing.

    Comparing only against a deeply negative null lets a losing strategy pass:
    caught by a known-answer case (one window had raw positives=0 but gated=15).
    """
    return net > 0.0 and net > p90


SIZING = {
    "mode": "cash",
    "value": NOTIONAL,
    "max_position_pct": 1.0,
    "scale_by_strength": False,
}


def _window_metric(name: str, closes: list[float], series: dict, seed: int) -> tuple[float, float]:
    """Return (net, p90 of same-activity timing null) for one strategy+window."""
    cls = _plugin(name)
    assert cls is not None
    plugin = cls.__new__(cls)
    cfg = plugin.default_params()
    decisions = [plugin.evaluate_at(series, i, config=cfg) for i in range(len(closes))]
    fills = decisions_to_fills(
        decisions,
        closes,
        sizing=SIZING,
        fee_bps=FEE_BPS,
        score_from=0,
        ticker="BTCUSDT",
        position_mode="long_flat",
    )
    net = float(equity_pnl(fills, start_equity=10_000.0, mark_price=closes[-1])["pnl"])

    # Reconstruct held bars from the fills so the null keeps the same activity.
    held = [False] * len(closes)
    pos = False
    by_bar: dict[int, str] = {}
    for f in fills:
        by_bar[int(f["bar_index"])] = str(f["action"])
    for i in range(len(closes)):
        action = by_bar.get(i)
        if action == "BUY":
            pos = True
        elif action == "SELL":
            pos = False
        held[i] = pos
    returns = [0.0] + [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    q = sum(held[1:]) / max(1, len(held) - 1)
    import random

    rng = random.Random(seed)
    null_nets = []
    for _ in range(DRAWS):
        state = False
        gross = 0.0
        flips = 0
        for i in range(1, len(closes)):
            want = rng.random() < q
            if want != state:
                flips += 1
                state = want
            if state:
                gross += returns[i] * NOTIONAL
        null_nets.append(gross - flips * FEE_BPS / 1e4 * NOTIONAL)
    ordered = sorted(null_nets)
    pos_idx = max(0, min(1.0, 0.90)) * (len(ordered) - 1)
    lo, hi = int(pos_idx), int(math.ceil(pos_idx))
    p90 = ordered[lo] * (hi - pos_idx) + ordered[hi] * (pos_idx - lo) if hi != lo else ordered[lo]
    return net, p90


async def main() -> int:
    rows = await _klines("1h", N_BARS)
    print(f"fetched {len(rows)} BTCUSDT 1h klines")
    if len(rows) < N_BARS:
        print("NOT EVALUABLE: fewer bars than registered")
        return 1

    all_ok = True
    for w in range(N_WINDOWS):
        end = len(rows) - w * WINDOW
        start = end - WINDOW
        window_rows = rows[start:end]
        series = _series(window_rows)
        wcloses = series["close"]
        raw: list[str] = []
        gated: list[str] = []
        for name in LINEUP:
            net, p90 = _window_metric(name, wcloses, series, SEED + w)
            if net > 0:
                raw.append(name)
            if beats_null_and_profitable(net, p90):
                gated.append(name)
        ok = bool(gated)
        all_ok = all_ok and ok
        print(
            f"window {w + 1}: raw positives={len(raw):2d}  gated={len(gated):2d}  "
            f"{'HOLDS' if ok else 'FAILS'}  {gated[:4]}"
        )
    print(
        f"\nR-45 {'HOLDS' if all_ok else 'FAILS'}: "
        f"{'every window has a gated strategy' if all_ok else 'at least one window has none'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
