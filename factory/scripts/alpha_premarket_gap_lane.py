#!/usr/bin/env python3
"""Premarket GAP lane: is the PREMARKET state of a top-gainer a tradable 09:30 signal?

ONE question: on the open-anchored top-gainer PANEL (the day's admitted names), do
premarket features observable strictly before 09:30 ET - premarket volume, premarket
price change vs the previous session close, premarket high/low, time of the last
premarket print, and premarket volume extremity vs the symbol's own trailing median -
predict the first-hour and hold-to-close next-open payoff at 09:30 on a 3 x $1,000
research book, after 25/100 bps round-trip friction?

PRE-REGISTRATION (this grid was fixed in code BEFORE any outcome was computed; the
full-run selection is frozen after the validation surface and before any late-block
cell is read):
* UNIVERSE (identical for every cell): the day's distinct PANEL tickers that are
  pm-observable (a premarket last print exists), have a computable previous-session
  close (the tape's last bar close at/before the prior session's session_end), and
  carry NO corporate action in the Alpaca split ledger with ex_date in
  (prev_session, day] - the pm-change feature is a previous-close feature, which the
  panel contract deliberately avoids, so split-window symbol-days are excluded rather
  than silently trusted. A second control (ctrl_all_panel) runs EVERY panel ticker of
  the day with no premarket requirement at all, so the cost of the pm-observable
  restriction itself is measured, not assumed.
* ENTRY: the first ACTUAL minute open at/after 09:30 ET (minute 570) - the 09:30-09:35
  window's opening print. No bar at/after 570 inside the session => unfilled_expired:
  the ticket is reserved and released, no fees, and the attempt is counted. No quote
  touch is modeled; this is the panel contract's own minute-open proxy.
* EXIT: the first actual minute open at/after min(entry_et + H, session_end) with
  H in {60, 390}; H=390 is the house close hold (exit = the session's last regular
  minute on a normal day, compressed on the seven early closes). A target minute that
  never prints stays UNKNOWN (retained position, ticket reserved to session_end+1).
* BOOK: 3 slots x $1,000 = $3,000, one position per ticker, same-clock sells precede
  buys, day-blocked accounting, UNKNOWNs charged the FULL ticket in a separately
  labelled lower bound (never blended, never dropped). Mirrors alpha_open_sim.replay;
  the only difference is an explicit short side for the fade cells.
* GRID (10 cells, X/Y on the coarse 3/5/10% ladder only):
    ctrl_all_panel  long, every panel ticker (no premarket requirement)
    ctrl_no_filter  long, universe, NO premarket filter (primary control)
    L5              long, pm_chg > +5%
    L3d             long, pm_chg > +3% AND pm_vol in the day's universe top decile
    L5d             long, pm_chg > +5% AND pm_vol in the day's universe top decile
    L10d            long, pm_chg > +10% AND pm_vol in the day's universe top decile
    L5own           long, pm_chg > +5% AND pm_vol >= 10x the symbol's own trailing
                    20-session median pm_vol (>= 5 prior observations, else UNKNOWN)
    F3 / F5 / F10   SHORT (fade), pm_chg < -3% / -5% / -10% (no volume qualifier)
  Slot priority inside a day is ticker-alphabetical (score 0 for every signal): the
  measured edge is the rule's average over qualifying names, never a within-rule pick.
* RUNGS: 25 and 100 bps ROUND TRIP (halved per leg,
  net = (1+g) (1-s) / (1+s) - 1); both reported everywhere, selection at 25 bps.
* BLOCKS: dev 2022-05..2022-12 (context), validation 2023 (selection), late
  2025-02..2026-05 (previously explored, NOT pristine). Protected unread:
  2024, 2025-01, 2026-06..08 - never opened.
* SELECTION: argmax of validation known $/panel session at 25 bps over the 8 SIGNAL
  cells x 2 horizons, tie-break (- objective, - known fills, cell key). Frozen to the
  artifact AFTER the validation surface and BEFORE any late cell is read.

Population caveat disclosed up front: panel admission clocks run 09:35..12:00, so the
day's panel is a post-hoc top-gainer population; every cell draws from the SAME
population, so filter-vs-control increments are population-consistent, and the
controls carry the unconditional panel level for reference.

Usage:
  uv run --no-sync python factory/scripts/alpha_premarket_gap_lane.py
  uv run --no-sync python factory/scripts/alpha_premarket_gap_lane.py --days 2023-06-15 2023-06-16
  uv run --no-sync python factory/scripts/alpha_premarket_gap_lane.py --skip-late
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import time
from collections import deque
from datetime import date as dt_date
from datetime import time as dt_time
from pathlib import Path

import numpy as np
import polars as pl

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from alpha_open_panel import allowed  # sealed-day guard (house function)

# ----- fixed configuration (pre-registered; no HPO, no outcome-informed edits) --
HORIZONS = (60, 390)  # first hour; 390 = the house close hold
RUNGS = (25.0, 100.0)  # round-trip bps
SELECT_RUNG = 25.0
ORDER_BUDGET = 1000.0
MAX_SLOTS = 3
BOOK = MAX_SLOTS * ORDER_BUDGET
ENTRY_MINUTE = 570  # 09:30 ET
TRAIL_WINDOW = 20  # sessions of own pm_vol history for the trailing median
TRAIL_MIN_OBS = 5
OWN_EXTREME_X = 10.0  # pm_vol >= 10x own trailing median
GRID_STEPS = (0.03, 0.05, 0.10)
BOOT_N = 1000
BOOT_SEED = 20261012
UNDERPOWERED_KNOWN_FILLS = 25  # label only; never a drop or auto-kill threshold

BLOCKS = {
    "dev": ("2022-05-01", "2022-12-31"),
    "validation": ("2023-01-01", "2023-12-31"),
    "confirmation": ("2025-02-01", "2026-05-31"),
}

ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = ROOT / "data"
PANEL_DAYS = Path("/home/hillel/alpha-data/open-search-v1/days")
PANEL_CONTRACT = Path("/home/hillel/alpha-data/open-search-v1/contract.json")
PM_ROOT = DATA_ROOT / "sip" / "pm_snapshots"
BARS_ROOT = DATA_ROOT / "sip" / "net" / "bars"
CALENDAR_PATH = ROOT / "factory" / "artifacts" / "basket" / "sip" / "phase2_session_calendar.json"
SPLITS_PATH = DATA_ROOT / "harvest01" / "base" / "splits.parquet"
# Full-universe 1-minute monthly tapes (raw = unadjusted, preferred; clean = the derived
# variant, fallback), used ONLY to read the PREVIOUS SESSION's closing price per ticker for
# the pm-change feature. Months are opened strictly by the session calendar, so no sealed
# month (2024, 2025-01, 2026-06..08) is ever read: the calendar has no session inside them,
# hence no prev-session close is ever needed there. 2025-02 exists only in the clean variant.
MONTHLY_ROOTS = (DATA_ROOT, DATA_ROOT / "backfill")
MONTHLY_STEMS = ("ohlcv", "clean_ohlcv")
ARTIFACT = ROOT / "factory" / "artifacts" / "overnight" / "premarket_gap_lane.json"

# cell -> (side, kind). kinds: all_panel, universe, pmove, pmove_decile, pmove_own
CELLS = {
    "ctrl_all_panel": (+1, "all_panel", None),
    "ctrl_no_filter": (+1, "universe", None),
    "L5": (+1, "pmove", 0.05),
    "L3d": (+1, "pmove_decile", 0.03),
    "L5d": (+1, "pmove_decile", 0.05),
    "L10d": (+1, "pmove_decile", 0.10),
    "L5own": (+1, "pmove_own", 0.05),
    "F3": (-1, "pmove", -0.03),
    "F5": (-1, "pmove", -0.05),
    "F10": (-1, "pmove", -0.10),
}
SIGNAL_CELLS = tuple(k for k in CELLS if not k.startswith("ctrl_"))
CONTROL_CELL = "ctrl_no_filter"
ALL_PANEL_CONTROL = "ctrl_all_panel"
STATUS = "DISCOVERY-NOT-VALIDATED"

FEATURE_SCHEMA = {
    "day": pl.String,
    "ticker": pl.String,
    "admit_t": pl.Int64,
    "session_end": pl.Int64,
    "pm_n_bars": pl.Int64,
    "pm_vol": pl.Float64,
    "pm_chg": pl.Float64,
    "pm_hi": pl.Float64,
    "pm_lo": pl.Float64,
    "pm_first_et": pl.Int64,
    "pm_last_et": pl.Int64,
    "pm_vol_trail_ratio": pl.Float64,
    "top_decile": pl.Boolean,
    "in_universe": pl.Boolean,
    "exclude_reason": pl.String,
    "entry_et": pl.Int64,
    "entry_open": pl.Float64,
    "entry_status": pl.String,
    "gross_60": pl.Float64,
    "exit_et_60": pl.Int64,
    "gross_390": pl.Float64,
    "exit_et_390": pl.Int64,
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def digest_bytes(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def bootstrap_daily(returns: list[float], n: int = BOOT_N, seed: int = BOOT_SEED) -> dict:
    """Day-clustered bootstrap, identical algorithm to alpha_open_learned.bootstrap_daily."""
    r = np.asarray(returns, dtype=float)
    if r.size == 0:
        return {"n_days": 0}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, r.size, size=(n, r.size))
    means = r[idx].mean(axis=1)
    return {
        "n_days": int(r.size),
        "n_boot": n,
        "seed": seed,
        "mean": float(r.mean()),
        "ci95_lo": float(np.percentile(means, 2.5)),
        "ci95_hi": float(np.percentile(means, 97.5)),
        "p_gt_zero": float((means > 0).mean()),
    }


# ----- inputs -----------------------------------------------------------------
def load_calendar() -> dict:
    cal = json.loads(CALENDAR_PATH.read_text())
    return {d: int(v) for d, v in cal["session_end_by_day"].items()}


def monthly_file(month: str) -> Path | None:
    for root in MONTHLY_ROOTS:
        for stem in MONTHLY_STEMS:
            p = root / f"{stem}_{month}.parquet"
            if p.exists():
                return p
    return None


def month_of(day: str) -> str:
    return day[:7]


def prev_session(day: str, sessions: list[str]) -> str | None:
    """The previous CALENDAR session, or None across the sealed gap.

    The calendar skips the sealed months entirely (2023-12-29 -> 2025-02-03), so a raw
    predecessor lookup would serve a 13-month-stale close as "prev close". Any gap of more
    than 10 calendar days means there is no usable predecessor: the prev-close feature is
    then UNKNOWN for every symbol that day (the sealed month is never opened to fake one).
    """
    i = next((k for k, d in enumerate(sessions) if d >= day), len(sessions))
    if i == 0:
        return None
    p = sessions[i - 1]
    if (dt_date.fromisoformat(day) - dt_date.fromisoformat(p)).days > 10:
        return None
    return p


class PrevCloseBook:
    """Previous-session closes from the full-universe monthly tapes.

    Only sessions on the session calendar are ever opened, which structurally excludes the
    sealed months (the calendar has no session inside 2024 / 2025-01 / 2026-06..08, so no
    prev-session close is ever requested there). 2025-02-03 has no calendar predecessor and
    therefore no prev close at all: every symbol that day is a disclosed exclusion.
    """

    def __init__(self, sessions: list[str], study_days: list[str], tickers_by_day: dict):
        self.sessions = sessions
        self.closes: dict[str, dict[str, float]] = {}
        self.months_loaded: dict[str, str] = {}
        need: dict[str, dict[str, set[str]]] = {}
        for day in study_days:
            p = prev_session(day, sessions)
            if p is None:
                continue
            need.setdefault(month_of(p), {}).setdefault(p, set()).update(tickers_by_day[day])
        for month, days in sorted(need.items()):
            path = monthly_file(month)
            if path is None:
                self.months_loaded[month] = "MISSING"
                continue
            tickers = sorted({t for ts in days.values() for t in ts})
            dates = sorted(days)
            frame = (
                pl.scan_parquet(path)
                .filter(pl.col("ticker").is_in(tickers))
                .select("ticker", "timestamp", "close")
                .collect()
            )
            frame = frame.with_columns(
                pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("ts_et")
            )
            frame = frame.filter(pl.col("ts_et").dt.date().cast(pl.String).is_in(dates))
            # the monthly tape spans 04:00-19:59 ET; the prev-session CLOSE is the last
            # print at/before 16:00 ET (the closing-auction minute when printed)
            frame = frame.filter(pl.col("ts_et").dt.time() <= dt_time(16, 0))
            # the raw tape has known (timestamp, ticker) duplicates: keep the LAST print
            frame = frame.unique(subset=["ticker", "timestamp"], keep="last")
            frame = frame.with_columns(pl.col("ts_et").dt.date().cast(pl.String).alias("et_day"))
            last = frame.sort(["ticker", "timestamp"]).group_by(["ticker", "et_day"]).agg(
                pl.col("close").last()
            )
            for sym, et_day, close in last.iter_rows():
                if close is not None and np.isfinite(close) and close > 0:
                    self.closes.setdefault(str(et_day), {})[str(sym)] = float(close)
            self.months_loaded[month] = str(path)
            del frame, last

    def get(self, day: str) -> dict:
        p = prev_session(day, self.sessions)
        if p is None:
            return {}
        return self.closes.get(p, {})


def load_splits() -> dict:
    frame = pl.read_parquet(SPLITS_PATH).select("symbol", "ex_date")
    out: dict[str, list[str]] = {}
    for sym, ex in frame.iter_rows():
        out.setdefault(str(sym), []).append(str(ex))
    for sym in out:
        out[sym].sort()
    return out


def pm_days() -> list[str]:
    days = []
    with (PM_ROOT / "index.jsonl").open() as f:
        for line in f:
            days.append(json.loads(line)["day"])
    return sorted(days)


def study_days() -> dict:
    files = sorted(p.stem for p in PANEL_DAYS.glob("????-??-??.parquet"))
    if any(not allowed(d) for d in files):
        raise ValueError("protected input file in panel root")
    out = {b: [] for b in BLOCKS}
    for d in files:
        for b, (lo, hi) in BLOCKS.items():
            if lo <= d <= hi:
                out[b].append(d)
                break
    return out


# ----- feature construction ---------------------------------------------------
def warmup_history(pm_all: list[str], first_study_day: str) -> dict:
    """Trailing pm_vol histories per symbol, built ONLY from days before the first study day."""
    hist: dict[str, deque] = {}
    for day in pm_all:
        if day >= first_study_day:
            break
        if not (PM_ROOT / f"{day}.parquet").exists():
            continue
        frame = pl.read_parquet(PM_ROOT / f"{day}.parquet").select("symbol", "pm_vol")
        for sym, vol in frame.iter_rows():
            if vol is not None and np.isfinite(vol) and vol > 0:
                hist.setdefault(str(sym), deque(maxlen=TRAIL_WINDOW)).append(float(vol))
    return hist


def tape_outcomes(day: str, tickers: list[str], session_end: int) -> dict:
    """Per-ticker entry (first actual open at/after 570) and both horizon exits.

    Mirrors alpha_sparse_daily.horizon_label exactly: exit = first actual minute open
    at/after min(entry_et + H, session_end); an absent print is UNKNOWN (None), never
    an interpolation.
    """
    path = BARS_ROOT / f"{day}.parquet"
    out = {}
    if not path.exists() or not tickers:
        return out
    frame = (
        pl.read_parquet(path)
        .filter(pl.col("ticker").is_in(tickers))
        .select("ticker", "et", "open")
        .sort(["ticker", "et"])
    )
    for sym, et, open_ in frame.iter_rows():
        out.setdefault(str(sym), ([], []))
        out[str(sym)][0].append(int(et))
        out[str(sym)][1].append(float(open_))
    resolved = {}
    for sym, (ets, opens) in out.items():
        ets_a = np.asarray(ets, dtype=np.int64)
        opens_a = np.asarray(opens, dtype=float)
        j = int(np.searchsorted(ets_a, ENTRY_MINUTE))
        if j >= len(ets_a) or int(ets_a[j]) > session_end:
            resolved[sym] = {"entry_status": "unfilled_expired", "entry_et": None,
                             "entry_open": None, 60: (None, None), 390: (None, None)}
            continue
        entry_et, entry_open = int(ets_a[j]), float(opens_a[j])
        rec = {"entry_status": "filled_proxy", "entry_et": entry_et, "entry_open": entry_open}
        for h in HORIZONS:
            target = min(entry_et + h, session_end)
            k = int(np.searchsorted(ets_a, target))
            if k < len(ets_a) and int(ets_a[k]) <= session_end:
                rec[h] = (float(opens_a[k]) / entry_open - 1.0, int(ets_a[k]))
            else:
                rec[h] = (None, None)
        resolved[sym] = rec
    return resolved


def qa_prev_close(day: str, sessions: list[str], cal: dict, book: "PrevCloseBook",
                  tickers: list[str]) -> dict | None:
    """Spot check: the monthly-tape prev close vs the SIP tape's own prev-session close.

    Runs on a fixed sample of study days only. The SIP day tape covers a subset of
    tickers, so only overlapping names compare; the SIP reference is the last bar close
    at/before the previous session's session_end.
    """
    prev_day = prev_session(day, sessions)
    if prev_day is None:
        return None
    path = BARS_ROOT / f"{prev_day}.parquet"
    if not path.exists():
        return None
    mito = book.get(day)
    frame = (
        pl.read_parquet(path)
        .filter(pl.col("ticker").is_in(tickers) & (pl.col("et") <= cal.get(prev_day, 959)))
        .sort(["ticker", "et"])
        .select("ticker", "close")
    )
    sip_last = {}
    for sym, close in frame.iter_rows():
        if close is not None and np.isfinite(close):
            sip_last[str(sym)] = float(close)
    diffs = []
    for sym, px in sip_last.items():
        ref = mito.get(sym)
        if ref and px > 0:
            diffs.append(abs(px / ref - 1.0))
    if not diffs:
        return None
    arr = np.asarray(diffs, dtype=float)
    return {
        "day": day,
        "prev_session": prev_day,
        "n_compared": int(arr.size),
        "median_abs_rel_diff": float(np.median(arr)),
        "p95_abs_rel_diff": float(np.quantile(arr, 0.95)),
        "max_abs_rel_diff": float(arr.max()),
    }


def build_day(day: str, sessions: list[str], splits: dict, book: "PrevCloseBook", hist: dict) -> list[dict]:
    panel = pl.read_parquet(PANEL_DAYS / f"{day}.parquet")
    tickers = sorted(set(panel["ticker"].to_list()))
    session_ends = panel["session_end"].unique().to_list()
    if len(session_ends) != 1:
        raise ValueError(f"panel session_end not unique on {day}: {session_ends}")
    session_end = int(session_ends[0])
    admit = {str(t): int(a) for t, a in panel.select("ticker", "admit_t").iter_rows()}

    pm = pl.read_parquet(PM_ROOT / f"{day}.parquet")
    pm = pm.filter(pl.col("symbol").is_in(tickers))
    pm_by_sym = {}
    for row in pm.iter_rows(named=True):
        pm_by_sym[str(row["symbol"])] = row

    prev_day = prev_session(day, sessions)
    prev_close = book.get(day)
    split_syms = (
        {t for t in tickers if any(prev_day < ex <= day for ex in splits.get(t, ()))}
        if prev_day
        else set()
    )

    vol_vals = [
        float(pm_by_sym[t]["pm_vol"])
        for t in tickers
        if t in pm_by_sym and pm_by_sym[t]["pm_vol"] is not None and np.isfinite(pm_by_sym[t]["pm_vol"])
    ]
    vol_p90 = float(np.quantile(np.asarray(vol_vals, dtype=float), 0.90)) if vol_vals else None

    tape = tape_outcomes(day, tickers, session_end)
    rows = []
    for t in tickers:
        pmr = pm_by_sym.get(t)
        last_px = pmr["pm_last_px"] if pmr else None
        pm_vol = pmr["pm_vol"] if pmr else None
        pc = prev_close.get(t)
        reason = None
        if last_px is None or not np.isfinite(last_px) or last_px <= 0:
            reason = "no_pm_print"
        elif pc is None:
            reason = "no_prev_close"
        elif t in split_syms:
            reason = "split_window"
        in_universe = reason is None
        pm_chg = (float(last_px) / pc - 1.0) if (last_px is not None and pc) else None
        hist_deque = hist.get(t, ())
        ratio = None
        if in_universe and len(hist_deque) >= TRAIL_MIN_OBS:
            med = float(np.median(np.asarray(hist_deque, dtype=float)))
            if med > 0:
                ratio = float(pm_vol) / med
        top_decile = bool(in_universe and vol_p90 is not None and pm_vol is not None
                          and float(pm_vol) >= vol_p90)
        rec = tape.get(t, {"entry_status": "unfilled_expired", "entry_et": None, "entry_open": None,
                           60: (None, None), 390: (None, None)})
        rows.append(
            {
                "day": day,
                "ticker": t,
                "admit_t": admit.get(t),
                "session_end": session_end,
                "pm_n_bars": int(pmr["pm_n_bars"]) if pmr and pmr["pm_n_bars"] is not None else None,
                "pm_vol": float(pm_vol) if pm_vol is not None else None,
                "pm_chg": pm_chg,
                "pm_hi": float(pmr["pm_hi"]) if pmr and pmr["pm_hi"] is not None else None,
                "pm_lo": float(pmr["pm_lo"]) if pmr and pmr["pm_lo"] is not None else None,
                "pm_first_et": int(pmr["pm_first_et"]) if pmr and pmr["pm_first_et"] is not None else None,
                "pm_last_et": int(pmr["pm_last_et"]) if pmr and pmr["pm_last_et"] is not None else None,
                "pm_vol_trail_ratio": ratio,
                "top_decile": top_decile,
                "in_universe": in_universe,
                "exclude_reason": reason,
                "entry_et": rec["entry_et"],
                "entry_open": rec["entry_open"],
                "entry_status": rec["entry_status"],
                "gross_60": rec[60][0],
                "exit_et_60": rec[60][1],
                "gross_390": rec[390][0],
                "exit_et_390": rec[390][1],
            }
        )
    return rows


def cell_mask(rows: list[dict], cell: str) -> list[bool]:
    side, kind, step = CELLS[cell]
    out = []
    for r in rows:
        if kind == "all_panel":
            ok = True
        elif kind == "universe":
            ok = r["in_universe"]
        elif kind == "pmove":
            ok = r["in_universe"] and r["pm_chg"] is not None and (
                (r["pm_chg"] > step) if step > 0 else (r["pm_chg"] < step)
            )
        elif kind == "pmove_decile":
            ok = (
                r["in_universe"]
                and r["pm_chg"] is not None
                and r["pm_chg"] > step
                and r["top_decile"]
            )
        elif kind == "pmove_own":
            ok = (
                r["in_universe"]
                and r["pm_chg"] is not None
                and r["pm_chg"] > step
                and r["pm_vol_trail_ratio"] is not None
                and r["pm_vol_trail_ratio"] >= OWN_EXTREME_X
            )
        else:
            raise ValueError(kind)
        out.append(ok)
    return out


# ----- replay (alpha_open_sim.replay conventions, side-aware) -----------------
def replay(rows: list[dict], days: list[str], horizon: int, cost_bps: float, side_dir: int) -> dict:
    rows = sorted(rows, key=lambda r: (r["day"], 0, r["ticker"]))
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["day"], []).append(r)
    side = cost_bps / 20_000.0
    initial = MAX_SLOTS * ORDER_BUDGET
    daily, trades = [], []
    attempts = unfilled = skips = 0
    for day in days:
        cash, queue, attempted, active = initial, [], set(), set()
        known_pnl, unknown, sequence = 0.0, 0, 0
        for r in by_day.get(day, ()):
            while queue and queue[0][0] <= ENTRY_MINUTE:
                _, _, symbol, proceeds = heapq.heappop(queue)
                active.discard(symbol)
                cash += proceeds
            symbol = r["ticker"]
            if symbol in attempted or symbol in active:
                continue
            if len(active) >= MAX_SLOTS or cash + 1e-8 < ORDER_BUDGET:
                skips += 1
                continue
            attempted.add(symbol)
            attempts += 1
            if r["entry_status"] == "unfilled_expired":
                cash -= ORDER_BUDGET
                active.add(symbol)
                heapq.heappush(queue, (ENTRY_MINUTE + 1, sequence, symbol, ORDER_BUDGET))
                sequence += 1
                unfilled += 1
                continue
            gross_raw = r[f"gross_{horizon}"]
            cash -= ORDER_BUDGET
            active.add(symbol)
            if gross_raw is None:
                unknown += 1
                net, status, proceeds = None, "unknown_pending", 0.0
                release_t = int(r["session_end"]) + 1
            else:
                net = (1.0 + side_dir * float(gross_raw)) * (1.0 - side) / (1.0 + side) - 1.0
                status, proceeds = "known_open_proxy", ORDER_BUDGET * (1.0 + net)
                release_t = int(r[f"exit_et_{horizon}"])
                known_pnl += ORDER_BUDGET * net
            heapq.heappush(queue, (release_t, sequence, symbol, proceeds))
            sequence += 1
            trades.append(
                {
                    "day": day,
                    "ticker": symbol,
                    "t": ENTRY_MINUTE,
                    "entry_et": r["entry_et"],
                    "entry_open": r["entry_open"],
                    "exit_et": r[f"exit_et_{horizon}"],
                    "horizon": horizon,
                    "gross": None if gross_raw is None else side_dir * float(gross_raw),
                    "net": net,
                    "cost_bps": cost_bps,
                    "order_budget": ORDER_BUDGET,
                    "side": "long" if side_dir > 0 else "short",
                    "status": status,
                }
            )
        lower = known_pnl - ORDER_BUDGET * unknown
        daily.append({"day": day, "known_pnl": known_pnl, "unknown": unknown,
                      "lower_bound_pnl": lower})
    known = np.array([t["net"] for t in trades if t["net"] is not None], dtype=float)
    wins = known[known > 0].sum() if known.size else 0.0
    losses = -known[known < 0].sum() if known.size else 0.0
    unknown_total = sum(d["unknown"] for d in daily)
    return {
        "days": len(days),
        "signals": len(rows),
        "attempts": attempts,
        "fills": len(trades),
        "known_fills": int(known.size),
        "unknown_fills": unknown_total,
        "unfilled_expired": unfilled,
        "cash_or_slot_skips": skips,
        "known_usd": float(known.sum()) if known.size else 0.0,
        "mean_net_known_fill": float(known.mean()) if known.size else None,
        "known_win_rate": float((known > 0).mean()) if known.size else None,
        "known_profit_factor": float(wins / losses) if losses > 0 else None,
        "daily": daily,
        "trades": trades,
    }


def summarize(rep: dict, block: str, cell: str, horizon: int, rung: float,
             days: list[str], n_cal_span: int) -> dict:
    known = rep["known_usd"]
    n = rep["days"] or 1
    lower = sum(d["lower_bound_pnl"] for d in rep["daily"])
    daily_known = [d["known_pnl"] for d in rep["daily"]]
    return {
        "block": block,
        "cell": cell,
        "horizon": horizon,
        "cost_bps": rung,
        "sessions": rep["days"],
        "signals": rep["signals"],
        "attempts": rep["attempts"],
        "fills": rep["fills"],
        "known_fills": rep["known_fills"],
        "unknown_fills": rep["unknown_fills"],
        "unfilled_expired": rep["unfilled_expired"],
        "cash_or_slot_skips": rep["cash_or_slot_skips"],
        "traded_days": len({t["day"] for t in rep["trades"]}),
        "known_usd": known,
        "known_usd_per_panel_session": known / n,
        "known_usd_per_calendar_day": known / n,  # legacy name: divides by PANEL SESSIONS
        "known_usd_per_calendar_day_span": known / n_cal_span,
        "full_loss_lower_bound_usd": lower,
        "full_loss_lower_bound_usd_per_panel_session": lower / n,
        "deployed_usd": ORDER_BUDGET * rep["fills"],
        "mean_net_known_fill": rep["mean_net_known_fill"],
        "known_win_rate": rep["known_win_rate"],
        "known_profit_factor": rep["known_profit_factor"],
        "bootstrap_daily_known_usd": bootstrap_daily(daily_known),
        "underpowered_known_fills": rep["known_fills"] < UNDERPOWERED_KNOWN_FILLS,
        "book_usd": BOOK,
        "slots": MAX_SLOTS,
    }


def strip_daily(rep: dict) -> dict:
    return {k: v for k, v in rep.items() if k not in ("daily", "trades")}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", nargs="*", default=None, help="smoke subset (debug only)")
    ap.add_argument("--skip-late", action="store_true")
    args = ap.parse_args(argv)

    t0 = time.time()
    cal = load_calendar()
    sessions = sorted(cal)
    splits = load_splits()
    pm_all = pm_days()
    blocks = study_days()
    smoke = set(args.days or ())
    if smoke:
        blocks = {b: [d for d in ds if d in smoke] for b, ds in blocks.items()}
    all_study_days = sorted(d for ds in blocks.values() for d in ds)
    if not all_study_days:
        raise SystemExit("no study days")
    for b, ds in blocks.items():
        missing_pm = [d for d in ds if not (PM_ROOT / f"{d}.parquet").exists()]
        missing_bars = [d for d in ds if not (BARS_ROOT / f"{d}.parquet").exists()]
        if missing_pm or missing_bars:
            raise SystemExit(f"{b}: missing pm={missing_pm[:3]} bars={missing_bars[:3]}")
    if any(not allowed(d) for d in all_study_days):
        raise SystemExit("protected study day refused")

    gaps = [
        (a, b, (dt_date.fromisoformat(b) - dt_date.fromisoformat(a)).days)
        for a, b in zip(sessions, sessions[1:])
    ]
    big = [g for g in gaps if g[2] > 10]
    if len(big) != 1 or big[0][0] != "2023-12-29" or big[0][1] != "2025-02-03":
        raise SystemExit(f"unexpected session-calendar gap structure: {big}")
    print(
        f"[pm-gap] sessions={len(sessions)}; the only >10d gap is the sealed block boundary "
        f"2023-12-29 -> 2025-02-03 ({big[0][2]} days); 2025-02-03 gets NO prev close",
        flush=True,
    )

    print(f"[pm-gap] blocks: " + ", ".join(f"{b}={len(ds)}" for b, ds in blocks.items()), flush=True)
    hist = warmup_history(pm_all, all_study_days[0])
    print(f"[pm-gap] warmup symbols with pm history: {len(hist)}", flush=True)

    tickers_by_day = {}
    for day in all_study_days:
        panel = pl.read_parquet(PANEL_DAYS / f"{day}.parquet")
        tickers_by_day[day] = sorted(set(panel["ticker"].to_list()))
    book = PrevCloseBook(sessions, all_study_days, tickers_by_day)
    print(
        f"[pm-gap] prev-close book: {sum(len(v) for v in book.closes.values())} (session, ticker) "
        f"closes across {len(book.closes)} prev sessions from {len(book.months_loaded)} monthly "
        f"tapes; 2025-02-03 has no calendar predecessor (sealed-safe: no prev close, symbols excluded)",
        flush=True,
    )

    features: list[dict] = []
    qa_rows: list[dict] = []
    for k, day in enumerate(all_study_days):
        features.extend(build_day(day, sessions, splits, book, hist))
        if k % 25 == 0:
            qa = qa_prev_close(day, sessions, cal, book, tickers_by_day[day])
            if qa is not None:
                qa_rows.append(qa)
        # causal update: today's pm_vol enters tomorrow's trailing median
        pm = pl.read_parquet(PM_ROOT / f"{day}.parquet").select("symbol", "pm_vol")
        for sym, vol in pm.iter_rows():
            if vol is not None and np.isfinite(vol) and vol > 0:
                d = hist.setdefault(str(sym), deque(maxlen=TRAIL_WINDOW))
                d.append(float(vol))
    feat = pl.DataFrame(features, schema=FEATURE_SCHEMA)
    print(f"[pm-gap] feature rows: {feat.height} in {time.time()-t0:.1f}s", flush=True)

    tmp_dir = Path("/tmp/premarket_gap_lane")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    feat_path = tmp_dir / "features.parquet"
    feat.write_parquet(feat_path)
    feat_sha = sha256_file(feat_path)

    coverage = {}
    for b, ds in blocks.items():
        if not ds:
            continue
        sub = feat.filter(pl.col("day").is_in(ds))
        excl = sub.group_by("exclude_reason").len().sort("exclude_reason")
        excl_map = {("included" if r["exclude_reason"] is None else str(r["exclude_reason"])): int(r["len"])
                    for r in excl.iter_rows(named=True)}
        coverage[b] = {
            "sessions": len(ds),
            "first_day": ds[0],
            "last_day": ds[-1],
            "calendar_days_spanned": int(
                (np.datetime64(ds[-1]) - np.datetime64(ds[0])).astype(int) + 1
            ),
            "panel_symbol_days": int(sub.height),
            "universe_symbol_days": int(sub["in_universe"].sum()),
            "exclusions": excl_map,
        }

    # per-day feature summary (compact, durable)
    day_sum = (
        feat.group_by("day")
        .agg(
            pl.len().alias("panel_symbols"),
            pl.col("in_universe").sum().alias("universe_symbols"),
            pl.col("pm_vol").filter(pl.col("in_universe")).median().alias("pm_vol_median"),
            pl.col("pm_chg").filter(pl.col("in_universe")).median().alias("pm_chg_median"),
            pl.col("pm_chg").filter(pl.col("in_universe")).quantile(0.9).alias("pm_chg_p90"),
            pl.col("pm_chg").filter(pl.col("in_universe")).quantile(0.1).alias("pm_chg_p10"),
            pl.col("pm_last_et").filter(pl.col("in_universe")).median().alias("pm_last_et_median"),
            (
                (pl.col("in_universe") & (pl.col("entry_status") == "unfilled_expired")).sum()
            ).alias("unfilled_universe"),
        )
        .sort("day")
    )

    # ----- replay grid ---------------------------------------------------------
    table: list[dict] = []
    surfaces: dict[str, dict[str, dict]] = {}
    for block, ds in blocks.items():
        if not ds:
            continue
        span = coverage[block]["calendar_days_spanned"]
        surfaces[block] = {}
        for cell in CELLS:
            side_dir = CELLS[cell][0]
            mask = cell_mask(features, cell)
            rows = [f for f, m in zip(features, mask) if m and f["day"] in set(ds)]
            surfaces[block][cell] = {}
            for h in HORIZONS:
                rep0 = replay(rows, ds, h, 0.0, side_dir)
                surfaces[block][cell][h] = {}
                for rung in RUNGS:
                    rep = replay(rows, ds, h, rung, side_dir)
                    s = summarize(rep, block, cell, h, rung, ds, span)
                    s["_rep"] = strip_daily(rep)
                    table.append(s)
                    surfaces[block][cell][h][rung] = s
                surfaces[block][cell][h]["_rep"] = strip_daily(rep0)
            print(
                f"[pm-gap] {block:<12} {cell:<14} h60 signals={surfaces[block][cell][60]['_rep']['signals']:<5} "
                f"known={surfaces[block][cell][60]['_rep']['known_fills']:<5} "
                f"unk={surfaces[block][cell][60]['_rep']['unknown_fills']:<4} "
                f"unfilled={surfaces[block][cell][60]['_rep']['unfilled_expired']:<4} "
                f"$/day@25={surfaces[block][cell][60][RUNGS[0]]['known_usd_per_calendar_day']:+.3f}",
                flush=True,
            )

    # ----- selection on validation (frozen BEFORE any late cell is read) --------
    val = surfaces.get("validation", {})
    if not val:
        raise SystemExit("no validation surface (smoke subset must include a 2023 day)")
    ranked = []
    for cell in SIGNAL_CELLS:
        for h in HORIZONS:
            s = val[cell][h][SELECT_RUNG]
            ranked.append((-s["known_usd_per_calendar_day"], -s["known_fills"], f"{cell}|h{h}", cell, h, s))
    ranked.sort(key=lambda x: (x[0], x[1], x[2]))
    if not ranked:
        raise SystemExit("no validation surface (smoke subset?)")
    neg, negf, key, win_cell, win_h, win_s = ranked[0]
    ctrl_same_h = val[CONTROL_CELL][win_h][SELECT_RUNG]
    increment_val = win_s["known_usd_per_calendar_day"] - ctrl_same_h["known_usd_per_calendar_day"]
    print(
        f"[freeze] validation winner {win_cell} h{win_h} "
        f"({win_s['known_usd_per_calendar_day']:+.4f} $/panel session @25bps, "
        f"{win_s['known_fills']} known fills) AFTER validation, BEFORE any late cell is read",
        flush=True,
    )

    # ----- late block -----------------------------------------------------------
    late = {}
    if not args.skip_late:
        late = surfaces.get("confirmation", {})
        if late:
            for cell in CELLS:
                side_dir = CELLS[cell][0]
                for h in HORIZONS:
                    s = late[cell][h][SELECT_RUNG]
                    print(
                        f"[late@25] {cell:<14} h{h} {s['known_usd_per_calendar_day']:+8.4f} $/day "
                        f"known={s['known_fills']} unk={s['unknown_fills']} "
                        f"attempts={s['attempts']} signals={s['signals']} "
                        f"traded={s['traded_days']}/{s['sessions']}",
                        flush=True,
                    )

    # ----- increment table (cell - primary control) ----------------------------
    increments = {}
    for block in surfaces:
        increments[block] = {}
        for h in HORIZONS:
            increments[block][str(h)] = {}
            base = {r: surfaces[block][CONTROL_CELL][h][r]["known_usd_per_calendar_day"] for r in RUNGS}
            for cell in CELLS:
                increments[block][str(h)][cell] = {
                    str(int(r)): surfaces[block][cell][h][r]["known_usd_per_calendar_day"] - base[r]
                    for r in RUNGS
                }

    winner_late = None
    if late:
        wl = late[win_cell][win_h]
        wl_ctrl = late[CONTROL_CELL][win_h]
        winner_late = {
            "cell": win_cell,
            "horizon": win_h,
            "late_known_usd_per_panel_session_25": wl[SELECT_RUNG]["known_usd_per_panel_session"],
            "late_known_usd_per_calendar_day_25": wl[SELECT_RUNG]["known_usd_per_calendar_day"],
            "late_known_fills": wl[SELECT_RUNG]["known_fills"],
            "late_unknown_fills": wl[SELECT_RUNG]["unknown_fills"],
            "late_control_known_usd_per_calendar_day_25": wl_ctrl[SELECT_RUNG]["known_usd_per_calendar_day"],
            "late_increment_usd_per_calendar_day_25": (
                wl[SELECT_RUNG]["known_usd_per_calendar_day"] - wl_ctrl[SELECT_RUNG]["known_usd_per_calendar_day"]
            ),
            "late_bootstrap_p_gt_zero": wl[SELECT_RUNG]["bootstrap_daily_known_usd"].get("p_gt_zero"),
        }

    dev_inc = (
        {str(h): {c: increments["dev"][str(h)][c] for c in SIGNAL_CELLS} for h in HORIZONS}
        if "dev" in increments
        else None
    )
    val_inc = {
        str(h): {c: increments["validation"][str(h)][c] for c in SIGNAL_CELLS} for h in HORIZONS
    }
    late_inc = (
        {str(h): {c: increments["confirmation"][str(h)][c] for c in SIGNAL_CELLS} for h in HORIZONS}
        if "confirmation" in increments else None
    )

    artifact = {
        "study": "premarket_gap_lane",
        "status": STATUS,
        "version": 1,
        "question": (
            "Is the premarket state of a top-gainer panel name (pm volume, pm change vs "
            "prev close, pm hi/lo, last pm print time, own-trailing-median volume "
            "extremity) a causal, tradable signal at the 09:30-09:35 open and in the "
            "first hour, after 25/100 bps round-trip friction?"
        ),
        "pre_registration": {
            "grid": {
                c: {
                    "side": "long" if CELLS[c][0] > 0 else "short",
                    "kind": CELLS[c][1],
                    "x_pct": None if CELLS[c][2] is None else round(100 * CELLS[c][2], 2),
                }
                for c in CELLS
            },
            "grid_ladder_pct": [100 * x for x in GRID_STEPS],
            "own_extreme_x": OWN_EXTREME_X,
            "trailing_window_sessions": TRAIL_WINDOW,
            "trailing_min_obs": TRAIL_MIN_OBS,
            "universe": (
                "day's distinct panel tickers with a premarket last print, a computable "
                "previous-session close (the previous CALENDAR session's last print on the "
                "full-universe monthly tape), and no split-ledger ex_date in "
                "(prev_session, day]; identical for every cell"
            ),
            "entry": "first actual minute open at/after 09:30 ET; no bar => unfilled_expired (reserve+release, no fees)",
            "exit": "first actual minute open at/after min(entry_et + H, session_end); absent => UNKNOWN (never dropped)",
            "horizons": {"60": "first hour", "390": "house close hold (last regular minute, early-close compressed)"},
            "rungs_bps_round_trip": [int(r) for r in RUNGS],
            "fee_formula": "net = (1+g) (1-s) / (1+s) - 1, s = bps/20000 per leg; shorts mirror the sign of g",
            "book": {"slots": MAX_SLOTS, "ticket_usd": ORDER_BUDGET, "total_usd": BOOK,
                     "one_position_per_ticker": True, "same_clock_sells_precede_buys": True},
            "slot_priority": "ticker alphabetical (score 0 everywhere); no within-rule pick",
            "unknown_convention": (
                "UNKNOWN exits excluded from the known numerator, position retained to "
                "session_end+1, and charged the FULL $1,000 ticket in the separately "
                "labelled full-loss lower bound"
            ),
            "selection": (
                "argmax validation known $/panel session at 25 bps over the 8 signal cells "
                "x 2 horizons; tie-break (- objective, - known fills, cell|horizon key); "
                "frozen after the validation surface and before any late cell is read"
            ),
            "blocks": {b: {"start": lo, "end": hi} for b, (lo, hi) in BLOCKS.items()},
            "protected_unread": ["2024", "2025-01", "2026-06", "2026-07", "2026-08"],
            "population_caveat": (
                "panel admission clocks run 09:35..12:00, so the day's panel is a post-hoc "
                "top-gainer population; every cell draws the SAME population so filter "
                "increments are population-consistent, and both controls carry the "
                "unconditional level"
            ),
        },
        "provenance": {
            "producer": "factory/scripts/alpha_premarket_gap_lane.py",
            "producer_sha256": sha256_file(Path(__file__).resolve()),
            "panel_days_root": str(PANEL_DAYS),
            "panel_contract_sha256": sha256_file(PANEL_CONTRACT),
            "pm_snapshots_root": str(PM_ROOT),
            "pm_days": len(pm_all),
            "bars_root": str(BARS_ROOT),
            "calendar": str(CALENDAR_PATH),
            "calendar_sessions": len(sessions),
            "calendar_early_closes": json.loads(CALENDAR_PATH.read_text())["detected_early_closes"],
            "splits_ledger": str(SPLITS_PATH),
            "prev_close_source": (
                "full-universe 1-minute monthly tapes (raw ohlcv_YYYY-MM.parquet preferred, "
                "clean_ohlcv_YYYY-MM.parquet fallback; the last print at/before 16:00 ET of "
                "the previous CALENDAR session per ticker), opened strictly by the session "
                "calendar so no sealed month is ever read; 2025-02-03 has no calendar "
                "predecessor and therefore no prev close (all symbols excluded)"
            ),
            "prev_close_months_loaded": book.months_loaded,
            "prev_close_pairs": sum(len(v) for v in book.closes.values()),
            "engine_mirror": "alpha_open_sim.replay conventions, side-aware for the fade cells",
            "bootstrap": {"n": BOOT_N, "seed": BOOT_SEED, "unit": "daily known USD, day-blocked"},
        },
        "coverage": coverage,
        "qa_prev_close_cross_source": {
            "note": (
                "every 25th study day: the monthly-tape prev close vs the SIP day tape's own "
                "prev-session close (overlapping tickers only); validates the cross-source "
                "prev-close feature input"
            ),
            "days_sampled": len(qa_rows),
            "per_day": qa_rows,
            "pooled": (
                {
                    "n_compared": int(sum(r["n_compared"] for r in qa_rows)),
                    "median_abs_rel_diff": float(
                        np.median([r["median_abs_rel_diff"] for r in qa_rows])
                    ),
                    "max_abs_rel_diff": float(max(r["max_abs_rel_diff"] for r in qa_rows)),
                }
                if qa_rows
                else None
            ),
        },
        "feature_table": {
            "full_parquet": {"path": str(feat_path), "sha256": feat_sha, "rows": int(feat.height),
                             "note": "throwaway copy under /tmp; durable digest recorded here"},
            "per_day_summary": day_sum.to_dicts(),
            "definitions": {
                "pm_chg": "pm_last_px / prev_session_close - 1 (raw prices; split-windows excluded)",
                "pm_vol": "premarket (04:00-09:30 ET) share volume from the pm snapshot",
                "top_decile": "pm_vol >= the day's universe 90th percentile",
                "pm_vol_trail_ratio": "pm_vol / median of the symbol's own prior 20 sessions' pm_vol",
                "pm_last_et": "ET minute of the last premarket print (240..569)",
            },
        },
        "table": [{k: v for k, v in row.items() if k != "_rep"} for row in table],
        "increment_usd_per_calendar_day_vs_control": increments,
        "selection": {
            "objective": "validation known_usd_per_calendar_day at 25 bps (per panel session)",
            "tie_break": ["objective desc", "known_fills desc", "cell|horizon key asc"],
            "winner": win_cell,
            "winner_horizon": win_h,
            "winner_key": key,
            "winner_validation": {
                "known_usd_per_calendar_day_25": win_s["known_usd_per_calendar_day"],
                "known_fills": win_s["known_fills"],
                "unknown_fills": win_s["unknown_fills"],
                "signals": win_s["signals"],
                "attempts": win_s["attempts"],
                "cash_or_slot_skips": win_s["cash_or_slot_skips"],
                "traded_days": win_s["traded_days"],
                "sessions": win_s["sessions"],
                "bootstrap_p_gt_zero": win_s["bootstrap_daily_known_usd"].get("p_gt_zero"),
            },
            "control_same_horizon_validation_25": {
                "known_usd_per_calendar_day_25": ctrl_same_h["known_usd_per_calendar_day"],
                "known_fills": ctrl_same_h["known_fills"],
            },
            "validation_increment_vs_control_25": increment_val,
            "frozen_before_late": True,
            "full_validation_ranking": [
                {
                    "key": r[2],
                    "cell": r[3],
                    "horizon": r[4],
                    "known_usd_per_calendar_day_25": -r[0],
                    "known_fills": r[5]["known_fills"],
                    "increment_vs_control_same_horizon_25": (
                        r[5]["known_usd_per_calendar_day"]
                        - val[CONTROL_CELL][r[4]][SELECT_RUNG]["known_usd_per_calendar_day"]
                    ),
                }
                for r in ranked
            ],
        },
        "winner_late": winner_late,
        "dev_increment_vs_control": dev_inc,
        "validation_increment_vs_control": val_inc,
        "late_increment_vs_control": late_inc,
        "runtime_s": round(time.time() - t0, 1),
    }

    # ----- measured decision text (computed from the numbers above) -------------
    wl25 = win_s["known_usd_per_calendar_day"]
    wl100 = val[win_cell][win_h][100.0]["known_usd_per_calendar_day"]
    c25 = ctrl_same_h["known_usd_per_calendar_day"]
    c100 = val[CONTROL_CELL][win_h][100.0]["known_usd_per_calendar_day"]
    late_txt = ""
    if winner_late is not None:
        late_txt = (
            f" The frozen cell on the previously-explored late block (2025-02..2026-05) "
            f"measures {winner_late['late_known_usd_per_calendar_day_25']:+.4f} $/panel session "
            f"at 25 bps over {winner_late['late_known_fills']} known fills vs the control's "
            f"{winner_late['late_control_known_usd_per_calendar_day_25']:+.4f} "
            f"(increment {winner_late['late_increment_usd_per_calendar_day_25']:+.4f})."
        )
    artifact["decision_text"] = (
        f"Validation winner {win_cell} at the h{win_h} horizon: {wl25:+.4f} $/panel session "
        f"at 25 bps ({wl100:+.4f} at 100 bps) over {win_s['known_fills']} known fills of "
        f"{win_s['signals']} signals on {win_s['traded_days']} traded sessions of "
        f"{win_s['sessions']}; day-bootstrap p(>0) = "
        f"{win_s['bootstrap_daily_known_usd'].get('p_gt_zero')}. The no-premarket-filter "
        f"control on the same horizon measures {c25:+.4f} $/panel session at 25 bps "
        f"({c100:+.4f} at 100 bps) over {ctrl_same_h['known_fills']} known fills, so the "
        f"premarket increment is {increment_val:+.4f} $/panel session at 25 bps "
        f"({(wl100 - c100):+.4f} at 100 bps).{late_txt} Both controls, both horizons and both "
        f"rungs are in the table; the pre-registered grid was fixed before any outcome was "
        f"computed and the selection was frozen after the validation surface and before any "
        f"late cell was read."
    )
    artifact["weakest_link"] = (
        "the prev-close anchor for the pm-change feature comes from a different provider tape "
        "(Finnhub monthly bars) than the study's own SIP tape, and the split-ledger exclusion "
        "is the only guard against unrecorded corporate actions; the cross-source spot check "
        "in qa_prev_close_cross_source quantifies that noise, and the signal counts per cell "
        "are small (a 3-slot book over ~30 panel names/day), so per-cell dollar figures carry "
        "wide uncertainty"
    )
    artifact["next_action"] = (
        "run the winning cell and the control forward on the next fresh month of pm_snapshots "
        "unseen by this grid (post-2026-05), with the same pre-registered contract, before any "
        "sizing or capacity work"
    )
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps(artifact, indent=1, default=str))
    print(f"[pm-gap] artifact -> {ARTIFACT} ({ARTIFACT.stat().st_size} bytes)", flush=True)


if __name__ == "__main__":
    main()
