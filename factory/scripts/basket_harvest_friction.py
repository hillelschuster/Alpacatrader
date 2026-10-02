#!/usr/bin/env python3
"""HARVEST01 — measured friction pass on the actual basket members (local print tape).

For a bounded population of member-days (default: clock-569 scalp members plus rank-1
members of clocks 560/600) compare the simulation's execution assumptions against the
raw SIP prints that actually traded:

  * entry slippage: first price-updating print at/after the fill minute vs the sim's
    bar-open fill price;
  * exit slippage at fixed clocks E: first print at/after E vs the sim's bar open;
  * participation: reference $10k notional vs that minute's traded dollar volume;
  * auction attribution across the 09:29->09:31 window (condition codes {Q,M,O,5,6}):
    last pre-open print -> first post-open print, then first post-open print -> first
    print after the exit clock (the open jump vs the first-minute move);
  * print density and the longest print gap in the held window.

Outputs: harvest01/report/friction_members.parquet + friction.md
Usage:
    .venv/bin/python factory/scripts/basket_harvest_friction.py --clocks 569 560 600
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
import time
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402
import basket_subminute_probe as probe  # noqa: E402
import basket_t5_rawpaths as t5  # noqa: E402

AUCTION_CODES = {"Q", "M", "O", "5", "6"}
EXITS = (571, 575, 585)
REF_NOTIONAL = 10_000.0


def bar_open_at(bars: dict, et_target: int) -> tuple[float, int] | None:
    b = bars
    j = bisect.bisect_left(b["et"], et_target)
    return None if j >= len(b["et"]) else (b["open"][j], b["et"][j])


def process_day(day: str, data_root: Path, clocks: tuple, rank_cap: int, force: bool) -> str:
    outd = data_root / "harvest01" / "friction"
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
    fills = pl.read_parquet(fl_p).filter(
        (pl.col("variant") == "primary") & (pl.col("status") == "filled")
        & pl.col("clock").is_in(list(clocks)) & (pl.col("rank") <= rank_cap))
    if fills.height == 0:
        return f"{day}: no members"
    tickers = sorted(set(fills["ticker"].to_list()))
    bars_p = data_root / "harvest01" / "base" / "bars" / f"{day}.parquet"
    bars_all = {}
    if bars_p.exists():
        bd = pl.read_parquet(bars_p, columns=["ticker", "et", "open", "high", "low", "close"])
        for tk, sub in bd.group_by("ticker"):
            t_ = tk[0] if isinstance(tk, tuple) else tk
            s = sub.sort("et")
            bars_all[t_] = {c: s[c].to_list() for c in ("et", "open", "high", "low", "close")}
    t0 = time.time()
    try:
        frame, _ = probe.load_prints(day, tickers)
    except Exception as e:
        return f"{day}: print load failed {str(e)[:80]}"
    if frame.height == 0:
        return f"{day}: no prints"
    pu = t5.price_updating(frame)
    rows = []
    for m in fills.iter_rows(named=True):
        tk = m["ticker"]
        sub = pu.filter(pl.col("symbol") == tk).sort("ts_utc")
        if sub.height == 0:
            rows.append({"day": day, "ticker": tk, "clock": m["clock"], "rank": m["rank"],
                         "status": "no_prints"})
            continue
        ets = sub["et"].to_list()
        px = sub["price"].to_list()
        sz = sub["size"].to_list()
        auc = [any(c in AUCTION_CODES for c in (conds or [])) for conds in sub["conditions"].to_list()]
        fill_et = int(m["fill_et"])
        row = {"day": day, "ticker": tk, "clock": int(m["clock"]), "rank": int(m["rank"]),
               "status": "ok", "fill_et": fill_et, "fill_px": float(m["fill_px"]),
               "mfe_adj": m.get("mfe_adj")}
        i = bisect.bisect_left(ets, fill_et)
        if i < len(ets):
            row["first_print_et"] = int(ets[i])
            row["entry_slip"] = float(px[i] / m["fill_px"] - 1)
            row["entry_is_auction"] = bool(auc[i])
            # minute dollar volume at entry
            dv = sum(px[j] * sz[j] for j in range(i, len(ets)) if ets[j] == ets[i])
            row["entry_min_dollar_vol"] = float(dv)
            row["entry_participation"] = float(REF_NOTIONAL / dv) if dv > 0 else None
            # first continuous print after 570 (post-open)
            j = bisect.bisect_left(ets, 570)
            row["first_post570_et"] = int(ets[j]) if j < len(ets) else None
            row["last_pre570_px"] = None
            k = bisect.bisect_left(ets, 570) - 1
            if k >= 0:
                row["last_pre570_px"] = float(px[k])
            if j < len(ets) and k >= 0:
                row["open_jump"] = float(px[j] / px[k] - 1)
        for E in EXITS:
            target = max(E, fill_et + 1)
            bp = bar_open_at(bars_all.get(tk) or {"et": []}, target)
            j = bisect.bisect_left(ets, target)
            if bp is not None and j < len(ets):
                row[f"exit{E}_bar_open"] = bp[0]
                row[f"exit{E}_first_print"] = float(px[j])
                row[f"exit{E}_slip"] = float(px[j] / bp[0] - 1)
                dv = sum(px[q] * sz[q] for q in range(j, len(ets)) if ets[q] == ets[j])
                row[f"exit{E}_min_dollar_vol"] = float(dv)
                row[f"exit{E}_participation"] = float(REF_NOTIONAL / dv) if dv > 0 else None
        gaps = [ets[q + 1] - ets[q] for q in range(max(i, 0), max(len(ets) - 1, 0))]
        row["max_gap_min"] = int(max(gaps)) if gaps else None
        row["n_prints_held"] = int(max(0, len(ets) - i))
        rows.append(row)
    df = pl.DataFrame(rows, infer_schema_length=None)
    fp, tmp = outd / f"{day}.parquet", outd / f"{day}.parquet.tmp"
    df.write_parquet(tmp)
    import os
    os.replace(tmp, fp)
    mp_.write_text(json.dumps({"day": day, "status": "ok", "rows": int(df.height),
                               "elapsed_s": round(time.time() - t0, 1)}, indent=1))
    return f"{day}: ok rows={df.height} el={round(time.time()-t0,1)}s"


def _worker(a):
    day, data_root, clocks, rank_cap, force = a
    return process_day(day, Path(data_root), tuple(clocks), rank_cap, force)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day")
    ap.add_argument("--days", nargs="+", default=[])
    ap.add_argument("--dev-days", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--clocks", nargs="+", type=int, default=[569, 560, 600])
    ap.add_argument("--rank-cap", type=int, default=2)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    data_root = bps.resolve_data_root(args.data_root)
    cal = json.loads(bps.CALENDAR.read_text())["evidence"]
    days = list(args.days)
    if args.day:
        days.append(args.day)
    if args.dev_days:
        days = sorted(cal.keys())
    if args.limit:
        days = days[:args.limit]
    if not days:
        ap.print_help()
        return 2
    t0 = time.time()
    if args.workers <= 1:
        for d in days:
            print(process_day(d, data_root, tuple(args.clocks), args.rank_cap, args.force), flush=True)
    else:
        import multiprocessing as mp
        tasks = [(d, str(data_root), tuple(args.clocks), args.rank_cap, args.force) for d in days]
        with mp.get_context("spawn").Pool(processes=args.workers) as pool:
            for line in pool.imap_unordered(_worker, tasks):
                print(line, flush=True)
    print(f"done {len(days)} days in {round(time.time() - t0, 1)}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
