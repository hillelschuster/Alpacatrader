#!/usr/bin/env python3
"""BASKET-01 SIP acquisition net-size measurement (decision support, no research change).

Quantifies how large the SIP fetch universe must be so that SIP's own top-10 at each
frozen decision time cannot be hidden by the legacy tape's sparser bars, without
fetching the whole market. Two candidate definitions are measured:

  floor net   : all PIT-eligible names with decision-time gain >= f at ANY frozen T
                (anchor = max(gain vs RTH open, gain vs previous close))
  cutoff net  : names within `margin` of the legacy 10th-ranked gain at each T
                (margin covers measured decision-price error: p50 ~1bp, p90 ~24bps,
                 max ~266bps in the certification panel)

Result (8 era-spread days, see net_size.json): floor@2% = 1587-3162 names/day;
cutoff net @1% = 11-23 names/day (4-13 beyond the legacy union). The floor net is
therefore rejected as impractical; the cutoff-margin net is the recommendation.

Measurement only; no selection rule, no thesis change. Usage:
  --days D [D ...]   (default: the 8 sampled days)   --json PATH   --self-test
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import datetime as dt
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_anatomy as ba  # noqa: E402

DEFAULT_DAYS = ["2021-02-01", "2021-10-25", "2022-03-10", "2023-03-16",
                "2025-03-24", "2025-09-09", "2025-10-30", "2026-05-15"]
FLOORS = [0.02, 0.05, 0.10]
MARGINS = [0.005, 0.01, 0.02]
TOPK = 10


def cutoff_net(rows: list, margin: float, k: int = TOPK) -> set:
    """rows: list of (name, score) -> names within margin of the k-th ranked score."""
    if len(rows) < k:
        return set()
    s = sorted((v for _, v in rows), reverse=True)
    cut = s[k - 1] - margin
    return set(n for n, v in rows if v >= cut)


def day_scores(day: str, pcache: dict):
    d = dt.date.fromisoformat(day)
    df = ba.load_month_lazy(day[:7]).filter(pl.col("date") == d).collect().unique(
        subset=["timestamp", "ticker"], keep="first")
    if df.height == 0:
        return None
    elig = ba.pit_elig(day)
    el = df.filter(pl.col("ticker").is_in(list(elig)))
    o570 = dict(zip(el.filter(pl.col("et") == 570)["ticker"].to_list(),
                    el.filter(pl.col("et") == 570)["open"].to_list()))
    pm = ba.add_month(day[:7], -1)
    if pm not in pcache:
        pcache[pm] = ba.last_closes_of_month(pm)
    pc = pcache[pm]
    per_T = []
    for T in ba.T_LIST:
        dec = el.filter(pl.col("et") <= T - 1).group_by("ticker").agg(
            pl.col("close").sort_by("et").last().alias("px"))
        rows = []
        for t, px in zip(dec["ticker"].to_list(), dec["px"].to_list()):
            o = o570.get(t); p = pc.get(t)
            if not o or not p or not px:
                continue
            rows.append((t, max(px / o - 1, px / p - 1)))
        per_T.append(rows)
    return {"elig": len(elig), "rows": df.height, "per_T": per_T}


def measure(day: str, pcache: dict, legacy: set):
    got = day_scores(day, pcache)
    if got is None:
        return None
    per_T = got["per_T"]
    out = {"day": day, "elig": got["elig"], "dayrows": got["rows"]}
    for f in FLOORS:
        names = set()
        for rows in per_T:
            names.update(t for t, v in rows if v >= f)
        out[f"floor{int(f*100)}"] = len(names)
        out[f"floor{int(f*100)}_new"] = len(names - legacy)
    for m in MARGINS:
        names = set()
        for rows in per_T:
            names.update(cutoff_net(rows, m))
        out[f"cut{m}"] = len(names)
        out[f"cut{m}_new"] = len(names - legacy)
    # legacy cutoff reference: union of per-T top-10 membership
    top10 = set()
    for rows in per_T:
        top10.update(t for t, _ in sorted(rows, key=lambda r: -r[1])[:TOPK])
    out["top10_union_B"] = len(top10)
    return out


def load_legacy(day: str) -> set:
    p = ROOT / "factory" / "artifacts" / "basket" / "anatomy" / f"{day}.jsonl"
    if not p.exists():
        return set()
    rec = json.load(open(p))
    names = set()
    for s in rec["snapshots"]:
        names.update(n["ticker"] for n in s["names"])
    return names


def selftest():
    rows = [("A", 0.20), ("B", 0.15), ("C", 0.12), ("D", 0.10), ("E", 0.09), ("F", 0.08),
            ("G", 0.07), ("H", 0.06), ("I", 0.05), ("J", 0.04), ("K", 0.035), ("L", 0.02)]
    s = cutoff_net(rows, 0.01, k=10)
    assert s == {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K"}, s   # cutoff 0.04
    assert "L" not in s
    assert cutoff_net(rows[:9], 0.01) == set()
    assert cutoff_net(rows, 0.0, k=1) == {"A"}
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="*", default=DEFAULT_DAYS)
    ap.add_argument("--json", default=str(ROOT / "factory" / "artifacts" / "basket" / "sip" / "net_size.json"))
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    pcache = {}
    rows = []
    for day in args.days:
        leg = load_legacy(day)
        out = measure(day, pcache, leg)
        if out:
            rows.append(out)
            print(f"{day}: elig={out['elig']} legacy={len(leg)} top10B={out['top10_union_B']} "
                  f"floor2={out['floor2']} floor5={out['floor5']} floor10={out['floor10']} | "
                  f"cut0.5={out['cut0.005']} cut1={out['cut0.01']} cut2={out['cut0.02']} "
                  f"(new1={out['cut0.01_new']})")
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    with open(args.json, "w") as fh:
        json.dump({"days": rows, "floors": FLOORS, "margins": MARGINS, "topk": TOPK}, fh, indent=1)
    print(f"wrote {args.json} ({len(rows)} days)")


if __name__ == "__main__":
    main()
