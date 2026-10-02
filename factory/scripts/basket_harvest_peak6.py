#!/usr/bin/env python3
"""HARVEST01 — stop variants: flush-bounce release vs market stop on the basket mark.

The market stop (basket mark <= -6% -> sell at next open) realizes about -8.3% and 39%
of stopped baskets later peak +16.7%: it whipsaws recoverers. The flush-bounce finding
says a panic low is followed by a bounce; this probe releases the basket into the first
bounce off its own low instead of selling into the panic, with a hard floor for the days
that never bounce.

Rules:
  tp3_ms6_c720        reference: take +3%, market stop -6%, flat by noon
  tp3_ms6d10_c720     stop armed only 10 min after entry
  tp3_ms6d20_c720     stop armed only 20 min after entry
  tp3_ms8/m10_c720    wider market stop
  tp3_fbB_c720        no stop: release into the first +B% bounce off the basket low,
                      hard floor -12%, else flat by noon
  tp3_fbB_floor_c720  same with floor -8%
  tp3_ms6_fb3_c720    stop -6% BUT if it triggers, hold and release into the +3% bounce
                      (floor -12%), else flat by noon

Usage:
    .venv/bin/python factory/scripts/basket_harvest_peak6.py --workers 3
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

import os
SIDE = float(os.environ.get('HARVEST_SIDE', '0.005'))
CLOCKS = (510, 540, 555, 565, 569, 575, 600, 630, 660, 720)
NS = (1, 2, 3)
RULES: list[tuple[str, dict]] = [
    ("hold", {}),
    ("tp3_ms6_c720", {"tp": 3, "ms": 6, "cap": 720}),
    ("tp3_ms6d10_c720", {"tp": 3, "ms": 6, "arm": 10, "cap": 720}),
    ("tp3_ms6d20_c720", {"tp": 3, "ms": 6, "arm": 20, "cap": 720}),
    ("tp3_ms8_c720", {"tp": 3, "ms": 8, "cap": 720}),
    ("tp3_ms10_c720", {"tp": 3, "ms": 10, "cap": 720}),
    ("tp3_fb2_c720", {"tp": 3, "fb": 2, "floor": 12, "cap": 720}),
    ("tp3_fb3_c720", {"tp": 3, "fb": 3, "floor": 12, "cap": 720}),
    ("tp3_fb5_c720", {"tp": 3, "fb": 5, "floor": 12, "cap": 720}),
    ("tp3_fb3_f8_c720", {"tp": 3, "fb": 3, "floor": 8, "cap": 720}),
    ("tp3_fb3_f20_c720", {"tp": 3, "fb": 3, "floor": 20, "cap": 720}),
    ("tp3_ms6fb3_c720", {"tp": 3, "ms": 6, "fb": 3, "floor": 12, "cap": 720}),
    ("tp3_ms6fb3_c959", {"tp": 3, "ms": 6, "fb": 3, "floor": 12, "cap": 959}),
]


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

            def sell_at(t_dec: int | None):
                t_dec = se if t_dec is None else int(t_dec)
                tot = blocked
                for m in mem:
                    tk = m["ticker"]
                    b = bars[tk]
                    fi = bisect.bisect_left(b["et"], int(m["fill_et"]))
                    if fi >= len(b["et"]):
                        return None
                    j = bisect.bisect_left(b["et"], max(t_dec, b["et"][fi] + 1), fi + 1)
                    if j >= len(b["et"]):
                        return None
                    tot += slot_w[tk] * b["open"][j] * (1 - SIDE)
                return tot - 1

            row = {"day": day, "clock": int(clock), "N": N, "session_end": se, "start_et": start}
            for name, r in RULES:
                if not r:
                    row[name] = sell_at(None)
                    continue
                peak = -1.0
                low = 1e9
                fired = None
                hold_mode = False
                for (t, val) in path:
                    if val > peak:
                        peak = val
                    if val < low:
                        low = val
                    if fired is not None:
                        continue
                    if "tp" in r and val >= 1 + r["tp"] / 100.0:
                        fired = t
                        continue
                    if hold_mode:
                        if val >= low * (1 + r["fb"] / 100.0):
                            fired = t
                        elif val <= 1 - r["floor"] / 100.0:
                            fired = t
                        elif t >= r["cap"]:
                            fired = t
                        continue
                    if "ms" in r and val <= 1 - r["ms"] / 100.0:
                        if "fb" in r:
                            hold_mode = True
                            low = val
                            continue
                        if t - start >= r.get("arm", 0):
                            fired = t
                            continue
                    if "fb" in r and val <= 1 - r.get("floor", 12) / 100.0:
                        fired = t
                        continue
                    if t >= r["cap"]:
                        fired = t
                if fired is None:
                    fired = se
                row[name] = sell_at(fired)
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
    df.write_parquet(rep / "peak6_exits.parquet")
    lines = ["# HARVEST01 — stop variants: flush-bounce release vs market stop\n",
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
    (rep / "peak6_exits.md").write_text("\n".join(lines) + "\n")
    print(f"peak6 rows={df.height} -> {rep/'peak6_exits.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
