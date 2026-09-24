#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path
from typing import Optional

import numpy as np
import polars as pl

import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory.scripts import basket_sim as sim  # noqa: E402
F1 = ROOT / "factory/artifacts/basket/phase2/F1"
OUT = ROOT / "factory/artifacts/basket/phase2/F8"
EXPECTED_CELLS = 120
BLOCKS = {"block1": ("2021-02", "2023-12"), "block2": ("2025-02", "2026-05")}
ENTRY_CONFIG = {"A_pm": ("A_pm", 570), "A_pm31": ("A_pm31", 570),
                "A_open": ("A_open", 570), "B585": ("B", 585), "B600": ("B", 600)}
EXIT_CONFIG = {"R0": "R0", "R1m8": "R1(-8)", "R1m10": "R1(-10)", "R1m15": "R1(-15)"}
THRESHOLDS = (5, 10, 20, 30)
INPUT_FILES = ("config.json", "daily.parquet", "tickets.parquet", "metrics.json", "run_summary.json")


def validate_surface(surface: dict) -> list[dict]:
    cells = surface.get("cells", [])
    ids = [cell.get("run_id") for cell in cells]
    if surface.get("family_id") != "F1" or len(cells) != EXPECTED_CELLS or len(set(ids)) != EXPECTED_CELLS:
        raise ValueError("F1 surface must contain 120 unique frozen cells")
    # "validated_frozen" is the pre-C1 tree's marker; the corrected C1 tree marks cells "ran"
    # or "skipped_complete" (the resumable runner's already-done marker).  Completeness is the
    # load-bearing condition: 1,066 development days per cell.
    if any(cell.get("status") not in ("validated_frozen", "ran", "skipped_complete")
           or cell.get("days_n") != 1066 for cell in cells):
        raise ValueError("F1 surface contains a non-frozen or incomplete cell")
    return cells


def summarize_day(tickets: list[dict], n_slots: int) -> dict:
    returns = [float(row["net_return"]) for row in tickets]
    mfes = [float(row["mfe_raw"]) for row in tickets if row["mfe_raw"] is not None]
    closed = [row for row in tickets if not row.get("open_end", False)]
    counts = {"n_filled": len(tickets), "n_closed": len(closed),
              "n_open_end": len(tickets) - len(closed),
              "n_closed_profitable": sum(row["net"] > 0 for row in closed),
              "n_eod_positive": sum(row["net"] > 0 for row in tickets),
              "n_profitable": sum(row["net"] > 0 for row in tickets),
              "pnl_c0": sum(float(row["net"]) for row in tickets)}
    counts["all_profitable"] = (counts["n_profitable"] == len(tickets) if tickets else None)
    counts["ge2_profitable"] = counts["n_profitable"] >= 2
    for h in THRESHOLDS:
        reached = sum(float(row["mfe_raw"] or 0.0) >= h / 100 for row in tickets)
        counts[f"n_reach_{h}"] = reached
        counts[f"all_reach_{h}"] = reached == len(tickets) if tickets else None
        counts[f"ge2_reach_{h}"] = reached >= 2
    counts["member_net_returns"] = returns
    counts["member_mfe_raw"] = mfes
    counts["n_slots"] = n_slots
    return counts


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _stats(values: list[float]) -> dict:
    a = np.asarray(values, dtype=float)
    if not a.size:
        return {"n": 0, "mean": None, "median": None, "p10": None, "p90": None}
    return {"n": int(a.size), "mean": float(a.mean()), "median": float(np.median(a)),
            "p10": float(np.percentile(a, 10)), "p90": float(np.percentile(a, 90))}


def _range(cells: list[dict], block: str, key: str) -> str:
    values = [cell[block][key] for cell in cells if cell[block].get(key) is not None]
    return "n/a" if not values else f"{min(values):.4f} to {max(values):.4f}"


def _summarize_rows(rows: list[dict], n_slots: int) -> dict:
    total = len(rows)
    metric = {"days": total, "mean_filled": float(np.mean([r["n_filled"] for r in rows])),
              "p_all_profitable": float(np.mean([r["all_profitable"] is True for r in rows])),
              "p_ge2_profitable": float(np.mean([r["ge2_profitable"] for r in rows])),
              "pnl_c0": _stats([r["pnl_c0"] for r in rows]),
              "filled_n": sum(r["n_filled"] for r in rows)}
    for h in THRESHOLDS:
        metric[f"p_all_reach_{h}"] = float(np.mean([r[f"all_reach_{h}"] is True for r in rows]))
        metric[f"p_ge2_reach_{h}"] = float(np.mean([r[f"ge2_reach_{h}"] for r in rows]))
    mfe = [x for row in rows for x in row["member_mfe_raw"]]
    member_returns = [x for row in rows for x in row["member_net_returns"]]
    paired = [(a, b) for row in rows for a, b in itertools.combinations(row["member_net_returns"], 2)]
    corr = None
    if len(paired) >= 2:
        x, y = np.asarray(paired).T
        if x.std() and y.std():
            corr = float(np.corrcoef(x, y)[0, 1])
    metric["member_mfe_raw"] = _stats(mfe)
    metric["member_net_return"] = _stats(member_returns)
    metric["pairwise_corr"] = corr
    metric["pair_observations"] = len(paired)
    return metric


def load_surface(f1_dir: Path) -> dict:
    """One surface.json, or the per-entry surface_*.json files the corrected tree emits."""
    single = f1_dir / "surface.json"
    if single.is_file():
        return json.loads(single.read_text())
    cells: list[dict] = []
    for path in sorted(f1_dir.glob("surface_*.json")):
        cells.extend(json.loads(path.read_text()).get("cells", []))
    return {"family_id": "F1", "cells": cells}


def run(f1_dir: Path | None = None, out_dir: Path | None = None) -> dict:
    F1 = f1_dir or globals()["F1"]
    OUT = out_dir or globals()["OUT"]
    surface = load_surface(F1)
    cells = validate_surface(surface)
    output_rows: list[dict] = []
    results: list[dict] = []
    provenance: list[dict] = []
    canonical_days: list[str] | None = None
    for cell in cells:
        run_id = cell["run_id"]
        run_dir = F1 / "F1" / run_id
        if any(not (run_dir / name).is_file() for name in INPUT_FILES):
            raise ValueError(f"incomplete F1 input cell: {run_id}")
        config = json.loads((run_dir / "config.json").read_text())
        n = int(config["n_slots"])
        if (config.get("family_id") != "F1" or config.get("run_id") != run_id
                or config.get("n_days") != 1066 or config.get("entry_pop") != ENTRY_CONFIG[cell["entry"]][0]
                or config.get("entry_T") != ENTRY_CONFIG[cell["entry"]][1] or n != cell["N"]
                or config.get("bps_total") != cell["bps"] or config.get("release") != [EXIT_CONFIG[cell["exit"]]]):
            raise ValueError(f"F1 config mismatch: {run_id}")
        # Truth-critical: the joint surface is only valid for the contract generation its inputs
        # were produced under.  Without this check a pre-C1 F1 tree is silently accepted and the
        # joint rates are quoted as current evidence (found 2026-09-24 by the swarm).
        if config.get("contract_version") != sim.CONTRACT_VERSION:
            raise ValueError(
                f"F1 input {run_id} was produced under contract "
                f"{config.get('contract_version')!r}, engine is {sim.CONTRACT_VERSION!r}; "
                "regenerate the F1 tree (e.g. F1_C1) before rebuilding the joint surface")
        daily = pl.read_parquet(run_dir / "daily.parquet").sort("date")
        tickets = pl.read_parquet(run_dir / "tickets.parquet")
        days = daily["date"].to_list()
        if len(days) != 1066 or len(set(days)) != 1066:
            raise ValueError(f"F1 daily grid is not 1,066 unique dates: {run_id}")
        if any(not (("2021-02" <= day[:7] <= "2023-12") or ("2025-02" <= day[:7] <= "2026-05"))
               for day in days):
            raise ValueError(f"F1 date outside permitted development blocks: {run_id}")
        if canonical_days is None:
            canonical_days = days
        elif days != canonical_days:
            raise ValueError(f"F1 date alignment mismatch: {run_id}")
        if tickets.height and not set(tickets["sleeve_day"].to_list()) <= set(days):
            raise ValueError(f"F1 ticket date outside daily grid: {run_id}")
        ticket_days = tickets.partition_by("sleeve_day", as_dict=True, maintain_order=True)
        per_day: list[dict] = []
        for day in days:
            part = ticket_days.get((day,))
            ticket_rows = [] if part is None else part.select(
                "net", "net_return", "mfe_raw", "open_end"
            ).to_dicts()
            if len(ticket_rows) > n:
                raise ValueError(f"more concurrent filled tickets than sleeve slots: {run_id} {day}")
            result = summarize_day(ticket_rows, n)
            row = {"run_id": run_id, "entry": cell["entry"], "N": n, "exit": cell["exit"],
                   "bps": cell["bps"], "date": day, **result}
            output_rows.append(row)
            per_day.append(row)
        block_results = {name: _summarize_rows([r for r in per_day if lo <= r["date"][:7] <= hi], n)
                         for name, (lo, hi) in BLOCKS.items()}
        results.append({"run_id": run_id, "entry": cell["entry"], "N": n, "exit": cell["exit"],
                        "bps": cell["bps"], "pooled": _summarize_rows(per_day, n), **block_results})
        provenance.append({"run_id": run_id, "files": {name: _hash(run_dir / name) for name in INPUT_FILES}})

    if canonical_days is None:
        raise ValueError("empty F1 surface")
    OUT.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(output_rows).write_parquet(OUT / "joint_daily.parquet")
    (OUT / "joint_surface.json").write_text(json.dumps({"family_id": "F8", "cells": results}, indent=2, allow_nan=False))
    surface_sources = ([F1 / "surface.json"] if (F1 / "surface.json").is_file()
                       else sorted(F1.glob("surface_*.json")))
    (OUT / "provenance.json").write_text(json.dumps({
        "f1_surface_sha256": {path.name: _hash(path) for path in surface_sources},
        "f1_surface_files": [path.name for path in surface_sources],
        "f1_inputs": provenance, "day_count": len(canonical_days), "dates_sha256": hashlib.sha256(
            "\n".join(canonical_days).encode()).hexdigest()}, indent=2))
    metrics = ("p_all_profitable", "p_ge2_profitable", "p_all_reach_5", "p_all_reach_10",
               "p_ge2_reach_20", "p_ge2_reach_30")
    lines = ["# F8 — unconditional joint basket economics", "",
             "**Evidence: [RUN]** Full unconditional analysis of all 120 validated frozen F1 cells, "
             "each retaining the same 1,066 development dates. All result ranges below are across "
             "the complete tested surface; no cell is selected or promoted.", "",
             "## Definitions", "",
             "- A sleeve-day is one independent F1 run/day with fixed `C0=1`; tickets are the actual "
             "filled concurrent positions in that run, at the run's actual 100/150-bps friction. "
             "Cash/blocked/missing slots stay cash; no replacement or leverage is inferred.",
             "- `pnl_c0` is the F1 end-of-day basket return in C0 units (sum of ticket `net`; it "
             "reconciles exactly to F1 `daily.r_day`). Tickets with `open_end=true` have no realized "
             "exit: their `net` is the contract's end-of-day mark. `n_closed`/`n_closed_profitable` "
             "count actual exits, while `n_open_end` and `n_eod_positive` expose still-open marks. "
             "`all_profitable`/`ge2_profitable` therefore mean EOD net-positive among filled tickets, "
             "not all tickets realized an exit profit. Reach H means actual post-fill `mfe_raw >= H%`; "
             "empty days remain in all-day denominators and are not called all-profitable.",
             "- Pairwise correlation pools all unordered pairs of concurrent member `net_return` "
             "observations within sleeve-days. F1 tickets have no rank field, so no rank-pair estimate "
             "is invented. Member MFE and net-return distributions are ticket-level; sleeve EOD "
             "distribution is the C0 `pnl_c0` distribution.",
             "- Golden-window conditional results remain **pending F2/F12** state-panel artifacts. "
             "This report is unconditional only.", "", "## Full-surface probability ranges", "",
             "Ranges are minimum–maximum probabilities on the 0–1 scale across all 120 F1 cells, "
             "not confidence intervals.", "",
             "| Outcome | Pooled | Block 1 (2021-02–2023-12) | Block 2 (2025-02–2026-05) |",
             "|---|---:|---:|---:|"]
    labels = {"p_all_profitable": "All filled positions EOD net-positive (realized + marked)",
              "p_ge2_profitable": "At least 2 positions EOD net-positive (realized + marked)",
              "p_all_reach_5": "All filled positions reach +5% MFE",
              "p_all_reach_10": "All filled positions reach +10% MFE",
              "p_ge2_reach_20": "At least 2 reach +20% MFE",
              "p_ge2_reach_30": "At least 2 reach +30% MFE"}
    for key in metrics:
        lines.append(f"| {labels[key]} | {_range(results, 'pooled', key)} | "
                     f"{_range(results, 'block1', key)} | {_range(results, 'block2', key)} |")
    lines.extend(["", "Complete cell-by-cell pooled and dual-block metrics: `joint_surface.json`. "
                  "Every per-cell/per-day joint count, C0 sleeve outcome, and filled-member MFE/return "
                  "vectors: `joint_daily.parquet`. "
                  "Input SHA-256 hashes and date-grid provenance for all five input files in each "
                  "of the 120 cells: `provenance.json`.", "", "## Interpretation and limits", "",
                  "F8 describes whether the concurrent filled members tended to finish EOD net-positive "
                  "together, finish negative together, or produce multiple meaningful survivors under each already-run "
                   "F1 implementation. Across cells, pooled all-filled-EOD-net-positive rates span "
                  "0.3–14.9% and at-least-two-+30%-touch rates span 1.0–15.8%; this breadth is "
                  "fragile to the F1 implementation and does not support a surface-wide joint "
                  "success claim. Overlapping block ranges do not establish per-cell stability. "
                  "These descriptive ranges are not inferential comparisons: "
                  "the cells share days and are not independent. A positive tail-touch rate does "
                  "not establish executable profitability; the F1 net outputs and state-conditioned "
                  "F2/F12 work must be read separately. No strategy, edge, or preferred cell is "
                  "promoted. Sealed and reserved periods are absent because inputs are exclusively "
                  "the frozen F1 development outputs.", ""])
    (OUT / "README.md").write_text("\n".join(lines))
    return {"cells": len(results), "days_per_cell": len(canonical_days), "rows": len(output_rows)}


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="F8 joint-survivor surface from an F1 tree")
    ap.add_argument("--f1", type=Path, default=F1)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    print(json.dumps(run(args.f1, args.out), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
