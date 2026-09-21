#!/usr/bin/env python3
"""BASKET-01 T9b on the SIP substrate — matched-random control (pre-read requirement).

Same construction as the legacy control, re-pointed at SIP provider bars: for each
deterministically sampled dev day (spacing ~4/month, identical to the legacy pass)
and each B(T) on the frozen timing surface, draw up to 3 PIT-eligible names matched
on (price band x liquidity tercile) to the actual top-3 slots, exclude the day's
top-10 gainers from the draw pool, apply the identical causal fill + ruler ladders
(basket_anatomy), and report the same k-of-3 joint histograms (touch/exec lenses).

Delegated per the work order: mechanically-checkable acquisition + recompute.
Diagnostic only - no strategy, no release rule, no parameter selection.

Outputs (resumable):
  data/sip/t9b_raw/<day>.json        per-day draws (scratch, gitignored data/)
  <root>/agg/T9b_random.json         committed evidence

Usage:
  .venv/bin/python factory/scripts/basket_random_control_sip.py --self-test
  BASKET_ART_ROOT=factory/artifacts/basket/sip .venv/bin/python factory/scripts/basket_random_control_sip.py --days 2021-02-01
  BASKET_ART_ROOT=factory/artifacts/basket/sip .venv/bin/python factory/scripts/basket_random_control_sip.py --all --workers 4
  .venv/bin/python factory/scripts/basket_random_control_sip.py --merge-only
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_anatomy as ba  # noqa: E402
import sip_universe as su  # noqa: E402

ART = Path(os.environ.get("BASKET_ART_ROOT", str(ROOT / "factory" / "artifacts" / "basket")))
ANAT = ART / "anatomy"
STAGE = ROOT / "data" / "sip" / "t9b_raw"
DRAWS = 3
BAND_EDGES = [2.0, 5.0, 10.0]


def band_idx(px: float) -> int:
    return int(np.digitize([px], BAND_EDGES)[0])


def tercile_idx(dv: float, q1: float, q2: float) -> int:
    return int(np.digitize([dv], [q1, q2])[0])


def fetch_full_bars(day: str) -> pl.DataFrame:
    """SIP 1-min RTH bars for the whole PIT universe of a day (columns symbol, et, o, h, l, c, v)."""
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed, Adjustment

    syms = sorted(su.pit_elig(day))
    start, end = su.window_utc(day, False)
    client = StockHistoricalDataClient(os.environ["ALPACA_API_KEY"],
                                       os.environ["ALPACA_SECRET_KEY"])
    rows = []
    for batch in su.chunks(syms, su.BATCH):
        todo, attempt = list(batch), 0
        while todo:
            try:
                req = StockBarsRequest(symbol_or_symbols=todo, timeframe=TimeFrame.Minute,
                                       start=start, end=end, feed=DataFeed.SIP,
                                       adjustment=Adjustment.RAW)
                res = client.get_stock_bars(req)
                for sym in todo:
                    for b in res.data.get(sym, []):
                        e = su.et_min(b.timestamp)
                        if su.RTH_LO <= e < su.RTH_HI:
                            rows.append((sym, e, float(b.open), float(b.high),
                                         float(b.low), float(b.close),
                                         float(b.volume) if b.volume else 0.0))
                break
            except Exception as exc:  # noqa: BLE001 - API/network
                import re
                m = re.search(r"invalid symbol:\s*([^\"\\]+)", str(exc))
                if m:
                    bad = m.group(1).strip()
                    todo = [s for s in todo if s.strip() != bad]
                    if len(todo) == len(batch):
                        break
                    continue
                if attempt >= 2:
                    print(f"  [warn] batch drop {todo[0] if todo else '?'}: {str(exc)[:120]}")
                    break
                attempt += 1
                time.sleep(5 * attempt)
    return pl.DataFrame(rows, schema=["symbol", "et", "o", "h", "l", "c", "v"],
                        orient="row")


def process_day(day: str, bars: pl.DataFrame):
    day_df = bars.sort(["symbol", "et"])
    bounds = day_df.group_by("symbol", maintain_order=True).agg(pl.len().alias("n"))
    _t = bounds["symbol"].to_list()
    _n = bounds["n"].to_list()
    _s = np.cumsum([0] + _n[:-1])
    _e = np.cumsum(_n)
    SL = {t: (int(a), int(b)) for t, a, b in zip(_t, _s, _e)}
    A_et = day_df["et"].to_numpy(); A_o = day_df["o"].to_numpy()
    A_h = day_df["h"].to_numpy(); A_l = day_df["l"].to_numpy()
    A_c = day_df["c"].to_numpy()
    o570 = day_df.filter(pl.col("et") == 570).select(
        "symbol", pl.col("o").alias("open0930"))
    snaps = []
    for T in ba.T_LIST:
        dec = day_df.filter(pl.col("et") <= T - 1).group_by("symbol").agg(
            pl.col("c").sort_by("et").last().alias("px"),
            (pl.col("c") * pl.col("v")).sum().alias("dv"))
        b = dec.join(o570, on="symbol", how="inner").filter(
            (pl.col("px") >= ba.MIN_PRICE) & (pl.col("open0930") > 0))
        b = b.with_columns((pl.col("px") / pl.col("open0930") - 1).alias("sel"))
        treated3 = b.sort("sel", descending=True).head(3)
        pool = b.filter(~pl.col("symbol").is_in(
            b.sort("sel", descending=True).head(ba.K_TOP)["symbol"].to_list()))
        if pool.height == 0 or treated3.height == 0:
            snaps.append({"T": T, "n_drawn": 0, "n_failed": DRAWS, "names": []})
            continue
        px = pool["px"].to_numpy()
        dv = np.maximum(pool["dv"].to_numpy().astype(float), 1e-9)
        ldv = np.log10(dv)
        q1, q2 = float(np.quantile(ldv, 1 / 3)), float(np.quantile(ldv, 2 / 3))
        pool_rows = list(zip(pool["symbol"].to_list(), px, ldv,
                             pool["open0930"].to_numpy()))
        rng = random.Random(int(day.replace("-", "")) * 1000 + T)
        drawn, failed = [], 0
        for r in treated3.iter_rows(named=True):
            bi = band_idx(float(r["px"]))
            ti = tercile_idx(float(np.log10(max(r["dv"], 1e-9))), q1, q2)
            cands = [t for (t, p, ld, _o) in pool_rows
                     if band_idx(float(p)) == bi and tercile_idx(float(ld), q1, q2) == ti]
            if not cands:
                failed += 1
                continue
            tkr = rng.choice(cands)
            s, e = SL[tkr]
            ets, os_, hs, ls, cs = A_et[s:e], A_o[s:e], A_h[s:e], A_l[s:e], A_c[s:e]
            fi = ba.pick_fill(ets, "B", T)
            if fi is None or os_[fi] <= 0:
                failed += 1
                continue
            fill = float(os_[fi])
            L, im, ia = ba.ladders(ets, os_, hs, ls, cs, fi, fill)
            prow = next(x for x in pool_rows if x[0] == tkr)
            sel = prow[1] / prow[3] - 1 if prow[3] > 0 else None
            drawn.append({"ticker": tkr, "band": bi, "terr": ti, "sel": ba.R(sel),
                          "fill_px": ba.R(fill), "mfe": ba.R(hs[im] / fill - 1),
                          "mae": ba.R(ls[ia] / fill - 1), "ladders": L})
        snaps.append({"T": T, "n_drawn": len(drawn), "n_failed": failed, "names": drawn})
    return {"date": day, "snapshots": snaps}


def stage_day(day: str, force: bool) -> str:
    sp = STAGE / f"{day}.json"
    if sp.exists() and not force:
        return "skip"
    if not (ANAT / f"{day}.jsonl").exists():
        return "no_anatomy"
    t0 = time.time()
    bars = fetch_full_bars(day)
    if bars.height == 0:
        return "no_bars"
    rec = process_day(day, bars)
    STAGE.mkdir(parents=True, exist_ok=True)
    tmp = sp.with_suffix(".json.tmp")
    with open(tmp, "w") as fh:
        json.dump(rec, fh, separators=(",", ":"), default=str)
    os.replace(tmp, sp)
    return f"ok({sum(s['n_drawn'] for s in rec['snapshots'])} drawn, {time.time()-t0:.0f}s)"


def sampled_days() -> list:
    files = sorted(glob.glob(str(ANAT / "*.jsonl")))
    by_month = defaultdict(list)
    for f in files:
        by_month[Path(f).name[:7]].append(Path(f).name[:10])
    days = []
    for month in sorted(by_month):
        present = sorted(by_month[month])
        step = max(1, len(present) // 4)
        days += present[::step]
    return days


def aggregate(root: Path):
    acc: dict = defaultdict(int)
    mfe_vals: dict = defaultdict(list)
    sels: dict = defaultdict(list)
    for f in sorted(glob.glob(str(STAGE / "*.json"))):
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
    out = {"draws": DRAWS, "substrate": "SIP provider bars (full PIT universe)",
           "day_sampling": "deterministic spacing ~4/month (same as the legacy pass)",
           "note": "B-surface matched-random control; treated top-10 excluded from draws; "
                   "k-histogram denominators are the days with 3 draws (n_drawn == 3), not "
                   "the sampled-day count",
           "_producer": "basket_random_control_sip.py",
           "tables": rows}
    (root / "agg").mkdir(parents=True, exist_ok=True)
    with open(root / "agg" / "T9b_random.json", "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(f"T9b_random: {len(rows)} (month,T) rows -> {root/'agg'/'T9b_random.json'}")


def selftest():
    assert band_idx(1.5) == 0 and band_idx(2.0) == 1 and band_idx(15.0) == 3
    assert tercile_idx(0.1, 0.2, 0.5) == 0 and tercile_idx(0.3, 0.2, 0.5) == 1
    assert tercile_idx(1.0, 0.2, 0.5) == 2
    bars = pl.DataFrame({
        "symbol": ["AAA"] * 3 + ["BBB"] * 2 + ["CCC"] * 3,
        "et": [570, 585, 600, 570, 600, 570, 585, 600],
        "o": [10.0, 11.0, 12.0, 20.0, 19.0, 5.0, 6.0, 7.0],
        "h": [10.5, 11.5, 12.5, 20.5, 19.5, 5.5, 6.5, 7.5],
        "l": [9.9, 10.5, 11.5, 19.5, 18.5, 4.9, 5.5, 6.5],
        "c": [10.2, 11.2, 12.2, 20.2, 19.2, 5.2, 5.9, 7.2],
        "v": [100.0] * 8})
    out = process_day("2021-02-01", bars)
    s585 = next(x for x in out["snapshots"] if x["T"] == 585)
    assert s585["n_drawn"] + s585["n_failed"] == 3, s585
    assert all(n["ladders"]["up"]["5"] is not None for n in s585["names"]), s585
    rng = random.Random(7)
    rng2 = random.Random(7)
    assert rng.choice(["a", "b"]) == rng2.choice(["a", "b"])
    print("self-test OK")


def merge_only(root: Path):
    aggregate(root)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="*", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--merge-only", action="store_true")
    ap.add_argument("--root", default=str(ART))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    if args.merge_only:
        aggregate(Path(args.root))
        return
    days = list(args.days or [])
    if args.all:
        days = sampled_days()
    if not days:
        ap.error("provide --days/--all/--merge-only")
    if args.workers > 1 and len(days) > 1 and not args.days:
        parts = [[] for _ in range(args.workers)]
        for i, d in enumerate(sorted(days)):
            parts[i % args.workers].append(d)
        procs = []
        for i, part in enumerate(parts):
            cmd = [sys.executable, "-u", str(Path(__file__)), "--days", *part]
            log = open(f"/tmp/opencode/t9b_w{i}.log", "w")
            procs.append((subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT), log, i))
        for p, log, i in procs:
            rc = p.wait()
            log.close()
            print(f"worker {i}: rc={rc}")
    else:
        counts = defaultdict(int)
        for i, d in enumerate(days, 1):
            res = stage_day(d, args.force)
            counts[res.split("(")[0]] += 1
            print(f"[{i}/{len(days)}] {d}: {res}", flush=True)
        print(f"staged: {dict(counts)}")


if __name__ == "__main__":
    main()
