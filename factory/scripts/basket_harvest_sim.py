#!/usr/bin/env python3
"""HARVEST01 — unmanaged equal-dollar top-N basket wealth curves (engine).

For every development day x (variant, entry clock, N in 1..4, exit clock) compute the
basket-day return of buying the top-N selected names equal-dollar at the first
executable bar open at/after the entry clock and selling each member at the first
executable bar open at/after the exit clock.

Conventions (mirroring factory/BASKET-SIM-CONTRACT.md):
  * entry = first bar with et >= clock; gap_min = fill_et - clock; gap >= 5 or no bar
    => blocked slot = pure cash (1/N), never backfilled or substituted;
  * exit  = first bar with et >= max(exit_clock, fill_et + 1) (strictly after the fill);
    EOD exit_clock = session_end requires a bar printed at/after session_end, else the
    member is UNKNOWN (censored), never marked at the last close;
  * friction per side = bps/2/10000; buy shares = budget/(px*(1+side)),
    sell proceeds = shares*px*(1-side); 100 bps => 0.005 per side;
  * basket-day = sum of slot wealth; blocked slots are cash; a day with any UNKNOWN
    member contributes a null return to that cell (excluded from means, counted).

Outputs per day (atomic, resumable):
    <data>/harvest01/sim/fills/<day>.parquet    member-level fills + per-exit returns
    <data>/harvest01/sim/cells/<day>.parquet    basket-day returns per cell

Usage:
    .venv/bin/python factory/scripts/basket_harvest_sim.py --days 2021-02-01
    .venv/bin/python factory/scripts/basket_harvest_sim.py --dev-days --workers 3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402

GAP_BLOCK = 5
N_LADDER = [1, 2, 3, 4]
EXITS = [555, 560, 565, 569, 571, 575, 580, 585, 590, 595, 600, 610, 620, 630,
         645, 660, 675, 690, 720, 750, 780, 840, 900, 955]
FEE = {100: 0.005, 150: 0.0075}
SIDE = {100: FEE[100], 150: FEE[150]}


def load_bars(path: Path) -> dict:
    if not path.exists():
        return {}
    d = pl.read_parquet(path, columns=["ticker", "et", "open", "high", "low", "close"])
    out = {}
    for tk, sub in d.group_by("ticker", maintain_order=False):
        t = tk[0] if isinstance(tk, tuple) else tk
        s = sub.sort("et")
        out[t] = {c: s[c].to_list() for c in ("et", "open", "high", "low", "close")}
    return out


def first_ge(ets: list[int], target: int, lo: int = 0) -> int:
    import bisect
    i = bisect.bisect_left(ets, target, lo)
    return i if i < len(ets) else -1


def member_eval(bars: dict | None, clock: int, exit_clocks: list[int], session_end: int):
    """Return (status, fill_et, fill_px, gap_min, rets_by_exit). status in filled/blocked."""
    if not bars:
        return "blocked", None, None, None, {E: "cash" for E in exit_clocks}
    ets = bars["et"]
    i = first_ge(ets, clock)
    if i < 0:
        return "blocked", None, None, None, {E: "cash" for E in exit_clocks}
    fill_et = ets[i]
    gap = fill_et - clock
    if gap >= GAP_BLOCK:
        return "blocked", None, None, None, {E: "cash" for E in exit_clocks}
    fill_px = bars["open"][i]
    rets = {}
    for E in exit_clocks:
        target = max(E, fill_et + 1)
        if E == session_end:
            # forced flat: require a print at/after session_end; else UNKNOWN
            j = first_ge(ets, session_end, i + 1)
        else:
            j = first_ge(ets, target, i + 1)
        if j < 0:
            rets[E] = None  # UNKNOWN (censored)
            continue
        exit_px = bars["open"][j]
        rets[E] = (exit_px, ets[j])
    return "filled", fill_et, fill_px, gap, rets


def process_day(day: str, data_root: Path, sends: dict, force: bool) -> str:
    outd = data_root / "harvest01" / "sim"
    (outd / "fills").mkdir(parents=True, exist_ok=True)
    (outd / "cells").mkdir(parents=True, exist_ok=True)
    mp = outd / "cells" / f"{day}.manifest.json"
    if not force and mp.exists():
        try:
            if json.loads(mp.read_text()).get("status") == "ok":
                return f"{day}: skip"
        except Exception:
            pass
    sel_p = data_root / "harvest01" / "base" / "selected" / f"{day}.parquet"
    if not sel_p.exists():
        return f"{day}: no selection"
    session_end = int(sends.get(day, 959))
    sel = pl.read_parquet(sel_p)
    bars = load_bars(data_root / "harvest01" / "base" / "bars" / f"{day}.parquet")
    t0 = time.time()

    exit_clocks = [E for E in EXITS if E < session_end] + [session_end]
    fill_rows, cell_rows = [], []
    for variant in sorted(sel["variant"].unique().to_list()):
        sv = sel.filter(pl.col("variant") == variant)
        for clock in sorted(sv["clock"].unique().to_list()):
            sub = sv.filter(pl.col("clock") == clock).sort("rank")
            members = sub.head(max(N_LADDER)).to_dicts()
            evals = {}
            for m in members:
                bars_t = bars.get(m["ticker"])
                st, fet, fpx, gap, rets = member_eval(bars_t, int(clock), exit_clocks, session_end)
                evals[int(m["rank"])] = (st, fet, fpx, gap, rets, m)
                row = {"day": day, "variant": variant, "clock": int(clock),
                       "rank": int(m["rank"]), "ticker": m["ticker"],
                       "status": st, "fill_et": fet, "fill_px": fpx, "entry_gap_min": gap,
                       "decision_px": m["decision_px"], "decision_gain": m["gain_adj"],
                       "slip_vs_decision": None if (fpx is None or m["decision_px"] in (None, 0))
                       else (fpx / m["decision_px"] - 1),
                       "pit_listed": m.get("pit_listed"), "age_min": m.get("age_min")}
                if st == "filled" and bars_t:
                    i = first_ge(bars_t["et"], fet)
                    hs = bars_t["high"][i:]
                    ls = bars_t["low"][i:]
                    row["mfe_adj"] = max(hs) / fpx - 1 if hs else None
                    row["mae_adj"] = min(ls) / fpx - 1 if ls else None
                    row["mfe_et"] = bars_t["et"][i + hs.index(max(hs))] if hs else None
                    row["mae_et"] = bars_t["et"][i + ls.index(min(ls))] if ls else None
                for E in exit_clocks:
                    r = rets.get(E)
                    key = f"r{E}"
                    if isinstance(r, tuple):
                        ex_px, _ = r
                        row[key + "_100"] = (ex_px * (1 - SIDE[100])) / (fpx * (1 + SIDE[100])) - 1
                        row[key + "_150"] = (ex_px * (1 - SIDE[150])) / (fpx * (1 + SIDE[150])) - 1
                    elif r == "cash":
                        row[key + "_100"] = 0.0
                        row[key + "_150"] = 0.0
                    else:
                        row[key + "_100"] = None
                        row[key + "_150"] = None
                fill_rows.append(row)

            for N in N_LADDER:
                for E in exit_clocks:
                    tot100 = tot150 = tot_gross = 0.0
                    n_fill = n_block = n_unk = 0
                    for r in range(1, N + 1):
                        if r not in evals:
                            n_block += 1
                            tot100 += 1.0 / N
                            tot150 += 1.0 / N
                            tot_gross += 1.0 / N
                            continue
                        st, fet, fpx, gap, rets, m = evals[r]
                        if st == "blocked":
                            n_block += 1
                            tot100 += 1.0 / N
                            tot150 += 1.0 / N
                            tot_gross += 1.0 / N
                            continue
                        rv = rets.get(E)
                        if rv is None:
                            n_unk += 1
                            continue
                        if rv == "cash":
                            n_block += 1
                            tot100 += 1.0 / N
                            tot150 += 1.0 / N
                            tot_gross += 1.0 / N
                            continue
                        n_fill += 1
                        ex_px, _ = rv
                        g = ex_px / fpx - 1
                        tot_gross += (1.0 / N) * (1 + g)
                        tot100 += (1.0 / N) * ((ex_px * (1 - SIDE[100])) / (fpx * (1 + SIDE[100])))
                        tot150 += (1.0 / N) * ((ex_px * (1 - SIDE[150])) / (fpx * (1 + SIDE[150])))
                    cell_rows.append({
                        "day": day, "variant": variant, "clock": int(clock), "N": N,
                        "exit": E, "exit_is_eod": E == session_end,
                        "n_filled": n_fill, "n_blocked": n_block, "n_unknown": n_unk,
                        "ret_gross": None if n_unk else tot_gross - 1,
                        "ret_100": None if n_unk else tot100 - 1,
                        "ret_150": None if n_unk else tot150 - 1,
                    })

    fdf = pl.DataFrame(fill_rows)
    cdf = pl.DataFrame(cell_rows)
    fp, tmp = outd / "fills" / f"{day}.parquet", outd / "fills" / f"{day}.parquet.tmp"
    fdf.write_parquet(tmp)
    os.replace(tmp, fp)
    cp, tmp = outd / "cells" / f"{day}.parquet", outd / "cells" / f"{day}.parquet.tmp"
    cdf.write_parquet(tmp)
    os.replace(tmp, cp)
    mp.write_text(json.dumps({"day": day, "status": "ok", "fills": int(fdf.height),
                              "cells": int(cdf.height), "session_end": session_end,
                              "elapsed_s": round(time.time() - t0, 1)}, indent=1))
    return f"{day}: ok fills={fdf.height} cells={cdf.height} el={round(time.time()-t0,1)}s"


def _worker(a):
    day, data_root, se, force = a
    return process_day(day, Path(data_root), {day: se}, force)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day")
    ap.add_argument("--days", nargs="+", default=[])
    ap.add_argument("--dev-days", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()

    data_root = bps.resolve_data_root(args.data_root)
    cal = json.loads(bps.CALENDAR.read_text())["evidence"]
    sends = {d: int(v["session_end"]) for d, v in cal.items()}
    days = list(args.days)
    if args.day:
        days.append(args.day)
    if args.dev_days:
        days = sorted(sends.keys())
    if args.limit:
        days = days[:args.limit]
    if not days:
        ap.print_help()
        return 2
    t0 = time.time()
    if args.workers <= 1:
        for d in days:
            print(process_day(d, data_root, sends, args.force), flush=True)
    else:
        import multiprocessing as mp
        tasks = [(d, str(data_root), int(sends.get(d, 959)), args.force) for d in days]
        with mp.Pool(processes=args.workers) as pool:
            for line in pool.imap_unordered(_worker, tasks):
                print(line, flush=True)
    print(f"done {len(days)} days in {round(time.time() - t0, 1)}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
