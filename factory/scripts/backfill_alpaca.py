"""Backfill US-equity RTH minute bars from Alpaca SIP into the HF OHLCV-1m parquet format.

Output schema matches data/ohlcv_{YYYY-MM}.parquet exactly so clean_month.py works unchanged:
timestamp (ns UTC), open/high/low/close/volume (f64), ticker (str).
Includes pre/post bars (clean_month filters RTH). All statuses included (captures
tickers delisted mid-window, avoiding survivorship).

Usage:
  uv run --no-project --with alpaca-py --with python-dotenv python factory/scripts/backfill_alpaca.py --months 2026-03
"""
from __future__ import annotations
import argparse
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv
import polars as pl

load_dotenv()


def fetch_chunk(client, symbols: list[str], start: datetime, end: datetime) -> pl.DataFrame:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed

    req = StockBarsRequest(symbol_or_symbols=symbols, timeframe=TimeFrame.Minute,
                           start=start, end=end, feed=DataFeed.SIP)
    for attempt in range(5):
        try:
            df = client.get_stock_bars(req).df
            if len(df) == 0:
                return pl.DataFrame(schema={"timestamp": pl.Datetime("ns", "UTC"),
                                            "open": pl.Float64, "high": pl.Float64,
                                            "low": pl.Float64, "close": pl.Float64,
                                            "volume": pl.Float64, "ticker": pl.String})
            df = df.reset_index()
            out = pl.from_pandas(df[["timestamp", "open", "high", "low", "close",
                                     "volume", "symbol"]])
            return out.rename({"symbol": "ticker"}).with_columns(
                pl.col("timestamp").cast(pl.Datetime("ns", "UTC")))
        except Exception as e:
            msg = str(e)
            wait = 15 * (attempt + 1)
            print(f"    retry in {wait}s: {msg[:120]}", flush=True)
            time.sleep(wait)
    raise SystemExit(f"failed chunk {start.date()}..{end.date()} after retries")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="+", required=True)
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--batch", type=int, default=100)
    args = ap.parse_args()

    from alpaca.trading.client import TradingClient
    from alpaca.data.historical import StockHistoricalDataClient

    tc = TradingClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], paper=True)
    assets = tc.get_all_assets()
    us = [a for a in assets if a.asset_class == "us_equity"
          and a.exchange in ("NYSE", "NASDAQ", "AMEX", "ARCA")
          and a.symbol.replace(".", "").replace("-", "").isalpha()
          and a.symbol.replace(".", "").replace("-", "").isupper()]
    symbols = sorted({a.symbol for a in us})
    print(f"universe: {len(symbols)} US primary symbols (all statuses)", flush=True)

    hc = StockHistoricalDataClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])

    for month in args.months:
        y, m = map(int, month.split("-"))
        out = args.out / f"ohlcv_{month}.parquet"
        if out.exists():
            print(f"exists: {out}", flush=True)
            continue
        m_start = datetime(y, m, 1, tzinfo=timezone.utc)
        m_end = datetime(y + (m == 12), (m % 12) + 1, 1, tzinfo=timezone.utc) - timedelta(seconds=1)
        # 10-day UTC chunks (RTH filtered later by clean_month)
        chunks = []
        cur = m_start
        while cur <= m_end:
            chunks.append((cur, min(cur + timedelta(days=9, hours=23, minutes=59, seconds=59), m_end)))
            cur += timedelta(days=10)
        parts = []
        for ci, (cs, ce) in enumerate(chunks):
            for bi in range(0, len(symbols), args.batch):
                batch = symbols[bi:bi + args.batch]
                t0 = time.time()
                df = fetch_chunk(hc, batch, cs, ce)
                parts.append(df)
                print(f"  [{month}] chunk {ci + 1}/{len(chunks)} batch {bi // args.batch + 1}"
                      f"/{(len(symbols) + args.batch - 1) // args.batch}: {df.height:,} rows "
                      f"({time.time() - t0:.1f}s)", flush=True)
        full = pl.concat(parts, how="vertical") \
            .unique(subset=["timestamp", "ticker"], keep="first") \
            .sort("timestamp", "ticker")
        full.write_parquet(out)
        print(f"[{month}] wrote {out}: {full.height:,} rows, "
              f"{out.stat().st_size / 1e6:.0f} MB", flush=True)


if __name__ == "__main__":
    main()
