"""Paper book reset + trading control helpers."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from engine.runtime import TradingRuntime  # noqa: E402
import pytest  # noqa: E402


def test_paper_reset_clears_positions() -> None:
    rt = TradingRuntime.from_config(
        {
            "mode": "paper",
            "equity": 10_000,
            "legacy_fee_bps": 5,
            "risk": {"max_position_pct": 1.0, "max_leverage": 2.0},
        }
    )
    rt.submit_signal(ticker="TEST", action="BUY", quantity=5, mark_price=100)
    assert rt.open_positions()
    rt.reset_sim_book()
    assert rt.open_positions() == []
    assert abs(float(rt.orders.equity) - 10_000) < 1e-6


def test_live_reset_forbidden() -> None:
    rt = TradingRuntime.from_config(
        {
            "mode": "live",
            "equity": 10_000,
            "risk": {"max_position_pct": 1.0, "max_leverage": 2.0},
        }
    )
    with pytest.raises(PermissionError):
        rt.reset_sim_book()


if __name__ == "__main__":
    test_paper_reset_clears_positions()
    test_live_reset_forbidden()
    print("paper reset OK")
