from __future__ import annotations

import pytest

from factory.scripts import basket_f4_scaleout as f4
from factory.scripts import basket_sim as sim


def test_registered_surface_is_complete_and_includes_controls():
    cells = f4.build_cells()
    assert len(cells) == 74
    assert len({cell.run_id for cell in cells}) == len(cells)
    assert sum(cell.kind == "partial" for cell in cells) == 54
    assert sum(cell.kind == "full_exit" for cell in cells) == 18
    assert sum(cell.kind == "hold" for cell in cells) == 2


def test_fraction_is_current_shares_and_action_waits_for_next_bar_open():
    rule = f4.ScaleOutRule(f4.Trigger("R3", g=40), (0.25, 0.25))
    tk = sim.Ticket("X", "2021-02-01", 570, 10.0, 1.0, shares=0.1,
                    cost_open=1.0, cash_in=1.0, shares_entry=0.1, peak=20.0)
    bars = sim.Bars("2021-02-01", __import__("polars").DataFrame({
        "ticker": ["X"] * 5, "et": [570, 571, 572, 573, 574],
        "open": [10.0, 10.0, 8.0, 7.0, 7.0],
        "high": [10.0, 10.0, 10.0, 10.0, 10.0],
        "low": [10.0, 10.0, 8.0, 7.0, 7.0],
        "close": [10.0, 10.0, 10.0, 10.0, 10.0],
        "volume": [1.0] * 5,
    }))
    strategy = sim.Strategy(sim.StrategySpec(n_slots=1, release=[rule]))
    strategy.open_tickets["X"] = tk
    strategy.sleeve_cash[tk.sleeve_day] = 0.0
    strategy.deployed[tk.sleeve_day] = 1.0
    sim.simulate_day(strategy, tk.sleeve_day, {"date": tk.sleeve_day, "snapshots": []},
                     bars, 574, 100.0, False)
    reduces = [action for action in tk.actions if action["action"] == "REDUCE"]
    assert [(a["et"], a["px"]) for a in reduces] == [(571, 10.0), (572, 8.0)]
    assert tk.n_reduces == 2
    assert tk.shares == 0
    assert tk.actions[-1]["reason"].startswith("F4(R3_g40")
    assert tk.proceeds == pytest.approx((.025 * 10 + .01875 * 8 + .05625 * 7) * .995)


def test_staged_rule_suppresses_repeat_until_pending_reduce_executes():
    rule = f4.ScaleOutRule(f4.Trigger("R3", g=40), (0.5,))
    tk = sim.Ticket("X", "2021-02-01", 570, 10.0, 1.0, shares=0.1,
                    peak=20.0)
    bar = {"et": 571, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0}
    first = rule.evaluate(tk, bar, 1)
    assert first["frac"] == 0.5
    assert rule.evaluate(tk, {**bar, "et": 572}, 2) is None
    assert rule.state(tk)["awaiting_execution"] is True


def test_hold_and_full_exit_controls_are_not_partial_patterns():
    assert f4.hold_spec().release[0].name == "R0"
    trigger = f4.Trigger("R2", L=10, w=3)
    rule = f4.FullExitRule(trigger)
    ticket = sim.Ticket("X", "2021-02-01", 570, 10.0, 1.0, peak=10.0)
    action = None
    for idx in range(1, 5):
        action = rule.evaluate(ticket, {"et": 570 + idx, "open": 10, "high": 10,
                                        "low": 8.9, "close": 9}, idx)
    assert action["action"] == "EXIT"


def test_r1_release_has_priority_over_scaleout_and_consumes_trigger_bar():
    rule = f4.ScaleOutRule(f4.Trigger("R3", g=40), (0.25,))
    strategy = sim.Strategy(sim.StrategySpec(n_slots=1,
                                              release=[sim.R1(-10), rule]))
    tk = sim.Ticket("X", "2021-02-01", 570, 10.0, 1.0, shares=.1,
                    cost_open=1.0, cash_in=1.0, shares_entry=.1, peak=20.0)
    strategy.open_tickets["X"] = tk
    strategy.sleeve_cash[tk.sleeve_day] = 0
    strategy.deployed[tk.sleeve_day] = 1
    import polars as pl
    bars = sim.Bars(tk.sleeve_day, pl.DataFrame({
        "ticker": ["X"] * 4, "et": [570, 571, 572, 573],
        "open": [10.0] * 4, "high": [10.0] * 4, "low": [10.0, 8.9, 8.9, 8.9],
        "close": [15.0, 9.0, 9.0, 9.0], "volume": [1.0] * 4,
    }))
    sim.simulate_day(strategy, tk.sleeve_day, {"date": tk.sleeve_day, "snapshots": []},
                     bars, 573, 100.0, False)
    assert tk.n_reduces == 0
    assert tk.exit_reason == "R1(-10)"


def test_tail_metrics_use_first_touch_chronology_not_mfe_only():
    records = [{"mfe_raw": .8, "reduced_before_touch": {"50": True},
                "tail_fraction_at_touch": {"50": .75}},
               {"mfe_raw": .4, "reduced_before_touch": {}, "tail_fraction_at_touch": {}}]
    result = f4.tail_metrics(records)["50"]
    assert result["touch_n"] == 1
    assert result["reduced_early_n"] == 1
    assert result["reduced_early_rate"] == 1.0
    assert result["mean_raw_shares_retained_at_touch"] == .75


def test_fraction_accounting_is_cumulative_from_current_share_base():
    ticket = {"shares_entry": .1, "unit_notional": 1.0, "entry_px": 10.0,
              "entry_et": 570, "mfe_raw": 0.0, "mae_raw": 0.0,
              "net": 0.0, "net_return": 0.0, "open_end": False,
              "shares": 0.05625, "actions": [
                  {"action": "REDUCE", "et": 571, "px": 10.0, "reason": "F4(R3,0.25,0.25):stage1"},
                  {"action": "REDUCE", "et": 572, "px": 8.0, "reason": "F4(R3,0.25,0.25):stage2"},
              ], "h_touch_et": {}}
    rule = f4.ScaleOutRule(f4.Trigger("R3", g=40), (.25, .25))
    rule._tickets[("2021-02-01", "X")] = sim.Ticket(
        "X", "2021-02-01", 570, 10.0, 1.0, shares=.05625, cost_open=.5625,
        cash_in=1.0, shares_entry=.1, n_reduces=2,
        actions=ticket["actions"])
    rows = f4.ticket_chronology(rule, 100, {
        ("2021-02-01", "X"): {"mfe_raw": .8, "mae_raw": -.1,
                                "first_touch_et": {"50": 573},
                                "_ets": [570, 571, 572], "_opens": [10.0, 10.0, 8.0]}
    }, ["2021-02-01"])
    actions = rows[0]["actions"]
    assert [a["shares_sold"] for a in actions] == pytest.approx([.025, .01875])
    assert [a["execution_date"] for a in actions] == ["2021-02-01"] * 2


def test_raw_path_tail_uses_every_post_fill_bar_including_after_exit():
    result = f4.path_metrics(10.0, [570, 571, 572], [10.0, 10.5, 20.0],
                             [10.0, 9.0, 8.0], 570)
    assert result["mfe_raw"] == pytest.approx(1.0)
    assert result["mae_raw"] == pytest.approx(-.2)
    assert result["first_touch_et"] == {"30": 572, "50": 572, "100": 572}


def test_carried_execution_at_earlier_clock_minute_is_not_before_prior_session_touch(monkeypatch):
    import polars as pl

    day1, day2 = "2021-02-01", "2021-02-02"
    ticket = sim.Ticket("X", day1, 570, 10.0, 1.0, shares=.1,
                        shares_entry=.1, actions=[{"action": "REDUCE", "et": 570,
                                                   "px": 11.0,
                                                   "reason": "F4(R3_g40,0.25):stage1"}])
    rule = f4.ScaleOutRule(f4.Trigger("R3", g=40), (.25,))
    rule._tickets[(day1, "X")] = ticket
    future = sim.Bars(day2, pl.DataFrame({"ticker": ["X"], "et": [570],
                         "open": [11.0], "high": [11.0], "low": [11.0],
                         "close": [11.0], "volume": [1.0]}))
    monkeypatch.setattr(sim, "load_bars", lambda day, _end=None: future)
    monkeypatch.setattr(sim, "session_end_map", lambda: {day2: 959})
    paths = {(day1, "X"): {"mfe_raw": .6, "mae_raw": -.1,
                           "first_touch_et": {"50": 800},
                           "_ets": [570, 800], "_opens": [10.0, 10.0]}}
    rows = f4.ticket_chronology(rule, 100, paths, [day1, day2])
    action = rows[0]["actions"][0]
    assert action["execution_date"] == day2
    assert rows[0]["reduced_before_touch"]["50"] is False


def test_r2_forced_flat_is_not_clamped_to_deterioration_level():
    day = "2021-02-01"
    ticket = sim.Ticket("X", day, 570, 4.63, .5, shares=.1, shares_entry=.1,
                        actions=[{"action": "EXIT", "et": 959, "px": 4.6892,
                                  "reason": "FORCED_FLAT"}])
    rule = f4.FullExitRule(f4.Trigger("R2", L=10, w=3))
    rule._tickets[(day, "X")] = ticket
    paths = {(day, "X"): {"mfe_raw": 0., "mae_raw": 0., "first_touch_et": {},
                            "_ets": [570, 959], "_opens": [4.63, 4.6892]}}
    chronology = f4.ticket_chronology(rule, 100, paths, [day])
    assert chronology[0]["actions"][0]["px"] == 4.6892


def test_reduce_at_first_touch_open_changes_retained_tail_but_not_pre_touch_flag():
    day = "2021-02-01"
    ticket = sim.Ticket("X", day, 570, 10.0, .5, shares=.05, shares_entry=.05,
                        actions=[{"action": "REDUCE", "et": 570, "px": 15.0,
                                  "reason": "F4(R3_g40,0.25):stage1"}])
    rule = f4.ScaleOutRule(f4.Trigger("R3", g=40), (.25,))
    rule._tickets[(day, "X")] = ticket
    paths = {(day, "X"): {"mfe_raw": .5, "mae_raw": 0.,
                            "first_touch_et": {"50": 570},
                            "_ets": [570], "_opens": [15.0]}}
    record = f4.ticket_chronology(rule, 100, paths, [day])[0]
    assert record["tail_fraction_at_touch"]["50"] == pytest.approx(.75)
    assert record["reduced_before_touch"]["50"] is False


def test_saved_touch_chronology_refresh_includes_same_open_reductions():
    records = [{"sleeve_day": "2021-02-01", "shares_entry": .1,
                "first_touch_et": {"50": 570},
                "tail_fraction_at_touch": {"50": 1.0},
                "reduced_before_touch": {"50": False},
                "released_before_touch": {"50": False},
                "actions": [{"execution_date": "2021-02-01", "et": 570,
                              "action": "REDUCE", "shares_remaining": .075}]}]
    result = f4.refresh_touch_chronology(records)
    assert result[0]["tail_fraction_at_touch"]["50"] == pytest.approx(.75)
    assert result[0]["reduced_before_touch"]["50"] is False


def test_all_registered_triggers_patterns_and_friction_cells_are_present():
    cells = f4.build_cells()
    assert {t.trigger_id for t in f4.build_triggers()} == {
        *(f"R2_L{L}_w{w}" for L in (10, 15) for w in (3, 5, 10)),
        *(f"R3_g{g}" for g in (40, 50, 60)),
    }
    assert {c.pattern_id for c in cells if c.kind == "partial"} == set(f4.PATTERNS)
    assert {c.bps for c in cells} == {100, 150}


def test_partial_cell_keeps_matched_full_exit_and_hold_deltas():
    def row(kind, mean, run_id, trigger=None, pattern=None):
        return {"kind": kind, "bps": 100, "mean_basket_day": mean,
                "run_id": run_id, "trigger_id": trigger, "pattern_id": pattern,
                "avg_failed_ticket_cost": mean / 2,
                "blocks": {"block1": {"mean_basket_day": mean},
                           "block2": {"mean_basket_day": mean}}}

    trigger = "R2_L10_w3"
    rows = f4.attach_control_deltas([
        row("hold", -0.10, "hold"),
        row("full_exit", -0.06, "full", trigger),
        row("partial", -0.04, "part", trigger, "25_25_rest"),
    ])
    partial = rows[2]
    assert partial["delta_net_c0_ev_vs_hold"] == pytest.approx(.06)
    assert partial["delta_net_c0_ev_vs_matched_full_exit"] == pytest.approx(.02)
    assert partial["delta_net_c0_ev_by_block_vs_hold"] == {
        "block1": pytest.approx(.06), "block2": pytest.approx(.06)}
    assert rows[1]["delta_net_c0_ev_vs_matched_full_exit"] == 0
    assert rows[0]["delta_net_c0_ev_vs_matched_full_exit"] is None


def test_pending_reduce_executes_first_available_new_session_open():
    import polars as pl

    day1, day2 = "2021-02-01", "2021-02-02"
    strategy = sim.Strategy(sim.StrategySpec(n_slots=1))
    ticket = sim.Ticket("X", day1, 570, 10.0, 1.0, shares=.1,
                        cost_open=1.0, cash_in=1.0, shares_entry=.1, peak=10.0,
                        pending={"action": "REDUCE", "reason": "carried partial",
                                 "frac": .25, "after_et": 959, "level": None,
                                 "carry": True})
    strategy.open_tickets["X"] = ticket
    strategy.sleeve_cash[day1] = 0.0
    strategy.deployed[day1] = 1.0
    bars = sim.Bars(day2, pl.DataFrame({
        "ticker": ["X"] * 3, "et": [570, 571, 572], "open": [11.0, 12.0, 13.0],
        "high": [11.0, 12.0, 13.0], "low": [11.0, 12.0, 13.0],
        "close": [11.0, 12.0, 13.0], "volume": [1.0] * 3,
    }))
    sim.simulate_day(strategy, day2, {"date": day2, "snapshots": []}, bars, 572,
                     100.0, False)
    assert ticket.actions[0]["action"] == "REDUCE"
    assert (ticket.actions[0]["et"], ticket.actions[0]["px"]) == (570, 11.0)
    assert ticket.n_reduces == 1
    assert ticket.shares == 0
    assert ticket.exit_reason == "FORCED_FLAT"
    strategy._check_invariants(ticket, "carry-test")


def test_trigger_at_forced_flat_bar_does_not_schedule_a_reduce():
    import polars as pl

    day = "2021-02-01"
    rule = f4.ScaleOutRule(f4.Trigger("R3", g=40), (.25,))
    strategy = sim.Strategy(sim.StrategySpec(n_slots=1, release=[rule]))
    ticket = sim.Ticket("X", day, 570, 10.0, 1.0, shares=.1,
                        cost_open=1.0, cash_in=1.0, shares_entry=.1, peak=20.0)
    strategy.open_tickets["X"] = ticket
    strategy.sleeve_cash[day] = 0.0
    strategy.deployed[day] = 1.0
    bars = sim.Bars(day, pl.DataFrame({
        "ticker": ["X"] * 3, "et": [570, 571, 572], "open": [10.0] * 3,
        "high": [10.0] * 3, "low": [10.0] * 3, "close": [15.0, 10.0, 10.0],
        "volume": [1.0] * 3,
    }))
    sim.simulate_day(strategy, day, {"date": day, "snapshots": []}, bars, 572,
                     0.0, False)
    assert ticket.n_reduces == 0
    assert ticket.exit_reason == "FORCED_FLAT"


def test_runner_exposes_full_grid_and_deterministic_canary():
    import subprocess
    import sys

    result = subprocess.run([sys.executable, "factory/scripts/basket_f4_scaleout.py", "--help"],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "--full" in result.stdout
    assert "--max-days" in result.stdout
    assert "--canary" in result.stdout
