#!/usr/bin/env python3
"""BASKET-01 SIP candidate/Snapshot layer — Layer-1 discovery from SIP compact tables.

Reads the full-PIT SIP minute-bar tables (data/sip/universe/rth/YYYY-MM-DD.parquet,
produced by sip_universe.py) and reconstructs the frozen BASKET ranking snapshots
DIRECTLY FROM SIP — no legacy tape in the selection path:

  * A_open : score = o570 / prev_close(SIP) - 1   (prev_close = previous available
             SIP session's c_last; names whose prev session is unavailable are
             reported, never silently gated)
  * B(T)   : score = px_T / o570 - 1              (px_T = SIP close at the last
             completed bar et<=T-1; frozen RTH-open anchor preserved; prev_close
             NOT required for B)
  * winners_open  = top-10 by hi/o570 - 1         (day-max diagnostics)
  * winners_prev  = top-10 by hi/prev_close - 1   (prev-anchored diagnostics)

Candidate net per day = union of per-snapshot top-10, boundary-margin names
(score within MARGIN of the 10th score, SIP-defined), winners_open and winners_prev.
This net drives Layer-2 raw trades/quotes acquisition. The legacy union is NOT
consulted here.

Outputs (atomic; resumable):
  data/sip/candidates/YYYY-MM-DD.json          per-day snapshots + net
  data/sip/candidates/YYYY-MM-DD.manifest.json provenance (inputs+sha256, counts)
  data/sip/candidates/index_candidates.jsonl   rebuilt with --index (never appended
                                               concurrently)

Usage:
  .venv/bin/python factory/scripts/sip_candidates.py --self-test
  .venv/bin/python factory/scripts/sip_candidates.py --day 2021-02-01
  .venv/bin/python factory/scripts/sip_candidates.py --all
  .venv/bin/python factory/scripts/sip_candidates.py --index
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_anatomy as ba  # noqa: E402  (T_LIST, MIN_PRICE — frozen surface)

UNI = ROOT / "data" / "sip" / "universe" / "rth"
PMU = ROOT / "data" / "sip" / "universe" / "premarket"
OUT = ROOT / "data" / "sip" / "candidates"
K_TOP = 10
MARGIN = 0.01  # SIP score margin near the 10th rank (boundary completeness)


# ---------------------------------------------------------------- helpers


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def available_days() -> list:
    return sorted(Path(p).stem for p in glob.glob(str(UNI / "*.parquet")))


def prev_day_of(days: list, day: str):
    import bisect
    i = bisect.bisect_left(days, day) - 1
    return days[i] if i >= 0 else None


def prev_close_map(day: str, days: list):
    pd = prev_day_of(days, day)
    if pd is None:
        return {}, None
    p = UNI / f"{pd}.parquet"
    if not p.exists():
        return {}, pd
    df = pl.read_parquet(p, columns=["symbol", "c_last"])
    return {s: c for s, c in zip(df["symbol"].to_list(), df["c_last"].to_list())
            if c is not None and c > 0}, pd


def top_list(df: pl.DataFrame, score_col: str, extra_cols: list) -> list:
    k = min(K_TOP, df.height)
    top = df.sort(score_col, descending=True).head(k)
    out = []
    for i, r in enumerate(top.iter_rows(named=True), start=1):
        row = {"rank": i, "symbol": r["symbol"], "score": ba.R(r[score_col])}
        for c in extra_cols:
            row[c] = ba.R(r[c]) if r.get(c) is not None else None
        out.append(row)
    return out


def with_prev(df: pl.DataFrame, prev: dict) -> pl.DataFrame:
    if not prev:
        return df.with_columns(pl.lit(None, dtype=pl.Float64).alias("pclose"))
    pdf = pl.DataFrame({"symbol": list(prev.keys()),
                        "pclose": [float(v) for v in prev.values()]})
    return df.join(pdf, on="symbol", how="left")


def snapshot(df: pl.DataFrame, pop: str, T: int, prev: dict):
    """Frozen-population snapshot from SIP compact rows. Returns dict or None."""
    if pop == "A_open":
        sub = df.filter((pl.col("o570") >= ba.MIN_PRICE))
        if prev:
            sub = with_prev(sub, prev)
            sub = sub.filter((pl.col("pclose") > 0))
            sub = sub.with_columns((pl.col("o570") / pl.col("pclose") - 1).alias("score"))
        else:
            return None  # no SIP prev session available: reported, not gated silently
        extra = []
    else:  # B(T)
        pxc, pxe = f"px_{T}", f"px_{T}_et"
        sub = df.filter((pl.col("o570") > 0) & (pl.col(pxc) >= ba.MIN_PRICE))
        sub = sub.with_columns((pl.col(pxc) / pl.col("o570") - 1).alias("score"))
        extra = [pxc, pxe]
    if sub.height == 0:
        return {"pop": pop, "T": T, "n_eligible": 0, "top": [], "margin": [], "cutoff": None}
    top = top_list(sub, "score", extra)
    cutoff = top[-1]["score"] if len(top) == K_TOP else None
    margin = []
    if cutoff is not None:
        in_top = {r["symbol"] for r in top}
        near = sub.filter((pl.col("score") > cutoff - MARGIN))
        margin = sorted(s for s in near["symbol"].to_list() if s not in in_top)
    return {"pop": pop, "T": T, "n_eligible": int(sub.height), "top": top,
            "margin": margin, "cutoff": cutoff}


def pm_top(df: pl.DataFrame, prev: dict, margin=MARGIN):
    """A_pm: SIP premarket last print (<=09:29 ET, freshness <= FRESH_MIN) vs SIP prev close."""
    sub = df.filter((pl.col("pm_last_px") >= ba.MIN_PRICE) & pl.col("pm_last_et").is_not_null()
                    & ((569 - pl.col("pm_last_et")) <= ba.FRESH_MIN) & (pl.col("pm_last_et") <= 569))
    if not prev or sub.height == 0:
        return {"pop": "A_pm", "T": 570, "n_eligible": 0, "top": [], "margin": [], "cutoff": None}
    sub = with_prev(sub, prev).filter(pl.col("pclose") > 0)
    if sub.height == 0:
        return {"pop": "A_pm", "T": 570, "n_eligible": 0, "top": [], "margin": [], "cutoff": None}
    sub = sub.with_columns((pl.col("pm_last_px") / pl.col("pclose") - 1).alias("score"))
    top = top_list(sub, "score", ["pm_last_px", "pm_last_et"])
    cutoff = top[-1]["score"] if len(top) == K_TOP else None
    margin_l = []
    if cutoff is not None:
        in_top = {r["symbol"] for r in top}
        near = sub.filter((pl.col("score") > cutoff - margin))
        margin_l = sorted(s for s in near["symbol"].to_list() if s not in in_top)
    return {"pop": "A_pm", "T": 570, "n_eligible": int(sub.height), "top": top,
            "margin": margin_l, "cutoff": cutoff}


def snapshot_pm(day: str, prev: dict):
    p = PMU / f"{day}.parquet"
    if not p.exists():
        return {"pop": "A_pm", "T": 570, "n_eligible": 0, "top": [], "margin": [],
                "cutoff": None, "skipped": True, "note": "no SIP premarket table for day"}
    df = pl.read_parquet(p, columns=["symbol", "pm_last_et", "pm_last_px", "pm_n_bars"])
    return pm_top(df, prev)


def winners(day_df: pl.DataFrame, prev: dict, anchor: str) -> list:
    """top-10 by day-max hi over an anchor (open or prev close)."""
    if anchor == "open":
        sub = day_df.filter(pl.col("o570") > 0).with_columns(
            (pl.col("hi") / pl.col("o570") - 1).alias("score"))
    else:
        if not prev:
            return []
        sub = with_prev(day_df, prev)
        sub = sub.filter(pl.col("pclose") > 0).with_columns(
            (pl.col("hi") / pl.col("pclose") - 1).alias("score"))
    return top_list(sub, "score", ["hi"])


def build_day(day: str, days: list, margin=MARGIN) -> dict:
    p = UNI / f"{day}.parquet"
    day_df = pl.read_parquet(p)
    prev, pd = prev_close_map(day, days)
    snaps = []
    a = snapshot(day_df, "A_open", 570, prev)
    if a is None:
        a = {"pop": "A_open", "T": 570, "n_eligible": 0, "top": [], "margin": [],
             "cutoff": None, "note": "no SIP prev session available"}
    snaps.append(a)
    snaps.append(snapshot_pm(day, prev))
    for T in ba.T_LIST:
        snaps.append(snapshot(day_df, "B", T, prev))
    w_open = winners(day_df, prev, "open")
    w_prev = winners(day_df, prev, "prev")
    net = set()
    for s in snaps:
        net |= {r["symbol"] for r in s["top"]}
        net |= set(s["margin"])
    net |= {r["symbol"] for r in w_open} | {r["symbol"] for r in w_prev}
    return {
        "day": day, "prev_day": pd,
        "rows_in": int(day_df.height),
        "snapshots": snaps,
        "winners_open": w_open, "winners_prev": w_prev,
        "net": sorted(net), "net_n": len(net),
        "margin": margin,
        "notes": ["selection from SIP compact tables only; legacy union not consulted"]
                 + ([] if prev else ["A_open skipped: no SIP prev session"]),
    }


def write_day(day: str, rec: dict, src: Path, elapsed: float) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    jp, mp = OUT / f"{day}.json", OUT / f"{day}.manifest.json"
    tmp = jp.with_suffix(".json.tmp")
    with open(tmp, "w") as fh:
        json.dump(rec, fh, separators=(",", ":"), default=str)
    os.replace(tmp, jp)
    man = {
        "day": day, "status": "ok", "provider": "alpaca", "feed": "sip",
        "layer": "candidates", "source": str(src.relative_to(ROOT)),
        "source_sha256": sha256(src), "rows_in": rec["rows_in"],
        "prev_day": rec["prev_day"], "net_n": rec["net_n"],
        "snapshot_counts": [{"pop": s["pop"], "T": s["T"], "n": s["n_eligible"],
                             "top": len(s["top"]), "margin": len(s["margin"])}
                            for s in rec["snapshots"]],
        "elapsed_s": round(elapsed, 2), "schema": "candidates-v1",
    }
    tmp = mp.with_suffix(".json.tmp")
    with open(tmp, "w") as fh:
        json.dump(man, fh, indent=1, default=str)
    os.replace(tmp, mp)


def should_skip(day: str, force: bool) -> bool:
    if force:
        return False
    mp = OUT / f"{day}.manifest.json"
    jp = OUT / f"{day}.json"
    if not (mp.exists() and jp.exists()):
        return False
    try:
        return json.load(open(mp)).get("status") == "ok"
    except Exception:
        return False


def rebuild_index() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    lines = []
    for mp in sorted(glob.glob(str(OUT / "*.manifest.json"))):
        m = json.load(open(mp))
        if m.get("status") == "ok":
            lines.append(json.dumps({"day": m["day"], "net_n": m["net_n"],
                                     "prev_day": m["prev_day"]}, separators=(",", ":")))
    with open(OUT / "index_candidates.jsonl", "w") as fh:
        fh.write("\n".join(lines) + ("\n" if lines else ""))
    return len(lines)


# ---------------------------------------------------------------- self-test


def _mini_frame():
    return pl.DataFrame({
        "symbol": ["AAA", "BBB", "CCC", "DDD", "EEE"],
        "o570":   [10.0,  20.0,  5.0,   None,  8.0],
        "px_585": [12.0,  21.0,  4.0,   9.0,   8.2],
        "px_585_et": [584, 584, 584, 584, 584],
        "hi":     [13.0,  22.0,  6.0,   9.5,   8.4],
        "c_last": [11.0,  20.5,  4.5,   9.0,   8.1],
    })


def selftest():
    df = _mini_frame()
    prev = {"AAA": 9.0, "BBB": 25.0, "CCC": 4.0, "EEE": 8.0}
    a = snapshot(df, "A_open", 570, prev)
    assert a is not None and a["n_eligible"] == 4, a
    syms = [r["symbol"] for r in a["top"]]
    # A_open scores: CCC 5/4-1=.25, AAA 10/9-1=.111, EEE 8/8-1=0, BBB 20/25-1=-.2
    assert syms == ["CCC", "AAA", "EEE", "BBB"], syms
    assert a["top"][0]["score"] == ba.R(5.0 / 4.0 - 1)
    b = snapshot(df, "B", 585, prev)
    assert b is not None
    bsyms = [r["symbol"] for r in b["top"]]
    # B scores: AAA 12/10-1=.2, BBB 21/20-1=.05, EEE 8.2/8-1=.025, CCC 4/5-1=-.2
    assert bsyms == ["AAA", "BBB", "EEE", "CCC"], bsyms
    assert b["top"][2]["score"] == ba.R(8.2 / 8.0 - 1)
    assert b["top"][-1]["score"] == ba.R(4.0 / 5.0 - 1)
    # fewer than K_TOP rows -> no cutoff, no margin
    assert b["margin"] == [] and b["cutoff"] is None
    # prev-close independence: B works with prev={}
    b2 = snapshot(df, "B", 585, {})
    assert b2 is not None and len(b2["top"]) == 4
    # A_open reports rather than silently skipping when prev missing
    assert snapshot(df, "A_open", 570, {}) is None
    # A_pm: stale prints excluded; scored vs SIP prev close
    pm = pl.DataFrame({"symbol": ["AAA", "BBB", "CCC", "DDD"],
                       "pm_last_et": [560, 569, 540, 568],
                       "pm_last_px": [10.0, 30.0, 4.0, 9.0]})
    ap = pm_top(pm, {"AAA": 9.0, "BBB": 25.0, "CCC": 4.0, "DDD": 8.0})
    assert [r["symbol"] for r in ap["top"]] == ["BBB", "DDD", "AAA"], ap
    assert ap["n_eligible"] == 3, ap
    assert ap["top"][0]["score"] == ba.R(30.0 / 25.0 - 1), ap
    assert pm_top(pm, {})["n_eligible"] == 0
    # winners open: AAA .3, CCC .2, BBB .1, EEE .05
    wo = winners(df, prev, "open")
    assert [r["symbol"] for r in wo][:2] == ["AAA", "CCC"], wo
    # DDD never appears anywhere (o570 missing)
    all_syms = {r["symbol"] for s in [a, b] for r in s["top"]}
    assert "DDD" not in all_syms
    print("self-test OK")


# ---------------------------------------------------------------- main


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=None)
    ap.add_argument("--months", nargs="*", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--index", action="store_true")
    ap.add_argument("--margin", type=float, default=MARGIN)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    if args.index:
        n = rebuild_index()
        print(f"index rebuilt: {n} days")
        return

    days = available_days()
    todo = []
    if args.day:
        todo = [args.day]
    elif args.months:
        ms = set(args.months)
        todo = [d for d in days if d[:7] in ms]
    elif args.all:
        todo = list(days)
    if not todo:
        raise SystemExit("no days selected (use --day/--months/--all)")
    done = skipped = failed = no_universe = 0
    for day in todo:
        if not (UNI / f"{day}.parquet").exists():
            no_universe += 1
            print(f"{day}: NO UNIVERSE TABLE -> skipped (reported, not silent)")
            continue
        if should_skip(day, args.force):
            skipped += 1
            continue
        t0 = time.time()
        try:
            rec = build_day(day, days, margin=args.margin)
            write_day(day, rec, UNI / f"{day}.parquet", time.time() - t0)
            done += 1
            if done % 25 == 0 or args.day:
                print(f"{day}: net={rec['net_n']} ({time.time()-t0:.1f}s)")
        except Exception as e:  # keep going; manifest absent -> retried next run
            failed += 1
            print(f"{day}: FAILED {type(e).__name__}: {e}")
    print(f"candidates: done={done} skipped={skipped} failed={failed} "
          f"no_universe={no_universe} -> {OUT}")


if __name__ == "__main__":
    main()
