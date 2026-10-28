"""Behavioral tests for the opening-range pullback producer's uncertain edges.

Covers the strict-past qualification state machine, the one-shot IOC entry pricing
(an IOC limit never rests), the MARKET exit's ACTUAL returned price clock (funding
must wait the real execution clock, never a lookahead-early release), the
first-attempt book semantics and the optional real-forecast gate's honesty.
"""

from __future__ import annotations

import sys

import polars as pl
import pytest

sys.path.insert(0, ".")
sys.path.insert(0, "factory/scripts")

import alpha_opening_range_pullback as orp  # noqa: E402
from alpha_quote_audit import clock_us  # noqa: E402

DAY = "2023-05-15"


def _us(minute: int, ms: int = 0) -> int:
    return clock_us(DAY, minute, ms)


def _tape(rows, ticker: str = "TEST") -> orp.Tape:
    """Minute bars: ``(minute, open, high, low, close, volume)``; 09:30..09:59."""
    frame = pl.DataFrame(
        [
            {
                "ticker": ticker,
                "et": int(m),
                "open": float(o),
                "high": float(h),
                "low": float(low),
                "close": float(c),
                "volume": float(v),
            }
            for (m, o, h, low, c, v) in rows
        ]
    ).sort("et")
    return orp.build_tape(DAY, ticker, frame)


def _or_bars(*, high=10.0, low=9.5, close=9.75, volume=200_000.0, skip=()):
    """The first 15 minutes; a skipped minute simply carries no trade bar."""
    return [(m, close, high, low, close, volume) for m in range(570, 585) if m not in set(skip)]


def _quote_rows(rows, ticker: str = "TEST"):
    """Raw quote rows: ``(minute, second_ms, bid, ask, bid_size, ask_size, conditions)``."""
    stamps = [clock_us(DAY, int(m), int(ms)) for (m, ms, bid, ask, bs, as_, cond) in rows]
    return pl.DataFrame(
        {
            "symbol": [ticker] * len(rows),
            "ts_utc": stamps,
            "bid_price": [float(r[2]) for r in rows],
            "bid_size": [float(r[4]) for r in rows],
            "ask_price": [float(r[3]) for r in rows],
            "ask_size": [float(r[5]) for r in rows],
            "bid_exchange": ["Q"] * len(rows),
            "ask_exchange": ["Q"] * len(rows),
            "conditions": [tuple(r[6]) for r in rows],
            "tape": ["A"] * len(rows),
        }
    ).sort("ts_utc")


def _stream(rows, ticker: str = "TEST"):
    return orp.aqf.symbol_quotes(_quote_rows(rows, ticker), DAY)[ticker]


def _dense(minutes, *, bid=10.0, ask=10.0, size=100_000.0, ticker="TEST", step_ms=500):
    """One fresh R print every ``step_ms`` across the given minutes."""
    rows = []
    for m in minutes:
        for k in range(0, 60_000, step_ms):
            rows.append((m, k, bid, ask, size, size, ("R",)))
    return _stream(rows, ticker)


def _dense_rows(minutes, *, bid=10.0, ask=10.02, size=100_000.0, step_ms=500):
    """The same print grid as ``_dense`` as RAW rows, for appending edge prints to."""
    return [
        (m, k, bid, ask, size, size, ("R",)) for m in minutes for k in range(0, 60_000, step_ms)
    ]


# ----- strict-past qualification state machine --------------------------------
def _break_scenario(
    *,
    breakout=(586, 9.8, 10.5, 9.75, 10.2, 400_000.0),
    retest=(588, 9.95, 10.2, 9.8, 10.05, 300_000.0),
):
    """The predeclared both-qualification day: the first 15m high is 10.00, bar 586
    breaks it with a confirmed positive bar, and bar 588 is the first causal retest
    (low 9.80 <= open VWAP 9.8229 < close 10.05) with contraction and a confirmed
    positive prior bar (open 9.95 < close 10.05)."""
    rows = _or_bars()
    rows.append(breakout)
    if retest is not None:
        rows.append(retest)
    return _tape(rows)


def test_pullback_signal_uses_only_bars_strictly_before_the_entry_minute():
    tape = _break_scenario()
    signals, reasons = orp.tape_signals(tape, admit_t=575, session_end=959)
    by_qual = {s["qual"]: s for s in signals}
    assert set(by_qual) == {"direct_breakout", "pullback_retest"}
    direct, pullback = by_qual["direct_breakout"], by_qual["pullback_retest"]
    # Clock chain: a bar stamped m closes at m+1 (the decision minute); the funded
    # IOC intent is the NEXT minute clock (entry minute = signal_bar + 2).
    assert direct["breakout_minute"] == 586
    assert direct["decision_minute"] == 587 and direct["entry_minute"] == 588
    assert direct["signal_bar_minute"] == 586 and direct["signal_minute"] == 587
    assert pullback["breakout_minute"] == 586
    assert pullback["signal_bar_minute"] == 588
    assert pullback["decision_minute"] == 589 and pullback["entry_minute"] == 590
    assert pullback["signal_minute"] == 589
    # the OR is the first 15 minutes only; the future day high (12:00) is never read.
    assert direct["or_high"] == pytest.approx(10.0)
    assert direct["or_low"] == pytest.approx(9.5)
    assert direct["signal_ret"] == pytest.approx(10.2 / 9.8 - 1.0)
    assert pullback["signal_ret"] == pytest.approx(10.05 / 9.95 - 1.0)
    # the retest bar's low dips to (at most) the open VWAP while the close holds above.
    assert pullback["retest_depth"] <= 0.0
    assert pullback["vwap_dist_signal"] > 0.0
    # volume contraction versus the breakout bar, carried as a ratio.
    assert pullback["volume_ratio"] == pytest.approx(300_000.0 / 400_000.0)
    assert reasons["pullback_signal"] == 1 and reasons["direct_signal"] == 1


def test_no_post_0945_break_of_the_first_15m_high_means_no_signal():
    rows = _or_bars()
    rows.append((586, 9.8, 9.9, 9.7, 9.85, 400_000.0))  # never exceeds the OR high
    tape = _tape(rows)
    signals, reasons = orp.tape_signals(tape, admit_t=575, session_end=959)
    assert signals == [] and reasons["no_post_0945_breakout"] == 1


def test_the_break_must_be_the_first_minute_after_0945_to_count_as_first():
    # a 09:44 excursion is INSIDE the first 15m: it raises the OR high itself.
    rows = _or_bars(high=10.0)
    rows[14] = (584, 9.8, 10.4, 9.7, 10.3, 400_000.0)
    rows.append((586, 10.2, 10.3, 10.0, 10.25, 400_000.0))  # no break of 10.40
    tape = _tape(rows)
    signals, reasons = orp.tape_signals(tape, admit_t=575, session_end=959)
    assert signals == [] and reasons["no_post_0945_breakout"] == 1


def test_first_retest_only_a_failed_filter_never_waits_for_a_later_retest():
    rows = _or_bars()
    rows.append((586, 9.8, 10.5, 9.75, 10.2, 400_000.0))  # breakout
    rows.append((588, 10.1, 10.2, 9.8, 10.05, 500_000.0))  # retest WITHOUT contraction
    rows.append((590, 10.05, 10.1, 9.85, 10.08, 100_000.0))  # a later, quieter retest
    tape = _tape(rows)
    signals, reasons = orp.tape_signals(tape, admit_t=575, session_end=959)
    assert [s["qual"] for s in signals] == ["direct_breakout"]
    assert reasons["pullback_volume_not_contracted"] == 1
    assert reasons["pullback_signal"] == 0


def test_retest_requires_a_touch_and_hold_above_the_open_vwap():
    rows = _or_bars()
    rows.append((586, 9.8, 10.5, 9.75, 10.2, 400_000.0))
    # close back BELOW the open VWAP is not a hold, so that bar is not a retest.
    rows.append((588, 10.0, 10.05, 9.6, 9.7, 300_000.0))  # touches (low 9.60) but closes below
    # a later bar holds above the VWAP but its low stays ABOVE it, so it never touched:
    # a retest requires a touch AND a hold, and neither bar is one.
    rows.append((590, 9.9, 9.95, 9.85, 9.93, 100_000.0))  # low 9.85 > open VWAP 9.798
    tape = _tape(rows)
    signals, reasons = orp.tape_signals(tape, admit_t=575, session_end=959)
    assert "pullback_retest" not in {s["qual"] for s in signals}
    assert reasons["pullback_retest_not_found"] == 1


def test_confirmed_positive_prior_bar_is_required_for_both_qualifications():
    rows = _or_bars()
    rows.append((586, 9.8, 10.5, 9.75, 9.7, 400_000.0))  # negative breakout bar
    # a genuine retest (low 9.60 <= open VWAP < close 10.05) that is still a negative
    # bar: the confirmed-positive-prior-bar gate must reject it on its own.
    rows.append((588, 10.1, 10.2, 9.6, 10.05, 300_000.0))
    tape = _tape(rows)
    signals, reasons = orp.tape_signals(tape, admit_t=575, session_end=959)
    assert signals == []
    assert reasons["direct_prior_bar_not_positive"] == 1
    assert reasons["pullback_prior_bar_not_positive"] == 1


def test_entry_window_close_blocks_an_1100_break_and_admission_gates_the_decision():
    rows = _or_bars()
    # a break at bar 660 (11:00) closes at 661: the decision minute is outside the
    # 09:45-11:00 decision window, so the qualification is explicitly skipped.
    rows.append((660, 9.8, 10.5, 9.75, 10.2, 400_000.0))
    tape = _tape(rows)
    signals, reasons = orp.tape_signals(tape, admit_t=575, session_end=959)
    assert signals == [] and reasons["direct_decision_after_window"] == 1
    # a break at bar 659 (10:59) decides at 660 and is funded at 661: in-window.
    rows2 = _or_bars()
    rows2.append((659, 9.8, 10.5, 9.75, 10.2, 400_000.0))
    tape2 = _tape(rows2)
    signals2, _ = orp.tape_signals(tape2, admit_t=575, session_end=959)
    assert [s["entry_minute"] for s in signals2 if s["qual"] == "direct_breakout"] == [661]

    tape2 = _break_scenario()
    signals2, reasons2 = orp.tape_signals(tape2, admit_t=700, session_end=959)
    assert signals2 == []
    assert reasons2["direct_before_admission"] == 1
    assert reasons2["pullback_before_admission"] == 1


def test_first_15m_reference_needs_the_declared_observed_bar_floor():
    sparse = _tape(_or_bars(skip=tuple(range(571, 580))))  # only 6 opening bars
    signals, reasons = orp.tape_signals(sparse, admit_t=575, session_end=959)
    assert signals == [] and reasons["insufficient_first_15m_bars"] == 1

    ok = _tape(
        _or_bars(skip=(572, 573, 574, 575, 576))  # exactly 10 opening bars
        + [
            (586, 9.8, 10.5, 9.75, 10.2, 400_000.0),  # the first post-09:45 break
            (588, 9.95, 10.2, 9.8, 10.05, 300_000.0),  # the first causal retest
        ]
    )
    signals2, _ = orp.tape_signals(ok, admit_t=575, session_end=959)
    assert {s["qual"] for s in signals2} == {"direct_breakout", "pullback_retest"}
    assert all(s["or_bars"] == 10 for s in signals2)


# ----- one-shot IOC entry pricing (an IOC limit cannot rest) ------------------
def _intent_stream(bid=None, ask=10.0, size=100_000.0, minute=600, ms=0):
    """A single fresh R print at ``minute:00.ms`` (the arrival clock is :00.250s).

    ``bid`` defaults to a cent below the ask so the print is a valid, un-crossed quote
    unless the caller asks for a specific cross.
    """
    bid = float(ask) - 0.01 if bid is None else float(bid)
    return _stream([(minute, ms, bid, ask, size, size, ("R",))])


def test_ioc_entry_prices_once_and_never_rests_to_a_later_print():
    # The latest raw state at arrival is crossed; a firm, fresh R print exists one
    # minute LATER. An IOC limit must not wait for it: the entry is UNKNOWN here.
    sq = _stream(
        [
            (600, 0, 10.5, 10.0, 100_000, 100_000, ("R",)),  # crossed at arrival
            (601, 0, 10.0, 10.0, 100_000, 100_000, ("R",)),  # eligible later
        ]
    )
    q, status = orp.aqf.entry_price_leg(sq, _us(600, 250))
    assert q is None and status == "invalid_or_crossed_quote"

    sq2 = _stream([(600, 0, 10.5, 10.0, 100_000, 100_000, ("R",))])
    q2, status2 = orp.aqf.entry_price_leg(sq2, _us(600, 250))
    assert q2 is None and status2 == "invalid_or_crossed_quote"


def test_ioc_entry_reports_a_stale_but_valid_arrival_as_unknown():
    sq = _stream([(599, 0, 10.0, 10.02, 100_000, 100_000, ("R",))])  # 60.25s old
    q, status = orp.aqf.entry_price_leg(sq, _us(600, 250))
    assert q is None and status == "unknown_stale_but_valid_at_arrival"


def test_ioc_entry_prices_a_fresh_firm_print_at_the_arrival_clock():
    sq = _intent_stream()
    q, status = orp.aqf.entry_price_leg(sq, _us(600, 250))
    assert status == "quoted_fresh_at_arrival"
    assert q["ask"] == 10.0 and q["age_s"] == pytest.approx(0.25)


# ----- funded book replay -----------------------------------------------------
def _state_row(
    ticker,
    entry_minute,
    *,
    qual="direct_breakout",
    ask=10.0,
    age_s=0.25,
    depth=100_000.0,
    spread_bps=20.0,
    qty=24,
    limit=10.1,
    session_end=959,
    intent_status="quoted",
    pred=None,
):
    intent_us = _us(entry_minute)
    facts = {
        "intent_status": intent_status,
        "intent_age_s": age_s,
        "qty": qty,
        "intent_ask_shares": depth,
        "intent_spread_bps": spread_bps,
    }
    intent_reason, _ = orp.aqf.entry_rule_status(facts, float("inf"))
    return {
        "day": DAY,
        "ticker": ticker,
        "qual": qual,
        "entry_minute": entry_minute,
        "decision_minute": entry_minute - 1,
        "signal_bar_minute": entry_minute - 2,
        "signal_minute": entry_minute - 1,
        "breakout_minute": entry_minute - 2,
        "admit_t": 575,
        "session_end": session_end,
        "intent_us": intent_us,
        "intent_status": intent_status,
        "intent_reason": intent_reason,
        "intent_ask": ask,
        "intent_bid": ask - 0.02,
        "intent_age_s": age_s,
        "intent_spread_bps": spread_bps,
        "intent_ask_shares": depth,
        "limit_price": limit,
        "qty": qty,
        "reserved_usd": (qty * limit * 1.0075) if (limit is not None and qty) else 0.0,
        "pred_15": pred,
        "pred_60": pred,
        "proxy_gross_15": None,
        "proxy_gross_30": None,
        "proxy_gross_60": None,
        "signal_ret": 0.01,
        "volume_ratio": 0.5,
        "range_ratio": 3.0,
    }


def _states(rows):
    return pl.DataFrame(rows, schema=orp.STATE_TYPES)


# the plain (no-model) direct-breakout view used by the book tests
VIEW = orp.View(qual="direct_breakout", head=30)


def _fresh_book(tickers, *, minutes, bid=10.0, ask=10.0):
    streams = {}
    for t in tickers:
        streams[t] = _dense(minutes, bid=bid, ask=ask, ticker=t)
    return streams


def test_max_first_attempt_per_ticker_per_day_is_enforced():
    rows = [_state_row("AAA", 600), _state_row("AAA", 620)]
    daily, trades = orp.replay_day(
        DAY, _states(rows), _fresh_book(["AAA"], minutes=range(600, 700)), VIEW, False
    )
    assert daily["attempts"] == 1
    assert daily["max_attempt_skips"] + daily["overlap_skips"] == 1
    assert [tr["attempt_index"] for tr in trades] == [1]
    assert all(tr["entry_minute"] == 600 for tr in trades)
    # the funded intent is observed at the entry clock 0 and priced at +250ms.
    trade = trades[0]
    assert trade["intent_us"] == _us(600)
    assert trade["entry_arrival_us"] == _us(600, 250)
    assert trade["decision_minute"] == 599 and trade["signal_bar_minute"] == 598


def test_three_slots_are_funded_before_any_arrival_outcome_no_substitution():
    rows = [_state_row(t, 600) for t in ("AAA", "BBB", "CCC", "DDD")]
    daily, trades = orp.replay_day(
        DAY,
        _states(rows),
        _fresh_book(["AAA", "BBB", "CCC", "DDD"], minutes=range(600, 700)),
        VIEW,
        False,
    )
    assert daily["intents_funded"] == 3
    assert daily["cash_or_slot_skips"] == 1
    assert {tr["ticker"] for tr in trades} == {"AAA", "BBB", "CCC"}
    assert daily["positions_peak"] == 3


def test_unknown_entry_keeps_the_slot_and_the_ticker_to_the_session_end():
    # AAA's INTENT is firm and fresh, but its ARRIVAL print is 2.15s old (stale but
    # valid), so the IOC is an UNKNOWN: its slot and ticket stay reserved and a
    # later-minute candidate cannot reuse them inside the same session.
    rows = [_state_row("AAA", 600), _state_row("BBB", 610)]
    streams = _fresh_book(["AAA", "BBB"], minutes=range(600, 700))
    streams["AAA"] = _stream([(599, 58_100, 10.0, 10.0, 100_000, 100_000, ("R",))])
    daily, trades = orp.replay_day(DAY, _states(rows), streams, VIEW, False)
    assert daily["unknown_fills"] == 1 and daily["known_fills"] == 1
    by_ticker = {tr["ticker"]: tr for tr in trades}
    assert by_ticker["AAA"]["fill_status"] == "unknown_entry_execution"
    assert by_ticker["AAA"]["actual_exit_us"] > _us(959)
    assert by_ticker["BBB"]["fill_status"] == "conditional_fill"


def test_no_match_entry_releases_the_ticket_at_the_arrival_clock():
    # AAA's arrival ask (10.5) exceeds the pre-declared limit (10.1): modelled
    # unfilled cash, the ticket released at the arrival clock.
    rows = [_state_row("AAA", 600, limit=10.1), _state_row("BBB", 601)]
    streams = _fresh_book(["AAA", "BBB"], minutes=range(600, 700))
    streams["AAA"] = _stream(
        [
            (600, 0, 10.0, 10.0, 100_000, 100_000, ("R",)),  # intent book: limit 10.10
            (600, 250, 10.4, 10.5, 100_000, 100_000, ("R",)),  # arrival ask > limit
        ]
    )
    daily, trades = orp.replay_day(DAY, _states(rows), streams, VIEW, False)
    by_ticker = {tr["ticker"]: tr for tr in trades}
    assert by_ticker["AAA"]["fill_status"] == "no_match_at_l1_unfilled_cash"
    assert by_ticker["AAA"]["actual_exit_us"] == _us(600, 250)
    assert daily["no_match_fills"] == 1
    # the released ticket funds BBB at the next minute.
    assert by_ticker["BBB"]["fill_status"] == "conditional_fill"


def test_known_contribution_uses_the_causal_quantity_and_both_leg_fees():
    # entry ask 10.00 -> exit bid 10.40 over the hold: a clear gross touch gain.
    rows = [_state_row("AAA", 600)]
    streams = {
        "AAA": _stream(
            [
                *(
                    (m, k, 10.0, 10.0, 100_000, 100_000, ("R",))
                    for m in range(600, 615)
                    for k in range(0, 60_000, 500)
                ),
                *(
                    (m, k, 10.4, 10.42, 100_000, 100_000, ("R",))
                    for m in range(615, 616)
                    for k in range(0, 60_000, 500)
                ),
            ],
            "AAA",
        )
    }
    view = orp.View(qual="direct_breakout", head=15)
    daily, trades = orp.replay_day(DAY, _states(rows), streams, view, False)
    trade = trades[0]
    assert trade["fill_status"] == "conditional_fill"
    assert trade["qty"] == 24
    assert trade["entry_ask"] == pytest.approx(10.0)
    assert trade["exit_bid"] == pytest.approx(10.4)
    assert trade["net_usd_0"] > trade["net_usd_25"] > trade["net_usd_150"]
    assert trade["net_usd_0"] == pytest.approx(24 * (10.4 - 10.0))
    # 150bps on both legs of a 4% touch gain still leaves the fill positive here.
    assert trade["net_usd_150"] == pytest.approx(24 * (10.4 * (1 - 0.0075) - 10.0 * (1 + 0.0075)))
    assert daily["known_fills"] == 1


# ----- MARKET exit: the ACTUAL returned price clock is the funding clock -------
def _exit_states(ticker="AAA", entry_minute=600, head=15):
    return _states([_state_row(ticker, entry_minute)])


def test_exit_rests_when_the_book_is_absent_and_funding_waits_the_real_clock():
    # At the exit due intent the latest raw print is invalid; a firm fresh print
    # exists later. A MARKET exit may rest to it, and the funding clock must be
    # THAT print's timestamp (the arrival clock would be a lookahead-early release).
    view = orp.View(qual="direct_breakout", head=15)  # exit due minute 615
    streams = {
        "AAA": _stream(
            _dense_rows(range(600, 615))
            + [(615, 0, 10.5, 10.0, 100_000, 100_000, ("R",))]  # crossed at the due intent
            + [(617, 0, 10.3, 10.32, 100_000, 100_000, ("R",))],  # first eligible later
            "AAA",
        )
    }
    daily, trades = orp.replay_day(DAY, _exit_states(), streams, view, False)
    trade = trades[0]
    assert trade["fill_status"] == "conditional_fill"
    assert trade["exit_status"] == "conditional_market_fill"
    assert trade["exit_submit_status"] == "submit_at_first_regular_after_due_intent"
    assert trade["exit_submit_us"] == _us(617, 0)
    # the funding clock is the real price clock: the order rested to 617:00.000 and
    # the fresh print there is priced at the 617:00.250 arrival - strictly AFTER the
    # planned 615:00.250 exit arrival, so no cash/slot is released lookahead-early.
    assert trade["actual_exit_us"] == _us(617, 250)
    assert trade["exit_arrival_us"] == _us(617, 250)
    assert trade["actual_exit_us"] > _us(615, 250)
    assert trade["exit_bid"] == pytest.approx(10.3)


def test_fresh_exit_funds_at_the_arrival_clock_not_the_prior_quote_stamp():
    # The latest print at the exit arrival is fresh but its own stamp is 1.5s
    # BEFORE the arrival: the fill clock is the arrival, never quote_us.
    view = orp.View(qual="direct_breakout", head=15)  # exit due minute 615
    streams = {
        "AAA": _stream(
            _dense_rows(range(600, 615))
            + [(615, 0, 10.3, 10.32, 100_000, 100_000, ("R",))],  # 15:59.x, fresh at 615.250
            "AAA",
        )
    }
    daily, trades = orp.replay_day(DAY, _exit_states(), streams, view, False)
    trade = trades[0]
    assert trade["fill_status"] == "conditional_fill"
    assert trade["exit_arrival_us"] == _us(615, 250)
    assert trade["actual_exit_us"] == _us(615, 250)
    assert trade["exit_quote_us"] == _us(615, 0)
    assert trade["exit_quote_us"] < trade["actual_exit_us"]


def test_exit_depth_shortfall_is_unknown_never_a_guaranteed_fill():
    view = orp.View(qual="direct_breakout", head=15)
    streams = {
        "AAA": _stream(
            _dense_rows(range(600, 614))
            + [(614, 59_000, 10.0, 10.02, 100_000, 100_000, ("R",))]
            + [(614, 59_500, 10.3, 10.32, 0.01, 100_000, ("R",))],  # thin BID: 1 share
            "AAA",
        )
    }
    daily, trades = orp.replay_day(DAY, _exit_states(), streams, view, False)
    trade = trades[0]
    assert trade["fill_status"] == "unknown_exit_execution"
    assert trade["exit_status"] == "unknown_exit_partial_depth"
    assert trade["actual_exit_us"] > _us(959)
    assert daily["unknown_fills"] == 1 and daily["known_fills"] == 0


def test_exit_submission_holds_to_the_first_eligible_print_after_a_stale_book():
    view = orp.View(qual="direct_breakout", head=15)  # exit due minute 615
    streams = {
        "AAA": _stream(
            _dense_rows(range(600, 614))
            + [(616, 0, 10.3, 10.32, 100_000, 100_000, ("R",))],  # first print after due
            "AAA",
        )
    }
    daily, trades = orp.replay_day(DAY, _exit_states(), streams, view, False)
    trade = trades[0]
    # a stale book at the due intent (last print 613:59.500): the order holds to the
    # NEXT eligible print and is priced 250ms after that submission.
    assert trade["exit_submit_status"] == "submit_at_first_regular_after_due_intent"
    assert trade["exit_submit_us"] == _us(616, 0)
    assert trade["exit_arrival_us"] == _us(616, 250)
    assert trade["actual_exit_us"] == _us(616, 250)


# ----- optional real-forecast gate honesty ------------------------------------
def test_model_gate_requires_a_real_forecast_and_never_invents_one():
    view15 = orp.View(qual="direct_breakout", head=15)
    view30 = orp.View(qual="direct_breakout", head=30)
    view60 = orp.View(qual="direct_breakout", head=60)
    missing = _state_row("AAA", 600)
    assert orp.view_rule(missing, view15, True) == ("model_forecast_unavailable", False)
    assert orp.view_rule(missing, view30, True) == ("model_forecast_unavailable", False)
    assert orp.view_rule(missing, view60, True) == ("model_forecast_unavailable", False)
    # a real forecast far below the observed spread fails the published spread bar.
    thin = _state_row("AAA", 600, spread_bps=400.0, pred=0.0005)
    assert orp.view_rule(thin, view15, True) == ("spread_above_prediction", False)
    rich = _state_row("AAA", 600, spread_bps=20.0, pred=0.02)
    assert orp.view_rule(rich, view15, True) == ("eligible", True)
    # without the gate the head-independent quote/depth reason IS the rule.
    assert orp.view_rule(thin, view15, False) == ("eligible", True)
    stale = _state_row("AAA", 600, age_s=30.0)
    assert orp.view_rule(stale, view15, False) == ("intent_not_firm_fresh", False)


def test_zero_quantity_is_a_known_no_order_never_an_attempt():
    rows = [_state_row("AAA", 600, qty=0, limit=None)]
    daily_row = orp.intent_facts(_intent_stream(ask=1000.0), _us(600, 250))
    assert daily_row["qty"] == 0
    assert daily_row["intent_reason"] == "no_order_min_capital"
    daily, trades = orp.replay_day(
        DAY, _states(rows), _fresh_book(["AAA"], minutes=range(600, 700)), VIEW, False
    )
    assert daily["attempts"] == 0 and daily["no_order_min_capital"] == 1


def test_quantity_is_sized_at_the_maximum_reported_rung_not_the_25bps_rung():
    """A cheap share is the case where 25bps sizing over-funds the ticket at 150bps.

    intent ask 1.00 -> limit 1.01. Sizing at 25bps gives q=247 whose 150bps
    reservation is 247*1.01*1.0075 = $251.4 > $250: the ticket would be unfundable
    at the highest reported rung. Sizing at the max reported rung gives q=245 and a
    reservation of $249.20, so every rung trades the same causal q/limit.
    """
    facts = orp.intent_facts(_intent_stream(ask=1.0), _us(600, 250))
    assert facts["limit_price"] == pytest.approx(1.01)
    assert facts["qty"] == 245
    assert facts["reserved_usd"] == pytest.approx(245 * 1.01 * 1.0075)
    assert facts["reserved_usd"] <= orp.ORDER_BUDGET + 1e-9
    # the 25bps-basis quantity would indeed over-fund this ticket at 150bps
    q25 = orp.aqf.causal_quantity(facts["limit_price"], 25.0)
    assert q25 * facts["limit_price"] * 1.0075 > orp.ORDER_BUDGET
    assert q25 > facts["qty"]
    # across a price sweep the reserved ticket never exceeds the $250 budget
    for ask in (0.5, 1.0, 2.5, 5.0, 9.99, 10.0, 32.5, 100.0, 999.0):
        f = orp.intent_facts(_intent_stream(ask=ask), _us(600, 250))
        if f["qty"] < 1:
            assert f["intent_reason"] == "no_order_min_capital"
            continue
        assert f["reserved_usd"] <= orp.ORDER_BUDGET + 1e-9
        assert f["qty"] == int(orp.ORDER_BUDGET // (f["limit_price"] * 1.0075))


# ----- frozen selection determinism -------------------------------------------
def test_choose_view_is_a_deterministic_argmax_of_the_frozen_objective():
    def surface(values, fills):
        return {
            v.key: {
                "known_usd_per_calendar_day": {"25": values[v.key]},
                "known_fills": fills[v.key],
                "unknown_fills": 0,
                "no_match_fills": 0,
                "traded_days": 1,
            }
            for v in orp.VIEWS
        }

    values = {v.key: 0.0 for v in orp.VIEWS}
    values["direct_breakout|h30"] = 1.5
    values["pullback_retest|h60"] = 1.5  # exact tie on the objective
    fills = {v.key: 0 for v in orp.VIEWS}
    fills["direct_breakout|h30"] = 3
    fills["pullback_retest|h60"] = 5
    chosen, ranking = orp.choose_view(surface(values, fills))
    assert chosen.key == "pullback_retest|h60"  # tie-break: more known fills
    assert ranking[0]["view_key"] == chosen.key
    assert len(ranking) == len(orp.VIEWS)
    # the ranking is ordered by the frozen objective (descending).
    objectives = [r["validation_known_usd_per_calendar_day_25"] for r in ranking]
    assert objectives == sorted(objectives, reverse=True)


# ----- an empty panel day is a reportable state, never a crash -----------------
def test_an_empty_panel_day_returns_empty_states_not_a_crash(tmp_path, monkeypatch):
    # A day whose panel file exists but carries no rows has no watchlist, so the day is
    # skipped with a real coverage dict - the shortcut used to reference an unbound
    # coverage variable and crashed the whole block on the first such day.
    days_root = tmp_path / "panel" / "days"
    days_root.mkdir(parents=True)
    pl.DataFrame(
        schema={
            "ticker": pl.String,
            "admit_t": pl.Int64,
            "admit_gain": pl.Float64,
            "session_end": pl.Int64,
        }
    ).write_parquet(days_root / f"{DAY}.parquet")
    monkeypatch.setattr(orp, "PANEL_DAYS", days_root)
    data_root = tmp_path / "data"
    (data_root / "sip" / "net" / "bars").mkdir(parents=True)
    pl.DataFrame(schema={"ticker": pl.String, "et": pl.Int64}).write_parquet(
        data_root / "sip" / "net" / "bars" / f"{DAY}.parquet"
    )
    states, cov = orp.score_day(DAY, {}, False, data_root, ())
    # an empty but correctly typed state frame, not a crash and not a phantom row
    assert states.height == 0
    assert list(states.columns) == list(orp.STATE_COLUMNS)
    # the coverage discloses the empty day instead of an unbound-variable failure
    assert cov["session_end"] is None
    assert cov["panel_rows"] == 0 and cov["watchlist_tickers"] == 0
    assert cov["tickers_with_quote_stream"] == 0
    assert cov["intent_status_counts"] == {}
