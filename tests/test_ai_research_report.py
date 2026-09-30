"""AI research report filesystem helpers."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.ai_research_report import (  # noqa: E402
    append_research_report,
    begin_research_report,
    finalize_research_report,
    list_research_reports,
    read_research_report,
    write_research_report,
)


def test_write_list_read_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        meta = write_research_report(
            title="Bollinger holdout BTC",
            body_markdown="Mean OOS positive; holdout sign matches.",
            root=root,
            kind="walk_forward",
            summary="Candidate, not live yet.",
            tags=["holdout", "btc"],
            strategy_ids=["bollinger_reversion"],
            tickers=["BTCUSDT"],
            sections={"Method": "WF optimize + holdout_bars=60"},
            metrics={"mean_oos_pnl": 10.2, "robust_sign_match": True},
            conclusions=["Keep observe-only until more tickers confirm."],
            next_steps=["Batch ETH/SOL with same params."],
            attachments={"digest": {"mean_oos_pnl": 10.2, "ok": True}},
        )
        assert meta["ok"] is True
        folder = Path(meta["folder"])
        assert (folder / "report.md").is_file()
        assert (folder / "meta.json").is_file()
        assert (folder / "attachments" / "digest.json").is_file()
        body = (folder / "report.md").read_text(encoding="utf-8")
        assert "Bollinger holdout BTC" in body
        assert "Mean OOS positive" in body
        assert "## Conclusions" in body

        listed = list_research_reports(root=root, strategy_id="bollinger_reversion")
        assert listed["count"] == 1
        rid = listed["reports"][0]["id"]

        loaded = read_research_report(rid, root=root, include_attachments=True)
        assert loaded["ok"] is True
        assert "Mean OOS positive" in (loaded.get("report_md") or "")
        assert "digest.json" in (loaded.get("attachments") or {})
        assert (loaded["attachments"]["digest.json"] or {}).get("ok") is True


def test_path_escape_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "reports"
        root.mkdir()
        write_research_report(
            title="x",
            body_markdown="y",
            root=root,
        )
        bad = read_research_report(
            str(Path(tmp) / ".." / "secrets"),
            root=root,
            include_body=False,
        )
        assert bad.get("ok") is False


def test_empty_list() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = list_research_reports(root=Path(tmp) / "missing")
        assert out["ok"] is True
        assert out["reports"] == []


def test_begin_append_finalize() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        draft = begin_research_report(
            title="chunked bollinger",
            root=root,
            summary="draft",
            strategy_ids=["bollinger_reversion"],
        )
        rid = draft["id"]
        append_research_report(
            rid, "Grid was weak.", root=root, section="Grid"
        )
        append_research_report(
            rid, "Optimize looked better.", root=root, section="Optimize"
        )
        final = finalize_research_report(
            rid,
            root=root,
            conclusions=["Keep observe-only"],
            next_steps=["Holdout on ETH"],
            attachments={"note": {"ok": True}},
        )
        assert final["status"] == "final"
        body = Path(final["report_md"]).read_text(encoding="utf-8")
        assert "Grid was weak" in body
        assert "Optimize looked better" in body
        assert "Keep observe-only" in body
        assert "_(draft" not in body


if __name__ == "__main__":
    test_write_list_read_roundtrip()
    test_path_escape_rejected()
    test_empty_list()
    test_begin_append_finalize()
    print("ai_research_report OK")
