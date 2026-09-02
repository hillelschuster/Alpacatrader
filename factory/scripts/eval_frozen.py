"""FROZEN v2 evaluation — E6_rvol8 + X_60m on given months (default: untouched 2026-04..08).

Exact replica of exposure_design.py frozen-path semantics (E6_rvol8 entries, X_60m exit,
economics with cap-10/$10k concurrency, per-name 1 position) + the pre-registered
reporting from FROZEN_V2.md: month-by-month, pooled, 20/40bps, PnL concentration.

NO tuning knobs. theta from env THETA (frozen default 0.00098), model path fixed.

Run: uv run --no-project --with polars --with numpy --with lightgbm --with tzdata \
  python factory/scripts/eval_frozen.py --months 2026-04 2026-05 2026-06 2026-07 2026-08
"""
import argparse
import datetime
import os
import pickle

import numpy as np
import polars as pl

FEATS = ["pct_gain_grid", "rank", "n_hod_breaks", "dip_5m", "trap_reclaim", "dip_depth_5m",
         "vwap_dist", "above_vwap", "dist_open", "open_gap", "dist_hod", "range_pos",
         "log_close", "log_dollar_volume", "dv_5m_rate", "dv_accel", "rvol", "excess_gain",
         "market_ret_5m", "tod_min", "dow",
         "ret_1m", "ret_3m", "ret_5m", "ret_10m", "ret_15m", "ret_30m",
         "realized_vol_15m", "efficiency_30m", "n_up_bars_15"]
THETA = float(os.environ.get("THETA", "0.00098"))
MODEL = "factory/artifacts/ml/model_v2.pkl"
COST = 0.002
HOLD = 60
ENTRY_CAP = 328
UNIT_USD = 10_000
CAP = 10


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="+", required=True)
    a = ap.parse_args()

    model = pickle.load(open(MODEL, "rb"))
    dfs = []
    for m in a.months:
        df = pl.read_parquet(f"data/ml_features/features_{m}.parquet")
        df = df.with_columns(pl.col("cum_dv").log1p().alias("log_dollar_volume"))
        X = df.select(FEATS).to_numpy().astype(np.float32)
        df = df.with_columns(pl.Series("score", model.predict(X, num_iteration=model.best_iteration)))
        dfs.append(df)
        print(f"{m}: {df.height:,} events, score [{df['score'].min():.4f},{df['score'].max():.4f}]")
    pool = pl.concat(dfs).filter(pl.col("score").is_not_null())
    pool = pool.sort(["et_date", "tod_min"]).with_columns(
        pl.col("score").rank("ordinal", descending=True).over(["et_date", "tod_min"]).alias("vis_rank"))
    m3 = pool.filter((pl.col("vis_rank") <= 2) & (pl.col("score") >= THETA))
    comp = m3.filter((pl.col("rvol") > 4) & (pl.col("vwap_dist") > 0.03) & (pl.col("tod_min") < 270))
    print(f"theta={THETA}  m3={m3.height:,}  composite={comp.height:,}")

    rows = comp.filter(pl.col("tod_min") <= ENTRY_CAP).to_dicts()
    eps = {}
    for r in rows:
        eps.setdefault((r["ticker"], str(r["et_date"])[:10]), []).append(r)
    for v in eps.values():
        v.sort(key=lambda r: r["tod_min"])

    entries = []
    for key, evs in eps.items():
        pool_evs = [e for e in evs if e["rvol"] is not None and e["rvol"] > 8]
        if not pool_evs:
            continue
        picks, t = [pool_evs[0]], pool_evs[0]["tod_min"]
        while len(picks) < 3:
            nxt = next((x for x in pool_evs if x["tod_min"] >= t + HOLD + 1), None)
            if not nxt:
                break
            picks.append(nxt)
            t = nxt["tod_min"]
        entries.extend(picks)
    entries.sort(key=lambda e: (str(e["et_date"])[:10], e["tod_min"]))
    print(f"E6_rvol8 entries: {len(entries)}")

    # concurrency accounting (exposure_design semantics)
    open_pos = {}
    daily = {}
    daily40 = {}
    taken = skipped = 0
    trades = []
    for e in entries:
        dnum = datetime.date.fromisoformat(str(e["et_date"])[:10]).toordinal() * 1440
        t = dnum + e["tod_min"]
        open_pos = {k: v for k, v in open_pos.items() if v > t}
        if e["ticker"] in open_pos:
            continue
        if len(open_pos) >= CAP:
            skipped += 1
            continue
        r = e["fwd60_t1entry"]
        open_pos[e["ticker"]] = t + HOLD + 1
        taken += 1
        day = str(e["et_date"])[:10]
        if r is not None:
            daily[day] = daily.get(day, 0.0) + (r - COST) * UNIT_USD
            daily40[day] = daily40.get(day, 0.0) + (r - 2 * COST) * UNIT_USD
            trades.append({"day": day, "ticker": e["ticker"], "tod": e["tod_min"],
                           "net20": (r - COST) * UNIT_USD})

    # month tables
    print(f"\n{'month':10} {'entries':>7} {'taken':>6} {'net20/unit':>11} {'wr':>6} {'net40/unit':>11}")
    for m in a.months:
        me = [e for e in entries if str(e["et_date"])[:7] == m]
        mt = [tr for tr in trades if tr["day"][:7] == m]
        if not me:
            print(f"{m:10} {len(me):>7} {len(mt):>6} {'n/a':>11}")
            continue
        r20 = np.array([tr["net20"] for tr in mt]) / UNIT_USD
        r40 = np.array([tr["net20"] for tr in mt]) / UNIT_USD - COST
        print(f"{m:10} {len(me):>7} {len(mt):>6} {r20.mean():>+10.3%} {(r20 > 0).mean():>6.3f} "
              f"{r40.mean():>+10.3%}")
    if trades:
        r20 = np.array([tr["net20"] for tr in trades]) / UNIT_USD
        r40 = r20 - COST
        vals = np.array(list(daily.values()))
        gross = np.abs(r20).sum()
        tops = np.sort(np.abs(r20))[::-1][:5].sum() / gross if gross > 0 else float("nan")
        pos_days = (vals > 0).mean()
        print(f"\nPOOLED: entries={len(entries)} taken={taken} skipped={skipped}")
        print(f"  net20/unit={r20.mean():+.3%} wr={(r20 > 0).mean():.3f}   "
              f"net40/unit={r40.mean():+.3%} wr={(r40 > 0).mean():.3f}")
        print(f"  economics @{UNIT_USD:,}/unit cap={CAP}: trade-days={len(vals)} "
              f"mean/day=${vals.mean():+,.0f} p10=${np.percentile(vals, 10):+,.0f} "
              f"p90=${np.percentile(vals, 90):+,.0f} pos-days={pos_days:.2f}")
        print(f"  concentration: top-5 trades = {tops:.1%} of |gross PnL|")
        best = sorted(trades, key=lambda x: -x["net20"])[:5]
        print("  top-5 trades: " + "; ".join(f"{t['ticker']} {t['day']} {t['net20'] / UNIT_USD:+.2%}"
                                             for t in best))


if __name__ == "__main__":
    main()
