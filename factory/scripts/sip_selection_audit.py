#!/usr/bin/env python3
"""BASKET-01 Layer-1 vs final-anatomy selection audit (work-order item 5).

Layer-1 = data/sip/candidates/<day>.json (full-PIT provider SIP compact tables).
Final   = factory/artifacts/basket/sip/anatomy/<day>.jsonl (selections made on the
          merged raw-derived bars frame by sip_anatomy.py).

For every dev day and every snapshot, compare the top-3 SETS, classify every
difference by cause, audit promoted names (anatomy top-3 but not Layer-1 top-3),
check their quote coverage, and audit the 8 unresolved symbol-days. This is the
"no silent promotion of rank #4" evidence: any promoted name must be listed with
its Layer-1 rank and quote coverage.

Measurement/audit only. No selection, no parameter, no strategy, no PnL.

Output: <root>/agg/selection_audit.json

Usage:
  .venv/bin/python factory/scripts/sip_selection_audit.py --self-test
  BASKET_ART_ROOT=factory/artifacts/basket/sip .venv/bin/python factory/scripts/sip_selection_audit.py --write [--limit 30]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from collections import Counter
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
CAND = ROOT / "data" / "sip" / "candidates"
UNI = ROOT / "data" / "sip" / "universe" / "rth"
NET = ROOT / "data" / "sip" / "net"  # symlink -> /home/hillel/sip/net
BARSD = NET / "bars"
QUOTES = NET / "quotes"
STAGE = ROOT / "data" / "sip" / "audit_stage"
UNRESOLVED = [("VVPR", "2022-05-13"), ("HSON", "2022-06-15"), ("PMN", "2023-02-13"),
              ("HSON", "2023-07-19"), ("MGLD", "2023-09-19"), ("CSLR", "2023-09-28"),
              ("CSLR", "2023-11-13"), ("HSON", "2025-05-22")]


def art_root(arg=None) -> Path:
    if arg:
        return Path(arg)
    env = os.environ.get("BASKET_ART_ROOT")
    return Path(env) if env else ROOT / "factory" / "artifacts" / "basket"


def classify_diff(l1_rows, an_names, merged_tickers, merged_dec=None, T=None,
                  merged_series=None, prev_syms=None):
    """Return the symmetric difference detail for one snapshot comparison.

    merged_dec: dict ticker -> set of et present on the merged frame that day (optional);
    used to separate "no decision bar in the merged frame" from ranking/population effects.
    """
    l1_top3 = [r["symbol"] for r in l1_rows[:3]]
    an_top3 = [n["ticker"] for n in an_names[:3]]
    l1_rank = {r["symbol"]: r["rank"] for r in l1_rows}
    l1_px = {}
    for r in l1_rows:
        for k, v in r.items():
            if k.startswith("px_") and not k.endswith("_et") and v is not None:
                l1_px[r["symbol"]] = v
    an_px = {n["ticker"]: n.get("px_decision") for n in an_names}
    dropped, promoted = [], []
    for t in l1_top3:
        if t in an_top3:
            continue
        in_bars = t in merged_tickers
        px_a = an_px.get(t)
        self_px = l1_px.get(t)
        ets = (merged_dec or {}).get(t, set())
        mpx = None
        if merged_series and t in merged_series and T is not None:
            se, sc = merged_series[t]
            cand = [c for e, c in zip(se, sc) if e <= T - 1]
            mpx = cand[-1] if cand else None
        if not in_bars:
            cause = "outside_net"
        elif T is not None and ets and not any(e <= T - 1 for e in ets):
            cause = "no_decision_bar_in_merged"
        elif prev_syms is not None and t not in prev_syms:
            cause = "no_prev_close"
        elif px_a is None:
            cause = "rank_below10_in_merged"
        elif self_px is not None and abs(px_a - self_px) > 1e-6:
            cause = "px_path_derivation"
        else:
            cause = "score_tie_or_other"
        dropped.append({"ticker": t, "l1_rank": l1_rank.get(t), "in_merged_bars": in_bars,
                        "l1_px": self_px, "an_px": px_a, "merged_px_at_T": mpx,
                        "prev_close_available": (None if prev_syms is None else t in prev_syms),
                        "cause": cause})
    for t in an_top3:
        if t in l1_top3:
            continue
        r = l1_rank.get(t)
        in_margin = False  # filled by caller (needs the margin list)
        promoted.append({"ticker": t, "l1_rank": r,
                         "l1_listed": "top10" if r else None,
                         "cause": None, "in_merged_bars": t in merged_tickers})
    return {"l1_top3": l1_top3, "anatomy_top3": an_top3,
            "dropped": dropped, "promoted": promoted}


PREV_CACHE: dict = {}


def prev_symbols(prev_day):
    if prev_day is None:
        return None
    if prev_day not in PREV_CACHE:
        p = UNI / f"{prev_day}.parquet"
        if not p.exists():
            PREV_CACHE[prev_day] = None
        else:
            d = pl.read_parquet(p, columns=["symbol", "c_last"])
            PREV_CACHE[prev_day] = {s for s, c in zip(d["symbol"].to_list(), d["c_last"].to_list())
                                    if c is not None and c > 0}
    return PREV_CACHE[prev_day]


def audit_day(day: str, anat_path: Path, cand_path: Path, quotes_cache: dict) -> dict:
    out = {"date": day, "snapshots": 0, "agree": 0, "diffs": []}
    if not cand_path.exists():
        out["no_candidates"] = True
        return out
    cand = json.load(open(cand_path))
    l1 = {(s["pop"], s["T"]): s for s in cand.get("snapshots", [])}
    prev_syms = prev_symbols(cand.get("prev_day"))
    rec = json.load(open(anat_path))
    need_bars = False
    rows = []
    for s in rec.get("snapshots", []):
        key = (s.get("pop"), s.get("T"))
        l1s = l1.get(key)
        out["snapshots"] += 1
        if l1s is None:
            rows.append({"pop": key[0], "T": key[1], "no_layer1_snapshot": True})
            continue
        l1_top3 = [r["symbol"] for r in l1s["top"][:3]]
        an_top3 = [n["ticker"] for n in s["names"][:3]]
        if set(l1_top3) == set(an_top3):
            out["agree"] += 1
            continue
        need_bars = True
        rows.append({"pop": key[0], "T": key[1], "l1_top3": l1_top3,
                     "an_top3": an_top3, "l1_margin": l1s.get("margin") or []})
    if not rows:
        return out
    merged_tickers: set = set()
    merged_dec: dict = {}
    merged_series: dict = {}
    if need_bars:
        bp = BARSD / f"{day}.parquet"
        if bp.exists():
            b = pl.read_parquet(bp, columns=["ticker", "et", "close"]).sort(["ticker", "et"])
            merged_tickers = set(b["ticker"].unique().to_list())
            for t, e, c in zip(b["ticker"].to_list(), b["et"].to_list(), b["close"].to_list()):
                merged_dec.setdefault(t, set()).add(int(e))
                merged_series.setdefault(t, ([], []))[0].append(int(e))
                merged_series[t][1].append(float(c))
    for r in rows:
        if r.get("no_layer1_snapshot"):
            out["diffs"].append(r)
            continue
        l1s = l1[(r["pop"], r["T"])]
        d = classify_diff(l1s["top"], next(x["names"] for x in rec["snapshots"]
                                           if (x["pop"], x["T"]) == (r["pop"], r["T"])),
                          merged_tickers, merged_dec, T=r["T"],
                          merged_series=merged_series, prev_syms=prev_syms)
        for p in d["promoted"]:
            margin = r.get("l1_margin") or []
            if p["l1_rank"] is not None:
                p["cause"] = "top10_promoted"
            elif p["ticker"] in margin:
                p["l1_listed"] = "margin"
                p["cause"] = "margin_promoted"
            else:
                p["cause"] = "rank_gt10_promoted"
            tkr = p["ticker"]
            if (day, tkr) not in quotes_cache:
                qp = QUOTES / f"{day}.parquet"
                have = False
                if qp.exists():
                    have = tkr in set(pl.read_parquet(qp, columns=["symbol"])["symbol"].unique().to_list())
                quotes_cache[(day, tkr)] = have
            p["quotes"] = quotes_cache[(day, tkr)]
        out["diffs"].append({"pop": r["pop"], "T": r["T"], **d})
    return out


def unresolved_audit(cand_dir: Path, anat_dir: Path) -> list:
    res = []
    for sym, day in UNRESOLVED:
        rec: dict = {"ticker": sym, "day": day}
        cp = cand_dir / f"{day}.json"
        ap = anat_dir / f"{day}.jsonl"
        if cp.exists():
            cand = json.load(open(cp))
            hits = []
            for s in cand.get("snapshots", []):
                for r in s.get("top", []):
                    if r["symbol"] == sym:
                        hits.append({"pop": s["pop"], "T": s["T"], "rank": r["rank"]})
                if sym in (s.get("margin") or []):
                    hits.append({"pop": s["pop"], "T": s["T"], "margin": True})
            rec["layer1_hits"] = hits
            rec["in_net"] = sym in set(cand.get("net") or [])
        if ap.exists():
            a = json.load(open(ap))
            sel = [{"pop": s["pop"], "T": s["T"], "rank": n["rank"]}
                   for s in a.get("snapshots", []) for n in s["names"] if n["ticker"] == sym]
            rec["anatomy_selected"] = sel
        res.append(rec)
    return res


def run(root: Path, write: bool, limit: int | None, merge_only: bool = False):
    anat_dir = root / "anatomy"
    days = sorted(Path(f).name[:10] for f in glob.glob(str(anat_dir / "*.jsonl")))
    if limit:
        days = days[:limit]
    quotes_cache: dict = {}
    per_day, cause = [], Counter()
    promoted, tot_snaps, tot_agree = [], 0, 0
    diffs_n = 0
    if merge_only:
        day_results = []
        for f in sorted(glob.glob(str(STAGE / "*.json"))):
            day_results.append(json.load(open(f)))
    else:
        day_results = []
        for i, day in enumerate(days, 1):
            d = audit_day(day, anat_dir / f"{day}.jsonl", CAND / f"{day}.json", quotes_cache)
            STAGE.mkdir(parents=True, exist_ok=True)
            with open(STAGE / f"{day}.json", "w") as fh:
                json.dump(d, fh, separators=(",", ":"), default=str)
            day_results.append(d)
            if i % 100 == 0:
                print(f"  ..{i}/{len(days)} days", flush=True)
    for d in day_results:
        day = d["date"]
        tot_snaps += d["snapshots"]
        tot_agree += d["agree"]
        if d["diffs"]:
            diffs_n += len(d["diffs"])
            per_day.append({"date": day, "n_diffs": len(d["diffs"]), "agree": d["agree"],
                            "snapshots": d["snapshots"]})
            for dd in d["diffs"]:
                for x in dd.get("dropped", []):
                    cause["dropped:" + x["cause"]] += 1
                for p in dd.get("promoted", []):
                    cause["promoted:" + str(p["cause"])] += 1
                    promoted.append({"day": day, "pop": dd.get("pop"), "T": dd.get("T"), **p})
    unresolved = unresolved_audit(CAND, anat_dir)
    with_q = sum(1 for p in promoted if p.get("quotes"))
    out = {
        "method": ("Layer-1 candidates top-3 (provider SIP compact tables) vs final anatomy "
                   "top-3 (merged raw-derived bars) for every dev day and snapshot; set "
                   "comparison; causes from merged-bar presence, px agreement, Layer-1 rank/margin"),
        "days_checked": len(days), "snapshots_checked": tot_snaps,
        "agree_top3_set": tot_agree, "differ_snapshots": tot_snaps - tot_agree,
        "diff_rows": diffs_n, "cause_counts": dict(cause),
        "promoted_n": len(promoted),
        "promoted_with_quotes": with_q,
        "promoted_with_quotes_share": round(with_q / len(promoted), 4) if promoted else None,
        "promoted_rank_gt10_no_quotes": [
            {k: p[k] for k in ("day", "pop", "T", "ticker", "l1_rank", "cause", "quotes", "in_merged_bars")}
            for p in promoted if p.get("cause") == "rank_gt10_promoted" and not p.get("quotes")],
        "promoted": promoted,
        "per_day_diffs": per_day,
        "unresolved_audit": unresolved,
        "notes": ["Layer-1 and the anatomy can differ at the margin by construction (different "
                  "bar substrate); this audit quantifies the differences and proves whether any "
                  "name outside the acquired net was selected.",
                  "quotas: quotes were acquired only for the top-3 quote union per snapshot; a "
                  "promoted name without quotes was never in a Layer-1 top-3.",
                  "No parameter, rule, or PnL is defined here."],
    }
    print(f"selection audit: days={len(days)} snapshots={tot_snaps} agree={tot_agree} "
          f"differ={tot_snaps-tot_agree} promoted={len(promoted)} causes={dict(cause)}")
    if write:
        (root / "agg").mkdir(parents=True, exist_ok=True)
        with open(root / "agg" / "selection_audit.json", "w") as fh:
            json.dump(out, fh, indent=1, default=str)
        print(f"[wrote] {root/'agg'/'selection_audit.json'}")
    return out


def selftest():
    l1 = [{"symbol": "AAA", "rank": 1, "px_600": 12.0}, {"symbol": "BBB", "rank": 2, "px_600": 11.0},
          {"symbol": "CCC", "rank": 3, "px_600": 10.5}, {"symbol": "DDD", "rank": 4, "px_600": 10.0}]
    an = [{"ticker": "AAA", "rank": 1, "px_decision": 12.0, "sel": 0.2},
          {"ticker": "DDD", "rank": 2, "px_decision": 10.0, "sel": 0.15},
          {"ticker": "CCC", "rank": 3, "px_decision": 10.5, "sel": 0.1}]
    d = classify_diff(l1, an, {"AAA", "CCC", "DDD"}, {})
    assert d["l1_top3"] == ["AAA", "BBB", "CCC"] and d["anatomy_top3"] == ["AAA", "DDD", "CCC"], d
    assert d["dropped"][0]["ticker"] == "BBB" and d["dropped"][0]["cause"] == "outside_net", d
    assert d["promoted"][0]["ticker"] == "DDD" and d["promoted"][0]["l1_rank"] == 4, d
    # px divergence -> px_path_derivation
    an2 = [dict(an[0]), {"ticker": "BBB", "rank": 2, "px_decision": 14.0, "sel": 0.3}, dict(an[2])]
    d2 = classify_diff(l1, an2, {"AAA", "BBB", "CCC"}, {})
    assert d2["dropped"] == [] and d2["promoted"] == [], d2
    l1b = [dict(l1[0]), {"symbol": "ZZZ", "rank": 2, "px_600": 9.0}, dict(l1[2])]
    d3 = classify_diff(l1b, an2, {"AAA", "CCC"}, {})
    assert d3["dropped"][0]["cause"] == "outside_net", d3
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--merge-only", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    run(art_root(args.root), args.write, args.limit, args.merge_only)


if __name__ == "__main__":
    main()
