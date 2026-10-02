#!/usr/bin/env python3
"""HARVEST01 — stop-limit vs market-stop basket exits.

peak2/3 used a market stop: basket mark <= -5% -> sell all at the NEXT open, which on
gappy microcaps realizes about -6.5%. A resting stop-limit order is the realistic
alternative: trigger at the basket -5% mark, then each member's limit = its own last
close at the trigger minute, and the fill is the first later bar whose HIGH >= limit
(fill at the limit price, fee one side). Members that never trade back to the limit are
held to the cap. The data decides which is better net of the no-fill risk.

Rules: tp (basket +X% -> exit all at next open), sl (stop-limit Y%), ms (market stop Y%),
cap (minutes since start -> exit at first bar et >= cap), trail (giveback from peak).

Usage:
    .venv/bin/python factory/scripts/basket_harvest_peak4.py --workers 3
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
RULES: list[tuple[str, dict]] = [
    ("hold", {}),
    ("tp3_ms6_c720", {"tp": 3, "ms": 6, "cap": 720}),
    ("tp3_sl6_c720", {"tp": 3, "sl": 6, "cap": 720}),
    ("tp3_sl5_c720", {"tp": 3, "sl": 5, "cap": 720}),
    ("tp3_sl4_c720", {"tp": 3, "sl": 4, "cap": 720}),
    ("tp3_sl3_c720", {"tp": 3, "sl": 3, "cap": 720}),
    ("tp5_sl6_c720", {"tp": 5, "sl": 6, "cap": 720}),
    ("tp5_sl5_c720", {"tp": 5, "sl": 5, "cap": 720}),
    ("tp5_sl4_c720", {"tp": 5, "sl": 4, "cap": 720}),
    ("sl6_c720", {"sl": 6, "cap": 720}),
    ("sl5_c720", {"sl": 5, "cap": 720}),
    ("tp3_sl5_c720_trail8", {"tp": 3, "sl": 5, "cap": 720, "trail": 8}),
    ("tp3_sl5_c690", {"tp": 3, "sl": 5, "cap": 690}),
    ("tp3_sl5_c750", {"tp": 3, "sl": 5, "cap": 750}),
    ("tp3_sl5_c959", {"tp": 3, "sl": 5, "cap": 959}),
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

            def fill_at_open(m, t_dec: int | None, cap_fill=False):
                """sell member m at the first bar open with et >= t_dec (fee one side)."""
                tk = m["ticker"]
                b = bars[tk]
                fi = bisect.bisect_left(b["et"], int(m["fill_et"]))
                if fi >= len(b["et"]):
                    return None
                target = max(int(t_dec), b["et"][fi] + 1)
                j = bisect.bisect_left(b["et"], target, fi + 1)
                if j >= len(b["et"]):
                    return None
                return slot_w[tk] * b["open"][j] * (1 - SIDE)

            def fill_at_limit(m, t_trig: int, limit_px: float):
                """limit sell: first later bar whose high >= limit -> fill at limit."""
                tk = m["ticker"]
                b = bars[tk]
                fi = bisect.bisect_left(b["et"], int(m["fill_et"]))
                j = bisect.bisect_left(b["et"], max(int(t_trig) + 1, b["et"][fi] + 1), fi + 1)
                while j < len(b["et"]):
                    if b["high"][j] >= limit_px:
                        return slot_w[tk] * limit_px * (1 - SIDE)
                    j += 1
                return None

            def settle(exit_plan: dict):
                """exit_plan[ticker] = ('open', t) | ('limit', t_trig, px) | ('none',)"""
                tot = blocked
                for m in mem:
                    tk = m["ticker"]
                    plan = exit_plan.get(tk)
                    if plan is None or plan[0] == "none":
                        return None
                    v = (fill_at_limit(m, plan[1], plan[2]) if plan[0] == "limit"
                         else fill_at_open(m, plan[1]))
                    if v is None:
                        return None
                    tot += v
                return tot - 1

            row = {"day": day, "clock": int(clock), "N": N, "session_end": se, "start_et": start}
            for name, r in RULES:
                if not r:
                    row[name] = settle({m["ticker"]: ("open", se) for m in mem})
                    continue
                peak = -1.0
                action = None  # ('tp', t) | ('sl', t, {tk: limit_px}) | ('ms', t) | ('cap', t) | ('trail', t)
                for (t, val) in path:
                    if val > peak:
                        peak = val
                    if action is not None:
                        continue
                    if "tp" in r and val >= 1 + r["tp"] / 100.0:
                        action = ("tp", t)
                    elif "sl" in r and val <= 1 - r["sl"] / 100.0:
                        action = ("sl", t, {m["ticker"]: last[m["ticker"]] for m in mem})
                    elif "ms" in r and val <= 1 - r["ms"] / 100.0:
                        action = ("ms", t)
                    elif "trail" in r and val <= peak * (1 - r["trail"] / 100.0) and peak > 1:
                        action = ("trail", t)
                    elif "cap" in r and t >= r["cap"]:
                        action = ("cap", t)
                if action is None:
                    action = ("cap", se)
                kind = action[0]
                if kind in ("tp", "ms", "trail", "cap"):
                    row[name] = settle({m["ticker"]: ("open", action[1]) for m in mem})
                else:  # stop-limit
                    trig = action[1]
                    limits = action[2]
                    plan = {m["ticker"]: ("limit", trig, limits[m["ticker"]]) for m in mem}
                    v = settle(plan)
                    if v is None:  # nobody traded back to the limit: hold to the cap
                        cap = r.get("cap", se)
                        v = settle({m["ticker"]: ("open", cap) for m in mem})
                    row[name] = v
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
    df.write_parquet(rep / "peak4_exits.parquet")
    lines = ["# HARVEST01 — stop-limit vs market stop (basket-level)\n",
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
    (rep / "peak4_exits.md").write_text("\n".join(lines) + "\n")
    print(f"peak4 rows={df.height} -> {rep/'peak4_exits.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
