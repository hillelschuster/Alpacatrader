#!/usr/bin/env python3
"""Daily-frequency variant of the retained sparse learned selector (no refit).

The retained lead is the h60 payoff model (2021-2022 fit, 26 causal features) read at
threshold 0.03: ONE attempt per ticker per session, ~2 trades per week, +0.0009/day on
2023 validation and +0.0014/day on the late block at 100 bps. That is a conditional tail
winner, not an income stream. This producer asks exactly one question: does the SAME
stored model, at the SAME threshold, support MORE entries per session when the earned
state is allowed to recur?

What is fixed and what varies (nothing else moves):
* ONE stored h60 model is loaded from ``learned/models`` and scored ONCE per day on the
  liquidity-qualified, strictly past-only panel. No refit, no HPO, no new ticker
  features, no label-sided feature. All five views read the same predicted score, so a
  view can only change HOW OFTEN a scored state is taken, never what the state means.
* Five finite views, fixed in code:
    once_h60            thr .03, 1 attempt/ticker/day  (CONTROL = original rare study)
    repeat_h60          thr .03, flat +15min cooldown, 3 attempts/ticker/day
    repeat_h30          same admission, 15min cooldown, 3 attempts, 30-minute exit
    repeat_h15          same admission, 15min cooldown, 3 attempts, 15-minute exit
    repeat_h30_strong   thr .03 + ret3>0 + dd_high15>=-1%, 15min cooldown, 3 attempts
  Re-entry is on the model's own earned-state forecast, not a lowered quality bar; the
  strong view tightens the bar while adding attempts.
* h15/h60 exit labels come from the panel unchanged. The 30-minute exit label is recomputed
  here per day from the STRICT ACTUAL minute opens of the same tape the panel was built
  from (first actual minute open at/after entry+30, capped at the session's last minute;
  no interpolation, no close-price proxy, no future-fill filtering: a state whose exit
  minute never prints stays UNKNOWN).

Replay conventions are deliberately the shared account conventions, tightened where
frequency makes them bite: $3,000 research reserve (3 x $1,000 tickets), no leverage,
fees charged on BOTH legs, one position per symbol, same-clock exits precede buys, and
NO re-entry until the ACTUAL exit plus the 15-minute cooldown (a pending/unknown exit
keeps the slot and the ticker blocked for the rest of that session). An UNKNOWN held
position is charged a full unit at the day's end; an unfilled attempt minute reserves and
releases its ticket and still counts as an attempt. Every calendar / no-signal day stays
in the denominator.

Daily-reset returns are a RESEARCH NORMALIZATION on a fixed $3,000 reserve: every
session restarts from the same reserve, so they are NOT a self-financing CAGR. An
additive carry-equity check (fixed tickets, no compounding) is reported beside it.

Selection: TWO pre-declared selections, both computed on the 2023 validation block only
and both frozen to disk BEFORE any 2025-02..2026-05 outcome exists:
* HISTORICAL (``contract.selection``, ``selection_cost_bps`` = 100): the pre-declared
  2023 dollars/day choice that the stored lane and the old Night callers already read
  (``contract.chosen`` = repeat_h60). 100 bps is recorded as a HISTORICAL choice, not a
  universal hurdle -- nothing is closed by failing to clear it.
* ``provider_cost_selection`` (``selection_cost_bps`` = 25): a second, equally
  pre-declared comparison at a TOTAL 25 bps modeled minute-proxy friction scenario. It is
  a modeled friction scenario, NOT an actual broker fee: a primary US broker's regular
  schedule (zero commission, SEC 20.6 per $1M, CAT 0.000003 per side, daily cent
  rounding) is typically well under 1 bp on a $250-$1,000 ticket, so the 25..150 rungs
  describe a LOW-FEE OPPORTUNITY set (what frequent trading would pay if friction were
  that low), not a fee claim. Two legitimate comparison objects, side by side; neither is
  a shim for the other and neither replaces it.

Reported cost scenarios on BOTH blocks are 25/50/75/100/125/150 bps, with 200 bp kept
only as the historical diagnostic the stored late ladder already carried. Every rung is a
modeled total-friction scenario, so no rung closes a candidate. Both selections are frozen
to disk before this replay's OWN late traversal; the late block was already explored
before this replay, so it is NOT pristine and NOT previously unknown.

The frozen views then replay the late block at the full ladder plus the per-leg
incremental accounting (baseline = same view at one attempt) and emit the historical
choice's trades as an as-of-quote audit input. Late-block outcomes are
DISCOVERY-NOT-VALIDATED (that window was previously explored), so both selections stay
discovery objects and neither is a fresh unseen holdout. More trades is not success:
only a positive incremental net per added leg counts. If all five views are negative
the retained rare base stays the reference; no old h15 freeze and no legacy H042 label
is resurrected.

Novelty vs the pre-owned re-entry roster (factory/scripts/sequencing.py S3_reentry):
that study re-entered a fixed 60-minute hold + 15-minute cooldown episode on the
pre-owned live-admission ML substrate (t1 close-to-close fills, 32 handcrafted features,
2% round-trip cost, Aug-Dec 2025 OOS, no slot/cash accounting, UNKNOWN exits dropped) and
concluded "re-entry-after-cooldown WORST". This is a different mechanism on a different
substrate: re-entry is triggered by the learned h60 payoff model's own score, the
cooldown is anchored to the ACTUAL exit clock (and therefore scales with h15/h30/h60),
the account is a funded reserve with per-symbol slots and same-clock exit precedence, and
the verdict is per-day dollars with per-added-leg incremental economics.

Capacity is a research assumption, not a scalability claim: the $1,000 tickets are book
size on a $3,000 reserve; the parent's as-of NBBO audit showed most displayed top-of-book
depth cannot support them, so dollars/year figures are NOT extrapolated to larger capital.

Usage:
  uv run --no-sync python factory/scripts/alpha_sparse_daily.py
      # outputs -> ~/alpha-data/open-search-v1/learned_sparse_daily
  uv run --no-sync python factory/scripts/alpha_sparse_daily.py --resume
      # top up from the per-day parts
  uv run --no-sync python factory/scripts/alpha_sparse_daily.py --skip-late
      # validation + frozen contract only
  uv run --no-sync python factory/scripts/alpha_sparse_daily.py --out <dir> # relocate the lane root
"""

from __future__ import annotations

import argparse
import heapq
import json
import shutil
import time
from collections import Counter
from dataclasses import dataclass, replace
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
    metrics_view,
    monthly_accounting,
    sha256_file,
)
from alpha_open_panel import ROOT, allowed
from alpha_open_sim import CONTEXT_FEATURES, causal_liquidity, period

# ----- fixed configuration (no HPO, no refit) --------------------------------
MODEL_HORIZON = 60  # the one stored head that is ever loaded
THRESHOLD = 0.03  # unchanged retained admission bar
COOLDOWN_MIN = 15  # flat minutes after the ACTUAL exit
MAX_ATTEMPTS = 3  # attempts per ticker per session
MAX_POSITIONS = 3  # one position per symbol, at most three at once
ORDER_BUDGET = 1000.0  # research ticket size (book only, not capacity)
RESERVE_USD = MAX_POSITIONS * ORDER_BUDGET
SELECT_COST = 100.0  # HISTORICAL selector (bps): the pre-declared 2023 validation choice
PROVIDER_SELECT_COST = 25.0  # TOTAL minute-proxy modeled friction scenario (NOT a broker fee)
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
TRADING_DAYS_PER_YEAR = 252  # session-count convention for $/year
PANEL_LABEL_HORIZONS = (15, 60)  # exit labels the panel producer already stores
RECOMPUTE_HORIZONS = (30,)  # exit labels recomputed here from actual opens
BARS_ROOT = ROOT / "data" / "sip" / "net" / "bars"
# Lane root: the parent's authoritative run of this producer landed in learned_sparse_daily
# (sibling of learned_sparse_extension), so the default stays there and a bare re-run
# resumes/extendsthat root; --out relocates the lane if the parent names another root.
OUTPUT = PANEL_ROOT / "learned_sparse_daily"
SURFACE_PATH = PANEL_ROOT / "learned" / "surface_validation.json"
EXPECTED_DAYS = {VAL_PERIOD: 250, CONF_PERIOD: 332}
ANALYSIS_PERIODS = (VAL_PERIOD, CONF_PERIOD)
FLOAT_TOL = 1e-12
STATUS = "DISCOVERY-NOT-VALIDATED"


# ----- the five fixed views ---------------------------------------------------
@dataclass(frozen=True)
class View:
    """One admission/exit cadence. Same score, same threshold; only cadence differs."""

    name: str
    horizon: int
    cooldown_min: int
    max_attempts: int
    quality: str = "none"  # "none" | "strong"
    control: bool = False

    @property
    def reenters(self) -> bool:
        return self.max_attempts > 1

    @property
    def cadence(self) -> str:
        return (
            f"thr{THRESHOLD:.2f}|quality={self.quality}|exit_h{self.horizon}|"
            f"flat_cooldown_{self.cooldown_min}min|max_attempts_{self.max_attempts}"
        )


VIEWS = (
    View("once_h60", 60, COOLDOWN_MIN, 1, control=True),
    View("repeat_h60", 60, COOLDOWN_MIN, MAX_ATTEMPTS),
    View("repeat_h30", 30, COOLDOWN_MIN, MAX_ATTEMPTS),
    View("repeat_h15", 15, COOLDOWN_MIN, MAX_ATTEMPTS),
    View("repeat_h30_strong", 30, COOLDOWN_MIN, MAX_ATTEMPTS, quality="strong"),
)
VIEWS_BY_NAME = {v.name: v for v in VIEWS}
REPEAT_VIEWS = tuple(v for v in VIEWS if v.reenters)


def baseline_of(view: View) -> View:
    """First-attempt-only twin of a view: the same cadence with re-entry disabled."""
    if not view.reenters:
        return view
    name = f"first_h{view.horizon}" + ("_strong" if view.quality == "strong" else "")
    return replace(view, name=name, max_attempts=1)


def quality_expr(name: str) -> pl.Expr:
    """Past-only admission refinement; 'strong' is the only non-trivial quality gate."""
    if name == "strong":
        return (pl.col("ret3") > 0) & (pl.col("dd_high15") >= -0.01)
    if name != "none":
        raise ValueError(f"unknown quality gate: {name}")
    return pl.lit(True)


def view_signals(signals: pl.DataFrame, view: View) -> pl.DataFrame:
    """Candidate intents for one view: same score, plus that view's quality gate."""
    return signals.filter(quality_expr(view.quality))


NOVELTY = {
    "vs_preowned_reentry_roster": {
        "prior": "factory/scripts/sequencing.py S3_reentry",
        "prior_mechanism": (
            "re-enter a fixed 60-minute hold + 15-minute cooldown episode, cap 3 units per episode"
        ),
        "prior_substrate": (
            "pre-owned live-admission ML stream, t1 close-to-close fills, "
            "32 handcrafted features, 2% round-trip cost, Aug-Dec 2025 OOS, "
            "no slot/cash accounting, null labels dropped"
        ),
        "prior_verdict": "re-entry-after-cooldown WORST (late-stage contamination)",
        "this_study": {
            "trigger": (
                "the stored h60 learned payoff model's own score at the SAME 0.03 "
                "bar; no threshold change, no new features"
            ),
            "cooldown_anchor": (
                "the ACTUAL exit minute of the position, so the spacing "
                "scales with the h15/h30/h60 exit clock instead of a fixed hold"
            ),
            "accounting": (
                "$3,000 funded reserve, 3 x $1,000 tickets, one position per "
                "symbol, same-clock exits before buys, UNKNOWN exits hold the slot "
                "and are charged a full unit, unfilled attempts reserve-and-release"
            ),
            "objective": (
                "two pre-declared 2023 validation selections -- the HISTORICAL "
                "100 bps dollars/day choice and a 25 bps TOTAL modeled minute-proxy "
                "friction comparison (not a broker fee) -- both frozen before this "
                "replay's own late traversal; per-added-leg "
                "incremental net; bootstrap day-level CIs"
            ),
            "exit_labels": (
                "panel h15/h60 plus h30 recomputed from strict actual minute "
                "opens with no future-fill filtering"
            ),
        },
        "why_not_the_same_experiment": (
            "different decision signal, different exit anchor, "
            "different account model, and re-entry is measured "
            "against a first-attempt baseline on identical signals"
        ),
    },
    "not_resurrected": ["primary h15 freeze", "legacy H042 label"],
}


# ----- exit labels recomputed from strict actual minute opens ---------------
SIGNAL_BASE = [
    "day",
    "ticker",
    "t",
    "entry_et",
    "entry_open",
    "entry_status",
    "session_end",
    "ret3",
    "dd_high15",
]
SIGNAL_LABELS = [
    f"{kind}_{h}" for h in (15, 30, 60) for kind in ("gross", "exit_et", "exit_status")
]
SIGNAL_COLUMNS = SIGNAL_BASE + SIGNAL_LABELS
# Columns the panel already stores (h30 is recomputed from the tape afterwards).
SIGNAL_PANEL_COLUMNS = SIGNAL_BASE + [
    f"{kind}_{h}" for h in PANEL_LABEL_HORIZONS for kind in ("gross", "exit_et", "exit_status")
]


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

    Mirrors the panel producer exactly: exit = first actual minute open at/after
    min(entry+h, session_last_minute). A minute that never prints leaves the payoff
    UNKNOWN (never cash, never an interpolation); a state that never filled is cash for
    that minute. ``et`` must be the tape's own ascending minute stamps.
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
    """Attach the h30 exit label (and any other recomputed horizon) to one day's signals.

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
    path = model_dir / f"payoff_h{MODEL_HORIZON}.joblib"
    if not path.exists():
        raise SystemExit(f"[sparse-daily] stored model missing: {path}")
    bundle = joblib.load(path)
    order = list(bundle.get("feature_order") or [])
    if order != list(FEATURES_ALL):
        raise SystemExit("[sparse-daily] stored feature_order != current FEATURES_ALL")
    if int(bundle.get("horizon", -1)) != MODEL_HORIZON:
        raise SystemExit(
            f"[sparse-daily] stored horizon {bundle.get('horizon')} != {MODEL_HORIZON}"
        )
    return bundle, path


# ----- memory-bounded per-day collection with atomic resume -------------------
def day_context(frame: pl.DataFrame) -> pl.DataFrame:
    """Re-derive the four past-only peer-context columns the stored model needs.

    ``alpha_open_sim.load_panel`` computes them at load time over
    ``over(["day","t"])`` windows, so the per-day panel files do not carry them.
    Recomputing them per day on the SAME partition key reproduces the exact values
    (the window is entirely inside one session, and peer_positive3 includes the
    subject itself, exactly as ``CONTEXT_FEATURES`` was defined).
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


def _empty_signal_frame() -> pl.DataFrame:
    labels = {
        **{f"exit_et_{h}": pl.Int64 for h in (15, 30, 60)},
        **{f"gross_{h}": pl.Float64 for h in (15, 30, 60)},
        **{f"exit_status_{h}": pl.String for h in (15, 30, 60)},
        "day": pl.String,
        "ticker": pl.String,
        "t": pl.Int64,
        "entry_et": pl.Int64,
        "entry_open": pl.Float64,
        "entry_status": pl.String,
        "session_end": pl.Int64,
        "ret3": pl.Float64,
        "dd_high15": pl.Float64,
        "score": pl.Float64,
    }
    return pl.DataFrame(schema={k: labels[k] for k in SIGNAL_COLUMNS + ["score"]})


def collect_signals(
    model: dict, days: list[str], out_dir: Path, resume: bool = False
) -> tuple[pl.DataFrame, dict]:
    """Score the panel ONCE (one predict per day) and keep only thr-clearing states.

    One day frame, one tape frame and one day's signals are resident at a time; the full
    corpus is never concatenated. Each finished day is written as an atomic per-day part,
    so a long run resumes exactly where it stopped (the parts are the resume truth).
    """
    parts_dir = out_dir / "collect_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    done = {p.stem for p in parts_dir.glob("????-??-??.parquet")} if resume else set()
    todo = [d for d in days if d not in done]
    coverage = {
        "days_total": len(days),
        "days_cached": len(done),
        "days_collected": len(todo),
        "rows_scored": 0,
        "rows_kept": 0,
        "missing_day_files": [],
        "missing_symbol_streams": {},
        "recomputed_label_status": {},
    }
    label_totals: Counter = Counter()
    for n, day in enumerate(todo, 1):
        if not allowed(day):
            raise ValueError(f"protected/out-of-scope day refused: {day}")
        frame = pl.read_parquet(PANEL_ROOT / "days" / f"{day}.parquet")
        frame = day_context(frame)
        cand = frame.filter(causal_liquidity(frame))
        coverage["rows_scored"] += int(cand.height)
        if cand.height:
            pred = np.asarray(model.predict(feature_matrix(cand)), dtype=float)
            scored = cand.with_columns(pl.Series("score", pred))
            sig = scored.filter(pl.col("score") >= THRESHOLD).select(
                SIGNAL_PANEL_COLUMNS + ["score"]
            )
            if not sig.height:
                sig = _empty_signal_frame()
            coverage["rows_kept"] += int(sig.height)
        else:
            sig = _empty_signal_frame()
        del frame, cand
        info = {
            "day_file_present": True,
            "rows": 0,
            "missing_symbol_streams": [],
            "label_status": {},
        }
        if sig.height:
            sig, info = attach_recomputed_labels(day, sig)
            if not info["day_file_present"]:
                coverage["missing_day_files"].append(day)
            if info["missing_symbol_streams"]:
                coverage["missing_symbol_streams"][day] = info["missing_symbol_streams"]
            label_totals.update(info["label_status"])
        sig = sig.select(SIGNAL_COLUMNS + ["score"]).sort(["ticker", "t"])
        dest = parts_dir / f"{day}.parquet"
        temp = dest.with_suffix(".parquet.tmp")
        sig.write_parquet(temp)
        temp.replace(dest)
        del sig
        if n % 50 == 0 or n == len(todo):
            print(
                f"[collect] {n}/{len(todo)} days, {coverage['rows_kept']} signals (last {day})",
                flush=True,
            )
    coverage["recomputed_label_status"] = dict(label_totals)
    parts = sorted(parts_dir.glob("????-??-??.parquet"))
    frames = [pl.read_parquet(p) for p in parts]
    signals = (
        pl.concat(frames, how="vertical").sort(
            ["day", "t", "score", "ticker"], descending=[False, False, True, False]
        )
        if frames
        else _empty_signal_frame()
    )
    for f in frames:
        del f
    return signals, coverage


# ----- funded-reserve replay with re-entry ------------------------------------
def replay_view(
    signals: pl.DataFrame,
    days: list[str],
    view: View,
    cost_bps: float,
    max_positions: int = MAX_POSITIONS,
    order_budget: float = ORDER_BUDGET,
) -> tuple[dict, list[dict]]:
    """Replay one view on a block under the shared funded-reserve conventions.

    Long-only, one position per symbol, fees on BOTH legs, same-clock exits precede buys,
    no leverage: a symbol may not be re-entered until its ACTUAL exit minute plus the
    view's flat cooldown. An UNKNOWN exit keeps its slot and blocks its ticker for the rest
    of the session and is charged a full unit at the day's end; an unfilled minute reserves
    and releases its ticket and still counts as an attempt. Every calendar day is replayed,
    so no-signal days stay in the denominator as cash days.

    Daily lower-bound returns are the RESEARCH NORMALIZATION on a fixed reserve (each
    session restarts from RESERVE_USD) -- not a self-financing return. ``carry_equity`` is
    the additive fixed-ticket equity check (initial reserve + summed dollars, no
    compounding, no reinvestment of proceeds).
    """
    horizon = view.horizon
    gcol, xcol = f"gross_{horizon}", f"exit_et_{horizon}"
    if not signals.height:
        # No thr-clearing state anywhere: a cash block, never a missing-column crash.
        signals = pl.DataFrame(
            {
                **{
                    c: pl.Series([], dtype=t)
                    for c, t in (
                        (gcol, pl.Float64),
                        (xcol, pl.Int64),
                        ("day", pl.String),
                        ("ticker", pl.String),
                        ("t", pl.Int64),
                        ("score", pl.Float64),
                        ("entry_status", pl.String),
                        ("session_end", pl.Int64),
                        ("entry_et", pl.Int64),
                    )
                }
            }
        )
    missing = [c for c in (gcol, xcol) if c not in signals.columns]
    if missing:
        raise ValueError(f"signals lack {missing} for horizon {horizon}")
    ordered = signals.sort(["day", "t", "score", "ticker"], descending=[False, False, True, False])
    by_day = ordered.partition_by("day", as_dict=True)
    side = cost_bps / 20_000.0
    trades: list[dict] = []
    daily: list[dict] = []
    skips: Counter = Counter()
    reserve = max_positions * order_budget
    equity = reserve
    # Block-wide ground-truth accumulators: EVERY day of the block adds to these. They
    # deliberately live above the day loop -- a per-day reset silently reports only the
    # LAST day's notional/hold sample as the whole block's turnover and hold statistics.
    entry_notional = 0.0
    exit_notional = 0.0
    holds: list[int] = []
    for day in days:
        rows = by_day.get((day,))
        cash, active, blocked, used = reserve, set(), {}, {}
        queue: list[tuple] = []
        seq = 0
        known_pnl = 0.0
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
                if len(active) >= max_positions or cash + 1e-8 < order_budget:
                    skips["cash_or_slot"] += 1
                    continue
                used[ticker] = int(used.get(ticker, 0)) + 1
                attempts += 1
                cash -= order_budget
                active.add(ticker)
                min_cash = min(min_cash, cash)
                pos_samples.append(len(active))
                if r["entry_status"] == "unfilled_expired":
                    # No position was opened: the attempt minute reserves the ticket and
                    # releases it one minute later, fee-free. No exit => no cooldown.
                    heapq.heappush(queue, (t + 1, seq, ticker, order_budget, False))
                    seq += 1
                    continue
                gross, exit_et = r[gcol], r[xcol]
                if gross is None:
                    unknown += 1
                    net, status, proceeds = None, "unknown_pending", 0.0
                    release = int(r["session_end"]) + 1
                else:
                    net = (1.0 + float(gross)) * (1.0 - side) / (1.0 + side) - 1.0
                    status, proceeds = "known_open_proxy", order_budget * (1.0 + net)
                    known_pnl += order_budget * net
                    release = int(exit_et)
                # Pending (unknown) exits keep the slot AND block the ticker: no reopening
                # until the actual exit prints, and never within the session otherwise.
                blocked[ticker] = release + view.cooldown_min
                heapq.heappush(queue, (release, seq, ticker, proceeds, True))
                seq += 1
                fills += 1
                entry_notional += order_budget
                exit_notional += proceeds
                if exit_et is not None and r["entry_et"] is not None:
                    holds.append(int(exit_et) - int(r["entry_et"]))
                trades.append(
                    {
                        "day": day,
                        "ticker": ticker,
                        "t": t,
                        "entry_et": r["entry_et"],
                        "entry_open": r["entry_open"],
                        "exit_et": exit_et,
                        "exit_day": day,
                        "horizon": horizon,
                        "cooldown_min": view.cooldown_min,
                        "gross": gross,
                        "net": net,
                        "cost_bps": cost_bps,
                        "order_budget": order_budget,
                        "score": r["score"],
                        "status": status,
                        "attempt_index": int(used[ticker]),
                        "view": view.name,
                        "max_attempts": view.max_attempts,
                        "positions_at_entry": len(active),
                    }
                )
        lower_pnl = known_pnl - order_budget * unknown
        equity += lower_pnl
        daily.append(
            {
                "day": day,
                "known_pnl": known_pnl,
                "unknown": unknown,
                "fills": fills,
                "attempts": attempts,
                "lower_bound_pnl": lower_pnl,
                "lower_bound_return": lower_pnl / reserve,
                "carry_equity": equity,
                "positions_peak": max(pos_samples) if pos_samples else 0,
                "positions_mean": float(np.mean(pos_samples)) if pos_samples else 0.0,
                "peak_deployed_usd": reserve - min_cash,
            }
        )
    known = np.array([tr["net"] for tr in trades if tr["net"] is not None], dtype=float)
    returns = np.array([d["lower_bound_return"] for d in daily], dtype=float)
    monthly: dict[str, list[float]] = {}
    for d in daily:
        monthly.setdefault(d["day"][:7], []).append(d["lower_bound_return"])
    wins = float(known[known > 0].sum()) if len(known) else 0.0
    losses = float(-known[known < 0].sum()) if len(known) else 0.0
    turnover = entry_notional + exit_notional  # ALL days of the block, never the last one
    dollars_per_day = float(returns.mean()) * reserve if len(returns) else None
    metrics = {
        "days": len(days),
        "attempts": sum(d["attempts"] for d in daily),
        "fills": len(trades),
        "known_fills": len(known),
        "unknown_fills": sum(d["unknown"] for d in daily),
        # The shared replay's counter covers only unfundable/unslotted minutes; cadence
        # skips (slot busy, cooldown, attempt cap) are reported separately.
        "cash_or_slot_skips": int(skips["cash_or_slot"]),
        "skips_total": int(sum(skips.values())),
        "skip_reasons": {k: int(v) for k, v in sorted(skips.items())},
        "cost_bps": cost_bps,
        "horizon": horizon,
        "view": view.name,
        "cooldown_min": view.cooldown_min,
        "max_attempts": view.max_attempts,
        "mean_net_known_fill": float(known.mean()) if len(known) else None,
        "mean_daily_lower_bound": float(returns.mean()) if len(returns) else None,
        "daily_se": (
            float(returns.std(ddof=1) / np.sqrt(len(returns))) if len(returns) > 1 else None
        ),
        "known_win_rate": float((known > 0).mean()) if len(known) else None,
        "known_profit_factor": (wins / losses) if losses else None,
        "worst_known_fill": float(known.min()) if len(known) else None,
        "positive_months_lower_bound": int(sum(np.mean(v) > 0 for v in monthly.values())),
        "months": len(monthly),
        "monthly_mean_lower_bound": {k: float(np.mean(v)) for k, v in monthly.items()},
        "traded_days": len({tr["day"] for tr in trades}),
        "mean_hold_min": float(np.mean(holds)) if holds else None,
        "median_hold_min": float(np.median(holds)) if holds else None,
        "min_hold_min": int(min(holds)) if holds else None,
        "max_hold_min": int(max(holds)) if holds else None,
        "entry_notional_usd": entry_notional,
        "exit_notional_usd": exit_notional,
        "turnover_usd": turnover,
        "turnover_usd_per_day": turnover / len(days) if days else None,
        "positions_mean": float(np.mean([d["positions_mean"] for d in daily])) if daily else None,
        "positions_peak": max(d["positions_peak"] for d in daily) if daily else None,
        "peak_deployed_usd": float(np.mean([d["peak_deployed_usd"] for d in daily]))
        if daily
        else None,
        "reserve_usd": reserve,
        "dollars_per_day": dollars_per_day,
        "dollars_per_year_252": dollars_per_day * TRADING_DAYS_PER_YEAR
        if dollars_per_day is not None
        else None,
        "capital_density_annual": (
            (float(returns.mean()) * TRADING_DAYS_PER_YEAR) if len(returns) else None
        ),
        "carry_equity_end": daily[-1]["carry_equity"] if daily else reserve,
        "carry_equity_return": ((daily[-1]["carry_equity"] / reserve - 1.0) if daily else None),
        "carry_equity_is_compounding": False,
        "daily_reset_normalization": (
            "research only: each session restarts from the same "
            "reserve, so this is NOT a self-financing CAGR"
        ),
        "capacity_note": (
            "book-sized tickets on a research reserve; displayed top-of-book "
            "depth and $1,000 tickets are NOT an executable capacity claim "
            "and scaling is not assumed linear"
        ),
        "execution_proxy_only": True,
        "daily": daily,
    }
    return metrics, trades


# ----- block reporting ---------------------------------------------------------
def ranked_views(val: dict, cost: float) -> list[View]:
    """Rank the five fixed views by validation dollars/day at ONE pre-declared cost rung.

    Both pre-declared selections share this ordering: dollars per day, then known fills,
    then the view name, so a tie can never depend on dict/frame/disk order and the two
    selections are directly comparable in structure (they differ only in the rung).
    """
    key = str(int(cost))
    return sorted(
        VIEWS,
        key=lambda v: (
            -float(val[v.name][key]["dollars_per_day"]),
            -int(val[v.name][key]["known_fills"]),
            v.name,
        ),
    )


def selection_section(
    cost: float,
    ranked: list[View],
    pick: View,
    *,
    identity: str,
    scope: str,
    sibling: str,
    val: dict,
) -> dict:
    """One frozen selection object: the ranked 2023-only choice at a declared cost rung.

    The HISTORICAL object (100 bps) is the choice old Night callers read; the
    provider-cost object (25 bps) is a second legitimate comparison at a TOTAL modeled
    minute-proxy friction scenario (not a broker fee). Both are pre-declared on the
    validation block before this replay's OWN late traversal (that block was already
    explored beforehand, so it is not pristine and not previously unknown), both report
    the full cost ladder beside them, and neither carries a cost floor: no rung closes a
    candidate.
    """
    key = str(int(cost))
    cell = val[pick.name][key]
    return {
        "objective": (
            "2023 validation dollars per day on the research reserve "
            "(= mean_daily_lower_bound x reserve)"
        ),
        "selection_cost_bps": cost,
        "identity": identity,
        "choice_scope": scope,
        "cost_scenario_nature": (
            "each rung is a TOTAL modeled minute-proxy friction scenario on the "
            "proxy prices, NOT an actual broker fee: a primary US broker's regular "
            "schedule (zero commission, SEC 20.6 per $1M, CAT 0.000003 per side, daily "
            "cent rounding) is typically well under 1 bp on a $1,000 ticket, so the low "
            "rungs describe a LOW-FEE OPPORTUNITY set, not a fee claim; actual provider "
            "fees are sourced in the separate provider-fee ledger"
        ),
        "comparison_sibling": sibling,
        "predeclared_before_late_inspection": True,
        "status": STATUS,
        "cost_ladder_validation_bps": [float(c) for c in VAL_COSTS],
        "cost_ladder_late_bps": [float(c) for c in LATE_COSTS],
        "cost_scenarios_bps": [float(c) for c in COST_SCENARIOS_BPS],
        "historical_diagnostic_cost_bps": [HISTORICAL_COST_BPS],
        "cost_ladder_is_comparison_not_hurdle": True,
        "stress_vetoes": None,
        "power_floors": None,
        "median_or_tail_gates": None,
        "sample_reported_with_every_cell": True,
        "ranked_dollars_per_day": {v.name: val[v.name][key]["dollars_per_day"] for v in ranked},
        "chosen": {
            "name": pick.name,
            "horizon": pick.horizon,
            "threshold": THRESHOLD,
            "cooldown_min": pick.cooldown_min,
            "max_attempts": pick.max_attempts,
            "quality": pick.quality,
            "cadence": pick.cadence,
            "control": pick.control,
        },
        "chosen_mean_daily_lower_bound": cell["mean_daily_lower_bound"],
        "chosen_dollars_per_day": cell["dollars_per_day"],
        "chosen_known_fills": cell["known_fills"],
        "chosen_traded_days": cell["traded_days"],
    }


def yearly_accounting(metrics: dict, trades: list[dict]) -> dict:
    """Per-year replayed-day / traded-day / fill / UNKNOWN accounting for one block."""
    years: dict[str, dict] = {}
    for d in metrics["daily"]:
        e = years.setdefault(
            d["day"][:4],
            {
                "days_replayed": 0,
                "traded_days": set(),
                "fills": 0,
                "unknown": 0,
                "known_fills": 0,
                "lower_bound_pnl": 0.0,
                "lower_bound_return_sum": 0.0,
            },
        )
        e["days_replayed"] += 1
        e["unknown"] += d["unknown"]
        e["fills"] += d["fills"]
        e["lower_bound_pnl"] += d["lower_bound_pnl"]
        e["lower_bound_return_sum"] += d["lower_bound_return"]
    for tr in trades:
        e = years.setdefault(
            tr["day"][:4],
            {
                "days_replayed": 0,
                "traded_days": set(),
                "fills": 0,
                "unknown": 0,
                "known_fills": 0,
                "lower_bound_pnl": 0.0,
                "lower_bound_return_sum": 0.0,
            },
        )
        e["traded_days"].add(tr["day"])
        if tr["net"] is not None:
            e["known_fills"] += 1
    out = {}
    for y in sorted(years):
        e = years[y]
        out[y] = {
            "days_replayed": e["days_replayed"],
            "traded_days": len(e["traded_days"]),
            "fills": e["fills"],
            "known_fills": e["known_fills"],
            "unknown_fills": e["unknown"],
            "lower_bound_pnl": round(e["lower_bound_pnl"], 2),
            "mean_daily_lower_bound": (
                e["lower_bound_return_sum"] / e["days_replayed"] if e["days_replayed"] else None
            ),
        }
    return out


def block_view(metrics: dict, trades: list[dict], view: View, n_signals: int) -> dict:
    """Stored metrics for one (view, cost, block) replay with its economics attached."""
    v = metrics_view(metrics)
    v["view"] = {
        "name": view.name,
        "cadence": view.cadence,
        "horizon": view.horizon,
        "cooldown_min": view.cooldown_min,
        "max_attempts": view.max_attempts,
        "quality": view.quality,
        "control": view.control,
        "reenters": view.reenters,
    }
    v["n_signals"] = int(n_signals)
    v["bootstrap_daily"] = bootstrap_daily([d["lower_bound_return"] for d in metrics["daily"]])
    v["sample"] = {
        "known_fills": v["known_fills"],
        "unknown_fills": v["unknown_fills"],
        "traded_days": v["traded_days"],
        "days_replayed": v["days"],
        "attempts": v["attempts"],
        "n_signals": int(n_signals),
    }
    v["economics"] = {
        "dollars_per_day": v["dollars_per_day"],
        "dollars_per_year_252": v["dollars_per_year_252"],
        "capital_density_annual_on_reserve": v["capital_density_annual"],
        "reserve_usd": v["reserve_usd"],
        "max_positions": MAX_POSITIONS,
        "order_budget_usd": ORDER_BUDGET,
        "turnover_usd": v["turnover_usd"],
        "turnover_usd_per_day": v["turnover_usd_per_day"],
        "turnover_per_day_of_capital": (
            v["turnover_usd_per_day"] / RESERVE_USD
            if v["turnover_usd_per_day"] is not None
            else None
        ),
        "time_held_min_mean": v["mean_hold_min"],
        "time_held_min_median": v["median_hold_min"],
        "traded_days": v["traded_days"],
        "attempts_per_traded_day": (v["attempts"] / v["traded_days"] if v["traded_days"] else None),
        "fills_per_attempt": (v["fills"] / v["attempts"]) if v["attempts"] else None,
        "mean_positions_open": v["positions_mean"],
        "peak_positions_open": v["positions_peak"],
        "mean_peak_deployed_usd": v["peak_deployed_usd"],
        "carry_equity_end_usd": v["carry_equity_end"],
        "carry_equity_return": v["carry_equity_return"],
    }
    v["monthly"] = monthly_accounting(metrics, trades)
    v["yearly"] = yearly_accounting(metrics, trades)
    return v


def compare_to_control(control: dict, view: dict, n_signals: int) -> dict:
    """Field-by-field reproduction check of a stored original surface cell."""
    checks: dict[str, dict] = {}
    for f in (
        "days",
        "attempts",
        "fills",
        "known_fills",
        "unknown_fills",
        "cash_or_slot_skips",
        "traded_days",
        "positive_months_lower_bound",
        "months",
        "n_signals",
    ):
        o = control.get(f)
        m = n_signals if f == "n_signals" else view.get(f)
        checks[f] = {"original": o, "reproduced": m, "match": o == m}
    for f in (
        "mean_net_known_fill",
        "mean_daily_lower_bound",
        "daily_se",
        "known_win_rate",
        "known_profit_factor",
        "worst_known_fill",
    ):
        o, m = control.get(f), view.get(f)
        ok = (o is None and m is None) or (
            o is not None and m is not None and abs(float(o) - float(m)) <= FLOAT_TOL
        )
        checks[f] = {"original": o, "reproduced": m, "match": ok}
    om, mm = (
        control.get("monthly_mean_lower_bound") or {},
        view.get("monthly_mean_lower_bound") or {},
    )
    checks["monthly_mean_lower_bound"] = {
        "match": set(om) == set(mm)
        and all(abs(float(om[k]) - float(mm[k])) <= FLOAT_TOL for k in om)
    }
    checks["all_match"] = all(c.get("match") for c in checks.values())
    return checks


def incremental_view(view_metrics: dict, base_metrics: dict) -> dict:
    """What the added legs are actually worth: delta dollars per added leg."""
    days = view_metrics["days"]
    delta_day = view_metrics["mean_daily_lower_bound"] - base_metrics["mean_daily_lower_bound"]
    delta_dollars = delta_day * RESERVE_USD * days
    added_attempts = view_metrics["attempts"] - base_metrics["attempts"]
    added_fills = view_metrics["fills"] - base_metrics["fills"]
    return {
        "baseline_view": base_metrics["view"]["name"],
        "baseline_attempts": base_metrics["attempts"],
        "baseline_fills": base_metrics["fills"],
        "added_attempts": added_attempts,
        "added_fills": added_fills,
        "delta_mean_daily_lower_bound": delta_day,
        "delta_dollars_per_day": delta_day * RESERVE_USD,
        "delta_dollars_total": delta_dollars,
        "delta_dollars_per_added_fill": (delta_dollars / added_fills) if added_fills else None,
        "delta_dollars_per_added_attempt": (delta_dollars / added_attempts)
        if added_attempts
        else None,
        "added_legs_net_positive": bool(added_fills > 0 and delta_dollars > 0),
    }


def write_quote_audit_input(out_dir: Path, view: View, trades: list[dict], cost_bps: float) -> dict:
    """Late-block trades of the chosen view as an as-of-quote audit input.

    Same source, same day/ticker/entry_et/exit_et fields alpha_quote_audit.py already
    consumes; UNKNOWN-exit trades are kept with a null exit so the auditor reports them
    as unknown proxy executions instead of silently dropping them.
    """
    audit_dir = out_dir / "quote_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{view.name}_{int(cost_bps)}bps"
    records = []
    for tr in trades:
        if tr["day"] < "2025-02-01":
            continue
        records.append(
            {
                "day": tr["day"],
                "ticker": tr["ticker"],
                "entry_et": tr["entry_et"],
                "exit_day": tr.get("exit_day", tr["day"]),
                "exit_et": tr["exit_et"],
                "net": tr["net"],
                "gross": tr["gross"],
                "horizon": tr["horizon"],
                "attempt_index": tr["attempt_index"],
                "status": tr["status"],
                "order_budget": tr["order_budget"],
                "cost_bps": tr["cost_bps"],
                "view": tr["view"],
            }
        )
    trades_path = audit_dir / f"trades_{tag}.parquet"
    if records:
        pl.DataFrame(records).write_parquet(trades_path)
    else:
        pl.DataFrame(
            schema={
                "day": pl.String,
                "ticker": pl.String,
                "entry_et": pl.Int64,
                "exit_day": pl.String,
                "exit_et": pl.Int64,
                "net": pl.Float64,
            }
        ).write_parquet(trades_path)
    intents = {
        "audit": "factory/scripts/alpha_quote_audit.py",
        "purpose": (
            "as-of NBBO audit of the chosen view's late-block trades; a quote audit "
            "is not an exchange fill and cannot certify the uncovered portfolio"
        ),
        "block": "2025-02-01..2026-05-31 (previously explored, not pristine)",
        "view": view.name,
        "cadence": view.cadence,
        "cost_bps": cost_bps,
        "selection_cost_bps": SELECT_COST,
        "trades_parquet": str(trades_path),
        "order_budget": ORDER_BUDGET,
        "latency_ms": 250,
        "max_age_s": 2.0,
        "trade_count": len(records),
        "unknown_exit_trades": sum(1 for r in records if r["exit_et"] is None),
        "records": records,
    }
    intents_path = audit_dir / f"intents_{tag}.json"
    intents_path.write_text(json.dumps(intents, indent=2, default=str) + "\n")
    return {
        "trades_parquet": str(trades_path),
        "intents_json": str(intents_path),
        "trade_count": len(records),
        "unknown_exit_trades": intents["unknown_exit_trades"],
    }


# ----- main pipeline -----------------------------------------------------------
def _fmt(value, spec="+.6f"):
    return "n/a" if value is None else format(value, spec)


def print_block(tag: str, view: dict) -> None:
    b = view["bootstrap_daily"]
    ci = f"[{_fmt(b.get('ci95_lo'))},{_fmt(b.get('ci95_hi'))}]" if "ci95_lo" in b else "n/a"
    print(
        f"[{tag}] {view['view']['name']:<18} h={view['horizon']} "
        f"mdlb={_fmt(view['mean_daily_lower_bound'])} "
        f"$/day={_fmt(view['dollars_per_day'], '+.2f')} se={_fmt(view['daily_se'])} "
        f"boot95={ci} p>0={b.get('p_gt_zero')} "
        f"n={view['known_fills']} unk={view['unknown_fills']} "
        f"traded_days={view['traded_days']} attempts={view['attempts']} "
        f"signals={view['n_signals']} "
        f"skips={view['cash_or_slot_skips']}/{view['skips_total']}",
        flush=True,
    )


def analysis_blocks(days: list[str] | None) -> dict[str, list[str]]:
    files = sorted(
        p.stem for p in (PANEL_ROOT / "days").glob("????-??-??.parquet") if allowed(p.stem)
    )
    keep = set(days) if days else None
    blocks: dict[str, list[str]] = {}
    for day in files:
        if keep is not None and day not in keep:
            continue
        p = period(day)
        if p in ANALYSIS_PERIODS:
            blocks.setdefault(p, []).append(day)
    if keep is None:
        for p, expected in EXPECTED_DAYS.items():
            if len(blocks.get(p, [])) != expected:
                raise SystemExit(
                    f"[sparse-daily] requires {expected} {p} days, got {len(blocks.get(p, []))}"
                )
    if not blocks:
        raise SystemExit("[sparse-daily] no analysis days selected")
    return blocks


def load_original_surface(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"[sparse-daily] original validation surface missing: {path}")
    return json.loads(path.read_text())


def scale_section(best_late: dict | None, best_val: dict | None) -> dict:
    point = best_late or best_val
    return {
        "reserve_usd": RESERVE_USD,
        "slots": MAX_POSITIONS,
        "ticket_usd": ORDER_BUDGET,
        "chosen_dollars_per_year_252": (point or {}).get("dollars_per_year_252"),
        "chosen_capital_density_annual_on_reserve": (point or {}).get("capital_density_annual"),
        "chosen_mean_peak_deployed_usd": (point or {}).get("peak_deployed_usd"),
        "chosen_mean_positions_open": (point or {}).get("positions_mean"),
        "annualization": (
            f"mean daily lower-bound $ x {TRADING_DAYS_PER_YEAR} sessions; "
            "late-block day counts are not extrapolated to a full year of "
            "sessions beyond that convention"
        ),
        "scaling_note": (
            "NOT assumed linear and NOT a capacity claim: $1,000 tickets are "
            "book size on a $3,000 research reserve and displayed top-of-book "
            "depth may not support them (see quote_audit/ input)"
        ),
    }


def decision_text(
    chosen: View | None,
    val: dict,
    late: dict,
    inc: dict,
    frozen: bool,
    provider_sel: dict | None = None,
) -> str:
    base = "once_h60"
    base_val = val[base][str(int(SELECT_COST))]["mean_daily_lower_bound"]
    repeat_neg_val = [
        v.name
        for v in REPEAT_VIEWS
        if not (val[v.name][str(int(SELECT_COST))]["mean_daily_lower_bound"] > 0)
    ]
    all_neg = len(repeat_neg_val) == len(REPEAT_VIEWS)
    if chosen is None:
        return (
            f"{STATUS}: no eligible view; the retained rare base once_h60 "
            f"({base_val:+.6f}/day val @100) stays the reference"
        )
    v100 = val[chosen.name][str(int(SELECT_COST))]
    if frozen:
        late_txt = (
            f"; late mean_daily_lower_bound "
            f"{_fmt(late[chosen.name][str(int(SELECT_COST))]['mean_daily_lower_bound'])} "
            f"@100, {_fmt(late[chosen.name][str(int(LATE_COSTS[-1]))]['mean_daily_lower_bound'])} "
            f"@{int(LATE_COSTS[-1])} on the previously explored 2025-02..2026-05 block"
        )
        inc_txt = (
            f"; added legs {inc[chosen.name]['added_fills']} for "
            f"{_fmt(inc[chosen.name]['delta_dollars_per_added_fill'], '+.2f')} $/leg "
            f"(added-leg net positive: {inc[chosen.name]['added_legs_net_positive']})"
        )
    else:
        late_txt = "; late block not run (--skip-late)"
        inc_txt = ""
    provider_txt = ""
    if provider_sel is not None:
        pname = provider_sel["chosen"]["name"]
        pcell = val[pname][str(int(PROVIDER_SELECT_COST))]
        provider_txt = (
            f" Separately pre-declared on the same 2023 validation block: the "
            f"{int(PROVIDER_SELECT_COST)}bps TOTAL modeled minute-proxy friction "
            f"comparison (a friction scenario, not a broker fee) selects {pname} "
            f"({_fmt(pcell['mean_daily_lower_bound'])}/day = "
            f"{_fmt(pcell['dollars_per_day'], '+.2f')} $/day @"
            f"{int(PROVIDER_SELECT_COST)}), reported beside the HISTORICAL "
            f"{int(SELECT_COST)}bps choice (100 bps is a historical choice, not a "
            f"hurdle; the cost ladder is a comparison set and closes nothing)."
        )
    return (
        f"{STATUS}: chosen {chosen.name} ({chosen.cadence}) by 2023 validation dollars/day at "
        f"{int(SELECT_COST)}bps: {_fmt(v100['dollars_per_day'], '+.2f')} $/day = "
        f"{_fmt(v100['mean_daily_lower_bound'])} on a ${int(RESERVE_USD)} reserve, "
        f"{v100['known_fills']} known fills on {v100['traded_days']} traded days; "
        f"control once_h60 {_fmt(base_val)}/day; repeat views not val-positive: "
        f"{repeat_neg_val or 'none'}{late_txt}{inc_txt}.{provider_txt} "
        + (
            "Every repeat view is validation-negative, so the retained rare h60 base stays the "
            "reference and no legacy label is resurrected."
            if all_neg
            else "A repeat view is validation-positive: promotion still requires the frozen "
            "late-block outcomes and the as-of quote audit, and remains DISCOVERY-NOT-VALIDATED."
        )
    )


def run(args: argparse.Namespace) -> None:
    t0 = time.time()
    out = args.out or OUTPUT
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")

    # 1) ONE stored model, loaded and scored (never fitted).
    bundle, model_path = load_stored_model(PANEL_ROOT / "learned" / "models")
    print(
        f"[model] h{MODEL_HORIZON} {model_path} (sha256 {sha256_file(model_path)[:12]}), "
        f"seed={bundle.get('seed')} clip={bundle.get('clip_fit')}",
        flush=True,
    )
    blocks = analysis_blocks(args.days)
    val_days = blocks.get(VAL_PERIOD, [])
    conf_days = blocks.get(CONF_PERIOD, [])
    if not val_days:
        raise SystemExit(
            "[sparse-daily] no 2023 validation days selected; the dollars/day "
            "selection objective requires the validation block"
        )
    collect_days = sorted(set(val_days) | set(conf_days))
    print(
        f"[blocks] validation={len(val_days)} days, late={len(conf_days)} days, "
        f"collect={len(collect_days)} days",
        flush=True,
    )

    # 2) one scoring pass over the analysis corpus; per-day atomic parts, resumable.
    signals, coverage = collect_signals(bundle["lgbm"], collect_days, out, args.resume)
    signals_file = out / "signals.parquet"
    signals.write_parquet(signals_file)
    print(
        f"[collect] {signals.height} thr-clearing states on "
        f"{len(collect_days)} days (h30 labels {coverage['recomputed_label_status']}, "
        f"missing tape days {len(coverage['missing_day_files'])}, "
        f"missing streams {sum(len(v) for v in coverage['missing_symbol_streams'].values())})",
        flush=True,
    )

    # 3) 2023 validation: five views x the cost ladder, no median/tail gating.
    val: dict[str, dict[str, dict]] = {}
    val_daily: dict[str, np.ndarray] = {}

    def block_signals(sig: pl.DataFrame, days: list[str]) -> int:
        """Candidate intents inside one block; the corpus count would cross blocks."""
        return int(sig.filter(pl.col("day").is_in(days)).height) if sig.height else 0

    for view in VIEWS:
        sig = view_signals(signals, view)
        n_val = block_signals(sig, val_days)
        val[view.name] = {}
        for cost in VAL_COSTS:
            metrics, trades = replay_view(sig, val_days, view, cost)
            val[view.name][str(int(cost))] = block_view(metrics, trades, view, n_val)
            if cost == SELECT_COST:
                val_daily[view.name] = np.array([d["lower_bound_return"] for d in metrics["daily"]])
        print_block(f"val@{int(SELECT_COST)}", val[view.name][str(int(SELECT_COST))])
    np.savez(out / "daily_validation.npz", **{k.lower(): v for k, v in val_daily.items()})
    val_costs = {
        view.name: {
            str(int(c)): val[view.name][str(int(c))]["mean_daily_lower_bound"] for c in VAL_COSTS
        }
        for view in VIEWS
    }

    # 3b) the control must reproduce the stored original rare-study cell exactly.
    surface = load_original_surface(SURFACE_PATH)
    control_cell = surface.get("60|0.03")
    if control_cell is None:
        raise SystemExit("[sparse-daily] stored surface has no 60|0.03 control cell")
    control_view = val["once_h60"][str(int(SELECT_COST))]
    repro = compare_to_control(control_cell, control_view, int(control_view["n_signals"]))
    if not repro["all_match"]:
        bad = {k: v for k, v in repro.items() if k != "all_match" and not v.get("match")}
        raise SystemExit(
            f"[sparse-daily] control does not reproduce the original rare study cell: {bad}"
        )
    print(
        f"[control] once_h60 reproduces learned/surface_validation 60|0.03 exactly "
        f"({control_view['known_fills']} known fills, "
        f"{control_view['mean_daily_lower_bound']:+.6f}/day @100)",
        flush=True,
    )

    # 4) first-attempt baselines + per-added-leg incremental economics (validation).
    val_inc: dict[str, dict] = {}
    val_base: dict[str, dict] = {}
    for view in REPEAT_VIEWS:
        base = baseline_of(view)
        sig = view_signals(signals, base)
        metrics, trades = replay_view(sig, val_days, base, SELECT_COST)
        cell = block_view(metrics, trades, base, block_signals(sig, val_days))
        val_base[view.name] = {
            "attempts": cell["attempts"],
            "fills": cell["fills"],
            "mean_daily_lower_bound": cell["mean_daily_lower_bound"],
            "dollars_per_day": cell["dollars_per_day"],
            "known_fills": cell["known_fills"],
            "traded_days": cell["traded_days"],
            "days": cell["days"],
        }
        val_inc[view.name] = incremental_view(val[view.name][str(int(SELECT_COST))], cell)
        print(
            f"[increment-val] {view.name} vs {base.name}: "
            f"+{val_inc[view.name]['added_fills']} fills, "
            f"{_fmt(val_inc[view.name]['delta_dollars_per_day'], '+.2f')} $/day, "
            f"{_fmt(val_inc[view.name]['delta_dollars_per_added_fill'], '+.2f')} $/leg",
            flush=True,
        )

    # 5) TWO pre-declared selections, both on 2023 validation only, both frozen here
    #    BEFORE any late-block outcome is computed:
    #      * HISTORICAL 100 bps -- the choice the stored lane and old Night callers read.
    #      * provider-cost 25 bps -- a TOTAL modeled minute-proxy friction comparison
    #        (a friction scenario, not a broker fee), equally pre-declared.
    historical_ranked = ranked_views(val, SELECT_COST)
    chosen = historical_ranked[0]
    provider_ranked = ranked_views(val, PROVIDER_SELECT_COST)
    provider_chosen = provider_ranked[0]
    print(
        f"[select] HISTORICAL {int(SELECT_COST)}bps choice {chosen.name} by validation "
        f"dollars/day ({_fmt(val[chosen.name][str(int(SELECT_COST))]['dollars_per_day'], '+.2f')} "
        f"$/day) of {len(VIEWS)} views; no median/top-days gate applied",
        flush=True,
    )
    p_cost = PROVIDER_SELECT_COST
    print(
        f"[select-{int(p_cost)}] provider-cost comparison {provider_chosen.name} "
        f"by the same 2023 validation dollars/day rule "
        f"({_fmt(val[provider_chosen.name][str(int(p_cost))]['dollars_per_day'], '+.2f')} "
        f"$/day @{int(p_cost)} TOTAL modeled friction, not a broker fee); declared before "
        f"this replay's own late traversal (block previously explored), "
        f"DISCOVERY-NOT-VALIDATED",
        flush=True,
    )
    historical_sel = selection_section(
        SELECT_COST,
        historical_ranked,
        chosen,
        identity="HISTORICAL",
        scope=(
            "pre-declared 2023 validation selector at 100 bps; kept verbatim as the "
            "historical choice (contract.chosen for old Night callers). 100 bps is a "
            "historical choice, NOT a universal profitability hurdle, and no candidate "
            "is closed by failing to clear it"
        ),
        sibling="provider_cost_selection",
        val=val,
    )
    provider_sel = selection_section(
        PROVIDER_SELECT_COST,
        provider_ranked,
        provider_chosen,
        identity="PROVIDER_COST_COMPARISON",
        scope=(
            "second pre-declared comparison at a 25 bps TOTAL modeled minute-proxy "
            "friction scenario (not a broker fee), ranked on the 2023 validation block "
            "only, before this replay's OWN late traversal; that block was already "
            "explored beforehand, so it is NOT pristine and NOT previously unknown. It "
            "neither replaces nor shims the HISTORICAL 100 bps choice"
        ),
        sibling="selection",
        val=val,
    )

    contract = {
        "study": "alpha_sparse_daily",
        "label": (
            "daily-frequency variant of the retained sparse h60 learned selector "
            "(re-entry on earned-state forecasts, same model, same threshold)"
        ),
        "status": STATUS,
        "frozen_before_late_inspection": True,
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "chosen": {
            "name": chosen.name,
            "horizon": chosen.horizon,
            "threshold": THRESHOLD,
            "cooldown_min": chosen.cooldown_min,
            "max_attempts": chosen.max_attempts,
            "quality": chosen.quality,
            "cadence": chosen.cadence,
            "control": chosen.control,
        },
        "views": [
            {
                "name": v.name,
                "cadence": v.cadence,
                "horizon": v.horizon,
                "cooldown_min": v.cooldown_min,
                "max_attempts": v.max_attempts,
                "quality": v.quality,
                "control": v.control,
            }
            for v in VIEWS
        ],
        "selection": historical_sel,
        "provider_cost_selection": provider_sel,
        "model": {
            "refit": False,
            "hpo": False,
            "feature_changes": False,
            "ticker_features_added": False,
            "horizons_scored": [MODEL_HORIZON],
            "scored_once_over_analysis_corpus": True,
            "fit_block_2021_2022_scored": False,
            "fit_block_note": (
                "the 2021-2022 fit block is never replayed (weights are "
                "frozen), so it is not scored; the single prediction pass "
                "is shared by all five views over the analysis corpus"
            ),
            "stored_path": str(model_path),
            "stored_sha256": sha256_file(model_path),
            "feature_order": FEATURES_ALL,
            "stored_params": bundle.get("params"),
            "clip_fit": bundle.get("clip_fit"),
            "seed": bundle.get("seed"),
        },
        "replay": {
            "engine": "alpha_sparse_daily.replay_view (shared funded-reserve conventions)",
            "reserve_usd": RESERVE_USD,
            "max_positions": MAX_POSITIONS,
            "order_budget": ORDER_BUDGET,
            "leverage": False,
            "fees_both_legs": True,
            "one_position_per_symbol": True,
            "same_clock_exits_before_buys": True,
            "cooldown_rule": (
                "no reopening until the ACTUAL exit minute + cooldown_min; a "
                "pending/UNKNOWN exit keeps its slot and blocks its ticker for "
                "the rest of the session"
            ),
            "unknown_exit_rule": "full-unit lower-bound charge at the day's end, never cash",
            "unfilled_rule": (
                "reserve and release the ticket one minute later, fee-free, and "
                "still count the attempt (no exit => no cooldown)"
            ),
            "all_calendar_days_retained": True,
            "daily_reset_is_not_self_financing_cagr": True,
            "carry_equity_check": "additive fixed-ticket equity, no compounding",
        },
        "labels": {
            "panel_horizons": list(PANEL_LABEL_HORIZONS),
            "recomputed_horizons": list(RECOMPUTE_HORIZONS),
            "recompute_rule": (
                "strict actual minute opens: first actual tape minute open "
                "at/after min(entry+h, session_last_minute); no interpolation, "
                "no close-price proxy, no future-fill filtering"
            ),
        },
        "validation": {
            "by_view_cost": {v.name: val_costs[v.name] for v in VIEWS},
            "surface": val,
            "incremental_vs_first_attempt": val_inc,
            "first_attempt_baselines": val_base,
            "control_reproduction": repro,
            "control_reproduction_note": (
                "the LEGACY control cell (learned/surface_validation 60|0.03) is "
                "reproduced exactly at the HISTORICAL 100 bps selection rung; it stays "
                "the explicit reproduction check and is not a hurdle for any other rung"
            ),
        },
        "coverage": coverage,
        "provenance": {
            "panel_root": str(PANEL_ROOT),
            "panel_contract_sha256": (
                sha256_file(PANEL_ROOT / "contract.json")
                if (PANEL_ROOT / "contract.json").exists()
                else None
            ),
            "producer_sha256": sha256_file(Path(__file__)),
            "original_validation_surface": str(SURFACE_PATH),
            "original_validation_surface_sha256": (
                sha256_file(SURFACE_PATH) if SURFACE_PATH.exists() else None
            ),
            "tape_root": str(BARS_ROOT),
            "analysis_days": len(collect_days),
            "validation_days": len(val_days),
            "late_days": len(conf_days),
            "signals_kept": int(signals.height),
        },
        "novelty": NOVELTY,
    }
    (out / "contract.json").write_text(json.dumps(contract, indent=2, default=str) + "\n")
    print(f"[freeze] contract -> {out / 'contract.json'} (before any late inspection)", flush=True)

    if args.skip_late:
        results = {
            "contract": contract,
            "validation": val,
            "validation_incremental": val_inc,
            "provider_cost_selection": provider_sel,
            "confirmation_by_cost": None,
            "decision": decision_text(
                chosen, val, {}, val_inc, frozen=False, provider_sel=provider_sel
            ),
            "status": STATUS,
            "artifacts": {
                "contract": str(out / "contract.json"),
                "signals": str(signals_file),
                "producer_snapshot": str(out / "producer_snapshot.py"),
            },
            "runtime_s": round(time.time() - t0, 1),
        }
        (out / "results.json").write_text(json.dumps(results, indent=2, default=str) + "\n")
        print(f"[decision] {results['decision']}", flush=True)
        print(f"[done] {results['runtime_s']}s -> {out / 'results.json'}", flush=True)
        return

    # 6) late block: every view at the full ladder, plus first-attempt baselines.
    late: dict[str, dict[str, dict]] = {}
    late_daily: dict[str, np.ndarray] = {}
    late_inc: dict[str, dict] = {}
    late_base: dict[str, dict] = {}
    chosen_trades: dict[str, list[dict]] = {}
    for view in VIEWS:
        sig = view_signals(signals, view)
        n_late = block_signals(sig, conf_days)
        late[view.name] = {}
        for cost in LATE_COSTS:
            metrics, trades = replay_view(sig, conf_days, view, cost)
            late[view.name][str(int(cost))] = block_view(metrics, trades, view, n_late)
            if view.name == chosen.name:
                chosen_trades[str(int(cost))] = trades
                if cost == SELECT_COST:
                    late_daily[view.name] = np.array(
                        [d["lower_bound_return"] for d in metrics["daily"]]
                    )
        print_block("late", late[view.name][str(int(SELECT_COST))])
    np.savez(out / "daily_confirmation.npz", **{k.lower(): v for k, v in late_daily.items()})
    for view in REPEAT_VIEWS:
        base = baseline_of(view)
        sig = view_signals(signals, base)
        metrics, trades = replay_view(sig, conf_days, base, SELECT_COST)
        cell = block_view(metrics, trades, base, block_signals(sig, conf_days))
        late_base[view.name] = {
            "attempts": cell["attempts"],
            "fills": cell["fills"],
            "mean_daily_lower_bound": cell["mean_daily_lower_bound"],
            "dollars_per_day": cell["dollars_per_day"],
            "known_fills": cell["known_fills"],
            "traded_days": cell["traded_days"],
            "days": cell["days"],
        }
        late_inc[view.name] = incremental_view(late[view.name][str(int(SELECT_COST))], cell)
        print(
            f"[increment-late] {view.name} vs {base.name}: "
            f"+{late_inc[view.name]['added_fills']} fills, "
            f"{_fmt(late_inc[view.name]['delta_dollars_per_day'], '+.2f')} $/day, "
            f"{_fmt(late_inc[view.name]['delta_dollars_per_added_fill'], '+.2f')} $/leg",
            flush=True,
        )

    # 7) chosen view: same-source as-of quote audit input at every late cost.
    audit = {}
    for cost in LATE_COSTS:
        key = str(int(cost))
        audit[key] = write_quote_audit_input(out, chosen, chosen_trades.get(key, []), cost)
    print(
        f"[quote-audit-input] view={chosen.name} "
        f"{ {k: v['trade_count'] for k, v in audit.items()} } trades "
        f"({audit[str(int(SELECT_COST))]['unknown_exit_trades']} unknown-exit @"
        f"{int(SELECT_COST)}) -> {audit[str(int(SELECT_COST))]['trades_parquet']}",
        flush=True,
    )
    base_cmp = {
        "retained_rare_base_once_h60": {
            "validation_mean_daily_lower_bound": val["once_h60"][str(int(SELECT_COST))][
                "mean_daily_lower_bound"
            ],
            "late_mean_daily_lower_bound_by_cost": {
                c: late["once_h60"][c]["mean_daily_lower_bound"] for c in late["once_h60"]
            },
            "late_known_fills": late["once_h60"][str(int(SELECT_COST))]["known_fills"],
        },
        "first_attempt_baseline_same_cadence": late_base.get(chosen.name),
        "incremental_vs_first_attempt": late_inc.get(chosen.name),
    }
    decision = decision_text(chosen, val, late, late_inc, frozen=True, provider_sel=provider_sel)
    provider_with_late = {
        **provider_sel,
        "late_mean_daily_lower_bound_by_cost": {
            c: late[provider_sel["chosen"]["name"]][c]["mean_daily_lower_bound"]
            for c in late[provider_sel["chosen"]["name"]]
        },
        "late_known_fills_at_selection_cost": late[provider_sel["chosen"]["name"]][
            str(int(PROVIDER_SELECT_COST))
        ]["known_fills"],
        "late_disclosure": (
            "2025-02..2026-05 was already explored before this replay, so this late "
            "outcome is DISCOVERY-NOT-VALIDATED - NOT pristine and NOT previously unknown"
        ),
    }
    results = {
        "contract": contract,
        "validation": val,
        "validation_incremental": val_inc,
        "validation_first_attempt_baselines": val_base,
        "provider_cost_selection": provider_with_late,
        "confirmation_by_cost": late,
        "confirmation_incremental": late_inc,
        "confirmation_first_attempt_baselines": late_base,
        "chosen_baseline_comparison": base_cmp,
        "quote_audit_input": audit,
        "scale": scale_section(
            late[chosen.name][str(int(SELECT_COST))], val[chosen.name][str(int(SELECT_COST))]
        ),
        "decision": decision,
        "status": STATUS,
        "artifacts": {
            "contract": str(out / "contract.json"),
            "signals": str(signals_file),
            "collect_parts": str(out / "collect_parts"),
            "quote_audit_input": audit,
            "producer_snapshot": str(out / "producer_snapshot.py"),
        },
        "runtime_s": round(time.time() - t0, 1),
    }
    (out / "results.json").write_text(json.dumps(results, indent=2, default=str) + "\n")
    print(f"[decision] {decision}", flush=True)
    print(f"[done] results -> {out / 'results.json'} ({results['runtime_s']}s)", flush=True)


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--out", type=Path, default=None, help=f"output root (default: {OUTPUT})")
    p.add_argument(
        "--days", nargs="+", default=None, help="restrict the analysis corpus to these days (debug)"
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="resume collection from the per-day parts already on disk",
    )
    p.add_argument(
        "--skip-late", action="store_true", help="stop after the frozen contract (validation only)"
    )
    run(p.parse_args())


if __name__ == "__main__":
    main()
