#!/usr/bin/env python3
"""As-of NBBO audit of fixed strategy trades, not an assumed exchange fill.

Market buys price at the ASK and sales at the BID at a declared arrival latency.
No +/-minute quote windows, future quote selection, full-spread-per-leg charge,
or round-lot/share confusion. Missing/nonfirm/stale/depth-insufficient quotes stay
UNKNOWN. Quote-covered results cannot certify the uncovered strategy portfolio.

A 0/25/50/100bps residual-cost ladder is diagnostic alongside 100-200bps research
rulers: quotes measure touch cost, not market impact or guaranteed routing fills.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl
from alpha_open_panel import ROOT, allowed

ET = ZoneInfo("America/New_York")


def clock_us(day: str, minute: int, latency_ms: int) -> int:
    clock = datetime.fromisoformat(day).replace(tzinfo=ET) + timedelta(
        minutes=minute, milliseconds=latency_ms
    )
    return int(clock.astimezone(UTC).timestamp() * 1_000_000)


def asof_quote(
    frame: pl.DataFrame, target_us: int, day: str, max_age_s: float = 2.0
) -> tuple[dict | None, str]:
    if frame.is_empty():
        return None, "quote_not_acquired"
    stamps = frame["ts_utc"].cast(pl.Int64).to_numpy()
    i = int(np.searchsorted(stamps, target_us, side="right")) - 1
    if i < 0:
        return None, "no_prior_quote"
    q = frame.row(i, named=True)
    age = (target_us - stamps[i]) / 1_000_000
    if age > max_age_s:
        return None, "stale_quote"
    bid, ask = q["bid_price"], q["ask_price"]
    if not (np.isfinite(bid) and np.isfinite(ask) and 0 < bid <= ask):
        return None, "invalid_or_crossed_quote"
    conditions = q["conditions"] or []
    if not conditions or any(c != "R" for c in conditions):
        return None, "nonregular_quote"
    multiplier = 100 if day < "2025-11-03" else 1
    return {
        "bid": bid,
        "ask": ask,
        "bid_shares": q["bid_size"] * multiplier,
        "ask_shares": q["ask_size"] * multiplier,
        "age_s": age,
        "quote_us": int(stamps[i]),
        "spread_bps": (ask / bid - 1) * 10_000,
    }, "quoted"


def load_trades(path: Path) -> list[dict]:
    if path.suffix == ".parquet":
        return pl.read_parquet(path).to_dicts()
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError("trade JSON must be a list of trade records")
    return data


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trades", type=Path, required=True)
    p.add_argument("--data", type=Path, default=ROOT / "data")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--latency-ms", type=int, default=250)
    p.add_argument("--max-age-s", type=float, default=2.0)
    p.add_argument("--order-budget", type=float, default=1000)
    args = p.parse_args()
    trades = load_trades(args.trades)
    days = {r["day"] for r in trades} | {r.get("exit_day", r["day"]) for r in trades}
    if any(not allowed(day) for day in days):
        raise ValueError("protected quote read refused")
    requests = {}
    for r in trades:
        for day, minute in ((r["day"], r["entry_et"]), (r.get("exit_day", r["day"]), r["exit_et"])):
            if minute is not None:
                requests.setdefault(day, {}).setdefault(r["ticker"], set()).add(minute)
    # Retain only requested as-of quote records; at most one day's streams are resident.
    quotes = {}
    for day, tickers in sorted(requests.items()):
        path = args.data / "sip" / "net" / "quotes" / f"{day}.parquet"
        if path.exists():
            q = pl.read_parquet(path).filter(pl.col("symbol").is_in(list(tickers)))
            groups = q.sort("symbol", "ts_utc").partition_by("symbol", as_dict=True)
            for key, f in groups.items():
                ticker = key[0]
                for minute in tickers[ticker]:
                    quotes[(day, ticker, minute)] = asof_quote(
                        f, clock_us(day, minute, args.latency_ms), day, args.max_age_s
                    )
            del q, groups
    rows = []
    for r in trades:
        record = {
            "day": r["day"],
            "ticker": r["ticker"],
            "entry_et": r["entry_et"],
            "exit_day": r.get("exit_day", r["day"]),
            "exit_et": r["exit_et"],
            "proxy_net": r.get("net"),
            "quoted_net_0": None,
            "quoted_net_25": None,
            "quoted_net_50": None,
            "quoted_net_100": None,
        }
        if r["entry_et"] is None or r["exit_et"] is None:
            record["status"] = "unknown_proxy_execution"
            rows.append(record)
            continue
        entry, ewhy = quotes.get(
            (r["day"], r["ticker"], r["entry_et"]), (None, "quote_not_acquired")
        )
        exit_day = record["exit_day"]
        exit_q, xwhy = quotes.get(
            (exit_day, r["ticker"], r["exit_et"]), (None, "quote_not_acquired")
        )
        if entry is None or exit_q is None:
            record["status"] = f"entry:{ewhy};exit:{xwhy}"
            rows.append(record)
            continue
        quantity = int(args.order_budget // entry["ask"])
        factor = r.get("split_factor", 1.0)
        position_verified = r.get("action_verified", True) and factor is not None and factor > 0
        exit_qty = quantity / factor if position_verified else None
        record.update(
            {
                "entry_ask": entry["ask"],
                "exit_bid": exit_q["bid"],
                "entry_spread_bps": entry["spread_bps"],
                "exit_spread_bps": exit_q["spread_bps"],
                "entry_quote_age_s": entry["age_s"],
                "exit_quote_age_s": exit_q["age_s"],
                "quantity": quantity,
                "entry_displayed_shares": entry["ask_shares"],
                "exit_displayed_shares": exit_q["bid_shares"],
            }
        )
        if not position_verified:
            record["status"] = "unknown_position_action"
        elif "exit_quantity" in r and not np.isclose(float(r["exit_quantity"]), exit_qty):
            record["status"] = "unknown_partial_or_unexplained_position"
        elif quantity < 1 or quantity > entry["ask_shares"] or exit_qty > exit_q["bid_shares"]:
            record["status"] = "unknown_top_of_book_capacity"
        else:
            record["status"] = "quoted_capacity_supported_not_fill_guaranteed"
            for residual in (0, 25, 50, 100):
                side = residual / 20_000
                paid_quantity = int(args.order_budget // (entry["ask"] * (1 + side)))
                record[f"quantity_{residual}"] = paid_quantity
                paid_exit_qty = paid_quantity / factor
                pnl = paid_exit_qty * exit_q["bid"] * (1 - side) - paid_quantity * entry["ask"] * (
                    1 + side
                )
                record[f"quoted_net_{residual}"] = pnl / args.order_budget
        rows.append(record)
    args.out.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, infer_schema_length=None).write_parquet(args.out / "rows.parquet")
    valid = [r for r in rows if r["quoted_net_0"] is not None]
    summary = {
        "trades_input": str(args.trades),
        "trades": len(rows),
        "quote_supported_pairs": len(valid),
        "unknown_pairs": len(rows) - len(valid),
        "latency_ms": args.latency_ms,
        "max_quote_age_s": args.max_age_s,
        "order_budget": args.order_budget,
        "status_counts": dict(Counter(r["status"] for r in rows)),
        "covered_mean_net": {
            str(c): float(np.mean([r[f"quoted_net_{c}"] for r in valid])) if valid else None
            for c in (0, 25, 50, 100)
        },
        "whole_portfolio_certified": False,
        "quote_is_not_exchange_fill": True,
        "unit_source": "https://docs.alpaca.markets/us/v1.1/changelog/marketdata-bid-and-ask-size-display-change",
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
