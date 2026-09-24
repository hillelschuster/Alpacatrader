from __future__ import annotations

import pytest

from factory.scripts import basket_f3_sim as f3
from factory.scripts import basket_sim as sim


def _path(highs, lows, closes):
    return {"et": list(range(570, 570 + len(closes))), "open": [10.0] * len(closes),
            "high": highs, "low": lows, "close": closes}


def test_r2_waits_window_then_releases_or_rearms():
    rule = sim.R2(-10, 3)
    tk = sim.Ticket("X", "2021-02-01", 570, 10, 1, peak=10)
    bars = _path([10] * 6, [9, 9.5, 9.5, 9.5, 9.5, 9.5],
                 [10, 9.5, 9.5, 9.0, 9.5, 8.9])
    assert rule.evaluate(tk, sim._bar(bars, 0), 0) is None
    assert rule.evaluate(tk, sim._bar(bars, 1), 1) is None
    assert rule.evaluate(tk, sim._bar(bars, 2), 2) is None
    assert rule.evaluate(tk, sim._bar(bars, 3), 3)["action"] == "EXIT"
    assert rule.evaluate(tk, sim._bar(bars, 4), 4) is None
    assert rule.evaluate(tk, sim._bar(bars, 5), 5) is None


def test_r2_rearms_after_window_recovery():
    rule = sim.R2(-10, 2)
    tk = sim.Ticket("X", "2021-02-01", 570, 10, 1, peak=10)
    bars = _path([10] * 6, [8.9, 9.5, 9.5, 8.9, 9.0, 9.0],
                 [9.0, 9.5, 9.2, 9.0, 9.0, 9.0])
    assert rule.evaluate(tk, sim._bar(bars, 0), 0) is None
    assert rule.evaluate(tk, sim._bar(bars, 1), 1) is None
    assert rule.evaluate(tk, sim._bar(bars, 2), 2) is None
    assert rule.evaluate(tk, sim._bar(bars, 3), 3) is None
    assert rule.evaluate(tk, sim._bar(bars, 4), 4) is None
    assert rule.evaluate(tk, sim._bar(bars, 5), 5)["action"] == "EXIT"


def test_r3_uses_prior_peak_not_current_bar_high():
    rule = sim.R3(40)
    tk = sim.Ticket("X", "2021-02-01", 570, 10, 1, peak=10)
    bar = {"et": 571, "open": 10, "high": 20, "low": 10, "close": 11}
    assert rule.evaluate(tk, bar, 1) is None
    tk.peak = 20
    assert rule.evaluate(tk, {**bar, "high": 12, "close": 12}, 2)["action"] == "EXIT"


@pytest.mark.parametrize("day", ["2020-12-31", "2024-06-01", "2025-01-02", "2026-06-01"])
def test_f3_refuses_non_development_days(day):
    with pytest.raises((ValueError, PermissionError)):
        f3.validate_day(day)


def test_exact_f3_surface_includes_registered_controls_and_both_frictions():
    cells = f3.build_cells()
    assert len(cells) == 20
    assert {c.release_id for c in cells} == {
        "R0", *(f"R2_L{loss}_w{window}" for loss in (10, 15) for window in (3, 5, 10)),
        *(f"R3_g{giveback}" for giveback in (40, 50, 60)),
    }
    assert {c.bps for c in cells} == {100, 150}
    assert len({c.run_id for c in cells}) == len(cells)


def test_surface_retains_empty_cells_and_computes_hold_delta():
    rows = f3.surface_rows([
        {"run_id": "A_pm_N2_R0_bps100", "release_id": "R0", "bps": 100, "mean_basket_day": -0.1,
         "blocks": {"block1": {"mean": -0.1}, "block2": {"mean": None}}},
        {"run_id": "A_pm_N2_R2_L10_w3_bps100", "release_id": "R2_L10_w3", "bps": 100, "mean_basket_day": -0.05,
         "blocks": {"block1": {"mean": -0.05}, "block2": {"mean": None}}},
    ], [c for c in f3.build_cells() if c.bps == 100 and c.release_id in {"R0", "R2_L10_w3"}])
    assert len(rows) == 2
    r2_row = next(row for row in rows if row["release_id"] == "R2_L10_w3")
    assert r2_row["delta_vs_hold_mean"] == pytest.approx(0.05)
    assert r2_row["blocks"]["block2"]["mean"] is None


def test_block_filter_treats_date_bounds_as_literals():
    import polars as pl

    daily = pl.DataFrame({"date": ["2021-02-01", "2024-01-02", "2025-02-03"]})
    filtered = f3._daily_in_block(daily, "2021-02-01", "2023-12-31")
    assert filtered["date"].to_list() == ["2021-02-01"]


def test_friction_cost_splits_entry_and_executed_exit_costs():
    ticket = {"unit_notional": 1.0, "shares_entry": 0.5, "entry_px": 1.0,
              "exit_px": 2.0, "open_end": False}
    assert f3.ticket_friction_cost(ticket, 100) == pytest.approx(0.505)
    ticket["open_end"] = True
    assert f3.ticket_friction_cost(ticket, 100) == pytest.approx(0.5)


def test_engine_schedules_r2_exit_only_at_later_bar_open():
    bars = sim.Bars("2021-02-01", __import__("polars").DataFrame(
        [("2021-02-01", "X", 570, 10., 10., 10., 10., 1.),
         ("2021-02-01", "X", 571, 10., 10., 8.9, 9., 1.),
         ("2021-02-01", "X", 572, 8., 8.5, 7.9, 8.2, 1.),
         ("2021-02-01", "X", 573, 8.2, 8.3, 8., 8.1, 1.),
         ("2021-02-01", "X", 574, 8.1, 8.2, 8., 8.1, 1.)],
        schema=["date", "ticker", "et", "open", "high", "low", "close", "volume"],
        orient="row").with_columns(__import__("polars").col("date").str.to_date()))
    rec = {"date": "2021-02-01", "snapshots": [{"pop": "A_pm", "T": 570,
           "names": [{"ticker": "X", "rank": 1, "fill": {"et": 570, "px": 10., "blocked": False}}]}]}
    strat = sim.Strategy(sim.StrategySpec(top_n=1, n_slots=1, release=[sim.R2(-10, 1)]))
    sim.simulate_day(strat, "2021-02-01", rec, bars, 574, 100.)
    assert strat.closed[0].exit_et == 573
    assert strat.closed[0].exit_px == 8.2
