"""ML v1 — LightGBM on winsorized fwd_ret_60m + trading-quality evaluation.

Design (per overnight spec + oracle review):
- Train: May+Jun events; Dev: Jul (early stopping + threshold); OOS: later months when features exist.
- Target: raw fwd_ret_60m winsorized at +-10% (costs applied at evaluation only).
- sample_weight = 1/n_events(ticker,et_date), capped at 1/120 — equalizes episode influence.
- Models: LightGBM (single config) + ElasticNet baseline. No big search.
- Evaluation: decile tables (all-events + one-trade-per-episode), net @20/40bps, wr,
  day-clustered bootstrap CI, daily PnL series, drawdown, monthly split,
  t+1m-entry label variant, long/short, IC.
- Frozen rule: thresholds chosen on July; Aug-Dec OOS evaluated once with frozen model+thresholds.
"""
import argparse, json, pickle
from pathlib import Path
import numpy as np
import polars as pl

FEATURES = ["pct_gain_grid", "rank", "n_hod_breaks", "dip_5m", "trap_reclaim", "dip_depth_5m",
            "vwap_dist", "above_vwap", "dist_open", "open_gap", "dist_hod", "range_pos",
            "log_close", "log_dollar_volume", "dv_5m_rate", "dv_accel", "rvol", "excess_gain",
            "market_ret_5m", "tod_min", "dow",
            "ret_1m", "ret_3m", "ret_5m", "ret_10m", "ret_15m", "ret_30m",
            "realized_vol_15m", "efficiency_30m", "n_up_bars_15"]
LABEL = "fwd_ret_60m"
LABEL_T1 = "fwd60_t1entry"
COST = {"c20": 0.002, "c40": 0.004}


def load_feats(months):
    dfs = [pl.read_parquet(f"data/ml_features/features_{m}.parquet") for m in months]
    df = pl.concat(dfs, how="vertical")
    df = df.with_columns(pl.col("cum_dv").log1p().alias("log_dollar_volume"))
    df = df.with_columns(pl.col(LABEL).clip(-0.10, 0.10).alias("y"))
    cnt = df.group_by(["ticker", "et_date"]).len().rename({"len": "n_ep"})
    df = df.join(cnt, on=["ticker", "et_date"], how="left")
    df = df.with_columns((1.0 / pl.min_horizontal(pl.col("n_ep"), 120)).alias("w"))
    return df


def decile_table(df: pl.DataFrame, score_col: str, cost_key: str, label_col: str = LABEL,
                 weighted: bool = False) -> list:
    cost = COST[cost_key]
    d = df.with_columns((pl.col(label_col) - cost).alias("net")).filter(pl.col("net").is_not_null())
    d = d.with_columns(pl.col(score_col).rank("ordinal").over("et_date").alias("_r"))
    d = d.with_columns((pl.col("_r") / pl.len().over("et_date")).alias("_q"))
    d = d.with_columns(pl.col("_q").cut([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
                                        labels=[f"D{i}" for i in range(1, 11)]).alias("decile"))
    if weighted:
        out = (d.group_by("decile")
               .agg(pl.len().alias("n"), ((pl.col("net") * pl.col("w")).sum() / pl.col("w").sum()).alias("net_mean"),
                    ((pl.col(label_col) * pl.col("w")).sum() / pl.col("w").sum()).alias("gross_mean"),
                    ((pl.col("net") > 0).cast(pl.Float64) * pl.col("w")).sum().alias("_wrn"),
                    pl.col("w").sum().alias("_wrd"))
               .sort("decile").to_dicts())
        for x in out:
            x["wr"] = x.pop("_wrn") / x.pop("_wrd")
            x["net_p90"] = None
        return out
    return (d.group_by("decile")
            .agg(pl.len().alias("n"), pl.col("net").mean().alias("net_mean"),
                 pl.col(label_col).mean().alias("gross_mean"),
                 (pl.col("net") > 0).mean().alias("wr"),
                 pl.col("net").quantile(0.9).alias("net_p90"))
            .sort("decile").to_dicts())


def one_per_episode(df: pl.DataFrame, score_col: str, label_col: str, cost: float) -> pl.DataFrame:
    best = (df.sort([score_col], descending=True)
            .group_by(["ticker", "et_date"]).first())
    return best.with_columns((pl.col(label_col) - cost).alias("net"))


def bootstrap_ci(vals_by_day: dict, n_boot: int = 2000, seed: int = 7):
    days = sorted(vals_by_day)
    arrs = [np.asarray(vals_by_day[d], dtype=float) for d in days]
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(arrs), len(arrs))
        means.append(np.concatenate([arrs[i] for i in pick]).mean())
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(np.concatenate(arrs).mean()), float(lo), float(hi)


def trade_report(df: pl.DataFrame, score_col: str, cost: float, label_col: str = LABEL, tag: str = "") -> dict:
    df = df.filter(pl.col(label_col).is_not_null() & pl.col(score_col).is_not_null())
    d = df.with_columns((pl.col(label_col) - cost).alias("net"))
    rep = {"tag": tag, "n": d.height, "n_days": d["et_date"].n_unique()}
    net_long = d[label_col] - cost
    net_short = -(d[label_col]) - cost
    rep["long"] = {"exp_net": float(net_long.mean()), "wr": float((net_long > 0).mean())}
    rep["short"] = {"exp_net": float(net_short.mean()), "wr": float((net_short > 0).mean())}
    daily = d.group_by("et_date").agg(pl.col("net").mean().alias("day_net"))
    pnl = daily["day_net"].to_numpy()
    curve = np.cumsum(pnl)
    dd = float((np.maximum.accumulate(curve) - curve).max()) if len(curve) else 0.0
    rep["daily_mean_net"] = float(pnl.mean())
    rep["daily_pnl_std"] = float(pnl.std())
    rep["total_pnl"] = float(curve[-1]) if len(curve) else 0.0
    rep["max_dd_of_daily_mean_series"] = dd
    rep["pos_days_frac"] = float((pnl > 0).mean())
    best = one_per_episode(df, score_col, label_col, cost)
    netb = best["net"].drop_nulls()
    rep["episode_best"] = {"n": best.height, "exp_net": float(netb.mean()), "wr": float((netb > 0).mean())}
    vals_by_day = {}
    for row in best.select(["et_date", "net"]).drop_nulls().iter_rows(named=True):
        if row["net"] == row["net"]:  # NaN guard
            vals_by_day.setdefault(row["et_date"], []).append(row["net"])
    m, lo, hi = bootstrap_ci(vals_by_day)
    rep["episode_best"]["boot_mean"] = m
    rep["episode_best"]["boot_ci95"] = [lo, hi]
    return rep


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--train", nargs="+", required=True)
    p.add_argument("--dev", nargs="+", required=True)
    p.add_argument("--oos", nargs="*", default=[])
    p.add_argument("--model", default="lgbm", choices=["lgbm", "enet"])
    p.add_argument("--tag", default="v1")
    p.add_argument("--outdir", type=Path, default=Path("factory/artifacts/ml"))
    a = p.parse_args()

    tr = load_feats(a.train)
    dv = load_feats(a.dev)
    print(f"train {tr.height:,} rows | dev {dv.height:,}")

    Xtr = tr.select(FEATURES).to_numpy().astype(np.float32)
    ytr = tr["y"].to_numpy().astype(np.float32)
    wtr = tr["w"].to_numpy().astype(np.float32)
    Xdv = dv.select(FEATURES).to_numpy().astype(np.float32)
    ydv = dv["y"].to_numpy().astype(np.float32)

    if a.model == "lgbm":
        import lightgbm as lgb
        params = {"objective": "regression", "metric": "mae", "learning_rate": 0.05,
                  "num_leaves": 31, "max_depth": 6, "min_data_in_leaf": 200,
                  "feature_fraction": 0.9, "bagging_fraction": 0.9, "bagging_freq": 1,
                  "verbose": -1, "seed": 7}
        dtrain = lgb.Dataset(Xtr, label=ytr, weight=wtr, feature_name=FEATURES, free_raw_data=False)
        dval = lgb.Dataset(Xdv, label=ydv, weight=dv["w"].to_numpy().astype(np.float32), reference=dtrain)
        model = lgb.train(params, dtrain, num_boost_round=2000, valid_sets=[dval],
                          callbacks=[lgb.early_stopping(100, verbose=False)])
        print(f"best_iter={model.best_iteration}")
        pred = lambda df: model.predict(df.select(FEATURES).to_numpy().astype(np.float32),
                                        num_iteration=model.best_iteration)
    else:
        from sklearn.linear_model import ElasticNet
        mu, sd = np.nanmean(Xtr, 0), np.nanstd(Xtr, 0) + 1e-9
        Xtr_s = np.where(np.isnan(Xtr), 0.0, (Xtr - mu) / sd)
        enet = ElasticNet(alpha=0.001, l1_ratio=0.2).fit(Xtr_s, ytr, sample_weight=wtr)
        pred = lambda df: enet.predict(np.where(
            np.isnan(df.select(FEATURES).to_numpy().astype(np.float32)), 0.0,
            (df.select(FEATURES).to_numpy().astype(np.float32) - mu) / sd))
        model = enet

    dv = dv.with_columns(pl.Series("score", pred(dv)))
    rep = {"config": {"train": a.train, "dev": a.dev, "oos": a.oos, "model": a.model,
                      "features": FEATURES, "label": LABEL, "cost": COST},
           "dev": {}, "oos": {}}
    rep["dev"]["deciles_c20"] = decile_table(dv, "score", "c20")
    rep["dev"]["deciles_c20_w"] = decile_table(dv, "score", "c20", weighted=True)
    rep["dev"]["trade_c20"] = trade_report(dv, "score", COST["c20"], tag="dev_all")
    rep["dev"]["trade_t1_c20"] = trade_report(dv, "score", COST["c20"], LABEL_T1, tag="dev_t1entry")
    # D10-only reports (the actionable pocket): t-entry and t+1m-entry, both costs
    d10 = dv.filter(pl.col("score").rank("ordinal").over("et_date") >= 0.9 * pl.len().over("et_date"))
    rep["dev"]["d10_t1_c40"] = trade_report(d10, "score", COST["c40"], LABEL_T1, tag="dev_D10_t1")
    rep["dev"]["d10_c20"] = trade_report(d10, "score", COST["c20"], LABEL, tag="dev_D10")
    rep["dev"]["d10_t1_c20"] = trade_report(d10, "score", COST["c20"], LABEL_T1, tag="dev_D10_t1")
    ics = []
    for day in dv["et_date"].unique().to_list():
        d1 = dv.filter(pl.col("et_date") == day).select(["score", LABEL]).drop_nulls()
        if d1.height > 30:
            ra = d1["score"].rank("average").to_numpy()
            rb = d1[LABEL].rank("average").to_numpy()
            ics.append(float(np.corrcoef(ra, rb)[0, 1]))
    rep["dev"]["spearman_IC_daymean"] = float(np.mean(ics)) if ics else None
    rep["dev"]["spearman_IC_days"] = len(ics)

    if a.oos:
        for m in a.oos:
            o = load_feats([m])
            o = o.with_columns(pl.Series("score", pred(o)))
            rep["oos"][m] = {
                "deciles_c20": decile_table(o, "score", "c20"),
                "deciles_c20_w": decile_table(o, "score", "c20", weighted=True),
                "trade_c20": trade_report(o, "score", COST["c20"], tag=f"{m}_all"),
                "trade_t1_c20": trade_report(o, "score", COST["c20"], LABEL_T1, tag=f"{m}_t1entry"),
                "trade_c40": trade_report(o, "score", COST["c40"], tag=f"{m}_all40"),
                "d10_t1_c20": trade_report(
                    o.filter(pl.col("score").rank("ordinal").over("et_date") >= 0.9 * pl.len().over("et_date")),
                    "score", COST["c20"], LABEL_T1, tag=f"{m}_D10_t1"),
            }
            print(f"OOS {m}: all net20={rep['oos'][m]['trade_c20']['long']['exp_net']:.4%} "
                  f"ep_best net20={rep['oos'][m]['trade_c20']['episode_best']['exp_net']:.4%}")

    a.outdir.mkdir(parents=True, exist_ok=True)
    (a.outdir / f"train_report_{a.tag}.json").write_text(json.dumps(rep, indent=2, default=str))
    if a.model == "lgbm":
        imp = sorted(zip(FEATURES, model.feature_importance("gain")), key=lambda x: -x[1])
        (a.outdir / f"feature_importance_{a.tag}.json").write_text(
            json.dumps({k: float(v) for k, v in imp}, indent=2))
        pickle.dump(model, open(a.outdir / f"model_{a.tag}.pkl", "wb"))
    print(f"wrote {a.outdir}/train_report_{a.tag}.json")


if __name__ == "__main__":
    main()
