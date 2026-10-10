#!/usr/bin/env python3
"""Decision-time x admission-age surface of the retained repeat_h60 mechanism (no refit).

The retained lead is the ONE immutable h60 payoff model (2021-2022 fit, 26 causal
features) read at threshold 0.03 with the frozen repeat cadence: flat 15-minute
cooldown after the ACTUAL exit, max 3 attempts per ticker per day, 3 slots x $1,000
research reserve, one position per symbol, same-clock exits before buys, UNKNOWN
exits charged a full unit (never cash). This producer asks exactly one question: is
that retained edge concentrated in particular DECISION TIMES (the panel signal minute
t) or in FRESH vs AGED admissions (t - admit_t)?

What is fixed and what varies (nothing else moves):
* ONE stored h60 model is loaded from ``learned/models`` and scored ONCE per day on the
  liquidity-qualified, strictly past-only panel. No refit, no HPO, no new features, no
  label-sided feature, no threshold change, no cadence change. Every cell of the grid
  reads the same predicted score, so a cell can only change WHICH scored states the
  shared account may act on, never what the state means.
* The pre-declared 2-axis grid (12 fixed cells, no other filter):
    decision band by the panel signal minute t (minutes since midnight ET):
        all            every scored minute
        t <= 630       through the 10:30 ET minute-open
        630 < t <= 780 10:35 ET through 13:00 ET
        t > 780        after 13:00 ET
    admission-age band by (t - admit_t) in minutes (both strictly causal panel fields;
    admit_t is the B-snapshot admission minute, so age is known at decision time):
        all            every age
        < 30 min       fresh admissions
        >= 30 min      aged admissions
  4 x 3 = 12 cells. The all/all cell is the frozen reference: it IS the stored
  repeat_h60 lane (factory/scripts/alpha_sparse_daily.py) with no extra filter, and it
  must reproduce that lane's cells exactly at every cost rung on both blocks (a hard
  runtime check against the stored lane's contract.json / results.json, never a soft
  note). Note: the panel minute clock is minutes since midnight ET, so t=630 is the
  10:30 ET minute and t=780 is 13:00 ET; the numeric cut points are the spec.
* Each cell is a FULL account replay of the shared funded-reserve conventions in which
  only that cell's signals are admissible: slots, per-ticker attempt caps, the actual-
  exit-anchored cooldown, unfilled reserve-and-release, UNKNOWN-slot-hold, and the
  full-calendar-day denominator all apply inside the cell, so every cell carries
  complete attempt/fill/unknown accounting. This is a mechanism PROFILE: it says where
  the retained edge sits, it does not add a mechanism.

Reported cost scenarios per cell are 25/50/75/100/125/150 bps (TOTAL modeled
round-trip friction on the minute-open proxy prices), with 200 bps kept only as the
historical diagnostic the stored ladder already carried. The ladder is a COMPARISON
SET, never a hurdle: no rung closes a candidate and no rung falsifies anything.
Selection is pre-declared on the 2023 validation block ONLY, at the 25 bps rung, on
known-contribution dollars per FULL CALENDAR DAY (known contributions less the
separately-reported full-loss charge on UNKNOWN exits; unknowns never cash and never
blend into the known-fill statistics), tie-broken by more known fills, then fewer
attempts, then the cell key. The choice is frozen to disk BEFORE any 2025-02..2026-05
outcome is computed; the late 332-day traversal that follows is for transparency only
and cannot change the choice. That late window was already explored before this study,
so it is NOT pristine and NOT previously unknown, and the whole surface stays
DISCOVERY-NOT-VALIDATED. The mechanism profile was DESCRIPTIVE: the winning cell is a
2023-validation selection only, not a validated sub-strategy, and 12 cells were scored
so multiple comparisons are expected to flatter small samples.

Fills are MINUTE-OPEN PROXIES throughout: every entry/exit is the first actual tape
minute open at/after the stamped decision minute (or an explicit no-fill / UNKNOWN),
never an actual quote, an exchange fill, or a high-touch price. Dollars per year are a
SIMPLE 252-SESSION annualization of mean daily dollars (not a CAGR, not a
self-financing return; every session restarts from the same $3,000 research reserve).
Capacity is a research assumption: $1,000 tickets are book size on the reserve and are
NOT an executable-capacity claim.

Usage:
  uv run --no-sync python factory/scripts/alpha_retained_timing.py
      # outputs -> ~/alpha-data/open-search-v1/retained_timing
  uv run --no-sync python factory/scripts/alpha_retained_timing.py --resume
      # resume collection from the per-day atomic parts already on disk
  uv run --no-sync python factory/scripts/alpha_retained_timing.py --skip-late
      # 2023 validation + frozen selection only (no late traversal)
  uv run --no-sync python factory/scripts/alpha_retained_timing.py --out <dir>
      # relocate the output root
  uv run --no-sync python factory/scripts/alpha_retained_timing.py --days 2023-01-03 2023-01-04
      # restrict the analysis corpus (debug)
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
    metrics_view,
    monthly_accounting,
    sha256_file,
)
from alpha_open_panel import allowed
from alpha_open_sim import CONTEXT_FEATURES, causal_liquidity, period

# ----- fixed configuration (no HPO, no refit) --------------------------------
MODEL_HORIZON = 60  # the one stored head that is ever loaded
THRESHOLD = 0.03  # unchanged retained admission bar
COOLDOWN_MIN = 15  # flat minutes after the ACTUAL exit
MAX_ATTEMPTS = 3  # attempts per ticker per session
MAX_POSITIONS = 3  # one position per symbol, at most three at once
ORDER_BUDGET = 1000.0  # research ticket size (book only, not capacity)
RESERVE_USD = MAX_POSITIONS * ORDER_BUDGET
# Pre-declared freeze rung for the cell choice: a TOTAL 25 bps modeled minute-proxy
# friction scenario (NOT a broker fee; a primary US broker's regular schedule is
# typically well under 1 bp on a $1,000 ticket). The 25..150 rungs describe a LOW-FEE
# OPPORTUNITY set, not a fee claim; actual provider fees are sourced separately.
FREEZE_COST_BPS = 25.0
COST_SCENARIOS_BPS = (25.0, 50.0, 75.0, 100.0, 125.0, 150.0)
# Historical diagnostic only: the 200 bp rung the stored late ladder already carried.
HISTORICAL_COST_BPS = 200.0
VAL_COSTS = COST_SCENARIOS_BPS + (HISTORICAL_COST_BPS,)
LATE_COSTS = COST_SCENARIOS_BPS + (HISTORICAL_COST_BPS,)
TRADING_DAYS_PER_YEAR = 252  # session-count convention for $/year (NOT a CAGR)
PANEL_LABEL_HORIZON = (60,)  # the only exit label this study reads (panel-stored)
OUTPUT = PANEL_ROOT / "retained_timing"
# The frozen reference lane this producer must reproduce in its all/all cell.
REFERENCE_LANE = PANEL_ROOT / "learned_sparse_daily"
EXPECTED_DAYS = {VAL_PERIOD: 250, CONF_PERIOD: 332}
ANALYSIS_PERIODS = (VAL_PERIOD, CONF_PERIOD)
PROGRESS_EVERY = 25  # collection progress cadence in days
FLOAT_TOL = 1e-12
STATUS = "DISCOVERY-NOT-VALIDATED"
FREEZE_REFERENCE_KEY = "all|all"


# ----- the pre-declared 2-axis grid (12 fixed cells) --------------------------
T_CUT_EARLY = 630  # t <= 630: through the 10:30 ET minute (minutes since midnight ET)
T_CUT_LATE = 780  # 630 < t <= 780: 10:35..13:00 ET; t > 780: after 13:00 ET
AGE_CUT_MIN = 30  # (t - admit_t) < 30 fresh vs >= 30 aged

T_BANDS = ("all", "le630", "gt630le780", "gt780")
AGE_BANDS = ("all", "lt30", "ge30")

T_BAND_TEXT = {
    "all": "all signal minutes",
    "le630": f"t <= {T_CUT_EARLY} (through the 10:30 ET minute-open)",
    "gt630le780": f"{T_CUT_EARLY} < t <= {T_CUT_LATE} (10:35..13:00 ET)",
    "gt780": f"t > {T_CUT_LATE} (after 13:00 ET)",
}
AGE_BAND_TEXT = {
    "all": "all admission ages",
    "lt30": f"t - admit_t < {AGE_CUT_MIN} min (fresh admissions)",
    "ge30": f"t - admit_t >= {AGE_CUT_MIN} min (aged admissions)",
}


@dataclass(frozen=True)
class Cell:
    """One fixed grid cell: a decision-time band x an admission-age band."""

    key: str
    t_band: str
    age_band: str

    @property
    def frozen_reference(self) -> bool:
        return self.key == FREEZE_REFERENCE_KEY

    @property
    def description(self) -> str:
        return f"decision={T_BAND_TEXT[self.t_band]}; admission_age={AGE_BAND_TEXT[self.age_band]}"


CELLS = tuple(Cell(key=f"{t}|{a}", t_band=t, age_band=a) for t in T_BANDS for a in AGE_BANDS)
CELLS_BY_KEY = {c.key: c for c in CELLS}


def cell_expr(cell: Cell) -> pl.Expr:
    """The cell's only filter, as a polars expression on causal panel fields."""
    e = pl.lit(True)
    if cell.t_band == "le630":
        e = e & (pl.col("t") <= T_CUT_EARLY)
    elif cell.t_band == "gt630le780":
        e = e & (pl.col("t") > T_CUT_EARLY) & (pl.col("t") <= T_CUT_LATE)
    elif cell.t_band == "gt780":
        e = e & (pl.col("t") > T_CUT_LATE)
    elif cell.t_band != "all":
        raise ValueError(f"unknown t band: {cell.t_band}")
    age = pl.col("t") - pl.col("admit_t")
    if cell.age_band == "lt30":
        e = e & (age < AGE_CUT_MIN)
    elif cell.age_band == "ge30":
        e = e & (age >= AGE_CUT_MIN)
    elif cell.age_band != "all":
        raise ValueError(f"unknown age band: {cell.age_band}")
    return e


def cell_signals(signals: pl.DataFrame, cell: Cell, days: list[str]) -> pl.DataFrame:
    """Candidate intents inside one block for one cell: the cell's band filter only."""
    if not signals.height:
        return signals
    return signals.filter(cell_expr(cell)).filter(pl.col("day").is_in(days))


# ----- one day's scored states (panel-stored h60 labels; nothing recomputed) --
SIGNAL_COLUMNS = [
    "day",
    "ticker",
    "t",
    "admit_t",
    "entry_et",
    "entry_open",
    "entry_status",
    "session_end",
    "gross_60",
    "exit_et_60",
    "exit_status_60",
    "score",
]
SIGNAL_SCHEMA = {
    "day": pl.String,
    "ticker": pl.String,
    "t": pl.Int64,
    "admit_t": pl.Int64,
    "entry_et": pl.Int64,
    "entry_open": pl.Float64,
    "entry_status": pl.String,
    "session_end": pl.Int64,
    "gross_60": pl.Float64,
    "exit_et_60": pl.Int64,
    "exit_status_60": pl.String,
    "score": pl.Float64,
}
SCHEMA_SIGNATURE = hashlib.sha256(
    json.dumps({c: str(t) for c, t in SIGNAL_SCHEMA.items()}, sort_keys=True).encode()
).hexdigest()
GCOL, XCOL = "gross_60", "exit_et_60"


def _empty_signal_frame() -> pl.DataFrame:
    return pl.DataFrame(schema=dict(SIGNAL_SCHEMA))


# ----- stored model (loaded, never fitted) ------------------------------------
def load_stored_model(model_dir: Path) -> tuple[dict, Path]:
    path = model_dir / f"payoff_h{MODEL_HORIZON}.joblib"
    if not path.exists():
        raise SystemExit(f"[retained-timing] stored model missing: {path}")
    bundle = joblib.load(path)
    order = list(bundle.get("feature_order") or [])
    if order != list(FEATURES_ALL):
        raise SystemExit("[retained-timing] stored feature_order != current FEATURES_ALL")
    if int(bundle.get("horizon", -1)) != MODEL_HORIZON:
        raise SystemExit(
            f"[retained-timing] stored horizon {bundle.get('horizon')} != {MODEL_HORIZON}"
        )
    return bundle, path


# ----- memory-bounded per-day collection with atomic resume -------------------
def day_context(frame: pl.DataFrame) -> pl.DataFrame:
    """Re-derive the four past-only peer-context columns the stored model needs.

    ``alpha_open_sim.load_panel`` computes them at load time over
    ``over(["day","t"])`` windows, so the per-day panel files do not carry them.
    Recomputing them per day on the SAME partition key reproduces the exact values
    (the window is entirely inside one session).
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


def resume_hash(day: str, hashes: dict) -> str:
    """sha256(producer sha | contract-core sha | model sha | day | schema signature)."""
    payload = "|".join(
        [
            hashes["producer_sha256"],
            hashes["contract_core_sha256"],
            hashes["model_sha256"],
            day,
            SCHEMA_SIGNATURE,
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _write_resume_index(index: dict, parts_dir: Path) -> None:
    temp = parts_dir / "_resume_index.json.tmp"
    temp.write_text(json.dumps(index, sort_keys=True) + "\n")
    temp.replace(parts_dir / "_resume_index.json")


def collect_signals(
    model: dict,
    days: list[str],
    out_dir: Path,
    hashes: dict,
    resume: bool = False,
) -> tuple[pl.DataFrame, dict]:
    """Score the panel ONCE (one predict per day) and keep only thr-clearing states.

    One day frame is resident at a time; the full corpus is never concatenated during
    collection. Each finished day is an atomic per-day part plus a resume-index entry
    recording the day's resume hash, so a long run resumes exactly where it stopped
    and a changed producer/contract/model/schema silently forces recollection.
    """
    parts_dir = out_dir / "collect_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    index: dict[str, str] = {}
    if resume and (parts_dir / "_resume_index.json").exists():
        index = json.loads((parts_dir / "_resume_index.json").read_text())
    expected = {d: resume_hash(d, hashes) for d in days}
    done = {
        d
        for d in days
        if resume and index.get(d) == expected[d] and (parts_dir / f"{d}.parquet").exists()
    }
    todo = [d for d in days if d not in done]
    coverage = {
        "days_total": len(days),
        "days_cached": len(done),
        "days_collected": len(todo),
        "rows_scored": 0,
        "rows_kept": 0,
        "resume": bool(resume),
        "schema_signature": SCHEMA_SIGNATURE,
    }
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
            sig = scored.filter(pl.col("score") >= THRESHOLD).select(list(SIGNAL_COLUMNS))
            if not sig.height:
                sig = _empty_signal_frame()
            coverage["rows_kept"] += int(sig.height)
        else:
            sig = _empty_signal_frame()
        del frame, cand
        sig = sig.select(SIGNAL_COLUMNS).cast(dict(SIGNAL_SCHEMA)).sort(["ticker", "t"])
        dest = parts_dir / f"{day}.parquet"
        temp = dest.with_suffix(".parquet.tmp")
        sig.write_parquet(temp)
        temp.replace(dest)
        index[day] = expected[day]
        _write_resume_index(index, parts_dir)
        del sig
        if n % PROGRESS_EVERY == 0 or n == len(todo):
            print(
                f"[collect] {n}/{len(todo)} days, {coverage['rows_kept']} signals (last {day})",
                flush=True,
            )
    # Only the requested days' parts assemble the corpus; anything else on disk is a
    # stale part from a different corpus selection and is ignored (never blended).
    frames = []
    for d in days:
        part = parts_dir / f"{d}.parquet"
        if part.exists():
            frames.append(pl.read_parquet(part))
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


# ----- funded-reserve replay restricted to one cell ---------------------------
def _empty_cell_frame() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "day": pl.String,
            "ticker": pl.String,
            "t": pl.Int64,
            "admit_t": pl.Int64,
            "entry_et": pl.Int64,
            "entry_open": pl.Float64,
            "entry_status": pl.String,
            "session_end": pl.Int64,
            GCOL: pl.Float64,
            XCOL: pl.Int64,
            "exit_status_60": pl.String,
            "score": pl.Float64,
        }
    )


def replay_cell(
    signals: pl.DataFrame,
    days: list[str],
    cost_bps: float,
    max_positions: int = MAX_POSITIONS,
    order_budget: float = ORDER_BUDGET,
) -> tuple[dict, list[dict]]:
    """Replay one cell on a block under the shared funded-reserve conventions.

    Long-only, one position per symbol, fees on BOTH legs, same-clock exits precede
    buys, no leverage: a symbol may not be re-entered until its ACTUAL exit minute plus
    the flat cooldown. An UNKNOWN exit keeps its slot and blocks its ticker for the rest
    of the session and is charged a full unit at the day's end; an unfilled minute
    reserves and releases its ticket and still counts as an attempt. Every calendar day
    is replayed, so no-signal days stay in the denominator as cash days.

    Known-fill statistics (mean net, win rate, profit factor, worst fill) are computed
    on KNOWN fills only; the UNKNOWN full-loss charge is a separately labeled lower
    bound that is never blended into them and never turned into cash.
    """
    if not signals.height:
        signals = _empty_cell_frame()
    missing = [c for c in (GCOL, XCOL) if c not in signals.columns]
    if missing:
        raise ValueError(f"signals lack {missing} for horizon {MODEL_HORIZON}")
    ordered = signals.sort(["day", "t", "score", "ticker"], descending=[False, False, True, False])
    by_day = ordered.partition_by("day", as_dict=True)
    side = cost_bps / 20_000.0
    trades: list[dict] = []
    daily: list[dict] = []
    skips: Counter = Counter()
    reserve = max_positions * order_budget
    equity = reserve
    entry_notional = 0.0
    exit_notional = 0.0
    holds: list[int] = []
    known_contribution_usd = 0.0
    unknown_fills = 0
    unfilled_attempts = 0
    no_order_days = 0
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
                if int(used.get(ticker, 0)) >= MAX_ATTEMPTS:
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
                    unfilled_attempts += 1
                    heapq.heappush(queue, (t + 1, seq, ticker, order_budget, False))
                    seq += 1
                    continue
                gross, exit_et = r[GCOL], r[XCOL]
                if gross is None:
                    unknown += 1
                    net, status, proceeds = None, "unknown_pending", 0.0
                    release = int(r["session_end"]) + 1
                else:
                    net = (1.0 + float(gross)) * (1.0 - side) / (1.0 + side) - 1.0
                    status, proceeds = "known_open_proxy", order_budget * (1.0 + net)
                    known_pnl += order_budget * net
                    release = int(exit_et)
                blocked[ticker] = release + COOLDOWN_MIN
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
                        "admit_t": int(r["admit_t"]),
                        "entry_et": r["entry_et"],
                        "entry_open": r["entry_open"],
                        "exit_et": exit_et,
                        "exit_day": day,
                        "horizon": MODEL_HORIZON,
                        "cooldown_min": COOLDOWN_MIN,
                        "gross": gross,
                        "net": net,
                        "cost_bps": cost_bps,
                        "order_budget": order_budget,
                        "score": r["score"],
                        "status": status,
                        "attempt_index": int(used[ticker]),
                        "positions_at_entry": len(active),
                    }
                )
        if attempts == 0:
            no_order_days += 1
        lower_pnl = known_pnl - order_budget * unknown
        known_contribution_usd += known_pnl
        unknown_fills += unknown
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
    turnover = entry_notional + exit_notional
    unknown_full_loss_usd = order_budget * unknown_fills
    dollars_per_day = float(returns.mean()) * reserve if len(returns) else None
    metrics = {
        "days": len(days),
        "attempts": sum(d["attempts"] for d in daily),
        "fills": len(trades),
        "known_fills": len(known),
        "unknown_fills": unknown_fills,
        "unfilled_attempts": unfilled_attempts,
        "no_order_days": no_order_days,
        "cash_or_slot_skips": int(skips["cash_or_slot"]),
        "skips_total": int(sum(skips.values())),
        "skip_reasons": {k: int(v) for k, v in sorted(skips.items())},
        "cost_bps": cost_bps,
        "horizon": MODEL_HORIZON,
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
        # Separately labeled decomposition: the lower bound is the KNOWN contribution
        # less the UNKNOWN full-loss charge; the two are never blended and unknowns
        # are never treated as cash.
        "known_contribution_usd": known_contribution_usd,
        "unknown_full_loss_usd": unknown_full_loss_usd,
        "lower_bound_usd": known_contribution_usd - unknown_full_loss_usd,
        "known_only_usd_per_day": (known_contribution_usd / len(days)) if days else None,
        "unknown_full_loss_usd_per_day": (unknown_full_loss_usd / len(days)) if days else None,
        "unknowns_never_cash": True,
        "full_loss_lower_bound_separate_from_known": True,
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
        "annualization": (
            f"simple mean daily dollars x {TRADING_DAYS_PER_YEAR} sessions; "
            "NOT a CAGR and not a self-financing return"
        ),
        "capacity_note": (
            "book-sized tickets on a research reserve; displayed top-of-book "
            "depth and $1,000 tickets are NOT an executable capacity claim "
            "and scaling is not assumed linear"
        ),
        "execution_proxy_only": True,
        "fills_are_minute_open_proxies": True,
        "daily": daily,
    }
    return metrics, trades


# ----- cell reporting ----------------------------------------------------------
def cell_view(metrics: dict, trades: list[dict], cell: Cell, n_signals: int) -> dict:
    """Stored metrics for one (cell, cost, block) replay with its economics attached."""
    v = metrics_view(metrics)
    v["cell"] = {
        "key": cell.key,
        "t_band": cell.t_band,
        "t_window": T_BAND_TEXT[cell.t_band],
        "age_band": cell.age_band,
        "age_window": AGE_BAND_TEXT[cell.age_band],
        "description": cell.description,
        "frozen_reference": cell.frozen_reference,
    }
    v["n_signals"] = int(n_signals)
    v["bootstrap_daily"] = bootstrap_daily([d["lower_bound_return"] for d in metrics["daily"]])
    v["sample"] = {
        "known_fills": v["known_fills"],
        "unknown_fills": v["unknown_fills"],
        "unfilled_attempts": v["unfilled_attempts"],
        "attempts": v["attempts"],
        "fills": v["fills"],
        "no_order_days": v["no_order_days"],
        "traded_days": v["traded_days"],
        "days_replayed": v["days"],
        "n_signals": int(n_signals),
        "months": v["months"],
    }
    v["economics"] = {
        "dollars_per_day": v["dollars_per_day"],
        "dollars_per_year_252": v["dollars_per_year_252"],
        "annualization": v["annualization"],
        "capital_density_annual_on_reserve": v["capital_density_annual"],
        "reserve_usd": v["reserve_usd"],
        "max_positions": MAX_POSITIONS,
        "order_budget_usd": ORDER_BUDGET,
        "turnover_usd": v["turnover_usd"],
        "turnover_usd_per_day": v["turnover_usd_per_day"],
        "time_held_min_mean": v["mean_hold_min"],
        "time_held_min_median": v["median_hold_min"],
        "traded_days_of_total_days": f"{v['traded_days']}/{v['days']}",
        "attempts_per_traded_day": (v["attempts"] / v["traded_days"]) if v["traded_days"] else None,
        "fills_per_attempt": (v["fills"] / v["attempts"]) if v["attempts"] else None,
        "mean_positions_open": v["positions_mean"],
        "peak_positions_open": v["positions_peak"],
        "mean_peak_deployed_usd": v["peak_deployed_usd"],
        "carry_equity_end_usd": v["carry_equity_end"],
        "carry_equity_return": v["carry_equity_return"],
    }
    # The full-loss lower bound over UNKNOWNs is reported as its own labeled object,
    # never blended into the known-fill statistics above.
    v["unknown_full_loss_bound"] = {
        "unknown_fills": v["unknown_fills"],
        "unknown_full_loss_usd": v["unknown_full_loss_usd"],
        "unknown_full_loss_usd_per_day": v["unknown_full_loss_usd_per_day"],
        "known_contribution_usd": v["known_contribution_usd"],
        "lower_bound_usd": v["lower_bound_usd"],
        "note": (
            "UNKNOWN exits never cash: each is charged a full unit loss in the "
            "lower bound; known-fill statistics exclude them entirely"
        ),
    }
    v["monthly"] = monthly_accounting(metrics, trades)
    return v


COMPARE_EXACT = (
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
)
COMPARE_TOL = (
    "mean_net_known_fill",
    "mean_daily_lower_bound",
    "daily_se",
    "known_win_rate",
    "known_profit_factor",
    "worst_known_fill",
    "dollars_per_day",
    "dollars_per_year_252",
)


def compare_cell_to_reference(control: dict, view: dict) -> dict:
    """Field-by-field reproduction check of the frozen reference lane's repeat_h60 cell."""
    checks: dict[str, dict] = {}
    for f in COMPARE_EXACT:
        o, m = control.get(f), view.get(f)
        checks[f] = {"original": o, "reproduced": m, "match": o == m}
    for f in COMPARE_TOL:
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


def load_reference_lane() -> tuple[dict, dict | None]:
    """The frozen lane this study's all/all cell must reproduce exactly.

    Validation cells come from the lane's contract.json (frozen before its own late
    traversal) under ``validation.surface.repeat_h60``; late cells come from its
    results.json under ``confirmation_by_cost.repeat_h60``. Both maps are keyed by the
    rung string ("25".."200") and each value is one replay cell. Both are read-only
    inputs; a missing/malformed reference is a hard stop, never fabricated, never
    silently skipped.
    """
    contract_path = REFERENCE_LANE / "contract.json"
    if not contract_path.exists():
        raise SystemExit(
            f"[retained-timing] frozen reference lane contract missing: {contract_path}"
        )
    contract = json.loads(contract_path.read_text())
    val_rungs = ((contract.get("validation") or {}).get("surface") or {}).get("repeat_h60")
    if not isinstance(val_rungs, dict) or not val_rungs:
        raise SystemExit(
            "[retained-timing] frozen lane has no validation repeat_h60 rung cells"
        )
    results_path = REFERENCE_LANE / "results.json"
    late_rungs: dict | None = None
    if results_path.exists():
        payload = json.loads(results_path.read_text())
        candidate = (payload.get("confirmation_by_cost") or {}).get("repeat_h60")
        if isinstance(candidate, dict) and candidate:
            late_rungs = candidate
    return val_rungs, late_rungs


def compare_reference_block(
    rungs: dict, surface: dict, label: str, expected_keys: list[str]
) -> dict:
    """All-rung reproduction check of the frozen lane's repeat_h60 cells.

    EVERY rung of the ladder must match field-for-field; a missing rung in the stored
    lane is a hard stop (the guard never fabricates a reference and never skips one).
    """
    missing = [k for k in expected_keys if k not in rungs]
    if missing:
        raise SystemExit(
            f"[retained-timing] frozen lane {label} repeat_h60 cells missing rungs "
            f"{missing}; the reproduction guard refuses to fabricate or skip them"
        )
    witness = {}
    for k in expected_keys:
        check = compare_cell_to_reference(rungs[k], surface[k])
        if not check["all_match"]:
            bad = {f: v for f, v in check.items() if f != "all_match" and not v.get("match")}
            raise SystemExit(
                f"[retained-timing] all/all does not reproduce the frozen repeat_h60 "
                f"{label} cell at {k} bps: {bad}"
            )
        witness[k] = {
            "original_dollars_per_day": rungs[k].get("dollars_per_day"),
            "reproduced_dollars_per_day": surface[k].get("dollars_per_day"),
            "original_known_fills": rungs[k].get("known_fills"),
            "reproduced_known_fills": surface[k].get("known_fills"),
        }
    return {"all_match": True, "rungs_checked": list(expected_keys), "witness": witness}


# ----- selection: one pre-declared rung, 2023 validation only -----------------
def rank_cells(val: dict, cost_key: str) -> list[Cell]:
    """Rank the 12 fixed cells by validation dollars/day at the ONE pre-declared rung.

    dollars_per_day (known contribution less the separately-reported UNKNOWN full-loss
    charge, over full calendar days), then more known fills, then fewer attempts, then
    the cell key: a tie can never depend on dict/frame/disk order.
    """
    return sorted(
        CELLS,
        key=lambda c: (
            -float(val[c.key][cost_key]["dollars_per_day"]),
            -int(val[c.key][cost_key]["known_fills"]),
            int(val[c.key][cost_key]["attempts"]),
            c.key,
        ),
    )


def selection_section(
    chosen: Cell,
    ranked: list[Cell],
    val: dict,
    cost_key: str,
    tie_break_engaged: bool,
) -> dict:
    """The frozen 2023-validation selection object for the chosen grid cell."""
    cell = val[chosen.key][cost_key]
    return {
        "objective": (
            "2023 validation dollars per FULL CALENDAR day on the research reserve "
            "(= mean_daily_lower_bound x reserve, known contributions less the "
            "separately-reported full-loss charge on UNKNOWN exits)"
        ),
        "selection_cost_bps": FREEZE_COST_BPS,
        "cost_scenario_nature": (
            "each rung is a TOTAL modeled minute-proxy friction scenario on the proxy "
            "prices, NOT an actual broker fee: a primary US broker's regular schedule "
            "(zero commission, SEC 20.6 per $1M, CAT 0.000003 per side, daily cent "
            "rounding) is typically well under 1 bp on a $1,000 ticket, so the low "
            "rungs describe a LOW-FEE OPPORTUNITY set, not a fee claim; actual provider "
            "fees are sourced in the separate provider-fee ledger"
        ),
        "comparison_set_not_hurdle": True,
        "stress_vetoes": None,
        "power_floors": None,
        "median_or_tail_gates": None,
        "tie_break": [
            "higher dollars_per_day at the 25 bps rung",
            "more known fills",
            "fewer attempts",
            "lexicographic cell key",
        ],
        "tie_break_engaged_beyond_dollars_per_day": bool(tie_break_engaged),
        "tie_break_note": (
            "the full ranked witness (dollars/day, known fills, attempts per cell) is "
            "stored beside this object so the ordering is auditable"
        ),
        "predeclared_before_late_inspection": True,
        "status": STATUS,
        "cost_ladder_validation_bps": [float(c) for c in VAL_COSTS],
        "cost_ladder_late_bps": [float(c) for c in LATE_COSTS],
        "cost_scenarios_bps": [float(c) for c in COST_SCENARIOS_BPS],
        "historical_diagnostic_cost_bps": [HISTORICAL_COST_BPS],
        "ranked_dollars_per_day": {c.key: val[c.key][cost_key]["dollars_per_day"] for c in ranked},
        "ranked_known_fills": {c.key: val[c.key][cost_key]["known_fills"] for c in ranked},
        "ranked_attempts": {c.key: val[c.key][cost_key]["attempts"] for c in ranked},
        "chosen": {
            "cell": chosen.key,
            "t_band": chosen.t_band,
            "t_window": T_BAND_TEXT[chosen.t_band],
            "age_band": chosen.age_band,
            "age_window": AGE_BAND_TEXT[chosen.age_band],
            "description": chosen.description,
            "frozen_reference": chosen.frozen_reference,
        },
        "chosen_mean_daily_lower_bound": cell["mean_daily_lower_bound"],
        "chosen_dollars_per_day": cell["dollars_per_day"],
        "chosen_known_fills": cell["known_fills"],
        "chosen_traded_days": cell["traded_days"],
        "chosen_unknown_fills": cell["unknown_fills"],
        "chosen_attempts": cell["attempts"],
        "caveat": (
            "the mechanism profile was DESCRIPTIVE: 12 cells were scored on 2023 "
            "validation, so multiple comparisons flatter small samples and the chosen "
            "cell is a 2023-validation selection only, NOT a validated sub-strategy; "
            "the surface stays DISCOVERY-NOT-VALIDATED"
        ),
    }


# ----- contract core (frozen before any validation outcome) --------------------
def contract_core(model_path: Path, model_sha: str, producer_sha: str) -> dict:
    return {
        "study": "alpha_retained_timing",
        "label": (
            "decision-time x admission-age surface of the retained repeat_h60 "
            "mechanism (same stored h60 model, same 0.03 threshold, same repeat "
            "cadence; only the pre-declared 2-axis grid filter varies)"
        ),
        "status": STATUS,
        "question": (
            "Is the retained edge concentrated in particular decision times "
            "(signal minute t) or in fresh-vs-aged admissions (t - admit_t)?"
        ),
        "grid": {
            "axes": {
                "decision_time": {
                    "field": "t",
                    "clock": "panel minute clock, minutes since midnight ET",
                    "bands": {b: T_BAND_TEXT[b] for b in T_BANDS},
                },
                "admission_age": {
                    "field": "t - admit_t (minutes; both strictly causal)",
                    "bands": {b: AGE_BAND_TEXT[b] for b in AGE_BANDS},
                },
            },
            "cells": [
                {
                    "key": c.key,
                    "t_band": c.t_band,
                    "age_band": c.age_band,
                    "description": c.description,
                    "frozen_reference": c.frozen_reference,
                }
                for c in CELLS
            ],
            "frozen_reference_cell": FREEZE_REFERENCE_KEY,
            "other_filters": "none",
            "note": (
                "the panel minute clock is minutes since midnight ET: t=630 is the "
                "10:30 ET minute-open and t=780 is 13:00 ET; the numeric cut points "
                "are the specification"
            ),
        },
        "cost_ladder": {
            "scenarios_bps": [float(c) for c in COST_SCENARIOS_BPS],
            "validation_ladder_bps": [float(c) for c in VAL_COSTS],
            "late_ladder_bps": [float(c) for c in LATE_COSTS],
            "historical_diagnostic_bps": [HISTORICAL_COST_BPS],
            "comparison_set_not_hurdle": True,
            "nature": (
                "each rung is a TOTAL modeled round-trip minute-proxy friction "
                "scenario, not a broker fee; no rung closes a candidate"
            ),
        },
        "selection_rule": {
            "rung_bps": FREEZE_COST_BPS,
            "block": "2023 validation only",
            "objective": (
                "known-contribution dollars per FULL CALENDAR day "
                "(dollars_per_day: mean daily lower bound x reserve; unknown exits "
                "charged full loss, never cash, never blended)"
            ),
            "tie_break": [
                "more known fills",
                "fewer attempts",
                "lexicographic cell key",
            ],
            "frozen_outcome_file": "frozen_selection.json",
            "frozen_before_late_inspection": True,
        },
        "model": {
            "refit": False,
            "hpo": False,
            "feature_changes": False,
            "horizons_scored": [MODEL_HORIZON],
            "fit_block_2021_2022_scored": False,
            "fit_block_note": (
                "the 2021-2022 fit block is never replayed (weights are frozen), so "
                "it is not scored; the single prediction pass is shared by all 12 cells"
            ),
            "stored_path": str(model_path),
            "stored_sha256": model_sha,
            "feature_order": FEATURES_ALL,
        },
        "replay": {
            "engine": "alpha_retained_timing.replay_cell (shared funded-reserve conventions)",
            "reserve_usd": RESERVE_USD,
            "max_positions": MAX_POSITIONS,
            "order_budget": ORDER_BUDGET,
            "leverage": False,
            "fees_both_legs": True,
            "one_position_per_symbol": True,
            "same_clock_exits_before_buys": True,
            "cooldown_rule": (
                "no reopening until the ACTUAL exit minute + 15 flat minutes; a "
                "pending/UNKNOWN exit keeps its slot and blocks its ticker for the "
                "rest of the session"
            ),
            "attempt_cap_per_ticker_per_day": MAX_ATTEMPTS,
            "unknown_exit_rule": "full-unit lower-bound charge at the day's end, never cash",
            "unfilled_rule": (
                "reserve and release the ticket one minute later, fee-free, and "
                "still count the attempt (no exit => no cooldown)"
            ),
            "all_calendar_days_retained": True,
            "daily_reset_is_not_self_financing_cagr": True,
            "carry_equity_check": "additive fixed-ticket equity, no compounding",
            "fills_are_minute_open_proxies": True,
        },
        "labels": {
            "panel_horizons": [60],
            "recompute": "none: the h60 exit label is read from the panel unchanged",
        },
        "schema": {"signal_columns": SIGNAL_COLUMNS, "schema_signature": SCHEMA_SIGNATURE},
        "resume": {
            "per_day_atomic_parts": str(OUTPUT / "collect_parts"),
            "resume_hash_inputs": [
                "producer sha256",
                "contract-core sha256 (grid, ladders, rules, model, replay "
                "conventions, schema; the post-validation selection block is "
                "excluded so frozen parts survive the freeze rewrite)",
                "model sha256",
                "day",
                "schema signature",
            ],
            "progress_every_days": PROGRESS_EVERY,
        },
        "late_block": {
            "window": "2025-02-01..2026-05-31",
            "days": EXPECTED_DAYS[CONF_PERIOD],
            "previously_explored": True,
            "disclosure": (
                "the late window was already explored before this study, so its "
                "outcomes are DISCOVERY-NOT-VALIDATED - NOT pristine and NOT "
                "previously unknown; traversal is for transparency only and the "
                "frozen choice cannot change"
            ),
        },
        "producer_sha256": producer_sha,
    }


# ----- main pipeline -----------------------------------------------------------
def _fmt(value, spec="+.6f"):
    return "n/a" if value is None else format(value, spec)


def print_cell(tag: str, view: dict) -> None:
    b = view["bootstrap_daily"]
    ci = f"[{_fmt(b.get('ci95_lo'))},{_fmt(b.get('ci95_hi'))}]" if "ci95_lo" in b else "n/a"
    print(
        f"[{tag}] {view['cell']['key']:<16} "
        f"mdlb={_fmt(view['mean_daily_lower_bound'])} "
        f"$/day={_fmt(view['dollars_per_day'], '+.2f')} se={_fmt(view['daily_se'])} "
        f"boot95={ci} p>0={b.get('p_gt_zero')} "
        f"known={view['known_fills']} unk={view['unknown_fills']} "
        f"unfilled={view['unfilled_attempts']} fills={view['fills']} "
        f"traded_days={view['traded_days']}/{view['days']} "
        f"attempts={view['attempts']} signals={view['n_signals']} "
        f"skips={view['cash_or_slot_skips']}/{view['skips_total']}",
        flush=True,
    )


def analysis_blocks(days: list[str] | None) -> dict[str, list[str]]:
    if days is not None:
        refused = [d for d in days if not allowed(d)]
        if refused:
            raise ValueError(f"protected/out-of-scope day refused: {sorted(refused)}")
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
                    f"[retained-timing] requires {expected} {p} days, got {len(blocks.get(p, []))}"
                )
    if not blocks:
        raise SystemExit("[retained-timing] no analysis days selected")
    return blocks


def decision_text(chosen: Cell, val: dict, late: dict | None, selection: dict) -> str:
    c25 = selection["chosen_dollars_per_day"]
    cell25 = selection["chosen"]
    ref25 = val[FREEZE_REFERENCE_KEY]["25"]
    txt = (
        f"{STATUS}: frozen {FREEZE_COST_BPS:.0f}bps 2023-validation choice "
        f"{cell25['cell']} ({cell25['description']}): "
        f"{_fmt(c25, '+.2f')} $/full calendar day = "
        f"{_fmt(selection['chosen_mean_daily_lower_bound'])}/day on a "
        f"${int(RESERVE_USD)} reserve, {selection['chosen_known_fills']} known fills "
        f"on {selection['chosen_traded_days']} traded days of the 2023 calendar; "
        f"frozen reference {FREEZE_REFERENCE_KEY} repeats the stored repeat_h60 lane "
        f"({_fmt(ref25['dollars_per_day'], '+.2f')} $/day @25, "
        f"{ref25['known_fills']} known fills); tie-break "
        f"{selection['tie_break']} (engaged beyond dollars/day: "
        f"{selection['tie_break_engaged_beyond_dollars_per_day']}); "
        f"mechanism profile was DESCRIPTIVE, selection is on 2023 validation only, "
        f"the 25..150bps ladder is a comparison set and not a hurdle, every fill is a "
        f"minute-open proxy, and dollars/year is a simple 252-session annualization "
        f"(NOT a CAGR)"
    )
    if late is None:
        return txt + "; late block not traversed (--skip-late)"
    c100 = late[chosen.key]["100"]
    txt += (
        f"; late 332-day traversal for transparency (window previously explored, NOT "
        f"pristine): {chosen.key} {_fmt(c100['dollars_per_day'], '+.2f')} $/day @100 "
        f"and {_fmt(late[chosen.key]['25']['dollars_per_day'], '+.2f')} $/day @25 on "
        f"{c100['known_fills']} known fills over {c100['traded_days']} traded days; "
        f"choice immutable"
    )
    return txt


def run(args: argparse.Namespace) -> None:
    t0 = time.time()
    out = args.out or OUTPUT
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")

    # 1) ONE stored model, loaded and scored (never fitted).
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
            "[retained-timing] no 2023 validation days selected; the dollars/day "
            "selection objective requires the validation block"
        )
    collect_days = sorted(set(val_days) | set(conf_days))
    print(
        f"[blocks] validation={len(val_days)} days, late={len(conf_days)} days, "
        f"collect={len(collect_days)} days",
        flush=True,
    )

    # 2) the contract core - the complete fixed 12-cell grid + cost ladder - is frozen
    #    BEFORE any validation outcome exists. Its sha feeds the resume hashes, so the
    #    per-day parts stay valid across the later freeze rewrite.
    core = contract_core(model_path, model_sha, producer_sha)
    contract_core_sha = hashlib.sha256(
        json.dumps(core, sort_keys=True, default=str).encode()
    ).hexdigest()
    hashes = {
        "producer_sha256": producer_sha,
        "contract_core_sha256": contract_core_sha,
        "model_sha256": model_sha,
    }
    pre_validation_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    contract = {
        **core,
        "frozen_before_late_inspection": True,
        "written_before_any_validation_outcome_at": pre_validation_at,
        "contract_core_sha256": contract_core_sha,
        "selection_outcome_file": "frozen_selection.json",
        "provenance": {
            "panel_root": str(PANEL_ROOT),
            "panel_contract_sha256": (
                sha256_file(PANEL_ROOT / "contract.json")
                if (PANEL_ROOT / "contract.json").exists()
                else None
            ),
            "reference_lane": str(REFERENCE_LANE),
            "frozen_reference_note": (
                "the all/all cell must reproduce the stored repeat_h60 lane exactly "
                "at every rung on both blocks; the check is hard-fail at runtime"
            ),
            "analysis_days": len(collect_days),
            "validation_days": len(val_days),
            "late_days": len(conf_days),
        },
    }
    (out / "contract.json").write_text(json.dumps(contract, indent=2, default=str) + "\n")
    contract_pre_validation_sha = sha256_file(out / "contract.json")
    print(
        f"[freeze] contract (12-cell grid + cost ladder + selection rule) -> "
        f"{out / 'contract.json'} before any validation outcome",
        flush=True,
    )

    # 3) one scoring pass over the analysis corpus; per-day atomic parts, resumable.
    signals, coverage = collect_signals(
        bundle["lgbm"], collect_days, out, hashes, resume=args.resume
    )
    coverage["contract_pre_validation_sha256"] = contract_pre_validation_sha
    signals_file = out / "signals.parquet"
    signals.write_parquet(signals_file)
    print(
        f"[collect] {signals.height} thr-clearing states on {len(collect_days)} days "
        f"(cached {coverage['days_cached']}, collected {coverage['days_collected']})",
        flush=True,
    )

    # 4) 2023 VALIDATION ONLY: the 12 fixed cells x the cost ladder, no other gate.
    val: dict[str, dict[str, dict]] = {}
    val_daily: dict[str, np.ndarray] = {}
    for cell in CELLS:
        sig = cell_signals(signals, cell, val_days)
        n_val = int(sig.height)
        val[cell.key] = {}
        for cost in VAL_COSTS:
            metrics, trades = replay_cell(sig, val_days, cost)
            val[cell.key][str(int(cost))] = cell_view(metrics, trades, cell, n_val)
            if cost == FREEZE_COST_BPS:
                val_daily[cell.key] = np.array([d["lower_bound_return"] for d in metrics["daily"]])
        print_cell(f"val@{int(FREEZE_COST_BPS)}", val[cell.key][str(int(FREEZE_COST_BPS))])
    np.savez(
        out / "daily_validation.npz",
        **{k.replace("|", "__"): v for k, v in val_daily.items()},
    )

    # 4b) the all/all cell must reproduce the frozen repeat_h60 lane exactly, at EVERY
    #     rung, BEFORE anything is frozen or the late block is touched.
    ref_val, ref_late = load_reference_lane()
    val_rung_keys = [str(int(c)) for c in VAL_COSTS]
    repro_val = compare_reference_block(
        ref_val, val[FREEZE_REFERENCE_KEY], "validation", val_rung_keys
    )
    if not repro_val["all_match"]:
        raise SystemExit("[retained-timing] all/all does not reproduce the frozen lane")
    print(
        f"[control] {FREEZE_REFERENCE_KEY} reproduces learned_sparse_daily repeat_h60 "
        f"exactly at every rung {val_rung_keys} on validation (@100: "
        f"{val[FREEZE_REFERENCE_KEY]['100']['known_fills']} known fills, "
        f"{val[FREEZE_REFERENCE_KEY]['100']['mean_daily_lower_bound']:+.6f}/day, "
        f"{val[FREEZE_REFERENCE_KEY]['100']['dollars_per_day']:+.6f} $/day)",
        flush=True,
    )

    # 5) freeze the chosen cell on 2023 validation only, BEFORE any late outcome.
    key25 = str(int(FREEZE_COST_BPS))
    ranked = rank_cells(val, key25)
    chosen = ranked[0]
    dpds = [float(val[c.key][key25]["dollars_per_day"]) for c in ranked]
    tie_break_engaged = len(set(dpds)) != len(dpds)
    selection = selection_section(chosen, ranked, val, key25, tie_break_engaged)
    selection["ranked_order"] = [c.key for c in ranked]
    (out / "frozen_selection.json").write_text(json.dumps(selection, indent=2, default=str) + "\n")
    contract = {
        **core,
        "frozen_before_late_inspection": True,
        "written_before_any_validation_outcome_at": pre_validation_at,
        "selection_frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "contract_core_sha256": contract_core_sha,
        "contract_pre_validation_sha256": contract_pre_validation_sha,
        "selection": selection,
        "provenance": {
            "panel_root": str(PANEL_ROOT),
            "panel_contract_sha256": (
                sha256_file(PANEL_ROOT / "contract.json")
                if (PANEL_ROOT / "contract.json").exists()
                else None
            ),
            "reference_lane": str(REFERENCE_LANE),
            "frozen_reference_note": (
                "the all/all cell must reproduce the stored repeat_h60 lane exactly "
                "at every rung on both blocks; the check is hard-fail at runtime"
            ),
            "resume_hash_contract_component": (
                "contract_core_sha256 (grid, ladders, rules, model, replay "
                "conventions, schema); the post-validation selection block is "
                "excluded so the frozen per-day parts stay valid across this rewrite"
            ),
            "analysis_days": len(collect_days),
            "validation_days": len(val_days),
            "late_days": len(conf_days),
        },
    }
    (out / "contract.json").write_text(json.dumps(contract, indent=2, default=str) + "\n")
    print(
        f"[freeze] chosen cell {chosen.key} by 2023 validation dollars/day at "
        f"{int(FREEZE_COST_BPS)}bps ({_fmt(selection['chosen_dollars_per_day'], '+.2f')} $/day, "
        f"{selection['chosen_known_fills']} known fills, "
        f"{selection['chosen_attempts']} attempts); ranked witness -> "
        f"{out / 'frozen_selection.json'}",
        flush=True,
    )

    results = {
        "contract": contract,
        "selection": selection,
        "validation": val,
        "control_reproduction": {"validation": repro_val},
        "coverage": coverage,
        "status": STATUS,
        "artifacts": {
            "contract": str(out / "contract.json"),
            "frozen_selection": str(out / "frozen_selection.json"),
            "signals": str(signals_file),
            "collect_parts": str(out / "collect_parts"),
            "producer_snapshot": str(out / "producer_snapshot.py"),
        },
        "runtime_s": round(time.time() - t0, 1),
    }

    if args.skip_late or not conf_days:
        results["late"] = None
        results["decision"] = decision_text(chosen, val, None, selection)
        (out / "results.json").write_text(json.dumps(results, indent=2, default=str) + "\n")
        print(f"[decision] {results['decision']}", flush=True)
        print(f"[done] results -> {out / 'results.json'} ({results['runtime_s']}s)", flush=True)
        return

    # 6) late block, for transparency only: every cell at the full ladder. The frozen
    #    choice is already on disk and cannot change here.
    late: dict[str, dict[str, dict]] = {}
    late_daily: dict[str, np.ndarray] = {}
    for cell in CELLS:
        sig = cell_signals(signals, cell, conf_days)
        n_late = int(sig.height)
        late[cell.key] = {}
        for cost in LATE_COSTS:
            metrics, trades = replay_cell(sig, conf_days, cost)
            late[cell.key][str(int(cost))] = cell_view(metrics, trades, cell, n_late)
            if cost == FREEZE_COST_BPS:
                late_daily[cell.key] = np.array([d["lower_bound_return"] for d in metrics["daily"]])
        print_cell("late", late[cell.key][str(int(FREEZE_COST_BPS))])
    np.savez(
        out / "daily_confirmation.npz",
        **{k.replace("|", "__"): v for k, v in late_daily.items()},
    )
    repro_late = None
    if ref_late is not None:
        late_rung_keys = [str(int(c)) for c in LATE_COSTS]
        repro_late = compare_reference_block(
            ref_late, late[FREEZE_REFERENCE_KEY], "late block", late_rung_keys
        )
        print(
            f"[control] {FREEZE_REFERENCE_KEY} reproduces learned_sparse_daily "
            f"repeat_h60 exactly at every rung {late_rung_keys} on the late block "
            f"(@100: {late[FREEZE_REFERENCE_KEY]['100']['known_fills']} known fills, "
            f"{late[FREEZE_REFERENCE_KEY]['100']['mean_daily_lower_bound']:+.6f}/day, "
            f"{late[FREEZE_REFERENCE_KEY]['100']['dollars_per_day']:+.6f} $/day)",
            flush=True,
        )
    results["late"] = late
    results["control_reproduction"] = {
        "validation": repro_val,
        "late": repro_late,
        "late_checked": repro_late is not None,
    }
    results["decision"] = decision_text(chosen, val, late, selection)
    results["runtime_s"] = round(time.time() - t0, 1)
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
        help="resume collection from the per-day atomic parts already on disk",
    )
    p.add_argument(
        "--skip-late",
        action="store_true",
        help="stop after the frozen selection (2023 validation only)",
    )
    p.add_argument(
        "--days",
        nargs="+",
        default=None,
        help="restrict the analysis corpus to these days (debug)",
    )
    run(p.parse_args())


if __name__ == "__main__":
    main()
