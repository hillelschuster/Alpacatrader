#!/usr/bin/env python3
"""HARVEST01 — per-day minute bars for every selected ticker (execution + path lane).

For each day, read the union of tickers that appear anywhere in
harvest01/base/selected/<day>.parquet and fetch Alpaca SIP RAW minute bars over
[04:00, session_end+1min) ET, filtered to et <= session_end (early closes from the
canonical calendar). These bars price entries, exits, paths and management rules.

Cross-check kept inside the producer: for every PM clock present in the day's snapshot
the first bar open with et >= C must equal the snapshot's fo_C (same underlying source);
mismatch counts are recorded per day.

Usage:
    .venv/bin/python factory/scripts/basket_harvest_bars.py --days 2021-02-01
    .venv/bin/python factory/scripts/basket_harvest_bars.py --dev-days --workers 3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402

from zoneinfo import ZoneInfo  # noqa: E402

ET = ZoneInfo("America/New_York")
_CLIENT = None
_WINDOW_MIN, _WINDOW_MAX = 240, 965  # 04:00 .. 16:05 as a safety bound


def _client():
    global _CLIENT
    if _CLIENT is None:
        from dotenv import load_dotenv
        load_dotenv(Path("/home/hillel/projects/Alpacatrader/.env"))
        load_dotenv(ROOT / ".env")
        from alpaca.data.historical import StockHistoricalDataClient
        _CLIENT = StockHistoricalDataClient(os.environ["ALPACA_API_KEY"],
                                            os.environ["ALPACA_SECRET_KEY"])
    return _CLIENT


def session_ends() -> dict:
    cal = json.loads(bps.CALENDAR.read_text())
    return {d: int(v["session_end"]) for d, v in cal["evidence"].items()}


def day_window(day: str, session_end: int):
    d = datetime.fromisoformat(day)
    start = datetime(d.year, d.month, d.day, 4, 0, tzinfo=ET)
    end = datetime(d.year, d.month, d.day, 0, 0, tzinfo=ET) + timedelta(minutes=session_end + 1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def fetch(day: str, tickers: list[str], session_end: int):
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed, Adjustment

    start, end = day_window(day, session_end)
    client = _client()
    rows, errors, raw_n = [], [], 0
    for i in range(0, len(tickers), 100):
        batch = tickers[i:i + 100]
        attempt = 0
        while batch:
            try:
                req = StockBarsRequest(symbol_or_symbols=batch, timeframe=TimeFrame.Minute,
                                       start=start, end=end, feed=DataFeed.SIP,
                                       adjustment=Adjustment.RAW)
                res = client.get_stock_bars(req)
                for sym in batch:
                    for b in res.data.get(sym, []):
                        t = b.timestamp.astimezone(ET)
                        et = t.hour * 60 + t.minute
                        if not (_WINDOW_MIN <= et <= session_end):
                            continue
                        rows.append({"day": day, "ticker": sym, "et": int(et),
                                     "open": float(b.open), "high": float(b.high),
                                     "low": float(b.low), "close": float(b.close),
                                     "volume": float(b.volume or 0.0),
                                     "trade_count": (float(getattr(b, "trade_count"))
                                                     if getattr(b, "trade_count", None) is not None
                                                     else None)})
                        raw_n += 1
                break
            except Exception as e:
                if attempt >= 2:
                    errors.append({"batch0": batch[0], "n": len(batch), "error": str(e)[:200]})
                    break
                attempt += 1
                time.sleep(3 * attempt)
    return rows, errors, raw_n


def cross_check_pm(day: str, bars: pl.DataFrame, data_root: Path) -> dict:
    """first-bar-open at/after each PM clock must equal the snapshot's fo_C."""
    snap_p = data_root / "sip" / "pm_snapshots" / f"{day}.parquet"
    if not snap_p.exists() or bars.height == 0:
        return {"checked": 0, "mismatch": 0}
    snap = pl.read_parquet(snap_p).select(
        ["symbol"] + [f"fo_{C}" for C in bps.PM_CLOCKS] + [f"fet_{C}" for C in bps.PM_CLOCKS])
    merged = snap.rename({"symbol": "ticker"}).join(
        bars.select(["ticker", "et", "open"]), on="ticker", how="inner")
    checked = mismatch = 0
    for C in bps.PM_CLOCKS:
        sub = merged.filter(pl.col("et") >= C).sort(["ticker", "et"]).group_by("ticker").first()
        j = sub.join(snap.rename({"symbol": "ticker"}).select(["ticker", f"fo_{C}", f"fet_{C}"]),
                     on="ticker", how="inner")
        n = j.height
        bad = j.filter(
            (pl.col(f"fo_{C}").is_not_null()) & (pl.col("et") != pl.col(f"fet_{C}"))).height
        checked += n
        mismatch += bad
    return {"checked": checked, "mismatch": mismatch}


def process_day(day: str, data_root: Path, sends: dict, force: bool) -> str:
    outd = data_root / "harvest01" / "base" / "bars"
    outd.mkdir(parents=True, exist_ok=True)
    mp = outd / f"{day}.manifest.json"
    if not force and mp.exists():
        try:
            if json.loads(mp.read_text()).get("status") == "ok":
                return f"{day}: skip"
        except Exception:
            pass
    sel_p = data_root / "harvest01" / "base" / "selected" / f"{day}.parquet"
    if not sel_p.exists():
        return f"{day}: no selection"
    sel = pl.read_parquet(sel_p, columns=["ticker"])
    tickers = set(sel["ticker"].to_list())
    lead_p = data_root / "harvest01" / "base" / "leaders" / f"{day}.parquet"
    if lead_p.exists():
        lead = pl.read_parquet(lead_p, columns=["ticker", "rank"])
        tickers |= set(lead.filter(pl.col("rank") == 1)["ticker"].to_list())
    tickers = sorted(tickers)
    if not tickers:
        return f"{day}: empty selection"
    session_end = int(sends.get(day, 959))
    t0 = time.time()
    rows, errors, raw_n = fetch(day, tickers, session_end)
    df = (pl.DataFrame(rows, infer_schema_length=None).sort(["ticker", "et"])
          if rows else pl.DataFrame(schema={"day": pl.Utf8, "ticker": pl.Utf8, "et": pl.Int32,
                                            "open": pl.Float64, "high": pl.Float64,
                                            "low": pl.Float64, "close": pl.Float64,
                                            "volume": pl.Float64, "trade_count": pl.Float64}))
    fp, tmp = outd / f"{day}.parquet", outd / f"{day}.parquet.tmp"
    df.write_parquet(tmp)
    os.replace(tmp, fp)
    xc = cross_check_pm(day, df, data_root)
    man = {"day": day, "status": "ok" if not errors else "partial",
           "tickers": len(tickers), "rows": int(df.height), "bars_raw": int(raw_n),
           "session_end": session_end, "pm_fill_crosscheck": xc,
           "errors": errors[:10], "elapsed_s": round(time.time() - t0, 1)}
    mp.write_text(json.dumps(man, indent=1, default=str))
    return (f"{day}: ok tickers={len(tickers)} rows={df.height} xc={xc['mismatch']}/"
            f"{xc['checked']} el={man['elapsed_s']}s")


def _worker(a):
    day, data_root, session_end, force = a
    return process_day(day, Path(data_root), {day: session_end}, force)


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
    sends = session_ends()
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
            print(process_day(d, data_root, sends, args.force), flush=True)
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
