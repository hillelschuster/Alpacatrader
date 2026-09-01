"""Entry-minute pct_gain distribution for E6 entries (both years).

Purpose: the research universe conditions ticker-days on day-max-gain >= 8% (certify_month.py:
"candidates (day max close >= prev_close, day max gain >= 8%, gap==1)"). Live we cannot know
that. This measures how much of the condition is already satisfied causally at the E6 entry
minute, to pick the correct causal live proxy (a current-gain floor) WITHOUT tuning on returns.
"""
import pickle
import numpy as np
import polars as pl
from exposure_design import load_stream, FEATS, THETA, ENTRY_CAP

def e6_entries(year, model):
    pool, m3, comp = load_stream(year, model)
    comp = comp.filter(pl.col("tod_min") <= ENTRY_CAP)
    eps = {}
    for r in comp.to_dicts():
        eps.setdefault((r["ticker"], str(r["et_date"])[:10]), []).append(r)
    out = []
    for key, evs in eps.items():
        evs.sort(key=lambda r: r["tod_min"])
        pool_evs = [e for e in evs if e["rvol"] is not None and e["rvol"] > 8]
        if not pool_evs:
            continue
        picks, t = [pool_evs[0]], pool_evs[0]["tod_min"]
        while True:
            nxt = next((x for x in pool_evs if x["tod_min"] >= t + 61), None)
            if not nxt:
                break
            picks.append(nxt); t = nxt["tod_min"]
        out.extend(picks)
    return pl.DataFrame(out)

import os
if os.path.basename(os.getcwd()) == "scripts":
    os.chdir("../..")
model = pickle.load(open("factory/artifacts/ml/model_v1.pkl", "rb"))
for year in ("2025", "2026"):
    df = e6_entries(year, model)
    pg = df["pct_gain_grid"].to_numpy()
    pg = pg[~np.isnan(pg)] if pg.dtype == np.float64 else pg
    print(f"=== {year}: E6 entries n={len(pg)} ===")
    for q in (1, 5, 10, 25, 50, 75, 90, 99):
        print(f"  p{q:>2}: {np.percentile(pg, q):+.2f}%")
    for thr in (3, 4, 5, 6, 7, 8, 9, 10):
        print(f"  share entry pct_gain >= {thr}%: {(pg >= thr).mean():.3f}")
    # also: at the FIRST entry of each episode only (where the day condition is least satisfied)
    first = df.filter(pl.col("tod_min") == pl.col("tod_min").min().over(["ticker", "et_date"]))
    fp = first["pct_gain_grid"].to_numpy()
    print(f"  first-entry only n={len(fp)}: share >=8%: {(fp >= 8).mean():.3f}  >=6%: {(fp >= 6).mean():.3f}  >=5%: {(fp >= 5).mean():.3f}")
    cd = df["cum_dv"].to_numpy() / 1e6
    print(f"  entry cum_dv $M: p1={np.percentile(cd,1):.1f} p5={np.percentile(cd,5):.1f} p25={np.percentile(cd,25):.1f} p50={np.percentile(cd,50):.1f}")
    print(f"  share entry cum_dv >= 5M: {(cd >= 5).mean():.3f}  >= 10M: {(cd >= 10).mean():.3f}  >= 20M: {(cd >= 20).mean():.3f}")
    tm = df["tod_min"].to_numpy()
    print(f"  entry tod_min: p1={np.percentile(tm,1):.0f} p5={np.percentile(tm,5):.0f} p25={np.percentile(tm,25):.0f} p50={np.percentile(tm,50):.0f} min={tm.min():.0f}")
    print(f"  share entry tod_min >= 45: {(tm >= 45).mean():.3f}  >= 60: {(tm >= 60).mean():.3f}")
