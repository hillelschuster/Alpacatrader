#!/usr/bin/env python3
"""Report lifecycle replay dollars at independent basket-day granularity.

No inference from overlapping minute counts. Cash has zero return. Unknown positions
are counted and excluded visibly, never assigned cash. Tail tags are attribution only.
"""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import polars as pl


def summary(values: pl.Series) -> dict:
    x = values.drop_nulls().to_numpy()
    x = x[np.isfinite(x)]
    if not len(x):
        return {"n": 0, "mean": None, "median": None, "day_se": None}
    se = float(x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 1 else None
    return {"n": len(x), "mean": float(x.mean()), "median": float(np.median(x)),
            "day_se": se, "positive_share": float((x > 0).mean()),
            "p05": float(np.quantile(x, .05)), "p95": float(np.quantile(x, .95)),
            "total_positive": float(x[x > 0].sum()), "total_negative": float(x[x < 0].sum()),
            "worst": float(x.min()), "best": float(x.max()),
            "top5_removed_mean": float(np.sort(x)[:-5].mean()) if len(x) > 5 else None}


def pct(x) -> str:
    return "unknown" if x is None else f"{100*x:+.3f}%"


def report(daily: list[dict], members: list[dict], fills: list[dict], root: Path,
           tag: str, notional: float = 10000.0) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    d = pl.DataFrame(daily, infer_schema_length=None)
    m = pl.DataFrame(members, infer_schema_length=None)
    f = pl.DataFrame(fills, infer_schema_length=None) if fills else pl.DataFrame()
    d.write_parquet(root / f"daily_{tag}.parquet")
    m.write_parquet(root / f"members_{tag}.parquet")
    if f.height:
        f.write_parquet(root / f"fills_{tag}.parquet")
    rows, evidence = [], {}
    for (clock, policy), sub in d.group_by(["clock", "policy"]):
        sub = sub.sort("day")
        s = summary(sub["ret"])
        s.update({"unknown_days": int(sub["unknown"].sum()), "scheduled_days": sub.height,
                  "orders_per_day": float(sub["orders"].mean()),
                  "turnover_per_day": float(sub["turnover"].mean()),
                  "fees_per_day": float(sub["fees"].mean()),
                  "dollars_per_day_at_notional": None if s["mean"] is None else s["mean"] * notional})
        evidence[f"{clock}|{policy}"] = s
        rows.append(f"| {clock} | {policy} | {s['n']}/{sub.height} | {pct(s['mean'])} | {pct(s['median'])} | "
                    f"{pct(s['day_se'])} | {s['orders_per_day']:.2f} | {s['turnover_per_day']:.2f} |")
    monthly = d.with_columns(pl.col("day").str.slice(0,7).alias("month")).group_by(
        ["clock", "policy", "month"]).agg(pl.col("ret").mean().alias("mean"),
                                         pl.col("ret").count().alias("known_n"),
                                         pl.col("unknown").sum().alias("unknown_n"))
    monthly.sort(["clock", "policy", "month"]).write_parquet(root / f"monthly_{tag}.parquet")
    # Attribution on original capital: tag future MFE only here, never in allocator.
    mfe = pl.col("mfe_day").cast(pl.Float64)
    tail = m.with_columns(
        pl.when(~mfe.is_finite() | mfe.is_null()).then(pl.lit("unknown_outcome"))
        .when(mfe >= 1).then(pl.lit("monster100"))
        .when(mfe >= .3).then(pl.lit("runner30"))
        .when(mfe >= .1).then(pl.lit("mid10"))
        .otherwise(pl.lit("dud")).alias("outcome"))
    attr = tail.group_by(["clock", "policy", "outcome"]).agg(
        pl.col("pnl").sum().alias("sum_pnl"), pl.col("pnl").count().alias("known_name_days"),
        pl.col("unknown").sum().alias("unknown_name_days"))
    attr.write_parquet(root / f"attribution_{tag}.parquet")
    differences = []
    for (clock, policy), actual in d.group_by(["clock", "policy"]):
        suffix = policy.rsplit("_", 1)[-1]
        hold_name = f"hold_{suffix}" if suffix.endswith("bps") else "hold"
        comparator = d.filter((pl.col("clock") == clock) & (pl.col("policy") == hold_name))
        if not comparator.height:
            continue
        pair = actual.select(["day", "clock", "ret"]).join(
            comparator.select(["day", "clock", pl.col("ret").alias("hold_ret")]),
            on=["day", "clock"], how="inner").filter(
                pl.col("ret").is_finite() & pl.col("hold_ret").is_finite())
        if not pair.height:
            continue
        evidence[f"{clock}|{policy}"]["paired_hold_days"] = pair.height
        evidence[f"{clock}|{policy}"]["ev_vs_hold"] = float((pair["ret"] - pair["hold_ret"]).mean())
        basis = tail.filter((pl.col("clock") == clock) & (pl.col("policy") == hold_name)).select(
            ["day", "clock", "ticker", pl.col("pnl").alias("hold_pnl")])
        names = tail.filter((pl.col("clock") == clock) & (pl.col("policy") == policy)).join(
            basis, on=["day", "clock", "ticker"], how="left").join(
            pair.select(["day", "clock"]), on=["day", "clock"], how="inner")
        # Rank4/5 are not owned by the top3 hold comparator; its claim is known zero.
        names = names.with_columns(pl.when(pl.col("rank") > 3).then(0.0)
                                   .otherwise(pl.col("hold_pnl")).alias("hold_pnl"))
        names = names.filter(pl.col("pnl").is_finite() & pl.col("hold_pnl").is_finite())
        for outcome, group in names.group_by("outcome"):
            differences.append({"clock": clock, "policy": policy, "outcome": outcome[0],
                                "paired_days": pair.height,
                                "absolute_contribution": float(group["pnl"].sum() / pair.height),
                                "hold_contribution": float(group["hold_pnl"].sum() / pair.height),
                                "delta_contribution": float((group["pnl"] - group["hold_pnl"]).sum() / pair.height)})
    if differences:
        pl.DataFrame(differences).write_parquet(root / f"tail_dud_vs_hold_{tag}.parquet")
    best = m.filter(pl.col("pnl").is_finite()).sort("pnl", descending=True).head(30)
    worst = m.filter(pl.col("pnl").is_finite()).sort("pnl").head(30)
    best.write_parquet(root / f"top_names_{tag}.parquet")
    worst.write_parquet(root / f"bottom_names_{tag}.parquet")
    if f.height and "participation" in f.columns:
        coverage = f["participation"].is_not_null().mean()
        v = f["participation"].drop_nulls()
        evidence["capacity"] = {"notional": notional, "fill_volume_coverage": coverage,
                                "median_volume_participation": float(v.median()) if len(v) else None,
                                "p90_volume_participation": float(v.quantile(.9)) if len(v) else None,
                                "warning": "historical-volume participation diagnostic, not an impact/fill guarantee"}
    (root / f"evidence_{tag}.json").write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    lines = [f"# LIFECYCLE-01 economic replay: {tag}", "",
             f"Original capital per independent basket-day: ${notional:,.0f}; cash return zero.",
             "BENCHMARKS ONLY: unconditional hold (and its scope variants) measure the window, they are never the strategy.",
             "Printed opens + declared friction are executable-price proxies, not proven market fills.",
             "Unknown liquidations excluded visibly. Calendar-day SE is descriptive, not independent-minute precision.", "",
             "| ET clock | policy | complete/scheduled days | mean vs cash | median | day SE | orders/day | turnover/day |",
             "|---|---|---|---|---|---|---|---|"] + sorted(rows)
    lines += ["", "## Exact P&L creators", "", "| day | clock | policy | ticker | rank | dollars |", "|---|---|---|---|---|---|"]
    for r in best.head(15).iter_rows(named=True):
        lines.append(f"| {r['day']} | {r['clock']} | {r['policy']} | {r['ticker']} | {r['rank']} | {r['pnl']*notional:+.2f} |")
    lines += ["", "## Exact P&L destroyers", "", "| day | clock | policy | ticker | rank | dollars |", "|---|---|---|---|---|---|"]
    for r in worst.head(15).iter_rows(named=True):
        lines.append(f"| {r['day']} | {r['clock']} | {r['policy']} | {r['ticker']} | {r['rank']} | {r['pnl']*notional:+.2f} |")
    (root / f"economics_{tag}.md").write_text("\n".join(lines) + "\n")
    return evidence
