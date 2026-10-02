#!/usr/bin/env python3
"""HARVEST01 — scale-out ladders on the basket mark (tail preservation).

The tp3/tp5 rules truncate the right tail (best days +22..+28%). This probe takes a
PARTIAL profit at the first basket +X% and manages the remainder with a peak trail /
stop / cap, so a fraction rides the continuation days:

  halfX            sell 50% at first mark >= +X%, rest held to the cap
  halfX_trailT     rest exits on giveback T% from the running peak (or cap)
  halfX_trailT_sY  rest also has a market stop at -Y%
  tpX_msY_c720     full exit at +X% (reference), market stop -Y%

Usage:
    .venv/bin/python factory/scripts/basket_harvest_peak5.py --workers 3
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
CLOCKS = (510, 540, 555, 565, 569, 575, 600, 630, 660, 720)
NS = (1, 2, 3)
RULES: list[tuple[str, dict]] = [
    ("hold", {}),
    ("tp3_ms6_c720", {"tp": 3, "ms": 6, "cap": 720}),
    ("half3_c720", {"half": 3, "cap": 720}),
    ("half3_c959", {"half": 3, "cap": 959}),
    ("half3_trail5_c720", {"half": 3, "trail": 5, "cap": 720}),
    ("half3_trail8_c720", {"half": 3, "trail": 8, "cap": 720}),
    ("half3_trail10_c720", {"half": 3, "trail": 10, "cap": 720}),
    ("half3_trail15_c720", {"half": 3, "trail": 15, "cap": 720}),
    ("half3_trail10_c959", {"half": 3, "trail": 10, "cap": 959}),
    ("half3_trail10_ms6_c720", {"half": 3, "trail": 10, "ms": 6, "cap": 720}),
    ("half5_trail10_ms6_c720", {"half": 5, "trail": 10, "ms": 6, "cap": 720}),
    ("half3_trail10_ms6_c959", {"half": 3, "trail": 10, "ms": 6, "cap": 959}),
    ("tp3_ms6_c959", {"tp": 3, "ms": 6, "cap": 959}),
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

            def sell_at(m, t_dec: int | None, frac: float):
                tk = m["ticker"]
                b = bars[tk]
                fi = bisect.bisect_left(b["et"], int(m["fill_et"]))
                if fi >= len(b["et"]):
                    return None
                t_dec = se if t_dec is None else int(t_dec)
                j = bisect.bisect_left(b["et"], max(t_dec, b["et"][fi] + 1), fi + 1)
                if j >= len(b["et"]):
                    return None
                return frac * slot_w[tk] * b["open"][j] * (1 - SIDE)

            row = {"day": day, "clock": int(clock), "N": N, "session_end": se, "start_et": start}
            for name, r in RULES:
                if not r:
                    tot = blocked
                    for m in mem:
                        v = sell_at(m, None, 1.0)
                        if v is None:
                            tot = None
                            break
                        tot += v
                    row[name] = None if tot is None else tot - 1
                    continue
                peak = -1.0
                fired = None      # time of the first event (tp/half/ms/trail/cap)
                frac0 = None      # fraction sold at the first event (None = full)
                for (t, val) in path:
                    if val > peak:
                        peak = val
                    if fired is not None:
                        continue
                    if "half" in r and val >= 1 + r["half"] / 100.0:
                        fired, frac0 = t, 0.5
                    elif "tp" in r and val >= 1 + r["tp"] / 100.0:
                        fired, frac0 = t, 1.0
                    elif "ms" in r and val <= 1 - r["ms"] / 100.0:
                        fired, frac0 = t, 1.0
                    elif "trail" in r and val <= peak * (1 - r["trail"] / 100.0) and peak > 1:
                        fired, frac0 = t, 1.0
                    elif "cap" in r and t >= r["cap"]:
                        fired, frac0 = t, 1.0
                if fired is None:
                    fired, frac0 = se, 1.0
                cap = r.get("cap", se)
                tot = blocked
                for m in mem:
                    v1 = sell_at(m, fired, frac0)
                    if v1 is None:
                        tot = None
                        break
                    tot += v1
                    rest = 1.0 - frac0
                    if rest > 0:
                        # remainder: continue the same rule from the event minute
                        peak2 = peak
                        fired2 = None
                        for (t, val) in path:
                            if t <= fired:
                                continue
                            if val > peak2:
                                peak2 = val
                            if fired2 is None:
                                if "trail" in r and val <= peak2 * (1 - r["trail"] / 100.0):
                                    fired2 = t
                                elif "ms" in r and val <= 1 - r["ms"] / 100.0:
                                    fired2 = t
                                elif t >= cap:
                                    fired2 = t
                        v2 = sell_at(m, fired2 if fired2 is not None else cap, rest)
                        if v2 is None:
                            tot = None
                            break
                        tot += v2
                row[name] = None if tot is None else tot - 1
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
    df.write_parquet(rep / "peak5_exits.parquet")
    lines = ["# HARVEST01 — scale-out ladders on the basket mark\n",
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
    (rep / "peak5_exits.md").write_text("\n".join(lines) + "\n")
    print(f"peak5 rows={df.height} -> {rep/'peak5_exits.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
