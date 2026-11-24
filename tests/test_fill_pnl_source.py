"""Explicit trade PnL must never be confused with a display proxy.

`estimate_fill_pnl` falls back to `direction * quantity * price * 1e-4` --
plus 1 bps for a BUY, minus 1 bps for a SELL. OpsMonitor fed that number to
`performance.record_trade` and the daily P&L accumulator, so a round trip
reported 0 while the account paid two fills of fees. The proxy remains for
display; the money path uses explicit PnL fields only.
"""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.research import actual_fill_pnl, estimate_fill_pnl  # noqa: E402


PROXY_ONLY = {"action": "BUY", "quantity": 1.0, "price": 100.0}


def test_explicit_pnl_is_used_by_both():
    payload = {**PROXY_ONLY, "pnl": -2.5, "fee": 0.07}
    assert actual_fill_pnl(payload) == -2.5
    assert estimate_fill_pnl(payload) == -2.5


def test_proxy_is_available_for_display_but_not_for_money():
    assert estimate_fill_pnl(PROXY_ONLY) == 0.01  # 1 bps of notional, display-only
    assert actual_fill_pnl(PROXY_ONLY) is None


def test_malformed_or_missing_explicit_pnl_is_unknown_not_zero():
    assert actual_fill_pnl({**PROXY_ONLY, "pnl": "n/a"}) is None
    assert actual_fill_pnl({"action": "BUY"}) is None
