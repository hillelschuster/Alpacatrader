#!/usr/bin/env python3
"""Causal decision trajectories of original top-gainer claims, discovery only.

Events are sampling rulers, not trade rules. Past-state/history is constructed on the
full minute grid before thinning. Labels compare inherited-share liquidation dollars;
entry fee is sunk, the common future sell fee cancels in relative continuation value.
All forward prices remain labelled columns, never feature_* predictors.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
import hashlib
import json
import math
import os
from pathlib import Path

os.environ.setdefault("POLARS_MAX_THREADS", "2")
import polars as pl
import lifecycle_study as ls

CLOCKS = (540, 560, 569, 571)
KINDS = ("entry", "profit5", "push", "damage", "flush", "repair", "failed_repair",
         "rank_loss", "rank_recovery", "new_high", "giveback", "stall", "reclaim",
         "resurrection", "fade_ruler", "heartbeat", "terminal")
HISTORY = ("ret_fill", "dd_from_high", "recovery_from_low", "race_rank", "v5", "dvol5")
KEYS = ["day", "clock", "rank", "ticker"]


def finite(value):
    return value is not None and math.isfinite(float(value))


def load_day(day, data_root):
    panel = ls.load_panel([day], data_root, clocks=CLOCKS, with_tape=False)
    # The generic tape loader drops availability and zero-fills uncovered counts.
    # Use raw flags explicitly; do not expose retrospective whole-day quality labels.
    ls.assert_tape_coverage([day], data_root)
    tape = pl.read_parquet(ls.v2_dir(data_root)/"tape"/f"{day}.parquet")
    keep = [c for c in ls.TAPE_FEATS if c in tape.columns]
    tape = tape.select(["day", "ticker", "t", "tape_covered", "minute_present", "quote_present"] + keep)
    tape = tape.with_columns(pl.col("t").cast(pl.Int64))
    panel = panel.with_columns(pl.col("t").cast(pl.Int64)).join(tape, on=["day","ticker","t"], how="left")
    # The raw net includes future day winners and later snapshot leaders. Data
    # availability itself leaks unless the name was admitted by a causal snapshot.
    candidate_path=data_root/"sip/candidates"/f"{day}.json"
    candidate=json.loads(candidate_path.read_text())
    admitted_trades, admitted_quotes={},{}
    for snapshot in candidate["snapshots"]:
        if snapshot["pop"] not in ("A_pm","B"):
            continue  # A_open may use delayed opens; no causal-time proof here.
        at=int(snapshot["T"])
        for item in snapshot.get("top",[]):
            admitted_trades[item["symbol"]]=min(at,admitted_trades.get(item["symbol"],10**9))
        for symbol in snapshot.get("margin",[]):
            admitted_trades[symbol]=min(at,admitted_trades.get(symbol,10**9))
        for item in snapshot.get("top",[])[:3]:
            admitted_quotes[item["symbol"]]=min(at,admitted_quotes.get(item["symbol"],10**9))
    admission=pl.DataFrame({"ticker":panel["ticker"].unique().to_list()}).with_columns(
        pl.col("ticker").replace_strict(admitted_trades,default=None,return_dtype=pl.Int64).alias("_tr_admitted"),
        pl.col("ticker").replace_strict(admitted_quotes,default=None,return_dtype=pl.Int64).alias("_q_admitted"))
    panel=panel.join(admission,on="ticker",how="left")
    masks = []
    for c in keep:
        available = pl.col("tape_covered").fill_null(False)
        # A zero emitted for a symbol absent from raw acquisition is not inactivity.
        # Retain trade features only with actual past print evidence in this minute.
        if c.startswith("tr_"):
            available = available & pl.col("minute_present").fill_null(False) & (pl.col("t")>=pl.col("_tr_admitted")).fill_null(False)
        else:
            available = available & pl.col("quote_present").fill_null(False) & (pl.col("t")>=pl.col("_q_admitted")).fill_null(False)
        if "15m" in c:
            available = available & (pl.col("t") >= 580)
        elif "5m" in c:
            available = available & (pl.col("t") >= 570)
        masks.append(pl.when(available & pl.col(c).is_finite()).then(pl.col(c)).otherwise(None).alias(c))
    panel = panel.with_columns(masks).sort(KEYS + ["t"])
    past_cols = list(dict.fromkeys(ls.PANEL_FEATS + ls.PM_FEATS + ls.RACE_FEATS + ls.PEER_FEATS))
    expr = [pl.when(pl.col(c).is_finite()).then(pl.col(c).cast(pl.Float64)).otherwise(None)
            .alias(f"feature_{c}") for c in past_cols if c in panel.columns and panel.schema[c].is_numeric()]
    expr += [pl.col("t").cast(pl.Float64).alias("feature_t"),
             pl.col("clock").cast(pl.Float64).alias("feature_clock"),
             pl.col("rank").cast(pl.Float64).alias("feature_rank"),
             pl.col("px").cast(pl.Float64).alias("feature_px"),
             pl.col("fill_px").cast(pl.Float64).alias("feature_fill_px"),
             (pl.col("session_end")-pl.col("t")).cast(pl.Float64).alias("feature_remaining_session")]
    expr += [pl.col(c).cast(pl.Float64).alias(f"feature_tape_{c}") for c in keep]
    for h in (5,15,30):
        for c in HISTORY:
            if c in panel.columns:
                expr.append((pl.col(c)-pl.col(c).shift(h).over(KEYS)).alias(f"feature_hist_{c}_delta{h}"))
        observed = pl.col("ret_fill").is_finite().cast(pl.Float64)
        above = pl.when(pl.col("ret_fill").is_finite()).then((pl.col("ret_fill")>=0).cast(pl.Float64)).otherwise(None)
        expr += [observed.rolling_sum(h, min_samples=h).over(KEYS).alias(f"feature_hist_known{h}"),
                 above.rolling_mean(h, min_samples=h).over(KEYS).alias(f"feature_hist_above_fill{h}"),
                 pl.col("ret1").rolling_std(h, min_samples=h).over(KEYS)
                 .alias(f"feature_hist_ret1_std{h}")]
    panel = panel.with_columns(expr)
    # Peer context is observable scanner context, not the policy's changing holdings.
    for n in (3,5):
        scan_ret = (pl.when(pl.col("status") == "missing").then(None)
                    .when(pl.col("filled_asof") & pl.col("ret_fill").is_finite()).then(pl.col("ret_fill"))
                    .when(~pl.col("filled_asof")).then(0.0).otherwise(None))
        peer = (panel.filter(pl.col("rank")<=n).group_by(["day","clock","t"])
                .agg(pl.when(scan_ret.is_not_null().all()).then(scan_ret.mean()).otherwise(None)
                     .alias(f"feature_scan{n}_mean_ret"),
                     pl.when(scan_ret.is_not_null().all()).then((scan_ret>0).sum().cast(pl.Float64))
                     .otherwise(None).alias(f"feature_scan{n}_positive"),
                     scan_ret.is_not_null().sum().cast(pl.Float64).alias(f"feature_scan{n}_known")))
        panel = panel.join(peer, on=["day","clock","t"], how="left")
    return panel.sort(KEYS + ["t"])


def numeric(row, key):
    value = row.get(key)
    return float(value) if finite(value) else None


def flags_for(row, prev, memory):
    ret, dd, r5 = (numeric(row,k) for k in ("ret_fill","dd_from_high","ret5"))
    pg, msh, rv = (numeric(row,k) for k in ("peak_gain","minutes_since_high","dd_velocity5"))
    def crosses(k, level, downward=False):
        now, before = numeric(row,k), numeric(prev,k)
        if now is None:
            return False
        return (now<=level and (before is None or before>level)) if downward else (now>=level and (before is None or before<level))
    repair = r5 is not None and rv is not None and r5>0 and rv>0 and dd is not None and dd<-.03
    rank_delta = numeric(row,"drank5")
    new_high = pg is not None and (numeric(prev,"peak_gain") is None or pg>numeric(prev,"peak_gain"))
    fade = (ret is not None and ret<0 and dd is not None and dd<=-.05 and msh is not None
            and msh>=15 and r5 is not None and r5<0 and row["t"]-row["fill_et"]>=10)
    kinds = {
        "entry": not memory["seen"],
        "profit5": not memory["profit5"] and ret is not None and ret>=.05,
        "push": crosses("ret5",.03), "damage": crosses("dd_from_high",-.05,True),
        "flush": crosses("dd_from_high",-.15,True), "repair": repair and not memory["repair"],
        "failed_repair": memory["repair"] and r5 is not None and r5<0,
        "rank_loss": rank_delta is not None and rank_delta>=3 and (numeric(prev,"drank5") is None or numeric(prev,"drank5")<3),
        "rank_recovery": rank_delta is not None and rank_delta<=-3 and (numeric(prev,"drank5") is None or numeric(prev,"drank5")>-3),
        "new_high": new_high,
        "giveback": pg is not None and pg>=.05 and crosses("dd_from_high",-.05,True),
        "stall": crosses("minutes_since_high",10),
        "reclaim": bool(row.get("reclaim_fill_now")),
        "resurrection": memory["damaged_below"] and crosses("ret_fill",0),
        "fade_ruler": fade and not memory["fade"],
        "heartbeat": row["t"]-memory["last_event"]>=15,
        "terminal": row["t"]==row["session_end"],
    }
    memory["seen"] = True
    memory["profit5"] |= kinds["profit5"]
    memory["damaged_below"] |= ret is not None and ret<=-.10
    memory["repair"], memory["fade"] = repair, fade
    return kinds


def build_events(panel):
    output = []
    feat_cols = [c for c in panel.columns if c.startswith("feature_")]
    for _, claim in panel.partition_by(KEYS, as_dict=True, maintain_order=True).items():
        rows = claim.sort("t").to_dicts()
        times=[row["t"] for row in rows]
        # Grid completeness matters: histories are clock minutes, not printed bars.
        if any(b["t"]-a["t"]!=1 for a,b in zip(rows,rows[1:])):
            raise AssertionError("noncontiguous minute grid")
        if rows[0]["status"]!="filled":
            continue
        events, prev = [], {}
        memory = {"seen":False,"profit5":False,"damaged_below":False,"repair":False,
                  "fade":False,"last_event":-10**9,"count":{k:0 for k in KINDS}}
        for row in rows:
            if row["t"]<=row["fill_et"]:
                prev=row
                continue
            kinds=flags_for(row,prev,memory)
            for k,value in kinds.items():
                if value and k not in ("heartbeat","terminal"):
                    memory["count"][k]+=1
            chosen = any(kinds.values())
            # Thinning controls repeated observations, not trade duration.
            important=any(kinds[k] for k in ("entry","profit5","flush","reclaim","resurrection","terminal","fade_ruler"))
            if chosen and (important or row["t"]-memory["last_event"]>=5):
                event={k:row[k] for k in KEYS}
                event.update({"t":int(row["t"]),"event_kind":"|".join(k for k,v in kinds.items() if v),
                              "sequence":len(events),"session_end":int(row["session_end"]),
                              "fill_et":int(row["fill_et"]),"fill_px":float(row["fill_px"]),"status":row["status"],
                              "sell_px":numeric(row,"sell_px"),"sell_et":int(row["sell_et"]) if finite(row.get("sell_et")) else None,
                              "sell_volume":numeric(row,"sell_volume")})
                for c in feat_cols:
                    event[c]=numeric(row,c)
                for k in KINDS:
                    event[f"feature_event_{k}"]=float(kinds[k])
                    event[f"feature_hist_count_{k}"]=float(memory["count"][k])
                event["next_sequence"]=event["next_sell_px"]=event["next_sell_et"]=None
                for h in (15,30,60,120):
                    event[f"V{h}"]=event[f"label_{h}_et"]=event[f"increment_{h}_original_dollar"]=None
                    j=bisect_left(times,row["t"]+h)
                    if j<len(rows):
                        future=rows[j]
                        et=future.get("sell_et")
                        if (finite(event["sell_px"]) and event["sell_px"]>0 and event["sell_et"] is not None
                            and et is not None and et>event["sell_et"] and et<=row["session_end"]
                            and event["sell_et"]<=row["t"]+h and finite(future.get("sell_px"))):
                            value=future["sell_px"]/event["sell_px"]-1
                            event[f"V{h}"]=value
                            event[f"label_{h}_et"]=int(et)
                            event[f"increment_{h}_original_dollar"]=event["sell_px"]/row["fill_px"]*value*.995/1.005
                events.append(event)
                memory["last_event"]=row["t"]
            prev=row
        for i,event in enumerate(events):
            et=event["sell_et"]
            if et is None or et>event["session_end"] or not finite(event["sell_px"]):
                continue
            for nxt in events[i+1:]:
                if (nxt["t"]>=et and nxt["sell_et"] is not None and et<nxt["sell_et"]<=event["session_end"]
                    and finite(nxt["sell_px"]) and nxt["sell_px"]>0):
                    event["next_sequence"],event["next_sell_px"],event["next_sell_et"]=nxt["sequence"],nxt["sell_px"],nxt["sell_et"]
                    break
        output.extend(events)
    return pl.DataFrame(output,infer_schema_length=None)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data-root",type=Path,required=True)
    ap.add_argument("--out",type=Path,required=True)
    ap.add_argument("--limit",type=int)
    a=ap.parse_args()
    days=ls.discovery_days(a.data_root)
    if a.limit:
        days=days[:a.limit]
    ls.assert_coverage(days,a.data_root)
    pin=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    for sub in ("events","_done"):
        (a.out/sub).mkdir(parents=True,exist_ok=True)
    counts,features={},None
    for i,day in enumerate(days):
        marker=a.out/"_done"/f"{day}.json"
        path=a.out/"events"/f"{day}.parquet"
        sources=[ls.v2_dir(a.data_root)/kind/f"{day}.parquet" for kind in ("panel","roster","tape")]
        sources.append(a.data_root/"sip/candidates"/f"{day}.json")
        input_hash=hashlib.sha256(b"".join(hashlib.sha256(p.read_bytes()).digest() for p in sources)).hexdigest()
        if marker.exists() and path.exists():
            info=json.loads(marker.read_text())
            if info.get("source_sha256")==pin and info.get("input_hash")==input_hash:
                counts[day]=info["events"]
                continue
        events=build_events(load_day(day,a.data_root))
        if events.height:
            features=[c for c in events.columns if c.startswith("feature_")]
        tmp=path.with_suffix(".tmp.parquet")
        events.write_parquet(tmp)
        tmp.replace(path)
        marker.write_text(json.dumps({"status":"ok" if events.height else "empty","source_sha256":pin,
                                      "input_hash":input_hash,"events":events.height})+"\n")
        counts[day]=events.height
        if i%25==0:
            print(f"{i+1}/{len(days)} {day} events={events.height}",flush=True)
    if features is None:
        nonempty=next(d for d in days if counts[d]>0)
        features=[c for c in pl.read_parquet_schema(a.out/"events"/f"{nonempty}.parquet") if c.startswith("feature_")]
    manifest={"source_sha256":pin,"discovery_days":days,"events_by_day":counts,"feature_columns":features,
              "clocks":CLOCKS,"event_kinds":KINDS,"thinning":"5 clock minutes except entry/profit5/flush/reclaim/resurrection/fade/terminal; heartbeat15",
              "labels":"strictly later executable opens within session; inherited-share return; UNKNOWN retained",
              "tape":"causal A_pm/B acquisition admission only; no full-day winners/later snapshots; unavailable/zero-without-print evidence UNKNOWN; no day quality predictor",
              "peer_context":"original scanner roster at N3 and N5, not changing policy holdings",
              "increment_dollars":"per original unit claim budget at modeled50bps per side; entry sunk, common exit fee",
              "split_sha256":hashlib.sha256((ls.v2_dir(a.data_root)/"split.json").read_bytes()).hexdigest()}
    (a.out/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    print("Events complete",sum(counts.values()),"days",len(days),"features",len(features))


if __name__=="__main__":
    main()
