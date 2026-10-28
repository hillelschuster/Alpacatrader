#!/usr/bin/env python
"""BASKET-01 Phase-2, family F2/F12 — causal "golden-window" state features.

This module defines **every** feature and target used by the F2/F12 state panel.
It is the single source of truth for the feature vocabulary; the panel builder
(``basket_f2_panel.py``) only orchestrates streaming.  Nothing here looks past
the checkpoint.

Causal convention (frozen for F2)
---------------------------------
A **checkpoint** ``tp`` is the ET minute of the **last completed bar** used.
All features are computed from bars with ``et <= tp`` (plus snapshot metadata),
the *checkpoint price* is ``close(tp)``, and all targets use bars
``tp + 1 .. session_end``.  This is causal: bar ``tp`` completes at minute
``tp + 1``, i.e. before any action can be executed at the open of the first bar
after ``tp``.  (Note: the anatomy ``state[T]`` field uses ``close(T-1)``; the F2
panel intentionally uses ``close(tp)`` per the F2 work order, so the two differ
by one bar.)

Entry families (one snapshot each per day)
------------------------------------------
``A_pm``  = snapshot ``(A_pm, 570)``  (premarket print, fill at 09:30 open)
``A_open``= snapshot ``(A_open, 570)`` (completed 09:30 bar, fill at 09:31 open)
``B600``  = snapshot ``(B, 600)``     (completed 09:59 bar, fill >= 10:00)

Only **filled** names produce panel rows; blocked / missing fills are excluded
from rows but counted (see ``build_day`` return value).  All ten snapshot names
still feed the basket-level features (breadth / relative strength).

Feature vocabulary (all causal, by ``tp`` only)
-----------------------------------------------
Identity
  date, family, ticker, rank, fill_et, fill_px, checkpoint et, session_end.
State / path
  px_tp                     close(tp)
  ret_from_fill             close(tp) / fill_px - 1
  ret_from_prevclose        close(tp) / prev_close - 1
  ret_from_open0930         close(tp) / open0930 - 1
  rank                      snapshot rank of the name (1..10)
  premarket_rank            rank in the (A_pm, 570) snapshot, else NaN
  rank_change               rank - premarket_rank (positive = slipped a rank)
  rank_std                  std of the name's rank across B snapshots with
                            T in {575..tp} where it is present (rank stability)
  rank_mode_share           share of those B snapshots at the modal rank
  mfe_so_far                max(high[fill..tp]) / fill_px - 1
  mae_so_far                min(low[fill..tp]) / fill_px - 1
  dd_from_running_high      close(tp) / max(high[fill..tp]) - 1  (peak from fill)
  dd_from_day_high          close(tp) / max(high[first bar..tp]) - 1 (session peak)
  time_since_running_high   et(tp) - et(bar of running max from fill), minutes
  time_since_day_high       et(tp) - et(bar of session running max), minutes
  pos_in_range_fill         (close(tp) - min low) / (max high - min low) over
                            [fill..tp]; 0.5 if the range is degenerate
  velocity_1/3/5            close(tp) / close(last bar at et <= tp-k) - 1 for
                            k = 1/3/5 (per-minute momentum)
  accel_proxy               velocity_1 - velocity_5 / 5  (1-min change minus the
                            5-min mean per-minute change; price-normalised)
  cum_volume                sum(volume[fill..tp])
  dollar_volume             sum(close * volume[fill..tp])
  vol_accel                 sum(volume last 5 min) / sum(volume prior 5 min) - 1,
                            windows (tp-5, tp] vs (tp-10, tp-5], NaN if prior 0
  new_high_count            # bars in [fill..tp] whose high equals the running
                            session high (i.e. that set a new session high)
  new_high_freq             new_high_count / (tp - fill_et + 1)
  time_below_recent_high    time_since_day_high / max(1, tp - fill_et)
  breach_5 / breach_10      min(low[fill..tp]) <= fill_px * (1 - x) for x=5%/10%
  reclaimed_5 / reclaimed_10  after the first -x% breach from fill, did any
                            later close[fill+1..tp] return above fill_px*(1-x)?
  n_bars_since_fill         # bars with et in [fill_et..tp]
Basket context (same family snapshot, computed at ``tp``)
  rel_strength              own ret_from_open0930 - median(member
                            ret_from_open0930 at tp); members = all top-10
  basket_score_disp         std of snapshot ``sel`` scores (snapshot-level)
  basket_breadth_10/30      # members whose close(tp)/open0930 - 1 >= 10%/30%
  n_members                 size of the snapshot (always 10 here)

Targets (bars tp+1 .. session_end only; all NaN if no forward bar)
------------------------------------------------------------------
  rem_mfe                   max(high[tp+1..]) / close(tp) - 1
  rem_mae                   min(low[tp+1..]) / close(tp) - 1
  touch10/30/50/100         any high[tp+1..] >= close(tp) * (1 + X)   (primary,
                            relative to checkpoint price)
  touch_fill_10/30/50/100   any high[tp+1..] >= fill_px * (1 + X)     (secondary,
                            relative to fill)
  fwd_eod_ret               close[session_end] / close(tp) - 1
  nhba                      new-high-before-adverse: scan bars tp+1.. in order;
                            a bar whose low <= (session running max up to tp) *
                            0.90 is adverse-first (same-bar ambiguity pessimistic:
                            adverse checked before new high); a bar whose high
                            exceeds that max is new-high-first.  True iff new
                            high happened first.
  adverse_first             True iff the adverse branch fired first
  no_event                  True iff neither branch fired by session_end
  event_et                  et of the first event bar, NaN if no event
  time_to_new_high          minutes from tp to the first bar with
                            high > session running max up to tp; censored at
                            session_end when no such bar exists
  newhigh_censored          True iff time_to_new_high was censored

Cohorts by *remaining* (forward) tail size are built in the analysis stage from
``rem_mfe`` — they are deliberately not stored as features.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
_ART = Path(os.environ.get("BASKET_ART_ROOT", ROOT / "factory" / "artifacts" / "basket"))
SIP_ROOT = _ART / "sip"
ANAT_DIR = SIP_ROOT / "anatomy"
BARS_DIR = SIP_ROOT / "bars"
CAL_PATH = SIP_ROOT / "phase2_session_calendar.json"

# Hard evidence boundary (must never be opened).
SEALED_PREFIXES = ("2024-", "2025-01")
RESERVED_MONTHS = ("2026-06", "2026-07", "2026-08")

# F2 work-order checkpoint grid (note: 595 intentionally omitted).
CHECKPOINTS = (580, 585, 590, 600, 615, 630, 645, 660, 690, 720)
# B snapshots used for rank-stability paths.
B_RANK_TS = (575, 580, 585, 590, 595, 600, 615, 630, 645, 660, 690, 720)
# family id -> (pop, T)
FAMILIES = {"A_pm": ("A_pm", 570), "A_open": ("A_open", 570), "B600": ("B", 600)}

# Temporary session map (canonical calendar absent at build time).  779 = 13:00 ET.
EARLY_CLOSE_CANDIDATES = {
    "2021-11-26", "2022-11-25", "2023-07-03", "2023-11-24",
    "2025-07-03", "2025-11-28", "2025-12-24",
}
SESSION_END_NORMAL = 959
SESSION_END_EARLY = 779

TOUCH_LEVELS = (10, 30, 50, 100)

#: Programmatic vocabulary for the surfaces pass.
FEATURE_DEFS = {
    "ret_from_fill": "close(tp)/fill_px-1",
    "ret_from_prevclose": "close(tp)/prev_close-1",
    "ret_from_open0930": "close(tp)/open0930-1",
    "rank": "snapshot rank 1..10",
    "rank_change": "rank - premarket_rank",
    "rank_std": "std of B-snapshot ranks in [575,tp]",
    "rank_mode_share": "share of B snapshots at modal rank",
    "mfe_so_far": "max(high[fill..tp])/fill_px-1",
    "mae_so_far": "min(low[fill..tp])/fill_px-1",
    "dd_from_running_high": "close(tp)/max(high[fill..tp])-1",
    "dd_from_day_high": "close(tp)/max(high[session..tp])-1",
    "time_since_running_high": "minutes since running max from fill",
    "time_since_day_high": "minutes since session running max",
    "pos_in_range_fill": "close position within [min low,max high] since fill",
    "velocity_1": "close(tp)/close(tp-1)-1",
    "velocity_3": "close(tp)/close(tp-3)-1",
    "velocity_5": "close(tp)/close(tp-5)-1",
    "accel_proxy": "velocity_1 - velocity_5/5",
    "cum_volume": "sum volume since fill",
    "dollar_volume": "sum close*volume since fill",
    "vol_accel": "last-5-min volume / prior-5-min volume - 1",
    "new_high_count": "# new session highs since fill",
    "new_high_freq": "new_high_count / bars since fill",
    "time_below_recent_high": "time_since_day_high / minutes since fill",
    "breach_5": "min(low since fill) <= fill_px*0.95",
    "breach_10": "min(low since fill) <= fill_px*0.90",
    "reclaimed_5": "close back above fill_px*0.95 after first -5% breach",
    "reclaimed_10": "close back above fill_px*0.90 after first -10% breach",
    "rel_strength": "own ret_from_open0930 - median member ret at tp",
    "basket_score_disp": "std of snapshot sel scores",
    "basket_breadth_10": "# members >= +10% from 09:30 at tp",
    "basket_breadth_30": "# members >= +30% from 09:30 at tp",
    "n_bars_since_fill": "# bars in [fill_et..tp]",
}

TARGET_DEFS = {
    "rem_mfe": "max(high[tp+1..sess])/close(tp)-1",
    "rem_mae": "min(low[tp+1..sess])/close(tp)-1",
    "touch10": "high reaches close(tp)*1.10",
    "touch30": "high reaches close(tp)*1.30",
    "touch50": "high reaches close(tp)*1.50",
    "touch100": "high reaches close(tp)*2.00",
    "fwd_eod_ret": "close(sess)/close(tp)-1",
    "nhba": "new session high before -10% drawdown from session running max",
    "time_to_new_high": "minutes to first bar above session running max at tp",
}


# --------------------------------------------------------------------------- #
# guards / loaders
# --------------------------------------------------------------------------- #
def guard_day(day: str) -> None:
    if not isinstance(day, str) or len(day) != 10:
        raise ValueError(f"bad day id: {day!r}")
    if day.startswith(SEALED_PREFIXES):
        raise PermissionError(f"REFUSED sealed day {day}")
    if day[:7] in RESERVED_MONTHS:
        raise PermissionError(f"REFUSED reserved month {day}")


def dev_days() -> list[str]:
    days = sorted(p.name[:-6] for p in ANAT_DIR.glob("*.jsonl"))
    for d in days:
        guard_day(d)
    return days


def load_anatomy(day: str) -> dict:
    guard_day(day)
    with (ANAT_DIR / f"{day}.jsonl").open("rb") as fh:
        rec = json.loads(fh.read())
    if rec.get("date") != day:
        raise RuntimeError(f"anatomy date mismatch for {day}")
    return rec


def load_bars(day: str, session_end: int) -> dict[str, dict[str, np.ndarray]]:
    """Per-ticker numpy arrays, sorted by et, restricted to et <= session_end."""
    guard_day(day)
    df = pl.read_parquet(
        BARS_DIR / f"{day}.parquet",
        columns=["ticker", "et", "high", "low", "close", "open", "volume"],
    ).filter(pl.col("et") <= session_end).sort(["ticker", "et"])
    out: dict[str, dict[str, np.ndarray]] = {}
    if df.height == 0:
        return out
    tk = df["ticker"].to_numpy()
    starts = np.flatnonzero(np.r_[True, tk[1:] != tk[:-1]])
    ends = np.r_[starts[1:], len(tk)]
    et = df["et"].to_numpy().astype(np.int64)
    hh = df["high"].to_numpy().astype(np.float64)
    ll = df["low"].to_numpy().astype(np.float64)
    cc = df["close"].to_numpy().astype(np.float64)
    oo = df["open"].to_numpy().astype(np.float64)
    vv = df["volume"].to_numpy().astype(np.float64)
    for s, e in zip(starts, ends):
        out[str(tk[s])] = {
            "et": et[s:e], "high": hh[s:e], "low": ll[s:e],
            "close": cc[s:e], "open": oo[s:e], "volume": vv[s:e],
        }
    return out


def session_end_for(day: str) -> tuple[int, bool]:
    """Return (session_end, estimated_flag).  Uses the canonical calendar when it
    exists, else the F2 temporary map (FLAGGED)."""
    if CAL_PATH.exists():
        with CAL_PATH.open("rb") as fh:
            cal = json.loads(fh.read())
        m = cal.get("session_end_by_day", {})
        if day in m:
            return int(m[day]), False
    return (SESSION_END_EARLY if day in EARLY_CLOSE_CANDIDATES else SESSION_END_NORMAL), True


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _iob(et: np.ndarray, t: int) -> int:
    """Index of last bar with et <= t, or -1."""
    i = int(np.searchsorted(et, t, side="right")) - 1
    return i


def _f(x) -> float:
    return float(x) if x is not None else float("nan")


def _running_argmax(x: np.ndarray) -> np.ndarray:
    """Index (first occurrence) of the running maximum at each position."""
    idx = np.arange(len(x))
    run = np.where(x == np.maximum.accumulate(x), idx, -1)
    return np.maximum.accumulate(run)


# --------------------------------------------------------------------------- #
# per-name path precomputation + per-checkpoint features/targets
# --------------------------------------------------------------------------- #
class _Path:
    """Pre-computed day arrays for one ticker, plus fill-relative prefixes."""

    __slots__ = ("et", "h", "l", "c", "v", "n", "fi", "fill_px",
                 "pmax", "firstidx", "smax", "smin", "cumv", "cumd",
                 "nhmask", "cmask", "fh", "fl", "frunarg", "sub_h", "sub_l",
                 "sub_c", "sub_et")

    def __init__(self, b: dict[str, np.ndarray], fill_et: int, fill_px: float):
        self.et = b["et"]; self.h = b["high"]; self.l = b["low"]
        self.c = b["close"]; self.v = b["volume"]; self.n = len(self.et)
        self.fill_px = fill_px
        self.fi = int(np.searchsorted(self.et, fill_et))
        if self.fi >= self.n or self.et[self.fi] != fill_et:
            raise ValueError("fill bar missing")
        h, l, c, v = self.h, self.l, self.c, self.v
        self.pmax = np.maximum.accumulate(h)
        self.firstidx = _running_argmax(h)
        self.smax = np.maximum.accumulate(h[::-1])[::-1]
        self.smin = np.minimum.accumulate(l[::-1])[::-1]
        self.cumv = np.cumsum(v)
        self.cumd = np.cumsum(c * v)
        self.nhmask = (h == self.pmax)
        self.cmask = np.cumsum(self.nhmask)
        fi = self.fi
        self.sub_h = h[fi:]; self.sub_l = l[fi:]; self.sub_c = c[fi:]; self.sub_et = self.et[fi:]
        self.fh = np.maximum.accumulate(self.sub_h)
        self.fl = np.minimum.accumulate(self.sub_l)
        self.frunarg = _running_argmax(self.sub_h)


def _checkpoint_row(day, family, name, p: _Path, tp: int, sess_end: int,
                    pre_rank: dict, b_ranks: dict, member_ret: list[float],
                    score_disp: float, n_members: int, cal_est: bool) -> dict | None:
    et = p.et
    ti = int(np.searchsorted(et, tp))
    if ti >= p.n or et[ti] != tp or ti <= p.fi:
        return None
    pos = ti - p.fi
    c_tp = float(p.c[ti])
    fill_px = p.fill_px
    o9 = _f(name.get("open0930"))
    pc = _f(name.get("prev_close"))

    # velocity lookbacks
    i1, i3, i5 = _iob(et, tp - 1), _iob(et, tp - 3), _iob(et, tp - 5)
    vel1 = c_tp / float(p.c[i1]) - 1.0 if i1 >= 0 else float("nan")
    vel3 = c_tp / float(p.c[i3]) - 1.0 if i3 >= 0 else float("nan")
    vel5 = c_tp / float(p.c[i5]) - 1.0 if i5 >= 0 else float("nan")
    accel = vel1 - vel5 / 5.0 if (i1 >= 0 and i5 >= 0) else float("nan")

    # volume windows
    ia, ib = _iob(et, tp - 5), _iob(et, tp - 10)
    base_v = float(p.cumv[p.fi - 1]) if p.fi > 0 else 0.0
    last5 = float(p.cumv[ti]) - (float(p.cumv[ia]) if ia >= 0 else 0.0)
    prior5 = (float(p.cumv[ia]) - (float(p.cumv[ib]) if ib >= 0 else 0.0)) if ia >= 0 else 0.0
    vol_accel = last5 / prior5 - 1.0 if prior5 > 0 else float("nan")

    # new highs since fill
    base_nh = float(p.cmask[p.fi - 1]) if p.fi > 0 else 0.0
    nh_count = float(p.cmask[ti]) - base_nh

    # breach / reclaim
    thr5, thr10 = fill_px * 0.95, fill_px * 0.90
    fl_pos = float(p.fl[pos])
    breach5 = fl_pos <= thr5
    breach10 = fl_pos <= thr10
    reclaimed5 = _reclaimed(p.sub_l[:pos + 1], p.sub_c[:pos + 1], thr5) if breach5 else False
    reclaimed10 = _reclaimed(p.sub_l[:pos + 1], p.sub_c[:pos + 1], thr10) if breach10 else False

    # targets
    tg = {k: float("nan") for k in
          ("rem_mfe", "rem_mae", "fwd_eod_ret", "time_to_new_high")}
    tg.update({f"touch{x}": float("nan") for x in TOUCH_LEVELS})
    tg.update({f"touch_fill_{x}": float("nan") for x in TOUCH_LEVELS})
    tg.update({"nhba": float("nan"), "adverse_first": float("nan"),
               "no_event": float("nan"), "event_et": float("nan"),
               "newhigh_censored": float("nan")})
    if ti + 1 < p.n:
        sm_h = float(p.smax[ti + 1]); sm_l = float(p.smin[ti + 1])
        tg["rem_mfe"] = sm_h / c_tp - 1.0
        tg["rem_mae"] = sm_l / c_tp - 1.0
        tg["fwd_eod_ret"] = float(p.c[p.n - 1]) / c_tp - 1.0
        for x in TOUCH_LEVELS:
            lvl = x / 100.0
            tg[f"touch{x}"] = float(sm_h >= c_tp * (1.0 + lvl))
            tg[f"touch_fill_{x}"] = float(sm_h >= fill_px * (1.0 + lvl))
        M0 = float(p.pmax[ti])
        ev, ev_et = -1, float("nan")
        th = None
        for i in range(ti + 1, p.n):
            if p.l[i] <= M0 * 0.90:
                ev, ev_et = 0, int(p.et[i]); break
            if p.h[i] > M0:
                ev, ev_et = 1, int(p.et[i]); break
        tg["nhba"] = float(ev == 1)
        tg["adverse_first"] = float(ev == 0)
        tg["no_event"] = float(ev == -1)
        tg["event_et"] = ev_et
        t2 = None
        for i in range(ti + 1, p.n):
            if p.h[i] > M0:
                t2 = int(p.et[i]); break
        if t2 is None:
            tg["time_to_new_high"] = float(sess_end - tp)
            tg["newhigh_censored"] = 1.0
        else:
            tg["time_to_new_high"] = float(t2 - tp)
            tg["newhigh_censored"] = 0.0

    own_open = c_tp / o9 - 1.0 if (o9 == o9 and o9 > 0) else float("nan")
    med = float(np.median(member_ret)) if member_ret else float("nan")

    row = {
        "date": day, "family": family, "ticker": name["ticker"],
        "rank": int(name["rank"]), "fill_et": int(_f(name["fill"]["et"])),
        "fill_px": fill_px, "et": int(tp), "px_tp": c_tp,
        "session_end": int(sess_end), "calendar_estimated": bool(cal_est),
        # state
        "ret_from_fill": c_tp / fill_px - 1.0,
        "ret_from_prevclose": (c_tp / pc - 1.0) if pc == pc else float("nan"),
        "ret_from_open0930": own_open,
        "premarket_rank": int(pre_rank.get(name["ticker"])) if name["ticker"] in pre_rank else None,
        "rank_change": (int(name["rank"]) - pre_rank[name["ticker"]]) if name["ticker"] in pre_rank else None,
        "mfe_so_far": float(p.fh[pos]) / fill_px - 1.0,
        "mae_so_far": float(p.fl[pos]) / fill_px - 1.0,
        "dd_from_running_high": c_tp / float(p.fh[pos]) - 1.0,
        "dd_from_day_high": c_tp / float(p.pmax[ti]) - 1.0,
        "time_since_running_high": int(tp - p.sub_et[p.frunarg[pos]]),
        "time_since_day_high": int(tp - et[p.firstidx[ti]]),
        "pos_in_range_fill": ((c_tp - fl_pos) / (float(p.fh[pos]) - fl_pos)
                              if float(p.fh[pos]) > fl_pos else 0.5),
        "velocity_1": vel1, "velocity_3": vel3, "velocity_5": vel5,
        "accel_proxy": accel,
        "cum_volume": float(p.cumv[ti]) - base_v,
        "dollar_volume": float(p.cumd[ti]) - (float(p.cumd[p.fi - 1]) if p.fi > 0 else 0.0),
        "vol_accel": vol_accel,
        "new_high_count": nh_count,
        "new_high_freq": nh_count / (ti - p.fi + 1),
        "time_below_recent_high": (tp - et[p.firstidx[ti]]) / max(1, tp - int(_f(name["fill"]["et"]))),
        "breach_5": bool(breach5), "breach_10": bool(breach10),
        "reclaimed_5": bool(reclaimed5), "reclaimed_10": bool(reclaimed10),
        "rel_strength": own_open - med if med == med else float("nan"),
        "basket_score_disp": score_disp,
        "basket_breadth_10": int(sum(1 for r in member_ret if r == r and r >= 0.10)),
        "basket_breadth_30": int(sum(1 for r in member_ret if r == r and r >= 0.30)),
        "n_members": int(n_members),
        "n_bars_since_fill": int(ti - p.fi + 1),
    }
    for k, val in tg.items():
        row[k] = val
    # rank stability across B snapshots up to tp
    rv = [b_ranks[t][name["ticker"]] for t in B_RANK_TS
          if t in b_ranks and t <= tp and name["ticker"] in b_ranks[t]]
    if rv:
        row["rank_std"] = float(np.std(rv))
        vals, cnts = np.unique(rv, return_counts=True)
        row["rank_mode_share"] = float(cnts.max()) / len(rv)
    else:
        row["rank_std"] = float("nan"); row["rank_mode_share"] = float("nan")
    return row


def _reclaimed(sub_l: np.ndarray, sub_c: np.ndarray, thr: float) -> bool:
    """After the first bar whose low <= thr, did a later close exceed thr?"""
    hit = np.flatnonzero(sub_l <= thr)
    if len(hit) == 0:
        return False
    fb = int(hit[0])
    if fb + 1 >= len(sub_c):
        return False
    return bool((sub_c[fb + 1:] > thr).any())


# --------------------------------------------------------------------------- #
# day builder
# --------------------------------------------------------------------------- #
def build_day(rec: dict, bars: dict, sess_end: int, cal_est: bool) -> tuple[list[dict], list[dict]]:
    """Return (panel_rows, blocked_records) for one day, all three families."""
    day = rec["date"]
    by = {(s["pop"], int(s["T"])): s for s in rec["snapshots"]}
    pm = by.get(("A_pm", 570))
    pre_rank = {n["ticker"]: int(n["rank"]) for n in pm["names"]} if pm else {}
    b_ranks = {int(s["T"]): {n["ticker"]: int(n["rank"]) for n in s["names"]}
               for s in rec["snapshots"] if s["pop"] == "B"}

    rows: list[dict] = []
    blocked: list[dict] = []
    for family, (pop, T) in FAMILIES.items():
        snap = by.get((pop, T))
        if snap is None:
            continue
        names = snap["names"]
        sels = [n["sel"] for n in names if n.get("sel") is not None]
        disp = float(np.std(sels)) if len(sels) > 1 else float("nan")
        # basket member returns at each checkpoint (from 09:30 open)
        member_ret: dict[int, list[float]] = {}
        for tp in CHECKPOINTS:
            vals = []
            for n in names:
                b = bars.get(n["ticker"])
                if b is None:
                    continue
                i = int(np.searchsorted(b["et"], tp))
                if i >= len(b["et"]) or b["et"][i] != tp:
                    continue
                o9 = n.get("open0930")
                if o9:
                    vals.append(float(b["close"][i]) / float(o9) - 1.0)
            member_ret[tp] = vals
        for n in names:
            fill = n.get("fill")
            if not fill or fill.get("blocked") or fill.get("et") is None:
                blocked.append({"date": day, "family": family, "ticker": n["ticker"],
                                "rank": int(n["rank"]),
                                "reason": "blocked" if (fill and fill.get("blocked")) else "no_fill"})
                continue
            b = bars.get(n["ticker"])
            if b is None:
                blocked.append({"date": day, "family": family, "ticker": n["ticker"],
                                "rank": int(n["rank"]), "reason": "no_bars"})
                continue
            try:
                p = _Path(b, int(fill["et"]), float(fill["px"]))
            except ValueError:
                blocked.append({"date": day, "family": family, "ticker": n["ticker"],
                                "rank": int(n["rank"]), "reason": "fill_bar_missing"})
                continue
            for tp in CHECKPOINTS:
                r = _checkpoint_row(day, family, n, p, tp, sess_end, pre_rank, b_ranks,
                                    member_ret.get(tp, []), disp, len(names), cal_est)
                if r is not None:
                    rows.append(r)
    return rows, blocked


# --------------------------------------------------------------------------- #
# spot tests (assert-based, manual arithmetic on 2021-02-01 / MBRX / A_open)
# --------------------------------------------------------------------------- #
def _selftest() -> None:
    day = "2021-02-01"
    rec = load_anatomy(day)
    sess_end, cal_est = session_end_for(day)
    assert sess_end == 959, sess_end
    bars = load_bars(day, sess_end)
    rows, blocked = build_day(rec, bars, sess_end, cal_est)
    got = [r for r in rows if r["ticker"] == "MBRX" and r["family"] == "A_open" and r["et"] == 585]
    assert len(got) == 1, f"expected one MBRX A_open@585 row, got {len(got)}"
    r = got[0]
    # hand arithmetic from the raw bars of MBRX on 2021-02-01 (fill et=571 px=4.6701,
    # close(585)=4.6399, max high since fill=4.92, min low since fill=4.55):
    assert abs(r["px_tp"] - 4.6399) < 1e-12
    assert abs(r["ret_from_fill"] - (4.6399 / 4.6701 - 1.0)) < 1e-12
    assert abs(r["ret_from_prevclose"] - (4.6399 / 0.8285 - 1.0)) < 1e-12
    assert abs(r["ret_from_open0930"] - (4.6399 / 4.63 - 1.0)) < 1e-12
    assert abs(r["mfe_so_far"] - (4.92 / 4.6701 - 1.0)) < 1e-12
    assert abs(r["mae_so_far"] - (4.55 / 4.6701 - 1.0)) < 1e-12
    assert abs(r["dd_from_running_high"] - (4.6399 / 4.92 - 1.0)) < 1e-12
    assert abs(r["dd_from_day_high"] - (4.6399 / 4.92 - 1.0)) < 1e-12
    assert r["time_since_day_high"] == 585 - 577  # session high 4.92 set at et=577
    assert r["cum_volume"] == 146204.0
    assert abs(r["dollar_volume"] - 691982.1652) < 1e-6
    assert r["rank"] == 1
    assert r["pos_in_range_fill"] == (4.6399 - 4.55) / (4.92 - 4.55)
    # A_pm fill at et=570 must not produce a row for checkpoint 570 (not in grid),
    # and B600 fill (et=600) must not produce a row at checkpoint 600.
    assert not any(x["family"] == "B600" and x["et"] == 600 for x in rows)
    assert any(x["family"] == "A_pm" for x in rows)
    print("selftest OK: MBRX A_open@585 features match manual arithmetic")
    print("  ret_from_fill", round(r["ret_from_fill"], 6),
          "mfe", round(r["mfe_so_far"], 6),
          "cum_volume", r["cum_volume"],
          "rem_mfe", round(r["rem_mfe"], 4),
          "touch50", r["touch50"], "nhba", r["nhba"])


if __name__ == "__main__":
    _selftest()
