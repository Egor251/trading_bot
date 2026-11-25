"""Forward book collector: known-answer and idempotence pins, no network."""

from __future__ import annotations

import json

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from tools.book_collector import BookCollector, snapshot_row  # noqa: E402


def _book(bid: float, ask: float) -> dict:
    return {"bids": [[bid, 2.0]], "asks": [[ask, 3.0]]}


def test_snapshot_is_normalized_and_spread_is_positive():
    row = snapshot_row("binance", "BTC/USDT", _book(100.0, 100.1), ts=1.0)
    assert row is not None
    assert row["bid"] == 100.0 and row["ask"] == 100.1
    assert row["spread_bps"] > 0


def test_crossed_or_missing_book_is_refused():
    assert snapshot_row("x", "y", _book(101.0, 100.0)) is None
    assert snapshot_row("x", "y", {"bids": [], "asks": [[1, 1]]}) is None
    assert snapshot_row("x", "y", {"bids": [["bad", 1]], "asks": [[1, 1]]}) is None


def test_append_is_idempotent(tmp_path):
    path = tmp_path / "book.jsonl"
    c = BookCollector(path, max_lines=10)
    rows = [snapshot_row("x", "s", _book(100.0 + i, 100.1 + i), ts=float(i)) for i in range(3)]
    assert c.append(rows) == 3
    assert c.append(rows) == 0
    saved = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r["ts"] for r in saved] == [0.0, 1.0, 2.0]


def test_bounded_file_keeps_newest(tmp_path):
    path = tmp_path / "book.jsonl"
    c = BookCollector(path, max_lines=2)
    rows = [snapshot_row("x", "s", _book(100.0 + i, 100.1 + i), ts=float(i)) for i in range(3)]
    assert c.append(rows) == 3
    saved = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(saved) == 2
    assert [r["ts"] for r in saved] == [1.0, 2.0]
