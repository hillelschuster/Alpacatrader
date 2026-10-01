#!/usr/bin/env python3
"""HARVEST01 — aggregate the unmanaged cell grid into the readable money map.

Reads harvest01/sim/cells/*.parquet (+ fills for execution texture) and writes:
    harvest01/report/cell_summary.parquet   per (variant, clock, N, exit) aggregates
    harvest01/report/readout.md             eye-readable tables, losers included

Blocks: B1 = 2021-02-01..2023-12-31; B2 = 2025-02-01..2026-05-29 (2024/2025-01 sealed).

Usage:
    .venv/bin/python factory/scripts/basket_harvest_report.py [--data-root ...]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402

B1 = ("2021-02-01", "2023-12-31")
B2 = ("2025-02-01", "2026-05-29")


def block_of(day: str) -> str:
    if B1[0] <= day <= B1[1]:
        return "B1"
    if B2[0] <= day <= B2[1]:
        return "B2"
    return "other"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    data_root = bps.resolve_data_root(args.data_root)
    sim = data_root / "harvest01" / "sim"
    rep = data_root / "harvest01" / "report"
    rep.mkdir(parents=True, exist_ok=True)

    cells = pl.scan_parquet(str(sim / "cells" / "*.parquet"),
                            glob=True, extra_columns="ignore").collect()
    if cells.height == 0:
        print("no cells")
        return 1
    cells = cells.with_columns(pl.col("day").map_elements(block_of, return_dtype=pl.Utf8).alias("block"))
    cells = cells.filter(pl.col("exit") > pl.col("clock"))  # contract valid_cells

    g = cells.group_by(["variant", "clock", "N", "exit"]).agg([
        pl.len().alias("n_days"),
        pl.col("ret_100").is_not_null().sum().alias("n_valid"),
        pl.col("ret_100").mean().alias("m100"),
        pl.col("ret_100").median().alias("med100"),
        pl.col("ret_100").std().alias("sd100"),
        (pl.col("ret_100") > 0).mean().alias("pos_share"),
        pl.col("ret_gross").mean().alias("m_gross"),
        pl.col("ret_150").mean().alias("m150"),
        pl.col("n_filled").mean().alias("avg_filled"),
        pl.col("n_blocked").mean().alias("avg_blocked"),
        pl.col("n_unknown").mean().alias("avg_unknown"),
    ]).with_columns((pl.col("m100") / (pl.col("sd100") / pl.col("n_valid").sqrt())).alias("t"))

    b = cells.filter(pl.col("ret_100").is_not_null()).group_by(
        ["variant", "clock", "N", "exit", "block"]).agg(
        pl.col("ret_100").mean().alias("m100")).pivot(
        values="m100", index=["variant", "clock", "N", "exit"], on="block")
    g = g.join(b, on=["variant", "clock", "N", "exit"], how="left")
    g.write_parquet(rep / "cell_summary.parquet")

    # ---- fills texture ----
    fills = pl.scan_parquet(str(sim / "fills" / "*.parquet"), glob=True, extra_columns="ignore").collect()
    fill_stats = fills.group_by(["variant", "clock"]).agg([
        pl.col("status").eq("filled").mean().alias("fill_rate"),
        pl.col("entry_gap_min").filter(pl.col("status") == "filled").mean().alias("mean_gap"),
        pl.col("slip_vs_decision").filter(pl.col("status") == "filled").mean().alias("mean_slip"),
        pl.col("slip_vs_decision").filter(pl.col("status") == "filled").median().alias("med_slip"),
        pl.col("mfe_adj").filter(pl.col("status") == "filled").mean().alias("mean_mfe"),
        pl.col("mae_adj").filter(pl.col("status") == "filled").mean().alias("mean_mae"),
    ])
    fill_stats.write_parquet(rep / "entry_texture.parquet")

    lines = []
    lines.append("# HARVEST01 — unmanaged basket money map (dev only)\n")
    lines.append(f"days in cells: {cells['day'].n_unique()}; cells: {g.height}; "
                 f"variants: {sorted(cells['variant'].unique().to_list())}\n")

    prim = g.filter(pl.col("variant") == "primary")
    for N in (1, 3):
        lines.append(f"\n## Wealth curve — variant=primary, N={N} (mean net-100 bps basket-day, %)")
        lines.append("rows = entry clock, cols = exit clock\n")
        piv = (prim.filter(pl.col("N") == N)
               .with_columns((pl.col("m100") * 100).round(2))
               .pivot(values="m100", index="clock", on="exit").sort("clock"))
        cols = [c for c in piv.columns]
        lines.append("| " + " | ".join(str(c) for c in cols) + " |")
        lines.append("|" + "---|" * len(cols))
        for row in piv.iter_rows(named=True):
            lines.append("| " + " | ".join("" if row[c] is None else f"{row[c]:.2f}" for c in cols) + " |")

    lines.append("\n## Best cells by mean net-100 (n_valid >= 400; exploratory — consumes DOF)")
    best = (g.filter((pl.col("n_valid") >= 400) & (pl.col("variant") == "primary"))
             .sort("m100", descending=True).head(25))
    lines.append("| clock | N | exit | m100 % | med % | pos | B1 % | B2 % | n | t |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in best.iter_rows(named=True):
        lines.append("| {clock} | {N} | {exit} | {m:.2f} | {md:.2f} | {p:.2f} | {b1} | {b2} | {n} | {t:.1f} |".format(
            clock=r["clock"], N=r["N"], exit=r["exit"], m=r["m100"] * 100, md=r["med100"] * 100,
            p=r["pos_share"], b1="" if r.get("B1") is None else round(r["B1"] * 100, 2),
            b2="" if r.get("B2") is None else round(r["B2"] * 100, 2), n=r["n_valid"],
            t=r["t"] if r["t"] is not None else 0))

    lines.append("\n## Worst cells (same filter)")
    worst = (g.filter((pl.col("n_valid") >= 400) & (pl.col("variant") == "primary"))
              .sort("m100").head(10))
    lines.append("| clock | N | exit | m100 % | n |")
    lines.append("|---|---|---|---|---|")
    for r in worst.iter_rows(named=True):
        lines.append(f"| {r['clock']} | {r['N']} | {r['exit']} | {r['m100']*100:.2f} | {r['n_valid']} |")

    lines.append("\n## Entry texture by clock (primary; filled members only)")
    lines.append("| clock | fill_rate | mean_gap_min | mean_slip % | med_slip % | mean_mfe % | mean_mae % |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in fill_stats.filter(pl.col("variant") == "primary").sort("clock").iter_rows(named=True):
        def pc(x):
            return "" if x is None else f"{x*100:.2f}"
        lines.append(f"| {r['clock']} | {r['fill_rate']:.3f} | {r['mean_gap']:.2f} | {pc(r['mean_slip'])} | "
                     f"{pc(r['med_slip'])} | {pc(r['mean_mfe'])} | {pc(r['mean_mae'])} |")

    lines.append("\n## Unknown (censored) counts — where the tape cannot price an exit")
    unk = (cells.filter((pl.col("variant") == "primary") & (pl.col("N") == 3))
           .group_by(["clock", "exit"]).agg(
        pl.col("n_unknown").sum().alias("unk_total"),
        pl.len().alias("rows")).sort("unk_total", descending=True).head(10))
    lines.append("| clock | exit | unknown slots | rows |")
    lines.append("|---|---|---|---|")
    for r in unk.iter_rows(named=True):
        lines.append(f"| {r['clock']} | {r['exit']} | {r['unk_total']} | {r['rows']} |")

    # ---- rank contribution: where does the money come from ----
    lines.append("\n## Member-rank contribution (primary; mean per-member net-100 at fixed exits, %)")
    lines.append("| clock | exit | rank1 % | rank2 % | rank3 % | rank4 % | fill rate r1-r4 |")
    lines.append("|---|---|---|---|---|---|---|")
    for clock in (560, 600, 660):
        for E in (600, 630, 720, 959):
            fr = fills.filter((pl.col("variant") == "primary") & (pl.col("clock") == clock))
            col = f"r{E}_100"
            if col not in fr.columns:
                continue
            row = []
            for rk in (1, 2, 3, 4):
                s = fr.filter(pl.col("rank") == rk)
                m = s[col].drop_nulls().mean()
                row.append("" if m is None else f"{m*100:.2f}")
            fills_rate = "/".join(
                f"{(fr.filter(pl.col('rank') == rk)['status'] == 'filled').mean():.2f}" for rk in (1, 2, 3, 4))
            if all(x == "" for x in row):
                continue
            lines.append(f"| {clock} | {E} | " + " | ".join(row) + f" | {fills_rate} |")

    lines.append("\n## Monthly means for two headline cells (primary, N=3)")
    for (clk, E) in ((560, 720), (600, 720)):
        mm = (cells.filter((pl.col("variant") == "primary") & (pl.col("clock") == clk)
                           & (pl.col("N") == 3) & (pl.col("exit") == E) &
                           pl.col("ret_100").is_not_null())
              .with_columns(pl.col("day").str.slice(0, 7).alias("mon"))
              .group_by("mon").agg(pl.col("ret_100").mean().alias("m"), pl.len().alias("n")).sort("mon"))
        lines.append(f"\nclock={clk} exit={E}: " +
                     ", ".join(f"{r['mon']} {r['m']*100:+.2f} (n={r['n']})" for r in mm.iter_rows(named=True)))

    # monthly x clock matrix at N=3 for the two golden-window exits
    for E in (660, 720):
        mm = (cells.filter((pl.col("variant") == "primary") & (pl.col("N") == 3)
                           & (pl.col("exit") == E) & pl.col("ret_100").is_not_null())
              .with_columns(pl.col("day").str.slice(0, 7).alias("mon"))
              .group_by(["mon", "clock"]).agg(pl.col("ret_100").mean().alias("m")))
        piv = mm.pivot(values="m", index="mon", on="clock").sort("mon")
        lines.append(f"\n## Monthly x clock matrix — primary N=3, exit={E} (net-100 mean, %)")
        cols = [c for c in piv.columns if c != "mon"]
        lines.append("| month | " + " | ".join(cols) + " |")
        lines.append("|" + "---|" * (len(cols) + 1))
        for r in piv.iter_rows(named=True):
            vals = ["{:.1f}".format(r[c] * 100) if r[c] is not None else "" for c in cols]
            lines.append(f"| {r['mon']} | " + " | ".join(vals) + " |")

    lines.append("\n## Block means for headline clocks (primary, N=3, exit=720)")
    bb = (cells.filter((pl.col("variant") == "primary") & (pl.col("N") == 3)
                       & (pl.col("exit") == 720) & pl.col("ret_100").is_not_null())
          .with_columns(pl.when(pl.col("day") <= "2023-12-31").then(pl.lit("B1"))
                        .when(pl.col("day") >= "2025-02-01").then(pl.lit("B2"))
                        .otherwise(pl.lit("other")).alias("block"))
          .group_by(["clock", "block"]).agg(pl.col("ret_100").mean().alias("m")).pivot(
              values="m", index="clock", on="block").sort("clock"))
    lines.append("| clock | B1 % | B2 % |")
    lines.append("|---|---|---|")
    for r in bb.iter_rows(named=True):
        f = lambda x: "" if x is None else f"{x*100:.2f}"
        lines.append(f"| {r['clock']} | {f(r.get('B1'))} | {f(r.get('B2'))} |")

    (rep / "readout.md").write_text("\n".join(lines) + "\n")
    print(f"cell_summary rows={g.height}; readout written to {rep/'readout.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
