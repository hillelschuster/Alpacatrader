#!/usr/bin/env python3
"""HARVEST01 — flush-bounce release rules vs the standing release rules (member-level).

Rules compared on every filled member (one sale maximum), basket-day dollars at fixed
endpoints {660, 720, session_end}, both blocks:

    hold            no release
    gb10            release on close <= 0.90 * running high (tonight's best)
    dmg_wait        release after close < fill*0.90 and still below 5 minutes later
    flush_b3        release ONLY after a -20% flush from the running high, selling into
                    the first +3% bounce off the panic low (cap 30 min)
    flush_b5        same with +5%
    flush_defer15   on the flush, sell at the first open >= flush + 15 min

Execution: decision on completed bar -> sell at the next bar open (same convention as
the management lane). A member with no trigger is held to the endpoint. Fees identical
within a rule (one sale); deltas vs hold are computed with the same fee model as the
management lane.
Outputs: harvest01/report/flushrule.parquet + flushrule.md
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402

SIDE = 0.005
ENDS = (660, 720)
RULES = ("hold", "gb10", "dmg_wait", "flush_b3", "flush_b5", "flush_defer15",
         "gb10_wait5_fix", "dmg_wait_fix")


def load_bars(path: Path) -> dict:
    if not path.exists():
        return {}
    d = pl.read_parquet(path, columns=["ticker", "et", "open", "high", "low", "close"])
    out = {}
    for tk, sub in d.group_by("ticker"):
        t = tk[0] if isinstance(tk, tuple) else tk
        s = sub.sort("et")
        out[t] = {c: s[c].to_list() for c in ("et", "open", "high", "low", "close")}
    return out


def exit_idx(ets, E, fi, se):
    if E == se:
        j = bisect.bisect_left(ets, se, fi + 1)
    else:
        j = bisect.bisect_left(ets, max(E, ets[fi] + 1), fi + 1)
    return j if j < len(ets) else -1


def rule_sell_idx(rule: str, b: dict, fi: int, fill_px: float, ets: list) -> int | None:
    """Return the bar index whose OPEN is the sale execution, or None for hold."""
    n = len(ets)
    h, lo_, c = b["high"], b["low"], b["close"]
    peak = -1.0
    breach_et = None
    flushed = False
    flush_low = None
    flush_et = None
    pending = None  # for *_wait5_fix: (trigger_et) waiting 5 minutes to see a flush
    for i in range(fi, n):
        peak = max(peak, h[i])
        if rule in ("gb10", "gb10_wait5_fix"):
            if rule == "gb10":
                if c[i] <= peak * 0.90:
                    return i + 1 if i + 1 < n else None
            else:
                if pending is None and c[i] <= peak * 0.90:
                    pending = ets[i]
                elif pending is not None:
                    # flush developed during the wait?
                    if not flushed and c[i] <= peak * 0.80:
                        flushed = True
                        flush_et = ets[i]
                        flush_low = min(lo_[i:min(i + 3, n)])
                    if flushed and flush_low is not None and h[i] >= flush_low * 1.03:
                        return i + 1 if i + 1 < n else None
                    if flushed and flush_et is not None and ets[i] >= flush_et + 30:
                        return i + 1 if i + 1 < n else None
                    if not flushed and ets[i] >= pending + 5:
                        return i + 1 if i + 1 < n else None
        elif rule in ("dmg_wait", "dmg_wait_fix"):
            if rule == "dmg_wait":
                if breach_et is None and c[i] < fill_px * 0.90:
                    breach_et = ets[i]
                elif breach_et is not None and ets[i] >= breach_et + 5:
                    if c[i] < fill_px * 0.90:
                        return i + 1 if i + 1 < n else None
                    breach_et = None
            else:
                if breach_et is None and c[i] < fill_px * 0.90:
                    breach_et = ets[i]
                elif breach_et is not None and ets[i] >= breach_et + 5 and pending is None:
                    if c[i] < fill_px * 0.90:
                        pending = ets[i]
                    else:
                        breach_et = None
                elif pending is not None:
                    if not flushed and c[i] <= peak * 0.80:
                        flushed = True
                        flush_et = ets[i]
                        flush_low = min(lo_[i:min(i + 3, n)])
                    if flushed and flush_low is not None and h[i] >= flush_low * 1.03:
                        return i + 1 if i + 1 < n else None
                    if flushed and flush_et is not None and ets[i] >= flush_et + 30:
                        return i + 1 if i + 1 < n else None
                    if not flushed and ets[i] >= pending + 5:
                        return i + 1 if i + 1 < n else None
        elif rule in ("flush_b3", "flush_b5", "flush_defer15"):
            if not flushed:
                if peak > 0 and c[i] <= peak * 0.80:
                    flushed = True
                    flush_et = ets[i]
                    flush_low = min(lo_[i:min(i + 3, n)])
                    if rule == "flush_defer15":
                        j = bisect.bisect_left(ets, ets[i] + 15)
                        return j if j < n else None
            else:
                flush_low = min(flush_low, lo_[i])
                X = 0.03 if rule == "flush_b3" else 0.05
                if h[i] >= flush_low * (1 + X):
                    return i + 1 if i + 1 < n else None
                if ets[i] >= ets[fi] + 30:
                    return i + 1 if i + 1 < n else None
    return None


def process_day(day: str, data_root: Path, se: int) -> list[dict]:
    fl_p = data_root / "harvest01" / "sim" / "fills" / f"{day}.parquet"
    if not fl_p.exists():
        return []
    fills = pl.read_parquet(fl_p).filter(
        (pl.col("variant") == "primary") & (pl.col("status") == "filled"))
    if fills.height == 0:
        return []
    bars = load_bars(data_root / "harvest01" / "base" / "bars" / f"{day}.parquet")
    rows = []
    for f in fills.iter_rows(named=True):
        b = bars.get(f["ticker"])
        if not b:
            continue
        ets = b["et"]
        fi = bisect.bisect_left(ets, int(f["fill_et"]))
        if fi >= len(ets):
            continue
        fill_px = float(f["fill_px"])
        row = {"day": day, "variant": f["variant"], "clock": f["clock"], "rank": f["rank"],
               "ticker": f["ticker"], "session_end": se}
        for rule in RULES:
            si = None if rule == "hold" else rule_sell_idx(rule, b, fi, fill_px, ets)
            if si is None or si >= len(ets):
                w_r = None
            else:
                w_r = (b["open"][si] * (1 - SIDE)) / (fill_px * (1 + SIDE))
            for E in ENDS + (se,):
                j = exit_idx(ets, E, fi, se)
                if j < 0:
                    row[f"d_{rule}_{E}"] = None
                    continue
                w_hold = (b["open"][j] * (1 - SIDE)) / (fill_px * (1 + SIDE))
                if w_r is None or ets[si] > E:
                    row[f"d_{rule}_{E}"] = 0.0
                else:
                    row[f"d_{rule}_{E}"] = w_r - w_hold
        rows.append(row)
    return rows


def _w(a):
    day, data_root, se = a
    return process_day(day, Path(data_root), se)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    data_root = bps.resolve_data_root(args.data_root)
    rep = data_root / "harvest01" / "report"
    cal = json.loads(bps.CALENDAR.read_text())["evidence"]
    days = sorted(cal.keys())
    if args.limit:
        days = days[:args.limit]
    rows = []
    if args.workers > 1:
        import multiprocessing as mp
        tasks = [(d, str(data_root), int(cal[d]["session_end"])) for d in days]
        with mp.get_context("spawn").Pool(processes=args.workers) as pool:
            for res in pool.imap_unordered(_w, tasks):
                rows += res
    else:
        for d in days:
            rows += process_day(d, data_root, int(cal[d]["session_end"]))
    df = pl.DataFrame(rows, infer_schema_length=None)
    df.write_parquet(rep / "flushrule.parquet")
    lines = ["# HARVEST01 — flush-bounce release rules vs standing rules (member deltas, dev)\n",
             f"members: {df.height}\n"]
    lines.append("| rule | end | mean delta vs hold | B1 | B2 | median | pos share | n |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for rule in RULES:
        for E in ENDS:
            col = f"d_{rule}_{E}"
            if col not in df.columns:
                continue
            v = df[col].drop_nulls()
            if v.len() == 0:
                continue
            b1 = df.filter(pl.col("day") <= "2023-12-31")[col].drop_nulls()
            b2 = df.filter(pl.col("day") >= "2025-02-01")[col].drop_nulls()
            lines.append(f"| {rule} | {E} | {v.mean()*100:+.3f} | {b1.mean()*100:+.3f} | "
                         f"{b2.mean()*100:+.3f} | {v.median()*100:+.3f} | {(v>0).mean():.2f} | {v.len()} |")
    (rep / "flushrule.md").write_text("\n".join(lines) + "\n")
    print(f"flushrule rows={df.height} -> {rep/'flushrule.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
