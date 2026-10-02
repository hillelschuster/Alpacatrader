#!/usr/bin/env python3
"""HARVEST01 — minute-grain state panel for the actual basket members (discovery lane).

One row per (day, variant, clock, rank, ticker, t) for every FILLED member, t from
fill_et+1 .. session_end. State at t is causal: own-path features use completed bars
with et <= t-1; market/race features come from race.minute_full row t (whose px is the
close of the last bar with et <= t-1); peer/basket features aggregate the sibling
members' states at the same t. Labels are future and executable: sell now = open of
the first bar >= t; hold = open of the first bar >= t+h; V_h = hold/sell - 1.

Outputs per day: <data>/harvest01/minute/<day>.parquet
Usage:
    .venv/bin/python factory/scripts/basket_harvest_minute.py --days 2021-02-01
    .venv/bin/python factory/scripts/basket_harvest_minute.py --dev-days --workers 3
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

VARIANTS = ("primary", "compact")
HORIZONS = (5, 15, 30, 60, 120)
TAIL_H = (30, 120)
RACE_COLS = ["t", "ticker", "px", "known_by_t", "age_min", "fresh_2m", "gain",
             "rank_known", "rank_fresh_2m", "n_known", "n_fresh_2m", "n_eligible"]


def load_bars(path: Path) -> dict:
    if not path.exists():
        return {}
    d = pl.read_parquet(path, columns=["ticker", "et", "open", "high", "low", "close", "volume"])
    out = {}
    for tk, sub in d.group_by("ticker"):
        t = tk[0] if isinstance(tk, tuple) else tk
        s = sub.sort("et")
        out[t] = {c: s[c].to_list() for c in ("et", "open", "high", "low", "close", "volume")}
    return out


def first_ge(ets: list[int], target: int, lo: int = 0) -> int:
    j = bisect.bisect_left(ets, target, lo)
    return j if j < len(ets) else -1


def member_series(b: dict, fill_et: int, fill_px: float, se: int) -> dict:
    """Per-minute causal state arrays for one member (index = t - start)."""
    ets = b["et"]
    h, lo_, c, v = b["high"], b["low"], b["close"], b["volume"]
    n = len(ets)
    fi = bisect.bisect_left(ets, fill_et)
    if fi >= n:
        return {}
    start = int(ets[fi]) + 1
    ts = list(range(start, se + 1))
    out = {k: [] for k in ("ret_fill", "dist_high", "mae_sofar", "bars_since_high", "nh5", "nh15",
                           "nh30", "streak_up", "ret1", "ret3", "ret5", "vol_accel",
                           "vol_med_ratio", "px", "px_age")}
    # rolling helpers
    peak = -1.0
    last_high_et = None
    nh_ets: list[int] = []
    mae = 0.0
    closes_seen: list[float] = []
    vols_seen: list[float] = []
    idx = fi
    for t in ts:
        while idx < n and ets[idx] <= t - 1:
            e = ets[idx]
            if h[idx] >= peak:
                peak = h[idx]
                last_high_et = e
                nh_ets.append(e)
            mae = min(mae, lo_[idx] / fill_px - 1)
            closes_seen.append(c[idx])
            vols_seen.append(v[idx])
            idx += 1
        if idx == fi:
            continue  # no completed bar yet
        last_e = ets[idx - 1]
        px = c[idx - 1]
        out["px"].append(px)
        out["px_age"].append(t - 1 - last_e)
        out["ret_fill"].append(px / fill_px - 1)
        out["dist_high"].append(px / peak - 1 if peak > 0 else None)
        out["mae_sofar"].append(mae)
        out["bars_since_high"].append(t - 1 - last_high_et if last_high_et is not None else None)
        for w, key in ((5, "nh5"), (15, "nh15"), (30, "nh30")):
            out[key].append(sum(1 for e in nh_ets if e > t - 1 - w))
        su = 0
        for cc in reversed(closes_seen):
            if cc > fill_px:
                su += 1
            else:
                break
        out["streak_up"].append(su)
        for k in (1, 3, 5):
            out[f"ret{k}"].append(closes_seen[-1] / closes_seen[-1 - k] - 1
                                  if len(closes_seen) > k else None)
        v5 = sum(vols_seen[-5:]) / max(1, len(vols_seen[-5:]))
        v20 = sum(vols_seen[-20:]) / max(1, len(vols_seen[-20:]))
        out["vol_accel"].append(v5 / v20 - 1 if v20 > 0 else None)
        med = sorted(vols_seen)[len(vols_seen) // 2] if vols_seen else 0.0
        out["vol_med_ratio"].append(v5 / med - 1 if med > 0 else None)
    out["t"] = ts[:len(out["px"])]
    return out


def process_day(day: str, data_root: Path, se: int, force: bool, variants: tuple) -> str:
    outd = data_root / "harvest01" / "minute"
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
        (pl.col("status") == "filled") & pl.col("variant").is_in(list(variants)))
    if fills.height == 0:
        return f"{day}: no filled members"
    bars = load_bars(data_root / "harvest01" / "base" / "bars" / f"{day}.parquet")
    t0 = time.time()
    # race rows for member tickers, all minutes
    tickers = sorted(set(fills["ticker"].to_list()))
    rf = (data_root / "atlas" / "observation" / "v0" / "race.minute_full"
          / f"month={day[:7]}" / f"{day}.parquet")
    race = {}
    if rf.exists():
        d = pl.scan_parquet(rf).select(RACE_COLS).filter(pl.col("ticker").is_in(tickers)).collect()
        for tk, sub in d.group_by("ticker"):
            t_ = tk[0] if isinstance(tk, tuple) else tk
            race[t_] = {r["t"]: r for r in sub.to_dicts()}
    rows = []
    # group members by (variant, clock) for peer aggregation
    for (variant, clock), grp in fills.group_by(["variant", "clock"]):
        members = grp.sort("rank").to_dicts()
        series = {}
        for m in members:
            b = bars.get(m["ticker"])
            if not b:
                continue
            s = member_series(b, int(m["fill_et"]), float(m["fill_px"]), se)
            if s:
                series[m["ticker"]] = s
        if not series:
            continue
        for m in members:
            s = series.get(m["ticker"])
            if not s:
                continue
            tk = m["ticker"]
            rr = race.get(tk, {})
            b = bars[tk]
            ets = b["et"]
            fill_et = int(m["fill_et"])
            for i, t in enumerate(s["t"]):
                r = rr.get(t)
                row = {"day": day, "variant": variant, "clock": int(clock), "rank": int(m["rank"]),
                       "ticker": tk, "t": int(t), "tenure": int(t) - fill_et,
                       "entry_gap": m.get("entry_gap_min")}
                for k in ("ret_fill", "dist_high", "mae_sofar", "bars_since_high", "nh5", "nh15",
                          "nh30", "streak_up", "ret1", "ret3", "ret5", "vol_accel",
                          "vol_med_ratio", "px", "px_age"):
                    row[k] = s[k][i]
                if r is not None:
                    row.update({"rank_known": r["rank_known"], "rank_fresh": r["rank_fresh_2m"],
                                "gain": r["gain"], "age_min": r["age_min"],
                                "fresh2": bool(r["fresh_2m"]) if r["fresh_2m"] is not None else None,
                                "n_known": r["n_known"], "n_fresh2": r["n_fresh_2m"],
                                "n_eligible": r["n_eligible"]})
                else:
                    row.update({"rank_known": None, "rank_fresh": None, "gain": None, "age_min": None,
                                "fresh2": None, "n_known": None, "n_fresh2": None, "n_eligible": None})
                # rank trajectory (t-5 / t-15)
                for k in (5, 15):
                    rk = rr.get(t - k)
                    row[f"drank{k}"] = (r["rank_known"] - rk["rank_known"]
                                        if r is not None and rk is not None
                                        and r["rank_known"] is not None
                                        and rk["rank_known"] is not None else None)
                # peers / basket at t
                sib_ret, above, sib_nh, bret = [], 0, 0, []
                for m2 in members:
                    if m2["ticker"] == tk:
                        continue
                    s2 = series.get(m2["ticker"])
                    if not s2:
                        continue
                    j = t - s2["t"][0]
                    if 0 <= j < len(s2["t"]):
                        sib_ret.append(s2["ret_fill"][j])
                        bret.append(s2["ret_fill"][j])
                        if s2["ret_fill"][j] > 0:
                            above += 1
                        if s2["nh5"][j] and s2["nh5"][j] > 0:
                            sib_nh += 1
                row["sib_ret_mean"] = (sum(sib_ret) / len(sib_ret)) if sib_ret else None
                row["sib_above_fill"] = above
                row["sib_nh5"] = sib_nh
                bret.append(s["ret_fill"][i])
                row["basket_ret"] = sum(bret) / len(bret)
                # labels
                sell_i = first_ge(ets, t)
                if sell_i < 0:
                    for h in HORIZONS:
                        row[f"v{h}"] = None
                    for h in TAIL_H:
                        row[f"fmfe{h}"], row[f"fmae{h}"] = None, None
                else:
                    sell_px = b["open"][sell_i]
                    for h in HORIZONS:
                        hi = first_ge(ets, t + h, sell_i)
                        row[f"v{h}"] = (b["open"][hi] / sell_px - 1) if hi >= 0 else None
                    for h in TAIL_H:
                        lo_i, hi_i = sell_i, first_ge(ets, t + h, sell_i)
                        if hi_i < 0:
                            row[f"fmfe{h}"], row[f"fmae{h}"] = None, None
                        else:
                            seg_h = b["high"][lo_i:hi_i + 1]
                            seg_l = b["low"][lo_i:hi_i + 1]
                            row[f"fmfe{h}"] = max(seg_h) / sell_px - 1 if seg_h else None
                            row[f"fmae{h}"] = min(seg_l) / sell_px - 1 if seg_l else None
                rows.append(row)
    df = pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame(
        schema={"day": pl.Utf8, "variant": pl.Utf8})
    fp, tmp = outd / f"{day}.parquet", outd / f"{day}.parquet.tmp"
    df.write_parquet(tmp)
    os.replace(tmp, fp)
    mp_.write_text(json.dumps({"day": day, "status": "ok", "rows": int(df.height),
                               "elapsed_s": round(time.time() - t0, 1)}, indent=1))
    return f"{day}: ok rows={df.height} el={round(time.time()-t0,1)}s"


def _worker(a):
    day, data_root, se, force, variants = a
    return process_day(day, Path(data_root), se, force, tuple(variants))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day")
    ap.add_argument("--days", nargs="+", default=[])
    ap.add_argument("--dev-days", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--variants", nargs="+", default=list(VARIANTS))
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
            print(process_day(d, data_root, int(sends.get(d, 959)), args.force,
                              tuple(args.variants)), flush=True)
    else:
        import multiprocessing as mp
        tasks = [(d, str(data_root), int(sends.get(d, 959)), args.force,
                  tuple(args.variants)) for d in days]
        with mp.get_context("spawn").Pool(processes=args.workers) as pool:
            for line in pool.imap_unordered(_worker, tasks):
                print(line, flush=True)
    print(f"done {len(days)} days in {round(time.time() - t0, 1)}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
