"""Consumer-visible cash, precedence, and censoring invariants for alpha replay."""

import sys
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from alpha_open_sim import replay
from alpha_short_diagnostic import exit_short


def signal(ticker, t, gross, exit_et, score=0.0, entry_status="filled_proxy"):
    return {
        "day": "2021-02-01",
        "ticker": ticker,
        "t": t,
        "entry_status": entry_status,
        "entry_et": t,
        "entry_open": 10.0,
        "gross_15": gross,
        "exit_et_15": exit_et,
        "session_end": 959,
        "score": score,
    }


def test_simultaneous_picks_do_not_use_future_payoff_and_losses_cannot_be_levered():
    f = pl.DataFrame(
        [
            signal("LOSER", 585, -0.20, 600, 2),
            signal("WINNER", 585, 0.50, 600, 1),
            signal("LATER", 605, 0.50, 620, 3),
        ]
    )
    m, trades = replay(f, ["2021-02-01"], 15, 0, max_positions=1)
    assert [r["ticker"] for r in trades] == ["LOSER"]
    assert m["mean_daily_lower_bound"] == pytest.approx(-0.20)
    assert m["cash_or_slot_skips"] == 2


def test_cash_days_stay_in_denominator_and_friction_is_paid_on_both_legs():
    f = pl.DataFrame([signal("FLAT", 585, 0.0, 600)])
    m, _ = replay(f, ["2021-02-01", "2021-02-02"], 15, 100)
    expected = 0.995 / 1.005 - 1
    assert m["mean_net_known_fill"] == pytest.approx(expected)
    assert m["mean_daily_lower_bound"] == pytest.approx(expected / 6)


def test_expired_order_is_cash_but_missing_held_exit_is_not_cash():
    f = pl.DataFrame(
        [
            signal("NOFILL", 585, 0.0, None, 2, "unfilled_expired"),
            signal("CENSORED", 590, None, None, 1),
        ]
    )
    m, trades = replay(f, ["2021-02-01"], 15, 100, max_positions=1)
    assert m["attempts"] == 2 and m["fills"] == 1
    assert trades[0]["status"] == "unknown_pending"
    assert m["unknown_fills"] == 1
    assert m["mean_daily_lower_bound"] == -1


def test_short_stop_prices_actual_gap_open_not_the_trigger_level():
    bars = pl.DataFrame(
        {
            "et": [585, 586, 587, 959],
            "open": [10.0, 10.5, 13.0, 12.0],
            "close": [10.2, 11.2, 12.5, 12.0],
        }
    )
    et, price, why = exit_short(bars, 585, 10.0, 959)
    assert (et, price, why) == (587, 13.0, "completed_close_stop")
