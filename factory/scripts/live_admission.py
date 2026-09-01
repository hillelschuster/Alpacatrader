"""Live-causal admission logic — replace whole-day D10 rank with executable decision rules.

The research D10 ranks an event against ALL of its day's events (unknowable live).
Here: five causal admission rules, calibrated on May+Jun+Jul ONLY, evaluated frozen
on Aug-Dec. All scores come from model_v1.pkl; features are decision-time safe.

Methods (all causal at t):
  M1_fixed     score >= theta_fixed (90th pct of May-Jul scores)
  M2_rolling   score >= 90th pct of trailing 5-day score distribution (prior days only)
  M3_visrank   score-rank <= K within the CURRENT minute's visible top-gainer set
               (events sharing et_date+tod_min = the live top-20 list), AND >= theta_fixed
  M4_first     first minute per (ticker,et_date) with score >= theta_fixed
  M5_persist   score >= theta_fixed in >=2 of last 3 minutes (per episode)
Reference: whole-day D10 (non-executable upper-bound view).

Usage:
  uv run --no-project --with polars --with numpy --with lightgbm --with tzdata \
    python factory/scripts/live_admission.py
"""
import pickle
import numpy as np
import polars as pl

FEATS = ["pct_gain_grid", "rank", "n_hod_breaks", "dip_5m", "trap_reclaim", "dip_depth_5m",
         "vwap_dist", "above_vwap", "dist_open", "open_gap", "dist_hod", "range_pos",
         "log_close", "log_dollar_volume", "dv_5m_rate", "dv_accel", "rvol", "excess_gain",
         "market_ret_5m", "tod_min", "dow",
         "ret_1m", "ret_3m", "ret_5m", "ret_10m", "ret_15m", "ret_30m",
         "realized_vol_15m", "efficiency_30m", "n_up_bars_15"]
COSTS = (0.002, 0.004)
CAL_MONTHS = ["2025-05", "2025-06", "2025-07"]
OOS_MONTHS = ["2025-08", "2025-09", "2025-10", "2025-11", "2025-12"]


def score_month(m, model):
    df = pl.read_parquet(f"data/ml_features/features_{m}.parquet")
    df = df.with_columns(pl.col("cum_dv").log1p().alias("log_dollar_volume"))
    X = df.select(FEATS).to_numpy().astype(np.float32)
    df = df.with_columns(pl.Series("score", model.predict(X, num_iteration=model.best_iteration)))
    return df.filter(pl.col("score").is_not_null() & pl.col("fwd_ret_60m").is_not_null())


def load(months, model):
    return pl.concat([score_month(m, model) for m in months])


def eval_adm(df, tag, cost=0.002):
    out = {"tag": tag, "n": df.height}
    if df.height == 0:
        return {**out, "net": None, "wr": None}
    net = df["fwd_ret_60m"] - cost
    net40 = df["fwd_ret_60m"] - 0.004
    days = df["et_date"].n_unique()
    out.update({
        "net20": float(net.mean()), "wr20": float((net > 0).mean()),
        "net40": float(net40.mean()),
        "net_t1": float((df["fwd60_t1entry"] - cost).mean()) if df["fwd60_t1entry"].is_not_null().any() else None,
        "trades_per_day": df.height / days, "episodes": df.select(["ticker", "et_date"]).unique().height,
        "monthly": {m: round(float(df.filter(pl.col("month") == m)["fwd_ret_60m"].mean() - cost), 5)
                    for m in sorted(df["month"].unique().to_list())},
    })
    return out


def main():
    model = pickle.load(open("factory/artifacts/ml/model_v1.pkl", "rb"))
    cal = load(CAL_MONTHS, model)
    theta_fixed = float(cal["score"].quantile(0.90))
    theta_hi = float(cal["score"].quantile(0.97))
    print(f"theta_fixed(90pct May-Jul)={theta_fixed:.5f}  theta_hi(97pct)={theta_hi:.5f}")

    oos = load(OOS_MONTHS, model).sort(["ticker", "et_date", "tod_min"])

    # M1 fixed threshold
    m1 = oos.filter(pl.col("score") >= theta_fixed)

    # M2 rolling 5-day percentile threshold (prior days only)
    day_q = (oos.group_by("et_date").agg(pl.col("score").quantile(0.90).alias("q90"))
             .sort("et_date"))
    day_q = day_q.with_columns(pl.col("q90").rolling_mean(window_size=5, min_samples=3).shift(1).alias("thr"))
    oos = oos.join(day_q.select(["et_date", "thr"]), on="et_date", how="left")
    oos = oos.with_columns(pl.col("thr").fill_null(theta_fixed))
    m2 = oos.filter(pl.col("score") >= pl.col("thr"))

    # M3 visible-rank within current minute's candidate set (live top-20 list)
    oos = oos.with_columns(pl.col("score").rank("ordinal", descending=True).over(["et_date", "tod_min"]).alias("vis_rank"))
    m3 = oos.filter((pl.col("vis_rank") <= 2) & (pl.col("score") >= theta_fixed))
    m3_pure = oos.filter(pl.col("vis_rank") <= 2)

    # M4 first crossing per episode
    m4 = (oos.filter(pl.col("score") >= theta_fixed)
          .sort(["ticker", "et_date", "tod_min"])
          .group_by(["ticker", "et_date"]).first())

    # M5 persistence: >=2 of last 3 minutes above theta
    oos = oos.sort(["ticker", "et_date", "tod_min"]).with_columns(
        (pl.col("score") >= theta_fixed).cast(pl.Int32).rolling_sum(window_size=3, min_samples=3)
        .over(["ticker", "et_date"]).alias("persist3"))
    m5 = oos.filter(pl.col("persist3") >= 2)

    rows = []
    # reference: whole-day D10 (non-executable)
    oos_r = oos.with_columns(pl.col("score").rank("ordinal").over("et_date").alias("_r"),
                             pl.len().over("et_date").alias("_n"))
    d10 = oos_r.filter(pl.col("_r") >= 0.9 * pl.col("_n"))
    rows.append(eval_adm(d10, "REF_wholeday_D10"))
    rows.append(eval_adm(m1, "M1_fixed_theta"))
    rows.append(eval_adm(m2, "M2_rolling_thr"))
    rows.append(eval_adm(m3_pure, "M3_visrank2_pure"))
    rows.append(eval_adm(m3, "M3_visrank2+theta"))
    rows.append(eval_adm(m4, "M4_first_crossing"))
    rows.append(eval_adm(m5, "M5_persist_2of3"))

    print(f"\n=== OOS Aug-Dec frozen @20bps ===")
    for r in rows:
        mo = " ".join(f"{k[-2:]}:{v:+.2%}" for k, v in (r.get("monthly") or {}).items())
        print(f"{r['tag']:<22} n={r['n']:>6} net={r['net20']:+.3%} wr={r['wr20']:.3f} "
              f"net40={r['net40']:+.3%} t1={r['net_t1']:+.3%} t/day={r['trades_per_day']:.0f} "
              f"eps={r['episodes']}")
        print(f"{'':<22} monthly: {mo}")

    # recommended combined: M3 admission ∧ first-crossing (1 entry/episode) — the bot behavior
    comb = (oos.filter((pl.col("vis_rank") <= 2) & (pl.col("score") >= theta_fixed))
            .sort(["ticker", "et_date", "tod_min"]).group_by(["ticker", "et_date"]).first())
    r = eval_adm(comb, "COMBINED_M3first")
    print(f"\nCOMBINED (M3 ∧ first-crossing/episode): n={r['n']} net={r['net20']:+.3%} "
          f"wr={r['wr20']:.3f} t/day={r['trades_per_day']:.0f} eps={r['episodes']}")
    mo = " ".join(f"{k[-2:]}:{v:+.2%}" for k, v in r["monthly"].items())
    print(f"{'':<22} monthly: {mo}")

    # theta-tier curve on M4 entries (smooth score -> admission tiers, not binary-only)
    print("\n=== M4 entries by score tier (OOS, @20bps) ===")
    m4s = m4.with_columns(pl.col("score").cut([theta_fixed, theta_hi],
                                              labels=["<t90", "t90-97", ">t97"]).alias("tier"))
    for x in (m4s.group_by("tier").agg(pl.len().alias("n"), (pl.col("fwd_ret_60m") - 0.002).mean().alias("net"),
                                       (pl.col("fwd_ret_60m") > 0.002).mean().alias("wr"))
              .sort("tier").to_dicts()):
        print(f"{x['tier']}: n={x['n']} net={x['net']:+.3%} wr={x['wr']:.3f}")

    # composite overlay on the M3 stream
    comp = m3.filter((pl.col("rvol") > 4) & (pl.col("vwap_dist") > 0.03) & (pl.col("tod_min") < 270))
    r = eval_adm(comp, "M3xCOMPOSITE")

    print(f"M3 & rv>4 & vwap>3% & tod<270: n={r['n']} net20={r['net20']:+.3%} wr={r['wr20']:.3f} "
          f"net40={r['net40']:+.3%} t/day={r['trades_per_day']:.0f}")
    mo = " ".join(f"{k[-2:]}:{v:+.2%}" for k, v in r["monthly"].items())
    print(f"{'':<22} monthly: {mo}")


if __name__ == "__main__":
    main()
