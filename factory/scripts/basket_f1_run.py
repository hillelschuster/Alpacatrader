#!/usr/bin/env python
"""F1 primitive-engine driver — runs the frozen PRE-REG-BASKET-02 §3.1 grid.

Does NOT edit or subclass core semantics: it only builds StrategySpec cells and
calls the canonical engine's ``run(RunConfig(...))`` (public API).  Verbatim
outputs land under ``<out_root>/f1_<entry>/<run_id>/``.

Cells (Tier-1, bps=100): entries {A_pm,A_pm31,A_open,B585,B600} x N {2,3,4}
x exits {R0, R1_10, R1_15, R2_10_5, R3_50} = 75.

Tier-2 (time permitting): extra exits, neighborhood entries, friction ladder,
N=5 descriptive.

Resume: a cell is skipped iff its ``run_summary.json`` reports the full day
count; an incomplete cell is wiped and re-run (the engine's metrics are computed
from the in-memory rows of the current invocation, so a partially-resumed run
would emit partial metrics — restarting is the faithful choice).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
from basket_sim import (  # noqa: E402
    R0, R1, R2, R3, RunConfig, StrategySpec, dev_days, run,
)

OUT_ROOT_DEFAULT = ROOT / "factory" / "artifacts" / "basket" / "phase2" / "F1_primitive" / "runs"

ENTRIES = {
    "A_pm": ("A_pm", 570),
    "A_pm31": ("A_pm31", 570),
    "A_open": ("A_open", 570),
    "B585": ("B", 585),
    "B600": ("B", 600),
    "B575": ("B", 575),
    "B615": ("B", 615),
}
EXITS = {
    "R0": lambda: [R0()],
    "R1_8": lambda: [R1(-8)],
    "R1_10": lambda: [R1(-10)],
    "R1_15": lambda: [R1(-15)],
    "R2_10_3": lambda: [R2(-10, 3)],
    "R2_10_5": lambda: [R2(-10, 5)],
    "R2_10_10": lambda: [R2(-10, 10)],
    "R2_15_10": lambda: [R2(-15, 10)],
    "R3_40": lambda: [R3(40)],
    "R3_50": lambda: [R3(50)],
    "R3_60": lambda: [R3(60)],
}


def cell_run_id(entry: str, n: int, exit_name: str) -> str:
    return f"{entry}_N{n}_{exit_name}"


def build_cells(tier: str) -> list[dict]:
    cells: list[dict] = []

    def add(entry, n, ex, t, bps=100.0, note=""):
        cells.append({"entry": entry, "n": n, "exit": ex, "tier": t,
                      "bps": bps, "note": note,
                      "run_id": cell_run_id(entry, n, ex) + (f"_bps{int(bps)}" if bps != 100 else "")})

    t1_entries = ["A_pm", "A_pm31", "A_open", "B585", "B600"]
    t1_exits = ["R0", "R1_10", "R1_15", "R2_10_5", "R3_50"]
    prio_entries = ["A_pm", "A_pm31", "B600"]
    prio_exits = ["R0", "R1_10", "R2_10_5"]

    if tier == "priority":
        for e in prio_entries:
            for n in (2, 3, 4):
                for x in prio_exits:
                    add(e, n, x, "t1")
        return cells

    for e in t1_entries:
        for n in (2, 3, 4):
            for x in t1_exits:
                add(e, n, x, "t1")

    if tier == "all":
        # extra exits on the 5x3 entry/N grid
        for e in t1_entries:
            for n in (2, 3, 4):
                for x in ["R1_8", "R2_10_3", "R2_10_10", "R2_15_10", "R3_40", "R3_60"]:
                    add(e, n, x, "t2")
        # neighborhood entries at N=3
        for e in ["B575", "B615"]:
            for n in (3,):
                for x in t1_exits:
                    add(e, n, x, "t2")
        # N=5 descriptive on headline entry x exits
        for e in ["A_pm", "A_pm31", "B600"]:
            for x in ["R0", "R1_10", "R2_10_5"]:
                add(e, 5, x, "t2", note="n5_descriptive")
    return cells


def run_cell(c, days, out_root, workers, force, verbose):
    pop, T = ENTRIES[c["entry"]]
    run_id = c["run_id"]
    fam = f"f1_{c['entry']}"
    run_dir = out_root / fam / run_id
    if run_dir.exists() and not force:
        rs = run_dir / "run_summary.json"
        if rs.exists():
            try:
                got = json.loads(rs.read_text()).get("days_run", 0)
                if got >= len(days):
                    return {"run_id": run_id, "status": "skip", "sec": 0.0}
            except Exception:
                pass
        # incomplete -> wipe (faithful metrics) and re-run
        shutil.rmtree(run_dir)
    spec = StrategySpec(family_id=fam, name=run_id, entry_pop=pop, entry_T=T,
                        top_n=c["n"], n_slots=c["n"], reserve_frac=1.0,
                        release=EXITS[c["exit"]](), scale_in=[])
    cfg = RunConfig(family_id=fam, run_id=run_id, spec=spec, bps_total=c["bps"],
                    out_root=out_root, days=days, workers=workers)
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    s = run(cfg, progress=verbose)
    sec = time.time() - t0
    m = s["metrics"]
    return {"run_id": run_id, "status": "done", "sec": sec,
            "mean": m["mean_basket_day"], "median": m["median_basket_day"],
            "tickets": s["ticket_rows"]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["priority", "tier1", "all"], default="tier1")
    ap.add_argument("--only", default=None, help="comma-separated run_ids")
    ap.add_argument("--bps", type=float, default=None, help="override bps for all cells")
    ap.add_argument("--max-days", type=int, default=None)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--out-root", default=str(OUT_ROOT_DEFAULT))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)

    cells = build_cells(a.tier)
    if a.only:
        keep = {s.strip() for s in a.only.split(",")}
        cells = [c for c in cells if c["run_id"] in keep]
    if a.bps is not None:
        for c in cells:
            c["bps"] = a.bps
    if a.list:
        for c in cells:
            print(f"{c['tier']:>3} {c['run_id']:<26} bps={c['bps']:g} n={c['n']} {c['note']}")
        print(f"total cells: {len(cells)}")
        return 0

    days = dev_days()
    if a.max_days:
        days = days[: a.max_days]
    out_root = Path(a.out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    print(f"F1 driver: {len(cells)} cells, {len(days)} days, bps sets "
          f"{sorted({c['bps'] for c in cells})}, workers={a.workers}, out={out_root}", flush=True)

    t0 = time.time()
    n_done = n_skip = 0
    for i, c in enumerate(cells):
        r = run_cell(c, days, out_root, a.workers, a.force, a.verbose)
        n_done += r["status"] == "done"
        n_skip += r["status"] == "skip"
        el = time.time() - t0
        if r["status"] == "done":
            print(f"[{i+1}/{len(cells)}] {r['run_id']:<26} {r['sec']:6.1f}s "
                  f"mean={r['mean']:+.4f} med={r['median']:+.4f} tick={r['tickets']} "
                  f"| total {el/60:.1f}m", flush=True)
        else:
            print(f"[{i+1}/{len(cells)}] {r['run_id']:<26} skip | total {el/60:.1f}m", flush=True)
    print(f"DONE cells={len(cells)} run={n_done} skip={n_skip} wall={time.time()-t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
