"""Shared instrument picker helpers (no Dear PyGui)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ui.instrument_keys import (  # noqa: E402
    catalog_keys_from_snap,
    filter_keys,
    parse_instrument_key,
    search_haystack,
)


def test_parse_instrument_key() -> None:
    assert parse_instrument_key("CRYPTO:BTC/USDT") == ("CRYPTO", "BTC/USDT")
    assert parse_instrument_key("sber", default_class="TQBR") == ("TQBR", "SBER")


def test_filter_and_catalog_order() -> None:
    snap = {
        "active_keys": ["CRYPTO:ETH/USDT", "CRYPTO:BTC/USDT"],
        "catalog": [
            {"key": "CRYPTO:SOL/USDT"},
            {"key": "CRYPTO:BTC/USDT"},
            {"symbol": "DOGE/USDT", "class_code": "CRYPTO"},
        ],
    }
    keys = catalog_keys_from_snap(snap, prefer_active=True)
    assert keys[:2] == ["CRYPTO:ETH/USDT", "CRYPTO:BTC/USDT"]
    assert "CRYPTO:SOL/USDT" in keys
    assert filter_keys(keys, "btc") == ["CRYPTO:BTC/USDT"]
    assert "btc" in search_haystack("CRYPTO:BTC/USDT")


if __name__ == "__main__":
    test_parse_instrument_key()
    test_filter_and_catalog_order()
    print("picker smoke OK")
