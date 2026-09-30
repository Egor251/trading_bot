from __future__ import annotations

from decimal import Decimal

import pytest

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.portfolio import PortfolioError, PortfolioLedger


D = Decimal


def test_fx_paths_are_reciprocal_and_preserve_value() -> None:
    ledger = PortfolioLedger("USD")
    ledger.set_fx_rate("EUR", "USD", "1.20")
    ledger.set_fx_rate("GBP", "EUR", "1.10")

    for amount in ("0", "0.01", "17.35", "1000000.125"):
        value = D(amount)
        usd = ledger.convert(value, "GBP", "USD")
        assert ledger.convert(usd, "USD", "GBP") == value

    ledger.deposit("100", "EUR")
    ledger.deposit("10", "GBP")
    assert ledger.cash_value() == D("133.20")
    before = ledger.cash_value()
    assert ledger.exchange_cash("10", "EUR", "USD") == D("12.0")
    assert ledger.cash_value() == before
    ledger.assert_invariants()


def test_missing_fx_path_fails_instead_of_silently_misvaluing() -> None:
    ledger = PortfolioLedger("USD")
    ledger.deposit("10", "JPY")
    with pytest.raises(PortfolioError, match="missing FX path"):
        ledger.equity()


def test_equity_round_trip_and_costs_reconcile_attribution() -> None:
    ledger = PortfolioLedger("USD")
    ledger.deposit("10000")
    ledger.trade_equity("ABC", "10", "100", "USD", strategy_id="value", fees="1")
    ledger.mark_position("ABC", "110")

    open_attr = ledger.strategy_attribution("value")
    assert open_attr.unrealized_pnl == D("100")
    assert open_attr.net_pnl == D("99")

    ledger.trade_equity("ABC", "-10", "110", "USD", strategy_id="value", fees="1")
    attr = ledger.strategy_attribution("value")
    assert attr.realized_pnl == D("100")
    assert attr.unrealized_pnl == 0
    assert attr.total_costs == D("2")
    assert attr.net_pnl == D("98")
    assert attr.net_return == D("0.098")
    assert ledger.equity() == D("10098")
    ledger.assert_invariants()


def test_futures_margin_headroom_variation_and_margin_call() -> None:
    ledger = PortfolioLedger("USD")
    ledger.deposit("1500")
    ledger.open_futures(
        "ES",
        "1",
        "1000",
        "USD",
        initial_margin="1000",
        maintenance_margin="750",
        multiplier="10",
        strategy_id="trend",
    )
    assert ledger.margin_headroom == D("500")
    assert not ledger.margin_call

    ledger.mark_futures("ES", "920")
    status = ledger.margin_status()
    assert status.collateral == D("700")
    assert status.maintenance_headroom == D("-50")
    assert status.margin_call

    assert ledger.settle_variation_margin("ES") == D("-800")
    assert ledger.cash["USD"] == D("700")
    assert ledger.futures[("ES", "trend")].unrealized_pnl == 0
    assert ledger.margin_call
    ledger.assert_invariants()


def test_initial_margin_rejects_position_without_headroom() -> None:
    ledger = PortfolioLedger("USD")
    ledger.deposit("999")
    with pytest.raises(PortfolioError, match="initial margin"):
        ledger.open_futures(
            "NQ",
            1,
            100,
            "USD",
            initial_margin=1000,
            maintenance_margin=800,
        )


def test_crypto_funding_and_short_borrow_are_net_costs() -> None:
    ledger = PortfolioLedger("USDT")
    ledger.deposit("10000")
    ledger.trade_crypto(
        "BTC-PERP",
        "1",
        "1000",
        "USDT",
        strategy_id="carry",
        asset_class="crypto",
    )
    assert ledger.apply_funding("BTC-PERP", "0.01") == D("10")

    ledger.trade_crypto(
        "ALT",
        "-2",
        "100",
        "USDT",
        strategy_id="short",
        asset_class="crypto",
    )
    assert ledger.accrue_borrow_fee("ALT", "0.02") == D("4")

    carry = ledger.strategy_attribution("carry")
    short = ledger.strategy_attribution("short")
    assert carry.funding_costs == D("10")
    assert carry.net_pnl == D("-10")
    assert short.borrow_costs == D("4")
    assert short.net_pnl == D("-4")
    assert ledger.equity() == D("9986")


def test_long_positions_never_accrue_borrow_cost() -> None:
    ledger = PortfolioLedger("USD")
    ledger.deposit("1000")
    ledger.trade_equity("LONG", 1, 100, "USD", strategy_id="long")
    assert ledger.accrue_borrow_fee("LONG", "0.50") == 0
    assert ledger.strategy_attribution("long").borrow_costs == 0
