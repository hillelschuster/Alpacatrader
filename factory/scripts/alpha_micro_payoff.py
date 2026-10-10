#!/usr/bin/env python3
"""Supervised micro-scalp payoff learner on the raw one-second SIP tick/NBBO substrate.

Pipeline (every stage atomic, one day in memory at a time, no global corpus of raw records):

  1. corpus - every one-second state of the full-PIT top-three watchlist that clears the
              SHARED common liveness/activity gate only (no family conjunction, no
              hand-coded burst/return/imbalance trigger), executed for BOTH heads on the
              real SIP NBBO: market ASK buy at signal+250ms, market BID sell at entry+h
              (or the first subsequent regular quote, never a favourable price), with L1
              capacity checked. A depth/quote failure is UNKNOWN - it stays in the corpus
              AND in the evaluation; it is never dropped and never booked as cash.
  2. fit    - two fixed LightGBM payoff regressors (5s/15s heads) on the FULLY COVERED
              development months only, 5-second sampled states, equal-day weights, known
              labels only; the target is clipped [-0.2, 0.3] for FIT ONLY, PnL unclipped.
  3. select - score every qualified validation state, build the full (head x threshold)
              surface at 10bps residual, freeze the single best BEFORE any late record is
              even built.
  4. late   - the frozen head/threshold run once on the late months at 0/10/25/100bps.

The model input is causal one-second core state only: NO symbol, date, absolute-UTC,
label, or future-coverage feature. Execution is a research replay, not a broker model:
a quote-supported touch is necessary, never a guaranteed exchange fill, and UNKNOWN
exits charge the full order budget in the day lower bound.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import itertools
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import joblib
import numpy as np
import polars as pl
from lightgbm import LGBMRegressor

sys.path.insert(0, str(Path(__file__).resolve().parent))

from alpha_micro_core import (
    COVERAGE_EPOCH,
    LATENCY_US,
    ORDER_BUDGET,
    admissions,
    coverage_is_complete,
    coverage_kind,
    coverage_unknown_reason,
    execution,
    load_day,
    states,
)
from alpha_open_panel import ROOT, allowed
from alpha_quote_audit import clock_us

# ----- fixed configuration (no HPO). ---------------------------------------
HORIZONS = (5, 15)  # two payoff heads, seconds
THRESHOLDS = (0.001, 0.003, 0.01)  # three fixed entry views on predicted gross-after-touch
COSTS = (0, 10, 25, 100)  # residual round-trip bps diagnostics
SELECT_COST = 10  # primary selection cost (residual bps)
CLIP = (-0.2, 0.3)  # target clip, FIT ONLY
FIT_SAMPLE_SECONDS = 5  # fit states are sampled every 5 seconds of the grid
LGB_PARAMS = {
    "objective": "regression",
    "num_leaves": 15,
    "min_data_in_leaf": 1000,
    "learning_rate": 0.03,
    "n_estimators": 200,
    "num_threads": 2,
    "seed": 20261008,
    "verbosity": -1,
    "deterministic": True,
    "force_row_wise": True,
}
SEED = 20261008
BOOT_N = 1000
BOOT_SEED = 20261008
MAX_SLOTS = 3
BOOK = ORDER_BUDGET * MAX_SLOTS  # $750 research sub-book
COOLDOWN_S = 300  # flat +5min per-ticker cooldown between attempts
MAX_ATTEMPTS_PER_TICKER = 5  # attempts per ticker per day
ANNUAL_DAYS = 252  # simple annualization, NOT a CAGR claim

# Fit uses the six fully covered development months; validation is ALL of 2023;
# the late block is the nine months 2025-09..2026-05.
MONTHS = {
    "development": ("2021-05", "2021-06", "2021-07", "2021-08", "2021-09", "2021-10"),
    "validation": tuple(f"2023-{m:02d}" for m in range(1, 13)),
    "late": (
        "2025-09",
        "2025-10",
        "2025-11",
        "2025-12",
        "2026-01",
        "2026-02",
        "2026-03",
        "2026-04",
        "2026-05",
    ),
}
PROTECTED_UNREAD = ["2024", "2025-01", "2026-06", "2026-07", "2026-08"]

CALENDAR = ROOT / "factory" / "artifacts" / "basket" / "sip" / "phase2_session_calendar.json"
DATA = ROOT / "data"
DEFAULT_OUT = Path.home() / "alpha-data" / "open-search-v1" / "micro_payoff"
VERSION = 1
# Epoch of the persisted corpus ROWS: features, gate, both execution heads and the exact
# output key names. A cached day whose resume hash matches is reused verbatim.
ROWS_SCHEMA = 1
UNDERPOWERED_KNOWN_FILLS = 25  # label only; NEVER a drop or auto-kill threshold

# Frozen model input order (24 causal one-second core-state features).
FEATURES = (
    "ret5s",
    "ret30s",
    "ret60s",
    "imb5s",
    "imb30s",
    "imb60s",
    "log_classdv5s",
    "log_dv5s",
    "n5s",
    "log_classdv30s",
    "log_dv30s",
    "n30s",
    "log_classdv60s",
    "log_dv60s",
    "n60s",
    "dd120s",
    "rebound30s",
    "quote_gap_ask_px",
    "depth_imbalance",
    "log_bid_notional",
    "log_ask_notional",
    "log_px",
    "clock_et_min",
    "admission_age_s",
)
FEATURE_SOURCES = {
    "ret5s": "states.ret5s (strictly prior prints)",
    "ret30s": "states.ret30s",
    "ret60s": "states.ret60s",
    "imb5s": "states.imbalance5s (classified buy/sell dollar flow)",
    "imb30s": "states.imbalance30s",
    "imb60s": "states.imbalance60s",
    "log_classdv5s": "log1p(states.classified_dv5s)",
    "log_dv5s": "log1p(states.dv5s)",
    "n5s": "states.n5s",
    "log_classdv30s": "log1p(states.classified_dv30s)",
    "log_dv30s": "log1p(states.dv30s)",
    "n30s": "states.n30s",
    "log_classdv60s": "log1p(states.classified_dv60s)",
    "log_dv60s": "log1p(states.dv60s)",
    "n60s": "states.n60s",
    "dd120s": "states.dd120s (NaN while the 120s rolling window warms up: undefined, never faked)",
    "rebound30s": "states.rebound30s (NaN while the 30s rolling window warms up: "
    "undefined, never faked)",
    "quote_gap_ask_px": "strictly-prior NBBO ask / last print - 1",
    "depth_imbalance": "states.depth_imbalance (strictly-prior NBBO)",
    "log_bid_notional": "log1p(strictly-prior bid_shares x bid)",
    "log_ask_notional": "log1p(strictly-prior ask_shares x ask)",
    "log_px": "log(last print)",
    "clock_et_min": "ET minutes since midnight of the signal second (no absolute UTC)",
    "admission_age_s": "seconds since watchlist admission (no date/symbol identity)",
}

COMMON_GATE = {
    "fresh": True,
    "dv60s_min": 50000,
    "n60s_min": 20,
    "px_min": 5.0,
    "spread_max": 0.003,
    "ask_notional_min_multiple": 1.05,
}
"""Common liveness/activity gate shared with the sibling micro families. It is the ONLY
pre-selection filter: family membership is learned, never hand-coded."""

CONTRACT = {
    "version": VERSION,
    "hypothesis": "a supervised model over causal one-second core states predicts the real "
    "ASK-to-BID micro-scalp payoff (5s/15s heads) well enough to clear round-trip "
    "residual cost on a frequent, slot-capped, no-leverage research replay",
    "periods": {k: {"months": list(v)} for k, v in MONTHS.items()},
    "fit_rule": "fully covered development months only; 5-second sampled states; equal-day "
    "weights; known labels only; target clip FIT ONLY; PnL unclipped",
    "feature_order": list(FEATURES),
    "feature_sources": FEATURE_SOURCES,
    "feature_excludes": [
        "symbol",
        "ticker",
        "day",
        "date",
        "ts_utc",
        "absolute UTC",
        "signal_us",
        "admit_t",
        "labels",
        "gross_*",
        "exit_*",
        "coverage",
        "pred_*",
        "quote_supported_*",
        "known_*",
    ],
    "feature_nan_rule": "dd120s/rebound30s stay NaN (undefined) during the rolling-window "
    "warm-up; LightGBM native NaN handling, identical at fit and predict",
    "common_gate": COMMON_GATE,
    "entry": f"market ASK buy at signal+{LATENCY_US // 1000}ms; quote-supported L1 capacity "
    "checked, never a guaranteed exchange fill",
    "exit": "market BID sell at entry+h or the first subsequent regular quote <= session_end "
    "(never a favourable price/depth); depth/quote failure => UNKNOWN, retained",
    "execution_policy": {
        "flat": True,
        "cooldown_s": COOLDOWN_S,
        "max_attempts_per_ticker_day": MAX_ATTEMPTS_PER_TICKER,
        "max_slots": MAX_SLOTS,
        "order_budget": ORDER_BUDGET,
        "research_book": BOOK,
        "no_overlap_per_symbol": True,
        "no_leverage": True,
        "pending_halt_exit_locks_slot_to_session_end": True,
        "cash_reuse": "margin-style funded reuse; not a claim a $750 cash account day-trades",
    },
    "costs_bps_residual_round_trip": list(COSTS),
    "selection": {
        "cost_bps": SELECT_COST,
        "objective": "validation mean_daily_net_usd",
        "power_floor": None,
        "note": "no hard min-fills/min-days floor; small-n views are labelled "
        "underpowered, never dropped and never auto-killed",
    },
    "unknown_rule": "entry/exit quote or L1 depth insufficient => UNKNOWN intent retained and "
    "charged the full order budget in the day lower bound; never cash",
    "coverage_rule": "a day whose coverage kind is not complete takes the whole -$750 book "
    "lower bound for that date; known-complete-day means reported separately",
    "out_of_fit_not_pristine": True,
    "protected_unread": PROTECTED_UNREAD,
    "calendar": str(CALENDAR.relative_to(ROOT)),
}


def common_gate() -> pl.Expr:
    """Liveness/activity qualification on causal core states (no family conjunction)."""
    return (
        pl.col("fresh")
        & (pl.col("dv60s") >= COMMON_GATE["dv60s_min"])
        & (pl.col("n60s") >= COMMON_GATE["n60s_min"])
        & (pl.col("px") >= COMMON_GATE["px_min"])
        & (pl.col("spread") <= COMMON_GATE["spread_max"])
        & (
            pl.col("ask_shares") * pl.col("ask")
            >= COMMON_GATE["ask_notional_min_multiple"] * ORDER_BUDGET
        )
    )


def feat_exprs(midnight_us: int, start_us: int) -> list[pl.Expr]:
    """Frozen FEATURES expressions over one alpha_micro_core.states frame."""
    return [
        pl.col("ret5s").fill_null(0.0).alias("ret5s"),
        pl.col("ret30s").fill_null(0.0).alias("ret30s"),
        pl.col("ret60s").fill_null(0.0).alias("ret60s"),
        pl.col("imbalance5s").fill_null(0.0).alias("imb5s"),
        pl.col("imbalance30s").fill_null(0.0).alias("imb30s"),
        pl.col("imbalance60s").fill_null(0.0).alias("imb60s"),
        pl.col("classified_dv5s").log1p().alias("log_classdv5s"),
        pl.col("dv5s").log1p().alias("log_dv5s"),
        pl.col("n5s").alias("n5s"),
        pl.col("classified_dv30s").log1p().alias("log_classdv30s"),
        pl.col("dv30s").log1p().alias("log_dv30s"),
        pl.col("n30s").alias("n30s"),
        pl.col("classified_dv60s").log1p().alias("log_classdv60s"),
        pl.col("dv60s").log1p().alias("log_dv60s"),
        pl.col("n60s").alias("n60s"),
        pl.col("dd120s").fill_null(0.0).alias("dd120s"),
        pl.col("rebound30s").fill_null(0.0).alias("rebound30s"),
        (pl.col("ask") / pl.col("px") - 1.0).alias("quote_gap_ask_px"),
        pl.col("depth_imbalance").fill_null(0.0).alias("depth_imbalance"),
        (pl.col("bid_shares") * pl.col("bid")).log1p().alias("log_bid_notional"),
        (pl.col("ask_shares") * pl.col("ask")).log1p().alias("log_ask_notional"),
        pl.col("px").log().alias("log_px"),
        ((pl.col("signal_us") - midnight_us) / 60_000_000.0).alias("clock_et_min"),
        ((pl.col("signal_us") - start_us) / 1_000_000.0).alias("admission_age_s"),
    ]


def state_frame(
    day: str, ticker: str, stream: dict, st: pl.DataFrame, session_end: int
) -> pl.DataFrame:
    """One ticker's qualified states: frozen features + both executed heads."""
    sig = st["signal_us"].to_list()
    feats = st.select(
        [pl.col("signal_us"), *feat_exprs(clock_us(day, 0, 0), clock_us(day, stream["admit_t"], 0))]
    ).select(["signal_us", *FEATURES])
    ex = {h: [execution(day, ticker, t, stream, session_end, h) for t in sig] for h in HORIZONS}
    base = ex[HORIZONS[0]]
    cols = [
        pl.lit(day).alias("day"),
        pl.lit(ticker).alias("ticker"),
        pl.col("signal_us"),
        pl.lit(int(stream["admit_t"])).alias("admit_t"),
        pl.lit(clock_us(day, session_end, 0)).alias("session_end_us"),
        pl.Series("entry_open", [r["entry_open"] for r in base], dtype=pl.Float32),
        pl.Series("entry_et", [r["entry_et"] for r in base], dtype=pl.Int64),
    ]
    for h in HORIZONS:
        cols += [
            pl.Series(f"gross_{h}", [r[f"gross_{h}"] for r in ex[h]], dtype=pl.Float32),
            pl.Series(f"known_{h}", [r[f"gross_{h}"] is not None for r in ex[h]]),
            pl.Series(f"exit_et_{h}", [r[f"exit_et_{h}"] for r in ex[h]], dtype=pl.Int64),
            pl.Series(f"exit_rode_{h}", [r["exit_us"] != r["exit_target_us"] for r in ex[h]]),
            pl.Series(f"quote_supported_{h}", [r["quote_supported"] for r in ex[h]]),
        ]
    return (
        feats.with_columns(cols)
        .select(
            [
                "day",
                "ticker",
                "signal_us",
                "admit_t",
                "session_end_us",
                "entry_open",
                "entry_et",
                *FEATURES,
                *[
                    f"{k}_{h}"
                    for h in HORIZONS
                    for k in ("gross", "known", "exit_et", "exit_rode", "quote_supported")
                ],
            ]
        )
        .with_columns(pl.col(list(FEATURES)).cast(pl.Float32))
    )


def build_day(data: Path, day: str, session_end: int) -> tuple[pl.DataFrame | None, dict]:
    """Full corpus for one date: qualified states x both executed heads (None if empty)."""
    cov: dict = {
        "watch_names": 0,
        "covered_names": 0,
        "missing_symbol_streams": [],
        "missing_day_file": False,
        "no_regular_quote_symbols": 0,
        "quote_condition_unknown_symbols": 0,
        "qualified_states": 0,
        "coverage_epoch": COVERAGE_EPOCH,
        "complete": False,
        "error": None,
    }
    frame = None
    try:
        watch = admissions(data, day)
        cov["watch_names"] = len(watch)
        streams, load_cov = load_day(data, day)
        cov.update(load_cov)
        gate = common_gate()
        frames = []
        for ticker, stream in streams.items():
            st = states(day, ticker, stream, session_end)
            if st.is_empty():
                continue
            st = st.filter(gate)
            if st.is_empty():
                continue
            cov["qualified_states"] += int(st.height)
            frames.append(state_frame(day, ticker, stream, st, session_end))
        frame = pl.concat(frames) if frames else None
    except Exception as exc:  # keep the calendar running; the day stays coverage-unknown
        cov["error"] = f"{type(exc).__name__}: {exc}"
    kind = (
        "day_error"
        if cov["error"]
        else coverage_kind(
            cov["watch_names"],
            cov.get("missing_symbol_streams") or [],
            cov.get("missing_day_file", False),
            cov.get("no_regular_quote_symbols", 0),
            cov.get("quote_condition_unknown_symbols", 0),
        )
    )
    cov["coverage_kind"] = kind
    cov["unknown_reason"] = "day_error" if cov["error"] else coverage_unknown_reason(cov)
    cov["complete"] = coverage_is_complete(kind) and not cov["error"]
    return frame, cov


# ----- shared helpers -------------------------------------------------------
def _default(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    raise TypeError(f"not serializable: {type(obj)}")


def _digest_bytes(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def producer_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _write_json_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=1, default=_default) + "\n")
    os.replace(tmp, path)


def session_ends(days: list[str]) -> dict[str, int]:
    cal = json.loads(CALENDAR.read_text())["evidence"]
    return {d: int(cal[d]["session_end"]) for d in days}


def period_days() -> dict[str, list[str]]:
    files = sorted(
        p.stem for p in (DATA / "sip" / "candidates").glob("????-??-??.json") if allowed(p.stem)
    )
    cal = json.loads(CALENDAR.read_text())["evidence"]
    out = {}
    for name, months in MONTHS.items():
        days = [d for d in files if d[:7] in months]
        caldays = [d for d in sorted(cal) if d[:7] in months]
        if days != caldays:
            raise ValueError(f"calendar/candidate day mismatch for {name}")
        out[name] = days
    return out


def resume_hash(day: str, contract_sha: str) -> str:
    return hashlib.sha256(
        json.dumps(
            [VERSION, ROWS_SCHEMA, COVERAGE_EPOCH, contract_sha, day], sort_keys=True
        ).encode()
    ).hexdigest()


# ----- corpus stage ---------------------------------------------------------
def corpus_paths(out_dir: Path, day: str) -> tuple[Path, Path]:
    return (out_dir / "corpus" / f"{day}.parquet", out_dir / "corpus" / f"{day}.cov.json")


def build_one_day(
    data: Path,
    out_dir: Path,
    day: str,
    session_end: int,
    producer: str,
    contract_sha: str,
    force: bool = False,
) -> dict:
    """Build (or resume) one day's corpus atomically: parquet + coverage sidecar."""
    qpath, cpath = corpus_paths(out_dir, day)
    expect = resume_hash(day, contract_sha)
    if not force and cpath.exists() and qpath.exists():
        try:
            prior = json.loads(cpath.read_text())
        except (json.JSONDecodeError, OSError):
            prior = None
        if (
            prior
            and prior.get("resume_hash") == expect
            and prior.get("version") == VERSION
            and prior.get("rows_schema") == ROWS_SCHEMA
            and prior.get("coverage_epoch") == COVERAGE_EPOCH
        ):
            return {
                "day": day,
                "resumed": True,
                "rows": prior.get("rows", 0),
                "coverage": prior.get("coverage", {}),
            }
    frame, cov = build_day(data, day, session_end)
    qpath.parent.mkdir(parents=True, exist_ok=True)
    tmp = qpath.with_name(f"{qpath.name}.tmp{os.getpid()}")
    if frame is None:
        tmp.write_bytes(b"")
    else:
        frame.write_parquet(tmp)
    os.replace(tmp, qpath)
    rows = 0 if frame is None else int(frame.height)
    _write_json_atomic(
        cpath,
        {
            "day": day,
            "version": VERSION,
            "rows_schema": ROWS_SCHEMA,
            "producer_sha256": producer,
            "resume_hash": expect,
            "coverage_epoch": COVERAGE_EPOCH,
            "rows": rows,
            "coverage": cov,
            "corpus_sha256": sha256_file(qpath) if rows else None,
            "empty": rows == 0,
        },
    )
    return {"day": day, "resumed": False, "rows": rows, "coverage": cov}


HEAD_COLS = (
    ["day", "ticker", "signal_us", "admit_t", "session_end_us", "entry_open", "entry_et"]
    + list(FEATURES)
    + [
        f"{k}_{h}"
        for h in HORIZONS
        for k in ("gross", "known", "exit_et", "exit_rode", "quote_supported")
    ]
)
HEAD_TYPES = {
    "day": pl.String,
    "ticker": pl.String,
    "signal_us": pl.Int64,
    "admit_t": pl.Int64,
    "session_end_us": pl.Int64,
    "entry_open": pl.Float32,
    "entry_et": pl.Int64,
    **dict.fromkeys(FEATURES, pl.Float32),
    **{
        f"{k}_{h}": (
            pl.Float32
            if k == "gross"
            else pl.Boolean
            if k in ("known", "exit_rode", "quote_supported")
            else pl.Int64
        )
        for h in HORIZONS
        for k in ("gross", "known", "exit_et", "exit_rode", "quote_supported")
    },
}


def empty_frame() -> pl.DataFrame:
    return pl.DataFrame(schema={c: HEAD_TYPES[c] for c in HEAD_COLS})


def read_day_corpus(out_dir: Path, day: str) -> tuple[pl.DataFrame | None, dict]:
    qpath, cpath = corpus_paths(out_dir, day)
    cov = json.loads(cpath.read_text())["coverage"]
    if not qpath.exists() or qpath.stat().st_size == 0:
        return None, cov
    return pl.read_parquet(qpath), cov


def stage_corpus(
    data: Path, out_dir: Path, days: list[str], force: bool = False, workers: int = 1
) -> list[dict]:
    """Build the per-day corpus cache. One process by default; days stay independent."""
    ends = session_ends(days)
    producer = producer_sha256()
    contract_sha = _digest_bytes(CONTRACT)
    todo, infos = [], []
    for day in days:
        qpath, cpath = corpus_paths(out_dir, day)
        expect = resume_hash(day, contract_sha)
        prior, fresh = None, False
        if cpath.exists() and qpath.exists() and not force:
            try:
                prior = json.loads(cpath.read_text())
                fresh = (
                    prior.get("resume_hash") == expect
                    and prior.get("version") == VERSION
                    and prior.get("rows_schema") == ROWS_SCHEMA
                    and prior.get("coverage_epoch") == COVERAGE_EPOCH
                )
            except (json.JSONDecodeError, OSError):
                fresh = False
        if fresh:
            infos.append(
                {
                    "day": day,
                    "resumed": True,
                    "rows": prior.get("rows", 0),
                    "coverage": prior.get("coverage", {}),
                }
            )
        else:
            todo.append(day)
    done = []
    if todo:
        if workers > 1:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                done = list(
                    pool.map(
                        lambda d: build_one_day(
                            data, out_dir, d, ends[d], producer, contract_sha, force
                        ),
                        todo,
                    )
                )
        else:
            for n, day in enumerate(todo, 1):
                info = build_one_day(data, out_dir, day, ends[day], producer, contract_sha, force)
                done.append(info)
                if n % 10 == 0 or n == len(todo):
                    print(
                        f"corpus {n}/{len(todo)} {json.dumps(info, default=_default)}", flush=True
                    )
    return infos + done


def load_period_frames(out_dir: Path, days: list[str]) -> tuple[pl.DataFrame, dict]:
    """Concatenate the cached day frames for one period (bounded per-day reads)."""
    parts, cov = [], {}
    for day in days:
        frame, day_cov = read_day_corpus(out_dir, day)
        cov[day] = day_cov
        if frame is not None and frame.height:
            parts.append(frame)
    if not parts:
        return empty_frame(), cov
    return pl.concat(parts, how="vertical_relaxed"), cov


# ----- fit stage ------------------------------------------------------------
def equal_day_weights(days: list[str]) -> np.ndarray:
    """Equal total weight per calendar day; liquid ticker-minutes cannot dominate."""
    days = list(days)
    counts: dict[str, int] = {}
    for d in days:
        counts[d] = counts.get(d, 0) + 1
    n_days = len(counts)
    return np.array([1.0 / (n_days * counts[d]) for d in days], dtype=np.float64)


def fit_head(frame: pl.DataFrame, horizon: int) -> tuple[LGBMRegressor, dict]:
    """One fixed LightGBM payoff head: known labels, 5s sampled, equal-day weights."""
    rows = (
        frame.filter(pl.col(f"known_{horizon}"))
        .sort(["day", "ticker", "signal_us"])
        .with_columns(pl.int_range(pl.len()).over(["day", "ticker"]).alias("_i"))
        .filter((pl.col("_i") % FIT_SAMPLE_SECONDS) == 0)
        .drop("_i")
    )
    raw = rows[f"gross_{horizon}"].to_numpy().astype(float)  # unclipped, for stats
    y = np.clip(raw, CLIP[0], CLIP[1])  # clip ONLY at fit
    feature_matrix = rows.select(list(FEATURES)).to_numpy().astype(np.float32)
    w = equal_day_weights(rows["day"].to_list())
    model = LGBMRegressor(**LGB_PARAMS)
    model.fit(feature_matrix, y, sample_weight=w)
    gain = np.asarray(model.booster_.feature_importance("gain"), dtype=float)
    report = {
        "horizon_seconds": horizon,
        "train_rows": int(rows.height),
        "train_days": int(rows["day"].n_unique()),
        "train_day_min": rows["day"].min(),
        "train_day_max": rows["day"].max(),
        "sampled_every_seconds": FIT_SAMPLE_SECONDS,
        "weight_rule": "equal total weight per calendar day",
        "label_unclipped": {
            "count": int(raw.size),
            "mean": float(raw.mean()) if raw.size else None,
            "std": float(raw.std()) if raw.size else None,
            "p05": float(np.percentile(raw, 5)) if raw.size else None,
            "p50": float(np.percentile(raw, 50)) if raw.size else None,
            "p95": float(np.percentile(raw, 95)) if raw.size else None,
            "min": float(raw.min()) if raw.size else None,
            "max": float(raw.max()) if raw.size else None,
        },
        "label_clip_for_fit": list(CLIP),
        "params": LGB_PARAMS,
        "gain_importance": dict(
            sorted(zip(FEATURES, gain.tolist(), strict=True), key=lambda kv: -kv[1])
        ),
    }
    return model, report


def fit_models(out_dir: Path, dev_days: list[str]) -> tuple[dict, dict]:
    """Fit both heads on the fully covered development days only."""
    frames, covs, fit_days = [], {}, []
    for day in dev_days:
        frame, cov = read_day_corpus(out_dir, day)
        covs[day] = cov
        if cov.get("complete") and frame is not None and frame.height:
            fit_days.append(day)
            frames.append(frame)
    if not fit_days:
        raise ValueError("no fully covered development corpus; run the corpus stage first")
    frame = pl.concat(frames, how="vertical_relaxed")
    models, report = {}, {}
    for h in HORIZONS:
        models[h], report[h] = fit_head(frame, h)
    report["fit_days"] = fit_days
    report["excluded_days"] = sorted(set(dev_days) - set(fit_days))
    report["exclusion_reasons"] = {
        d: covs[d].get("unknown_reason") or covs[d].get("coverage_kind")
        for d in report["excluded_days"]
    }
    save_models(out_dir, models, report)
    return models, report


def save_models(out_dir: Path, models: dict, report: dict) -> list[str]:
    model_dir = out_dir / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for h, model in models.items():
        jl = model_dir / f"payoff_h{h}.joblib"
        txt = model_dir / f"payoff_h{h}.txt"
        joblib.dump(
            {
                "lgbm": model,
                "feature_order": list(FEATURES),
                "horizon": h,
                "clip_fit": list(CLIP),
                "params": LGB_PARAMS,
                "seed": SEED,
            },
            jl,
        )
        model.booster_.save_model(str(txt))
        saved += [str(jl), str(txt)]
    (model_dir / "feature_order.json").write_text(
        json.dumps(
            {
                "feature_order": list(FEATURES),
                "n_features": len(FEATURES),
                "sources": FEATURE_SOURCES,
                "nan_rule": "dd120s/rebound30s stay NaN (undefined) during the rolling-window "
                "warm-up; LightGBM's native NaN handling applies identically at fit "
                "and predict; no value is fabricated and null is never imputed",
                "excludes": [
                    "day",
                    "ticker",
                    "signal_us",
                    "admit_t",
                    "labels",
                    "exit_*",
                    "gross_*",
                    "pred_*",
                    "coverage",
                ],
            },
            indent=2,
        )
        + "\n"
    )
    return saved


def load_models(model_dir: Path) -> dict:
    return {h: joblib.load(model_dir / f"payoff_h{h}.joblib")["lgbm"] for h in HORIZONS}


def score_frame(frame: pl.DataFrame, models: dict) -> pl.DataFrame:
    """Predict both heads over every qualified state (per-frame chunk, float32)."""
    if not frame.height:
        return frame.with_columns(
            [pl.lit(None, dtype=pl.Float64).alias(f"pred_gross_{h}") for h in HORIZONS]
        )
    feature_matrix = frame.select(list(FEATURES)).to_numpy().astype(np.float32)
    out = frame
    for h in HORIZONS:
        pred = np.empty(feature_matrix.shape[0], dtype=np.float64)
        step = 200_000
        for lo in range(0, feature_matrix.shape[0], step):
            pred[lo : lo + step] = models[h].predict(feature_matrix[lo : lo + step])
        out = out.with_columns(pl.Series(f"pred_gross_{h}", pred))
    return out


# ----- frequent-execution research replay -----------------------------------
def replay_cell(
    scores: pl.DataFrame, days: list[str], horizon: int, threshold: float, cost_bps: float
) -> tuple[dict, list[dict]]:
    """One (head, threshold, cost) cell: flat, +5min cooldown, max 5 attempts/ticker/day,
    3 x $250 slots under a $750 sub-book, no overlap per symbol, no leverage, and pending
    halt exits that lock their slot to the session end.

    Signals are the thresholded model scores (predicted gross-after-touch >= threshold).
    An UNKNOWN execution stays in the replay and is charged the full order budget in the
    day's lower bound; it is never dropped and never booked as cash.
    """
    pred, gross, exit_et = f"pred_gross_{horizon}", f"gross_{horizon}", f"exit_et_{horizon}"
    side = cost_bps / 20_000.0
    sig = scores.filter(pl.col("day").is_in(list(days)) & (pl.col(pred) >= threshold)).select(
        [
            "day",
            "ticker",
            "signal_us",
            "entry_open",
            "entry_et",
            gross,
            exit_et,
            "session_end_us",
            pl.col(pred).alias("score"),
        ]
    )
    trades: list[dict] = []
    daily: list[dict] = []
    cooldown_us = COOLDOWN_S * 1_000_000
    for day in days:
        d = sig.filter(pl.col("day") == day)
        base = {
            "day": day,
            "attempts": 0,
            "known_pnl": 0.0,
            "unknown": 0,
            "lower_bound_pnl": 0.0,
            "cash_skips": 0,
            "cooldown_skips": 0,
            "ticker_day_cap_skips": 0,
            "overlap_skips": 0,
            "signals": int(d.height),
        }
        if not d.height:
            daily.append(base)
            continue
        session_end_us = int(d["session_end_us"][0])
        t = d["signal_us"].to_numpy()
        score = d["score"].to_numpy()
        tick = d["ticker"].to_list()
        codes = {name: i for i, name in enumerate(sorted(set(tick)))}
        code = np.array([codes[s] for s in tick], dtype=np.int64)
        order = np.lexsort((code, -score, t))
        g = d[gross].to_numpy().astype(float)
        xe = d[exit_et].to_numpy().astype(float)
        eet = d["entry_et"].to_numpy()
        eop = d["entry_open"].to_numpy().astype(float)
        cash, queue, active = float(BOOK), [], set()
        attempts_ct: dict[str, int] = {}
        last_attempt: dict[str, int] = {}
        leg = 0
        for i in order:
            tt = int(t[i])
            while queue and queue[0][0] <= tt:
                _, _, sym, proceeds = heapq.heappop(queue)
                active.discard(sym)
                cash += proceeds
            sym = tick[i]
            if sym in active:
                base["overlap_skips"] += 1
                continue
            if attempts_ct.get(sym, 0) >= MAX_ATTEMPTS_PER_TICKER:
                base["ticker_day_cap_skips"] += 1
                continue
            if tt - last_attempt.get(sym, -(1 << 62)) < cooldown_us:
                base["cooldown_skips"] += 1
                continue
            if cash + 1e-8 < ORDER_BUDGET:
                base["cash_skips"] += 1
                continue
            attempts_ct[sym] = attempts_ct.get(sym, 0) + 1
            last_attempt[sym] = tt
            base["attempts"] += 1
            leg += 1
            cash -= ORDER_BUDGET
            active.add(sym)
            gv = g[i]
            if not np.isfinite(gv):
                base["unknown"] += 1
                net, proceeds, release, status = None, 0.0, session_end_us + 1, "unknown_pending"
            else:
                net = (1.0 + gv) * (1.0 - side) / (1.0 + side) - 1.0
                proceeds = ORDER_BUDGET * (1.0 + net)
                base["known_pnl"] += ORDER_BUDGET * net
                release = int(xe[i]) if np.isfinite(xe[i]) else session_end_us + 1
                status = "known_touch"
            heapq.heappush(queue, (release, leg, sym, proceeds))
            trades.append(
                {
                    "day": day,
                    "ticker": sym,
                    "t": tt,
                    "leg_index": leg,
                    "entry_et": int(eet[i]),
                    "entry_open": float(eop[i]),
                    "exit_et": release,
                    "horizon_seconds": horizon,
                    "threshold": threshold,
                    "score": float(score[i]),
                    "gross": float(gv) if np.isfinite(gv) else None,
                    "net": net,
                    "cost_bps": cost_bps,
                    "order_budget": ORDER_BUDGET,
                    "status": status,
                }
            )
        base["lower_bound_pnl"] = base["known_pnl"] - ORDER_BUDGET * base["unknown"]
        daily.append(base)
    metrics = {
        "period_days": len(days),
        "horizon_seconds": horizon,
        "threshold": threshold,
        "cost_bps_residual": cost_bps,
        "n_signals": int(sig.height),
        "attempts": int(sum(r["attempts"] for r in daily)),
        "fills": len(trades),
        "known_fills": int(sum(r["net"] is not None for r in trades)),
        "unknown_fills": int(sum(r["unknown"] for r in daily)),
        "skips": {
            "cash": int(sum(r["cash_skips"] for r in daily)),
            "cooldown": int(sum(r["cooldown_skips"] for r in daily)),
            "ticker_day_cap": int(sum(r["ticker_day_cap_skips"] for r in daily)),
            "overlap": int(sum(r["overlap_skips"] for r in daily)),
        },
        "daily": daily,
        "trades": trades,
    }
    return metrics, trades


# ----- metrics, coverage lower bounds, uncertainty --------------------------
def bootstrap_daily(values: list[float], n: int = BOOT_N, seed: int = BOOT_SEED) -> dict:
    """Independent-day bootstrap over the daily series (days resampled with replacement)."""
    r = np.asarray(values, dtype=float)
    if r.size == 0:
        return {"n_days": 0, "n_boot": n, "seed": seed}
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


def adjust_coverage(daily: list[dict], days: list[str], cov: dict[str, dict]) -> list[dict]:
    """Coverage-unknown days take the whole -$750 book lower bound for that date."""
    out, unknown = [], []
    by_day = {r["day"]: r for r in daily}
    for day in days:
        row = dict(
            by_day.get(
                day,
                {
                    "day": day,
                    "attempts": 0,
                    "known_pnl": 0.0,
                    "unknown": 0,
                    "lower_bound_pnl": 0.0,
                    "cash_skips": 0,
                    "cooldown_skips": 0,
                    "ticker_day_cap_skips": 0,
                    "overlap_skips": 0,
                    "signals": 0,
                },
            )
        )
        if not cov.get(day, {}).get("complete", False):
            unknown.append(day)
            row["coverage_unknown"] = True
            row["lower_bound_pnl"] = float(-BOOK)
        else:
            row["coverage_unknown"] = False
        out.append(row)
    for row in out:
        row["coverage_unknown"] = row["day"] in set(unknown)
    return out


def incremental_legs(trades: list[dict]) -> dict:
    """Marginal PnL by intra-day attempt order (leg 1..MAX) - the incremental legs."""
    acc: dict[int, dict] = {}
    for tr in trades:
        leg = int(tr["leg_index"])
        slot = acc.setdefault(leg, {"attempts": 0, "known": 0, "unknown": 0, "net_usd": 0.0})
        slot["attempts"] += 1
        if tr["net"] is None:
            slot["unknown"] += 1
        else:
            slot["known"] += 1
            slot["net_usd"] += ORDER_BUDGET * tr["net"]
    out = {}
    for leg in sorted(acc):
        v = acc[leg]
        out[str(leg)] = {
            "attempts": v["attempts"],
            "n_known_fills": v["known"],
            "n_unknown_fills": v["unknown"],
            "mean_net_usd_per_known_fill": (v["net_usd"] / v["known"] if v["known"] else None),
            "total_net_usd": v["net_usd"] - ORDER_BUDGET * v["unknown"],
        }
    return out


def cell_view(
    metrics: dict,
    trades: list[dict],
    days: list[str],
    period: str,
    horizon: int,
    threshold: float,
    cost_bps: float,
    cov: dict[str, dict] | None = None,
    n_states: int | None = None,
) -> dict:
    """Summary view of one replay cell: N/traded days next to every mean."""
    cov = cov or {}
    daily = adjust_coverage(metrics["daily"], days, cov)
    lower = np.array([r["lower_bound_pnl"] for r in daily], dtype=float)
    complete = np.array(
        [r["lower_bound_pnl"] for r in daily if not r["coverage_unknown"]], dtype=float
    )
    nets = np.array([tr["net"] for tr in trades if tr["net"] is not None], dtype=float)
    wins = nets[nets > 0].sum() if nets.size else 0.0
    losses = -nets[nets < 0].sum() if nets.size else 0.0
    monthly: dict[str, list[float]] = {}
    for r in daily:
        monthly.setdefault(r["day"][:7], []).append(float(r["lower_bound_pnl"]))
    traded = {tr["day"] for tr in trades}
    unknown_days = sorted(r["day"] for r in daily if r["coverage_unknown"])
    n_days = len(days)
    mean_daily = float(lower.mean()) if lower.size else None
    return {
        "period": period,
        "horizon_seconds": horizon,
        "threshold": threshold,
        "cost_bps_residual": cost_bps,
        "n_days": n_days,
        "n_traded_days": len(traded),
        "n_signals": metrics["n_signals"],
        "n_states": n_states,
        "signals_per_day": (metrics["n_signals"] / n_days) if n_days else None,
        "qualified_states_per_day": (n_states / n_days) if (n_days and n_states) else None,
        "attempts": metrics["attempts"],
        "attempts_per_traded_day": (metrics["attempts"] / len(traded)) if traded else None,
        "fills": metrics["fills"],
        "n_known_fills": int(nets.size),
        "n_unknown_fills": metrics["unknown_fills"],
        "coverage_unknown_days": len(unknown_days),
        "coverage_unknown_day_list": unknown_days,
        "skips": metrics["skips"],
        "mean_daily_net_usd": mean_daily,
        "mean_daily_net_known_complete_usd": float(complete.mean()) if complete.size else None,
        "n_known_complete_days": int(complete.size),
        "mean_daily_net_annualized_usd": (
            mean_daily * ANNUAL_DAYS if mean_daily is not None else None
        ),
        "annualization": "mean daily net x 252 trading days, simple research estimate, not CAGR",
        "day_bootstrap_ci95_usd": bootstrap_daily(lower.tolist()),
        "known_fills_per_day": (int(nets.size) / n_days) if n_days else None,
        "mean_net_known_fill_usd": float(ORDER_BUDGET * nets.mean()) if nets.size else None,
        "mean_net_known_fill_fraction": float(nets.mean()) if nets.size else None,
        "known_win_rate": float((nets > 0).mean()) if nets.size else None,
        "known_profit_factor": (float(wins / losses) if losses else None),
        "worst_known_fill_usd": float(ORDER_BUDGET * nets.min()) if nets.size else None,
        "best_known_fill_usd": float(ORDER_BUDGET * nets.max()) if nets.size else None,
        "incremental_legs": incremental_legs(trades),
        "monthly_mean_net_usd": {k: float(np.mean(v)) for k, v in sorted(monthly.items())},
        "positive_months": int(sum(np.mean(v) > 0 for v in monthly.values())),
        "months": len(monthly),
        "underpowered": bool(nets.size < UNDERPOWERED_KNOWN_FILLS),
        "research_book": BOOK,
    }


def run_cell(
    scores: pl.DataFrame,
    days: list[str],
    horizon: int,
    threshold: float,
    cost_bps: float,
    cov: dict[str, dict],
    n_states: int,
    period: str,
) -> tuple[dict, list[dict]]:
    metrics, trades = replay_cell(scores, days, horizon, threshold, cost_bps)
    view = cell_view(metrics, trades, days, period, horizon, threshold, cost_bps, cov, n_states)
    return view, trades


# ----- selection (frozen BEFORE any late record is built) -------------------
def choose_cell(surface: dict[tuple[int, float], dict]) -> dict | None:
    """Deterministic argmax of the validation objective; no power floor, no auto-kill."""
    ranked = []
    for (h, thr), v in surface.items():
        mean = v.get("mean_daily_net_usd")
        if mean is None:
            continue
        ranked.append(((mean, v["n_known_fills"], -h, -thr), h, thr, v))
    if not ranked:
        return None
    ranked.sort(key=lambda r: r[0], reverse=True)
    (_, h, thr, v) = ranked[0]
    return {
        "horizon": h,
        "threshold": thr,
        "validation_mean_daily_net_usd": v["mean_daily_net_usd"],
        "validation_n_days": v.get("n_days"),
        "validation_n_traded_days": v.get("n_traded_days"),
        "validation_n_known_fills": v.get("n_known_fills"),
        "validation_day_bootstrap_ci95_usd": v.get("day_bootstrap_ci95_usd"),
        "validation_underpowered": v.get("underpowered"),
        "selection_cost_bps": SELECT_COST,
        "objective": "validation mean_daily_net_usd (whole-book lower bound at 10bps residual)",
        "tie_break": "(mean_daily_net_usd, n_known_fills, -horizon, -threshold)",
        "rationale": (
            f"highest validation mean daily net $ among all "
            f"{len(surface)} (head x threshold) views; no power floor applied"
        ),
    }


def freeze_identity(
    prior: dict | None, fit_report: dict | None, chosen: dict | None
) -> tuple[str, dict | None, bool]:
    """Freeze identity for a (re-)freeze: ``(frozen_at, fit_evidence, schema_replay)``.

    A select that supplies NO fresh fit evidence replays the SAME stored models against the
    SAME validation days, so an unchanged chosen view is a deterministic SCHEMA REPLAY and
    keeps the ORIGINAL freeze identity (first timestamp + fit evidence) - a re-stamped freeze
    would fake a fresh selection over previously unseen validation. A caller that fits
    (cmd_run), a first freeze, or a changed view is always a FRESH freeze.
    """
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    if not (prior and fit_report is None and chosen and prior.get("frozen_at")):
        return stamp, fit_report, False
    if int(prior["chosen"]["horizon"]) != int(chosen["horizon"]) or float(
        prior["chosen"]["threshold"]
    ) != float(chosen["threshold"]):
        return stamp, fit_report, False
    return prior["frozen_at"], prior.get("fit_report"), True


def select_stage(
    out_dir: Path, models: dict, val_days: list[str], fit_report: dict | None = None
) -> tuple[dict, dict]:
    """Full validation surface at the selection cost, then freeze the single best."""
    scores, cov = load_period_frames(out_dir, val_days)
    scored = score_frame(scores, models)
    n_states = int(scored.height)
    surface = {}
    for h in HORIZONS:
        for thr in THRESHOLDS:
            view, _ = run_cell(scored, val_days, h, thr, SELECT_COST, cov, n_states, "validation")
            surface[(h, thr)] = view
            print(
                f"[val] h={h} thr={thr:.3f} fills={view['fills']} "
                f"known={view['n_known_fills']} traded_days={view['n_traded_days']} "
                f"mean_daily_net_usd={view['mean_daily_net_usd']}",
                flush=True,
            )
    chosen = choose_cell(surface)
    diagnostics = {}
    if chosen:
        h, thr = chosen["horizon"], chosen["threshold"]
        for cost in COSTS:
            view, _ = run_cell(scored, val_days, h, thr, cost, cov, n_states, "validation")
            view["validation_cost_diagnostic"] = True
            diagnostics[str(cost)] = view
            print(
                f"[val-diag] h={h} thr={thr:.3f} cost={cost} "
                f"mean_daily_net_usd={view['mean_daily_net_usd']}",
                flush=True,
            )
    froze_at, fit_evidence, replay = freeze_identity(_prior_freeze(out_dir), fit_report, chosen)
    frozen = {
        "frozen_before_late": True,
        "frozen_at": froze_at,
        "selection": {
            "objective": "validation mean_daily_net_usd",
            "selection_cost_bps": SELECT_COST,
            "power_floor": None,
            "costs_reported_as_diagnostics": list(COSTS),
        },
        "chosen": chosen,
        "chosen_cost_diagnostics": diagnostics,
        "validation_surface": {f"h{h}|{thr:.3f}": v for (h, thr), v in surface.items()},
        "producer_sha256": producer_sha256(),
        "contract_sha256": _digest_bytes(CONTRACT),
        "coverage_epoch": COVERAGE_EPOCH,
        "fit_report": fit_evidence,
        "feature_order": list(FEATURES),
    }
    if replay:
        preserved = ["frozen_at"]
        if fit_evidence is not None:
            preserved.append("fit_report")
        frozen["schema_replay"] = {
            "refreshed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "chosen_unchanged": True,
            "preserved_from_original_freeze": preserved,
            "note": (
                "deterministic schema replay on the stored models, the frozen "
                "(head x threshold) grid and the same validation days; only the persisted "
                "surface views were re-emitted for the label rename "
                "mean_net_known_fill_pct -> mean_net_known_fill_fraction. NOT a fresh "
                "freeze and NOT previously unseen validation."
            ),
        }
    (out_dir / "surface_validation.json").write_text(
        json.dumps(
            {f"h{h}|{thr:.3f}": v for (h, thr), v in surface.items()}, indent=1, default=_default
        )
        + "\n"
    )
    _write_json_atomic(out_dir / "frozen_contract.json", frozen)
    tag = " (schema replay; first freeze identity kept)" if replay else ""
    print(f"[freeze] chosen={chosen}{tag} -> {out_dir / 'frozen_contract.json'}", flush=True)
    return frozen, surface


def load_surface(path: Path) -> dict[tuple[int, float], dict]:
    """Read the persisted validation surface under the writer's native two-part keys.

    select_stage writes ``f"h{h}|{thr:.3f}"`` (e.g. ``h5|0.001``): the horizon carries a
    literal ``h`` prefix, the threshold is a decimal fraction. Nothing else is accepted.
    """
    surface: dict[tuple[int, float], dict] = {}
    for key, v in json.loads(Path(path).read_text()).items():
        h, thr = key.split("|")
        surface[(int(h[1:]), float(thr))] = v
    return surface


def _prior_freeze(out_dir: Path) -> dict | None:
    """The freeze already on disk, if any (never raises: selection may be first)."""
    path = out_dir / "frozen_contract.json"
    if not path.exists():
        return None
    prior = json.loads(path.read_text())
    return prior if prior.get("chosen") else None


def load_frozen(out_dir: Path) -> dict:
    path = out_dir / "frozen_contract.json"
    if not path.exists():
        raise SystemExit(
            "frozen_contract.json missing: selection must be frozen before "
            "the late block is scored or replayed"
        )
    frozen = json.loads(path.read_text())
    if not frozen.get("chosen"):
        raise SystemExit("frozen_contract.json has no chosen cell")
    return frozen


# ----- late block (frozen config, cost ladder) ------------------------------
def late_stage(out_dir: Path, models: dict, frozen: dict, late_days: list[str]) -> dict:
    """Run the frozen (head, threshold) once on the late months at every reported cost."""
    chosen = frozen["chosen"]
    h, thr = int(chosen["horizon"]), float(chosen["threshold"])
    scores, cov = load_period_frames(out_dir, late_days)
    scored = score_frame(scores, models)
    n_states = int(scored.height)
    out_dir.joinpath("late").mkdir(parents=True, exist_ok=True)
    by_cost, trades_by_cost = {}, {}
    for cost in COSTS:
        view, trades = run_cell(scored, late_days, h, thr, cost, cov, n_states, "late")
        view["frozen_chosen"] = {"horizon": h, "threshold": thr}
        by_cost[str(cost)] = view
        trades_by_cost[cost] = trades
        if trades:
            pl.DataFrame(trades).write_parquet(
                out_dir / "late" / f"trades_late_h{h}_{int(thr * 1000):03d}_{cost}.parquet"
            )
        print(
            f"[late] h={h} thr={thr:.3f} cost={cost} fills={view['fills']} "
            f"known={view['n_known_fills']} traded_days={view['n_traded_days']} "
            f"mean_daily_net_usd={view['mean_daily_net_usd']}",
            flush=True,
        )
    sig = (
        scored.filter(pl.col("day").is_in(late_days) & (pl.col(f"pred_gross_{h}") >= thr))
        .select(
            [
                "day",
                "ticker",
                "signal_us",
                "entry_et",
                "entry_open",
                f"gross_{h}",
                f"exit_et_{h}",
                f"pred_gross_{h}",
            ]
        )
        .with_columns(pl.lit(h).alias("horizon_seconds"), pl.lit(thr).alias("threshold"))
    )
    sig.write_parquet(out_dir / "late" / f"signals_late_h{h}_{int(thr * 1000):03d}.parquet")
    return {
        "by_cost": by_cost,
        "n_states": n_states,
        "signals": int(sig.height),
        "chosen": {"horizon": h, "threshold": thr},
    }


def provenance(out_dir: Path, days: list[str]) -> dict:
    cov_hashes = {}
    for day in days:
        cpath = corpus_paths(out_dir, day)[1]
        if cpath.exists():
            obj = json.loads(cpath.read_text())
            cov_hashes[day] = obj.get("corpus_sha256")
    model_dir = out_dir / "models"
    return {
        "producer_sha256": producer_sha256(),
        "contract_sha256": _digest_bytes(CONTRACT),
        "coverage_epoch": COVERAGE_EPOCH,
        "coverage_epoch_source": "alpha_micro_core.COVERAGE_EPOCH",
        "model_files": {p.name: sha256_file(p) for p in sorted(model_dir.glob("*")) if p.is_file()}
        if model_dir.exists()
        else {},
        "corpus_day_sha256": cov_hashes,
    }


def verdict_of(val_cell: dict | None, late_by_cost: dict) -> tuple[str, list[str]]:
    notes = []
    val_mean = (val_cell or {}).get("mean_daily_net_usd")
    at10 = (late_by_cost.get("10") or {}).get("mean_daily_net_usd")
    at100 = (late_by_cost.get("100") or {}).get("mean_daily_net_usd")
    if at10 is None:
        return "no_late_observation", notes
    if val_mean is not None and val_mean > 0 and at10 > 0:
        verdict = "CANDIDATE_POSITIVE_EV_NEEDS_FORWARD (research evidence, not guaranteed alpha)"
    elif val_mean is not None and val_mean > 0 and at10 <= 0:
        verdict = "validation_only_not_confirmed_late"
        notes.append("validation mean positive at the frozen view but the late block is not")
    elif any((late_by_cost.get(str(c)) or {}).get("mean_daily_net_usd", 0) > 0 for c in (0, 25)):
        verdict = "cost_sensitive: positive only at 0/25bps residual; no 10bps pass"
    else:
        verdict = "no_edge_at_any_cost"
    if at100 is not None and at100 <= 0:
        notes.append("late block does not clear 100bps residual round-trip")
    return verdict, notes


def build_results(
    out_dir: Path, models: dict, frozen: dict, surface: dict, late: dict, per: dict[str, list[str]]
) -> dict:
    chosen = frozen.get("chosen") or {}
    val_cell = (
        (surface or {}).get((int(chosen["horizon"]), float(chosen["threshold"])))
        if chosen
        else None
    )
    late_by_cost = late["by_cost"]
    verdict, notes = verdict_of(val_cell, late_by_cost)
    payload = {
        "contract": {**CONTRACT, "producer_sha256": producer_sha256()},
        "provenance": provenance(out_dir, sorted(itertools.chain.from_iterable(per.values()))),
        "fit": frozen.get("fit_report"),
        "selection": frozen.get("selection"),
        "schema_replay": frozen.get("schema_replay"),
        "chosen": chosen,
        "chosen_cost_diagnostics": frozen.get("chosen_cost_diagnostics"),
        "validation_surface": frozen.get("validation_surface"),
        "validation_chosen_view": val_cell,
        "late": late_by_cost,
        "late_surface_meta": {"n_qualified_states": late["n_states"], "n_signals": late["signals"]},
        "verdict": verdict + ("; " + "; ".join(notes) if notes else ""),
        "assumptions": [
            "quote-supported touch is not a guaranteed exchange fill",
            "residual 0bps is a diagnostic; entry/exit prices are the real as-of ASK/BID",
            "UNKNOWN exits charge the full $250 order budget in the day lower bound, never cash",
            "cash reuse assumes an eligible margin account, not a $750 cash account",
            "development/validation/late blocks are previously explored markets, "
            "not pristine holdout",
            "annualization is mean daily net x 252, a simple research estimate, not a CAGR",
        ],
        "period_days": {k: len(v) for k, v in per.items()},
    }
    _write_json_atomic(out_dir / "results.json", payload)
    _write_json_atomic(
        out_dir / "summary.json",
        {
            "verdict": payload["verdict"],
            "chosen": chosen,
            "validation_chosen_view": val_cell,
            "late_by_cost": late_by_cost,
            "coverage": payload["provenance"].get("coverage_epoch"),
            "period_days": payload["period_days"],
        },
    )
    return payload


# ----- CLI ------------------------------------------------------------------
def ensure_contract(out: Path) -> None:
    contract = {**CONTRACT, "producer_sha256": producer_sha256()}
    path = out / "contract.json"
    if path.exists():
        prior = json.loads(path.read_text())
        if {k: v for k, v in prior.items() if k != "producer_sha256"} != {
            k: v for k, v in contract.items() if k != "producer_sha256"
        }:
            raise ValueError("output root contract mismatch; use a new versioned output root")
    else:
        path.write_text(json.dumps(contract, indent=2) + "\n")


def _subset(per: dict[str, list[str]], arg_days: str) -> dict[str, list[str]]:
    if not arg_days:
        return per
    want = set(arg_days.split(","))
    out = {k: [d for d in v if d in want] for k, v in per.items()}
    missing = sorted(want - set(itertools.chain.from_iterable(out.values())))
    if missing:
        raise ValueError(f"days outside fixed payoff periods: {missing}")
    return out


def cmd_run(args) -> None:
    t0 = time.time()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    ensure_contract(out)
    per = _subset(period_days(), args.days)
    pre = sorted(set(per["development"]) | set(per["validation"]))
    stage_corpus(args.data, out, pre, args.force, args.workers)
    models, fit_report = fit_models(out, per["development"])
    for h in HORIZONS:
        r = fit_report[str(h)] if str(h) in fit_report else fit_report[h]
        print(
            f"[fit] h={h}: rows={r['train_rows']} days={r['train_days']} "
            f"label_mean={r['label_unclipped']['mean']:.4f}",
            flush=True,
        )
    frozen, surface = select_stage(out, models, per["validation"], fit_report)
    # Freeze is on disk; only now are the late records even built, then run once.
    stage_corpus(args.data, out, per["late"], args.force, args.workers)
    late = late_stage(out, models, frozen, per["late"])
    payload = build_results(out, models, frozen, surface, late, per)
    print(f"[done] {payload['verdict']} ({round(time.time() - t0, 1)}s)", flush=True)


def cmd_corpus(args) -> None:
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    ensure_contract(out)
    per = _subset(period_days(), args.days)
    days = sorted(itertools.chain.from_iterable(per.values()))
    infos = stage_corpus(args.data, out, days, args.force, args.workers)
    _write_json_atomic(
        out / "day_stage.json",
        {
            "producer_sha256": producer_sha256(),
            "contract_sha256": _digest_bytes(CONTRACT),
            "days": [
                {
                    "day": i["day"],
                    "resumed": i.get("resumed", False),
                    "rows": i.get("rows", 0),
                    "coverage": i["coverage"],
                }
                for i in infos
            ],
        },
    )
    print(f"corpus: {len(infos)} days -> {out / 'corpus'}", flush=True)


def cmd_fit(args) -> None:
    out = args.out
    ensure_contract(out)
    per = _subset(period_days(), args.days)
    models, report = fit_models(out, per["development"])
    print(
        f"[fit] fit_days={len(report['fit_days'])} "
        f"excluded={len(report['excluded_days'])} -> {out / 'models'}",
        flush=True,
    )


def cmd_select(args) -> None:
    out = args.out
    ensure_contract(out)
    models = load_models(out / "models")
    per = _subset(period_days(), args.days)
    frozen, _ = select_stage(out, models, per["validation"], None)
    print(f"[freeze] {frozen['chosen']}", flush=True)


def cmd_late(args) -> None:
    out = args.out
    ensure_contract(out)
    models = load_models(out / "models")
    frozen = load_frozen(out)
    per = _subset(period_days(), args.days)
    late = late_stage(out, models, frozen, per["late"])
    surface = {}
    spath = out / "surface_validation.json"
    if spath.exists():
        surface = load_surface(spath)
    payload = build_results(out, models, frozen, surface, late, per)
    print(f"[done] {payload['verdict']}", flush=True)


def cmd_replay(args) -> None:
    """Functional full replay from the saved models: score -> signals -> replay."""
    out = args.out
    ensure_contract(out)
    models = load_models(out / "models")
    per = _subset(period_days(), args.days)
    days = per[args.period]
    scores, cov = load_period_frames(out, days)
    scored = score_frame(scores, models)
    view, trades = run_cell(
        scored, days, args.horizon, args.threshold, args.cost, cov, int(scored.height), args.period
    )
    d = out / "replay"
    d.mkdir(parents=True, exist_ok=True)
    tag = f"h{args.horizon}_{int(args.threshold * 1000):03d}_{args.period}_{int(args.cost)}"
    if trades:
        pl.DataFrame(trades).write_parquet(d / f"trades_{tag}.parquet")
    (d / f"metrics_{tag}.json").write_text(json.dumps(view, indent=1, default=_default) + "\n")
    print(
        json.dumps(
            {
                "tag": tag,
                "mean_daily_net_usd": view["mean_daily_net_usd"],
                "fills": view["fills"],
                "n_known_fills": view["n_known_fills"],
                "n_traded_days": view["n_traded_days"],
                "day_bootstrap_ci95_usd": view["day_bootstrap_ci95_usd"],
            },
            indent=1,
        ),
        flush=True,
    )


def cmd_predict(args) -> None:
    models = load_models(args.models)
    if args.file:
        state = pl.read_parquet(Path(args.file)).to_dicts()[0]
    elif args.state:
        state = json.loads(args.state)
    else:
        raise SystemExit("provide --state JSON or --file parquet")
    x = np.array([[float(state[c]) for c in FEATURES]], dtype=np.float32)
    pred = {h: float(models[h].predict(x)[0]) for h in HORIZONS}
    print(
        json.dumps(
            {
                "predicted_gross_after_touch": pred,
                "clip_fit": list(CLIP),
                "clearance_bps": {h: pred[h] * 10000 for h in HORIZONS},
            },
            indent=2,
        ),
        flush=True,
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, default=DATA)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument(
        "--days", type=str, default="", help="optional comma-separated day subset (smoke runs)"
    )
    p.add_argument("--force", action="store_true", help="rebuild corpus days, ignore resume")
    p.add_argument(
        "--workers", type=int, default=1, help="corpus build processes; default 1 (single process)"
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, helptext in (
        ("run", "corpus -> fit -> select/freeze -> late full pipeline"),
        ("corpus", "build the per-day corpus cache only"),
        ("fit", "fit both heads on fully covered development days"),
        ("select", "validation surface + freeze the chosen view"),
        ("late", "run the frozen view once on the late block"),
    ):
        sub.add_parser(name, help=helptext)
    rp = sub.add_parser("replay", help="functional full replay from saved models")
    rp.add_argument("--horizon", type=int, choices=HORIZONS, required=True)
    rp.add_argument("--threshold", type=float, required=True)
    rp.add_argument("--period", choices=list(MONTHS), required=True)
    rp.add_argument("--cost", type=float, default=SELECT_COST)
    pr = sub.add_parser("predict", help="per-state predictor from saved models")
    pr.add_argument("--models", type=Path, default=None)
    pr.add_argument("--state", type=str, help="JSON dict of the frozen features")
    pr.add_argument("--file", type=str, help="parquet whose first row is the state")
    args = p.parse_args()
    if args.cmd == "predict" and args.models is None:
        args.models = args.out / "models"
    {
        "run": cmd_run,
        "corpus": cmd_corpus,
        "fit": cmd_fit,
        "select": cmd_select,
        "late": cmd_late,
        "replay": cmd_replay,
        "predict": cmd_predict,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
