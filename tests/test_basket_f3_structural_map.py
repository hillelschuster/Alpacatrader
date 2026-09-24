from __future__ import annotations

import numpy as np
import pytest

from factory.scripts import basket_f3_structural_map as f3map


def test_structural_row_uses_completed_state_and_strictly_later_outcomes():
    bars = {
        "et": np.array([570, 571, 572, 573]),
        "open": np.array([10.0, 9.0, 9.5, 10.0]),
        "high": np.array([10.0, 12.0, 10.0, 11.0]),
        "low": np.array([10.0, 8.0, 9.0, 9.8]),
        "close": np.array([10.0, 9.0, 9.8, 10.5]),
    }
    row = f3map.structural_row("2021-02-01", "X", 570, 10.0, bars, 0, 2)
    assert row["entry_drawdown_pct"] == pytest.approx(-0.02)
    assert row["peak_drawdown_pct"] == pytest.approx(-0.18333333333333335)
    assert row["mfe_surrendered_pct"] == pytest.approx(1.1)
    assert row["time_since_high_bars"] == 1
    assert row["future_max_return"] == pytest.approx(0.12244897959183665)
    assert row["future_close_return"] == pytest.approx(0.0714285714285714)
    assert row["future_bars_n"] == 1


def test_interaction_rows_retain_all_observed_structural_combinations():
    rows = [
        {"block": "block1", "depth_bin": "d0", "duration_bin": "short",
         "recovery_bin": "reclaimed", "peak_drawdown_bin": "shallow",
         "mfe_surrendered_bin": "low", "future_close_return": 0.1,
         "future_max_return": 0.2, "future_min_return": -0.1},
        {"block": "block1", "depth_bin": "d1", "duration_bin": "long",
         "recovery_bin": "not_reclaimed", "peak_drawdown_bin": "deep",
         "mfe_surrendered_bin": "high", "future_close_return": -0.1,
         "future_max_return": 0.1, "future_min_return": -0.2},
    ]
    table = f3map.interaction_table(rows, ("depth_bin", "duration_bin", "recovery_bin"))
    assert len(table) == len(f3map.BINS["depth_bin"]) * len(f3map.BINS["duration_bin"]) * len(f3map.BINS["recovery_bin"])
    assert {tuple(r[k] for k in ("depth_bin", "duration_bin", "recovery_bin"))
            for r in table if r["n"]} == {("d0", "short", "reclaimed"), ("d1", "long", "not_reclaimed")}
    assert all(r["n"] == 0 for r in table if r["depth_bin"] == "d2")


@pytest.mark.parametrize("day", ["2020-12-31", "2024-01-02", "2025-01-02", "2026-06-01"])
def test_structural_map_rejects_non_development_dates(day):
    with pytest.raises((ValueError, PermissionError)):
        f3map.validate_day(day)
