"""Empirical feed fingerprint: does the research parquet carry consolidated (SIP-grade)
volume or IEX-only volume?

Method: compare dataset daily volume for mega-caps against known consolidated volumes
(NVDA ~150-250M sh/day, TSLA ~80-120M, AAPL ~40-70M, SPY ~45-80M in this era).
IEX-only data shows ~2-3% of consolidated volume. Also: bars/day per ticker and
universe size per month (sanity: full US equities incl. OTC vs listed-only).
"""
from pathlib import Path
import polars as pl

DATA = Path("data")
MONTHS = ["2025-08", "2026-01", "2026-03"]
FOCUS = ["NVDA", "TSLA", "AAPL", "AMD", "PLTR", "SOFI", "SPY", "QQQ", "INTC"]

for m in MONTHS:
    p = DATA / f"ohlcv_{m}.parquet"
    df = pl.read_parquet(p)
    df = df.with_columns(pl.col("timestamp").dt.convert_time_zone("America/New_York"))
    date = df.select(pl.col("timestamp").dt.date().alias("d"))["d"].unique().sort()
    # pick the 3rd trading day of the month for stability
    day = date[2]
    dd = df.filter(pl.col("timestamp").dt.date() == day)
    n_tickers = dd["ticker"].n_unique()
    n_rows = dd.height
    print(f"\n=== {m}  (day={day}) raw rows={len(df):,}  tickers={df['ticker'].n_unique():,} ===")
    print(f"  day {day}: tickers={n_tickers:,} rows={n_rows:,} avg bars/ticker={n_rows/n_tickers:.1f}")
    g = (dd.group_by("ticker")
           .agg(pl.col("volume").sum().alias("vol"),
                pl.col("close").last().alias("last"),
                pl.len().alias("bars"))
           .with_columns((pl.col("vol") * pl.col("last")).alias("dollar_vol")))
    f = g.filter(pl.col("ticker").is_in(FOCUS)).sort("ticker")
    for r in f.iter_rows(named=True):
        print(f"  {r['ticker']:>5}: vol={r['vol']:>12,.0f}  last=${r['last']:>8.2f}  $vol=${r['dollar_vol']/1e6:>8.1f}M  bars={r['bars']}")
    # how many tickers have >=300 bars that day (full session) -> exchange coverage signal
    full = g.filter(pl.col("bars") >= 300).height
    print(f"  tickers with >=300 bars: {full:,} ({full/n_tickers:.1%})")
    med_dv = g["dollar_vol"].median()
    print(f"  median ticker $vol: ${med_dv/1e3:,.0f}K")
