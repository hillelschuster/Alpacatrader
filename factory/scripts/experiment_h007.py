"""H007 precursor contraction: tight 15m range before breakout vs wide grind — ponytail minimal."""
import argparse, json
from pathlib import Path
import polars as pl

HORIZONS=[5,15,30,60]
TRAIN_END="2025-06-24"  # inclusive train; test from 2025-06-25

def tb(et):
    hm=et.hour*60+et.minute
    if hm<600: return "09:30-10:00"
    if hm<720: return "10:00-12:00"
    if hm<840: return "12:00-14:00"
    return "14:00-15:30"

def gain_bin(g):
    if g<10: return "<10%"
    if g<20: return "10-20%"
    return "20%+"

def metrics(df,cost):
    if df.height==0: return {}
    out={}
    for h in HORIZONS:
        c=f"fwd_ret_{h}m"
        if c not in df.columns: continue
        s=df[c].drop_nulls()
        if s.len()==0: continue
        wr=(s>0).sum()/s.len()
        wr_net=(s>cost).sum()/s.len()
        avg=s.mean(); med=s.median()
        wins=s.filter(s>0); losses=s.filter(s<=0)
        aw=wins.mean() if wins.len()>0 else 0
        al=losses.mean() if losses.len()>0 else 0
        # ponytail: 68M scan, global median enough
        print(f"  h{h}m n={s.len()} wr={wr:.3f} wr_net={wr_net:.3f} avg={avg:.4%} exp_net={avg-cost:.4%}")
        out[f"h{h}"]={"n":int(s.len()),"wr":float(wr),"wr_net":float(wr_net),"avg":float(avg),"median":float(med) if med is not None else None,"exp_net":float(avg-cost),"avg_win":float(aw),"avg_loss":float(al)}
    return out

def run(clean_dir, ranked_dir, cost_bps, out):
    cost=cost_bps*2/10000
    clean_dir=Path(clean_dir)
    files=sorted(clean_dir.glob("clean_*.parquet"))
    if not files: files=sorted(clean_dir.glob("*.parquet"))
    print(f"Loading {len(files)} clean files ...")
    # scan and collect with RTH filter; use scan for memory
    dfs=[pl.scan_parquet(str(f)) for f in files]
    # union via concat of scans requires collect per scan; use pl.concat of collected filtered would be easier: scan + filter then collect
    # ponytail: collect via lazy concat if available, else per-file collect
    try:
        clean=pl.concat([pl.scan_parquet(str(f)).collect() for f in files])
    except Exception:
        clean=pl.read_parquet(files[0])
        for f in files[1:]:
            clean=pl.concat([clean, pl.read_parquet(f)])
    print(f" raw rows {clean.height}")
    clean=clean.with_columns([
        pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("et"),
        pl.col("timestamp").dt.convert_time_zone("America/New_York").dt.date().alias("et_date"),
    ])
    # keep RTH 09:30-16:00 for fwd lookup, but signal walk limited to 09:30-15:30 later
    # ponytail fix: dt.hour() is Int8 -> hour*60 overflows/wraps negative; cast to Int32 for correct minute-of-day
    clean=clean.filter((pl.col("et").dt.hour().cast(pl.Int32)*60+pl.col("et").dt.minute()>=570)&(pl.col("et").dt.hour().cast(pl.Int32)*60+pl.col("et").dt.minute()<960))
    clean=clean.filter(pl.col("close")>=2.0)
    clean=clean.sort(["ticker","timestamp"])
    print(f" RTH filtered {clean.height}")
    # prior close per ticker et_date
    daily_last=clean.sort("timestamp").group_by(["ticker","et_date"]).agg([pl.col("close").last().alias("last_close")])
    daily_last=daily_last.sort(["ticker","et_date"]).with_columns(pl.col("last_close").shift(1).over("ticker").alias("prior_close"))
    daily_last=daily_last.filter(pl.col("prior_close").is_not_null())
    clean=clean.join(daily_last.select(["ticker","et_date","prior_close"]), on=["ticker","et_date"], how="inner")
    clean=clean.with_columns(((pl.col("close")-pl.col("prior_close"))/pl.col("prior_close")*100).alias("pct_gain"))
    clean=clean.sort(["ticker","et_date","timestamp"])
    # ponytail: output-identical candidate pre-filter (H009 pattern). A signal needs pct_gain>=5 at the
    # signal bar, so no day whose max pct_gain<5 can emit a signal. Keep ALL bars of candidate days so
    # the rolling_15m / cum_max flags are preserved exactly. Drops most ticker-days -> fits in RAM/time.
    cand_mask=clean.group_by(["ticker","et_date"]).agg(pl.col("pct_gain").max().alias("_mx")).filter(pl.col("_mx")>=5).select(["ticker","et_date"])
    clean=clean.join(cand_mask,on=["ticker","et_date"],how="inner")
    clean=clean.sort(["ticker","et_date","timestamp"])
    print(f" candidate days {clean.select(['ticker','et_date']).unique().height} rows {clean.height}")
    # rolling 15m window for contraction detection
    print("Computing contraction flags (15m rolling) ...")
    clean=clean.with_columns([
        pl.col("high").rolling_max(window_size=15, min_samples=15).over(["ticker","et_date"]).alias("roll_max_high"),
        pl.col("low").rolling_min(window_size=15, min_samples=15).over(["ticker","et_date"]).alias("roll_min_low"),
        pl.col("high").cum_max().over(["ticker","et_date"]).alias("hod_incl"),
    ])
    clean=clean.with_columns([
        ((pl.col("roll_max_high")-pl.col("roll_min_low"))/((pl.col("roll_max_high")+pl.col("roll_min_low"))/2)).alias("range_pct"),
        pl.col("hod_incl").shift(1).over(["ticker","et_date"]).alias("hod_before"),
        pl.col("roll_max_high").alias("cons_high"),
    ])
    # breakout is next bar closes above prior cons_high and gain>=5%
    clean=clean.with_columns([
        pl.col("cons_high").shift(1).over(["ticker","et_date"]).alias("prev_cons_high"),
        pl.col("range_pct").shift(1).over(["ticker","et_date"]).alias("prev_range_pct"),
    ])
    # restrict signals to 09:30-15:30 (570 <= hm <= 930) so fwd 60m stays mostly RTH
    clean_sig=clean.filter(
        (pl.col("et").dt.hour().cast(pl.Int32)*60+pl.col("et").dt.minute()>=570)&
        (pl.col("et").dt.hour().cast(pl.Int32)*60+pl.col("et").dt.minute()<=930)&
        pl.col("prev_cons_high").is_not_null()&
        pl.col("prev_range_pct").is_not_null()&
        (pl.col("pct_gain")>=5)&
        (pl.col("close")>pl.col("prev_cons_high"))
    )
    print(f" breakout candidates {clean_sig.height}")
    tight=clean_sig.filter(pl.col("prev_range_pct")<0.01)
    wide=clean_sig.filter(pl.col("prev_range_pct")>=0.015)
    print(f"  contraction (tight <1%) {tight.height}  control (wide >=1.5%) {wide.height}")
    # tag group
    tight=tight.with_columns(pl.lit("contraction").alias("group"))
    wide=wide.with_columns(pl.lit("non_contraction").alias("group"))
    sig=pl.concat([tight, wide], how="diagonal") if tight.height>0 or wide.height>0 else pl.DataFrame()
    if sig.height==0:
        print("No signals")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({}).write_parquet(out)
        with open(Path(out).parent/f"{Path(out).stem}_summary.json","w") as f: json.dump({"n_contraction":0,"n_control":0},f,indent=2)
        return
    # dedup 5m per ticker-day per group
    sig=sig.sort(["ticker","et_date","timestamp","group"])
    # simple 5m dedup per (ticker,et_date,group) via python
    from collections import defaultdict
    rows=sig.to_dicts()
    grouped=defaultdict(list)
    for r in rows: grouped[(r["ticker"],r["et_date"],r["group"])].append(r)
    keep=[]
    for k,lst in grouped.items():
        lst.sort(key=lambda x: x["timestamp"])
        last=None
        for r in lst:
            ts=r["timestamp"]
            if last and (ts-last).total_seconds()/60<5: continue
            keep.append(r); last=ts
    sig=pl.DataFrame(keep)
    print(f" after 5m dedup {sig.height} (contraction {sig.filter(pl.col('group')=='contraction').height} control {sig.filter(pl.col('group')=='non_contraction').height})")
    # forward returns via timestamp join
    sig=sig.with_columns([pl.col("close").alias("sig_close"), pl.col("high").alias("sig_high"), pl.col("low").alias("sig_low")])
    sig=sig.with_row_index("__idx")
    for h in HORIZONS:
        fwd_map=clean.select(["ticker","timestamp","close"]).rename({"timestamp":"fwd_ts","close":f"_fwd_{h}"})
        sig=sig.with_columns((pl.col("timestamp")+pl.duration(minutes=h)).cast(pl.Datetime("ns","UTC")).alias("fwd_ts"))
        sig=sig.join(fwd_map, on=["ticker","fwd_ts"], how="left")
        sig=sig.with_columns(((pl.col(f"_fwd_{h}")-pl.col("sig_close"))/pl.col("sig_close")).alias(f"fwd_ret_{h}m")).drop(["fwd_ts",f"_fwd_{h}"])
    # MFE/MAE next 60m
    need_df=sig.select(["ticker","et_date"]).unique()
    clean_needed=clean.join(need_df, on=["ticker","et_date"], how="inner")
    cg={}
    for (t,d), sub in clean_needed.group_by(["ticker","et_date"]):
        sub=sub.sort("timestamp")
        cg[(t,d)]=(sub["timestamp"].to_list(), sub["high"].to_list(), sub["low"].to_list())
    mfe=[]; mae=[]
    for r in sig.iter_rows(named=True):
        k=(r["ticker"],r["et_date"]); ts_list,hs,ls=cg.get(k,([],[],[]))
        try: idx=ts_list.index(r["timestamp"])
        except: mfe.append(None); mae.append(None); continue
        end=min(idx+60, len(ts_list)-1)
        if end>idx:
            mfe.append((max(hs[idx+1:end+1])-r["sig_close"])/r["sig_close"])
            mae.append((min(ls[idx+1:end+1])-r["sig_close"])/r["sig_close"])
        else: mfe.append(None); mae.append(None)
    sig=sig.with_columns([pl.Series("mfe_60m",mfe), pl.Series("mae_60m",mae)])
    sig=sig.with_columns([
        pl.col("et").map_elements(tb, return_dtype=pl.String).alias("time_bucket"),
        pl.col("pct_gain").map_elements(gain_bin, return_dtype=pl.String).alias("gain_bin"),
    ])
    # reporting
    print("\n=== OVERALL contraction vs control ===")
    for g in ["contraction","non_contraction"]:
        print(f"\n{g} n={sig.filter(pl.col('group')==g).height}:")
        metrics(sig.filter(pl.col("group")==g), cost)
    print("\n=== by time_bucket (contraction) ===")
    for b in sorted(sig.filter(pl.col("group")=="contraction")["time_bucket"].unique().to_list()):
        print(f" {b}:"); metrics(sig.filter((pl.col("group")=="contraction")&(pl.col("time_bucket")==b)), cost)
    print("\n=== by gain_bin (contraction) ===")
    for b in ["<10%","10-20%","20%+"]:
        print(f" {b}:"); metrics(sig.filter((pl.col("group")=="contraction")&(pl.col("gain_bin")==b)), cost)
    print("\n=== by gain_bin (control) ===")
    for b in ["<10%","10-20%","20%+"]:
        print(f" {b}:"); metrics(sig.filter((pl.col("group")=="non_contraction")&(pl.col("gain_bin")==b)), cost)
    # chronological split
    train=sig.filter(pl.col("et_date")<=pl.date(2025,6,24)) if "et_date" in sig.columns else sig
    # polars date literal workaround: filter via string compare
    sig_str=sig.with_columns(pl.col("et_date").cast(pl.String).alias("_d"))
    train=sig_str.filter(pl.col("_d")<="2025-06-24").drop("_d")
    test=sig_str.filter(pl.col("_d")> "2025-06-24").drop("_d")
    print(f"\n=== CHRONO split train <=2025-06-24 n={train.height} test n={test.height} ===")
    for g in ["contraction","non_contraction"]:
        print(f"\n train {g}:"); metrics(train.filter(pl.col("group")==g), cost)
        print(f" test {g}:"); metrics(test.filter(pl.col("group")==g), cost)
    # save
    out=Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    # keep relevant cols
    keep_cols=["timestamp","et","et_date","ticker","close","pct_gain","prev_range_pct","prev_cons_high","group","time_bucket","gain_bin","mfe_60m","mae_60m"]+[f"fwd_ret_{h}m" for h in HORIZONS]
    sig.select([c for c in keep_cols if c in sig.columns]).write_parquet(out)
    print(f"\nWrote {out} {sig.height} rows")
    # summary
    def blk(df): return metrics(df,cost)
    import io, contextlib
    summary={
        "n_contraction": int(sig.filter(pl.col("group")=="contraction").height),
        "n_control": int(sig.filter(pl.col("group")=="non_contraction").height),
        "n_total": int(sig.height),
        "cost_bps_per_side": cost_bps,
        "roundtrip_cost": cost,
        "all_contraction": blk(sig.filter(pl.col("group")=="contraction")),
        "all_control": blk(sig.filter(pl.col("group")=="non_contraction")),
        "train_contraction": blk(train.filter(pl.col("group")=="contraction")),
        "train_control": blk(train.filter(pl.col("group")=="non_contraction")),
        "test_contraction": blk(test.filter(pl.col("group")=="contraction")),
        "test_control": blk(test.filter(pl.col("group")=="non_contraction")),
        "params": {"range_pct_tight":0.01,"range_pct_wide":0.015,"window":15,"min_gain_pct":5,"session":"09:30-15:30","dedup":"5m"},
        "period": {"train":"2025-05-01:2025-06-24","test":"2025-06-25:2025-07-31","all":"2025-05-01:2025-07-31"},
    }
    with open(out.parent/f"{out.stem}_summary.json","w") as f: json.dump(summary,f,indent=2,default=str)
    print(f"Wrote {out.parent/(out.stem+'_summary.json')}")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--clean-dir", type=Path, required=True)
    p.add_argument("--ranked-dir", type=Path, default=Path("factory/artifacts"))
    p.add_argument("--cost-bps", type=float, default=10)
    p.add_argument("--out", type=Path, default=Path("factory/artifacts/h007_results.parquet"))
    a=p.parse_args()
    run(a.clean_dir, a.ranked_dir, a.cost_bps, a.out)

if __name__=="__main__": main()
