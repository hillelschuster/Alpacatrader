#!/usr/bin/env python
"""Causal descriptive F3 structural map; never selects a release rule."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path

import numpy as np
import polars as pl

try:
    from factory.scripts import basket_sim as sim
except ImportError:
    import basket_sim as sim

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "factory/artifacts/basket/phase2/F3"
BLOCKS = {"block1": ("2021-02", "2023-12", 734), "block2": ("2025-02", "2026-05", 332)}
BINS = {
    "depth_bin": ("d0", "d1", "d2", "d3"),
    "duration_bin": ("short", "medium", "long"),
    "recovery_bin": ("reclaimed", "not_reclaimed"),
    "peak_drawdown_bin": ("shallow", "moderate", "deep"),
    "mfe_surrendered_bin": ("low", "medium", "high"),
}


def validate_day(day: str) -> None:
    sim.guard_day(day)
    if not (("2021-02-01" <= day <= "2023-12-31") or
            ("2025-02-01" <= day <= "2026-05-31")):
        raise ValueError(f"day outside frozen development blocks: {day}")


def _bin(value: float, edges: tuple[float, ...], labels: tuple[str, ...]) -> str:
    return labels[int(np.searchsorted(edges, value, side="right"))]


def structural_row(day: str, ticker: str, entry_et: int, entry_px: float,
                   bars: dict, entry_index: int, state_index: int) -> dict:
    """One close-at-t observation; all outcome fields start at index t+1."""
    et = np.asarray(bars["et"])
    highs, lows, closes = (np.asarray(bars[k], dtype=float) for k in ("high", "low", "close"))
    if (entry_px <= 0 or entry_index < 0 or state_index < entry_index or
            state_index >= len(et) or et[entry_index] != entry_et):
        raise ValueError("invalid entry price or decision-bar index")
    decision_et = int(et[state_index])
    path_high = highs[entry_index:state_index + 1]
    path_low = lows[entry_index:state_index + 1]
    path_close = closes[entry_index:state_index + 1]
    local_index = state_index - entry_index
    peak_value = float(path_high.max())
    peak_ix = int(np.flatnonzero(path_high == peak_value)[-1])
    peak = peak_value
    close = float(closes[state_index])
    mfe = max(0.0, float(path_high.max()) / entry_px - 1.0)
    current_return = close / entry_px - 1.0
    breach_duration = 0
    if path_close[-1] < entry_px:
        breach_duration = 1
        while breach_duration < local_index + 1 and path_close[-breach_duration - 1] < entry_px:
            breach_duration += 1
    # Recovery speed is elapsed completed bars from the lowest low in the current
    # continuous below-entry episode; a live episode remains explicitly unresolved.
    reclaimed = close >= entry_px
    if breach_duration:
        episode_start = local_index - breach_duration + 1
        episode_end = local_index
    elif reclaimed:
        episode_start = local_index - 1
        while episode_start >= 0 and path_close[episode_start] < entry_px:
            episode_start -= 1
        episode_start += 1
        episode_end = local_index - 1
    else:
        episode_start, episode_end = local_index, local_index
    if episode_start <= episode_end:
        trough_ix = episode_start + int(np.argmin(path_low[episode_start:episode_end + 1]))
        recovery_bars = local_index - trough_ix
    else:
        recovery_bars = 0
    surrendered = max(0.0, mfe - current_return) / mfe if mfe > 0 else 0.0
    future = slice(state_index + 1, len(et))
    future_n = len(et) - state_index - 1
    future_close = float(closes[-1] / close - 1.0) if future_n else None
    future_max = float(highs[future].max() / close - 1.0) if future_n else None
    future_min = float(lows[future].min() / close - 1.0) if future_n else None
    depth = current_return
    peak_dd = close / peak - 1.0
    peak_breached = bool(np.any(path_close[peak_ix + 1:] < peak))
    row = {
        "date": day, "month": day[:7], "block": _block(day), "ticker": ticker,
        "entry_et": int(entry_et), "decision_et": decision_et, "entry_px": float(entry_px), "decision_close": close,
        "entry_drawdown_pct": depth, "breach_duration_bars": breach_duration,
        "recovery_bars_from_episode_low": recovery_bars if reclaimed else None,
        "recovery_speed_bars_from_low": recovery_bars,
        "reclaimed_entry": reclaimed, "running_peak_px": peak,
        "peak_drawdown_pct": peak_dd, "mfe_to_date_pct": mfe,
        "mfe_surrendered_pct": surrendered, "time_since_high_bars": local_index - peak_ix,
        "failed_reclaim": peak_breached and close < peak,
        "future_max_return": future_max, "future_min_return": future_min,
        "future_close_return": future_close, "future_bars_n": future_n,
        "outcome_available": future_n > 0,
    }
    row.update({
        "depth_bin": _bin(depth, (-0.20, -0.10, -0.05), BINS["depth_bin"]),
        "duration_bin": _bin(breach_duration, (0, 5), BINS["duration_bin"]),
        "recovery_bin": "reclaimed" if reclaimed else "not_reclaimed",
        "peak_drawdown_bin": _bin(peak_dd, (-0.15, -0.05), ("deep", "moderate", "shallow")),
        "mfe_surrendered_bin": _bin(surrendered, (0.25, 0.75), BINS["mfe_surrendered_bin"]),
    })
    return row


def _block(day: str) -> str:
    for name, (lo, hi, _) in BLOCKS.items():
        if lo <= day[:7] <= hi:
            return name
    raise ValueError(f"day outside frozen development blocks: {day}")


def interaction_table(rows: list[dict], dimensions: tuple[str, ...]) -> list[dict]:
    """Fixed full categorical cross-product; empty strata remain with n=0."""
    for dimension in dimensions:
        if dimension not in BINS:
            raise ValueError(f"unregistered F3 structural dimension: {dimension}")
    grouped: dict[tuple, list[dict]] = {}
    for row in rows:
        if row.get("outcome_available", True):
            grouped.setdefault(tuple(row[d] for d in dimensions), []).append(row)
    result = []
    for values in itertools.product(*(BINS[d] for d in dimensions)):
        sample = grouped.get(values, [])
        result.append({**dict(zip(dimensions, values)), "n": len(sample),
                       "future_close_return_mean": _mean(sample, "future_close_return"),
                       "future_max_return_mean": _mean(sample, "future_max_return"),
                       "future_min_return_mean": _mean(sample, "future_min_return")})
    return result


def _mean(rows: list[dict], key: str):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return sum(vals) / len(vals) if vals else None


def _atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=1, sort_keys=True, allow_nan=False))
    os.replace(tmp, path)


def _part_rows(day: str, session_end: int) -> list[dict]:
    validate_day(day)
    rec = sim.load_anatomy(day)
    snap = sim.snapshot_of(rec, "A_pm", 570)
    if not snap:
        return []
    bars = sim.load_bars(day, session_end)
    output = []
    for name in sorted(snap["names"], key=lambda x: (int(x["rank"]), x["ticker"]))[:2]:
        fill = name.get("fill") or {}
        ticker = name["ticker"]
        path = bars.ticker(ticker)
        if fill.get("blocked") or not path:
            continue
        index = int(np.searchsorted(path["et"], int(fill["et"])))
        if index >= len(path["et"]) or int(path["et"][index]) != int(fill["et"]):
            continue
        for i in range(index, len(path["et"])):
            output.append(structural_row(day, ticker, int(fill["et"]), float(fill["px"]),
                                         path, index, i))
    return output


def _inputs_hash(days: list[str]) -> dict:
    def digest(paths):
        h = hashlib.sha256()
        for path in sorted(paths):
            h.update(path.name.encode()); h.update(path.read_bytes())
        return h.hexdigest()
    return {
        "contract_sha256": hashlib.sha256(sim.CONTRACT_PATH.read_bytes()).hexdigest(),
        "calendar_sha256": hashlib.sha256(sim.CAL_PATH.read_bytes()).hexdigest(),
        "day_manifest_sha256": hashlib.sha256("\n".join(days).encode()).hexdigest(),
        "anatomy_manifest_sha256": digest([sim.ANAT_DIR / f"{d}.jsonl" for d in days]),
        "bars_manifest_sha256": digest([sim.BARS_DIR / f"{d}.parquet" for d in days]),
    }


def run(out_root: Path = OUT, *, force: bool = False) -> dict:
    days = sim.dev_days()
    if len(days) != 1066:
        raise RuntimeError(f"canonical anatomy day count must equal 1066, got {len(days)}")
    for day in days:
        validate_day(day)
    expected = {name: n for name, (_, _, n) in BLOCKS.items()}
    got = {name: sum(_block(day) == name for day in days) for name in BLOCKS}
    if got != expected or set(sim.session_end_map()) != set(days):
        raise RuntimeError(f"canonical date/block/calendar guard failed: blocks={got}")
    root = out_root / "structural_map"
    parts = root / "parts"
    parts.mkdir(parents=True, exist_ok=True)
    provenance = _inputs_hash(days)
    resume_fingerprint = hashlib.sha256(
        json.dumps(provenance, sort_keys=True).encode()
    ).hexdigest()
    _atomic_json(root / "run_config.json", {
        "scope": "A_pm/T570 canonical top-2 filled own paths", "days": len(days),
        "blocks": expected, "rows": "one observation per completed path bar t from entry through session end",
        "decision": "closed bar through t only; current high may update running own-path peak",
        "outcomes": "high/low/close only for bars with et > decision_et, relative to decision close",
        "selection_or_profitability": False, "no thresholds/gates/composites/cell selection": True,
        "provenance": provenance,
    })
    sessions = sim.session_end_map()
    months = sorted({day[:7] for day in days})
    for month in months:
        target = parts / f"month={month}.parquet"
        marker = parts / f"month={month}.sha256"
        month_days = [d for d in days if d[:7] == month]
        if target.exists() and marker.exists() and not force and marker.read_text() == resume_fingerprint:
            continue
        rows = [row for day in month_days for row in _part_rows(day, int(sessions[day]))]
        temp = target.with_suffix(".parquet.tmp")
        pl.DataFrame(rows, infer_schema_length=None).write_parquet(temp)
        os.replace(temp, target)
        marker.write_text(resume_fingerprint)
    frames = [pl.read_parquet(parts / f"month={m}.parquet") for m in months]
    frame = pl.concat(frames, how="diagonal_relaxed")
    daily = root / "structural_daily.parquet"
    tmp = daily.with_suffix(".parquet.tmp"); frame.write_parquet(tmp); os.replace(tmp, daily)
    rows = frame.to_dicts()
    tables = {}
    for block in BLOCKS:
        block_rows = [r for r in rows if r["block"] == block]
        tables[block] = {
            "entry_depth_duration_recovery": interaction_table(block_rows, ("depth_bin", "duration_bin", "recovery_bin")),
            "peak_drawdown_duration_recovery": interaction_table(block_rows, ("peak_drawdown_bin", "duration_bin", "recovery_bin")),
            "mfe_surrender_duration_recovery": interaction_table(block_rows, ("mfe_surrendered_bin", "duration_bin", "recovery_bin")),
        }
    _atomic_json(root / "relationship_tables.json", {
        "dimensions": BINS, "blocks": tables,
        "outcome_rows_only": "future_bars_n > 0; all registered cross-product strata retained",
    })
    _atomic_json(root / "provenance.json", {
        "label": "RUN", "producer": "factory/scripts/basket_f3_structural_map.py",
        "inputs": provenance, "days_n": len(days), "block_days": got,
        "monthly_parts_n": len(months), "rows_n": frame.height,
        "rows_with_future_outcome_n": int(frame["outcome_available"].sum()),
        "sealed_reserved_access": False,
    })
    return {"days": len(days), "blocks": got, "months": len(months), "rows": frame.height}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-root", type=Path, default=OUT)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(run(args.out_root, force=args.force), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
