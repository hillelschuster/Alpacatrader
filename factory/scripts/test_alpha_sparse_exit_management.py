"""Paired exit-policy guards: fill-ack window, no level fills, halt waiting,
unknown never cash, frozen entry frequency, validation-only selection."""

import sys
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from alpha_quote_audit import clock_us
from alpha_sparse_exit_management import (
    ORDER_BUDGET,
    RESIDUALS,
    book_view,
    evaluate_trade,
    first_bid_trigger,
    paired_summary,
    proxy_net,
    resting_exit_quote,
    select_policy,
    stop_quality,
)
from alpha_sparse_quote_service import NO_ORDER_STATUS, QUOTE_COLS

DAY = "2023-03-01"
ENTRY_MIN, EXIT_MIN, SESSION_END = 600, 660, 959


def trade(gross: float = 0.02) -> dict:
    return {
        "day": DAY,
        "ticker": "XYZ",
        "entry_et": ENTRY_MIN,
        "exit_et": EXIT_MIN,
        "session_end": SESSION_END,
        "gross": gross,
        "net": proxy_net(gross, 100.0),
        "status": "known_open_proxy",
        "entry_open": 10.0,
        "exit_open_proxy": 10.2,
        "t": ENTRY_MIN,
        "cost_bps": 100.0,
        "order_budget": ORDER_BUDGET,
        "score": 0.04,
        "horizon": 60,
    }


def quote_frame(stamps, bids, asks, bids_sizes, asks_sizes=None, conditions=None):
    asks_sizes = bids_sizes if asks_sizes is None else asks_sizes
    conditions = conditions or [["R"] for _ in stamps]
    return (
        pl.DataFrame(
            {
                "symbol": ["XYZ"] * len(stamps),
                "ts_utc": pl.Series(stamps).cast(pl.Datetime("us", "UTC")),
                "bid_price": bids,
                "bid_size": bids_sizes,
                "ask_price": asks,
                "ask_size": asks_sizes,
                "bid_exchange": ["Q"] * len(stamps),
                "ask_exchange": ["Q"] * len(stamps),
                "conditions": conditions,
                "tape": ["A"] * len(stamps),
            }
        )
        .select(list(QUOTE_COLS))
        .sort("ts_utc")
    )


def entry_frame():
    """Entry ASK 10.00 then a pre-activation breach, an activation breach, and a gap."""
    t = clock_us(DAY, ENTRY_MIN, 250)
    act = t + 250_000
    trig = act + 100_000
    stamps = [t - 50_000, t, t + 100_000, act - 1, act + 100_000, trig + 250_000, trig + 400_000]
    bids = [9.99, 9.99, 8.80, 8.90, 9.00, 8.80, 9.50]
    asks = [10.00, 10.00, 8.81, 8.91, 9.01, 8.81, 9.51]
    sizes = [50] * 7
    return quote_frame(stamps, bids, asks, sizes), t, act, trig


def test_stop_ignores_the_fill_ack_window_and_prices_the_gap_not_the_level():
    frame, t, act, trig = entry_frame()
    rec = evaluate_trade(trade(), frame)
    assert rec["activation_us"] == t + 250_000
    assert rec["stop10_triggered"] is True
    assert rec["stop10_trigger_bid"] == pytest.approx(9.00)  # not the 8.90 pre-activation print
    assert rec["stop10_trigger_us"] == act + 100_000
    assert rec["stop10_exit_arrival_us"] == trig + 250_000
    assert rec["stop10_exit_bid"] == pytest.approx(8.80)  # BID at arrival, never the level
    assert rec["stop10_exit_vs_level_bps"] < 0  # stop-through gap loss
    assert rec["stop10_net_0"] == pytest.approx((8.80 * 100 - 10.00 * 100) / 1000.0)
    # A print exists exactly at arrival: the market exit prices at that BID.
    assert rec["stop10_exit_age_s"] == pytest.approx(0.0)
    assert rec["stop10_exit_us"] == trig + 250_000


def test_no_trigger_caps_holding_to_the_original_60m_target():
    frame, t, act, _ = entry_frame()
    quiet = quote_frame(
        [t - 50_000, t, clock_us(DAY, EXIT_MIN, 250)],
        [9.99, 9.99, 9.70],
        [10.00, 10.00, 9.71],
        [50, 50, 50],
    )
    rec = evaluate_trade(trade(), quiet)
    assert rec["stop10_triggered"] is False and rec["stop10_trigger_us"] is None
    assert rec["stop10_exit_arrival_us"] == rec["target_exit_us"]
    assert rec["stop10_exit_bid"] == pytest.approx(9.70)
    hold = evaluate_trade(trade(), quiet)["hold60m_exit_bid"]
    assert hold == pytest.approx(9.70)  # same clock, same price
    # A late dip after the target minute cannot retroactively trigger the stop.
    late = quote_frame(
        [t - 50_000, t, clock_us(DAY, EXIT_MIN, 250), clock_us(DAY, EXIT_MIN, 250) + 500_000],
        [9.99, 9.99, 9.70, 5.00],
        [10.00, 10.00, 9.71, 5.01],
        [50, 50, 50, 50],
    )
    assert evaluate_trade(trade(), late)["stop15_triggered"] is False


def test_nonregular_and_crossed_quotes_cannot_trigger_a_stop():
    t = clock_us(DAY, ENTRY_MIN, 250)
    act = t + 250_000
    stamps = [t, act + 50_000, act + 150_000]
    frame = quote_frame(
        stamps,
        [9.99, 8.00, 8.95],
        [10.00, 7.99, 8.96],
        [50, 50, 50],
        conditions=[["R"], ["A"], ["R", "R"]],
    )
    rec = evaluate_trade(trade(), frame)
    assert rec["stop10_trigger_us"] == act + 150_000  # crossed 8.00 print skipped
    assert rec["stop10_trigger_bid"] == pytest.approx(8.95)
    # first_bid_trigger on the same window agrees with the policy record.
    lo = int(clock_us(DAY, ENTRY_MIN, 250) + 250_000)
    us, bid = first_bid_trigger(frame, lo, rec["target_exit_us"], 10.0 * 0.90)
    assert (us, bid) == (act + 150_000, pytest.approx(8.95))


def test_halted_arrival_rests_until_the_first_subsequent_regular_quote():
    """A market exit whose arrival has no fresh print waits for the FIRST later
    regular quote; it never picks a more favorable one, and never goes past the
    session end."""
    t = clock_us(DAY, ENTRY_MIN, 250)
    target = clock_us(DAY, EXIT_MIN, 250)
    end_us = clock_us(DAY, SESSION_END, 0)
    resumed = quote_frame(
        [t - 50_000, t, target - 5_000_000, target + 3_000_000],
        [9.99, 9.99, 9.60, 9.55],
        [10.00, 10.00, 9.61, 9.56],
        [50, 50, 50, 50],
    )
    q, status, us, delay = resting_exit_quote(resumed, target, end_us)
    assert q is not None and status == "rested_until_regular_quote"
    assert us == target + 3_000_000 and delay == pytest.approx(3.0)
    rec = evaluate_trade(trade(), resumed)
    assert rec["hold60m_exit_status"] == "rested_until_regular_quote"
    assert rec["hold60m_exit_bid"] == pytest.approx(9.55)  # first subsequent, not best
    assert rec["hold60m_exit_delay_s"] == pytest.approx(3.0)
    rec15 = evaluate_trade(trade(), resumed)
    assert rec15["stop15_exit_bid"] == pytest.approx(9.55)  # capped 60m exit, same rule
    # Resumption only after the session end: UNKNOWN, never cash.
    past_end = quote_frame(
        [t - 50_000, t, target - 5_000_000, end_us + 1_000_000],
        [9.99, 9.99, 9.60, 9.40],
        [10.00, 10.00, 9.61, 9.41],
        [50, 50, 50, 50],
    )
    rec2 = evaluate_trade(trade(), past_end)
    assert rec2["hold60m_exit_status"] == "no_valid_regular_quote_before_end"
    assert rec2["hold60m_net_0"] is None
    assert rec2["hold60m_unknown_0"] == "unknown_exit_quote:no_valid_regular_quote_before_end"
    # An empty session tail cannot manufacture a price either.
    q2, status2, _, _ = resting_exit_quote(past_end, target, end_us)
    assert q2 is None and status2 == "no_valid_regular_quote_before_end"


def test_missing_entry_quote_is_unknown_on_every_policy():
    empty = quote_frame([], [], [], [])
    rec = evaluate_trade(trade(), empty)
    for p in ("hold60m", "stop10", "stop15"):
        assert rec[f"{p}_net_25"] is None
        assert rec[f"{p}_unknown_25"] == "unknown_quote_entry:quote_not_acquired"


def test_residual_ladder_is_fee_funded_and_l1_shallow_exits_stay_unknown():
    frame, *_ = entry_frame()
    rec = evaluate_trade(trade(), frame)
    qs = [rec[f"stop10_q_{int(r)}"] for r in RESIDUALS]
    assert qs == sorted(qs, reverse=True) and qs[0] == 100  # 1000 // 10.00
    assert rec["stop10_net_25"] < rec["stop10_net_0"]
    t = clock_us(DAY, ENTRY_MIN, 250)
    shallow = quote_frame(
        [t - 50_000, t, t + 350_000, t + 600_000],
        [9.99, 9.99, 9.00, 8.80],
        [10.00, 10.00, 9.01, 8.81],
        [50, 50, 50, 0],
    )  # exit print shows no L1 size
    rec2 = evaluate_trade(trade(), shallow)
    assert rec2["stop10_triggered"] is True
    assert rec2["stop10_net_0"] is None
    assert rec2["stop10_unknown_0"] == "unknown_l1_capacity_exit"
    assert rec2["stop10_rt_status_0"] == "unknown_l1_capacity"


def test_budget_below_one_share_is_known_no_order_on_every_policy(monkeypatch):
    """Future generic budget: a $250 unit against an ASK of 306.00 funds zero
    whole shares, so NO order is sent on any policy. The record is a known
    no-order (never a zero-return fill, never an UNKNOWN) and the frozen $1000
    unit is untouched by this mechanism."""
    monkeypatch.setattr("alpha_sparse_exit_management.ORDER_BUDGET", 250.0)
    t = clock_us(DAY, ENTRY_MIN, 250)
    frame = quote_frame(
        [t - 50_000, clock_us(DAY, EXIT_MIN, 250) - 50_000],
        [305.90, 305.95],
        [306.00, 306.10],
        [100, 100],
    )
    rec = evaluate_trade(trade(), frame)
    for p in ("hold60m", "stop10", "stop15"):
        for r in RESIDUALS:
            tag = int(r)
            assert rec[f"{p}_q_{tag}"] == 0
            assert rec[f"{p}_net_{tag}"] is None
            assert rec[f"{p}_unknown_{tag}"] is None
            assert rec[f"{p}_no_order_{tag}"] is True
            assert rec[f"{p}_rt_status_{tag}"] == NO_ORDER_STATUS


def test_frozen_1000_unit_records_are_never_no_order():
    """The actual cohort unit ($1,000 against ~$10 quotes) always funds whole
    shares, so the no-order class stays empty and no existing value moves."""
    t = clock_us(DAY, ENTRY_MIN, 250)
    x = clock_us(DAY, EXIT_MIN, 250)
    frame = quote_frame([t - 50_000, x - 50_000], [9.99, 9.70], [10.00, 9.71], [100, 100])
    rec = evaluate_trade(trade(), frame)
    for p in ("hold60m", "stop10", "stop15"):
        for r in RESIDUALS:
            assert rec[f"{p}_q_{int(r)}"] >= 1
            assert rec[f"{p}_no_order_{int(r)}"] is False
            assert rec[f"{p}_net_{int(r)}"] is not None


def test_book_view_charges_unknown_full_loss_over_the_whole_calendar():
    calendar = ["2023-03-01", "2023-03-02", "2023-03-03"]
    records = [
        {"day": "2023-03-01", "stop10_net_0": 0.01, "stop10_unknown_0": None},
        {"day": "2023-03-02", "stop10_net_0": None, "stop10_unknown_0": "unknown_l1_capacity_exit"},
    ]
    view = book_view(records, "stop10_net_0", calendar, unknown_key="stop10_unknown_0")
    assert view["days"] == 3 and view["known_fills"] == 1 and view["unknown_fills"] == 1
    assert view["traded_days"] == 2 and view["months"] == 1
    lb = [r["lower_bound_return"] for r in view["daily"]]
    assert lb[0] == pytest.approx(10.0 / 3000.0)  # +$10 on a $3,000 book
    assert lb[1] == pytest.approx(-1000.0 / 3000.0)  # UNKNOWN is never cash
    assert lb[2] == 0.0  # untouched calendar day
    assert view["mean_daily_lower_bound"] == pytest.approx(sum(lb) / 3.0)
    assert view["unknown_causes"] == {"unknown_l1_capacity_exit": 1}
    all_unknown = book_view(
        [
            {
                "day": "2023-03-01",
                "stop10_net_0": None,
                "stop10_unknown_0": "unknown_exit_quote:rested",
            }
        ],
        "stop10_net_0",
        calendar,
        unknown_key="stop10_unknown_0",
    )
    assert all_unknown["mean_net_known_fill"] is None
    assert all_unknown["unknown_fills"] == 1
    # One UNKNOWN fill charges -$1,000 on that day only; the other calendar days
    # stay zero, so the day mean is -1000/3000/3, never a whole-book zero.
    assert all_unknown["mean_daily_lower_bound"] == pytest.approx(-1.0 / 9.0)


def test_book_view_known_no_order_is_retained_cash_not_full_loss():
    """A known no-order (budget below one integer share) contributes 0 USD to the
    day lower bound because the cash was never deployed, while a true UNKNOWN at
    the same notional still charges the full unit loss."""
    calendar = ["2023-03-01", "2023-03-02", "2023-03-03"]
    records = [
        {
            "day": "2023-03-01",
            "stop10_net_0": 0.01,
            "stop10_unknown_0": None,
            "stop10_no_order_0": False,
        },
        {
            "day": "2023-03-02",
            "stop10_net_0": None,
            "stop10_unknown_0": None,
            "stop10_no_order_0": True,
        },
        {
            "day": "2023-03-03",
            "stop10_net_0": None,
            "stop10_unknown_0": "unknown_l1_capacity_exit",
            "stop10_no_order_0": False,
        },
    ]
    view = book_view(
        records,
        "stop10_net_0",
        calendar,
        unknown_key="stop10_unknown_0",
        no_order_key="stop10_no_order_0",
    )
    assert view["known_fills"] == 1
    assert view["known_no_order_fills"] == 1
    assert view["unknown_fills"] == 1
    assert view["unknown_causes"] == {"unknown_l1_capacity_exit": 1}
    lb = [r["lower_bound_return"] for r in view["daily"]]
    assert lb[0] == pytest.approx(10.0 / 3000.0)  # priced fill
    assert lb[1] == 0.0  # no-order: cash kept
    assert lb[2] == pytest.approx(-1000.0 / 3000.0)  # UNKNOWN still charged
    assert view["mean_daily_lower_bound"] == pytest.approx(sum(lb) / 3.0)
    # Without the explicit no_order_key the same record is indistinguishable from
    # an UNKNOWN: the key is what separates retained cash from a charged loss.
    blind = book_view(records, "stop10_net_0", calendar, unknown_key="stop10_unknown_0")
    assert blind["known_no_order_fills"] == 0 and blind["unknown_fills"] == 2


def test_base_proxy_net_matches_the_frozen_replay_math():
    assert proxy_net(0.0714285714285714, 100.0) == pytest.approx(0.06076759061833692)
    assert proxy_net(0.0714285714285714, 0.0) == pytest.approx(0.0714285714285714)
    assert proxy_net(-0.3030674846625767, 100.0) == pytest.approx(-0.31000213655648134)


def test_selection_uses_validation_only_with_deterministic_tie_break():
    views = {
        "hold60m": {"25": {"mean_daily_lower_bound": 0.002}},
        "stop10": {"25": {"mean_daily_lower_bound": 0.001}},
        "stop15": {"25": {"mean_daily_lower_bound": None}},
    }
    assert select_policy(views, 25.0)["chosen"] == "hold60m"
    views["stop10"]["25"]["mean_daily_lower_bound"] = 0.002  # exact tie
    assert select_policy(views, 25.0)["chosen"] == "hold60m"  # fixed order wins ties
    views["stop15"]["25"]["mean_daily_lower_bound"] = 0.003
    assert select_policy(views, 25.0)["chosen"] == "stop15"


def test_paired_summary_counts_unknown_transitions_not_zeros():
    records = [
        {"day": "2023-03-01", "hold60m_net_25": 0.02, "stop10_net_25": 0.05},
        {"day": "2023-03-01", "hold60m_net_25": 0.02, "stop10_net_25": 0.02},
        {"day": "2023-03-01", "hold60m_net_25": 0.02, "stop10_net_25": -0.03},
        {"day": "2023-03-01", "hold60m_net_25": 0.02, "stop10_net_25": None},
        {"day": "2023-03-01", "hold60m_net_25": 0.02, "stop10_net_25": 0.0},
    ]
    s = paired_summary(records, "stop10", "hold60m", 25.0)
    assert s["fills"] == 5 and s["paired_known_pairs"] == 4
    assert s["paired_classes"] == {"improved": 1, "unchanged": 1, "worsened": 2, "unknown_lost": 1}
    assert s["delta_max"] == pytest.approx(0.03)
    assert s["delta_min"] == pytest.approx(-0.05)
    both_unknown = paired_summary(
        [{"day": "d", "hold60m_net_25": None, "stop10_net_25": None}], "stop10", "hold60m", 25.0
    )
    assert both_unknown["paired_classes"] == {"unknown_both": 1}
    assert both_unknown["delta_mean"] is None


def test_stop_quality_reports_gap_through_and_win_to_loss_transitions():
    records = [
        {
            "stop10_triggered": True,
            "stop10_exit_vs_level_bps": -120.0,
            "stop10_net_0": -0.14,
            "hold60m_net_0": 0.02,
        },
        {
            "stop10_triggered": True,
            "stop10_exit_vs_level_bps": 5.0,
            "stop10_net_0": -0.09,
            "hold60m_net_0": -0.05,
        },
        {
            "stop10_triggered": False,
            "stop10_exit_vs_level_bps": None,
            "stop10_net_0": 0.01,
            "hold60m_net_0": 0.03,
        },
    ]
    q = stop_quality(records, "stop10", "hold60m", 0.0)
    assert q["stop_triggers"] == 2 and q["stop_through_gap_trades"] == 1
    assert q["stop_through_gap_worst_bps"] == pytest.approx(-120.0)
    assert q["stop_through_worst_net"] == pytest.approx(-0.14)
    assert q["win_to_loss_transitions"] == 1  # control win, stop loss
