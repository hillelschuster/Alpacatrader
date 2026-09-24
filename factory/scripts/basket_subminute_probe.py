#!/usr/bin/env python3
"""ATLAS sub-minute reconstructibility probe (measurement only, no strategy).

Question this answers, with evidence: **can a second-level (sub-minute) price path for an
ATLAS member-day be reconstructed from data already on disk, with no purchase?**

Answer construction (three deterministic day cases + one reconstructed series):
  * day selection (deterministic, over `sim.dev_days()`):
      - runner   = argmax over days of max A_pm top-3 member MFE (ties -> earliest date)
      - halt     = argmax over days of halt-shaped print holes (>= 5-minute print-to-print
                   jumps) of the A_pm top-3 inside their entry->last-bar windows, among days
                   where every member prints in >= HALT_ACTIVITY_FLOOR minutes (so the pick is
                   a live day with holes, not a dead name); ties -> larger single hole, then
                   earliest date. Holes are read from the committed derived bars.
      - ordinary = the day with zero print gaps at all (anatomy gap_post = 0 and no >= 2-minute
                   hole in the bars) whose max member MFE is closest to the median of that
                   statistic over all such days (ties -> earliest date)
  * for each day, the raw SIP prints of that day's A_pm top-3 filled members are loaded and
    filtered to the prints that may update high/low under the canonical Alpaca bar rules
    (`basket_t5_rawpaths.price_updating`, i.e. `sip_bars.combine` strictest-rule-wins);
  * verification per member: timestamp monotonicity (per-symbol file order), same-microsecond
    tie structure (can the print sequence be recovered?), per-minute coverage of the
    entry->last-bar window, gap runs with the prints bracketing them and whether the tape
    LABELS the hole (condition 5 = market-center reopening), the path delta if all prints
    (incl. odd lots) counted, a cross-check against the anatomy's day_high/close, and a
    minute-by-minute high/low reconciliation against the committed derived bars
    (`factory/artifacts/basket/sip/bars/<day>.parquet`) that the panel substrate is built from.
  * the cost model uses an evenly spaced census of dev days (`--cost-days`, default 24) so the
    per-member-day print distribution is not biased by the three extreme probe days.

Raw source is the canonical net SIP tape: `data/sip/net/trades/<day>.parquet`
(schema: symbol, ts_utc[us,UTC], price, size, exchange, conditions[List(str)], trade_id,
tape[IEX? -> 'A'/'B', CTA -> 'C']). It is read-only; nothing is downloaded.

Resumable: the artifact is rewritten atomically after every completed day, and a day whose
trades file (size, mtime_ns) is unchanged is reused from the existing artifact unless --force.

Outputs:
  factory/artifacts/basket/phase2/ATLAS/subminute_probe.json

Usage:
  .venv/bin/python factory/scripts/basket_subminute_probe.py --select-only
  .venv/bin/python factory/scripts/basket_subminute_probe.py
  .venv/bin/python factory/scripts/basket_subminute_probe.py --days 2021-02-01 --force
  .venv/bin/python factory/scripts/basket_subminute_probe.py --self-test
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import statistics
import sys
import time
from pathlib import Path

import polars as pl

try:
    from factory.scripts import basket_sim as sim
except ImportError:  # direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from factory.scripts import basket_sim as sim

try:
    from factory.scripts import basket_t5_rawpaths as t5
except ImportError:  # direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from factory.scripts import basket_t5_rawpaths as t5

try:
    from factory.scripts import sip_bars as sb
except ImportError:  # direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from factory.scripts import sip_bars as sb

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "factory" / "artifacts" / "basket" / "phase2" / "ATLAS" / "subminute_probe.json"
POP, T_ENTRY = "A_pm", 570
TOP = 3
GAP_MIN = 2            # print-to-print jump (minutes) that counts as a discovered gap
HALT_MIN = 5           # print-to-print jump (minutes) that counts as a halt-sized gap
SAMPLE_PRINT_CAP = 180
BAR_EPS = 1e-9

# trades root candidates, in order: explicit env, worktree/main-checkout layout, canonical store
TRADES_CANDIDATES = ("BASKET_SIP_TRADES", "BASKET_SIP_NET")


def _first_existing(paths):
    for p in paths:
        if p is not None and Path(p).is_dir():
            return Path(p)
    return None


def trades_root() -> Path:
    env = [os.environ.get(k) for k in TRADES_CANDIDATES]
    cands = [Path(e) / "trades" if e else None for e in env]
    cands += [ROOT / "data" / "sip" / "net" / "trades",
              Path("/home/hillel/projects/Alpacatrader/data/sip/net/trades"),
              Path("/home/hillel/sip/net/trades")]
    got = _first_existing(cands)
    if got is None:
        raise FileNotFoundError(
            "no raw SIP trades root; tried: " + ", ".join(str(c) for c in cands if c))
    return got


TRADES = trades_root()
BARS = sim.BARS_DIR


# --------------------------------------------------------------------------- #
# day selection (deterministic)
# --------------------------------------------------------------------------- #

def member_rows(rec: dict, pop: str = POP, T: int = T_ENTRY, top: int = TOP) -> list[dict]:
    """A_pm top-`top` filled, non-blocked members in canonical rank order."""
    snap = sim.snapshot_of(rec, pop, T)
    if snap is None:
        return []
    out = []
    for n in snap["names"][:top]:
        fl = n.get("fill")
        if not fl or fl.get("blocked"):
            continue
        out.append({"ticker": n["ticker"], "rank": int(n.get("rank", len(out) + 1)),
                    "fill_et": int(fl["et"]), "fill_px": float(fl["px"]),
                    "mfe": n.get("mfe"), "gap_post": int(n.get("gap_post") or 0),
                    "gap_pre": int(n.get("gap_pre") or 0), "last_et": n.get("last_et"),
                    "day_high": n.get("day_high"), "close": n.get("close")})
    return out


def member_gap_stats(day: str, ticker: str, fill_et: int, last_et: int) -> dict:
    """Halt-shaped print holes of one member inside [fill_et, last_et], from the committed bars.

    The stored bars are one row per minute that had a price-updating print, so a run of
    missing minutes is a run of minutes with no print. `n_halt` counts runs of >= HALT_MIN-1
    missing minutes (a print-to-print jump of >= HALT_MIN minutes); `n_gap` counts jumps of
    >= GAP_MIN minutes.
    """
    path = BARS / f"{day}.parquet"
    if not path.exists():
        return {"n_bars": 0, "n_gap": 0, "n_halt": 0, "max_run": 0}
    b = (pl.read_parquet(path, columns=["ticker", "et"])
         .filter((pl.col("ticker") == ticker) & (pl.col("et") >= fill_et)
                 & (pl.col("et") <= last_et)))
    ets = b["et"].to_list()
    runs = empty_runs(ets, fill_et, last_et)
    return {"n_bars": len(ets),
            "n_gap": sum(1 for r in runs if len(r) + 1 >= GAP_MIN),
            "n_halt": sum(1 for r in runs if len(r) + 1 >= HALT_MIN),
            "max_run": max((len(r) for r in runs), default=0)}


def day_index(days: list[str], halt_metrics: bool = True) -> dict:
    idx = {}
    for day in days:
        mem = member_rows(sim.load_anatomy(day))
        if not mem:
            continue
        row = {"members": [m["ticker"] for m in mem],
               "max_mfe": max((m["mfe"] if m["mfe"] is not None else -9.0) for m in mem),
               "sum_gaps": sum(m["gap_post"] for m in mem)}
        if halt_metrics:
            gs = [member_gap_stats(day, m["ticker"], m["fill_et"],
                                   int(m["last_et"] or 959)) for m in mem]
            row.update({"n_halt_gaps": sum(g["n_halt"] for g in gs),
                        "n_gaps": sum(g["n_gap"] for g in gs),
                        "max_run_min": max(g["max_run"] for g in gs),
                        "min_window_bars": min(g["n_bars"] for g in gs)})
        idx[day] = row
    return idx


HALT_ACTIVITY_FLOOR = 60  # member must print in >= 60 minutes of its window: a live name


def select_days(idx: dict) -> dict:
    days = sorted(idx)
    runner = max(days, key=lambda d: idx[d]["max_mfe"])
    halt_pool = [d for d in days
                 if idx[d].get("n_halt_gaps", 0) >= 1
                 and idx[d].get("min_window_bars", 0) >= HALT_ACTIVITY_FLOOR]
    halt = max(halt_pool, key=lambda d: (idx[d]["n_halt_gaps"], idx[d]["max_run_min"],
                                         -int(d.replace("-", "")))) if halt_pool else None
    zero = [d for d in days if idx[d]["sum_gaps"] == 0 and not idx[d].get("n_gap", 0)]
    med = statistics.median(idx[d]["max_mfe"] for d in zero)
    ordinary = min(zero, key=lambda d: (abs(idx[d]["max_mfe"] - med), d))
    return {
        "runner": {"day": runner, "rule": "argmax max A_pm top-3 mfe over dev days",
                   "tie_break": "earliest day", **idx[runner]},
        "halt": {"day": halt,
                 "rule": f"argmax sum of halt-shaped print holes (>= {HALT_MIN}-minute "
                         f"print-to-print jumps) of the A_pm top-3 inside their windows, among "
                         f"days where every member prints in >= {HALT_ACTIVITY_FLOOR} minutes",
                 "tie_break": "larger single hole, then earliest day",
                 "pool_days": len(halt_pool), **(idx.get(halt, {}) if halt else {})},
        "ordinary": {"day": ordinary,
                     "rule": "zero print gaps (no >= 2-minute hole, anatomy gap_post = 0); max "
                             "member mfe closest to the median of that statistic over all such days",
                     "tie_break": "earliest day", "median_max_mfe_zero_gap": med, **idx[ordinary]},
        "scan": {"dev_days": len(days),
                 "days_with_zero_member_gaps": len(zero),
                 "days_with_halt_gaps": len(halt_pool),
                 "max_halt_gaps_seen": max((idx[d].get("n_halt_gaps", 0) for d in days), default=0),
                 "top_halt_days": [{"day": d, "n_halt_gaps": idx[d]["n_halt_gaps"],
                                    "max_run_min": idx[d]["max_run_min"],
                                    "min_window_bars": idx[d]["min_window_bars"],
                                    "max_mfe": round(idx[d]["max_mfe"], 4)}
                                   for d in sorted(halt_pool,
                                                   key=lambda d: (-idx[d]["n_halt_gaps"],
                                                                  -idx[d]["max_run_min"],
                                                                  d))[:5]]},
    }


# --------------------------------------------------------------------------- #
# raw print loading / path reconstruction
# --------------------------------------------------------------------------- #

COLS = ["symbol", "ts_utc", "price", "size", "exchange", "conditions", "trade_id", "tape"]


def load_prints(day: str, symbols: list[str]) -> tuple[pl.DataFrame, dict]:
    """Raw SIP prints for `symbols` on `day` (RTH +/- 30 min), plus IO telemetry."""
    path = TRADES / f"{day}.parquet"
    st = path.stat()
    t0 = time.perf_counter()
    df = (pl.scan_parquet(path).select(COLS)
          .filter(pl.col("symbol").is_in(symbols))
          .with_columns(pl.col("ts_utc").dt.convert_time_zone("America/New_York").alias("ts_et"))
          .with_columns((pl.col("ts_et").dt.hour().cast(pl.Int32) * 60
                         + pl.col("ts_et").dt.minute().cast(pl.Int32)).alias("et"))
          .filter((pl.col("et") >= 540) & (pl.col("et") < 1000))
          .collect())
    elapsed = time.perf_counter() - t0
    tel = {"file_bytes": st.st_size, "read_s": round(elapsed, 3),
           "rows_loaded": df.height,
           # across all three symbols at once the frame is not time-ordered; per symbol it is
           # (checked per member as ts_monotone_in_file_order)
           "frame_ts_sorted_all_symbols": bool(df["ts_utc"].is_sorted()) if df.height else None}
    return df, tel


def et_clock(ts_et) -> str:
    return ts_et.strftime("%H:%M:%S.%f")[:-3]


def empty_runs(ets: list[int], first_et: int, last_et: int) -> list[list[int]]:
    """Consecutive runs of ET minutes with no print, inside [first_et, last_et]."""
    present = set(int(e) for e in ets if first_et <= int(e) <= last_et)
    runs, cur = [], []
    for m in range(first_et, last_et + 1):
        if m in present:
            if cur:
                runs.append(cur)
                cur = []
        else:
            cur.append(m)
    if cur:
        runs.append(cur)
    return runs


def minute_coverage(ets: list[int], first_et: int, last_et: int) -> dict:
    """Coverage of the [first_et, last_et] minute grid by the minutes present in `ets`."""
    grid = last_et - first_et + 1
    present = set(int(e) for e in ets if first_et <= int(e) <= last_et)
    runs = empty_runs(ets, first_et, last_et)
    missing = sum(len(r) for r in runs)
    gaps = [r for r in runs if len(r) + 1 >= GAP_MIN]
    halts = [r for r in runs if len(r) + 1 >= HALT_MIN]
    biggest = max(runs, key=len) if runs else []
    return {"grid_minutes": grid, "minutes_with_print": len(present), "empty_minutes": missing,
            "coverage": round(len(present) / grid, 4) if grid else None,
            "n_gap_runs_ge2min": len(gaps),
            "n_gap_runs_ge5min": len(halts),
            "longest_empty_run_min": len(biggest) if biggest else 0,
            "longest_empty_run_et": [biggest[0], biggest[-1]] if biggest else None,
            "empty_run_hist": {str(k): sum(1 for r in runs if len(r) == k)
                               for k in sorted({len(r) for r in runs})}}


def gap_prints(sub: pl.DataFrame, runs: list[list[int]]) -> list[dict]:
    """For each empty run (given as ET minute lists), the prints bracketing it."""
    out = []
    if not runs:
        return out
    ts, ets = sub["ts_et"].to_list(), sub["et"].to_list()

    def _print(i: int) -> dict:
        return {"et": int(ets[i]), "ts_et": et_clock(ts[i]), "price": float(sub["price"][i]),
                "size": float(sub["size"][i]), "exchange": sub["exchange"][i],
                "conditions": list(sub["conditions"][i]), "tape": sub["tape"][i],
                "trade_id": int(sub["trade_id"][i])}

    for r in sorted(runs, key=len, reverse=True)[:5]:
        lo, hi = r[0], r[-1]
        before = [i for i, e in enumerate(ets) if e < lo]
        after = [i for i, e in enumerate(ets) if e > hi]
        if not before or not after:
            continue
        b, a = before[-1], after[0]
        out.append({
            "empty_et": [lo, hi], "empty_minutes": len(r),
            "minutes_without_print": int(hi - lo + 1),
            "before": _print(b), "after": _print(a),
            "reopen_move": round(float(sub["price"][a]) / float(sub["price"][b]) - 1.0, 5)})
    return out


def policy_delta(mins: pl.DataFrame, mins_all: pl.DataFrame) -> dict:
    """How the path would differ if every print (incl. odd lots) counted, minute by minute."""
    j = mins.join(mins_all, on="et", how="full", coalesce=True)
    if not j.height:
        return {}
    only_all = int(j.filter(pl.col("rh").is_null()).height)
    both = j.filter(pl.col("rh").is_not_null() & pl.col("ah").is_not_null())
    up = ((both["ah"] - both["rh"]) / both["rh"]).max() if both.height else None
    dn = ((both["rl"] - both["al"]) / both["rl"]).max() if both.height else None
    return {"minutes_compared": both.height,
            "minutes_only_in_all_prints": only_all,
            "minutes_identical": int(both.filter((pl.col("ah") == pl.col("rh"))
                                                 & (pl.col("al") == pl.col("rl"))).height),
            "minutes_with_higher_high": int(both.filter(pl.col("ah") > pl.col("rh")).height),
            "minutes_with_lower_low": int(both.filter(pl.col("al") < pl.col("rl")).height),
            "max_minute_high_uplift_rel": round(float(up), 6) if up is not None else None,
            "max_minute_low_drop_rel": round(float(dn), 6) if dn is not None else None}


def member_probe(day: str, mem: dict, all_p: pl.DataFrame, elig: pl.DataFrame,
                 bars: pl.DataFrame, session_end: int) -> dict:
    sym = mem["ticker"]
    a_raw = all_p.filter(pl.col("symbol") == sym)          # file order, unsorted on purpose
    tsv = a_raw["ts_utc"].to_numpy()
    n_back = int((tsv[1:] < tsv[:-1]).sum()) if len(tsv) > 1 else 0
    a = a_raw.sort("ts_utc")
    e = elig.filter(pl.col("symbol") == sym).sort("ts_utc")
    first_et, last_et = mem["fill_et"], int(mem["last_et"] or session_end)
    win_all = a.filter((pl.col("et") >= first_et) & (pl.col("et") <= last_et))
    win = e.filter((pl.col("et") >= first_et) & (pl.col("et") <= last_et))
    clk = win["ts_et"].to_list()
    cov = minute_coverage(win["et"].to_list(), first_et, last_et)
    runs = empty_runs(win["et"].to_list(), first_et, last_et)
    gaps = [r for r in runs if len(r) + 1 >= GAP_MIN]
    cov["gaps"] = gap_prints(win, gaps)

    # does the tape LABEL the hole? condition '5' = market-center reopening (halt reopen auction)
    ets_l = [int(x) for x in win["et"].to_list()]
    conds_l = win["conditions"].to_list()
    first_of: dict[int, int] = {}
    for i, x in enumerate(ets_l):
        first_of.setdefault(x, i)
    present_m = sorted(first_of)
    halts = [r for r in runs if len(r) + 1 >= HALT_MIN]
    n_halt_labeled = 0
    for r in halts:
        j = bisect.bisect_right(present_m, r[-1])
        if 0 < j < len(present_m) and "5" in conds_l[first_of[present_m[j]]]:
            n_halt_labeled += 1
    n_code5 = int(win.filter(pl.col("conditions").list.contains("5")).height)

    # same-microsecond ties: can the print SEQUENCE be recovered exactly?
    ties = (win.group_by("ts_utc")
            .agg(pl.len().alias("n"), pl.col("price").n_unique().alias("npx"),
                 pl.col("trade_id").min().alias("tid_lo"), pl.col("trade_id").max().alias("tid_hi"),
                 (pl.col("trade_id").diff().drop_nulls() <= 0).any().alias("tid_desc"))
            .filter(pl.col("n") > 1))
    n_tie_groups = ties.height
    n_tie_prints = int(ties["n"].sum()) if ties.height else 0
    n_tie_multipx = ties.filter(pl.col("npx") > 1).height
    n_tie_tid_desc = ties.filter(pl.col("tid_desc")).height
    amb = win.join(ties.filter(pl.col("npx") > 1).select("ts_utc"), on="ts_utc", how="inner")
    n_amb_seconds = amb.select(pl.col("ts_utc").dt.truncate("1s").n_unique()).item() if amb.height else 0
    mp_ts = ties.filter(pl.col("npx") > 1)["ts_utc"].to_list()
    mm = (win.group_by("et").agg(pl.col("ts_utc").min().alias("fts"),
                                 pl.col("ts_utc").max().alias("lts")))
    n_amb_minutes = int(mm.filter(pl.col("fts").is_in(mp_ts) | pl.col("lts").is_in(mp_ts)).height)

    # excluded prints: the raw prints whose conditions do NOT update high/low.
    # the (ts_utc, trade_id, price) key is validated unique inside the window below.
    key = ["ts_utc", "trade_id", "price"]
    n_dup_keys = win_all.height - win_all.select(key).n_unique()
    excl = win_all.join(win.select(key), on=key, how="anti")

    # what the exclusion policy costs the path: same minutes, ALL prints vs price-updating only
    mins_all = (win_all.group_by("et").agg(pl.col("price").max().alias("ah"),
                                           pl.col("price").min().alias("al")).sort("et"))

    def _hist_frame(f: pl.DataFrame, col: str = "count", k: int = 6) -> dict:
        if not f.height:
            return {}
        h = (f.with_columns(pl.col("conditions").list.join("|").alias("ck"))
             .group_by("ck").len().sort("len", descending=True))
        return {str(r[0]): int(r[1]) for r in h.head(k).iter_rows()}

    # minute reconciliation against the committed derived bars (hl == price-updating prints)
    mins = (win.group_by("et").agg(pl.col("price").max().alias("rh"), pl.col("price").min().alias("rl"),
                                   pl.len().alias("n"))
            .sort("et"))
    b = (bars.filter((pl.col("ticker") == sym) & (pl.col("et") >= first_et)
                     & (pl.col("et") <= last_et)).sort("et"))
    j = mins.join(b, on="et", how="full", coalesce=True).sort("et")
    both = j.filter(pl.col("rh").is_not_null() & pl.col("high").is_not_null())
    mism = both.filter((pl.col("rh") - pl.col("high")).abs() > BAR_EPS)
    mism_lo = both.filter((pl.col("rl") - pl.col("low")).abs() > BAR_EPS)
    bar_no_print = j.filter(pl.col("rh").is_null() & pl.col("high").is_not_null())
    max_hi = float((both["rh"] - both["high"]).abs().max()) if both.height else None
    max_lo = float((both["rl"] - both["low"]).abs().max()) if both.height else None
    return {
        "ticker": sym, "rank": mem["rank"], "fill_et": first_et, "fill_px": mem["fill_px"],
        "mfe": mem["mfe"], "last_bar_et": last_et, "session_end": session_end,
        "prints_all_file": a_raw.height, "prints_price_updating_file": e.height,
        "prints_window_all": win_all.height, "prints_window_eligible": win.height,
        "eligible_share_in_window": round(win.height / win_all.height, 4) if win_all.height else None,
        "first_print_et": et_clock(clk[0]) if clk else None,
        "last_print_et": et_clock(clk[-1]) if clk else None,
        "ts_monotone_in_file_order": n_back == 0, "n_backwards_ts_steps": n_back,
        "halt_visibility": {
            "n_gaps_ge2min": cov["n_gap_runs_ge2min"], "n_gaps_ge5min": cov["n_gap_runs_ge5min"],
            "max_gap_min": cov["longest_empty_run_min"],
            "n_halts_ending_with_reopen_print_code5": n_halt_labeled,
            "n_prints_with_code5": n_code5,
            "largest": (cov["gaps"][0] if cov["gaps"] else None)},
        "tie_structure": {
            "n_tie_groups": n_tie_groups, "n_tie_prints": n_tie_prints,
            "max_group": int(ties["n"].max()) if ties.height else 1,
            "tie_print_share": round(n_tie_prints / win.height, 4) if win.height else None,
            "n_groups_with_multiple_prices": n_tie_multipx,
            "n_groups_trade_id_not_ascending": n_tie_tid_desc,
            "n_seconds_touched_by_multiprice_ties": n_amb_seconds,
            "n_minutes_open_or_close_touched_by_multiprice_ties": n_amb_minutes},
        "coverage": {k: v for k, v in cov.items()},
        "bar_reconciliation": {
            "minutes_compared": both.height, "n_minutes_bar_without_print": bar_no_print.height,
            "n_high_mismatch": mism.height, "n_low_mismatch": mism_lo.height,
            "max_abs_high_diff": max_hi, "max_abs_low_diff": max_lo},
        "condition_hist_eligible": _hist_frame(win),
        "condition_hist_excluded": _hist_frame(excl),
        "prints_excluded": excl.height,
        "duplicate_exclusion_keys": n_dup_keys,
        "exchanges": sorted(set(win["exchange"].to_list())),
        "tapes": sorted(set(win["tape"].to_list())),
        "policy_delta_all_prints_vs_price_updating": policy_delta(mins, mins_all),
        "vs_anatomy": {
            "anatomy_day_high": mem.get("day_high"), "anatomy_close": mem.get("close"),
            "anatomy_mfe": mem.get("mfe"),
            "reconstructed_high": float(win["price"].max()) if win.height else None,
            "reconstructed_last": float(win["price"][-1]) if win.height else None,
            "high_delta_rel": (round(float(win["price"].max()) / mem["day_high"] - 1.0, 6)
                               if win.height and mem.get("day_high") else None),
            "close_delta_rel": (round(float(win["price"][-1]) / mem["close"] - 1.0, 6)
                                if win.height and mem.get("close") else None)},
        "prices": ({"min": float(win["price"].min()), "max": float(win["price"].max())}
                   if win.height else None),
    }


def sample_series(day: str, mem: dict, elig: pl.DataFrame, n_cap: int, sec_rows: int = 120) -> dict:
    """One ticker, the entry hour: print-level rows, per-minute OHLCV and a second-level series."""
    sym = mem["ticker"]
    h0 = mem["fill_et"]
    w = (elig.filter((pl.col("symbol") == sym) & (pl.col("et") >= h0) & (pl.col("et") <= h0 + 59))
         .sort("ts_utc"))
    ts, ets = w["ts_et"].to_list(), w["et"].to_list()
    px, sz = w["price"].to_list(), w["size"].to_list()
    ex, cd, tid = w["exchange"].to_list(), w["conditions"].to_list(), w["trade_id"].to_list()
    rows = [{"ts_et": et_clock(ts[i]), "et": int(ets[i]), "px": float(px[i]), "sz": float(sz[i]),
             "ex": ex[i], "cond": list(cd[i]), "tid": int(tid[i])} for i in range(w.height)]

    def _ohlc(keys):
        return (w.with_columns(keys.alias("k"))
                .group_by("k").agg(pl.col("price").first().alias("o"), pl.col("price").max().alias("h"),
                                   pl.col("price").min().alias("l"), pl.col("price").last().alias("c"),
                                   pl.col("size").sum().alias("v"), pl.len().alias("n"))
                .sort("k"))
    sec = _ohlc(pl.col("ts_et").dt.truncate("1s"))
    sec_rows_l = [{"sec_et": et_clock(r["k"]), "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"],
                   "v": r["v"], "n": r["n"]} for r in sec.head(sec_rows).iter_rows(named=True)]
    minute = [{"et": r["k"], "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"], "v": r["v"],
               "n": r["n"]} for r in _ohlc(pl.col("et")).iter_rows(named=True)]
    return {"day": day, "ticker": sym, "window_et": [h0, h0 + 59],
            "prints_in_hour": w.height, "prints_stored": min(w.height, n_cap),
            "seconds_in_hour": sec.height, "seconds_stored": len(sec_rows_l),
            "print_row_bytes_jsonl": len(json.dumps(rows[0], separators=(",", ":"))) if rows else None,
            "prints": rows[:n_cap], "per_second": sec_rows_l, "per_minute": minute}


# --------------------------------------------------------------------------- #
# cost model
# --------------------------------------------------------------------------- #

def cost_sample_day(day: str) -> dict:
    """Print census for one extra day (members only): the unbiased basis of the cost model."""
    mem = member_rows(sim.load_anatomy(day))
    if not mem:
        return {"day": day, "skipped": "no filled A_pm members"}
    t0 = time.perf_counter()
    all_p, tel = load_prints(day, [m["ticker"] for m in mem])
    elig = t5.price_updating(all_p)
    rows = []
    for m in mem:
        first_et, last_et = m["fill_et"], int(m["last_et"] or 959)
        wa = all_p.filter((pl.col("symbol") == m["ticker"]) & (pl.col("et") >= first_et)
                          & (pl.col("et") <= last_et))
        we = elig.filter((pl.col("symbol") == m["ticker"]) & (pl.col("et") >= first_et)
                         & (pl.col("et") <= last_et))
        rows.append({"ticker": m["ticker"], "prints_all": wa.height, "prints_eligible": we.height,
                     "minutes": last_et - first_et + 1,
                     "minutes_with_print": we["et"].n_unique()})
    return {"day": day, "members": rows, "rows_loaded": tel["rows_loaded"],
            "read_s": tel["read_s"], "file_bytes": tel["file_bytes"],
            "wall_s": round(time.perf_counter() - t0, 3)}


def pct(vals: list, p: float):
    if not vals:
        return None
    s = sorted(vals)
    return s[min(int(len(s) * p), len(s) - 1)]


def cost_model(idx_days: list[str], tel: list[dict], members: list[dict], n_dev_days: int,
               sample: dict, cost_rows: list[dict], filled_member_days: int) -> dict:
    sizes = sorted((TRADES / f"{d}.parquet").stat().st_size for d in idx_days
                   if (TRADES / f"{d}.parquet").exists())
    tot = sum(sizes)
    reads = [t["read_s"] for t in tel] + [r["read_s"] for r in cost_rows if "read_s" in r]
    per_day = statistics.median(reads)
    walls = [r["wall_s"] for r in cost_rows if "wall_s" in r]
    wall_p50 = statistics.median(walls) if walls else None
    probe_walls = [t["wall_s"] for t in tel if t.get("wall_s")]
    elig = [m["prints_eligible"] for r in cost_rows for m in r.get("members", [])]
    allp = [m["prints_all"] for r in cost_rows for m in r.get("members", [])]
    covs = [m["minutes_with_print"] / m["minutes"] for r in cost_rows for m in r.get("members", [])
            if m["minutes"]]
    jsonl_row = sample.get("print_row_bytes_jsonl") if sample else None
    member_days = filled_member_days
    med_prints = statistics.median(elig) if elig else None
    have = [d for d in idx_days if (TRADES / f"{d}.parquet").exists()]
    return {
        "day_availability": {"dev_days": n_dev_days, "dev_days_with_trades_file": len(have),
                             "dev_days_missing": sorted(set(idx_days) - set(have))[:10]},
        "trades_store": {"files": len(sizes), "dev_days": n_dev_days, "bytes_total": tot,
                         "bytes_mean": int(tot / len(sizes)) if sizes else None,
                         "bytes_min": sizes[0] if sizes else None,
                         "bytes_max": sizes[-1] if sizes else None,
                         "bytes_p50": int(statistics.median(sizes)) if sizes else None,
                         "probe_day_bytes": {t["day"]: t["file_bytes"] for t in tel}},
        "cost_sample": {
            "days_sampled": len(cost_rows), "member_days_sampled": len(elig),
            "read_s_per_day": {"p10": pct(reads, 0.1), "p50": per_day, "p90": pct(reads, 0.9)},
            "prints_eligible_per_member_day": {"p10": pct(elig, 0.1), "p50": med_prints,
                                               "p90": pct(elig, 0.9), "max": max(elig) if elig else None},
            "prints_all_per_member_day": {"p50": pct(allp, 0.5), "p90": pct(allp, 0.9)},
            "eligible_share_of_prints": (round(statistics.median(
                [m["prints_eligible"] / m["prints_all"] for r in cost_rows
                 for m in r.get("members", []) if m["prints_all"]]), 4) if elig else None),
            "minute_coverage": {"p10": round(pct(covs, 0.1), 4) if covs else None,
                                "p50": round(pct(covs, 0.5), 4) if covs else None,
                                "p90": round(pct(covs, 0.9), 4) if covs else None},
            "per_day": [{"day": r["day"], "read_s": r.get("read_s"),
                         "rows_loaded": r.get("rows_loaded"),
                         "prints_eligible": sum(m["prints_eligible"] for m in r.get("members", []))}
                        for r in cost_rows],
        },
        "full_universe_1066": {
            "dev_days": n_dev_days,
            "filled_member_days_A_pm_top3": member_days,
            "member_days_upper_bound": TOP * n_dev_days,
            "full_file_scan_bytes": tot,
            "full_file_scan_s_serial": round(per_day * n_dev_days, 1),
            "full_file_scan_min_4_workers": round(per_day * n_dev_days / 4 / 60, 2),
            "reconstruct_wall_s_per_day_p50": wall_p50,
            "reconstruct_s_serial": round(wall_p50 * n_dev_days, 1) if wall_p50 else None,
            "reconstruct_min_serial": round(wall_p50 * n_dev_days / 60, 2) if wall_p50 else None,
            "reconstruct_min_4_workers": (round(wall_p50 * n_dev_days / 4 / 60, 2)
                                          if wall_p50 else None),
            "probe_verify_wall_s_per_day": probe_walls,
            "median_prints_per_member_day": med_prints,
            "total_eligible_prints": int(med_prints * member_days) if med_prints else None,
            "jsonl_row_bytes": jsonl_row,
            "reconstructed_path_bytes_jsonl": (int(med_prints * member_days * jsonl_row)
                                               if med_prints and jsonl_row else None),
            "reconstructed_path_bytes_binary_est": (int(med_prints * member_days * 20)
                                                    if med_prints else None),
            "note": "binary estimate = 20 B/print (int64 ts + float32 px + float32 sz + code byte); "
                    "reconstruct = read + price_updating + per-member minute aggregation, measured "
                    "on the cost-sample days; probe_verify adds the tie/gap/bar-reconciliation checks",
        },
        "note": "read cost = one projected-column scan of the whole day file per day; the symbol "
                "filter runs after the read (no row-group pruning guarantee), so this is an upper "
                "bound on a member-only extraction. Times are warm-cache single-process "
                "measurements with no competing load; expect run-to-run variance well above the "
                "clock resolution, so read p10/p50/p90 above are the honest summary, not a point "
                "estimate.",
    }


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #

def probe_day(day: str, n_cap: int) -> dict:
    sim.guard_day(day)
    rec = sim.load_anatomy(day)
    mem = member_rows(rec)
    if not mem:
        raise RuntimeError(f"{day}: no A_pm top-{TOP} filled members")
    syms = [m["ticker"] for m in mem]
    all_p, tel = load_prints(day, syms)
    drip = time.perf_counter()
    elig = t5.price_updating(all_p)
    tel["price_updating_s"] = round(time.perf_counter() - drip, 3)
    bp = BARS / f"{day}.parquet"
    bars = pl.read_parquet(bp) if bp.exists() else pl.DataFrame(
        schema={"ticker": pl.Utf8, "et": pl.Int32, "high": pl.Float64, "low": pl.Float64})
    sem = sim.session_end_map()
    session_end = int(sem.get(day, 959))
    members = [member_probe(day, m, all_p, elig, bars, session_end) for m in mem]
    return {"day": day, "symbols": syms, "io": tel, "members": members,
            "bars_file": {"path": str(bp.relative_to(ROOT)), "exists": bp.exists(),
                          "bytes": bp.stat().st_size if bp.exists() else None}}


def build(days: list[str] | None, force: bool, n_cap: int, out_path: Path, n_cost: int = 24) -> dict:
    dev = sim.dev_days()
    idx = day_index(dev)
    picks = select_days(idx)
    chosen = {k: picks[k]["day"] for k in ("runner", "halt", "ordinary") if picks.get(k, {}).get("day")}
    if days:
        chosen = {f"day{i + 1}": d for i, d in enumerate(days)}

    prev = {}
    if out_path.exists() and not force:
        try:
            prev = json.load(open(out_path)).get("days", {})
        except Exception:  # noqa: BLE001 - corrupt partial artifact -> rebuild
            prev = {}

    out_days = dict(prev)
    tel, member_stats, sample = [], [], None
    for key, day in chosen.items():
        path = TRADES / f"{day}.parquet"
        st = path.stat()
        cached = out_days.get(day)
        if (cached and not force and cached.get("io", {}).get("file_mtime_ns") == st.st_mtime_ns
                and cached.get("io", {}).get("file_bytes") == st.st_size):
            res = cached
            print(f"[cache] {key}: {day}", flush=True)
        else:
            t0 = time.perf_counter()
            res = probe_day(day, n_cap)
            res["io"]["file_mtime_ns"] = st.st_mtime_ns
            res["io"]["wall_s"] = round(time.perf_counter() - t0, 3)
            print(f"[probe] {key}: {day} {res['symbols']} wall={res['io']['wall_s']}s "
                  f"read={res['io']['read_s']}s rows={res['io']['rows_loaded']}", flush=True)
        out_days[day] = res
        tel.append({"day": day, **res["io"]})
        member_stats.extend({"day": day, **m} for m in res["members"])
        # incremental artifact write (resumable)
        _write({"status": "partial", "picks": picks, "days": out_days}, out_path)

    # the stored sample series = runner day's rank-1 member, entry hour
    sday = chosen.get("runner") or next(iter(chosen.values()))
    rec = sim.load_anatomy(sday)
    mem0 = member_rows(rec)[0]
    all_p, _ = load_prints(sday, [mem0["ticker"]])
    elig = t5.price_updating(all_p)
    sample = {**sample_series(sday, mem0, elig, n_cap),
              "case": "runner" if sday == chosen.get("runner") else "first-day",
              "entry_rank": 1}

    # print census over evenly spaced dev days: the cost model's unbiased basis
    cost_rows = []
    if n_cost > 0:
        step = max(1, len(dev) // n_cost)
        cost_days = dev[::step][:n_cost]
        for i, d in enumerate(cost_days, 1):
            r = cost_sample_day(d)
            cost_rows.append(r)
            print(f"[cost {i}/{len(cost_days)}] {d} read={r.get('read_s')}s "
                  f"elig={sum(m['prints_eligible'] for m in r.get('members', []))}", flush=True)

    obj = {
        "_producer": "basket_subminute_probe.py",
        "answer": {
            "reconstructible_local": True,
            "grain": "individual trade prints (microsecond timestamps), i.e. strictly below "
                     "second level; a second-level or coarser path is a truncation of it",
            "purchase_required": False,
            "source": str(TRADES),
            "api": {
                "loader": "factory/scripts/basket_subminute_probe.py :: load_prints(day, symbols)",
                "anatomy": "factory/scripts/basket_sim.py :: load_anatomy(day), "
                           "snapshot_of(rec, 'A_pm', 570), dev_days(), session_end_map()",
                "print_filter": "factory/scripts/basket_t5_rawpaths.py :: price_updating(frame) "
                                "(= factory/scripts/sip_bars.py :: combine(conditions, tape) "
                                "strictest-rule-wins, keep hl == 2)",
                "file_layout": f"{{trades_root}}/<YYYY-MM-DD>.parquet  (trades_root={TRADES})",
                "substrate_chain": "raw SIP prints (this probe) -> sip_bars.build_bars -> "
                                   "sip_netbars.py net bars -> basket_anatomy.py anatomy jsonl; "
                                   "basket_anatomy.py itself consumes minute BARS, not prints, "
                                   "so the print-level API above is the only raw-print entry point",
                "manifest": "{trades_root}/<YYYY-MM-DD>.manifest.json (rows, window, sha256, "
                            "schema, min/max ts per day)",
                "scan_template": "pl.scan_parquet(f).select(COLS).filter(symbol is_in syms)"
                                 ".with_columns(tz->America/New_York).collect()",
            },
            "fields_available": ["symbol", "ts_utc (Datetime us, UTC)", "price (f64)",
                                 "size (f64, shares)", "exchange (single-char code)",
                                 "conditions (List[str])", "trade_id (i64)", "tape ('C')"],
            "condition_policy": {
                "rule": "strictest rule wins across a print's condition codes; a print is "
                        "eligible for the path iff its high-low rule is 2 (sip_bars.combine)",
                "fill": "sip_bars.py :: combine(conditions, tape) -> (open_close, high_low, volume)",
                "updates_high_low": sorted(c for c, r in sb.RULES_M.items() if r[1] == sb.G),
                "does_not_update_high_low": sorted(c for c, r in sb.RULES_M.items() if r[1] != sb.G),
                "tape_dependent": "'B' updates high/low on tape C but not on tape A/B "
                                  "(average-price trade)",
                "auction_official_codes": sorted(sb.AUCTION_CODES),
                "print_side": "a raw print is not necessarily a plain trade: odd lots (I), "
                              "derivatively priced (4), prior reference (P), official/auction "
                              "(Q, M, O, 5, 6), odd-lot+reopen combos appear with their own "
                              "prices; the policy above decides which of them move the path",
            },
            "evidence_summary": {
                "member_days_probed": len(member_stats),
                "days_probed": sorted(out_days),
                "minutes_reconciled_vs_committed_bars": sum(
                    m["bar_reconciliation"]["minutes_compared"] for m in member_stats),
                "high_or_low_mismatches": sum(
                    m["bar_reconciliation"]["n_high_mismatch"]
                    + m["bar_reconciliation"]["n_low_mismatch"] for m in member_stats),
                "per_symbol_ts_monotone_in_file_order": all(
                    m["ts_monotone_in_file_order"] for m in member_stats),
            },
            "not_reconstructible_locally": [
                "the mutual order of prints sharing one microsecond (8.8%-style ties): file order "
                "is the only order, trade_id is not monotone inside ties",
                "a price for a minute that has no price-updating print: there is nothing to "
                "reconstruct, and any value there is a provider-bar fill",
                "exchange matching-engine timestamps (the tape carries feed print timestamps)",
            ],
        },
        "selection": picks,
        "days": out_days,
        "sample_series": sample,
        "cost_model": cost_model(sorted(idx), tel, member_stats, len(dev), sample, cost_rows,
                                 sum(len(v["members"]) for v in idx.values())),
        "cost_sample_days": [r["day"] for r in cost_rows],
        "io_telemetry": tel,
        "checks": checks(member_stats),
        "caveats": caveats(out_days, member_stats),
    }
    _write({**obj, "status": "ok"})
    return obj


def caveats(days: dict, member_stats: list[dict]) -> list[str]:
    excl = sum(m["prints_excluded"] for m in member_stats)
    tot = sum(m["prints_window_all"] for m in member_stats)
    ties = [m["tie_structure"] for m in member_stats]
    # dominant excluded condition combos across the probe
    agg: dict[str, int] = {}
    for m in member_stats:
        for k, v in m["condition_hist_excluded"].items():
            agg[k] = agg.get(k, 0) + v
    top_excl = ", ".join(f"{k} ({v})" for k, v in sorted(agg.items(), key=lambda kv: -kv[1])[:4])
    no_print = sum(m["bar_reconciliation"]["n_minutes_bar_without_print"] for m in member_stats)
    sizes = sorted((TRADES / f"{d}.parquet").stat().st_size for d in days)
    return [
        "the tape is a raw print stream, not a trade-only stream: prints whose conditions do not "
        "update high/low are excluded from the path by the canonical policy - "
        f"{excl} of {tot} window prints ({round(100 * excl / max(tot, 1), 1)}%) in the "
        f"{len(member_stats)} probe member-days; dominant excluded combos: {top_excl}",
        "prints at the same microsecond (groups of up to "
        f"{max(t['max_group'] for t in ties)} in the probe) have no recoverable mutual order: "
        "trade_id is not monotone inside ties "
        f"({sum(t['n_groups_trade_id_not_ascending'] for t in ties)} such groups), so file order is "
        "the only order available. Per-second and per-minute high/low are unaffected (verified "
        "against the committed bars); open/close of a second or minute can differ by the "
        "tie-break rule only where the bucket's first or last microsecond is a multi-price tie: "
        f"{sum(t['n_seconds_touched_by_multiprice_ties'] for t in ties)} seconds and "
        f"{sum(t['n_minutes_open_or_close_touched_by_multiprice_ties'] for t in ties)} minutes of "
        f"{sum(m['coverage']['grid_minutes'] for m in member_stats)} probe minutes",
        "a minute with no price-updating print has no raw open/close at all: any fill or valuation "
        f"there is a provider-bar fill, not a print ({no_print} such bar-minutes in these "
        f"{len(member_stats)} member-days); on thin/halted names they are the norm, not the exception",
        f"one day file holds the whole net universe (median {int(statistics.median(sizes)) / 1e6:.1f} "
        "MB): a per-day member extraction reads the whole day file unless row-group pruning on "
        "`symbol` applies",
        "IEX is not a sub-minute source locally: data/iex_tape/ holds minute bars (668 days, "
        "2024-01 onward) of IEX-only prints; IEX is a single venue, a strict subset of SIP, so "
        "IEX-only prices can differ from the consolidated SIP print at the same second",
        "timestamps are the feed's print timestamps (microsecond resolution), not exchange "
        "matching-engine timestamps; they are the only ordering evidence in the file",
        "a run of minutes with no price-updating print is a halt signature (halts stop prints) but "
        "illiquidity produces the same signature; the tape does carry reopen labels - a print with "
        "condition code 5 (market-center reopening) follows "
        f"{sum(m['halt_visibility']['n_halts_ending_with_reopen_print_code5'] for m in member_stats)}"
        f"of {sum(m['halt_visibility']['n_gaps_ge5min'] for m in member_stats)} halt-sized holes in "
        "the probe; the rest are indistinguishable from illiquidity, and the ordinary day has no "
        "holes at all",
        "excluded-print counts rely on the (ts_utc, trade_id, price) key being unique inside the "
        f"window: measured duplicates = {sum(m['duplicate_exclusion_keys'] for m in member_stats)}",
    ]


def checks(member_stats: list[dict]) -> dict:
    ms = member_stats
    recs = [m["bar_reconciliation"] for m in ms]
    cov = [m["coverage"] for m in ms]
    ties = [m["tie_structure"] for m in ms]
    return {
        "n_member_days_probed": len(ms),
        "ts_monotone_in_file_order_all": all(m["ts_monotone_in_file_order"] for m in ms),
        "backwards_ts_steps_total": sum(m["n_backwards_ts_steps"] for m in ms),
        "minutes_compared": sum(r["minutes_compared"] for r in recs),
        "high_mismatch_minutes": sum(r["n_high_mismatch"] for r in recs),
        "low_mismatch_minutes": sum(r["n_low_mismatch"] for r in recs),
        "max_abs_high_diff": max((r["max_abs_high_diff"] or 0.0) for r in recs),
        "max_abs_low_diff": max((r["max_abs_low_diff"] or 0.0) for r in recs),
        "bar_minutes_without_raw_print": sum(r["n_minutes_bar_without_print"] for r in recs),
        "coverage_min": min(c["coverage"] for c in cov),
        "coverage_median": statistics.median(c["coverage"] for c in cov),
        "empty_minutes_total": sum(c["empty_minutes"] for c in cov),
        "gap_runs_ge2min_total": sum(c["n_gap_runs_ge2min"] for c in cov),
        "gap_runs_ge5min_total": sum(c["n_gap_runs_ge5min"] for c in cov),
        "halts_ending_with_reopen_print_code5": sum(
            m["halt_visibility"]["n_halts_ending_with_reopen_print_code5"] for m in ms),
        "duplicate_exclusion_keys_total": sum(m["duplicate_exclusion_keys"] for m in ms),
        "longest_empty_run_min": max(c["longest_empty_run_min"] for c in cov),
        "eligible_prints_total": sum(m["prints_window_eligible"] for m in ms),
        "excluded_prints_total": sum(m["prints_excluded"] for m in ms),
        "tie_prints_total": sum(t["n_tie_prints"] for t in ties),
        "tie_print_share_overall": round(
            sum(t["n_tie_prints"] for t in ties) / max(sum(m["prints_window_eligible"] for m in ms), 1), 4),
        "max_tie_group": max(t["max_group"] for t in ties),
        "seconds_touched_by_multiprice_ties_total": sum(
            t["n_seconds_touched_by_multiprice_ties"] for t in ties),
        "tie_groups_multiprice_total": sum(t["n_groups_with_multiple_prices"] for t in ties),
        "tie_groups_trade_id_not_ascending_total": sum(
            t["n_groups_trade_id_not_ascending"] for t in ties),
    }


def _write(obj: dict, path: Path = OUT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=1, default=str)
    os.replace(tmp, path)


def selftest() -> None:
    assert minute_coverage([570, 571, 573], 570, 573)["empty_minutes"] == 1
    c1 = minute_coverage([570, 574], 570, 574)          # 3 empty minutes -> 4-min jump
    assert c1["n_gap_runs_ge2min"] == 1 and c1["n_gap_runs_ge5min"] == 0, c1
    c1b = minute_coverage([570, 572], 570, 575)         # runs [571] and [573..575]
    assert (c1b["n_gap_runs_ge2min"], c1b["empty_run_hist"]) == (2, {"1": 1, "3": 1}), c1b
    c2 = minute_coverage([570, 575], 570, 575)          # 4 empty minutes -> 5-min jump
    assert c2["longest_empty_run_min"] == 4 and c2["n_gap_runs_ge5min"] == 1, c2
    assert c2["longest_empty_run_et"] == [571, 574], c2
    assert empty_runs([570, 573, 574], 570, 575) == [[571, 572], [575]], \
        empty_runs([570, 573, 574], 570, 575)
    assert empty_runs([570, 575], 570, 575) == [[571, 572, 573, 574]]
    assert et_clock(__import__("datetime").datetime(2021, 2, 1, 9, 30, 1, 123456)) == "09:30:01.123"
    print("self-test OK")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="*", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--select-only", action="store_true")
    ap.add_argument("--print-cap", type=int, default=SAMPLE_PRINT_CAP)
    ap.add_argument("--cost-days", type=int, default=24,
                    help="evenly spaced dev days censused for the cost model (0 = skip)")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return 0
    out = Path(args.out)
    print(f"trades root: {TRADES}", flush=True)
    if args.select_only:
        idx = day_index(sim.dev_days())
        print(json.dumps(select_days(idx), indent=1))
        return 0
    obj = build(args.days, args.force, args.print_cap, out, args.cost_days)
    print(json.dumps({"picks": {k: v.get("day") for k, v in obj["selection"].items()
                                if k != "scan"},
                      "checks": obj["checks"],
                      "cost": obj["cost_model"]["full_universe_1066"],
                      "cost_sample": obj["cost_model"]["cost_sample"]["prints_eligible_per_member_day"],
                      "bytes": out.stat().st_size}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
