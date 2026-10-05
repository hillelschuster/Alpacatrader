"""Capacity attribution preserves unresolved liquidity and whole-book money sets."""

import numpy as np
import owned_claim_cash_capacity as capacity
import owned_claim_cash_execution as execution
import polars as pl
import pytest

KEY = {"clock": 540, "n": 3, "side_cost": 0.005, "policy": "h3:cash", "cycles": "once"}


def _leg(day, ticker, side, minute, participation):
    price = 10.0 if side == "buy" else 11.0
    return {
        **KEY,
        "day": day,
        "ticker": ticker,
        "rank": 1 if ticker == "A" else 2,
        "side": side,
        "decision_et": minute - 1,
        "exec_et": minute,
        "px": price,
        "shares_per_capital": 0.03,
        "gross_fraction": 0.03 * price,
        "fee_fraction": 0.03 * price * 0.005,
        "reason": "fresh" if side == "buy" else "exit",
        "participation": participation,
    }


def _record(buy_participation=0.005, sell_participation=0.005):
    fills = pl.DataFrame(
        [
            _leg("known", "A", "buy", 600, buy_participation),
            _leg("known", "A", "sell", 605, sell_participation),
            _leg("unknown", "A", "buy", 600, 0.005),
            _leg("unknown", "A", "sell", 605, 0.005),
            _leg("unknown", "B", "buy", 600, 0.005),
        ]
    ).with_columns(pl.col("participation").cast(pl.Float64))
    daily = pl.DataFrame(
        [
            {
                "day": "known",
                "ret": 0.02685,
                "gross_pnl": 0.03,
                "fees": 0.00315,
                "roster_claims": 1,
            },
            {
                "day": "unknown",
                "ret": None,
                "gross_pnl": -0.27,
                "fees": 0.00465,
                "roster_claims": 2,
            },
            {"day": "cash", "ret": 0.0, "gross_pnl": 0.0, "fees": 0.0, "roster_claims": 1},
        ]
    ).with_columns(
        pl.lit(0).alias("affordability_unknown"),
        pl.lit(False).alias("unresolved_fresh"),
        pl.lit([], dtype=pl.List(pl.String)).alias("unresolved_fresh_tickers"),
    )
    members = pl.DataFrame(
        [
            {"day": "known", "ticker": "A", "net_pnl": 0.02685, "gross_pnl": 0.03, "fees": 0.00315},
            {
                "day": "unknown",
                "ticker": "A",
                "net_pnl": 0.02685,
                "gross_pnl": 0.03,
                "fees": 0.00315,
            },
            {"day": "unknown", "ticker": "B", "net_pnl": None, "gross_pnl": -0.3, "fees": 0.0015},
            {"day": "cash", "ticker": "A", "net_pnl": 0.0, "gross_pnl": 0.0, "fees": 0.0},
        ]
    )
    pairs = capacity.attach_participation(execution.build_pairs(fills), fills)
    return capacity.analyse_cell(KEY, daily, fills, members, pairs, None)


def test_known_member_profit_of_an_unknown_book_never_enters_capacity_cohorts():
    record = _record()
    assert record["known_set_money"]["known_books"] == 2
    assert record["known_set_money"]["net_sum"] == pytest.approx(0.02685)
    assert record["known_set_money"]["net_mean_per_known_book_date"] == pytest.approx(0.013425)
    assert record["unknown_book_ledger"]["member_rows_excluded"] == 2
    assert record["unknown_book_ledger"]["member_rows_with_known_net_on_excluded_books"] == 1
    assert record["unknown_book_ledger"]["member_net_on_excluded_books"] is None
    assert record["closed_pair_money_by_size"]["units"] == 1
    assert record["closed_pair_money_by_size"]["partition_identity"]["net_sum"] == pytest.approx(
        0.02685
    )


@pytest.mark.parametrize("buy_part,sell_part", [(None, None), (0.005, None)])
def test_executed_missing_volume_is_not_no_trade_or_known_low_participation(buy_part, sell_part):
    record = _record(buy_part, sell_part)
    date_bands = {r["band"]: r for r in record["book_date_money_by_size"]["bands"]}
    pair_bands = {r["band"]: r for r in record["closed_pair_money_by_size"]["bands"]}
    assert date_bands["no_leg_executed"]["count"] == 1
    assert date_bands["missing_volume"]["count"] == 1
    assert date_bands["missing_volume"]["net_sum"] == pytest.approx(0.02685)
    assert pair_bands["missing_volume"]["count"] == 1
    assert pair_bands["le_1pct"]["count"] == 0


def test_an_open_buy_with_no_sell_vocabulary_keeps_exit_liquidity_unknown():
    fills = pl.DataFrame([_leg("unknown", "A", "buy", 600, 0.005)])
    pairs = capacity.attach_participation(execution.build_pairs(fills), fills)
    assert pairs["entry_participation"].item() == pytest.approx(0.005)
    assert pairs["exit_participation"].item() is None
    assert pairs["cycle_participation"].item() is None


def test_band_boundaries_and_invalid_liquidity_values_are_distinct():
    values = np.array([0.01, 0.05, 0.25, 1.0, np.nextafter(1.0, np.inf), np.nan, -0.01])
    assert capacity.band_codes(values).tolist() == [1, 2, 3, 4, 5, 0, 0]
