#!/usr/bin/env python3
"""Retained score-proportional SIZING grid on the immutable h60 top-gainer model (no refit).

The sparse lane (factory/scripts/alpha_sparse_daily.py) froze ONE account shape: three
flat $1,000 tickets on a $3,000 reserve, funded in descending forecast order. This lane
changes the TICKET SIZE -- and only the ticket size -- under the same everything else:
the same immutable learned h60 minute-open head (``learned/models/payoff_h60.joblib``, fit
block 2021-02..2022-12, never refit, never HPO'd), the same 0.030 score bar, the same h60
exit, the same repeat cadence (flat 15-minute cooldown after the ACTUAL exit, at most 3
attempts per ticker per session, one position per ticker), the same funded-reserve
conventions (simultaneous intents funded by descending model score BEFORE any fill outcome
is observed, unfilled attempts reserve-and-release fee-free, an UNKNOWN exit holds its slot
and its cash to the session end). The head is scored ONCE per day; every cell replays the
identical scored states, so no cell is ever a rescale of another cell's fills.

THE QUESTION: does concentrating capital in the strongest forecasts beat equal tickets, and
does a wider, shallower book capture more of the same signals? NINE pre-declared cells,
fixed on disk BEFORE any validation outcome exists:

* ``flat1000_s3``  flat $1,000 tickets, 3 slots   -- the frozen REFERENCE cell
* ``flat500_s6``   flat $500 tickets, 6 slots     -- wider/shallower, same $3,000 reserve
* ``flat250_s12``  flat $250 tickets, 12 slots    -- widest/shallowest, same reserve
* ``prop2000_s3`` / ``prop4000_s3`` / ``prop8000_s3``
                    ticket = clamp(scale * max(score - 0.030, 0), $100, $1,000), 3 slots
* ``prop2000_s6`` / ``prop4000_s6`` / ``prop8000_s6``
                    the same three scales with up to 6 slots

Book rules for every cell: a $3,000 cash reserve restored each session (a research
normalization, NOT a self-financing return), a $1,000 cap per position, up to the cell's
slot count held at once, whole-share quantity ``q = floor(ticket / entry_open)`` (a causal
q of zero is a KNOWN NO ORDER: no order is sent, the cash stays unfilled, and the intent is
never a priced zero-return fill and never an UNKNOWN). An intent whose entry bar never
printed (panel ``unfilled_expired``) is sized from the causal last-completed close carried
by the panel's own past-only ``log_price`` feature, so the ticket reservation, the attempt
count and the one-minute release mirror the frozen reference exactly; such an intent is
never a fill.

FILL SEMANTICS (stated everywhere, never hidden): every "fill" here is a MINUTE-OPEN
PROXY -- the panel's stored minute-open entry price and its stored first-actual-open exit
label at the declared h60 horizon. They are NOT actual quotes, NOT exchange fills, NOT a
fill guarantee, and NOT a capacity claim; $250-$1,000 tickets are book sizes on a research
reserve and displayed depth is not modelled at all. A missing exit bar is an UNKNOWN: it
never becomes cash, it holds its slot and its cash to the session end, and it is charged a
FULL-LOSS lower bound (the whole deployed notional) that is reported separately and is
NEVER blended into the known-contribution objective and never treated as an expected loss.

COSTS: the reported ladder 25/50/75/100/125/150 bps (plus the 200 bps historical diagnostic
rung carried by the stored late ladder for continuity) is TOTAL round-trip modeled friction
on the minute-open proxy prices, charged on both legs exactly as in the frozen reference
(``net = (1+gross)*(1-side)/(1+side) - 1`` with ``side = bps/20_000``). The ladder is a
COMPARISON SET, never a hurdle: no rung closes a candidate, no rung falsifies anything, and
100-150 bps is not a profitability bar. A primary US broker's regular schedule is typically
well under 1 bp on these tickets, so actual provider fees are sourced separately (provider
fee ledger) and stay UNKNOWN here; the low rungs describe a LOW-FEE OPPORTUNITY set, not a
fee claim. Each rung is its own full replay, so the cash path (exit proceeds returning to
the reserve later the same session) is internally consistent with that rung's friction.

SELECTION: the single pre-declared objective is 2023 VALIDATION known-contribution dollars
per FULL CALENDAR day at the 25 bps rung (unknowns excluded from the numerator, reported
beside it; the full-loss bound is never the objective and never a veto). Ties break
deterministically on more known fills, then fewer funded intents, then the cell key. The
complete grid, the cost ladder and this rule are frozen to ``contract.json`` BEFORE any
validation outcome exists; the choice is frozen to ``selection_freeze.json`` after the
validation surface and BEFORE any late panel day is read; the late 2025-02..2026-05 block
(a previously explored window, NOT pristine) is then traversed for transparency only, with
the choice immutable. Every reported cell carries an explicit delta against the frozen
``flat1000_s3`` reference cell on the identical basis.

ANNUALIZATION: mean daily known contribution x 252 sessions -- a simple session-count
convention on a daily-reset research book, NOT a CAGR and NOT an account claim.

Outputs (default ``~/alpha-data/open-search-v1/retained_score_sizing``):
  contract.json          the complete fixed grid + cost ladder + rules, before any outcome
  selection_freeze.json  the frozen validation-only choice + ranking + surface
  results.json           validation + late surfaces, per-cell deltas, decision, provenance
  states/<day>.parquet   one day's score>=0.030 states (atomic, resumable)
  replay/<block>/<day>.parquet        one day's per-cell x per-rung daily accounting
  replay/<block>/<day>.trades.parquet the day's funded intents with per-rung net dollars
  replay/<block>/<day>.cov.json       per-day resume manifest
Resume: ``resume_hash = sha256(producer sha + contract sha + model sha + day + schema)``;
a replay part additionally records the scored-states sha it consumed and reuses only when
that matches. Bounded memory: one panel day, one states part and one replay part are
resident at a time; progress prints every 25 days. No network, no live order, no account
write; protected windows 2024, 2025-01, 2026-06..2026-08 are never read (``allowed(day)``
guard runs first on every day).

Parent CLI:
  uv run --no-sync python factory/scripts/alpha_retained_score_sizing.py
      [--out DIR] [--resume] [--skip-late] [--days ...]

Usage:
  uv run --no-sync python factory/scripts/alpha_retained_score_sizing.py
      # full 250 validation (2023) + 332 late (2025-02..2026-05) days
  uv run --no-sync python factory/scripts/alpha_retained_score_sizing.py --resume
      # resume from the per-day states / replay parts already on disk
  uv run --no-sync python factory/scripts/alpha_retained_score_sizing.py --skip-late
      # stop after the frozen selection (no late panel day is read at all)
  uv run --no-sync python factory/scripts/alpha_retained_score_sizing.py --days 2023-05-15
      # smoke subset (debug only)
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
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))

from alpha_open_learned import (
    FEATURES_ALL,
    PANEL_ROOT,
    bootstrap_daily,
    feature_matrix,
    sha256_file,
)
from alpha_open_panel import allowed
from alpha_open_sim import causal_liquidity, period
from alpha_sparse_daily import day_context

# ----- fixed configuration (no HPO, no refit, no grid search) ------------------
HEAD = 60  # the one stored payoff head ever loaded
THETA = 0.030  # unchanged retained admission bar on the model's own prediction
COOLDOWN_MIN = 15  # flat minutes after the ACTUAL exit minute
MAX_ATTEMPTS = 3  # funded intents per ticker per session
RESERVE_USD = 3_000.0  # cash reserve, restored every session (research normalization)
PER_POSITION_CAP_USD = 1_000.0  # per-position cap on every cell
PROP_MIN_TICKET_USD = 100.0  # score-proportional floor, never a re-fit knob
PROP_MAX_TICKET_USD = PER_POSITION_CAP_USD  # score-proportional cap
SESSION_START_MIN = 570  # RTH open anchor the panel producer requires (et[0] == 570)
# Reported round-trip modeled-friction ladder (bps, both legs): a COMPARISON SET, never a
# hurdle; no scenario closes a candidate and no rung falsifies anything.
COST_SCENARIOS_BPS = (25.0, 50.0, 75.0, 100.0, 125.0, 150.0)
# Historical diagnostic only: the 200 bps rung the stored late ladder already carried
# (alpha_open_learned.CONFIRM_COSTS). Reported for continuity, never a modern fee claim.
HISTORICAL_COST_BPS = 200.0
COST_LADDER_BPS = COST_SCENARIOS_BPS + (HISTORICAL_COST_BPS,)  # validation + late ladder
SELECT_COST_BPS = 25.0  # the single pre-declared selection rung (validation only)
TRADING_DAYS_PER_YEAR = 252  # simple session-count convention, NOT a CAGR
VAL_BLOCK = "validation"
LATE_BLOCK = "confirmation"
EXPECTED_DAYS = {VAL_BLOCK: 250, LATE_BLOCK: 332}
PANEL_DAYS = PANEL_ROOT / "days"
PANEL_CONTRACT = PANEL_ROOT / "contract.json"
MODEL_DIR = PANEL_ROOT / "learned" / "models"
MODEL_PATH = MODEL_DIR / f"payoff_h{HEAD}.joblib"
MODEL_SHA256 = "c5493c6c043a22e544ca98aa4fb5620292d4a1aeb370f63f6492907334f0bf96"
OUTPUT = PANEL_ROOT / "retained_score_sizing"
STATUS = "DISCOVERY-NOT-VALIDATED"
VERSION = 1
STATES_SCHEMA = 1
REPLAY_SCHEMA = 1
PROTECTED_UNREAD = ["2024", "2025-01", "2026-06", "2026-07", "2026-08"]
REFERENCE_CELL = "flat1000_s3"
BOOT_N = 1_000
BOOT_SEED = 20261009


# ----- the nine fixed sizing cells ---------------------------------------------
@dataclass(frozen=True)
class Cell:
    """One pre-declared sizing shape: a ticket rule plus a concurrent-slot cap.

    ``ticket_usd`` is the flat ticket for ``shape="flat"``; ``scale`` is dollars per unit of
    prediction above theta for ``shape="prop"`` (ticket = clamp(scale * max(score - theta,
    0), $100, $1,000)). Every cell runs on the SAME $3,000 cash reserve with the SAME
    $1,000 per-position cap; the slot count is only a concurrency allowance, so a wider
    book holds more, smaller positions inside the identical reserve.
    """

    key: str
    shape: str  # "flat" | "prop"
    ticket_usd: float | None
    scale: float | None
    max_slots: int
    is_reference: bool = False

    def ticket(self, score: float) -> float:
        """Causal research ticket in dollars for one model score (never outcome-fed)."""
        if self.shape == "flat":
            return float(self.ticket_usd)
        raw = float(self.scale) * max(float(score) - THETA, 0.0)
        return min(max(raw, PROP_MIN_TICKET_USD), PROP_MAX_TICKET_USD)

    @property
    def label(self) -> str:
        bar = f"h{HEAD}_thr{int(round(THETA * 10_000))}bps"
        if self.shape == "flat":
            return f"{bar}_flat{int(self.ticket_usd)}_s{self.max_slots}"
        return f"{bar}_prop{int(self.scale)}_s{self.max_slots}"


CELLS = (
    Cell("flat1000_s3", "flat", 1_000.0, None, 3, is_reference=True),
    Cell("flat500_s6", "flat", 500.0, None, 6),
    Cell("flat250_s12", "flat", 250.0, None, 12),
    Cell("prop2000_s3", "prop", None, 2_000.0, 3),
    Cell("prop4000_s3", "prop", None, 4_000.0, 3),
    Cell("prop8000_s3", "prop", None, 8_000.0, 3),
    Cell("prop2000_s6", "prop", None, 2_000.0, 6),
    Cell("prop4000_s6", "prop", None, 4_000.0, 6),
    Cell("prop8000_s6", "prop", None, 8_000.0, 6),
)
CELLS_BY_KEY = {c.key: c for c in CELLS}
REFERENCE = CELLS_BY_KEY[REFERENCE_CELL]

FILL_SEMANTICS = (
    "every fill in this study is a MINUTE-OPEN PROXY: the panel's stored minute-open entry "
    "price and its stored first-actual-minute-open exit label at the h60 horizon. Fills are "
    "NOT actual quotes, NOT exchange fills, NOT a fill guarantee and NOT a capacity claim; "
    "displayed book depth is not modelled and $250-$1,000 tickets are book sizes on a "
    "research reserve"
)

CONTRACT = {
    "version": VERSION,
    "study": "alpha_retained_score_sizing",
    "label": (
        "retained score-proportional SIZING grid on the immutable learned h60 top-gainer "
        "head: one score bar, one exit, one cadence, nine pre-declared ticket/slot shapes "
        "on the same $3,000 funded reserve"
    ),
    "status": STATUS,
    "hypothesis": (
        "concentrating capital in the strongest forecasts (score-proportional tickets up to "
        "the $1,000 cap) and widening the book into more, smaller concurrent positions beat "
        "the frozen flat $1,000 x 3-slot book on 2023 validation known contribution per "
        "calendar day at the 25 bps modeled-friction rung, on identical signals and rules"
    ),
    "question_answered": (
        "does concentrating capital in the strongest forecasts beat equal tickets, and does "
        "a wider shallower book capture more of the same signals?"
    ),
    "fill_semantics": FILL_SEMANTICS,
    "periods": {
        VAL_BLOCK: "2023-01-01..2023-12-31 (all 250 allowed panel days; selection only)",
        LATE_BLOCK: (
            "2025-02-01..2026-05-31 (all 332 allowed panel days, previously explored, "
            "NOT pristine; transparency only, choice already frozen)"
        ),
    },
    "protected_unread": PROTECTED_UNREAD,
    "model": {
        "refit": False,
        "hpo": False,
        "feature_changes": False,
        "new_features": False,
        "ticker_or_date_features": False,
        "loaded_from": str(MODEL_PATH),
        "expected_sha256": MODEL_SHA256,
        "heads": [HEAD],
        "feature_order": list(FEATURES_ALL),
        "feature_matrix_helper": "alpha_open_learned.feature_matrix (Float64, null-filled)",
        "fit_block": "2021-02..2022-12 (never replayed, never re-scored as outcomes)",
        "target": "panel minute-open gross payoff label gross_60",
        "clip_fit": [-0.5, 2.0],
        "scored_once_per_day_shared_by_all_cells": True,
    },
    "sizing_grid": {
        "reserve_usd": RESERVE_USD,
        "reserve_rule": (
            "a $3,000 cash reserve restored every session (research normalization, NOT a "
            "self-financing return); PnL recycles inside the session and resets at the close"
        ),
        "per_position_cap_usd": PER_POSITION_CAP_USD,
        "quantity_rule": "q = floor(ticket / entry_open) whole shares; notional = q * entry_open",
        "no_order_rule": (
            "a causal q == 0 (or an unusable sizing price) is a KNOWN NO ORDER: no order is "
            "sent, the cash stays unfilled, the intent is never a priced zero-return fill "
            "and never an UNKNOWN, and it does not consume an attempt"
        ),
        "unfilled_sizing_rule": (
            "an intent whose entry bar never printed (panel entry_status=unfilled_expired, "
            "entry_open absent) is sized from the causal last-completed close carried by the "
            "panel's own past-only log_price feature, so its reservation, attempt count and "
            "one-minute fee-free release mirror the frozen reference; it is never a fill"
        ),
        "cells": [
            {
                "cell_key": c.key,
                "label": c.label,
                "shape": c.shape,
                "ticket_usd": c.ticket_usd,
                "scale_usd_per_unit_prediction": c.scale,
                "ticket_rule": (
                    f"flat ${int(c.ticket_usd)}"
                    if c.shape == "flat"
                    else (
                        f"clamp({int(c.scale)} * max(score - {THETA}, 0), "
                        f"${int(PROP_MIN_TICKET_USD)}, ${int(PER_POSITION_CAP_USD)})"
                    )
                ),
                "max_slots": c.max_slots,
                "is_reference": bool(c.is_reference),
            }
            for c in CELLS
        ],
        "reference_cell": REFERENCE_CELL,
        "n_cells": len(CELLS),
        "fixed_before_any_validation_outcome": True,
    },
    "book": {
        "slots": "per cell (3 / 6 / 12)",
        "one_position_per_ticker": True,
        "cooldown": (
            f"flat {COOLDOWN_MIN} minutes after the ACTUAL exit minute; an UNKNOWN exit "
            "blocks its ticker for the rest of the session"
        ),
        "max_funded_intents_per_ticker_per_day": MAX_ATTEMPTS,
        "funding_order": (
            "simultaneous same-minute candidates are funded in DESCENDING model score "
            "AFTER the cell-independent gates (one position per ticker, cooldown, attempt "
            "cap, causal whole-share quantity) and BEFORE any fill outcome of that clock is "
            "observed; no same-clock substitution after an unfilled or UNKNOWN outcome"
        ),
        "cash_reservation": (
            "the deployed notional leaves the reserve at funding; exit proceeds return at "
            "the ACTUAL exit minute (known), a one-minute later release (unfilled), or "
            "never within the session (UNKNOWN, whose cash is charged as a full loss at the "
            "day's end in the separately labeled lower bound)"
        ),
        "same_clock_exits_before_buys": True,
        "leverage": False,
        "fees_both_legs": True,
        "all_calendar_days_retained": True,
        "unknown_rule": (
            "an UNKNOWN exit keeps its slot and its cash to the session end and is charged "
            "its full deployed notional in the full-loss lower bound; it is NEVER cash and "
            "never blended into the known-contribution objective"
        ),
    },
    "cost": {
        "ladder_bps": [float(c) for c in COST_LADDER_BPS],
        "scenarios_bps": [float(c) for c in COST_SCENARIOS_BPS],
        "historical_diagnostic_bps": [HISTORICAL_COST_BPS],
        "selection_rung_bps": SELECT_COST_BPS,
        "nature": (
            "TOTAL round-trip modeled friction on the minute-open proxy prices, charged on "
            "both legs exactly as the frozen reference (net = (1+gross)*(1-side)/(1+side)-1, "
            "side = bps/20000). The ladder is a COMPARISON SET, never a hurdle: no rung "
            "closes a candidate and no rung falsifies anything; 100-150 bps is not a "
            "profitability bar"
        ),
        "fee_claim": (
            "NOT a broker-fee claim: a primary US broker's regular schedule (zero "
            "commission, SEC 20.6 per $1M, CAT 0.000003 per side, daily cent rounding) is "
            "typically well under 1 bp on these tickets; actual provider fees are sourced "
            "separately and stay UNKNOWN here, so the low rungs describe a LOW-FEE "
            "OPPORTUNITY set"
        ),
        "per_rung_full_replay": (
            "each rung is its own full replay, so the intra-session cash path (exit "
            "proceeds recycling into later same-day funding) is internally consistent with "
            "that rung's friction"
        ),
    },
    "selection": {
        "objective": (
            "2023 validation KNOWN-contribution dollars per full calendar day at the "
            f"{int(SELECT_COST_BPS)} bps rung; unknowns are excluded from the numerator and "
            "reported beside it"
        ),
        "basis": (
            "PARTIAL: the full-loss lower bound over unknowns is a coded convention, NEVER "
            "the objective, NEVER a veto and NEVER an expected loss"
        ),
        "synthetic_unknown_loss_veto": False,
        "power_floors": None,
        "median_or_tail_gates": None,
        "cost_or_count_kill": None,
        "tie_break": (
            "(- validation known $ per calendar day at 25bps, - known fills, "
            "+ funded intents, cell key)"
        ),
        "frozen_before_late_inspection": True,
        "freeze_artifacts": [
            "contract.json (the complete fixed grid + cost ladder, before any outcome)",
            "selection_freeze.json (the validation-only choice, before any late day is read)",
        ],
        "late_block_is_transparency_only": True,
        "chosen_cell_immutable_after_freeze": True,
        "reference_delta": (
            "every cell reports an explicit delta against the frozen flat1000_s3 reference "
            "cell on the identical basis (same block, same rung, same rules)"
        ),
    },
    "accounting": {
        "known_usd_per_calendar_day": (
            "sum of per-fill net dollars / every calendar day of the block"
        ),
        "known_usd_per_calendar_day_per_1000_reserve": (
            "known $ per calendar day x 1000 / 3000; every cell runs on the SAME $3,000 "
            "reserve, so this is a fixed-basis restatement and mean/peak deployed capital "
            "carries the capital-efficiency comparison"
        ),
        "annualization": (
            f"mean daily known contribution x {TRADING_DAYS_PER_YEAR} sessions; a simple "
            "session-count convention on a daily-reset research book, NOT a CAGR and NOT an "
            "account claim"
        ),
        "full_loss_lower_bound": (
            "known contribution minus the full deployed notional of every UNKNOWN fill, "
            "reported separately per rung and never blended into the known figures"
        ),
        "deployed_capital": (
            "deployed_notional_usd is the sum of reserved deployment; mean_deployed_usd and "
            "mean_positions_open are time-weighted over the RTH session [570, session_end]; "
            "peak_deployed_usd is the largest simultaneous cash commitment"
        ),
        "traded_days": (
            "days with at least one funded intent that opened a position (known or UNKNOWN)"
        ),
        "positive_months": (
            "months whose KNOWN contribution at the 25 bps rung is strictly positive"
        ),
    },
    "labels": {
        "horizons": [HEAD],
        "source": "panel stored gross_60 / exit_et_60 / exit_status_60 labels (no recompute)",
        "unknown_pending": "the exit bar is absent: never cash, never dropped",
        "unfilled_cash": (
            "the entry bar is absent: reserve-and-release, fee-free, counts an attempt"
        ),
    },
    "resume": {
        "hash_recipe": "sha256(producer sha + contract sha + model sha + day + schema)",
        "states_stage": "states/<day>.parquet + states/<day>.cov.json, atomic per day",
        "replay_stage": (
            "replay/<block>/<day>.parquet + .trades.parquet + .cov.json, atomic per day; a "
            "replay part additionally records the scored-states sha it consumed and is "
            "reused only when that sha still matches"
        ),
        "bounded_memory": "one panel day / one states part / one replay part resident at a time",
        "progress_every_days": 25,
    },
    "provenance": {
        "panel_root": str(PANEL_ROOT),
        "panel_days_root": str(PANEL_DAYS),
        "panel_contract_sha256": (sha256_file(PANEL_CONTRACT) if PANEL_CONTRACT.exists() else None),
        "output_root": str(OUTPUT),
    },
}


# ----- shared helpers ---------------------------------------------------------
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


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=1, default=_default) + "\n")
    os.replace(tmp, path)


def block_days(days: list[str] | None, block: str) -> list[str]:
    """All allowed panel days of one research block (``days`` restricts for smoke runs)."""
    keep = set(days) if days else None
    out = []
    for path in sorted(PANEL_DAYS.glob("????-??-??.parquet")):
        day = path.stem
        if not allowed(day) or period(day) != block:
            continue
        if keep is not None and day not in keep:
            continue
        out.append(day)
    return out


# ----- immutable stored model -------------------------------------------------
def load_stored_model() -> tuple[dict, dict]:
    """Load the one immutable head; verify order/horizon/sha; disclose every fit fact."""
    if not MODEL_PATH.exists():
        raise SystemExit(f"[retained-sizing] stored model missing: {MODEL_PATH}")
    bundle = joblib.load(MODEL_PATH)
    order = list(bundle.get("feature_order") or [])
    if order != list(FEATURES_ALL):
        raise SystemExit(
            f"[retained-sizing] h{HEAD} stored feature_order != current FEATURES_ALL "
            f"({len(order)} vs {len(FEATURES_ALL)})"
        )
    if int(bundle.get("horizon", -1)) != HEAD:
        raise SystemExit(
            f"[retained-sizing] h{HEAD} stored horizon {bundle.get('horizon')} != {HEAD}"
        )
    booster = bundle["lgbm"]
    n_feat = int(getattr(booster, "n_features_in_", 0) or len(order))
    if n_feat != len(order):
        raise SystemExit(
            f"[retained-sizing] h{HEAD} booster feature count {n_feat} != {len(order)}"
        )
    sha = sha256_file(MODEL_PATH)
    if sha != MODEL_SHA256:
        raise SystemExit(
            f"[retained-sizing] stored model sha256 {sha} != pinned {MODEL_SHA256}; "
            "the immutable head changed and this lane refuses to replay it"
        )
    report = {
        f"h{HEAD}": {
            "path": str(MODEL_PATH),
            "sha256": sha,
            "horizon": HEAD,
            "feature_order": order,
            "n_features": n_feat,
            "params": bundle.get("params"),
            "clip_fit": bundle.get("clip_fit"),
            "seed": bundle.get("seed"),
            "refit": False,
        },
        "training_immutability": {
            "fitted_on": "2021-02..2022-12 panel days, t%15==0, filled_proxy entries, known labels",
            "replayed_or_rescored_as_outcomes": False,
            "refit": False,
            "hpo": False,
            "feature_changes": False,
            "ticker_or_date_features": False,
        },
        "scoring": (
            "ONE predict per day over the liquidity-qualified panel states; the identical "
            "scored states feed every cell, so no cell is a rescale of another's fills"
        ),
    }
    return bundle, report


# ----- per-day scored states (atomic, resumable) ------------------------------
STATE_COLUMNS = (
    "day",
    "ticker",
    "t",
    "entry_et",
    "entry_open",
    "entry_status",
    "session_end",
    "gross_60",
    "exit_et_60",
    "exit_status_60",
    "score",
    "size_ref_price",
    "size_ref_source",
)
STATE_TYPES = {
    "day": pl.String,
    "ticker": pl.String,
    "t": pl.Int64,
    "entry_et": pl.Int64,
    "entry_open": pl.Float64,
    "entry_status": pl.String,
    "session_end": pl.Int64,
    "gross_60": pl.Float64,
    "exit_et_60": pl.Int64,
    "exit_status_60": pl.String,
    "score": pl.Float64,
    "size_ref_price": pl.Float64,
    "size_ref_source": pl.String,
}


def empty_states() -> pl.DataFrame:
    return pl.DataFrame(schema={c: STATE_TYPES[c] for c in STATE_COLUMNS})


def state_paths(out_dir: Path, day: str) -> tuple[Path, Path]:
    return out_dir / "states" / f"{day}.parquet", out_dir / "states" / f"{day}.cov.json"


def score_day(day: str, bundle: dict) -> tuple[pl.DataFrame, dict]:
    """Score one session's liquidity-qualified states; keep the thr-clearing rows only.

    One panel frame is resident at a time and the full corpus is never concatenated. The
    sizing reference price is causal for every row: the minute-open entry price when the
    entry bar printed, else the last completed close the panel's own past-only ``log_price``
    feature carries (an ``unfilled_expired`` intent still reserves and releases its ticket
    exactly like the frozen reference, and is never a fill).
    """
    if not allowed(day):
        raise ValueError(f"protected/out-of-scope day refused: {day}")
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
        "signals": 0,
        "label_status_counts": {},
        "size_ref_source_counts": {},
    }
    if not cand.height:
        return empty_states(), cov
    tickers = sorted(set(cand["ticker"].to_list()))
    cov["tickers_qualified"] = len(tickers)
    preds = np.asarray(bundle["lgbm"].predict(feature_matrix(cand)), dtype=float)
    sig = cand.with_columns(pl.Series("score", preds)).filter(pl.col("score") >= THETA)
    cov["signals"] = int(sig.height)
    if not sig.height:
        return empty_states(), cov
    labels = sig["exit_status_60"].to_list()
    cov["label_status_counts"] = {k: int(labels.count(k)) for k in sorted(set(labels))}
    rows = sig.select(
        [
            "day",
            "ticker",
            "t",
            "entry_et",
            "entry_open",
            "entry_status",
            "session_end",
            "gross_60",
            "exit_et_60",
            "exit_status_60",
            "score",
            "log_price",
        ]
    ).to_dicts()
    out: list[dict] = []
    source_counts: dict[str, int] = {}
    for r in rows:
        entry_open = r["entry_open"]
        if entry_open is not None and math.isfinite(float(entry_open)) and float(entry_open) > 0.0:
            price, source = float(entry_open), "entry_open"
        else:
            lp = r["log_price"]
            price = (
                float(math.exp(float(lp))) if lp is not None and math.isfinite(float(lp)) else None
            )
            source = "last_completed_close"
        source_counts[source] = source_counts.get(source, 0) + 1
        out.append(
            {
                "day": day,
                "ticker": r["ticker"],
                "t": int(r["t"]),
                "entry_et": int(r["entry_et"]) if r["entry_et"] is not None else None,
                "entry_open": float(entry_open) if entry_open is not None else None,
                "entry_status": r["entry_status"],
                "session_end": int(r["session_end"]),
                "gross_60": float(r["gross_60"]) if r["gross_60"] is not None else None,
                "exit_et_60": int(r["exit_et_60"]) if r["exit_et_60"] is not None else None,
                "exit_status_60": r["exit_status_60"],
                "score": float(r["score"]),
                "size_ref_price": price,
                "size_ref_source": source,
            }
        )
    cov["size_ref_source_counts"] = source_counts
    states = pl.DataFrame(out, schema={c: STATE_TYPES[c] for c in STATE_COLUMNS})
    return states, cov


def score_block(out_dir: Path, bundle: dict, days: list[str], resume: bool) -> dict:
    """Build (or resume) every day's scored states atomically, one day at a time."""
    producer = producer_sha256()
    contract_sha = _digest_bytes(CONTRACT)
    model_sha = sha256_file(MODEL_PATH)
    todo, infos = [], []
    coverage = {
        "days_total": len(days),
        "days_cached": 0,
        "days_scored": 0,
        "qualified_rows": 0,
        "signals": 0,
        "label_status_counts": {},
        "size_ref_source_counts": {},
    }
    label_totals: dict[str, int] = {}
    source_totals: dict[str, int] = {}
    signals_seen = 0
    qualified_seen = 0
    for day in days:
        qpath, cpath = state_paths(out_dir, day)
        expect = _digest_bytes(
            {
                "day": day,
                "producer": producer,
                "contract": contract_sha,
                "model": model_sha,
                "schema": STATES_SCHEMA,
            }
        )
        fresh = False
        if resume and cpath.exists() and qpath.exists():
            try:
                prior = json.loads(cpath.read_text())
                fresh = (
                    prior.get("resume_hash") == expect
                    and prior.get("rows_schema") == STATES_SCHEMA
                    and prior.get("states_sha256") == sha256_file(qpath)
                )
            except (json.JSONDecodeError, OSError):
                fresh = False
        if fresh:
            infos.append({"day": day, "resumed": True, "coverage": prior.get("coverage", {})})
        else:
            todo.append(day)
    for n, day in enumerate(todo, 1):
        states, cov = score_day(day, bundle)
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
                "resume_hash": _digest_bytes(
                    {
                        "day": day,
                        "producer": producer,
                        "contract": contract_sha,
                        "model": model_sha,
                        "schema": STATES_SCHEMA,
                    }
                ),
                "producer_sha256": producer,
                "contract_sha256": contract_sha,
                "model_sha256": model_sha,
                "states_sha256": sha256_file(qpath),
                "rows_schema": STATES_SCHEMA,
                "coverage": cov,
            },
        )
        infos.append({"day": day, "resumed": False, "coverage": cov})
        signals_seen += int(cov["signals"])
        qualified_seen += int(cov["qualified_rows"])
        if n % 25 == 0 or n == len(todo):
            print(
                f"[score] {n}/{len(todo)} days, last {day}, "
                f"signals_today={cov['signals']} signals_total={signals_seen}, "
                f"qualified_rows_today={cov['qualified_rows']} "
                f"qualified_rows_total={qualified_seen}",
                flush=True,
            )
    for info in infos:
        cov = info["coverage"] or {}
        coverage["qualified_rows"] += int(cov.get("qualified_rows", 0))
        coverage["signals"] += int(cov.get("signals", 0))
        for k, v in (cov.get("label_status_counts") or {}).items():
            label_totals[k] = label_totals.get(k, 0) + int(v)
        for k, v in (cov.get("size_ref_source_counts") or {}).items():
            source_totals[k] = source_totals.get(k, 0) + int(v)
    coverage.update(
        {
            "days_cached": len(infos),
            "days_scored": len(todo),
            "label_status_counts": label_totals,
            "size_ref_source_counts": source_totals,
        }
    )
    print(
        f"[score-block] thr-clearing signals={coverage['signals']} over "
        f"{coverage['days_total']} days ({coverage['days_cached']} cached, "
        f"{coverage['days_scored']} scored this run), "
        f"qualified_rows={coverage['qualified_rows']}; the frozen lane's same-day "
        "convention (day_context -> causal_liquidity -> predict -> score >= 0.030) is "
        "unchanged, so this block total is directly comparable to learned_sparse_daily",
        flush=True,
    )
    return coverage


def read_day_states(out_dir: Path, day: str) -> pl.DataFrame:
    path, _ = state_paths(out_dir, day)
    if not path.exists():
        return empty_states()
    return pl.read_parquet(path)


# ----- per-cell causal replay (one rung = one full simulation) -----------------
DAILY_COLUMNS = (
    "day",
    "cell_key",
    "cost_bps",
    "signals",
    "funded_intents",
    "fills",
    "known_fills",
    "unknown_fills",
    "unfilled_fills",
    "no_order_skips",
    "cash_or_slot_skips",
    "slot_busy_skips",
    "cooldown_skips",
    "max_attempt_skips",
    "known_usd",
    "lower_bound_usd",
    "deployed_notional_usd",
    "peak_deployed_usd",
    "deployed_area_usd_min",
    "session_span_min",
    "positions_area_min",
    "positions_peak",
)
DAILY_TYPES = {
    "day": pl.String,
    "cell_key": pl.String,
    "cost_bps": pl.Float64,
    "signals": pl.Int64,
    "funded_intents": pl.Int64,
    "fills": pl.Int64,
    "known_fills": pl.Int64,
    "unknown_fills": pl.Int64,
    "unfilled_fills": pl.Int64,
    "no_order_skips": pl.Int64,
    "cash_or_slot_skips": pl.Int64,
    "slot_busy_skips": pl.Int64,
    "cooldown_skips": pl.Int64,
    "max_attempt_skips": pl.Int64,
    "known_usd": pl.Float64,
    "lower_bound_usd": pl.Float64,
    "deployed_notional_usd": pl.Float64,
    "peak_deployed_usd": pl.Float64,
    "deployed_area_usd_min": pl.Float64,
    "session_span_min": pl.Int64,
    "positions_area_min": pl.Float64,
    "positions_peak": pl.Int64,
}
TRADE_COLUMNS = (
    "day",
    "cell_key",
    "cost_bps",
    "ticker",
    "t",
    "score",
    "ticket_usd",
    "qty",
    "size_ref_price",
    "size_ref_source",
    "notional_usd",
    "gross_60",
    "net_usd",
    "outcome",
    "exit_et_60",
    "attempt_index",
    "positions_at_entry",
)
TRADE_TYPES = {
    "day": pl.String,
    "cell_key": pl.String,
    "cost_bps": pl.Float64,
    "ticker": pl.String,
    "t": pl.Int64,
    "score": pl.Float64,
    "ticket_usd": pl.Float64,
    "qty": pl.Int64,
    "size_ref_price": pl.Float64,
    "size_ref_source": pl.String,
    "notional_usd": pl.Float64,
    "gross_60": pl.Float64,
    "net_usd": pl.Float64,
    "outcome": pl.String,
    "exit_et_60": pl.Int64,
    "attempt_index": pl.Int64,
    "positions_at_entry": pl.Int64,
}


def empty_daily() -> pl.DataFrame:
    return pl.DataFrame(schema=dict(DAILY_TYPES))


def empty_trades() -> pl.DataFrame:
    return pl.DataFrame(schema=dict(TRADE_TYPES))


def replay_day(
    day: str, states: pl.DataFrame, cell: Cell, cost_bps: float
) -> tuple[dict, list[dict]]:
    """One cell, one session, one modeled-friction rung: the funded book replay.

    Chronological by signal minute; the highest-forecast eligible candidates of a minute are
    funded (cash reserved first) BEFORE any fill outcome of that clock is observed, so a
    later same-minute candidate is never substituted for an unfilled or UNKNOWN one. Gates
    in order: one position per ticker, the flat cooldown after the ACTUAL exit, the
    per-ticker attempt cap, the causal whole-share quantity (q == 0 is a known no-order that
    never sends an order), then the cell's slot count and the reserve. Same-clock exits
    release cash before buys. Known exits return their net proceeds at the ACTUAL exit
    minute; an unfilled-expired attempt releases its reservation one minute later, fee-free;
    an UNKNOWN exit never releases inside the session and is charged its full deployed
    notional in the separately labeled lower bound.
    """
    row = dict.fromkeys(DAILY_COLUMNS, 0)
    row.update(
        {
            "day": day,
            "cell_key": cell.key,
            "cost_bps": float(cost_bps),
            "known_usd": 0.0,
            "lower_bound_usd": 0.0,
            "deployed_notional_usd": 0.0,
            "peak_deployed_usd": 0.0,
            "deployed_area_usd_min": 0.0,
            "session_span_min": 0,
            "positions_area_min": 0.0,
            "positions_peak": 0,
        }
    )
    trades: list[dict] = []
    if not states.height:
        return row, trades
    side = float(cost_bps) / 20_000.0
    session_end = int(states["session_end"][0])
    row["session_span_min"] = max(0, session_end - SESSION_START_MIN)
    candidates = states.sort(["t", "score", "ticker"], descending=[False, True, False])
    row["signals"] = int(candidates.height)
    groups: dict[int, list[dict]] = {}
    for r in candidates.iter_rows(named=True):
        groups.setdefault(int(r["t"]), []).append(r)

    cash = RESERVE_USD
    min_cash = RESERVE_USD
    active: set[str] = set()
    blocked: dict[str, int] = {}
    used: dict[str, int] = {}
    queue: list[tuple] = []
    events: dict[int, list[tuple[float, int]]] = {}
    seq = 0
    unknown_notional = 0.0

    def note_event(minute: int, d_notional: float, d_positions: int) -> None:
        events.setdefault(min(int(minute), session_end), []).append((d_notional, d_positions))

    for t in sorted(groups):
        while queue and queue[0][0] <= t:
            release, _, sym, proceeds, notional = heapq.heappop(queue)
            active.discard(sym)
            cash += proceeds
            note_event(release, -notional, -1)
        admitted: list[tuple[dict, int, float]] = []
        for r in groups[t]:
            sym = r["ticker"]
            if sym in active:
                row["slot_busy_skips"] += 1
                continue
            if t < int(blocked.get(sym, -1)):
                row["cooldown_skips"] += 1
                continue
            if int(used.get(sym, 0)) >= MAX_ATTEMPTS:
                row["max_attempt_skips"] += 1
                continue
            ticket = cell.ticket(float(r["score"]))
            price = r["size_ref_price"]
            qty = 0
            if price is not None and math.isfinite(float(price)) and float(price) > 0.0:
                qty = int(math.floor(ticket / float(price)))
            if qty <= 0:
                # A causal zero-share order is a KNOWN NO ORDER: no order sent, cash stays
                # unfilled, never a priced zero-return fill and never an UNKNOWN.
                row["no_order_skips"] += 1
                continue
            notional = qty * float(price)
            if len(active) >= cell.max_slots or cash + 1e-8 < notional:
                row["cash_or_slot_skips"] += 1
                continue
            used[sym] = int(used.get(sym, 0)) + 1
            row["funded_intents"] += 1
            row["deployed_notional_usd"] += notional
            cash -= notional
            min_cash = min(min_cash, cash)
            active.add(sym)
            note_event(t, notional, 1)
            admitted.append((r, qty, notional))
        for r, qty, notional in admitted:
            base = {
                "day": day,
                "cell_key": cell.key,
                "cost_bps": float(cost_bps),
                "ticker": r["ticker"],
                "t": int(r["t"]),
                "score": float(r["score"]),
                "ticket_usd": cell.ticket(float(r["score"])),
                "qty": int(qty),
                "size_ref_price": (
                    float(r["size_ref_price"]) if r["size_ref_price"] is not None else None
                ),
                "size_ref_source": r["size_ref_source"],
                "notional_usd": notional,
                "gross_60": None,
                "net_usd": None,
                "outcome": None,
                "exit_et_60": None,
                "attempt_index": int(used[r["ticker"]]),
                "positions_at_entry": len(active),
            }
            if r["entry_status"] == "unfilled_expired":
                # No position opened: the attempt reserves the ticket and releases it one
                # minute later, fee-free. No exit => no cooldown; the attempt still counts.
                row["unfilled_fills"] += 1
                heapq.heappush(queue, (int(r["t"]) + 1, seq, r["ticker"], notional, notional))
                seq += 1
                base["outcome"] = "unfilled_expired"
                trades.append(base)
                continue
            row["fills"] += 1
            gross, exit_et = r["gross_60"], r["exit_et_60"]
            if gross is None or exit_et is None:
                row["unknown_fills"] += 1
                unknown_notional += notional
                release = session_end + 1
                heapq.heappush(queue, (release, seq, r["ticker"], 0.0, notional))
                seq += 1
                blocked[r["ticker"]] = release + COOLDOWN_MIN
                base["outcome"] = "unknown_pending"
                trades.append(base)
                continue
            g = float(gross)
            net = (1.0 + g) * (1.0 - side) / (1.0 + side) - 1.0
            row["known_fills"] += 1
            row["known_usd"] += notional * net
            release = int(exit_et)
            heapq.heappush(queue, (release, seq, r["ticker"], notional * (1.0 + net), notional))
            seq += 1
            blocked[r["ticker"]] = release + COOLDOWN_MIN
            base["gross_60"] = g
            base["net_usd"] = notional * net
            base["outcome"] = "known_open_proxy"
            base["exit_et_60"] = release
            trades.append(base)
    row["lower_bound_usd"] = row["known_usd"] - unknown_notional
    row["peak_deployed_usd"] = RESERVE_USD - min_cash
    deployed, positions, prev = 0.0, 0, None
    area, pos_area, peak_pos = 0.0, 0.0, 0
    for minute in sorted(events):
        if prev is not None:
            area += deployed * (minute - prev)
            pos_area += positions * (minute - prev)
        for d_notional, d_positions in events[minute]:
            deployed += d_notional
            positions += d_positions
        peak_pos = max(peak_pos, positions)
        prev = minute
    row["deployed_area_usd_min"] = area
    row["positions_area_min"] = pos_area
    row["positions_peak"] = peak_pos
    return row, trades


def replay_paths(out_dir: Path, block: str, day: str) -> tuple[Path, Path, Path]:
    root = out_dir / "replay" / block
    return root / f"{day}.parquet", root / f"{day}.trades.parquet", root / f"{day}.cov.json"


def replay_block(out_dir: Path, block: str, days: list[str], resume: bool) -> dict:
    """Replay every cell at every rung on every day of one block; resumable per-day parts."""
    producer = producer_sha256()
    contract_sha = _digest_bytes(CONTRACT)
    model_sha = sha256_file(MODEL_PATH)
    (out_dir / "replay" / block).mkdir(parents=True, exist_ok=True)
    todo, infos = [], []
    coverage = {
        "block": block,
        "days_total": len(days),
        "days_cached": 0,
        "days_replayed": 0,
        "signals": 0,
        "funded_intents": 0,
        "fills": 0,
        "known_fills": 0,
        "unknown_fills": 0,
        "unfilled_fills": 0,
        "no_order_skips": 0,
        "cash_or_slot_skips": 0,
    }
    for day in days:
        dpath, tpath, cpath = replay_paths(out_dir, block, day)
        _, spath = state_paths(out_dir, day)
        states_sha = None
        if spath.exists():
            try:
                states_sha = json.loads(spath.read_text()).get("states_sha256")
            except (json.JSONDecodeError, OSError):
                states_sha = None
        expect = _digest_bytes(
            {
                "day": day,
                "producer": producer,
                "contract": contract_sha,
                "model": model_sha,
                "schema": REPLAY_SCHEMA,
            }
        )
        fresh = False
        prior = None
        if resume and cpath.exists() and dpath.exists() and tpath.exists():
            try:
                prior = json.loads(cpath.read_text())
                fresh = (
                    prior.get("resume_hash") == expect
                    and prior.get("schema") == REPLAY_SCHEMA
                    and prior.get("states_sha256") == states_sha
                )
            except (json.JSONDecodeError, OSError):
                fresh = False
        if fresh:
            infos.append(
                {"day": day, "resumed": True, "coverage": (prior or {}).get("coverage", {})}
            )
        else:
            todo.append(day)
    funded_seen = 0
    fills_seen = 0
    for n, day in enumerate(todo, 1):
        dpath, tpath, cpath = replay_paths(out_dir, block, day)
        states = read_day_states(out_dir, day)
        _, spath = state_paths(out_dir, day)
        if not spath.exists():
            raise SystemExit(
                f"[retained-sizing] scored states missing for {day} ({spath}); run the score "
                "stage for the whole block before replaying it"
            )
        daily_rows: list[dict] = []
        trade_rows: list[dict] = []
        day_cov: dict[str, dict] = {}
        for cell in CELLS:
            select_daily: dict | None = None
            for rung in COST_LADDER_BPS:
                daily, trades = replay_day(day, states, cell, rung)
                daily_rows.append(daily)
                trade_rows.extend(trades)
                if rung == SELECT_COST_BPS:
                    select_daily = daily
            day_cov[cell.key] = {
                "funded_intents": int((select_daily or daily)["funded_intents"]),
                "fills": int((select_daily or daily)["fills"]),
                "known_fills": int((select_daily or daily)["known_fills"]),
                "known_usd": float((select_daily or daily)["known_usd"]),
                "rung_bps": float(SELECT_COST_BPS),
            }
        daily_frame = pl.DataFrame(daily_rows, schema=dict(DAILY_TYPES))
        trade_frame = (
            pl.DataFrame(trade_rows, schema=dict(TRADE_TYPES)) if trade_rows else empty_trades()
        )
        tmp_d = dpath.with_name(f"{dpath.name}.tmp{os.getpid()}")
        daily_frame.write_parquet(tmp_d)
        os.replace(tmp_d, dpath)
        tmp_t = tpath.with_name(f"{tpath.name}.tmp{os.getpid()}")
        trade_frame.write_parquet(tmp_t)
        os.replace(tmp_t, tpath)
        states_sha = None
        try:
            states_sha = json.loads(spath.read_text()).get("states_sha256")
        except (json.JSONDecodeError, OSError):
            states_sha = None
        write_json_atomic(
            cpath,
            {
                "day": day,
                "resume_hash": _digest_bytes(
                    {
                        "day": day,
                        "producer": producer,
                        "contract": contract_sha,
                        "model": model_sha,
                        "schema": REPLAY_SCHEMA,
                    }
                ),
                "producer_sha256": producer,
                "contract_sha256": contract_sha,
                "model_sha256": model_sha,
                "states_sha256": states_sha,
                "schema": REPLAY_SCHEMA,
                "coverage": {"signals": int(daily_rows[0]["signals"]), "cells": day_cov},
            },
        )
        infos.append(
            {
                "day": day,
                "resumed": False,
                "coverage": {"signals": int(daily_rows[0]["signals"]), "cells": day_cov},
            }
        )
        if n % 25 == 0 or n == len(todo):
            funded_today = sum(v["funded_intents"] for v in day_cov.values())
            fills_today = sum(v["fills"] for v in day_cov.values())
            funded_seen += funded_today
            fills_seen += fills_today
            print(
                f"[replay:{block}] {n}/{len(todo)} days, last {day}, "
                f"signals_today={daily_rows[0]['signals']}, "
                f"funded_intents_today={funded_today} funded_intents_total={funded_seen}, "
                f"fills_today={fills_today} fills_total={fills_seen} "
                f"over {len(CELLS)} cells x {len(COST_LADDER_BPS)} rungs",
                flush=True,
            )
    for info in infos:
        cov = info["coverage"] or {}
        coverage["signals"] += int(cov.get("signals", 0))
        for v in (cov.get("cells") or {}).values():
            coverage["funded_intents"] += int(v.get("funded_intents", 0))
            coverage["fills"] += int(v.get("fills", 0))
    coverage.update({"days_cached": len(infos), "days_replayed": len(todo)})
    print(
        f"[replay-block:{block}] signals={coverage['signals']}, "
        f"funded_intents={coverage['funded_intents']}, fills={coverage['fills']} over "
        f"{coverage['days_total']} days x {len(CELLS)} cells x {len(COST_LADDER_BPS)} rungs "
        f"({coverage['days_cached']} cached, {coverage['days_replayed']} replayed this run)",
        flush=True,
    )
    return coverage


def read_block_frames(out_dir: Path, block: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    """All per-day parts of one block: the daily accounting rows and the funded intents."""
    root = out_dir / "replay" / block
    daily_parts = sorted(root.glob("????-??-??.parquet"))
    trade_parts = sorted(root.glob("????-??-??.trades.parquet"))
    daily = (
        pl.concat([pl.read_parquet(p) for p in daily_parts], how="vertical_relaxed")
        if daily_parts
        else empty_daily()
    )
    trades = (
        pl.concat([pl.read_parquet(p) for p in trade_parts], how="vertical_relaxed")
        if trade_parts
        else empty_trades()
    )
    return daily, trades


# ----- block aggregation ------------------------------------------------------
def monthly_view(daily: pl.DataFrame, rung: float) -> dict:
    """Per-month accounting at one rung: calendar days, fills, known $ and the lower bound."""
    out = {}
    for month in sorted({str(d)[:7] for d in daily["day"].to_list()}):
        rows = daily.filter(pl.col("day").str.starts_with(month))
        out[month] = {
            "days_replayed": int(rows.height),
            "traded_days": int((rows["fills"] > 0).sum()),
            "funded_intents": int(rows["funded_intents"].sum()),
            "fills": int(rows["fills"].sum()),
            "known_fills": int(rows["known_fills"].sum()),
            "unknown_fills": int(rows["unknown_fills"].sum()),
            "unfilled_fills": int(rows["unfilled_fills"].sum()),
            "no_order_skips": int(rows["no_order_skips"].sum()),
            "cash_or_slot_skips": int(rows["cash_or_slot_skips"].sum()),
            "known_usd": float(rows["known_usd"].sum()),
            "full_loss_lower_bound_usd": float(rows["lower_bound_usd"].sum()),
            "deployed_notional_usd": float(rows["deployed_notional_usd"].sum()),
        }
    return out


def yearly_view(daily: pl.DataFrame) -> dict:
    """Per-calendar-year accounting at the selection rung (the late block spans two years)."""
    out = {}
    for year in sorted({str(d)[:4] for d in daily["day"].to_list()}):
        rows = daily.filter(pl.col("day").str.starts_with(year))
        out[year] = {
            "days_replayed": int(rows.height),
            "traded_days": int((rows["fills"] > 0).sum()),
            "funded_intents": int(rows["funded_intents"].sum()),
            "fills": int(rows["fills"].sum()),
            "known_fills": int(rows["known_fills"].sum()),
            "unknown_fills": int(rows["unknown_fills"].sum()),
            "unfilled_fills": int(rows["unfilled_fills"].sum()),
            "known_usd": float(rows["known_usd"].sum()),
            "full_loss_lower_bound_usd": float(rows["lower_bound_usd"].sum()),
            "deployed_notional_usd": float(rows["deployed_notional_usd"].sum()),
        }
    return out


def rung_report(rows: pl.DataFrame, trades: pl.DataFrame, rung: float, n_days: int) -> dict:
    """Every reported number for one (cell, rung) on one block's full calendar."""
    known_total = float(rows["known_usd"].sum())
    bound_total = float(rows["lower_bound_usd"].sum())
    deployed_total = float(rows["deployed_notional_usd"].sum())
    per_day = known_total / n_days
    known_trades = trades.filter(pl.col("outcome") == "known_open_proxy")
    nets = known_trades["net_usd"].to_list() if known_trades.height else []
    rates = (
        [
            float(n) / float(x)
            for n, x in zip(
                known_trades["net_usd"].to_list(),
                known_trades["notional_usd"].to_list(),
                strict=True,
            )
            if x
        ]
        if known_trades.height
        else []
    )
    wins = float(sum(v for v in nets if v > 0))
    losses = float(-sum(v for v in nets if v < 0))
    span = rows["session_span_min"].to_list()
    area = rows["deployed_area_usd_min"].to_list()
    pos_area = rows["positions_area_min"].to_list()
    mean_deployed = (
        float(np.mean([a / s if s else 0.0 for a, s in zip(area, span, strict=True)]))
        if span
        else 0.0
    )
    mean_positions = (
        float(np.mean([a / s if s else 0.0 for a, s in zip(pos_area, span, strict=True)]))
        if span
        else 0.0
    )
    return {
        "funded_intents": int(rows["funded_intents"].sum()),
        "fills": int(rows["fills"].sum()),
        "known_fills": int(rows["known_fills"].sum()),
        "unknown_fills": int(rows["unknown_fills"].sum()),
        "unfilled_fills": int(rows["unfilled_fills"].sum()),
        "no_order_skips": int(rows["no_order_skips"].sum()),
        "cash_or_slot_skips": int(rows["cash_or_slot_skips"].sum()),
        "slot_busy_skips": int(rows["slot_busy_skips"].sum()),
        "cooldown_skips": int(rows["cooldown_skips"].sum()),
        "max_attempt_skips": int(rows["max_attempt_skips"].sum()),
        "traded_days": int((rows["fills"] > 0).sum()),
        "known_traded_days": int((rows["known_fills"] > 0).sum()),
        "known_usd": known_total,
        "known_usd_per_calendar_day": per_day,
        "known_usd_per_calendar_day_per_1000_reserve": per_day * 1_000.0 / RESERVE_USD,
        "known_usd_per_year_252_sessions": per_day * TRADING_DAYS_PER_YEAR,
        "full_loss_lower_bound_usd": bound_total,
        "full_loss_lower_bound_usd_per_calendar_day": bound_total / n_days,
        "full_loss_lower_bound_note": (
            "known contribution minus the FULL deployed notional of every UNKNOWN fill; a "
            "separately labeled coding convention over unknowns, NEVER blended into the "
            "known figures and NEVER an expected loss"
        ),
        "mean_net_usd_per_known_fill": float(np.mean(nets)) if nets else None,
        "mean_net_rate_per_known_fill": float(np.mean(rates)) if rates else None,
        "known_win_rate": float(np.mean([v > 0 for v in nets])) if nets else None,
        "known_profit_factor": (wins / losses) if losses else None,
        "worst_known_fill_usd": float(np.min(nets)) if nets else None,
        "deployed_notional_usd": deployed_total,
        "deployed_usd_per_calendar_day": deployed_total / n_days,
        "mean_deployed_usd": mean_deployed,
        "peak_deployed_usd_mean": float(rows["peak_deployed_usd"].mean()),
        "peak_deployed_usd_max": float(rows["peak_deployed_usd"].max()),
        "mean_positions_open": mean_positions,
        "peak_positions": int(rows["positions_peak"].max()),
        "known_usd_per_deployed_dollar": (known_total / deployed_total) if deployed_total else None,
        "fill_stats_are_minute_open_proxies": True,
        "bootstrap_daily_known_usd": bootstrap_daily(
            rows["known_usd"].to_list(), n=BOOT_N, seed=BOOT_SEED
        ),
    }


def cell_report(daily: pl.DataFrame, trades: pl.DataFrame, cell: Cell, n_days: int) -> dict:
    """The full per-cell surface of one block: every rung plus monthly/yearly accounting."""
    cell_daily = daily.filter(pl.col("cell_key") == cell.key)
    cell_trades = trades.filter(pl.col("cell_key") == cell.key)
    per_rung = {}
    for rung in COST_LADDER_BPS:
        rows = cell_daily.filter(pl.col("cost_bps") == rung)
        if int(rows.height) != n_days:
            raise ValueError(
                f"cell {cell.key} rung {int(rung)}: {rows.height} daily rows on disk for "
                f"{n_days} calendar days; replay the whole block before aggregating"
            )
        per_rung[str(int(rung))] = rung_report(
            rows, cell_trades.filter(pl.col("cost_bps") == rung), rung, n_days
        )
    sel_rows = cell_daily.filter(pl.col("cost_bps") == SELECT_COST_BPS)
    sel_monthly = monthly_view(sel_rows, SELECT_COST_BPS)
    return {
        "cell_key": cell.key,
        "label": cell.label,
        "shape": cell.shape,
        "ticket_usd": cell.ticket_usd,
        "scale_usd_per_unit_prediction": cell.scale,
        "max_slots": cell.max_slots,
        "is_reference": bool(cell.is_reference),
        "days_replayed": n_days,
        "signals": int(sel_rows["signals"].sum()),
        "per_rung": per_rung,
        "positive_months_known_25": int(sum(v["known_usd"] > 0 for v in sel_monthly.values())),
        "positive_months_full_loss_lower_bound_25": int(
            sum(v["full_loss_lower_bound_usd"] > 0 for v in sel_monthly.values())
        ),
        "months": len(sel_monthly),
        "monthly_25bps": sel_monthly,
        "yearly_25bps": yearly_view(sel_rows),
        "fills_are_minute_open_proxies": True,
        "selection_rung_bps": float(SELECT_COST_BPS),
        "reference_cell": REFERENCE_CELL,
    }


def reference_delta(cell: dict, ref: dict) -> dict:
    """Explicit delta of one cell vs the frozen flat1000_s3 reference on the identical basis."""
    per_rung = {}
    for key, rep in cell["per_rung"].items():
        r = ref["per_rung"][key]
        per_rung[key] = {
            "delta_known_usd": rep["known_usd"] - r["known_usd"],
            "delta_known_usd_per_calendar_day": (
                rep["known_usd_per_calendar_day"] - r["known_usd_per_calendar_day"]
            ),
            "delta_known_usd_per_year_252_sessions": (
                rep["known_usd_per_year_252_sessions"] - r["known_usd_per_year_252_sessions"]
            ),
            "delta_known_fills": rep["known_fills"] - r["known_fills"],
            "delta_fills": rep["fills"] - r["fills"],
            "delta_unknown_fills": rep["unknown_fills"] - r["unknown_fills"],
            "delta_funded_intents": rep["funded_intents"] - r["funded_intents"],
            "delta_full_loss_lower_bound_usd_per_calendar_day": (
                rep["full_loss_lower_bound_usd_per_calendar_day"]
                - r["full_loss_lower_bound_usd_per_calendar_day"]
            ),
            "delta_deployed_usd_per_calendar_day": (
                rep["deployed_usd_per_calendar_day"] - r["deployed_usd_per_calendar_day"]
            ),
        }
    return {
        "reference_cell": ref["cell_key"],
        "identical_basis": (
            "same block, same rung, same signals, same book rules; the reference cell is "
            "this study's own flat $1,000 x 3-slot cell"
        ),
        "per_rung": per_rung,
    }


def choose_cell(surface: dict) -> tuple[Cell, list[dict]]:
    """Deterministic argmax of the frozen validation objective; no floors, no gates."""
    ranked = sorted(
        CELLS,
        key=lambda c: (
            -surface[c.key]["per_rung"][str(int(SELECT_COST_BPS))]["known_usd_per_calendar_day"],
            -surface[c.key]["per_rung"][str(int(SELECT_COST_BPS))]["known_fills"],
            surface[c.key]["per_rung"][str(int(SELECT_COST_BPS))]["funded_intents"],
            c.key,
        ),
    )
    ranking = []
    for rank, c in enumerate(ranked, 1):
        rep = surface[c.key]["per_rung"][str(int(SELECT_COST_BPS))]
        boot = rep["bootstrap_daily_known_usd"]
        ranking.append(
            {
                "rank": rank,
                "cell_key": c.key,
                "label": c.label,
                "shape": c.shape,
                "ticket_usd": c.ticket_usd,
                "scale_usd_per_unit_prediction": c.scale,
                "max_slots": c.max_slots,
                "validation_known_usd_per_calendar_day_25": rep["known_usd_per_calendar_day"],
                "validation_known_usd_per_calendar_day_per_1000_reserve": rep[
                    "known_usd_per_calendar_day_per_1000_reserve"
                ],
                "validation_known_usd_per_year_252_sessions": rep[
                    "known_usd_per_year_252_sessions"
                ],
                "known_fills": rep["known_fills"],
                "unknown_fills": rep["unknown_fills"],
                "funded_intents": rep["funded_intents"],
                "fills": rep["fills"],
                "traded_days": rep["traded_days"],
                "mean_net_usd_per_known_fill": rep["mean_net_usd_per_known_fill"],
                "day_bootstrap_p_gt_zero": boot.get("p_gt_zero"),
                "full_loss_lower_bound_usd_per_calendar_day": rep[
                    "full_loss_lower_bound_usd_per_calendar_day"
                ],
            }
        )
    return ranked[0], ranking


def decision_text(chosen: Cell, val: dict, late: dict | None, frozen: bool) -> str:
    rep = val["per_rung"][str(int(SELECT_COST_BPS))]
    boot = rep["bootstrap_daily_known_usd"]
    if rep["known_fills"] == 0:
        return (
            "NO_EDGE_EVIDENCE: the chosen cell has no known fill on 2023 validation, so an "
            "empty signal set is cash, not a positive edge. Nothing here is promoted; the "
            "retained flat $1,000 x 3-slot reference stays the reference."
        )
    head = (
        f"{STATUS}: chosen {chosen.label} ({chosen.key}) by 2023 validation known "
        f"contribution: {rep['known_usd_per_calendar_day']:+.2f} $/calendar day at "
        f"{int(SELECT_COST_BPS)}bps = {rep['known_usd_per_year_252_sessions']:+.0f} $/year "
        f"(simple {TRADING_DAYS_PER_YEAR}-session convention, NOT a CAGR), "
        f"{rep['known_fills']} known fills on {rep['traded_days']} traded days of "
        f"{val['days_replayed']} calendared days, {rep['funded_intents']} funded intents, "
        f"{rep['unknown_fills']} UNKNOWN fills and {rep['unfilled_fills']} unfilled "
        f"attempts. {FILL_SEMANTICS}. Day bootstrap p>0 = {boot.get('p_gt_zero')}."
    )
    delta_block = val.get("reference_delta") or {}
    delta_per_rung = delta_block.get("per_rung") or {}
    delta = delta_per_rung.get(str(int(SELECT_COST_BPS))) or {}
    if chosen.key == REFERENCE_CELL:
        head += (
            f" The chosen cell is the {REFERENCE_CELL} reference itself, so its delta "
            "against the frozen reference is identically zero by definition (no separate "
            "comparison is computed)."
        )
    elif delta:
        head += (
            f" Versus the frozen {REFERENCE_CELL} reference cell on the identical basis: "
            f"{delta['delta_known_usd_per_calendar_day']:+.2f} $/calendar day "
            f"({delta['delta_known_fills']:+d} known fills, "
            f"{delta['delta_funded_intents']:+d} funded intents, "
            f"{delta['delta_unknown_fills']:+d} UNKNOWN fills)."
        )
    else:
        head += (
            f" Delta vs the frozen {REFERENCE_CELL} reference cell: unavailable (the "
            "reference comparison was not produced for this cell on this block)."
        )
    if rep["known_usd_per_calendar_day"] <= 0:
        head += (
            " The best cell is NOT validation-positive at the selection rung: every cell is "
            "measured and none clears zero, so the retained reference stays the reference; "
            "the UNKNOWN share is reported beside these numbers and is NOT assumed zero."
        )
    if frozen and late is not None:
        late_rep = late["per_rung"][str(int(SELECT_COST_BPS))]
        head += (
            f" The frozen late block (2025-02..2026-05, previously explored, NOT pristine) "
            f"measures {late_rep['known_usd_per_calendar_day']:+.2f} $/calendar day at "
            f"{int(SELECT_COST_BPS)}bps over {late_rep['known_fills']} known fills of "
            f"{late_rep['funded_intents']} funded intents; the choice was frozen before any "
            f"late day was read, so this is confirmation of an already-frozen decision, not "
            f"a re-selection."
        )
    elif not frozen:
        head += " Late block not run (--skip-late)."
    head += (
        f" Separately labeled full-loss lower bound over unknowns at "
        f"{int(SELECT_COST_BPS)}bps: {rep['full_loss_lower_bound_usd_per_calendar_day']:+.2f} "
        f" $/calendar day (validation); it is a coding convention over unpriced exits, never "
        f"blended into the known contribution and never an expected loss."
    )
    return head


def provenance_block(
    out_dir: Path,
    days_val: list[str],
    days_late: list[str] | None,
    val_cov: dict,
    late_cov: dict | None,
    score_cov_val: dict,
    score_cov_late: dict | None,
    replay_cov_val: dict,
    replay_cov_late: dict | None,
) -> dict:
    return {
        "producer_sha256": producer_sha256(),
        "producer_script": str(Path(__file__).resolve()),
        "contract_sha256": _digest_bytes(CONTRACT),
        "panel_root": str(PANEL_ROOT),
        "panel_days_root": str(PANEL_DAYS),
        "panel_contract_sha256": (sha256_file(PANEL_CONTRACT) if PANEL_CONTRACT.exists() else None),
        "model": {
            "path": str(MODEL_PATH),
            "sha256": sha256_file(MODEL_PATH),
            "pinned_sha256": MODEL_SHA256,
        },
        "validation_days": len(days_val),
        "late_days": (
            len(days_late)
            if days_late is not None
            else "not_yet_inspected: the freeze precedes any late file access"
        ),
        "coverage_validation": val_cov,
        "coverage_late": late_cov,
        "score_coverage_validation": score_cov_val,
        "score_coverage_late": score_cov_late,
        "replay_coverage_validation": replay_cov_val,
        "replay_coverage_late": replay_cov_late,
        "output_root": str(out_dir),
        "protected_unread": PROTECTED_UNREAD,
        "fill_semantics": FILL_SEMANTICS,
    }


# ----- run --------------------------------------------------------------------
def print_cell_line(tag: str, cell: dict) -> None:
    rep = cell["per_rung"][str(int(SELECT_COST_BPS))]
    boot = rep["bootstrap_daily_known_usd"]
    print(
        f"[{tag}] {cell['label']:<44} {rep['known_usd_per_calendar_day']:+8.2f} $/day "
        f"({rep['known_usd_per_year_252_sessions']:+8.0f} $/yr252) "
        f"known={rep['known_fills']} unk={rep['unknown_fills']} unfilled={rep['unfilled_fills']} "
        f"noorder={rep['no_order_skips']} cashslot={rep['cash_or_slot_skips']} "
        f"funded={rep['funded_intents']} traded_days={rep['traded_days']}/{cell['days_replayed']} "
        f"meanpos={rep['mean_positions_open']:.2f} peakdep={rep['peak_deployed_usd_mean']:.0f} "
        f"lb={rep['full_loss_lower_bound_usd_per_calendar_day']:+.2f} "
        f"p>0={boot.get('p_gt_zero')}",
        flush=True,
    )


def aggregate_block(out_dir: Path, block: str, days: list[str]) -> tuple[dict, dict]:
    """The full nine-cell surface of one block plus the block's coverage."""
    daily, trades = read_block_frames(out_dir, block)
    surface = {c.key: cell_report(daily, trades, c, len(days)) for c in CELLS}
    ref = surface[REFERENCE_CELL]
    for c in CELLS:
        if c.key == REFERENCE_CELL:
            # The reference cell never carries a None placeholder: its delta is an
            # identity, stated explicitly, so no consumer has to assume a dict exists.
            surface[c.key]["reference_delta"] = {
                "reference_cell": REFERENCE_CELL,
                "identical_basis": (
                    "same block, same rung, same signals, same book rules; this IS the "
                    "frozen flat1000_s3 reference cell"
                ),
                "per_rung": {},
                "note": (
                    "the chosen/comparing cell is the reference itself, so every delta is "
                    "identically zero by definition and no separate comparison is computed"
                ),
            }
        else:
            surface[c.key]["reference_delta"] = reference_delta(surface[c.key], ref)
    coverage = {
        "block": block,
        "days": len(days),
        "signals": int(daily["signals"].sum()),
        "funded_intents": int(daily["funded_intents"].sum()),
        "fills": int(daily["fills"].sum()),
        "known_fills": int(daily["known_fills"].sum()),
        "unknown_fills": int(daily["unknown_fills"].sum()),
        "unfilled_fills": int(daily["unfilled_fills"].sum()),
        "no_order_skips": int(daily["no_order_skips"].sum()),
        "cash_or_slot_skips": int(daily["cash_or_slot_skips"].sum()),
        "slot_busy_skips": int(daily["slot_busy_skips"].sum()),
        "cooldown_skips": int(daily["cooldown_skips"].sum()),
        "max_attempt_skips": int(daily["max_attempt_skips"].sum()),
    }
    return surface, coverage


def run(args: argparse.Namespace) -> None:
    t0 = time.time()
    out = args.out or OUTPUT
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")
    print(
        "[note] every fill in this study is a MINUTE-OPEN PROXY: never an actual quote, "
        "never an exchange fill, never a capacity claim",
        flush=True,
    )

    # 1) ONE immutable head, loaded and scored (never fitted).
    bundle, model_report = load_stored_model()
    print(
        f"[model] h{HEAD} immutable (no refit): {MODEL_PATH} "
        f"sha256 {sha256_file(MODEL_PATH)[:12]}, "
        f"{model_report[f'h{HEAD}']['n_features']} features",
        flush=True,
    )

    days_val = block_days(args.days, VAL_BLOCK)
    if args.days is None and len(days_val) != EXPECTED_DAYS[VAL_BLOCK]:
        raise SystemExit(
            f"[retained-sizing] requires {EXPECTED_DAYS[VAL_BLOCK]} {VAL_BLOCK} days, "
            f"got {len(days_val)}"
        )
    if not days_val:
        raise SystemExit(
            "[retained-sizing] no 2023 validation days selected; the dollars/day "
            "selection objective requires the validation block"
        )

    # 2) the complete fixed grid + cost ladder, frozen BEFORE any validation outcome.
    (out / "contract.json").write_text(json.dumps(CONTRACT, indent=1, default=_default) + "\n")
    print(
        f"[freeze-grid] contract -> {out / 'contract.json'} "
        f"({len(CELLS)} cells x {len(COST_LADDER_BPS)} rungs, before any outcome)",
        flush=True,
    )

    # 3) validation only: score, replay, aggregate. No late panel day is read yet.
    print(
        f"[blocks] validation={len(days_val)} days (late block deferred until after the freeze)",
        flush=True,
    )
    score_cov_val = score_block(out, bundle, days_val, args.resume)
    replay_cov_val = replay_block(out, VAL_BLOCK, days_val, args.resume)
    val_surface, val_cov = aggregate_block(out, VAL_BLOCK, days_val)
    for cell in CELLS:
        print_cell_line("val@25", val_surface[cell.key])

    chosen, ranking = choose_cell(val_surface)
    frozen_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    freeze = {
        "frozen_before_late_file_access": True,
        "frozen_at": frozen_at,
        "study": "alpha_retained_score_sizing",
        "status": STATUS,
        "chosen": {
            "cell_key": chosen.key,
            "label": chosen.label,
            "shape": chosen.shape,
            "ticket_usd": chosen.ticket_usd,
            "scale_usd_per_unit_prediction": chosen.scale,
            "max_slots": chosen.max_slots,
            "head": HEAD,
            "threshold": THETA,
            "is_reference": bool(chosen.is_reference),
        },
        "selection": {
            "objective": CONTRACT["selection"]["objective"],
            "selection_cost_bps": SELECT_COST_BPS,
            "cost_ladder_bps": [float(c) for c in COST_LADDER_BPS],
            "basis": CONTRACT["selection"]["basis"],
            "synthetic_unknown_loss_veto": False,
            "power_floors": None,
            "median_or_tail_gates": None,
            "cost_or_count_kill": None,
            "tie_break": CONTRACT["selection"]["tie_break"],
            "cells": len(CELLS),
            "frozen_before_late_inspection": True,
            "late_block_is_transparency_only": True,
            "chosen_cell_immutable_after_freeze": True,
            "ranking": ranking,
        },
        "cells": [
            {
                "cell_key": c.key,
                "label": c.label,
                "shape": c.shape,
                "ticket_usd": c.ticket_usd,
                "scale_usd_per_unit_prediction": c.scale,
                "max_slots": c.max_slots,
                "is_reference": bool(c.is_reference),
            }
            for c in CELLS
        ],
        "validation_surface": val_surface,
        "validation_reference_deltas": {
            c.key: val_surface[c.key]["reference_delta"] for c in CELLS if c.key != REFERENCE_CELL
        },
        "model": model_report,
        "provenance": provenance_block(
            out,
            days_val,
            None,
            val_cov,
            None,
            score_cov_val,
            None,
            replay_cov_val,
            None,
        ),
        "fill_semantics": FILL_SEMANTICS,
    }
    write_json_atomic(out / "selection_freeze.json", freeze)
    chosen_cell_selection = val_surface[chosen.key]["per_rung"][str(int(SELECT_COST_BPS))]
    print(
        f"[freeze] selection_freeze.json -> chosen {chosen.key} "
        f"({chosen_cell_selection['known_usd_per_calendar_day']:+.2f} "
        f"$/day @{int(SELECT_COST_BPS)} on validation) AFTER validation, BEFORE any late "
        "file is read",
        flush=True,
    )

    late_surface = None
    late_cov = None
    days_late = None
    score_cov_late = None
    replay_cov_late = None
    if not args.skip_late:
        # 4) late block: every cell at every rung for transparency, the choice immutable.
        #    This is the FIRST access to any late panel file.
        days_late = block_days(args.days, LATE_BLOCK)
        if args.days is None and len(days_late) != EXPECTED_DAYS[LATE_BLOCK]:
            raise SystemExit(
                f"[retained-sizing] requires {EXPECTED_DAYS[LATE_BLOCK]} {LATE_BLOCK} days, "
                f"got {len(days_late)}"
            )
        if not days_late:
            print(
                "[blocks] no late day selected: validation-only results, no late file read",
                flush=True,
            )
        else:
            print(
                f"[blocks] late={len(days_late)} days (after the freeze; previously "
                "explored, NOT pristine)",
                flush=True,
            )
            score_cov_late = score_block(out, bundle, days_late, args.resume)
            replay_cov_late = replay_block(out, LATE_BLOCK, days_late, args.resume)
            late_surface, late_cov = aggregate_block(out, LATE_BLOCK, days_late)
            for cell in CELLS:
                print_cell_line("late@25", late_surface[cell.key])

    results = {
        "study": "alpha_retained_score_sizing",
        "status": STATUS,
        "decision": decision_text(
            chosen,
            val_surface[chosen.key],
            late_surface[chosen.key] if late_surface else None,
            frozen=late_surface is not None,
        ),
        "chosen": freeze["chosen"],
        "frozen_choice_immutable": True,
        "fill_semantics": FILL_SEMANTICS,
        "contract": CONTRACT,
        "model": model_report,
        "validation": val_surface,
        "validation_ranking": ranking,
        "validation_reference_deltas": {
            c.key: val_surface[c.key]["reference_delta"] for c in CELLS if c.key != REFERENCE_CELL
        },
        "late": late_surface,
        "late_ranking": (
            [
                {
                    "cell_key": c.key,
                    "label": c.label,
                    "late_known_usd_per_calendar_day_25": late_surface[c.key]["per_rung"][
                        str(int(SELECT_COST_BPS))
                    ]["known_usd_per_calendar_day"],
                    "known_fills": late_surface[c.key]["per_rung"][str(int(SELECT_COST_BPS))][
                        "known_fills"
                    ],
                    "unknown_fills": late_surface[c.key]["per_rung"][str(int(SELECT_COST_BPS))][
                        "unknown_fills"
                    ],
                    "funded_intents": late_surface[c.key]["per_rung"][str(int(SELECT_COST_BPS))][
                        "funded_intents"
                    ],
                }
                for c in sorted(
                    CELLS,
                    key=lambda c: (
                        -late_surface[c.key]["per_rung"][str(int(SELECT_COST_BPS))][
                            "known_usd_per_calendar_day"
                        ],
                        -late_surface[c.key]["per_rung"][str(int(SELECT_COST_BPS))]["known_fills"],
                        late_surface[c.key]["per_rung"][str(int(SELECT_COST_BPS))][
                            "funded_intents"
                        ],
                        c.key,
                    ),
                )
            ]
            if late_surface
            else None
        ),
        "chosen_late_block": (late_surface[chosen.key] if late_surface else None),
        "chosen_validation_block": val_surface[chosen.key],
        "cost_ladder_whole_calendar": {
            c.key: {
                str(int(r)): {
                    "validation_known_usd": val_surface[c.key]["per_rung"][str(int(r))][
                        "known_usd"
                    ],
                    "late_known_usd": (
                        late_surface[c.key]["per_rung"][str(int(r))]["known_usd"]
                        if late_surface
                        else None
                    ),
                    "validation_known_usd_per_calendar_day": val_surface[c.key]["per_rung"][
                        str(int(r))
                    ]["known_usd_per_calendar_day"],
                    "late_known_usd_per_calendar_day": (
                        late_surface[c.key]["per_rung"][str(int(r))]["known_usd_per_calendar_day"]
                        if late_surface
                        else None
                    ),
                    "validation_known_usd_per_year_252_sessions": val_surface[c.key]["per_rung"][
                        str(int(r))
                    ]["known_usd_per_year_252_sessions"],
                    "late_known_usd_per_year_252_sessions": (
                        late_surface[c.key]["per_rung"][str(int(r))][
                            "known_usd_per_year_252_sessions"
                        ]
                        if late_surface
                        else None
                    ),
                    "validation_full_loss_lower_bound_usd_per_calendar_day": val_surface[c.key][
                        "per_rung"
                    ][str(int(r))]["full_loss_lower_bound_usd_per_calendar_day"],
                    "late_full_loss_lower_bound_usd_per_calendar_day": (
                        late_surface[c.key]["per_rung"][str(int(r))][
                            "full_loss_lower_bound_usd_per_calendar_day"
                        ]
                        if late_surface
                        else None
                    ),
                }
                for r in COST_LADDER_BPS
            }
            for c in CELLS
        },
        "coverage": {
            "validation": val_cov,
            "late": late_cov,
            "score_validation": score_cov_val,
            "score_late": score_cov_late,
            "replay_validation": replay_cov_val,
            "replay_late": replay_cov_late,
        },
        "provenance": provenance_block(
            out,
            days_val,
            days_late,
            val_cov,
            late_cov,
            score_cov_val,
            score_cov_late,
            replay_cov_val,
            replay_cov_late,
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
    print(f"[done] results -> {out / 'results.json'} ({results['runtime_s']}s)", flush=True)


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--out", type=Path, default=None, help=f"output root (default: {OUTPUT})")
    p.add_argument(
        "--resume",
        action="store_true",
        help="resume from the per-day states / replay parts already on disk",
    )
    p.add_argument(
        "--skip-late",
        action="store_true",
        help="stop after the frozen selection (validation block only; no late day is read)",
    )
    p.add_argument(
        "--days",
        nargs="+",
        default=None,
        help="restrict the analysis calendar to these days (smoke/debug only)",
    )
    run(p.parse_args())


if __name__ == "__main__":
    main()
