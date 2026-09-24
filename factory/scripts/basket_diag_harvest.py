"""Diagnostic producer (worktree-local research tool, 2026-09-24 C1 cycle).

Generates the evidence under factory/artifacts/basket/phase2/DIAGNOSTICS_20260924/
and the run directories named in its docstring.  Not part of the frozen Phase-2
family registry: these are bounded diagnostics used to answer one economic
question each; see factory/STATE.md (2026-09-24 entries) for the readings.
"""
"""Harvest-into-strength diagnostic.

The corrected engine says the held top-gainer basket bleeds from entry to the close
(and most of the hole is in the first 30 minutes), so "hold the convexity" is the wrong
frame: the measured right tail has to be SOLD INTO, not held through.

Arms (entry A_pm, N in {2,3}, p=1.00, 100/150 bps; execution = next bar open after the
completed bar that first trades through the level):
  hold        - R0 control
  cut30/cut50/cut100 - REDUCE 50% at the first +30%/+50%/+100% touch, rest to flat
  all30       - EXIT 100% at the first +30% touch
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import polars as pl

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))

from factory.scripts import basket_sim as sim  # noqa: E402

NS = (2, 3)
BPS = (100, 150)
LEVELS = (30, 50, 100)
BLOCKS = (("block1", "2021-02", "2023-12"), ("block2", "2025-02", "2026-05"))
OUT = WT / "factory/artifacts/basket/phase2/HARVEST_DIAG"


class HarvestAt(sim.ReleaseRule):
    """Take profit at the first completed bar that trades through ``level`` percent."""

    def __init__(self, level: float, frac: float):
        self.level = float(level)
        self.frac = float(frac)
        self.name = f"H{int(level)}_{int(frac*100)}"

    def evaluate(self, tk, bar, idx):
        st = self.state(tk)
        if st.get("done"):
            return None
        trigger = tk.entry_px * (1.0 + self.level / 100.0)
        if float(bar["high"]) < trigger - 1e-12:
            return None
        st["done"] = True
        if self.frac >= 1.0:
            return {"action": "EXIT", "reason": self.name, "level": None}
        return {"action": "REDUCE", "reason": self.name, "level": None, "frac": self.frac}


def run_cell(args):
    n, arm, bps = args
    run_id = f"A_pm_N{n}_{arm}_bps{bps}"
    days = sim.dev_days()
    if arm == "hold":
        release = []
    elif arm == "all30":
        release = [HarvestAt(30, 1.0)]
    else:
        release = [HarvestAt(float(arm[3:]), 0.5)]
    spec = sim.StrategySpec(family_id="HARVEST", entry_pop="A_pm", entry_T=570, top_n=n,
                            n_slots=n, reserve_frac=1.0, release=release, scale_in=[],
                            name=run_id)
    summary = sim.run(sim.RunConfig("HARVEST", run_id, spec, float(bps), out_root=OUT,
                                    days=days, workers=2), progress=False)
    daily = pl.read_parquet(OUT / "HARVEST" / run_id / "daily.parquet", columns=["date", "r_day"])
    month = pl.col("date").str.slice(0, 7)
    metrics = summary["metrics"]
    row = {"run_id": run_id, "n": n, "arm": arm, "bps": bps,
           "mean": metrics["mean_basket_day"],
           "n_reduces": metrics["n_reduces"], "n_exits": metrics["n_exits"],
           "failed_ticket_cost": metrics["avg_failed_ticket_cost"],
           "avg_deployed": metrics["avg_deployed_capital"],
           "path_contrib_50": metrics["path_contrib"]["50"],
           "tail_retained_mean": metrics["tail_retained"]["mean"]}
    for name, lo, hi in BLOCKS:
        row[name] = float(daily.filter((month >= lo) & (month <= hi))["r_day"].mean())
    return row


def main():
    todo = [(n, arm, bps) for n in NS for arm in ("hold", "cut30", "cut50", "cut100", "all30")
            for bps in BPS]
    with ProcessPoolExecutor(max_workers=3) as ex:
        rows = list(ex.map(run_cell, todo))
    by = {r["run_id"]: r for r in rows}
    out = []
    for r in sorted(rows, key=lambda r: (r["n"], r["bps"], r["arm"])):
        hold = by[f"A_pm_N{r['n']}_hold_bps{r['bps']}"]
        out.append({**r, "delta_vs_hold": r["mean"] - hold["mean"],
                    "delta_b1": r["block1"] - hold["block1"],
                    "delta_b2": r["block2"] - hold["block2"]})
    (OUT / "SUMMARY.json").write_text(json.dumps({
        "question": ("does selling INTO the measured right tail (scale out at a first MFE touch) "
                     "beat holding to the forced flat?"),
        "design": ("A_pm, N in {2,3}, p=1.00, 100/150 bps; REDUCE 50% at the first +30/+50/+100% "
                   "touch (next-bar-open execution) or EXIT 100% at the first +30% touch."),
        "cells": out,
    }, indent=1, sort_keys=True))
    print(f"{'run_id':26s} {'mean':>9s} {'b1':>9s} {'b2':>9s} {'delta':>9s} {'d_b1':>9s} {'d_b2':>9s} "
          f"{'red':>5s} {'fail':>8s} {'depl':>6s} {'p50':>6s}")
    for r in out:
        p50 = r["path_contrib_50"]
        print(f"{r['run_id']:26s} {r['mean']:9.5f} {r['block1']:9.5f} {r['block2']:9.5f} "
              f"{r['delta_vs_hold']:9.5f} {r['delta_b1']:9.5f} {r['delta_b2']:9.5f} "
              f"{r['n_reduces']:5d} {r['failed_ticket_cost']:8.4f} {r['avg_deployed']:6.3f} "
              f"{p50 if p50 is not None else float('nan'):6.3f}")
    print("saved", OUT / "SUMMARY.json")


if __name__ == "__main__":
    main()
