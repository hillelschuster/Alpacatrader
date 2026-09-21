#!/usr/bin/env python3
"""BASKET-01 market-opportunity -> basket-capture funnel (2026-09-21).

Turns the market base-rate artifact into a conditional bridge. For every dev day and
every market monster (session-high based, computed on the FULL PIT universe under two
separate anchors -- RTH open and previous close), questions answered:

  * where was that mover in OUR ranking at A_pm / A_open / B(09:35..12:00)?
    (rank computed with the frozen population rules on the SIP compact tables)
  * if it was in our top-3: was the fill accessible, how much of its eventual move
    (post-fill high, touch lens) remained ahead of the fill, and did the
    strict above-lens ladder reach the monster ruler?
  * if we missed the ultimate monster: how often did ANOTHER member of the basket
    nevertheless deliver a +30/+50/+100 post-entry excursion?

Anchors stay explicit and separate: hi_open monsters, hi_prev monsters, and basket
post-entry excursions are three different objects and are never subtracted or pooled.
The thesis does not require capturing the eventual #1 -- this table measures how often
the small basket does anyway, and what the misses still produced.

Diagnostic only. Rulers remain rulers; no parameter is chosen; no strategy code.

Outputs (resumable):
  data/sip/funnel_stage/<day>.json     per-day rows (scratch, gitignored data/)
  <root>/agg/capture_funnel.json       merged committed table

Usage:
  .venv/bin/python factory/scripts/basket_capture_funnel.py --self-test
  BASKET_ART_ROOT=factory/artifacts/basket/sip .venv/bin/python factory/scripts/basket_capture_funnel.py --days 2021-02-01
  BASKET_ART_ROOT=factory/artifacts/basket/sip .venv/bin/python factory/scripts/basket_capture_funnel.py --all --workers 3
  .venv/bin/python factory/scripts/basket_capture_funnel.py --merge-only
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_anatomy as ba  # noqa: E402

ART = Path(os.environ.get("BASKET_ART_ROOT", str(ROOT / "factory" / "artifacts" / "basket")))
ANAT = ART / "anatomy"
UNI = ROOT / "data" / "sip" / "universe" / "rth"
PMU = ROOT / "data" / "sip" / "universe" / "premarket"
STAGE = ROOT / "data" / "sip" / "funnel_stage"
H_LIST = [50, 100, 200]
TOUCH_RULERS = [30, 50, 100]
SUMMARY_VIEWS = [("A_pm", 570), ("A_pm31", 570), ("A_open", 570), ("B", 600)]


def with_prev(df: pl.DataFrame, prev: dict) -> pl.DataFrame:
    if not prev:
        return df.with_columns(pl.lit(None, dtype=pl.Float64).alias("pclose"))
    pdf = pl.DataFrame({"symbol": list(prev.keys()),
                        "pclose": [float(v) for v in prev.values()]})
    return df.join(pdf, on="symbol", how="left")


def rank_table(uni: pl.DataFrame, pm: pl.DataFrame | None, prev: dict, pop: str, T: int) -> dict:
    if pop == "B":
        c = f"px_{T}"
        sub = uni.filter((pl.col("o570") > 0) & (pl.col(c) >= ba.MIN_PRICE))
        sub = sub.with_columns((pl.col(c) / pl.col("o570") - 1).alias("score"))
    elif pop == "A_open":
        sub = with_prev(uni.filter(pl.col("o570") >= ba.MIN_PRICE), prev)
        sub = sub.filter(pl.col("pclose") > 0)
        sub = sub.with_columns((pl.col("o570") / pl.col("pclose") - 1).alias("score"))
    else:
        if pm is None:
            return {}
        sub = pm.filter(pl.col("pm_last_et").is_not_null()
                        & (pl.col("pm_last_et") <= 569)
                        & ((569 - pl.col("pm_last_et")) <= ba.FRESH_MIN)
                        & (pl.col("pm_last_px") >= ba.MIN_PRICE))
        sub = with_prev(sub, prev).filter(pl.col("pclose") > 0)
        sub = sub.with_columns((pl.col("pm_last_px") / pl.col("pclose") - 1).alias("score"))
    if sub.height == 0:
        return {}
    sub = sub.sort(["score", "symbol"], descending=[True, False])
    return {s: i + 1 for i, s in enumerate(sub["symbol"].to_list())}


def bucket_of(rank: int | None) -> str:
    if rank is None:
        return "absent"
    if rank <= 3:
        return "1-3"
    if rank <= 10:
        return "4-10"
    if rank <= 50:
        return "11-50"
    if rank <= 100:
        return "51-100"
    return ">100"


def best_above_ladder(n: dict) -> int | None:
    f = n.get("fill") or {}
    if not f or f.get("blocked") or f.get("px") is None:
        return None
    thr_base = float(f["px"])
    best = None
    for H in ba.UP:
        u = (n.get("ladders") or {}).get("up", {}).get(str(H))
        if u is not None and u.get("exec") is not None and float(u["exec"]) >= thr_base * (1 + H / 100.0):
            best = H
    return best


def day_rows(day: str, uni: pl.DataFrame, pm: pl.DataFrame | None, prev: dict, rec: dict):
    mon: dict = {}
    # monster eligibility matches the frozen basket universe: price >= $1 at the
    # decision open (same floor as market_base_rates.json), never sub-$1 names
    base = uni.filter(pl.col("o570") >= ba.MIN_PRICE)
    for H in H_LIST:
        mon[("hi_open", H)] = set(
            base.filter((pl.col("hi") / pl.col("o570") - 1) >= H / 100.0)["symbol"].to_list())
        pv = with_prev(base, prev).filter(pl.col("pclose") > 0)
        mon[("hi_prev", H)] = set(
            pv.filter((pl.col("hi") / pl.col("pclose") - 1) >= H / 100.0)["symbol"].to_list())
    snaps = {(s["pop"], s["T"]): s for s in rec["snapshots"]}
    ranks = {}
    for key in [("A_open", 570), ("A_pm", 570), ("A_pm31", 570)] + [("B", T) for T in ba.T_LIST]:
        ranks[key] = rank_table(uni, pm, prev, key[0], key[1])
    rows = []
    for anchor in ("hi_open", "hi_prev"):
        for H in H_LIST:
            ms = mon[(anchor, H)]
            if not ms:
                continue
            for key, snap in snaps.items():
                names3 = snap["names"][:3]
                snames = [n["ticker"] for n in names3]
                rk = ranks.get(key) or {}
                rr = [rk[m] for m in ms if m in rk]
                best = min(rr) if rr else None
                in3 = [t for t in snames if t in ms]
                row = {"anchor": anchor, "H": H, "pop": key[0], "T": key[1],
                       "n_monsters": len(ms), "best_rank": best, "bucket": bucket_of(best),
                       "in_top3": bool(in3), "n_in_top3": len(in3)}
                if in3:
                    mm = next(n for n in names3 if n["ticker"] == in3[0])
                    f = mm.get("fill") or {}
                    acc = bool(f) and not f.get("blocked")
                    row["accessible"] = acc
                    mfe = mm.get("mfe")
                    row["monster_mfe"] = float(mfe) if (acc and mfe is not None) else None
                    row["monster_above"] = best_above_ladder(mm) if acc else None
                    a = mm.get("open0930") if anchor == "hi_open" else mm.get("prev_close")
                    hi = mm.get("day_high")
                    if (acc and mfe is not None and a and hi and hi > a and f.get("px")):
                        fill = float(f["px"])
                        post_hi = fill * (1.0 + float(mfe))
                        row["monster_ahead_share"] = (post_hi - fill) / (float(hi) - float(a))
                else:
                    accs = [n for n in names3
                            if (n.get("fill") and not n["fill"].get("blocked"))]
                    for R in TOUCH_RULERS:
                        row[f"other_touch_{R}"] = any(
                            (n.get("ladders") or {}).get("up", {}).get(str(R)) is not None
                            for n in accs)
                    row["other_above_100"] = any(
                        (best_above_ladder(n) or 0) >= 100 for n in accs)
                    row["other_best_mfe"] = max(
                        (float(n["mfe"]) for n in accs if n.get("mfe") is not None), default=None)
                rows.append(row)
    return rows


def stage_day(day: str, force: bool) -> str:
    sp = STAGE / f"{day}.json"
    if sp.exists() and not force:
        return "skip"
    ap = ANAT / f"{day}.jsonl"
    up = UNI / f"{day}.parquet"
    if not (ap.exists() and up.exists()):
        return "missing"
    rec = json.load(open(ap))
    uni = pl.read_parquet(up)
    pm = None
    pp = PMU / f"{day}.parquet"
    if pp.exists():
        pm = pl.read_parquet(pp, columns=["symbol", "pm_last_et", "pm_last_px"])
    days = sorted(Path(p).name[:10] for p in glob.glob(str(UNI / "*.parquet")))
    i = days.index(day) if day in days else None
    prev = {}
    if i is not None and i > 0:
        pdf = pl.read_parquet(UNI / f"{days[i-1]}.parquet", columns=["symbol", "c_last"])
        prev = {s: c for s, c in zip(pdf["symbol"].to_list(), pdf["c_last"].to_list())
                if c is not None and c > 0}
    rows = day_rows(day, uni, pm, prev, rec)
    if not rows:
        # an empty result must DELETE any stale staged file: otherwise an older
        # (pre-floor) staging survives re-runs and the merge keeps reading it
        if sp.exists():
            sp.unlink()
        return "empty"
    STAGE.mkdir(parents=True, exist_ok=True)
    tmp = sp.with_suffix(".json.tmp")
    with open(tmp, "w") as fh:
        json.dump({"date": day, "rows": rows}, fh, separators=(",", ":"), default=str)
    os.replace(tmp, sp)
    return f"ok({len(rows)})"


def _q(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return None
    a = np.array(v, dtype=float)
    return {"n": int(len(a)), "p50": round(float(np.percentile(a, 50)), 4),
            "p90": round(float(np.percentile(a, 90)), 4)}


def merge(out_dir: Path) -> dict:
    grp: dict = defaultdict(list)
    offered = defaultdict(set)
    for f in sorted(glob.glob(str(STAGE / "*.json"))):
        st = json.load(open(f))
        for r in st["rows"]:
            key = (r["anchor"], r["H"], r["pop"], r["T"])
            grp[key].append(r)
            offered[(r["anchor"], r["H"])].add(st["date"])
    table = []
    for (anchor, H, pop, T), rows in sorted(grp.items()):
        buckets = defaultdict(int)
        for r in rows:
            buckets[r["bucket"]] += 1
        contains = [r for r in rows if r["in_top3"]]
        misses = [r for r in rows if not r["in_top3"]]
        entry = {
            "anchor": anchor, "H": H, "pop": pop, "T": T,
            "days": len(rows), "buckets": dict(buckets),
            "contains_days": len(contains),
            "contains_share": round(len(contains) / len(rows), 4) if rows else None,
            "contains_accessible_days": sum(1 for r in contains if r.get("accessible")),
            "contains_blocked_days": sum(1 for r in contains if not r.get("accessible")),
            "monster_mfe_q": _q([r.get("monster_mfe") for r in contains]),
            "monster_ahead_share_q": _q([r.get("monster_ahead_share") for r in contains]),
            "monster_above_H_days": (sum(1 for r in contains if (r.get("monster_above") or 0) >= H)
                                     if H <= 100 else None),
            "miss_days": len(misses),
            "miss_other_touch_30": sum(1 for r in misses if r.get("other_touch_30")),
            "miss_other_touch_50": sum(1 for r in misses if r.get("other_touch_50")),
            "miss_other_touch_100": sum(1 for r in misses if r.get("other_touch_100")),
            "miss_other_above_100": sum(1 for r in misses if r.get("other_above_100")),
            "miss_other_best_mfe_q": _q([r.get("other_best_mfe") for r in misses]),
        }
        table.append(entry)
    summary = {}
    for anchor in ("hi_open", "hi_prev"):
        for H in H_LIST:
            views = {}
            for pop, T in SUMMARY_VIEWS:
                e = next((x for x in table if x["anchor"] == anchor and x["H"] == H
                          and x["pop"] == pop and x["T"] == T), None)
                if e:
                    views[f"{pop}/{T}"] = e
            summary[f"{anchor}/H{H}"] = {
                "days_offered": len(offered.get((anchor, H), set())), "views": views}
    out = {"summary": summary, "table": table,
           "_producer": "basket_capture_funnel.py",
           "_definitions": (
               "monster = day with >=1 full-PIT name whose session high reached +H over the "
               "stated anchor (hi_open = RTH open, hi_prev = previous close); rank = position "
               "in the frozen population ranking computed on SIP compact tables; contains = the "
               "day's anatomy top-3 included at least one such +H-qualified name for that "
               "(pop,T) -- it does NOT mean the top-3 contained the singular eventual champion "
               "(that object is the containment table's top1_in); monster_mfe = post-fill "
               "high (touch lens, accessible fills only); ahead_share = (post-fill high - fill) "
               "/ (day high - anchor); miss_* = on days no +H-qualified name was in our top-3, "
               "what the accessible top-3 members still delivered (touch lens)."),
           "notes": ["hi_open, hi_prev and post-entry excursions are separate objects; never "
                     "subtract or pool them.", "Diagnostic only; no parameter selected."]}
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "capture_funnel.json", "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(f"capture_funnel: {len(table)} (anchor,H,pop,T) rows -> {out_dir/'capture_funnel.json'}")
    return out


def selftest():
    assert bucket_of(None) == "absent" and bucket_of(2) == "1-3" and bucket_of(11) == "11-50"
    assert bucket_of(51) == "51-100" and bucket_of(101) == ">100"
    uni = pl.DataFrame({
        "symbol": ["AAA", "BBB", "CCC", "DDD"],
        "o570": [10.0, 20.0, 5.0, 8.0],
        "hi": [50.0, 21.0, 6.0, 40.0],
        "c_last": [45.0, 20.5, 5.5, 30.0],
        "px_600": [42.0, 20.0, 5.4, 25.0],
        "prev_close": [10.0, 20.0, 4.0, 9.0],
    })
    for T in ba.T_LIST:
        key = f"px_{T}"
        if key not in uni.columns:
            uni = uni.with_columns(pl.col("c_last").alias(key))
    pm = None
    prev = {"AAA": 10.0, "BBB": 20.0, "CCC": 4.0, "DDD": 9.0}
    rk = rank_table(uni, pm, prev, "B", 600)
    assert rk["AAA"] == 1 and set(rk) == {"AAA", "BBB", "CCC", "DDD"}, rk
    rec = {"snapshots": [
        {"pop": "B", "T": 600, "names": [
            {"ticker": "AAA", "fill": {"px": 30.0, "blocked": False}, "mfe": 0.50,
             "open0930": 10.0, "day_high": 50.0,
             "ladders": {"up": {"50": {"exec": 46.0}, "100": None}}},
            {"ticker": "BBB", "fill": {"px": 20.0, "blocked": False}, "mfe": 0.05,
             "open0930": 20.0, "day_high": 21.0, "ladders": {"up": {"50": None}}},
            {"ticker": "CCC", "fill": {"px": 5.4, "blocked": False}, "mfe": 0.11,
             "open0930": 5.0, "day_high": 6.0, "ladders": {"up": {"50": None}}}]}]}
    rows = day_rows("2021-02-01", uni, pm, prev, rec)
    r = next(x for x in rows if x["anchor"] == "hi_open" and x["H"] == 100 and x["pop"] == "B")
    assert r["in_top3"] and r["accessible"] and r["n_monsters"] >= 1, r
    assert r["monster_above"] == 50, r
    r50 = next(x for x in rows if x["anchor"] == "hi_open" and x["H"] == 50 and x["pop"] == "B")
    assert r50["in_top3"] and r50["monster_above"] == 50, r50
    assert abs(r50["monster_ahead_share"] - ((30.0 * 1.5 - 30.0) / (50.0 - 10.0))) < 1e-9, r50
    ba50 = best_above_ladder(rec["snapshots"][0]["names"][0])
    assert ba50 == 50, ba50
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="*", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--merge-only", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--root", default=str(ART))
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    if args.merge_only:
        merge(Path(args.root) / "agg")
        return
    days = list(args.days or [])
    if args.all:
        days = sorted(Path(f).name[:10] for f in glob.glob(str(ANAT / "*.jsonl")))
    if not days:
        ap.error("provide --days/--all/--merge-only")
    if args.workers > 1 and len(days) > 1:
        parts = [[] for _ in range(args.workers)]
        for i, d in enumerate(sorted(days)):
            parts[i % args.workers].append(d)
        procs = []
        for i, part in enumerate(parts):
            cmd = [sys.executable, "-u", str(Path(__file__)), "--days", *part]
            if args.force:
                cmd.append("--force")
            log = open(f"/tmp/opencode/funnel_w{i}.log", "w")
            procs.append((subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT), log))
        for p, log in procs:
            rc = p.wait()
            log.close()
            print(f"worker rc={rc}")
    else:
        counts = defaultdict(int)
        for i, d in enumerate(days, 1):
            res = stage_day(d, args.force)
            counts[res.split("(")[0]] += 1
            if i % 50 == 0 or len(days) <= 5:
                print(f"[{i}/{len(days)}] {d}: {res}", flush=True)
        print(f"staged: {dict(counts)}")


if __name__ == "__main__":
    main()
