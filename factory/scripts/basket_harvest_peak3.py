#!/usr/bin/env python3
"""HARVEST01 — basket-level rule DSL probe (profit-take / stop / trail / time-cap).

Extends basket_harvest_peak.py: a basket-day is a path of marks (equal slots, last
close). Rules act on the BASKET's own state, first-match wins, executed at the next
minute's open (one-sided fee, conservative). Purpose: cut the never-pop left tail
while keeping the early +5% pops, and get cash by a chosen time.

Rule DSL: tp (profit take %), stop (loss cut %), trail (giveback from peak %),
minpeak (trail only arms after peak >= minpeak %), cap (minutes-since-start cut).

Usage:
    .venv/bin/python factory/scripts/basket_harvest_peak2.py --workers 3
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
CLOCKS = (510, 540, 555, 565, 569, 575, 600, 630, 660, 690, 720)
NS = (1, 2, 3)
RULES: list[tuple[str, dict]] = [("hold", {})]
for _tp in (3, 4, 5, 6, 8):
    for _s in (2, 3, 4, 5, 6, 8, None):
        _n = f"tp{_tp}_s{_s if _s is not None else 'off'}_t10_c720"
        RULES.append((_n, {"tp": _tp, "stop": _s, "trail": 10, "cap": 720} if _s is not None
                           else {"tp": _tp, "trail": 10, "cap": 720}))
for _cap in (690, 750, 780):
    RULES.append((f"tp5_s4_t10_c{_cap}", {"tp": 5, "stop": 4, "trail": 10, "cap": _cap}))
for _tr in (None, 6, 8, 12, 15):
    _n = f"tp5_s4_t{_tr if _tr is not None else 'off'}_c720"
    RULES.append((_n, {"tp": 5, "stop": 4, "trail": _tr, "cap": 720} if _tr is not None
                       else {"tp": 5, "stop": 4, "cap": 720}))


def load_bars(path: Path) -> dict:
    if not path.exists():
        return {}
    d = pl.read_parquet(path, columns=["ticker", "et", "open", "high", "low", "close"]).drop_nulls(
        ["open", "high", "low", "close"])
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
            mem = [m for m in members[:N] if m["ticker"] in bars]
            if not mem:
                continue
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

            def sell_value(t_dec: int | None):
                if t_dec is None:
                    t_dec = se
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

            row = {"day": day, "clock": int(clock), "N": N, "session_end": se, "start_et": start}
            peak = -1.0
            peak_ret = -1.0
            exits: dict[str, int | None] = {name: None for name, _ in RULES}
            for (t, val) in path:
                if val > peak:
                    peak = val
                peak_ret = max(peak_ret, val - 1)
                for name, r in RULES:
                    if exits[name] is not None or not r:
                        continue
                    hit = False
                    if "tp" in r and val >= 1 + r["tp"] / 100.0:
                        hit = True
                    elif "stop" in r and val <= 1 - r["stop"] / 100.0:
                        hit = True
                    elif ("trail" in r and peak - 1 >= r.get("minpeak", 0) / 100.0
                          and val <= peak * (1 - r["trail"] / 100.0)):
                        hit = True
                    elif "cap" in r and t >= r["cap"]:
                        hit = True
                    if hit:
                        exits[name] = t
            row["peak_ret"] = peak_ret
            for name, _ in RULES:
                row[name] = sell_value(exits[name])
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
    df.write_parquet(rep / "peak3_exits.parquet")
    lines = ["# HARVEST01 — basket rule DSL (tp / stop / trail / cap)\n",
             f"basket-days: {df.height}\n",
             "| clock | N | rule | mean % | median % | B1 | B2 | pos | n |",
             "|---|---|---|---|---|---|---|---|---|"]
    for clock in CLOCKS:
        for N in NS:
            s = df.filter((pl.col("clock") == clock) & (pl.col("N") == N))
            if s.height == 0:
                continue
            for name, _ in RULES:
                v = s[name].drop_nulls()
                if v.len() == 0:
                    continue
                b1 = s.filter(pl.col("day") <= "2023-12-31")[name].drop_nulls()
                b2 = s.filter(pl.col("day") >= "2025-02-01")[name].drop_nulls()
                m1 = f"{b1.mean()*100:+.2f}" if b1.len() else "n/a"
                m2 = f"{b2.mean()*100:+.2f}" if b2.len() else "n/a"
                lines.append(f"| {clock} | {N} | {name} | {v.mean()*100:+.2f} | {v.median()*100:+.2f} | "
                             f"{m1} | {m2} | {(v>0).mean():.2f} | {v.len()} |")
    (rep / "peak3_exits.md").write_text("\n".join(lines) + "\n")
    print(f"peak3 rows={df.height} -> {rep/'peak2_exits.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
