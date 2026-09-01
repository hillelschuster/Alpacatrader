"""Trade sequencing — turn many correlated admission minutes into realistic position sequences.

All variants use ONLY causal info at each entry decision. Entry fill = next bar close
(labels are fwd60_t1entry = c[t+61]/c[t+1]-1; executable). Units equal-size, 60m hold.

Streams (causal, from live_admission.py):
  m3    vis_rank<=2 within current minute's visible candidate set & score >= theta_fixed
  comp  m3 & rvol>4 & vwap_dist>0.03 & tod<270

Variants per (ticker, et_date) episode:
  S0_indep   every stream event = one unit (upper bound on trade count)
  S1_first   one unit at first stream event
  S2_scale   unit1 at first event; unit2 at first later event with score >= theta_hi (cap 2)
  S3_reentry unit1 at first event; re-enter after 60m hold + 15m cooldown (cap 3)
  S4_scale3  S2 plus unit3 at a second theta_hi crossing >=20m after unit2 (cap 3)

Usage:
  uv run --no-project --with polars --with numpy --with lightgbm --with tzdata \
    python factory/scripts/sequencing.py
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
COST = 0.002
OOS = ["2025-08", "2025-09", "2025-10", "2025-11", "2025-12"]


def main():
    model = pickle.load(open("factory/artifacts/ml/model_v1.pkl", "rb"))
    dfs = []
    for m in OOS:
        df = pl.read_parquet(f"data/ml_features/features_{m}.parquet")
        df = df.with_columns(pl.col("cum_dv").log1p().alias("log_dollar_volume"))
        X = df.select(FEATS).to_numpy().astype(np.float32)
        df = df.with_columns(pl.Series("score", model.predict(X, num_iteration=model.best_iteration)))
        dfs.append(df)
    df = pl.concat(dfs).filter(pl.col("score").is_not_null())
    theta, theta_hi = 0.00115, 0.00340  # frozen (May-Jul p90/p97, live_admission.log)

    # reviewer-fix (MATERIAL): NO label filter before picks — pool is score-complete only.
    # Labelability is enforced causally: entry tod<=328 guarantees t+61 inside session
    # (clock is knowable at decision time). Residual null labels = intraday no-trade bars
    # (unknowable live) -> excluded from PnL, share reported per variant.
    LABEL_CAP = 328
    df = df.sort(["et_date", "tod_min"]).with_columns(
        pl.col("score").rank("ordinal", descending=True).over(["et_date", "tod_min"]).alias("vis_rank"))
    m3 = df.filter((pl.col("vis_rank") <= 2) & (pl.col("score") >= theta))
    comp = m3.filter((pl.col("rvol") > 4) & (pl.col("vwap_dist") > 0.03) & (pl.col("tod_min") < 270))

    for name, stream in [("M3", m3), ("COMPOSITE", comp)]:
        rows = stream.filter(pl.col("tod_min") <= LABEL_CAP).to_dicts()
        eps = {}
        for r in rows:
            eps.setdefault((r["ticker"], str(r["et_date"])[:10]), []).append(r)
        variants = ["S0_indep", "S1_first", "S2_scale", "S3_reentry", "S4_scale3"]
        res = {v: {"units": 0, "episodes": 0, "net_sum": 0.0, "wins": 0, "dv_sum": 0.0,
                   "nulls": 0} for v in variants}
        monthly = {v: {} for v in res}
        for key, evs in eps.items():
            evs.sort(key=lambda r: r["tod_min"])
            for v in res:
                res[v]["episodes"] += 1
            # S0
            picks = {"S0_indep": evs, "S1_first": evs[:1]}
            s2 = [evs[0]]
            u2 = next((e for e in evs[1:] if e["score"] >= theta_hi and e["tod_min"] > evs[0]["tod_min"] + 5), None)
            if u2: s2.append(u2)
            picks["S2_scale"] = s2
            s4 = list(s2)
            if u2:
                u3 = next((e for e in evs if e["score"] >= theta_hi and e["tod_min"] > u2["tod_min"] + 20 and e is not u2), None)
                if u3: s4.append(u3)
            picks["S4_scale3"] = s4
            s3 = [evs[0]]
            last_exit = evs[0]["tod_min"] + 60
            while len(s3) < 3:
                nxt = next((e for e in evs if e["tod_min"] >= last_exit + 15 and e not in s3), None)
                if not nxt: break
                s3.append(nxt); last_exit = nxt["tod_min"] + 60
            picks["S3_reentry"] = s3
            for v, ps in picks.items():
                for e in ps:
                    res[v]["units"] += 1
                    res[v]["dv_sum"] += e["cum_dv"]
                    if e["fwd60_t1entry"] is None:
                        res[v]["nulls"] += 1
                        continue
                    net = e["fwd60_t1entry"] - COST
                    res[v]["net_sum"] += net
                    res[v]["wins"] += net > 0
                    monthly[v].setdefault(e["month"], []).append(net)
        print(f"\n=== stream {name} (OOS Aug-Dec, {COST:.1%} RT, t1-entry fills, reviewer-fixed pool) ===")
        print(f"{'variant':<11} {'eps':>5} {'units':>6} {'u/ep':>5} {'net/unit':>9} {'wr':>5} {'net/ep':>9} {'null%':>6} {'avg_cumdv$M':>11}")
        for v in res:
            r = res[v]
            u, e = r["units"], r["episodes"]
            ul = u - r["nulls"]  # label-complete units
            nu = r["net_sum"] / ul if ul else 0.0
            print(f"{v:<11} {e:>5} {u:>6} {u/e:>5.1f} {nu:>+9.3%} {r['wins']/ul:>5.3f} "
                  f"{r['net_sum']/e:>+9.3%} {100*r['nulls']/u:>5.1f}% {r['dv_sum']/u/1e6:>11.1f}")
            mo = {k: sum(x) / len(x) for k, x in sorted(monthly[v].items())}
            print(f"{'':<11} monthly/unit: " + " ".join(f"{k[-2:]}:{x:+.2%}" for k, x in mo.items()))


if __name__ == "__main__":
    main()
