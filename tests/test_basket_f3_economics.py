from __future__ import annotations

import pytest

from factory.scripts import basket_f3_economics as economics


def test_daily_alignment_rejects_missing_or_duplicate_dates():
    expected = ["2021-02-01", "2021-02-02"]
    economics.validate_daily_dates(expected, expected)
    with pytest.raises(ValueError, match="date grid"):
        economics.validate_daily_dates(expected, ["2021-02-01"])
    with pytest.raises(ValueError, match="unique"):
        economics.validate_daily_dates(expected, ["2021-02-01", "2021-02-01"])


def test_ticket_join_rejects_dates_without_a_daily_sleeve_row():
    tickets = [{"sleeve_day": "2021-02-02", "ticker": "X"}]
    economics.validate_ticket_dates(["2021-02-01", "2021-02-02"], tickets)
    with pytest.raises(ValueError, match="ticket date outside daily date grid"):
        economics.validate_ticket_dates(["2021-02-01"], tickets)


def test_ticket_metrics_separate_marked_realized_and_raw_path_tail():
    tickets = [
        {"sleeve_day": "2021-02-01", "net": -0.20, "net_return": -0.20,
         "mfe_raw": 0.10, "open_end": False, "exit_reason": "R2(-10,3)", "exit_et": 600},
        {"sleeve_day": "2021-02-01", "net": 0.15, "net_return": 0.30,
         "mfe_raw": 0.60, "open_end": False, "exit_reason": "FORCED_FLAT", "exit_et": 959},
        {"sleeve_day": "2021-02-02", "net": 0.05, "net_return": 0.10,
         "mfe_raw": 1.20, "open_end": True, "exit_reason": None, "exit_et": None},
    ]
    result = economics.ticket_economics(tickets)
    assert result["realized_net"] == pytest.approx(-0.05)
    assert result["marked_net"] == pytest.approx(0.05)
    assert result["failed_n"] == 1
    assert result["failed_net"] == pytest.approx(-0.20)
    assert result["raw_mfe_contribution"]["50"]["n"] == 2
    assert result["raw_mfe_contribution"]["50"]["ticket_net"] == pytest.approx(0.20)
    assert result["raw_mfe_contribution"]["100"]["n"] == 1
    assert result["release_exit_count"] == 1
    assert result["release_execution_et"]["median"] == 600
    assert result["forced_flat_exit_count"] == 1
    assert result["raw_mfe_is_executable_fill"] is False


def test_c0_daily_mean_is_not_ticket_net_sum():
    daily = [{"date": "2021-02-01", "r_day": -0.10},
             {"date": "2021-02-02", "r_day": 0.20}]
    assert economics.daily_summary(daily)["mean_c0_return"] == pytest.approx(0.05)
    assert economics.daily_summary(daily)["c0_return_sum"] == pytest.approx(0.10)
