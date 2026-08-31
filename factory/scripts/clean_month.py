"""Clean one month of OHLCV-1m data for top-gainer research.

Steps:
  1. Load Parquet
  2. Convert timestamps to ET
  3. Filter to RTH (09:30-16:00 ET)
  4. Deduplicate by (timestamp, ticker) — keep first
  5. Price floor: close >= $2.00
  6. Minimum bar volume: >= 100
  7. (Optional) PIT universe join — NYSE/NASDAQ/AMEX only
  8. (Optional) Exclude known split-affected dates

Outputs: one cleaned Parquet per input month.
"""

import argparse
from pathlib import Path
import polars as pl


def clean_month(
    filepath: Path,
    outpath: Path,
    price_floor: float = 2.0,
    min_volume: int = 100,
    pit_dir: Path | None = None,
    split_file: Path | None = None,
) -> pl.DataFrame:
    print(f"Loading {filepath.name} ({filepath.stat().st_size / 1e6:.1f} MB) ...")
    df = pl.read_parquet(filepath)
    n0 = df.height
    print(f"  Raw rows: {n0:,}")

    # --- 1. Convert to Eastern Time, filter to RTH ---
    df = df.with_columns(
        pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("et")
    )
    et = df["et"]
    hour = et.dt.hour() + et.dt.minute() / 60.0
    df = df.filter((hour >= 9.5) & (hour < 16.0))
    n1 = df.height
    print(f"  After RTH filter: {n1:,} ({100 * n1 / n0:.1f}%)")

    # --- 2. Deduplicate (timestamp, ticker), keep first ---
    df = df.unique(subset=["timestamp", "ticker"], keep="first")
    n2 = df.height
    print(f"  After dedup: {n2:,} (removed {n1 - n2:,})")

    # --- 3. Price floor ---
    df = df.filter(pl.col("close") >= price_floor)
    n3 = df.height
    print(f"  After price >= ${price_floor:.0f}: {n3:,} ({100 * n3 / n2:.1f}%)")

    # --- 4. Minimum volume ---
    df = df.filter(pl.col("volume") >= min_volume)
    n4 = df.height
    print(f"  After volume >= {min_volume}: {n4:,} ({100 * n4 / n3:.1f}%)")

    # --- 5. PIT universe join (optional) ---
    if pit_dir:
        print(f"  PIT universe join: {pit_dir}")
        # TODO: load daily snapshots and left-join on (ticker, date)
        # For now, skip — will implement after downloading PIT data
        pass

    # --- 6. Split-date exclusion (optional) ---
    if split_file:
        print(f"  Split exclusion: {split_file}")
        # TODO: load split events, exclude ticker-date pairs within ±1 day
        pass

    n_final = df.height
    unique_tickers = df["ticker"].n_unique()
    print(f"\n  FINAL: {n_final:,} rows, {unique_tickers:,} tickers "
          f"({100 * n_final / n0:.1f}% of raw)")

    # Drop helper column before write
    if "et" in df.columns:
        df = df.drop("et")

    # Write
    outpath.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(outpath)
    size_mb = outpath.stat().st_size / 1e6
    print(f"  Written: {outpath} ({size_mb:.1f} MB)")

    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None,
                        help="Output path (default: data/clean_{filename})")
    parser.add_argument("--price-floor", type=float, default=2.0)
    parser.add_argument("--min-volume", type=int, default=100)
    parser.add_argument("--pit-dir", type=Path, default=None,
                        help="Directory with PIT universe daily snapshots")
    parser.add_argument("--split-file", type=Path, default=None,
                        help="Parquet/CSV of split events")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"File not found: {args.file}")
        raise SystemExit(1)

    out = args.out or args.file.parent / f"clean_{args.file.name}"
    clean_month(args.file, out, args.price_floor, args.min_volume,
                args.pit_dir, args.split_file)