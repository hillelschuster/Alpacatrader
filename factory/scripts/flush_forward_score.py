#!/usr/bin/env python3
"""Offline scorer for the frozen flush rule (PRE-REG-FLUSH-01) on live logs.
Reads data/forward/<day>/{scans,bars}.jsonl logged by forward_observe.py,
rebuilds (paths, lb) frames equivalent to the research leaderboard files
(causal top-3 by prev-close gain among PIT-eligible names), and runs the
validated engine lb18_oos.run_engine. Live scorer is evidence-only.
Usage: python flush_forward_score.py --day 2026-09-11 | --all
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lb18_oos import run_engine  # noqa: E402

FWD = ROOT / "data" / "forward"
ART = ROOT / "factory" / "artifacts"


def _jsonl(p):
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def _et_minute(ts):
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    e = t.tz_convert("America/New_York")
    return int(e.hour * 60 + e.minute)


def load_day(dd, day, elig):
    bars, scans = _jsonl(dd / "bars.jsonl"), _jsonl(dd / "scans.jsonl")
    if not bars or not scans:
        return None, None
    b = pd.DataFrame(bars)
    b["t"] = b["ts"].map(_et_minute)
    b = b[(b["t"] >= 570) & (b["t"] <= 959)]
    b["date"] = day
    b = b.sort_values("ts").drop_duplicates(["symbol", "t"], keep="last")
    paths = b.rename(columns={"symbol": "ticker"})[
        ["date", "t", "ticker", "o", "h", "l", "c", "v", "n_bars"]].copy()
    have = set(paths["ticker"])
    lb_rows = []
    for sc in scans:
        t = _et_minute(sc["ts"])
        if t < 570 or t > 959:
            continue
        rows = [r for r in sc.get("rows", [])
                if r.get("symbol") in elig and r.get("symbol") in have
                and r.get("percent_gain") is not None]
        rows.sort(key=lambda r: r["percent_gain"], reverse=True)
        for rank, r in enumerate(rows[:3], 1):
            lb_rows.append({"date": day, "t": t, "rank": rank,
                            "ticker": r["symbol"],
                            "gain": float(r["percent_gain"]),
                            "px": float(r.get("price") or 0.0)})
    if not lb_rows:
        return paths, pd.DataFrame()
    lb = pd.DataFrame(lb_rows).drop_duplicates(["date", "t", "rank"], keep="last")
    return paths, lb


def _summary(df):
    if len(df) == 0:
        return {"n": 0}
    mo = df.groupby("month")["ret"].mean()
    return {"n": int(len(df)), "mean": round(float(df["ret"].mean()), 4),
            "med": round(float(df["ret"].median()), 4),
            "pos": round(float((df["ret"] > 0).mean()), 3),
            "months_pos": int((mo > 0).sum()), "n_months": int(len(mo))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day")
    ap.add_argument("--dir", help="explicit log directory (contains bars.jsonl)")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    pit = pd.read_parquet(ROOT / "data" / "pit" / "pit_symbols.parquet")
    elig = set(pit[pit["vintage"] == pit["vintage"].max()]["symbol"])
    if a.dir:
        days = [a.day or Path(a.dir).name]
        dirs = {days[0]: Path(a.dir)}
    elif a.day:
        days = [a.day]
        dirs = {a.day: FWD / a.day}
    elif a.all and FWD.exists():
        days = sorted(p.name for p in FWD.iterdir() if p.is_dir())
        dirs = {d: FWD / d for d in days}
    else:
        sys.exit("pass --day YYYY-MM-DD, --dir PATH, or --all")
    parts = []
    for d in days:
        paths, lb = load_day(dirs[d], d, elig)
        if paths is None or lb is None:
            print(f"{d}: no logs (scans/bars missing)")
            continue
        if len(lb) == 0:
            print(f"{d}: no eligible leaderboard snapshots")
            continue
        dr = run_engine(paths, lb)
        if len(dr) == 0:
            print(f"{d}: 0 fills")
            continue
        dr = dr.copy()
        dr["day"] = d
        parts.append(dr)
        print(f"{d}: {_summary(dr)} | pf2 {_summary(dr[dr['prior_flush'] >= 2])}")
    if not parts:
        print("no live fills scored")
        return
    allf = pd.concat(parts, ignore_index=True)
    out = {"days": days, "all": _summary(allf),
           "pf2": _summary(allf[allf["prior_flush"] >= 2]),
           "n_days_scored": len(parts)}
    allf.to_parquet(ART / "flush_forward_live.parquet")
    (ART / "flush_forward_live.json").write_text(
        json.dumps(out, indent=1, default=str))
    print(f"\npooled: {out['all']}")
    print(f"pf2   : {out['pf2']}")
    print(f"artifact -> {ART/'flush_forward_live.json'}")


if __name__ == "__main__":
    main()
