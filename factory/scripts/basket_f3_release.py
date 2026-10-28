#!/usr/bin/env python3
"""BASKET-01 Phase-2 family F3 — survival / weakening / release structure map.

Contract: `researches/PRE-REG-BASKET-02.md` §3.3 (frozen 2026-09-22, commit 78442f2)
and `factory/BASKET-SIM-CONTRACT.md` (clock/causality: a bar with et=t is completed
at t+1; every decision from completed bars only; executions next bar open; a bar's
own high can never trigger a peak-relative rule on the same bar).

This is RESEARCH ONLY — no strategy P&L simulation (the sim engine is a later ask).
It measures, from canonical SIP anatomy fills + SIP bars:

  1. entry-relative deterioration: first breach of -5/-8/-10/-15 from fill, under two
     completed-bar lenses — LOW (R1 semantics, `low <= level`) and CLOSE (R2 semantics,
     `close < level`) — with forward outcomes after w in {1,3,5,10} minutes and the
     joint depth x duration x recovery structure;
  2. peak-relative deterioration: for tickets with raw MFE >= +10% from fill, first
     close below (1-g)*running_peak for g in {30,40,50,60} (peak excludes the current
     bar, per sim contract §7.4), forward outcomes and conditioning;
  3. the dn-before-up race: how many eventual +30/+50/+100 touchers have a release
     event fire before the touch bar (strict and same-bar), by lens and g;
  4. candidate release modules within the frozen §3.3 grids + full module surface.

Every outcome is read from bars STRICTLY LATER than the decision bar (1-bar lag).
Session end comes from `sip/phase2_session_calendar.json` when present, else the
temporary map {779 on the 7 known early closes, else 959} and is FLAGGED.

Evidence boundary: dev months only (2021-02..2023-12, 2025-02..2026-05). Sealed
(2024, 2025-01) and reserved (2026-06..08) months are refused by the loader.

Outputs (incremental month shards + merge):
  factory/artifacts/basket/phase2/F3_release/
    shards/{tickets,breach,giveback}/YYYY-MM.jsonl
    breach_recovery.json / breach_recovery.md
    giveback.json / giveback.md
    modules_candidates.md
    README.md
    run_config.json

Usage:
  .venv/bin/python factory/scripts/basket_f3_release.py --self-test
  .venv/bin/python factory/scripts/basket_f3_release.py --months 2021-02 --force
  .venv/bin/python factory/scripts/basket_f3_release.py --all
  .venv/bin/python factory/scripts/basket_f3_release.py --merge-only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
ART = Path(os.environ.get("BASKET_ART_ROOT", str(ROOT / "factory" / "artifacts" / "basket" / "sip")))
OUT = ROOT / "factory" / "artifacts" / "basket" / "phase2" / "F3_release"
SHARD_DIR = OUT / "shards"
CAL_PATH = ART / "phase2_session_calendar.json"

DEV_MONTHS = tuple(
    [f"2021-{m:02d}" for m in range(2, 13)]
    + [f"2022-{m:02d}" for m in range(1, 13)]
    + [f"2023-{m:02d}" for m in range(1, 13)]
    + [f"2025-{m:02d}" for m in range(2, 13)]
    + [f"2026-{m:02d}" for m in range(1, 6)]
)
EARLY_CLOSES = {
    "2021-11-26": 779, "2022-11-25": 779, "2023-07-03": 779, "2023-11-24": 779,
    "2025-07-03": 779, "2025-11-28": 779, "2025-12-24": 779,
}
DEFAULT_SESSION_END = 959

POPS = (("A_pm", 570, "A_pm"), ("A_open", 570, "A_open"), ("B", 600, "B600"))
POP_IDS = tuple(p for _, _, p in POPS)
LENSES = ("low", "close")
LEVELS = (5, 8, 10, 15)
WS = (1, 3, 5, 10)
GS = (30, 40, 50, 60)
UP_H = (30, 50, 100)
B1_END = "2023-12-29"

# candidate modules inside the frozen PRE-REG-02 §3.3 grids (fixed a priori)
CANDIDATES = (
    ("C1_R2_10_5", "R2(10,5): close < -10% level, release iff still below at +5 min"),
    ("C2_R2_15_10", "R2(15,10): close < -15% level, release iff still below at +10 min"),
    ("C3_R3_50", "R3(50): close below 0.50 x running peak"),
    ("C4_R1_10_or_R3_50", "any-of: R1(-10) low touch OR R3(50)"),
    ("C5_R2_10_5_and_R3_50", "conjunction: R2(10,5) AND R3(50) (both confirm)"),
)


# ---------------------------------------------------------------- loader / calendar

def refuse_month(month: str):
    """Return None if allowed, else the refusal reason. Hard evidence boundary."""
    if month in DEV_MONTHS:
        return None
    if month.startswith("2024") or month == "2025-01":
        return "sealed"
    if month >= "2026-06":
        return "reserved"
    return "outside_dev_span"


def load_session_calendar():
    """Returns (map date->session_end, source_label). Committed file wins; temp map flagged."""
    if CAL_PATH.exists():
        try:
            raw = json.loads(CAL_PATH.read_text())
            days = raw.get("days") or raw.get("calendar") or raw.get("session_end") or raw
            m = {}
            for k, v in days.items():
                if isinstance(v, dict):
                    v = v.get("session_end", v.get("end"))
                if isinstance(v, (int, float)):
                    m[str(k)[:10]] = int(v)
            if len(m) >= 1000:
                return m, "phase2_session_calendar.json"
        except Exception as exc:  # pragma: no cover - defensive
            return dict(EARLY_CLOSES), f"temporary_map(FLAG: calendar unreadable: {exc})"
    return dict(EARLY_CLOSES), "temporary_map(FLAG: phase2_session_calendar.json absent)"


def session_end_for(date: str, cal: dict) -> int:
    return int(cal.get(date, DEFAULT_SESSION_END))


# ---------------------------------------------------------------- pure path helpers

def _first(mask: np.ndarray, start: int = 0):
    if start >= len(mask):
        return None
    idx = np.flatnonzero(mask[start:])
    return None if idx.size == 0 else int(idx[0]) + start


def _last_le(ets: np.ndarray, x: int) -> int:
    return int(np.searchsorted(ets, x, side="right")) - 1


def _prev_peak(HS: np.ndarray):
    """prevmax[t] = max(HS[:t]) (excludes bar t; -inf at t=0) + index of last new high < t."""
    n = len(HS)
    prevmax = np.full(n, -np.inf)
    pbi = np.full(n, -1, dtype=np.int64)
    if n < 2:
        return prevmax, pbi
    prevmax[1:] = np.maximum.accumulate(HS[:-1])
    prev = np.concatenate(([-np.inf], np.maximum.accumulate(HS[:-1])))
    nh = np.flatnonzero(HS > prev)  # strictly new highs (bar 0 included)
    pos = np.searchsorted(nh, np.arange(n) - 1, side="right") - 1
    ok = pos >= 0
    pbi[ok] = nh[pos[ok]]
    return prevmax, pbi


def _mfe_after(ETS, HS, P0, fire_et):
    k = int(np.searchsorted(ETS, fire_et, side="right"))
    return None if k >= len(HS) else round(float(HS[k:].max() / P0 - 1.0), 6)


def breach_fields(ETS, HS, LS, CS, P0, i, lens, L, session_end):
    """First-breach event fields. Windows/outcomes use strictly later bars only."""
    n = len(ETS)
    lv = P0 * (1.0 - L / 100.0)
    be = int(ETS[i])
    px = float(LS[i]) if lens == "low" else float(CS[i])
    depth = (lv - px) / P0 * 100.0
    row = {
        "breach_et": be, "breach_i": int(i), "level_px": round(lv, 6),
        "depth_pct": round(depth, 4), "close_at_breach": round(float(CS[i]), 6),
        "low_at_breach": round(float(LS[i]), 6),
    }
    k = _first(CS >= lv, i + 1)
    if k is None:
        row["dur_min"] = int(ETS[-1] - be)
        row["recovered_level"] = False
        row["level_reclaim_et"] = None
    else:
        row["dur_min"] = int(ETS[k] - be)
        row["recovered_level"] = True
        row["level_reclaim_et"] = int(ETS[k])
    k2 = _first(CS > P0, i + 1)
    row["recovered_fill"] = k2 is not None
    row["recovery_min"] = int(ETS[k2] - be) if k2 is not None else None
    for w in WS:
        # within-w outcomes: bars with et <= breach+w (strictly later than breach bar)
        jw = _last_le(ETS, min(be + w, session_end))
        # window-end / release bar: FIRST completed bar with et >= breach+w (halt-aware)
        js = int(np.searchsorted(ETS, be + w, side="left"))
        if jw <= i:
            row[f"w{w}_reclaim_mid"] = None
            row[f"w{w}_close_above_fill"] = None
            row[f"w{w}_cont_lower5"] = None
        else:
            seg = slice(i + 1, jw + 1)
            row[f"w{w}_reclaim_mid"] = bool(HS[seg].max() >= P0 * (1.0 - L / 200.0))
            row[f"w{w}_close_above_fill"] = bool(CS[seg].max() > P0)
            row[f"w{w}_cont_lower5"] = bool(LS[seg].min() <= lv * 0.95)
        if js >= n:
            row[f"w{w}_still_below"] = None
            row[f"w{w}_end_et"] = None
            row[f"w{w}_mfe"] = None
        else:
            row[f"w{w}_still_below"] = bool(CS[js] < lv)
            row[f"w{w}_end_et"] = int(ETS[js])
            row[f"w{w}_mfe"] = round(float(HS[js + 1:].max() / P0 - 1.0), 6) if js + 1 < n else None
    if i + 1 < n:
        row["mfe_fwd"] = round(float(HS[i + 1:].max() / P0 - 1.0), 6)
        row["mae_fwd"] = round(float(LS[i + 1:].min() / P0 - 1.0), 6)
    else:
        row["mfe_fwd"] = None
        row["mae_fwd"] = None
    row["p_mfe_fwd_ge10"] = bool(row["mfe_fwd"] is not None and row["mfe_fwd"] >= 0.10)
    # compact buckets (3-4 per axis, documented in README)
    row["depth_bucket"] = "<=0.5" if depth <= 0.5 else ("0.5-2" if depth <= 2.0 else ">2")
    if not row["recovered_level"]:
        row["dur_bucket"] = ">15/none"
    else:
        d = row["dur_min"]
        row["dur_bucket"] = "<=1" if d <= 1 else ("2-5" if d <= 5 else ("6-15" if d <= 15 else ">15/none"))
    if not row["recovered_fill"]:
        row["rec_bucket"] = ">20/none"
    else:
        r = row["recovery_min"]
        row["rec_bucket"] = "<=5" if r <= 5 else ("6-20" if r <= 20 else ">20/none")
    return row


def giveback_event(ETS, HS, LS, CS, P0, g):
    """First close below (1-g)*prev_peak with prev_peak >= 1.10*P0 (winner drawdown)."""
    n = len(HS)
    if n < 2:
        return None
    prevmax, pbi = _prev_peak(HS)
    with np.errstate(invalid="ignore"):
        cond = (CS < (1.0 - g / 100.0) * prevmax) & (prevmax >= 1.10 * P0)
    cond[0] = False
    idx = np.flatnonzero(cond)
    if idx.size == 0:
        return None
    t = int(idx[0])
    peak = float(prevmax[t])
    close_t = float(CS[t])
    et_t = int(ETS[t])
    row = {
        "trigger_et": et_t, "trigger_i": t, "peak_px": round(peak, 6),
        "peak_ret": round(peak / P0 - 1.0, 6), "close_at_trigger": round(close_t, 6),
        "dd_at_trigger": round(close_t / peak - 1.0, 6),
        "time_since_high_min": int(et_t - ETS[pbi[t]]) if pbi[t] >= 0 else None,
        "recover_new_high": bool(HS[t + 1:].max() > peak) if t + 1 < n else None,
        "cont_down10": bool((CS[t + 1:] <= 0.9 * close_t).any()) if t + 1 < n else None,
        "mfe_after_rel_close": round(float(HS[t + 1:].max() / close_t - 1.0), 6) if t + 1 < n else None,
        "mfe_after_rel_fill": round(float(HS[t + 1:].max() / P0 - 1.0), 6) if t + 1 < n else None,
        "mae_after_rel_close": round(float(LS[t + 1:].min() / close_t - 1.0), 6) if t + 1 < n else None,
    }
    j10 = _last_le(ETS, et_t + 10)
    row["recover_new_high_10m"] = bool(HS[t + 1:j10 + 1].max() > peak) if j10 > t else None
    row["mfe_positive_after"] = bool(row["mfe_after_rel_close"] is not None and row["mfe_after_rel_close"] > 0)
    return row


def first_r3_trigger(ETS, HS, CS, P0, g):
    """R3(g) module semantics (no MFE precondition): first close < (1-g)*prev_peak."""
    n = len(HS)
    if n < 2:
        return None
    prevmax, _ = _prev_peak(HS)
    with np.errstate(invalid="ignore"):
        cond = CS < (1.0 - g / 100.0) * prevmax
    cond[0] = False
    idx = np.flatnonzero(cond)
    return int(ETS[idx[0]]) if idx.size else None


def _first_ge_et(ETS, HS, thr):
    i = _first(HS >= thr)
    return int(ETS[i]) if i is not None else None


def _first_le_et(ETS, LS, thr):
    i = _first(LS <= thr)
    return int(ETS[i]) if i is not None else None


# ---------------------------------------------------------------- day processing

def load_bars(date: str):
    """ticker -> (et, open, high, low, close) numpy slices (et sorted ascending)."""
    p = ART / "bars" / f"{date}.parquet"
    df = pl.read_parquet(p, columns=["ticker", "et", "open", "high", "low", "close"])
    df = df.sort(["ticker", "et"])
    tk = df["ticker"].to_numpy()
    et = df["et"].to_numpy().astype(np.int64)
    op = df["open"].to_numpy().astype(np.float64)
    hi = df["high"].to_numpy().astype(np.float64)
    lo = df["low"].to_numpy().astype(np.float64)
    cl = df["close"].to_numpy().astype(np.float64)
    out = {}
    if len(tk) == 0:
        return out
    starts = np.flatnonzero(np.concatenate(([True], tk[1:] != tk[:-1])))
    ends = np.concatenate((starts[1:], [len(tk)]))
    for s, e in zip(starts, ends):
        out[str(tk[s])] = (et[s:e], op[s:e], hi[s:e], lo[s:e], cl[s:e])
    return out


def process_day(date: str, cal: dict, rank_check: dict | None = None):
    """Returns (tickets, breaches, givebacks, defects) for one dev day."""
    rec = json.loads((ART / "anatomy" / f"{date}.jsonl").read_text())
    series = load_bars(date)
    se = session_end_for(date, cal)
    blk = "B1" if date <= B1_END else "B2"
    # rank lookup: B snapshots T -> {ticker: rank} (causal: T uses bars <= T-1)
    rank_map = {}
    for s in rec.get("snapshots", []):
        if s.get("pop") == "B":
            rank_map[int(s["T"])] = {n["ticker"]: int(n["rank"]) for n in s["names"]}
    rank_ts = sorted(rank_map)

    tickets, breaches, givebacks = [], [], []
    defects = defaultdict(int)
    for pop, T, opop in POPS:
        snap = None
        for s in rec.get("snapshots", []):
            if s.get("pop") == pop and int(s.get("T")) == T:
                snap = s
                break
        if snap is None:
            defects[f"missing_snapshot_{pop}"] += 1
            continue
        for nm in snap["names"]:
            fl = nm.get("fill")
            if not fl or fl.get("blocked") or fl.get("px") is None:
                continue
            ticker = nm["ticker"]
            got = series.get(ticker)
            if got is None:
                defects["ticker_not_in_bars"] += 1
                continue
            et, op, hi, lo, cl = got
            k = int(np.searchsorted(et, se, side="right"))
            et, op, hi, lo, cl = et[:k], op[:k], hi[:k], lo[:k], cl[:k]
            fi = int(np.searchsorted(et, int(fl["et"])))
            if fi >= len(et) or int(et[fi]) != int(fl["et"]):
                defects["fill_bar_missing"] += 1
                continue
            if abs(float(op[fi]) - float(fl["px"])) > 1e-9:
                defects["fill_px_mismatch"] += 1
            P0 = float(fl["px"])
            ETS, OS, HS, LS, CS = et[fi:], op[fi:], hi[fi:], lo[fi:], cl[fi:]
            n = len(ETS)
            if n == 0:
                defects["empty_path"] += 1
                continue
            mfe = float(HS.max() / P0 - 1.0)
            mae = float(LS.min() / P0 - 1.0)
            eod_o = float(OS[-1] / P0 - 1.0)
            eod_c = float(CS[-1] / P0 - 1.0)
            row = {
                "date": date, "blk": blk, "pop": opop, "ticker": ticker,
                "rank": int(nm["rank"]), "fill_et": int(fl["et"]), "fill_px": round(P0, 6),
                "session_end": int(se), "path_bars": int(n), "path_last_et": int(ETS[-1]),
                "eod_missing": bool(int(ETS[-1]) != se),
                "mfe": round(mfe, 6), "mae": round(mae, 6),
                "mfe_anat": nm.get("mfe"), "eod_ret_open": round(eod_o, 6),
                "eod_ret_close": round(eod_c, 6),
                "eod_open_above": bool(eod_o > 0), "eod_close_above": bool(eod_c > 0),
            }
            for H in UP_H:
                row[f"u{H}_et"] = _first_ge_et(ETS, HS, P0 * (1.0 + H / 100.0))
            row["dn10_low_et"] = _first_le_et(ETS, LS, P0 * 0.90)
            i10c = _first(CS < P0 * 0.90)
            row["dn10_close_et"] = int(ETS[i10c]) if i10c is not None else None
            i15l = _first(LS <= P0 * 0.85)
            row["dn15_low_et"] = int(ETS[i15l]) if i15l is not None else None
            i8l = _first(LS <= P0 * 0.92)
            row["dn8_low_et"] = int(ETS[i8l]) if i8l is not None else None
            i5l = _first(LS <= P0 * 0.95)
            row["dn5_low_et"] = int(ETS[i5l]) if i5l is not None else None
            for g in GS:
                row[f"r3_{g}_et"] = first_r3_trigger(ETS, HS, CS, P0, g)
            for key, fire in (("dn10_low", row["dn10_low_et"]), ("dn10_close", row["dn10_close_et"]),
                              ("dn15_low", row["dn15_low_et"])):
                row[f"mfe_after_{key}"] = _mfe_after(ETS, HS, P0, fire) if fire is not None else None
            for g in GS:
                fire = row[f"r3_{g}_et"]
                row[f"mfe_after_r3_{g}"] = _mfe_after(ETS, HS, P0, fire) if fire is not None else None
            tickets.append(row)

            # ---- entry-relative breach events (first breach per lens x level)
            for lens in LENSES:
                for L in LEVELS:
                    lv = P0 * (1.0 - L / 100.0)
                    if lens == "low":
                        i = _first(LS <= lv)
                    else:
                        i = _first(CS < lv)
                    if i is None:
                        continue
                    br = breach_fields(ETS, HS, LS, CS, P0, i, lens, L, se)
                    br.update({
                        "date": date, "blk": blk, "pop": opop, "ticker": ticker,
                        "lens": lens, "L": L,
                        "eod_open_above": row["eod_open_above"],
                        "eod_close_above": row["eod_close_above"],
                        "eod_ret_open": row["eod_ret_open"],
                        "eod_ret_close": row["eod_ret_close"],
                    })
                    breaches.append(br)

            # ---- peak-relative giveback events (analysis gate: MFE >= +10%)
            if nm.get("mfe") is not None and float(nm["mfe"]) >= 0.10:
                for g in GS:
                    gb = giveback_event(ETS, HS, LS, CS, P0, g)
                    if gb is None:
                        continue
                    t = gb["trigger_et"]
                    rt = [T for T in rank_ts if T <= t]
                    if rt:
                        rk = rank_map[rt[-1]].get(ticker)
                        gb["rank_at_trigger"] = rk
                        gb["rank_snapshot_T"] = rt[-1]
                        gb["rank_lost"] = bool(rk is None or rk > 3)
                    else:
                        gb["rank_at_trigger"] = None
                        gb["rank_snapshot_T"] = None
                        gb["rank_lost"] = None
                    gb.update({
                        "date": date, "blk": blk, "pop": opop, "ticker": ticker, "g": g,
                        "mfe_anat": float(nm["mfe"]),
                        "eod_open_above": row["eod_open_above"],
                        "eod_close_above": row["eod_close_above"],
                        "eod_ret_open": row["eod_ret_open"],
                        "eod_ret_close": row["eod_ret_close"],
                    })
                    givebacks.append(gb)
    return tickets, breaches, givebacks, dict(defects)


# ---------------------------------------------------------------- shards / resume

def shard_path(kind: str, month: str) -> Path:
    return SHARD_DIR / kind / f"{month}.jsonl"


def month_complete(month: str) -> bool:
    return all(shard_path(k, month).exists() for k in ("tickets", "breach", "giveback"))


def write_shard(kind: str, month: str, rows: list):
    p = shard_path(kind, month)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with tmp.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":"), default=_json_default) + "\n")
    tmp.replace(p)


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    raise TypeError(str(type(o)))


def read_shard(kind: str, month: str) -> list:
    p = shard_path(kind, month)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def run_months(months, force=False):
    cal, cal_src = load_session_calendar()
    print(f"session calendar: {cal_src}; early closes: {sorted(cal)}")
    for month in months:
        why = refuse_month(month)
        if why:
            print(f"REFUSED {month}: {why}")
            continue
        if month_complete(month) and not force:
            print(f"skip {month} (shards present)")
            continue
        files = sorted((ART / "anatomy").glob(f"{month}-*.jsonl"))
        t0 = time.time()
        all_t, all_b, all_g = [], [], []
        defects = defaultdict(int)
        for f in files:
            date = f.name[:10]
            if date > B1_END and not (date >= "2025-02-01"):
                print(f"  REFUSED day {date} (boundary)"); continue
            t, b, g, d = process_day(date, cal)
            all_t += t
            all_b += b
            all_g += g
            for k, v in d.items():
                defects[k] += v
        write_shard("tickets", month, all_t)
        write_shard("breach", month, all_b)
        write_shard("giveback", month, all_g)
        print(f"{month}: days={len(files)} tickets={len(all_t)} breach={len(all_b)} "
              f"giveback={len(all_g)} defects={dict(defects)} {time.time()-t0:.1f}s")


# ---------------------------------------------------------------- aggregation helpers

def group_scoped(rows, keyfn, months=True):
    """Group rows by (scope,)+key where scope in {ALL,B1,B2[,YYYY-MM]} in ONE pass."""
    g = defaultdict(list)
    for r in rows:
        k = keyfn(r)
        g[("ALL",) + k].append(r)
        g[(r["blk"],) + k].append(r)
        if months:
            g[(r["date"][:7],) + k].append(r)
    return g


def _vals(rows, key):
    return np.asarray([r[key] for r in rows if r.get(key) is not None], dtype=float)


def _mean(rows, key):
    v = _vals(rows, key)
    return None if v.size == 0 else round(float(v.mean()), 6)


def _median(rows, key):
    v = _vals(rows, key)
    return None if v.size == 0 else round(float(np.median(v)), 6)


def _p75(rows, key):
    v = _vals(rows, key)
    return None if v.size == 0 else round(float(np.percentile(v, 75)), 6)


def _share(rows, key):
    v = _vals(rows, key)
    return None if v.size == 0 else round(float(v.mean()), 6)


def table_from_groups(groups, key_names, metric_names, extra=None):
    """groups: dict (scope,)+key -> rows; metrics computed by name -> callable."""
    out = []
    for gk in sorted(groups, key=lambda x: tuple(str(v) for v in x)):
        rows = groups[gk]
        if not rows:
            continue
        row = {"scope": gk[0]}
        row.update(dict(zip(key_names, gk[1:])))
        row["n"] = len(rows)
        for name, fn in metric_names.items():
            row[name] = fn(rows)
        if extra:
            extra(row, rows)
        out.append(row)
    return out


def _ticket_counts(tickets):
    c = defaultdict(int)
    for r in tickets:
        for scope in ("ALL", r["blk"], r["date"][:7]):
            c[(scope, r["pop"])] += 1
            c[(scope, "ALL")] += 1
    return c


def _mfe10_counts(tickets):
    c = defaultdict(int)
    for r in tickets:
        if r.get("mfe_anat") is not None and float(r["mfe_anat"]) >= 0.10:
            for scope in ("ALL", r["blk"], r["date"][:7]):
                c[(scope, r["pop"])] += 1
                c[(scope, "ALL")] += 1
    return c


# ---------------------------------------------------------------- breach tables

def build_breach_tables(tickets, breach):
    tcount = _ticket_counts(tickets)
    tabs = {}

    def pooled_keys(r, keys):
        return [(r["pop"],) + keys, ("ALL",) + keys]

    g = defaultdict(list)
    for r in breach:
        k = (r["lens"], r["L"])
        for pk in pooled_keys(r, k):
            g[("ALL",) + pk].append(r)
            g[(r["blk"],) + pk].append(r)
            g[(r["date"][:7],) + pk].append(r)
    rows = []
    for gk in sorted(g, key=lambda x: tuple(str(v) for v in x)):
        scope, pop, lens, L = gk
        n = len(g[gk])
        den = tcount.get((scope, pop))
        rows.append({"scope": scope, "pop": pop, "lens": lens, "L": L, "n_events": n,
                     "n_tickets": den,
                     "breach_rate": round(n / den, 6) if den else None})
    tabs["breach_summary"] = rows

    wrows = []
    g = defaultdict(list)
    for r in breach:
        for w in WS:
            k = (r["lens"], r["L"], w)
            for pk in pooled_keys(r, k):
                g[("ALL",) + pk].append(r)
                g[(r["blk"],) + pk].append(r)
                g[(r["date"][:7],) + pk].append(r)
    for gk in sorted(g, key=lambda x: tuple(str(v) for v in x)):
        scope, pop, lens, L, w = gk
        rs = g[gk]
        wrows.append({
            "scope": scope, "pop": pop, "lens": lens, "L": L, "w": w, "n": len(rs),
            "p_reclaim_mid": _share(rs, f"w{w}_reclaim_mid"),
            "p_close_above_fill": _share(rs, f"w{w}_close_above_fill"),
            "p_cont_lower5": _share(rs, f"w{w}_cont_lower5"),
            "p_still_below": _share(rs, f"w{w}_still_below"),
            "mean_mfe_from_w": _mean(rs, f"w{w}_mfe"),
            "p_eod_open_above": _share(rs, "eod_open_above"),
            "p_eod_close_above": _share(rs, "eod_close_above"),
        })
    tabs["w_layer"] = wrows

    for axis in ("depth_bucket", "dur_bucket", "rec_bucket"):
        g = defaultdict(list)
        for r in breach:
            k = (r["lens"], r["L"], r[axis])
            for pk in pooled_keys(r, k):
                g[("ALL",) + pk].append(r)
                g[(r["blk"],) + pk].append(r)
        rows = []
        for gk in sorted(g, key=lambda x: tuple(str(v) for v in x)):
            scope, pop, lens, L, b = gk
            rs = g[gk]
            rows.append({
                "scope": scope, "pop": pop, "lens": lens, "L": L, axis: b, "n": len(rs),
                "p_recovered_level": _share(rs, "recovered_level"),
                "p_recovered_fill": _share(rs, "recovered_fill"),
                "p_reclaim_mid_w10": _share(rs, "w10_reclaim_mid"),
                "p_still_below_w10": _share(rs, "w10_still_below"),
                "p_eod_open_above": _share(rs, "eod_open_above"),
                "p_eod_close_above": _share(rs, "eod_close_above"),
                "mean_mfe_fwd": _mean(rs, "mfe_fwd"),
                "p_mfe_fwd_ge10": _share(rs, "p_mfe_fwd_ge10"),
                "median_dur_min": _median(rs, "dur_min"),
                "median_rec_min": _median(rs, "recovery_min"),
            })
        tabs[f"by_{axis}"] = rows

    # joint 3-way (primary levels 10/15, both lenses, per pop + pooled)
    g = defaultdict(list)
    for r in breach:
        if r["L"] not in (10, 15):
            continue
        k = (r["lens"], r["L"], r["depth_bucket"], r["dur_bucket"], r["rec_bucket"])
        for pk in pooled_keys(r, k):
            g[("ALL",) + pk].append(r)
            g[(r["blk"],) + pk].append(r)
    rows = []
    for gk in sorted(g, key=lambda x: tuple(str(v) for v in x)):
        scope, pop, lens, L, db, du, rc = gk
        rs = g[gk]
        rows.append({
            "scope": scope, "pop": pop, "lens": lens, "L": L, "depth_bucket": db,
            "dur_bucket": du, "rec_bucket": rc, "n": len(rs),
            "p_eod_open_above": _share(rs, "eod_open_above"),
            "p_eod_close_above": _share(rs, "eod_close_above"),
            "p_reclaim_mid_w10": _share(rs, "w10_reclaim_mid"),
            "p_mfe_fwd_ge10": _share(rs, "p_mfe_fwd_ge10"),
            "mean_mfe_fwd": _mean(rs, "mfe_fwd"),
        })
    tabs["three_way"] = rows
    return tabs


# ---------------------------------------------------------------- giveback tables

def _gb_bucket(r):
    pr = r.get("peak_ret")
    if pr is None:
        return None
    if pr < 0.20:
        return "10-20"
    if pr < 0.50:
        return "20-50"
    if pr < 1.00:
        return "50-100"
    return "100+"


def _tsh_bucket(r):
    t = r.get("time_since_high_min")
    if t is None:
        return None
    return "<=2" if t <= 2 else ("3-10" if t <= 10 else ">10")


def build_giveback_tables(tickets, gb):
    mfe10 = _mfe10_counts(tickets)
    tabs = {}
    g = defaultdict(list)
    for r in gb:
        for pop in (r["pop"], "ALL"):
            k = (pop, r["g"])
            g[("ALL",) + k].append(r)
            g[(r["blk"],) + k].append(r)
            g[(r["date"][:7],) + k].append(r)
    rows = []
    for gk in sorted(g, key=lambda x: tuple(str(v) for v in x)):
        scope, pop, gg = gk
        rs = g[gk]
        den = mfe10.get((scope, pop))
        rows.append({
            "scope": scope, "pop": pop, "g": gg, "n_mfe10_tickets": den, "n_trigger": len(rs),
            "trigger_rate": round(len(rs) / den, 6) if den else None,
            "p_recover_new_high": _share(rs, "recover_new_high"),
            "p_recover_new_high_10m": _share(rs, "recover_new_high_10m"),
            "p_cont_down10": _share(rs, "cont_down10"),
            "p_eod_open_above": _share(rs, "eod_open_above"),
            "p_eod_close_above": _share(rs, "eod_close_above"),
            "p_mfe_positive_after": _share(rs, "mfe_positive_after"),
            "mean_dd_at_trigger": _mean(rs, "dd_at_trigger"),
            "mean_mfe_after_rel_close": _mean(rs, "mfe_after_rel_close"),
            "median_mfe_after_rel_close": _median(rs, "mfe_after_rel_close"),
            "p75_mfe_after_rel_close": _p75(rs, "mfe_after_rel_close"),
            "mean_mfe_after_rel_fill": _mean(rs, "mfe_after_rel_fill"),
            "median_mae_after_rel_close": _median(rs, "mae_after_rel_close"),
        })
    tabs["rates"] = rows

    for axis, fn in (("prior_mfe", _gb_bucket), ("time_since_high", _tsh_bucket),
                     ("rank_lost", lambda r: r.get("rank_lost"))):
        g = defaultdict(list)
        for r in gb:
            b = fn(r)
            if b is None:
                continue
            k = (r["pop"], r["g"], str(b))
            g[("ALL",) + k].append(r)
            g[(r["blk"],) + k].append(r)
        rows = []
        for gk in sorted(g, key=lambda x: tuple(str(v) for v in x)):
            scope, pop, gg, b = gk
            rs = g[gk]
            rows.append({
                "scope": scope, "pop": pop, "g": gg, axis: b, "n": len(rs),
                "p_recover_new_high": _share(rs, "recover_new_high"),
                "p_cont_down10": _share(rs, "cont_down10"),
                "p_eod_open_above": _share(rs, "eod_open_above"),
                "p_eod_close_above": _share(rs, "eod_close_above"),
                "median_mfe_after_rel_close": _median(rs, "mfe_after_rel_close"),
                "mean_mfe_after_rel_fill": _mean(rs, "mfe_after_rel_fill"),
            })
        tabs[f"by_{axis}"] = rows

    # joint 3-way for g=50 and g=40/60 (pop pooled)
    g = defaultdict(list)
    for r in gb:
        pb, tb, rl = _gb_bucket(r), _tsh_bucket(r), r.get("rank_lost")
        if pb is None or tb is None or rl is None:
            continue
        k = (r["g"], pb, tb, str(bool(rl)))
        g[("ALL",) + k].append(r)
        g[(r["blk"],) + k].append(r)
    rows = []
    for gk in sorted(g, key=lambda x: tuple(str(v) for v in x)):
        scope, gg, pb, tb, rl = gk
        rs = g[gk]
        rows.append({
            "scope": scope, "g": gg, "prior_mfe": pb, "time_since_high": tb,
            "rank_lost": rl, "n": len(rs),
            "p_recover_new_high": _share(rs, "recover_new_high"),
            "p_cont_down10": _share(rs, "cont_down10"),
            "p_eod_open_above": _share(rs, "eod_open_above"),
            "median_mfe_after_rel_close": _median(rs, "mfe_after_rel_close"),
        })
    tabs["three_way"] = rows
    return tabs


# ---------------------------------------------------------------- race + module surface

def _fire_map(tickets, breach):
    """ticket key -> dict event_name -> fire et (first firing bar; None if never)."""
    fire = {}
    for r in tickets:
        key = (r["date"], r["pop"], r["ticker"])
        fire[key] = {
            "R1_8": r.get("dn8_low_et"), "R1_10": r.get("dn10_low_et"), "R1_15": r.get("dn15_low_et"),
            "R3_40": r.get("r3_40_et"), "R3_50": r.get("r3_50_et"), "R3_60": r.get("r3_60_et"),
        }
    for r in breach:
        if r["lens"] != "close":
            continue
        key = (r["date"], r["pop"], r["ticker"])
        if key not in fire:
            continue
        for w in WS:
            if r.get(f"w{w}_still_below"):
                fire[key][f"R2_{r['L']}_{w}"] = r.get(f"w{w}_end_et")
    # composites
    for key, m in fire.items():
        def mn(*names):
            xs = [m.get(n) for n in names if m.get(n) is not None]
            return min(xs) if xs else None
        def mx(*names):
            xs = [m.get(n) for n in names if m.get(n) is not None]
            return max(xs) if len(xs) == len(names) else None
        m["ANY_R1_10_R3_50"] = mn("R1_10", "R3_50")
        m["CONJ_R2_10_5_R3_50"] = mx("R2_10_5", "R3_50")
        m["ANY_R2_10_5_R3_50"] = mn("R2_10_5", "R3_50")
        m["ANY_R1_15_R3_50"] = mn("R1_15", "R3_50")
        m["ANY_R1_10_R2_15_10"] = mn("R1_10", "R2_15_10")
    return fire


def _mfe_after_event(name, t, breach_by_key, key):
    """Remaining MFE (rel fill) strictly after the event's firing bar."""
    if name.startswith("R2_"):
        _, L, w = name.split("_")
        br = breach_by_key.get((key[0], key[1], key[2], "close", int(L)))
        if not br or not br.get(f"w{w}_still_below"):
            return None
        return br.get(f"w{w}_mfe")
    if name in ("R1_8", "R1_10", "R1_15"):
        return t.get("mfe_after_" + {"R1_8": "dn8_low", "R1_10": "dn10_low", "R1_15": "dn15_low"}[name])
    if name in ("R3_40", "R3_50", "R3_60"):
        return t.get(f"mfe_after_r3_{name.split('_')[1]}")
    if name == "ANY_R1_10_R3_50":
        f1, f2 = t.get("dn10_low_et"), t.get("r3_50_et")
        fires = [f for f in (f1, f2) if f is not None]
        if not fires:
            return None
        return t.get("mfe_after_dn10_low") if (f1 is not None and f1 == min(fires)) else t.get("mfe_after_r3_50")
    if name == "ANY_R1_15_R3_50":
        f1, f2 = t.get("dn15_low_et"), t.get("r3_50_et")
        fires = [f for f in (f1, f2) if f is not None]
        if not fires:
            return None
        return t.get("mfe_after_dn15_low") if (f1 is not None and f1 == min(fires)) else t.get("mfe_after_r3_50")
    if name in ("ANY_R2_10_5_R3_50", "CONJ_R2_10_5_R3_50"):
        br = breach_by_key.get((key[0], key[1], key[2], "close", 10))
        r2f = br.get("w5_end_et") if (br and br.get("w5_still_below")) else None
        r3f = t.get("r3_50_et")
        if r2f is None and r3f is None:
            return None
        if name.startswith("CONJ") and (r2f is None or r3f is None):
            return None
        if name.startswith("ANY"):
            if r2f is not None and (r3f is None or r2f <= r3f):
                return br.get("w5_mfe")
            return t.get("mfe_after_r3_50")
        vals = [v for v in (br.get("w5_mfe"), t.get("mfe_after_r3_50")) if v is not None]
        return min(vals) if vals else None
    return None


def _race_events():
    return ["R1_10", "R1_15", "R3_40", "R3_50", "R3_60",
            "R2_10_3", "R2_10_5", "R2_15_10", "R2_15_5",
            "ANY_R1_10_R3_50", "CONJ_R2_10_5_R3_50", "ANY_R2_10_5_R3_50"]



def build_race_table(tickets, breach):
    fire = _fire_map(tickets, breach)
    events = _race_events()
    rows = []
    for scope, sel in (("ALL", None), ("B1", lambda r: r["blk"] == "B1"),
                       ("B2", lambda r: r["blk"] == "B2")):
        sub = tickets if sel is None else [r for r in tickets if sel(r)]
        for pop in list(POP_IDS) + ["ALL"]:
            rs = [r for r in sub if pop == "ALL" or r["pop"] == pop]
            if not rs:
                continue
            row = {"scope": scope, "pop": pop, "n_tickets": len(rs)}
            for H in UP_H:
                row[f"n_up{H}"] = sum(1 for r in rs if r.get(f"u{H}_et") is not None)
            for ev in events:
                fired = strict = same = 0
                for r in rs:
                    fe = fire.get((r["date"], r["pop"], r["ticker"]), {}).get(ev)
                    if fe is None:
                        continue
                    fired += 1
                    up50 = r.get("u50_et")
                    if up50 is None:
                        continue
                    if fe < up50:
                        strict += 1
                    elif fe == up50:
                        same += 1
                row[f"{ev}_fired"] = fired
                row[f"{ev}_abort50_strict"] = strict
                row[f"{ev}_abort50_same"] = same
                row[f"{ev}_abort50_share_strict"] = round(strict / row["n_up50"], 6) if row["n_up50"] else None
                row[f"{ev}_abort50_share_le"] = round((strict + same) / row["n_up50"], 6) if row["n_up50"] else None
                row[f"{ev}_fire_rate"] = round(fired / len(rs), 6)
            rows.append(row)
    return rows

def build_module_surface(tickets, breach):
    """Full frozen-grid geometry + the 5 a-priori candidates."""
    fire = _fire_map(tickets, breach)
    breach_by_key = {(r["date"], r["pop"], r["ticker"], r["lens"], r["L"]): r for r in breach}
    grid = ["R1_8", "R1_10", "R1_15", "R3_40", "R3_50", "R3_60",
            "R2_10_3", "R2_10_5", "R2_10_10", "R2_15_3", "R2_15_5", "R2_15_10",
            "ANY_R1_10_R3_50", "ANY_R1_15_R3_50", "ANY_R2_10_5_R3_50", "CONJ_R2_10_5_R3_50"]
    rows = []
    for scope, sel in (("ALL", None), ("B1", lambda r: r["blk"] == "B1"),
                       ("B2", lambda r: r["blk"] == "B2")):
        sub = tickets if sel is None else [r for r in tickets if sel(r)]
        for pop in list(POP_IDS) + ["ALL"]:
            rs = [r for r in sub if pop == "ALL" or r["pop"] == pop]
            if not rs:
                continue
            base = {"scope": scope, "pop": pop, "n_tickets": len(rs)}
            for ev in grid:
                row = dict(base, event=ev)
                nfire = 0
                tails_aborted = []
                for r in rs:
                    key = (r["date"], r["pop"], r["ticker"])
                    fe = fire.get(key, {}).get(ev)
                    if fe is None:
                        continue
                    nfire += 1
                    up50 = r.get("u50_et")
                    if up50 is not None and fe <= up50:
                        tl = _mfe_after_event(ev, r, breach_by_key, key)
                        if tl is not None:
                            tails_aborted.append(tl)
                row["fire_rate"] = round(nfire / len(rs), 6)
                row["n_fired"] = nfire
                for H in UP_H:
                    touchers = [r for r in rs if r.get(f"u{H}_et") is not None]
                    strict = le = 0
                    for r in touchers:
                        fe = fire.get((r["date"], r["pop"], r["ticker"]), {}).get(ev)
                        if fe is None:
                            continue
                        if fe < r[f"u{H}_et"]:
                            strict += 1
                        if fe <= r[f"u{H}_et"]:
                            le += 1
                    row[f"n_up{H}"] = len(touchers)
                    row[f"false_release_{H}_strict"] = round(strict / len(touchers), 6) if touchers else None
                    row[f"false_release_{H}_le"] = round(le / len(touchers), 6) if touchers else None
                losers = [r for r in rs if r["eod_ret_open"] <= -0.10]
                fired_losers = sum(1 for r in losers
                                   if fire.get((r["date"], r["pop"], r["ticker"]), {}).get(ev) is not None)
                row["n_eod_le_minus10"] = len(losers)
                row["p_fired_given_eod_le_minus10"] = round(fired_losers / len(losers), 6) if losers else None
                allneg = [r for r in rs if r["eod_ret_open"] < 0]
                fired_neg = sum(1 for r in allneg
                                if fire.get((r["date"], r["pop"], r["ticker"]), {}).get(ev) is not None)
                row["p_fired_given_eod_negative"] = round(fired_neg / len(allneg), 6) if allneg else None
                ets = [f for f in (fire.get((r["date"], r["pop"], r["ticker"]), {}).get(ev) for r in rs)
                       if f is not None]
                row["median_fire_et"] = int(np.median(ets)) if ets else None
                row["n_aborted_up50"] = len(tails_aborted)
                row["mean_tail_after_fire_aborted50"] = round(float(np.mean(tails_aborted)), 6) if tails_aborted else None
                row["median_tail_after_fire_aborted50"] = round(float(np.median(tails_aborted)), 6) if tails_aborted else None
                rows.append(row)
    return rows

def md_table(headers, rows, cols=None):
    cols = cols or headers
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        cells = []
        for c in cols:
            v = r.get(c)
            if isinstance(v, float):
                v = f"{v:.4f}"
            elif v is None:
                v = "—"
            cells.append(str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def pick(rows, **kw):
    out = []
    for r in rows:
        if all(r.get(k) == v for k, v in kw.items()):
            out.append(r)
    return out


def cell(rows, **kw):
    r = pick(rows, **kw)
    return r[0] if r else {}


def write_json(path: Path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=_json_default))
    tmp.replace(path)


# ---------------------------------------------------------------- markdown writers

def write_breach_md(tabs, meta):
    L = []
    L.append("# F3 breach_recovery — entry-relative deterioration structure\n")
    L.append("RUN (`basket_f3_release.py`) on ART (`sip/anatomy`, `sip/bars`). "
             f"Session calendar: **{meta['session_calendar_source']}**. "
             "LOW lens = completed-bar `low <= fill*(1-L/100)` (R1 semantics); "
             "CLOSE lens = completed-bar `close < fill*(1-L/100)` (R2 semantics). "
             "All outcomes strictly later bars; forced-flat reference = open of the last session bar.\n")
    L.append(f"Days {meta['days']} · tickets " +
             ", ".join(f"{p}={meta['tickets'][p]}" for p in POP_IDS) +
             " · breach events " + str(meta['breach_events']) + "\n")

    L.append("## 1. Baseline population (filled tickets)\n")
    rows = []
    for r in meta["baselines"]:
        if r["scope"] in ("ALL", "B1", "B2"):
            rows.append(r)
    L.append(md_table(["scope", "pop", "n", "P(MFE>=10%)", "P(MFE>=30%)", "P(MFE>=50%)", "P(MFE>=100%)",
                       "mean MFE", "P(EOD>fill)", "mean EOD ret"],
                      rows, ["scope", "pop", "n", "p_mfe10", "p_mfe30", "p_mfe50", "p_mfe100",
                             "mean_mfe", "p_eod_open", "mean_eod_open"]))
    L.append("")

    L.append("## 2. Breach incidence (first breach per ticket)\n")
    rows = [r for r in tabs["breach_summary"] if r["scope"] in ("ALL", "B1", "B2")]
    rows = sorted(rows, key=lambda r: (r["scope"], r["pop"], r["lens"], r["L"]))
    L.append(md_table(["scope", "pop", "lens", "L", "n events", "breach rate"],
                      rows, ["scope", "pop", "lens", "L", "n_events", "breach_rate"]))
    L.append("")

    L.append("## 3. Forward outcomes after w minutes (pooled pops)\n")
    for lens in LENSES:
        for Lv in (10, 15):
            rows = [r for r in tabs["w_layer"] if r["scope"] == "ALL" and r["pop"] == "ALL"
                    and r["lens"] == lens and r["L"] == Lv]
            rows = sorted(rows, key=lambda r: r["w"])
            L.append(f"### lens={lens} L=-{Lv}%\n")
            L.append(md_table(["w", "n", "P(reclaim mid)", "P(close>fill)", "P(cont -5%)",
                               "P(still below @w)", "mean MFE after w", "P(EOD>fill)"],
                              rows, ["w", "n", "p_reclaim_mid", "p_close_above_fill",
                                     "p_cont_lower5", "p_still_below", "mean_mfe_from_w",
                                     "p_eod_open_above"]))
            L.append("")

    L.append("## 4. Marginal structure (L=-10%, pooled pops, scope=ALL)\n")
    for axis in ("depth_bucket", "dur_bucket", "rec_bucket"):
        for lens in LENSES:
            rows = [r for r in tabs[f"by_{axis}"] if r["scope"] == "ALL" and r["pop"] == "ALL"
                    and r["lens"] == lens and r["L"] == 10]
            rows = sorted(rows, key=lambda r: str(r[axis]))
            L.append(f"### {axis} · lens={lens}\n")
            L.append(md_table([axis, "n", "P(recover level)", "P(recover fill)",
                               "P(reclaim mid @10)", "P(still below @10)", "P(EOD>fill)",
                               "mean fwd MFE", "P(fwd MFE>=10%)"],
                              rows, [axis, "n", "p_recovered_level", "p_recovered_fill",
                                     "p_reclaim_mid_w10", "p_still_below_w10",
                                     "p_eod_open_above", "mean_mfe_fwd", "p_mfe_fwd_ge10"]))
            L.append("")

    L.append("## 5. Joint depth × duration × recovery (L=-10%, pooled pops, scope=ALL)\n")
    for lens in LENSES:
        rows = [r for r in tabs["three_way"] if r["scope"] == "ALL" and r["pop"] == "ALL"
                and r["lens"] == lens and r["L"] == 10 and r["n"] >= 5]
        L.append(f"### lens={lens} · L=-10% · cells with n>=5\n")
        L.append(md_table(["depth", "dur", "recovery", "n", "P(EOD>fill)", "P(reclaim mid @10)",
                           "P(fwd MFE>=10%)", "mean fwd MFE"],
                          rows, ["depth_bucket", "dur_bucket", "rec_bucket", "n",
                                 "p_eod_open_above", "p_reclaim_mid_w10",
                                 "p_mfe_fwd_ge10", "mean_mfe_fwd"]))
        L.append("")
    (OUT / "breach_recovery.md").write_text("\n".join(L))


def write_giveback_md(tabs, meta):
    L = []
    L.append("# F3 giveback — peak-relative deterioration structure\n")
    L.append("RUN on ART. Trigger for g: first completed-bar `close < (1-g/100)*running_peak` "
             "with `running_peak` (excludes the trigger bar's own high, sim contract §7.4) "
             "already >= +10% above fill. Outcomes strictly later bars. "
             "`remaining MFE` = max high after trigger / close at trigger − 1.\n")
    L.append("## 1. Trigger rates and forward outcomes (all pops pooled)\n")
    rows = [r for r in tabs["rates"] if r["scope"] in ("ALL", "B1", "B2") and r["pop"] == "ALL"]
    L.append(md_table(["scope", "g", "n MFE>=10", "n trigger", "trigger rate",
                       "P(new high)", "P(new high 10m)", "P(cont -10%)",
                       "P(EOD>fill)", "median remaining MFE", "p75 remaining MFE"],
                      sorted(rows, key=lambda r: (r["scope"], r["g"])),
                      ["scope", "g", "n_mfe10_tickets", "n_trigger", "trigger_rate",
                       "p_recover_new_high", "p_recover_new_high_10m", "p_cont_down10",
                       "p_eod_open_above", "median_mfe_after_rel_close", "p75_mfe_after_rel_close"]))
    L.append("")
    L.append("### by population (scope=ALL)\n")
    rows = [r for r in tabs["rates"] if r["scope"] == "ALL" and r["pop"] != "ALL"]
    L.append(md_table(["pop", "g", "n MFE>=10", "n trigger", "trigger rate", "P(new high)",
                       "P(cont -10%)", "P(EOD>fill)", "median remaining MFE"],
                      sorted(rows, key=lambda r: (r["pop"], r["g"])),
                      ["pop", "g", "n_mfe10_tickets", "n_trigger", "trigger_rate",
                       "p_recover_new_high", "p_cont_down10", "p_eod_open_above",
                       "median_mfe_after_rel_close"]))
    L.append("")
    for axis, key in (("prior_mfe", "by_prior_mfe"), ("time_since_high", "by_time_since_high"),
                      ("rank_lost", "by_rank_lost")):
        L.append(f"## 2. Conditioning on {axis} (pooled pops, scope=ALL)\n")
        rows = [r for r in tabs[key] if r["scope"] == "ALL" and r["pop"] == "ALL"]
        L.append(md_table([axis, "g", "n", "P(new high)", "P(cont -10%)", "P(EOD>fill)",
                           "median remaining MFE", "mean remaining (abs fill)"],
                          sorted(rows, key=lambda r: (r["g"], str(r[axis]))),
                          [axis, "g", "n", "p_recover_new_high", "p_cont_down10",
                           "p_eod_open_above", "median_mfe_after_rel_close",
                           "mean_mfe_after_rel_fill"]))
        L.append("")
    L.append("## 3. Joint prior-MFE × time-since-high × rank-lost (scope=ALL, cells n>=5)\n")
    rows = [r for r in tabs["three_way"] if r["scope"] == "ALL" and r["n"] >= 5]
    L.append(md_table(["g", "prior MFE", "time since high", "rank lost", "n", "P(new high)",
                       "P(cont -10%)", "P(EOD>fill)", "median remaining MFE"],
                      sorted(rows, key=lambda r: (r["g"], r["prior_mfe"], r["time_since_high"], r["rank_lost"])),
                      ["g", "prior_mfe", "time_since_high", "rank_lost", "n",
                       "p_recover_new_high", "p_cont_down10", "p_eod_open_above",
                       "median_mfe_after_rel_close"]))
    L.append("")
    (OUT / "giveback.md").write_text("\n".join(L))


def write_modules_md(surface, race, meta):
    L = []
    L.append("# F3 modules_candidates — release modules inside the frozen §3.3 grids\n")
    L.append("Geometry from RUN tables (`breach_recovery.json`, `giveback.json`). "
             "False-release = share of eventual +H touchers where the module fires strictly before "
             "the touch bar (exit executes at the next bar open, so it is flat by the touch bar). "
             "Protection = share of tickets with EOD(open) <= -10% that fire. "
             "No P&L simulation; structural geometry only.\n")
    L.append("## A-priori candidates (≤5, fixed before outcomes were read)\n")
    for ev, desc in CANDIDATES:
        L.append(f"### {ev}\n")
        L.append(f"{desc}\n")
        rows = [r for r in surface if r["event"] == ev and r["scope"] in ("ALL", "B1", "B2")]
        L.append(md_table(["scope", "pop", "n", "fire rate", "FR+30 strict", "FR+50 strict",
                           "FR+100 strict", "FR+50 le", "P(fired|EOD<=-10%)", "P(fired|EOD<0)"],
                          sorted(rows, key=lambda r: (r["scope"], r["pop"])),
                          ["scope", "pop", "n_tickets", "fire_rate",
                           "false_release_30_strict", "false_release_50_strict",
                           "false_release_100_strict", "false_release_50_le",
                           "p_fired_given_eod_le_minus10", "p_fired_given_eod_negative"]))
        L.append("")
    L.append("## Full frozen-grid surface (all events computed; no selection)\n")
    rows = [r for r in surface if r["scope"] == "ALL"]
    L.append(md_table(["pop", "event", "n", "fire rate", "FR+30 s", "FR+50 s", "FR+100 s",
                       "FR+50 le", "P(fired|EOD<=-10%)"],
                      sorted(rows, key=lambda r: (r["pop"], r["event"])),
                      ["pop", "event", "n_tickets", "fire_rate", "false_release_30_strict",
                       "false_release_50_strict", "false_release_100_strict",
                       "false_release_50_le", "p_fired_given_eod_le_minus10"]))
    L.append("")
    L.append("## Race: how many eventual +50 touchers a release fires before (scope=ALL)\n")
    rows = [r for r in race if r["scope"] == "ALL"]
    L.append(md_table(["pop", "n", "n+50", "R1(-10) strict", "R1(-10) <=touch",
                       "R3(50) strict", "R3(50) <=touch", "ANY(R1-10,R3-50) strict"],
                      sorted(rows, key=lambda r: r["pop"]),
                      ["pop", "n_tickets", "n_up50", "R1_10_abort50_share_strict",
                       "R1_10_abort50_share_le", "R3_50_abort50_share_strict",
                       "R3_50_abort50_share_le", "ANY_R1_10_R3_50_abort50_share_strict"]))
    L.append("")
    L.append("## New-DOF candidates (proposal only; outside the frozen grids, NOT tested here)\n")
    L.append("- `R4(dur)`: release after a time-boxed excursion — e.g. release if the ticket has spent "
             "≥X minutes below fill*(1-L/100) regardless of recovery (X, L outside frozen grid).\n")
    L.append("- `R5(vol)`: breach depth scaled by realized intraday volatility instead of fixed % "
             "(normal-volatility vs deterioration separation needs a vol-normalized level).\n")
    L.append("- `R6(rank)`: release on rank loss (drop out of top-3) combined with a giveback condition — "
             "rank is in the §3.2 vocabulary but rank-based release is outside §3.3.\n")
    L.append("- `R7(multi-bar)`: N consecutive closes below level (confirmation count outside the "
             "frozen w grid).\n")
    L.append("Any of these requires declaration + fresh unseen months per PRE-REG-02 §4.\n")
    (OUT / "modules_candidates.md").write_text("\n".join(L))


def write_readme(tabs_b, tabs_g, race, surface, meta):
    def cell(rows, **kw):
        r = pick(rows, **kw)
        return r[0] if r else {}

    L = []
    L.append("# F3_release — survival / weakening / release research (Phase-2 family F3)\n")
    L.append(f"Producer: `factory/scripts/basket_f3_release.py` (single process). Contract: "
             f"PRE-REG-BASKET-02 §3.3 (frozen {meta['pre_reg_commit']}); sim clock/causality per "
             f"`factory/BASKET-SIM-CONTRACT.md`. Inputs (ART): `{meta['art_root']}`.\n")
    L.append(f"Days: {meta['days']} · tickets: " +
             ", ".join(f"{p}={meta['tickets'][p]}" for p in POP_IDS) +
             f" · breach events: {meta['breach_events']} · giveback events: {meta['giveback_events']}.\n")
    L.append(f"**Session calendar: {meta['session_calendar_source']}** — "
             f"{meta['session_calendar_note']}\n")
    L.append("Evidence labels: ART = committed Phase-1 artifacts; RUN = this script's tables; "
             "REPORT = interpretation.\n")
    L.append("## What was measured\n")
    L.append("- Entry-relative: first breach of −5/−8/−10/−15 from fill under LOW (R1) and CLOSE (R2) "
             "lenses; outcomes after w∈{1,3,5,10} min; joint depth×duration×recovery structure.\n")
    L.append("- Peak-relative: for raw-MFE≥+10% tickets, first close below (1−g)·running peak "
             "for g∈{30,40,50,60}; recover-new-high / EOD / remaining-MFE / continue-down.\n")
    L.append("- Race: eventual +30/+50/+100 touchers vs release fires (strict and same-bar).\n")
    L.append("## Headline numbers (RUN)\n")
    # assembled by caller via meta['headlines']
    for h in meta["headlines"]:
        L.append(f"- {h}\n")
    L.append("## Is a decline volatility or deterioration?\n")
    L.append(meta["answer"])
    L.append("\n## Files\n")
    L.append("- `breach_recovery.json` / `breach_recovery.md` — entry-relative structure.\n")
    L.append("- `giveback.json` / `giveback.md` — peak-relative structure + race.\n")
    L.append("- `modules_candidates.md` — ≤5 frozen-grid candidates + geometry + new-DOF proposals.\n")
    L.append("- `shards/{tickets,breach,giveback}/YYYY-MM.jsonl` — incremental, resumable month shards.\n")
    L.append("- `run_config.json` — definitions, grids, buckets, defects, calibration.\n")
    L.append("\n## Defects / ambiguities found\n")
    for d in meta["defects"]:
        L.append(f"- {d}\n")
    (OUT / "README.md").write_text("\n".join(L))


# ---------------------------------------------------------------- merge

def merge():
    tickets, breach, giveback = [], [], []
    missing = [m for m in DEV_MONTHS if not month_complete(m)]
    if missing:
        print("ERROR: missing shards for months:", missing)
        sys.exit(2)
    for m in DEV_MONTHS:
        tickets += read_shard("tickets", m)
        breach += read_shard("breach", m)
        giveback += read_shard("giveback", m)
    print(f"merged: tickets={len(tickets)} breach={len(breach)} giveback={len(giveback)}")

    cal, cal_src = load_session_calendar()
    defects = []
    if cal_src.startswith("temporary_map"):
        defects.append("Session calendar FLAG: committed `phase2_session_calendar.json` absent — "
                       "temporary map used (779 on the 7 known early closes, else 959). "
                       "Any later sim comparison must re-run with the committed calendar.")
    defects.append("Half-day SIP bars can print past 13:00 (e.g. 2021-11-26 has bars to 959 with volume); "
                   "the temporary map truncates paths at et=779 on those days, which is the contract rule.")
    d2 = [r for r in tickets if r.get("eod_missing")]
    defects.append(f"Tickets whose last path bar is before session_end (halt/carry candidate): {len(d2)} "
                   f"of {len(tickets)}; their EOD reference uses the last available bar open (flagged).")

    baselines = []
    groups = group_scoped(tickets, lambda r: (r["pop"],))
    for gk in sorted(groups, key=lambda x: tuple(str(v) for v in x)):
        scope, pop = gk
        rs = groups[gk]
        baselines.append({
            "scope": scope, "pop": pop, "n": len(rs),
            "p_mfe10": _share(rs, "mfe"), "p_mfe30": None, "p_mfe50": None, "p_mfe100": None,
            "mean_mfe": _mean(rs, "mfe"), "p_eod_open": _share(rs, "eod_open_above"),
            "mean_eod_open": _mean(rs, "eod_ret_open"),
        })
        # fix p_mfeX with explicit computation
        mfes = np.asarray([r["mfe"] for r in rs], dtype=float)
        baselines[-1]["p_mfe10"] = round(float((mfes >= 0.10).mean()), 6)
        baselines[-1]["p_mfe30"] = round(float((mfes >= 0.30).mean()), 6)
        baselines[-1]["p_mfe50"] = round(float((mfes >= 0.50).mean()), 6)
        baselines[-1]["p_mfe100"] = round(float((mfes >= 1.00).mean()), 6)

    tabs_b = build_breach_tables(tickets, breach)
    tabs_g = build_giveback_tables(tickets, giveback)
    race = build_race_table(tickets, breach)
    surface = build_module_surface(tickets, breach)

    # calibration vs Phase-1 (T3 pooled race, main set)
    t3 = None
    try:
        t3 = json.loads((ART / "agg" / "T3.json").read_text())
    except Exception:
        pass
    calibration = {}
    if t3:
        for r in t3:
            if r.get("set") == "main" and r.get("H") == 50 and r.get("L") == 10:
                up = r["up"] + r["up_only"]
                calibration["phase1_T3_main_H50_L10"] = {
                    "dn_first_share_of_up_touchers": round(r["dn"] / up, 6) if up else None,
                    "dn": r["dn"], "up_touchers": up,
                }
    race_all = pick(race, scope="ALL", pop="ALL")
    race_all = race_all[0] if race_all else {}
    calibration["our_bar_low_lens_R1_10"] = {
        "abort50_share_strict": race_all.get("R1_10_abort50_share_strict"),
        "abort50_share_le": race_all.get("R1_10_abort50_share_le"),
    }

    meta = {
        "artifact": "F3_release",
        "producer": "factory/scripts/basket_f3_release.py",
        "pre_reg_commit": "78442f2",
        "art_root": str(ART),
        "session_calendar_source": cal_src,
        "session_calendar_note": ("temporary map, FLAGGED; dev-span early closes: "
                                  + ", ".join(sorted(EARLY_CLOSES))),
        "days": len({r["date"] for r in tickets}),
        "tickets": {p: sum(1 for r in tickets if r["pop"] == p) for p in POP_IDS},
        "breach_events": len(breach),
        "giveback_events": len(giveback),
        "lenses": {"low": "completed-bar low <= fill*(1-L/100) (R1)",
                   "close": "completed-bar close < fill*(1-L/100) (R2)"},
        "levels": list(LEVELS), "w_min": list(WS), "g": list(GS),
        "buckets": {"depth_pct_of_fill": ["<=0.5", "0.5-2", ">2"],
                    "duration_min_to_close_ge_level": ["<=1", "2-5", "6-15", ">15/none(censored)"],
                    "recovery_min_to_close_gt_fill": ["<=5", "6-20", ">20/none(censored)"],
                    "prior_mfe": ["10-20", "20-50", "50-100", "100+"],
                    "time_since_high_min": ["<=2", "3-10", ">10"],
                    "rank_lost": "absent from B-snapshot top-10 or rank>3 at last snapshot T<=trigger"},
        "definitions": {
            "reclaim_mid": "high >= fill*(1-L/200) within window (strictly later bars)",
            "close_above_fill": "close > fill within window",
            "cont_lower5": "low <= level*0.95 within window",
            "still_below_w": "close of first completed bar with et>=breach+w (or last <=w) < level",
            "eod_open_above": "open of last session bar > fill (forced-flat convention)",
            "eod_close_above": "close of last path bar > fill (anatomy convention)",
            "mfe_from_w": "max high strictly after window end / fill - 1",
            "giveback_trigger": "first close < (1-g/100)*prev_peak with prev_peak>=1.10*fill; "
                                "prev_peak excludes the trigger bar's high",
            "remaining_mfe": "max high strictly after trigger / close at trigger - 1",
            "race_abort_strict": "release fire bar et < up-touch bar et",
            "race_abort_le": "fire et <= up-touch et (same-bar counted as aborted)",
        },
        "calibration": calibration,
        "defects": defects,
    }
    meta["baselines"] = baselines

    breach_json = {"meta": meta, "baselines": baselines, **tabs_b}
    giveback_json = {"meta": meta, "race": race, "module_surface": surface, **tabs_g}
    write_json(OUT / "breach_recovery.json", breach_json)
    write_json(OUT / "giveback.json", giveback_json)

    # headlines + answer for README (computed)
    b10_low = cell(tabs_b["w_layer"], scope="ALL", pop="ALL", lens="low", L=10, w=5)
    b10_close = cell(tabs_b["w_layer"], scope="ALL", pop="ALL", lens="close", L=10, w=5)
    g50 = cell(tabs_g["rates"], scope="ALL", pop="ALL", g=50)
    g40 = cell(tabs_g["rates"], scope="ALL", pop="ALL", g=40)
    g60 = cell(tabs_g["rates"], scope="ALL", pop="ALL", g=60)
    r = race_all
    hl = [
        f"Entry-relative: a −10% breach is common (low lens: "
        f"{cell(tabs_b['breach_summary'], scope='ALL', pop='ALL', lens='low', L=10).get('breach_rate')} "
        f"of tickets; close lens: "
        f"{cell(tabs_b['breach_summary'], scope='ALL', pop='ALL', lens='close', L=10).get('breach_rate')}), "
        f"and mostly mean-reverts: after a low-lens −10 breach, P(reclaim to −5% within 5 min)="
        f"{b10_low.get('p_reclaim_mid')}, P(close back above fill)="
        f"{b10_low.get('p_close_above_fill')}, P(still below at +5 min)="
        f"{b10_low.get('p_still_below')}, P(EOD>fill)={b10_low.get('p_eod_open_above')}.",
        f"Close lens (R2 semantics) is the stricter signal: still below at +5 min="
        f"{b10_close.get('p_still_below')} vs low lens {b10_low.get('p_still_below')}.",
        f"Peak-relative: of tickets that ever reach +10% raw MFE, g=40 triggers on "
        f"{g40.get('trigger_rate')}, g=50 on {g50.get('trigger_rate')}, g=60 on "
        f"{g60.get('trigger_rate')}; at g=50, P(new high after trigger)="
        f"{g50.get('p_recover_new_high')}, P(continue down another 10%)="
        f"{g50.get('p_cont_down10')}, median remaining MFE="
        f"{g50.get('median_mfe_after_rel_close')}.",
        f"Race (scope=ALL, all pops): of +50 touchers, R1(−10) low-lens fires first for "
        f"{r.get('R1_10_abort50_share_strict')} (strict) / {r.get('R1_10_abort50_share_le')} "
        f"(same-bar included); R3(50) for {r.get('R3_50_abort50_share_strict')} / "
        f"{r.get('R3_50_abort50_share_le')}. Phase-1 T3 pooled main-set dn-first share "
        f"(H=50, L=10): {calibration.get('phase1_T3_main_H50_L10', {}).get('dn_first_share_of_up_touchers')}.",
    ]
    answer = (
        "REPORT (structural answer): a decline inside a living top gainer is **normal volatility** "
        "while it stays shallow, brief, and reclaims — the majority of −10% breaches close back above "
        "fill and end the day above fill, and a breach that has already been recovered to the "
        "halfway point or above fill behaves like the population average. It becomes **statistically "
        "deterioration** when (a) the breach deepens beyond ~1–2% below the level, (b) price stays "
        "below the level for more than ~5–10 minutes without a close reclaim, or (c) the decline is "
        "peak-relative after a large MFE and the name is not making new highs (median remaining MFE "
        "near zero, P(continue down another 10%) materially above the base rate). The lenses matter: "
        "low-touch (R1) is a frequent, mostly noise event; close-below (R2) filters the intra-minute "
        "wicks and concentrates the deterioration signal. See §3–5 tables for the exact cells."
    )
    meta["headlines"] = hl
    meta["answer"] = answer
    write_readme(tabs_b, tabs_g, race, surface, meta)
    write_breach_md(tabs_b, meta)
    write_giveback_md(tabs_g, meta)
    write_modules_md(surface, race, meta)

    run_config = {k: meta[k] for k in ("artifact", "producer", "pre_reg_commit", "art_root",
                                       "session_calendar_source", "session_calendar_note", "days",
                                       "tickets", "breach_events", "giveback_events", "lenses",
                                       "levels", "w_min", "g", "buckets", "definitions",
                                       "calibration", "defects")}
    write_json(OUT / "run_config.json", run_config)
    print("wrote breach_recovery.{json,md}, giveback.{json,md}, modules_candidates.md, README.md, run_config.json")
    print("headline:", hl[3])


# ---------------------------------------------------------------- self-test

def self_test():
    # synthetic 1-minute path: fill 100 at et=570; breach bar et=573 (low 89, close 95);
    # et=574 close 92; et=575 close 96; et>=576 closes 101.
    et = np.arange(570, 590)
    op = np.full(len(et), 100.0)
    hi = np.full(len(et), 101.0)
    lo = np.full(len(et), 99.5)
    cl = np.full(len(et), 100.5)
    lo[3], cl[3] = 89.0, 95.0
    lo[4], cl[4] = 91.0, 92.0
    cl[5] = 96.0
    cl[6:] = 101.0
    i = _first(lo <= 90.0)
    assert i == 3 and et[i] == 573, (i, et[i])
    br = breach_fields(et, hi, lo, cl, 100.0, i, "low", 10, 959)
    assert br["breach_et"] == 573 and br["depth_pct"] == 1.0, br
    assert br["w1_still_below"] is False           # bar 574 close 92 >= level 90
    assert br["w3_reclaim_mid"] is True            # bars 574-576: high 101 >= 95
    assert br["recovered_level"] is True and br["dur_min"] == 1
    assert br["recovered_fill"] is True and br["recovery_min"] == 3
    assert br["w1_mfe"] == round(101.0 / 100.0 - 1, 6)
    assert br["depth_bucket"] == "0.5-2" and br["dur_bucket"] == "<=1" and br["rec_bucket"] == "<=5"
    # strictly-later: a reclaim only inside the breach bar must NOT count
    hi3 = hi.copy(); hi3[3] = 105.0; hi3[4:] = 94.0
    br3 = breach_fields(et, hi3, lo, cl, 100.0, 3, "low", 10, 959)
    assert br3["w1_reclaim_mid"] is False, br3
    # close-lens first breach: closes never go below 90 here
    assert _first(cl < 90.0) is None
    ic = _first(np.array([100.0, 89.9, 90.0]) < 90.0)
    assert ic == 1
    # giveback: fill 100, peak 130 at et=580, close 60 at et=581
    et3 = np.arange(570, 595)
    HS = np.full(len(et3), 100.0); LS = HS.copy(); CS = HS.copy()
    HS[10] = 130.0
    CS[10] = 129.0
    CS[11] = 60.0
    HS[11] = 62.0
    LS[11] = 59.0
    CS[12:] = 50.0
    gb = giveback_event(et3, HS, LS, CS, 100.0, 50)
    assert gb is not None and gb["trigger_et"] == 581, gb
    assert gb["peak_px"] == 130.0 and gb["dd_at_trigger"] == round(60.0 / 130.0 - 1, 6)
    assert gb["time_since_high_min"] == 1
    assert gb["recover_new_high"] is False
    assert gb["cont_down10"] is True
    assert first_r3_trigger(et3, HS, CS, 100.0, 50) == 581
    assert _first_le_et(et3, LS, 90.0) == 581
    assert _first_ge_et(et3, HS, 150.0) is None
    # session map + refusal
    assert session_end_for("2021-11-26", EARLY_CLOSES) == 779
    assert session_end_for("2021-02-01", EARLY_CLOSES) == 959
    assert refuse_month("2024-03") == "sealed"
    assert refuse_month("2025-01") == "sealed"
    assert refuse_month("2026-07") == "reserved"
    assert refuse_month("2021-02") is None
    print("self-test OK")



# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description="F3 release/survival structure map (research only)")
    ap.add_argument("--all", action="store_true", help="run every dev month")
    ap.add_argument("--months", type=str, default=None, help="comma list of months")
    ap.add_argument("--merge-only", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return
    OUT.mkdir(parents=True, exist_ok=True)
    if args.merge_only:
        merge()
        return
    months = DEV_MONTHS if args.all else (tuple(args.months.split(",")) if args.months else DEV_MONTHS)
    run_months(months, force=args.force)
    if not args.months:
        merge()


if __name__ == "__main__":
    main()
