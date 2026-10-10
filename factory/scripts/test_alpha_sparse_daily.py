"""Consumer-visible cadence, cash, and gap-causality invariants for alpha_sparse_daily."""

import sys
from pathlib import Path

import numpy as np
import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from alpha_sparse_daily import (
    MAX_ATTEMPTS,
    ORDER_BUDGET,
    PROVIDER_SELECT_COST,
    RESERVE_USD,
    SELECT_COST,
    THRESHOLD,
    VIEWS,
    VIEWS_BY_NAME,
    View,
    attach_recomputed_labels,
    baseline_of,
    horizon_label,
    ranked_views,
    replay_view,
    view_signals,
)

DAY = ["2023-06-01"]
D1, D2, D3 = "2023-06-01", "2023-06-02", "2023-06-03"


def sig(
    ticker,
    t,
    gross,
    exit_et,
    score=1.0,
    entry_status="filled_proxy",
    horizon=15,
    ret3=0.01,
    dd_high15=-0.02,
):
    """One scored state: the panel's filled/unfilled status plus one horizon label."""
    row = {
        "day": DAY[0],
        "ticker": ticker,
        "t": t,
        "entry_et": t,
        "entry_open": 10.0,
        "entry_status": entry_status,
        "session_end": 959,
        "ret3": ret3,
        "dd_high15": dd_high15,
        "score": score,
    }
    for h in (15, 30, 60):
        row[f"gross_{h}"] = gross if h == horizon else gross
        row[f"exit_et_{h}"] = exit_et if h == horizon else exit_et
        row[f"exit_status_{h}"] = (
            "observed_open_proxy" if exit_et is not None else "unknown_pending"
        )
    return row


def sig_on(day, ticker, t, gross, exit_et, **kw):
    """The same scored state, re-dated onto another session of a multi-day block."""
    row = sig(ticker, t, gross, exit_et, **kw)
    row["day"] = day
    return row


def frame(*rows):
    return pl.DataFrame(list(rows))


def test_cooldown_is_measured_from_the_actual_exit_not_the_entry():
    repeat = VIEWS_BY_NAME["repeat_h15"]
    exit_et = 615  # entry 600 + 15, so a flat 15 frees the slot at 630
    early = frame(sig("A", 600, 0.10, exit_et), sig("A", 625, 0.10, 640))
    metrics, trades = replay_view(early, DAY, repeat, 0)
    assert [t["t"] for t in trades] == [600]
    assert metrics["skip_reasons"]["cooldown"] == 1
    on_time = frame(sig("A", 600, 0.10, exit_et), sig("A", 630, 0.10, 645))
    metrics, trades = replay_view(on_time, DAY, repeat, 0)
    assert [t["t"] for t in trades] == [600, 630]
    assert metrics["skip_reasons"] == {}
    # A flat cooldown ignores the exit clock, so the same spacing no longer blocks: the
    # slot is genuinely free at 615 (exit + 0) and 625 is a fresh, funded re-entry.
    slack = View("slack", horizon=15, cooldown_min=0, max_attempts=MAX_ATTEMPTS)
    metrics, trades = replay_view(early, DAY, slack, 0)
    assert [t["t"] for t in trades] == [600, 625]
    assert metrics["skip_reasons"] == {}
    assert metrics["attempts"] == 2
    # What still blocks a same-ticker signal at zero cooldown is a slot that is truly
    # held: a signal landing BEFORE the actual exit minute.
    overlap = frame(sig("A", 600, 0.10, exit_et), sig("A", 605, 0.10, 620))
    metrics, trades = replay_view(overlap, DAY, slack, 0)
    assert [t["t"] for t in trades] == [600]
    assert metrics["skip_reasons"] == {"slot_busy": 1}


def test_one_attempt_per_ticker_day_makes_the_control_the_original_rare_study():
    control = VIEWS_BY_NAME["once_h60"]
    signals = frame(
        sig("A", 600, 0.10, 660, horizon=60),
        sig("A", 630, 0.10, 690, horizon=60),
        sig("B", 660, 0.10, 720, horizon=60),
    )
    metrics, trades = replay_view(signals, DAY, control, 0)
    assert [(t["ticker"], t["t"]) for t in trades] == [("A", 600), ("B", 660)]
    assert metrics["attempts"] == 2
    assert metrics["skip_reasons"] == {"slot_busy": 1}
    # The A duplicate was cadence-blocked while the slot was held, never starved of
    # cash: both executed tickets were funded $1,000 each and booked +$100 apiece, and
    # the two never overlapped, so the reserve was never levered past one ticket.
    assert metrics["fills"] == 2
    assert metrics["peak_deployed_usd"] == pytest.approx(1000.0)
    assert metrics["carry_equity_end"] == pytest.approx(3000.0 + 2 * 100.0)
    # The control never reopens a position it already closed this session either.
    reopened = frame(sig("A", 600, 0.10, 660, horizon=60), sig("A", 700, 0.10, 760, horizon=60))
    metrics, trades = replay_view(reopened, DAY, control, 0)
    assert metrics["attempts"] == 1 and metrics["fills"] == 1
    assert metrics["skip_reasons"] == {"max_attempts": 1}


def test_cash_is_reserved_per_slot_and_a_third_ticket_cannot_be_levered():
    signals = frame(
        sig("A", 600, 0.05, 660, horizon=60),
        sig("B", 600, 0.05, 660, horizon=60),
        sig("C", 600, 0.05, 660, horizon=60),
        sig("D", 600, 0.05, 660, horizon=60),
    )
    metrics, trades = replay_view(signals, DAY, VIEWS_BY_NAME["repeat_h60"], 0)
    assert metrics["fills"] == 3 and metrics["skip_reasons"]["cash_or_slot"] == 1
    assert metrics["peak_deployed_usd"] == 3000.0
    assert metrics["carry_equity_end"] == pytest.approx(3000.0 + 3 * 50.0)
    assert metrics["turnover_usd"] == pytest.approx(3000.0 + 3 * 1050.0)


def test_gappy_exit_keeps_the_slot_blocks_reentry_and_is_charged_a_full_unit():
    # The h30 exit minute never prints, so the position is pending all session.
    pending = frame(
        sig("A", 600, None, None, horizon=30),
        sig("A", 700, 0.40, 730, horizon=30),
        sig("B", 700, 0.40, 730, horizon=30),
    )
    view = VIEWS_BY_NAME["repeat_h30"]
    metrics, trades = replay_view(pending, DAY, view, 0)
    assert metrics["unknown_fills"] == 1
    assert metrics["skip_reasons"]["slot_busy"] == 1
    # The unknown books one full unit of loss, yet the other slots stay live: B traded a
    # free slot for +40%, so the day is (-1000 + 400) / 3000 = -0.2 -- an unknown exit
    # locks its own ticker, never the whole book.
    assert metrics["mean_daily_lower_bound"] == pytest.approx(-0.2)
    assert metrics["carry_equity_end"] == pytest.approx(2400.0)
    assert [t["status"] for t in trades] == ["unknown_pending", "known_open_proxy"]
    assert metrics["fills"] == 2
    assert all(t["net"] is not None for t in trades[1:])
    # Boundary: with EVERY slot held by an unknown exit the book really is locked -- a
    # fourth signal finds no ticket and the full-unit charges exhaust the reserve.
    locked = frame(
        sig("A", 600, None, None, horizon=30),
        sig("B", 600, None, None, horizon=30),
        sig("C", 600, None, None, horizon=30),
        sig("D", 600, 0.40, 730, horizon=30),
    )
    metrics, trades = replay_view(locked, DAY, view, 0)
    assert [t["ticker"] for t in trades] == ["A", "B", "C"]
    assert [t["status"] for t in trades] == ["unknown_pending"] * 3
    assert metrics["unknown_fills"] == 3
    assert metrics["skip_reasons"] == {"cash_or_slot": 1}
    assert metrics["mean_daily_lower_bound"] == pytest.approx(-1.0)
    assert metrics["carry_equity_end"] == pytest.approx(0.0)


def test_same_clock_exit_precedes_the_buy_that_needs_its_cash():
    signals = frame(sig("A", 600, 0.10, 630, horizon=15), sig("B", 630, 0.10, 660, horizon=15))
    metrics, trades = replay_view(signals, DAY, VIEWS_BY_NAME["repeat_h15"], 0)
    assert [t["ticker"] for t in trades] == ["A", "B"]
    assert metrics["skip_reasons"] == {}


def test_unfilled_minute_reserves_and_releases_cash_and_still_counts_the_attempt():
    signals = frame(
        sig("A", 600, None, None, entry_status="unfilled_expired"), sig("A", 605, 0.05, 620)
    )
    view = VIEWS_BY_NAME["repeat_h15"]
    metrics, trades = replay_view(signals, DAY, view, 100)
    assert metrics["attempts"] == 2 and metrics["fills"] == 1
    assert metrics["cash_or_slot_skips"] == 0
    assert trades[0]["status"] == "known_open_proxy"
    assert metrics["mean_net_known_fill"] == pytest.approx(1.05 * 0.995 / 1.005 - 1)
    # With one attempt the same day, the follow-up minute is now blocked, not traded.
    control = View("one", horizon=15, cooldown_min=15, max_attempts=1)
    metrics, trades = replay_view(signals, DAY, control, 100)
    assert metrics["attempts"] == 1 and metrics["fills"] == 0
    assert metrics["mean_daily_lower_bound"] == 0.0


def test_every_calendar_day_stays_in_the_denominator():
    metrics, _ = replay_view(pl.DataFrame(), ["2023-06-01", "2023-06-02"], VIEWS[0], 100)
    assert metrics["days"] == 2 and metrics["traded_days"] == 0
    assert metrics["mean_daily_lower_bound"] == 0.0
    assert metrics["carry_equity_end"] == 3000.0
    assert metrics["carry_equity_is_compounding"] is False


def test_strong_view_tightens_the_bar_without_adding_a_signal():
    strong = VIEWS_BY_NAME["repeat_h30_strong"]
    rows = frame(
        sig("OK", 600, 0.10, 630, horizon=30, ret3=0.02, dd_high15=-0.005),
        sig("FLAT", 605, 0.10, 635, horizon=30, ret3=0.02, dd_high15=-0.05),
        sig("DIP", 610, 0.10, 640, horizon=30, ret3=-0.01, dd_high15=-0.005),
    )
    assert view_signals(rows, strong).height == 1
    assert view_signals(rows, VIEWS_BY_NAME["repeat_h30"]).height == 3
    assert view_signals(rows, strong)["ticker"].to_list() == ["OK"]


def test_recomputed_label_uses_the_strict_actual_minute_open_across_gaps():
    et = np.array([570, 571, 572, 580, 600], dtype=np.int64)
    opens = np.array([10.0, 11.0, 12.0, 13.0, 14.0])
    # 570 + 30 = 600 prints exactly: the label is 600's own open (14) -- not the target
    # level and not the pre-gap minute's close.
    gross, exit_et, status = horizon_label(et, opens, 570, 10.0, True, 30, 959)
    assert (gross, exit_et, status) == (14.0 / 10.0 - 1.0, 600, "observed_open_proxy")
    # A real gap: the 600 target never prints, so the first ACTUAL minute at/after it
    # (605, open 14) prices the exit -- never the 580 close, never an interpolated 600.
    gap_et = np.array([570, 571, 572, 580, 605], dtype=np.int64)
    gap_opens = np.array([10.0, 11.0, 12.0, 13.0, 14.0])
    gross, exit_et, status = horizon_label(gap_et, gap_opens, 570, 10.0, True, 30, 959)
    assert (gross, exit_et, status) == (14.0 / 10.0 - 1.0, 605, "observed_open_proxy")
    # A capped target that never prints is UNKNOWN, never the last open.
    assert horizon_label(et, opens, 570, 10.0, True, 400, 575) == (None, None, "unknown_pending")
    # A state that never filled is cash in that minute, not a missing exit.
    assert horizon_label(et, opens, 570, None, False, 30, 959) == (0.0, None, "unfilled_cash")
    # Cap at the session's last minute keeps the label inside the session.
    assert horizon_label(et, opens, 560, 10.0, True, 30, 580)[1] == 580


def test_missing_tape_stream_becomes_unknown_never_cash(tmp_path, monkeypatch):
    day = "2023-06-01"
    rows = frame(sig("GHOST", 600, 0.10, 630, horizon=30))
    monkeypatch.setattr("alpha_sparse_daily.BARS_ROOT", tmp_path)
    out, info = attach_recomputed_labels(day, rows)
    assert info["day_file_present"] is False
    assert info["missing_symbol_streams"] == ["GHOST"]
    assert out["gross_30"].to_list() == [None]
    assert out["exit_status_30"].to_list() == ["unknown_pending"]
    assert out.height == 1


def test_first_attempt_baseline_only_disables_reentry():
    for name in ("repeat_h60", "repeat_h30", "repeat_h15", "repeat_h30_strong"):
        view = VIEWS_BY_NAME[name]
        base = baseline_of(view)
        assert base.max_attempts == 1
        assert base.horizon == view.horizon and base.quality == view.quality
        assert baseline_of(base) is base
    assert baseline_of(VIEWS_BY_NAME["once_h60"]) is VIEWS_BY_NAME["once_h60"]
    assert [v.name for v in VIEWS if v.max_attempts > 1] == [
        "repeat_h60",
        "repeat_h30",
        "repeat_h15",
        "repeat_h30_strong",
    ]
    assert all(v.cadence.startswith(f"thr{THRESHOLD:.2f}") for v in VIEWS)
    assert {v.name for v in VIEWS} == {
        "once_h60",
        "repeat_h60",
        "repeat_h30",
        "repeat_h15",
        "repeat_h30_strong",
    }


def test_block_turnover_and_hold_stats_accumulate_every_day_not_just_the_last():
    # A three-day block whose FIRST day carries by far the most turnover (four funded
    # tickets, three of them concurrent) and whose LAST day carries only two. A block
    # total that reports the last day alone is wrong by ~3.5x here, so this block is the
    # regression: the accumulator must span every day of the block.
    view = VIEWS_BY_NAME["repeat_h15"]
    cost_bps = 100.0
    side = cost_bps / 20_000.0

    def net_of(gross):
        """Both-legs round-trip fee on a research ticket, the documented convention."""
        return (1.0 + gross) * (1.0 - side) / (1.0 + side) - 1.0

    rows = frame(
        # day 1: three concurrent tickets fill the reserve, a fourth finds no cash, then
        # A re-enters after its ACTUAL exit (640) + the 15-minute cooldown (free at 655).
        sig_on(D1, "A", 600, 0.10, 640),
        sig_on(D1, "B", 600, 0.10, 640),
        sig_on(D1, "C", 600, 0.10, 640),
        sig_on(D1, "D", 600, 0.10, 640),
        sig_on(D1, "A", 660, 0.05, 675),
        # day 2: one ticket.
        sig_on(D2, "B", 600, 0.20, 615),
        # day 3 (the LAST day): two tickets -- deliberately the smallest of the three.
        sig_on(D3, "A", 600, -0.05, 615),
        sig_on(D3, "C", 600, 0.30, 615),
    )
    days = [D1, D2, D3]
    metrics, trades = replay_view(rows, days, view, cost_bps)

    # Consumer-visible cadence: concurrency cap, repeat attempt index, all days traded.
    assert [(t["day"], t["ticker"], t["t"], t["attempt_index"]) for t in trades] == [
        (D1, "A", 600, 1),
        (D1, "B", 600, 1),
        (D1, "C", 600, 1),
        (D1, "A", 660, 2),
        (D2, "B", 600, 1),
        (D3, "A", 600, 1),
        (D3, "C", 600, 1),
    ]
    assert metrics["fills"] == 7 and metrics["attempts"] == 7
    assert metrics["traded_days"] == 3
    assert metrics["skip_reasons"] == {"cash_or_slot": 1}

    # Fees are charged per ticket on BOTH legs, concurrently and on the repeat attempt.
    for (day, ticker, t), gross in {
        (D1, "A", 600): 0.10,
        (D1, "B", 600): 0.10,
        (D1, "C", 600): 0.10,
        (D1, "A", 660): 0.05,
        (D2, "B", 600): 0.20,
        (D3, "A", 600): -0.05,
        (D3, "C", 600): 0.30,
    }.items():
        tr = next(x for x in trades if (x["day"], x["ticker"], x["t"]) == (day, ticker, t))
        assert tr["net"] == pytest.approx(net_of(gross))
        assert tr["cost_bps"] == cost_bps and tr["order_budget"] == ORDER_BUDGET

    # Block turnover recomputed from the trade records, day by day.
    def trade_turnover(t):
        proceeds = t["order_budget"] * (1.0 + t["net"]) if t["net"] is not None else 0.0
        return t["order_budget"] + proceeds

    day_turnover = {d: sum(trade_turnover(t) for t in trades if t["day"] == d) for d in days}
    expected_d1 = (
        4 * ORDER_BUDGET + 3 * ORDER_BUDGET * (1 + net_of(0.10)) + ORDER_BUDGET * (1 + net_of(0.05))
    )
    assert day_turnover[D1] == pytest.approx(expected_d1)
    assert day_turnover[D1] != pytest.approx(day_turnover[D3])  # early day != last day
    assert metrics["turnover_usd"] == pytest.approx(sum(day_turnover.values()))
    assert metrics["turnover_usd_per_day"] == pytest.approx(sum(day_turnover.values()) / 3)

    # Hold statistics are block-wide too: 40/40/40/15 + 15 + 15/15, never the last day.
    assert metrics["mean_hold_min"] == pytest.approx((3 * 40 + 4 * 15) / 7)
    assert metrics["median_hold_min"] == pytest.approx(15.0)
    assert metrics["min_hold_min"] == 15 and metrics["max_hold_min"] == 40

    # PnL / selection math is untouched by the accumulator: per-day lower bounds are the
    # same fixed-reserve normalization they always were, and the additive carry check
    # sums the same dollars.
    expected_pnl = {
        D1: ORDER_BUDGET * (3 * net_of(0.10) + net_of(0.05)),
        D2: ORDER_BUDGET * net_of(0.20),
        D3: ORDER_BUDGET * (net_of(-0.05) + net_of(0.30)),
    }
    assert [d["day"] for d in metrics["daily"]] == days
    for d in metrics["daily"]:
        assert d["lower_bound_pnl"] == pytest.approx(expected_pnl[d["day"]])
        assert d["lower_bound_return"] == pytest.approx(expected_pnl[d["day"]] / RESERVE_USD)
        assert d["unknown"] == 0
    assert metrics["carry_equity_end"] == pytest.approx(RESERVE_USD + sum(expected_pnl.values()))
    assert metrics["carry_equity_is_compounding"] is False
    assert metrics["mean_daily_lower_bound"] == pytest.approx(
        sum(expected_pnl.values()) / RESERVE_USD / len(days)
    )
    assert metrics["mean_net_known_fill"] == pytest.approx(
        sum(net_of(g) for g in (0.10, 0.10, 0.10, 0.05, 0.20, -0.05, 0.30)) / 7
    )


def test_both_predeclared_selections_rank_on_dollars_per_day_with_a_deterministic_tie_break():
    """One ranking rule, two pre-declared rungs; ties break on known fills then name."""

    def cell(dollars, known):
        return {"dollars_per_day": dollars, "known_fills": known}

    val = {
        "once_h60": {"25": cell(1.0, 10), "100": cell(0.5, 10)},
        "repeat_h60": {"25": cell(3.0, 5), "100": cell(2.0, 5)},
        "repeat_h30": {"25": cell(3.0, 9), "100": cell(2.0, 3)},
        "repeat_h15": {"25": cell(0.0, 2), "100": cell(-1.0, 4)},
        "repeat_h30_strong": {"25": cell(0.0, 2), "100": cell(0.0, 2)},
    }
    at_25 = [v.name for v in ranked_views(val, PROVIDER_SELECT_COST)]
    at_100 = [v.name for v in ranked_views(val, SELECT_COST)]
    # Same dollars/day on both rungs for the two repeat leaders, so the known-fill
    # tie-break decides, and the two rungs legitimately pick different views.
    assert at_25[0] == "repeat_h30" and at_100[0] == "repeat_h60"
    assert at_25 == ["repeat_h30", "repeat_h60", "once_h60", "repeat_h15", "repeat_h30_strong"]
    assert at_100 == ["repeat_h60", "repeat_h30", "once_h60", "repeat_h30_strong", "repeat_h15"]
    assert set(at_25) == {v.name for v in VIEWS} and set(at_100) == {v.name for v in VIEWS}
