#!/usr/bin/env python3
"""HARVEST01 — containment, capture funnel, and the joint tail of the original basket.

Answers, per (variant, clock, N), on dev days only:
  * did the day's eventual session-max leader(s) sit inside the clock's top-N list?
  * of all names whose session max gain crossed +30/50/100% vs prev close, what share
    was captured at the clock, and how much of the eventual move was still ahead of the
    decision price?
  * joint tail of the original N members: share of basket-days with k>=1/2/3 members
    touching +30/50/100% after their actual fill (touch lens, from the fills lane).

Inputs: harvest01/base/champs/*.parquet, .../selected/*.parquet, harvest01/sim/fills/*.parquet.
Outputs: harvest01/report/containment_summary.parquet, containment.md

Usage:
    .venv/bin/python factory/scripts/basket_harvest_containment.py [--data-root ...]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402

RULERS = [0.30, 0.50, 1.00]


def block_of(day: str) -> str:
    if "2021-02-01" <= day <= "2023-12-31":
        return "B1"
    if "2025-02-01" <= day <= "2026-05-29":
        return "B2"
    return "other"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    data_root = bps.resolve_data_root(args.data_root)
    base = data_root / "harvest01" / "base"
    rep = data_root / "harvest01" / "report"
    rep.mkdir(parents=True, exist_ok=True)

    ch_files = list((base / "champs").glob("*.parquet"))
    if not ch_files:
        print("no champs yet (selection pass still running?); nothing to do")
        return 0
    ch = pl.scan_parquet(str(base / "champs" / "*.parquet"), glob=True).collect()
    ch = ch.filter(pl.col("prev_close_adj") > 0).with_columns(
        (pl.col("px_max") / pl.col("prev_close_adj") - 1).alias("gain_max_adj"))
    # quality filter same spirit as selection primary
    chq = ch.filter((~pl.col("flag_discrepancy")) & (pl.col("px_max") >= 0.05)
                    & (pl.col("gain_max_adj") <= 10.0))
    sel = pl.scan_parquet(str(base / "selected" / "*.parquet"), glob=True).collect()
    fills = pl.scan_parquet(str(data_root / "harvest01" / "sim" / "fills" / "*.parquet"),
                            glob=True).collect()

    # day champions by session max gain (quality universe)
    champ = (chq.sort(["gain_max_adj", "ticker"], descending=[True, False])
             .group_by("day").head(3)
             .with_columns(pl.int_range(1, pl.len() + 1).over("day").alias("champ_rank")))

    rows = []
    for variant in sorted(sel["variant"].unique().to_list()):
        for clock in sorted(sel["clock"].unique().to_list()):
            sv = sel.filter((pl.col("variant") == variant) & (pl.col("clock") == clock))
            if sv.height == 0:
                continue
            top = {n: set(sv.filter(pl.col("rank") <= n)["ticker"].to_list())
                   for n in (1, 2, 3, 4)}
            days = sv["day"].unique().to_list()
            for N in (1, 2, 3, 4):
                mem = top[N]
                day_rows = champ.filter(pl.col("day").is_in(days))
                c1 = day_rows.filter(pl.col("champ_rank") == 1)
                c3 = day_rows.filter(pl.col("champ_rank") <= 3)
                n_days = len(days)
                in1 = c1.filter(pl.col("ticker").is_in(mem)).height
                in3 = c3.filter(pl.col("ticker").is_in(mem)).height
                # funnel per ruler
                funnel = {}
                for H in RULERS:
                    hit = chq.filter(pl.col("day").is_in(days) & (pl.col("gain_max_adj") >= H))
                    captured = hit.filter(pl.col("ticker").is_in(mem)).height
                    funnel[f"cross_H{int(H*100)}"] = hit.height
                    funnel[f"captured_H{int(H*100)}"] = captured
                    # of captured, remaining share of the move at the decision price
                    if captured:
                        cc = (hit.filter(pl.col("ticker").is_in(mem))
                              .join(sv.select(["day", "ticker", "decision_px"]),
                                    on=["day", "ticker"], how="inner"))
                        rem = ((cc["px_max"] - cc["decision_px"])
                               / (cc["px_max"] - cc["prev_close_adj"]))
                        funnel[f"remain_p50_H{int(H*100)}"] = float(rem.median())
                        funnel[f"remain_p90_H{int(H*100)}"] = float(rem.quantile(0.9))
                rows.append({"variant": variant, "clock": clock, "N": N, "n_days": n_days,
                             "champ1_in": in1, "champ1_share": in1 / max(1, n_days),
                             "champ3_in": in3, "champ3_share": in3 / max(3 * n_days, 1),
                             **funnel})

    cs = pl.DataFrame(rows)
    cs.write_parquet(rep / "containment_summary.parquet")

    # joint tail: share of basket-days with >=1/2/3 members touching +H after fill
    fj = []
    for variant in sorted(fills["variant"].unique().to_list()):
        for clock in sorted(fills["clock"].unique().to_list()):
            fv = fills.filter((pl.col("variant") == variant) & (pl.col("clock") == clock)
                              & (pl.col("status") == "filled"))
            if fv.height == 0:
                continue
            for N in (1, 2, 3, 4):
                sub = fv.filter(pl.col("rank") <= N)
                days = sorted(sub["day"].unique().to_list())
                if not days:
                    continue
                for H in RULERS:
                    hit = sub.with_columns((pl.col("mfe_adj") >= H).alias("hit"))
                    k = hit.group_by("day").agg(pl.col("hit").sum().alias("k"))
                    k = k.join(pl.DataFrame({"day": days}), on="day", how="right").with_columns(
                        pl.col("k").fill_null(0))
                    fj.append({"variant": variant, "clock": clock, "N": N, "H": int(H * 100),
                               "days": len(days),
                               "share_k_ge1": float((k["k"] >= 1).mean()),
                               "share_k_ge2": float((k["k"] >= 2).mean()),
                               "share_k_ge3": float((k["k"] >= 3).mean())})
    jt = pl.DataFrame(fj)
    jt.write_parquet(rep / "joint_tail.parquet")

    lines = ["# HARVEST01 — containment / funnel / joint tail (dev only)\n"]
    lines.append("## Champion containment — variant=primary, N=3 (champ1_share: day's "
                 "session-max leader inside the top-N list; champ3_share: pooled share of "
                 "the day's top-3 leaders captured)\n")
    lines.append("| clock | N | champ1_share | champ3_share | ±30 crossers | captured | ±50 | captured | ±100 | captured |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in cs.filter((pl.col("variant") == "primary") & (pl.col("N") == 3)).sort("clock").iter_rows(named=True):
        lines.append(f"| {r['clock']} | 3 | {r['champ1_share']:.3f} | {r['champ3_share']:.3f} | "
                     f"{r['cross_H30']} | {r['captured_H30']} | {r['cross_H50']} | {r['captured_H50']} | "
                     f"{r['cross_H100']} | {r['captured_H100']} |")
    lines.append("\n## Joint tail after actual fill — share of basket-days with >=1 member "
                 "touching +H post-fill (variant=primary)\n")
    lines.append("| clock | N | +30 k>=1 | +50 k>=1 | +100 k>=1 | days |")
    lines.append("|---|---|---|---|---|---|")
    for r in jt.filter((pl.col("variant") == "primary") & (pl.col("N") == 3)).sort("clock").iter_rows(named=True):
        lines.append(f"| {r['clock']} | 3 | {r['share_k_ge1']:.3f} | "
                     f"{(jt.filter((pl.col('variant')=='primary')&(pl.col('clock')==r['clock'])&(pl.col('N')==3)&(pl.col('H')==50))['share_k_ge1']).item():.3f} | "
                     f"{(jt.filter((pl.col('variant')=='primary')&(pl.col('clock')==r['clock'])&(pl.col('N')==3)&(pl.col('H')==100))['share_k_ge1']).item():.3f} | {r['days']} |")
    lines.append("\n_Remaining-move medians are in containment_summary.parquet "
                 "(remain_p50_Hxx columns)._\n")
    (rep / "containment.md").write_text("\n".join(lines) + "\n")
    print(f"containment rows={cs.height}, joint rows={jt.height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
