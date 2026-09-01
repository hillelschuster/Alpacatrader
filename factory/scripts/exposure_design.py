"""Exposure design: how does one persistent qualifying episode become real exposure?

Derivation on 2025-08..12 composite stream, then --frozen replay on 2026-01..03.
Composite stream (frozen): score-complete pool, vis_rank<=2 within minute & score>=THETA
& rvol>4 & vwap_dist>0.03 & tod<270; entry minutes capped at tod<=328 (60m hold fits).

Exposure structures (per ticker-day episode, per-name 1 position at a time):
  E1_one      enter at first qualified minute only
  E2_cycle3   enter; after each 60m hold, re-enter at next qualified minute (max 3 entries)
  E3_persist  same, unlimited until tod<=328
  E5_rvol2    E2_cycle3 but unit=2 when rvol>8 at entry (economics: $ per unit dollar)

Exit variants evaluated on the SAME E3_persist entries (structure choice, 2025 only):
  X_60m    fixed 60m hold (fwd60_t1entry)              [baseline]
  X_death  exit at first checkpoint (t+5/15/30/60m) where the name is no longer stream-
           qualified (or fell out of the top-20) -> ret=(1+fwd[k+1])/(1+fwd[1])-1
  X_30m    fixed 30m (fwd30_t1entry)
  X_15m    fixed 15m (fwd15_t1entry)

Within-episode timing profile on E3 entries: net by cycle k, by minutes-since-first-qual,
by k-th-of-n qualified minutes.

Usage:
  uv run --no-project --with polars --with numpy --with lightgbm --with tzdata \
    python factory/scripts/exposure_design.py --years 2025
  uv run ... python factory/scripts/exposure_design.py --years 2026 --frozen-structure E2_cycle3 --frozen-exit X_60m
"""
import argparse
import pickle
import numpy as np
import polars as pl

FEATS = ["pct_gain_grid", "rank", "n_hod_breaks", "dip_5m", "trap_reclaim", "dip_depth_5m",
         "vwap_dist", "above_vwap", "dist_open", "open_gap", "dist_hod", "range_pos",
         "log_close", "log_dollar_volume", "dv_5m_rate", "dv_accel", "rvol", "excess_gain",
         "market_ret_5m", "tod_min", "dow",
         "ret_1m", "ret_3m", "ret_5m", "ret_10m", "ret_15m", "ret_30m",
         "realized_vol_15m", "efficiency_30m", "n_up_bars_15"]
THETA = 0.00115
COST = 0.002
HOLD = 60
ENTRY_CAP = 328
FWDS = {4: "fwd_ret_5m", 14: "fwd_ret_15m", 29: "fwd_ret_30m", 59: "fwd_ret_60m"}
YR = {"2025": ["2025-08", "2025-09", "2025-10", "2025-11", "2025-12"],
      "2026": ["2026-01", "2026-02", "2026-03"]}


def load_stream(year, model):
    dfs = []
    for m in YR[year]:
        df = pl.read_parquet(f"data/ml_features/features_{m}.parquet")
        df = df.with_columns(pl.col("cum_dv").log1p().alias("log_dollar_volume"))
        X = df.select(FEATS).to_numpy().astype(np.float32)
        df = df.with_columns(pl.Series("score", model.predict(X, num_iteration=model.best_iteration)))
        dfs.append(df)
    pool = pl.concat(dfs).filter(pl.col("score").is_not_null())
    pool = pool.sort(["et_date", "tod_min"]).with_columns(
        pl.col("score").rank("ordinal", descending=True).over(["et_date", "tod_min"]).alias("vis_rank"))
    m3 = pool.filter((pl.col("vis_rank") <= 2) & (pl.col("score") >= THETA))
    comp = m3.filter((pl.col("rvol") > 4) & (pl.col("vwap_dist") > 0.03) & (pl.col("tod_min") < 270))
    return pool, m3, comp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", required=True)
    ap.add_argument("--frozen-structure", default=None)
    ap.add_argument("--frozen-exit", default="X_60m")
    a = ap.parse_args()
    model = pickle.load(open("factory/artifacts/ml/model_v1.pkl", "rb"))
    pool, m3, comp = load_stream(a.years, model)

    # qualified-minute membership per (ticker, et_date) for state-death checks:
    # a minute is DEAD if not qualified; minutes absent from the pool rows are also dead.
    qual_sets = {}
    for r in comp.filter(pl.col("tod_min") <= ENTRY_CAP).iter_rows(named=True):
        qual_sets.setdefault((r["ticker"], str(r["et_date"])[:10]), set()).add(r["tod_min"])

    # events grid (all top-20 minutes) for dead-minute detection: minutes present but unqualified
    present = {}
    for r in pool.iter_rows(named=True):
        present.setdefault((r["ticker"], str(r["et_date"])[:10]), set()).add(r["tod_min"])

    rows = comp.filter(pl.col("tod_min") <= ENTRY_CAP).to_dicts()
    eps = {}
    for r in rows:
        eps.setdefault((r["ticker"], str(r["et_date"])[:10]), []).append(r)
    for v in eps.values():
        v.sort(key=lambda r: r["tod_min"])

    # ---- build entries per structure ----
    def entries_for(struct):
        out = []
        for key, evs in eps.items():
            q = [e["tod_min"] for e in evs]
            if struct == "E1_one":
                picks = [evs[0]]
            elif struct in ("E2_cycle3", "E5_rvol2"):
                picks, t = [evs[0]], evs[0]["tod_min"]
                while len(picks) < 3:
                    nxt = next((x for x in evs if x["tod_min"] >= t + HOLD + 1), None)
                    if not nxt:
                        break
                    picks.append(nxt); t = nxt["tod_min"]
            elif struct == "E3_persist":
                picks, t = [evs[0]], evs[0]["tod_min"]
                while True:
                    nxt = next((x for x in evs if x["tod_min"] >= t + HOLD + 1), None)
                    if not nxt:
                        break
                    picks.append(nxt); t = nxt["tod_min"]
            else:
                raise ValueError(struct)
            for k, e in enumerate(picks):
                e = dict(e)
                e["cycle"] = k + 1
                e["min_since_first"] = e["tod_min"] - evs[0]["tod_min"]
                e["kth_of_n"] = (k + 1, len(evs))
                e["units"] = 2 if (struct == "E5_rvol2" and e["rvol"] is not None and e["rvol"] > 8) else 1
                out.append(e)
        return out

    def ret_x(e, exit_variant):
        if exit_variant == "X_60m":
            return e["fwd60_t1entry"]
        if exit_variant == "X_30m":
            return e["fwd30_t1entry"]
        if exit_variant == "X_15m":
            return e["fwd15_t1entry"]
        if exit_variant == "X_death":
            t0 = e["tod_min"]
            key = (e["ticker"], str(e["et_date"])[:10])
            f1 = e["fwd_ret_1m"]
            if f1 is None:
                return None
            for k, col in FWDS.items():
                # minute t0+k: dead if absent from pool or present-but-unqualified
                if (t0 + k) not in qual_sets.get(key, set()):
                    fk = e[col]
                    if fk is None:
                        continue
                    return (1 + fk) / (1 + f1) - 1
            return e["fwd60_t1entry"]
        raise ValueError(exit_variant)

    def report(name, ents, exit_variant="X_60m"):
        nets, units_total, ep_done = [], 0, len(eps)
        daily = {}
        for e in ents:
            r = ret_x(e, exit_variant)
            if r is None:
                continue
            nets.append(r - COST)
            units_total += e["units"]
        if not nets:
            print(f"{name}: no labeled entries")
            return
        nets = np.array(nets)
        eps_traded = len({(e["ticker"], str(e["et_date"])[:10]) for e in ents if ret_x(e, exit_variant) is not None})
        days = len({str(e["et_date"])[:10] for e in ents})
        print(f"{name:<26} entries={len(nets):>5} ({len(nets)/days:.1f}/day) net/unit={nets.mean():+.3%} "
              f"wr={(nets>0).mean():.3f} net/episode={nets.sum()/max(eps_traded,1):+.3%} "
              f"eps={eps_traded}")
        mo = {}
        for e in ents:
            r = ret_x(e, exit_variant)
            if r is not None:
                mo.setdefault(e["month"], []).append(r - COST)
        print(f"{'':<26} monthly/unit: " + " ".join(f"{k[-2:]}:{np.mean(v):+.2%}" for k, v in sorted(mo.items())))

    print(f"=== {a.years} composite stream (derivation-mode) ===")
    for s in ["E1_one", "E2_cycle3", "E3_persist", "E5_rvol2"]:
        report(s, entries_for(s))

    if a.frozen_structure is None:
        print("\n--- exit variants on E3_persist entries ---")
        for x in ["X_60m", "X_death", "X_30m", "X_15m"]:
            report(f"E3 {x}", entries_for("E3_persist"), exit_variant=x)
        print("\n--- within-episode timing (E3, X_60m) ---")
        ents = [e for e in entries_for("E3_persist") if e["fwd60_t1entry"] is not None]
        def bucket_rows(keyfn, labels):
            d = {}
            for e in ents:
                d.setdefault(keyfn(e), []).append(e["fwd60_t1entry"] - COST)
            for lb in labels:
                v = d.get(lb)
                if v:
                    print(f"{str(lb):<14} n={len(v):>5} net={np.mean(v):+.3%} wr={(np.array(v)>0).mean():.3f}")
        bucket_rows(lambda e: e["cycle"], [1, 2, 3])
        bucket_rows(lambda e: min(e["min_since_first"] // 30, 4) if e["min_since_first"] < 120 else 5,
                    [0, 1, 2, 3, 4, 5])
        print("\n--- rvol tier at entry (E3, X_60m) ---")
        e8 = [e for e in ents if e["rvol"] is not None and e["rvol"] > 8]
        e4 = [e for e in ents if e["rvol"] is not None and 4 < e["rvol"] <= 8]
        for tag, v in [("rvol 4-8", e4), ("rvol >8", e8)]:
            if v:
                rr = np.array([x["fwd60_t1entry"] - COST for x in v])
                print(f"{tag:<14} n={len(v):>5} net={rr.mean():+.3%} wr={(rr>0).mean():.3f}")
    else:
        s, x = a.frozen_structure, a.frozen_exit
        print(f"\n=== {a.years} FROZEN replay: {s} + {x} ===")
        report(f"{s}+{x}", entries_for(s), exit_variant=x)
        report(f"{s} X_death", entries_for(s), exit_variant="X_death")
        # economics: minute-by-minute concurrency with global cap 10, per-name 1 pos, $10k units
        cap, unit_usd = 10, 10_000
        evs = sorted(entries_for(s), key=lambda e: (str(e["et_date"])[:10], e["tod_min"]))
        open_pos = {}
        daily = {}
        skipped = 0
        for e in evs:
            d = str(e["et_date"])[:10]
            t = e["tod_min"]
            open_pos = {k: v for k, v in open_pos.items() if v > t}
            if e["ticker"] in open_pos:
                continue
            if len(open_pos) >= cap:
                skipped += 1
                continue
            r = ret_x(e, x)
            if r is None:
                open_pos[e["ticker"]] = t + HOLD + 1
                continue
            open_pos[e["ticker"]] = t + HOLD + 1
            daily.setdefault(d, 0.0)
            daily[d] += (r - COST) * unit_usd * e["units"]
        vals = np.array(list(daily.values()))
        print(f"\neconomics @{unit_usd:,}/unit cap={cap}: trade-days={len(vals)} "
              f"mean/day=${vals.mean():+,.0f} p10=${np.percentile(vals,10):+,.0f} "
              f"p90=${np.percentile(vals,90):+,.0f} pos-days≈{len(evs)/len(vals):.0f} skipped={skipped}")


if __name__ == "__main__":
    main()
