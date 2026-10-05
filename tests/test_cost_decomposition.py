"""The cost model is mostly fees, and a fee is not an impact.

Almgren et al. (2005) split execution cost into permanent impact (driven by size,
persisting after the order) and temporary impact (driven by pace, vanishing when it
completes). Applying that split here produced a confident decomposition of a model
that does not contain the quantity being decomposed.

This system's per-fill cost is `fee_bps + slippage_bps`. The fee is a **transfer to
the venue** -- no execution schedule touches it, it does not scale with size, and it
does not decay. Describing it as permanent impact overstates how much of the barrier
is addressable; describing it as temporary impact implies a scheduling fix exists.

What the split actually shows is that the barrier an edge competes against is much
smaller than the barrier the account is charged, and that any "the edge does not
cover the cost" claim measured against the full model overstates the problem by the
fee share.

The unit trap is pinned here too. An edge is naturally per round trip and a cost
schedule is quoted per fill; comparing them directly halves the apparent barrier,
and that error produced a real misstatement in a research log.
"""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

import math  # noqa: E402

import pytest  # noqa: E402

from engine.simulation import breakeven_vs_cost, decompose_cost  # noqa: E402

#: The shipped model's own numbers: config/trading_bot.yaml phantom_service.
FEE_BPS = 5.0
SLIPPAGE_BPS = 2.0
#: Roll (1984) at 1m on BTC/USDT, EDGE_ROLL.
MEASURED_SPREAD_BPS = 1.19
#: tools/edge_ceiling, hardcoded at 19 call sites.
RESEARCH_COST_BPS = 10.0
#: Realized edge, basis points per trade.
EDGE_BPS = 0.78


def test_fee_dominates_and_is_not_an_impact():
    """71% of the charged cost is a fee, which no execution change can reduce."""
    split = decompose_cost(FEE_BPS, SLIPPAGE_BPS, MEASURED_SPREAD_BPS)

    assert split["total_bps"] == pytest.approx(7.0)
    assert split["fee_share"] > 0.70
    assert split["impact_share"] < 0.30
    # The impact is the measured crossing plus whatever slippage exceeds it.
    assert split["crossing_bps"] == pytest.approx(MEASURED_SPREAD_BPS)
    assert split["residual_slippage_bps"] == pytest.approx(
        SLIPPAGE_BPS - MEASURED_SPREAD_BPS
    )
    assert split["impact_bps"] == pytest.approx(SLIPPAGE_BPS)


def test_slippage_below_the_measured_spread_does_not_go_negative():
    """A model charging less slippage than the market's spread is over-optimistic.

    Flooring the residual at zero keeps the split additive and refuses to report a
    negative impact, which would mean the cost model contains a credit the exchange
    never gives.
    """
    split = decompose_cost(5.0, 0.5, MEASURED_SPREAD_BPS)
    assert split["residual_slippage_bps"] == 0.0
    assert split["impact_bps"] == pytest.approx(MEASURED_SPREAD_BPS)
    assert split["impact_share"] < 1.0


def test_zero_cost_model_does_not_divide_by_zero():
    split = decompose_cost(0.0, 0.0, 0.0)
    assert split["fee_share"] == 0.0
    assert split["impact_share"] == 0.0


def test_round_trip_units_are_converted_not_mixed():
    """An edge is per trade, a cost is per fill, and the factor of two is real.

    Getting this wrong halved the apparent barrier in a research log: 12.8x
    reported where the round-trip comparison is 25.6x.
    """
    result = breakeven_vs_cost(
        EDGE_BPS,
        RESEARCH_COST_BPS,
        fee_bps=FEE_BPS,
        measured_spread_bps=MEASURED_SPREAD_BPS,
    )

    assert result["cost_bps_full_round_trip"] == pytest.approx(20.0)
    assert result["shortfall_vs_full"] == pytest.approx(20.0 / EDGE_BPS)
    assert result["shortfall_vs_full"] > 25.0


def test_impact_only_shortfall_is_much_smaller_than_the_full_one():
    """The number a strategy can actually act on.

    Excluding the fee removes a barrier that is fixed regardless of how the order is
    placed, so the shortfall drops by roughly the fee share.
    """
    result = breakeven_vs_cost(
        EDGE_BPS,
        RESEARCH_COST_BPS,
        fee_bps=FEE_BPS,
        measured_spread_bps=MEASURED_SPREAD_BPS,
    )

    # 20 bps round trip, 10 of which is fee -> 10 bps of impact.
    assert result["cost_bps_impact_round_trip"] == pytest.approx(10.0)
    assert result["shortfall_vs_impact"] == pytest.approx(10.0 / EDGE_BPS)
    # Exactly half here, because the research cost is 5 fee + 5 slippage per fill.
    # The general statement is that the drop equals the fee share.
    assert result["shortfall_vs_impact"] == pytest.approx(
        result["shortfall_vs_full"] * (1.0 - FEE_BPS / RESEARCH_COST_BPS)
    )


def test_the_conclusion_survives_even_with_no_fee_at_all():
    """The edge still fails against the measured spread on its own.

    This is the part that matters. Reframing the cost model does not rescue the
    4h reversal rule: at a zero fee the only cost left is the spread Roll measured,
    and the edge is still well below it. A correction to a cost accounting must not
    become an excuse for a strategy that still does not pay.
    """
    zero_fee = breakeven_vs_cost(
        EDGE_BPS, MEASURED_SPREAD_BPS, fee_bps=0.0,
        measured_spread_bps=MEASURED_SPREAD_BPS,
    )
    assert zero_fee["cost_bps_full_round_trip"] == pytest.approx(2 * MEASURED_SPREAD_BPS)
    assert zero_fee["shortfall_vs_full"] > 1.0


def test_without_a_fee_the_impact_ratio_is_not_invented():
    """Absent the fee split, the two ratios are equal rather than guessed."""
    result = breakeven_vs_cost(EDGE_BPS, RESEARCH_COST_BPS)
    assert result["shortfall_vs_impact"] == result["shortfall_vs_full"]
    assert result["cost_bps_impact_round_trip"] == result["cost_bps_full_round_trip"]


def test_a_zero_or_negative_edge_reports_infinite_shortfall():
    """Division by a non-positive edge must not silently produce a finite ratio."""
    for edge in (0.0, -1.0):
        result = breakeven_vs_cost(edge, 10.0, fee_bps=FEE_BPS,
                                  measured_spread_bps=MEASURED_SPREAD_BPS)
        assert math.isinf(result["shortfall_vs_full"])
        assert math.isinf(result["shortfall_vs_impact"])