#!/usr/bin/env python3
"""Diagnose lifecycle-vs-sequential-events gap + rolling-bid lifecycle variant.
(1) Diff strict-lifecycle fills (lb18_relax.parquet) vs seq-filtered events on
(date,ticker,tf): counts, exclusive means, paired return gap.
(2) Rolling-bid lifecycle: while flat, refresh a resting -10% bid at EVERY
fresh state minute (level tracks latest state close, expires 120 min after
placement); fill = first new bar low <= current bid; tl30 exit; re-arm after
exit. Test all-bids vs pf>=2 bids vs stay/abort-by-fill-close rules.
Artifact: factory/artifacts/lb18_roll.json
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lb18_episodes import build, load  # noqa: E402

ART = ROOT / "factory" / "artifacts"
FR = 0.01
STOP_L = 0.10
WIN = 120
TL = 30


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
        print(f"{tag:46s} n=0")
        return {"n": 0}
    v = df["ret"]
    mo = df.groupby("month")["ret"].mean()
    d = {"n": int(len(df)), "mean": round(float(v.mean()), 4),
         "med": round(float(v.median()), 4),
         "pos": round(float((v > 0).mean()), 3),
         "months_pos": int((mo > 0).sum()), "n_months": int(len(mo)),
         "worst_month": round(float(mo.min()), 4)}
    print(f"{tag:46s} n={d['n']:5d} mean={d['mean']:+.4f} med={d['med']:+.4f} "
          f"pos={d['pos']:.2f} months+={d['months_pos']}/{d['n_months']} "
          f"worst={d['worst_month']:+.4f}")
    return d


def main():
    paths, lb = load()
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

    print("=== (1) lifecycle-strict vs seq-events diff ===")
    L = pd.read_parquet(ART / "lb18_relax.parquet")
    Ls = L[L["mode"] == "strict"].copy()
    ev = pd.read_parquet(ART / "lb18_episodes_events.parquet")
    ev = ev[~ev["censored"]].copy()
    recs = []
    for r in ev.itertuples():
        d = A.get((r.date, r.ticker))
        if d is None:
            continue
        t, o, h, l, c = d["t"], d["o"], d["h"], d["l"], d["c"]
        npx = d["newpos"]
        jf = int(np.searchsorted(t[npx], r.tf))
        if jf >= len(npx) or int(t[npx[jf]]) != int(r.tf):
            continue
        c0 = float(r.px)
        B = c0 * (1 - STOP_L)
        ret, ex = sim_tl30(npx[jf:], o, h, l, c, t, B, c0)
        if ret is None:
            continue
        recs.append({"date": r.date, "month": r.date[:7], "ticker": r.ticker,
                     "t0": int(r.t0), "tf": int(r.tf), "ret": ret - FR,
                     "exit_t": int(ex)})
    dfe = pd.DataFrame(recs).sort_values(["date", "ticker", "t0"])
    taken = np.zeros(len(dfe), bool)
    last = {}
    for i, (_, r) in enumerate(dfe.iterrows()):
        k = (r["date"], r["ticker"])
        if r["t0"] > last.get(k, -10**9):
            taken[i] = True
            last[k] = r["exit_t"]
    S = dfe[taken].copy()
    j = Ls.merge(S, on=["date", "ticker", "tf"], how="outer",
                 suffixes=("_L", "_S"), indicator=True)
    cnt = j["_merge"].value_counts().to_dict()
    print("  overlap:", cnt)
    both = j[j["_merge"] == "both"]
    lon = j[j["_merge"] == "left_only"]
    son = j[j["_merge"] == "right_only"]
    print(f"  both n={len(both)} retL={both['ret_L'].mean():+.4f} "
          f"retS={both['ret_S'].mean():+.4f} gapS-L={(both['ret_S']-both['ret_L']).mean():+.4f}")
    if len(lon):
        print(f"  L-only n={len(lon)} retL={lon['ret_L'].mean():+.4f}")
    if len(son):
        print(f"  S-only n={len(son)} retS={son['ret_S'].mean():+.4f}")

    print("\n=== (2) rolling-bid lifecycle (refresh at every state minute) ===")
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
                    "r15": float(row["r15"]), "prior_flush": int(row["prior_flush"]),
                    "fc": fc, "ret": ret - FR, "exit_t": int(ex)})
                flat = False
                exit_t = int(ex)
                B = None
                c0 = None
    dr = pd.DataFrame(rows)
    dr.to_parquet(ART / "lb18_roll.parquet")
    out = {}
    out["diff"] = {"counts": cnt,
                   "both_retL": round(float(both["ret_L"].mean()), 4),
                   "both_retS": round(float(both["ret_S"].mean()), 4)}
    out["all"] = stats(dr, "rolling all fills")
    if len(dr) == 0:
        sys.exit("no rolling fills")
    out["pf2"] = stats(dr[dr["prior_flush"] >= 2], "rolling pf>=2")
    out["rank1"] = stats(dr[dr["rank"] == 1], "rolling rank1")
    print()
    for th in (0.0, 0.02, 0.05):
        for tag, sub in [("all", dr), ("pf2", dr[dr["prior_flush"] >= 2])]:
            v = np.where(sub["fc"] >= th, sub["ret"], sub["fc"] - FR)
            tmp = sub.copy()
            tmp["ret"] = v
            out[f"rule_{tag}_fc{th}"] = stats(tmp, f"{tag} stay fc>={th} else abort@close")
            v2 = np.where(sub["fc"] >= th, sub["ret"], -FR)  # exit at bid (approx)
            tmp["ret"] = v2
            out[f"rule_{tag}_fc{th}_abortB"] = stats(
                tmp, f"{tag} stay fc>={th} else abort@bid")
    print("\nmonthly: pf2 stay fc>=0.02 else abort@close")
    sub = dr[dr["prior_flush"] >= 2].copy()
    sub["ret"] = np.where(sub["fc"] >= 0.02, sub["ret"], sub["fc"] - FR)
    mo = sub.groupby("month")["ret"].agg(["count", "mean"]).round(4)
    print(mo.to_string())
    out["monthly_pf2_rule"] = mo["mean"].to_dict()
    (ART / "lb18_roll.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"\nartifact -> {ART/'lb18_roll.json'}")


if __name__ == "__main__":
    main()
