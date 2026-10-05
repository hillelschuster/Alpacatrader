#!/usr/bin/env python
"""ENTRY-EV-01 Stage-A producer: per-day entry-event tables (gross, pre-cost).

Reads only the DISCOVERY block of PRE-REG-ENTRY-EV-01 (2021-02-01 .. 2023-03-14);
protected / sealed / reserved days are refused by an assertion before any file is
opened.

Inputs (READ ONLY)
  board : data/atlas/observation/v0/race.minute_full/month=YYYY-MM/<day>.parquet
          rows with rank_known <= 10 and t in 570..959 (RTH decision minutes).
  lane  : data/ohlcv_<month>.parquet          <- FE-0 pinned price/volume lane
          [timestamp(ns,UTC), ticker, open, high, low, close, volume]
          etm = timestamp.dt.convert_time_zone("America/New_York") then
          hour.cast(Int32)*60 + minute.cast(Int32)   (the CAST is load-bearing:
          uncast it overflows i8 and silently corrupts etm), restricted to
          etm 570..959 and to etm <= session_end of the day.

Semantics (all causal; decision at t uses bars et <= t-1 only)
  entry      : open of the first lane bar with et >= t   (never the bar at t-1)
  exit(h)    : open of the first lane bar with et >= t+h
  fwd_ret_h  : exit_open / entry_open - 1                (gross, no costs)
               null (UNKNOWN, never 0) when the entry open or the exit open is
               unavailable, or when t+h > session_end (end-of-day censored).
  mae/mfe_h  : min(low) / max(high) over the lane bars stamped et in
               [entry_et, exit_et) -- the entry minute is inside the path, the
               exit minute is not -- divided by entry_open, minus 1.
               null when the path is not complete (no entry or no exit bar).
  vol30_ratio: (V(t) - V(t-30)) / (V(t-30) - V(t-60)) where V(e) is the lane
               volume cumulated over bars et <= e-1; null when the denominator
               is <= 0.
  ret_k      : px(t)/px(t-k) - 1 on the board's own px column (dense minute grid).
  dd_from_high: px(t)/cummax_px(t) - 1, cummax over the board px of the same
               ticker from the open of the session to t.
  promo_age  : t - (first minute of the day with rank_known <= 5 for the
               ticker); null when the ticker never reached rank <= 5.  Signed on
               purpose: a negative value marks an event that PRECEDES that
               ticker's first top-5 minute.

Guard (AMV class, FE-0 §8): rows are dropped when prev_close is null,
prev_close < 1.00, prev_close_stale, prev_close_floor_qualified, or
flag_prevclose_discrepancy.  Per-reason counts are reported per day (a row can
fail several reasons).  flag_extreme_gain rows are REPORTED, never dropped.

Output (default data/entry_ev/stage_a, i.e. <data-root>/entry_ev/stage_a):
  <day>.parquet        one row per surviving (day, t, ticker) board event
  <day>.manifest.json  provenance, guard counts, coverage/censor counts, runtime
  <day>_done           marker written last; its presence means the day is done
  Resume skips any day whose <day>_done marker exists (unless --force).

Usage
  python factory/scripts/entry_ev/stage_a_build.py --days 2021-03-01,2021-03-02
  python factory/scripts/entry_ev/stage_a_build.py --months 2021-03
  python factory/scripts/entry_ev/stage_a_build.py --out /tmp/stage_a_smoke
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

# --------------------------------------------------------------------------- #
# Block guard.  Every day and every month is asserted into the discovery block
# before a path is opened or a file is written.
# --------------------------------------------------------------------------- #
DISCOVERY_START = dt.date(2021, 2, 1)
DISCOVERY_END = dt.date(2023, 3, 14)

ROOT = Path(__file__).resolve().parents[3]
ET = ZoneInfo("America/New_York")

T0 = 570                      # 09:30 ET, inclusive
T1 = 959                      # 16:00 ET stamp, inclusive
NG = T1 - T0 + 1              # 390 minute slots
HORIZONS = (1, 3, 5, 10, 15, 30, 60)
PATH_HORIZONS = (5, 15, 30)
RET_LAGS = (1, 3, 5, 15)
VOL_WINDOWS = (30, 60)

BOARD_REL = Path("atlas/observation/v0/race.minute_full")

BOARD_COLS = [
    "day", "t", "ticker", "px", "px_et", "rank_known", "gain", "prev_close",
    "prev_close_stale", "prev_close_floor_qualified", "flag_prevclose_discrepancy",
    "flag_extreme_gain", "session_end",
]
LANE_COLS = ["timestamp", "ticker", "open", "high", "low", "close", "volume"]

GUARD_REASONS = (
    "prev_close_null",
    "prev_close_lt_1",
    "prev_close_stale",
    "prev_close_floor_qualified",
    "flag_prevclose_discrepancy",
)

OUT_SCHEMA: dict[str, pl.DataType] = {
    "day": pl.Utf8,
    "t": pl.Int32,
    "ticker": pl.Utf8,
    "rank_known": pl.Int32,
    "gain": pl.Float64,
    "px": pl.Float64,
    "px_et": pl.Int32,
    "prev_close": pl.Float64,
    "flag_extreme_gain": pl.Boolean,
    "promo_age": pl.Int32,
    "ret1": pl.Float64,
    "ret3": pl.Float64,
    "ret5": pl.Float64,
    "ret15": pl.Float64,
    "dd_from_high": pl.Float64,
    "vol30_ratio": pl.Float64,
    "entry_et": pl.Int32,
    "entry_open": pl.Float64,
    **{f"fwd_ret_{h}": pl.Float64 for h in HORIZONS},
    **{f"mae_{h}": pl.Float64 for h in PATH_HORIZONS},
    **{f"mfe_{h}": pl.Float64 for h in PATH_HORIZONS},
}


def assert_in_block(day: str) -> dt.date:
    """Assert `day` (YYYY-MM-DD) is inside the discovery block.  No exceptions."""
    d = dt.date.fromisoformat(day)
    assert DISCOVERY_START <= d <= DISCOVERY_END, (
        f"day {day} is outside the ENTRY-EV-01 discovery block "
        f"{DISCOVERY_START}..{DISCOVERY_END}: protected / sealed / reserved data "
        f"is not read by this producer"
    )
    return d


def assert_month_in_block(month: str) -> None:
    y, m = (int(x) for x in month.split("-"))
    assert DISCOVERY_START <= dt.date(y, m, 1) <= DISCOVERY_END, (
        f"month {month} is outside the discovery block {DISCOVERY_START}..{DISCOVERY_END}"
    )


def board_dir(data_root: Path) -> Path:
    p = data_root / BOARD_REL
    assert p.is_dir(), f"board directory not found: {p}"
    return p


def board_path(data_root: Path, day: str) -> Path:
    d = assert_in_block(day)
    p = board_dir(data_root) / f"month={d:%Y-%m}" / f"{day}.parquet"
    assert p.is_file(), f"board day file missing: {p}"
    return p


def lane_path(data_root: Path, day: str) -> Path:
    d = assert_in_block(day)
    assert_month_in_block(f"{d:%Y-%m}")
    p = data_root / f"ohlcv_{d:%Y-%m}.parquet"
    assert p.is_file(), f"FE-0 lane file missing: {p}"
    return p


def resolve_data_root(explicit: Path | None = None) -> Path:
    """First candidate that actually holds the board wins; never guessed further."""
    cands: list[Path] = []
    if explicit is not None:
        cands.append(Path(explicit))
    env = os.environ.get("ENTRY_EV_DATA_ROOT")
    if env:
        cands.append(Path(env))
    cands.append(ROOT / "data")
    cands.append(Path("/home/hillel/projects/Alpacatrader/data"))
    for c in cands:
        if (c / BOARD_REL).is_dir():
            return c
    raise FileNotFoundError(
        "board not found under any of: " + ", ".join(str(c) for c in cands)
    )


# --------------------------------------------------------------------------- #
# Numeric kernels (pure; unit-tested in test_stage_a_build.py)
# --------------------------------------------------------------------------- #
def first_bar_at_or_after(present_idx: np.ndarray, targets: np.ndarray) -> np.ndarray:
    """Grid index of the first present slot >= target, -1 where there is none.

    `present_idx` are ascending indices into the 570..959 slot grid.
    """
    n = present_idx.size
    if n == 0:
        return np.full(targets.shape, -1, dtype=np.int64)
    j = np.searchsorted(present_idx, targets, side="left")
    ok = j < n
    return np.where(ok, present_idx[np.clip(j, 0, n - 1)], -1).astype(np.int64)


def _sparse_stack(f: np.ndarray, op, fill: float) -> np.ndarray:
    """Sparse table of range extrema, level k = op over 2**k slots starting at i."""
    n = f.size
    rows = [f]
    length = 1
    while length * 2 <= n:
        prev = rows[-1]
        m = n - 2 * length + 1
        cur = op(prev[:m], prev[length:length + m])
        rows.append(np.concatenate([cur, np.full(n - cur.size, fill)]))
        length *= 2
    return np.vstack(rows)


def _LOG2() -> np.ndarray:
    t = np.zeros(NG + 1, dtype=np.int64)
    for i in range(2, NG + 1):
        t[i] = t[i // 2] + 1
    return t


LOG2 = _LOG2()


def range_extremes(lo_tab: np.ndarray, hi_tab: np.ndarray, a: np.ndarray, b: np.ndarray):
    """min/max over the half-open slot range [a, b); NaN where the range is empty."""
    a = np.asarray(a, dtype=np.int64)
    b = np.asarray(b, dtype=np.int64)
    ok = (a >= 0) & (b > a)
    aa = np.where(ok, a, 0)
    bb = np.where(ok, b, 1)
    k = LOG2[bb - aa]
    span = 1 << k
    lo = np.minimum(lo_tab[k, aa], lo_tab[k, bb - span])
    hi = np.maximum(hi_tab[k, aa], hi_tab[k, bb - span])
    return np.where(ok, lo, np.nan), np.where(ok, hi, np.nan)


def forward_and_path(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    t: np.ndarray,
    horizons=HORIZONS,
    path_horizons=PATH_HORIZONS,
) -> dict[str, np.ndarray]:
    """Forward next-open returns and the intraday path proxy, aligned to `t`.

    open_/high/low are dense NG-slot arrays over et 570..959 with NaN where the
    lane has no bar for the (ticker, day).  MAE/MFE cover exactly the bars
    stamped et in [entry_et, exit_et): the entry minute is in, the exit minute
    is out (the exit is priced at its open).
    """
    idx = np.asarray(t, dtype=np.int64) - T0
    present_idx = np.flatnonzero(~np.isnan(open_))
    entry_idx = first_bar_at_or_after(present_idx, idx)
    has_entry = entry_idx >= 0
    entry_open = np.where(has_entry, open_[np.clip(entry_idx, 0, NG - 1)], np.nan)

    lo_tab = _sparse_stack(np.where(np.isnan(low), np.inf, low), np.minimum, np.inf)
    hi_tab = _sparse_stack(np.where(np.isnan(high), -np.inf, high), np.maximum, -np.inf)

    out: dict[str, np.ndarray] = {
        "entry_et": np.where(has_entry, T0 + entry_idx, -1),
        "entry_open": entry_open,
    }
    for h in horizons:
        x_idx = first_bar_at_or_after(present_idx, idx + h)
        has_exit = has_entry & (x_idx >= 0)
        x_open = np.where(has_exit, open_[np.clip(x_idx, 0, NG - 1)], np.nan)
        out[f"exit_et_{h}"] = np.where(has_exit, T0 + x_idx, -1)
        out[f"fwd_ret_{h}"] = x_open / entry_open - 1.0
        if h in path_horizons:
            lo, hi = range_extremes(lo_tab, hi_tab, np.clip(entry_idx, 0, NG - 1),
                                    np.clip(x_idx, 0, NG - 1))
            span = has_exit & (x_idx > entry_idx)
            out[f"mae_{h}"] = np.where(span, lo / entry_open - 1.0, np.nan)
            out[f"mfe_{h}"] = np.where(span, hi / entry_open - 1.0, np.nan)
    return out


def volume_ratio(volume: np.ndarray, t: np.ndarray, windows=VOL_WINDOWS) -> np.ndarray:
    """(V(t) - V(t-w1)) / (V(t-w1) - V(t-w2)); null when the denominator <= 0.

    V(e) = cumulative lane volume over bars stamped et <= e-1 (causal).
    """
    w1, w2 = windows
    cv = np.cumsum(np.where(np.isnan(volume), 0.0, volume))

    def cum_at(et: np.ndarray) -> np.ndarray:
        j = np.asarray(et, dtype=np.int64) - 1 - T0
        return np.where(j < 0, 0.0, cv[np.clip(j, 0, NG - 1)])

    t = np.asarray(t, dtype=np.int64)
    numer = cum_at(t) - cum_at(t - w1)
    denom = cum_at(t - w1) - cum_at(t - w2)
    out = np.full(t.shape, np.nan)
    np.divide(numer, denom, out=out, where=denom > 0.0)
    return out


def board_coordinates(px: np.ndarray, rank_known: np.ndarray, t: np.ndarray) -> dict[str, np.ndarray]:
    """Board-side coordinates for one ticker-day (dense px/rank over 570..959).

    promo_age is signed on purpose: an event that PRECEDES the ticker's first
    rank_known <= 5 minute of the day has a negative age, not a null.
    """
    idx = np.asarray(t, dtype=np.int64) - T0
    px_t = px[idx]

    run_hi = np.maximum.accumulate(np.where(np.isnan(px), -np.inf, px))
    dd = np.where(run_hi[idx] > 0.0, px_t / run_hi[idx] - 1.0, np.nan)

    out: dict[str, np.ndarray] = {"dd_from_high": dd}
    for k in RET_LAGS:
        prev = px[np.maximum(idx - k, 0)]
        prev = np.where(idx - k >= 0, prev, np.nan)
        out[f"ret{k}"] = np.where(np.isfinite(px_t) & np.isfinite(prev) & (prev > 0.0),
                                  px_t / prev - 1.0, np.nan)

    rk = np.where(np.isnan(rank_known), np.inf, rank_known)
    promo_hits = np.flatnonzero(rk <= 5)
    out["promo_age"] = (idx - promo_hits[0]).astype(np.float64) if promo_hits.size else np.full(idx.shape, np.nan)
    return out


def fin(v: float):
    """A missing measurement is NULL in the output table -- never NaN, never 0."""
    v = float(v)
    return v if np.isfinite(v) else None


def num(v: float):
    """Integer twin of :func:`fin`."""
    return None if not np.isfinite(v) else int(v)


# --------------------------------------------------------------------------- #
# I/O
# --------------------------------------------------------------------------- #
def etm_expr(col: str = "timestamp") -> pl.Expr:
    """ET minute-of-day.  The Int32 CAST is mandatory (i8 overflow otherwise)."""
    tset = pl.col(col).dt.convert_time_zone("America/New_York")
    return tset.dt.hour().cast(pl.Int32) * 60 + tset.dt.minute().cast(pl.Int32)


def load_board_day(data_root: Path, day: str) -> pl.DataFrame:
    return pl.read_parquet(board_path(data_root, day), columns=BOARD_COLS)


def board_source_sha(data_root: Path, day: str) -> str:
    """The board's own lane fingerprint for the day (FE-0 gate, one value)."""
    vals = (pl.scan_parquet(board_path(data_root, day))
            .select(pl.col("source_sha256").unique())
            .collect()["source_sha256"].to_list())
    assert len(vals) == 1, f"board day {day} carries {len(vals)} lane fingerprints"
    return vals[0]


def load_lane_day(data_root: Path, day: str, tickers: list[str]) -> pl.DataFrame:
    """Lane bars for one ET day and a ticker subset, restricted to 570..959."""
    d = dt.date.fromisoformat(day)
    day_start_et = dt.datetime(d.year, d.month, d.day, tzinfo=ET)
    lo = pl.lit(day_start_et.astimezone(dt.timezone.utc).replace(tzinfo=None)).dt.replace_time_zone("UTC")
    hi = pl.lit((day_start_et + dt.timedelta(days=1)).astimezone(dt.timezone.utc)
                .replace(tzinfo=None)).dt.replace_time_zone("UTC")
    return (
        pl.scan_parquet(lane_path(data_root, day))
        .select(LANE_COLS)
        .filter((pl.col("timestamp") >= lo) & (pl.col("timestamp") < hi))
        .with_columns(etm_expr().alias("etm"))
        .filter((pl.col("etm") >= T0) & (pl.col("etm") <= T1) & pl.col("ticker").is_in(tickers))
        .collect()
    )


def dense_bars(lane_day: pl.DataFrame, ticker: str, session_end: int) -> dict[str, np.ndarray]:
    """One (open, high, low, close, volume) NG-slot grid for one ticker-day."""
    sub = lane_day.filter(pl.col("ticker") == ticker)
    out = {f: np.full(NG, np.nan) for f in ("open", "high", "low", "close", "volume")}
    if sub.height:
        idx = sub["etm"].to_numpy().astype(np.int64) - T0
        keep = idx <= session_end - T0          # never price past the session end
        idx = idx[keep]
        for f in out:
            out[f][idx] = sub[f].to_numpy()[keep]
    return out


# --------------------------------------------------------------------------- #
# Day build
# --------------------------------------------------------------------------- #
def select_events(board_day: pl.DataFrame, session_end: int) -> tuple[pl.DataFrame, dict[str, int], int]:
    """rank_known<=10, RTH rows; apply the AMV guard; return (kept, counts, n_selected)."""
    sel = board_day.filter(
        (pl.col("rank_known") <= 10) & (pl.col("t") >= T0) & (pl.col("t") <= session_end)
    )
    masks = {
        "prev_close_null": pl.col("prev_close").is_null(),
        "prev_close_lt_1": pl.col("prev_close") < 1.0,
        "prev_close_stale": pl.col("prev_close_stale").fill_null(False),
        "prev_close_floor_qualified": pl.col("prev_close_floor_qualified").fill_null(False),
        "flag_prevclose_discrepancy": pl.col("flag_prevclose_discrepancy").fill_null(False),
    }
    assert tuple(masks) == GUARD_REASONS, "guard reason order drifted from GUARD_REASONS"
    dropped = sel.with_columns([m.alias(k) for k, m in masks.items()])
    any_flag = pl.any_horizontal(*[pl.col(k) for k in masks])
    counts = {k: dropped.filter(pl.col(k)).height for k in masks}
    counts["any"] = int(dropped.select(any_flag.sum()).item())
    kept = dropped.filter(~any_flag).drop(list(masks))
    counts["flag_extreme_gain_reported"] = kept.filter(
        pl.col("flag_extreme_gain").fill_null(False)
    ).height
    return kept, counts, sel.height


def build_day(day: str, data_root: Path, out_root: Path) -> dict:
    t_start = time.perf_counter()
    board_day = load_board_day(data_root, day)
    assert board_day.height > 0, f"empty board day {day}"
    session_end = int(board_day["session_end"].drop_nulls().max())
    assert T0 <= session_end <= T1, f"unexpected session_end {session_end} on {day}"

    events, guard, n_selected = select_events(board_day, session_end)
    tickers = sorted(events["ticker"].unique().to_list())

    lane_day = load_lane_day(data_root, day, tickers) if tickers else pl.DataFrame()
    tk_board = board_day.filter(pl.col("ticker").is_in(tickers)) if tickers else board_day.head(0)

    rows: list[dict] = []
    cov = {f"h{h}": {"n": 0, "available": 0, "missing_entry_open": 0,
                     "missing_exit_open": 0, "eod_censored": 0} for h in HORIZONS}
    path_cov = {f"h{h}": {"n": 0, "complete": 0, "null_path": 0} for h in PATH_HORIZONS}
    promo_missing = 0

    for tk in tickers:
        ev = events.filter(pl.col("ticker") == tk)
        if ev.height == 0:
            continue
        t = ev["t"].to_numpy().astype(np.int64)
        bars = dense_bars(lane_day, tk, session_end)

        tb = tk_board.filter(pl.col("ticker") == tk)
        px = np.full(NG, np.nan)
        rk = np.full(NG, np.nan)
        bt = tb["t"].to_numpy().astype(np.int64)
        px[bt - T0] = tb["px"].to_numpy()
        rk[bt - T0] = tb["rank_known"].to_numpy()

        fwd = forward_and_path(bars["open"], bars["high"], bars["low"], t)
        coords = board_coordinates(px, rk, t)
        vr = volume_ratio(bars["volume"], t)
        promo_missing += int(np.isnan(coords["promo_age"]).sum())

        px_t = px[t - T0]
        rank_t = rk[t - T0]
        px_et_t = ev["px_et"].fill_null(-1).to_numpy()
        gain = ev["gain"].to_numpy()
        prev_close = ev["prev_close"].to_numpy()
        extreme = ev["flag_extreme_gain"].fill_null(False).to_numpy()


        for i in range(ev.height):
            row = {
                "day": day,
                "t": int(t[i]),
                "ticker": tk,
                "rank_known": int(rank_t[i]),
                "gain": fin(gain[i]),
                "px": fin(px_t[i]),
                "px_et": None if int(px_et_t[i]) < 0 else int(px_et_t[i]),
                "prev_close": fin(prev_close[i]),
                "flag_extreme_gain": bool(extreme[i]),
                "promo_age": num(coords["promo_age"][i]),
                "ret1": fin(coords["ret1"][i]),
                "ret3": fin(coords["ret3"][i]),
                "ret5": fin(coords["ret5"][i]),
                "ret15": fin(coords["ret15"][i]),
                "dd_from_high": fin(coords["dd_from_high"][i]),
                "vol30_ratio": fin(vr[i]),
                "entry_et": None if fwd["entry_et"][i] < 0 else int(fwd["entry_et"][i]),
                "entry_open": fin(fwd["entry_open"][i]),
            }
            for h in HORIZONS:
                v = fwd[f"fwd_ret_{h}"][i]
                row[f"fwd_ret_{h}"] = fin(v)
                c = cov[f"h{h}"]
                c["n"] += 1
                censored = int(t[i]) + h > session_end
                if censored:
                    c["eod_censored"] += 1
                if np.isfinite(v):
                    c["available"] += 1
                elif not censored:
                    c["missing_entry_open" if fwd["entry_et"][i] < 0 else "missing_exit_open"] += 1
            for h in PATH_HORIZONS:
                for k in ("mae", "mfe"):
                    row[f"{k}_{h}"] = fin(fwd[f"{k}_{h}"][i])
                c = path_cov[f"h{h}"]
                c["n"] += 1
                c["complete" if np.isfinite(fwd[f"mae_{h}"][i]) else "null_path"] += 1
            rows.append(row)

    events_out = pl.DataFrame(rows, schema=OUT_SCHEMA) if rows else pl.DataFrame(schema=OUT_SCHEMA)
    assert list(events_out.columns) == list(OUT_SCHEMA), "output column drift vs OUT_SCHEMA"
    out_root.mkdir(parents=True, exist_ok=True)
    events_out.write_parquet(out_root / f"{day}.parquet", compression="zstd")

    lane = lane_path(data_root, day)
    runtime = time.perf_counter() - t_start
    manifest = {
        "day": day,
        "producer": "factory/scripts/entry_ev/stage_a_build.py",
        "schema_version": 1,
        "built_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "runtime_s": round(runtime, 2),
        "block": {"start": str(DISCOVERY_START), "end": str(DISCOVERY_END)},
        "grid": {"t_first": T0, "t_last": T1, "session_end": session_end},
        "horizons": list(HORIZONS),
        "path_horizons": list(PATH_HORIZONS),
        "inputs": {
            "board": str(board_path(data_root, day).relative_to(data_root)),
            "board_rows": board_day.height,
            "board_source_sha256": board_source_sha(data_root, day),
            "lane": str(lane.relative_to(data_root)),
            "lane_bytes": lane.stat().st_size,
        },
        "rows": {
            "selected_rank_le10_rth": n_selected,
            "guard_dropped": guard["any"],
            "kept": events_out.height,
            "tickers": len(tickers),
        },
        "guard": guard,
        "coverage": cov,
        "path_coverage": path_cov,
        "promo_age_null": promo_missing,
    }
    (out_root / f"{day}.manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    (out_root / f"{day}_done").write_text(json.dumps({"day": day, "runtime_s": round(runtime, 2)}))
    return manifest


# --------------------------------------------------------------------------- #
# Day selection + CLI
# --------------------------------------------------------------------------- #
def in_block(day: str) -> bool:
    d = dt.date.fromisoformat(day)
    return DISCOVERY_START <= d <= DISCOVERY_END


def discover_days(data_root: Path, months: list[str] | None, days: list[str] | None) -> list[str]:
    """Days of the DISCOVERY block to build.

    An explicit --days request is asserted into the block (a named protected day
    is refused, not quietly dropped).  A directory walk skips whatever the board
    happens to hold outside the block -- the corpus runs past 2023-03-14 and
    those files must never be opened -- and reports how many were excluded.
    """
    bdir = board_dir(data_root)
    if days:
        out = sorted(set(days))
        for d in out:
            assert_in_block(d)
        return out

    if months is None:
        months = []
        y, m = DISCOVERY_START.year, DISCOVERY_START.month
        while (y, m) <= (DISCOVERY_END.year, DISCOVERY_END.month):
            months.append(f"{y:04d}-{m:02d}")
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    out: list[str] = []
    excluded = 0
    for mth in months:
        assert_month_in_block(mth)
        d = bdir / f"month={mth}"
        if not d.is_dir():
            print(f"[skip] no board month dir {d}")
            continue
        found = sorted(p.stem for p in d.glob("*.parquet"))
        excluded += sum(1 for x in found if not in_block(x))
        out += [x for x in found if in_block(x)]
    if excluded:
        print(f"[block] excluded {excluded} board days outside "
              f"{DISCOVERY_START}..{DISCOVERY_END} (protected/sealed/reserved: never read)")
    return sorted(set(out))


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ENTRY-EV-01 Stage-A per-day event tables")
    p.add_argument("--data-root", type=Path, default=None,
                   help="root holding atlas/observation/... and ohlcv_<month>.parquet "
                        "(default: $ENTRY_EV_DATA_ROOT, <repo>/data, "
                        "/home/hillel/projects/Alpacatrader/data -- first with the board)")
    p.add_argument("--out", type=Path, default=None,
                   help="output root (default: <data-root>/entry_ev/stage_a)")
    p.add_argument("--months", type=str, default=None, help="comma list, e.g. 2021-03")
    p.add_argument("--days", type=str, default=None, help="comma list, e.g. 2021-03-01,2021-03-02")
    p.add_argument("--force", action="store_true", help="rebuild days that already have a _done marker")
    p.add_argument("--limit", type=int, default=None, help="build at most N pending days")
    return p.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    data_root = resolve_data_root(a.data_root)
    out_root = Path(a.out) if a.out else data_root / "entry_ev" / "stage_a"
    months = [m.strip() for m in a.months.split(",")] if a.months else None
    days = [d.strip() for d in a.days.split(",")] if a.days else None

    todo = discover_days(data_root, months, days)
    todo = [d for d in todo if a.force or not (out_root / f"{d}_done").exists()]
    if a.limit:
        todo = todo[: a.limit]

    print(f"data_root = {data_root}")
    print(f"out_root  = {out_root}")
    print(f"days: {len(todo)} to build (resume skips <day>_done)")
    totals = {"guard_dropped": 0, "selected": 0, "kept": 0}
    for i, day in enumerate(todo, 1):
        m = build_day(day, data_root, out_root)
        g = m["guard"]
        cov5, cov30 = m["coverage"]["h5"], m["coverage"]["h30"]
        print(
            f"[{i}/{len(todo)}] {day} {m['runtime_s']:>6.1f}s "
            f"rows {m['rows']['selected_rank_le10_rth']} -> {m['rows']['kept']} "
            f"(dropped {g['any']}: null={g['prev_close_null']} lt1={g['prev_close_lt_1']} "
            f"stale={g['prev_close_stale']} floor={g['prev_close_floor_qualified']} "
            f"disc={g['flag_prevclose_discrepancy']}; extreme_gain kept={g['flag_extreme_gain_reported']}) "
            f"| h5 n={cov5['available']}/{cov5['n']} eod={cov5['eod_censored']} "
            f"h30 n={cov30['available']}/{cov30['n']} eod={cov30['eod_censored']} "
            f"missing_in/out h5={cov5['missing_entry_open']}/{cov5['missing_exit_open']} "
            f"h30={cov30['missing_entry_open']}/{cov30['missing_exit_open']}",
            flush=True,
        )
        totals["guard_dropped"] += m["rows"]["guard_dropped"]
        totals["selected"] += m["rows"]["selected_rank_le10_rth"]
        totals["kept"] += m["rows"]["kept"]
    print(f"done: selected={totals['selected']} kept={totals['kept']} guard_dropped={totals['guard_dropped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
