#!/usr/bin/env python3
"""Full-policy entry development curves; per-day resume; no broker API."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from h025_research_core import Policy, allowed_dates, load_day, replay_day

ROOT = Path(__file__).resolve().parents[2]
DEPTHS = (.06,.08,.09,.10,.11,.12,.14,.16,.20)
COSTS = (100,150,200)
NOTIONALS = (250,500,1000)
ORDERINGS = ("legacy","pessimistic","optimistic")


def policy_grid():
    cells = {(d,120,True,True) for d in DEPTHS}
    cells.update((d,e,r,True) for d in (.06,.08,.10,.12,.16)
                 for e in (10,30,60,120) for r in (False,True))
    cells.update((d,e,r,False) for d in (.08,.10,.12,.16)
                 for e in (30,120) for r in (False,True))
    return [(f"d{d*100:02.0f}_e{e:03}_" + ("rolling" if r else "static") +
             ("_rearm" if a else "_once"), pf,
             replace(Policy(),discount=d,expiry_minutes=e,refresh=r,rearm=a,prior_flush_min=pf))
            for d,e,r,a in sorted(cells) for pf in (0,2)]


def scalar(value):
    if isinstance(value,(np.integer,np.floating,np.bool_)):
        return value.item()
    if isinstance(value,Path):
        return str(value)
    raise TypeError(type(value).__name__)


def save_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp = path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(value,indent=2,default=scalar,allow_nan=False)+"\n")
    tmp.replace(path)


def occupancy(orders,fills,notional):
    pending,held,events = 0.,0.,[]
    for row in orders.itertuples(index=False):
        dollars = int(np.floor(notional/row.B))*row.B
        start,end = int(row.t0),int(row.end_t)
        pending += dollars*max(0,end-start)
        if end > start and dollars:
            events.extend(((start,dollars),(end,-dollars)))
    for row in fills.itertuples(index=False):
        dollars = int(np.floor(notional/row.B))*row.B
        start,end = int(row.tf),int(row.exit_t)
        held += dollars*max(0,end-start)
        if end > start and dollars:
            events.extend(((start,dollars),(end,-dollars)))
    peak,current = 0.,0.
    # Half-open clock intervals: release before replacement at equal times.
    for _,delta in sorted(events,key=lambda event:(event[0],event[1])):
        current += delta
        peak = max(peak,current)
    return pending,held,peak


def summarize_day(date,cell,pf,ordering,policy,replay,notionals=NOTIONALS):
    fills,orders = replay.fills,replay.orders
    populations = [("all_orders" if pf == 0 else "pf2_preorder",fills)]
    if pf == 0:
        populations.append(("pf2_posthoc",fills[fills.prior_flush >= 2]))
    occupancies = {n:occupancy(orders,fills,n) for n in notionals}
    records = []
    for population,s in populations:
        returns = s.ret_gross.to_numpy(float)
        prices = s.B.to_numpy(float)
        causes = s.exit_reason.replace({"time":"horizon","eod":"session"}).to_numpy(str)
        if not np.isin(causes,("target","stop","horizon","session")).all():
            raise ValueError("Unmapped replay exit reason")
        for notional in notionals:
            quantity = np.floor(notional/prices).astype(np.int64)
            used = quantity*prices
            pending,held,peak = occupancies[notional]
            for cost in COSTS:
                net = returns-cost/10000
                pnl = used*net
                row = dict(date=date,month=date[:7],year=date[:4],cell=cell,
                           population=population,ordering=ordering,discount=policy.discount,
                           expiry=policy.expiry_minutes,refresh=policy.refresh,rearm=policy.rearm,
                           friction_bps=cost,order_notional=notional,fills=len(s),orders=len(orders),
                           net_return_sum=float(net.sum()),net_dollars=float(pnl.sum()),
                           deployed_dollars=float(used.sum()),quantity_sum=int(quantity.sum()),
                           positive_quantity_fills=int((quantity>0).sum()),
                           zero_quantity_fills=int((quantity==0).sum()),
                           holding_minutes_sum=float((s.exit_t-s.tf).sum()),
                           inclusive_bar_holding_minutes_sum=float((s.exit_t-s.tf+1).sum()),
                           held_dollar_minutes_inclusive_bar_proxy=float((used*(s.exit_t-s.tf+1)).sum()),
                           waiting_minutes_sum=float((s.tf-s.t0).sum()),
                           reserved_dollar_minutes=pending,held_dollar_minutes=held,
                           peak_committed_dollars=peak,
                           occupancy_population="all_orders" if pf == 0 else "pf2_preorder",
                           entry_gap_fills=int(s.entry_gap.sum()),stop_gap_fills=int(s.stop_gap.sum()),
                           ambiguous_fills=int((s.ambiguous_fill_high | s.ambiguous_both).sum()),
                           worst_fill_net_return=float(net.min()) if len(net) else None,
                           worst_fill_net_dollars=float(pnl.min()) if len(pnl) else None)
                for reason in ("target","stop","horizon","session"):
                    mask = causes == reason
                    row[reason+"_fills"] = int(mask.sum())
                    row[reason+"_net_dollars"] = float(pnl[mask].sum())
                for label,mask in (("entry_gap",s.entry_gap.to_numpy(bool)),
                                   ("stop_gap",s.stop_gap.to_numpy(bool))):
                    row[label+"_net_dollars"] = float(pnl[mask].sum())
                records.append(row)
    return records


GROUP = ["cell","population","ordering","discount","expiry","refresh","rearm",
         "friction_bps","order_notional","occupancy_population"]
SUMS = ["fills","positive_quantity_fills","orders","net_return_sum","net_dollars",
        "deployed_dollars","quantity_sum","zero_quantity_fills","holding_minutes_sum",
        "waiting_minutes_sum","reserved_dollar_minutes","held_dollar_minutes",
        "inclusive_bar_holding_minutes_sum","held_dollar_minutes_inclusive_bar_proxy",
        "entry_gap_fills","stop_gap_fills","ambiguous_fills","entry_gap_net_dollars",
        "stop_gap_net_dollars"]
SUMS += [reason+suffix for reason in ("target","stop","horizon","session")
         for suffix in ("_fills","_net_dollars")]


class CurveAccumulator:
    """Bounded research summary memory; only daily PnL retained for tails."""
    def __init__(self, metadata):
        self.metadata = metadata.reset_index(drop=True)
        count = len(metadata)
        self.total = np.zeros((count,len(SUMS)))
        self.days = 0
        self.active = np.zeros(count,dtype=int)
        self.positive = np.zeros(count,dtype=int)
        self.peak = np.zeros(count)
        self.worst_return = np.full(count,np.inf)
        self.worst_dollars = np.full(count,np.inf)
        self.daily_pnl = []

    def add(self, frame):
        if not frame[GROUP].reset_index(drop=True).equals(self.metadata):
            raise ValueError("Daily policy row order/metadata changed")
        self.total += frame[SUMS].to_numpy(float)
        self.days += 1
        self.active += frame.fills.to_numpy() > 0
        self.positive += frame.net_dollars.to_numpy() > 0
        self.peak = np.maximum(self.peak,frame.peak_committed_dollars)
        self.worst_return = np.minimum(self.worst_return,frame.worst_fill_net_return.fillna(np.inf))
        self.worst_dollars = np.minimum(self.worst_dollars,frame.worst_fill_net_dollars.fillna(np.inf))
        self.daily_pnl.append(frame.net_dollars.to_numpy(float))

    def finish(self):
        out = self.metadata.copy()
        for j,name in enumerate(SUMS):
            out[name] = self.total[:,j]
        out["trading_days"] = self.days
        out["active_days"] = self.active
        out["positive_days"] = self.positive
        out["peak_committed_dollars"] = self.peak
        pnl = np.asarray(self.daily_pnl)
        out["worst_day_net_dollars"] = pnl.min(axis=0)
        out["day_net_dollars_q05"] = np.quantile(pnl,.05,axis=0)
        out["worst_fill_net_return"] = np.where(np.isfinite(self.worst_return),self.worst_return,np.nan)
        out["worst_fill_net_dollars"] = np.where(np.isfinite(self.worst_dollars),self.worst_dollars,np.nan)
        for name in ("fills","net_return_sum","net_dollars","orders",
                     "reserved_dollar_minutes","held_dollar_minutes"):
            out[name+"_per_day"] = out[name]/self.days
        for name,numerator in (("net_return_per_fill","net_return_sum"),
                               ("holding_minutes_per_fill","holding_minutes_sum"),
                               ("waiting_minutes_per_fill","waiting_minutes_sum")):
            out[name] = out[numerator]/out.fills.replace(0,np.nan)
        out["dollar_return_on_deployed"] = out.net_dollars/out.deployed_dollars.replace(0,np.nan)
        return out


def produce_summaries(checkpoints,output,stage,dates):
    accumulators = {}
    for date in dates:
        frame = pd.read_parquet(checkpoints/(date+".parquet"))
        for kind,label in (("all","all"),("month",date[:7]),("year",date[:4])):
            key = (kind,label)
            if key not in accumulators:
                accumulators[key] = CurveAccumulator(frame[GROUP])
            accumulators[key].add(frame)
    curves = accumulators[("all","all")].finish()
    curves.to_csv(output/(stage+"_curves.csv"),index=False)
    for kind in ("month","year"):
        parts = [acc.finish().assign(**{kind:label}) for (k,label),acc in accumulators.items() if k == kind]
        pd.concat(parts,ignore_index=True).to_csv(output/(stage+"_"+("monthly" if kind == "month" else "yearly")+".csv"),index=False)
    scenarios = curves.pivot(index=[k for k in GROUP if k != "ordering"],columns="ordering",
                             values=["net_dollars_per_day","fills_per_day","net_return_per_fill"])
    scenarios.columns = ["_".join(k) for k in scenarios.columns]
    scenarios.reset_index().to_csv(output/(stage+"_chronology_scenarios.csv"),index=False)
    return curves


def run_day(job):
    """One date's full policy-grid replay; atomic per-day checkpoint."""
    date,data_root,bulk_root,grid = job
    bulk_root = Path(bulk_root)
    target = bulk_root/"daily"/(date+".parquet")
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        return date, "skip"
    day = load_day(Path(data_root),date)
    records,raw_fills,raw_orders = [],[],[]
    for cell,pf,policy in grid:
        for ordering in ORDERINGS:
            # Cashflow reuse is exact only if every candidate bid buys >=1 share.
            max_bid = float(day.states.c.max())*(1-policy.discount) if len(day.states) else 0.
            scenarios = [(None,NOTIONALS)] if max_bid <= min(NOTIONALS) else [(n,(n,)) for n in NOTIONALS]
            for notional,notionals in scenarios:
                sized_policy = replace(policy,order_notional=notional)
                replay = replay_day(day,sized_policy,ordering=ordering)
                records.extend(summarize_day(date,cell,pf,ordering,sized_policy,replay,notionals))
                raw_fills.append(replay.fills.assign(cell=cell,prior_flush_min=pf,ordering=ordering,
                                                    lifecycle_order_notional=notional))
                raw_orders.append(replay.orders.assign(cell=cell,prior_flush_min=pf,ordering=ordering,
                                                      lifecycle_order_notional=notional))
    for label,rows in (("fills",raw_fills),("orders",raw_orders)):
        directory = bulk_root/label
        directory.mkdir(parents=True,exist_ok=True)
        pd.concat(rows,ignore_index=True).to_parquet(directory/(date+".parquet"),index=False)
    save_json(bulk_root/"source"/(date+".json"),day.source)
    tmp = target.with_suffix(".parquet.tmp")
    pd.DataFrame(records).sort_values(GROUP).reset_index(drop=True).to_parquet(tmp,index=False)
    tmp.replace(target)
    return date, "done"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root",type=Path,required=True)
    ap.add_argument("--bulk-root",type=Path,required=True)
    ap.add_argument("--output",type=Path,default=ROOT/"factory/artifacts/h025_research/entry")
    ap.add_argument("--stage",choices=("dev533","allowlist"),default="allowlist")
    ap.add_argument("--limit-days",type=int)
    ap.add_argument("--workers",type=int,default=1)
    args = ap.parse_args()
    dates = sorted(set(allowed_dates(args.data_root,stage="all")))
    dates = [d for d in dates if "2021-02-01" <= d <= "2023-12-29"
             or "2025-03-01" <= d <= "2026-05-29"]
    original = [d for d in dates if d <= "2023-03-14"]
    if args.stage == "dev533":
        dates = original
    if args.limit_days:
        dates = dates[:args.limit_days]
        original = [d for d in original if d in dates]
    if not dates or not original:
        raise ValueError("No paired original development daily files")
    grid = policy_grid()
    signature = hashlib.sha256(Path(__file__).read_bytes()+
                               Path(__file__).with_name("h025_research_core.py").read_bytes()).hexdigest()
    args.bulk_root.mkdir(parents=True,exist_ok=True)
    args.output.mkdir(parents=True,exist_ok=True)
    manifest_path = args.bulk_root/"manifest.json"
    manifest = dict(signature=signature,data_root=str(args.data_root.resolve()),dates=dates,
                    original_days=len(original),legitimate_days=len(dates),
                    excluded=["2024","2025-01","2025-02","2026-06..08"],
                    scope="DEVELOPMENT and already-seen temporal replication, not new OOS",
                    policies=[dict(cell=c,prior_flush_min=pf,policy=asdict(p)) for c,pf,p in grid],
                    costs_bps=COSTS,order_notionals=NOTIONALS,orderings=ORDERINGS,
                    integer_sizing="q=floor(N/B), dollars=qB. Independent notional lifecycle if any candidate bid exceeds250; otherwise identical lifecycle proof via maximum candidate B.",
                    range_normalized_discount="Not selected: historical causal trailing-range activity is fragile across years and does not demonstrate range-normalized discount efficacy; future flush depth is not a calibration feature.",
                    limitations=["No queue probability or broker API","Fixed original H025 NAME qualification",
                                 "Posthoc pf2 occupancy is parent all-orders lifecycle",
                                 "Summed per-order return per day is not account CAGR",
                                 "No account cap or competing-strategy allocation",
                                 "Clock occupancy excludes unknown intrabar duration; inclusive-bar proxy separately reported",
                                 "Chronology scenarios are not guaranteed aggregate economic bounds: exits change rearm"])
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        if old["signature"] != signature or old["data_root"] != manifest["data_root"]:
            raise ValueError("Checkpoint source/code mismatch; use fresh bulk root")
    save_json(manifest_path,manifest)
    save_json(args.output/"manifest.json",manifest)
    checkpoints = args.bulk_root/"daily"
    checkpoints.mkdir(exist_ok=True)

    def run_span(span,label):
        jobs = [(d,str(args.data_root),str(args.bulk_root),grid) for d in span]
        if args.workers > 1:
            with ProcessPoolExecutor(max_workers=args.workers) as pool:
                results = pool.map(run_day,jobs)
                for number,(date,status) in enumerate(results,1):
                    if number % 25 == 0 or number == len(jobs):
                        print(f"entry {label} {number}/{len(jobs)} {date} {status}",flush=True)
        else:
            for number,job in enumerate(jobs,1):
                date,status = run_day(job)
                if number % 25 == 0 or number == len(jobs):
                    print(f"entry {label} {number}/{len(jobs)} {date} {status}",flush=True)

    first_span = [d for d in dates if d <= original[-1]]
    rest_span = [d for d in dates if d > original[-1]]
    run_span(first_span,"dev")
    dev = [d for d in dates if d <= "2023-03-14"]
    if dev:
        produce_summaries(checkpoints,args.output,"dev533",dev)
    if rest_span:
        run_span(rest_span,"replication")
    curves = produce_summaries(checkpoints,args.output,args.stage,dates)
    replication = [d for d in dates if d > "2023-03-14"]
    if replication:
        produce_summaries(checkpoints,args.output,"replication",replication)
    print(curves[(curves.population == "pf2_preorder") & (curves.ordering == "pessimistic") &
                 (curves.friction_bps == 100) & (curves.order_notional == 500) &
                 (curves.expiry == 120) & curves.refresh & curves.rearm]
          [["discount","fills","net_return_per_fill","net_dollars_per_day"]].to_string(index=False))


if __name__ == "__main__":
    main()
