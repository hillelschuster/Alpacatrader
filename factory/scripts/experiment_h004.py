"""H004 micro-pullback 0.5-1% above VWAP + reclaim — ponytail minimal."""
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

def run(clean_path, cost_bps, out_path):
    cost=cost_bps*2/10000
    print(f"Loading clean {clean_path} ...")
    clean=pl.read_parquet(clean_path)
    clean=clean.with_columns(
        pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("et"),
        pl.col("timestamp").dt.convert_time_zone("America/New_York").dt.date().alias("et_date"),
    )
    # RTH 09:30-16:00 ET, price floor 2.0
    clean=clean.filter((pl.col("et").dt.hour().cast(pl.Int32)*60+pl.col("et").dt.minute().cast(pl.Int32)>=570)&(pl.col("et").dt.hour().cast(pl.Int32)*60+pl.col("et").dt.minute().cast(pl.Int32)<960))
    clean=clean.filter(pl.col("close")>=2.0)
    clean=clean.sort(["ticker","timestamp"])
    # prior close for pct_gain
    print("Computing prior closes ...")
    daily_last=clean.sort("timestamp").group_by(["ticker","et_date"]).agg(pl.col("close").last().alias("last_close"))
    daily_last=daily_last.sort(["ticker","et_date"])
    daily_last=daily_last.with_columns(pl.col("last_close").shift(1).over("ticker").alias("prior_close"))
    daily_last=daily_last.filter(pl.col("prior_close").is_not_null())
    clean=clean.join(daily_last.select(["ticker","et_date","prior_close"]), on=["ticker","et_date"], how="inner")
    if clean.height==0:
        print("No bars with prior close"); return
    clean=clean.with_columns(((pl.col("close")-pl.col("prior_close"))/pl.col("prior_close")*100).alias("pct_gain"))
    clean=clean.sort(["ticker","et_date","timestamp"])
    # VWAP: cum(close*volume)/cum(volume) per ticker-day
    # ponytail: close proxy for typical price, true VWAP if edge found
    print("Computing VWAP ...")
    clean=clean.with_columns(
        (pl.col("close")*pl.col("volume")).cum_sum().over(["ticker","et_date"]).alias("cum_pv"),
        pl.col("volume").cum_sum().over(["ticker","et_date"]).alias("cum_v"),
    )
    clean=clean.with_columns((pl.col("cum_pv")/pl.col("cum_v")).alias("vwap"))
    # session high
    clean=clean.with_columns(pl.col("high").cum_max().over(["ticker","et_date"]).alias("session_high"))
    clean=clean.with_columns(((pl.col("session_high")-pl.col("close"))/pl.col("session_high")).alias("pullback_depth"))

    # Detect signals — candidate-filtered per-group walk (faster)
    print("Detecting micro-pullback reclaim signals ...", flush=True)
    # candidate reclaim bars: depth<=0.001 & above VWAP & gain>=2%
    # filter first to reduce groups
    cand_mask = (pl.col("pullback_depth")<=0.001) & (pl.col("close")>pl.col("vwap")) & (pl.col("pct_gain")>=2.0)
    cand_tickers = clean.filter(cand_mask).select(["ticker","et_date"]).unique()
    print(f" candidate ticker-days: {cand_tickers.height}", flush=True)
    if cand_tickers.height==0:
        signals=[]
    else:
        clean_cand = clean.join(cand_tickers, on=["ticker","et_date"], how="inner").sort(["ticker","et_date","timestamp"])
        signals=[]
        import polars as _pl
        # iterate only candidate groups
        for (ticker, et_date), sub in clean_cand.group_by(["ticker","et_date"]):
            sub=sub.sort("timestamp")
            ts_list=sub["timestamp"].to_list()
            close_list=sub["close"].to_list()
            high_list=sub["high"].to_list()
            low_list=sub["low"].to_list()
            vwap_list=sub["vwap"].to_list()
            sh_list=sub["session_high"].to_list()
            gain_list=sub["pct_gain"].to_list()
            et_list=sub["et"].to_list()
            depth_list=sub["pullback_depth"].to_list()
            n=len(ts_list)
            last_high=None
            pullback_seen=False
            best_depth=None
            last_signal_ts=None
            for i in range(n):
                sh=sh_list[i]
                c=close_list[i]
                depth=depth_list[i]
                if last_high is None or sh > last_high+1e-9:
                    last_high=sh
                    pullback_seen=False
                    best_depth=None
                if 0.005 <= depth <= 0.01:
                    pullback_seen=True
                    if best_depth is None or depth>best_depth:
                        best_depth=depth
                if depth is not None and depth > 0.01:
                    pullback_seen=False
                    best_depth=None
                if pullback_seen and c > vwap_list[i] and gain_list[i] >= 2.0 and depth is not None and depth <= 0.001:
                    ts=ts_list[i]
                    if last_signal_ts is not None and (ts - last_signal_ts).total_seconds()/60 < 5:
                        continue
                    signals.append({
                        "timestamp": ts, "et": et_list[i], "ticker": ticker, "et_date": et_date,
                        "close": c, "vwap": vwap_list[i], "session_high": sh,
                        "pct_gain": gain_list[i], "pullback_depth": best_depth if best_depth is not None else depth,
                        "high": high_list[i], "low": low_list[i],
                    })
                    last_signal_ts=ts
                    pullback_seen=False
                    best_depth=None
    print(f" signals found: {len(signals)}", flush=True)
    if not signals:
        print("No signals")
        out_path=Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({}).write_parquet(out_path)
        summary={"n_signals":0,"cost_bps_per_side":cost_bps,"roundtrip_cost":cost}
        jpath=out_path.parent/"h004_summary.json"
        with open(jpath,"w") as f: json.dump(summary,f,indent=2,default=str)
        print(f"Wrote {out_path} {jpath}")
        return summary

    df=pl.DataFrame(signals)
    df=df.with_columns(
        pl.col("et").map_elements(time_bucket, return_dtype=pl.String).alias("time_bucket"),
        pl.when(pl.col("pct_gain")<10).then(pl.lit("<10%")).when(pl.col("pct_gain")<20).then(pl.lit("10-20%")).otherwise(pl.lit("20%+")).alias("gain_bin"),
        pl.when(pl.col("pullback_depth")<0.007).then(pl.lit("0.5-0.7%")).otherwise(pl.lit("0.7-1.0%")).alias("depth_bin"),
    )

    # forward returns via join
    df=df.with_row_index("__idx")
    sig_close=df.select(["__idx","ticker","timestamp","close"]).rename({"close":"sig_close"})
    # need clean lookup
    for h in HORIZONS:
        fwd_map=clean.select(["ticker","timestamp","close"]).rename({"timestamp":"fwd_ts","close":f"_fwd_{h}"})
        tmp=df.with_columns((pl.col("timestamp")+pl.duration(minutes=h)).cast(pl.Datetime("ns","UTC")).alias("fwd_ts"))
        tmp=tmp.join(fwd_map, on=["ticker","fwd_ts"], how="left")
        tmp=tmp.with_columns(((pl.col(f"_fwd_{h}")-pl.col("close"))/pl.col("close")).alias(f"fwd_ret_{h}m")).drop(["fwd_ts",f"_fwd_{h}"])
        df=tmp
    # MFE/MAE 60m
    clean_needed_groups={}
    # build need set
    need_df=df.select(["ticker","et_date"]).unique()
    clean_needed=clean.join(need_df, on=["ticker","et_date"], how="inner")
    for (ticker, et_date), sub in clean_needed.group_by(["ticker","et_date"]):
        sub=sub.sort("timestamp")
        clean_needed_groups[(ticker,et_date)]=(sub["timestamp"].to_list(), sub["high"].to_list(), sub["low"].to_list())
    mfe=[]; mae=[]
    for r in df.iter_rows(named=True):
        key=(r["ticker"], r["et_date"])
        tup=clean_needed_groups.get(key)
        if not tup:
            mfe.append(None); mae.append(None); continue
        ts_list, highs, lows = tup
        try: idx=ts_list.index(r["timestamp"])
        except: mfe.append(None); mae.append(None); continue
        end=min(idx+60, len(ts_list)-1)
        if end>idx:
            wh=max(highs[idx+1:end+1]); wl=min(lows[idx+1:end+1])
            mfe.append((wh-r["close"])/r["close"] if r["close"] else None)
            mae.append((wl-r["close"])/r["close"] if r["close"] else None)
        else:
            mfe.append(None); mae.append(None)
    df=df.with_columns([pl.Series("mfe_60m", mfe), pl.Series("mae_60m", mae)])

    # rank join if available
    ranked_dir=Path("factory/artifacts")
    try:
        sig_ts=df["timestamp"].unique().to_list()
        parts=[]
        for f in sorted(ranked_dir.glob("ranked_*.parquet")):
            part=pl.scan_parquet(f).filter(pl.col("timestamp").is_in(sig_ts)).select(["timestamp","ticker","rank"]).collect()
            if part.height>0: parts.append(part)
        if parts:
            ranked_all=pl.concat(parts)
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
    except Exception as e:
        print(f"rank join failed: {e}")
        if "rank" not in df.columns: df=df.with_columns(pl.lit(None).alias("rank"))
        df=df.with_columns(pl.lit("21+").alias("rank_bin"))

    print(f"\nTotal signals: {df.height}")
    print(f"Dates: {df['et_date'].min()} .. {df['et_date'].max()} ({df['et_date'].n_unique()} days)")

    print("\n=== ALL signals ===")
    all_m=metrics_for(df, cost)

    print("\n=== by time_bucket ===")
    seg_tb={}
    for tb in sorted(df["time_bucket"].unique().to_list()):
        print(f" {tb}:")
        sub=df.filter(pl.col("time_bucket")==tb)
        seg_tb[tb]=metrics_for(sub, cost)

    print("\n=== by pullback depth ===")
    seg_depth={}
    for b in ["0.5-0.7%","0.7-1.0%"]:
        print(f" {b}:")
        sub=df.filter(pl.col("depth_bin")==b)
        seg_depth[b]=metrics_for(sub, cost)

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

    dates_sorted=sorted(df["et_date"].unique().to_list())
    split=14
    train_dates=set(dates_sorted[:split])
    test_dates=set(dates_sorted[split:])
    print(f"\n=== Chronological split: train first {split} / test last {len(dates_sorted)-split} ===")
    print(f" dates: {dates_sorted[0]} .. {dates_sorted[-1]} ({len(dates_sorted)} days)")
    print(f" train: {len(train_dates)} days n={df.filter(pl.col('et_date').is_in(list(train_dates))).height}")
    train_m=metrics_for(df.filter(pl.col("et_date").is_in(list(train_dates))), cost)
    print(f" test: {len(test_dates)} days n={df.filter(pl.col('et_date').is_in(list(test_dates))).height}")
    test_m=metrics_for(df.filter(pl.col("et_date").is_in(list(test_dates))), cost)

    out_path=Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.drop("__idx").write_parquet(out_path)
    print(f"\nWrote {out_path} ({df.height} signals)")

    summary={
        "n_signals": df.height,
        "cost_bps_per_side": cost_bps,
        "roundtrip_cost": cost,
        "all_metrics": all_m,
        "by_time_bucket": seg_tb,
        "by_depth": seg_depth,
        "by_gain": seg_gain,
        "by_rank": seg_rank,
        "train_metrics": train_m,
        "test_metrics": test_m,
        "dates": [str(d) for d in dates_sorted],
        "train_dates": [str(d) for d in sorted(train_dates)],
        "test_dates": [str(d) for d in sorted(test_dates)],
    }
    jpath=out_path.parent/"h004_summary.json"
    with open(jpath,"w") as f: json.dump(summary,f,indent=2,default=str)
    # also canonical
    j2=Path("factory/artifacts/h004_summary.json")
    if jpath!=j2:
        j2.parent.mkdir(parents=True, exist_ok=True)
        with open(j2,"w") as f: json.dump(summary,f,indent=2,default=str)
    print(f"Wrote {jpath}")
    return summary

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--clean", type=Path, required=True)
    p.add_argument("--cost-bps", type=float, default=10)
    p.add_argument("--out", type=Path, default=Path("factory/artifacts/h004_results.parquet"))
    a=p.parse_args()
    run(a.clean, a.cost_bps, a.out)

if __name__=="__main__":
    main()
