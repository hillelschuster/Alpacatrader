#!/usr/bin/env python3
"""Runner-side study: exit policies and entry filters on the strong fills
(flush bar closed >= bid). Loads lb18_lifecycle_orders.parquet + paths.
Artifact: factory/artifacts/lb18_runners.parquet (+ tiny json)
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lb18_episodes import load  # noqa: E402

ART = ROOT / "factory" / "artifacts"
FRICTION = 0.01
STOP_L = 0.10


def sim(bars, o, h, l, c, t, entry, kind, T=0.20, brk=None, cap=None):
    """Exit simulator over new-bar positions `bars` (absolute indices).
    trail: hard floor -10%, trail T from peak.
    ratchet: floor rises to entry once peak >= entry*(1+brk), trail T.
    eod: exit at last close.
    cap: optional time cap in minutes (exit at close of last bar within cap).
    """
    if kind == "eod":
        return float(c[int(bars[-1])]) / entry - 1
    floor0 = entry * (1 - STOP_L)
    runmax = entry
    t0 = t[int(bars[0])]
    for j in bars:
        if cap is not None and t[j] > t0 + cap:
            prev = int(np.searchsorted(bars, j)) - 1
            return float(c[int(bars[prev])]) / entry - 1
        floor = floor0
        if kind == "ratchet" and runmax >= entry * (1 + brk):
            floor = entry
        lvl = max(floor, runmax * (1 - T))
        if l[j] <= lvl:
            return min(float(o[j]), lvl) / entry - 1
        if h[j] > runmax:
            runmax = float(h[j])
    return float(c[int(bars[-1])]) / entry - 1


def main():
    orders = pd.read_parquet(ART / "lb18_lifecycle_orders.parquet")
    paths, _ = load()
    store = {}
    for key, g in paths.groupby(["date", "ticker"], sort=False):
        g = g.sort_values("t")
        t = g["t"].to_numpy(np.int32)
        o = g["o"].to_numpy(float); h = g["h"].to_numpy(float)
        l = g["l"].to_numpy(float); c = g["c"].to_numpy(float)
        nb = g["n_bars"].to_numpy(np.int32)
        new = np.empty(len(nb), bool)
        new[0] = nb[0] > 0
        new[1:] = nb[1:] > nb[:-1]
        store[key] = (t, o, h, l, c, np.where(new)[0])

    strong = orders[orders["fill_close"] >= 0].copy()
    print(f"strong fills: {len(strong)}")

    rows = []
    for r in strong.itertuples():
        key = (r.date, r.ticker)
        if key not in store:
            continue
        t, o, h, l, c, npx = store[key]
        B = r.c0 * 0.9
        j0 = int(np.searchsorted(t[npx], r.t0 + 1, "left"))
        jf = None
        for jj in range(j0, len(npx)):
            if l[npx[jj]] <= B:
                jf = jj
                break
        if jf is None:
            continue
        fp = int(npx[jf])
        fw = npx[jf + 1:]
        if len(fw) < 3:
            continue
        B2 = float(o[fw[0]])
        rec = {"date": r.date, "month": r.date[:7], "ticker": r.ticker,
               "rank": r.rank, "gain": r.gain, "fc": r.fill_close,
               "depth": r.depth, "r15": r.r15, "volx": r.volx,
               "t0": r.t0, "tf": r.tf, "prem": B2 / B - 1}
        for tag, entry, bars in [("bid", B, fw), ("abs", B2, fw[1:])]:
            if len(bars) < 3:
                continue
            rec[f"{tag}_t15f"] = sim(bars, o, h, l, c, t, entry, "trail", T=0.15) - FRICTION
            rec[f"{tag}_t20f"] = sim(bars, o, h, l, c, t, entry, "trail", T=0.20) - FRICTION
            rec[f"{tag}_t25f"] = sim(bars, o, h, l, c, t, entry, "trail", T=0.25) - FRICTION
            rec[f"{tag}_rat5_20"] = sim(bars, o, h, l, c, t, entry, "ratchet", T=0.20, brk=0.05) - FRICTION
            rec[f"{tag}_rat10_15"] = sim(bars, o, h, l, c, t, entry, "ratchet", T=0.15, brk=0.10) - FRICTION
            rec[f"{tag}_cap60"] = sim(bars, o, h, l, c, t, entry, "trail", T=0.20, cap=60) - FRICTION
            rec[f"{tag}_cap120"] = sim(bars, o, h, l, c, t, entry, "trail", T=0.20, cap=120) - FRICTION
            rec[f"{tag}_eod"] = sim(bars, o, h, l, c, t, entry, "eod") - FRICTION
        rows.append(rec)
    d = pd.DataFrame(rows)
    d.to_parquet(ART / "lb18_runners.parquet")
    print(f"rows: {len(d)}")

    def stat(col, mask=None):
        s = d if mask is None else d[mask]
        v = s[col].dropna()
        if len(v) < 10:
            return None
        mv = s.groupby("month")[col].mean().dropna()
        return {"n": int(len(v)), "mean": round(float(v.mean()), 4),
                "med": round(float(v.median()), 4),
                "months_pos": int((mv > 0).sum()), "n_months": int(len(mv))}

    print("\n# exit policies (all strong fills), entry at bid vs absorb-next-open")
    out = {}
    pols = [c for c in d.columns if c.startswith(("bid_", "abs_"))]
    for p in pols:
        s = stat(p)
        if s:
            out[p] = s
            print(f"  {p:14s} n={s['n']:3d} mean={s['mean']:+.4f} med={s['med']:+.4f} "
                  f"months+={s['months_pos']}/{s['n_months']}")

    print("\n# filters x policies")
    filt = {
        "all": np.ones(len(d), bool),
        "fc>=0.02": (d.fc >= 0.02).values,
        "r15>=0.20": (d.r15 >= 0.20).values,
        "fc>=0.02 & r15>=0.2": ((d.fc >= 0.02) & (d.r15 >= 0.20)).values,
        "depth<=13": (d.depth <= 0.13).values,
        "depth<=13 & fc>=0.02": ((d.depth <= 0.13) & (d.fc >= 0.02)).values,
        "rank1": (d["rank"] == 1).values,
    }
    for fname, mask in filt.items():
        for p in ["bid_t20f", "bid_rat5_20", "bid_cap60", "abs_t20f", "abs_rat5_20"]:
            s = stat(p, mask)
            if s:
                print(f"  {fname:20s} {p:12s} n={s['n']:3d} mean={s['mean']:+.4f} "
                      f"med={s['med']:+.4f} months+={s['months_pos']}/{s['n_months']}")

    (ART / "lb18_runners.json").write_text(json.dumps({"rows": len(d)}, indent=1))
    print(f"\nparquet -> {ART/'lb18_runners.parquet'}")


if __name__ == "__main__":
    main()
