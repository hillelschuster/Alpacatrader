#!/usr/bin/env python3
"""SIP-consistent overnight shadow ledger (T7 sibling for the SIP regeneration).

For every filled main/adj member in the SIP anatomy day files:
    close     = member's stored close (SIP frame, last RTH close of the held day)
    next_open = o570 of the next AVAILABLE universe day (SIP first RTH bar open)
    gap       = next_open / close - 1
Diagnostic only - never used to repair the intraday result. No legacy tape is read.

Reads : <root>/anatomy/*.jsonl           (BASKET_ART_ROOT or --root)
        data/sip/universe/rth/*.parquet  (Layer-1 compact tables, symbol/o570/c_last)
Writes: <root>/agg/T7_overnight.json     (same schema as the legacy table)
Reserved months (2026-06..08) are refused; unresolved rows are counted by reason.

Usage:
  .venv/bin/python factory/scripts/basket_shadow_sip.py --self-test
  BASKET_ART_ROOT=factory/artifacts/basket/sip .venv/bin/python factory/scripts/basket_shadow_sip.py --write
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
UNIV = ROOT / "data" / "sip" / "universe" / "rth"
RESERVED = {"2026-06", "2026-07", "2026-08"}
PRIMARY_N = 3
UP_STRATA = [20, 30, 50]


def _root(arg_root: str | None) -> Path:
    if arg_root:
        return Path(arg_root)
    return Path(os.environ.get("BASKET_ART_ROOT", str(ROOT / "factory" / "artifacts" / "basket")))


def next_of(days: list, day: str):
    i = np.searchsorted(np.array(days), day, side="right")
    return days[i] if i < len(days) else None


def gap_of(close, next_open):
    if close is None or next_open is None or close <= 0:
        return None
    return round(float(next_open) / float(close) - 1.0, 6)


def _q(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return None
    a = np.array(v, dtype=float)
    return {"n": int(len(a)), "mean": round(float(a.mean()), 5),
            "p10": round(float(np.percentile(a, 10)), 5),
            "p50": round(float(np.percentile(a, 50)), 5),
            "p90": round(float(np.percentile(a, 90)), 5)}


def member_rows(files):
    rows = []
    for f in files:
        rec = json.load(open(f))
        day = rec["date"]
        for s in rec.get("snapshots", []):
            names = s["names"]
            for setname, mset in (("main", names[:PRIMARY_N]), ("adj", names[PRIMARY_N:2 * PRIMARY_N])):
                for n in mset:
                    fl = n.get("fill")
                    if not fl or fl.get("blocked"):
                        continue
                    rows.append({"date": day, "month": day[:7], "set": setname,
                                 "ticker": n["ticker"], "close": n.get("close"),
                                 "mfe": n.get("mfe"), "eod_ret": n.get("eod_ret")})
    return rows


def run(root: Path, write: bool):
    files = sorted(glob.glob(str(root / "anatomy" / "*.jsonl")))
    if not files:
        raise SystemExit(f"no anatomy day files under {root}")
    days = sorted(p.name[:10] for p in UNIV.glob("*.parquet"))
    rows = member_rows(files)
    need_next = {}
    for r in rows:
        nd = next_of(days, r["date"])
        need_next[(r["date"], r["ticker"])] = nd

    resolved, reasons = {}, defaultdict(int)
    cache: dict = {}  # next_day -> {symbol: (o570, delayed)}

    def next_table(nd: str):
        if nd not in cache:
            p = UNIV / f"{nd}.parquet"
            if not p.exists():
                cache[nd] = None
            else:
                df = pl.read_parquet(p, columns=["symbol", "o570", "delayed_open"])
                cache[nd] = {s: (o, d) for s, o, d in
                             zip(df["symbol"].to_list(), df["o570"].to_list(),
                                 df["delayed_open"].to_list())}
        return cache[nd]

    acc: dict = defaultdict(list)
    for r in rows:
        key = (r["date"], r["ticker"])
        nd = need_next.get(key)
        if nd is None:
            reasons["no_next_day"] += 1
            continue
        if nd[:7] in RESERVED:
            reasons["reserved"] += 1
            continue
        tab = next_table(nd)
        if not tab:
            reasons["no_next_table"] += 1
            continue
        got = tab.get(r["ticker"])
        if not got or got[0] is None:
            reasons["delayed_or_missing_open"] += 1
            continue
        if got[1]:
            reasons["delayed_open_flag"] += 1
        g = gap_of(r["close"], got[0])
        if g is None:
            reasons["bad_close"] += 1
            continue
        reasons["ok"] += 1
        base = (r["month"], r["set"])
        acc[base + ("all", "gap")].append(g)
        for cut in UP_STRATA:
            if r["mfe"] is not None and r["mfe"] >= cut / 100.0:
                acc[base + (f"mfe>={cut}", "gap")].append(g)
        if r["eod_ret"] is not None:
            acc[base + ("all", "gap|eod_ret")].append(
                (1.0 + float(r["eod_ret"])) * (1.0 + g) - 1.0)

    tables = []
    for (month, setname, stratum, stat), vals in sorted(acc.items()):
        q = _q(vals)
        if q:
            tables.append({"month": month, "set": setname, "stratum": stratum, "stat": stat, **q})
    out = {"source": "sip universe tables (next-day o570)", "reasons": dict(reasons), "tables": tables}
    if write:
        (root / "agg").mkdir(parents=True, exist_ok=True)
        with open(root / "agg" / "T7_overnight.json", "w") as fh:
            json.dump(out, fh, indent=1, default=str)
        print(f"wrote {root / 'agg' / 'T7_overnight.json'} rows={len(tables)} reasons={dict(reasons)}")
    else:
        print(f"dry-run rows={len(tables)} reasons={dict(reasons)}")
    return out


def selftest():
    days = ["2025-06-02", "2025-06-03", "2025-06-05"]
    assert next_of(days, "2025-06-02") == "2025-06-03"
    assert next_of(days, "2025-06-03") == "2025-06-05"
    assert next_of(days, "2025-06-05") is None
    assert gap_of(10.0, 11.0) == 0.1 and gap_of(10.0, None) is None
    assert gap_of(0, 5) is None and gap_of(10.0, 9.0) == -0.1
    assert _q([None, 0.1, -0.1])["n"] == 2
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    run(_root(args.root), args.write)


if __name__ == "__main__":
    main()
