#!/usr/bin/env python3
"""Book-shape grid on the retained sparse learned selector (no refit, no re-selection).

The retained lead is ONE immutable stored model (``learned/models/payoff_h60.joblib``,
fit 2021-02..2022-12, 26 causal features) read at the frozen chosen signal of the
retained program: score threshold 0.03, the h60 minute-open exit label, the REPEAT
cadence (flat 15-minute cooldown after the ACTUAL exit, at most 3 attempts per ticker
per session, one position per ticker). That signal is NOT re-selected here: every cell
of this study reads the same score, the same bar, the same exit and the same cadence.
The ONLY thing that varies is the SHAPE of the research book - concurrent slots x
ticket - crossed with a fixed cost ladder. 8 book cells x 7 cost rungs = 56 fixed cells,
pre-declared in ``contract.json`` before any validation outcome is computed.

HONEST BASIS (stated prominently, and it bounds every number in this study):
every fill in this study is the SAME minute-open PROXY the retained program uses.
The entry is the panel's own causal next-minute open of the state's clock and the exit
is the panel's stored first-actual-minute-open label at min(entry+60, session_end).
There are NO quotes here: no NBBO touch, no depth, no spread, no as-of audit, no
exchange-fill model, no capacity claim. A positive cell is a statement about the proxy
basis only, and the parent's as-of quote audit remains the separate execution question.

Book and funding conventions (identical across all 56 cells except the two shape axes):
* reserve = slots x ticket, reset at the start of every session (daily-reset research
  normalization, NOT a self-financing CAGR and NOT a compounding equity curve).
* quantity = floor(ticket / entry_open) WHOLE SHARES at the observed causal next-minute
  open of the state. The quantity is fixed from that bar and is never resized later, so
  the deployed notional is at most the ticket. A state whose floor quantity is 0 is a
  KNOWN NO-ORDER cash skip: no order is sent, no attempt is consumed, no cooldown is
  anchored, no cash moves - it is never a fill and never an UNKNOWN.
* funded cash declines by the deployed notional on entry and is restored at the ACTUAL
  exit minute at the SAME notional (cost-invariant restoration), so all seven rungs of a
  cell share ONE identical fill set and the ladder is a pure cost translation, not a
  different trading path. An unfilled entry minute (the panel's ``unfilled_expired``
  state) reserves the full ticket for one minute and releases it fee-free.
* ALL eligible intents of one clock are funded in descending model score up to the
  remaining cash and slots BEFORE any fill outcome is known: no substitution, ever. A
  same-clock candidate that finds no cash or no slot stays unfunded even when a funded
  neighbour of the same clock later turns out to be unfilled.
* an UNKNOWN exit keeps BOTH its cash and its slot reserved to the session end (never
  released early, never converted to cash, never dropped).
* one position per ticker, at most ``slots`` concurrent positions, same-clock exits
  precede new buys, no leverage, no re-entry before the ACTUAL exit minute + 15 minutes.

Skip precedence inside a clock (deterministic, recorded per cell): NO-ORDER (the ticket
cannot buy one share) -> ticker busy -> cooldown -> attempt cap -> no free slot -> not
enough cash.

Honest unknown / cash semantics: the reported objective is the PARTIAL KNOWN
contribution (UNKNOWN executions are excluded from the numerator and reported beside
it); the separately labeled FULL-LOSS LOWER BOUND charges every UNKNOWN fill its whole
notional. Neither is ever presented as the other, an UNKNOWN is never booked as cash,
and an UNKNOWN loss is never used as the expectation.

Cost ladder: 25/50/75/100/125/150 bps are TOTAL modeled round-trip friction scenarios on
the proxy prices - a LOW-FEE OPPORTUNITY comparison set, never a hurdle, never a fee
claim (a primary US broker's regular schedule is typically well under 1 bp on a
$100-$1,000 ticket, so the actual provider fee is a separate question). 200 bps is kept
only as the historical diagnostic the stored late ladder already carried.

Selection (pre-declared, on 2023 validation only, frozen to
``selection_freeze.json`` BEFORE any late file is read): the cell with the largest 2023
validation KNOWN contribution dollars per calendar day, ties broken by more known fills,
then a lower reserve, then the cell key. Two equally pre-declared rungs are frozen side
by side - the 25 bps low-fee opportunity comparison (primary) and the 100 bps
historical rung the retained program used - and the full ranking at all seven rungs is
stored, so no single rung acts as a veto. The 2025-02..2026-05 block is then traversed
for transparency only (that window was previously explored, so it is NOT pristine and
NOT previously unknown) and the frozen choice is never re-selected on it.

Annualization: ``dollars_per_year_252`` is a SIMPLE 252-session extrapolation of the
daily-reset dollars/day figure. It is a research normalization on a fixed reserve that
restarts every session, explicitly NOT a CAGR and NOT a self-financing return. The same
figure is also normalized per $1,000 of each cell's own reserve, because the eight cells
carry reserves from $1,000 to $5,000 and a raw dollar ranking across them is a capital
ranking, not an edge ranking.

Novelty vs the frozen reference (factory/scripts/alpha_sparse_daily.py, the retained
lane): that study held a $3,000 book of 3 x $1,000 tickets and varied the ADMISSION and
EXIT cadence (five views, thresholds fixed, h15/h30/h60 exits, first-attempt baselines,
per-added-leg increments). This study holds the cadence and the signal FIXED and varies
the BOOK SHAPE, adds whole-share quantity flooring at the observed entry bar (so a
state can be a known no-order), makes the funded cash cost-invariant so the cost ladder
is a pure translation, and reports per-cell capital-normalized economics beside the raw
dollars. It is a different axis on the same substrate, not a re-run of that lane.

Usage:
  uv run --no-sync python factory/scripts/alpha_retained_book_shape.py
      # outputs -> ~/alpha-data/open-search-v1/retained_book_shape
  uv run --no-sync python factory/scripts/alpha_retained_book_shape.py --resume
      # resume from the per-day parts already on disk
  uv run --no-sync python factory/scripts/alpha_retained_book_shape.py --skip-late
      # validation + frozen selection only (no late file is read)
  uv run --no-sync python factory/scripts/alpha_retained_book_shape.py --out <dir>
      # relocate the lane root
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import os
import shutil
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import polars as pl  # noqa: E402
from alpha_open_learned import (  # noqa: E402
    CONF_PERIOD,
    FEATURES_ALL,
    PANEL_ROOT,
    VAL_PERIOD,
    bootstrap_daily,
    feature_matrix,
    sha256_file,
)
from alpha_open_panel import allowed  # noqa: E402
from alpha_open_sim import CONTEXT_FEATURES, causal_liquidity, period  # noqa: E402

# ----- fixed configuration (no refit, no HPO, no threshold/horizon re-selection) ----
STUDY = "alpha_retained_book_shape"
STATUS = "DISCOVERY-NOT-VALIDATED"
VERSION = 2
MODEL_HORIZON = 60  # the one stored head that is ever loaded
THRESHOLD = 0.03  # the frozen retained admission bar; NOT re-selected here
COOLDOWN_MIN = 15  # flat minutes after the ACTUAL exit (the frozen repeat cadence)
MAX_ATTEMPTS = 3  # attempts per ticker per session (the frozen repeat cadence)
MODEL_DIR = PANEL_ROOT / "learned" / "models"
PANEL_DAYS = PANEL_ROOT / "days"
PANEL_CONTRACT = PANEL_ROOT / "contract.json"

# ----- the pre-declared book grid: (concurrent slots, ticket $) --------------------
# Fixed in code before any outcome is read. (3, 1000) is the retained program's own
# book and is kept inside the grid as the direct reference cell.
BOOK_CELLS = (
    (3, 1000.0),
    (3, 500.0),
    (3, 250.0),
    (6, 500.0),
    (6, 250.0),
    (10, 250.0),
    (10, 100.0),
    (20, 100.0),
)
REFERENCE_CELL = (3, 1000.0)

# ----- the fixed cost ladder (comparison set, never a hurdle) ----------------------
COST_SCENARIOS_BPS = (25.0, 50.0, 75.0, 100.0, 125.0, 150.0)
HISTORICAL_COST_BPS = 200.0  # historical diagnostic only (the stored late ladder)
CELL_COSTS_BPS = COST_SCENARIOS_BPS + (HISTORICAL_COST_BPS,)
COST_KEYS = tuple(str(int(c)) for c in CELL_COSTS_BPS)
# Two equally pre-declared selection rungs, frozen side by side; neither is a hurdle.
SELECT_COST = 25.0  # PRIMARY: LOW-fee opportunity comparison (a total modeled
# minute-proxy friction scenario, NOT a broker fee)
HISTORICAL_SELECT_COST = 100.0  # HISTORICAL: the rung the retained program used
TRADING_DAYS_PER_YEAR = 252  # simple session-count convention, NOT a CAGR
BOOT_N = 1000
BOOT_SEED = 1000
STATES_SCHEMA = 2  # 2: h60 signal states with the frozen repeat-cadence inputs
REPLAY_SCHEMA = 2  # 2: cost-invariant funded cash, whole-share quantity, 8 book cells
OUTPUT = PANEL_ROOT / "retained_book_shape"
EXPECTED_DAYS = {VAL_PERIOD: 250, CONF_PERIOD: 332}
PROTECTED_UNREAD = ("2024", "2025-01", "2026-06", "2026-07", "2026-08")
# The frozen retained lane whose (3,1000) result is the direct comparison object.
FROZEN_LANE = PANEL_ROOT / "learned_sparse_daily"
FROZEN_VIEW = "repeat_h60"


# ----- shared helpers -------------------------------------------------------------
def producer_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _digest_bytes(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _default(obj):
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (set, tuple)):
        return list(obj)
    raise TypeError(f"not serializable: {type(obj)}")


def _resume_hash(producer: str, contract_sha: str, model_sha: str, day: str, schema: int) -> str:
    """Atomic-checkpoint identity: producer + contract + model + day + schema."""
    return _digest_bytes(
        {
            "day": day,
            "producer": producer,
            "contract": contract_sha,
            "model": model_sha,
            "schema": int(schema),
        }
    )


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=1, default=_default) + "\n")
    os.replace(tmp, path)


# ----- the book cells -------------------------------------------------------------
@dataclass(frozen=True)
class Cell:
    """One pre-declared book shape. Same signal, same cadence; only slots x ticket."""

    slots: int
    ticket: float
    control: bool = False

    @property
    def key(self) -> str:
        return f"s{self.slots}_t{int(self.ticket)}"

    @property
    def label(self) -> str:
        return f"{self.slots}x${int(self.ticket)}"

    @property
    def reserve(self) -> float:
        return float(self.slots) * float(self.ticket)

    @property
    def reserve_per_1000(self) -> float:
        return 1000.0 / self.reserve


CELLS = tuple(Cell(s, t, control=(s, t) == REFERENCE_CELL) for s, t in BOOK_CELLS)
CELLS_BY_KEY = {c.key: c for c in CELLS}
TICKETS = tuple(sorted({c.ticket for c in CELLS}))
N_CELLS = len(CELLS)
N_CELL_RUNS = N_CELLS * len(CELL_COSTS_BPS)
assert len({c.key for c in CELLS}) == N_CELLS, "duplicate book cell"
assert len(set(CELL_COSTS_BPS)) == len(CELL_COSTS_BPS), "duplicate cost rung"
assert N_CELL_RUNS == 56, f"predeclared grid is {N_CELL_RUNS} cells, expected 56"


# ----- the pre-declared contract (static: no outcome may enter it) ----------------
CONTRACT = {
    "version": VERSION,
    "study": STUDY,
    "status": STATUS,
    "hypothesis": (
        "on the ONE immutable stored h60 payoff model at the frozen retained signal "
        "(threshold 0.03, h60 minute-open exit, repeat cadence), the SHAPE of the "
        "research book - concurrent slots x ticket - moves dollars per calendar day, "
        "because it changes how many same-clock intents are funded and how many whole "
        "shares a ticket buys; and no rung of the cost ladder is a hurdle"
    ),
    "signal_frozen_not_selected": {
        "model_horizon": MODEL_HORIZON,
        "threshold": THRESHOLD,
        "exit": f"panel gross_{MODEL_HORIZON} minute-open label",
        "cadence": (
            f"repeat: flat {COOLDOWN_MIN}-minute cooldown after the ACTUAL exit, "
            f"max {MAX_ATTEMPTS} attempts per ticker per session, one position per ticker"
        ),
        "re_selected_here": False,
        "note": (
            "the signal is the frozen chosen signal of the retained program "
            "(learned_sparse_daily repeat_h60); this study changes only the book shape "
            "and the reported cost rung"
        ),
    },
    "protected_unread": list(PROTECTED_UNREAD),
    "model": {
        "refit": False,
        "hpo": False,
        "feature_changes": False,
        "new_features": False,
        "ticker_or_date_features": False,
        "loaded_from": str(MODEL_DIR),
        "head": MODEL_HORIZON,
        "feature_order": list(FEATURES_ALL),
        "feature_matrix_helper": "alpha_open_learned.feature_matrix (Float64, null-filled)",
        "fit_block": "2021-02..2022-12 (never replayed, never re-scored as outcomes)",
        "target": "panel minute-open gross payoff label gross_60",
        "clip_fit": [-0.5, 2.0],
    },
    "execution_basis": {
        "fill_basis": (
            "MINUTE-OPEN PROXY, identical to the retained program: entry at the panel's "
            "causal next-minute open of the state's clock, exit at the stored first "
            "actual minute open at/after min(entry+60, session_end)"
        ),
        "is_quote_based": False,
        "no_nbbo_touch": True,
        "no_depth_or_spread_model": True,
        "no_asof_quote_audit": True,
        "not_an_exchange_fill_model": True,
        "capacity_claim": False,
        "prominent_disclosure": (
            "every cell below is a proxy-basis result; it is NOT an executable claim and "
            "it is not a quote-verified fill"
        ),
    },
    "book_grid": {
        "cells": [
            {
                "key": c.key,
                "label": c.label,
                "slots": c.slots,
                "ticket_usd": c.ticket,
                "reserve_usd": c.reserve,
                "control": c.control,
            }
            for c in CELLS
        ],
        "reserve_rule": "reserve = slots x ticket, reset at the start of every session",
        "cost_ladder_bps": [float(c) for c in CELL_COSTS_BPS],
        "cost_scenarios_bps": [float(c) for c in COST_SCENARIOS_BPS],
        "historical_diagnostic_cost_bps": [HISTORICAL_COST_BPS],
        "n_book_cells": N_CELLS,
        "n_cost_rungs": len(CELL_COSTS_BPS),
        "n_cell_runs": N_CELL_RUNS,
        "cost_ladder_is_comparison_not_hurdle": True,
        "fees_both_legs": True,
        "net_formula": "net = (1 + gross) * (1 - bps/20000) / (1 + bps/20000) - 1",
    },
    "quantity": {
        "rule": "q = floor(ticket / entry_open) whole shares at the OBSERVED entry bar",
        "fixed_forever": True,
        "never_resized": True,
        "notional_rule": "deployed notional = q * entry_open <= ticket",
        "no_order_rule": (
            "q == 0 is a KNOWN no-order cash skip: no order, no attempt, no cooldown, no "
            "cash movement; never a fill and never an UNKNOWN"
        ),
        "unfilled_rule": (
            "a panel unfilled_expired state has no bar, so it reserves the full ticket "
            "for one minute and releases it fee-free; the attempt still counts"
        ),
    },
    "replay": {
        "engine": "alpha_retained_book_shape.replay_day (funded-reserve conventions)",
        "leverage": False,
        "one_position_per_symbol": True,
        "same_clock_exits_before_buys": True,
        "cooldown_rule": (
            f"no reopening before the ACTUAL exit minute + {COOLDOWN_MIN} minutes; a "
            "pending/UNKNOWN exit keeps its slot and blocks its ticker for the session"
        ),
        "unknown_exit_rule": (
            "keeps BOTH cash and slot reserved to the session end; never released early, "
            "never booked as cash, never dropped"
        ),
        "same_clock_funding_rule": (
            "ALL eligible intents of one clock are funded in descending model score up to "
            "the remaining cash and slots BEFORE any fill outcome is known; no "
            "substitution at any point"
        ),
        "cash_restore_rule": (
            "cash is restored at the ACTUAL exit minute at the SAME notional (cost "
            "invariant), so every rung of a cell shares ONE identical fill set and the "
            "cost ladder is a pure translation"
        ),
        "skip_precedence": [
            "no_order_min_capital",
            "ticker_busy",
            "cooldown",
            "max_attempts",
            "slot_full",
            "cash_short",
        ],
        "all_calendar_days_retained": True,
        "daily_reset_is_not_self_financing_cagr": True,
    },
    "unknown_and_cash_semantics": {
        "objective_basis": (
            "PARTIAL KNOWN contribution: UNKNOWN executions are excluded from the "
            "numerator and reported beside it"
        ),
        "full_loss_lower_bound": (
            "separately labeled: known contribution minus the whole notional of every "
            "UNKNOWN fill; a lower bound, never the expectation"
        ),
        "never_unknown_as_cash": True,
        "never_unknown_loss_as_ev": True,
    },
    "selection": {
        "block": f"{VAL_PERIOD} (2023, all 250 allowed panel days)",
        "objective": "validation KNOWN contribution dollars per calendar day",
        "primary_rung_bps": SELECT_COST,
        "historical_rung_bps": HISTORICAL_SELECT_COST,
        "tie_break": [
            "higher known contribution $ per calendar day",
            "more known fills",
            "lower reserve",
            "cell key (lexicographic)",
        ],
        "power_floors": None,
        "median_or_tail_gates": None,
        "cost_or_count_kill": None,
        "frozen_before_late_file_access": True,
        "late_block_is_transparency_only": True,
        "late_block_note": (
            "2025-02..2026-05 was already explored before this study, so it is NOT "
            "pristine and NOT previously unknown; the frozen choice is never re-selected"
        ),
    },
    "reporting": {
        "per_cell_fields": [
            "signals",
            "funded_intents",
            "fills",
            "known_fills",
            "unknown_fills",
            "no_order_skips",
            "cash_or_slot_skips",
            "traded_days",
            "known_usd_per_calendar_day",
            "dollars_per_year_252",
            "known_usd_per_day_per_1000_reserve",
            "dollars_per_year_252_per_1000_reserve",
            "mean_usd_per_known_fill",
            "peak_concurrent_positions",
            "mean_deployed_usd_per_day",
            "full_loss_lower_bound (separately labeled)",
        ],
        "costs_reported_beside_every_cell": True,
    },
    "annualization": {
        "convention": (
            "dollars_per_year_252 = known contribution $ per calendar day x 252, a SIMPLE "
            "session-count extrapolation of a daily-reset reserve"
        ),
        "is_cagr": False,
        "is_self_financing_return": False,
    },
    "frozen_reference_comparison": {
        "lane": f"{FROZEN_LANE} (alpha_sparse_daily)",
        "view": FROZEN_VIEW,
        "reference_cell_in_grid": Cell(*REFERENCE_CELL).key,
        "basis_difference": (
            "the frozen lane books a FIXED $1,000 order notional per attempt (no whole-"
            "share flooring, so the notional is exactly $1,000); this study floors to "
            "whole shares at the observed entry bar, so its notional is q * entry_open"
        ),
        "read_after_this_runs_own_late_traversal": True,
    },
    "novelty": {
        "vs_frozen_retained_lane": {
            "prior": "factory/scripts/alpha_sparse_daily.py (learned_sparse_daily)",
            "prior_axis": (
                "admission/exit cadence on a FIXED 3 x $1,000 book (five views, "
                "first-attempt baselines, per-added-leg increments)"
            ),
            "this_axis": (
                "BOOK SHAPE (slots x ticket) on the FIXED frozen signal, with whole-share "
                "quantity flooring at the observed entry bar, cost-invariant funded cash, "
                "per-cell capital normalization and an explicit no-order cash skip"
            ),
            "why_not_the_same_experiment": (
                "different decision axis, different sizing rule, different cash invariant "
                "and a different reporting object; the retained lane's cadence conclusions "
                "are neither re-derived nor overturned here"
            ),
        },
        "not_resurrected": ["threshold re-selection", "horizon re-selection", "model refit"],
    },
}


# ----- immutable stored model (loaded, never fitted) ------------------------------
def load_stored_model() -> tuple[dict, Path, dict]:
    """Load the one stored h60 head and verify its frozen order/horizon contract."""
    path = MODEL_DIR / f"payoff_h{MODEL_HORIZON}.joblib"
    if not path.exists():
        raise SystemExit(f"[book-shape] stored model missing: {path}")
    bundle = joblib.load(path)
    order = list(bundle.get("feature_order") or [])
    if order != list(FEATURES_ALL):
        raise SystemExit("[book-shape] stored feature_order != current FEATURES_ALL")
    if int(bundle.get("horizon", -1)) != MODEL_HORIZON:
        raise SystemExit(f"[book-shape] stored horizon {bundle.get('horizon')} != {MODEL_HORIZON}")
    booster = bundle["lgbm"]
    names = list(getattr(booster, "feature_name_", []) or [])
    generic = [f"Column_{i}" for i in range(len(order))]
    if names and names != order and names != generic:
        raise SystemExit(
            "[book-shape] booster feature names match neither the stored feature_order "
            "nor the generic matrix names"
        )
    n_feat = int(getattr(booster, "n_features_in_", 0) or len(order))
    if n_feat != len(order):
        raise SystemExit(f"[book-shape] booster feature count {n_feat} != {len(order)}")
    report = {
        "path": str(path),
        "sha256": sha256_file(path),
        "horizon": MODEL_HORIZON,
        "feature_order": order,
        "n_features": n_feat,
        "params": bundle.get("params"),
        "clip_fit": bundle.get("clip_fit"),
        "seed": bundle.get("seed"),
        "refit": False,
        "hpo": False,
    }
    return bundle, path, report


def day_context(frame: pl.DataFrame) -> pl.DataFrame:
    """Re-derive the four past-only peer-context columns the stored model needs.

    Verbatim mirror of the frozen reference (alpha_sparse_daily.day_context): the panel
    producer computes these at load time over ``over(["day", "t"])`` windows, so the
    per-day files do not carry them; recomputing on the SAME partition key per day
    reproduces the exact values because the window is entirely inside one session and
    peer_positive3 includes the subject itself, exactly as CONTEXT_FEATURES was defined.
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


# ----- per-day signal states (scored ONCE, shared by every book cell) -------------
STATE_COLUMNS = (
    "day",
    "ticker",
    "t",
    "entry_et",
    "entry_open",
    "entry_status",
    "session_end",
    "score",
    "gross_60",
    "exit_et_60",
    "exit_status_60",
)
STATE_TYPES = {
    "day": pl.String,
    "ticker": pl.String,
    "t": pl.Int64,
    "entry_et": pl.Int64,
    "entry_open": pl.Float64,
    "entry_status": pl.String,
    "session_end": pl.Int64,
    "score": pl.Float64,
    "gross_60": pl.Float64,
    "exit_et_60": pl.Int64,
    "exit_status_60": pl.String,
}


def empty_states() -> pl.DataFrame:
    return pl.DataFrame(schema=STATE_TYPES)


def quantity_of(ticket: float, row: dict) -> tuple[int | None, float]:
    """Whole shares the ticket buys at the OBSERVED entry bar (None = no bar printed).

    The quantity is fixed from the observed causal next-minute open and is never
    resized: the deployed notional is ``q * entry_open <= ticket``. A zero floor is the
    KNOWN no-order cash skip, which is deliberately NOT an error and NOT an unknown.
    """
    if row["entry_status"] != "filled_proxy":
        return None, 0.0
    price = row["entry_open"]
    if price is None or not math.isfinite(float(price)) or float(price) <= 0:
        raise ValueError(
            f"nonpositive entry open: {row['day']} {row['ticker']} t={row['t']} entry_open={price}"
        )
    qty = int(math.floor(float(ticket) / float(price)))
    return qty, float(qty) * float(price)


def score_day(day: str, model: dict) -> tuple[pl.DataFrame, dict]:
    """Score one session's liquidity-qualified panel states at the frozen bar.

    One panel frame and one day's states are resident at a time; the corpus is never
    concatenated here. The score is the ONLY thing kept: no label is used as an entry
    filter, and the h60 label is carried along for the replay's realized basis only.
    """
    if not allowed(day):
        raise ValueError(f"protected/out-of-scope day refused: {day}")
    if any(day.startswith(p) for p in PROTECTED_UNREAD):
        raise ValueError(f"protected outcome window refused: {day}")
    path = PANEL_DAYS / f"{day}.parquet"
    if not path.exists():
        raise ValueError(f"panel day missing: {path}")
    frame = pl.read_parquet(path)
    frame = day_context(frame)
    cand = frame.filter(causal_liquidity(frame))
    cov = {
        "panel_rows": int(frame.height),
        "qualified_rows": int(cand.height),
        "tickers_qualified": 0,
        "thr_clearing_states": 0,
        "entry_status_counts": {},
        "exit_status_60_counts": {},
        "no_order_min_capital_by_ticket": {str(int(t)): 0 for t in TICKETS},
        "unfilled_states": 0,
        "unknown_exit_states": 0,
    }
    if not cand.height:
        return empty_states(), cov
    tickers = sorted(set(cand["ticker"].to_list()))
    cov["tickers_qualified"] = len(tickers)
    pred = np.asarray(model["lgbm"].predict(feature_matrix(cand)), dtype=float)
    scored = cand.with_columns(pl.Series("score", pred))
    states = (
        scored.filter(pl.col("score") >= THRESHOLD)
        .select(STATE_COLUMNS)
        .sort(["t", "score", "ticker"], descending=[False, True, False])
    )
    cov["thr_clearing_states"] = int(states.height)
    if states.height:
        cov["entry_status_counts"] = dict(
            states.group_by("entry_status").len().sort("len", descending=True).iter_rows()
        )
        cov["exit_status_60_counts"] = dict(
            states.group_by("exit_status_60").len().sort("len", descending=True).iter_rows()
        )
        cov["unfilled_states"] = int((states["entry_status"] == "unfilled_expired").sum())
        cov["unknown_exit_states"] = int((states["exit_status_60"] == "unknown_pending").sum())
        rows = states.to_dicts()
        no_order = Counter()
        for ticket in TICKETS:
            for r in rows:
                qty, _ = quantity_of(ticket, r)
                if qty == 0:
                    no_order[str(int(ticket))] += 1
        cov["no_order_min_capital_by_ticket"] = {
            str(int(t)): int(no_order.get(str(int(t)), 0)) for t in TICKETS
        }
    return states, cov


def state_paths(out_dir: Path, day: str) -> tuple[Path, Path]:
    return out_dir / "states" / f"{day}.parquet", out_dir / "states" / f"{day}.cov.json"


def score_block(out_dir: Path, model: dict, model_sha: str, days: list[str], resume: bool) -> dict:
    """Build (or resume) every day's scored states atomically, one day at a time.

    ``resume_hash`` binds a cached part to the producer sha, the contract sha, the model
    sha, the day and the states schema, so any change to the grid, the engine or the
    model silently re-derives that day instead of resuming a stale artifact.
    """
    producer = producer_sha256()
    contract_sha = _digest_bytes(CONTRACT)
    todo, infos = [], []
    coverage = {
        "days_total": len(days),
        "days_cached": 0,
        "days_scored": 0,
        "panel_rows": 0,
        "qualified_rows": 0,
        "thr_clearing_states": 0,
        "tickers_qualified": 0,
        "entry_status_counts": {},
        "exit_status_60_counts": {},
        "no_order_min_capital_by_ticket": {str(int(t)): 0 for t in TICKETS},
        "unfilled_states": 0,
        "unknown_exit_states": 0,
    }
    status_totals: Counter = Counter()
    exit_totals: Counter = Counter()
    no_order_totals: Counter = Counter()
    for day in days:
        qpath, cpath = state_paths(out_dir, day)
        expect = _resume_hash(producer, contract_sha, model_sha, day, STATES_SCHEMA)
        prior, fresh = None, False
        if resume and cpath.exists() and qpath.exists():
            try:
                prior = json.loads(cpath.read_text())
                fresh = (
                    prior.get("resume_hash") == expect and prior.get("rows_schema") == STATES_SCHEMA
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
    for n, day in enumerate(todo, 1):
        states, cov = score_day(day, model)
        qpath, cpath = state_paths(out_dir, day)
        qpath.parent.mkdir(parents=True, exist_ok=True)
        tmp = qpath.with_name(f"{qpath.name}.tmp{os.getpid()}")
        states.write_parquet(tmp)
        os.replace(tmp, qpath)
        write_json_atomic(
            cpath,
            {
                "day": day,
                "rows": int(states.height),
                "resume_hash": _resume_hash(producer, contract_sha, model_sha, day, STATES_SCHEMA),
                "producer_sha256": producer,
                "contract_sha256": contract_sha,
                "model_sha256": model_sha,
                "states_sha256": sha256_file(qpath),
                "rows_schema": STATES_SCHEMA,
                "coverage": cov,
            },
        )
        infos.append({"day": day, "resumed": False, "rows": int(states.height), "coverage": cov})
        if n % 25 == 0 or n == len(todo):
            print(
                f"[score] {n}/{len(todo)} days, last {day}, "
                f"thr_clearing={cov['thr_clearing_states']}",
                flush=True,
            )
    for info in infos:
        cov = info["coverage"] or {}
        coverage["panel_rows"] += int(cov.get("panel_rows", 0))
        coverage["qualified_rows"] += int(cov.get("qualified_rows", 0))
        coverage["thr_clearing_states"] += int(cov.get("thr_clearing_states", 0))
        coverage["tickers_qualified"] += int(cov.get("tickers_qualified", 0))
        coverage["unfilled_states"] += int(cov.get("unfilled_states", 0))
        coverage["unknown_exit_states"] += int(cov.get("unknown_exit_states", 0))
        status_totals.update(cov.get("entry_status_counts") or {})
        exit_totals.update(cov.get("exit_status_60_counts") or {})
        for k, v in (cov.get("no_order_min_capital_by_ticket") or {}).items():
            no_order_totals[k] += int(v)
    coverage.update(
        {
            "days_cached": len(infos),
            "days_scored": len(todo),
            "entry_status_counts": dict(status_totals),
            "exit_status_60_counts": dict(exit_totals),
            "no_order_min_capital_by_ticket": {
                str(int(t)): int(no_order_totals.get(str(int(t)), 0)) for t in TICKETS
            },
        }
    )
    return coverage


# ----- funded-reserve replay: every book cell on one session ----------------------
DAILY_TYPES: dict[str, pl.DataType] = {
    "day": pl.String,
    "cell_key": pl.String,
    "slots": pl.Int64,
    "ticket": pl.Float64,
    "reserve_usd": pl.Float64,
    "signals": pl.Int64,
    "funded_intents": pl.Int64,
    "fills": pl.Int64,
    "known_fills": pl.Int64,
    "unknown_fills": pl.Int64,
    "unfilled_cash_intents": pl.Int64,
    "no_order_skips": pl.Int64,
    "ticker_busy_skips": pl.Int64,
    "cooldown_skips": pl.Int64,
    "max_attempt_skips": pl.Int64,
    "slot_skips": pl.Int64,
    "cash_skips": pl.Int64,
    "skips_total": pl.Int64,
    "positions_peak": pl.Int64,
    "positions_mean": pl.Float64,
    "deployed_sum_usd": pl.Float64,
    "deployed_samples": pl.Int64,
    "peak_deployed_usd": pl.Float64,
    "entry_notional_usd": pl.Float64,
    "unknown_notional_usd": pl.Float64,
}
for _c in CELL_COSTS_BPS:
    DAILY_TYPES[f"known_usd_{int(_c)}"] = pl.Float64
    DAILY_TYPES[f"lower_bound_usd_{int(_c)}"] = pl.Float64
DAILY_COLUMNS = tuple(DAILY_TYPES)

TRADE_TYPES: dict[str, pl.DataType] = {
    "day": pl.String,
    "cell_key": pl.String,
    "ticker": pl.String,
    "t": pl.Int64,
    "entry_et": pl.Int64,
    "entry_open": pl.Float64,
    "entry_status": pl.String,
    "qty": pl.Int64,
    "notional_usd": pl.Float64,
    "score": pl.Float64,
    "attempt_index": pl.Int64,
    "fill_status": pl.String,
    "exit_et": pl.Int64,
    "exit_status": pl.String,
    "gross_60": pl.Float64,
    "hold_min": pl.Int64,
    "positions_at_entry": pl.Int64,
}
for _c in CELL_COSTS_BPS:
    TRADE_TYPES[f"net_usd_{int(_c)}"] = pl.Float64
TRADE_COLUMNS = tuple(TRADE_TYPES)


def empty_daily(day: str, cell: Cell) -> dict:
    row = {
        "day": day,
        "cell_key": cell.key,
        "slots": int(cell.slots),
        "ticket": float(cell.ticket),
        "reserve_usd": float(cell.reserve),
        "signals": 0,
        "funded_intents": 0,
        "fills": 0,
        "known_fills": 0,
        "unknown_fills": 0,
        "unfilled_cash_intents": 0,
        "no_order_skips": 0,
        "ticker_busy_skips": 0,
        "cooldown_skips": 0,
        "max_attempt_skips": 0,
        "slot_skips": 0,
        "cash_skips": 0,
        "skips_total": 0,
        "positions_peak": 0,
        "positions_mean": 0.0,
        "deployed_sum_usd": 0.0,
        "deployed_samples": 0,
        "peak_deployed_usd": 0.0,
        "entry_notional_usd": 0.0,
        "unknown_notional_usd": 0.0,
    }
    for _c in CELL_COSTS_BPS:
        row[f"known_usd_{int(_c)}"] = 0.0
        row[f"lower_bound_usd_{int(_c)}"] = 0.0
    return row


def empty_daily_frame(day: str) -> pl.DataFrame:
    return pl.DataFrame(
        {c: [empty_daily(day, cell)[c] for cell in CELLS] for c in DAILY_COLUMNS},
        schema=DAILY_TYPES,
    )


def empty_trade_frame() -> pl.DataFrame:
    return pl.DataFrame(schema=TRADE_TYPES)


def _trade_record(day: str, cell: Cell, row: dict, t: int, qty, notional: float, **kw) -> dict:
    rec = {
        "day": day,
        "cell_key": cell.key,
        "ticker": row["ticker"],
        "t": int(t),
        "entry_et": int(row["entry_et"]) if row["entry_et"] is not None else None,
        "entry_open": row["entry_open"],
        "entry_status": row["entry_status"],
        "qty": int(qty) if qty is not None else 0,
        "notional_usd": float(notional),
        "score": row["score"],
        "attempt_index": int(kw["attempt_index"]),
        "fill_status": kw["fill_status"],
        "exit_et": int(kw["exit_et"]) if kw["exit_et"] is not None else None,
        "exit_status": kw["exit_status"],
        "gross_60": row["gross_60"],
        "hold_min": kw["hold_min"],
        "positions_at_entry": int(kw["positions_at_entry"]),
    }
    for _c in CELL_COSTS_BPS:
        rec[f"net_usd_{int(_c)}"] = kw["net"].get(str(int(_c)))
    return rec


def replay_day(day: str, states: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Replay every pre-declared book cell on one session under the shared conventions.

    Long-only, funded reserve, no leverage: cash declines by the deployed notional on
    entry and is restored at the ACTUAL exit minute at the SAME notional (cost
    invariant), so all seven rungs of a cell share ONE identical fill set. All eligible
    intents of one clock are funded in descending model score up to the remaining cash
    and slots BEFORE any fill outcome is known (no substitution). An UNKNOWN exit keeps
    its cash and its slot to the session end. An unfilled entry minute reserves the
    ticket and releases it one minute later, fee-free, without anchoring a cooldown.
    Every calendar day stays in the denominator, so a no-signal day is a cash day.
    """
    if not states.height:
        return empty_daily_frame(day), empty_trade_frame()
    session_end = int(states["session_end"][0])
    rows = states.sort(["t", "score", "ticker"], descending=[False, True, False]).to_dicts()
    groups: dict[int, list[tuple[int, dict]]] = {}
    for i, r in enumerate(rows):
        groups.setdefault(int(r["t"]), []).append((i, r))
    # Quantity is fixed from the OBSERVED entry bar, per ticket, before any funding.
    shapes = {ticket: [quantity_of(ticket, r) for r in rows] for ticket in TICKETS}
    n_signals = len(rows)
    daily_records: list[dict] = []
    trade_records: list[dict] = []
    for cell in CELLS:
        reserve = cell.reserve
        cash = reserve
        active: dict[str, float] = {}
        blocked: dict[str, int] = {}
        used: dict[str, int] = {}
        queue: list[tuple] = []
        seq = 0
        skips: Counter = Counter()
        funded_intents = 0
        unfilled_intents = 0
        known_fills = 0
        unknown_fills = 0
        unknown_notional = 0.0
        entry_notional = 0.0
        known_rung = dict.fromkeys(COST_KEYS, 0.0)
        holds: list[int] = []
        deployed_sum = 0.0
        deployed_samples = 0
        positions_sum = 0
        positions_peak = 0
        min_cash = reserve
        for t in sorted(groups):
            while queue and queue[0][0] <= t:
                _, _, sym, back = heapq.heappop(queue)
                if sym in active:
                    del active[sym]
                    cash += back
            deployed = math.fsum(active.values())
            deployed_sum += deployed
            deployed_samples += 1
            positions_sum += len(active)
            for i, r in groups[t]:
                sym = r["ticker"]
                qty, notional = shapes[cell.ticket][i]
                if qty == 0:
                    # KNOWN no-order: the ticket cannot buy one whole share. No order,
                    # no attempt, no cooldown, no cash movement; never a fill, never an
                    # UNKNOWN, and never a priced zero-return trade.
                    skips["no_order_min_capital"] += 1
                    continue
                if sym in active:
                    skips["ticker_busy"] += 1
                    continue
                if t < int(blocked.get(sym, -1)):
                    skips["cooldown"] += 1
                    continue
                if int(used.get(sym, 0)) >= MAX_ATTEMPTS:
                    skips["max_attempts"] += 1
                    continue
                need = float(cell.ticket) if qty is None else notional
                if len(active) >= cell.slots:
                    skips["slot_full"] += 1
                    continue
                if cash + 1e-9 < need:
                    skips["cash_short"] += 1
                    continue
                # ---- funded: the book is committed BEFORE any fill outcome is known.
                used[sym] = int(used.get(sym, 0)) + 1
                funded_intents += 1
                if qty is None:
                    # No bar at the entry minute: the ticket is reserved and released one
                    # minute later, fee-free. No exit exists, so no cooldown is anchored.
                    cash -= float(cell.ticket)
                    unfilled_intents += 1
                    trade_records.append(
                        _trade_record(
                            day,
                            cell,
                            r,
                            t,
                            0,
                            0.0,
                            attempt_index=used[sym],
                            fill_status="unfilled_expired_cash",
                            exit_et=None,
                            exit_status="unfilled_cash",
                            hold_min=None,
                            positions_at_entry=len(active),
                            net=dict.fromkeys(COST_KEYS),
                        )
                    )
                    heapq.heappush(queue, (t + 1, seq, sym, float(cell.ticket)))
                    seq += 1
                    continue
                cash -= notional
                active[sym] = notional
                entry_notional += notional
                positions_peak = max(positions_peak, len(active))
                deployed_sum += notional
                deployed_samples += 1
                positions_sum += len(active)
                min_cash = min(min_cash, cash)
                gross, exit_et = r["gross_60"], r["exit_et_60"]
                if r["exit_status_60"] != "observed_open_proxy" or gross is None or exit_et is None:
                    # UNKNOWN: the exit bar is absent, so the position keeps its cash AND
                    # its slot to the session end. It is never cash and never dropped.
                    unknown_fills += 1
                    unknown_notional += notional
                    fill_status = "unknown_pending"
                    release = session_end + 1
                    net = dict.fromkeys(COST_KEYS)
                    hold = None
                else:
                    known_fills += 1
                    fill_status = "known_open_proxy"
                    release = int(exit_et)
                    net = {}
                    for key, bps in ((str(int(c)), c) for c in CELL_COSTS_BPS):
                        side = bps / 20_000.0
                        net[key] = notional * (
                            (1.0 + float(gross)) * (1.0 - side) / (1.0 + side) - 1.0
                        )
                        known_rung[key] += net[key]
                    hold = int(exit_et) - int(r["entry_et"]) if r["entry_et"] is not None else None
                    if hold is not None:
                        holds.append(hold)
                trade_records.append(
                    _trade_record(
                        day,
                        cell,
                        r,
                        t,
                        qty,
                        notional,
                        attempt_index=used[sym],
                        fill_status=fill_status,
                        exit_et=exit_et if fill_status == "known_open_proxy" else None,
                        exit_status=r["exit_status_60"],
                        hold_min=hold,
                        positions_at_entry=len(active),
                        net=net,
                    )
                )
                blocked[sym] = release + COOLDOWN_MIN
                heapq.heappush(queue, (release, seq, sym, notional))
                seq += 1
        rec = empty_daily(day, cell)
        rec.update(
            {
                "signals": int(n_signals),
                "funded_intents": int(funded_intents),
                "fills": int(known_fills + unknown_fills),
                "known_fills": int(known_fills),
                "unknown_fills": int(unknown_fills),
                "unfilled_cash_intents": int(unfilled_intents),
                "no_order_skips": int(skips["no_order_min_capital"]),
                "ticker_busy_skips": int(skips["ticker_busy"]),
                "cooldown_skips": int(skips["cooldown"]),
                "max_attempt_skips": int(skips["max_attempts"]),
                "slot_skips": int(skips["slot_full"]),
                "cash_skips": int(skips["cash_short"]),
                "skips_total": int(sum(skips.values())),
                "positions_peak": int(positions_peak),
                "positions_mean": (positions_sum / deployed_samples) if deployed_samples else 0.0,
                "deployed_sum_usd": float(deployed_sum),
                "deployed_samples": int(deployed_samples),
                "peak_deployed_usd": float(reserve - min_cash),
                "entry_notional_usd": float(entry_notional),
                "unknown_notional_usd": float(unknown_notional),
            }
        )
        for key in COST_KEYS:
            rec[f"known_usd_{key}"] = float(known_rung[key])
            rec[f"lower_bound_usd_{key}"] = float(known_rung[key] - unknown_notional)
        daily_records.append(rec)
    daily = pl.DataFrame(daily_records, schema=DAILY_TYPES)
    trades = (
        pl.DataFrame(trade_records, schema=TRADE_TYPES) if trade_records else empty_trade_frame()
    )
    return daily, trades


def replay_paths(out_dir: Path, block: str, day: str) -> tuple[Path, Path]:
    return (
        out_dir / "replay" / block / f"{day}.parquet",
        out_dir / "replay" / block / "trades" / f"{day}.parquet",
    )


def replay_block(
    out_dir: Path,
    block: str,
    days: list[str],
    resume: bool,
    producer: str,
    contract_sha: str,
    model_sha: str,
) -> dict:
    """Replay every book cell on every day of one block, writing resumable per-day parts."""
    todo, infos = [], []
    for day in days:
        if not allowed(day):
            raise ValueError(f"protected/out-of-scope day refused: {day}")
        qpath, _ = state_paths(out_dir, day)
        if not qpath.exists():
            raise ValueError(f"scored states missing for {day}: run the scoring pass first")
        dpath, tpath = replay_paths(out_dir, block, day)
        expect = _resume_hash(producer, contract_sha, model_sha, f"{block}|{day}", REPLAY_SCHEMA)
        cpath = tpath.with_name(f"{day}.cov.json")
        fresh = False
        prior = None
        if resume and dpath.exists() and tpath.exists() and cpath.exists():
            try:
                prior = json.loads(cpath.read_text())
                fresh = prior.get("resume_hash") == expect and prior.get("schema") == REPLAY_SCHEMA
            except (json.JSONDecodeError, OSError):
                fresh = False
        if fresh:
            infos.append({"day": day, "resumed": True, "rows": prior.get("rows", {})})
        else:
            todo.append(day)
    for n, day in enumerate(todo, 1):
        states = pl.read_parquet(state_paths(out_dir, day)[0])
        daily, trades = replay_day(day, states)
        dpath, tpath = replay_paths(out_dir, block, day)
        for path, frame in ((dpath, daily), (tpath, trades)):
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
            frame.write_parquet(tmp)
            os.replace(tmp, path)
        write_json_atomic(
            tpath.with_name(f"{day}.cov.json"),
            {
                "day": day,
                "block": block,
                "rows": {
                    "daily": int(daily.height),
                    "trades": int(trades.height),
                },
                "resume_hash": _resume_hash(
                    producer, contract_sha, model_sha, f"{block}|{day}", REPLAY_SCHEMA
                ),
                "producer_sha256": producer,
                "contract_sha256": contract_sha,
                "model_sha256": model_sha,
                "daily_sha256": sha256_file(dpath),
                "trades_sha256": sha256_file(tpath),
                "schema": REPLAY_SCHEMA,
            },
        )
        infos.append(
            {"day": day, "resumed": False, "rows": {"daily": daily.height, "trades": trades.height}}
        )
        if n % 25 == 0 or n == len(todo):
            print(f"[replay] {block} {n}/{len(todo)} days, last {day}", flush=True)
    return {
        "block": block,
        "days_total": len(days),
        "days_cached": len(infos),
        "days_replayed": len(todo),
    }


def read_block_frames(
    out_dir: Path, block: str, days: list[str]
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """All per-day parts of one block: the per-cell daily rows and the funded intents."""
    daily_parts, trade_parts = [], []
    for day in days:
        dpath, tpath = replay_paths(out_dir, block, day)
        if not dpath.exists():
            raise ValueError(f"replay part missing for {block} {day}: rerun the replay pass")
        daily_parts.append(pl.read_parquet(dpath))
        trade_parts.append(pl.read_parquet(tpath) if tpath.exists() else empty_trade_frame())
    daily = pl.concat(daily_parts) if daily_parts else empty_daily_frame("")
    trades = pl.concat(trade_parts) if trade_parts else empty_trade_frame()
    return daily, trades


# ----- block aggregation: every reported number for one (book cell, cost) run ------
def _sum(rows: list[dict], key: str) -> int:
    return int(sum(int(r[key]) for r in rows))


def monthly_yearly(daily_rows: list[dict]) -> tuple[dict, dict]:
    """Per-month and per-year replayed-day / traded-day / fill / UNKNOWN accounting."""
    buckets: dict[str, dict] = {}
    for r in daily_rows:
        for tag in (r["day"][:7], r["day"][:4]):
            e = buckets.setdefault(
                tag,
                {
                    "days_replayed": 0,
                    "days_with_fills": 0,
                    "fills": 0,
                    "known_fills": 0,
                    "unknown_fills": 0,
                    "known_usd": {},
                    "lower_bound_usd": {},
                },
            )
            e["days_replayed"] += 1
            if int(r["fills"]) > 0:
                e["days_with_fills"] += 1
            e["fills"] += int(r["fills"])
            e["known_fills"] += int(r["known_fills"])
            e["unknown_fills"] += int(r["unknown_fills"])
            for key in COST_KEYS:
                e["known_usd"][key] = e["known_usd"].get(key, 0.0) + float(r[f"known_usd_{key}"])
                e["lower_bound_usd"][key] = e["lower_bound_usd"].get(key, 0.0) + float(
                    r[f"lower_bound_usd_{key}"]
                )
    monthly = {
        tag: {
            "days_replayed": int(e["days_replayed"]),
            "days_with_fills": int(e["days_with_fills"]),
            "fills": int(e["fills"]),
            "known_fills": int(e["known_fills"]),
            "unknown_fills": int(e["unknown_fills"]),
            "known_usd": {k: round(float(v), 2) for k, v in sorted(e["known_usd"].items())},
            "full_loss_lower_bound_usd": {
                k: round(float(v), 2) for k, v in sorted(e["lower_bound_usd"].items())
            },
        }
        for tag, e in sorted(buckets.items())
        if len(tag) == 7
    }
    yearly = {
        tag: {
            "days_replayed": int(e["days_replayed"]),
            "days_with_fills": int(e["days_with_fills"]),
            "fills": int(e["fills"]),
            "known_fills": int(e["known_fills"]),
            "unknown_fills": int(e["unknown_fills"]),
            "known_usd_per_calendar_day": {
                k: float(v) / float(e["days_replayed"]) for k, v in sorted(e["known_usd"].items())
            },
            "full_loss_lower_bound_usd_per_calendar_day": {
                k: float(v) / float(e["days_replayed"])
                for k, v in sorted(e["lower_bound_usd"].items())
            },
        }
        for tag, e in sorted(buckets.items())
        if len(tag) == 4
    }
    return monthly, yearly


def cell_metrics(
    cell: Cell, daily_rows: list[dict], trades_cell: pl.DataFrame, days: list[str]
) -> dict:
    """Every reported number for one book cell on one block's calendar."""
    n_days = len(days)
    reserve = cell.reserve
    known = {k: math.fsum(float(r[f"known_usd_{k}"]) for r in daily_rows) for k in COST_KEYS}
    lower = {k: math.fsum(float(r[f"lower_bound_usd_{k}"]) for r in daily_rows) for k in COST_KEYS}
    unknown_notional = math.fsum(float(r["unknown_notional_usd"]) for r in daily_rows)
    entry_notional = math.fsum(float(r["entry_notional_usd"]) for r in daily_rows)
    known_fills = _sum(daily_rows, "known_fills")
    unknown_fills = _sum(daily_rows, "unknown_fills")
    fills = _sum(daily_rows, "fills")
    samples = _sum(daily_rows, "deployed_samples")
    deployed = math.fsum(float(r["deployed_sum_usd"]) for r in daily_rows)
    traded_days = sum(1 for r in daily_rows if int(r["fills"]) > 0)
    mean_hold = None
    if trades_cell.height:
        hold = trades_cell.filter(pl.col("hold_min").is_not_null())["hold_min"]
        if hold.len():
            mean_hold = float(hold.mean())
    monthly, yearly = monthly_yearly(daily_rows)
    return {
        "cell": {
            "key": cell.key,
            "label": cell.label,
            "slots": int(cell.slots),
            "ticket_usd": float(cell.ticket),
            "reserve_usd": float(reserve),
            "is_reference_cell": bool(cell.control),
        },
        "days_replayed": int(n_days),
        "signals": _sum(daily_rows, "signals"),
        "funded_intents": _sum(daily_rows, "funded_intents"),
        "fills": int(fills),
        "known_fills": int(known_fills),
        "unknown_fills": int(unknown_fills),
        "unfilled_cash_intents": _sum(daily_rows, "unfilled_cash_intents"),
        "no_order_skips": _sum(daily_rows, "no_order_skips"),
        "cash_or_slot_skips": _sum(daily_rows, "slot_skips") + _sum(daily_rows, "cash_skips"),
        "skip_reasons": {
            "no_order_min_capital": _sum(daily_rows, "no_order_skips"),
            "ticker_busy": _sum(daily_rows, "ticker_busy_skips"),
            "cooldown": _sum(daily_rows, "cooldown_skips"),
            "max_attempts": _sum(daily_rows, "max_attempt_skips"),
            "slot_full": _sum(daily_rows, "slot_skips"),
            "cash_short": _sum(daily_rows, "cash_skips"),
        },
        "skips_total": _sum(daily_rows, "skips_total"),
        "traded_days": int(traded_days),
        "days_with_funded_intents": sum(1 for r in daily_rows if int(r["funded_intents"]) > 0),
        "peak_concurrent_positions": (
            max(int(r["positions_peak"]) for r in daily_rows) if daily_rows else 0
        ),
        "mean_positions_open": (
            math.fsum(float(r["positions_mean"]) * int(r["deployed_samples"]) for r in daily_rows)
            / samples
            if samples
            else None
        ),
        "mean_deployed_usd_per_day": (
            math.fsum(
                float(r["deployed_sum_usd"]) / int(r["deployed_samples"])
                for r in daily_rows
                if int(r["deployed_samples"]) > 0
            )
            / n_days
            if n_days
            else None
        ),
        "deployed_usd_per_sample": (deployed / samples if samples else None),
        "deployed_samples": int(samples),
        "peak_deployed_usd": (
            max(float(r["peak_deployed_usd"]) for r in daily_rows) if daily_rows else None
        ),
        "entry_notional_usd": float(entry_notional),
        "exit_notional_usd": float(entry_notional - unknown_notional),
        "turnover_usd": float(2.0 * entry_notional - unknown_notional),
        "turnover_usd_per_day": (
            float(2.0 * entry_notional - unknown_notional) / n_days if n_days else None
        ),
        "unknown_notional_usd": float(unknown_notional),
        "mean_hold_min": mean_hold,
        "cost_ladder_bps": [float(c) for c in CELL_COSTS_BPS],
        "known_usd": {k: float(v) for k, v in known.items()},
        "known_usd_per_calendar_day": {
            k: (float(v) / n_days if n_days else None) for k, v in known.items()
        },
        "dollars_per_year_252": {
            k: (float(v) / n_days * TRADING_DAYS_PER_YEAR if n_days else None)
            for k, v in known.items()
        },
        "known_usd_per_day_per_1000_reserve": {
            k: (float(v) / n_days * cell.reserve_per_1000 if n_days else None)
            for k, v in known.items()
        },
        "dollars_per_year_252_per_1000_reserve": {
            k: (
                float(v) / n_days * cell.reserve_per_1000 * TRADING_DAYS_PER_YEAR
                if n_days
                else None
            )
            for k, v in known.items()
        },
        "mean_usd_per_known_fill": {
            k: (float(v) / known_fills if known_fills else None) for k, v in known.items()
        },
        "full_loss_lower_bound": {
            "basis": (
                "known contribution minus the WHOLE notional of every UNKNOWN fill; a "
                "lower bound that assumes every unknown position lost its entire ticket, "
                "never an expectation"
            ),
            "unknown_notional_usd": float(unknown_notional),
            "usd": {k: float(v) for k, v in lower.items()},
            "usd_per_calendar_day": {
                k: (float(v) / n_days if n_days else None) for k, v in lower.items()
            },
            "dollars_per_year_252": {
                k: (float(v) / n_days * TRADING_DAYS_PER_YEAR if n_days else None)
                for k, v in lower.items()
            },
            "usd_per_day_per_1000_reserve": {
                k: (float(v) / n_days * cell.reserve_per_1000 if n_days else None)
                for k, v in lower.items()
            },
        },
        "bootstrap_daily_known_return_on_reserve": {
            k: bootstrap_daily(
                [float(r[f"known_usd_{k}"]) / reserve for r in daily_rows], n=BOOT_N, seed=BOOT_SEED
            )
            for k in COST_KEYS
        },
        "monthly": monthly,
        "yearly": yearly,
    }


def block_invariants(surface: dict, daily: pl.DataFrame, trades: pl.DataFrame) -> dict:
    """Structural checks a consumer of these artifacts would rely on.

    Two properties must hold or the surface is not comparable: every book cell sees the
    SAME thr-clearing signal set (the book shape must never change what is admitted), and
    the fill set of a cell is rung-invariant (a known fill carries all seven rung nets,
    an UNKNOWN carries none), so the cost ladder is a pure translation.
    """
    checks = {
        "signals_identical_across_cells": len({v["signals"] for v in surface.values()}) == 1,
        "rung_net_consistency_by_cell": {},
        "daily_row_counts": {},
    }
    net_cols = [f"net_usd_{k}" for k in COST_KEYS]
    for cell in CELLS:
        tr = trades.filter(pl.col("cell_key") == cell.key)
        filled = tr.filter(pl.col("fill_status") != "unfilled_expired_cash")
        known_rows = filled.filter(pl.col(net_cols[0]).is_not_null())
        unknown_rows = filled.filter(pl.col(net_cols[0]).is_null())
        unknown_has_net = False
        if unknown_rows.height:
            flags = unknown_rows.select([pl.col(c).is_not_null().any() for c in net_cols]).row(0)
            unknown_has_net = bool(any(flags))
        known_missing_net = False
        if known_rows.height:
            flags = known_rows.select([pl.col(c).is_null().any() for c in net_cols]).row(0)
            known_missing_net = bool(any(flags))
        checks["rung_net_consistency_by_cell"][cell.key] = {
            "funded_intent_rows": int(tr.height),
            "position_rows": int(filled.height),
            "known_rows": int(known_rows.height),
            "unknown_rows": int(unknown_rows.height),
            "unknown_row_carries_no_rung_net": not unknown_has_net,
            "known_row_carries_every_rung_net": not known_missing_net,
            "known_rows_match_daily_known_fills": int(known_rows.height)
            == int(surface[cell.key]["known_fills"]),
            "position_rows_match_daily_fills": int(filled.height)
            == int(surface[cell.key]["fills"]),
        }
        checks["daily_row_counts"][cell.key] = int(
            daily.filter(pl.col("cell_key") == cell.key).height
        )
    checks["all_match"] = bool(
        checks["signals_identical_across_cells"]
        and all(
            v["unknown_row_carries_no_rung_net"]
            and v["known_row_carries_every_rung_net"]
            and v["known_rows_match_daily_known_fills"]
            and v["position_rows_match_daily_fills"]
            for v in checks["rung_net_consistency_by_cell"].values()
        )
    )
    return checks


def aggregate_block(out_dir: Path, block: str, days: list[str]) -> tuple[dict, dict]:
    """The full eight-cell book surface of one block, plus the block's coverage."""
    daily, trades = read_block_frames(out_dir, block, days)
    surface: dict[str, dict] = {}
    for cell in CELLS:
        rows = daily.filter(pl.col("cell_key") == cell.key).sort("day").to_dicts()
        surface[cell.key] = cell_metrics(
            cell, rows, trades.filter(pl.col("cell_key") == cell.key), days
        )
    totals = {
        f: int(daily[f].sum())
        for f in (
            "funded_intents",
            "fills",
            "known_fills",
            "unknown_fills",
            "unfilled_cash_intents",
            "no_order_skips",
            "slot_skips",
            "cash_skips",
            "skips_total",
            "signals",
        )
    }
    coverage = {
        "block": block,
        "days": len(days),
        "cells": N_CELLS,
        "cost_rungs": len(CELL_COSTS_BPS),
        "sum_over_cells": totals,
        "daily_parts": len(days),
        "trade_rows": int(trades.height),
        "invariants": block_invariants(surface, daily, trades),
    }
    return surface, coverage


# ----- selection: 2023 validation only, frozen before any late read ---------------
def rank_cells(surface: dict, cost_bps: float) -> list[dict]:
    """Deterministic ordering of the pre-declared grid by validation dollars/day.

    Tie-break is fixed in the contract: dollars per calendar day, then known fills, then
    a LOWER reserve, then the cell key, so no ordering can depend on dict/disk order.
    """
    key = str(int(cost_bps))
    ranked = sorted(
        CELLS,
        key=lambda c: (
            -float(surface[c.key]["known_usd_per_calendar_day"][key]),
            -int(surface[c.key]["known_fills"]),
            float(c.reserve),
            c.key,
        ),
    )
    return [
        {
            "rank": i + 1,
            "cell_key": c.key,
            "label": c.label,
            "slots": c.slots,
            "ticket_usd": c.ticket,
            "reserve_usd": c.reserve,
            "known_usd_per_calendar_day": surface[c.key]["known_usd_per_calendar_day"][key],
            "dollars_per_year_252": surface[c.key]["dollars_per_year_252"][key],
            "known_fills": surface[c.key]["known_fills"],
            "unknown_fills": surface[c.key]["unknown_fills"],
            "no_order_skips": surface[c.key]["no_order_skips"],
            "traded_days": surface[c.key]["traded_days"],
        }
        for i, c in enumerate(ranked)
    ]


def selection_object(
    surface: dict, cost_bps: float, ranked: list[dict], *, identity: str, scope: str, sibling: str
) -> dict:
    """One frozen selection object at one pre-declared rung (never a hurdle)."""
    key = str(int(cost_bps))
    top = ranked[0]
    cell = CELLS_BY_KEY[top["cell_key"]]
    metrics = surface[cell.key]
    return {
        "objective": (
            "2023 validation KNOWN contribution dollars per calendar day (PARTIAL basis: "
            "UNKNOWN executions excluded from the numerator, reported beside it)"
        ),
        "selection_cost_bps": float(cost_bps),
        "cost_scenario_nature": (
            "each rung is a TOTAL modeled round-trip minute-proxy friction scenario on the "
            "proxy prices, NOT an actual broker fee: a primary US broker's regular schedule "
            "(zero commission, SEC 20.6 per $1M, CAT 0.000003 per side, daily cent rounding) "
            "is typically well under 1 bp on a $100-$1,000 ticket, so the low rungs describe "
            "a LOW-FEE OPPORTUNITY set, not a fee claim"
        ),
        "identity": identity,
        "choice_scope": scope,
        "comparison_sibling": sibling,
        "predeclared_before_late_inspection": True,
        "status": STATUS,
        "cost_ladder_bps": [float(c) for c in CELL_COSTS_BPS],
        "cost_ladder_is_comparison_not_hurdle": True,
        "power_floors": None,
        "median_or_tail_gates": None,
        "cost_or_count_kill": None,
        "tie_break": [
            "higher known contribution $ per calendar day",
            "more known fills",
            "lower reserve",
            "cell key (lexicographic)",
        ],
        "ranked": ranked,
        "chosen": {
            "cell_key": cell.key,
            "label": cell.label,
            "slots": cell.slots,
            "ticket_usd": cell.ticket,
            "reserve_usd": cell.reserve,
        },
        "chosen_known_usd_per_calendar_day": metrics["known_usd_per_calendar_day"][key],
        "chosen_dollars_per_year_252": metrics["dollars_per_year_252"][key],
        "chosen_known_fills": metrics["known_fills"],
        "chosen_unknown_fills": metrics["unknown_fills"],
        "chosen_no_order_skips": metrics["no_order_skips"],
        "chosen_traded_days": metrics["traded_days"],
        "chosen_mean_usd_per_known_fill": metrics["mean_usd_per_known_fill"][key],
        "chosen_full_loss_lower_bound_usd_per_calendar_day": metrics["full_loss_lower_bound"][
            "usd_per_calendar_day"
        ][key],
    }


# ----- the frozen (3,1000) reference on the retained lane --------------------------
def frozen_reference(include_late: bool) -> dict:
    """The retained lane's stored repeat_h60 cells, read AFTER this run's own freeze."""
    path = FROZEN_LANE / "results.json"
    if not path.exists():
        return {
            "present": False,
            "path": str(path),
            "note": "frozen lane results.json not found; the in-grid reference cell still stands",
        }
    payload = json.loads(path.read_text())
    val = (payload.get("validation") or {}).get(FROZEN_VIEW) or {}
    late = (payload.get("confirmation_by_cost") or {}).get(FROZEN_VIEW) or {}
    fields = (
        "days",
        "attempts",
        "fills",
        "known_fills",
        "unknown_fills",
        "traded_days",
        "mean_net_known_fill",
        "mean_daily_lower_bound",
        "dollars_per_day",
        "dollars_per_year_252",
        "reserve_usd",
        "order_budget",
        "max_positions",
    )

    def one(cell: dict | None) -> dict | None:
        if not cell:
            return None
        out = {f: cell.get(f) for f in fields}
        out["economics"] = {
            k: (cell.get("economics") or {}).get(k)
            for k in ("order_budget_usd", "max_positions", "reserve_usd", "turnover_usd")
        }
        known, mean_net = cell.get("known_fills"), cell.get("mean_net_known_fill")
        budget = (cell.get("economics") or {}).get("order_budget_usd")
        days = cell.get("days")
        out["derived_known_usd_per_calendar_day_partial_basis"] = (
            float(known) * float(mean_net) * float(budget) / float(days)
            if known and mean_net is not None and budget and days
            else None
        )
        out["derived_basis"] = (
            "known_fills x mean_net_known_fill x order_budget_usd / days_replayed: the "
            "frozen lane's PARTIAL KNOWN basis, recomputed from its stored fields so it is "
            "comparable with this study's objective"
        )
        out["stored_dollars_per_day_basis"] = (
            "the frozen lane's dollars_per_day is a LOWER-BOUND figure (known PnL minus "
            "a full unit per UNKNOWN fill), not the partial KNOWN basis used here"
        )
        return out

    out = {
        "present": True,
        "path": str(path),
        "lane": str(FROZEN_LANE),
        "producer": "alpha_sparse_daily",
        "view": FROZEN_VIEW,
        "basis": (
            "FIXED $1,000 order notional per attempt on a $3,000 reserve: no whole-share "
            "flooring, so the notional is exactly $1,000 and the entry bar price never "
            "changes the size"
        ),
        "validation_at_25bps": one(val.get(str(int(SELECT_COST)))),
        "validation_at_100bps": one(val.get(str(int(HISTORICAL_SELECT_COST)))),
        "read_after_this_runs_own_late_traversal": True,
    }
    if include_late:
        out["late_at_25bps"] = one(late.get(str(int(SELECT_COST))))
        out["late_at_100bps"] = one(late.get(str(int(HISTORICAL_SELECT_COST))))
        out["late_disclosure"] = (
            "2025-02..2026-05 was already explored before this study, so the late block "
            "is DISCOVERY-NOT-VALIDATED - NOT pristine and NOT previously unknown"
        )
    return out


def reference_comparison(val: dict, late: dict | None) -> dict:
    """Direct comparison object: the in-grid (3,1000) cell beside the frozen lane."""
    ref = Cell(*REFERENCE_CELL)
    mine_val = val[ref.key]
    frozen = frozen_reference(include_late=late is not None)
    out = {
        "reference_cell_key": ref.key,
        "reference_cell_label": ref.label,
        "this_study_reference_cell_validation": mine_val,
        "this_study_reference_cell_late": (late or {}).get(ref.key),
        "frozen_lane": frozen,
        "identical_basis_note": (
            "the (3,1000) cell of THIS study runs through the identical engine, quantity "
            "rule and funding rules as the other seven cells; the frozen lane books a "
            "fixed $1,000 order notional with no whole-share flooring, so the two columns "
            "differ in basis as well as in result and are not a like-for-like fill count"
        ),
    }
    fv = frozen.get("validation_at_100bps") or {}
    derived = fv.get("derived_known_usd_per_calendar_day_partial_basis")
    mine = mine_val["known_usd_per_calendar_day"][str(int(HISTORICAL_SELECT_COST))]
    if derived is not None and mine is not None:
        out["validation_delta_known_usd_per_calendar_day_at_100bps"] = float(mine) - float(derived)
    fl = frozen.get("late_at_100bps") or {}
    derived_late = fl.get("derived_known_usd_per_calendar_day_partial_basis")
    mine_late = (
        (late or {})
        .get(ref.key, {})
        .get("known_usd_per_calendar_day", {})
        .get(str(int(HISTORICAL_SELECT_COST)))
    )
    if derived_late is not None and mine_late is not None:
        out["late_delta_known_usd_per_calendar_day_at_100bps"] = float(mine_late) - float(
            derived_late
        )
    return out


# ----- reporting ------------------------------------------------------------------
def _fmt(value, spec="+.2f"):
    return "n/a" if value is None else format(value, spec)


def print_cell(tag: str, cell_key: str, m: dict, cost_bps: float) -> None:
    key = str(int(cost_bps))
    cell = m["cell"]
    reserve = cell["reserve_usd"]
    print(
        f"[{tag}@{int(cost_bps)}] {cell['label']:<10} "
        f"reserve=${int(reserve) if reserve is not None else -1:<5} "
        f"known={_fmt(m['known_usd_per_calendar_day'][key])} $/day "
        f"yr252={_fmt(m['dollars_per_year_252'][key])} "
        f"per1k={_fmt(m['known_usd_per_day_per_1000_reserve'][key])} $/day "
        f"known_fills={m['known_fills']} unk={m['unknown_fills']} "
        f"no_order={m['no_order_skips']} cash_slot={m['cash_or_slot_skips']} "
        f"traded_days={m['traded_days']}/{m['days_replayed']} "
        f"lb={_fmt(m['full_loss_lower_bound']['usd_per_calendar_day'][key])} $/day",
        flush=True,
    )


def decision_text(
    chosen: Cell,
    val: dict,
    late: dict | None,
    frozen: bool,
    historical: dict,
) -> str:
    key = str(int(SELECT_COST))
    m = val[chosen.key]
    obj = m["known_usd_per_calendar_day"][key]
    boot = m["bootstrap_daily_known_return_on_reserve"][key]
    lower = m["full_loss_lower_bound"]["usd_per_calendar_day"][key]
    if m["known_fills"] == 0 or obj is None:
        return (
            "NO_EDGE_EVIDENCE: the selected 2023 validation book cell has no known fill, "
            "so an empty signal set is cash, not a positive edge. Nothing here is "
            "promoted; the retained rare h60 lead stays the reference."
        )
    text = (
        f"{STATUS}: by 2023 validation KNOWN contribution the book shape {chosen.label} "
        f"({chosen.slots} slots x ${int(chosen.ticket)} = ${int(chosen.reserve)} reserve) "
        f"measures {obj:+.2f} $/calendar day at {int(SELECT_COST)} bps TOTAL modeled "
        f"minute-proxy friction over {m['known_fills']} known fills on "
        f"{m['traded_days']} traded days of {m['days_replayed']} calendared days, with "
        f"{m['unknown_fills']} UNKNOWN executions excluded from that numerator and a "
        f"separately labeled full-loss lower bound of {lower:+.2f} $/calendar day. "
        f"On the cell's own reserve that is {m['dollars_per_year_252'][key]:+.2f} $ per 252 "
        f"sessions (simple session-count extrapolation of a daily-reset reserve, NOT a "
        f"CAGR), or {m['known_usd_per_day_per_1000_reserve'][key]:+.2f} $/day per $1,000 "
        f"of reserve ({m['dollars_per_year_252_per_1000_reserve'][key]:+.2f} $/252 "
        f"sessions per $1,000). {m['no_order_skips']} thr-clearing states were KNOWN "
        f"no-order cash skips because the ticket cannot buy one whole share, and "
        f"{m['cash_or_slot_skips']} intents found no free cash or slot. Day bootstrap "
        f"p>0 = {boot.get('p_gt_zero')}."
    )
    if obj <= 0:
        text += (
            " The selected cell is NOT positive on validation, so NO book shape is "
            "promoted: every cell of the grid was measured, the ranking is stored in "
            "full, and the retained (3,1000) rare lead stays the reference."
        )
    if frozen and late is not None:
        lm = late[chosen.key]
        lobj = lm["known_usd_per_calendar_day"][key]
        text += (
            f" The frozen late block (2025-02..2026-05, previously explored, NOT pristine) "
            f"measures {lobj:+.2f} $/calendar day at {int(SELECT_COST)} bps over "
            f"{lm['known_fills']} known fills on {lm['traded_days']} traded days; the "
            f"choice was frozen before any late file was read, so this is transparency on "
            f"an already-frozen decision, never a re-selection."
        )
    elif not frozen:
        text += " Late block not run (--skip-late)."
    text += (
        f" BASIS: every fill is the minute-open PROXY the retained program uses - no "
        f"NBBO touch, no depth, no spread, no as-of quote audit and no capacity claim."
        f" The historical {int(HISTORICAL_SELECT_COST)} bps comparison selection is "
        f"{historical['chosen']['label']} "
        f"({historical['chosen_known_usd_per_calendar_day']:+.2f} $/calendar day); the two "
        f"frozen selections are reported side by side and neither rung is a hurdle."
    )
    return text


def provenance_block(
    out: Path,
    model_path: Path,
    model_sha: str,
    bundle: dict,
    val_days: list[str],
    late_days: list[str] | None,
    val_cov: dict,
    late_cov: dict | None,
) -> dict:
    return {
        "producer_sha256": producer_sha256(),
        "producer_script": str(Path(__file__).resolve()),
        "producer_snapshot": str(out / "producer_snapshot.py"),
        "contract_sha256": _digest_bytes(CONTRACT),
        "panel_root": str(PANEL_ROOT),
        "panel_days_root": str(PANEL_DAYS),
        "panel_contract_sha256": (sha256_file(PANEL_CONTRACT) if PANEL_CONTRACT.exists() else None),
        "model": {
            "path": str(model_path),
            "sha256": model_sha,
            "head": MODEL_HORIZON,
            "params": bundle.get("params"),
            "clip_fit": bundle.get("clip_fit"),
            "seed": bundle.get("seed"),
            "refit": False,
            "hpo": False,
        },
        "validation_days": len(val_days),
        "late_days": (
            len(late_days)
            if late_days is not None
            else "not_yet_inspected: the freeze precedes any late file access"
        ),
        "coverage_validation": val_cov,
        "coverage_late": late_cov,
        "output_root": str(out),
        "states_schema": STATES_SCHEMA,
        "replay_schema": REPLAY_SCHEMA,
    }


# ----- run ------------------------------------------------------------------------
def panel_days(requested: list[str] | None) -> list[str]:
    files = sorted(p.stem for p in PANEL_DAYS.glob("????-??-??.parquet") if allowed(p.stem))
    if not requested:
        return files
    known = set(files)
    missing = [d for d in sorted(set(requested)) if d not in known]
    if missing:
        raise SystemExit(f"[book-shape] requested days absent from the panel: {missing}")
    return sorted(set(requested))


def run(args: argparse.Namespace) -> None:
    t0 = time.time()
    out = args.out or OUTPUT
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")
    # 0) the pre-declared grid hits disk BEFORE any validation outcome exists.
    write_json_atomic(out / "contract.json", CONTRACT)
    print(
        f"[freeze-grid] contract -> {out / 'contract.json'} "
        f"({N_CELLS} book cells x {len(CELL_COSTS_BPS)} cost rungs = {N_CELL_RUNS} "
        f"fixed cells, before any outcome)",
        flush=True,
    )

    bundle, model_path, model_report = load_stored_model()
    model_sha = model_report["sha256"]
    print(
        f"[model] h{MODEL_HORIZON} {model_path} (sha256 {model_sha[:12]}), "
        f"seed={bundle.get('seed')} clip={bundle.get('clip_fit')}; no refit, no HPO",
        flush=True,
    )

    days = panel_days(args.days)
    val_days = [d for d in days if period(d) == VAL_PERIOD]
    late_days = [d for d in days if period(d) == CONF_PERIOD]
    if args.days is None:
        if len(val_days) != EXPECTED_DAYS[VAL_PERIOD]:
            raise SystemExit(
                f"[book-shape] requires {EXPECTED_DAYS[VAL_PERIOD]} validation days, "
                f"got {len(val_days)}"
            )
        if len(late_days) != EXPECTED_DAYS[CONF_PERIOD]:
            raise SystemExit(
                f"[book-shape] requires {EXPECTED_DAYS[CONF_PERIOD]} late days, "
                f"got {len(late_days)}"
            )
    if not val_days:
        raise SystemExit(
            "[book-shape] no 2023 validation days selected; the dollars/day selection "
            "objective requires the validation block"
        )
    if not late_days and not args.skip_late:
        raise SystemExit(
            "[book-shape] the requested calendar has no 2025-02..2026-05 days; either "
            "pass --skip-late (validation + frozen selection only) or request late days"
        )
    producer, contract_sha = producer_sha256(), _digest_bytes(CONTRACT)
    print(
        f"[blocks] validation={len(val_days)} days; the late block is deferred until "
        f"after the selection freeze",
        flush=True,
    )

    # 1) validation only: score once, replay all eight cells, aggregate.
    val_score_cov = score_block(out, bundle, model_sha, val_days, args.resume)
    val_replay_cov = replay_block(
        out, VAL_PERIOD, val_days, args.resume, producer, contract_sha, model_sha
    )
    val_surface, val_cov = aggregate_block(out, VAL_PERIOD, val_days)
    for cell in CELLS:
        print_cell("val", cell.key, val_surface[cell.key], SELECT_COST)

    # 2) TWO pre-declared selections, both on 2023 validation only, both frozen here
    #    BEFORE any late file is read: the 25 bps low-fee opportunity comparison and
    #    the 100 bps historical rung the retained program used.
    primary_ranked = rank_cells(val_surface, SELECT_COST)
    historical_ranked = rank_cells(val_surface, HISTORICAL_SELECT_COST)
    primary = selection_object(
        val_surface,
        SELECT_COST,
        primary_ranked,
        identity="PRIMARY_LOW_FEE_COMPARISON",
        scope=(
            "pre-declared 2023 validation selector at a 25 bps TOTAL modeled minute-proxy "
            "friction scenario (a friction scenario, NOT a broker fee). It is the primary "
            "comparison object and no candidate is closed by failing to clear it"
        ),
        sibling="historical_selection",
    )
    historical = selection_object(
        val_surface,
        HISTORICAL_SELECT_COST,
        historical_ranked,
        identity="HISTORICAL",
        scope=(
            "second, equally pre-declared comparison at the 100 bps rung the retained "
            "program used. 100 bps is a historical choice, NOT a universal hurdle; it "
            "neither replaces nor shims the 25 bps comparison"
        ),
        sibling="selection",
    )
    chosen = CELLS_BY_KEY[primary["chosen"]["cell_key"]]
    print(
        f"[select@{int(SELECT_COST)}] {chosen.label} by validation known contribution "
        f"({primary['chosen_known_usd_per_calendar_day']:+.2f} $/calendar day, "
        f"{primary['chosen_known_fills']} known fills) of {N_CELLS} book cells; "
        f"no median/tail gate, no count or cost kill",
        flush=True,
    )
    print(
        f"[select@{int(HISTORICAL_SELECT_COST)}] historical comparison "
        f"{historical['chosen']['label']} "
        f"({historical['chosen_known_usd_per_calendar_day']:+.2f} $/calendar day)",
        flush=True,
    )
    rankings = {str(int(c)): rank_cells(val_surface, c) for c in CELL_COSTS_BPS}
    freeze = {
        "frozen_before_late_file_access": True,
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "study": STUDY,
        "status": STATUS,
        "chosen": primary["chosen"],
        "selection": primary,
        "historical_selection": historical,
        "rankings_by_cost_bps": rankings,
        "validation_surface": val_surface,
        "model": model_report,
        "coverage": {"scoring": val_score_cov, "replay": val_replay_cov, "block": val_cov},
        "provenance": provenance_block(
            out, model_path, model_sha, bundle, val_days, None, val_cov, None
        ),
    }
    write_json_atomic(out / "selection_freeze.json", freeze)
    print(
        f"[freeze] selection_freeze.json -> {chosen.label} AFTER validation, BEFORE any "
        f"late panel file is read",
        flush=True,
    )

    if args.skip_late:
        results = {
            "study": STUDY,
            "status": STATUS,
            "decision": decision_text(
                chosen, val_surface, None, frozen=False, historical=historical
            ),
            "chosen": primary["chosen"],
            "frozen_choice_immutable": True,
            "contract": CONTRACT,
            "model": model_report,
            "validation": val_surface,
            "validation_ranking": primary_ranked,
            "selection": primary,
            "historical_selection": historical,
            "rankings_by_cost_bps": rankings,
            "reference_comparison": reference_comparison(val_surface, None),
            "late": None,
            "coverage": {
                "scoring": {"validation": val_score_cov, "late": None},
                "replay": {"validation": val_replay_cov, "late": None},
                "block": {"validation": val_cov, "late": None},
            },
            "provenance": provenance_block(
                out, model_path, model_sha, bundle, val_days, None, val_cov, None
            ),
            "artifacts": {
                "contract": str(out / "contract.json"),
                "selection_freeze": str(out / "selection_freeze.json"),
                "producer_snapshot": str(out / "producer_snapshot.py"),
                "states_root": str(out / "states"),
                "replay_root": str(out / "replay"),
            },
            "runtime_s": round(time.time() - t0, 1),
        }
        write_json_atomic(out / "results.json", results)
        print(f"[decision] {results['decision']}", flush=True)
        print(f"[done] {results['runtime_s']}s -> {out / 'results.json'}", flush=True)
        return

    # 3) late block: all eight cells for transparency, the choice already frozen. This
    #    is the FIRST access to any late panel file.
    late_score_cov = score_block(out, bundle, model_sha, late_days, args.resume)
    late_replay_cov = replay_block(
        out, CONF_PERIOD, late_days, args.resume, producer, contract_sha, model_sha
    )
    late_surface, late_cov = aggregate_block(out, CONF_PERIOD, late_days)
    for cell in CELLS:
        print_cell("late", cell.key, late_surface[cell.key], SELECT_COST)
    late_rankings = {str(int(c)): rank_cells(late_surface, c) for c in CELL_COSTS_BPS}
    late_leader = late_rankings[str(int(SELECT_COST))][0]
    print(
        f"[late@{int(SELECT_COST)}] transparency leader {late_leader['label']} "
        f"({late_leader['known_usd_per_calendar_day']:+.2f} $/calendar day); "
        f"the frozen choice stays {chosen.label} and is NEVER re-selected on late data",
        flush=True,
    )
    reference = reference_comparison(val_surface, late_surface)
    results = {
        "study": STUDY,
        "status": STATUS,
        "decision": decision_text(
            chosen, val_surface, late_surface, frozen=True, historical=historical
        ),
        "chosen": primary["chosen"],
        "frozen_choice_immutable": True,
        "contract": CONTRACT,
        "model": model_report,
        "validation": val_surface,
        "validation_ranking": primary_ranked,
        "selection": primary,
        "historical_selection": historical,
        "rankings_by_cost_bps": rankings,
        "late": late_surface,
        "late_ranking": late_rankings[str(int(SELECT_COST))],
        "late_rankings_by_cost_bps": late_rankings,
        "late_leader_is_not_a_reselection": True,
        "reference_comparison": reference,
        "coverage": {
            "scoring": {"validation": val_score_cov, "late": late_score_cov},
            "replay": {"validation": val_replay_cov, "late": late_replay_cov},
            "block": {"validation": val_cov, "late": late_cov},
        },
        "provenance": provenance_block(
            out, model_path, model_sha, bundle, val_days, late_days, val_cov, late_cov
        ),
        "artifacts": {
            "contract": str(out / "contract.json"),
            "selection_freeze": str(out / "selection_freeze.json"),
            "results": str(out / "results.json"),
            "producer_snapshot": str(out / "producer_snapshot.py"),
            "states_root": str(out / "states"),
            "replay_root": str(out / "replay"),
        },
        "runtime_s": round(time.time() - t0, 1),
    }
    write_json_atomic(out / "results.json", results)
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
        help="resume from the per-day parts already on disk (resume_hash must match)",
    )
    p.add_argument(
        "--skip-late",
        action="store_true",
        help="stop after the frozen selection (validation block only; no late file read)",
    )
    p.add_argument(
        "--days",
        nargs="+",
        default=None,
        help="restrict the analysis calendar to these ET days (debug only)",
    )
    run(p.parse_args())


if __name__ == "__main__":
    main()
