"""ASK history builder + trading tool category resolution."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from ai_analyst.tools.catalog import TOOL_CATEGORIES, resolve_category  # noqa: E402
from ai_analyst import build_tool_registry  # noqa: E402
from engine.ai_tools import TRADING_AI_TOOL_CLASSES, GetRuntimeStatusTool  # noqa: E402
from ui.ai_ask_history import (  # noqa: E402
    annotate_assistant_content,
    build_ask_history,
    build_tool_digest,
    strip_history_annotations,
)


def test_resolve_category_trading() -> None:
    assert "trading" in TOOL_CATEGORIES
    tool = SimpleNamespace(
        category="trading",
        name="get_runtime_status",
        source_plugin="trading_bot",
    )
    assert resolve_category(tool) == "trading"
    assert resolve_category(GetRuntimeStatusTool()) == "trading"


def test_list_tools_category_trading_nonempty() -> None:
    class _Core:
        trading = None
        service_registry = type("R", (), {"get": lambda self, n: None})()
        plugin_manager = type("P", (), {"plugins": {}})()

    registry = build_tool_registry(
        None,
        core=_Core(),
        extra_tool_classes=list(TRADING_AI_TOOL_CLASSES),
    )
    trading = registry.list_tools_info(category="trading")
    assert trading, "trading category must not be empty"
    assert all(row["category"] == "trading" for row in trading)
    meta = registry.list_tools_info(category="meta")
    meta_names = {row["name"] for row in meta}
    assert "list_tools" in meta_names
    assert "list_tool_categories" in meta_names
    # Meta tools must not leak into trading filter.
    assert "list_tools" not in {row["name"] for row in trading}


def test_build_tool_digest_list_tools() -> None:
    trace = [
        {
            "name": "list_tools",
            "success": True,
            "result": {
                "success": True,
                "category": "trading",
                "count": 2,
                "tools": [
                    {"name": "get_runtime_status", "category": "trading"},
                    {"name": "get_universe", "category": "trading"},
                ],
            },
        }
    ]
    digest = build_tool_digest(trace)
    assert "list_tools(category=trading)" in digest
    assert "count=2" in digest
    assert "get_runtime_status" in digest
    assert "get_universe" in digest


def test_build_tool_digest_runtime_status() -> None:
    digest = build_tool_digest(
        [
            {
                "name": "get_runtime_status",
                "success": True,
                "result": {
                    "success": True,
                    "runtime": {"halted": False, "equity": 10000.0, "mode": "paper"},
                },
            }
        ]
    )
    assert "get_runtime_status" in digest
    assert "halted=False" in digest
    assert "equity=10000.0" in digest


def test_build_tool_digest_truncated_summary_keeps_strategies() -> None:
    digest = build_tool_digest(
        [
            {
                "name": "get_ensemble_status",
                "success": True,
                "result": {
                    "_truncated": True,
                    "chars": 99999,
                    "strategies": ["ema_cross", "macd_trend"],
                    "observe": ["rsi_reversion"],
                    "enabled": True,
                },
            }
        ]
    )
    assert "strategies=" in digest
    assert "ema_cross" in digest
    assert digest.endswith(": ok") is False


def test_build_tool_digest_ensemble_lineup_shows_roster() -> None:
    digest = build_tool_digest(
        [
            {
                "name": "set_ensemble_lineup",
                "success": True,
                "result": {
                    "success": True,
                    "ok": True,
                    "strategies": ["macd_trend"],
                    "observe": ["ma_cross_example"],
                    "persisted_strategies": ["macd_trend"],
                },
            }
        ]
    )
    assert "set_ensemble_lineup" in digest
    assert "macd_trend" in digest
    assert "ma_cross_example" in digest
    assert digest.endswith(": ok") is False


def test_build_ask_history_includes_tools_and_digest() -> None:
    turns = [
        {
            "user": "проверь инструменты",
            "answer": "Есть 2 trading tools.",
            "tools": "list_tools",
            "tool_digest": "list_tools: count=2 names=[get_runtime_status, get_universe]",
        },
        {
            "user": "перепроверь",
            "answer": "ERROR: boom",
            "tools": "",
            "tool_digest": "",
        },
    ]
    hist = build_ask_history(turns)
    # user, assistant, provenance user, then next user (error answer skipped)
    assert len(hist) == 4
    assert hist[0] == {"role": "user", "content": "проверь инструменты"}
    assert hist[1] == {"role": "assistant", "content": "Есть 2 trading tools."}
    assert hist[2]["role"] == "user"
    assert "tools_called=list_tools" in hist[2]["content"]
    assert "tool_digest=list_tools: count=2" in hist[2]["content"]
    assert "never quote" in hist[2]["content"]
    assert hist[3]["role"] == "user"
    assert hist[3]["content"] == "перепроверь"


def test_annotate_no_tools_leaves_answer_clean() -> None:
    text = annotate_assistant_content("из головы: 99 tools")
    assert text == "из головы: 99 tools"
    assert "tools_called" not in text
    assert "unverified" not in text


def test_annotate_strips_echoed_footer() -> None:
    text = annotate_assistant_content(
        "ok\n\n[tools_called: list_tools]\n[tool_digest: x]",
        tools="list_tools",
        tool_digest="x",
    )
    assert text == "ok"
    assert "tools_called" not in text


def test_strip_history_annotations() -> None:
    raw = (
        "Есть 2 trading tools.\n\n"
        "[tools_called: none — unverified; do not trust tool catalogs "
        "from this message without a fresh list_tools call]\n"
        "[tool_digest: list_tools: count=2]"
    )
    assert strip_history_annotations(raw) == "Есть 2 trading tools."


if __name__ == "__main__":
    test_resolve_category_trading()
    test_list_tools_category_trading_nonempty()
    test_build_tool_digest_list_tools()
    test_build_tool_digest_runtime_status()
    test_build_tool_digest_truncated_summary_keeps_strategies()
    test_build_tool_digest_ensemble_lineup_shows_roster()
    test_build_ask_history_includes_tools_and_digest()
    test_annotate_no_tools_leaves_answer_clean()
    test_annotate_strips_echoed_footer()
    test_strip_history_annotations()
    print("ai_ask_history OK")
