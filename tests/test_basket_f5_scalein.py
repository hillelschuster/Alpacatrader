from __future__ import annotations

from pathlib import Path

import numpy as np

def test_rule_triggers_only_strict_new_peak_per_ticket() -> None:
    from factory.scripts.basket_f5_scalein import ScaleInRule
    from factory.scripts import basket_sim as sim

    rule = ScaleInRule((0.25, 0.25))
    first = sim.Ticket("AAA", "2021-02-01", 570, 10.0, 0.5, peak=10.0)
    second = sim.Ticket("BBB", "2021-02-01", 570, 10.0, 0.5, peak=10.0)

    assert rule.evaluate(first, {"et": 571, "high": 10.0, "close": 10.0}, 1) is None
    assert rule.evaluate(first, {"et": 572, "high": 10.1, "close": 10.0}, 2)["frac"] == 0.25
    assert rule.evaluate(first, {"et": 573, "high": 10.1, "close": 10.0}, 3) is None
    assert rule.evaluate(first, {"et": 574, "high": 10.2, "close": 10.0}, 4)["frac"] == 0.25
    assert rule.evaluate(first, {"et": 575, "high": 10.3, "close": 10.0}, 5) is None
    assert rule.evaluate(second, {"et": 572, "high": 10.0, "close": 10.0}, 2) is None


def test_reserved_cash_signal_does_not_consume_next_staged_add() -> None:
    from factory.scripts.basket_f5_scalein import ScaleInRule
    from factory.scripts import basket_sim as sim

    day = "2021-02-01"
    decisions: list[dict] = []
    rule = ScaleInRule((0.25, 0.50), decisions, {day: 575})
    ticket = sim.Ticket("AAA", day, 570, 10.0, 0.5, peak=10.0)

    reserved = rule.evaluate(ticket, {"et": 571, "high": 10.1, "close": 10.0}, 1)
    assert rule.state(ticket).get("next_add", 0) == 0
    first_eligible = rule.evaluate(ticket, {"et": 575, "high": 10.2, "close": 10.1}, 2)
    second_eligible = rule.evaluate(ticket, {"et": 576, "high": 10.3, "close": 10.2}, 3)

    assert reserved is None
    assert decisions[0]["skip_reason"] == "pending_entry_cash_reserved"
    assert decisions[0]["size_frac"] == 0.25
    assert first_eligible["frac"] == 0.25
    assert second_eligible["frac"] == 0.50


def test_pending_selected_entry_reservation_does_not_create_executed_add() -> None:
    import polars as pl

    from factory.scripts.basket_f5_scalein import ScaleInRule
    from factory.scripts import basket_sim as sim

    day = "2021-02-01"
    decisions: list[dict] = []
    rule = ScaleInRule((0.25, 0.50), decisions, {day: 573})
    strategy = sim.Strategy(sim.StrategySpec(
        n_slots=2, release=[sim.R0()], scale_in=[rule]))
    first = sim.Ticket("AAA", day, 570, 10.0, 0.25, shares=0.25 / 10.05,
                       cost_open=0.25, cash_in=0.25, shares_entry=0.25 / 10.05,
                       peak=10.0)
    later_entry = sim.Ticket("BBB", day, 573, 10.0, 0.25,
                             pending={"action": "ENTER", "reason": "ENTRY",
                                      "after_et": 572, "level": None,
                                      "frac": None, "carry": False})
    strategy.open_tickets.update({"AAA": first, "BBB": later_entry})
    strategy.sleeve_cash[day] = 0.75
    strategy.deployed[day] = 0.25
    cash_before_reserved_signal = strategy._cash(day)
    reserved = rule.evaluate(first, {"et": 571, "high": 10.1, "close": 10.0}, 1)
    assert reserved is None
    assert rule.state(first).get("next_add", 0) == 0
    assert strategy._cash(day) == cash_before_reserved_signal
    decisions.clear()

    ets = list(range(570, 577))
    bars = sim.Bars(day, pl.DataFrame({
        "ticker": ["AAA"] * len(ets) + ["BBB"] * len(ets),
        "et": ets * 2,
        "open": [10.0, 10.0, 10.0, 10.0, 12.0, 12.0, 12.0] + [10.0] * len(ets),
        "high": [10.0, 10.1, 10.2, 10.3, 10.3, 10.3, 10.3] + [10.0] * len(ets),
        "low": [10.0] * (2 * len(ets)),
        "close": [10.0] * (2 * len(ets)),
        "volume": [1.0] * (2 * len(ets)),
    }))
    sim.simulate_day(strategy, day, {"date": day, "snapshots": []}, bars, 576, 100.0, False)

    adds = [action for action in first.actions if action["action"] == "ADD"]
    assert len(adds) == first.n_adds == 1
    assert adds[0]["et"] == 574
    assert adds[0]["px"] == 12.0
    assert [event["skip_reason"] for event in decisions if "skip_reason" in event] == [
        "pending_entry_cash_reserved"]
    assert decisions[-1]["size_frac"] == 0.25
    assert rule.state(first)["next_add"] == 1
    assert first.add_notional == 0.25 * first.unit_notional


def test_scalein_cell_grid_and_blocks_are_exact() -> None:
    from factory.scripts import basket_f5_scalein as f5

    cells = f5.build_cells()
    assert len(cells) == 840
    assert {cell.add_sizes for cell in cells} == {
        (), (0.25,), (0.5,), (1.0,), (0.25, 0.25), (0.25, 0.5), (0.5, 0.25)
    }
    assert {cell.bps for cell in cells} == {100, 150}
    assert len({cell.run_id for cell in cells}) == len(cells)
    assert all(sum(cell.add_sizes) <= 1.0 + 1e-12 for cell in cells)

    blocks = f5.dev_blocks(["2021-02-01", "2023-12-29", "2025-02-03", "2026-05-29"])
    assert blocks == {"pooled": ["2021-02-01", "2023-12-29", "2025-02-03", "2026-05-29"],
                      "block1": ["2021-02-01", "2023-12-29"],
                      "block2": ["2025-02-03", "2026-05-29"]}


def test_full_cell_requires_month_parts_and_engine_outputs(tmp_path: Path) -> None:
    from factory.scripts import basket_f5_scalein as f5

    run_dir = tmp_path / "F5" / "cell"
    run_dir.mkdir(parents=True)
    for name in f5.REQUIRED_OUTPUTS:
        (run_dir / name).write_text("[]" if name.endswith(".json") else "{}")
    assert not f5.is_complete(run_dir, ["2021-02-01", "2021-03-01"])
    (run_dir / "parts" / "daily").mkdir(parents=True)
    (run_dir / "parts" / "tickets").mkdir(parents=True)
    assert not f5.is_complete(run_dir, ["2021-02-01", "2021-03-01"])


def test_add_cell_cannot_reuse_without_decision_and_event_evidence(tmp_path: Path) -> None:
    import json

    from factory.scripts import basket_f5_scalein as f5

    cell = next(cell for cell in f5.build_cells() if cell.add_sizes)
    run_dir = tmp_path / "F5" / cell.run_id
    run_dir.mkdir(parents=True)
    for name in f5.REQUIRED_OUTPUTS:
        (run_dir / name).write_text("[]" if name.endswith(".json") else "{}")
    for kind in ("daily", "tickets"):
        part_dir = run_dir / "parts" / kind
        part_dir.mkdir(parents=True)
        for month in ("2021-02", "2021-03"):
            (part_dir / f"{month}.parquet").touch()
    (run_dir / "add_decisions.json").unlink()

    assert not f5.is_complete(run_dir, ["2021-02-01", "2021-03-01"], cell)

    (run_dir / "add_decisions.json").write_text(json.dumps([]))
    assert f5.is_complete(run_dir, ["2021-02-01", "2021-03-01"], cell)


def test_add_executes_next_open_and_release_consumes_trigger_bar() -> None:
    from factory.scripts.basket_f5_scalein import ScaleInRule
    from factory.scripts import basket_sim as sim

    day = "2021-02-01"
    def run(highs: list[float], lows: list[float], release: list) -> sim.Ticket:
        strat = sim.Strategy(sim.StrategySpec(n_slots=2, release=release,
                                               scale_in=[ScaleInRule((0.25,))]))
        tk = sim.Ticket("AAA", day, 570, 10.0, 0.5, shares=0.05, cost_open=0.5,
                        cash_in=0.5, shares_entry=0.05, peak=10.0)
        strat.open_tickets["AAA"] = tk
        strat.sleeve_cash[day] = 0.5
        strat.deployed[day] = 0.5
        bars = sim.Bars(day, __import__("polars").DataFrame({
            "ticker": ["AAA"] * 5, "et": np.array([570, 571, 572, 573, 574]),
            "open": [10.0, 10.5, 12.0, 12.0, 12.0], "high": highs,
            "low": lows, "close": [10.0] * 5, "volume": [1.0] * 5,
        }))
        sim.simulate_day(strat, day, {"date": day, "snapshots": []}, bars, 574, 0.0, False)
        return tk

    added = run([10.0, 11.0, 12.5, 12.5, 12.5], [10.0] * 5, [])
    add_action = next(action for action in added.actions if action["action"] == "ADD")
    assert add_action["et"] == 572
    assert add_action["px"] == 12.0

    released = run([10.0, 11.0, 12.5, 12.5, 12.5], [10.0, 8.0, 10.0, 10.0, 10.0],
                   [sim.R1(-10)])
    assert released.n_adds == 0
    assert released.exit_reason == "R1(-10)"


def test_add_event_accounting_excludes_reservation_skip_and_matches_control(tmp_path: Path) -> None:
    import polars as pl

    from factory.scripts import basket_f5_scalein as f5
    from factory.scripts import basket_sim as sim

    day = "2021-02-01"
    bars_dir = tmp_path / "bars"
    bars_dir.mkdir()
    pl.DataFrame({"ticker": ["AAA"] * 7, "et": list(range(570, 577)),
                  "open": [10.0, 10.0, 10.0, 10.0, 12.0, 12.0, 12.0],
                  "high": [10.0, 10.1, 10.2, 10.3, 10.3, 10.3, 10.3],
                  "close": [10.0, 10.0, 10.0, 10.0, 12.5, 12.7, 12.8]}).write_parquet(
                      bars_dir / f"{day}.parquet")
    old_bars_dir = sim.BARS_DIR
    old_session_end_map = sim.session_end_map
    sim.BARS_DIR = bars_dir
    sim.session_end_map = lambda: {day: 576}
    try:
        decisions = [
            {"sleeve_day": day, "ticker": "AAA", "decision_et": 571,
             "decision_close": 10.0, "decision_high": 10.1, "prior_peak": 10.0,
             "size_frac": 0.25, "skip_reason": "pending_entry_cash_reserved"},
            {"sleeve_day": day, "ticker": "AAA", "decision_et": 573,
             "decision_close": 10.0, "decision_high": 10.3, "prior_peak": 10.2,
             "size_frac": 0.25},
        ]
        trace = [{"sleeve_day": day, "ticker": "AAA", "execution_et": 574,
                  "execution_open": 12.0, "size_frac": 0.25,
                  "allocated_original_unit_notional": 0.0625,
                  "executed": True, "skip_cause": None, "friction_bps": 100}]
        tickets = [{"sleeve_day": day, "ticker": "AAA", "open_end": False,
                    "exit_et": 575, "exit_px": 13.0}]
        baseline = [{"sleeve_day": day, "ticker": "AAA", "net": -0.1}]
        outcome = f5._add_event_outcomes(decisions, trace, tickets, baseline, 100)
    finally:
        sim.BARS_DIR = old_bars_dir
        sim.session_end_map = old_session_end_map

    skipped, executed = outcome["events"]
    expected_shares = 0.0625 / (12.0 * 1.005)
    expected_net = expected_shares * 13.0 * 0.995 - 0.0625
    assert skipped["executed_status"] == "skipped"
    assert skipped["skip_cause"] == "pending_entry_cash_reserved"
    assert skipped["allocated_original_unit_notional"] is None
    assert executed["execution_et"] == executed["next_eligible_open_et"] == 574
    assert executed["execution_open"] == executed["next_eligible_open"] == 12.0
    assert executed["executed_status"] == "executed"
    assert executed["applicable_friction_bps"] == 100
    assert executed["matched_no_add_ticket_net"] == -0.1
    assert abs(executed["incremental_net_pnl_contribution"] - expected_net) < 1e-12
    block_ev = f5._tranche_ev_by_block(outcome["events"], {"pooled": [day]})
    assert block_ev["pooled"]["n_executed_tranches"] == 1
    assert abs(block_ev["pooled"]["incremental_net_pnl_contribution"] - expected_net) < 1e-12


def test_unfunded_and_over_cap_adds_are_skipped_and_flagged() -> None:
    from factory.scripts import basket_sim as sim

    for cash, deployed, already_added, expected_flag in (
        (0.1, 0.9, 0.0, "add_unfunded"),
        (0.5, 0.5, 0.9, "add_cap_exceeded"),
    ):
        strat = sim.Strategy(sim.StrategySpec(n_slots=1))
        tk = sim.Ticket("AAA", "2021-02-01", 570, 1.0, 0.5, shares=0.5,
                        cost_open=0.5, cash_in=0.5, add_notional=already_added)
        strat.sleeve_cash[tk.sleeve_day] = cash
        strat.deployed[tk.sleeve_day] = deployed
        sim._execute(strat, tk, "ADD", 1.0, 572, "test", 0.0, frac=0.5)
        assert expected_flag in tk.flags
        assert tk.n_adds == 0
        assert strat._cash(tk.sleeve_day) == cash


def test_runner_script_exposes_full_grid_cli() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "factory/scripts/basket_f5_scalein.py", "--help"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "--full" in result.stdout
    assert "--max-days" in result.stdout
