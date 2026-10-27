#!/usr/bin/env python3
"""Full funded-book quote microstructure replay for adaptive-depth thr300bps|p50.

Every cell runs the lane's imported replay_day on the same scored state rows and quote
streams. Variant pricing hooks are monkeypatched only within this process; the book,
quantity, clocks, quote parser and reservation/cooldown logic remain the lane's own.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

import alpha_adaptive_depth_sizing as depth

ROOT = Path.home() / "alpha-data/open-search-v1"
SOURCE = ROOT / "adaptive_depth_sizing"
OUTPUT = ROOT / "entry_microstructure"
ARM = depth.View(0.03, 0.50)
COSTS = (0, 25, 50, 100)
CACHE_SCHEMA = 4
ORIGINAL_ELIGIBILITY = depth.arm_eligibility
ORIGINAL_ENTRY = depth.price_entry_ioc
ORIGINAL_PRICED = depth.priced_leg
CURRENT = "baseline"


def tick(price: float) -> float:
    return 0.01 if price >= 1.0 else 0.0001


def arm_eligibility(facts, arm, pred):
    if facts.get("intent_reason") == "eligible" and facts.get("intent_ask") is not None:
        name = CURRENT
        age_caps = {"age_le_0.5s": 0.5, "age_le_1s": 1.0, "age_le_2s": 2.0}
        spread_caps = {f"spread_le_{n}bps": n for n in (50, 100, 200, 400)}
        if name in age_caps and facts["intent_age_s"] > age_caps[name]:
            return "intent_not_firm_fresh", 0
        if name in spread_caps and (
            facts["intent_spread_bps"] is None or facts["intent_spread_bps"] > spread_caps[name]
        ):
            return "spread_above_prediction", 0
        ask = float(facts["intent_ask"])
        if name == "entry_ask":
            limit = ask
        elif name == "entry_ask_plus_1tick":
            limit = ask + tick(ask)
        elif name == "entry_ask_x1.005":
            limit = ask * 1.005
        elif name == "entry_ask_x1.01":
            limit = ask * 1.01
        else:
            limit = None
        if limit is not None:
            facts["limit_price"] = limit
            facts["qty_capital"] = depth.capital_quantity(limit)
    return ORIGINAL_ELIGIBILITY(facts, arm, pred)


def price_entry(sq, arrival, limit, quantity):
    if CURRENT != "wait_mid_5s":
        return ORIGINAL_ENTRY(sq, arrival, limit, quantity)
    ttl = arrival + 5_000_000
    begin = int(__import__("numpy").searchsorted(sq.stamps, arrival, side="left"))
    stop = int(__import__("numpy").searchsorted(sq.stamps, ttl, side="right"))
    for ix in range(begin, stop):
        if not sq.eligible[ix] or sq.ask_shares[ix] < quantity:
            continue
        bid, ask = float(sq.bid[ix]), float(sq.ask[ix])
        if ask <= (bid + ask) / 2 + 1e-12:
            q = sq.quote(ix, int(sq.stamps[ix]))
            return q, "conditional_ioc_fill", arrival
    return ORIGINAL_ENTRY(sq, ttl, math.inf, quantity)


def priced_leg(sq, arrival, end):
    q, status, clock = ORIGINAL_PRICED(sq, arrival, end)
    if q is None or CURRENT not in ("exit_bid", "exit_bid_minus_tick", "exit_bid_x0.995"):
        return q, status, clock
    bid = float(q["bid"])
    limit = bid
    if CURRENT == "exit_bid_minus_tick":
        limit = max(0.0, bid - tick(bid))
    elif CURRENT == "exit_bid_x0.995":
        limit = bid * 0.995
    # A marketable sell limit executes at the displayed bid when bid >= limit.
    if bid + 1e-12 < limit:
        return None, "unknown_exit_limit_not_marketable", None
    return q, status, clock


def replay(variant, day, states, streams):
    global CURRENT
    CURRENT = variant
    hooks = {
        "arm_eligibility": arm_eligibility,
        "price_entry_ioc": price_entry,
        "priced_leg": priced_leg,
    }
    originals = {name: getattr(depth, name) for name in hooks}
    try:
        for name, hook in hooks.items():
            setattr(depth, name, hook)
        return depth.replay_day(day, states, streams, ARM)
    finally:
        for name, hook in originals.items():
            setattr(depth, name, hook)
        CURRENT = "baseline"


def outcomes(trades):
    attempts = len(trades)
    known = [r for r in trades if r["fill_status"] == "conditional_fill"]
    unknown = [r for r in trades if "unknown" in str(r["fill_status"])]
    by_rung = {}
    import statistics
    for c in COSTS:
        vals = [float(r[f"net_usd_{c}"]) for r in known]
        by_rung[str(c)] = {"known_usd": sum(vals),
                           "median_fill_usd": statistics.median(vals) if vals else None}
    vals25 = [float(r["net_usd_25"]) for r in known]
    return {"attempts": attempts, "known_fills": len(known), "unknown_fills": len(unknown),
            "unknown_share": len(unknown) / attempts if attempts else 0.0,
            "top5_share_25": (
                (sum(sorted(vals25, reverse=True)[:5]) / sum(vals25))
                if vals25 and sum(vals25) else None
            ),
            "costs": by_rung,
            "unknown_statuses": dict(
                Counter(f"{r.get('entry_status')}|{r.get('exit_status')}" for r in unknown)
            ),
            "trades": trades}


def day_files():
    states = SOURCE / "states"
    days = sorted(p.stem for p in states.glob("*.parquet"))
    return {"validation": [d for d in days if d.startswith("2023-")],
            "confirmation": [d for d in days if "2025-02-01" <= d <= "2026-05-31"]}


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    variants = [
        "baseline", "entry_ask", "entry_ask_plus_1tick", "entry_ask_x1.005", "entry_ask_x1.01",
        *[f"spread_le_{n}bps" for n in (50, 100, 200, 400)],
        "age_le_0.5s", "age_le_1s", "age_le_2s", "exit_bid", "exit_bid_minus_tick",
        "exit_bid_x0.995", "wait_mid_5s",
    ]
    periods = day_files()
    result = {
        "study": "entry_microstructure", "arm": ARM.key,
        "days": {k: len(v) for k, v in periods.items()},
        "costs_bps": list(COSTS),
        "basis": "full funded replay per variant; known contribution excludes UNKNOWNs",
        "variant_results": {
            b: {v: {"daily": [], "trades": []} for v in variants} for b in periods
        },
        "unknown_causes": {},
    }
    for block, days in periods.items():
        for n, day in enumerate(days, 1):
            cache = OUTPUT / "days" / block / f"{day}.json"
            if cache.exists():
                saved = json.loads(cache.read_text())
                if saved.get("schema") == CACHE_SCHEMA:
                    saved = saved["payload"]
                    for variant in variants:
                        result["variant_results"][block][variant]["daily"].append(
                            saved[variant]["daily"]
                        )
                        result["variant_results"][block][variant]["trades"].extend(
                            saved[variant]["trades"]
                        )
                    print(f"{block} {n}/{len(days)} {day} resumed", flush=True)
                    continue
            states = depth.read_day_states(SOURCE, day)
            symbols = set(states["ticker"].to_list()) if states.height else set()
            qf = (
                depth.quote_day_frame(depth.DATA_ROOT, day, symbols, depth.SUPPLEMENT_ROOTS)
                if symbols else None
            )
            streams = depth.symbol_quotes(qf, day)
            day_result = {}
            for variant in variants:
                daily, trades = replay(variant, day, states, streams)
                day_result[variant] = {
                    "daily": {
                        "day": day,
                        **{
                            k: daily[k]
                            for k in (
                                "attempts", "fills", "known_fills", "unknown_fills",
                                "no_match_fills", *(f"known_usd_{c}" for c in COSTS),
                            )
                        },
                    },
                    "trades": trades,
                }
                dest = result["variant_results"][block][variant]
                dest["daily"].append(day_result[variant]["daily"])
                dest["trades"].extend(trades)
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp_cache = cache.with_suffix(".json.tmp")
            tmp_cache.write_text(
                json.dumps({"schema": CACHE_SCHEMA, "payload": day_result}, default=str) + "\n"
            )
            tmp_cache.replace(cache)
            print(f"{block} {n}/{len(days)} {day}", flush=True)
            save_partial(result)
        for variant in variants:
            raw = result["variant_results"][block][variant]
            summary = outcomes(raw.pop("trades"))
            span = 361 if block == "validation" else 481
            for cost in COSTS:
                total = summary["costs"][str(cost)]["known_usd"]
                summary["costs"][str(cost)]["per_session"] = total / len(days)
                summary["costs"][str(cost)]["per_calendar_day"] = total / span
            raw["summary"] = summary
        save_partial(result)
    causes = Counter()
    late_base = result["variant_results"]["confirmation"]["baseline"]["summary"]["trades"]
    for r in late_base:
        if "unknown" not in str(r["fill_status"]):
            continue
        causes[cause_of(r)] += 1
    result["unknown_causes"] = dict(causes)
    base_unknown = {
        (r["day"], r["ticker"], r["intent_us"]): r
        for r in result["variant_results"]["confirmation"]["baseline"]["summary"]["trades"]
        if "unknown" in str(r.get("fill_status"))
    }
    result["unknown_conversions"] = {}
    for variant in variants:
        rows = result["variant_results"]["confirmation"][variant]["summary"]["trades"]
        converted = [r for r in rows if (r["day"], r["ticker"], r["intent_us"]) in base_unknown
                     and r.get("fill_status") == "conditional_fill"]
        result["unknown_conversions"][variant] = {
            "converted_known_fills": len(converted),
            "known_usd_25": sum(float(r["net_usd_25"]) for r in converted),
            "known_usd_50": sum(float(r["net_usd_50"]) for r in converted),
            "causes": dict(Counter(
                cause_of(base_unknown[(r["day"], r["ticker"], r["intent_us"])])
                for r in converted
            )),
        }
    val_variant = max(
        variants,
        key=lambda variant: (
            result["variant_results"]["validation"][variant]
            ["summary"]["costs"]["25"]["per_session"]
        ),
    )
    result["validation_choice"] = {
        "variant": val_variant,
        "validation_usd_per_session_25": (
            result["variant_results"]["validation"][val_variant]["summary"]["costs"]["25"][
                "per_session"
            ]
        ),
        "late_usd_per_session_25": (
            result["variant_results"]["confirmation"][val_variant]["summary"]["costs"]["25"][
                "per_session"
            ]
        ),
        "selection_basis": (
            "highest validation known contribution per panel session at 25bps; "
            "descriptive, no unknown-loss penalty"
        ),
    }
    result["baseline_check"] = {
        block: {
            "known_usd_per_session_25": (
                result["variant_results"][block]["baseline"]["summary"]["costs"]["25"][
                    "per_session"
                ]
            ),
            "known_usd_per_session_50": (
                result["variant_results"][block]["baseline"]["summary"]["costs"]["50"][
                    "per_session"
                ]
            ),
            "known_fills": result["variant_results"][block]["baseline"]["summary"]["known_fills"],
            "unknown_fills": (
                result["variant_results"][block]["baseline"]["summary"]["unknown_fills"]
            ),
        }
        for block in periods
    }
    save_partial(result)


def cause_of(trade):
    entry = str(trade.get("entry_status") or "").lower()
    exit_status = str(trade.get("exit_status") or "").lower()
    if str(trade.get("fill_status", "")).startswith("unknown_entry"):
        if "stale" in entry:
            return "entry stale"
        if "partial_depth" in entry:
            return "entry no-depth"
        return "halt/gap"
    if "stale" in exit_status:
        return "exit stale"
    if "partial_depth" in exit_status:
        return "exit no-depth"
    return "halt/gap"


def save_partial(result):
    path = OUTPUT / "results.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, indent=1, default=str) + "\n")
    tmp.replace(path)


if __name__ == "__main__":
    main()
