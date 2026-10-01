#!/usr/bin/env python3
"""HARVEST01 — dense current-leader snapshots for replacement experiments.

For each day, on a 5-minute grid of RTH clocks, record the top-2 names by
split-normalized gain vs the previous session close using the race.minute_full as-of
convention (decision row t = clock, px = close of last completed bar <= clock-1).
These are the candidates a causal "replace the dead ticket with the strongest current
claim" experiment may buy after a release; nothing here is used as an entry by the
unmanaged baskets.

Output: <data>/harvest01/base/leaders/<day>.parquet
    day, clock, rank, ticker, px, px_et, gain_adj, rank_known, rank_unfiltered, age_min
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402
import basket_harvest_select as bhs  # noqa: E402

COLS = ["t", "ticker", "px", "px_et", "known_by_t", "age_min", "prev_close",
        "flag_prevclose_discrepancy", "flag_nonpositive_px", "rank_known", "rank_unfiltered"]
GAIN_CEIL = 10.0
PX_FLOOR = 0.05


def process_day(day: str, data_root: Path, sessions: dict, splits: pl.DataFrame,
                sends: dict, force: bool) -> str:
    outd = data_root / "harvest01" / "base" / "leaders"
    outd.mkdir(parents=True, exist_ok=True)
    mp = outd / f"{day}.manifest.json"
    if not force and mp.exists():
        try:
            if json.loads(mp.read_text()).get("status") == "ok":
                return f"{day}: skip"
        except Exception:
            pass
    pf = (data_root / "atlas" / "observation" / "v0" / "race.minute_full"
          / f"month={day[:7]}" / f"{day}.parquet")
    if not pf.exists():
        return f"{day}: no panel"
    se = int(sends.get(day, 959))
    prev_day = sessions.get(day)
    clocks = sorted(set(list(range(575, se, 5)) + [se]))
    t0 = time.time()
    panel = pl.scan_parquet(pf).select(COLS).filter(
        pl.col("t").is_in(clocks) & pl.col("known_by_t") & pl.col("px").is_not_null()).collect()
    # light universe from the same frame: panel prev_close with the compact fallback
    u = panel.group_by("ticker").agg(
        pl.col("prev_close").drop_nulls().last().alias("prev_close_panel"),
        pl.col("flag_prevclose_discrepancy").any().alias("flag_discrepancy"),
        pl.col("flag_nonpositive_px").any().alias("flag_nonpos"))
    cp = bhs.compact_prev(data_root, prev_day) if prev_day else None
    if cp is not None:
        u = u.join(cp, on="ticker", how="full", coalesce=True).with_columns(
            pl.col("prev_close_panel").fill_null(pl.col("prev_close_compact")))
    u = u.drop_nulls("prev_close_panel")
    sf = bhs.split_map_for_day(day, prev_day, splits).rename({"symbol": "ticker"}) if prev_day \
        else pl.DataFrame({"ticker": []}, schema={"ticker": pl.Utf8})
    u = u.join(sf, on="ticker", how="left").with_columns(
        pl.col("split_factor").fill_null(1.0))
    u = u.with_columns(
        pl.col("flag_discrepancy").fill_null(False),
        pl.col("flag_nonpos").fill_null(False),
        (pl.col("prev_close_panel") * pl.col("split_factor")).alias("prev_close_adj"))
    j = panel.join(u.select(["ticker", "prev_close_adj", "flag_discrepancy", "flag_nonpos"]),
                   on="ticker", how="inner")
    j = j.filter((~pl.col("flag_discrepancy")) & (~pl.col("flag_nonpos"))
                 & (pl.col("prev_close_adj") > 0))
    j = j.with_columns((pl.col("px") / pl.col("prev_close_adj") - 1).alias("gain_adj"))
    j = j.filter((pl.col("gain_adj") <= GAIN_CEIL) & (pl.col("px") >= PX_FLOOR))
    top = (j.sort(["gain_adj", "ticker"], descending=[True, False])
            .group_by("t").head(2)
            .with_columns(pl.int_range(1, pl.len() + 1).over("t").alias("rank"))
            .with_columns(pl.lit(day).alias("day"))
            .rename({"t": "clock"})
            .select(["day", "clock", "rank", "ticker", "px", "px_et", "gain_adj",
                     "rank_known", "rank_unfiltered", "age_min"]))
    fp, tmp = outd / f"{day}.parquet", outd / f"{day}.parquet.tmp"
    top.write_parquet(tmp)
    os.replace(tmp, fp)
    mp.write_text(json.dumps({"day": day, "status": "ok", "rows": int(top.height),
                              "clocks": len(clocks),
                              "elapsed_s": round(time.time() - t0, 1)}, indent=1))
    return f"{day}: ok rows={top.height} el={round(time.time()-t0,1)}s"


def _worker(a):
    day, data_root, prev_day, splits_path, se, force = a
    splits = bhs.split_factor_frame(Path(data_root))
    return process_day(day, Path(data_root), {day: prev_day}, splits, {day: se}, force)


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
    sessions = bhs.prev_session_map(data_root)
    splits_path = data_root / "harvest01" / "base" / "splits.parquet"
    splits = bhs.split_factor_frame(data_root)
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
            print(process_day(d, data_root, sessions, splits, sends, args.force), flush=True)
    else:
        import multiprocessing as mp
        tasks = [(d, str(data_root), sessions.get(d), str(splits_path),
                  int(sends.get(d, 959)), args.force) for d in days]
        with mp.get_context("spawn").Pool(processes=args.workers) as pool:
            for line in pool.imap_unordered(_worker, tasks):
                print(line, flush=True)
    print(f"done {len(days)} days in {round(time.time() - t0, 1)}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
