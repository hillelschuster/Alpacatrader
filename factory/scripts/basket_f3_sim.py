#!/usr/bin/env python
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median

import polars as pl

try:
    from factory.scripts import basket_sim as sim
except ImportError:
    import basket_sim as sim

ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / "factory/artifacts/basket/phase2/F3"
BLOCKS = {"block1": ("2021-02", "2023-12"), "block2": ("2025-02", "2026-05")}
REQUIRED = ("config.json", "daily.parquet", "tickets.parquet", "metrics.json", "run_summary.json")


@dataclass(frozen=True)
class Cell:
    release_id: str
    kind: str
    L: int | None
    w: int | None
    g: int | None
    bps: int

    @property
    def run_id(self) -> str:
        return f"A_pm_N2_{self.release_id}_bps{self.bps}"

    def rule(self):
        if self.kind == "R0":
            return sim.R0()
        if self.kind == "R2":
            return sim.R2(-int(self.L), int(self.w))
        return sim.R3(int(self.g))

    def strategy(self) -> sim.StrategySpec:
        return sim.StrategySpec(
            family_id="F3", entry_pop="A_pm", entry_T=570, top_n=2, n_slots=2,
            reserve_frac=1.0, release=[self.rule()], name=self.run_id,
        )


def build_cells() -> list[Cell]:
    rules = [Cell("R0", "R0", None, None, None, bps) for bps in (100, 150)]
    rules += [Cell(f"R2_L{L}_w{w}", "R2", L, w, None, bps)
              for L in (10, 15) for w in (3, 5, 10) for bps in (100, 150)]
    rules += [Cell(f"R3_g{g}", "R3", None, None, g, bps)
              for g in (40, 50, 60) for bps in (100, 150)]
    return rules


def validate_day(day: str) -> None:
    sim.guard_day(day)
    if not (("2021-02-01" <= day <= "2023-12-31") or
            ("2025-02-01" <= day <= "2026-05-31")):
        raise ValueError(f"day outside frozen development blocks: {day}")


def _block(day: str) -> str:
    for name, (lo, hi) in BLOCKS.items():
        if lo <= day[:7] <= hi:
            return name
    raise ValueError(f"day outside frozen development blocks: {day}")


def _atomic_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, allow_nan=False))
    os.replace(tmp, path)


def surface_rows(rows: list[dict], cells: list[Cell]) -> list[dict]:
    by_id = {row["run_id"]: row for row in rows}
    hold = {bps: by_id.get(f"A_pm_N2_R0_bps{bps}", {}).get("mean_basket_day")
            for bps in (100, 150)}
    result = []
    for cell in cells:
        row = by_id.get(cell.run_id)
        if row is None:
            result.append({"run_id": cell.run_id, "release_id": cell.release_id,
                           "bps": cell.bps, "status": "not_run", "days_n": 0,
                           "mean_basket_day": None, "delta_vs_hold_mean": None,
                           "blocks": {k: {"mean": None} for k in BLOCKS}})
        else:
            row = dict(row)
            baseline = hold[cell.bps]
            row["delta_vs_hold_mean"] = (
                row["mean_basket_day"] - baseline
                if baseline is not None and row["mean_basket_day"] is not None else None
            )
            hold_row = by_id.get(f"A_pm_N2_R0_bps{cell.bps}")
            row["delta_vs_hold_by_block"] = {}
            for block in BLOCKS:
                base_mean = (hold_row or {}).get("blocks", {}).get(block, {}).get("mean")
                block_mean = row.get("blocks", {}).get(block, {}).get("mean")
                row["delta_vs_hold_by_block"][block] = (
                    block_mean - base_mean
                    if block_mean is not None and base_mean is not None else None
                )
            result.append(row)
    return result


def _daily_in_block(daily: pl.DataFrame, start: str, end: str) -> pl.DataFrame:
    return daily.filter((pl.col("date") >= pl.lit(start)) & (pl.col("date") <= pl.lit(end)))


def ticket_friction_cost(ticket: dict, bps: int) -> float:
    entry_cost = ticket["unit_notional"] - ticket["shares_entry"] * ticket["entry_px"]
    exit_cost = (ticket["shares_entry"] * ticket["exit_px"] * bps / 20_000
                 if not ticket["open_end"] and ticket["exit_px"] is not None else 0.0)
    return entry_cost + exit_cost


def _cell_report(cell: Cell, run_dir: Path, metrics: dict, days: list[str]) -> dict:
    daily = pl.read_parquet(run_dir / "daily.parquet").sort("date")
    tickets = pl.read_parquet(run_dir / "tickets.parquet")
    if daily["date"].to_list() != days:
        raise RuntimeError(f"daily coverage mismatch for {cell.run_id}")
    blocks = {}
    for name, (lo, hi) in BLOCKS.items():
        dates = [d for d in days if lo <= d[:7] <= hi]
        ddf = _daily_in_block(daily, dates[0], dates[-1])
        ticket_rows = tickets.filter(
            (pl.col("sleeve_day") >= pl.lit(dates[0])) &
            (pl.col("sleeve_day") <= pl.lit(dates[-1]))
        )
        block_tickets = list(ticket_rows.iter_rows(named=True))
        vals = ddf["r_day"].to_list()
        tail_ratios = [r["net_return"] / r["mfe_raw"] for r in block_tickets
                       if r["mfe_raw"] >= 0.5 and r["mfe_raw"] > 0]
        exits = Counter(r["exit_reason"] for r in block_tickets if r["exit_reason"])
        monthly_values: dict[str, list[float]] = {}
        for row in ddf.iter_rows(named=True):
            monthly_values.setdefault(row["date"][:7], []).append(row["r_day"])
        monthly = {
            month: {"days_n": len(values), "mean": sum(values) / len(values),
                    "sum": sum(values), "worst_day": min(values)}
            for month, values in monthly_values.items()
        }
        blocks[name] = {
            "days_n": len(vals), "months_n": len({d[:7] for d in dates}),
            "mean": sum(vals) / len(vals) if vals else None,
            "median": float(ddf["r_day"].median()) if vals else None,
            "sum": float(ddf["r_day"].sum()) if vals else 0.0,
            "worst_day": float(ddf["r_day"].min()) if vals else None,
            "positive_day_share": float((ddf["r_day"] > 0).mean()) if vals else None,
            "compounded_growth": float((ddf["r_day"] + 1).product()) if vals else 1.0,
            "compounded_max_dd": _max_drawdown(vals),
            "daily_pnl_including_mark_changes": float(ddf["pnl"].sum()),
            "deployed_time_avg": float(ddf["deployed_avg"].mean()) if vals else 0.0,
            "n_actions": int(ddf["n_actions"].sum()),
            "n_ticket_rows": len(block_tickets),
            "realized_closed_ticket_net": sum(r["net"] for r in block_tickets if not r["open_end"]),
            "open_end_ticket_net_marked": sum(r["net"] for r in block_tickets if r["open_end"]),
            "friction_cost": sum(ticket_friction_cost(r, cell.bps) for r in block_tickets),
            "ticket_gross_net_before_friction": sum(
                r["net"] + ticket_friction_cost(r, cell.bps) for r in block_tickets
            ),
            "exit_reason_counts": dict(sorted(exits.items())),
            "tail_retained_mean_mfe_ge_50": (sum(tail_ratios) / len(tail_ratios) if tail_ratios else None),
            "tail_retained_median_mfe_ge_50": median(tail_ratios) if tail_ratios else None,
            "tail_retained_n_mfe_ge_50": len(tail_ratios),
            "false_release_rate_by_H": {"30": None, "50": None, "100": None},
            "false_release_rate_limit": "first-touch et is not retained in native ticket output",
            "half_release_rate_by_H": {"30": 0.0, "50": 0.0, "100": 0.0},
            "monthly": monthly,
        }
    ticket_rows = list(tickets.iter_rows(named=True))
    pooled_realized = sum(r["net"] for r in ticket_rows if not r["open_end"])
    pooled_marked = sum(r["net"] for r in ticket_rows if r["open_end"])
    total_friction = sum(ticket_friction_cost(r, cell.bps) for r in ticket_rows)
    report = {
        "run_id": cell.run_id, "release_id": cell.release_id, "bps": cell.bps,
        "status": "complete", "days_n": len(days), "months_n": len({d[:7] for d in days}),
        "mean_basket_day": metrics["mean_basket_day"], "median_basket_day": metrics["median_basket_day"],
        "std_basket_day": metrics["std_basket_day"], "daily_percentiles": metrics["daily_percentiles"],
        "worst_day": metrics["worst_day"], "worst_week": metrics["worst_week"],
        "worst_month": metrics["worst_month"], "positive_day_share": metrics["positive_day_share"],
        "compounded_growth": metrics["compounded_growth"], "compounded_max_dd": metrics["compounded_max_dd"],
        "avg_deployed_capital": metrics["avg_deployed_capital"],
        "turnover_per_day": metrics["turnover_per_day"], "turnover_annualized": metrics["turnover_annualized"],
        "n_entries": metrics["n_entries"], "n_exits": metrics["n_exits"],
        "n_blocked_slots": metrics["n_blocked_slots"], "n_pending": metrics["n_pending"],
        "n_carries": metrics["n_carries"], "avg_failed_ticket_cost": metrics["avg_failed_ticket_cost"],
        "path_contrib": metrics["path_contrib"], "false_release_rate": metrics["false_release_rate"],
        "half_release_rate": metrics["half_release_rate"], "tail_retained": metrics["tail_retained"],
        "per_year": metrics["per_year"], "per_quarter": metrics["per_quarter"],
        "realized_pnl_tickets": pooled_realized, "marked_pnl_tickets": pooled_marked,
        "friction_cost": total_friction,
        "ticket_gross_net_before_friction": sum(r["net"] for r in ticket_rows) + total_friction,
        "marked_ticket_n": sum(bool(r["open_end"]) for r in ticket_rows), "blocks": blocks,
        "exit_reason_counts": dict(sorted(Counter(
            r["exit_reason"] for r in ticket_rows if r["exit_reason"]
        ).items())),
    }
    return report


def _max_drawdown(values: list[float]) -> float:
    equity, peak, worst = 1.0, 1.0, 0.0
    for value in values:
        equity *= 1.0 + value
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst


def run_cells(out_root: Path, days: list[str], workers: int = 4) -> list[dict]:
    for day in days:
        validate_day(day)
    expected = sim.dev_days()
    if days != expected or len(days) != 1066:
        raise RuntimeError(f"expected the exact 1,066 canonical dev days; got {len(days)}")
    if set(sim.session_end_map()) != set(days):
        raise RuntimeError("session calendar coverage differs from permitted dev days")
    out_root.mkdir(parents=True, exist_ok=True)
    _atomic_json(out_root / "configs.json", {
        "family_id": "F3", "entry": {"pop": "A_pm", "T": 570, "top_n": 2, "n_slots": 2},
        "capital": "C0=1, no leverage", "release_order": "one rule per cell",
        "grid": [asdict(c) for c in build_cells()], "frictions_bps_total": [100, 150],
        "blocks": BLOCKS,
    })
    rows: list[dict] = []
    for cell in build_cells():
        run_dir = out_root / "F3" / cell.run_id
        summary = sim.run(sim.RunConfig("F3", cell.run_id, cell.strategy(), float(cell.bps),
                                        out_root=out_root, days=days, workers=workers))
        metrics = summary["metrics"]
        report = _cell_report(cell, run_dir, metrics, days)
        rows = [r for r in rows if r["run_id"] != cell.run_id] + [report]
        _atomic_json(out_root / "surface.json", {"family_id": "F3", "cells": surface_rows(rows, build_cells())})
        print(f"completed {cell.run_id} ({len(rows)}/{len(build_cells())})", flush=True)
    _write_provenance(out_root, days)
    return surface_rows(rows, build_cells())


def _write_provenance(out_root: Path, days: list[str]) -> None:
    def manifest(paths):
        h = hashlib.sha256()
        for path in sorted(paths):
            h.update(path.name.encode())
            h.update(path.read_bytes())
        return h.hexdigest()
    _atomic_json(out_root / "provenance.json", {
        "label": "RUN", "producer": "factory/scripts/basket_f3_sim.py",
        "simulator": "factory/scripts/basket_sim.py (unchanged)",
        "contract_sha256": hashlib.sha256(sim.CONTRACT_PATH.read_bytes()).hexdigest(),
        "calendar_sha256": hashlib.sha256(sim.CAL_PATH.read_bytes()).hexdigest(),
        "day_manifest_sha256": hashlib.sha256("\n".join(days).encode()).hexdigest(),
        "anatomy_manifest_sha256": manifest([sim.ANAT_DIR / f"{d}.jsonl" for d in days]),
        "bars_manifest_sha256": manifest([sim.BARS_DIR / f"{d}.parquet" for d in days]),
        "development_days": {"n": len(days), "first": days[0], "last": days[-1]},
        "blocks": {"block1": {"months": 35, "days": 734}, "block2": {"months": 16, "days": 332}},
        "sealed_reserved_access": False,
    })


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--full", action="store_true", help="run all frozen cells on 1,066 days")
    mode.add_argument("--max-days", type=int, help="small deterministic canary prefix")
    ap.add_argument("--out-root", type=Path, default=None)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    days = sim.dev_days()
    if args.max_days:
        if args.max_days < 1:
            ap.error("--max-days must be positive")
        cells = build_cells()
        out = args.out_root or OUT_ROOT / "canary"
        for cell in cells:
            sim.run(sim.RunConfig("F3", cell.run_id, cell.strategy(), float(cell.bps),
                                  out_root=out, days=days[:args.max_days], workers=args.workers))
        return 0
    result = run_cells(args.out_root or OUT_ROOT, days=days, workers=args.workers)
    print(json.dumps({"family": "F3", "cells": len(result), "complete": sum(r["status"] == "complete" for r in result)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
