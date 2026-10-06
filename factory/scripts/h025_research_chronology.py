#!/usr/bin/env python3
"""Fill-bar chronology resolution from SIP prints (read-only, development scope).

The frozen engine credits a same-bar target (exit c0) when the fill bar's high
>= c0. For flushes that start at the session high this tag usually prints
BEFORE the bid is touched (price was at the high, then collapsed through B).
This producer measures, per fill, whether a >=c0 print occurred before/after
the first <=B touch inside the fill minute, and recomputes:
  resolved_gross = same-bar target kept only if (close >= c0) or (target_after)
                   else the pessimistic continuation (paired_pessimistic_gross)
No protected dates; NET trades only for allowed calendar days. Per-day
checkpoints; resume on matching producer fingerprint.
Usage: h025_research_chronology.py --data-root ... --sip-root ... --stage replication --workers 3
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from h025_research_core import allowed_dates, load_day
from h025_research_execution import annotate

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ART = ROOT / "factory" / "artifacts" / "h025_research" / "chronology"
POPULATIONS = {
    "legacy_all": "legacy_all_fills.parquet",
    "pf2_preorder": "prepf2_legacy_fills.parquet",
}


_NET_EMPTY = {"net_present": False, "net_touch": False, "target_before": False, "target_after": False}


def net_ordering(sip_root, date, ticker, tf, B, c0):
    """before/after target prints around first <= B touch in the fill minute."""
    path = Path(sip_root) / "trades" / f"{date}.parquet"
    if not path.exists():
        return dict(_NET_EMPTY)
    try:
        table = pq.read_table(path, filters=[("symbol", "=", str(ticker))])
    except Exception:
        return dict(_NET_EMPTY)
    if table.num_rows == 0:
        return dict(_NET_EMPTY)
    tr = table.to_pandas()
    ts = pd.to_datetime(tr["ts_utc"], utc=True).dt.tz_convert("America/New_York")
    start = pd.Timestamp(date, tz="America/New_York") + pd.Timedelta(minutes=int(tf) - 1)
    win = tr.assign(ts=ts)
    win = win[(win.ts >= start) & (win.ts < start + pd.Timedelta(minutes=1))].sort_values("ts")
    if not len(win):
        return {**_NET_EMPTY, "net_present": True, "net_prints": 0}
    touch = win[win.price <= B]
    if not len(touch):
        return {**_NET_EMPTY, "net_present": True, "net_prints": int(len(win))}
    anchor = touch.ts.iloc[0]
    before = win[(win.ts < anchor) & (win.price >= c0)]
    after = win[(win.ts > anchor) & (win.price >= c0)]
    return {"net_present": True, "net_touch": True, "net_prints": int(len(win)),
            "target_before": bool(len(before)), "target_after": bool(len(after)),
            "touch_volume_below_B": float(touch["size"].sum())}


def run_day(job):
    date, data_root, sip_root, bulk, fingerprint, core_root = job
    date = str(date)
    bulk = Path(bulk)
    bulk.mkdir(parents=True, exist_ok=True)
    dest = bulk / f"{date}.parquet"
    if dest.exists() and (bulk / f"{date}.done.json").exists():
        return date, "skip"
    rows = []
    core_root = Path(core_root) / date
    day = None
    for population, fname in POPULATIONS.items():
        src = core_root / fname
        if not src.exists():
            continue
        fills = pd.read_parquet(src)
        if not len(fills):
            continue
        if day is None:
            day = load_day(data_root, date)
        ann = annotate(day, fills)
        for f in ann.itertuples(index=False):
            r = net_ordering(sip_root, date, f.ticker, f.tf, float(f.B), float(f.c0))
            row = {"date": date, "population": population, "ticker": str(f.ticker),
                   "t0": int(f.t0), "tf": int(f.tf), "B": float(f.B), "c0": float(f.c0),
                   "prior_flush": int(f.prior_flush), "rank": int(f.rank),
                   "ret_gross": float(f.ret_gross), "ret_net": float(f.ret),
                   "exit_t": int(f.exit_t), "exit_reason": str(f.exit_reason),
                   "same_bar": bool(int(f.exit_t) == int(f.tf)),
                   "paired_pessimistic_gross": float(f.paired_pessimistic_gross),
                   "fillbar_high_ge_target": bool(f.fillbar_high_ge_target),
                   "fillbar_stop_touch": bool(f.fillbar_stop_touch),
                   "fillbar_close_ge_target": bool(f.fillbar_close_ge_target),
                   **r}
            rows.append(row)
    frame = pd.DataFrame(rows)
    if len(frame):
        same_target = (frame.exit_reason == "target") & frame.same_bar
        genuine = frame.fillbar_close_ge_target | frame.target_after.fillna(False)
        frame["resolved_gross"] = frame.ret_gross
        frame.loc[same_target & genuine, "resolved_gross"] = frame.loc[same_target & genuine, "c0"] / frame.loc[same_target & genuine, "B"] - 1
        frame.loc[same_target & ~genuine, "resolved_gross"] = frame.loc[same_target & ~genuine, "paired_pessimistic_gross"]
        frame.loc[same_target & ~genuine & ~frame.net_present.fillna(False), "resolved_gross"] = np.nan  # unknown tape: exclude
    tmp = dest.with_suffix(".parquet.tmp")
    frame.to_parquet(tmp, index=False)
    tmp.replace(dest)
    (bulk / f"{date}.done.json").write_text(json.dumps({"fingerprint": fingerprint, "rows": len(frame)}))
    return date, f"rows={len(frame)}"


def summarize(bulk, dates):
    frames = [pd.read_parquet(bulk / f"{d}.parquet") for d in dates if (bulk / f"{d}.parquet").exists()]
    frames = [x for x in frames if len(x)]
    f = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    out = {"days": len(dates), "fills": int(len(f)), "populations": {}}
    if not len(f):
        return out
    f["month"] = f.date.str[:7]
    f["year"] = f.date.str[:4]
    for pop, sub in f.groupby("population"):
        amb = sub[(sub.exit_reason == "target") & sub.same_bar & ~sub.fillbar_close_ge_target]
        rec = {"fills": int(len(sub)),
               "legacy_net": float(sub.ret_gross.mean() - 0.01),
               "resolved_net": float(sub.resolved_gross.mean() - 0.01) if sub.resolved_gross.notna().any() else None,
               "resolved_n": int(sub.resolved_gross.notna().sum()),
               "pessimistic_net": float(sub.paired_pessimistic_gross.mean() - 0.01),
               "same_bar_target_fills": int(((sub.exit_reason == "target") & sub.same_bar).sum()),
               "ambiguous_fills": int(len(amb)),
               "ambiguous_net_present": int(amb.net_present.fillna(False).sum()),
               "ambiguous_target_after": int(amb.target_after.fillna(False).sum()),
               "ambiguous_after_share": float(amb.target_after.fillna(False).mean()) if len(amb) else None,
               "unknown_tape_same_bar_target": int((((sub.exit_reason == "target") & sub.same_bar) & ~sub.net_present.fillna(False)).sum()),
               "years": {}, "months": {}}
        for y, ys in sub.groupby("year"):
            rec["years"][y] = {"fills": int(len(ys)), "legacy_net": float(ys.ret_gross.mean() - 0.01),
                               "resolved_net": float(ys.resolved_gross.mean() - 0.01) if ys.resolved_gross.notna().any() else None}
        out["populations"][pop] = rec
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--sip-root", type=Path, required=True)
    ap.add_argument("--stage", choices=("original533", "replication", "all"), default="replication")
    ap.add_argument("--bulk-root", type=Path)
    ap.add_argument("--core-root", type=Path)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit-days", type=int)
    a = ap.parse_args()
    dates = allowed_dates(a.data_root, a.stage)
    if a.limit_days:
        dates = dates[: a.limit_days]
    bulk = a.bulk_root or a.data_root / "h025_research" / "chronology"
    bulk = Path(bulk)
    bulk.mkdir(parents=True, exist_ok=True)
    ART.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(Path(__file__).read_bytes()
                                 + Path(__file__).with_name("h025_research_execution.py").read_bytes()).hexdigest()
    (bulk / "manifest.json").write_text(json.dumps(
        {"fingerprint": fingerprint, "stage": a.stage, "dates": dates,
         "data_root": str(a.data_root), "sip_root": str(a.sip_root)}, indent=2))
    jobs = [(d, str(a.data_root), str(a.sip_root), str(bulk), fingerprint,
             str(a.core_root or a.data_root / "h025_research" / "core")) for d in dates]
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        for n, (date, status) in enumerate(pool.map(run_day, jobs), 1):
            if n % 25 == 0 or n == len(jobs):
                print(f"chronology {n}/{len(jobs)} {date} {status}", flush=True)
    summary = summarize(bulk, dates)
    (ART / f"{a.stage}_summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False))
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "years" and kk != "months"}
                      for k, v in summary["populations"].items()}, indent=2, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
