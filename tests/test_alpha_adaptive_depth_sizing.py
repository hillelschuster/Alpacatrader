"""Behavioral tests for the adaptive depth-sizing producer's execution physics.

These cover the consumer boundaries the new formulation actually turns on and that no
sibling producer asserts today:

* the fee-funded capital cap ``floor(1000 / (limit * 1.0075))`` at the 150bps reserve and
  the cent-rounded (rounded UP) pre-declared marketability limit;
* the causal depth-participation cap ``floor(participation * min(intent ask, intent bid))``
  in EXACT rational arithmetic, and ``q = min`` of the two caps fixed BEFORE arrival;
* every causal zero-quantity state is a KNOWN no-order (capital cap -> no_order_min_capital,
  participation cap -> no_order_zero_depth), never a priced fill and never an UNKNOWN;
* the ENTRY IOC leg is priced from ONE latest-raw read at its arrival clock and NEVER rests
  into a later, more favourable print (the shared MARKET-style rest branch is not inherited),
  while stale-but-valid / invalid / absent books and partial depth are UNKNOWNs that stay
  reserved to the session end and an ask above the cap is unfilled cash at the arrival clock;
* the EXIT preserves the REAL execution clock of the priced leg - the arrival clock when the
  book was fresh, the first future regular print's timestamp when the working market order
  had to rest - so the cooldown and the cash release wait for the real clock, never the
  planned arrival;
* the funded $3,000 book honours the 3-slot, overlap, 15-minute post-ACTUAL-exit cooldown,
  3-attempt cap and simultaneous-clock funding rules without any same-clock substitution;
* the fixed-size reference arm is a SIMULATED baseline (a capital-cap ticket that the
  displayed depth cannot support is a causal skip), never a linear rescale.

Synthetic frames only: no data file, no network, no live order.

Run:
  uv run --no-sync python -m pytest tests/test_alpha_adaptive_depth_sizing.py -q
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from zoneinfo import ZoneInfo

import polars as pl
import pytest

sys.path.insert(0, ".")
sys.path.insert(0, "factory/scripts")

import alpha_adaptive_depth_sizing as ads  # noqa: E402

ET = ZoneInfo("America/New_York")
DAY = "2023-05-15"
LOT = 1.0  # 2023 sizes are already single shares after the lot multiplier


def _minutes_us(minute: int, us: int = 0) -> int:
    return int(ads.minute_us(DAY, minute)) + int(us)


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
    return ads.symbol_quotes(_frame(rows, ticker), DAY)[ticker]


def _dense_quotes(minutes, *, bid=10.0, ask=10.02, size=100_000.0, ticker="TEST", step_us=500_000):
    """One print every ``step_us`` across the given minutes (default: fresh every 0.5s)."""
    rows = []
    for minute in minutes:
        for off in range(0, 60_000_000, step_us):
            rows.append((minute, off, bid, ask, size, size, ("R",)))
    return _stream(rows, ticker)


# ----- cent rounding and the capital (fee-funded) cap --------------------------
def test_limit_price_is_rounded_up_to_the_cent():
    assert ads.limit_price_of(10.0) == pytest.approx(10.10)  # 10.00 * 1.01, cent grid
    assert ads.limit_price_of(10.001) == pytest.approx(10.11)  # ceil(10.10101 * 100) / 100
    assert ads.limit_price_of(9.999) == pytest.approx(10.10)


def test_capital_cap_is_floored_at_the_150bps_funding_side():
    limit = ads.limit_price_of(10.0)  # 10.10
    assert ads.capital_quantity(limit) == 98  # floor(1000 / (10.10 * 1.0075))
    # exactly the documented factor, sourced in every scenario
    assert ads.capital_quantity(limit) == int(
        math.floor(ads.ORDER_BUDGET / (limit * ads.FUNDING_SIDE_FACTOR))
    )
    # a price whose fee-adjusted ticket exceeds the budget funds nothing
    assert ads.capital_quantity(1000.0) == 0


def test_reservation_covers_the_150bps_rung_without_reflooring():
    quantity, limit = 98, ads.limit_price_of(10.0)
    assert ads.reserved_usd(quantity, limit) == pytest.approx(quantity * limit * 1.0075)
    # a cheaper future print would only make the conditional fill cheaper; the reservation
    # is derived from the INTENT limit, never re-derived from the arrival price
    assert ads.reserved_usd(quantity, limit) != ads.reserved_usd(
        ads.capital_quantity(ads.limit_price_of(9.0)), ads.limit_price_of(9.0)
    )


# ----- the causal participation cap -------------------------------------------
def test_participation_cap_is_exact_rational_at_boundaries():
    # 10% of 30 shares is exactly 3 - never 2 from binary floating point drift
    assert ads.participation_quantity(0.10, 30.0) == 3
    assert ads.participation_quantity(0.10, 10.0) == 1
    assert ads.participation_quantity(0.25, 4.0) == 1
    assert ads.participation_quantity(0.25, 3.0) == 0
    assert ads.participation_quantity(0.50, 3.0) == 1
    assert ads.participation_quantity(0.50, 1.0) == 0
    # a fractional displayed depth floors the exact rational product
    assert ads.participation_quantity(0.10, 29.999) == 2


def test_quantity_is_the_min_of_the_capital_and_both_sides_depth():
    limit = ads.limit_price_of(10.0)
    # the BID side binds: 10% of min(1000 ask, 500 bid) = 50, below the 98 capital cap
    assert ads.view_quantity(limit, 0.10, 1000.0, 500.0) == 50
    # the capital cap binds when both sides are deep
    assert ads.view_quantity(limit, 0.50, 100_000.0, 100_000.0) == 98
    # a zero displayed side is a zero quantity (a known no-order downstream)
    assert ads.view_quantity(limit, 0.10, 100_000.0, 0.0) == 0


# ----- eligibility boundaries --------------------------------------------------
def _facts(**over):
    ask = float(over.get("intent_ask", 10.0))
    limit = ads.limit_price_of(ask)
    base = {
        "intent_status": "quoted",
        "intent_reason": "eligible",
        "intent_ask": ask,
        "intent_bid": ask - 0.02,
        "intent_age_s": 0.5,
        "intent_spread_bps": 20.0,
        "intent_ask_shares": 100_000.0,
        "intent_bid_shares": 100_000.0,
        "limit_price": limit,
        "qty_capital": ads.capital_quantity(limit),
    }
    base.update(over)
    base["intent_reason"] = ads.intent_gate(base)
    return base


def test_zero_quantity_states_are_known_no_orders():
    rich = _facts(intent_ask=1000.0)  # the fee-adjusted ticket exceeds the budget
    assert rich["qty_capital"] == 0
    assert ads.intent_gate(rich) == "no_order_min_capital"
    assert ads.arm_eligibility(rich, ads.View(0.015, 0.10), 0.05)[0] == "no_order_min_capital"
    # a thin two-sided book floors the participation cap to zero
    thin = _facts(intent_ask_shares=9.0, intent_bid_shares=9.0)
    assert ads.arm_eligibility(thin, ads.View(0.015, 0.10), 0.05) == ("no_order_zero_depth", 0)
    # ... while a higher participation of the same book is fundable
    assert ads.arm_eligibility(thin, ads.View(0.015, 0.50), 0.05) == ("eligible", 4)


def test_spread_bar_uses_the_observed_spread_plus_25bps():
    ok = _facts()  # 20 + 25 <= 500 bps at pred 0.05
    assert ads.arm_eligibility(ok, ads.View(0.015, 0.10), 0.05)[0] == "eligible"
    assert ads.arm_eligibility(ok, ads.View(0.030, 0.10), 0.05)[0] == "eligible"
    tight = _facts(intent_spread_bps=600.0)
    assert ads.arm_eligibility(tight, ads.View(0.015, 0.10), 0.05) == (
        "spread_above_prediction",
        0,
    )
    assert ads.arm_eligibility(ok, ads.View(0.015, 0.10), 0.001)[0] == "spread_above_prediction"
    stale = _facts(intent_age_s=3.0)
    assert ads.arm_eligibility(stale, ads.View(0.015, 0.10), 0.05)[0] == "intent_not_firm_fresh"


def test_fixed_size_arm_skips_a_ticket_the_displayed_depth_cannot_support():
    # the $1,000 capital-cap ticket (98 shares) vs 50 displayed ask shares
    thin = _facts(intent_ask_shares=50.0, intent_bid_shares=50.0)
    assert ads.arm_eligibility(thin, ads.View(0.015, None), 0.05) == (
        "intent_depth_unsupported",
        0,
    )
    # the adaptive 10% participation of the same book is 5 shares and is eligible
    assert ads.arm_eligibility(thin, ads.View(0.015, 0.10), 0.05) == ("eligible", 5)


# ----- entry IOC physics (own leg: one read, never a rest) ---------------------
def test_entry_ioc_treats_a_stale_but_valid_print_as_unknown():
    rows = [
        (601, 0, 10.0, 10.02, 100, 100, ("R",)),
        (604, 0, 12.0, 12.20, 100, 100, ("R",)),  # much more favourable, but later
    ]
    sq = _stream(rows)
    arrival = _minutes_us(603, 500_000)  # latest print is 2.5s old: stale but valid
    limit = ads.limit_price_of(10.0)
    q, status, clock = ads.price_entry_ioc(sq, arrival, limit, 50)
    assert q is None
    assert status == "unknown_entry_stale_but_valid"
    assert clock is None
    # the later favourable print is NOT used by the entry leg
    raw, raw_status = sq.latest_raw(arrival)
    assert raw is not None and raw_status == "quoted" and raw["age_s"] > ads.MAX_AGE_S


def test_entry_ioc_never_rests_into_a_future_eligible_print():
    rows = [
        (601, 0, 10.0, 10.02, 100, 100, ("R",)),
        (603, 0, 12.0, 11.0, 100, 100, ("R",)),  # crossed at arrival
        (604, 0, 10.0, 10.02, 100, 100, ("R",)),  # first eligible print after arrival
    ]
    sq = _stream(rows)
    arrival = _minutes_us(603, 500_000)
    limit = ads.limit_price_of(10.0)
    q, status, clock = ads.price_entry_ioc(sq, arrival, limit, 50)
    assert q is None
    assert status == "unknown_entry_invalid_or_crossed_quote"
    assert clock is None
    # the shared MARKET-style helper WOULD rest to the 604 print - the entry leg must not
    rested, rest_status, rest_clock = ads.priced_leg(sq, arrival, _minutes_us(959))
    assert rest_status == "priced_rested_until_regular"
    assert rest_clock == _minutes_us(604)
    assert rested is not None and rested["ask"] == pytest.approx(10.02)


def test_entry_ioc_fills_at_the_actual_arrival_ask_below_the_cap():
    rows = [(601, 0, 10.0, 10.02, 100, 100, ("R",))]
    sq = _stream(rows)
    arrival = _minutes_us(601, 250_000)
    limit = ads.limit_price_of(10.0)  # 10.10
    q, status, clock = ads.price_entry_ioc(sq, arrival, limit, 50)
    assert status == "conditional_ioc_fill"
    assert clock == arrival  # the fill clock is the arrival clock, not the print stamp
    assert q is not None and q["ask"] == pytest.approx(10.02)
    # an arrival ask above the pre-declared cap is unfilled cash, released at the arrival
    over = _stream([(601, 0, 10.4, 10.5, 100, 100, ("R",))])
    q2, status2, clock2 = ads.price_entry_ioc(over, arrival, limit, 50)
    assert q2 is None and status2 == "no_match_at_l1" and clock2 == arrival
    # an arrival ask at/below the cap with thin displayed depth is an UNKNOWN, not a fill
    # (pre-2025-11-03 raw sizes are round lots: 0.04 lots displays as 4 shares)
    shallow = _stream([(601, 0, 10.0, 10.02, 0.04, 0.04, ("R",))])
    q3, status3, clock3 = ads.price_entry_ioc(shallow, arrival, limit, 50)
    assert q3 is None and status3 == "unknown_entry_partial_depth" and clock3 is None


# ----- funded book replay ------------------------------------------------------
def _state_row(
    ticker,
    t,
    *,
    pred=0.05,
    ask=10.0,
    spread_bps=20.0,
    ask_depth=100_000.0,
    bid_depth=100_000.0,
    session_end=760,
):
    minute = t + 1
    limit = ads.limit_price_of(ask)
    return {
        "day": DAY,
        "ticker": ticker,
        "t": t,
        "admit_t": t,
        "entry_et": t,
        "entry_open": ask,
        "entry_status": "filled_proxy",
        "session_end": session_end,
        "proxy_gross_60": 0.02,
        "pred_60": pred,
        "entry_minute": minute,
        "intent_us": _minutes_us(minute),
        "intent_status": "quoted",
        "intent_reason": "eligible",
        "intent_ask": ask,
        "intent_bid": ask - 0.02,
        "intent_age_s": 0.5,
        "intent_spread_bps": spread_bps,
        "intent_ask_shares": ask_depth,
        "intent_bid_shares": bid_depth,
        "limit_price": limit,
        "qty_capital": ads.capital_quantity(limit),
    }


def _states_frame(rows):
    return pl.DataFrame(rows, schema=ads.STATE_TYPES)


def _book_streams(tickers, *, bid=10.0, ask=10.02, minutes=None):
    minutes = range(600, 761) if minutes is None else minutes
    return {
        t: _dense_quotes(list(minutes), bid=bid, ask=ask, size=100_000.0, ticker=t) for t in tickers
    }


def _arm(threshold=0.015, participation=0.10):
    return ads.View(threshold, participation)


def test_adaptive_quantity_uses_the_displayed_two_sided_depth():
    rows = [_state_row("AAA", 600, ask_depth=1000.0, bid_depth=500.0)]
    states = _states_frame(rows)
    daily, trades = ads.replay_day(DAY, states, _book_streams(["AAA"]), _arm())
    assert daily["attempts"] == 1
    assert trades[0]["qty"] == 50  # 10% of the shallower (bid) side, below the 98 capital cap
    assert trades[0]["qty_capital"] == 98
    assert trades[0]["deployed_reserved_usd"] == pytest.approx(50 * 10.10 * 1.0075)


def test_simultaneous_clock_funds_top_forecasts_without_substitution():
    rows = [
        _state_row("AAA", 600, pred=0.05),
        _state_row("BBB", 600, pred=0.04),
        _state_row("CCC", 600, pred=0.03),
        _state_row("DDD", 600, pred=0.02),
    ]
    states = _states_frame(rows)
    daily, trades = ads.replay_day(DAY, states, _book_streams(["AAA", "BBB", "CCC", "DDD"]), _arm())
    assert daily["attempts"] == 3
    assert daily["cash_or_slot_skips"] == 1
    assert {tr["ticker"] for tr in trades} == {"AAA", "BBB", "CCC"}
    assert max(tr["positions_at_entry"] for tr in trades) == 3
    # the unfunded fourth candidate is never funded later at that clock
    assert "DDD" not in {tr["ticker"] for tr in trades}


def test_cooldown_waits_the_actual_exit_plus_fifteen_minutes():
    rows = [
        _state_row("AAA", 600),  # entry 601, h60 exit at 661
        _state_row("AAA", 669),  # intent 670: inside the 661 + 15min cooldown
        _state_row("AAA", 676),  # intent 677: past the real exit + 15min
    ]
    states = _states_frame(rows)
    daily, trades = ads.replay_day(DAY, states, _book_streams(["AAA"]), _arm())
    assert daily["attempts"] == 2
    assert daily["cooldown_skips"] == 1
    assert [tr["entry_minute"] for tr in trades] == [601, 677]


def test_max_attempts_per_ticker_per_day_is_three():
    rows = [_state_row("AAA", minute - 1) for minute in (601, 677, 753, 829, 905)]
    states = _states_frame(rows)
    streams = _book_streams(["AAA"], minutes=range(600, 981))
    daily, trades = ads.replay_day(DAY, states, streams, _arm())
    assert daily["attempts"] == 3
    assert daily["max_attempt_skips"] == 2
    assert [tr["attempt_index"] for tr in trades] == [1, 2, 3]


def test_no_match_entry_releases_cash_and_does_not_block_the_ticker():
    rows = [_state_row("AAA", 600), _state_row("AAA", 605)]
    states = _states_frame(rows)
    # the arrival ask (10.50) is far above the intent-derived limit (10.01 -> 10.10 cap)
    streams = {"AAA": _dense_quotes(list(range(600, 761)), bid=10.4, ask=10.5, size=100_000.0)}
    daily, trades = ads.replay_day(DAY, states, streams, _arm())
    assert daily["attempts"] == 2
    assert daily["no_match_fills"] == 2
    assert daily["unknown_fills"] == 0
    assert daily["known_usd_25"] == 0.0
    assert {tr["entry_status"] for tr in trades} == {"no_match_at_l1"}


def test_unknown_entry_stays_reserved_to_the_session_end():
    rows = [_state_row("AAA", 600)]
    states = _states_frame(rows)
    # the book is crossed at the entry arrival and never recovers
    streams = {"AAA": _stream([(601, 100_000, 12.0, 11.0, 100, 100, ("R",))])}
    daily, trades = ads.replay_day(DAY, states, streams, _arm())
    assert daily["attempts"] == 1
    assert daily["unknown_fills"] == 1
    assert trades[0]["fill_status"] == "unknown_entry_execution"
    assert trades[0]["actual_exit_us"] == _minutes_us(760) + 1
    assert daily["lower_bound_usd_25"] == -ads.ORDER_BUDGET
    assert daily["known_usd_25"] == 0.0


def test_exit_preserves_the_real_price_clock_when_the_market_order_rests():
    """A working market order that rests to a later regular print releases at THAT clock.

    The exit due intent (661) sees a firm fresh book and submits, but the arrival read is
    crossed, so the shared MARKET leg rests to the first eligible regular print at/after
    the arrival (670). The REAL execution clock is 670 - not the planned 661.25 arrival -
    and the 15-minute cooldown is anchored to it.
    """
    rows = []
    for minute in range(600, 661):  # dense fresh prints 600..660 inclusive
        for off in range(0, 60_000_000, 500_000):
            rows.append((minute, off, 10.0, 10.02, 100_000, 100_000, ("R",)))
    rows.append((661, 0, 10.0, 10.02, 100_000, 100_000, ("R",)))  # firm fresh at the due intent
    rows.append((661, 100_000, 12.0, 11.0, 100, 100, ("R",)))  # crossed at the arrival read
    for minute in range(670, 761):
        for off in range(0, 60_000_000, 500_000):
            rows.append((minute, off, 10.0, 10.02, 100_000, 100_000, ("R",)))
    streams = {"AAA": _stream(rows)}
    states = _states_frame([_state_row("AAA", 600), _state_row("AAA", 679)])
    daily, trades = ads.replay_day(DAY, states, streams, _arm())
    assert daily["attempts"] == 1
    assert daily["cooldown_skips"] == 1  # intent 680 is inside 670 + 15min
    assert daily["fills"] == 1
    trade = trades[0]
    assert trade["exit_priced_at_status"] == "priced_rested_until_regular"
    assert trade["exit_arrival_us"] == _minutes_us(661, 250_000)
    assert trade["actual_exit_us"] == _minutes_us(670)  # the REAL execution clock
    assert trade["actual_exit_us"] != trade["exit_arrival_us"]
    # had the planned arrival been used as the release clock, the 680 intent (inside
    # 670 + 15min but outside 661.25 + 15min) would have been funded
    assert trade["exit_bid"] == pytest.approx(10.0)


def test_rung_ladder_only_moves_the_fee_never_the_quantity():
    rows = [_state_row("AAA", 600)]
    states = _states_frame(rows)
    daily, trades = ads.replay_day(DAY, states, _book_streams(["AAA"]), _arm())
    trade = trades[0]
    assert trade["fill_status"] == "conditional_fill"
    assert daily["known_usd_0"] > daily["known_usd_25"] > daily["known_usd_150"]
    assert trade["qty"] == 98  # the capital cap binds against the deep synthetic book
    for rung in ads.RUNG_COSTS:
        side = rung / 20_000.0
        expected = 98 * (trade["exit_bid"] * (1 - side) - trade["entry_ask"] * (1 + side))
        assert trade[f"net_usd_{int(rung)}"] == pytest.approx(expected)


def test_fixed_size_baseline_arm_is_simulated_alongside_the_adaptive_views():
    rows = [_state_row("AAA", 600, ask_depth=500.0, bid_depth=500.0)]
    states = _states_frame(rows)
    streams = _book_streams(["AAA"])
    adaptive, adaptive_trades = ads.replay_day(DAY, states, streams, _arm(0.015, 0.10))
    baseline, baseline_trades = ads.replay_day(DAY, states, streams, _arm(0.015, None))
    # the same signal set: the adaptive arm funds the participated size, the fixed-size arm
    # funds the full capital-cap ticket - two simulated outcomes, not one rescaled fill
    assert adaptive["attempts"] == 1 and baseline["attempts"] == 1
    assert adaptive_trades[0]["qty"] == 50  # 10% of min(500 ask, 500 bid)
    assert baseline_trades[0]["qty"] == 98  # the capital cap alone (98 <= 500 depth)
    assert baseline["deployed_reserved_usd"] > adaptive["deployed_reserved_usd"]
    assert adaptive_trades[0]["participation"] == 0.10
    assert baseline_trades[0]["participation"] is None
    assert baseline["is_fixed_size_baseline"] and not adaptive["is_fixed_size_baseline"]


# ----- peak reserved cash, per-day denominators, comparator honesty ------------
def test_peak_reserved_usd_is_the_concurrent_peak_not_the_daily_sum():
    """AAA (601->661) and BBB (631->691) overlap; CCC (701->760) overlaps neither."""
    rows = [
        _state_row("AAA", 600),
        _state_row("BBB", 630),
        _state_row("CCC", 700),
    ]
    states = _states_frame(rows)
    daily, trades = ads.replay_day(DAY, states, _book_streams(["AAA", "BBB", "CCC"]), _arm())
    assert daily["attempts"] == 3
    assert daily["positions_peak"] == 2  # the only overlap is AAA + BBB
    per_ticket = ads.reserved_usd(98, ads.limit_price_of(10.0))
    assert daily["peak_reserved_usd"] == pytest.approx(2 * per_ticket)
    assert daily["deployed_reserved_usd"] == pytest.approx(3 * per_ticket)
    # a cumulative sum would have made the two equal: the peak is strictly smaller here
    assert daily["peak_reserved_usd"] < daily["deployed_reserved_usd"]
    assert daily["peak_reserved_usd"] <= ads.BOOK  # never more than the funded book


def _patched_cell(day_values: dict[str, float], days: list[str], arm=None) -> dict:
    """One view_cell over two replayed days with the money columns pinned per day."""
    arm = arm or _arm()
    rows = [_state_row("AAA", 600)]
    streams = _book_streams(["AAA"])
    daily_rows = []
    for day in days:
        daily, _ = ads.replay_day(day, _states_frame(rows), streams, arm)
        daily_rows.append(daily)
    for row, values in zip(daily_rows, day_values, strict=True):
        row.update(values)
    frame = pl.DataFrame(daily_rows, schema={c: ads.DAILY_TYPES[c] for c in ads.DAILY_COLUMNS})
    return ads.view_cell(frame, ads.empty_trades(), arm, days)


def test_per_day_keys_separate_panel_sessions_from_calendar_days():
    # two panel sessions three calendar days apart: 2023-05-15, 16, 17
    days = ["2023-05-15", "2023-05-17"]
    money = [
        {
            "known_usd_25": 600.0,
            "lower_bound_usd_25": -400.0,
            "deployed_reserved_usd": 60_000.0,
        },
        {
            "known_usd_25": 300.0,
            "lower_bound_usd_25": -200.0,
            "deployed_reserved_usd": 40_000.0,
        },
    ]
    cell = _patched_cell(money, days)
    assert cell["days_replayed"] == 2  # panel sessions
    assert cell["calendar_days_spanned"] == 3  # first..last session day inclusive
    # the legacy key keeps its historical VALUE (per session) and is repeated under an
    # honest name; the span key divides by calendar days
    assert cell["known_usd"]["25"] == pytest.approx(900.0)
    assert cell["known_usd_per_calendar_day"]["25"] == pytest.approx(450.0)
    assert cell["known_usd_per_panel_session"]["25"] == pytest.approx(450.0)
    assert cell["known_usd_per_calendar_day_span"]["25"] == pytest.approx(300.0)
    assert cell["full_loss_lower_bound_usd_per_calendar_day"]["25"] == pytest.approx(-300.0)
    assert cell["full_loss_lower_bound_usd_per_panel_session"]["25"] == pytest.approx(-300.0)
    assert cell["full_loss_lower_bound_usd_per_calendar_day_span"]["25"] == pytest.approx(-200.0)
    assert cell["deployed_capital_usd_per_calendar_day"] == pytest.approx(50_000.0)
    assert cell["deployed_capital_usd_per_panel_session"] == pytest.approx(50_000.0)
    assert cell["deployed_capital_usd_per_calendar_day_span"] == pytest.approx(100_000.0 / 3)
    assert "PANEL SESSIONS" in cell["per_day_denominator_note"]


def test_day_states_sha256_reads_each_days_own_record(tmp_path):
    """The recorded hash is read per day, so no lookup can leak another day's value."""
    out = tmp_path / "lane"
    digests = {"2023-05-15": "a" * 64, "2023-05-16": "b" * 64}
    for day, digest in digests.items():
        _, cpath = ads.state_paths(out, day)
        cpath.parent.mkdir(parents=True, exist_ok=True)
        cpath.write_text(json.dumps({"day": day, "states_sha256": digest}))
    assert ads.day_states_sha256(out, "2023-05-15") == "a" * 64
    assert ads.day_states_sha256(out, "2023-05-16") == "b" * 64
    assert ads.day_states_sha256(out, "2023-05-17") is None
    # an unreadable/absent recorded value falls back to hashing the states cov record
    _, cpath = ads.state_paths(out, "2023-05-18")
    cpath.parent.mkdir(parents=True, exist_ok=True)
    cpath.write_text(json.dumps({"day": "2023-05-18"}))
    fallback = hashlib.sha256(cpath.read_bytes()).hexdigest()
    assert ads.day_states_sha256(out, "2023-05-18") == fallback


@pytest.mark.skipif(
    not (ads.MODEL_DIR / f"payoff_h{ads.HEAD}.joblib").exists(),
    reason="the immutable stored head is not on disk in this environment",
)
def test_replay_coverage_records_each_days_own_states_hash(tmp_path):
    """The replay coverage writer recomputes states_sha256 PER DAY inside its own pass.

    The regression guard for the stale-carry-over defect: the writer must never record
    the hash a loop variable happened to hold from another day's lookup.
    """
    out = tmp_path / "lane"
    digests = {"2023-05-15": "a" * 64, "2023-05-16": "b" * 64}
    for day, digest in digests.items():
        qpath, cpath = ads.state_paths(out, day)
        qpath.parent.mkdir(parents=True, exist_ok=True)
        ads.empty_states().write_parquet(qpath)
        cpath.write_text(json.dumps({"day": day, "states_sha256": digest}))
    coverage = ads.replay_block(out, "validation", list(digests), False, ())
    assert coverage["days_replayed"] == 2
    written = {}
    for day in digests:
        record = json.loads((out / "replay" / "validation" / f"{day}.cov.json").read_text())
        written[day] = record["states_sha256"]
        assert record["states_sha256"] == digests[day]
    # the two days must not share one (the final day's) hash
    assert written["2023-05-15"] != written["2023-05-16"]


def _two_phase_book(high_from_minute: int = 661) -> dict[str, object]:
    """Dense fresh prints: the entry book (10.00/10.02) then a higher book (10.50/10.52)."""
    rows = []
    for minute in range(600, high_from_minute):
        for off in range(0, 60_000_000, 500_000):
            rows.append((minute, off, 10.0, 10.02, 100_000, 100_000, ("R",)))
    for minute in range(high_from_minute, 761):
        for off in range(0, 60_000_000, 500_000):
            rows.append((minute, off, 10.5, 10.52, 100_000, 100_000, ("R",)))
    return {"AAA": _stream(rows)}


def _full_surface(states, streams, days, day=DAY):
    """Every arm's cell over the same day frame (the real aggregation shape)."""
    daily_rows, trades = [], []
    for arm in ads.ARMS:
        daily, arm_trades = ads.replay_day(day, states, streams, arm)
        daily_rows.append(daily)
        trades.extend(arm_trades)
    frame = pl.DataFrame(daily_rows, schema={c: ads.DAILY_TYPES[c] for c in ads.DAILY_COLUMNS})
    trade_frame = (
        pl.DataFrame(trades, schema={c: ads.TRADE_TYPES[c] for c in ads.TRADE_COLUMNS})
        if trades
        else ads.empty_trades()
    )
    return {arm.key: ads.view_cell(frame, trade_frame, arm, days) for arm in ads.ARMS}


def test_baseline_comparison_states_the_intent_set_and_the_admission_asymmetry():
    """The fixed-size arm SKIPS what the displayed ask cannot carry; adaptive attempts it."""
    adaptive_arm = _arm(0.015, 0.10)
    states = _states_frame([_state_row("AAA", 600, ask_depth=50.0, bid_depth=50.0)])
    surface = _full_surface(states, _book_streams(["AAA"]), [DAY])
    comparison = ads.baseline_comparison(surface)[adaptive_arm.key]
    intent_set = comparison["intent_set"]
    assert intent_set["adaptive_attempts"] == 1
    assert intent_set["fixed_size_baseline_attempts"] == 0
    assert intent_set["adaptive_intent_depth_unsupported_skips"] == 0
    assert intent_set["fixed_size_baseline_intent_depth_unsupported_skips"] == 1
    assert "CAPPED DOWN and ATTEMPTED" in comparison["admission_rule"]["adaptive"]
    assert "SKIPPED" in comparison["admission_rule"]["fixed_size_baseline"]
    assert "NEVER" in comparison["admission_rule"]["fixed_size_baseline"]
    assert "NOT a like-for-like" in comparison["comparison_caveat"]
    assert "1 adaptive attempts vs 0 fixed-size attempts" in comparison["comparison_caveat"]
    # the per-day rates of the comparison carry the honest names
    assert "known_usd_per_panel_session_25" in comparison
    assert "known_usd_per_calendar_day_span_25" in comparison


def test_decision_text_carries_the_per_session_label_and_the_comparator_caveat():
    """The decision text names the real denominator and the comparator asymmetry."""
    adaptive_arm = _arm(0.015, 0.10)
    states = _states_frame([_state_row("AAA", 600, ask_depth=50.0, bid_depth=50.0)])
    surface = _full_surface(states, _two_phase_book(), [DAY])
    comparison = ads.baseline_comparison(surface)
    cell = surface[adaptive_arm.key]
    assert cell["known_fills"] == 1
    assert cell["known_usd_per_calendar_day"]["25"] > 0  # entry 10.02, exit bid 10.50
    text = ads.decision_text(
        adaptive_arm,
        cell,
        None,
        frozen=False,
        val_comparison=comparison,
        late_comparison=None,
    )
    assert "$/panel session" in text
    assert "$/calendar day" in text  # the span figure beside the session figure
    assert "NOT a like-for-like 'adaptive beats fixed' claim" in text
    assert "1 adaptive vs 0 fixed-size attempts on validation" in text
    assert "Late block not run (--skip-late)." in text
    # the same map shape with a late block: the late comparison entry is looked up by the
    # chosen arm's key inside decision_text, never indexed at the call site
    late_surface = _full_surface(states, _two_phase_book(), ["2023-05-16"], day="2023-05-16")
    frozen_text = ads.decision_text(
        adaptive_arm,
        cell,
        late_surface[adaptive_arm.key],
        frozen=True,
        val_comparison=comparison,
        late_comparison=ads.baseline_comparison(late_surface),
    )
    assert "$/panel session at 25bps (= " in frozen_text
    assert "on validation, 1 vs 0 on the late block" in frozen_text
    assert "per deployed dollar (validation " in frozen_text
