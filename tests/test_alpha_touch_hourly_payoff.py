"""Behavioral tests for the actual-touch hourly payoff producer's execution physics.

These cover the consumer-visible edges this formulation actually turns on and that no
sibling producer asserts today:

* the LABEL is the ASK-to-BID NBBO touch of a real marketable-IOC entry held h minutes:
  gross-before-extra, the spread inside the realized prices and never subtracted again;
* the LIMIT IOC entry evaluates the arrival print ONCE and can NEVER rest to a later
  eligible quote (a crossed / non-regular / absent / stale-but-valid arrival is an
  execution UNKNOWN, never a substituted future print, and q is never re-sized);
* a MARKET exit may rest through a genuinely unavailable book, never merely for an
  aged-but-valid quote, and the cash / slot / cooldown are released at the REAL
  execution clock (the rested print's own arrival), not the nominal due-arrival;
* a policy that sends no order (q == 0, an unfirm intent book, thin intent depth) and an
  IOC no-L1-match are KNOWN cash zeros, while a never-acquired stream is a DATA UNKNOWN;
* the funded book honours the slot / overlap / cooldown / attempt-cap / simultaneous
  rules at the 150bps worst-rung reserve, and the per-view net-clearance bar charges the
  extra fee separately from the observed touch.

Synthetic frames only: no data file, no network, no live order.

Run:
  uv run --no-sync python -m pytest tests/test_alpha_touch_hourly_payoff.py -q
"""

from __future__ import annotations

import sys
from zoneinfo import ZoneInfo

import polars as pl
import pytest

sys.path.insert(0, ".")
sys.path.insert(0, "factory/scripts")

import alpha_touch_hourly_payoff as atp  # noqa: E402

ET = ZoneInfo("America/New_York")
DAY = "2023-05-15"
SESSION_END = 700


def _minutes_us(minute: int, ms: int = 0) -> int:
    from alpha_quote_audit import clock_us

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
    return atp.symbol_quotes(_frame(rows, ticker), DAY)[ticker]


def _dense(minutes, *, bid=10.0, ask=10.02, size=100_000.0, ticker="TEST", step_us=500_000):
    """One fresh R print every ``step_us`` across the given minutes."""
    rows = []
    for minute in minutes:
        for off in range(0, 60_000_000, step_us):
            rows.append((minute, off, bid, ask, size, size, ("R",)))
    return _stream(rows, ticker)


def _cand(ticker: str = "TEST", t: int = 600, session_end: int = SESSION_END):
    """One liquidity-qualified panel state with the 26 base features present."""
    row = {
        "day": DAY,
        "ticker": ticker,
        "t": t,
        "session_end": session_end,
    }
    for c in atp.FEATURES_ALL:
        row[c] = 1.0 if c not in ("minute", "bars15", "rank_snapshot") else 12.0
    return row


def _cand_frame(rows):
    schema = {
        "day": pl.String,
        "ticker": pl.String,
        "t": pl.Int64,
        "session_end": pl.Int64,
        **dict.fromkeys(atp.FEATURES_ALL, pl.Float64),
    }
    return pl.DataFrame(rows, schema=schema)


def _labels(rows, streams):
    frame = _cand_frame(rows)
    out = atp.label_rows_for_day(DAY, frame, streams)
    return out[0] if len(out) == 1 else out


# ----- label physics ----------------------------------------------------------
def test_label_is_the_ask_to_bid_touch_gross_before_extra():
    streams = {"TEST": _dense(range(600, 620))}
    rows_l = _labels([_cand()], streams)
    entry_ask, exit_bid = 10.02, 10.00
    expected = exit_bid / entry_ask - 1.0
    assert rows_l["entry_execution_status"] == "conditional_ioc_fill"
    assert rows_l["exit_status_h15"] == "conditional_market_fill"
    assert rows_l["y_touch_h15"] == pytest.approx(expected)
    assert rows_l["label_known_h15"] is True
    assert rows_l["label_price_basis"] == atp.LABEL_PRICE_BASIS
    # the limit is a pre-declared marketability cap and q is funded at the 150bps cap
    limit = atp.limit_price_of(10.02)
    assert limit == pytest.approx(10.13)  # ceil_to_cent(10.02 * 1.01)
    assert rows_l["causal_qty"] == int(250.0 // (limit * (1.0 + 150.0 / 20_000.0)))
    # the five quote features are observed at the INTENT clock (2023 sizes are x100 lots)
    assert rows_l["spread_bps"] == pytest.approx(20.0)
    assert rows_l["quote_age_s"] == pytest.approx(0.0)
    assert rows_l["log_ask_shares"] == pytest.approx(16.118095750958314)  # log1p(100k lots)
    assert rows_l["depth_imbalance"] == pytest.approx(0.0)


def test_label_exit_hold_is_capped_at_the_session_end():
    rows = [_cand(t=690, session_end=700)]
    streams = {"TEST": _dense(range(600, 701))}
    out = _labels(rows, streams)
    assert out["entry_execution_status"] == "conditional_ioc_fill"
    # entry minute 691 + 15 and + 60 both cap at the session end 700
    assert out["exit_status_h15"] == "conditional_market_fill"
    assert out["exit_status_h60"] == "conditional_market_fill"
    assert out["label_known_h60"] is True


# ----- LIMIT IOC entry: evaluate once, never rest -----------------------------
def test_ioc_entry_cannot_rest_to_a_later_eligible_print():
    rows = [
        (600, 0, 10.00, 10.02, 100, 100, ("R",)),
        (601, 0, 10.00, 10.02, 100, 100, ("R",)),  # fresh at the intent clock
        (601, 100_000, 10.50, 10.00, 100, 100, ("R",)),  # crossed at the arrival clock
        (602, 0, 10.00, 10.02, 100, 100, ("R",)),  # a perfectly good LATER print
    ]
    streams = {"TEST": _stream(rows)}
    out = _labels([_cand()], streams)
    assert out["entry_execution_status"] == "unknown_entry_execution"
    assert out["y_touch_h15"] is None
    assert out["label_known_h15"] is False
    assert out["exit_status_h15"] == "not_applicable_unknown_entry"


def test_stale_but_valid_arrival_is_unknown_not_a_rest():
    intent = _minutes_us(601)
    rows = [
        (intent - 2_000_000, None),  # placeholder replaced below
    ]
    # a single print exactly 2.0s before the intent: fresh at the intent (age == 2.0),
    # 2.25s old at the arrival (age > 2.0) -> UNKNOWN, never the later fresh print.
    rows = [
        (600, 58_000_000, 10.00, 10.02, 100, 100, ("R",)),
        (603, 0, 10.00, 10.02, 100, 100, ("R",)),
    ]
    streams = {"TEST": _stream(rows)}
    obs = atp.intent_observation(streams["TEST"], intent)
    assert obs["intent_status"] == "quoted"
    assert obs["quote_age_s"] == pytest.approx(2.0)
    assert atp.intent_verdict(obs) == "eligible"
    out = _labels([_cand()], streams)
    assert out["entry_execution_status"] == "unknown_entry_stale_but_valid_at_arrival"
    assert out["label_known_h15"] is False


def test_entry_ask_above_limit_is_known_cash_zero():
    rows = [
        (600, 0, 10.00, 10.02, 100, 100, ("R",)),
        (601, 0, 10.00, 10.02, 100, 100, ("R",)),  # fresh at the intent clock
        (601, 100_000, 12.00, 12.02, 100, 100, ("R",)),  # arrival ask above the limit
    ]
    streams = {"TEST": _stream(rows)}
    out = _labels([_cand()], streams)
    assert out["entry_execution_status"] == "no_match_at_l1"
    assert out["y_touch_h15"] == 0.0
    assert out["y_touch_h60"] == 0.0
    assert out["label_known_h15"] is True
    assert out["label_known_h60"] is True
    assert out["exit_status_h15"] == "no_position_cash"


def test_entry_partial_depth_is_unknown():
    rows = [
        (600, 0, 10.00, 10.02, 100, 100, ("R",)),
        (601, 0, 10.00, 10.02, 100, 100, ("R",)),  # deep intent book: the order is sent
        (601, 100_000, 10.00, 10.02, 0.1, 0.1, ("R",)),  # arrival depth below causal q
    ]
    streams = {"TEST": _stream(rows)}
    out = _labels([_cand()], streams)
    assert out["entry_execution_status"] == "unknown_entry_partial_depth"
    assert out["label_known_h15"] is False


def test_no_order_states_are_known_cash_zeros():
    # (a) minimum capital: a price so high the $250 ticket funds no whole share
    rows = [_cand()]
    streams = {"TEST": _dense(range(600, 620), bid=400.0, ask=400.5)}
    out = _labels(rows, streams)
    assert out["entry_execution_status"] == "no_order_min_capital"
    assert out["causal_qty"] == 0
    assert out["y_touch_h15"] == 0.0 and out["label_known_h15"] is True
    # (b) intent top-of-book depth below the causal quantity (2023 sizes are x100 lots)
    streams = {"TEST": _dense(range(600, 620), bid=10.0, ask=10.02, size=0.1)}
    out = _labels(rows, streams)
    assert out["entry_execution_status"] == "no_order_intent_depth_unsupported"
    assert out["y_touch_h15"] == 0.0 and out["label_known_h15"] is True
    # (c) the latest intent print is crossed: no firm book, so no order is sent
    streams = {
        "TEST": _stream(
            [
                (600, 0, 10.00, 10.02, 100, 100, ("R",)),
                (601, 0, 10.50, 10.00, 100, 100, ("R",)),
            ]
        )
    }
    out = _labels(rows, streams)
    assert out["entry_execution_status"] == "no_order_intent_not_firm_fresh"
    assert out["y_touch_h15"] == 0.0 and out["label_known_h15"] is True
    # an unobservable intent book carries NULL quote features (never imputed as tight)
    assert out["spread_bps"] is None and out["log_ask_shares"] is None


def test_missing_quote_stream_is_a_data_unknown():
    out = _labels([_cand()], {})
    assert out["entry_execution_status"] == "quote_not_acquired"
    assert out["y_touch_h15"] is None
    assert out["label_known_h15"] is False
    assert out["y_touch_h60"] is None
    assert out["label_known_h60"] is False


# ----- MARKET exit: fresh-gated, may rest only through an unavailable book -----
def test_exit_rests_to_the_first_eligible_print_and_prices_at_its_arrival():
    rows = [(601, 0, 10.00, 10.02, 100, 100, ("R",))]
    for minute in range(602, 616):
        rows.append((minute, 0, 10.00, 10.02, 100, 100, ("R",)))
    rows.append((617, 500_000, 10.40, 10.42, 100, 100, ("R",)))  # first print after the due
    rows.append((618, 0, 10.40, 10.42, 100, 100, ("R",)))
    streams = {"TEST": _stream(rows)}
    out = _labels([_cand()], streams)
    assert out["exit_status_h15"] == "conditional_market_fill"
    # due 616:00 is not fresh -> rested to 617:30 and priced at its +250ms arrival
    assert out["y_touch_h15"] == pytest.approx(10.40 / 10.02 - 1.0)


def test_exit_stale_at_arrival_is_unknown_and_never_rests():
    # the exit submission is fresh at the due intent (age == 2.0s), but the arrival is
    # 2.25s old: an aged-but-valid quote NEVER keeps a market order working, so the exit
    # is an execution UNKNOWN and the later better book is never used.
    rows = [
        (601, 0, 10.00, 10.02, 100, 100, ("R",)),
        (602, 0, 10.00, 10.02, 100, 100, ("R",)),
        (615, 58_000_000, 10.00, 10.02, 100, 100, ("R",)),  # exactly 2s before the due
        (618, 0, 10.40, 10.42, 100, 100, ("R",)),
    ]
    streams = {"TEST": _stream(rows)}
    out = _labels([_cand()], streams)
    assert out["exit_status_h15"] == "unknown_exit_stale_but_valid_at_arrival"
    assert out["y_touch_h15"] is None
    assert out["label_known_h15"] is False


def test_exit_partial_depth_is_unknown():
    rows = [(601, 0, 10.00, 10.02, 100, 100, ("R",))]
    for minute in range(602, 701):
        rows.append((minute, 0, 10.00, 10.02, 100, 100, ("R",)))
    rows[15] = (616, 0, 10.00, 10.02, 0.1, 100, ("R",))  # exit BID depth below causal q
    streams = {"TEST": _stream(rows)}
    out = _labels([_cand()], streams)
    assert out["exit_status_h15"] == "unknown_exit_partial_depth"
    assert out["label_known_h15"] is False
    assert out["label_known_h60"] is True  # h60 exits at 661 with a healthy book


# ----- fit contracts ----------------------------------------------------------
def test_fit_rows_keep_known_observable_rows_and_drop_the_rest():
    base = {
        "day": DAY,
        "ticker": "TEST",
        "t": 600,
        "entry_intent_us": 1,
        "causal_qty": 24,
        "label_price_basis": atp.LABEL_PRICE_BASIS,
    }
    rows = []
    for status, known in (
        ("conditional_ioc_fill", True),
        ("no_match_at_l1", True),
        ("no_order_min_capital", True),
        ("no_order_intent_not_firm_fresh", True),  # known but NO observable intent book
        ("quote_not_acquired", False),
        ("unknown_entry_execution", False),
        ("unknown_entry_partial_depth", False),
    ):
        row = dict(base)
        row["entry_execution_status"] = status
        row["y_touch_h15"] = 0.01 if known else None
        row["y_touch_h60"] = 0.01 if known else None
        row["label_known_h15"] = known
        row["label_known_h60"] = known
        row["exit_status_h15"] = "x"
        row["exit_status_h60"] = "x"
        rows.append(row)
    frame = pl.DataFrame(rows, schema={c: atp.LABEL_TYPES[c] for c in atp.LABEL_COLUMNS})
    fit = atp.fit_rows(frame, 15)
    assert set(fit["entry_execution_status"].to_list()) == {
        "conditional_ioc_fill",
        "no_match_at_l1",
        "no_order_min_capital",
    }


def test_equal_day_weights_give_every_session_the_same_total_weight():
    days = ["a"] * 9 + ["b"] * 1
    w = atp.equal_day_weights(days)
    assert w.mean() == pytest.approx(1.0)
    assert w[:9].sum() == pytest.approx(5.0)  # session "a" total weight
    assert w[9] == pytest.approx(5.0)  # session "b" carries the same total weight
    assert w[:9][0] < w[9]  # the heavy session's ROWS each weigh less


# ----- views, replay book and cost separation ---------------------------------
def _state_row(
    ticker,
    t,
    *,
    pred=0.02,
    ask=10.02,
    spread_bps=20.0,
    depth=100_000.0,
    session_end=SESSION_END,
    reason="eligible",
    y=0.01,
):
    minute = t + 1
    limit = atp.limit_price_of(ask)
    qty = atp.causal_quantity(limit, atp.LABEL_COST_BPS)
    return {
        "day": DAY,
        "ticker": ticker,
        "t": t,
        "entry_minute": minute,
        "intent_us": _minutes_us(minute),
        "session_end": session_end,
        "intent_status": "quoted" if reason == "eligible" else reason,
        "intent_reason": reason,
        "intent_ask": ask,
        "intent_bid": ask - 0.02,
        "intent_age_s": 0.25,
        "intent_spread_bps": spread_bps,
        "intent_ask_shares": depth,
        "limit_price": limit,
        "qty": qty,
        "reserved_usd": atp.reserved_usd(qty, limit, atp.LABEL_COST_BPS),
        "pred_h15": pred,
        "pred_h60": pred,
        "y_touch_h15": y,
        "y_touch_h60": y,
        "label_known_h15": True,
        "label_known_h60": True,
        "entry_execution_status": "conditional_ioc_fill",
    }


def _states_frame(rows):
    return pl.DataFrame(rows, schema=atp.STATE_TYPES)


def _book_streams(tickers, *, bid=10.0, ask=10.02, minutes=None):
    minutes = range(600, 701) if minutes is None else minutes
    return {t: _dense(list(minutes), bid=bid, ask=ask, ticker=t) for t in tickers}


def test_clearance_bar_is_the_fee_residual_not_the_spread():
    # y_touch is the spread-INCLUSIVE ASK->BID touch (exit_bid/entry_ask - 1), so the
    # per-view bar charges ONLY the provider-extra fee residual (ENTRY_OVERHEAD_BPS=25)
    # plus the view clearance - NEVER the observed spread a second time.
    pred = 0.0070  # 70bps predicted touch payoff
    row = _state_row("AAA", 600, pred=pred, spread_bps=20.0)
    # bar = (25 + 10_000*clearance)/10000
    assert atp.view_status(row, atp.View(15, 0.0))[1] is True  # 0.0070 >= 0.0025
    assert atp.view_status(row, atp.View(15, 0.0025))[1] is True  # 0.0070 >= 0.0050
    assert atp.view_status(row, atp.View(15, 0.005))[1] is False  # 0.0070 < 0.0075
    assert atp.view_status(row, atp.View(15, 0.010))[1] is False
    # an ARBITRARILY wide observable spread is IGNORED by the gate (no double charge):
    # the same prediction that cleared the fee bar at a 20bps spread still clears it with
    # a 500bps observable spread, and that 500bps spread is STILL recorded on the row.
    wide = _state_row("AAA", 600, pred=pred, spread_bps=500.0)
    assert wide["intent_spread_bps"] == pytest.approx(500.0)  # spread stays a DPT feature
    assert atp.view_status(wide, atp.View(15, 0.0))[1] is True
    assert atp.view_status(wide, atp.View(15, 0.0025))[1] is True
    assert atp.view_status(wide, atp.View(15, 0.005))[1] is False  # 0.0070 < 0.0075 fee bar
    # the fee-residual floor itself does not move with the observed spread
    at_floor = _state_row("AAA", 600, pred=25.0 / 10_000.0, spread_bps=500.0)
    assert atp.view_status(at_floor, atp.View(15, 0.0))[1] is True  # 25bps == the residual
    below = _state_row("AAA", 600, pred=0.0024, spread_bps=500.0)  # 24bps < 25bps residual
    status, ok = atp.view_status(below, atp.View(15, 0.0))
    assert ok is False and status == "touch_payoff_below_clearance"
    # the intent gates are head independent and come first
    thin = _state_row("AAA", 600, reason="no_order_min_capital")
    for view in atp.VIEWS:
        status, ok = atp.view_status(thin, view)
        assert ok is False and status == "no_order_min_capital"


def test_replay_uses_the_150bps_reserve_and_three_slots():
    rows = [
        _state_row("AAA", 600, pred=0.05),
        _state_row("BBB", 600, pred=0.04),
        _state_row("CCC", 600, pred=0.03),
        _state_row("DDD", 600, pred=0.02),
    ]
    states = _states_frame(rows)
    streams = _book_streams(["AAA", "BBB", "CCC", "DDD"])
    view = atp.View(15, 0.0)
    daily, trades = atp.replay_day(DAY, states, streams, view)
    assert daily["attempts"] == 3
    assert daily["cash_or_slot_skips"] == 1
    assert {tr["ticker"] for tr in trades} == {"AAA", "BBB", "CCC"}
    # reserve at the worst reported rung (150bps), not at the 100bps of the sibling
    limit = atp.limit_price_of(10.02)
    qty = atp.causal_quantity(limit, 150.0)
    assert trades[0]["qty"] == qty
    assert trades[0]["reserved_usd"] == pytest.approx(qty * limit * (1 + 150.0 / 20_000.0))
    assert daily["peak_reserved_usd"] == pytest.approx(3 * qty * limit * (1 + 150.0 / 20_000.0))


def test_replay_respects_slots_overlap_cooldown_and_attempt_cap():
    rows = [
        _state_row("AAA", 600),
        _state_row("AAA", 605),  # blocked: the 601 position is still open
        _state_row("AAA", 618),  # blocked: exit 616 + 5min cooldown
        _state_row("AAA", 625),  # allowed: well past the exit + 5min
    ]
    states = _states_frame(rows)
    streams = _book_streams(["AAA"])
    view = atp.View(15, 0.0)
    daily, trades = atp.replay_day(DAY, states, streams, view)
    assert daily["attempts"] == 2
    assert [tr["entry_minute"] for tr in trades] == [601, 626]
    assert daily["overlap_skips"] == 1
    assert daily["cooldown_skips"] == 1
    assert all(tr["fill_status"] == "conditional_fill" for tr in trades)


def test_rested_exit_releases_the_slot_at_the_real_execution_clock():
    rows = [_state_row("AAA", 600), _state_row("AAA", 616)]
    states = _states_frame(rows)
    quote_rows = [(601, 0, 10.00, 10.02, 100, 100, ("R",))]
    for minute in range(602, 616):
        quote_rows.append((minute, 0, 10.00, 10.02, 100, 100, ("R",)))
    quote_rows.append((617, 500_000, 10.40, 10.42, 100, 100, ("R",)))  # first print >= due
    streams = {"AAA": _stream(quote_rows)}
    view = atp.View(15, 0.0)
    daily, trades = atp.replay_day(DAY, states, streams, view)
    assert daily["attempts"] == 1  # the 617 re-entry never funds: the slot is still held
    assert daily["overlap_skips"] == 1
    tr = trades[0]
    # the REAL execution clock is the rested print's own arrival (617:30 + 250ms),
    # never the nominal due arrival (616:00 + 250ms) and never the print's quote_us
    assert tr["fill_status"] == "conditional_fill"
    assert tr["actual_exit_us"] == _minutes_us(617, 500) + 250_000
    assert tr["exit_bid"] == pytest.approx(10.40)


def test_no_match_releases_cash_and_unknown_holds_the_slot_to_session_end():
    rows = [_state_row("AAA", 600)]
    states = _states_frame(rows)
    streams = {"AAA": _dense(list(range(600, 701)), bid=10.4, ask=10.5)}
    view = atp.View(15, 0.0)
    daily, trades = atp.replay_day(DAY, states, streams, view)
    assert daily["no_match_fills"] == 1
    assert daily["unknown_fills"] == 0
    assert trades[0]["actual_exit_us"] == _minutes_us(601) + 250_000

    rows = [_state_row("AAA", 600)]
    states = _states_frame(rows)
    streams = {"AAA": _stream([(601, 0, 10.00, 10.02, 100, 100, ("R",))])}
    daily, trades = atp.replay_day(DAY, states, streams, view)
    assert daily["unknown_fills"] == 1
    assert trades[0]["actual_exit_us"] == _minutes_us(SESSION_END) + 1
    assert daily["lower_bound_usd_25"] == pytest.approx(-atp.ORDER_BUDGET)


def test_rungs_charge_only_the_extra_fee_on_top_of_the_touch():
    rows = [_state_row("AAA", 600, ask=10.52)]
    states = _states_frame(rows)
    streams = _book_streams(["AAA"], bid=10.5, ask=10.52)
    view = atp.View(15, 0.0)
    daily, trades = atp.replay_day(DAY, states, streams, view)
    tr = trades[0]
    qty, entry_ask, exit_bid = tr["qty"], tr["entry_ask"], tr["exit_bid"]
    assert entry_ask == pytest.approx(10.52) and exit_bid == pytest.approx(10.50)
    assert tr["y_touch"] == pytest.approx(exit_bid / entry_ask - 1.0)
    # rung 0 is the pure observed touch: the spread is inside the prices, never re-charged
    assert tr["net_usd_0"] == pytest.approx(qty * (exit_bid - entry_ask))
    # each rung adds the extra fee on BOTH legs, and nothing else moves
    side = 25.0 / 20_000.0
    assert tr["net_usd_25"] == pytest.approx(qty * (exit_bid * (1 - side) - entry_ask * (1 + side)))
    assert daily["known_usd_0"] > daily["known_usd_25"] > daily["known_usd_150"]
    extra = daily["known_usd_0"] - daily["known_usd_25"]
    assert extra == pytest.approx(qty * (entry_ask + exit_bid) * side)


def test_choose_view_is_validation_only_argmax_with_deterministic_tie_break():
    surface = {}
    for v in atp.VIEWS:
        cell = {
            "known_usd_per_calendar_day": {"25": 0.0},
            "known_fills": 0,
            "unknown_fills": 0,
            "traded_days": 0,
        }
        surface[v.key] = cell
    best, runner = atp.VIEWS[3], atp.VIEWS[5]
    surface[best.key]["known_usd_per_calendar_day"]["25"] = 1.5
    surface[runner.key]["known_usd_per_calendar_day"]["25"] = 1.5  # tie on the objective
    surface[runner.key]["known_fills"] = 10
    chosen, ranking = atp.choose_view(surface)
    assert chosen.key == runner.key  # tie broken by known fills, then key
    assert ranking[0]["view_key"] == runner.key
    assert ranking[0]["validation_known_usd_per_calendar_day_25"] == 1.5


def test_selection_objective_is_the_25bps_extra_rung_not_a_synthetic_hurdle():
    surface = {}
    for v in atp.VIEWS:
        surface[v.key] = {
            "known_usd_per_calendar_day": {"25": -5.0},
            "known_fills": 3,
            "unknown_fills": 0,
            "traded_days": 3,
        }
    surface[atp.VIEWS[0].key]["known_usd_per_calendar_day"]["25"] = -0.01
    chosen, _ = atp.choose_view(surface)
    assert chosen.key == atp.VIEWS[0].key
    # a negative-but-best view is still chosen (no kill gate); the decision text says so
    text = atp.decision_text(
        chosen,
        {
            "known_usd_per_calendar_day": {"25": -0.01},
            "known_fills": 3,
            "bootstrap_daily_known_usd": {"25bps": {"p_gt_zero": 0.2}},
        },
        None,
        frozen=False,
    )
    assert "NO_ACTUAL_TOUCH_POSITIVE_FORMULATION" in text
