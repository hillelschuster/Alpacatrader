"""Regression pins for the retained-mechanism calibration label and quote economics.

The model was fit on the realized GROSS proxy, so the forecast must be calibrated
against gross only; the 100 bps round-trip friction is a separate economic result.
For matched quoted pairs the touch price return, the integer-share idle effect and
the cost-model difference are separately named quantities that sum exactly, row by
row, to the touch-minus-proxy net difference; unresolved pairs stay null.
"""

import sys
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from alpha_retained_mechanism import (
    ORDER_BUDGET,
    QUOTED_STATUS,
    pair_economics_block,
    with_pair_economics,
)


def pair(
    quote_status,
    entry_open,
    exit_open_proxy,
    entry_ask,
    exit_bid,
    quantity,
    quoted_net_0,
    quoted_net_25,
    cost_bps=100.0,
):
    """One trade row; gross/net are the immutable trades' own bar-price economics."""
    gross = exit_open_proxy / entry_open - 1
    side = cost_bps / 20_000.0
    net = (1 + gross) * (1 - side) / (1 + side) - 1
    return {
        "quote_status": quote_status,
        "entry_open": entry_open,
        "exit_open_proxy": exit_open_proxy,
        "gross": gross,
        "net": net,
        "cost_bps": cost_bps,
        "entry_ask": entry_ask,
        "exit_bid": exit_bid,
        "quantity": quantity,
        "quoted_net_0": quoted_net_0,
        "quoted_net_25": quoted_net_25,
    }


# winner: bar 10.00 -> 10.20 (+2%), touched 10.01 -> 10.19, 99 shares of $10.01
# ($991.98 invested of $1000, $8.02 idle); the 25 bps rung pays side=0.00125 on both
# legs and shrinks the paid quantity to 99 shares as well.
WINNER = pair(QUOTED_STATUS, 10.00, 10.20, 10.01, 10.19, 99, 0.01782, 0.01532025)
# loser: bar 10.00 -> 9.80 (-2%), touched 10.02 -> 9.78, 99 shares
LOSER = pair(QUOTED_STATUS, 10.00, 9.80, 10.02, 9.78, 99, -0.02376, -0.02621025)
# quote known but top-of-book capacity unknown: no priced budget result at all
UNRESOLVED = pair("unknown_top_of_book_capacity", 10.00, 10.10, 10.05, None, None, None, None)


def test_quoted_pair_split_matches_hand_computed_economics():
    out = with_pair_economics(pl.DataFrame([WINNER])).row(0, named=True)
    # touch price ratio and the fixed bar-price gross proxy are different numbers
    assert out["actual_price_return"] == pytest.approx(0.017982017982)
    assert out["gross"] == pytest.approx(0.02)
    # paying 10.01 vs a 10.00 bar costs 0.20pp of the proxy gross
    assert out["pure_price_effect"] == pytest.approx(-0.002017982018)
    # integer shares leave $8.02 of the budget idle: budget ROI is below the price ratio
    assert out["quoted_net_0"] == pytest.approx(0.01782)
    assert out["idle_rounding_effect"] == pytest.approx(-0.000162017982)
    # cost models differenced: proxy 100bps wedge (0.010149) minus touch 25bps rung
    assert out["fee_effect"] == pytest.approx(0.007649503731)
    # exact row-wise identity across price + rounding + fees/cost
    assert out["net_difference"] == pytest.approx(0.005469503731)
    assert out["net_difference"] == pytest.approx(
        out["pure_price_effect"] + out["idle_rounding_effect"] + out["fee_effect"]
    )


def test_identity_holds_in_both_directions_with_exact_block_residual():
    block = pair_economics_block(with_pair_economics(pl.DataFrame([WINNER, LOSER])))
    assert block["n"] == 2
    assert block["identity_max_abs_residual"] == 0.0
    # the loser's wedge is smaller because the proxy fee scales with (1+gross)
    winner = with_pair_economics(pl.DataFrame([WINNER])).row(0, named=True)
    loser = with_pair_economics(pl.DataFrame([LOSER])).row(0, named=True)
    assert loser["fee_effect"] == pytest.approx(0.007300993781)
    assert loser["fee_effect"] < winner["fee_effect"]
    assert loser["net_difference"] == pytest.approx(0.003540993781)
    # same fixed $1000 budget under both cost models, labelled by their own means
    assert block["dollars_proxy_net_at_order_budget"] == pytest.approx(
        (WINNER["net"] + LOSER["net"]) * ORDER_BUDGET, abs=0.01
    )
    assert block["mean_net_difference_touch_minus_proxy"] == pytest.approx(
        (0.005469503731 + 0.003540993781) / 2, abs=1e-6
    )


def test_entry_ask_above_the_bar_open_is_a_cost_not_an_improvement():
    r = pair(QUOTED_STATUS, 10.00, 10.20, 10.03, 10.20, 99, quoted_net_0=None, quoted_net_25=None)
    r["quoted_net_0"] = 99 * (10.20 - 10.03) / ORDER_BUDGET
    r["quoted_net_25"] = 99 * (10.20 * 0.99875 - 10.03 * 1.00125) / ORDER_BUDGET
    out = with_pair_economics(pl.DataFrame([r])).row(0, named=True)
    assert r["entry_ask"] > r["entry_open"]
    assert out["actual_price_return"] < out["gross"]
    assert out["pure_price_effect"] < 0


def test_unresolved_pairs_stay_null_never_zero():
    out = with_pair_economics(pl.DataFrame([UNRESOLVED, WINNER]))
    row = out.row(0, named=True)
    assert row["quote_status"] != QUOTED_STATUS
    for col in (
        "actual_price_return",
        "pure_price_effect",
        "idle_rounding_effect",
        "actual_touch_net_25",
        "net_difference",
        "fee_effect",
    ):
        assert row[col] is None, col
    # ...and they are excluded from the block instead of dragged in as zeros
    assert pair_economics_block(out)["n"] == 1
    assert pair_economics_block(pl.DataFrame([UNRESOLVED])) == {
        "n": 0,
        "note": "no quoted pairs in this period",
    }
