#!/usr/bin/env python3
"""Delayed free-feed (IEX) execution study of the immutable h60 SIP-scored model.

The retained h60 payoff heads were scored on ORIGINAL state-t panel features with the
full consolidated (SIP) tape available at the normal t+1 intent - i.e. as if the desk
paid for real-time SIP. This producer prices the OTHER side of that data-cost trade:
the SAME immutable model, the SAME panel states and the SAME forecast, received on the
FREE Basic plan instead, where the consolidated tape is 15-20 minutes delayed and only
the non-consolidated IEX tape is observable. It measures what the free plan costs in
execution terms, and it never pretends the delay, the universe or the prices are
unchanged.

METHOD (all predeclared, no refit, no HPO, no outcome-conditioned sampling):

* PREDETERMINED SIGNALS: per allowed panel day, every causal-liquidity-qualified state
  is scored ONCE by the immutable h60 booster (sha256 pinned below) on the unchanged
  FEATURES_ALL matrix. The forecast is the original model forecast on state-t features
  (minute bars strictly before t); those bars are legitimately received by the delayed
  intent clock (bar t-1 arrives at t-1+15 < t+1+15), so the default delayed intent is
  t+1+delay minutes - the normal next-minute intent shifted by the feed delay. The
  delayed PIT snapshot at that clock carries one extra observation minute (the t bar)
  which the unchanged model does not use and no feature is recomputed.
* TWELVE PRE DECLARED VIEWS: 3 fixed score bars (theta = 0.015 / 0.020 / 0.030) x 2
  feed delays (15 / 20 minutes) x 2 exit conventions (the exit stays on the ORIGINAL
  h60 exit minute t+61, or a full 60 minutes from the ACTUAL delayed entry t+1+delay)
  = 12 views, all reported as one cost curve. No view is selected on, filtered by, or
  killed by any late-block number.
* IEX-ONLY OBSERVABILITY: the delayed intent observation (sizing quantity, marketability
  limit price, current L1 liquidity, the spread bar) is the latest RAW IEX print at or
  before the intent clock - the only tape the free plan sees. SIC / the consolidated
  NBBO NEVER substitutes when the IEX stream is missing: a missing IEX stream is an
  execution UNKNOWN, never market-no-quote and never cash, and there is no artificial
  IEX->SIP feature fallback.
* SIP EXECUTION BENCHMARK: at intent + 250ms the BUY is benchmarked from the FULL SIP
  ASK (a conditional, full-top-of-book-touch IOC-limit fill at the actual SIP ask when
  ask <= the IEX-based limit and the SIP L1 depth supports the causal quantity; an IOC
  LIMIT CANNOT rest, so a stale / invalid / absent SIP book at arrival is an UNKNOWN,
  never a later print). The EXIT observes IEX freshness to submit a MARKET order (at the
  due exit intent, else at the first eligible IEX regular print at/after it inside RTH),
  then prices from the SIP BID at the submission + 250ms arrival, or - when the SIP book
  is invalid / unavailable - from the first eligible SIP regular print at/after arrival
  (the actual market REST clock). This is the explicit, labeled assumption that a live
  broker executes on the consolidated tape without the client subscribing to SIP quotes;
  it is NOT a proof of exchange fills.
* DATA COST, HONESTLY SPLIT: the current data-expense assumption is $0 (Alpaca Basic:
  free IEX, non-consolidated, 15-min-delayed SIP history). The $99/month ($1,188/year)
  SIP subscription benchmark is reported SEPARATELY, as a portfolio-level opex line
  shared once per family and never inside per-fill fees. Both are reported next to the
  same income numbers; the delayed-feeds alternative is not claimed to be equivalent to
  the SIP-timed study.
* FEES: every view is reported over the predeclared total-modeled-friction ladder
  0/5/10/25-150 bps charged separately on the ASK (buy) and the BID (sell) leg (the
  touch prices already carry the spread, so a rung is the extra residual only), PLUS the
  sourced actual Alpaca retail/regulatory fee profiles imported from the provider fee
  ledger (zero commission, SEC/TAF/CAT pass-through, daily per-type up-cent posting),
  whose measured round-trip fees are typically well under 1 bp on these tickets.

BOOK: fixed 3 x $1,000 funded $3,000 research sub-book, one position per
ticker, flat 15-minute cooldown after the ACTUAL exit clock, max 3 attempts per ticker
per day, causal integer quantity reserved at the 150-bps rung from the IEX-based limit
(fixed before arrival, never re-floored), all intents of one clock reserved BEFORE any
arrival outcome is observed, hold capped at the RTH close (half days included), UNKNOWN
positions keep their slot and cash until they resolve or the session ends.

STAGES (the parent runs the actual commands; this file never runs itself):
  plan    metadata only: per-day predetermined signal manifests and the IEX request
          windows, written BEFORE any quote file, credential or network access
  acquire read-only Alpaca /v2/stocks/quotes feed=iex asof=<day> with full page
          pagination into this producer's own raw day windows (network; parent only)
  run     per-day atomic score -> replay -> report on 2023 validation FIRST, freeze the
          selection, then the previously explored 2025-02..2026-05 confirmation block

Usage:
  uv run --no-sync python factory/scripts/alpha_delayed_sip_iex.py plan
      # metadata only: manifests + IEX request windows, no network, no credentials
  uv run --no-sync python factory/scripts/alpha_delayed_sip_iex.py plan --days 2023-05-15
      # smoke subset (metadata only)
  uv run --no-sync python factory/scripts/alpha_delayed_sip_iex.py acquire --resume
      # full incremental IEX acquisition, resumable per (day, ticker) and per page
  uv run --no-sync python factory/scripts/alpha_delayed_sip_iex.py acquire --days 2023-05-15
      # smoke subset (metadata + fetch for one day)
  uv run --no-sync python factory/scripts/alpha_delayed_sip_iex.py run --skip-late
      # validation only: score -> replay -> frozen selection
  uv run --no-sync python factory/scripts/alpha_delayed_sip_iex.py run --resume
      # full run: validation, freeze, then the confirmation block
  uv run --no-sync python factory/scripts/alpha_delayed_sip_iex.py run --days 2023-05-15
      # smoke subset (debug only)
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
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

sys.path.insert(0, str(Path(__file__).resolve().parent))

from alpha_open_learned import FEATURES_ALL, PANEL_ROOT, bootstrap_daily, feature_matrix
from alpha_open_panel import allowed
from alpha_open_sim import causal_liquidity
from alpha_provider_fee_ledger import (
    DATA_OPEX,
    PROVIDERS,
    build_day_ledger,
    provider_data_opex,
    trade_fee_record,
)
from alpha_quote_aware_frequency import (
    DATA_ROOT,
    ENTRY_OVERHEAD_BPS,
    MAX_AGE_S,
    SUPPLEMENT_ROOTS,
    arrival_us,
    block_days,
    entry_price_leg,
    entry_rule_status,
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
from alpha_sparse_execution_frontier import (
    ALPACA_QUOTES_URL,
    FETCH_WINDOW_BEFORE_S,
    PAGE_LIMIT,
    _alpaca_headers,
    _get,
    _iso_us,
    _quote_rows,
)
from alpha_sparse_quote_service import supplement_day_path

# ----- fixed configuration (no HPO, no grid search, no refit). -----------------
HEAD = 60  # the ONE immutable head this delayed study replays
THRESHOLDS = (0.015, 0.020, 0.030)  # three fixed score bars, all predeclared
DELAYS_MIN = (15, 20)  # free-plan feed-delay scenarios, minutes
EXIT_REMAINING = "remaining_original_hour"  # exit stays on the original t+61 minute
EXIT_FULL = "full_hour_from_delayed_entry"  # exit = (t+1+delay) + 60 minutes
EXIT_CONVENTIONS = (EXIT_REMAINING, EXIT_FULL)
RUNG_COSTS = (0.0, 5.0, 10.0, 25.0, 50.0, 75.0, 100.0, 125.0, 150.0)  # total extra bps
SELECT_COST = 25.0  # primary selection / reported rung: ONE scenario, never a hurdle
MAX_RUNG_COST = float(max(RUNG_COSTS))  # 150 bps: causal sizing + worst-case reserve
ORDER_BUDGET = 1000.0  # per-position ticket
MAX_SLOTS = 3  # concurrent positions
BOOK = MAX_SLOTS * ORDER_BUDGET  # $3,000 funded research sub-book
COOLDOWN_MIN = 15  # flat minutes after the ACTUAL exit clock
MAX_ATTEMPTS = 3  # attempts per ticker per session
ANNUAL_SESSIONS = 252  # simple session-count convention, NOT a CAGR
BOOT_N = 1000
BOOT_SEED = 20261009
VAL_BLOCK = "validation"
LATE_BLOCK = "confirmation"
EXPECTED_DAYS = {VAL_BLOCK: 250, LATE_BLOCK: 332}
MODEL_PATH = PANEL_ROOT / "learned" / "models" / "payoff_h60.joblib"
MODEL_SHA256 = "c5493c6c043a22e544ca98aa4fb5620292d4a1aeb370f63f6492907334f0bf96"
PANEL_DAYS = PANEL_ROOT / "days"
PANEL_CONTRACT = PANEL_ROOT / "contract.json"
OUTPUT = PANEL_ROOT / "delayed_sip_iex"
# SIP execution-benchmark substrate (read-only, the same merge order the retained
# studies use: the ranked SIP day cache first, supplements may only add timestamps,
# this producer's own acquisition root last).
SIP_BENCHMARK_ROOTS = tuple(SUPPLEMENT_ROOTS) + (
    PANEL_ROOT / "quote_universe_acquire" / "quotes",
)
# This producer's OWN acquired IEX day cache (never overwrites any existing file).
IEX_QUOTES_DIRNAME = "iex_quotes"
IEX_COLS = (  # the raw columns the consumer contract needs (exchange ids are dropped)
    "symbol",
    "ts_utc",
    "bid_price",
    "bid_size",
    "ask_price",
    "ask_size",
    "conditions",
)
IEX_SCHEMA = {
    "symbol": pl.Utf8,
    "ts_utc": pl.Datetime("us", "UTC"),
    "bid_price": pl.Float64,
    "bid_size": pl.Float64,
    "ask_price": pl.Float64,
    "ask_size": pl.Float64,
    "conditions": pl.List(pl.Utf8),
}
# Verbatim raw page schema (exchange ids and tape kept on disk, dropped on merge).
PAGE_SCHEMA = {
    "symbol": pl.Utf8,
    "ts_utc": pl.Datetime("us", "UTC"),
    "bid_price": pl.Float64,
    "bid_size": pl.Float64,
    "ask_price": pl.Float64,
    "ask_size": pl.Float64,
    "bid_exchange": pl.Utf8,
    "ask_exchange": pl.Utf8,
    "conditions": pl.List(pl.Utf8),
    "tape": pl.Utf8,
}
DEFAULT_REQ_SLEEP_S = 0.35  # ~171 requests/minute, below the historical Basic 200/min
FEE_PROFILE_IDS = ("alpaca_current_2026q4", "alpaca_taf_jan2027")
STATUS = "DISCOVERY-NOT-VALIDATED"
VERSION = 1
PLAN_SCHEMA = 1
STATES_SCHEMA = 1
REPLAY_SCHEMA = 1
PROTECTED_UNREAD = ["2024", "2025-01", "2026-06", "2026-07", "2026-08"]
SESSION_CLOSE_MARGIN_MIN = 1  # one minute past the panel's last regular minute


def view_key(threshold: float, delay_min: int, exit_convention: str) -> str:
    return f"thr{int(round(threshold * 10000))}bps_d{int(delay_min)}_{exit_convention}"


_SHORT_EXIT = {EXIT_REMAINING: "orig61", EXIT_FULL: "full60"}


def view_label(threshold: float, delay_min: int, exit_convention: str) -> str:
    return (
        f"thr{int(round(threshold * 10000))}bps_d{int(delay_min)}"
        f"min_{_SHORT_EXIT[exit_convention]}"
    )


@dataclass(frozen=True)
class View:
    """One (threshold, delay, exit convention) cell: same model, same signals."""

    threshold: float
    delay_min: int
    exit_convention: str

    @property
    def key(self) -> str:
        return view_key(self.threshold, self.delay_min, self.exit_convention)

    @property
    def label(self) -> str:
        return view_label(self.threshold, self.delay_min, self.exit_convention)


VIEWS = tuple(
    View(thr, dly, conv) for thr in THRESHOLDS for dly in DELAYS_MIN for conv in EXIT_CONVENTIONS
)
VIEWS_BY_KEY = {v.key: v for v in VIEWS}

CONTRACT = {
    "version": VERSION,
    "study": "alpha_delayed_sip_iex",
    "status": STATUS,
    "hypothesis": (
        "the immutable h60 SIP-scored payoff model, received on the FREE Basic plan "
        "(non-consolidated IEX observability, 15-20 minute consolidated delay) with a "
        "delayed t+1+delay intent, an IEX-sourced marketable IOC-limit entry and an "
        "IEX-fresh-gated market exit benchmarked on the full SIP NBBO, still clears a "
        "residual round-trip cost often enough to matter - priced honestly against the "
        "$99/month SIP alternative"
    ),
    "periods": {
        VAL_BLOCK: "2023-01-01..2023-12-31 (all 250 allowed panel days, selection here)",
        LATE_BLOCK: (
            "2025-02-01..2026-05-31 (all 332 allowed panel days, previously explored, "
            "explored, not pristine)"
        ),
    },
    "protected_unread": PROTECTED_UNREAD,
    "model": {
        "refit": False,
        "hpo": False,
        "feature_changes": False,
        "new_features": False,
        "ticker_or_date_features": False,
        "path": str(MODEL_PATH),
        "sha256": MODEL_SHA256,
        "head": HEAD,
        "feature_order": list(FEATURES_ALL),
        "feature_matrix_helper": "alpha_open_learned.feature_matrix (Float64, null-filled)",
        "fit_block": "2021-02..2022-12 (never replayed, never re-scored as outcomes)",
        "target": "panel minute-open gross payoff label gross_60",
    },
    "views": {
        "grid": "3 thresholds x 2 delays x 2 exit conventions = 12, all predeclared",
        "thresholds": list(THRESHOLDS),
        "delays_min": list(DELAYS_MIN),
        "exit_conventions": {
            EXIT_REMAINING: "exit minute = t + 61 (the original h60 exit minute)",
            EXIT_FULL: "exit minute = (t + 1 + delay) + 60 (a full hour from the delayed entry)",
        },
        "hold_cap": "min(exit minute, panel session_end): RTH close, half days included",
        "views": [
            {
                "view_key": v.key,
                "label": v.label,
                "threshold": v.threshold,
                "delay_min": v.delay_min,
                "exit_convention": v.exit_convention,
            }
            for v in VIEWS
        ],
    },
    "delayed_timing": {
        "default_intent": "minute t + 1 + delay (the normal next-minute intent, delayed)",
        "one_extra_observation_minute": (
            "the delayed PIT snapshot at the intent clock carries one more minute bar "
            "(minute t) than the state-t feature window (bars strictly < t); the "
            "unchanged model does not use it and no feature is recomputed"
        ),
        "forecast_legitimacy": (
            "the forecast is the unchanged model's pred_60 on state-t features; bar t-1 "
            "is delivered at (t-1)+15 <= t+16 = t+1+15, so the forecast is legitimately "
            "received by every delayed intent clock"
        ),
        "arrival_clock": "intent + 250ms (both legs; the exit arrival is submission + 250ms)",
    },
    "iex_acquisition": {
        "endpoint": ALPACA_QUOTES_URL,
        "feed": "iex",
        "asof": "the trade day",
        "limit": PAGE_LIMIT,
        "pagination": "every next_page_token, one symbol per request, cursor-resumable",
        "window": (
            "per (day, ticker): from 30s before that ticker's earliest delayed intent "
            "clock through one minute past its latest exit clock inside RTH"
        ),
        "request_pacing_s": DEFAULT_REQ_SLEEP_S,
        "request_pacing_note": (
            "~171 requests/minute, below the historical Basic 200/min REST limit; the "
            "30-symbol websocket cap does not apply to REST. A bulk multi-symbol "
            "request is NOT used: it is unverified here, so only the primary "
            "one-symbol-per-request path is exercised"
        ),
        "credentials": (
            "ALPACA_API_KEY / ALPACA_SECRET_KEY from the repo root .env, never printed, "
            "stored or logged; no account, no order, no paid purchase"
        ),
        "merge_rule": (
            "acquired raw parts first, then any existing IEX quote cache root (adds "
            "timestamps only); one row per (symbol, ts_utc); the original SIP corpus "
            "and every existing file are read-only and never overwritten"
        ),
        "known_empty_vs_unknown": (
            "a 200 with zero events is a KNOWN acquisition empty (recorded, and the "
            "day simply has no IEX observability -> execution UNKNOWN); any HTTP error "
            "or request exception is a DATA UNKNOWN for that (day, ticker)"
        ),
    },
    "observability": {
        "intent_observation": "latest RAW IEX print at/before the delayed intent clock",
        "intent_uses": "sizing quantity, marketability limit price, L1 liquidity, spread bar",
        "freshness_gate_s": MAX_AGE_S,
        "freshness_note": (
            "age > 2s is an operational freshness gate for THIS policy, never a claim "
            "that an NBBO expired or that a submitted order was cancelled"
        ),
        "no_sic_substitution": (
            "the consolidated NBBO / SIC NEVER replaces a missing IEX observation: a "
            "missing IEX stream is an execution UNKNOWN (not market-no-quote, not cash), "
            "and there is no artificial IEX->SIP feature fallback"
        ),
        "iex_size_units": (
            "IEX quote sizes follow the same displayed-unit epoch as the SIP corpus "
            "(round lots before 2025-11-03, single shares after); both feeds are "
            "treated identically"
        ),
    },
    "entry": {
        "instrument": "marketable IOC LIMIT buy, fixed at the delayed intent clock",
        "limit_price": "ceil_to_cent(IEX intent ASK * 1.01): pre-declared marketability cap",
        "quantity": (
            "q = floor(1000 / (limit_price * (1 + 150bps/2))) at the HIGHEST reported "
            "rung, fixed BEFORE arrival and never re-floored from the arrival price"
        ),
        "depth_rule": "q >= 1 and q <= IEX intent ASK top-of-book depth",
        "spread_rule": (
            f"IEX spread_bps + {int(ENTRY_OVERHEAD_BPS)} <= 10000 * pred_60 "
            "(the core's entry rule, evaluated on the IEX observation)"
        ),
        "arrival_rule": (
            "ONE observation of the latest RAW SIP state at intent + 250ms: ask <= the "
            "IEX-based limit AND SIP ASK depth >= q -> conditional full-touch IOC fill "
            "at the ACTUAL SIP ask; ask > limit -> NO_MATCH_AT_L1 unfilled cash; a "
            "ticker with NO acquired SIP stream, a stale-but-valid, invalid, "
            "non-regular or absent SIP book at arrival -> execution UNKNOWN, because an "
            "IOC LIMIT CANNOT REST (no later print is ever used to fill it) and hidden "
            "liquidity / routed exchange fills are unmeasured"
        ),
        "benchmark_nature": (
            "the SIP ASK fill is a labeled execution BENCHMARK of the consolidated tape "
            "a live broker would trade on; it is not a proof of exchange fills"
        ),
        "no_order_rule": (
            "q == 0 is a KNOWN no-order (no order sent, cash unfilled, never a priced "
            "zero-return fill, never an UNKNOWN)"
        ),
    },
    "exit": {
        "submission": (
            "FRESH-QUOTE-GATED on the IEX feed: submit the MARKET order at the due exit "
            "intent when the latest IEX raw state is firm and fresh, else at the FIRST "
            "eligible IEX regular print at/after the due intent inside RTH"
        ),
        "arrival_clock": "actual IEX submission + 250ms",
        "pricing": (
            "the SIP BID at the arrival clock when the latest SIP raw state is firm and "
            "fresh; a stale-but-valid SIP state -> UNKNOWN (never a later favourable "
            "print); an invalid / non-regular / absent SIP book -> the working MARKET "
            "order conditionally rests to the first eligible SIP regular print at/after "
            "arrival and prices at that print (the actual market REST clock)"
        ),
        "quantity": "the causal entry q, never re-sized",
        "depth_rule": "SIP exit BID top-of-book < q -> UNKNOWN partial capacity",
        "release_rule": (
            "the position, its reserved cash and the ticker cooldown are released at the "
            "clock the exit price actually came from, never at the planned arrival"
        ),
    },
    "book": {
        "slots": MAX_SLOTS,
        "ticket_usd": ORDER_BUDGET,
        "book_usd": BOOK,
        "leverage": False,
        "one_position_per_ticker": True,
        "cooldown": f"flat {COOLDOWN_MIN} minutes after the ACTUAL exit clock",
        "max_attempts_per_ticker_per_day": MAX_ATTEMPTS,
        "cash_reservation": (
            "reserved = q * limit * (1 + 150bps/2) with q sized at the highest rung, so "
            "every reported rung stays inside the $1,000 ticket"
        ),
        "simultaneous_clock_rule": (
            "highest-score eligible intents are funded (up to the slot limit) BEFORE any "
            "arrival outcome is observed; no same-clock substitution after an unfilled "
            "intent"
        ),
        "unknown_rule": (
            "an UNKNOWN position keeps its slot and blocks its ticker until it resolves "
            "or the session ends; cash is never freed at a planned time"
        ),
        "later_clock_rule": "a later signal may use cash released by an earlier ACTUAL exit",
    },
    "costs": {
        "rung_bps": list(RUNG_COSTS),
        "primary_selection_rung_bps": SELECT_COST,
        "selection_rung_is_one_scenario_not_a_hurdle": True,
        "cost_or_count_kill": None,
        "scenario_nature": (
            "every rung is a TOTAL MODELED friction scenario layered on the observed "
            "ASK/BID touch prices, charged separately on each leg, NOT an actual broker "
            "fee; the touch prices already carry the spread, so each rung charges only "
            "the extra residual and the spread is never double-charged"
        ),
        "quantity_sizing_rung_bps": MAX_RUNG_COST,
        "actual_fee_profiles": {
            "source": "alpha_provider_fee_ledger (sourced schedules, as-of 2026-10-09)",
            "profile_ids": list(FEE_PROFILE_IDS),
            "note": (
                "actual Alpaca retail round-trip fees on these tickets are typically well "
                "under 1 bp (zero commission; SEC/TAF/CAT pass-through; per-day per-type "
                "up-cent posting); they are reported beside the modeled rung ladder, "
                "never blended into it"
            ),
        },
        "data_expense": {
            "current_assumption_usd_per_year": 0.0,
            "current_assumption_note": (
                "Alpaca Basic: free IEX-only, non-consolidated; SIP history is 15-min "
                "delayed; this delayed-IEX design is what makes the $0 assumption "
                "self-consistent"
            ),
            "sip_benchmark_usd_per_year": DATA_OPEX["alpaca_algo_trader_plus_usd_per_year"],
            "sip_benchmark_note": (
                "the $99/month Algo Trader Plus SIP subscription is reported as a "
                "SEPARATE portfolio-level opex line, shared once per family and never "
                "charged inside per-fill fees; the delayed alternative is not claimed "
                "to be equivalent to the SIP-timed study"
            ),
        },
    },
    "selection": {
        "objective": (
            "2023 validation KNOWN contribution dollars per full calendar day at the "
            "primary residual rung (ONE predeclared scenario, never a hurdle)"
        ),
        "selection_cost_bps": SELECT_COST,
        "basis": (
            "PARTIAL: unknown executions are excluded from the numerator and reported "
            "beside it; the full-loss lower bound is an optional guard, never the "
            "objective"
        ),
        "power_floors": None,
        "median_or_tail_gates": None,
        "cost_or_count_kill": None,
        "late_cherrypick": None,
        "frozen_before_late_inspection": True,
        "freeze_artifact": "selection_freeze.json (written after the validation surface, "
        "before any late file is read)",
        "all_views_reported": True,
    },
    "prior_data": {
        "validation_block": (
            "2023: explored before this replay; not pristine, not previously unknown"
        ),
        "late_block": (
            "2025-02..2026-05: previously explored BEFORE this replay; NOT pristine and "
            "NOT previously unknown, so every late number is DISCOVERY-NOT-VALIDATED"
        ),
    },
    "no_live_orders": True,
    "sip_benchmark_substrate": {
        "merge_order": ["ranked SIP day cache"] + [str(r) for r in SIP_BENCHMARK_ROOTS],
        "dedup_rule": (
            "unique (symbol, ts_utc) keep=first in that order: the ORIGINAL print always "
            "wins, a supplement may ONLY add timestamps and never silently replaces or "
            "revises an original print"
        ),
        "coverage_note": (
            "the ranked SIP cache covers only part of the qualified universe; a missing "
            "SIP stream is an execution UNKNOWN for the benchmark legs, reported beside "
            "the numbers and never substituted with IEX"
        ),
    },
}


# ----- shared helpers ---------------------------------------------------------
def producer_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


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


def guard_days(days: list[str]) -> None:
    """The protected-day guard runs BEFORE any panel, quote, manifest or API read."""
    bad = [d for d in days if not allowed(d)]
    if bad:
        raise SystemExit(
            f"[delayed-sip-iex] protected/out-of-scope day refused: {bad[:4]} "
            f"({len(bad)} days); nothing was read"
        )


def intent_minute_of(t: int, delay_min: int) -> int:
    """The delayed intent minute: the normal next-minute intent, shifted by the delay."""
    return int(t) + 1 + int(delay_min)


def exit_minute_of(t: int, delay_min: int, exit_convention: str, session_end: int) -> int:
    """The view's exit minute, capped at the panel's last regular minute (half days)."""
    if exit_convention == EXIT_REMAINING:
        minute = int(t) + 61
    elif exit_convention == EXIT_FULL:
        minute = intent_minute_of(t, delay_min) + 60
    else:
        raise ValueError(f"unknown exit convention: {exit_convention}")
    return min(int(minute), int(session_end))


def request_window(
    day: str, lo_minute: int, hi_minute: int, day_session_end: int
) -> tuple[int, int]:
    """One (day, ticker) IEX request window in UTC microseconds.

    From 30 seconds before that ticker's earliest delayed intent clock through one
    minute past its latest exit observation clock inside RTH (the exit submission may
    rest to the first eligible IEX print at/after the due clock, capped at the
    session close). Pure clock arithmetic: no quote file, no network.
    """
    start = minute_us(day, int(lo_minute)) - int(FETCH_WINDOW_BEFORE_S * 1_000_000)
    end = minute_us(day, min(int(hi_minute), int(day_session_end)) + SESSION_CLOSE_MARGIN_MIN)
    return int(start), int(end)


# ----- delayed intent physics (IEX-only observability) ------------------------
def delayed_quantity(limit_price: float, cost_bps: float = MAX_RUNG_COST) -> int:
    """Fee-funded integer quantity from the INTENT limit at THIS study's $1,000 ticket."""
    return int(ORDER_BUDGET // (float(limit_price) * (1.0 + cost_bps / 20_000.0)))


def delayed_intent_facts(iex_sq, intent_us_: int) -> dict:
    """The delayed intent observation and the size it implies (IEX only, never SIP).

    ``intent_status`` uses the core's vocabulary so ``entry_rule_status`` decides the
    rule unchanged: ``quote_not_acquired`` (no IEX stream for the ticker), ``quoted``,
    or the RAW print's own ineligibility verdict (no prior quote / crossed / non-R).
    No age cap, no fallback to an older regular print, no SIC/NBBO substitution.
    """
    base = {
        "intent_ask": None,
        "intent_bid": None,
        "intent_age_s": None,
        "intent_spread_bps": None,
        "intent_ask_shares": None,
        "limit_price": None,
        "qty": 0,
        "reserved_usd": 0.0,
    }
    if iex_sq is None:
        base["intent_status"] = "quote_not_acquired"
        return base
    q, status = iex_sq.latest_raw(intent_us_)
    if q is None:
        base["intent_status"] = status
        return base
    base["intent_status"] = "quoted"
    limit = limit_price_of(q["ask"])
    qty = delayed_quantity(limit)
    base.update(
        {
            "intent_ask": q["ask"],
            "intent_bid": q["bid"],
            "intent_age_s": q["age_s"],
            "intent_spread_bps": q["spread_bps"],
            "intent_ask_shares": q["ask_shares"],
            "limit_price": limit,
            "qty": qty,
            "reserved_usd": reserved_usd(qty, limit, MAX_RUNG_COST),
        }
    )
    return base


def daily_reason_key(intent_reason: str) -> str:
    """Map the core's head-independent skip reason to this study's daily counter key."""
    if intent_reason == "quote_not_acquired":
        return "iex_quote_not_acquired"
    if intent_reason == "intent_not_firm_fresh":
        return "iex_intent_not_firm_fresh"
    if intent_reason == "intent_depth_unsupported":
        return "iex_intent_depth_unsupported"
    if intent_reason == "intent_depth_unknown":
        return "iex_intent_depth_unknown"
    return intent_reason


DAILY_REASON_COLUMNS = (
    "no_order_min_capital",
    "iex_quote_not_acquired",
    "iex_intent_not_firm_fresh",
    "iex_intent_depth_unsupported",
    "iex_intent_depth_unknown",
    "spread_above_prediction",
    "overlap_skips",
    "cooldown_skips",
    "max_attempt_skips",
    "cash_or_slot_skips",
)
DAILY_INTENT_STATUS_COLUMNS = (
    "iex_intent_quoted",
    "iex_intent_no_prior_quote",
    "iex_intent_invalid_or_crossed",
    "iex_intent_nonregular_quote",
    "iex_intent_not_acquired",
)


# ----- immutable stored model -------------------------------------------------
def load_stored_model() -> tuple[dict, dict]:
    """Load the ONE immutable h60 head; verify the pinned sha, order and horizon.

    Returns the head-keyed model map the consumers use (``models[HEAD]["lgbm"]``),
    matching the stored-bundle layout the immutable producer wrote (``lgbm`` booster,
    ``feature_order``, ``horizon``, ``clip_fit``, ``params``, ``seed``).
    """
    path = MODEL_PATH
    if not path.exists():
        raise SystemExit(
            f"[delayed-sip-iex] stored model missing: {path} "
            f"(expected the pinned sha256 {MODEL_SHA256}); nothing is planned or replayed"
        )
    sha = sha256_file(path)
    if sha != MODEL_SHA256:
        raise SystemExit(
            f"[delayed-sip-iex] stored model sha256 {sha} != pinned {MODEL_SHA256}; "
            "the immutable h60 head changed, so nothing is replayed"
        )
    bundle = joblib.load(path)
    order = list(bundle.get("feature_order") or [])
    if order != list(FEATURES_ALL):
        raise SystemExit(
            f"[delayed-sip-iex] stored feature_order != current FEATURES_ALL "
            f"({len(order)} vs {len(FEATURES_ALL)})"
        )
    if int(bundle.get("horizon", -1)) != HEAD:
        raise SystemExit(f"[delayed-sip-iex] stored horizon {bundle.get('horizon')} != {HEAD}")
    booster = bundle["lgbm"]
    names = list(getattr(booster, "feature_name_", []) or [])
    generic = [f"Column_{i}" for i in range(len(order))]
    if names and names != order and names != generic:
        raise SystemExit(
            "[delayed-sip-iex] booster feature names match neither the stored "
            "feature_order nor the generic matrix names"
        )
    n_feat = int(getattr(booster, "n_features_in_", 0) or len(order))
    if n_feat != len(order):
        raise SystemExit(f"[delayed-sip-iex] booster feature count {n_feat} != {len(order)}")
    report = {
        "path": str(path),
        "sha256": sha,
        "horizon": HEAD,
        "feature_order": order,
        "n_features": n_feat,
        "params": bundle.get("params"),
        "clip_fit": bundle.get("clip_fit"),
        "seed": bundle.get("seed"),
        "refit": False,
        "booster_feature_names": (
            "generic matrix names (fitted from a numpy matrix); the order contract is "
            "the bundle's feature_order verified against alpha_open_learned.FEATURES_ALL"
        ),
        "training_immutability": {
            "fitted_on": "2021-02..2022-12 panel days, t%15==0, filled_proxy entries, "
            "known labels",
            "replayed_or_rescored_as_outcomes": False,
            "refit": False,
            "hpo": False,
            "feature_changes": False,
            "ticker_or_date_features": False,
        },
    }
    return {HEAD: bundle}, report


def stored_booster(models: dict):
    """The immutable h60 booster from the head-keyed model map (never a bare KeyError).

    A genuinely missing head or a bundle without its ``lgbm`` booster is a clear,
    module-prefixed SystemExit naming the file and the pinned sha256 it looked for -
    not a KeyError deep inside a consumer.
    """
    if not isinstance(models, dict) or HEAD not in models:
        raise SystemExit(
            f"[delayed-sip-iex] stored model map has no h{HEAD} entry (keys: "
            f"{sorted(models) if isinstance(models, dict) else type(models).__name__}); "
            f"expected {MODEL_PATH} sha256 {MODEL_SHA256}"
        )
    bundle = models[HEAD]
    booster = bundle.get("lgbm") if isinstance(bundle, dict) else None
    if booster is None:
        raise SystemExit(
            f"[delayed-sip-iex] stored h{HEAD} bundle {MODEL_PATH} carries no 'lgbm' "
            f"booster (keys: "
            f"{sorted(bundle) if isinstance(bundle, dict) else type(bundle).__name__}); "
            f"the pinned sha256 is {MODEL_SHA256}"
        )
    return booster


# ----- per-day predetermined signal manifests (metadata only, no network) ------
# The REAL day-panel columns this producer reads (never an assumed schema): the state
# key, the panel's own entry status, and the REAL minute-open exit labels for all three
# stored horizons (gross + exit_et + exit_status, exactly as the label producer wrote
# them). pred_60 is the immutable model's forecast, attached here.
SIGNAL_COLUMNS = (
    "ticker",
    "t",
    "session_end",
    "entry_status",
    "gross_15",
    "exit_et_15",
    "exit_status_15",
    "gross_60",
    "exit_et_60",
    "exit_status_60",
    "gross_390",
    "exit_et_390",
    "exit_status_390",
    "pred_60",
)
SIGNAL_TYPES = {
    "ticker": pl.String,
    "t": pl.Int64,
    "session_end": pl.Int64,
    "entry_status": pl.String,
    "gross_15": pl.Float64,
    "exit_et_15": pl.Int64,
    "exit_status_15": pl.String,
    "gross_60": pl.Float64,
    "exit_et_60": pl.Int64,
    "exit_status_60": pl.String,
    "gross_390": pl.Float64,
    "exit_et_390": pl.Int64,
    "exit_status_390": pl.String,
    "pred_60": pl.Float64,
}


def manifest_path(out_dir: Path, day: str) -> Path:
    return out_dir / "manifest" / f"{day}.json"


def load_manifest(out_dir: Path, day: str) -> dict | None:
    path = manifest_path(out_dir, day)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def day_manifest(day: str, models: dict) -> dict:
    """The day's PREDETERMINED manifest: qualified states, forecasts, request windows.

    Metadata only: the panel, the immutable model and the predeclared clock grid. No
    quote file, no credential, no network access happens here, so no outcome can
    condition the request list. The full causal-liquidity-qualified watchlist is
    requested (never a threshold-filtered subset): the acquire mode is the neutral
    predeclared watchlist.
    """
    guard_days([day])
    path = PANEL_DAYS / f"{day}.parquet"
    if not path.exists():
        raise SystemExit(f"[delayed-sip-iex] panel day missing: {path}")
    frame = pl.read_parquet(path)
    frame = day_context(frame)
    cand = frame.filter(causal_liquidity(frame))
    session_end = int(cand["session_end"][0]) if cand.height else 959
    if not cand.height:
        return {
            "day": day,
            "schema": PLAN_SCHEMA,
            "planned_before_any_quote_or_api_read": True,
            "feed": "iex",
            "asof": day,
            "limit": PAGE_LIMIT,
            "window_before_s": FETCH_WINDOW_BEFORE_S,
            "session_end": session_end,
            "qualified_tickers": [],
            "signals": [],
            "requests": [],
            "records": [],
            "merge": None,
            "note": "no causal-liquidity-qualified state this day",
        }
    tickers = sorted(set(cand["ticker"].to_list()))
    booster = stored_booster(models)
    preds = np.asarray(booster.predict(feature_matrix(cand)), dtype=float)
    rows = cand.select([c for c in SIGNAL_COLUMNS if c != "pred_60"]).to_dicts()
    signals = []
    for i, r in enumerate(rows):
        t = int(r["t"])
        s_end = int(r["session_end"])
        signals.append(
            {
                "ticker": str(r["ticker"]),
                "t": t,
                "session_end": s_end,
                "entry_status": r["entry_status"],
                "gross_15": r["gross_15"],
                "exit_et_15": r["exit_et_15"],
                "exit_status_15": r["exit_status_15"],
                "gross_60": r["gross_60"],
                "exit_et_60": r["exit_et_60"],
                "exit_status_60": r["exit_status_60"],
                "gross_390": r["gross_390"],
                "exit_et_390": r["exit_et_390"],
                "exit_status_390": r["exit_status_390"],
                "pred_60": float(preds[i]),
                "intent_minutes": {str(d): intent_minute_of(t, d) for d in DELAYS_MIN},
                "exit_minutes": {
                    f"d{d}_{conv}": exit_minute_of(t, d, conv, s_end)
                    for d in DELAYS_MIN
                    for conv in EXIT_CONVENTIONS
                },
            }
        )
    per_ticker: dict[str, dict] = {}
    for s in signals:
        slot = per_ticker.setdefault(s["ticker"], {"n": 0, "lo": None, "hi": None})
        slot["n"] += 1
        lo = min(s["intent_minutes"].values())
        hi = max(s["exit_minutes"].values())
        slot["lo"] = lo if slot["lo"] is None else min(slot["lo"], lo)
        slot["hi"] = hi if slot["hi"] is None else max(slot["hi"], hi)
    day_session_end = max(s["session_end"] for s in signals)
    requests = []
    for ticker in sorted(per_ticker):
        slot = per_ticker[ticker]
        start_us, end_us = request_window(
            day, int(slot["lo"]), int(slot["hi"]), day_session_end
        )
        requests.append(
            {
                "ticker": ticker,
                "signals": int(slot["n"]),
                "feed": "iex",
                "asof": day,
                "limit": PAGE_LIMIT,
                "window_before_s": FETCH_WINDOW_BEFORE_S,
                "window_us": [int(start_us), int(end_us)],
                "window_utc": [_iso_us(int(start_us)), _iso_us(int(end_us))],
            }
        )
    return {
        "day": day,
        "schema": PLAN_SCHEMA,
        "planned_before_any_quote_or_api_read": True,
        "feed": "iex",
        "asof": day,
        "limit": PAGE_LIMIT,
        "window_before_s": FETCH_WINDOW_BEFORE_S,
        "session_end": day_session_end,
        "qualified_tickers": tickers,
        "signals": signals,
        "requests": requests,
        "records": [],
        "merge": None,
        "note": (
            "the request list is the full causal-liquidity-qualified watchlist with "
            "every qualified signal's predeclared intent/exit observation clocks; it "
            "was fixed before any quote read, so no outcome can condition it"
        ),
    }


def plan_block(out_dir: Path, models: dict, days: list[str], resume: bool) -> dict:
    """Write (or resume) every day's predetermined manifest atomically, one day each."""
    producer = producer_sha256()
    contract_sha = _digest_bytes(CONTRACT)
    model_sha = sha256_file(MODEL_PATH)
    infos = []
    for day in days:
        fresh_manifest = day_manifest(day, models)
        expect = _digest_bytes(
            {
                "day": day,
                "producer": producer,
                "contract": contract_sha,
                "model": model_sha,
                "feed": "iex",
                "asof": day,
                "limit": PAGE_LIMIT,
                "window_before_s": FETCH_WINDOW_BEFORE_S,
                "schema": PLAN_SCHEMA,
                "signals": _digest_bytes(fresh_manifest["signals"]),
            }
        )
        prior = load_manifest(out_dir, day) if resume else None
        if (
            prior is not None
            and prior.get("resume_hash") == expect
            and prior.get("schema") == PLAN_SCHEMA
        ):
            infos.append(
                {
                    "day": day,
                    "resumed": True,
                    "signals": len(prior.get("signals") or []),
                    "candidates": len(prior.get("requests") or []),
                }
            )
            print(
                f"[plan] {day}: {len(prior.get('requests') or [])} candidate tickers of "
                f"{len(prior.get('qualified_tickers') or [])} qualified (resumed)",
                flush=True,
            )
            continue
        payload = dict(fresh_manifest)
        payload["resume_hash"] = expect
        payload["producer_sha256"] = producer
        payload["contract_sha256"] = contract_sha
        payload["model_sha256"] = model_sha
        write_json_atomic(manifest_path(out_dir, day), payload)
        infos.append(
            {
                "day": day,
                "resumed": False,
                "signals": len(payload["signals"]),
                "candidates": len(payload["requests"]),
            }
        )
        print(
            f"[plan] {day}: {len(payload['requests'])} candidate tickers of "
            f"{len(payload['qualified_tickers'])} qualified, "
            f"{len(payload['signals'])} predeclared signals",
            flush=True,
        )
    return {
        "days_total": len(days),
        "days_planned": len(infos),
        "signals": sum(int(i["signals"]) for i in infos),
        "candidate_day_ticker_pairs": sum(int(i["candidates"]) for i in infos),
    }


# ----- IEX acquisition (read-only network; the parent runs this stage) --------
def raw_dir(out_dir: Path, day: str, ticker: str) -> Path:
    return out_dir / "raw" / day / ticker


def parts_dir(out_dir: Path, day: str, ticker: str) -> Path:
    return out_dir / "parts" / day / ticker


def cursor_path(out_dir: Path, day: str, ticker: str) -> Path:
    return parts_dir(out_dir, day, ticker) / "cursor.json"


def read_cursor(out_dir: Path, day: str, ticker: str) -> dict:
    path = cursor_path(out_dir, day, ticker)
    if not path.exists():
        return {"pages": 0, "next_token": None, "status": "pending", "quote_events": 0}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"pages": 0, "next_token": None, "status": "pending", "quote_events": 0}


def write_cursor(out_dir: Path, day: str, ticker: str, cursor: dict) -> None:
    write_json_atomic(cursor_path(out_dir, day, ticker), cursor)


def load_parts(out_dir: Path, day: str, ticker: str) -> pl.DataFrame:
    """All page parts already on disk for one (day, ticker), in page order."""
    root = parts_dir(out_dir, day, ticker)
    parts = sorted(root.glob("page_*.parquet"))
    if not parts:
        return pl.DataFrame(schema=IEX_SCHEMA)
    return pl.concat([pl.read_parquet(p) for p in parts], how="vertical")


def _write_page_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)


def _write_page_parquet(path: Path, frame: pl.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    frame.write_parquet(tmp)
    os.replace(tmp, path)


def quote_schema_fits(path: Path) -> bool:
    """True when an existing cache day file carries the raw columns the consumer needs."""
    try:
        cols = set(pl.scan_parquet(path).collect_schema().names())
    except Exception:
        return False
    return set(IEX_COLS) <= cols


def existing_iex_symbols(day: str, iex_roots: tuple[Path, ...]) -> set[str]:
    """Tickers already present in any FITTING existing IEX quote cache root."""
    have: set[str] = set()
    for root in iex_roots:
        path = supplement_day_path(root, day)
        if path.exists() and quote_schema_fits(path):
            have |= set(
                pl.scan_parquet(path).select("symbol").unique().collect()["symbol"].to_list()
            )
    return have


def fetch_symbol_day(
    session,
    headers: dict,
    out_dir: Path,
    day: str,
    ticker: str,
    request: dict,
    req_sleep: float,
) -> dict:
    """Full-pagination IEX read of ONE ticker's predeclared window, resumable per page."""
    start_us, end_us = (int(x) for x in request["window_us"])
    record = {
        "day": day,
        "ticker": ticker,
        "endpoint": ALPACA_QUOTES_URL,
        "feed": "iex",
        "asof": day,
        "limit": PAGE_LIMIT,
        "signals": int(request["signals"]),
        "window_utc": [_iso_us(start_us), _iso_us(end_us)],
        "status": "pending",
        "http_status": None,
        "pages": 0,
        "quote_events": 0,
        "error": None,
    }
    cursor = read_cursor(out_dir, day, ticker)
    if cursor.get("status") in ("complete", "no_quotes_in_window"):
        record.update(
            {
                "status": cursor["status"],
                "http_status": cursor.get("http_status"),
                "pages": int(cursor.get("pages", 0)),
                "quote_events": int(cursor.get("quote_events", 0)),
                "resumed": True,
            }
        )
        return record
    token = cursor.get("next_token")
    page = int(cursor.get("pages", 0))
    events_total = int(cursor.get("quote_events", 0))
    http = cursor.get("http_status")
    http_codes: list[int] = []
    try:
        while True:
            params = {
                "symbols": ticker,
                "start": _iso_us(start_us),
                "end": _iso_us(end_us),
                "feed": "iex",
                "asof": day,
                "limit": PAGE_LIMIT,
            }
            if token:
                params["page_token"] = token
            r = _get(session, ALPACA_QUOTES_URL, params, headers)
            http = r.status_code
            http_codes.append(int(r.status_code))
            if r.status_code != 200:
                record.update(
                    {
                        "status": f"unknown_http_{r.status_code}",
                        "http_status": r.status_code,
                        "error": r.text[:300],
                        "pages": page,
                        "quote_events": events_total,
                    }
                )
                cursor.update(
                    {
                        "pages": page,
                        "next_token": token,
                        "status": record["status"],
                        "http_status": r.status_code,
                        "quote_events": events_total,
                        "error": record["error"],
                    }
                )
                write_cursor(out_dir, day, ticker, cursor)
                return record
            js = r.json()
            _write_page_json(raw_dir(out_dir, day, ticker) / f"page_{page}.json", js)
            data = js.get("quotes") or {}
            ev = data.get(ticker, []) if isinstance(data, dict) else []
            frame = (
                pl.DataFrame(_quote_rows(ev, ticker), schema=PAGE_SCHEMA)
                if ev
                else pl.DataFrame(schema=PAGE_SCHEMA)
            )
            _write_page_parquet(
                parts_dir(out_dir, day, ticker) / f"page_{page}.parquet", frame
            )
            events_total += len(ev)
            page += 1
            token = js.get("next_page_token")
            cursor.update(
                {
                    "pages": page,
                    "next_token": token,
                    "status": (
                        ("complete" if events_total else "no_quotes_in_window")
                        if not token
                        else "paginating"
                    ),
                    "http_status": http,
                    "quote_events": events_total,
                }
            )
            write_cursor(out_dir, day, ticker, cursor)
            if not token:
                break
            if req_sleep:
                time.sleep(req_sleep)
    except Exception as e:  # network error: a precise UNKNOWN, never fabricated data
        record.update(
            {
                "status": "unknown_request_exception",
                "http_status": http,
                "pages": page,
                "quote_events": events_total,
                "error": f"{type(e).__name__}: {str(e)[:200]}",
            }
        )
        cursor.update(
            {
                "pages": page,
                "next_token": token,
                "status": record["status"],
                "http_status": http,
                "quote_events": events_total,
                "error": record["error"],
            }
        )
        write_cursor(out_dir, day, ticker, cursor)
        return record
    record.update(
        {
            "status": "complete" if events_total else "no_quotes_in_window",
            "http_status": http,
            "http_codes": http_codes,
            "pages": page,
            "quote_events": events_total,
        }
    )
    return record


def merge_day(
    out_dir: Path, day: str, requests: list[dict], iex_roots: tuple[Path, ...]
) -> dict:
    """PRIMARY_KEEPFIRST merge: acquired raw parts first, existing IEX caches last.

    The original SIP corpus and every existing file are read-only; the combined day
    file is written only when the acquisition actually added prints, so an all-empty
    day stays a recorded KNOWN acquisition empty instead of a duplicated day file.
    """
    frames = []
    for request in requests:
        part = load_parts(out_dir, day, request["ticker"]).select(list(IEX_COLS))
        if part.height:
            frames.append(part)
    new_rows = int(sum(f.height for f in frames))
    if not new_rows:
        return {
            "merged": False,
            "reason": "no_new_events_known_acquisition_empty",
            "new_rows": 0,
        }
    for root in iex_roots:
        spath = supplement_day_path(root, day)
        if spath.exists() and quote_schema_fits(spath):
            frames.append(pl.read_parquet(spath).select(list(IEX_COLS)))
    merged = (
        pl.concat(frames, how="vertical")
        .unique(subset=["symbol", "ts_utc"], keep="first")
        .sort("symbol", "ts_utc")
    )
    target = out_dir / IEX_QUOTES_DIRNAME / f"{day}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp{os.getpid()}")
    merged.write_parquet(tmp)
    os.replace(tmp, target)
    return {
        "merged": True,
        "path": str(target),
        "rows": int(merged.height),
        "new_rows": new_rows,
        "symbols": int(merged["symbol"].n_unique()),
        "sha256": sha256_file(target),
    }


def acquire_block(
    out_dir: Path,
    days: list[str],
    resume: bool,
    req_sleep: float,
    env_path: Path,
    iex_roots: tuple[Path, ...],
) -> dict:
    """Fetch every (day, ticker) IEX window the manifests predeclared, incrementally."""
    import requests

    producer = producer_sha256()
    headers = _alpaca_headers(env_path)
    if headers is None:
        raise SystemExit(
            "[delayed-sip-iex] ALPACA_API_KEY/ALPACA_SECRET_KEY not found in "
            f"{env_path}; nothing was fetched (run the plan stage for metadata only)"
        )
    session = requests.Session()
    done = 0
    coverage = {
        "days_total": len(days),
        "days_with_candidates": 0,
        "requests_planned": 0,
        "requests_resolved": 0,
        "unknown_requests": 0,
        "known_empty_requests": 0,
        "present_in_existing_cache": 0,
        "merged_days": 0,
    }
    for day in days:
        manifest = load_manifest(out_dir, day)
        if manifest is None:
            raise SystemExit(
                f"[delayed-sip-iex] manifest missing for {day}; run the plan stage first"
            )
        requests = manifest.get("requests") or []
        coverage["requests_planned"] += len(requests)
        if requests:
            coverage["days_with_candidates"] += 1
        records = {r["ticker"]: r for r in (manifest.get("records") or [])}
        have = existing_iex_symbols(day, iex_roots)
        for request in requests:
            ticker = request["ticker"]
            prior = records.get(ticker)
            if prior and prior.get("status") in (
                "complete",
                "no_quotes_in_window",
                "present_in_existing_cache",
            ):
                coverage["requests_resolved"] += 1
                if prior.get("status") == "present_in_existing_cache":
                    coverage["present_in_existing_cache"] += 1
                elif prior.get("status") == "no_quotes_in_window":
                    coverage["known_empty_requests"] += 1
                continue
            if ticker in have:
                records[ticker] = {
                    "day": day,
                    "ticker": ticker,
                    "status": "present_in_existing_cache",
                    "feed": "iex",
                    "asof": day,
                    "window_utc": request["window_utc"],
                    "signals": int(request["signals"]),
                    "pages": 0,
                    "quote_events": None,
                }
                coverage["present_in_existing_cache"] += 1
            else:
                rec = fetch_symbol_day(session, headers, out_dir, day, ticker, request, req_sleep)
                records[ticker] = rec
                if str(rec["status"]).startswith("unknown"):
                    coverage["unknown_requests"] += 1
                elif rec["status"] == "no_quotes_in_window":
                    coverage["known_empty_requests"] += 1
            done += 1
            coverage["requests_resolved"] += 1
            manifest["records"] = [
                records[t] for t in (r["ticker"] for r in requests) if t in records
            ]
            manifest["fetched_through"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            write_json_atomic(manifest_path(out_dir, day), manifest)
            if done % 25 == 0:
                print(
                    f"[fetch] {done} day-ticker windows; last {day} {ticker} "
                    f"status={records[ticker]['status']} "
                    f"events={records[ticker].get('quote_events')}",
                    flush=True,
                )
            if req_sleep:
                time.sleep(req_sleep)
        manifest["records"] = [records[t] for t in (r["ticker"] for r in requests) if t in records]
        manifest["merge"] = merge_day(out_dir, day, requests, iex_roots)
        if manifest["merge"].get("merged"):
            coverage["merged_days"] += 1
        unknowns = [
            r["ticker"]
            for r in manifest["records"]
            if str(r.get("status", "")).startswith("unknown")
        ]
        manifest["unknown_tickers"] = unknowns
        manifest["acquisition_outcome"] = (
            "acquired"
            if not unknowns
            else "partial_unknown" if manifest["merge"].get("merged") else "unknown"
        )
        write_json_atomic(manifest_path(out_dir, day), manifest)
        print(
            f"[day] {day}: candidates={len(requests)} unknown={len(unknowns)} "
            f"merged={manifest['merge'].get('merged')} "
            f"new_rows={manifest['merge'].get('new_rows', 0)}",
            flush=True,
        )
    coverage["producer_sha256"] = producer
    summary = {
        "study": "alpha_delayed_sip_iex",
        "stage": "acquire",
        "status": STATUS,
        "feed": "iex",
        "asof": "trade day",
        "requests_planned": coverage["requests_planned"],
        "requests_resolved": coverage["requests_resolved"],
        "unknown_requests": coverage["unknown_requests"],
        "known_empty_requests": coverage["known_empty_requests"],
        "present_in_existing_cache": coverage["present_in_existing_cache"],
        "merged_days": coverage["merged_days"],
        "days_total": coverage["days_total"],
        "days_with_candidates": coverage["days_with_candidates"],
        "existing_iex_cache_roots": [str(r) for r in iex_roots],
        "req_sleep_s": req_sleep,
        "pacing_note": (
            "~171 requests/minute at the default sleep, below the historical Basic "
            "200/min REST limit; websocket symbol caps do not apply to REST"
        ),
        "runtime_s": None,
    }
    return summary


# ----- per-day scored states (IEX delayed intent facts, per view) --------------
STATE_COLUMNS = (
    "day",
    "view_key",
    "threshold",
    "delay_min",
    "exit_convention",
    "ticker",
    "t",
    "pred_60",
    "session_end",
    "entry_status",
    "gross_15",
    "exit_et_15",
    "exit_status_15",
    "gross_60",
    "exit_et_60",
    "exit_status_60",
    "gross_390",
    "exit_et_390",
    "exit_status_390",
    "intent_minute",
    "intent_us",
    "intent_status",
    "intent_reason",
    "rule_ok",
    "intent_ask",
    "intent_bid",
    "intent_age_s",
    "intent_spread_bps",
    "intent_ask_shares",
    "limit_price",
    "qty",
    "reserved_usd",
    "exit_minute",
    "exit_due_us",
)
STATE_TYPES = {
    "day": pl.String,
    "view_key": pl.String,
    "threshold": pl.Float64,
    "delay_min": pl.Int64,
    "exit_convention": pl.String,
    "ticker": pl.String,
    "t": pl.Int64,
    "pred_60": pl.Float64,
    "session_end": pl.Int64,
    "entry_status": pl.String,
    "gross_15": pl.Float64,
    "exit_et_15": pl.Int64,
    "exit_status_15": pl.String,
    "gross_60": pl.Float64,
    "exit_et_60": pl.Int64,
    "exit_status_60": pl.String,
    "gross_390": pl.Float64,
    "exit_et_390": pl.Int64,
    "exit_status_390": pl.String,
    "intent_minute": pl.Int64,
    "intent_us": pl.Int64,
    "intent_status": pl.String,
    "intent_reason": pl.String,
    "rule_ok": pl.Boolean,
    "intent_ask": pl.Float64,
    "intent_bid": pl.Float64,
    "intent_age_s": pl.Float64,
    "intent_spread_bps": pl.Float64,
    "intent_ask_shares": pl.Float64,
    "limit_price": pl.Float64,
    "qty": pl.Int64,
    "reserved_usd": pl.Float64,
    "exit_minute": pl.Int64,
    "exit_due_us": pl.Int64,
}


def empty_states() -> pl.DataFrame:
    return pl.DataFrame(schema={c: STATE_TYPES[c] for c in STATE_COLUMNS})


def state_paths(out_dir: Path, day: str) -> tuple[Path, Path]:
    return out_dir / "states" / f"{day}.parquet", out_dir / "states" / f"{day}.cov.json"


def iex_day_digest(out_dir: Path, day: str, iex_roots: tuple[Path, ...]) -> str | None:
    """Resume digest of every IEX day file the consumers actually read (feeds pinned).

    The producer's own acquired day file first, then any FITTING existing IEX cache
    root; a change in any of them invalidates the day's scored states and replay.
    """
    entries = []
    own = out_dir / IEX_QUOTES_DIRNAME / f"{day}.parquet"
    if own.exists():
        entries.append({"path": str(own), "sha256": sha256_file(own)})
    for root in iex_roots:
        spath = supplement_day_path(root, day)
        if spath.exists() and quote_schema_fits(spath):
            entries.append({"path": str(spath), "sha256": sha256_file(spath)})
    if not entries:
        return None
    return _digest_bytes({"iex_day_feeds": entries})


def iex_day_frame(out_dir: Path, day: str, tickers: set[str], iex_roots: tuple[Path, ...]):
    """This run's acquired IEX day cache first, then any FITTING existing IEX cache.

    The ORIGINAL (acquired raw) print wins for a duplicated (symbol, ts_utc); an
    existing cache may only add timestamps. Returns None when neither source has the
    day - which the consumer reports as an IEX acquisition UNKNOWN, never as cash.
    """
    frames = []
    own = out_dir / IEX_QUOTES_DIRNAME / f"{day}.parquet"
    if own.exists():
        frames.append(pl.read_parquet(own).select(list(IEX_COLS)))
    for root in iex_roots:
        spath = supplement_day_path(root, day)
        if spath.exists() and quote_schema_fits(spath):
            frames.append(pl.read_parquet(spath).select(list(IEX_COLS)))
    if not frames:
        return None
    keep = sorted(tickers)
    frames = [f.filter(pl.col("symbol").is_in(keep)) for f in frames]
    frames = [f for f in frames if f.height]
    if not frames:
        return None
    merged = frames[0] if len(frames) == 1 else pl.concat(frames, how="vertical")
    return merged.unique(subset=["symbol", "ts_utc"], keep="first").sort("symbol", "ts_utc")


def intent_status_column(intent_status: str) -> str:
    """Daily counter column for one raw IEX intent status."""
    if intent_status == "quote_not_acquired":
        return "iex_intent_not_acquired"
    if intent_status == "invalid_or_crossed_quote":
        return "iex_intent_invalid_or_crossed"
    return f"iex_intent_{intent_status}"


def score_day(day: str, manifest: dict, out_dir: Path, iex_roots: tuple[Path, ...]):
    """Score one session's predeclared signals on the delayed IEX intent clocks.

    The forecast is read from the predetermined manifest (the immutable model's
    pred_60 on unchanged state-t features, pinned by the manifest's model hash); the
    intent facts are the IEX-only observation at each view's delayed intent clock.
    States are written per view only for that view's own signal set (pred_60 >= the
    view's predeclared threshold), so the day's states are the UNION of the twelve
    views' signal sets; the manifest keeps the full qualified watchlist for the
    neutral IEX acquisition.
    """
    signals = manifest.get("signals") or []
    cov = {
        "manifest_signals": len(signals),
        "states_signals": 0,
        "tickers_signals": 0,
        "tickers_with_iex_stream": 0,
        "iex_day_file_present": (out_dir / IEX_QUOTES_DIRNAME / f"{day}.parquet").exists(),
        "intent_status_counts": {},
    }
    sig_frame = (
        pl.DataFrame(
            {c: [s[c] for s in signals] for c in SIGNAL_COLUMNS}, schema=SIGNAL_TYPES
        )
        if signals
        else pl.DataFrame(schema=SIGNAL_TYPES)
    )
    if not sig_frame.height:
        return empty_states(), cov
    sig_frame = sig_frame.filter(pl.col("pred_60") >= min(THRESHOLDS))
    if not sig_frame.height:
        return empty_states(), cov
    tickers = sorted(set(sig_frame["ticker"].to_list()))
    cov["states_signals"] = int(sig_frame.height)
    cov["tickers_signals"] = len(tickers)
    iex_frame = iex_day_frame(out_dir, day, set(tickers), iex_roots)
    streams = symbol_quotes(iex_frame, day)
    del iex_frame
    cov["tickers_with_iex_stream"] = len(streams)
    out: list[dict] = []
    status_counts: Counter = Counter()
    for r in sig_frame.iter_rows(named=True):
        t = int(r["t"])
        session_end = int(r["session_end"])
        pred = float(r["pred_60"])
        iex_sq = streams.get(r["ticker"])
        base = {
            "day": day,
            "ticker": r["ticker"],
            "t": t,
            "pred_60": pred,
            "session_end": session_end,
            "entry_status": r["entry_status"],
            "gross_15": r["gross_15"],
            "exit_et_15": r["exit_et_15"],
            "exit_status_15": r["exit_status_15"],
            "gross_60": r["gross_60"],
            "exit_et_60": r["exit_et_60"],
            "exit_status_60": r["exit_status_60"],
            "gross_390": r["gross_390"],
            "exit_et_390": r["exit_et_390"],
            "exit_status_390": r["exit_status_390"],
        }
        for delay in DELAYS_MIN:
            intent_us = minute_us(day, intent_minute_of(t, delay))
            facts = delayed_intent_facts(iex_sq, intent_us)
            reason, ok = entry_rule_status(facts, pred)
            status_counts[facts["intent_status"]] += 1
            for view in VIEWS:
                if view.delay_min != delay or pred < view.threshold:
                    continue
                exit_minute = exit_minute_of(t, delay, view.exit_convention, session_end)
                rec = dict(base)
                rec.update(
                    {
                        "view_key": view.key,
                        "threshold": view.threshold,
                        "delay_min": int(delay),
                        "exit_convention": view.exit_convention,
                        "intent_minute": intent_minute_of(t, delay),
                        "intent_us": int(intent_us),
                        "intent_status": facts["intent_status"],
                        "intent_reason": reason,
                        "rule_ok": bool(ok),
                        "intent_ask": facts["intent_ask"],
                        "intent_bid": facts["intent_bid"],
                        "intent_age_s": facts["intent_age_s"],
                        "intent_spread_bps": facts["intent_spread_bps"],
                        "intent_ask_shares": facts["intent_ask_shares"],
                        "limit_price": facts["limit_price"],
                        "qty": int(facts["qty"]),
                        "reserved_usd": float(facts["reserved_usd"]),
                        "exit_minute": int(exit_minute),
                        "exit_due_us": minute_us(day, int(exit_minute)),
                    }
                )
                out.append(rec)
    cov["intent_status_counts"] = dict(status_counts)
    frame = pl.DataFrame(
        {c: [rec[c] for rec in out] for c in STATE_COLUMNS},
        schema={c: STATE_TYPES[c] for c in STATE_COLUMNS},
    )
    return frame, cov


def score_block(out_dir: Path, days: list[str], resume: bool, iex_roots: tuple[Path, ...]) -> dict:
    """Build (or resume) every day's scored states atomically, one day at a time."""
    producer = producer_sha256()
    contract_sha = _digest_bytes(CONTRACT)
    model_sha = sha256_file(MODEL_PATH)
    todo, infos = [], []
    coverage = {
        "days_total": len(days),
        "days_cached": 0,
        "days_scored": 0,
        "manifest_signals": 0,
        "states_signals": 0,
        "intent_status_counts": {},
        "days_without_iex_day_file": [],
        "days_without_iex_stream": [],
    }
    intent_totals: Counter = Counter()
    expects: dict[str, str] = {}
    for day in days:
        spath, cpath = state_paths(out_dir, day)
        manifest = load_manifest(out_dir, day)
        if manifest is None:
            raise SystemExit(
                f"[delayed-sip-iex] manifest missing for {day}; run the plan stage first"
            )
        iex_sha = iex_day_digest(out_dir, day, iex_roots)
        expect = _digest_bytes(
            {
                "day": day,
                "producer": producer,
                "contract": contract_sha,
                "model": model_sha,
                "manifest": manifest.get("resume_hash"),
                "iex_day": iex_sha,
                "schema": STATES_SCHEMA,
            }
        )
        expects[day] = expect
        prior = None
        if resume and cpath.exists() and spath.exists():
            try:
                prior = json.loads(cpath.read_text())
            except (json.JSONDecodeError, OSError):
                prior = None
        if (
            prior is not None
            and prior.get("resume_hash") == expect
            and prior.get("schema") == STATES_SCHEMA
        ):
            infos.append({"day": day, "resumed": True, "coverage": prior.get("coverage", {})})
        else:
            todo.append(day)
    for n, day in enumerate(todo, 1):
        spath, cpath = state_paths(out_dir, day)
        manifest = load_manifest(out_dir, day)
        states, cov = score_day(day, manifest, out_dir, iex_roots)
        spath.parent.mkdir(parents=True, exist_ok=True)
        tmp = spath.with_name(f"{spath.name}.tmp{os.getpid()}")
        states.write_parquet(tmp)
        os.replace(tmp, spath)
        write_json_atomic(
            cpath,
            {
                "day": day,
                "rows": int(states.height),
                "resume_hash": expects[day],
                "producer_sha256": producer,
                "contract_sha256": contract_sha,
                "model_sha256": model_sha,
                "manifest_resume_hash": manifest.get("resume_hash"),
                "states_sha256": sha256_file(spath),
                "schema": STATES_SCHEMA,
                "coverage": cov,
            },
        )
        infos.append({"day": day, "resumed": False, "coverage": cov})
        if n % 25 == 0 or n == len(todo):
            print(
                f"[score] {n}/{len(todo)} days, last {day}, signals={cov['states_signals']}, "
                f"state_rows={int(states.height)} over {len(VIEWS)} views, "
                f"iex_streams={cov['tickers_with_iex_stream']}",
                flush=True,
            )
    for info in infos:
        cov = info["coverage"] or {}
        coverage["manifest_signals"] += int(cov.get("manifest_signals", 0))
        coverage["states_signals"] += int(cov.get("states_signals", 0))
        intent_totals.update(cov.get("intent_status_counts") or {})
        if cov.get("iex_day_file_present") is False:
            coverage["days_without_iex_day_file"].append(info["day"])
        if (
            int(cov.get("tickers_with_iex_stream", 0)) == 0
            and int(cov.get("states_signals", 0)) > 0
        ):
            coverage["days_without_iex_stream"].append(info["day"])
    coverage.update(
        {
            "days_cached": len(infos),
            "days_scored": len(todo),
            "intent_status_counts": dict(intent_totals),
        }
    )
    return coverage


# ----- per-view causal replay (IEX gates, SIP execution benchmark) -------------
DAILY_COLUMNS = (
    "day",
    "view_key",
    "threshold",
    "delay_min",
    "exit_convention",
    "signals",
    "intents_funded",
    "attempts",
    "fills",
    "known_fills",
    "unknown_fills",
    "no_match_fills",
    "peak_reserved_usd",
    "positions_peak",
    "positions_mean",
) + DAILY_REASON_COLUMNS + DAILY_INTENT_STATUS_COLUMNS
DAILY_TYPES = {
    "day": pl.String,
    "view_key": pl.String,
    "threshold": pl.Float64,
    "delay_min": pl.Int64,
    "exit_convention": pl.String,
    "signals": pl.Int64,
    "intents_funded": pl.Int64,
    "attempts": pl.Int64,
    "fills": pl.Int64,
    "known_fills": pl.Int64,
    "unknown_fills": pl.Int64,
    "no_match_fills": pl.Int64,
    "peak_reserved_usd": pl.Float64,
    "positions_peak": pl.Int64,
    "positions_mean": pl.Float64,
}
for _c in DAILY_REASON_COLUMNS + DAILY_INTENT_STATUS_COLUMNS:
    DAILY_TYPES[_c] = pl.Int64
TRADE_COLUMNS = (
    "day",
    "view_key",
    "threshold",
    "delay_min",
    "exit_convention",
    "ticker",
    "t",
    "pred_60",
    "intent_minute",
    "intent_us",
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
    "entry_arrival_us",
    "exit_minute",
    "exit_due_us",
    "exit_submit_us",
    "exit_submit_status",
    "exit_arrival_us",
    "exit_bid",
    "exit_age_s",
    "exit_quote_us",
    "exit_price_status",
    "exit_status",
    "actual_exit_us",
    "fill_status",
    "gross_60",
    "exit_status_60",
)
TRADE_TYPES = {
    "day": pl.String,
    "view_key": pl.String,
    "threshold": pl.Float64,
    "delay_min": pl.Int64,
    "exit_convention": pl.String,
    "ticker": pl.String,
    "t": pl.Int64,
    "pred_60": pl.Float64,
    "intent_minute": pl.Int64,
    "intent_us": pl.Int64,
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
    "entry_arrival_us": pl.Int64,
    "exit_minute": pl.Int64,
    "exit_due_us": pl.Int64,
    "exit_submit_us": pl.Int64,
    "exit_submit_status": pl.String,
    "exit_arrival_us": pl.Int64,
    "exit_bid": pl.Float64,
    "exit_age_s": pl.Float64,
    "exit_quote_us": pl.Int64,
    "exit_price_status": pl.String,
    "exit_status": pl.String,
    "actual_exit_us": pl.Int64,
    "fill_status": pl.String,
    "gross_60": pl.Float64,
    "exit_status_60": pl.String,
}
for _rung in RUNG_COSTS:
    DAILY_COLUMNS += (f"known_usd_{int(_rung)}", f"lower_bound_usd_{int(_rung)}")
    DAILY_TYPES[f"known_usd_{int(_rung)}"] = pl.Float64
    DAILY_TYPES[f"lower_bound_usd_{int(_rung)}"] = pl.Float64
    TRADE_COLUMNS += (f"net_usd_{int(_rung)}",)
    TRADE_TYPES[f"net_usd_{int(_rung)}"] = pl.Float64


def empty_daily() -> pl.DataFrame:
    return pl.DataFrame(schema={c: DAILY_TYPES[c] for c in DAILY_COLUMNS})


def empty_trades() -> pl.DataFrame:
    return pl.DataFrame(schema={c: TRADE_TYPES[c] for c in TRADE_COLUMNS})


def resolve_delayed_trade(
    day: str,
    r: dict,
    view: View,
    iex_sq,
    sip_sq,
    session_end: int,
    session_end_us: int,
) -> dict:
    """Resolve one funded delayed intent: the IOC entry, then the scheduled exit.

    ENTRY (no rest, by construction): the causal IOC limit was fixed at the delayed
    IEX intent clock; at intent + 250ms ONE observation of the full SIP state decides
    it - a conditional full-touch fill at the actual SIP ASK when ask <= the IEX-based
    limit and the SIP L1 depth supports q, an unfilled NO_MATCH when ask > limit, an
    UNKNOWN when the SIP book is stale / invalid / non-regular / absent, because an
    IOC LIMIT CANNOT rest and no later print may fill it.

    EXIT (MARKET, may rest): the submission is IEX-fresh-gated (at the due exit
    intent, else the first eligible IEX regular print at/after it inside RTH); the
    price is the SIP BID at submission + 250ms, or the first eligible SIP regular
    print at/after arrival when the SIP book is invalid / unavailable - the actual
    market REST clock, which is when the position, its cash and its cooldown release.
    """
    record = {
        "day": day,
        "view_key": view.key,
        "threshold": view.threshold,
        "delay_min": int(view.delay_min),
        "exit_convention": view.exit_convention,
        "ticker": r["ticker"],
        "t": int(r["t"]),
        "pred_60": float(r["pred_60"]),
        "intent_minute": int(r["intent_minute"]),
        "intent_us": int(r["intent_us"]),
        "limit_price": float(r["limit_price"]),
        "qty": int(r["qty"]),
        "reserved_usd": float(r["reserved_usd"]),
        "exit_minute": int(r["exit_minute"]),
        "exit_due_us": int(r["exit_due_us"]),
        "gross_60": r["gross_60"],
        "exit_status_60": r["exit_status_60"],
        "entry_status": None,
        "entry_ask": None,
        "entry_bid": None,
        "entry_age_s": None,
        "entry_quote_us": None,
        "entry_arrival_us": arrival_us(int(r["intent_us"])),
        "exit_submit_us": None,
        "exit_submit_status": None,
        "exit_arrival_us": None,
        "exit_bid": None,
        "exit_age_s": None,
        "exit_quote_us": None,
        "exit_price_status": None,
        "exit_status": None,
        "actual_exit_us": None,
        "fill_status": None,
        "attempt_index": None,
        "positions_at_entry": None,
    }
    for rung in RUNG_COSTS:
        record[f"net_usd_{int(rung)}"] = None

    # The ranked SIP cache covers only part of the qualified universe; a ticker with
    # no SIP stream is an execution-benchmark UNKNOWN (never a crash, never cash).
    # The IEX stream, by contrast, is guaranteed for every funded intent: the entry
    # rule already required a quoted IEX observation at the intent clock.
    if sip_sq is None:
        record["entry_status"] = "sip_quote_not_acquired"
        record["fill_status"] = "unknown_entry_execution"
        record["exit_status"] = "unknown_entry_execution"
        record["actual_exit_us"] = session_end_us + 1
        return record

    entry_arrival = record["entry_arrival_us"]
    eq, estat = entry_price_leg(sip_sq, entry_arrival)
    if eq is None:
        record["entry_status"] = estat
        record["fill_status"] = "unknown_entry_execution"
        record["exit_status"] = "unknown_entry_execution"
        record["actual_exit_us"] = session_end_us + 1
        return record
    record["entry_ask"] = float(eq["ask"])
    record["entry_bid"] = float(eq["bid"])
    record["entry_age_s"] = float(eq["age_s"])
    record["entry_quote_us"] = int(eq["quote_us"])
    if float(eq["ask"]) > float(record["limit_price"]) + 1e-12:
        record["entry_status"] = "no_match_at_l1"
        record["fill_status"] = "no_match_at_l1_unfilled_cash"
        record["actual_exit_us"] = entry_arrival
        return record
    if float(eq["ask_shares"]) < record["qty"]:
        record["entry_status"] = "unknown_entry_partial_depth"
        record["fill_status"] = "unknown_entry_execution"
        record["exit_status"] = "unknown_entry_partial_depth"
        record["actual_exit_us"] = session_end_us + 1
        return record
    record["entry_status"] = "conditional_ioc_fill_sip_benchmark"

    submit_us, submit_status = submission_clock(iex_sq, int(r["exit_due_us"]), session_end_us)
    record["exit_submit_us"] = submit_us
    record["exit_submit_status"] = submit_status
    if submit_us is None:
        record["fill_status"] = "unknown_exit_execution"
        record["exit_status"] = "unknown_exit_no_valid_regular_iex_quote"
        record["actual_exit_us"] = session_end_us + 1
        return record
    exit_arrival = arrival_us(submit_us)
    record["exit_arrival_us"] = exit_arrival
    xq, xstat, priced_at = priced_leg(sip_sq, exit_arrival, session_end_us)
    record["exit_price_status"] = xstat
    if xq is None:
        record["fill_status"] = "unknown_exit_execution"
        record["exit_status"] = xstat
        record["actual_exit_us"] = session_end_us + 1
        return record
    record["exit_bid"] = float(xq["bid"])
    record["exit_age_s"] = float(xq["age_s"])
    record["exit_quote_us"] = int(xq["quote_us"])
    if float(xq["bid_shares"]) < record["qty"]:
        record["fill_status"] = "unknown_exit_execution"
        record["exit_status"] = "unknown_exit_partial_depth"
        record["actual_exit_us"] = session_end_us + 1
        return record
    record["exit_status"] = "conditional_market_fill_sip_benchmark"
    record["fill_status"] = "conditional_fill"
    record["actual_exit_us"] = int(priced_at) if priced_at is not None else int(exit_arrival)
    for rung in RUNG_COSTS:
        record[f"net_usd_{int(rung)}"] = net_usd(
            record["qty"], record["exit_bid"], record["entry_ask"], rung
        )
    return record


def replay_day(
    day: str, states: pl.DataFrame, iex_streams: dict, sip_streams: dict, view: View
) -> tuple[dict, list[dict]]:
    """One view, one session: IEX-observed intents under the funded $3,000 book.

    Chronological by delayed intent clock; the highest-score eligible intents of a
    simultaneous clock are funded (up to the slot and cash limits) BEFORE any arrival
    outcome is seen, so a fourth same-clock candidate is never substituted after an
    unfilled one. Cash comes back only at an ACTUAL exit clock, an unmatched entry's
    arrival, or the session end.
    """
    daily = dict.fromkeys(DAILY_COLUMNS, 0)
    daily.update(
        {
            "day": day,
            "view_key": view.key,
            "threshold": view.threshold,
            "delay_min": int(view.delay_min),
            "exit_convention": view.exit_convention,
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
    rows = list(states.iter_rows(named=True))
    daily["signals"] = int(len(rows))
    for r in rows:
        col = intent_status_column(r["intent_status"])
        if col in daily:
            daily[col] += 1
    groups: dict[int, list[dict]] = {}
    for r in rows:
        groups.setdefault(int(r["intent_minute"]), []).append(r)

    cash, active = float(BOOK), set()
    attempts: dict[str, int] = {}
    cooldown_until: dict[str, int] = {}
    queue: list[tuple[int, int, str, float]] = []
    seq = 0
    trades: list[dict] = []
    positions_samples: list[int] = []
    reserved_total = 0.0
    for minute in sorted(groups):
        intent_us = minute_us(day, minute)
        while queue and queue[0][0] <= intent_us:
            _, _, sym, proceeds = heapq.heappop(queue)
            active.discard(sym)
            cash += proceeds
        admitted: list[dict] = []
        for r in groups[minute]:
            sym = r["ticker"]
            if bool(r["rule_ok"]):
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
                active.add(sym)
                positions_samples.append(len(active))
                reserved_total += float(r["reserved_usd"])
                daily["intents_funded"] += 1
                daily["attempts"] += 1
                admitted.append(r)
            else:
                daily[daily_reason_key(r["intent_reason"])] += 1
        for r in admitted:
            record = resolve_delayed_trade(
                day,
                r,
                view,
                iex_streams.get(r["ticker"]),
                sip_streams.get(r["ticker"]),
                session_end,
                session_end_us,
            )
            record["attempt_index"] = int(attempts[r["ticker"]])
            record["positions_at_entry"] = len(active)
            status = record["fill_status"]
            release = int(record["actual_exit_us"])
            if status == "no_match_at_l1_unfilled_cash":
                daily["no_match_fills"] += 1
            else:
                daily["unknown_fills" if "unknown" in status else "fills"] += 1
                if status == "conditional_fill":
                    daily["known_fills"] += 1
                    for rung in RUNG_COSTS:
                        daily[f"known_usd_{int(rung)}"] += float(record[f"net_usd_{int(rung)}"])
                cooldown_until[r["ticker"]] = release + COOLDOWN_MIN * 60_000_000
            heapq.heappush(queue, (release, seq, r["ticker"], float(r["reserved_usd"])))
            seq += 1
            trades.append(record)
    daily["positions_peak"] = max(positions_samples) if positions_samples else 0
    daily["positions_mean"] = float(np.mean(positions_samples)) if positions_samples else 0.0
    daily["peak_reserved_usd"] = float(reserved_total)
    for rung in RUNG_COSTS:
        daily[f"lower_bound_usd_{int(rung)}"] = (
            daily[f"known_usd_{int(rung)}"] - ORDER_BUDGET * daily["unknown_fills"]
        )
    return daily, trades


def replay_block(
    out_dir: Path,
    block: str,
    days: list[str],
    resume: bool,
    iex_roots: tuple[Path, ...],
    sip_roots: tuple[Path, ...],
) -> dict:
    """Replay every view on every day of one block, writing resumable per-day parts."""
    producer = producer_sha256()
    contract_sha = _digest_bytes(CONTRACT)
    model_sha = sha256_file(MODEL_PATH)
    sip_digest, sip_entries = supplement_digest(sip_roots)
    root = out_dir / "replay" / block
    root.mkdir(parents=True, exist_ok=True)
    todo, infos = [], []
    expects: dict[str, str] = {}
    coverage = {
        "block": block,
        "days_total": len(days),
        "days_cached": 0,
        "days_replayed": 0,
        "signals": 0,
        "intents_funded": 0,
        "attempts": 0,
        "fills": 0,
        "known_fills": 0,
        "unknown_fills": 0,
        "no_match_fills": 0,
    }
    for day in days:
        dpath = root / f"{day}.parquet"
        tpath = root / f"{day}.trades.parquet"
        cpath = root / f"{day}.cov.json"
        spath, scov = state_paths(out_dir, day)
        if not spath.exists():
            raise SystemExit(
                f"[delayed-sip-iex] scored states missing for {day} ({spath}); run the "
                "score stage for the whole block before replaying it"
            )
        manifest = load_manifest(out_dir, day)
        if manifest is None:
            raise SystemExit(
                f"[delayed-sip-iex] manifest missing for {day}; run the plan stage first"
            )
        states_sha = None
        if scov.exists():
            try:
                states_sha = json.loads(scov.read_text()).get("states_sha256")
            except (json.JSONDecodeError, OSError):
                states_sha = None
        if states_sha is None:
            states_sha = sha256_file(spath)
        iex_sha = iex_day_digest(out_dir, day, iex_roots)
        expect = _digest_bytes(
            {
                "day": day,
                "producer": producer,
                "contract": contract_sha,
                "model": model_sha,
                "manifest": manifest.get("resume_hash"),
                "states": states_sha,
                "iex_day": iex_sha,
                "sip": sip_digest,
                "schema": REPLAY_SCHEMA,
            }
        )
        expects[day] = expect
        prior = None
        if resume and cpath.exists() and dpath.exists() and tpath.exists():
            try:
                prior = json.loads(cpath.read_text())
            except (json.JSONDecodeError, OSError):
                prior = None
        if (
            prior is not None
            and prior.get("resume_hash") == expect
            and prior.get("schema") == REPLAY_SCHEMA
        ):
            infos.append({"day": day, "resumed": True, "coverage": prior.get("coverage", {})})
        else:
            todo.append(day)
    for n, day in enumerate(todo, 1):
        dpath = root / f"{day}.parquet"
        tpath = root / f"{day}.trades.parquet"
        cpath = root / f"{day}.cov.json"
        states = pl.read_parquet(state_paths(out_dir, day)[0])
        tickers = sorted(set(states["ticker"].to_list())) if states.height else []
        iex_frame = iex_day_frame(out_dir, day, set(tickers), iex_roots) if tickers else None
        iex_streams = symbol_quotes(iex_frame, day)
        del iex_frame
        sip_frame = (
            quote_day_frame(DATA_ROOT, day, set(tickers), sip_roots) if tickers else None
        )
        sip_streams = symbol_quotes(sip_frame, day)
        del sip_frame
        daily_rows, trade_rows = [], []
        day_cov = {
            "states_rows": int(states.height) if states.height else 0,
            "iex_streams": len(iex_streams),
            "sip_streams": len(sip_streams),
            "views": {},
        }
        for view in VIEWS:
            view_states = states.filter(pl.col("view_key") == view.key)
            daily, trades = replay_day(day, view_states, iex_streams, sip_streams, view)
            daily_rows.append(daily)
            trade_rows.extend(trades)
            day_cov["views"][view.key] = {
                "signals": daily["signals"],
                "attempts": daily["attempts"],
                "fills": daily["fills"],
                "known_fills": daily["known_fills"],
                "unknown_fills": daily["unknown_fills"],
                "no_match_fills": daily["no_match_fills"],
                f"known_usd_{int(SELECT_COST)}": daily[f"known_usd_{int(SELECT_COST)}"],
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
                "resume_hash": expects[day],
                "producer_sha256": producer,
                "contract_sha256": contract_sha,
                "model_sha256": model_sha,
                "sip_digest": sip_digest,
                "schema": REPLAY_SCHEMA,
                "coverage": day_cov,
            },
        )
        infos.append({"day": day, "resumed": False, "coverage": day_cov})
        if n % 25 == 0 or n == len(todo):
            funded = sum(v["attempts"] for v in day_cov["views"].values())
            print(
                f"[replay:{block}] {n}/{len(todo)} days, last {day}, "
                f"funded_intents={funded} over {len(VIEWS)} views",
                flush=True,
            )
    for info in infos:
        cov = info["coverage"] or {}
        coverage["signals"] += sum(v.get("signals", 0) for v in (cov.get("views") or {}).values())
        coverage["attempts"] += sum(v.get("attempts", 0) for v in (cov.get("views") or {}).values())
        coverage["intents_funded"] = coverage["attempts"]
        coverage["fills"] += sum(v.get("fills", 0) for v in (cov.get("views") or {}).values())
        coverage["known_fills"] += sum(
            v.get("known_fills", 0) for v in (cov.get("views") or {}).values()
        )
        coverage["unknown_fills"] += sum(
            v.get("unknown_fills", 0) for v in (cov.get("views") or {}).values()
        )
        coverage["no_match_fills"] += sum(
            v.get("no_match_fills", 0) for v in (cov.get("views") or {}).values()
        )
    coverage.update(
        {"days_cached": len(infos), "days_replayed": len(todo), "sip_supplements": sip_entries}
    )
    return coverage


# ----- block aggregation ------------------------------------------------------
def read_block_frames(
    out_dir: Path, block: str, days: list[str] | None = None
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """All per-day parts of one block: the daily rows and the funded-intent trades.

    ``days`` restricts the parts (a day-subset smoke must aggregate exactly the days
    it replayed, never a mixture with other days' parts already on disk).
    """
    root = out_dir / "replay" / block
    keep = set(days) if days else None
    daily_parts = [
        p
        for p in sorted(root.glob("????-??-??.parquet"))
        if keep is None or p.stem in keep
    ]
    trade_parts = [
        p
        for p in sorted(root.glob("????-??-??.trades.parquet"))
        if keep is None or p.name[: -len(".trades.parquet")] in keep
    ]
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


def monthly_view(daily: pl.DataFrame) -> dict:
    """Per-month traded-day / fill / UNKNOWN accounting with the known contribution."""
    col = f"known_usd_{int(SELECT_COST)}"
    months = sorted({str(d)[:7] for d in daily["day"].to_list()})
    out = {}
    for month in months:
        rows = daily.filter(pl.col("day").str.starts_with(month))
        out[month] = {
            "days_replayed": int(rows.height),
            "traded_days": int((rows["attempts"] > 0).sum()),
            "attempts": int(rows["attempts"].sum()),
            "fills": int(rows["fills"].sum()),
            "known_fills": int(rows["known_fills"].sum()),
            "unknown_fills": int(rows["unknown_fills"].sum()),
            "no_match_fills": int(rows["no_match_fills"].sum()),
            col: float(rows[col].sum()),
            f"mean_daily_{col}": float(rows[col].mean()),
        }
    return out


def yearly_view(daily: pl.DataFrame) -> dict:
    col = f"known_usd_{int(SELECT_COST)}"
    years = sorted({str(d)[:4] for d in daily["day"].to_list()})
    out = {}
    for year in years:
        rows = daily.filter(pl.col("day").str.starts_with(year))
        out[year] = {
            "days_replayed": int(rows.height),
            "traded_days": int((rows["attempts"] > 0).sum()),
            "attempts": int(rows["attempts"].sum()),
            "fills": int(rows["fills"].sum()),
            "known_fills": int(rows["known_fills"].sum()),
            "unknown_fills": int(rows["unknown_fills"].sum()),
            "no_match_fills": int(rows["no_match_fills"].sum()),
            col: float(rows[col].sum()),
            f"mean_daily_{col}": float(rows[col].mean()),
        }
    return out


def fee_scenarios(trades: pl.DataFrame, n_days: int) -> dict:
    """The sourced actual Alpaca fee profiles priced on this view's known fills.

    Both legs of every conditional fill are priced by the provider ledger's schedule
    (zero commission + SEC/TAF/CAT pass-through); per-day per-TYPE amounts aggregate
    and round UP to a cent exactly as the schedule states. The rung-0 touch result
    minus the measured fees is the actual-fee net; UNKNOWN legs are never priced.
    """
    out: dict[str, dict] = {}
    for pid in FEE_PROFILE_IDS:
        profile = PROVIDERS[pid]
        rows: list[dict] = []
        net_after: list[float | None] = []
        filled = (
            trades.filter(pl.col("fill_status") == "conditional_fill") if trades.height else trades
        )
        if filled.height:
            for i in range(filled.height):
                entry_ask = float(filled["entry_ask"][i])
                exit_bid = float(filled["exit_bid"][i])
                qty = int(filled["qty"][i])
                rec = trade_fee_record(profile, entry_ask, exit_bid, qty)
                row = {
                    "known_fill": True,
                    "day": str(filled["day"][i]),
                    "exit_day": str(filled["day"][i]),
                }
                for comp, val in rec["buy_leg_fees"].items():
                    row[f"fee_{comp}_buy"] = val
                for comp, val in rec["sell_leg_fees"].items():
                    row[f"fee_{comp}_sell"] = val
                rows.append(row)
                fee = rec["fee_total_usd"]
                if fee is None:
                    net_after.append(None)
                else:
                    net_after.append(net_usd(qty, exit_bid, entry_ask, 0.0) - fee)
        ledger = build_day_ledger(rows, profile)
        raw_total = (
            round(sum(d["fee_raw_total_usd"] for d in ledger.values()), 10) if ledger else 0.0
        )
        posted_total = None
        if profile.daily_up_cent_posting and ledger:
            posted_total = round(
                sum(
                    d["fee_posted_total_usd_eod"]
                    for d in ledger.values()
                    if d["fee_posted_total_usd_eod"] is not None
                ),
                10,
            )
        net_known = [v for v in net_after if v is not None]
        two_sided = 0.0
        if filled.height:
            for i in range(filled.height):
                two_sided += float(filled["entry_ask"][i]) * int(filled["qty"][i])
                two_sided += float(filled["exit_bid"][i]) * int(filled["qty"][i])
        n_fills = int(filled.height)
        out[pid] = {
            "profile_id": pid,
            "provider": profile.provider,
            "plan": profile.plan,
            "scenario": profile.scenario,
            "conditional": profile.conditional,
            "conditional_assumption": profile.conditional_assumption,
            "total_complete": profile.total_complete,
            "unknown_components": profile.unknown_components(),
            "n_known_fills": n_fills,
            "fee_raw_total_usd": raw_total,
            "fee_posted_eod_total_usd": posted_total,
            "mean_fee_usd_per_fill": (raw_total / n_fills) if n_fills else None,
            "fee_rt_bps_of_two_sided_notional": (
                (raw_total / two_sided * 10_000.0) if two_sided else None
            ),
            "net_after_fees_usd_total": (round(sum(net_known), 10) if net_known else None),
            "net_after_fees_usd_per_calendar_day": (
                (sum(net_known) / n_days) if (n_days and net_known) else None
            ),
            "fills_with_measured_fee": len(net_known),
            "posting_policy": (
                "each fee TYPE aggregated per day per account, then up-cent; per-trade "
                "amounts stay unrounded regulator math"
                if profile.daily_up_cent_posting
                else "provider posting/rounding policy unverified: raw regulator math only"
            ),
        }
    return out


def data_expense_block() -> dict:
    """The data-cost line: the free Basic assumption vs the SIP benchmark, apart."""
    alpaca = PROVIDERS[FEE_PROFILE_IDS[0]]
    opex = provider_data_opex(alpaca)
    return {
        "current_assumption_usd_per_year": 0.0,
        "current_assumption_plan": "Alpaca Basic (free): IEX-only, non-consolidated; "
        "consolidated SIP history is 15-min delayed",
        "current_assumption_note": (
            "this study's entire observability design (delayed IEX intents) is what "
            "makes the $0 data-expense assumption self-consistent; it is NOT a claim "
            "that the free feed is equivalent to real-time SIP"
        ),
        "sip_benchmark_usd_per_year": float(opex["usd_per_year"]),
        "sip_benchmark_usd_per_month": float(opex["usd_per_month"]),
        "sip_benchmark_plan": opex["plan"],
        "sip_benchmark_usd_per_replayed_trading_day": (
            float(opex["usd_per_year"]) / ANNUAL_SESSIONS
        ),
        "shared_once_per_portfolio": bool(DATA_OPEX["shared_once_per_portfolio"]),
        "shared_once_note": DATA_OPEX["shared_once_note"],
        "free_non_consolidated_note": DATA_OPEX["free_non_consolidated_note"],
        "source": opex["source"],
    }


def income_minus_shared_opex(known_per_day: float) -> dict:
    opex_per_day = float(DATA_OPEX["alpaca_algo_trader_plus_usd_per_year"]) / ANNUAL_SESSIONS
    return {
        "basic_free_data_expense_usd_per_calendar_day": known_per_day,
        "sip_benchmark_data_expense_usd_per_calendar_day": known_per_day - opex_per_day,
        "basis": (
            "the SHARED portfolio-level data opex subtracted once (never inside "
            "per-fill fees, never per strategy); the delayed alternative is not "
            "claimed to be equivalent to the SIP-timed study"
        ),
    }


def view_cell(
    daily: pl.DataFrame, trades: pl.DataFrame, view: View, days: list[str]
) -> dict:
    """Every reported number for one view on one block's calendar."""
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
        "threshold": view.threshold,
        "delay_min": view.delay_min,
        "exit_convention": view.exit_convention,
        "days_replayed": n_days,
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
        "full_loss_lower_bound_usd_per_calendar_day": {str(r): bound_per_day[r] for r in rung_keys},
        "dollars_per_year_252_on_book_3000": {
            str(r): known_per_day[r] * ANNUAL_SESSIONS for r in rung_keys
        },
        "annualization": (
            f"mean daily known contribution x {ANNUAL_SESSIONS} sessions on a "
            f"${int(BOOK)} funded book; a simple session-count convention, NOT a CAGR "
            "and NOT an account claim"
        ),
        "bootstrap_daily_known_usd": bootstrap,
        "bootstrap_daily_return_on_book": series_book,
        "iex_observability": {
            col: int(rows[col].sum()) for col in DAILY_INTENT_STATUS_COLUMNS
        },
        "skip_reasons": {reason: int(rows[reason].sum()) for reason in DAILY_REASON_COLUMNS},
        "monthly": monthly_view(rows),
        "yearly": yearly_view(rows),
    }
    trades_view = trades.filter(pl.col("view_key") == view.key) if trades.height else trades
    if trades_view.height:
        filled = trades_view.filter(pl.col("fill_status") == "conditional_fill")
        nets = {
            str(r): (filled[f"net_usd_{r}"].to_list() if filled.height else []) for r in rung_keys
        }
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
            "mean_touch_gross_0bps": (
                float(
                    np.mean(
                        [
                            net_usd(
                                int(filled["qty"][i]),
                                float(filled["exit_bid"][i]),
                                float(filled["entry_ask"][i]),
                                0.0,
                            )
                            / (int(filled["qty"][i]) * float(filled["entry_ask"][i]))
                            for i in range(filled.height)
                        ]
                    )
                )
                if filled.height
                else None
            ),
            "mean_gross_60_minute_open_basis": (
                float(filled["gross_60"].mean()) if filled.height else None
            ),
            "share_exit_status_60_observed_open_proxy": (
                float((filled["exit_status_60"] == "observed_open_proxy").mean())
                if filled.height
                else None
            ),
            "fill_status_counts": dict(
                trades_view.group_by("fill_status").len().sort("len", descending=True).iter_rows()
            ),
            "entry_status_counts": dict(
                trades_view.group_by("entry_status").len().sort("len", descending=True).iter_rows()
            ),
            "exit_submit_status_counts": dict(
                trades_view.group_by("exit_submit_status")
                .len()
                .sort("len", descending=True)
                .iter_rows()
            ),
            "mean_hold_minutes_intent_to_actual_exit": (
                float((filled["actual_exit_us"] - filled["intent_us"]).mean() / 60_000_000.0)
                if filled.height
                else None
            ),
            "mean_feed_delay_minutes": float(view.delay_min),
        }
        cell["actual_fee_profiles"] = fee_scenarios(trades_view, n_days)
    else:
        cell["trade_stats"] = {
            "funded_intents": 0,
            "conditional_fills": 0,
            "mean_net_usd_per_conditional_fill": {str(r): None for r in rung_keys},
            "worst_known_fill_usd": {str(r): None for r in rung_keys},
            "win_rate_known_fill": {str(r): None for r in rung_keys},
            "mean_touch_gross_0bps": None,
            "mean_gross_60_minute_open_basis": None,
            "share_exit_status_60_observed_open_proxy": None,
            "fill_status_counts": {},
            "entry_status_counts": {},
            "exit_submit_status_counts": {},
            "mean_hold_minutes_intent_to_actual_exit": None,
            "mean_feed_delay_minutes": float(view.delay_min),
        }
        cell["actual_fee_profiles"] = fee_scenarios(empty_trades(), n_days)
    sel_key = str(int(SELECT_COST))
    cell["income_minus_shared_data_opex_usd_per_calendar_day"] = income_minus_shared_opex(
        cell["known_usd_per_calendar_day"][sel_key]
    )
    return cell


def aggregate_block(out_dir: Path, block: str, days: list[str]) -> tuple[dict, dict]:
    """The full twelve-view surface of one block, plus the block's data coverage."""
    daily, trades = read_block_frames(out_dir, block, days)
    surface = {view.key: view_cell(daily, trades, view, days) for view in VIEWS}
    funded = int(daily["intents_funded"].sum())
    coverage = {
        "block": block,
        "days": len(days),
        "signals": int(daily["signals"].sum()),
        "intents_funded": funded,
        "attempts": int(daily["attempts"].sum()),
        "fills": int(daily["fills"].sum()),
        "known_fills": int(daily["known_fills"].sum()),
        "unknown_fills": int(daily["unknown_fills"].sum()),
        "no_match_fills": int(daily["no_match_fills"].sum()),
        "unknown_share_of_funded_intents": (
            float(daily["unknown_fills"].sum() / funded) if funded else None
        ),
        "causal_skips": {reason: int(daily[reason].sum()) for reason in DAILY_REASON_COLUMNS},
        "iex_intent_status_counts": {
            col: int(daily[col].sum()) for col in DAILY_INTENT_STATUS_COLUMNS
        },
    }
    return surface, coverage


def choose_view(surface: dict) -> tuple[View, list[dict]]:
    """Deterministic argmax of the frozen validation objective; no floors, no gates."""
    sel_key = str(int(SELECT_COST))
    ranked = sorted(
        VIEWS,
        key=lambda v: (
            -surface[v.key]["known_usd_per_calendar_day"][sel_key],
            -surface[v.key]["known_fills"],
            v.key,
        ),
    )
    ranking = [
        {
            "view_key": v.key,
            "label": v.label,
            "threshold": v.threshold,
            "delay_min": v.delay_min,
            "exit_convention": v.exit_convention,
            "validation_known_usd_per_calendar_day_at_selection_rung": surface[v.key][
                "known_usd_per_calendar_day"
            ][sel_key],
            "known_fills": surface[v.key]["known_fills"],
            "unknown_fills": surface[v.key]["unknown_fills"],
            "traded_days": surface[v.key]["traded_days"],
        }
        for v in ranked
    ]
    return ranked[0], ranking


def decision_text(chosen: View, val: dict, late: dict | None, frozen: bool) -> str:
    sel_key = str(int(SELECT_COST))
    objective = val["known_usd_per_calendar_day"][sel_key]
    fills = val["known_fills"]
    boot = val["bootstrap_daily_known_usd"][f"{int(SELECT_COST)}bps"]
    if fills == 0 or objective is None:
        return (
            "NO_EDGE_EVIDENCE: the chosen validation view has no known fill, so an empty "
            "signal set is cash, not a positive edge. No actual-touch positive "
            "formulation was measured; nothing here is promoted."
        )
    if objective <= 0:
        return (
            f"NO_ACTUAL_TOUCH_POSITIVE_FORMULATION: the best of the twelve predeclared "
            f"views ({chosen.label}) measures {objective:+.2f} $/calendar day of KNOWN "
            f"contribution on 2023 validation at {int(SELECT_COST)}bps over {fills} known "
            f"fills (day bootstrap p>0 = {boot.get('p_gt_zero')}). All twelve views are "
            "measured and none is positive; the unknown share is reported beside this "
            "number and is NOT assumed zero, and no late-block number is used to rescue "
            "or to kill this result."
        )
    text = (
        f"{STATUS}: chosen {chosen.label} (theta {chosen.threshold:.4f}, "
        f"{chosen.delay_min}-minute feed delay, exit {chosen.exit_convention}) by 2023 "
        f"validation actual-touch known contribution: {objective:+.2f} $/calendar day at "
        f"{int(SELECT_COST)}bps = "
        f"{val['dollars_per_year_252_on_book_3000'][sel_key]:+.0f} $/year on the "
        f"${int(BOOK)} funded book (simple 252-session convention, not a CAGR), "
        f"{fills} known fills on {val['traded_days']} traded days of {val['days_replayed']} "
        f"calendared days, {val['unknown_fills']} UNKNOWN executions "
        f"({100.0 * val['unknown_fills'] / max(1, val['attempts']):.1f}% of attempts) and "
        f"{val['no_match_fills']} no-match unfilled intents. Day bootstrap p>0 = "
        f"{boot.get('p_gt_zero')}. This is the measured KNOWN-contribution expectation on "
        "a PARTIAL basis (unknowns excluded from the numerator), not an account CAGR."
    )
    if frozen and late is not None:
        late_obj = late["known_usd_per_calendar_day"][sel_key]
        text += (
            f" The frozen late block measures {late_obj:+.2f} $/calendar day at "
            f"{int(SELECT_COST)}bps over {late['known_fills']} known fills of "
            f"{late['attempts']} attempts on the previously explored 2025-02..2026-05 "
            "window; the choice was frozen before any late file was read, so this is "
            "confirmation of an already-frozen decision, not a re-selection."
        )
    elif not frozen:
        text += " Late block not run (--skip-late)."
    return text


def provenance_block(
    out_dir: Path,
    days_val: list[str],
    days_late: list[str] | None,
    sip_roots: tuple[Path, ...],
    iex_roots: tuple[Path, ...],
    val_cov: dict,
    late_cov: dict | None,
) -> dict:
    sip_digest, sip_entries = supplement_digest(sip_roots)
    all_days = days_val + list(days_late or [])
    return {
        "producer_sha256": producer_sha256(),
        "producer_script": str(Path(__file__).resolve()),
        "contract_sha256": _digest_bytes(CONTRACT),
        "panel_root": str(PANEL_ROOT),
        "panel_days_root": str(PANEL_DAYS),
        "panel_contract_sha256": (sha256_file(PANEL_CONTRACT) if PANEL_CONTRACT.exists() else None),
        "sip_quote_cache_root": str(DATA_ROOT / "sip" / "net" / "quotes"),
        "sip_quote_cache_days_present": sum(
            1 for d in all_days if (DATA_ROOT / "sip" / "net" / "quotes" / f"{d}.parquet").exists()
        ),
        "sip_supplemental_roots": [str(r) for r in sip_roots],
        "sip_supplemental_files": sip_entries,
        "sip_supplement_digest": sip_digest,
        "iex_acquired_root": str(out_dir / IEX_QUOTES_DIRNAME),
        "iex_acquired_days_present": sum(
            1 for d in all_days if (out_dir / IEX_QUOTES_DIRNAME / f"{d}.parquet").exists()
        ),
        "existing_iex_cache_roots": [str(r) for r in iex_roots],
        "model": {"path": str(MODEL_PATH), "sha256": sha256_file(MODEL_PATH)},
        "validation_days": len(days_val),
        "late_days": (
            len(days_late)
            if days_late is not None
            else "not_yet_inspected: the freeze precedes any late file access"
        ),
        "coverage_validation": val_cov,
        "coverage_late": late_cov,
        "data_expense": data_expense_block(),
        "output_root": str(out_dir),
    }


# ----- run --------------------------------------------------------------------
def run(args: argparse.Namespace) -> None:
    t0 = time.time()
    out = args.out or OUTPUT
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")

    models, model_report = load_stored_model()
    print(
        "[model] immutable h60 head loaded (no refit): sha256 "
        f"{model_report['sha256'][:12]} ({model_report['n_features']} features)",
        flush=True,
    )

    iex_roots = tuple(args.iex_cache_root or [])
    sip_roots = tuple(SIP_BENCHMARK_ROOTS) + tuple(args.supplement or [])
    existing = [r for r in sip_roots if r.exists()]
    print(
        "[sip-benchmark] supplements: "
        + (", ".join(str(r) for r in existing) if existing else "none present"),
        flush=True,
    )
    if iex_roots:
        print(
            "[iex] existing cache roots: " + ", ".join(str(r) for r in iex_roots),
            flush=True,
        )
    else:
        print("[iex] existing cache roots: none (acquired raw only)", flush=True)

    (out / "contract.json").write_text(
        json.dumps(CONTRACT, indent=1, default=_json_default) + "\n"
    )
    print(
        f"[freeze-grid] contract -> {out / 'contract.json'} "
        f"({len(VIEWS)} predeclared views x {len(RUNG_COSTS)} rungs, before any outcome)",
        flush=True,
    )

    blocks = [b.strip() for b in (args.blocks or "validation,confirmation").split(",") if b.strip()]
    order = [b for b in (VAL_BLOCK, LATE_BLOCK) if b in blocks]
    if VAL_BLOCK not in order:
        raise SystemExit(
            "[delayed-sip-iex] run requires the validation block: the selection is "
            "frozen on 2023 validation before any late file is read"
        )

    surfaces: dict[str, dict] = {}
    coverages: dict[str, dict] = {}
    chosen: View | None = None
    ranking: list[dict] | None = None
    sel_key = str(int(SELECT_COST))
    for block in order:
        days = block_days(args.days, block)
        if not days:
            if args.days is None:
                raise SystemExit(
                    f"[delayed-sip-iex] requires {EXPECTED_DAYS[block]} {block} days, "
                    f"got {len(days)}"
                )
            print(
                f"[blocks] {block}: 0 of the requested days fall in this block; skipped",
                flush=True,
            )
            continue
        if args.days is None and len(days) != EXPECTED_DAYS[block]:
            raise SystemExit(
                f"[delayed-sip-iex] requires {EXPECTED_DAYS[block]} {block} days, "
                f"got {len(days)}"
            )
        print(f"[blocks] {block}={len(days)} days", flush=True)
        score_cov = score_block(out, days, args.resume, iex_roots)
        replay_cov = replay_block(out, block, days, args.resume, iex_roots, sip_roots)
        surface, cov = aggregate_block(out, block, days)
        surfaces[block] = surface
        coverages[block] = {
            "aggregate": cov,
            "score": score_cov,
            "replay": replay_cov,
        }
        for view in VIEWS:
            cell = surface[view.key]
            boot = cell["bootstrap_daily_known_usd"][f"{int(SELECT_COST)}bps"]
            print(
                f"[{block[:4]}@{int(SELECT_COST)}] {view.label:<26} "
                f"{cell['known_usd_per_calendar_day'][sel_key]:+8.2f} $/day "
                f"known fills={cell['known_fills']} unknown={cell['unknown_fills']} "
                f"nomatch={cell['no_match_fills']} attempts={cell['attempts']} "
                f"signals={cell['signals']} traded_days={cell['traded_days']} "
                f"p>0={boot.get('p_gt_zero')}",
                flush=True,
            )
        if block == VAL_BLOCK:
            chosen, ranking = choose_view(surface)
            frozen_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            freeze = {
                "frozen_before_late_file_access": True,
                "frozen_at": frozen_at,
                "study": "alpha_delayed_sip_iex",
                "status": STATUS,
                "chosen": {
                    "view_key": chosen.key,
                    "label": chosen.label,
                    "threshold": chosen.threshold,
                    "delay_min": chosen.delay_min,
                    "exit_convention": chosen.exit_convention,
                },
                "selection": {
                    "objective": (
                        "2023 validation KNOWN contribution dollars per full calendar "
                        "day at the primary residual rung (ONE predeclared scenario, "
                        "never a hurdle)"
                    ),
                    "selection_cost_bps": SELECT_COST,
                    "cost_ladder_bps": list(RUNG_COSTS),
                    "basis": (
                        "PARTIAL: unknown executions are excluded from the numerator and "
                        "reported beside it; the full-loss lower bound is an optional "
                        "guard, never the objective"
                    ),
                    "power_floors": None,
                    "median_or_tail_gates": None,
                    "cost_or_count_kill": None,
                    "late_cherrypick": None,
                    "tie_break": "(- objective, - known_fills, view_key)",
                    "all_twelve_views_reported": True,
                    "ranking": ranking,
                },
                "views": [
                    {
                        "view_key": v.key,
                        "label": v.label,
                        "threshold": v.threshold,
                        "delay_min": v.delay_min,
                        "exit_convention": v.exit_convention,
                    }
                    for v in VIEWS
                ],
                "validation_surface": surface,
                "model": model_report,
                "provenance": provenance_block(
                    out,
                    block_days(args.days, VAL_BLOCK),
                    None,
                    sip_roots,
                    iex_roots,
                    coverages[VAL_BLOCK]["aggregate"],
                    None,
                ),
            }
            write_json_atomic(out / "selection_freeze.json", freeze)
            print(
                f"[freeze] selection_freeze.json -> chosen {chosen.label} "
                f"({surface[chosen.key]['known_usd_per_calendar_day'][sel_key]:+.2f} $/day "
                f"@{int(SELECT_COST)}) AFTER validation, BEFORE any late file is read",
                flush=True,
            )
        if args.skip_late:
            break

    late_surface = surfaces.get(LATE_BLOCK)
    if VAL_BLOCK not in surfaces or chosen is None:
        raise SystemExit(
            "[delayed-sip-iex] no validation surface: the selection is frozen on the "
            "2023 validation block, so a day-restricted run must include validation days"
        )
    results = {
        "study": "alpha_delayed_sip_iex",
        "status": STATUS,
        "decision": decision_text(
            chosen,
            surfaces[VAL_BLOCK][chosen.key],
            late_surface[chosen.key] if late_surface is not None else None,
            late_surface is not None,
        ),
        "chosen": freeze_chosen(chosen),
        "frozen_choice_immutable": True,
        "contract": CONTRACT,
        "model": model_report,
        "validation": surfaces[VAL_BLOCK],
        "validation_ranking": ranking,
        "late": late_surface,
        "late_ranking": (
            [
                {
                    "view_key": v.key,
                    "label": v.label,
                    "late_known_usd_per_calendar_day_at_selection_rung": late_surface[v.key][
                        "known_usd_per_calendar_day"
                    ][sel_key],
                    "known_fills": late_surface[v.key]["known_fills"],
                    "unknown_fills": late_surface[v.key]["unknown_fills"],
                }
                for v in sorted(
                    VIEWS,
                    key=lambda v: (
                        -late_surface[v.key]["known_usd_per_calendar_day"][sel_key],
                        v.key,
                    ),
                )
            ]
            if late_surface is not None
            else None
        ),
        "chosen_late_block": (
            late_surface[chosen.key] if late_surface is not None else None
        ),
        "predeclared_twelve_view_cost_curve": {
            view.key: {
                str(int(r)): {
                    "validation_usd_per_calendar_day": (
                        surfaces[VAL_BLOCK][view.key]["known_usd_per_calendar_day"][str(int(r))]
                    ),
                    "late_usd_per_calendar_day": (
                        late_surface[view.key]["known_usd_per_calendar_day"][str(int(r))]
                        if late_surface is not None
                        else None
                    ),
                    "validation_known_fills": surfaces[VAL_BLOCK][view.key]["known_fills"],
                    "late_known_fills": (
                        late_surface[view.key]["known_fills"] if late_surface is not None else None
                    ),
                    "validation_unknown_fills": surfaces[VAL_BLOCK][view.key]["unknown_fills"],
                    "late_unknown_fills": (
                        late_surface[view.key]["unknown_fills"]
                        if late_surface is not None
                        else None
                    ),
                }
                for r in RUNG_COSTS
            }
            for view in VIEWS
        },
        "data_expense": data_expense_block(),
        "coverage": coverages,
        "provenance": provenance_block(
            out,
            block_days(args.days, VAL_BLOCK),
            block_days(args.days, LATE_BLOCK) if late_surface is not None else None,
            sip_roots,
            iex_roots,
            coverages[VAL_BLOCK]["aggregate"],
            coverages.get(LATE_BLOCK, {}).get("aggregate"),
        ),
        "artifacts": {
            "contract": str(out / "contract.json"),
            "selection_freeze": str(out / "selection_freeze.json"),
            "producer_snapshot": str(out / "producer_snapshot.py"),
            "manifests_root": str(out / "manifest"),
            "iex_quotes_root": str(out / IEX_QUOTES_DIRNAME),
            "states_root": str(out / "states"),
            "replay_root": str(out / "replay"),
            "results": str(out / "results.json"),
        },
        "runtime_s": round(time.time() - t0, 1),
    }
    write_json_atomic(out / "results.json", results)
    print(f"[decision] {results['decision']}", flush=True)
    print(
        f"[done] results -> {out / 'results.json'} ({results['runtime_s']}s)",
        flush=True,
    )


def freeze_chosen(chosen: View | None) -> dict | None:
    if chosen is None:
        return None
    return {
        "view_key": chosen.key,
        "label": chosen.label,
        "threshold": chosen.threshold,
        "delay_min": chosen.delay_min,
        "exit_convention": chosen.exit_convention,
    }


def plan_command(args: argparse.Namespace) -> None:
    t0 = time.time()
    out = args.out or OUTPUT
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")
    models, _ = load_stored_model()
    blocks = [b.strip() for b in (args.blocks or "validation,confirmation").split(",") if b.strip()]
    order = [b for b in (VAL_BLOCK, LATE_BLOCK) if b in blocks]
    if not order:
        raise SystemExit("[delayed-sip-iex] plan requires validation and/or confirmation")
    days: list[str] = []
    for block in order:
        days.extend(block_days(args.days, block))
    guard_days(days)
    print(
        f"[delayed-sip-iex] plan days={len(days)} blocks={','.join(order)} "
        "(metadata only: no quote file, no credential, no network)",
        flush=True,
    )
    summary = plan_block(out, models, days, args.resume)
    summary.update(
        {
            "study": "alpha_delayed_sip_iex",
            "stage": "plan",
            "status": STATUS,
            "blocks": order,
            "views": len(VIEWS),
            "requests_planned": summary["candidate_day_ticker_pairs"],
            "producer_sha256": producer_sha256(),
            "runtime_s": round(time.time() - t0, 1),
        }
    )
    write_json_atomic(out / "plan_summary.json", summary)
    print(
        f"[plan-only] days={summary['days_total']} signals={summary['signals']} "
        f"day-ticker requests={summary['candidate_day_ticker_pairs']} -> "
        f"{out / 'plan_summary.json'}",
        flush=True,
    )


def acquire_command(args: argparse.Namespace) -> None:
    t0 = time.time()
    out = args.out or OUTPUT
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")
    blocks = [b.strip() for b in (args.blocks or "validation,confirmation").split(",") if b.strip()]
    order = [b for b in (VAL_BLOCK, LATE_BLOCK) if b in blocks]
    if not order:
        raise SystemExit("[delayed-sip-iex] acquire requires validation and/or confirmation")
    days: list[str] = []
    for block in order:
        days.extend(block_days(args.days, block))
    guard_days(days)
    iex_roots = tuple(args.iex_cache_root or [])
    print(
        f"[delayed-sip-iex] acquire days={len(days)} blocks={','.join(order)} "
        f"feed=iex asof=<day> req_sleep={args.req_sleep}s existing_iex_roots={len(iex_roots)}",
        flush=True,
    )
    summary = acquire_block(out, days, args.resume, args.req_sleep, args.env_path, iex_roots)
    summary["runtime_s"] = round(time.time() - t0, 1)
    write_json_atomic(out / "acquire_summary.json", summary)
    print(
        f"[done] {summary['runtime_s']}s -> {out / 'acquire_summary.json'} "
        f"(iex quotes root: {out / IEX_QUOTES_DIRNAME})",
        flush=True,
    )


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--out", type=Path, default=None, help=f"output root (default: {OUTPUT})")
        sp.add_argument(
            "--resume", action="store_true", help="resume from the per-day artifacts on disk"
        )
        sp.add_argument(
            "--days",
            nargs="+",
            default=None,
            help="restrict the calendar to these ET days (smoke/debug only)",
        )
        sp.add_argument(
            "--blocks",
            type=str,
            default=None,
            help="comma subset of {validation,confirmation} (default: both)",
        )

    sp = sub.add_parser("plan", help="metadata only: manifests + IEX request windows")
    common(sp)
    sp.set_defaults(func=plan_command)

    sp = sub.add_parser("acquire", help="read-only IEX quote acquisition (parent runs it)")
    common(sp)
    sp.add_argument(
        "--req-sleep",
        type=float,
        default=DEFAULT_REQ_SLEEP_S,
        help=f"seconds between requests (default {DEFAULT_REQ_SLEEP_S:.2f}s ~ "
        f"{60.0 / DEFAULT_REQ_SLEEP_S:.0f} req/min, below the historical Basic 200/min)",
    )
    sp.add_argument(
        "--env-path",
        type=Path,
        default=Path(__file__).resolve().parents[2] / ".env",
        help="repo root .env holding the read-only market-data credentials",
    )
    sp.add_argument(
        "--iex-cache-root",
        type=Path,
        action="append",
        default=None,
        help="existing IEX quote cache root (<root>/<day>.parquet); a FITTING day file "
        "counts as PRESENT (no refetch) and merges after the acquired raw prints",
    )
    sp.set_defaults(func=acquire_command)

    sp = sub.add_parser(
        "run", help="score -> replay -> report: validation first, freeze, then late"
    )
    common(sp)
    sp.add_argument(
        "--skip-late",
        action="store_true",
        help="stop after the frozen selection (validation block only)",
    )
    sp.add_argument(
        "--supplement",
        type=Path,
        action="append",
        default=None,
        help="extra SIP quote supplement root for the execution benchmark (read-only)",
    )
    sp.add_argument(
        "--iex-cache-root",
        type=Path,
        action="append",
        default=None,
        help="existing IEX quote cache root to merge after this run's acquired prints",
    )
    sp.set_defaults(func=run)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
