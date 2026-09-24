#!/usr/bin/env python
"""F7 released-capital recycling: recycle a fraction of an executed release; development
evidence only.

Economic question
-----------------
A basket sleeve commits ``C0`` at entry and holds equal slots to the forced flat.  F7
keeps the frozen F3 full-exit release path and asks what the *released* capital should do
afterwards:

* ``hold``  — no release rule at all (``R0``): the primitive control ``H``;
* ``q=0``   — the frozen trigger releases to cash and the money stays there: the release
  control ``C``;
* ``q=50`` / ``q=100`` — the sleeve recycles that fraction of the released capital into
  its own remaining survivors (``E``), equally across recipients, executed as ordinary
  ``ADD``s at each recipient's next eligible bar open.

The paired comparisons (``release_effect = C - H``, ``redeployment_effect = E - C``,
``total_effect = E - H``) are reported per block with the block-day denominators.

Registered scope
----------------
Anchor entry ``A_pm``/``T570``, top-2 by canonical rank, ``N = 2`` slots, ``C0 = 1``,
no F5 adds (``scale_in = []``), no F6 reserve (``reserve_frac = 1.0``).  One registered
full-exit release path, factorised over the nine frozen F3 triggers
(``R2`` ``L`` in {10, 15} x ``w`` in {3, 5, 10}, ``R3`` ``g`` in {40, 50, 60}), or a
``--trigger`` subset.  Recycle fraction ``q`` in {0, 50, 100}%; friction 100/150 bps.
Four arms per (path, friction) -> 9 x 2 x 4 = 72 declared cells; the ``hold`` arm declares
no trigger (and no policy), so its nine rows per friction collapse onto one run:
56 unique simulations.

Mechanism, causality and capital rules
-------------------------------------
* Engine hook: ``EXT-1 BATCH-CAPITAL-CHECKPOINT-2026-09-24`` in
  ``factory/BASKET-SIM-EXTENSIONS.md`` (``StrategySpec.batch_policy`` / ``plan(ctx)``).
  The hook fires once per completed golden-window bar, after every ticket decision for
  that bar; returned intents become ordinary §7-step-0 pending ``ADD``s, so nothing ever
  executes on the decision bar.
* **Released capital is the removed cost basis of an executed ``EXIT`` action** — read
  from the executed action history (``ENTER``/``ADD`` open the basis, ``REDUCE`` removes it
  pro rata, ``EXIT`` removes all of it).  It is never read from a signal, a mark, a
  pending action, or a data-boundary/data-end terminal mark (which is not an action at
  all).
* A recycle is only triggered by an executed release: the policy pools the releases it can
  see in the executed action history at the completed checkpoint bar and nothing else.
  ``timing`` (declared per cell) selects the assignment rule: ``next_checkpoint`` = the
  first declared checkpoint at or after the release's execution bar; ``same_bar`` = only a
  release that executed on the checkpoint bar itself.  A release that never reaches a
  declared checkpoint is reported as unrecycled cash, never silently recycled later.
* Recipients are the engine's own ``eligible`` survivors of the **same sleeve** (open, no
  pending action, completed bar at the checkpoint), re-checked here for a pending action,
  zero/closed shares, a same-bar action and an unresolved carry.  No rank-1 concentration
  (equal split), no cross-sleeve transfer: recycled money is sleeve-local cash only.
* The requested notional is ``min(q * released, sleeve cash, C0 - deployed)``, split
  equally across recipients and truncated per recipient at the engine's ADD cap headroom
  (``unit_notional - add_notional``).  Truncated, unfunded and unallocated money stays in
  the sleeve as cash and is reported; there is no re-split and no bypass of the engine's
  cap/cash rules.

Outputs (``<out-root>/F7/<run_id>/``)
------------------------------------
The standard simulator outputs (``config.json``, ``daily.parquet``, ``tickets.parquet``,
``metrics.json``, ``run_summary.json``, month parts), plus ``recycle_events.parquet``
(executed-tranche ledger, below) and ``f7_cell.json`` (cell parameters + reconciliation).

At the out-root: ``run_config.json`` (provenance: contract version, engine hash, extension
hash, registered grid, day/block manifest), ``surface.json`` (every declared cell,
including the null-effect rows), ``pooled.parquet`` (the same rows in tabular form),
``blocks.parquet`` (canonical run x block), ``effects.parquet`` (paired arm effects per
declared path/friction/fraction x block with the block-day denominators), ``days.parquet``
(canonical run x day) and ``recycle_events.parquet`` (pooled ledger).

Ledger semantics (``recycle_events.parquet``)
--------------------------------------------
One row per decision unit, discriminated by ``row_kind``:

* ``release`` — every executed ``EXIT`` of a sleeve-day ticket: released notional (removed
  cost basis), the plan checkpoint it fed (or the reason it never fed one), its pro-rata
  share of the plan's requested/executed notional, and the cash it left behind;
* ``allocation`` — every recipient intent: allocation id, release linkage, equal share,
  cap headroom, requested vs executed notional, execution day/et/px, carried flag, the
  underlying ticket's terminal exit/mark/terminal-kind, the executed tranche's
  realized/marked/terminal split and direct net P&L;
* ``excluded`` — every candidate the recipient rules removed, with ``exclusion_reason``.

``status`` values: ``executed``, ``cap_limited``, ``unfunded``, ``no_eligible_survivor``,
``pending_excluded``, ``halted``, ``underlying_closed``, ``zero_shares``, ``closed``,
``same_bar_action``, ``release_to_cash``, ``unrecycled_no_checkpoint``,
``unrecycled_same_bar_miss``, ``cross_session_out_of_scope``,
``unexecuted_open_at_end``.  Nothing is assumed to have filled: an intent without a
matching executed action is classified from the engine's own evidence (the ticket's
``add_unfunded`` / ``add_cap_exceeded`` flags raised after the decision, closure, or a
still-pending action).

Contract: ``factory/BASKET-SIM-CONTRACT.md`` (C1, 2026-09-24),
``factory/BASKET-SIM-EXTENSIONS.md`` (EXT-1) and ``researches/PRE-REG-BASKET-02.md`` §3.6.
Development evidence only: no profitability, selection, or out-of-sample claim is made
anywhere in this module, and no validation is performed here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

if __package__:
    from factory.scripts import basket_sim as sim
else:
    import basket_sim as sim

ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / "factory" / "artifacts" / "basket" / "phase2" / "F7"
EXTENSIONS_PATH = ROOT / "factory" / "BASKET-SIM-EXTENSIONS.md"
EXTENSION_ID = "EXT-1 BATCH-CAPITAL-CHECKPOINT-2026-09-24"

ENTRY_POP = "A_pm"
ENTRY_T = 570
N_SLOTS = 2
# (trigger_id, kind, L, w, g) — the nine frozen F3 full-exit triggers, ids verbatim.
TRIGGER_SPECS = (
    ("R2_L10_w3", "R2", 10, 3, None),
    ("R2_L10_w5", "R2", 10, 5, None),
    ("R2_L10_w10", "R2", 10, 10, None),
    ("R2_L15_w3", "R2", 15, 3, None),
    ("R2_L15_w5", "R2", 15, 5, None),
    ("R2_L15_w10", "R2", 15, 10, None),
    ("R3_g40", "R3", None, None, 40),
    ("R3_g50", "R3", None, None, 50),
    ("R3_g60", "R3", None, None, 60),
)
ARM_HOLD = "hold"
Q_GRID = (0.0, 0.5, 1.0)
BPS_GRID = (100, 150)
BLOCKS = (("block1", "2021-02", "2023-12"), ("block2", "2025-02", "2026-05"))
# Declared release -> checkpoint assignment.  ``next_checkpoint`` is the registered value;
# ``same_bar`` is a declared alternative (see the module docstring).
TIMING_NEXT_CHECKPOINT = "next_checkpoint"
TIMING_SAME_BAR = "same_bar"
RECYCLE_TIMINGS = (TIMING_NEXT_CHECKPOINT, TIMING_SAME_BAR)
REGISTERED_TIMING = TIMING_NEXT_CHECKPOINT

ROW_KIND_RELEASE = "release"
ROW_KIND_ALLOCATION = "allocation"
ROW_KIND_EXCLUDED = "excluded"

STATUS_EXECUTED = "executed"
STATUS_CAP_LIMITED = "cap_limited"
STATUS_UNFUNDED = "unfunded"
STATUS_NO_ELIGIBLE_SURVIVOR = "no_eligible_survivor"
STATUS_PENDING_EXCLUDED = "pending_excluded"
STATUS_HALTED = "halted"
STATUS_UNDERLYING_CLOSED = "underlying_closed"
STATUS_ZERO_SHARES = "zero_shares"
STATUS_CLOSED = "closed"
STATUS_SAME_BAR_ACTION = "same_bar_action"
STATUS_RELEASE_TO_CASH = "release_to_cash"
STATUS_RECYCLED = "recycled"
STATUS_UNRECYCLED_NO_CHECKPOINT = "unrecycled_no_checkpoint"
STATUS_UNRECYCLED_SAME_BAR_MISS = "unrecycled_same_bar_miss"
STATUS_CROSS_SESSION_OUT_OF_SCOPE = "cross_session_out_of_scope"
STATUS_UNEXECUTED_OPEN_AT_END = "unexecuted_open_at_end"
STATUS_ALLOCATED = "allocated"

# recipient-rule exclusion reason -> recorded status
EXCLUSION_STATUS = {
    "pending_action": STATUS_PENDING_EXCLUDED,
    "unresolved_carry": STATUS_PENDING_EXCLUDED,
    "halted_at_checkpoint": STATUS_HALTED,
    "zero_shares": STATUS_ZERO_SHARES,
    "closed": STATUS_CLOSED,
    "same_bar_action": STATUS_SAME_BAR_ACTION,
}

REASON_PREFIX = "F7_RECYCLE_EQUAL:"
EPS = 1e-12

CORE_OUTPUTS = ("config.json", "daily.parquet", "tickets.parquet", "metrics.json",
                "run_summary.json")
F7_OUTPUTS = ("f7_cell.json", "recycle_events.parquet")
REQUIRED_OUTPUTS = CORE_OUTPUTS + F7_OUTPUTS


class F7ContractError(RuntimeError):
    """F7 refused an inconsistent declaration, core output, or reconciliation."""


def _require_extension() -> None:
    """Fail loudly when ``basket_sim`` predates EXT-1."""
    if (not hasattr(sim, "BatchAllocationPolicy")
            or not hasattr(sim, "BatchCheckpointContext")
            or "batch_policy" not in getattr(sim.StrategySpec, "__dataclass_fields__", {})):
        raise F7ContractError(
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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# Triggers, cells, grid
# --------------------------------------------------------------------------- #


def trigger_spec(trigger_id: str) -> tuple:
    for spec in TRIGGER_SPECS:
        if spec[0] == trigger_id:
            return spec
    raise F7ContractError(f"unknown trigger {trigger_id!r}; registered ids: "
                          f"{[spec[0] for spec in TRIGGER_SPECS]}")


def trigger_rule(kind: str, L: int | None, w: int | None, g: int | None) -> sim.ReleaseRule:
    """The frozen F3 rule for one trigger id (``R2`` uses the signed-percent convention)."""
    if kind == "R0":
        return sim.R0()
    if kind == "R2":
        return sim.R2(-int(L), int(w))
    if kind == "R3":
        return sim.R3(int(g))
    raise F7ContractError(f"unknown trigger kind {kind!r}")


def trigger_rule_name(kind: str, L: int | None, w: int | None, g: int | None) -> str:
    return trigger_rule(kind, L, w, g).name


@dataclass(frozen=True)
class Cell:
    """One declared F7 cell.  ``declared_id`` is the logical row; ``run_id`` is the
    canonical simulation it resolves to (the ``hold`` arm declares no trigger)."""

    trigger_id: str
    kind: str
    L: int | None
    w: int | None
    g: int | None
    q: float | None
    bps: int
    timing: str = REGISTERED_TIMING

    @property
    def arm(self) -> str:
        return ARM_HOLD if self.q is None else f"q{int(round(self.q * 100)):03d}"

    @property
    def holds(self) -> bool:
        return self.q is None

    @property
    def timing_suffix(self) -> str:
        """Non-registered timings are declared in the id so runs can never collide."""
        return "" if self.timing == REGISTERED_TIMING else f"_timing_{self.timing}"

    @property
    def declared_id(self) -> str:
        return f"A_pm_N2_{self.trigger_id}_{self.arm}_bps{self.bps}{self.timing_suffix}"

    @property
    def run_id(self) -> str:
        if self.holds:
            # R0 declares no release and no policy: the trigger and the timing are inert.
            return f"A_pm_N2_R0_bps{self.bps}"
        return (f"A_pm_N2_{self.trigger_id}_{self.arm}_bps{self.bps}{self.timing_suffix}")

    @property
    def canonical(self) -> bool:
        """True when this declared row *is* the simulation its ``run_id`` names."""
        if self.holds:
            return self.trigger_id == TRIGGER_SPECS[0][0]
        return True

    @property
    def is_alias(self) -> bool:
        return not self.canonical

    @property
    def alias_reason(self) -> str | None:
        if self.canonical:
            return None
        return ("hold arm (R0): the release trigger, the recycle fraction and the "
                "release-to-checkpoint timing are all inert -> one run per friction")

    @property
    def h_run_id(self) -> str:
        """The HOLD arm run for this friction (this cell's own run for the hold arm)."""
        return f"A_pm_N2_R0_bps{self.bps}"

    @property
    def c_run_id(self) -> str | None:
        """The release-to-cash (q=0) arm run for this path and friction."""
        if self.holds:
            return None
        if self.q is not None and self.q <= EPS:
            return self.run_id
        return f"A_pm_N2_{self.trigger_id}_q000_bps{self.bps}{self.timing_suffix}"

    @property
    def e_run_id(self) -> str | None:
        """The recycling arm run (None for the hold and release-to-cash arms)."""
        if self.holds or self.q is None or self.q <= EPS:
            return None
        return self.run_id

    def rule(self) -> sim.ReleaseRule:
        return trigger_rule(self.kind, self.L, self.w, self.g)

    def build_policy(self) -> "RecyclePolicy | None":
        """A fresh policy instance for this cell (None for the hold arm)."""
        _require_extension()
        if self.holds:
            return None
        return RecyclePolicy(q=float(self.q), timing=self.timing, trigger_id=self.trigger_id,
                             bps=float(self.bps))

    def strategy(self, policy=None) -> sim.StrategySpec:
        """The declared cell as a simulator spec (``R0`` and no policy for the hold arm)."""
        _require_extension()
        pol = self.build_policy() if policy is None else policy
        release = [sim.R0()] if self.holds else [self.rule()]
        return sim.StrategySpec(
            family_id="F7",
            entry_pop=ENTRY_POP,
            entry_T=ENTRY_T,
            top_n=N_SLOTS,
            n_slots=N_SLOTS,
            reserve_frac=1.0,          # no F6 reserve: the whole sleeve is committed
            release=release,
            scale_in=[],               # no F5 adds
            name=self.run_id,
            batch_policy=pol,
        )


def build_cells(triggers: tuple[str, ...] | None = None,
                timing: str = REGISTERED_TIMING) -> list[Cell]:
    """The declared F7 cells: 4 arms x 9 paths x 2 frictions (72 by default)."""
    if timing not in RECYCLE_TIMINGS:
        raise F7ContractError(f"unknown recycle timing {timing!r}; declared: {RECYCLE_TIMINGS}")
    selected = TRIGGER_SPECS if triggers is None else tuple(
        trigger_spec(trigger_id) for trigger_id in triggers)
    cells = []
    for trigger_id, kind, L, w, g in selected:
        for q in (None,) + Q_GRID:
            for bps in BPS_GRID:
                cells.append(Cell(trigger_id, kind, L, w, g, q, bps, timing))
    return cells


def unique_runs(triggers: tuple[str, ...] | None = None,
                timing: str = REGISTERED_TIMING) -> dict[str, Cell]:
    """``run_id -> canonical cell``.

    The hold arm (``R0``, no policy) is trigger-independent, so it is one run per
    friction — always declared, because it is the ``H`` reference of every paired effect.
    """
    reference = TRIGGER_SPECS[0]
    runs: dict[str, Cell] = {}
    for bps in BPS_GRID:
        cell = Cell(reference[0], reference[1], reference[2], reference[3], reference[4],
                    None, bps, timing)
        runs[cell.run_id] = cell
    for cell in build_cells(triggers, timing):
        if cell.holds:
            continue
        if cell.run_id in runs:
            raise F7ContractError(f"duplicate canonical run id {cell.run_id}")
        runs[cell.run_id] = cell
    return runs


def registered_grid(triggers: tuple[str, ...] | None = None,
                    timing: str = REGISTERED_TIMING) -> dict:
    cells = build_cells(triggers, timing)
    return {
        "family_id": "F7",
        "entry": {"pop": ENTRY_POP, "T": ENTRY_T, "top_n": N_SLOTS, "n_slots": N_SLOTS},
        "capital": "C0 = 1.0 per basket-day sleeve; no leverage; sleeve-local cash",
        "f5_adds": "none (scale_in = [])",
        "f6_reserve": "none (reserve_frac = 1.0)",
        "triggers": [{"trigger_id": tid, "kind": kind, "L": L, "w": w, "g": g,
                      "rule": trigger_rule_name(kind, L, w, g)}
                     for tid, kind, L, w, g in TRIGGER_SPECS],
        "trigger_subset": list(triggers) if triggers is not None else None,
        "recycle_fraction_pct": [int(round(q * 100)) for q in Q_GRID],
        "arms": [ARM_HOLD, "q000", "q050", "q100"],
        "bps_round_trip": list(BPS_GRID),
        "timing": timing,
        "checkpoint_ets": list(sim.CHECKPOINTS),
        "add_cap": "total ADD notional per ticket <= 1.0 x unit notional (engine-owned)",
        "recipient_policy": ("equal across causal eligible original survivors of the same "
                             "sleeve; no rank-1 concentration; no cross-sleeve transfer"),
        "released_capital": ("removed cost basis of an executed EXIT action; never a "
                             "signal, a mark, or a boundary/data-end mark"),
        "logical_cells": len(cells),
        "unique_simulations": len(unique_runs(triggers, timing)),
        "alias_rules": ["hold arm (R0): trigger, fraction and timing are inert -> "
                        "one run per friction"],
    }


def parse_triggers(values: list[str] | None) -> tuple[str, ...] | None:
    """``--trigger`` values (repeatable, comma-separated) -> validated trigger ids."""
    if not values:
        return None
    ids: list[str] = []
    for value in values:
        ids.extend(part.strip() for part in str(value).split(",") if part.strip())
    unknown = [trigger_id for trigger_id in ids
               if trigger_id not in {spec[0] for spec in TRIGGER_SPECS}]
    if unknown:
        raise F7ContractError(f"unknown trigger ids {unknown}; registered ids: "
                              f"{[spec[0] for spec in TRIGGER_SPECS]}")
    if not ids:
        raise F7ContractError("--trigger requires at least one registered trigger id")
    return tuple(dict.fromkeys(ids))


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
# Executed-action accounting (released capital and tranche P&L)
# --------------------------------------------------------------------------- #


def removed_cost_basis(tk: sim.Ticket, bps: float) -> list[tuple[int, float]]:
    """``(action index, removed cost basis)`` for every executed ``EXIT`` of one ticket.

    Replays the engine's own share/cost arithmetic (``sim._execute``): ``ENTER``/``ADD``
    open the basis, ``REDUCE`` removes it pro rata, ``EXIT`` removes all of it.  A
    data-boundary/data-end terminal mark is not an action, so it can never appear here.
    """
    side = bps / 2.0 / 10000.0
    out: list[tuple[int, float]] = []
    cost_open = 0.0
    shares = 0.0
    for index, act in enumerate(tk.actions):
        action = act["action"]
        qty = float(act["shares"])
        if action == "ENTER":
            shares = qty
            cost_open += float(tk.unit_notional)
        elif action == "ADD":
            shares += qty
            cost_open += qty * float(act["px"]) * (1.0 + side)
        elif action == "REDUCE":
            if shares > 0.0:
                cost_open = max(0.0, cost_open - cost_open * (qty / shares))
            shares -= qty
        elif action == "EXIT":
            out.append((index, cost_open))
            shares = 0.0
            cost_open = 0.0
    return out


def replay_tranches(tk: sim.Ticket, bps: float) -> dict[str, dict]:
    """Exact per-tranche P&L replay of one ticket's executed actions, keyed by reason.

    Every ``ENTER``/``ADD`` opens a tranche; every ``REDUCE``/``EXIT`` distributes its
    proceeds across the live tranches pro rata by share count, exactly as the engine's
    fungible-share arithmetic does.  A tranche's net is ``realized + mark or terminal
    value - cash_in``; the tranches of one ticket sum to ``Ticket.net()``.
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

    ``terminal_basis`` is ``exit`` (a real executed ``EXIT``), ``terminal_mark`` (a
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


# --------------------------------------------------------------------------- #
# Recipient rules
# --------------------------------------------------------------------------- #


def _has_bar_at(bars: sim.Bars, ticker: str, et: int) -> bool:
    tkb = bars.ticker(ticker)
    if tkb is None:
        return False
    arr = tkb["et"]
    pos = int(np.searchsorted(arr, et, side="left"))
    return pos < len(arr) and int(arr[pos]) == et


def _same_bar_action(tk: sim.Ticket, day: str, et: int) -> bool:
    return any(str(act["day"]) == day and int(act["et"]) == et for act in tk.actions)


def recipient_exclusion(tk: sim.Ticket, ctx: sim.BatchCheckpointContext) -> str | None:
    """Recipient-rule exclusions the engine's ``eligible`` list does not cover.

    Returns an ``EXCLUSION_STATUS`` key, or None when the ticket is a valid recipient.
    """
    if not tk.open:
        return "closed"
    if float(tk.shares) <= 0.0:
        return "zero_shares"
    pending = tk.pending
    if pending is not None:
        return "unresolved_carry" if pending.get("carry") else "pending_action"
    if _same_bar_action(tk, ctx.day, ctx.et):
        return "same_bar_action"
    if not _has_bar_at(ctx.bars, tk.ticker, ctx.et):
        return "halted_at_checkpoint"
    return None


# --------------------------------------------------------------------------- #
# The recycling policy (EXT-1 consumer)
# --------------------------------------------------------------------------- #


class RecyclePolicy(sim.BatchAllocationPolicy):
    """Recycle ``q`` of every visible executed release across the sleeve's survivors.

    The policy is the only place that decides *what* to request; the engine still owns
    execution, the ADD cap, the cash/no-leverage invariants and the carry rules.  The
    ``q = 0`` arm declares the same policy and requests nothing, so it is bit-identical
    to the same-trigger full-exit cell while still recording the release ledger.
    """

    name = "F7_release_recycle_equal_survivors"

    def __init__(self, *, q: float, timing: str, trigger_id: str, bps: float,
                 checkpoint_ets: tuple[int, ...] = sim.CHECKPOINTS):
        if timing not in RECYCLE_TIMINGS:
            raise F7ContractError(f"unknown recycle timing {timing!r}; declared: "
                                  f"{RECYCLE_TIMINGS}")
        if not 0.0 <= float(q) <= 1.0:
            raise F7ContractError(f"recycle fraction must be within [0, 1]; got {q!r}")
        declared = tuple(int(et) for et in checkpoint_ets)
        if not declared or any(et not in sim.CHECKPOINTS for et in declared):
            raise F7ContractError("checkpoint_ets must be a non-empty subset of "
                                  f"CHECKPOINTS {sim.CHECKPOINTS}")
        self.q = float(q)
        self.timing = timing
        self.trigger_id = trigger_id
        self.bps = float(bps)
        self.checkpoint_ets = declared
        self.strategy: sim.Strategy | None = None
        self.plans: list[dict] = []
        self.allocations: list[dict] = []
        self.exclusions: list[dict] = []
        self.release_plan: dict[tuple[str, str, int], str] = {}
        self._closed_cursor = 0

    # -- EXT-1 hook ------------------------------------------------------- #
    def plan(self, ctx: sim.BatchCheckpointContext) -> list:
        self.strategy = ctx.strategy
        releases = self._visible_releases(ctx)
        if not releases:
            return []
        day = ctx.day
        released = sum(notional for _, _, notional in releases)
        cash = float(ctx.strategy._cash(day))
        deployed = float(ctx.strategy._deployed(day))
        available = max(0.0, min(cash, sim.C0 - deployed))
        budget = min(self.q * released, available)
        recipients: list[sim.Ticket] = []
        excluded: list[tuple[sim.Ticket, str]] = []
        for tk in sorted(ctx.eligible, key=lambda item: (item.entry_rank, item.ticker)):
            why = recipient_exclusion(tk, ctx)
            if why is None:
                recipients.append(tk)
            else:
                excluded.append((tk, why))
        for tk, why in ctx.excluded:
            if why == "pending_action":
                pending = tk.pending or {}
                excluded.append((tk, "unresolved_carry" if pending.get("carry")
                                 else "pending_action"))
            else:
                excluded.append((tk, "halted_at_checkpoint"))

        share = budget / len(recipients) if recipients else 0.0
        if self.q <= EPS:
            plan_status = STATUS_RELEASE_TO_CASH
        elif not recipients:
            plan_status = STATUS_NO_ELIGIBLE_SURVIVOR
        elif budget <= EPS:
            plan_status = STATUS_UNFUNDED
        else:
            plan_status = STATUS_ALLOCATED
        plan_id = f"{day}:C{int(ctx.et)}"
        plan = {
            "plan_id": plan_id,
            "sleeve_day": day,
            "checkpoint_et": int(ctx.et),
            "timing": self.timing,
            "release_keys": [key for key, _, _ in releases],
            "release_ids": [_release_id(tk, tk.actions[key[2]])
                            for key, tk, _ in releases],
            "released_notional": released,
            "sleeve_cash": cash,
            "deployed_at_checkpoint": deployed,
            "available_notional": available,
            "budget_notional": budget,
            "equal_share_notional": share,
            "eligible_n": len(recipients),
            "excluded_n": len(excluded),
            "status": plan_status,
            "requested_notional": 0.0,
            "cap_limited_notional": 0.0,
            "executed_notional": 0.0,
            "retained_notional": released,
            "alloc_ids": [],
        }
        self.plans.append(plan)
        for key, _, _ in releases:
            self.release_plan[key] = plan_id

        intents: list = []
        if self.q <= EPS:
            # Declared release-to-cash arm: nothing is requested by construction, so the
            # ledger records the release and the checkpoint's eligibility, not intents.
            for tk, why in excluded:
                self._record_exclusion(plan_id, day, tk, why)
            return intents
        for tk in recipients:
            unit = float(tk.unit_notional)
            headroom = max(0.0, unit - float(tk.add_notional))
            requested = min(share, headroom)
            truncated = share - requested
            alloc_id = f"{day}:{tk.ticker}:C{int(ctx.et)}"
            reason = f"{REASON_PREFIX}{alloc_id}"
            self.allocations.append({
                "plan_id": plan_id,
                "alloc_id": alloc_id,
                "reason": reason,
                "sleeve_day": day,
                "ticker": tk.ticker,
                "entry_rank": int(tk.entry_rank),
                "unit_notional": unit,
                "add_notional_before": float(tk.add_notional),
                "cap_headroom": headroom,
                "equal_share_notional": share,
                "requested_notional": requested,
                "cap_limited_notional": truncated,
                "flags_before": tuple(sorted(set(tk.flags))),
                "_ticket": tk,
            })
            plan["requested_notional"] += requested
            plan["cap_limited_notional"] += truncated
            plan["alloc_ids"].append(alloc_id)
            if requested > EPS and unit > EPS:
                intents.append(sim.BatchAllocationIntent(
                    sleeve_day=day, ticker=tk.ticker, frac=requested / unit,
                    reason=reason, alloc_id=alloc_id,
                ))
        for tk, why in excluded:
            self._record_exclusion(plan_id, day, tk, why)
        return intents

    def _record_exclusion(self, plan_id: str, day: str, tk: sim.Ticket, why: str) -> None:
        self.exclusions.append({
            "plan_id": plan_id,
            "sleeve_day": day,
            "ticker": tk.ticker,
            "entry_rank": int(tk.entry_rank),
            "exclusion_reason": why,
            "pending_action": (tk.pending or {}).get("action"),
            "_ticket": tk,
        })

    # -- release discovery ------------------------------------------------ #
    def _visible_releases(self, ctx: sim.BatchCheckpointContext) -> list[tuple]:
        """Executed releases of this sleeve that no plan has consumed yet.

        Only the executed action history of tickets closed at or before the checkpoint bar
        is read, so a pending, unexecuted or terminal-mark release can never trigger a
        recycle.  The closed-ticket cursor keeps the scan monotone and cheap.
        """
        closed = ctx.strategy.closed
        out: list[tuple] = []
        for tk in closed[self._closed_cursor:]:
            if tk.sleeve_day != ctx.day:
                continue
            for index, notional in removed_cost_basis(tk, self.bps):
                key = (tk.sleeve_day, tk.ticker, index)
                if key in self.release_plan:
                    continue
                act = tk.actions[index]
                if str(act["day"]) != ctx.day or int(act["et"]) > ctx.et:
                    continue
                if self.timing == TIMING_SAME_BAR and int(act["et"]) != ctx.et:
                    continue
                out.append((key, tk, notional))
        self._closed_cursor = len(closed)
        return out


def _release_id(tk: sim.Ticket, act: dict) -> str:
    return f"{tk.sleeve_day}:{tk.ticker}:E{int(act['et'])}"


def _policy_tickets(policy: RecyclePolicy) -> list[sim.Ticket]:
    strategy = getattr(policy, "strategy", None)
    if strategy is None:
        return []
    return list(strategy.closed) + list(strategy.open_tickets.values())


# --------------------------------------------------------------------------- #
# Ledger resolution
# --------------------------------------------------------------------------- #


def _plan_context(plan: dict | None) -> dict:
    if plan is None:
        return {"plan_id": None, "checkpoint_et": None, "sleeve_cash": None,
                "deployed_at_checkpoint": None, "available_notional": None,
                "budget_notional": None, "equal_share_notional": None,
                "eligible_n": None, "excluded_n": None}
    return {"plan_id": plan["plan_id"], "checkpoint_et": plan["checkpoint_et"],
            "sleeve_cash": plan["sleeve_cash"],
            "deployed_at_checkpoint": plan["deployed_at_checkpoint"],
            "available_notional": plan["available_notional"],
            "budget_notional": plan["budget_notional"],
            "equal_share_notional": plan["equal_share_notional"],
            "eligible_n": plan["eligible_n"], "excluded_n": plan["excluded_n"]}


def _release_status(plan: dict) -> str:
    """The release-level outcome of one plan (never an assumption about execution)."""
    if plan["status"] == STATUS_RELEASE_TO_CASH:
        return STATUS_RELEASE_TO_CASH
    if plan["status"] == STATUS_NO_ELIGIBLE_SURVIVOR:
        return STATUS_NO_ELIGIBLE_SURVIVOR
    if plan["executed_notional"] > EPS:
        return STATUS_RECYCLED
    if plan["cap_limited_notional"] > EPS and plan["requested_notional"] <= EPS:
        return STATUS_CAP_LIMITED
    return STATUS_UNFUNDED


def _release_row(cell: Cell, policy: RecyclePolicy, tk: sim.Ticket, index: int,
                 notional: float, plan: dict | None) -> dict:
    act = tk.actions[index]
    release_et = int(act["et"])
    release_day = str(act["day"])
    row = {
        "run_id": cell.run_id, "declared_id": cell.declared_id,
        "row_kind": ROW_KIND_RELEASE, "trigger_id": cell.trigger_id, "kind": cell.kind,
        "L": cell.L, "w": cell.w, "g": cell.g, "arm": cell.arm, "q": cell.q,
        "bps": cell.bps, "timing": cell.timing, "block": block_of(tk.sleeve_day),
        "sleeve_day": tk.sleeve_day, "ticker": tk.ticker,
        "entry_rank": int(tk.entry_rank), "unit_notional": float(tk.unit_notional),
        "add_notional_before": None,
        "release_id": _release_id(tk, act), "release_ids": None,
        "release_day": release_day, "release_et": release_et,
        "released_notional": float(notional),
        "decision_day": tk.sleeve_day, "decision_et": None,
        "alloc_id": None, "reason": None, "exclusion_reason": None, "pending_action": None,
        "cap_headroom": None, "cap_limited_notional": None, "unfunded_notional": None,
        "executed": False, "execution_day": None, "execution_et": None,
        "execution_px": None, "executed_notional": 0.0, "carried": None,
        "tranche_cash_in": None, "tranche_realized_net": None, "tranche_marked_net": None,
        "tranche_terminal_value": None, "tranche_net_pnl": None,
    }
    if plan is None:
        if release_day != tk.sleeve_day:
            status = STATUS_CROSS_SESSION_OUT_OF_SCOPE
        elif policy.timing == TIMING_SAME_BAR and release_et not in policy.checkpoint_ets:
            status = STATUS_UNRECYCLED_SAME_BAR_MISS
        else:
            status = STATUS_UNRECYCLED_NO_CHECKPOINT
        row.update({"status": status, "requested_notional": 0.0,
                    "retained_notional": float(notional), **_plan_context(None)})
        row.update(_terminal_fields(tk))
        return row
    total_released = float(plan["released_notional"])
    share_of_plan = float(notional) / total_released if total_released > 0.0 else 0.0
    executed = float(plan["executed_notional"]) * share_of_plan
    row.update({
        "status": _release_status(plan),
        "decision_et": plan["checkpoint_et"],
        "requested_notional": float(plan["requested_notional"]) * share_of_plan,
        "executed_notional": executed,
        "retained_notional": float(notional) - executed,
        **_plan_context(plan),
    })
    row.update(_terminal_fields(tk))
    return row


def _allocation_row(cell: Cell, event: dict, plan: dict | None) -> dict:
    tk = event["_ticket"]
    row = {
        "run_id": cell.run_id, "declared_id": cell.declared_id,
        "row_kind": ROW_KIND_ALLOCATION, "trigger_id": cell.trigger_id,
        "kind": cell.kind, "L": cell.L, "w": cell.w, "g": cell.g, "arm": cell.arm,
        "q": cell.q, "bps": cell.bps, "timing": cell.timing,
        "block": block_of(event["sleeve_day"]), "sleeve_day": event["sleeve_day"],
        "ticker": event["ticker"], "entry_rank": event["entry_rank"],
        "unit_notional": event["unit_notional"],
        "add_notional_before": event["add_notional_before"],
        "release_id": None,
        "release_ids": ",".join(plan["release_ids"]) if plan else None,
        "release_day": None, "release_et": None,
        "released_notional": plan["released_notional"] if plan else None,
        "decision_day": event["sleeve_day"], "decision_et": (plan["checkpoint_et"]
                                                             if plan else None),
        "alloc_id": event["alloc_id"], "reason": event["reason"],
        "exclusion_reason": None, "pending_action": None,
        "cap_headroom": event["cap_headroom"],
        "requested_notional": event["requested_notional"],
        "cap_limited_notional": event["cap_limited_notional"],
        "unfunded_notional": 0.0,
        "executed": False, "execution_day": None, "execution_et": None,
        "execution_px": None, "executed_notional": 0.0, "carried": None,
        "tranche_cash_in": None, "tranche_realized_net": None, "tranche_marked_net": None,
        "tranche_terminal_value": None, "tranche_net_pnl": None,
        **_plan_context(plan),
    }
    row.update(_terminal_fields(tk))
    replay = replay_tranches(tk, cell.bps)
    tranche = replay.get(event["reason"])
    if tranche is not None:
        drift = sum(value["net"] for value in replay.values()) - tk.net()
        if abs(drift) > 1e-9 * max(1.0, abs(tk.net())):
            raise F7ContractError(
                f"tranche replay does not reconcile for {tk.sleeve_day}/{tk.ticker}: "
                f"{drift:.3e}")
        row.update({
            "status": (STATUS_CAP_LIMITED if event["cap_limited_notional"] > EPS
                       else STATUS_EXECUTED),
            "executed": True,
            "execution_day": tranche["execution_day"],
            "execution_et": int(tranche["execution_et"]),
            "execution_px": float(tranche["execution_px"]),
            "executed_notional": float(tranche["cash_in"]),
            "carried": bool(tranche["execution_day"] != event["sleeve_day"]),
            "tranche_cash_in": float(tranche["cash_in"]),
            "tranche_realized_net": float(tranche["realized_net"]),
            "tranche_marked_net": float(tranche["marked_net"]),
            "tranche_terminal_value": float(tranche["terminal_value"]),
            "tranche_net_pnl": float(tranche["net"]),
        })
        row["retained_notional"] = float(event["requested_notional"]) - \
            row["executed_notional"]
        return row
    new_flags = set(tk.flags) - set(event["flags_before"])
    if event["requested_notional"] <= EPS:
        # No intent was scheduled: either the sleeve had no deployable cash, or the
        # recipient's own cap headroom was already exhausted.
        capped = event["equal_share_notional"] > EPS and event["cap_headroom"] <= EPS
        row.update({
            "status": STATUS_CAP_LIMITED if capped else STATUS_UNFUNDED,
            "retained_notional": 0.0,
            "unfunded_notional": 0.0 if capped else float(event["equal_share_notional"]),
        })
        return row
    if "add_unfunded" in new_flags:
        status = STATUS_UNFUNDED
    elif "add_cap_exceeded" in new_flags:
        status = STATUS_CAP_LIMITED
    elif not tk.open or tk.terminal_kind is not None:
        status = STATUS_UNDERLYING_CLOSED
    else:
        status = STATUS_UNEXECUTED_OPEN_AT_END
    row.update({
        "status": status,
        "retained_notional": float(event["requested_notional"]),
        "unfunded_notional": (float(event["requested_notional"])
                              if status == STATUS_UNFUNDED else 0.0),
    })
    return row


def _excluded_row(cell: Cell, event: dict, plan: dict | None) -> dict:
    why = event["exclusion_reason"]
    row = {
        "run_id": cell.run_id, "declared_id": cell.declared_id,
        "row_kind": ROW_KIND_EXCLUDED, "trigger_id": cell.trigger_id, "kind": cell.kind,
        "L": cell.L, "w": cell.w, "g": cell.g, "arm": cell.arm, "q": cell.q,
        "bps": cell.bps, "timing": cell.timing, "block": block_of(event["sleeve_day"]),
        "sleeve_day": event["sleeve_day"], "ticker": event["ticker"],
        "entry_rank": event["entry_rank"], "unit_notional": None,
        "add_notional_before": None,
        "release_id": None,
        "release_ids": ",".join(plan["release_ids"]) if plan else None,
        "release_day": None, "release_et": None,
        "released_notional": plan["released_notional"] if plan else None,
        "decision_day": event["sleeve_day"],
        "decision_et": plan["checkpoint_et"] if plan else None,
        "alloc_id": None, "reason": None, "status": EXCLUSION_STATUS[why],
        "exclusion_reason": why, "pending_action": event["pending_action"],
        "cap_headroom": None, "cap_limited_notional": None, "unfunded_notional": None,
        "executed": False, "execution_day": None, "execution_et": None,
        "execution_px": None, "executed_notional": 0.0, "carried": None,
        "requested_notional": 0.0, "retained_notional": None,
        "tranche_cash_in": None, "tranche_realized_net": None, "tranche_marked_net": None,
        "tranche_terminal_value": None, "tranche_net_pnl": None,
        **_plan_context(plan),
    }
    row.update(_terminal_fields(event["_ticket"]))
    return row


def _row_sort_key(row: dict) -> tuple:
    return (str(row["sleeve_day"]), str(row["plan_id"] or ""),
            str(row["release_id"] or ""), str(row["row_kind"]),
            str(row["ticker"]), str(row["alloc_id"] or ""))


def resolve_cell_ledger(cell: Cell, policy: RecyclePolicy | None,
                        tickets: list[sim.Ticket] | None = None) -> list[dict]:
    """Resolve every recorded release, allocation and exclusion into ledger rows.

    Runs after ``sim.run`` in the same process.  Release rows come from the authoritative
    executed action history of the tickets the policy's strategy holds (``tickets`` may be
    supplied for tests); allocation statuses are classified from the engine's own evidence
    (matching executed action, the ``add_unfunded``/``add_cap_exceeded`` flags raised
    after the decision, closure, or a still-pending action), never guessed.  A hold cell
    declares no policy and therefore has no recycle ledger.
    """
    if policy is None:
        return []
    plans = {plan["plan_id"]: dict(plan) for plan in policy.plans}
    rows: list[dict] = []
    allocations: list[dict] = []
    by_plan: dict[str, list[dict]] = {}
    for event in policy.allocations:
        plan = plans.get(event["plan_id"])
        row = _allocation_row(cell, event, plan)
        allocations.append(row)
        by_plan.setdefault(event["plan_id"], []).append(row)
    for plan in plans.values():
        plan_rows = by_plan.get(plan["plan_id"], [])
        plan["requested_notional"] = sum(float(r["requested_notional"] or 0.0)
                                        for r in plan_rows)
        plan["cap_limited_notional"] = sum(float(r["cap_limited_notional"] or 0.0)
                                           for r in plan_rows)
        plan["executed_notional"] = sum(float(r["executed_notional"] or 0.0)
                                        for r in plan_rows)
        plan["retained_notional"] = plan["released_notional"] - plan["executed_notional"]
    universe = list(tickets) if tickets is not None else _policy_tickets(policy)
    for tk in universe:
        for index, notional in removed_cost_basis(tk, cell.bps):
            key = (tk.sleeve_day, tk.ticker, index)
            rows.append(_release_row(cell, policy, tk, index, notional,
                                     plans.get(policy.release_plan.get(key))))
    rows.extend(allocations)
    for event in policy.exclusions:
        rows.append(_excluded_row(cell, event, plans.get(event["plan_id"])))
    rows.sort(key=_row_sort_key)
    return rows


# --------------------------------------------------------------------------- #
# Output schemas
# --------------------------------------------------------------------------- #

_EVENT_SCHEMA = {
    "run_id": pl.Utf8, "declared_id": pl.Utf8, "row_kind": pl.Utf8, "trigger_id": pl.Utf8,
    "kind": pl.Utf8, "L": pl.Int64, "w": pl.Int64, "g": pl.Int64, "arm": pl.Utf8,
    "q": pl.Float64, "bps": pl.Int64, "timing": pl.Utf8, "block": pl.Utf8,
    "sleeve_day": pl.Utf8, "ticker": pl.Utf8, "entry_rank": pl.Int64,
    "unit_notional": pl.Float64, "add_notional_before": pl.Float64,
    "plan_id": pl.Utf8, "checkpoint_et": pl.Int64, "decision_day": pl.Utf8,
    "decision_et": pl.Int64, "release_id": pl.Utf8, "release_ids": pl.Utf8,
    "release_day": pl.Utf8, "release_et": pl.Int64, "released_notional": pl.Float64,
    "status": pl.Utf8, "exclusion_reason": pl.Utf8, "pending_action": pl.Utf8,
    "sleeve_cash": pl.Float64, "deployed_at_checkpoint": pl.Float64,
    "available_notional": pl.Float64, "budget_notional": pl.Float64,
    "equal_share_notional": pl.Float64, "eligible_n": pl.Int64, "excluded_n": pl.Int64,
    "requested_notional": pl.Float64, "cap_headroom": pl.Float64,
    "cap_limited_notional": pl.Float64, "unfunded_notional": pl.Float64,
    "retained_notional": pl.Float64, "executed": pl.Boolean, "execution_day": pl.Utf8,
    "execution_et": pl.Int64, "execution_px": pl.Float64, "executed_notional": pl.Float64,
    "carried": pl.Boolean, "terminal_basis": pl.Utf8, "terminal_kind": pl.Utf8,
    "terminal_day": pl.Utf8, "terminal_et": pl.Int64, "terminal_px": pl.Float64,
    "terminal_reason": pl.Utf8, "terminal_value": pl.Float64,
    "tranche_cash_in": pl.Float64, "tranche_realized_net": pl.Float64,
    "tranche_marked_net": pl.Float64, "tranche_terminal_value": pl.Float64,
    "tranche_net_pnl": pl.Float64, "alloc_id": pl.Utf8, "reason": pl.Utf8,
}

_DAY_SCHEMA = {
    "run_id": pl.Utf8, "declared_id": pl.Utf8, "trigger_id": pl.Utf8, "kind": pl.Utf8,
    "L": pl.Int64, "w": pl.Int64, "g": pl.Int64, "arm": pl.Utf8, "q": pl.Float64,
    "bps": pl.Int64, "timing": pl.Utf8, "block": pl.Utf8, "date": pl.Utf8,
    "r_day": pl.Float64, "pnl": pl.Float64, "deployed_end": pl.Float64,
    "deployed_avg": pl.Float64, "n_open_end": pl.Int64, "n_actions": pl.Int64,
}

_BLOCK_SCHEMA = {
    "run_id": pl.Utf8, "declared_id": pl.Utf8, "trigger_id": pl.Utf8, "kind": pl.Utf8,
    "L": pl.Int64, "w": pl.Int64, "g": pl.Int64, "arm": pl.Utf8, "q": pl.Float64,
    "bps": pl.Int64, "timing": pl.Utf8, "block": pl.Utf8, "days_n": pl.Int64,
    "mean_basket_day": pl.Float64, "median_basket_day": pl.Float64,
    "std_basket_day": pl.Float64, "worst_day": pl.Float64,
    "positive_day_share": pl.Float64, "total_pnl": pl.Float64,
    "mean_deployed_end": pl.Float64, "n_entries": pl.Int64, "n_adds": pl.Int64,
    "n_exits": pl.Int64, "released_notional": pl.Float64,
    "executed_notional": pl.Float64, "retained_notional": pl.Float64,
    "events_n": pl.Int64, "releases_n": pl.Int64, "allocations_n": pl.Int64,
    "executed_n": pl.Int64,
}

_EFFECTS_SCHEMA = {
    "trigger_id": pl.Utf8, "kind": pl.Utf8, "L": pl.Int64, "w": pl.Int64, "g": pl.Int64,
    "bps": pl.Int64, "arm": pl.Utf8, "q": pl.Float64, "timing": pl.Utf8,
    "block": pl.Utf8, "days_n": pl.Int64,
    "h_run_id": pl.Utf8, "h_total_pnl": pl.Float64, "h_mean_basket_day": pl.Float64,
    "c_run_id": pl.Utf8, "c_total_pnl": pl.Float64, "c_mean_basket_day": pl.Float64,
    "e_run_id": pl.Utf8, "e_total_pnl": pl.Float64, "e_mean_basket_day": pl.Float64,
    "release_effect": pl.Float64, "release_effect_mean_basket_day": pl.Float64,
    "redeployment_effect": pl.Float64,
    "redeployment_effect_mean_basket_day": pl.Float64,
    "total_effect": pl.Float64, "total_effect_mean_basket_day": pl.Float64,
    "effects_add_up": pl.Boolean,
}

_POOLED_SCHEMA = {
    "declared_id": pl.Utf8, "run_id": pl.Utf8, "alias": pl.Boolean, "alias_of": pl.Utf8,
    "alias_reason": pl.Utf8, "trigger_id": pl.Utf8, "kind": pl.Utf8, "L": pl.Int64,
    "w": pl.Int64, "g": pl.Int64, "arm": pl.Utf8, "q": pl.Float64, "bps": pl.Int64,
    "timing": pl.Utf8, "status": pl.Utf8, "reused": pl.Boolean, "days_n": pl.Int64,
    "mean_basket_day": pl.Float64, "median_basket_day": pl.Float64,
    "std_basket_day": pl.Float64, "worst_day": pl.Float64, "worst_week": pl.Float64,
    "worst_month": pl.Float64, "positive_day_share": pl.Float64,
    "compounded_growth": pl.Float64, "compounded_max_dd": pl.Float64,
    "avg_deployed_capital": pl.Float64, "turnover_per_day": pl.Float64,
    "turnover_annualized": pl.Float64, "n_entries": pl.Int64, "n_adds": pl.Int64,
    "n_reduces": pl.Int64, "n_exits": pl.Int64, "n_blocked_slots": pl.Int64,
    "n_pending": pl.Int64, "n_carries": pl.Int64, "total_pnl": pl.Float64,
    "h_run_id": pl.Utf8, "h_total_pnl": pl.Float64, "c_run_id": pl.Utf8,
    "c_total_pnl": pl.Float64, "e_run_id": pl.Utf8, "e_total_pnl": pl.Float64,
    "release_effect": pl.Float64, "release_effect_mean_basket_day": pl.Float64,
    "redeployment_effect": pl.Float64,
    "redeployment_effect_mean_basket_day": pl.Float64, "total_effect": pl.Float64,
    "total_effect_mean_basket_day": pl.Float64,
    "released_notional": pl.Float64, "requested_notional": pl.Float64,
    "executed_notional": pl.Float64, "retained_notional": pl.Float64,
    "cap_limited_notional": pl.Float64, "unfunded_notional": pl.Float64,
    "tranche_net_total": pl.Float64, "tranche_delta_residual": pl.Float64,
    "tranche_delta_ok": pl.Boolean, "events_n": pl.Int64, "releases_n": pl.Int64,
    "allocations_n": pl.Int64, "executed_n": pl.Int64, "excluded_n": pl.Int64,
    "pending_excluded_n": pl.Int64, "halted_n": pl.Int64, "cap_limited_n": pl.Int64,
    "unfunded_n": pl.Int64, "no_eligible_survivor_n": pl.Int64,
    "release_to_cash_n": pl.Int64, "unrecycled_n": pl.Int64,
    "underlying_closed_n": pl.Int64,
}


# --------------------------------------------------------------------------- #
# Aggregation helpers
# --------------------------------------------------------------------------- #


def paired_effects(h_total: float | None, c_total: float | None, e_total: float | None,
                   days_n: int) -> dict:
    """``release_effect = C - H``, ``redeployment_effect = E - C``, ``total_effect = E - H``.

    Totals are sleeve-unit P&L sums over the same day set; the ``*_mean_basket_day``
    fields divide by that block's day count (``days_n``), the block-day denominator.
    """
    def mean(value: float | None) -> float | None:
        return None if value is None else float(value) / days_n

    release = None if (h_total is None or c_total is None) else float(c_total) - float(h_total)
    redeployment = None if (c_total is None or e_total is None) else float(e_total) - float(c_total)
    total = None if (h_total is None or e_total is None) else float(e_total) - float(h_total)
    return {
        "release_effect": release, "release_effect_mean_basket_day": mean(release),
        "redeployment_effect": redeployment,
        "redeployment_effect_mean_basket_day": mean(redeployment),
        "total_effect": total, "total_effect_mean_basket_day": mean(total),
        "effects_add_up": (None if total is None else
                           abs(total - (release + redeployment)) <= 1e-9 * max(1.0, abs(total))),
    }


def _event_counts(events: list[dict]) -> dict:
    def count(*statuses: str) -> int:
        return sum(1 for row in events if row["status"] in statuses)

    releases = [row for row in events if row["row_kind"] == ROW_KIND_RELEASE]
    allocations = [row for row in events if row["row_kind"] == ROW_KIND_ALLOCATION]
    return {
        "events_n": len(events),
        "releases_n": len(releases),
        "allocations_n": len(allocations),
        "executed_n": sum(1 for row in allocations if row["executed"]),
        "excluded_n": sum(1 for row in events if row["row_kind"] == ROW_KIND_EXCLUDED),
        "pending_excluded_n": count(STATUS_PENDING_EXCLUDED),
        "halted_n": count(STATUS_HALTED),
        "cap_limited_n": count(STATUS_CAP_LIMITED),
        "unfunded_n": count(STATUS_UNFUNDED),
        "no_eligible_survivor_n": count(STATUS_NO_ELIGIBLE_SURVIVOR),
        "release_to_cash_n": count(STATUS_RELEASE_TO_CASH),
        "unrecycled_n": count(STATUS_UNRECYCLED_NO_CHECKPOINT,
                              STATUS_UNRECYCLED_SAME_BAR_MISS,
                              STATUS_CROSS_SESSION_OUT_OF_SCOPE),
        "underlying_closed_n": count(STATUS_UNDERLYING_CLOSED),
    }


def _ledger_totals(events: list[dict]) -> dict:
    releases = [row for row in events if row["row_kind"] == ROW_KIND_RELEASE]
    allocations = [row for row in events if row["row_kind"] == ROW_KIND_ALLOCATION]
    return {
        "released_notional": sum(float(row["released_notional"] or 0.0) for row in releases),
        "requested_notional": sum(float(row["requested_notional"] or 0.0)
                                  for row in allocations),
        "executed_notional": sum(float(row["executed_notional"] or 0.0)
                                 for row in allocations),
        "retained_notional": sum(float(row["retained_notional"] or 0.0) for row in releases),
        "cap_limited_notional": sum(float(row["cap_limited_notional"] or 0.0)
                                    for row in allocations),
        "unfunded_notional": sum(float(row["unfunded_notional"] or 0.0)
                                 for row in allocations),
        "tranche_net_total": sum(float(row["tranche_net_pnl"] or 0.0) for row in allocations),
    }


def _surface_row(cell: Cell, status: str, metrics: dict, total_pnl: float,
                 events: list[dict], totals: dict[str, float], *, days: list[str],
                 reused: bool) -> dict:
    """One surface row for a declared cell (canonical run + alias resolution).

    ``release_effect`` / ``redeployment_effect`` / ``total_effect`` are the pooled paired
    comparisons against the same-friction HOLD (``H``) and release-to-cash (``C``) arms;
    they are null where the arm does not exist (hold rows, and the redeployment/total
    effects of the release-to-cash arm).  ``tranche_delta_residual`` compares the executed
    recycle tranches' own replay P&L with the cell-level ``redeployment_effect``: a
    non-zero residual means the ADD itself changed the recipient's later path, which is
    reported rather than hidden.
    """
    counts = _event_counts(events)
    ledger = _ledger_totals(events)
    h_total = totals.get(cell.h_run_id)
    c_total = totals.get(cell.c_run_id) if cell.c_run_id else None
    e_total = totals.get(cell.e_run_id) if cell.e_run_id else None
    effects = paired_effects(h_total, c_total, e_total, len(days))
    residual = None
    delta_ok = None
    if effects["redeployment_effect"] is not None:
        residual = ledger["tranche_net_total"] - effects["redeployment_effect"]
        delta_ok = abs(residual) <= 1e-9 * max(1.0, abs(effects["redeployment_effect"]))
    return {
        "declared_id": cell.declared_id,
        "run_id": cell.run_id,
        "alias": cell.is_alias,
        "alias_of": cell.run_id if cell.is_alias else None,
        "alias_reason": cell.alias_reason,
        "trigger_id": cell.trigger_id, "kind": cell.kind, "L": cell.L, "w": cell.w,
        "g": cell.g, "arm": cell.arm, "q": cell.q, "bps": cell.bps,
        "timing": cell.timing, "status": status, "reused": reused,
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
        "turnover_annualized": metrics["turnover_annualized"],
        "n_entries": metrics["n_entries"], "n_adds": metrics["n_adds"],
        "n_reduces": metrics["n_reduces"], "n_exits": metrics["n_exits"],
        "n_blocked_slots": metrics["n_blocked_slots"],
        "n_pending": metrics["n_pending"], "n_carries": metrics["n_carries"],
        "total_pnl": total_pnl,
        "h_run_id": cell.h_run_id, "h_total_pnl": h_total,
        "c_run_id": cell.c_run_id, "c_total_pnl": c_total,
        "e_run_id": cell.e_run_id, "e_total_pnl": e_total,
        **effects,
        **ledger,
        "tranche_delta_residual": residual, "tranche_delta_ok": delta_ok,
        **counts,
    }


def _pooled_from_surface(row: dict) -> dict:
    pooled = {name: row.get(name) for name in _POOLED_SCHEMA}
    return pooled


def _aggregate_day_rows(cell: Cell, daily: pl.DataFrame) -> list[dict]:
    rows = []
    for rec in daily.iter_rows(named=True):
        rows.append({
            "run_id": cell.run_id, "declared_id": cell.declared_id,
            "trigger_id": cell.trigger_id, "kind": cell.kind, "L": cell.L, "w": cell.w,
            "g": cell.g, "arm": cell.arm, "q": cell.q, "bps": cell.bps,
            "timing": cell.timing, "block": block_of(rec["date"]), "date": rec["date"],
            "r_day": float(rec["r_day"]), "pnl": float(rec["pnl"]),
            "deployed_end": float(rec["deployed_end"]),
            "deployed_avg": float(rec["deployed_avg"]),
            "n_open_end": int(rec["n_open_end"]), "n_actions": int(rec["n_actions"]),
        })
    return rows


def _block_stats(daily: pl.DataFrame, days: list[str]) -> dict[str, dict]:
    """``block -> {days_n, total_pnl, mean_basket_day, ...}`` for one run."""
    by_day = {rec["date"]: rec for rec in daily.iter_rows(named=True)}
    stats: dict[str, dict] = {}
    for block, block_days in dev_blocks(days).items():
        pnls = [float(by_day[day]["pnl"]) for day in block_days if day in by_day]
        if not pnls:
            continue
        stats[block] = {
            "days_n": len(pnls), "total_pnl": sum(pnls),
            "mean_basket_day": sum(pnls) / len(pnls),
            "median_basket_day": float(pl.Series(pnls).median()),
            "std_basket_day": float(pl.Series(pnls).std()) if len(pnls) > 1 else 0.0,
            "worst_day": min(pnls),
            "positive_day_share": sum(1 for value in pnls if value > 0) / len(pnls),
            "mean_deployed_end": (sum(float(by_day[day]["deployed_end"])
                                      for day in block_days if day in by_day) / len(pnls)),
        }
    return stats


def _aggregate_block_rows(cell: Cell, daily: pl.DataFrame, tickets: pl.DataFrame,
                          events: list[dict], days: list[str],
                          stats: dict[str, dict] | None = None) -> list[dict]:
    stats = _block_stats(daily, days) if stats is None else stats
    ticket_rows = tickets.to_dicts()
    rows = []
    for block, block_days in dev_blocks(days).items():
        if block not in stats:
            continue
        block_set = set(block_days)
        entries = [row for row in ticket_rows if row["sleeve_day"] in block_set]
        exits = [row for row in entries
                 if not row["open_end"]
                 and (row["exit_day"] or row["sleeve_day"]) in block_set]
        block_events = [row for row in events if row["block"] == block]
        ledger = _ledger_totals(block_events)
        rows.append({
            "run_id": cell.run_id, "declared_id": cell.declared_id,
            "trigger_id": cell.trigger_id, "kind": cell.kind, "L": cell.L, "w": cell.w,
            "g": cell.g, "arm": cell.arm, "q": cell.q, "bps": cell.bps,
            "timing": cell.timing, "block": block,
            "days_n": stats[block]["days_n"],
            "mean_basket_day": stats[block]["mean_basket_day"],
            "median_basket_day": stats[block]["median_basket_day"],
            "std_basket_day": stats[block]["std_basket_day"],
            "worst_day": stats[block]["worst_day"],
            "positive_day_share": stats[block]["positive_day_share"],
            "total_pnl": stats[block]["total_pnl"],
            "mean_deployed_end": stats[block]["mean_deployed_end"],
            "n_entries": len(entries), "n_adds": sum(int(row["n_adds"]) for row in entries),
            "n_exits": len(exits),
            "released_notional": ledger["released_notional"],
            "executed_notional": ledger["executed_notional"],
            "retained_notional": ledger["retained_notional"],
            "events_n": len(block_events),
            "releases_n": sum(1 for row in block_events
                              if row["row_kind"] == ROW_KIND_RELEASE),
            "allocations_n": sum(1 for row in block_events
                                 if row["row_kind"] == ROW_KIND_ALLOCATION),
            "executed_n": sum(1 for row in block_events if row["executed"]),
        })
    return rows


def _effects_rows(cells: list[Cell], run_stats: dict[str, dict[str, dict]],
                  days: list[str]) -> list[dict]:
    """Paired arm effects per declared path, friction and recycle fraction, per block.

    ``run_stats[run_id][block]`` is a :func:`_block_stats` entry; every arm is the same
    friction and the same day set, so the block-day denominator is shared.
    """
    def total(stats: dict | None) -> float | None:
        return float(stats["total_pnl"]) if stats else None

    def mean(stats: dict | None) -> float | None:
        return float(stats["mean_basket_day"]) if stats else None

    rows = []
    for cell in cells:
        if cell.q is None:
            continue                      # the hold arm is the reference, not a comparison
        for block in dev_blocks(days):
            h_stats = run_stats.get(cell.h_run_id, {}).get(block)
            c_stats = run_stats.get(cell.c_run_id or "", {}).get(block)
            e_stats = (run_stats.get(cell.e_run_id, {}).get(block)
                       if cell.e_run_id else None)
            days_n = max((stats["days_n"] for stats in (h_stats, c_stats, e_stats)
                          if stats), default=0)
            if not days_n:
                continue
            effects = paired_effects(total(h_stats), total(c_stats), total(e_stats), days_n)
            rows.append({
                "trigger_id": cell.trigger_id, "kind": cell.kind, "L": cell.L,
                "w": cell.w, "g": cell.g, "bps": cell.bps, "arm": cell.arm, "q": cell.q,
                "timing": cell.timing, "block": block, "days_n": days_n,
                "h_run_id": cell.h_run_id, "h_total_pnl": total(h_stats),
                "h_mean_basket_day": mean(h_stats),
                "c_run_id": cell.c_run_id, "c_total_pnl": total(c_stats),
                "c_mean_basket_day": mean(c_stats),
                "e_run_id": cell.e_run_id, "e_total_pnl": total(e_stats),
                "e_mean_basket_day": mean(e_stats),
                **effects,
            })
    return rows


# --------------------------------------------------------------------------- #
# Per-cell execution
# --------------------------------------------------------------------------- #


def is_complete(run_dir: Path) -> bool:
    return all((run_dir / name).is_file() for name in REQUIRED_OUTPUTS)


def execute_cell(cell: Cell, out_root: Path, days: list[str], workers: int) -> dict:
    """Run one canonical cell (or reuse its complete outputs).

    Returns the evidence the surface needs: metrics, the run's pooled P&L and the
    resolved recycle ledger.  The ledger is resolved in-process against the live tickets
    the policy's strategy holds, so executed-tranche P&L is exact; the durable per-cell
    tables are written here.
    """
    run_dir = out_root / "F7" / cell.run_id
    if is_complete(run_dir):
        cell_row = json.loads((run_dir / "f7_cell.json").read_text())
        if (cell_row.get("contract_version") != sim.CONTRACT_VERSION
                or cell_row.get("engine_hash") != sim._engine_hash()
                or cell_row.get("run_id") != cell.run_id):
            raise F7ContractError(f"stale F7 cell outputs for {cell.run_id}: refusing reuse")
        return {"run_id": cell.run_id, "status": "reused", "reused": True,
                "metrics": cell_row["metrics"],
                "total_pnl": _daily_total(run_dir / "daily.parquet"),
                "events": pl.read_parquet(run_dir / "recycle_events.parquet").to_dicts()}
    if run_dir.exists():
        shutil.rmtree(run_dir)
    policy = cell.build_policy()
    spec = cell.strategy(policy)
    summary = sim.run(sim.RunConfig("F7", cell.run_id, spec, float(cell.bps),
                                    out_root=out_root, days=days, workers=workers),
                      progress=False)
    events = resolve_cell_ledger(cell, policy)
    _atomic_parquet(run_dir / "recycle_events.parquet", events, _EVENT_SCHEMA)
    cell_row = {
        "family_id": "F7", "run_id": cell.run_id, "declared_id": cell.declared_id,
        "trigger_id": cell.trigger_id, "kind": cell.kind, "L": cell.L, "w": cell.w,
        "g": cell.g, "arm": cell.arm, "q": cell.q, "bps_round_trip": cell.bps,
        "timing": cell.timing, "checkpoint_ets": list(sim.CHECKPOINTS),
        "entry": {"pop": ENTRY_POP, "T": ENTRY_T, "top_n": N_SLOTS, "n_slots": N_SLOTS},
        "capital": "C0=1, no leverage, sleeve-local cash, no F5 adds, no F6 reserve",
        "release": "R0" if cell.holds else trigger_rule_name(cell.kind, cell.L, cell.w, cell.g),
        "recycle_fraction": cell.q, "add_cap_unit_notional": 1.0,
        "h_run_id": cell.h_run_id, "c_run_id": cell.c_run_id, "e_run_id": cell.e_run_id,
        "status": "ran", "days": [days[0], days[-1]] if days else [],
        "n_days": len(days), "metrics": summary["metrics"],
        "extension": EXTENSION_ID, "extension_sha256": _file_sha256(EXTENSIONS_PATH),
        "sim_contract_hash": sim._contract_hash(),
        "contract_version": sim.CONTRACT_VERSION,
        "engine_hash": sim._engine_hash(),
    }
    _atomic_json(run_dir / "f7_cell.json", cell_row)
    return {"run_id": cell.run_id, "status": "ran", "reused": False,
            "metrics": summary["metrics"],
            "total_pnl": _daily_total(run_dir / "daily.parquet"), "events": events}


def _daily_total(path: Path) -> float:
    frame = pl.read_parquet(path, columns=["pnl"])
    return float(frame["pnl"].sum())


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #


def _write_run_config(out_root: Path, days: list[str], triggers: tuple[str, ...] | None,
                      timing: str) -> None:
    """Provenance manifest: contract version, engine hash, registered grid, day manifest."""
    source_paths = (
        EXTENSIONS_PATH,
        ROOT / "factory" / "BASKET-SIM-CONTRACT.md",
        ROOT / "factory" / "scripts" / "basket_sim.py",
        ROOT / "factory" / "scripts" / "basket_f7.py",
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
        "family_id": "F7", "evidence_label": "RUN",
        "days": days, "days_n": len(days),
        "blocks": {name: {"days_n": len(block_days),
                          "first": block_days[0] if block_days else None,
                          "last": block_days[-1] if block_days else None}
                   for name, block_days in dev_blocks(days).items()},
        "registered_grid": registered_grid(triggers, timing),
        "extension": {"id": EXTENSION_ID, "file": "factory/BASKET-SIM-EXTENSIONS.md",
                      "sha256": _file_sha256(EXTENSIONS_PATH)},
        "contract_version": sim.CONTRACT_VERSION,
        "sim_contract_hash": sim._contract_hash(),
        "engine_hash": sim._engine_hash(),
        "canonical_data_sha256": data_hasher.hexdigest(),
        "provenance_sha256": provenance,
        "git_head": sim._git_head(),
        "execution": ("batch checkpoint after all ticket decisions of the completed bar; "
                      "recycle ADD executes at the recipient's next eligible bar open; no "
                      "same-bar execution; sleeve-local cash; ADD cap and cash/no-leverage "
                      "invariants remain engine-owned"),
        "released_capital": ("removed cost basis of an executed EXIT action; a pending "
                             "release, a mark, or a boundary/data-end terminal mark never "
                             "funds a recycle"),
        "paired_effects": ("release_effect = C - H, redeployment_effect = E - C, "
                           "total_effect = E - H per block; the *_mean_basket_day fields "
                           "divide by that block's day count"),
        "no_validation_claim": True,
    })


def finalize_outputs(out_root: Path, days: list[str]) -> dict:
    """Rebuild the pooled / block / day / effects aggregates from per-cell artifacts."""
    _require_extension()
    surface = json.loads((out_root / "surface.json").read_text())
    config = json.loads((out_root / "run_config.json").read_text())
    grid = config["registered_grid"]
    triggers = tuple(grid["trigger_subset"]) if grid.get("trigger_subset") else None
    timing = grid["timing"]
    cells = build_cells(triggers, timing)
    expected_rows = len(cells)
    if len(surface.get("cells", [])) != expected_rows:
        raise F7ContractError(
            f"surface.json holds {len(surface.get('cells', []))} rows, expected "
            f"{expected_rows} declared cells: refusing to publish partial aggregates")
    day_rows: list[dict] = []
    block_rows: list[dict] = []
    event_rows: list[dict] = []
    run_stats: dict[str, dict[str, dict]] = {}
    for run_id, cell in sorted(unique_runs(triggers, timing).items()):
        run_dir = out_root / "F7" / run_id
        if not is_complete(run_dir):
            raise F7ContractError(f"missing cell outputs for {run_id}")
        daily = pl.read_parquet(run_dir / "daily.parquet")
        tickets = pl.read_parquet(run_dir / "tickets.parquet")
        events = pl.read_parquet(run_dir / "recycle_events.parquet").to_dicts()
        stats = _block_stats(daily, days)
        day_rows.extend(_aggregate_day_rows(cell, daily))
        block_rows.extend(_aggregate_block_rows(cell, daily, tickets, events, days, stats))
        run_stats[run_id] = stats
        event_rows.extend(events)
    effects = _effects_rows(cells, run_stats, days)
    _atomic_parquet(out_root / "days.parquet", day_rows, _DAY_SCHEMA)
    _atomic_parquet(out_root / "blocks.parquet", block_rows, _BLOCK_SCHEMA)
    _atomic_parquet(out_root / "effects.parquet", effects, _EFFECTS_SCHEMA)
    _atomic_parquet(out_root / "pooled.parquet",
                    [_pooled_from_surface(row) for row in surface["cells"]], _POOLED_SCHEMA)
    _atomic_parquet(out_root / "recycle_events.parquet", event_rows, _EVENT_SCHEMA)
    return {"runs_n": len(unique_runs(triggers, timing)),
            "logical_cells_n": len(surface["cells"]),
            "day_rows_n": len(day_rows), "block_rows_n": len(block_rows),
            "effect_rows_n": len(effects), "event_rows_n": len(event_rows)}


def run_cells(out_root: Path, *, days: list[str], workers: int = 4, cell_workers: int = 3,
              triggers: tuple[str, ...] | None = None,
              timing: str = REGISTERED_TIMING) -> list[dict]:
    """Execute every unique cell, then rebuild the derived aggregates from the artifacts."""
    _require_extension()
    if not days:
        raise F7ContractError("F7 requires a non-empty day list")
    if cell_workers < 1:
        raise F7ContractError("cell_workers must be positive")
    cells = build_cells(triggers, timing)
    runs = sorted(unique_runs(triggers, timing).items())
    out_root.mkdir(parents=True, exist_ok=True)
    _write_run_config(out_root, days, triggers, timing)
    totals: dict[str, float] = {}
    states: dict[str, dict] = {}
    with ProcessPoolExecutor(max_workers=cell_workers) as executor:
        futures = {executor.submit(execute_cell, cell, out_root, days, workers): cell
                   for _, cell in runs}
        for index, future in enumerate(as_completed(futures), start=1):
            cell = futures[future]
            state = future.result()
            states[cell.run_id] = state
            totals[cell.run_id] = state["total_pnl"]
            print(f"F7 runs completed {index}/{len(runs)}: {cell.run_id} "
                  f"({state['status']})", flush=True)
    rows = []
    for cell in cells:
        state = states[cell.run_id]
        rows.append(_surface_row(cell, state["status"], state["metrics"],
                                 state["total_pnl"], state["events"], totals,
                                 days=days, reused=state["reused"]))
    rows.sort(key=lambda row: row["declared_id"])
    _atomic_json(out_root / "surface.json", {
        "family_id": "F7", "phase": "core", "cells": rows,
        "completed_runs": len(runs), "expected_runs": len(runs),
        "logical_cells": len(cells),
    })
    summary = finalize_outputs(out_root, days)
    print(json.dumps({"mode": "run", "out_root": str(out_root), **summary}), flush=True)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="run all 1,066 development days")
    parser.add_argument("--max-days", type=int, default=None, help="smoke-test prefix only")
    parser.add_argument("--out-root", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=4,
                        help="simulator I/O prefetch threads")
    parser.add_argument("--cell-workers", type=int, default=3,
                        help="cells executed in parallel")
    parser.add_argument("--trigger", action="append", default=None,
                        help="registered trigger id (repeatable or comma-separated); "
                             "default: all nine frozen F3 triggers")
    parser.add_argument("--timing", choices=RECYCLE_TIMINGS, default=REGISTERED_TIMING,
                        help="declared release-to-checkpoint assignment (registered: "
                             f"{REGISTERED_TIMING})")
    args = parser.parse_args(argv)
    if args.full == (args.max_days is not None):
        parser.error("provide exactly one of --full or --max-days")
    if args.max_days is not None and args.max_days < 1:
        parser.error("--max-days must be positive")
    if args.cell_workers < 1:
        parser.error("--cell-workers must be positive")
    try:
        triggers = parse_triggers(args.trigger)
    except F7ContractError as exc:
        parser.error(str(exc))
    days = sim.dev_days()
    if args.max_days is not None:
        days = days[:args.max_days]
    if args.out_root is not None:
        out_root = args.out_root
    elif triggers is not None:
        out_root = OUT_ROOT / "subset"
    elif args.full:
        out_root = OUT_ROOT
    else:
        out_root = OUT_ROOT / "smoke"
    rows = run_cells(out_root, days=days, workers=args.workers,
                     cell_workers=args.cell_workers, triggers=triggers, timing=args.timing)
    print(json.dumps({"runs": len(unique_runs(triggers, args.timing)),
                      "logical_cells": len(build_cells(triggers, args.timing)),
                      "rows": len(rows), "days": len(days), "out_root": str(out_root)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
