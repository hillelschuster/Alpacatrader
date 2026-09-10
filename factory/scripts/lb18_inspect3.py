#!/usr/bin/env python3
"""Discovery inspection #3: subsequent PATH SHAPE for causal candidate states.

States defined ONLY from observables at t (no burst/future knowledge):
  regime = causal gain bucket; fresh = c at running high; thrust = pre_net15>=3%;
  dip = pullback<=-10%; morning = t<=630; volhi = vol_x>=1.
Characterize the preserved subsequent path per state: touch probs, f-return
quantiles, MFE/MAE and WHEN the extremes occur. Plus raw example paths.

No committed targets/entries/exits — description only.
Artifact: factory/artifacts/lb18_inspect3.json
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
    return (pd.concat(pp, ignore_index=True).sort_values(["date", "ticker", "t"])
            .reset_index(drop=True), pd.concat(ll, ignore_index=True))


def main():
    months = sys.argv[1:]
    if not months:
        months = {p.stem[3:10] for p in LB.glob("lb_*.parquet")}
    paths, lb = load(months)
    gb = paths.groupby(["date", "ticker"], sort=False)

    paths["runmax"] = gb["h"].cummax()
    paths["pullback"] = paths["c"] / paths["runmax"] - 1
    paths["pre_net15"] = paths["c"] / gb["c"].shift(15) - 1
    paths["vol20"] = gb["v"].transform(lambda s: s.rolling(20, min_periods=5).mean())
    paths["vol_x"] = paths["v"] / paths["vol20"]
    paths["f120"] = gb["c"].shift(-120) / paths["c"] - 1

    # path shape: cumulative median f at multiple +h
    for h in [5, 15, 30, 60]:
        paths[f"f{h}"] = gb["c"].shift(-h) / paths["c"] - 1

    def extreme(paths, H):
        fmax = np.full(len(paths), -np.inf)
        fmin = np.full(len(paths), np.inf)
        tmax = np.zeros(len(paths))
        tmin = np.zeros(len(paths))
        for k in range(1, H + 1):
            f = (gb["c"].shift(-k) / paths["c"] - 1).to_numpy()
            f = np.nan_to_num(f, nan=-np.inf)
            upd = f > fmax
            fmax = np.where(upd, f, fmax)
            tmax = np.where(upd, k, tmax)
            g2 = np.nan_to_num((gb["c"].shift(-k) / paths["c"] - 1).to_numpy(),
                               nan=np.inf)
            dn = g2 < fmin
            fmin = np.where(dn, g2, fmin)
            tmin = np.where(dn, k, tmin)
        fmax[fmax == -np.inf] = np.nan
        fmin[fmin == np.inf] = np.nan
        return fmax, fmin, tmax, tmin

    fmax120, fmin120, tmax120, tmin120 = extreme(paths, 120)
    paths["fmax120"] = fmax120
    paths["fmin120"] = fmin120
    paths["tmax120"] = np.where(np.isnan(fmax120), np.nan, tmax120)
    paths["tmin120"] = np.where(np.isnan(fmin120), np.nan, tmin120)

    m = lb.merge(paths[["date", "ticker", "t", "pullback", "pre_net15", "vol_x",
                        "f5", "f15", "f30", "f60", "f120", "fmax120", "fmin120",
                        "tmax120", "tmin120"]],
                 on=["date", "ticker", "t"], how="inner")
    m["gain_pct"] = m["gain"] * 100
    m["regime"] = pd.cut(m["gain_pct"], [-100, 25, 50, 100, 10000],
                         labels=["<25", "25-50", "50-100", "100+"])
    m["fresh"] = m["pullback"] >= -0.005
    m["thrust"] = m["pre_net15"] >= 0.03
    m["dip"] = m["pullback"] <= -0.10
    m["morning"] = m["t"] <= 630

    states = {
        "A ext_fresh_morning": (m.regime == "100+") & m.fresh & m.morning,
        "B ext_thrust_morning": (m.regime == "100+") & m.thrust & m.morning,
        "C ext_dip_morning": (m.regime == "100+") & m.dip & m.morning,
        "D mod_fresh_morning": (m.regime == "50-100") & m.fresh & m.morning,
        "E mod_thrust_morning": (m.regime == "50-100") & m.thrust & m.morning,
        "F base_ext_morning": (m.regime == "100+") & m.morning,
        "G base_mod_morning": (m.regime == "50-100") & m.morning,
        "H ext_thrust_any": (m.regime == "100+") & m.thrust,
        "I ext_any": (m.regime == "100+"),
    }

    rows = {}
    for name, mask in states.items():
        d = m[mask]
        if len(d) < 30:
            continue
        a = np.array(d["f120"], dtype=float)
        rows[name] = {
            "n": int(len(d)),
            "p_e30_60": round(float((d["fmax120"] >= 0.30).mean()), 4),
            "p_e50_120": round(float((d["fmax120"] >= 0.50).mean()), 4),
            "p_f20_60": round(float((d["fmin120"] <= -0.20).mean()), 4),
            "p_f30_120": round(float((d["fmin120"] <= -0.30).mean()), 4),
            "med_f5": round(float(d["f5"].median()), 4),
            "med_f15": round(float(d["f15"].median()), 4),
            "med_f30": round(float(d["f30"].median()), 4),
            "med_f60": round(float(d["f60"].median()), 4),
            "med_f120": round(float(d["f120"].median()), 4),
            "q10_f120": round(float(np.nanquantile(a, 0.10)), 4),
            "q25_f120": round(float(np.nanquantile(a, 0.25)), 4),
            "q75_f120": round(float(np.nanquantile(a, 0.75)), 4),
            "q90_f120": round(float(np.nanquantile(a, 0.90)), 4),
            "med_mfe120": round(float(d["fmax120"].median()), 4),
            "med_mae120": round(float(d["fmin120"].median()), 4),
            "med_tmax": round(float(d["tmax120"].median()), 1),
            "med_tmin": round(float(d["tmin120"].median()), 1),
            "p_mfe_gt_mae": round(float((d["fmax120"] > -d["fmin120"]).mean()), 4),
        }
    tab = pd.DataFrame(rows).T
    print(tab.to_string())

    ART.mkdir(exist_ok=True)
    (ART / "lb18_inspect3.json").write_text(json.dumps(rows, indent=1, default=str))
    print(f"\nartifact -> {ART/'lb18_inspect3.json'}")

    # raw example paths: largest +60m excursions from state B (ext thrust morning)
    dd = m[states["B ext_thrust_morning"]].copy()
    dd = dd.sort_values("fmax120", ascending=False).head(6)
    for _, r in dd.iterrows():
        p = paths[(paths["date"] == r["date"]) & (paths["ticker"] == r["ticker"])]
        seg = p[(p["t"] >= r["t"] - 15) & (p["t"] <= r["t"] + 75)]
        head = (f"\n### {r['date']} {r['ticker']} t={int(r['t'])} "
                f"gain={r['gain_pct']:.1f}% fmax120={r['fmax120']*100:.0f}%")
        print(head)
        out = []
        for _, b in seg.iterrows():
            mark = " <-- t" if b["t"] == r["t"] else ""
            out.append(f"  {int(b['t'])} c={b['c']:.2f} v={int(b['v'])} "
                       f"g={b['gain_c']*100:.0f}%{mark}" if pd.notna(b["gain_c"])
                       else f"  {int(b['t'])} halt{mark}")
        print("\n".join(out[:60]))


if __name__ == "__main__":
    main()
