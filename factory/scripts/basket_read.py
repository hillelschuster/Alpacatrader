#!/usr/bin/env python3
"""BASKET-01 consolidated read packet — non-selective view over the agg tables.

Reads the committed aggregation tables for a given artifact root (legacy tree by
default, or the SIP-regenerated tree via BASKET_ART_ROOT / --root) and prints the
FULL frozen family: composition, containment, joint k-tail across the whole ruler
ladder, competing-risk counters, the F/Q frontier over all (H, L) cells, runner
path anatomy, overnight shadow, matched-random control, the continuous T11
distribution and monthly stability.

Design rule: nothing is selected. Rulers (+5..+300) are measurement rulers, never
targets or definitions; every H and L is reported. No thresholds are chosen here.

Usage:
  .venv/bin/python factory/scripts/basket_read.py --self-test
  BASKET_ART_ROOT=factory/artifacts/basket/sip \\
    .venv/bin/python factory/scripts/basket_read.py --write
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
H_LADDER = [5, 10, 20, 30, 50, 100]
L_LADDER = [3, 5, 8, 10, 15]
SETS = ("main", "adj")


def art_root(arg=None) -> Path:
    if arg:
        return Path(arg)
    env = os.environ.get("BASKET_ART_ROOT")
    return Path(env) if env else ROOT / "factory" / "artifacts" / "basket"


def load(root: Path, name: str):
    p = root / "agg" / f"{name}.json"
    if not p.exists():
        return None
    return json.load(open(p))


def sum_k(rows, key, k) -> list:
    """Pool k-histograms across months."""
    return [sum(r.get(key, [0, 0, 0, 0])[k] for r in rows) for k in range(4)]


def pooled_F_Q(rows) -> tuple:
    """Pooled frontier from monthly rows: F = sum(days-k0)/sum(days); Q over rows with a defined Q."""
    days = sum(r["days"] for r in rows)
    k0 = sum((r.get("k_hist") or [0, 0, 0, 0])[0] for r in rows)
    touched = 0
    qw = 0.0
    for r in rows:
        q = r.get("Q_pess")
        t = r.get("touched", 0) or 0
        if q is not None:
            touched += t
            qw += q * t
    F = (days - k0) / days if days else None
    Q = qw / touched if touched else None
    return days, touched, F, Q


def _median_field(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return None if not vals else float(np.median(np.array(vals, dtype=float)))


def build(root: Path) -> dict:
    out: dict = {"root": str(root), "sections": {}, "notes": []}
    t1 = load(root, "T1")
    t2 = load(root, "T2")
    t7b = load(root, "T7b")
    t3 = load(root, "T3")
    t4b = load(root, "T4b_frontier")
    t5p = load(root, "T5_paths")
    t5m = load(root, "T5_member")
    t5r = load(root, "T5_mfe_ranks")
    t7o = load(root, "T7_overnight")
    t9b = load(root, "T9b_random")
    t11 = load(root, "T11_dist")
    t6 = load(root, "T6")
    t7e = load(root, "T7_payforteam")
    t8 = load(root, "T8_stability")
    mbr = load(root, "market_base_rates")
    cf = load(root, "capture_funnel")
    rbv = load(root, "race_by_view")
    cov = load(root, "coverage_summary")

    s: dict = out["sections"]
    if cov:
        s["coverage"] = {"days": cov.get("days"), "symbol_days": cov.get("symbol_days"),
                         "classes": cov.get("classes"), "shares": cov.get("shares"),
                         "unresolved_n": len(cov.get("unresolved", []) or [])}
    if t1:
        comp = defaultdict(lambda: {"days": 0, "empty": 0, "lt3": 0})
        for r in t1["composition"]:
            k = (r["pop"], r["T"])
            comp[k]["days"] += r["days"]
            comp[k]["empty"] += r["days_empty"]
            comp[k]["lt3"] += r["days_lt3"]
        s["composition"] = {f"{p}/{T}": v for (p, T), v in sorted(comp.items())}
        ch = defaultdict(list)
        for r in t1.get("churn", []):
            ch[(r["pop"], r["T_from"], r["T_to"])].append(r["mean_top3_overlap"])
        s["churn_top3_overlap"] = {f"{p}/{a}->{b}": round(float(np.mean(v)), 3)
                                   for (p, a, b), v in sorted(ch.items())}
    if t2:
        keys = ["top1_in", "top2_in", "top3_in",
                "prev_top1_in", "prev_top2_in", "prev_top3_in",
                "eodopen_top1_in", "eodopen_top2_in", "eodopen_top3_in",
                "eodprev_top1_in", "eodprev_top2_in", "eodprev_top3_in"]
        agg = defaultdict(lambda: defaultdict(int))
        for r in t2:
            k = (r["pop"], r["T"], r["N"])
            agg[k]["d"] += int(r.get("days", r.get("days_eval", 0)) or 0)
            for key in keys:
                agg[k][key] += int(r.get(key, 0) or 0)
            agg[k]["blk"] += r.get("blocked_in", 0)
        s["containment"] = {}
        for (p, T, N), v in sorted(agg.items()):
            d = v["d"]
            entry: dict = {"days": d, "blocked_in": v["blk"]}
            for key in keys:
                entry[key] = round(v[key] / d, 4) if d else None
            s["containment"][f"{p}/{T}/N{N}"] = entry
    if t7b:
        grp = defaultdict(list)
        for r in t7b:
            grp[(r["pop"], r["T"], r["set"])].append(r)
        jt = {}
        for (p, T, setn), rows in sorted(grp.items()):
            entry = {"days": int(np.sum([r["days"] for r in rows]))}
            for H in H_LADDER:
                for lens in ("touch", "exec", "above"):
                    hs = sum_k(rows, f"{lens}_k_{H}", 0)
                    hist = [sum(r.get(f"{lens}_k_{H}", [0, 0, 0, 0])[i] for r in rows) for i in range(4)]
                    entry[f"{lens}_{H}"] = {
                        "k>=1": round((sum(hist) - hist[0]) / sum(hist), 4) if sum(hist) else None,
                        "k>=2": round((hist[2] + hist[3]) / sum(hist), 4) if sum(hist) else None,
                        "all3": round(hist[3] / sum(hist), 4) if sum(hist) else None,
                        "hist": hist}
            jt[f"{p}/{T}/{setn}"] = entry
        s["joint_tail"] = jt
    if t3:
        agg = defaultdict(lambda: defaultdict(int))
        for r in t3:
            k = (r["set"], r["H"], r["L"])
            for f in ("up", "dn", "amb", "up_only", "dn_only", "neither"):
                agg[k][f] += int(r.get(f, 0) or 0)
        s["race"] = {f"{setn}/H{H}/L{L}": {**v, "amb_share": round(v["amb"] / max(sum(v.values()), 1), 4)}
                     for (setn, H, L), v in sorted(agg.items())}
    if t4b:
        grp = defaultdict(list)
        for r in t4b:
            grp[(r["pop"], r["T"], r["set"], r["H"], r["L"])].append(r)
        fr = {}
        for (p, T, setn, H, L), rows in sorted(grp.items()):
            days, touched, F, Q = pooled_F_Q(rows)
            fr[f"{p}/{T}/{setn}"] = fr.get(f"{p}/{T}/{setn}", {})
            fr[f"{p}/{T}/{setn}"][f"H{H}/L{L}"] = {
                "days": days, "touched": touched,
                "F": None if F is None else round(F, 4),
                "Q": None if Q is None else round(Q, 4),
                "zero_months": int(np.sum([1 for r in rows if r["F_pess"] == 0.0]))}
        s["frontier"] = fr
    if t5p:
        grp = defaultdict(list)
        for r in t5p["tables"]:
            grp[(r.get("pop", "-"), r.get("T", 0), r["set"], r["stratum"], r["stat"])].append(r)
        s["runner_paths"] = {
            f"{pop}/{T}/{setn}/{strat}/{stat}": {
                "months": len(rows),
                "p50_monthly_median": _round(_median_field(rows, "p50")),
                "p50_monthly_range": _range([r["p50"] for r in rows]),
            }
            for (pop, T, setn, strat, stat), rows in sorted(grp.items())}
        s["runner_paths_meta"] = {
            "n_members": t5p.get("n_members"), "n_no_trades": t5p.get("n_no_trades"),
            "n_sparse_lt5": t5p.get("n_sparse_lt5"), "method": t5p.get("method"),
            "peak_recon_vs_stored_mfe": t5p.get("peak_recon_vs_stored_mfe")}
    if t5m:
        grp = defaultdict(list)
        for r in t5m:
            grp[(r["pop"], r["set"])].append(r)
        s["member_mfe_mae"] = {}
        for key, rows in sorted(grp.items()):
            n = sum(r["n"] for r in rows)
            mfe = grp_quantile(rows, "mfe")
            s["member_mfe_mae"][f"{key[0]}/{key[1]}"] = {"n": n, "mfe": mfe, "mae": grp_quantile(rows, "mae")}
    if t5r:
        s["mfe_ranks"] = {r["set"]: {k: r[k] for k in r if k != "set"} for r in t5r}
    if t7o:
        grp = defaultdict(list)
        for r in t7o["tables"]:
            if r["stat"] == "gap":
                grp[(r["set"], r["stratum"])].append(r)
        s["overnight"] = {f"{setn}/{strat}": {"months": len(rows),
                                              "p50_monthly_median": _round(np.median([r["p50"] for r in rows])),
                                              "p50_monthly_range": _range([r["p50"] for r in rows])}
                          for (setn, strat), rows in sorted(grp.items())}
        s["overnight_reasons"] = t7o.get("reasons")
    if t9b:
        grp = defaultdict(list)
        for r in t9b["tables"]:
            grp[r["T"]].append(r)
        s["random_control"] = {}
        for T, rows in sorted(grp.items()):
            entry = {"days": int(np.sum([r["days"] for r in rows]))}
            for H in H_LADDER:
                hist = [sum(r.get(f"touch_k_{H}", [0, 0, 0, 0])[i] for r in rows) for i in range(4)]
                entry[f"touch_{H}_k>=1"] = round((sum(hist) - hist[0]) / sum(hist), 4) if sum(hist) else None
            s["random_control"][str(T)] = entry
    if t11:
        s["continuous"] = {}
        for r in t11["per_pop_T"]:
            key = f"{r['pop']}/{r['T']}"
            if r["pop"] == "B" and r["T"] not in (600, 585, 720):
                continue
            if r["pop"] != "B" and r["T"] != 570:
                continue
            s["continuous"][key] = {
                "days": r["days"], "days_lt3": r["days_lt3"], "members": r["members_filled"],
                "member_mfe_q": r["member_mfe_q"], "member_mae_q": r["member_mae_q"],
                "day_max_q": r["day_max_q"], "day_second_q": r["day_second_q"],
                "day_third_q": r["day_third_q"],
                "member_mfe_bands": r["member_mfe_bands"], "bands": r["bands"],
                "k_touch": r["k_touch"], "k_exec": r["k_exec"],
                "ordinary": r["ordinary"], "cover": r["cover"], "dn_touch": r["dn_touch"]}
        s["extremes"] = t11.get("extremes_top", [])[:20]
    if t6:
        tot = defaultdict(int)
        for r in t6:
            for k, v in r.items():
                if k in ("month", "pop", "T", "set"):
                    continue
                tot[(r["pop"], r["set"], k)] += v
        s["buckets"] = {f"{p}/{setn}/{k}": v for (p, setn, k), v in sorted(tot.items())}
    if t7e:
        s["pay_for_team"] = t7e.get("pooled", t7e)
        s["pay_for_team_break_even"] = t7e.get("break_even_map")
    if t8:
        s["stability"] = t8
    if mbr:
        s["market_base_rates"] = mbr
    if cf:
        s["capture_funnel"] = cf.get("summary", cf)
    if rbv:
        s["race_by_view"] = {"views": rbv.get("views"), "method": rbv.get("method")}
    out["notes"].append("Rulers are descriptive; no H/L/T/N were selected by this reader.")
    out["notes"].append("Lenses: touch = the excursion exists on the minute path; exec = touch AND a next "
                        "bar existed (the frozen contract's 'executable' bookkeeping -- NOT a claim of "
                        "sellability); above = the next bar's open is at/above the threshold (strict "
                        "saleable lens). Never read exec as 'we could have sold there'.")
    out["notes"].append("T11 'cover' is a non-economic illustration (best raw MFE vs co-member MAE); "
                        "T7_pay_for_team carries the labeled economics.")
    out["notes"].append("Containment: top{k}_in = hi_open leaders (session max, RTH-open anchored); "
                        "prev_/eodopen_/eodprev_ are the hi_prev, EOD/open and EOD/prev leader "
                        "objects — never conflated with the intraday-high definition.")
    out["notes"].append("Pooling = day-weighted sums across months; quantities are provisional until "
                        "the artifact root's QA gate reports PASS.")
    return out


def grp_quantile(rows, field):
    """n-weighted mean of monthly quantiles plus the median monthly p50."""
    tot = sum(r["n"] for r in rows)
    if not tot:
        return None
    agg = {}
    for q in ("p10", "p50", "p90"):
        vals = [(r[field] or {}).get(q) for r in rows]
        vals = [v for v in vals if v is not None]
        agg[q] = round(float(np.median(vals)), 5) if vals else None
    agg["n"] = tot
    return agg


def _round(x):
    return None if x is None else round(float(x), 5)


def _range(vals):
    v = [x for x in vals if x is not None]
    return None if not v else [round(float(min(v)), 5), round(float(max(v)), 5)]


def render(pkt: dict) -> str:
    L = [f"# BASKET-01 read packet — root `{pkt['root']}`", ""]
    s = pkt["sections"]
    for name, body in s.items():
        L.append(f"## {name}")
        L.append("```json")
        L.append(json.dumps(body, indent=1, default=str))
        L.append("```")
        L.append("")
    L += ["## notes"] + [f"- {n}" for n in pkt["notes"]]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- self-test


def selftest():
    rows = [{"days": 10, "k_hist": [6, 3, 1, 0], "touched": 4, "Q_pess": 0.5},
            {"days": 10, "k_hist": [5, 4, 1, 0], "touched": 6, "Q_pess": 0.75}]
    days, touched, F, Q = pooled_F_Q(rows)
    assert days == 20 and touched == 10, (days, touched)
    assert abs(F - (20 - 11) / 20) < 1e-9, F
    assert abs(Q - (0.5 * 4 + 0.75 * 6) / 10) < 1e-9, Q
    hist = sum_k(rows, "k_hist", 1)
    assert hist == [11, 7, 2, 0], hist
    assert _range([1.0, None, 3.0]) == [1.0, 3.0]
    assert _round(None) is None
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
    root = art_root(args.root)
    pkt = build(root)
    text = render(pkt)
    print(text)
    if args.write:
        (root / "READ_PACKET.md").write_text(text)
        with open(root / "read_packet.json", "w") as fh:
            json.dump(pkt, fh, indent=1, default=str)
        print(f"[wrote] {root/'READ_PACKET.md'} + read_packet.json")


if __name__ == "__main__":
    main()
