#!/usr/bin/env python3
"""LIFECYCLE-02 — unified causal top-gainer roster + minute life-cycle substrate (v2).

ONE anchor for the whole study: gain versus the immediately previous ACTUAL session close,
split-normalized (the displayed-change convention a screener shows):

    gain_adj = decision_px / (prev_close_raw * split_factor) - 1

Decision price is the causal, completed-bar price at the clock:
  * PM clocks (540 = 09:00, 560 = 09:20, 569 = 09:29): close of the last completed premarket
    bar et <= clock-1, from the PIT `sip/pm_snapshots` lane (px_clock).
  * near-open clock 571 (09:31): the `race.minute_full` board row t=571, whose px is the
    close of the last completed bar et <= 570 with known_by_t true.

Ranking reuses the validated HARVEST01 same-anchor primary universe
(`basket_harvest_select.build_universe` + its primary filter) with a LOCAL KEEP_RANKS=5.
That selector is imported, never modified. Rows failing the primary filter are RETAINED in
the universe with primary=False, exclude_reason and action in {exclude_confirmed,
action_uncertain} (a >10x gain alone is an uncertainty flag, not proof of broken data), plus
prev_close provenance for the discrepancy evidence. Ties break on (gain_adj desc, ticker asc).

Previous session is resolved from the RTH universe index (`sip/universe/index_rth.jsonl`),
never from "the previous development day".

Bars / paths: the local RAW full-market minute store `ohlcv_<month>.parquet` (root, or
`backfill/ohlcv_<month>.parquet`) — exactly the `ohlcv_raw` seed the atlas race panel was
built from (verified: race px == ohlcv close at px_et). It carries PM (et>=240) and RTH in
one consistent lane, avoiding the clean_ohlcv sparse/delisted dropouts. Only month 2025-02
has no monthly file: RTH from `atlas/acquisition/v{4,3,2}/bars/<day>.parquet`, PM from
`backfill/premarket_ohlcv_2025-02.parquet`, source recorded per row. No network.

Semantics. For a member-minute at grid time t:
  * px / px_et  = close of the last COMPLETED bar with et <= t-1 (never the entry print)
  * filled      = executed (t >= fill_et); filled_asof = position marked (t > fill_et).
    Own return/entry-basis features (ret_fill, peak_gain, dd_from_high, mae_sofar,
    bars_since_high, ret_k) are only defined when filled_asof.
  * labels are executable from the OBSERVED tape: sell_px = open of the first valid bar with
    et >= t; V{h} = open of the first valid bar with et >= t+h; fmfe/fmae{h} span observed
    bars with et in [sell_et, t+h] and are masked when t+h > session_end or sell_et > t+h
    (incomplete horizon). exit_px/exit_et = open/et of the first valid bar with et >=
    session_end (UNKNOWN if none) — never the last close. mark_px/mark_et = last observed
    close (a mark, not an execution). No close-for-open fallback anywhere.
  * blocked (first valid open >= clock + GAP_MAX min) and missing (no execution data) ranks
    are retained and treated as cash for peer aggregates; missing is UNKNOWN (nan).

Outputs (atomic, per-day resumable; the schema/source manifest invalidates stale output):
    <data>/harvest01/lifecycle/v2/universe/<day>.parquet   complete causal ranked universe
    <data>/harvest01/lifecycle/v2/roster/<day>.parquet     fixed top-5 anchor + labels
    <data>/harvest01/lifecycle/v2/panel/<day>.parquet      roster-member minute life cycle
    <data>/harvest01/lifecycle/v2/_done/<day>.json         per-day marker (status ok|empty)
    <data>/harvest01/lifecycle/v2/manifest.json            schema + source identities
`v2/split.json` is read-only and never written here.

Usage:
    .venv/bin/python factory/scripts/lifecycle_build.py --days 2021-02-01
    .venv/bin/python factory/scripts/lifecycle_build.py --months 2021-02
    HARVEST_THREADS=2 .venv/bin/python factory/scripts/lifecycle_build.py --all --workers 3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import warnings
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# bound native threads before numeric libraries load (parent runs several workers)
_TH = os.environ.get("HARVEST_THREADS", "2")
for _v in ("POLARS_MAX_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
           "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, _TH)

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402
import basket_harvest_select as bhs  # noqa: E402  (validated same-anchor universe; imported only)

ET = ZoneInfo("America/New_York")
SCHEMA_VERSION = "lifecycle-v2.8"
CLOCKS = (510, 540, 550, 555, 560, 565, 569, 571)
PM_CLOCKS = (510, 540, 550, 555, 560, 565, 569)
NEAR_OPEN = 571
KEEP_RANKS = 5
HORIZONS = (1, 2, 3, 5, 10, 15, 30, 60, 120)
TAIL_H = (30, 60, 120)
GAP_MAX = 5                     # execution assumption: first valid open >= clock + GAP_MAX min is blocked
V2_SUB = ("harvest01", "lifecycle", "v2")
ACQ_VERSIONS = ("v4", "v3", "v2")


# --------------------------------------------------------------------------- calendar/split

def calendar(data_root: Path) -> dict:
    return {d: int(v["session_end"]) for d, v in json.loads(bps.CALENDAR.read_text())["evidence"].items()}


def calendar_sha256() -> str:
    return hashlib.sha256(bps.CALENDAR.read_bytes()).hexdigest()


def read_split(data_root: Path) -> dict:
    p = data_root.joinpath(*V2_SUB) / "split.json"
    return json.loads(p.read_text()) if p.exists() else {}


# --------------------------------------------------------------------------- bar sources

def _add_et_day(lf: pl.LazyFrame) -> pl.LazyFrame:
    ets = pl.col("timestamp").dt.convert_time_zone("America/New_York")
    return lf.with_columns(
        day=ets.dt.strftime("%Y-%m-%d"),
        et=(ets.dt.hour().cast(pl.Int32) * 60 + ets.dt.minute().cast(pl.Int32)),
    )


def monthly_ohlcv(data_root: Path, month: str) -> Path | None:
    for p in (data_root / f"ohlcv_{month}.parquet", data_root / "backfill" / f"ohlcv_{month}.parquet"):
        if p.exists():
            return p
    return None


def acq_bars_path(data_root: Path, day: str) -> Path | None:
    for v in ACQ_VERSIONS:
        p = data_root / "atlas" / "acquisition" / v / "bars" / f"{day}.parquet"
        if p.exists():
            return p
    return None


def resolve_bars_plan(data_root: Path, month: str, days: list[str]) -> dict:
    m = monthly_ohlcv(data_root, month)
    if m is not None:
        return {"kind": "monthly", "monthly": m, "label": m.name}
    acq = {d: acq_bars_path(data_root, d) for d in days}
    prem = data_root / "backfill" / f"premarket_ohlcv_{month}.parquet"
    prem = prem if prem.exists() else None
    if not any(p is not None for p in acq.values()) and prem is None:
        return {"kind": "missing", "label": None}
    return {"kind": "split", "acq": acq, "premarket": prem, "label": f"acq+premarket_{month}"}


def load_month_bars(data_root: Path, month: str, days: list[str],
                    tickers: list[str]) -> tuple[pl.DataFrame, dict]:
    """All complete minute bars for `tickers` over `days`, unified to (day,ticker,et,OHLCV)."""
    empty = pl.DataFrame(schema={"day": pl.String, "ticker": pl.String, "et": pl.Int32,
                                 "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
                                 "close": pl.Float64, "volume": pl.Float64,
                                 "bars_source": pl.String})
    plan = resolve_bars_plan(data_root, month, days)
    if plan["kind"] == "missing" or not tickers:
        return empty, plan
    tl = list(tickers)
    lo = datetime.fromisoformat(days[0]).replace(tzinfo=ET, hour=0, minute=0).astimezone(timezone.utc)
    hi = (datetime.fromisoformat(days[-1]).replace(tzinfo=ET) + timedelta(days=1)).astimezone(timezone.utc)
    sel = ["day", "ticker", "et", "open", "high", "low", "close", "volume", "bars_source"]
    frames: list[pl.DataFrame] = []

    def _take(lf, source):
        return (_add_et_day(lf)
                .with_columns(pl.lit(source).alias("bars_source"))
                .select(sel).collect())

    if plan["kind"] == "monthly":
        lf = (pl.scan_parquet(plan["monthly"])
              .filter(pl.col("ticker").is_in(tl)
                      & (pl.col("timestamp") >= lo) & (pl.col("timestamp") < hi)))
        frames.append(_take(lf, plan["label"]))
    else:
        if plan.get("premarket") is not None:
            lf = (pl.scan_parquet(plan["premarket"])
                  .filter(pl.col("ticker").is_in(tl)
                          & (pl.col("timestamp") >= lo) & (pl.col("timestamp") < hi)))
            frames.append(_take(lf, f"premarket_{month}"))
        for _d, p in plan.get("acq", {}).items():
            if p is None:
                continue
            frames.append(_take(pl.scan_parquet(p).filter(pl.col("ticker").is_in(tl)),
                                f"acq_{p.parent.parent.name}"))
    out = pl.concat(frames, how="vertical_relaxed")
    # keep PM strictly before RTH where both sources overlap (premarket wins et<=569)
    out = out.filter(~pl.col("bars_source").str.starts_with("premarket") | (pl.col("et") <= 569))
    out = out.filter(~pl.col("bars_source").str.starts_with("acq") | (pl.col("et") >= 570))
    out = out.filter((pl.col("et") >= 240) & (pl.col("et") <= 1199))
    before = out.height
    out = out.unique(subset=["day", "ticker", "et"], keep="first", maintain_order=True)
    plan["dup_rows_dropped"] = before - out.height
    return out.sort(["day", "ticker", "et"]), plan


# --------------------------------------------------------------------------- universe + anchor

def _uni_cols() -> list[str]:
    return ["ticker", "prev_close_raw", "prev_close_adj", "prev_close_compact_adj", "prev_used_src",
            "prev_close_day", "prev_close_source",
            "split_factor", "split_events", "flag_discrepancy", "flag_nonpos", "pit_listed"]


def _rank_frame(df: pl.DataFrame) -> pl.DataFrame:
    pri = (df.filter(pl.col("primary"))
           .sort(["gain_adj", "ticker"], descending=[True, False])
           .with_row_index("rank", offset=1).select(["ticker", "rank"]))
    allb = (df.filter(pl.col("decision_px").is_not_null() & pl.col("prev_close_raw").is_not_null())
            .sort(["gain_adj", "ticker"], descending=[True, False])
            .with_row_index("rank_all", offset=1).select(["ticker", "rank_all"]))
    return df.join(pri, on="ticker", how="left").join(allb, on="ticker", how="left")


def _flag(reason: tuple) -> pl.Expr:
    return pl.when(pl.col(reason[0])).then(pl.lit(reason[1])).otherwise(pl.lit(None, dtype=pl.String))


def _universe_rows(day: str, clock: int, source: str, df: pl.DataFrame) -> pl.DataFrame:
    extreme = pl.col("gain_adj") > bhs.GAIN_CEIL
    px_bad = pl.col("decision_px") < bhs.PX_FLOOR
    no_prev = pl.col("prev_close_adj").is_null() | (pl.col("prev_close_adj") <= 0)
    d = df.with_columns(
        pl.col("flag_discrepancy").fill_null(False),
        pl.col("flag_nonpos").fill_null(False),
        extreme.alias("flag_extreme_gain"), px_bad.alias("px_floor_bad"), no_prev.alias("no_prev"),
    ).with_columns(
        primary=(~no_prev) & (~pl.col("flag_discrepancy")) & (~pl.col("flag_nonpos"))
                & (~pl.col("flag_extreme_gain")) & (~pl.col("px_floor_bad")),
    )
    d = _rank_frame(d).with_columns(
        exclude_reason=pl.concat_str([
            _flag(("no_prev", "no_prev_close")),
            _flag(("flag_discrepancy", "flag_prevclose_discrepancy")),
            _flag(("flag_nonpos", "flag_nonpositive_px")),
            _flag(("flag_extreme_gain", "extreme_gain_gt_10x")),
            _flag(("px_floor_bad", "px_below_floor")),
        ], separator=";", ignore_nulls=True),
        day=pl.lit(day), clock=pl.lit(clock, dtype=pl.Int32), source=pl.lit(source),
    ).with_columns(
        raw_top5=(pl.col("rank_all") <= 5).fill_null(False),
        action=pl.when(pl.col("primary")).then(pl.lit("primary"))
                 .when(pl.col("no_prev") | pl.col("flag_discrepancy") | pl.col("flag_nonpos"))
                 .then(pl.lit("exclude_confirmed"))
                 .otherwise(pl.lit("action_uncertain")),
    )
    cols = ["day", "clock", "ticker", "decision_px", "decision_et", "gain_adj", "gain_raw",
            "prev_close_raw", "prev_close_adj", "prev_close_day", "prev_close_source",
            "split_factor", "prev_used_src", "flag_discrepancy", "flag_nonpos",
            "flag_extreme_gain", "px_floor_bad", "pit_listed", "primary", "action",
            "raw_top5", "exclude_reason", "rank", "rank_all", "source"]
    return d.select([c for c in cols if c in d.columns])


def anchor_day(day: str, data_root: Path, u: pl.DataFrame, snap: pl.DataFrame | None,
               race571: pl.DataFrame | None) -> tuple[pl.DataFrame, pl.DataFrame]:
    uni: list[pl.DataFrame] = []
    anchors: list[dict] = []
    base = u.select(_uni_cols())

    for C in PM_CLOCKS:
        if snap is None or f"px_{C}" not in snap.columns:
            continue
        j = (snap.rename({"symbol": "ticker"})
             .select(["ticker", f"px_{C}", f"et_{C}", f"fo_{C}", f"fet_{C}"])
             .join(base, on="ticker", how="inner")
             .filter(pl.col(f"px_{C}").is_not_null())
             .with_columns(
                 decision_px=pl.col(f"px_{C}"), decision_et=pl.col(f"et_{C}"),
                 gain_adj=(pl.col(f"px_{C}") / pl.col("prev_close_adj") - 1),
                 gain_raw=(pl.col(f"px_{C}") / pl.col("prev_close_raw") - 1)))
        urows = _universe_rows(day, C, "pm_snapshot", j)
        uni.append(urows)
        top = (urows.filter(pl.col("primary")).sort("rank").head(KEEP_RANKS)
               .join(j.select(["ticker", f"fo_{C}", f"fet_{C}"]), on="ticker", how="left"))
        for r in top.iter_rows(named=True):
            anchors.append({**r, "clock": C, "fo": r.get(f"fo_{C}"), "fet": r.get(f"fet_{C}")})

    if race571 is not None and race571.height:
        j = (race571.select(["ticker", "px", "px_et"]).join(base, on="ticker", how="inner")
             .filter(pl.col("px").is_not_null())
             .with_columns(
                 decision_px=pl.col("px"), decision_et=pl.col("px_et"),
                 gain_adj=(pl.col("px") / pl.col("prev_close_adj") - 1),
                 gain_raw=(pl.col("px") / pl.col("prev_close_raw") - 1)))
        urows = _universe_rows(day, NEAR_OPEN, "minute_full", j)
        uni.append(urows)
        top = urows.filter(pl.col("primary")).sort("rank").head(KEEP_RANKS)
        for r in top.iter_rows(named=True):
            anchors.append({**r, "clock": NEAR_OPEN, "fo": None, "fet": None})

    universe = pl.concat(uni, how="vertical_relaxed") if uni else pl.DataFrame()
    return universe, (pl.DataFrame(anchors) if anchors else pl.DataFrame())


# --------------------------------------------------------------------------- series helpers

def _float_series(vals) -> pl.Series:
    if isinstance(vals, np.ndarray):
        return pl.Series(np.asarray(vals, dtype=np.float64))
    return pl.Series([np.nan if v is None else float(v) for v in vals], dtype=pl.Float64)


def _int_series(vals) -> pl.Series:
    if isinstance(vals, np.ndarray):
        out = [None if not np.isfinite(x) else int(round(float(x))) for x in vals]
    else:
        out = [None if v is None or (isinstance(v, float) and not np.isfinite(v)) else int(v) for v in vals]
    return pl.Series(out, dtype=pl.Int32)


# --------------------------------------------------------------------------- numpy core

def _cum_at(cum: np.ndarray, idx: np.ndarray, n: int) -> np.ndarray:
    return np.where(idx >= 0, cum[np.clip(idx, 0, n - 1)], 0.0)


def _next_valid(valid: np.ndarray) -> np.ndarray:
    n = len(valid)
    return np.minimum.accumulate(np.where(valid, np.arange(n), n)[::-1])[::-1]


def _sliding_extreme(vals: np.ndarray, L: np.ndarray, R: np.ndarray, mode: str = "max") -> np.ndarray:
    """Variable-window max/min with L,R nondecreasing (inclusive). O(n + m)."""
    n = len(vals)
    out = np.full(len(L), np.nan)
    dq: deque[int] = deque()
    j = 0
    for i in range(len(L)):
        l, r = int(L[i]), int(R[i])
        if l < 0 or r < l or l >= n:
            continue
        while j <= r and j < n:
            v = vals[j]
            if not np.isnan(v):
                if mode == "max":
                    while dq and vals[dq[-1]] <= v:
                        dq.pop()
                else:
                    while dq and vals[dq[-1]] >= v:
                        dq.pop()
                dq.append(j)
            j += 1
        while dq and dq[0] < l:
            dq.popleft()
        if dq:
            out[i] = vals[dq[0]]
    return out


_FLOAT_STATE = ("px", "px_age", "ret_fill", "peak_gain", "dd_from_high", "mae_sofar", "nh5", "nh15",
                "nh30", "streak_up", "ret1", "ret3", "ret5", "ret10", "ret15", "v5", "v15", "v30",
                "v60", "dvol5", "dvol15", "vol_med_ratio", "range5", "pm_cum_vol", "pm_high",
                "pm_low", "pm_last_px", "sell_px", "sell_volume", "exit_px", "mark_px", "V1", "V2",
                "V3", "V5", "V10", "V15", "V30", "V60", "V120", "Vclose", "fmfe30", "fmfe60",
                "fmfe120", "fmae30", "fmae60", "fmae120", "mfe_close", "mae_close")
_INT_STATE = ("px_et", "pm_n_bars", "pm_last_et", "sell_et", "exit_et", "mark_et", "bars_since_high",
              "t_peak")


def _member_arrays(bars: dict | None, ts: np.ndarray, entry_et: int | None,
                   entry_px: float | None, status: str) -> dict:
    """Causal state + forward labels for one member over the shared minute grid `ts`."""
    nt = len(ts)
    cols = {c: np.full(nt, np.nan) for c in _FLOAT_STATE}
    cols.update({c: np.full(nt, np.nan) for c in _INT_STATE})
    cols["filled"] = np.zeros(nt, dtype=bool)
    cols["filled_asof"] = np.zeros(nt, dtype=bool)
    cols["censored"] = np.ones(nt, dtype=bool)
    if bars is None or len(bars["et"]) == 0:
        return cols
    ets = bars["et"]
    O = np.where(np.isfinite(bars["open"]) & (bars["open"] > 0), bars["open"], np.nan)
    C = np.where(np.isfinite(bars["close"]) & (bars["close"] > 0), bars["close"], np.nan)
    Hv = np.where(np.isfinite(bars["high"]) & (bars["high"] > 0), bars["high"], -np.inf)
    Lv = np.where(np.isfinite(bars["low"]) & (bars["low"] > 0), bars["low"], np.inf)
    V = np.nan_to_num(bars["volume"], nan=0.0, posinf=0.0, neginf=0.0)
    n = len(ets)
    valid = np.isfinite(O)
    if not valid.any():
        return cols
    nxt = _next_valid(valid)
    se = int(ts[-1])

    j1 = np.searchsorted(ets, ts - 1, side="right") - 1
    jc = np.clip(j1, 0, n - 1)
    px = np.where(j1 >= 0, C[jc], np.nan)
    px_et = np.where(j1 >= 0, ets[jc].astype(float), np.nan)

    filled = np.zeros(nt, dtype=bool)
    filled_asof = np.zeros(nt, dtype=bool)
    entry_i = -1
    if status == "filled" and entry_et is not None and entry_px is not None:
        pos = int(np.searchsorted(ets, entry_et))
        if pos < n and int(ets[pos]) == int(entry_et) and valid[pos]:
            entry_i = pos
            filled = ts >= entry_et
            filled_asof = ts > entry_et
    cols["filled"] = filled
    cols["filled_asof"] = filled_asof
    cols["px"] = px
    cols["px_et"] = px_et
    with np.errstate(invalid="ignore"):
        cols["px_age"] = np.where(np.isnan(px_et), np.nan, ts - px_et)

    if entry_i >= 0:
        Hh = Hv.copy(); Hh[:entry_i] = -np.inf
        Lh = Lv.copy(); Lh[:entry_i] = np.inf
        rh = np.maximum.accumulate(Hh)
        rl = np.minimum.accumulate(Lh)
        is_nh_run = np.zeros(n, dtype=bool); is_nh_run[entry_i] = True
        is_nl_run = np.zeros(n, dtype=bool); is_nl_run[entry_i] = True
        if entry_i + 1 < n:
            is_nh_run[entry_i + 1:] = rh[entry_i + 1:] > rh[entry_i:n - 1]
            is_nl_run[entry_i + 1:] = rl[entry_i + 1:] < rl[entry_i:n - 1]
        h_idx = np.maximum.accumulate(np.where(is_nh_run, np.arange(n), -1))
        geom = j1 >= entry_i
        rh_a = np.where(geom, rh[jc], np.nan)
        rl_a = np.where(geom, rl[jc], np.nan)
        rh_i = np.where(geom, h_idx[jc], -1)
        bsh = np.where(geom & (rh_i >= 0), j1 - rh_i, np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            cols["ret_fill"] = np.where(filled_asof, px / entry_px - 1, np.nan)
            cols["peak_gain"] = np.where(filled_asof, rh_a / entry_px - 1, np.nan)
            cols["dd_from_high"] = np.where(filled_asof, px / rh_a - 1, np.nan)
            cols["mae_sofar"] = np.where(filled_asof, rl_a / entry_px - 1, np.nan)
        cols["bars_since_high"] = np.where(filled_asof, bsh, np.nan)

    # strict new-high cadence (day-anchored), true wall-clock windows
    rh_all = np.maximum.accumulate(Hv)
    is_nh = np.concatenate(([True], rh_all[1:] > rh_all[:-1]))
    cum_nh = np.cumsum(is_nh)
    for k in (5, 15, 30):
        jk = np.searchsorted(ets, ts - 1 - k, side="right") - 1
        cols[f"nh{k}"] = _cum_at(cum_nh, j1, n) - _cum_at(cum_nh, jk, n)

    streak = np.zeros(n, dtype=np.int32)
    for i in range(1, n):
        prev = C[i - 1]
        cur = C[i]
        streak[i] = streak[i - 1] + 1 if (np.isfinite(prev) and np.isfinite(cur) and cur > prev) else 0
    cols["streak_up"] = np.where(j1 >= 0, streak[jc].astype(float), np.nan)

    with np.errstate(invalid="ignore", divide="ignore"):
        for k in (1, 3, 5, 10, 15):
            jk = np.searchsorted(ets, ts - 1 - k, side="right") - 1
            base = np.where(jk >= 0, C[np.clip(jk, 0, n - 1)], np.nan)
            cols[f"ret{k}"] = np.where(filled_asof & (jk >= 0), px / base - 1, np.nan)

    dvol = V * np.nan_to_num(C, nan=0.0)
    cum_v = np.cumsum(V)
    cum_dv = np.cumsum(dvol)
    rng = np.where(np.isfinite(Hv) & np.isfinite(Lv) & np.isfinite(C) & (C > 0),
                   (Hv - Lv) / C, np.nan)
    cum_r = np.cumsum(np.nan_to_num(rng, nan=0.0))
    for k in (5, 15, 30, 60):
        jk = np.searchsorted(ets, ts - 1 - k, side="right") - 1
        cols[f"v{k}"] = _cum_at(cum_v, j1, n) - _cum_at(cum_v, jk, n)
    for k in (5, 15):
        jk = np.searchsorted(ets, ts - 1 - k, side="right") - 1
        cols[f"dvol{k}"] = _cum_at(cum_dv, j1, n) - _cum_at(cum_dv, jk, n)
        if k == 5:
            num = _cum_at(cum_r, j1, n) - _cum_at(cum_r, jk, n)
            den = _cum_at(np.arange(1, n + 1, dtype=float), j1, n) - _cum_at(
                np.arange(1, n + 1, dtype=float), jk, n)
            cols["range5"] = np.divide(num, den, out=np.full(nt, np.nan), where=den > 0)
    dv5 = pl.Series(cols["dvol5"])
    med = dv5.rolling_median(window_size=20, min_samples=20).shift(1).to_numpy()
    cols["vol_med_ratio"] = np.divide(cols["dvol5"], med, out=np.full(nt, np.nan),
                                      where=(med > 0) & np.isfinite(med))

    # causal as-of PM own history (et <= 569), frozen after the open
    pm = ets <= 569
    pm_cnt = np.cumsum(pm.astype(np.int64))
    pm_v = np.cumsum(np.where(pm, V, 0.0))
    pm_hi = np.maximum.accumulate(np.where(pm, Hv, -np.inf))
    pm_lo = np.minimum.accumulate(np.where(pm, Lv, np.inf))
    cols["pm_n_bars"] = _cum_at(pm_cnt.astype(float), j1, n)
    cols["pm_cum_vol"] = _cum_at(pm_v, j1, n)
    ph = np.where(j1 >= 0, pm_hi[jc], np.nan)
    cols["pm_high"] = np.where(np.isfinite(ph), ph, np.nan)
    pld = np.where(j1 >= 0, pm_lo[jc], np.nan)
    cols["pm_low"] = np.where(np.isfinite(pld), pld, np.nan)
    jpm = np.searchsorted(ets, np.minimum(ts - 1, 569), side="right") - 1
    cols["pm_last_px"] = np.where(jpm >= 0, C[np.clip(jpm, 0, n - 1)], np.nan)
    cols["pm_last_et"] = np.where(jpm >= 0, ets[np.clip(jpm, 0, n - 1)].astype(float), np.nan)

    # ---- labels: executable from observed tape only
    i0 = np.searchsorted(ets, ts, side="left")
    k0 = np.where(i0 < n, nxt[np.clip(i0, 0, n - 1)], n)
    ok = k0 < n
    kc = np.clip(k0, 0, n - 1)
    sell_px = np.where(ok, O[kc], np.nan)
    sell_et = np.where(ok, ets[kc].astype(float), np.nan)
    cols["sell_px"] = sell_px
    cols["sell_et"] = sell_et
    cols["sell_volume"] = np.where(ok, V[kc], np.nan)
    cols["censored"] = ~ok
    # terminal executable open at/after session_end (UNKNOWN if none); mark = last close
    ie = int(np.searchsorted(ets, se, side="left"))
    ke = int(nxt[ie]) if ie < n else n
    cols["exit_px"] = np.full(nt, O[ke] if ke < n else np.nan)
    cols["exit_et"] = np.full(nt, float(ets[ke]) if ke < n else np.nan)
    cols["mark_px"] = np.full(nt, C[n - 1])
    cols["mark_et"] = np.full(nt, float(ets[n - 1]))
    with np.errstate(invalid="ignore", divide="ignore"):
        for h in HORIZONS:
            ih = np.searchsorted(ets, ts + h, side="left")
            kh = np.where(ih < n, nxt[np.clip(ih, 0, n - 1)], n)
            okh = ok & (kh < n) & (sell_et <= (ts + h))
            hp = np.where(okh, O[np.clip(kh, 0, n - 1)], np.nan)
            cols[f"V{h}"] = np.where(okh, hp / sell_px - 1, np.nan)
        hmax = ts + 0
        for h in TAIL_H:
            hmax = ts + h
            complete = ok & (hmax <= se) & (sell_et <= hmax)
            R = np.minimum(np.searchsorted(ets, hmax, side="right") - 1, n - 1)
            fh = _sliding_extreme(Hv, kc, R, "max")
            fl = _sliding_extreme(Lv, kc, R, "min")
            cols[f"fmfe{h}"] = np.where(complete, fh / sell_px - 1, np.nan)
            cols[f"fmae{h}"] = np.where(complete, fl / sell_px - 1, np.nan)
        sufmax = np.maximum.accumulate(Hv[::-1])[::-1]
        sufmin = np.minimum.accumulate(Lv[::-1])[::-1]
        sufarg = np.empty(n, dtype=np.int64)
        mi = n - 1
        for i in range(n - 1, -1, -1):
            if Hv[i] > Hv[mi]:
                mi = i
            sufarg[i] = mi
        cols["mfe_close"] = np.where(ok, sufmax[kc] / sell_px - 1, np.nan)
        cols["mae_close"] = np.where(ok, sufmin[kc] / sell_px - 1, np.nan)
        cols["t_peak"] = np.where(ok, ets[sufarg[kc]].astype(float) - ts, np.nan)
        cols["Vclose"] = np.where(ok & np.isfinite(cols["exit_px"]),
                                  cols["exit_px"] / sell_px - 1, np.nan)
    return cols


def _race_arrays(rm: dict, ts: np.ndarray) -> dict:
    nt = len(ts)
    out = {c: np.full(nt, np.nan) for c in (
        "race_rank", "race_rank_fresh", "race_gain", "race_n_known", "race_n_eligible",
        "race_age_min", "race_fresh2", "drank5", "drank15")}
    for ti, t in enumerate(ts):
        t = int(t)
        r = rm.get(t)
        if r:
            for dst, src in (("race_rank", "rank_known"), ("race_rank_fresh", "rank_fresh_2m"),
                             ("race_gain", "gain"), ("race_n_known", "n_known"),
                             ("race_n_eligible", "n_eligible"), ("race_age_min", "age_min")):
                v = r.get(src)
                if v is not None:
                    out[dst][ti] = float(v)
            out["race_fresh2"][ti] = 1.0 if r.get("fresh_2m") else 0.0
        r5 = rm.get(t - 5); r15 = rm.get(t - 15)
        if r and r5 and r.get("rank_known") is not None and r5.get("rank_known") is not None:
            out["drank5"][ti] = r["rank_known"] - r5["rank_known"]
        if r and r15 and r.get("rank_known") is not None and r15.get("rank_known") is not None:
            out["drank15"][ti] = r["rank_known"] - r15["rank_known"]
    return out


def _peer_stats(ret_mat: np.ndarray, dvol_mat: np.ndarray) -> tuple[list[dict], np.ndarray]:
    """Peer distributions excluding self; not-yet-filled/blocked peers contribute cash (0)."""
    k, nt = ret_mat.shape
    valid = ~np.isnan(ret_mat)
    cnt_all = valid.sum(axis=0)
    sum_all = np.where(valid, ret_mat, 0.0).sum(axis=0)
    above_all = (ret_mat > 0).sum(axis=0)
    peer: list[dict] = []
    for i in range(k):
        own = ret_mat[i]; own_ok = valid[i]
        pcnt = cnt_all - own_ok
        psum = sum_all - np.where(own_ok, own, 0.0)
        mean = np.where(pcnt > 0, psum / np.maximum(pcnt, 1), np.nan)
        other = np.delete(ret_mat, i, axis=0)
        dl = np.delete(dvol_mat, i, axis=0)
        if other.shape[0] >= 1:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                q25 = np.nanpercentile(other, 25, axis=0)
                q50 = np.nanpercentile(other, 50, axis=0)
                q75 = np.nanpercentile(other, 75, axis=0)
                dv = np.nanmean(dl, axis=0)
            q25 = np.where(pcnt > 0, q25, np.nan)
            q50 = np.where(pcnt > 0, q50, np.nan)
            q75 = np.where(pcnt > 0, q75, np.nan)
            dv = np.where(np.isfinite(dv), dv, np.nan)
        else:
            q25 = q50 = q75 = np.full(nt, np.nan)
            dv = np.full(nt, np.nan)
        peer.append({"n_peers": pcnt.astype(float), "sib_ret_mean": mean, "peer_ret_p25": q25,
                     "peer_ret_p50": q50, "peer_ret_p75": q75,
                     "sib_above_fill": (above_all - (own > 0)).astype(float),
                     "peer_dvol5_mean": dv})
    basket = np.where(cnt_all > 0, sum_all / np.maximum(cnt_all, 1), np.nan)
    return peer, basket


def roster_labels(bars: dict | None, entry_px: float | None, entry_et: int | None,
                  status: str, se: int) -> dict:
    out = {f"R{h}": None for h in HORIZONS}
    out.update({"Rclose": None, "mark_px": None, "mark_et": None, "mfe_day": None, "mae_day": None,
                "t_peak_day": None, "censored_day": None})
    if bars is None or len(bars["et"]) == 0:
        return out
    ets = bars["et"]
    O = np.where(np.isfinite(bars["open"]) & (bars["open"] > 0), bars["open"], np.nan)
    C = np.where(np.isfinite(bars["close"]) & (bars["close"] > 0), bars["close"], np.nan)
    Hv = np.where(np.isfinite(bars["high"]) & (bars["high"] > 0), bars["high"], -np.inf)
    Lv = np.where(np.isfinite(bars["low"]) & (bars["low"] > 0), bars["low"], np.inf)
    n = len(ets)
    out["mark_px"] = float(C[n - 1]) if np.isfinite(C[n - 1]) else None
    out["mark_et"] = float(ets[n - 1])
    # executable terminal exit (open of first valid bar et>=session_end), UNKNOWN otherwise
    ie = int(np.searchsorted(ets, se, side="left"))
    nxt = _next_valid(np.isfinite(O))
    ke = int(nxt[ie]) if ie < n else n
    out["censored_day"] = not (0 <= ke < n)
    if entry_px is None or status != "filled":
        return out
    fi = int(np.searchsorted(ets, entry_et))
    if fi >= n:
        return out
    for h in HORIZONS:
        ih = int(np.searchsorted(ets, entry_et + h))
        k = nxt[ih] if ih < n else n
        if k < n and np.isfinite(O[k]):
            out[f"R{h}"] = float(O[k] / entry_px - 1)
    if ke < n:
        out["Rclose"] = float(O[ke] / entry_px - 1)
    seg_h = Hv[fi:]; seg_l = Lv[fi:]
    if np.isfinite(seg_h).any():
        out["mfe_day"] = float(np.max(seg_h) / entry_px - 1)
        out["t_peak_day"] = float(ets[fi + int(np.argmax(seg_h))] - entry_et)
    if np.isfinite(seg_l).any():
        out["mae_day"] = float(np.min(seg_l) / entry_px - 1)
    return out


def resolve_exec(bars: dict | None, clock: int) -> tuple[str, int | None, float | None, int | None, float | None]:
    """First valid open with et >= clock. Returns (status, fill_et, fill_px, gap, fill_volume)."""
    if bars is None or len(bars["et"]) == 0:
        return ("missing", None, None, None, None)
    ets = bars["et"]; O = bars["open"]
    n = len(ets)
    i = int(np.searchsorted(ets, clock))
    while i < n and not (np.isfinite(O[i]) and O[i] > 0):
        i += 1
    if i >= n:
        return ("missing", None, None, None, None)
    gap = int(ets[i] - clock)
    if gap >= GAP_MAX:
        return ("blocked", int(ets[i]), None, gap, None)
    return ("filled", int(ets[i]), float(O[i]), gap, float(bars["volume"][i]))


# --------------------------------------------------------------------------- day processing

def process_day(day: str, month: pl.DataFrame, day_universe: pl.DataFrame,
                anchors: list[dict], cal: dict, data_root: Path, bars_plan: dict,
                force: bool = False) -> tuple[int, int, int]:
    v2 = data_root.joinpath(*V2_SUB)
    uni_p = v2 / "universe" / f"{day}.parquet"
    ros_p = v2 / "roster" / f"{day}.parquet"
    pan_p = v2 / "panel" / f"{day}.parquet"
    done_p = v2 / "_done" / f"{day}.json"
    input_id = _input_id(day, cal, bars_plan, anchors)
    if done_p.exists() and not force:
        try:
            m = json.loads(done_p.read_text())
            if m.get("schema_version") == SCHEMA_VERSION and m.get("inputs_sha256") == input_id:
                return (0, 0, 0)
        except Exception:
            pass

    se = cal[day]
    day_bars = month.filter((pl.col("day") == day) & (pl.col("et") <= se))
    bmap: dict[str, dict] = {}
    if day_bars.height:
        for (tk,), sub in day_bars.group_by(["ticker"], maintain_order=True):
            s = sub.sort("et")
            bmap[str(tk)] = {c: np.asarray(s[c].to_numpy(), dtype=np.float64)
                             for c in ("et", "open", "high", "low", "close", "volume")}
    bars_src_str = (",".join(sorted(set(day_bars["bars_source"].to_list())))
                    if day_bars.height else None)
    bars_bad_rows = 0
    if day_bars.height:
        bad = day_bars.filter(
            ~(pl.col("open").is_finite() & (pl.col("open") > 0))
            | ~pl.col("high").is_finite() | ~pl.col("low").is_finite()
            | ~pl.col("close").is_finite() | (pl.col("high") < pl.col("low")))
        bars_bad_rows = bad.height

    race_map: dict[str, dict[int, dict]] = {}
    if anchors:
        tickers = sorted({a["ticker"] for a in anchors})
        rf = (pl.scan_parquet(data_root / "atlas" / "observation" / "v0" / "race.minute_full"
                              / f"month={day[:7]}" / f"{day}.parquet")
              .filter(pl.col("ticker").is_in(tickers))
              .select(["t", "ticker", "rank_known", "rank_fresh_2m", "gain", "n_known",
                       "n_eligible", "age_min", "fresh_2m"]).collect())
        for r in rf.iter_rows(named=True):
            race_map.setdefault(r["ticker"], {})[int(r["t"])] = r

    panel_frames: list[pl.DataFrame] = []
    roster_rows: list[dict] = []
    for clock in CLOCKS:
        mem = [a for a in anchors if a["clock"] == clock]
        if not mem:
            continue
        ts = np.arange(clock, se + 1, dtype=np.int64)
        nt = len(ts)
        states: list[dict] = []
        ret_mat = np.full((len(mem), nt), np.nan)
        dvol_mat = np.full((len(mem), nt), np.nan)
        for mi, a in enumerate(mem):
            b = bmap.get(a["ticker"])
            status, fill_et, fill_px, gap, fill_vol = resolve_exec(b, clock)
            st = _member_arrays(b, ts, fill_et, fill_px, status)
            if status == "blocked":
                contrib = np.zeros(nt)
            elif status == "filled":
                contrib = np.where(st["filled_asof"], st["ret_fill"], 0.0)
            else:
                contrib = np.full(nt, np.nan)
            ret_mat[mi] = contrib
            dvol_mat[mi] = st["dvol5"]
            states.append({"a": a, "status": status, "fill_et": fill_et, "fill_px": fill_px,
                           "gap": gap, "fill_vol": fill_vol, "st": st})
            rr = {**{k: a.get(k) for k in (
                "day", "clock", "rank", "ticker", "decision_px", "decision_et", "gain_adj",
                "gain_raw", "prev_close_adj", "prev_close_raw", "split_factor", "prev_used_src",
                "prev_close_source", "flag_discrepancy", "flag_nonpos", "flag_extreme_gain",
                "px_floor_bad", "pit_listed", "rank_all", "source", "exclude_reason")},
                "fill_et": fill_et, "fill_px": fill_px, "fill_volume": fill_vol, "status": status,
                "entry_gap_min": gap, "bars_source": bars_src_str, "session_end": se}
            if clock in PM_CLOCKS:
                fo = a.get("fo")
                rr["fill_crosscheck"] = (None if (fill_px is None or fo is None)
                                         else bool(abs(fill_px - fo) < 1e-6))
            rr.update(roster_labels(b, fill_px, fill_et, status, se))
            roster_rows.append(rr)

        peer, basket = _peer_stats(ret_mat, dvol_mat)
        for mi, s in enumerate(states):
            a = s["a"]; st = s["st"]; tkr = a["ticker"]; p = peer[mi]
            with np.errstate(invalid="ignore", divide="ignore"):
                rel_dv = np.divide(st["dvol5"], p["peer_dvol5_mean"], out=np.full(nt, np.nan),
                                   where=(p["peer_dvol5_mean"] > 0) & np.isfinite(p["peer_dvol5_mean"]))
            d = {
                "day": pl.Series([day] * nt, dtype=pl.String),
                "clock": pl.Series([clock] * nt, dtype=pl.Int32),
                "rank": pl.Series([a["rank"]] * nt, dtype=pl.Int32),
                "ticker": pl.Series([tkr] * nt, dtype=pl.String),
                "source": pl.Series([a.get("source")] * nt, dtype=pl.String),
                "status": pl.Series([s["status"]] * nt, dtype=pl.String),
                "entry_et": _int_series([s["fill_et"]] * nt),
                "entry_px": _float_series([s["fill_px"]] * nt),
                "fill_et": _int_series([s["fill_et"]] * nt),
                "fill_px": _float_series([s["fill_px"]] * nt),
                "fill_volume": _float_series([s["fill_vol"]] * nt),
                "bars_source": pl.Series([bars_src_str] * nt, dtype=pl.String),
                "session_end": pl.Series([se] * nt, dtype=pl.Int32),
                "t": pl.Series(ts, dtype=pl.Int32),
                "tenure": pl.Series(ts - clock, dtype=pl.Int32),
                "own_decision_gain": _float_series([a.get("gain_adj")] * nt),
                "rank_all": _int_series([a.get("rank_all")] * nt),
                "filled": pl.Series(st["filled"], dtype=pl.Boolean),
                "filled_asof": pl.Series(st["filled_asof"], dtype=pl.Boolean),
                "censored": pl.Series(st["censored"], dtype=pl.Boolean),
                "basket_ret": _float_series(basket),
                "rel_dvol5": _float_series(rel_dv),
            }
            for c in _FLOAT_STATE:
                d[c] = _float_series(st[c])
            for c in _INT_STATE:
                d[c] = _int_series(st[c])
            for c, v in _race_arrays(race_map.get(tkr, {}), ts).items():
                d[c] = _int_series(v) if c in ("race_rank", "race_rank_fresh", "race_n_known",
                                               "race_n_eligible", "race_age_min") else _float_series(v)
            for c in ("n_peers", "sib_ret_mean", "sib_above_fill", "peer_ret_p25", "peer_ret_p50",
                      "peer_ret_p75", "peer_dvol5_mean"):
                d[c] = _float_series(p[c])
            panel_frames.append(pl.DataFrame(d))

    n_roster = n_panel = 0
    if panel_frames:
        pan = pl.concat(panel_frames, how="vertical_relaxed")
        n_panel = pan.height
        _atomic_parquet(pan, pan_p)
    if roster_rows:
        ros = pl.DataFrame(roster_rows, infer_schema_length=None)
        n_roster = ros.height
        _atomic_parquet(ros, ros_p)
    if day_universe.height:
        _atomic_parquet(day_universe, uni_p)
    status = "ok" if roster_rows else "empty"
    raw_top5_excluded: list[dict] = []
    if day_universe.height and "raw_top5" in day_universe.columns:
        raw_top5_excluded = (day_universe.filter(pl.col("raw_top5") & ~pl.col("primary"))
                             .sort(["clock", "rank_all"])
                             .select(["clock", "rank_all", "ticker", "gain_adj", "exclude_reason",
                                      "action", "prev_close_source", "flag_discrepancy"])
                             .to_dicts())
    _atomic_json({"day": day, "status": status, "schema_version": SCHEMA_VERSION,
                  "inputs_sha256": input_id, "rows_universe": int(day_universe.height),
                  "rows_roster": n_roster, "rows_panel": n_panel,
                  "raw_top5_excluded": raw_top5_excluded, "bars_bad_rows": int(bars_bad_rows),
                  "bars_source": bars_src_str, "session_end": se}, done_p)
    return (int(day_universe.height), n_roster, n_panel)


def _plan_id(bars_plan: dict) -> str:
    parts = [bars_plan.get("label") or ""]

    def _id(p):
        try:
            st = Path(p).stat()
            return f"{Path(p).name}:{st.st_size}:{int(st.st_mtime)}"
        except OSError:
            return f"{Path(p).name}:absent"
    if bars_plan.get("monthly"):
        parts.append(_id(bars_plan["monthly"]))
    for _d, p in (bars_plan.get("acq") or {}).items():
        if p is not None:
            parts.append(_id(p))
    if bars_plan.get("premarket"):
        parts.append(_id(bars_plan["premarket"]))
    return "|".join(parts)


def _input_id(day: str, cal: dict, bars_plan: dict, anchors: list[dict]) -> str:
    parts = [day, SCHEMA_VERSION, str(cal[day]), _plan_id(bars_plan)]
    parts += [f"{a['clock']}:{a['rank']}:{a['ticker']}" for a in anchors]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _atomic_parquet(df: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.write_parquet(tmp)
    os.replace(tmp, path)


def _atomic_json(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    os.replace(tmp, path)


# --------------------------------------------------------------------------- month / main

def build_month(month: str, days: list[str], data_root: Path, cal: dict,
                force: bool) -> tuple[str, int, int, int]:
    split = read_split(data_root)
    if split.get("calendar_sha256") and split["calendar_sha256"] != calendar_sha256():
        raise SystemExit("split.json calendar_sha256 does not match calendar; refusing to proceed")
    splits = bhs.split_factor_frame(data_root)
    prev_map = bhs.prev_session_map(data_root)
    anchors_per_day: dict[str, list[dict]] = {}
    uni_per_day: dict[str, pl.DataFrame] = {}
    tickers: set[str] = set()
    for day in days:
        prev_day = prev_map.get(day)
        if prev_day is None:
            anchors_per_day[day] = []
            uni_per_day[day] = pl.DataFrame()
            continue
        u = bhs.build_universe(day, prev_day, splits, data_root)
        snap_p = data_root / "sip" / "pm_snapshots" / f"{day}.parquet"
        snap = pl.read_parquet(snap_p) if snap_p.exists() else None
        rp = (data_root / "atlas" / "observation" / "v0" / "race.minute_full"
              / f"month={month}" / f"{day}.parquet")
        race571 = None
        if rp.exists():
            race571 = (pl.scan_parquet(rp)
                       .filter((pl.col("t") == NEAR_OPEN) & pl.col("known_by_t")
                               & pl.col("px").is_not_null())
                       .select(["ticker", "px", "px_et"]).collect())
        uni, anc = anchor_day(day, data_root, u, snap, race571)
        uni_per_day[day] = uni
        anchors_per_day[day] = anc.to_dicts() if anc.height else []
        for a in anchors_per_day[day]:
            tickers.add(a["ticker"])
    month_bars, bars_plan = load_month_bars(data_root, month, days, sorted(tickers))
    tot_u = tot_r = tot_p = 0
    for day in days:
        nu, nr, npp = process_day(day, month_bars, uni_per_day.get(day, pl.DataFrame()),
                                  anchors_per_day.get(day, []), cal, data_root, bars_plan, force)
        tot_u += nu; tot_r += nr; tot_p += npp
    return (month, tot_u, tot_r, tot_p)


def _w_month(a):
    month, days, data_root, cal, force = a
    return build_month(month, list(days), Path(data_root), cal, force)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="")
    ap.add_argument("--months", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    data_root = bps.resolve_data_root(args.data_root)
    v2 = data_root.joinpath(*V2_SUB)
    for sub in ("universe", "roster", "panel", "_done"):
        (v2 / sub).mkdir(parents=True, exist_ok=True)
    cal = calendar(data_root)
    all_days = sorted(cal)
    if args.days:
        days = [d for d in all_days if d in set(args.days.split(","))]
    elif args.months:
        ms = set(args.months.split(","))
        days = [d for d in all_days if d[:7] in ms]
    elif args.all:
        days = all_days
    else:
        days = all_days[:1]
    split = read_split(data_root)
    if split:
        exp = set(split.get("discovery_days", [])) | set(split.get("validation_days", []))
        extra = [d for d in days if d and d not in exp]
        if extra:
            print(f"WARNING: {len(extra)} requested days not in split.json (e.g. {extra[:3]})", flush=True)
    t0 = time.time()
    by_month: dict[str, list[str]] = {}
    for d in days:
        by_month.setdefault(d[:7], []).append(d)
    tasks = [(m, ds, str(data_root), cal, args.force) for m, ds in sorted(by_month.items())]
    tot_u = tot_r = tot_p = 0
    if args.workers > 1:
        import multiprocessing as mp
        with mp.get_context("spawn").Pool(processes=args.workers) as pool:
            for i, (m, nu, nr, np_) in enumerate(pool.imap_unordered(_w_month, tasks), 1):
                tot_u += nu; tot_r += nr; tot_p += np_
                print(f"  month {m}: universe={nu} roster={nr} panel={np_} [{i}/{len(tasks)}] {time.time()-t0:.0f}s", flush=True)
    else:
        for i, t in enumerate(tasks, 1):
            m, nu, nr, np_ = _w_month(t)
            tot_u += nu; tot_r += nr; tot_p += np_
            print(f"  month {m}: universe={nu} roster={nr} panel={np_} [{i}/{len(tasks)}] {time.time()-t0:.0f}s", flush=True)
    _write_manifest(data_root, days, by_month, cal)
    print(f"lifecycle_build v2 done: days={len(days)} universe={tot_u} roster={tot_r} "
          f"panel={tot_p} el={time.time()-t0:.0f}s")
    return 0


def _file_id(p: Path) -> dict:
    if not p.exists():
        return {"path": str(p), "exists": False}
    st = p.stat()
    return {"path": str(p), "exists": True, "bytes": st.st_size, "mtime": int(st.st_mtime)}


def _write_manifest(data_root: Path, days: list[str], by_month: dict, cal: dict) -> None:
    v2 = data_root.joinpath(*V2_SUB)
    man = {
        "study": "LIFECYCLE-01", "schema_version": SCHEMA_VERSION, "clocks": list(CLOCKS),
        "keep_ranks": KEEP_RANKS, "gap_max_min": GAP_MAX, "horizons": list(HORIZONS),
        "tail_h": list(TAIL_H),
        "anchor": "gain_vs_prev_actual_session_close_split_normalized",
        "universe": "harvest01 primary same-anchor (local KEEP_RANKS=5; excluded rows retained)",
        "labels": "executable observed opens only; exit open>=session_end; mark=last close; no close fallback",
        "bars_policy": ("local RAW monthly ohlcv (PM+RTH, matches race px); 2025-02 = "
                        "atlas acquisition v4 RTH + backfill premarket"),
        "days_requested": len(days), "months": sorted(by_month), "n_days_calendar": len(cal),
        "sources": {
            "calendar": {"path": str(bps.CALENDAR), "sha256": calendar_sha256()},
            "splits": _file_id(data_root / "harvest01" / "base" / "splits.parquet"),
            "universe_index_rth": _file_id(data_root / "sip" / "universe" / "index_rth.jsonl"),
        },
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_json(man, v2 / "manifest.json")


if __name__ == "__main__":
    raise SystemExit(main())
