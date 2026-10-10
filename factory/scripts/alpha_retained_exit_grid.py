#!/usr/bin/env python3
"""Exit-grid variant of the retained sparse learned selector (threshold x horizon x cadence).

The retained lane (factory/scripts/alpha_sparse_daily.py) pinned ONE admission threshold
(0.030), THREE exit horizons it already had labels for (15/30/60) and TWO cadences (one
attempt per ticker per session, or re-entry after a flat 15-minute cooldown measured from the
ACTUAL exit), all on the ONE stored h60 payoff model. This producer asks the product
question that lane deliberately left closed: on the SAME immutable model and the SAME
funded-reserve account, which THRESHOLD x EXIT-HORIZON x CADENCE cell is the most profitable
top-gainer mechanism, ranked on 2023 validation only and reported beside a full cost ladder?

What is fixed and what varies (nothing else moves):
* ONE stored h60 model is loaded from ``learned/models``; its sha256 and its feature_order
  are verified against the frozen ``FEATURES_ALL`` at load. No refit, no HPO, no new
  ticker features, no label-sided feature. All 50 views read the SAME predicted score, so a
  view can only change WHICH states are admitted, HOW LONG a position is held and HOW OFTEN
  the ticker may recur -- never what the state means.
* The grid is fixed in code and written to ``contract.json`` BEFORE any validation score is
  computed: thresholds (0.020, 0.025, 0.030, 0.035, 0.040) x exit horizons (15, 30, 60, 120,
  390 minutes) x cadences (once-per-ticker-session; repeat with a flat 15-minute cooldown
  after the ACTUAL exit and at most 3 attempts per ticker per session) = 50 views.
* Exit labels: h15/h60/h390 come from the panel unchanged; h30 and h120 are recomputed here
  per day from the STRICT ACTUAL minute opens of the same tape the panel was built from
  (first actual minute open at/after min(entry+h, session_last_minute); no interpolation, no
  close-price proxy, no future-fill filtering: a state whose exit minute never prints stays
  UNKNOWN, never cash and never an interpolation).
* Replay conventions are the shared funded-reserve conventions of
  ``alpha_sparse_daily.replay_view``: $3,000 research reserve (3 x $1,000 tickets), no
  leverage, fees on BOTH legs, one position per symbol, same-clock exits precede buys, the
  highest-score eligible intents of a simultaneous clock are funded before any fill outcome
  is known, an UNKNOWN exit keeps its slot and blocks its ticker for the rest of the session,
  an unfilled attempt minute reserves and releases its ticket fee-free, and every calendar
  day stays in the denominator as a cash day. A fill's both-legs net is what flows back
  into the book, so the cash a later minute can fund depends on the rung: every rung is
  replayed in full (never derived from another rung's fills) and each cell carries its own
  honest fill counts.

Two reported bases, never blended: the KNOWN CONTRIBUTION (the dollars earned by the fills
whose exit actually printed -- a partial figure that ignores UNKNOWN exits) and the
FULL-LOSS LOWER BOUND (the same dollars minus one whole ticket per UNKNOWN fill). An UNKNOWN
exit is never treated as cash and its loss is never treated as an expected value: it is
reported as the separately-labeled bound it is. Every cost rung on both blocks is a TOTAL
modeled minute-proxy friction scenario on the proxy prices, NOT a broker fee claim: a
primary US broker's regular schedule (zero commission, SEC 20.6 per $1M, CAT 0.000003 per
side, daily cent rounding) is typically well under 1 bp on a $1,000 ticket, so the 25..150
rungs describe a LOW-FEE OPPORTUNITY set. The ladder is a comparison set, never a hurdle:
no rung closes a candidate, and no count, median, CI or cost floor gates a cell.

Selection: ONE pre-declared selection, computed on the 2023 validation block (250 days) ONLY
and frozen to disk BEFORE any late-block outcome exists: the view with the highest 2023
validation KNOWN-CONTRIBUTION dollars per FULL CALENDAR day at the pre-declared 25 bps rung,
ties broken by known fills, then fewest attempts, then the view key. The chosen view then
replays the 2025-02..2026-05 block (332 days) for transparency with the choice immutable,
and is compared incrementally against the frozen 0.030/h60/repeat reference of the stored
lane as plain arithmetic on the same basis. That late block was previously explored, so late
outcomes are DISCOVERY-NOT-VALIDATED: not pristine, not a fresh unseen holdout, and the
selection stays a discovery object.

Simple 252-session annualization (dollars/day x 252) is a DAILY-RESET CONVENTION on a fixed
reserve -- every session restarts from the same $3,000 -- NOT a self-financing CAGR; an
additive carry-equity check (fixed tickets, no compounding) is reported beside it.

Capacity is a research assumption, not a scalability claim: the $1,000 tickets are book size
on a $3,000 reserve and displayed top-of-book depth is not claimed to support them.

Day-parallel-safe by construction: one process, one resident day at collection time, per-day
atomic parts (parquet + coverage json) keyed by a resume hash over the producer sha, the
declared contract sha, the model sha, the day and the schema, so --resume skips intact days
and a changed producer/contract/model invalidates the stale parts. The corpus is never
concatenated across blocks; each block concatenates only its own days.

Usage:
  uv run --no-sync python factory/scripts/alpha_retained_exit_grid.py
  uv run --no-sync python factory/scripts/alpha_retained_exit_grid.py --out <dir>
  uv run --no-sync python factory/scripts/alpha_retained_exit_grid.py --resume
  uv run --no-sync python factory/scripts/alpha_retained_exit_grid.py --skip-late
  uv run --no-sync python factory/scripts/alpha_retained_exit_grid.py --days 2023-05-01 ...
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import shutil
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import polars as pl
from alpha_open_learned import (
    CONF_PERIOD,
    FEATURES_ALL,
    PANEL_ROOT,
    VAL_PERIOD,
    bootstrap_daily,
    feature_matrix,
    sha256_file,
)
from alpha_open_panel import ROOT, allowed
from alpha_open_sim import CONTEXT_FEATURES, causal_liquidity, period

# ----- fixed configuration (no HPO, no refit) --------------------------------
MODEL_HORIZON = 60  # the one stored head that is ever loaded
MODEL_NAME = "payoff_h60.joblib"
# Pinned digest of the ONE immutable stored model: verified at load, never refit.
EXPECTED_MODEL_SHA256 = "c5493c6c043a22e544ca98aa4fb5620292d4a1aeb370f63f6492907334f0bf96"
THRESHOLDS = (0.020, 0.025, 0.030, 0.035, 0.040)  # admission bars on the SAME score
HORIZONS = (15, 30, 60, 120, 390)  # exit horizons in minutes
CADENCE_ONCE = "once"  # one attempt per ticker per session
CADENCE_REPEAT = "repeat"  # re-entry after a flat cooldown from the ACTUAL exit
CADENCES = (CADENCE_ONCE, CADENCE_REPEAT)
COOLDOWN_MIN = 15  # flat minutes after the ACTUAL exit
ATTEMPTS_ONCE = 1
ATTEMPTS_REPEAT = 3  # attempts per ticker per session
MAX_POSITIONS = 3  # one position per symbol, at most three at once
ORDER_BUDGET = 1000.0  # research ticket size (book only, not capacity)
RESERVE_USD = MAX_POSITIONS * ORDER_BUDGET
PANEL_LABEL_HORIZONS = (15, 60, 390)  # exit labels the panel producer already stores
RECOMPUTE_HORIZONS = (30, 120)  # exit labels recomputed here from actual opens
MIN_THRESHOLD = min(THRESHOLDS)  # collection keeps every state any view could admit
# Reported cost scenarios in bps (TOTAL round-trip modeled friction on the minute-open
# proxy prices). The ladder is a COMPARISON SET, never a hurdle: no scenario closes a
# candidate and no rung falsifies anything. 25..150 bps describe a LOW-FEE OPPORTUNITY
# set, not a fee claim - a primary US broker's regular schedule (zero commission, SEC
# 20.6 per $1M, CAT 0.000003 per side, daily cent rounding) is typically well under 1 bp
# on a $250-$1,000 ticket, so the actual broker fee is sourced separately, not here.
COST_SCENARIOS_BPS = (25.0, 50.0, 75.0, 100.0, 125.0, 150.0)
# Historical diagnostic only: the 200 bp rung the stored late ladder already carried
# (alpha_open_learned.CONFIRM_COSTS). Reported for continuity, never a modern fee claim.
HISTORICAL_COST_BPS = 200.0
VAL_COSTS = COST_SCENARIOS_BPS + (HISTORICAL_COST_BPS,)  # validation cost ladder
LATE_COSTS = COST_SCENARIOS_BPS + (HISTORICAL_COST_BPS,)  # late ladder = scenarios + diagnostic
SELECT_COST = 25.0  # pre-declared rung of the single pre-declared selection (NOT a fee claim)
REFERENCE_COST = 100.0  # rung of the stored lane's reference figures (NOT a hurdle)
TRADING_DAYS_PER_YEAR = 252  # session-count convention for $/year (daily reset, not CAGR)
BARS_ROOT = ROOT / "data" / "sip" / "net" / "bars"
OUTPUT = PANEL_ROOT / "retained_exit_grid"
EXPECTED_DAYS = {VAL_PERIOD: 250, CONF_PERIOD: 332}
ANALYSIS_PERIODS = (VAL_PERIOD, CONF_PERIOD)
FLOAT_TOL = 1e-9
STATUS = "DISCOVERY-NOT-VALIDATED"
SCHEMA = "retained_exit_grid_parts_v1"
# The frozen reference this grid is compared against: the stored lane's chosen view
# (0.030 threshold, h60 exit, repeat with a flat 15-minute cooldown from the ACTUAL exit).
REFERENCE_VIEW = "repeat_h60_thr030"
REFERENCE_LANE = {
    "producer": "factory/scripts/alpha_sparse_daily.py",
    "lane_root": str(PANEL_ROOT / "learned_sparse_daily"),
    "results_json": str(PANEL_ROOT / "learned_sparse_daily" / "results.json"),
    "view": {
        "name": "repeat_h60",
        "threshold": 0.030,
        "horizon": 60,
        "cooldown_min": 15,
        "max_attempts": 3,
    },
    "metric_basis": (
        "the stored lane's dollars_per_day = mean daily FULL-LOSS LOWER-BOUND return x the "
        "$3,000 reserve, at its 100 bps HISTORICAL rung"
    ),
    "validation_2023": {
        "days": 250,
        "known_fills": 67,
        "traded_days": 55,
        "dollars_per_day_100bps": 2.8387168415288033,
    },
    "late_2025_02_2026_05": {
        "days": 332,
        "known_fills": 157,
        "traded_days": 124,
        "dollars_per_day_100bps": 5.677768172596966,
        "turnover_usd_100bps": 315885.0190333022,
    },
}


# ----- the fifty fixed views --------------------------------------------------
@dataclass(frozen=True)
class View:
    """One admission/exit/cadence cell of the predeclared grid.

    Same stored score for every view; only the admission bar, the exit horizon and the
    re-entry cadence differ. Cooldown is flat minutes after the ACTUAL exit minute.
    """

    name: str
    threshold: float
    horizon: int
    cooldown_min: int
    max_attempts: int
    cadence: str
    control: bool = False

    @property
    def reenters(self) -> bool:
        return self.max_attempts > 1

    @property
    def cadence_str(self) -> str:
        return (
            f"thr{self.threshold:.3f}|exit_h{self.horizon}|cadence_{self.cadence}|"
            f"flat_cooldown_{self.cooldown_min}min|max_attempts_{self.max_attempts}"
        )


def _view(cadence: str, horizon: int, threshold: float) -> View:
    attempts = ATTEMPTS_ONCE if cadence == CADENCE_ONCE else ATTEMPTS_REPEAT
    return View(
        name=f"{cadence}_h{horizon}_thr{int(round(threshold * 1000)):03d}",
        threshold=float(threshold),
        horizon=int(horizon),
        cooldown_min=COOLDOWN_MIN,
        max_attempts=attempts,
        cadence=cadence,
        # The frozen stored-lane reference cell: 0.030 / h60 / repeat.
        control=(
            cadence == CADENCE_REPEAT and horizon == 60 and abs(float(threshold) - 0.030) < 1e-12
        ),
    )


VIEWS = tuple(
    _view(cadence, horizon, threshold)
    for cadence in CADENCES
    for horizon in HORIZONS
    for threshold in THRESHOLDS
)
VIEWS_BY_NAME = {v.name: v for v in VIEWS}
REPEAT_VIEWS = tuple(v for v in VIEWS if v.reenters)

# ----- columns of one per-day signal part ------------------------------------
SIGNAL_BASE = ["day", "ticker", "t", "entry_et", "entry_open", "entry_status", "session_end"]
PART_LABELS = [f"{kind}_{h}" for h in HORIZONS for kind in ("gross", "exit_et", "exit_status")]
PART_COLUMNS = SIGNAL_BASE + PART_LABELS + ["score"]
PART_SCHEMA = {
    "day": pl.String,
    "ticker": pl.String,
    "t": pl.Int64,
    "entry_et": pl.Int64,
    "entry_open": pl.Float64,
    "entry_status": pl.String,
    "session_end": pl.Int64,
    **{f"exit_et_{h}": pl.Int64 for h in HORIZONS},
    **{f"gross_{h}": pl.Float64 for h in HORIZONS},
    **{f"exit_status_{h}": pl.String for h in HORIZONS},
    "score": pl.Float64,
}


def _empty_part_frame() -> pl.DataFrame:
    return pl.DataFrame(schema={k: PART_SCHEMA[k] for k in PART_COLUMNS})


# ----- exit labels recomputed from strict actual minute opens ----------------
def horizon_label(
    et: np.ndarray,
    opens: np.ndarray,
    entry_et: int,
    entry_open: float,
    filled: bool,
    horizon: int,
    session_end: int,
) -> tuple:
    """Realized gross payoff of a minute-t entry using STRICT ACTUAL minute opens.

    Mirrors the panel producer and alpha_sparse_daily exactly: exit = first actual minute
    open at/after min(entry+h, session_last_minute). A minute that never prints leaves the
    payoff UNKNOWN (never cash, never an interpolation); a state that never filled is cash
    for that minute. ``et`` must be the tape's own ascending minute stamps.
    """
    if not filled:
        return 0.0, None, "unfilled_cash"
    if entry_open is None or not np.isfinite(entry_open) or entry_open <= 0:
        return None, None, "unknown_pending"
    target = min(int(entry_et) + horizon, int(session_end))
    j = int(np.searchsorted(et, target))
    if j < len(et) and int(et[j]) <= int(session_end):
        return float(opens[j]) / float(entry_open) - 1.0, int(et[j]), "observed_open_proxy"
    return None, None, "unknown_pending"


def load_day_opens(day: str, tickers: list[str]) -> tuple[dict, bool]:
    """One day's tape minute opens per ticker; never more than one day resident."""
    path = BARS_ROOT / f"{day}.parquet"
    if not path.exists():
        return {}, False
    frame = (
        pl.read_parquet(path).filter(pl.col("ticker").is_in(tickers)).select("ticker", "et", "open")
    )
    if frame.height and frame.select(pl.struct("ticker", "et").is_duplicated().any()).item():
        raise ValueError(f"duplicate tape bars: {day}")
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    if frame.height:
        for key, part in frame.sort("et").partition_by("ticker", as_dict=True).items():
            out[key[0]] = (
                part["et"].to_numpy().astype(np.int64),
                part["open"].to_numpy().astype(np.float64),
            )
    return out, True


def attach_recomputed_labels(day: str, sig: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    """Attach the h30/h120 exit labels (and any other recomputed horizon) to one day.

    A ticker whose tape stream is absent, or a day whose tape file is missing, yields
    UNKNOWN labels -- never a silently dropped state and never cash.
    """
    if not sig.height:
        return sig, {
            "day_file_present": True,
            "rows": 0,
            "missing_symbol_streams": [],
            "label_status": {},
        }
    tickers = sorted(set(sig["ticker"].unique().to_list()))
    tapes, present = load_day_opens(day, tickers)
    missing = sorted({t for t in sig["ticker"].to_list() if t not in tapes})
    status_counts: Counter = Counter()
    for horizon in RECOMPUTE_HORIZONS:
        gross, exit_et, status = [], [], []
        for r in sig.to_dicts():
            tape = tapes.get(r["ticker"])
            if tape is None:
                gross.append(None)
                exit_et.append(None)
                status.append("unknown_pending")
                status_counts["unknown_pending"] += 1
                continue
            g, x, s = horizon_label(
                tape[0],
                tape[1],
                r["entry_et"],
                r["entry_open"],
                r["entry_status"] == "filled_proxy",
                horizon,
                r["session_end"],
            )
            gross.append(g)
            exit_et.append(x)
            status.append(s)
            status_counts[s] += 1
        sig = sig.with_columns(
            [
                pl.Series(f"gross_{horizon}", gross, dtype=pl.Float64),
                pl.Series(f"exit_et_{horizon}", exit_et, dtype=pl.Int64),
                pl.Series(f"exit_status_{horizon}", status, dtype=pl.String),
            ]
        )
    return sig, {
        "day_file_present": present,
        "rows": int(sig.height),
        "missing_symbol_streams": missing,
        "label_status": dict(status_counts),
    }


# ----- stored model (loaded, never fitted) ------------------------------------
def load_stored_model(model_dir: Path) -> tuple[dict, Path]:
    """Load the ONE immutable stored head; verify its digest and frozen feature order."""
    path = model_dir / MODEL_NAME
    if not path.exists():
        raise SystemExit(f"[retained-exit-grid] stored model missing: {path}")
    digest = sha256_file(path)
    if digest != EXPECTED_MODEL_SHA256:
        raise SystemExit(
            f"[retained-exit-grid] stored model sha256 {digest} != pinned {EXPECTED_MODEL_SHA256}"
        )
    bundle = joblib.load(path)
    order = list(bundle.get("feature_order") or [])
    if order != list(FEATURES_ALL):
        raise SystemExit("[retained-exit-grid] stored feature_order != current FEATURES_ALL")
    if int(bundle.get("horizon", -1)) != MODEL_HORIZON:
        raise SystemExit(
            f"[retained-exit-grid] stored horizon {bundle.get('horizon')} != {MODEL_HORIZON}"
        )
    return bundle, path


# ----- past-only peer context, per day (same partition key as the panel) -----
def day_context(frame: pl.DataFrame) -> pl.DataFrame:
    """Re-derive the four past-only peer-context columns the stored model needs.

    ``alpha_open_sim.load_panel`` computes them at load time over ``over(["day","t"])``
    windows, so the per-day panel files do not carry them. Recomputing them per day on the
    SAME partition key reproduces the exact values (the window is entirely inside one
    session, and peer_positive3 includes the subject itself, exactly as
    ``CONTEXT_FEATURES`` was defined).
    """
    if all(c in frame.columns for c in CONTEXT_FEATURES):
        return frame
    out = frame.with_columns(
        [
            (pl.col("ret3") > 0).sum().over(["day", "t"]).alias("peer_positive3"),
            pl.col("gain_open").mean().over(["day", "t"]).alias("peer_gain_mean"),
            (pl.col("gain_open") >= 0.10).sum().over(["day", "t"]).alias("peer_breadth10"),
            pl.col("range5").median().over(["day", "t"]).alias("peer_heat"),
        ]
    )
    missing = [c for c in CONTEXT_FEATURES if c not in out.columns]
    if missing:
        raise ValueError(f"peer context not derivable, missing {missing}")
    return out


# ----- memory-bounded per-day collection with atomic resume -------------------
def resume_hash(day: str, producer_sha: str, contract_sha: str, model_sha: str) -> str:
    """Identity of one per-day part: producer, declared contract, model, day and schema."""
    payload = "|".join([SCHEMA, producer_sha, contract_sha, model_sha, day])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def part_intact(day: str, parts_dir: Path, expected: str) -> bool:
    """A cached day part counts as intact only if its hash and its columns still match."""
    parquet = parts_dir / f"{day}.parquet"
    cov = parts_dir / f"{day}.cov.json"
    if not parquet.exists() or not cov.exists():
        return False
    try:
        meta = json.loads(cov.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if meta.get("resume_hash") != expected:
        return False
    try:
        cols = set(pl.read_parquet_schema(parquet).keys())
    except Exception:
        return False
    return cols == set(PART_COLUMNS)


def collect_day(
    day: str,
    model,
    parts_dir: Path,
    producer_sha: str,
    contract_sha: str,
    model_sha: str,
) -> dict:
    """Score ONE panel day and keep every state any grid view could admit.

    The protected guard runs BEFORE any file read. One day frame, one tape frame and one
    day's signal part are resident at a time; the part is written atomically (parquet +
    coverage json) so an interrupted run resumes exactly where it stopped.
    """
    if not allowed(day):
        raise ValueError(f"protected/out-of-scope day refused: {day}")
    frame = pl.read_parquet(PANEL_ROOT / "days" / f"{day}.parquet")
    frame = day_context(frame)
    cand = frame.filter(causal_liquidity(frame))
    rows_scored = int(cand.height)
    if rows_scored:
        pred = np.asarray(model.predict(feature_matrix(cand)), dtype=float)
        sig = cand.with_columns(pl.Series("score", pred)).filter(pl.col("score") >= MIN_THRESHOLD)
        del cand
    else:
        sig = _empty_part_frame()
    del frame
    rows_kept = int(sig.height)
    if not rows_kept:
        # No state cleared the lowest admission bar: a cash day, still written with the
        # full part schema so the block frames always concat cleanly.
        sig = _empty_part_frame()
        info = {
            "day_file_present": (BARS_ROOT / f"{day}.parquet").exists(),
            "rows": 0,
            "missing_symbol_streams": [],
            "label_status": {},
        }
    else:
        sig, info = attach_recomputed_labels(day, sig)
    sig = sig.select(PART_COLUMNS).sort(["ticker", "t"])
    threshold_counts = {str(t): int((sig["score"] >= t).sum() or 0) for t in THRESHOLDS}
    dest = parts_dir / f"{day}.parquet"
    temp = dest.with_suffix(".parquet.tmp")
    sig.write_parquet(temp)
    temp.replace(dest)
    del sig
    digest = resume_hash(day, producer_sha, contract_sha, model_sha)
    cov = {
        "schema": SCHEMA,
        "day": day,
        "resume_hash": digest,
        "producer_sha256": producer_sha,
        "contract_sha256": contract_sha,
        "model_sha256": model_sha,
        "rows_scored": rows_scored,
        "rows_kept": rows_kept,
        "rows_kept_by_threshold": threshold_counts,
        "day_file_present": bool(info["day_file_present"]),
        "missing_symbol_streams": list(info["missing_symbol_streams"]),
        "label_status": dict(info["label_status"]),
        "period": period(day),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    cov_dest = parts_dir / f"{day}.cov.json"
    cov_temp = cov_dest.with_suffix(".json.tmp")
    cov_temp.write_text(json.dumps(cov, indent=2, default=str) + "\n")
    cov_temp.replace(cov_dest)
    return cov


def collect_parts(
    model,
    days: list[str],
    parts_dir: Path,
    producer_sha: str,
    contract_sha: str,
    model_sha: str,
    resume: bool = False,
) -> dict:
    """Score the corpus once (one predict per day) and persist per-day atomic parts."""
    parts_dir.mkdir(parents=True, exist_ok=True)
    done = set()
    if resume:
        for day in days:
            if part_intact(day, parts_dir, resume_hash(day, producer_sha, contract_sha, model_sha)):
                done.add(day)
    todo = [d for d in days if d not in done]
    coverage = {
        "days_total": len(days),
        "days_cached": len(done),
        "days_collected": len(todo),
        "rows_scored": 0,
        "rows_kept": 0,
        "rows_kept_by_threshold": {str(t): 0 for t in THRESHOLDS},
        "missing_day_files": [],
        "missing_symbol_streams": {},
        "recomputed_label_status": {},
    }
    label_totals: Counter = Counter()
    for n, day in enumerate(todo, 1):
        cov = collect_day(day, model, parts_dir, producer_sha, contract_sha, model_sha)
        coverage["rows_scored"] += cov["rows_scored"]
        coverage["rows_kept"] += cov["rows_kept"]
        for key, count in (cov.get("rows_kept_by_threshold") or {}).items():
            coverage["rows_kept_by_threshold"][key] = (
                coverage["rows_kept_by_threshold"].get(key, 0) + count
            )
        if not cov["day_file_present"]:
            coverage["missing_day_files"].append(day)
        if cov["missing_symbol_streams"]:
            coverage["missing_symbol_streams"][day] = cov["missing_symbol_streams"]
        for status, count in cov["label_status"].items():
            label_totals[f"{status}"] += count
        if n % 25 == 0 or n == len(todo):
            print(
                f"[collect] {n}/{len(todo)} days, {coverage['rows_kept']} states "
                f"(last {day}, cached {coverage['days_cached']})",
                flush=True,
            )
    for day in days:
        if day in done:
            cov_path = parts_dir / f"{day}.cov.json"
            cov = json.loads(cov_path.read_text())
            coverage["rows_scored"] += cov["rows_scored"]
            coverage["rows_kept"] += cov["rows_kept"]
            for key, count in (cov.get("rows_kept_by_threshold") or {}).items():
                coverage["rows_kept_by_threshold"][key] = (
                    coverage["rows_kept_by_threshold"].get(key, 0) + count
                )
            if not cov["day_file_present"]:
                coverage["missing_day_files"].append(day)
            if cov["missing_symbol_streams"]:
                coverage["missing_symbol_streams"][day] = cov["missing_symbol_streams"]
            for status, count in cov["label_status"].items():
                label_totals[f"{status}"] += count
    coverage["recomputed_label_status"] = dict(label_totals)
    return coverage


def block_frame(parts_dir: Path, days: list[str]) -> pl.DataFrame:
    """One block's signals; the whole corpus is never concatenated, only one block."""
    frames = []
    for day in days:
        path = parts_dir / f"{day}.parquet"
        if path.exists():
            frames.append(pl.read_parquet(path))
    if not frames:
        return _empty_part_frame()
    out = pl.concat(frames, how="vertical")
    for f in frames:
        del f
    return out


# ----- funded-reserve replay (one replay per view per block per rung) --------
def replay_view(frame: pl.DataFrame, days: list[str], view: View, cost_bps: float) -> dict:
    """Replay ONE view on ONE block at ONE cost rung under the shared conventions.

    Faithful to ``alpha_sparse_daily.replay_view``: a fill's realized, both-legs net is
    the cash that flows back into the book, so the funding available to a later minute
    depends on the friction rung. Every rung is therefore replayed in full rather than
    derived arithmetically, and each cell carries its own honest fill counts.

    Long-only, one position per symbol, same-clock exits precede buys, no leverage: a
    symbol may not be re-entered until its ACTUAL exit minute plus the view's flat
    cooldown. An UNKNOWN exit keeps its slot, blocks its ticker for the rest of the session
    and is charged one full ticket by the lower bound; an unfilled minute reserves and
    releases its ticket fee-free and still counts as an attempt. Every calendar day of the
    block is replayed, so no-signal days stay in the denominator as cash days.
    """
    horizon = view.horizon
    gcol, xcol = f"gross_{horizon}", f"exit_et_{horizon}"
    needed = [
        "day",
        "ticker",
        "t",
        "entry_et",
        "entry_status",
        "session_end",
        "score",
        gcol,
        xcol,
    ]
    missing = [c for c in needed if c not in frame.columns]
    if missing:
        raise ValueError(f"block frame lacks {missing} for horizon {horizon}")
    sig = frame if not frame.height else frame.filter(pl.col("score") >= view.threshold)
    if sig.height:
        ordered = sig.sort(["day", "t", "score", "ticker"], descending=[False, False, True, False])
        by_day = ordered.partition_by("day", as_dict=True)
        del ordered
    else:
        by_day = {}
    reserve = RESERVE_USD
    side = cost_bps / 20_000.0  # total round-trip friction, charged on BOTH legs
    daily: list[dict] = []
    skips: Counter = Counter()
    known_nets: list[float] = []
    entry_notional = 0.0
    exit_notional = 0.0
    holds: list[int] = []
    peak_positions = 0
    position_means: list[float] = []
    peak_deployed: list[float] = []
    for day in days:
        rows = by_day.get((day,))
        cash, active, blocked, used = reserve, set(), {}, {}
        queue: list[tuple] = []
        seq = 0
        known_pnl = 0.0
        known_fills = 0
        unknown = 0
        fills = 0
        attempts = 0
        min_cash = reserve
        pos_samples: list[int] = []
        if rows is not None:
            for r in rows.iter_rows(named=True):
                t = int(r["t"])
                while queue and queue[0][0] <= t:
                    _, _, sym, proceeds, _ = heapq.heappop(queue)
                    active.discard(sym)
                    cash += proceeds
                pos_samples.append(len(active))
                ticker = r["ticker"]
                if ticker in active:
                    skips["slot_busy"] += 1
                    continue
                if t < int(blocked.get(ticker, -1)):
                    skips["cooldown"] += 1
                    continue
                if int(used.get(ticker, 0)) >= view.max_attempts:
                    skips["max_attempts"] += 1
                    continue
                if len(active) >= MAX_POSITIONS or cash + 1e-8 < ORDER_BUDGET:
                    skips["cash_or_slot"] += 1
                    continue
                used[ticker] = int(used.get(ticker, 0)) + 1
                attempts += 1
                cash -= ORDER_BUDGET
                active.add(ticker)
                min_cash = min(min_cash, cash)
                pos_samples.append(len(active))
                if r["entry_status"] == "unfilled_expired":
                    # No position was opened: the attempt minute reserves the ticket and
                    # releases it one minute later, fee-free. No exit => no cooldown.
                    heapq.heappush(queue, (t + 1, seq, ticker, ORDER_BUDGET, False))
                    seq += 1
                    continue
                gross, exit_et = r[gcol], r[xcol]
                if gross is None:
                    # Pending (unknown) exit: keeps its slot AND blocks its ticker; the
                    # lower bound charges one full ticket for it, never cash and never an
                    # expected value.
                    unknown += 1
                    net, proceeds = None, 0.0
                    release = int(r["session_end"]) + 1
                else:
                    net = (1.0 + float(gross)) * (1.0 - side) / (1.0 + side) - 1.0
                    known_nets.append(net)
                    known_fills += 1
                    proceeds = ORDER_BUDGET * (1.0 + net)
                    known_pnl += ORDER_BUDGET * net
                    release = int(exit_et)
                    if exit_et is not None and r["entry_et"] is not None:
                        holds.append(int(exit_et) - int(r["entry_et"]))
                blocked[ticker] = release + view.cooldown_min
                heapq.heappush(queue, (release, seq, ticker, proceeds, True))
                seq += 1
                fills += 1
                entry_notional += ORDER_BUDGET
                exit_notional += proceeds
        peak_positions = max([peak_positions, max(pos_samples) if pos_samples else 0])
        position_means.append(float(np.mean(pos_samples)) if pos_samples else 0.0)
        peak_deployed.append(reserve - min_cash)
        lower_pnl = known_pnl - ORDER_BUDGET * unknown
        daily.append(
            {
                "day": day,
                "known_pnl": known_pnl,
                "lower_pnl": lower_pnl,
                "known_return": known_pnl / reserve,
                "lower_return": lower_pnl / reserve,
                "unknown": unknown,
                "fills": fills,
                "known_fills": known_fills,
                "attempts": attempts,
            }
        )
    return {
        "view": view,
        "cost_bps": cost_bps,
        "days": len(days),
        "days_list": list(days),
        "attempts": sum(d["attempts"] for d in daily),
        "fills": sum(d["fills"] for d in daily),
        "unknown_fills": sum(d["unknown"] for d in daily),
        "known_fills": sum(d["known_fills"] for d in daily),
        "traded_days": sum(1 for d in daily if d["fills"] > 0),
        "known_nets": known_nets,
        "entry_notional_usd": entry_notional,
        "exit_notional_usd": exit_notional,
        "skip_reasons": {k: int(v) for k, v in sorted(skips.items())},
        "skips_total": int(sum(skips.values())),
        "hold_min": {
            "mean": float(np.mean(holds)) if holds else None,
            "median": float(np.median(holds)) if holds else None,
            "min": int(min(holds)) if holds else None,
            "max": int(max(holds)) if holds else None,
        },
        "positions_mean": float(np.mean(position_means)) if position_means else 0.0,
        "positions_peak": int(peak_positions),
        "peak_deployed_usd": float(np.mean(peak_deployed)) if peak_deployed else 0.0,
        "daily": daily,
    }


# ----- per-rung economics: every figure straight from that rung's replay ------
def cell_from_replay(replay: dict) -> dict:
    """The reported cell of one (view, block, cost rung), taken from its own replay.

    Two bases are reported and never blended:
    * ``known_contribution`` -- the dollars of the fills whose exit actually printed.
      UNKNOWN fills are EXCLUDED, so this figure is partial by construction.
    * ``full_loss_lower_bound`` -- the same dollars minus one whole ticket per UNKNOWN
      fill. A labelled bound, never an expected value and never a cash zero.
    """
    budget, reserve = ORDER_BUDGET, RESERVE_USD
    daily = replay["daily"]
    n_days = len(daily)
    nets = np.array(replay["known_nets"], dtype=float)
    known_returns = np.array([d["known_return"] for d in daily], dtype=float)
    lower_returns = np.array([d["lower_return"] for d in daily], dtype=float)
    wins = float(nets[nets > 0].sum()) if nets.size else 0.0
    losses = float(-nets[nets < 0].sum()) if nets.size else 0.0
    # Monthly / yearly accounting aggregates the SAME unrounded daily dollars.
    monthly: dict[str, dict] = {}
    yearly: dict[str, dict] = {}
    for rec in daily:
        for key, table in ((rec["day"][:7], monthly), (rec["day"][:4], yearly)):
            slot = table.setdefault(
                key,
                {
                    "days_replayed": 0,
                    "traded_days": 0,
                    "fills": 0,
                    "known_fills": 0,
                    "unknown_fills": 0,
                    "known_pnl": 0.0,
                    "lower_pnl": 0.0,
                    "known_return_sum": 0.0,
                    "lower_return_sum": 0.0,
                },
            )
            slot["days_replayed"] += 1
            slot["traded_days"] += 1 if rec["fills"] > 0 else 0
            slot["fills"] += rec["fills"]
            slot["known_fills"] += rec["known_fills"]
            slot["unknown_fills"] += rec["unknown"]
            slot["known_pnl"] += rec["known_pnl"]
            slot["lower_pnl"] += rec["lower_pnl"]
            slot["known_return_sum"] += rec["known_return"]
            slot["lower_return_sum"] += rec["lower_return"]
    monthly_out = {
        k: {
            "days_replayed": v["days_replayed"],
            "traded_days": v["traded_days"],
            "fills": v["fills"],
            "known_fills": v["known_fills"],
            "unknown_fills": v["unknown_fills"],
            "known_contribution_usd": round(v["known_pnl"], 6),
            "full_loss_lower_bound_usd": round(v["lower_pnl"], 6),
            "mean_known_contribution_return": v["known_return_sum"] / v["days_replayed"],
            "mean_full_loss_lower_bound_return": v["lower_return_sum"] / v["days_replayed"],
        }
        for k, v in sorted(monthly.items())
    }
    yearly_out = {
        k: {
            "days_replayed": v["days_replayed"],
            "traded_days": v["traded_days"],
            "fills": v["fills"],
            "known_fills": v["known_fills"],
            "unknown_fills": v["unknown_fills"],
            "known_contribution_usd": round(v["known_pnl"], 6),
            "full_loss_lower_bound_usd": round(v["lower_pnl"], 6),
        }
        for k, v in sorted(yearly.items())
    }
    known_dollars_per_day = float(known_returns.mean()) * reserve if n_days else None
    lower_dollars_per_day = float(lower_returns.mean()) * reserve if n_days else None
    turnover = replay["entry_notional_usd"] + replay["exit_notional_usd"]
    return {
        "cost_bps": replay["cost_bps"],
        "days": n_days,
        "attempts": replay["attempts"],
        "fills": replay["fills"],
        "known_fills": replay["known_fills"],
        "unknown_fills": replay["unknown_fills"],
        "traded_days": replay["traded_days"],
        "known_fill_stats": {
            "n": int(nets.size),
            "mean_net_per_known_fill": float(nets.mean()) if nets.size else None,
            "known_win_rate": float((nets > 0).mean()) if nets.size else None,
            "known_profit_factor": (wins / losses) if losses else None,
            "worst_known_fill_net": float(nets.min()) if nets.size else None,
        },
        "known_contribution": {
            "basis": "known fills only; UNKNOWN fills excluded (partial by construction)",
            "total_usd": round(float(known_returns.sum()) * reserve, 6) if n_days else None,
            "dollars_per_day": known_dollars_per_day,
            "dollars_per_year_252": known_dollars_per_day * TRADING_DAYS_PER_YEAR
            if known_dollars_per_day is not None
            else None,
            "mean_daily_return": float(known_returns.mean()) if n_days else None,
            "daily_se": (
                float(known_returns.std(ddof=1) / np.sqrt(n_days)) if n_days > 1 else None
            ),
            "positive_months": int(
                sum(1 for v in monthly_out.values() if v["mean_known_contribution_return"] > 0)
            ),
            "months": len(monthly_out),
            "bootstrap_daily": bootstrap_daily([float(x) for x in known_returns]),
        },
        "full_loss_lower_bound": {
            "basis": (
                "known contribution minus one full ticket per UNKNOWN fill; a labelled "
                "lower bound, never an expected value and never a cash zero"
            ),
            "total_usd": round(float(lower_returns.sum()) * reserve, 6) if n_days else None,
            "dollars_per_day": lower_dollars_per_day,
            "dollars_per_year_252": lower_dollars_per_day * TRADING_DAYS_PER_YEAR
            if lower_dollars_per_day is not None
            else None,
            "mean_daily_return": float(lower_returns.mean()) if n_days else None,
            "daily_se": float(lower_returns.std(ddof=1) / np.sqrt(n_days)) if n_days > 1 else None,
            "unknown_charge_usd": round(budget * replay["unknown_fills"], 6),
            "positive_months": int(
                sum(1 for v in monthly_out.values() if v["mean_full_loss_lower_bound_return"] > 0)
            ),
            "months": len(monthly_out),
            "bootstrap_daily": bootstrap_daily([float(x) for x in lower_returns]),
        },
        "monthly": monthly_out,
        "yearly": yearly_out,
        "turnover_usd": turnover,
        "turnover_usd_per_day": turnover / n_days if n_days else None,
        "turnover_per_day_of_capital": (turnover / n_days / RESERVE_USD) if n_days else None,
        "carry_equity_end_usd": round(RESERVE_USD + float(lower_returns.sum()) * reserve, 6)
        if n_days
        else None,
        "carry_equity_is_compounding": False,
        "execution_proxy_only": True,
    }


def block_entry(
    frame: pl.DataFrame,
    days: list[str],
    view: View,
    n_signals: int,
    costs: tuple[float, ...],
) -> dict:
    """Stored cell for one (view, block): a replay at every rung of the ladder.

    The rung is not a label on one replay: a fill's both-legs net is the cash that flows
    back into the book, so each rung funds (and therefore fills) on its own terms. The
    headline counts shown beside the ladder are the pre-declared selection rung's
    (25 bps); every rung's cell carries its own counts as well.
    """
    replays: dict[str, dict] = {}
    cells: dict[str, dict] = {}
    for cost in costs:
        replay = replay_view(frame, days, view, cost)
        replays[str(int(cost))] = replay
        cells[str(int(cost))] = cell_from_replay(replay)
    head = replays[str(int(SELECT_COST))]
    return {
        "view": {
            "name": view.name,
            "cadence": view.cadence_str,
            "threshold": view.threshold,
            "horizon": view.horizon,
            "cooldown_min": view.cooldown_min,
            "max_attempts": view.max_attempts,
            "cadence_kind": view.cadence,
            "reenters": view.reenters,
            "control": view.control,
        },
        "n_signals": int(n_signals),
        "days": head["days"],
        "attempts": head["attempts"],
        "fills": head["fills"],
        "known_fills": head["known_fills"],
        "unknown_fills": head["unknown_fills"],
        "traded_days": head["traded_days"],
        "skips_total": head["skips_total"],
        "skip_reasons": head["skip_reasons"],
        "hold_min": head["hold_min"],
        "positions_mean": head["positions_mean"],
        "positions_peak": head["positions_peak"],
        "peak_deployed_usd": head["peak_deployed_usd"],
        "entry_notional_usd": head["entry_notional_usd"],
        "headline_rung_bps": SELECT_COST,
        "cost_cells": cells,
    }


# ----- the one pre-declared selection (2023 validation only) -----------------
def selection_key(entry: dict) -> tuple:
    """Deterministic ordering: validation known-contribution $/calendar day, then
    known fills, then fewest attempts, then the view key."""
    cell = entry["cost_cells"][str(int(SELECT_COST))]
    return (
        -float(cell["known_contribution"]["dollars_per_day"]),
        -int(entry["known_fills"]),
        int(entry["attempts"]),
        entry["view"]["name"],
    )


def ranked_entries(entries: dict[str, dict]) -> list[dict]:
    return sorted(entries.values(), key=selection_key)


def selection_row(entry: dict, rank: int) -> dict:
    cell = entry["cost_cells"][str(int(SELECT_COST))]
    return {
        "rank": rank,
        "view": entry["view"]["name"],
        "threshold": entry["view"]["threshold"],
        "horizon": entry["view"]["horizon"],
        "cadence": entry["view"]["cadence_kind"],
        "max_attempts": entry["view"]["max_attempts"],
        "known_contribution_dollars_per_calendar_day": cell["known_contribution"][
            "dollars_per_day"
        ],
        "known_contribution_dollars_per_year_252": cell["known_contribution"][
            "dollars_per_year_252"
        ],
        "full_loss_lower_bound_dollars_per_calendar_day": cell["full_loss_lower_bound"][
            "dollars_per_day"
        ],
        "known_fills": entry["known_fills"],
        "unknown_fills": entry["unknown_fills"],
        "traded_days": entry["traded_days"],
        "days": entry["days"],
        "attempts": entry["attempts"],
        "n_signals": entry["n_signals"],
    }


def selection_document(
    ranked: list[dict], model_sha: str, producer_sha: str, contract_sha: str
) -> dict:
    pick = ranked[0]
    return {
        "objective": (
            "highest 2023 validation KNOWN-CONTRIBUTION dollars per FULL CALENDAR day "
            "(known fills only, UNKNOWN fills excluded from the objective) on the $3,000 "
            "research reserve"
        ),
        "selection_block": "validation 2023 only (250 sessions), never the late block",
        "selection_cost_bps": SELECT_COST,
        "rung_nature": (
            "the pre-declared rung is a TOTAL modeled minute-proxy friction scenario on "
            "the proxy prices, NOT an actual broker fee (a primary US broker's regular "
            "schedule is typically well under 1 bp on a $1,000 ticket); it is a "
            "comparison object, not a hurdle, and no rung closes a candidate"
        ),
        "tie_breaks": [
            "higher known-contribution dollars per full calendar day at the 25 bps rung",
            "more known fills",
            "fewer attempts",
            "lexicographic view key",
        ],
        "gates_applied": {
            "cost_floor": None,
            "count_floor": None,
            "median_gate": None,
            "ci_gate": None,
            "tail_gate": None,
            "note": "no gate of any kind is applied; every cell of the grid is reported",
        },
        "grid_size": len(ranked),
        "ranked": [selection_row(entry, i + 1) for i, entry in enumerate(ranked)],
        "chosen": pick["view"]["name"],
        "chosen_detail": pick["view"],
        "chosen_known_contribution_dollars_per_calendar_day": pick["cost_cells"][
            str(int(SELECT_COST))
        ]["known_contribution"]["dollars_per_day"],
        "chosen_full_loss_lower_bound_dollars_per_calendar_day": pick["cost_cells"][
            str(int(SELECT_COST))
        ]["full_loss_lower_bound"]["dollars_per_day"],
        "chosen_known_fills": pick["known_fills"],
        "chosen_traded_days": pick["traded_days"],
        "frozen_before_late_inspection": True,
        "status": STATUS,
        "late_block_disclosure": (
            "2025-02-01..2026-05-31 was already explored before this producer, so any "
            "late outcome is DISCOVERY-NOT-VALIDATED - not pristine and not previously "
            "unknown; the late traversal below is a transparency read with the choice "
            "immutable, not a holdout"
        ),
        "provenance": {
            "producer_sha256": producer_sha,
            "contract_sha256": contract_sha,
            "model_sha256": model_sha,
        },
    }


def reproduction_check(
    val_entries: dict,
    val_days: list[str],
    late_entries: dict | None = None,
    conf_days: list[str] | None = None,
) -> dict:
    """Does the grid reproduce the frozen stored-lane reference cell, field for field?

    The 0.030 / h60 / repeat view of this grid is the stored lane's chosen view; its
    labels (panel h60) and its replay conventions are identical, so on a full block it
    must reproduce the stored figures exactly. A mismatch would mean the grid drifted from
    the frozen engine. A restricted corpus (``--days``) cannot reproduce a full-block
    figure, so the check is reported as skipped instead of silently failing.
    """
    out: dict[str, dict] = {}
    checks: list[bool] = []
    for tag, entries, expected, n_days in (
        ("validation_2023", val_entries, REFERENCE_LANE["validation_2023"], len(val_days)),
        (
            "late_2025_02_2026_05",
            late_entries,
            REFERENCE_LANE["late_2025_02_2026_05"],
            len(conf_days or []),
        ),
    ):
        if entries is None:
            out[tag] = {"skipped": True, "reason": "late block not run (--skip-late)"}
            continue
        if n_days != expected["days"]:
            out[tag] = {
                "skipped": True,
                "reason": (
                    f"corpus restricted to {n_days} of the block's {expected['days']} days; "
                    "a full-block reproduction needs the full block"
                ),
            }
            continue
        cell = entries[REFERENCE_VIEW]["cost_cells"][str(int(REFERENCE_COST))]
        block = {
            "known_fills": {
                "stored": expected["known_fills"],
                "reproduced": entries[REFERENCE_VIEW]["known_fills"],
                "match": expected["known_fills"] == entries[REFERENCE_VIEW]["known_fills"],
            },
            "traded_days": {
                "stored": expected["traded_days"],
                "reproduced": entries[REFERENCE_VIEW]["traded_days"],
                "match": expected["traded_days"] == entries[REFERENCE_VIEW]["traded_days"],
            },
            "days": {
                "stored": expected["days"],
                "reproduced": entries[REFERENCE_VIEW]["days"],
                "match": expected["days"] == entries[REFERENCE_VIEW]["days"],
            },
            "full_loss_lower_bound_dollars_per_day_at_100bps": {
                "stored": expected["dollars_per_day_100bps"],
                "reproduced": cell["full_loss_lower_bound"]["dollars_per_day"],
                "match": abs(
                    float(expected["dollars_per_day_100bps"])
                    - float(cell["full_loss_lower_bound"]["dollars_per_day"])
                )
                <= FLOAT_TOL,
            },
        }
        if "turnover_usd_100bps" in expected:
            block["turnover_usd_at_100bps"] = {
                "stored": expected["turnover_usd_100bps"],
                "reproduced": cell["turnover_usd"],
                "match": abs(float(expected["turnover_usd_100bps"]) - float(cell["turnover_usd"]))
                <= FLOAT_TOL,
            }
        block["all_match"] = all(v.get("match") for k, v in block.items() if k != "all_match")
        checks.append(bool(block["all_match"]))
        out[tag] = block
    out["all_match"] = (bool(checks) and all(checks)) if checks else None
    out["reference_view_in_this_grid"] = REFERENCE_VIEW
    out["reference_cost_bps"] = REFERENCE_COST
    out["note"] = (
        "the stored lane's dollars_per_day is the FULL-LOSS LOWER-BOUND basis; the "
        "reference cell carries no UNKNOWN fill on either block, so the known-contribution "
        "basis and the lower-bound basis coincide for it"
    )
    return out


def _delta(left, right):
    """Plain difference, or None when either side has no figure (an empty block)."""
    if left is None or right is None:
        return None
    return left - right


def incremental_vs_reference(
    chosen_entry: dict, reference_entry: dict, costs: tuple[float, ...]
) -> dict:
    """Plain arithmetic on the same basis: chosen view minus the frozen reference view."""
    rows: dict[str, dict] = {}
    for cost in costs:
        key = str(int(cost))
        c = chosen_entry["cost_cells"][key]
        r = reference_entry["cost_cells"][key]
        ck = c["known_contribution"]
        rk = r["known_contribution"]
        cl = c["full_loss_lower_bound"]
        rl = r["full_loss_lower_bound"]
        rows[key] = {
            "cost_bps": cost,
            "chosen_known_contribution_dollars_per_day": ck["dollars_per_day"],
            "reference_known_contribution_dollars_per_day": rk["dollars_per_day"],
            "delta_known_contribution_dollars_per_day": _delta(
                ck["dollars_per_day"], rk["dollars_per_day"]
            ),
            "delta_known_contribution_dollars_per_year_252": _delta(
                ck["dollars_per_year_252"], rk["dollars_per_year_252"]
            ),
            "chosen_full_loss_lower_bound_dollars_per_day": cl["dollars_per_day"],
            "reference_full_loss_lower_bound_dollars_per_day": rl["dollars_per_day"],
            "delta_full_loss_lower_bound_dollars_per_day": _delta(
                cl["dollars_per_day"], rl["dollars_per_day"]
            ),
            "delta_full_loss_lower_bound_dollars_per_year_252": _delta(
                cl["dollars_per_year_252"], rl["dollars_per_year_252"]
            ),
            "chosen_known_fills": c["known_fills"],
            "reference_known_fills": r["known_fills"],
            "delta_known_fills": c["known_fills"] - r["known_fills"],
            "chosen_unknown_fills": c["unknown_fills"],
            "reference_unknown_fills": r["unknown_fills"],
            "delta_unknown_fills": c["unknown_fills"] - r["unknown_fills"],
            "chosen_traded_days": c["traded_days"],
            "reference_traded_days": r["traded_days"],
            "delta_traded_days": c["traded_days"] - r["traded_days"],
            "chosen_attempts": c["attempts"],
            "reference_attempts": r["attempts"],
            "delta_attempts": c["attempts"] - r["attempts"],
        }
    pick = rows[str(int(REFERENCE_COST))]
    return {
        "basis": (
            "plain arithmetic on the same basis: the frozen 0.030/h60/repeat reference "
            "view replayed by THIS producer on the same block, reserve, ticket, "
            "conventions and cost rung; nothing is re-weighted, re-sampled or re-scaled"
        ),
        "reference_view": REFERENCE_VIEW,
        "reference_is_grid_control": True,
        "by_cost": rows,
        "at_reference_100bps": pick,
        "known_contribution_better_than_reference_at_selection_rung": (
            None
            if rows[str(int(SELECT_COST))]["delta_known_contribution_dollars_per_day"] is None
            else bool(rows[str(int(SELECT_COST))]["delta_known_contribution_dollars_per_day"] > 0)
        ),
        "full_loss_lower_bound_better_than_reference_at_selection_rung": (
            None
            if rows[str(int(SELECT_COST))]["delta_full_loss_lower_bound_dollars_per_day"] is None
            else bool(
                rows[str(int(SELECT_COST))]["delta_full_loss_lower_bound_dollars_per_day"] > 0
            )
        ),
    }


# ----- the predeclared contract (written before any validation score) --------
def contract_document(
    model_path: Path,
    model_sha: str,
    producer_sha: str,
    blocks: dict[str, list[str]],
    panel_contract_sha: str | None,
) -> dict:
    """The full predeclared grid, ladder and selection rule. Deterministic on purpose:
    no wall-clock field, so a --resume run rewrites the byte-identical document and the
    per-day parts stay valid."""
    return {
        "study": "alpha_retained_exit_grid",
        "label": (
            "threshold x exit-horizon x cadence grid of the retained sparse h60 learned "
            "selector (50 fixed views, one immutable stored model, no refit)"
        ),
        "status": STATUS,
        "declared_before_any_validation_outcome": True,
        "grid": {
            "n_views": len(VIEWS),
            "axes": {
                "thresholds": [float(t) for t in THRESHOLDS],
                "exit_horizons_minutes": [int(h) for h in HORIZONS],
                "cadences": {
                    CADENCE_ONCE: {"max_attempts": ATTEMPTS_ONCE, "reentry": False},
                    CADENCE_REPEAT: {
                        "max_attempts": ATTEMPTS_REPEAT,
                        "reentry": True,
                        "cooldown_min": COOLDOWN_MIN,
                        "cooldown_anchor": "the ACTUAL exit minute of the position",
                    },
                },
            },
            "views": [
                {
                    "name": v.name,
                    "cadence": v.cadence_str,
                    "threshold": v.threshold,
                    "horizon": v.horizon,
                    "cooldown_min": v.cooldown_min,
                    "max_attempts": v.max_attempts,
                    "cadence_kind": v.cadence,
                    "reenters": v.reenters,
                    "control": v.control,
                }
                for v in VIEWS
            ],
            "fixed_in_code": True,
            "no_hpo": True,
            "no_refit": True,
            "every_view_reads_the_same_score": True,
        },
        "selection": {
            "objective": (
                "highest 2023 validation KNOWN-CONTRIBUTION dollars per FULL CALENDAR day "
                "(known fills only; UNKNOWN fills excluded from the objective) on the "
                "$3,000 research reserve"
            ),
            "selection_block": "validation 2023 only (250 sessions)",
            "selection_cost_bps": SELECT_COST,
            "rung_nature": (
                "a TOTAL modeled minute-proxy friction scenario on the proxy prices, NOT "
                "an actual broker fee; a comparison object, not a hurdle"
            ),
            "tie_breaks": [
                "higher known-contribution dollars per full calendar day at the 25 bps rung",
                "more known fills",
                "fewest attempts",
                "lexicographic view key",
            ],
            "gates_applied": {
                "cost_floor": None,
                "count_floor": None,
                "median_gate": None,
                "ci_gate": None,
                "tail_gate": None,
            },
            "frozen_before_late_inspection": True,
            "output": "selection.json (frozen before any late-block outcome is computed)",
        },
        "cost_ladder": {
            "cost_scenarios_bps": [float(c) for c in COST_SCENARIOS_BPS],
            "historical_diagnostic_cost_bps": [HISTORICAL_COST_BPS],
            "validation_bps": [float(c) for c in VAL_COSTS],
            "late_bps": [float(c) for c in LATE_COSTS],
            "selection_rung_bps": SELECT_COST,
            "reference_rung_bps": REFERENCE_COST,
            "is_comparison_not_hurdle": True,
            "stress_vetoes": None,
            "note": (
                "every rung is a TOTAL round-trip modeled friction scenario charged on "
                "both legs of the minute-open proxy prices; 25..150 bps describe a "
                "LOW-FEE OPPORTUNITY set (a primary US broker's regular schedule - zero "
                "commission, SEC 20.6 per $1M, CAT 0.000003 per side, daily cent rounding "
                "- is typically well under 1 bp on a $250-$1,000 ticket), and the actual "
                "provider fee is sourced separately in the provider-fee ledger"
            ),
        },
        "replay": {
            "engine": (
                "alpha_retained_exit_grid.replay_view + cell_from_replay "
                "(funded-reserve conventions of alpha_sparse_daily.replay_view)"
            ),
            "conventions_source": "factory/scripts/alpha_sparse_daily.py (frozen engine)",
            "reserve_usd": RESERVE_USD,
            "max_positions": MAX_POSITIONS,
            "order_budget": ORDER_BUDGET,
            "leverage": False,
            "fees_both_legs": True,
            "one_position_per_symbol": True,
            "same_clock_exits_before_buys": True,
            "simultaneous_clock_funded_before_fill_outcome": True,
            "highest_score_eligible_intents_funded_first": True,
            "cooldown_rule": (
                "no reopening until the ACTUAL exit minute + cooldown_min; an unfilled "
                "attempt minute has no exit and therefore no cooldown"
            ),
            "unknown_exit_rule": (
                "keeps its slot and blocks its ticker for the rest of the session; the "
                "lower bound charges one full ticket, never cash and never an expected "
                "value"
            ),
            "unfilled_rule": (
                "reserve and release the ticket one minute later, fee-free, and still "
                "count the attempt"
            ),
            "all_calendar_days_retained": True,
            "cost_rung_rule": (
                "a fill's both-legs net is the cash that flows back into the book, so "
                "the funding available to a later minute depends on the rung; every "
                "rung is therefore replayed in full (fees on both legs, "
                "net = (1+gross)*(1-side)/(1+side)-1, side = cost_bps/20_000, exactly "
                "as alpha_sparse_daily.replay_view charges them) and each cell carries "
                "its own honest fill counts"
            ),
            "daily_reset_is_not_self_financing_cagr": True,
            "carry_equity_check": "additive fixed-ticket equity, no compounding",
        },
        "labels": {
            "panel_horizons": [int(h) for h in PANEL_LABEL_HORIZONS],
            "recomputed_horizons": [int(h) for h in RECOMPUTE_HORIZONS],
            "recompute_rule": (
                "strict actual minute opens: first actual tape minute open at/after "
                "min(entry+h, session_last_minute); no interpolation, no close-price "
                "proxy, no future-fill filtering; a state whose exit minute never prints "
                "stays UNKNOWN"
            ),
            "unfilled_rule": "no bar at the entry minute => cash retained for that minute",
        },
        "reported_bases": {
            "known_contribution": (
                "dollars of the fills whose exit actually printed; UNKNOWN fills are "
                "excluded, so the figure is partial by construction"
            ),
            "full_loss_lower_bound": (
                "known contribution minus one whole ticket per UNKNOWN fill; a labelled "
                "lower bound over unknowns, never blended with the known contribution"
            ),
            "never_blended": True,
        },
        "annualization": {
            "trading_days_per_year": TRADING_DAYS_PER_YEAR,
            "convention": (
                "simple dollars/day x 252 sessions on a fixed $3,000 reserve where every "
                "session restarts from the same reserve - a DAILY-RESET CONVENTION, "
                "explicitly NOT a CAGR and not a self-financing return"
            ),
        },
        "capacity_note": (
            "book-sized tickets on a research reserve; displayed top-of-book depth and "
            "$1,000 tickets are NOT an executable capacity claim and scaling is not "
            "assumed linear"
        ),
        "execution_proxy_only": True,
        "reference_view": {
            "grid_view_name": REFERENCE_VIEW,
            "stored_lane": REFERENCE_LANE,
        },
        "model": {
            "refit": False,
            "hpo": False,
            "feature_changes": False,
            "ticker_features_added": False,
            "horizons_scored": [MODEL_HORIZON],
            "scored_once_over_analysis_corpus": True,
            "fit_block_2021_2022_scored": False,
            "fit_block_note": (
                "the 2021-2022 fit block is never replayed (the weights are frozen), so "
                "it is not scored; the single prediction pass is shared by all 50 views"
            ),
            "stored_path": str(model_path),
            "stored_sha256": model_sha,
            "expected_sha256": EXPECTED_MODEL_SHA256,
            "feature_order": list(FEATURES_ALL),
            "feature_order_verified_at_load": True,
        },
        "provenance": {
            "panel_root": str(PANEL_ROOT),
            "panel_contract_sha256": panel_contract_sha,
            "producer_sha256": producer_sha,
            "tape_root": str(BARS_ROOT),
            "analysis_days": sum(len(v) for v in blocks.values()),
            "validation_days": len(blocks.get(VAL_PERIOD, [])),
            "late_days": len(blocks.get(CONF_PERIOD, [])),
            "protected_outcomes_never_read": (
                "allowed(day) guards every day before any file read; 2024, 2025-01 and "
                "2026-06..2026-08 are outside the allowed window and are never opened"
            ),
        },
        "resume": {
            "schema": SCHEMA,
            "per_day_parts": "collect_parts/<day>.parquet + collect_parts/<day>.cov.json",
            "atomicity": (
                "temporary file then rename, so an interrupted day never lands half-written"
            ),
            "resume_hash": (
                "sha256 over 'schema|producer_sha256|contract_sha256|model_sha256|day'; a "
                "day part is intact only when its stored hash and its columns still match"
            ),
            "block_concatenation": "one block at a time; the corpus is never concatenated",
        },
        "novelty": {
            "vs_frozen_lane": {
                "prior": "factory/scripts/alpha_sparse_daily.py",
                "prior_axes": (
                    "one threshold (0.030) x three labelled horizons (15/30/60) x two cadences"
                ),
                "this_study": (
                    "five thresholds x five horizons (h30/h120 recomputed from the actual "
                    "tape) x both cadences = 50 views, with the exit-horizon axis opened "
                    "past the stored labels to 120 and 390 minutes"
                ),
                "unchanged": (
                    "the stored model, the funded-reserve account and the label conventions"
                ),
            },
            "vs_preowned_reentry_roster": {
                "prior": "factory/scripts/sequencing.py S3_reentry",
                "prior_mechanism": (
                    "fixed 60-minute hold + 15-minute cooldown episode, 3 units per "
                    "episode, pre-owned live-admission ML substrate, t1 close-to-close "
                    "fills, 2% round-trip cost, no slot/cash accounting, UNKNOWN dropped"
                ),
                "prior_verdict": "re-entry-after-cooldown WORST",
                "this_study": (
                    "re-entry on the stored h60 payoff model's own score with the "
                    "cooldown anchored to the ACTUAL exit minute, on a funded reserve "
                    "with per-symbol slots and both cost bases reported"
                ),
            },
        },
    }


# ----- block discovery --------------------------------------------------------
def analysis_blocks(days: list[str] | None) -> dict[str, list[str]]:
    files = sorted(
        p.stem for p in (PANEL_ROOT / "days").glob("????-??-??.parquet") if allowed(p.stem)
    )
    keep = set(days) if days else None
    blocks: dict[str, list[str]] = {}
    for day in files:
        if keep is not None and day not in keep:
            continue
        if period(day) in ANALYSIS_PERIODS:
            blocks.setdefault(period(day), []).append(day)
    if keep is None:
        for p, expected in EXPECTED_DAYS.items():
            if len(blocks.get(p, [])) != expected:
                raise SystemExit(
                    f"[retained-exit-grid] requires {expected} {p} days, "
                    f"got {len(blocks.get(p, []))}"
                )
    if not blocks:
        raise SystemExit("[retained-exit-grid] no analysis days selected")
    return blocks


# ----- console reporting ------------------------------------------------------
def _fmt(value, spec="+.6f"):
    return "n/a" if value is None else format(value, spec)


def print_entry(tag: str, entry: dict) -> None:
    cell = entry["cost_cells"][str(int(SELECT_COST))]
    print(
        f"[{tag}] {entry['view']['name']:<20} thr={entry['view']['threshold']:.3f} "
        f"h={entry['view']['horizon']} {entry['view']['cadence_kind']:<6} "
        f"known$/day={_fmt(cell['known_contribution']['dollars_per_day'], '+.4f')} "
        f"lower$/day={_fmt(cell['full_loss_lower_bound']['dollars_per_day'], '+.4f')} "
        f"n={entry['known_fills']} unk={entry['unknown_fills']} "
        f"traded={entry['traded_days']}/{entry['days']} att={entry['attempts']} "
        f"signals={entry['n_signals']}",
        flush=True,
    )


def summary_row(entry: dict) -> dict:
    row = {
        "view": entry["view"]["name"],
        "threshold": entry["view"]["threshold"],
        "horizon": entry["view"]["horizon"],
        "cadence": entry["view"]["cadence_kind"],
        "max_attempts": entry["view"]["max_attempts"],
        "n_signals": entry["n_signals"],
        "attempts": entry["attempts"],
        "fills": entry["fills"],
        "known_fills": entry["known_fills"],
        "unknown_fills": entry["unknown_fills"],
        "traded_days": entry["traded_days"],
        "days": entry["days"],
    }
    for cost in COST_SCENARIOS_BPS + (HISTORICAL_COST_BPS,):
        cell = entry["cost_cells"][str(int(cost))]
        row[f"known_contribution_dollars_per_day_{int(cost)}bps"] = cell["known_contribution"][
            "dollars_per_day"
        ]
        row[f"full_loss_lower_bound_dollars_per_day_{int(cost)}bps"] = cell[
            "full_loss_lower_bound"
        ]["dollars_per_day"]
        row[f"known_contribution_dollars_per_year_252_{int(cost)}bps"] = cell["known_contribution"][
            "dollars_per_year_252"
        ]
        row[f"mean_net_per_known_fill_{int(cost)}bps"] = cell["known_fill_stats"][
            "mean_net_per_known_fill"
        ]
    return row


def decision_text(
    chosen: str,
    val_entry: dict,
    ranked: list[dict],
    late_entry: dict | None,
    reproduction: dict,
) -> str:
    cell = val_entry["cost_cells"][str(int(SELECT_COST))]
    skipped = any(
        isinstance(v, dict) and v.get("skipped")
        for k, v in reproduction.items()
        if k not in ("all_match", "reference_view_in_this_grid", "reference_cost_bps", "note")
    )
    if reproduction["all_match"]:
        repro_txt = "reproduces the stored lane reference cell exactly"
    elif skipped:
        repro_txt = "was not comparable to the stored lane (restricted corpus or skipped block)"
    else:
        repro_txt = "DOES NOT reproduce the stored lane reference cell"
    if late_entry is None:
        late_txt = "; late block not run (--skip-late)"
    else:
        late_cell = late_entry["cost_cells"][str(int(SELECT_COST))]
        late_txt = (
            f"; late known-contribution "
            f"{_fmt(late_cell['known_contribution']['dollars_per_day'], '+.4f')} $/day "
            f"and lower bound "
            f"{_fmt(late_cell['full_loss_lower_bound']['dollars_per_day'], '+.4f')} $/day "
            f"@{int(SELECT_COST)} on the previously explored 2025-02..2026-05 block "
            f"({late_entry['known_fills']} known fills on {late_entry['traded_days']} "
            f"traded days of {late_entry['days']})"
        )
    return (
        f"{STATUS}: chosen {chosen} of {len(ranked)} grid views by 2023 validation "
        f"known-contribution dollars per full calendar day at {int(SELECT_COST)}bps "
        f"({_fmt(cell['known_contribution']['dollars_per_day'], '+.4f')} $/day, "
        f"{_fmt(cell['known_contribution']['dollars_per_year_252'], '+.2f')} $/yr by the "
        f"simple 252-session daily-reset convention) with {val_entry['known_fills']} known "
        f"fills on {val_entry['traded_days']} traded days of {val_entry['days']}; its "
        f"full-loss lower bound is "
        f"{_fmt(cell['full_loss_lower_bound']['dollars_per_day'], '+.4f')} $/day over "
        f"{val_entry['unknown_fills']} UNKNOWN fills; the frozen {REFERENCE_VIEW} reference "
        f"cell {repro_txt}{late_txt}"
    )


def replay_block(frame: pl.DataFrame, days: list[str], costs: tuple[float, ...], tag: str) -> dict:
    """All 50 fixed views on one block: one replay at every rung of the ladder each."""
    entries: dict[str, dict] = {}
    for view in VIEWS:
        n_signals = int(frame.filter(pl.col("score") >= view.threshold).height)
        entries[view.name] = block_entry(frame, days, view, n_signals, costs)
        print_entry(tag, entries[view.name])
    return entries


# ----- main pipeline ----------------------------------------------------------
def run(args: argparse.Namespace) -> None:
    t0 = time.time()
    out = args.out or OUTPUT
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")

    # 1) ONE stored model, loaded, digest-verified and scored (never fitted).
    bundle, model_path = load_stored_model(PANEL_ROOT / "learned" / "models")
    model_sha = sha256_file(model_path)
    producer_sha = sha256_file(Path(__file__))
    print(
        f"[model] h{MODEL_HORIZON} {model_path} (sha256 {model_sha[:12]}), "
        f"seed={bundle.get('seed')} clip={bundle.get('clip_fit')}",
        flush=True,
    )
    blocks = analysis_blocks(args.days)
    val_days = blocks.get(VAL_PERIOD, [])
    conf_days = blocks.get(CONF_PERIOD, [])
    if not val_days:
        raise SystemExit(
            "[retained-exit-grid] no 2023 validation days selected; the "
            "known-contribution selection objective requires the validation block"
        )
    print(
        f"[blocks] validation={len(val_days)} days, late={len(conf_days)} days, views={len(VIEWS)}",
        flush=True,
    )

    # 2) the predeclared contract (grid + ladder + selection rule) BEFORE any score.
    panel_contract = PANEL_ROOT / "contract.json"
    contract = contract_document(
        model_path,
        model_sha,
        producer_sha,
        blocks,
        sha256_file(panel_contract) if panel_contract.exists() else None,
    )
    (out / "contract.json").write_text(json.dumps(contract, indent=2, default=str) + "\n")
    contract_sha = sha256_file(out / "contract.json")
    print(
        f"[freeze] contract -> {out / 'contract.json'} ({len(VIEWS)} views, ladder "
        f"{[int(c) for c in VAL_COSTS]} bps, selection@{int(SELECT_COST)} by 2023 "
        f"known-contribution $/calendar day) -- before any validation score",
        flush=True,
    )

    # 3) one scoring pass over the analysis corpus; per-day atomic parts, resumable.
    collect_days = sorted(set(val_days) | set(conf_days))
    coverage = collect_parts(
        bundle["lgbm"],
        collect_days,
        out / "collect_parts",
        producer_sha,
        contract_sha,
        model_sha,
        args.resume,
    )
    print(
        f"[collect] {coverage['rows_kept']} admitted states (score >= {MIN_THRESHOLD}) on "
        f"{len(collect_days)} days; recomputed h30/h120 label status "
        f"{coverage['recomputed_label_status']}; missing tape days "
        f"{len(coverage['missing_day_files'])}; missing streams "
        f"{sum(len(v) for v in coverage['missing_symbol_streams'].values())}",
        flush=True,
    )

    # 4) 2023 validation: 50 views x the cost ladder, no median/tail/count gating.
    val_frame = block_frame(out / "collect_parts", val_days)
    print(f"[signals] validation block rows {val_frame.height}", flush=True)
    val_entries = replay_block(val_frame, val_days, VAL_COSTS, "val")
    del val_frame
    ranked = ranked_entries(val_entries)
    chosen_name = ranked[0]["view"]["name"]
    selection = selection_document(ranked, model_sha, producer_sha, contract_sha)
    (out / "selection.json").write_text(json.dumps(selection, indent=2, default=str) + "\n")
    print(
        f"[select] frozen {chosen_name} by 2023 validation known-contribution $/calendar "
        f"day @{int(SELECT_COST)}bps "
        f"({_fmt(selection['chosen_known_contribution_dollars_per_calendar_day'], '+.4f')} "
        f"$/day of {len(VIEWS)} views); ties -> known fills -> fewest attempts -> view key",
        flush=True,
    )

    if args.skip_late:
        reproduction = reproduction_check(val_entries, val_days, None, conf_days)
        results = {
            "contract": contract,
            "selection": selection,
            "validation": {
                "by_view": val_entries,
                "summary": [summary_row(e) for e in ranked],
                "ranked_views": [r["view"] for r in selection["ranked"]],
            },
            "confirmation_by_cost": None,
            "reference_comparison": {
                "validation_2023": incremental_vs_reference(
                    val_entries[chosen_name], val_entries[REFERENCE_VIEW], VAL_COSTS
                ),
                "late_2025_02_2026_05": None,
                "note": "late block not run (--skip-late); the frozen choice stands",
            },
            "reproduction_vs_stored_lane": reproduction,
            "coverage": coverage,
            "decision": decision_text(
                chosen_name, val_entries[chosen_name], ranked, None, reproduction
            ),
            "status": STATUS,
            "artifacts": {
                "contract": str(out / "contract.json"),
                "selection": str(out / "selection.json"),
                "collect_parts": str(out / "collect_parts"),
                "producer_snapshot": str(out / "producer_snapshot.py"),
            },
            "runtime_s": round(time.time() - t0, 1),
        }
        (out / "results.json").write_text(json.dumps(results, indent=2, default=str) + "\n")
        print(f"[decision] {results['decision']}", flush=True)
        print(f"[done] {results['runtime_s']}s -> {out / 'results.json'}", flush=True)
        return

    # 5) late block: every view at the full ladder, choice immutable.
    conf_frame = block_frame(out / "collect_parts", conf_days)
    print(f"[signals] late block rows {conf_frame.height}", flush=True)
    late_entries = replay_block(conf_frame, conf_days, LATE_COSTS, "late")
    del conf_frame
    reproduction = reproduction_check(val_entries, val_days, late_entries, conf_days)
    if reproduction["all_match"] is True:
        print(
            f"[control] {REFERENCE_VIEW} reproduces the stored lane reference cell exactly "
            f"(validation {val_entries[REFERENCE_VIEW]['known_fills']} known fills, "
            f"late {late_entries[REFERENCE_VIEW]['known_fills']} known fills)",
            flush=True,
        )
    elif reproduction["all_match"] is False:
        print(
            f"[control] WARNING: {REFERENCE_VIEW} does not reproduce the stored lane "
            f"reference cell: {reproduction}",
            flush=True,
        )
    else:
        print(
            f"[control] {REFERENCE_VIEW} not comparable to the stored lane "
            f"(restricted corpus or skipped block)",
            flush=True,
        )

    comparison = {
        "validation_2023": incremental_vs_reference(
            val_entries[chosen_name], val_entries[REFERENCE_VIEW], VAL_COSTS
        ),
        "late_2025_02_2026_05": incremental_vs_reference(
            late_entries[chosen_name], late_entries[REFERENCE_VIEW], LATE_COSTS
        ),
    }
    for tag in ("validation_2023", "late_2025_02_2026_05"):
        ref = comparison[tag]["at_reference_100bps"]
        print(
            f"[increment-{tag[:3]}] {chosen_name} vs {REFERENCE_VIEW}: "
            f"delta known-contribution "
            f"{_fmt(ref['delta_known_contribution_dollars_per_day'], '+.4f')} $/day, "
            f"delta lower bound "
            f"{_fmt(ref['delta_full_loss_lower_bound_dollars_per_day'], '+.4f')} $/day, "
            f"delta known fills {ref['delta_known_fills']:+d}, "
            f"delta traded days {ref['delta_traded_days']:+d} @100bps",
            flush=True,
        )
    results = {
        "contract": contract,
        "selection": selection,
        "validation": {
            "by_view": val_entries,
            "summary": [summary_row(e) for e in ranked],
            "ranked_views": [r["view"] for r in selection["ranked"]],
        },
        "confirmation_by_cost": {
            "by_view": late_entries,
            "summary": [summary_row(late_entries[v.name]) for v in VIEWS],
        },
        "reference_comparison": comparison,
        "reproduction_vs_stored_lane": reproduction,
        "coverage": coverage,
        "decision": decision_text(
            chosen_name, val_entries[chosen_name], ranked, late_entries[chosen_name], reproduction
        ),
        "status": STATUS,
        "artifacts": {
            "contract": str(out / "contract.json"),
            "selection": str(out / "selection.json"),
            "collect_parts": str(out / "collect_parts"),
            "producer_snapshot": str(out / "producer_snapshot.py"),
        },
        "runtime_s": round(time.time() - t0, 1),
    }
    (out / "results.json").write_text(json.dumps(results, indent=2, default=str) + "\n")
    print(f"[decision] {results['decision']}", flush=True)
    print(f"[done] results -> {out / 'results.json'} ({results['runtime_s']}s)", flush=True)


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--out", type=Path, default=None, help=f"output root (default: {OUTPUT})")
    p.add_argument(
        "--resume",
        action="store_true",
        help="resume collection from the intact per-day parts already on disk",
    )
    p.add_argument(
        "--skip-late", action="store_true", help="stop after the frozen selection (validation only)"
    )
    p.add_argument(
        "--days", nargs="+", default=None, help="restrict the analysis corpus to these days (debug)"
    )
    run(p.parse_args())


if __name__ == "__main__":
    main()
