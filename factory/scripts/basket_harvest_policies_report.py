#!/usr/bin/env python3
"""HARVEST01 — aggregate the policy grid into basket-level readouts.

Member deltas (per-dollar vs holding the same member) are averaged over the N equal
slots of each basket-day; the basket-day is the unit of account. Reports per
(policy, clock, N, endpoint): mean delta, t, positive-day share, fire rate, and the
tail question (mean delta on members whose own post-fill MFE was >= 100%, plus how
often a fire preceded a later +30% excursion).

Usage: .venv/bin/python factory/scripts/basket_harvest_policies_report.py [--data-root ...]
Outputs: harvest01/report/policies_summary.parquet, policies.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    data_root = bps.resolve_data_root(args.data_root)
    rep = data_root / "harvest01" / "report"
    rep.mkdir(parents=True, exist_ok=True)

    p = pl.scan_parquet(str(data_root / "harvest01" / "policies" / "*.parquet"),
                        glob=True).collect()
    if p.height == 0:
        print("no policy rows")
        return 1
    fills = pl.scan_parquet(str(data_root / "harvest01" / "sim" / "fills" / "*.parquet"),
                            glob=True).collect().select(
        ["day", "variant", "clock", "rank", "ticker", "mfe_adj", "status"])
    p = p.join(fills, on=["day", "variant", "clock", "rank", "ticker"], how="left")
    p = p.filter(pl.col("status") == "filled")

    dcols = [c for c in p.columns if c.startswith("d100_")]
    ends = sorted({int(c.split("_")[1]) for c in dcols})
    rows = []
    for variant in ("primary",):
        pv = p.filter(pl.col("variant") == variant)
        for clock in sorted(pv["clock"].unique().to_list()):
            for N in (1, 2, 3, 4):
                sub = pv.filter((pl.col("clock") == clock) & (pl.col("rank") <= N))
                if sub.height == 0:
                    continue
                for pol in sorted(sub["policy"].unique().to_list()):
                    sp = sub.filter(pl.col("policy") == pol)
                    for E in ends:
                        dc = f"d100_{E}"
                        per_day = sp.group_by("day").agg(
                            pl.col(dc).mean().alias("bd"),
                            pl.col(dc).is_null().any().alias("has_null"))
                        v = per_day.filter(~pl.col("has_null") & pl.col("bd").is_not_null())
                        n = v.height
                        if n == 0:
                            continue
                        mean = float(v["bd"].mean())
                        sd = float(v["bd"].std() or 0.0)
                        rows.append({
                            "variant": variant, "clock": clock, "N": N, "policy": pol, "end": E,
                            "n_days": n, "mean_delta": mean,
                            "t": mean / (sd / n ** 0.5) if sd > 0 else 0.0,
                            "pos_share": float((v["bd"] > 0).mean()),
                            "fire_rate": float(sp["fired"].mean()),
                        })
    agg = pl.DataFrame(rows)
    agg.write_parquet(rep / "policies_summary.parquet")

    tail = (p.filter(pl.col("mfe_adj") >= 1.0).group_by("policy").agg(
        pl.len().alias("n_monsters"), pl.col("d100_720").mean().alias("monster_delta720"),
        pl.col("fired").mean().alias("fire_rate")).sort("monster_delta720", descending=True))
    tail.write_parquet(rep / "policies_tail.parquet")

    lines = ["# HARVEST01 — policy grid (dev only; member deltas -> basket-day means)\n"]
    lines.append("mean Δ = per-dollar vs hold-to-same-endpoint (pp), averaged over slots. "
                 "Positive = policy beat holding the same member.\n")
    for E in (720, ends[-1]):
        top = (agg.filter((pl.col("N") == 3) & (pl.col("end") == E))
               .sort("mean_delta", descending=True).head(12))
        lines.append(f"\n## Top policies, N=3, end={E} (all clocks pooled per policy? no — per clock)")
        lines.append("| clock | policy | mean Δ pp | t | pos | fire | n |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in top.iter_rows(named=True):
            lines.append(f"| {r['clock']} | {r['policy']} | {r['mean_delta']*100:.2f} | {r['t']:.1f} | "
                         f"{r['pos_share']:.2f} | {r['fire_rate']:.2f} | {r['n_days']} |")
    lines.append("\n## Policy average across clocks (N=3, end=720): mean of cell means")
    piv = (agg.filter((pl.col("N") == 3) & (pl.col("end") == 720))
           .group_by("policy").agg(pl.col("mean_delta").mean().alias("avg"), pl.len().alias("cells"))
           .sort("avg", descending=True))
    for r in piv.head(10).iter_rows(named=True):
        lines.append(f"- {r['policy']}: {r['avg']*100:+.2f} pp (cells={r['cells']})")
    lines.append("\n## Tail: policies vs the members that actually became big movers (post-fill MFE >= 100%)")
    lines.append("| policy | n | mean Δ@720 pp | fire rate |")
    lines.append("|---|---|---|---|")
    for r in tail.iter_rows(named=True):
        md = "" if r["monster_delta720"] is None else f"{r['monster_delta720']*100:+.2f}"
        lines.append(f"| {r['policy']} | {r['n_monsters']} | {md} | {r['fire_rate']:.2f} |")
    (rep / "policies.md").write_text("\n".join(lines) + "\n")
    print(f"policies_summary rows={agg.height} -> {rep/'policies.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
