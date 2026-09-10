#!/usr/bin/env python3
"""Scale-out runner study: sell f1 at c0 (pre-flush close), trail the rest.
Correct trail semantics: running max of highs since entry (includes pre-c0 bars);
remainder floor -10% below fill; optional breakeven lock after c0 hit.
Loads event set (all unique flush fills) + paths. Discovery-grade artifact:
factory/artifacts/lb18_scaleout.json + lb18_scaleout.parquet
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
FLOOR = 0.10
F1S = (1 / 3, 0.5, 2 / 3)
TS = (0.15, 0.20, 0.25)


def trail_exit(bars, o, h, l, c, entry, runmax0, T, floor_lvl):
    runmax = runmax0
    for j in bars:
        lvl = max(floor_lvl, runmax * (1 - T))
        if l[j] <= lvl:
            return min(float(o[j]), lvl) / entry - 1
        if h[j] > runmax:
            runmax = float(h[j])
    return float(c[int(bars[-1])]) / entry - 1


def scaleout(bars, o, h, l, c, entry, c0, f1, T, lock=False):
    hit = None
    runmax = entry
    for idx, j in enumerate(bars):
        if h[j] > runmax:
            runmax = float(h[j])
        if h[j] >= c0:
            hit = idx
            break
    if hit is None:
        return trail_exit(bars, o, h, l, c, entry, entry, T, entry * (1 - FLOOR))
    h1 = f1 * (c0 / entry - 1)
    rest = bars[hit + 1:]
    rm = max(runmax, c0)
    floor = entry if lock else entry * (1 - FLOOR)
    if len(rest) == 0:
        return h1
    return h1 + (1 - f1) * trail_exit(rest, o, h, l, c, entry, rm, T, floor)


def main():
    ev = pd.read_parquet(ART / "lb18_episodes_events.parquet")
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

    ev2 = ev[~ev["censored"]].copy()
    rows = []
    for r in ev2.itertuples():
        key = (r.date, r.ticker)
        if key not in store:
            continue
        t, o, h, l, c, npx = store[key]
        B = r.px * 0.9
        c0 = r.px
        j0 = int(np.searchsorted(t[npx], r.t0 + 1, "left"))
        jf = None
        for jj in range(j0, len(npx)):
            if l[npx[jj]] <= B:
                jf = jj
                break
        if jf is None:
            continue
        fw = npx[jf + 1:]
        if len(fw) < 4:
            continue
        rec = {"date": r.date, "month": r.date[:7], "ticker": r.ticker,
               "rank": r.rank, "gain": r.gain, "fc": r.fill_close,
               "depth": r.depth, "r15": r.r15, "t0": r.t0, "tf": r.tf,
               "px": c0}
        rec["t15f"] = trail_exit(fw, o, h, l, c, B, B, 0.15, B * 0.9) - FRICTION
        rec["t20f"] = trail_exit(fw, o, h, l, c, B, B, 0.20, B * 0.9) - FRICTION
        for f1 in F1S:
            for T in TS:
                rec[f"so{f1:.2f}_t{T:.2f}"] = scaleout(fw, o, h, l, c, B, c0, f1, T) - FRICTION
        for f1 in (0.5, 2 / 3):
            for T in (0.15, 0.20):
                rec[f"lk{f1:.2f}_t{T:.2f}"] = scaleout(fw, o, h, l, c, B, c0, f1, T, lock=True) - FRICTION
        rows.append(rec)
    d = pd.DataFrame(rows)
    d.to_parquet(ART / "lb18_scaleout.parquet")
    print(f"events: {len(d)}")

    def stats(mask, tag, pols):
        s = d[mask]
        print(f"\n{tag} (n={len(s)})")
        for p in pols:
            v = s[p].dropna()
            mv = s.groupby("month")[p].mean().dropna()
            print(f"  {p:12s} mean={v.mean():+.4f} med={v.median():+.4f} "
                  f"pos={(v>0).mean():.2f} months+={int((mv>0).sum())}/{len(mv)}")

    allp = ["t15f", "t20f"] + [f"so{f1:.2f}_t{T:.2f}" for f1 in F1S for T in TS] + \
           [f"lk{f1:.2f}_t{T:.2f}" for f1 in (0.5, 2 / 3) for T in (0.15, 0.20)]
    stats(np.ones(len(d), bool), "ALL events", allp)
    stats((d.fc >= 0.02).values, "strong fc>=0.02", allp)
    stats((d.fc < 0).values, "weak fc<0", allp)

    s = d[(d.fc >= 0.02)]
    hit = 0
    for r in s.itertuples():
        key = (r.date, r.ticker)
        t, o, h, l, c, npx = store[key]
        B = r.px * 0.9
        j0 = int(np.searchsorted(t[npx], r.t0 + 1, "left"))
        jf = None
        for jj in range(j0, len(npx)):
            if l[npx[jj]] <= B:
                jf = jj
                break
        if jf is None:
            continue
        fw = npx[jf + 1:]
        if len(fw) < 4:
            continue
        if (h[fw] >= r.px).any():
            hit += 1
    print(f"\nstrong subset c0 hit rate: {hit/len(s):.3f}")
    (ART / "lb18_scaleout.json").write_text(json.dumps({"rows": len(d)}, indent=1))
    print(f"-> {ART/'lb18_scaleout.parquet'}")


if __name__ == "__main__":
    main()
