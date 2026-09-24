#!/usr/bin/env python
"""F5 causal own-ticket new-high scale-in surface; development evidence only.

Two deliberately separate paths:

* :func:`run_cells` is the only path that may execute or clean cells: it runs the
  registered 840-cell grid and then hands the finished core outputs to
  :func:`reconcile_surface`.
* :func:`reconcile_surface` (also ``--reconcile``) is manifest-driven and
  read-only on core artifacts.  It rebuilds every derived field from each cell's
  own corrected core outputs, cannot reach ``_clean_cell_outputs`` /
  ``_execute_cell`` / ``sim.run``, reuses one bounded immutable bar batch per day
  window, treats the recorded execution traces as authoritative, and publishes
  the top-level ``surface.json`` last as the commit marker.  Rerunning it over
  unchanged core outputs is byte-idempotent.

Contract: ``factory/BASKET-SIM-CONTRACT.md`` (C1, 2026-09-24 correction) and
``researches/PRE-REG-BASKET-02.md`` (F5 grid).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from collections.abc import Iterable, Iterator, Mapping, Sequence
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
OUT_ROOT = ROOT / "factory" / "artifacts" / "basket" / "phase2" / "F5"
ENTRY_SPECS = (("A_pm", "A_pm", 570), ("A_pm31", "A_pm31", 570),
               ("A_open", "A_open", 570), ("B585", "B", 585), ("B600", "B", 600))
EXIT_SPECS = (("R0", None), ("R1m8", -8), ("R1m10", -10), ("R1m15", -15))
ADD_SCHEDULES = ((), (0.25,), (0.50,), (1.00,), (0.25, 0.25), (0.25, 0.50), (0.50, 0.25))
# Core outputs come from cell execution; derived outputs only from reconciliation.
CORE_OUTPUTS = ("config.json", "f5_config.json", "daily.parquet", "tickets.parquet", "metrics.json",
                "run_summary.json", "add_decisions.json", "add_execution_trace.json")
DERIVED_OUTPUTS = ("add_events.json", "executed_tranches.json")
REQUIRED_OUTPUTS = CORE_OUTPUTS + DERIVED_OUTPUTS
BLOCKS = (("block1", "2021-02", "2023-12"), ("block2", "2025-02", "2026-05"))
RECONCILE_SCHEMA_VERSION = 1
DEFAULT_CELL_BATCH_SIZE = 12
DEFAULT_DAY_BATCH_SIZE = 32
_ENTRY_READY_CACHE: dict[tuple[str, ...], dict[tuple[str, int], dict[str, int]]] = {}
_DECISION_KEYS = ("sleeve_day", "ticker", "decision_et", "decision_high", "decision_close",
                  "prior_peak", "size_frac")
_TRACE_KEYS = ("sleeve_day", "ticker", "execution_day", "execution_et", "execution_open",
               "size_frac", "allocated_original_unit_notional", "executed", "friction_bps")
_EVENT_NULLS = {
    "execution_day": None, "execution_et": None, "execution_open": None,
    "allocated_original_unit_notional": None, "executed_status": None, "skip_cause": None,
    "terminal_value_kind": None, "terminal_day": None, "terminal_et": None, "terminal_px": None,
    "executed_tranche_net_pnl": None,
}
_BAR_NULLS = {
    "bar_evidence": "unresolved", "first_observed_later_bar_et": None,
    "first_observed_later_bar_open": None, "tail_high_after_decision": None,
    "remaining_mfe_from_decision_close": None, "tail_mfe_from_add_open": None,
}


class ReconcileError(RuntimeError):
    """Derived reconciliation refused inconsistent or uncorrected core evidence."""


class ScaleInRule(sim.ScaleInRule):
    """One staged ADD per strict own-ticket running-high continuation event."""

    name = "F5_own_strict_new_high"

    def __init__(self, sizes: tuple[float, ...], decisions: list[dict] | None = None,
                 entry_ready_et: dict[str, int] | None = None):
        if any(size not in (0.25, 0.50, 1.00) for size in sizes) or sum(sizes) > 1.0 + 1e-12:
            raise ValueError("F5 sizes must be registered and cumulative adds <= 100%")
        self.sizes = sizes
        self.decisions = decisions if decisions is not None else []
        self.entry_ready_et = entry_ready_et or {}

    def evaluate(self, tk: sim.Ticket, bar: dict, idx: int) -> dict | None:
        state = self.state(tk)
        prior_peak = float(state.setdefault("prior_peak", tk.peak))
        number = int(state.get("next_add", 0))
        triggered = float(bar["high"]) > prior_peak
        action = None
        if triggered and number < len(self.sizes):
            size = self.sizes[number]
            self.decisions.append({
                "sleeve_day": tk.sleeve_day, "ticker": tk.ticker,
                "decision_et": int(bar["et"]), "prior_peak": prior_peak,
                "decision_high": float(bar["high"]), "decision_close": float(bar["close"]),
                "size_frac": size,
            })
            if int(bar["et"]) < self.entry_ready_et.get(tk.sleeve_day, -1):
                self.decisions[-1]["skip_reason"] = "pending_entry_cash_reserved"
            else:
                state["next_add"] = number + 1
                action = {"action": "ADD", "frac": size, "reason": self.name}
        if float(bar["high"]) > prior_peak:
            state["prior_peak"] = float(bar["high"])
        return action


@dataclass(frozen=True)
class Cell:
    entry_id: str
    entry_pop: str
    entry_T: int
    n: int
    exit_id: str
    loss_pct: int | None
    add_sizes: tuple[float, ...]
    bps: int

    @property
    def run_id(self) -> str:
        schedule = "control" if not self.add_sizes else "add_" + "_".join(f"{int(x*100)}" for x in self.add_sizes)
        return f"{self.entry_id}_N{self.n}_{self.exit_id}_{schedule}_bps{self.bps}"

    def strategy(self, decisions: list[dict], entry_ready_et: dict[str, int]) -> sim.StrategySpec:
        release = [sim.R0()] if self.loss_pct is None else [sim.R1(self.loss_pct)]
        scale = [] if not self.add_sizes else [ScaleInRule(self.add_sizes, decisions, entry_ready_et)]
        return sim.StrategySpec(family_id="F5", entry_pop=self.entry_pop, entry_T=self.entry_T,
                                top_n=self.n, n_slots=self.n, release=release, scale_in=scale,
                                name=self.run_id)


def build_cells() -> list[Cell]:
    return [Cell(eid, pop, et, n, xid, loss, sizes, bps)
            for eid, pop, et in ENTRY_SPECS for n in (2, 3, 4)
            for xid, loss in EXIT_SPECS for sizes in ADD_SCHEDULES for bps in (100, 150)]


def dev_blocks(days: list[str]) -> dict[str, list[str]]:
    return {"pooled": list(days), **{name: [d for d in days if start <= d[:7] <= end]
                                      for name, start, end in BLOCKS}}


def _entry_ready_et(days: list[str], cell: Cell) -> dict[str, int]:
    day_key = tuple(days)
    if day_key not in _ENTRY_READY_CACHE:
        by_config = {(entry_id, n): {} for entry_id, _, _ in ENTRY_SPECS for n in (2, 3, 4)}
        for day in days:
            anatomy = sim.load_anatomy(day)
            for entry_id, pop, checkpoint in ENTRY_SPECS:
                snapshot = sim.snapshot_of(anatomy, pop, checkpoint)
                for n in (2, 3, 4):
                    fill_ets = []
                    if snapshot is not None:
                        for name in snapshot["names"][:n]:
                            fill = name.get("fill")
                            if fill and not fill.get("blocked"):
                                fill_ets.append(int(fill["et"]))
                    by_config[(entry_id, n)][day] = max(fill_ets, default=570)
        _ENTRY_READY_CACHE[day_key] = by_config
    return _ENTRY_READY_CACHE[day_key][(cell.entry_id, cell.n)]


# --------------------------------------------------------------------------- #
# Completeness: core inputs vs derived outputs
# --------------------------------------------------------------------------- #


def is_core_complete(run_dir: Path, days: list[str], cell: Cell | None = None) -> bool:
    """True when a cell's execution outputs (everything derived from ``sim.run``) are present."""
    if not all((run_dir / name).is_file() for name in CORE_OUTPUTS):
        return False
    expected_months = {day[:7] for day in days}
    if not all((run_dir / "parts" / kind / f"{month}.parquet").is_file()
               for kind in ("daily", "tickets") for month in expected_months):
        return False
    try:
        decisions = json.loads((run_dir / "add_decisions.json").read_text())
        trace = json.loads((run_dir / "add_execution_trace.json").read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if not all(isinstance(value, list) for value in (decisions, trace)):
        return False
    if cell is not None and cell.add_sizes and not all(
            isinstance(item, dict) and "decision_et" in item and "ticker" in item
            for item in decisions):
        return False
    return True


def is_complete(run_dir: Path, days: list[str], cell: Cell | None = None, *,
                require_derived: bool = True) -> bool:
    """Core completeness, plus (by default) reconciled derived evidence presence."""
    if not is_core_complete(run_dir, days, cell):
        return False
    if not require_derived:
        return True
    return all((run_dir / name).is_file() and (run_dir / name).stat().st_size > 0
               for name in DERIVED_OUTPUTS)


# --------------------------------------------------------------------------- #
# Canonical atomic writes
# --------------------------------------------------------------------------- #


def _canonical_json(value: object) -> str:
    return json.dumps(value, indent=1, sort_keys=True, allow_nan=False) + "\n"


def _atomic_text(path: Path, text: str) -> str:
    """Write ``text`` atomically; return the sha256 of the written bytes.

    The temporary file lives next to the target (same filesystem) and is removed
    on any failure, so a failed publication never damages the previous target.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode()
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return hashlib.sha256(data).hexdigest()


def _atomic_json(path: Path, value: object) -> str:
    return _atomic_text(path, _canonical_json(value))


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ReconcileError(f"cannot read {path}: {exc}") from exc


def _read_parquet_hashed(path: Path) -> tuple[pl.DataFrame, str]:
    data = path.read_bytes()
    return pl.read_parquet(io.BytesIO(data)), hashlib.sha256(data).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReconcileError(message)


def _unique_index(rows: Iterable[dict], what: str, owner: str = "") -> dict[tuple[str, str], dict]:
    """Index rows by the C1 ticket identity, refusing duplicate evidence."""
    index: dict[tuple[str, str], dict] = {}
    duplicates: list[tuple[str, str]] = []
    for row in rows:
        key = (str(row["sleeve_day"]), str(row["ticker"]))
        if key in index:
            duplicates.append(key)
        else:
            index[key] = row
    label = f"{owner}: " if owner else ""
    _require(not duplicates, f"{label}duplicate (sleeve_day, ticker) {what}: {sorted(duplicates)[:5]}")
    return index


def _batched(values: Iterable, size: int) -> Iterator[list]:
    batch: list = []
    for value in values:
        batch.append(value)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


# --------------------------------------------------------------------------- #
# Core execution path (the only place cells are cleaned or simulated)
# --------------------------------------------------------------------------- #


def _clean_cell_outputs(run_dir: Path) -> None:
    for name in (*REQUIRED_OUTPUTS, "surface.json", "_progress.json", "_progress.json.tmp"):
        (run_dir / name).unlink(missing_ok=True)
    for kind in ("daily", "tickets"):
        parts = run_dir / "parts" / kind
        if parts.is_dir():
            for path in parts.glob("*.parquet"):
                path.unlink()


def _execute_cell(cell: Cell, out_root: Path, days: list[str], workers: int,
                  entry_ready: dict[str, int]) -> tuple[dict, str]:
    run_dir = out_root / "F5" / cell.run_id
    _clean_cell_outputs(run_dir)
    decisions: list[dict] = []
    trace: list[dict] = []
    cfg = sim.RunConfig("F5", cell.run_id, cell.strategy(decisions, entry_ready),
                        float(cell.bps), out_root, days=days, workers=workers)
    execute = sim._execute

    def record_execution(strat, ticket, action, px, et, reason, side, frac=None, day=None):
        if action != "ADD":
            return execute(strat, ticket, action, px, et, reason, side,
                           frac=frac, day=day)
        old_count = ticket.n_adds
        old_flags = len(ticket.flags)
        execute(strat, ticket, action, px, et, reason, side, frac=frac, day=day)
        new_flags = ticket.flags[old_flags:]
        trace.append({
            "sleeve_day": ticket.sleeve_day, "ticker": ticket.ticker,
            "execution_day": day or ticket.sleeve_day,
            "execution_et": int(et), "execution_open": float(px),
            "size_frac": float(frac),
            "allocated_original_unit_notional": float(frac) * ticket.unit_notional,
            "executed": ticket.n_adds == old_count + 1,
            "skip_cause": new_flags[-1] if new_flags else None,
            "friction_bps": cell.bps,
        })

    sim._execute = record_execution
    try:
        summary = sim.run(cfg, progress=False)
    finally:
        sim._execute = execute
    _atomic_json(run_dir / "add_decisions.json", decisions)
    _atomic_json(run_dir / "add_execution_trace.json", trace)
    status = "ran"
    _atomic_json(run_dir / "f5_config.json", {
        "family_id": "F5", "run_id": cell.run_id, "entry": cell.entry_id,
        "entry_pop": cell.entry_pop, "entry_T": cell.entry_T, "N": cell.n,
        "release": cell.exit_id, "add_trigger": "ticket high > prior running peak, strict; evaluated before peak update",
        "add_sizes_original_unit_notional": list(cell.add_sizes),
        "add_cap_original_unit_notional": 1.0, "bps_round_trip": cell.bps,
        "days": [days[0], days[-1]], "n_days": len(days),
        "sim_contract_hash": sim._contract_hash(), "contract_version": sim.CONTRACT_VERSION,
    })
    return summary, status


def _core_row(cell: Cell, summary: dict, status: str, block_days: dict[str, list[str]]) -> dict:
    metrics = summary["metrics"]
    return {"run_id": cell.run_id, "entry": cell.entry_id, "N": cell.n,
            "exit": cell.exit_id, "add_sizes": list(cell.add_sizes), "bps": cell.bps,
            "status": status, "days_n": metrics["days_n"],
            "mean_basket_day": metrics["mean_basket_day"],
            "avg_deployed_capital": metrics["avg_deployed_capital"],
            "turnover_per_day": metrics["turnover_per_day"],
            "turnover_annualized": metrics["turnover_annualized"],
            "n_entries": metrics["n_entries"], "n_adds": metrics["n_adds"],
            "n_exits": metrics["n_exits"], "n_blocked_slots": metrics["n_blocked_slots"],
            "n_pending": metrics["n_pending"], "n_carries": metrics["n_carries"],
            "worst_day": metrics["worst_day"], "worst_week": metrics["worst_week"],
            "worst_month": metrics["worst_month"], "compounded_max_dd": metrics["compounded_max_dd"],
            "metrics": metrics,
            "blocks": {name: {"days_n": len(ds)} for name, ds in block_days.items()}}


def _load_core_row(cell: Cell, run_dir: Path, days: list[str],
                   block_days: dict[str, list[str]]) -> dict | None:
    """Reuse a complete, contract-current core cell.

    The registered grid is 840 cells (~4-5 h of compute); a rerun after an
    interruption or a data-coverage change must resume instead of deleting every
    finished cell.  Returns None when the cell must be (re)executed.
    """
    if not is_core_complete(run_dir, days, cell):
        return None
    try:
        f5_config = json.loads((run_dir / "f5_config.json").read_text())
        summary = json.loads((run_dir / "run_summary.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if (f5_config.get("contract_version") != sim.CONTRACT_VERSION
            or f5_config.get("sim_contract_hash") != sim._contract_hash()
            or int(f5_config.get("bps_round_trip", -1)) != int(cell.bps)
            or f5_config.get("add_sizes_original_unit_notional") != list(cell.add_sizes)
            or f5_config.get("entry") != cell.entry_id
            or int(f5_config.get("N", -1)) != cell.n
            or f5_config.get("release") != cell.exit_id
            or int(summary.get("days_run", -1)) != len(days)):
        return None
    return _core_row(cell, summary, "skipped_complete", block_days)


def run_cells(out_root: Path, *, days: list[str], workers: int = 4, cell_workers: int = 3,
              cell_batch_size: int = DEFAULT_CELL_BATCH_SIZE,
              day_batch_size: int = DEFAULT_DAY_BATCH_SIZE) -> list[dict]:
    """Execute every registered cell, then reconcile derived evidence from the core outputs."""
    cells = build_cells()
    rows: list[dict] = []
    block_days = dev_blocks(days)
    _write_run_config(out_root, days)
    if cell_workers < 1:
        raise ValueError("cell_workers must be positive")
    if any(cell.add_sizes for cell in cells):
        _entry_ready_et(days, cells[0])

    todo: list[Cell] = []
    for cell in cells:
        reused = _load_core_row(cell, out_root / "F5" / cell.run_id, days, block_days)
        if reused is None:
            todo.append(cell)
        else:
            rows.append(reused)
    if rows:
        rows.sort(key=lambda row: row["run_id"])
        _atomic_json(out_root / "surface.json", {"family_id": "F5", "phase": "core",
                                                 "cells": rows, "completed_cells": len(rows),
                                                 "expected_cells": len(cells)})
        print(f"F5 core cells reused: {len(rows)}/{len(cells)}", flush=True)

    with ProcessPoolExecutor(max_workers=cell_workers) as executor:
        futures = {executor.submit(_execute_cell, cell, out_root, days, workers,
                                   _entry_ready_et(days, cell)): cell for cell in todo}
        for future in as_completed(futures):
            cell = futures[future]
            summary, status = future.result()
            rows.append(_core_row(cell, summary, status, block_days))
            rows.sort(key=lambda row: row["run_id"])
            _atomic_json(out_root / "surface.json", {"family_id": "F5", "phase": "core",
                                                      "cells": rows, "completed_cells": len(rows),
                                                      "expected_cells": len(cells)})
            print(f"F5 cells completed {len(rows)}/{len(cells)}: {cell.run_id}", flush=True)
    return reconcile_surface(out_root, cell_batch_size=cell_batch_size, day_batch_size=day_batch_size)


def _write_run_config(out_root: Path, days: list[str]) -> None:
    source_paths = (ROOT / "researches" / "PRE-REG-BASKET-02.md",
                    ROOT / "factory" / "BASKET-SIM-CONTRACT.md",
                    ROOT / "factory" / "scripts" / "basket_sim.py",
                    ROOT / "factory" / "scripts" / "basket_f1.py",
                    ROOT / "factory" / "scripts" / "basket_f5_scalein.py",
                    ROOT / "factory" / "artifacts" / "basket" / "sip" / "phase2_session_calendar.json")
    inputs = {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in source_paths}
    data_hasher = hashlib.sha256()
    for day in days:
        sim.guard_day(day)
        for path in (sim.ANAT_DIR / f"{day}.jsonl", sim.BARS_DIR / f"{day}.parquet"):
            data_hasher.update(path.relative_to(ROOT).as_posix().encode())
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    data_hasher.update(chunk)
    _atomic_json(out_root / "run_config.json", {
        "family_id": "F5", "evidence_label": "RUN", "days": days, "days_n": len(days),
        "blocks": {name: {"days_n": len(ds), "first": ds[0] if ds else None,
                          "last": ds[-1] if ds else None} for name, ds in dev_blocks(days).items()},
        "entry_configs": [{"entry": eid, "pop": pop, "T": et} for eid, pop, et in ENTRY_SPECS],
        "N": [2, 3, 4], "release": [{"id": xid, "L": loss} for xid, loss in EXIT_SPECS],
        "add_schedules_original_unit_notional": [list(s) for s in ADD_SCHEDULES],
        "bps_round_trip": [100, 150], "cell_count": len(build_cells()),
        "add_trigger": "own-ticket strict current high > prior running peak; evaluate before peak update",
        "execution": "first available later bar open; simulator release precedence; cash funded; no leverage",
        "pending_entry_cash": "strict new-high signal before the last selected unblocked anatomy fill et is explicitly skipped; pending entry budget remains reserved",
        "provenance_sha256": inputs, "canonical_data_sha256": data_hasher.hexdigest(),
        "sim_contract_hash": sim._contract_hash(),
        "contract_version": sim.CONTRACT_VERSION, "git_head": sim._git_head(),
    })


# --------------------------------------------------------------------------- #
# Manifest preflight (days and grid come from run_config.json, never from dev_days)
# --------------------------------------------------------------------------- #


def _load_manifest(out_root: Path) -> dict:
    path = out_root / "run_config.json"
    manifest = _read_json(path)
    _require(isinstance(manifest, dict) and manifest.get("family_id") == "F5",
             f"{path} is not an F5 run manifest")
    return manifest


def _require_manifest_contract(manifest: dict, out_root: Path) -> None:
    path = out_root / "run_config.json"
    _require(manifest.get("contract_version") == sim.CONTRACT_VERSION,
             f"{path}: manifest contract_version {manifest.get('contract_version')!r} is not the "
             f"corrected contract {sim.CONTRACT_VERSION!r}; rerun the core cells before reconciling")
    _require(manifest.get("sim_contract_hash") == sim._contract_hash(),
             f"{path}: manifest sim_contract_hash {manifest.get('sim_contract_hash')!r} does not match "
             f"the corrected simulator {sim._contract_hash()!r}; preserved pre-correction F5 core "
             "outputs are not reconcilable evidence")


def _manifest_days(manifest: dict, out_root: Path) -> list[str]:
    path = out_root / "run_config.json"
    days = manifest.get("days")
    _require(isinstance(days, list) and bool(days) and all(isinstance(day, str) for day in days),
             f"{path}: 'days' must be a non-empty list of date strings")
    _require(list(days) == sorted(set(days)), f"{path}: 'days' must be unique and ascending")
    for day in days:
        try:
            sim.guard_day(day)
        except (ValueError, PermissionError) as exc:
            raise ReconcileError(f"{path}: refused manifest day: {exc}") from exc
    if "days_n" in manifest:
        _require(int(manifest["days_n"]) == len(days), f"{path}: days_n does not match days")
    declared_blocks = manifest.get("blocks")
    if declared_blocks is not None:
        for name, block_days in dev_blocks(days).items():
            declared = declared_blocks.get(name)
            _require(isinstance(declared, dict) and int(declared.get("days_n", -1)) == len(block_days),
                     f"{path}: manifest block {name} does not match the declared calendar")
            if block_days:
                _require(declared.get("first") == block_days[0] and declared.get("last") == block_days[-1],
                         f"{path}: manifest block {name} bounds do not match the declared calendar")
    return list(days)


def _manifest_int(value: object) -> int:
    """Lenient int for manifest fields; invalid values become -1 and fail validation."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return -1


def _cells_from_manifest(manifest: dict, out_root: Path) -> list[Cell]:
    """Resolve the registered grid the manifest declares (the full 840-cell grid in production)."""
    path = out_root / "run_config.json"
    registered_entries = {(eid, pop, et) for eid, pop, et in ENTRY_SPECS}
    entries = manifest.get("entry_configs")
    _require(isinstance(entries, list) and bool(entries),
             f"{path}: entry_configs must be a non-empty list")
    declared_entries = set()
    for item in entries:
        _require(isinstance(item, dict), f"{path}: entry_configs entries must be objects")
        key = (item.get("entry"), item.get("pop"), _manifest_int(item.get("T")))
        _require(key in registered_entries, f"{path}: entry config {key} is not a registered F5 entry")
        declared_entries.add(key)
    ns = {_manifest_int(value) for value in manifest.get("N", [])} if isinstance(manifest.get("N"), list) else set()
    _require(ns and ns <= {2, 3, 4}, f"{path}: N must be a non-empty subset of (2, 3, 4)")
    declared_releases = set()
    for item in manifest.get("release", []):
        _require(isinstance(item, dict), f"{path}: release entries must be objects")
        key = (item.get("id"), item.get("L"))
        _require(key in set(EXIT_SPECS), f"{path}: release {key} is not a registered F5 exit")
        declared_releases.add(key)
    _require(declared_releases, f"{path}: release must not be empty")
    declared_schedules = set()
    for item in manifest.get("add_schedules_original_unit_notional", []):
        _require(isinstance(item, list), f"{path}: add schedules must be lists")
        try:
            schedule = tuple(float(value) for value in item)
        except (TypeError, ValueError) as exc:
            raise ReconcileError(f"{path}: add schedule {item!r} is not numeric: {exc}") from exc
        _require(schedule in ADD_SCHEDULES, f"{path}: add schedule {schedule} is not registered")
        declared_schedules.add(schedule)
    _require(declared_schedules, f"{path}: add_schedules_original_unit_notional must not be empty")
    bps = {_manifest_int(value) for value in manifest.get("bps_round_trip", [])}
    _require(bps and bps <= {100, 150}, f"{path}: bps_round_trip must be a non-empty subset of (100, 150)")
    cells = [cell for cell in build_cells()
             if (cell.entry_id, cell.entry_pop, cell.entry_T) in declared_entries
             and cell.n in ns and (cell.exit_id, cell.loss_pct) in declared_releases
             and tuple(cell.add_sizes) in declared_schedules and cell.bps in bps]
    _require(cells, f"{path}: declared grid selects no registered F5 cell")
    _require(int(manifest.get("cell_count", -1)) == len(cells),
             f"{path}: cell_count {manifest.get('cell_count')!r} != {len(cells)} registered cells "
             "for the declared grid")
    return cells


def _require_tree_matches(out_root: Path, cells: list[Cell]) -> None:
    root = out_root / "F5"
    _require(root.is_dir(), f"missing F5 cell root {root}")
    found = {entry.name for entry in root.iterdir() if entry.is_dir()}
    expected = {cell.run_id for cell in cells}
    _require(found == expected,
             f"{root}: cell tree does not match the manifest grid "
             f"(missing={sorted(expected - found)[:5]} extra={sorted(found - expected)[:5]})")


def _session_ends(days: list[str], out_root: Path) -> dict[str, int]:
    if not sim.CAL_PATH.exists():
        raise ReconcileError(f"session calendar missing at {sim.CAL_PATH}; reconciliation never rebuilds it")
    try:
        calendar = sim.session_end_map()
    except Exception as exc:  # malformed committed calendar must fail loudly, never rebuild
        raise ReconcileError(f"cannot load the committed session calendar: {exc}") from exc
    missing = [day for day in days if day not in calendar]
    _require(not missing, f"session calendar is missing {len(missing)} manifest day(s): {missing[:5]}")
    return {day: int(calendar[day]) for day in days}


def _require_contract_doc(cell: Cell, name: str, doc: dict) -> None:
    _require(doc.get("contract_version") == sim.CONTRACT_VERSION,
             f"{cell.run_id}: {name} contract_version {doc.get('contract_version')!r} is not the "
             f"corrected contract {sim.CONTRACT_VERSION!r}")
    _require(doc.get("sim_contract_hash") == sim._contract_hash(),
             f"{cell.run_id}: {name} sim_contract_hash {doc.get('sim_contract_hash')!r} does not match "
             f"the corrected simulator {sim._contract_hash()!r}")


def _expected_release_name(cell: "Cell") -> str:
    """Engine rule name for a registered F5 exit (``sim.R0`` / ``sim.R1``)."""
    return "R0" if cell.loss_pct is None else f"R1({cell.loss_pct:g})"


def _validate_decisions(decisions: list, cell: Cell, days: list[str]) -> None:
    day_set = set(days)
    for position, decision in enumerate(decisions):
        _require(isinstance(decision, dict) and all(key in decision for key in _DECISION_KEYS),
                 f"{cell.run_id}: decision {position} is missing required fields")
        _require(str(decision["sleeve_day"]) in day_set,
                 f"{cell.run_id}: decision {position} sleeve_day {decision['sleeve_day']!r} is outside "
                 "the manifest calendar")
        _require(sim.FIRST_ET <= int(decision["decision_et"]) <= sim.SESSION_END_NORMAL,
                 f"{cell.run_id}: decision {position} decision_et {decision['decision_et']!r} is outside "
                 "the regular session")
        _require(any(abs(float(decision["size_frac"]) - size) < 1e-12 for size in cell.add_sizes),
                 f"{cell.run_id}: decision {position} size_frac {decision['size_frac']!r} is not in the "
                 "cell schedule")


def _validate_traces(execution_trace: list, cell: Cell, days: list[str],
                     session_end: Mapping[str, int]) -> None:
    day_set = set(days)
    for position, trace in enumerate(execution_trace):
        _require(isinstance(trace, dict) and all(key in trace for key in _TRACE_KEYS),
                 f"{cell.run_id}: execution trace {position} is missing required C1 fields")
        _require(str(trace["sleeve_day"]) in day_set,
                 f"{cell.run_id}: execution trace {position} sleeve_day {trace['sleeve_day']!r} is "
                 "outside the manifest calendar")
        execution_day = str(trace["execution_day"])
        _require(execution_day in day_set,
                 f"{cell.run_id}: execution trace {position} execution_day {execution_day!r} is outside "
                 "the manifest calendar")
        _require(sim.FIRST_ET <= int(trace["execution_et"]) <= int(session_end[execution_day]),
                 f"{cell.run_id}: execution trace {position} execution_et {trace['execution_et']!r} is "
                 "outside its session")
        _require(int(trace["friction_bps"]) == int(cell.bps),
                 f"{cell.run_id}: execution trace {position} friction {trace['friction_bps']!r} != cell "
                 f"friction {cell.bps!r}")
        _require(isinstance(trace["executed"], bool),
                 f"{cell.run_id}: execution trace {position} 'executed' must be a boolean")


def _preflight_rows(out_root: Path, cells: list[Cell], days: list[str],
                    blocks: dict[str, list[str]], session_end: Mapping[str, int]) -> list[dict]:
    """Validate every core cell before any derived write, and rebuild each surface row from it."""
    rows = []
    for cell in cells:
        run_dir = out_root / "F5" / cell.run_id
        _require(is_core_complete(run_dir, days, cell), f"{cell.run_id}: core outputs incomplete")
        f5_cfg = _read_json(run_dir / "f5_config.json")
        cfg = _read_json(run_dir / "config.json")
        metrics = _read_json(run_dir / "metrics.json")
        summary = _read_json(run_dir / "run_summary.json")
        _require_contract_doc(cell, "f5_config.json", f5_cfg)
        _require_contract_doc(cell, "config.json", cfg)
        _require(summary.get("contract_version") == sim.CONTRACT_VERSION,
                 f"{cell.run_id}: run_summary.json contract_version is not the corrected contract")
        expected_span = [days[0], days[-1]]
        _require(f5_cfg.get("run_id") == cell.run_id and f5_cfg.get("entry") == cell.entry_id
                 and f5_cfg.get("entry_pop") == cell.entry_pop and int(f5_cfg.get("entry_T", -1)) == cell.entry_T
                 and int(f5_cfg.get("N", -1)) == cell.n and f5_cfg.get("release") == cell.exit_id
                 and [float(value) for value in f5_cfg.get("add_sizes_original_unit_notional", [])]
                 == [float(value) for value in cell.add_sizes]
                 and int(f5_cfg.get("bps_round_trip", -1)) == cell.bps
                 and int(f5_cfg.get("n_days", -1)) == len(days)
                 and list(f5_cfg.get("days", [])) == expected_span,
                 f"{cell.run_id}: f5_config.json does not match the registered cell identity")
        _require(cfg.get("run_id") == cell.run_id and cfg.get("family_id") == "F5"
                 and cfg.get("strategy") == cell.run_id and cfg.get("entry_pop") == cell.entry_pop
                 and int(cfg.get("entry_T", -1)) == cell.entry_T
                 and int(cfg.get("top_n", -1)) == cell.n and int(cfg.get("n_slots", -1)) == cell.n
                 and abs(float(cfg.get("bps_total", -1)) - float(cell.bps)) < 1e-9
                 and list(cfg.get("release", [])) == [_expected_release_name(cell)]
                 and list(cfg.get("scale_in", [])) == (["F5_own_strict_new_high"] if cell.add_sizes else [])
                 and int(cfg.get("n_days", -1)) == len(days)
                 and list(cfg.get("days", [])) == expected_span,
                 f"{cell.run_id}: config.json does not match the registered cell identity")
        expected_fingerprint = sim._run_fingerprint(
            sim.RunConfig("F5", cell.run_id, cell.strategy([], {}), float(cell.bps), out_root, days=days), days)
        _require(summary.get("fingerprint") == expected_fingerprint,
                 f"{cell.run_id}: run_summary.json fingerprint does not bind this cell, contract and calendar")
        _require(int(summary.get("days_run", -1)) == len(days) and int(metrics.get("days_n", -1)) == len(days),
                 f"{cell.run_id}: metrics/summary day count does not match the manifest calendar")
        _require(summary.get("metrics") == metrics, f"{cell.run_id}: metrics.json disagrees with run_summary.json")
        daily, daily_sha = _read_parquet_hashed(run_dir / "daily.parquet")
        tickets, tickets_sha = _read_parquet_hashed(run_dir / "tickets.parquet")
        _require(summary.get("daily_sha256") == daily_sha and summary.get("tickets_sha256") == tickets_sha,
                 f"{cell.run_id}: core parquet digest does not match run_summary.json")
        _require(int(summary.get("daily_rows", -1)) == daily.height
                 and int(summary.get("ticket_rows", -1)) == tickets.height,
                 f"{cell.run_id}: core parquet row counts do not match run_summary.json")
        dates = daily["date"].to_list()
        _require(len(dates) == len(days) and set(dates) == set(days),
                 f"{cell.run_id}: daily.parquet calendar does not match the manifest")
        ticket_rows = tickets.to_dicts()
        _unique_index(ticket_rows, "ticket rows", cell.run_id)
        outside = sorted({str(row["sleeve_day"]) for row in ticket_rows} - set(days))
        _require(not outside, f"{cell.run_id}: ticket sleeve_day outside the manifest calendar: {outside[:5]}")
        decisions = _read_json(run_dir / "add_decisions.json")
        trace = _read_json(run_dir / "add_execution_trace.json")
        if cell.add_sizes:
            _validate_decisions(decisions, cell, days)
            _validate_traces(trace, cell, days, session_end)
            _pair_add_traces(decisions, trace)
        else:
            _require(not decisions and not trace,
                     f"{cell.run_id}: no-add control cell must carry empty ADD decision/trace evidence")
        returns = dict(zip(daily["date"].to_list(), daily["pnl"].to_list()))
        block_rows = {}
        for name, block_days in blocks.items():
            values = [float(returns[day]) for day in block_days]
            block_rows[name] = {"days_n": len(block_days),
                                "mean_basket_day": (sum(values) / len(block_days)) if block_days else None,
                                "pnl": sum(values)}
        flags = [value for value in tickets["flags"].to_list() if value]
        rows.append({
            "run_id": cell.run_id, "entry": cell.entry_id, "N": cell.n, "exit": cell.exit_id,
            "add_sizes": list(cell.add_sizes), "bps": cell.bps, "status": "reconciled",
            "days_n": int(metrics["days_n"]), "mean_basket_day": float(metrics["mean_basket_day"]),
            "avg_deployed_capital": float(metrics["avg_deployed_capital"]),
            "turnover_per_day": float(metrics["turnover_per_day"]),
            "turnover_annualized": float(metrics["turnover_annualized"]),
            "n_entries": int(metrics["n_entries"]), "n_adds": int(metrics["n_adds"]),
            "n_exits": int(metrics["n_exits"]), "n_blocked_slots": int(metrics["n_blocked_slots"]),
            "n_pending": int(metrics["n_pending"]), "n_carries": int(metrics["n_carries"]),
            "worst_day": metrics["worst_day"], "worst_week": metrics["worst_week"],
            "worst_month": metrics["worst_month"], "compounded_max_dd": metrics["compounded_max_dd"],
            "metrics": metrics, "blocks": block_rows,
            "realized_net": float(tickets.filter(~pl.col("open_end"))["net"].sum() or 0.0),
            "marked_open_net": float(tickets.filter(pl.col("open_end"))["net"].sum() or 0.0),
            "estimated_transaction_cost_c0": (float(metrics["turnover_per_day"]) * len(days)
                                              * cell.bps / 20000.0),
            "skipped_add_flags": {flag: sum(value.split(",").count(flag) for value in flags)
                                  for flag in ("add_unfunded", "add_cap_exceeded")},
        })
    rows.sort(key=lambda row: row["run_id"])
    cells = sorted(cells, key=lambda cell: cell.run_id)
    grouped: dict[tuple[str, int, str, int], dict] = {}
    for cell, row in zip(cells, rows):
        grouped.setdefault((cell.entry_id, cell.n, cell.exit_id, cell.bps), {})[tuple(cell.add_sizes)] = row
    for cell, row in zip(cells, rows):
        baseline = grouped[(cell.entry_id, cell.n, cell.exit_id, cell.bps)].get(())
        if baseline is None:
            # a partial (non-canonical) grid may omit the matched no-add control
            row["incremental_vs_no_add"] = {
                name: {"pnl": None, "mean_c0_ev": None, "denominator_days": row["blocks"][name]["days_n"]}
                for name in blocks}
            continue
        row["incremental_vs_no_add"] = {}
        for name in blocks:
            current, base = row["blocks"][name], baseline["blocks"][name]
            row["incremental_vs_no_add"][name] = {
                "pnl": (current["pnl"] - base["pnl"]) if current["days_n"] else None,
                "mean_c0_ev": ((current["mean_basket_day"] - base["mean_basket_day"])
                               if current["days_n"] else None),
                "denominator_days": current["days_n"]}
    return rows


# --------------------------------------------------------------------------- #
# Bounded, reused, immutable bar batches
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _DayBars:
    """Immutable projected bars for one session, shared by a whole reconcile cell batch.

    ``by_ticker`` maps ticker -> ``(ets, opens, highs, closes)`` numpy views of the
    one projected frame loaded for that day, so every decision lookup is a dict hit
    plus ``np.searchsorted`` instead of a fresh re-filter of the day frame.
    """

    day: str
    by_ticker: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]

    def ticker(self, ticker: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
        return self.by_ticker.get(ticker)


def _index_day_bars(frame: pl.DataFrame) -> dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    tickers = frame["ticker"].to_numpy()
    if tickers.size == 0:
        return {}
    ets = frame["et"].to_numpy()
    opens = frame["open"].to_numpy()
    highs = frame["high"].to_numpy()
    closes = frame["close"].to_numpy()
    starts = np.flatnonzero(tickers[1:] != tickers[:-1]) + 1
    bounds = np.concatenate(([0], starts, [tickers.size]))
    return {str(tickers[bounds[i]]): (ets[bounds[i]:bounds[i + 1]], opens[bounds[i]:bounds[i + 1]],
                                      highs[bounds[i]:bounds[i + 1]], closes[bounds[i]:bounds[i + 1]])
            for i in range(len(bounds) - 1)}


def _load_bar_batch(days: Sequence[str], session_end: Mapping[str, int]) -> dict[str, _DayBars]:
    """Read each canonical day parquet exactly once, projected and session-filtered."""
    batch: dict[str, _DayBars] = {}
    for day in days:
        sim.guard_day(day)
        _require(day in session_end, f"day {day} is not in the session calendar")
        path = sim.BARS_DIR / f"{day}.parquet"
        _require(path.exists(), f"missing canonical bar file for {day}: {path}")
        frame = pl.read_parquet(path, columns=["ticker", "et", "open", "high", "close"])
        frame = frame.filter(pl.col("et") <= int(session_end[day])).sort(["ticker", "et"])
        batch[day] = _DayBars(day, _index_day_bars(frame))
    return batch


# --------------------------------------------------------------------------- #
# Derived event reconciliation (pure consumers of preloaded bars and traces)
# --------------------------------------------------------------------------- #


def _trace_order(trace: dict) -> tuple[str, int]:
    return (str(trace["execution_day"]), int(trace["execution_et"]))


def _pair_add_traces(decisions: list[dict], execution_trace: list[dict]) -> list[int | None]:
    """Pair every ADD decision with its own execution trace, positionally per ticket.

    Engine invariant: a scheduled ADD is the ticket's only pending action and is
    resolved before that ticket can decide again, and only decisions that schedule
    an action produce a trace.  Within one ``(sleeve_day, ticker)`` ticket the first
    ``k`` scheduling decisions therefore own the ``k`` traces in chronological
    order.  Reservation skips (``skip_reason``) schedule nothing and own no trace;
    a trailing scheduled decision owns no trace when its ticker never trades again
    (the action stays pending forever) and is reported as ``not_executed``.
    """
    groups: dict[tuple[str, str], dict[str, list[int]]] = {}
    for position, decision in enumerate(decisions):
        _require(isinstance(decision, dict) and all(field in decision for field in _DECISION_KEYS),
                 f"ADD decision {position} is missing required fields")
        key = (str(decision["sleeve_day"]), str(decision["ticker"]))
        groups.setdefault(key, {"decisions": [], "traces": []})["decisions"].append(position)
    for position, trace in enumerate(execution_trace):
        _require(all(field in trace for field in _TRACE_KEYS),
                 f"execution trace {position} is missing required C1 fields")
        key = (str(trace["sleeve_day"]), str(trace["ticker"]))
        group = groups.get(key)
        _require(group is not None,
                 f"execution trace {position} for {key} has no ADD decision (unmatched trace evidence)")
        group["traces"].append(position)
    pairs: list[int | None] = [None] * len(decisions)
    for key, group in groups.items():
        ordered_traces = sorted(group["traces"], key=lambda position: _trace_order(execution_trace[position]))
        scheduled = [position for position in group["decisions"] if not decisions[position].get("skip_reason")]
        _require(len(ordered_traces) <= len(scheduled),
                 f"ticket {key}: {len(ordered_traces)} execution trace(s) for {len(scheduled)} "
                 "scheduled ADD decision(s) (unmatched trace evidence)")
        previous: dict | None = None
        for decision_position, trace_position in zip(scheduled, ordered_traces):
            decision = decisions[decision_position]
            trace = execution_trace[trace_position]
            order = _trace_order(trace)
            _require(previous is None or order > _trace_order(previous),
                     f"ticket {key}: execution traces are not in chronological order")
            _require(order[0] >= str(decision["sleeve_day"]),
                     f"ticket {key}: execution day {order[0]} precedes the sleeve day")
            if order[0] == str(decision["sleeve_day"]):
                _require(order[1] > int(decision["decision_et"]),
                         f"ticket {key}: execution et {order[1]} does not follow decision et "
                         f"{decision['decision_et']}")
            previous = trace
            pairs[decision_position] = trace_position
    return pairs


def _add_bar_evidence(event: dict, day_bars: _DayBars, session_end: Mapping[str, int]) -> None:
    """Descriptive post-decision evidence from the decision's recorded sleeve-day bars.

    This is evidence only: it never decides executed status.  When the recorded
    day/ticker/bar cannot be located (for example a decision taken on a carried
    ticket in a later session), the event keeps an explicit ``unresolved`` status
    and null descriptive fields instead of inventing a tail.
    """
    event.update(_BAR_NULLS)
    day = str(event["sleeve_day"])
    if int(event["decision_et"]) > int(session_end.get(day, -1)):
        return
    arrays = day_bars.ticker(str(event["ticker"]))
    if arrays is None:
        return
    ets, opens, highs, closes = arrays
    position = int(np.searchsorted(ets, int(event["decision_et"]), side="left"))
    if position >= len(ets) or int(ets[position]) != int(event["decision_et"]):
        return
    if (abs(float(highs[position]) - float(event["decision_high"])) > 1e-9
            or abs(float(closes[position]) - float(event["decision_close"])) > 1e-9):
        return
    if position + 1 >= len(ets):
        event["bar_evidence"] = "no_later_bar"
        return
    first_open = float(opens[position + 1])
    tail_high = float(highs[position + 1:].max())
    event.update({"bar_evidence": "recorded_sleeve_day",
                  "first_observed_later_bar_et": int(ets[position + 1]),
                  "first_observed_later_bar_open": first_open,
                  "tail_high_after_decision": tail_high,
                  "remaining_mfe_from_decision_close": tail_high / float(event["decision_close"]) - 1.0,
                  "tail_mfe_from_add_open": tail_high / first_open - 1.0})


def _fill_executed_tranche(event: dict, trace: dict, ticket: dict | None, bps: int,
                           open_end_marks: Mapping[tuple[str, str], float] | None) -> None:
    """Standalone P&L of the executed ADD unit, taken from the authoritative trace."""
    rate = int(bps) / 20000.0
    allocated = float(trace["allocated_original_unit_notional"])
    event.update({"executed_status": "executed", "skip_cause": None,
                  "execution_day": str(trace["execution_day"]),
                  "execution_et": int(trace["execution_et"]),
                  "execution_open": float(trace["execution_open"]),
                  "allocated_original_unit_notional": allocated})
    _require(ticket is not None, f"{event['decision_event_id']}: executed ADD has no ticket row")
    _require(abs(allocated - float(trace["size_frac"]) * float(ticket["unit_notional"])) < 1e-9,
             f"{event['decision_event_id']}: trace allocation does not match the ticket unit notional")
    shares = allocated / (float(trace["execution_open"]) * (1.0 + rate))
    if bool(ticket.get("open_end")):
        key = (str(event["sleeve_day"]), str(event["ticker"]))
        mark_price = (open_end_marks or {}).get(key)
        _require(mark_price is not None,
                 f"{event['decision_event_id']}: no reconstructed open-end mark for {key}")
        event.update({"terminal_value_kind": "open_end_mark", "terminal_day": None,
                      "terminal_et": None, "terminal_px": float(mark_price)})
        event["executed_tranche_net_pnl"] = shares * float(mark_price) - allocated
        return
    exit_px = ticket.get("exit_px")
    _require(exit_px is not None, f"{event['decision_event_id']}: closed ticket has no exit price")
    terminal_kind = ticket.get("terminal_kind")
    if terminal_kind:
        # Data-boundary / data-end terminal mark: a mark, not a trade, so no exit
        # friction is applied and no exit day exists (contract §13.9).
        event.update({"terminal_value_kind": str(terminal_kind).lower(), "terminal_day": None,
                      "terminal_et": None, "terminal_px": float(exit_px)})
        event["executed_tranche_net_pnl"] = shares * float(exit_px) - allocated
        return
    terminal_day = ticket.get("exit_day")
    _require(terminal_day is not None,
             f"{event['decision_event_id']}: closed ticket has no exit day for chronology")
    terminal_et = ticket.get("exit_et")
    _require(terminal_et is not None and (str(terminal_day), int(terminal_et))
             >= (str(trace["execution_day"]), int(trace["execution_et"])),
             f"{event['decision_event_id']}: ticket exit is not after the ADD execution")
    event.update({"terminal_value_kind": "exit", "terminal_day": str(terminal_day),
                  "terminal_et": int(terminal_et), "terminal_px": float(exit_px)})
    event["executed_tranche_net_pnl"] = shares * float(exit_px) * (1.0 - rate) - allocated


def _open_end_mark_prices(tickets: Mapping[tuple[str, str], dict], execution_trace: list[dict],
                          bps: int) -> dict[tuple[str, str], float]:
    """Exact per-share mark price for every open-end ticket carrying executed ADDs.

    An open ticket's contract value is its end-of-session mark, and ``Ticket.net()``
    already contains it (``net = proceeds - cash_in + mark``).  F5 releases are
    EXIT-only, so a ticket with no REDUCE has ``proceeds == 0`` and
    ``cash_in = unit_notional + sum(executed allocations)``, while
    ``shares = shares_entry + sum(add shares)``.  The engine's mark price is then
    recovered from the cell's own ticket row and its authoritative traces; nothing
    is read from another cell and no bar close is invented.
    """
    rate = int(bps) / 20000.0
    add_alloc: dict[tuple[str, str], float] = {}
    add_shares: dict[tuple[str, str], float] = {}
    for trace in execution_trace:
        if not trace["executed"]:
            continue
        key = (str(trace["sleeve_day"]), str(trace["ticker"]))
        allocated = float(trace["allocated_original_unit_notional"])
        add_alloc[key] = add_alloc.get(key, 0.0) + allocated
        add_shares[key] = add_shares.get(key, 0.0) + allocated / (
            float(trace["execution_open"]) * (1.0 + rate))
    prices: dict[tuple[str, str], float] = {}
    for key, ticket in tickets.items():
        if not bool(ticket.get("open_end")) or key not in add_alloc:
            continue
        _require(all(field in ticket for field in ("net", "unit_notional", "shares_entry", "n_reduces")),
                 f"{key}: open-end ticket row is missing mark fields")
        _require(not int(ticket.get("n_reduces", 0)),
                 f"{key}: open-end ticket with REDUCEs cannot be decomposed into tranche marks")
        shares_total = float(ticket["shares_entry"]) + add_shares[key]
        _require(shares_total > 0, f"{key}: open-end ticket has no shares")
        mark = float(ticket["net"]) + float(ticket["unit_notional"]) + add_alloc[key]
        _require(mark > 0, f"{key}: open-end ticket mark is not positive")
        prices[key] = mark / shares_total
    return prices


def _event_order(event: dict) -> tuple:
    return (str(event["sleeve_day"]), str(event["ticker"]), int(event["decision_et"]),
            float(event["size_frac"]), str(event["decision_event_id"]))


def _summarize_events(events: list[dict]) -> dict:
    executed = [event for event in events if event["executed_status"] == "executed"]
    priced = [event for event in executed if event["executed_tranche_net_pnl"] is not None]
    _require(len(priced) == len(executed),
             "every executed tranche must carry a terminal value")
    tails = [event for event in events if event["bar_evidence"] == "recorded_sleeve_day"]
    allocated = sum(float(event["allocated_original_unit_notional"]) for event in priced)
    net = sum(float(event["executed_tranche_net_pnl"]) for event in priced)
    return {
        "triggered": len(events),
        "actions_with_later_recorded_bar": len(tails),
        "bar_evidence_unresolved": sum(1 for event in events if event["bar_evidence"] == "unresolved"),
        "executed_tranches": len(executed),
        "skipped_tranches": sum(1 for event in events if event["executed_status"] == "skipped"),
        "not_executed_tranches": sum(1 for event in events if event["executed_status"] == "not_executed"),
        "pending_entry_cash_reserved_skips": sum(
            1 for event in events if event["skip_cause"] == "pending_entry_cash_reserved"),
        "allocated_original_unit_notional": allocated,
        "executed_tranche_net_pnl": net,
        "net_ev_per_allocated_notional": (net / allocated) if allocated else None,
        "tail_after_decision_n": len(tails),
        "mean_tail_mfe_from_add_open": (sum(float(event["tail_mfe_from_add_open"]) for event in tails)
                                        / len(tails)) if tails else None,
        "mean_remaining_mfe_from_decision_close": (
            sum(float(event["remaining_mfe_from_decision_close"]) for event in tails) / len(tails))
        if tails else None,
    }


def _add_event_outcomes(decisions: list[dict], execution_trace: list[dict], ticket_rows: list | Mapping,
                        bps: int, bars_by_day: Mapping[str, _DayBars],
                        session_end: Mapping[str, int], *,
                        trace_index: list[int | None] | Mapping[int, int | None] | None = None,
                        open_end_marks: Mapping[tuple[str, str], float] | None = None) -> dict:
    """Build one event per decision from preloaded bars and authoritative execution traces.

    Pure with respect to its inputs: it opens no files, mutates no bar batch and
    lets ``trace["executed"]`` alone decide tranche status.  ``trace_index`` maps a
    decision position to its paired trace position (None for reservation skips or a
    scheduled ADD that never resolved) and is precomputed per cell by
    :func:`_pair_add_traces`; when omitted it is derived here from the supplied lists.
    """
    if trace_index is None:
        trace_index = _pair_add_traces(decisions, execution_trace)
    _require(len(trace_index) == len(decisions),
             "trace pairing does not cover the supplied decision list")
    tickets = ticket_rows if isinstance(ticket_rows, Mapping) else _unique_index(ticket_rows, "ticket rows")
    events: list[dict] = []
    for position, decision in enumerate(decisions):
        day = str(decision["sleeve_day"])
        ticker = str(decision["ticker"])
        day_bars = bars_by_day.get(day)
        _require(day_bars is not None, f"no preloaded bar batch for decision day {day}")
        event = {field: decision[field] for field in _DECISION_KEYS}
        event["decision_event_id"] = f"{day}:{ticker}:{int(decision['decision_et'])}:{position}"
        event["applicable_friction_bps"] = int(bps)
        _add_bar_evidence(event, day_bars, session_end)
        skip_reason = decision.get("skip_reason")
        if skip_reason:
            event["executed_status"] = "skipped"
            event["skip_cause"] = str(skip_reason)
        else:
            trace_position = trace_index[position]
            if trace_position is None:
                # the scheduled ADD never resolved (ticker never trades again):
                # no execution trace means no tranche and no invented value
                event["executed_status"] = "not_executed"
                event["skip_cause"] = "no_execution_trace"
            else:
                trace = execution_trace[int(trace_position)]
                _require(abs(float(trace["size_frac"]) - float(event["size_frac"])) < 1e-12,
                         f"{event['decision_event_id']}: trace size_frac does not match the decision")
                _require(int(trace["friction_bps"]) == int(bps),
                         f"{event['decision_event_id']}: trace friction {trace['friction_bps']!r} != cell "
                         f"friction {bps!r}")
                if trace["executed"]:
                    _fill_executed_tranche(event, trace, tickets.get((day, ticker)), bps, open_end_marks)
                else:
                    event["executed_status"] = "skipped"
                    event["skip_cause"] = trace.get("skip_cause")
        for field, value in _EVENT_NULLS.items():
            event.setdefault(field, value)
        events.append(event)
    events.sort(key=_event_order)
    return {**_summarize_events(events), "events": events}


def _executed_tranche_ev_by_block(events: list[dict], blocks: dict[str, list[str]]) -> dict:
    """Per-block executed-tranche totals; every executed tranche has a terminal value."""
    result = {}
    for block, block_days in blocks.items():
        day_set = set(block_days)
        selected = [event for event in events
                    if event["sleeve_day"] in day_set and event["executed_status"] == "executed"]
        allocated = sum(float(event["allocated_original_unit_notional"]) for event in selected)
        net = sum(float(event["executed_tranche_net_pnl"]) for event in selected)
        result[block] = {"n_executed_tranches": len(selected),
                         "allocated_original_unit_notional": allocated,
                         "executed_tranche_net_pnl": net,
                         "net_ev_per_allocated_notional": (net / allocated) if allocated else None}
    return result


# --------------------------------------------------------------------------- #
# Reconciliation (read-only on core artifacts, publishes derived outputs atomically)
# --------------------------------------------------------------------------- #


def _load_cell_bundle(cell: Cell, out_root: Path, days: list[str],
                      session_end: Mapping[str, int]) -> dict:
    run_dir = out_root / "F5" / cell.run_id
    decisions = _read_json(run_dir / "add_decisions.json")
    execution_trace = _read_json(run_dir / "add_execution_trace.json")
    ticket_rows = pl.read_parquet(run_dir / "tickets.parquet").to_dicts()
    tickets = _unique_index(ticket_rows, "ticket rows", cell.run_id)
    _validate_decisions(decisions, cell, days)
    _validate_traces(execution_trace, cell, days, session_end)
    by_day: dict[str, list[int]] = {}
    for position, decision in enumerate(decisions):
        by_day.setdefault(str(decision["sleeve_day"]), []).append(position)
    return {"cell": cell, "decisions": decisions, "trace": execution_trace, "tickets": tickets,
            "open_end_marks": _open_end_mark_prices(tickets, execution_trace, cell.bps),
            "by_day": by_day, "trace_index": _pair_add_traces(decisions, execution_trace), "events": []}


def _reconcile_bundles(bundles: list[dict], day_batch_size: int,
                       session_end: Mapping[str, int]) -> None:
    """Derive events for one cell batch, one bounded day window at a time."""
    active_days = sorted({day for bundle in bundles for day in bundle["by_day"]})
    for window in _batched(active_days, day_batch_size):
        bars_by_day = _load_bar_batch(window, session_end)
        for bundle in bundles:
            indices = [position for day in window for position in bundle["by_day"].get(day, ())]
            if not indices:
                continue
            decisions = [bundle["decisions"][position] for position in indices]
            trace_index = {local: bundle["trace_index"][position] for local, position in enumerate(indices)}
            outcome = _add_event_outcomes(decisions, bundle["trace"], bundle["tickets"],
                                          bundle["cell"].bps, bars_by_day, session_end,
                                          trace_index=trace_index,
                                          open_end_marks=bundle["open_end_marks"])
            bundle["events"].extend(outcome["events"])


def _publish_cell_evidence(run_dir: Path, events: list[dict], executed: list[dict]) -> dict[str, str]:
    return {"add_events": _atomic_text(run_dir / "add_events.json", _canonical_json(events)),
            "executed_tranches": _atomic_text(run_dir / "executed_tranches.json", _canonical_json(executed))}


def _derived_digest(digests: Mapping[str, Mapping[str, str]]) -> str:
    payload = [[run_id, digests[run_id]["add_events"], digests[run_id]["executed_tranches"]]
               for run_id in sorted(digests)]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


def _tranche_surface(rows: list[dict], blocks: dict[str, list[str]]) -> dict:
    surface = {str(bps): {block: {
        "n_executed_tranches": sum(row["executed_tranche_ev"][block]["n_executed_tranches"]
                                   for row in rows if row["bps"] == bps and row["add_sizes"]),
        "allocated_original_unit_notional": sum(
            row["executed_tranche_ev"][block]["allocated_original_unit_notional"]
            for row in rows if row["bps"] == bps and row["add_sizes"]),
        "executed_tranche_net_pnl": sum(
            row["executed_tranche_ev"][block]["executed_tranche_net_pnl"]
            for row in rows if row["bps"] == bps and row["add_sizes"]),
    } for block in blocks} for bps in (100, 150)}
    for bps_rows in surface.values():
        for stats in bps_rows.values():
            stats["net_ev_per_allocated_notional"] = (
                stats["executed_tranche_net_pnl"] / stats["allocated_original_unit_notional"]
                if stats["allocated_original_unit_notional"] else None)
    return surface


def reconcile_surface(out_root: Path, *, cell_batch_size: int = DEFAULT_CELL_BATCH_SIZE,
                      day_batch_size: int = DEFAULT_DAY_BATCH_SIZE) -> list[dict]:
    """Rebuild and publish every derived F5 output from the corrected core outputs.

    Manifest-driven (exact days and grid from ``run_config.json``), read-only on
    core artifacts, bounded in live bars/cells, and idempotent: rerunning over
    unchanged core outputs writes byte-identical derived files and leaves every
    core hash and timestamp untouched.  The top-level ``surface.json`` is written
    last as the commit marker.
    """
    if cell_batch_size < 1 or day_batch_size < 1:
        raise ValueError("cell_batch_size and day_batch_size must be positive")
    out_root = Path(out_root)
    manifest = _load_manifest(out_root)
    _require_manifest_contract(manifest, out_root)
    days = _manifest_days(manifest, out_root)
    cells = _cells_from_manifest(manifest, out_root)
    _require_tree_matches(out_root, cells)
    session_end = _session_ends(days, out_root)
    blocks = dev_blocks(days)
    rows = _preflight_rows(out_root, cells, days, blocks, session_end)
    rows_by_id = {row["run_id"]: row for row in rows}
    digests: dict[str, dict[str, str]] = {}
    for cell in sorted((cell for cell in cells if not cell.add_sizes), key=lambda cell: cell.run_id):
        run_dir = out_root / "F5" / cell.run_id
        row = rows_by_id[cell.run_id]
        row["add_decision_count"] = 0
        row["add_decision_et"] = {}
        row["add_execution_trace_count"] = 0
        row["add_events"] = _summarize_events([])
        row["executed_tranche_ev"] = _executed_tranche_ev_by_block([], blocks)
        digests[cell.run_id] = _publish_cell_evidence(run_dir, [], [])
    add_cells = sorted((cell for cell in cells if cell.add_sizes), key=lambda cell: cell.run_id)
    for batch in _batched(add_cells, cell_batch_size):
        bundles = [_load_cell_bundle(cell, out_root, days, session_end) for cell in batch]
        _reconcile_bundles(bundles, day_batch_size, session_end)
        for bundle in bundles:
            cell = bundle["cell"]
            events = sorted(bundle["events"], key=_event_order)
            _require(len(events) == len(bundle["decisions"]),
                     f"{cell.run_id}: derived event count does not match the core decision count")
            _require(len({event["decision_event_id"] for event in events}) == len(events),
                     f"{cell.run_id}: derived decision_event_id values are not unique")
            executed = [event for event in events if event["executed_status"] == "executed"]
            _require(len(executed) == sum(1 for trace in bundle["trace"] if trace["executed"]),
                     f"{cell.run_id}: executed tranche count does not match the authoritative traces")
            row = rows_by_id[cell.run_id]
            row["add_decision_count"] = len(bundle["decisions"])
            row["add_decision_et"] = {str(et): sum(1 for event in events if event["decision_et"] == et)
                                      for et in sorted({event["decision_et"] for event in events})}
            row["add_execution_trace_count"] = len(bundle["trace"])
            row["add_events"] = {key: value for key, value in _summarize_events(events).items()
                                 if key != "events"}
            row["executed_tranche_ev"] = _executed_tranche_ev_by_block(events, blocks)
            run_dir = out_root / "F5" / cell.run_id
            digests[cell.run_id] = _publish_cell_evidence(run_dir, events, executed)
    derived_sha256 = _derived_digest(digests)
    for row in rows:
        row["derived_sha256"] = digests[row["run_id"]]
    surface = {
        "family_id": "F5",
        "phase": "reconciled",
        "reconcile_schema_version": RECONCILE_SCHEMA_VERSION,
        "source": {
            "contract_version": sim.CONTRACT_VERSION,
            "sim_contract_hash": sim._contract_hash(),
            "manifest_sha256": hashlib.sha256((out_root / "run_config.json").read_bytes()).hexdigest(),
            "derived_sha256": derived_sha256,
        },
        "scope": {"days": len(days),
                  "blocks": {name: len(block_days) for name, block_days in blocks.items()},
                  "registered_schedules": [list(schedule) for schedule in ADD_SCHEDULES],
                  "cells": len(cells), "controls": len(cells) - len(add_cells), "add_cells": len(add_cells)},
        "outputs": {"cells": len(cells),
                    "add_events": sum(row["add_events"]["triggered"] for row in rows),
                    "executed_tranches": sum(row["add_events"]["executed_tranches"] for row in rows)},
        "cells": rows,
        "executed_tranche_net_ev": _tranche_surface(rows, blocks),
        "nonclaims": ["profitability", "strategy_selection", "out_of_sample_validity", "live_readiness"],
    }
    validate_surface(out_root, rows, days, cells=cells)
    _write_readme(out_root, rows, days)
    _atomic_json(out_root / "surface.json", surface)
    return rows


def validate_surface(out_root: Path, rows: list[dict], days: list[str], *,
                     cells: list[Cell] | None = None) -> None:
    """Final gate before the top-level surface is published as the commit marker."""
    cells = cells or build_cells()
    expected_days = set(days)
    if len(rows) != len(cells) or {row["run_id"] for row in rows} != {cell.run_id for cell in cells}:
        raise AssertionError("F5 cell surface incomplete or duplicate")
    for cell in cells:
        run_dir = out_root / "F5" / cell.run_id
        if not is_complete(run_dir, days, cell, require_derived=True):
            raise AssertionError(f"missing cell output: {cell.run_id}")
        row = next(row for row in rows if row["run_id"] == cell.run_id)
        if "derived_sha256" not in row or "executed_tranche_ev" not in row:
            raise AssertionError(f"cell row is not reconciled evidence: {cell.run_id}")
        daily = pl.read_parquet(run_dir / "daily.parquet", columns=["date"])["date"].to_list()
        if len(daily) != len(expected_days) or set(daily) != expected_days:
            raise AssertionError(f"daily calendar mismatch: {cell.run_id}")
        cfg = _read_json(run_dir / "f5_config.json")
        if [float(value) for value in cfg.get("add_sizes_original_unit_notional", [])] != [
                float(value) for value in cell.add_sizes]:
            raise AssertionError(f"config schedule mismatch: {cell.run_id}")
    leftovers = [str(path) for path in out_root.rglob("*")
                 if path.is_file() and path.name.endswith(".tmp")]
    if leftovers:
        raise AssertionError(f"temporary artifacts remain: {leftovers[:5]}")


def _write_readme(out_root: Path, rows: list[dict], days: list[str]) -> None:
    def metric_range(field: str, bps: int, block: str | None = None) -> tuple[float | None, float | None]:
        if field == "mean_c0_ev":
            values = [row["incremental_vs_no_add"][block][field]
                      for row in rows if row["bps"] == bps and row["add_sizes"]]
        else:
            values = [row[field] if block is None else row["blocks"][block][field]
                      for row in rows if row["bps"] == bps and row["add_sizes"]]
        values = [value for value in values if value is not None]
        return (min(values), max(values)) if values else (None, None)

    def fmt_range(bounds: tuple[float | None, float | None]) -> str:
        return "n/a" if bounds[0] is None else f"{bounds[0]:.6f}–{bounds[1]:.6f}"

    means = {bps: metric_range("mean_basket_day", bps) for bps in (100, 150)}
    paired = {bps: {block: metric_range("mean_c0_ev", bps, block)
                    for block in ("pooled", "block1", "block2")} for bps in (100, 150)}
    tranche = {}
    for bps in (100, 150):
        tranche[bps] = {}
        for block in ("pooled", "block1", "block2"):
            n = sum(row["executed_tranche_ev"][block]["n_executed_tranches"]
                    for row in rows if row["bps"] == bps and row["add_sizes"])
            capital = sum(row["executed_tranche_ev"][block]["allocated_original_unit_notional"]
                          for row in rows if row["bps"] == bps and row["add_sizes"])
            net = sum(row["executed_tranche_ev"][block]["executed_tranche_net_pnl"]
                      for row in rows if row["bps"] == bps and row["add_sizes"])
            tranche[bps][block] = (n, capital, net, net / capital if capital else None)
    text = f"""# F5 — causal own-state scale-in continuation

**[RECONCILED] Development-only full surface.** Producer: `factory/scripts/basket_f5_scalein.py`; focused checks: `tests/test_basket_f5_scalein.py`. Every derived file here and under `F5/<run_id>/` was rebuilt by the manifest-driven reconciliation over corrected core outputs (`daily.parquet`, `tickets.parquet`, `metrics.json`, `add_decisions.json`, `add_execution_trace.json`); no cell was re-executed and no core artifact was modified. Exact frozen matrix and inputs: `run_config.json`; full cell metrics: `surface.json`; per-cell simulator outputs and month shards: `F5/<run_id>/`; trigger decisions: `F5/<run_id>/add_decisions.json`; authoritative fills: `F5/<run_id>/add_execution_trace.json`; reconciled events: `F5/<run_id>/add_events.json`, `F5/<run_id>/executed_tranches.json`.

## Registered experiment

- {len(rows)} cells across {len(days)} permitted development days; blocks are 2021-02–2023-12 and 2025-02–2026-05.
- Every F1 entry/breadth/release configuration is present: five entry families × N={{2,3,4}} × hold/R1(-8,-10,-15), with matched no-add control and all seven schedules from registered {{25%,50%,100%}} sizes: `[]`, `[25]`, `[50]`, `[100]`, `[25,25]`, `[25,50]`, `[50,25]`; 100 and 150 bps round-trip friction.
- Trigger: each ticket independently requires completed-bar `high > prior running peak`, strictly, evaluated before updating the peak. No golden gate or recovery threshold is used.
- The frozen simulator retains release precedence, next-available-open execution, anatomy entries, no leverage, cash and +100% cumulative original-unit add caps; blocked/unfunded actions remain flagged. While later selected entries remain pending, an otherwise-valid early add signal is explicitly skipped to reserve their cash.

## Reconciliation semantics [RECONCILED]

- Executed status comes only from `add_execution_trace.json` (`executed`). Reservation skips and unfunded/cap-skipped attempts are `skipped`; a scheduled ADD whose ticker never trades again leaves its action pending forever, has no trace, and is `not_executed`. None of those carry allocation or P&L.
- `first_observed_later_bar_*`, `tail_high_after_decision` and the tail MFE fields are descriptive evidence read from the decision's recorded sleeve-day bars; when that day/ticker/bar cannot be located they stay null with `bar_evidence="unresolved"` (counted per cell as `bar_evidence_unresolved`).
- An executed tranche's `executed_tranche_net_pnl` is the standalone P&L of that executed ADD unit: entry at the trace's execution open with entry friction, terminal at the same cell's ticket exit price with exit friction, or, for open-end tickets, at the end-of-run mark price reconstructed exactly from that ticket's own row (F5 releases are EXIT-only, so `mark = net + unit_notional + executed allocations` over `shares_entry + executed add shares`; `BLOCK_BOUNDARY_MARK` tickets use their recorded mark price without friction). It is not a causal incremental effect.
- Open-end marks are per-share mark prices implied by the same cell's ticket row; no bar close from another session is substituted.

## Descriptive surface [RECONCILED]

Across all add schedules and entry/release cells, basket-day mean ranges are **{fmt_range(means[100])} at 100 bps** and **{fmt_range(means[150])} at 150 bps**. These portfolio outcomes are separate from the executed-tranche accounting below.

### Executed-tranche net P&L [RECONCILED]

Net P&L below belongs only to actually executed ADD units; reservation skips, cap/funding skips, and signals without an authoritative execution trace are not tranches. Allocated capital is the registered fraction of original ticket notional recorded on the execution trace. Net EV per allocated notional is executed-tranche net P&L / executed allocated original-unit notional.

| Block | 100 bps: n / notional / net / EV | 150 bps: n / notional / net / EV |
|---|---:|---:|
| Pooled | {tranche[100]['pooled'][0]} / {tranche[100]['pooled'][1]:.6f} / {tranche[100]['pooled'][2]:.6f} / {tranche[100]['pooled'][3]} | {tranche[150]['pooled'][0]} / {tranche[150]['pooled'][1]:.6f} / {tranche[150]['pooled'][2]:.6f} / {tranche[150]['pooled'][3]} |
| Block 1 | {tranche[100]['block1'][0]} / {tranche[100]['block1'][1]:.6f} / {tranche[100]['block1'][2]:.6f} / {tranche[100]['block1'][3]} | {tranche[150]['block1'][0]} / {tranche[150]['block1'][1]:.6f} / {tranche[150]['block1'][2]:.6f} / {tranche[150]['block1'][3]} |
| Block 2 | {tranche[100]['block2'][0]} / {tranche[100]['block2'][1]:.6f} / {tranche[100]['block2'][2]:.6f} / {tranche[100]['block2'][3]} | {tranche[150]['block2'][0]} / {tranche[150]['block2'][1]:.6f} / {tranche[150]['block2'][2]:.6f} / {tranche[150]['block2'][3]} |

### Paired basket-day deltas vs matched no-add controls

| Block | 100 bps range over cells | 150 bps range over cells |
|---|---:|---:|
| Pooled | {fmt_range(paired[100]['pooled'])} | {fmt_range(paired[150]['pooled'])} |
| Block 1 | {fmt_range(paired[100]['block1'])} | {fmt_range(paired[150]['block1'])} |
| Block 2 | {fmt_range(paired[100]['block2'])} | {fmt_range(paired[150]['block2'])} |

These cell-level deltas are correlated shared-development comparisons, not independent replications or a cell-selection rule; they are the only paired comparison reported here and are kept separate from executed-tranche net P&L. `surface.json` contains every cell, standard engine metrics, executed-tranche net P&L by block and friction, the reconciliation schema version, the manifest/source digests and per-cell derived digests. Per-cell `add_events.json`, `add_execution_trace.json`, and `executed_tranches.json` link decision events to actual fills and terminal exit/mark contribution. Do not infer tranche profitability from MFE.

Transaction-cost estimate is explicitly reported per cell as `turnover_per_day × days × bps / 20,000`; turnover follows the simulator's executed-notional convention. It is a reporting estimate, not a substitute for the simulator's already-costed net P&L.

## Limits

Development evidence only: no profitability, strategy selection, out-of-sample validity, or live-readiness claim. Sealed/reserved dates were not loaded. Source hashes, frozen contract hash/version, exact date/block calendar, and git revision are in `run_config.json` and cell configs.
"""
    _atomic_text(out_root / "README.md", text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--full", action="store_true", help="run all canonical development days")
    mode.add_argument("--max-days", type=int, default=None, help="deterministic prefix canary")
    mode.add_argument("--reconcile", action="store_true",
                      help="rebuild derived evidence from existing core outputs (never executes cells)")
    parser.add_argument("--out-root", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--cell-workers", type=int, default=None)
    parser.add_argument("--cell-batch-size", type=int, default=DEFAULT_CELL_BATCH_SIZE)
    parser.add_argument("--day-batch-size", type=int, default=DEFAULT_DAY_BATCH_SIZE)
    args = parser.parse_args(argv)
    if args.reconcile:
        if args.workers is not None or args.cell_workers is not None:
            parser.error("--workers/--cell-workers are run-only flags; reconciliation never executes cells")
        out = args.out_root or OUT_ROOT
        rows = reconcile_surface(out, cell_batch_size=args.cell_batch_size,
                                 day_batch_size=args.day_batch_size)
        days = _manifest_days(_load_manifest(out), out)
        print(json.dumps({"mode": "reconcile", "out_root": str(out), "cells": len(rows),
                          "days": len(days),
                          "add_cells": sum(1 for row in rows if row["add_sizes"]),
                          "controls": sum(1 for row in rows if not row["add_sizes"]),
                          "executed_tranches": sum(row["add_events"]["executed_tranches"] for row in rows)}))
        return 0
    days = sim.dev_days()
    if args.max_days is not None:
        if args.max_days < 1:
            parser.error("--max-days must be positive")
        days = days[:args.max_days]
    out = args.out_root or (OUT_ROOT if args.full else OUT_ROOT / "canary")
    rows = run_cells(out, days=days, workers=args.workers or 4, cell_workers=args.cell_workers or 3,
                     cell_batch_size=args.cell_batch_size, day_batch_size=args.day_batch_size)
    print(json.dumps({"mode": "run", "cells": len(rows), "days": len(days), "out_root": str(out),
                      "reconciled": sum(row["status"] == "reconciled" for row in rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
