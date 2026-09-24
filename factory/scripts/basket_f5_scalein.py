#!/usr/bin/env python
"""F5 causal own-ticket new-high scale-in surface; development evidence only."""
from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

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
REQUIRED_OUTPUTS = ("config.json", "daily.parquet", "tickets.parquet", "metrics.json", "run_summary.json",
                    "add_decisions.json", "add_execution_trace.json", "add_events.json")
BLOCKS = (("block1", "2021-02", "2023-12"), ("block2", "2025-02", "2026-05"))
_ENTRY_READY_CACHE: dict[tuple[str, ...], dict[tuple[str, int], dict[str, int]]] = {}


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


def is_complete(run_dir: Path, days: list[str], cell: Cell | None = None) -> bool:
    if not all((run_dir / name).is_file() for name in REQUIRED_OUTPUTS):
        return False
    expected_months = {day[:7] for day in days}
    if not all((run_dir / "parts" / kind / f"{month}.parquet").is_file()
               for kind in ("daily", "tickets") for month in expected_months):
        return False
    try:
        decisions = json.loads((run_dir / "add_decisions.json").read_text())
        events = json.loads((run_dir / "add_events.json").read_text())
        trace = json.loads((run_dir / "add_execution_trace.json").read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if not all(isinstance(value, list) for value in (decisions, events, trace)):
        return False
    if cell is not None and cell.add_sizes and not all(
            isinstance(item, dict) and "decision_et" in item and "ticker" in item
            for item in decisions):
        return False
    return True


def _clean_cell_outputs(run_dir: Path) -> None:
    for name in (*REQUIRED_OUTPUTS, "f5_config.json", "surface.json", "executed_tranches.json", "_progress.json",
                 "_progress.json.tmp"):
        (run_dir / name).unlink(missing_ok=True)
    for kind in ("daily", "tickets"):
        parts = run_dir / "parts" / kind
        if parts.is_dir():
            for path in parts.glob("*.parquet"):
                path.unlink()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=1, sort_keys=True, allow_nan=False))
    tmp.replace(path)


def _execute_cell(cell: Cell, out_root: Path, days: list[str], workers: int,
                  entry_ready: dict[str, int]) -> tuple[dict, str]:
    run_dir = out_root / "F5" / cell.run_id
    _clean_cell_outputs(run_dir)
    decisions: list[dict] = []
    trace: list[dict] = []
    cfg = sim.RunConfig("F5", cell.run_id, cell.strategy(decisions, entry_ready),
                        float(cell.bps), out_root, days=days, workers=workers)
    execute = sim._execute

    def record_execution(strat, ticket, action, px, et, reason, side, frac=None):
        if action != "ADD":
            return execute(strat, ticket, action, px, et, reason, side, frac)
        old_count = ticket.n_adds
        old_flags = len(ticket.flags)
        execute(strat, ticket, action, px, et, reason, side, frac)
        new_flags = ticket.flags[old_flags:]
        trace.append({
            "sleeve_day": ticket.sleeve_day, "ticker": ticket.ticker,
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
        "sim_contract_hash": sim._contract_hash(), "contract_version": "FROZEN-2026-09-22",
    })
    return summary, status


def run_cells(out_root: Path, *, days: list[str], workers: int = 4,
              cell_workers: int = 3) -> list[dict]:
    cells = build_cells()
    rows: list[dict] = []
    block_days = dev_blocks(days)
    _write_run_config(out_root, days)
    if cell_workers < 1:
        raise ValueError("cell_workers must be positive")
    if any(cell.add_sizes for cell in cells):
        _entry_ready_et(days, cells[0])

    with ProcessPoolExecutor(max_workers=cell_workers) as executor:
        futures = {executor.submit(_execute_cell, cell, out_root, days, workers,
                                   _entry_ready_et(days, cell)): cell for cell in cells}
        for future in as_completed(futures):
            cell = futures[future]
            summary, status = future.result()
            metrics = summary["metrics"]
            rows.append({"run_id": cell.run_id, "entry": cell.entry_id, "N": cell.n,
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
                         "blocks": {name: {"days_n": len(ds)} for name, ds in block_days.items()}})
            rows.sort(key=lambda row: row["run_id"])
            _atomic_json(out_root / "surface.json", {"family_id": "F5", "cells": rows,
                                                       "completed_cells": len(rows), "expected_cells": len(cells)})
            print(f"F5 cells completed {len(rows)}/{len(cells)}: {cell.run_id}", flush=True)
    _complete_surface(out_root, rows, days)
    validate_surface(out_root, rows, days)
    _write_readme(out_root, rows, days)
    return rows


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
        "contract_version": "FROZEN-2026-09-22", "git_head": sim._git_head(),
    })


def _complete_surface(out_root: Path, rows: list[dict], days: list[str]) -> None:
    blocks = dev_blocks(days)
    grouped: dict[tuple[str, int, str, int], dict] = {}
    for cell_row in rows:
        cell = next(c for c in build_cells() if c.run_id == cell_row["run_id"])
        key = (cell.entry_id, cell.n, cell.exit_id, cell.bps)
        grouped.setdefault(key, {})[tuple(cell.add_sizes)] = cell_row
        run_dir = out_root / "F5" / cell.run_id
        daily = pl.read_parquet(run_dir / "daily.parquet")
        tickets = pl.read_parquet(run_dir / "tickets.parquet")
        returns = dict(zip(daily["date"].to_list(), daily["pnl"].to_list()))
        cell_row["blocks"].update({name: {"days_n": len(ds),
            "mean_basket_day": (sum(float(returns[d]) for d in ds) / len(ds) if ds else None),
            "pnl": sum(float(returns[d]) for d in ds)} for name, ds in blocks.items()})
        cell_row["realized_net"] = float(tickets.filter(~pl.col("open_end"))["net"].sum() or 0.0)
        cell_row["marked_open_net"] = float(tickets.filter(pl.col("open_end"))["net"].sum() or 0.0)
        cell_row["estimated_transaction_cost_c0"] = (
            cell_row["turnover_per_day"] * cell_row["days_n"] * cell_row["bps"] / 20000.0)
        flags = [v for v in tickets["flags"].to_list() if v]
        cell_row["skipped_add_flags"] = {
            flag: sum(value.split(",").count(flag) for value in flags)
            for flag in ("add_unfunded", "add_cap_exceeded")
        }
    for cell_row in rows:
        cell = next(c for c in build_cells() if c.run_id == cell_row["run_id"])
        run_dir = out_root / "F5" / cell.run_id
        baseline = grouped[(cell.entry_id, cell.n, cell.exit_id, cell.bps)][()]
        cell_row["incremental_vs_no_add"] = {
            name: {"pnl": cell_row["blocks"][name]["pnl"] - baseline["blocks"][name]["pnl"],
                   "mean_c0_ev": cell_row["blocks"][name]["mean_basket_day"] - baseline["blocks"][name]["mean_basket_day"],
                   "denominator_days": cell_row["blocks"][name]["days_n"]}
            for name in blocks
        }
        add_dir = out_root / "F5" / cell.run_id / "add_decisions.json"
        if add_dir.exists():
            decisions = json.loads(add_dir.read_text())
        else:
            decisions = []
        cell_row["add_decision_count"] = len(decisions)
        cell_row["add_decision_et"] = {str(et): sum(d["decision_et"] == et for d in decisions)
                                        for et in sorted({d["decision_et"] for d in decisions})}
        execution_trace = json.loads((run_dir / "add_execution_trace.json").read_text())
        baseline_tickets = pl.read_parquet(
            out_root / "F5" / baseline["run_id"] / "tickets.parquet").to_dicts()
        outcome = _add_event_outcomes(decisions, execution_trace, tickets.to_dicts(),
                                      baseline_tickets, cell.bps)
        cell_row["add_events"] = {key: value for key, value in outcome.items() if key != "events"}
        cell_row["executed_tranche_ev"] = _tranche_ev_by_block(outcome["events"], blocks)
        _atomic_json(run_dir / "add_events.json", outcome["events"])
        _atomic_json(run_dir / "executed_tranches.json", [
            event for event in outcome["events"] if event["executed_status"] == "executed"])
        cell_row["add_execution_trace_count"] = len(execution_trace)
    tranche_surface = {
        str(bps): {block: {
            "n_executed_tranches": sum(
                row["executed_tranche_ev"][block]["n_executed_tranches"]
                for row in rows if row["bps"] == bps and row["add_sizes"]),
            "allocated_original_unit_notional": sum(
                row["executed_tranche_ev"][block]["allocated_original_unit_notional"]
                for row in rows if row["bps"] == bps and row["add_sizes"]),
            "incremental_net_pnl_contribution": sum(
                row["executed_tranche_ev"][block]["incremental_net_pnl_contribution"]
                for row in rows if row["bps"] == bps and row["add_sizes"]),
        } for block in blocks} for bps in (100, 150)}
    for bps_rows in tranche_surface.values():
        for stats in bps_rows.values():
            stats["net_ev_per_allocated_notional"] = (
                stats["incremental_net_pnl_contribution"] /
                stats["allocated_original_unit_notional"]
                if stats["allocated_original_unit_notional"] else None)
    _atomic_json(out_root / "surface.json", {"family_id": "F5", "cells": rows,
        "scope": {"days": len(days), "blocks": {k: len(v) for k, v in blocks.items()},
                  "registered_schedules": [list(s) for s in ADD_SCHEDULES]},
        "executed_tranche_incremental_net_ev": tranche_surface,
        "nonclaims": ["profitability", "strategy_selection", "out_of_sample_validity", "live_readiness"]})


def _tranche_ev_by_block(events: list[dict], blocks: dict[str, list[str]]) -> dict:
    result = {}
    for block, days in blocks.items():
        selected = [event for event in events if event["sleeve_day"] in set(days)
                    and event["executed_status"] == "executed"]
        allocated = sum(event["allocated_original_unit_notional"] for event in selected)
        net = sum(event["incremental_net_pnl_contribution"] for event in selected)
        result[block] = {"n_executed_tranches": len(selected),
                         "allocated_original_unit_notional": allocated,
                         "incremental_net_pnl_contribution": net,
                         "net_ev_per_allocated_notional": net / allocated if allocated else None}
    return result


def _add_event_outcomes(decisions: list[dict], execution_trace: list[dict],
                        ticket_rows: list[dict], baseline_rows: list[dict], bps: int) -> dict:
    by_day: dict[str, set[str]] = {}
    session_end = sim.session_end_map()
    for event in decisions:
        by_day.setdefault(event["sleeve_day"], set()).add(event["ticker"])
    ticket_by_key = {(row["sleeve_day"], row["ticker"]): row for row in ticket_rows}
    baseline_by_key = {(row["sleeve_day"], row["ticker"]): row for row in baseline_rows}
    unmatched_trace = list(execution_trace)
    events = []
    for day, tickers in by_day.items():
        bars = pl.read_parquet(sim.BARS_DIR / f"{day}.parquet",
                               columns=["ticker", "et", "open", "high", "close"]).filter(
                                   pl.col("et") <= session_end[day])
        for ticker in sorted(tickers):
            frame = bars.filter(pl.col("ticker") == ticker).sort("et")
            ets = frame["et"].to_list()
            opens, highs, closes = (frame[column].to_list() for column in ("open", "high", "close"))
            sources = sorted((item for item in decisions if item["sleeve_day"] == day
                              and item["ticker"] == ticker), key=lambda item: item["decision_et"])
            for idx, source in enumerate(sources):
                after = [i for i, et in enumerate(ets) if et > source["decision_et"]]
                event = dict(source)
                event["decision_event_id"] = f"{day}:{ticker}:{source['decision_et']}:{idx}"
                event["applicable_friction_bps"] = bps
                if after:
                    first = after[0]
                    future_high = max(highs[i] for i in after)
                    event.update({"next_eligible_open_et": int(ets[first]),
                                  "next_eligible_open": float(opens[first]),
                                  "tail_high_after_decision": float(future_high),
                                  "remaining_mfe_from_decision_close": float(future_high / source["decision_close"] - 1.0),
                                  "tail_mfe_from_add_open": float(future_high / opens[first] - 1.0)})
                else:
                    event.update({"next_eligible_open_et": None, "next_eligible_open": None,
                                  "tail_high_after_decision": None,
                                  "remaining_mfe_from_decision_close": None,
                                  "tail_mfe_from_add_open": None})
                execution = None
                if not source.get("skip_reason"):
                    candidates = [item for item in unmatched_trace
                                  if item["sleeve_day"] == day and item["ticker"] == ticker
                                  and abs(item["size_frac"] - source["size_frac"]) < 1e-12
                                  and item["execution_et"] > source["decision_et"]]
                    if candidates:
                        execution = min(candidates, key=lambda item: item["execution_et"])
                        unmatched_trace.remove(execution)
                        if (execution["execution_et"] != ets[first]
                                or abs(execution["execution_open"] - opens[first]) > 1e-12):
                            raise AssertionError(
                                f"ADD fill is not first later open: {day} {ticker} et={source['decision_et']}")
                ticket = ticket_by_key.get((day, ticker), {})
                baseline = baseline_by_key.get((day, ticker), {})
                if source.get("skip_reason"):
                    event.update({"executed_status": "skipped", "skip_cause": source["skip_reason"]})
                elif execution is None:
                    event.update({"executed_status": "not_executed", "skip_cause": "no_execution_trace"})
                elif not execution["executed"]:
                    event.update({"executed_status": "skipped", "skip_cause": execution["skip_cause"]})
                else:
                    event.update({"executed_status": "executed", "skip_cause": None,
                                  "execution_et": execution["execution_et"],
                                  "execution_open": execution["execution_open"],
                                  "allocated_original_unit_notional": execution["allocated_original_unit_notional"]})
                    if ticket.get("open_end"):
                        terminal_px = float(closes[-1]) if closes else None
                        exit_kind = "mark"
                    else:
                        terminal_px = ticket.get("exit_px")
                        exit_kind = "exit"
                    if terminal_px is None:
                        raise AssertionError(f"missing terminal value for executed ADD: {event['decision_event_id']}")
                    notional = event["allocated_original_unit_notional"]
                    shares = notional / (execution["execution_open"] * (1.0 + bps / 20000.0))
                    proceeds = shares * float(terminal_px) * (1.0 - (bps / 20000.0 if exit_kind == "exit" else 0.0))
                    event.update({"terminal_value_kind": exit_kind, "terminal_et": ticket.get("exit_et") if exit_kind == "exit" else int(ets[-1]),
                                  "terminal_px": float(terminal_px),
                                  "incremental_net_pnl_contribution": proceeds - notional,
                                  "matched_no_add_ticket_net": baseline.get("net"),
                                  "matched_no_add_tranche_contribution": 0.0})
                event.setdefault("execution_et", None)
                event.setdefault("execution_open", None)
                event.setdefault("allocated_original_unit_notional", None)
                event.setdefault("incremental_net_pnl_contribution", None)
                event.setdefault("matched_no_add_ticket_net", baseline.get("net"))
                event.setdefault("matched_no_add_tranche_contribution", None)
                events.append(event)
    if unmatched_trace:
        raise AssertionError(f"unmatched ADD execution traces: {unmatched_trace[:3]}")
    eligible = [event for event in events if event["next_eligible_open_et"] is not None]
    executed = [event for event in events if event["executed_status"] == "executed"]
    return {"triggered": len(events), "actions_with_later_same_day_bar": len(eligible),
            "executed_tranches": len(executed),
            "pending_entry_cash_reserved_skips": sum(e.get("skip_cause") == "pending_entry_cash_reserved" for e in events),
            "incremental_net_pnl_contribution": sum(e["incremental_net_pnl_contribution"] for e in executed),
            "allocated_original_unit_notional": sum(e["allocated_original_unit_notional"] for e in executed),
            "net_ev_per_allocated_notional": (
                sum(e["incremental_net_pnl_contribution"] for e in executed) /
                sum(e["allocated_original_unit_notional"] for e in executed) if executed else None),
            "tail_after_decision_n": sum(e["tail_high_after_decision"] is not None for e in events),
            "mean_tail_mfe_from_add_open": (sum(e["tail_mfe_from_add_open"] for e in executed) / len(executed)
                                             if executed else None),
            "mean_remaining_mfe_from_decision_close": (
                sum(e["remaining_mfe_from_decision_close"] for e in executed) / len(executed) if executed else None),
            "events": events}


def validate_surface(out_root: Path, rows: list[dict], days: list[str]) -> None:
    expected_days = set(days)
    if len(rows) != len(build_cells()) or len({row["run_id"] for row in rows}) != len(rows):
        raise AssertionError("F5 cell surface incomplete or duplicate")
    for cell in build_cells():
        run_dir = out_root / "F5" / cell.run_id
        if not is_complete(run_dir, days, cell):
            raise AssertionError(f"missing cell output: {cell.run_id}")
        daily = pl.read_parquet(run_dir / "daily.parquet", columns=["date"])["date"].to_list()
        if len(daily) != len(expected_days) or set(daily) != expected_days:
            raise AssertionError(f"daily calendar mismatch: {cell.run_id}")
        cfg = json.loads((run_dir / "f5_config.json").read_text())
        if cfg["add_sizes_original_unit_notional"] != list(cell.add_sizes):
            raise AssertionError(f"config schedule mismatch: {cell.run_id}")
        events = json.loads((run_dir / "add_events.json").read_text())
        if not isinstance(events, list):
            raise AssertionError(f"invalid event evidence: {cell.run_id}")
        if cell.add_sizes and not (run_dir / "executed_tranches.json").is_file():
            raise AssertionError(f"missing executed-tranche evidence: {cell.run_id}")
    leftovers = [str(path) for path in out_root.rglob("*")
                 if path.is_file() and (path.name.endswith(".tmp") or path.name.endswith(".parquet.tmp"))]
    if leftovers:
        raise AssertionError(f"temporary artifacts remain: {leftovers[:5]}")


def _write_readme(out_root: Path, rows: list[dict], days: list[str]) -> None:
    def metric_range(field: str, bps: int, block: str | None = None) -> tuple[float, float]:
        if field == "mean_c0_ev":
            values = [row["incremental_vs_no_add"][block][field]
                      for row in rows if row["bps"] == bps and row["add_sizes"]]
        else:
            values = [row[field] if block is None else row["blocks"][block][field]
                      for row in rows if row["bps"] == bps and row["add_sizes"]]
        return min(values), max(values)

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
            net = sum(row["executed_tranche_ev"][block]["incremental_net_pnl_contribution"]
                      for row in rows if row["bps"] == bps and row["add_sizes"])
            tranche[bps][block] = (n, capital, net, net / capital if capital else None)
    text = f"""# F5 — causal own-state scale-in continuation

**[RUN] Development-only full surface.** Producer: `factory/scripts/basket_f5_scalein.py`; focused checks: `tests/test_basket_f5_scalein.py`. Exact frozen matrix and inputs: `run_config.json`; full cell metrics: `surface.json`; per-cell simulator outputs and month shards: `F5/<run_id>/`; trigger decisions and post-decision tails: `F5/<run_id>/add_decisions.json`, `add_events.json`.

## Registered experiment

- {len(rows)} cells across {len(days)} permitted development days; blocks are 2021-02–2023-12 and 2025-02–2026-05.
- Every F1 entry/breadth/release configuration is present: five entry families × N={{2,3,4}} × hold/R1(-8,-10,-15), with matched no-add control and all seven schedules from registered {{25%,50%,100%}} sizes: `[]`, `[25]`, `[50]`, `[100]`, `[25,25]`, `[25,50]`, `[50,25]`; 100 and 150 bps round-trip friction.
- Trigger: each ticket independently requires completed-bar `high > prior running peak`, strictly, evaluated before updating the peak. No golden gate or recovery threshold is used.
- The frozen simulator retains release precedence, next-available-open execution, anatomy entries, no leverage, cash and +100% cumulative original-unit add caps; blocked/unfunded actions remain flagged. While later selected entries remain pending, an otherwise-valid early add signal is explicitly skipped to reserve their cash. Remaining tail is scored only on bars strictly after the trigger decision; add event files preserve decision and next-open timing.

## Descriptive surface [RUN]

Across all add schedules and entry/release cells, basket-day mean ranges are **{means[100][0]:.6f}–{means[100][1]:.6f} at 100 bps** and **{means[150][0]:.6f}–{means[150][1]:.6f} at 150 bps**. These portfolio outcomes are separate from the executed-tranche accounting below.

### Executed-tranche incremental net EV [RUN]

Net P&L below belongs only to actually executed ADD units; reservation skips, cap/funding skips, and signals without a next-open execution are not tranches. Allocated capital is the registered fraction of original ticket notional. Exit proceeds include the configured per-side exit friction; open-end marks are marked without an assumed exit fee. Net EV per allocated notional is net P&L contribution / executed allocated original-unit notional.

| Block | 100 bps: n / notional / net / EV | 150 bps: n / notional / net / EV |
|---|---:|---:|
| Pooled | {tranche[100]['pooled'][0]} / {tranche[100]['pooled'][1]:.6f} / {tranche[100]['pooled'][2]:.6f} / {tranche[100]['pooled'][3]} | {tranche[150]['pooled'][0]} / {tranche[150]['pooled'][1]:.6f} / {tranche[150]['pooled'][2]:.6f} / {tranche[150]['pooled'][3]} |
| Block 1 | {tranche[100]['block1'][0]} / {tranche[100]['block1'][1]:.6f} / {tranche[100]['block1'][2]:.6f} / {tranche[100]['block1'][3]} | {tranche[150]['block1'][0]} / {tranche[150]['block1'][1]:.6f} / {tranche[150]['block1'][2]:.6f} / {tranche[150]['block1'][3]} |
| Block 2 | {tranche[100]['block2'][0]} / {tranche[100]['block2'][1]:.6f} / {tranche[100]['block2'][2]:.6f} / {tranche[100]['block2'][3]} | {tranche[150]['block2'][0]} / {tranche[150]['block2'][1]:.6f} / {tranche[150]['block2'][2]:.6f} / {tranche[150]['block2'][3]} |

### Paired basket-day deltas vs matched no-add controls

| Block | 100 bps range over cells | 150 bps range over cells |
|---|---:|---:|
| Pooled | {paired[100]['pooled'][0]:.6f}–{paired[100]['pooled'][1]:.6f} | {paired[150]['pooled'][0]:.6f}–{paired[150]['pooled'][1]:.6f} |
| Block 1 | {paired[100]['block1'][0]:.6f}–{paired[100]['block1'][1]:.6f} | {paired[150]['block1'][0]:.6f}–{paired[150]['block1'][1]:.6f} |
| Block 2 | {paired[100]['block2'][0]:.6f}–{paired[100]['block2'][1]:.6f} | {paired[150]['block2'][0]:.6f}–{paired[150]['block2'][1]:.6f} |

These are correlated shared-development comparisons, not independent replications or a cell-selection rule. `surface.json` contains every cell, standard engine metrics, executed-tranche incremental net EV by block and friction, and separately paired basket-day deltas. Per-cell `add_events.json`, `add_execution_trace.json`, and `executed_tranches.json` link decision events to actual fills and terminal exit/mark contribution. Do not infer tranche profitability from MFE.

Transaction-cost estimate is explicitly reported per cell as `turnover_per_day × days × bps / 20,000`; turnover follows the simulator's executed-notional convention. It is a reporting estimate, not a substitute for the simulator's already-costed net P&L.

## Limits

Development evidence only: no profitability, strategy selection, out-of-sample validity, or live-readiness claim. Sealed/reserved dates were not loaded. Source hashes, frozen contract hash/version, exact date/block calendar, and git revision are in `run_config.json` and cell configs.
"""
    path = out_root / "README.md"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="run all canonical development days")
    parser.add_argument("--max-days", type=int, default=None, help="deterministic prefix canary")
    parser.add_argument("--out-root", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--cell-workers", type=int, default=3)
    args = parser.parse_args(argv)
    if args.full == (args.max_days is not None):
        parser.error("provide exactly one of --full or --max-days")
    days = sim.dev_days()
    if args.max_days is not None:
        if args.max_days < 1:
            parser.error("--max-days must be positive")
        days = days[:args.max_days]
    out = args.out_root or (OUT_ROOT if args.full else OUT_ROOT / "canary")
    rows = run_cells(out, days=days, workers=args.workers, cell_workers=args.cell_workers)
    print(json.dumps({"cells": len(rows), "days": len(days), "ran": sum(r["status"] == "ran" for r in rows),
                      "out_root": str(out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
