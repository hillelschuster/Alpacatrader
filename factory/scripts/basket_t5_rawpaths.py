#!/usr/bin/env python3
"""BASKET-01 T5 path-shape pass, ordering-resolved (repair of basket_t5_bars.py).

Same objects as the bars pass (PRE-REG T5: max retracement before the session high,
drawdown after the high, EOD vs high, time-to-high, peak vs fill), rebuilt with the
peak bar excluded from both bar segments and the peak minute's own low/high order
resolved from the raw SIP prints:

    retr_pre_hi   = min over bars strictly before the peak bar, PLUS the peak
                    minute's low when the raw prints show low BEFORE high
    retr_after_hi = min over bars strictly after the peak bar, PLUS the peak
                    minute's low when the raw prints show high BEFORE low

Rows are keyed by (month, pop, T, set, stratum, stat) so population and timing
dimensions survive into the read layer (the old table dropped them). Non-peak
minutes keep bar resolution; that ceiling is stated, not hidden.

Measurement only. Rulers remain rulers; no release rule, no strategy code.
Raw trades are read from data/sip/net/trades/<day>.parquet (SIP root only).

Outputs (resumable):
  data/sip/t5_raw/<day>.json      per-day member stats (scratch, gitignored data/)
  <root>/agg/T5_paths.json        merged committed table

Usage:
  .venv/bin/python factory/scripts/basket_t5_rawpaths.py --self-test
  BASKET_ART_ROOT=factory/artifacts/basket/sip .venv/bin/python factory/scripts/basket_t5_rawpaths.py --days 2021-02-01 2021-02-02
  BASKET_ART_ROOT=factory/artifacts/basket/sip .venv/bin/python factory/scripts/basket_t5_rawpaths.py --all
  .venv/bin/python factory/scripts/basket_t5_rawpaths.py --merge-only
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
ART = Path(os.environ.get("BASKET_ART_ROOT", str(ROOT / "factory" / "artifacts" / "basket")))
ANAT = ART / "anatomy"
BARS = ART / "bars"
OUT = ART / "agg"
TRADES = ROOT / "data" / "sip" / "net" / "trades"
STAGE = ROOT / "data" / "sip" / "t5_raw"
UP_STRATA = [20, 30, 50]
SETS = ("main", "adj")
PRIMARY_N = 3
PRICE_TOL = 1e-6


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


def peak_order(trades_sym, peak_et: int, peak_hi: float, peak_lo: float):
    """Order of the peak minute's high vs low from raw prints; None = unresolved."""
    if peak_hi <= peak_lo:
        return None
    m = trades_sym.filter(pl.col("et") == peak_et)
    if m.height == 0:
        return None
    ts = m["ts_utc"].to_list()
    px = m["price"].to_numpy()
    hi_ts = [t for t, p in zip(ts, px) if abs(p - peak_hi) <= PRICE_TOL]
    lo_ts = [t for t, p in zip(ts, px) if abs(p - peak_lo) <= PRICE_TOL]
    if not hi_ts or not lo_ts:
        return None
    if min(lo_ts) < min(hi_ts):
        return "lo_first"
    if min(hi_ts) < min(lo_ts):
        return "hi_first"
    return None


def path_stats(post_et, post_h, post_l, post_c, fill_px, order):
    """Post-fill path shape; peak bar excluded from both segments, its own low
    attributed by the raw-resolved order. Returns dict or None."""
    if len(post_h) == 0:
        return None
    k = int(np.argmax(post_h))
    peak = float(post_h[k])
    retr_pre = None
    if k > 0:
        runmax = np.maximum.accumulate(post_h[:k])
        retr_pre = float((post_l[:k] / runmax - 1.0).min())
    if order == "lo_first":
        base = float(post_h[:k].max()) if k > 0 else fill_px
        v = float(post_l[k]) / base - 1.0
        retr_pre = v if retr_pre is None else min(retr_pre, v)
    retr_post = None
    if k < len(post_l) - 1:
        retr_post = float((post_l[k + 1:] / peak - 1.0).min())
    if order == "hi_first":
        v = float(post_l[k]) / peak - 1.0
        retr_post = v if retr_post is None else min(retr_post, v)
    return {
        "time_to_hi": float(post_et[k] - post_et[0]),
        "retr_pre_hi": retr_pre,
        "retr_after_hi": retr_post,
        "eod_vs_hi": float(post_c[-1] / peak - 1.0),
        "peak_vs_fill": float(peak / fill_px - 1.0),
    }


def day_stats(day: str, rec: dict, bars: pl.DataFrame | None, trades: pl.DataFrame | None):
    rows = member_rows(rec)
    if not rows or bars is None:
        return None
    need = sorted({r["ticker"] for r in rows})
    bdf = bars.filter(pl.col("ticker").is_in(need)).sort(["ticker", "et"])
    if trades is None:
        trades = pl.DataFrame(schema={"symbol": pl.Utf8, "ts_utc": pl.Datetime("us", "UTC"),
                                      "price": pl.Float64, "et": pl.Int32})
    tdf = trades.filter(pl.col("symbol").is_in(need)).sort(["symbol", "ts_utc"])
    members, n_skip, n_ord = [], 0, {"lo_first": 0, "hi_first": 0, "unresolved": 0}
    by_sym_bars: dict = {}
    for t, sub in bdf.group_by("ticker", maintain_order=True):
        by_sym_bars[t[0] if isinstance(t, tuple) else t] = sub
    by_sym_tr: dict = {}
    for s, sub in tdf.group_by("symbol", maintain_order=True):
        by_sym_tr[s[0] if isinstance(s, tuple) else s] = sub
    empty_tr = tdf.clear()
    for r in rows:
        sub = by_sym_bars.get(r["ticker"])
        if sub is None or sub.height == 0:
            n_skip += 1
            continue
        et = sub["et"].to_numpy()
        idx = int(np.searchsorted(et, r["fill_et"], side="left"))
        if idx >= len(et) or int(et[idx]) != r["fill_et"]:
            n_skip += 1
            continue
        hi, lo, cs = sub["high"].to_numpy(), sub["low"].to_numpy(), sub["close"].to_numpy()
        pk = int(np.argmax(hi[idx:])) + idx
        order = peak_order(by_sym_tr.get(r["ticker"], empty_tr), int(et[pk]),
                           float(hi[pk]), float(lo[pk]))
        n_ord["unresolved" if order is None else order] += 1
        st = path_stats(et[idx:], hi[idx:], lo[idx:], cs[idx:], r["fill_px"], order)
        if st is None:
            n_skip += 1
            continue
        members.append({"pop": r["pop"], "T": r["T"], "set": r["set"], "ticker": r["ticker"],
                        "mfe": r["mfe"], "order": order, **st})
    return {"date": day, "members": members, "skip": n_skip, "order": n_ord}


def stage_day(day: str, force: bool) -> str:
    sp = STAGE / f"{day}.json"
    if sp.exists() and not force:
        return "skip"
    ap = ANAT / f"{day}.jsonl"
    bp = BARS / f"{day}.parquet"
    tp = TRADES / f"{day}.parquet"
    if not (ap.exists() and bp.exists() and tp.exists()):
        return "missing"
    rec = json.load(open(ap))
    bars = pl.read_parquet(bp)
    trades = (pl.scan_parquet(tp).select(["symbol", "ts_utc", "price"])
              .with_columns(pl.col("ts_utc").dt.convert_time_zone("America/New_York")
                            .alias("tset"))
              .with_columns((pl.col("tset").dt.hour().cast(pl.Int32) * 60
                             + pl.col("tset").dt.minute().cast(pl.Int32)).alias("et"))
              .filter((pl.col("et") >= 570) & (pl.col("et") < 960))
              .collect())
    st = day_stats(day, rec, bars, trades)
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
    n_members = n_days = 0
    order = defaultdict(int)
    for f in sorted(glob.glob(str(STAGE / "*.json"))):
        st = json.load(open(f))
        n_days += 1
        for m in st["members"]:
            n_members += 1
            order[m["order"] or "unresolved"] += 1
            month = st["date"][:7]
            for stat in ("time_to_hi", "retr_pre_hi", "retr_after_hi", "eod_vs_hi",
                         "peak_vs_fill"):
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
    out = {"n_days": n_days, "n_members": n_members,
           "strata": ["all"] + [f"mfe>={c}" for c in UP_STRATA],
           "method": "bars path + peak-minute raw-trade ordering; peak bar excluded "
                     "from both segments; non-peak minutes remain bar-resolution",
           "peak_minute_order": dict(order), "tables": tables}
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "T5_paths.json", "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(f"T5_paths: {n_days} staged days, {n_members} members -> {out_dir/'T5_paths.json'} "
          f"order={dict(order)}")
    return out


def selftest():
    et = np.array([570, 571, 572, 573, 574, 575, 576])
    hi = np.array([1.0, 1.2, 1.15, 1.3, 1.8, 2.0, 1.6])
    lo = np.array([1.0, 1.1, 1.0, 1.05, 1.4, 1.5, 1.2])
    cl = np.array([1.0, 1.15, 1.05, 1.25, 1.6, 1.9, 1.2])
    # post-fill from index 0: peak bar k=5 (hi 2.0, lo 1.5). Peak bar excluded:
    # pre segment bars 0..4: min(low/runmax-1) = 1.0/1.2-1 = -1/6
    st = path_stats(et, hi, lo, cl, 1.0, None)
    assert st is not None
    assert st["time_to_hi"] == 5.0 and abs(st["retr_pre_hi"] + 2 / 9) < 1e-9, st
    # peak bar excluded from post: bars 6 only -> 1.2/2.0-1 = -0.4
    assert abs(st["retr_after_hi"] + 0.4) < 1e-9, st
    assert abs(st["eod_vs_hi"] + 0.4) < 1e-9 and abs(st["peak_vs_fill"] - 1.0) < 1e-9
    # peak-minute low 1.5 before the high (lo_first): 1.5/1.8-1 = -1/6, dominated by -2/9
    st2 = path_stats(et, hi, lo, cl, 1.0, "lo_first")
    assert st2 is not None
    assert abs(st2["retr_pre_hi"] + 2 / 9) < 1e-9, st2
    assert abs(st2["retr_after_hi"] + 0.4) < 1e-9, st2
    # peak-minute low after the high (hi_first): post = min(bars 6, 1.5/2.0-1) = -0.4
    st3 = path_stats(et, hi, lo, cl, 1.0, "hi_first")
    assert st3 is not None
    assert abs(st3["retr_after_hi"] + 0.4) < 1e-9, st3
    assert abs(st3["retr_pre_hi"] + 2 / 9) < 1e-9, st3
    # first post-fill bar is the peak, low precedes high -> base = fill, no pre drawdown
    st4 = path_stats(et[5:], hi[5:], lo[5:], cl[5:], 1.5, "lo_first")
    assert st4 is not None
    assert st4["retr_pre_hi"] == 0.0 and abs(st4["retr_after_hi"] + 0.4) < 1e-9, st4
    tr = pl.DataFrame({"symbol": ["X", "X", "X"], "et": [575, 575, 575],
                       "price": [2.0, 1.9, 1.5],
                       "ts_utc": [1, 2, 3]}).with_columns(
        pl.col("ts_utc").cast(pl.Datetime("us", "UTC")))
    assert peak_order(tr, 575, 2.0, 1.5) == "hi_first"
    tr2 = tr.with_columns(pl.Series("price", [1.5, 1.9, 2.0]))
    assert peak_order(tr2, 575, 2.0, 1.5) == "lo_first"
    assert peak_order(tr, 575, 2.0, 2.0) is None
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
    counts = defaultdict(int)
    for i, d in enumerate(days, 1):
        res = stage_day(d, args.force)
        counts[res.split("(")[0]] += 1
        if i % 25 == 0 or args.days is None:
            print(f"[{i}/{len(days)}] {d}: {res}  {dict(counts)}", flush=True)
    print(f"staged: {dict(counts)}")


if __name__ == "__main__":
    main()
