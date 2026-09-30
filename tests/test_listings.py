"""Tests for MOEX listings → universe catalog composition."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.listings import board_meta  # noqa: E402
from engine.universe import Instrument  # noqa: E402
from services.universe_service import UniverseService  # noqa: E402


def test_board_meta() -> None:
    assert board_meta("TQBR")[0] == "equity"
    assert board_meta("TQTF")[0] == "etf"
    assert board_meta("SPBFUT")[0] == "future"


class _FakeConfig:
    async def get(self, _key: str, default: Any = None) -> Any:
        return default


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


class _FakeCore:
    def __init__(self) -> None:
        self.config = _FakeConfig()
        self.event_bus = _FakeBus()
        self.service_registry = type("R", (), {"get": lambda self, n: None})()


async def _listings_merge() -> None:
    core = _FakeCore()
    uni = UniverseService(core, "universe_service")  # type: ignore[arg-type]
    uni.config = uni.default_config()
    uni.config["catalog_path"] = ""
    uni.config["catalog"] = [
        {
            "symbol": "SBER",
            "class_code": "TQBR",
            "asset_class": "equity",
            "price": 100.0,
            "dollar_volume": 1.0,
        },
        {
            "symbol": "Si",
            "class_code": "SPBFUT",
            "asset_class": "future",
            "price": 90000.0,
            "dollar_volume": 1e10,
        },
    ]
    uni.config["listings_mode"] = "merge"

    async def _fake_fetch(*, boards, include_marketdata=True):
        return [
            Instrument(
                "SBER",
                "TQBR",
                "equity",
                exchange="MOEX",
                price=290.0,
                dollar_volume=2e9,
                meta={"source": "moex_iss"},
            ),
            Instrument(
                "GAZP",
                "TQBR",
                "equity",
                exchange="MOEX",
                price=160.0,
                dollar_volume=5e8,
            ),
        ]

    import engine.listings as listings_mod
    import services.universe_service as uni_mod

    orig = uni_mod.fetch_moex_board_listings
    uni_mod.fetch_moex_board_listings = _fake_fetch  # type: ignore[assignment]
    try:
        stats = await uni.refresh_listings(force=True)
        assert stats["ok"] is True
        assert stats["count"] == 2
        keys = {i.key for i in uni._catalog}  # noqa: SLF001
        assert "TQBR:SBER" in keys
        assert "TQBR:GAZP" in keys
        assert "SPBFUT:Si" in keys  # seed kept in merge
        sber = next(i for i in uni._catalog if i.symbol == "SBER")  # noqa: SLF001
        assert sber.price == 290.0  # listing overwrote seed
    finally:
        uni_mod.fetch_moex_board_listings = orig  # type: ignore[assignment]
        _ = listings_mod


def test_listings_merge() -> None:
    asyncio.run(_listings_merge())


async def _listings_replace() -> None:
    core = _FakeCore()
    uni = UniverseService(core, "universe_service")  # type: ignore[arg-type]
    uni.config = uni.default_config()
    uni.config["catalog_path"] = ""
    uni.config["catalog"] = [
        {"symbol": "Si", "class_code": "SPBFUT", "asset_class": "future", "price": 1}
    ]
    uni.config["listings_mode"] = "replace"
    uni._listings = [  # noqa: SLF001
        Instrument("SBER", "TQBR", "equity", price=1.0),
    ]
    cat = uni._compose_catalog()  # noqa: SLF001
    assert [i.key for i in cat] == ["TQBR:SBER"]


def test_listings_replace() -> None:
    asyncio.run(_listings_replace())


if __name__ == "__main__":
    test_board_meta()
    test_listings_merge()
    test_listings_replace()
    print("listings OK")
