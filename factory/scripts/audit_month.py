"""Audit one month of mito0o852/OHLCV-1m data quality."""

import argparse
from pathlib import Path
import polars as pl


def audit(filepath: Path, sample_tickers: int = 10):
    print(f"=== AUDIT: {filepath.name} ({filepath.stat().st_size / 1e6:.1f} MB) ===\n")
    df = pl.read_parquet(filepath)

    # 1. BASIC COUNTS
    print(f"Rows:             {df.height:>14,}")
    print(f"Unique tickers:   {df['ticker'].n_unique():>14,}")
    print(f"Date range:       {df['timestamp'].min()} → {df['timestamp'].max()}")
    print()

    # 2. TIMESTAMPS — UTC vs ET mapping
    print("--- TIMESTAMPS ---")
    ts = df["timestamp"].dt
    print(f"  dtype:        {df['timestamp'].dtype}")
    print(f"  timezone:     {df['timestamp'].dtype}")
    print(f"  Hour range (UTC):  {ts.hour().min():>3} → {ts.hour().max()}")
    # Convert sample to ET
    et = df["timestamp"].dt.convert_time_zone("America/New_York")
    print(f"  Hour range (ET):  {et.dt.hour().min():>4} → {et.dt.hour().max()}")
    print(f"  Unique dates:      {et.dt.date().n_unique():>5}")

    # RTH bar count (09:30-16:00 ET)
    rth_mask = (et.dt.hour() + et.dt.minute() / 60.0 >= 9.5) & (
        et.dt.hour() + et.dt.minute() / 60.0 < 16.0
    )
    rth_count = rth_mask.sum()
    print(f"  RTH bars:          {rth_count:>7,}  ({100 * rth_count / df.height:.1f}%)")
    print(f"  Pre-market bars:   {(et.dt.hour() + et.dt.minute() / 60.0 < 9.5).sum():>7,}")
    print(f"  Post-market bars:  {(et.dt.hour() + et.dt.minute() / 60.0 >= 16.0).sum():>7,}")

    # Weekend check
    dow_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
    dow_counts = (
        df.with_columns(et.dt.weekday().alias("dow"))
        .group_by("dow").len().sort("dow")
    )
    dow_parts = []
    for r in dow_counts.iter_rows(named=True):
        name = dow_names.get(r["dow"], "?")
        dow_parts.append(f"{name}={r['len']:,}")
    print(f"  Day-of-week:       {', '.join(dow_parts)}")

    # Date gaps
    dates_sorted = sorted(et.dt.date().unique().to_list())
    if len(dates_sorted) > 1:
        from datetime import timedelta
        gaps = []
        for i in range(1, len(dates_sorted)):
            d0, d1 = dates_sorted[i - 1], dates_sorted[i]
            diff = (d1 - d0).days
            if diff > 3:
                gaps.append(f"{d0} → {d1} ({diff}d gap)")
        if gaps:
            print(f"  Large date gaps:   {', '.join(gaps)}")
        else:
            print(f"  Large date gaps:   none")
    print()

    # 3. PRICE QUALITY
    print("--- PRICES ---")
    for col in ["open", "high", "low", "close"]:
        s = df[col]
        print(
            f"  {col:<8} min={s.min():>10.4f}  max={s.max():>10.4f}  "
            f"mean={s.mean():>12.6f}  zeros={s.is_null().sum():>5}  nulls={s.is_null().sum():>5}"
        )
    # Sub-$1 exposure
    sub1 = df.filter(pl.col("close") < 1.0)
    sub1_tickers = sub1["ticker"].n_unique()
    print(f"  Sub-$1 close bars: {sub1.height:>7,} ({100 * sub1.height / df.height:.1f}%)")
    print(f"  Sub-$1 tickers:    {sub1_tickers:>7,}")
    print()

    # 4. VOLUME QUALITY
    print("--- VOLUME ---")
    v = df["volume"]
    print(f"  min={v.min():>10.0f}  max={v.max():>10.0f}  mean={v.mean():>12.1f}  median={v.median():>12.1f}")
    zero_vol = df.filter(pl.col("volume") == 0)
    print(f"  Zero-volume bars:  {zero_vol.height:>7,}")
    low_vol = df.filter(pl.col("volume") < 100)
    print(f"  Volume < 100:      {low_vol.height:>7,} ({100 * low_vol.height / df.height:.2f}%)")
    print()

    # 5. DUPLICATES
    print("--- DUPLICATES ---")
    dup_count = df.height - df.unique(subset=["timestamp", "ticker"]).height
    print(f"  Dup (ts, ticker):  {dup_count:>7,}  ({100 * dup_count / df.height:.2f}%)")
    if dup_count > 0:
        dup_rows = df.filter(df.is_duplicated())
        print(f"  Duplicate rows:    {dup_rows.height:>7,}")
    print()

    # 6. TICKER UNIVERSE
    print("--- TICKERS ---")
    # Top tickers by row count
    ticker_counts = df.group_by("ticker").len().sort("len", descending=True).head(sample_tickers)
    print(f"  Top {sample_tickers} by bar count:")
    for r in ticker_counts.iter_rows(named=True):
        print(f"    {r['ticker']:<8} {r['len']:>8,}")
    # Tickers by row count distribution
    counts = ticker_counts["len"]
    # Full distribution via describe
    full_counts = df.group_by("ticker").len()["len"]
    desc = full_counts.describe()
    print(f"  Bars-per-ticker:   min={desc.filter(pl.col('statistic')=='min')['value'][0]:>6.0f}  "
          f"p25={desc.filter(pl.col('statistic')=='25%')['value'][0]:>6.0f}  "
          f"median={desc.filter(pl.col('statistic')=='50%')['value'][0]:>6.0f}  "
          f"p75={desc.filter(pl.col('statistic')=='75%')['value'][0]:>6.0f}  "
          f"max={desc.filter(pl.col('statistic')=='max')['value'][0]:>6.0f}")
    # Tickers with < 100 bars (likely dead/near-halted)
    sparse = full_counts.to_frame().filter(pl.col("len") < 100)
    print(f"  Tickers <100 bars: {sparse.height:>7,}")
    print()

    # 7. KNOWN SPLIT CHECK — quick scan for suspicious discontinuities
    print("--- SPLIT CHECK (quick scan) ---")
    # Sample top 5 tickers by volume for split signature
    top_vol = (
        df.group_by("ticker").agg(pl.col("volume").sum().alias("total_vol"))
        .top_k(5, by="total_vol")
        .get_column("ticker")
        .to_list()
    )
    for ticker in top_vol:
        tdf = df.filter(pl.col("ticker") == ticker).sort("timestamp")
        if tdf.height < 2:
            continue
        close_diff = tdf["close"].diff().abs()
        # Flag any single-bar move > 20% (potential raw split signature)
        big_jumps = close_diff.filter(
            (close_diff / tdf["close"].shift(1) > 0.20) & tdf["close"].shift(1).is_not_null()
        )
        if big_jumps.len():
            print(f"  {ticker:<8} {big_jumps.len():>4} bar-to-bar jumps >20% — SUSPICIOUS")
    print()

    # 8. SUMMARY
    print("=== SUMMARY ===")
    print(f"  File:   {filepath.name}")
    print(f"  Rows:   {df.height:,}")
    print(f"  Tickers:{df['ticker'].n_unique():,}")
    print(f"  RTH%:   {100 * rth_count / df.height:.1f}%")
    print(f"  Sub-$1: {100 * sub1.height / df.height:.1f}% of bars / {sub1_tickers} tickers")
    print(f"  Dups:   {dup_count:,} ({100 * dup_count / df.height:.2f}%)")
    print(f"  Memory: {df.estimated_size() / 1e6:.1f} MB")

    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--sample", type=int, default=10)
    args = parser.parse_args()

    if not args.file.exists():
        print(f"File not found: {args.file}")
        raise SystemExit(1)

    audit(args.file, args.sample)