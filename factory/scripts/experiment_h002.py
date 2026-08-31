"""H002 HOD breakout — ponytail minimal. Chronological, no lookahead."""
import argparse, json
from pathlib import Path
from datetime import time
import polars as pl

HORIZONS=[1,3,5,15,30,60]

def time_bucket(et):
    hm=et.hour*60+et.minute
    if hm<600: return "09:30-10:00"
    h=et.hour
    return f"{h:02d}:00-{h+1:02d}:00" if h<15 else "15:00-16:00"

def metrics_for(sub, cost):
    if sub.height==0: return {}
    out={}
    for h in HORIZONS:
        c=f"fwd_ret_{h}m"
        if c not in sub.columns: continue
        s=sub[c].drop_nulls()
        if s.len()==0: continue
        wr=(s>0).sum()/s.len()
        avg=s.mean(); med=s.median()
        wins=s.filter(s>0); losses=s.filter(s<=0)
        avg_w=wins.mean() if wins.len()>0 else 0
        avg_l=losses.mean() if losses.len()>0 else 0
        exp=wr*avg_w+(1-wr)*avg_l
        exp_net=exp-cost
        wr_net=(s>cost).sum()/s.len()
        print(f"  h={h}m: n={s.len()} wr={wr:.3f} wr_net={wr_net:.3f} avg={avg:.4f} med={med:.4f} exp={exp:.4f} exp_net={exp_net:.4f} avg_win={avg_w:.4f} avg_loss={avg_l:.4f}")
        out[f"h{h}"]={"n":s.len(),"wr":float(wr),"wr_net":float(wr_net),"avg":float(avg),"median":float(med),"exp":float(exp),"exp_net":float(exp_net),"avg_win":float(avg_w),"avg_loss":float(avg_l)}
    return out

def run(clean_path, ranked_dir, cost_bps, min_gain_pct, out_path):
    cost=cost_bps*2/10000
    print(f"Loading clean {clean_path} ...")
    clean=pl.read_parquet(clean_path)
    clean=clean.with_columns(
        pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("et"),
        pl.col("timestamp").dt.convert_time_zone("America/New_York").dt.date().alias("et_date"),
    )
    # RTH filter 09:30-16:00 ET, price floor 2.0 to match rank_day
    clean=clean.filter((pl.col("et").dt.hour().cast(pl.Int32)*60+pl.col("et").dt.minute().cast(pl.Int32)>=570)&(pl.col("et").dt.hour().cast(pl.Int32)*60+pl.col("et").dt.minute().cast(pl.Int32)<960))
    clean=clean.filter(pl.col("close")>=2.0)
    clean=clean.sort(["ticker","timestamp"])
    # prior close = last close before et_date per ticker
    # need distinct et_dates sorted for prior lookup
    print("Computing prior closes ...")
    # build prior_close via groupby: for each ticker, sort by timestamp, then for each date take prior day last close
    # simpler: use window: shift cumulative? Instead replicate rank_day logic: split not needed if we do per-date last.
    # We'll compute per (ticker,et_date) last close, then join shifted.
    daily_last=clean.sort("timestamp").group_by(["ticker","et_date"]).agg(pl.col("close").last().alias("last_close"), pl.col("timestamp").max().alias("max_ts"))
    daily_last=daily_last.sort(["ticker","et_date"])
    # shift last_close to get prior_close
    daily_last=daily_last.with_columns(pl.col("last_close").shift(1).over("ticker").alias("prior_close"))
    daily_last=daily_last.filter(pl.col("prior_close").is_not_null())
    # join prior_close back
    clean=clean.join(daily_last.select(["ticker","et_date","prior_close"]), on=["ticker","et_date"], how="inner")
    if clean.height==0:
        print("No bars with prior close"); return
    clean=clean.with_columns(((pl.col("close")-pl.col("prior_close"))/pl.col("prior_close")*100).alias("pct_gain"))
    clean=clean.sort(["ticker","et_date","timestamp"])

    ranked_dir=Path(ranked_dir)  # used later for join

    # vectorized HOD breakout detection (polars windows)
    print("Detecting HOD breakouts (vectorized) ...")
    clean=clean.sort(["ticker","et_date","timestamp"])
    # running max high per ticker-day, shifted to get prior HOD
    clean=clean.with_columns(
        pl.col("high").cum_max().over(["ticker","et_date"]).alias("hod_incl"),
    )
    clean=clean.with_columns(
        pl.col("hod_incl").shift(1).over(["ticker","et_date"]).alias("hod_before"),
    )
    # signal condition
    sig=clean.filter(pl.col("hod_before").is_not_null() & (pl.col("pct_gain")>=min_gain_pct) & (pl.col("close")>pl.col("hod_before")))
    print(f" raw signals before dedup: {sig.height}")
    # dedup within 5 min per ticker-day: keep first, drop subsequent within 5 bars
    # use timestamp diff
    if sig.height>0:
        sig=sig.sort(["ticker","et_date","timestamp"])
        # compute gap to prior signal in same group
        sig=sig.with_columns(
            (pl.col("timestamp").diff().over(["ticker","et_date"]).dt.total_seconds()/60).alias("gap_min")
        )
        # iterative dedup: need to handle chain; use python loop per group but groups are tiny now (only signals)
        rows_raw=sig.to_dicts()
        # group by (ticker,et_date)
        from collections import defaultdict
        grouped=defaultdict(list)
        for r in rows_raw:
            grouped[(r["ticker"], r["et_date"])].append(r)
        rows=[]
        for key, lst in grouped.items():
            lst.sort(key=lambda x: x["timestamp"])
            last_ts=None
            for r in lst:
                ts=r["timestamp"]
                if last_ts is not None and (ts - last_ts).total_seconds()/60 < 5:
                    continue
                # forward returns via exact timestamp lookup within clean (use dict for this ticker-day)
                rows.append(r)
                last_ts=ts
        # build signals df from filtered rows
        sig=pl.DataFrame(rows) if rows else pl.DataFrame([])
        print(f" after 5m dedup: {sig.height}")
        if sig.height==0:
            print("No HOD breakout signals after dedup")
            out_path=Path(out_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            pl.DataFrame({}).write_parquet(out_path)
            summary={"n_signals":0,"min_gain_pct":min_gain_pct,"cost_bps_per_side":cost_bps}
            jpath=out_path.parent/"h002_summary.json"
            import json as _js
            with open(jpath,"w") as f: _js.dump(summary,f,indent=2,default=str)
            print(f"Wrote {out_path} 0 signals, {jpath}")
            return summary
        # need to compute forward returns and MFE/MAE for each signal
        # build lookup per ticker-day for fast index-based retrieval (clean is sorted, we can use join via timestamp+delta)
        # collect needed fwd timestamps and join
        # For each horizon, join on ticker + fwd_timestamp
        sig=sig.with_columns(pl.col("close").alias("sig_close"), pl.col("high").alias("sig_high"), pl.col("low").alias("sig_low"))
        # prepare base sig with row index for joins
        sig=sig.with_row_index("__sig_idx")
        # forward returns via exact timestamp join: fwd_ts = timestamp + h minutes
        for h in HORIZONS:
            fwd_map=clean.select(["ticker","timestamp","close"]).rename({"timestamp":"fwd_ts","close":f"_fwd_close_{h}"})
            sig=sig.with_columns((pl.col("timestamp")+pl.duration(minutes=h)).cast(pl.Datetime("ns","UTC")).alias("fwd_ts"))
            sig=sig.join(fwd_map, on=["ticker","fwd_ts"], how="left")
            sig=sig.with_columns(((pl.col(f"_fwd_close_{h}")-pl.col("sig_close"))/pl.col("sig_close")).alias(f"fwd_ret_{h}m")).drop(["fwd_ts",f"_fwd_close_{h}"])
        # MFE/MAE over next 60m: only for needed ticker-days
        needed=set(zip(sig["ticker"].to_list(), sig["et_date"].to_list()))
        clean_needed=clean.filter(pl.struct(["ticker","et_date"]).is_in(list(needed)) if False else pl.col("ticker").is_in([k[0] for k in needed]))  # placeholder, filter properly below
        # proper filter via join
        need_df=pl.DataFrame({"ticker":[k[0] for k in needed],"et_date":[k[1] for k in needed]}).unique()
        clean_needed=clean.join(need_df, on=["ticker","et_date"], how="inner")
        clean_groups={}
        for (ticker, et_date), sub in clean_needed.group_by(["ticker","et_date"]):
            sub=sub.sort("timestamp")
            clean_groups[(ticker,et_date)]=(sub["timestamp"].to_list(), sub["high"].to_list(), sub["low"].to_list())
        mfe_list=[]; mae_list=[]
        for r in sig.iter_rows(named=True):
            key=(r["ticker"], r["et_date"])
            ts_list, highs, lows = clean_groups.get(key, ([],[ ],[]))
            try:
                idx=ts_list.index(r["timestamp"])
            except ValueError:
                mfe_list.append(None); mae_list.append(None); continue
            end=min(idx+60, len(ts_list)-1)
            if end>idx:
                wh=max(highs[idx+1:end+1]); wl=min(lows[idx+1:end+1])
                mfe_list.append((wh-r["sig_close"])/r["sig_close"] if r["sig_close"] else None)
                mae_list.append((wl-r["sig_close"])/r["sig_close"] if r["sig_close"] else None)
            else:
                mfe_list.append(None); mae_list.append(None)
        sig=sig.with_columns([pl.Series("mfe_60m", mfe_list), pl.Series("mae_60m", mae_list)])
        # finalize columns
        sig=sig.with_columns(
            pl.col("et").map_elements(time_bucket, return_dtype=pl.String).alias("time_bucket"),
            pl.when(pl.col("pct_gain")<10).then(pl.lit("<10%")).when(pl.col("pct_gain")<20).then(pl.lit("10-20%")).otherwise(pl.lit("20%+")).alias("gain_bin"),
            pl.col("sig_close").alias("close"),
        )
        # prepare rows for downstream DataFrame (reuse sig as df)
        df=sig.select(["timestamp","et","ticker","et_date","close","pct_gain","hod_before","time_bucket","gain_bin","mfe_60m","mae_60m"] + [f"fwd_ret_{h}m" for h in HORIZONS] + ["__sig_idx"])
        # need to keep sig for rank join; rename df to sig-like and continue below via df variable
        # trick: set df and skip old rows path
        # create dummy rows to satisfy downstream (we already have df)
        rows=df.to_dicts()  # marker to skip old loop
        # we will handle df directly after this block
        _vectorized=True
    else:
        print("No HOD breakout signals found")
        df=pl.DataFrame([])
        out_path=Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"timestamp":[]}).write_parquet(out_path)
        summary={"n_signals":0,"min_gain_pct":min_gain_pct,"cost_bps_per_side":cost_bps}
        jpath=out_path.parent/"h002_summary.json"
        import json as _js
        with open(jpath,"w") as f: _js.dump(summary,f,indent=2,default=str)
        print(f"Wrote {out_path} 0 signals, {jpath}")
        return summary
    # vectorized path: df already set; rows is non-empty marker, skip re-creation
    if "_vectorized" in locals() and _vectorized:
        pass  # df already from vectorized block
    else:
        if not rows:
            print("No HOD breakout signals found")
            df=pl.DataFrame([])
            out_path=Path(out_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            pl.DataFrame({"timestamp":[]}).write_parquet(out_path)
            summary={"n_signals":0,"min_gain_pct":min_gain_pct,"cost_bps_per_side":cost_bps}
            jpath=out_path.parent/"h002_summary.json"
            with open(jpath,"w") as f: json.dump(summary,f,indent=2,default=str)
            print(f"Wrote {out_path} 0 signals, {jpath}")
            return summary
        df=pl.DataFrame(rows)
    # join rank/dollar_volume from ranked files (filtered to signal timestamps)
    try:
        sig_ts = df["timestamp"].unique().to_list()
        ranked_parts=[]
        for f in sorted(ranked_dir.glob("ranked_*.parquet")):
            part=pl.scan_parquet(f).filter(pl.col("timestamp").is_in(sig_ts)).select(["timestamp","ticker","rank","dollar_volume"]).collect()
            if part.height>0:
                ranked_parts.append(part)
        if ranked_parts:
            ranked_all=pl.concat(ranked_parts)
            df=df.join(ranked_all, on=["timestamp","ticker"], how="left")
        def _rb(r):
            if r is None: return "21+"
            try:
                if r<=5: return "1-5"
                if r<=10: return "6-10"
                if r<=20: return "11-20"
            except: pass
            return "21+"
        if "rank" not in df.columns:
            df=df.with_columns(pl.lit(None).alias("rank"))
        df=df.with_columns(pl.col("rank").map_elements(_rb, return_dtype=pl.String).alias("rank_bin"))
        if "dollar_volume" not in df.columns:
            df=df.with_columns(pl.lit(None).cast(pl.Float64).alias("dollar_volume"))
    except Exception as e:
        print(f"rank join failed: {e}")
        if "rank" not in df.columns: df=df.with_columns(pl.lit(None).alias("rank"))
        df=df.with_columns(pl.lit("21+").alias("rank_bin"))
        if "dollar_volume" not in df.columns: df=df.with_columns(pl.lit(None).cast(pl.Float64).alias("dollar_volume"))
    # dollar volume quantile bins
    if "dollar_volume" in df.columns and df["dollar_volume"].drop_nulls().len()>0:
        # compute quantiles
        qs=df["dollar_volume"].drop_nulls().quantile(0.33), df["dollar_volume"].drop_nulls().quantile(0.66)
        q1,q2=qs
        def vol_bin(v):
            if v is None or v!=v: return "unknown"
            if v<=q1: return "low"
            if v<=q2: return "mid"
            return "high"
        df=df.with_columns(pl.col("dollar_volume").map_elements(vol_bin, return_dtype=pl.String).alias("vol_bin"))
    else:
        df=df.with_columns(pl.lit("unknown").alias("vol_bin"))

    print(f"\nTotal HOD breakout signals: {df.height} (min_gain={min_gain_pct}%, dedup 5m)")
    print(f"Dates: {df['et_date'].min()} .. {df['et_date'].max()} ({df['et_date'].n_unique()} days)")

    print("\n=== ALL signals ===")
    all_m=metrics_for(df, cost)

    print("\n=== by time_bucket ===")
    seg_tb={}
    for tb in sorted(df["time_bucket"].unique().to_list()):
        print(f" {tb}:")
        sub=df.filter(pl.col("time_bucket")==tb)
        seg_tb[tb]=metrics_for(sub, cost)

    print("\n=== by gain_bin ===")
    seg_gain={}
    for b in ["<10%","10-20%","20%+"]:
        print(f" {b}:")
        sub=df.filter(pl.col("gain_bin")==b)
        seg_gain[b]=metrics_for(sub, cost)

    print("\n=== by rank_bin ===")
    seg_rank={}
    for b in ["1-5","6-10","11-20","21+"]:
        print(f" {b}:")
        sub=df.filter(pl.col("rank_bin")==b)
        seg_rank[b]=metrics_for(sub, cost)

    print("\n=== by vol_bin ===")
    seg_vol={}
    for b in ["low","mid","high","unknown"]:
        sub=df.filter(pl.col("vol_bin")==b)
        if sub.height==0: continue
        print(f" {b}:")
        seg_vol[b]=metrics_for(sub, cost)

    # chronological split
    dates_sorted=sorted(df["et_date"].unique().to_list())
    # spec: train first 14 trading days (2025-07-02 to 2025-07-22) vs test last 7
    # calendar has 21 ranked days; map to actual dates
    split=14
    train_dates=set(dates_sorted[:split])
    test_dates=set(dates_sorted[split:])
    print(f"\n=== Chronological split: train first {split} days / test last {len(dates_sorted)-split} ===")
    print(f" dates: {dates_sorted[0]} .. {dates_sorted[-1]} ({len(dates_sorted)} days)")
    print(f" train: {len(train_dates)} days n={df.filter(pl.col('et_date').is_in(list(train_dates))).height}")
    train_m=metrics_for(df.filter(pl.col("et_date").is_in(list(train_dates))), cost)
    print(f" test: {len(test_dates)} days n={df.filter(pl.col('et_date').is_in(list(test_dates))).height}")
    test_m=metrics_for(df.filter(pl.col("et_date").is_in(list(test_dates))), cost)

    out_path=Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out_path)
    print(f"\nWrote {out_path} ({df.height} signals)")

    summary={
        "n_signals": df.height,
        "min_gain_pct": min_gain_pct,
        "cost_bps_per_side": cost_bps,
        "roundtrip_cost": cost,
        "all_metrics": all_m,
        "by_time_bucket": seg_tb,
        "by_gain": seg_gain,
        "by_rank": seg_rank,
        "by_vol": seg_vol,
        "train_metrics": train_m,
        "test_metrics": test_m,
        "dates": [str(d) for d in dates_sorted],
        "train_dates": [str(d) for d in sorted(train_dates)],
        "test_dates": [str(d) for d in sorted(test_dates)],
    }
    jpath=out_path.parent/"h002_summary.json"
    # also ensure factory/artifacts path
    jpath2=Path("factory/artifacts/h002_summary.json")
    jpath2.parent.mkdir(parents=True, exist_ok=True)
    with open(jpath,"w") as f: json.dump(summary,f,indent=2,default=str)
    if jpath!=jpath2:
        with open(jpath2,"w") as f: json.dump(summary,f,indent=2,default=str)
    print(f"Wrote {jpath} and {jpath2}")
    return summary

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--clean", type=Path, required=True)
    p.add_argument("--ranked-dir", type=Path, default=Path("factory/artifacts"))
    p.add_argument("--cost-bps", type=float, default=10)
    p.add_argument("--min-gain-pct", type=float, default=5)
    p.add_argument("--out", type=Path, default=Path("factory/artifacts/h002_results.parquet"))
    a=p.parse_args()
    run(a.clean, a.ranked_dir, a.cost_bps, a.min_gain_pct, a.out)

if __name__=="__main__":
    main()
