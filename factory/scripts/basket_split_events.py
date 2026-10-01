#!/usr/bin/env python3
"""HARVEST01 — corporate-actions split events for causal prev-close normalization.

Fetches Alpaca corporate actions (forward_split, reverse_split) month by month over a
date span and writes one parquet of events:

    symbol, action_type, new_rate, old_rate, ex_date, process_date, id

Why: RAW minute bars make a reverse split look like a giant overnight "gainer" and a
forward split like a giant "loser". Displayed change (e.g. TradingView-equivalent) is
computed against a split-adjusted previous close. The split's ex_date/rates are public
event metadata, not outcome data, so adjusting prev_close with them keeps the ranking
causal. Consumer rule: prev_adj = prev_close * old_rate / new_rate for events with
prev_session_date < ex_date <= day.

Usage:
    .venv/bin/python factory/scripts/basket_split_events.py --start 2021-01-01 --end 2026-06-01
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = Path("/home/hillel/projects/Alpacatrader/data/harvest01/base/splits.parquet")


def month_starts(start: str, end: str) -> list[tuple[str, str]]:
    y0, m0 = int(start[:4]), int(start[5:7])
    y1, m1 = int(end[:4]), int(end[5:7])
    out = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        first = date(y, m, 1)
        nxt = date(y + (m == 12), (m % 12) + 1, 1)
        out.append((first.isoformat(), min(nxt.isoformat(), end)))
        y, m = y + (m == 12), (m % 12) + 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-06-01")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(Path("/home/hillel/projects/Alpacatrader/.env"))
    load_dotenv(ROOT / ".env")
    from alpaca.data.historical.corporate_actions import CorporateActionsClient
    from alpaca.data.requests import CorporateActionsRequest

    client = CorporateActionsClient(os.environ["ALPACA_API_KEY"],
                                    os.environ["ALPACA_SECRET_KEY"])
    rows = []
    for s, e in month_starts(args.start, args.end):
        for attempt in range(4):
            try:
                res = client.get_corporate_actions(CorporateActionsRequest(
                    types=["forward_split", "reverse_split"], start=s, end=e, limit=10000))
                d = res.data if hasattr(res, "data") else res
                for key in ("forward_splits", "reverse_splits"):
                    for item in (d.get(key) or []):
                        rows.append({
                            "symbol": item.symbol, "action_type": key,
                            "new_rate": float(item.new_rate), "old_rate": float(item.old_rate),
                            "ex_date": str(item.ex_date), "process_date": str(item.process_date),
                            "id": str(item.id),
                        })
                break
            except Exception as ex:
                if attempt == 3:
                    print(f"{s}..{e} FAILED {str(ex)[:200]}")
                time.sleep(2 * (attempt + 1))
        time.sleep(0.2)

    df = pl.DataFrame(rows) if rows else pl.DataFrame(schema={
        "symbol": pl.Utf8, "action_type": pl.Utf8, "new_rate": pl.Float64,
        "old_rate": pl.Float64, "ex_date": pl.Utf8, "process_date": pl.Utf8, "id": pl.Utf8})
    df = df.unique(subset=["id"]).sort(["ex_date", "symbol"])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out)
    man = {"file": out.name, "rows": int(df.height),
           "by_type": df.group_by("action_type").len().to_dicts() if df.height else [],
           "span": [args.start, args.end]}
    (out.parent / (out.name + ".manifest.json")).write_text(json.dumps(man, indent=1))
    print(json.dumps(man, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
