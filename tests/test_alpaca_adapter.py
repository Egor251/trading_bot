"""Alpaca adapter smoke — no network / no keys required."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from engine.adapters.alpaca_adapter import AlpacaMarketDataAdapter, _parse_timeframe  # noqa: E402


def test_alpaca_available_when_package_installed() -> None:
    a = AlpacaMarketDataAdapter()
    assert a.available is True


def test_alpaca_configured_requires_keys() -> None:
    empty = AlpacaMarketDataAdapter(api_key="", secret_key="")
    assert empty.configured is False
    filled = AlpacaMarketDataAdapter(api_key="PKTEST", secret_key="secret")
    assert filled.configured is True


def test_parse_timeframe_minute() -> None:
    tf = _parse_timeframe("1m")
    assert tf is not None
