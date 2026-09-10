#!/usr/bin/env python3
"""Discovery inspection #2: forward touch structure + burst pre/post shapes.

- P(extension/fade touch) at 30/60/120 min by regime and time-of-day.
- Burst = +30% within 60 min. Compare pre-state (pullback, recent net, vol_x,
  up-fraction) of burst vs non-burst samples within regime.
- Post-burst retention: how much of the excursion survives to +120 min.
- Raw path dumps of the largest burst examples (few, readable).

No targets/entries/exits committed: description of preserved paths only.
Artifact: factory/artifacts/lb18_inspect2.json
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LB = ROOT / "data" / "leaderboard"
ART = ROOT / "factory" / "artifacts"


def strdict(t):
    return {str(k): v for k, v in t.to_dict("index").items()}


def load(months):
    pp, ll = [], []
    for p in sorted(LB.glob("path_*.parquet")):
        if p.stem[5:12] in months:
            pp.append(pd.read_parquet(p))
    for p in sorted(LB.glob("lb_*.parquet")):
        if p.stem[3:10] in months:
            ll.append(pd.read_parquet(p))
    paths = pd.concat(pp, ignore_index=True).sort_values(
        ["date", "ticker", "t"]).reset_index(drop=True)
    return paths, pd.concat(ll, ignore_index=True)


def fwd_extremes(paths, H):
    g = paths.groupby(["date", "ticker"], sort=False)["c"]
    fmax = np.full(len(paths), -np.inf)
    fmin = np.full(len(paths), np.inf)
    for k in range(1, H + 1):
        f = (g.shift(-k) / paths["c"] - 1).to_numpy()
        fmax = np.maximum(fmax, np.nan_to_num(f, nan=-np.inf))
        fmin = np.minimum(fmin, np.nan_to_num(f, nan=np.inf))
    fmax[fmax == -np.inf] = np.nan
    fmin[fmin == np.inf] = np.nan
    return fmax, fmin


def main():
    months = sys.argv[1:]
    if not months:
        months = {p.stem[3:10] for p in LB.glob("lb_*.parquet")}
    paths, lb = load(months)
    print(f"path-rows={len(paths):,}  lb-rows={len(lb):,}")

    gb = paths.groupby(["date", "ticker"], sort=False)
    paths["runmax"] = gb["h"].cummax()
    paths["pullback"] = paths["c"] / paths["runmax"] - 1
    paths["pre_net15"] = paths["c"] / gb["c"].shift(15) - 1
    up = paths["c"].gt(gb["c"].shift(1)).astype(float)
    paths["upfrac5"] = up.groupby([paths["date"], paths["ticker"]]).transform(
        lambda s: s.rolling(5).mean())
    paths["vol20"] = gb["v"].transform(lambda s: s.rolling(20, min_periods=5).mean())
    paths["vol_x"] = paths["v"] / paths["vol20"]

    for H, tag in [(30, "30"), (60, "60"), (120, "120")]:
        fm, fn = fwd_extremes(paths, H)
        paths[f"fmax{tag}"] = fm
        paths[f"fmin{tag}"] = fn
        print(f"computed extremes H={H}")
    paths["f120"] = gb["c"].shift(-120) / paths["c"] - 1

    m = lb.merge(paths[["date", "ticker", "t", "pullback", "pre_net15",
                        "upfrac5", "vol_x", "fmax30", "fmin30", "fmax60",
                        "fmin60", "fmax120", "fmin120", "f120", "n_bars"]],
                 on=["date", "ticker", "t"], how="inner")
    m["gain_pct"] = m["gain"] * 100
    m["regime"] = pd.cut(m["gain_pct"], [-100, 10, 25, 50, 100, 10000],
                         labels=["<10", "10-25", "25-50", "50-100", "100+"])
    m["tod"] = pd.cut(m["t"], [570, 600, 630, 660, 720, 960],
                      labels=["9:30-10", "10-10:30", "10:30-11", "11-12", "12-16"])

    def probs(d, by):
        return d.groupby(by, observed=True).agg(
            n=("t", "size"),
            p_e20_30=("fmax30", lambda x: float((x >= 0.20).mean())),
            p_e30_30=("fmax30", lambda x: float((x >= 0.30).mean())),
            p_e30_60=("fmax60", lambda x: float((x >= 0.30).mean())),
            p_e50_120=("fmax120", lambda x: float((x >= 0.50).mean())),
            p_f20_60=("fmin60", lambda x: float((x <= -0.20).mean())),
            p_f30_120=("fmin120", lambda x: float((x <= -0.30).mean())),
            med_f120=("f120", "median"),
        ).round(4)

    res = {}
    for name, by in [("by_regime", "regime"), ("by_tod", "tod"),
                     ("regime_x_tod", ["regime", "tod"])]:
        t = probs(m, by)
        res[name] = strdict(t)
        print(f"\n=== {name} ===")
        print(t.to_string())

    # burst pre-state comparison within regime
    m["burst"] = m["fmax60"] >= 0.30
    rows = {}
    for reg in ["25-50", "50-100", "100+"]:
        d = m[m["regime"] == reg]
        comp = d.groupby("burst", observed=True).agg(
            n=("t", "size"), pullback=("pullback", "median"),
            pre_net15=("pre_net15", "median"), upfrac5=("upfrac5", "median"),
            vol_x=("vol_x", "median"), n_bars=("n_bars", "median"),
        ).round(4)
        rows[reg] = strdict(comp)
        print(f"\n=== burst pre-state, regime {reg} (burst = +30% within 60m) ===")
        print(comp.to_string())
    res["burst_prestate"] = rows

    b = m[m["burst"]]
    arr = np.array(b["f120"], dtype=float)
    ps = [0.1, 0.25, 0.5, 0.75, 0.9]
    q = {str(p): round(float(v), 4)
         for p, v in zip(ps, np.nanquantile(arr, ps))}
    res["burst_f120_quantiles"] = q
    res["burst_counts"] = {"n_burst": int(len(b)),
                           "share_of_all": round(len(b) / len(m), 4)}
    print(f"\nburst f120 quantiles: {dict(q)}")

    ART.mkdir(exist_ok=True)
    (ART / "lb18_inspect2.json").write_text(json.dumps(res, indent=1, default=str))
    print(f"\nburst samples: {len(b):,} ({len(b)/len(m)*100:.1f}% of {len(m):,})")
    print(f"artifact -> {ART/'lb18_inspect2.json'}")


if __name__ == "__main__":
    main()
