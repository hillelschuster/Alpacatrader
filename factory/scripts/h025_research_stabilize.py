#!/usr/bin/env python3
"""PRE-REG-STABILIZE-01: post-flush confirmation entry (development study).

Honest-by-construction: entry executes at the OPEN of a bar strictly after the
observed flush/latest confirmation bar, so no pre-entry same-bar credit is possible.
Exits run from the entry bar onward, stop-first; gap stops exit at min(open, stop).

Semantics (frozen in researches/PRE-REG-STABILIZE-01.md):
  qualification -> rolling reference (c0 = state close, B = 0.9*c0, 120-min expiry)
  flush = bar with low <= B; reference freezes at flush
  confirmation (low > B after a flush) variants: stall | reclaim | half | none
  entry = open of the bar after the confirmation bar ("none": bar after the flush bar)
  exits: target c0*{1.00,1.02} (high touch), stop {flush_low, entry*0.94} (stop-first),
         hold 60 bars -> close, EOD -> close; friction 100/150/200bps.

Usage: h025_research_stabilize.py --data-root ... [--stage all] [--workers 3]
Per-day checkpoints under (default) <data-root>/h025_research/stabilize; summary to
factory/artifacts/h025_research/stabilize/summary.json.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from h025_research_core import allowed_dates, load_day

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ART = ROOT / "factory" / "artifacts" / "h025_research" / "stabilize"
COSTS = (100, 150, 200)
NOTIONALS = (250, 500, 1000)
CONFIRMS = ("stall", "reclaim", "half", "none")
TARGETS = (1.00, 1.02)
STOPS = ("flush_low", "entry_6")
HOLD = 60
EXPIRY_MIN = 120


def policies():
    return [dict(confirm=c, target=t, stop=s, pf=p)
            for c in CONFIRMS for t in TARGETS for s in STOPS for p in (0, 2)]


def pname(p):
    return f"{p['confirm']}_t{p['target']:.2f}_{p['stop']}_pf{p['pf']}"


def run_variant(t, o, h, l, c, st_t, st_c, pol):
    n = len(t)
    fills = []
    si = 0
    cur_c0 = cur_t0 = None
    flat = True
    exit_t = -10**9
    state = None
    pending = None
    next_i = 0
    for i in range(n):
        if i < next_i:
            continue
        tj = int(t[i])
        if not flat and tj > exit_t:
            flat, state, pending = True, None, None
        while si < len(st_t) and int(st_t[si]) < tj:
            if flat and state is None and pending is None:
                cur_c0, cur_t0 = float(st_c[si]), int(st_t[si])
            si += 1
        if pending is not None and i == pending:
            entry_px = float(o[i])
            stop = state["flush_low"] if pol["stop"] == "flush_low" else entry_px * 0.94
            if stop >= entry_px:
                state, pending = None, None
                continue
            target = state["c0"] * pol["target"]
            exit_px = exit_reason = None
            exit_j = n - 1
            for j in range(i, n):
                lo, hi, op = float(l[j]), float(h[j]), float(o[j])
                if lo <= stop:
                    exit_px, exit_reason, exit_j = min(op, stop), "stop", j
                    break
                if hi >= target:
                    exit_px, exit_reason, exit_j = target, "target", j
                    break
                if (j - i + 1) >= HOLD:
                    exit_px, exit_reason, exit_j = float(c[j]), "hold", j
                    break
            else:
                exit_px, exit_reason = float(c[n - 1]), "eod"
            fills.append({"date": None, "ticker": None, "t_ref": int(cur_t0), "t_flush": state["t_flush"],
                          "t_confirm": state.get("t_confirm"), "entry_t": tj, "entry_px": entry_px,
                          "B": state["B"], "c0": state["c0"], "flush_low": state["flush_low"],
                          "target": target, "stop": stop, "exit_t": int(t[exit_j]),
                          "exit_px": exit_px, "exit_reason": exit_reason,
                          "ret_gross": exit_px / entry_px - 1,
                          "gap_stop": bool(exit_reason == "stop" and float(o[exit_j]) < stop)})
            flat, exit_t, state, pending = False, int(t[exit_j]), None, None
            next_i = exit_j + 1
            continue
        if not flat or cur_c0 is None:
            continue
        if tj - cur_t0 > EXPIRY_MIN:
            cur_c0, state, pending = None, None, None
            continue
        B = cur_c0 * 0.9
        if state is None:
            if float(l[i]) <= B:
                state = {"flush_low": float(l[i]), "flush_close": float(c[i]), "B": B,
                         "c0": cur_c0, "t_flush": tj, "t_confirm": None}
                if pol["confirm"] == "none":
                    pending = i + 1
            continue
        if float(l[i]) <= B:
            state["flush_low"] = min(state["flush_low"], float(l[i]))
            state["flush_close"] = float(c[i])
            state["t_flush"] = tj
            if pol["confirm"] == "none":
                pending = i + 1
            continue
        close = float(c[i])
        ok = (close >= state["flush_close"] if pol["confirm"] == "stall"
              else close >= B if pol["confirm"] == "reclaim"
              else close >= state["flush_low"] + 0.5 * (B - state["flush_low"]))
        if ok:
            state["t_confirm"] = tj
            pending = i + 1
    return fills


def run_day(job):
    date, data_root, bulk, fingerprint = job
    date = str(date)
    bulk = Path(bulk)
    bulk.mkdir(parents=True, exist_ok=True)
    dest = bulk / f"{date}.parquet"
    marker = bulk / f"{date}.done.json"
    if dest.exists() and marker.exists() and json.loads(marker.read_text()).get("fingerprint") == fingerprint:
        return date, "skip"
    day = load_day(data_root, date)
    rows = []
    st = day.states
    for ticker, g in st.groupby("ticker", sort=False):
        arrays = day.arrays.get((date, ticker))
        if arrays is None:
            continue
        t, o, h, l, c = (arrays[k] for k in ("t", "o", "h", "l", "c"))
        base = ((g["gain"] >= 1.0) & (g["rank"] <= 3) & (g["pullback"] >= -0.01)
                & (g["r15"] >= 0.03)).fillna(False)
        for pol in policies():
            mask = base & (g["pf_carry"] >= pol["pf"]) if pol["pf"] else base
            sub = g[mask]
            if not len(sub):
                continue
            fills = run_variant(t, o, h, l, c,
                                sub["t"].to_numpy(), sub["c"].to_numpy(float), pol)
            for f in fills:
                f.update({"date": date, "ticker": str(ticker), **{k: pol[k] for k in ("confirm", "target", "stop", "pf")}})
                rows.append(f)
    frame = pd.DataFrame(rows)
    tmp = dest.with_suffix(".parquet.tmp")
    frame.to_parquet(tmp, index=False)
    tmp.replace(dest)
    marker.write_text(json.dumps({"fingerprint": fingerprint, "rows": len(frame)}))
    return date, f"rows={len(frame)}"


def summarize(bulk, dates):
    frames = [pd.read_parquet(bulk / f"{d}.parquet") for d in dates if (bulk / f"{d}.parquet").exists()]
    frames = [x for x in frames if len(x)]
    f = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    out = {"days": len(dates), "fills": int(len(f)), "cells": {}}
    if not len(f):
        return out
    f["policy"] = f.confirm + "_t" + f.target.round(2).astype(str) + "_" + f.stop + "_pf" + f.pf.astype(str)
    f["month"] = f.date.str[:7]
    f["year"] = f.date.str[:4]
    f["block"] = np.where(f.date <= "2023-03-14", "original533", "replication")
    for name, sub in f.groupby("policy"):
        rec = {"fills": int(len(sub)), "fills_per_day": len(sub) / len(dates),
               "worst_gross": float(sub.ret_gross.min()) if len(sub) else None,
               "gap_stops": int(sub.gap_stop.sum())}
        for cost in COSTS:
            net = sub.ret_gross - cost / 10000
            daily = net.groupby(sub.date).sum().reindex(dates, fill_value=0.0)
            rec[f"mean_net_{cost}"] = float(net.mean())
            rec[f"sum_net_per_day_{cost}"] = float(daily.mean())
            for n_ in NOTIONALS:
                qty = np.floor(n_ / sub.entry_px)
                dollars = (qty * sub.entry_px) * net
                dd = dollars.groupby(sub.date).sum().reindex(dates, fill_value=0.0)
                rec[f"dollars_per_day_{n_}_{cost}"] = float(dd.mean())
        rec["blocks"] = {b: {"fills": int(len(s)),
                             "sum_net_per_day_150": float((s.ret_gross - .015).groupby(s.date).sum().mean()),
                             "days": int(s.date.nunique())} for b, s in sub.groupby("block")}
        rec["years"] = {y: {"fills": int(len(s)),
                            "sum_net_per_day_150": float((s.ret_gross - .015).groupby(s.date).sum().mean())}
                        for y, s in sub.groupby("year")}
        out["cells"][name] = rec
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--stage", choices=("original533", "replication", "all"), default="all")
    ap.add_argument("--bulk-root", type=Path)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit-days", type=int)
    a = ap.parse_args()
    dates = allowed_dates(a.data_root, a.stage)
    if a.limit_days:
        dates = dates[: a.limit_days]
    bulk = a.bulk_root or a.data_root / "h025_research" / "stabilize"
    bulk = Path(bulk)
    bulk.mkdir(parents=True, exist_ok=True)
    ART.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(Path(__file__).read_bytes()
                                 + Path(__file__).with_name("h025_research_core.py").read_bytes()).hexdigest()
    (bulk / "manifest.json").write_text(json.dumps(
        {"fingerprint": fingerprint, "stage": a.stage, "dates": dates,
         "policies": [dict(p, name=pname(p)) for p in policies()]}, indent=2))
    jobs = [(d, str(a.data_root), str(bulk), fingerprint) for d in dates]
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        for n, (date, status) in enumerate(pool.map(run_day, jobs), 1):
            if n % 25 == 0 or n == len(jobs):
                print(f"stabilize {n}/{len(jobs)} {date} {status}", flush=True)
    summary = summarize(bulk, dates)
    (ART / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False))
    ranked = sorted(summary.get("cells", {}).items(),
                    key=lambda kv: kv[1].get("sum_net_per_day_150", -9), reverse=True)
    print(json.dumps({"top": [(k, round(v.get("sum_net_per_day_150", 0), 4), v["fills"]) for k, v in ranked[:8]],
                      "bottom": [(k, round(v.get("sum_net_per_day_150", 0), 4), v["fills"]) for k, v in ranked[-4:]]},
                     indent=1), flush=True)


if __name__ == "__main__":
    main()
