#!/usr/bin/env python3
"""Map adaptive-depth threshold/participation/latency/slot neighbourhoods."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fractions import Fraction

import alpha_adaptive_depth_sizing as depth

depth.PARTICIPATION_EXACT.update(
    {
        0.75: Fraction(3, 4),
        1.00: Fraction(1, 1),
    }
)

THRESHOLDS = (0.020, 0.025, 0.030, 0.035, 0.040, 0.050)
PARTICIPATIONS = (0.25, 0.50, 0.75, 1.00)
LATENCIES_MS = (250, 1000)
SLOTS = (3, 5)
COSTS = (0, 25, 50, 100)
OUTPUT = depth.PANEL_ROOT / "depth_plateau"


def arms():
    return [
        (depth.View(t, p), latency, slots)
        for t in THRESHOLDS
        for p in PARTICIPATIONS
        for latency in LATENCIES_MS
        for slots in SLOTS
    ]


def key(arm, latency, slots):
    return f"{arm.key}|a{latency}|s{slots}"


def replay_days(out: Path, block: str, days: list[str], resume: bool, roots: tuple[Path, ...]):
    root = out / "replay" / block
    root.mkdir(parents=True, exist_ok=True)
    views = arms()
    for ix, day in enumerate(days, 1):
        dpath, tpath = root / f"{day}.parquet", root / f"{day}.trades.parquet"
        cpath = root / f"{day}.cov.json"
        if resume and dpath.exists() and tpath.exists() and cpath.exists():
            continue
        states = depth.read_day_states(out, day)
        tickers = sorted(set(states["ticker"].to_list())) if states.height else []
        frame = (
            depth.quote_day_frame(depth.DATA_ROOT, day, set(tickers), roots)
            if tickers else None
        )
        streams = depth.symbol_quotes(frame, day)
        daily_rows, trades = [], []
        for view, latency, slots in views:
            depth.MAX_SLOTS = slots
            depth.BOOK = slots * depth.ORDER_BUDGET
            # The producer's baseline arrival is 250ms; add only the requested increment.
            original_arrival = depth.arrival_us
            depth.arrival_us = (
                lambda ts, delta=latency - 250, f=original_arrival: f(ts) + delta * 1000
            )
            daily, rows = depth.replay_day(day, states, streams, view)
            depth.arrival_us = original_arrival
            k = key(view, latency, slots)
            daily["arm_key"] = k
            daily_rows.append(daily)
            for row in rows:
                row["arm_key"] = k
                trades.append(row)
        pl.DataFrame(
            daily_rows, schema={c: depth.DAILY_TYPES[c] for c in depth.DAILY_COLUMNS}
        ).write_parquet(dpath)
        pl.DataFrame(
            trades, schema={c: depth.TRADE_TYPES[c] for c in depth.TRADE_COLUMNS}
        ).write_parquet(tpath)
        cpath.write_text(json.dumps({"day": day, "arms": len(views)}) + "\n")
        if ix % 25 == 0 or ix == len(days):
            print(f"[{block}] {ix}/{len(days)} {day}", flush=True)


def summarize(out: Path, block: str, days: list[str]) -> dict:
    files = sorted((out / "replay" / block).glob("*.parquet"))
    daily = pl.concat([pl.read_parquet(p) for p in files if not p.name.endswith(".trades.parquet")])
    trades = pl.concat(
        [pl.read_parquet(p) for p in sorted((out / "replay" / block).glob("*.trades.parquet"))]
    )
    caldays = depth.calendar_days_spanned(days)
    result = {}
    for view, latency, slots in arms():
        k = key(view, latency, slots)
        dr = daily.filter(pl.col("arm_key") == k)
        tr = trades.filter((pl.col("arm_key") == k) & (pl.col("fill_status") == "conditional_fill"))
        by_month = {}
        for row in dr.iter_rows(named=True):
            by_month.setdefault(row["day"][:7], []).append(float(row["known_usd_25"]))
        metrics = {}
        for cost in COSTS:
            total = float(dr[f"known_usd_{cost}"].sum())
            metrics[str(cost)] = {
                "usd_per_session": total / len(days),
                "usd_per_calendar_day": total / caldays,
            }
        fill_values = [float(x) for x in tr["net_usd_25"].to_list() if x is not None]
        top = sorted(fill_values, reverse=True)[:5]
        result[k] = {
            "threshold": view.threshold, "participation": view.participation,
            "latency_ms": latency, "slots": slots, "costs": metrics,
            "fills": len(fill_values),
            "median_fill_25bps": (
                float(pl.Series(fill_values).median()) if fill_values else None
            ),
            "top5_share_25bps": (
                sum(top) / sum(fill_values) if fill_values and sum(fill_values) else None
            ),
            "months_positive_25bps": sum(sum(v) > 0 for v in by_month.values()),
            "months": len(by_month),
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUTPUT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--days", nargs="+", default=None)
    args = parser.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    models, _ = depth.load_stored_model()
    roots = tuple(depth.SUPPLEMENT_ROOTS)
    valdays = depth.block_days(args.days, depth.VAL_BLOCK)
    if args.days is None and len(valdays) != depth.EXPECTED_DAYS[depth.VAL_BLOCK]:
        raise SystemExit(f"Expected full validation calendar, got {len(valdays)}")
    depth.score_block(out, models, valdays, args.resume, roots)
    replay_days(out, depth.VAL_BLOCK, valdays, args.resume, roots)
    validation = summarize(out, depth.VAL_BLOCK, valdays)
    freeze = {
        "validation": validation,
        "grid": {
            "thresholds": THRESHOLDS, "participations": PARTICIPATIONS,
            "latencies_ms": LATENCIES_MS, "slots": SLOTS, "costs_bps": COSTS,
        },
    }
    depth.write_json_atomic(out / "selection_freeze.json", freeze)
    latedays = depth.block_days(args.days, depth.LATE_BLOCK)
    if args.days is None and len(latedays) != depth.EXPECTED_DAYS[depth.LATE_BLOCK]:
        raise SystemExit(f"Expected full late calendar, got {len(latedays)}")
    depth.score_block(out, models, latedays, args.resume, roots)
    replay_days(out, depth.LATE_BLOCK, latedays, args.resume, roots)
    late = summarize(out, depth.LATE_BLOCK, latedays)
    ranked = sorted(
        validation,
        key=lambda k: (
            -validation[k]["costs"]["25"]["usd_per_calendar_day"],
            -validation[k]["fills"],
            k,
        ),
    )
    best = ranked[0]
    center = next(
        k for k in validation
        if validation[k]["threshold"] == .03 and validation[k]["participation"] == .5
        and validation[k]["latency_ms"] == 250 and validation[k]["slots"] == 3
    )
    def neighbors(k):
        x = validation[k]
        return [
            q for q, y in validation.items()
            if sum(
                (x[a] != y[a]) for a in ("threshold", "participation", "latency_ms", "slots")
            ) == 1
            and (
                x["threshold"] == y["threshold"]
                or abs(
                    THRESHOLDS.index(x["threshold"]) - THRESHOLDS.index(y["threshold"])
                ) == 1
            )
            and (
                x["participation"] == y["participation"]
                or abs(
                    PARTICIPATIONS.index(x["participation"])
                    - PARTICIPATIONS.index(y["participation"])
                ) == 1
            )
        ]
    robust = max(
        validation,
        key=lambda k: (
            min(
                [validation[q]["costs"]["25"]["usd_per_calendar_day"] for q in neighbors(k)]
                or [-float("inf")]
            ),
            validation[k]["costs"]["25"]["usd_per_calendar_day"],
            k,
        ),
    )
    report = {
        "validation": validation,
        "late": late,
        "choices": {
            "baseline_cell": center,
            "validation_best_25bps": best,
            "robust_min_neighbor": robust,
        },
        "neighbor_definition": (
            "one coordinate step away; threshold/participation use adjacent grid levels; "
            "latency/slots use the other level"
        ),
        "calendar_days": {
            "validation": depth.calendar_days_spanned(valdays),
            "late": depth.calendar_days_spanned(latedays),
        },
    }
    depth.write_json_atomic(out / "results.json", report)
    print(f"Wrote {out / 'results.json'}", flush=True)


if __name__ == "__main__":
    main()
