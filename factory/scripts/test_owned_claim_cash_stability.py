"""Financial concentration uses money, dates and resolved-book denominators."""

import owned_claim_cash_stability as stability
import polars as pl
import pytest


@pytest.fixture
def cell():
    key = {"clock": 540, "n": 3, "side": 0.005, "policy": "h3:cash", "cycles": "once"}
    daily = pl.DataFrame(
        {
            "day": ["2021-09-03", "2021-10-01", "2021-11-01", "2021-11-02"],
            "month": ["2021-09", "2021-10", "2021-11", "2021-11"],
            "fold": [0, 1, 2, 2],
            "ret": [0.03, -0.01, 0.001, None],
            "unknown": [False, False, False, True],
            "unresolved_fresh": [False, False, False, True],
            "affordability_unknown": [0, 0, 0, 2],
            "orders": [2, 2, 2, 0],
            "fresh_entries": [1, 1, 1, 0],
            "fees": [0.001, 0.001, 0.001, 0.5],
            "gross_pnl": [0.031, -0.009, 0.002, 0.5],
        }
    )
    return stability.analyse_cell(key, daily, 0, 17)[0]


def test_extreme_blocks_follow_money_not_chronological_labels(cell):
    concentration = cell["concentration"]
    assert concentration["top_fold_by_mass"]["fold"] == 0
    assert concentration["bottom_fold_by_mass"]["fold"] == 1
    assert concentration["top_month_by_mass"]["month"] == "2021-09"
    assert concentration["bottom_month_by_mass"]["month"] == "2021-10"
    assert concentration["top_month_by_mass"]["mass"] == pytest.approx(0.03)


def test_unknown_cashflows_do_not_enter_profit_fee_or_mean_denominators(cell):
    assert cell["known_book_dates"] == 3
    assert cell["unknown_date_list"] == ["2021-11-02"]
    assert cell["known_book_date_mean"] == pytest.approx(0.007)
    assert cell["fees_sum_known_book_dates"] == pytest.approx(0.003)
    assert cell["unknown_break_even"]["break_even_loss_per_unknown_date"] == pytest.approx(0.021)


def test_best_date_removal_uses_retained_book_dates_and_handles_exhaustion(cell):
    removal = cell["best_date_removal"]
    assert removal["best_1"]["removed_dates"] == [{"day": "2021-09-03", "ret": 0.03}]
    assert removal["best_1"]["retained_known_book_dates"] == 2
    assert removal["best_1"]["retained_book_date_mean"] == pytest.approx(-0.0045)
    assert removal["best_3"]["retained_known_book_dates"] == 0
    assert removal["best_3"]["retained_book_date_mean"] is None


def test_affordability_counts_book_dates_separately_from_failed_claims(cell):
    bounds = cell["unknown_break_even"]
    assert bounds["unknown_book_dates"] == 1
    assert bounds["unknown_books_with_affordability_gap"] == 1
    assert bounds["affordability_affected_claims"] == 2


def test_published_order_mean_is_compared_with_independent_known_date_candidates():
    key = {"clock": 540, "n": 3, "side": 0.005, "policy": "h3:cash", "cycles": "once"}
    daily = pl.DataFrame(
        [
            {**key, "ret": 0.02, "fees": 0.01, "orders": 2, "gross_pnl": 0.03},
            {**key, "ret": None, "fees": 0.5, "orders": 4, "gross_pnl": -0.8},
        ]
    )
    published = pl.DataFrame([{**key, "ev": 0.02, "fees": 0.01, "orders": 3.0, "gross_pnl": 0.03}])
    _, recovered = stability.recover_denominators(daily, published)
    assert recovered["orders"]["published_denominator_recovered"] == "all_date_mean"
    assert recovered["orders"]["max_abs_diff_vs_known_date_mean"] == pytest.approx(1.0)
    assert recovered["fees"]["published_denominator_recovered"] == "known_date_mean"
    assert recovered["gross_pnl"]["published_denominator_recovered"] == "known_date_mean"
