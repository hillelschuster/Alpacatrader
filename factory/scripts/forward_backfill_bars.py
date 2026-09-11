#!/usr/bin/env python3
"""Backfill session minute bars for live-observer scan days (evidence-only).
Reads data/forward/<day>/scans.jsonl (symbols with percent_gain), fetches
Alpaca SIP (fallback IEX) 1-min bars for that ET session, and writes
bars.jsonl in the forward_observe.log_new_bars schema so the frozen-rule
scorer (flush_forward_score.py) can replay live days. Never trades.
Usage: python forward_backfill_bars.py --day 2026-09-09 ... | --all
"""
import argparse
import json
import os
import sys
import time
from datetime import date as _date, datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

FWD = ROOT / "data" / "forward"
BATCH = 20


def scan_symbols(day_dir: Path):
    p = day_dir / "scans.jsonl"
    if not p.exists():
        return []
    syms = set()
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        for r in obj.get("rows", []):
            s = r.get("symbol")
            if s and r.get("percent_gain") is not None:
                syms.add(s)
    return sorted(syms)


def fetch_day(day: str, syms, log=print):
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed
    from alpaca.common.enums import Sort
    et = ZoneInfo("America/New_York")
    d = _date.fromisoformat(day)
    start = datetime.combine(d, dtime(9, 30), tzinfo=et)
    hc = StockHistoricalDataClient(os.getenv("ALPACA_API_KEY"),
                                   os.getenv("ALPACA_SECRET_KEY"))
    today_et = datetime.now(et).date().isoformat()
    feeds = ((DataFeed.IEX, DataFeed.SIP) if day == today_et
             else (DataFeed.SIP, DataFeed.IEX))
    for feed in feeds:
        got: dict = {}
        ok = True
        for i in range(0, len(syms), BATCH):
            chunk = syms[i:i + BATCH]
            try:
                res = hc.get_stock_bars(StockBarsRequest(
                    symbol_or_symbols=chunk, timeframe=TimeFrame.Minute,
                    start=start, sort=Sort.ASC, limit=10000, feed=feed)).data
            except Exception as e:
                log(f"  feed {feed.value} chunk {i} err: {str(e)[:90]}")
                for s in chunk:
                    try:
                        res2 = hc.get_stock_bars(StockBarsRequest(
                            symbol_or_symbols=[s], timeframe=TimeFrame.Minute,
                            start=start, sort=Sort.ASC, limit=10000,
                            feed=feed)).data
                        for s2, bl in (res2 or {}).items():
                            got.setdefault(s2, []).extend(bl)
                    except Exception:
                        continue
                time.sleep(0.2)
                continue
            for s, blist in (res or {}).items():
                got.setdefault(s, []).extend(blist)
            time.sleep(0.15)
        if got:
            return got, feed.value
    return {}, None


def write_bars(day_dir: Path, day: str, got) -> int:
    et = ZoneInfo("America/New_York")
    d = _date.fromisoformat(day)
    rows = []
    for sym, blist in got.items():
        sess = []
        for b in blist:
            bt = b.timestamp
            if bt.tzinfo is None:
                bt = bt.replace(tzinfo=ZoneInfo("UTC"))
            be = bt.astimezone(et)
            if be.date() != d or not (570 <= be.hour * 60 + be.minute <= 959):
                continue
            sess.append((bt, b))
        sess.sort(key=lambda x: x[0])
        for i, (bt, b) in enumerate(sess, 1):
            rows.append({"ts": str(bt), "symbol": sym, "o": b.open, "h": b.high,
                         "l": b.low, "c": b.close, "v": b.volume, "n_bars": i})
    out = day_dir / "bars.jsonl"
    with open(out, "w") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    if a.all:
        days = sorted(p.name for p in FWD.iterdir()
                      if p.is_dir() and (p / "scans.jsonl").exists())
    elif a.day:
        days = a.day
    else:
        sys.exit("pass --day YYYY-MM-DD or --all")
    for day in days:
        day_dir = FWD / day
        syms = scan_symbols(day_dir)
        if not syms:
            print(f"{day}: no scan symbols")
            continue
        got, feed = fetch_day(day, syms, log=print)
        n = write_bars(day_dir, day, got) if got else 0
        print(f"{day}: {len(syms)} scanned symbols -> {len(got)} with bars "
              f"({feed or 'none'}) -> {n} session rows")


if __name__ == "__main__":
    main()
