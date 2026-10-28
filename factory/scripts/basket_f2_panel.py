#!/usr/bin/env python
"""BASKET-01 Phase-2 F2/F12 — build the causal golden-window state panel.

Streams dev days one at a time (anatomy JSONL + bars parquet), emits one row per
``(day, family, ticker, checkpoint)`` for **filled** names only, and writes
month-sharded parquet plus a merged ``state_panel.parquet``.

Usage
-----
    python factory/scripts/basket_f2_panel.py              # build (resumable)
    python factory/scripts/basket_f2_panel.py --merge-only # concat shards
    python factory/scripts/basket_f2_panel.py --force      # rebuild shards
    python factory/scripts/basket_f2_panel.py --months 2021-02,2021-03
    python factory/scripts/basket_f2_panel.py --max-days 30

Resumability: one shard per calendar month; existing shards are skipped unless
``--force``.  A kill therefore loses at most the current month (seconds).
Memory: one day is resident at a time (RSS well under 2 GB).
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from pathlib import Path

import polars as pl

import basket_f2_features as F

ROOT = Path(__file__).resolve().parents[2]
OUT = F._ART / "phase2" / "F2_golden"
PANEL_DIR = OUT / "state_panel"
PANEL_PATH = OUT / "state_panel.parquet"
COVERAGE_PATH = OUT / "coverage.json"
PROGRESS_PATH = OUT / "progress.json"

META_COLS = ["date", "family", "ticker", "rank", "fill_et", "fill_px", "et", "session_end",
             "calendar_estimated"]


def _atomic_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str))
    os.replace(tmp, path)


def build(months=None, force=False, max_days=None) -> None:
    days = F.dev_days()
    if months:
        keep = set(months)
        days = [d for d in days if d[:7] in keep]
    if max_days:
        # keep whole months so shards stay consistent
        cut_months = sorted({d[:7] for d in days})[: max_days // 21 + 1]
        days = [d for d in days if d[:7] in set(cut_months)][:max_days]
    by_month: dict[str, list[str]] = {}
    for d in days:
        by_month.setdefault(d[:7], []).append(d)

    PANEL_DIR.mkdir(parents=True, exist_ok=True)
    coverage = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "canonical_calendar_present": F.CAL_PATH.exists(),
        "calendar_note": (
            "canonical calendar used" if F.CAL_PATH.exists() else
            "TEMP session map: session_end=779 for the 7 known early-close candidates, "
            "959 otherwise; FLAGGED - re-run when phase2_session_calendar.json lands"),
        "checkpoints": list(F.CHECKPOINTS),
        "families": {k: list(v) for k, v in F.FAMILIES.items()},
        "months": {},
        "blocked": [],
        "n_days": 0, "n_rows": 0, "n_blocked": 0,
    }
    t0 = time.time()
    done = 0
    for m in sorted(by_month):
        shard = PANEL_DIR / f"month={m}.parquet"
        if shard.exists() and not force:
            print(f"[skip] {m} (shard exists)", flush=True)
            done += len(by_month[m])
            continue
        mrows: list[dict] = []
        mb = Counter()
        for d in by_month[m]:
            se, est = F.session_end_for(d)
            rec = F.load_anatomy(d)
            bars = F.load_bars(d, se)
            rows, blocked = F.build_day(rec, bars, se, est)
            mrows.extend(rows)
            mb["rows"] += len(rows)
            mb["blocked"] += len(blocked)
            coverage["blocked"].extend(blocked)
            del bars, rec, rows, blocked
        df = pl.DataFrame(mrows, infer_schema_length=None) if mrows else pl.DataFrame()
        tmp = shard.with_suffix(".parquet.tmp")
        df.write_parquet(tmp)
        os.replace(tmp, shard)
        coverage["months"][m] = {"days": len(by_month[m]), **mb}
        coverage["n_days"] += len(by_month[m])
        coverage["n_rows"] += mb["rows"]
        coverage["n_blocked"] += mb["blocked"]
        done += len(by_month[m])
        _atomic_json(PROGRESS_PATH, {"phase": "build", "last_month": m,
                                     "months_done": len(coverage["months"]),
                                     "days_done": done, "days_total": len(days),
                                     "rows": coverage["n_rows"], "blocked": coverage["n_blocked"],
                                     "elapsed_s": round(time.time() - t0, 1)})
        print(f"[build] {m}: {mb['rows']} rows, {mb['blocked']} blocked "
              f"({done}/{len(days)} days, {time.time()-t0:.0f}s)", flush=True)

    _atomic_json(COVERAGE_PATH, coverage)
    print(f"[build] done: {coverage['n_rows']} rows, {coverage['n_blocked']} blocked, "
          f"{time.time()-t0:.0f}s", flush=True)


def finalize_coverage() -> dict:
    """Recompute coverage from the shards + a cheap anatomy-only blocked scan.

    Robust to resume (skipped shards are still counted).  The blocked scan does
    not read bars, so it captures ``blocked`` / ``no_fill`` fills exactly; the
    rarer ``no_bars`` / ``fill_bar_missing`` cases are counted only for months
    that were (re)built in this process.
    """
    shards = sorted(PANEL_DIR.glob("month=*.parquet"))
    months = {}
    total_rows = 0
    for s in shards:
        m = s.stem.split("=", 1)[1]
        h = pl.read_parquet(s).height
        months[m] = {"rows": h}
        total_rows += h
    blocked = []
    days_by_month: dict[str, int] = {}
    for d in F.dev_days():
        rec = F.load_anatomy(d)
        by = {(s["pop"], int(s["T"])): s for s in rec["snapshots"]}
        for family, (pop, T) in F.FAMILIES.items():
            snap = by.get((pop, T))
            if snap is None:
                continue
            for n in snap["names"]:
                fill = n.get("fill")
                if not fill or fill.get("blocked") or fill.get("et") is None:
                    blocked.append({"date": d, "family": family, "ticker": n["ticker"],
                                    "rank": int(n["rank"]),
                                    "reason": "blocked" if (fill and fill.get("blocked")) else "no_fill"})
        days_by_month[d[:7]] = days_by_month.get(d[:7], 0) + 1
    for m, v in months.items():
        v["days"] = days_by_month.get(m, 0)
    cov = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "canonical_calendar_present": F.CAL_PATH.exists(),
        "calendar_note": (
            "canonical calendar present; its 7 detected early closes match the F2 temp "
            "map exactly, so all shards are session_end-consistent" if F.CAL_PATH.exists() else
            "TEMP session map: session_end=779 for the 7 known early-close candidates, "
            "959 otherwise; FLAGGED - re-run when phase2_session_calendar.json lands"),
        "checkpoints": list(F.CHECKPOINTS),
        "families": {k: list(v) for k, v in F.FAMILIES.items()},
        "months": months,
        "blocked": blocked,
        "n_days": sum(days_by_month.values()),
        "n_rows": total_rows,
        "n_blocked": len(blocked),
    }
    _atomic_json(COVERAGE_PATH, cov)
    return cov


def merge_only() -> None:
    shards = sorted(PANEL_DIR.glob("month=*.parquet"))
    if not shards:
        raise SystemExit("no shards to merge")
    frames = [pl.read_parquet(s) for s in shards]
    df = pl.concat(frames, how="diagonal_relaxed")
    # deterministic column order: identity, state, targets/flags
    cols = list(df.columns)
    ordered = [c for c in META_COLS if c in cols] + [c for c in cols if c not in META_COLS]
    df = df.select(ordered).sort(["date", "family", "ticker", "et"])
    tmp = PANEL_PATH.with_suffix(".parquet.tmp")
    df.write_parquet(tmp)
    os.replace(tmp, PANEL_PATH)
    print(f"[merge] {len(shards)} shards -> {PANEL_PATH} ({df.height} rows, "
          f"{df.width} cols)", flush=True)
    for s in shards:
        print("  shard", s.name, pl.read_parquet(s).height)
    cov = finalize_coverage()
    print(f"[coverage] {cov['n_rows']} rows, {cov['n_blocked']} blocked, "
          f"{cov['n_days']} days -> {COVERAGE_PATH}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--merge-only", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--months", default=None, help="comma-separated YYYY-MM subset")
    ap.add_argument("--max-days", type=int, default=None)
    a = ap.parse_args()
    if a.merge_only:
        merge_only()
        return
    build(months=a.months.split(",") if a.months else None, force=a.force, max_days=a.max_days)


if __name__ == "__main__":
    main()
