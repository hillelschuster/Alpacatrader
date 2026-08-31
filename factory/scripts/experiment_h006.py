"""H006 VWAP fade >8% above VWAP — ponytail minimal."""
import argparse, json
from pathlib import Path
import polars as pl

HORIZONS=[1,3,5,15,30,60]
BUCKETS=["<3%","3-5%","5-8%","8-12%","12%+"]

def bucket_vwap(d):
    if d is None: return None
    if d < 3: return "<3%"
    if d < 5: return "3-5%"
    if d < 8: return "5-8%"
    if d < 12: return "8-12%"
    return "12%+"

def metrics_both(sub, cost):
    if sub.height==0: return {}
    out={}
    for h in HORIZONS:
        c=f"fwd_ret_{h}m"
        if c not in sub.columns: continue
        s=sub[c].drop_nulls()
        if s.len()==0: continue
        # long metrics
        wr_long=(s>0).sum()/s.len()
        wr_long_net=(s>cost).sum()/s.len()
        avg=float(s.mean()); med=float(s.median())
        wins=s.filter(s>0); losses=s.filter(s<=0)
        aw=float(wins.mean()) if wins.len()>0 else 0
        al=float(losses.mean()) if losses.len()>0 else 0
        exp_long=wr_long*aw+(1-wr_long)*al
        # short = fade: win when s<0
        wr_short=(s<0).sum()/s.len()
        wr_short_net=(s<-cost).sum()/s.len()
        # short avg ret = -s mean; expectancy short = -avg - cost
        exp_short_net = -avg - cost
        exp_long_net = exp_long - cost
        print(f"  h={h}m n={s.len()} avg={avg:.4%} med={med:.4%} long(wr={wr_long:.2%} wr_net={wr_long_net:.2%} exp_net={exp_long_net:.4%}) short(wr={wr_short:.2%} wr_net={wr_short_net:.2%} exp_net={exp_short_net:.4%})")
        out[f"h{h}"]={"n":s.len(),"avg":avg,"median":med,"wr_long":float(wr_long),"wr_long_net":float(wr_long_net),"exp_long":float(exp_long),"exp_long_net":float(exp_long_net),"wr_short":float(wr_short),"wr_short_net":float(wr_short_net),"exp_short_net":float(exp_short_net),"avg_win":aw,"avg_loss":al}
    return out

def run(events, clean_dir, cost_bps, threshold, out_path):
    cost=cost_bps*2/10000
    print(f"Loading events {events}")
    ev=pl.read_parquet(events)
    print(f" events: {ev.height} dates {ev.select(pl.col('timestamp').cast(pl.Date).n_unique()).item()} tickers {ev.select(pl.col('ticker').n_unique()).item()}")
    # load clean dir
    clean_dir=Path(clean_dir)
    files=sorted(clean_dir.glob("clean_ohlcv_2025-*.parquet"))
    if not files: files=sorted(clean_dir.glob("*.parquet"))
    print(f"Loading clean {files}")
    clean=pl.concat([pl.read_parquet(f) for f in files])
    clean=clean.with_columns(
        pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("et"),
        pl.col("timestamp").dt.convert_time_zone("America/New_York").dt.date().alias("et_date"),
    )
    # RTH only, price floor
    clean=clean.filter((pl.col("et").dt.hour().cast(pl.Int32)*60+pl.col("et").dt.minute().cast(pl.Int32)>=570)&(pl.col("et").dt.hour().cast(pl.Int32)*60+pl.col("et").dt.minute().cast(pl.Int32)<960))
    clean=clean.filter(pl.col("close")>=2.0)
    clean=clean.sort(["ticker","timestamp"])
    # VWAP per ticker-day: cum(close*volume)/cum(volume)
    print("Computing VWAP...")
    clean=clean.with_columns(
        (pl.col("close")*pl.col("volume")).cum_sum().over(["ticker","et_date"]).alias("cum_pv"),
        pl.col("volume").cum_sum().over(["ticker","et_date"]).alias("cum_v"),
    )
    clean=clean.with_columns((pl.col("cum_pv")/pl.col("cum_v")).alias("vwap"))
    # join vwap to events at exact timestamp
    vwap_lookup=clean.select(["ticker","timestamp","vwap"])
    ev=ev.join(vwap_lookup, on=["ticker","timestamp"], how="left")
    # if missing vwap (no clean bar? e.g. halt), drop
    before=ev.height
    ev=ev.filter(pl.col("vwap").is_not_null() & (pl.col("vwap")>0))
    print(f" vwap join: {before}->{ev.height} (dropped {before-ev.height} no vwap)")
    ev=ev.with_columns(((pl.col("close")-pl.col("vwap"))/pl.col("vwap")*100).alias("vwap_dist"))
    ev=ev.with_columns(pl.col("vwap_dist").map_elements(bucket_vwap, return_dtype=pl.String).alias("vwap_bucket"))
    # ensure et_date
    if "et_date" not in ev.columns:
        ev=ev.with_columns(pl.col("timestamp").dt.convert_time_zone("America/New_York").dt.date().alias("et_date"))
    # ensure time_bucket, rank, pct_gain exist
    if "time_bucket" not in ev.columns:
        ev=ev.with_columns(pl.col("et").map_elements(lambda x: f"{x.hour:02d}:{x.minute:02d}", return_dtype=pl.String).alias("time_bucket"))
    # recompute forward returns via exact join (recompute to be safe)
    print("Computing forward returns via clean join...")
    for h in HORIZONS:
        fwd_map=clean.select(["ticker","timestamp","close"]).rename({"timestamp":"fwd_ts","close":f"_fwd_{h}"})
        tmp=ev.with_columns((pl.col("timestamp")+pl.duration(minutes=h)).cast(pl.Datetime("ns","UTC")).alias("fwd_ts"))
        tmp=tmp.join(fwd_map, on=["ticker","fwd_ts"], how="left")
        tmp=tmp.with_columns(((pl.col(f"_fwd_{h}")-pl.col("close"))/pl.col("close")).alias(f"fwd_ret_{h}m")).drop(["fwd_ts",f"_fwd_{h}"])
        ev=tmp
    # gain bins
    ev=ev.with_columns(
        pl.when(pl.col("pct_gain")<10).then(pl.lit("<10%")).when(pl.col("pct_gain")<20).then(pl.lit("10-20%")).otherwise(pl.lit("20%+")).alias("gain_bin"),
    )
    def rank_bin(r):
        if r is None: return "21+"
        try:
            if r<=5: return "1-5"
            if r<=10: return "6-10"
            if r<=20: return "11-20"
        except: pass
        return "21+"
    if "rank" in ev.columns:
        ev=ev.with_columns(pl.col("rank").map_elements(rank_bin, return_dtype=pl.String).alias("rank_bin"))
    else:
        ev=ev.with_columns(pl.lit("21+").alias("rank_bin"))

    # signals at thresholds
    for thr in [5,8,12]:
        n=ev.filter(pl.col("vwap_dist")>thr).height
        print(f" threshold >{thr}%: n={n} ({n/ev.height:.1%})")

    # main bucket segmentation (monotonic)
    print("\n=== VWAP distance buckets (all events) ===")
    by_bucket={}
    for b in BUCKETS:
        print(f" {b}:")
        sub=ev.filter(pl.col("vwap_bucket")==b)
        by_bucket[b]=metrics_both(sub,cost)

    # threshold 8% signals
    sig=ev.filter(pl.col("vwap_dist")>threshold)
    print(f"\n=== SIGNALS vwap_dist >{threshold}% : n={sig.height} ===")
    sig_m=metrics_both(sig,cost)

    # segmentation for signals or all? spec: by vwap bucket, time, pct_gain, rank
    print("\n=== by time_bucket (signals) ===")
    by_time={}
    for tb in sorted(sig.select("time_bucket").unique().to_series().to_list()):
        if tb is None: continue
        print(f" {tb}:")
        by_time[str(tb)]=metrics_both(sig.filter(pl.col("time_bucket")==tb),cost)
    print("\n=== by gain_bin (signals) ===")
    by_gain={}
    for b in ["<10%","10-20%","20%+"]:
        print(f" {b}:")
        by_gain[b]=metrics_both(sig.filter(pl.col("gain_bin")==b),cost)
    print("\n=== by rank_bin (signals) ===")
    by_rank={}
    for b in ["1-5","6-10","11-20","21+"]:
        print(f" {b}:")
        by_rank[b]=metrics_both(sig.filter(pl.col("rank_bin")==b),cost)

    # also bucket metrics for 5% and 12% threshold signals
    thresh_metrics={}
    for thr in [5,12]:
        sub=ev.filter(pl.col("vwap_dist")>thr)
        print(f"\n=== threshold >{thr}% ===")
        thresh_metrics[str(thr)]=metrics_both(sub,cost)

    # chronological split train 40d test 20d
    dates_sorted=sorted(ev["et_date"].unique().to_list())
    split=40
    train_dates=set(dates_sorted[:split])
    test_dates=set(dates_sorted[split:])
    print(f"\n=== Chronological split train {split} / test {len(dates_sorted)-split} ===")
    print(f" dates {dates_sorted[0]} .. {dates_sorted[-1]} ({len(dates_sorted)} days)")
    def split_metrics(label, dates):
        sub=sig.filter(pl.col("et_date").is_in(list(dates)))
        print(f" {label} n={sub.height}")
        return metrics_both(sub,cost)
    train_m=split_metrics("train",train_dates)
    test_m=split_metrics("test",test_dates)
    # also bucket split for 15m/30m
    all_train=ev.filter(pl.col("et_date").is_in(list(train_dates)))
    all_test=ev.filter(pl.col("et_date").is_in(list(test_dates)))
    print("\n=== bucket train vs test (h15 h30 short exp) ===")
    for b in BUCKETS:
        tr=all_train.filter(pl.col("vwap_bucket")==b)
        te=all_test.filter(pl.col("vwap_bucket")==b)
        for h in [15,30]:
            c=f"fwd_ret_{h}m"
            for name, sub in [("train",tr),("test",te)]:
                s=sub[c].drop_nulls()
                if s.len()>0:
                    avg=float(s.mean()); exp_short=-avg-cost
                    print(f"  {b} {name} h{h}m n={s.len()} avg={avg:.4%} short_exp_net={exp_short:.4%}")

    out_path=Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ev.write_parquet(out_path)
    print(f"\nWrote {out_path} ({ev.height} rows, {sig.height} signals >{threshold}%)")

    summary={
        "n_events": ev.height, "n_signals": sig.height, "threshold": threshold,
        "cost_bps_per_side": cost_bps, "roundtrip_cost": cost,
        "by_bucket": by_bucket, "signal_metrics": sig_m,
        "by_time": by_time, "by_gain": by_gain, "by_rank": by_rank,
        "threshold_metrics": thresh_metrics,
        "train_metrics": train_m, "test_metrics": test_m,
        "dates": [str(d) for d in dates_sorted],
        "train_dates": [str(d) for d in sorted(train_dates)],
        "test_dates": [str(d) for d in sorted(test_dates)],
        "buckets": BUCKETS, "horizons": HORIZONS,
    }
    jpath=out_path.parent/"h006_summary.json"
    with open(jpath,"w") as f: json.dump(summary,f,indent=2,default=str)
    print(f"Wrote {jpath}")
    return summary

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--events", type=Path, required=True)
    p.add_argument("--clean-dir", type=Path, required=True)
    p.add_argument("--cost-bps", type=float, default=10)
    p.add_argument("--threshold", type=float, default=8)
    p.add_argument("--out", type=Path, default=Path("factory/artifacts/h006_results.parquet"))
    a=p.parse_args()
    run(a.events, a.clean_dir, a.cost_bps, a.threshold, a.out)

if __name__=="__main__":
    main()
