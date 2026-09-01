"""FROZEN 2026 evaluation — model_v1 + live admission stack applied to truly unseen months.

Nothing here is tuned on 2026. Thresholds/model/rule all frozen from 2025 work:
  theta_fixed=0.00115 (90th pct May-Jul scores), theta_hi=0.00340 (97th pct)
  M3 admission: score-rank<=2 within current minute's visible candidate set & score>=theta
  composite: M3 & rvol>4 & vwap_dist>0.03 & tod<270
  S4 sequencing: unit1 first composite minute; unit2/3 at later score>=theta_hi crossings
                 (unit2 >= +5m after unit1, unit3 >= +20m after unit2)
  economic pocket: composite & close<=20 & cum_dv in [5M,100M]   (post-peek 2025 selection —
                   disclosed; this is its first out-of-sample look)
Costs: RT on t1-entry executable fills (fwd60_t1entry - cost).

Usage:
  uv run --no-project --with polars --with numpy --with lightgbm --with tzdata \
    python factory/scripts/eval_2026.py
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
THETA, THETA_HI = 0.00115, 0.00340
MONTHS = ["2026-01", "2026-02", "2026-03"]


def main():
    model = pickle.load(open("factory/artifacts/ml/model_v1.pkl", "rb"))
    dfs = []
    for m in MONTHS:
        df = pl.read_parquet(f"data/ml_features/features_{m}.parquet")
        df = df.with_columns(pl.col("cum_dv").log1p().alias("log_dollar_volume"))
        X = df.select(FEATS).to_numpy().astype(np.float32)
        df = df.with_columns(pl.Series("score", model.predict(X, num_iteration=model.best_iteration)))
        dfs.append(df)
        print(f"{m}: {df.height} events, score range [{df['score'].min():.4f},{df['score'].max():.4f}]")
    df_all = pl.concat(dfs)
    df = df_all.filter(pl.col("score").is_not_null() & pl.col("fwd60_t1entry").is_not_null())
    df = df.sort(["et_date", "tod_min"]).with_columns(
        pl.col("score").rank("ordinal", descending=True).over(["et_date", "tod_min"]).alias("vis_rank"),
        pl.col("score").rank("ordinal").over("et_date").alias("day_rank"),
        pl.len().over("et_date").alias("day_n"))

    def rep(name, d):
        if d.height == 0:
            print(f"{name}: n=0")
            return
        net = d["fwd60_t1entry"] - 0.002
        days = d["et_date"].n_unique()
        print(f"{name:<28} n={d.height:>6} net20={net.mean():+.3%} wr={(net>0).mean():.3f} "
              f"net40={net.mean()-0.002:+.3%} net60={net.mean()-0.004:+.3%} t/day={d.height/days:.0f} "
              f"eps={d.select(['ticker','et_date']).unique().height}")
        mo = {k: (v["fwd60_t1entry"] - 0.002).mean() for k, v in
              {m: d.filter(pl.col("month") == m) for m in sorted(d["month"].unique().to_list())}.items()}
        print(f"{'':<28} monthly: " + " ".join(f"{k[-2:]}:{v:+.2%}" for k, v in mo.items()))

    print(f"\n=== FROZEN 2026 eval: {MONTHS} ===")
    d10 = df.filter(pl.col("day_rank") >= 0.9 * pl.col("day_n"))
    m3 = df.filter((pl.col("vis_rank") <= 2) & (pl.col("score") >= THETA))
    comp = m3.filter((pl.col("rvol") > 4) & (pl.col("vwap_dist") > 0.03) & (pl.col("tod_min") < 270))
    pocket = comp.filter((pl.col("close") <= 20) & (pl.col("cum_dv") >= 5e6) & (pl.col("cum_dv") <= 1e8))
    rep("REF_wholeday_D10", d10)
    rep("M3_admission", m3)
    rep("M3xCOMPOSITE", comp)
    rep("POCKET(<=20,5-100M)", pocket)

    # DIAGNOSTIC (not frozen): month-own p90 theta — separates regime shift from edge loss.
    m3_rel = df.filter((pl.col("vis_rank") <= 2) &
                       (pl.col("score") >= pl.col("score").quantile(0.90)))
    comp_rel = m3_rel.filter((pl.col("rvol") > 4) & (pl.col("vwap_dist") > 0.03) &
                             (pl.col("tod_min") < 270))
    rep("DIAG_M3_relp90 (not frozen)", m3_rel)
    rep("DIAG_COMP_relp90 (not frozen)", comp_rel)

    # S4 sequencing on composite — reviewer-fixed: picks from SCORE-complete pool with
    # decision-time tod cap (<=328); PnL on label-complete picks; null share reported.
    pool = df_all.filter(pl.col("score").is_not_null())
    pool = pool.sort(["et_date", "tod_min"]).with_columns(
        pl.col("score").rank("ordinal", descending=True).over(["et_date", "tod_min"]).alias("vis_rank"))
    comp_full = pool.filter((pl.col("vis_rank") <= 2) & (pl.col("score") >= THETA) &
                            (pl.col("rvol") > 4) & (pl.col("vwap_dist") > 0.03) &
                            (pl.col("tod_min") < 270) & (pl.col("tod_min") <= 328))
    eps = {}
    for r in comp_full.to_dicts():
        eps.setdefault((r["ticker"], str(r["et_date"])[:10]), []).append(r)
    u1 = u2 = u3 = nul = 0; s = 0.0; w = 0; ms = {}
    for key, evs in eps.items():
        evs.sort(key=lambda r: r["tod_min"])
        picks = [evs[0]]
        x2 = next((e for e in evs[1:] if e["score"] >= THETA_HI and e["tod_min"] > evs[0]["tod_min"] + 5), None)
        if x2:
            picks.append(x2)
            x3 = next((e for e in evs if e["score"] >= THETA_HI and e["tod_min"] > x2["tod_min"] + 20 and e is not x2), None)
            if x3: picks.append(x3)
        for e in picks:
            u1 += e is picks[0]; u2 += e is x2; u3 += e is picks[-1] and len(picks) == 3 and e is not x2
            if e["fwd60_t1entry"] is None:
                nul += 1
                continue
            net = e["fwd60_t1entry"] - 0.002
            s += net; w += net > 0
            ms.setdefault(e["month"], []).append(net)
    n = u1 + u2 + u3 - nul
    tot = u1 + u2 + u3
    print(f"S4_scale_composite          n={n:>6} net20={s/n:+.3%} wr={w/n:.3f} "
          f"(u1={u1} u2={u2} u3={u3} nulls={nul} of {tot}, eps={len(eps)})")
    print(f"{'':<28} monthly: " + " ".join(f"{k[-2:]}:{np.mean(v):+.2%}" for k, v in sorted(ms.items())))

    # per-unit attribution on S4 (is the add-on entry still good?)
    print("\n=== S4 unit-level expectancy (composite, 2026) ===")
    for tag, cnt in [("unit1", u1), ("unit2", u2), ("unit3", u3)]:
        print(f"{tag}: {cnt}")

    # score-tier gradient on M3 stream (frozen model, fresh months)
    print("\n=== M3 stream score tiers (2026) ===")
    tiers = m3.with_columns(pl.col("score").cut([THETA, THETA_HI], labels=["t90-97", ">t97"]).alias("tier"))
    for x in (tiers.group_by("tier").agg(pl.len().alias("n"), (pl.col("fwd60_t1entry") - 0.002).mean().alias("net"),
                                         ((pl.col("fwd60_t1entry") - 0.002) > 0).mean().alias("wr"))
              .sort("tier").to_dicts()):
        print(f"{x['tier']}: n={x['n']} net={x['net']:+.3%} wr={x['wr']:.3f}")


if __name__ == "__main__":
    main()
