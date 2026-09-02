"""Validate Alpaca-SIP backfill against the HF ground truth month (bar level).

Usage: uv run --no-project --with polars --with tzdata python factory/scripts/validate_backfill.py
"""
import polars as pl
import numpy as np

RAW_HF = "data/ohlcv_2026-03.parquet"
RAW_ALP = "data/backfill/ohlcv_2026-03.parquet"

hf = pl.read_parquet(RAW_HF).with_columns(
    pl.col("timestamp").dt.convert_time_zone("America/New_York").dt.hour().cast(pl.Int32) * 60
    + pl.col("timestamp").dt.convert_time_zone("America/New_York").dt.minute().cast(pl.Int32).alias("_m"))
hf = hf.with_columns(pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("et"))
hf = hf.filter((pl.col("et").dt.hour() + pl.col("et").dt.minute() / 60.0 >= 9.5)
               & (pl.col("et").dt.hour() + pl.col("et").dt.minute() / 60.0 < 16.0))
alp = pl.read_parquet(RAW_ALP).with_columns(
    pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("et"))
alp = alp.filter((pl.col("et").dt.hour() + pl.col("et").dt.minute() / 60.0 >= 9.5)
                 & (pl.col("et").dt.hour() + pl.col("et").dt.minute() / 60.0 < 16.0))
print(f"RTH rows: hf={hf.height:,}  alpaca={alp.height:,}")
print(f"tickers:  hf={hf['ticker'].n_unique():,}  alpaca={alp['ticker'].n_unique():,}")

# RTH-only bar-level join
hf2 = hf.select("timestamp", "ticker", "open", "high", "low", "close", "volume").unique(["timestamp", "ticker"])
alp2 = alp.select("timestamp", "ticker", "open", "high", "low", "close", "volume").unique(["timestamp", "ticker"])
j = hf2.join(alp2, on=["timestamp", "ticker"], how="inner", suffix="_a")
inter = j.height
union = hf2.height + alp2.height - inter
print(f"\nbar join: inner={inter:,}  jaccard={inter / union:.3f}  (hf-only={hf2.height - inter:,}, alp-only={alp2.height - inter:,})")
dc = (j["close"] - j["close_a"]).abs() / j["close"]
dv = (j["volume"] - j["volume_a"]).abs() / j["volume"].clip(1)
print(f"|dclose|/close: median={dc.median():.2e} p99={dc.quantile(0.99):.2e} share<1e-6={(dc < 1e-6).mean():.3f}")
print(f"|dvol|/vol:     median={dv.median():.2e} p99={dv.quantile(0.99):.2e} share<1e-6={(dv < 1e-6).mean():.3f}")
