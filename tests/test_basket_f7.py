"""Focused tests for F7 released-capital recycling and its EXT-1 batch policy.

These tests drive the real engine (``sim.simulate_day``), the real policy hook and the real
ledger resolution (``basket_f7.resolve_cell_ledger``) on synthetic single-day fixtures, so
the policy, the engine call site, the released-capital definition and the ledger are
exercised together.  No canonical artifact is read or written and no full-span simulation
is run.
"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from factory.scripts import basket_f7 as f7
from factory.scripts import basket_sim as sim

DAY = "2021-02-01"
DAY0 = "2021-01-29"
SESSION_END = 600
CHECKPOINT = 580
ETS = list(range(570, SESSION_END + 1))       # 570..600, includes every checkpoint
TOL = 1e-12
UNIT = 0.5                                    # N=2 -> C0/2
N3_UNIT = 1.0 / 3.0

# Sentinel: build the spec without declaring any batch policy at all.
NO_POLICY = object()


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #


def _bars(day: str, series: dict[str, list[tuple]]) -> sim.Bars:
    rows = []
    for ticker, bars in series.items():
        for et, open_px, high, low, close in bars:
            rows.append({"ticker": ticker, "et": et, "open": open_px, "high": high,
                         "low": low, "close": close, "volume": 1.0})
    return sim.Bars(day, pl.DataFrame(rows))


def _tape(ets, px: float = 10.0) -> list[tuple]:
    """A flat tape over the given minutes."""
    return [(et, px, px, px, px) for et in ets]


def _flat(day: str, tickers: list[str], ets=ETS, px: float = 10.0,
          overrides: dict | None = None) -> sim.Bars:
    """A flat tape; ``overrides[(ticker, et)] = (open, high, low, close)``."""
    overrides = overrides or {}
    series = {ticker: [(et, *overrides.get((ticker, et), (px, px, px, px))) for et in ets]
              for ticker in tickers}
    return _bars(day, series)


def _release_tape(ets, breach_et: int, w: int, *, level: float = 9.0,
                  px: float = 10.0) -> list[tuple]:
    """A flat tape where the low first breaches ``0.9 * fill`` at ``breach_et`` and the
    close is still at the level ``w`` completed bars later, so the R2 window releases."""
    rows = []
    for et in ets:
        if et == breach_et:
            rows.append((et, px, px, level, px))
        elif et == breach_et + w:
            rows.append((et, px, px, level, level))
        else:
            rows.append((et, px, px, px, px))
    return rows


def _rec(day: str, pop: str, T: int, names: list[tuple]) -> dict:
    """Minimal anatomy record: ``names = [(ticker, rank, fill_et, fill_px, blocked)]``."""
    return {"date": day, "snapshots": [{"pop": pop, "T": T, "names": [
        {"ticker": ticker, "rank": rank, "sel": True,
         "fill": None if blocked else {"et": fill_et, "px": fill_px, "gap_min": 0.0,
                                       "blocked": False}}
        for ticker, rank, fill_et, fill_px, blocked in names]}]}


def _pm_rec(day: str, names: list[tuple]) -> dict:
    return _rec(day, "A_pm", 570, names)


def _cell(trigger_id: str = "R2_L10_w3", q: float | None = 1.0, bps: int = 100,
          timing: str = f7.REGISTERED_TIMING) -> f7.Cell:
    kind, L, w, g = f7.trigger_spec(trigger_id)[1:]
    return f7.Cell(trigger_id, kind, L, w, g, q, bps, timing)


def _simulate(cell: f7.Cell, policy, day: str, bars: sim.Bars, rec: dict, *,
              n: int | None = None, session_end: int = SESSION_END,
              do_entries: bool = True, carry=None, seed=None) -> sim.Strategy:
    """Run one synthetic day.

    ``policy is NO_POLICY`` declares no batch policy at all; ``policy is None`` lets the
    cell build its own (a hold cell then declares none).
    """
    if policy is NO_POLICY:
        spec = sim.StrategySpec(family_id="F7test", entry_pop="A_pm", entry_T=570,
                                top_n=n or 2, n_slots=n or 2, reserve_frac=1.0,
                                release=[sim.R0()] if cell.holds else [cell.rule()],
                                scale_in=[], name=cell.run_id)
    else:
        spec = cell.strategy() if policy is None else cell.strategy(policy)
        if n is not None:
            spec.top_n = n
            spec.n_slots = n
    strat = sim.Strategy(spec)
    if seed is not None:
        seed(strat)
    sim.simulate_day(strat, day, rec, bars, session_end, float(cell.bps),
                     do_entries=do_entries, carry=carry)
    return strat


def _tickets(strat: sim.Strategy) -> dict[str, sim.Ticket]:
    """Every ticket the strategy ever created, open or closed."""
    return {tk.ticker: tk for tk in list(strat.closed) + list(strat.open_tickets.values())}


def _rows(events: list[dict], kind: str) -> list[dict]:
    return [row for row in events if row["row_kind"] == kind]


def _by_ticker(rows: list[dict], ticker: str) -> dict:
    found = [row for row in rows if row["ticker"] == ticker]
    assert len(found) == 1, f"expected exactly one row for {ticker}, got {len(found)}"
    return found[0]


# --------------------------------------------------------------------------- #
# 1. hook inertness: the hold arm declares no policy
# --------------------------------------------------------------------------- #


def test_hold_arm_declares_no_policy_and_is_the_primitive_baseline() -> None:
    assert "batch_policy" in sim.StrategySpec.__dataclass_fields__
    assert sim.StrategySpec().batch_policy is None

    cell = _cell(q=None)
    assert cell.arm == f7.ARM_HOLD
    assert cell.build_policy() is None
    spec = cell.strategy()
    baseline = sim.StrategySpec(family_id="F1", entry_pop="A_pm", entry_T=570, top_n=2,
                                n_slots=2, reserve_frac=1.0, release=[sim.R0()],
                                scale_in=[], name="A_pm_N2_R0_bps100")
    assert spec.batch_policy is None
    for field in ("entry_pop", "entry_T", "top_n", "n_slots", "reserve_frac", "scale_in"):
        assert getattr(spec, field) == getattr(baseline, field)
    assert [rule.name for rule in spec.release] == [rule.name for rule in baseline.release]
    assert spec.release[0].name == "R0"

    rec = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, 570, 10.0, False)])
    bars = _flat(DAY, ["AAA", "BBB"])
    from_cell = _simulate(cell, NO_POLICY, DAY, bars, rec)
    from_baseline = sim.Strategy(baseline)
    sim.simulate_day(from_baseline, DAY, rec, bars, SESSION_END, 100.0)

    assert from_cell.day_rows[-1] == from_baseline.day_rows[-1]
    assert ({tk.ticker: (tk.net(), tk.n_adds, tk.unit_notional)
             for tk in _tickets(from_cell).values()}
            == {tk.ticker: (tk.net(), tk.n_adds, tk.unit_notional)
                for tk in _tickets(from_baseline).values()})
    # the hold arm has no release ledger at all: nothing was ever declared
    assert f7.resolve_cell_ledger(cell, cell.build_policy()) == []
    # every hold row collapses onto the same run, whatever trigger it declares
    hold_cells = [row for row in f7.build_cells() if row.holds]
    assert len(hold_cells) == 18
    assert {row.run_id for row in hold_cells} == {"A_pm_N2_R0_bps100",
                                                  "A_pm_N2_R0_bps150"}


# --------------------------------------------------------------------------- #
# 2. the q=0 arm is the same-trigger full exit
# --------------------------------------------------------------------------- #


def test_q0_arm_is_identical_to_the_same_trigger_full_exit() -> None:
    cell = _cell(q=0.0)
    policy = cell.build_policy()
    assert isinstance(policy, f7.RecyclePolicy) and policy.q == 0.0
    rec = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, 570, 10.0, False)])
    bars = _bars(DAY, {"AAA": _release_tape(ETS, 571, 3), "BBB": _tape(ETS)})
    declared = _simulate(cell, policy, DAY, bars, rec)
    plain = _simulate(cell, NO_POLICY, DAY, bars, rec)

    assert declared.day_rows[-1] == plain.day_rows[-1]
    assert ({tk.ticker: (tk.net(), tk.n_adds, tk.exit_et, tk.exit_reason)
             for tk in _tickets(declared).values()}
            == {tk.ticker: (tk.net(), tk.n_adds, tk.exit_et, tk.exit_reason)
                for tk in _tickets(plain).values()})
    assert declared.n_skipped_adds == plain.n_skipped_adds == 0
    assert all(tk.n_adds == 0 for tk in _tickets(declared).values())

    events = f7.resolve_cell_ledger(cell, policy)
    releases = _rows(events, f7.ROW_KIND_RELEASE)
    assert len(releases) == 1
    release = releases[0]
    assert release["status"] == f7.STATUS_RELEASE_TO_CASH
    assert release["ticker"] == "AAA" and release["release_et"] == 575
    assert release["released_notional"] == pytest.approx(UNIT, abs=TOL)
    assert release["requested_notional"] == pytest.approx(0.0, abs=TOL)
    assert release["retained_notional"] == pytest.approx(UNIT, abs=TOL)
    assert release["plan_id"] is not None and release["checkpoint_et"] == CHECKPOINT
    assert release["eligible_n"] == 1                     # BBB survived the release
    assert policy.allocations == []
    assert _rows(events, f7.ROW_KIND_ALLOCATION) == []
    assert policy.plans[0]["status"] == f7.STATUS_RELEASE_TO_CASH


# --------------------------------------------------------------------------- #
# 3. released capital is the removed cost basis
# --------------------------------------------------------------------------- #


def test_released_capital_is_the_removed_cost_basis_not_the_proceeds() -> None:
    cell = _cell(q=1.0)
    policy = cell.build_policy()
    rec = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, 570, 10.0, False)])
    bars = _bars(DAY, {"AAA": _release_tape(ETS, 571, 3), "BBB": _tape(ETS)})
    strat = _simulate(cell, policy, DAY, bars, rec)
    events = f7.resolve_cell_ledger(cell, policy)
    release = _by_ticker(_rows(events, f7.ROW_KIND_RELEASE), "AAA")
    ticket = _tickets(strat)["AAA"]

    # the basis is the entry budget, while the exit proceeds are net of friction and of
    # the adverse level clamp -- the two are never the same number
    assert release["released_notional"] == pytest.approx(UNIT, abs=TOL)
    assert release["released_notional"] == pytest.approx(ticket.cash_in, abs=TOL)
    assert ticket.proceeds < UNIT
    assert ticket.exit_reason == "R2(-10,3)"
    assert f7.removed_cost_basis(ticket, cell.bps) == [(1, UNIT)]
    assert [act["action"] for act in ticket.actions] == ["ENTER", "EXIT"]
    assert release["release_id"] == f"{DAY}:AAA:E575"

    allocation = _by_ticker(_rows(events, f7.ROW_KIND_ALLOCATION), "BBB")
    # the recycle is funded from sleeve cash, so it can never exceed it
    assert allocation["executed_notional"] <= allocation["sleeve_cash"] + TOL
    assert allocation["executed_notional"] <= allocation["budget_notional"] + TOL
    assert allocation["budget_notional"] == pytest.approx(
        min(cell.q * release["released_notional"], allocation["sleeve_cash"],
            sim.C0 - allocation["deployed_at_checkpoint"]), abs=1e-12)
    assert allocation["deployed_at_checkpoint"] + allocation["executed_notional"] <= \
        sim.C0 + TOL
    assert allocation["execution_et"] == 581               # next bar, never same-bar
    assert allocation["execution_px"] == pytest.approx(10.0)
    assert allocation["status"] == f7.STATUS_EXECUTED
    assert allocation["executed"] is True
    assert allocation["terminal_basis"] == "exit"
    assert allocation["terminal_reason"] == "FORCED_FLAT"     # the recipient's own exit
    assert allocation["terminal_et"] == SESSION_END
    assert allocation["tranche_cash_in"] == pytest.approx(allocation["executed_notional"])
    assert allocation["tranche_net_pnl"] == pytest.approx(
        allocation["tranche_realized_net"] + allocation["tranche_marked_net"]
        + allocation["tranche_terminal_value"] - allocation["tranche_cash_in"], abs=1e-9)
    assert release["executed_notional"] == pytest.approx(allocation["executed_notional"])
    assert release["retained_notional"] == pytest.approx(
        UNIT - allocation["executed_notional"], abs=1e-12)


# --------------------------------------------------------------------------- #
# 4. exact equal split for k = 2 and k = 1, with cap truncation
# --------------------------------------------------------------------------- #


def _three_slot_two_release_day(cell: f7.Cell, policy):
    """Three slots; AAA releases at ET575 and BBB at ET590; CCC survives both."""
    rec = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, 570, 10.0, False),
                        ("CCC", 2, 570, 10.0, False)])
    bars = _bars(DAY, {
        "AAA": _release_tape(ETS, 571, 3),
        "BBB": _release_tape(ETS, 586, 3),
        "CCC": _tape(ETS),
    })
    strat = _simulate(cell, policy, DAY, bars, rec, n=3)
    events = f7.resolve_cell_ledger(cell, policy)
    return strat, events


def test_equal_split_for_two_survivors_and_cap_truncation_for_one() -> None:
    cell = _cell(q=1.0)
    policy = cell.build_policy()
    strat, events = _three_slot_two_release_day(cell, policy)

    allocations = _rows(events, f7.ROW_KIND_ALLOCATION)
    releases = _rows(events, f7.ROW_KIND_RELEASE)
    assert {row["release_et"] for row in releases} == {575, 590}

    first = [row for row in allocations if row["checkpoint_et"] == CHECKPOINT]
    assert {row["ticker"] for row in first} == {"BBB", "CCC"}
    # k = 2: the pool is split exactly equally, with no rank-1 concentration
    shares = {row["equal_share_notional"] for row in first}
    assert len(shares) == 1
    assert all(row["requested_notional"] == pytest.approx(row["equal_share_notional"],
                                                          abs=TOL) for row in first)
    assert sum(row["requested_notional"] for row in first) == pytest.approx(
        policy.plans[0]["budget_notional"], abs=1e-12)
    assert all(row["cap_limited_notional"] == pytest.approx(0.0, abs=TOL) for row in first)
    assert all(row["execution_et"] == 581 for row in first)
    assert all(row["executed"] is True for row in first)
    assert {row["entry_rank"] for row in first} == {1, 2}
    assert first[0]["eligible_n"] == 2

    # the second release is triggered by BBB's executed EXIT and its basis includes the
    # capital BBB received at the first checkpoint: a release is the removed cost basis,
    # never a signal or a mark
    second = [row for row in allocations if row["checkpoint_et"] == 590]
    assert len(second) == 1 and second[0]["ticker"] == "CCC"
    bbb_add = _by_ticker(first, "BBB")["executed_notional"]
    bbb_release = [row for row in releases if row["ticker"] == "BBB"][0]
    assert bbb_release["released_notional"] == pytest.approx(N3_UNIT + bbb_add, abs=1e-12)

    # k = 1 with an exhausted cap headroom: the request is truncated at the engine cap and
    # the remainder stays in the sleeve as cash
    row = second[0]
    assert row["status"] == f7.STATUS_CAP_LIMITED
    assert row["cap_headroom"] < row["equal_share_notional"]
    assert row["requested_notional"] == pytest.approx(row["cap_headroom"], abs=1e-12)
    assert row["cap_limited_notional"] == pytest.approx(
        row["equal_share_notional"] - row["requested_notional"], abs=1e-12)
    assert row["cap_limited_notional"] > 0.0
    assert row["executed"] is True
    assert bbb_release["status"] == f7.STATUS_RECYCLED
    assert bbb_release["retained_notional"] > 0.0

    ccc = _tickets(strat)["CCC"]
    assert ccc.n_adds == 2
    assert ccc.add_notional == pytest.approx(ccc.unit_notional, abs=1e-9)   # cap reached
    assert strat._cash(DAY) >= 0.0
    assert strat._deployed(DAY) <= sim.C0 + TOL


# --------------------------------------------------------------------------- #
# 5. pending-action exclusion at the checkpoint bar
# --------------------------------------------------------------------------- #


def test_pending_action_on_the_checkpoint_bar_is_excluded() -> None:
    cell = _cell(q=1.0)
    policy = cell.build_policy()
    rec = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, 570, 10.0, False),
                        ("CCC", 2, 570, 10.0, False)])
    bars = _bars(DAY, {
        "AAA": _release_tape(ETS, 571, 3),            # releases at ET575 (pooled at 580)
        "BBB": _release_tape(ETS, 577, 3),            # releases ON the ET580 checkpoint
        "CCC": _tape(ETS),
    })
    strat = _simulate(cell, policy, DAY, bars, rec, n=3)
    events = f7.resolve_cell_ledger(cell, policy)

    excluded = _rows(events, f7.ROW_KIND_EXCLUDED)
    assert {row["ticker"] for row in excluded} == {"BBB"}
    row = excluded[0]
    assert row["status"] == f7.STATUS_PENDING_EXCLUDED
    assert row["exclusion_reason"] == "pending_action"
    assert row["pending_action"] == "EXIT"
    assert row["decision_et"] == CHECKPOINT
    assert row["executed"] is False
    assert row["requested_notional"] == pytest.approx(0.0, abs=TOL)

    # the split denominator is the eligible set only: CCC receives the whole pool
    allocation = _by_ticker(_rows(events, f7.ROW_KIND_ALLOCATION), "CCC")
    assert allocation["requested_notional"] == pytest.approx(
        allocation["equal_share_notional"], abs=TOL)
    assert allocation["eligible_n"] == 1 and allocation["excluded_n"] == 1
    tickets = _tickets(strat)
    assert tickets["BBB"].n_adds == 0
    assert tickets["CCC"].n_adds == 1
    # BBB's own release was pending on the checkpoint bar and executed on the next bar
    assert tickets["BBB"].exit_et == CHECKPOINT + 1
    assert tickets["BBB"].exit_reason == "R2(-10,3)"


# --------------------------------------------------------------------------- #
# 6. sleeve-scoped identity: the same ticker in two sleeves stays independent
# --------------------------------------------------------------------------- #


def test_same_ticker_two_sleeve_independence() -> None:
    cell = _cell(q=1.0)
    policy = cell.build_policy()
    rec = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, 570, 10.0, False)])
    bars = _bars(DAY, {"AAA": _release_tape(ETS, 571, 3), "BBB": _tape(ETS)})

    carried_unit = 0.25
    carried = sim.Ticket("AAA", DAY0, 570, 10.0, carried_unit, entry_rank=0, peak=10.0)
    carried.shares = carried.shares_entry = carried_unit / 10.0
    carried.cash_in = carried.cost_open = carried_unit
    carried.actions.append({"day": DAY0, "et": 570, "action": "ENTER", "px": 10.0,
                            "reason": "ENTRY", "shares": carried.shares,
                            "shares_after": carried.shares})

    def seed(strat: sim.Strategy) -> None:
        strat.open_tickets[(DAY0, "AAA")] = carried
        strat.sleeve_cash[DAY0] = 1.0 - carried_unit
        strat.deployed[DAY0] = carried_unit

    strat = _simulate(cell, policy, DAY, bars, rec, seed=seed)
    events = f7.resolve_cell_ledger(cell, policy)

    fresh = [tk for tk in list(strat.closed) + list(strat.open_tickets.values())
             if tk.sleeve_day == DAY and tk.ticker == "AAA"]
    assert len(fresh) == 1 and fresh[0] is not carried
    assert fresh[0].sleeve_day == DAY
    # only the current sleeve can be planned for, and only from its own cash
    assert {row["sleeve_day"] for row in events
            if row["row_kind"] != f7.ROW_KIND_RELEASE} == {DAY}
    allocations = _rows(events, f7.ROW_KIND_ALLOCATION)
    assert {row["sleeve_day"] for row in allocations} == {DAY}
    assert {row["ticker"] for row in allocations} == {"BBB"}
    # the carried ticket's own sleeve cash is untouched by the recycle, and the carried
    # ticket never receives recycled capital
    assert strat._cash(DAY0) == pytest.approx(1.0 - carried_unit + carried.proceeds, abs=TOL)
    assert carried.n_adds == 0 and carried.add_notional == pytest.approx(0.0, abs=TOL)
    assert strat._cash(DAY) == pytest.approx(0.0, abs=1e-9)
    # the carried sleeve's own release is out of scope for the current sleeve's plan
    carried_release = [row for row in _rows(events, f7.ROW_KIND_RELEASE)
                       if row["sleeve_day"] == DAY0]
    assert len(carried_release) == 1
    assert carried_release[0]["status"] == f7.STATUS_CROSS_SESSION_OUT_OF_SCOPE
    assert carried_release[0]["executed_notional"] == pytest.approx(0.0, abs=TOL)


# --------------------------------------------------------------------------- #
# 7. a release that never executes recycles nothing
# --------------------------------------------------------------------------- #


def test_release_that_never_executes_recycles_nothing() -> None:
    cell = _cell(q=1.0)
    policy = cell.build_policy()
    rec = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, 570, 10.0, False)])
    # AAA's window releases on its last bar of the session: the EXIT cannot execute today
    bars = _bars(DAY, {"AAA": _release_tape(list(range(570, 586)), 582, 3),
                       "BBB": _tape(ETS)})
    strat = _simulate(cell, policy, DAY, bars, rec)
    events = f7.resolve_cell_ledger(cell, policy)

    ticket = _tickets(strat)["AAA"]
    assert ticket.open is True
    assert ticket.pending is not None and ticket.pending["action"] == "EXIT"
    assert ticket.pending["carry"] is True
    assert f7.removed_cost_basis(ticket, cell.bps) == []
    assert events == []
    assert policy.plans == [] and policy.allocations == []
    assert all(tk.n_adds == 0 for tk in _tickets(strat).values())


# --------------------------------------------------------------------------- #
# 8. boundary / data-end marks never fund a recycle
# --------------------------------------------------------------------------- #


def test_boundary_and_data_end_marks_never_fund_a_recycle() -> None:
    cell = _cell(q=1.0)
    policy = cell.build_policy()
    rec = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, 570, 10.0, False)])
    bars = _flat(DAY, ["AAA", "BBB"])
    strat = _simulate(cell, policy, DAY, bars, rec)

    sim.terminalize(strat, "DATA_END_MARK")
    events = f7.resolve_cell_ledger(cell, policy)

    tickets = _tickets(strat)
    assert tickets["AAA"].terminal_kind == "DATA_END_MARK"
    assert tickets["AAA"].terminal_value > 0.0            # a positive mark ...
    assert tickets["AAA"].exit_reason == "DATA_END_MARK"
    assert f7.removed_cost_basis(tickets["AAA"], cell.bps) == []   # ... is not an action
    assert events == []
    assert policy.plans == []


# --------------------------------------------------------------------------- #
# 9. paired-effect arithmetic
# --------------------------------------------------------------------------- #


def test_paired_effect_arithmetic_and_signs() -> None:
    effects = f7.paired_effects(0.10, 0.04, 0.09, 10)
    assert effects["release_effect"] == pytest.approx(-0.06)
    assert effects["redeployment_effect"] == pytest.approx(0.05)
    assert effects["total_effect"] == pytest.approx(-0.01)
    assert effects["release_effect_mean_basket_day"] == pytest.approx(-0.006)
    assert effects["redeployment_effect_mean_basket_day"] == pytest.approx(0.005)
    assert effects["total_effect_mean_basket_day"] == pytest.approx(-0.001)
    assert effects["effects_add_up"] is True
    assert effects["total_effect"] == pytest.approx(
        effects["release_effect"] + effects["redeployment_effect"])

    better = f7.paired_effects(0.0, 0.05, 0.12, 4)
    assert better["release_effect"] > 0.0
    assert better["redeployment_effect"] > 0.0
    assert better["total_effect"] > 0.0
    assert better["total_effect_mean_basket_day"] == pytest.approx(0.03)

    # missing arms stay null instead of being invented
    hold = f7.paired_effects(0.10, None, None, 10)
    assert hold["release_effect"] is None and hold["redeployment_effect"] is None
    assert hold["total_effect"] is None and hold["effects_add_up"] is None
    release_only = f7.paired_effects(0.10, 0.04, None, 10)
    assert release_only["release_effect"] == pytest.approx(-0.06)
    assert release_only["redeployment_effect"] is None
    assert release_only["total_effect"] is None

    # the effects table joins the three arms per path/friction/fraction and block
    cells = f7.build_cells(("R3_g40",))
    cell = [row for row in cells if row.q == 1.0][0]
    stats = {cell.h_run_id: {"pooled": {"days_n": 10, "total_pnl": 0.10,
                                        "mean_basket_day": 0.01},
                             "block1": {"days_n": 6, "total_pnl": 0.06,
                                        "mean_basket_day": 0.01}},
             cell.c_run_id: {"pooled": {"days_n": 10, "total_pnl": 0.04,
                                        "mean_basket_day": 0.004},
                             "block1": {"days_n": 6, "total_pnl": 0.03,
                                        "mean_basket_day": 0.005}},
             cell.e_run_id: {"pooled": {"days_n": 10, "total_pnl": 0.09,
                                        "mean_basket_day": 0.009},
                             "block1": {"days_n": 6, "total_pnl": 0.05,
                                        "mean_basket_day": 0.008333333333}}}
    rows = f7._effects_rows(cells, stats, ["2021-02-01", "2021-02-02"])
    pooled = [row for row in rows if row["block"] == "pooled" and row["bps"] == 100]
    assert len(pooled) == 3 and {row["q"] for row in pooled} == {0.0, 0.5, 1.0}
    row = [entry for entry in pooled if entry["q"] == 1.0][0]
    assert row["days_n"] == 10 and row["h_run_id"] == cell.h_run_id
    assert row["c_run_id"] == cell.c_run_id and row["e_run_id"] == cell.e_run_id
    assert row["release_effect"] == pytest.approx(-0.06)
    assert row["redeployment_effect"] == pytest.approx(0.05)
    assert row["total_effect"] == pytest.approx(-0.01)
    # the release-to-cash arm has no redeployment arm, so those effects stay null
    release_only = [entry for entry in pooled if entry["q"] == 0.0][0]
    assert release_only["release_effect"] == pytest.approx(-0.06)
    assert release_only["redeployment_effect"] is None
    assert release_only["total_effect"] is None
    block1 = [entry for entry in rows
              if entry["block"] == "block1" and entry["q"] == 1.0 and entry["bps"] == 100][0]
    assert block1["days_n"] == 6
    assert block1["release_effect"] == pytest.approx(0.03 - 0.06)
    assert block1["release_effect_mean_basket_day"] == pytest.approx(-0.03 / 6)


# --------------------------------------------------------------------------- #
# 10. registered grid and alias collapse
# --------------------------------------------------------------------------- #


def test_registered_grid_collapses_each_unique_behaviour_once() -> None:
    cells = f7.build_cells()
    runs = f7.unique_runs()
    assert len(cells) == 72
    assert len(runs) == 56
    assert len({cell.declared_id for cell in cells}) == 72
    assert sum(1 for cell in cells if cell.is_alias) == 16
    assert {cell.trigger_id for cell in cells} == {spec[0] for spec in f7.TRIGGER_SPECS}
    assert {cell.arm for cell in cells} == {"hold", "q000", "q050", "q100"}
    assert {cell.q for cell in cells if not cell.holds} == {0.0, 0.5, 1.0}
    assert {cell.bps for cell in cells} == {100, 150}
    assert {cell.timing for cell in cells} == {f7.REGISTERED_TIMING}

    for cell in cells:
        assert cell.run_id in runs
        assert runs[cell.run_id].run_id == cell.run_id
        spec = cell.strategy()
        assert spec.n_slots == spec.top_n == 2
        assert spec.reserve_frac == 1.0 and spec.scale_in == []
        assert (spec.batch_policy is None) == cell.holds
        if cell.is_alias:
            assert cell.alias_reason and "inert" in cell.alias_reason
        else:
            assert cell.alias_reason is None
        if not cell.holds:
            assert spec.release[0].name == f7.trigger_rule_name(cell.kind, cell.L,
                                                                cell.w, cell.g)
            assert spec.batch_policy.q == cell.q
            assert spec.batch_policy.checkpoint_ets == tuple(sim.CHECKPOINTS)
            assert spec.batch_policy.timing == cell.timing

    assert f7.trigger_rule_name("R2", 10, 3, None) == "R2(-10,3)"
    assert f7.trigger_rule_name("R2", 15, 10, None) == "R2(-15,10)"
    assert f7.trigger_rule_name("R3", None, None, 40) == "R3(40)"

    grid = f7.registered_grid()
    assert grid["logical_cells"] == 72 and grid["unique_simulations"] == 56
    assert grid["timing"] == f7.REGISTERED_TIMING
    assert grid["checkpoint_ets"] == list(sim.CHECKPOINTS)
    assert f7.dev_blocks(["2021-02-01", "2023-12-29", "2025-02-03", "2026-05-29"]) == {
        "pooled": ["2021-02-01", "2023-12-29", "2025-02-03", "2026-05-29"],
        "block1": ["2021-02-01", "2023-12-29"],
        "block2": ["2025-02-03", "2026-05-29"],
    }

    # a --trigger subset declares its own grid, and the HOLD reference run stays available
    subset = f7.build_cells(("R3_g40",))
    subset_runs = f7.unique_runs(("R3_g40",))
    assert len(subset) == 8
    assert len(subset_runs) == 8
    assert all(cell.h_run_id in subset_runs for cell in subset)
    assert f7.parse_triggers(["R3_g40,R2_L10_w5"]) == ("R3_g40", "R2_L10_w5")
    assert f7.parse_triggers(None) is None
    with pytest.raises(f7.F7ContractError):
        f7.parse_triggers(["R9_nope"])


# --------------------------------------------------------------------------- #
# 11. ledger, surface and aggregate builders cover their schemas
# --------------------------------------------------------------------------- #


def test_ledger_surface_and_aggregate_builders_cover_their_schemas() -> None:
    cell = _cell(q=1.0)
    policy = cell.build_policy()
    rec = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, 570, 10.0, False)])
    bars = _bars(DAY, {"AAA": _release_tape(ETS, 571, 3), "BBB": _tape(ETS)})
    strat = _simulate(cell, policy, DAY, bars, rec)
    events = f7.resolve_cell_ledger(cell, policy)
    assert events and all(set(row) >= set(f7._EVENT_SCHEMA) for row in events)
    assert {row["row_kind"] for row in events} <= {f7.ROW_KIND_RELEASE,
                                                   f7.ROW_KIND_ALLOCATION,
                                                   f7.ROW_KIND_EXCLUDED}

    metrics = sim.compute_metrics(
        strat.day_rows, list(strat.closed) + list(strat.open_tickets.values()), 1,
        strat.n_blocked_slots, strat.n_pending, strat.n_carries, draws=50)
    total = float(strat.day_rows[-1]["pnl"])
    totals = {cell.h_run_id: total - 0.02, cell.c_run_id: total - 0.01,
              cell.e_run_id: total}
    surface = f7._surface_row(cell, "ran", metrics, total, events, totals, days=[DAY],
                              reused=False)
    assert set(surface) >= set(f7._POOLED_SCHEMA)
    assert set(f7._pooled_from_surface(surface)) == set(f7._POOLED_SCHEMA)
    assert surface["release_effect"] == pytest.approx(0.01)
    assert surface["redeployment_effect"] == pytest.approx(0.01)
    assert surface["total_effect"] == pytest.approx(0.02)
    assert surface["released_notional"] == pytest.approx(UNIT, abs=TOL)
    assert surface["events_n"] == len(events)
    assert surface["releases_n"] == 1 and surface["allocations_n"] == 1
    assert surface["executed_n"] == 1
    # the released pool is fully accounted for: executed plus retained cash
    assert surface["released_notional"] == pytest.approx(
        surface["executed_notional"] + surface["retained_notional"], abs=1e-12)

    daily = pl.DataFrame({
        "date": [DAY], "r_day": [0.01], "pnl": [0.01], "deployed_end": [0.9],
        "deployed_avg": [0.8], "n_open_end": [1], "n_actions": [3],
    })
    day_rows = f7._aggregate_day_rows(cell, daily)
    assert len(day_rows) == 1 and set(day_rows[0]) == set(f7._DAY_SCHEMA)
    assert day_rows[0]["block"] == "block1"

    tickets = pl.DataFrame({"sleeve_day": [DAY], "open_end": [False],
                            "exit_day": [DAY], "n_adds": [1]})
    block_rows = f7._aggregate_block_rows(cell, daily, tickets, events, [DAY])
    assert {row["block"] for row in block_rows} == {"pooled", "block1"}
    assert all(set(row) == set(f7._BLOCK_SCHEMA) for row in block_rows)
    block1 = [row for row in block_rows if row["block"] == "block1"][0]
    assert block1["n_entries"] == 1 and block1["n_adds"] == 1
    assert block1["released_notional"] == pytest.approx(UNIT, abs=TOL)
    assert block1["executed_notional"] == pytest.approx(
        _rows(events, f7.ROW_KIND_ALLOCATION)[0]["executed_notional"], abs=TOL)
    assert block1["days_n"] == 1


# --------------------------------------------------------------------------- #
# 12. declared same-bar timing
# --------------------------------------------------------------------------- #


def test_same_bar_timing_recycles_only_on_the_checkpoint_bar() -> None:
    cell = _cell(q=1.0, timing=f7.TIMING_SAME_BAR)
    assert cell.timing_suffix == "_timing_same_bar"
    assert cell.run_id.endswith("_timing_same_bar")
    policy = cell.build_policy()
    rec = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, 570, 10.0, False)])

    # release decision at ET579 -> the EXIT executes on the ET580 checkpoint bar itself
    on_bar = _bars(DAY, {"AAA": _release_tape(ETS, 576, 3), "BBB": _tape(ETS)})
    strat = _simulate(cell, policy, DAY, on_bar, rec)
    events = f7.resolve_cell_ledger(cell, policy)
    release = _by_ticker(_rows(events, f7.ROW_KIND_RELEASE), "AAA")
    assert release["release_et"] == CHECKPOINT
    assert release["status"] == f7.STATUS_RECYCLED
    allocation = _by_ticker(_rows(events, f7.ROW_KIND_ALLOCATION), "BBB")
    assert allocation["executed"] is True and allocation["execution_et"] == 581
    assert allocation["decision_et"] == CHECKPOINT

    # a release that executes off a checkpoint bar is never silently recycled later
    policy_off = cell.build_policy()
    off_bar = _bars(DAY, {"AAA": _release_tape(ETS, 571, 3), "BBB": _tape(ETS)})
    _simulate(cell, policy_off, DAY, off_bar, rec)
    events = f7.resolve_cell_ledger(cell, policy_off)
    release = _by_ticker(_rows(events, f7.ROW_KIND_RELEASE), "AAA")
    assert release["release_et"] == 575 and release["plan_id"] is None
    assert release["status"] == f7.STATUS_UNRECYCLED_SAME_BAR_MISS
    assert release["retained_notional"] == pytest.approx(UNIT, abs=TOL)
    assert _rows(events, f7.ROW_KIND_ALLOCATION) == []
    assert policy_off.allocations == []


# --------------------------------------------------------------------------- #
# 13. CLI
# --------------------------------------------------------------------------- #


def test_cli_matches_the_other_family_scripts(monkeypatch, tmp_path: Path) -> None:
    calls: dict = {}

    def fake_run_cells(out_root, *, days, workers, cell_workers, triggers, timing):
        calls.update({"out_root": out_root, "days": days, "workers": workers,
                      "cell_workers": cell_workers, "triggers": triggers,
                      "timing": timing})
        return []

    monkeypatch.setattr(f7, "run_cells", fake_run_cells)
    assert f7.main(["--max-days", "3", "--out-root", str(tmp_path), "--workers", "1",
                    "--cell-workers", "2"]) == 0
    assert calls["days"] == sim.dev_days()[:3]
    assert calls["workers"] == 1 and calls["cell_workers"] == 2
    assert calls["out_root"] == tmp_path
    assert calls["triggers"] is None and calls["timing"] == f7.REGISTERED_TIMING

    assert f7.main(["--max-days", "2", "--trigger", "R3_g40,R3_g50"]) == 0
    assert calls["triggers"] == ("R3_g40", "R3_g50")
    assert calls["out_root"] == f7.OUT_ROOT / "subset"

    assert f7.main(["--max-days", "2", "--timing", f7.TIMING_SAME_BAR]) == 0
    assert calls["timing"] == f7.TIMING_SAME_BAR
    assert calls["out_root"] == f7.OUT_ROOT / "smoke"

    for bad in (["--full", "--max-days", "2"], [], ["--max-days", "0"],
                ["--max-days", "2", "--trigger", "nope"], ["--max-days", "2",
                                                           "--cell-workers", "0"]):
        with pytest.raises(SystemExit):
            f7.main(bad)


def test_run_config_records_the_contract_engine_and_grid(tmp_path: Path) -> None:
    f7._write_run_config(tmp_path, [DAY, "2025-02-03"], ("R3_g40",), f7.REGISTERED_TIMING)
    config = json.loads((tmp_path / "run_config.json").read_text())
    assert config["contract_version"] == sim.CONTRACT_VERSION
    assert config["engine_hash"] == sim._engine_hash()
    assert config["sim_contract_hash"] == sim._contract_hash()
    assert config["registered_grid"]["trigger_subset"] == ["R3_g40"]
    assert config["registered_grid"]["logical_cells"] == 8
    assert config["registered_grid"]["unique_simulations"] == 8
    assert config["registered_grid"]["recycle_fraction_pct"] == [0, 50, 100]
    assert config["extension"]["id"] == f7.EXTENSION_ID
    assert config["blocks"]["block1"]["days_n"] == 1
    assert config["blocks"]["block2"]["days_n"] == 1
    assert config["no_validation_claim"] is True
