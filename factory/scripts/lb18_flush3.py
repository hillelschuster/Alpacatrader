#!/usr/bin/env python3
"""Flush-context mining (discovery, causal): within the extreme state
(g100+ / fresh / thrust / in top-3), examine (a) fill realism for a resting
-10% bid, (b) which pre-flush causal context and flush-bar shape separate
recovery from deeper, (c) the descriptive recover-vs-deeper race.

No committed entry/exit/target; fills are modeled only to read realism.
Artifact: factory/artifacts/lb18_flush3.json
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LB = ROOT / "data" / "leaderboard"
ART = ROOT / "factory" / "artifacts"
L = 0.10


def load():
    pp = [pd.read_parquet(p) for p in sorted(LB.glob("path_*.parquet"))]
    ll = [pd.read_parquet(p) for p in sorted(LB.glob("lb_*.parquet"))]
    return (pd.concat(pp, ignore_index=True).sort_values(["date", "ticker", "t"])
            .reset_index(drop=True), pd.concat(ll, ignore_index=True))


def pct(x):
    return None if x is None or not np.isfinite(x) else round(float(x) * 100, 2)


def main():
    paths, lb = load()
    gk = ["date", "ticker"]
    gb = paths.groupby(gk, sort=False)
    c, l, v = paths["c"], paths["l"], paths["v"]
    cmax = gb["c"].transform("cummax")
    flag = (l <= cmax * 0.90).astype(int)
    prior_flush = flag.groupby([paths["date"], paths["ticker"]], sort=False).cumsum() - flag
    feats = pd.DataFrame({
        "pullback": c / cmax - 1,
        "r15": c / gb["c"].shift(15) - 1,
        "volx": v / gb["v"].transform(lambda s: s.rolling(20, min_periods=5).mean()),
        "nbar": paths["n_bars"],
        "prior_flush": prior_flush,
    })
    pp = paths[["date", "ticker", "t", "c"]].join(feats)
    pp["_i"] = np.arange(len(pp))
    m = lb.merge(pp, on=gk + ["t"], how="inner")
    m = m[m["t"] % 5 == 0]
    st = m[(m["gain"] >= 1.0) & (m["pullback"] >= -0.01) & (m["r15"] >= 0.03)].copy()

    store = {}
    for key, g in paths.groupby(gk, sort=False):
        store[key] = (g["t"].to_numpy(), g["o"].to_numpy(float), g["c"].to_numpy(float),
                      g["h"].to_numpy(float), g["l"].to_numpy(float))

    rows = []
    for _, r in st.iterrows():
        key = (r["date"], r["ticker"])
        if key not in store:
            continue
        t_arr, o_arr, c_arr, h_arr, l_arr = store[key]
        pos = int(np.searchsorted(t_arr, int(r["t"])))
        if pos >= len(c_arr):
            continue
        c0 = c_arr[pos]
        if c0 <= 0:
            continue
        end = min(pos + 121, len(t_arr))
        hit = np.where(l_arr[pos + 1:end] <= c0 * (1 - L))[0]
        if len(hit) == 0:
            continue
        i = pos + 1 + int(hit[0])
        bid = c0 * (1 - L)
        o_i, c_i, l_i = o_arr[i], c_arr[i], l_arr[i]
        gap = bool(o_i < bid)
        pf = o_i if gap else bid
        w_h, w_l = h_arr[i + 1:i + 61], l_arr[i + 1:i + 61]
        if len(w_h) == 0:
            continue
        ku = np.where(w_h >= c0)[0]
        kd = np.where(w_l <= pf * 0.90)[0]
        rec = len(ku) > 0
        rec_first = bool(rec and (len(kd) == 0 or ku[0] < kd[0]))
        depth = 1 - l_i / c0
        rows.append({
            "date": r["date"], "ticker": r["ticker"], "t": int(r["t"]),
            "gain": float(r["gain"]), "rank": int(r["rank"]), "tod": int(r["t"]),
            "volx": float(r["volx"]) if np.isfinite(r["volx"]) else np.nan,
            "nbar": int(r["nbar"]), "prior_flush": int(r["prior_flush"]),
            "r15": float(r["r15"]), "px": float(c0), "depth": float(depth),
            "gap": gap, "overshoot": float(l_i / bid - 1), "bar_close_rec": bool(c_i >= bid),
            "rec": rec, "rec_first": rec_first,
            "newx20": bool((w_h >= c0 * 1.20).any()),
            "deeper10": bool((w_l <= pf * 0.90).any()),
            "win_ret": float(c0 / pf - 1), "fmax": float(w_h.max() / pf - 1),
            "fmin": float(w_l.min() / pf - 1), "trec": int(ku[0] + 1) if rec else None,
            "month": r["date"][:7],
        })
    d = pd.DataFrame(rows)
    out = {"n_state": int(len(st)), "n_flush": int(len(d))}

    def tab(col, edges, labels):
        res = {}
        for lo, hi, lb_ in zip(edges[:-1], edges[1:], labels):
            s = d[(d[col] >= lo) & (d[col] < hi)]
            if len(s) == 0:
                continue
            res[lb_] = {
                "n": int(len(s)), "rec": round(float(s["rec"].mean()), 3),
                "rec_first": round(float(s["rec_first"].mean()), 3),
                "newx20": round(float(s["newx20"].mean()), 3),
                "deeper10": round(float(s["deeper10"].mean()), 3),
                "med_trec": (float(s["trec"].median()) if s["trec"].notna().any() else None),
                "med_fmax": pct(s["fmax"].median()), "med_fmin": pct(s["fmin"].median()),
            }
            r_ = res[lb_]
            print(f"  {col:12s} {lb_:12s} n={r_['n']:5d} rec={r_['rec']:.3f} "
                  f"rec1st={r_['rec_first']:.3f} newx={r_['newx20']:.3f} deep={r_['deeper10']:.3f} "
                  f"medT={r_['med_trec']} fmax={r_['med_fmax']} fmin={r_['med_fmin']}")
        return res

    print(f"state n={len(st)}  flushes(filled)={len(d)}")
    print("\n# fill realism")
    out["fill_realism"] = {
        "n_flush": int(len(d)), "gap_through": round(float(d["gap"].mean()), 4),
        "overshoot_med": pct(d["overshoot"].median()), "overshoot_q75": pct(d["overshoot"].quantile(0.75)),
        "bar_close_recovered": round(float(d["bar_close_rec"].mean()), 4),
    }
    print(" ", out["fill_realism"])

    print("\n# pre-flush context (rates by bucket)")
    out["rank"] = tab("rank", [0.5, 1.5, 2.5, 3.5], ["r1", "r2", "r3"])
    out["tod"] = tab("tod", [570, 630, 690, 780, 960], ["am1", "am2", "mid", "pm"])
    out["gain"] = tab("gain", [1, 2, 3, 6, 99], ["100-200", "200-300", "300-600", "600+"])
    out["depth"] = tab("depth", [0.10, 0.15, 0.25, 0.40, 1.0], ["10-15", "15-25", "25-40", "40+"])
    qs = d["volx"].quantile([0.25, 0.5, 0.75]).to_list()
    out["volx"] = tab("volx", [0, *qs, 1e9], ["q1", "q2", "q3", "q4"])
    out["nbar"] = tab("nbar", [0, 20, 40, 80, 1e9], ["<20", "20-40", "40-80", "80+"])
    out["px"] = tab("px", [0, 2, 5, 15, 1e9], ["<2", "2-5", "5-15", "15+"])
    out["prior_flush"] = tab("prior_flush", [0, 1, 2, 1e9], ["0", "1", "2+"])
    out["r15"] = tab("r15", [0.03, 0.06, 0.10, 0.20, 1e9], ["3-6", "6-10", "10-20", "20+"])

    print("\n# race (fill at bid unless gapped; win=c0, stop=-10% from fill)")
    clean = d[~d["gap"]]
    p_all = float(d["rec_first"].mean())
    p_clean = float(clean["rec_first"].mean())
    wr = float(d["win_ret"].mean())
    out["race"] = {
        "all": {"n": int(len(d)), "p_win": round(p_all, 4),
                "ev_descriptive": round(p_all * wr - (1 - p_all) * 0.10, 4)},
        "clean_fill": {"n": int(len(clean)), "p_win": round(p_clean, 4),
                       "ev_descriptive": round(p_clean * wr - (1 - p_clean) * 0.10, 4)},
        "mean_win_pct": pct(wr),
    }
    print(" ", out["race"])

    print("\n# month stability of the key cells (rec_first)")
    cells = {
        "all_flush": d,
        "morning": d[d["tod"] <= 630],
        "morning_rank1": d[(d["tod"] <= 630) & (d["rank"] == 1)],
        "morning_r1_300+": d[(d["tod"] <= 630) & (d["rank"] == 1) & (d["gain"] >= 3)],
    }
    stab = {}
    for name, s in cells.items():
        rows_ = []
        for mo, g in s.groupby("month"):
            if len(g) < 10:
                continue
            rows_.append((mo, len(g), round(float(g["rec_first"].mean()), 3)))
        if rows_:
            p = sum(1 for x in rows_ if x[2] >= 0.5)
            stab[name] = {"n_months": len(rows_), "months_rec_first_ge_50": p, "rows": rows_}
            print(f"  {name:18s} months={len(rows_)} >=50% in {p} "
                  f"mean={np.mean([x[2] for x in rows_]):.3f}")
    out["stability"] = stab

    (ART / "lb18_flush3.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"\nartifact -> {ART/'lb18_flush3.json'}")


if __name__ == "__main__":
    main()
