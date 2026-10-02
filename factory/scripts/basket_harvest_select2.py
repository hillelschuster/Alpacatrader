#!/usr/bin/env python3
"""HARVEST01 — selection probe at the 569 clock: re-rank the roster by PM state.

The sim's rank order at 569 is the decision_gain order. This probe re-ranks the SAME
causal roster (the day's candidates with a print >= 569) by PM-state features known at
09:29 (PM run-up `pop` = pm_hi/pm_first_px - 1; PM dollar volume dvol = pm_vol*px_569)
and runs the basket rule (tp3_ms6_c720) on the selected members.

Selections (all causal at 569):
  sim_rankN        the sim's own rank order (baseline)
  popN             top-N by pop
  dvolN            top-N by PM dollar volume
  popN_dvol3M      top-N by pop among dvol >= 3M
  dvolN_pop100     top-N by dvol among pop >= 100%

Usage:
    .venv/bin/python factory/scripts/basket_harvest_select2.py --workers 3
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
CLOCK = 569
SELECTIONS = ("sim_rank1", "pop1", "pop2", "dvol1", "pop1_dvol3M", "pop1_dvol1M",
              "dvol1_pop50", "dvol1_pop100", "sim_rank2", "sim_rank3")


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


def run_rule(bars: dict, mem: list[dict], se: int, tp: float, ms: float, cap: int) -> float | None:
    if not mem:
        return None
    N = len(mem)
    start = min(int(m["fill_et"]) for m in mem)
    grid = list(range(start + 1, se + 1))
    ptr = {m["ticker"]: bisect.bisect_left(bars[m["ticker"]]["et"], int(m["fill_et"])) for m in mem}
    last = {m["ticker"]: float(m["fill_px"]) for m in mem}
    slot_w = {m["ticker"]: (1.0 / N) / (float(m["fill_px"]) * (1 + SIDE)) for m in mem}
    fired = None
    for t in grid:
        val = 0.0
        for m in mem:
            tk = m["ticker"]
            b = bars[tk]
            j = ptr[tk]
            while j < len(b["et"]) and b["et"][j] <= t - 1:
                last[tk] = b["close"][j]
                j += 1
            ptr[tk] = j
            val += slot_w[tk] * last[tk]
        if fired is None:
            if val >= 1 + tp / 100.0 or val <= 1 - ms / 100.0 or t >= cap:
                fired = t
    if fired is None:
        fired = se
    tot = 0.0
    for m in mem:
        tk = m["ticker"]
        b = bars[tk]
        fi = bisect.bisect_left(b["et"], int(m["fill_et"]))
        if fi >= len(b["et"]):
            return None
        j = bisect.bisect_left(b["et"], max(fired, b["et"][fi] + 1), fi + 1)
        if j >= len(b["et"]):
            return None
        tot += slot_w[tk] * b["open"][j] * (1 - SIDE)
    return tot - 1


def process_day(day: str, data_root: Path, pm: pl.DataFrame, se: int) -> list[dict]:
    fl_p = data_root / "harvest01" / "sim" / "fills" / f"{day}.parquet"
    if not fl_p.exists():
        return []
    fills = pl.read_parquet(fl_p).filter(
        (pl.col("variant") == "primary") & (pl.col("status") == "filled")
        & (pl.col("clock") == CLOCK))
    if fills.height == 0:
        return []
    bars = load_bars(data_root / "harvest01" / "base" / "bars" / f"{day}.parquet")
    pmd = pm.filter(pl.col("day") == day)
    feats = {}
    for r in pmd.iter_rows(named=True):
        if r["pm_first_px"] and r["pm_first_px"] > 0 and r["pm_hi"]:
            pop = r["pm_hi"] / r["pm_first_px"] - 1
        else:
            pop = None
        dvol = (r["pm_vol"] or 0) * (r["px_569"] or 0)
        feats[r["symbol"]] = (pop, dvol)
    roster = []
    for f in fills.sort("rank").iter_rows(named=True):
        tk = f["ticker"]
        if tk not in bars:
            continue
        pop, dvol = feats.get(tk, (None, 0.0))
        roster.append({"ticker": tk, "rank": int(f["rank"]), "fill_et": int(f["fill_et"]),
                       "fill_px": float(f["fill_px"]), "pop": pop, "dvol": dvol})
    if not roster:
        return []
    rows = []
    for sel in SELECTIONS:
        mem: list[dict] = []
        if sel.startswith("sim_rank"):
            k = int(sel[-1])
            mem = [m for m in roster if m["rank"] == k]
        elif sel.startswith("pop"):
            k = int(sel[3]) if sel[3].isdigit() else 1
            cand = [m for m in roster if m["pop"] is not None]
            if "_dvol" in sel:
                thr = float(sel.split("_dvol")[1].replace("M", "")) * 1e6
                cand = [m for m in cand if m["dvol"] >= thr]
            mem = sorted(cand, key=lambda m: -m["pop"])[:k]
        elif sel.startswith("dvol"):
            k = int(sel[4]) if sel[4].isdigit() else 1
            cand = list(roster)
            if "_pop" in sel:
                thr = float(sel.split("_pop")[1]) / 100.0
                cand = [m for m in cand if m["pop"] is not None and m["pop"] >= thr]
            mem = sorted(cand, key=lambda m: -m["dvol"])[:k]
        if not mem:
            continue
        row = {"day": day, "sel": sel, "n": len(mem),
               "rule": run_rule(bars, mem, se, 3.0, 6.0, 720),
               "hold": run_rule(bars, mem, se, 999.0, 999.0, se)}
        rows.append(row)
    return rows


def _w(a):
    day, data_root, se = a
    pm = (pl.scan_parquet(Path(data_root) / "sip" / "pm_snapshots" / "*.parquet", extra_columns="ignore",
                          missing_columns="insert", include_file_paths="src")
          .select(["src", "symbol", "pm_vol", "pm_hi", "pm_first_px", "px_569"])
          .with_columns(pl.col("src").str.extract(r"(\d{4}-\d{2}-\d{2})").alias("day"))
          .drop("src").filter(pl.col("day") == day).collect())
    return process_day(day, Path(data_root), pm, se)


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
    feats = (pl.scan_parquet(data_root / "sip" / "pm_snapshots" / "*.parquet", extra_columns="ignore",
                             missing_columns="insert", include_file_paths="src")
             .select(["src", "symbol", "pm_vol", "pm_hi", "pm_first_px", "px_569"])
             .with_columns(pl.col("src").str.extract(r"(\d{4}-\d{2}-\d{2})").alias("day"))
             .drop("src").collect())
    for d in days:
        pm = feats.filter(pl.col("day") == d)
        rows += process_day(d, data_root, pm, int(cal[d]["session_end"]))
    df = pl.DataFrame(rows, infer_schema_length=None)
    df.write_parquet(rep / "select2.parquet")
    lines = ["# HARVEST01 — selection at 569 (re-rank the causal roster)\n",
             f"basket-days: {df.height}\n",
             "| sel | rule | mean % | median % | B1 | B2 | pos | n |",
             "|---|---|---|---|---|---|---|---|"]
    for sel in SELECTIONS:
        s = df.filter(pl.col("sel") == sel)
        for c in ("rule", "hold"):
            v = s[c].drop_nulls()
            if v.len() == 0:
                continue
            b1 = s.filter(pl.col("day") <= "2023-12-31")[c].drop_nulls()
            b2 = s.filter(pl.col("day") >= "2025-02-01")[c].drop_nulls()
            m1 = f"{b1.mean()*100:+.2f}" if b1.len() else "n/a"
            m2 = f"{b2.mean()*100:+.2f}" if b2.len() else "n/a"
            lines.append(f"| {sel} | {c} | {v.mean()*100:+.2f} | {v.median()*100:+.2f} | "
                         f"{m1} | {m2} | {(v>0).mean():.2f} | {v.len()} |")
    (rep / "select2.md").write_text("\n".join(lines) + "\n")
    print(f"select2 rows={df.height} -> {rep/'select2.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
