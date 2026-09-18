#!/usr/bin/env python3
"""BASKET-01 read layer — full daily basket anatomy + continuous outcome distributions.

Owner re-centering directive (2026-09-17): the +5..+100 rulers are measurement tools,
never ontology. This pass describes what happened to ALL THREE original tickets on
every day: the complete (continuous) excursion distribution including extremes far
beyond +100%, 0/1/2/3-mover frequencies across the ruler ladder, ordinary days, the
failed-ticket burden, and member-level executable accessibility.

Reads only committed anatomy day files (no extractor change, no regeneration, no
strategy code, no release/survivor rules). Rulers remain rulers.

The pay-for-participation arithmetic included here (does the best ticket's raw
excursion cover the other tickets' worst adverse excursions?) is explicitly an
ex-post illustration, not a policy.

Output: factory/artifacts/basket/agg/T11_dist.json

Usage:
  .venv/bin/python factory/scripts/basket_dist.py --self-test
  .venv/bin/python factory/scripts/basket_dist.py --months 2021-02
  .venv/bin/python factory/scripts/basket_dist.py              # all present days
"""
from __future__ import annotations

import argparse
import glob
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
import os
ART = Path(os.environ.get("BASKET_ART_ROOT", str(ROOT / "factory" / "artifacts" / "basket")))
ANAT = ART / "anatomy"
OUT = ART / "agg"

LADDER = [5, 10, 20, 30, 50, 100]      # frozen rulers (measurement tools only)
EXT = [150, 200, 300]                  # descriptive extension, same status
DN = [3, 5, 8, 10, 15]
EDGES = [0, 0.05, 0.10, 0.20, 0.30, 0.50, 1.00, 2.00, 3.00, 5.00]
BANDS = ["<0", "0-5", "5-10", "10-20", "20-30", "30-50", "50-100",
         "100-200", "200-300", "300-500", "500+"]
PRIMARY_N = 3
MONTHLY_KEYS = [("A_open", 570), ("B", 585), ("B", 600), ("B", 720)]
TOP_EXTREMES = 40


def band(x: float) -> int:
    return int(np.digitize([x], EDGES)[0])


def _q(vals, ps=(50, 90, 95, 99)):
    v = [x for x in vals if x is not None]
    if not v:
        return None
    a = np.array(v, dtype=float)
    out = {"n": int(len(a)), "max": round(float(a.max()), 4)}
    for p in ps:
        out[f"p{p}"] = round(float(np.percentile(a, p)), 4)
    return out


def new_pt():
    d: dict = {k: [] for k in ("max_mfe", "second_mfe", "third_mfe", "member_mfe",
                               "member_mae", "member_eod", "o_mfe", "o_mae")}
    for H in LADDER + EXT:
        d[f"k_touch_{H}"] = []
        d[f"k_exec_{H}"] = []
        d[f"reach_{H}"] = 0
        d[f"exec_{H}"] = 0
    for L in DN:
        d[f"dn_touch_{L}"] = 0
    for k in ("days", "days_lt3", "days_empty", "o_days", "cover_days"):
        d[k] = 0
    d["cover_ratio"] = []
    return d


def add_rec(rec, per_pt, monthly, extremes):
    month = rec["date"][:7]
    for s in rec["snapshots"]:
        pop, T = s["pop"], s["T"]
        pt = per_pt[(pop, T)]
        names = s["names"][:PRIMARY_N]
        filled = [n for n in names if n.get("fill") and not n["fill"].get("blocked")]
        pt["days"] += 1
        if len(filled) < PRIMARY_N:
            pt["days_lt3"] += 1
        if not filled:
            pt["days_empty"] += 1
            continue
        # pairs keep each member's MFE and MAE together; pairs[1:] excludes the best-MFE member
        pairs = sorted(((float(n["mfe"]), float(n["mae"])) for n in filled),
                       key=lambda x: x[0], reverse=True)
        mfes = [p[0] for p in pairs]
        maes = [p[1] for p in pairs]
        pt["max_mfe"].append(mfes[0])
        pt["second_mfe"].append(mfes[1] if len(mfes) > 1 else None)
        pt["third_mfe"].append(mfes[2] if len(mfes) > 2 else None)
        pt["member_mfe"].extend(mfes)
        pt["member_mae"].extend(maes)
        pt["member_eod"].extend(float(n["eod_ret"]) for n in filled)
        others_adverse = sum(-m for _, m in pairs[1:]) if len(pairs) > 1 else 0.0
        pt["cover_ratio"].append(mfes[0] / others_adverse if others_adverse > 0 else None)
        pt["cover_days"] += int(mfes[0] >= others_adverse)
        # ordinary days: day max below 10%
        if mfes[0] < 0.10:
            pt["o_days"] += 1
            pt["o_mfe"].extend(mfes)
            pt["o_mae"].extend(maes)
        for n in filled:
            for L in DN:
                pt[f"dn_touch_{L}"] += int(n["ladders"]["dn"][str(L)] is not None)
            for H in LADDER:
                u = n["ladders"]["up"].get(str(H))
                if u is not None:
                    pt[f"reach_{H}"] += 1
                    if u.get("exec") is not None:
                        pt[f"exec_{H}"] += 1
            for H in EXT:  # beyond the stored ladder: mfe-based touch only (exec unmeasurable)
                if float(n["mfe"]) >= H / 100.0:
                    pt[f"reach_{H}"] += 1
        for H in LADDER:
            k = sum(1 for n in filled if n["ladders"]["up"].get(str(H)) is not None)
            ke = sum(1 for n in filled
                     if (n["ladders"]["up"].get(str(H)) or {}).get("exec") is not None)
            pt[f"k_touch_{H}"].append(k)
            pt[f"k_exec_{H}"].append(ke)
        for H in EXT:
            pt[f"k_touch_{H}"].append(
                sum(1 for n in filled if float(n["mfe"]) >= H / 100.0))
        for n in filled:
            extremes.append({
                "date": rec["date"], "ticker": n["ticker"], "pop": pop, "T": T,
                "mfe": float(n["mfe"]), "mae": float(n["mae"]),
                "eod_ret": float(n["eod_ret"]), "fill": float(n["fill"]["px"]),
                "day_hi": n.get("day_high"), "split_flag": bool(n.get("split_flag")),
                "ratio_anchor": n.get("ratio_anchor"),
                "exec_max_H": max((H for H in LADDER
                                   if (n["ladders"]["up"].get(str(H)) or {}).get("exec")
                                   is not None), default=None),
            })
        if (pop, T) in MONTHLY_KEYS:
            m = monthly[(month, pop, T)]
            m["days"] += 1
            m["max_ge30"] += int(mfes[0] >= 0.30)
            m["max_ge50"] += int(mfes[0] >= 0.50)
            m["max_ge100"] += int(mfes[0] >= 1.00)
            m["m2_ge30"] += int(len(mfes) > 1 and mfes[1] >= 0.30)
            m["m3_ge30"] += int(len(mfes) > 2 and mfes[2] >= 0.30)
            m["ordinary_lt10"] += int(mfes[0] < 0.10)


def finalize(per_pt, monthly, extremes):
    rows = []
    for (pop, T), pt in sorted(per_pt.items()):
        if pt["days"] == 0:
            continue
        n_members = len(pt["member_mfe"])
        bands = [0] * len(BANDS)
        for x in pt["member_mfe"]:
            bands[band(x)] += 1
        mbands = [0] * len(BANDS)
        for x in pt["max_mfe"]:
            mbands[band(x)] += 1
        row = {
            "pop": pop, "T": T, "days": pt["days"], "days_lt3": pt["days_lt3"],
            "days_empty": pt["days_empty"], "members_filled": n_members,
            "bands": BANDS,
            "member_mfe_bands": bands,
            "day_max_bands": mbands,
            "member_mfe_q": _q(pt["member_mfe"]),
            "day_max_q": _q(pt["max_mfe"]),
            "day_second_q": _q(pt["second_mfe"]),
            "day_third_q": _q(pt["third_mfe"]),
            "member_mae_q": _q(pt["member_mae"]),
            "member_eod_q": _q(pt["member_eod"]),
            "reach": {str(H): pt[f"reach_{H}"] for H in LADDER + EXT},
            "exec": {str(H): (pt[f"exec_{H}"] if H in LADDER else None)
                     for H in LADDER + EXT},
            "dn_touch": {str(L): pt[f"dn_touch_{L}"] for L in DN},
            "k_touch": {str(H): _hist(pt[f"k_touch_{H}"]) for H in LADDER + EXT},
            "k_exec": {str(H): (_hist(pt[f"k_exec_{H}"]) if H in LADDER else None)
                       for H in LADDER + EXT},
            "ordinary": {"days": pt["o_days"], "share": round(pt["o_days"] / pt["days"], 4),
                         "member_mfe_q": _q(pt["o_mfe"]), "member_mae_q": _q(pt["o_mae"])},
            "cover": {"days": pt["cover_days"],
                      "share": round(pt["cover_days"] / pt["days"], 4),
                      "ratio_q": _q(pt["cover_ratio"])},
        }
        rows.append(row)
    monthly_rows = [{"month": m, "pop": p, "T": T, **v}
                    for (m, p, T), v in sorted(monthly.items())]
    extremes = sorted(extremes, key=lambda r: r["mfe"], reverse=True)[:TOP_EXTREMES]
    return {"bands": BANDS, "ladder": LADDER, "extension": EXT, "dn": DN,
            "per_pop_T": rows, "monthly": monthly_rows, "extremes_top": extremes}


def _hist(ks):
    h = [0, 0, 0, 0]
    for k in ks:
        h[min(int(k), 3)] += 1
    return h


def selftest():
    assert band(-0.1) == 0 and band(0.01) == 1 and band(0.35) == 5
    assert band(1.2) == 7 and band(6.0) == 10
    assert _hist([0, 1, 2, 3, 3]) == [1, 1, 1, 2]
    pt = defaultdict(new_pt)
    monthly = defaultdict(lambda: defaultdict(int))
    extremes = []

    def mk(tkr, mfe, mae, up_max, exec_h=()):
        return {"ticker": tkr, "fill": {"px": 10.0, "blocked": False}, "mfe": mfe,
                "mae": mae, "eod_ret": mfe / 2,
                "ladders": {"up": {str(H): ({"exec": 11.0 if H in exec_h else None}
                                            if H <= up_max else None)
                                   for H in LADDER + EXT},
                            "dn": {str(L): ({"exec": 9.0} if L == 5 else None) for L in DN}}}
    rec = {"date": "2025-06-02", "snapshots": [
        {"pop": "B", "T": 600, "names": [
            mk("AAA", 0.35, -0.10, up_max=50, exec_h=(30,)), mk("BBB", 0.05, -0.20, up_max=5),
            {"ticker": "CCC", "fill": {"px": 10.0, "blocked": True}, "mfe": None, "mae": None,
             "eod_ret": None, "ladders": {"up": {}, "dn": {}}}]},
        {"pop": "B", "T": 630, "names": [
            mk("DDD", 1.60, -0.05, up_max=50, exec_h=(30,))]}]}
    add_rec(rec, pt, monthly, extremes)
    out = finalize(pt, monthly, extremes)
    row = out["per_pop_T"][0]
    assert row["days"] == 1 and row["days_lt3"] == 1 and row["members_filled"] == 2, row
    assert row["day_max_bands"][5] == 1, row["day_max_bands"]      # 0.35 -> band 30-50
    assert row["member_mfe_bands"][2] == 1, row["member_mfe_bands"]  # 0.05 -> band 5-10 (edges right-open)
    assert row["k_touch"]["30"] == [0, 1, 0, 0]
    assert row["k_exec"]["30"] == [0, 1, 0, 0]
    assert row["k_exec"]["50"] == [1, 0, 0, 0]                     # exec None for H>=50
    assert row["reach"]["30"] == 1 and row["exec"]["30"] == 1
    assert row["dn_touch"]["5"] == 2 and row["dn_touch"]["10"] == 0
    assert row["ordinary"]["days"] == 0
    assert row["cover"]["days"] == 1                               # 0.35 >= 0.20 adverse
    assert out["extremes_top"][0]["ticker"] == "DDD"
    assert len(out["monthly"]) == 1  # only (B,600) is in MONTHLY_KEYS
    row2 = next(r for r in out["per_pop_T"] if r["T"] == 630)
    assert row2["k_touch"]["150"] == [0, 1, 0, 0], row2["k_touch"]["150"]   # mfe-based extension
    assert row2["k_exec"]["150"] is None and row2["reach"]["150"] == 1
    assert out["extremes_top"][0]["exec_max_H"] == 30
    rec2 = {"date": "2025-06-02", "snapshots": [
        {"pop": "B", "T": 645, "names": [
            mk("XXX", 0.10, -0.50, up_max=5),
            mk("YYY", 0.40, -0.05, up_max=50),
            mk("ZZZ", 0.05, -0.02, up_max=5)]}]}
    add_rec(rec2, pt, monthly, extremes)
    out2 = finalize(pt, monthly, extremes)
    row3 = next(r for r in out2["per_pop_T"] if r["T"] == 645)
    assert row3["cover"]["days"] == 0, row3["cover"]
    assert abs(row3["cover"]["ratio_q"]["p50"] - round(0.40 / 0.52, 4)) < 1e-9, row3["cover"]
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="*", default=None)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    files = sorted(glob.glob(str(ANAT / "*.jsonl")))
    if args.months:
        months = set(args.months)
        files = [f for f in files if Path(f).name[:7] in months]
    if not files:
        raise SystemExit("no day files")
    per_pt = defaultdict(new_pt)
    monthly = defaultdict(lambda: defaultdict(int))
    extremes = []
    for f in files:
        add_rec(json.load(open(f)), per_pt, monthly, extremes)
    out = finalize(per_pt, monthly, extremes)
    out["n_day_files"] = len(files)
    Path(args.out).mkdir(parents=True, exist_ok=True)
    with open(Path(args.out) / "T11_dist.json", "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(f"T11_dist: {len(files)} day files, {len(out['per_pop_T'])} pop/T rows -> {args.out}/T11_dist.json")


if __name__ == "__main__":
    main()
