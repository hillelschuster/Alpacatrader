#!/usr/bin/env python3
"""Quote-priced learned-score basket survival experiment.

Fixed grid: decision ET 10:00/10:30/11:00, N=2/3, cut at 5/10/15m;
score cutoff 0.030. One $1,000 ticket per name, depth capped at 50% of the smaller
displayed bid/ask size, and $3,000 max gross. A completed checkpoint score below the
cutoff sells at the next +250ms quote; survivors sell at T+60. Historical quote replay,
not broker fill evidence.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import alpha_adaptive_depth_sizing as depth
from alpha_open_learned import feature_matrix
from alpha_open_sim import causal_liquidity

ROOT = Path(__file__).resolve().parents[2]
CALENDAR = ROOT / "factory/artifacts/basket/sip/phase2_session_calendar.json"
OUT = Path.home() / "alpha-data/open-search-v1/basket_reborn"
TIMES = (600, 630, 660)
SIZES = (2, 3)
CUTS = (5, 10, 15)
COSTS = (0, 25, 50, 100)
CUT_SCORE = 0.030
CAP_USD = 1000.0
DEPTH_FRACTION = 0.5


def score_day(day: str, model: dict) -> pl.DataFrame:
    panel = depth.day_context(pl.read_parquet(depth.PANEL_DAYS / f"{day}.parquet"))
    candidates = panel.filter(causal_liquidity(panel))
    if not candidates.height:
        return pl.DataFrame(schema={"ticker": pl.String, "t": pl.Int64, "pred": pl.Float64})
    pred = np.asarray(model[depth.HEAD]["lgbm"].predict(feature_matrix(candidates)), dtype=float)
    return candidates.select("ticker", "t").with_columns(pl.Series("pred", pred))


def trade(day: str, ticker: str, score: float, entry_minute: int, exit_minute: int,
          session_end: int, streams: dict) -> dict:
    result = {"day": day, "ticker": ticker, "score": score, "entry_minute": entry_minute,
              "exit_minute": exit_minute, "status": "unknown", "qty": 0}
    sq = streams.get(ticker)
    if sq is None:
        result["status"] = "quote_not_acquired"
        return result
    intent = depth.minute_us(day, entry_minute)
    facts = depth.intent_facts(sq, intent)
    if facts["intent_reason"] != "eligible":
        result["status"] = facts["intent_reason"]
        return result
    depth_cap = int(math.floor(DEPTH_FRACTION * min(
        float(facts["intent_ask_shares"]), float(facts["intent_bid_shares"]))))
    qty = min(int(facts["qty_capital"]), depth_cap)
    if qty < 1:
        result["status"] = "no_order_zero_depth"
        return result
    result["qty"] = qty
    result["limit_price"] = float(facts["limit_price"])
    eq, status, _ = depth.price_entry_ioc(sq, depth.arrival_us(intent),
                                           result["limit_price"], qty)
    if eq is None:
        result["status"] = "no_match_at_l1" if status == "no_match_at_l1" else "unknown_entry"
        return result
    session_end_us = depth.minute_us(day, session_end)
    due = depth.minute_us(day, min(exit_minute, session_end))
    submit, submit_status = depth.submission_clock(sq, due, session_end_us)
    result["exit_submit_status"] = submit_status
    if submit is None:
        result["status"] = "unknown_exit"
        return result
    xq, xstatus, _ = depth.priced_leg(sq, depth.arrival_us(submit), session_end_us)
    if xq is None or float(xq["bid_shares"]) < qty:
        result["status"] = "unknown_exit"
        return result
    result.update({"status": "conditional_fill", "entry_ask": float(eq["ask"]),
                   "exit_bid": float(xq["bid"]), "exit_quote_status": xstatus})
    for cost in COSTS:
        result[f"net_usd_{cost}"] = depth.net_usd(
            qty, result["exit_bid"], result["entry_ask"], cost
        )
    return result

def summarize(rows: list[dict], block: str, n_days: int) -> dict:
    known = [r for r in rows if r["status"] == "conditional_fill"]
    daily = defaultdict(float)
    for r in known:
        daily[r["day"]] += float(r["net_usd_25"])
    vals = [float(r["net_usd_25"]) for r in known]
    total = sum(vals)
    months = defaultdict(float)
    for day, pnl in daily.items():
        months[day[:7]] += pnl
    span = 361 if block == "validation" else 481
    return {"block": block, "sessions": n_days, "fills": len(known),
            "unknown": sum(r["status"].startswith("unknown") or r["status"] == "quote_not_acquired"
                           for r in rows),
            "median_fill_usd_25": float(np.median(vals)) if vals else None,
            "top5_share_usd_25": (
                (sum(sorted(vals, reverse=True)[:5]) / total) if total > 0 else None
            ),
            "positive_months_usd_25": sum(v > 0 for v in months.values()),
            "known_usd_25_per_session": total / n_days if n_days else None,
            "known_usd_25_per_calendar_day_span": total / span,
            **{f"known_usd_{cost}": sum(float(r[f"net_usd_{cost}"]) for r in known)
               for cost in COSTS}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--days", nargs="*")
    args = parser.parse_args()
    (OUT / "daily").mkdir(parents=True, exist_ok=True)
    model, model_report = depth.load_stored_model()
    calendar = json.loads(CALENDAR.read_text())
    all_days = sorted(p.stem for p in depth.PANEL_DAYS.glob("*.parquet")
                      if depth.allowed(p.stem) and
                      (p.stem.startswith("2023-") or "2025-02-01" <= p.stem <= "2026-05-31"))
    days = [d for d in (args.days or all_days) if d in all_days]
    for day in days:
        dest = OUT / "daily" / f"{day}.json"
        if args.resume and dest.exists():
            try:
                cached = json.loads(dest.read_text())
            except (OSError, json.JSONDecodeError):
                cached = {}
            if cached.get("rows"):
                continue
        states = score_day(day, model)
        symbols = set(states["ticker"].to_list()) if states.height else set()
        quote_frame = depth.quote_day_frame(depth.DATA_ROOT, day, symbols, depth.SUPPLEMENT_ROOTS)
        streams = depth.symbol_quotes(quote_frame, day)
        end = int(calendar["session_end_by_day"][day])
        rows = []
        for clock in TIMES:
            board = states.filter(pl.col("t") == clock).sort(
                ["pred", "ticker"], descending=[True, False]).head(3).to_dicts()
            for cut in CUTS:
                for n in (1, *SIZES):
                    for candidate in board[:n]:
                        ticker = candidate["ticker"]
                        cp = states.filter((pl.col("ticker") == ticker) &
                                           (pl.col("t") == clock + cut))
                        cut_fires = bool(cp.height and float(cp["pred"][0]) < CUT_SCORE)
                        exit_minute = min(clock + 1 + (cut if cut_fires else 60), end)
                        row = trade(day, ticker, float(candidate["pred"]), clock + 1,
                                    exit_minute, end, streams)
                        row.update({"decision_et": clock, "basket_n": n, "cut_min": cut,
                                    "cut_fired": cut_fires,
                                    "checkpoint_pred": float(cp["pred"][0]) if cp.height else None})
                        rows.append(row)
        tmp = dest.with_suffix(".tmp")
        tmp.write_text(json.dumps({"day": day, "rows": rows}, separators=(",", ":")))
        tmp.replace(dest)
        print(f"{day}: {len(rows)} member outcomes", flush=True)

    # Rebuild result rows only from the exact requested dates. Day parts are atomic/resumable.
    grid = defaultdict(list)
    for day in days:
        payload = json.loads((OUT / "daily" / f"{day}.json").read_text())
        for row in payload["rows"]:
            grid[("validation" if day.startswith("2023-") else "late", row["decision_et"],
                  row["basket_n"], row["cut_min"])].append(row)
    session_counts = {"validation": sum(d.startswith("2023-") for d in days),
                      "late": sum(d.startswith("2025-") or d.startswith("2026-") for d in days)}
    summaries = [{"block": key[0], "decision_et": key[1], "basket_n": key[2],
                  "cut_min": key[3], **summarize(rows, key[0], session_counts[key[0]])}
                 for key, rows in sorted(grid.items())]
    by_key = {(r["block"], r["decision_et"], r["cut_min"], r["basket_n"]): r
              for r in summaries}
    for result in summaries:
        single = by_key.get((result["block"], result["decision_et"], result["cut_min"], 1))
        pnl = result["known_usd_25"]
        single_pnl = single["known_usd_25"] if single else None
        result["delta_known_usd_25_vs_single"] = (
            pnl - single_pnl if single_pnl is not None else None)
        result["delta_usd_25_per_session_vs_single"] = (
            result["delta_known_usd_25_vs_single"] / result["sessions"]
            if result["delta_known_usd_25_vs_single"] is not None and result["sessions"] else None)
    (OUT / "contract.json").write_text(json.dumps({
        "grid": {"decision_et": TIMES, "basket_size": [1, *SIZES], "cut_min": CUTS,
                 "cut_score": CUT_SCORE, "depth_fraction": DEPTH_FRACTION,
                 "ticket_cap_usd": CAP_USD, "cost_bps_round_trip": COSTS},
        "model": model_report,
        "blocks": {"validation": "2023-01-01..2023-12-31; selection only",
                   "late": "2025-02..2026-05; previously explored, transparency only"},
        "no_protected_dates": True, "no_live_orders": True}, indent=2))
    (OUT / "results.json").write_text(json.dumps({"summaries": summaries}, indent=2))


if __name__ == "__main__":
    main()
