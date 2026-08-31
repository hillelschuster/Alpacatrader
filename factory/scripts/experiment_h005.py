"""H005 rank persistence vs brief spike — ponytail minimal."""
import argparse, json
from pathlib import Path
import polars as pl

HORIZONS=[1,3,5,15,30,60]

def metrics(df, cost):
    out={}
    for h in HORIZONS:
        c=f"fwd_ret_{h}m"
        if c not in df.columns: continue
        s=df[c].drop_nulls()
        if s.len()==0: continue
        wr=(s>0).sum()/s.len()
        wr_net=(s>cost).sum()/s.len()
        avg=s.mean()
        out[f"h{h}"]={"n":s.len(),"wr":float(wr),"wr_net":float(wr_net),"avg":float(avg),"exp_net":float(avg-cost)}
    return out

def extract_signals(ranked_dir: Path, min_persist=5, min_persist20=10):
    files=sorted(ranked_dir.glob("ranked_*.parquet"))
    if not files:
        raise SystemExit(f"no ranked_*.parquet in {ranked_dir}")
    # ponytail: vectorized polars streak logic
    def signals_for_threshold(df, thresh, need):
        # df sorted by ticker,timestamp, has rank, timestamp
        df=df.sort(["ticker","timestamp"])
        df=df.with_columns([
            (pl.col("rank")<=thresh).alias("_in"),
            (pl.col("timestamp")-pl.col("timestamp").shift(1).over("ticker")).alias("_gap"),
            (pl.col("rank").shift(1).over("ticker")<=thresh).alias("_prev_in"),
        ])
        # gap ==1m
        df=df.with_columns(
            (pl.col("_gap")==pl.duration(minutes=1)).alias("_gap1")
        )
        # streak: cum count within run
        # break start: _in and (not _prev_in or not _gap1)
        df=df.with_columns(
            (pl.col("_in") & ((~pl.col("_prev_in").fill_null(False)) | (~pl.col("_gap1").fill_null(False)))).alias("_run_start")
        )
        # run id = cum sum of _run_start over ticker
        df=df.with_columns(
            pl.col("_run_start").cum_sum().over("ticker").alias("_run_id")
        )
        # streak position within run
        df=df.with_columns(
            pl.when(pl.col("_in")).then(pl.col("_in").cum_count().over(["ticker","_run_id"])).otherwise(0).alias("_streak")
        )
        # max streak per ticker
        max_streak=df.filter(pl.col("_in")).group_by("ticker").agg(pl.col("_streak").max().alias("mx")) if df.filter(pl.col("_in")).height else pl.DataFrame({"ticker":[],"mx":[]})
        mx_map=dict(zip(max_streak["ticker"].to_list(), max_streak["mx"].to_list())) if max_streak.height else {}
        # persistent: first row where _streak==need per ticker
        persist=df.filter(pl.col("_streak")==need).sort(["ticker","timestamp"]).group_by("ticker").agg(pl.all().first())
        # brief candidates: tickers where 1<=mx<=2 and not in persist
        persist_tickers=set(persist["ticker"].to_list()) if persist.height else set()
        brief_tickers=[t for t,mx in mx_map.items() if 1<=mx<=2 and t not in persist_tickers]
        brief=None
        if brief_tickers:
            brief=df.filter(pl.col("ticker").is_in(brief_tickers) & pl.col("_in")).sort(["ticker","timestamp"]).group_by("ticker").agg(pl.all().first())
        return persist, brief

    persist_all=[]; brief_all=[]; persist20_all=[]; brief20_all=[]
    for f in files:
        df=pl.read_parquet(f)
        p,b=signals_for_threshold(df, 5, min_persist)
        if p is not None and p.height:
            p=p.with_columns(pl.col("et").dt.date().cast(pl.String).alias("et_date"))
            persist_all.append(p)
        if b is not None and b.height:
            b=b.with_columns(pl.col("et").dt.date().cast(pl.String).alias("et_date"))
            brief_all.append(b)
        p20,b20=signals_for_threshold(df, 20, min_persist20)
        if p20 is not None and p20.height:
            p20=p20.with_columns(pl.col("et").dt.date().cast(pl.String).alias("et_date"))
            persist20_all.append(p20)
        if b20 is not None and b20.height:
            b20=b20.with_columns(pl.col("et").dt.date().cast(pl.String).alias("et_date"))
            brief20_all.append(b20)
    def cat(lst):
        if not lst: return pl.DataFrame()
        return pl.concat(lst, how="diagonal")
    return cat(persist_all), cat(brief_all), cat(persist20_all), cat(brief20_all)

def print_comparison(name_a, df_a, name_b, df_b, cost):
    ma=metrics(df_a,cost)
    mb=metrics(df_b,cost)
    print(f"\n=== {name_a} (n={df_a.height}) vs {name_b} (n={df_b.height}) ===")
    print(f"{'horizon':<8} {'group':<18} {'n':>6} {'wr':>6} {'wr_net':>7} {'avg':>8} {'exp_net':>8}")
    for h in HORIZONS:
        k=f"h{h}"
        a=ma.get(k); b=mb.get(k)
        if a:
            print(f"{k:<8} {name_a:<18} {a['n']:6d} {a['wr']:6.3f} {a['wr_net']:7.3f} {a['avg']:8.4f} {a['exp_net']:8.4f}")
        if b:
            print(f"{k:<8} {name_b:<18} {b['n']:6d} {b['wr']:6.3f} {b['wr_net']:7.3f} {b['avg']:8.4f} {b['exp_net']:8.4f}")
        if a and b:
            print(f"{'':<8} {'diff(persist-brief)':<18} {'':6} {'':6} {'':7} {a['avg']-b['avg']:8.4f} {a['exp_net']-b['exp_net']:8.4f}")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--ranked-dir", type=Path, default=Path("factory/artifacts"))
    p.add_argument("--cost-bps", type=float, default=10)
    p.add_argument("--min-persist", type=int, default=5)
    p.add_argument("--out", type=Path, default=Path("factory/artifacts/h005_results.parquet"))
    args=p.parse_args()
    cost=args.cost_bps*2/10000
    print(f"Cost {args.cost_bps} bps/side roundtrip {cost:.4f} min_persist={args.min_persist}")
    persist, brief, persist20, brief20 = extract_signals(args.ranked_dir, args.min_persist, 10)
    print(f"persistent_top5: {persist.height}  brief_top5: {brief.height}")
    print(f"persistent_top20(>=10m): {persist20.height}  brief_top20(1-2m): {brief20.height}")

    # combined for output
    all_sig=pl.concat([d for d in [persist,brief,persist20,brief20] if d.height>0], how="diagonal") if any(d.height for d in [persist,brief,persist20,brief20]) else pl.DataFrame()

    # comparisons
    if persist.height and brief.height:
        print_comparison("persistent_top5", persist, "brief_top5", brief, cost)
    if persist20.height and brief20.height:
        print_comparison("persistent_top20", persist20, "brief_top20", brief20, cost)

    # baseline: persistent_top5 vs all top20 events? load events_all for baseline counts
    try:
        ev=pl.read_parquet(args.ranked_dir/"events_all.parquet")
        print(f"\nBaseline events_all: {ev.height} (top20 entry events)")
        # print baseline metrics
        me=metrics(ev,cost)
        print(f"{'horizon':<8} {'group':<18} {'n':>6} {'wr':>6} {'wr_net':>7} {'avg':>8} {'exp_net':>8}")
        for h in HORIZONS:
            k=f"h{h}"
            if k in me:
                v=me[k]
                print(f"{k:<8} {'events_all':<18} {v['n']:6d} {v['wr']:6.3f} {v['wr_net']:7.3f} {v['avg']:8.4f} {v['exp_net']:8.4f}")
    except Exception as e:
        print(f"baseline events_all not available: {e}")

    # chronological split
    if all_sig.height:
        # et_date str -> sort
        dates=sorted(all_sig["et_date"].unique().to_list())
        print(f"\n=== Chronological split train first 14 / test last 7 ===")
        print(f" dates: {dates[0]} .. {dates[-1]} ({len(dates)} days)")
        split=14
        train_dates=set(dates[:split]); test_dates=set(dates[split:])
        for label, df in [("persistent_top5",persist),("brief_top5",brief)]:
            tr=df.filter(pl.col("et_date").is_in(list(train_dates))) if df.height else df
            te=df.filter(pl.col("et_date").is_in(list(test_dates))) if df.height else df
            mt=metrics(tr,cost); me2=metrics(te,cost)
            print(f"\n -- {label} train n={tr.height} / test n={te.height} --")
            for h in HORIZONS:
                k=f"h{h}"
                a=mt.get(k); b=me2.get(k)
                if a or b:
                    av=f"{a['avg']:.4f}/{a['exp_net']:.4f}" if a else "NA"
                    bv=f"{b['avg']:.4f}/{b['exp_net']:.4f}" if b else "NA"
                    print(f"  {k}: train avg/exp_net {av}  test {bv}")
        # secondary
        for label, df in [("persistent_top20",persist20),("brief_top20",brief20)]:
            tr=df.filter(pl.col("et_date").is_in(list(train_dates))) if df.height else df
            te=df.filter(pl.col("et_date").is_in(list(test_dates))) if df.height else df
            mt=metrics(tr,cost); me2=metrics(te,cost)
            print(f"\n -- {label} train n={tr.height} / test n={te.height} --")
            for h in HORIZONS:
                k=f"h{h}"
                a=mt.get(k); b=me2.get(k)
                if a or b:
                    av=f"{a['avg']:.4f}/{a['exp_net']:.4f}" if a else "NA"
                    bv=f"{b['avg']:.4f}/{b['exp_net']:.4f}" if b else "NA"
                    print(f"  {k}: train avg/exp_net {av}  test {bv}")

        # verdict helper
        # compare persistent vs brief exp_net at horizons 5,15,60
        def avg_exp(df): return metrics(df,cost)
        mp=avg_exp(persist); mb2=avg_exp(brief)
        wins=sum(1 for h in [5,15,60] if mp.get(f"h{h}") and mb2.get(f"h{h}") and mp[f"h{h}"]["exp_net"]>mb2[f"h{h}"]["exp_net"])
        print(f"\nPersistent beats brief at {wins}/3 key horizons (5,15,60m)")
        # OOS persistence: train persistent beats brief?
        tr_p=persist.filter(pl.col("et_date").is_in(list(train_dates))) if persist.height else persist
        tr_b=brief.filter(pl.col("et_date").is_in(list(train_dates))) if brief.height else brief
        te_p=persist.filter(pl.col("et_date").is_in(list(test_dates))) if persist.height else persist
        te_b=brief.filter(pl.col("et_date").is_in(list(test_dates))) if brief.height else brief
        mtp=metrics(tr_p,cost); mtb=metrics(tr_b,cost); mep=metrics(te_p,cost); meb=metrics(te_b,cost)
        train_wins=sum(1 for h in [5,15,60] if mtp.get(f"h{h}") and mtb.get(f"h{h}") and mtp[f"h{h}"]["exp_net"]>mtb[f"h{h}"]["exp_net"])
        test_wins=sum(1 for h in [5,15,60] if mep.get(f"h{h}") and meb.get(f"h{h}") and mep[f"h{h}"]["exp_net"]>meb[f"h{h}"]["exp_net"])
        print(f"Train persistent>brief {train_wins}/3, Test persistent>brief {test_wins}/3")

    # write outputs
    out=args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    if all_sig.height:
        all_sig.write_parquet(out)
        print(f"\nWrote {out} ({all_sig.height} rows)")
    summary={
        "cost_bps_per_side":args.cost_bps,"roundtrip_cost":cost,"min_persist":args.min_persist,
        "n_persistent_top5":persist.height,"n_brief_top5":brief.height,
        "n_persistent_top20":persist20.height,"n_brief_top20":brief20.height,
        "metrics_persistent_top5":metrics(persist,cost),
        "metrics_brief_top5":metrics(brief,cost),
        "metrics_persistent_top20":metrics(persist20,cost),
        "metrics_brief_top20":metrics(brief20,cost),
    }
    jpath=out.parent/"h005_summary.json"
    with open(jpath,"w") as f: json.dump(summary,f,indent=2,default=str)
    print(f"Wrote {jpath}")

    # update ledgers
    # determine verdict: killed if no persistent>brief edge net positive and OOS
    mp2=metrics(persist,cost)
    # if any horizon has persistent exp_net >0 and beats brief, maybe partial else killed
    has_edge=any(v["exp_net"]>0 for v in mp2.values()) if mp2 else False
    beats= wins>=2 if 'wins' in locals() else False
    oos_ok= test_wins>=2 if 'test_wins' in locals() else False
    if has_edge and beats and oos_ok:
        verdict="promising"
    elif has_edge and beats:
        verdict="mixed"
    else:
        verdict="killed"
    exp_id="EXP-H005-2025-07"
    # append EXPERIMENTS.jsonl
    import datetime
    exp_rec={
        "id":exp_id,"hypothesis_id":"H005",
        "description":f"Rank persistence top5 >={args.min_persist}m vs brief 1-2m, top20 >=10m secondary, 10bps/side",
        "period":{"train":["2025-07-02","2025-07-22"],"test":["2025-07-23","2025-07-31"],"all":["2025-07-02","2025-07-31"]},
        "data":{"ranked":"factory/artifacts/ranked_*.parquet(21 files)","events":f"persistent {persist.height} brief {brief.height}"},
        "params":{"min_persist_top5":args.min_persist,"min_persist_top20":10,"cost_bps_per_side":args.cost_bps,"roundtrip":cost},
        "results":summary,
        "verdict":f"{verdict}",
        "artifacts":[str(out),str(jpath),"factory/scripts/experiment_h005.py"],
        "timestamp":datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    with open("factory/EXPERIMENTS.jsonl","a") as f:
        f.write(json.dumps(exp_rec, default=str)+"\n")
    # update HYPOTHESES.jsonl
    import json as js
    hyps=[]
    with open("factory/HYPOTHESES.jsonl") as f:
        for line in f:
            hyps.append(js.loads(line))
    for h in hyps:
        if h["id"]=="H005":
            h["status"]="tested"
            h["linked_experiments"]=["EXP-H005-2025-07"]
            h["verdict"]=verdict
            h["tested_date"]="2025-07-02:2025-07-31"
            h["n_signals"]=persist.height+brief.height
            # reason summary
            mp_s=metrics(persist,cost); mb_s=metrics(brief,cost)
            h["reason"]=f"persistent n={persist.height} brief n={brief.height} wins {wins}/3 cost {args.cost_bps}bps/side train {train_wins}/3 test {test_wins}/3 verdict {verdict}. Persistent metrics {mp_s} vs brief {mb_s}"
    with open("factory/HYPOTHESES.jsonl","w") as f:
        for h in hyps:
            f.write(js.dumps(h)+"\n")
    print(f"Updated HYPOTHESES.jsonl H005 -> {verdict}, appended {exp_id}")

if __name__=="__main__":
    main()
