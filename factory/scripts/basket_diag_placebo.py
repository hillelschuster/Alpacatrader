"""Diagnostic producer (worktree-local research tool, 2026-09-24 C1 cycle).

Time-matched identity-shuffled placebo for the "sell into strength" reading.

The harvest arm (``factory/scripts/basket_diag_harvest.py``) exits each ticket at the next open
after the first bar that trades through +30%.  Its realized exit-time multiset is reused here,
but the exit times are re-assigned to the *wrong* tickets within the same day: exposure profile
(how many exits per day, at which minutes) is held fixed and only the touch identity is removed.
That is the one thing the harvest claim adds over "be in the market less".

    python factory/scripts/basket_diag_placebo.py --source-run A_pm_N2_all30_bps100 --n 2
    python factory/scripts/basket_diag_placebo.py --source-run A_pm_N3_all30_bps100 --n 3
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import polars as pl

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))

from factory.scripts import basket_sim as sim  # noqa: E402

HARVEST_ROOT = WT / "factory/artifacts/basket/phase2/HARVEST_DIAG"
OUT = WT / "factory/artifacts/basket/phase2/PLACEBO_DIAG"
BLOCKS = (("block1", "2021-02", "2023-12"), ("block2", "2025-02", "2026-05"))


def build_schedule(source_run: str, seed: int = 20260924) -> dict[tuple[str, str], tuple[int, int]]:
    """(sleeve_day, ticker) -> (session offset of the exit day, exit et)."""
    tickets = pl.read_parquet(
        HARVEST_ROOT / "HARVEST" / source_run / "tickets.parquet",
        columns=["sleeve_day", "ticker", "exit_day", "exit_et", "open_end", "flags"])
    exited = tickets.filter((~pl.col("open_end")) & pl.col("exit_et").is_not_null())
    by_day: dict[str, list[tuple[str, str, int]]] = {}
    for row in exited.iter_rows(named=True):
        day = str(row["exit_day"])
        by_day.setdefault(day, []).append((str(row["sleeve_day"]), str(row["ticker"]),
                                           int(row["exit_et"])))
    rng = random.Random(seed)
    schedule: dict[tuple[str, str], tuple[int, int]] = {}
    # session offset of the exit day, counted inside the ticket's own bar stream
    day_index = {d: i for i, d in enumerate(sim.dev_days())}
    for day, items in by_day.items():
        ets = [et for _, _, et in items]
        rng.shuffle(ets)
        for (sleeve_day, ticker, _real_et), et in zip(items, ets):
            offset = max(day_index.get(day, 0) - day_index.get(sleeve_day, 0), 0)
            schedule[(sleeve_day, ticker)] = (offset, et)
    return schedule


class ShuffledExit(sim.ReleaseRule):
    """EXIT when the completed bar is one minute before this ticket's shuffled exit et.

    Session boundaries are detected from the bar stream itself (``et`` going backwards), so a
    carried ticket cannot fire its shuffled time on the wrong session.
    """

    name = "S_shuffled_exit"

    def __init__(self, schedule: dict[tuple[str, str], tuple[int, int]], seed: int = 0):
        self.schedule = schedule
        self.seed = int(seed)

    def signature(self) -> str:
        # the shuffle is a parameter, so it must bind the run fingerprint
        return f"{self.name}:{self.seed}"

    def evaluate(self, tk, bar, idx):
        target = self.schedule.get((tk.sleeve_day, tk.ticker))
        if target is None:
            return None
        st = self.state(tk)
        et = int(bar["et"])
        if st.get("prev_et") is not None and et < st["prev_et"]:
            st["sess"] = st.get("sess", 0) + 1
        st["prev_et"] = et
        if st.get("done"):
            return None
        offset, exit_et = target
        if st.get("sess", 0) != offset or et + 1 != exit_et:
            return None
        st["done"] = True
        return {"action": "EXIT", "reason": self.name, "level": None}


def run_cell(n: int, bps: int, source_run: str, seed: int = 20260924) -> dict:
    schedule = build_schedule(source_run, seed)
    suffix = "" if seed == 20260924 else f"_s{seed}"
    run_id = f"A_pm_N{n}_placebo{suffix}_bps{bps}"
    spec = sim.StrategySpec(family_id="PLACEBO", entry_pop="A_pm", entry_T=570, top_n=n,
                            n_slots=n, reserve_frac=1.0,
                            release=[ShuffledExit(schedule, seed)],
                            scale_in=[], name=run_id)
    summary = sim.run(sim.RunConfig("PLACEBO", run_id, spec, float(bps), out_root=OUT,
                                    days=sim.dev_days(), workers=2), progress=False)
    daily = pl.read_parquet(OUT / "PLACEBO" / run_id / "daily.parquet", columns=["date", "r_day"])
    month = pl.col("date").str.slice(0, 7)
    cells = json.loads((HARVEST_ROOT / "SUMMARY.json").read_text())["cells"]
    hold = next(r for r in cells if r["run_id"] == f"A_pm_N{n}_hold_bps{bps}")
    harvest = next(r for r in cells if r["run_id"] == f"A_pm_N{n}_all30_bps{bps}")
    metrics = summary["metrics"]
    row = {"run_id": run_id, "n": n, "bps": bps, "seed": seed,
           "mean": metrics["mean_basket_day"], "hold_mean": hold["mean"],
           "harvest_mean": harvest["mean"],
           "delta_vs_hold": metrics["mean_basket_day"] - hold["mean"],
           "harvest_delta_vs_hold": harvest["mean"] - hold["mean"],
           "avg_deployed": metrics["avg_deployed_capital"],
           "hold_avg_deployed": hold["avg_deployed"],
           "n_exits": metrics["n_exits"], "scheduled_exits": len(schedule)}
    for name, lo, hi in BLOCKS:
        row[name] = float(daily.filter((month >= lo) & (month <= hi))["r_day"].mean())
        row[f"hold_{name}"] = hold[name]
    row["delta_b1"] = row["block1"] - hold["block1"]
    row["delta_b2"] = row["block2"] - hold["block2"]
    row["interpretation"] = (
        "placebo delta ≈ 0 → the exit-time profile alone earns nothing and the harvest gain is "
        "touch identity; placebo delta ≈ harvest delta → the gain is time-in-market, not state.")
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-run", default="A_pm_N2_all30_bps100")
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--bps", type=int, default=100)
    ap.add_argument("--seed", type=int, default=20260924)
    args = ap.parse_args(argv)
    row = run_cell(args.n, args.bps, args.source_run, args.seed)
    OUT.mkdir(parents=True, exist_ok=True)
    suffix = "" if args.seed == 20260924 else f"_s{args.seed}"
    out = OUT / f"placebo_N{args.n}_bps{args.bps}{suffix}.json"
    out.write_text(json.dumps(row, indent=1, sort_keys=True))
    print(json.dumps(row, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
