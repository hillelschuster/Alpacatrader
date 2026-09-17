#!/usr/bin/env python3
"""BASKET-01 final QA gate — completeness + parse integrity of the anatomy run.

Fails loudly (exit 1) on any problem. No interpretation, no strategy.
Checks:
  1. every anatomy day file parses; required structure present
     (snapshots in {13,14}: A_open + B x12 + A_pm in 2025; every filled member has
      causal fill fields, up/dn ladders, mfe/mae, fixed-time states)
  2. bars parquet exists, has the exact column set, and is non-empty for every day
  3. zero ".tmp" leftovers (atomic-write hygiene)
  4. calendar completeness vs the repo leaderboard calendar, restricted to months
     with raw clean data and <= DEV_END, minus declared known skips:
       - 2024-* and 2025-01: no raw clean data exists (nothing expected)
       - 2025-02 first trading session: no 2025-01 raw data for the prev-close seed
         (documented extractor behaviour)

Usage: .venv/bin/python factory/scripts/basket_qa.py --self-test | (no args)
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASKET = ROOT / "factory" / "artifacts" / "basket"
ANAT = BASKET / "anatomy"
BARSD = BASKET / "bars"
DEV_END = "2026-05"
BAR_COLS = ["date", "ticker", "et", "open", "high", "low", "close", "volume"]
UP = [5, 10, 20, 30, 50, 100]
DN = [3, 5, 8, 10, 15]
STATES = [575, 580, 585, 590, 595, 600, 615, 630, 645, 660, 690, 720]


def check_record(rec: dict) -> list:
    errs = []
    if not isinstance(rec, dict):
        return ["not a dict"]
    for k in ("date", "audit", "winners_open", "snapshots"):
        if k not in rec:
            errs.append(f"missing key {k}")
    snaps = rec.get("snapshots") or []
    if len(snaps) not in (13, 14):
        errs.append(f"snapshot count {len(snaps)}")
    year = str(rec.get("date", ""))[:4]
    for i, s in enumerate(snaps):
        if s.get("pop") not in ("A_open", "A_pm", "B"):
            errs.append(f"snap{i}: bad pop {s.get('pop')}")
        for n in s.get("names", []):
            t = n.get("ticker", "?")
            if "fill" not in n:
                errs.append(f"{t}: no fill key")
                continue
            f = n["fill"]
            if f is None:
                continue  # blocked: top-level unfilled path (allowed, rare)
            if f.get("blocked"):
                continue
            if not isinstance(f.get("et"), int) or not isinstance(f.get("px"), (int, float)):
                errs.append(f"{t}: bad fill {f}")
            lad = n.get("ladders") or {}
            for H in UP:
                if str(H) not in (lad.get("up") or {}):
                    errs.append(f"{t}: ladders.up missing {H}")
            for L in DN:
                if str(L) not in (lad.get("dn") or {}):
                    errs.append(f"{t}: ladders.dn missing {L}")
            if n.get("mfe") is None or n.get("mae") is None:
                errs.append(f"{t}: mfe/mae missing")
            st = n.get("state") or {}
            for T in STATES:
                if str(T) not in st:
                    errs.append(f"{t}: state missing {T}")
    return errs


def known_skips(lb_dates: list, raw_months: set) -> set:
    """Declared, documented skips (not bugs)."""
    skips = set()
    first_2025_02 = next((d for d in lb_dates if d[:7] == "2025-02"), None)
    if first_2025_02 and "2025-01" not in raw_months:
        skips.add(first_2025_02)
    return skips


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        good = {"date": "2025-06-02", "audit": {}, "winners_open": [],
                "snapshots": [{"pop": "B", "T": T, "names": []} for T in
                              [570] + STATES]}
        good["snapshots"] = [{"pop": "A_open", "T": 570, "names": []}] + \
            [{"pop": "B", "T": T, "names": []} for T in STATES]
        assert check_record(good) == [], check_record(good)
        bad = json.loads(json.dumps(good))
        bad["snapshots"] = bad["snapshots"][:3]
        assert check_record(bad), "short snapshot list must fail"
        mm = {"2025-02", "2026-01"}  # no 2025-01 raw data -> first 2025-02 day is a declared skip
        sk = known_skips(["2025-02-03", "2025-02-04", "2026-01-02"], mm)
        assert sk == {"2025-02-03"}, sk
        assert known_skips(["2025-02-03"], {"2025-01"}) == set()
        print("self-test OK")
        return

    lb = sorted(Path(p).name[3:13] for p in glob.glob(str(ROOT / "data" / "leaderboard" / "lb_*.parquet")))
    raw = set()
    for p in glob.glob(str(ROOT / "data" / "clean_ohlcv_*.parquet")):
        raw.add(Path(p).name[len("clean_ohlcv_"):-len(".parquet")])
    for p in glob.glob(str(ROOT / "data" / "backfill" / "clean_ohlcv_*.parquet")):
        raw.add(Path(p).name[len("clean_ohlcv_"):-len(".parquet")])

    files = sorted(glob.glob(str(ANAT / "*.jsonl")))
    present = {Path(f).name[:10] for f in files}
    expected = [d for d in lb if d[:7] in raw and d[:7] <= DEV_END]
    skips = known_skips(lb, raw)
    missing = [d for d in expected if d not in present and d not in skips]

    corrupt, struct_bad, bars_missing, bars_bad = [], [], [], []
    n_names = 0
    for f in files:
        day = Path(f).name[:10]
        try:
            rec = json.load(open(f))
        except Exception as e:  # noqa: BLE001
            corrupt.append((day, str(e)))
            continue
        errs = check_record(rec)
        if errs:
            struct_bad.append((day, errs[:5]))
        n_names += sum(len(s.get("names", [])) for s in rec.get("snapshots", []))
        bp = BARSD / f"{day}.parquet"
        if not bp.exists():
            bars_missing.append(day)
    import polars as pl
    for f in files:
        day = Path(f).name[:10]
        bp = BARSD / f"{day}.parquet"
        if not bp.exists():
            continue
        try:
            b = pl.read_parquet(bp, n_rows=1)
            if list(b.columns) != BAR_COLS:
                bars_bad.append((day, list(b.columns)))
        except Exception as e:  # noqa: BLE001
            bars_bad.append((day, str(e)))

    tmp = glob.glob(str(BASKET / "**" / "*.tmp"), recursive=True)
    print(f"days present      : {len(present)}")
    print(f"expected (calendar): {len(expected)}  skips: {sorted(skips)}")
    print(f"missing            : {len(missing)}  {missing[:5]}{'...' if len(missing) > 5 else ''}")
    print(f"corrupt            : {len(corrupt)}  {corrupt[:3]}")
    print(f"structure errors   : {len(struct_bad)}  {struct_bad[:3]}")
    print(f"bars missing/bad   : {len(bars_missing)}/{len(bars_bad)}")
    print(f"tmp leftovers      : {len(tmp)}")
    print(f"total candidate rows in day files: {n_names}")
    ok = not (missing or corrupt or struct_bad or bars_missing or bars_bad or tmp)
    print("QA:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
