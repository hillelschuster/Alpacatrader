#!/usr/bin/env python3
"""Discovery inspection on the 18-month lb18 top-3 population.

#1 per-sample subsequent path by regime/rank/time-of-day.
#2 state-conditioned: fresh-high vs pullback, volume burst — extension vs fade.

No targets/brackets/entries: description of the preserved subsequent path only.
Usage: python lb18_inspect.py [month ...]  (default all months)
Artifact: factory/artifacts/lb18_inspect1.json
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LB = ROOT / "data" / "leaderboard"
ART = ROOT / "factory" / "artifacts"

HORIZONS = [5, 15, 30, 60, 120]


def strdict(t):
    return {str(k): v for k, v in t.to_dict("index").items()}


def load_paths(months):
    parts = []
    for p in sorted(LB.glob("path_*.parquet")):
        if p.stem[5:12] in months:
            parts.append(pd.read_parquet(p))
    return pd.concat(parts, ignore_index=True).sort_values(
        ["date", "ticker", "t"]).reset_index(drop=True)


def load_lb(months):
    parts = []
    for p in sorted(LB.glob("lb_*.parquet")):
        if p.stem[3:10] in months:
            parts.append(pd.read_parquet(p))
    return pd.concat(parts, ignore_index=True)


def add_forward(paths):
    g = paths.groupby(["date", "ticker"], sort=False)["c"]
    for h in HORIZONS:
        paths[f"f{h}"] = g.shift(-h) / paths["c"] - 1
    fmax = np.zeros(len(paths))
    fmin = np.zeros(len(paths))
    for h in range(1, 121):
        fwd = (g.shift(-h) / paths["c"] - 1).to_numpy()
        fmax = np.maximum(fmax, np.nan_to_num(fwd, nan=-np.inf))
        fmin = np.minimum(fmin, np.nan_to_num(fwd, nan=np.inf))
    fmax[fmax == -np.inf] = np.nan
    fmin[fmin == np.inf] = np.nan
    paths["fmax120"] = fmax
    paths["fmin120"] = fmin
    return paths


def add_state(paths):
    gb = paths.groupby(["date", "ticker"], sort=False)
    paths["runmax_h"] = gb["h"].cummax()
    paths["pullback"] = paths["c"] / paths["runmax_h"] - 1
    paths["fresh"] = paths["h"] >= paths["runmax_h"] * 0.9999
    paths["vol20"] = gb["v"].transform(
        lambda s: s.rolling(20, min_periods=5).mean())
    paths["vol_x"] = paths["v"] / paths["vol20"]
    return paths


def table(d, by):
    out = d.groupby(by, observed=True).agg(
        n=("f30", "size"),
        med_f30=("f30", "median"), med_f60=("f60", "median"),
        med_f120=("f120", "median"),
        p_ext30=("fmax120", lambda x: float((x >= 0.30).mean())),
        p_fade30=("fmin120", lambda x: float((x <= -0.30).mean())),
    )
    out["ext_fade"] = (out["p_ext30"] / out["p_fade30"]).round(2)
    return out.round(4)


def main():
    months = sys.argv[1:]
    if not months:
        months = {p.stem[3:10] for p in LB.glob("lb_*.parquet")}
    paths = add_state(add_forward(load_paths(months)))
    lb = load_lb(months)
    print(f"samples={len(lb):,}  path-rows={len(paths):,}  days={lb['date'].nunique()}")

    m = lb.merge(paths[["date", "ticker", "t", "f5", "f15", "f30", "f60",
                        "f120", "fmax120", "fmin120", "n_bars", "pullback",
                        "fresh", "vol_x"]],
                 on=["date", "ticker", "t"], how="inner")
    print(f"merged samples={len(m):,}")
    m["gain_pct"] = m["gain"] * 100
    m["regime"] = pd.cut(m["gain_pct"], [-100, 10, 25, 50, 100, 10000],
                         labels=["<10", "10-25", "25-50", "50-100", "100+"])
    m["tod_bucket"] = pd.cut(m["t"], [570, 600, 630, 660, 720, 960],
                             labels=["9:30-10", "10-10:30", "10:30-11",
                                     "11-12", "12-16"])
    m["extreme"] = pd.cut(m["gain_pct"], [-100, 25, 50, 100, 200, 10000],
                          labels=["<25", "25-50", "50-100", "100-200", "200+"])

    res = {}
    for name, by in [("by_regime", "regime"), ("by_rank", "rank"),
                     ("by_tod", "tod_bucket"), ("by_extreme", "extreme"),
                     ("by_regime_rank", ["regime", "rank"])]:
        t = table(m, by)
        res[name] = strdict(t)
        print(f"\n=== {name} ===")
        print(t.to_string())

    # #2 state-conditioned: extension vs fade
    m["pb_b"] = pd.cut(m["pullback"], [-1, -0.10, -0.05, -0.02, -0.005, 100],
                       labels=["<-10", "-10:-5", "-5:-2", "-2:-0.5", "-0.5:hi"])
    m["vol_b"] = pd.cut(m["vol_x"], [0, 0.5, 1, 2, 5, 1e9],
                        labels=["<0.5", "0.5-1", "1-2", "2-5", "5+"])
    for name, by in [("regime_x_fresh", ["regime", "fresh"]),
                     ("regime_x_pb", ["regime", "pb_b"]),
                     ("regime_x_vol", ["regime", "vol_b"]),
                     ("extreme_x_fresh_pb", ["extreme", "fresh", "pb_b"])]:
        t = table(m, by)
        res[name] = strdict(t)
        print(f"\n=== {name} ===")
        print(t.to_string())

    ART.mkdir(exist_ok=True)
    (ART / "lb18_inspect1.json").write_text(json.dumps(res, indent=1, default=str))
    print(f"\nartifact -> {ART/'lb18_inspect1.json'}")


if __name__ == "__main__":
    main()
