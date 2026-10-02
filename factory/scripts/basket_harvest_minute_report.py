#!/usr/bin/env python3
"""HARVEST01 — discovery readout: state-conditioned continuation value V_h vs cash.

Reads harvest01/minute/*.parquet (per member-minute causal state + executable V_h
labels) and produces, for each declared state coordinate and bin:
  * occupancy view  : mean/median V_h over member-minutes
  * member-balanced : mean over member-days of the member's mean V_h in the bin
  * basket-day view : mean over basket-days of the basket's mean V_h in the bin
  * block split B1/B2 on the occupancy mean
  * tail ledger     : P(fwd MFE120 >= 30/50/100%) and mean V120 given >=100%
All three weightings are published side by side (the CV01 wedge made explicit).
Descriptive only: one coordinate at a time, declared bins, no fitting, no joint grids.

Usage:
    .venv/bin/python factory/scripts/basket_harvest_minute_report.py [--limit N]
Outputs: harvest01/report/minute_summary.parquet, minute.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402

HORIZONS = (5, 15, 30, 60, 120)
COORDS = ("dist_high", "ret_fill", "bars_since_high", "nh15", "vol_accel", "rank_known",
          "drank5", "n_fresh2", "sib_above_fill", "basket_ret", "tenure")


def bin_exprs() -> list:
    return [
        pl.when(pl.col("dist_high") <= -0.20).then(pl.lit("1_le-20%"))
          .when(pl.col("dist_high") <= -0.10).then(pl.lit("2_-20..-10%"))
          .when(pl.col("dist_high") <= -0.05).then(pl.lit("3_-10..-5%"))
          .when(pl.col("dist_high") <= -0.02).then(pl.lit("4_-5..-2%"))
          .when(pl.col("dist_high") <= 0.02).then(pl.lit("5_-2..2%"))
          .otherwise(pl.lit("6_gt2%")).alias("dist_high"),
        pl.when(pl.col("ret_fill") <= 0).then(pl.lit("1_le0"))
          .when(pl.col("ret_fill") <= 0.10).then(pl.lit("2_0..10%"))
          .when(pl.col("ret_fill") <= 0.30).then(pl.lit("3_10..30%"))
          .when(pl.col("ret_fill") <= 1.00).then(pl.lit("4_30..100%"))
          .otherwise(pl.lit("5_gt100%")).alias("ret_fill"),
        pl.when(pl.col("bars_since_high") <= 5).then(pl.lit("1_0-5"))
          .when(pl.col("bars_since_high") <= 15).then(pl.lit("2_5-15"))
          .when(pl.col("bars_since_high") <= 30).then(pl.lit("3_15-30"))
          .when(pl.col("bars_since_high") <= 60).then(pl.lit("4_30-60"))
          .otherwise(pl.lit("5_60+")).alias("bars_since_high"),
        pl.when(pl.col("nh15") <= 0).then(pl.lit("1_0"))
          .when(pl.col("nh15") == 1).then(pl.lit("2_1"))
          .when(pl.col("nh15") <= 3).then(pl.lit("3_2-3"))
          .otherwise(pl.lit("4_4+")).alias("nh15"),
        pl.when(pl.col("vol_accel").is_null()).then(pl.lit("9_null"))
          .when(pl.col("vol_accel") <= -0.5).then(pl.lit("1_le-0.5"))
          .when(pl.col("vol_accel") <= 0).then(pl.lit("2_-0.5..0"))
          .when(pl.col("vol_accel") <= 1).then(pl.lit("3_0..1"))
          .otherwise(pl.lit("4_gt1")).alias("vol_accel"),
        pl.when(pl.col("rank_known").is_null()).then(pl.lit("9_null"))
          .when(pl.col("rank_known") <= 3).then(pl.lit("1_1-3"))
          .when(pl.col("rank_known") <= 10).then(pl.lit("2_4-10"))
          .when(pl.col("rank_known") <= 50).then(pl.lit("3_11-50"))
          .when(pl.col("rank_known") <= 100).then(pl.lit("4_51-100"))
          .otherwise(pl.lit("5_101+")).alias("rank_known"),
        pl.when(pl.col("drank5").is_null()).then(pl.lit("9_null"))
          .when(pl.col("drank5") <= -10).then(pl.lit("1_improved10+"))
          .when(pl.col("drank5") <= -1).then(pl.lit("2_improved1-9"))
          .when(pl.col("drank5") == 0).then(pl.lit("3_same"))
          .when(pl.col("drank5") <= 9).then(pl.lit("4_worse1-9"))
          .otherwise(pl.lit("5_worse10+")).alias("drank5"),
        pl.when(pl.col("n_fresh2").is_null()).then(pl.lit("9_null"))
          .when(pl.col("n_fresh2") < 50).then(pl.lit("1_lt50"))
          .when(pl.col("n_fresh2") < 150).then(pl.lit("2_50-150"))
          .when(pl.col("n_fresh2") < 400).then(pl.lit("3_150-400"))
          .otherwise(pl.lit("4_400+")).alias("n_fresh2"),
        pl.when(pl.col("sib_above_fill").is_null()).then(pl.lit("9_null"))
          .otherwise((pl.lit("n") + pl.col("sib_above_fill").cast(pl.Utf8))).alias("sib_above_fill"),
        pl.when(pl.col("basket_ret").is_null()).then(pl.lit("9_null"))
          .when(pl.col("basket_ret") <= -0.05).then(pl.lit("1_le-5%"))
          .when(pl.col("basket_ret") <= 0).then(pl.lit("2_-5..0"))
          .when(pl.col("basket_ret") <= 0.05).then(pl.lit("3_0..5%"))
          .otherwise(pl.lit("4_gt5%")).alias("basket_ret"),
        pl.when(pl.col("tenure") <= 5).then(pl.lit("1_0-5"))
          .when(pl.col("tenure") <= 15).then(pl.lit("2_5-15"))
          .when(pl.col("tenure") <= 30).then(pl.lit("3_15-30"))
          .when(pl.col("tenure") <= 60).then(pl.lit("4_30-60"))
          .when(pl.col("tenure") <= 120).then(pl.lit("5_60-120"))
          .otherwise(pl.lit("6_120+")).alias("tenure"),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    data_root = bps.resolve_data_root(args.data_root)
    rep = data_root / "harvest01" / "report"
    rep.mkdir(parents=True, exist_ok=True)
    files = sorted((data_root / "harvest01" / "minute").glob("*.parquet"))
    if args.limit:
        files = files[:args.limit]
    if not files:
        print("no minute files")
        return 1

    occ_parts, mem_parts, bd_parts = [], [], []
    for fp in files:
        d = (pl.read_parquet(fp, columns=["day", "variant", "clock", "rank", "ticker", "t",
                                          "tenure", "dist_high", "ret_fill", "bars_since_high",
                                          "nh15", "vol_accel", "rank_known", "drank5", "n_fresh2",
                                          "sib_above_fill", "basket_ret"]
                               + [f"v{h}" for h in HORIZONS]
                               + ["fmfe120"]))
        if d.height == 0:
            continue
        d = d.with_columns(bin_exprs()).with_columns(
            pl.when(pl.col("day") <= "2023-12-31").then(pl.lit("B1"))
              .when(pl.col("day") >= "2025-02-01").then(pl.lit("B2"))
              .otherwise(pl.lit("X")).alias("block"))
        for coord in COORDS:
            long = d.select(["day", "variant", "clock", "rank", "ticker", coord, "block",
                             "fmfe120"] + [f"v{h}" for h in HORIZONS]).unpivot(
                index=["day", "variant", "clock", "rank", "ticker", coord, "block", "fmfe120"],
                on=[f"v{h}" for h in HORIZONS], variable_name="vh", value_name="v")
            long = long.with_columns(pl.col("vh").str.replace("v", "").cast(pl.Int32).alias("h"))
            long = long.with_columns(pl.col(coord).alias("bin"))
            occ = long.group_by(["bin", "h"]).agg([
                pl.col("v").sum().alias("sum_v"), pl.col("v").count().alias("n"),
                pl.col("v").median().alias("med"),
                (pl.col("fmfe120") >= 0.30).mean().alias("p_ge30"),
                (pl.col("fmfe120") >= 0.50).mean().alias("p_ge50"),
                (pl.col("fmfe120") >= 1.00).mean().alias("p_ge100"),
                pl.col("v").filter(pl.col("fmfe120") >= 1.0).mean().alias("v_given_ge100"),
            ]).with_columns(pl.lit(coord).alias("coord"))
            b1 = long.filter(pl.col("block") == "B1").group_by(["bin", "h"]).agg(
                pl.col("v").mean().alias("m_b1")).with_columns(pl.lit(coord).alias("coord"))
            b2 = long.filter(pl.col("block") == "B2").group_by(["bin", "h"]).agg(
                pl.col("v").mean().alias("m_b2")).with_columns(pl.lit(coord).alias("coord"))
            occ = occ.join(b1, on=["coord", "bin", "h"], how="left").join(
                b2, on=["coord", "bin", "h"], how="left")
            occ_parts.append(occ)
            # member-balanced: per member-day first
            mem = long.group_by(["day", "variant", "clock", "rank", "ticker", "bin", "h"]).agg(
                pl.col("v").mean().alias("mv")).group_by(["bin", "h"]).agg(
                pl.col("mv").sum().alias("sum_mv"), pl.len().alias("n_members")).with_columns(
                pl.lit(coord).alias("coord"))
            mem_parts.append(mem)
            # basket-day: per member-day then per basket-day
            bd = long.group_by(["day", "variant", "clock", "rank", "ticker", "bin", "h"]).agg(
                pl.col("v").mean().alias("mv")).group_by(["day", "variant", "clock", "bin", "h"]).agg(
                pl.col("mv").mean().alias("bv")).group_by(["bin", "h"]).agg(
                pl.col("bv").sum().alias("sum_bv"), pl.len().alias("n_bd")).with_columns(
                pl.lit(coord).alias("coord"))
            bd_parts.append(bd)

    occ = pl.concat(occ_parts).group_by(["coord", "bin", "h"]).agg([
        pl.col("sum_v").sum(), pl.col("n").sum(), pl.col("med").mean(),
        pl.col("p_ge30").mean(), pl.col("p_ge50").mean(), pl.col("p_ge100").mean(),
        pl.col("v_given_ge100").mean(), pl.col("m_b1").mean(), pl.col("m_b2").mean(),
    ]).with_columns((pl.col("sum_v") / pl.col("n")).alias("mean_occ"))
    mem = pl.concat(mem_parts).group_by(["coord", "bin", "h"]).agg([
        pl.col("sum_mv").sum(), pl.col("n_members").sum()]).with_columns(
        (pl.col("sum_mv") / pl.col("n_members")).alias("mean_member"))
    bd = pl.concat(bd_parts).group_by(["coord", "bin", "h"]).agg([
        pl.col("sum_bv").sum(), pl.col("n_bd").sum()]).with_columns(
        (pl.col("sum_bv") / pl.col("n_bd")).alias("mean_basketday"))
    out = occ.join(mem.select(["coord", "bin", "h", "mean_member", "n_members"]),
                   on=["coord", "bin", "h"], how="left").join(
        bd.select(["coord", "bin", "h", "mean_basketday", "n_bd"]), on=["coord", "bin", "h"],
        how="left")
    out.write_parquet(rep / "minute_summary.parquet")

    lines = ["# HARVEST01 — minute-grain state → continuation value (V_h vs cash, dev)\n"]
    lines.append("V_h = (open of first bar >= t+h) / (open of first bar >= t) − 1, per member-minute; "
                 "three weightings side by side; tail columns from fwd MFE120.\n")
    for coord in COORDS:
        for h in (30, 120):
            s = out.filter((pl.col("coord") == coord) & (pl.col("h") == h)).sort("bin")
            if s.height == 0:
                continue
            lines.append(f"\n## {coord} — V{h} (pp)")
            lines.append("| bin | n | occ | member | basketday | B1 | B2 | med | P(MFE≥30) | P(≥50) | P(≥100) | V120|≥100 |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
            for r in s.iter_rows(named=True):
                def p(x):
                    return "" if x is None else f"{x*100:.2f}"

                def q2(x):
                    return "" if x is None else f"{x:.2f}"
                lines.append(f"| {r['bin']} | {r['n']} | {p(r['mean_occ'])} | {p(r['mean_member'])} | "
                             f"{p(r['mean_basketday'])} | {p(r['m_b1'])} | {p(r['m_b2'])} | {p(r['med'])} | "
                             f"{q2(r['p_ge30'])} | {q2(r['p_ge50'])} | {q2(r['p_ge100'])} | {p(r['v_given_ge100'])} |")
    (rep / "minute.md").write_text("\n".join(lines) + "\n")
    print(f"minute_summary rows={out.height} -> {rep/'minute.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
