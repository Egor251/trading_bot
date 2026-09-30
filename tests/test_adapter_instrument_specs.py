from decimal import Decimal
from types import SimpleNamespace

import pytest

from engine.adapters.alpaca_adapter import instrument_spec_from_alpaca_asset
from engine.adapters.ccxt_adapter import instrument_spec_from_ccxt_market
from engine.adapters.quik_adapter import instrument_spec_from_quik_security


def test_ccxt_market_produces_derivative_spec() -> None:
    spec = instrument_spec_from_ccxt_market(
        {
            "id": "BTCUSDT",
            "symbol": "BTC/USDT:USDT",
            "base": "BTC",
            "quote": "USDT",
            "settle": "USDT",
            "swap": True,
            "contract": True,
            "contractSize": 1,
            "precision": {"amount": 0.001, "price": 0.1},
            "limits": {"amount": {"min": 0.001}},
            "active": True,
        },
        exchange_id="binance",
    )
    assert spec.venue == "BINANCE"
    assert spec.asset_class == "swap"
    assert spec.currency == "USDT"
    assert spec.lot_size == Decimal("0.001")
    assert spec.tick_size == Decimal("0.1")
    assert spec.margin_model == "derivative"


def test_alpaca_asset_produces_equity_spec() -> None:
    spec = instrument_spec_from_alpaca_asset(
        SimpleNamespace(
            id="asset-1",
            symbol="AAPL",
            asset_class="us_equity",
            exchange="NASDAQ",
            status="active",
            tradable=True,
            fractionable=True,
            shortable=True,
        )
    )
    assert spec.symbol == "AAPL"
    assert spec.currency == "USD"
    assert spec.lot_size == Decimal("0.000001")
    assert spec.calendar == "XNYS"


def test_quik_futures_uses_step_value_for_multiplier() -> None:
    spec = instrument_spec_from_quik_security(
        {
            "currency": "RUB",
            "lot_size": 1,
            "min_price_step": "10",
            "step_price": "12.5",
        },
        class_code="SPBFUT",
        symbol="SiZ6",
    )
    assert spec.asset_class == "future"
    assert spec.tick_value == Decimal("12.5")
    assert spec.contract_multiplier == Decimal("1.25")


def test_quik_unknown_economic_metadata_fails_closed() -> None:
    with pytest.raises(ValueError):
        instrument_spec_from_quik_security(
            {}, class_code="TQBR", symbol="SBER"
        )
