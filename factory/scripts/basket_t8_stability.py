#!/usr/bin/env python3
"""BASKET-01 T8 — consolidated month/quarter stability artifact (one file).

One row per (block, view) where a block is a calendar month or a quarter and a view
is a (population, T) pair on the read surface. Headline numbers only, pooled from the
committed agg tables with the same day-weighted semantics as basket_read.py:

  containment   top1_in day-level share (T2)
  joint tail    touch k>=1 / k>=2 @30, above k>=1 @30/50/100 (T7b)
  frontier      F/Q at H30/L10, H30/L15, H100/L10 (T4b)
  member paths  MFE/MAE p50 (T5_member; block = median of the blocked months' p50s)
  economics     pays_net_allhold, pays_net_c3 (T7_payforteam, day-weighted)
  structure     filled/gap_blocked/unfilled slots (T6)

Quarter rows sum counters across the quarter's months and recompute ratios; quantiles
remain medians of monthly quantiles (stated, not hidden).

Output: <root>/agg/T8_stability.json

Usage:
  .venv/bin/python factory/scripts/basket_t8_stability.py --self-test
  BASKET_ART_ROOT=factory/artifacts/basket/sip .venv/bin/python factory/scripts/basket_t8_stability.py
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
ART = Path(os.environ.get("BASKET_ART_ROOT", str(ROOT / "factory" / "artifacts" / "basket")))
VIEWS = [("B", 600), ("B", 585), ("B", 720), ("A_open", 570), ("A_pm", 570)]
H_LADDER = [5, 10, 20, 30, 50, 100]


def load(root: Path, name: str):
    p = root / "agg" / f"{name}.json"
    return json.load(open(p)) if p.exists() else None


def quarter(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y}Q{(m - 1) // 3 + 1}"


def median(vals):
    v = [x for x in vals if x is not None]
    return None if not v else round(float(np.median(np.array(v, dtype=float))), 5)


def pooled_F_Q(rows):
    days = sum(r["days"] for r in rows)
    k0 = sum((r.get("k_hist") or [0, 0, 0, 0])[0] for r in rows)
    touched, qw = 0, 0.0
    for r in rows:
        q, t = r.get("Q_pess"), r.get("touched", 0) or 0
        if q is not None:
            touched += t
            qw += q * t
    return {"days": days, "F": round((days - k0) / days, 4) if days else None,
            "Q": round(qw / touched, 4) if touched else None}


def block_view(t2, t4b, t7b, t5m, t7e, t6, t11, months, pop, T):
    out = {}
    mset = set(months)
    for label in months:
        out[label] = {}
    for r in t2 or []:
        if r["pop"] == pop and r["T"] == T and r["month"] in mset:
            d = r.get("days") or 0
            out[r["month"]].update({
                "days": d,
                "cont_top1_in": round(r["top1_in"] / d, 4) if d else None,
                "cont_blocked": r.get("blocked_in", 0)})
    for r in t7b or []:
        if r["pop"] == pop and r["T"] == T and r["set"] == "main" and r["month"] in mset:
            b = out[r["month"]]
            tot = sum(r.get("touch_k_30", [0, 0, 0, 0]))
            b["touch30_k>=1"] = round((tot - r["touch_k_30"][0]) / tot, 4) if tot else None
            tot = sum(r.get("touch_k_50", [0, 0, 0, 0]))
            b["touch50_k>=1"] = round((tot - r["touch_k_50"][0]) / tot, 4) if tot else None
            tot = sum(r.get("touch_k_100", [0, 0, 0, 0]))
            b["touch100_k>=1"] = round((tot - r["touch_k_100"][0]) / tot, 4) if tot else None
            tot = sum(r.get("above_k_30", [0, 0, 0, 0]))
            b["above30_k>=1"] = round((tot - r["above_k_30"][0]) / tot, 4) if tot else None
            tot = sum(r.get("above_k_100", [0, 0, 0, 0]))
            b["above100_k>=1"] = round((tot - r["above_k_100"][0]) / tot, 4) if tot else None
    for r in t4b or []:
        if (r["pop"] == pop and r["T"] == T and r["set"] == "main"
                and r["month"] in mset and r["H"] in (30, 100)):
            b = out[r["month"]]
            b[f"F{r['H']}L{r['L']}"] = r["F_pess"]
            b[f"Q{r['H']}L{r['L']}"] = r["Q_pess"]
    for r in t5m or []:
        if r["pop"] == pop and r["T"] == T and r["set"] == "main" and r["month"] in mset:
            b = out[r["month"]]
            b["mfe_p50"] = (r.get("mfe") or {}).get("p50")
            b["mae_p50"] = (r.get("mae") or {}).get("p50")
    for r in (t7e or {}).get("monthly", []):
        if r["pop"] == pop and r["T"] == T and r["month"] in mset:
            b = out[r["month"]]
            b["pays_net"] = r.get("pays_net")
            b["pays_net_c3"] = r.get("pays_net_c3")
    for r in t6 or []:
        if r["pop"] == pop and r["T"] == T and r["set"] == "main" and r["month"] in mset:
            b = out[r["month"]]
            for k in ("filled", "gap_blocked", "unfilled"):
                b[f"{k}_slots"] = r.get(k, 0)
    for r in (t11 or {}).get("monthly", []):
        if r["pop"] == pop and r["T"] == T and r["month"] in mset:
            out[r["month"]]["ordinary_lt10"] = r.get("ordinary_lt10")
    return out


def finalize(root: Path):
    t2 = load(root, "T2")
    t4b = load(root, "T4b_frontier")
    t7b = load(root, "T7b")
    t5m = load(root, "T5_member")
    t7e = load(root, "T7_payforteam")
    t6 = load(root, "T6")
    t11 = load(root, "T11_dist")
    months = sorted({r["month"] for r in (t2 or [])})
    qmonths = defaultdict(list)
    for m in months:
        qmonths[quarter(m)].append(m)
    tables = []
    for pop, T in VIEWS:
        mv = block_view(t2, t4b, t7b, t5m, t7e, t6, t11, months, pop, T)
        for m in months:
            tables.append({"block": m, "pop": pop, "T": T, **mv[m]})
        for q, ms in sorted(qmonths.items()):
            rows = [mv[m] for m in ms]
            agg = {}
            for key in ("days", "cont_blocked", "filled_slots", "gap_blocked_slots",
                        "unfilled_slots"):
                vals = [r.get(key) for r in rows if r.get(key) is not None]
                if vals:
                    agg[key] = sum(vals)
            d = agg.get("days")
            if d:
                num = sum((r.get("cont_top1_in") or 0) * (r.get("days") or 0) for r in rows)
                agg["cont_top1_in"] = round(num / d, 4)
            for key in ("touch30_k>=1", "touch50_k>=1", "touch100_k>=1",
                        "above30_k>=1", "above100_k>=1", "pays_net", "pays_net_c3"):
                vals = [r.get(key) for r in rows if r.get(key) is not None]
                w = [r.get("days") or 0 for r in rows if r.get(key) is not None]
                if vals and sum(w):
                    agg[key] = round(sum(v * ww for v, ww in zip(vals, w)) / sum(w), 4)
            for key in ("F30L10", "Q30L10", "F30L15", "Q30L15", "F100L10",
                        "mfe_p50", "mae_p50"):
                vals = [r.get(key) for r in rows if r.get(key) is not None]
                if vals:
                    agg[key] = median(vals)
            tables.append({"block": q, "pop": pop, "T": T, **agg})
    return {"views": [f"{p}/{T}" for p, T in VIEWS], "months": months,
            "blocks": tables,
            "method": "month rows from the agg tables; quarter rows sum counters and "
                      "recompute ratios; quantiles are medians of monthly quantiles",
            "_producer": "basket_t8_stability.py"}


def selftest():
    assert quarter("2021-02") == "2021Q1" and quarter("2023-12") == "2023Q4"
    t2 = [{"month": "2021-02", "pop": "B", "T": 600, "N": 3, "days": 19,
           "top1_in": 5, "blocked_in": 1}]
    t4b = [{"month": "2021-02", "pop": "B", "T": 600, "set": "main", "H": 30, "L": 10,
            "F_pess": 0.25, "Q_pess": 0.8, "days": 19, "k_hist": [14, 5, 0, 0],
            "touched": 7}]
    t7b = [{"month": "2021-02", "pop": "B", "T": 600, "set": "main",
            "touch_k_30": [10, 6, 3, 0], "touch_k_50": [15, 4, 0, 0],
            "touch_k_100": [18, 1, 0, 0], "above_k_30": [14, 5, 0, 0],
            "above_k_100": [18, 1, 0, 0]}]
    mv = block_view(t2, t4b, t7b, [], [], [], None, ["2021-02"], "B", 600)
    b = mv["2021-02"]
    assert b["days"] == 19 and b["cont_top1_in"] == round(5 / 19, 4), b
    assert b["touch30_k>=1"] == round(9 / 19, 4), b
    assert b["F30L10"] == 0.25 and b["touch100_k>=1"] == round(1 / 19, 4), b
    empty = block_view([], [], [], [], [], [], None, ["2021-02"], "B", 600)
    assert empty["2021-02"] == {}
    assert median([1.0, None, 3.0]) == 2.0
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ART))
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    root = Path(args.root)
    out = finalize(root)
    (root / "agg").mkdir(parents=True, exist_ok=True)
    with open(root / "agg" / "T8_stability.json", "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(f"T8_stability: {len(out['blocks'])} rows ({len(out['months'])} months, "
          f"{len(out['views'])} views) -> {root/'agg'/'T8_stability.json'}")


if __name__ == "__main__":
    main()
