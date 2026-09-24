"""Diagnostic producer (worktree-local research tool, 2026-09-24 C1 cycle).

Generates the evidence under factory/artifacts/basket/phase2/DIAGNOSTICS_20260924/
and the run directories named in its docstring.  Not part of the frozen Phase-2
family registry: these are bounded diagnostics used to answer one economic
question each; see factory/STATE.md (2026-09-24 entries) for the readings.
"""
"""Golden-window DE-RISK diagnostic: does cutting damaged exposure at 10:00 pay?

The F6 arms showed the marginal *added* dollar is ≈0 EV and only ~5% of capital is
deployable.  This tests the mirror question at full scale: at the completed ET600 bar,
cut 50% of any ticket whose own causal state is damaged, keep intact ones.

Arms (entry A_pm, N in {2,3}, p=1.00 full deployment, 100/150 bps):
  hold        - no action (primitive R0 control)
  reduce_all  - cut 50% of every open ticket at ET600 (unconditional comparator)
  derisk      - cut 50% only when ret_from_fill <= 0 OR retained MFE <= 1/3
  derisk_tight- cut 50% only when retained MFE <= 1/3 AND ret_from_fill <= 0
12 cells; deltas are paired against the hold arm of the same N/friction.
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import polars as pl

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))

from factory.scripts import basket_sim as sim  # noqa: E402

ET = 600
FRAC = 0.5
NS = (2, 3)
BPS = (100, 150)
BLOCKS = (("block1", "2021-02", "2023-12"), ("block2", "2025-02", "2026-05"))
OUT = WT / "factory/artifacts/basket/phase2/DERISK_DIAG"


class CheckpointReduce(sim.ReleaseRule):
    """Reduce ``frac`` of the position at one completed checkpoint bar.

    ``mode`` selects the recipient rule: 'all' (unconditional), 'damaged'
    (ret_from_fill <= 0 or retained <= floor) or 'tight' (retained <= floor and
    ret_from_fill <= 0).
    """

    def __init__(self, mode: str, et: int = ET, frac: float = FRAC, floor: float = 1.0 / 3.0):
        self.mode = mode
        self.et = int(et)
        self.frac = float(frac)
        self.floor = float(floor)
        self.name = f"S_{mode}_T{self.et}"

    def evaluate(self, tk, bar, idx):
        if int(bar["et"]) != self.et:
            return None
        st = self.state(tk)
        if st.get("done"):
            return None
        peak = max(float(tk.peak), float(bar["high"]))
        ret = float(bar["close"]) / float(tk.entry_px) - 1.0
        mfe = peak / float(tk.entry_px) - 1.0
        retained = (ret / mfe) if mfe > 0 else 0.0
        if self.mode == "all":
            take = True
        elif self.mode == "damaged":
            take = (ret <= 0.0) or (retained <= self.floor)
        else:
            take = (retained <= self.floor) and (ret <= 0.0)
        st["done"] = True
        st["retained"] = retained
        if not take:
            return None
        return {"action": "REDUCE", "reason": self.name, "level": None, "frac": self.frac}


def run_cell(args):
    n, arm, bps = args
    run_id = f"A_pm_N{n}_{arm}_bps{bps}"
    days = sim.dev_days()
    release = [] if arm == "hold" else [CheckpointReduce(arm)]
    spec = sim.StrategySpec(family_id="DERISK", entry_pop="A_pm", entry_T=570, top_n=n,
                            n_slots=n, reserve_frac=1.0, release=release, scale_in=[],
                            name=run_id)
    summary = sim.run(sim.RunConfig("DERISK", run_id, spec, float(bps), out_root=OUT,
                                    days=days, workers=2), progress=False)
    daily = pl.read_parquet(OUT / "DERISK" / run_id / "daily.parquet", columns=["date", "r_day"])
    month = pl.col("date").str.slice(0, 7)
    metrics = summary["metrics"]
    row = {"run_id": run_id, "n": n, "arm": arm, "bps": bps,
           "mean": metrics["mean_basket_day"],
           "n_reduces": metrics["n_reduces"], "n_carries": metrics["n_carries"],
           "failed_ticket_cost": metrics["avg_failed_ticket_cost"],
           "half_release_50": metrics["half_release_rate"]["50"],
           "false_release_50": metrics["false_release_rate"]["50"],
           "tail_retained_mean": metrics["tail_retained"]["mean"],
           "avg_deployed": metrics["avg_deployed_capital"]}
    for name, lo, hi in BLOCKS:
        row[name] = float(daily.filter((month >= lo) & (month <= hi))["r_day"].mean())
    return row


def main():
    todo = [(n, arm, bps) for n in NS for arm in ("hold", "all", "damaged", "tight")
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
        "question": ("does a causal de-risk (50% cut) of damaged positions at the completed ET600 "
                     "bar improve net EV at full deployment?"),
        "design": ("A_pm, N in {2,3}, p=1.00, 100/150 bps, 1,066 dev days; arms hold / "
                   "reduce_all / damaged (ret<=0 or retained<=1/3) / tight (retained<=1/3 and "
                   "ret<=0); execution is the engine's next-bar-open REDUCE."),
        "cells": out,
    }, indent=1, sort_keys=True))
    print(f"{'run_id':28s} {'mean':>9s} {'b1':>9s} {'b2':>9s} {'delta':>9s} {'d_b1':>9s} {'d_b2':>9s} "
          f"{'red':>5s} {'fail':>8s} {'hrel50':>7s} {'tail':>7s}")
    for r in out:
        print(f"{r['run_id']:28s} {r['mean']:9.5f} {r['block1']:9.5f} {r['block2']:9.5f} "
              f"{r['delta_vs_hold']:9.5f} {r['delta_b1']:9.5f} {r['delta_b2']:9.5f} "
              f"{r['n_reduces']:5d} {r['failed_ticket_cost']:8.4f} "
              f"{r['half_release_50'] if r['half_release_50'] is not None else float('nan'):7.3f} "
              f"{r['tail_retained_mean'] if r['tail_retained_mean'] is not None else float('nan'):7.3f}")
    print("saved", OUT / "SUMMARY.json")


if __name__ == "__main__":
    main()
