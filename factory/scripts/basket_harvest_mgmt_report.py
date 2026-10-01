#!/usr/bin/env python3
"""HARVEST01 — aggregate management-rule member deltas into basket-level readouts.

Member deltas (per-dollar, vs holding to the same endpoint) are averaged over the N
equal slots of each basket-day; the basket-day is the unit. Also reports, per rule:
fire rate, how often a fire preceded a later >= +30% excursion (monster sold), and the
mean delta restricted to members that were themselves large post-fill movers (the tail
question: does the rule repeatedly sell the future monster?).

Usage:
    .venv/bin/python factory/scripts/basket_harvest_mgmt_report.py [--data-root ...]
Outputs: harvest01/report/mgmt_summary.parquet, mgmt.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402

ENDS = [600, 630, 660, 720, 959, 779]
RULES = ["hold", "gb10", "dmg_wait", "failrec_a3", "failrec_a5", "failrec_a8",
         "decay_v", "decay_nh", "tstop30", "gb10_half", "failrec_a5_half",
         "reentry_gb10", "replace_gb10"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    data_root = bps.resolve_data_root(args.data_root)
    rep = data_root / "harvest01" / "report"
    rep.mkdir(parents=True, exist_ok=True)

    m = pl.scan_parquet(str(data_root / "harvest01" / "mgmt" / "*.parquet"), glob=True).collect()
    if m.height == 0:
        print("no mgmt rows")
        return 1
    delta_cols = [c for c in m.columns if c.startswith("delta100_")]
    ends = sorted({int(c.split("_")[1]) for c in delta_cols})

    rows = []
    for variant in sorted(m["variant"].unique().to_list()):
        for clock in sorted(m["clock"].unique().to_list()):
            base = m.filter((pl.col("variant") == variant) & (pl.col("clock") == clock))
            for N in (1, 2, 3, 4):
                sub = base.filter(pl.col("rank") <= N)
                if sub.height == 0:
                    continue
                for rule in RULES:
                    sr = sub.filter(pl.col("rule") == rule)
                    if sr.height == 0:
                        continue
                    for E in ends:
                        dc = f"delta100_{E}"
                        if dc not in sr.columns:
                            continue
                        per_day = sr.group_by("day").agg(
                            pl.col(dc).mean().alias("bd"),
                            pl.col(dc).is_null().any().alias("has_null"))
                        valid = per_day.filter(~pl.col("has_null")).filter(pl.col("bd").is_not_null())
                        n = valid.height
                        if n == 0:
                            continue
                        mean = float(valid["bd"].mean())
                        sd = float(valid["bd"].std() or 0.0)
                        t = mean / (sd / max(n, 1) ** 0.5) if sd > 0 else 0.0
                        b1 = valid.filter(pl.col("day") <= "2023-12-31")["bd"]
                        b2 = valid.filter(pl.col("day") >= "2025-02-01")["bd"]
                        rows.append({
                            "variant": variant, "clock": clock, "N": N, "rule": rule, "end": E,
                            "n_days": n, "mean_delta": mean, "t": t,
                            "pos_share": float((valid["bd"] > 0).mean()),
                            "median_delta": float(valid["bd"].median()),
                            "B1": float(b1.mean()) if b1.len() else None,
                            "B2": float(b2.mean()) if b2.len() else None,
                        })
    agg = pl.DataFrame(rows)
    agg.write_parquet(rep / "mgmt_summary.parquet")

    fire = (m.group_by(["variant", "rule"]).agg(
        pl.col("fired").mean().alias("fire_rate"),
        pl.col("monster_after").sum().alias("monsters_after_fire"),
        pl.len().alias("n_members")))
    # tail split: members whose own post-fill MFE was large
    fills = pl.scan_parquet(str(data_root / "harvest01" / "sim" / "fills" / "*.parquet"),
                            glob=True).collect().select(["day", "variant", "clock", "rank",
                                                         "ticker", "mfe_adj"])
    mm = m.join(fills, on=["day", "variant", "clock", "rank", "ticker"], how="left")
    tail = []
    for thr in (0.3, 1.0):
        for rule in RULES:
            s = mm.filter((pl.col("rule") == rule) & (pl.col("mfe_adj") >= thr))
            if s.height == 0:
                continue
            endc = "delta100_720" if "delta100_720" in s.columns else None
            tail.append({"mfe_ge": thr, "rule": rule, "n": s.height,
                         "fire_rate": float(s["fired"].mean()),
                         "mean_delta720": float(s[endc].mean()) if endc else None})
    taildf = pl.DataFrame(tail)
    taildf.write_parquet(rep / "mgmt_tail.parquet")

    lines = ["# HARVEST01 — management rules (dev only, member deltas -> basket-day means)\n"]
    lines.append("deltas are per-dollar vs hold-to-same-endpoint, ×100 (pp), averaged over slots; "
                 "positive = rule beat holding.\n")
    lines.append("## Primary variant, N=3, mean delta at each endpoint (pp)")
    lines.append("| clock | rule | end | mean pp | t | pos days | B1 | B2 | n |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    sel = agg.filter((pl.col("variant") == "primary") & (pl.col("N") == 3)).sort(
        ["clock", "end", "rule"])
    for r in sel.iter_rows(named=True):
        def g(x):
            return "" if x is None else f"{x*100:+.2f}"
        lines.append(f"| {r['clock']} | {r['rule']} | {r['end']} | {r['mean_delta']*100:.2f} | "
                     f"{r['t']:.1f} | {r['pos_share']:.2f} | {g(r.get('B1'))} | {g(r.get('B2'))} | {r['n_days']} |")
    lines.append("\n## Fire rates and future monsters sold (all clocks/variants pooled)")
    lines.append("| variant | rule | fire_rate | monsters_after_fire | n |")
    lines.append("|---|---|---|---|---|")
    for r in fire.sort(["variant", "rule"]).iter_rows(named=True):
        lines.append(f"| {r['variant']} | {r['rule']} | {r['fire_rate']:.3f} | "
                     f"{r['monsters_after_fire']} | {r['n_members']} |")
    lines.append("\n## Tail members (post-fill MFE >= threshold): fire rate and mean delta@720 (pp)")
    lines.append("| mfe_ge | rule | n | fire_rate | mean_delta720 pp |")
    lines.append("|---|---|---|---|---|")
    for r in taildf.sort(["mfe_ge", "rule"]).iter_rows(named=True):
        md = "" if r["mean_delta720"] is None else f"{r['mean_delta720']*100:.2f}"
        lines.append(f"| {r['mfe_ge']} | {r['rule']} | {r['n']} | {r['fire_rate']:.3f} | {md} |")
    (rep / "mgmt.md").write_text("\n".join(lines) + "\n")
    print(f"mgmt_summary rows={agg.height}; mgmt.md written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
