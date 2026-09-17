#!/usr/bin/env python3
"""BASKET-01 split + bad-print audit of the basket candidate universe.

Two artifact classes were found in the Phase-1 candidates (2026-09-17):
  1. corporate actions: e.g. CYN/UPXI/ROLR/NAOV member-days whose open is >=2.5x
     the prior close with a small session body (repo audit signature; the shipped
     data/split_flags.parquet covers 2024+ only);
  2. bad prints: BRP 2022-03-10 had single-minute bars trading at ~42x the day's
     price (et=587 close 1092.71; et=629 open 785.47 -> close 25.14). Those bars
     corrupted BOTH the causal ranking (decision px 1092.71 vs prev close 25.61)
     and the recorded MFE (+2991% on a day that closed at 26.19).

Definitions used here (all audit-only; none of them is a causal admission rule):
  * susp_expost  = open0930/prev_close >= 2.5 AND |close/open0930 - 1| < 0.30
  * susp_causal  = open0930/prev_close outside [0.5, 2.0]   (knowable at the open)
  * glitch       = any single-minute bar in the day whose max(o,h,l,c)/min(...) > 3
                   (impossible intraday range under LULD for these names)
  * member clean = not susp_expost and not glitch

Recomputes the continuous tail with and without the flagged member-days.

Output: factory/artifacts/basket/agg/T12_splitaudit.json
Usage: --self-test | [--months ...] | (default: all anatomy days)
"""
from __future__ import annotations

import argparse
import glob
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
ANAT = ROOT / "factory" / "artifacts" / "basket" / "anatomy"
BARS = ROOT / "factory" / "artifacts" / "basket" / "bars"
OUT = ROOT / "factory" / "artifacts" / "basket" / "agg"

PRIMARY_N = 3
TAIL = 1.00
RANGE_MAX = 3.0
EDGES = [0, 0.05, 0.10, 0.20, 0.30, 0.50, 1.00, 2.00, 3.00, 5.00]
BANDS = ["<0", "0-5", "5-10", "10-20", "20-30", "30-50", "50-100",
         "100-200", "200-300", "300-500", "500+"]


def band(x):
    return int(np.digitize([x], EDGES)[0])


def classify_body(open_px, prev_close, close_px):
    if not open_px or not prev_close or not close_px or prev_close <= 0 or open_px <= 0:
        return None, None, False, False
    ro = open_px / prev_close
    intr = close_px / open_px - 1.0
    susp_expost = bool(ro >= 2.5 and abs(intr) < 0.30)
    susp_causal = bool(ro >= 2.0 or ro <= 0.5)
    return ro, intr, susp_expost, susp_causal


def bar_range_max(o, h, l, c):
    v = np.stack([o, h, l, c]).astype(float)
    lo = v.min(axis=0)
    hi = v.max(axis=0)
    ok = lo > 0
    if not ok.any():
        return 0.0
    return float((hi[ok] / lo[ok]).max())


def day_glitch(bdf):
    """ticker -> (glitch_any, bad_ets, max_range) for one day's bars."""
    out = {}
    if bdf is None or bdf.height == 0:
        return out
    bdf = bdf.sort("et")
    for key, sub in bdf.group_by("ticker", maintain_order=True):
        tkr = key[0] if isinstance(key, tuple) else key
        v = np.stack([sub["open"].to_numpy(), sub["high"].to_numpy(),
                      sub["low"].to_numpy(), sub["close"].to_numpy()]).astype(float)
        lo = v.min(axis=0)
        hi = v.max(axis=0)
        rr = np.where(lo > 0, hi / np.maximum(lo, 1e-12), 1.0)
        ets = sub["et"].to_numpy()
        bad = rr > RANGE_MAX
        out[tkr] = (bool(bad.any()), set(int(e) for e in ets[bad]), float(rr.max()))
    return out


def _q(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return None
    a = np.array(v, dtype=float)
    return {"n": int(len(a)), "max": round(float(a.max()), 4),
            "p50": round(float(np.percentile(a, 50)), 4),
            "p90": round(float(np.percentile(a, 90)), 4),
            "p99": round(float(np.percentile(a, 99)), 4)}


def run(files):
    counts = defaultdict(int)
    by_year = defaultdict(lambda: defaultdict(int))
    raw_mfe, clean_mfe = [], []
    tail_rows = []
    for f in files:
        rec = json.load(open(f))
        day = rec["date"]
        bp = BARS / f"{day}.parquet"
        gl = day_glitch(pl.read_parquet(bp)) if bp.exists() else {}
        for s in rec["snapshots"]:
            for n in s["names"][:PRIMARY_N]:
                fill = n.get("fill")
                if not fill or fill.get("blocked") or n.get("mfe") is None:
                    continue
                tkr = n["ticker"]
                g = gl.get(tkr)
                glitch_any = bool(g[0]) if g else False
                glitch_pre = glitch_post = False
                mfe_gf = None
                if g:
                    _, bad_ets, _ = g
                    glitch_pre = any(e < fill["et"] for e in bad_ets)
                    glitch_post = any(e >= fill["et"] for e in bad_ets)
                    if glitch_post and bp.exists():
                        sub = pl.read_parquet(bp).sort("et").filter(pl.col("ticker") == tkr)
                        v = np.stack([sub["open"].to_numpy(), sub["high"].to_numpy(),
                                      sub["low"].to_numpy(), sub["close"].to_numpy()]).astype(float)
                        lo = v.min(axis=0)
                        rr = np.where(lo > 0, v.max(axis=0) / np.maximum(lo, 1e-12), 1.0)
                        ets = sub["et"].to_numpy()
                        post = (ets >= fill["et"]) & (rr <= RANGE_MAX)
                        mfe_gf = (round(float(sub["high"].to_numpy()[post].max() / fill["px"] - 1), 4)
                                  if post.any() else None)
                ro, intr, se, sc = classify_body(n.get("open0930"), n.get("prev_close"), n.get("close"))
                mfe = float(n["mfe"])
                counts["member_days"] += 1
                for k, v in (("glitch", glitch_any), ("glitch_pre", glitch_pre),
                             ("glitch_post", glitch_post), ("susp_expost", se),
                             ("susp_causal", sc)):
                    counts[k] += int(v)
                raw_mfe.append(mfe)
                if not (se or glitch_any):
                    clean_mfe.append(mfe)
                year = day[:4]
                by_year[year]["members"] += 1
                by_year[year]["susp_expost"] += int(se)
                by_year[year]["glitch"] += int(glitch_any)
                row = {"date": day, "ticker": tkr, "pop": s["pop"], "T": s["T"],
                       "mfe": round(mfe, 4), "mfe_glitchfree": mfe_gf,
                       "ratio_open_prev": None if ro is None else round(ro, 4),
                       "intraday_body": None if intr is None else round(intr, 4),
                       "glitch": glitch_any, "glitch_pre": glitch_pre,
                       "glitch_post": glitch_post, "susp_expost": se, "susp_causal": sc}
                if mfe >= TAIL or glitch_any or se:
                    tail_rows.append(row)
    tail_rows.sort(key=lambda r: -r["mfe"])
    rawb = [0] * len(BANDS)
    for x in raw_mfe:
        rawb[band(x)] += 1
    clb = [0] * len(BANDS)
    for x in clean_mfe:
        clb[band(x)] += 1
    out = {
        "counts": dict(counts),
        "bands": BANDS, "raw_bands": rawb, "clean_bands": clb,
        "raw_mfe_q": _q(raw_mfe), "clean_mfe_q": _q(clean_mfe),
        "by_year": {k: dict(v) for k, v in sorted(by_year.items())},
        "tail_ge_100_raw": sum(1 for x in raw_mfe if x >= TAIL),
        "tail_rows": tail_rows,
        "note": ("glitch = single-minute bar range > 3x (impossible under LULD); "
                 "susp_expost/susp_causal as documented; all audit-only. "
                 "BRP 2022-03-10 is the motivating bad-print case."),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "T12_splitaudit.json", "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    c = out["counts"]
    print(f"splitaudit: {c['member_days']} member-days | glitch {c['glitch']} "
          f"(pre {c['glitch_pre']}, post {c['glitch_post']}) | susp_expost {c['susp_expost']} "
          f"| tail>=+100% raw {out['tail_ge_100_raw']} | clean_q {out['clean_mfe_q']}")
    print(" glitch rows:", [(r["date"], r["ticker"], r["mfe"], r["mfe_glitchfree"])
                            for r in out["tail_rows"] if r["glitch"]][:10])
    return out


def selftest():
    assert band(1.2) == 7
    assert classify_body(25.4, 0.595, 26.2)[2:] == (True, True)
    assert classify_body(18.0, 11.0, 34.0)[2:] == (False, False)
    assert classify_body(None, 1.0, 1.0)[0] is None
    assert bar_range_max(np.array([25.4, 25.5]), np.array([25.6, 1092.7]),
                         np.array([25.3, 25.5]), np.array([25.5, 1092.7])) > 40
    assert bar_range_max(np.array([25.4]), np.array([26.0]),
                         np.array([25.3]), np.array([25.9])) < 3
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="*", default=None)
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
    run(files)


if __name__ == "__main__":
    main()
