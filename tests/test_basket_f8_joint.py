import pytest

from factory.scripts.basket_f8_joint import summarize_day, validate_surface


def test_joint_day_counts_concurrent_tickets_and_equal_sleeve_return():
    tickets = [
        {"net": 0.10, "net_return": 0.20, "mfe_raw": 0.30},
        {"net": -0.05, "net_return": -0.10, "mfe_raw": 0.08},
    ]

    result = summarize_day(tickets, n_slots=2)

    assert result["n_filled"] == 2
    assert result["pnl_c0"] == 0.05
    assert result["n_profitable"] == 1
    assert result["n_reach_5"] == 2
    assert result["n_reach_10"] == 1
    assert result["n_reach_30"] == 1
    assert result["all_profitable"] is False


def test_open_end_mark_is_eod_profit_not_a_realized_exit():
    tickets = [
        {"net": 0.10, "net_return": 0.20, "mfe_raw": 0.30, "open_end": False},
        {"net": -0.05, "net_return": -0.10, "mfe_raw": 0.08, "open_end": True},
    ]

    result = summarize_day(tickets, n_slots=2)

    assert result["n_closed"] == 1
    assert result["n_open_end"] == 1
    assert result["n_closed_profitable"] == 1
    assert result["n_eod_positive"] == 1


def test_cash_only_sleeve_is_retained_and_not_called_profitable():
    result = summarize_day([], n_slots=3)

    assert result["n_filled"] == 0
    assert result["pnl_c0"] == 0.0
    assert result["all_profitable"] is None
    assert result["n_reach_5"] == 0


def test_surface_rejects_missing_or_duplicate_f1_cells():
    surface = {"family_id": "F1", "cells": [{"run_id": "only", "status": "validated_frozen"}]}

    with pytest.raises(ValueError, match="120 unique"):
        validate_surface(surface)
