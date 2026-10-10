"""Guards for the packet's headline horizon selection and the annual-EV CI lineage.

Two consumer-visible invariants are pinned here, both against TOY artifacts that are
written to a tmp dir and fed through the real builders:

1. bid_backed_burst's headline is the producer's CHOSEN horizon at the 100bps residual
   rung.  ``results[]`` is emitted h5 before h15, so a cost-only match silently returns
   the non-selected horizon.  The regression below rebuilds exactly that ordering and
   asserts the headline is the chosen row, not the first cost row -- and that it stays
   the chosen row when the list order is swapped.
2. ``annual_mean_ci_annualized_usd`` is DERIVED from the staged daily lower-bound
   bootstrap CI95 through 252 x 3000, so it tracks the artifact instead of a constant.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from alpha_search_report import (  # noqa: E402
    _annual_ev_facts,
    _chosen_horizon_seconds,
    _tick_strategies_in_progress,
    build_bid_backed_burst,
    render_table,
)

DAYS_PER_YEAR, BOOK_USD = 252, 3000.0
ORDER_USD = 1000.0
H5_100_FRACTION, H15_100_FRACTION = -0.008839906810393566, -0.009802612385888755


def _result(period, horizon, cost, fraction):
    return {
        "period": period,
        "period_role": period,
        "horizon_seconds": horizon,
        "cost_bps_residual": float(cost),
        "fills": 278 if period == "confirmation" else 321,
        "known_fills": 277 if period == "confirmation" else 320,
        "unknown_fills": 1,
        "attempts": 278 if period == "confirmation" else 321,
        "period_days": 187 if period == "confirmation" else 250,
        "months": 9 if period == "confirmation" else 12,
        "mean_net_known_fill": fraction / 2.0,
        "mean_daily_lower_bound": fraction,
        "daily_se": abs(fraction) / 10.0,
        "day_bootstrap_ci95": [fraction * 1.2, fraction * 0.8],
        "known_win_rate": 0.4,
        "known_profit_factor": 0.9,
        "worst_known_fill": fraction * 3.0,
        "coverage_unknown_days": 0,
        "coverage_worst_case_mean_daily": fraction,
        "known_complete_day_mean_lower_bound": fraction,
        "monthly_mean_lower_bound": {"2023-01": fraction},
        "order_usd": 250.0,
        "research_book": 750.0,
    }


def _summary(chosen=15, order=(5, 15)):
    results = [
        _result(period, horizon, cost, H5_100_FRACTION if horizon == 5 else H15_100_FRACTION)
        for period in ("development", "validation", "confirmation")
        for horizon in order
        for cost in (0, 25, 100, 150)
    ]
    s = {
        "contract": {
            "research_book": 750.0,
            "order_budget": 250.0,
            "cost_unit": "bps",
            "horizon_seconds": 15,
        },
        "results": results,
        "chosen_horizon_seconds": chosen,
        "selection": {
            "chosen_horizon_seconds": chosen,
            "chosen_validation_value_at_100": H15_100_FRACTION,
        },
        "verdict": {"status": "DIAGNOSTIC", "chosen_horizon_seconds": chosen},
        "chosen_horizon_by_cost": {
            "confirmation": {
                "100": {"horizon_seconds": chosen, "mean_daily_lower_bound": H15_100_FRACTION}
            },
        },
    }
    return s


def _build(tmp_path, summary):
    d = tmp_path / "bid_backed_burst"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "summary.json"
    p.write_text(json.dumps(summary))
    return build_bid_backed_burst(tmp_path, {"bid_backed_burst/summary.json": p})


def _conf_rows(block):
    return [r for r in block["rows"] if r["period_role"] == "confirmation"]


# --------------------------------------------------------------------------- #
# headline horizon selection
# --------------------------------------------------------------------------- #
def test_headline_is_the_chosen_horizon_not_the_first_cost_row(tmp_path):
    block = _build(tmp_path, _summary())
    h = block["headline"]

    # the chosen h15 confirmation cell at 100bps residual, converted ONCE from the fraction
    assert h["headline_value_pct"] == pytest.approx(round(H15_100_FRACTION * 100.0, 4))
    # and NOT the h5 row that comes first in results[]
    assert h["headline_value_pct"] != pytest.approx(round(H5_100_FRACTION * 100.0, 4))

    assert h["headline_horizon_seconds"] == 15
    assert h["headline_horizon_unit"] == "seconds"
    assert h["headline_row_key"] == "bid_backed_burst|confirmation|h15|100"
    assert "h15" in h["headline_metric"]
    assert h["n_known_fills"] == 277 and h["n_unknown_fills"] == 1 and h["n_days"] == 187
    assert h["all_known_negative"] is True


def test_headline_follows_the_selection_regardless_of_results_list_order(tmp_path):
    """Selector is (cost, chosen horizon), never the first matching position."""
    block = _build(tmp_path, _summary(chosen=5, order=(15, 5)))
    h = block["headline"]
    assert h["headline_horizon_seconds"] == 5
    assert h["headline_value_pct"] == pytest.approx(round(H5_100_FRACTION * 100.0, 4))
    assert h["headline_row_key"] == "bid_backed_burst|confirmation|h5|100"


def test_no_producer_selection_means_no_headline_row(tmp_path):
    """Without a frozen selection the packet reports absent instead of a default row."""
    s = _summary()
    for key in ("selection", "chosen_horizon_seconds", "verdict", "chosen_horizon_by_cost"):
        s.pop(key)
    assert _chosen_horizon_seconds(s) is None
    h = _build(tmp_path, s)["headline"]
    assert h["headline_value_pct"] is None
    assert h["headline_row_key"] is None
    assert h["headline_horizon_seconds"] is None
    assert "NO horizon selection" in h["headline_metric"]


def test_chosen_horizon_resolution_prefers_the_frozen_selection_field():
    s = _summary()
    assert _chosen_horizon_seconds(s) == 15
    fallback = {"chosen_horizon_by_cost": {"confirmation": {"100": {"horizon_seconds": 5}}}}
    assert _chosen_horizon_seconds(fallback) == 5
    assert _chosen_horizon_seconds({}) is None
    assert (
        _chosen_horizon_seconds(
            {
                "chosen_horizon_by_cost": {
                    "confirmation": {"100": {"horizon_seconds": 5}, "150": {"horizon_seconds": 15}}
                }
            }
        )
        is None
    )


# --------------------------------------------------------------------------- #
# horizon uniqueness / audit reproduction
# --------------------------------------------------------------------------- #
def test_row_keys_are_unique_and_carry_their_own_horizon(tmp_path):
    rows = _build(tmp_path, _summary())["rows"]
    keys = [r["row_key"] for r in rows]
    assert len(keys) == len(set(keys)) == 24
    for r in rows:
        assert f"|h{r['horizon']}|" in r["row_key"]
        assert f"h{r['horizon']}" in r["alternative"]
    conf = _conf_rows(_build(tmp_path, _summary()))
    assert len({r["cost_bps"] for r in conf}) == 4
    assert len(conf) == 8  # 4 rungs x 2 horizons, each with its own key


def test_headline_row_key_resolves_to_exactly_one_metric_row(tmp_path):
    block = _build(tmp_path, _summary())
    key = block["headline"]["headline_row_key"]
    hits = [r for r in block["rows"] if r["row_key"] == key]
    assert len(hits) == 1
    assert hits[0]["mean_daily_lower_bound_book_pct"] == pytest.approx(
        block["headline"]["headline_value_pct"]
    )
    for r in hits:
        assert r["horizon"] == block["headline"]["headline_horizon_seconds"]


def test_monthly_rows_and_human_render_are_horizon_unambiguous(tmp_path):
    block = _build(tmp_path, _summary())
    assert {m["horizon"] for m in block["monthly"]} == {5, 15}
    table = render_table(block["rows"], ["bid_backed_burst"])
    assert "bid_backed_burst_h5" in table and "bid_backed_burst_h15" in table


def test_underlying_numbers_are_unchanged_by_the_headline_fix(tmp_path):
    """Only WHICH cell is the headline changed; no value was recomputed or re-scaled."""
    rows = _build(tmp_path, _summary())["rows"]
    by_key = {
        (r["period_role"], r["horizon"], r["cost_bps"]): r["mean_daily_lower_bound_book_pct"]
        for r in rows
    }
    assert by_key[("confirmation", 5, 100.0)] == pytest.approx(round(H5_100_FRACTION * 100.0, 4))
    assert by_key[("confirmation", 15, 100.0)] == pytest.approx(round(H15_100_FRACTION * 100.0, 4))
    assert by_key[("validation", 15, 100.0)] == pytest.approx(round(H15_100_FRACTION * 100.0, 4))


def test_percent_field_is_never_converted_twice(tmp_path):
    s = _summary()
    for r in s["results"]:
        # the producer's own percent spelling must win over the fraction
        r["mean_daily_lower_bound_book_pct"] = r["mean_daily_lower_bound"] * 100.0
    rows = _build(tmp_path, s)["rows"]
    for r in rows:
        expected = H5_100_FRACTION if r["horizon"] == 5 else H15_100_FRACTION
        # the explicit _book_pct spelling is carried verbatim -- never multiplied again
        assert r["mean_daily_lower_bound_book_pct"] == pytest.approx(expected * 100.0)


# --------------------------------------------------------------------------- #
# annual-EV CI derivation
# --------------------------------------------------------------------------- #
def _rare_res(lo, hi, fills=143, days=332, net=0.009868940862863676):
    return {
        "confirmation_by_cost": {
            "100": {
                "fills": fills,
                "days": days,
                "known_fills": 143,
                "unknown_fills": 0,
                "mean_net_known_fill": net,
                "bootstrap_daily": {
                    "n_days": days,
                    "n_boot": 1000,
                    "mean": 0.0014,
                    "ci95_lo": lo,
                    "ci95_hi": hi,
                    "p_gt_zero": 0.66,
                },
            }
        }
    }


def _contract():
    return {"replay": {"order_budget": ORDER_USD}}


def test_annual_ci_band_is_derived_from_the_staged_bootstrap_fields(tmp_path):
    lo, hi = -0.004203690312619715, 0.0069737334473370174
    f = _annual_ev_facts(tmp_path, _rare_res(lo, hi), _contract(), {}, {})

    assert f["annual_mean_ci_annualized_usd"] == [
        round(lo * DAYS_PER_YEAR * BOOK_USD, 2),
        round(hi * DAYS_PER_YEAR * BOOK_USD, 2),
    ]
    assert f["annual_mean_ci_annualized_usd"] == [-3177.99, 5272.14]

    d = f["annual_mean_ci_derivation"]
    assert d["daily_lower_bound_ci95_lo_fraction"] == lo
    assert d["daily_lower_bound_ci95_hi_fraction"] == hi
    assert d["trading_days_per_year"] == DAYS_PER_YEAR
    assert d["research_book_usd"] == BOOK_USD
    assert f["annual_mean_ci_source"].startswith("DERIVED from the staged confirmation artifact")


def test_annual_ci_band_tracks_the_artifact_not_a_constant(tmp_path):
    """Divisibility: scale the artifact's daily CI and the band must move with it."""
    lo, hi = -0.004203690312619715, 0.0069737334473370174
    f = _annual_ev_facts(tmp_path, _rare_res(lo, hi), _contract(), {}, {})
    f2 = _annual_ev_facts(tmp_path, _rare_res(lo * 2.0, hi * 2.0), _contract(), {}, {})

    assert f2["annual_mean_ci_annualized_usd"] == [
        round(lo * 2 * DAYS_PER_YEAR * BOOK_USD, 2),
        round(hi * 2 * DAYS_PER_YEAR * BOOK_USD, 2),
    ]
    assert f2["annual_mean_ci_annualized_usd"][0] == pytest.approx(
        2 * f["annual_mean_ci_annualized_usd"][0]
    )
    assert f2["annual_mean_ci_annualized_usd"] != f["annual_mean_ci_annualized_usd"]


def test_annual_ci_band_divides_back_to_the_daily_fraction(tmp_path):
    lo, hi = -0.004203690312619715, 0.0069737334473370174
    band = _annual_ev_facts(tmp_path, _rare_res(lo, hi), _contract(), {}, {})[
        "annual_mean_ci_annualized_usd"
    ]
    for bound, fraction in zip(band, (lo, hi), strict=True):
        assert bound / DAYS_PER_YEAR / BOOK_USD == pytest.approx(fraction, abs=1e-7)


def test_annual_ci_band_never_fabricates_when_the_bootstrap_is_absent(tmp_path):
    res = _rare_res(None, None)
    res["confirmation_by_cost"]["100"]["bootstrap_daily"] = None
    f = _annual_ev_facts(tmp_path, res, _contract(), {}, {})
    assert f["annual_mean_ci_annualized_usd"] == [None, None]
    assert f["annual_mean_ci_derivation"]["daily_lower_bound_ci95_lo_fraction"] is None
    # the rest of the block still derives normally
    assert f["derived"]["annual_ev_usd_before_opex_and_tax"] == pytest.approx(1071.196)
    assert f["derived"]["fills_per_year"] == pytest.approx(108.5422)


def test_annual_ci_keeps_the_hist_mean_not_a_forward_interval_reading(tmp_path):
    f = _annual_ev_facts(tmp_path, _rare_res(-0.001, 0.002), _contract(), {}, {})
    assert "NOT a future prediction interval" in f["annual_mean_ci_interpretation"]
    assert "NOT a claim about any forward period" in f["annual_mean_ci_interpretation"]


# --------------------------------------------------------------------------- #
# packet scope marker
# --------------------------------------------------------------------------- #
def test_tick_strategies_are_marked_in_progress_and_never_required():
    entries = _tick_strategies_in_progress(Path("/nonexistent"))
    assert len(entries) == 4
    for e in entries:
        assert e["required_by_base_producer"] is False
        assert e["registered_in_base_ten"] is False
        assert e["staged_outputs_present"] is False  # no output staged anywhere yet
