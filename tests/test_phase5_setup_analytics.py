"""Tests for per-setup analytics report (SPEC §11.17.13 item #5)."""

from src.analytics import format_setup_report, summarize_by_setup
from src.models.schemas import DecisionRecord


def test_summarize_by_setup_pairs_exit_records_to_last_entry_setup():
    records = [
        DecisionRecord(
            symbol="RUNR",
            decision="enter",
            entry_setup="first_pullback",
            entry_risk_amount=250.0,
            attention_score=82.0,
        ),
        DecisionRecord(
            symbol="RUNR",
            decision="exit",
            exit_reason="target_1r",
            exit_pnl=125.0,
            exit_pnl_r=0.5,
            exit_remaining_shares=50,
        ),
        DecisionRecord(
            symbol="RUNR",
            decision="trail_exit",
            exit_reason="atr_trail_hit",
            exit_pnl=375.0,
            exit_pnl_r=1.5,
            exit_remaining_shares=0,
        ),
        DecisionRecord(
            symbol="FAKE",
            decision="enter",
            entry_setup="hod_reclaim",
            entry_risk_amount=200.0,
            attention_score=71.0,
        ),
        DecisionRecord(
            symbol="FAKE",
            decision="exit",
            exit_reason="hard_stop",
            exit_pnl=-200.0,
            exit_pnl_r=-1.0,
            exit_remaining_shares=0,
        ),
    ]

    stats = {row.setup: row for row in summarize_by_setup(records)}

    first_pullback = stats["first_pullback"]
    assert first_pullback.entries == 1
    assert first_pullback.exits == 2
    assert first_pullback.wins == 2
    assert first_pullback.losses == 0
    assert first_pullback.total_pnl == 500.0
    assert first_pullback.total_r == 2.0
    assert first_pullback.avg_r == 1.0
    assert first_pullback.win_rate_pct == 100.0
    assert first_pullback.avg_attention == 82.0

    hod_reclaim = stats["hod_reclaim"]
    assert hod_reclaim.entries == 1
    assert hod_reclaim.exits == 1
    assert hod_reclaim.wins == 0
    assert hod_reclaim.losses == 1
    assert hod_reclaim.total_pnl == -200.0
    assert hod_reclaim.total_r == -1.0
    assert hod_reclaim.win_rate_pct == 0.0


def test_format_setup_report_renders_readable_per_setup_table():
    records = [
        DecisionRecord(
            symbol="RUNR",
            decision="enter",
            entry_setup="first_pullback",
            entry_risk_amount=250.0,
            attention_score=82.0,
        ),
        DecisionRecord(
            symbol="RUNR",
            decision="trail_exit",
            exit_pnl=375.0,
            exit_pnl_r=1.5,
            exit_remaining_shares=0,
        ),
    ]

    report = format_setup_report(summarize_by_setup(records))

    assert "Setup" in report
    assert "first_pullback" in report
    assert "Entries" in report
    assert "Win%" in report
    assert "Total R" in report
    assert "Best R" in report
    assert "Worst R" in report
    assert "PF" in report
    assert "1.50" in report


def test_summarize_by_setup_ignores_unscored_exits_in_averages():
    records = [
        DecisionRecord(
            symbol="RUNR",
            decision="enter",
            entry_setup="first_pullback",
        ),
        DecisionRecord(
            symbol="RUNR",
            decision="exit",
            exit_remaining_shares=10,
        ),
        DecisionRecord(
            symbol="RUNR",
            decision="exit",
            exit_pnl=100.0,
            exit_pnl_r=2.0,
            exit_remaining_shares=0,
        ),
    ]

    stats = summarize_by_setup(records)[0]

    assert stats.exits == 2
    assert stats.total_pnl == 100.0
    assert stats.total_r == 2.0
    assert stats.avg_pnl == 100.0
    assert stats.avg_r == 2.0


def test_summarize_by_setup_clears_unknown_remaining_shares_to_avoid_stale_mapping():
    records = [
        DecisionRecord(
            symbol="RUNR",
            decision="enter",
            entry_setup="first_pullback",
        ),
        DecisionRecord(
            symbol="RUNR",
            decision="exit",
            exit_remaining_shares=None,
        ),
        DecisionRecord(
            symbol="RUNR",
            decision="exit",
            exit_pnl=-100.0,
            exit_pnl_r=-1.0,
            exit_remaining_shares=0,
        ),
    ]

    stats = summarize_by_setup(records)[0]

    assert stats.exits == 1
    assert stats.total_r == 0.0
