"""Composite ML rule — nested selection + frozen validation (reviewer-response version).

Rule: model top-decile (day-rank score >= 0.9*n) & rvol > R & vwap_dist > V & tod < T.
Thresholds (R,V,T) argmaxed on --select months ONLY, then applied frozen to --val months.

Provenance note (post adversarial review): the grid ranges were designed after a
descriptive slice (rv>3 & vwap+2% & tod<240) had been VIEWED on all OOS months
including the validation months (phase6_joint.log). Threshold-conditional selection
is controlled here, but the design-space prior is NOT pristine. Treat validation
numbers as partially validated, not clean re-OOS. Definitive test = future months.

Usage:
  uv run --no-project --with polars --with numpy --with lightgbm --with tzdata \
    python factory/scripts/composite_ml.py --select 2025-08 2025-09 2025-10 --val 2025-11 2025-12
"""
import argparse, pickle
from pathlib import Path
import numpy as np
import polars as pl

FEATS = ["pct_gain_grid", "rank", "n_hod_breaks", "dip_5m", "trap_reclaim", "dip_depth_5m",
         "vwap_dist", "above_vwap", "dist_open", "open_gap", "dist_hod", "range_pos",
         "log_close", "log_dollar_volume", "dv_5m_rate", "dv_accel", "rvol", "excess_gain",
         "market_ret_5m", "tod_min", "dow",
         "ret_1m", "ret_3m", "ret_5m", "ret_10m", "ret_15m", "ret_30m",
         "realized_vol_15m", "efficiency_30m", "n_up_bars_15"]
GRID = {"rvol": [2.0, 3.0, 4.0], "vwap_dist": [0.01, 0.02, 0.03], "tod": [210, 240, 270]}
MIN_EVENTS = 3000


def load(months, model):
    dfs = []
    for m in months:
        df = pl.read_parquet(f"data/ml_features/features_{m}.parquet")
        df = df.with_columns(pl.col("cum_dv").log1p().alias("log_dollar_volume"))
        X = df.select(FEATS).to_numpy().astype(np.float32)
        df = df.with_columns(pl.Series("score", model.predict(X, num_iteration=model.best_iteration)))
        df = df.filter(pl.col("fwd_ret_60m").is_not_null() & pl.col("score").is_not_null())
        df = df.with_columns(pl.col("score").rank("ordinal").over("et_date").alias("_r"),
                             pl.len().over("et_date").alias("_n"))
        dfs.append(df.filter(pl.col("_r") >= 0.9 * pl.col("_n"))
                   .with_columns((pl.col("fwd_ret_60m") - 0.002).alias("net")))
    return pl.concat(dfs)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--select", nargs="+", required=True)
    p.add_argument("--val", nargs="+", required=True)
    p.add_argument("--model", type=Path, default=Path("factory/artifacts/ml/model_v1.pkl"))
    p.add_argument("--cost", type=float, default=0.002)
    a = p.parse_args()
    model = pickle.load(open(a.model, "rb"))
    sel = load(a.select, model)
    val = load(a.val, model)
    best = None
    for rv in GRID["rvol"]:
        for vw in GRID["vwap_dist"]:
            for tod in GRID["tod"]:
                c = sel.filter((pl.col("rvol") > rv) & (pl.col("vwap_dist") > vw) & (pl.col("tod_min") < tod))
                if c.height < MIN_EVENTS:
                    continue
                m = c["net"].mean()
                if best is None or m > best[0]:
                    best = (m, rv, vw, tod, c.height)
    m, rv, vw, tod, nsel = best
    print(f"SELECTED on {a.select}: rvol>{rv} & vwap_dist>{vw} & tod<{tod} "
          f"-> {m:+.4%} (n={nsel})")
    c = val.filter((pl.col("rvol") > rv) & (pl.col("vwap_dist") > vw) & (pl.col("tod_min") < tod))
    print(f"FROZEN {a.val}: n={c.height} net={c['net'].mean():+.4%} wr={(c['net'] > 0).mean():.3f}")
    print(f"REF D10-all {a.val}: net={val['net'].mean():+.4%} wr={(val['net'] > 0).mean():.3f} n={val.height}")


if __name__ == "__main__":
    main()
