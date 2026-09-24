#!/usr/bin/env python
"""Descriptive, as-of-10:00 environment and calendar maps over frozen F1 cells."""

from __future__ import annotations

import argparse
import json
import os
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

if __package__:
    from factory.scripts import basket_sim as sim
else:
    import basket_sim as sim


ROOT = Path(__file__).resolve().parents[2]
F1_ROOT = ROOT / "factory" / "artifacts" / "basket" / "phase2" / "F1" / "F1"
OUT_ROOT = ROOT / "factory" / "artifacts" / "basket" / "phase2" / "F10_F14"
ASOF_ET = 600
BUCKETS = {
    "pm_leader_strength": (0.10, 0.30),
    "pm_top10_dispersion": (0.10, 0.50),
    "pm_names_ge_10pct": (2, 4),
    "b600_cohort_trend": (-0.01, 0.01),
    "prior_b600_cohort_trend": (-0.01, 0.01),
    "b600_realized_volatility": (0.005, 0.015),
    "b600_names_ge_10pct": (2, 4),
    "b600_top10_dollar_volume": (1_000_000, 10_000_000),
}
BUCKET_LABELS = {
    "pm_leader_strength": ("lt10pct", "10to30pct", "ge30pct"),
    "pm_top10_dispersion": ("lt10pp", "10to50pp", "ge50pp"),
    "pm_names_ge_10pct": ("0to1", "2to3", "4plus"),
    "b600_cohort_trend": ("lt_minus1pct", "minus1_to_plus1pct", "ge_plus1pct"),
    "prior_b600_cohort_trend": ("lt_minus1pct", "minus1_to_plus1pct", "ge_plus1pct"),
    "b600_realized_volatility": ("lt0_5pct", "0_5to1_5pct", "ge1_5pct"),
    "b600_names_ge_10pct": ("0to1", "2to3", "4plus"),
    "b600_top10_dollar_volume": ("lt1m", "1to10m", "ge10m"),
}
TIME_DIMENSIONS = ("year", "quarter", "month", "weekday")
BLOCKS = {
    "block1_2021-02_to_2023-12": ("2021-02-01", "2023-12-31"),
    "block2_2025-02_to_2026-05": ("2025-02-01", "2026-05-31"),
}


def bucket_index(value: float, cutpoints: tuple[float, float]) -> int:
    """Fixed left-closed categories; the cutpoints are not fitted on outcomes."""
    return int(value >= cutpoints[0]) + int(value >= cutpoints[1])


def bar_environment(
    rows: list[dict], cutoff_et: int = ASOF_ET, tickers: set[str] | None = None
) -> dict[str, float | int]:
    """Summarize candidate bars completed by ``cutoff_et`` (et < decision minute)."""
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if int(row["et"]) < cutoff_et and (tickers is None or str(row["ticker"]) in tickers):
            by_ticker[str(row["ticker"])].append(row)

    ticker_vols: list[float] = []
    dollar_volume = 0.0
    for ticker_rows in by_ticker.values():
        ordered = sorted(ticker_rows, key=lambda row: int(row["et"]))
        if not ordered:
            continue
        closes = [float(row["close"]) for row in ordered]
        minute_returns = [
            b / a - 1.0 for a, b in zip(closes, closes[1:], strict=False) if a > 0
        ]
        if minute_returns:
            ticker_vols.append(statistics.pstdev(minute_returns))
        dollar_volume += sum(float(row["close"]) * float(row["volume"]) for row in ordered)

    return {
        "b600_realized_volatility": statistics.median(ticker_vols) if ticker_vols else 0.0,
        "b600_top10_dollar_volume": dollar_volume,
        "b600_bar_tickers": len(by_ticker),
    }


def _snapshot(record: dict, pop: str, minute: int) -> list[dict]:
    for snapshot in record["snapshots"]:
        if snapshot["pop"] == pop and int(snapshot["T"]) == minute:
            return snapshot["names"]
    raise ValueError(f"missing canonical snapshot {pop}/{minute} on {record.get('date')}")


def environment_for_day(
    day: str, record: dict, bars: list[dict], prior_trend: float | None
) -> dict:
    pm_names = _snapshot(record, "A_pm", 570)
    b600_names = _snapshot(record, "B", 600)
    pm_scores = [float(row["sel"]) for row in pm_names]
    b600_scores = [float(row["sel"]) for row in b600_names]
    bar_features = bar_environment(bars, tickers={str(row["ticker"]) for row in b600_names})
    d = date.fromisoformat(day)
    out: dict[str, Any] = {
        "date": day,
        "year": d.year,
        "quarter": f"{d.year}-Q{(d.month - 1) // 3 + 1}",
        "month": day[:7],
        "weekday": d.strftime("%A"),
        "pm_leader_strength": max(pm_scores, default=0.0),
        "pm_top10_dispersion": (max(pm_scores) - min(pm_scores)) if pm_scores else 0.0,
        "pm_names_ge_10pct": sum(score >= 0.10 for score in pm_scores),
        "b600_cohort_trend": statistics.mean(b600_scores) if b600_scores else 0.0,
        "prior_b600_cohort_trend": prior_trend if prior_trend is not None else 0.0,
        "prior_trend_available": prior_trend is not None,
        "b600_names_ge_10pct": sum(score >= 0.10 for score in b600_scores),
        **bar_features,
    }
    out["labels"] = {
        name: BUCKET_LABELS[name][bucket_index(float(out[name]), cuts)]
        for name, cuts in BUCKETS.items()
    }
    return out


def _read_f1_outcomes(days: list[str]) -> dict[str, dict[str, float]]:
    surface_path = F1_ROOT.parent / "surface.json"
    cells = json.loads(surface_path.read_text())["cells"]
    if len(cells) != 120 or len({cell["run_id"] for cell in cells}) != 120:
        raise RuntimeError(f"F1 surface incomplete or not unique: {len(cells)} cells")
    want_days = set(days)
    outcomes: dict[str, dict[str, float]] = {}
    for cell in cells:
        run_id = cell["run_id"]
        daily_path = F1_ROOT / run_id / "daily.parquet"
        if not daily_path.is_file():
            raise FileNotFoundError(daily_path)
        frame = pl.read_parquet(daily_path, columns=["date", "r_day"])
        rows = frame.to_dicts()
        row_days = [row["date"] for row in rows]
        if (
            len(row_days) != len(days)
            or set(row_days) != want_days
            or len(set(row_days)) != len(days)
        ):
            raise RuntimeError(f"F1 date coverage mismatch in {run_id}")
        for row in rows:
            outcomes.setdefault(row["date"], {})[run_id] = float(row["r_day"])
    return outcomes


def _stats(values: list[float]) -> dict:
    return {
        "n_days": len(values),
        "mean_basket_day": statistics.mean(values) if values else None,
        "median_basket_day": statistics.median(values) if values else None,
        "positive_day_share": (sum(value > 0 for value in values) / len(values))
        if values
        else None,
    }


def build_maps(
    environment: list[dict], outcomes: dict[str, dict[str, float]], cells: list[dict]
) -> dict:
    dimensions = {
        name: [(str(item[name]), item) for item in environment] for name in TIME_DIMENSIONS
    }
    dimensions.update(
        {name: [(item["labels"][name], item) for item in environment] for name in BUCKETS}
    )
    maps: dict[str, dict] = {}
    for dimension, memberships in dimensions.items():
        levels: dict[str, list[str]] = defaultdict(list)
        if dimension in BUCKET_LABELS:
            for label in BUCKET_LABELS[dimension]:
                levels[label]
        for level, item in memberships:
            levels[level].append(item["date"])
        dimension_result: dict[str, dict] = {}
        for level, level_days in sorted(levels.items()):
            per_cell = {}
            for cell in cells:
                run_id = cell["run_id"]
                pooled = [outcomes[day][run_id] for day in level_days]
                per_cell[run_id] = {
                    "parameters": {key: cell[key] for key in ("entry", "N", "exit", "bps")},
                    "pooled": _stats(pooled),
                    "blocks": {
                        block: _stats(
                            [outcomes[day][run_id] for day in level_days if start <= day <= end]
                        )
                        for block, (start, end) in BLOCKS.items()
                    },
                }
            dimension_result[level] = {"days_n": len(level_days), "cells": per_cell}
        maps[dimension] = dimension_result
    return maps


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def run(out_root: Path = OUT_ROOT) -> dict:
    days = sim.dev_days()
    if len(days) != 1066:
        raise RuntimeError(f"expected 1,066 canonical development days, got {len(days)}")
    outcomes = _read_f1_outcomes(days)
    environments: list[dict] = []
    prior_trend = None
    prior_day = None
    for day in days:
        record = sim.load_anatomy(day)
        bars_df = (
            pl.scan_parquet(sim.BARS_DIR / f"{day}.parquet")
            .filter(pl.col("et") < ASOF_ET)
            .select("ticker", "et", "open", "high", "low", "close", "volume")
            .collect()
        )
        usable_prior = (
            prior_trend
            if prior_day and (date.fromisoformat(day) - date.fromisoformat(prior_day)).days <= 7
            else None
        )
        env = environment_for_day(day, record, bars_df.to_dicts(), usable_prior)
        environments.append(env)
        prior_trend = env["b600_cohort_trend"]
        prior_day = day

    cells = json.loads((F1_ROOT.parent / "surface.json").read_text())["cells"]
    maps = build_maps(environments, outcomes, cells)
    provenance = {
        "family": "F10_F14",
        "mode": "descriptive_only",
        "days_n": len(days),
        "first_day": days[0],
        "last_day": days[-1],
        "days": days,
        "asof_et": ASOF_ET,
        "bar_rule": (
            "only rows with et < 600; bars et=t complete at t+1, so 09:59 is latest input "
            "at 10:00 decision"
        ),
        "parameters": BUCKETS,
        "labels": BUCKET_LABELS,
        "time_dimensions": list(TIME_DIMENSIONS),
        "dual_blocks": {key: {"start": val[0], "end": val[1]} for key, val in BLOCKS.items()},
        "f1_cell_count": len(cells),
        "f1_input": "factory/artifacts/basket/phase2/F1/F1/*/daily.parquet + F1/surface.json",
        "canonical_inputs": [
            "factory/artifacts/basket/sip/anatomy/YYYY-MM-DD.jsonl",
            "factory/artifacts/basket/sip/bars/YYYY-MM-DD.parquet",
        ],
        "excluded_access": (
            "sim.dev_days() guards sealed 2024/2025-01 and reserved 2026-06..08 "
            "before any day file is opened"
        ),
        "limits": [
            "As-of-10:00 labels are post-entry for A_pm/A_pm31/A_open; associations with those "
            "full-day F1 outcomes cannot justify their pre-10:00 entry economics. Use them only "
            "as descriptive morning-state context; continuation/action tests must use outcomes "
            "strictly after the declared causal decision and executable next-bar fills.",
            "No SPY/IWM or PIT market-cap series exists in permitted anatomy/bars/F1 "
            "evidence; cohort trend is not a broad-market or small-cap index proxy.",
            "All environment labels are descriptive and are not selected gates; no winner, "
            "profitability, OOS, or live claim is produced.",
        ],
    }
    out_root.mkdir(parents=True, exist_ok=True)
    _atomic(
        out_root / "environment_days.jsonl",
        b"".join((json.dumps(row, sort_keys=True) + "\n").encode() for row in environments),
    )
    _atomic(out_root / "maps.json", json.dumps(maps, indent=1, sort_keys=True).encode())
    _atomic(out_root / "run_config.json", json.dumps(provenance, indent=1, sort_keys=True).encode())
    return {
        "days": len(days),
        "cells": len(cells),
        "dimensions": len(maps),
        "out_root": str(out_root),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-root", type=Path, default=OUT_ROOT)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.out_root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
