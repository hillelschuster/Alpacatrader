#!/usr/bin/env python3
"""Subsequent-path characterization of the headline causal state.

State discovery context: lb18_causal.py found no strict positive-drift state,
but extreme-gain (>=100%) + fresh (at session running max of causal close) +
thrust (r15 >= +3%) in-top3 names show a month-stable UP-FIRST ORDERING
(up-first ~0.39 vs dn-first ~0.23 on +-30%/120m) plus a fat extension tail
(e30/60 26%, q75_f120 +14%), all fade-dominant in medians.

This script describes the subsequent path (no committed target/horizon):
  - forward return quantile curves f5..f120
  - MFE/MAE and time-to-extreme
  - first-touch ordering across a ladder of natural +/- pairs
  - raw example paths
  - month-by-month recurrence of the ordering
Artifact: factory/artifacts/lb18_state.json
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LB = ROOT / "data" / "leaderboard"
ART = ROOT / "factory" / "artifacts"

UP_LEVELS = [0.20, 0.30, 0.40, 0.50]
DN_LEVELS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.40]
PAIRS = [(0.20, 0.10), (0.30, 0.15), (0.40, 0.20), (0.50, 0.25),
         (0.20, 0.20), (0.30, 0.30), (0.40, 0.40)]


def load():
    pp, ll = [], []
    for p in sorted(LB.glob("path_*.parquet")):
        pp.append(pd.read_parquet(p))
    for p in sorted(LB.glob("lb_*.parquet")):
        ll.append(pd.read_parquet(p))
    return (pd.concat(pp, ignore_index=True).sort_values(["date", "ticker", "t"])
            .reset_index(drop=True), pd.concat(ll, ignore_index=True))


def describe(m, tag):
    if len(m) < 60:
        return None
    d = {"n": int(len(m)), "gain_med": round(float(m["gain"].median()), 3)}
    for k in (5, 15, 30, 60, 90, 120):
        fk = m[f"f{k}"]
        d[f"f{k}"] = {q: round(float(fk.quantile(q)), 4)
                      for q in (0.1, 0.25, 0.5, 0.75, 0.9)}
    d["fmax120"] = {q: round(float(m["fmax120"].quantile(q)), 4)
                    for q in (0.25, 0.5, 0.75, 0.9)}
    d["tmax_med"] = int(m.loc[m["tmax60"] >= 1, "tmax60"].median())
    d["tmin_med"] = int(m.loc[m["tmin60"] >= 1, "tmin60"].median())
    pairs = {}
    for lu, ld in PAIRS:
        ku, kd = m[f"ku{int(lu*100)}"], m[f"kd{int(ld*100)}"]
        upf = (ku < kd) | ((ku < 999) & (kd == 999))
        dnf = (kd < ku) | ((kd < 999) & (ku == 999))
        pairs[f"+{int(lu*100)}/-{int(ld*100)}"] = {
            "up": round(float(upf.mean()), 4), "dn": round(float(dnf.mean()), 4),
            "none": round(float((~upf & ~dnf).mean()), 4)}
    d["pairs"] = pairs
    print(f"  {tag:40s} n={d['n']:6d} gain={d['gain_med']*100:5.0f}%  pairs: "
          + " ".join(f"{k}:{v['up']:.2f}/{v['dn']:.2f}" for k, v in pairs.items()))
    return d


def main():
    paths, lb = load()
    gk = ["date", "ticker"]
    gb = paths.groupby(gk, sort=False)
    c = paths["c"].to_numpy()
    n = len(paths)

    fs = {}
    for k in (5, 15, 30, 60, 90, 120):
        fs[f"f{k}"] = (gb["c"].shift(-k) / paths["c"] - 1).to_numpy()

    fm60 = np.full(n, -np.inf); fn60 = np.full(n, np.inf)
    fm120 = np.full(n, -np.inf); fn120 = np.full(n, np.inf)
    tmax60 = np.full(n, -1, dtype=np.int32); tmin60 = np.full(n, -1, dtype=np.int32)
    kus = {int(l * 100): np.full(n, 999, dtype=np.int32) for l in UP_LEVELS}
    kds = {int(l * 100): np.full(n, 999, dtype=np.int32) for l in DN_LEVELS}
    for k in range(1, 121):
        f = fs.get(f"f{k}")
        if f is None:
            f = (gb["c"].shift(-k) / paths["c"] - 1).to_numpy()
        if k <= 60:
            m_up = f > fm60
            fm60 = np.where(m_up, f, fm60)
            tmax60 = np.where(m_up & ~np.isnan(f), k, tmax60)
            m_dn = f < fn60
            fn60 = np.where(m_dn, f, fn60)
            tmin60 = np.where(m_dn & ~np.isnan(f), k, tmin60)
        fm120 = np.fmax(fm120, np.nan_to_num(f, nan=-np.inf))
        fm120[fm120 == -np.inf] = np.nan
        hu = gb["h"].shift(-k).to_numpy()
        hl = gb["l"].shift(-k).to_numpy()
        for lf in UP_LEVELS:
            arr = kus[int(lf * 100)]
            m1 = (hu >= c * (1 + lf)) & (arr == 999)
            arr[m1] = k
        for lf in DN_LEVELS:
            arr = kds[int(lf * 100)]
            m2 = (hl <= c * (1 - lf)) & (arr == 999)
            arr[m2] = k

    pp = paths[["date", "ticker", "t"]].copy()
    pp["f60"] = fs["f60"]; pp["f120"] = fs["f120"]
    pp["f5"] = fs["f5"]; pp["f15"] = fs["f15"]; pp["f30"] = fs["f30"]; pp["f90"] = fs["f90"]
    pp["fmax120"] = fm120
    pp["tmax60"] = tmax60; pp["tmin60"] = tmin60
    # MFE/MAE in 120 via rolling max of forward highs is expensive; approximate
    # with touch levels already computed + fm120 (close path) for quantiles.
    for lv in UP_LEVELS:
        pp[f"ku{int(lv*100)}"] = kus[int(lv * 100)]
    for lv in DN_LEVELS:
        pp[f"kd{int(lv*100)}"] = kds[int(lv * 100)]

    m = lb.merge(pp, on=["date", "ticker", "t"], how="inner")
    pats = paths[["date", "ticker", "t", "c", "h", "l", "v"]].copy()
    gpb = pats.groupby(gk, sort=False)
    pats["pullback"] = pats["c"] / gpb["c"].transform("cummax") - 1
    pats["r15"] = pats["c"] / gpb["c"].shift(15) - 1
    m = m.merge(pats[["date", "ticker", "t", "pullback", "r15"]],
                on=["date", "ticker", "t"], how="left")
    m = m[m["t"] % 5 == 0]
    m["_m"] = m["date"].str[:7]

    fresh = m["pullback"] >= -0.01
    th = m["r15"] >= 0.03
    ext = m["gain"] >= 1.0
    S1 = ext & fresh & th
    S1m = S1 & (m["t"] <= 660)

    out = {}
    print("# subsequent-path description")
    out["control_g100"] = describe(m[ext], "control: gain>=100% (in-top3)")
    out["state_g100_fresh_thrust"] = describe(m[S1], "STATE: g100+ fresh+thrust")
    out["state_g100_fresh_thrust_morn"] = describe(m[S1m], "STATE: g100+ fresh+thrust morning")
    out["control_rank1_g100"] = describe(m[ext & (m["rank"] == 1)], "control: rank1 g100+")

    months = []
    for mo, s in m[S1].groupby("_m"):
        if len(s) < 15:
            continue
        ku, kd = s["ku30"], s["kd30"]
        upf = ((ku < kd) | ((ku < 999) & (kd == 999))).mean()
        dnf = ((kd < ku) | ((kd < 999) & (ku == 999))).mean()
        fm = s["fmax120"].median()
        months.append({"month": mo, "n": len(s), "upf30": round(float(upf), 3),
                       "dnf30": round(float(dnf), 3), "fmax120_med": round(float(fm), 4)})
    out["state_months"] = months
    pos = sum(1 for r in months if r["upf30"] > r["dnf30"])
    print(f"\n# STATE month recurrence: up>dn in {pos}/{len(months)} months")

    print("\n# example state paths (gain_c % at t, t+5..t+60):")
    ex = []
    sample = m[S1].sample(min(5, int(S1.sum())), random_state=7)
    for _, r in sample.iterrows():
        g = paths[(paths["date"] == r["date"]) & (paths["ticker"] == r["ticker"])]
        g = g[(g["t"] >= r["t"]) & (g["t"] <= r["t"] + 60)].iloc[::5]
        path = [round(x * 100, 1) for x in g["gain_c"]]
        ex.append({"date": r["date"], "ticker": r["ticker"], "t": int(r["t"]),
                   "gain_at_t": round(float(r["gain"]) * 100, 1), "path_5min": path})
        print(f"  {r['date']} {r['ticker']:6s} t={int(r['t'])} gain={r['gain']*100:6.1f}% -> "
              + " ".join(f"{x:.0f}" for x in path))
    out["examples"] = ex

    (ART / "lb18_state.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"\nartifact -> {ART/'lb18_state.json'}")


if __name__ == "__main__":
    main()
