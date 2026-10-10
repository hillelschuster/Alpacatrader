"""Behavioral tests for the delayed SIP/IEX producer's feed, timing and IOC physics.

These cover the edges the delayed-free-feed formulation actually turns on:

* the delayed intent clock is t+1+delay (the normal next-minute intent, shifted by the
  feed delay), and the two predeclared exit conventions (the original t+61 minute, or a
  full hour from the ACTUAL delayed entry) with the RTH/half-day cap;
* the intent observation is IEX-ONLY: the quantity, the marketability limit, the L1
  liquidity and the spread bar all come from the latest RAW IEX print at/before the
  intent clock, and a missing IEX stream is an execution UNKNOWN - the consolidated
  NBBO NEVER substitutes for it and there is no IEX->SIP feature fallback;
* the 2s rule is an OPERATIONAL freshness gate, so every synthetic print that must be
  observed FRESH is placed inside the 2s window of its observation clock (a print a
  full minute early is stale and is reported as such - that is the policy, not a bug);
* the IOC LIMIT entry never rests: one observation of the full SIP state at intent
  +250ms decides it (fill at the actual SIP ASK within the IEX-based limit, an
  unfilled no-match above it, or an UNKNOWN for a stale / invalid / absent book);
* the MARKET exit submission is IEX-fresh-gated, prices from the SIP BID at the
  submission +250ms arrival, and - when the SIP book is invalid / unavailable - rests
  to the first eligible SIP print, releasing the position, its cash and its 15-minute
  cooldown at that ACTUAL rest clock, never at the planned arrival;
* the funded $3,000 book honours the slot, cash, one-position-per-ticker, 15-minute
  actual-exit cooldown, 3-attempts and simultaneous-clock reservation rules without any
  same-clock substitution after an unfilled intent;
* the panel reads use the REAL day-panel schema (entry_status, gross_15/gross_60/
  gross_390 with their exit_et_*/exit_status_* labels) and the actual Alpaca fee
  profiles are sourced and tiny, with the $0 Basic data expense reported apart from the
  $99 SIP benchmark;
* the immutable h60 model's pinned sha256 is enforced before anything is planned.

Synthetic frames only: no data file, no network, no live order.

Run:
  uv run --no-sync python -m pytest factory/scripts/test_alpha_delayed_sip_iex.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import joblib
import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import alpha_delayed_sip_iex as ads  # noqa: E402
from alpha_open_learned import FEATURES_ALL  # noqa: E402
from alpha_provider_fee_ledger import PROVIDERS  # noqa: E402

DAY = "2023-05-15"  # an allowed validation day present in the real panel
V15 = ads.VIEWS_BY_KEY[ads.view_key(0.015, 15, ads.EXIT_REMAINING)]
V15_FULL = ads.VIEWS_BY_KEY[ads.view_key(0.015, 15, ads.EXIT_FULL)]
V20 = ads.VIEWS_BY_KEY[ads.view_key(0.030, 20, ads.EXIT_REMAINING)]

# The observation clocks the synthetic prints must respect (UTC microseconds).
INTENT_516 = ads.minute_us(DAY, 516)  # the d15 delayed intent for t=500
ENTRY_ARRIVAL = INTENT_516 + 250_000  # intent + 250ms (the shared arrival latency)
DUE_561 = ads.minute_us(DAY, 561)  # the remaining-original-hour exit due clock
EXIT_ARRIVAL = DUE_561 + 250_000
FRESH = 100_000  # a print 0.1s before its observation clock is inside the 2s gate


# ----- synthetic quote substrate ----------------------------------------------
def _stamp(minute: int, us: int = 0) -> int:
    return ads.minute_us(DAY, minute) + int(us)


def _frame(rows, ticker: str) -> pl.DataFrame:
    """Raw quote rows keyed by ABSOLUTE stamp: (stamp_us, bid, ask, bsz, asz, conds)."""
    stamps, bid, ask, bsz, asz, conds = [], [], [], [], [], []
    for stamp_us, b, a, bs, ash, c in rows:
        stamps.append(int(stamp_us))
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


def _stream(rows, ticker: str):
    return ads.symbol_quotes(_frame(rows, ticker), DAY)[ticker]


def _dense(minutes, *, bid=10.00, ask=10.02, size=10_000.0, ticker="TEST", step_us=100_000):
    """One R print every ``step_us`` across the given minutes (always fresh)."""
    rows = []
    for minute in minutes:
        for off in range(0, 60_000_000, step_us):
            rows.append((_stamp(minute, off), bid, ask, size, size, ("R",)))
    return _stream(rows, ticker)


def _entry_print(ask: float = 10.03, bid: float = 10.00) -> tuple:
    """A fresh SIP print 0.1s before the entry arrival clock."""
    return (INTENT_516 - FRESH, bid, ask, 5_000.0, 5_000.0, ("R",))


def _exit_print(bid: float = 10.08, ask: float = 10.09) -> tuple:
    """A fresh SIP print at the remaining-hour exit due clock."""
    return (DUE_561, bid, ask, 5_000.0, 5_000.0, ("R",))


# ----- synthetic scored-state rows --------------------------------------------
def _state(
    ticker: str,
    t: int,
    view: ads.View = V15,
    *,
    pred: float = 0.05,
    rule_ok: bool = True,
    intent_status: str = "quoted",
    intent_reason: str = "eligible",
    ask: float = 10.02,
    session_end: int = 959,
    gross_60: float | None = 0.01,
) -> dict:
    delay = view.delay_min
    limit = ads.limit_price_of(ask)
    qty = ads.delayed_quantity(limit)
    return {
        "day": DAY,
        "view_key": view.key,
        "threshold": view.threshold,
        "delay_min": int(delay),
        "exit_convention": view.exit_convention,
        "ticker": ticker,
        "t": int(t),
        "pred_60": pred,
        "session_end": session_end,
        "entry_status": "filled_proxy",
        "gross_15": 0.005,
        "exit_et_15": int(t) + 15,
        "exit_status_15": "observed_open_proxy",
        "gross_60": gross_60,
        "exit_et_60": int(t) + 60,
        "exit_status_60": "observed_open_proxy",
        "gross_390": 0.02,
        "exit_et_390": int(t) + 390,
        "exit_status_390": "observed_open_proxy",
        "intent_minute": ads.intent_minute_of(t, delay),
        "intent_us": _stamp(ads.intent_minute_of(t, delay)),
        "intent_status": intent_status,
        "intent_reason": intent_reason,
        "rule_ok": rule_ok,
        "intent_ask": ask,
        "intent_bid": ask - 0.02,
        "intent_age_s": 0.05,
        "intent_spread_bps": (ask / (ask - 0.02) - 1.0) * 10_000.0,
        "intent_ask_shares": 1_000_000.0,
        "limit_price": limit,
        "qty": qty,
        "reserved_usd": ads.reserved_usd(qty, limit, ads.MAX_RUNG_COST),
        "exit_minute": ads.exit_minute_of(t, delay, view.exit_convention, session_end),
        "exit_due_us": _stamp(ads.exit_minute_of(t, delay, view.exit_convention, session_end)),
    }


def _states_frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(
        {c: [r[c] for r in rows] for c in ads.STATE_COLUMNS},
        schema={c: ads.STATE_TYPES[c] for c in ads.STATE_COLUMNS},
    )


def _trade(ticker: str, entry_ask: float, exit_bid: float, qty: int, view: ads.View = V15) -> dict:
    row = dict.fromkeys(ads.TRADE_COLUMNS)
    row.update(
        {
            "day": DAY,
            "view_key": view.key,
            "threshold": view.threshold,
            "delay_min": int(view.delay_min),
            "exit_convention": view.exit_convention,
            "ticker": ticker,
            "t": 500,
            "pred_60": 0.05,
            "intent_minute": ads.intent_minute_of(500, view.delay_min),
            "intent_us": _stamp(ads.intent_minute_of(500, view.delay_min)),
            "limit_price": ads.limit_price_of(10.02),
            "qty": qty,
            "reserved_usd": 1000.0,
            "attempt_index": 1,
            "positions_at_entry": 1,
            "entry_status": "conditional_ioc_fill_sip_benchmark",
            "entry_ask": entry_ask,
            "entry_bid": entry_ask - 0.02,
            "entry_age_s": 0.1,
            "entry_quote_us": 0,
            "entry_arrival_us": 0,
            "exit_minute": 561,
            "exit_due_us": 0,
            "exit_submit_us": 0,
            "exit_submit_status": "submit_at_due_intent_firm_fresh",
            "exit_arrival_us": 0,
            "exit_bid": exit_bid,
            "exit_age_s": 0.1,
            "exit_quote_us": 0,
            "exit_price_status": "priced_fresh_at_arrival",
            "exit_status": "conditional_market_fill_sip_benchmark",
            "actual_exit_us": 0,
            "fill_status": "conditional_fill",
            "gross_60": 0.01,
            "exit_status_60": "observed_open_proxy",
        }
    )
    for rung in ads.RUNG_COSTS:
        row[f"net_usd_{int(rung)}"] = ads.net_usd(qty, exit_bid, entry_ask, rung)
    return row


def _trades_frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(
        {c: [r[c] for r in rows] for c in ads.TRADE_COLUMNS},
        schema={c: ads.TRADE_TYPES[c] for c in ads.TRADE_COLUMNS},
    )


# ----- predeclared view grid and clock math ------------------------------------
def test_twelve_predeclared_views_are_a_stable_grid():
    assert len(ads.VIEWS) == 12
    assert len({v.key for v in ads.VIEWS}) == 12
    assert len({v.label for v in ads.VIEWS}) == 12
    assert {v.threshold for v in ads.VIEWS} == set(ads.THRESHOLDS)
    assert {v.delay_min for v in ads.VIEWS} == set(ads.DELAYS_MIN)
    assert {v.exit_convention for v in ads.VIEWS} == set(ads.EXIT_CONVENTIONS)
    # 0.015 as a fraction is 150 bps, so the key/label carry thr150bps (not 1500)
    assert ads.view_key(0.015, 15, ads.EXIT_REMAINING) == "thr150bps_d15_remaining_original_hour"
    assert ads.view_label(0.015, 15, ads.EXIT_FULL) == "thr150bps_d15min_full60"
    assert ads.view_key(0.030, 20, ads.EXIT_REMAINING) == "thr300bps_d20_remaining_original_hour"
    assert V15_FULL.exit_convention == ads.EXIT_FULL
    assert V20.threshold == 0.030 and V20.delay_min == 20


def test_delayed_intent_and_exit_clock_math():
    assert ads.intent_minute_of(500, 15) == 516
    assert ads.intent_minute_of(500, 20) == 521
    # the two predeclared exit conventions
    assert ads.exit_minute_of(500, 15, ads.EXIT_REMAINING, 959) == 561  # original t+61
    assert ads.exit_minute_of(500, 20, ads.EXIT_REMAINING, 959) == 561  # delay-free
    assert ads.exit_minute_of(500, 15, ads.EXIT_FULL, 959) == 576  # (t+1+15)+60
    assert ads.exit_minute_of(500, 20, ads.EXIT_FULL, 959) == 581  # (t+1+20)+60
    # RTH / half-day cap
    assert ads.exit_minute_of(940, 15, ads.EXIT_REMAINING, 959) == 959
    assert ads.exit_minute_of(940, 15, ads.EXIT_FULL, 959) == 959
    assert ads.exit_minute_of(760, 15, ads.EXIT_FULL, 780) == 780
    with pytest.raises(ValueError):
        ads.exit_minute_of(500, 15, "nonsense", 959)


def test_request_window_covers_intents_exits_and_session_close():
    start, end = ads.request_window(DAY, 516, 621, 959)
    assert start == ads.minute_us(DAY, 516) - int(ads.FETCH_WINDOW_BEFORE_S * 1_000_000)
    assert end == ads.minute_us(DAY, 622)  # one minute past the latest exit observation
    close_start, close_end = ads.request_window(DAY, 516, 959, 959)
    assert close_end == ads.minute_us(DAY, 960)  # a resting exit submission reaches the close
    half_start, half_end = ads.request_window(DAY, 516, 700, 780)
    assert half_end == ads.minute_us(DAY, 701)  # capped at the half-day close + 1


def test_delayed_sizing_is_funded_at_the_150bps_rung():
    limit = ads.limit_price_of(10.02)
    assert limit == 10.13  # ceil_to_cent(10.02 * 1.01): the marketability cap, not a fee
    qty = ads.delayed_quantity(limit)
    assert qty == int(ads.ORDER_BUDGET // (limit * (1.0 + 150.0 / 20_000.0)))
    assert qty >= 1
    reserved = ads.reserved_usd(qty, limit, ads.MAX_RUNG_COST)
    assert reserved <= ads.ORDER_BUDGET
    assert reserved == pytest.approx(qty * limit * (1.0 + 150.0 / 20_000.0))


# ----- IEX-only intent observability -------------------------------------------
def test_intent_observation_is_iex_only_and_never_a_future_print():
    sq = _stream(
        [
            (INTENT_516 - FRESH, 10.00, 10.02, 5_000.0, 5_000.0, ("R",)),
            (ads.minute_us(DAY, 517), 10.00, 9.00, 5_000.0, 5_000.0, ("R",)),  # AFTER intent
        ],
        "TEST",
    )
    facts = ads.delayed_intent_facts(sq, INTENT_516)
    assert facts["intent_status"] == "quoted"
    assert facts["intent_ask"] == 10.02  # the pre-intent observation, never the later print
    assert facts["limit_price"] == 10.13
    reason, ok = ads.entry_rule_status(facts, 0.05)
    assert (reason, ok) == ("eligible", True)


def test_missing_iex_stream_is_unknown_never_sip_substitution():
    facts = ads.delayed_intent_facts(None, INTENT_516)
    assert facts["intent_status"] == "quote_not_acquired"
    assert ads.entry_rule_status(facts, 0.05) == ("quote_not_acquired", False)
    # a rich consolidated tape changes nothing: no IEX stream -> no intent at all
    sip_streams = {"TEST": _dense([515, 516, 560, 561], ticker="TEST")}
    states = _states_frame(
        [
            _state(
                "TEST",
                500,
                rule_ok=False,
                intent_status="quote_not_acquired",
                intent_reason="quote_not_acquired",
            )
        ]
    )
    daily, trades = ads.replay_day(DAY, states, {}, sip_streams, V15)
    assert trades == []
    assert daily["attempts"] == 0
    assert daily["iex_quote_not_acquired"] == 1
    assert daily["iex_intent_not_acquired"] == 1
    for rung in ads.RUNG_COSTS:
        assert daily[f"known_usd_{int(rung)}"] == 0.0


def test_stale_iex_intent_is_not_firm_fresh():
    # the 2s rule is an OPERATIONAL gate: a print a full minute early is stale
    sq = _stream([(ads.minute_us(DAY, 515), 10.00, 10.02, 5_000.0, 5_000.0, ("R",))], "TEST")
    facts = ads.delayed_intent_facts(sq, INTENT_516)
    assert facts["intent_status"] == "quoted"
    assert facts["intent_age_s"] == pytest.approx(60.0)
    reason, ok = ads.entry_rule_status(facts, 0.05)
    assert (reason, ok) == ("intent_not_firm_fresh", False)
    assert ads.daily_reason_key(reason) == "iex_intent_not_firm_fresh"


def test_spread_bar_uses_the_iex_observation_against_the_forecast():
    wide = _stream([(INTENT_516 - FRESH, 10.00, 10.50, 5_000.0, 5_000.0, ("R",))], "TEST")
    facts = ads.delayed_intent_facts(wide, INTENT_516)
    assert facts["intent_spread_bps"] == pytest.approx(500.0)
    assert ads.entry_rule_status(facts, 0.02) == ("spread_above_prediction", False)
    assert ads.entry_rule_status(facts, 0.10) == ("eligible", True)


# ----- IOC entry: one observation at intent + 250ms, never a rest -------------
def _entry_rec(sip_rows, state=None, view=V15, iex_rows=None):
    r = state if state is not None else _state("TEST", 500, view=view)
    iex = _stream(iex_rows, "TEST") if iex_rows is not None else _dense([560], ticker="TEST")
    sip = _stream(sip_rows, "TEST")
    return r, ads.resolve_delayed_trade(
        DAY, r, view, iex, sip, 959, ads.minute_us(DAY, 959)
    )


def test_ioc_entry_fills_at_the_sip_ask_within_the_iex_limit():
    r, rec = _entry_rec([_entry_print(), _exit_print()])
    assert rec["entry_status"] == "conditional_ioc_fill_sip_benchmark"
    assert rec["entry_ask"] == 10.03  # the FULL SIP benchmark ASK, not the IEX 10.02
    assert rec["entry_arrival_us"] == ENTRY_ARRIVAL
    assert rec["exit_submit_status"] == "submit_at_due_intent_firm_fresh"
    assert rec["exit_bid"] == 10.08
    assert rec["actual_exit_us"] == EXIT_ARRIVAL
    assert rec["fill_status"] == "conditional_fill"
    assert rec["net_usd_0"] == pytest.approx(r["qty"] * (10.08 - 10.03))


def test_ioc_entry_never_rests_on_a_stale_sip_book():
    _, rec = _entry_rec(
        [
            (ads.minute_us(DAY, 513), 10.00, 10.03, 5_000.0, 5_000.0, ("R",)),  # stale
            (ads.minute_us(DAY, 517), 10.00, 9.90, 5_000.0, 5_000.0, ("R",)),  # after arrival
        ]
    )
    assert rec["fill_status"] == "unknown_entry_execution"
    assert rec["entry_status"] == "unknown_stale_but_valid_at_arrival"
    assert rec["entry_ask"] is None  # the later print is never used to fill an IOC
    assert rec["exit_submit_us"] is None
    assert rec["actual_exit_us"] == ads.minute_us(DAY, 959) + 1  # held to the session end


def test_ioc_entry_never_rests_when_the_sip_book_is_absent():
    _, rec = _entry_rec([(ads.minute_us(DAY, 520), 10.00, 10.03, 5_000.0, 5_000.0, ("R",))])
    assert rec["entry_status"] == "no_prior_quote"
    assert rec["fill_status"] == "unknown_entry_execution"


def test_sip_ask_above_the_iex_limit_is_unfilled_cash():
    _, rec = _entry_rec([_entry_print(ask=10.50)])
    assert rec["entry_status"] == "no_match_at_l1"  # 10.50 > the 10.13 IEX-based limit
    assert rec["fill_status"] == "no_match_at_l1_unfilled_cash"
    assert rec["actual_exit_us"] == ENTRY_ARRIVAL  # cash returns at the arrival
    assert rec["exit_submit_us"] is None
    assert rec["net_usd_0"] is None


def test_sip_depth_short_of_the_causal_quantity_is_unknown():
    r = _state("TEST", 500, ask=200.00)
    assert r["qty"] == 4  # floor(1000 / (202.01 * 1.0075))
    _, rec = _entry_rec(
        [(INTENT_516 - FRESH, 199.00, 199.50, 5_000.0, 0.01, ("R",))], state=r
    )
    assert rec["entry_status"] == "unknown_entry_partial_depth"
    assert rec["fill_status"] == "unknown_entry_execution"
    assert rec["entry_ask"] == 199.50  # observed, but only one displayed share of support
    assert rec["actual_exit_us"] == ads.minute_us(DAY, 959) + 1


def test_missing_sip_benchmark_stream_is_unknown_never_cash():
    # the ranked SIP cache covers only part of the qualified universe
    r = _state("TEST", 500)
    iex = _dense([560], ticker="TEST")
    rec = ads.resolve_delayed_trade(DAY, r, V15, iex, None, 959, ads.minute_us(DAY, 959))
    assert rec["entry_status"] == "sip_quote_not_acquired"
    assert rec["fill_status"] == "unknown_entry_execution"
    assert rec["actual_exit_us"] == ads.minute_us(DAY, 959) + 1


# ----- MARKET exit: IEX-fresh-gated submission, SIP-BID pricing ---------------
def test_exit_submission_waits_for_the_first_iex_regular_after_the_due_clock():
    _, rec = _entry_rec(
        [_entry_print(), (ads.minute_us(DAY, 563), 10.08, 10.09, 5_000.0, 5_000.0, ("R",))],
        iex_rows=[
            (INTENT_516 - FRESH, 10.00, 10.02, 5_000.0, 5_000.0, ("R",)),
            (ads.minute_us(DAY, 563), 10.00, 10.02, 5_000.0, 5_000.0, ("R",)),
        ],
    )
    assert rec["exit_submit_status"] == "submit_at_first_regular_after_due_intent"
    assert rec["exit_submit_us"] == ads.minute_us(DAY, 563)
    assert rec["exit_arrival_us"] == ads.minute_us(DAY, 563) + 250_000
    assert rec["exit_bid"] == 10.08
    assert rec["actual_exit_us"] == rec["exit_arrival_us"]


def test_exit_rests_on_sip_and_releases_on_the_rest_clock():
    r = _state("TEST", 500)
    iex = _dense([560], ticker="TEST")
    sip = _stream(
        [
            _entry_print(),
            (DUE_561, 10.20, 10.10, 5_000.0, 5_000.0, ("R",)),  # crossed: invalid at arrival
            (ads.minute_us(DAY, 571), 10.08, 10.09, 5_000.0, 5_000.0, ("R",)),  # first valid
        ],
        "TEST",
    )
    rec = ads.resolve_delayed_trade(DAY, r, V15, iex, sip, 959, ads.minute_us(DAY, 959))
    assert rec["entry_status"] == "conditional_ioc_fill_sip_benchmark"
    assert rec["exit_submit_status"] == "submit_at_due_intent_firm_fresh"
    assert rec["exit_price_status"] == "priced_rested_until_regular"
    assert rec["exit_bid"] == 10.08
    assert rec["actual_exit_us"] == ads.minute_us(DAY, 571)  # the ACTUAL market rest clock
    assert rec["net_usd_0"] == pytest.approx(r["qty"] * (10.08 - 10.03))
    # the 15-minute cooldown anchors on the rest clock, not the planned arrival
    later = _state("TEST", 561)  # intent minute 577: inside 15 min of the 571 rest clock
    daily, trades = ads.replay_day(
        DAY, _states_frame([r, later]), {"TEST": iex}, {"TEST": sip}, V15
    )
    assert daily["attempts"] == 1
    assert daily["cooldown_skips"] == 1
    assert trades[0]["actual_exit_us"] == ads.minute_us(DAY, 571)


# ----- funded book, schemas, sourcing ------------------------------------------
def test_book_slots_cash_and_same_clock_reservation():
    states = _states_frame(
        [
            _state("AAA", 500, pred=0.09),  # funded first; its entry UNKNOWNs at arrival
            _state("BBB", 500, pred=0.05),
            _state("CCC", 500, pred=0.04),
            _state("DDD", 500, pred=0.03),
        ]
    )
    iex = {t: _dense([560], ticker=t) for t in ("AAA", "BBB", "CCC", "DDD")}
    sip = {
        # AAA: no acquired SIP stream at all -> UNKNOWN, its slot still held
        "AAA": None,
        "BBB": _stream([_entry_print(), _exit_print()], "BBB"),
        "CCC": _stream([_entry_print(), _exit_print()], "CCC"),
        "DDD": _stream([_entry_print(), _exit_print()], "DDD"),
    }
    daily, trades = ads.replay_day(DAY, states, iex, sip, V15)
    assert daily["intents_funded"] == 3  # three $1,000 tickets on the $3,000 book
    assert daily["attempts"] == 3
    assert daily["positions_peak"] == 3
    assert daily["cash_or_slot_skips"] == 1  # DDD is NOT substituted in after AAA unknowns
    assert daily["unknown_fills"] == 1
    assert daily["known_fills"] == 2
    assert sorted(t["ticker"] for t in trades) == ["AAA", "BBB", "CCC"]


def test_attempt_cap_and_one_position_per_ticker():
    # four sequential, non-overlapping attempts on ONE ticker (15-min cooldown honoured)
    states = _states_frame([_state("TTT", t) for t in (500, 561, 622, 698)])
    iex = {"TTT": _dense([560, 621, 682], ticker="TTT")}
    sip = {
        "TTT": _stream(
            [
                (INTENT_516 - FRESH, 10.00, 10.03, 5_000.0, 5_000.0, ("R",)),
                (DUE_561, 10.08, 10.09, 5_000.0, 5_000.0, ("R",)),
                (ads.minute_us(DAY, 577) - FRESH, 10.00, 10.03, 5_000.0, 5_000.0, ("R",)),
                (ads.minute_us(DAY, 622), 10.08, 10.09, 5_000.0, 5_000.0, ("R",)),
                (ads.minute_us(DAY, 638) - FRESH, 10.00, 10.03, 5_000.0, 5_000.0, ("R",)),
                (ads.minute_us(DAY, 683), 10.08, 10.09, 5_000.0, 5_000.0, ("R",)),
            ],
            "TTT",
        )
    }
    daily, trades = ads.replay_day(DAY, states, iex, sip, V15)
    assert daily["attempts"] == 3
    assert daily["max_attempt_skips"] == 1  # the fourth attempt is never sent
    assert daily["overlap_skips"] == 0
    assert daily["cooldown_skips"] == 0
    assert daily["known_fills"] == 3
    assert [t["attempt_index"] for t in trades] == [1, 2, 3]


def test_empty_artifacts_carry_the_declared_schemas():
    assert ads.empty_states().columns == list(ads.STATE_COLUMNS)
    assert ads.empty_daily().columns == list(ads.DAILY_COLUMNS)
    assert ads.empty_trades().columns == list(ads.TRADE_COLUMNS)
    # the real panel label columns the consumer contract reads
    for col in (
        "entry_status",
        "gross_15",
        "exit_et_15",
        "exit_status_15",
        "gross_60",
        "exit_et_60",
        "exit_status_60",
        "gross_390",
        "exit_et_390",
        "exit_status_390",
    ):
        assert col in ads.STATE_COLUMNS
    for rung in ads.RUNG_COSTS:
        assert f"known_usd_{int(rung)}" in ads.DAILY_COLUMNS
        assert f"net_usd_{int(rung)}" in ads.TRADE_COLUMNS
    daily, trades = ads.replay_day(DAY, ads.empty_states(), {}, {}, V15)
    assert trades == []
    assert daily["signals"] == 0 and daily["attempts"] == 0
    for rung in ads.RUNG_COSTS:
        assert daily[f"known_usd_{int(rung)}"] == 0.0


def test_score_refuses_without_a_planned_manifest(tmp_path):
    # the score stage never invents a signal set: the predetermined manifest comes first
    with pytest.raises(SystemExit):
        ads.score_block(tmp_path, [DAY], False, ())


def test_actual_alpaca_fees_are_sourced_and_tiny_on_these_tickets():
    qty = 97
    trades = _trades_frame(
        [
            _trade("AAA", 10.03, 10.08, qty),
            _trade("BBB", 5.02, 5.06, qty),
        ]
    )
    scen = ads.fee_scenarios(trades, 250)
    assert set(ads.FEE_PROFILE_IDS) <= set(PROVIDERS)
    current = scen["alpaca_current_2026q4"]
    assert current["provider"] == "alpaca" and current["plan"] == "alpaca_retail"
    assert current["n_known_fills"] == 2
    assert 0.0 < current["fee_rt_bps_of_two_sided_notional"] < 1.0
    assert current["fee_posted_eod_total_usd"] >= current["fee_raw_total_usd"]
    net0 = ads.net_usd(qty, 10.08, 10.03, 0.0) + ads.net_usd(qty, 5.06, 5.02, 0.0)
    assert current["net_after_fees_usd_total"] == pytest.approx(
        net0 - current["fee_raw_total_usd"]
    )
    taf = scen["alpaca_taf_jan2027"]
    assert taf["scenario"] == "taf_jan2027"
    assert taf["fee_raw_total_usd"] > current["fee_raw_total_usd"]


def test_data_expense_is_free_basic_apart_from_the_sip_benchmark():
    expense = ads.data_expense_block()
    assert expense["current_assumption_usd_per_year"] == 0.0
    assert expense["sip_benchmark_usd_per_year"] == 1188.0
    assert expense["sip_benchmark_usd_per_replayed_trading_day"] == pytest.approx(1188.0 / 252.0)
    assert expense["shared_once_per_portfolio"] is True
    lines = ads.income_minus_shared_opex(10.0)
    assert lines["basic_free_data_expense_usd_per_calendar_day"] == 10.0
    assert lines["sip_benchmark_data_expense_usd_per_calendar_day"] == pytest.approx(
        10.0 - 1188.0 / 252.0
    )


def _fake_bundle(**over):
    bundle = {
        "feature_order": list(FEATURES_ALL),
        "horizon": ads.HEAD,
        "lgbm": {},
        "params": {"objective": "regression"},
        "clip_fit": (-0.5, 2.0),
        "seed": 1,
    }
    bundle.update(over)
    return bundle


def test_model_sha_pin_is_enforced(tmp_path, monkeypatch):
    path = tmp_path / "payoff_h60.joblib"
    joblib.dump(_fake_bundle(), path)
    monkeypatch.setattr(ads, "MODEL_PATH", path)
    monkeypatch.setattr(ads, "MODEL_SHA256", ads.sha256_file(path))
    models, report = ads.load_stored_model()
    assert report["horizon"] == ads.HEAD
    assert report["n_features"] == len(FEATURES_ALL)
    # the loader hands the consumers the head-keyed map day_manifest indexes
    assert set(models) == {ads.HEAD}
    booster = ads.stored_booster(models)
    assert booster is models[ads.HEAD]["lgbm"]
    # a genuinely missing head / booster is a clear SystemExit, never a KeyError
    with pytest.raises(SystemExit):
        ads.stored_booster({15: _fake_bundle(horizon=15)})
    with pytest.raises(SystemExit):
        ads.stored_booster({60: {"horizon": 60}})
    monkeypatch.setattr(ads, "MODEL_SHA256", "0" * 64)
    with pytest.raises(SystemExit):
        ads.load_stored_model()
    joblib.dump(_fake_bundle(horizon=15), path)
    monkeypatch.setattr(ads, "MODEL_SHA256", ads.sha256_file(path))
    with pytest.raises(SystemExit):
        ads.load_stored_model()
    joblib.dump(_fake_bundle(feature_order=["x"]), path)
    monkeypatch.setattr(ads, "MODEL_SHA256", ads.sha256_file(path))
    with pytest.raises(SystemExit):
        ads.load_stored_model()
