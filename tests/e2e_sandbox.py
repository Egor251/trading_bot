"""Per-test sandbox for end-to-end tests that boot a real core.

Running the suite used to execute against the *production* `config/trading_bot.yaml`
from the repository root, so every relative data path (`data/storage/records.db`,
`data/ops/trades.jsonl`, `data/phantom/timeline.jsonl`, `data/execution/orders.json`)
resolved to the live operational directory. Evidence that this had already been
happening for weeks: events with `reason: "crypto_rule_e2e"` — a string that
exists only in `tests/test_crypto_path_e2e.py` — were sitting in the production
event journal, alongside test fills at the test-only price 159.0.

`sandbox_core` gives a test its own config copy, its own data directory and its
own CWD, so it exercises the real code paths without touching live state.
"""

from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import yaml

ROOT = Path(__file__).resolve().parent.parent
SHIPPED_CONFIG = ROOT / "config" / "trading_bot.yaml"

#: Services that must not run in a sandbox: they either talk to the network or
#: write long-lived state. Market connectors are deliberately *not* listed — the
#: E2E tests inject a fake exchange into them, and disabling `auto_start` left
#: them un-initialised so the test asserted on `None`.
_SANDBOX_DISABLED_SERVICES = (
    "storage_service",
    "phantom_service",
    "ops_monitor_service",
    "historical_data_service",
    "ui_bridge_service",
    "execution_service",
    "alpaca_market_service",
)


def _force_off(node: Any) -> None:
    if isinstance(node, dict):
        for value in node.values():
            _force_off(value)
    elif isinstance(node, list):
        for value in node:
            _force_off(value)


#: Every relative data path in the shipped config, as dotted config keys. The
#: engine resolves these against the *process CWD*, so rewriting them to
#: absolute sandbox paths is what actually isolates a test run. Keeping the CWD
#: at the repository root is deliberate: plugin discovery looks for
#: `strategies/` and `plugins/` relative to it, so a sandboxed CWD would find
#: zero plugins and silently test nothing.
_SANDBOX_PATHS: tuple[tuple[str, str], ...] = (
    ("storage.backends.sqlite.path", "storage/records.db"),
    ("storage.backends.chdb_local.path", "storage/analytics.chdb"),
    ("storage.backends.duckdb_local.path", "storage/events.duckdb"),
    ("storage.backends.jsonl_local.path", "storage/events.jsonl"),
    ("storage.backends.parquet_local.path", "storage/cold"),
    ("services.execution_service.store_path", "execution/orders.json"),
    ("services.ops_monitor_service.journal_path", "ops/trades.jsonl"),
    ("services.ops_monitor_service.alerts_path", "ops/alerts.jsonl"),
    ("services.phantom_service.state_path", "phantom/state.json"),
    ("services.phantom_service.timeline_path", "phantom/timeline.jsonl"),
    ("services.phantom_service.walk_forward.report_dir", "phantom/walk_forward"),
    ("services.ccxt_market_service.disk_cache_dir", "ohlc_cache"),
    ("services.historical_data_service.cache_dir", "history"),
    ("rules.path", "rules"),
    ("trading.cost_store_path", "cost_schedules"),
    ("trading.trade_journal.path", "ops/trades.jsonl"),
)


def _set_dotted(raw: dict[str, Any], dotted: str, value: Any) -> None:
    node: Any = raw
    parts = dotted.split(".")
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


#: RuleEngine rule files are *shipped configuration*, not runtime state, but they
#: live under `data/`. `build_sandbox_config` redirects every `data/`-relative
#: path into the sandbox, so without seeding the rules the engine booted with an
#: empty ruleset: no `rule.triggered`, no `place_order`, no fill — and
#: `test_crypto_rule_engine_path_e2e` failed for that reason alone rather than
#: for the Consilium defect it was written to catch.
SANDBOX_RULES_SOURCE = ROOT / "data" / "rules"

#: Plugins that are execution *infrastructure*, not strategies. A sandbox whose
#: job is to prove the strategy → order path works must leave these alone.
#: `build_sandbox_config` disables everything outside ``enable_plugins``, which
#: switched off `order_execution` and made every execution assertion in
#: `test_crypto_rule_engine_path_e2e` unreachable — the rule fired, the action
#: named a disabled plugin, and no `execution.result` was ever published.
_SANDBOX_ALWAYS_ENABLED_PLUGINS = frozenset({"order_execution"})


def _seed_sandbox_rules(data_root: Path) -> int:
    """Copy the shipped rule files into *data_root*/rules."""
    if not SANDBOX_RULES_SOURCE.is_dir():
        return 0
    dest = data_root / "rules"
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src in sorted(SANDBOX_RULES_SOURCE.glob("*.xml")):
        shutil.copy2(src, dest / src.name)
        copied += 1
    return copied


def build_sandbox_config(
    dest: Path,
    *,
    data_root: Path,
    enable_plugins: tuple[str, ...] = ("test_strategy",),
    extra_overrides: dict[str, Any] | None = None,
) -> Path:
    """Write a test config into *dest* and return its path.

    The shipped file is used as the base so a test still covers the real
    configuration surface, but every plugin is disabled except *enable_plugins*,
    the noisy/long-lived services are switched off, and every relative data path
    is redirected under *data_root*.
    """
    raw = yaml.safe_load(SHIPPED_CONFIG.read_text(encoding="utf-8")) or {}

    plugins = dict(raw.get("plugins") or {})
    for pid, block in list(plugins.items()):
        if not isinstance(block, dict):
            continue
        block = dict(block)
        if pid in enable_plugins or pid in _SANDBOX_ALWAYS_ENABLED_PLUGINS:
            block["enabled"] = True
        else:
            block["enabled"] = False
            block["observe_only"] = True
            block["tickers"] = []
        plugins[pid] = block
    raw["plugins"] = plugins

    services = dict(raw.get("services") or {})
    for sid in _SANDBOX_DISABLED_SERVICES:
        block = dict(services.get(sid) or {})
        block["enabled"] = False
        block["auto_start"] = False
        services[sid] = block
    raw["services"] = services

    storage = dict(raw.get("storage") or {})
    storage["enabled"] = False
    raw["storage"] = storage

    for dotted, relative in _SANDBOX_PATHS:
        _set_dotted(raw, dotted, str(data_root / relative))
    # catalog_path points at a *read-only* repo file; keep it pointed there.
    _set_dotted(raw, "services.universe_service.catalog_path", str(ROOT / "config" / "universe_catalog.yaml"))

    if extra_overrides:
        for dotted, value in (extra_overrides or {}).items():
            _set_dotted(raw, dotted, value)

    # The research gate demotes any live Consilium member that has not cleared
    # holdout validation. A sandbox runs no research at all, so every member
    # would be demoted and the end-to-end tests could no longer exercise the
    # execution path they exist to cover. The gate itself is covered directly by
    # tests/test_research_gate_enforced.py.
    _set_dotted(raw, "trading.live_readiness.enforce", False)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(yaml.safe_dump(raw, sort_keys=True, allow_unicode=True), encoding="utf-8")
    return dest


@contextmanager
def sandbox_core(
    tmp_path: Path,
    *,
    enable_plugins: tuple[str, ...] = ("test_strategy",),
    extra_overrides: dict[str, Any] | None = None,
) -> Iterator[Path]:
    """Yield a config path whose data paths all live under *tmp_path*.

    The CWD stays at the repository root on purpose: plugin discovery resolves
    ``strategies/`` and ``plugins/`` relative to it, so moving the CWD would make
    the test discover zero plugins and pass vacuously.
    """
    workdir = Path(tmp_path)
    data_root = workdir / "data"
    (workdir / "config").mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    _seed_sandbox_rules(data_root)
    config_path = build_sandbox_config(
        workdir / "config" / "trading_bot.yaml",
        data_root=data_root,
        enable_plugins=enable_plugins,
        extra_overrides=extra_overrides,
    )
    root_text = str(ROOT)
    if root_text not in os.sys.path:
        os.sys.path.insert(0, root_text)

    previous_cwd = Path.cwd()
    os.chdir(ROOT)
    try:
        yield config_path
    finally:
        os.chdir(previous_cwd)
        shutil.rmtree(data_root, ignore_errors=True)


def sandbox_rules_are_present(tmp_path: Path) -> bool:
    """Whether the seeded sandbox actually carries the shipped ruleset."""
    return (Path(tmp_path) / "data" / "rules").is_dir() and any(
        (Path(tmp_path) / "data" / "rules").glob("*.xml")
    )


def assert_no_production_writes(before: dict[str, int], root: Path = ROOT) -> None:
    """Guard for tests that must not touch the live ``data/`` tree."""
    data = root / "data"
    if not data.is_dir():
        return
    after: dict[str, int] = {}
    for path in data.rglob("*"):
        if path.is_file():
            try:
                after[str(path.relative_to(data))] = path.stat().st_mtime_ns
            except OSError:
                continue
    changed = {
        name
        for name, mtime in after.items()
        if before.get(name) != mtime
    }
    assert not changed, f"test wrote into the production data directory: {sorted(changed)}"
