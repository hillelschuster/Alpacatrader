#!/usr/bin/env python3
"""BASKET-01 T5 path-shape pass — genuine trade-level chronology (2026-09-21 rebuild).

Replaces the bars+peak-minute-ordering pass. For every filled main/adj member of the
SIP anatomy, the post-fill path is built trade-by-trade from the raw SIP prints that
count as price updates under the same condition policy the derived bars use
(sip_bars.combine, hl == update-high/low). No minute-level ordering ambiguity remains
for any bar: the sequence IS the sequence.

Objects (PRE-REG T5, rulers stay rulers):
    retr_pre_hi   = deepest trade price / running max - 1 before first touch of the peak
    retr_after_hi = deepest trade price / peak - 1 from the peak onward
    eod_vs_hi     = last eligible print of the session / peak - 1
    peak_vs_fill  = peak / fill - 1
    time_to_hi    = peak print time minus first post-fill print time (minutes)

Members with no eligible prints after the fill are counted as unresolved, never
dropped silently. Rows keep (month, pop, T, set, stratum, stat) dimensions; strata
are all / mfe>=20 / 30 / 50 / 100. Coverage class per member (healthy_raw /
provider_only) is stored so provider-only sparsity stays visible; the merge reports
class counts and a reconciliation of trade-level peak vs the stored bar-based MFE.

Measurement only. No release rule, no survivor rule, no strategy code.

Outputs (resumable):
  data/sip/t5_raw/<day>.json      per-day member stats (scratch, gitignored data/)
  <root>/agg/T5_paths.json        merged committed table

Usage:
  .venv/bin/python factory/scripts/basket_t5_rawpaths.py --self-test
  BASKET_ART_ROOT=factory/artifacts/basket/sip .venv/bin/python factory/scripts/basket_t5_rawpaths.py --days 2021-02-01
  BASKET_ART_ROOT=factory/artifacts/basket/sip .venv/bin/python factory/scripts/basket_t5_rawpaths.py --all --workers 4
  .venv/bin/python factory/scripts/basket_t5_rawpaths.py --merge-only
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
import sip_bars as sb  # noqa: E402

ART = Path(os.environ.get("BASKET_ART_ROOT", str(ROOT / "factory" / "artifacts" / "basket")))
ANAT = ART / "anatomy"
BARS = ART / "bars"
OUT = ART / "agg"
TRADES = ROOT / "data" / "sip" / "net" / "trades"
COV = ROOT / "data" / "sip" / "net" / "coverage"
STAGE = ROOT / "data" / "sip" / "t5_raw"
UP_STRATA = [20, 30, 50, 100]
PRIMARY_N = 3
SPARSE_MIN = 5
STATS = ("time_to_hi", "retr_pre_hi", "retr_after_hi", "eod_vs_hi", "peak_vs_fill")


def member_rows(rec):
    out = []
    for s in rec.get("snapshots", []):
        names = s["names"]
        for setname, mset in (("main", names[:PRIMARY_N]), ("adj", names[PRIMARY_N:2 * PRIMARY_N])):
            for n in mset:
                f = n.get("fill")
                if not f or f.get("blocked"):
                    continue
                out.append({"pop": s["pop"], "T": s["T"], "set": setname,
                            "ticker": n["ticker"], "fill_et": int(f["et"]),
                            "fill_px": float(f["px"]), "mfe": n.get("mfe")})
    return out


def path_stats(prices: np.ndarray, ts_us: np.ndarray, fill_px: float):
    if len(prices) == 0:
        return None
    runmax = np.maximum.accumulate(prices)
    k = int(np.argmax(prices))
    peak = float(prices[k])
    retr_pre = float((prices[:k + 1] / runmax[:k + 1] - 1.0).min())
    retr_post = float((prices[k:] / peak - 1.0).min())
    return {
        "time_to_hi": float((int(ts_us[k]) - int(ts_us[0])) / 60_000_000.0),
        "retr_pre_hi": retr_pre,
        "retr_after_hi": retr_post,
        "eod_vs_hi": float(prices[-1] / peak - 1.0),
        "peak_vs_fill": float(peak / fill_px - 1.0),
        "n_trades": int(len(prices)),
    }


def price_updating(trades: pl.DataFrame) -> pl.DataFrame:
    """Trades that can update high/low under Alpaca's documented bar rules."""
    t = trades.with_columns(pl.col("conditions").list.join("|").alias("_ck"))
    key = t.select(["_ck", "tape"]).unique()
    rows = []
    for ck, tp in key.iter_rows():
        conds = ck.split("|") if ck else []
        _oc, hl, _v, _unk = sb.combine(conds, tp)
        rows.append({"_ck": ck, "tape": tp, "hl": hl})
    rules = pl.DataFrame(rows)
    return t.join(rules, on=["_ck", "tape"], how="left").filter(pl.col("hl") == sb.G)


def day_stats(day: str, rec: dict, trades: pl.DataFrame | None):
    rows = member_rows(rec)
    if not rows:
        return None
    need = sorted({r["ticker"] for r in rows})
    frame: pl.DataFrame = trades if trades is not None else pl.DataFrame(
        schema={"symbol": pl.Utf8, "ts_utc": pl.Datetime("us", "UTC"),
                "price": pl.Float64, "et": pl.Int32})
    tdf = price_updating(frame.filter(pl.col("symbol").is_in(need)))
    by_sym: dict = {}
    for s, sub in tdf.sort(["symbol", "ts_utc"]).group_by("symbol", maintain_order=True):
        key = s[0] if isinstance(s, tuple) else s
        by_sym[key] = (sub["price"].to_numpy(),
                       sub["ts_utc"].cast(pl.Int64).to_numpy(),
                       sub["et"].to_numpy())
    cls_map = {}
    cp = COV / f"{day}.json"
    if cp.exists():
        cls_map = {k: v.get("cls") for k, v in (json.load(open(cp)).get("per_symbol") or {}).items()}
    members, n_no_trades, n_sparse = [], 0, 0
    for r in rows:
        got = by_sym.get(r["ticker"])
        if got is None:
            n_no_trades += 1
            continue
        px, ts, et = got
        mask = et >= r["fill_et"]
        if not mask.any():
            n_no_trades += 1
            continue
        pr, tv = px[mask], ts[mask]
        if len(pr) < SPARSE_MIN:
            n_sparse += 1
        st = path_stats(pr, tv, r["fill_px"])
        if st is None:
            n_no_trades += 1
            continue
        members.append({"pop": r["pop"], "T": r["T"], "set": r["set"], "ticker": r["ticker"],
                        "mfe": r["mfe"], "cls": cls_map.get(r["ticker"]), **st})
    return {"date": day, "members": members, "no_trades": n_no_trades, "sparse": n_sparse}


def stage_day(day: str, force: bool) -> str:
    sp = STAGE / f"{day}.json"
    if sp.exists() and not force:
        return "skip"
    ap = ANAT / f"{day}.jsonl"
    tp = TRADES / f"{day}.parquet"
    if not (ap.exists() and tp.exists()):
        return "missing"
    rec = json.load(open(ap))
    trades = (pl.scan_parquet(tp).select(["symbol", "ts_utc", "price", "conditions", "tape"])
              .with_columns(pl.col("ts_utc").dt.convert_time_zone("America/New_York")
                            .alias("tset"))
              .with_columns((pl.col("tset").dt.hour().cast(pl.Int32) * 60
                             + pl.col("tset").dt.minute().cast(pl.Int32)).alias("et"))
              .filter((pl.col("et") >= 570) & (pl.col("et") < 960))
              .collect())
    st = day_stats(day, rec, trades)
    if st is None:
        return "empty"
    STAGE.mkdir(parents=True, exist_ok=True)
    tmp = sp.with_suffix(".json.tmp")
    with open(tmp, "w") as fh:
        json.dump(st, fh, separators=(",", ":"), default=str)
    os.replace(tmp, sp)
    return f"ok({len(st['members'])})"


def _q(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return None
    a = np.array(v, dtype=float)
    return {"n": int(len(a)), "mean": round(float(a.mean()), 5),
            "p10": round(float(np.percentile(a, 10)), 5),
            "p50": round(float(np.percentile(a, 50)), 5),
            "p90": round(float(np.percentile(a, 90)), 5)}


def merge(out_dir: Path) -> dict:
    acc: dict = defaultdict(list)
    n_members = n_days = n_no = n_sparse = 0
    cls_counts = defaultdict(int)
    recon = []
    for f in sorted(glob.glob(str(STAGE / "*.json"))):
        st = json.load(open(f))
        n_days += 1
        n_no += st.get("no_trades", 0)
        n_sparse += st.get("sparse", 0)
        for m in st["members"]:
            n_members += 1
            cls_counts[m.get("cls") or "unknown"] += 1
            if m.get("mfe") is not None:
                recon.append(abs(m["peak_vs_fill"] - m["mfe"]))
            month = st["date"][:7]
            for stat in STATS:
                acc[(month, m["pop"], m["T"], m["set"], "all", stat)].append(m[stat])
                if m.get("mfe") is not None:
                    for cut in UP_STRATA:
                        if m["mfe"] >= cut / 100.0:
                            acc[(month, m["pop"], m["T"], m["set"], f"mfe>={cut}",
                                 stat)].append(m[stat])
    tables = []
    for (month, pop, T, setn, stratum, stat), vals in sorted(acc.items()):
        q = _q(vals)
        if q:
            tables.append({"month": month, "pop": pop, "T": T, "set": setn,
                           "stratum": stratum, "stat": stat, **q})
    r = np.array(recon) if recon else np.array([0.0])
    out = {"n_days": n_days, "n_members": n_members,
           "n_no_trades": n_no, "n_sparse_lt5": n_sparse,
           "class_counts": dict(cls_counts),
           "strata": ["all"] + [f"mfe>={c}" for c in UP_STRATA],
           "method": "trade-level chronological path from raw SIP prints; condition policy "
                     "= sip_bars alpaca rules (hl updates); no minute ordering assumed",
           "peak_recon_vs_stored_mfe": {
               "n": int(len(r)), "p50_abs_diff": round(float(np.percentile(r, 50)), 8),
               "p90_abs_diff": round(float(np.percentile(r, 90)), 6),
               "share_within_1e-4": round(float((r <= 1e-4).mean()), 4)},
           "tables": tables}
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "T5_paths.json", "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(f"T5_paths: {n_days} staged days, {n_members} members, no_trades={n_no}, "
          f"sparse={n_sparse} -> {out_dir/'T5_paths.json'}")
    return out


def selftest():
    ts = np.array([1, 2, 3, 4, 5, 6], dtype=np.int64) * 60_000_000
    px = np.array([1.0, 1.2, 1.1, 1.5, 1.2, 1.3])
    st = path_stats(px, ts, 1.0)
    assert st is not None
    assert abs(st["retr_pre_hi"] + 1 / 12) < 1e-9, st
    assert abs(st["retr_after_hi"] + 0.2) < 1e-9, st
    assert abs(st["eod_vs_hi"] + 0.13333333) < 1e-7, st
    assert abs(st["peak_vs_fill"] - 0.5) < 1e-9 and st["time_to_hi"] == 3.0, st
    single = path_stats(np.array([2.0]), np.array([60_000_000]), 1.0)
    assert single is not None
    assert single["retr_pre_hi"] == 0.0 and single["time_to_hi"] == 0.0
    tr = pl.DataFrame({
        "symbol": ["X", "X", "X", "X"], "price": [1.0, 2.0, 3.0, 4.0],
        "ts_utc": [1, 2, 3, 4], "conditions": [["@"], ["@"], ["Q"], ["B"]],
        "tape": ["C", "C", "C", "C"],
    }).with_columns(pl.col("ts_utc").cast(pl.Datetime("us", "UTC")))
    keep = price_updating(tr)
    assert keep.height == 3, keep  # auction print Q is not a price update
    tr2 = tr.with_columns(pl.Series("tape", ["C", "C", "C", "A"]))
    keep2 = price_updating(tr2)
    assert keep2.height == 2, keep2  # average-price tape print B/A is not a price update
    rec = {"snapshots": [{"pop": "B", "T": 585, "names": [
        {"ticker": "AAA", "fill": {"et": 571, "px": 1.0, "blocked": False}, "mfe": 1.0},
        {"ticker": "BBB", "fill": {"et": 580, "px": 1.0, "blocked": True}, "mfe": 0.1},
    ]}]}
    rows = member_rows(rec)
    assert len(rows) == 1 and rows[0]["pop"] == "B" and rows[0]["T"] == 585, rows
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="*", default=None)
    ap.add_argument("--months", nargs="*", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--merge-only", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    if args.merge_only:
        merge(Path(args.out))
        return
    days = []
    if args.days:
        days = list(args.days)
    elif args.months or args.all:
        files = sorted(glob.glob(str(ANAT / "*.jsonl")))
        days = [Path(f).name[:10] for f in files]
        if args.months:
            months = set(args.months)
            days = [d for d in days if d[:7] in months]
    if not days:
        ap.error("provide --days/--months/--all/--merge-only")
    if args.workers > 1 and len(days) > 1:
        parts = [[] for _ in range(args.workers)]
        for i, d in enumerate(sorted(days)):
            parts[i % args.workers].append(d)
        procs = []
        for i, part in enumerate(parts):
            cmd = [sys.executable, "-u", str(Path(__file__)), "--days", *part]
            if args.force:
                cmd.append("--force")
            log = open(f"/tmp/opencode/t5_w{i}.log", "w")
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
            if i % 25 == 0 or len(days) <= 5:
                print(f"[{i}/{len(days)}] {d}: {res}", flush=True)
        print(f"staged: {dict(counts)}")


if __name__ == "__main__":
    main()
