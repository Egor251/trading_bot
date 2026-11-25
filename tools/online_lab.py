"""Online-learning driver: replay or live polling, always observe-only.

R-61's deterministic core lives in engine/online_learning.py. This driver feeds
it either a recorded kline stream (`--replay`) or live polling (`--live`) and
writes decisions to research/online_decisions.jsonl. It never calls execution.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

import numpy as np  # noqa: E402

from engine.online_learning import LiveBar, OnlineConfig, OnlineLearner  # noqa: E402
from engine.online_checkpoint import weights_from_models  # noqa: E402
from engine.paths import data_path  # noqa: E402
from tools.book_collector import collect_once  # noqa: E402
from tools.neuroevolution_count import (  # noqa: E402
    enumerate_architectures,
    net_long_flat,
    predict_proba,
    run_evolution,
)
from tools.timeframe_verdict import _klines  # noqa: E402

CFG = OnlineConfig()
ARCHS = enumerate_architectures()


class GAOptimizer:
    """Frozen GA over the registered 50-architecture space."""

    def __init__(self) -> None:
        self.last_models: dict[str, Any] = {}

    def weights(self) -> dict[str, Any]:
        return weights_from_models(self.last_models)

    def run(self, X, y, R, tr, va, te, seed) -> dict[str, Any]:
        Xa, ya = np.asarray(X, dtype=float), np.asarray(y, dtype=int)
        evo = run_evolution(Xa, ya, np.asarray(tr), np.asarray(va), ARCHS, seed=seed)
        self.last_models = {key: item["model"] for key, item in evo["distinct"].items()}
        probs = sum(
            predict_proba(evo["distinct"][k]["model"], Xa[np.asarray(te)]) for k in evo["distinct"]
        ) / len(evo["distinct"])
        acc = float(((probs > 0.5).astype(int) == ya[np.asarray(te)]).mean())
        net = net_long_flat(
            probs, np.asarray(R, dtype=float)[np.asarray(te)], cost=CFG.fee_bps / 1e4
        )
        return {"acc": acc, "net": net, "prob": float(probs[-1]) if len(probs) else 0.5}


def _write_decision(path: Path, decision: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(decision, sort_keys=True, default=str) + "\n")


def _book_map() -> dict[int, dict]:
    path = data_path("research", "order_book.jsonl")
    if not path.exists():
        return {}
    out: dict[int, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            if r.get("symbol") == "BTC/USDT":
                out[int(float(r["ts"]))] = r
        except (ValueError, TypeError, KeyError):
            continue
    return out


async def replay(
    n_bars: int = 600, out: str = "online_decisions.jsonl", optimizer: GAOptimizer | None = None
) -> tuple[OnlineLearner, GAOptimizer]:
    opt = optimizer or GAOptimizer()
    streams = {s: (await _klines("1h", n_bars, symbol=s)) for s in CFG.symbols}
    n = min(len(v) for v in streams.values())
    learner = OnlineLearner(CFG)
    path = data_path("research", out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    books = _book_map()
    for i in range(n):
        ts = int(streams[CFG.symbols[0]][i][0]) / 1000.0
        book = books.get(int(ts))
        for s in CFG.symbols:
            row = streams[s][i]
            learner.observe(
                LiveBar(
                    ts,
                    s,
                    float(row[4]),
                    float(book["spread_bps"]) if book and s == CFG.symbols[0] else None,
                    float(book["bid_size"])
                    / (float(book["bid_size"]) + float(book["ask_size"]))
                    * 2
                    - 1
                    if book and s == CFG.symbols[0]
                    else None,
                )
            )
        d = learner.step(opt)
        if d is not None:
            _write_decision(path, d)
    print(
        f"replay: observations={len(learner.features)} "
        f"decisions={len(learner.decisions)} observe_only=True path={path}"
    )
    print(json.dumps(learner.snapshot(), indent=2))
    return learner, opt


async def live(
    out: str = "online_decisions.jsonl",
    optimizer: GAOptimizer | None = None,
    learner: OnlineLearner | None = None,
) -> tuple[OnlineLearner, GAOptimizer]:
    """One polling pass; safe to schedule. No execution, observe-only."""
    opt = optimizer or GAOptimizer()
    state = learner or OnlineLearner(CFG)
    books = await collect_once()
    btc = next((r for r in books if r.get("symbol") == "BTC/USDT"), None)
    rows = {s: (await _klines("1h", CFG.window_bars, symbol=s)) for s in CFG.symbols}
    for s in CFG.symbols:
        row = rows[s][-1]
        ts = float(row[0]) / 1000.0
        if ts <= state.last_ts(s):  # no new bar yet; never replay the same close
            continue
        state.observe(
            LiveBar(
                ts,
                s,
                float(row[4]),
                float(btc["spread_bps"]) if btc and s == CFG.symbols[0] else None,
                float(btc["bid_size"]) / (float(btc["bid_size"]) + float(btc["ask_size"])) * 2 - 1
                if btc and s == CFG.symbols[0]
                else None,
            )
        )
    d = state.step(opt)
    path = data_path("research", out)
    if d is not None:
        _write_decision(path, d)
    print(f"live pass: {state.snapshot()} decision={d is not None} observe_only=True")
    return state, opt


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("replay", "live"), default="replay")
    ap.add_argument("--bars", type=int, default=600)
    ap.add_argument("--out", default="online_decisions.jsonl")
    args = ap.parse_args()
    if args.mode == "live":
        await live(args.out)
    else:
        await replay(args.bars, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
