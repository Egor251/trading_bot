"""Unit tests for chart math helpers."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from ui.chart_math import (  # noqa: E402
    aggregate_ohlc,
    bollinger,
    heikin_ashi,
    macd,
    rsi,
)


def _sample(n: int = 60) -> dict[str, list[float]]:
    closes = [100.0 + (i % 7) - 3 + i * 0.05 for i in range(n)]
    opens = [closes[i - 1] if i else closes[0] for i in range(n)]
    highs = [max(opens[i], closes[i]) + 0.4 for i in range(n)]
    lows = [min(opens[i], closes[i]) - 0.4 for i in range(n)]
    vols = [1000.0 + i * 10 for i in range(n)]
    return {
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "closes": closes,
        "volumes": vols,
    }


def test_aggregate_5m() -> None:
    src = _sample(20)
    out = aggregate_ohlc(src, factor=5)
    assert len(out["closes"]) == 4
    assert out["opens"][0] == src["opens"][0]
    assert out["closes"][0] == src["closes"][4]
    assert out["highs"][0] == max(src["highs"][0:5])
    assert out["volumes"][0] == sum(src["volumes"][0:5])


def test_aggregate_preserves_dates() -> None:
    src = _sample(20)
    src["dates"] = [1_700_000_000.0 + i * 60.0 for i in range(20)]
    out = aggregate_ohlc(src, factor=5)
    assert len(out["dates"]) == 4
    assert out["dates"][0] == src["dates"][4]
    assert out["dates"][-1] == src["dates"][19]


def test_heikin_and_oscillators() -> None:
    src = _sample(80)
    ha = heikin_ashi(src)
    assert len(ha["closes"]) == 80
    assert ha["highs"][10] >= ha["lows"][10]
    r = rsi(src["closes"], period=14)
    assert r[13] is None
    assert r[20] is not None
    assert 0.0 <= float(r[20]) <= 100.0
    mid, up, lo = bollinger(src["closes"], window=20)
    assert mid[30] is not None and up[30] is not None and lo[30] is not None
    assert float(up[30]) >= float(mid[30]) >= float(lo[30])
    line, sig, hist = macd(src["closes"])
    assert any(v is not None for v in line)
    assert any(v is not None for v in sig)


if __name__ == "__main__":
    test_aggregate_5m()
    test_aggregate_preserves_dates()
    test_heikin_and_oscillators()
    print("chart math OK")
