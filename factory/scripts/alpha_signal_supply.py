#!/usr/bin/env python3
"""Quote-backed signal-supply variants for the adaptive depth lane.

Uses the frozen h60/p50/0.030 states and the producer's actual IOC ask / displayed-depth /
60-minute bid execution functions. Selection is on 2023 validation at 25 residual bps;
2025-02..2026-05 is reported only as previously explored late evidence.
"""
from __future__ import annotations

import heapq
import json
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import alpha_adaptive_depth_sizing as depth  # noqa: E402

ROOT = depth.PANEL_ROOT / "adaptive_depth_sizing"
OUTPUT = depth.PANEL_ROOT / "signal_supply"
ARM = depth.View(0.030, 0.50)
COSTS = (0, 25, 50, 100)


@dataclass(frozen=True)
class Variant:
    name: str
    slots: int = 3
    cooldown_min: int = 15
    attempts: int = 3


VARIANTS = (
    Variant("baseline"),
    Variant("cooldown_10m", cooldown_min=10),
    Variant("repeat_5m", cooldown_min=5, attempts=20),
    Variant("repeat_no_cooldown", cooldown_min=0, attempts=20),
    Variant("attempts_4", attempts=4),
    Variant("attempts_5", attempts=5),
    Variant("attempts_10", attempts=10),
    Variant("slots_4", slots=4),
    Variant("slots_5", slots=5),
    Variant("slots_6", slots=6),
    Variant("slots_8", slots=8),
    Variant("open_supply", slots=8, cooldown_min=0, attempts=20),
)


def replay(
    day: str, states: pl.DataFrame, streams: dict, variant: Variant
) -> tuple[dict, list[dict]]:
    candidates = states.filter(pl.col("pred_60") >= ARM.threshold).sort(
        ["entry_minute", "pred_60", "ticker"], descending=[False, True, False]
    )
    session_end = int(states["session_end"][0]) if states.height else 960
    session_end_us = depth.minute_us(day, session_end)
    by_minute: dict[int, list[dict]] = {}
    for row in candidates.iter_rows(named=True):
        by_minute.setdefault(int(row["entry_minute"]), []).append(row)
    counts = {"signals": candidates.height, "intents_funded": 0, "attempts": 0,
              "fills": 0, "known_fills": 0, "unknown_fills": 0, "no_match_fills": 0,
              "overlap_skips": 0, "cooldown_skips": 0, "max_attempt_skips": 0,
              "cash_or_slot_skips": 0, "slot_full_skips": 0, "cash_skips": 0}
    for key in depth.SKIP_REASONS:
        counts[key] = 0
    cash = variant.slots * depth.ORDER_BUDGET
    active: set[str] = set()
    queue: list[tuple[int, int, str, float]] = []
    attempt_count: dict[str, int] = {}
    cooldown_until: dict[str, int] = {}
    seq = 0
    trades = []
    for minute in sorted(by_minute):
        intent_us = depth.minute_us(day, minute)
        while queue and queue[0][0] <= intent_us:
            _, _, symbol, proceeds = heapq.heappop(queue)
            active.discard(symbol)
            cash += proceeds
        funded = []
        for row in by_minute[minute]:
            symbol = row["ticker"]
            reason, qty = depth.arm_eligibility(row, ARM, float(row["pred_60"]))
            if reason != "eligible":
                counts[reason] += 1
            elif symbol in active:
                counts["overlap_skips"] += 1
            elif intent_us < cooldown_until.get(symbol, -(1 << 62)):
                counts["cooldown_skips"] += 1
            elif attempt_count.get(symbol, 0) >= variant.attempts:
                counts["max_attempt_skips"] += 1
            else:
                reserved = depth.reserved_usd(qty, float(row["limit_price"]))
                if len(active) >= variant.slots:
                    counts["cash_or_slot_skips"] += 1
                    counts["slot_full_skips"] += 1
                elif cash + 1e-9 < reserved:
                    counts["cash_or_slot_skips"] += 1
                    counts["cash_skips"] += 1
                else:
                    attempt_count[symbol] = attempt_count.get(symbol, 0) + 1
                    cash -= reserved
                    active.add(symbol)
                    counts["intents_funded"] += 1
                    counts["attempts"] += 1
                    funded.append((row, qty, reserved))
        for row, qty, reserved in funded:
            trade = depth.resolve_trade(day, row, ARM, qty, streams[row["ticker"]],
                                        session_end, session_end_us)
            trade["variant"] = variant.name
            trade["attempt_index"] = attempt_count[row["ticker"]]
            status = trade["fill_status"]
            release = int(trade["actual_exit_us"])
            if status == "no_match_at_l1_unfilled_cash":
                counts["no_match_fills"] += 1
            else:
                counts["fills"] += 1
                if status == "conditional_fill":
                    counts["known_fills"] += 1
                else:
                    counts["unknown_fills"] += 1
                cooldown_until[row["ticker"]] = release + variant.cooldown_min * 60_000_000
            heapq.heappush(queue, (release, seq, row["ticker"], reserved))
            seq += 1
            trades.append(trade)
    counts["qualified"] = states.height
    return counts, trades


def summarize(
    days: list[str], by_day: dict[str, dict], trades: list[dict], variant: Variant
) -> dict:
    days_sorted = sorted(days)
    span = (
        date.fromisoformat(days_sorted[-1]) - date.fromisoformat(days_sorted[0])
    ).days + 1
    out = {
        "name": variant.name,
        "slots": variant.slots,
        "cooldown_min": variant.cooldown_min,
        "attempt_cap": variant.attempts,
        "sessions": len(days),
        "calendar_days": span,
        "nominal_book_usd": variant.slots * depth.ORDER_BUDGET,
    }
    for rung in COSTS:
        values = [
            float(t[f"net_usd_{rung}"])
            for t in trades
            if t["fill_status"] == "conditional_fill" and t[f"net_usd_{rung}"] is not None
        ]
        total = sum(values)
        out[f"known_usd_{rung}"] = total
        out[f"usd_per_session_{rung}"] = total / len(days) if days else 0.0
        out[f"usd_per_calendar_day_{rung}"] = total / span if span else 0.0
        out[f"usd_per_known_fill_{rung}"] = total / len(values) if values else None
        out[f"median_fill_{rung}"] = float(np.median(values)) if values else None
        out[f"top5_share_{rung}"] = (
            sum(sorted(values, reverse=True)[:5]) / total if total else None
        )
    out["known_fills"] = sum(int(x["known_fills"]) for x in by_day.values())
    out["fills"] = sum(int(x["fills"]) for x in by_day.values())
    out["unknown_fills"] = sum(int(x["unknown_fills"]) for x in by_day.values())
    out["attempts"] = sum(int(x["attempts"]) for x in by_day.values())
    months = {}
    for day, _values in by_day.items():
        months.setdefault(day[:7], 0.0)
        months[day[:7]] += sum(
            float(t["net_usd_25"])
            for t in trades
            if t["day"] == day and t["fill_status"] == "conditional_fill"
        )
    out["months_positive_25"] = sum(v > 0 for v in months.values())
    out["months_total"] = len(months)
    out["monthly_known_usd_25"] = months
    return out


def days_for(block: str) -> list[str]:
    return depth.block_days(None, block)


def write_part(
    daily_path: Path, trades_path: Path, daily: dict, trades: list[dict]
) -> None:
    daily_tmp = daily_path.with_suffix(f".json.tmp{os.getpid()}")
    trades_tmp = trades_path.with_suffix(f".parquet.tmp{os.getpid()}")
    if trades:
        pl.DataFrame(trades).write_parquet(trades_tmp)
        os.replace(trades_tmp, trades_path)
    elif trades_path.exists():
        trades_path.unlink()
    daily_tmp.write_text(json.dumps(daily, sort_keys=True) + "\n")
    os.replace(daily_tmp, daily_path)


def run() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    all_results = {
        "study": "alpha_signal_supply",
        "input": str(ROOT),
        "variants": {},
        "funnel": {},
    }
    for block in ("validation", "confirmation"):
        days = days_for(block)
        by_variant = {v.name: {} for v in VARIANTS}
        trades_variant = {v.name: [] for v in VARIANTS}
        for i, day in enumerate(days, 1):
            states = pl.read_parquet(ROOT / "states" / f"{day}.parquet")
            tickers = set(states["ticker"].to_list()) if states.height else set()
            streams = None
            for variant in VARIANTS:
                part = OUTPUT / "parts" / variant.name / block
                daily_path = part / f"{day}.daily.json"
                trades_path = part / f"{day}.trades.parquet"
                config = {
                    "slots": variant.slots,
                    "cooldown_min": variant.cooldown_min,
                    "attempt_cap": variant.attempts,
                }
                daily = None
                trades = []
                if daily_path.exists():
                    try:
                        cached = json.loads(daily_path.read_text())
                        if cached.get("variant_config") == config:
                            daily = cached
                            if trades_path.exists():
                                trades = pl.read_parquet(trades_path).to_dicts()
                    except (OSError, json.JSONDecodeError, pl.exceptions.PolarsError):
                        daily = None
                if daily is None:
                    if streams is None:
                        quotes = (
                            depth.quote_day_frame(
                                depth.DATA_ROOT, day, tickers, depth.SUPPLEMENT_ROOTS
                            )
                            if tickers else None
                        )
                        streams = depth.symbol_quotes(quotes, day)
                    daily, trades = replay(day, states, streams, variant)
                    daily["variant_config"] = config
                    part.mkdir(parents=True, exist_ok=True)
                    write_part(daily_path, trades_path, daily, trades)
                by_variant[variant.name][day] = daily
                trades_variant[variant.name].extend(trades)
            if i % 25 == 0 or i == len(days):
                print(f"[{block}] {i}/{len(days)} days", flush=True)
        results_block = {}
        for variant in VARIANTS:
            summary = summarize(
                days, by_variant[variant.name], trades_variant[variant.name], variant
            )
            results_block[variant.name] = summary
            if variant.name == "baseline":
                base = by_variant["baseline"]
                all_results["funnel"][block] = {
                    "qualified_state_rows": sum(x["qualified"] for x in base.values()),
                    "threshold_signals": sum(x["signals"] for x in base.values()),
                    "execution_eligible": sum(
                        x["signals"]
                        - sum(
                            x[k]
                            for k in depth.SKIP_REASONS
                            if k not in {
                                "overlap_skips",
                                "cooldown_skips",
                                "max_attempt_skips",
                                "cash_or_slot_skips",
                            }
                        )
                        for x in base.values()
                    ),
                    "funded": sum(x["intents_funded"] for x in base.values()),
                    "filled_or_unknown": sum(x["fills"] for x in base.values()),
                    "known": sum(x["known_fills"] for x in base.values()),
                    "unknown": sum(x["unknown_fills"] for x in base.values()),
                    "no_match": sum(x["no_match_fills"] for x in base.values()),
                    "skip_reasons": {
                        k: sum(x[k] for x in base.values())
                        for k in base[days[0]]
                        if k.endswith("skips") or k in depth.SKIP_REASONS
                    },
                }
        all_results["variants"][block] = results_block
    chosen = max(
        VARIANTS,
        key=lambda v: all_results["variants"]["validation"][v.name][
            "usd_per_calendar_day_25"
        ],
    )
    all_results["validation_selected"] = chosen.name
    all_results["selection"] = (
        "Maximum validation known dollars/calendar-day at 25 bps; late is descriptive"
    )
    (OUTPUT / "results.json").write_text(
        json.dumps(all_results, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    run()
