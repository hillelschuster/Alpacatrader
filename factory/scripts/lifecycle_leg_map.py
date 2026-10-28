#!/usr/bin/env python3
"""Behavior-to-dollar map: first visits to path states, future legs and failure risk.

States are diagnostic coordinates, not an adopted trading rule. Report raw full minute
paths elsewhere; first visits here avoid treating a long-lived state as many trades.
All probabilities use completed horizons only; tail touches are NOT realized proceeds.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import polars as pl
import numpy as np
import lifecycle_study as ls


def states(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        pl.when(pl.col("dd_from_high") >= -.02).then(pl.lit("near_high"))
        .when(pl.col("dd_from_high") >= -.10).then(pl.lit("pullback2_10"))
        .otherwise(pl.lit("damage10plus")).alias("damage"),
        pl.when(pl.col("minutes_since_high") <= 5).then(pl.lit("active_high"))
        .when(pl.col("minutes_since_high") <= 15).then(pl.lit("quiet5_15"))
        .otherwise(pl.lit("quiet15plus")).alias("cadence"),
        pl.when(pl.col("ret5") > 0).then(pl.lit("repairing"))
        .when(pl.col("ret5").is_finite()).then(pl.lit("fading"))
        .otherwise(pl.lit("unknown_slope")).alias("direction"),
        pl.when(pl.col("vol_med_ratio") > 1).then(pl.lit("expanding_flow"))
        .when(pl.col("vol_med_ratio").is_finite()).then(pl.lit("contracting_flow"))
        .otherwise(pl.lit("unknown_flow")).alias("flow"),
    )


def build(df: pl.DataFrame) -> pl.DataFrame:
    d = states(df.filter(pl.col("filled_asof") & (pl.col("rank") <= 3)))
    coords = ["clock", "damage", "cadence", "direction", "flow"]
    event = d.sort("t").unique(subset=["day", "ticker"]+coords, keep="first", maintain_order=True)
    rows = []
    for h in (30,60,120):
        complete = event.filter(pl.col(f"V{h}").is_finite() & pl.col(f"fmfe{h}").is_finite()
                                & pl.col(f"fmae{h}").is_finite())
        for key, s in complete.group_by(coords):
            days = s.group_by("day").agg(pl.col(f"V{h}").mean().alias("ev"))
            if days.height < 20:
                continue
            v, m, a = s[f"V{h}"], s[f"fmfe{h}"], s[f"fmae{h}"]
            dv = days["ev"].to_numpy()
            row = dict(zip(coords,key))
            row.update({"horizon":h,"events":s.height,"days":days.height,
                        "continuation_ev":float(dv.mean()),
                        "day_se":float(dv.std(ddof=1)/np.sqrt(len(dv))),
                        "cash_ev":0.0,"half_reduce_ev":float(dv.mean()/2),
                        "entry_reentry_ev_100bps":float(((1+v)*(.995/1.005)-1).mean()),
                        "entry_reentry_ev_150bps":float(((1+v)*(.9925/1.0075)-1).mean()),
                        "p_leg30":float((m>=.3).mean()),"p_leg50":float((m>=.5).mean()),
                        "p_leg100":float((m>=1.).mean()),"p_adverse10":float((a<=-.1).mean()),
                        "mean_max_leg":float(m.mean()),"mean_adverse":float(a.mean()),
                        "median_decision_et":float(s["t"].median())})
            if (m>=.3).sum():
                row["mean_leg_if30"] = float(m.filter(m>=.3).mean())
            else:
                row["mean_leg_if30"] = None
            rows.append(row)
    return pl.DataFrame(rows,infer_schema_length=None)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data-root",type=Path,default=Path("/home/hillel/projects/Alpacatrader/data"))
    ap.add_argument("--clock",type=int,required=True)
    ap.add_argument("--which",choices=("discovery","validation"),default="discovery")
    a=ap.parse_args()
    days=ls.discovery_days(a.data_root) if a.which=="discovery" else ls.validation_days(a.data_root,allow=True)
    d=ls.load_panel(days,a.data_root,clocks=(a.clock,))
    table=build(d)
    out=a.data_root/"harvest01/lifecycle/v2/report"
    out.mkdir(parents=True,exist_ok=True)
    table.write_parquet(out/f"leg_map_{a.which}_{a.clock}.parquet")
    evidence=table.to_dicts()
    (out/f"leg_map_{a.which}_{a.clock}.json").write_text(json.dumps(evidence,indent=2,allow_nan=False)+"\n")
    lines=[f"# Next-leg/recovery economic map: {a.which}, ET{a.clock}","",
           "First visit per day-name-state; top3. Complete forward horizons only. Diagnostic coordinates, not a frozen rule.",
           "continuation EV = value of NOT releasing for h minutes vs releasing now (entry cost sunk): a RULER, not a strategy.",
           "The thesis is a WINDOW of strength, not a session-long hold; fresh entry/re-entry pays a fresh roundtrip.","",
           "| state | h | independent days/events | continuation EV | day SE | fresh entry 100bps | P(+30/+50/+100) | P(-10) |",
           "|---|---|---|---|---|---|---|---|"]
    if table.height:
        for r in table.sort("continuation_ev",descending=True).head(40).iter_rows(named=True):
            state=" / ".join(r[c] for c in ("damage","cadence","direction","flow"))
            lines.append(f"| {state} | {r['horizon']} | {r['days']}/{r['events']} | {100*r['continuation_ev']:+.2f}% | "
                         f"{100*r['day_se']:.2f}% | {100*r['entry_reentry_ev_100bps']:+.2f}% | "
                         f"{100*r['p_leg30']:.1f}/{100*r['p_leg50']:.1f}/{100*r['p_leg100']:.1f}% | {100*r['p_adverse10']:.1f}% |")
    (out/f"leg_map_{a.which}_{a.clock}.md").write_text("\n".join(lines)+"\n")
    print(f"leg map states={table.height}, independent day-event support; no validation refit")


if __name__=="__main__":
    main()
