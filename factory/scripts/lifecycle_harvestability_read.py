#!/usr/bin/env python3
"""Read discovery first-push cashflows with fixed-slot and paired-day attribution."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import polars as pl
import lifecycle_study as ls


def records(df):
    return df.to_dicts()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--evidence", type=Path, required=True)
    a = ap.parse_args()
    manifest = json.loads((a.out / "manifest.json").read_text())
    days = manifest["discovery_days"]
    if days != ls.discovery_days(a.data_root):
        raise SystemExit("reader requires exactly the guarded discovery half")
    d = pl.read_parquet(a.out / "daily.parquet")
    m = pl.read_parquet(a.out / "members.parquet")
    e = pl.read_parquet(a.out / "events.parquet")
    paired = pl.read_parquet(a.out / "paired.parquet")
    if sorted(d["day"].unique().to_list()) != days:
        raise SystemExit("daily evidence does not cover split exactly")
    # Roster mfe_day includes post-session bars. Attribute only the observed window.
    window_frames = []
    for day in days:
        path = pl.read_parquet(ls.v2_dir(a.data_root)/"panel"/f"{day}.parquet",
                               columns=["day","clock","rank","ticker","t","peak_gain"])
        path = path.filter(pl.col("clock").is_in(manifest["clocks"]))
        for end in manifest["ends_rulers"]:
            window_frames.append(path.filter(pl.col("t") <= end)
                                 .group_by(["day","clock","rank","ticker"])
                                 .agg(pl.col("peak_gain").filter(pl.col("peak_gain").is_finite())
                                      .max().alias("window_mfe"))
                                 .with_columns(pl.lit(end).alias("end")))
    m = m.join(pl.concat(window_frames), on=["day","clock","rank","ticker","end"])
    # Broad MFE categories only for ex-post dollars attribution; never an action input.
    keys = ["clock", "end", "policy", "side"]
    complete = d.filter((pl.col("n") == 5) & pl.col("ret").is_not_null())
    support = complete.group_by(keys).agg(pl.len().alias("known_days"))
    known = (m.join(complete.select(["day"] + keys), on=["day"] + keys, how="semi")
             .with_columns(pl.when(pl.col("status") == "blocked").then(pl.lit("blocked_cash"))
                           .when(~pl.col("window_mfe").is_finite().fill_null(False)).then(pl.lit("unknown_mfe"))
                           .when(pl.col("window_mfe") >= 1).then(pl.lit("MFE100"))
                           .when(pl.col("window_mfe") >= .30).then(pl.lit("MFE30_100"))
                           .when(pl.col("window_mfe") < .10).then(pl.lit("MFElt10"))
                           .otherwise(pl.lit("MFE10_30")).alias("anatomy")))
    attribution = (known.group_by(keys + ["anatomy"])
                   .agg(pl.len().alias("members"), pl.col("ret").mean().alias("member_ev"),
                        pl.col("ret").sum().alias("return_sum"))
                   .join(support, on=keys)
                   .with_columns((pl.col("return_sum") / (5 * pl.col("known_days")))
                                 .alias("original_capital_pp")))
    # Event rates have the complete selected-slot denominator, including blocked/missing.
    opportunity = (e.group_by(["clock", "end", "threshold"])
                   .agg(pl.len().alias("signals"), pl.col("sale_ret_gross").count().alias("priced_signals"),
                        pl.col("day").n_unique().alias("signal_days"),
                        pl.col("sale_ret_gross").mean().alias("sale_gross_mean"),
                        pl.col("sale_ret_gross").median().alias("sale_gross_median"),
                        (pl.col("sale_ret_gross") < pl.col("threshold")).mean().alias("below_signal_level_share"),
                        (pl.col("sale_ret_gross") < 0).mean().alias("negative_sale_share"),
                        *[pl.col(f"continuation_{h}").filter(pl.col(f"continuation_{h}").is_finite())
                          .mean().alias(f"continuation_{h}_mean") for h in (5,15,30,60,120)],
                        *[pl.col(f"continuation_{h}").is_finite().sum()
                          .alias(f"continuation_{h}_known") for h in (5,15,30,60,120)])
                   .with_columns((pl.col("signals")/(5*len(days))).alias("selected_slot_signal_share")))
    fade_sales = pl.concat([
        pl.read_parquet(a.out/"days"/f"{day}.fills.parquet",
                        columns=["day","clock","rank","ticker","end","policy","reason","exec_et"])
        .filter((pl.col("policy") == "fade") & (pl.col("reason") == "fade"))
        for day in days], how="diagonal_relaxed")
    push_keys = ["day","clock","rank","ticker","end"]
    released = e.join(fade_sales.select(push_keys + ["exec_et"])
                      .rename({"exec_et":"fade_exec"}), on=push_keys, how="left")
    release_damage = (released.group_by(["clock","end","threshold"])
                      .agg(pl.len().alias("raw_signals"),
                           (pl.col("fade_exec") < pl.col("t")).fill_null(False)
                           .sum().alias("released_before_signal"))
                      .with_columns((pl.col("released_before_signal")/pl.col("raw_signals"))
                                    .alias("lost_signal_share")))
    ranks = (e.filter((pl.col("end")==780)&(pl.col("threshold")==.30))
             .group_by(["clock", "rank"]).agg(pl.len().alias("signals"),
                pl.col("sale_ret_gross").mean().alias("sale_gross_mean")))
    paired_summary = (paired.group_by(["clock","end","policy","side","n"])
                      .agg(pl.len().alias("paired_days"),pl.col("delta_fade").mean().alias("delta_fade"),
                           (pl.col("delta_fade").std()/pl.len().sqrt()).alias("paired_se")))
    monthly = (d.with_columns(pl.col("day").str.slice(0,7).alias("month"))
               .group_by(["clock","end","policy","side","n","month"])
               .agg(pl.col("ret").count().alias("known_days"),pl.col("ret").mean().alias("ev")))
    # Remove the five best policy days, never pretend this is a second validation sample.
    tail_removed=[]
    for key,g in d.group_by(["clock","end","policy","side","n"]):
        vals=g["ret"].drop_nulls().sort(descending=True).slice(5)
        tail_removed.append(dict(zip(["clock","end","policy","side","n"],key))|
                            {"ev_ex_best5":vals.mean()})
    sample = pl.concat([pl.read_parquet(ls.v2_dir(a.data_root)/"entry"/f"{day}.parquet")
                        for day in days[:20]], how="diagonal_relaxed")
    quote_sample = (sample.filter(pl.col("clock").is_in(manifest["clocks"]))
                    .group_by("clock").agg(pl.len().alias("members"),
                         pl.col("q_exec_bid").is_not_null().sum().alias("has_quote"),
                         pl.col("q_exec_age_ok").fill_null(False).sum().alias("fresh_quote")))
    artifact={"kind":"DISCOVERY-DIAGNOSTIC-NOT-ALPHA","source_manifest":manifest,
              "reader_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "split_sha256": hashlib.sha256((ls.v2_dir(a.data_root)/"split.json").read_bytes()).hexdigest(),
              "input_manifest": ls.load_manifest(a.data_root),
              "summary":records(pl.read_parquet(a.out/"summary.parquet")),
              "paired":records(paired_summary),"opportunity":records(opportunity),
              "rank30":records(ranks),"attribution":records(attribution),
              "release_damage": records(release_damage),
              "monthly":records(monthly),"ex_best5":tail_removed,
              "quote_coverage_first20_discovery_days": records(quote_sample),
              "caveats":["Next-open proxy plus 50/75bps per-side is not quote-certified execution.",
                         "Fixed endpoints are sensitivity rulers, not discovered optimal windows.",
                         "Conditional future returns are retrospective anatomy, not deployable rules.",
                         "UNKNOWN outcomes remain excluded and counted; paired comparisons use common known days.",
                         "No protected second-half outcomes read; discovery search is disclosed."]}
    a.evidence.parent.mkdir(parents=True,exist_ok=True)
    a.evidence.write_text(json.dumps(artifact,indent=2,allow_nan=False)+"\n")
    print("Evidence written",a.evidence,"days",len(days),"member cashflows",m.height,"first-push events",e.height)
    print(opportunity.filter(pl.col("end")==780).sort(["clock","threshold"]))


if __name__ == "__main__":
    main()
