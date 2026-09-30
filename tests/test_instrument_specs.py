"""Canonical multi-asset instrument specification tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from engine.instruments import (  # noqa: E402
    AmbiguousInstrumentError,
    InstrumentError,
    InstrumentNotFoundError,
    InstrumentRegistry,
    InstrumentResolver,
    InstrumentSpec,
    to_decimal,
)


def test_moex_equity_lots_ticks_and_identity() -> None:
    sber = InstrumentSpec(
        venue="moex",
        asset_class="equity",
        symbol="sber",
        class_code="tqbr",
        currency="rub",
        lot_size="10",
        tick_size="0.01",
        margin_model="cash",
        calendar="MOEX",
        source="moex_iss",
        metadata={"isin": "RU0009029540", "flags": ["tradable"]},
    )

    assert sber.key == "MOEX:TQBR:SBER"
    assert sber.lot_size == Decimal("10")
    assert sber.tick_value == Decimal("0.01")
    assert sber.normalize_quantity("27") == Decimal("20")
    assert sber.normalize_quantity("-27") == Decimal("-20")
    assert sber.round_price("287.125") == Decimal("287.13")
    assert sber.notional("20", "287.13") == Decimal("5742.60")
    assert sber.metadata["flags"] == ("tradable",)

    with pytest.raises(FrozenInstanceError):
        sber.symbol = "GAZP"  # type: ignore[misc]
    with pytest.raises(TypeError):
        sber.metadata["isin"] = "changed"  # type: ignore[index]


def test_moex_future_contract_economics() -> None:
    si = InstrumentSpec(
        venue="MOEX",
        asset_class="future",
        symbol="SiU6",
        class_code="SPBFUT",
        currency="RUB",
        lot_size=1,
        tick_size="1",
        tick_value="1.23567",
        contract_multiplier="1000",
        margin_model="initial_variation",
        calendar="MOEX_FORTS",
        source="quik",
        metadata={"underlying": "USD/RUB", "expiry": "2026-09-17"},
    )

    assert si.symbol == "SIU6"
    assert si.round_to_tick("92745.6") == Decimal("92746")
    assert si.calculate_notional("92.745", 2) == Decimal("185490.000")
    assert si.value_for_ticks(10, 2) == Decimal("24.71340")


def test_crypto_fractional_precision_and_synthetic_metadata() -> None:
    btc = InstrumentSpec(
        venue="binance",
        asset_class="crypto",
        symbol="btc/usdt",
        currency="usdt",
        lot_size="0.000001",
        tick_size="0.10",
        margin_model="cross",
        calendar="24/7",
        source="ccxt",
        metadata={"market_id": "BTCUSDT"},
    )
    index = InstrumentSpec(
        venue="internal",
        asset_class="synthetic",
        symbol="crypto_top_10",
        currency="USD",
        lot_size="0.0001",
        tick_size="0.00000001",
        synthetic=True,
        source="weighted_basket",
        metadata={"components": {"BTC": "0.6", "ETH": "0.4"}},
    )

    assert btc.normalize_quantity("0.123456789") == Decimal("0.123456")
    assert btc.round_price("65432.149999999") == Decimal("65432.10")
    assert index.synthetic is True
    assert index.metadata["components"]["BTC"] == "0.6"
    with pytest.raises(TypeError):
        index.metadata["components"]["BTC"] = "1"  # type: ignore[index]


def test_alpaca_stock_registry_resolution_and_aliases() -> None:
    alpaca = InstrumentSpec(
        venue="ALPACA",
        asset_class="equity",
        symbol="AAPL",
        currency="USD",
        lot_size="0.001",
        tick_size="0.01",
        margin_model="reg_t",
        calendar="XNYS",
        source="alpaca",
    )
    moex = InstrumentSpec(
        venue="MOEX",
        asset_class="equity",
        symbol="AAPL",
        class_code="FQBR",
        currency="RUB",
        lot_size=1,
        tick_size="0.01",
    )
    registry = InstrumentRegistry()
    assert registry.register(alpaca, aliases=("NASDAQ:AAPL",)) is alpaca
    registry.register(moex)

    assert registry.get("alpaca:aapl") is alpaca
    assert registry.resolve("NASDAQ:AAPL") is alpaca
    assert registry.resolve("AAPL", venue="alpaca") is alpaca
    assert InstrumentResolver(registry).resolve("MOEX:FQBR:AAPL") is moex
    with pytest.raises(AmbiguousInstrumentError):
        registry.resolve("AAPL")
    with pytest.raises(InstrumentNotFoundError):
        registry.resolve("MSFT")
    with pytest.raises(InstrumentError):
        registry.register(alpaca)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("venue", ""),
        ("asset_class", "bad class!"),
        ("symbol", " "),
        ("currency", ""),
        ("lot_size", "0"),
        ("lot_size", "-1"),
        ("tick_size", "NaN"),
        ("tick_value", "Infinity"),
        ("contract_multiplier", 0),
        ("margin_model", ""),
        ("synthetic", 1),
        ("metadata", []),
    ],
)
def test_invalid_metadata_is_rejected(field: str, value: object) -> None:
    values: dict[str, object] = {
        "venue": "MOEX",
        "asset_class": "equity",
        "symbol": "SBER",
        "currency": "RUB",
        "lot_size": "10",
        "tick_size": "0.01",
    }
    values[field] = value
    with pytest.raises(InstrumentError):
        InstrumentSpec(**values)  # type: ignore[arg-type]


def test_decimal_conversion_never_leaks_binary_float_precision() -> None:
    spec = InstrumentSpec(
        venue="ALPACA",
        asset_class="equity",
        symbol="AAPL",
        currency="USD",
        lot_size=0.1,
        tick_size=0.01,
    )

    assert spec.lot_size == Decimal("0.1")
    assert spec.tick_size == Decimal("0.01")
    assert to_decimal(0.1) == Decimal("0.1")
    assert spec.normalize_quantity(0.3) == Decimal("0.3")
    assert spec.notional("0.3", "123.45") == Decimal("37.035")
    with pytest.raises(InstrumentError):
        to_decimal(True)
    with pytest.raises(InstrumentError):
        to_decimal("not-a-number")


def test_from_mapping_and_rounding_modes() -> None:
    spec = InstrumentSpec.from_mapping(
        {
            "venue": "BINANCE",
            "asset_class": "crypto",
            "symbol": "ETH/USDT",
            "currency": "USDT",
            "lot_size": "0.01",
            "tick_size": "0.25",
        }
    )

    assert spec.normalize_quantity("1.231", rounding="up") == Decimal("1.24")
    assert spec.round_price("10.37", rounding="floor") == Decimal("10.25")
    assert spec.round_price("10.38", rounding="ceiling") == Decimal("10.50")
    with pytest.raises(InstrumentError):
        spec.round_price("10", rounding="bankers")
