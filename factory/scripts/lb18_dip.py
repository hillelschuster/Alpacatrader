#!/usr/bin/env python3
"""Dip-response analysis: within the headline state (g100+/fresh/thrust),
when the first adverse flush hits (-10% / -20% below the decision close),
what happens next? Is the flush bought (recovery to breakeven), does it
extend down (deeper), or does a new extension run happen (+20% off low)?

Descriptive counterfactual read of the subsequent path; no committed entry.
Artifact: factory/artifacts/lb18_dip.json
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LB = ROOT / "data" / "leaderboard"
ART = ROOT / "factory" / "artifacts"


def load():
    pp, ll = [], []
    for p in sorted(LB.glob("path_*.parquet")):
        pp.append(pd.read_parquet(p))
    for p in sorted(LB.glob("lb_*.parquet")):
        ll.append(pd.read_parquet(p))
    return (pd.concat(pp, ignore_index=True).sort_values(["date", "ticker", "t"])
            .reset_index(drop=True), pd.concat(ll, ignore_index=True))


def main():
    paths, lb = load()
    gk = ["date", "ticker"]
    store = {}
    for key, g in paths.groupby(gk, sort=False):
        store[key] = (g["t"].to_numpy(), g["c"].to_numpy(float),
                      g["h"].to_numpy(float), g["l"].to_numpy(float))

    gpb = paths.groupby(gk, sort=False)
    feat = pd.DataFrame({
        "pullback": paths["c"] / gpb["c"].transform("cummax") - 1,
        "r15": paths["c"] / gpb["c"].shift(15) - 1,
    })
    feat["date"] = paths["date"]; feat["ticker"] = paths["ticker"]; feat["t"] = paths["t"]
    m = lb.merge(feat, on=gk + ["t"], how="left")
    m = m[m["t"] % 5 == 0]
    fresh = m["pullback"] >= -0.01
    th = m["r15"] >= 0.03
    ext = m["gain"] >= 1.0
    pops = {"state": (ext & fresh & th).fillna(False), "control_g100": ext}

    out = {}
    for name, mask in pops.items():
        sub = m[mask]
        res = {}
        for L in (0.10, 0.20):
            n_pop = len(sub)
            dips = rec = newx = d10 = d20 = rec_first = 0
            trecs, fmaxs, fmins = [], [], []
            bym = {}
            for _, r in sub.iterrows():
                t_arr, c_arr, h_arr, l_arr = store[(r["date"], r["ticker"])]
                pos = int(np.searchsorted(t_arr, int(r["t"])))
                c0 = c_arr[pos]
                if c0 <= 0:
                    continue
                end = min(pos + 121, len(t_arr))
                seg_l = l_arr[pos + 1:end]
                hit = np.where(seg_l <= c0 * (1 - L))[0]
                if len(hit) == 0:
                    continue
                i = pos + 1 + int(hit[0])
                a = l_arr[i]
                w_h = h_arr[i + 1:i + 61]
                w_l = l_arr[i + 1:i + 61]
                if len(w_h) == 0:
                    continue
                dips += 1
                ri = np.where(w_h >= c0)[0]
                ov = int(ri[0]) if len(ri) else -1
                di = np.where(w_l <= a * 0.90)[0]
                dv = int(di[0]) if len(di) else -1
                rec += ov >= 0
                rec_first += (ov >= 0) and (dv < 0 or ov < dv)
                newx += bool((w_h >= c0 * 1.20).any())
                d10 += bool((w_l <= a * 0.90).any())
                d20 += bool((w_l <= a * 0.80).any())
                if ov >= 0:
                    trecs.append(ov + 1)
                fmaxs.append((w_h.max() / a - 1) * 100)
                fmins.append((w_l.min() / a - 1) * 100)
                mo = r["date"][:7]
                bym.setdefault(mo, [0, 0, 0])
                bym[mo][0] += 1
                bym[mo][1] += ov >= 0
                bym[mo][2] += bool((w_h >= c0 * 1.20).any())
            res[L] = {
                "n_pop": int(n_pop),
                "dip_incidence": round(dips / n_pop, 4) if n_pop else None,
                "n_dip": dips,
                "recover_be": round(rec / dips, 3) if dips else None,
                "recover_before_deeper": round(rec_first / dips, 3) if dips else None,
                "newx20": round(newx / dips, 3) if dips else None,
                "deeper10": round(d10 / dips, 3) if dips else None,
                "deeper20": round(d20 / dips, 3) if dips else None,
                "med_trecover": float(np.median(trecs)) if trecs else None,
                "med_fmax_from_dip": round(float(np.median(fmaxs)), 2) if fmaxs else None,
                "med_fmin_from_dip": round(float(np.median(fmins)), 2) if fmins else None,
                "months": {k: [v[0], round(v[1] / v[0], 3) if v[0] else None,
                               round(v[2] / v[0], 3) if v[0] else None]
                           for k, v in sorted(bym.items())},
            }
            r_ = res[L]
            print(f"{name:14s} L={L:.2f} pop={n_pop:6d} dip={r_['dip_incidence']:.3f} "
                  f"rec={r_['recover_be']} rec_first={r_['recover_before_deeper']} "
                  f"newx20={r_['newx20']} "
                  f"deep10={r_['deeper10']} deep20={r_['deeper20']} "
                  f"medT_rec={r_['med_trecover']} fmax={r_['med_fmax_from_dip']} "
                  f"fmin={r_['med_fmin_from_dip']}")
        out[name] = res

    (ART / "lb18_dip.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"\nartifact -> {ART/'lb18_dip.json'}")


if __name__ == "__main__":
    main()
