#!/usr/bin/env python3
"""Discovery inspection #4: recurring local-shape clusters of top-3 minutes.

For every top-3 minute sample with >=11 bars, compute a 10-bar price/volume
fingerprint (causal, from preserved paths):
  r10, r5, r3 (net moves), dd10 (drawdown from 10-bar high), upfrac10,
  volratio (last3 vs prior7), volx (last bar vs window mean), miss10 (bar holes)
K-means (k=10) on the standardized fingerprints -> recurring shapes.
Per cluster: context (gain, rank, tod) + subsequent path stats (t%5==0 dedup,
t<=839 for full 120-bar window):
  f5..f120 medians, q25/q75_f120, P(+30%/60m), P(-20%/60m),
  first-touch ordering P(+30% before -20% within 120m).

Description only. Artifact: factory/artifacts/lb18_clusters.json
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LB = ROOT / "data" / "leaderboard"
ART = ROOT / "factory" / "artifacts"


def load(months):
    pp, ll = [], []
    for p in sorted(LB.glob("path_*.parquet")):
        if p.stem[5:12] in months:
            pp.append(pd.read_parquet(p))
    for p in sorted(LB.glob("lb_*.parquet")):
        if p.stem[3:10] in months:
            ll.append(pd.read_parquet(p))
    return (pd.concat(pp, ignore_index=True).sort_values(["date", "ticker", "t"])
            .reset_index(drop=True), pd.concat(ll, ignore_index=True))


def main():
    months = sys.argv[1:]
    if not months:
        months = {p.stem[3:10] for p in LB.glob("lb_*.parquet")}
    paths, lb = load(months)
    gb = paths.groupby(["date", "ticker"], sort=False)
    c, h, l, v = paths["c"], paths["h"], paths["l"], paths["v"]
    nb = paths["n_bars"]

    dd10 = c / gb["h"].transform(lambda s: s.rolling(10, min_periods=10).max()) - 1
    up = c.gt(gb["c"].shift(1)).astype(float)
    up10 = up.groupby([paths["date"], paths["ticker"]]).transform(
        lambda s: s.rolling(10, min_periods=10).mean())
    vm10 = gb["v"].transform(lambda s: s.rolling(10, min_periods=10).mean())
    v3 = gb["v"].transform(lambda s: s.rolling(3, min_periods=3).mean())
    v7p = gb["v"].transform(lambda s: s.rolling(7, min_periods=7).mean().shift(3))

    feats = pd.DataFrame({
        "r10": c / gb["c"].shift(10) - 1,
        "r5": c / gb["c"].shift(5) - 1,
        "r3": c / gb["c"].shift(3) - 1,
        "dd10": dd10,
        "up10": up10,
        "vratio": v3 / v7p,
        "volx": v / vm10,
        "miss10": (10 - (nb - gb["n_bars"].shift(10))).clip(0, 10).fillna(0),
    })
    ok = feats[["r10", "dd10", "up10", "vratio", "volx"]].notna().all(axis=1)
    print(f"path-rows={len(paths):,}  feats-ok={int(ok.sum()):,}")

    for hz in [5, 15, 30, 60, 120]:
        paths[f"f{hz}"] = gb["c"].shift(-hz) / c - 1
    for hz, tag in [(60, "60"), (120, "120")]:
        fmax = np.full(len(paths), -np.inf)
        fmin = np.full(len(paths), np.inf)
        for k in range(1, hz + 1):
            f = (gb["c"].shift(-k) / c - 1).to_numpy()
            fmax = np.maximum(fmax, np.nan_to_num(f, nan=-np.inf))
            fmin = np.minimum(fmin, np.nan_to_num(f, nan=np.inf))
        fmax[fmax == -np.inf] = np.nan
        fmin[fmin == np.inf] = np.nan
        paths[f"fmax{tag}"] = fmax
        paths[f"fmin{tag}"] = fmin

    # first-touch ordering within 120 bars (up: h>=+30%, dn: l<=-20%)
    n = len(paths)
    ku = np.full(n, 999, dtype=np.int32)
    kd = np.full(n, 999, dtype=np.int32)
    for k in range(1, 121):
        hu = gb["h"].shift(-k).to_numpy()
        hl = gb["l"].shift(-k).to_numpy()
        cu = c.to_numpy()
        m1 = (hu >= cu * 1.30) & (ku == 999)
        ku[m1] = k
        m2 = (hl <= cu * 0.80) & (kd == 999)
        kd[m2] = k
    t_order = np.where((ku < 999) & (kd == 999), 1,
                       np.where((kd < 999) & (ku == 999), -1,
                                np.where((ku < 999) & (kd < 999),
                                         np.where(ku < kd, 1, -1), 0)))
    paths["order"] = t_order

    allf = pd.concat([paths[["date", "ticker", "t"]],
                      feats,
                      paths[["f5", "f15", "f30", "f60", "f120", "fmax60",
                             "fmin60", "fmax120", "fmin120", "order"]]], axis=1)
    allf = allf[ok]
    m = lb.merge(allf, on=["date", "ticker", "t"], how="inner")
    print(f"samples with fingerprint: {len(m):,}")

    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler
    cols = ["r10", "r5", "r3", "dd10", "up10", "vratio", "volx", "miss10"]
    X = m[cols].to_numpy(float)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    rng = np.random.default_rng(7)
    idx = rng.choice(len(X), size=min(120000, len(X)), replace=False)
    sc = StandardScaler().fit(X[idx])
    km = KMeans(n_clusters=10, n_init=10, random_state=7).fit(sc.transform(X[idx]))
    m["cl"] = km.predict(sc.transform(X))

    sub = m[(m["t"] % 5 == 0) & (m["t"] <= 839)].copy()
    rows = {}
    for cl, d in sub.groupby("cl"):
        cent = {k: round(float(m.loc[m["cl"] == cl, k].median()), 4) for k in cols}
        rows[int(cl)] = {
            "n": int(len(d)),
            "ctx": {
                "med_gain_pct": round(float(d["gain"].median() * 100), 1),
                "med_rank": round(float(d["rank"].median()), 1),
                "med_tod": int(d["t"].median()),
                "centroid": cent,
            },
            "fwd": {
                "med_f5": round(float(d["f5"].median()), 4),
                "med_f15": round(float(d["f15"].median()), 4),
                "med_f30": round(float(d["f30"].median()), 4),
                "med_f60": round(float(d["f60"].median()), 4),
                "med_f120": round(float(d["f120"].median()), 4),
                "q25_f120": round(float(np.nanquantile(d["f120"], 0.25)), 4),
                "q75_f120": round(float(np.nanquantile(d["f120"], 0.75)), 4),
                "p_e30_60": round(float((d["fmax60"] >= 0.30).mean()), 4),
                "p_f20_60": round(float((d["fmin60"] <= -0.20).mean()), 4),
                "p_e50_120": round(float((d["fmax120"] >= 0.50).mean()), 4),
            },
        }
        o = d["order"]
        rows[int(cl)]["order"] = {
            "p_up_first": round(float((o == 1).mean()), 4),
            "p_dn_first": round(float((o == -1).mean()), 4),
            "p_none": round(float((o == 0).mean()), 4),
        }

    # need fmax60/fmin60 for touch probs; recompute quickly on sub
    df = pd.DataFrame(rows).T
    print(df.to_string())

    ART.mkdir(exist_ok=True)
    (ART / "lb18_clusters.json").write_text(json.dumps(rows, indent=1, default=str))
    print(f"\nartifact -> {ART/'lb18_clusters.json'}")


if __name__ == "__main__":
    main()
