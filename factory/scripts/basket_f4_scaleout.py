#!/usr/bin/env python
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path

import polars as pl

try:
    from factory.scripts import basket_sim as sim
    from factory.scripts.basket_f4_metrics import attach_control_deltas, cell_metrics, tail_metrics
    from factory.scripts.basket_f4_paths import path_metrics, raw_path_map, refresh_touch_chronology
    from factory.scripts.basket_f4_paths import ticket_actions
    from factory.scripts.basket_f4_paths import ticket_chronology
    from factory.scripts.basket_f4_rules import BLOCKS, Cell, FullExitRule, PATTERNS
    from factory.scripts.basket_f4_rules import ScaleOutRule, Trigger, build_cells, build_triggers
    from factory.scripts.basket_f4_rules import F4ContractError, strategy as build_strategy
    from factory.scripts.basket_f4_rules import hold_spec, validate_day
except ImportError:
    import basket_sim as sim
    from basket_f4_metrics import attach_control_deltas, cell_metrics, tail_metrics
    from basket_f4_paths import path_metrics, raw_path_map, refresh_touch_chronology
    from basket_f4_paths import ticket_actions, ticket_chronology
    from basket_f4_rules import BLOCKS, Cell, FullExitRule, PATTERNS
    from basket_f4_rules import ScaleOutRule, Trigger, build_cells, build_triggers
    from basket_f4_rules import F4ContractError, strategy as build_strategy
    from basket_f4_rules import hold_spec, validate_day

ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / "factory/artifacts/basket/phase2/F4"


def _atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=1, sort_keys=True, allow_nan=False))
    os.replace(temp, path)


def _all_days() -> list[str]:
    days = sim.dev_days()
    for day in days:
        validate_day(day)
    block_days = {
        block: sum(lo <= day[:7] <= hi for day in days)
        for block, (lo, hi) in BLOCKS.items()
    }
    if len(days) != 1066 or block_days != {"block1": 734, "block2": 332}:
        raise F4ContractError(f"canonical day manifest mismatch: {len(days)} / {block_days}")
    if set(sim.session_end_map()) != set(days):
        raise F4ContractError("canonical calendar coverage mismatch")
    return days


def _write_provenance(out_root: Path, days: list[str]) -> None:
    def manifest(paths):
        digest = hashlib.sha256()
        for path in sorted(paths):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()

    f3_root = ROOT / "factory/artifacts/basket/phase2/F3"
    producers = ("basket_f4_scaleout.py", "basket_f4_rules.py", "basket_f4_paths.py",
                 "basket_f4_blocks.py", "basket_f4_metrics.py")
    _atomic_json(out_root / "provenance.json", {
        "label": "RUN", "producer": "factory/scripts/basket_f4_scaleout.py",
        "simulator": "factory/scripts/basket_sim.py (unchanged)",
        "contract_sha256": hashlib.sha256(sim.CONTRACT_PATH.read_bytes()).hexdigest(),
        "calendar_sha256": hashlib.sha256(sim.CAL_PATH.read_bytes()).hexdigest(),
        "producer_sha256": {name: hashlib.sha256(
            (ROOT / "factory/scripts" / name).read_bytes()).hexdigest() for name in producers},
        "f3_structural_map_provenance_sha256": hashlib.sha256(
            (f3_root / "structural_map/provenance.json").read_bytes()).hexdigest(),
        "f3_acceptance_bridge_sha256": hashlib.sha256(
            (f3_root / "acceptance_bridge.md").read_bytes()).hexdigest(),
        "day_manifest_sha256": hashlib.sha256("\n".join(days).encode()).hexdigest(),
        "anatomy_manifest_sha256": manifest([sim.ANAT_DIR / f"{day}.jsonl" for day in days]),
        "bars_manifest_sha256": manifest([sim.BARS_DIR / f"{day}.parquet" for day in days]),
        "days_n": len(days), "blocks": {"block1": 734, "block2": 332},
        "sealed_reserved_access": False,
        "basket_level_mode": "not run; see README shared dynamic-capital extension requirement",
    })


def audit_outputs(out_root: Path, days: list[str]) -> dict:  # noqa: DICT_OK — JSON coverage artifact
    surface = json.loads((out_root / "surface.json").read_text())
    cells = surface["cells"]
    expected_ids = {cell.run_id for cell in build_cells()}
    if len(cells) != 74 or {row["run_id"] for row in cells} != expected_ids:
        raise F4ContractError("F4 surface is not the exact 74-cell registered grid")
    if any(row.get("status") != "complete" or row.get("days_n") != 1066 for row in cells):
        raise F4ContractError("F4 surface contains an incomplete cell")
    months = sorted({day[:7] for day in days})
    missing = []
    action_rows = {}
    for cell in build_cells():
        run_dir = out_root / "F4" / cell.run_id
        for name in ("config.json", "daily.parquet", "tickets.parquet", "metrics.json",
                     "run_summary.json"):
            if not (run_dir / name).is_file():
                missing.append(f"{cell.run_id}/{name}")
        for kind in ("daily", "tickets"):
            parts = sorted(path.stem for path in (run_dir / "parts" / kind).glob("*.parquet"))
            if parts != months:
                missing.append(f"{cell.run_id}/{kind}_monthly_parts")
        if (run_dir / "daily.parquet").exists():
            recorded_days = pl.read_parquet(run_dir / "daily.parquet", columns=["date"])[
                "date"].cast(pl.String).to_list()
            if recorded_days != days:
                missing.append(f"{cell.run_id}/daily_coverage")
        if cell.kind in ("partial", "full_exit"):
            chronology_path = run_dir / "action_chronology.json"
            if not chronology_path.is_file():
                missing.append(f"{cell.run_id}/action_chronology")
            else:
                chronology = json.loads(chronology_path.read_text())
                if any("execution_date" not in action for ticket in chronology
                       for action in ticket.get("actions", [])):
                    missing.append(f"{cell.run_id}/action_dates")
                if len(chronology) != pl.read_parquet(run_dir / "tickets.parquet").height:
                    missing.append(f"{cell.run_id}/action_ticket_coverage")
                action_rows[cell.run_id] = sum(len(ticket.get("actions", []))
                                               for ticket in chronology)
    if missing:
        raise F4ContractError(f"F4 output audit failed: {missing[:20]}")
    if any(path.name.endswith(".tmp") for path in out_root.rglob("*")):
        raise F4ContractError("temporary artifact files remain under F4 root")
    audit = {"label": "PASS", "cells_n": len(cells), "days_n": len(days),
             "block_days": {"block1": 734, "block2": 332}, "months_n": len(months),
             "daily_and_ticket_month_parts_per_cell": len(months),
             "action_events_by_run": action_rows, "missing": [],
             "sealed_reserved_access": False}
    _atomic_json(out_root / "coverage_audit.json", audit)
    return audit


def run_cells(out_root: Path, days: list[str], workers: int = 4) -> list[dict]:
    if days != _all_days():
        raise F4ContractError("full F4 runs require exact canonical development-day set")
    out_root.mkdir(parents=True, exist_ok=True)
    paths = raw_path_map(days)
    cells = build_cells()
    _atomic_json(out_root / "configs.json", {
        "family_id": "F4", "entry": {"pop": "A_pm", "T": 570, "top_n": 2, "n_slots": 2},
        "capital": "C0=1, no leverage", "grid": [asdict(cell) for cell in cells],
        "patterns": PATTERNS, "triggers": [asdict(trigger) for trigger in build_triggers()],
        "frictions_bps_total": [100, 150], "blocks": BLOCKS,
        "cell_count": len(cells), "basket_level_mode": "not run",
    })
    rows = []
    for index, cell in enumerate(cells, start=1):
        spec = build_strategy(cell)
        run_dir = out_root / "F4" / cell.run_id
        chronology_path = run_dir / "action_chronology.json"
        meta_path = run_dir / "action_chronology.meta.json"
        if cell.kind in ("partial", "full_exit"):
            # Freshness: a stored chronology is reusable only when it was
            # produced under the current simulator contract (C1).  The pre-C1
            # form had no meta file/fingerprint and must be rebuilt.
            stored_meta = (json.loads(meta_path.read_text())
                           if meta_path.exists() else {})
            freshness = (stored_meta.get("contract_version") == sim.CONTRACT_VERSION
                         and stored_meta.get("sim_contract_hash") == sim._contract_hash())
            if not freshness and (run_dir / "run_summary.json").exists():
                chronology_path.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                (run_dir / "run_summary.json").unlink()
        summary = sim.run(sim.RunConfig("F4", cell.run_id, spec, float(cell.bps),
                                        out_root=out_root, days=days, workers=workers))
        if cell.kind in ("partial", "full_exit") and chronology_path.exists():
            chronology = json.loads(chronology_path.read_text())
            chronology = refresh_touch_chronology(chronology)
        else:
            chronology = ticket_actions(spec.release[0], cell.bps, paths, days)
            if cell.kind in ("partial", "full_exit") and len(chronology) != pl.read_parquet(
                    run_dir / "tickets.parquet").height:
                raise F4ContractError(f"missing resumable F4 action chronology for {cell.run_id}")
        rows.append(cell_metrics(cell, run_dir, days, summary["metrics"], chronology, paths))
        if cell.kind in ("partial", "full_exit"):
            _atomic_json(chronology_path, chronology)
            _atomic_json(meta_path, {"contract_version": sim.CONTRACT_VERSION,
                                     "sim_contract_hash": sim._contract_hash()})
        _atomic_json(out_root / "surface.json", {
            "family_id": "F4", "cells": attach_control_deltas(rows),
            "registered_cell_count": len(cells)})
        print(f"completed {cell.run_id} ({index}/{len(cells)})", flush=True)
    _write_provenance(out_root, days)
    audit_outputs(out_root, days)
    return attach_control_deltas(rows)


def run_canary(out_root: Path, days: list[str], workers: int = 1) -> dict:  # noqa: DICT_OK — JSON canary artifact
    sample = days[:5]
    paths = raw_path_map(sample)
    trigger = build_triggers()[0]
    cells = [Cell("hold", 100), Cell("full_exit", 100, trigger.trigger_id),
             Cell("partial", 100, trigger.trigger_id, "25_25_rest")]
    hashes = {}
    for repeat in ("first", "second"):
        for cell in cells:
            root = out_root / repeat
            spec = build_strategy(cell)
            summary = sim.run(sim.RunConfig("F4", cell.run_id, spec, cell.bps,
                                            out_root=root, days=sample, workers=workers))
            run_dir = root / "F4" / cell.run_id
            hashes[(repeat, cell.run_id)] = {
                kind: hashlib.sha256((run_dir / f"{kind}.parquet").read_bytes()).hexdigest()
                for kind in ("daily", "tickets")}
            if summary["metrics"]["days_n"] != len(sample):
                raise F4ContractError("canary day coverage failed")
            if cell.kind in ("partial", "full_exit"):
                _atomic_json(run_dir / "action_chronology.json",
                             ticket_actions(spec.release[0], cell.bps, paths, sample))
    for cell in cells:
        if hashes[("first", cell.run_id)] != hashes[("second", cell.run_id)]:
            raise F4ContractError(f"F4 deterministic canary mismatch: {cell.run_id}")
    report = {"label": "PASS", "days": sample, "cells": [cell.run_id for cell in cells],
              "hashes": {f"{repeat}/{run_id}": value
                         for (repeat, run_id), value in hashes.items()},
              "deterministic": True}
    _atomic_json(out_root / "canary.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--full", action="store_true")
    modes.add_argument("--max-days", type=int)
    modes.add_argument("--canary", action="store_true")
    parser.add_argument("--out-root", type=Path, default=OUT_ROOT)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    days = _all_days()
    if args.canary:
        print(json.dumps(run_canary(args.out_root / "canary", days, args.workers), indent=1))
        return 0
    if args.max_days is not None:
        if args.max_days < 1:
            parser.error("--max-days must be positive")
        for cell in build_cells():
            sim.run(sim.RunConfig("F4", cell.run_id, build_strategy(cell), float(cell.bps),
                                  out_root=args.out_root, days=days[:args.max_days],
                                  workers=args.workers))
        return 0
    rows = run_cells(args.out_root, days, args.workers)
    print(json.dumps({"family": "F4", "cells": len(rows),
                      "complete": sum(row["status"] == "complete" for row in rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
