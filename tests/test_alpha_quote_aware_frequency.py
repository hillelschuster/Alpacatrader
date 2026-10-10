"""Behavioral tests for the quote-aware frequency producer's execution physics.

These cover the edges the new formulation actually turns on and that no sibling producer
asserts today:

* the RAW latest print is never pre-filtered for eligibility (a crossed, non-regular or
  stale latest NBBO stays observable and is reported, exactly like
  ``alpha_quote_audit.asof_quote`` reports it - no silent fallback to an older R print);
* a stale-but-valid print at an arrival clock is an UNKNOWN execution, never a later,
  more favourable print;
* an IOC LIMIT entry NEVER rests: a stale, invalid, non-regular or absent book at its
  arrival clock is an execution UNKNOWN, never a fill priced from a later print (resting
  belongs to the submitted MARKET exit, which may rest through an unavailable book);
* the MARKET exit releases its position, its reserved cash and the ticker cooldown at the
  clock its price actually came from - the rested print's own timestamp, never the planned
  arrival;
* the causal quantity is sized at the HIGHEST reported residual rung, so ONE q funds
  every rung and the worst-case reservation never exceeds the $250 ticket;
* the entry size, limit price and reserved cash are fixed at the INTENT quote and are
  never re-floored from a future arrival price;
* a zero funded quantity is a KNOWN no-order (no position, no priced zero return);
* the funded book honours the slot, overlap, cooldown, attempt-cap and simultaneous-clock
  funding rules without any same-clock substitution after an unfilled intent.

Synthetic frames only: no data file, no network, no live order.

Run:
  uv run --no-sync python -m pytest tests/test_alpha_quote_aware_frequency.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import pytest

sys.path.insert(0, ".")
sys.path.insert(0, "factory/scripts")

import alpha_quote_aware_frequency as aqf  # noqa: E402
from alpha_quote_audit import asof_quote, clock_us  # noqa: E402
from alpha_sparse_quote_service import day_quote_path, supplement_day_path  # noqa: E402

ET = ZoneInfo("America/New_York")
DAY = "2023-05-15"
BASE_US = clock_us(DAY, 0, 0)


def _minutes_us(minute: int, ms: int = 0) -> int:
    return clock_us(DAY, minute, ms)


def _frame(rows, ticker: str = "TEST") -> pl.DataFrame:
    """Raw quote rows: (minute, second_ms, bid, ask, bid_size, ask_size, conditions)."""
    stamps, bid, ask, bsz, asz, conds = [], [], [], [], [], []
    for minute, second_us, b, a, bs, ash, c in rows:
        stamps.append(_minutes_us(minute) + int(second_us))
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


def _dense_quotes(minutes, *, bid=10.0, ask=10.02, size=100_000.0, ticker="TEST", step_us=500_000):
    """One print every ``step_us`` across the given minutes (default: fresh every 0.5s)."""
    rows = []
    for minute in minutes:
        for off in range(0, 60_000_000, step_us):
            rows.append((minute, off, bid, ask, size, size, ("R",)))
    return _stream(rows, ticker)


# ----- raw-state observation ---------------------------------------------------
def test_latest_raw_keeps_the_ineligible_latest_print_observable():
    """A crossed / non-regular latest print is reported, never replaced by an older R."""
    rows = [
        (600, 0, 10.00, 10.02, 100, 100, ("R",)),
        (601, 0, 10.05, 10.01, 100, 100, ("R",)),  # crossed
    ]
    sq = _stream(rows)
    target = _minutes_us(602)
    q, status = sq.latest_raw(target)
    assert q is None and status == "invalid_or_crossed_quote"
    ref, ref_status = asof_quote(_frame(rows), target, DAY, 10**9)
    assert ref is None and ref_status == status
    # ... and the older regular print is NOT substituted for it.
    assert sq.raw_index(target) == 1


def test_latest_raw_reports_nonregular_latest_print():
    rows = [
        (600, 0, 10.00, 10.02, 100, 100, ("R",)),
        (601, 0, 10.00, 10.02, 100, 100, ("O",)),
    ]
    sq = _stream(rows)
    q, status = sq.latest_raw(_minutes_us(602))
    assert q is None and status == "nonregular_quote"
    ref, ref_status = asof_quote(_frame(rows), _minutes_us(602), DAY, 10**9)
    assert ref is None and ref_status == status


def test_latest_raw_locked_quote_is_eligible():
    rows = [(600, 0, 10.00, 10.00, 100, 100, ("R",))]
    sq = _stream(rows)
    q, status = sq.latest_raw(_minutes_us(600, 500))
    assert status == "quoted"
    assert q["ask"] == q["bid"] == 10.0
    assert q["age_s"] == pytest.approx(0.5)


def test_latest_raw_no_prior_print():
    sq = _stream([(600, 0, 10.0, 10.02, 100, 100, ("R",))])
    q, status = sq.latest_raw(_minutes_us(559))
    assert q is None and status == "no_prior_quote"


# ----- entry rule --------------------------------------------------------------
def _facts(**over):
    ask = float(over.get("intent_ask", 10.0))
    limit = aqf.limit_price_of(ask)
    qty = aqf.causal_quantity(limit)
    base = {
        "intent_status": "quoted",
        "intent_age_s": 0.5,
        "intent_reason": "eligible",
        "intent_ask": ask,
        "intent_bid": ask - 0.02,
        "intent_spread_bps": 20.0,
        "intent_ask_shares": 100_000.0,
        "limit_price": limit,
        "qty": qty,
        "reserved_usd": aqf.reserved_usd(qty, limit),
    }
    base.update(over)
    if "intent_ask" in over:
        base["intent_reason"] = aqf.entry_rule_status(base, float("inf"))[0]
    return base


def test_entry_rule_requires_a_firm_fresh_intent_print():
    stale = _facts(intent_age_s=3.0)
    assert aqf.entry_rule_status(stale, 0.05) == ("intent_not_firm_fresh", False)
    missing = _facts(intent_status="no_prior_quote", intent_age_s=None)
    assert aqf.entry_rule_status(missing, 0.05) == ("intent_not_firm_fresh", False)


def test_entry_rule_spread_bar_uses_the_prediction():
    ok = _facts()
    assert aqf.entry_rule_status(ok, 0.05) == ("eligible", True)  # 20 + 25 <= 500 bps
    tight = _facts(intent_spread_bps=600.0)
    assert aqf.entry_rule_status(tight, 0.05) == ("spread_above_prediction", False)
    assert aqf.entry_rule_status(ok, 0.001) == ("spread_above_prediction", False)


def test_entry_rule_depth_and_min_capital_are_distinct_outcomes():
    thin = _facts(intent_ask_shares=1.0)
    reason, ok = aqf.entry_rule_status(thin, 0.05)
    assert (reason, ok) == ("intent_depth_unsupported", False)
    rich = _facts(intent_ask=400.0)
    reason, ok = aqf.entry_rule_status(rich, 0.05)
    assert (reason, ok) == ("no_order_min_capital", False)
    assert rich["qty"] == 0


def test_zero_quantity_is_a_known_no_order_never_a_fill():
    """A min-capital state is not an unfilled attempt: no order is sent at all."""
    rich = _facts(intent_ask=400.0)
    assert rich["qty"] == 0
    assert aqf.view_reason("no_order_min_capital", False) == "no_order_min_capital"
    rows = [
        (601, 0, 400.0, 400.5, 1_000_000, 1_000_000, ("R",)),
    ]
    sq = _stream(rows)
    q, status = sq.latest_raw(_minutes_us(601))
    assert status == "quoted"
    assert aqf.causal_quantity(aqf.limit_price_of(q["ask"])) == 0


# ----- causal sizing -----------------------------------------------------------
def test_causal_quantity_is_fixed_at_the_intent_limit_not_the_arrival_price():
    limit = aqf.limit_price_of(10.0)  # 10.10 = pre-declared 1% marketability cap
    qty = aqf.causal_quantity(limit)
    assert limit == pytest.approx(10.10)
    max_rung = max(aqf.RUNG_COSTS)
    assert max_rung == pytest.approx(150.0)
    # sized at the HIGHEST reported rung, from the intent limit alone
    assert qty == int(aqf.ORDER_BUDGET // (limit * (1 + max_rung / 20_000.0)))
    # A much cheaper arrival print MUST NOT re-size the order upward: the policy is
    # floored from the intent limit, so the cheaper print is simply not exploited.
    assert qty == aqf.causal_quantity(limit)
    # the same causal q is what every rung is funded with: sizing never varies by rung
    for rung in aqf.RUNG_COSTS:
        reserved = aqf.reserved_usd(qty, limit, rung)
        assert reserved == pytest.approx(qty * limit * (1 + rung / 20_000.0))
        # every reported rung stays inside the per-ticket budget, 150 included
        assert reserved <= aqf.ORDER_BUDGET + 1e-9
    # the default reservation is the worst-case rung, not a mid-ladder one
    assert aqf.reserved_usd(qty, limit) == pytest.approx(qty * limit * (1 + max_rung / 20_000.0))


# ----- submission and pricing policy -------------------------------------------
def test_submission_uses_the_due_intent_when_the_book_is_firm_and_fresh():
    sq = _dense_quotes([615, 616, 617, 618])
    submit_us, status = aqf.submission_clock(sq, _minutes_us(616), _minutes_us(959))
    assert submit_us == _minutes_us(616)
    assert status == "submit_at_due_intent_firm_fresh"


def test_submission_holds_to_the_first_eligible_print_after_a_stale_book():
    rows = [(615, 0, 10.0, 10.02, 100, 100, ("R",))]
    for off in range(0, 40_000_000, 1_000_000):
        rows.append((619, off, 10.0, 10.02, 100, 100, ("R",)))
    sq = _stream(rows)
    due = _minutes_us(617)  # latest print is 2 minutes old -> not fresh
    submit_us, status = aqf.submission_clock(sq, due, _minutes_us(959))
    assert status == "submit_at_first_regular_after_due_intent"
    assert submit_us == _minutes_us(619)


def test_submission_ignores_an_ineligible_first_print_while_holding():
    rows = [
        (615, 0, 10.0, 10.02, 100, 100, ("R",)),
        (617, 500_000, 99.0, 98.0, 100, 100, ("R",)),  # crossed: not eligible
        (617, 900_000, 10.0, 10.02, 100, 100, ("O",)),  # non-regular: not eligible
        (618, 100_000, 10.0, 10.02, 100, 100, ("R",)),
    ]
    sq = _stream(rows)
    submit_us, status = aqf.submission_clock(sq, _minutes_us(617), _minutes_us(959))
    assert status == "submit_at_first_regular_after_due_intent"
    assert submit_us == _minutes_us(618, 100)


def test_priced_leg_treats_a_stale_but_valid_print_as_unknown():
    """A stale NBBO is not an expired order: it is simply an UNKNOWN execution here."""
    rows = [
        (601, 0, 10.0, 10.02, 100, 100, ("R",)),
        (604, 0, 12.0, 12.20, 100, 100, ("R",)),  # much more favourable, but later
    ]
    sq = _stream(rows)
    arrival = _minutes_us(603)
    q, status, priced_at = aqf.priced_leg(sq, arrival, _minutes_us(959))
    assert q is None
    assert status == "unknown_stale_but_valid_at_arrival"
    assert priced_at == arrival
    # the latest raw print at that clock really is valid-and-stale
    raw, raw_status = sq.latest_raw(arrival)
    assert raw is not None and raw_status == "quoted" and raw["age_s"] > aqf.MAX_AGE_S


def test_priced_leg_rests_to_the_first_eligible_print_when_the_book_is_absent():
    rows = [
        (601, 0, 10.0, 10.02, 100, 100, ("R",)),
        (603, 0, 12.0, 11.0, 100, 100, ("R",)),  # crossed at arrival
        (604, 0, 10.0, 10.02, 100, 100, ("R",)),  # first eligible print after arrival
    ]
    sq = _stream(rows)
    arrival = _minutes_us(603, 500)
    q, status, priced_at = aqf.priced_leg(sq, arrival, _minutes_us(959))
    assert status == "priced_rested_until_regular"
    assert priced_at == _minutes_us(604)
    assert q["ask"] == pytest.approx(10.02)
    assert q["age_s"] == pytest.approx(0.0)


def test_priced_leg_is_unknown_when_no_eligible_print_exists_before_the_close():
    rows = [
        (601, 0, 10.0, 10.02, 100, 100, ("R",)),
        (603, 0, 12.0, 11.0, 100, 100, ("R",)),  # crossed, never recovers
    ]
    sq = _stream(rows)
    q, status, priced_at = aqf.priced_leg(sq, _minutes_us(603, 500), _minutes_us(700))
    assert q is None
    assert status.startswith("unknown_no_valid_regular_at_or_after_arrival")
    assert priced_at is None


def test_entry_price_leg_never_uses_a_future_print_when_the_arrival_book_is_bad():
    """An IOC limit cannot rest: a bad book at arrival is an UNKNOWN, later R or not."""
    rows = [
        (601, 0, 10.0, 10.02, 100, 100, ("R",)),
        (601, 100_000, 10.50, 10.40, 100, 100, ("R",)),  # crossed at the arrival clock
        (605, 0, 9.90, 9.92, 100, 100, ("R",)),  # a much better, LATER print
    ]
    sq = _stream(rows)
    arrival = _minutes_us(601, 250)
    q, status = aqf.entry_price_leg(sq, arrival)
    assert q is None
    assert status == "invalid_or_crossed_quote"
    # the later favourable print exists and is still never used by the entry leg
    later, later_status = sq.first_eligible_at_or_after(arrival, _minutes_us(959))
    assert later_status == "regular_quote" and later["ask"] == pytest.approx(9.92)


def test_entry_price_leg_treats_a_stale_but_valid_arrival_print_as_unknown():
    rows = [
        (601, 0, 10.0, 10.02, 100, 100, ("R",)),
        (605, 0, 10.0, 10.02, 100, 100, ("R",)),
    ]
    sq = _stream(rows)
    q, status = aqf.entry_price_leg(sq, _minutes_us(604, 500))
    assert q is None and status == "unknown_stale_but_valid_at_arrival"


def test_entry_price_leg_prices_one_fresh_print_and_no_book_is_not_a_rest():
    rows = [
        (600, 0, 10.0, 10.02, 100, 100, ("R",)),
        (601, 0, 10.0, 10.02, 100, 100, ("O",)),  # non-regular at the arrival clock
        (603, 0, 10.0, 10.02, 100, 100, ("R",)),
    ]
    sq = _stream(rows)
    q, status = aqf.entry_price_leg(sq, _minutes_us(601, 250))
    assert q is None and status == "nonregular_quote"
    fresh, fresh_status = aqf.entry_price_leg(sq, _minutes_us(600, 250))
    assert fresh_status == "quoted_fresh_at_arrival"
    assert fresh["ask"] == pytest.approx(10.02)
    # an absent book at arrival is an UNKNOWN too, never a no-match cash claim
    empty = _stream([(601, 0, 10.0, 10.02, 100, 100, ("R",))])
    q, status = aqf.entry_price_leg(empty, _minutes_us(600, 500))
    assert q is None and status == "no_prior_quote"


# ----- supplement merge --------------------------------------------------------
def _write_quote_parquet(path: Path, rows, ticker="TEST"):
    path.parent.mkdir(parents=True, exist_ok=True)
    _frame(rows, ticker).write_parquet(path)


def test_supplement_only_adds_timestamps_and_never_revises_an_original_print(tmp_path):
    data_root = tmp_path / "data"
    sup = tmp_path / "sup"
    original = [(601, 0, 10.0, 10.02, 100, 100, ("R",))]
    _write_quote_parquet(day_quote_path(data_root, DAY), original)
    _write_quote_parquet(
        supplement_day_path(sup, DAY),
        [
            (601, 0, 99.0, 99.5, 100, 100, ("R",)),  # same print: must NOT win
            (602, 0, 10.0, 10.02, 100, 100, ("R",)),  # new timestamp: must be added
        ],
    )
    merged = aqf.quote_day_frame(data_root, DAY, {"TEST"}, (sup,))
    assert merged is not None
    by_ts = {int(row["ts_utc"].timestamp() * 1_000_000): row for row in merged.to_dicts()}
    assert by_ts[_minutes_us(601)]["bid_price"] == pytest.approx(10.0)
    assert _minutes_us(602) in by_ts
    # the frame keeps the full raw history: 2 rows, one per (symbol, timestamp)
    assert merged.height == 2


def test_missing_quote_day_file_is_a_data_unknown_not_a_cash_day(tmp_path):
    assert aqf.quote_day_frame(tmp_path / "nothing", DAY, {"TEST"}, ()) is None


# ----- funded book replay ------------------------------------------------------
def _state_row(
    ticker, t, *, pred=0.05, ask=10.0, spread_bps=20.0, depth=100_000.0, session_end=700
):
    minute = t + 1
    limit = aqf.limit_price_of(ask)
    qty = aqf.causal_quantity(limit)
    return {
        "day": DAY,
        "ticker": ticker,
        "t": t,
        "admit_t": t,
        "entry_et": t,
        "entry_open": ask,
        "entry_status": "filled_proxy",
        "session_end": session_end,
        "proxy_gross_15": 0.01,
        "proxy_gross_60": 0.02,
        "pred_15": pred,
        "pred_60": pred,
        "entry_minute": minute,
        "intent_us": _minutes_us(minute),
        "intent_status": "quoted",
        "intent_reason": "eligible",
        "intent_ask": ask,
        "intent_bid": ask - 0.02,
        "intent_age_s": 0.5,
        "intent_spread_bps": spread_bps,
        "intent_ask_shares": depth,
        "limit_price": limit,
        "qty": qty,
        "reserved_usd": aqf.reserved_usd(qty, limit),
        "rule_15": True,
        "rule_60": True,
    }


def _states_frame(rows):
    return pl.DataFrame(rows, schema=aqf.STATE_TYPES)


def _book_streams(tickers, *, bid=10.0, ask=10.02, minutes=None):
    """Dense, always-fresh quote streams for one day (fresh print every 0.5s)."""
    minutes = range(600, 701) if minutes is None else minutes
    return {
        t: _dense_quotes(list(minutes), bid=bid, ask=ask, size=100_000.0, ticker=t) for t in tickers
    }


def test_replay_respects_slots_overlap_cooldown_and_attempt_cap():
    """One ticker cannot overlap itself, re-enter inside the cooldown, or exceed 5 tries."""
    rows = [
        _state_row("AAA", 600),
        _state_row("AAA", 605),  # blocked: the 601 position is still open
        _state_row("AAA", 618),  # blocked: exit 616:00.25 + 5min cooldown
        _state_row("AAA", 625),  # allowed: well past the exit + 5min
    ]
    states = _states_frame(rows)
    streams = _book_streams(["AAA"])
    view = aqf.View(15, 0.003)
    daily, trades = aqf.replay_day(DAY, states, streams, view)
    assert daily["attempts"] == 2
    assert [tr["entry_minute"] for tr in trades] == [601, 626]
    assert daily["overlap_skips"] == 1
    assert daily["cooldown_skips"] == 1
    assert all(tr["fill_status"] == "conditional_fill" for tr in trades)
    assert len({tr["ticker"] for tr in trades}) == 1
    # the exit is the scheduled hold, priced from a fresh print, at the causal quantity
    assert trades[0]["exit_minute"] == 616
    assert trades[0]["qty"] == trades[1]["qty"]


def test_max_attempts_per_ticker_per_session_is_enforced():
    """Five attempts per ticker per session; the sixth is a causal cap skip."""
    rows = [_state_row("AAA", minute - 1, session_end=820) for minute in (601, 626, 651, 676, 701)]
    rows.append(_state_row("AAA", 725, session_end=820))
    states = _states_frame(rows)
    streams = _book_streams(["AAA"], minutes=range(600, 821))
    daily, trades = aqf.replay_day(DAY, states, streams, aqf.View(15, 0.003))
    assert daily["attempts"] == 5
    assert daily["max_attempt_skips"] == 1
    assert [tr["attempt_index"] for tr in trades] == [1, 2, 3, 4, 5]


def test_simultaneous_clock_funds_top_scores_without_substitution():
    """Three intents are funded before any arrival outcome; no fourth same-clock pick."""
    rows = [
        _state_row("AAA", 600, pred=0.05),
        _state_row("BBB", 600, pred=0.04),
        _state_row("CCC", 600, pred=0.03),
        _state_row("DDD", 600, pred=0.02),
    ]
    states = _states_frame(rows)
    streams = _book_streams(["AAA", "BBB", "CCC", "DDD"])
    view = aqf.View(15, 0.003)
    daily, trades = aqf.replay_day(DAY, states, streams, view)
    assert daily["attempts"] == 3
    assert daily["cash_or_slot_skips"] == 1
    assert {tr["ticker"] for tr in trades} == {"AAA", "BBB", "CCC"}
    assert all(tr["positions_at_entry"] <= aqf.MAX_SLOTS for tr in trades)
    assert max(tr["positions_at_entry"] for tr in trades) == 3
    # the unfunded fourth candidate is never funded later at that clock
    assert "DDD" not in {tr["ticker"] for tr in trades}


def test_no_match_entry_releases_cash_and_does_not_block_the_ticker_forever():
    """An unfilled no-match intent is cash released at its arrival, not an UNKNOWN."""
    rows = [_state_row("AAA", 600)]
    states = _states_frame(rows)
    # the arrival ask (10.50) is far above the intent-derived limit (10.01)
    streams = {"AAA": _dense_quotes(list(range(600, 701)), bid=10.4, ask=10.5, size=100_000.0)}
    view = aqf.View(15, 0.003)
    daily, trades = aqf.replay_day(DAY, states, streams, view)
    assert daily["attempts"] == 1
    assert daily["fills"] == 0
    assert daily["no_match_fills"] == 1
    assert daily["unknown_fills"] == 0
    assert daily["known_usd_25"] == 0.0
    assert trades[0]["fill_status"] == "no_match_at_l1_unfilled_cash"
    assert trades[0]["entry_status"] == "no_match_at_l1"


def test_unknown_exit_keeps_the_slot_and_blocks_the_ticker_for_the_session():
    """An unresolvable exit holds the position (and its ticket) to the session end."""
    rows = [_state_row("AAA", 600), _state_row("AAA", 690)]
    states = _states_frame(rows)
    # the book dies right after the entry intent and never comes back
    rows_q = [(601, 0, 10.0, 10.02, 100, 100, ("R",))]
    streams = {"AAA": _stream(rows_q)}
    view = aqf.View(15, 0.003)
    daily, trades = aqf.replay_day(DAY, states, streams, view)
    assert daily["attempts"] == 1
    assert daily["unknown_fills"] == 1
    assert daily["fills"] == 0
    assert trades[0]["fill_status"] == "unknown_exit_execution"
    assert trades[0]["actual_exit_us"] == _minutes_us(700) + 1
    assert daily["lower_bound_usd_25"] == -aqf.ORDER_BUDGET
    assert daily["known_usd_25"] == 0.0


def test_known_contribution_uses_the_causal_quantity_and_both_leg_fees():
    rows = [_state_row("AAA", 600)]
    states = _states_frame(rows)
    streams = _book_streams(["AAA"], bid=10.0, ask=10.02)
    view = aqf.View(15, 0.003)
    daily, trades = aqf.replay_day(DAY, states, streams, view)
    trade = trades[0]
    qty = int(trade["qty"])
    expected = qty * (
        float(trade["exit_bid"]) * (1 - 25.0 / 20_000.0)
        - float(trade["entry_ask"]) * (1 + 25.0 / 20_000.0)
    )
    assert trade["fill_status"] == "conditional_fill"
    assert trade["net_usd_25"] == pytest.approx(expected)
    assert daily["known_usd_25"] == pytest.approx(expected)
    # the rung ladder only moves the fee, never the quantity or the fill decision
    assert trade["qty"] == qty and trade["net_usd_100"] < trade["net_usd_0"]


def test_rung_zero_is_cheaper_than_the_primary_rung():
    rows = [_state_row("AAA", 600)]
    states = _states_frame(rows)
    streams = _book_streams(["AAA"])
    daily, _ = aqf.replay_day(DAY, states, streams, aqf.View(15, 0.003))
    assert daily["known_usd_0"] > daily["known_usd_25"] > daily["known_usd_100"]


# ----- repaired execution physics: IOC no-rest, real exit clock, fee grid -------
def test_ioc_entry_with_a_bad_arrival_book_is_unknown_even_when_a_later_print_exists():
    """The entry cannot rest: a crossed print at arrival is an UNKNOWN, not a later fill."""
    rows = [
        _state_row("AAA", 600),
        _state_row("AAA", 615),  # would re-enter if the slot were ever released
    ]
    states = _states_frame(rows)
    # fresh at the intent (601.0), crossed at the arrival (601.25), fresh again at 603.0
    quote_rows = [
        (601, 0, 10.0, 10.02, 100_000, 100_000, ("R",)),
        (601, 250_000, 10.50, 10.40, 100_000, 100_000, ("R",)),
        (603, 0, 9.90, 9.92, 100_000, 100_000, ("R",)),
    ]
    streams = {"AAA": _stream(quote_rows)}
    view = aqf.View(15, 0.003)
    daily, trades = aqf.replay_day(DAY, states, streams, view)
    assert daily["attempts"] == 1
    assert daily["fills"] == 0
    assert daily["known_fills"] == 0
    assert daily["unknown_fills"] == 1
    assert daily["known_usd_25"] == 0.0
    assert daily["lower_bound_usd_25"] == -aqf.ORDER_BUDGET
    trade = trades[0]
    assert trade["fill_status"] == "unknown_entry_execution"
    assert trade["entry_status"] == "invalid_or_crossed_quote"
    # never priced: no entry price at all, so the later 9.92 print cannot have filled it
    assert trade["entry_ask"] is None
    # the unresolved position holds the slot and the ticker for the session
    assert trade["actual_exit_us"] == _minutes_us(700) + 1
    assert daily["cooldown_skips"] == 0 and daily["max_attempt_skips"] == 0


def test_market_exit_rested_print_holds_cash_and_cooldown_until_the_price_clock():
    """A market exit priced five minutes late holds the slot, cash and cooldown till then."""
    rows = [
        _state_row("AAA", 600),  # exit 616 priced from a rested print at 621
        _state_row("BBB", 600),  # exit never resolves: holds its ticket to the session end
        _state_row("CCC", 600),  # exit never resolves: holds its ticket to the session end
        _state_row("DDD", 619),  # intent at 620: between the planned arrival and the price
    ]
    states = _states_frame(rows)
    quote_rows = []
    # a fresh book through the exit due intent (616.0) so AAA's sell is submitted there
    for minute in range(600, 616):
        for off in range(0, 60_000_000, 500_000):
            quote_rows.append((minute, off, 10.0, 10.02, 100_000, 100_000, ("R",)))
    quote_rows.append((616, 0, 10.0, 10.02, 100_000, 100_000, ("R",)))  # due intent: fresh
    quote_rows.append((616, 100_000, 10.50, 10.40, 100_000, 100_000, ("R",)))  # crossed
    quote_rows.append((621, 0, 10.0, 10.02, 100_000, 100_000, ("R",)))  # first eligible
    dead = [(601, 0, 10.0, 10.02, 100, 100, ("R",))]  # the book dies after the intent
    streams = {
        "AAA": _stream(quote_rows),
        "BBB": _stream(dead),
        "CCC": _stream(dead),
        "DDD": _book_streams(["DDD"])["DDD"],
    }
    view = aqf.View(15, 0.003)
    daily, trades = aqf.replay_day(DAY, states, streams, view)
    trade = trades[0]
    exit_arrival = _minutes_us(616, 250)
    priced_clock = _minutes_us(621)
    assert trade["exit_price_status"] == "priced_rested_until_regular"
    assert trade["exit_arrival_us"] == exit_arrival
    # the position, its cash and the cooldown are anchored at the PRICED clock
    assert trade["actual_exit_us"] == priced_clock
    assert priced_clock > exit_arrival
    # DDD's intent at 620 finds the book still fully committed: AAA's cash and slot are
    # NOT released at the planned arrival (616.25) - they wait for the 621 price clock.
    assert daily["attempts"] == 3
    assert daily["cash_or_slot_skips"] == 1
    assert [tr["ticker"] for tr in trades] == ["AAA", "BBB", "CCC"]
    assert daily["fills"] == 1 and daily["unknown_fills"] == 2
    assert daily["known_usd_25"] == pytest.approx(trade["net_usd_25"])
    # the held exits are charged a full unit each in the lower bound
    assert daily["lower_bound_usd_25"] == pytest.approx(trade["net_usd_25"] - 2 * aqf.ORDER_BUDGET)
    # the fill itself is still priced from the rested print at the causal quantity
    assert trade["fill_status"] == "conditional_fill"
    assert trade["exit_quote_us"] == priced_clock
    assert trade["exit_bid"] == pytest.approx(10.0)  # the 621 print's BID, not its ask
    assert trade["qty"] >= 1


def test_fee_grid_reuses_one_causal_quantity_inside_the_ticket_budget():
    """Every rung 0..150 is priced with the SAME causal q and stays inside the ticket."""
    rows = [_state_row("AAA", 600)]
    states = _states_frame(rows)
    streams = _book_streams(["AAA"], bid=10.0, ask=10.02)
    view = aqf.View(15, 0.003)
    daily, trades = aqf.replay_day(DAY, states, streams, view)
    trade = trades[0]
    qty = int(trade["qty"])
    assert qty >= 1
    max_rung = max(aqf.RUNG_COSTS)
    # the funding sized q at the HIGHEST rung and reserved within the $250 ticket
    assert trade["reserved_usd"] == pytest.approx(
        qty * trade["limit_price"] * (1 + max_rung / 20_000.0)
    )
    assert trade["reserved_usd"] <= aqf.ORDER_BUDGET + 1e-9
    for rung in aqf.RUNG_COSTS:
        expected = qty * (
            float(trade["exit_bid"]) * (1 - rung / 20_000.0)
            - float(trade["entry_ask"]) * (1 + rung / 20_000.0)
        )
        assert trade[f"net_usd_{int(rung)}"] == pytest.approx(expected)
        assert daily[f"known_usd_{int(rung)}"] == pytest.approx(expected)
    # the ladder only moves the fee: same quantity, same decision, monotone drag
    assert trade["net_usd_0"] > trade["net_usd_150"]
    assert trade["net_usd_150"] == pytest.approx(
        trade["net_usd_0"]
        - qty * (float(trade["entry_ask"]) + float(trade["exit_bid"])) * (max_rung / 20_000.0)
    )
