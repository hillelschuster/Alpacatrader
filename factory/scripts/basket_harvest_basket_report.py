#!/usr/bin/env python3
"""HARVEST01 — basket-level handling readout (release -> cash / survivors / leader).

Aggregates harvest01/basket/*.parquet: for each (policy, clock, N, endpoint) the mean
basket-day return and its delta vs base_hold in the same cell, block splits, and the
share of days improved. The baseline row `base_hold` is verified equal to the unmanaged
sim cells in the same run (see manifest notes).

Usage: .venv/bin/python factory/scripts/basket_harvest_basket_report.py [--data-root ...]
Outputs: harvest01/report/basket.md + basket_summary.parquet
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402


def block_of(day: str) -> str:
    if "2021-02-01" <= day <= "2023-12-31":
        return "B1"
    if "2025-02-01" <= day <= "2025-12-31":
        return "B2"
    return "other"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    data_root = bps.resolve_data_root(args.data_root)
    rep = data_root / "harvest01" / "report"
    rep.mkdir(parents=True, exist_ok=True)

    b = pl.scan_parquet(str(data_root / "harvest01" / "basket" / "*.parquet"),
                        glob=True).collect()
    if b.height == 0:
        print("no basket rows")
        return 1
    b = b.with_columns(pl.col("day").map_elements(block_of, return_dtype=pl.Utf8).alias("block"))
    base = (b.filter(pl.col("policy") == "base_hold")
            .select(["day", "clock", "N", "end", "block", pl.col("ret").alias("base_ret")]))
    j = b.join(base, on=["day", "clock", "N", "end", "block"], how="inner")
    j = j.with_columns((pl.col("ret") - pl.col("base_ret")).alias("delta"))
    j = j.filter(pl.col("delta").is_not_null())

    g = j.group_by(["policy", "clock", "N", "end"]).agg([
        pl.len().alias("n"),
        pl.col("ret").mean().alias("m_ret"),
        pl.col("base_ret").mean().alias("m_base"),
        pl.col("delta").mean().alias("m_delta"),
        pl.col("delta").std().alias("sd"),
        (pl.col("delta") > 0).mean().alias("pos_share"),
    ]).with_columns((pl.col("m_delta") / (pl.col("sd") / pl.col("n").sqrt())).alias("t"))
    blk = (j.group_by(["policy", "clock", "N", "end", "block"])
            .agg(pl.col("delta").mean().alias("md")).pivot(
        values="md", index=["policy", "clock", "N", "end"], on="block"))
    g = g.join(blk, on=["policy", "clock", "N", "end"], how="left")
    g.write_parquet(rep / "basket_summary.parquet")

    lines = ["# HARVEST01 — basket handling: where the released money goes (dev only)\n"]
    lines.append("delta = policy basket-day return minus base_hold in the same cell (pp); "
                 "all policies release on the member's own causal trigger and act at bar opens.\n")
    for E in (660, 720, None):
        sel = g.filter(pl.col("N") == 3)
        if E is None:
            sel = sel.sort("m_delta", descending=True)
            hdr = "all endpoints"
        else:
            sel = sel.filter(pl.col("end") == E).sort("m_delta", descending=True)
            hdr = f"end={E}"
        lines.append(f"\n## N=3, {hdr} — top policies")
        lines.append("| clock | policy | end | mean Δ pp | t | pos | B1 Δ | B2 Δ | n |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for r in sel.head(14).iter_rows(named=True):
            def f(x):
                return "" if x is None else f"{x*100:.2f}"
            lines.append(f"| {r['clock']} | {r['policy']} | {r['end']} | {r['m_delta']*100:+.2f} | "
                         f"{r['t']:.1f} | {r['pos_share']:.2f} | {f(r.get('B1'))} | {f(r.get('B2'))} | {r['n']} |")
    lines.append("\n## Policy mean across all clocks (N=3, end=720)")
    piv = (g.filter((pl.col("N") == 3) & (pl.col("end") == 720))
           .group_by("policy").agg(pl.col("m_delta").mean().alias("avg")).sort("avg", descending=True))
    for r in piv.iter_rows(named=True):
        lines.append(f"- {r['policy']}: {r['avg']*100:+.2f} pp")
    (rep / "basket.md").write_text("\n".join(lines) + "\n")
    print(f"basket_summary rows={g.height} -> {rep/'basket.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
