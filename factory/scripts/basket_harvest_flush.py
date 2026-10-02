#!/usr/bin/env python3
"""HARVEST01 — flush-timing test: what an existing holder should do at the −20% flush.

For every deep-flush episode (first touch of −20% from the running high; from the
transition-anatomy lane) compute PAIRED executable exit values from the flush minute:

    v_flush      sell at the open of the first bar >= ev_t          (tonight's rules)
    v_h5/15/30   sell at the open of the first bar >= ev_t + 5/15/30
    v_bounce_X   sell at the open of the first bar after the first completed bar whose
                 high >= flush_low * (1+X); capped: if no touch by ev_t+30, sell at
                 ev_t+30. X in {1,2,3,5}%.

All variants sell exactly once; fees are identical across variants, so paired
differences are friction-free hold-timing value. Outputs per event + paired summary
by block. Usage:
    .venv/bin/python factory/scripts/basket_harvest_flush.py --workers 3
"""
from __future__ import annotations

import argparse
import bisect
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402


def process_day(day: str, data_root: Path) -> list[dict]:
    evp = data_root / "harvest01" / "report" / "transitions_deep20.parquet"
    bars_p = data_root / "harvest01" / "base" / "bars" / f"{day}.parquet"
    if not bars_p.exists():
        return []
    d = pl.read_parquet(evp).filter((pl.col("day") == day) & (pl.col("event") == "deep20"))
    if d.height == 0:
        return []
    bars = {}
    bd = pl.read_parquet(bars_p, columns=["ticker", "et", "open", "high", "low"])
    for tk, sub in bd.group_by("ticker"):
        t = tk[0] if isinstance(tk, tuple) else tk
        s = sub.sort("et")
        bars[t] = {c: s[c].to_list() for c in ("et", "open", "high", "low")}
    rows = []
    for e in d.iter_rows(named=True):
        b = bars.get(e["ticker"])
        if not b:
            continue
        ets = b["et"]
        i = bisect.bisect_left(ets, int(e["ev_t"]))
        if i >= len(ets):
            continue
        row = {"day": day, "ticker": e["ticker"], "clock": e["clock"], "rank": e["rank"],
               "ev_t": int(e["ev_t"]), "ret_fill_at_ev": e["ret_fill_at_ev"]}
        p0 = b["open"][i]
        row["v_flush"] = 0.0  # baseline: selling at the flush open; all variants are returns vs it
        for h in (5, 15, 30):
            j = bisect.bisect_left(ets, int(e["ev_t"]) + h)
            row[f"v_h{h}"] = b["open"][j] / p0 - 1 if j < len(ets) else None
        # flush low over the flush bar and the following 2 bars (the panic low)
        lo = min(b["low"][i:min(i + 3, len(ets))])
        j30 = bisect.bisect_left(ets, int(e["ev_t"]) + 30)
        cap = j30 if j30 < len(ets) else len(ets) - 1
        for X in (0.01, 0.02, 0.03, 0.05):
            tgt = lo * (1 + X)
            k = next((j for j in range(i, cap) if b["high"][j] >= tgt), None)
            if k is None:
                row[f"v_bounce{int(X*100)}"] = (b["open"][cap] / p0 - 1) if cap > i else None
            else:
                sell = k + 1 if k + 1 < len(ets) else k
                row[f"v_bounce{int(X*100)}"] = b["open"][sell] / p0 - 1
        rows.append(row)
    return rows


def _w(a):
    day, data_root = a
    return process_day(day, Path(data_root))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    data_root = bps.resolve_data_root(args.data_root)
    rep = data_root / "harvest01" / "report"
    ev = pl.read_parquet(rep / "transitions_deep20.parquet")
    days = sorted(ev.filter(pl.col("event") == "deep20")["day"].unique().to_list())
    rows = []
    if args.workers > 1:
        import multiprocessing as mp
        with mp.get_context("spawn").Pool(processes=args.workers) as pool:
            for res in pool.imap_unordered(_w, [(d, str(data_root)) for d in days]):
                rows += res
    else:
        for d in days:
            rows += process_day(d, data_root)
    df = pl.DataFrame(rows, infer_schema_length=None)
    df.write_parquet(rep / "flush_events.parquet")
    lines = ["# HARVEST01 — flush-timing test (paired executable exits from the −20% flush)\n",
             f"episodes with bars: {df.height}\n"]
    lines.append("| variant | mean vs v_flush (pp) | B1 | B2 | median | n |")
    lines.append("|---|---|---|---|---|---|")
    b1 = df.filter(pl.col("day") <= "2023-12-31")
    b2 = df.filter(pl.col("day") >= "2025-02-01")
    for col in ("v_h5", "v_h15", "v_h30", "v_bounce1", "v_bounce2", "v_bounce3", "v_bounce5"):
        d_ = (df[col] - df["v_flush"]).drop_nulls()
        d1 = (b1[col] - b1["v_flush"]).drop_nulls()
        d2 = (b2[col] - b2["v_flush"]).drop_nulls()
        lines.append(f"| {col} | {d_.mean()*100:+.3f} | {d1.mean()*100:+.3f} | {d2.mean()*100:+.3f} | "
                     f"{d_.median()*100:+.3f} | {d_.len()} |")
    lines.append("\nPaired: every variant sells once; fees identical across variants, so these are "
                 "friction-free hold-timing differences vs selling at the flush open.\n")
    (rep / "flush.md").write_text("\n".join(lines) + "\n")
    print(f"flush_events rows={df.height} -> {rep/'flush.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
