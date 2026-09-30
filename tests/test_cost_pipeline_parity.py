from decimal import Decimal

from engine.runtime import TradingRuntime


def _run(mode: str) -> TradingRuntime:
    runtime = TradingRuntime.from_config(
        {
            "mode": mode,
            "equity": 10_000,
            "legacy_fee_bps": 5,
            "legacy_slippage_bps": 2,
            "risk": {"max_position_pct": 1, "max_leverage": 2},
        }
    )
    runtime.submit_signal(
        ticker="TEST", action="BUY", quantity=10, mark_price=100
    )
    runtime.submit_signal(
        ticker="TEST", action="SELL", quantity=10, mark_price=110
    )
    return runtime


def test_paper_phantom_and_replay_have_identical_net_ledger() -> None:
    runtimes = [_run(mode) for mode in ("paper", "phantom", "replay")]
    expected = runtimes[0].snapshot()
    for runtime in runtimes[1:]:
        snapshot = runtime.snapshot()
        assert snapshot["equity"] == expected["equity"]
        assert snapshot["cash"] == expected["cash"]
        assert snapshot["total_costs"] == expected["total_costs"]
        assert [
            (row["side"], row["quantity"], row["price"], row["cash_delta"])
            for row in snapshot["fill_ledger"]
        ] == [
            (row["side"], row["quantity"], row["price"], row["cash_delta"])
            for row in expected["fill_ledger"]
        ]


def test_costs_are_explicit_and_not_embedded_in_fill_price() -> None:
    runtime = _run("paper")
    fills = runtime.orders.fill_ledger
    assert [str(fill.price) for fill in fills] == ["100.0", "110.0"]
    assert fills[0].gross_notional == Decimal("1000")
    assert fills[0].total_cost > 0
    assert runtime.orders.equity < 10_100
