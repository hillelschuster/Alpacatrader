#!/usr/bin/env python3
"""Behavioral chronology/account guards for alpha_liquidity_recovery.

Hermetic: synthetic streams/quote arrays only, no data files. These pin the edges the
producer deliberately does NOT leave to chance:
  * the intent is the NEXT SECOND after the signal second; limit/qty are fixed at the
    intent clock BEFORE the +250ms arrival;
  * an IOC entry evaluates the latest RAW state at arrival ONCE and never rests to a
    later or more favourable print (ask>limit -> modeled no-L1-match; partial/stale/
    invalid arrival -> UNKNOWN);
  * a MARKET exit prices the actual BID, rests only when the book is genuinely
    unavailable (invalid/nonregular) and never solely because a valid print is aged;
  * the hold views are SECONDS (300s/900s) bounded by the session-end clock;
  * the confirmation is edge-triggered to the first qualified second after the flow flip;
  * the book funds every intent of one clock BEFORE any arrival outcome (no same-clock
    substitution), max 5 attempts/ticker/day, 300s post-exit cooldown, UNKNOWN reserved.
"""

import json
import sys
from pathlib import Path

import numpy as np
import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import alpha_liquidity_recovery as alr  # noqa: E402
import alpha_micro_core  # noqa: E402
from alpha_micro_core import load_day  # noqa: E402
from alpha_quote_audit import clock_us  # noqa: E402

DAY = "2021-06-01"
SECOND = 1_000_000
SESSION_END_US = clock_us(DAY, 959, 0)


def q_stream(stamps, bid, ask, bs, az, valid=None):
    n = len(stamps)
    return {
        "ts": np.asarray(stamps, dtype=np.int64),
        "bid": np.asarray(bid, dtype=float),
        "ask": np.asarray(ask, dtype=float),
        "bs": np.asarray(bs, dtype=float),
        "az": np.asarray(az, dtype=float),
        "regular": np.ones(n, dtype=bool),
        "valid": np.ones(n, dtype=bool) if valid is None else np.asarray(valid, dtype=bool),
    }


def _g(minute: int) -> int:
    return clock_us(DAY, minute, 0)


# ---------------------------------------------------------------- entry chronology
def test_intent_is_the_next_second_with_limit_fixed_before_arrival():
    g = _g(600)
    q = q_stream(
        stamps=[g - 1_000_000, g, g + 1 * SECOND, g + 1 * SECOND + alr.LATENCY_US],
        bid=[9.98, 9.99, 10.08, 10.13],
        ask=[10.00, 10.01, 10.10, 10.15],
        bs=[9000.0, 9000.0, 9000.0, 9000.0],
        az=[9000.0, 9000.0, 9000.0, 9000.0],
    )
    entry = alr.entry_leg(q, g + alr.INTENT_NEXT_SECOND_US)
    # limit comes from the NEXT-SECOND intent quote (10.10 -> ceil(10.10*1.01)=10.21),
    # not from the signal-second quote
    assert entry["intent_status"] == "quoted"
    assert entry["limit_price"] == pytest.approx(alr.limit_price_of(10.10))  # 10.21
    # the fill is priced from the arrival print (10.15 <= limit), never the intent or signal
    assert entry["order_state"] == "filled"
    assert entry["entry_status"] == "conditional_ioc_fill"
    assert entry["entry_ask"] == pytest.approx(10.15)
    assert entry["entry_et"] == g + alr.INTENT_NEXT_SECOND_US + alr.LATENCY_US


def test_future_prints_cannot_change_the_entry_outcome():
    g = _g(600)
    base = q_stream(
        stamps=[g, g + 1 * SECOND, g + 1 * SECOND + alr.LATENCY_US],
        bid=[9.99, 10.09, 10.14],
        ask=[10.00, 10.10, 10.15],
        bs=[9000.0, 9000.0, 9000.0],
        az=[9000.0, 9000.0, 9000.0],
    )
    later = q_stream(
        stamps=list(base["ts"]) + [g + 5 * SECOND, g + 6 * SECOND],
        bid=[9.99, 10.09, 10.14, 1.00, 1.00],
        ask=[10.00, 10.10, 10.15, 1.01, 1.01],
        bs=[9000.0, 9000.0, 9000.0, 9000.0, 9000.0],
        az=[9000.0, 9000.0, 9000.0, 9000.0, 9000.0],
    )
    assert alr.entry_leg(later, g + alr.INTENT_NEXT_SECOND_US) == alr.entry_leg(
        base, g + alr.INTENT_NEXT_SECOND_US
    )


def test_ioc_entry_never_rests_to_a_later_cheaper_print():
    g = _g(600)
    intent_us = g + alr.INTENT_NEXT_SECOND_US
    # the intent print is 1.9s old at the intent clock (firm), 2.15s old at the arrival
    # clock (stale); a cheaper fresh print exists 3s later and must never be used.
    q = q_stream(
        stamps=[intent_us - int(1.9 * SECOND), intent_us + 3 * SECOND],
        bid=[10.09, 1.00],
        ask=[10.10, 1.01],
        bs=[9000.0, 9000.0],
        az=[9000.0, 9000.0],
    )
    entry = alr.entry_leg(q, intent_us)
    assert entry["intent_status"] == "quoted"
    assert entry["order_state"] == "unknown"
    assert entry["entry_status"] == "unknown_stale_or_invalid_at_arrival"


def test_ask_above_the_predeclared_limit_is_a_modeled_no_match():
    g = _g(600)
    q = q_stream(
        stamps=[g + 1 * SECOND, g + 1 * SECOND + alr.LATENCY_US],
        bid=[10.09, 10.49],
        ask=[10.10, 10.50],
        bs=[9000.0, 9000.0],
        az=[9000.0, 9000.0],
    )
    entry = alr.entry_leg(q, g + alr.INTENT_NEXT_SECOND_US)
    assert entry["order_state"] == "unfilled"
    assert entry["entry_status"] == "no_match_at_l1"
    # the arrival print is observed and reported, but no fill is booked at it
    assert entry["entry_ask"] == pytest.approx(10.50)


def test_partial_entry_depth_is_unknown_not_a_resize():
    g = _g(600)
    q = q_stream(
        stamps=[g + 1 * SECOND, g + 1 * SECOND + alr.LATENCY_US],
        bid=[10.09, 10.14],
        ask=[10.10, 10.15],
        bs=[9000.0, 9000.0],
        az=[9000.0, 5.0],  # displayed depth cannot cover qty
    )
    entry = alr.entry_leg(q, g + alr.INTENT_NEXT_SECOND_US)
    assert entry["order_state"] == "unknown"
    assert entry["entry_status"] == "unknown_entry_partial_depth"


def test_nonfresh_intent_quote_is_a_cash_skip_not_an_order():
    g = _g(600)
    q = q_stream(
        stamps=[g - 5 * SECOND, g + 1 * SECOND + alr.LATENCY_US],
        bid=[10.09, 10.14],
        ask=[10.10, 10.15],
        bs=[9000.0, 9000.0],
        az=[9000.0, 9000.0],
    )
    entry = alr.entry_leg(q, g + alr.INTENT_NEXT_SECOND_US)
    assert entry["order_state"] == "cash_skip"
    assert entry["intent_status"] == "intent_not_firm_fresh"


# ---------------------------------------------------------------- exit chronology
def test_exit_prices_the_actual_bid_at_the_due_clock():
    g = _g(600)
    q = q_stream(
        stamps=[
            g + 1 * SECOND,
            g + 1 * SECOND + alr.LATENCY_US,
            g + 1 * SECOND + alr.LATENCY_US + 300 * SECOND,
            g + 1 * SECOND + alr.LATENCY_US + 300 * SECOND + alr.LATENCY_US,
        ],
        bid=[10.09, 10.15, 10.25, 10.30],
        ask=[10.10, 10.20, 10.31, 10.36],
        bs=[9000.0, 9000.0, 9000.0, 9000.0],
        az=[9000.0, 9000.0, 9000.0, 9000.0],
    )
    entry = alr.entry_leg(q, g + alr.INTENT_NEXT_SECOND_US)
    session_end_us = clock_us(DAY, 959, 0)
    leg = alr.exit_leg(q, entry["entry_et"], 300, session_end_us, entry["qty"])
    assert leg["exit_due_300"] == entry["entry_et"] + 300 * SECOND
    assert leg["exit_status_300"] == "conditional_market_fill"
    assert leg["exit_bid_300"] == pytest.approx(10.30)
    assert leg["delayed_300"] is False


def test_aged_but_valid_exit_arrival_is_unknown_never_a_rest():
    g = _g(600)
    intent_us = g + alr.INTENT_NEXT_SECOND_US
    entry_arrival = intent_us + alr.LATENCY_US
    due = entry_arrival + 300 * SECOND
    # the exit ARRIVAL state is the same valid print, now 2.5s old; a fresh HIGHER bid
    # exists 6s after the due clock and must not be waited for.
    q = q_stream(
        stamps=[
            intent_us,
            entry_arrival,
            due - int(2.25 * SECOND),
            due + 6 * SECOND,
        ],
        bid=[10.09, 10.15, 10.24, 11.00],
        ask=[10.10, 10.20, 10.30, 11.10],
        bs=[9000.0, 9000.0, 9000.0, 9000.0],
        az=[9000.0, 9000.0, 9000.0, 9000.0],
    )
    entry = alr.entry_leg(q, intent_us)
    leg = alr.exit_leg(q, entry["entry_et"], 300, clock_us(DAY, 959, 0), entry["qty"])
    assert leg["exit_submit_status_300"] == "submit_at_due_intent_firm_regular"
    assert leg["exit_status_300"] == "unknown_stale_but_valid_at_exit_arrival"
    assert leg["exit_bid_300"] is None


def test_unavailable_exit_book_rests_to_the_first_valid_print():
    g = _g(600)
    q = q_stream(
        stamps=[
            g + 1 * SECOND,
            g + 1 * SECOND + alr.LATENCY_US,
            g + 1 * SECOND + alr.LATENCY_US + 300 * SECOND,
            g + 1 * SECOND + alr.LATENCY_US + 300 * SECOND + alr.LATENCY_US,
            g + 1 * SECOND + alr.LATENCY_US + 300 * SECOND + alr.LATENCY_US + 2 * SECOND,
        ],
        bid=[10.09, 10.15, 10.24, 99.0, 10.31],  # arrival print crossed -> invalid
        ask=[10.10, 10.20, 10.30, 10.25, 10.36],
        bs=[9000.0, 9000.0, 9000.0, 9000.0, 9000.0],
        az=[9000.0, 9000.0, 9000.0, 9000.0, 9000.0],
        valid=[True, True, True, False, True],
    )
    entry = alr.entry_leg(q, g + alr.INTENT_NEXT_SECOND_US)
    leg = alr.exit_leg(q, entry["entry_et"], 300, clock_us(DAY, 959, 0), entry["qty"])
    assert leg["exit_status_300"] == "conditional_market_fill"
    assert leg["exit_bid_300"] == pytest.approx(10.31)
    assert leg["delayed_300"] is True


def test_hold_views_are_seconds_bounded_by_session_end():
    g = _g(750)  # 12:30 ET
    intent_us = g + alr.INTENT_NEXT_SECOND_US
    entry_arrival = intent_us + alr.LATENCY_US
    session_end_us = clock_us(DAY, 720, 0)  # early close at 12:00
    q = q_stream(
        stamps=[session_end_us, intent_us, entry_arrival],  # ascending, as a quote stream is
        bid=[10.25, 10.09, 10.15],
        ask=[10.31, 10.10, 10.20],
        bs=[9000.0, 9000.0, 9000.0],
        az=[9000.0, 9000.0, 9000.0],
    )
    entry = alr.entry_leg(q, intent_us)
    leg = alr.exit_leg(q, entry["entry_et"], 900, session_end_us, entry["qty"])
    # 900s past the 12:30 arrival would run to ~12:45, past the 12:00 session end -> bounded
    assert leg["exit_due_900"] == session_end_us
    assert leg["exit_status_900"] == "conditional_market_fill"
    leg2 = alr.exit_leg(q, entry["entry_et"], 300, clock_us(DAY, 959, 0), entry["qty"])
    assert leg2["exit_due_300"] == entry["entry_et"] + 300 * SECOND
    assert leg["exit_due_900"] != leg2["exit_due_300"]  # seconds, never a minutes/seconds mix


# ---------------------------------------------------------------- family features
def _family_stream():
    """A moving-name day: flat near the open, then a 120s dip of -2.3% (index 240..360)."""
    start = _g(575)
    stamps = np.arange(start - 60 * SECOND, start + 480 * SECOND, SECOND)
    px = np.full(len(stamps), 10.75)
    px[:240] = 11.0  # seconds [-60, +179] relative to admission
    px[240:361] = np.linspace(11.0, 10.75, 121)  # the dip: -2.27% over 120 SECONDS
    px[:60] = 11.05  # pre-admission prints
    quotes = {
        "ts": stamps - 1,
        "bid": px - 0.01,
        "ask": px + 0.01,
        "bs": np.full(len(stamps), 2000.0),
        "az": np.full(len(stamps), 2000.0),
        "regular": np.ones(len(stamps), dtype=bool),
        "valid": np.ones(len(stamps), dtype=bool),
    }
    return {
        "admit_t": 575,
        "trades": {"ts": stamps, "px": px, "size": np.full(len(stamps), 1000.0)},
        "quotes": quotes,
    }


def test_family_grid_matches_state_frame_and_columns_are_past_only():
    stream = _family_stream()
    frame = alr.state_frame(DAY, "XYZ", stream, 959)
    cols = alr.family_columns(DAY, stream, 959, 11.0, frame["imbalance5s"].to_numpy())
    grid = cols.pop("signal_us")
    assert np.array_equal(grid, frame["signal_us"].to_numpy())
    # mutating trades/prints at/after a second must not move that second's features
    changed = _family_stream()
    t = changed["trades"]
    target = _g(578)
    for f in ("px", "size"):
        t[f][t["ts"] >= target] *= 3.0
    cols2 = alr.family_columns(DAY, changed, 959, 11.0, frame["imbalance5s"].to_numpy())
    row = int(np.flatnonzero(grid == target)[0])
    for k in cols:
        assert cols[k][row] == pytest.approx(cols2[k][row]) or (
            np.isinf(cols[k][row]) and np.isinf(cols2[k][row])
        ), k


def test_cumulative_dv_counts_only_earlier_prints():
    stream = _family_stream()
    frame = alr.state_frame(DAY, "XYZ", stream, 959)
    cols = alr.family_columns(DAY, stream, 959, 11.0, frame["imbalance5s"].to_numpy())
    g = _g(578)
    row = int(np.flatnonzero(cols["signal_us"] == g)[0])
    earlier = stream["trades"]["ts"] < g
    assert cols["cum_dv"][row] == pytest.approx(
        float((stream["trades"]["px"][earlier] * stream["trades"]["size"][earlier]).sum())
    )


def test_trailing_minima_exclude_invalid_quotes():
    start = _g(575)
    stamps = np.arange(start, start + 400 * SECOND, SECOND)
    px = np.full(len(stamps), 11.0)
    bs = np.full(len(stamps), 5000.0)
    az = np.full(len(stamps), 1000.0)
    valid = np.ones(len(stamps), dtype=bool)
    valid[10:400] = False  # only the first 10 seconds have a valid book
    trades = {"ts": stamps, "px": px, "size": np.full(len(stamps), 100.0)}
    q = q_stream(stamps - 1, px - 0.01, px + 0.01, bs, az, valid=valid)
    stream = {"admit_t": 575, "trades": trades, "quotes": q}
    frame = alr.state_frame(DAY, "XYZ", stream, 959)
    cols = alr.family_columns(DAY, stream, 959, 11.0, frame["imbalance5s"].to_numpy())
    g = _g(578)
    row = int(np.flatnonzero(cols["signal_us"] == g)[0])
    # the trailing-180s minimum over the VALID part is 5000; invalid quotes never lower it
    assert cols["bid_hist_min"][row] == pytest.approx(5000.0)


def test_first_after_flip_is_edge_triggered_per_episode():
    flip = np.array([True, True, True, False, True, True])
    qualified = np.array([True, True, True, False, True, False])
    out = alr.first_after_flip(flip, qualified)
    assert out.tolist() == [True, False, False, False, True, False]
    # a second qualified only AFTER the flip turned is not selectable
    early = np.array([False, False])
    assert alr.first_after_flip(np.array([False, True]), early).tolist() == [False, False]


def test_setup_and_confirmation_masks():
    day_gain = 0.11
    frame = pl.DataFrame(
        {
            "px": [10.0, 10.0, 10.0],
            "cum_dv": [11_000_000.0, 11_000_000.0, 11_000_000.0],
            "day_gain": [day_gain, 0.05, day_gain],
            "fresh": [True, True, True],
            "spread": [0.002, 0.002, 0.004],
            "ret180s": [-0.03, -0.03, -0.03],
            "ret180s_valid": [True, True, True],
            "ret1s_valid": [True, True, True],
            "px_valid": [True, True, True],
            "bid_shares": [4000.0, 4000.0, 4000.0],
            "bid_hist_min": [1000.0, 1000.0, 1000.0],
            "epoch_index": [200, 200, 200],
            "ret1s": [0.001, 0.001, 0.001],
            "ask_shares": [1000.0, 1000.0, 1000.0],
            "imbalance5s": [0.2, 0.2, 0.2],
            "imbalance_hist_min": [-0.3, -0.3, -0.3],
        }
    )
    setup = frame.select(alr.setup_expr().alias("m"))["m"].to_numpy()
    assert setup.tolist() == [True, False, False]  # gain gate, then spread gate
    flip = alr.flow_flip(
        frame["imbalance5s"].to_numpy(), frame["imbalance_hist_min"].to_numpy()
    )
    assert flip.all()
    sel = alr.first_after_flip(
        flip, alr.variant_qualified("flip_first_positive_1s", flip, setup, frame)
    )
    assert sel.tolist() == [True, False, False]
    sel_b = alr.first_after_flip(
        flip, alr.variant_qualified("bid_depth_2x_ask", flip, setup, frame)
    )
    assert sel_b.tolist() == [True, False, False]


# ---------------------------------------------------------------- full-RTH grid
def _late_opportunity_stream(episodes, session_end=959, admit_t=575, base=11.0):
    """A full-session stream with dip+flow-flip episodes at the given start minutes.

    Every second has one firm quote and at least one eligible print; during a dip the
    prints are classified SELLs at the bid with a thin displayed bid, and the recovery
    burst is classified BUYs at the ask while the displayed bid replenishes. Prices stay
    at/above +10% over the 9.50 open, so the family gates can pass in the afternoon.
    """
    start = clock_us(DAY, admit_t, 0) - 60 * SECOND
    end_us = clock_us(DAY, session_end, 0) + SECOND  # a closing print may sit at the end
    secs = np.arange(start, end_us, SECOND)
    q_px = np.full(len(secs), base)
    kind = np.full(len(secs), "base")
    bs = np.full(len(secs), 2000.0)
    az = np.full(len(secs), 1000.0)
    dip, buy = 174, 7
    for minute in episodes:
        k0 = int(np.searchsorted(secs, clock_us(DAY, minute, 0)))
        for i in range(dip + buy):
            k = k0 + i
            if i < dip:
                q_px[k] = base - i * (base - 10.65) / dip
                kind[k] = "dip" if i < dip - 8 else "flat"
                bs[k] = 1500.0
            else:
                q_px[k] = 10.65 + (i - dip + 1) * 0.01
                kind[k] = "buy"
                bs[k] = 6000.0
    q = {
        "ts": secs,
        "bid": q_px - 0.01,
        "ask": q_px + 0.01,
        "bs": bs,
        "az": az,
        "regular": np.ones(len(secs), dtype=bool),
        "valid": np.ones(len(secs), dtype=bool),
    }
    t_ts: list[int] = []
    t_px: list[float] = []
    t_size: list[float] = []
    for k, s in enumerate(secs):
        if kind[k] == "dip":
            t_ts += [s + 100_000, s + 200_000]
            t_px += [q_px[k] - 0.015, q_px[k]]
            t_size += [1200.0, 1000.0]
        elif kind[k] == "buy":
            t_ts += [s + 100_000]
            t_px += [q_px[k] + 0.01]
            t_size += [1500.0]
        else:
            t_ts += [s + 100_000]
            t_px += [q_px[k]]
            t_size += [1000.0]
    return {
        "admit_t": admit_t,
        "trades": {
            "ts": np.array(t_ts, dtype=np.int64),
            "px": np.array(t_px),
            "size": np.array(t_size),
        },
        "quotes": q,
    }


def test_signal_grid_is_full_rth_maximal_with_inside_rth_arrival():
    stream = _late_opportunity_stream(())
    session_end_us = clock_us(DAY, 959, 0)
    grid = alr.signal_grid(DAY, stream, 959)
    assert grid[0] == clock_us(DAY, 575, 0)
    assert grid[-1] + alr.INTENT_NEXT_SECOND_US + alr.LATENCY_US <= session_end_us
    assert grid[-1] + alr.INTENT_NEXT_SECOND_US + alr.LATENCY_US + SECOND > session_end_us
    assert grid[-1] > clock_us(DAY, 900, 0)  # late afternoon, not a 13:00-style cutoff
    half = alr.signal_grid(DAY, stream, 720)
    assert half[-1] + alr.INTENT_NEXT_SECOND_US + alr.LATENCY_US <= clock_us(DAY, 720, 0)
    assert half[-1] < clock_us(DAY, 720, 0)


def test_state_frame_extends_past_the_core_framework_cap():
    stream = _late_opportunity_stream(())
    core = alr.states(DAY, "XYZ", stream, 959)
    frame = alr.state_frame(DAY, "XYZ", stream, 959)
    assert frame.height > core.height
    assert int(frame["signal_us"].max()) > int(core["signal_us"].max())
    head = frame.head(core.height)
    for name in ("px", "imbalance5s", "fresh", "bid_shares", "spread", "dv5s"):
        assert np.allclose(
            head[name].to_numpy(), core[name].to_numpy(), equal_nan=True
        ), name
    ext = frame["signal_us"].to_numpy()[core.height :]
    assert (np.diff(ext) == SECOND).all()
    assert ext[-1] <= clock_us(DAY, 959, 0)


def _afternoon_selection(stream, session_end, o570=9.5):
    frame = alr.state_frame(DAY, "XYZ", stream, session_end)
    cols = alr.family_columns(DAY, stream, session_end, o570, frame["imbalance5s"].to_numpy())
    frame = frame.with_columns([pl.Series(k, v) for k, v in cols.items()])
    setup = frame.select(alr.setup_expr().alias("m"))["m"].to_numpy()
    flip = alr.flow_flip(frame["imbalance5s"].to_numpy(), cols["imbalance_hist_min"])
    out = {}
    for variant in alr.CONFIRMATIONS:
        sel = alr.first_after_flip(flip, alr.variant_qualified(variant, flip, setup, frame))
        out[variant] = frame["signal_us"].to_numpy()[sel]
    return frame, out


def test_late_afternoon_opportunity_is_admitted_and_exits_inside_rth():
    stream = _late_opportunity_stream((865, 944))  # dip+flip at 14:25 and 15:44 ET
    session_end = 959
    session_end_us = clock_us(DAY, session_end, 0)
    old_cap = clock_us(DAY, 780, 0)  # the inherited 13:00 framework cutoff
    frame, selected = _afternoon_selection(stream, session_end)
    for variant, stamps in selected.items():
        assert len(stamps) >= 2, variant  # both afternoon episodes are discovered
        assert all(int(s) > old_cap for s in stamps), variant
        for g in stamps:
            for hold in alr.HOLD_SECONDS:
                rec = alr.resolve_intent(DAY, "XYZ", int(g), stream, session_end, hold)
                assert rec["order_state"] == "filled", (variant, hold)
                assert rec["entry_et"] <= session_end_us
                assert rec[f"exit_due_{hold}"] == min(
                    rec["entry_et"] + hold * SECOND, session_end_us
                )
                assert rec[f"exit_status_{hold}"] == "conditional_market_fill"
                assert rec[f"exit_us_{hold}"] <= session_end_us + alr.LATENCY_US
                assert rec[f"gross_{hold}"] is not None


def test_half_day_calendar_is_respected_for_late_signals():
    stream = _late_opportunity_stream((710,), session_end=720)  # dip+flip at 11:50 ET
    session_end = 720
    session_end_us = clock_us(DAY, session_end, 0)
    _, selected = _afternoon_selection(stream, session_end)
    for variant, stamps in selected.items():
        assert len(stamps) >= 1, variant
        for g in stamps:
            for hold in alr.HOLD_SECONDS:
                rec = alr.resolve_intent(DAY, "XYZ", int(g), stream, session_end, hold)
                assert rec["order_state"] == "filled", (variant, hold)
                assert rec["entry_et"] <= session_end_us
                # a 900s hold near the half-day close is capped at the last regular clock
                assert rec[f"exit_due_{hold}"] == min(
                    rec["entry_et"] + hold * SECOND, session_end_us
                )
                assert rec[f"exit_status_{hold}"] == "conditional_market_fill"
                assert rec[f"exit_us_{hold}"] <= session_end_us + alr.LATENCY_US


def test_no_signals_after_the_last_inside_rth_arrival_second():
    stream = _late_opportunity_stream((944,))
    session_end_us = clock_us(DAY, 959, 0)
    _, selected = _afternoon_selection(stream, 959)
    grid = alr.signal_grid(DAY, stream, 959)
    for stamps in selected.values():
        for g in stamps:
            assert int(g) <= grid[-1]
            assert int(g) + alr.INTENT_NEXT_SECOND_US + alr.LATENCY_US <= session_end_us


# ---------------------------------------------------------------- account replay
def _intent(
    ticker,
    clock,
    state="filled",
    gross=0.01,
    exit_us=None,
    score=0.5,
    session_end=SESSION_END_US,
    exit_bid=None,
):
    return {
        "day": DAY,
        "ticker": ticker,
        "signal_us": clock - alr.INTENT_NEXT_SECOND_US,
        "intent_us": clock,
        "score": score,
        "order_state": state,
        "entry_et": clock + alr.LATENCY_US,
        "entry_ask": 10.0,
        "session_end": session_end,
        "gross_300": gross,
        "exit_us_300": exit_us if exit_us is not None else clock + 300 * SECOND,
        "exit_bid_300": exit_bid if exit_bid is not None else 10.1,
        "variant": "flip_first_positive_1s",
    }


def test_same_clock_intents_are_funded_before_outcomes_no_fourth_substitution():
    clock = _g(600)
    intents = [
        _intent("AAA", clock, state="unfilled", gross=None, score=0.9),
        _intent("BBB", clock, score=0.8),
        _intent("CCC", clock, score=0.7),
        _intent("DDD", clock, score=0.6),
    ]
    metrics, trades = alr.account_replay(intents, [DAY], 300, 25.0)
    assert metrics["attempts"] == 3  # three slots; the fourth is never substituted in
    assert metrics["skip_counts"]["no_free_slot"] == 1
    unfilled = [t for t in trades if t["status"] == "unfilled_no_l1_match"]
    assert len(unfilled) == 1 and unfilled[0]["net"] is None
    # the unfilled slot is released for LATER clocks only
    intents2 = intents + [_intent("DDD", clock + SECOND, score=0.6)]
    metrics2, _ = alr.account_replay(intents2, [DAY], 300, 25.0)
    assert metrics2["attempts"] == 4


def test_cooldown_and_max_attempts_per_ticker_day():
    clock = _g(600)
    exit_at = clock + 300 * SECOND
    intents = [
        _intent("AAA", clock, exit_us=exit_at),
        _intent("AAA", exit_at + 299 * SECOND),  # inside the 300s cooldown -> skip
        _intent("AAA", exit_at + 300 * SECOND),  # exactly at cooldown end -> admitted
    ]
    metrics, trades = alr.account_replay(intents, [DAY], 300, 25.0)
    assert metrics["skip_counts"]["cooldown_300s"] == 1
    assert metrics["known_fills"] == 2
    # a sixth attempt on the same ticker/day is refused
    many = [
        _intent("BBB", clock + i * 700 * SECOND, exit_us=clock + i * 700 * SECOND + 300 * SECOND)
        for i in range(6)
    ]
    metrics2, _ = alr.account_replay(many, [DAY], 300, 25.0)
    assert metrics2["known_fills"] == alr.MAX_ATTEMPTS_PER_TICKER_DAY
    assert metrics2["skip_counts"].get("max_attempts_per_ticker_day", 0) == 1
    assert metrics2["skip_counts"].get("ticker_overlap", 0) == 0


def test_unknown_position_is_reserved_to_session_end():
    clock = _g(600)
    intents = [
        _intent("AAA", clock, state="unknown", gross=None),
        _intent("AAA", clock + 10 * SECOND, score=0.9),  # blocked: reserved slot
        _intent("BBB", clock + 20 * SECOND),
    ]
    metrics, trades = alr.account_replay(intents, [DAY], 300, 25.0)
    assert metrics["unknown_fills"] == 1
    assert metrics["skip_counts"]["ticker_overlap"] == 1
    row = next(d for d in metrics["daily"] if d["day"] == DAY)
    assert row["unknown"] == 1
    # the reserved slot stays blocked to the session end: the later AAA attempt never fires
    assert metrics["known_fills"] == 1


def test_cash_skip_intents_never_reserve_capital():
    clock = _g(600)
    intents = [_intent(f"T{i}", clock, state="cash_skip", gross=None) for i in range(4)]
    metrics, _ = alr.account_replay(intents, [DAY], 300, 25.0)
    assert metrics["attempts"] == 0
    assert metrics["skip_counts"]["intent_not_firm_fresh"] == 4
    row = next(d for d in metrics["daily"] if d["day"] == DAY)
    assert row["lower_bound_return"] == 0.0


def test_residual_cost_is_charged_on_top_of_the_touch():
    clock = _g(600)
    intents = [_intent("AAA", clock, gross=0.02)]
    _, trades = alr.account_replay(intents, [DAY], 300, 100.0)
    side = 100.0 / 20_000.0
    assert trades[0]["net"] == pytest.approx((1.02) * (1 - side) / (1 + side) - 1)
    _, trades0 = alr.account_replay(intents, [DAY], 300, 0.0)
    assert trades0[0]["net"] == pytest.approx(0.02)


# ---------------------------------------------------------------- universe watch
def _moving_rows(
    symbol, base=11.0, episodes=(865, 944), session_end=959, admit_t=575
):
    """Raw SIP trade/quote rows for one watch symbol, in the exact column layout
    ``alpha_micro_core.load_day`` reads.

    The shape mirrors the synthetic streams above (a liquid, moving name whose dip
    + aggressor-flow flip episodes sit at the given minutes), so the LQ family gates
    can pass on a full-RTH grid. Raw quote sizes are round lots before 2025-11-03;
    the loader multiplies them by 100. Conditions are R-only (quotes) and
    eligible non-auction prints (trades).
    """
    start = clock_us(DAY, admit_t, 0) - 60 * SECOND
    end_us = clock_us(DAY, session_end, 0) + SECOND
    secs = np.arange(start, end_us, SECOND)
    q_px = np.full(len(secs), base)
    kind = np.full(len(secs), "base")
    bs = np.full(len(secs), 2000.0)
    az = np.full(len(secs), 1000.0)
    dip, buy = 174, 7
    for minute in episodes:
        k0 = int(np.searchsorted(secs, clock_us(DAY, minute, 0)))
        for i in range(dip + buy):
            k = k0 + i
            if i < dip:
                q_px[k] = base - i * (base - 10.65) / dip
                kind[k] = "dip" if i < dip - 8 else "flat"
                bs[k] = 1500.0
            else:
                q_px[k] = 10.65 + (i - dip + 1) * 0.01
                kind[k] = "buy"
                bs[k] = 6000.0
    quotes, trades = [], []
    for k, s in enumerate(secs):
        ts = int(s)
        quotes.append(
            {
                "symbol": symbol,
                "ts_utc": ts,
                "bid_price": float(q_px[k] - 0.01),
                "ask_price": float(q_px[k] + 0.01),
                "bid_size": float(bs[k]) / 100.0,
                "ask_size": float(az[k]) / 100.0,
                "bid_exchange": "P",
                "ask_exchange": "P",
                "conditions": ["R"],
                "tape": "C",
            }
        )
        if kind[k] == "dip":
            trades.append(
                {
                    "symbol": symbol,
                    "ts_utc": ts + 100_000,
                    "price": float(q_px[k] - 0.015),
                    "size": 1200.0,
                    "conditions": ["@"],
                    "tape": "C",
                }
            )
            trades.append(
                {
                    "symbol": symbol,
                    "ts_utc": ts + 200_000,
                    "price": float(q_px[k]),
                    "size": 1000.0,
                    "conditions": ["@"],
                    "tape": "C",
                }
            )
        elif kind[k] == "buy":
            trades.append(
                {
                    "symbol": symbol,
                    "ts_utc": ts + 100_000,
                    "price": float(q_px[k] + 0.01),
                    "size": 1500.0,
                    "conditions": ["@"],
                    "tape": "C",
                }
            )
        else:
            trades.append(
                {
                    "symbol": symbol,
                    "ts_utc": ts + 100_000,
                    "price": float(q_px[k]),
                    "size": 1000.0,
                    "conditions": ["@"],
                    "tape": "C",
                }
            )
    return quotes, trades


def _write_universe_day(root: Path, day: str, rank4: str, missing: list[str]) -> None:
    """A SIP day declaring a top-ten watch where only ``rank4`` has raw rows.

    ``missing`` names further declared candidates whose raw streams are absent, so
    the coverage denominator has to count requested names, not covered ones.
    """
    top = [
        {"symbol": "TOP1", "score": 0.2, "px_575": 12.0, "px_575_et": 575},
        {"symbol": "TOP2", "score": 0.2, "px_575": 12.0, "px_575_et": 575},
        {"symbol": "TOP3", "score": 0.2, "px_575": 12.0, "px_575_et": 575},
        {"symbol": rank4, "score": 0.2, "px_575": 12.0, "px_575_et": 575},
    ] + [{"symbol": s, "score": 0.2, "px_575": 12.0, "px_575_et": 575} for s in missing]
    cp = root / "sip" / "candidates"
    cp.mkdir(parents=True, exist_ok=True)
    (cp / f"{day}.json").write_text(
        json.dumps({"snapshots": [{"T": 575, "pop": "B", "top": top}]})
    )
    quotes, trades = _moving_rows(rank4)
    (root / "sip" / "net" / "quotes").mkdir(parents=True, exist_ok=True)
    (root / "sip" / "net" / "trades").mkdir(parents=True, exist_ok=True)
    pl.DataFrame(quotes).write_parquet(root / "sip" / "net" / "quotes" / f"{day}.parquet")
    pl.DataFrame(trades).write_parquet(root / "sip" / "net" / "trades" / f"{day}.parquet")
    (root / "sip" / "universe" / "rth").mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"symbol": [rank4], "o570": [9.5]}).write_parquet(
        root / "sip" / "universe" / "rth" / f"{day}.parquet"
    )


def test_full_watch_is_the_full_top_ten_causal_universe(tmp_path):
    """full_watch: top TEN ranks, $1 floor, score floor, and a snapshot price that
    is fresh within 2 minutes but never stamped ahead of the snapshot."""
    top = [
        {"symbol": "AAA", "score": 0.2, "px_575": 12.0, "px_575_et": 575},
        {"symbol": "BBB", "score": 0.2, "px_575": 12.0, "px_575_et": 575},
        {"symbol": "CCC", "score": 0.2, "px_575": 12.0, "px_575_et": 575},
        # rank 4: the $1 floor of the wider universe, admitted
        {"symbol": "R4FLOOR", "score": 0.2, "px_575": 1.0, "px_575_et": 575},
        {"symbol": "R5CHEAP", "score": 0.2, "px_575": 0.99, "px_575_et": 575},  # below $1
        {"symbol": "R6LOWSCORE", "score": 0.049, "px_575": 12.0, "px_575_et": 575},
        {"symbol": "R7STALE", "score": 0.2, "px_575": 12.0, "px_575_et": 572},
        {"symbol": "R8AHEAD", "score": 0.2, "px_575": 12.0, "px_575_et": 576},
        {"symbol": "R9EDGE", "score": 0.2, "px_575": 12.0, "px_575_et": 574},  # exactly T-2
        {"symbol": "R10EDGE", "score": 0.05, "px_575": 12.0, "px_575_et": 575},
        {"symbol": "R11OUT", "score": 0.2, "px_575": 12.0, "px_575_et": 575},
        {"symbol": "R12OUT", "score": 0.2, "px_575": 12.0, "px_575_et": 575},
    ]
    cp = tmp_path / "sip" / "candidates"
    cp.mkdir(parents=True)
    (cp / f"{DAY}.json").write_text(
        json.dumps(
            {
                "snapshots": [
                    # a pre-open population is not a B admission
                    {"T": 570, "pop": "A_open", "top": top},
                    # a B snapshot outside the declared snapshot set is ignored
                    {"T": 590, "pop": "B", "top": top},
                    # a non-B population never admits
                    {"T": 585, "pop": "C", "top": top[:3]},
                    {"T": 575, "pop": "B", "top": top},
                    # the same name qualifying again later keeps its FIRST admission
                    {"T": 585, "pop": "B", "top": top[:1]},
                ]
            }
        )
    )
    watch = alr.full_watch(tmp_path, DAY)
    assert watch == {
        "AAA": 575,
        "BBB": 575,
        "CCC": 575,
        "R4FLOOR": 575,
        "R9EDGE": 575,
        "R10EDGE": 575,
    }
    # the family's own px>=5 gate is NOT an admission gate here: $1-$4 names are
    # requested and only dropped later by the past-only signal state
    assert watch["R4FLOOR"] == 575


def test_full_watch_loads_rank_four_and_builds_a_legitimate_intent(tmp_path):
    """Consumer regression: a snapshot rank-4 stock loads through the shared core
    loader for the NEW full watch and can produce a legitimate LQ intent, while the
    legacy top-3 default never admits it; a declared candidate with no raw stream
    stays data-UNKNOWN (the day is never a quiet cash day)."""
    day = DAY
    _write_universe_day(tmp_path, day, "RANK4", ["NODATA"])
    data = Path(tmp_path)

    # legacy default: the top-3 / px>=5 policy never requests the rank-4 name
    assert alpha_micro_core.admissions(data, day) == {
        "TOP1": 575,
        "TOP2": 575,
        "TOP3": 575,
    }
    legacy_streams, legacy_cov = load_day(data, day)
    assert "RANK4" not in legacy_streams
    assert legacy_cov["watch_names"] == 3
    # the legacy default requests only the top-3 names, none of which have raw rows
    assert legacy_cov.get("no_eligible_trades") is True

    # the new universe: five requested candidates, only rank 4 has raw data
    assert alr.full_watch(data, day) == {
        "TOP1": 575,
        "TOP2": 575,
        "TOP3": 575,
        "RANK4": 575,
        "NODATA": 575,
    }
    out = tmp_path / "out"
    job = (
        day,
        str(data),
        str(out),
        959,  # session_end
        False,  # force
        alr.PRODUCER_SHA,
        alr.contract_sha256(),
        alr.core_sha256(),
        (),  # no supplement roots
        alr.supplement_digest(())[0],
    )
    info = alr.build_day(job)
    assert info["error"] is None
    man = json.loads((out / "days" / f"{day}.json").read_text())
    cov = man["coverage"]
    # the denominator is the REQUESTED full watch, not the covered intersection
    assert cov["watch_names"] == 5
    assert cov["covered_names"] == 1
    assert sorted(cov["missing_symbol_streams"]) == ["NODATA", "TOP1", "TOP2", "TOP3"]
    assert cov["gain_anchor_missing"] == []
    assert man["coverage_label"] == "missing_symbol_streams"
    assert man["coverage_complete"] is False

    intents = man["intents"]
    assert intents, "the rank-4 stock must be able to produce an LQ intent"
    assert {i["ticker"] for i in intents} == {"RANK4"}
    # a legitimate LQ state: a real entry and a real priced exit, both hold views
    filled = [i for i in intents if i["order_state"] == "filled"]
    assert filled, [i["entry_status"] for i in intents]
    for i in filled:
        assert i["entry_status"] == "conditional_ioc_fill"
        assert i["limit_price"] == alr.limit_price_of(i["entry_open"])
        assert i["qty"] >= 1
        for hold in alr.HOLD_SECONDS:
            assert i[f"exit_status_{hold}"] == "conditional_market_fill"
            assert i[f"gross_{hold}"] is not None
            assert i[f"exit_us_{hold}"] <= clock_us(day, 959, 0) + alr.LATENCY_US
    # the signal grid is the full-RTH grid (not the inherited 13:00 framework cap)
    assert max(i["signal_us"] for i in intents) > clock_us(day, 780, 0)
    assert (out / "days" / f"{day}.parquet").exists()
    assert man["supplement"]["prints_added_total"] == 0
