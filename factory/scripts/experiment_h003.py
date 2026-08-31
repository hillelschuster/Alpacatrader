"""H003 gain x volume x TOD interaction — ponytail minimal."""
import argparse, json
from pathlib import Path
import polars as pl

HORIZONS=[5,15,30,60]

def gain_bucket(g):
    if g is None: return "unknown"
    if g < 10: return "<10%"
    if g < 20: return "10-20%"
    return "20%+"

def tod_bucket(et):
    hm=et.hour*60+et.minute
    if hm < 600: return "09:30-10:00"
    if hm < 720: return "10:00-12:00"
    if hm < 840: return "12:00-14:00"
    return "14:00-16:00"

def cell_metrics(sub, cost):
    out={}
    for h in HORIZONS:
        c=f"fwd_ret_{h}m"
        if c not in sub.columns: continue
        s=sub[c].drop_nulls()
        if s.len()==0: continue
        wr=(s>0).sum()/s.len()
        wr_net=(s>cost).sum()/s.len()
        avg=s.mean(); med=s.median()
        exp_net=avg-cost
        out[f"h{h}"]={"n":s.len(),"wr":wr,"wr_net":wr_net,"avg":avg,"median":med,"exp_net":exp_net}
    return out

def run(events,clean,cost_bps,out):
    cost=cost_bps*2/10000
    ev=pl.read_parquet(events)
    print(f"Events total: {ev.height}")
    # ensure et & et_date
    if "et" not in ev.columns:
        ev=ev.with_columns(pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("et"))
    ev=ev.with_columns(pl.col("et").dt.date().alias("et_date"))
    # filter nulls where forward needed? keep all but metrics handle nulls
    # vol terciles per day
    # compute quantile thresholds per et_date
    qs=ev.group_by("et_date").agg([
        pl.col("dollar_volume").quantile(1/3).alias("q1"),
        pl.col("dollar_volume").quantile(2/3).alias("q2"),
    ])
    ev=ev.join(qs,on="et_date",how="left")
    ev=ev.with_columns(
        pl.when(pl.col("dollar_volume")<=pl.col("q1")).then(pl.lit("low"))
        .when(pl.col("dollar_volume")<=pl.col("q2")).then(pl.lit("med"))
        .otherwise(pl.lit("high")).alias("vol_bucket")
    )
    # gain bucket
    ev=ev.with_columns(pl.col("pct_gain").map_elements(gain_bucket, return_dtype=pl.String).alias("gain_bucket"))
    # tod bucket (override events time_bucket)
    ev=ev.with_columns(pl.col("et").map_elements(tod_bucket, return_dtype=pl.String).alias("tod_bucket"))
    ev=ev.with_columns((pl.col("gain_bucket")+"|"+pl.col("vol_bucket")+"|"+pl.col("tod_bucket")).alias("cell"))

    # overall cell stats
    cells=ev.group_by("cell").len().sort("len",descending=True)
    print(f"Cells: {cells.height} (expect up to 36), total events {ev.height}")
    print(f" Cost: {cost_bps} bps/side, roundtrip {cost:.4f}")

    # per-cell metrics
    rows=[]
    for cell in ev["cell"].unique().to_list():
        sub=ev.filter(pl.col("cell")==cell)
        m=cell_metrics(sub,cost)
        # split gain/vol/tod
        g,v,t=cell.split("|")
        for hk,vals in m.items():
            rows.append({"cell":cell,"gain":g,"vol":v,"tod":t,"horizon":hk,**vals})
    cell_df=pl.DataFrame(rows) if rows else pl.DataFrame()

    def print_rank(h, top=True):
        hk=f"h{h}"
        sub=cell_df.filter(pl.col("horizon")==hk)
        if sub.height==0:
            print(f" no cells for h={h}")
            return
        sub=sub.sort("exp_net",descending=top)
        label="TOP" if top else "BOTTOM"
        print(f"\n=== {label} 5 cells by exp_net @ {h}m (cost {cost_bps}bps/side) ===")
        for r in sub.head(5).iter_rows(named=True):
            print(f" {r['cell']:30s} n={r['n']:4d} wr={r['wr']:.3f} wr_net={r['wr_net']:.3f} avg={r['avg']:.4f} exp_net={r['exp_net']:.4f}")

    print_rank(15,True); print_rank(15,False)
    print_rank(60,True); print_rank(60,False)

    # also overall by each dimension
    print("\n=== by gain_bucket ===")
    for g in ["<10%","10-20%","20%+"]:
        sub=ev.filter(pl.col("gain_bucket")==g)
        if sub.height==0: continue
        m=cell_metrics(sub,cost)
        print(f" {g} n={sub.height}")
        for hk,v in m.items():
            print(f"  {hk}: wr={v['wr']:.3f} wr_net={v['wr_net']:.3f} avg={v['avg']:.4f} exp_net={v['exp_net']:.4f} n={v['n']}")

    print("\n=== by vol_bucket ===")
    for v in ["low","med","high"]:
        sub=ev.filter(pl.col("vol_bucket")==v)
        if sub.height==0: continue
        m=cell_metrics(sub,cost)
        print(f" {v} n={sub.height}")
        for hk,vals in m.items():
            print(f"  {hk}: wr={vals['wr']:.3f} wr_net={vals['wr_net']:.3f} avg={vals['avg']:.4f} exp_net={vals['exp_net']:.4f} n={vals['n']}")

    print("\n=== by tod_bucket ===")
    for t in ["09:30-10:00","10:00-12:00","12:00-14:00","14:00-16:00"]:
        sub=ev.filter(pl.col("tod_bucket")==t)
        if sub.height==0: continue
        m=cell_metrics(sub,cost)
        print(f" {t} n={sub.height}")
        for hk,vals in m.items():
            print(f"  {hk}: wr={vals['wr']:.3f} wr_net={vals['wr_net']:.3f} avg={vals['avg']:.4f} exp_net={vals['exp_net']:.4f} n={vals['n']}")

    # pairwise early vs late, high vs low
    print("\n=== pairwise ===")
    for h in HORIZONS:
        c=f"fwd_ret_{h}m"
        early=ev.filter(pl.col("tod_bucket").is_in(["09:30-10:00","10:00-12:00"]))[c].drop_nulls()
        late=ev.filter(pl.col("tod_bucket").is_in(["12:00-14:00","14:00-16:00"]))[c].drop_nulls()
        if early.len() and late.len():
            diff=early.mean()-late.mean()
            print(f" h={h}m early(09:30-12:00) n={early.len()} avg={early.mean():.4f} vs late(12-16) n={late.len()} avg={late.mean():.4f} diff={diff:.4f} (early-late)")
        hv=ev.filter(pl.col("vol_bucket")=="high")[c].drop_nulls()
        lv=ev.filter(pl.col("vol_bucket")=="low")[c].drop_nulls()
        if hv.len() and lv.len():
            diff=hv.mean()-lv.mean()
            print(f" h={h}m high-vol n={hv.len()} avg={hv.mean():.4f} vs low-vol n={lv.len()} avg={lv.mean():.4f} diff={diff:.4f} (high-low)")

    # chronological split
    dates=sorted([d for d in ev["et_date"].unique().to_list() if d is not None])
    print(f"\n=== Chronological split: train first 14 / test last 7 ===")
    print(f" dates: {dates[0]} .. {dates[-1]} ({len(dates)} days)")
    split=14
    train_dates=set(dates[:split]); test_dates=set(dates[split:])
    train=ev.filter(pl.col("et_date").is_in(list(train_dates)))
    test=ev.filter(pl.col("et_date").is_in(list(test_dates)))
    print(f" train {len(train_dates)}d n={train.height}  test {len(test_dates)}d n={test.height}")

    def best_cells(df, h):
        hk=f"h{h}"
        rows=[]
        for cell in df["cell"].unique().to_list():
            sub=df.filter(pl.col("cell")==cell)
            m=cell_metrics(sub,cost)
            if hk in m:
                rows.append((cell,m[hk]["exp_net"],m[hk]["n"]))
        rows.sort(key=lambda x: x[1], reverse=True)
        return rows

    for h in [15,60]:
        tr_best=best_cells(train,h)
        print(f"\n -- h={h}m train best 5 --")
        for cell,exp,n in tr_best[:5]:
            print(f"  {cell:30s} exp_net={exp:.4f} n={n}")
            # test performance of same cell
            sub=test.filter(pl.col("cell")==cell)
            m=cell_metrics(sub,cost)
            hk=f"h{h}"
            if hk in m:
                print(f"    -> test: n={m[hk]['n']} exp_net={m[hk]['exp_net']:.4f} wr={m[hk]['wr']:.3f}")
            else:
                print(f"    -> test: n=0")
        # correlation check: do train top stay top on test?
        te_best=best_cells(test,h)
        print(f" -- h={h}m test best 5 --")
        for cell,exp,n in te_best[:5]:
            print(f"  {cell:30s} exp_net={exp:.4f} n={n}")
        # overlap
        tr_top=set(c for c,_,_ in tr_best[:5])
        te_top=set(c for c,_,_ in te_best[:5])
        print(f" overlap top5 train/test @ {h}m: {len(tr_top & te_top)}/5 {tr_top & te_top}")

    # write outputs
    out=Path(out)
    out.parent.mkdir(parents=True,exist_ok=True)
    # drop helper q cols before write but keep buckets
    ev_out=ev.drop(["q1","q2"]) if "q1" in ev.columns else ev
    ev_out.write_parquet(out)
    print(f"\nWrote {out} ({ev_out.height} rows)")
    # summary json
    summary={
        "cost_bps_per_side":cost_bps,"roundtrip_cost":cost,
        "n_events":ev.height,"n_dates":len(dates),"dates":[str(d) for d in dates],
        "train_dates":[str(d) for d in sorted(train_dates)],"test_dates":[str(d) for d in sorted(test_dates)],
        "cells":cell_df.sort("exp_net",descending=True).to_dicts() if cell_df.height else [],
    }
    jpath=out.parent/"h003_summary.json"
    with open(jpath,"w") as f: json.dump(summary,f,indent=2,default=str)
    print(f"Wrote {jpath}")
    # also write csv for readability
    if cell_df.height:
        csv_path=out.parent/"h003_cells.csv"
        cell_df.sort(["horizon","exp_net"],descending=[False,True]).write_csv(csv_path)
        print(f"Wrote {csv_path}")
    return summary

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--events",type=Path,default=Path("factory/artifacts/events_all.parquet"))
    p.add_argument("--clean",type=Path,default=Path("data/clean_ohlcv_2025-07.parquet"))
    p.add_argument("--cost-bps",type=float,default=10)
    p.add_argument("--out",type=Path,default=Path("factory/artifacts/h003_results.parquet"))
    a=p.parse_args()
    run(a.events,a.clean,a.cost_bps,a.out)

if __name__=="__main__":
    main()
