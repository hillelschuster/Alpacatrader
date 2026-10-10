#!/usr/bin/env python3
"""ACTUAL-TOUCH hourly payoff heads on the open-anchored top-gainer panel (new fit).

The retained lane trades a frozen MINUTE-OPEN payoff model; this producer asks the
orthogonal question the quote-aware engine left open: can two NEW heads be trained
directly on the label the quote engine actually trades - the ASK-to-BID NBBO touch
of a real marketable-IOC entry held 15 or 60 minutes - instead of on the minute-open
proxy? Every number below is an actual-touch measurement on the observable, causal
substrate; nothing is promoted and no live order exists.

* LABELS (canonical, shared). For every causal-liquidity-qualified panel state of every
  allowed day (train 2021-05-01..2022-12-31, validation all of 2023, confirmation
  2025-02-01..2026-05-31) one row is written under ``labels/<day>.parquet``: the 26 base
  ``alpha_open_learned.FEATURES_ALL`` state features UNCHANGED plus the five OBSERVED
  intent-quote features (spread_bps, log_ask_shares, log_bid_shares, quote_age_s,
  depth_imbalance) measured at the intent clock (t+1 at 00.000s, BEFORE any order), the
  causal integer quantity ``q = floor(250 / (limit * (1 + 150bps/2)))`` fixed at the
  intent (fee-cap 150, never re-floored from an arrival price), and the realized
  gross-before-extra touch return of the teacher execution:
  ``y_touch_h = exit_bid / entry_ask - 1`` with entry = the ACTUAL arrival ASK of a
  conditional full-touch IOC fill and exit = the ACTUAL arrival BID of a
  fresh-quote-gated market submission. The spread is already inside those prices, so it
  is NEVER subtracted again. The teacher is independent of any stored model forecast: no
  old-model prediction, spread bar or future depth ever gates a label. A leg that cannot
  be honestly priced is an explicit UNKNOWN (NULL y, ``label_known=False``); a policy
  that sends no order (q == 0, an intent book that is not firm/fresh, or top-of-book
  intent depth below q) is a KNOWN cash zero (y = 0.0); a ticker whose quote stream was
  never acquired that session is a DATA UNKNOWN, never a cash zero.
* ENTRY PHYSICS. The buy is a LIMIT IOC: at the intent the limit price
  ``ceil_to_cent(ask * 1.01)`` and q are fixed BEFORE arrival; at intent + 250ms the
  latest RAW state is evaluated ONCE. An IOC cannot rest, so an invalid / crossed /
  non-regular / absent / stale-but-valid arrival print is an execution UNKNOWN - the
  first future regular print is NEVER substituted and q is never re-sized. A fresh print
  with ask <= limit and ASK depth >= q is a conditional full-L1-touch fill at the ACTUAL
  ask; ask > limit is a modeled no-L1-match (unfilled cash); partial depth is an
  UNKNOWN. (The MARKET-style resting helper ``priced_leg`` is used for EXITS only, where
  a submitted market order may rest through a genuinely unavailable book.)
* EXIT PHYSICS. The sell is a MARKET order due at min(entry_minute + h, session_end):
  FRESH-QUOTE-GATED submission at the due intent when the latest raw state is firm and
  fresh, otherwise held to the FIRST eligible regular print at/after the due intent
  inside RTH; priced +250ms after the ACTUAL submission. Age > 2s never expires a valid
  NBBO and never cancels a submitted order - it only means this policy does not send
  blindly into an unobservable book. Exit quantity is the causal entry q.
* FIT. Two fixed LightGBM regressors (h15/h60; num_leaves 15, min_data_in_leaf 300,
  200 trees, lr 0.03, 2 threads, seed 20261009) on the 31 causal features (26 base + 5
  quote; no ticker/date identifiers), equal-day fit weights (each session contributes
  equal total weight), target clipped to [-0.5, 1.0] FOR FIT ONLY. The fit set is the
  known-label rows with an OBSERVABLE intent book; known cash zeros stay in, unknown
  executions and unobservable-intent rows stay out, and every exclusion is disclosed
  with the missingness it implies.
* EVALUATION. The two heads are replayed through the shared funded book (3 slots x $250,
  reserve at the 150bps worst rung, one position per ticker, flat 5-minute cooldown
  after the ACTUAL exit, at most 5 attempts per ticker per session, simultaneous-clock
  intents funded BEFORE any arrival outcome, an UNKNOWN position keeping its slot and
  its ticker blocked to the session end). Eight fixed views = 2 heads x 4 net-clearance
  bars {0, 25, 50, 100}bps measured ABOVE the realistic 25bps provider extra fee only -
  the observed spread is already inside the touch prices, so it is never re-charged at
  the gate. The single view is chosen ONLY by 2023 validation actual-touch
  known contribution per calendar day at the 25bps extra rung, on an explicit PARTIAL
  basis, and is frozen to disk BEFORE any confirmation file is read. No cost, count,
  median, top-day or CI floor is applied anywhere. The full 0/5/10/25/50/75/100/125/150
  extra-cost ladder is reported for every view: the observed touch cost lives inside the
  realized entry/exit prices and every rung charges only the extra fee, separately.
  Annual figures are the simple mean-daily x 252-session convention on the $750 nominal
  book, never a CAGR, with the PARTIAL (known-only) and WHOLE (unknowns as full ticket
  loss) bases side by side and fixed SIP opex as a separate line.

Usage:
  uv run --no-sync python factory/scripts/alpha_touch_hourly_payoff.py
      # full run: labels (1003 days) -> fit -> validation -> freeze -> confirmation
  uv run --no-sync python factory/scripts/alpha_touch_hourly_payoff.py --resume
      # resume from the per-day label / state / replay parts already on disk
  uv run --no-sync python factory/scripts/alpha_touch_hourly_payoff.py --days 2023-05-15
      # smoke subset (debug only; day counts are not enforced)
  uv run --no-sync python factory/scripts/alpha_touch_hourly_payoff.py --stages labels
      # labels only (the shared contract root other producers read)
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

import joblib
import numpy as np
import polars as pl
from lightgbm import LGBMRegressor

sys.path.insert(0, str(Path(__file__).resolve().parent))

from alpha_open_learned import FEATURES_ALL, PANEL_ROOT, bootstrap_daily
from alpha_open_panel import allowed
from alpha_open_sim import causal_liquidity, period
from alpha_quote_aware_frequency import (
    DATA_ROOT,
    MAX_AGE_S,
    QUOTE_CACHE_ROOT,
    SUPPLEMENT_ROOTS,
    arrival_us,
    causal_quantity,
    entry_price_leg,
    limit_price_of,
    minute_us,
    net_usd,
    priced_leg,
    quote_day_frame,
    reserved_usd,
    sha256_file,
    submission_clock,
    supplement_digest,
    symbol_quotes,
    write_json_atomic,
)
from alpha_sparse_daily import day_context

# ----- fixed configuration (no HPO, no grid search, no refit). ----------------
HEADS = (15, 60)  # the two new actual-touch payoff heads
QUOTE_FEATURES = (
    "spread_bps",
    "log_ask_shares",
    "log_bid_shares",
    "quote_age_s",
    "depth_imbalance",
)  # observed-intent quote features (intent clock, before any order)
FEATURE_ORDER = list(FEATURES_ALL) + list(QUOTE_FEATURES)  # 31 causal dims, no ids
LABEL_COST_BPS = 150.0  # fee cap for the causal quantity AND the worst-rung reserve
ORDER_BUDGET = 250.0  # per-position ticket
MAX_SLOTS = 3  # concurrent positions
BOOK = MAX_SLOTS * ORDER_BUDGET  # $750 nominal research book
COOLDOWN_MIN = 5  # flat minutes after the ACTUAL exit
MAX_ATTEMPTS = 5  # attempts per ticker per session
ENTRY_OVERHEAD_BPS = 25.0  # realistic provider extra in the net-clearance entry bar
SELECT_COST = 25.0  # selection / primary reported extra rung
RUNG_COSTS = (0.0, 5.0, 10.0, 25.0, 50.0, 75.0, 100.0, 125.0, 150.0)  # extra bps, both legs
CLEARANCES = (
    0.0,
    0.0025,
    0.005,
    0.010,
)  # extra-fee clearance above the 25bps residual (spread already in the touch)
TRAIN_START = "2021-05-01"  # >= 20 months of covered sessions
TRAIN_END = "2022-12-31"
ANNUAL_SESSIONS = 252  # simple session-count convention, NOT a CAGR
FIXED_OPEX_USD_PER_YEAR = 1188.0  # official SIP subscription 99 USD/month, reported apart
BOOT_N = 1000
BOOT_SEED = 20261009
SEED = 20261009
CLIP = (-0.5, 1.0)  # target clip, FIT ONLY
LGB_PARAMS = {
    "objective": "regression",
    "num_leaves": 15,
    "min_data_in_leaf": 300,
    "learning_rate": 0.03,
    "n_estimators": 200,
    "num_threads": 2,
    "seed": SEED,
    "verbosity": -1,
    "deterministic": True,
    "force_row_wise": True,
}
TRAIN_BLOCK = "train"
VAL_BLOCK = "validation"
LATE_BLOCK = "confirmation"
LATE_START = "2025-02-01"
LATE_END = "2026-05-31"
EXPECTED_DAYS = {VAL_BLOCK: 250, LATE_BLOCK: 332}
MIN_TRAIN_DAYS = 400  # >= 20 months of covered sessions (421 present)
OUTPUT = PANEL_ROOT / "quote_hourly_payoff"
LABELS_ROOT = OUTPUT / "labels"
STATES_ROOT = OUTPUT / "states"
# This producer's OWN quote-universe acquisition (alpha_quote_universe_acquire.py): a
# PRIMARY_KEEPFIRST combined day cache for the not-acquired panel tickers. It is the
# LAST supplement, so the ranked SIP original always wins and a run before the harvest
# exists is bit-identical to one that never sees it.
ACQUIRE_QUOTES_ROOT = PANEL_ROOT / "quote_universe_acquire" / "quotes"
DEFAULT_SUPPLEMENTS = tuple(SUPPLEMENT_ROOTS) + (ACQUIRE_QUOTES_ROOT,)


def fitted_model_path(out_dir: Path, head: int) -> Path:
    """The fitted head lives under THIS run's output root (smoke runs never touch it)."""
    return out_dir / "models" / f"touch_payoff_h{head}.joblib"


PANEL_DAYS = PANEL_ROOT / "days"
PANEL_CONTRACT = PANEL_ROOT / "contract.json"
STATUS = "DISCOVERY-NOT-VALIDATED"
VERSION = 1
LABEL_SCHEMA = 1
STATE_SCHEMA = 1
REPLAY_SCHEMA = 1
ENGINE_SHA_NOTE = (
    "quote-engine helper SHAs are recorded as provenance only; the helpers are the "
    "stable contract named in the handoff and are never edited by this producer"
)
PROTECTED_UNREAD = ["2024", "2025-01", "2026-06", "2026-07", "2026-08"]
LABEL_PRICE_BASIS = "actual_nbbo_touch_ask_entry_bid_exit_gross_before_extra"

CONTRACT = {
    "version": VERSION,
    "hypothesis": "two NEW payoff heads trained directly on the actual ASK-to-BID NBBO "
    "touch label (IOC-limit entry at t+1, fresh-quote-gated market exit 15/60 minutes "
    "later) on the same causal-liquidity-qualified open-anchored top-gainer states clear "
    "a realistic 25bps provider-extra fee residual (the observed spread is already inside "
    "the touch prices and is never re-charged) more often than the retained "
    "minute-open-proxy lane",
    "periods": {
        TRAIN_BLOCK: f"{TRAIN_START}..{TRAIN_END} (>= 20 months of covered sessions)",
        VAL_BLOCK: "2023-01-01..2023-12-31 (all 250 allowed panel days)",
        LATE_BLOCK: f"{LATE_START}..{LATE_END} (all 332 allowed panel days, previously "
        "explored, not pristine)",
    },
    "protected_unread": PROTECTED_UNREAD,
    "labels": {
        "root": str(LABELS_ROOT),
        "day_file": "labels/<day>.parquet (atomic tmp+replace, per-day resume)",
        "columns": [
            "day",
            "ticker",
            "t",
            "entry_intent_us",
            *FEATURES_ALL,
            *QUOTE_FEATURES,
            "y_touch_h15",
            "y_touch_h60",
            "label_known_h15",
            "label_known_h60",
            "entry_execution_status",
            "exit_status_h15",
            "exit_status_h60",
            "causal_qty",
            "label_price_basis",
        ],
        "state_universe": "causal_liquidity-qualified panel states of every allowed day "
        "(cumDV >= 1M and bars15 >= 12); no model forecast, spread bar or future depth "
        "ever gates a label",
        "intent_clock": "clock_us(day, t+1, 0): the latest RAW NBBO state observed there "
        "is the pre-order observation (never pre-filtered for eligibility)",
        "quote_features": "observed at the intent clock, BEFORE any order",
        "quantity": "q = floor(250 / (ceil_to_cent(ask*1.01) * (1 + 150bps/2))), fixed "
        "BEFORE arrival and never re-floored from an arrival price",
        "target": "y_touch_h = actual_exit_bid / actual_entry_ask - 1 (PRICE-GROSS "
        "gross-before-extra: a dimensionless Float64 price ratio on one column that holds "
        "a filled touch, a known cash 0.0 and an unknown NULL; the spread is inside the "
        "prices and is never subtracted again)",
        "target_normalization": "price_gross_return_for_filled; zero_for_known_cash; "
        "null_for_unknown",
        "known_cash_zero": "a policy that sends no order (q == 0, an intent book that is "
        "not firm/fresh, or intent top-of-book depth below q) and an IOC no-L1-match are "
        "KNOWN cash zeros: y = 0.0, label_known = True",
        "unknown_rule": "any leg that cannot be honestly priced (partial depth, stale or "
        "absent book at arrival, no valid regular print inside RTH, a stream that was "
        "never acquired) is an explicit UNKNOWN: y = NULL, label_known = False",
        "hold": "min(entry_minute + h, session_end) minutes, RTH close capped",
    },
    "entry": {
        "order_type": "LIMIT IOC (a limit IOC can never rest to a later eligible print)",
        "limit_price": "ceil_to_cent(observed_ask * 1.01): pre-declared marketability "
        "cap, NOT charged friction",
        "quantity": "q fixed at the intent at the 150bps fee cap, never re-sized",
        "arrival_clock": "intent + 250ms, latest RAW state evaluated ONCE",
        "fresh_firm_rule": "valid, un-crossed, R-only, age <= 2s",
        "depth_rule": "intent ASK depth >= q (else no order) and arrival ASK depth >= q "
        "(else execution UNKNOWN)",
        "arrival_rule": "fresh + ask <= limit + depth supports q -> conditional "
        "full-L1-touch IOC fill at the ACTUAL ask; ask > limit -> modeled no-L1-match "
        "unfilled cash; partial depth / invalid / crossed / non-regular / absent / "
        "stale-but-valid arrival -> execution UNKNOWN (no future print substituted, q "
        "never re-sized)",
        "no_order_rule": "q == 0 or an unfirm intent book is a KNOWN no-order (cash, "
        "never a priced zero-return fill, never an UNKNOWN)",
    },
    "exit": {
        "order_type": "MARKET (a submitted market order may rest through a genuinely "
        "unavailable book; it never waits merely for an aged-but-valid quote)",
        "due_intent_clock": "clock_us(day, min(entry_minute + h, session_end), 0)",
        "submission": "FRESH-QUOTE-GATED: submit at the due intent when the latest raw "
        "state is firm and fresh, else hold to the FIRST eligible regular print at/after "
        "the due intent inside RTH and submit at that print's own timestamp",
        "arrival_clock": "actual submission + 250ms",
        "pricing": "latest raw state at the arrival clock; stale-but-valid -> UNKNOWN; "
        "invalid / non-regular / absent book -> conditional rest to the first eligible "
        "print at/after arrival (never a later favourable print)",
        "depth_rule": "exit BID top-of-book < q -> UNKNOWN partial capacity",
        "quantity": "the causal entry q, never re-sized",
    },
    "book": {
        "slots": MAX_SLOTS,
        "ticket_usd": ORDER_BUDGET,
        "book_usd": BOOK,
        "leverage": False,
        "one_position_per_ticker": True,
        "cooldown": "flat 5 minutes after the ACTUAL exit clock",
        "max_attempts_per_ticker_per_day": MAX_ATTEMPTS,
        "cash_reservation": "q * limit * (1 + 150bps/2): the worst reported rung, so no "
        "rung is unfundable",
        "simultaneous_clock_rule": "highest-score eligible intents are funded (up to the "
        "slot and cash limits) BEFORE any arrival outcome is observed; no same-clock "
        "substitution after an unfilled intent",
        "unknown_rule": "an UNKNOWN position keeps its slot and blocks its ticker until "
        "it resolves or the session ends; cash is never freed at a planned time",
        "later_clock_rule": "a later intent may use cash released by an earlier ACTUAL "
        "exit (or by an unfilled no-match arrival)",
    },
    "fit": {
        "heads": list(HEADS),
        "params": LGB_PARAMS,
        "clip_fit": list(CLIP),
        "sample_weight": "equal-day: each session's fit rows share 1/n_day of that "
        "session's weight, normalized to mean 1",
        "features": {
            "order": FEATURE_ORDER,
            "count": len(FEATURE_ORDER),
            "base": "alpha_open_learned.FEATURES_ALL (26), unchanged",
            "quote": list(QUOTE_FEATURES),
            "ticker_or_date_identifiers": False,
            "null_fill": "0.0 at fit, exactly the alpha_open_learned.feature_matrix convention",
        },
        "fit_set": "known-label rows whose intent book was OBSERVABLE; known cash zeros "
        "are included, unknown executions and unobservable-intent rows are excluded and "
        "the missingness is disclosed in fit_report.json",
        "fit_only_clip": True,
    },
    "selection": {
        "objective": "2023 validation actual-touch known contribution USD per full "
        "calendar day at the 25bps extra rung",
        "views": "2 heads x 4 net-clearance bars {0, 25, 50, 100}bps above the 25bps "
        "provider-extra fee residual only (the observed spread is already inside the "
        "touch prices and is never re-charged at the gate)",
        "selection_cost_bps": SELECT_COST,
        "cost_ladder_bps": list(RUNG_COSTS),
        "basis": "PARTIAL: unknown executions are excluded from the numerator and "
        "reported beside it; the whole (full-loss) basis is reported side by side and "
        "never the objective",
        "power_floors": None,
        "median_or_tail_gates": None,
        "cost_or_count_kill": None,
        "synthetic_100bps_veto": False,
        "frozen_before_late_inspection": True,
        "freeze_artifact": "selection_freeze.json (written after the validation "
        "surface, before any confirmation file is read)",
    },
    "reporting": {
        "extra_cost_ladder_bps": list(RUNG_COSTS),
        "touch_vs_extra": "the observed touch cost is inside the realized entry/exit "
        "prices; every rung charges only the extra fee, reported separately as the "
        "known_usd_0 - known_usd_rung drag",
        "annual": "mean daily known contribution x 252 sessions on the $750 nominal "
        "book; a simple session-count convention, NOT a CAGR and NOT an account claim",
        "annual_bases": ["PARTIAL known-only", "WHOLE unknowns-as-full-ticket-loss"],
        "fixed_opex_usd_per_year": FIXED_OPEX_USD_PER_YEAR,
        "fixed_opex_note": "official SIP subscription, reported as a separate line and "
        "never netted into the edge",
    },
    "supplements": {
        "merge_order": ["ranked SIP day cache"] + [str(r) for r in DEFAULT_SUPPLEMENTS],
        "dedup_rule": "unique (symbol, ts_utc) keep=first in that order: the ORIGINAL "
        "print always wins, a supplement may ONLY add timestamps",
        "own_universe_acquisition": (
            "alpha_quote_universe_acquire.py writes a PRIMARY_KEEPFIRST combined day "
            "cache for the not-acquired panel tickers; it is the LAST supplement, so a "
            "pre-harvest run and the ranked original always win. The 3.9GB SIP source is "
            "never written."
        ),
        "quote_age_rule": "age > 2s is an operational freshness gate for THIS policy, "
        "never a claim that an NBBO expired or that a submitted order was cancelled",
        "engine_helper_provenance": ENGINE_SHA_NOTE,
    },
    "no_live_orders": True,
}


def producer_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def engine_helper_shas() -> dict:
    """Provenance only: the imported quote-engine modules this producer builds on."""
    import alpha_open_learned as aol
    import alpha_quote_aware_frequency as aqf
    import alpha_sparse_daily as asd

    return {mod.__name__: sha256_file(Path(mod.__file__)) for mod in (aqf, aol, asd)}


def _digest_bytes(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _json_default(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (set, tuple)):
        return list(obj)
    raise TypeError(f"not serializable: {type(obj)}")


def block_days(days: list[str] | None, block: str) -> list[str]:
    """All allowed panel days of one research block (``days`` restricts for smoke runs)."""
    keep = set(days) if days else None
    out = []
    for path in sorted(PANEL_DAYS.glob("????-??-??.parquet")):
        day = path.stem
        if not allowed(day) or period(day) != block:
            continue
        if block == TRAIN_BLOCK and not (TRAIN_START <= day <= TRAIN_END):
            continue
        if block == LATE_BLOCK and not (LATE_START <= day <= LATE_END):
            continue
        if keep is not None and day not in keep:
            continue
        out.append(day)
    return out


# ----- canonical label artifacts (shared contract, atomic per day) -----------
LABEL_COLUMNS = (
    "day",
    "ticker",
    "t",
    "entry_intent_us",
    *FEATURE_ORDER,
    "y_touch_h15",
    "y_touch_h60",
    "label_known_h15",
    "label_known_h60",
    "entry_execution_status",
    "exit_status_h15",
    "exit_status_h60",
    "causal_qty",
    "label_price_basis",
)
LABEL_TYPES = {
    "day": pl.String,
    "ticker": pl.String,
    "t": pl.Int64,
    "entry_intent_us": pl.Int64,
    **dict.fromkeys(FEATURE_ORDER, pl.Float64),
    "y_touch_h15": pl.Float64,
    "y_touch_h60": pl.Float64,
    "label_known_h15": pl.Boolean,
    "label_known_h60": pl.Boolean,
    "entry_execution_status": pl.String,
    "exit_status_h15": pl.String,
    "exit_status_h60": pl.String,
    "causal_qty": pl.Int64,
    "label_price_basis": pl.String,
}
# An observable intent book is what makes the five quote features real; a known cash
# zero with NO observable book carries NULL features and is excluded from the fit set
# (disclosed), so the fitted features never describe an unobserved book as a tight one.
NO_INTENT_BOOK_STATUSES = (
    "quote_not_acquired",
    "no_order_intent_not_firm_fresh",
)
KNOWN_CASH_STATUSES = (
    "no_order_intent_not_firm_fresh",
    "no_order_min_capital",
    "no_order_intent_depth_unsupported",
    "no_match_at_l1",
)


def empty_labels() -> pl.DataFrame:
    return pl.DataFrame(schema={c: LABEL_TYPES[c] for c in LABEL_COLUMNS})


def label_paths(out_dir: Path, day: str) -> tuple[Path, Path]:
    return out_dir / "labels" / f"{day}.parquet", out_dir / "labels" / f"{day}.cov.json"


def depth_imbalance(bid_shares: float, ask_shares: float) -> float | None:
    denom = float(bid_shares) + float(ask_shares)
    if not math.isfinite(denom) or denom <= 0.0:
        return None
    return (float(bid_shares) - float(ask_shares)) / denom


def intent_observation(sq, intent_us: int) -> dict:
    """The causal pre-order observation: the latest RAW NBBO state at the intent clock.

    Observed once, never pre-filtered for eligibility (a crossed, non-regular or stale
    latest print is still observable). A print that is valid and regular populates the
    five quote features and the causal size; anything else leaves them NULL and reports
    the reason. No age cap is applied HERE - the freshness gate belongs to the policy
    verdict, and an aged-but-valid print keeps its observed features.
    """
    obs = {
        "intent_status": "quote_not_acquired",
        "intent_ask": None,
        "intent_bid": None,
        "intent_ask_shares": None,
        "spread_bps": None,
        "log_ask_shares": None,
        "log_bid_shares": None,
        "quote_age_s": None,
        "depth_imbalance": None,
        "limit_price": None,
        "qty": 0,
    }
    if sq is None:
        return obs
    q, status = sq.latest_raw(intent_us)
    if q is None:
        obs["intent_status"] = status
        return obs
    ask, bid = float(q["ask"]), float(q["bid"])
    limit = limit_price_of(ask)
    obs.update(
        {
            "intent_status": "quoted",
            "intent_ask": ask,
            "intent_bid": bid,
            "intent_ask_shares": float(q["ask_shares"]),
            "spread_bps": (None if q["spread_bps"] is None else float(q["spread_bps"])),
            "log_ask_shares": math.log1p(float(q["ask_shares"])),
            "log_bid_shares": math.log1p(float(q["bid_shares"])),
            "quote_age_s": float(q["age_s"]),
            "depth_imbalance": depth_imbalance(q["bid_shares"], q["ask_shares"]),
            "limit_price": limit,
            "qty": causal_quantity(limit, LABEL_COST_BPS),
        }
    )
    return obs


def intent_verdict(obs: dict) -> str:
    """Head-independent causal verdict: ``"eligible"`` or the no-order reason."""
    if obs["intent_status"] != "quoted":
        return "no_order_intent_not_firm_fresh"
    age = obs["quote_age_s"]
    if age is None or not math.isfinite(age) or age > MAX_AGE_S:
        return "no_order_intent_not_firm_fresh"
    if obs["qty"] < 1:
        return "no_order_min_capital"
    depth = obs["intent_ask_shares"]
    if depth is None or not math.isfinite(depth):
        return "no_order_intent_depth_unsupported"
    if obs["qty"] > depth:
        return "no_order_intent_depth_unsupported"
    return "eligible"


def entry_leg_ioc(sq, intent_us: int, limit_price: float, qty: int) -> tuple[dict | None, str]:
    """The LIMIT IOC entry leg: priced at the arrival print exactly ONCE, then gated.

    The shared IOC pricing kernel ``entry_price_leg`` observes the latest RAW state at
    intent + 250ms ONCE and never rests to a later, more favourable print (an invalid /
    crossed / non-regular / absent / stale-but-valid book is an execution UNKNOWN). THIS
    helper keeps only the entry-specific consumer rules on top of that canonical price:
    ``ask > limit`` is a modeled no-L1-match (unfilled cash) and an arrival ASK depth
    below q is an execution UNKNOWN. q is the causal intent quantity and is never
    re-sized.
    """
    arrival = arrival_us(intent_us)
    aq, status = entry_price_leg(sq, arrival)
    if aq is None:
        if status == "unknown_stale_but_valid_at_arrival":
            return None, "unknown_entry_stale_but_valid_at_arrival"
        return None, "unknown_entry_execution"
    if float(aq["ask"]) > float(limit_price) + 1e-12:
        return None, "no_match_at_l1"
    if float(aq["ask_shares"]) < qty:
        return None, "unknown_entry_partial_depth"
    return aq, "conditional_ioc_fill"


def exit_leg_market(sq, day: str, exit_minute: int, session_end_us: int, qty: int):
    """The MARKET exit leg: fresh-quote-gated submission, priced +250ms after it.

    A submitted market order may rest through a genuinely unavailable book (invalid /
    non-regular / absent), never merely for an aged-but-valid quote: that is an UNKNOWN.
    Returns (quote | None, status).
    """
    due_us = minute_us(day, exit_minute)
    submit_us, _ = submission_clock(sq, due_us, session_end_us)
    if submit_us is None:
        return None, "unknown_exit_no_valid_regular_quote"
    xq, xstat, _ = priced_leg(sq, arrival_us(submit_us), session_end_us)
    if xq is None:
        if "stale" in xstat:
            return None, "unknown_exit_stale_but_valid_at_arrival"
        return None, "unknown_exit_no_valid_regular_at_or_after_arrival"
    if float(xq["bid_shares"]) < qty:
        return None, "unknown_exit_partial_depth"
    return xq, "conditional_market_fill"


def label_rows_for_day(day: str, cand: pl.DataFrame, streams: dict) -> list[dict]:
    """Teacher execution of every causal-liquidity-qualified state of one session."""
    session_end = int(cand["session_end"][0]) if cand.height else 959
    session_end_us = minute_us(day, session_end)
    out: list[dict] = []
    base_cols = {c: cand[c].to_list() for c in FEATURES_ALL}
    tickers = cand["ticker"].to_list()
    ts = cand["t"].to_list()
    for i in range(cand.height):
        ticker, t = tickers[i], int(ts[i])
        entry_minute = t + 1
        intent_us = minute_us(day, entry_minute)
        obs = intent_observation(streams.get(ticker), intent_us)
        row = {
            "day": day,
            "ticker": ticker,
            "t": t,
            "entry_intent_us": int(intent_us),
            **{c: base_cols[c][i] for c in FEATURES_ALL},
            "spread_bps": obs["spread_bps"],
            "log_ask_shares": obs["log_ask_shares"],
            "log_bid_shares": obs["log_bid_shares"],
            "quote_age_s": obs["quote_age_s"],
            "depth_imbalance": obs["depth_imbalance"],
            "y_touch_h15": None,
            "y_touch_h60": None,
            "label_known_h15": False,
            "label_known_h60": False,
            "entry_execution_status": None,
            "exit_status_h15": None,
            "exit_status_h60": None,
            "causal_qty": int(obs["qty"]),
            "label_price_basis": LABEL_PRICE_BASIS,
        }
        verdict = intent_verdict(obs)
        if obs["intent_status"] == "quote_not_acquired":
            # DATA UNKNOWN acquisition: reported separately from cash and rule skips.
            row["entry_execution_status"] = "quote_not_acquired"
            row["exit_status_h15"] = "not_applicable_data_unknown"
            row["exit_status_h60"] = "not_applicable_data_unknown"
            out.append(row)
            continue
        if verdict != "eligible":
            # KNOWN cash: the policy sends no order, so the realized return is zero.
            row["entry_execution_status"] = verdict
            row["y_touch_h15"] = 0.0
            row["y_touch_h60"] = 0.0
            row["label_known_h15"] = True
            row["label_known_h60"] = True
            row["exit_status_h15"] = "no_position_cash"
            row["exit_status_h60"] = "no_position_cash"
            out.append(row)
            continue
        aq, estat = entry_leg_ioc(streams[ticker], intent_us, obs["limit_price"], obs["qty"])
        row["entry_execution_status"] = estat
        if estat == "conditional_ioc_fill":
            entry_ask = float(aq["ask"])
            for head in HEADS:
                exit_minute = min(entry_minute + head, session_end)
                xq, xstat = exit_leg_market(
                    streams[ticker], day, exit_minute, session_end_us, obs["qty"]
                )
                # the entry DID fill, so the exit status is the real exit status
                row[f"exit_status_h{head}"] = xstat
                if xq is not None:
                    row[f"y_touch_h{head}"] = float(xq["bid"]) / entry_ask - 1.0
                    row[f"label_known_h{head}"] = True
        elif estat == "no_match_at_l1":
            # KNOWN cash: the order was sent and did not fill, the cash is unfilled.
            row["y_touch_h15"] = 0.0
            row["y_touch_h60"] = 0.0
            row["label_known_h15"] = True
            row["label_known_h60"] = True
            row["exit_status_h15"] = "no_position_cash"
            row["exit_status_h60"] = "no_position_cash"
        else:
            row["exit_status_h15"] = "not_applicable_unknown_entry"
            row["exit_status_h60"] = "not_applicable_unknown_entry"
        out.append(row)
    return out


def label_day(day: str, data_root: Path, supplemental_roots: tuple[Path, ...]):
    """One session's canonical label rows plus its honest coverage counters."""
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
        "tickers_with_quote_stream": 0,
        "quote_day_file_present": True,
        "entry_status_counts": {},
        "known_counts": {},
    }
    if not cand.height:
        return empty_labels(), cov
    tickers = sorted(set(cand["ticker"].to_list()))
    cov["tickers_qualified"] = len(tickers)
    quote_frame = quote_day_frame(data_root, day, set(tickers), supplemental_roots)
    streams = symbol_quotes(quote_frame, day)
    cov["tickers_with_quote_stream"] = len(streams)
    if not (data_root / "sip" / "net" / "quotes" / f"{day}.parquet").exists():
        cov["quote_day_file_present"] = False
    del quote_frame
    rows = label_rows_for_day(day, cand, streams)
    statuses: Counter = Counter(r["entry_execution_status"] for r in rows)
    known = {
        "h15": int(sum(1 for r in rows if r["label_known_h15"])),
        "h60": int(sum(1 for r in rows if r["label_known_h60"])),
        "h15_filled_touch": int(
            sum(
                1
                for r in rows
                if r["label_known_h15"] and r["entry_execution_status"] == "conditional_ioc_fill"
            )
        ),
        "h60_filled_touch": int(
            sum(
                1
                for r in rows
                if r["label_known_h60"] and r["entry_execution_status"] == "conditional_ioc_fill"
            )
        ),
        "h15_known_cash_zero": int(
            sum(
                1
                for r in rows
                if r["label_known_h15"] and r["entry_execution_status"] in KNOWN_CASH_STATUSES
            )
        ),
        "h60_known_cash_zero": int(
            sum(
                1
                for r in rows
                if r["label_known_h60"] and r["entry_execution_status"] in KNOWN_CASH_STATUSES
            )
        ),
        "h15_unknown": int(sum(1 for r in rows if not r["label_known_h15"])),
        "h60_unknown": int(sum(1 for r in rows if not r["label_known_h60"])),
    }
    cov["entry_status_counts"] = dict(statuses)
    cov["known_counts"] = known
    frame_out = pl.DataFrame(rows, schema={c: LABEL_TYPES[c] for c in LABEL_COLUMNS})
    return frame_out, cov


def label_block(
    out_dir: Path, days: list[str], resume: bool, supplemental_roots: tuple[Path, ...]
) -> dict:
    """Build (or resume) every day's canonical labels atomically, one day at a time."""
    producer = producer_sha256()
    contract_sha = _digest_bytes(CONTRACT)
    sup_digest, sup_entries = supplement_digest(supplemental_roots)
    coverage = {
        "days_total": len(days),
        "days_cached": 0,
        "days_scored": 0,
        "qualified_rows": 0,
        "entry_status_counts": {},
        "known_counts": {},
        "missing_quote_day_files": [],
        "days_without_quote_stream": [],
    }
    intent_totals: Counter = Counter()
    known_totals: Counter = Counter()
    infos = []
    for day in days:
        lpath, cpath = label_paths(out_dir, day)
        expect = _digest_bytes(
            {
                "day": day,
                "producer": producer,
                "contract": contract_sha,
                "supplements": sup_digest,
                "schema": LABEL_SCHEMA,
            }
        )
        fresh, prior = False, None
        if resume and cpath.exists() and lpath.exists():
            try:
                prior = json.loads(cpath.read_text())
                fresh = prior.get("resume_hash") == expect and prior.get("schema") == LABEL_SCHEMA
            except (json.JSONDecodeError, OSError):
                fresh = False
        if fresh:
            infos.append(
                {"day": day, "resumed": True, "coverage": (prior or {}).get("coverage", {})}
            )
        else:
            labels, cov = label_day(day, DATA_ROOT, supplemental_roots)
            lpath.parent.mkdir(parents=True, exist_ok=True)
            tmp = lpath.with_name(f"{lpath.name}.tmp{os.getpid()}")
            labels.write_parquet(tmp)
            os.replace(tmp, lpath)
            write_json_atomic(
                cpath,
                {
                    "day": day,
                    "rows": int(labels.height),
                    "resume_hash": expect,
                    "producer_sha256": producer,
                    "contract_sha256": contract_sha,
                    "supplement_digest": sup_digest,
                    "labels_sha256": sha256_file(lpath),
                    "schema": LABEL_SCHEMA,
                    "coverage": cov,
                },
            )
            infos.append({"day": day, "resumed": False, "coverage": cov})
            scored = len([i for i in infos if not i["resumed"]])
            if scored % 50 == 0:
                print(
                    f"[labels] {scored} scored, last {day}, qualified={cov['qualified_rows']}",
                    flush=True,
                )
    for info in infos:
        cov = info["coverage"] or {}
        coverage["qualified_rows"] += int(cov.get("qualified_rows", 0))
        intent_totals.update(cov.get("entry_status_counts") or {})
        known_totals.update(cov.get("known_counts") or {})
        if cov.get("quote_day_file_present") is False:
            coverage["missing_quote_day_files"].append(info["day"])
        if (
            int(cov.get("tickers_with_quote_stream", 0)) == 0
            and int(cov.get("qualified_rows", 0)) > 0
        ):
            coverage["days_without_quote_stream"].append(info["day"])
    coverage.update(
        {
            "days_cached": len(infos),
            "days_scored": len([i for i in infos if not i["resumed"]]),
            "entry_status_counts": dict(intent_totals),
            "known_counts": dict(known_totals),
            "supplement_digest": sup_digest,
            "supplements": sup_entries,
        }
    )
    return coverage


def read_day_labels(out_dir: Path, day: str) -> pl.DataFrame:
    path, _ = label_paths(out_dir, day)
    if not path.exists():
        return empty_labels()
    return pl.read_parquet(path)


# ----- fit stage: two fixed actual-touch heads -------------------------------
def feature_matrix31(df: pl.DataFrame) -> np.ndarray:
    """The 31-dim causal matrix: Float64 in FEATURE_ORDER, nulls filled with 0.0.

    Same convention as ``alpha_open_learned.feature_matrix`` (which covers the 26 base
    features only); the five quote features join in the declared order.
    """
    return (
        df.select([pl.col(c).cast(pl.Float64, strict=False) for c in FEATURE_ORDER])
        .fill_null(0.0)
        .to_numpy()
    )


def fit_rows(frame: pl.DataFrame, head: int) -> pl.DataFrame:
    """Known-label rows with an OBSERVABLE intent book for one head.

    Known cash zeros stay in (the policy really realized zero). Unknown executions are
    out, and so are known zeros whose intent book was never observable: their five quote
    features are NULL, and imputing a tight book for an unobserved one would teach the
    head a fiction. Every excluded bucket is counted by the caller and disclosed.
    """
    return frame.filter(
        (pl.col(f"label_known_h{head}"))
        & (~pl.col("entry_execution_status").is_in(list(NO_INTENT_BOOK_STATUSES)))
    )


def equal_day_weights(days: list[str]) -> np.ndarray:
    """One equal total weight per session (1/n_rows of that day), mean-normalized."""
    counts: Counter = Counter(days)
    w = np.asarray([1.0 / counts[d] for d in days], dtype=float)
    mean = float(w.mean())
    return w / mean if mean > 0 else w


def _fit_report_row(df: pl.DataFrame, head: int) -> dict:
    y = df[f"y_touch_h{head}"].to_numpy().astype(float)
    status = df["entry_execution_status"]
    return {
        "rows": int(df.height),
        "days": int(df["day"].n_unique()),
        "day_min": df["day"].min(),
        "day_max": df["day"].max(),
        "y_unclipped": {
            "count": int(y.size),
            "mean": float(y.mean()) if y.size else None,
            "std": float(y.std()) if y.size else None,
            "p05": float(np.percentile(y, 5)) if y.size else None,
            "p50": float(np.percentile(y, 50)) if y.size else None,
            "p95": float(np.percentile(y, 95)) if y.size else None,
            "min": float(y.min()) if y.size else None,
            "max": float(y.max()) if y.size else None,
        },
        "entry_status_composition": {
            s: int((status == s).sum()) for s in sorted(set(status.to_list()))
        },
        "quote_feature_null_counts": {c: int(df[c].null_count()) for c in QUOTE_FEATURES},
    }


def fit_stage(out_dir: Path, days_train: list[str]) -> dict:
    """Fit the two fixed heads on the train block's known, observable labels."""
    if not days_train:
        raise SystemExit(
            f"[touch-payoff] no train days selected; the fit stage needs {TRAIN_START}..{TRAIN_END}"
        )
    frames = [read_day_labels(out_dir, day) for day in days_train]
    present = [f for f in frames if f.height]
    if not present:
        raise SystemExit(
            "[touch-payoff] no train labels on disk; run the labels stage for the train block first"
        )
    train = pl.concat(present, how="vertical_relaxed")
    models: dict[int, dict] = {}
    report: dict = {
        "params": LGB_PARAMS,
        "clip_fit": list(CLIP),
        "seed": SEED,
        "sample_weight": "equal-day 1/n_day, mean-normalized to 1",
        "feature_order": FEATURE_ORDER,
        "n_features": len(FEATURE_ORDER),
        "ticker_or_date_identifiers": False,
        "train_block": {"start": TRAIN_START, "end": TRAIN_END, "days": len(days_train)},
        "label_price_basis": LABEL_PRICE_BASIS,
        "quantity_fee_cap_bps": LABEL_COST_BPS,
        "heads": {},
    }
    for head in HEADS:
        fit_df = fit_rows(train, head)
        excluded = {
            "unknown_leg": int(train.filter(~pl.col(f"label_known_h{head}")).height),
            "known_no_intent_book": int(
                train.filter(
                    pl.col(f"label_known_h{head}")
                    & pl.col("entry_execution_status").is_in(list(NO_INTENT_BOOK_STATUSES))
                ).height
            ),
        }
        if not fit_df.height:
            raise SystemExit(
                f"[touch-payoff] no fit rows for h{head}: known-with-observable-intent "
                "labels are empty; run the labels stage first"
            )
        raw = fit_df[f"y_touch_h{head}"].to_numpy().astype(float)  # unclipped, for stats
        y = np.clip(raw, CLIP[0], CLIP[1])  # clip ONLY at fit
        x = feature_matrix31(fit_df)
        weights = equal_day_weights(fit_df["day"].to_list())
        model = LGBMRegressor(**LGB_PARAMS)
        model.fit(x, y, sample_weight=weights)
        models[head] = model
        gain = np.asarray(model.booster_.feature_importance("gain"), dtype=float)
        head_report = _fit_report_row(fit_df, head)
        head_report["excluded"] = excluded
        head_report["label_clip_for_fit"] = list(CLIP)
        head_report["clip_at_bounds"] = {
            "at_lower": int((raw <= CLIP[0]).sum()),
            "at_upper": int((raw >= CLIP[1]).sum()),
        }
        head_report["equal_day_weight"] = {
            "sessions": int(fit_df["day"].n_unique()),
            "weight_sum": float(weights.sum()),
            "weight_mean": float(weights.mean()),
            "min_row_weight": float(weights.min()),
            "max_row_weight": float(weights.max()),
        }
        head_report["gain_importance"] = dict(
            sorted(zip(FEATURE_ORDER, gain.tolist(), strict=True), key=lambda kv: -kv[1])
        )
        report["heads"][f"h{head}"] = head_report
        composition = head_report["entry_status_composition"]
        print(
            f"[fit] h{head}: {fit_df.height} rows over "
            f"{fit_df['day'].n_unique()} sessions; known cash zeros: "
            f"{composition.get('no_match_at_l1', 0)} no-match, "
            f"{composition.get('no_order_min_capital', 0)} "
            f"min-capital, "
            f"{composition.get('no_order_intent_depth_unsupported', 0)} "
            f"depth-skip; excluded unknown/no-book "
            f"{excluded['unknown_leg']}/{excluded['known_no_intent_book']}",
            flush=True,
        )
    model_dir = out_dir / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for head in HEADS:
        path = fitted_model_path(out_dir, head)
        bundle = {
            "horizon": head,
            "feature_order": list(FEATURE_ORDER),
            "lgbm": models[head],
            "params": LGB_PARAMS,
            "clip_fit": list(CLIP),
            "seed": SEED,
            "sample_weight": "equal-day 1/n_day mean-normalized",
            "label_price_basis": LABEL_PRICE_BASIS,
            "quantity_fee_cap_bps": LABEL_COST_BPS,
            "fit_block": {"start": TRAIN_START, "end": TRAIN_END, "days": len(days_train)},
            "fit_rows": int(report["heads"][f"h{head}"]["rows"]),
            "producer": Path(__file__).name,
        }
        joblib.dump(bundle, path)
        paths[f"h{head}"] = {"path": str(path), "sha256": sha256_file(path)}
    report["models"] = paths
    report["producer_sha256"] = producer_sha256()
    report["contract_sha256"] = _digest_bytes(CONTRACT)
    report["engine_helper_shas"] = engine_helper_shas()
    write_json_atomic(out_dir / "fit_report.json", report)
    print(
        f"[fit] models -> {model_dir} (h15 {paths['h15']['sha256'][:12]}, "
        f"h60 {paths['h60']['sha256'][:12]})",
        flush=True,
    )
    return report


def load_fitted_models(out_dir: Path) -> dict:
    models = {}
    for head in HEADS:
        path = fitted_model_path(out_dir, head)
        if not path.exists():
            raise SystemExit(
                f"[touch-payoff] fitted model missing: {path}; run the fit stage first"
            )
        bundle = joblib.load(path)
        order = list(bundle.get("feature_order") or [])
        if order != list(FEATURE_ORDER):
            raise SystemExit(
                f"[touch-payoff] h{head} feature_order != this producer's FEATURE_ORDER"
            )
        models[head] = bundle
    return models


# ----- scored states (per-day, atomic, resumable) ----------------------------
STATE_COLUMNS = (
    "day",
    "ticker",
    "t",
    "entry_minute",
    "intent_us",
    "session_end",
    "intent_status",
    "intent_reason",
    "intent_ask",
    "intent_bid",
    "intent_age_s",
    "intent_spread_bps",
    "intent_ask_shares",
    "limit_price",
    "qty",
    "reserved_usd",
    "pred_h15",
    "pred_h60",
    "y_touch_h15",
    "y_touch_h60",
    "label_known_h15",
    "label_known_h60",
    "entry_execution_status",
)
STATE_TYPES = {
    "day": pl.String,
    "ticker": pl.String,
    "t": pl.Int64,
    "entry_minute": pl.Int64,
    "intent_us": pl.Int64,
    "session_end": pl.Int64,
    "intent_status": pl.String,
    "intent_reason": pl.String,
    "intent_ask": pl.Float64,
    "intent_bid": pl.Float64,
    "intent_age_s": pl.Float64,
    "intent_spread_bps": pl.Float64,
    "intent_ask_shares": pl.Float64,
    "limit_price": pl.Float64,
    "qty": pl.Int64,
    "reserved_usd": pl.Float64,
    "pred_h15": pl.Float64,
    "pred_h60": pl.Float64,
    "y_touch_h15": pl.Float64,
    "y_touch_h60": pl.Float64,
    "label_known_h15": pl.Boolean,
    "label_known_h60": pl.Boolean,
    "entry_execution_status": pl.String,
}


def empty_states() -> pl.DataFrame:
    return pl.DataFrame(schema={c: STATE_TYPES[c] for c in STATE_COLUMNS})


def state_paths(out_dir: Path, day: str) -> tuple[Path, Path]:
    return out_dir / "states" / f"{day}.parquet", out_dir / "states" / f"{day}.cov.json"


def states_day(day: str, models: dict, out_dir: Path, supplemental_roots: tuple[Path, ...]):
    """Score one session: the fitted heads on the label features + the intent facts."""
    labels = read_day_labels(out_dir, day)
    cov = {
        "label_rows": int(labels.height),
        "intent_reason_counts": {},
        "tickers_with_quote_stream": 0,
    }
    if not labels.height:
        return empty_states(), cov
    panel_path = PANEL_DAYS / f"{day}.parquet"
    if not panel_path.exists():
        raise SystemExit(f"[touch-payoff] panel day missing for states: {panel_path}")
    session_end = int(pl.read_parquet(panel_path)["session_end"][0])
    tickers = sorted(set(labels["ticker"].to_list()))
    quote_frame = quote_day_frame(DATA_ROOT, day, set(tickers), supplemental_roots)
    streams = symbol_quotes(quote_frame, day)
    cov["tickers_with_quote_stream"] = len(streams)
    del quote_frame
    preds = {
        head: np.asarray(models[head]["lgbm"].predict(feature_matrix31(labels)), dtype=float)
        for head in HEADS
    }
    rows: list[dict] = []
    reasons: Counter = Counter()
    for i, r in enumerate(labels.iter_rows(named=True)):
        entry_minute = int(r["t"]) + 1
        intent_us = minute_us(day, entry_minute)
        obs = intent_observation(streams.get(r["ticker"]), intent_us)
        reason = intent_verdict(obs)
        reasons[reason] += 1
        rows.append(
            {
                "day": day,
                "ticker": r["ticker"],
                "t": int(r["t"]),
                "entry_minute": entry_minute,
                "intent_us": int(intent_us),
                "session_end": session_end,
                "intent_status": obs["intent_status"],
                "intent_reason": reason,
                "intent_ask": obs["intent_ask"],
                "intent_bid": obs["intent_bid"],
                "intent_age_s": obs["quote_age_s"],
                "intent_spread_bps": obs["spread_bps"],
                "intent_ask_shares": obs["intent_ask_shares"],
                "limit_price": obs["limit_price"],
                "qty": int(obs["qty"]),
                "reserved_usd": (
                    reserved_usd(int(obs["qty"]), obs["limit_price"], LABEL_COST_BPS)
                    if obs["limit_price"] is not None and obs["qty"] >= 1
                    else 0.0
                ),
                "pred_h15": float(preds[15][i]),
                "pred_h60": float(preds[60][i]),
                "y_touch_h15": r["y_touch_h15"],
                "y_touch_h60": r["y_touch_h60"],
                "label_known_h15": bool(r["label_known_h15"]),
                "label_known_h60": bool(r["label_known_h60"]),
                "entry_execution_status": r["entry_execution_status"],
            }
        )
    cov["intent_reason_counts"] = dict(reasons)
    frame = pl.DataFrame(rows, schema={c: STATE_TYPES[c] for c in STATE_COLUMNS})
    return frame, cov


def states_block(out_dir: Path, models: dict, days: list[str], resume: bool, supp) -> dict:
    """Build (or resume) every day's scored states atomically, one day at a time."""
    producer = producer_sha256()
    contract_sha = _digest_bytes(CONTRACT)
    sup_digest, _ = supplement_digest(supp)
    model_shas = {f"h{h}": sha256_file(fitted_model_path(out_dir, h)) for h in HEADS}
    coverage = {
        "days_total": len(days),
        "days_cached": 0,
        "days_scored": 0,
        "label_rows": 0,
        "intent_reason_counts": {},
    }
    reason_totals: Counter = Counter()
    infos = []
    for day in days:
        lpath, lcov = label_paths(out_dir, day)
        if not lpath.exists():
            raise SystemExit(
                f"[touch-payoff] labels missing for {day} ({lpath}); run the labels stage "
                "for the whole block before scoring it"
            )
        try:
            labels_sha = (
                json.loads(lcov.read_text()).get("labels_sha256") if lcov.exists() else None
            )
        except (json.JSONDecodeError, OSError):
            labels_sha = None
        if labels_sha is None:
            labels_sha = sha256_file(lpath)
        spath, cpath = state_paths(out_dir, day)
        expect = _digest_bytes(
            {
                "day": day,
                "producer": producer,
                "contract": contract_sha,
                "supplements": sup_digest,
                "models": model_shas,
                "labels": labels_sha,
                "schema": STATE_SCHEMA,
            }
        )
        fresh, prior = False, None
        if resume and cpath.exists() and spath.exists():
            try:
                prior = json.loads(cpath.read_text())
                fresh = prior.get("resume_hash") == expect and prior.get("schema") == STATE_SCHEMA
            except (json.JSONDecodeError, OSError):
                fresh = False
        if fresh:
            infos.append(
                {"day": day, "resumed": True, "coverage": (prior or {}).get("coverage", {})}
            )
        else:
            states, cov = states_day(day, models, out_dir, supp)
            spath.parent.mkdir(parents=True, exist_ok=True)
            tmp = spath.with_name(f"{spath.name}.tmp{os.getpid()}")
            states.write_parquet(tmp)
            os.replace(tmp, spath)
            write_json_atomic(
                cpath,
                {
                    "day": day,
                    "rows": int(states.height),
                    "resume_hash": expect,
                    "producer_sha256": producer,
                    "contract_sha256": contract_sha,
                    "supplement_digest": sup_digest,
                    "labels_sha256": labels_sha,
                    "states_sha256": sha256_file(spath),
                    "model_shas": model_shas,
                    "schema": STATE_SCHEMA,
                    "coverage": cov,
                },
            )
            infos.append({"day": day, "resumed": False, "coverage": cov})
            scored = len([i for i in infos if not i["resumed"]])
            if scored % 50 == 0:
                print(f"[states] {scored} scored, last {day}, rows={states.height}", flush=True)
    for info in infos:
        cov = info["coverage"] or {}
        coverage["label_rows"] += int(cov.get("label_rows", 0))
        reason_totals.update(cov.get("intent_reason_counts") or {})
    coverage.update(
        {
            "days_cached": len(infos),
            "days_scored": len([i for i in infos if not i["resumed"]]),
            "intent_reason_counts": dict(reason_totals),
        }
    )
    return coverage


def read_day_states(out_dir: Path, day: str) -> pl.DataFrame:
    path, _ = state_paths(out_dir, day)
    if not path.exists():
        return empty_states()
    return pl.read_parquet(path)


# ----- per-view causal replay (fresh-quote-gated IOC entry, market exit) ------
@dataclass(frozen=True)
class View:
    """One (head, net-clearance bar) cell. Same models, same universe; only the bar."""

    head: int
    clearance: float

    @property
    def key(self) -> str:
        return f"h{self.head}|clear{int(round(self.clearance * 10_000))}bps"

    @property
    def label(self) -> str:
        return f"h{self.head}_net{int(round(self.clearance * 10_000))}bps"


VIEWS = tuple(View(h, c) for h in HEADS for c in CLEARANCES)
VIEWS_BY_KEY = {v.key: v for v in VIEWS}


def view_status(row: dict, view: View) -> tuple[str, bool]:
    """Per-view eligibility: the causal intent gates first, then the clearance bar.

    The bar is ``10_000 * predicted_gross >= ENTRY_OVERHEAD_BPS + 10_000 * clearance``:
    the predicted touch payoff must clear the realistic provider-extra fee residual
    (25bps) plus the fixed view clearance. The predicted touch is the ASK->BID NBBO
    gross (exit_bid / entry_ask - 1), so the observed spread is ALREADY inside the
    predicted number and is NEVER re-charged here; it stays recorded as the causal
    intent ``intent_spread_bps`` DPT feature regardless of this gate.
    """
    if row["intent_reason"] != "eligible":
        return row["intent_reason"], False
    pred = float(row[f"pred_h{view.head}"])
    if 10_000.0 * pred < ENTRY_OVERHEAD_BPS + 10_000.0 * view.clearance:
        return "touch_payoff_below_clearance", False
    return "eligible", True


DAILY_COLUMNS = (
    "day",
    "view_key",
    "head",
    "clearance",
    "states_total",
    "signals",
    "intents_funded",
    "attempts",
    "fills",
    "known_fills",
    "unknown_fills",
    "no_match_fills",
    *(f"known_usd_{int(r)}" for r in RUNG_COSTS),
    *(f"lower_bound_usd_{int(r)}" for r in RUNG_COSTS),
    "peak_reserved_usd",
    "positions_peak",
    "positions_mean",
    "no_order_intent_not_firm_fresh",
    "no_order_min_capital",
    "no_order_intent_depth_unsupported",
    "quote_not_acquired",
    "touch_payoff_below_clearance",
    "overlap_skips",
    "cooldown_skips",
    "max_attempt_skips",
    "cash_or_slot_skips",
)
DAILY_TYPES = {
    "day": pl.String,
    "view_key": pl.String,
    "head": pl.Int64,
    "clearance": pl.Float64,
    "states_total": pl.Int64,
    "signals": pl.Int64,
    "intents_funded": pl.Int64,
    "attempts": pl.Int64,
    "fills": pl.Int64,
    "known_fills": pl.Int64,
    "unknown_fills": pl.Int64,
    "no_match_fills": pl.Int64,
    **{f"known_usd_{int(r)}": pl.Float64 for r in RUNG_COSTS},
    **{f"lower_bound_usd_{int(r)}": pl.Float64 for r in RUNG_COSTS},
    "peak_reserved_usd": pl.Float64,
    "positions_peak": pl.Int64,
    "positions_mean": pl.Float64,
    "no_order_intent_not_firm_fresh": pl.Int64,
    "no_order_min_capital": pl.Int64,
    "no_order_intent_depth_unsupported": pl.Int64,
    "quote_not_acquired": pl.Int64,
    "touch_payoff_below_clearance": pl.Int64,
    "overlap_skips": pl.Int64,
    "cooldown_skips": pl.Int64,
    "max_attempt_skips": pl.Int64,
    "cash_or_slot_skips": pl.Int64,
}
TRADE_COLUMNS = (
    "day",
    "view_key",
    "head",
    "clearance",
    "ticker",
    "t",
    "entry_minute",
    "intent_us",
    "pred",
    "limit_price",
    "qty",
    "reserved_usd",
    "attempt_index",
    "positions_at_entry",
    "entry_status",
    "entry_ask",
    "entry_bid",
    "entry_age_s",
    "entry_quote_us",
    "exit_minute",
    "exit_submit_us",
    "exit_submit_status",
    "exit_bid",
    "exit_age_s",
    "exit_quote_us",
    "exit_status",
    "actual_exit_us",
    "fill_status",
    "y_touch",
    "label_y",
    *(f"net_usd_{int(r)}" for r in RUNG_COSTS),
)
TRADE_TYPES = {
    "day": pl.String,
    "view_key": pl.String,
    "head": pl.Int64,
    "clearance": pl.Float64,
    "ticker": pl.String,
    "t": pl.Int64,
    "entry_minute": pl.Int64,
    "intent_us": pl.Int64,
    "pred": pl.Float64,
    "limit_price": pl.Float64,
    "qty": pl.Int64,
    "reserved_usd": pl.Float64,
    "attempt_index": pl.Int64,
    "positions_at_entry": pl.Int64,
    "entry_status": pl.String,
    "entry_ask": pl.Float64,
    "entry_bid": pl.Float64,
    "entry_age_s": pl.Float64,
    "entry_quote_us": pl.Int64,
    "exit_minute": pl.Int64,
    "exit_submit_us": pl.Int64,
    "exit_submit_status": pl.String,
    "exit_bid": pl.Float64,
    "exit_age_s": pl.Float64,
    "exit_quote_us": pl.Int64,
    "exit_status": pl.String,
    "actual_exit_us": pl.Int64,
    "fill_status": pl.String,
    "y_touch": pl.Float64,
    "label_y": pl.Float64,
    **{f"net_usd_{int(r)}": pl.Float64 for r in RUNG_COSTS},
}
SKIP_REASONS = (
    "no_order_intent_not_firm_fresh",
    "no_order_min_capital",
    "no_order_intent_depth_unsupported",
    "quote_not_acquired",
    "touch_payoff_below_clearance",
    "overlap_skips",
    "cooldown_skips",
    "max_attempt_skips",
    "cash_or_slot_skips",
)


def empty_daily() -> pl.DataFrame:
    return pl.DataFrame(schema={c: DAILY_TYPES[c] for c in DAILY_COLUMNS})


def empty_trades() -> pl.DataFrame:
    return pl.DataFrame(schema={c: TRADE_TYPES[c] for c in TRADE_COLUMNS})


def resolve_trade(day: str, r: dict, view: View, sq, session_end: int, session_end_us: int) -> dict:
    """Resolve one funded intent: the IOC-limit entry leg, then the market exit leg."""
    head = view.head
    qty = int(r["qty"])
    record = {
        "day": day,
        "view_key": view.key,
        "head": head,
        "clearance": view.clearance,
        "ticker": r["ticker"],
        "t": int(r["t"]),
        "entry_minute": int(r["entry_minute"]),
        "intent_us": int(r["intent_us"]),
        "pred": float(r[f"pred_h{head}"]),
        "limit_price": float(r["limit_price"]),
        "qty": qty,
        "reserved_usd": float(r["reserved_usd"]),
        "label_y": r[f"y_touch_h{head}"],
        "entry_status": None,
        "entry_ask": None,
        "entry_bid": None,
        "entry_age_s": None,
        "entry_quote_us": None,
        "exit_minute": min(int(r["entry_minute"]) + head, session_end),
        "exit_submit_us": None,
        "exit_submit_status": None,
        "exit_bid": None,
        "exit_age_s": None,
        "exit_quote_us": None,
        "exit_status": None,
        "actual_exit_us": None,
        "fill_status": None,
        "y_touch": None,
    }
    for rung in RUNG_COSTS:
        record[f"net_usd_{int(rung)}"] = None

    # ---- entry leg: the causal IOC limit was fixed at the intent clock.
    aq, estat = entry_leg_ioc(sq, int(r["intent_us"]), float(r["limit_price"]), qty)
    record["entry_status"] = estat
    if estat != "conditional_ioc_fill":
        record["fill_status"] = (
            "no_match_at_l1_unfilled_cash"
            if estat == "no_match_at_l1"
            else "unknown_entry_execution"
        )
        record["actual_exit_us"] = (
            arrival_us(int(r["intent_us"])) if estat == "no_match_at_l1" else session_end_us + 1
        )
        return record
    entry_ask = float(aq["ask"])
    record["entry_ask"] = entry_ask
    record["entry_bid"] = float(aq["bid"])
    record["entry_age_s"] = float(aq["age_s"])
    record["entry_quote_us"] = int(aq["quote_us"])

    # ---- exit leg: fresh-quote-gated submission, priced +250ms after it.
    due_us = minute_us(day, int(record["exit_minute"]))
    submit_us, submit_status = submission_clock(sq, due_us, session_end_us)
    record["exit_submit_us"] = submit_us
    record["exit_submit_status"] = submit_status
    if submit_us is None:
        record["fill_status"] = "unknown_exit_execution"
        record["exit_status"] = "unknown_exit_no_valid_regular_quote"
        record["actual_exit_us"] = session_end_us + 1
        return record
    xq, xstat, price_clock = priced_leg(sq, arrival_us(submit_us), session_end_us)
    if xq is None:
        record["fill_status"] = "unknown_exit_execution"
        record["exit_status"] = (
            "unknown_exit_stale_but_valid_at_arrival"
            if "stale" in xstat
            else "unknown_exit_no_valid_regular_at_or_after_arrival"
        )
        record["actual_exit_us"] = session_end_us + 1
        return record
    if float(xq["bid_shares"]) < qty:
        record["fill_status"] = "unknown_exit_execution"
        record["exit_status"] = "unknown_exit_partial_depth"
        record["actual_exit_us"] = session_end_us + 1
        return record
    exit_bid = float(xq["bid"])
    record["exit_bid"] = exit_bid
    record["exit_age_s"] = float(xq["age_s"])
    record["exit_quote_us"] = int(xq["quote_us"])
    record["exit_status"] = "conditional_market_fill"
    record["fill_status"] = "conditional_fill"
    # The execution clock is the clock the price was actually READ at: the arrival when
    # the latest state was fresh, or the first future regular print's own timestamp when
    # the working order rested. Never the fresh prior print's own quote_us (it can
    # predate the arrival) and never the nominal arrival of a rested order: cash, the
    # slot and the cooldown all wait for the REAL execution clock.
    record["actual_exit_us"] = int(price_clock)
    record["y_touch"] = exit_bid / entry_ask - 1.0
    for rung in RUNG_COSTS:
        record[f"net_usd_{int(rung)}"] = net_usd(qty, exit_bid, entry_ask, rung)
    return record


def replay_day(
    day: str, states: pl.DataFrame, streams: dict, view: View
) -> tuple[dict, list[dict]]:
    """One view, one session: fresh-quote-gated entries under the funded $750 book.

    Chronological by intent clock; the highest-score eligible intents of a simultaneous
    clock are funded (up to the slot and cash limits) BEFORE any arrival outcome is seen,
    so a fourth same-clock candidate is never substituted after an unfilled one. Cash
    comes back only at an ACTUAL exit clock, an unmatched entry's arrival, or the
    session end; an UNKNOWN position keeps its slot to the session end.
    """
    daily = dict.fromkeys(DAILY_COLUMNS, 0)
    daily.update(
        {
            "day": day,
            "view_key": view.key,
            "head": view.head,
            "clearance": view.clearance,
            "states_total": int(states.height),
            "peak_reserved_usd": 0.0,
            "positions_mean": 0.0,
        }
    )
    for rung in RUNG_COSTS:
        daily[f"known_usd_{int(rung)}"] = 0.0
        daily[f"lower_bound_usd_{int(rung)}"] = 0.0
    if not states.height:
        return daily, []
    session_end = int(states["session_end"][0])
    session_end_us = minute_us(day, session_end)
    pred_col = f"pred_h{view.head}"
    groups: dict[int, list[dict]] = {}
    signals = 0
    for r in states.iter_rows(named=True):
        reason, ok = view_status(r, view)
        if not ok:
            if reason in daily:
                daily[reason] += 1
            continue
        signals += 1
        groups.setdefault(int(r["entry_minute"]), []).append(r)
    daily["signals"] = signals
    if not groups:
        return daily, []

    cash, active = float(BOOK), set()
    reserved_out = 0.0
    attempts: dict[str, int] = {}
    cooldown_until: dict[str, int] = {}
    queue: list[tuple[int, int, str, float]] = []
    seq = 0
    trades: list[dict] = []
    positions_samples: list[int] = []
    for minute in sorted(groups):
        intent_us = minute_us(day, minute)
        while queue and queue[0][0] <= intent_us:
            _, _, sym, proceeds = heapq.heappop(queue)
            active.discard(sym)
            cash += proceeds
            reserved_out -= proceeds
        # fund the highest-score eligible intents of this clock BEFORE any arrival
        admitted: list[dict] = []
        for r in sorted(groups[minute], key=lambda x: (-float(x[pred_col]), x["ticker"])):
            sym = r["ticker"]
            if sym in active:
                daily["overlap_skips"] += 1
                continue
            if intent_us < int(cooldown_until.get(sym, -(1 << 62))):
                daily["cooldown_skips"] += 1
                continue
            if int(attempts.get(sym, 0)) >= MAX_ATTEMPTS:
                daily["max_attempt_skips"] += 1
                continue
            if len(active) >= MAX_SLOTS or cash + 1e-9 < float(r["reserved_usd"]):
                daily["cash_or_slot_skips"] += 1
                continue
            attempts[sym] = int(attempts.get(sym, 0)) + 1
            cash -= float(r["reserved_usd"])
            reserved_out += float(r["reserved_usd"])
            daily["peak_reserved_usd"] = max(daily["peak_reserved_usd"], reserved_out)
            active.add(sym)
            positions_samples.append(len(active))
            daily["intents_funded"] += 1
            daily["attempts"] += 1
            admitted.append(r)
        for r in admitted:
            record = resolve_trade(day, r, view, streams[r["ticker"]], session_end, session_end_us)
            record["attempt_index"] = int(attempts[r["ticker"]])
            record["positions_at_entry"] = len(active)
            status = record["fill_status"]
            release = int(record["actual_exit_us"])
            if status == "no_match_at_l1_unfilled_cash":
                # No position was opened: the ticket is released at the arrival clock
                # and there is no exit to anchor a cooldown on (the attempt counts).
                daily["no_match_fills"] += 1
            else:
                daily["unknown_fills" if "unknown" in status else "fills"] += 1
                if status == "conditional_fill":
                    daily["known_fills"] += 1
                    for rung in RUNG_COSTS:
                        daily[f"known_usd_{int(rung)}"] += float(record[f"net_usd_{int(rung)}"])
                # A position - conditional or UNKNOWN - is open until its ACTUAL
                # resolution, and the ticker is then blocked for a flat 5 minutes.
                cooldown_until[r["ticker"]] = release + COOLDOWN_MIN * 60_000_000
            heapq.heappush(queue, (release, seq, r["ticker"], float(r["reserved_usd"])))
            seq += 1
            trades.append(record)
    daily["positions_peak"] = max(positions_samples) if positions_samples else 0
    daily["positions_mean"] = float(np.mean(positions_samples)) if positions_samples else 0.0
    for rung in RUNG_COSTS:
        daily[f"lower_bound_usd_{int(rung)}"] = (
            daily[f"known_usd_{int(rung)}"] - ORDER_BUDGET * daily["unknown_fills"]
        )
    return daily, trades


def replay_block(
    out_dir: Path, block: str, days: list[str], models: dict, resume: bool, supp
) -> dict:
    """Replay every view on every day of one block, writing resumable per-day parts."""
    producer = producer_sha256()
    contract_sha = _digest_bytes(CONTRACT)
    sup_digest, _ = supplement_digest(supp)
    model_shas = {f"h{h}": sha256_file(fitted_model_path(out_dir, h)) for h in HEADS}
    root = out_dir / "replay" / block
    root.mkdir(parents=True, exist_ok=True)
    todo, infos = [], []
    coverage = {
        "block": block,
        "days_total": len(days),
        "days_cached": 0,
        "days_replayed": 0,
        "signals": 0,
        "intents_funded": 0,
        "fills": 0,
        "unknown_fills": 0,
        "no_match_fills": 0,
    }
    for day in days:
        spath, scov = state_paths(out_dir, day)
        if not spath.exists():
            raise SystemExit(
                f"[touch-payoff] scored states missing for {day} ({spath}); run the "
                "states stage for the whole block before replaying it"
            )
        states_sha = json.loads(scov.read_text()).get("states_sha256") if scov.exists() else None
        if states_sha is None:
            states_sha = sha256_file(spath)
        dpath, tpath, cpath = (
            root / f"{day}.parquet",
            root / f"{day}.trades.parquet",
            root / f"{day}.cov.json",
        )
        expect = _digest_bytes(
            {
                "day": day,
                "producer": producer,
                "contract": contract_sha,
                "supplements": sup_digest,
                "models": model_shas,
                "states": states_sha,
                "schema": REPLAY_SCHEMA,
            }
        )
        fresh, prior = False, None
        if resume and cpath.exists() and dpath.exists() and tpath.exists():
            try:
                prior = json.loads(cpath.read_text())
                fresh = prior.get("resume_hash") == expect and prior.get("schema") == REPLAY_SCHEMA
            except (json.JSONDecodeError, OSError):
                fresh = False
        if fresh:
            infos.append(
                {"day": day, "resumed": True, "coverage": (prior or {}).get("coverage", {})}
            )
        else:
            todo.append((day, dpath, tpath, cpath, expect, states_sha))
    for n, (day, dpath, tpath, cpath, expect, states_sha) in enumerate(todo, 1):
        states = read_day_states(out_dir, day)
        spath, scov = state_paths(out_dir, day)
        if not spath.exists():
            raise SystemExit(f"[touch-payoff] scored states missing for {day}")
        if states_sha is None:
            states_sha = sha256_file(spath)
        tickers = sorted(set(states["ticker"].to_list())) if states.height else []
        quote_frame = quote_day_frame(DATA_ROOT, day, set(tickers), supp) if tickers else None
        streams = symbol_quotes(quote_frame, day)
        del quote_frame
        daily_rows, trade_rows = [], []
        day_cov = {"states": int(states.height), "quote_streams": len(streams), "views": {}}
        for view in VIEWS:
            daily, trades = replay_day(day, states, streams, view)
            daily_rows.append(daily)
            trade_rows.extend(trades)
            day_cov["views"][view.key] = {
                "signals": daily["signals"],
                "attempts": daily["attempts"],
                "fills": daily["fills"],
                "known_usd_25": daily["known_usd_25"],
            }
        daily_frame = pl.DataFrame(daily_rows, schema={c: DAILY_TYPES[c] for c in DAILY_COLUMNS})
        trade_frame = (
            pl.DataFrame(trade_rows, schema={c: TRADE_TYPES[c] for c in TRADE_COLUMNS})
            if trade_rows
            else empty_trades()
        )
        tmp_d = dpath.with_name(f"{dpath.name}.tmp{os.getpid()}")
        daily_frame.write_parquet(tmp_d)
        os.replace(tmp_d, dpath)
        tmp_t = tpath.with_name(f"{tpath.name}.tmp{os.getpid()}")
        trade_frame.write_parquet(tmp_t)
        os.replace(tmp_t, tpath)
        write_json_atomic(
            cpath,
            {
                "day": day,
                "resume_hash": expect,
                "producer_sha256": producer,
                "contract_sha256": contract_sha,
                "supplement_digest": sup_digest,
                "states_sha256": states_sha,
                "model_shas": model_shas,
                "schema": REPLAY_SCHEMA,
                "coverage": day_cov,
            },
        )
        infos.append({"day": day, "resumed": False, "coverage": day_cov})
        if n % 50 == 0 or n == len(todo):
            funded = sum(v["attempts"] for v in day_cov["views"].values())
            print(
                f"[replay:{block}] {n}/{len(todo)} days, last {day}, funded={funded} "
                f"over {len(VIEWS)} views",
                flush=True,
            )
    for info in infos:
        cov = info["coverage"] or {}
        coverage["signals"] += sum(v.get("signals", 0) for v in (cov.get("views") or {}).values())
        coverage["intents_funded"] += sum(
            v.get("attempts", 0) for v in (cov.get("views") or {}).values()
        )
        coverage["fills"] += sum(v.get("fills", 0) for v in (cov.get("views") or {}).values())
        coverage["unknown_fills"] += sum(
            v.get("unknown_fills", 0) for v in (cov.get("views") or {}).values()
        )
        coverage["no_match_fills"] += sum(
            v.get("no_match_fills", 0) for v in (cov.get("views") or {}).values()
        )
    coverage.update({"days_cached": len(infos), "days_replayed": len(todo)})
    return coverage


# ----- block aggregation ------------------------------------------------------
def read_block_frames(out_dir: Path, block: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    """All per-day parts of one block: the daily rows and the funded-intent trades."""
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


def period_view(rows: pl.DataFrame, key: str, rungs=(0.0, 25.0, 150.0)) -> dict:
    """Per-month or per-year accounting with the known contribution at key rungs."""
    rung_keys = [int(r) for r in rungs]
    out = {}
    names = sorted({str(d)[: len(key)] for d in rows["day"].to_list()})
    for name in names:
        part = rows.filter(pl.col("day").str.starts_with(name))
        cell = {
            "days_replayed": int(part.height),
            "traded_days": int((part["attempts"] > 0).sum()),
            "attempts": int(part["attempts"].sum()),
            "fills": int(part["fills"].sum()),
            "known_fills": int(part["known_fills"].sum()),
            "unknown_fills": int(part["unknown_fills"].sum()),
            "no_match_fills": int(part["no_match_fills"].sum()),
        }
        for r in rung_keys:
            cell[f"known_usd_{r}"] = float(part[f"known_usd_{r}"].sum())
            cell[f"mean_daily_known_usd_{r}"] = float(part[f"known_usd_{r}"].mean())
        out[name] = cell
    return out


def view_cell(daily: pl.DataFrame, trades: pl.DataFrame, view: View, days: list[str]) -> dict:
    """Every reported number for one (head, clearance) view on one block's calendar."""
    rows = daily.filter(pl.col("view_key") == view.key)
    n_days = len(days)
    if int(rows.height) != n_days:
        raise ValueError(
            f"view {view.key}: {rows.height} daily rows on disk for {n_days} calendar "
            "days; replay the whole block before aggregating"
        )
    rung_keys = [int(r) for r in RUNG_COSTS]
    known_total = {r: float(rows[f"known_usd_{r}"].sum()) for r in rung_keys}
    known_per_day = {r: known_total[r] / n_days for r in rung_keys}
    bound_per_day = {r: float(rows[f"lower_bound_usd_{r}"].sum()) / n_days for r in rung_keys}
    bootstrap = {
        f"{r}bps": bootstrap_daily(rows[f"known_usd_{r}"].to_list(), n=BOOT_N, seed=BOOT_SEED)
        for r in rung_keys
    }
    series_book = {
        f"{r}bps": bootstrap_daily(
            (rows[f"known_usd_{r}"] / BOOK).to_list(), n=BOOT_N, seed=BOOT_SEED
        )
        for r in rung_keys
    }
    cell = {
        "view_key": view.key,
        "label": view.label,
        "head": view.head,
        "clearance": view.clearance,
        "days_replayed": n_days,
        "states_total": int(rows["states_total"].sum()),
        "signals": int(rows["signals"].sum()),
        "intents_funded": int(rows["intents_funded"].sum()),
        "attempts": int(rows["attempts"].sum()),
        "fills": int(rows["fills"].sum()),
        "known_fills": int(rows["known_fills"].sum()),
        "unknown_fills": int(rows["unknown_fills"].sum()),
        "no_match_fills": int(rows["no_match_fills"].sum()),
        "traded_days": int((rows["attempts"] > 0).sum()),
        "positions_peak": int(rows["positions_peak"].max() or 0),
        "positions_mean": float(rows["positions_mean"].mean()),
        "known_usd": {str(r): known_total[r] for r in rung_keys},
        "known_usd_per_calendar_day": {str(r): known_per_day[r] for r in rung_keys},
        "extra_cost_usd": {str(r): known_total[0] - known_total[r] for r in rung_keys},
        "extra_cost_note": (
            "known_usd_0 - known_usd_rung: the extra provider fee charged on top of the "
            "observed touch, which already lives inside the realized entry/exit prices"
        ),
        "full_loss_lower_bound_usd_per_calendar_day": {str(r): bound_per_day[r] for r in rung_keys},
        "dollars_per_year_252_partial_on_book_750": {
            str(r): known_per_day[r] * ANNUAL_SESSIONS for r in rung_keys
        },
        "dollars_per_year_252_whole_basis_on_book_750": {
            str(r): bound_per_day[r] * ANNUAL_SESSIONS for r in rung_keys
        },
        "annualization": (
            f"mean daily known contribution x {ANNUAL_SESSIONS} sessions on a "
            f"${int(BOOK)} nominal book; a simple session-count convention, NOT a CAGR "
            "and NOT an account claim"
        ),
        "fixed_opex_usd_per_year": FIXED_OPEX_USD_PER_YEAR,
        "partial_after_fixed_opex_usd_per_year_25bps": (
            known_per_day[25] * ANNUAL_SESSIONS - FIXED_OPEX_USD_PER_YEAR
        ),
        "fixed_opex_note": (
            "official SIP subscription reported as a SEPARATE line, never netted into "
            "the edge; eligibility and house margin are unverified"
        ),
        "bootstrap_daily_known_usd": bootstrap,
        "bootstrap_daily_return_on_book": series_book,
        "skip_reasons": {reason: int(rows[reason].sum()) for reason in SKIP_REASONS},
        "monthly": period_view(rows, "month", rungs=(0.0, 25.0, 150.0)),
        "yearly": period_view(rows, "year", rungs=(0.0, 25.0, 150.0)),
    }
    trades_view = trades.filter(pl.col("view_key") == view.key)
    if trades_view.height:
        filled = trades_view.filter(pl.col("fill_status") == "conditional_fill")
        nets = {
            str(r): (filled[f"net_usd_{r}"].to_list() if filled.height else []) for r in rung_keys
        }
        entry_asks = filled["entry_ask"].to_list() if filled.height else []
        exit_bids = filled["exit_bid"].to_list() if filled.height else []
        cell["trade_stats"] = {
            "funded_intents": int(trades_view.height),
            "conditional_fills": int(filled.height),
            "mean_net_usd_per_conditional_fill": {
                str(r): (float(np.mean(v)) if len(v) else None) for r, v in nets.items()
            },
            "worst_known_fill_usd": {
                str(r): (float(np.min(v)) if len(v) else None) for r, v in nets.items()
            },
            "win_rate_known_fill": {
                str(r): (float(np.mean(np.asarray(v) > 0)) if len(v) else None)
                for r, v in nets.items()
            },
            "mean_entry_ask": (float(np.mean(entry_asks)) if entry_asks else None),
            "mean_exit_bid": (float(np.mean(exit_bids)) if exit_bids else None),
            "mean_touch_gross_before_extra": (
                float(filled["y_touch"].mean()) if filled.height else None
            ),
            "mean_predicted_gross": (float(filled["pred"].mean()) if filled.height else None),
            "overprediction_pp": (
                float(100.0 * (filled["pred"].mean() - filled["y_touch"].mean()))
                if filled.height
                else None
            ),
            "label_replay_agreement": (
                int(((filled["y_touch"] - filled["label_y"]).abs() < 1e-9).sum())
                if filled.height
                else 0
            ),
            "label_replay_agreement_note": (
                "conditional fills whose replayed per-share touch return equals the "
                "canonical label y_touch to 1e-9 (same clocks, same causal helpers)"
            ),
            "fill_status_counts": dict(
                trades_view.group_by("fill_status").len().sort("len", descending=True).iter_rows()
            ),
            "exit_submit_status_counts": dict(
                trades_view.group_by("exit_submit_status")
                .len()
                .sort("len", descending=True)
                .iter_rows()
            ),
            "mean_hold_minutes": (
                float((filled["actual_exit_us"] - filled["intent_us"]).mean() / 60_000_000.0)
                if filled.height
                else None
            ),
        }
    else:
        cell["trade_stats"] = {
            "funded_intents": 0,
            "conditional_fills": 0,
            "mean_net_usd_per_conditional_fill": {str(r): None for r in rung_keys},
            "worst_known_fill_usd": {str(r): None for r in rung_keys},
            "win_rate_known_fill": {str(r): None for r in rung_keys},
            "mean_entry_ask": None,
            "mean_exit_bid": None,
            "mean_touch_gross_before_extra": None,
            "mean_predicted_gross": None,
            "overprediction_pp": None,
            "label_replay_agreement": 0,
            "fill_status_counts": {},
            "exit_submit_status_counts": {},
            "mean_hold_minutes": None,
        }
    return cell


def aggregate_block(out_dir: Path, block: str, days: list[str]) -> tuple[dict, dict]:
    """The full eight-view surface of one block, plus the block's data coverage."""
    daily, trades = read_block_frames(out_dir, block)
    surface = {view.key: view_cell(daily, trades, view, days) for view in VIEWS}
    coverage = {
        "block": block,
        "days": len(days),
        "states_total": int(daily["states_total"].sum()),
        "signals": int(daily["signals"].sum()),
        "intents_funded": int(daily["intents_funded"].sum()),
        "attempts": int(daily["attempts"].sum()),
        "fills": int(daily["fills"].sum()),
        "unknown_fills": int(daily["unknown_fills"].sum()),
        "no_match_fills": int(daily["no_match_fills"].sum()),
        "unknown_share_of_funded_intents": (
            float(daily["unknown_fills"].sum() / daily["intents_funded"].sum())
            if int(daily["intents_funded"].sum())
            else None
        ),
        "causal_skips": {reason: int(daily[reason].sum()) for reason in SKIP_REASONS},
    }
    return surface, coverage


def choose_view(surface: dict) -> tuple[View, list[dict]]:
    """Deterministic argmax of the frozen validation objective; no floors, no gates."""
    ranked = sorted(
        VIEWS,
        key=lambda v: (
            -surface[v.key]["known_usd_per_calendar_day"]["25"],
            -surface[v.key]["known_fills"],
            v.key,
        ),
    )
    ranking = [
        {
            "view_key": v.key,
            "label": v.label,
            "head": v.head,
            "clearance": v.clearance,
            "validation_known_usd_per_calendar_day_25": surface[v.key][
                "known_usd_per_calendar_day"
            ]["25"],
            "known_fills": surface[v.key]["known_fills"],
            "unknown_fills": surface[v.key]["unknown_fills"],
            "traded_days": surface[v.key]["traded_days"],
        }
        for v in ranked
    ]
    return ranked[0], ranking


def decision_text(chosen: View, val: dict, late: dict | None, frozen: bool) -> str:
    objective = val["known_usd_per_calendar_day"]["25"]
    fills = val["known_fills"]
    boot = val["bootstrap_daily_known_usd"]["25bps"]
    if fills == 0 or objective is None:
        return (
            "NO_EDGE_EVIDENCE: the chosen validation view has no known fill, so an empty "
            "signal set is cash, not a positive edge. No actual-touch positive formulation "
            "was measured; the retained rare h60 lead stays the reference and nothing here "
            "is promoted."
        )
    if objective <= 0:
        return (
            f"NO_ACTUAL_TOUCH_POSITIVE_FORMULATION: the best of the eight views "
            f"({chosen.label}) measures {objective:+.2f} $/calendar day of KNOWN "
            f"contribution on 2023 validation at 25bps extra over {fills} known fills "
            f"(day bootstrap p>0 = {boot.get('p_gt_zero')}). All eight views are measured "
            "and none is positive, so the retained rare h60 lead stays the reference; the "
            "unknown share is reported beside this number and is NOT assumed zero."
        )
    text = (
        f"{STATUS}: chosen {chosen.label} (h{chosen.head}, net clearance "
        f"{chosen.clearance:.4f} above the 25bps fee residual) by 2023 validation "
        f"actual-touch known contribution: {objective:+.2f} $/calendar day at 25bps extra "
        f"= {val['dollars_per_year_252_partial_on_book_750']['25']:+.0f} $/year on the "
        f"${int(BOOK)} nominal book (simple 252-session convention, not a CAGR), "
        f"{fills} known fills on {val['traded_days']} traded days of {val['days_replayed']} "
        f"calendared days, {val['unknown_fills']} UNKNOWN executions "
        f"({100.0 * val['unknown_fills'] / max(1, val['attempts']):.1f}% of attempts) and "
        f"{val['no_match_fills']} no-match unfilled intents. Day bootstrap p>0 = "
        f"{boot.get('p_gt_zero')}. This is the measured KNOWN-contribution expectation on "
        "a PARTIAL basis (unknowns excluded from the numerator); the whole (full-loss) "
        f"basis is {val['dollars_per_year_252_whole_basis_on_book_750']['25']:+.0f} $/year "
        f"and fixed SIP opex of ${int(FIXED_OPEX_USD_PER_YEAR)}/year is reported "
        "separately."
    )
    if frozen and late is not None:
        late_obj = late["known_usd_per_calendar_day"]["25"]
        text += (
            f" The frozen late block measures {late_obj:+.2f} $/calendar day at 25bps "
            f"extra over {late['known_fills']} known fills of {late['attempts']} attempts "
            "on the previously explored 2025-02..2026-05 window; the choice was frozen "
            "before any late file was read, so this is confirmation of an already-frozen "
            "decision, not a re-selection."
        )
    elif not frozen:
        text += " Late block not run (--skip-late)."
    return text


def provenance_block(
    out_dir: Path,
    days_train: list[str],
    days_val: list[str],
    days_late: list[str] | None,
    supplemental_roots: tuple[Path, ...],
    val_cov: dict,
    late_cov: dict | None,
) -> dict:
    sup_digest, sup_entries = supplement_digest(supplemental_roots)
    return {
        "producer_sha256": producer_sha256(),
        "producer_script": str(Path(__file__).resolve()),
        "contract_sha256": _digest_bytes(CONTRACT),
        "panel_root": str(PANEL_ROOT),
        "panel_days_root": str(PANEL_DAYS),
        "panel_contract_sha256": (sha256_file(PANEL_CONTRACT) if PANEL_CONTRACT.exists() else None),
        "quote_cache_root": str(QUOTE_CACHE_ROOT),
        "quote_cache_days_present": sum(
            1
            for d in days_train + days_val + list(days_late or [])
            if (QUOTE_CACHE_ROOT / f"{d}.parquet").exists()
        ),
        "supplemental_roots": [str(r) for r in supplemental_roots],
        "supplemental_roots_existing": [str(r) for r in supplemental_roots if r.exists()],
        "supplemental_files": sup_entries,
        "supplement_digest": sup_digest,
        "engine_helper_shas": engine_helper_shas(),
        "engine_helper_note": ENGINE_SHA_NOTE,
        "model_dir": str(out_dir / "models"),
        "models": {
            f"h{h}": {
                "path": str(fitted_model_path(out_dir, h)),
                "sha256": sha256_file(fitted_model_path(out_dir, h)),
            }
            for h in HEADS
        },
        "train_days": len(days_train),
        "validation_days": len(days_val),
        "late_days": (
            len(days_late)
            if days_late is not None
            else "not_yet_inspected: the freeze precedes any late file access"
        ),
        "coverage_validation": val_cov,
        "coverage_late": late_cov,
        "output_root": str(out_dir),
    }


# ----- run --------------------------------------------------------------------
def _validate_blocks(days_train: list[str], days_val: list[str], days_late: list[str]) -> None:
    if len(days_val) != EXPECTED_DAYS[VAL_BLOCK]:
        raise SystemExit(
            f"[touch-payoff] requires {EXPECTED_DAYS[VAL_BLOCK]} validation days, "
            f"got {len(days_val)}"
        )
    if len(days_late) != EXPECTED_DAYS[LATE_BLOCK]:
        raise SystemExit(
            f"[touch-payoff] requires {EXPECTED_DAYS[LATE_BLOCK]} confirmation days, "
            f"got {len(days_late)}"
        )
    if len(days_train) < MIN_TRAIN_DAYS:
        raise SystemExit(
            f"[touch-payoff] requires >= {MIN_TRAIN_DAYS} train days "
            f"({TRAIN_START}..{TRAIN_END}), got {len(days_train)}"
        )


def run(args: argparse.Namespace) -> None:
    t0 = time.time()
    out = args.out or OUTPUT
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")

    supplemental = list(DEFAULT_SUPPLEMENTS)
    for extra in args.supplement or []:
        if extra not in supplemental:
            supplemental.append(extra)
    supplemental_roots = tuple(supplemental)
    existing = [r for r in supplemental_roots if r.exists()]
    print(
        "[supplements] " + (", ".join(str(r) for r in existing) if existing else "none present"),
        flush=True,
    )

    days_train = block_days(args.days, TRAIN_BLOCK)
    days_val = block_days(args.days, VAL_BLOCK)
    days_late = block_days(args.days, LATE_BLOCK)
    if args.days is None:
        _validate_blocks(days_train, days_val, days_late)
    if not days_val and not days_train:
        raise SystemExit("[touch-payoff] no train or validation days selected")

    # 0) the preregistered contract is written BEFORE any label, fit or validation work.
    (out / "contract.json").write_text(json.dumps(CONTRACT, indent=1, default=_json_default) + "\n")
    print(
        f"[freeze-grid] contract -> {out / 'contract.json'} "
        f"({len(VIEWS)} views x {len(RUNG_COSTS)} rungs, before any outcome)",
        flush=True,
    )

    stages = set(args.stages.split(",")) if args.stages else {"all"}
    if "all" in stages:
        stages = {"labels", "fit", "eval"}

    freeze_path = out / "selection_freeze.json"
    resumed_freeze = None
    if args.resume and freeze_path.exists():
        try:
            resumed_freeze = json.loads(freeze_path.read_text())
        except (json.JSONDecodeError, OSError):
            resumed_freeze = None
        if resumed_freeze is not None:
            print(
                "[resume] selection_freeze.json found: the validation surface and the "
                "frozen choice are reused as-is and no validation file is re-read",
                flush=True,
            )

    # 1) pre-freeze labels (train + validation). No confirmation panel or quote file is
    #    touched before the freeze.
    if "labels" in stages:
        pre_days = days_train + days_val
        if not pre_days:
            raise SystemExit("[touch-payoff] no train or validation days selected for labels")
        cov = label_block(out, pre_days, args.resume, supplemental_roots)
        known = cov.get("known_counts", {})
        print(
            f"[labels] days={cov['days_cached']} (scored {cov['days_scored']}) "
            f"qualified_rows={cov['qualified_rows']} entry_status="
            + json.dumps(cov.get("entry_status_counts", {}), default=_json_default)
            + " known="
            + json.dumps(known, default=_json_default),
            flush=True,
        )

    # 2) fit the two fixed heads on the train block's known, observable labels.
    models: dict = {}
    if "fit" in stages:
        fit_stage(out, days_train)
        models = load_fitted_models(out)  # reload + verify the frozen feature order

    if "eval" not in stages:
        print("[done] labels/fit stages only (eval not requested)", flush=True)
        return
    if not models:
        models = load_fitted_models(out)

    if resumed_freeze is not None:
        chosen = VIEWS_BY_KEY[resumed_freeze["chosen"]["view_key"]]
        val_surface = {
            k: (resumed_freeze.get("validation_surface") or {}).get(k)
            for k in resumed_freeze.get("validation_surface", {})
        }
        val_cov = (resumed_freeze.get("provenance") or {}).get("coverage_validation", {})
        ranking = (resumed_freeze.get("selection") or {}).get("ranking", [])
        print(f"[resume] frozen choice {chosen.label} reused", flush=True)
    else:
        # 3) validation only: score, replay, aggregate, then freeze BEFORE any late read.
        states_block(out, models, days_val, args.resume, supplemental_roots)
        replay_block(out, VAL_BLOCK, days_val, models, args.resume, supplemental_roots)
        val_surface, val_cov = aggregate_block(out, VAL_BLOCK, days_val)
        for view in VIEWS:
            cell = val_surface[view.key]
            boot = cell["bootstrap_daily_known_usd"]["25bps"]
            print(
                f"[val@25] {view.label:<14} "
                f"{cell['known_usd_per_calendar_day']['25']:+8.2f} $/day "
                f"known fills={cell['known_fills']} unknown={cell['unknown_fills']} "
                f"nomatch={cell['no_match_fills']} attempts={cell['attempts']} "
                f"signals={cell['signals']} traded_days={cell['traded_days']} "
                f"p>0={boot.get('p_gt_zero')}",
                flush=True,
            )
        chosen, ranking = choose_view(val_surface)
        late_preexisting = (
            sorted((out / "replay" / LATE_BLOCK).glob("*.parquet"))
            if (out / "replay" / LATE_BLOCK).exists()
            else []
        )
        freeze = {
            "frozen_before_late_file_access": True,
            "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "study": "alpha_touch_hourly_payoff",
            "status": STATUS,
            "chosen": {
                "view_key": chosen.key,
                "label": chosen.label,
                "head": chosen.head,
                "clearance": chosen.clearance,
            },
            "selection": {
                "objective": (
                    "2023 validation actual-touch known contribution USD per full "
                    "calendar day at the 25bps extra rung"
                ),
                "selection_cost_bps": SELECT_COST,
                "cost_ladder_bps": list(RUNG_COSTS),
                "basis": (
                    "PARTIAL: unknown executions are excluded from the numerator and "
                    "reported beside it; the whole (full-loss) basis is reported side by "
                    "side and never the objective"
                ),
                "power_floors": None,
                "median_or_tail_gates": None,
                "cost_or_count_kill": None,
                "synthetic_100bps_veto": False,
                "tie_break": "(- objective, - known_fills, view_key)",
                "ranking": ranking,
            },
            "views": [
                {
                    "view_key": v.key,
                    "label": v.label,
                    "head": v.head,
                    "clearance": v.clearance,
                }
                for v in VIEWS
            ],
            "validation_surface": val_surface,
            "provenance": provenance_block(
                out, days_train, days_val, None, supplemental_roots, val_cov, None
            ),
            "late_files_preexisting_on_disk": [str(p) for p in late_preexisting],
            "late_files_preexisting_note": (
                "on a --resume run some confirmation artifacts may already exist from an "
                "earlier invocation; the choice above was still fixed by validation only"
            ),
        }
        write_json_atomic(freeze_path, freeze)
        print(
            f"[freeze] selection_freeze.json -> chosen {chosen.label} "
            f"({val_surface[chosen.key]['known_usd_per_calendar_day']['25']:+.2f} $/day "
            "@25) AFTER validation, BEFORE any confirmation file is read",
            flush=True,
        )

    if args.skip_late or not days_late:
        if args.skip_late:
            print("[blocks] confirmation skipped (--skip-late)", flush=True)
        else:
            print("[blocks] no confirmation days selected (smoke subset)", flush=True)
        results = {
            "study": "alpha_touch_hourly_payoff",
            "status": STATUS,
            "decision": decision_text(chosen, val_surface[chosen.key], None, frozen=False),
            "chosen": {
                "view_key": chosen.key,
                "label": chosen.label,
                "head": chosen.head,
                "clearance": chosen.clearance,
            },
            "frozen_choice_immutable": True,
            "contract": CONTRACT,
            "validation": val_surface,
            "validation_ranking": ranking,
            "late": None,
            "coverage": {"validation": val_cov, "late": None},
            "artifacts": {
                "contract": str(out / "contract.json"),
                "selection_freeze": str(freeze_path),
                "producer_snapshot": str(out / "producer_snapshot.py"),
                "labels_root": str(LABELS_ROOT),
                "fit_report": str(out / "fit_report.json"),
                "models": {f"h{h}": str(fitted_model_path(out, h)) for h in HEADS},
                "states_root": str(STATES_ROOT),
                "replay_root": str(out / "replay"),
            },
            "runtime_s": round(time.time() - t0, 1),
        }
        write_json_atomic(out / "results.json", results)
        print(f"[decision] {results['decision']}", flush=True)
        print(f"[done] {results['runtime_s']}s -> {out / 'results.json'}", flush=True)
        return

    # 4) confirmation: labels, states and replay for transparency, choice immutable.
    late_label_cov = label_block(out, days_late, args.resume, supplemental_roots)
    late_state_cov = states_block(out, models, days_late, args.resume, supplemental_roots)
    late_replay_cov = replay_block(
        out, LATE_BLOCK, days_late, models, args.resume, supplemental_roots
    )
    late_surface, late_cov = aggregate_block(out, LATE_BLOCK, days_late)
    for view in VIEWS:
        cell = late_surface[view.key]
        print(
            f"[late@25] {view.label:<14} "
            f"{cell['known_usd_per_calendar_day']['25']:+8.2f} $/day "
            f"known fills={cell['known_fills']} unknown={cell['unknown_fills']} "
            f"nomatch={cell['no_match_fills']} attempts={cell['attempts']} "
            f"signals={cell['signals']} traded_days={cell['traded_days']}",
            flush=True,
        )

    results = {
        "study": "alpha_touch_hourly_payoff",
        "status": STATUS,
        "decision": decision_text(
            chosen, val_surface[chosen.key], late_surface[chosen.key], frozen=True
        ),
        "chosen": {
            "view_key": chosen.key,
            "label": chosen.label,
            "head": chosen.head,
            "clearance": chosen.clearance,
        },
        "frozen_choice_immutable": True,
        "contract": CONTRACT,
        "validation": val_surface,
        "validation_ranking": ranking,
        "late": late_surface,
        "late_ranking": [
            {
                "view_key": v.key,
                "label": v.label,
                "late_known_usd_per_calendar_day_25": late_surface[v.key][
                    "known_usd_per_calendar_day"
                ]["25"],
                "known_fills": late_surface[v.key]["known_fills"],
                "unknown_fills": late_surface[v.key]["unknown_fills"],
            }
            for v in sorted(
                VIEWS,
                key=lambda v: (
                    -late_surface[v.key]["known_usd_per_calendar_day"]["25"],
                    v.key,
                ),
            )
        ],
        "chosen_late_block": late_surface[chosen.key],
        "cost_ladder_whole_calendar_known_contributions": {
            view.key: {
                str(int(r)): {
                    "validation_usd": val_surface[view.key]["known_usd"][str(int(r))],
                    "late_usd": late_surface[view.key]["known_usd"][str(int(r))],
                    "validation_usd_per_calendar_day": val_surface[view.key][
                        "known_usd_per_calendar_day"
                    ][str(int(r))],
                    "late_usd_per_calendar_day": late_surface[view.key][
                        "known_usd_per_calendar_day"
                    ][str(int(r))],
                    "validation_extra_cost_usd": val_surface[view.key]["extra_cost_usd"][
                        str(int(r))
                    ],
                    "late_extra_cost_usd": late_surface[view.key]["extra_cost_usd"][str(int(r))],
                    "validation_full_loss_lower_bound_usd_per_day": val_surface[view.key][
                        "full_loss_lower_bound_usd_per_calendar_day"
                    ][str(int(r))],
                    "late_full_loss_lower_bound_usd_per_day": late_surface[view.key][
                        "full_loss_lower_bound_usd_per_calendar_day"
                    ][str(int(r))],
                }
                for r in RUNG_COSTS
            }
            for view in VIEWS
        },
        "coverage": {
            "validation": val_cov,
            "late": late_cov,
            "labels_validation": None,
            "labels_late": late_label_cov,
            "states_late": late_state_cov,
            "replay_late": late_replay_cov,
        },
        "provenance": provenance_block(
            out, days_train, days_val, days_late, supplemental_roots, val_cov, late_cov
        ),
        "artifacts": {
            "contract": str(out / "contract.json"),
            "selection_freeze": str(freeze_path),
            "producer_snapshot": str(out / "producer_snapshot.py"),
            "labels_root": str(LABELS_ROOT),
            "fit_report": str(out / "fit_report.json"),
            "models": {f"h{h}": str(fitted_model_path(out, h)) for h in HEADS},
            "states_root": str(STATES_ROOT),
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
        "--days",
        nargs="+",
        default=None,
        help="restrict the analysis calendar to these ET days (smoke/debug only)",
    )
    p.add_argument(
        "--resume", action="store_true", help="resume from the per-day artifacts on disk"
    )
    p.add_argument(
        "--skip-late",
        action="store_true",
        help="stop after the frozen selection (validation block only)",
    )
    p.add_argument(
        "--supplement",
        type=Path,
        action="append",
        default=None,
        help="extra quote supplement cache root (<root>/<day>.parquet); originals still win",
    )
    p.add_argument(
        "--stages",
        type=str,
        default="all",
        help="comma subset of {labels,fit,eval} (default: all)",
    )
    run(p.parse_args())


if __name__ == "__main__":
    main()
