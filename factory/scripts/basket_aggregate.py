#!/usr/bin/env python3
"""BASKET-01 Phase-1 aggregation — anatomy day files -> month-blocked tables.

Implements PRE-REG-BASKET-01 §5 (T1-T10, first pass) including the locked
**T7b joint multi-member right tail** (multi-survivor preservation: k = 0/1/2/3
original top-3 members per +H ruler, touch vs executable lenses, 1st/2nd/3rd MFE,
>=2 / all-3 day frequencies, rank-adjacent mirror, across the frozen timing surface).

Measurement only. No release rule, no survivor rule, no policy P&L.
The +H/-L rulers are descriptive rulers, never definitions or targets.

Discovery posture: these tables are the pre-declared way to look for the mechanism's
practical signatures (accessible right tail; possibly multiple survivors; remaining
opportunity after identification improves; asymmetric failure/survival geometry; a
cheap-failure region that does not eliminate exceptional movers). Full month-blocked
reporting, unfavorable cells included; no T/N/T mining; implementation artifacts are
never thesis evidence.

Lenses locked by PRE-REG:
  * touch lens        = minute path opportunity (ladders up[H] present)
  * executable lens   = touch AND a next bar existed (exec not None); the
                        exec-price-vs-threshold variant is reported too
  * blocked/unfilled  = NEVER counted as accessible participation; own bucket.
                        (Their raw paths live in the stored candidate bars and may
                        appear in the selected-name lens only.)

Follow-ups deliberately NOT in this script (no extractor change needed):
  * T5 bars pass (max-retracement-before-high etc. from candidate bars) -> --t5 flag later
  * T9b matched-random control (needs a raw-data pass over the PIT universe)
  * T7 overnight shadow ledger (needs next-session opens from raw data)

Usage:
  .venv/bin/python factory/scripts/basket_aggregate.py --self-test
  .venv/bin/python factory/scripts/basket_aggregate.py --months 2021-02 2021-03
  .venv/bin/python factory/scripts/basket_aggregate.py            # all present days
"""
from __future__ import annotations

import argparse
import glob
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
import os
ART = Path(os.environ.get("BASKET_ART_ROOT", str(ROOT / "factory" / "artifacts" / "basket")))
ANAT = ART / "anatomy"
OUT = ART / "agg"

UP = [5, 10, 20, 30, 50, 100]
DN = [3, 5, 8, 10, 15]
N_LADDER = [1, 3, 5, 10]
PRIMARY_N = 3


def tickers(names, k):
    return frozenset(n["ticker"] for n in names[:k])


def member_class(n):
    f = n.get("fill")
    if f is None:
        return "unfilled"
    return "gap_blocked" if f.get("blocked") else "filled"


def race_label(n, H, L):
    """Ordering of first touches for one member. Returns one of:
    up_only / dn_only / up / dn / amb / neither.
    amb = same-bar both-touch (order unknown) -> bounds are reported by callers."""
    up = n["ladders"]["up"][str(H)]
    dn = n["ladders"]["dn"][str(L)]
    if up is None and dn is None:
        return "neither"
    if dn is None:
        return "up_only"
    if up is None:
        return "dn_only"
    if up["i"] < dn["i"]:
        return "up"
    if dn["i"] < up["i"]:
        return "dn"
    return "amb"


def new_acc():
    return {
        # T1/T8
        "days": Counter(), "ncand": [], "churn": defaultdict(list),
        # T2
        "contain": defaultdict(Counter), "cdays": Counter(),
        "shares": defaultdict(lambda: defaultdict(list)),
        # T3/T4/T9 member-level, keyed (setname, H, L)
        "race": defaultdict(Counter),
        # T4 basket-level frontier, keyed (month, pop, T, setname, H, L)
        "front": defaultdict(Counter),
        # T7b, keyed (setname, H)
        "jt": defaultdict(Counter), "mfe_rank": defaultdict(lambda: defaultdict(list)),
        "multi": defaultdict(Counter),
        # T5/T6
        "t5": defaultdict(list), "t6": defaultdict(Counter),
        # T10 materials
        "t10": defaultdict(Counter),
    }


def add_member(acc, month, pop, T, setname, n, is_top3):
    cls = member_class(n)
    acc["t6"][(month, pop, T, setname)][cls] += 1
    if cls != "filled":
        return
    for H in UP:
        for L in DN:
            acc["race"][(setname, H, L)][race_label(n, H, L)] += 1
    acc["t5"][(month, pop, T, setname)].append(
        (n["mfe"], n["mae"], n["i_mfe"], n["i_mae"], n["eod_ret"], n["gap_post"])
    )
    if is_top3:
        acc["mfe_rank"][(setname,)]["m"].append(n["mfe"])


def add_snapshot(acc, rec, month, pop, T, names):
    key = (month, pop, T)
    acc["days"][key] += 1
    acc["cdays"][key] += 1          # T2 day-level denominator (one per snapshot-day)
    acc["ncand"].append((month, pop, T, len(names)))
    if len(names) == 0:
        acc["days"][(month, pop, T, "empty")] += 1
    if len(names) < PRIMARY_N:
        acc["days"][(month, pop, T, "lt3")] += 1

    # churn source: top-3 tickers for the main set
    acc["churn"][(rec["date"], pop)].append((T, tickers(names, PRIMARY_N)))

    # ---- T2 containment vs session-max leaders (winners_open)
    # Day-level numerators over acc["cdays"] (the old "eval" denominator counted one
    # row per (winner, N) pair = 3x days, printing shares at one third of the day value).
    winners = rec.get("winners_open", [])[:3]
    for N in N_LADDER:
        s = set(n["ticker"] for n in names[:N])
        for wr, w in enumerate(winners, start=1):
            if w["ticker"] in s:
                acc["contain"][(month, pop, T, N)][f"top{wr}_in"] += 1
                m = next((n for n in names[:N] if n["ticker"] == w["ticker"]), None)
                f = (m or {}).get("fill")
                # Shares conditioned on an ACCESSIBLE fill: a blocked slot is cash,
                # its later raw path is not participation (PRE-REG §5 / T6 semantics).
                if m and f is not None and not f.get("blocked") and m.get("mfe") is not None:
                    acc["contain"][(month, pop, T, N)]["filled_in"] += 1
                    a = float(m["open0930"]) if m.get("open0930") else None
                    hi = float(m["day_high"]) if m.get("day_high") else None
                    fill = float(f["px"])
                    pxd = float(m["px_decision"]) if m.get("px_decision") is not None else None
                    post_hi = fill * (1.0 + float(m["mfe"]))
                    if a and hi and pxd is not None and hi > a and fill > 0:
                        sh = acc["shares"][(month, pop, T, N)]
                        sh["completed"].append((pxd - a) / (hi - a))
                        # post-fill shares: the part of the day's open-anchored move
                        # that still lay ahead of OUR fill (accessibility lens).
                        sh["ahead"].append((post_hi - fill) / (hi - a))
                        sh["remaining"].append(post_hi / fill - 1.0)
                else:
                    acc["contain"][(month, pop, T, N)]["blocked_in"] += 1
            acc["contain"][(month, pop, T, N)]["eval"] += 1

    # ---- member-level, main set (top-3) and rank-adjacent control (ranks 4-6)
    for setname, mset in (("main", names[:PRIMARY_N]), ("adj", names[PRIMARY_N:2 * PRIMARY_N])):
        for n in mset:
            add_member(acc, month, pop, T, setname, n, is_top3=(setname == "main"))
        filled = [n for n in mset if member_class(n) == "filled"]
        for H in UP:
            for L in DN:
                labels = [race_label(n, H, L) for n in filled]
                kp = sum(1 for x in labels if x in ("up", "up_only"))
                ka = sum(1 for x in labels if x == "amb")
                c = acc["front"][(month, pop, T, setname, H, L)]
                c["days"] += 1
                c[f"k{kp}"] += 1
                c["amb0_days"] += int(kp == 0 and ka > 0)
                touched = [n for n in filled if n["ladders"]["up"][str(H)] is not None]
                c["touched"] += len(touched)
                c["upfirst"] += sum(1 for n in touched
                                    if race_label(n, H, L) in ("up", "up_only"))
                c["amb"] += sum(1 for n in touched if race_label(n, H, L) == "amb")
        short = PRIMARY_N - len(mset)
        acc["jt"][(month, pop, T, setname)]["unfilled_slots"] += short
        for H in UP:
            k_touch = sum(1 for n in filled if n["ladders"]["up"][str(H)] is not None)
            k_exec = sum(1 for n in filled
                         if n["ladders"]["up"][str(H)] is not None
                         and n["ladders"]["up"][str(H)]["exec"] is not None)
            k_above = 0
            for n in filled:
                u = n["ladders"]["up"][str(H)]
                if u is not None and u["exec"] is not None:
                    thr = float(n["fill"]["px"]) * (1 + H / 100.0)
                    if float(u["exec"]) >= thr:
                        k_above += 1
            acc["jt"][(month, pop, T, setname)][f"k_touch_{H}/{k_touch}"] += 1
            acc["jt"][(month, pop, T, setname)][f"k_exec_{H}/{k_exec}"] += 1
            acc["jt"][(month, pop, T, setname)][f"k_above_{H}/{k_above}"] += 1
            acc["multi"][(month, pop, T, setname, H)][f"ge2_touch"] += int(k_touch >= 2)
            acc["multi"][(month, pop, T, setname, H)][f"all3_touch"] += int(k_touch == 3 and len(mset) == 3)
        mfes = sorted((float(n["mfe"]) for n in filled), reverse=True)
        for i in range(PRIMARY_N):
            acc["mfe_rank"][(setname,)][f"rank{i+1}"].append(
                mfes[i] if i < len(mfes) else None)

    # ---- T10 materials (rule-free): number of top-3 members breaching dn rulers
    t10 = acc["t10"][(month, pop, T)]
    m3 = [n for n in names[:PRIMARY_N] if member_class(n) == "filled"]
    for L in (5, 10):
        k = sum(1 for n in m3 if n["ladders"]["dn"][str(L)] is not None)
        t10[f"dn{L}_members/{k}"] += 1


def add_rec(rec, acc):
    month = rec["date"][:7]
    for s in rec["snapshots"]:
        add_snapshot(acc, rec, month, s["pop"], s["T"], s["names"])


def _q(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return None
    a = np.array(v, dtype=float)
    return {
        "n": int(len(a)), "mean": round(float(a.mean()), 5),
        "p10": round(float(np.percentile(a, 10)), 5),
        "p50": round(float(np.percentile(a, 50)), 5),
        "p90": round(float(np.percentile(a, 90)), 5),
        "p99": round(float(np.percentile(a, 99)), 5),
    }


def finalize(acc):
    tables = {}

    # T1 composition (+churn per month/pop from stored day series)
    t1 = []
    bykey = sorted({k[:3] for k in acc["days"] if len(k) == 3})
    for month, pop, T in bykey:
        nc = [x[3] for x in acc["ncand"] if x[:3] == (month, pop, T)]
        row = {"month": month, "pop": pop, "T": T,
               "days": acc["days"][(month, pop, T)],
               "days_empty": acc["days"][(month, pop, T, "empty")],
               "days_lt3": acc["days"][(month, pop, T, "lt3")],
               "n_cand": _q(nc)}
        t1.append(row)
    churn = []
    for (day, pop), seq in sorted(acc["churn"].items()):
        seq = sorted(seq)
        for (Ta, sa), (Tb, sb) in zip(seq, seq[1:]):
            churn.append({"month": day[:7], "pop": pop, "T_from": Ta, "T_to": Tb,
                          "overlap": len(sa & sb)})
    churns = defaultdict(list)
    for c in churn:
        churns[(c["month"], c["pop"], c["T_from"], c["T_to"])].append(c["overlap"])
    t1b = [{"month": m, "pop": p, "T_from": a, "T_to": b,
            "mean_top3_overlap": round(float(np.mean(v)), 3), "n_days": len(v)}
           for (m, p, a, b), v in sorted(churns.items())]
    tables["T1"] = {"composition": t1, "churn": t1b}

    # T2 containment (day-level shares; numerators are day counts)
    t2 = []
    for (month, pop, T, N), c in sorted(acc["contain"].items()):
        days = acc["cdays"].get((month, pop, T), 0)
        row = {"month": month, "pop": pop, "T": T, "N": N, "days": days,
               "top1_in": c["top1_in"], "top2_in": c["top2_in"], "top3_in": c["top3_in"],
               "top1_in_share": round(c["top1_in"] / days, 5) if days else None,
               "top2_in_share": round(c["top2_in"] / days, 5) if days else None,
               "top3_in_share": round(c["top3_in"] / days, 5) if days else None,
               "filled_in": c.get("filled_in", 0), "blocked_in": c.get("blocked_in", 0)}
        sh = acc["shares"].get((month, pop, T, N), {})
        for k in ("completed", "ahead", "remaining"):
            row[k] = _q(sh.get(k, []))
        t2.append(row)
    tables["T2"] = t2

    # T3/T4/T9 race counters (bounds derived by callers: amb reported raw)
    t3 = []
    for (setname, H, L), c in sorted(acc["race"].items()):
        tot = sum(c.values()) or 1
        t3.append({"set": setname, "H": H, "L": L, **{k: c[k] for k in c},
                   "amb_share": round(c["amb"] / tot, 4),
                   "up_first_pess": round((c["up"] + c["up_only"]) / tot, 4),
                   "up_first_opt": round((c["up"] + c["up_only"] + c["amb"]) / tot, 4)})
    tables["T3"] = t3

    # T4 frontier (day-level, from T7b-shaped baskets): any up-first per day/pop/T
    t4 = []
    jt = acc["jt"]
    for (month, pop, T, setname), c in sorted(jt.items()):
        base = {k: c[k] for k in c if k.startswith(("k_touch", "unfilled_slots"))}
        t4.append({"month": month, "pop": pop, "T": T, "set": setname, **base})
    tables["T4"] = t4

    # T4b basket-level release-retention frontier F(L,H) + Q_H(L)
    t4b = []
    for (month, pop, T, setname, H, L), c in sorted(acc["front"].items()):
        days = c["days"] or 1
        t4b.append({
            "month": month, "pop": pop, "T": T, "set": setname, "H": H, "L": L,
            "days": c["days"],
            "F_pess": round((days - c["k0"]) / days, 5),
            "F_opt": round((days - c["k0"] + c["amb0_days"]) / days, 5),
            "k_hist": [c["k0"], c["k1"], c["k2"], c["k3"]],
            "touched": c["touched"],
            "Q_pess": round(c["upfirst"] / c["touched"], 5) if c["touched"] else None,
            "Q_opt": round((c["upfirst"] + c["amb"]) / c["touched"], 5) if c["touched"] else None,
        })
    tables["T4b_frontier"] = t4b

    # T7b joint tail distributions (aggregate k-histograms over months + per month)
    t7b = []
    keys = sorted({k for k in jt if len(k) == 4})
    for (month, pop, T, setname) in keys:
        c = jt[(month, pop, T, setname)]
        days = c.get("k_touch_50/0", 0) + c.get("k_touch_50/1", 0) + c.get("k_touch_50/2", 0) + c.get("k_touch_50/3", 0)
        row = {"month": month, "pop": pop, "T": T, "set": setname, "days": days,
               "unfilled_slots": c.get("unfilled_slots", 0)}
        for H in UP:
            row[f"touch_k_{H}"] = [c.get(f"k_touch_{H}/{k}", 0) for k in range(4)]
            row[f"exec_k_{H}"] = [c.get(f"k_exec_{H}/{k}", 0) for k in range(4)]
            row[f"above_k_{H}"] = [c.get(f"k_above_{H}/{k}", 0) for k in range(4)]
        t7b.append(row)
    tables["T7b"] = t7b

    # T7b multi-member day frequencies
    t7b2 = []
    for (month, pop, T, setname, H), c in sorted(acc["multi"].items()):
        t7b2.append({"month": month, "pop": pop, "T": T, "set": setname, "H": H, **c})
    tables["T7b_multi"] = t7b2

    # MFE ranks (pooled + monthly)
    t5 = []
    for (setname,), d in sorted(acc["mfe_rank"].items()):
        row = {"set": setname}
        for k, v in sorted(d.items()):
            row[k] = _q(v)
        t5.append(row)
    tables["T5_mfe_ranks"] = t5

    # T5 member path stats
    t5b = []
    for (month, pop, T, setname), rows in sorted(acc["t5"].items()):
        if not rows:
            continue
        arr = np.array(rows, dtype=float)
        t5b.append({"month": month, "pop": pop, "T": T, "set": setname,
                    "n": int(arr.shape[0]),
                    "mfe": _q(arr[:, 0]), "mae": _q(arr[:, 1]),
                    "i_mfe": _q(arr[:, 2]), "i_mae": _q(arr[:, 3]),
                    "eod_ret": _q(arr[:, 4])})
    tables["T5_member"] = t5b

    # T6 structural buckets
    t6 = []
    for (month, pop, T, setname), c in sorted(acc["t6"].items()):
        t6.append({"month": month, "pop": pop, "T": T, "set": setname, **c})
    tables["T6"] = t6

    # T10 materials
    t10 = []
    for (month, pop, T), c in sorted(acc["t10"].items()):
        t10.append({"month": month, "pop": pop, "T": T, **c})
    tables["T10_materials"] = t10
    return tables


# ---------------------------------------------------------------- self-test


def _fake_member(ticker, fill, mfe, up_i, dn_i, up_exec=None, blocked=False):
    return {
        "ticker": ticker, "rank": 1, "sel": 0.1, "px_decision": 110.0,
        "open0930": 100.0, "prev_close": 90.0, "split_flag": False,
        "fill": {"et": 575, "px": fill, "gap_min": 0, "blocked": blocked, "alt_px": None},
        "day_high": 200.0, "close": 150.0, "eod_ret": 0.5, "mfe": mfe, "mae": -0.05,
        "i_mfe": 10, "i_mae": 3, "gap_pre": 0, "gap_post": 2,
        "ladders": {
            "up": {str(H): (None if up_i is None else
                            {"i": up_i, "et": 585, "touch": 999.0, "exec": up_exec})
                   for H in UP},
            "dn": {str(L): (None if dn_i is None else
                            {"i": dn_i, "et": 580, "touch": 0.01, "exec": 0.02})
                   for L in DN},
        },
        "state": {},
    }


def selftest():
    acc = new_acc()
    m1 = _fake_member("AAA", 100.0, 0.50, 5, 9, up_exec=140.0)     # up first
    m2 = _fake_member("BBB", 100.0, 0.10, 8, 2, up_exec=105.0)     # dn first
    m3 = _fake_member("CCC", 100.0, 0.02, 3, 3, up_exec=101.0)     # amb at H/L
    m4 = _fake_member("DDD", 100.0, 0.30, None, 4, blocked=True)   # gap-blocked, dn only
    rec = {"date": "2025-06-02", "winners_open": [{"ticker": "AAA", "gain_open": 0.9}],
           "snapshots": [{"pop": "B", "T": 585, "names": [m1, m2, m3, m4]}]}
    add_rec(rec, acc)
    t = finalize(acc)

    jt = next(x for x in t["T7b"] if x["set"] == "main")
    assert jt["days"] == 1, jt
    # filled top-3 = AAA,BBB,CCC; all touch +5 -> k_touch_5 = 3
    assert jt["touch_k_5"] == [0, 0, 0, 1], jt["touch_k_5"]
    # exec exists for all three -> k_exec_5 = 3; k_above_5: AAA 140>=105 ->1, BBB 105>=105 ->1, CCC 101<105 ->0
    assert jt["exec_k_5"] == [0, 0, 0, 1]
    assert jt["above_k_5"] == [0, 0, 1, 0], jt["above_k_5"]
    # race H=20/L=5: AAA up(5<9), BBB dn(2<8), CCC amb(3,3)
    r = {(x["H"], x["L"]): x for x in t["T3"] if x["set"] == "main"}
    assert r[(20, 5)]["up"] == 1 and r[(20, 5)]["dn"] == 1 and r[(20, 5)]["amb"] == 1
    assert r[(20, 5)]["up_first_pess"] == round(1 / 3, 4)
    # basket-level frontier: main k>=1 (pess) present; Q = up-first | touched
    fr = next(x for x in t["T4b_frontier"]
              if x["set"] == "main" and x["H"] == 20 and x["L"] == 5)
    assert fr["k_hist"][1] == 1 and fr["F_pess"] == 1.0 and fr["F_opt"] == 1.0, fr
    assert fr["touched"] == 3 and fr["Q_pess"] == round(1 / 3, 5), fr
    # MFE ranks: 0.50, 0.10, 0.02
    tr = next(x for x in t["T5_mfe_ranks"] if x["set"] == "main")
    assert tr["rank1"]["p50"] == 0.5 and tr["rank3"]["p50"] == 0.02
    # T2 day-level: one winner contained -> numerator 1 over ONE day (not 3 rows)
    t2 = {(x["pop"], x["T"], x["N"]): x for x in t["T2"]}
    row = t2[("B", 585, 3)]
    assert row["days"] == 1 and row["top1_in"] == 1 and row["top1_in_share"] == 1.0, row
    assert row["blocked_in"] == 0 and row["filled_in"] == 1, row
    # post-fill shares: completed (110-100)/(200-100)=.1; post_hi=150 -> ahead .5, remaining .5
    assert row["completed"]["p50"] == 0.1 and row["ahead"]["p50"] == 0.5, row
    assert row["remaining"]["p50"] == 0.5, row
    t6 = {(x["set"]): x for x in t["T6"]}
    main_filled = t6["main"]["filled"]
    adj_blocked = t6["adj"]["gap_blocked"]
    assert main_filled == 3, main_filled
    assert adj_blocked == 1, adj_blocked
    assert t6["adj"].get("filled", 0) == 0
    print("self-test OK")


# ---------------------------------------------------------------- main


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
        months = {m for m in args.months}
        files = [f for f in files if Path(f).name[:7] in months]
    if not files:
        raise SystemExit("no day files found")

    acc = new_acc()
    for f in files:
        rec = json.load(open(f))
        add_rec(rec, acc)
    tables = finalize(acc)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, data in tables.items():
        with open(out / f"{name}.json", "w") as fh:
            json.dump(data, fh, indent=1, default=str)
    print(f"wrote {len(tables)} tables from {len(files)} day files -> {out}")
    for name in sorted(tables):
        print(f"  {name}: {len(tables[name])} rows")


if __name__ == "__main__":
    main()
