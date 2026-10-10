"""Behavioral tests for the multi-session carry producer's timing and accounting edges.

Covers exactly the edges this study can get silently wrong: calendar adjacency and the
protected-crossing removal order, the causal NEAR-CLOSE qualification (the session's
still-open final minute bar and the last-minute admissions never reach the decision), the
one-shot IOC entry (an IOC limit cannot rest into a later eligible print), the MARKET exit's
real execution clock priced against the ACTUAL post-corporate-action share count, the
split ledger's price-versus-share residual with its balanced-action and fractional
branches, the wealth-return (not raw-price-ratio) gross, the missing-tape retention rule,
and the self-financing 3 x $250 book across sessions.

Quote rows are ``(minute, ms, bid, ask, bid_size, ask_size, conditions)`` where ``ms`` is
the millisecond offset INSIDE that ET minute (the same ``clock_us`` convention the shared
quote service uses). Displayed quote sizes are round lots (x100) before 2025-11-03.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import pytest

sys.path.insert(0, ".")
sys.path.insert(0, "factory/scripts")

import alpha_multi_session_carry as msc  # noqa: E402

ET = ZoneInfo("America/New_York")
DAY = "2023-03-15"  # Wednesday
D1, D2, D3, D4, D5 = "2023-03-16", "2023-03-17", "2023-03-20", "2023-03-21", "2023-03-22"
SEG_DAYS = [
    "2023-03-15",
    "2023-03-16",
    "2023-03-17",
    "2023-03-20",
    "2023-03-21",
    "2023-03-22",
    "2023-03-23",
    "2023-03-24",
]
# a six-session segment that ends on the protected boundary (2024 is unread)
TAIL_DAYS = ["2023-12-21", "2023-12-22", "2023-12-26", "2023-12-27", "2023-12-28", "2023-12-29"]
LATE_DAYS = ["2025-02-03", "2025-02-04", "2025-02-05"]


def _frame(rows, ticker: str = "TEST", day: str = DAY) -> pl.DataFrame:
    stamps = [
        (
            datetime.fromisoformat(day).replace(tzinfo=ET) + timedelta(minutes=m, milliseconds=ms)
        ).astimezone(UTC)
        for m, ms, *_ in rows
    ]
    return pl.DataFrame(
        {
            "symbol": [ticker] * len(rows),
            "ts_utc": stamps,
            "bid_price": [r[2] for r in rows],
            "ask_price": [r[3] for r in rows],
            "bid_size": [r[4] for r in rows],
            "ask_size": [r[5] for r in rows],
            "bid_exchange": ["Q"] * len(rows),
            "ask_exchange": ["Q"] * len(rows),
            "conditions": [list(r[6]) for r in rows],
            "tape": ["A"] * len(rows),
        }
    ).sort("ts_utc")


def _stream(rows, ticker: str = "TEST", day: str = DAY):
    return msc.symbol_quotes(_frame(rows, ticker, day), day)[ticker]


def _dense(day: str, ticker: str, *, bid=10.0, ask=10.02, size=100_000.0):
    """One fresh R print every 0.5s across the whole regular session."""
    rows = [
        (minute, ms, bid, ask, size, size, ("R",)) for minute in range(570, 960) for ms in (0, 500)
    ]
    return _stream(rows, ticker, day)


def _write_candidates(out: Path, day: str, tickers, *, quality_a=True, session_end=959):
    rows = [
        {
            "day": day,
            "ticker": t,
            "admit_t": 575,
            "admit_gain": 0.10,
            "session_end": session_end,
            "decision_minute": session_end - 1,
            "last_observed_bar": session_end - 2,
            "entry_minute": session_end,
            "last_et": session_end - 2,
            "n_bars": 390,
            "close": 5.0,
            "high": 5.2,
            "low": 4.8,
            "vwap": 4.95,
            "cum_dv": 20_000_000.0,
            "ctr": 0.90,
            "vwap_dist": 0.0101,
            "quality_A": quality_a,
        }
        for t in tickers
    ]
    path, _ = msc.cand_paths(out, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=msc.CAND_TYPES).write_parquet(path)


def _qual_row(ticker: str, exit_day: str, hold: int, qual: str = "A"):
    return {
        "day": DAY,
        "ticker": ticker,
        "view_key": f"{qual}h{hold}",
        "qual": qual,
        "hold": hold,
        "exit_day": exit_day,
        "ctr": 0.90,
        "vwap_dist": 0.01,
        "cum_dv": 20_000_000.0,
        "close": 5.0,
    }


# ----- the fixed quality flag --------------------------------------------------
def test_quality_a_boundaries_are_exact():
    assert msc.quality_a(0.80, 0.01, 10_000_000.0, 3.0) is True
    assert msc.quality_a(0.7999, 0.01, 10_000_000.0, 3.0) is False  # outside the upper 20%
    assert msc.quality_a(0.90, 0.0, 10_000_000.0, 3.0) is False  # not strictly above VWAP
    assert msc.quality_a(0.90, 0.01, 9_999_999.0, 3.0) is False  # below the liquidity floor
    assert msc.quality_a(0.90, 0.01, 10_000_000.0, 2.99) is False  # below the price floor


# ----- calendar adjacency and the protected-crossing removal -------------------
def test_segment_map_never_hands_out_a_protected_next_session():
    sessions = dict.fromkeys(("2023-12-27", "2023-12-28", "2023-12-29", "2025-02-03"), 959)
    segmap = msc.SegmentMap.build(sessions)
    assert [len(s) for s in segmap.segments] == [3, 1]
    assert segmap.next_nth("2023-12-27", 2) == "2023-12-29"
    assert segmap.next_nth("2023-12-28", 2) is None  # would cross the protected months
    assert segmap.next_nth("2025-02-03", 1) is None  # would run past the late segment end


def test_protected_crossing_intent_is_removed_before_qualification(tmp_path):
    sessions = dict.fromkeys(TAIL_DAYS + LATE_DAYS, 959)
    segmap = msc.SegmentMap.build(sessions)
    for day in sessions:
        _write_candidates(tmp_path, day, ["AAA"])
    frame = msc.build_qual_block(
        tmp_path, "validation", TAIL_DAYS, segmap, False, "producer", "contract"
    )
    got = {(r["day"], r["hold"]) for r in frame.iter_rows(named=True) if r["qual"] == "A"}
    # the hold-2/3/5 exits must all land inside the segment; the last sessions keep
    # shrinking holds, and the final two sessions qualify for nothing at all
    assert got == {
        ("2023-12-21", 2),
        ("2023-12-21", 3),
        ("2023-12-21", 5),
        ("2023-12-22", 2),
        ("2023-12-22", 3),
        ("2023-12-26", 2),
        ("2023-12-26", 3),
        ("2023-12-27", 2),
    }
    # and every reported exit_day is a real traversable session of the same segment
    assert {r["exit_day"] for r in frame.iter_rows(named=True)} <= set(TAIL_DAYS)


def test_first_emergence_and_breadth_filter_quality_b(tmp_path):
    sessions = dict.fromkeys(SEG_DAYS, 959)
    segmap = msc.SegmentMap.build(sessions)
    _write_candidates(tmp_path, DAY, ["AAA", "BBB"])
    _write_candidates(tmp_path, D1, ["AAA"])  # AAA repeats: not a first emergence
    _write_candidates(tmp_path, D2, ["AAA", "BBB"])  # both already emerged
    _write_candidates(tmp_path, D3, ["CCC"])  # first emergence, but breadth is too thin
    _write_candidates(tmp_path, D4, ["CCC", "DDD"])  # DDD emerges on a wide day
    for day in (D5,):
        _write_candidates(tmp_path, day, [], quality_a=False)
    frame = msc.build_qual_block(
        tmp_path, "validation", [DAY, D1, D2, D3, D4], segmap, False, "producer", "contract"
    )
    b = {(r["day"], r["ticker"]) for r in frame.iter_rows(named=True) if r["qual"] == "B"}
    a = {(r["day"], r["ticker"]) for r in frame.iter_rows(named=True) if r["qual"] == "A"}
    assert b == {(DAY, "AAA"), (DAY, "BBB"), (D4, "DDD")}
    assert (D1, "AAA") in a and (D1, "AAA") not in b
    assert (D3, "CCC") in a and (D3, "CCC") not in b  # breadth: 1 < trailing median 2


# ----- the entry leg: one-shot IOC, evaluated once at the arrival clock --------
def test_ioc_entry_never_rests_into_a_later_eligible_print():
    rows = [
        (959, 0, 10.0, 10.02, 100_000, 100_000, ("R",)),  # fresh intent at the send clock
        (959, 250, 12.0, 11.0, 100_000, 100_000, ("R",)),  # crossed at the arrival clock
        (960, 0, 10.0, 10.02, 100_000, 100_000, ("R",)),  # a perfectly good later print
    ]
    stream = _stream(rows)
    facts = msc.resolve_intent(stream, msc.minute_us(DAY, 959))
    assert facts["entry_status"] is None
    assert facts["limit_price"] == pytest.approx(10.13)
    assert facts["qty"] == 24
    msc.resolve_entry_arrival(stream, facts)
    assert facts["entry_status"] == "unknown_entry_invalid_or_crossed_quote"
    assert facts["fill_clock_us"] is None  # nothing filled: the position is retained


def test_ioc_entry_is_a_known_no_match_when_the_arrival_ask_exceeds_the_limit():
    rows = [
        (959, 0, 10.0, 10.02, 100_000, 100_000, ("R",)),
        (959, 250, 10.0, 10.30, 100_000, 100_000, ("R",)),  # ask above the 10.13 limit
    ]
    stream = _stream(rows)
    facts = msc.resolve_intent(stream, msc.minute_us(DAY, 959))
    msc.resolve_entry_arrival(stream, facts)
    assert facts["entry_status"] == "no_match_at_l1"


def test_ioc_entry_partial_depth_is_an_unknown_not_a_partial_fill():
    rows = [
        (959, 0, 10.0, 10.02, 100_000.0, 100_000.0, ("R",)),
        # 0.1 round lots = 10 displayed shares: fewer than the 24-share causal quantity
        (959, 250, 10.0, 10.02, 100_000.0, 0.1, ("R",)),
    ]
    stream = _stream(rows)
    facts = msc.resolve_intent(stream, msc.minute_us(DAY, 959))
    assert facts["qty"] == 24
    msc.resolve_entry_arrival(stream, facts)
    assert facts["entry_status"] == "unknown_entry_partial_depth_at_arrival"


def test_ioc_entry_intent_must_be_firm_and_fresh_at_the_send_clock():
    stream = _stream([(957, 0, 10.0, 10.02, 100_000, 100_000, ("R",))])  # two minutes old
    facts = msc.resolve_intent(stream, msc.minute_us(DAY, 959))
    assert facts["entry_status"] == "intent_not_firm_fresh"


def test_ioc_entry_fills_at_the_actual_ask_with_the_arrival_as_the_fill_clock():
    rows = [
        (959, 0, 10.0, 10.02, 100_000, 100_000, ("R",)),
        (959, 250, 10.0, 10.05, 100_000, 100_000, ("R",)),
    ]
    stream = _stream(rows)
    facts = msc.resolve_intent(stream, msc.minute_us(DAY, 959))
    msc.resolve_entry_arrival(stream, facts)
    assert facts["entry_status"] == "conditional_ioc_fill"
    assert facts["entry_ask"] == pytest.approx(10.05)
    assert facts["fill_clock_us"] == facts["intent_arrival_us"]


def test_min_capital_and_depth_gates_are_known_skips_not_orders():
    rich = _stream([(959, 0, 900.0, 900.5, 100_000, 100_000, ("R",))])
    facts = msc.resolve_intent(rich, msc.minute_us(DAY, 959))
    assert facts["qty"] == 0
    assert facts["entry_status"] == "no_order_min_capital"
    # 0.02 round lots = 2 displayed shares: fewer than the 24-share causal quantity
    thin = _stream([(959, 0, 10.0, 10.02, 100_000, 0.02, ("R",))])
    facts = msc.resolve_intent(thin, msc.minute_us(DAY, 959))
    assert facts["entry_status"] == "intent_depth_unsupported"


# ----- the exit leg: MARKET rests, and the fill clock is the real one ----------
def test_market_exit_rests_and_prices_at_the_real_rested_clock():
    rows = [
        (929, 59500, 10.0, 10.02, 100_000, 100_000, ("R",)),  # fresh at the 15:30 due intent
        (930, 250, 12.0, 11.0, 100_000, 100_000, ("R",)),  # crossed at the arrival clock
        (931, 0, 9.9, 9.95, 100_000, 100_000, ("R",)),  # first eligible print after it
    ]
    stream = _stream(rows)
    leg = msc.resolve_exit_leg(stream, qty=10, exit_day=DAY, session_end=959)
    assert leg["exit_status"] == "conditional_market_fill"
    assert leg["exit_bid"] == pytest.approx(9.9)
    # the fill clock is the rested print's own timestamp, NOT the original arrival clock
    assert leg["exit_fill_clock_us"] == msc.minute_us(DAY, 931)
    assert leg["exit_fill_clock_us"] != leg["exit_arrival_us"]


def test_market_exit_stale_but_valid_at_the_arrival_is_unknown():
    rows = [
        (929, 58100, 10.0, 10.02, 100_000, 100_000, ("R",)),  # 1.9s old at the due intent
        (931, 0, 9.9, 9.95, 100_000, 100_000, ("R",)),  # a later, more favourable print
    ]
    stream = _stream(rows)
    leg = msc.resolve_exit_leg(stream, qty=10, exit_day=DAY, session_end=959)
    # 1.9s + 250ms arrival latency is 2.15s: stale at arrival, and the later print is NOT used
    assert leg["exit_status"] == "unknown_stale_but_valid_at_arrival"
    assert leg["exit_bid"] is None


def test_market_exit_without_any_valid_regular_print_is_unknown():
    rows = [
        (929, 59500, 10.0, 10.02, 100_000, 100_000, ("R",)),
        (930, 250, 12.0, 11.0, 100_000, 100_000, ("R",)),  # crossed, never recovers
    ]
    stream = _stream(rows)
    leg = msc.resolve_exit_leg(stream, qty=10, exit_day=DAY, session_end=959)
    assert leg["exit_status"].startswith("unknown_no_valid_regular_at_or_after_arrival")


# ----- split-ledger accounting ------------------------------------------------
def _actions(events):
    return {"TEST": [dict(e) for e in events]}


def test_recorded_split_changes_the_quantity_at_the_ex_date():
    actions = _actions(
        [
            {
                "ex_date": D1,
                "action_type": "forward_splits",
                "new_rate": 3.0,
                "old_rate": 2.0,
                "id": "x",
            }
        ]
    )
    state = msc.exit_action_state("TEST", DAY, D2, actions, {})
    assert state["action_verified"] is True
    assert state["shares_mult"] == pytest.approx(1.5)
    assert msc.exit_quantity(24, state["shares_mult"]) == (36.0, None)


def test_fractional_post_event_share_count_is_an_unknown():
    actions = _actions(
        [
            {
                "ex_date": D1,
                "action_type": "forward_splits",
                "new_rate": 3.0,
                "old_rate": 2.0,
                "id": "x",
            }
        ]
    )
    state = msc.exit_action_state("TEST", DAY, D2, actions, {})
    qty, reason = msc.exit_quantity(25, state["shares_mult"])
    assert qty is None
    assert reason == "fractional_shares_cash_in_lieu_unverified"


def test_unrecorded_split_like_discontinuity_inside_the_hold_is_unverified():
    state = msc.exit_action_state("TEST", DAY, D2, {}, {(DAY, D1): 3.0})
    assert state["action_verified"] is False
    assert state["unknown_reason"] == "unverified_action_identity"


def test_recorded_factor_that_does_not_explain_the_tape_is_unverified():
    actions = _actions(
        [
            {
                "ex_date": D1,
                "action_type": "forward_splits",
                "new_rate": 2.0,
                "old_rate": 1.0,
                "id": "x",
            }
        ]
    )
    state = msc.exit_action_state("TEST", DAY, D2, actions, {(DAY, D1): 6.0})
    assert state["action_verified"] is False


def test_a_normal_in_hold_move_is_never_treated_as_an_action():
    state = msc.exit_action_state("TEST", DAY, D2, {}, {(DAY, D1): 0.93, (D1, D2): 1.21})
    assert state["action_verified"] is True
    assert state["shares_mult"] == 1.0


# ----- the funded book across sessions ----------------------------------------
def _book_setup(view: msc.View, days=None):
    days = SEG_DAYS if days is None else days
    sessions = dict.fromkeys(days, 959)
    segmap = msc.SegmentMap.build(sessions)
    book = msc.Book(view.key)
    boundaries = msc.BoundaryBook(sessions)
    boundaries._anchor = lambda d: {}  # no tape evidence needed in these fixtures
    return sessions, segmap, book, boundaries


def test_slot_cap_and_ticker_block_are_causal():
    sessions, segmap, book, boundaries = _book_setup(msc.View("A", 2))
    streams = {t: _dense(DAY, t) for t in ("AAA", "BBB", "CCC", "DDD")}
    rows = [_qual_row(t, D2, 2) for t in ("AAA", "BBB", "CCC", "DDD")]
    daily, trades = msc.process_book_day(
        book, DAY, msc.View("A", 2), streams, sessions, segmap, {}, boundaries, [], 25.0, rows
    )
    assert daily["entries_funded"] == 3
    assert daily["cash_or_slot_skips"] == 1
    assert daily["entry_fills"] == 3
    assert len(book.open) == 3
    outlays = [t["outlay_usd"] for t in trades if t["kind"] == "entry"]
    assert book.cash == pytest.approx(750.0 - sum(outlays))
    # three fee-funded tickets can never exceed the $750 book at any fee rung
    assert sum(outlays) <= 3 * msc.ORDER_BUDGET


def test_held_position_blocks_the_ticker_and_the_slot_across_sessions():
    sessions, segmap, book, boundaries = _book_setup(msc.View("A", 2))
    daily, _ = msc.process_book_day(
        book,
        DAY,
        msc.View("A", 2),
        {"AAA": _dense(DAY, "AAA")},
        sessions,
        segmap,
        {},
        boundaries,
        [],
        25.0,
        [_qual_row("AAA", D2, 2)],
    )
    assert len(book.open) == 1
    # the next session: AAA is still held, so a fresh AAA intent is a known skip
    daily2, trades2 = msc.process_book_day(
        book,
        D1,
        msc.View("A", 2),
        {"AAA": _dense(D1, "AAA")},
        sessions,
        segmap,
        {},
        boundaries,
        [],
        25.0,
        [_qual_row("AAA", D2, 2)],
    )
    assert daily2["ticker_held_skips"] == 1
    assert daily2["entries_funded"] == 0
    assert trades2 == []


def test_exit_proceeds_recycle_into_the_self_financing_book():
    sessions, segmap, book, boundaries = _book_setup(msc.View("A", 2))
    msc.process_book_day(
        book,
        DAY,
        msc.View("A", 2),
        {"AAA": _dense(DAY, "AAA")},
        sessions,
        segmap,
        {},
        boundaries,
        [],
        25.0,
        [_qual_row("AAA", D2, 2)],
    )
    cash_after_entry = book.cash
    bid_up = 12.0
    streams_exit = {"AAA": _dense(D2, "AAA", bid=bid_up, ask=bid_up + 0.02)}
    daily, trades = msc.process_book_day(
        book, D2, msc.View("A", 2), streams_exit, sessions, segmap, {}, boundaries, [], 25.0, []
    )
    assert daily["exit_fills"] == 1
    assert len(book.open) == 0 and book.retained == []
    exit_row = [t for t in trades if t["kind"] == "exit"][0]
    expected_proceeds = msc.proceeds_usd(int(exit_row["qty"]), bid_up, 25.0)
    assert book.cash == pytest.approx(cash_after_entry + expected_proceeds)
    assert exit_row["net_usd"] > 0
    assert daily["known_usd"] == pytest.approx(exit_row["net_usd"])


def test_missing_later_tape_retains_the_position_and_keeps_the_reservation():
    sessions, segmap, book, boundaries = _book_setup(msc.View("A", 2))
    msc.process_book_day(
        book,
        DAY,
        msc.View("A", 2),
        {"AAA": _dense(DAY, "AAA")},
        sessions,
        segmap,
        {},
        boundaries,
        [],
        25.0,
        [_qual_row("AAA", D2, 2)],
    )
    cash_before_exit = book.cash
    missing: list[dict] = []
    # the exit session's tape was never acquired: no stream, no substitutes
    daily, trades = msc.process_book_day(
        book,
        D2,
        msc.View("A", 2),
        {},
        sessions,
        segmap,
        {},
        boundaries,
        missing,
        25.0,
        [_qual_row("ZZZ", D3, 2)],
    )
    assert daily["exit_unknown"] == 1
    assert daily["exit_fills"] == 0
    assert len(book.open) == 0 and len(book.retained) == 1
    assert book.cash == pytest.approx(cash_before_exit)  # the ticket is never refunded
    assert daily["lower_bound_usd"] == pytest.approx(-msc.ORDER_BUDGET)
    assert {
        "day": D2,
        "ticker": "AAA",
        "leg": "exit",
        "minute": 930,
        "reason": "quote_not_acquired",
    } in missing
    # the retained position still blocks its ticker on a later session
    daily3, _ = msc.process_book_day(
        book,
        D3,
        msc.View("A", 2),
        {"AAA": _dense(D3, "AAA")},
        sessions,
        segmap,
        {},
        boundaries,
        [],
        25.0,
        [_qual_row("AAA", D4, 2)],
    )
    assert daily3["ticker_held_skips"] == 1


def test_no_match_entry_releases_its_reservation():
    sessions, segmap, book, boundaries = _book_setup(msc.View("A", 2))
    rows = [
        (959, 0, 10.0, 10.02, 100_000, 100_000, ("R",)),
        (959, 250, 10.0, 10.40, 100_000, 100_000, ("R",)),
    ]
    streams = {"AAA": _stream(rows)}
    daily, trades = msc.process_book_day(
        book,
        DAY,
        msc.View("A", 2),
        streams,
        sessions,
        segmap,
        {},
        boundaries,
        [],
        25.0,
        [_qual_row("AAA", D2, 2)],
    )
    assert daily["entry_no_match"] == 1
    assert daily["entry_fills"] == 0
    assert book.cash == pytest.approx(750.0)
    assert book.open == [] and book.retained == []
    assert [t["entry_status"] for t in trades if t["kind"] == "entry"] == ["no_match_at_l1"]


def test_unknown_entry_keeps_the_slot_and_the_reservation():
    sessions, segmap, book, boundaries = _book_setup(msc.View("A", 2))
    rows = [
        (959, 0, 10.0, 10.02, 100_000.0, 100_000.0, ("R",)),
        # 0.04 round lots = 4 displayed shares at the arrival clock: partial depth
        (959, 250, 10.0, 10.02, 100_000.0, 0.04, ("R",)),
    ]
    streams = {"AAA": _stream(rows)}
    daily, trades = msc.process_book_day(
        book,
        DAY,
        msc.View("A", 2),
        streams,
        sessions,
        segmap,
        {},
        boundaries,
        [],
        25.0,
        [_qual_row("AAA", D2, 2)],
    )
    assert daily["entry_unknown"] == 1
    assert len(book.retained) == 1 and book.open == []
    assert book.cash == pytest.approx(750.0 - book.reserved())
    assert daily["lower_bound_usd"] == pytest.approx(-msc.ORDER_BUDGET)
    assert trades[-1]["entry_status"] == "unknown_entry_partial_depth_at_arrival"


# ----- the causal NEAR-CLOSE qualification -----------------------------------
def _bar(ticker: str, et: int, *, o=5.0, h=5.2, low=4.0, c=5.0, v=60_000.0):
    return {"ticker": ticker, "et": et, "open": o, "high": h, "low": low, "close": c, "volume": v}


def _decision_day_tape(tmp_path, monkeypatch, day, *, final_bars, panel_rows):
    """A qualifying 09:30..15:57 prefix plus whatever final-minute bars are appended."""
    panel_root = tmp_path / "panel"
    (panel_root / "days").mkdir(parents=True, exist_ok=True)
    pl.DataFrame(panel_rows).write_parquet(panel_root / "days" / f"{day}.parquet")
    monkeypatch.setattr(msc, "PANEL_DAYS", panel_root / "days")
    bars_root = tmp_path / "bars"
    bars_root.mkdir(parents=True, exist_ok=True)
    # the early bars trade cheaper so the last observed close (5.00) sits strictly above
    # the session VWAP and inside the upper fifth of the observed range
    rows = [_bar("AAA", et, c=4.8 if et < 700 else 5.0) for et in range(570, 958)] + list(
        final_bars
    )
    pl.DataFrame(rows).write_parquet(bars_root / f"{day}.parquet")
    monkeypatch.setattr(msc, "BARS_ROOT", bars_root)
    return {day: 959}


def _observed_close_prefix(day):
    return [{"ticker": "AAA", "admit_t": 575, "admit_gain": 0.10}]


def test_the_still_open_final_minute_bars_never_reach_the_decision(tmp_path, monkeypatch):
    # the 15:58/15:59 SIP bars are appended with an absurd close: the decision is at
    # 15:58, so neither of them (both still open while the 15:59 intent is priced) may
    # move the qualification, the aggregates or the reported clocks.
    extreme = [
        _bar("AAA", 958, o=100.0, h=900.0, low=90.0, c=100.0, v=60_000.0),
        _bar("AAA", 959, o=1.0, h=2.0, low=0.5, c=1.0, v=60_000.0),
    ]
    sessions = _decision_day_tape(
        tmp_path, monkeypatch, DAY, final_bars=extreme, panel_rows=_observed_close_prefix(DAY)
    )
    rows, cov = msc.candidate_rows(DAY, sessions)
    assert len(rows) == 1
    row = rows[0]
    # the last OBSERVED bar is the 15:57 one: its close is the decision-minute price
    assert row["close"] == pytest.approx(5.0)
    assert row["last_observed_bar"] == 957
    assert row["decision_minute"] == 958
    assert row["entry_minute"] == 959
    assert row["high"] == pytest.approx(5.2) and row["low"] == pytest.approx(4.0)
    # ... and without the final bars at all, nothing changes (mutating them cannot
    # retro-fit the qualification)
    sessions_clean = _decision_day_tape(
        tmp_path, monkeypatch, DAY, final_bars=[], panel_rows=_observed_close_prefix(DAY)
    )
    rows_clean, _ = msc.candidate_rows(DAY, sessions_clean)
    comparable = ("close", "high", "low", "vwap", "cum_dv", "ctr", "vwap_dist", "quality_A")
    assert {k: row[k] for k in comparable} == {k: rows_clean[0][k] for k in comparable}
    # the qualifying session does still qualify on the observed prefix alone
    assert row["quality_A"] is True
    assert cov["decision_minute"] == 958


def test_a_name_admitted_in_the_last_minute_is_not_known_at_the_decision(tmp_path, monkeypatch):
    panel_rows = [
        {"ticker": "AAA", "admit_t": 575, "admit_gain": 0.10},
        {"ticker": "BBB", "admit_t": 959, "admit_gain": 0.20},  # admitted at the entry minute
    ]
    sessions = _decision_day_tape(tmp_path, monkeypatch, DAY, final_bars=[], panel_rows=panel_rows)
    rows, cov = msc.candidate_rows(DAY, sessions)
    assert [r["ticker"] for r in rows] == ["AAA"]
    assert cov["admitted_after_decision"] == ["BBB"]
    assert cov["admitted"] == 1


# ----- the split ledger: price ratio, share factor, depth basis ----------------
def test_a_balanced_reverse_split_is_verified_not_flagged():
    # a 1:10 reverse shares x 0.1 and price x 10: 10 * 0.1 = 1.0 explains the tape
    actions = _actions(
        [
            {
                "ex_date": D1,
                "action_type": "reverse_splits",
                "new_rate": 1.0,
                "old_rate": 10.0,
                "id": "x",
            }
        ]
    )
    state = msc.exit_action_state("TEST", DAY, D2, actions, {(DAY, D1): 10.0})
    assert state["action_verified"] is True
    assert state["shares_mult"] == pytest.approx(0.1)
    assert state["unknown_reason"] is None


def test_a_balanced_forward_split_is_verified_not_flagged():
    # a 2:1 forward shares x 2 and price x 0.5: 0.5 * 2 = 1.0 explains the tape
    actions = _actions(
        [
            {
                "ex_date": D1,
                "action_type": "forward_splits",
                "new_rate": 2.0,
                "old_rate": 1.0,
                "id": "x",
            }
        ]
    )
    state = msc.exit_action_state("TEST", DAY, D2, actions, {(DAY, D1): 0.5})
    assert state["action_verified"] is True
    assert state["shares_mult"] == pytest.approx(2.0)


def test_an_unexplained_discontinuity_still_stays_an_unknown():
    actions = _actions(
        [
            {
                "ex_date": D1,
                "action_type": "forward_splits",
                "new_rate": 2.0,
                "old_rate": 1.0,
                "id": "x",
            }
        ]
    )
    # 6.0 * 2 = 12.0 is split-like after the recorded 2:1 is accounted for
    state = msc.exit_action_state("TEST", DAY, D2, actions, {(DAY, D1): 6.0})
    assert state["action_verified"] is False
    assert state["unknown_reason"] == "unverified_action_identity"


def test_exit_depth_is_tested_against_the_post_action_share_count():
    rows = [
        (929, 59500, 5.0, 5.02, 0.5, 1.0, ("R",)),  # fresh at the 15:30 due intent
        (930, 250, 5.0, 5.02, 0.5, 1.0, ("R",)),  # the same 50-deep bid at the arrival
    ]
    stream = _stream(rows)  # 0.5 round lots = 50 displayed bid shares
    leg = msc.resolve_exit_leg(stream, 100.0, exit_day=DAY, session_end=959)
    assert leg["exit_shares"] == pytest.approx(100.0)
    assert leg["exit_bid"] == pytest.approx(5.0)
    assert leg["exit_status"] == "unknown_exit_partial_depth"
    # the SAME book carries the post-reverse 5 shares, and the fill clock is the arrival
    leg5 = msc.resolve_exit_leg(stream, 5.0, exit_day=DAY, session_end=959)
    assert leg5["exit_status"] == "conditional_market_fill"
    assert leg5["exit_fill_clock_us"] == msc.minute_us(DAY, 930) + 250_000


def test_an_unverified_share_count_is_unknown_with_no_original_quantity_fallback():
    stream = _dense(DAY, "AAA")
    leg = msc.resolve_exit_leg(stream, None, exit_day=DAY, session_end=959)
    assert leg["exit_status"] == "unknown_exit_share_count_unverified"
    assert leg["exit_shares"] is None
    # nothing is priced against the pre-action quantity: no bid, no clock, no fill
    assert leg["exit_bid"] is None
    assert leg["exit_fill_clock_us"] is None


def test_a_recorded_forward_split_conserves_wealth_not_the_raw_price_ratio():
    sessions, segmap, book, boundaries = _book_setup(msc.View("A", 2))
    actions = {
        "AAA": [
            {
                "ex_date": D1,
                "action_type": "forward_splits",
                "new_rate": 2.0,
                "old_rate": 1.0,
                "id": "x",
            }
        ]
    }
    _, entries = msc.process_book_day(
        book,
        DAY,
        msc.View("A", 2),
        {"AAA": _dense(DAY, "AAA", bid=10.0, ask=10.0)},
        sessions,
        segmap,
        actions,
        boundaries,
        [],
        25.0,
        [_qual_row("AAA", D2, 2)],
    )
    entry = [t for t in entries if t["kind"] == "entry"][0]
    assert entry["entry_status"] == "conditional_ioc_fill"
    assert entry["qty"] == 24
    cash_after_entry = book.cash
    # the halved price on the doubled shares is the balanced case: wealth is conserved
    daily, trades = msc.process_book_day(
        book,
        D2,
        msc.View("A", 2),
        {"AAA": _dense(D2, "AAA", bid=5.0, ask=5.02)},
        sessions,
        segmap,
        actions,
        boundaries,
        [],
        25.0,
        [],
    )
    exit_row = [t for t in trades if t["kind"] == "exit"][0]
    assert exit_row["exit_status"] == "conditional_market_fill"
    assert exit_row["shares_mult"] == pytest.approx(2.0)
    assert exit_row["exit_shares"] == pytest.approx(48.0)
    # total wealth gross is 0% (48 x $5 / 24 x $10); the raw price ratio would have
    # reported a fake -50% on the very same round trip
    assert exit_row["gross"] == pytest.approx(0.0, abs=1e-12)
    assert exit_row["gross_price"] == pytest.approx(-0.5)
    assert daily["exit_fills"] == 1
    assert book.cash == pytest.approx(cash_after_entry + msc.proceeds_usd(48.0, 5.0, 25.0))


def test_a_fractional_post_event_count_is_unknown_and_never_the_entry_quantity():
    sessions, segmap, book, boundaries = _book_setup(msc.View("A", 2))
    actions = {
        "AAA": [
            {
                "ex_date": D1,
                "action_type": "reverse_splits",
                "new_rate": 1.0,
                "old_rate": 10.0,
                "id": "x",
            }
        ]
    }
    msc.process_book_day(
        book,
        DAY,
        msc.View("A", 2),
        {"AAA": _dense(DAY, "AAA", bid=10.0, ask=10.0)},
        sessions,
        segmap,
        actions,
        boundaries,
        [],
        25.0,
        [_qual_row("AAA", D2, 2)],
    )
    cash_before_exit = book.cash
    daily, trades = msc.process_book_day(
        book,
        D2,
        msc.View("A", 2),
        {"AAA": _dense(D2, "AAA", bid=1.0, ask=1.02)},
        sessions,
        segmap,
        actions,
        boundaries,
        [],
        25.0,
        [],
    )
    exit_row = [t for t in trades if t["kind"] == "exit"][0]
    # 24 shares x 0.1 = 2.4 shares: cash-in-lieu unverified, never filled as 24
    assert exit_row["exit_status"] == "unknown:fractional_shares_cash_in_lieu_unverified"
    assert exit_row["exit_shares"] is None
    assert daily["exit_fills"] == 0 and daily["exit_unknown"] == 1
    assert len(book.retained) == 1 and book.open == []
    assert book.cash == pytest.approx(cash_before_exit)


# ----- the daily-frame rollup (inventory aggregation) --------------------------
def _daily_rows(day_positions, *, view_key="Ah2", fee=25.0):
    """One daily row per ``(day, positions_open_end)`` on one view/fee rung."""
    rows = []
    for day, (open_end, retained_end) in enumerate(day_positions):
        row = dict.fromkeys(msc.DAILY_COLUMNS, 0)
        row.update(
            {
                "day": f"2023-0{1 + day // 28}-{1 + day % 28:02d}",
                "view_key": view_key,
                "qual": "A",
                "hold": 2,
                "fee_bps": fee,
                "cash_end": 750.0,
                "reserved_end": 250.0,
                "positions_open_end": open_end,
                "positions_retained_end": retained_end,
                "qualified_today": 1,
                "entries_funded": 1,
                "entry_fills": 1,
                "exit_fills": 1,
                "exits_resolved": 1,
                "known_usd": 5.0,
                "lower_bound_usd": 5.0,
            }
        )
        rows.append(row)
    return pl.DataFrame(rows, schema={c: msc.DAILY_TYPES[c] for c in msc.DAILY_COLUMNS})


def test_the_monthly_inventory_rolls_up_each_day_end_series_exactly_once():
    # Regression: the monthly rollup consumed the day-end list twice (mean, then peak),
    # so every block's aggregation died on a KeyError before a single cell was written.
    daily = _daily_rows([(3, 0), (1, 1), (2, 0)])
    days = sorted(daily["day"].unique().to_list())
    cell = msc.view_cell(daily, msc.empty_trades(), msc.View("A", 2), days, 25.0)
    month = cell["monthly"][next(iter(cell["monthly"]))]
    # the mean and the peak are the mean and the max of the SAME observed day-end series
    assert month["positions_open_mean"] == pytest.approx(2.0)
    assert month["positions_open_peak"] == 3
    assert month["positions_retained_final"] == 0
    # and the dollars of those days are unaffected by the rollup
    assert month["known_usd"] == pytest.approx(15.0)
    assert month["exit_fills"] == 3
    # the block-level inventory mirrors the day-end series and never invents a zero
    assert cell["inventory"]["positions_open_mean"] == pytest.approx(2.0)
    assert cell["inventory"]["positions_open_peak"] == 3
    assert cell["inventory"]["reserved_usd_peak"] == 250.0
    assert cell["inventory_coverage"]["unknown_coverage_days"] == []
    assert cell["inventory_coverage"]["days_with_book_state"] == 3


def test_a_day_frame_without_the_book_state_section_is_unknown_coverage_not_zero():
    daily = _daily_rows([(3, 0), (1, 1), (2, 0)]).drop(
        ["positions_open_end", "positions_retained_end", "reserved_end"]
    )
    days = sorted(daily["day"].unique().to_list())
    cell = msc.view_cell(daily, msc.empty_trades(), msc.View("A", 2), days, 25.0)
    # a missing section is UNKNOWN coverage: never a fabricated 0-position day
    assert cell["inventory"]["positions_open_peak"] is None
    assert cell["inventory"]["positions_open_mean"] is None
    assert cell["inventory"]["positions_retained_final"] is None
    assert cell["inventory"]["reserved_usd_mean"] is None
    assert cell["inventory_coverage"]["days_with_book_state"] == 0
    assert cell["inventory_coverage"]["unknown_coverage_days"] == days
    month = cell["monthly"][next(iter(cell["monthly"]))]
    assert month["inventory_unknown_days"] == len(days)
    assert month["positions_open_peak"] is None
    # ... while the dollars, fills and skip reasons of those days are still counted
    assert month["known_usd"] == pytest.approx(15.0)
    assert month["exit_fills"] == 3
    assert month["traded_days"] == 3
