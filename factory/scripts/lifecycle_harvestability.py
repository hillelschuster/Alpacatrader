#!/usr/bin/env python3
"""Discovery-only first-push economics; no winner prediction or high-touch fills.

Each original top-5 claim owns fixed shares. A completed-close profit signal submits
one partial sale at the actual next executable open; remaining shares use the existing
fade ruler or a window liquidation. No rebalancing, recycling, hindsight route inputs,
or absolute-capital minimum trade size. Blocked slots are cash; missing sales UNKNOWN.
Outputs are resumable per day, and include causal first-push states for trajectory work.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
from pathlib import Path

os.environ.setdefault("POLARS_MAX_THREADS", "2")
import polars as pl
import lifecycle_study as ls

CLOCKS = (540, 560, 569, 571)
ENDS = (690, 780, 900)
THRESHOLDS = (0.05, 0.15, 0.30)
FRACTIONS = (0.25, 0.50, 0.75)
SIDES = (0.005, 0.0075)
FEATURES = ("ret_fill", "peak_gain", "dd_from_high", "mae_sofar", "minutes_since_high",
            "ret5", "ret15", "nh5", "nh15", "recovery_from_low", "vol_med_ratio",
            "rel_dvol5", "sib_ret_mean", "race_rank", "drank5", "race_gain", "px_age")


def finite(v):
    return v is not None and math.isfinite(float(v))


def fade(row, fill_et):
    fields = ("ret_fill", "minutes_since_high", "dd_from_high", "ret5")
    return (row["t"] - fill_et >= 10 and all(finite(row.get(k)) for k in fields)
            and row["ret_fill"] < 0 and row["minutes_since_high"] >= 15
            and row["dd_from_high"] <= -0.05 and row["ret5"] < 0)


def sale(row, fraction, reason, session_end):
    px, et = row.get("sell_px"), row.get("sell_et")
    if not finite(px) or px <= 0 or et is None or et > session_end:
        return None
    if et < row["t"]:
        raise AssertionError("sale precedes completed-bar decision")
    return {"decision_et": row["t"], "exec_et": int(et), "px": float(px),
            "fraction": fraction, "reason": reason}


def simulate(rows, roster, end, threshold=None, fraction=0.0, release=True):
    """Return fixed-original-share sales. Never inspect future labels to choose actions."""
    se = min(end, int(roster["session_end"]))
    if roster["status"] == "blocked" or (roster["status"] == "filled" and roster["fill_et"] > se):
        return [], False
    if roster["status"] != "filled" or not finite(roster.get("fill_px")):
        return [], True
    fill_et = int(roster["fill_et"])
    remaining, pending_until, banked = 1.0, fill_et, False
    sales = []
    for row in rows:
        t = int(row["t"])
        if t <= fill_et or t < pending_until or t > se or remaining <= 1e-12:
            continue
        reason, qty = None, 0.0
        if t == se:
            reason, qty = "window", remaining
        elif release and fade(row, fill_et):
            reason, qty = "fade", remaining
        elif threshold is not None and not banked and finite(row.get("ret_fill")) and row["ret_fill"] >= threshold:
            reason, qty = "bank", min(remaining, fraction)
        if reason is None:
            continue
        order = sale(row, qty, reason, int(roster["session_end"]))
        if order is None:
            continue
        if reason != "window" and order["exec_et"] > se:
            continue  # leave claim owned; no fabricated deadline sale
        sales.append(order)
        remaining -= qty
        pending_until = order["exec_et"]
        banked = banked or reason == "bank"
    return sales, remaining > 1e-12


def member_return(roster, sales, unknown, side):
    if unknown:
        return None
    if not sales:
        return 0.0
    if abs(sum(s["fraction"] for s in sales) - 1) > 1e-10:
        raise AssertionError("share conservation")
    return sum(s["fraction"] * s["px"] / roster["fill_px"] for s in sales) * (1-side)/(1+side) - 1


def configs():
    yield "hold", None, 0.0, False
    yield "fade", None, 0.0, True
    for threshold, fraction in itertools.product(THRESHOLDS, FRACTIONS):
        yield f"bank{int(threshold*100)}_f{int(fraction*100)}_fade", threshold, fraction, True


def process_day(panel, roster):
    events, members, executions = [], [], []
    groups = {key: g.sort("t").to_dicts() for key, g in panel.partition_by(
        ["clock", "rank", "ticker"], as_dict=True).items()}
    for ros in roster.iter_rows(named=True):
        key = (ros["clock"], ros["rank"], ros["ticker"])
        rows = groups.get(key, [])
        ident = {k: ros[k] for k in ("day", "clock", "rank", "ticker")}
        # Retrospective event outcomes are written separately, never passed to simulate.
        if ros["status"] == "filled":
            for end, threshold in itertools.product(ENDS, THRESHOLDS):
                eligible = [r for r in rows if ros["fill_et"] < r["t"] < min(end, ros["session_end"])
                            and finite(r.get("ret_fill")) and r["ret_fill"] >= threshold]
                if not eligible:
                    continue
                row = eligible[0]
                order = sale(row, 1.0, "first_push", ros["session_end"])
                event = {**ident, "end": end, "threshold": threshold, "t": row["t"],
                         "sale_et": order["exec_et"] if order else None,
                         "sale_ret_gross": order["px"]/ros["fill_px"]-1 if order else None}
                event.update({k: row.get(k) for k in FEATURES})
                event.update({f"continuation_{h}": row.get(f"V{h}") for h in (5,15,30,60,120)})
                events.append(event)
        for end in ENDS:
            for label, threshold, fraction, release in configs():
                sales, unknown = simulate(rows, ros, end, threshold, fraction, release)
                base = {**ident, "end": end, "policy": label, "status": ros["status"],
                        "unknown": unknown, "banked": any(s["reason"] == "bank" for s in sales),
                        "mfe_day": ros.get("mfe_day")}
                for side in SIDES:
                    members.append({**base, "side": side,
                                    "ret": member_return(ros, sales, unknown, side)})
                executions.extend({**ident, "end": end, "policy": label, **s} for s in sales)
    return events, members, executions


def report(out, days, run_id):
    frames = [pl.read_parquet(out/"days"/f"{d}.members.parquet") for d in days]
    m = pl.concat(frames, how="diagonal_relaxed")
    # Same original slot denominator even when a fill is blocked. Censored day != zero.
    daily = []
    for n in (3,5):
        g = (m.filter(pl.col("rank") <= n).group_by(["day","clock","end","policy","side"])
             .agg(pl.col("ret").sum().alias("pnl_sum"), pl.col("unknown").any().alias("unknown"),
                  pl.len().alias("slots"), pl.col("banked").sum().alias("banked")))
        g = g.with_columns(pl.lit(n).alias("n"),
                           pl.when(pl.col("unknown")).then(None).otherwise(pl.col("pnl_sum")/n).alias("ret"))
        daily.append(g)
    d = pl.concat(daily)
    d.write_parquet(out/"daily.parquet")
    m.write_parquet(out/"members.parquet")
    ev = pl.concat([pl.read_parquet(out/"days"/f"{day}.events.parquet") for day in days],how="diagonal_relaxed")
    ev.write_parquet(out/"events.parquet")
    summary = d.group_by(["clock","end","policy","side","n"]).agg(
        pl.len().alias("days"),pl.col("ret").count().alias("known_days"),pl.col("unknown").sum().alias("unknown_days"),
        pl.col("ret").mean().alias("ev"),pl.col("ret").median().alias("median"),
        (pl.col("ret").std()/pl.col("ret").count().sqrt()).alias("day_se"))
    summary.sort(["side","end","n","clock","ev"]).write_parquet(out/"summary.parquet")
    # Paired policy deltas only on days both cashflows resolve.
    ref = d.filter(pl.col("policy")=="fade").select(["day","clock","end","side","n",pl.col("ret").alias("fade_ret")])
    paired = d.join(ref,on=["day","clock","end","side","n"]).filter(pl.col("ret").is_not_null() & pl.col("fade_ret").is_not_null())
    paired.with_columns((pl.col("ret")-pl.col("fade_ret")).alias("delta_fade")).write_parquet(out/"paired.parquet")
    metadata = {"run_id":run_id,"discovery_days":days,"protected_half_read":False,
                "clocks":CLOCKS,"ends_rulers":ENDS,"thresholds_rulers":THRESHOLDS,"fractions_rulers":FRACTIONS,
                "side_costs":SIDES,"policy_count":11,"interpretation":"discovery diagnostic, not frozen policy",
                "source_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (out/"manifest.json").write_text(json.dumps(metadata,indent=2)+"\n")
    print(summary.filter((pl.col("side")==.005)&(pl.col("end")==780)&(pl.col("n")==5)).sort(["clock","ev"],descending=[False,True]))


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data-root",type=Path,required=True)
    ap.add_argument("--out",type=Path,required=True)
    ap.add_argument("--limit",type=int)
    a=ap.parse_args()
    days=ls.discovery_days(a.data_root)
    if a.limit:
        days=days[:a.limit]
    ls.assert_coverage(days,a.data_root,"roster")
    ls.assert_coverage(days,a.data_root,"panel")
    run_id=hashlib.sha256((Path(__file__).read_text()+json.dumps(days)).encode()).hexdigest()
    (a.out/"days").mkdir(parents=True,exist_ok=True)
    for index,day in enumerate(days):
        marker=a.out/"days"/f"{day}.json"
        if marker.exists() and json.loads(marker.read_text()).get("run_id")==run_id:
            continue
        panel=ls.load_panel([day],a.data_root,with_tape=False)
        roster=ls.load_roster([day],a.data_root)
        e,m,f=process_day(panel,roster)
        for label,rows in (("events",e),("members",m),("fills",f)):
            frame=pl.DataFrame(rows,infer_schema_length=None)
            path=a.out/"days"/f"{day}.{label}.parquet"
            frame.write_parquet(path.with_suffix(".tmp.parquet"))
            path.with_suffix(".tmp.parquet").replace(path)
        marker.write_text(json.dumps({"run_id":run_id,"day":day,"members":len(m),"events":len(e)})+"\n")
        if index%25==0:
            print(f"{index+1}/{len(days)} {day}",flush=True)
    report(a.out,days,run_id)


if __name__ == "__main__":
    main()
