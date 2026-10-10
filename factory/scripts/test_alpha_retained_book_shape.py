"""Funding-edge regressions for the retained book-shape grid.

Consumer-visible account behaviors that are easy to get wrong and expensive when
wrong, all on synthetic states (no panel, no model, no network): the whole-share
quantity floor taken from the OBSERVED entry bar, the zero-floor KNOWN no-order
cash skip (never a fill, never an UNKNOWN, never an attempt), same-clock funding
with NO substitution, the cooldown anchored on the ACTUAL exit, an UNKNOWN exit
holding BOTH cash and slot to the session end, cost-invariant cash restoration so
every rung of a cell shares one fill set, and the both-legs fee formula. Every
assertion is about account behavior a future reader would rely on.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import polars as pl  # noqa: E402
from alpha_retained_book_shape import (  # noqa: E402
    CELL_COSTS_BPS,
    CELLS,
    CELLS_BY_KEY,
    COOLDOWN_MIN,
    COST_KEYS,
    MAX_ATTEMPTS,
    STATE_TYPES,
    Cell,
    empty_daily,
    quantity_of,
    replay_day,
)

DAY = "2023-06-15"
SESSION_END = 895


def state(
    ticker,
    t,
    entry_open,
    *,
    score=0.05,
    entry_status="filled_proxy",
    gross=0.01,
    exit_et=None,
    exit_status="observed_open_proxy",
):
    observed = exit_status == "observed_open_proxy"
    return {
        "day": DAY,
        "ticker": ticker,
        "t": int(t),
        "entry_et": int(t),
        "entry_open": entry_open,
        "entry_status": entry_status,
        "session_end": SESSION_END,
        "score": float(score),
        "gross_60": gross if observed else None,
        "exit_et_60": (int(t) + 60 if exit_et is None else int(exit_et)) if observed else None,
        "exit_status_60": exit_status,
    }


def frame(rows):
    return pl.DataFrame(rows, schema=STATE_TYPES)


def one(rows, cell_key="s3_t1000"):
    """Replay one synthetic session for one cell; returns (daily dict, trades frame)."""
    daily, trades = replay_day(DAY, frame(rows))
    row = daily.filter(pl.col("cell_key") == cell_key).to_dicts()[0]
    return row, trades.filter(pl.col("cell_key") == cell_key)


def net_at(gross, bps):
    side = bps / 20_000.0
    return (1.0 + gross) * (1.0 - side) / (1.0 + side) - 1.0


def test_quantity_is_fixed_from_the_observed_entry_bar():
    # 250 / 12.30 = 20.32 -> 20 whole shares, notional 246.00 (never resized later).
    assert quantity_of(250.0, state("A", 600, 12.30)) == (20, 246.0)
    # A ticket smaller than the share price floors to zero: the KNOWN no-order case.
    assert quantity_of(100.0, state("A", 600, 125.0)) == (0, 0.0)
    # An unfilled minute has no bar at all, so there is no quantity to floor.
    qty, notional = quantity_of(1000.0, state("A", 600, None, entry_status="unfilled_expired"))
    assert qty is None and notional == 0.0
    # Exact division never rounds up.
    assert quantity_of(1000.0, state("A", 600, 125.0)) == (8, 1000.0)


def test_zero_floor_is_a_known_no_order_cash_skip():
    # (10, 100): a $150 share cannot be bought with a $100 ticket, so the state is a
    # KNOWN cash skip - no order, no attempt, no cooldown, no cash movement - and it is
    # never a fill and never an UNKNOWN.
    row, trades = one(
        [state("A", 600, 150.0), state("B", 600, 10.0, score=0.04)],
        cell_key="s10_t100",
    )
    assert row["no_order_skips"] == 1
    assert row["funded_intents"] == 1
    assert row["fills"] == 1
    assert row["known_fills"] == 1
    assert row["unknown_fills"] == 0
    assert row["cash_skips"] == 0 and row["slot_skips"] == 0
    assert row["peak_deployed_usd"] == 100.0  # only B's 10 shares x $10 moved
    assert row["skips_total"] == 1
    assert trades.height == 1  # the no-order state produced no trade row at all


def test_no_order_skip_consumes_no_attempt():
    # Three unorderable states for one ticker, then one orderable state: the attempt
    # cap must still allow the orderable one, because a no-order is not an attempt.
    rows = [state("A", 600 + 5 * i, 150.0) for i in range(MAX_ATTEMPTS)]
    rows.append(state("A", 600 + 5 * MAX_ATTEMPTS, 10.0, score=0.06))
    row, trades = one(rows, cell_key="s10_t100")
    assert row["no_order_skips"] == MAX_ATTEMPTS
    assert row["funded_intents"] == 1
    assert row["known_fills"] == 1
    assert trades.filter(pl.col("fill_status") == "known_open_proxy").height == 1


def test_same_clock_funding_never_substitutes():
    # Four same-clock intents on a 3-slot, $250-ticket book, the second one unfilled.
    # The fourth intent finds no cash and STAYS unfunded: a funded neighbour's unfilled
    # outcome is never used to backfill the clock, even though its ticket is released at
    # the next minute (t+1), before the next clock.
    rows = [
        state("AAA", 600, 10.0, score=0.90),
        state("BBB", 600, None, score=0.50, entry_status="unfilled_expired"),
        state("CCC", 600, 10.0, score=0.20),
        state("DDD", 600, 10.0, score=0.10),
    ]
    row, trades = one(rows, cell_key="s3_t250")
    assert row["funded_intents"] == 3
    assert row["cash_skips"] == 1
    assert row["slot_skips"] == 0
    assert row["unfilled_cash_intents"] == 1
    assert row["known_fills"] == 2
    assert row["fills"] == 2
    assert trades.height == 3  # the three funded intents; DDD produced no row at all
    assert trades.filter(pl.col("ticker") == "DDD").height == 0
    # The same-clock intents are funded in DESCENDING score order, so the position
    # counter seen at entry follows the score ordering, not the row ordering.
    funded = trades.filter(pl.col("fill_status") != "unfilled_expired_cash")
    assert funded["positions_at_entry"].to_list() == [1, 2]


def test_unknown_exit_holds_cash_and_slot_to_session_end():
    rows = [state("A", 600, 10.0, gross=None, exit_status="unknown_pending")]
    row, trades = one(rows, cell_key="s3_t1000")
    assert row["unknown_fills"] == 1
    assert row["known_fills"] == 0
    assert row["fills"] == 1
    # 100 shares x $10 stays deployed and is NOT restored at the (absent) exit bar.
    assert row["entry_notional_usd"] == 1000.0
    assert row["unknown_notional_usd"] == 1000.0
    assert row["peak_deployed_usd"] == 1000.0
    assert row["known_usd_100"] == 0.0
    # The separately labeled full-loss lower bound charges the whole notional.
    assert row["lower_bound_usd_100"] == -1000.0
    assert row["lower_bound_usd_25"] == -1000.0
    assert trades["net_usd_100"].to_list() == [None]
    # The unknown keeps the SLOT too: a later same-ticker intent is blocked by the
    # still-open position (ticker busy precedes the cooldown reason in precedence).
    row2, _ = one(
        [
            state("A", 600, 10.0, gross=None, exit_status="unknown_pending"),
            state("A", 700, 10.0, score=0.06),
        ],
        cell_key="s3_t1000",
    )
    assert row2["ticker_busy_skips"] == 1
    assert row2["cooldown_skips"] == 0
    assert row2["funded_intents"] == 1
    assert row2["fills"] == 1


def test_unfilled_minute_reserves_and_releases_the_ticket():
    # No bar at the entry minute: the ticket is reserved for one minute, released
    # fee-free, the attempt still counts, and no cooldown is anchored - so a LATER
    # intent of the same ticker in the SAME session is still fundable.
    rows = [
        state("A", 600, None, entry_status="unfilled_expired"),
        state("A", 605, 10.0, score=0.06),
    ]
    row, trades = one(rows, cell_key="s3_t1000")
    assert row["unfilled_cash_intents"] == 1
    assert row["funded_intents"] == 2
    assert row["known_fills"] == 1
    assert row["cooldown_skips"] == 0
    statuses = sorted(trades["fill_status"].to_list())
    assert statuses == ["known_open_proxy", "unfilled_expired_cash"]
    # The unfilled minute itself pays no fee at any rung; only the real fill does.
    unfilled = trades.filter(pl.col("fill_status") == "unfilled_expired_cash")
    assert all(unfilled[f"net_usd_{k}"][0] is None for k in COST_KEYS)
    assert unfilled["qty"].to_list() == [0] and unfilled["notional_usd"].to_list() == [0.0]


def test_cooldown_anchors_on_the_actual_exit_minute():
    exit_et = 660
    rows = [
        state("A", 600, 10.0, gross=0.01, exit_et=exit_et, score=0.09),
        state("A", 670, 10.0, score=0.08),  # 660 + 15 = 675 > 670 -> blocked
        state("A", exit_et + COOLDOWN_MIN, 10.0, score=0.07),  # exactly eligible
    ]
    row, _ = one(rows, cell_key="s3_t1000")
    assert row["cooldown_skips"] == 1
    assert row["funded_intents"] == 2
    assert row["known_fills"] == 2


def test_known_exit_restores_cash_and_charges_both_legs():
    price, qty, gross = 10.0, 100, 0.02
    rows = [
        state("A", 600, price, gross=gross, exit_et=660, score=0.09),
        state("B", 600, price, score=0.08),  # same clock, third slot is free
    ]
    row, trades = one(rows, cell_key="s3_t1000")
    assert row["known_fills"] == 2
    assert row["peak_deployed_usd"] == 2000.0
    assert row["positions_peak"] == 2
    expected = qty * price * net_at(gross, 100.0) + qty * price * net_at(0.01, 100.0)
    assert math.isclose(row["known_usd_100"], expected, rel_tol=1e-12)
    assert math.isclose(trades["net_usd_100"][0], qty * price * net_at(gross, 100.0), rel_tol=1e-12)
    # Cash is restored at the ACTUAL exit minute, so the deployed capital falls back.
    assert row["deployed_samples"] > row["funded_intents"]


def test_every_rung_of_a_cell_shares_one_fill_set():
    price, qty, gross = 20.0, 50, 0.03
    rows = [state("A", 600, price, gross=gross, exit_et=660)]
    row, trades = one(rows, cell_key="s3_t1000")
    notional = qty * price
    for bps in CELL_COSTS_BPS:
        key = str(int(bps))
        assert math.isclose(row[f"known_usd_{key}"], notional * net_at(gross, bps), rel_tol=1e-12)
        assert trades[f"net_usd_{key}"][0] is not None
    # Cost is a pure translation of one identical fill set: monotone, never a fill change.
    assert row["known_usd_25"] > row["known_usd_150"] > row["known_usd_200"]
    assert row["known_fills"] == 1 and row["fills"] == 1
    assert len(COST_KEYS) == 7


def test_every_book_cell_sees_the_same_signal_set():
    rows = [
        state("A", 600, 150.0, score=0.09),
        state("B", 600, 10.0, score=0.08),
        state("C", 605, 300.0, score=0.07),
    ]
    daily, _ = replay_day(DAY, frame(rows))
    assert daily.height == len(CELLS)
    assert set(daily["cell_key"].to_list()) == {c.key for c in CELLS}
    # The book shape must never change what is admitted, only what is funded.
    assert daily["signals"].n_unique() == 1
    counts = {
        r["cell_key"]: (r["signals"], r["funded_intents"], r["fills"], r["no_order_skips"])
        for r in daily.to_dicts()
    }
    # $1,000 tickets buy all three names (6 + 100 + 3 shares = $2,800 of $3,000).
    assert counts["s3_t1000"] == (3, 3, 3, 0)
    # $500 tickets buy all three (3 + 50 + 1 shares = $1,250 of $1,500).
    assert counts["s3_t500"] == (3, 3, 3, 0)
    # $250 tickets cannot buy the $300 name: a KNOWN no-order, never an UNKNOWN.
    assert counts["s3_t250"] == (3, 2, 2, 1)
    assert counts["s10_t250"] == (3, 2, 2, 1)
    # $100 tickets cannot buy the $150 or the $300 name.
    assert counts["s10_t100"] == (3, 1, 1, 2)
    assert counts["s20_t100"] == (3, 1, 1, 2)


def test_slot_and_cash_skips_are_separate_and_precedence_is_fixed():
    # Four orderable same-clock intents on a 3-slot, $250-ticket book: the third fills
    # the last slot and the fourth finds no slot (cash is still idle), so precedence is
    # slot before capital.
    rows = [state(t, 600, 10.0, score=0.5 - 0.01 * i) for i, t in enumerate("ABCD")]
    row, _ = one(rows, cell_key="s3_t250")
    assert row["slot_skips"] == 1
    assert row["cash_skips"] == 0
    assert row["no_order_skips"] == 0
    assert row["funded_intents"] == 3
    # The attempt cap applies per ticker, not per slot: three exits at t+15 (cooldown
    # ends at t+30) fund three attempts, and the fourth and fifth are capped.
    capped = [
        state("A", 600 + 30 * i, 10.0, score=0.05, exit_et=600 + 30 * i + 15) for i in range(5)
    ]
    row2, _ = one(capped, cell_key="s3_t1000")
    assert row2["max_attempt_skips"] == 2
    assert row2["cooldown_skips"] == 0
    assert row2["funded_intents"] == MAX_ATTEMPTS


def test_grid_and_reference_cell_are_the_predeclared_ones():
    assert [(c.slots, c.ticket) for c in CELLS] == [
        (3, 1000.0),
        (3, 500.0),
        (3, 250.0),
        (6, 500.0),
        (6, 250.0),
        (10, 250.0),
        (10, 100.0),
        (20, 100.0),
    ]
    ref = CELLS_BY_KEY["s3_t1000"]
    assert ref.reserve == 3000.0 and ref.control is True
    assert empty_daily(DAY, ref)["reserve_usd"] == 3000.0
    assert Cell(20, 100.0).reserve == 2000.0
