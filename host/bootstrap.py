"""Ensure Code_base packages are on sys.path (plugin_manager, microkernel, storage, rules, AI)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODE_BASE = ROOT.parent / "Code_base"

_PACKAGES = (
    CODE_BASE,  # ``from utils.prod_logging import …``
    CODE_BASE / "utils",  # ``from prod_logging import …`` (Aphina-style)
    CODE_BASE / "microkernel_core",
    CODE_BASE / "plugin_manager",
    CODE_BASE / "cb_storage",
    CODE_BASE / "cb_rule_engine",
    CODE_BASE / "cb_math",
    CODE_BASE / "ai_analyst",
)


def ensure_code_base_path() -> list[Path]:
    """Insert sibling Code_base packages; return those that exist."""
    found: list[Path] = []
    # repo root first so ``host`` / ``engine`` / ``plugins`` resolve
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    for path in _PACKAGES:
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))
            found.append(path)
        elif path.is_dir():
            found.append(path)
    return found


def plugin_manager_info() -> dict[str, str]:
    """Report which plugin_manager module is loaded (for smoke / status)."""
    ensure_code_base_path()
    import plugin_manager

    return {
        "package": "plugin_manager",
        "file": str(getattr(plugin_manager, "__file__", "")),
        "version": str(getattr(plugin_manager, "__version__", "local")),
    }
