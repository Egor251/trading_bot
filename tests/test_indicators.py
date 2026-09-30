"""Tests for engine indicators + FormulaSeries (cb_math)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from engine.indicators import FormulaSeries, equity_pnl, max_drawdown, sma  # noqa: E402


def test_sma() -> None:
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    out = sma(vals, 3)
    assert out[0] is None and out[1] is None
    assert out[2] == 2.0
    assert out[4] == 4.0


def test_max_drawdown() -> None:
    assert max_drawdown([100, 110, 90, 95]) == 20 / 110


def test_equity_pnl() -> None:
    fills = [
        {"action": "BUY", "quantity": 10, "price": 100},
        {"action": "SELL", "quantity": 10, "price": 110},
    ]
    m = equity_pnl(fills, start_equity=10_000)
    assert m["trades"] == 2
    assert m["pnl"] == 100.0


def test_equity_pnl_open_marked_to_window_end() -> None:
    """Single open fill must MTM to window end — not freeze at entry (fee-only ghost)."""
    fills = [{"action": "BUY", "quantity": 10, "price": 100, "cost": 0.7}]
    frozen = equity_pnl(fills, start_equity=10_000)
    assert abs(frozen["pnl"] - (-0.7)) < 1e-9  # legacy: mark = entry
    marked = equity_pnl(fills, start_equity=10_000, mark_price=110.0)
    # 10 * (110-100) - 0.7 = 99.3
    assert abs(marked["pnl"] - 99.3) < 1e-9


def test_formula_series_diff_and_eval() -> None:
    fs = FormulaSeries("x**2")
    assert "2*x" in fs.derive("x").replace(" ", "")
    series = FormulaSeries("a - b")
    out = series.eval({"a": [10.0, 12.0], "b": [1.0, 2.0]})
    assert out == [9.0, 10.0]


def test_formula_point_via_cb_math() -> None:
    fs = FormulaSeries("2*x + 3")
    assert fs.eval_point({"x": 4}) == 11.0


if __name__ == "__main__":
    test_sma()
    test_max_drawdown()
    test_equity_pnl()
    test_equity_pnl_open_marked_to_window_end()
    test_formula_series_diff_and_eval()
    test_formula_point_via_cb_math()
    print("indicators OK")
