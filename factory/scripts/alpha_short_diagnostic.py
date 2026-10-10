#!/usr/bin/env python3
"""Conditional short-side economics; NOT deployable without dated borrow/SSR evidence.

One fixed liquid/high-price exhaustion cohort, no parameter search. Qualify on
>=30% same-day open gain, >=$10 price, >=$10M causal dollar volume, and <=3%
below the recent high. Short the first observable event, at most three names/day.
Cover after a COMPLETED close breaches +10% adverse, at next actual open, else
at the session's last-minute open. No synthetic intrabar stop-level fill.

Reports the maximum affordable borrow/locate cost, not assumed free locates.
Unlimited short loss and actual gap losses are retained. Missing exits are
UNBOUNDED/UNKNOWN, not zero or a fictitious -100% bound.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import polars as pl
from alpha_open_panel import ROOT
from alpha_open_sim import load_panel, period

CONTRACT = {
    "qualification": "gain_open>=.30,price>=10,cum_dv>=10M,dd_high15>=-.03",
    "attempts": "one per ticker/day; first three causal clock events; ties higher gain then ticker",
    "entry": "minute-open SHORT proxy, no borrow/SSR assumed verified",
    "exit": "close>=1.10*entry triggers next actual open; otherwise session-end open",
    "costs_bps": [100, 150, 200],
    "order_budget": 1000,
    "integer_shares": True,
    "unknown_exit": "unbounded, not zero-imputed",
    "alternatives": 1,
    "status": "CONDITIONAL_SHORT_DIAGNOSTIC_NOT_EXECUTABLE_ALPHA",
    "required_before_promotion": [
        "point-in-time locates/fees",
        "SSR-aware legal fills",
        "as-of bid/ask and market impact",
        "margin/buy-in requirements",
    ],
    "out_of_fit_not_pristine": True,
}


def exit_short(bars: pl.DataFrame, entry_et: int, entry: float, end: int):
    f = bars.filter((pl.col("et") >= entry_et) & (pl.col("et") <= end)).sort("et")
    for r in f.iter_rows(named=True):
        if r["et"] >= end:
            return r["et"], r["open"], "session_flat"
        if r["close"] >= entry * 1.10:
            next_rows = f.filter(pl.col("et") > r["et"])
            if next_rows.height:
                n = next_rows.row(0, named=True)
                return n["et"], n["open"], "completed_close_stop"
            return None, None, "unknown_pending"
    return None, None, "unknown_pending"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--panel", type=Path, default=Path.home() / "alpha-data" / "open-search-v1")
    p.add_argument("--data", type=Path, default=ROOT / "data")
    args = p.parse_args()
    out = args.panel / "short_diagnostic"
    out.mkdir(parents=True, exist_ok=True)
    contract = {
        **CONTRACT,
        "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (out / "contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    panel, days = load_panel(args.panel)
    mask = (
        (pl.col("gain_open") >= 0.30)
        & (pl.col("log_price") >= np.log(10))
        & (pl.col("log_cum_dv") >= np.log1p(10_000_000))
        & (pl.col("dd_high15") >= -0.03)
    )
    signals = (
        panel.filter(mask)
        .sort(["day", "ticker", "t"])
        .unique(["day", "ticker"], keep="first", maintain_order=True)
        .sort(["day", "t", "gain_open", "ticker"], descending=[False, False, True, False])
        .group_by("day", maintain_order=True)
        .head(3)
    )
    signals.write_parquet(out / "signals.parquet")
    trades, attempts = [], {}
    for key, group in signals.partition_by("day", as_dict=True).items():
        day = key[0]
        tape = pl.read_parquet(args.data / "sip" / "net" / "bars" / f"{day}.parquet")
        attempts[day] = group.height
        for r in group.iter_rows(named=True):
            if r["entry_status"] != "filled_proxy":
                continue
            b = tape.filter(pl.col("ticker") == r["ticker"])
            et, px, why = exit_short(b, r["entry_et"], r["entry_open"], r["session_end"])
            qty = int(1000 // r["entry_open"])
            max_adverse = (
                (
                    float(b.filter(pl.col("et").is_between(r["entry_et"], et))["high"].max())
                    / r["entry_open"]
                    - 1
                )
                if et is not None
                else None
            )
            trades.append(
                {
                    "day": day,
                    "ticker": r["ticker"],
                    "entry_et": r["entry_et"],
                    "entry_open": r["entry_open"],
                    "exit_et": et,
                    "exit_open": px,
                    "quantity": qty,
                    "status": why,
                    "max_adverse": max_adverse,
                }
            )
    pl.DataFrame(trades).write_parquet(out / "trades.parquet")
    results = []
    for block in ("train", "validation", "confirmation"):
        dates = [d for d in days if period(d) == block]
        selected = [r for r in trades if r["day"] in dates]
        unknown = sum(r["exit_open"] is None for r in selected)
        for cost in CONTRACT["costs_bps"]:
            side = cost / 20_000
            daily = dict.fromkeys(dates, 0.0)
            values = []
            for r in selected:
                if r["exit_open"] is None:
                    daily[r["day"]] = None
                    continue
                pnl = r["quantity"] * (r["entry_open"] * (1 - side) - r["exit_open"] * (1 + side))
                values.append(pnl / 1000)
                if daily[r["day"]] is not None:
                    daily[r["day"]] += pnl / 3000
            known_daily = np.array([v for v in daily.values() if v is not None])
            rng = np.random.default_rng(20261008)
            draws = rng.choice(known_daily, (1000, len(known_daily)), replace=True).mean(axis=1)
            monthly = {}
            for day, value in daily.items():
                if value is not None:
                    monthly.setdefault(day[:7], []).append(value)
            metrics = {
                "block": block,
                "cost_bps": cost,
                "days": len(dates),
                "attempts": sum(attempts.get(d, 0) for d in dates),
                "fills": len(selected),
                "unknown_fills": unknown,
                "mean_net_known_fill": float(np.mean(values)),
                "mean_daily_known": float(known_daily.mean()),
                "unconditional_daily_mean": None if unknown else float(known_daily.mean()),
                "ci95_known_days": [float(v) for v in np.quantile(draws, [0.025, 0.975])],
                "worst_known_fill": float(min(values)),
                "max_intratrade_adverse": max(
                    r["max_adverse"] for r in selected if r["max_adverse"] is not None
                ),
                "positive_months": sum(float(np.mean(v)) > 0 for v in monthly.values()),
                "months": len(monthly),
                "monthly_mean": {k: float(np.mean(v)) for k, v in monthly.items()},
                "mean_affordable_locate_borrow_bps": float(np.mean(values) * 10_000),
                "status": CONTRACT["status"],
            }
            results.append(metrics)
            print(json.dumps({k: v for k, v in metrics.items() if k != "monthly_mean"}), flush=True)
    (out / "results.json").write_text(
        json.dumps({"contract": contract, "results": results}, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
