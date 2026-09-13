#!/usr/bin/env python3
# /// script
# dependencies = ["pandas", "pyarrow", "numpy"]
# ///
"""PRE-REG-CENSUS-01 producer: vacuum-event census for extreme top gainers.

Measurement only (no trading rule, no H025 recomputation). See
researches/PRE-REG-CENSUS-01.md. Resumable per-day cache in
data/scratch_census/. Usage:
  python event_census.py --self-test
  python event_census.py --run
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LB = ROOT / "data" / "leaderboard"
CACHE = ROOT / "data" / "scratch_census"
ART = ROOT / "factory" / "artifacts"
L = 0.10
HORIZON = 30
N_BLOCKS = 3
POWER_FLOOR = 500
FREQ_FLOOR = 3.0
REC_TOL_PP = 5.0
MAXREC_TOL_PP = 2.0
COLUMNS = ["date", "ticker", "t", "rank", "prior_flush", "seq", "pre_gap_min",
           "gain_event", "depth", "l_event", "ref", "recovered_30m",
           "t_recover_min", "max_recovery", "adverse_from_low"]


def events_for_symbol(day, tk, g, lb):
    g = g.sort_values("t")
    t = g["t"].to_numpy()
    if len(t) == 0:
        return []
    h = g["h"].to_numpy(float)
    l = g["l"].to_numpy(float)
    c = g["c"].to_numpy(float)
    nb = g["n_bars"].to_numpy(float) if "n_bars" in g.columns else np.arange(1, len(g) + 1.0)
    gv = g["gain_c"].to_numpy(float) if "gain_c" in g.columns else np.full(len(g), np.nan)
    new = np.empty(len(g), bool)
    new[0] = True
    new[1:] = nb[1:] > nb[:-1]
    cm = np.maximum.accumulate(c)
    under = l <= cm * (1 - L)
    prev = np.empty(len(g), bool)
    prev[0] = False
    prev[1:] = under[:-1]
    real = under & ~prev & new
    cnt = np.cumsum(real) - real
    prev_new_t = np.full(len(g), np.nan)
    last = None
    for i in range(len(g)):
        prev_new_t[i] = last if last is not None else np.nan
        if new[i]:
            last = t[i]
    rank_map = {}
    if lb is not None and len(lb):
        sub = lb[lb["ticker"] == tk]
        rank_map = dict(zip(sub["t"].astype(int), sub["rank"].astype(int)))
    rows = []
    for j in np.flatnonzero(real):
        t0 = int(t[j])
        ref = float(cm[j])
        el = float(l[j])
        w = (t > t0) & (t <= t0 + HORIZON)
        if w.any():
            hit = np.flatnonzero(w & (h >= ref))
            rec = bool(len(hit) > 0)
            trec = float(t[hit[0]] - t0) if rec else np.nan
            maxrec = float(h[w].max() / ref - 1)
            adv = float(l[w].min() / el - 1)
        else:
            rec, trec, maxrec, adv = False, np.nan, 0.0, np.nan
        gap = float(t0 - prev_new_t[j]) if not np.isnan(prev_new_t[j]) else np.nan
        rows.append(dict(
            date=day, ticker=tk, t=t0, rank=rank_map.get(t0, np.nan),
            prior_flush=int(cnt[j]), seq=int(cnt[j]) + 1, pre_gap_min=gap,
            gain_event=float(gv[j]), depth=float(el / ref - 1), l_event=el,
            ref=ref, recovered_30m=rec, t_recover_min=trec,
            max_recovery=maxrec, adverse_from_low=adv))
    return rows


def events_for_day(day):
    p = pd.read_parquet(LB / f"path_{day}.parquet")
    try:
        lb = pd.read_parquet(LB / f"lb_{day}.parquet")
        lb = lb[["ticker", "t", "rank"]].drop_duplicates(["ticker", "t"], keep="last")
    except Exception:
        lb = None
    if p is None or len(p) == 0 or not {"ticker", "t", "h", "l", "c"}.issubset(p.columns):
        return []
    rows = []
    for tk, g in p.groupby("ticker", sort=False):
        rows.extend(events_for_symbol(day, tk, g, lb))
    return rows


def usable_days():
    days = []
    for f in sorted(LB.glob("path_*.parquet")):
        d = f.name[len("path_"):-len(".parquet")]
        if len(d) == 10 and f.stat().st_size > 0:
            days.append(d)
    return days


def extract(days, limit=None):
    CACHE.mkdir(parents=True, exist_ok=True)
    todo = [d for d in days if not (CACHE / f"events_{d}.parquet").exists()]
    if limit:
        todo = todo[:limit]
    for i, d in enumerate(todo, 1):
        try:
            rows = events_for_day(d)
        except Exception as e:
            print(f"  skip {d}: {type(e).__name__}: {e}")
            rows = []
        pd.DataFrame(rows, columns=COLUMNS).to_parquet(CACHE / f"events_{d}.parquet", index=False)
        if i % 100 == 0:
            print(f"  {i}/{len(todo)} days", flush=True)
    return len(todo)


def load_all(days):
    parts = []
    for d in days:
        f = CACHE / f"events_{d}.parquet"
        if f.exists():
            parts.append(pd.read_parquet(f))
    if not parts:
        return pd.DataFrame(columns=COLUMNS)
    return pd.concat(parts, ignore_index=True)


def label_cells(df):
    df = df.copy()
    df["rank_c"] = np.where(df["rank"].notna() & (df["rank"] <= 3),
                            df["rank"].astype("Int64").astype(str), "off")
    df["pf_c"] = np.where(df["prior_flush"] == 0, "0",
                          np.where(df["prior_flush"] == 1, "1", "2+"))
    df["seq_c"] = np.where(df["seq"] == 1, "1", np.where(df["seq"] == 2, "2", "3+"))
    df["gap_c"] = np.where(df["pre_gap_min"].fillna(0) >= 5, "gap>=5", "no_gap")
    df["tod_c"] = np.where(df["t"] < 720, "AM", "PM")
    return df


def summarize(df, dims, n_days, pooled):
    rows = []
    for dim in dims:
        for key, sub in df.groupby(dim, observed=True):
            if len(sub) == 0:
                continue
            rec = sub["recovered_30m"]
            t_rec = sub.loc[rec, "t_recover_min"]
            block_rec = []
            for b in range(N_BLOCKS):
                sb = sub[sub["block"] == b]
                block_rec.append(round(float(sb["recovered_30m"].mean()), 4) if len(sb) else None)
            rows.append({
                "dim": dim, "cell": str(key), "n": int(len(sub)),
                "events_per_day": round(len(sub) / n_days, 3),
                "recovered_30m": round(float(rec.mean()), 4),
                "t_recover_med": round(float(t_rec.median()), 2) if len(t_rec) else None,
                "max_recovery_med": round(float(sub["max_recovery"].median()), 4),
                "adverse_p05": round(float(sub["adverse_from_low"].quantile(0.05)), 4)
                if sub["adverse_from_low"].notna().any() else None,
                "depth_med": round(float(sub["depth"].median()), 4),
                "block_rec": block_rec,
                "rec_vs_pooled_pp": round((float(rec.mean()) - pooled["recovered_30m"]) * 100, 2),
                "maxrec_vs_pooled_pp": round((float(sub["max_recovery"].median())
                                              - pooled["max_recovery_med"]) * 100, 2),
            })
    return rows


def apply_gate(row, pooled):
    checks = {
        "power_floor": row["n"] >= POWER_FLOOR,
        "freq_floor": row["events_per_day"] >= FREQ_FLOOR,
        "rec_not_worse": row["recovered_30m"] >= pooled["recovered_30m"] - REC_TOL_PP / 100,
        "maxrec_not_worse": row["max_recovery_med"] >= pooled["max_recovery_med"] - MAXREC_TOL_PP / 100,
        "blocks_above_pooled": sum(1 for b in row["block_rec"]
                                   if b is not None and b > pooled["recovered_30m"]) >= 2,
    }
    row["gate"] = checks
    row["worth_rule_prereg"] = all(checks.values())
    if not checks["power_floor"]:
        row["verdict"] = "INCONCLUSIVE_POWER"
    elif row["worth_rule_prereg"]:
        row["verdict"] = "WORTH_RULE_PREREG"
    else:
        row["verdict"] = "NO"
    return row


def run():
    days = usable_days()
    print(f"universe: {len(days)} usable days")
    n_new = extract(days)
    print(f"extracted {n_new} new day caches")
    df = load_all(days)
    print(f"events: {len(df)}")
    if len(df) == 0:
        sys.exit("no events")
    df = df[df["date"].isin(set(days))].copy()
    df["date"] = df["date"].astype(str)
    df = label_cells(df)
    dates = sorted(df["date"].unique())
    df["block"] = pd.cut(pd.Series(df["date"]).rank(method="first"),
                         bins=N_BLOCKS, labels=False).astype(int).values
    n_days = len(days)
    pooled = {
        "n": int(len(df)),
        "events_per_day": round(len(df) / n_days, 3),
        "recovered_30m": round(float(df["recovered_30m"].mean()), 4),
        "max_recovery_med": round(float(df["max_recovery"].median()), 4),
        "adverse_p05": round(float(df["adverse_from_low"].quantile(0.05)), 4),
        "depth_med": round(float(df["depth"].median()), 4),
    }
    dims = ["rank_c", "pf_c", "seq_c", "gap_c", "tod_c", "rank_c_x_pf", "pf_c_x_seq"]
    df["rank_c_x_pf"] = df["rank_c"] + "|" + df["pf_c"]
    df["pf_c_x_seq"] = df["pf_c"] + "|" + df["seq_c"]
    rows = summarize(df, dims, n_days, pooled)
    for r in rows:
        apply_gate(r, pooled)
    out = {
        "study": "PRE-REG-CENSUS-01",
        "scope": {"measurement": True, "seen_data": True, "not_an_alpha_claim": True,
                  "no_trading_rule": True, "h025_untouched": True,
                  "halt_detection": "bar-gap proxy (>=5 min since previous new bar); no LULD flags",
                  "rank_note": "rank in {1,2,3} from lb top-3 rows; 'off' = candidate not top-3 that minute"},
        "frozen": {"L": L, "horizon_min": HORIZON, "power_floor": POWER_FLOOR,
                   "freq_floor_events_per_day": FREQ_FLOOR, "rec_tol_pp": REC_TOL_PP,
                   "maxrec_tol_pp": MAXREC_TOL_PP, "blocks": N_BLOCKS},
        "universe": {"days": n_days, "dates": [dates[0], dates[-1]], "events": len(df)},
        "pooled": pooled,
        "block_edges": {f"block{b}": [g.iloc[0], g.iloc[-1]]
                        for b, g in df.groupby("block")["date"]},
        "cells": rows,
        "worth_rule_prereg_cells": [f"{r['dim']}:{r['cell']}" for r in rows if r["worth_rule_prereg"]],
        "winners_by_frequency": sorted(
            [{"cell": f"{r['dim']}:{r['cell']}", "events_per_day": r["events_per_day"],
              "n": r["n"], "recovered_30m": r["recovered_30m"],
              "max_recovery_med": r["max_recovery_med"]} for r in rows],
            key=lambda x: -x["events_per_day"])[:10],
    }
    ART.mkdir(parents=True, exist_ok=True)
    (ART / "event_census.json").write_text(json.dumps(out, indent=1))
    df.drop(columns=[c for c in ["block"] if c in df]).to_parquet(
        ART / "event_census.parquet", index=False)
    print(json.dumps({"pooled": pooled, "top_freq": out["winners_by_frequency"][:5],
                      "worth_rule_prereg": out["worth_rule_prereg_cells"]}, indent=1))
    print("artifacts written: event_census.json / event_census.parquet")


def self_test():
    t = np.arange(571, 601)
    c = np.concatenate([np.linspace(1.0, 10.0, 10), np.full(20, 10.0)])
    h = np.full(30, 10.0)
    l = np.full(30, 10.0)
    l[14] = 8.9   # t=585
    l[19] = 8.9   # t=590
    g = pd.DataFrame({"t": t, "c": c, "h": h, "l": l, "o": c,
                      "v": 1.0, "n_bars": np.arange(1, 31, dtype=float),
                      "gain_c": c / 1.0 - 1})
    rows = events_for_symbol("TEST", "AAA", g, None)
    assert len(rows) == 2, rows
    assert [r["prior_flush"] for r in rows] == [0, 1], rows
    assert [r["seq"] for r in rows] == [1, 2], rows
    assert all(r["recovered_30m"] for r in rows), rows
    assert rows[0]["t"] == 585 and rows[1]["t"] == 590, rows
    assert rows[0]["t_recover_min"] == 1.0, rows
    assert abs(rows[0]["depth"] - (8.9 / 10.0 - 1)) < 1e-9, rows
    print("SELF-TEST PASS: 2 events, prior 0/1, seq 1/2, both recovered at +1m")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--extract-limit", type=int, default=None)
    a = ap.parse_args()
    if a.self_test:
        self_test()
    elif a.extract_limit is not None or not a.run:
        n = extract(usable_days(), limit=a.extract_limit)
        print(f"extracted {n} days")
    else:
        run()
