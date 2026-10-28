"""Behavioral tests for the cross-name leader-rotation producer's decision physics.

These cover the edges the peer-relative rotation formulation actually turns on and that
no sibling producer asserts today:

* an IOC LIMIT entry is priced ONCE at the arrival clock and NEVER rests to a later
  eligible print (invalid / non-regular / crossed / stale-but-valid arrival is an
  execution UNKNOWN, ``ask > limit`` is a modeled no-L1-match unfilled cash, partial
  depth is an UNKNOWN with conservative reservation) - in deliberate contrast to the
  genuine MARKET exit, whose resting uses the REAL returned price clock (the arrival
  when fresh, the first future eligible print's own timestamp when the order already
  rested), so cash, slot and cooldown never anchor to a lookahead clock;
* the twelve peer-relative features are computed over the OTHER watchlist rows of the
  SAME past snapshot only: the subject is always excluded from its own statistics, a
  single-name snapshot is exactly zero (never NaN, never the subject's own value), and
  rows of a different clock are neither peers nor future information;
* the touch-only provider baseline enters on the observable book with no learned score,
  and its headline known contribution is the PARTIAL basis (an unknown execution is
  never charged as a full-EV loss);
* peak reserved cash and positions are measured per day from each day's own funding /
  release events, so a multi-day aggregate can never silently report only the last day.

Synthetic frames only: no data file, no network, no live order.

Run:
  uv run --no-sync python -m pytest tests/test_alpha_cross_name_rotation.py -q
"""

from __future__ import annotations

import math
import sys

import numpy as np
import polars as pl
import pytest

sys.path.insert(0, ".")
sys.path.insert(0, "factory/scripts")

import alpha_cross_name_rotation as acr  # noqa: E402
from alpha_quote_audit import clock_us  # noqa: E402

DAY = "2023-05-15"
DAY2 = "2023-05-16"
SESSION_END = 959


def _minutes_us(day: str, minute: int, second_us: int = 0) -> int:
    return clock_us(day, minute, 0) + int(second_us)


def _frame(rows, ticker: str, day: str) -> pl.DataFrame:
    """Raw quote rows: (minute, second_us, bid, ask, bid_size, ask_size, conditions)."""
    stamps, bid, ask, bsz, asz, conds = [], [], [], [], [], []
    for minute, second_us, b, a, bs, ash, c in rows:
        stamps.append(_minutes_us(day, minute) + int(second_us))
        bid.append(b)
        ask.append(a)
        bsz.append(bs)
        asz.append(ash)
        conds.append(list(c))
    return pl.DataFrame(
        {
            "symbol": [ticker] * len(rows),
            "ts_utc": pl.Series(stamps, dtype=pl.Datetime("us", "UTC")),
            "bid_price": bid,
            "bid_size": bsz,
            "ask_price": ask,
            "ask_size": asz,
            "bid_exchange": [None] * len(rows),
            "ask_exchange": [None] * len(rows),
            "conditions": conds,
            "tape": [None] * len(rows),
        }
    ).sort("ts_utc")


def _stream(rows, ticker="TEST", day=DAY):
    return acr.symbol_quotes(_frame(rows, ticker, day), day)[ticker]


def _dense_quotes(ticker, day, lo, hi, *, bid=10.0, ask=10.02, size=100_000.0, step_us=500_000):
    """One fresh R print every ``step_us`` across [lo, hi] minutes (firm fresh book)."""
    rows = []
    for minute in range(lo, hi + 1):
        us = 0
        while us < 60_000_000:
            rows.append((minute, us, bid, ask, size, size, ("R",)))
            us += step_us
    return _stream(rows, ticker, day)


def _panel(rows):
    """Synthetic watchlist rows: (ticker, t, ret3, ret15, gain, age, rank_snap, log_cum_dv)."""
    return pl.DataFrame(
        {
            "day": [DAY] * len(rows),
            "ticker": [r[0] for r in rows],
            "t": [r[1] for r in rows],
            "ret3": [r[2] for r in rows],
            "ret15": [r[3] for r in rows],
            "gain_open": [r[4] for r in rows],
            "admission_age": [r[5] for r in rows],
            "rank_snapshot": [r[6] for r in rows],
            "log_cum_dv": [r[7] for r in rows],
        }
    )


def _state_row(
    ticker,
    t,
    *,
    pred=0.05,
    rule=True,
    touch_rule=True,
    ask=10.0,
    spread_bps=20.0,
    depth=100_000.0,
    session_end=SESSION_END,
):
    minute = t + 1
    limit = acr.limit_price_of(ask)
    qty = acr.causal_quantity(limit, acr.FEE_CAP_BPS)
    row = {
        "day": DAY,
        "ticker": ticker,
        "t": t,
        "entry_minute": minute,
        "intent_us": _minutes_us(DAY, minute),
        "session_end": session_end,
        "intent_status": "quoted",
        "intent_ask": ask,
        "intent_bid": ask * 0.998,
        "intent_age_s": 0.1,
        "intent_spread_bps": spread_bps,
        "intent_ask_shares": depth,
        "limit_price": limit,
        "qty": qty,
        "reserved_usd": acr.reserved_usd(qty, limit, acr.RESERVE_BPS),
        "pred_ridge_h15": pred,
        "pred_lgbm_reg_h15": pred,
        "pred_lgbm_rank_h15": pred,
        "pred_ridge_h60": pred,
        "pred_lgbm_reg_h60": pred,
        "pred_lgbm_rank_h60": pred,
        "rule_ridge_h15": rule,
        "rule_lgbm_reg_h15": rule,
        "rule_lgbm_rank_h15": rule,
        "rule_ridge_h60": rule,
        "rule_lgbm_reg_h60": rule,
        "rule_lgbm_rank_h60": rule,
        "rule_touch_only": touch_rule,
        "y_touch_h15": None,
        "y_touch_h60": None,
        "label_known_h15": False,
        "label_known_h60": False,
    }
    return {c: row[c] for c in acr.STATE_COLUMNS}


def _states_frame(rows):
    return pl.DataFrame(rows, schema=acr.STATE_TYPES)


# ----- the entry IOC pricer: once at arrival, never rests ----------------------
def test_ioc_entry_unknown_arrival_stays_reserved_and_never_fills():
    t = 600
    rows = [
        (t + 1, 0, 10.0, 10.02, 100_000, 100_000, ("R",)),
        (t + 1, 100_000, 10.5, 10.0, 100_000, 100_000, ("R",)),
        (t + 1, 400_000, 10.0, 10.02, 100_000, 100_000, ("R",)),
    ]
    states = _states_frame([_state_row("AAA", t)])
    view = acr.View("ridge", 60, 0.0)
    daily, trades = acr.replay_day(DAY, states, {"AAA": _stream(rows)}, view)
    assert trades[0]["fill_status"] == "unknown_entry_execution"
    assert trades[0]["entry_ask"] is None  # never the later, more favourable print
    assert trades[0]["actual_exit_us"] == _minutes_us(DAY, SESSION_END) + 1
    assert trades[0]["net_usd_25"] is None
    assert daily["unknown_fills"] == 1
    assert daily["known_fills"] == 0 and daily["known_usd_25"] == 0.0
    # the unknown exposure keeps its reserved cash for the whole session
    assert daily["peak_reserved_usd"] == pytest.approx(trades[0]["reserved_usd"])


def test_ioc_entry_no_match_is_unfilled_cash_released_at_arrival():
    t = 600
    rows = [
        (t + 1, 0, 10.0, 10.02, 100_000, 100_000, ("R",)),  # intent: ask 10.02 -> limit 10.13
        (t + 1, 250_000, 10.0, 10.20, 100_000, 100_000, ("R",)),  # arrival: ask > limit
    ]
    states = _states_frame([_state_row("AAA", t)])
    view = acr.View("ridge", 60, 0.0)
    daily, trades = acr.replay_day(DAY, states, {"AAA": _stream(rows)}, view)
    assert trades[0]["entry_status"] == "no_match_at_l1"
    assert trades[0]["fill_status"] == "no_match_at_l1_unfilled_cash"
    assert trades[0]["actual_exit_us"] == _minutes_us(DAY, t + 1, 250_000)
    assert daily["no_match_fills"] == 1
    assert daily["fills"] == 0 and daily["unknown_fills"] == 0


def test_intent_facts_sizes_with_the_teachers_150bps_fee_cap():
    sq = _stream([(601, 0, 10.0, 10.02, 100_000, 100_000, ("R",))])
    facts = acr.intent_facts(sq, _minutes_us(DAY, 601))
    limit = acr.limit_price_of(10.02)  # ceil_to_cent(10.02 * 1.01)
    assert facts["qty"] == int(250 // (limit * (1.0 + 150 / 20_000)))
    assert facts["reserved_usd"] == pytest.approx(facts["qty"] * limit * (1.0 + 150 / 20_000))
    assert acr.causal_quantity(300.0) == 0  # min-capital is a KNOWN no order, not an attempt


def test_primary_clearance_is_the_touch_payoff_floor_never_the_spread_again():
    """The touch forecast already nets the spread, so the gate never re-charges it."""
    # A very WIDE observed spread (500bps) must not decide the primary gate: the predicted
    # touch payoff (ask entry -> bid exit) already paid it inside the target.
    sq = _stream([(601, 0, 10.0, 10.5, 100_000, 100_000, ("R",))])
    facts = acr.intent_facts(sq, _minutes_us(DAY, 601))
    assert facts["intent_spread_bps"] == pytest.approx(500.0, abs=1e-9)
    assert acr.touch_clearance_status(facts, 0.05)[1] is True  # 500bps paid inside the pred
    assert acr.touch_clearance_status(facts, 0.003)[1] is True  # 30bps >= the 25bps floor
    reason, ok = acr.touch_clearance_status(facts, 0.002)
    assert ok is False and reason == "touch_payoff_below_clearance"  # 20bps < 25bps
    assert acr.touch_clearance_status(facts, 0.0025)[1] is True  # exactly the floor


# ----- the market exit: resting keeps the REAL price clock ---------------------
def test_market_exit_rests_to_the_real_price_clock_not_the_arrival():
    """The engine's discarded third return value (the actual price clock) is preserved."""
    t = 600
    head = 60
    exit_minute = t + 1 + head  # 661
    rows = [
        (t + 1, 0, 10.0, 10.02, 100_000, 100_000, ("R",)),  # entry intent: firm/fresh
        (t + 1, 250_000, 10.0, 10.02, 100_000, 100_000, ("R",)),  # entry arrival: fill
        (exit_minute, 0, 10.5, 10.0, 100_000, 100_000, ("R",)),  # crossed at the due intent
        (exit_minute, 400_000, 10.0, 10.02, 100_000, 100_000, ("R",)),  # eligible -> submit
        (exit_minute, 600_000, 10.5, 10.0, 100_000, 100_000, ("R",)),  # crossed at arrival
        (exit_minute, 900_000, 10.5, 10.0, 100_000, 100_000, ("R",)),  # still crossed
        (exit_minute, 1_200_000, 9.98, 10.0, 100_000, 100_000, ("R",)),  # first eligible
    ]
    states = _states_frame([_state_row("AAA", t)])
    view = acr.View("ridge", head, 0.0)
    daily, trades = acr.replay_day(DAY, states, {"AAA": _stream(rows)}, view)
    trade = trades[0]
    submit_at = _minutes_us(DAY, exit_minute, 400_000)
    rest_price_clock = _minutes_us(DAY, exit_minute, 1_200_000)
    assert trade["exit_submit_us"] == submit_at  # held past the crossed due intent
    assert trade["exit_arrival_us"] == submit_at + 250_000
    assert trade["exit_price_status"] == "priced_rested_until_regular"
    assert trade["actual_exit_us"] == rest_price_clock  # the REAL execution clock
    assert trade["actual_exit_us"] != trade["exit_arrival_us"]  # never the lookahead clock
    assert trade["exit_quote_us"] == rest_price_clock
    assert trade["exit_bid"] == 9.98
    assert trade["fill_status"] == "conditional_fill"
    assert trade["net_usd_25"] == pytest.approx(
        trade["qty"] * (9.98 * (1 - 25 / 20_000) - 10.02 * (1 + 25 / 20_000))
    )
    assert daily["known_fills"] == 1


def test_market_exit_stale_but_valid_arrival_is_unknown_never_a_later_print():
    t = 600
    head = 60
    exit_minute = t + 1 + head
    rows = [
        (t + 1, 0, 10.0, 10.02, 100_000, 100_000, ("R",)),
        (t + 1, 250_000, 10.0, 10.02, 100_000, 100_000, ("R",)),
        (exit_minute - 1, 58_100_000, 10.0, 10.02, 100_000, 100_000, ("R",)),  # 1.9s old
        (exit_minute, 500_000, 10.0, 11.00, 100_000, 100_000, ("R",)),  # later + favourable
    ]
    states = _states_frame([_state_row("AAA", t)])
    view = acr.View("ridge", head, 0.0)
    daily, trades = acr.replay_day(DAY, states, {"AAA": _stream(rows)}, view)
    trade = trades[0]
    assert trade["exit_price_status"] == "unknown_stale_but_valid_at_arrival"
    assert trade["fill_status"] == "unknown_exit_execution"
    assert trade["exit_bid"] is None  # the later favourable print is never used
    assert trade["actual_exit_us"] == _minutes_us(DAY, SESSION_END) + 1
    assert trade["net_usd_25"] is None
    assert daily["unknown_fills"] == 1 and daily["known_usd_25"] == 0.0


# ----- the twelve peer-relative features --------------------------------------
def test_peer_features_exclude_own_row_and_isolate_the_snapshot():
    rows = [
        ("AAA", 600, 0.10, 0.20, 0.10, 5.0, 2.0, math.log1p(2000.0)),
        ("BBB", 600, 0.02, 0.05, 0.02, 15.0, 6.0, math.log1p(1000.0)),
        ("CCC", 600, 0.04, 0.08, 0.04, 10.0, 4.0, math.log1p(500.0)),
        # the same ticker at a DIFFERENT clock: neither a peer nor future information
        ("AAA", 605, 0.50, 0.50, 0.50, 5.0, 2.0, math.log1p(2000.0)),
        ("BBB", 605, -0.30, -0.30, -0.30, 15.0, 6.0, math.log1p(1000.0)),
    ]
    out = {(r["ticker"], r["t"]): r for r in acr.peer_features(_panel(rows)).to_dicts()}
    aaa = out[("AAA", 600)]
    bbb = out[("BBB", 600)]
    ccc = out[("CCC", 600)]
    # relative returns versus the OTHER names' snapshot means (own excluded)
    assert aaa["rel_ret3"] == pytest.approx(0.10 - (0.02 + 0.04) / 2)
    assert bbb["rel_ret3"] == pytest.approx(0.02 - (0.10 + 0.04) / 2)
    assert ccc["rel_ret3"] == pytest.approx(0.04 - (0.10 + 0.02) / 2)
    assert aaa["rel_ret15"] == pytest.approx(0.20 - (0.05 + 0.08) / 2)
    assert aaa["rel_gain"] == pytest.approx(0.10 - (0.02 + 0.04) / 2)
    # strict rank percentiles: the fraction of OTHERS strictly below the subject
    assert aaa["ret3_pctile"] == pytest.approx(1.0)
    assert ccc["ret3_pctile"] == pytest.approx(0.5)
    assert bbb["ret3_pctile"] == pytest.approx(0.0)
    assert aaa["ret15_pctile"] == pytest.approx(1.0)
    # breadth over the OTHERS only
    assert aaa["peer_pos3_share"] == pytest.approx(1.0)
    assert bbb["peer_pos3_share"] == pytest.approx(1.0)
    # dispersion of the OTHERS (population std)
    assert aaa["peer_ret3_std"] == pytest.approx(0.01)
    assert aaa["peer_gain_std"] == pytest.approx(0.01)
    # ownership of causal dollar volume and relative arrival age
    assert aaa["vol_ownership"] == pytest.approx(2000.0 / 3500.0)
    assert bbb["vol_ownership"] == pytest.approx(1000.0 / 3500.0)
    assert aaa["arrival_age_rel"] == pytest.approx(5.0 - (15.0 + 10.0) / 2)
    # dominance change: 1 = strongest, matching rank_snapshot's own convention
    assert aaa["rank_delta"] == pytest.approx(1.0 - 2.0)
    assert ccc["rank_delta"] == pytest.approx(2.0 - 4.0)
    assert bbb["rank_delta"] == pytest.approx(3.0 - 6.0)
    # gap to the 15-minute leader: 0 for the leader itself, negative otherwise
    assert aaa["lead_gap15"] == pytest.approx(0.0)
    assert bbb["lead_gap15"] == pytest.approx(0.05 - 0.20)
    # the later-clock rows carry their OWN snapshot features, never this snapshot's
    assert out[("AAA", 605)]["rel_ret3"] == pytest.approx(0.50 - (-0.30))


def test_single_name_snapshot_peer_features_are_exactly_zero():
    (row,) = acr.peer_features(
        _panel([("AAA", 600, 0.10, 0.20, 0.10, 5.0, 3.0, math.log1p(2000.0))])
    ).to_dicts()
    for name in (
        "rel_ret3",
        "rel_ret15",
        "rel_gain",
        "ret3_pctile",
        "ret15_pctile",
        "peer_pos3_share",
        "peer_ret3_std",
        "peer_gain_std",
        "arrival_age_rel",
        "lead_gap15",
    ):
        assert row[name] == 0.0
    assert row["vol_ownership"] == 1.0  # its own share of its own volume
    assert row["rank_delta"] == pytest.approx(1.0 - 3.0)  # snapshot dominance, no peers needed


def test_peer_features_are_finite_on_a_degenerate_tied_snapshot():
    rows = [
        ("AAA", 600, 0.01, 0.01, 0.01, 0.0, 1.0, 0.0),
        ("BBB", 600, 0.01, 0.01, 0.01, 0.0, 2.0, 0.0),
    ]
    for row in acr.peer_features(_panel(rows)).to_dicts():
        assert row["rel_ret3"] == pytest.approx(0.0)
        assert row["peer_ret3_std"] == pytest.approx(0.0)
        assert row["peer_pos3_share"] == pytest.approx(1.0)
        assert math.isfinite(row["vol_ownership"])


# ----- the touch-only provider baseline ---------------------------------------
def test_touch_only_baseline_enters_without_a_score_and_keeps_unknowns_out():
    # pred = 0 everywhere: the model view's PRIMARY touch-payoff clearance bar
    # (10_000 * pred >= 25) rejects every candidate, while the baseline enters on the
    # observable book alone.
    states = _states_frame([_state_row("AAA", 600, pred=0.0, rule=False)])
    streams = {"AAA": _dense_quotes("AAA", DAY, 600, 662)}
    model_view = acr.View("ridge", 60, 0.0)
    base_view = acr.View("touch_only", 60, 0.0)
    daily_model, trades_model = acr.replay_day(DAY, states, streams, model_view)
    daily_base, trades_base = acr.replay_day(DAY, states, streams, base_view)
    assert trades_model == []
    assert daily_model["touch_payoff_below_clearance"] == 1
    assert daily_model["known_usd_25"] == 0.0
    assert len(trades_base) == 1
    assert trades_base[0]["fill_status"] == "conditional_fill"
    assert trades_base[0]["score"] == 0.0  # no learned score was used
    # PARTIAL basis: the baseline headline is the conditional fill's net at the primary
    # rung, and no unknown execution is charged as a full-EV loss.
    assert daily_base["known_usd_25"] == pytest.approx(trades_base[0]["net_usd_25"])
    assert daily_base["known_fills"] == 1
    assert (
        daily_base["lower_bound_usd_25"]
        >= daily_base["known_usd_25"] - daily_base["unknown_reserved_usd"] - 1e-9
    )


def test_touch_only_baseline_uses_deterministic_tie_breaking():
    """No score to rank by: simultaneous-clock candidates are funded in ticker order."""
    states = _states_frame([_state_row("CCC", 600), _state_row("AAA", 600), _state_row("BBB", 600)])
    streams = {
        "AAA": _dense_quotes("AAA", DAY, 600, 602),
        "BBB": _dense_quotes("BBB", DAY, 600, 602),
        "CCC": _dense_quotes("CCC", DAY, 600, 602),
    }
    daily, trades = acr.replay_day(DAY, states, streams, acr.View("touch_only", 60, 0.0))
    assert [t["ticker"] for t in trades] == ["AAA", "BBB", "CCC"]
    assert daily["attempts"] == 3
    assert daily["positions_peak"] == 3


# ----- per-day capital accounting (the multi-day defect regression) ------------
def test_capital_peak_is_measured_per_day_not_from_the_last_day():
    reserved_each = acr.reserved_usd(
        acr.causal_quantity(acr.limit_price_of(10.0), acr.FEE_CAP_BPS),
        acr.limit_price_of(10.0),
        acr.RESERVE_BPS,
    )
    view = acr.View("ridge", 60, 0.0)
    day1_states = _states_frame(
        [_state_row("AAA", 600), _state_row("BBB", 600), _state_row("CCC", 600)]
    )
    day1_states = day1_states.with_columns(pl.lit(DAY).alias("day"))
    day2_states = _states_frame([_state_row("DDD", 600)]).with_columns(pl.lit(DAY2).alias("day"))
    streams = {
        "AAA": _dense_quotes("AAA", DAY, 600, 602),
        "BBB": _dense_quotes("BBB", DAY, 600, 602),
        "CCC": _dense_quotes("CCC", DAY, 600, 602),
        "DDD": _dense_quotes("DDD", DAY2, 600, 602),
    }
    daily1, _ = acr.replay_day(DAY, day1_states, streams, view)
    daily2, _ = acr.replay_day(DAY2, day2_states, streams, view)
    assert daily1["positions_peak"] == 3
    assert daily1["peak_reserved_usd"] == pytest.approx(3 * reserved_each)
    assert daily2["positions_peak"] == 1
    assert daily2["peak_reserved_usd"] == pytest.approx(reserved_each)
    # A multi-day aggregate must carry day 1's true peak, never the last day's value.
    agg = pl.DataFrame([daily1, daily2], schema=acr.DAILY_TYPES)
    assert int(agg["positions_peak"].max()) == 3
    assert float(agg["peak_reserved_usd"].max()) == pytest.approx(3 * reserved_each)


# ----- the labels prerequisite floor ------------------------------------------
def test_missing_label_day_files_floor_fail_with_a_prerequisite_message(tmp_path):
    with pytest.raises(SystemExit, match="MISSING PREREQUISITE"):
        acr.features_block(
            ["2023-05-15"], tmp_path / "out", tmp_path / "no_labels", tmp_path, False, "x"
        )


def test_label_schema_mismatch_floors_with_the_missing_columns(tmp_path):
    days = tmp_path / "panel" / "days"
    days.mkdir(parents=True)
    labels = tmp_path / "labels"
    labels.mkdir(parents=True)
    pl.DataFrame({"day": ["2023-05-15"]}).write_parquet(labels / "2023-05-15.parquet")
    pl.DataFrame(
        {
            "day": ["2023-05-15"],
            "ticker": ["AAA"],
            "t": [600],
            "session_end": [959],
            "ret3": [0.01],
            "ret15": [0.02],
            "gain_open": [0.03],
            "admission_age": [5.0],
            "rank_snapshot": [1.0],
            "log_cum_dv": [14.0],
        }
    ).write_parquet(days / "2023-05-15.parquet")
    with pytest.raises(SystemExit, match="LABEL SCHEMA MISMATCH"):
        acr.features_block(["2023-05-15"], tmp_path / "out", labels, tmp_path / "panel", False, "x")


# ----- ranker calibration ------------------------------------------------------
def test_relevance_grades_span_the_clipped_target_range():
    grades = acr.relevance_grades(np.array([-0.5, 0.0, 1.0, -0.6, 1.4]))
    assert grades.tolist() == [0, 10, 31, 0, 31]
    assert grades.dtype == np.int32


def test_calibrate_maps_rank_scores_and_is_identity_without_calibration():
    assert acr.calibrate({"calibration": {"a": 2.0, "b": 0.01}}, 0.5) == pytest.approx(1.01)
    assert acr.calibrate({"calibration": None}, 0.5) == 0.5
