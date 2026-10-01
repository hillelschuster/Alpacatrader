#!/usr/bin/env python3
"""HARVEST01 — birds-eye readout of the golden-window anatomy (marks).

Answers: entering at clock X with N names, WHEN does the basket peak (median ET), how
much of the peak survives to 10:00/10:30/11:00/11:30/12:00/13:00 (capture ratios), and
when do the individual members top out. Marks only — executable economics are separate.

Usage: .venv/bin/python factory/scripts/basket_harvest_window_report.py
Outputs: harvest01/report/window.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402

EXITS = [600, 615, 630, 645, 660, 675, 690, 705, 720, 750, 780]


def et_label(et):
    if et is None:
        return ""
    et = int(et)
    return f"{et//60:02d}:{et%60:02d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    data_root = bps.resolve_data_root(args.data_root)
    rep = data_root / "harvest01" / "report"
    rep.mkdir(parents=True, exist_ok=True)
    w = pl.scan_parquet(str(data_root / "harvest01" / "window" / "*.parquet"),
                        glob=True).collect()
    if w.height == 0:
        print("no window rows")
        return 1
    lines = ["# HARVEST01 — birds-eye golden-window anatomy (marks; dev)\n"]
    lines.append("mark = basket value using each member's last known close; peak = max over "
                 "the session path. capture@E = mark@E / peak mark.\n")

    for N in (2, 3, 4):
        lines.append(f"\n## N={N}: median peak time and mean mark (%) by entry clock")
        lines.append("| clock | median peak | p25 | p75 | peak % | r660 | r690 | r720 | r780 | cap@720 | cap@780 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        sub = w.filter(pl.col("N") == N)
        for clock in sorted(sub["clock"].unique().to_list()):
            s = sub.filter(pl.col("clock") == clock)
            lines.append("| {} | {} | {} | {} | {:.2f} | {:.2f} | {:.2f} | {:.2f} | {:.2f} | {:.2f} | {:.2f} |".format(
                clock, et_label(s["peak_et"].median()), et_label(s["peak_et"].quantile(0.25)),
                et_label(s["peak_et"].quantile(0.75)), s["peak_ret"].mean() * 100,
                s["r660"].mean() * 100, s["r690"].mean() * 100, s["r720"].mean() * 100,
                s["r780"].mean() * 100, s["cap720"].mean() * 100 if "cap720" in s.columns else float("nan"),
                s["cap780"].mean() * 100 if "cap780" in s.columns else float("nan")))

    lines.append("\n## Distribution of the basket mark at key window clocks (N=3, all clocks pooled)")
    lines.append("| mark clock | p10 % | p25 % | median % | p75 % | p90 % | mean % | pos share |")
    lines.append("|---|---|---|---|---|---|---|---|")
    s3 = w.filter(pl.col("N") == 3)
    for E in (600, 630, 660, 690, 720, 750, 780):
        c = f"r{E}"
        if c not in s3.columns:
            continue
        v = s3[c].drop_nulls()
        if not v.len():
            continue
        lines.append(f"| {et_label(E)} | {v.quantile(0.10)*100:.2f} | {v.quantile(0.25)*100:.2f} | "
                     f"{v.median()*100:.2f} | {v.quantile(0.75)*100:.2f} | {v.quantile(0.90)*100:.2f} | "
                     f"{v.mean()*100:.2f} | {(v > 0).mean():.2f} |")

    lines.append("\n## Peak timing relative to entry (N=3): share of days peaking 30–90 min after entry")
    lines.append("| clock | n | share peak in [entry+30, entry+90] | share peak before entry+30 | share peak after entry+90 |")
    lines.append("|---|---|---|---|---|")
    for clock in sorted(s3["clock"].unique().to_list()):
        s = s3.filter(pl.col("clock") == clock)
        if not s.height:
            continue
        rel = s["peak_et"] - clock
        lines.append(f"| {clock} | {s.height} | {((rel >= 30) & (rel <= 90)).mean():.2f} | "
                     f"{(rel < 30).mean():.2f} | {(rel > 90).mean():.2f} |")

    lines.append("\n## Peak-minute histogram (N=3, pooled, 30-min bins)")
    s3 = w.filter(pl.col("N") == 3).with_columns(
        ((pl.col("peak_et") // 30) * 30).alias("bin"))
    hist = s3.group_by("bin").agg(pl.len().alias("n")).sort("bin")
    tot = hist["n"].sum()
    for r in hist.iter_rows(named=True):
        lines.append(f"- {et_label(r['bin'])}–{et_label(r['bin']+29)}: {r['n']} "
                     f"({r['n']/tot*100:.1f}%)")

    mp = data_root / "harvest01" / "window" / "members"
    if mp.exists():
        m = pl.scan_parquet(str(mp / "*.parquet"), glob=True).collect()
        if m.height:
            lines.append("\n## Individual member peak minute by entry clock (median, N<=4 members)")
            lines.append("| clock | median member peak | median peak return % | median close-from-fill % | n |")
            lines.append("|---|---|---|---|---|")
            for clock in sorted(m["clock"].unique().to_list()):
                s = m.filter(pl.col("clock") == clock)
                lines.append(f"| {clock} | {et_label(s['m_peak_et'].median())} | "
                             f"{s['m_peak_ret'].median()*100:.2f} | {s['m_close_ret'].median()*100:.2f} | {s.height} |")
    (rep / "window.md").write_text("\n".join(lines) + "\n")
    print(f"window.md written; rows={w.height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
