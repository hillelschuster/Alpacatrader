"""Focused tests for F6 staged reserve capital and the EXT-1 batch checkpoint hook.

These tests drive the real engine (``simulate_day``) on synthetic single-day fixtures and
the real reconciliation path (``basket_f6.resolve_cell_ledger``), so they exercise the
policy, the engine call site and the ledger together.  No canonical artifact is read or
written and no full-span simulation is run.
"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from factory.scripts import basket_f6 as f6
from factory.scripts import basket_sim as sim

DAY = "2021-02-01"
DAY0 = "2021-01-29"
DAY2 = "2021-02-02"
SESSION_END = 590
CHECKPOINT = 585
ETS = list(range(570, SESSION_END + 1))       # 570..590, includes the ET585 checkpoint
HOLD = list(range(570, SESSION_END))          # 570..589: forced flat cannot execute
TOL = 1e-12

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


def _flat(day: str, tickers: list[str], ets=ETS, px: float = 10.0,
          overrides: dict | None = None) -> sim.Bars:
    """A flat tape; ``overrides[(ticker, et)] = (open, high, low, close)``."""
    overrides = overrides or {}
    series = {ticker: [(et, *overrides.get((ticker, et), (px, px, px, px))) for et in ets]
              for ticker in tickers}
    return _bars(day, series)


def _tape(ets) -> list[tuple]:
    """A flat 10.00 tape over the given minutes."""
    return [(et, 10.0, 10.0, 10.0, 10.0) for et in ets]


def _rec(day: str, pop: str, T: int, names: list[tuple]) -> dict:
    """Minimal anatomy record: ``names = [(ticker, rank, fill_et, fill_px, blocked)]``."""
    return {"date": day, "snapshots": [{"pop": pop, "T": T, "names": [
        {"ticker": ticker, "rank": rank, "sel": True,
         "fill": None if blocked else {"et": fill_et, "px": fill_px, "gap_min": 0.0,
                                       "blocked": False}}
        for ticker, rank, fill_et, fill_px, blocked in names]}]}


def _pm_rec(day: str, names: list[tuple]) -> dict:
    return _rec(day, "A_pm", 570, names)


def _cell(entry: str = "A_pm", n: int = 2, p: float = 0.5, reserve_policy: str = "equal",
          checkpoint: int = CHECKPOINT, bps: int = 100) -> f6.Cell:
    pop, entry_T = {eid: (pop, T) for eid, pop, T in f6.ENTRY_SPECS}[entry]
    return f6.Cell(entry, pop, entry_T, n, p, reserve_policy, checkpoint, bps)


def _simulate(cell: f6.Cell, policy, day: str, bars: sim.Bars, rec: dict, *,
              session_end: int = SESSION_END, do_entries: bool = True, carry=None,
              release=None, seed=None) -> sim.Strategy:
    """Run one synthetic day.  ``policy is NO_POLICY`` declares no batch policy."""
    if policy is NO_POLICY:
        spec = sim.StrategySpec(family_id="F6test", entry_pop=cell.entry_pop,
                                entry_T=cell.entry_T, top_n=cell.n, n_slots=cell.n,
                                reserve_frac=cell.p, release=release or [sim.R0()],
                                scale_in=[], name=cell.run_id)
    else:
        spec = cell.strategy(policy)
        if release is not None:
            spec.release = release
    strat = sim.Strategy(spec)
    if seed is not None:
        seed(strat)
    sim.simulate_day(strat, day, rec, bars, session_end, float(cell.bps),
                     do_entries=do_entries, carry=carry)
    return strat


def _tickets(strat: sim.Strategy) -> dict[str, sim.Ticket]:
    """Every ticket the strategy ever created, open or closed."""
    return {tk.ticker: tk for tk in list(strat.closed) + list(strat.open_tickets.values())}


def _scheduled(events: list[dict]) -> list[dict]:
    return [row for row in events if row.get("alloc_id") is not None]


def _by_ticker(rows: list[dict], ticker: str) -> dict:
    found = [row for row in rows if row["ticker"] == ticker]
    assert len(found) == 1, f"expected exactly one row for {ticker}, got {len(found)}"
    return found[0]


# --------------------------------------------------------------------------- #
# 1. hook inertness
# --------------------------------------------------------------------------- #


def test_hook_is_inert_when_no_policy_is_declared() -> None:
    assert "batch_policy" in sim.StrategySpec.__dataclass_fields__
    assert sim.StrategySpec().batch_policy is None

    rec = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, 570, 10.0, False)])
    bars = _flat(DAY, ["AAA", "BBB"])
    cell = _cell(n=2, p=0.5)

    plain = _simulate(cell, NO_POLICY, DAY, bars, rec)
    row_plain = dict(plain.day_rows[-1])

    # a declared policy that emits nothing must be indistinguishable from no policy
    quiet = f6.CashReservePolicy(CHECKPOINT, 0.5, 2)
    declared = _simulate(cell, quiet, DAY, bars, rec)
    row_declared = dict(declared.day_rows[-1])

    assert row_plain == row_declared
    assert ({tk.ticker: (tk.net(), tk.n_adds) for tk in _tickets(plain).values()}
            == {tk.ticker: (tk.net(), tk.n_adds) for tk in _tickets(declared).values()})
    assert len(quiet.sleeves) == 1
    assert quiet.sleeves[0]["sleeve_status"] == f6.STATUS_POLICY_CASH
    assert quiet.events == []
    assert all(tk.n_adds == 0 for tk in _tickets(declared).values())


def test_policy_is_called_only_on_its_declared_checkpoint() -> None:
    class Spy(f6.CashReservePolicy):
        def __init__(self, checkpoint_et: int):
            super().__init__(checkpoint_et, 0.5, 2)
            self.calls: list[int] = []

        def plan(self, ctx):
            self.calls.append(int(ctx.et))
            return super().plan(ctx)

    rec = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, 570, 10.0, False)])
    bars = _flat(DAY, ["AAA", "BBB"])
    cell = _cell(n=2, p=0.5)

    late = Spy(615)                      # never reached: the tape ends at ET590
    _simulate(cell, late, DAY, bars, rec)
    assert late.calls == []
    assert late.sleeves == []

    at_checkpoint = Spy(CHECKPOINT)
    _simulate(cell, at_checkpoint, DAY, bars, rec)
    assert at_checkpoint.calls == [CHECKPOINT]


def test_engine_rejects_a_checkpoint_outside_the_declared_golden_window() -> None:
    class OffWindow(f6.CashReservePolicy):
        def __init__(self):
            super().__init__(CHECKPOINT, 0.5, 2)
            self.checkpoint_ets = (587,)

    with pytest.raises(ValueError):
        sim.Strategy(_cell(n=2, p=0.5).strategy(OffWindow()))


# --------------------------------------------------------------------------- #
# 2. exact allocation
# --------------------------------------------------------------------------- #


def test_equal_policy_splits_the_reserve_exactly_across_survivors() -> None:
    cell = _cell(n=3, p=0.67)
    policy = f6.EqualReservePolicy(CHECKPOINT, 0.67, 3)
    rec = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, 570, 10.0, False),
                        ("CCC", 2, 570, 10.0, False)])
    bars = _flat(DAY, ["AAA", "BBB", "CCC"], overrides={
        ("AAA", 586): (11.0, 11.0, 11.0, 11.0),
        ("BBB", 586): (12.0, 12.0, 12.0, 12.0),
        ("CCC", 586): (13.0, 13.0, 13.0, 13.0),
    })
    strat = _simulate(cell, policy, DAY, bars, rec)
    events, sleeves = f6.resolve_cell_ledger(cell, policy, [DAY])

    reserve = 1.0 - 0.67
    share = reserve / 3
    unit = 0.67 / 3
    rows = _scheduled(events)
    assert len(rows) == 3
    for row in rows:
        assert row["decision_day"] == DAY and row["decision_et"] == CHECKPOINT
        assert row["reserve_share"] == pytest.approx(share, abs=TOL)
        assert row["scheduled_notional"] == pytest.approx(share, abs=TOL)
        assert row["cap_headroom"] == pytest.approx(unit, abs=TOL)
        assert row["cap_limited_notional"] == pytest.approx(0.0, abs=TOL)
        assert row["status"] == f6.STATUS_EXECUTED
        assert row["executed"] is True
        assert row["execution_et"] == 586                  # next bar, never same-bar
        assert row["execution_px"] == pytest.approx(
            {"AAA": 11.0, "BBB": 12.0, "CCC": 13.0}[row["ticker"]])
        assert row["executed_notional"] == pytest.approx(share, abs=TOL)
        assert row["tranche_cash_in"] == pytest.approx(share, abs=TOL)
        assert row["tranche_marked_net"] == pytest.approx(0.0, abs=TOL)   # flat forced
        assert row["tranche_terminal_value"] == pytest.approx(0.0, abs=TOL)
        assert row["terminal_basis"] == "exit"
        assert row["terminal_kind"] is None
        assert row["terminal_reason"] == "FORCED_FLAT"
        assert row["terminal_et"] == SESSION_END
        assert row["incremental_net_pnl"] == pytest.approx(row["tranche_net_pnl"], abs=TOL)
        assert row["control_run_id"] == cell.cash_run_id

    sleeve = sleeves[0]
    assert sleeve["sleeve_status"] == f6.STATUS_ALLOCATED
    assert sleeve["eligible_n"] == 3 and sleeve["excluded_n"] == 0
    assert sleeve["reserve_notional"] == pytest.approx(reserve, abs=TOL)
    assert sleeve["requested_notional"] == pytest.approx(reserve, abs=TOL)
    assert sleeve["executed_notional"] == pytest.approx(reserve, abs=TOL)
    assert sleeve["retained_reserve_cash"] == pytest.approx(0.0, abs=TOL)
    assert sleeve["reserve_share"] == pytest.approx(share, abs=TOL)
    assert sleeve["carried_n"] == 0

    tickets = _tickets(strat)
    for ticker in ("AAA", "BBB", "CCC"):
        assert tickets[ticker].n_adds == 1
        assert tickets[ticker].add_notional == pytest.approx(share, abs=TOL)
    assert (sum(tk.add_notional for tk in tickets.values())
            == pytest.approx(reserve, abs=TOL))


# --------------------------------------------------------------------------- #
# 3. blocked-slot cash is not reserve
# --------------------------------------------------------------------------- #


def test_blocked_slot_cash_is_never_used_as_reserve() -> None:
    cell = _cell(n=3, p=0.67)
    policy = f6.EqualReservePolicy(CHECKPOINT, 0.67, 3)
    rec = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, None, None, True),
                        ("CCC", 2, 570, 10.0, False)])
    bars = _flat(DAY, ["AAA", "CCC"], ets=HOLD)          # exits cannot execute today
    strat = _simulate(cell, policy, DAY, bars, rec)
    events, sleeves = f6.resolve_cell_ledger(cell, policy, [DAY])

    unit = 0.67 / 3
    reserve = 1.0 - 0.67
    share = reserve / 2
    sleeve = sleeves[0]
    assert sleeve["tickets_n"] == 2
    assert sleeve["slots_without_ticket"] == 1
    assert sleeve["blocked_slot_cash"] == pytest.approx(unit, abs=TOL)
    assert sleeve["sleeve_cash"] == pytest.approx(1.0 - 2 * unit, abs=TOL)
    assert sleeve["non_reserve_cash"] == pytest.approx(unit, abs=TOL)
    assert sleeve["reserve_notional"] == pytest.approx(reserve, abs=TOL)

    # exactly the reserve is requested/executed: the blocked slot's budget is untouched
    assert sleeve["requested_notional"] == pytest.approx(reserve, abs=TOL)
    assert sleeve["executed_notional"] == pytest.approx(reserve, abs=TOL)
    assert sleeve["cap_limited_notional"] == pytest.approx(0.0, abs=TOL)
    assert strat._cash(DAY) == pytest.approx(unit, abs=TOL)
    assert (sleeve["executed_notional"] + sleeve["non_reserve_cash"]
            == pytest.approx(sleeve["sleeve_cash"], abs=TOL))
    assert len(_scheduled(events)) == 2
    assert all(row["reserve_share"] == pytest.approx(share, abs=TOL)
               for row in _scheduled(events))
    assert all(row["executed_notional"] == pytest.approx(share, abs=TOL)
               for row in _scheduled(events))
    assert all(tk.n_adds == 1 for tk in _tickets(strat).values())


# --------------------------------------------------------------------------- #
# 4. entry-family timing
# --------------------------------------------------------------------------- #


def test_b585_reserve_deploys_no_earlier_than_et586() -> None:
    cell = _cell(entry="B585", n=2, p=0.5)
    policy = f6.EqualReservePolicy(CHECKPOINT, 0.5, 2)
    rec = _rec(DAY, "B", 585, [("AAA", 0, 585, 10.0, False), ("BBB", 1, 585, 10.0, False)])
    bars = _flat(DAY, ["AAA", "BBB"], ets=HOLD,
                 overrides={("AAA", 586): (11.0, 11.0, 11.0, 11.0)})
    strat = _simulate(cell, policy, DAY, bars, rec)
    events, _ = f6.resolve_cell_ledger(cell, policy, [DAY])

    row = _by_ticker(_scheduled(events), "AAA")
    assert row["decision_et"] == CHECKPOINT
    assert row["execution_et"] == 586
    assert row["execution_px"] == pytest.approx(11.0)      # the 586 open, not the 585 bar
    assert row["carried"] is False
    adds = [act for act in _tickets(strat)["AAA"].actions if act["action"] == "ADD"]
    assert [act["et"] for act in adds] == [586]


def test_b600_reserve_deploys_no_earlier_than_et601() -> None:
    cell = _cell(entry="B600", n=2, p=0.5, checkpoint=600)
    policy = f6.EqualReservePolicy(600, 0.5, 2)
    rec = _rec(DAY, "B", 600, [("AAA", 0, 600, 10.0, False), ("BBB", 1, 600, 10.0, False)])
    bars = _flat(DAY, ["AAA", "BBB"], ets=list(range(570, 605)),
                 overrides={("AAA", 601): (12.0, 12.0, 12.0, 12.0)})
    strat = _simulate(cell, policy, DAY, bars, rec, session_end=605)
    events, _ = f6.resolve_cell_ledger(cell, policy, [DAY])

    row = _by_ticker(_scheduled(events), "AAA")
    assert row["decision_et"] == 600
    assert row["execution_et"] == 601
    assert row["execution_px"] == pytest.approx(12.0)
    adds = [act for act in _tickets(strat)["AAA"].actions if act["action"] == "ADD"]
    assert [act["et"] for act in adds] == [601]
    assert row["execution_et"] > row["decision_et"]


def test_b600_at_585_cannot_allocate_and_matches_cash() -> None:
    cell = _cell(entry="B600", n=2, p=0.5)
    policy = f6.EqualReservePolicy(CHECKPOINT, 0.5, 2)
    rec = _rec(DAY, "B", 600, [("AAA", 0, 600, 10.0, False), ("BBB", 1, 600, 10.0, False)])
    bars = _flat(DAY, ["AAA", "BBB"], ets=list(range(570, 605)))

    strat = _simulate(cell, policy, DAY, bars, rec, session_end=605)
    events, sleeves = f6.resolve_cell_ledger(cell, policy, [DAY])

    assert _scheduled(events) == []
    assert all(row["status"] == f6.STATUS_EXCLUDED for row in events)
    assert all(row["exclusion_reason"] == "pending_action" for row in events)
    assert all(row["pending_action"] == "ENTER" for row in events)
    assert sleeves[0]["sleeve_status"] == f6.STATUS_NO_SURVIVOR
    assert sleeves[0]["eligible_n"] == 0
    assert sleeves[0]["requested_notional"] == pytest.approx(0.0, abs=TOL)
    assert sleeves[0]["retained_reserve_cash"] == pytest.approx(0.5, abs=TOL)

    cash = _simulate(cell, f6.CashReservePolicy(CHECKPOINT, 0.5, 2), DAY, bars, rec,
                     session_end=605)
    assert strat.day_rows[-1] == cash.day_rows[-1]
    assert ({tk.ticker: (tk.net(), tk.n_adds) for tk in _tickets(strat).values()}
            == {tk.ticker: (tk.net(), tk.n_adds) for tk in _tickets(cash).values()})

    # the grid declares this as an alias of the same-p cash control
    assert cell.is_alias is True
    assert cell.run_id == cell.cash_run_id
    assert "ET585" in cell.alias_reason


# --------------------------------------------------------------------------- #
# 5. exclusions and honest non-execution
# --------------------------------------------------------------------------- #


def test_pending_action_on_the_checkpoint_bar_excludes_the_ticket() -> None:
    cell = _cell(n=2, p=0.67)
    policy = f6.EqualReservePolicy(CHECKPOINT, 0.67, 2)
    rec = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, 570, 10.0, False)])
    bars = _flat(DAY, ["AAA", "BBB"],
                 overrides={("AAA", CHECKPOINT): (10.0, 10.0, 8.9, 9.5)})
    strat = _simulate(cell, policy, DAY, bars, rec, release=[sim.R1(-10)])
    events, sleeves = f6.resolve_cell_ledger(cell, policy, [DAY])

    excluded = _by_ticker([row for row in events
                           if row["status"] == f6.STATUS_EXCLUDED], "AAA")
    assert excluded["exclusion_reason"] == "pending_action"
    assert excluded["pending_action"] == "EXIT"
    assert excluded["scheduled_notional"] == pytest.approx(0.0, abs=TOL)
    assert excluded["executed"] is False
    assert excluded["terminal_reason"] == "R1(-10)"

    # the split denominator is the eligible set only: BBB receives the whole reserve
    row = _by_ticker(_scheduled(events), "BBB")
    assert row["reserve_share"] == pytest.approx(0.33, abs=TOL)
    assert row["executed_notional"] == pytest.approx(0.33, abs=TOL)
    sleeve = sleeves[0]
    assert sleeve["eligible_n"] == 1
    assert sleeve["pending_excluded_n"] == 1 and sleeve["halted_excluded_n"] == 0
    assert sleeve["executed_notional"] == pytest.approx(0.33, abs=TOL)
    tickets = _tickets(strat)
    assert tickets["AAA"].n_adds == 0
    assert tickets["BBB"].n_adds == 1


def test_halted_at_checkpoint_is_excluded_and_cap_truncation_leaves_cash() -> None:
    cell = _cell(n=3, p=0.33)
    policy = f6.EqualReservePolicy(CHECKPOINT, 0.33, 3)
    rec = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, 570, 10.0, False),
                        ("CCC", 2, 570, 10.0, False)])
    bars = _bars(DAY, {"AAA": _tape(HOLD), "BBB": _tape(range(570, 581)),
                       "CCC": _tape(range(570, 581))})
    strat = _simulate(cell, policy, DAY, bars, rec)
    events, sleeves = f6.resolve_cell_ledger(cell, policy, [DAY])

    halted = [row for row in events if row["status"] == f6.STATUS_EXCLUDED]
    assert {row["ticker"] for row in halted} == {"BBB", "CCC"}
    assert all(row["exclusion_reason"] == "halted_at_checkpoint" for row in halted)

    unit = 0.33 / 3
    reserve = 1.0 - 0.33
    row = _by_ticker(_scheduled(events), "AAA")
    assert row["reserve_share"] == pytest.approx(reserve, abs=TOL)
    assert row["cap_headroom"] == pytest.approx(unit, abs=TOL)
    assert row["status"] == f6.STATUS_CAP_LIMITED
    assert row["scheduled_notional"] == pytest.approx(unit, abs=TOL)
    assert row["executed_notional"] == pytest.approx(unit, abs=TOL)
    assert row["cap_limited_notional"] == pytest.approx(reserve - unit, abs=TOL)
    assert row["retained_cash"] == pytest.approx(reserve - unit, abs=TOL)
    assert row["executed"] is True

    sleeve = sleeves[0]
    assert sleeve["eligible_n"] == 1 and sleeve["halted_excluded_n"] == 2
    assert sleeve["reserve_notional"] == pytest.approx(reserve, abs=TOL)
    assert sleeve["requested_notional"] == pytest.approx(unit, abs=TOL)
    assert sleeve["executed_notional"] == pytest.approx(unit, abs=TOL)
    assert sleeve["retained_reserve_cash"] == pytest.approx(reserve - unit, abs=TOL)
    # the cap-limited remainder really is still sleeve cash
    assert strat._cash(DAY) == pytest.approx(reserve - unit, abs=TOL)
    assert _tickets(strat)["AAA"].add_notional == pytest.approx(unit, abs=TOL)


def test_unexecuted_intent_is_reported_and_never_assumed() -> None:
    cell = _cell(n=2, p=0.5)
    policy = f6.EqualReservePolicy(CHECKPOINT, 0.5, 2)
    rec = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, 570, 10.0, False)])
    # AAA's last bar is the checkpoint bar: its ADD can only carry into the next session
    bars = _bars(DAY, {"AAA": _tape(range(570, 586)), "BBB": _tape(HOLD)})
    strat = _simulate(cell, policy, DAY, bars, rec)
    events, sleeves = f6.resolve_cell_ledger(cell, policy, [DAY])

    row = _by_ticker(_scheduled(events), "AAA")
    assert row["executed"] is False
    assert row["status"] == f6.STATUS_UNEXECUTED_OPEN
    assert row["execution_day"] is None and row["execution_et"] is None
    assert row["executed_notional"] == pytest.approx(0.0, abs=TOL)
    assert row["tranche_net_pnl"] is None
    assert row["retained_cash"] == pytest.approx(0.25, abs=TOL)

    ticket = _tickets(strat)["AAA"]
    assert ticket.open is True and ticket.n_adds == 0
    assert ticket.pending is not None and ticket.pending["action"] == "ADD"
    assert ticket.pending["carry"] is True
    assert sleeves[0]["retained_reserve_cash"] == pytest.approx(0.25, abs=TOL)
    assert _by_ticker(_scheduled(events), "BBB")["executed"] is True


# --------------------------------------------------------------------------- #
# 6. p = 1 identity with the primitive baseline
# --------------------------------------------------------------------------- #


def test_p1_cell_is_the_primitive_baseline_and_declares_no_policy() -> None:
    cell = _cell(n=2, p=1.00, reserve_policy="cash", checkpoint=585)
    spec = cell.strategy()
    baseline = sim.StrategySpec(family_id="F1", entry_pop="A_pm", entry_T=570, top_n=2,
                                n_slots=2, reserve_frac=1.0, release=[sim.R0()],
                                scale_in=[], name="A_pm_N2_R0_bps100")
    assert spec.batch_policy is None
    for field in ("entry_pop", "entry_T", "top_n", "n_slots", "reserve_frac"):
        assert getattr(spec, field) == getattr(baseline, field)
    assert [rule.name for rule in spec.release] == [rule.name for rule in baseline.release]
    assert spec.scale_in == [] and baseline.scale_in == []

    rec = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, 570, 10.0, False)])
    bars = _flat(DAY, ["AAA", "BBB"])
    from_f6 = _simulate(cell, None, DAY, bars, rec)
    from_baseline = sim.Strategy(baseline)
    sim.simulate_day(from_baseline, DAY, rec, bars, SESSION_END, 100.0)

    assert from_f6.day_rows[-1] == from_baseline.day_rows[-1]
    assert ({tk.ticker: (tk.net(), tk.n_adds, tk.unit_notional)
             for tk in _tickets(from_f6).values()}
            == {tk.ticker: (tk.net(), tk.n_adds, tk.unit_notional)
                for tk in _tickets(from_baseline).values()})

    # every declared p=1 row (any policy, any checkpoint) collapses onto that same run
    collapsed = []
    for reserve_policy in f6.RESERVE_POLICIES:
        for checkpoint in f6.CHECKPOINT_GRID:
            declared = _cell(n=2, p=1.00, reserve_policy=reserve_policy,
                             checkpoint=checkpoint)
            assert declared.run_id == cell.run_id
            assert declared.strategy().batch_policy is None
            collapsed.append(declared)
    assert len(collapsed) == 4
    assert [declared.is_alias for declared in collapsed] == [False, True, True, True]
    assert all(declared.alias_reason for declared in collapsed if declared.is_alias)


# --------------------------------------------------------------------------- #
# 7. sleeve-scoped ticket identity
# --------------------------------------------------------------------------- #


def test_two_sleeves_holding_the_same_ticker_stay_independent() -> None:
    cell = _cell(n=2, p=0.5)
    policy = f6.EqualReservePolicy(CHECKPOINT, 0.5, 2)
    rec = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, 570, 10.0, False)])
    bars = _flat(DAY, ["AAA", "BBB"], ets=HOLD)      # exits carry: cash stays observable

    unit = 0.25
    carry = sim.Ticket("AAA", DAY0, 570, 10.0, unit, entry_rank=0, peak=10.0)
    carry.shares = carry.shares_entry = unit / 10.0
    carry.cash_in = unit
    carry.cost_open = unit

    def seed(strat: sim.Strategy) -> None:
        strat.open_tickets[(DAY0, "AAA")] = carry
        strat.sleeve_cash[DAY0] = 1.0 - unit
        strat.deployed[DAY0] = unit

    strat = _simulate(cell, policy, DAY, bars, rec, seed=seed)
    events, sleeves = f6.resolve_cell_ledger(cell, policy, [DAY])

    # the checkpoint is sleeve-local: the carried ticket is never a candidate
    assert {row["sleeve_day"] for row in events} == {DAY}
    assert {row["sleeve_day"] for row in sleeves} == {DAY}
    assert carry.n_adds == 0 and carry.add_notional == pytest.approx(0.0, abs=TOL)

    tickets = _tickets(strat)
    fresh = tickets["AAA"]
    assert fresh is not carry
    assert fresh.sleeve_day == DAY and fresh.n_adds == 1
    assert fresh.unit_notional == pytest.approx(unit, abs=TOL)
    # the reserve ADD was funded from the DAY sleeve only
    assert strat._cash(DAY) == pytest.approx(0.0, abs=TOL)
    assert strat._cash(DAY0) == pytest.approx(1.0 - unit + carry.proceeds, abs=TOL)
    assert {row["ticker"] for row in _scheduled(events)} == {"AAA", "BBB"}


# --------------------------------------------------------------------------- #
# 8. fail-closed carry behaviour
# --------------------------------------------------------------------------- #


def _carry_ticket(day: str) -> sim.Ticket:
    tk = sim.Ticket("AAA", day, 570, 10.0, 0.25, entry_rank=0, peak=10.0)
    tk.shares = tk.shares_entry = 0.25 / 10.0
    tk.cash_in = tk.cost_open = 0.25
    tk.pending = {"action": "EXIT", "reason": "R1(-10)", "after_et": 589,
                  "decision_day": day, "level": 9.0, "frac": None, "carry": True}
    return tk


def _carry_fixture(tmp_path: Path, days: dict) -> sim.CarrySubstrate:
    root = tmp_path / "carry_bars"
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps(
        {"contract": sim.CONTRACT_VERSION, "days": days}))
    return sim.CarrySubstrate(root=root, require_production=False)


def _seed_carry(strat: sim.Strategy) -> None:
    strat.open_tickets[(DAY0, "AAA")] = _carry_ticket(DAY0)
    strat.sleeve_cash[DAY0] = 0.75
    strat.deployed[DAY0] = 0.25


def test_carry_without_certified_substrate_fails_closed(tmp_path: Path) -> None:
    cell = _cell(n=2, p=0.5)
    rec = _pm_rec(DAY, [("BBB", 0, 570, 10.0, False)])
    bars = _flat(DAY, ["BBB"])            # the carried AAA has no tape today

    with pytest.raises(sim.MissingCarrySubstrate):
        _simulate(cell, NO_POLICY, DAY, bars, rec, carry=None, seed=_seed_carry)

    # an uncertified (day, ticker) is not a "no trade" day either
    uncertified = _carry_fixture(tmp_path, {DAY: {"session_end": SESSION_END, "source": {},
                                                  "tickers": {"ZZZ": {"status": "no_bars"}}}})
    with pytest.raises(sim.MissingCarrySubstrate):
        _simulate(cell, NO_POLICY, DAY, bars, rec, carry=uncertified, seed=_seed_carry)

    # a fixture overlay can never certify a production run
    production = sim.CarrySubstrate(root=tmp_path / "carry_bars")
    assert production.certified(DAY, "ZZZ", SESSION_END) is None
    with pytest.raises(sim.MissingCarrySubstrate):
        production.bars(DAY, "ZZZ", SESSION_END)


def test_certified_no_bars_carry_continues_without_executing(tmp_path: Path) -> None:
    cell = _cell(n=2, p=0.5)
    rec = _pm_rec(DAY, [("BBB", 0, 570, 10.0, False)])
    bars = _flat(DAY, ["BBB"])
    substrate = _carry_fixture(tmp_path, {DAY: {"session_end": SESSION_END, "source": {},
                                                "tickers": {"AAA": {"status": "no_bars"}}}})
    strat = _simulate(cell, NO_POLICY, DAY, bars, rec, carry=substrate, seed=_seed_carry)
    carry = strat.open_tickets[(DAY0, "AAA")]

    assert carry.open is True
    assert carry.n_adds == 0 and carry.n_reduces == 0
    assert carry.exit_reason is None
    assert carry.pending is not None and carry.pending["action"] == "EXIT"
    assert "no_resumption" in carry.flags
    assert strat.n_carries == 1
    assert strat.day_rows[-1]["n_open_end"] == 1


# --------------------------------------------------------------------------- #
# 9. registered surface and CLI
# --------------------------------------------------------------------------- #


def test_registered_grid_collapses_each_unique_behaviour_once() -> None:
    cells = f6.build_cells()
    runs = f6.unique_runs()
    assert len(cells) == 720
    assert len(runs) == 423
    assert len({cell.declared_id for cell in cells}) == 720
    assert sum(1 for cell in cells if cell.is_alias) == 297

    assert {cell.entry_id for cell in cells} == {"A_pm", "A_pm31", "A_open", "B585", "B600"}
    assert {cell.n for cell in cells} == {2, 3, 4}
    assert {cell.p for cell in cells} == {1.0, 0.67, 0.5, 0.33}
    assert {cell.checkpoint for cell in cells} == {585, 600}
    assert {cell.reserve_policy for cell in cells} == {"cash", "equal"}
    assert {cell.bps for cell in cells} == {0, 100, 150}

    for cell in cells:
        assert cell.run_id in runs
        assert runs[cell.run_id].run_id == cell.run_id
        assert runs[cell.run_id].canonical is True
        if cell.is_alias:
            assert cell.alias_reason
        else:
            assert cell.alias_reason is None
        assert cell.strategy().reserve_frac == cell.p
        assert (cell.strategy().batch_policy is None) == (cell.p >= 1.0)

    assert len({cell.run_id for cell in cells if cell.p == 1.0}) == 45
    assert len({cell.run_id for cell in cells
                if cell.p < 1.0 and cell.reserve_policy == "cash"}) == 135
    assert len({cell.run_id for cell in cells if cell.deploys_reserve}) == 243

    b600_585 = [cell for cell in cells if cell.entry_id == "B600" and cell.p < 1.0
                and cell.reserve_policy == "equal" and cell.checkpoint == 585]
    assert len(b600_585) == 27
    assert all(cell.is_alias and cell.run_id == cell.cash_run_id for cell in b600_585)

    grid = f6.registered_grid()
    assert grid["logical_cells"] == 720 and grid["unique_simulations"] == 423
    assert f6.dev_blocks(["2021-02-01", "2023-12-29", "2025-02-03", "2026-05-29"]) == {
        "pooled": ["2021-02-01", "2023-12-29", "2025-02-03", "2026-05-29"],
        "block1": ["2021-02-01", "2023-12-29"],
        "block2": ["2025-02-03", "2026-05-29"],
    }


def test_reserve_add_that_cannot_fill_in_session_executes_next_session() -> None:
    """A reserve ADD is an ordinary pending: it carries and fills at the next session's
    first bar, and the ledger records the carried execution honestly."""
    cell = _cell(n=2, p=0.5)
    policy = f6.EqualReservePolicy(CHECKPOINT, 0.5, 2)
    rec1 = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, 570, 10.0, False)])
    # AAA's day-1 tape ends on the checkpoint bar, so the ADD cannot fill that session
    bars1 = _bars(DAY, {"AAA": _tape(range(570, 586)), "BBB": _tape(HOLD)})
    spec = cell.strategy(policy)
    strat = sim.Strategy(spec)
    sim.simulate_day(strat, DAY, rec1, bars1, SESSION_END, float(cell.bps))
    day1 = [row for row in policy.events if row.get("reason") is not None
            and row["ticker"] == "AAA"]
    assert len(day1) == 1 and strat.open_tickets[(DAY, "AAA")].pending is not None

    # day 2: both carries resolve from the candidate tape; first bar is the 09:30 open
    bars2 = _flat(DAY2, ["AAA", "BBB"], ets=ETS,
                  overrides={("AAA", 570): (11.0, 11.0, 11.0, 11.0),
                             ("BBB", 570): (12.0, 12.0, 12.0, 12.0)})
    rec2 = _rec(DAY2, "A_pm", 570, [])
    sim.simulate_day(strat, DAY2, rec2, bars2, SESSION_END, float(cell.bps),
                     do_entries=False)
    events, sleeves = f6.resolve_cell_ledger(cell, policy, [DAY, DAY2])

    row = _by_ticker(_scheduled(events), "AAA")
    assert row["executed"] is True
    assert row["decision_day"] == DAY and row["decision_et"] == CHECKPOINT
    assert row["execution_day"] == DAY2 and row["execution_et"] == ETS[0]
    assert row["execution_px"] == pytest.approx(11.0)
    assert row["carried"] is True
    assert row["executed_notional"] == pytest.approx(0.25, abs=TOL)
    assert _by_ticker(_scheduled(events), "BBB")["carried"] is False
    assert _by_ticker(_scheduled(events), "BBB")["execution_day"] == DAY
    assert sleeves[0]["carried_n"] == 1
    assert [sleeve["sleeve_day"] for sleeve in sleeves] == [DAY, DAY2]
    assert all(tk.n_adds == 1 for tk in _tickets(strat).values())


def test_data_boundary_terminal_mark_is_split_across_live_tranches() -> None:
    """A terminal mark (not a trade) must be attributed to the executed tranche."""
    cell = _cell(n=2, p=0.5)
    policy = f6.EqualReservePolicy(CHECKPOINT, 0.5, 2)
    rec = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, 570, 10.0, False)])
    bars = _flat(DAY, ["AAA", "BBB"], ets=HOLD)
    strat = _simulate(cell, policy, DAY, bars, rec)

    # the engine's own data-boundary terminalization (no EXIT action, mark -> terminal)
    sim.terminalize(strat, "DATA_END_MARK")
    events, sleeves = f6.resolve_cell_ledger(cell, policy, [DAY])

    tickets = _tickets(strat)
    for ticker in ("AAA", "BBB"):
        assert tickets[ticker].open is False
        assert tickets[ticker].terminal_kind == "DATA_END_MARK"
        assert tickets[ticker].exit_reason == "DATA_END_MARK"
        row = _by_ticker(_scheduled(events), ticker)
        assert row["status"] == f6.STATUS_EXECUTED
        assert row["terminal_basis"] == "terminal_mark"
        assert row["terminal_kind"] == "DATA_END_MARK"
        assert row["terminal_reason"] == "DATA_END_MARK"
        assert row["terminal_day"] is None and row["terminal_et"] is None
        assert row["tranche_marked_net"] == pytest.approx(0.0, abs=TOL)
        assert row["tranche_terminal_value"] > 0.0
        assert row["tranche_net_pnl"] == pytest.approx(
            row["tranche_realized_net"] + row["tranche_terminal_value"]
            - row["tranche_cash_in"], abs=1e-9)
        assert row["terminal_value"] == pytest.approx(tickets[ticker].terminal_value,
                                                      abs=TOL)
        # the entry tranche plus the reserve tranche is the ticket's whole net P&L
        entry = f6.replay_tranches(tickets[ticker], cell.bps)["ENTRY"]
        assert (entry["net"] + row["tranche_net_pnl"]
                == pytest.approx(tickets[ticker].net(), abs=1e-9))
    assert sleeves[0]["executed_notional"] == pytest.approx(0.5, abs=TOL)


def test_no_reserve_cell_has_no_reserve_ledger() -> None:
    """p=1.0 declares no policy and therefore has no reserve ledger at all."""
    cell = _cell(n=2, p=1.00, reserve_policy="cash")
    policy = cell.build_policy()
    assert policy is None
    assert cell.strategy().batch_policy is None
    assert f6.resolve_cell_ledger(cell, policy, [DAY]) == ([], [])


def test_surface_and_aggregate_builders_cover_their_schemas() -> None:
    """The surface/aggregate builders must consume the ledger rows they are handed."""
    cell = _cell(n=2, p=0.67)
    policy = f6.EqualReservePolicy(CHECKPOINT, 0.67, 2)
    rec = _pm_rec(DAY, [("AAA", 0, 570, 10.0, False), ("BBB", 1, 570, 10.0, False)])
    bars = _flat(DAY, ["AAA", "BBB"], ets=HOLD)
    strat = _simulate(cell, policy, DAY, bars, rec)
    events, sleeves = f6.resolve_cell_ledger(cell, policy, [DAY])
    assert all(set(row) >= set(f6._EVENT_SCHEMA) for row in events)
    assert all(set(row) >= set(f6._SLEEVE_SCHEMA) for row in sleeves)

    metrics = sim.compute_metrics(
        strat.day_rows, list(strat.closed) + list(strat.open_tickets.values()), 1,
        strat.n_blocked_slots, strat.n_pending, strat.n_carries, draws=50)
    surface = f6._surface_row(cell, "ran", metrics, float(strat.day_rows[-1]["pnl"]), None,
                              events, sleeves, days=[DAY], reused=False)
    assert set(surface) >= set(f6._POOLED_SCHEMA) - {"n", "alias"}
    assert set(f6._pooled_from_surface(surface)) == set(f6._POOLED_SCHEMA)
    assert surface["reserve_requested_notional"] == pytest.approx(0.33, abs=TOL)
    assert surface["reserve_executed_notional"] == pytest.approx(0.33, abs=TOL)
    assert surface["events_n"] == len(events)

    daily = pl.DataFrame({
        "date": [DAY], "r_day": [0.01], "pnl": [0.01], "deployed_end": [0.9],
        "deployed_avg": [0.8], "n_open_end": [2], "n_actions": [2],
    })
    day_rows = f6._aggregate_day_rows(cell, daily, None, [DAY])
    assert len(day_rows) == 1 and set(day_rows[0]) == set(f6._DAY_SCHEMA)
    assert day_rows[0]["incremental_pnl"] is None

    tickets = pl.DataFrame({"sleeve_day": ["2021-02-01"], "open_end": [True],
                            "exit_day": [None], "n_adds": [1]})
    block_rows = f6._aggregate_block_rows(cell, daily, tickets, events, sleeves, None, [DAY])
    assert len(block_rows) == 1 and set(block_rows[0]) == set(f6._BLOCK_SCHEMA)
    assert block_rows[0]["block"] == "block1"
    assert block_rows[0]["n_entries"] == 1 and block_rows[0]["n_adds"] == 1
    assert block_rows[0]["reserve_executed_notional"] == pytest.approx(0.33, abs=TOL)


def test_cli_matches_the_other_family_scripts(monkeypatch, tmp_path: Path) -> None:
    calls: dict = {}

    def fake_run_cells(out_root, *, days, workers):
        calls.update({"out_root": out_root, "days": days, "workers": workers})
        return []

    monkeypatch.setattr(f6, "run_cells", fake_run_cells)
    assert f6.main(["--max-days", "3", "--out-root", str(tmp_path), "--workers", "1"]) == 0
    assert calls["days"] == sim.dev_days()[:3]
    assert calls["workers"] == 1
    assert calls["out_root"] == tmp_path

    with pytest.raises(SystemExit):
        f6.main(["--full", "--max-days", "2"])
    with pytest.raises(SystemExit):
        f6.main([])
    with pytest.raises(SystemExit):
        f6.main(["--max-days", "0"])
