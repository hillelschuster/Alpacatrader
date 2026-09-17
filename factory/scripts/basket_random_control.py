#!/usr/bin/env python3
"""BASKET-01 T9b matched-random control — PIT price/liquidity-matched random baskets.

Sanity check (PRE-REG T9): for each sampled dev day and each B(T) snapshot on the
frozen timing surface, draw up to 3 random PIT-eligible names matched on
(price band x liquidity tercile) to the actual candidate slots, apply the identical
causal fill + ruler ladders, and report the same k-of-3 joint histograms (touch and
executable lenses). Draws exclude the day's top-10 gainers so the control never
overlaps the treated set. Nothing is matched on outcomes.

Scope note: the control is computed for the B timing surface (the discovery surface
where selection is least resolved). It is quantitatively the same base-rate object
needed to sanity-check any treated set at those times; A_open's control can be added
later without changing this construction. T9a (rank-adjacent) remains the primary
mechanism control.

Day sampling: deterministic spacing (~4 sessions/month) to bound compute; sampling
error is documented, not hidden. Diagnostic only — no strategy, no release rule.

Outputs (resumable):
  factory/artifacts/basket/random/YYYY-MM-DD.jsonl   (day draws, gitignored)
  factory/artifacts/basket/agg/T9b_random.json       (committed evidence)

Usage:
  .venv/bin/python factory/scripts/basket_random_control.py --self-test
  .venv/bin/python factory/scripts/basket_random_control.py --months 2025-06
  .venv/bin/python factory/scripts/basket_random_control.py            # all dev days
"""
from __future__ import annotations

import argparse
import glob
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_anatomy as ba  # noqa: E402

ANAT = ROOT / "factory" / "artifacts" / "basket" / "anatomy"
RAND = ROOT / "factory" / "artifacts" / "basket" / "random"
OUT = ROOT / "factory" / "artifacts" / "basket" / "agg"
DRAWS = 3
BAND_EDGES = [2.0, 5.0, 10.0]


def band_idx(px: float) -> int:
    return int(np.digitize([px], BAND_EDGES)[0])


def tercile_idx(dv: float, q1: float, q2: float) -> int:
    return int(np.digitize([dv], [q1, q2])[0])


def draw_one(cands: list, rng: random.Random):
    return rng.choice(cands) if cands else None


def attach(ticker, SL, A_et, A_o, A_h, A_l, A_c, T):
    s, e = SL[ticker]
    ets, os_, hs, ls, cs = A_et[s:e], A_o[s:e], A_h[s:e], A_l[s:e], A_c[s:e]
    fi = ba.pick_fill(ets, "B", T)
    if fi is None or os_[fi] <= 0:
        return None
    fill = float(os_[fi])
    L, im, ia = ba.ladders(ets, os_, hs, ls, cs, fi, fill)
    return {"fill_px": ba.R(fill), "mfe": ba.R(hs[im] / fill - 1),
            "mae": ba.R(ls[ia] / fill - 1), "ladders": L}


def process_day(day_df, day_str):
    elig = ba.pit_elig(day_str)
    if not elig:
        return None
    day = day_df.sort(["ticker", "et"])
    bounds = day.group_by("ticker", maintain_order=True).agg(pl.len().alias("n"))
    _t = bounds["ticker"].to_list()
    _n = bounds["n"].to_list()
    _s = np.cumsum([0] + _n[:-1])
    _e = np.cumsum(_n)
    SL = {t: (int(s0), int(e0)) for t, s0, e0 in zip(_t, _s, _e)}
    A_et = day["et"].to_numpy(); A_o = day["open"].to_numpy()
    A_h = day["high"].to_numpy(); A_l = day["low"].to_numpy()
    A_c = day["close"].to_numpy()
    el = day.filter(pl.col("ticker").is_in(list(elig)))
    o570 = el.filter(pl.col("et") == 570).select("ticker", pl.col("open").alias("open0930"))

    snaps = []
    for T in ba.T_LIST:
        dec = el.filter(pl.col("et") <= T - 1).group_by("ticker").agg(
            pl.col("close").sort_by("et").last().alias("px"),
            (pl.col("close") * pl.col("volume")).sum().alias("dv"),
        )
        b = dec.join(o570, on="ticker", how="inner").filter(
            (pl.col("px") >= ba.MIN_PRICE) & (pl.col("open0930") > 0))
        b = b.with_columns((pl.col("px") / pl.col("open0930") - 1).alias("sel"))
        treated3 = b.sort("sel", descending=True).head(3)
        pool = b.filter(~pl.col("ticker").is_in(b.sort("sel", descending=True).head(ba.K_TOP)["ticker"].to_list()))
        if pool.height == 0 or treated3.height == 0:
            snaps.append({"T": T, "n_drawn": 0, "n_failed": DRAWS, "names": []})
            continue
        px = pool["px"].to_numpy()
        dv = pool["dv"].to_numpy().astype(float)
        dv = np.where(dv > 0, dv, 1e-9)
        ldv = np.log10(dv)
        q1, q2 = float(np.quantile(ldv, 1 / 3)), float(np.quantile(ldv, 2 / 3))
        pool_rows = list(zip(pool["ticker"].to_list(), px, ldv, pool["open0930"].to_numpy()))
        rng = random.Random(int(day_str.replace("-", "")) * 1000 + T)
        drawn, failed = [], 0
        for r in treated3.iter_rows(named=True):
            bi = band_idx(float(r["px"]))
            ti = tercile_idx(float(np.log10(max(r["dv"], 1e-9))), q1, q2)
            cands = [t for (t, p, ld, _o) in pool_rows
                     if band_idx(float(p)) == bi and tercile_idx(float(ld), q1, q2) == ti]
            tkr = draw_one(cands, rng)
            if tkr is None:
                failed += 1
                continue
            att = attach(tkr, SL, A_et, A_o, A_h, A_l, A_c, T)
            if att is None:
                failed += 1
                continue
            row = next((x for x in pool_rows if x[0] == tkr), None)
            sel = None if row is None or row[3] <= 0 else row[1] / row[3] - 1
            drawn.append({"ticker": tkr, "band": bi, "terr": ti, "sel": ba.R(sel), **att})
        snaps.append({"T": T, "n_drawn": len(drawn), "n_failed": failed, "names": drawn})
    return {"date": day_str, "snapshots": snaps}


def aggregate():
    acc: dict = defaultdict(int)
    mfe_vals: dict = defaultdict(list)
    sels: dict = defaultdict(list)
    for f in sorted(glob.glob(str(RAND / "*.jsonl"))):
        day = Path(f).name[:10]
        month = day[:7]
        rec = json.load(open(f))
        for s in rec["snapshots"]:
            T = s["T"]
            key = (month, T)
            acc[(key, "days")] += 1
            if s["n_drawn"] != DRAWS:
                acc[(key, "days_lt3")] += 1
                continue
            for H in ba.UP:
                k_touch = sum(1 for n in s["names"] if n["ladders"]["up"][str(H)] is not None)
                k_exec = sum(1 for n in s["names"]
                             if n["ladders"]["up"][str(H)] is not None
                             and n["ladders"]["up"][str(H)]["exec"] is not None)
                acc[(key, f"touch_{H}/{k_touch}")] += 1
                acc[(key, f"exec_{H}/{k_exec}")] += 1
            for n in s["names"]:
                mfe_vals[key].append(n["mfe"])
                sels[key].append(n["sel"])
    rows = []
    for (month, T) in sorted({k[0] for k in acc if k[1] == "days"}):
        key = (month, T)
        row = {"month": month, "T": T, "days": acc[(key, "days")],
               "days_lt3": acc[(key, "days_lt3")]}
        for H in ba.UP:
            row[f"touch_k_{H}"] = [acc[(key, f"touch_{H}/{k}")] for k in range(DRAWS + 1)]
            row[f"exec_k_{H}"] = [acc[(key, f"exec_{H}/{k}")] for k in range(DRAWS + 1)]
        m = [x for x in mfe_vals[key] if x is not None]
        if m:
            a = np.array(m, dtype=float)
            row["mfe_drawn"] = {"n": int(len(a)), "p50": ba.R(np.percentile(a, 50)),
                                "p90": ba.R(np.percentile(a, 90))}
        sv = [x for x in sels[key] if x is not None]
        if sv:
            row["sel_drawn_p50"] = ba.R(np.percentile(np.array(sv, dtype=float), 50))
        rows.append(row)
    out = {"draws": DRAWS, "day_sampling": "deterministic spacing ~4/month",
           "note": "B-surface matched-random control; treated top-10 excluded from draws",
           "tables": rows}
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "T9b_random.json", "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(f"T9b_random: {len(rows)} (month,T) rows -> {OUT/'T9b_random.json'}")


def run(days, force=False):
    import datetime as _dt

    RAND.mkdir(parents=True, exist_ok=True)
    by_month = defaultdict(list)
    for p in days:
        by_month[Path(p).name[:7]].append(Path(p).name[:10])
    n_written = 0
    for month in sorted(by_month):
        present = sorted(by_month[month])
        step = max(1, len(present) // 4)
        sampled = present[::step]
        lf = ba.load_month_lazy(month)
        for ds in sampled:
            out_p = RAND / f"{ds}.jsonl"
            if out_p.exists() and not force:
                continue
            d = _dt.date.fromisoformat(ds)
            wdf = lf.filter(pl.col("date") == d).collect().unique(
                subset=["timestamp", "ticker"], keep="first")
            if wdf.height == 0:
                continue
            rec = process_day(wdf, ds)
            if not rec:
                continue
            with open(out_p, "w") as fh:
                fh.write(json.dumps(rec, separators=(",", ":"), default=str) + "\n")
            n_written += 1
            print(f"{ds}: " + " ".join(f"{s['T']}:{s['n_drawn']}/{s['n_failed']}"
                                       for s in rec["snapshots"]))
        del lf
    print(f"random control: wrote {n_written} day files")
    aggregate()


def selftest():
    assert band_idx(1.5) == 0 and band_idx(2.0) == 1 and band_idx(4.9) == 1
    assert band_idx(7.0) == 2 and band_idx(15.0) == 3
    assert tercile_idx(0.1, 0.2, 0.5) == 0 and tercile_idx(0.3, 0.2, 0.5) == 1
    assert tercile_idx(1.0, 0.2, 0.5) == 2
    r1 = random.Random(42)
    r2 = random.Random(42)
    assert draw_one(["a", "b", "c"], r1) == draw_one(["a", "b", "c"], r2)
    assert draw_one([], r1) is None
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="*", default=None)
    ap.add_argument("--force", action="store_true")
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
    run(files, force=args.force)


if __name__ == "__main__":
    main()
