#!/usr/bin/env python3
"""Research-only daily H025 replay. Legacy ALL lifecycle then posthoc pf2;
prior_flush_min=2 is a distinct causal PRE-order policy. Native clock t labels
completed [t-1,t). Chronology scenarios have rearm feedback, not portfolio bounds.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Callable
import numpy as np
import pandas as pd
from lb18_episodes import build

HERE = Path(__file__).resolve().parent
ART = HERE.parents[1] / "factory/artifacts/h025_research/core"
VERSION = "daily-v1"
PATH_COLUMNS = ["date", "ticker", "t", "o", "h", "l", "c", "v", "gain_c", "n_bars"]
LB_COLUMNS = ["date", "ticker", "t", "rank", "gain", "px"]
FILL_COLUMNS = ["date", "month", "ticker", "t0", "tf", "gain", "rank", "r15", "prior_flush", "fc", "ret", "exit_t", "B", "c0", "ret_gross", "exit_px", "exit_reason", "entry_gap", "stop_gap", "ambiguous_fill_high", "ambiguous_both", "order_id", "ordering", "source_classifier", "reference_certified", "hold_new_bars", "completion_clock_minutes"]
ORDER_COLUMNS = ["order_id", "date", "ticker", "t0", "end_t", "B", "c0", "status", "fill_tf", "rank", "gain", "prior_flush"]

@dataclass(frozen=True)
class Policy:
    rank_max: int = 3
    gain_min: float = 1.0
    pullback_min: float = -.01
    r15_min: float = .03
    r15_period: int = 15
    prior_flush_min: int = 0
    discount: float = .10
    stop: float = .10
    target: float = 1.0
    hold_bars: int = 30
    hold_minutes: int | None = None
    expiry_minutes: int = 120
    refresh: bool = True
    rearm: bool = True
    friction_bps: float = 100
    order_notional: float | None = None
    flush_counter: str = "legacy_zero"
    fresh_state_only: bool = False

@dataclass
class Day:
    date: str
    paths: pd.DataFrame
    lb: pd.DataFrame
    arrays: dict
    states: pd.DataFrame
    source: dict

@dataclass
class Replay:
    fills: pd.DataFrame
    orders: pd.DataFrame
    def __iter__(self):
        yield self.fills
        yield self.orders

def protection(date):
    if "2021-02-01" <= date <= "2023-03-14":
        return "original533_development"
    if "2023-03-15" <= date <= "2023-12-29" or "2025-03-01" <= date <= "2026-05-29":
        return "seen_temporal_replication"
    if date.startswith("2024-") or "2025-01-01" <= date <= "2025-02-28":
        return "spent_oos_no_new_selection"
    if "2026-06-01" <= date <= "2026-08-31":
        return "cross_strategy_reserved_no_new_selection"
    return "outside_authorized_calendar"

def allowed_dates(data_root, stage="all"):
    if stage not in ("all", "original533", "replication"):
        raise ValueError(stage)
    root = Path(data_root) / "leaderboard"
    result = []
    for p in sorted(root.glob("path_*.parquet")):
        date = p.stem[5:]
        flag = protection(date)
        if not (root / f"lb_{date}.parquet").exists():
            continue
        if flag not in ("original533_development", "seen_temporal_replication"):
            continue
        if stage == "original533" and flag != "original533_development":
            continue
        if stage == "replication" and flag != "seen_temporal_replication":
            continue
        result.append(date)
    return result

def fingerprint(path):
    return {"path": str(path.resolve()), "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

def prepare_day(paths, lb, date, source=None):
    paths, lb = paths.copy(), lb.copy()
    if paths.empty:
        paths = pd.DataFrame(columns=PATH_COLUMNS)
    if lb.empty:
        lb = pd.DataFrame(columns=LB_COLUMNS)
    paths = paths.sort_values(["date", "ticker", "t"]).reset_index(drop=True)
    arrays = build(paths) if len(paths) else {}
    if len(paths):
        grouped = paths.groupby(["date", "ticker"], sort=False)
        paths["pullback"] = paths.c / grouped.c.transform("cummax") - 1
        paths["r15"] = paths.c / grouped.c.shift(15) - 1
        paths["prev_close_implied"] = paths.c / (1 + paths.gain_c)
    else:
        for name in ("pullback", "r15", "prior_flush", "volx", "nprint20", "prev_close_implied"):
            paths[name] = pd.Series(dtype=float)
    paths["is_new"] = False
    paths["pf_carry"] = 0
    for key, g in paths.groupby(["date", "ticker"], sort=False):
        d = arrays[key]
        ix = g.index.to_numpy()
        fresh = d["new"]
        paths.loc[ix, "is_new"] = fresh
        carried = pd.Series(np.where(fresh, paths.loc[ix, "prior_flush"].to_numpy(), np.nan)).ffill().fillna(0).to_numpy()
        paths.loc[ix, "pf_carry"] = carried.astype(int)
    cols = ["is_new", "pf_carry", "date", "ticker", "t", "c", "pullback", "r15", "prior_flush", "volx", "nprint20", "n_bars", "prev_close_implied"]
    states = lb.merge(paths[cols], on=["date", "ticker", "t"], how="left")
    src = {"classifier": "not_certified", "reference_certified": False,
           "feed": "unknown_native_lineage", "adjustment": "raw_unadjusted_by_contract",
           "previous_close": "previous available source session RTH last close, inferred c/(1+gain_c)",
           "split_adjustment": "none in native producer", "protection": protection(date),
           "native_clock": "completion t of [t-1,t), ffilled clock grid, NEW iff n_bars increments",
           "legacy_prior_flush": "build initializes stale positions to zero; ffill does NOT carry nonNEW counts",
           "empty_path": paths.empty, "empty_lb": lb.empty}
    src.update(source or {})
    return Day(date, paths, lb, arrays, states, src)

def load_day(data_root, date):
    root = Path(data_root) / "leaderboard"
    pp, lp = root / f"path_{date}.parquet", root / f"lb_{date}.parquet"
    paths, lb = pd.read_parquet(pp), pd.read_parquet(lp)
    return prepare_day(paths, lb, date, {"inputs": [fingerprint(pp), fingerprint(lp)],
                                       "path_fields": list(paths.columns), "lb_fields": list(lb.columns)})

def _exit(d, jj, B, c0, p, ordering):
    npx = d["newpos"]
    tf = int(d["t"][npx[jj]])
    stop, target = B * (1-p.stop), c0 * p.target
    both = False
    fill_high = bool(d["h"][npx[jj]] >= target)
    for n, j in enumerate(npx[jj:], 1):
        o,h,l,c,t = (float(d[k][j]) for k in ("o","h","l","c","t"))
        low_touch, high_touch = l <= stop, h >= target
        both |= low_touch and high_touch
        if ordering == "optimistic" and high_touch and not (n > 1 and o <= stop):
            return target, int(t), "target", False, fill_high, both, n
        if low_touch:
            return min(o,stop), int(t), "stop", o < stop, fill_high, both, n
        if high_touch and (ordering != "pessimistic" or n > 1 or c >= target):
            return target, int(t), "target", False, fill_high, both, n
        timed = int(t) >= tf + p.hold_minutes if p.hold_minutes is not None else n >= p.hold_bars
        if timed:
            return c, int(t), "time", False, fill_high, both, n
    j = npx[-1]
    return float(d["c"][j]), int(d["t"][j]), "eod", False, fill_high, both, len(npx)-jj

def replay_day(day, policy=Policy(), ordering="legacy", state_filter: Callable | None = None):
    # Exact frozen rearm can reuse state == exit_t and stale intervening states.
    if ordering not in ("legacy", "pessimistic", "optimistic"):
        raise ValueError(ordering)
    if not 1 <= policy.rank_max <= 3:
        raise ValueError("native supply has only top3")
    if not 0 <= policy.discount < 1 or not 0 <= policy.stop < 1 or policy.target <= 0:
        raise ValueError("invalid price policy")
    if policy.hold_bars <= 0 or policy.expiry_minutes < 0 or policy.r15_period <= 0:
        raise ValueError("invalid clock policy")
    if policy.hold_minutes is not None and policy.hold_minutes < 0:
        raise ValueError("invalid clock hold")
    if policy.flush_counter not in ("legacy_zero", "carried"):
        raise ValueError("unknown flush counter")
    cache = getattr(day, "_qualified_groups", {})
    qkey = (id(day.states), policy.rank_max, policy.gain_min, policy.pullback_min,
            policy.r15_min, policy.r15_period, policy.prior_flush_min,
            policy.flush_counter, policy.fresh_state_only)
    groups = cache.get(qkey) if state_filter is None else None
    if groups is None:
        m = day.states
        if policy.flush_counter == "carried":
            m = m.assign(prior_flush=m.pf_carry)
        if policy.r15_period != 15:
            r = day.paths[["date", "ticker", "t", "c"]].copy()
            r["r15"] = r.c / r.groupby(["date", "ticker"],sort=False).c.shift(policy.r15_period)-1
            m = m.drop(columns="r15").merge(r[["date", "ticker", "t", "r15"]],on=["date", "ticker", "t"],how="left")
        mask = ((m.gain >= policy.gain_min) & (m["rank"] <= policy.rank_max)
                & (m.pullback >= policy.pullback_min) & (m.r15 >= policy.r15_min)
                & (m.prior_flush >= policy.prior_flush_min))
        if policy.fresh_state_only:
            mask &= m.is_new
        if state_filter is not None:
            mask &= state_filter(m).fillna(False)
        groups = []
        for key, g in m[mask.fillna(False)].groupby(["date", "ticker"],sort=False):
            gs = g.sort_values("t").reset_index(drop=True)
            groups.append((key, gs.t.to_numpy(), gs.to_dict("records")))
        if state_filter is None:
            cache[qkey] = groups
            day._qualified_groups = cache
    fills, orders = [], []
    for key, st_t, gs in groups:
        d = day.arrays.get(key)
        if d is None:
            continue
        npx, t = d["newpos"], d["t"]
        si,pending,flat,exit_t,completed = 0,None,True,-10**9,False
        for jj,j in enumerate(npx):
            tj = int(t[j])
            if not flat and tj > exit_t:
                flat = True
            while si < len(gs) and int(st_t[si]) < tj:
                rr = gs[si]
                st = int(st_t[si])
                if flat and (policy.rearm or not completed):
                    if pending is not None and st-pending["t0"] > policy.expiry_minutes:
                        pending.update(status="expired",end_t=pending["t0"]+policy.expiry_minutes)
                        pending = None
                    if policy.refresh or pending is None:
                        pos0 = int(np.searchsorted(t,st))
                        c0 = float(d["c"][pos0])
                        B = c0*(1-policy.discount)
                        quantity_ok = policy.order_notional is None or B <= policy.order_notional
                        if c0 > 0 and quantity_ok:
                            if pending is not None:
                                pending.update(status="refreshed",end_t=st)
                            pending = {"order_id":f"{day.date}:{key[1]}:{len(orders)}", "date":key[0],"ticker":key[1],
                                       "t0":st,"end_t":int(t[-1]),"B":B,"c0":c0,"status":"eod","fill_tf":None,
                                       "rank":int(rr["rank"]),"gain":float(rr["gain"]),"prior_flush":int(rr["prior_flush"])}
                            orders.append(pending)
                            row = rr
                si += 1
            if not flat or pending is None:
                continue
            if tj-pending["t0"] > policy.expiry_minutes:
                pending.update(status="expired",end_t=pending["t0"]+policy.expiry_minutes)
                pending = None
                continue
            B,c0 = pending["B"],pending["c0"]
            if d["l"][j] <= B:
                px,ex,reason,stop_gap,fill_high,both,hold_n = _exit(d,jj,B,c0,policy,ordering)
                gross = px/B-1
                fills.append({"date":key[0],"month":key[0][:7],"ticker":key[1],"t0":int(row["t"]),"tf":tj,
                              "gain":float(row["gain"]),"rank":int(row["rank"]),"r15":float(row["r15"]),"prior_flush":int(row["prior_flush"]),
                              "fc":float(d["c"][j])/B-1,"ret":gross-policy.friction_bps/10000,"ret_gross":gross,
                              "exit_t":ex,"B":B,"c0":c0,"exit_px":px,"exit_reason":reason,
                              "entry_gap":bool(d["o"][j]<B),"stop_gap":stop_gap,"ambiguous_fill_high":fill_high,
                              "ambiguous_both":both,"order_id":pending["order_id"],"ordering":ordering,
                              "source_classifier":day.source["classifier"],"reference_certified":day.source["reference_certified"],
                              "hold_new_bars":hold_n,"completion_clock_minutes":ex-tj})
                pending.update(status="filled",end_t=tj,fill_tf=tj)
                pending,flat,exit_t,completed = None,False,ex,True
        if pending is not None:
            pending["end_t"] = min(int(t[-1]),pending["t0"]+policy.expiry_minutes)
    return Replay(pd.DataFrame(fills,columns=FILL_COLUMNS),pd.DataFrame(orders,columns=ORDER_COLUMNS))

def simple(fills,dates):
    n = len(fills)
    return {"days":len(dates),"n":n,"mean":float(fills.ret.mean()) if n else None,
            "total_net_return_per_day":float(fills.ret.sum())/len(dates) if dates else None,
            "fills_per_day":n/len(dates) if dates else None}

def summarize(fills,dates):
    out = simple(fills,dates)
    if len(fills):
        out.update(median=float(fills.ret.median()),positive_share=float((fills.ret>0).mean()),
                   p01=float(fills.ret.quantile(.01)),worst=float(fills.ret.min()))
    for label,width in (("monthly",7),("yearly",4)):
        out[label] = {g:simple(fills[fills.date.str[:width]==g],[d for d in dates if d[:width]==g]) for g in sorted({d[:width] for d in dates})}
    return out

def inventory_report(root,manifests):
    lb = root / "leaderboard"
    paths = {p.stem[5:] for p in lb.glob("path_*.parquet")}
    boards = {p.stem[3:] for p in lb.glob("lb_*.parquet")}
    paired = paths & boards
    net = Path("/home/hillel/sip/net")
    netfiles = {lane:sorted(str(p.relative_to(net)) for p in (net/lane).rglob("*.parquet")) for lane in ("bars","quotes","trades","coverage")}
    return {"classifier":"not_certified","data_root":str(root.resolve()),"allowed_days":len(allowed_dates(root)),
            "original533_days":len(allowed_dates(root,"original533")),"replication_days":len(allowed_dates(root,"replication")),
            "path_days":len(paths),"lb_days":len(boards),"paired_days":len(paired),"path_without_lb":sorted(paths-boards),
            "lb_without_path":sorted(boards-paths),"paired_calendar_protection":{d:protection(d) for d in sorted(paired)},
            "spent_feb2025_days_excluded":len([d for d in paired if d.startswith("2025-02")]),
            "net_root":str(net),"net_availability_metadata_only":netfiles,"input_manifest":manifests,
            "contracts":{"leaderboard":"native top3 gain rounded4, PIT vintage<=day",
                         "reference":"previous source session RTH last close; prior-month fallback may silently produce empty board",
                         "raw_source":"tv_leaderboard.month_path prefers H12_STAGE clean then clean monthly (backfill for >=2026-03); no raw fallback; feed not certified",
                         "split_adjustment":"none in lb18.py; audit_splits is ex-post full-day heuristic not causal certificate",
                         "correction_required_before_new_rank_claim":True,
                         "legacy_prior_flush":"stale positions zero, not carried; exact build reused",
                         "occupancy":"completion-label minutes not exact intrabar timestamps; samebar exits still have exposure",
                         "protection":"2024 and JanFeb2025 spent OOS; JunAug2026 cross-strategy reserved, no new selection"}}

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root",type=Path,required=True)
    ap.add_argument("--output-root",type=Path)
    ap.add_argument("--stage",choices=("all","original533","replication"),default="all")
    ap.add_argument("--dates",nargs="+")
    args = ap.parse_args()
    dates = allowed_dates(args.data_root,args.stage)
    if args.dates:
        if not set(args.dates)<=set(dates):
            raise ValueError("dates outside authorized selection calendar")
        dates = sorted(set(args.dates))
    outroot = args.output_root or args.data_root / "h025_research/core"
    outroot.mkdir(parents=True,exist_ok=True)
    ART.mkdir(parents=True,exist_ok=True)
    variants = [("legacy_all",Policy(),"legacy"),("prepf2_legacy",Policy(prior_flush_min=2),"legacy"),
                ("prepf2_pessimistic",Policy(prior_flush_min=2),"pessimistic"),("prepf2_optimistic",Policy(prior_flush_min=2),"optimistic")]
    all_fills,manifests,parity = [],[],[]
    from lb18_oos import run_engine
    producers = [fingerprint(HERE / name) for name in ("h025_research_core.py", "lb18_episodes.py", "lb18_oos.py")]
    for i,date in enumerate(dates):
        day = load_day(args.data_root,date)
        cache = outroot/date
        cache.mkdir(exist_ok=True)
        manifest = {"version":VERSION,"producers":producers,"source":day.source,"policies":{tag:{**asdict(p),"ordering":ordering} for tag,p,ordering in variants}}
        marker = cache/"complete.json"
        resume = marker.exists() and json.loads(marker.read_text()).get("manifest")==manifest
        if not resume:
            for tag,p,ordering in variants:
                r = replay_day(day,p,ordering)
                r.fills.to_parquet(cache/f"{tag}_fills.parquet",index=False)
                r.orders.to_parquet(cache/f"{tag}_orders.parquet",index=False)
            legacy = pd.read_parquet(cache/"legacy_all_fills.parquet")
            old = run_engine(day.paths.copy(),day.lb.copy()) if not day.paths.empty and not day.lb.empty else pd.DataFrame()
            cols = ["date","month","ticker","t0","tf","gain","rank","r15","prior_flush","fc","ret","exit_t"]
            same = len(old)==len(legacy)
            if len(old) and same:
                same = old[cols].reset_index(drop=True).equals(legacy[cols].reset_index(drop=True))
            if not same:
                raise RuntimeError(f"legacy parity failure {date}")
            marker.write_text(json.dumps({"manifest":manifest,"legacy_engine_exact_parity":same},indent=2))
        saved = json.loads(marker.read_text())
        manifests.append({"date":date,**saved["manifest"]["source"]})
        parity.append(saved["legacy_engine_exact_parity"])
        for tag,_,_ in variants:
            f = pd.read_parquet(cache/f"{tag}_fills.parquet")
            f["variant"] = tag
            all_fills.append(f)
        if (i+1)%25==0 or i==len(dates)-1:
            print(f"daily replay {i+1}/{len(dates)} {date} exact_legacy_parity=True",flush=True)
    fills = pd.concat(all_fills,ignore_index=True) if all_fills else pd.DataFrame(columns=FILL_COLUMNS+["variant"])
    fills.to_parquet(outroot/"fills.parquet",index=False)
    summaries = {}
    for stage in ("original533","replication","all"):
        sd = sorted(set(allowed_dates(args.data_root,stage))&set(dates))
        if not sd:
            continue
        sf = fills[fills.date.isin(sd)]
        views = {tag:sf[sf.variant==tag] for tag,_,_ in variants}
        views["legacy_posthoc_pf2"] = views["legacy_all"][views["legacy_all"].prior_flush>=2]
        summaries[stage] = {tag:summarize(f,sd) for tag,f in views.items()}
    published = {}
    for name in ("lb18_oos_dev","lb18_backbone_fills"):
        path = ART.parents[1]/f"{name}.parquet"
        if not path.exists():
            continue
        ref = pd.read_parquet(path)
        ref = ref[ref.date.isin(dates)]
        matching = sorted(ref.date.unique())
        actual = fills[(fills.variant=="legacy_all")&fills.date.isin(matching)]
        keys = ["date","ticker","t0","tf","exit_t","ret"]
        joined = ref[keys].merge(actual[keys],on=keys[:-1],how="outer",suffixes=("_published","_daily"),indicator=True)
        paired = joined[joined._merge=="both"]
        published[name] = {"matching_allowed_dates_only":matching,"published_n":len(ref),"daily_n":len(actual),
                           "unmatched":int((joined._merge!="both").sum()),
                           "max_abs_ret_difference":float((paired.ret_published-paired.ret_daily).abs().max()) if len(paired) else None,
                           "excluded_oos_or_reserved_outcomes_not_used":True}
    report = {"version":VERSION,"dates":dates,"summaries":summaries,"published_parity":published,
              "exact_frozen_daily_parity":all(parity),"no_tuning":True,"original_frozen_pass_unchanged":True,
              "chronology":"per-fill ordering scenarios with lifecycle feedback; not portfolio bounds","classifier":"not_certified"}
    (ART/"baseline.json").write_text(json.dumps(report,indent=2))
    (ART/"data_inventory.json").write_text(json.dumps(inventory_report(args.data_root,manifests),indent=2))
    print(json.dumps({"days":len(dates),"parity":all(parity),"summary":{k:{v:{x:z for x,z in r.items() if x not in ("monthly","yearly")} for v,r in s.items()} for k,s in summaries.items()}},indent=2),flush=True)

if __name__ == "__main__":
    main()
