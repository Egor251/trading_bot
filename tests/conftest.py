"""Shared pytest setup.

Two jobs:

1. Put the sibling ``Code_base`` packages on ``sys.path`` so plugins import.
2. Make it impossible for a test to write into the live ``data/`` tree.

(2) is not theoretical. Every relative data path in this codebase resolves
against the process CWD, and the E2E tests used to ``os.chdir(ROOT)`` and boot a
real core against the shipped ``config/trading_bot.yaml`` — so running pytest
appended test halts (``reason: "crypto_rule_e2e"``), test fills at the test-only
price 159.0 and mock SBER candles to the production event journal. The journal is
what ``docs/runbooks/reconciliation-break.md`` tells an operator to trust, so
contaminating it is a correctness problem, not a tidiness one.

The guard snapshots ``data/`` mtimes per test and fails with the exact list of
files that moved. Tests that legitimately need those paths should use
``tests.e2e_sandbox.sandbox_core``, which rewrites every data path in a config
copy to a temporary root.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

#: Every relative data path a service falls back to when its config omits one.
#: Redirecting these under a temp root is what isolates a test, because the
#: engine resolves relative paths against the process CWD and the CWD must stay
#: at the repo root for plugin discovery to find ``strategies/``.
_DEFAULT_DATA_PATHS: tuple[tuple[str, str], ...] = (
    ("services.execution_service.store_path", "execution/orders.json"),
    ("services.ops_monitor_service.alerts_path", "ops/alerts.jsonl"),
    ("services.ops_monitor_service.journal_path", "ops/trades.jsonl"),
    ("services.phantom_service.state_path", "phantom/state.json"),
    ("services.phantom_service.timeline_path", "phantom/timeline.jsonl"),
    ("services.phantom_service.walk_forward.report_dir", "phantom/walk_forward"),
    ("services.ccxt_market_service.disk_cache_dir", "ohlc_cache"),
    ("storage.backends.sqlite.path", "storage/records.db"),
    ("trading.cost_store_path", "cost_schedules"),
    ("trading.trade_journal.path", "ops/trades.jsonl"),
    ("rules.path", "rules"),
)


def _snapshot() -> dict[str, int]:
    if not DATA_DIR.is_dir():
        return {}
    out: dict[str, int] = {}
    for path in DATA_DIR.rglob("*"):
        if path.is_file():
            try:
                out[str(path)] = path.stat().st_mtime_ns
            except OSError:
                continue
    return out


def _set_dotted(target: dict, dotted: str, value: object) -> None:
    node: object = target
    parts = dotted.split(".")
    for part in parts[:-1]:
        if not isinstance(node, dict):
            return
        node = node.setdefault(part, {})
    if isinstance(node, dict):
        node[parts[-1]] = value


@pytest.fixture(autouse=True)
def _no_production_data_writes(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Isolate every test from the live ``data/`` tree, and prove it.

    ``TRADING_DATA_DIR`` is the single lever: :mod:`engine.paths` resolves every
    runtime data path through it, so pointing it at a temp root moves the
    journals, caches, ledgers, reports and the secret database at once — while
    the CWD stays at the repo root, which plugin discovery needs in order to find
    ``strategies/``.

    The assertion is a hard failure on purpose. The contamination it prevents is
    not hypothetical: the production event journal already contains test halts
    (``reason: "crypto_rule_e2e"``) and test-only fills, and
    ``docs/runbooks/reconciliation-break.md`` tells an operator to treat that
    journal as the source of truth.
    """
    sandbox = tmp_path_factory.mktemp("tb-data")
    for relative in ("storage", "ops", "phantom", "execution", "ohlc_cache", "rules", "ui"):
        (sandbox / relative).mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("TRADING_DATA_DIR", str(sandbox))
    # Caches keyed on first call must not survive into another test.
    import host.secrets_env as secrets_env

    monkeypatch.setattr(secrets_env, "_sqlite_path_cache", None, raising=False)

    before = _snapshot()
    yield sandbox
    after = _snapshot()
    touched = sorted(
        name.replace(str(ROOT) + "\\", "").replace(str(ROOT) + "/", "")
        for name, mtime in after.items()
        if before.get(name) != mtime
    )
    if not touched:
        return
    pytest.fail(
        "test wrote into the production data directory: "
        + ", ".join(touched)
        + ". Resolve the path through engine.paths.data_path(...) (honouring "
        "TRADING_DATA_DIR) instead of a bare 'data/...' string."
    )


@pytest.fixture
def repo_root() -> Path:
    return ROOT
