#!/usr/bin/env python3
"""HARVEST01 — birds-eye golden-window anatomy (descriptive marks, dev only).

For every (day, primary variant, clock, N) trace the basket's minute-by-minute mark
value from entry (each member marked at its last known close) and record:
  * the basket's peak mark and the ET minute at which it occurred,
  * the mark at fixed window clocks 10:00..13:00 (600/615/630/645/660/675/690/705/720/750/780),
  * the capture ratio mark_at_E / peak.
Also per member: the close-based session peak minute.

These are marks (not executable fills): they answer WHEN the money appears and how
much of it survives to each window clock. Executable economics live in the sim/policy
lanes. Outputs: harvest01/window/<day>.parquet, report/window.md
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import sys
import time
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402
from basket_harvest_policies import load_bars  # noqa: E402

WINDOW_EXITS = [600, 615, 630, 645, 660, 675, 690, 705, 720, 750, 780]


def process_day(day: str, data_root: Path, se: int, force: bool) -> str:
    outd = data_root / "harvest01" / "window"
    outd.mkdir(parents=True, exist_ok=True)
    mp_ = outd / f"{day}.manifest.json"
    if not force and mp_.exists():
        try:
            if json.loads(mp_.read_text()).get("status") == "ok":
                return f"{day}: skip"
        except Exception:
            pass
    fl_p = data_root / "harvest01" / "sim" / "fills" / f"{day}.parquet"
    if not fl_p.exists():
        return f"{day}: no fills"
    fills = pl.read_parquet(fl_p).filter(pl.col("variant") == "primary")
    bars = load_bars(data_root / "harvest01" / "base" / "bars" / f"{day}.parquet")
    t0 = time.time()
    rows, mrows = [], []
    for clock in sorted(fills["clock"].unique().to_list()):
        fv = fills.filter(pl.col("clock") == clock).sort("rank").to_dicts()
        for N in (1, 2, 3, 4):
            members = fv[:N]
            if not members:
                continue
            # per-member share/cash from actual fills
            filled = [m for m in members if m["status"] == "filled" and m["ticker"] in bars]
            blocked_cash = sum(1.0 / N for m in members if m["status"] != "filled")
            shares = {}
            for m in filled:
                shares[m["ticker"]] = (1.0 / N) / (m["fill_px"] * 1.005)
            starts = [int(m["fill_et"]) for m in filled] or [clock]
            grid = range(min(starts), se + 1)
            ptr = {t: 0 for t in shares}
            last = {t: None for t in shares}
            path = {}
            peak_ret, peak_et = -9.9, None
            for t in grid:
                val = blocked_cash
                for tk in shares:
                    b = bars[tk]
                    while ptr[tk] < len(b["et"]) and b["et"][ptr[tk]] <= t:
                        last[tk] = b["close"][ptr[tk]]
                        ptr[tk] += 1
                    if last[tk] is not None:
                        val += shares[tk] * last[tk]
                r = val - 1
                if r > peak_ret:
                    peak_ret, peak_et = r, t
                for E in WINDOW_EXITS:
                    if t == E:
                        path[E] = r
            row = {"day": day, "clock": int(clock), "N": N, "peak_et": peak_et,
                   "peak_ret": peak_ret}
            for E in WINDOW_EXITS:
                row[f"r{E}"] = path.get(E)
                row[f"cap{E}"] = (path.get(E) / peak_ret) if (path.get(E) is not None and peak_ret > 1e-9) else None
            rows.append(row)
            for m in filled:
                b = bars[m["ticker"]]
                fi = bisect.bisect_left(b["et"], m["fill_et"])
                if fi >= len(b["et"]):
                    continue
                seg = b["close"][fi:]
                j = max(range(len(seg)), key=lambda i: seg[i])
                mrows.append({"day": day, "clock": int(clock), "rank": m["rank"],
                              "ticker": m["ticker"], "m_peak_et": int(b["et"][fi + j]),
                              "m_peak_ret": float(seg[j] / m["fill_px"] - 1),
                              "m_close_ret": float(seg[-1] / m["fill_px"] - 1)})
    df = pl.DataFrame(rows, infer_schema_length=None)
    mdf = pl.DataFrame(mrows)
    fp, tmp = outd / f"{day}.parquet", outd / f"{day}.parquet.tmp"
    df.write_parquet(tmp)
    os.replace(tmp, fp)
    md = outd / "members"
    md.mkdir(parents=True, exist_ok=True)
    fp2, tmp2 = md / f"{day}.parquet", md / f"{day}.parquet.tmp"
    mdf.write_parquet(tmp2)
    os.replace(tmp2, fp2)
    mp_.write_text(json.dumps({"day": day, "status": "ok", "rows": int(df.height),
                               "elapsed_s": round(time.time() - t0, 1)}, indent=1))
    return f"{day}: ok rows={df.height} el={round(time.time()-t0,1)}s"


def _worker(a):
    day, data_root, se, force = a
    return process_day(day, Path(data_root), se, force)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day")
    ap.add_argument("--days", nargs="+", default=[])
    ap.add_argument("--dev-days", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    data_root = bps.resolve_data_root(args.data_root)
    cal = json.loads(bps.CALENDAR.read_text())["evidence"]
    sends = {d: int(v["session_end"]) for d, v in cal.items()}
    days = list(args.days)
    if args.day:
        days.append(args.day)
    if args.dev_days:
        days = sorted(sends.keys())
    if args.limit:
        days = days[:args.limit]
    if not days:
        ap.print_help()
        return 2
    t0 = time.time()
    if args.workers <= 1:
        for d in days:
            print(process_day(d, data_root, int(sends.get(d, 959)), args.force), flush=True)
    else:
        import multiprocessing as mp
        tasks = [(d, str(data_root), int(sends.get(d, 959)), args.force) for d in days]
        with mp.get_context("spawn").Pool(processes=args.workers) as pool:
            for line in pool.imap_unordered(_worker, tasks):
                print(line, flush=True)
    print(f"done {len(days)} days in {round(time.time() - t0, 1)}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
