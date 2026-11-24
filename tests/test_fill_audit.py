"""Pin the fill-audit rules (R-42, raw-record S10). Synthetic rows only —
production data never enters tests."""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from tools.fill_audit import (  # noqa: E402
    has_reference_price,
    is_fill_like,
    markers,
)


def _row(**kw) -> dict:
    base = {"strategy_id": "consilium", "price": 50000.0, "fee": 0.0,
            "reason": "signal", "meta": {}}
    base.update(kw)
    return base


def test_markers_catch_documented_test_rows():
    assert "test-strategy" in markers(_row(strategy_id="a", reason="fill"))
    assert "mock-price" in markers(_row(price=100.0, reason="fill"))
    assert "mock-price" in markers(_row(price=159.0, reason="paper"))
    assert "mock-price" in markers(_row(price=285.0, reason="quik_fill"))
    assert "paper-sim" in markers(_row(reason="paper"))
    assert markers(_row()) == []


def test_fill_like_covers_reasons_and_fees():
    assert is_fill_like(_row(reason="fill"))
    assert is_fill_like(_row(reason="quik_fill"))
    assert is_fill_like(_row(reason="signal", fee=1.5))
    assert not is_fill_like(_row(reason="signal"))
    assert not is_fill_like(_row(reason="paper"))


def test_reference_price_must_be_present_and_positive():
    assert not has_reference_price(_row())
    assert not has_reference_price(_row(meta={"reference_price": 0.0}))
    assert has_reference_price(_row(meta={"reference_price": 49900.0}))
    assert has_reference_price(_row(reference_price=49900.0))
