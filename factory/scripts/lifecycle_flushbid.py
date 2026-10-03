#!/usr/bin/env python3
"""LIFECYCLE-01 Stage E — earning the spread instead of paying it: resting bid into a flush.

REANCHOR: the binding constraint measured so far is the ENTRY COST (a 100 bps round trip
consumes every personality edge found). This probe changes the entry mechanism, not the
selection: when a roster member flushes (a deep drawdown from its running high), place a
RESTING BID below the market instead of buying the pop. A resting bid can earn the spread
rather than pay it, and it only fills when the price actually comes to us.

Mechanics, all causal and executable on the observed tape:
  * flush detection at minute t: dd_from_high <= -X% (state uses completed bars only)
  * the bid is placed at t+1 at price bid_px = px(t) * (1 - discount)
  * fill test: the FIRST bar with et > t whose LOW <= bid_px fills at bid_px (we are the
    passive side; the bar's low proves the price traded through our bid)
  * if unfilled by the window end, no position and no cost (cash)
  * after a fill: the position is marked and exited by the same executable opens; the
    forward value is measured to +30/+60/+120 minutes and to the window end
  * friction: the FILL pays no spread (we were passive) but the EXIT pays one side (0.5%),
    plus the same fee structure; a filled-and-exited cycle therefore costs ~50 bps, not 100.

Discovery half only; no validation read.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import polars as pl

import lifecycle_study as ls

FRICTION_SIDE = 0.005


def probe(df: pl.DataFrame, clock: int, end: int, discounts: list[float],
          dd_thresholds: list[float], horizons=(30, 60, 120)) -> pl.DataFrame:
    d = df.filter((pl.col("rank") <= 5) & (pl.col("t") <= end))
    events = []
    for key, s in d.group_by(["day", "ticker"]):
        s = s.sort("t")
        t = s["t"].to_numpy()
        px = s["px"].to_numpy().astype(float)
        dd = s["dd_from_high"].to_numpy().astype(float)
        high = s["peak_gain"].to_numpy().astype(float)
        low = s["mae_sofar"].to_numpy().astype(float)
        se = int(s["session_end"][0])
        ok = np.isfinite(px) & np.isfinite(dd)
        if ok.sum() < 10:
            continue
        t, px, dd = t[ok], px[ok], dd[ok]
        # first minute where the completed path is >= X% below its running high
        for thresh in dd_thresholds:
            hit = np.where(dd <= -thresh)[0]
            if hit.size == 0:
                continue
            i = int(hit[0])
            if i + 1 >= len(t) or t[i] + 5 > end:
                continue
            base = px[i]
            for disc in discounts:
                bid = base * (1 - disc)
                events.append({"day": key[0], "ticker": key[1], "clock": int(s["clock"][0]),
                               "rank": int(s["rank"][0]), "t_flush": int(t[i]),
                               "base_px": float(base), "bid": float(bid), "discount": disc,
                               "dd_thresh": thresh, "session_end": se})
    if not events:
        return pl.DataFrame()
    ev = pl.DataFrame(events, infer_schema_length=None)
    # fill + forward value from the executable path
    rows = []
    for r in ev.iter_rows(named=True):
        s = d.filter((pl.col("day") == r["day"]) & (pl.col("ticker") == r["ticker"])).sort("t")
        t = s["t"].to_numpy()
        lows = s["sell_px"].to_numpy().astype(float)  # executable open of each minute
        after = t > r["t_flush"]
        if not after.any():
            continue
        idx = np.where(after)[0]
        fill_i = None
        for j in idx:
            lo = lows[j]
            if np.isfinite(lo) and lo <= r["bid"]:
                fill_i = j
                break
        out = dict(r)
        out["filled"] = fill_i is not None
        out["fill_et"] = int(t[fill_i]) if fill_i is not None else None
        if fill_i is not None:
            for h in horizons:
                k = np.searchsorted(t, t[fill_i] + h, side="left")
                if k < len(t) and np.isfinite(lows[k]) and lows[k] > 0:
                    out[f"gross_{h}"] = float(lows[k] / r["bid"] - 1)
                    out[f"net_{h}"] = float(lows[k] * (1 - FRICTION_SIDE) / r["bid"] - 1)
                else:
                    out[f"gross_{h}"] = out[f"net_{h}"] = None
            k = np.searchsorted(t, end, side="left")
            if k < len(t) and np.isfinite(lows[k]) and lows[k] > 0:
                out["net_window"] = float(lows[k] * (1 - FRICTION_SIDE) / r["bid"] - 1)
            else:
                out["net_window"] = None
        rows.append(out)
    return pl.DataFrame(rows, infer_schema_length=None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--clock", type=int, default=540)
    ap.add_argument("--end", type=int, default=780)
    a = ap.parse_args()
    root = a.data_root / "harvest01/lifecycle/v2"
    out = root / "report"
    days = ls.discovery_days(a.data_root)
    df = ls.load_panel(days, a.data_root, clocks=(a.clock,))
    discounts = [0.03, 0.05, 0.08, 0.12]
    thresholds = [0.10, 0.20, 0.30]
    res = probe(df, a.clock, a.end, discounts, thresholds)
    if res.height == 0:
        print("no flush events")
        return 0
    res.write_parquet(out / f"flushbid_{a.clock}.parquet")
    lines = [f"# LIFECYCLE-01 Stage E — resting bid into a flush (clock {a.clock}, window {a.end})", "",
             "REANCHOR: entry cost is the binding constraint; a passive bid earns the spread.",
             "Fill = first observed executable open at or below the resting bid; exit pays one side.",
             "Unfilled bids cost nothing. Discovery half only.", "",
             "| flush depth | discount | events | fill rate | median fill delay | net +60m | net +120m | net window |",
             "|---|---|---|---|---|---|---|---|"]
    for th in thresholds:
        for disc in discounts:
            s = res.filter((pl.col("dd_thresh") == th) & (pl.col("discount") == disc))
            if s.height < 20:
                continue
            f = s.filter(pl.col("filled"))
            fill_rate = f.height / s.height
            delay = (f["fill_et"] - f["t_flush"]).median() if f.height else None
            def m(c):
                v = f[c].drop_nulls() if f.height else pl.Series([])
                return f"{100*v.mean():+.2f}%" if v.len() else "n/a"
            lines.append(f"| -{th*100:.0f}% | -{disc*100:.0f}% | {s.height} | {fill_rate:.0%} | "
                         f"{delay if delay is not None else 'n/a'} | {m('net_60')} | {m('net_120')} | {m('net_window')} |")
    (out / f"flushbid_{a.clock}.md").write_text("\n".join(lines) + "\n")
    print(f"flush-bid probe clock={a.clock} events={res.height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
