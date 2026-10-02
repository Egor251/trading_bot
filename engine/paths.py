"""Single root for every runtime data path.

Data paths were a mix of two conventions: some services resolved them against
the repo root via ``Path(__file__).parents[N]`` and the rest used bare
``data/...`` strings that resolved against the *process CWD*. The engine is
started with ``os.chdir(ROOT)`` in ``host/main.py`` and ``WORKDIR`` in the
Dockerfile, so it happened to work — until anything else started the process
(a Windows service, a scheduler, ``docker run -w``, a test), at which point the
durable order store silently began empty, idempotency keys were lost, and the
OHLC cache quietly relocated.

``TRADING_DATA_DIR`` overrides the root. That is what lets a test run against a
temporary data directory without moving the CWD (which would break plugin
discovery, since ``strategies/`` is resolved relative to it).
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["DATA_DIR_ENV", "data_dir", "data_path", "repo_root"]

DATA_DIR_ENV = "TRADING_DATA_DIR"


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Root for journals, caches, ledgers and reports."""
    override = str(os.environ.get(DATA_DIR_ENV) or "").strip()
    if override:
        return Path(override)
    return repo_root() / "data"


def data_path(*parts: str) -> Path:
    """Join *parts* under the data root."""
    return data_dir().joinpath(*parts)
