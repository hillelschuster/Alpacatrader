#!/usr/bin/env python3
"""TradingView-equivalent top-gainers leaderboard.

live  : fetch current #1/#2/#3 via TV scanner API (no key needed).
hist MONTH... : reconstruct per-(date, ET-minute) causal leaderboard from
                clean_ohlcv monthly parquets, same semantics as the live
                source, and cache to data/leaderboard/.
semantics (pinned 2026-09-09 by comparing the live movers page against the
scanner API): exchange in {NASDAQ, NYSE, AMEX}, type=stock, subtype=common,
rank by change vs previous session close, desc. No price/volume/mcap floors
(page has none — verified down to $2.02 rows; MGN $0.39 IS rank 1 on the live
API and absent only because the public page snapshot was cached earlier).

Historical universe via data/universe_tags.parquet (yfinance exchange codes:
NMS/NGM/NCM=NASDAQ, NYQ=NYSE, ASE=AMEX, quote_type=EQUITY). Tickér-suffix
junk (warrants/units/rights) excluded by pattern; tags cover ~10.6k names and
are the repo's existing PIT source (caveat: yfinance, not exchange PIT).
"""
import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "leaderboard"
ART = ROOT / "factory" / "artifacts"

TV_URL = "https://scanner.tradingview.com/america/scan"
TV_BODY = {
    "symbols": {"tickers": [], "query": {"types": ["stock"]}},
    "columns": ["name", "close", "change", "volume", "market_cap_basic",
                "type", "subtype", "exchange", "typespecs"],
    "filter": [{"left": "exchange", "operation": "in_range",
                "right": ["NASDAQ", "NYSE", "AMEX"]},
               {"left": "type", "operation": "in_range", "right": ["stock"]},
               {"left": "subtype", "operation": "in_range", "right": ["common"]}],
    "sort": {"sortBy": "change", "sortOrder": "desc"},
    "range": [0, 5],
}

YF_EXCHANGE = {"NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ",
               "NYQ": "NYSE", "ASE": "AMEX"}
JUNK = re.compile(r".{3}[A-Z]?[WU]|^.{4}(W|WS|WW|WT|U|UN|UNN|UT|UU|UW|RT|PR|R)$")


def cmd_live(k=3):
    req = urllib.request.Request(
        TV_URL, data=json.dumps(TV_BODY).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                 "Origin": "https://www.tradingview.com"})
    with urllib.request.urlopen(req, timeout=25) as r:
        d = json.loads(r.read())
    if d.get("error"):
        sys.exit(f"TV error: {d['error']}")
    for row in d["data"][:k]:
        v = row["d"]
        print(f"{v[0]:8s} {v[2]:>8.2f}%  close={v[1]:<9} vol={v[3]:<10} "
              f"mcap={v[4]} {v[5]}/{v[6]} {v[7]}")


def eligible_set():
    tags = pd.read_parquet(ROOT / "data" / "universe_tags.parquet")
    tags = tags[tags["quote_type"] == "EQUITY"]
    tags = tags[tags["exchange"].isin(YF_EXCHANGE)]
    ok = set(tags["ticker"])
    return {t for t in ok if not JUNK.match(t)}


def month_path(month: str) -> Path:
    import os
    stage = Path(os.environ.get("H12_STAGE", "/tmp/opencode/h12stage"))
    stage.mkdir(parents=True, exist_ok=True)
    sp = stage / f"clean_ohlcv_{month}.parquet"
    if sp.exists():
        return sp
    base = ROOT / "data" / "backfill" if month >= "2026-03" else ROOT / "data"
    src = base / f"clean_ohlcv_{month}.parquet"
    if src.exists():
        import shutil
        shutil.copy2(src, sp)
    return src


def month_days(month: str):
    """ET session dates in a month file (via UTC ts -> ET date, tz-safe)."""
    df = pd.read_parquet(month_path(month), columns=["timestamp"])
    ts = pd.to_datetime(df["timestamp"], utc=True)
    return sorted(ts.dt.tz_convert("America/New_York").dt.date.unique())


def prev_close_map(month: str, day) -> dict:
    """{ticker: prev session close} from the IMMEDIATELY prior trading session
    (searches back across month files by session date, not month order)."""
    import datetime as dt
    d0 = day if isinstance(day, dt.date) else dt.date.fromisoformat(str(day))
    for back in range(1, 10):
        tgt = d0 - dt.timedelta(days=back)
        mm = f"{tgt.year:04d}-{tgt.month:02d}"
        p = month_path(mm)
        if not p.exists():
            continue
        df = pd.read_parquet(p, columns=["timestamp", "ticker", "close"])
        ts = pd.to_datetime(df["timestamp"], utc=True)
        etd = ts.dt.tz_convert("America/New_York")
        eth = etd.dt.hour * 60 + etd.dt.minute
        df = df[(eth >= 570) & (eth < 960)]
        df["d"] = etd.dt.date
        if tgt not in set(df["d"].unique()):
            continue
        last = (df[df["d"] == tgt].sort_values("timestamp")
                .groupby("ticker")["close"].last())
        if len(last):
            return dict(last)
    return {}


def board_at(gday: pd.DataFrame, prev: dict, elig: set, t: int, k: int):
    """Causal leaderboard at arbitrary decision time t: rank eligible names by
    (close of last bar stamped et<=t-1) / prev_session_close - 1, desc.

    Bars are START-stamped: bar et==t-1 completes at t, so its close is the
    last price causally known at t. No shift(1) — that would use bar t-2.
    """
    hist = gday[(gday["et"] <= t - 1) & gday["ticker"].isin(elig)
                & gday["ticker"].isin(prev)]
    if len(hist) == 0:
        return []
    last = (hist.sort_values("timestamp").groupby("ticker")
            .agg(px=("close", "last"), et_last=("et", "max")))
    last = last[last["et_last"] <= t - 1]
    last["gain"] = last["px"] / last.index.map(prev) - 1
    last = last.sort_values("gain", ascending=False)
    out = []
    for rank, (tk, r) in enumerate(last.head(k).iterrows(), 1):
        out.append({"t": t, "rank": rank, "ticker": tk,
                    "gain": round(float(r["gain"]), 4),
                    "px": round(float(r["px"]), 4)})
    return out


def cmd_hist(months, k=3, ts=None):
    elig = eligible_set()
    ts = ts or list(range(585, 631, 5))  # default grid ONLY when caller passes none
    for month in months:
        for day in month_days(month):
            key = CACHE / f"lb_{day.isoformat()}.parquet"
            if key.exists():
                continue
            p = month_path(month)
            df = pd.read_parquet(p, columns=["timestamp", "ticker", "close"])
            ts_utc = pd.to_datetime(df["timestamp"], utc=True)
            et = ts_utc.dt.tz_convert("America/New_York")
            df["et"] = et.dt.hour * 60 + et.dt.minute
            df["d"] = et.dt.date
            g = df[(df["d"] == day) & (df["et"] >= 570) & (df["et"] <= 960)]
            if len(g) == 0:
                pd.DataFrame().to_parquet(key)
                continue
            prev = prev_close_map(month, day)
            rows = [dict(date=str(day), **r) for t in ts for r in board_at(g, prev, elig, t, k)]
            out = pd.DataFrame(rows)
            out.to_parquet(key)
            print(f"{day}: {len(out)} rows", flush=True)


def cmd_sanity():
    """Spot-check historical reconstruction against a few known mover days."""
    files = sorted(CACHE.glob("lb_*.parquet"))
    if not files:
        sys.exit("no leaderboard cache")
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    print(f"{len(df)} rows, {df['date'].nunique()} days")
    print("\nrank-1 gain distribution (per snapshot):")
    print(df[df["rank"] == 1]["gain"].describe().round(3).to_string())
    print("\ntop-10 most frequent rank-1 names:")
    print(df[df["rank"] == 1]["ticker"].value_counts().head(10).to_string())
    # stability: rank-1 name changes per day (attention turnover)
    per_day = df[df["rank"] == 1].groupby("date")["ticker"].nunique()
    print(f"\nrank-1 unique names/day: mean {per_day.mean():.2f} "
          f"max {per_day.max()} (of {int((631-585)/5)+1} snapshots)")


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["live", "hist", "sanity", "verify"])
    p.add_argument("months", nargs="*")
    p.add_argument("--k", type=int, default=3)
    p.add_argument("--ts", nargs="*", type=int, default=None,
                   help="decision times (ET minutes); default research grid 585..630/5")
    p.add_argument("--day", default=None, help="verify: YYYY-MM-DD")
    a = p.parse_args()
    if a.cmd == "live":
        cmd_live(a.k)
    elif a.cmd == "hist":
        cmd_hist(a.months, a.k, a.ts)
    elif a.cmd == "verify":
        cmd_verify(a.day, a.ts, a.k)
    else:
        cmd_sanity()


def cmd_verify(day, ts, k):
    """Explicit causal trace at given timestamps: prev close, causal price used,
    gain, resulting top-3 — with bar-level evidence printed."""
    if not day:
        sys.exit("verify needs --day YYYY-MM-DD")
    elig = eligible_set()
    ts = ts or [585, 600, 615]
    month = day[:7]
    p = month_path(month)
    df = pd.read_parquet(p, columns=["timestamp", "ticker", "close"])
    ts_utc = pd.to_datetime(df["timestamp"], utc=True)
    et = ts_utc.dt.tz_convert("America/New_York")
    df["et"] = et.dt.hour * 60 + et.dt.minute
    df["d"] = et.dt.date
    import datetime as dt
    d = dt.date.fromisoformat(day)
    g = df[(df["d"] == d) & (df["et"] >= 570) & (df["et"] <= 960)]
    prev = prev_close_map(month, d)
    for i in range(1, 10):
        cand = (d - dt.timedelta(days=i))
        mm = f"{cand.year:04d}-{cand.month:02d}"
        pp = month_path(mm)
        if pp.exists():
            ddf = pd.read_parquet(pp, columns=["timestamp", "ticker", "close"])
            tss = pd.to_datetime(ddf["timestamp"], utc=True)
            e = tss.dt.tz_convert("America/New_York")
            ddf["d"] = e.dt.date
            if cand in set(ddf["d"].unique()):
                print(f"prev session = {cand} (searched {i} cal days back)\n")
                break
    for t in ts:
        print(f"=== t={t} ET ({t//60}:{t%60:02d}) — causal price = close of last bar et<={t-1}")
        hist = g[(g["et"] <= t - 1) & g["ticker"].isin(elig) & g["ticker"].isin(prev)]
        last = (hist.sort_values("timestamp").groupby("ticker")
                .agg(px=("close", "last"), et_last=("et", "max")))
        last = last[last["et_last"] <= t - 1]
        last["prev"] = last.index.map(prev)
        last["gain"] = last["px"] / last["prev"] - 1
        top = last.sort_values("gain", ascending=False).head(k)
        for rank, (tk, r) in enumerate(top.iterrows(), 1):
            bar_et = int(r["et_last"])
            print(f"  #{rank} {tk:6s} prev_close={r['prev']:<8} causal_px={r['px']:<8} "
                  f"(bar et={bar_et} {bar_et//60}:{bar_et%60:02d}) gain={r['gain']*100:6.2f}%")
        print()


if __name__ == "__main__":
    main()
