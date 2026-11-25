"""R-49: composite entry/exit with a 2% stop and 10-bar recompute cooldown.

Registered S22: same six pairs, four holdouts, same null/Holm budget as R-48.
HOLDS iff one pair beats baseline AND is profitable AND Holm-significant in
all four windows.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.simulation import adjust_pvalues  # noqa: E402
from engine.strategy_plugin import StrategyDecision  # noqa: E402
from tools.composite_strategy_verdict import (  # noqa: E402
    LENGTHS,
    N_BARS,
    PAIRS,
    REPS,
    SEED,
    TRAIN,
    VAL,
    _actions,
    _composite,
    _net_and_p,
)
from tools.neuroevolution_count import build_real_rows  # noqa: E402
from tools.timeframe_verdict import _klines, _series  # noqa: E402

STOP = 0.02
COOLDOWN = 10


def _with_stop(entry: list[str], exit_: list[str], closes: list[float]) -> list[StrategyDecision]:
    out: list[StrategyDecision] = []
    long = False
    entry_px = 0.0
    cooldown = 0
    for i, (a, b) in enumerate(zip(entry, exit_)):
        px = closes[i]
        if long:
            if px <= entry_px * (1.0 - STOP):
                out.append(StrategyDecision(action="SELL", strength=1.0))
                long, cooldown = False, COOLDOWN
            elif b in {"SELL", "SHORT"}:
                out.append(StrategyDecision(action="SELL", strength=1.0))
                long = False
            else:
                out.append(StrategyDecision(action="HOLD", strength=1.0, intent="long"))
        elif cooldown > 0:
            cooldown -= 1
            out.append(StrategyDecision(action="HOLD", strength=0.0))
        elif a in {"BUY", "LONG"}:
            out.append(StrategyDecision(action="BUY", strength=1.0))
            long, entry_px = True, px
        else:
            out.append(StrategyDecision(action="HOLD", strength=0.0))
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    args = ap.parse_args()
    rows = await _klines("1d", N_BARS, symbol=args.symbol)
    closes = [float(r[4]) for r in rows]
    print(f"fetched {len(rows)} {args.symbol} 1d klines")
    if len(rows) < N_BARS:
        print("NOT EVALUABLE")
        return 1
    X, y, R = build_real_rows(closes)
    N = len(y)
    all_ok = True
    for length in LENGTHS:
        iva_end = N - length
        itr_end = iva_end - VAL
        s = _series(rows[itr_end - TRAIN + 7 : N + 7])
        wcloses = s["close"]
        entry_acts = {k: _actions(REPS[k], s) for k in REPS}
        results = []
        for pi, (a, b) in enumerate(PAIRS):
            base, _ = _net_and_p(_composite(entry_acts[a], entry_acts[b]), wcloses, SEED + pi)
            stop, p = _net_and_p(
                _with_stop(entry_acts[a], entry_acts[b], wcloses), wcloses, SEED + pi
            )
            results.append((f"{REPS[a]}->{REPS[b]}", base, stop, p))
        adjusted = adjust_pvalues([r[3] for r in results], "holm")
        print(f"\nholdout {length}:")
        for (name, base, stop, _p), q in zip(results, adjusted):
            ok = stop > 0 and stop > base and q < 0.05
            print(
                f"  {name:34} base {base:>+9.2f} stop {stop:>+9.2f} "
                f"holm {q:.4f} {'HOLDS' if ok else '.'}"
            )
        winners = [r[0] for r, q in zip(results, adjusted) if r[2] > 0 and r[2] > r[1] and q < 0.05]
        all_ok = all_ok and bool(winners)
        print(f"  winners: {winners or 'NONE'}")
    print(f"\nR-50 {'HOLDS' if all_ok else 'FAILS'} ({args.symbol})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
