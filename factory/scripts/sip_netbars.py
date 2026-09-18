#!/usr/bin/env python3
"""BASKET-01 SIP substrate — Layer-2 net bars + coverage classification.

For one day, builds the SIP anatomy substrate from Layer-2 artifacts:
  * derived bars: raw SIP trades (data/sip/net/trades/DAY.parquet) -> sip_bars.build_bars
    (policy='alpaca', the owner-approved condition table);
  * provider bars: Alpaca SIP 1-minute bars for the same net symbols (one batched request);
  * per-symbol coverage class: healthy_raw / provider_only / unresolved, with reasons;
  * merged RTH day frame (570<=et<960) with a `src` column (derived|provider).

Raw market record first, interpretation second: provider bars are only a fallback /
cross-check for symbols whose raw trade archive is insufficient; every classification is
recorded per symbol-day, never silently dropped.

Outputs (atomic, resumable):
  data/sip/net/bars/<day>.parquet        merged rows: date,ticker,et,open,high,low,close,volume,src
  data/sip/net/coverage/<day>.json       per-symbol class + counts + reasons
  data/sip/net/coverage/<day>.manifest.json  status/elapsed/hash/source sha256s

Usage:
  .venv/bin/python factory/scripts/sip_netbars.py --self-test
  .venv/bin/python factory/scripts/sip_netbars.py --day 2021-02-01
  .venv/bin/python factory/scripts/sip_netbars.py --days 2021-02-01 2025-09-09 --force
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
import sip_bars as sb  # noqa: E402

DATA = ROOT / "data"
NET = DATA / "sip" / "net"
BARS = NET / "bars"
COV = NET / "coverage"
MIN_TRADES = 200
MIN_DERIVED_BARS = 30
COVERAGE_RATIO = 0.95


def classify_symbol(n_trades: int, d_bars: int, p_bars, last_et) -> tuple:
    """Return (class, reason) for one symbol-day. Deterministic, documented rule."""
    if n_trades >= MIN_TRADES and d_bars >= MIN_DERIVED_BARS:
        if p_bars is None:
            if last_et is not None and last_et >= 950:
                return "healthy_raw", "raw>=min, no provider reference"
            return "unresolved", "raw>=min but session truncated and no provider reference"
        if p_bars > 0 and d_bars >= COVERAGE_RATIO * p_bars:
            return "healthy_raw", "raw coverage >= 95% of provider bars"
        return ("provider_only", f"derived {d_bars} < 95% of provider {p_bars}") \
            if p_bars > 0 else ("unresolved", "no provider bars")
    if p_bars is not None and p_bars > 0:
        return "provider_only", f"raw insufficient (n_trades={n_trades})"
    return "unresolved", f"no usable source (n_trades={n_trades}, d_bars={d_bars})"


def _sha256(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _env():
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=ROOT / ".env")


def fetch_provider_bars(day: str, symbols: list) -> pl.DataFrame:
    """Alpaca SIP 1-min bars for the net symbols (raw/unadjusted)."""
    time.sleep(0.2)
    from datetime import datetime, timezone
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import Adjustment, DataFeed

    _env()
    client = StockHistoricalDataClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])
    start = datetime.fromisoformat(day + "T00:00:00+00:00")
    end = start.replace(hour=23, minute=59)
    rows = []
    bad = []
    todo = [s for s in symbols if s]
    for attempt in range(4):
        if not todo:
            break
        try:
            res = client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=todo, timeframe=TimeFrame.Minute, start=start, end=end,
                feed=DataFeed.SIP, adjustment=Adjustment.RAW,
            ))
            for sym, bars in res.data.items():
                for b in bars:
                    rows.append((sym, b.timestamp, b.open, b.high, b.low, b.close, b.volume))
            break
        except Exception as e:  # invalid symbol kills the whole batch
            msg = str(e)
            if "invalid symbol" in msg.lower():
                bad_sym = msg.split(":")[-1].strip().strip("'\"")
                need = [s for s in todo if s.strip() == bad_sym.strip()]
                if not need:
                    raise
                bad.extend(need)
                todo = [s for s in todo if s not in need]
                continue
            raise
    if not rows:
        return pl.DataFrame(schema={"symbol": pl.String, "timestamp": pl.Datetime,
                                    "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
                                    "close": pl.Float64, "volume": pl.Int64})
    df = pl.DataFrame(rows, schema=["symbol", "timestamp", "open", "high", "low", "close", "volume"],
                      orient="row")
    if bad:
        print(f"  provider fetch: dropped invalid symbols {bad}")
    return df


def rth(frame: pl.DataFrame, ts_col: str) -> pl.DataFrame:
    """Filter 570<=et<960 and add et_min (ET minute) + date."""
    out = frame.with_columns(pl.col(ts_col).dt.convert_time_zone("America/New_York").alias("_tset"))
    out = out.with_columns(
        (pl.col("_tset").dt.hour().cast(pl.Int32) * 60 + pl.col("_tset").dt.minute().cast(pl.Int32)).alias("et_min"),
        pl.col("_tset").dt.date().alias("date"),
    )
    return out.filter((pl.col("et_min") >= 570) & (pl.col("et_min") < 960)).drop("_tset")


def build_day(day: str, force: bool = False) -> dict:
    bar_p = BARS / f"{day}.parquet"
    man_p = COV / f"{day}.manifest.json"
    if man_p.exists() and not force:
        try:
            if json.load(open(man_p)).get("status") == "ok" and bar_p.exists():
                return {"day": day, "status": "skip"}
        except Exception:
            pass

    t0 = time.time()
    trades_p = NET / "trades" / f"{day}.parquet"
    if not trades_p.exists():
        return {"day": day, "status": "no_trades"}
    tdf = pl.read_parquet(trades_p)
    symbols = sorted(tdf["symbol"].unique().to_list())
    derived_all = sb.build_bars(tdf, policy="alpaca")
    d_rth = derived_all.filter(pl.col("sess") == "rth").select([
        pl.lit(day).str.to_date().alias("date"), "symbol", "et_min",
        pl.col("o").alias("open"), pl.col("h").alias("high"), pl.col("l").alias("low"),
        pl.col("c").alias("close"), pl.col("v").alias("volume"),
    ])

    n_trades = tdf.group_by("symbol").len().rename({"len": "n"}).to_dicts()
    n_trades = {r["symbol"]: int(r["n"]) for r in n_trades}
    prov = fetch_provider_bars(day, symbols)
    if prov.height:
        p_rth = rth(prov, "timestamp").select(
            ["date", "symbol", "et_min", "open", "high", "low", "close", "volume"])
    else:
        p_rth = prov

    d_by = {s: g for s, g in d_rth.group_by("symbol")} if d_rth.height else {}
    p_by = {s: g for s, g in p_rth.group_by("symbol")} if p_rth.height else {}
    # polars group_by iteration: keys may be tuples
    d_by = {k[0] if isinstance(k, tuple) else k: v for k, v in d_by.items()}
    p_by = {k[0] if isinstance(k, tuple) else k: v for k, v in p_by.items()}

    per_symbol = {}
    merged = []
    for s in symbols:
        dg = d_by.get(s)
        pg = p_by.get(s)
        d_bars = dg.height if dg is not None else 0
        p_bars = pg.height if pg is not None else 0
        last_et = int(dg["et_min"].max()) if dg is not None and d_bars else None
        cls, reason = classify_symbol(n_trades.get(s, 0), d_bars, p_bars if p_bars else None, last_et)
        per_symbol[s] = {"cls": cls, "n_trades": n_trades.get(s, 0), "derived_bars": d_bars,
                         "provider_bars": p_bars, "last_et": last_et, "reason": reason}
        if cls == "healthy_raw" and dg is not None:
            merged.append(dg.with_columns(pl.lit("derived").alias("src")))
        elif cls == "provider_only" and pg is not None:
            merged.append(pg.with_columns(pl.lit("provider").alias("src")))
    if merged:
        frame = pl.concat(merged, how="vertical").rename({"symbol": "ticker", "et_min": "et"})
    else:
        frame = pl.DataFrame(schema={"date": pl.Date, "ticker": pl.String, "et": pl.Int32,
                                     "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
                                     "close": pl.Float64, "volume": pl.Int64, "src": pl.String})

    BARS.mkdir(parents=True, exist_ok=True)
    COV.mkdir(parents=True, exist_ok=True)
    tmp = bar_p.with_suffix(".parquet.tmp")
    frame.write_parquet(tmp)
    os.replace(tmp, bar_p)
    counts = {c: sum(1 for v in per_symbol.values() if v["cls"] == c)
              for c in ("healthy_raw", "provider_only", "unresolved")}
    cov = {"day": day, "symbols": len(symbols), "classes": counts, "per_symbol": per_symbol}
    cj = COV / f"{day}.json"
    tmpj = cj.with_suffix(".json.tmp")
    with open(tmpj, "w") as fh:
        json.dump(cov, fh, indent=1, default=str)
    os.replace(tmpj, cj)
    man = {"day": day, "status": "ok", "symbols": len(symbols), "rows_merged": frame.height,
           "classes": counts, "elapsed_s": round(time.time() - t0, 1),
           "source_trades": str(trades_p), "src_sha256": _sha256(trades_p)}
    tmpm = man_p.with_suffix(".json.tmp")
    with open(tmpm, "w") as fh:
        json.dump(man, fh, indent=1, default=str)
    os.replace(tmpm, man_p)
    return {"day": day, "status": "ok", **{k: man[k] for k in ("symbols", "rows_merged", "classes", "elapsed_s")}}


def selftest():
    assert classify_symbol(500, 390, 390, 959)[0] == "healthy_raw"
    assert classify_symbol(50, 20, 390, 959)[0] == "provider_only"
    assert classify_symbol(500, 300, 390, 959)[0] == "provider_only"
    assert classify_symbol(0, 0, 0, None)[0] == "unresolved"
    assert classify_symbol(500, 200, None, 959)[0] == "healthy_raw"
    assert classify_symbol(500, 200, None, 800)[0] == "unresolved"
    assert classify_symbol(10, 0, None, None)[0] == "unresolved"
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--day")
    ap.add_argument("--days", nargs="*", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    days = []
    if args.day:
        days.append(args.day)
    if args.days:
        days.extend(args.days)
    if not days:
        ap.error("--day or --days required")
    for d in days:
        r = build_day(d, force=args.force)
        print(r)


if __name__ == "__main__":
    main()
