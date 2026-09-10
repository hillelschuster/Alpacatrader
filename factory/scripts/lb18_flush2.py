#!/usr/bin/env python3
"""Flush conditioning: within the extreme state (g100+/fresh/thrust), which
flushes recover? Condition flush-response on depth, drop speed, time-of-day,
rank, and gain bucket. Descriptive; feeds formulation design.
Artifact: factory/artifacts/lb18_flush2.json
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
    state = ((m["gain"] >= 1.0) & (m["pullback"] >= -0.01) & (m["r15"] >= 0.03))
    sub = m[state.fillna(False)]

    rows = []
    for _, r in sub.iterrows():
        t_arr, c_arr, h_arr, l_arr = store[(r["date"], r["ticker"])]
        pos = int(np.searchsorted(t_arr, int(r["t"])))
        c0 = c_arr[pos]
        if c0 <= 0:
            continue
        end = min(pos + 121, len(t_arr))
        seg_l = l_arr[pos + 1:end]
        hit = np.where(seg_l <= c0 * 0.90)[0]
        if len(hit) == 0:
            continue
        kd = int(hit[0]) + 1
        i = pos + kd
        a = l_arr[i]
        w_h = h_arr[i + 1:i + 61]
        w_l = l_arr[i + 1:i + 61]
        if len(w_h) < 5:
            continue
        ri = np.where(w_h >= c0)[0]
        di = np.where(w_l <= a * 0.90)[0]
        ov = int(ri[0]) if len(ri) else -1
        dv = int(di[0]) if len(di) else -1
        # drop speed: bars since price last at/above 0.98*c0 (causal, in window)
        pre_h = h_arr[: i + 1]
        last_hi = np.where(pre_h >= c0 * 0.98)[0]
        speed = kd - int(last_hi[-1]) if len(last_hi) else kd
        rows.append({
            "date": r["date"], "rank": int(r["rank"]), "gain": float(r["gain"]),
            "t_flush": int(r["t"]) + kd, "depth": a / c0 - 1, "speed": speed,
            "rec": ov >= 0, "rec_first": (ov >= 0) and (dv < 0 or ov < dv),
            "newx20": bool((w_h >= c0 * 1.20).any()) if len(w_h) else False,
            "fmax": float(w_h.max() / a - 1) if len(w_h) else np.nan,
            "fmin": float(w_l.min() / a - 1) if len(w_l) else np.nan,
        })
    fl = pd.DataFrame(rows)
    print("flush events:", len(fl))

    def tab(mask, tag):
        s = fl[mask]
        if len(s) < 30:
            return None
        d = {"n": int(len(s)), "rec": round(float(s["rec"].mean()), 3),
             "rec_first": round(float(s["rec_first"].mean()), 3),
             "newx20": round(float(s["newx20"].mean()), 3),
             "med_fmax": round(float(s["fmax"].median()), 3),
             "med_fmin": round(float(s["fmin"].median()), 3)}
        print(f"  {tag:26s} n={d['n']:5d} rec={d['rec']} rec1st={d['rec_first']} "
              f"newx={d['newx20']} fmax={d['med_fmax']} fmin={d['med_fmin']}")
        return d

    out = {}
    print("\n# depth buckets (flush low vs c0)")
    out["depth"] = {
        "-15..-10": tab((fl["depth"] >= -0.15) & (fl["depth"] < -0.10), "-15..-10"),
        "-25..-15": tab((fl["depth"] >= -0.25) & (fl["depth"] < -0.15), "-25..-15"),
        "-40..-25": tab((fl["depth"] >= -0.40) & (fl["depth"] < -0.25), "-40..-25"),
        "deeper": tab(fl["depth"] < -0.40, "< -40"),
    }
    print("\n# drop speed (bars from last >=0.98c0 to flush low)")
    out["speed"] = {
        "fast<=3": tab(fl["speed"] <= 3, "fast (<=3 bars)"),
        "4-10": tab((fl["speed"] >= 4) & (fl["speed"] <= 10), "medium (4-10)"),
        "slow>10": tab(fl["speed"] > 10, "slow (>10)"),
    }
    print("\n# flush time-of-day")
    out["tod"] = {
        "570-630": tab((fl["t_flush"] < 630), "9:30-10:30"),
        "630-690": tab((fl["t_flush"] >= 630) & (fl["t_flush"] < 690), "10:30-11:30"),
        "690-780": tab((fl["t_flush"] >= 690) & (fl["t_flush"] < 780), "11:30-13:00"),
        "780+": tab(fl["t_flush"] >= 780, "13:00+"),
    }
    print("\n# rank / gain")
    out["rank"] = {f"r{r}": tab(fl["rank"] == r, f"rank {r}") for r in (1, 2, 3)}
    out["gain"] = {
        "g100-300": tab(fl["gain"] < 3, "gain 100-300%"),
        "g300+": tab(fl["gain"] >= 3, "gain 300%+"),
    }
    print("\n# depth x speed (recovery)")
    for dep_t, dep_m in [("shallow", fl["depth"] >= -0.20), ("deep", fl["depth"] < -0.20)]:
        for sp_t, sp_m in [("fast", fl["speed"] <= 5), ("slow", fl["speed"] > 5)]:
            out[f"{dep_t}_{sp_t}"] = tab(dep_m & sp_m, f"{dep_t} x {sp_t}")

    (ART / "lb18_flush2.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"\nartifact -> {ART/'lb18_flush2.json'}")


if __name__ == "__main__":
    main()
