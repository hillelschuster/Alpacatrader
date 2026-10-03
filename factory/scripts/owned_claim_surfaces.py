#!/usr/bin/env python3
"""Owned-share event anatomy in incremental dollars, not winner classification.

Transparent joint-state signs and event families are descriptive rulers only. First
visit per claim/family/episode bucket prevents repeated stalls from dominating. Means
are day-balanced; all price and horizon uncertainty remains null and counted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault("POLARS_MAX_THREADS","2")
import polars as pl
import lifecycle_study as ls

FAMILIES=("entry","damage","flush","repair","failed_repair","profit5","new_high",
          "giveback","rank_loss","rank_recovery","reclaim","resurrection","stall","fade_ruler")
KEYS=["day","clock","rank","ticker"]


def label_joint(frame):
    # Missing is a distinct measurement stratum, not a bearish signal.
    return frame.with_columns(
        pl.when(pl.col("feature_ret_fill").is_null()).then(pl.lit("unknown"))
        .when(pl.col("feature_ret_fill")>=0).then(pl.lit("above_entry"))
        .otherwise(pl.lit("below_entry")).alias("entry_level"),
        pl.when(pl.col("feature_ret5").is_null() | pl.col("feature_dd_velocity5").is_null()).then(pl.lit("unknown"))
        .when((pl.col("feature_ret5")>0)&(pl.col("feature_dd_velocity5")>0)).then(pl.lit("repairing"))
        .otherwise(pl.lit("not_repairing")).alias("repair_state"),
        pl.when(pl.col("feature_dvol5").is_null() | pl.col("feature_dvol15").is_null()).then(pl.lit("unknown"))
        .when(pl.col("feature_dvol5")>pl.col("feature_dvol15")/3).then(pl.lit("expanding_flow"))
        .otherwise(pl.lit("not_expanding")).alias("flow_state"),
        pl.when(pl.col("t")<570).then(pl.lit("PM"))
        .when(pl.col("t")<600).then(pl.lit("open_30m"))
        .when(pl.col("t")<660).then(pl.lit("10_11"))
        .when(pl.col("t")<780).then(pl.lit("11_13"))
        .otherwise(pl.lit("after13")).alias("tod"),
        pl.when(pl.col("feature_hist_count_damage")>0).then(pl.lit("prior_damage"))
        .otherwise(pl.lit("no_prior_damage")).alias("damage_history"))


def summarize(frame, group, outcome):
    eligible=frame.filter(pl.col(outcome).is_finite())
    # Mean first within day; dates, not row occupancy, are the replication unit.
    day=(eligible.group_by(group+["day"]).agg(pl.col(outcome).mean().alias("day_value"),pl.len().alias("events")))
    total=frame.group_by(group).agg(pl.len().alias("selected_events"))
    s=(day.group_by(group).agg(pl.len().alias("known_days"),pl.col("events").sum().alias("known_events"),
        pl.col("day_value").mean().alias("mean_increment"),pl.col("day_value").median().alias("median_day_increment"),
        (pl.col("day_value").std()/pl.len().sqrt()).alias("day_se"))
       .join(total,on=group,how="right"))
    return s.with_columns(pl.col("known_days").fill_null(0),pl.col("known_events").fill_null(0),
                          pl.lit(outcome).alias("outcome")).to_dicts()


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data-root",type=Path,required=True)
    ap.add_argument("--events-root",type=Path,required=True)
    ap.add_argument("--out",type=Path,required=True)
    a=ap.parse_args()
    days=ls.discovery_days(a.data_root)
    manifest=json.loads((a.events_root/"manifest.json").read_text())
    if manifest["discovery_days"]!=days:
        raise SystemExit("surfaces require exact discovery split")
    frames=[]
    cols=KEYS+["t","session_end","fill_px","sell_px","sell_et","event_kind",
                "feature_px","feature_ret_fill","feature_ret5","feature_dd_velocity5",
                "feature_dvol5","feature_dvol15","feature_hist_count_damage"]
    cols += [f"feature_event_{k}" for k in FAMILIES]+[f"feature_hist_count_{k}" for k in FAMILIES]
    cols += [f"increment_{h}_original_dollar" for h in (15,30,60,120)]+[f"V{h}" for h in (15,30,60,120)]
    cols=list(dict.fromkeys(cols))
    for day in days:
        frames.append(pl.read_parquet(a.events_root/"events"/f"{day}.parquet",columns=cols))
    events=label_joint(pl.concat(frames,how="diagonal_relaxed").sort(KEYS+["t"]))
    family_frames=[]
    for family in FAMILIES:
        sub=events.filter(pl.col(f"feature_event_{family}")>0).with_columns(
            pl.lit(family).alias("family"),
            pl.when(pl.col(f"feature_hist_count_{family}")<=1).then(pl.lit("first"))
            .otherwise(pl.lit("repeat")).alias("episode"))
        sub=sub.unique(subset=KEYS+["family","episode"],keep="first",maintain_order=True)
        family_frames.append(sub)
    anchors=pl.concat(family_frames,how="diagonal_relaxed")
    results=[]
    for n in (3,5):
        view=anchors.filter(pl.col("rank")<=n).with_columns(pl.lit(n).alias("n"))
        for h in (15,30,60,120):
            outcome=f"increment_{h}_original_dollar"
            for group in (["n","clock","family","episode","tod"],
                          ["n","clock","family","entry_level","repair_state","flow_state"],
                          ["n","clock","family","damage_history"]):
                results.extend(summarize(view,list(group),outcome))
    # Shadow sale is a ruler. Follow-up event inclusion uses already-executed sale
    # time and settled cash, not its eventual performance.
    release=(events.filter(pl.col("feature_event_fade_ruler")>0).unique(subset=KEYS,keep="first",maintain_order=True)
             .select(KEYS+[pl.col("sell_et").alias("release_et"),
                           (pl.col("sell_px")/pl.col("fill_px")*.995/1.005).alias("released_cash")]))
    # Use the first actual follow-up of each family AFTER release (the original
    # first family event can precede release, so select from the full event stream).
    extra=[]
    for family in ("repair","rank_recovery","new_high","reclaim","resurrection"):
        sub=(events.filter(pl.col(f"feature_event_{family}")>0).join(release,on=KEYS,how="inner")
             .filter((pl.col("t")>pl.col("release_et")) & pl.col("released_cash").is_finite())
             .unique(subset=KEYS,keep="first",maintain_order=True).with_columns(pl.lit(family).alias("family")))
        # The new order quantity is chosen from the past mark. Future sale/open
        # prices determine dollars and feasibility, never the quantity.
        q=.90*pl.col("released_cash")/(pl.col("feature_px")*1.005)
        cost=q*pl.col("sell_px")*1.005
        sub=sub.with_columns(*[pl.when((cost<=pl.col("released_cash")) & pl.col(f"V{h}").is_finite())
            .then(q*(pl.col("sell_px")*(1+pl.col(f"V{h}"))*.995-pl.col("sell_px")*1.005))
            .otherwise(None).alias(f"fresh_delta_{h}") for h in (15,30,60,120)])
        extra.append(sub)
    follow=pl.concat(extra,how="diagonal_relaxed")
    reentry=[]
    for n in (3,5):
        view=follow.filter(pl.col("rank")<=n).with_columns(pl.lit(n).alias("n"))
        for h in (15,30,60,120):
            reentry.extend(summarize(view,["n","clock","family","entry_level","flow_state"],f"fresh_delta_{h}"))
    artifact={"kind":"RETROSPECTIVE-CAUSAL-STATE-DOLLAR-ANATOMY-NOT-POLICY","days":days,
              "source_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "event_manifest_sha256":hashlib.sha256((a.events_root/"manifest.json").read_bytes()).hexdigest(),
              "states":results,"reentry":reentry,"event_rows":events.height,"first_repeat_anchors":anchors.height,
              "units":"incremental dollars per original claim cash budget, same fixed shares; common exit cost, original entry sunk",
              "sampling":"first family visit and first repeat per claim; day-balanced means; N3/N5 separately",
              "caveats":["Sign-defined joint states are rulers, never trade thresholds or discovered architecture.",
                         "UNKNOWN horizon/execution/gap outcomes remain null and counted, never 0.",
                         "First fade is a shadow release ruler, not a commitment to that policy.",
                         "Fresh reentry quantities fixed at causal marks with90% headroom; costs/gaps differ from owned retention.",
                         "No protected outcomes, model tuning or freeze; open-price proxies not quote-certified."]}
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(artifact,indent=2,allow_nan=False)+"\n")
    print("Dollar surfaces written",a.out,"events",events.height,"anchors",anchors.height,"surfaces",len(results),"reentry",len(reentry))


if __name__=="__main__":
    main()
