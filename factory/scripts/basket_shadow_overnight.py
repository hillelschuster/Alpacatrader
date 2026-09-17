#!/usr/bin/env python3
"""BASKET-01 T7 fixed overnight shadow ledger — EOD close -> next-session first open.

Diagnostic only (PRE-REG T7): records what every filled main/adj member did between
the held EOD close and the next session's first executable RTH open. It is NEVER used
to repair the intraday result, and it is not a strategy input.

Evidence boundary: if the next session falls in a reserved month (2026-06..08) the row
is skipped with reason "reserved" — the boundary is respected even for diagnostics.

Outputs (resumable; per-day files gitignored like the other raw passes):
  factory/artifacts/basket/shadow/YYYY-MM-DD.jsonl   {date,ticker,close,next_date,next_open,next_et,gap,reason}
  factory/artifacts/basket/agg/T7_overnight.json     month-blocked quantiles (committed)

Usage:
  .venv/bin/python factory/scripts/basket_shadow_overnight.py --self-test
  .venv/bin/python factory/scripts/basket_shadow_overnight.py --months 2025-06
  .venv/bin/python factory/scripts/basket_shadow_overnight.py             # all days
"""
from __future__ import annotations

import argparse
import glob
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
ANAT = ROOT / "factory" / "artifacts" / "basket" / "anatomy"
SHADOW = ROOT / "factory" / "artifacts" / "basket" / "shadow"
OUT = ROOT / "factory" / "artifacts" / "basket" / "agg"
RESERVED = {"2026-06", "2026-07", "2026-08"}
PRIMARY_N = 3
UP_STRATA = [20, 30, 50]


def month_path(month: str) -> Path:
    if month >= "2026-03":
        return DATA / "backfill" / f"clean_ohlcv_{month}.parquet"
    return DATA / f"clean_ohlcv_{month}.parquet"


def trading_dates() -> list:
    """Repo trading calendar from the leaderboard file names (complete, 2021-02..2026-08)."""
    ds = sorted(Path(p).name[3:13] for p in glob.glob(str(DATA / "leaderboard" / "lb_*.parquet")))
    return [d for d in ds if len(d) == 10]


def next_of(dates: list, day: str):
    i = np.searchsorted(np.array(dates), day, side="right")
    return dates[i] if i < len(dates) else None


def gap_of(close, next_open):
    if close is None or next_open is None or close <= 0:
        return None
    return round(float(next_open) / float(close) - 1.0, 6)


def _q(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return None
    a = np.array(v, dtype=float)
    return {"n": int(len(a)), "mean": round(float(a.mean()), 5),
            "p10": round(float(np.percentile(a, 10)), 5),
            "p50": round(float(np.percentile(a, 50)), 5),
            "p90": round(float(np.percentile(a, 90)), 5)}


def member_index(files):
    """(date,ticker) -> {close, strata mfes}; plus per-day (pop,T,set,ticker) rows for agg."""
    closes, rows = {}, []
    for f in files:
        rec = json.load(open(f))
        day = rec["date"]
        for s in rec.get("snapshots", []):
            names = s["names"]
            for setname, mset in (("main", names[:PRIMARY_N]), ("adj", names[PRIMARY_N:2 * PRIMARY_N])):
                for n in mset:
                    f_ = n.get("fill")
                    if not f_ or f_.get("blocked"):
                        continue
                    closes[(day, n["ticker"])] = n.get("close")
                    rows.append({"date": day, "month": day[:7], "pop": s["pop"], "T": s["T"],
                                 "set": setname, "ticker": n["ticker"],
                                 "mfe": n.get("mfe"), "eod_ret": n.get("eod_ret")})
    return closes, rows


def run(files, force=False):
    import polars as pl

    dates = trading_dates()
    closes, rows = member_index(files)
    need = defaultdict(set)  # next_date -> tickers
    next_map = {}
    for (day, tkr) in closes:
        nd = next_of(dates, day)
        next_map[(day, tkr)] = nd
        if nd and nd[:7] not in RESERVED:
            need[nd].add(tkr)

    resolved = {}  # (next_date, ticker) -> (open, et)
    from datetime import date as _date

    months_need = defaultdict(lambda: {"dates": set(), "tickers": set()})
    for nd in sorted(need):
        months_need[nd[:7]]["dates"].add(nd)
        months_need[nd[:7]]["tickers"] |= need[nd]
    for month, mset in sorted(months_need.items()):
        mp = month_path(month)
        if not mp.exists():
            continue
        ds = [_date.fromisoformat(x) for x in sorted(mset["dates"])]
        tickers = sorted(mset["tickers"])
        lf = pl.scan_parquet(mp).select(["timestamp", "ticker", "open"])
        lf = lf.with_columns(pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("tset"))
        lf = lf.with_columns(
            (pl.col("tset").dt.hour().cast(pl.Int32) * 60 + pl.col("tset").dt.minute().cast(pl.Int32)).alias("et"),
            pl.col("tset").dt.date().alias("date"),
        )
        df = lf.filter(pl.col("date").is_in(ds) & pl.col("ticker").is_in(tickers)).collect()
        df = df.filter((pl.col("et") >= 570) & (pl.col("et") < 960)).sort("et")
        first = df.group_by(["date", "ticker"], maintain_order=True).agg(
            pl.col("open").first().alias("o"), pl.col("et").first().alias("e"))
        for d, t, o, e in zip(first["date"].to_list(), first["ticker"].to_list(),
                              first["o"].to_list(), first["e"].to_list()):
            resolved[(str(d), t)] = (float(o), int(e))

    SHADOW.mkdir(parents=True, exist_ok=True)
    by_day = defaultdict(dict)
    for (day, tkr), c in closes.items():
        nd = next_map[(day, tkr)]
        reason = None
        if nd is None:
            reason = "no_next"
        elif nd[:7] in RESERVED:
            reason = "reserved"
        elif (nd, tkr) not in resolved:
            reason = "no_data"
        val = resolved.get((nd, tkr))
        by_day[day][tkr] = {
            "date": day, "ticker": tkr, "close": c, "next_date": nd,
            "next_open": None if val is None else round(val[0], 6),
            "next_et": None if val is None else val[1],
            "gap": None if val is None else gap_of(c, val[0]),
            "reason": reason,
        }
    n_written = 0
    for day, d in sorted(by_day.items()):
        p = SHADOW / f"{day}.jsonl"
        if p.exists() and not force:
            continue
        with open(p, "w") as fh:
            for tkr in sorted(d):
                fh.write(json.dumps(d[tkr], separators=(",", ":"), default=str) + "\n")
        n_written += 1

    # ---- aggregate over month/pop/T/set with mfe strata (from shadow files)
    acc: dict = defaultdict(list)  # 4-tuple (month,set,stratum,stat) -> vals
    reasons: dict = defaultdict(int)
    for f in sorted(glob.glob(str(SHADOW / "*.jsonl"))):
        day = Path(f).name[:10]
        if day > "2026-05":
            continue
        shadow_day = {}
        for line in open(f):
            r = json.loads(line)
            shadow_day[r["ticker"]] = r
            reasons[r["reason"] or "ok"] += 1
        for row in rows:
            if row["date"] != day:
                continue
            s = shadow_day.get(row["ticker"])
            if s is None:
                continue
            g = s["gap"]
            key = (row["month"], row["set"])
            acc[key + ("all", "gap")].append(g)
            for cut in UP_STRATA:
                if row["mfe"] is not None and row["mfe"] >= cut / 100.0:
                    acc[key + (f"mfe>={cut}", "gap")].append(g)
            acc[key + ("all", "gap|eod_ret")].append(
                None if g is None or row["eod_ret"] is None else float(row["eod_ret"]) + float(g))
    tables = []
    for (month, setname, stratum, stat), vals in sorted(acc.items()):
        q = _q(vals)
        if q:
            tables.append({"month": month, "set": setname, "stratum": stratum, "stat": stat, **q})
    out = {"reasons": dict(reasons), "tables": tables}
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "T7_overnight.json", "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(f"shadow: wrote {n_written} day files; agg rows {len(tables)}; reasons {dict(reasons)}")
    return out


def selftest():
    dates = ["2025-06-02", "2025-06-03", "2025-06-04", "2025-06-05"]
    assert next_of(dates, "2025-06-02") == "2025-06-03"
    assert next_of(dates, "2025-06-05") is None
    assert next_of(dates, "2025-06-03") == "2025-06-04"
    assert gap_of(10.0, 11.0) == 0.1
    assert gap_of(10.0, None) is None and gap_of(0, 5) is None
    assert next_of([], "2025-01-01") is None
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
