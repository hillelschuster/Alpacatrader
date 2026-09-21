#!/usr/bin/env python3
"""BASKET-01 market-opportunity base rates + funnel (work-order item 11).

Answers, for the same 1,066 dev days and the full PIT universe:
  "how often did the market offer >=1 mover at +20/+30/+50/+100/+200%?"
under TWO anchors, kept separate:
  open      : session high / RTH open (o570) - 1     (the B-basket anchor)
  prev_close: session high / previous close - 1      (the A_open anchor)

and then funnels that opportunity through the basket's own committed tables:
  market offered -> in our top-3? -> what remained after entry? -> did any member supply it?

Measurement only. Rulers (+20..+200) are descriptive measurement rulers, never
targets. No parameter is selected here. Full-universe = every row of the SIP
compact table (the PIT-eligible universe built by sip_universe.py).

Output: <root>/agg/market_base_rates.json      (<root> = BASKET_ART_ROOT or --root)

Usage:
  .venv/bin/python factory/scripts/basket_market_base_rates.py --self-test
  BASKET_ART_ROOT=factory/artifacts/basket/sip .venv/bin/python factory/scripts/basket_market_base_rates.py --write
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
H_LADDER = [20, 30, 50, 100, 200]
MIN_PRICE = 1.0
COLS = ["symbol", "o570", "hi", "c_last", "delayed_open"]
FUNNEL_VIEWS = [("B", 600), ("A_open", 570)]
FUNNEL_H = [30, 50, 100]


def art_root(arg=None) -> Path:
    if arg:
        return Path(arg)
    env = os.environ.get("BASKET_ART_ROOT")
    return Path(env) if env else ROOT / "factory" / "artifacts" / "basket"


def dev_days(anat: Path) -> list:
    return sorted(Path(f).name[:10] for f in glob.glob(str(anat / "*.jsonl")))


def all_days() -> list:
    return sorted(p.stem for p in UNIV.glob("*.parquet"))


def prev_of(days: list, day: str):
    i = np.searchsorted(np.array(days), day, side="left") - 1
    return days[i] if i >= 0 else None


def day_stats(df: pl.DataFrame, prev: dict, day: str) -> dict:
    """One day's two-anchor base rates. prev = {symbol: prev close}.

    Canonical eligibility (2026-09-21, aligned with the capture funnel): the frozen
    universe floor is applied to TRADEABILITY AT THE OPEN (o570 >= MIN_PRICE) for both
    anchors; the prev-anchor additionally needs prev_close > 0 to be measurable. The
    earlier provisional rule (floor on the prev-close basis itself, o570 unconstrained)
    is preserved as `prev_close_prevfloor` (sensitivity), not as a headline.
    """
    got = set(df.columns)
    missing = [c for c in COLS if c not in got]
    if missing:
        raise SystemExit(f"{day}: universe table missing columns {missing}; has {sorted(got)}")
    sym = df["symbol"].to_list()
    o570 = df["o570"].to_list()
    hi = df["hi"].to_list()
    prov = [prev.get(s) for s in sym]
    out: dict = {"date": day, "n_symbols": len(sym),
           "n_delayed_open": sum(1 for x in o570 if x is None),
           "n_no_prev": sum(1 for p in prov if p is None or p <= 0),
           "open": {}, "prev_close": {}}
    for anchor, basis in (("open", o570), ("prev_close", prov)):
        gains = []
        for o, h, b in zip(o570, hi, basis):
            if h is None or b is None or o is None or o < MIN_PRICE:
                continue  # not tradeable at the open: outside the frozen universe
            if b <= 0:
                continue
            gains.append(h / b - 1.0)
        if not gains:
            out[anchor] = {"n_ge": {str(H): 0 for H in H_LADDER}, "max": None,
                           "n_eligible": 0}
            continue
        a = np.array(gains, dtype=float)
        out[anchor] = {
            "n_eligible": int(len(a)),
            "max": round(float(a.max()), 4),
            "n_ge": {str(H): int((a >= H / 100.0).sum()) for H in H_LADDER},
            "median_eligible": round(float(np.median(a)), 4),
        }
    # sensitivity: the earlier provisional prev-anchor floor (prev >= MIN_PRICE, o570
    # unconstrained). Kept so the previously published 0.747 number stays traceable.
    s = [h / b - 1.0 for h, b in zip(hi, prov)
         if h is not None and b is not None and b >= MIN_PRICE]
    out["prev_close_prevfloor"] = {
        "n_eligible": int(len(s)),
        "max": round(float(max(s)), 4) if s else None,
        "n_ge": {str(H): int(sum(1 for g in s if g >= H / 100.0)) for H in H_LADDER}}
    return out


def pooled(per_day: list, anchor: str) -> dict:
    days = [d for d in per_day if d[anchor]["max"] is not None]
    n = len(days) or 1
    res: dict = {"days_evaluated": len(days),
           "days_no_eligible": len(per_day) - len(days)}
    for H in H_LADDER:
        k = str(H)
        hit = [d for d in days if d[anchor]["n_ge"][k] > 0]
        counts = [d[anchor]["n_ge"][k] for d in hit]
        res[k] = {"days": len(hit), "share": round(len(hit) / n, 4),
                  "mean_names_on_hit_days": round(float(np.mean(counts)), 3) if counts else 0.0}
    return res


def monthly_rows(per_day: list) -> list:
    by = defaultdict(list)
    for d in per_day:
        by[d["date"][:7]].append(d)
    rows = []
    for m, ds in sorted(by.items()):
        row: dict = {"month": m, "days": len(ds)}
        for anchor in ("open", "prev_close"):
            ev = [d for d in ds if d[anchor]["max"] is not None]
            row[anchor] = {"days_evaluated": len(ev)}
            for H in H_LADDER:
                row[anchor][str(H)] = sum(1 for d in ev if d[anchor]["n_ge"][str(H)] > 0)
        rows.append(row)
    return rows


def funnel(root: Path) -> dict:
    """Sourced from committed tables only; every field names its source key."""
    out: dict = {"_sources": {
        "contained_share": "agg/T2.json rows (pop,T,N=3): sum(top1_in)/sum(days)",
        "any_member_tail_share": "agg/T7b.json rows (pop,T,set=main): (sum(hist)-hist[0])/sum(hist)",
    }, "views": {}}
    t2p, t7p = root / "agg" / "T2.json", root / "agg" / "T7b.json"
    if not t2p.exists() or not t7p.exists():
        out["missing_source"] = [str(p) for p in (t2p, t7p) if not p.exists()]
        return out
    t2 = json.load(open(t2p))
    t7b = json.load(open(t7p))
    for pop, T in FUNNEL_VIEWS:
        key = f"{pop}/{T}"
        c_days = sum(r["days"] for r in t2 if r["pop"] == pop and r["T"] == T and r["N"] == 3)
        c_in = sum(r["top1_in"] for r in t2 if r["pop"] == pop and r["T"] == T and r["N"] == 3)
        rows7 = [r for r in t7b if r["pop"] == pop and r["T"] == T and r["set"] == "main"]
        days7 = sum(r["days"] for r in rows7)
        entry = {"days": c_days,
                 "contained_share": round(c_in / c_days, 4) if c_days else None,
                 "contained_n": c_in}
        for H in FUNNEL_H:
            for lens in ("touch", "above"):
                hist = [sum(r.get(f"{lens}_k_{H}", [0, 0, 0, 0])[i] for r in rows7)
                        for i in range(4)]
                tot = sum(hist)
                entry[f"{lens}_{H}_k>=1"] = round((tot - hist[0]) / tot, 4) if tot else None
        entry["days_t7b"] = days7
        out["views"][key] = entry
    return out


def build(root: Path, write: bool):
    per_day = []
    days = all_days()
    dev = set(dev_days(root / "anatomy"))
    if not dev:
        raise SystemExit(f"no anatomy day files under {root/'anatomy'}")
    prev: dict = {}
    n_prev_tables = 0
    for d in days:
        df = pl.read_parquet(UNIV / f"{d}.parquet", columns=COLS)
        if d in dev:
            per_day.append(day_stats(df, prev, d))
        prev = {s: c for s, c in zip(df["symbol"].to_list(), df["c_last"].to_list())
                if c is not None and c > 0}
        n_prev_tables += 1
    p = {
        "method": ("full-PIT SIP compact universe rows (sip_universe.py); dev days = the "
                   f"{len(dev)} anatomy days; open anchor = hi/o570-1 with o570>=${MIN_PRICE}; "
                   "prev_close anchor = hi/prev-1 with o570>=$1 (tradeable at the open) and "
                   "prev>0 (prev = previous available SIP table's c_last, seeds 2021-01-29 / "
                   "2025-01-31)"),
        "rulers": H_LADDER, "root": str(root),
        "days": len(per_day), "universe_tables_read": n_prev_tables,
        "anchors": {"open": pooled(per_day, "open"),
                    "prev_close": pooled(per_day, "prev_close")},
        "anchors_sensitivity": {"prev_close_prevfloor": pooled(per_day, "prev_close_prevfloor")},
        "per_day": per_day,
        "monthly": monthly_rows(per_day),
        "funnel": funnel(root),
        "notes": ["Descriptive base rates; no parameter selected, no strategy implied.",
                  "The basket's own +H numbers are post-ENTRY and fill-anchored; these base "
                  "rates are session-anchored and must never be subtracted from them.",
                  "Eligibility (2026-09-21): the $1 floor is applied to tradeability at the "
                  "open (o570 >= $1) for BOTH anchors; prev-anchor additionally needs "
                  "prev_close > 0. `anchors_sensitivity.prev_close_prevfloor` preserves the "
                  "earlier provisional rule (floor on the prev-close basis), whose +100 share "
                  "was 0.747."],
    }
    if write:
        (root / "agg").mkdir(parents=True, exist_ok=True)
        with open(root / "agg" / "market_base_rates.json", "w") as fh:
            json.dump(p, fh, indent=1, default=str)
        print(f"market_base_rates: {len(per_day)} days -> {root/'agg'/'market_base_rates.json'}")
    else:
        print(f"dry-run: {len(per_day)} days (use --write to persist)")
    for anchor in ("open", "prev_close"):
        print(f"[{anchor}] " + "  ".join(
            f"H{H}: {p['anchors'][anchor][str(H)]['share']:.3f}"
            for H in H_LADDER))
    for k, v in p["funnel"].get("views", {}).items():
        print(f"[funnel {k}] contained={v['contained_share']} "
              f"touch30={v.get('touch_30_k>=1')} above30={v.get('above_30_k>=1')}")
    return p


def selftest():
    df = pl.DataFrame({
        "symbol": ["A", "B", "C", "D", "E"],
        "o570": [10.0, 20.0, 5.0, None, 8.0],
        "hi": [13.0, 21.0, 11.0, 9.5, 8.4],
        "c_last": [11.0, 20.5, 4.5, 9.0, 8.1],
        "delayed_open": [False, False, False, True, False],
    })
    st = day_stats(df, {"A": 9.0, "B": 25.0, "C": 4.0, "E": 8.0}, "2021-02-01")
    # open anchor: A 13/10-1=+.30, B 21/20-1=+.05, C 11/5-1=+1.20, E 8.4/8-1=+.05
    # D excluded (no o570). So >=20: A,C ; >=50: C ; >=100: C
    assert st["open"]["n_eligible"] == 4, st
    assert st["open"]["n_ge"]["20"] == 2 and st["open"]["n_ge"]["30"] == 2, st
    assert st["open"]["n_ge"]["50"] == 1, st
    assert st["open"]["n_ge"]["100"] == 1, st        # C = +120%
    assert st["n_delayed_open"] == 1, st
    # prev anchor (D has no prev in the map): C 11/4-1=+1.75 (max), A 13/9-1=+.44
    assert st["prev_close"]["n_eligible"] == 4, st
    assert abs(st["prev_close"]["max"] - 1.75) < 1e-9, st["prev_close"]["max"]
    assert st["prev_close"]["n_ge"]["100"] == 1, st
    assert st["n_no_prev"] == 1, st
    # a symbol priced under $1 is not eligible on either anchor
    df2 = pl.DataFrame({"symbol": ["X", "Y", "Z"], "o570": [0.5, 2.0, 0.5],
                        "hi": [0.6, 3.0, 4.0], "c_last": [0.5, 2.0, 2.0],
                        "delayed_open": [False, False, False]})
    st2 = day_stats(df2, {"X": 0.4, "Y": 2.0, "Z": 2.0}, "2021-02-02")
    assert st2["open"]["n_eligible"] == 1 and st2["prev_close"]["n_eligible"] == 1, st2
    # sensitivity keeps the old rule: Z (prev 2.0, o570 0.5) counts there, not canonically
    assert st2["prev_close_prevfloor"]["n_eligible"] == 2, st2
    assert st2["prev_close_prevfloor"]["n_ge"]["100"] == 1, st2
    assert st2["prev_close"]["n_ge"]["100"] == 0, st2
    pl_pool = pooled([st, {"date": "2021-02-02", "open": {"max": None, "n_ge": {str(H): 0 for H in H_LADDER}},
                           "prev_close": {"max": None, "n_ge": {str(H): 0 for H in H_LADDER}}}], "open")
    assert pl_pool["days_evaluated"] == 1 and pl_pool["days_no_eligible"] == 1, pl_pool
    assert pl_pool["20"]["share"] == 1.0, pl_pool
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
    build(art_root(args.root), args.write)


if __name__ == "__main__":
    main()
