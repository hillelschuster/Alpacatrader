#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Final, TypedDict

import numpy as np

import basket_sim as bs
from basket_f4_scaleout import ENTRIES

THIRD_TAG: Final[str] = "0.3333"
H_LEVELS: Final[tuple[int, ...]] = (30, 50, 100)
BLOCKS: Final[dict[str, tuple[str, str]]] = {
    "block1": ("2021-02", "2023-12"),
    "block2": ("2025-02", "2026-05"),
}


class SeriesStats(TypedDict):
    days: int
    mean: float
    sum: float
    positive_share: float
    worst_day: float
    max_dd: float


def exact_fraction(reason: str) -> float:
    if reason.startswith("DERISK"):
        return float(reason.split("x=")[1].rstrip(")")) / 100.0
    tag = reason.split(":REDUCE:")[1].split(":")[0]
    return 1.0 / 3.0 if tag == THIRD_TAG else float(tag)


def replay_ticket(row: dict, side: float) -> tuple[list[dict], float]:
    actions = json.loads(row["actions_json"])
    shares = float(row["shares_entry"])
    cost = float(row["unit_notional"])
    tranches: list[dict] = []
    total = 0.0
    for action in actions:
        kind = action["action"]
        if kind == "ENTER":
            continue
        if kind == "REDUCE":
            fraction = exact_fraction(action["reason"])
            removed = fraction * shares
            reason = action["reason"]
            label = "derisk" if reason.startswith("DERISK") else f"t{reason.rsplit(':', 1)[-1]}_reduce"
        elif kind == "EXIT":
            removed = shares
            reason = action["reason"]
            if reason == "FORCED_FLAT":
                label = "hold_to_close"
            elif ":EXIT:" in reason:
                label = f"t{reason.rsplit(':', 1)[-1]}_exit"
            else:
                label = "release_exit"
        else:
            continue
        notional = cost * removed / shares
        pnl = removed * float(action["px"]) * (1.0 - side) - notional
        tranches.append({"label": label, "pnl": pnl, "notional": notional})
        total += pnl
        shares -= removed
        cost -= notional
    if row["open_end"] and shares > 1e-15:
        pnl = float(row["net"]) - total
        tranches.append({"label": "terminal_mark", "pnl": pnl, "notional": cost})
        total += pnl
    return tranches, total


def load_raw_paths() -> dict[tuple[str, str, str], dict]:
    paths: dict[tuple[str, str, str], dict] = {}
    for day in bs.dev_days():
        record = bs.load_anatomy(day)
        for entry, (population, timestamp) in ENTRIES.items():
            snapshot = bs.snapshot_of(record, population, timestamp)
            if snapshot is None:
                continue
            for name in snapshot["names"][:4]:
                fill = name.get("fill")
                if not fill or fill.get("blocked"):
                    continue
                touches = {
                    level: (name["ladders"]["up"].get(str(level)) or {}).get("et")
                    for level in H_LEVELS
                }
                paths[(entry, day, name["ticker"])] = {
                    "mfe": float(name["mfe"]),
                    "touches": touches,
                }
    return paths


def false_release(row: dict, path: dict) -> dict[int, dict]:
    actions = json.loads(row["actions_json"])
    result: dict[int, dict] = {}
    for level, touch_et in path["touches"].items():
        if touch_et is None:
            continue
        shares = float(row["shares_entry"])
        entry_shares = shares
        flat = False
        for action in actions:
            if action["action"] == "ENTER" or int(action["et"]) >= int(touch_et):
                continue
            if action["action"] == "REDUCE":
                shares -= exact_fraction(action["reason"]) * shares
            elif action["action"] == "EXIT":
                shares = 0.0
                flat = True
        sold_fraction = 1.0 - shares / entry_shares
        result[level] = {
            "flat": flat,
            "half": sold_fraction >= 0.5 - 1e-12,
            "sold_fraction": sold_fraction,
        }
    return result


def block_name(day: str) -> str:
    month = day[:7]
    for name, (start, end) in BLOCKS.items():
        if start <= month <= end:
            return name
    return "outside"


def series_stats(values: list[float]) -> SeriesStats:
    assert values
    array = np.asarray(values, dtype=np.float64)
    equity = np.cumprod(1.0 + array)
    drawdown = equity / np.maximum.accumulate(equity) - 1.0
    return {
        "days": len(array),
        "mean": float(array.mean()),
        "sum": float(array.sum()),
        "positive_share": float((array > 0).mean()),
        "worst_day": float(array.min()),
        "max_dd": float(drawdown.min()),
    }


def daily_surfaces(rows: list[dict]) -> tuple[dict[str, SeriesStats], list[dict]]:
    by_block: dict[str, list[float]] = defaultdict(list)
    by_month: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = float(row["r_day"])
        by_block[block_name(row["date"])].append(value)
        by_month[row["date"][:7]].append(value)
    blocks = {name: series_stats(values) for name, values in by_block.items()}
    months = [
        {"month": month, **series_stats(values)}
        for month, values in sorted(by_month.items())
    ]
    return blocks, months


def safe_number(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return value
