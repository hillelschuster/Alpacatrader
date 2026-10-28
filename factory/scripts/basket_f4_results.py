#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve()
FACTORY = HERE.parents[1]
sys.path.insert(0, str(FACTORY / "scripts"))

import basket_sim as bs  # noqa: E402
from basket_f4_analysis import (  # noqa: E402
    BLOCKS,
    H_LEVELS,
    block_name,
    daily_surfaces,
    false_release,
    load_raw_paths,
    replay_ticket,
)
from basket_f4_report import write_reports  # noqa: E402
from basket_f4_scaleout import RUNS, registry  # noqa: E402

OUT = RUNS.parent
REQUIRED = (
    "config.json", "metrics.json", "daily.parquet", "tickets.parquet",
    "tickets_actions.jsonl", "_progress.json", "run_summary.json", "surface.json",
)


def read_json(path: Path):
    return json.loads(path.read_text())


def write_json(name: str, rows: list[dict] | dict) -> None:
    (OUT / name).write_text(json.dumps(rows, indent=1, sort_keys=True) + "\n")


def write_csv(name: str, rows: list[dict]) -> None:
    with (OUT / name).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def tranche_rows(groups: dict, run_id: str, entries: int, days: int) -> list[dict]:
    rows = []
    for (scope, label), values in sorted(groups.items()):
        rows.append({
            "run_id": run_id,
            "scope": scope,
            "label": label,
            "pnl_sum": values["pnl"],
            "triggered_tickets": len(values["tickets"]),
            "executions": values["executions"],
            "mean_per_triggered_ticket": values["pnl"] / len(values["tickets"]),
            "mean_per_entry": values["pnl"] / entries,
            "mean_per_day": values["pnl"] / days,
            "notional_removed": values["notional"],
        })
    return rows


def analyze_cell(cell: dict, raw_paths: dict, dev_days: list[str]) -> tuple[dict, list[dict], list[dict], list[dict], dict]:
    run_id = cell["run_id"]
    run_dir = RUNS / run_id
    cfg = read_json(run_dir / "config.json")
    metrics = read_json(run_dir / "metrics.json")
    progress = read_json(run_dir / "_progress.json")
    daily = pl.scan_parquet(run_dir / "daily.parquet").collect().to_dicts()
    blocks, months = daily_surfaces(daily)
    groups = defaultdict(lambda: {"pnl": 0.0, "notional": 0.0, "executions": 0, "tickets": set()})
    false_counts = defaultdict(lambda: {"touches": 0, "flat": 0, "half": 0, "sold": 0.0})
    tails: list[float] = []
    replay_bad = 0
    replay_max_diff = 0.0
    missing_paths = 0
    ticket_net = 0.0
    entries = 0
    reduces = 0
    exits = 0
    side = float(cfg["bps_total"]) / 2.0 / 10000.0
    with (run_dir / "tickets_actions.jsonl").open() as handle:
        for line in handle:
            row = json.loads(line)
            entries += 1
            reduces += int(row["n_reduces"])
            exits += int(not row["open_end"])
            ticket_net += float(row["net"])
            path = raw_paths.get((cell["entry"], row["sleeve_day"], row["ticker"]))
            if path is None:
                missing_paths += 1
                continue
            tranches, replayed = replay_ticket(row, side)
            difference = abs(replayed - float(row["net"]))
            replay_max_diff = max(replay_max_diff, difference)
            replay_bad += int(difference > 1e-9)
            ticket_key = f"{row['sleeve_day']}:{row['ticker']}"
            scope = block_name(row["sleeve_day"])
            for tranche in tranches:
                for bucket in ("all", scope):
                    group = groups[(bucket, tranche["label"])]
                    group["pnl"] += tranche["pnl"]
                    group["notional"] += tranche["notional"]
                    group["executions"] += 1
                    group["tickets"].add(ticket_key)
            for level, values in false_release(row, path).items():
                count = false_counts[level]
                count["touches"] += 1
                count["flat"] += int(values["flat"])
                count["half"] += int(values["half"])
                count["sold"] += values["sold_fraction"]
            raw_mfe = float(path["mfe"])
            if raw_mfe >= 0.50:
                tails.append(float(row["net"]) / float(row["unit_notional"]) / raw_mfe)
    false_rows = []
    for level in H_LEVELS:
        count = false_counts[level]
        false_rows.append({
            "run_id": run_id, "entry": cell["entry"], "base": cell["base"],
            "pattern": cell["pattern"], "kind": cell["kind"], "N": cell["N"],
            "H": level, "n_touch": count["touches"],
            "flat_before_rate": count["flat"] / count["touches"],
            "half_sold_before_rate": count["half"] / count["touches"],
            "mean_sold_fraction": count["sold"] / count["touches"],
        })
    b1 = blocks["block1"]
    b2 = blocks["block2"]
    status = "invalid_derisk" if cell["kind"] == "derisk" else "valid_as_run"
    matrix = {
        "run_id": run_id, **cell, "status": status,
        "net100_mean": metrics["mean_basket_day"],
        "ci_lo": metrics["bootstrap_ci95"][0], "ci_hi": metrics["bootstrap_ci95"][1],
        "median": metrics["median_basket_day"], "positive_day_share": metrics["positive_day_share"],
        "failed_ticket_cost": metrics["avg_failed_ticket_cost"],
        "survivor_mean": metrics["avg_survivor_contribution"]["mean_net_return"],
        "tail_retained_mean_raw": float(np.mean(tails)),
        "tail_retained_median_raw": float(np.median(tails)), "tail_n_raw": len(tails),
        "worst_day": metrics["worst_day"], "worst_week": metrics["worst_week"],
        "worst_month": metrics["worst_month"], "compounded_max_dd": metrics["compounded_max_dd"],
        "turnover_per_day": metrics["turnover_per_day"], "n_entries": metrics["n_entries"],
        "n_reduces": metrics["n_reduces"], "n_exits": metrics["n_exits"],
        "block1_mean": b1["mean"], "block2_mean": b2["mean"],
        "block1_worst_day": b1["worst_day"], "block2_worst_day": b2["worst_day"],
        "block1_max_dd": b1["max_dd"], "block2_max_dd": b2["max_dd"],
        "replay_bad": replay_bad, "missing_raw_paths": missing_paths,
    }
    month_rows = [{"run_id": run_id, **month} for month in months]
    validation = {
        "run_id": run_id,
        "artifacts_present": all((run_dir / name).is_file() for name in REQUIRED),
        "progress_days": len(progress["done"]), "daily_rows": len(daily),
        "daily_dates_exact": [row["date"] for row in daily] == dev_days,
        "contract_hash_ok": cfg["sim_contract_hash"] == bs._contract_hash(),
        "config_contract_exact": all((cfg["bps_total"] == 100.0, cfg["n_days"] == 1066, cfg["seed"] == bs.BOOT_SEED, cfg["contract_version"] == "FROZEN-2026-09-22", cfg["days"] == [dev_days[0], dev_days[-1]])),
        "config_cell_exact": cfg["cell"] == {key: value for key, value in cell.items() if key != "run_id"},
        "metrics_days_exact": metrics["days_n"] == metrics["dev_days_n"] == 1066,
        "metric_counts_match": (metrics["n_entries"], metrics["n_reduces"], metrics["n_exits"]) == (entries, reduces, exits),
        "ticket_rows": entries, "ticket_daily_pnl_diff": ticket_net - sum(float(row["pnl"]) for row in daily),
        "replay_bad": replay_bad, "replay_max_abs_diff": replay_max_diff,
        "missing_raw_paths": missing_paths, "semantic_status": status,
    }
    return matrix, tranche_rows(groups, run_id, entries, len(dev_days)), false_rows, month_rows, validation


def attach_comparisons(matrix: list[dict], false_rows: list[dict], months: list[dict]) -> None:
    binary = {(row["entry"], row["base"]): row for row in matrix if row["kind"] == "base" and row["N"] == 3 and row["pattern"] == "EXIT"}
    for row in matrix:
        base = binary[(row["entry"], row["base"])]
        for field in ("net100_mean", "failed_ticket_cost", "tail_retained_mean_raw", "worst_day", "compounded_max_dd", "turnover_per_day", "block1_mean", "block2_mean"):
            row[f"delta_{field}"] = row[field] - base[field]
    false_map = {(row["run_id"], row["H"]): row for row in false_rows}
    for row in false_rows:
        base_id = binary[(row["entry"], row["base"])]["run_id"]
        base = false_map[(base_id, row["H"])]
        row["binary_run_id"] = base_id
        row["delta_flat_before"] = row["flat_before_rate"] - base["flat_before_rate"]
        row["delta_half_sold_before"] = row["half_sold_before_rate"] - base["half_sold_before_rate"]
    month_map = {(row["run_id"], row["month"]): row for row in months}
    by_id = {row["run_id"]: row for row in matrix}
    for row in months:
        cell = by_id[row["run_id"]]
        base_id = binary[(cell["entry"], cell["base"])]["run_id"]
        row["binary_run_id"] = base_id
        row["delta_mean"] = row["mean"] - month_map[(base_id, row["month"])]["mean"]


def main() -> None:
    dev_days = bs.dev_days()
    raw_paths = load_raw_paths()
    matrix: list[dict] = []
    tranches: list[dict] = []
    false_rows: list[dict] = []
    months: list[dict] = []
    validations: list[dict] = []
    for cell in registry():
        result = analyze_cell(cell, raw_paths, dev_days)
        matrix.append(result[0]); tranches.extend(result[1]); false_rows.extend(result[2]); months.extend(result[3]); validations.append(result[4])
    attach_comparisons(matrix, false_rows, months)
    invalid = [row["run_id"] for row in validations if row["semantic_status"] != "valid_as_run"]
    manifest = {
        "planned_cells": len(registry()), "run_dirs": len([path for path in RUNS.iterdir() if path.is_dir()]),
        "mechanically_valid_cells": sum(int(all((row["artifacts_present"], row["progress_days"] == 1066, row["daily_rows"] == 1066, row["daily_dates_exact"], row["contract_hash_ok"], row["config_contract_exact"], row["config_cell_exact"], row["metrics_days_exact"], row["metric_counts_match"], abs(row["ticket_daily_pnl_diff"]) < 1e-8, row["replay_bad"] == 0, row["missing_raw_paths"] == 0))) for row in validations),
        "policy_invalid_cells": invalid,
        "policy_defects": [
            "DeRiskController state is keyed by ticker and ET but not day, so fires leak across sessions.",
            "Sequential de-risk evaluation does not trim tickets evaluated before the Kth fire.",
            "PRE-REG simultaneous satisfaction differs from the plan's rolling 10-minute fire window.",
        ],
        "pricing_ambiguity": "PatternRule schedules trigger-driven REDUCE and staged EXIT with level=None, unlike binary level-clamped EXIT; as-run economics are reported without changing policy.",
        "stage_semantics": "PASS: a firing disarms; only a strict close reclaim re-arms; the reclaim bar cannot fire.",
        "cells": validations,
    }
    for name, rows in (("matrix", matrix), ("tranches", tranches), ("false_release", false_rows), ("monthly", months)):
        write_json(f"{name}.json", rows); write_csv(f"{name}.csv", rows)
    write_json("validation_manifest.json", manifest)
    write_reports(OUT, matrix, tranches, false_rows, months, manifest)
    print(f"F4 results: {len(matrix)} cells; valid artifacts={manifest['mechanically_valid_cells']}; policy-invalid={len(invalid)}")


if __name__ == "__main__":
    main()
