#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas", "pyarrow", "numpy"]
# ///
"""PRE-REG-DRIFT-01 (H034 probe 1): forward drift after a RECOVERED vacuum.

Measurement only. H025 and the live bot are untouched. A CANDIDATE cell here is
not adoptable: it requires its own new pre-reg + forward test.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import event_census as ec  # noqa: E402

FR = 0.01
HORIZONS = (15, 30, 60)
ART = ROOT / "factory" / "artifacts"
LB = ROOT / "data" / "leaderboard"
FLAGS = {"measurement": True, "seen_data": True, "not_an_alpha_claim": True}


def paths_for(day):
    p = pd.read_parquet(LB / f"path_{day}.parquet")
    return {tk: (g["t"].to_numpy(np.int64), g["c"].to_numpy(float),
                 g["h"].to_numpy(float), g["l"].to_numpy(float))
            for tk, g in p.groupby("ticker", sort=False)}


def forward(t, c, h, l, minute, horizon):
    """Enter at the first grid minute strictly after `minute` (causal), exit at
    the last minute <= entry + horizon. Returns (net, mfe, mae)."""
    i0 = int(np.searchsorted(t, minute, side="right"))
    if i0 >= len(t):
        return None
    i1 = int(np.searchsorted(t, t[i0] + horizon, side="right")) - 1
    if i1 < i0:
        return None
    entry = c[i0]
    return (c[i1] / entry - 1 - FR,
            h[i0:i1 + 1].max() / entry - 1,
            l[i0:i1 + 1].min() / entry - 1)


def collect(days):
    ev = ec.load_all(days)
    rec = ev[ev["recovered_30m"].fillna(False) & ev["t_recover_min"].notna()].copy()
    rec["t_rec"] = rec["t"].astype(int) + rec["t_recover_min"].astype(int)
    rows = []
    for day, g in rec.groupby("date"):
        cm = paths_for(day)
        for r in g.itertuples():
            v = cm.get(r.ticker)
            if v is None:
                continue
            for horizon in HORIZONS:
                o = forward(*v, minute=int(r.t_rec), horizon=horizon)
                ctrl = forward(*v, minute=int(r.t), horizon=horizon)
                if o is None or ctrl is None:
                    continue
                rows.append(dict(
                    date=day, ticker=r.ticker, t=int(r.t), rank=r.rank,
                    seq=int(r.seq), prior_flush=int(r.prior_flush), t_rec=int(r.t_rec),
                    H=horizon, period="2021-2023" if day < "2024-01-01" else "2024-2026",
                    session="AM" if int(r.t) < 720 else "PM",
                    key=bool((r.rank == 1) and (int(r.seq) >= 2)),
                    ret_net=o[0], mfe=o[1], mae=o[2], ctrl_net=ctrl[0]))
    return pd.DataFrame(rows)


def cells(df):
    out = df.copy()
    out["seq_c"] = out["seq"].map(lambda v: "3+" if v >= 3 else str(int(v)))
    out["rank_c"] = out["rank"].where(out["rank"] <= 3).map(
        lambda v: str(int(v)) if pd.notna(v) else "off")
    out["key_c"] = np.where(out["key"], "rank1&seq2+", "other")
    return out


def summarize(df):
    dims = {"all": lambda d: pd.Series("all", index=d.index),
            "seq": lambda d: d["seq_c"], "rank": lambda d: d["rank_c"],
            "session": lambda d: d["session"], "key": lambda d: d["key_c"]}
    rows = []
    for name, fn in dims.items():
        d = df.assign(_c=fn(df))
        for cell, sub in d.groupby("_c"):
            for horizon in HORIZONS:
                s = sub[sub["H"] == horizon]
                if len(s) == 0:
                    continue
                bym = s.groupby(s["date"].str[:7])["ret_net"].mean()
                rec = {"dim": name, "cell": str(cell), "H": horizon, "n": int(len(s)),
                       "mean_net": round(float(s["ret_net"].mean()), 4),
                       "ctrl_net": round(float(s["ctrl_net"].mean()), 4),
                       "diff_pp": round(float((s["ret_net"] - s["ctrl_net"]).mean()) * 100, 3),
                       "months_pos": int((bym > 0).sum()), "n_months": int(len(bym)),
                       "mfe_med": round(float(s["mfe"].median()), 4),
                       "mae_med": round(float(s["mae"].median()), 4)}
                for p in ("2021-2023", "2024-2026"):
                    sp = s[s["period"] == p]
                    rec[p] = {"n": int(len(sp)),
                              "mean_net": round(float(sp["ret_net"].mean()), 4) if len(sp) else None}
                rows.append(rec)
    return rows


def verdict(r):
    if r["H"] not in (30, 60):
        return "n/a"
    a = r["2021-2023"]["mean_net"]
    b = r["2024-2026"]["mean_net"]
    ok = (r["n"] >= 300 and r["mean_net"] > 0 and r["diff_pp"] >= 0.30
          and r["n_months"] > 0 and r["months_pos"] / r["n_months"] >= 0.60
          and a is not None and b is not None and a > 0 and b > 0)
    return "CANDIDATE" if ok else "NEGATIVE"


def selftest():
    t = np.arange(600, 660)
    c = np.linspace(1.0, 2.0, len(t))
    o = forward(t, c, c + 0.01, c - 0.01, minute=610, horizon=30)
    i0 = int(np.searchsorted(t, 610, side="right"))
    i1 = int(np.searchsorted(t, t[i0] + 30, side="right")) - 1
    assert abs(o[0] - (c[i1] / c[i0] - 1 - FR)) < 1e-12, o
    assert forward(t, c, c, c, minute=int(t[-1]) + 5, horizon=30) is None
    print("SELF-TEST PASS", o[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        selftest()
        return
    days = ec.usable_days()
    ec.extract(days)
    cached = len(list(ec.CACHE.glob("events_*.parquet")))
    if cached < len(days):
        sys.exit(f"census cache incomplete: {cached}/{len(days)} - rerun")
    df = collect(days)
    summ = summarize(cells(df))
    for r in summ:
        r["verdict"] = verdict(r)
    (ART / "lb18_drift.json").write_text(json.dumps(
        {"study": "PRE-REG-DRIFT-01", "flags": FLAGS,
         "n_recovered_rows": int(len(df)), "summary": summ}, indent=1, default=str))
    df.to_parquet(ART / "lb18_drift.parquet", index=False)
    for r in summ:
        if r["H"] == 60 and r["n"] >= 200:
            print(f"  {r['dim']}:{r['cell']:12s} H={r['H']} n={r['n']:5d} "
                  f"mean={r['mean_net']:+.4f} ctrl={r['ctrl_net']:+.4f} "
                  f"diff={r['diff_pp']:+.3f}pp m+={r['months_pos']}/{r['n_months']} "
                  f"{r['verdict']}")
    print("artifacts written: lb18_drift.json/.parquet")


if __name__ == "__main__":
    main()
