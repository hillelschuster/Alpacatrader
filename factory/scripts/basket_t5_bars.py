#!/usr/bin/env python3
"""BASKET-01 T5 path-shape pass — retracement-before-high etc. from candidate bars.

Supplements basket_aggregate.py (which reads day files only) by walking the stored
per-day candidate bars (factory/artifacts/basket/bars/YYYY-MM-DD.parquet) for every
filled main/adj member. Measurement only — PRE-REG T5 runner path anatomy:
max retracement from running high BEFORE the session high, time-to-high after fill,
drawdown after the high to EOD, EOD close vs high.

This is descriptive anatomy. It is not a release rule, not a survivor rule, and the
+h rulers remain rulers. No strategy code.

Output: factory/artifacts/basket/agg/T5_paths.json  (committed evidence)

Usage:
  .venv/bin/python factory/scripts/basket_t5_bars.py --self-test
  .venv/bin/python factory/scripts/basket_t5_bars.py --months 2025-06
  .venv/bin/python factory/scripts/basket_t5_bars.py            # all present days
"""
from __future__ import annotations

import argparse
import glob
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
ANAT = ROOT / "factory" / "artifacts" / "basket" / "anatomy"
BARS = ROOT / "factory" / "artifacts" / "basket" / "bars"
OUT = ROOT / "factory" / "artifacts" / "basket" / "agg"
UP_STRATA = [20, 30, 50]
SETS = ("main", "adj")
PRIMARY_N = 3


def path_stats(et, hi, lo, close, fill_i, fill_px):
    """Post-fill path shape for one ticker day (arrays over all of the day's bars)."""
    post_et, post_h, post_l, post_c = et[fill_i:], hi[fill_i:], lo[fill_i:], close[fill_i:]
    if len(post_h) == 0:
        return None
    ph = np.maximum.accumulate(post_h)
    k = int(np.argmax(post_h))
    pre_seg = post_l[: k + 1] / ph[: k + 1] - 1.0
    peak = float(post_h[k])
    aft = post_l[k:] / peak - 1.0
    return {
        "time_to_hi": float(post_et[k] - post_et[0]),
        "retr_pre_hi": float(pre_seg.min()),
        "retr_after_hi": float(aft.min()),
        "eod_vs_hi": float(post_c[-1] / peak - 1.0),
        "peak_vs_fill": float(peak / fill_px - 1.0),
        "bars_post": int(len(post_h)),
    }


def member_rows(rec):
    """Filled main/adj members with fill + bar lookup keys."""
    out = []
    for s in rec.get("snapshots", []):
        names = s["names"]
        for setname, mset in (("main", names[:PRIMARY_N]), ("adj", names[PRIMARY_N:2 * PRIMARY_N])):
            for n in mset:
                f = n.get("fill")
                if not f or f.get("blocked"):
                    continue
                out.append({
                    "pop": s["pop"], "T": s["T"], "set": setname,
                    "ticker": n["ticker"], "fill_et": int(f["et"]),
                    "fill_px": float(f["px"]), "mfe": n.get("mfe"),
                })
    return out


def _q(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return None
    a = np.array(v, dtype=float)
    return {"n": int(len(a)), "mean": round(float(a.mean()), 5),
            "p10": round(float(np.percentile(a, 10)), 5),
            "p50": round(float(np.percentile(a, 50)), 5),
            "p90": round(float(np.percentile(a, 90)), 5)}


def run(files, out_dir: Path):
    import polars as pl

    acc: dict = defaultdict(list)  # 4-tuple (month,set,stratum,stat) -> vals
    n_days = n_members = n_skipped = 0
    for f in files:
        day = Path(f).name[:10]
        bp = BARS / f"{day}.parquet"
        if not bp.exists():
            n_skipped += 1
            continue
        rec = json.load(open(f))
        rows = member_rows(rec)
        if not rows:
            continue
        bdf = pl.read_parquet(bp)
        n_days += 1
        month = day[:7]
        for r in rows:
            sub = bdf.filter(pl.col("ticker") == r["ticker"]).sort("et")
            if sub.height == 0:
                n_skipped += 1
                continue
            et = sub["et"].to_numpy()
            idx = int(np.searchsorted(et, r["fill_et"], side="left"))
            if idx >= len(et) or int(et[idx]) != r["fill_et"]:
                n_skipped += 1
                continue
            st = path_stats(et, sub["high"].to_numpy(), sub["low"].to_numpy(),
                            sub["close"].to_numpy(), idx, r["fill_px"])
            if st is None:
                n_skipped += 1
                continue
            n_members += 1
            base = (month, r["set"])
            for stat, val in st.items():
                if stat == "bars_post":
                    continue
                acc[base + ("all", stat)].append(val)
                mfe = r["mfe"]
                for cut in UP_STRATA:
                    if mfe is not None and mfe >= cut / 100.0:
                        acc[base + (f"mfe>={cut}", stat)].append(val)
    tables = []
    for (month, setname, stratum, stat), vals in sorted(acc.items()):
        q = _q(vals)
        if q:
            tables.append({"month": month, "set": setname, "stratum": stratum,
                           "stat": stat, **q})
    out = {"n_days": n_days, "n_members": n_members, "n_skipped": n_skipped,
           "strata": [f"all"] + [f"mfe>={c}" for c in UP_STRATA], "tables": tables}
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "T5_paths.json", "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(f"T5_paths: {n_days} days, {n_members} members, {n_skipped} skipped -> {out_dir/'T5_paths.json'}")
    return out


def selftest():
    et = np.array([570, 571, 572, 573, 574, 575, 576])
    hi = np.array([1.0, 1.2, 1.15, 1.3, 1.8, 2.0, 1.6])
    lo = np.array([1.0, 1.1, 1.0, 1.05, 1.4, 1.5, 1.2])
    cl = np.array([1.0, 1.15, 1.05, 1.25, 1.6, 1.9, 1.2])
    st = path_stats(et, hi, lo, cl, 0, 1.0)
    assert st is not None
    assert st["time_to_hi"] == 5.0, st
    assert abs(st["retr_pre_hi"] + 0.25) < 1e-9, st       # worst dd from running high up to peak
    assert abs(st["retr_after_hi"] + 0.4) < 1e-9, st      # peak 2.0 -> low 1.2
    assert abs(st["eod_vs_hi"] + 0.4) < 1e-9, st          # close 1.2 vs peak 2.0
    assert abs(st["peak_vs_fill"] - 1.0) < 1e-9, st
    st2 = path_stats(et, hi, lo, cl, 6, 1.6)
    assert st2 is not None
    assert st2["time_to_hi"] == 0.0 and st2["bars_post"] == 1, st2
    rec = {"snapshots": [{"pop": "B", "T": 585, "names": [
        {"ticker": "AAA", "fill": {"et": 571, "px": 1.0, "blocked": False}, "mfe": 1.0},
        {"ticker": "BBB", "fill": {"et": 580, "px": 1.0, "blocked": True}, "mfe": 0.1},
    ]}]}
    rows = member_rows(rec)
    assert len(rows) == 1 and rows[0]["ticker"] == "AAA", rows
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="*", default=None)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    files = sorted(glob.glob(str(ANAT / "*.jsonl")))
    if args.months:
        months = set(args.months)
        files = [f for f in files if Path(f).name[:7] in months]
    if not files:
        raise SystemExit("no day files")
    run(files, Path(args.out))


if __name__ == "__main__":
    main()
