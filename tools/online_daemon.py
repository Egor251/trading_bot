"""Background observe-only online-learning daemon with periodic weight saves.

Never touches execution or the live lineup. Restarts from a checkpoint after a
reboot; system-level autostart units are in ops/systemd and ops/windows.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.online_checkpoint import Checkpoint, config_hash, load, save
from engine.online_learning import OnlineLearner

# Checkpoints and decisions MUST live outside data/: the test suite snapshots
# data/ mtimes and fails on any write there, and a background writer would make
# every suite run fail for reasons unrelated to the code under test.
ONLINE_DIR = Path(
    os.environ.get("TRADING_ONLINE_DIR")
    or (Path.home() / ".trading_bot" / "online")
)
from tools.online_lab import CFG, GAOptimizer, live, replay


def _online_path(name: str) -> Path:
    return ONLINE_DIR / name


async def run_cycle(
    mode: str,
    bars: int,
    decisions: str,
    opt: GAOptimizer,
    checkpoint_path: Path,
    learner: OnlineLearner | None = None,
) -> OnlineLearner:
    if mode == "live":
        state, _ = await live(decisions, opt, learner)
    else:
        state, _ = await replay(bars, decisions, opt)
    ckpt = Checkpoint(
        version=1,
        ts=time.time(),
        observations=len(state.features),
        bars_seen=state.bars_seen,
        last_decision=state.decisions[-1] if state.decisions else None,
        weights=opt.weights(),
        config_hash=config_hash(
            {
                "symbols": CFG.symbols,
                "window_bars": CFG.window_bars,
                "update_every": CFG.update_every,
                "fee_bps": CFG.fee_bps,
                "seed": CFG.seed,
            }
        ),
    )
    save(checkpoint_path, ckpt)
    print(
        f"checkpoint saved: observations={ckpt.observations} "
        f"weights_architectures={len(ckpt.weights)} ts={ckpt.ts:.0f}"
    )
    return state


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("replay", "live"), default="replay")
    ap.add_argument("--bars", type=int, default=600)
    ap.add_argument("--interval", type=float, default=300.0)
    ap.add_argument("--iterations", type=int, default=0, help="0 = forever")
    ap.add_argument("--decisions", default="online_decisions.jsonl")
    ap.add_argument("--checkpoint", default="online_weights.json")
    args = ap.parse_args()

    cp = _online_path(args.checkpoint)
    restored = load(cp)
    print(
        f"daemon start mode={args.mode} restored={restored is not None} "
        f"observations={restored.observations if restored else 0}"
    )
    opt = GAOptimizer()
    decisions_path = str(_online_path(args.decisions))
    state = OnlineLearner(CFG)
    iteration = 0
    while args.iterations == 0 or iteration < args.iterations:
        iteration += 1
        try:
            state = await run_cycle(args.mode, args.bars, decisions_path, opt, cp, state)
        except Exception as exc:  # noqa: BLE001 - daemon must survive a bad fetch
            print(f"cycle failed ({type(exc).__name__}): {exc}")
        if args.iterations == 0 or iteration < args.iterations:
            await asyncio.sleep(max(1.0, args.interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
