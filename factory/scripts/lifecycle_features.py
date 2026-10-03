"""Past-only route memory derived from the full minute path, not endpoint snapshots.

All shifts/accumulators stay inside the fixed day-clock-name claim. No future label,
execution price or full-session/PM summary is read by this module.
"""
from __future__ import annotations
import polars as pl

KEY = ["day", "clock", "ticker"]
ROUTE_FEATURES = ["minutes_since_high", "minutes_since_low", "time_below_fill",
                  "reclaim_fill_now", "failed_fill_reclaim_now", "recovery_from_low",
                  "peak_retention", "dd_velocity5", "acceleration5", "pm_range_asof"]


def augment(df: pl.DataFrame) -> pl.DataFrame:
    d = df.sort(KEY + ["t"])
    valid = pl.col("filled_asof") & pl.col("ret_fill").is_finite()
    previous_peak = pl.col("peak_gain").shift(1).over(KEY)
    previous_low = pl.col("mae_sofar").shift(1).over(KEY)
    previous_ret = pl.col("ret_fill").shift(1).over(KEY)
    new_high = valid & (previous_peak.is_null() | previous_peak.is_nan()
                       | (pl.col("peak_gain") > previous_peak))
    new_low = valid & (previous_low.is_null() | previous_low.is_nan()
                      | (pl.col("mae_sofar") < previous_low))
    d = d.with_columns(
        pl.when(new_high).then(pl.col("t") - 1).otherwise(None).forward_fill().over(KEY).alias("_high_et"),
        pl.when(new_low).then(pl.col("t") - 1).otherwise(None).forward_fill().over(KEY).alias("_low_et"),
        pl.when(valid & (pl.col("ret_fill") < 0)).then(1).otherwise(0).cum_sum().over(KEY).alias("time_below_fill"),
        (valid & previous_ret.is_finite() & (previous_ret < 0) & (pl.col("ret_fill") >= 0)).alias("reclaim_fill_now"),
        (valid & previous_ret.is_finite() & (previous_ret >= 0) & (pl.col("ret_fill") < 0)).alias("failed_fill_reclaim_now"),
    )
    d = d.with_columns(
        (pl.col("t") - pl.col("_high_et")).alias("minutes_since_high"),
        (pl.col("t") - pl.col("_low_et")).alias("minutes_since_low"),
        ((1 + pl.col("ret_fill")) / (1 + pl.col("mae_sofar")) - 1).alias("recovery_from_low"),
        ((1 + pl.col("ret_fill")) / (1 + pl.col("peak_gain"))).alias("peak_retention"),
        (pl.col("dd_from_high") - pl.col("dd_from_high").shift(5).over(KEY)).alias("dd_velocity5"),
        (pl.col("ret5") - pl.col("ret5").shift(5).over(KEY)).alias("acceleration5"),
        (pl.col("pm_high") / pl.col("pm_low") - 1).alias("pm_range_asof"),
    )
    for feature in ROUTE_FEATURES:
        if d.schema[feature].is_numeric():
            d = d.with_columns(pl.when(valid & pl.col(feature).is_finite())
                               .then(pl.col(feature)).otherwise(None).alias(feature))
    return d.drop(["_high_et", "_low_et"])
