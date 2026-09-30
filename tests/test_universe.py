"""Universe selection unit tests."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from engine import (  # noqa: E402
    Instrument,
    ScreenPolicy,
    UniverseConfig,
    UniverseLimits,
    UniverseManager,
    normalize_universe_mode,
)


def _catalog() -> list[Instrument]:
    return [
        Instrument(
            "SBER", "TQBR", "equity", price=285, dollar_volume=1.4e9, atr_pct=0.015
        ),
        Instrument(
            "GAZP", "TQBR", "equity", price=165, dollar_volume=5e8, atr_pct=0.04
        ),
        Instrument(
            "LKOH", "TQBR", "equity", price=7200, dollar_volume=1.5e9, atr_pct=0.012
        ),
        Instrument(
            "PENNY", "TQBR", "equity", price=0.5, dollar_volume=5e3, atr_pct=0.2
        ),
        Instrument(
            "BTC/USDT",
            "CRYPTO",
            "crypto",
            price=65000,
            dollar_volume=1.3e9,
            atr_pct=0.05,
        ),
        Instrument(
            "Si", "SPBFUT", "future", price=90000, dollar_volume=1e10, atr_pct=0.02
        ),
        Instrument(
            "BR", "SPBFUT", "future", price=80, dollar_volume=2e9, atr_pct=0.035
        ),
    ]


def test_manual_static_active() -> None:
    mgr = UniverseManager(
        UniverseConfig(
            mode="manual",
            static_active=("TQBR:SBER", "GAZP"),
            screen=ScreenPolicy(max_active=10),
        )
    )
    snap = mgr.select(_catalog())
    assert snap.mode == "manual"
    assert snap.active_symbols() == ["SBER", "GAZP"]


def test_screener_liquidity_and_filters() -> None:
    mgr = UniverseManager(
        UniverseConfig(
            mode="screener",
            screen=ScreenPolicy(
                name="liquid",
                rank_by="dollar_volume",
                max_candidates=10,
                max_active=2,
                min_price=1.0,
                min_dollar_volume=1e6,
                asset_classes=("equity",),
            ),
        )
    )
    snap = mgr.select(_catalog())
    assert snap.mode == "screener"
    assert snap.rank_by == "dollar_volume"
    assert all(i.asset_class == "equity" for i in snap.candidates)
    assert "PENNY" not in snap.active_symbols()
    assert len(snap.active) == 2
    assert snap.active[0].symbol == "LKOH"
    assert snap.active[1].symbol == "SBER"


def test_volatility_profile_ranks_by_atr() -> None:
    mgr = UniverseManager.from_mapping(
        {
            "mode": "screener",
            "profile": "volatility",
            "screen": {
                "max_active": 2,
                "min_dollar_volume": 1e6,
                "asset_classes": ["equity"],
            },
        }
    )
    snap = mgr.select(_catalog())
    assert snap.profile == "volatility"
    assert snap.rank_by == "atr_pct"
    # Among liquid equities, GAZP has highest atr_pct
    assert snap.active[0].symbol == "GAZP"
    assert "PENNY" not in snap.active_symbols()  # failed dollar_volume floor


def test_futures_speculation_profile() -> None:
    mgr = UniverseManager.from_mapping(
        {
            "mode": "screener",
            "profile": "futures_speculation",
            "screen": {"max_active": 2},
        }
    )
    snap = mgr.select(_catalog())
    assert all(i.asset_class == "future" for i in snap.active)
    assert snap.active[0].symbol == "Si"


def test_crypto_liquid_profile() -> None:
    mgr = UniverseManager.from_mapping(
        {
            "mode": "screener",
            "profile": "crypto_liquid",
            "screen": {"max_active": 3, "min_dollar_volume": 1e6},
        }
    )
    snap = mgr.select(_catalog())
    assert snap.profile == "crypto_liquid"
    assert snap.rank_by == "dollar_volume"
    assert all(i.asset_class == "crypto" for i in snap.active)
    assert snap.active_keys() == ["CRYPTO:BTC/USDT"]
    assert "crypto_liquid" in snap.reason
    assert "dollar_volume" in snap.reason


def test_pin_keeps_active_under_screener() -> None:
    """Open-position pins stay in the book even when over max_active."""
    mgr = UniverseManager(
        UniverseConfig(
            mode="screener",
            include=("CRYPTO:BTC/USDT", "TQBR:PENNY"),
            screen=ScreenPolicy(
                name="liquid",
                rank_by="dollar_volume",
                max_active=2,
                min_price=1.0,
                min_dollar_volume=1e6,
                asset_classes=("equity",),
            ),
        )
    )
    snap = mgr.select(_catalog())
    keys = set(snap.active_keys())
    assert "CRYPTO:BTC/USDT" in keys
    assert "TQBR:PENNY" in keys
    # Pins are kept even though they fail equity floors / exceed max_active.
    assert len(snap.active) >= 2


def test_profiles_union_active_books() -> None:
    mgr = UniverseManager.from_mapping(
        {
            "mode": "screener",
            "profiles": {
                "trend": {
                    "profile": "crypto_liquid",
                    "max_active": 1,
                    "min_dollar_volume": 1e6,
                },
                "futures": {
                    "profile": "futures_speculation",
                    "max_active": 1,
                },
            },
        }
    )
    snap = mgr.select(_catalog())
    assert snap.profile == "union"
    assert "union:" in snap.reason
    keys = set(snap.active_keys())
    assert "CRYPTO:BTC/USDT" in keys
    assert "SPBFUT:Si" in keys
    assert snap.books.get("trend") == ["CRYPTO:BTC/USDT"]
    assert snap.books.get("futures") == ["SPBFUT:Si"]
    payload = snap.to_payload()
    assert payload["books"]["trend"] == ["CRYPTO:BTC/USDT"]


def test_merge_active_books() -> None:
    mgr = UniverseManager.from_mapping({"mode": "screener", "profile": "liquid"})
    liquid = mgr.select_with_policy(
        _catalog(),
        ScreenPolicy(
            name="liquid",
            rank_by="dollar_volume",
            max_active=1,
            asset_classes=("equity",),
            min_dollar_volume=1e6,
        ),
    )
    fut = mgr.select_with_policy(
        _catalog(),
        ScreenPolicy(
            name="futures_speculation",
            rank_by="dollar_volume",
            max_active=1,
            asset_classes=("future",),
            min_dollar_volume=1e6,
        ),
    )
    merged = UniverseManager.merge_active_books(liquid, fut)
    keys = {i.key for i in merged}
    assert "TQBR:LKOH" in keys or "TQBR:SBER" in keys
    assert "SPBFUT:Si" in keys


def test_include_exclude_and_hysteresis() -> None:
    mgr = UniverseManager(
        UniverseConfig(
            mode="screener",
            include=("TQBR:GAZP",),
            exclude=("LKOH",),
            screen=ScreenPolicy(
                max_active=2,
                min_price=1.0,
                min_dollar_volume=1e6,
                asset_classes=("equity",),
                prefer_previous_active=True,
            ),
        )
    )
    first = mgr.select(_catalog())
    assert "LKOH" not in first.active_symbols()
    assert "GAZP" in first.active_symbols()
    second = mgr.select(_catalog())
    assert set(second.active_keys()) == set(first.active_keys())


def test_from_mapping_legacy_limits() -> None:
    mgr = UniverseManager.from_mapping(
        {
            "mode": "manual",
            "static_active": ["SBER"],
            "limits": {"max_active": 5},
        }
    )
    snap = mgr.select(_catalog())
    assert snap.active_symbols() == ["SBER"]
    # UniverseLimits alias still works
    assert isinstance(UniverseLimits(), ScreenPolicy)


def test_normalize_universe_mode_aliases() -> None:
    assert normalize_universe_mode("static") == "manual"
    assert normalize_universe_mode("screen") == "screener"
    assert normalize_universe_mode("hybrid") == "hybrid"
    assert normalize_universe_mode("MANUAL") == "manual"


def test_static_alias_selects_manual_book() -> None:
    mgr = UniverseManager.from_mapping(
        {
            "mode": "static",
            "static_active": ["CRYPTO:BTC/USDT", "CRYPTO:ETH/USDT"],
            "screen": {"max_active": 10},
        }
    )
    assert mgr.config.mode == "manual"
    snap = mgr.select(_catalog())
    assert snap.mode == "manual"
    keys = snap.active_keys()
    assert "CRYPTO:BTC/USDT" in keys
    assert "CRYPTO:ETH/USDT" in keys


if __name__ == "__main__":
    test_manual_static_active()
    test_screener_liquidity_and_filters()
    test_volatility_profile_ranks_by_atr()
    test_futures_speculation_profile()
    test_crypto_liquid_profile()
    test_pin_keeps_active_under_screener()
    test_profiles_union_active_books()
    test_merge_active_books()
    test_include_exclude_and_hysteresis()
    test_from_mapping_legacy_limits()
    test_normalize_universe_mode_aliases()
    test_static_alias_selects_manual_book()
    print("universe smoke OK")
