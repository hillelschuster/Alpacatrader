#!/usr/bin/env python3
"""PRE-REG-ROBUST-RANGE5-01: stability stress-test of the range5 conditioner.

Measurement only: no adoption, no bot change, H025 untouched. Reads the frozen
fill artifacts, computes trailing-window ranges {3,5,10,20}, applies the five
frozen tests (R1 per-year, R2 window sensitivity, R3 month-blocked LOMO,
R4 half-year sign, R5 threshold profile). Verdict STABLE / FRAGILE / MIXED.
Usage: python factory/scripts/range5_robust.py [--self-test]
Artifacts: factory/artifacts/lb18_range5_robust.json + .parquet
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import toxicity_state as ts  # noqa: E402

OUT = ROOT / "factory" / "artifacts"
THR = 0.13214
WINS = (3, 5, 10, 20)
ALT_WINS = (3, 10, 20)
QUANTILES = (0.33, 0.50, 0.67, 0.75)
YEARS = (2021, 2022, 2023, 2024, 2025)
SEED = 20260913
BOOT = 2000


def trailing_range(g, t0, win):
    if g is None or t0 not in g.index:
        return None
    idx = g.index
    w = g.loc[(idx >= t0 - (win - 1)) & (idx <= t0)]
    if len(w) < min(3, win):
        return None
    c0 = float(g.loc[t0, "c"])
    if c0 <= 0:
        return None
    return (float(w["h"].max()) - float(w["l"].min())) / c0


def attach_ranges(d):
    rows = []
    for day, sub in d.groupby("date", sort=False):
        frames = ts.day_frames(day)
        for r in sub.itertuples():
            g = frames.get(r.ticker)
            f = {"date": r.date, "month": r.month, "ticker": r.ticker, "t0": r.t0,
                 "tf": r.tf, "ret": r.ret, "fc": r.fc, "rank": r.rank,
                 "prior_flush": r.prior_flush, "era": r.era}
            for win in WINS:
                v = trailing_range(g, r.t0, win)
                f[f"r_{win}"] = np.nan if v is None else v
            if not np.isfinite(f["r_5"]):
                continue
            rows.append(f)
    out = pd.DataFrame(rows)
    out["year"] = out["date"].str[:4].astype(int)
    out["yhalf"] = out["date"].str[:4] + np.where(out["date"].str[5:7].astype(int) <= 6, "H1", "H2")
    return out


def gap(sub, col, thr):
    v = sub[col].to_numpy(dtype=float)
    r = sub["ret"].to_numpy(dtype=float)
    hi, lo = r[v >= thr], r[v < thr]
    if len(hi) == 0 or len(lo) == 0:
        return {"n_hi": int(len(hi)), "n_lo": int(len(lo)),
                "mean_hi": None, "mean_lo": None, "gap": None}
    return {"n_hi": int(len(hi)), "n_lo": int(len(lo)),
            "mean_hi": float(hi.mean()), "mean_lo": float(lo.mean()),
            "gap": float(hi.mean() - lo.mean())}


def pf2(d):
    return d[d["prior_flush"] >= 2]


def _boot(sub, hi):
    rng = np.random.default_rng(SEED)
    pos = sub.groupby("date").indices
    keys = list(pos)
    if not keys:
        return None
    r = sub["ret"].to_numpy(dtype=float)
    gaps = []
    for _ in range(BOOT):
        pick = rng.integers(0, len(keys), len(keys))
        idx = np.concatenate([pos[keys[i]] for i in pick])
        h, rr = hi[idx], r[idx]
        if h.sum() == 0 or (~h).sum() == 0:
            continue
        gaps.append(float(rr[h].mean() - rr[~h].mean()))
    if not gaps:
        return None
    return {"lo": float(np.percentile(gaps, 2.5)),
            "hi": float(np.percentile(gaps, 97.5)),
            "mean": float(np.mean(gaps)), "n_boot": len(gaps)}


def boot_gap(sub, col="r_5", thr=THR):
    return _boot(sub, sub[col].to_numpy(dtype=float) >= thr)


def r1_per_year(d):
    p = pf2(d)
    res = {}
    counted = 0
    positive = 0
    for y in YEARS:
        g = gap(p[p["year"] == y], "r_5", THR)
        res[str(y)] = g
        if g["n_hi"] >= 30 and g["n_lo"] >= 30:
            counted += 1
            positive += int(g["gap"] is not None and g["gap"] > 0)
    return {"years": res, "counted": counted, "positive": positive,
            "pass": bool(counted >= 4 and positive >= 4)}


def r2_window(d):
    p = pf2(d)
    res = {}
    positive = 0
    for win in ALT_WINS:
        col = f"r_{win}"
        sub = p[p[col].notna()]
        thr = float(np.nanmedian(d[col].to_numpy(dtype=float)))
        g = gap(sub, col, thr)
        g["thr"] = thr
        g["fixed_thr_gap"] = gap(sub, col, THR)["gap"]
        res[str(win)] = g
        positive += int(g["gap"] is not None and g["gap"] > 0)
    return {"windows": res, "positive": positive, "pass": bool(positive >= 2)}


def r3_lomo(d):
    months = sorted(d["month"].unique())
    res = {}
    counted = 0
    positive = 0
    assigned = []
    for m in months:
        other = d[d["month"] != m]["r_5"].to_numpy(dtype=float)
        thr = float(np.nanmedian(other))
        sub = pf2(d[d["month"] == m])
        g = gap(sub, "r_5", thr)
        counts = g["n_hi"] >= 5 and g["n_lo"] >= 5
        res[m] = {**g, "thr": thr, "counted": bool(counts)}
        if counts:
            counted += 1
            positive += int(g["gap"] is not None and g["gap"] > 0)
        s = sub.copy()
        s["thr_lomo"] = thr
        assigned.append(s)
    pooled = pd.concat(assigned, ignore_index=True) if assigned else pd.DataFrame()
    boot = None
    pooled_gap = None
    if len(pooled):
        mixed = pooled["r_5"] >= pooled["thr_lomo"]
        hi, lo = pooled[mixed], pooled[~mixed]
        if len(hi) and len(lo):
            pooled_gap = float(hi["ret"].mean() - lo["ret"].mean())
        boot = _boot(pooled, (pooled["r_5"] >= pooled["thr_lomo"]).to_numpy())
    return {"months": res, "counted": counted, "positive": positive,
            "share_positive": (positive / counted) if counted else None,
            "pooled_gap": pooled_gap,
            "bootstrap": boot,
            "pass": bool(counted >= 8 and positive / counted >= 0.55) if counted else False}


def r4_halfyear(d):
    p = pf2(d)
    res = {}
    counted = 0
    positive = 0
    for h in sorted(p["yhalf"].unique()):
        g = gap(p[p["yhalf"] == h], "r_5", THR)
        res[h] = g
        if g["n_hi"] + g["n_lo"] >= 20:
            counted += 1
            positive += int(g["gap"] is not None and g["gap"] > 0)
    return {"halves": res, "counted": counted, "positive": positive,
            "share_positive": (positive / counted) if counted else None,
            "pass": bool(counted >= 6 and (positive / counted) >= 0.70) if counted else False}


def r5_profile(d):
    p = pf2(d)
    vals = d["r_5"].to_numpy(dtype=float)
    res = {}
    signs = []
    for q in QUANTILES:
        thr = float(np.nanquantile(vals, q))
        g = gap(p, "r_5", thr)
        g["thr"] = thr
        res[f"{q:.2f}"] = g
        if g["gap"] is not None:
            signs.append(int(np.sign(g["gap"])))
    pos = sum(1 for s in signs if s > 0)
    flips = any(signs[i] != signs[i + 1] for i in range(len(signs) - 1))
    return {"quantiles": res, "positive": pos, "adjacent_flip": bool(flips),
            "pass": bool(pos >= 3 and not flips)}


def verdict(r1, r2, r3, r4, r5):
    failed = [n for n, r in (("R1", r1), ("R2", r2), ("R3", r3), ("R4", r4), ("R5", r5))
              if not r["pass"]]
    if not failed:
        return "STABLE", failed
    if any(n in failed for n in ("R1", "R3", "R4")):
        return "FRAGILE", failed
    return "MIXED", failed


def self_test():
    idx = pd.Index(range(570, 600), name="t")
    n = len(idx)
    g = pd.DataFrame({"h": np.full(n, 10.0), "l": np.full(n, 9.0), "c": np.full(n, 9.5)}, index=idx)
    g.loc[595:599, "h"] = 12.0
    g.loc[595:599, "l"] = 8.0
    v = trailing_range(g, 599, 5)
    assert v is not None
    assert abs(v - (12.0 - 8.0) / 9.5) < 1e-9, v
    assert trailing_range(g, 570, 5) is None
    base = dict(month="2021-01", ticker="AAA", t0=590, tf=595, fc=0.0, rank=1,
                prior_flush=3, era="2021-2023", yhalf="2021H1", year=2021,
                r_3=np.nan, r_10=np.nan, r_20=np.nan)
    rows = [
        dict(base, date="2021-01-04", ret=0.10, r_5=0.30),
        dict(base, date="2021-01-04", ret=-0.05, r_5=0.10),
        dict(base, date="2021-01-05", ret=0.08, r_5=0.25),
        dict(base, date="2021-01-05", ret=-0.06, r_5=0.11),
        dict(base, date="2021-01-06", ret=0.09, r_5=0.40),
        dict(base, date="2021-01-06", ret=-0.02, r_5=0.12),
    ]
    d = pd.DataFrame(rows)
    d["prior_flush"] = 3
    g5 = gap(d, "r_5", THR)
    assert g5["n_hi"] == 3 and g5["n_lo"] == 3, g5
    assert g5["gap"] is not None and g5["gap"] > 0, g5
    b = boot_gap(d)
    assert b is not None and b["lo"] <= b["mean"] <= b["hi"], b
    r_ok = {"pass": True}
    v1, _ = verdict(r_ok, r_ok, r_ok, r_ok, r_ok)
    assert v1 == "STABLE", v1
    v2, fails = verdict(r_ok, r_ok, {"pass": False}, r_ok, r_ok)
    assert v2 == "FRAGILE" and fails == ["R3"], (v2, fails)
    print("SELF-TEST PASS")


def run():
    d = attach_ranges(ts.load_fills())
    if not len(d):
        raise SystemExit("no fills with ranges")
    r1, r2, r3, r4, r5 = r1_per_year(d), r2_window(d), r3_lomo(d), r4_halfyear(d), r5_profile(d)
    v, failed = verdict(r1, r2, r3, r4, r5)
    out = {
        "study": "PRE-REG-ROBUST-RANGE5-01",
        "pre_reg": "researches/PRE-REG-ROBUST-RANGE5-01.md",
        "threshold_frozen": THR,
        "n_fills": int(len(d)),
        "n_pf2": int((d["prior_flush"] >= 2).sum()),
        "r1_per_year": r1, "r2_window": r2, "r3_lomo": r3,
        "r4_halfyear": r4, "r5_profile": r5,
        "verdict": v, "failed": failed,
        "flags": {"measurement": True, "seen_data": True, "not_an_alpha_claim": True},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "lb18_range5_robust.json").write_text(json.dumps(out, indent=1, default=str))
    keep = ["date", "month", "ticker", "t0", "tf", "ret", "fc", "rank",
            "prior_flush", "era", "year", "yhalf"] + [f"r_{w}" for w in WINS]
    d[keep].to_parquet(OUT / "lb18_range5_robust.parquet", index=False)
    print(f"verdict={v} failed={failed} n={len(d)} pf2={out['n_pf2']}")
    print("R1:", {k: {kk: (round(vv, 4) if isinstance(vv, float) else vv)
                      for kk, vv in g.items()} for k, g in r1["years"].items()})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        self_test()
    else:
        run()
