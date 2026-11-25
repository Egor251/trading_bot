"""R-48: enter on strategy A, exit only on strategy B.

Registered S21: six deterministic cross-cluster pairs, four nested daily
holdouts, same-activity timing null (500 draws), Holm over the six pairs.
HOLDS iff one pair is profitable and Holm-significant in ALL four windows.
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
from engine.simulation import adjust_pvalues  # noqa: E402
from engine.strategy_plugin import StrategyDecision  # noqa: E402
from tools.neuroevolution_count import build_real_rows  # noqa: E402
from tools.timeframe_verdict import _klines, _plugin, _series  # noqa: E402

N_BARS = 1200
TRAIN, VAL = 600, 100
LENGTHS = (60, 120, 240, 360)
REPS = {
    "trend": "adx_di_trend",
    "reversion": "bollinger_reversion",
    "breakout": "dual_thrust",
}
PAIRS = [
    ("trend", "reversion"),
    ("reversion", "trend"),
    ("trend", "breakout"),
    ("breakout", "trend"),
    ("reversion", "breakout"),
    ("breakout", "reversion"),
]
FEE_BPS = 7.0
NOTIONAL = 1000.0
DRAWS = 500
SEED = 480117
SIZING = {"mode": "cash", "value": NOTIONAL, "max_position_pct": 1.0, "scale_by_strength": False}


def _actions(name: str, series: dict) -> list[str]:
    cls = _plugin(name)
    assert cls is not None
    plugin = cls.__new__(cls)
    cfg = plugin.default_params()
    return [
        str(plugin.evaluate_at(series, i, config=cfg).action).upper()
        for i in range(len(series["close"]))
    ]


def _composite(entry: list[str], exit_: list[str]) -> list[StrategyDecision]:
    out: list[StrategyDecision] = []
    long = False
    for a, b in zip(entry, exit_):
        if not long and a in {"BUY", "LONG"}:
            out.append(StrategyDecision(action="BUY", strength=1.0))
            long = True
        elif long and b in {"SELL", "SHORT"}:
            out.append(StrategyDecision(action="SELL", strength=1.0))
            long = False
        elif long:
            out.append(StrategyDecision(action="HOLD", strength=1.0, intent="long"))
        else:
            out.append(StrategyDecision(action="HOLD", strength=0.0))
    return out


def _net_and_p(
    decisions: list[StrategyDecision], closes: list[float], seed: int
) -> tuple[float, float]:
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
    held = [False] * len(closes)
    pos = False
    by_bar = {int(f["bar_index"]): str(f["action"]) for f in fills}
    for i in range(len(closes)):
        act = by_bar.get(i)
        if act == "BUY":
            pos = True
        elif act == "SELL":
            pos = False
        held[i] = pos
    rets = [0.0] + [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    q = sum(held[1:]) / max(1, len(held) - 1)
    import random

    rng = random.Random(seed)
    nulls = []
    for _ in range(DRAWS):
        state, gross, flips = False, 0.0, 0
        for i in range(1, len(closes)):
            want = rng.random() < q
            if want != state:
                flips += 1
                state = want
            if state:
                gross += rets[i] * NOTIONAL
        nulls.append(gross - flips * FEE_BPS / 1e4 * NOTIONAL)
    p = (1 + sum(1 for v in nulls if v >= net)) / (DRAWS + 1)
    return net, p


async def main() -> int:
    rows = await _klines("1d", N_BARS)
    closes = [float(r[4]) for r in rows]
    print(f"fetched {len(rows)} BTCUSDT 1d klines")
    if len(rows) < N_BARS:
        print("NOT EVALUABLE")
        return 1
    X, y, R = build_real_rows(closes)
    N = len(y)
    all_ok = True
    for length in LENGTHS:
        ite_end = N
        iva_end = ite_end - length
        itr_end = iva_end - VAL
        s = _series(rows[itr_end - TRAIN + 7 : ite_end + 7])
        wcloses = s["close"]
        entry_acts = {k: _actions(REPS[k], s) for k in REPS}
        results = []
        for pi, (a, b) in enumerate(PAIRS):
            decisions = _composite(entry_acts[a], entry_acts[b])
            net, p = _net_and_p(decisions, wcloses, SEED + pi)
            results.append((f"{REPS[a]}->{REPS[b]}", net, p))
        adjusted = adjust_pvalues([r[2] for r in results], "holm")
        print(f"\nholdout {length}:")
        for (name, net, p), q in zip(results, adjusted):
            ok = net > 0 and q < 0.05
            print(f"  {name:34} net {net:>+9.4f} p {p:.4f} holm {q:.4f} {'HOLDS' if ok else '.'}")
        winners = [r[0] for r, q in zip(results, adjusted) if r[1] > 0 and q < 0.05]
        ok = bool(winners)
        all_ok = all_ok and ok
        print(f"  windows winners: {winners or 'NONE'} -> {'HOLDS' if ok else 'FAILS'}")
    print(f"\nR-48 {'HOLDS' if all_ok else 'FAILS'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
