from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from engine.ai_tools import TRADING_AI_TOOL_CLASSES
from engine.cost_workflow import (
    CostScheduleDraft,
    CostScheduleStore,
    SourceDocument,
    validate_cost_draft,
)
from services.ui_bridge_service import UiBridgeService


TEXT = "Maker fee is 2 bps. Taker fee is 4 bps."


def source() -> SourceDocument:
    return SourceDocument(document_id="fees-1", title="Broker fees", text=TEXT)


def draft(src: SourceDocument, *, quote: str = "Maker fee is 2 bps.") -> CostScheduleDraft:
    start = TEXT.index("Maker")
    return CostScheduleDraft.model_validate(
        {
            "version": "2026-07-draft",
            "rules": [
                {
                    "rule_id": "maker",
                    "match": {"symbol": "*", "liquidity": "maker"},
                    "components": [{"kind": "bps", "rate": "2", "name": "broker"}],
                    "source_spans": [
                        {
                            "source_hash": src.sha256,
                            "start": start,
                            "end": start + len(quote),
                            "quote": quote,
                            "confidence": "0.99",
                        }
                    ],
                }
            ],
        }
    )


def test_schemas_forbid_unknown_fields_and_verify_source_hash() -> None:
    with pytest.raises(ValidationError):
        SourceDocument(document_id="x", title="x", text="x", surprise=True)
    with pytest.raises(ValidationError):
        SourceDocument(document_id="x", title="x", text="x", sha256="0" * 64)


def test_grounding_rate_coverage_and_tier_examples() -> None:
    src = source()
    value = draft(src).model_dump(mode="python")
    value["rules"][0]["match"]["tier_min"] = "100"
    value["rules"][0]["components"][0]["rate"] = "10001"
    report = validate_cost_draft(src, value)
    codes = {issue.code for issue in report.issues}
    assert not report.valid
    assert "rate_out_of_range" in codes
    assert "liquidity_coverage" in codes
    assert any(row.label == "at 100" for row in report.examples)


def test_quote_must_match_exact_source_bytes() -> None:
    src = source()
    value = draft(src).model_dump(mode="python")
    value["rules"][0]["source_spans"][0]["quote"] = "Maker fee is 3 bps."
    report = validate_cost_draft(src, value)
    assert {issue.code for issue in report.issues} >= {"source_quote_mismatch"}


def test_duplicate_and_overlap_detection() -> None:
    src = source()
    value = draft(src).model_dump(mode="python")
    duplicate = dict(value["rules"][0])
    duplicate["rule_id"] = "maker-copy"
    value["rules"] = [*value["rules"], duplicate]
    report = validate_cost_draft(src, value)
    assert "duplicate_rule" in {issue.code for issue in report.issues}


def test_immutable_store_activation_and_stale_hash(tmp_path: Path) -> None:
    src = source()
    store = CostScheduleStore(tmp_path)
    saved = store.save_draft(src, draft(src), created_by="alice")
    original = (tmp_path / "drafts" / f"{saved.draft_hash}.json").read_bytes()
    record = store.activate(saved.draft_hash, activated_by="alice")
    assert record.draft_hash == saved.draft_hash
    assert store.load_active_schedule().schedule_hash == saved.schedule_hash
    assert (tmp_path / "drafts" / f"{saved.draft_hash}.json").read_bytes() == original
    with pytest.raises(FileNotFoundError):
        store.activate("f" * 64, activated_by="alice")


def test_tampered_immutable_draft_fails_hash_revalidation(tmp_path: Path) -> None:
    src = source()
    store = CostScheduleStore(tmp_path)
    saved = store.save_draft(src, draft(src), created_by="alice")
    path = tmp_path / "drafts" / f"{saved.draft_hash}.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"created_by":"alice"', '"created_by":"mallory"'))
    with pytest.raises(ValueError, match="hash mismatch"):
        store.activate(saved.draft_hash, activated_by="alice")


def test_ai_tools_expose_gated_cost_activate() -> None:
    names = {tool.name for tool in TRADING_AI_TOOL_CLASSES}
    assert "read_cost_schedule" in names
    assert "propose_cost_schedule" in names
    assert "activate_cost_schedule" in names
    by_name = {tool.name: tool for tool in TRADING_AI_TOOL_CLASSES}
    assert by_name["activate_cost_schedule"].is_modifying is True


def test_bridge_requires_confirmation_and_exact_hash(tmp_path: Path) -> None:
    async def run() -> None:
        core = MagicMock()
        core.config.raw = {"costs": {"store_path": str(tmp_path)}}
        core.event_bus.publish = AsyncMock()
        svc = UiBridgeService(core, "ui_bridge_service")
        src = source()
        saved = svc._cost_store().save_draft(src, draft(src), created_by="alice")

        denied = await svc._cmd_cost_activate(
            {"expected_hash": saved.draft_hash}, "local"
        )
        assert denied["error"] == "confirm_cost_schedule_required"
        stale = await svc._cmd_cost_activate(
            {
                "draft_hash": saved.draft_hash,
                "expected_hash": "f" * 64,
                "confirm_cost_schedule": True,
            },
            "local",
        )
        assert stale["ok"] is False
        assert stale["error"] == "stale_expected_hash"
        ok = await svc._cmd_cost_activate(
            {
                "expected_hash": saved.draft_hash,
                "confirm_cost_schedule": True,
                "operator_id": "alice",
            },
            "local",
        )
        assert ok["ok"] is True
        core.event_bus.publish.assert_awaited_once()

    asyncio.run(run())
