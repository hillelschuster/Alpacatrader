#!/usr/bin/env python3
"""Descriptive, own-path structural map for the frozen F3 delivery."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
ART = Path(os.environ.get("BASKET_ART_ROOT", str(ROOT / "factory/artifacts/basket/sip")))
OUT = ROOT / "factory/artifacts/basket/phase2/F3_structure"
DEV_MONTHS = tuple([f"2021-{m:02d}" for m in range(2, 13)]
                   + [f"2022-{m:02d}" for m in range(1, 13)]
                   + [f"2023-{m:02d}" for m in range(1, 13)]
                   + [f"2025-{m:02d}" for m in range(2, 13)]
                   + [f"2026-{m:02d}" for m in range(1, 6)])
POPS = (("A_pm", 570), ("A_open", 570), ("B600", 600))


def describe_path(et, high, low, close, fill):
    """Map full own path without cut points; all extrema use completed bars."""
    if not (len(et) == len(high) == len(low) == len(close)) or len(et) == 0:
        raise ValueError("path arrays must be nonempty and equal length")
    et = np.asarray(et, dtype=np.int64)
    high, low, close = (np.asarray(x, dtype=float) for x in (high, low, close))
    trough = int(np.argmin(low))
    adverse = max(0.0, (float(fill) - float(low[trough])) / float(fill) * 100)
    # Recovery is measured from the deepest adverse print; no recovery is censored at session end.
    recovered = next((int(i) for i in range(trough + 1, len(et)) if close[i] >= fill), None)
    entry_duration = int(et[recovered] - et[trough]) if recovered is not None else int(et[-1] - et[trough])
    running_high = np.maximum.accumulate(high)
    candidates = np.arange(1, len(et))
    drawdowns = (running_high[candidates] - low[candidates]) / running_high[candidates] * 100
    if candidates.size:
        dd_i = int(candidates[int(np.argmax(drawdowns))])
        peak = int(np.flatnonzero(high[:dd_i + 1] == running_high[dd_i])[-1])
        dd = max(0.0, float(drawdowns.max()))
        mfe = max(0.0, (float(running_high[dd_i]) / float(fill) - 1) * 100)
        time_since = int(et[dd_i] - et[peak])
        failed = not bool(np.any(high[dd_i + 1:] > running_high[dd_i]))
    else:
        peak = 0
        dd, mfe, time_since, failed = 0.0, max(0.0, (float(high[peak]) / float(fill) - 1) * 100), 0, False
    surrendered = dd / mfe * 100 if mfe > 0 else None
    return {
        "entry_max_adverse_pct": round(adverse, 6),
        "entry_max_adverse_duration_min": entry_duration,
        "entry_recovery_min": int(et[recovered] - et[trough]) if recovered is not None else None,
        "entry_recovery_speed_pct_per_min": round(adverse / (int(et[recovered] - et[trough])), 6)
        if recovered is not None and et[recovered] > et[trough] else None,
        "peak_drawdown_pct": round(dd, 6), "peak_mfe_pct": round(mfe, 6),
        "peak_mfe_surrendered_pct": round(surrendered, 6) if surrendered is not None else None,
        "peak_time_since_peak_min": time_since, "peak_failed_reclaim": failed,
        "entry_mag_x_duration": round(adverse * entry_duration, 6),
        "peak_dd_x_time": round(dd * time_since, 6),
    }


def load_calendar():
    raw = json.loads((ART / "phase2_session_calendar.json").read_text())
    days = (raw.get("days") or raw.get("calendar") or raw.get("session_end_by_day")
            or raw.get("session_end"))
    if not isinstance(days, dict):
        raise ValueError("canonical calendar lacks day mapping")
    calendar = {str(k)[:10]: int(v.get("session_end", v.get("end")) if isinstance(v, dict) else v)
                for k, v in days.items()}
    return calendar


def load_bars(date):
    df = pl.read_parquet(ART / "bars" / f"{date}.parquet",
                         columns=["ticker", "et", "high", "low", "close"]).sort(["ticker", "et"])
    grouped = {}
    for ticker, group in df.group_by("ticker", maintain_order=True):
        grouped[str(ticker[0])] = tuple(group[c].to_numpy() for c in ("et", "high", "low", "close"))
    return grouped


def process_day(date, calendar):
    session_end = calendar[date]
    rec = json.loads((ART / "anatomy" / f"{date}.jsonl").read_text())
    snapshots = {(s["pop"], int(s["T"])): s for s in rec.get("snapshots", [])}
    bars = load_bars(date)
    rows = []
    for pop, decision_et in POPS:
        snapshot = snapshots.get((pop, decision_et))
        if snapshot is None:
            continue
        for name in snapshot.get("names", []):
            fill = name.get("fill")
            if not fill or fill.get("blocked") or fill.get("px") is None:
                continue
            values = bars.get(name["ticker"])
            if values is None:
                continue
            et, high, low, close = values
            end = int(np.searchsorted(et, session_end, side="right"))
            start = int(np.searchsorted(et, int(fill["et"])))
            et, high, low, close = (x[start:end] for x in (et, high, low, close))
            if not len(et) or int(et[0]) != int(fill["et"]):
                continue
            row = {"date": date, "blk": "B1" if date <= "2023-12-29" else "B2",
                   "pop": pop, "ticker": name["ticker"], "fill_et": int(fill["et"]),
                   "fill_px": float(fill["px"]), "path_bars": len(et), "path_last_et": int(et[-1])}
            row.update(describe_path(et, high, low, close, float(fill["px"])))
            rows.append(row)
    return rows


def atomic_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run():
    calendar = load_calendar()
    expected = sorted(d for d in calendar if d[:7] in DEV_MONTHS)
    if len(expected) != 1066:
        raise ValueError(f"canonical dev calendar count {len(expected)} != 1066")
    shard_dir = OUT / "days"
    shard_dir.mkdir(parents=True, exist_ok=True)
    for date in expected:
        path = shard_dir / f"{date}.jsonl"
        if path.exists():
            continue
        rows = process_day(date, calendar)
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows))
        tmp.replace(path)
        print(f"{date}: {len(rows)} path rows")
    paths = [shard_dir / f"{d}.jsonl" for d in expected]
    if any(not p.exists() for p in paths):
        raise RuntimeError("incomplete daily shards")
    rows = [json.loads(line) for path in paths for line in path.read_text().splitlines() if line]
    # Raw pairs keep the relationship map inspectable; no bins or threshold selection.
    relations = [{"date": r["date"], "blk": r["blk"], "pop": r["pop"], "ticker": r["ticker"],
                  "magnitude_pct": r["entry_max_adverse_pct"], "duration_min": r["entry_max_adverse_duration_min"],
                  "recovery_speed_pct_per_min": r["entry_recovery_speed_pct_per_min"],
                  "magnitude_x_duration": r["entry_mag_x_duration"],
                  "peak_drawdown_pct": r["peak_drawdown_pct"], "peak_time_min": r["peak_time_since_peak_min"],
                  "peak_dd_x_time": r["peak_dd_x_time"]} for r in rows]
    (OUT / "structural_paths.jsonl").write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows))
    (OUT / "magnitude_duration_recovery.jsonl").write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in relations))
    numeric = ("magnitude_pct", "duration_min", "recovery_speed_pct_per_min",
               "magnitude_x_duration", "peak_drawdown_pct", "peak_time_min", "peak_dd_x_time")
    summary = {"scope": "descriptive only", "rows": len(relations), "by_block_pop": []}
    for blk in ("B1", "B2"):
        for pop, _ in POPS:
            subset = [r for r in relations if r["blk"] == blk and r["pop"] == pop]
            stats = {"blk": blk, "pop": pop, "n": len(subset)}
            for field in numeric:
                values = np.asarray([r[field] for r in subset if r[field] is not None], dtype=float)
                stats[field + "_median"] = round(float(np.median(values)), 6) if values.size else None
            pairs = (("magnitude_pct", "duration_min"), ("magnitude_pct", "recovery_speed_pct_per_min"),
                     ("duration_min", "recovery_speed_pct_per_min"),
                     ("magnitude_pct", "magnitude_x_duration"),
                     ("peak_drawdown_pct", "peak_time_min"))
            stats["pearson_r"] = {}
            for left, right in pairs:
                paired = [(r[left], r[right]) for r in subset if r[left] is not None and r[right] is not None]
                if len(paired) < 2:
                    value = None
                else:
                    value = round(float(np.corrcoef(np.asarray(paired, dtype=float).T)[0, 1]), 6)
                stats["pearson_r"][left + "__" + right] = value
            summary["by_block_pop"].append(stats)
    atomic_json(OUT / "relationship_summary.json", summary)
    meta = {"artifact": "F3_structure", "producer": "factory/scripts/basket_f3_structure.py",
            "calendar": str(ART / "phase2_session_calendar.json"), "calendar_sha256": sha256(ART / "phase2_session_calendar.json"),
            "dev_days": len(expected), "block_days": {"B1": sum(d <= "2023-12-29" for d in expected),
            "B2": sum(d > "2023-12-29" for d in expected)}, "path_rows": len(rows),
            "source_hashes": {str(p.relative_to(ROOT)): sha256(p) for p in
                              [Path(__file__), ROOT / "researches/PRE-REG-BASKET-02.md",
                               ROOT / "factory/BASKET-SIM-CONTRACT.md"]},
            "inputs": "committed canonical calendar plus completed SIP bars/anatomy; dev months only",
            "excluded": ["sealed 2024 and 2025-01", "reserved 2026-06..08"],
            "scope": "descriptive own-path mapping; no thresholds, gates, composites, or selection"}
    atomic_json(OUT / "provenance.json", meta)
    report = ["# F3 structural map (descriptive; development only)", "",
              "Relationships are presented first, before any release-module discussion. "
              "All rows are own-path and use completed bars through canonical session end.", "",
              "Continuous raw pairs and descriptive Pearson associations are provided; "
              "no cut points, thresholds, gates, composites, or selection were fit.", "",
              "| block | population | n | median magnitude×duration | median peak drawdown×time | r(magnitude,duration) | r(magnitude,recovery speed) |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for r in summary["by_block_pop"]:
        corr = r["pearson_r"]
        report.append(f"| {r['blk']} | {r['pop']} | {r['n']} | {r['magnitude_x_duration_median']} | "
                      f"{r['peak_dd_x_time_median']} | {corr['magnitude_pct__duration_min']} | "
                      f"{corr['magnitude_pct__recovery_speed_pct_per_min']} |")
    report += ["", "Metric definitions and chronology are in `README.md`; full ticket rows in "
               "`structural_paths.jsonl`; unbinned paired measurements in "
               "`magnitude_duration_recovery.jsonl`; reproducibility details in `provenance.json`."]
    (OUT / "structural_map.md").write_text("\n".join(report) + "\n")
    print(f"complete: days={len(expected)} B1={meta['block_days']['B1']} B2={meta['block_days']['B2']} rows={len(rows)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="build missing days and aggregate full artifacts")
    parser.parse_args()
    run()
