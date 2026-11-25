"""Atomic, versioned checkpoints for the online learner (weights + counters).

Pure I/O module: JSON only, atomic replace, explicit version and config hash.
A corrupt or missing checkpoint is never silently treated as a clean state.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class Checkpoint:
    version: int
    ts: float
    observations: int
    bars_seen: int
    last_decision: dict[str, Any] | None
    weights: dict[str, list[list[list[float]]]]
    config_hash: str


def config_hash(config: Mapping[str, Any]) -> str:
    payload = json.dumps(config, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def weights_from_models(
    models: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[list[list[float]]]]:
    """Flatten numpy trainer params to plain JSON lists."""
    out: dict[str, list[list[float]]] = {}
    for key, model in models.items():
        params = model.get("params") or []
        layers = []
        for array in params:
            layers.append([[float(v) for v in row] for row in array])
        out[str(key)] = layers
    return out


def save(path: Path, ckpt: Checkpoint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            {
                "version": ckpt.version,
                "ts": ckpt.ts,
                "observations": ckpt.observations,
                "bars_seen": ckpt.bars_seen,
                "last_decision": ckpt.last_decision,
                "weights": ckpt.weights,
                "config_hash": ckpt.config_hash,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def load(path: Path) -> Checkpoint | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if int(raw.get("version") or 0) != 1:
            return None
        weights = raw.get("weights")
        if not isinstance(weights, dict):
            return None
        return Checkpoint(
            version=1,
            ts=float(raw.get("ts") or 0.0),
            observations=int(raw.get("observations") or 0),
            bars_seen=int(raw.get("bars_seen") or 0),
            last_decision=raw.get("last_decision")
            if isinstance(raw.get("last_decision"), dict)
            else None,
            weights={
                str(k): [[[float(v) for v in row] for row in layer] for layer in layers]
                for k, layers in weights.items()
            },
            config_hash=str(raw.get("config_hash") or ""),
        )
    except (ValueError, TypeError, KeyError, OSError):
        return None
