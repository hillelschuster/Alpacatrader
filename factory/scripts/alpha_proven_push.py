#!/usr/bin/env python3
"""One fixed fresh-entry test of the measured +30% proven-push opportunity.

Not a threshold grid or an owned-position continuation statistic. Qualification:
>=30% from same-day open, <=3% below recent high, >=3 positive watchlist peers,
non-contracting dollar flow, and causal liquidity. First event per ticker/day;
next-minute open entry, 15-clock-minute exit. Failed lookalikes stay in the calendar.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import polars as pl
from alpha_open_sim import causal_liquidity, load_panel, period, replay

CONTRACT = {
    "hypothesis": "fresh capital after a proven open-anchored push with broad flow confirmation",
    "conditions": {
        "gain_open_min": 0.30,
        "dd_high15_min": -0.03,
        "peer_positive3_min": 3,
        "dv_accel_min": 1.0,
    },
    "peer_gate": "at least three OTHER watchlist names have ret3>0; subject excluded",
    "liquidity": "cum_dv>=1M and actual bars15>=12; all causal",
    "horizon_minutes": 15,
    "alternatives": 1,
    "first_event_per_ticker_day": True,
    "costs_bps": [100, 150, 200],
    "out_of_fit_not_pristine": True,
    "selection": "none; one fixed formulation before any outcome inspected",
    "fill_model": "market open proxy; actual side-aware quote audit still required",
}


def qualifies() -> pl.Expr:
    return (
        (pl.col("gain_open") >= 0.30)
        & (pl.col("dd_high15") >= -0.03)
        & ((pl.col("peer_positive3") - (pl.col("ret3") > 0).cast(pl.Int64)) >= 3)
        & (pl.col("dv_accel") >= 1.0)
        & causal_liquidity(None)
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--panel", type=Path, default=Path.home() / "alpha-data" / "open-search-v1")
    p.add_argument("--out", type=Path)
    args = p.parse_args()
    out = args.out or args.panel / "proven_push"
    out.mkdir(parents=True, exist_ok=True)
    contract = {
        **CONTRACT,
        "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (out / "contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    frame, days = load_panel(args.panel)
    if len(days) != 1066:
        raise ValueError("requires complete 1066-day development corpus")
    signals = (
        frame.filter(qualifies())
        .sort(["day", "ticker", "t"])
        .unique(["day", "ticker"], keep="first", maintain_order=True)
        .with_columns(pl.col("gain_open").alias("score"))
    )
    signals.write_parquet(out / "signals.parquet")
    results = []
    for block in ("train", "validation", "confirmation"):
        dates = [d for d in days if period(d) == block]
        selected = signals.filter(pl.col("day").is_in(dates))
        for cost in CONTRACT["costs_bps"]:
            metrics, trades = replay(selected, dates, 15, cost)
            daily = np.array([r["lower_bound_return"] for r in metrics["daily"]])
            rng = np.random.default_rng(20261008)
            draws = rng.choice(daily, size=(1000, len(daily)), replace=True).mean(axis=1)
            metrics["day_bootstrap_ci95"] = [float(v) for v in np.quantile(draws, [0.025, 0.975])]
            metrics["block"] = block
            metrics["causal_events"] = selected.height
            results.append(metrics)
            pl.DataFrame(trades).write_parquet(out / f"trades_{block}_{cost}.parquet")
            print(
                json.dumps(
                    {
                        k: v
                        for k, v in metrics.items()
                        if k not in ("daily", "monthly_mean_lower_bound")
                    }
                ),
                flush=True,
            )
    (out / "results.json").write_text(
        json.dumps({"contract": contract, "results": results}, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
