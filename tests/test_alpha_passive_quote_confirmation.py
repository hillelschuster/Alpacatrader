"""Behavioral tests for the passive quote-confirmation producer's order lifetime.

These cover exactly the queue/time-uncertainty edges the resting-limit formulation turns
on and that no sibling producer asserts today (the IOC lane's arrival clock is evaluated
ONCE and can never rest):

* a posted passive DAY limit legitimately RESTS: it fills at a LATER print's ACTUAL ASK,
  never at the optimistic bid that was merely touched, never at a same-bar low, never at a
  trade price and never at a later, cheaper "best future quote";
* a print between the intent clock and the acknowledgement is invisible (the order is not
  working yet), and a print after the cancellation acknowledgement is invisible too, so a
  fill inside the cancellation's own latency window still counts;
* the FIRST visible potential touch is adjudicated once: an ambiguous book (crossed /
  non-regular) or a displayed ASK depth short of q is a PARTIAL/UNKNOWN execution that
  holds the slot and the cash to the session end and NEVER skips forward to a later,
  better, full-size print;
* a resting order with no visible ASK at or below its limit is cancelled unfilled and
  releases its cash at the CANCEL ACK, not at the cancellation submission;
* the limit and the causal quantity are fixed at the INTENT, before the acknowledgement,
  and are never re-floored from a later (even much cheaper) print;
* the placement never rounds UP into the ask, identical bid/mid limits are ONE order, and
  the whole working window must close before the RTH last minute so the capped 60-minute
  exit due intent is always strictly after the actual fill clock;
* the funded book honours the slot, overlap, cooldown (15 minutes after the ACTUAL exit),
  attempt cap (3) and simultaneous-clock funding rules, with no fourth same-clock
  substitution after an unfilled intent;
* every residual rung charges only the extra fee on top of the observed ASK/BID touch
  prices; the spread is already inside those prices and is never re-charged.

Synthetic frames only: no data file, no network, no live order.

Run:
  uv run --no-sync python -m pytest tests/test_alpha_passive_quote_confirmation.py -q
"""

from __future__ import annotations

import math
import sys
from zoneinfo import ZoneInfo

import polars as pl
import pytest

sys.path.insert(0, ".")
sys.path.insert(0, "factory/scripts")

import alpha_passive_quote_confirmation as apc  # noqa: E402
import alpha_quote_aware_frequency as aqf  # noqa: E402
from alpha_quote_audit import clock_us  # noqa: E402

ET = ZoneInfo("America/New_York")
DAY = "2023-05-15"
SESSION_END = 959


def _minutes_us(minute: int, offset_us: int = 0) -> int:
    return clock_us(DAY, minute, 0) + int(offset_us)


def _frame(rows, ticker: str = "TEST") -> pl.DataFrame:
    """Raw quote rows: (minute, offset_us, bid, ask, bid_size, ask_size, conditions)."""
    stamps, bid, ask, bsz, asz, conds = [], [], [], [], [], []
    for minute, offset_us, b, a, bs, ash, c in rows:
        stamps.append(_minutes_us(minute) + int(offset_us))
        bid.append(b)
        ask.append(a)
        bsz.append(bs)
        asz.append(ash)
        conds.append(list(c))
    return pl.DataFrame(
        {
            "symbol": [ticker] * len(rows),
            "ts_utc": pl.Series(stamps, dtype=pl.Datetime("us", "UTC")),
            "bid_price": pl.Series(bid, dtype=pl.Float64),
            "ask_price": pl.Series(ask, dtype=pl.Float64),
            "bid_size": pl.Series(bsz, dtype=pl.Float64),
            "ask_size": pl.Series(asz, dtype=pl.Float64),
            "bid_exchange": ["P"] * len(rows),
            "ask_exchange": ["Q"] * len(rows),
            "conditions": pl.Series(conds, dtype=pl.List(pl.Utf8)),
            "tape": ["C"] * len(rows),
        }
    ).sort("ts_utc")


def _stream(rows, ticker: str = "TEST"):
    return aqf.symbol_quotes(_frame(rows, ticker), DAY)[ticker]


def _dense(ticker: str = "TEST", minutes=None, bid=10.0, ask=10.0, size=100_000.0):
    """A locked, always-deep book: every print is immediately marketable at a 10.00 limit."""
    minutes = range(600, 701) if minutes is None else minutes
    rows = [
        (minute, off, bid, ask, size, size, ("R",))
        for minute in minutes
        for off in range(0, 60_000_000, 250_000)
    ]
    return _stream(rows, ticker)


def _floor_cent(value: float) -> float:
    return math.floor(value * 100) / 100


def _lots(shares: float) -> float:
    """RAW round-lot size for a share count (pre-2025-11-03 sizes are lots, x100 shares)."""
    return shares / 100.0


def _state_row(
    ticker: str,
    t: int,
    *,
    bid: float = 10.00,
    ask: float = 10.00,
    pred: float = 0.05,
    depth: float = 100_000.0,
    session_end: int = SESSION_END,
    limit: float | None = None,
    intent_status: str = "quoted",
) -> dict:
    """One scored panel state. ``limit`` is the posted passive limit at the intent."""
    limit_bid = _floor_cent(bid) if limit is None else float(limit)
    limit_mid = _floor_cent(0.5 * (bid + ask)) if limit is None else float(limit)
    qty = int(apc.ORDER_BUDGET // (limit_bid * (1.0 + apc.MAX_RUNG_COST / 20_000.0)))
    return {
        "day": DAY,
        "ticker": ticker,
        "t": t,
        "admit_t": t,
        "entry_et": t,
        "entry_open": ask,
        "entry_status": "observed_open_proxy",
        "session_end": session_end,
        "proxy_gross_60": 0.01,
        "pred": pred,
        "entry_minute": t + 1,
        "intent_us": _minutes_us(t + 1),
        "intent_status": intent_status,
        "intent_reason": "eligible" if intent_status == "quoted" else intent_status,
        "intent_bid": bid,
        "intent_ask": ask,
        "intent_age_s": 0.25,
        "intent_spread_bps": (ask / bid - 1.0) * 10_000.0,
        "intent_ask_shares": depth,
        "limit_bid": limit_bid,
        "qty_bid": qty,
        "reserved_bid": qty * limit_bid * (1.0 + apc.MAX_RUNG_COST / 20_000.0),
        "limit_mid": limit_mid,
        "qty_mid": qty,
        "reserved_mid": qty * limit_mid * (1.0 + apc.MAX_RUNG_COST / 20_000.0),
        "placement_collapsed": limit_bid == limit_mid,
    }


def _states_frame(rows) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=apc.STATE_TYPES)


def _view(placement: str = "bid", ttl_s: int = 120, threshold: float = 0.020) -> apc.View:
    return apc.View(threshold, placement, ttl_s)


# ----- placement price --------------------------------------------------------
def test_placement_never_rounds_up_into_the_ask():
    """The passive limit is floored to the tick, so it can never become a marketable take."""
    # a 1-cent spread: mid is a half cent, which must floor DOWN to the bid, not up
    assert apc.passive_limit_price("mid", 10.00, 10.01) == 10.00
    assert apc.passive_limit_price("bid", 10.00, 10.01) == 10.00
    # a 2-cent spread: mid is a whole cent and stays below the ask
    assert apc.passive_limit_price("mid", 10.00, 10.02) == 10.01
    assert apc.passive_limit_price("bid", 10.00, 10.02) == 10.00
    # a fractional-cent bid floors down (never up to the next tick)
    assert apc.passive_limit_price("bid", 10.005, 10.02) == 10.00
    # a locked book saves nothing and is reported as such
    assert apc.placement_saving_bps(10.00, 10.00) == 0.0
    assert apc.placement_saving_bps(10.00, 10.02) == pytest.approx((10.02 - 10.00) / 10.02 * 1e4)


def test_identical_bid_and_mid_limits_are_flagged_as_one_order():
    """A sub-cent-spread book floors both placements to the same posted limit."""
    assert _floor_cent(0.5 * (10.00 + 10.01)) == _floor_cent(10.00)
    views = {(v.placement, v.ttl_s) for v in apc.VIEWS}
    assert views == {("bid", 30), ("bid", 120), ("mid", 30), ("mid", 120)}
    assert len(apc.VIEWS) == 8


# ----- the resting window -----------------------------------------------------
def test_passive_limit_rests_and_fills_at_a_later_print_actual_ask():
    """The order rests (an IOC could not) and fills at the ACTUAL ask of the touch print."""
    rows = [
        _state_row("AAA", 600, bid=10.00, ask=10.02),
    ]
    states = _states_frame(rows)
    quote_rows = [
        (601, 0, 10.00, 10.02, 100_000, 100_000, ("R",)),  # the intent print
        (601, 10_000_000, 10.00, 10.05, 100_000, 100_000, ("R",)),  # ask above the limit
        (601, 40_000_000, 9.98, 10.00, 100_000, _lots(500), ("R",)),  # first potential touch
        (601, 45_000_000, 9.95, 9.99, 100_000, _lots(5_000), ("R",)),  # a better future print
        (661, 0, 10.05, 10.07, _lots(5_000), _lots(5_000), ("R",)),  # the scheduled exit
    ]
    streams = {"AAA": _stream(quote_rows)}
    daily, trades = apc.replay_day(DAY, states, streams, _view(ttl_s=120))
    assert daily["attempts"] == 1
    trade = trades[0]
    assert trade["fill_status"] == "conditional_fill"
    assert trade["entry_status"] == "conditional_passive_fill"
    assert trade["entry_touch_kind"] == "firm_full_displayed_depth"
    # priced at the ACTUAL ask of the touch print, never at the touched bid / the later
    # cheaper print / a same-bar low
    assert trade["entry_ask"] == 10.00
    assert trade["entry_bid"] == 9.98
    assert trade["entry_fill_us"] == _minutes_us(601, 40_000_000)
    assert trade["fill_delay_s"] == pytest.approx(39.75)
    assert trade["entry_depth_shares"] == 500.0
    # the scan stopped at the FIRST potential touch: no best future quote
    assert trade["entry_ask"] != 9.99
    # the 60-minute hold runs from the actual fill, and the exit prices the real bid
    assert trade["exit_minute"] == 661
    assert trade["exit_submit_us"] == _minutes_us(661)
    assert trade["exit_price_status"] == "priced_fresh_at_arrival"
    assert trade["exit_bid"] == 10.05
    assert trade["actual_exit_us"] == _minutes_us(661, 250_000)
    assert trade["hold_minutes"] == pytest.approx(60.0 - 40.0 / 60.0, abs=0.01)


def test_short_displayed_depth_at_the_first_touch_is_unknown_and_never_skips_forward():
    """A partial fill is an UNKNOWN; the scan does NOT skip to a later full-size print."""
    states = _states_frame([_state_row("AAA", 600, bid=10.00, ask=10.02)])
    quote_rows = [
        (601, 0, 10.00, 10.02, 100_000, 100_000, ("R",)),
        (601, 5_000_000, 9.99, 10.00, 100_000, _lots(10), ("R",)),  # touch, depth 10 << q
        (601, 10_000_000, 9.95, 9.99, 100_000, _lots(5_000), ("R",)),  # a later FULL print
    ]
    streams = {"AAA": _stream(quote_rows)}
    daily, trades = apc.replay_day(DAY, states, streams, _view(ttl_s=120))
    trade = trades[0]
    assert trade["entry_status"] == "unknown_entry_partial_depth_at_touch"
    assert trade["fill_status"] == "unknown_entry_execution"
    assert trade["entry_touch_kind"] == "firm_short_displayed_depth"
    assert trade["entry_ask"] is None
    assert trade["entry_fill_us"] is None
    assert daily["unknown_fills"] == 1 and daily["fills"] == 0
    assert daily["known_usd_25"] == 0.0
    assert daily["lower_bound_usd_25"] == pytest.approx(-apc.ORDER_BUDGET)
    # the unresolved position holds its slot, cash and ticker to the session end
    assert trade["actual_exit_us"] == _minutes_us(SESSION_END) + 1
    assert trade["release_reason"] == "session_end_unresolved"


def test_ambiguous_book_at_the_first_touch_is_unknown():
    """A crossed / non-regular print at the first potential touch is an ambiguous book."""
    states = _states_frame([_state_row("AAA", 600, bid=10.00, ask=10.02)])
    quote_rows = [
        (601, 0, 10.00, 10.02, 100_000, 100_000, ("R",)),
        (601, 5_000_000, 10.10, 9.95, 100_000, _lots(5_000), ("R",)),  # crossed: ask<=limit
        (601, 10_000_000, 9.95, 9.99, 100_000, _lots(5_000), ("R",)),  # a later firm print
    ]
    streams = {"AAA": _stream(quote_rows)}
    daily, trades = apc.replay_day(DAY, states, streams, _view(ttl_s=120))
    trade = trades[0]
    assert trade["entry_status"] == "unknown_entry_ambiguous_book_at_touch"
    assert trade["entry_touch_kind"] == "ambiguous_book"
    assert trade["fill_status"] == "unknown_entry_execution"
    assert trade["entry_ask"] is None
    assert daily["unknown_fills"] == 1


def test_ambiguous_book_at_the_acknowledgement_is_unknown():
    """A crossed state already on the book at the ACK with ask <= limit is UNKNOWN."""
    sq = _stream(
        [
            (601, 0, 10.05, 9.98, 100_000, _lots(5_000), ("R",)),  # crossed, ask inside limit
        ]
    )
    submit_ack = apc.arrival_us(_minutes_us(601))
    touch, status = apc.passive_entry_leg(sq, submit_ack, submit_ack + 120_000_000, 10.00, 99)
    assert touch is None and status == "unknown_entry_ambiguous_book_at_ack"


def test_a_pre_ack_print_becomes_the_ack_state_and_never_resizes_the_order():
    """A print before the acknowledgement is the book state AT the acknowledgement.

    It can therefore make the order marketable at placement, but the limit and the causal
    quantity were fixed at the INTENT and are never re-derived from that favourable print.
    """
    states = _states_frame([_state_row("AAA", 600, bid=10.00, ask=10.02)])
    quote_rows = [
        (601, 0, 10.00, 10.02, 100_000, 100_000, ("R",)),
        (601, 100_000, 5.00, 5.50, 100_000, _lots(5_000), ("R",)),  # 100ms after the intent
        (661, 0, 5.60, 5.62, _lots(5_000), _lots(5_000), ("R",)),
    ]
    streams = {"AAA": _stream(quote_rows)}
    daily, trades = apc.replay_day(DAY, states, streams, _view(ttl_s=30))
    trade = trades[0]
    assert trade["fill_status"] == "conditional_fill"
    # the fill prices the state the order was acknowledged into
    assert trade["entry_ask"] == 5.50
    assert trade["fill_delay_s"] == pytest.approx(0.0)
    # ... but the size and the limit come from the INTENT observation only
    assert trade["limit_price"] == 10.00
    assert trade["qty"] == int(apc.ORDER_BUDGET // (10.00 * (1.0 + apc.MAX_RUNG_COST / 20_000.0)))
    assert trade["qty"] != int(apc.ORDER_BUDGET // (5.50 * (1.0 + apc.MAX_RUNG_COST / 20_000.0)))


def test_a_fill_inside_the_cancellation_latency_window_counts():
    """The visible window ends at the CANCEL ACK, so a late print can still fill it."""
    states = _states_frame([_state_row("AAA", 600, bid=10.00, ask=10.02)])
    quote_rows = [
        (601, 0, 10.00, 10.02, 100_000, 100_000, ("R",)),
        (601, 30_400_000, 9.99, 10.00, 100_000, _lots(5_000), ("R",)),  # inside the latency
        (661, 0, 10.05, 10.07, _lots(5_000), _lots(5_000), ("R",)),
    ]
    streams = {"AAA": _stream(quote_rows)}
    view = _view(ttl_s=30)
    daily, trades = apc.replay_day(DAY, states, streams, view)
    trade = trades[0]
    assert trade["submit_ack_us"] == _minutes_us(601, 250_000)
    assert trade["cancel_submit_us"] == _minutes_us(601, 30_250_000)
    assert trade["cancel_ack_us"] == _minutes_us(601, 30_500_000)
    assert trade["entry_fill_us"] == _minutes_us(601, 30_400_000)
    assert trade["fill_status"] == "conditional_fill"
    assert daily["known_fills"] == 1


def test_a_print_after_the_cancel_acknowledgement_is_invisible():
    """Once the cancellation is acknowledged the order is gone, whatever prints later."""
    states = _states_frame([_state_row("AAA", 600, bid=10.00, ask=10.02)])
    quote_rows = [
        (601, 0, 10.00, 10.02, 100_000, 100_000, ("R",)),
        (601, 30_600_000, 9.95, 9.99, 100_000, _lots(5_000), ("R",)),  # after the cancel ACK
    ]
    streams = {"AAA": _stream(quote_rows)}
    daily, trades = apc.replay_day(DAY, states, streams, _view(ttl_s=30))
    trade = trades[0]
    assert trade["fill_status"] == "rested_unfilled_no_visible_touch"
    assert trade["actual_exit_us"] == trade["cancel_ack_us"]
    assert daily["fills"] == 0 and daily["unknown_fills"] == 0


def test_rested_order_releases_cash_at_the_cancel_ack_not_the_cancel_submit():
    """Cash returns only at the cancel ACK, so the ticker is free at the next clock."""
    states = _states_frame(
        [
            _state_row("AAA", 600, bid=10.00, ask=10.02),
            _state_row("AAA", 602, bid=10.00, ask=10.02),
        ]
    )
    quote_rows = [
        (601, 0, 10.00, 10.02, 100_000, 100_000, ("R",)),
        (601, 10_000_000, 10.00, 10.05, 100_000, 100_000, ("R",)),  # never a touch
        (603, 0, 10.00, 10.00, 100_000, 100_000, ("R",)),
        (663, 0, 10.05, 10.07, _lots(5_000), _lots(5_000), ("R",)),
    ]
    streams = {"AAA": _stream(quote_rows)}
    daily, trades = apc.replay_day(DAY, states, streams, _view(ttl_s=30))
    assert daily["attempts"] == 2
    assert trades[0]["fill_status"] == "rested_unfilled_no_visible_touch"
    assert trades[0]["actual_exit_us"] == trades[0]["cancel_ack_us"]
    assert trades[0]["release_reason"] == "cancel_ack_no_visible_touch"
    assert trades[0]["cancel_ack_us"] > trades[0]["cancel_submit_us"]
    # the second intent lands after the cancel ACK, so the released ticket funds it
    assert trades[1]["fill_status"] == "conditional_fill"


def test_limit_and_quantity_are_fixed_at_the_intent_never_re_floored():
    """A much cheaper fill never re-sizes the causal quantity or re-prices the limit."""
    states = _states_frame([_state_row("AAA", 600, bid=10.00, ask=10.02)])
    quote_rows = [
        (601, 0, 10.00, 10.02, 100_000, 100_000, ("R",)),
        (601, 5_000_000, 9.00, 9.50, 100_000, _lots(5_000), ("R",)),  # far cheaper
        (661, 0, 9.60, 9.62, _lots(5_000), _lots(5_000), ("R",)),
    ]
    streams = {"AAA": _stream(quote_rows)}
    daily, trades = apc.replay_day(DAY, states, streams, _view(ttl_s=120))
    trade = trades[0]
    expected_qty = int(apc.ORDER_BUDGET // (10.00 * (1.0 + apc.MAX_RUNG_COST / 20_000.0)))
    assert trade["limit_price"] == 10.00
    assert trade["qty"] == expected_qty
    assert trade["reserved_usd"] == pytest.approx(
        expected_qty * 10.00 * (1.0 + apc.MAX_RUNG_COST / 20_000.0)
    )
    assert trade["reserved_usd"] <= apc.ORDER_BUDGET + 1e-9
    # the actual ask of the touch print is paid, but the size is the causal one
    assert trade["entry_ask"] == 9.50


def test_marketable_at_placement_fills_at_that_prints_own_ask():
    """A locked/sub-cent book posts a limit at the ask and fills at that ask at the ACK."""
    states = _states_frame([_state_row("AAA", 600, bid=10.00, ask=10.00)])
    quote_rows = [
        (601, 0, 10.00, 10.00, 100_000, 100_000, ("R",)),
        (661, 0, 10.10, 10.12, _lots(5_000), _lots(5_000), ("R",)),
    ]
    streams = {"AAA": _stream(quote_rows)}
    daily, trades = apc.replay_day(DAY, states, streams, _view(ttl_s=30))
    trade = trades[0]
    assert trade["placement_collapsed"] is True
    assert trade["placement_saving_bps"] == 0.0
    assert trade["fill_status"] == "conditional_fill"
    assert trade["entry_ask"] == 10.00
    assert trade["entry_fill_us"] == trade["submit_ack_us"]
    assert trade["fill_delay_s"] == pytest.approx(0.0)
    assert trade["exit_bid"] == 10.10


# ----- the funded book --------------------------------------------------------
def test_simultaneous_clock_funds_before_any_outcome_without_substitution():
    """Three intents are funded before any outcome; the fourth is never substituted."""
    rows = [
        _state_row("AAA", 600, bid=10.00, ask=10.00, pred=0.05),
        _state_row("BBB", 600, bid=10.00, ask=10.00, pred=0.04),
        _state_row("CCC", 600, bid=10.00, ask=10.00, pred=0.03),
        _state_row("DDD", 600, bid=10.00, ask=10.00, pred=0.02),
    ]
    states = _states_frame(rows)
    streams = {t: _dense(t, minutes=range(600, 701)) for t in ("AAA", "BBB", "CCC", "DDD")}
    daily, trades = apc.replay_day(DAY, states, streams, _view())
    assert daily["attempts"] == 3
    assert daily["cash_or_slot_skips"] == 1
    assert {tr["ticker"] for tr in trades} == {"AAA", "BBB", "CCC"}
    assert max(tr["positions_at_entry"] for tr in trades) == apc.MAX_SLOTS
    assert "DDD" not in {tr["ticker"] for tr in trades}
    # the funded book is 3 x $1,000 and every reservation stays inside its ticket
    assert apc.BOOK == 3000.0 and apc.MAX_SLOTS == 3
    assert all(tr["reserved_usd"] <= apc.ORDER_BUDGET + 1e-9 for tr in trades)


def test_unknown_position_holds_the_slot_and_blocks_the_ticker_for_the_session():
    """An unresolvable entry keeps its slot, cash and ticker until the session ends."""
    rows = [
        _state_row("AAA", 600, bid=10.00, ask=10.02),
        _state_row("AAA", 690, bid=10.00, ask=10.02),
    ]
    states = _states_frame(rows)
    quote_rows = [
        (601, 0, 10.00, 10.02, 100_000, 100_000, ("R",)),
        (601, 5_000_000, 9.99, 10.00, 100_000, 10, ("R",)),  # short depth at the touch
    ]
    streams = {"AAA": _stream(quote_rows)}
    daily, trades = apc.replay_day(DAY, states, streams, _view(ttl_s=120))
    assert daily["attempts"] == 1
    assert daily["overlap_skips"] == 1
    assert trades[0]["actual_exit_us"] == _minutes_us(SESSION_END) + 1


def test_chronic_working_orders_consume_slots_for_their_whole_ttl():
    """A working order holds its slot until its cancel ACK, so a peer at that clock waits."""
    # three tickers whose books never show a touch: all three slots stay committed
    rows = [_state_row(t, 600, bid=10.00, ask=10.02) for t in ("AAA", "BBB", "CCC")]
    rows.append(_state_row("DDD", 600, bid=10.00, ask=10.02))
    states = _states_frame(rows)
    quote_rows = [
        (601, 0, 10.00, 10.02, 100_000, 100_000, ("R",)),
        (601, 10_000_000, 10.00, 10.05, 100_000, 100_000, ("R",)),
    ]
    streams = {t: _stream(quote_rows, t) for t in ("AAA", "BBB", "CCC", "DDD")}
    daily, trades = apc.replay_day(DAY, states, streams, _view(ttl_s=120))
    assert daily["attempts"] == 3
    assert daily["cash_or_slot_skips"] == 1
    assert all(tr["fill_status"] == "rested_unfilled_no_visible_touch" for tr in trades)
    assert {tr["ticker"] for tr in trades} == {"AAA", "BBB", "CCC"}
    # ... and once the cancellations are acknowledged the slots come back
    rows_late = _states_frame([_state_row("DDD", 610, bid=10.00, ask=10.00)])
    streams_late = {"DDD": _dense("DDD", minutes=range(611, 701))}
    late_daily, late_trades = apc.replay_day(DAY, rows_late, streams_late, _view())
    assert late_daily["attempts"] == 1


def test_cooldown_is_fifteen_minutes_after_the_actual_exit():
    """One ticker cannot re-enter inside 15 minutes of its ACTUAL priced exit clock."""
    rows = [
        _state_row("AAA", 600, bid=10.00, ask=10.00),  # fills at the ACK, exit due 661
        _state_row("AAA", 675, bid=10.00, ask=10.00),  # intent 676:00 < exit + 15min
        _state_row("AAA", 676, bid=10.00, ask=10.00),  # intent 677:00 > exit + 15min
    ]
    states = _states_frame(rows)
    streams = {"AAA": _dense("AAA", minutes=range(601, 701))}
    daily, trades = apc.replay_day(DAY, states, streams, _view())
    assert daily["cooldown_skips"] == 1
    assert daily["attempts"] == 2
    assert trades[0]["exit_minute"] == 661
    assert trades[0]["actual_exit_us"] >= _minutes_us(661)
    assert trades[0]["hold_minutes"] == pytest.approx(60.0, abs=0.01)


def test_three_attempts_per_ticker_per_session_are_enforced():
    """Three attempts per ticker per session; the fourth is a causal cap skip."""
    rows = [_state_row("AAA", t, bid=10.00, ask=10.00) for t in (600, 676, 752, 829)]
    states = _states_frame(rows)
    streams = {"AAA": _dense("AAA", minutes=range(601, 900))}
    daily, trades = apc.replay_day(DAY, states, streams, _view())
    assert daily["attempts"] == 3
    assert daily["max_attempt_skips"] == 1
    assert daily["cooldown_skips"] == 0
    assert [tr["attempt_index"] for tr in trades] == [1, 2, 3]


def test_identical_posted_limits_within_a_view_are_one_order():
    """Duplicate (ticker, clock, limit) candidates collapse to a single order."""
    rows = [
        _state_row("AAA", 600, bid=10.00, ask=10.00, pred=0.05),
        _state_row("AAA", 600, bid=10.00, ask=10.00, pred=0.04),
    ]
    states = _states_frame(rows)
    streams = {"AAA": _dense("AAA", minutes=range(601, 701))}
    daily, trades = apc.replay_day(DAY, states, streams, _view())
    assert daily["signals"] == 2
    assert daily["dedup_identical_limits"] == 1
    assert daily["attempts"] == 1


def test_working_window_must_close_before_the_rth_last_minute():
    """No order is placed whose cancellation could outlive the session's last minute."""
    rows = [_state_row("AAA", 958, bid=10.00, ask=10.00)]
    states = _states_frame(rows)
    streams = {"AAA": _dense("AAA", minutes=range(959, 960))}
    daily, trades = apc.replay_day(DAY, states, streams, _view(ttl_s=30))
    assert daily["ttl_window_beyond_session"] == 1
    assert daily["attempts"] == 0 and trades == []


# ----- the exit ---------------------------------------------------------------
def test_exit_is_sixty_minutes_after_the_fill_clock_capped_by_the_session_end():
    """The 60-minute hold is measured from the ACTUAL fill and bounded by the last clock."""
    rows = [_state_row("AAA", 901, bid=10.00, ask=10.00)]
    states = _states_frame(rows)
    quote_rows = [
        (902, 0, 10.00, 10.00, 100_000, 100_000, ("R",)),
        (959, 0, 10.20, 10.22, 100_000, 100_000, ("R",)),
    ]
    streams = {"AAA": _stream(quote_rows)}
    daily, trades = apc.replay_day(DAY, states, streams, _view(ttl_s=30))
    trade = trades[0]
    assert trade["fill_status"] == "conditional_fill"
    # fill at 902:00.25, +60min = 962 > session_end 959 -> capped to the last clock
    assert trade["exit_minute"] == SESSION_END
    assert trade["exit_submit_us"] >= _minutes_us(SESSION_END)
    assert trade["actual_exit_us"] >= _minutes_us(SESSION_END)
    assert trade["hold_minutes"] == pytest.approx(56.99, abs=0.02)


def test_exit_bid_depth_short_of_the_causal_quantity_is_unknown():
    """A shallow exit book is an UNKNOWN partial capacity, never a guaranteed full fill."""
    states = _states_frame([_state_row("AAA", 600, bid=10.00, ask=10.00)])
    quote_rows = [
        (601, 0, 10.00, 10.00, 100_000, 100_000, ("R",)),
        (661, 0, 10.05, 10.07, _lots(10), 100_000, ("R",)),  # bid depth 10 << q
    ]
    streams = {"AAA": _stream(quote_rows)}
    daily, trades = apc.replay_day(DAY, states, streams, _view(ttl_s=30))
    trade = trades[0]
    assert trade["exit_status"] == "unknown_exit_partial_depth"
    assert trade["fill_status"] == "unknown_exit_execution"
    assert trade["actual_exit_us"] == _minutes_us(SESSION_END) + 1
    assert daily["unknown_fills"] == 1
    assert daily["known_usd_25"] == 0.0


# ----- costs ------------------------------------------------------------------
def test_rungs_charge_only_the_extra_residual_and_never_the_spread_again():
    """The spread is inside the observed prices; a rung only moves the extra fee."""
    states = _states_frame([_state_row("AAA", 600, bid=10.00, ask=10.00)])
    quote_rows = [
        (601, 0, 10.00, 10.00, 100_000, 100_000, ("R",)),  # entry: marketable at placement
        (661, 0, 10.10, 10.12, _lots(5_000), _lots(5_000), ("R",)),  # exit: the rallied book
    ]
    streams = {"AAA": _stream(quote_rows)}
    daily, trades = apc.replay_day(DAY, states, streams, _view())
    trade = trades[0]
    qty = int(trade["qty"])
    assert trade["entry_ask"] == 10.00 and trade["exit_bid"] == pytest.approx(10.10)
    for rung in aqf.RUNG_COSTS:
        side = rung / 20_000.0
        expected = qty * (10.10 * (1.0 - side) - 10.00 * (1.0 + side))
        assert trade[f"net_usd_{int(rung)}"] == pytest.approx(expected)
        assert daily[f"known_usd_{int(rung)}"] == pytest.approx(expected)
    assert trade["net_usd_0"] > trade["net_usd_5"] > trade["net_usd_25"] > trade["net_usd_150"]
    # the 0bps touch gross is the pure ASK->BID move with nothing added back on top
    assert trade["net_usd_0"] == pytest.approx(qty * (10.10 - 10.00))


def test_one_causal_quantity_funds_every_rung_inside_the_ticket():
    """q is sized at the highest rung, so no reported rung is ever unfundable."""
    states = _states_frame([_state_row("AAA", 600, bid=4.00, ask=4.02)])
    quote_rows = [
        (601, 0, 4.00, 4.02, 100_000, 100_000, ("R",)),
        (601, 5_000_000, 3.99, 4.00, 100_000, _lots(5_000), ("R",)),
        (661, 0, 4.05, 4.07, _lots(5_000), _lots(5_000), ("R",)),
    ]
    streams = {"AAA": _stream(quote_rows)}
    daily, trades = apc.replay_day(DAY, states, streams, _view(ttl_s=120))
    trade = trades[0]
    qty = int(trade["qty"])
    assert qty >= 1
    assert trade["limit_price"] == 4.00
    assert trade["entry_ask"] == 4.00
    assert trade["reserved_usd"] == pytest.approx(qty * 4.00 * (1.0 + apc.MAX_RUNG_COST / 20_000.0))
    assert trade["reserved_usd"] <= apc.ORDER_BUDGET + 1e-9
    for rung in aqf.RUNG_COSTS:
        assert trade[f"net_usd_{int(rung)}"] is not None


def test_missing_quote_stream_is_a_data_unknown_never_cash():
    """A state whose stream was never acquired is skipped as data, not as an unfilled order."""
    states = _states_frame(
        [_state_row("ZZZZ", 600, bid=10.00, ask=10.02, intent_status="quote_not_acquired")]
    )
    streams = {}
    daily, trades = apc.replay_day(DAY, states, streams, _view())
    assert daily["attempts"] == 0 and trades == []
    assert daily["quote_not_acquired"] == 1
    assert daily["fills"] == 0 and daily["unknown_fills"] == 0
    assert daily["known_usd_25"] == 0.0
