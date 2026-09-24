"""Diagnostic producer (worktree-local research tool, 2026-09-24 C1 cycle).

Generates the evidence under factory/artifacts/basket/phase2/DIAGNOSTICS_20260924/
and the run directories named in its docstring.  Not part of the frozen Phase-2
family registry: these are bounded diagnostics used to answer one economic
question each; see factory/STATE.md (2026-09-24 entries) for the readings.
"""
"""Where in the day does the money live?  Morning-only vs hold-to-close.

If the 10:00->close continuation of the held basket is negative EV, then
(a) adding at 10:00 destroys value (measured: -2.2..-3.2% per dollar),
(b) cutting at 10:00 adds value (measured: +30..+73 bps/day),
and (c) holding to the close is the wrong architecture regardless of state.

Arms (entry A_pm, N in {2,3}, p=1.00, 100/150 bps):
  hold     - R0, hold to the forced flat
  exit600  - EXIT at the first bar after the completed ET600 bar
  exit580  - EXIT at the first bar after the completed ET580 bar (09:40)
  exit630  - EXIT at the first bar after the completed ET630 bar (10:30)
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
ARMS = ("hold", "exit580", "exit600", "exit630")
BLOCKS = (("block1", "2021-02", "2023-12"), ("block2", "2025-02", "2026-05"))
OUT = WT / "factory/artifacts/basket/phase2/SEGMENT_DIAG"


class ExitAt(sim.ReleaseRule):
    """EXIT at the first bar after one completed checkpoint bar."""

    def __init__(self, et: int):
        self.et = int(et)
        self.name = f"X_T{self.et}"

    def evaluate(self, tk, bar, idx):
        if int(bar["et"]) != self.et:
            return None
        st = self.state(tk)
        if st.get("done"):
            return None
        st["done"] = True
        return {"action": "EXIT", "reason": self.name, "level": None}


def run_cell(args):
    n, arm, bps = args
    run_id = f"A_pm_N{n}_{arm}_bps{bps}"
    days = sim.dev_days()
    release = [] if arm == "hold" else [ExitAt(int(arm[4:]))]
    spec = sim.StrategySpec(family_id="SEGMENT", entry_pop="A_pm", entry_T=570, top_n=n,
                            n_slots=n, reserve_frac=1.0, release=release, scale_in=[],
                            name=run_id)
    summary = sim.run(sim.RunConfig("SEGMENT", run_id, spec, float(bps), out_root=OUT,
                                    days=days, workers=2), progress=False)
    daily = pl.read_parquet(OUT / "SEGMENT" / run_id / "daily.parquet", columns=["date", "r_day"])
    month = pl.col("date").str.slice(0, 7)
    metrics = summary["metrics"]
    row = {"run_id": run_id, "n": n, "arm": arm, "bps": bps,
           "mean": metrics["mean_basket_day"],
           "n_exits": metrics["n_exits"], "n_carries": metrics["n_carries"],
           "failed_ticket_cost": metrics["avg_failed_ticket_cost"],
           "avg_deployed": metrics["avg_deployed_capital"],
           "turnover": metrics["turnover_per_day"]}
    for name, lo, hi in BLOCKS:
        row[name] = float(daily.filter((month >= lo) & (month <= hi))["r_day"].mean())
    return row


def main():
    todo = [(n, arm, bps) for n in NS for arm in ARMS for bps in BPS]
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
        "question": "is the held basket's edge in the morning segment or in the rest of the day?",
        "design": ("A_pm, N in {2,3}, p=1.00, 100/150 bps; EXIT at the first bar after the "
                   "completed ET580/600/630 bar versus holding to the forced flat."),
        "cells": out,
    }, indent=1, sort_keys=True))
    print(f"{'run_id':26s} {'mean':>9s} {'b1':>9s} {'b2':>9s} {'delta':>9s} {'d_b1':>9s} {'d_b2':>9s} "
          f"{'exits':>6s} {'depl':>6s} {'turn':>6s}")
    for r in out:
        print(f"{r['run_id']:26s} {r['mean']:9.5f} {r['block1']:9.5f} {r['block2']:9.5f} "
              f"{r['delta_vs_hold']:9.5f} {r['delta_b1']:9.5f} {r['delta_b2']:9.5f} "
              f"{r['n_exits']:6d} {r['avg_deployed']:6.3f} {r['turnover']:6.2f}")
    print("saved", OUT / "SUMMARY.json")


if __name__ == "__main__":
    main()
