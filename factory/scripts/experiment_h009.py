"""H009 failed breakdown reclaim vs clean HOD break — ponytail minimal."""
import argparse, json
from pathlib import Path
from datetime import time
import polars as pl

HORIZONS=[5,15,30,60]
def time_bucket(et):
    hm=et.hour*60+et.minute
    if hm<600: return "09:30-10:00"
    h=et.hour
    return f"{h:02d}:00-{h+1:02d}:00" if h<15 else "15:00-16:00"

def metrics(sub,cost):
    if sub.height==0: return {}
    out={}
    for h in HORIZONS:
        c=f"fwd_ret_{h}m"
        if c not in sub.columns: continue
        s=sub[c].drop_nulls()
        if s.len()==0: continue
        wr=(s>0).sum()/s.len(); avg=s.mean(); med=s.median()
        wins=s.filter(s>0); losses=s.filter(s<=0)
        aw=wins.mean() if wins.len()>0 else 0; al=losses.mean() if losses.len()>0 else 0
        exp=wr*aw+(1-wr)*al; exp_net=exp-cost; wr_net=(s>cost).sum()/s.len()
        print(f"  h={h}m: n={s.len()} wr={wr:.3f} wr_net={wr_net:.3f} avg={avg:.4f} med={med:.4f} exp_net={exp_net:.4f} aw={aw:.4f} al={al:.4f}")
        out[f"h{h}"]={"n":s.len(),"wr":float(wr),"wr_net":float(wr_net),"avg":float(avg),"median":float(med),"exp_net":float(exp_net),"avg_win":float(aw),"avg_loss":float(al)}
    return out

def run(clean_dir,cost_bps,out_path):
    cost=cost_bps*2/10000
    clean_dir=Path(clean_dir)
    # load clean: support dir with monthly parquets or single file
    if clean_dir.is_dir():
        files=sorted(clean_dir.glob("clean_ohlcv_*.parquet"))
        if not files: files=sorted(clean_dir.glob("*.parquet"))
        print(f"Loading {len(files)} clean files ...")
        clean=pl.concat([pl.scan_parquet(f) for f in files]).collect()
    else:
        clean=pl.read_parquet(clean_dir)
    print(f" rows {clean.height}")
    clean=clean.with_columns(
        pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("et"),
        pl.col("timestamp").dt.convert_time_zone("America/New_York").dt.date().alias("et_date"),
    )
    clean=clean.filter((pl.col("et").dt.hour().cast(pl.Int32)*60+pl.col("et").dt.minute().cast(pl.Int32)>=570)&(pl.col("et").dt.hour().cast(pl.Int32)*60+pl.col("et").dt.minute().cast(pl.Int32)<960))
    clean=clean.filter(pl.col("close")>=2.0)
    clean=clean.sort(["ticker","timestamp"])
    print(" prior closes ...")
    daily_last=clean.sort("timestamp").group_by(["ticker","et_date"]).agg(pl.col("close").last().alias("last_close"))
    daily_last=daily_last.sort(["ticker","et_date"]).with_columns(pl.col("last_close").shift(1).over("ticker").alias("prior_close")).filter(pl.col("prior_close").is_not_null())
    clean=clean.join(daily_last.select(["ticker","et_date","prior_close"]),on=["ticker","et_date"],how="inner")
    if clean.height==0: print("no bars"); return
    clean=clean.with_columns(((pl.col("close")-pl.col("prior_close"))/pl.col("prior_close")*100).alias("pct_gain"))
    clean=clean.sort(["ticker","et_date","timestamp"])
    # HOD and VWAP per ticker-day
    # ponytail: output-identical candidate pre-filter. Only days with a pct_gain>=3 bar can emit a
    # signal (inner loop skips every pct<3 bar); all bars of a candidate day are kept, so per-day
    # cum_max/cum_sum (hod_before, vwap) and the lookback dip windows are preserved exactly. Drops ~90%
    # of ticker-days, which is the only reason this fits and finishes in RAM/time.
    cand_mask=clean.group_by(["ticker","et_date"]).agg(pl.col("pct_gain").max().alias("_mx")).filter(pl.col("_mx")>=3).select(["ticker","et_date"])
    clean=clean.join(cand_mask,on=["ticker","et_date"],how="inner")
    clean=clean.sort(["ticker","et_date","timestamp"])
    print(f" candidate days {clean.select(['ticker','et_date']).unique().height} rows {clean.height}")
    clean=clean.with_columns([
        pl.col("high").cum_max().over(["ticker","et_date"]).alias("hod_incl"),
        (pl.col("close")*pl.col("volume")).cum_sum().over(["ticker","et_date"]).alias("cum_pv"),
        pl.col("volume").cum_sum().over(["ticker","et_date"]).alias("cum_v"),
    ])
    clean=clean.with_columns([
        pl.col("hod_incl").shift(1).over(["ticker","et_date"]).alias("hod_before"),
        (pl.col("cum_pv")/pl.col("cum_v")).alias("vwap"),
    ])
    # detect signals per ticker-day via python loop over groups (ponytail: global scan, O(n))
    print(" detecting trap vs clean signals ...")
    # need clean sorted; iterate groups
    trap_rows=[]; clean_rows=[]
    # For MFE we need cutoff: collect per group lists but detect first
    # To avoid per-row python overhead on 68M, filter to pct_gain>=3 candidates only, but HOD tracking needs full history for hod_before/vwap dip check
    # We'll iterate groups but skip groups with no pct_gain>=3 bar
    grouped=clean.group_by(["ticker","et_date"], maintain_order=True)
    # Instead of group_by iteration which materializes, use partition-like loop via unique keys
    keys=clean.select(["ticker","et_date"]).unique().sort(["ticker","et_date"]).to_dicts()
    # To avoid huge loop overhead, pre-filter clean to dict per group using polars partitions would be slower; just iterate via index slicing
    # Use clean partitioned by sorting; iterate with python loop over sorted df rows sequential scan (single pass)
    # ponytail: use polars group iteration which is efficient in Rust (df_dict left over from draft, removed: memory blowup)
    n_groups=0
    for (ticker, et_date), sub in clean.group_by(["ticker","et_date"]):
        sub=sub.sort("timestamp")
        n_groups+=1
        # quick skip if max pct_gain <3
        if sub["pct_gain"].max() < 3: continue
        ts=sub["timestamp"].to_list(); closes=sub["close"].to_list()
        lows=sub["low"].to_list(); highs=sub["high"].to_list()
        hod_bef=sub["hod_before"].to_list(); vwap=sub["vwap"].to_list()
        pct=sub["pct_gain"].to_list(); ets=sub["et"].to_list()
        # Need volume for vwap not needed
        m=len(sub)
        # track dip events: for trap we need within last 5m low < HOD*0.997 or low < vwap
        # For clean: no dip below HOD in last 10m
        # Precompute dip_hod = low < hod_before*0.997 (0.3% below), dip_vwap = low < vwap
        for i in range(m):
            if pct[i] < 3: continue
            hb=hod_bef[i]
            if hb is None: continue
            # check signal types
            is_reclaim = closes[i] > hb  # reclaim above HOD
            is_above_vwap = vwap[i] is not None and closes[i] > vwap[i]
            # For trap A: need reclaim (HOD or VWAP) AND prior dip within 5m
            reclaim_type=None
            if is_reclaim: reclaim_type="hod"
            elif is_above_vwap: reclaim_type="vwap"
            else: reclaim_type=None
            # need prior dip within 5 min window (5 bars since 1m bars)
            has_dip=False; dip_depth=None
            # look back up to 5 bars
            for j in range(max(0,i-5), i):
                hb_j=hod_bef[j]
                # dip definition: low < hod_before*0.997 or low < vwap[j]
                dip_hod = hb_j is not None and lows[j] < hb_j*0.997
                dip_vw = vwap[j] is not None and lows[j] < vwap[j]
                if dip_hod or dip_vw:
                    has_dip=True
                    # depth: max dip below level
                    if dip_hod and hb_j:
                        d=(hb_j - lows[j])/hb_j*100
                        dip_depth=d if dip_depth is None else max(dip_depth,d)
                    if dip_vw and vwap[j]:
                        # vwap dip depth not needed for segmentation primary but store
                        pass
                    # break early if found? need max depth so continue
            # For clean B: need new HOD (close>hod_before) AND no dip below HOD in last 10m
            is_clean=False
            if is_reclaim:
                no_dip_10=True
                for j in range(max(0,i-10), i):
                    hb_j=hod_bef[j]
                    if hb_j is not None and lows[j] < hb_j*0.997:
                        no_dip_10=False; break
                    # also consider VWAP dip? spec says without prior dip below HOD - so only HOD
                if no_dip_10:
                    is_clean=True
            # assign
            row={"timestamp":ts[i],"et":ets[i],"ticker":ticker,"et_date":et_date,"close":closes[i],"pct_gain":pct[i],"hod_before":hb,"vwap":vwap[i],"dip_depth":dip_depth}
            if reclaim_type and has_dip:
                # trap signal
                # differentiate dip depth bucket later
                row["signal_type"]="trap"
                row["dip_type"]="hod" if dip_depth is not None else "vwap"
                trap_rows.append(row)
            elif is_clean:
                row["signal_type"]="clean"
                clean_rows.append(row)
    print(f" groups {n_groups} trap {len(trap_rows)} clean {len(clean_rows)}")
    all_rows=trap_rows+clean_rows
    if not all_rows:
        print(" no signals")
        out_path=Path(out_path); out_path.parent.mkdir(parents=True,exist_ok=True)
        pl.DataFrame({}).write_parquet(out_path)
        summary={"n_trap":0,"n_clean":0,"cost_bps":cost_bps}
        with open(out_path.parent/"h009_summary.json","w") as f: json.dump(summary,f,indent=2,default=str)
        return summary
    sig=pl.DataFrame(all_rows).sort(["ticker","et_date","timestamp"])
    # dedup 5m per ticker-day per type? spec says walk bars chronologically; use 5m dedup to avoid clusters
    # apply per (ticker,et_date,signal_type)
    from collections import defaultdict
    grouped2=defaultdict(list)
    for r in sig.to_dicts():
        grouped2[(r["ticker"],r["et_date"],r["signal_type"])].append(r)
    deduped=[]
    for k,lst in grouped2.items():
        lst.sort(key=lambda x: x["timestamp"])
        last=None
        for r in lst:
            if last and (r["timestamp"]-last).total_seconds()/60 <5: continue
            deduped.append(r); last=r["timestamp"]
    sig=pl.DataFrame(deduped).sort(["ticker","et_date","timestamp"]) if deduped else pl.DataFrame([])
    print(f" after dedup trap {sig.filter(pl.col('signal_type')=='trap').height if sig.height else 0} clean {sig.filter(pl.col('signal_type')=='clean').height if sig.height else 0}")
    if sig.height==0:
        out_path=Path(out_path); out_path.parent.mkdir(parents=True,exist_ok=True)
        sig.write_parquet(out_path)
        return {"n_trap":0,"n_clean":0}
    sig=sig.with_columns(pl.col("close").alias("sig_close"))
    sig=sig.with_row_index("__idx")
    # forward returns via join
    for h in HORIZONS:
        fwd_map=clean.select(["ticker","timestamp","close"]).rename({"timestamp":"fwd_ts","close":f"_fwd_{h}"})
        sig=sig.with_columns((pl.col("timestamp")+pl.duration(minutes=h)).cast(pl.Datetime("ns","UTC")).alias("fwd_ts"))
        sig=sig.join(fwd_map,on=["ticker","fwd_ts"],how="left")
        sig=sig.with_columns(((pl.col(f"_fwd_{h}")-pl.col("sig_close"))/pl.col("sig_close")).alias(f"fwd_ret_{h}m")).drop(["fwd_ts",f"_fwd_{h}"])
    # MFE/MAE 60m via per-group lookup
    need=sig.select(["ticker","et_date"]).unique().to_dicts()
    need_df=pl.DataFrame(need).unique() if need else pl.DataFrame({"ticker":[],"et_date":[]})
    clean_needed=clean.join(need_df,on=["ticker","et_date"],how="inner") if need_df.height else clean.head(0)
    groups={}
    for (t,d),sub in clean_needed.group_by(["ticker","et_date"]):
        sub=sub.sort("timestamp")
        groups[(t,d)]=(sub["timestamp"].to_list(),sub["high"].to_list(),sub["low"].to_list())
    mfe=[]; mae=[]
    for r in sig.iter_rows(named=True):
        key=(r["ticker"],r["et_date"])
        tup=groups.get(key)
        if not tup: mfe.append(None); mae.append(None); continue
        tl,hl,ll=tup
        try: idx=tl.index(r["timestamp"])
        except: mfe.append(None); mae.append(None); continue
        end=min(idx+60,len(tl)-1)
        if end>idx:
            mfe.append((max(hl[idx+1:end+1])-r["sig_close"])/r["sig_close"])
            mae.append((min(ll[idx+1:end+1])-r["sig_close"])/r["sig_close"])
        else: mfe.append(None); mae.append(None)
    sig=sig.with_columns([pl.Series("mfe_60m",mfe),pl.Series("mae_60m",mae)])
    sig=sig.with_columns([
        pl.col("et").map_elements(time_bucket,return_dtype=pl.String).alias("time_bucket"),
        pl.when(pl.col("pct_gain")<10).then(pl.lit("<10%")).when(pl.col("pct_gain")<20).then(pl.lit("10-20%")).otherwise(pl.lit("20%+")).alias("gain_bin"),
        pl.when(pl.col("dip_depth").is_null()).then(pl.lit("no_hod_dip")).when(pl.col("dip_depth")<0.5).then(pl.lit("0.3-0.5%")).when(pl.col("dip_depth")<1.0).then(pl.lit("0.5-1%")).otherwise(pl.lit("1%+")).alias("dip_bin"),
    ])
    df=sig
    # reporting
    print(f"\n Total signals {df.height} trap {(df['signal_type']=='trap').sum()} clean {(df['signal_type']=='clean').sum()}")
    print(f" Dates {df['et_date'].min()} .. {df['et_date'].max()} ({df['et_date'].n_unique()} days)")
    def seg(label,sub):
        print(f"\n=== {label} n={sub.height} ===")
        return metrics(sub,cost)
    all_m=seg("ALL",df)
    trap_m=seg("TRAP",df.filter(pl.col("signal_type")=="trap"))
    clean_m=seg("CLEAN",df.filter(pl.col("signal_type")=="clean"))
    # trap vs clean diff
    print("\n=== TRAP vs CLEAN diff (trap exp_net - clean exp_net) ===")
    for h in HORIZONS:
        k=f"h{h}"
        if k in trap_m and k in clean_m:
            print(f" h{h}m trap {trap_m[k]['exp_net']:.4f} clean {clean_m[k]['exp_net']:.4f} diff {trap_m[k]['exp_net']-clean_m[k]['exp_net']:.4f}")
    # by time_bucket trap vs clean
    print("\n=== by time_bucket (trap vs clean) ===")
    seg_tb={}
    for tb in sorted(df["time_bucket"].unique().to_list()):
        sub=df.filter(pl.col("time_bucket")==tb)
        print(f" {tb} n={sub.height} trap {(sub['signal_type']=='trap').sum()} clean {(sub['signal_type']=='clean').sum()}")
        seg_tb[tb]={"all":metrics(sub,cost),"trap":metrics(sub.filter(pl.col("signal_type")=="trap"),cost),"clean":metrics(sub.filter(pl.col("signal_type")=="clean"),cost)}
    print("\n=== by gain_bin ===")
    seg_gain={}
    for b in ["<10%","10-20%","20%+"]:
        sub=df.filter(pl.col("gain_bin")==b)
        if sub.height==0: continue
        print(f" {b} n={sub.height}")
        seg_gain[b]={"all":metrics(sub,cost),"trap":metrics(sub.filter(pl.col("signal_type")=="trap"),cost),"clean":metrics(sub.filter(pl.col("signal_type")=="clean"),cost)}
    print("\n=== by dip depth (trap only) ===")
    seg_dip={}
    for b in ["0.3-0.5%","0.5-1%","1%+","no_hod_dip"]:
        sub=df.filter(pl.col("dip_bin")==b)
        if sub.height==0: continue
        print(f" {b} n={sub.height}")
        seg_dip[b]=metrics(sub,cost)
    # chronological split 40d train / 20d test
    dates_sorted=sorted(df["et_date"].unique().to_list())
    split=min(40,len(dates_sorted)-20) if len(dates_sorted)>=60 else len(dates_sorted)*2//3
    # spec: 2025-05-01:2025-06-24 (40d) vs 2025-06-25:2025-07-31 (20d) - use date threshold
    import datetime
    cutoff=datetime.date(2025,6,25)
    train_dates=[d for d in dates_sorted if d < cutoff]
    test_dates=[d for d in dates_sorted if d >= cutoff]
    if not train_dates: train_dates=dates_sorted[:split]
    if not test_dates: test_dates=dates_sorted[split:]
    print(f"\n=== Chronological split train {len(train_dates)}d test {len(test_dates)}d cutoff 2025-06-25 ===")
    train_df=df.filter(pl.col("et_date").is_in(train_dates)); test_df=df.filter(pl.col("et_date").is_in(test_dates))
    print(f" train n={train_df.height} trap {(train_df['signal_type']=='trap').sum() if train_df.height else 0}")
    train_all=metrics(train_df,cost); train_trap=metrics(train_df.filter(pl.col("signal_type")=="trap"),cost); train_clean=metrics(train_df.filter(pl.col("signal_type")=="clean"),cost)
    print(f" test n={test_df.height} trap {(test_df['signal_type']=='trap').sum() if test_df.height else 0}")
    test_all=metrics(test_df,cost); test_trap=metrics(test_df.filter(pl.col("signal_type")=="trap"),cost); test_clean=metrics(test_df.filter(pl.col("signal_type")=="clean"),cost)
    out_path=Path(out_path); out_path.parent.mkdir(parents=True,exist_ok=True)
    df.write_parquet(out_path)
    print(f"\nWrote {out_path} ({df.height})")
    summary={"n_trap":int((df["signal_type"]=="trap").sum()),"n_clean":int((df["signal_type"]=="clean").sum()),"n_total":df.height,"cost_bps_per_side":cost_bps,"roundtrip_cost":cost,
             "all":all_m,"trap":trap_m,"clean":clean_m,"by_time_bucket":seg_tb,"by_gain":seg_gain,"by_dip":seg_dip,
             "train_all":train_all,"train_trap":train_trap,"train_clean":train_clean,"test_all":test_all,"test_trap":test_trap,"test_clean":test_clean,
             "dates":[str(d) for d in dates_sorted],"train_dates":[str(d) for d in train_dates],"test_dates":[str(d) for d in test_dates]}
    jpath=out_path.parent/"h009_summary.json"
    with open(jpath,"w") as f: json.dump(summary,f,indent=2,default=str)
    print(f"Wrote {jpath}")
    return summary

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--clean-dir",type=Path,required=True)
    p.add_argument("--cost-bps",type=float,default=10)
    p.add_argument("--out",type=Path,default=Path("factory/artifacts/h009_results.parquet"))
    a=p.parse_args()
    run(a.clean_dir,a.cost_bps,a.out)
if __name__=="__main__": main()
