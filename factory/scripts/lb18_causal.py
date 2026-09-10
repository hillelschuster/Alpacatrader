#!/usr/bin/env python3
"""Causal discovery pass on the top-3 population (leak-immune by construction:
population = lb rows, i.e. names in top-3 at t; features from bars <= t).

States: gain regime x fresh(at high) x morning x rank; thrust15, vol_x.
Outcomes: P(+30%/60m), P(-20%/60m), symmetric up-first +-30 within 120,
median f60/f120, q75/q90.
Includes month-blocked stability for headline cells (recurring = evidence).
Artifact: factory/artifacts/lb18_causal.json
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
    gb = paths.groupby(gk, sort=False)
    c, h, l, v = paths["c"], paths["h"], paths["l"], paths["v"]

    feats = pd.DataFrame({
        "pullback": c / gb["c"].transform("cummax") - 1,
        "r15": c / gb["c"].shift(15) - 1,
        "volx": v / gb["v"].transform(lambda s: s.rolling(20, min_periods=5).mean()),
        "n_bars": paths["n_bars"],
    })
    paths["f60"] = gb["c"].shift(-60) / c - 1
    paths["f120"] = gb["c"].shift(-120) / c - 1
    n = len(paths)
    fm60 = np.full(n, np.nan)
    fn60 = np.full(n, np.nan)
    fm120 = np.full(n, np.nan)
    ku = np.full(n, 999, dtype=np.int32)
    kd = np.full(n, 999, dtype=np.int32)
    cu = c.to_numpy()
    for k in range(1, 121):
        f = (gb["c"].shift(-k) / c - 1).to_numpy()
        if k <= 60:
            fm60 = np.fmax(fm60, np.nan_to_num(f, nan=-np.inf))
            fn60 = np.fmin(fn60, np.nan_to_num(f, nan=np.inf))
        fm120 = np.fmax(fm120, np.nan_to_num(f, nan=-np.inf))
        hu = gb["h"].shift(-k).to_numpy()
        hl = gb["l"].shift(-k).to_numpy()
        m1 = (hu >= cu * 1.30) & (ku == 999)
        ku[m1] = k
        m2 = (hl <= cu * 0.70) & (kd == 999)
        kd[m2] = k
    fm60[fm60 == -np.inf] = np.nan
    fn60[fn60 == np.inf] = np.nan
    fm120[fm120 == -np.inf] = np.nan
    up = (ku < 999) & ((kd == 999) | (ku < kd))
    dn = (kd < 999) & ((ku == 999) | (kd < ku))

    pp = paths[["date", "ticker", "t", "f60", "f120"]].join(feats)
    pp["_i"] = np.arange(len(pp))
    m = lb.merge(pp, on=["date", "ticker", "t"], how="inner")
    idx = m["_i"].to_numpy()
    m["e30_60"] = fm60[idx] >= 0.30
    m["f20_60"] = fn60[idx] <= -0.20
    m["e50_120"] = fm120[idx] >= 0.50
    m["upf30"] = up[idx]
    m["dnf30"] = dn[idx]
    m["_m"] = m["date"].str[:7]
    m = m[m["t"] % 5 == 0]

    def line(mask, tag):
        s = m[mask]
        if len(s) < 60:
            return None
        d = {
            "n": int(len(s)),
            "gain": round(float(s["gain"].median() * 100), 0),
            "f60": round(float(s["f60"].median()), 4),
            "f120": round(float(s["f120"].median()), 4),
            "q75_f120": round(float(s["f120"].quantile(0.75)), 4),
            "e30_60": round(float(s["e30_60"].mean()), 4),
            "f20_60": round(float(s["f20_60"].mean()), 4),
            "e50_120": round(float(s["e50_120"].mean()), 4),
            "upf30": round(float(s["upf30"].mean()), 4),
            "dnf30": round(float(s["dnf30"].mean()), 4),
        }
        print(f"  {tag:44s} n={d['n']:6d} g={d['gain']:5.0f}% f60={d['f60']:+.3f} "
              f"f120={d['f120']:+.3f} q75={d['q75_f120']:+.3f} "
              f"e30/60={d['e30_60']:.3f} f20/60={d['f20_60']:.3f} "
              f"upf30={d['upf30']:.3f} dnf30={d['dnf30']:.3f}")
        return d

    out = {"base": line(m["t"] >= 0, "BASE (all in-top3, t%5)")}
    gr = [("g<25", (0, 0.25)), ("g25-50", (0.25, 0.5)), ("g50-100", (0.5, 1.0)),
          ("g100-200", (1.0, 2.0)), ("g200+", (2.0, 99.0))]
    print("\n# by gain regime")
    for t_, (lo, hi) in gr:
        out[t_] = line((m["gain"] >= lo) & (m["gain"] < hi), t_)
    print("\n# by gain regime x fresh x morning (the extreme-at-moment question)")
    fresh = m["pullback"] >= -0.01
    morn = m["t"] <= 660
    for t_, (lo, hi) in gr:
        base = (m["gain"] >= lo) & (m["gain"] < hi)
        out[f"{t_}_fresh_morn"] = line(base & fresh & morn, f"{t_} fresh morn")
        out[f"{t_}_fresh_any"] = line(base & fresh, f"{t_} fresh any")
        out[f"{t_}_notfresh_morn"] = line(base & ~fresh & morn, f"{t_} notfresh morn")
    print("\n# thrust x gain")
    th = m["r15"] >= 0.03
    for t_, (lo, hi) in gr:
        base = (m["gain"] >= lo) & (m["gain"] < hi)
        out[f"{t_}_thrust"] = line(base & th, f"{t_} thrust15")
        out[f"{t_}_fresh_thrust"] = line(base & th & fresh, f"{t_} fresh+thrust")
    print("\n# rank x gain (100+)")
    for r in (1, 2, 3):
        out[f"r{r}_100+"] = line((m["rank"] == r) & (m["gain"] >= 1), f"rank{r} g100+")
        out[f"r{r}_all"] = line(m["rank"] == r, f"rank{r} all")
    print("\n# vol_x quartile within g100+")
    g100 = m[m["gain"] >= 1]
    if len(g100):
        qs = g100["volx"].quantile([0.25, 0.5, 0.75]).to_dict()
        for q, rng in [("q1", (0, qs[0.25])), ("q2", (qs[0.25], qs[0.5])),
                       ("q3", (qs[0.5], qs[0.75])), ("q4", (qs[0.75], 99))]:
            out[f"g100_volx_{q}"] = line((m["gain"] >= 1) & (m["volx"] >= rng[0]) & (m["volx"] < rng[1]), f"g100+ volx {q}")

    # month stability of headline cells
    print("\n# month stability (cell: e30_60 / upf30)")
    cells = {
        "g100+_fresh_morn": (m["gain"] >= 1) & fresh & morn,
        "g50-100_fresh_morn": (m["gain"].between(0.5, 1.0)) & fresh & morn,
        "g100+_thrust": (m["gain"] >= 1) & th,
        "rank1_g100+": (m["rank"] == 1) & (m["gain"] >= 1),
    }
    stab = {}
    for name, mask in cells.items():
        mm = m[mask.fillna(False) if hasattr(mask, "fillna") else mask]
        rows = []
        for mo, s in mm.groupby("_m"):
            if len(s) < 20:
                continue
            rows.append((mo, len(s), round(float(s["e30_60"].mean()), 3),
                         round(float(s["upf30"].mean() - s["dnf30"].mean()), 3)))
        stab[name] = rows
        pos = sum(1 for r in rows if r[3] > 0)
        print(f"  {name:24s} months={len(rows)} up-dn>0 in {pos} "
              f"mean_e30_60={np.mean([r[2] for r in rows]):.3f}")
    out["stability"] = stab

    (ART / "lb18_causal.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"\nartifact -> {ART/'lb18_causal.json'}")


if __name__ == "__main__":
    main()
