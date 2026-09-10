#!/usr/bin/env python3
"""OOS evaluation of the frozen flush-bid rule (PRE-REG-FLUSH-01).
Months-filtered port of the rolling-bid lifecycle engine from lb18_roll.py:
same strict state minute, rolling -10% bid refreshed while flat with 120-min
expiry, fill on first new-bar touch at the bid, tl30 exit, re-arm after exit,
100bps friction. Loader reads only path_/lb_ files for the requested months.
Usage: python lb18_oos.py --months 2024-01 2024-02 ... [--tag oos]
Dev parity check (required before OOS per pre-reg): --months <dev span>
must reproduce lb18_roll.json stats exactly.
Artifact: factory/artifacts/lb18_oos{_tag}.json (+ .parquet fills)
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lb18_episodes import build  # noqa: E402

LB = ROOT / "data" / "leaderboard"
ART = ROOT / "factory" / "artifacts"
FR = 0.01
STOP_L = 0.10
WIN = 120
TL = 30


def load_months(months):
    months = set(months)
    pp, ll = [], []
    for p in sorted(LB.glob("path_*.parquet")):
        if p.name[5:12] in months:
            pp.append(pd.read_parquet(p))
    for p in sorted(LB.glob("lb_*.parquet")):
        if p.name[3:10] in months:
            ll.append(pd.read_parquet(p))
    if not pp or not ll:
        sys.exit(f"no leaderboard files for {sorted(months)}")
    paths = (pd.concat(pp, ignore_index=True)
             .sort_values(["date", "ticker", "t"]).reset_index(drop=True))
    lb = pd.concat(ll, ignore_index=True)
    return paths, lb


def sim_tl30(fut, o, h, l, c, t, B, c0):
    stop = B * (1 - STOP_L)
    n = 0
    for j in fut:
        if l[j] <= stop:
            return min(float(o[j]), stop) / B - 1, int(t[j])
        if h[j] >= c0:
            return c0 / B - 1, int(t[j])
        n += 1
        if n >= TL:
            return float(c[j]) / B - 1, int(t[j])
    if len(fut) == 0:
        return None, None
    j = int(fut[-1])
    return float(c[j]) / B - 1, int(t[j])


def stats(df, tag):
    if len(df) == 0:
        print(f"{tag:24s} n=0")
        return {"n": 0}
    v = df["ret"]
    mo = df.groupby("month")["ret"].mean()
    d = {"n": int(len(df)), "mean": round(float(v.mean()), 4),
         "med": round(float(v.median()), 4),
         "pos": round(float((v > 0).mean()), 3),
         "months_pos": int((mo > 0).sum()), "n_months": int(len(mo)),
         "worst_month": round(float(mo.min()), 4)}
    print(f"{tag:24s} n={d['n']:5d} mean={d['mean']:+.4f} med={d['med']:+.4f} "
          f"pos={d['pos']:.2f} months+={d['months_pos']}/{d['n_months']} "
          f"worst={d['worst_month']:+.4f}")
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="+", required=True)
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    paths, lb = load_months(a.months)
    A = build(paths)
    gpb = paths.groupby(["date", "ticker"], sort=False)
    paths["pullback"] = paths["c"] / gpb["c"].transform("cummax") - 1
    paths["r15"] = paths["c"] / gpb["c"].shift(15) - 1
    m = lb.merge(paths[["date", "ticker", "t", "c", "pullback", "r15",
                        "prior_flush"]], on=["date", "ticker", "t"], how="left")
    strict = (m["gain"] >= 1.0) & (m["pullback"] >= -0.01) & (m["r15"] >= 0.03)
    st = m[strict.fillna(False)].copy()
    st_groups = {k: g.sort_values("t").reset_index(drop=True)
                 for k, g in st.groupby(["date", "ticker"], sort=False)}

    rows = []
    for key, gs in st_groups.items():
        d = A.get(key)
        if d is None:
            continue
        t, o, h, l, c = d["t"], d["o"], d["h"], d["l"], d["c"]
        npx = d["newpos"]
        st_t = gs["t"].to_numpy()
        n = len(gs)
        si = 0
        B = None
        c0 = None
        row = None
        b_t = None
        flat = True
        exit_t = -10**9
        for jj in range(len(npx)):
            j = int(npx[jj])
            tj = int(t[j])
            if not flat and tj > exit_t:
                flat = True
                B = None
                c0 = None
            while si < n and int(st_t[si]) < tj:
                if flat:
                    rr = gs.iloc[si]
                    pos0 = int(np.searchsorted(t, int(st_t[si])))
                    c0n = float(c[pos0])
                    if c0n > 0:
                        B = c0n * (1 - STOP_L)
                        c0 = c0n
                        row = rr
                        b_t = int(st_t[si])
                si += 1
            if not flat or B is None:
                continue
            if b_t is not None and tj - b_t > WIN:
                B = None
                continue
            if l[j] <= B:
                ret, ex = sim_tl30(npx[jj:], o, h, l, c, t, B, c0)
                if ret is None:
                    continue
                fc = float(c[j]) / B - 1
                rows.append({
                    "date": row["date"], "month": row["date"][:7],
                    "ticker": row["ticker"], "t0": int(row["t"]), "tf": tj,
                    "gain": float(row["gain"]), "rank": int(row["rank"]),
                    "r15": float(row["r15"]),
                    "prior_flush": int(row["prior_flush"]),
                    "fc": fc, "ret": ret - FR, "exit_t": int(ex)})
                flat = False
                exit_t = int(ex)
                B = None
                c0 = None
    dr = pd.DataFrame(rows)
    suffix = f"_{a.tag}" if a.tag else ""
    dr.to_parquet(ART / f"lb18_oos{suffix}.parquet")
    out = {"months": sorted(a.months), "rule": "rolling-bid tl30 (PRE-REG-FLUSH-01)"}
    out["all"] = stats(dr, "all fills")
    if len(dr):
        out["pf2"] = stats(dr[dr["prior_flush"] >= 2], "pf>=2")
        out["rank1"] = stats(dr[dr["rank"] == 1], "rank1")
        mo = dr[dr["prior_flush"] >= 2].groupby("month")["ret"].agg(
            ["count", "mean"]).round(4)
        print("\nmonthly pf>=2:")
        print(mo.to_string())
        out["monthly_pf2"] = {k: {"n": int(v["count"]), "mean": float(v["mean"])}
                              for k, v in mo.iterrows()}
        w = dr.nsmallest(5, "ret")[["date", "ticker", "tf", "fc", "ret"]]
        print("\nworst 5 fills:")
        print(w.to_string(index=False))
        out["worst5"] = w.to_dict("records")
    (ART / f"lb18_oos{suffix}.json").write_text(
        json.dumps(out, indent=1, default=str))
    print(f"\nartifact -> {ART / f'lb18_oos{suffix}.json'}")


if __name__ == "__main__":
    main()
