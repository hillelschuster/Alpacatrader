"""Archetype discovery + composite gradient + execution-cost buckets on OOS Aug-Dec.

1) Conditional expectancy tables (net @20bps RT, executable t1-entry) by key features.
2) SHAP attribution (LightGBM pred_contrib) — global and contrast (composite members vs all).
3) KMeans k=5 archetypes on composite-stream members; profile + expectancy per cluster.
4) Execution buckets: price x liquidity x cost curve for the composite stream.

Descriptive/exploratory (OOS = post-peek): hypothesis-generating, frozen-test candidates only.
Usage:
  uv run --no-project --with polars --with numpy --with lightgbm --with scikit-learn --with tzdata \
    python factory/scripts/archetypes.py
"""
import pickle
import numpy as np
import polars as pl
from sklearn.cluster import KMeans

FEATS = ["pct_gain_grid", "rank", "n_hod_breaks", "dip_5m", "trap_reclaim", "dip_depth_5m",
         "vwap_dist", "above_vwap", "dist_open", "open_gap", "dist_hod", "range_pos",
         "log_close", "log_dollar_volume", "dv_5m_rate", "dv_accel", "rvol", "excess_gain",
         "market_ret_5m", "tod_min", "dow",
         "ret_1m", "ret_3m", "ret_5m", "ret_10m", "ret_15m", "ret_30m",
         "realized_vol_15m", "efficiency_30m", "n_up_bars_15"]
COST = 0.002
OOS = ["2025-08", "2025-09", "2025-10", "2025-11", "2025-12"]
THETA, THETA_HI = 0.00115, 0.00340


def table(df, col, bins, labels, cost=COST):
    d = df.with_columns(pl.col(col).cut(bins, labels=labels).alias("b"))
    out = (d.group_by("b").agg(pl.len().alias("n"),
                               (pl.col("fwd60_t1entry") - cost).mean().alias("net"),
                               ((pl.col("fwd60_t1entry") - cost) > 0).mean().alias("wr"),
                               pl.col("score").mean().alias("mean_score"))
           .sort("b"))
    return out


def show(title, t):
    print(f"\n--- {title} ---")
    print(f"{'bin':<14} {'n':>7} {'net':>9} {'wr':>6} {'score':>8}")
    for x in t.to_dicts():
        b = str(x["b"])
        print(f"{b:<14} {x['n']:>7} {x['net']:>+9.3%} {x['wr']:>6.3f} {x['mean_score']:>8.4f}")


def main():
    model = pickle.load(open("factory/artifacts/ml/model_v1.pkl", "rb"))
    dfs = []
    for m in OOS:
        df = pl.read_parquet(f"data/ml_features/features_{m}.parquet")
        df = df.with_columns(pl.col("cum_dv").log1p().alias("log_dollar_volume"))
        X = df.select(FEATS).to_numpy().astype(np.float32)
        df = df.with_columns(pl.Series("score", model.predict(X, num_iteration=model.best_iteration)))
        dfs.append(df)
    df = pl.concat(dfs).filter(pl.col("score").is_not_null() & pl.col("fwd60_t1entry").is_not_null())
    df = df.sort(["et_date", "tod_min"]).with_columns(
        pl.col("score").rank("ordinal", descending=True).over(["et_date", "tod_min"]).alias("vis_rank"))
    m3 = df.filter((pl.col("vis_rank") <= 2) & (pl.col("score") >= THETA))
    comp = m3.filter((pl.col("rvol") > 4) & (pl.col("vwap_dist") > 0.03) & (pl.col("tod_min") < 270))

    # ---------- 1. conditional expectancy tables (all OOS events) ----------
    show("net by rvol (all OOS)", table(df, "rvol", [1.5, 3, 5, 8], ["<1.5", "1.5-3", "3-5", "5-8", ">8"]))
    show("net by vwap_dist (all OOS)", table(df, "vwap_dist", [0, 0.02, 0.05, 0.10], ["<0", "0-2%", "2-5%", "5-10%", ">10%"]))
    show("net by ret_15m (all OOS)", table(df, "ret_15m", [0, 0.03, 0.08, 0.15], ["<0", "0-3%", "3-8%", "8-15%", ">15%"]))
    show("net by open_gap (all OOS)", table(df, "open_gap", [0.02, 0.05, 0.15, 0.30], ["<2%", "2-5%", "5-15%", "15-30%", ">30%"]))
    show("net by tod (all OOS)", table(df, "tod_min", [120, 240, 300], ["<2h", "2-4h", "4-5h", ">5h"]))
    show("net by realized_vol_15m (all OOS)", table(df, "realized_vol_15m", [0.01, 0.02, 0.04], ["<1%", "1-2%", "2-4%", ">4%"]))
    show("net by dv_accel (all OOS)", table(df, "dv_accel", [1, 2, 4], ["<1x", "1-2x", "2-4x", ">4x"]))
    show("net by price (all OOS)", table(df, "close", [5, 10, 20], ["<5", "5-10", "10-20", ">20"]))

    # 2D: rvol x vwap_dist on M3-admitted stream (the live candidate pool)
    d = m3.with_columns(pl.col("rvol").cut([4, 8], labels=["r2-4", "r4-8", "r>8"]).alias("rb"),
                        pl.col("vwap_dist").cut([0.03, 0.08], labels=["v0-3%", "v3-8%", "v>8%"]).alias("vb"))
    t2 = (d.group_by(["rb", "vb"]).agg(pl.len().alias("n"), (pl.col("fwd60_t1entry") - COST).mean().alias("net"),
                                       ((pl.col("fwd60_t1entry") - COST) > 0).mean().alias("wr"))
          .sort(["rb", "vb"]))
    print("\n--- M3 stream: rvol x vwap_dist (net @20bps) ---")
    for x in t2.to_dicts():
        print(f"{x['rb']} x {x['vb']}: n={x['n']:>5} net={x['net']:+.3%} wr={x['wr']:.3f}")

    # ---------- 2. SHAP ----------
    samp = df.sample(min(60000, df.height), seed=7)
    sv = model.predict(samp.select(FEATS).to_numpy().astype(np.float32),
                       num_iteration=model.best_iteration, pred_contrib=True)
    contrib = sv[:, :-1]  # last col = expected value
    comp_mask = ((samp["vis_rank"].to_numpy() <= 2) & (samp["score"].to_numpy() >= THETA) &
                 (samp["rvol"].to_numpy() > 4) & (samp["vwap_dist"].to_numpy() > 0.03) &
                 (samp["tod_min"].to_numpy() < 270))
    print("\n--- SHAP: mean signed contribution (composite members vs all) ---")
    print(f"{'feature':<18} {'|shap| all':>10} {'shap all':>9} {'shap comp':>10}")
    order = np.argsort(-np.abs(contrib).mean(0))
    for i in order[:15]:
        print(f"{FEATS[i]:<18} {np.abs(contrib[:, i]).mean():>10.4f} {contrib[:, i].mean():>+9.4f} "
              f"{contrib[comp_mask, i].mean():>+10.4f}")


    # composite-internal gradients: does vwap_dist keep paying? which tod?
    for col, bins, labels in [("vwap_dist", [0.05, 0.08, 0.12], ["3-5%", "5-8%", "8-12%", ">12%"]),
                              ("tod_min", [120, 210], ["<2h", "2-3.5h", "3.5-4.5h"])]:
        d = comp.with_columns(pl.col(col).cut(bins, labels=labels).alias("b"))
        print(f"composite stream: net by {col} ---")
        for x in (d.group_by("b").agg(pl.len().alias("n"), (pl.col("fwd60_t1entry") - COST).mean().alias("net"),
                                       ((pl.col("fwd60_t1entry") - COST) > 0).mean().alias("wr"))
                  .sort("b").to_dicts()):
            print(f"{x['b']}: n={x['n']:>5} net={x['net']:+.3%} wr={x['wr']:.3f}")

    # ---------- 3. KMeans archetypes on composite stream ----------
    key = ["ret_5m", "ret_15m", "ret_30m", "open_gap", "pct_gain_grid", "vwap_dist", "rvol",
           "realized_vol_15m", "tod_min", "log_close", "dv_accel", "dist_hod", "efficiency_30m"]
    C = comp.select(key).to_numpy().astype(np.float64)
    mu = np.nanmean(C, axis=0)
    sd = np.nanstd(C, axis=0) + 1e-9
    Z = np.nan_to_num((C - mu) / sd, nan=0.0)  # per-feature mean-impute after z-scoring
    print("")
    km = KMeans(n_clusters=5, n_init=10, random_state=7).fit(Z)
    comp2 = comp.with_columns(pl.Series("cl", km.labels_))
    prof = (comp2.group_by("cl").agg(pl.len().alias("n"), (pl.col("fwd60_t1entry") - COST).mean().alias("net"),
                                     ((pl.col("fwd60_t1entry") - COST) > 0).mean().alias("wr"),
                                     *[pl.col(c).mean().alias(c) for c in key])
            .sort("cl"))
    print("\n--- KMeans k=5 archetypes within composite stream ---")
    cols = ["n", "net", "wr", "ret_15m", "open_gap", "vwap_dist", "rvol", "realized_vol_15m",
            "tod_min", "pct_gain_grid", "dv_accel", "dist_hod"]
    hdr = "cl " + " ".join(f"{c[:8]:>8}" for c in cols)
    print(hdr)
    for x in prof.to_dicts():
        print(f"{x['cl']:>2} " + " ".join(
            (f"{x[c]:>8.3%}" if c in ("net", "wr", "ret_15m", "open_gap", "vwap_dist",
                                      "realized_vol_15m", "dist_hod") else f"{x[c]:>8.1f}") for c in cols))

    # ---------- 4. execution buckets on composite stream ----------
    print("\n--- composite stream: cost survival by price bucket ---")
    d = comp.with_columns(pl.col("close").cut([5, 10, 20], labels=["<5", "5-10", "10-20", ">20"]).alias("pb"))
    for x in (d.group_by("pb").agg(pl.len().alias("n"), pl.col("fwd60_t1entry").mean().alias("gross"))
              .sort("pb").to_dicts()):
        g = x["gross"]
        print(f"price {x['pb']}: n={x['n']:>5} gross={g:+.3%} "
              f"net20={g-0.002:+.3%} net40={g-0.004:+.3%} net60={g-0.006:+.3%} net100={g-0.010:+.3%}")
    print("\n--- composite stream: cost survival by cum_dv bucket (liquidity) ---")
    d = comp.with_columns(pl.col("cum_dv").cut([5e6, 2e7, 1e8], labels=["<5M", "5-20M", "20-100M", ">100M"]).alias("lb"))
    for x in (d.group_by("lb").agg(pl.len().alias("n"), pl.col("fwd60_t1entry").mean().alias("gross"))
              .sort("lb").to_dicts()):
        g = x["gross"]
        print(f"cumdv {x['lb']}: n={x['n']:>5} gross={g:+.3%} "
              f"net20={g-0.002:+.3%} net40={g-0.004:+.3%} net60={g-0.006:+.3%} net100={g-0.010:+.3%}")


if __name__ == "__main__":
    main()
