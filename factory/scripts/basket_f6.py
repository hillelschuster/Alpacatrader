#!/usr/bin/env python
"""F6 staged reserve capital: one batch checkpoint per sleeve; development evidence only.

Economic question
-----------------
A basket sleeve normally commits its whole ``C0`` at entry.  F6 instead commits only
``p * C0`` at entry and parks ``(1 - p) * C0`` as **sleeve cash**.  At one declared
golden-window checkpoint (ET585 or ET600) the sleeve decides what to do with that reserve:

* ``cash``  — never deploy it (the same-``p`` control: identical entries, smaller size);
* ``equal`` — split the reserve equally across the sleeve's *eligible open originals* and
  request one normal ``ADD`` per recipient, executed at each ticket's next eligible bar
  open (never same-bar).

``p = 1.0`` holds no reserve, so the policy and the checkpoint are inert and the cell is
exactly the primitive baseline (``R0`` hold-to-forced-flat, equal slots, no scale-in).

Mechanism, causality and capital rules
-------------------------------------
* Engine hook: ``EXT-1 BATCH-CAPITAL-CHECKPOINT-2026-09-24`` in
  ``factory/BASKET-SIM-EXTENSIONS.md`` (``StrategySpec.batch_policy`` / ``plan(ctx)``).
  The hook fires once per completed checkpoint bar, after all ticket decisions for that
  bar; intents are scheduled as ordinary §7-step-0 pending actions.
* A reserve tranche is an ``ADD`` and therefore respects the existing engine cap
  (total ADD notional per ticket <= 1.0 x unit notional) and the cash/no-leverage
  invariants.  Sizing and truncation are decided here; the engine still owns funding.
* Reserve cash is **sleeve-local** and is never commingled with blocked-slot /
  unfilled-slot cash: the policy never requests more than ``(1 - p) * C0`` per sleeve, and
  every requested-but-unexecuted notional is reported as retained cash.
* Eligible at the checkpoint = open, same sleeve, no pending action, and a completed bar
  at the checkpoint.  Pending-action and halted-at-checkpoint tickets are excluded and
  recorded (``halted_pending_excluded``).
* Nothing is assumed to fill: intents that are cap-limited, unfunded, lost to a dev-block
  terminal mark, or still pending when the data ends are reported with their own status.

Alias collapse (registered surface runs each unique behaviour exactly once)
-------------------------------------------------------------------------
720 logical rows collapse to 423 unique simulations:

* ``p = 1.0``      -> one run per (entry, N, bps); policy and checkpoint are inert;
* ``cash`` policy  -> one run per (entry, N, p, bps); the checkpoint is inert;
* ``B600`` at ET585 with the ``equal`` policy -> the same-``p`` ``cash`` run: B600 entries
  fill at the first open with ``et >= 600``, so no B600 ticket is live on the completed
  ET585 bar and the checkpoint can only ever report ``no_survivor``.

Every logical row is retained in ``surface.json`` with ``alias_of`` / ``alias_reason``.

Outputs (``<out-root>/F6/<run_id>/``)
------------------------------------
the standard simulator outputs (``config.json``, ``daily.parquet``, ``tickets.parquet``,
``metrics.json``, ``run_summary.json``, month parts), plus
``reserve_events.parquet`` (executed-tranche table: allocation ids, decision/execution
day+et, scheduled/executed notional, terminal exit or open-end mark, realized/marked split,
incremental net P&L vs the same-``p`` cash control, statuses), ``reserve_sleeves.parquet``
(per-sleeve reserve ledger) and ``f6_cell.json`` (cell parameters + reconciliation).

At the out-root: ``run_config.json`` (provenance: extension-file hash, contract version,
registered grid), ``surface.json`` (all 720 declared rows; alias rows carry ``alias_of`` /
``alias_reason`` and repeat their canonical run's numbers), ``pooled.parquet`` (the same
720 rows in tabular form, ``alias`` flagged), ``blocks.parquet`` (canonical run x block),
``days.parquet`` (canonical run x day), ``reserve_events.parquet`` and
``reserve_sleeves.parquet`` (pooled ledgers).

Ledger semantics
----------------
* ``status`` is one of ``executed``, ``cap_limited`` (the ADD cap reduced the request),
  ``unfunded`` (the engine refused the ADD for cash/deployed reasons), ``halted_pending_excluded``,
  ``no_entries``, ``no_survivor``, ``checkpoint_bar_absent``, ``policy_cash``,
  ``underlying_closed`` (the ticket was terminalized at a dev-block/data boundary before the
  ADD could execute) and ``unexecuted_open_at_end`` (the ADD was still pending when the data
  ended).  Nothing is assumed to have filled.
* ``terminal_*`` always describes the underlying ticket (``terminal_basis`` is ``exit``,
  ``terminal_mark`` or ``open_end_mark``; ``terminal_kind``/``terminal_value`` are the
  engine's own fields).  ``tranche_*`` is filled only for executed tranches, so no P&L is
  ever implied for a non-executed intent.
* ``incremental_net_pnl`` on a tranche is that tranche's own replay P&L (realized + mark or
  terminal value - cash in).  The cell-level comparison with the same-``p`` cash control
  lives in ``incremental_total_pnl`` / ``incremental_mean_basket_day``; the two agree to
  float tolerance except when the ADD itself changed the ticket's later path (a pending ADD
  displacing a forced-flat carry), which is reported as ``tranche_delta_residual`` /
  ``tranche_delta_ok`` rather than hidden.
* ``p = 1.0`` holds no reserve: no policy is declared, so no reserve ledger rows exist for
  those runs and ``control_run_id`` is null for cells that deploy nothing.

Contract: ``factory/BASKET-SIM-CONTRACT.md`` (C1, 2026-09-24) and
``researches/PRE-REG-BASKET-02.md`` §3.6.  Development evidence only: no profitability,
selection, or out-of-sample claim is made anywhere in this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

if __package__:
    from factory.scripts import basket_sim as sim
else:
    import basket_sim as sim

ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / "factory" / "artifacts" / "basket" / "phase2" / "F6"
EXTENSIONS_PATH = ROOT / "factory" / "BASKET-SIM-EXTENSIONS.md"
EXTENSION_ID = "EXT-1 BATCH-CAPITAL-CHECKPOINT-2026-09-24"

ENTRY_SPECS = (
    ("A_pm", "A_pm", 570),
    ("A_pm31", "A_pm31", 570),
    ("A_open", "A_open", 570),
    ("B585", "B", 585),
    ("B600", "B", 600),
)
N_GRID = (2, 3, 4)
P_GRID = (1.00, 0.67, 0.50, 0.33)
CHECKPOINT_GRID = (585, 600)
RESERVE_POLICIES = ("cash", "equal")
BPS_GRID = (0, 100, 150)
BLOCKS = (("block1", "2021-02", "2023-12"), ("block2", "2025-02", "2026-05"))
# The cash policy never deploys, so its checkpoint is inert; keep one declared value so a
# cash cell is still fully described and reproducible.
CASH_CHECKPOINT = CHECKPOINT_GRID[0]
# Checkpoint at which the diagnostic state-conditioned arm is canonical
# (it is not part of the registered F6 grid; see F6_DIAG runs).
DIAGNOSTIC_CHECKPOINT = 600
EPS = 1e-12
REASON_PREFIX = "F6_RESERVE_EQUAL:"
REASON_PREFIX_STATE = "F6_RESERVE_STATE:"
# Diagnostic state arms: fraction of the running MFE a recipient must still
# be holding at the checkpoint (see StateReservePolicy).
STATE_RETENTION = {"state67": 0.67, "state33": 0.33}
DEFAULT_RETENTION = 0.67

STATUS_SCHEDULED = "scheduled"
STATUS_EXECUTED = "executed"
STATUS_CAP_LIMITED = "cap_limited"
STATUS_UNFUNDED = "unfunded"
STATUS_EXCLUDED = "halted_pending_excluded"
STATUS_NO_ENTRIES = "no_entries"
STATUS_NO_SURVIVOR = "no_survivor"
STATUS_UNDERLYING_CLOSED = "underlying_closed"
STATUS_UNEXECUTED_OPEN = "unexecuted_open_at_end"
STATUS_CHECKPOINT_ABSENT = "checkpoint_bar_absent"
STATUS_POLICY_CASH = "policy_cash"
STATUS_ALLOCATED = "allocated"

CORE_OUTPUTS = ("config.json", "daily.parquet", "tickets.parquet", "metrics.json",
                "run_summary.json")
F6_OUTPUTS = ("f6_cell.json", "reserve_events.parquet", "reserve_sleeves.parquet")
REQUIRED_OUTPUTS = CORE_OUTPUTS + F6_OUTPUTS


class F6ContractError(RuntimeError):
    """F6 refused an inconsistent declaration, core output, or reconciliation."""


def _require_extension() -> None:
    """Fail loudly when ``basket_sim`` predates EXT-1."""
    if (not hasattr(sim, "BatchAllocationPolicy")
            or not hasattr(sim, "BatchCheckpointContext")
            or "batch_policy" not in getattr(sim.StrategySpec, "__dataclass_fields__", {})):
        raise F6ContractError(
            "factory/scripts/basket_sim.py does not implement "
            f"{EXTENSION_ID}; see factory/BASKET-SIM-EXTENSIONS.md"
        )


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _atomic_json(path: Path, value: object) -> None:
    _atomic_write(path, json.dumps(value, indent=1, sort_keys=True, allow_nan=False).encode())


def _atomic_parquet(path: Path, rows: list[dict], schema: dict) -> None:
    frame = _frame(rows, schema)
    tmp = path.with_name(path.name + ".tmp")
    frame.write_parquet(tmp)
    os.replace(tmp, path)


def _frame(rows: list[dict], schema: dict) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame({name: pl.Series(name, [], dtype=dtype)
                             for name, dtype in schema.items()})
    return pl.DataFrame({name: [row.get(name) for row in rows] for name in schema}, schema=schema)


# --------------------------------------------------------------------------- #
# Cell grid
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Cell:
    """One declared F6 cell.  ``declared_id`` is the logical row; ``run_id`` is the
    canonical simulation it resolves to (see the alias-collapse rules)."""

    entry_id: str
    entry_pop: str
    entry_T: int
    n: int
    p: float
    reserve_policy: str
    checkpoint: int
    bps: int

    @property
    def pct(self) -> int:
        return int(round(self.p * 100))

    @property
    def declared_id(self) -> str:
        return (f"{self.entry_id}_N{self.n}_p{self.pct:03d}_{self.reserve_policy}"
                f"_T{self.checkpoint}_bps{self.bps}")

    @property
    def base_id(self) -> str:
        return f"{self.entry_id}_N{self.n}_p{self.pct:03d}"

    @property
    def cash_run_id(self) -> str:
        """The same-``p`` cash control (itself, for a cash cell)."""
        return f"{self.base_id}_cash_bps{self.bps}"

    @property
    def equal_run_id(self) -> str:
        return f"{self.base_id}_equal_T{self.checkpoint}_bps{self.bps}"

    @property
    def state_run_id(self) -> str:
        """Diagnostic state-conditioned arm (not part of the registered grid)."""
        return (f"{self.base_id}_{self.reserve_policy}"
                f"_T{self.checkpoint}_bps{self.bps}")

    @property
    def canonical(self) -> bool:
        """True when this declared row *is* the simulation its ``run_id`` names.

        Alias rows keep their full declared identity (policy, checkpoint) in the surface
        and point at the canonical run.
        """
        if self.reserve_policy.startswith("state"):
            # Each checkpoint is a distinct treatment (allocate at 09:45 vs 10:00),
            # so both are canonical runs.
            return self.p < 1.0
        if self.reserve_policy == "equal" and self.p < 1.0 and not self._equal_inapplicable:
            return True
        return self.reserve_policy == "cash" and self.checkpoint == CASH_CHECKPOINT

    @property
    def is_alias(self) -> bool:
        return not self.canonical

    @property
    def alias_reason(self) -> str | None:
        if self.canonical:
            return None
        if self.p >= 1.0:
            return "p=1.0 holds no reserve: reserve policy and checkpoint are inert"
        if self.reserve_policy == "cash":
            return "cash reserve policy never deploys: checkpoint is inert"
        return ("B600 entries fill at the first open with et>=600, so no eligible "
                "survivor exists on the completed ET585 bar: identical to the same-p "
                "cash control")

    @property
    def run_id(self) -> str:
        if self.reserve_policy.startswith("state") and self.p < 1.0:
            return self.state_run_id
        if self.reserve_policy == "equal" and self.p < 1.0 and not self._equal_inapplicable:
            return self.equal_run_id
        return self.cash_run_id

    @property
    def _equal_inapplicable(self) -> bool:
        return self.entry_id == "B600" and self.checkpoint == CASH_CHECKPOINT

    @property
    def control_run_id(self) -> str | None:
        """Same-``p`` cash control run id (None for cells that deploy nothing)."""
        if (self.reserve_policy == "equal" or self.reserve_policy.startswith("state")) \
                and self.p < 1.0 \
                and not self._equal_inapplicable:
            return self.cash_run_id
        return None

    @property
    def deploys_reserve(self) -> bool:
        return ((self.reserve_policy == "equal"
                 or self.reserve_policy.startswith("state"))
                and self.p < 1.0 and not self._equal_inapplicable)

    def build_policy(self):
        """A fresh policy instance for this cell (None for the no-reserve cells)."""
        _require_extension()
        if self.p >= 1.0:
            return None
        if self.reserve_policy.startswith("state"):
            return StateReservePolicy(self.checkpoint, self.p, self.n,
                                      retention=STATE_RETENTION.get(
                                          self.reserve_policy, DEFAULT_RETENTION))
        if self.reserve_policy == "equal":
            return EqualReservePolicy(self.checkpoint, self.p, self.n)
        return CashReservePolicy(self.checkpoint, self.p, self.n)

    def strategy(self, policy=None) -> sim.StrategySpec:
        """The declared cell as a simulator spec.

        ``release = [R0]`` (hold to forced flat) is the primitive baseline module: F6
        varies capital staging only.  ``reserve_frac = p`` is the existing entry-budget
        reduction; the reserve itself is deployed by the batch policy.
        """
        _require_extension()
        pol = self.build_policy() if policy is None else policy
        return sim.StrategySpec(
            family_id="F6",
            entry_pop=self.entry_pop,
            entry_T=self.entry_T,
            top_n=self.n,
            n_slots=self.n,
            reserve_frac=self.p,
            release=[sim.R0()],
            scale_in=[],
            name=self.run_id,
            batch_policy=pol,
        )


def build_cells() -> list[Cell]:
    """All 720 declared (logical) F6 cells."""
    return [
        Cell(entry_id, entry_pop, entry_T, n, p, policy, checkpoint, bps)
        for entry_id, entry_pop, entry_T in ENTRY_SPECS
        for n in N_GRID
        for p in P_GRID
        for policy in RESERVE_POLICIES
        for checkpoint in CHECKPOINT_GRID
        for bps in BPS_GRID
    ]


def unique_runs() -> dict[str, Cell]:
    """``run_id -> canonical cell`` (423 unique simulations)."""
    runs: dict[str, Cell] = {}
    for cell in build_cells():
        if not cell.canonical:
            continue
        if cell.run_id in runs:
            raise F6ContractError(f"duplicate canonical run id {cell.run_id}")
        runs[cell.run_id] = cell
    return runs


def registered_grid() -> dict:
    return {
        "entry_families": [{"entry": eid, "pop": pop, "T": et} for eid, pop, et in ENTRY_SPECS],
        "N": list(N_GRID),
        "p": list(P_GRID),
        "checkpoint_et": list(CHECKPOINT_GRID),
        "reserve_policy": list(RESERVE_POLICIES),
        "bps_round_trip": list(BPS_GRID),
        "release": "R0 (hold to forced flat); F6 varies capital staging only",
        "add_cap": "total ADD notional per ticket <= 1.0 x unit notional (engine)",
        "logical_cells": len(build_cells()),
        "unique_simulations": len(unique_runs()),
        "alias_rules": [
            "p=1.0: policy and checkpoint are inert -> one cash run per (entry, N, bps)",
            "cash policy: checkpoint is inert -> one run per (entry, N, p, bps)",
            "B600 at ET585 (equal): no eligible survivor exists -> the same-p cash run",
        ],
    }


def dev_blocks(days: list[str]) -> dict[str, list[str]]:
    blocks = {"pooled": list(days)}
    for name, lo, hi in BLOCKS:
        blocks[name] = [day for day in days if lo <= day[:7] <= hi]
    return blocks


def block_of(day: str) -> str | None:
    for name, lo, hi in BLOCKS:
        if lo <= day[:7] <= hi:
            return name
    return None


# --------------------------------------------------------------------------- #
# Reserve policies (EXT-1 consumers)
# --------------------------------------------------------------------------- #


class CashReservePolicy(sim.BatchAllocationPolicy):
    """Declared ``cash`` reserve: record the held reserve, never deploy it."""

    name = "F6_reserve_cash"

    def __init__(self, checkpoint_et: int, p: float, n_slots: int):
        self.checkpoint_et = int(checkpoint_et)
        self.checkpoint_ets = (self.checkpoint_et,)
        self.p = float(p)
        self.n_slots = int(n_slots)
        self.reserve_frac = 1.0 - self.p
        self.sleeves: list[dict] = []
        self.events: list[dict] = []

    def plan(self, ctx) -> list:
        self.sleeves.append(_sleeve_row(
            ctx, p=self.p, n_slots=self.n_slots, reserve_frac=self.reserve_frac,
            checkpoint_et=self.checkpoint_et, status=STATUS_POLICY_CASH,
            eligible_n=len(ctx.eligible), excluded=ctx.excluded,
        ))
        return []


class EqualReservePolicy(sim.BatchAllocationPolicy):
    """Split the sleeve reserve equally across the eligible open originals at one
    checkpoint and request one ``ADD`` per recipient."""

    name = "F6_reserve_equal_survivors"

    def __init__(self, checkpoint_et: int, p: float, n_slots: int):
        self.checkpoint_et = int(checkpoint_et)
        self.checkpoint_ets = (self.checkpoint_et,)
        self.p = float(p)
        self.n_slots = int(n_slots)
        self.reserve_frac = 1.0 - self.p
        self.sleeves: list[dict] = []
        self.events: list[dict] = []

    def plan(self, ctx) -> list:
        day = ctx.day
        sleeve_tickets = [tk for tk in ctx.strategy.open_tickets.values()
                          if tk.sleeve_day == day]
        reserve = self.reserve_frac * sim.C0
        eligible = sorted(ctx.eligible, key=lambda tk: (tk.entry_rank, tk.ticker))
        sleeve = _sleeve_row(
            ctx, p=self.p, n_slots=self.n_slots, reserve_frac=self.reserve_frac,
            checkpoint_et=self.checkpoint_et, status=None, eligible_n=len(eligible),
            excluded=ctx.excluded, sleeve_tickets=sleeve_tickets, reserve=reserve,
        )
        if not sleeve_tickets:
            sleeve["sleeve_status"] = STATUS_NO_ENTRIES
        elif not eligible:
            sleeve["sleeve_status"] = STATUS_NO_SURVIVOR
        else:
            sleeve["sleeve_status"] = STATUS_ALLOCATED
        self.sleeves.append(sleeve)

        intents: list = []
        for tk, why in ctx.excluded:
            self.events.append(_ticket_event(
                ctx, tk, status=STATUS_EXCLUDED, exclusion_reason=why,
                reserve_share=None, requested=0.0, cap_headroom=None,
                cap_limited=0.0, retained=0.0,
            ))
        if eligible:
            share = reserve / len(eligible)
            for tk in eligible:
                headroom = max(0.0, float(tk.unit_notional) - float(tk.add_notional))
                requested = min(share, headroom)
                truncated = share - requested
                alloc_id = f"{day}:{tk.ticker}:T{self.checkpoint_et}"
                reason = f"{REASON_PREFIX}{alloc_id}"
                status = STATUS_SCHEDULED if truncated <= EPS else STATUS_CAP_LIMITED
                self.events.append(_ticket_event(
                    ctx, tk, status=status, exclusion_reason=None,
                    reserve_share=share, requested=requested, cap_headroom=headroom,
                    cap_limited=truncated, retained=truncated, alloc_id=alloc_id,
                    reason=reason,
                ))
                if requested > EPS:
                    intents.append(sim.BatchAllocationIntent(
                        sleeve_day=day, ticker=tk.ticker,
                        frac=requested / float(tk.unit_notional), reason=reason,
                        alloc_id=alloc_id,
                    ))
        return intents


class StateReservePolicy(EqualReservePolicy):
    """Diagnostic extension: deploy the reserve only to survivors whose own causal
    path at the checkpoint still shows continuation with intact integrity.

    Two interpretable dimensions, one threshold each (no fitted grid):
    * ``ret_from_fill``  > 0        — the ticket is still holding a gain (energy);
    * ``dd_from_high``  >= -0.05    — it is within 5% of its own running high (integrity).
    Recipients split the reserve equally; if none qualify the reserve stays cash.
    This is the state-conditioned arm of the withholding / redeploy / informed-redeploy
    decomposition; it is not part of the registered F6 grid.
    """

    name = "F6_reserve_state_continuation"

    def __init__(self, checkpoint_et: int, p: float, n_slots: int,
                 retention: float = 0.67):
        super().__init__(checkpoint_et, p, n_slots)
        self.retention = float(retention)

    def _state(self, ctx, tk):
        """(ret_from_fill, dd_from_high) from completed bars up to the checkpoint."""
        day_bars = ctx.bars.ticker(tk.ticker)
        if day_bars is None:
            return None
        ets = day_bars["et"]
        highs = day_bars["high"]
        closes = day_bars["close"]
        first = int(np.searchsorted(ets, int(tk.entry_et), side="left"))
        last = int(np.searchsorted(ets, int(self.checkpoint_et), side="right"))
        window = slice(first, last)
        if last <= first:
            return None
        peak = float(np.max(highs[window]))
        close = float(closes[last - 1])
        if peak <= 0 or tk.entry_px <= 0:
            return None
        ret = close / float(tk.entry_px) - 1.0
        mfe = peak / float(tk.entry_px) - 1.0
        retained = (ret / mfe) if mfe > 0 else 0.0
        return (ret, close / peak - 1.0, retained)

    def plan(self, ctx) -> list:
        day = ctx.day
        sleeve_tickets = [tk for tk in ctx.strategy.open_tickets.values()
                          if tk.sleeve_day == day]
        reserve = self.reserve_frac * sim.C0
        eligible = sorted(ctx.eligible, key=lambda tk: (tk.entry_rank, tk.ticker))
        state = {tk.ticker: self._state(ctx, tk) for tk in eligible}
        recipients = [tk for tk in eligible
                      if state.get(tk.ticker) is not None
                      and state[tk.ticker][0] > 0.0
                      and state[tk.ticker][2] >= self.retention]
        sleeve = _sleeve_row(
            ctx, p=self.p, n_slots=self.n_slots, reserve_frac=self.reserve_frac,
            checkpoint_et=self.checkpoint_et, status=None, eligible_n=len(eligible),
            excluded=ctx.excluded, sleeve_tickets=sleeve_tickets, reserve=reserve,
        )
        sleeve["state_recipients"] = len(recipients)
        sleeve["state_retention"] = self.retention
        sleeve["state_detail"] = {t: None if s is None else [round(s[0], 6), round(s[1], 6),
                                                            round(s[2], 6)]
                                 for t, s in state.items()}
        if not sleeve_tickets:
            sleeve["sleeve_status"] = STATUS_NO_ENTRIES
        elif not eligible:
            sleeve["sleeve_status"] = STATUS_NO_SURVIVOR
        elif not recipients:
            sleeve["sleeve_status"] = "state_no_recipient"
        else:
            sleeve["sleeve_status"] = STATUS_ALLOCATED
        self.sleeves.append(sleeve)

        intents: list = []
        for tk, why in ctx.excluded:
            self.events.append(_ticket_event(
                ctx, tk, status=STATUS_EXCLUDED, exclusion_reason=why,
                reserve_share=None, requested=0.0, cap_headroom=None,
                cap_limited=0.0, retained=0.0,
            ))
        for tk in eligible:
            stats = state.get(tk.ticker)
            if tk not in recipients:
                self.events.append(_ticket_event(
                    ctx, tk, status="state_excluded",
                    exclusion_reason=("state_unresolved" if stats is None else
                                      f"ret={stats[0]:.4f},dd={stats[1]:.4f},"
                                      f"retained={stats[2]:.4f}"),
                    reserve_share=None, requested=0.0, cap_headroom=None,
                    cap_limited=0.0, retained=0.0,
                ))
        if recipients:
            share = reserve / len(recipients)
            for tk in recipients:
                headroom = max(0.0, float(tk.unit_notional) - float(tk.add_notional))
                requested = min(share, headroom)
                truncated = share - requested
                alloc_id = f"{day}:{tk.ticker}:T{self.checkpoint_et}:state"
                reason = f"{REASON_PREFIX_STATE}{alloc_id}"
                status = STATUS_SCHEDULED if truncated <= EPS else STATUS_CAP_LIMITED
                self.events.append(_ticket_event(
                    ctx, tk, status=status, exclusion_reason=None,
                    reserve_share=share, requested=requested, cap_headroom=headroom,
                    cap_limited=truncated, retained=truncated, alloc_id=alloc_id,
                    reason=reason,
                ))
                if requested > EPS:
                    intents.append(sim.BatchAllocationIntent(
                        sleeve_day=day, ticker=tk.ticker,
                        frac=requested / float(tk.unit_notional), reason=reason,
                        alloc_id=alloc_id,
                    ))
        return intents


def _sleeve_row(ctx, *, p, n_slots, reserve_frac, checkpoint_et, status, eligible_n,
                excluded, sleeve_tickets=None, reserve=None) -> dict:
    """Sleeve-level reserve bookkeeping at the checkpoint.

    ``non_reserve_cash`` is the sleeve cash the policy must never touch: the budget of
    slots that never filled (blocked/unfilled) plus any proceeds already booked.  The
    reserve is funded strictly out of ``reserve_notional``.
    """
    day = ctx.day
    tickets = list(sleeve_tickets) if sleeve_tickets is not None else [
        tk for tk in ctx.strategy.open_tickets.values() if tk.sleeve_day == day]
    cash = float(ctx.strategy._cash(day))
    deployed = float(ctx.strategy._deployed(day))
    entry_spent = sum(float(tk.unit_notional) for tk in tickets)
    reserve_notional = float(reserve_frac * sim.C0 if reserve is None else reserve)
    slots_without_ticket = max(0, int(n_slots) - len(tickets))
    blocked_slot_cash = slots_without_ticket * p * sim.C0 / int(n_slots)
    reasons = [why for _, why in excluded]
    return {
        "run_id": ctx.spec.name,
        "declared_id": ctx.spec.name,
        "sleeve_day": day,
        "block": block_of(day),
        "entry": None, "n": int(n_slots), "p": float(p),
        "reserve_policy": None, "checkpoint_et": int(checkpoint_et), "bps": None,
        "sleeve_status": status,
        "reserve_notional": reserve_notional,
        "sleeve_cash": cash,
        "non_reserve_cash": cash - reserve_notional,
        "blocked_slot_cash": blocked_slot_cash,
        "entry_spent": entry_spent,
        "deployed_at_checkpoint": deployed,
        "slots_without_ticket": slots_without_ticket,
        "tickets_n": len(tickets),
        "eligible_n": int(eligible_n),
        "excluded_n": len(excluded),
        "pending_excluded_n": sum(1 for why in reasons if why == "pending_action"),
        "halted_excluded_n": sum(1 for why in reasons if why == "halted_at_checkpoint"),
        "reserve_share": None,
        "requested_notional": 0.0,
        "cap_limited_notional": 0.0,
        "executed_notional": 0.0,
        "retained_reserve_cash": reserve_notional,
        "executed_n": 0, "unexecuted_n": 0, "carried_n": 0,
        "tranche_net_pnl": 0.0,
        "control_run_id": None,
    }


def _ticket_event(ctx, tk, *, status, exclusion_reason, reserve_share, requested,
                  cap_headroom, cap_limited, retained, alloc_id=None, reason=None) -> dict:
    return {
        "run_id": ctx.spec.name,
        "declared_id": ctx.spec.name,
        "sleeve_day": ctx.day,
        "block": block_of(ctx.day),
        "ticker": tk.ticker,
        "entry_rank": int(tk.entry_rank),
        "alloc_id": alloc_id,
        "reason": reason,
        "status": status,
        "exclusion_reason": exclusion_reason,
        "pending_action": ((tk.pending or {}).get("action")
                           if exclusion_reason == "pending_action" else None),
        "decision_day": ctx.day,
        "decision_et": int(ctx.et),
        "reserve_share": reserve_share,
        "scheduled_notional": requested,
        "cap_headroom": cap_headroom,
        "cap_limited_notional": cap_limited,
        "retained_cash": retained,
        "executed": False,
        "execution_day": None, "execution_et": None, "execution_px": None,
        "executed_notional": 0.0, "carried": False,
        "terminal_basis": None, "terminal_kind": None, "terminal_day": None,
        "terminal_et": None, "terminal_px": None, "terminal_reason": None,
        "terminal_value": None,
        "tranche_cash_in": None, "tranche_realized_net": None,
        "tranche_marked_net": None, "tranche_terminal_value": None,
        "tranche_net_pnl": None,
        "incremental_net_pnl": None,
        "control_run_id": None,
        "_ticket": tk,
    }


# --------------------------------------------------------------------------- #
# Executed-tranche reconciliation
# --------------------------------------------------------------------------- #


def replay_tranches(tk: sim.Ticket, bps: float) -> dict[str, dict]:
    """Exact per-tranche P&L replay of one ticket's executed actions.

    Every ``ENTER``/``ADD`` opens a tranche; every ``REDUCE``/``EXIT`` distributes its
    proceeds across the live tranches pro rata by share count, which is exactly how the
    engine's share arithmetic treats fungible shares.  A tranche's net is
    ``realized + mark - cash_in``; the tranches of one ticket sum to ``Ticket.net()``.
    """
    side = bps / 2.0 / 10000.0
    tranches: list[dict] = []
    for index, act in enumerate(tk.actions):
        if act["action"] == "ENTER":
            tranches.append({"reason": act["reason"], "index": index,
                             "shares": float(act["shares"]),
                             "cash_in": float(tk.unit_notional), "realized": 0.0,
                             "day": act["day"], "et": int(act["et"]), "px": float(act["px"])})
        elif act["action"] == "ADD":
            notional = float(act["shares"]) * float(act["px"]) * (1.0 + side)
            tranches.append({"reason": act["reason"], "index": index,
                             "shares": float(act["shares"]), "cash_in": notional,
                             "realized": 0.0, "day": act["day"], "et": int(act["et"]),
                             "px": float(act["px"])})
    for index, act in enumerate(tk.actions):
        if act["action"] not in ("REDUCE", "EXIT"):
            continue
        shares = float(act["shares"])
        proceeds = shares * float(act["px"]) * (1.0 - side)
        live = [t for t in tranches if t["index"] < index and t["shares"] > 0.0]
        total = sum(t["shares"] for t in live)
        if total <= 0.0:
            continue
        for tranche in live:
            fraction = tranche["shares"] / total
            tranche["realized"] += proceeds * fraction
            tranche["shares"] -= shares * fraction
    out: dict[str, dict] = {}
    # A data-boundary / data-end terminal mark is recognized by Ticket.net() but is not a
    # trade (no EXIT action, shares and mark are zeroed by the engine).  It is distributed
    # across the tranches still live at terminalization, exactly like a final mark.
    terminal_total = float(getattr(tk, "terminal_value", 0.0) or 0.0)
    live_total = sum(tranche["shares"] for tranche in tranches)
    for tranche in tranches:
        marked = 0.0
        terminal = 0.0
        if tk.open and float(tk.shares) > 0.0 and tranche["shares"] > 0.0:
            marked = float(tk.mark) * (tranche["shares"] / float(tk.shares))
        elif terminal_total > 0.0 and live_total > 0.0 and tranche["shares"] > 0.0:
            terminal = terminal_total * (tranche["shares"] / live_total)
        out[tranche["reason"]] = {
            "cash_in": tranche["cash_in"],
            "realized_net": tranche["realized"],
            "marked_net": marked,
            "terminal_value": terminal,
            "net": tranche["realized"] + marked + terminal - tranche["cash_in"],
            "shares": tranche["shares"],
            "execution_day": tranche["day"],
            "execution_et": tranche["et"],
            "execution_px": tranche["px"],
        }
    return out


def _terminal_fields(tk: sim.Ticket) -> dict:
    """The underlying ticket's terminal state, in the engine's own vocabulary.

    ``terminal_basis`` is ``exit`` (a real executed EXIT), ``terminal_mark`` (a
    data-boundary/data-end mark booked as ``Ticket.terminal_value``, not a trade) or
    ``open_end_mark`` (still open when the run ended).
    """
    if tk.terminal_kind is not None:
        basis = "terminal_mark"
    elif tk.open:
        basis = "open_end_mark"
    else:
        basis = "exit"
    return {"terminal_basis": basis,
            "terminal_kind": tk.terminal_kind,
            "terminal_day": tk.exit_day,
            "terminal_et": (int(tk.exit_et) if tk.exit_et is not None else None),
            "terminal_px": (float(tk.exit_px) if tk.exit_px is not None
                            else (float(tk.last_close) if tk.last_close is not None
                                  else float(tk.entry_px))),
            "terminal_reason": tk.exit_reason,
            "terminal_value": float(getattr(tk, "terminal_value", 0.0) or 0.0)}


def resolve_cell_ledger(cell: Cell, policy, days: list[str]) -> tuple[list[dict], list[dict]]:
    """Fill every recorded intent with its executed/terminal outcome and P&L.

    Runs after ``sim.run`` in the same process, against the live ``Ticket`` objects the
    policy saw.  Unexecuted intents are classified from the engine's own evidence
    (``add_unfunded`` / ``add_cap_exceeded`` flags, ticket closure, still-pending), never
    guessed.  A cell with no declared policy (``p = 1.0``: no reserve exists) has no
    reserve ledger, so both tables are empty.
    """
    if policy is None:
        return [], []
    events: list[dict] = []
    for event in policy.events:
        row = {key: value for key, value in event.items() if key != "_ticket"}
        tk = event["_ticket"]
        # `terminal_*` always describes the underlying ticket; `tranche_*` is filled only
        # when this allocation actually executed, so no P&L is ever implied for a
        # non-executed intent.
        row.update({
            "run_id": cell.run_id, "declared_id": cell.declared_id, "entry": cell.entry_id,
            "n": cell.n, "p": cell.p, "reserve_policy": cell.reserve_policy,
            "checkpoint_et": cell.checkpoint, "bps": cell.bps,
            "control_run_id": cell.control_run_id,
        })
        row.update(_terminal_fields(tk))
        reason = event.get("reason")
        if reason is not None:
            replay = replay_tranches(tk, cell.bps)
            tranche = replay.get(reason)
            if tranche is None:
                flags = set(tk.flags)
                if "add_unfunded" in flags:
                    row["status"] = STATUS_UNFUNDED
                elif "add_cap_exceeded" in flags:
                    row["status"] = STATUS_CAP_LIMITED
                else:
                    row["status"] = (STATUS_UNDERLYING_CLOSED if not tk.open
                                     else STATUS_UNEXECUTED_OPEN)
                row["retained_cash"] = float(event["retained_cash"]) + float(
                    event["scheduled_notional"])
            else:
                drift = sum(value["net"] for value in replay.values()) - tk.net()
                if abs(drift) > 1e-9 * max(1.0, abs(tk.net())):
                    raise F6ContractError(
                        f"tranche replay does not reconcile for {tk.sleeve_day}/{tk.ticker}: "
                        f"{drift:.3e}")
                row["status"] = (STATUS_CAP_LIMITED
                                 if event["status"] == STATUS_CAP_LIMITED else STATUS_EXECUTED)
                row["executed"] = True
                row["execution_day"] = tranche["execution_day"]
                row["execution_et"] = int(tranche["execution_et"])
                row["execution_px"] = float(tranche["execution_px"])
                row["executed_notional"] = float(tranche["cash_in"])
                row["carried"] = bool(tranche["execution_day"] != event["decision_day"])
                row["tranche_cash_in"] = float(tranche["cash_in"])
                row["tranche_realized_net"] = float(tranche["realized_net"])
                row["tranche_marked_net"] = float(tranche["marked_net"])
                row["tranche_terminal_value"] = float(tranche["terminal_value"])
                row["tranche_net_pnl"] = float(tranche["net"])
                row["incremental_net_pnl"] = float(tranche["net"])
        events.append(row)

    sleeves = [dict(row) for row in policy.sleeves]
    recorded = {row["sleeve_day"] for row in sleeves}
    for day in days:
        if day in recorded:
            continue
        sleeves.append({
            "run_id": cell.run_id, "declared_id": cell.declared_id, "sleeve_day": day,
            "block": block_of(day), "entry": cell.entry_id, "n": cell.n, "p": cell.p,
            "reserve_policy": cell.reserve_policy, "checkpoint_et": cell.checkpoint,
            "bps": cell.bps, "sleeve_status": STATUS_CHECKPOINT_ABSENT,
            "reserve_notional": (1.0 - cell.p) * sim.C0,
            "sleeve_cash": None, "non_reserve_cash": None, "blocked_slot_cash": None,
            "entry_spent": None, "deployed_at_checkpoint": None,
            "slots_without_ticket": None, "tickets_n": None, "eligible_n": None,
            "excluded_n": None, "pending_excluded_n": None, "halted_excluded_n": None,
            "reserve_share": None, "requested_notional": 0.0,
            "cap_limited_notional": 0.0, "executed_notional": 0.0,
            "retained_reserve_cash": (1.0 - cell.p) * sim.C0,
            "executed_n": 0, "unexecuted_n": 0, "carried_n": 0,
            "tranche_net_pnl": 0.0, "control_run_id": cell.control_run_id,
        })

    by_sleeve: dict[str, list[dict]] = {}
    for row in events:
        by_sleeve.setdefault(row["sleeve_day"], []).append(row)
    for row in sleeves:
        rows = by_sleeve.get(row["sleeve_day"], [])
        row["entry"] = cell.entry_id
        row["n"] = cell.n
        row["p"] = cell.p
        row["reserve_policy"] = cell.reserve_policy
        row["checkpoint_et"] = cell.checkpoint
        row["bps"] = cell.bps
        row["control_run_id"] = cell.control_run_id
        row["requested_notional"] = sum(float(r["scheduled_notional"] or 0.0) for r in rows)
        row["cap_limited_notional"] = sum(float(r["cap_limited_notional"] or 0.0) for r in rows)
        row["executed_notional"] = sum(float(r["executed_notional"] or 0.0) for r in rows)
        row["executed_n"] = sum(1 for r in rows if r["executed"])
        row["unexecuted_n"] = sum(1 for r in rows if r.get("reason") and not r["executed"])
        row["carried_n"] = sum(1 for r in rows if r["executed"] and r["carried"])
        row["tranche_net_pnl"] = sum(float(r["tranche_net_pnl"] or 0.0) for r in rows)
        row["retained_reserve_cash"] = float(row["reserve_notional"] or 0.0) - \
            row["executed_notional"]
        if rows and row["sleeve_status"] == STATUS_ALLOCATED:
            row["reserve_share"] = max(float(r["reserve_share"] or 0.0) for r in rows)
    return events, sleeves


# --------------------------------------------------------------------------- #
# Output schemas
# --------------------------------------------------------------------------- #

_EVENT_SCHEMA = {
    "run_id": pl.Utf8, "declared_id": pl.Utf8, "entry": pl.Utf8, "n": pl.Int64,
    "p": pl.Float64, "reserve_policy": pl.Utf8, "checkpoint_et": pl.Int64, "bps": pl.Int64,
    "block": pl.Utf8, "sleeve_day": pl.Utf8, "ticker": pl.Utf8, "entry_rank": pl.Int64,
    "alloc_id": pl.Utf8, "status": pl.Utf8, "exclusion_reason": pl.Utf8,
    "pending_action": pl.Utf8, "decision_day": pl.Utf8, "decision_et": pl.Int64,
    "reserve_share": pl.Float64, "scheduled_notional": pl.Float64,
    "cap_headroom": pl.Float64, "cap_limited_notional": pl.Float64,
    "retained_cash": pl.Float64, "executed": pl.Boolean, "execution_day": pl.Utf8,
    "execution_et": pl.Int64, "execution_px": pl.Float64, "executed_notional": pl.Float64,
    "carried": pl.Boolean, "terminal_basis": pl.Utf8, "terminal_kind": pl.Utf8,
    "terminal_day": pl.Utf8, "terminal_et": pl.Int64, "terminal_px": pl.Float64,
    "terminal_reason": pl.Utf8, "terminal_value": pl.Float64,
    "tranche_cash_in": pl.Float64, "tranche_realized_net": pl.Float64,
    "tranche_marked_net": pl.Float64, "tranche_terminal_value": pl.Float64,
    "tranche_net_pnl": pl.Float64,
    "incremental_net_pnl": pl.Float64, "control_run_id": pl.Utf8,
}

_SLEEVE_SCHEMA = {
    "run_id": pl.Utf8, "declared_id": pl.Utf8, "entry": pl.Utf8, "n": pl.Int64,
    "p": pl.Float64, "reserve_policy": pl.Utf8, "checkpoint_et": pl.Int64, "bps": pl.Int64,
    "block": pl.Utf8, "sleeve_day": pl.Utf8, "sleeve_status": pl.Utf8,
    "reserve_notional": pl.Float64, "sleeve_cash": pl.Float64,
    "non_reserve_cash": pl.Float64, "blocked_slot_cash": pl.Float64,
    "entry_spent": pl.Float64, "deployed_at_checkpoint": pl.Float64,
    "slots_without_ticket": pl.Int64, "tickets_n": pl.Int64, "eligible_n": pl.Int64,
    "excluded_n": pl.Int64, "pending_excluded_n": pl.Int64, "halted_excluded_n": pl.Int64,
    "reserve_share": pl.Float64, "requested_notional": pl.Float64,
    "cap_limited_notional": pl.Float64, "executed_notional": pl.Float64,
    "retained_reserve_cash": pl.Float64, "executed_n": pl.Int64, "unexecuted_n": pl.Int64,
    "carried_n": pl.Int64, "tranche_net_pnl": pl.Float64, "control_run_id": pl.Utf8,
}

_DAY_SCHEMA = {
    "run_id": pl.Utf8, "declared_id": pl.Utf8, "entry": pl.Utf8, "n": pl.Int64,
    "p": pl.Float64, "reserve_policy": pl.Utf8, "checkpoint_et": pl.Int64, "bps": pl.Int64,
    "block": pl.Utf8, "date": pl.Utf8, "r_day": pl.Float64, "pnl": pl.Float64,
    "deployed_end": pl.Float64, "deployed_avg": pl.Float64, "n_open_end": pl.Int64,
    "n_actions": pl.Int64, "control_run_id": pl.Utf8, "control_pnl": pl.Float64,
    "incremental_pnl": pl.Float64,
}

_BLOCK_SCHEMA = {
    "run_id": pl.Utf8, "declared_id": pl.Utf8, "entry": pl.Utf8, "n": pl.Int64,
    "p": pl.Float64, "reserve_policy": pl.Utf8, "checkpoint_et": pl.Int64, "bps": pl.Int64,
    "block": pl.Utf8, "days_n": pl.Int64, "mean_basket_day": pl.Float64,
    "median_basket_day": pl.Float64, "std_basket_day": pl.Float64,
    "worst_day": pl.Float64, "positive_day_share": pl.Float64,
    "total_pnl": pl.Float64, "control_total_pnl": pl.Float64,
    "incremental_total_pnl": pl.Float64, "incremental_mean_basket_day": pl.Float64,
    "mean_deployed_end": pl.Float64, "n_entries": pl.Int64, "n_adds": pl.Int64,
    "n_exits": pl.Int64, "reserve_executed_notional": pl.Float64,
    "reserve_retained_notional": pl.Float64, "events_n": pl.Int64, "executed_n": pl.Int64,
    "control_run_id": pl.Utf8,
}

_POOLED_SCHEMA = {
    "run_id": pl.Utf8, "declared_id": pl.Utf8, "entry": pl.Utf8, "n": pl.Int64,
    "p": pl.Float64, "reserve_policy": pl.Utf8, "checkpoint_et": pl.Int64, "bps": pl.Int64,
    "days_n": pl.Int64, "mean_basket_day": pl.Float64, "median_basket_day": pl.Float64,
    "std_basket_day": pl.Float64, "worst_day": pl.Float64, "worst_week": pl.Float64,
    "worst_month": pl.Float64, "positive_day_share": pl.Float64,
    "compounded_growth": pl.Float64, "compounded_max_dd": pl.Float64,
    "avg_deployed_capital": pl.Float64, "turnover_per_day": pl.Float64,
    "n_entries": pl.Int64, "n_adds": pl.Int64, "n_reduces": pl.Int64, "n_exits": pl.Int64,
    "n_blocked_slots": pl.Int64, "n_pending": pl.Int64, "n_carries": pl.Int64,
    "total_pnl": pl.Float64, "control_run_id": pl.Utf8, "control_total_pnl": pl.Float64,
    "incremental_total_pnl": pl.Float64, "incremental_mean_basket_day": pl.Float64,
    "reserve_notional": pl.Float64, "reserve_requested_notional": pl.Float64,
    "reserve_executed_notional": pl.Float64, "reserve_retained_notional": pl.Float64,
    "tranche_net_total": pl.Float64, "tranche_delta_residual": pl.Float64,
    "tranche_delta_ok": pl.Boolean, "events_n": pl.Int64, "executed_n": pl.Int64,
    "cap_limited_n": pl.Int64, "unfunded_n": pl.Int64, "excluded_n": pl.Int64,
    "no_survivor_n": pl.Int64, "no_entries_n": pl.Int64,
    "underlying_closed_n": pl.Int64, "unexecuted_open_n": pl.Int64,
    "carried_n": pl.Int64, "checkpoint_absent_n": pl.Int64,
    "alias": pl.Boolean, "alias_of": pl.Utf8, "alias_reason": pl.Utf8,
}


# --------------------------------------------------------------------------- #
# Per-cell execution
# --------------------------------------------------------------------------- #


def is_complete(run_dir: Path) -> bool:
    return all((run_dir / name).is_file() for name in REQUIRED_OUTPUTS)


def _load_cell_ledger(run_dir: Path) -> tuple[list[dict], list[dict]]:
    return (pl.read_parquet(run_dir / "reserve_events.parquet").to_dicts(),
            pl.read_parquet(run_dir / "reserve_sleeves.parquet").to_dicts())


def execute_cell(cell: Cell, out_root: Path, days: list[str], workers: int) -> dict:
    """Run one canonical cell (or reuse its complete outputs).

    Returns the evidence needed to build the surface row: metrics, the run's pooled P&L,
    and the resolved reserve ledger.  The ledger is resolved in-process against the live
    ``Ticket`` objects the policy saw, so executed-tranche P&L is exact; the durable
    per-cell tables are written here.
    """
    run_dir = out_root / "F6" / cell.run_id
    if is_complete(run_dir):
        cell_row = json.loads((run_dir / "f6_cell.json").read_text())
        events, sleeves = _load_cell_ledger(run_dir)
        return {"run_id": cell.run_id, "status": "reused", "reused": True,
                "metrics": cell_row["metrics"],
                "total_pnl": _daily_total(run_dir / "daily.parquet"),
                "events": events, "sleeves": sleeves}
    if run_dir.exists():
        shutil.rmtree(run_dir)
    policy = cell.build_policy()
    spec = cell.strategy(policy)
    summary = sim.run(sim.RunConfig("F6", cell.run_id, spec, float(cell.bps),
                                    out_root=out_root, days=days, workers=workers),
                      progress=False)
    events, sleeves = resolve_cell_ledger(cell, policy, days)
    _atomic_parquet(run_dir / "reserve_events.parquet", events, _EVENT_SCHEMA)
    _atomic_parquet(run_dir / "reserve_sleeves.parquet", sleeves, _SLEEVE_SCHEMA)
    cell_row = {
        "family_id": "F6", "run_id": cell.run_id, "declared_id": cell.declared_id,
        "entry": cell.entry_id, "entry_pop": cell.entry_pop, "entry_T": cell.entry_T,
        "N": cell.n, "p": cell.p, "reserve_policy": cell.reserve_policy,
        "checkpoint_et": cell.checkpoint, "bps_round_trip": cell.bps,
        "release": "R0", "add_cap_unit_notional": 1.0,
        "status": "ran", "days": [days[0], days[-1]] if days else [], "n_days": len(days),
        "metrics": summary["metrics"], "control_run_id": cell.control_run_id,
        "extension": EXTENSION_ID, "extension_sha256": _file_sha256(EXTENSIONS_PATH),
        "sim_contract_hash": sim._contract_hash(),
        "contract_version": sim.CONTRACT_VERSION,
    }
    _atomic_json(run_dir / "f6_cell.json", cell_row)
    return {"run_id": cell.run_id, "status": "ran", "reused": False,
            "metrics": summary["metrics"],
            "total_pnl": _daily_total(run_dir / "daily.parquet"),
            "events": events, "sleeves": sleeves}


def _daily_total(path: Path) -> float:
    frame = pl.read_parquet(path, columns=["pnl"])
    return float(frame["pnl"].sum())


def _resolve_control(cell: Cell, totals: dict[str, float], out_root: Path,
                     days: list[str]) -> dict | None:
    """Pooled P&L of the same-``p`` cash control (None when the cell deploys nothing)."""
    control_id = cell.control_run_id
    if control_id is None:
        return None
    total = totals.get(control_id)
    if total is None:
        path = out_root / "F6" / control_id / "daily.parquet"
        if not path.is_file():
            return None
        if pl.read_parquet(path, columns=["date"]).height != len(days):
            raise F6ContractError(f"control run {control_id} does not cover {len(days)} days")
        total = _daily_total(path)
    return {"run_id": control_id, "total_pnl": total, "days_n": len(days)}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _event_counts(events: list[dict], sleeves: list[dict]) -> dict:
    def count(status: str) -> int:
        return sum(1 for row in events if row["status"] == status)

    return {
        "events_n": len(events),
        "executed_n": sum(1 for row in events if row["executed"]),
        "cap_limited_n": count(STATUS_CAP_LIMITED),
        "unfunded_n": count(STATUS_UNFUNDED),
        "excluded_n": count(STATUS_EXCLUDED),
        "no_survivor_n": count(STATUS_NO_SURVIVOR),
        "no_entries_n": count(STATUS_NO_ENTRIES),
        "underlying_closed_n": count(STATUS_UNDERLYING_CLOSED),
        "unexecuted_open_n": count(STATUS_UNEXECUTED_OPEN),
        "carried_n": sum(1 for row in events if row["executed"] and row["carried"]),
        "checkpoint_absent_n": sum(1 for row in sleeves
                                   if row["sleeve_status"] == STATUS_CHECKPOINT_ABSENT),
    }


def _surface_row(cell: Cell, status: str, metrics: dict, total_pnl: float,
                 control: dict | None, events: list[dict], sleeves: list[dict],
                 *, days: list[str], reused: bool) -> dict:
    """One surface row for a declared cell (canonical run + alias resolution).

    ``incremental_*`` compare the cell with its same-``p`` cash control on the identical
    day set.  ``tranche_net_total`` is the sum of the executed tranches' own replay P&L;
    ``tranche_delta_residual`` is the (usually zero) gap between that and the cell-level
    control delta.  A non-zero residual means the ADD itself changed the ticket's later
    path (e.g. a pending ADD displaced a forced-flat carry), which is reported rather
    than hidden.
    """
    counts = _event_counts(events, sleeves)
    reserve_requested = sum(float(row["requested_notional"] or 0.0) for row in sleeves)
    reserve_executed = sum(float(row["executed_notional"] or 0.0) for row in sleeves)
    reserve_retained = sum(float(row["retained_reserve_cash"] or 0.0) for row in sleeves)
    reserve_notional = sum(float(row["reserve_notional"] or 0.0) for row in sleeves)
    tranche_total = sum(float(row["tranche_net_pnl"] or 0.0) for row in events)
    incremental_total = None
    residual = None
    delta_ok = None
    if control is not None:
        incremental_total = total_pnl - float(control["total_pnl"])
        residual = tranche_total - incremental_total
        delta_ok = abs(residual) <= 1e-9 * max(1.0, abs(incremental_total))
    return {
        "declared_id": cell.declared_id,
        "run_id": cell.run_id,
        "alias_of": cell.run_id if cell.is_alias else None,
        "alias_reason": cell.alias_reason,
        "entry": cell.entry_id, "entry_pop": cell.entry_pop, "entry_T": cell.entry_T,
        "N": cell.n, "p": cell.p, "reserve_policy": cell.reserve_policy,
        "checkpoint_et": cell.checkpoint, "bps": cell.bps,
        "status": status, "reused": reused,
        "days_n": metrics["days_n"],
        "mean_basket_day": metrics["mean_basket_day"],
        "median_basket_day": metrics["median_basket_day"],
        "std_basket_day": metrics["std_basket_day"],
        "worst_day": metrics["worst_day"], "worst_week": metrics["worst_week"],
        "worst_month": metrics["worst_month"],
        "positive_day_share": metrics["positive_day_share"],
        "compounded_growth": metrics["compounded_growth"],
        "compounded_max_dd": metrics["compounded_max_dd"],
        "avg_deployed_capital": metrics["avg_deployed_capital"],
        "turnover_per_day": metrics["turnover_per_day"],
        "n_entries": metrics["n_entries"], "n_adds": metrics["n_adds"],
        "n_reduces": metrics["n_reduces"], "n_exits": metrics["n_exits"],
        "n_blocked_slots": metrics["n_blocked_slots"],
        "n_pending": metrics["n_pending"], "n_carries": metrics["n_carries"],
        "total_pnl": total_pnl,
        "control_run_id": cell.control_run_id,
        "control_total_pnl": (float(control["total_pnl"]) if control else None),
        "incremental_total_pnl": incremental_total,
        "incremental_mean_basket_day": (None if incremental_total is None
                                        else incremental_total / max(1, len(days))),
        "reserve_notional": reserve_notional,
        "reserve_requested_notional": reserve_requested,
        "reserve_executed_notional": reserve_executed,
        "reserve_retained_notional": reserve_retained,
        "tranche_net_total": tranche_total,
        "tranche_delta_residual": residual,
        "tranche_delta_ok": delta_ok,
        **counts,
    }


def _aliases_by_run() -> dict[str, list[Cell]]:
    aliases: dict[str, list[Cell]] = {}
    for cell in build_cells():
        if cell.is_alias:
            aliases.setdefault(cell.run_id, []).append(cell)
    return aliases


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #


def _write_run_config(out_root: Path, days: list[str]) -> None:
    """Provenance manifest: extension hash, contract version, registered grid."""
    source_paths = (
        EXTENSIONS_PATH,
        ROOT / "factory" / "BASKET-SIM-CONTRACT.md",
        ROOT / "factory" / "scripts" / "basket_sim.py",
        ROOT / "factory" / "scripts" / "basket_f6.py",
        ROOT / "researches" / "PRE-REG-BASKET-02.md",
        sim.CAL_PATH,
    )
    provenance = {path.relative_to(ROOT).as_posix(): _file_sha256(path)
                  for path in source_paths}
    data_hasher = hashlib.sha256()
    for day in days:
        sim.guard_day(day)
        for path in (sim.ANAT_DIR / f"{day}.jsonl", sim.BARS_DIR / f"{day}.parquet"):
            data_hasher.update(path.relative_to(ROOT).as_posix().encode())
            data_hasher.update(_file_sha256(path).encode())
    _atomic_json(out_root / "run_config.json", {
        "family_id": "F6", "evidence_label": "RUN",
        "days": days, "days_n": len(days),
        "blocks": {name: {"days_n": len(ds), "first": ds[0] if ds else None,
                          "last": ds[-1] if ds else None}
                   for name, ds in dev_blocks(days).items()},
        "registered_grid": registered_grid(),
        "extension": {"id": EXTENSION_ID, "file": "factory/BASKET-SIM-EXTENSIONS.md",
                      "sha256": _file_sha256(EXTENSIONS_PATH)},
        "contract_version": sim.CONTRACT_VERSION,
        "sim_contract_hash": sim._contract_hash(),
        "canonical_data_sha256": data_hasher.hexdigest(),
        "provenance_sha256": provenance,
        "git_head": sim._git_head(),
        "execution": ("batch checkpoint after all ticket decisions of the completed bar; "
                      "reserve ADD executes at the recipient's next eligible bar open; "
                      "no same-bar execution; sleeve-local reserve cash; ADD cap and "
                      "cash/no-leverage invariants remain engine-owned"),
        "incremental_basis": ("incremental_* compares each deploying cell with its same-p "
                              "cash control over the identical day set; cells that deploy "
                              "nothing carry control_run_id = null"),
        "no_validation_claim": True,
    })


def _pooled_from_surface(row: dict) -> dict:
    pooled = {name: None for name in _POOLED_SCHEMA}
    pooled.update({name: row.get(name) for name in _POOLED_SCHEMA
                   if name in row and name != "n"})
    pooled["n"] = row["N"]
    pooled["alias"] = row["alias_of"] is not None
    return pooled


def _aggregate_day_rows(cell: Cell, daily: pl.DataFrame, control: pl.DataFrame | None,
                        days: list[str]) -> list[dict]:
    control_pnl = {}
    if control is not None:
        control_pnl = dict(zip(control["date"].to_list(), control["pnl"].to_list()))
    rows = []
    for rec in daily.iter_rows(named=True):
        day = rec["date"]
        rows.append({
            "run_id": cell.run_id, "declared_id": cell.declared_id, "entry": cell.entry_id,
            "n": cell.n, "p": cell.p, "reserve_policy": cell.reserve_policy,
            "checkpoint_et": cell.checkpoint, "bps": cell.bps, "block": block_of(day),
            "date": day, "r_day": float(rec["r_day"]), "pnl": float(rec["pnl"]),
            "deployed_end": float(rec["deployed_end"]),
            "deployed_avg": float(rec["deployed_avg"]),
            "n_open_end": int(rec["n_open_end"]), "n_actions": int(rec["n_actions"]),
            "control_run_id": cell.control_run_id,
            "control_pnl": (control_pnl.get(day) if cell.control_run_id else None),
            "incremental_pnl": (float(rec["pnl"]) - control_pnl[day]
                                if cell.control_run_id and day in control_pnl else None),
        })
    return rows


def _aggregate_block_rows(cell: Cell, daily: pl.DataFrame, tickets: pl.DataFrame,
                          events: list[dict], sleeves: list[dict],
                          control: pl.DataFrame | None, days: list[str]) -> list[dict]:
    daily_rows = {rec["date"]: rec for rec in daily.iter_rows(named=True)}
    control_pnl = {}
    if control is not None:
        control_pnl = dict(zip(control["date"].to_list(), control["pnl"].to_list()))
    ticket_rows = tickets.to_dicts()
    rows = []
    for block, _, _ in BLOCKS:
        block_days = [day for day in days if block_of(day) == block]
        pnls = [float(daily_rows[day]["pnl"]) for day in block_days if day in daily_rows]
        if not pnls:
            continue
        block_set = set(block_days)
        entries = [row for row in ticket_rows if row["sleeve_day"] in block_set]
        exits = [row for row in entries
                 if not row["open_end"]
                 and (row["exit_day"] or row["sleeve_day"]) in block_set]
        block_events = [row for row in events if row["block"] == block]
        block_sleeves = [row for row in sleeves if row["block"] == block]
        total = sum(pnls)
        control_total = (sum(control_pnl[day] for day in block_days if day in control_pnl)
                         if cell.control_run_id else None)
        incremental = (total - control_total) if control_total is not None else None
        rows.append({
            "run_id": cell.run_id, "declared_id": cell.declared_id, "entry": cell.entry_id,
            "n": cell.n, "p": cell.p, "reserve_policy": cell.reserve_policy,
            "checkpoint_et": cell.checkpoint, "bps": cell.bps, "block": block,
            "days_n": len(pnls),
            "mean_basket_day": total / len(pnls),
            "median_basket_day": float(pl.Series(pnls).median()),
            "std_basket_day": float(pl.Series(pnls).std()) if len(pnls) > 1 else 0.0,
            "worst_day": min(pnls), "positive_day_share": sum(1 for x in pnls if x > 0) / len(pnls),
            "total_pnl": total, "control_total_pnl": control_total,
            "incremental_total_pnl": incremental,
            "incremental_mean_basket_day": (None if incremental is None
                                            else incremental / len(pnls)),
            "mean_deployed_end": sum(float(daily_rows[day]["deployed_end"])
                                     for day in block_days if day in daily_rows) / len(pnls),
            "n_entries": len(entries), "n_adds": sum(int(row["n_adds"]) for row in entries),
            "n_exits": len(exits),
            "reserve_executed_notional": sum(float(row["executed_notional"] or 0.0)
                                             for row in block_sleeves),
            "reserve_retained_notional": sum(float(row["retained_reserve_cash"] or 0.0)
                                             for row in block_sleeves),
            "events_n": len(block_events),
            "executed_n": sum(1 for row in block_events if row["executed"]),
            "control_run_id": cell.control_run_id,
        })
    return rows


def finalize_outputs(out_root: Path, days: list[str]) -> dict:
    """Rebuild the pooled / block / day aggregates from per-cell artifacts (idempotent)."""
    _require_extension()
    surface = json.loads((out_root / "surface.json").read_text())
    expected_rows = len(build_cells())
    if len(surface.get("cells", [])) != expected_rows:
        raise F6ContractError(
            f"surface.json holds {len(surface.get('cells', []))} rows, expected "
            f"{expected_rows} logical cells: refusing to publish partial aggregates")
    day_rows: list[dict] = []
    block_rows: list[dict] = []
    event_rows: list[dict] = []
    sleeve_rows: list[dict] = []
    for run_id, cell in sorted(unique_runs().items()):
        run_dir = out_root / "F6" / run_id
        if not is_complete(run_dir):
            raise F6ContractError(f"missing cell outputs for {run_id}")
        daily = pl.read_parquet(run_dir / "daily.parquet")
        tickets = pl.read_parquet(run_dir / "tickets.parquet")
        events = pl.read_parquet(run_dir / "reserve_events.parquet").to_dicts()
        sleeves = pl.read_parquet(run_dir / "reserve_sleeves.parquet").to_dicts()
        control = None
        if cell.control_run_id is not None:
            control_path = out_root / "F6" / cell.control_run_id / "daily.parquet"
            control = pl.read_parquet(control_path, columns=["date", "pnl"])
        day_rows.extend(_aggregate_day_rows(cell, daily, control, days))
        block_rows.extend(_aggregate_block_rows(cell, daily, tickets, events, sleeves,
                                                control, days))
        event_rows.extend(events)
        sleeve_rows.extend(sleeves)
    _atomic_parquet(out_root / "days.parquet", day_rows, _DAY_SCHEMA)
    _atomic_parquet(out_root / "blocks.parquet", block_rows, _BLOCK_SCHEMA)
    _atomic_parquet(out_root / "pooled.parquet",
                    [_pooled_from_surface(row) for row in surface["cells"]], _POOLED_SCHEMA)
    _atomic_parquet(out_root / "reserve_events.parquet", event_rows, _EVENT_SCHEMA)
    _atomic_parquet(out_root / "reserve_sleeves.parquet", sleeve_rows, _SLEEVE_SCHEMA)
    return {"runs_n": len(unique_runs()), "logical_cells_n": len(surface["cells"]),
            "day_rows_n": len(day_rows), "block_rows_n": len(block_rows),
            "event_rows_n": len(event_rows), "sleeve_rows_n": len(sleeve_rows)}


def run_cells(out_root: Path, *, days: list[str], workers: int) -> list[dict]:
    """Execute every unique cell, then rebuild the derived aggregates from the artifacts."""
    _require_extension()
    if not days:
        raise F6ContractError("F6 requires a non-empty day list")
    out_root.mkdir(parents=True, exist_ok=True)
    _write_run_config(out_root, days)
    aliases = _aliases_by_run()
    totals: dict[str, float] = {}
    rows: list[dict] = []
    runs = sorted(unique_runs().items())
    for index, (run_id, cell) in enumerate(runs, start=1):
        state = execute_cell(cell, out_root, days, workers)
        totals[run_id] = state["total_pnl"]
        rows.append(_surface_row(cell, state["status"], state["metrics"],
                                 state["total_pnl"],
                                 _resolve_control(cell, totals, out_root, days),
                                 state["events"], state["sleeves"], days=days,
                                 reused=state["reused"]))
        for alias in sorted(aliases.get(run_id, []), key=lambda c: c.declared_id):
            rows.append(_surface_row(alias, state["status"], state["metrics"],
                                     state["total_pnl"],
                                     _resolve_control(alias, totals, out_root, days),
                                     state["events"], state["sleeves"], days=days,
                                     reused=state["reused"]))
        rows.sort(key=lambda row: row["declared_id"])
        _atomic_json(out_root / "surface.json", {
            "family_id": "F6", "phase": "core", "cells": rows,
            "completed_runs": index, "expected_runs": len(runs),
            "logical_cells": len(build_cells()),
        })
        print(f"F6 runs completed {index}/{len(runs)}: {run_id} "
              f"({state['status']}, logical rows {len(rows)})", flush=True)
    summary = finalize_outputs(out_root, days)
    print(json.dumps({"mode": "run", "out_root": str(out_root), **summary}), flush=True)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="run all 1,066 development days")
    parser.add_argument("--max-days", type=int, default=None, help="smoke-test prefix only")
    parser.add_argument("--out-root", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    if args.full == (args.max_days is not None):
        parser.error("provide exactly one of --full or --max-days")
    days = sim.dev_days()
    if args.max_days is not None:
        if args.max_days < 1:
            parser.error("--max-days must be positive")
        days = days[:args.max_days]
    out_root = args.out_root or (OUT_ROOT if args.full else OUT_ROOT / "smoke")
    rows = run_cells(out_root, days=days, workers=args.workers)
    print(json.dumps({"runs": len(unique_runs()), "logical_cells": len(build_cells()),
                      "rows": len(rows), "days": len(days), "out_root": str(out_root)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
