#!/usr/bin/env python3
"""HARVEST01 — basket-level peak exits (sell the WHOLE basket on its own mark path).

The window lane showed the basket's own mark peaks early (median 09:43-09:50 for PM
entries). This probe tests basket-level allocation rules nobody has run: watch the
basket mark (equal slots, last close), and act on the BASKET's own state:

    hold            no action
    bgb_G           sell everything when the basket mark <= (1-G) * running peak mark
    tp_X            sell everything when the basket mark >= +X% (profit take)
    tp_X_bgb_G      take X% if it comes first, else peak-giveback G

Executions: decision on the completed minute -> sell every open slot at the next
minute's open (one-sided fee), i.e. exactly the member-lane convention. Outputs mean
net-100 basket-day returns by (clock, N, rule) with block splits.

Usage:
    .venv/bin/python factory/scripts/basket_harvest_peak.py --workers 3
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
CLOCKS = (510, 540, 550, 555, 560, 565, 569, 571, 575, 585, 600, 660)
NS = (2, 3)
RULES = ("hold", "bgb8", "bgb10", "bgb12", "tp5", "tp10", "tp5_bgb10")


def load_bars(path: Path) -> dict:
    if not path.exists():
        return {}
    d = pl.read_parquet(path, columns=["ticker", "et", "open", "high", "low", "close"]).drop_nulls(["open", "high", "low", "close"])
    out = {}
    for tk, sub in d.group_by("ticker"):
        t = tk[0] if isinstance(tk, tuple) else tk
        s = sub.sort("et")
        out[t] = {c: s[c].to_list() for c in ("et", "open", "high", "low", "close")}
    return out


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
    for (clock,), grp in fills.group_by(["clock"]):
        members = grp.sort("rank").to_dicts()
        for N in NS:
            mem = members[:N]
            mem = [m for m in mem if m["ticker"] in bars]
            if not mem:
                continue
            # minute mark path from the earliest fill
            start = min(int(m["fill_et"]) for m in mem)
            grid = list(range(start + 1, se + 1))
            ptr = {m["ticker"]: bisect.bisect_left(bars[m["ticker"]]["et"], int(m["fill_et"])) for m in mem}
            last = {m["ticker"]: float(m["fill_px"]) for m in mem}
            slot_w = {m["ticker"]: (1.0 / N) / (float(m["fill_px"]) * (1 + SIDE)) for m in mem}
            blocked = (N - len(mem)) * (1.0 / N)
            path = []
            for t in grid:
                val = blocked
                for m in mem:
                    tk = m["ticker"]
                    b = bars[tk]
                    j = ptr[tk]
                    while j < len(b["et"]) and b["et"][j] <= t - 1:
                        last[tk] = b["close"][j]
                        j += 1
                    ptr[tk] = j
                    val += slot_w[tk] * last[tk]
                path.append((t, val))
            if not path:
                continue
            row = {"day": day, "clock": int(clock), "N": N, "session_end": se}
            # exit execution helper: first bar open with et >= t_exit (per member), fee one side
            def sell_value(t_dec: int | None):
                if t_dec is None:
                    t_dec = se  # hold to end
                tot = blocked
                for m in mem:
                    tk = m["ticker"]
                    b = bars[tk]
                    fi = bisect.bisect_left(b["et"], int(m["fill_et"]))
                    target = max(t_dec, b["et"][fi] + 1) if fi < len(b["et"]) else t_dec
                    j = bisect.bisect_left(b["et"], target, fi + 1)
                    if j >= len(b["et"]):
                        return None
                    tot += slot_w[tk] * b["open"][j] * (1 - SIDE)
                return tot - 1

            peak = -1.0
            fire_g = {G: None for G in (0.08, 0.10, 0.12)}
            fire_tp = {X: None for X in (0.05, 0.10)}
            for (t, val) in path:
                if val > peak:
                    peak = val
                for G in fire_g:
                    if fire_g[G] is None and val <= peak * (1 - G):
                        fire_g[G] = t
                for X in fire_tp:
                    if fire_tp[X] is None and val >= 1 + X:
                        fire_tp[X] = t
            row["peak_ret"] = peak - 1
            row["hold"] = sell_value(None)
            for G, name in ((0.08, "bgb8"), (0.10, "bgb10"), (0.12, "bgb12")):
                row[name] = sell_value(fire_g[G])
            for X, name in ((0.05, "tp5"), (0.10, "tp10")):
                row[name] = sell_value(fire_tp[X])
            both = None
            if fire_tp[0.05] is not None and fire_g[0.10] is not None:
                both = min(fire_tp[0.05], fire_g[0.10])
            elif fire_tp[0.05] is not None:
                both = fire_tp[0.05]
            elif fire_g[0.10] is not None:
                both = fire_g[0.10]
            row["tp5_bgb10"] = sell_value(both)
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
    df.write_parquet(rep / "peak_exits.parquet")
    lines = ["# HARVEST01 — basket-level peak exits (sell the whole basket on its own mark)\n",
             f"basket-days: {df.height}\n"]
    lines.append("| clock | N | rule | mean % | median % | B1 | B2 | pos | n |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for clock in CLOCKS:
        for N in NS:
            s = df.filter((pl.col("clock") == clock) & (pl.col("N") == N))
            if s.height == 0:
                continue
            for rule in RULES:
                v = s[rule].drop_nulls()
                if v.len() == 0:
                    continue
                b1 = s.filter(pl.col("day") <= "2023-12-31")[rule].drop_nulls()
                b2 = s.filter(pl.col("day") >= "2025-02-01")[rule].drop_nulls()
                m1 = f"{b1.mean()*100:+.2f}" if b1.len() else "n/a"
                m2 = f"{b2.mean()*100:+.2f}" if b2.len() else "n/a"
                lines.append(f"| {clock} | {N} | {rule} | {v.mean()*100:+.2f} | {v.median()*100:+.2f} | "
                             f"{m1} | {m2} | {(v>0).mean():.2f} | {v.len()} |")
    (rep / "peak_exits.md").write_text("\n".join(lines) + "\n")
    print(f"peak_exits rows={df.height} -> {rep/'peak_exits.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
