#!/usr/bin/env python
"""F1 multi-strategy day-major runner.

Why: the canonical engine's ``run()`` re-reads all 1,066 day parquets per cell
(~168 s/cell x 75 cells = 3.5 h, mostly I/O).  This runner loads each day once
and calls the engine's public ``simulate_day`` for every F1 cell, then emits the
engine's canonical per-cell outputs (parts + merged parquet + metrics.json +
config.json + surface.json + run_summary.json) using the engine's own metric and
output helpers.  No engine file is modified; core semantics are untouched.

Resume: per-cell month parts + a pickled per-cell Strategy state at each month
boundary, so an interrupted run continues with in-flight carries intact.
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
import basket_sim as bs  # noqa: E402
from basket_f1_run import ENTRIES, EXITS, build_cells  # noqa: E402

OUT_ROOT_DEFAULT = ROOT / "factory" / "artifacts" / "basket" / "phase2" / "F1_primitive" / "runs"
STATE_DIR_DEFAULT = ROOT / "factory" / "artifacts" / "basket" / "phase2" / "F1_primitive" / "_multi_state"


def make_spec(cell):
    pop, T = ENTRIES[cell["entry"]]
    fam = f"f1_{cell['entry']}"
    return bs.StrategySpec(family_id=fam, name=cell["run_id"], entry_pop=pop, entry_T=T,
                           top_n=cell["n"], n_slots=cell["n"], reserve_frac=1.0,
                           release=EXITS[cell["exit"]](), scale_in=[])


def finalize(cell, cfg, strat, run_dir, days, out_root, state_path):
    bs._flush_parts(run_dir / "parts" / "daily", run_dir / "parts" / "tickets",
                    cfg["buf_daily"], cfg["buf_tickets"])
    # terminal marks for still-open tickets
    term_rows = [bs._ticket_row(tk) for tk in strat.open_tickets.values()]
    if term_rows:
        last_month = days[-1][:7]
        bs._append_parquet(run_dir / "parts" / "tickets" / f"{last_month}.parquet",
                           term_rows, schema="tickets")
    daily_df = bs.merge_parts(run_dir, "daily")
    tick_df = bs.merge_parts(run_dir, "tickets")
    metrics = bs.compute_metrics(strat.day_rows,
                                 strat.closed + list(strat.open_tickets.values()),
                                 len(days), strat.n_blocked_slots, strat.n_pending,
                                 strat.n_carries)
    cfg_obj = {
        "family_id": cell["run_id"] and f"f1_{cell['entry']}",
        "run_id": cell["run_id"],
        "strategy": cell["run_id"],
        "entry_pop": ENTRIES[cell["entry"]][0],
        "entry_T": ENTRIES[cell["entry"]][1],
        "entry_family": cell["entry"],
        "top_n": cell["n"],
        "n_slots": cell["n"],
        "reserve_frac": 1.0,
        "release": [r.name for r in strat.spec.release],
        "scale_in": [],
        "bps_total": cell["bps"],
        "days": [days[0], days[-1]] if days else [],
        "n_days": len(days),
        "seed": bs.BOOT_SEED,
        "git_head": bs._git_head(),
        "sim_contract_hash": bs._contract_hash(),
        "contract_version": "FROZEN-2026-09-22",
        "tier": cell["tier"],
        "runner": "basket_f1_multi.py (day-major, load-once)",
    }
    bs._atomic_write_json(run_dir / "config.json", cfg_obj)
    bs._atomic_write_json(run_dir / "metrics.json", metrics)
    bs._atomic_write_json(run_dir / "surface.json",
                          {"family_id": f"f1_{cell['entry']}", "cells": [cell["run_id"]]})
    summary = {
        "run_dir": str(run_dir),
        "days_run": len(days),
        "wall_seconds": cfg["wall"],
        "daily_rows": daily_df.height,
        "ticket_rows": tick_df.height,
        "daily_sha256": bs._sha256_bytes((run_dir / "daily.parquet").read_bytes()),
        "metrics": metrics,
    }
    bs._atomic_write_json(run_dir / "run_summary.json", summary)
    if state_path.exists():
        state_path.unlink()
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["priority", "tier1", "all"], default="tier1")
    ap.add_argument("--bps", type=float, default=None)
    ap.add_argument("--max-days", type=int, default=None)
    ap.add_argument("--out-root", default=str(OUT_ROOT_DEFAULT))
    ap.add_argument("--state-dir", default=str(STATE_DIR_DEFAULT))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--shard", default=None, help="i/N - run cells[i::N] only")
    ap.add_argument("--only", default=None, help="comma-separated run_ids")
    a = ap.parse_args(argv)

    cells = build_cells(a.tier)
    if a.only:
        keep = {s.strip() for s in a.only.split(",")}
        cells = [c for c in cells if c["run_id"] in keep]
    if a.shard:
        i, n = (int(x) for x in a.shard.split("/"))
        cells = cells[i::n]
    if a.bps is not None:
        for c in cells:
            c["bps"] = a.bps
            if a.bps != 100:
                c["run_id"] = f"{c['run_id']}_bps{int(a.bps)}"
    if a.list:
        for c in cells:
            print(f"{c['tier']:>3} {c['run_id']:<28} bps={c['bps']:g} N={c['n']}")
        print("total", len(cells))
        return 0

    days = bs.dev_days()
    if a.max_days:
        days = days[: a.max_days]
    sem = bs.session_end_map()
    out_root = Path(a.out_root)
    state_dir = Path(a.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    # state: one entry per cell
    S: dict[str, dict] = {}
    n_skip = 0
    for c in cells:
        rid = c["run_id"]
        run_dir = out_root / f"f1_{c['entry']}" / rid
        st_path = state_dir / f"{rid}.pkl"
        if not a.force and not st_path.exists():
            rs = run_dir / "run_summary.json"
            if rs.exists():
                try:
                    if json.loads(rs.read_text()).get("days_run", 0) >= len(days):
                        n_skip += 1
                        continue
                except Exception:
                    pass
        if st_path.exists() and not a.force:
            with st_path.open("rb") as fh:
                st = pickle.load(fh)
            done = set(st["done"])
            print(f"resume {rid}: {len(done)} days done", flush=True)
        else:
            st = {"strat": bs.Strategy(make_spec(c)), "done": set(), "wall": 0.0}
        (run_dir / "parts" / "daily").mkdir(parents=True, exist_ok=True)
        (run_dir / "parts" / "tickets").mkdir(parents=True, exist_ok=True)
        st.update({"cell": c, "run_dir": run_dir, "state_path": st_path,
                   "buf_daily": {}, "buf_tickets": {}, "started": time.time()})
        S[rid] = st

    print(f"F1 multi: {len(cells)} cells, {len(days)} days, "
          f"bps={sorted({c['bps'] for c in cells})}", flush=True)
    t0 = time.time()
    nprocessed = 0
    for day in days:
        if all(day in st["done"] for st in S.values()):
            continue
        se = sem.get(day, bs.SESSION_END_NORMAL)
        rec = bs.load_anatomy(day)
        bars = bs.load_bars(day, se)
        m = day[:7]
        for rid, st in S.items():
            if day in st["done"]:
                continue
            strat = st["strat"]
            prev = len(strat.closed)
            row = bs.simulate_day(strat, day, rec, bars, se, st["cell"]["bps"])
            st["buf_daily"].setdefault(m, []).append(row)
            for tk in strat.closed[prev:]:
                st["buf_tickets"].setdefault(m, []).append(bs._ticket_row(tk))
            st["done"].add(day)
        nprocessed += 1
        # month boundary -> incremental flush + state checkpoint
        if (nprocessed % 25 == 0 or day == days[-1]):
            for rid, st in S.items():
                run_dir = st["run_dir"]
                bs._flush_parts(run_dir / "parts" / "daily", run_dir / "parts" / "tickets",
                                st["buf_daily"], st["buf_tickets"])
                st["wall"] = time.time() - st["started"]
                with st["state_path"].open("wb") as fh:
                    pickle.dump({"strat": st["strat"], "done": st["done"],
                                 "wall": st["wall"]}, fh, protocol=4)
            el = time.time() - t0
            print(f"  {nprocessed} days processed, {el:.0f}s "
                  f"({el/nprocessed*1000:.0f} ms/day)", flush=True)

    print("finalizing cells...", flush=True)
    for rid, st in S.items():
        st["wall"] = time.time() - st["started"]
        summ = finalize(st["cell"], st, st["strat"], st["run_dir"], days, out_root,
                        st["state_path"])
        mm = summ["metrics"]
        print(f"  {rid:<28} mean={mm['mean_basket_day']:+.4f} "
              f"med={mm['median_basket_day']:+.4f} tick={summ['ticket_rows']}", flush=True)
    print(f"DONE {len(cells)} cells in {time.time()-t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
