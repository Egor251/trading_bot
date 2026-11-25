"""Fast observe-only forecast daemon: polls book mids, trains on distance loss."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

from engine.forecast_learning import ForecastConfig, ForecastLearner
from engine.online_checkpoint import Checkpoint, config_hash, load, save, weights_from_models
from tools.book_collector import collect_once
from tools.forecast_trainer import loss, predict, run_forecast_evolution
from tools.neuroevolution_count import enumerate_architectures

ONLINE_DIR = Path(
    os.environ.get("TRADING_ONLINE_DIR")
    or (Path.home() / ".trading_bot" / "online")
)
ARCHS = enumerate_architectures()


class ForecastOptimizer:
    def __init__(self) -> None:
        self.out: dict[str, Any] = {}

    def weights(self) -> dict[str, Any]:
        models = {
            k: v["model"] for k, v in self.out.get("distinct", {}).items()
        }
        return weights_from_models(models)

    def run(self, X, y, tr, va, te, seed) -> dict[str, Any]:
        Xa, ya = np.asarray(X, dtype=float), np.asarray(y, dtype=float)
        out = run_forecast_evolution(
            Xa, ya, np.asarray(tr), np.asarray(va), ARCHS, seed=seed
        )
        self.out = out
        test_idx = np.asarray(te)
        pred = predict(out["model"], Xa[test_idx])
        target = ya[test_idx]
        baseline = np.zeros_like(pred)
        return {
            "pred": float(pred[-1]) if len(pred) else 0.0,
            "loss": loss(pred, target),
            "baseline": loss(baseline, target),
        }


async def cycle(learner: ForecastLearner, opt: ForecastOptimizer,
                decisions: Path, checkpoint: Path) -> dict[str, Any] | None:
    books = await collect_once()
    btc = next((r for r in books if r.get("symbol") == "BTC/USDT"), None)
    if btc is None:
        return None
    bid, ask = float(btc["bid"]), float(btc["ask"])
    mid = (bid + ask) / 2.0
    imbalance = (
        float(btc["bid_size"]) / (float(btc["bid_size"]) + float(btc["ask_size"])) * 2 - 1
    )
    learner.observe(float(btc["ts"]), mid, float(btc["spread_bps"]), imbalance)
    d = learner.step(opt)
    if d is None:
        return None
    decisions.parent.mkdir(parents=True, exist_ok=True)
    with decisions.open("a", encoding="utf-8") as f:
        f.write(json.dumps(d, sort_keys=True, default=str) + "\n")
    cp = Checkpoint(
        version=1,
        ts=time.time(),
        observations=len(learner.targets),
        bars_seen=len(learner.features),
        last_decision=d,
        weights=opt.weights(),
        config_hash=config_hash({"horizon_sec": learner.cfg.horizon_sec,
                                 "poll_sec": learner.cfg.poll_sec,
                                 "seed": learner.cfg.seed}),
    )
    save(checkpoint, cp)
    return d


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--iterations", type=int, default=0)
    ap.add_argument("--horizon", type=float, default=300.0)
    ap.add_argument("--decisions", default="forecast_decisions.jsonl")
    ap.add_argument("--checkpoint", default="forecast_weights.json")
    args = ap.parse_args()

    decisions = ONLINE_DIR / args.decisions
    checkpoint = ONLINE_DIR / args.checkpoint
    restored = load(checkpoint)
    learner = ForecastLearner(ForecastConfig(horizon_sec=args.horizon))
    opt = ForecastOptimizer()
    print(f"fast daemon start restored={restored is not None} "
          f"interval={args.interval}s horizon={args.horizon}s observe_only=True")
    i = 0
    while args.iterations == 0 or i < args.iterations:
        i += 1
        try:
            d = await cycle(learner, opt, decisions, checkpoint)
            if d:
                print(f"forecast loss {d['loss']:.6f} baseline {d['baseline']:.6f} "
                      f"observations {d['observations']}")
        except Exception as exc:  # noqa: BLE001 - survive a bad poll
            print(f"cycle failed ({type(exc).__name__}): {exc}")
        if args.iterations == 0 or i < args.iterations:
            await asyncio.sleep(max(0.5, args.interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
