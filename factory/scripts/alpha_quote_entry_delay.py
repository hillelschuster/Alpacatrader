#!/usr/bin/env python3
"""Quote-aware ENTRY-DELAY study on the open-anchored top-gainer panel (no refit).

QUESTION. The retained lead in this lane is the frozen learned h60 minute-open model read at
a score bar and replayed under an actual-touch execution (see the shared repaired engine
``alpha_quote_aware_frequency.py``). The separate minute-open-PROXY producer
``alpha_retained_entry_delay`` moved exactly one axis - the EXECUTION DELAY of the entry - and
found that delaying the entry by 1-4 minutes after the signal minute ``t`` beats the immediate
entry on BOTH blocks at 25 and 100 bps (validation repeat: d2 +8.73 $/day vs d0 +6.24; late:
d2 +12.29 $/day @25bps and +8.69 @100bps vs d0 +8.89 / +5.29), with the effect a plateau
(d1, d2, d4 all better; d8 decays). Those fills were MINUTE-OPEN PROXY prices recomputed from
the day's bars, NOT actual quotes. This producer re-tests that ONE axis on the REAL quote lane
of the repaired engine, where every fill is an OBSERVED NBBO ASK entry / BID exit touch with
honest UNKNOWNs, and it reports the per-view UNKNOWN/no-match funnel beside every delta so a
delay effect cannot be an artifact of a smaller or differently-composed fill set.

Nothing about the model or the execution semantics moves. The grid is PRE-DECLARED: 32 fixed
views = entry delay {0, 1, 2, 4} minutes x heads {15, 60} x score bars
{0.005, 0.010, 0.020, 0.030}. The 0.030 bar is the frozen RETAINED study's own score bar: the
first run of this producer measured the delay axis only at the three lower bars, and the
decision-relevant retained signal was missing, so the grid now carries all four. A score bar
is a bar on the model's predicted payoff - NEVER a fee - and every view label states the
threshold as a bare decimal (``d2min_h60_thr0.0300``) so a score bar can never be read as a
cost rung. Only the CLOCKS move with the delay:

* ENTRY intent clock becomes ``clock_us(day, t+1+d, 0)`` for delay ``d``; ``d = 0`` is the
  engine's own next-minute intent (the signal minute ``t`` plus one minute of reaction time),
  so the d=0 column is the control view on the IDENTICAL basis.
* the EXIT due minute becomes ``min(entry_minute + head, session_end)`` with
  ``entry_minute = t + 1 + d``: the head's hold window starts at the DELAYED entry minute and
  is still capped at the panel's own RTH session end.
* every other rule is the repaired engine's, imported and never re-implemented: the marketable
  IOC-limit entry observed at the intent clock (limit = observed ASK x 1.01 rounded UP to the
  cent, q = floor(250 / (limit x (1 + 150bps/2))) fixed BEFORE the arrival print, q <= the
  displayed ASK depth, spread + 25bps <= 10000 x predicted gross), one arrival observation at
  intent + 250ms that can never rest, the fresh-quote-gated MARKET exit priced at the latest
  raw BID with its REAL priced clock, the funded $750 book (3 slots x $250), one position per
  ticker, a flat 5-minute cooldown after the ACTUAL exit, at most 5 attempts per ticker per
  session, all intents of one clock funded (highest score first) BEFORE any arrival outcome,
  a $0-quantity as a KNOWN NO ORDER cash skip, an UNKNOWN holding cash + slot to the session
  end, and KEEP-FIRST supplements that may only ADD timestamps.

MODEL: the ONE immutable stored path ``alpha_open_learned.feature_matrix`` + the two stored
heads h15/h60 under ``learned/models`` (loaded through the engine's ``load_stored_models``,
which verifies the stored feature order and horizons). No refit, no HPO, no new features, no
ticker or date identifiers, no label features; the 2021-02..2022-12 fit block is never
replayed. The panel's stored minute-open proxy labels ride along for an explicit basis
comparison only and are NEVER an entry filter.

SELECTION: the entire 32-view grid and the 0/5/10/25/50/75/100/125/150 bps rung ladder are
frozen to ``contract.json`` BEFORE any validation day is scored. The single view is then chosen
ONLY by 2023 validation actual-touch KNOWN-contribution dollars per full calendar day at the
25 bps rung, on an explicit PARTIAL basis (unknown executions are excluded from the numerator
and reported beside it; the separately labeled full-loss lower bound is a guard, never the
objective), tie-broken deterministically by known fills, then fewer attempts, then view key.
No count, median, tail, CI or cost floor is applied anywhere: 25 bps is ONE pre-declared
comparison scenario among the reported rungs, never a hurdle, and no rung - 100 bps included -
closes a candidate. ``selection_freeze.json`` is written AFTER the validation surface and
BEFORE any late file is read; the 332 late days are then traversed with the choice immutable.

COSTS are a COMPARISON SET, never a hurdle and never a fee claim. Each rung is an EXTRA
residual fee/slippage scenario charged ON TOP of the ACTUAL observed ASK entry / BID exit
touch prices - the spread is already inside those prices, so it is never double-charged. A
primary US broker's regular schedule (zero commission, SEC 20.6 per $1M, CAT 0.000003 per
side, daily cent rounding) is typically well under 1 bp on a $250 ticket, so real provider
fees CAN be far below the low rungs: the rungs describe a LOW-FEE OPPORTUNITY set, actual
provider fees are sourced in the separate provider-fee ledger, and the 25 bps selection rung
is never an enforced minimum.

REPORTED per view and per rung over each block's whole calendar: signals, funded intents,
attempts, fills, known fills, UNKNOWN fills, no-match unfilled cash, traded days of the total,
known-contribution $/calendar day, the simple 252-session $/year on the $750 nominal book,
mean net $ per known fill, win rate, profit factor, worst known fill, positive months, the
skip-reason ledger, the fill-status / entry-status / exit-submit-status composition, a day
bootstrap, the separately labeled full-loss lower bound over UNKNOWNs, and the direct delta
against the SAME (head, threshold) d=0 control view at every rung.

Outputs -> ``~/alpha-data/open-search-v1/quote_entry_delay``: ``contract.json`` (grid + rung
ladder, before any outcome), ``selection_freeze.json`` (the frozen choice after validation and
before any late read), ``results.json`` (the full surface plus the delay axis), ``states/``
and ``replay/<block>/`` per-day atomic checkpoints with resume hashes, and
``producer_snapshot.py``. Protected days (2024, 2025-01, 2026-06..08) are never read; every day
passes ``allowed()`` before any file is opened; one day is resident at a time.

This is a research replay of a historical conditional L1 IOC model with a shifted intent
clock. No live order, no broker call, no account write.

Usage:
  uv run --no-sync python factory/scripts/alpha_quote_entry_delay.py
      # 250 validation days (frozen), then 332 late days
      # -> ~/alpha-data/open-search-v1/quote_entry_delay
  uv run --no-sync python factory/scripts/alpha_quote_entry_delay.py --resume
      # resume from the per-day state / daily / trade parts already on disk
  uv run --no-sync python factory/scripts/alpha_quote_entry_delay.py --skip-late
      # validation + frozen selection only
  uv run --no-sync python factory/scripts/alpha_quote_entry_delay.py --out <dir>
      # relocate the lane root
  uv run --no-sync python factory/scripts/alpha_quote_entry_delay.py --days 2023-05-15
      # smoke subset (debug only)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The repaired quote-aware engine is the authoritative causal execution lane: it is IMPORTED
# and never edited, never vendored and never re-implemented. Every clock, sizing, gate and
# leg helper this producer uses is the engine's own published helper.
import alpha_quote_aware_frequency as engine  # noqa: E402
from alpha_open_learned import FEATURES_ALL, PANEL_ROOT, feature_matrix  # noqa: E402
from alpha_open_panel import allowed  # noqa: E402
from alpha_open_sim import causal_liquidity  # noqa: E402
from alpha_quote_aware_frequency import (  # noqa: E402
    ANNUAL_SESSIONS,
    BOOK,
    COOLDOWN_MIN,
    DATA_ROOT,
    ENTRY_OVERHEAD_BPS,
    HEADS,
    LATE_BLOCK,
    LATENCY_US,
    LIMIT_CAP,
    MAX_AGE_S,
    MAX_ATTEMPTS,
    MAX_RUNG_COST,
    MAX_SLOTS,
    MODEL_DIR,
    ORDER_BUDGET,
    PANEL_CONTRACT,
    PANEL_DAYS,
    QUOTE_CACHE_ROOT,
    RUNG_COSTS,
    SELECT_COST,
    SUPPLEMENT_ROOTS,
    VAL_BLOCK,
    SymbolQuotes,
    arrival_us,
    block_days,
    causal_quantity,
    entry_price_leg,
    entry_rule_status,
    intent_facts,
    limit_price_of,
    load_stored_models,
    minute_us,
    priced_leg,
    quote_day_frame,
    reserved_usd,
    sha256_file,
    submission_clock,
    supplement_digest,
    symbol_quotes,
    write_json_atomic,
)
from alpha_quote_aware_frequency import (
    producer_sha256 as engine_producer_sha256,
)
from alpha_sparse_daily import day_context  # noqa: E402

# ----- fixed configuration (no HPO, no refit, no grid search). -----------------
STUDY = "alpha_quote_entry_delay"
STATUS = "DISCOVERY-NOT-VALIDATED"
VERSION = 1
PROGRESS_EVERY = 25  # per-day progress cadence (days)
STATES_SCHEMA = 1  # per-day scored-states schema (delay-aware)
REPLAY_SCHEMA = 1  # per-day replay parts schema (one daily row per pre-declared view)
PROTECTED_UNREAD = ["2024", "2025-01", "2026-06", "2026-07", "2026-08"]
DELAYS = (0, 1, 2, 4)  # minutes added to the engine's own t+1 intent; d=0 IS the control
# Four fixed bars on the model's OWN predicted payoff - a bar is a bar on the prediction,
# never a re-fit knob and never a fee. 0.030 is deliberately included: it is the frozen
# RETAINED study's own score bar, so this quote lane's delay axis is also measured at the
# retained signal, not only at lower and noisier bars. A threshold and a cost rung never
# share a unit in any label, key or field (see view_label / LABEL_NOTE below).
THRESHOLDS = (0.005, 0.010, 0.020, 0.030)
LABEL_NOTE = (
    "a view LABEL states the threshold as a bare decimal (d2min_h60_thr0.0300) and a cost "
    "RUNG is always a bps figure, so the two can never be confused: the retained theta "
    "0.030 is a SCORE bar, never a 30 bps cost"
)
EXPECTED_DAYS = {VAL_BLOCK: 250, LATE_BLOCK: 332}
OUTPUT = PANEL_ROOT / "quote_entry_delay"
# Quote supplements, ranked: the engine's own fetch caches first, then the ALL-PIT
# missing-symbol acquisition cache (still being harvested by the parent). Originals always
# KEEP FIRST - a supplement may only ADD timestamps, never replace or revise a print - and a
# stream that is still absent afterwards is an acquisition UNKNOWN, never known cash.
QUOTE_SUPPLEMENT_ROOTS = tuple(SUPPLEMENT_ROOTS) + (
    PANEL_ROOT / "quote_universe_acquire" / "quotes",
)
COST_NATURE = (
    "every rung is an EXTRA residual fee/slippage scenario charged ON TOP of the ACTUAL "
    "observed ASK entry / BID exit touch prices, NOT total modeled friction and NOT an "
    "actual broker fee; the touch prices already carry the spread, so each rung charges "
    "only the extra residual and the spread is never double-charged"
)
LOW_FEE_LABEL = (
    "0..150 bps describe a LOW-FEE OPPORTUNITY set, not minimum real provider fees: a "
    "primary US broker's regular schedule (zero commission, SEC 20.6 per $1M, CAT "
    "0.000003 per side, daily cent rounding) is typically well under 1 bp on a "
    "$250-$1,000 ticket, so the 25 bps primary rung is ONE pre-declared comparison "
    "scenario, never an enforced minimum, and NO rung (100 bps included) vetoes a "
    "candidate; actual provider fees are sourced in the separate provider-fee ledger, "
    "never asserted here"
)
SIZING_RULE = (
    "q = floor(250 / (limit_price * (1 + 150bps/2))) from the INTENT limit price (never "
    "the arrival price), so the same causal q is funded at every reported rung and the "
    "worst-case reservation never exceeds the $250 ticket"
)
RESERVATION_RULE = (
    "reserved = q * limit * (1 + rung/2) with q sized at the highest rung, so every "
    "reported rung stays inside the $250 ticket"
)
DELAY_AXIS_RULE = (
    "delay d shifts ONLY the intent clock to clock_us(day, t+1+d, 0) and therefore the "
    "exit due minute to min(t+1+d+head, session_end); the signal set, the model, the "
    "frozen scoring path and every execution rule are unchanged, so each delayed view is "
    "compared against the SAME (head, threshold) d=0 control view on the identical basis"
)
FUNNEL_RULE = (
    "signals -> funded intents -> known fills / UNKNOWN fills / no-match unfilled cash "
    "are reported per view beside every delay delta, so a delay effect cannot be an "
    "artifact of a smaller or differently-composed fill set"
)
SELECTION_RULE = {
    "objective": (
        "2023 validation actual-touch known contribution dollars per full calendar day "
        "at the 25 bps residual rung (ONE pre-declared scenario, never a hurdle)"
    ),
    "selection_cost_bps": SELECT_COST,
    "cost_ladder_bps": list(RUNG_COSTS),
    "basis": (
        "PARTIAL: unknown executions are excluded from the numerator and reported beside "
        "it; the full-loss lower bound is an optional guard, never the objective"
    ),
    "power_floors": None,
    "median_or_tail_gates": None,
    "cost_or_count_kill": None,
    "tie_break": [
        "known_usd_per_calendar_day at the selection rung desc",
        "known_fills desc",
        "attempts asc",
        "view_key asc",
    ],
    "frozen_before_late_inspection": True,
    "freeze_artifact": (
        "selection_freeze.json (written after the validation surface, before any late file is read)"
    ),
}


# ----- the pre-declared grid: delay x head x threshold -------------------------
def base_key(head: int, threshold: float) -> str:
    """The (head, threshold) cell key: the SAME key identifies a delay's control view."""
    return f"h{int(head)}|{float(threshold):.4f}"


def view_key(delay_min: int, head: int, threshold: float) -> str:
    """Stable machine key (already unambiguous: the threshold is a bare decimal)."""
    return f"d{int(delay_min)}|{base_key(head, threshold)}"


def view_label(delay_min: int, head: int, threshold: float) -> str:
    """An unambiguous human label: the score bar is a bare DECIMAL, never a bps figure.

    The earlier spelling turned a 0.005 threshold into "thr50bps", which reads exactly like
    the 50 bps COST rung and could hide the retained theta 0.030 inside a cost ladder. A
    label now states the score bar as it is (``d2min_h60_thr0.0300``); the cost ladder is
    always reported in bps and never inside a view label. See LABEL_NOTE.
    """
    return f"d{int(delay_min)}min_h{int(head)}_thr{float(threshold):.4f}"


@dataclass(frozen=True)
class View:
    """One (delay, head, threshold) cell. Same model, same signals, same execution rules.

    The engine's own ``View`` carries (head, threshold); this study's cell adds the entry
    delay and is passed straight to the engine's published ``replay_day`` / ``resolve_trade``
    / ``view_cell`` helpers, which read only ``head``, ``threshold`` and ``key``.
    """

    delay_min: int
    head: int
    threshold: float

    @property
    def key(self) -> str:
        return view_key(self.delay_min, self.head, self.threshold)

    @property
    def label(self) -> str:
        return view_label(self.delay_min, self.head, self.threshold)

    @property
    def control_key(self) -> str:
        return view_key(0, self.head, self.threshold)


VIEWS = tuple(View(d, h, thr) for d in DELAYS for h in HEADS for thr in THRESHOLDS)

CONTRACT = {
    "version": VERSION,
    "study": STUDY,
    "status": STATUS,
    "hypothesis": (
        "the same two immutable learned payoff heads (h15/h60), scored at four fixed bars "
        "and traded with the repaired engine's fresh-quote-gated marketable-IOC-limit entry "
        "and fresh-quote-gated market exit, earn more known contribution per calendar day "
        "when the ENTRY intent clock is delayed 1-4 minutes past the engine's own t+1 intent "
        "- the delay axis the minute-open-proxy producer measured on bars, re-measured here "
        "on actual NBBO touches with honest UNKNOWNs"
    ),
    "shared_engine": {
        "module": "alpha_quote_aware_frequency",
        "role": (
            "the authoritative causal execution lane: IMPORTED, never edited; every clock, "
            "sizing, gate, leg and aggregation helper used here is the engine's own "
            "published helper"
        ),
        "producer_sha256": engine_producer_sha256(),
        "reused_helpers": [
            "quote_day_frame",
            "symbol_quotes",
            "SymbolQuotes",
            "limit_price_of",
            "causal_quantity",
            "reserved_usd",
            "intent_facts",
            "entry_rule_status",
            "entry_price_leg",
            "priced_leg",
            "submission_clock",
            "minute_us",
            "arrival_us",
            "load_stored_models",
            "block_days",
            "replay_day",
            "resolve_trade",
            "view_cell",
            "supplement_digest",
        ],
        "book_constants": [
            "BOOK",
            "MAX_SLOTS",
            "ORDER_BUDGET",
            "COOLDOWN_MIN",
            "MAX_ATTEMPTS",
            "RUNG_COSTS",
            "SELECT_COST",
            "ANNUAL_SESSIONS",
            "LIMIT_CAP",
            "LATENCY_US",
            "MAX_AGE_S",
            "ENTRY_OVERHEAD_BPS",
        ],
    },
    "periods": {
        VAL_BLOCK: "2023-01-01..2023-12-31 (all 250 allowed panel days; the ONLY selection block)",
        LATE_BLOCK: (
            "2025-02-01..2026-05-31 (all 332 allowed panel days, previously explored, NOT "
            "pristine: traversal for transparency only, the frozen choice is immutable)"
        ),
    },
    "protected_unread": PROTECTED_UNREAD,
    "model": {
        "refit": False,
        "hpo": False,
        "feature_changes": False,
        "new_features": False,
        "ticker_or_date_features": False,
        "loaded_from": str(MODEL_DIR),
        "loader": "alpha_quote_aware_frequency.load_stored_models (verifies order and horizon)",
        "heads": list(HEADS),
        "feature_order": list(FEATURES_ALL),
        "feature_matrix_helper": "alpha_open_learned.feature_matrix (Float64, null-filled)",
        "fit_block": "2021-02..2022-12 (never replayed, never re-scored as outcomes)",
        "target": "panel minute-open gross payoff labels gross_15 / gross_60",
        "clip_fit": [-0.5, 2.0],
    },
    "views": {
        "grid": (
            f"{len(DELAYS)} entry delays x {len(HEADS)} heads x {len(THRESHOLDS)} score bars "
            f"= {len(VIEWS)} pre-declared views"
        ),
        "entry_delays_min": list(DELAYS),
        "heads": list(HEADS),
        "thresholds": list(THRESHOLDS),
        "threshold_nature": (
            "a bar on the model's own predicted gross payoff, never a re-fit knob and never "
            "a fee; 0.030 is the frozen RETAINED study's own score bar, included so this "
            "quote lane's delay axis is also measured at the retained signal"
        ),
        "label_convention": LABEL_NOTE,
        "delay_axis_rule": DELAY_AXIS_RULE,
        "funnel_rule": FUNNEL_RULE,
        "control": "d=0 is the engine's own t+1 intent clock: the direct control view",
        "views": [
            {
                "view_key": v.key,
                "label": v.label,
                "delay_min": v.delay_min,
                "head": v.head,
                "threshold": v.threshold,
                "control_view_key": v.control_key,
            }
            for v in VIEWS
        ],
    },
    "entry": {
        "intent_clock": (
            "clock_us(day, t+1+d, 0) with d in {0,1,2,4}; d=0 reproduces the engine's own "
            "t+1 intent"
        ),
        "arrival_clock": "intent + 250ms (IOC: one observation, never rests)",
        "observation": "the latest RAW NBBO state at the intent clock (never pre-filtered)",
        "firm_fresh_rule": "valid, un-crossed, R-only, age <= 2s",
        "limit_price": (
            "ceil_to_cent(observed_ask * 1.01): a PRE-DECLARED marketability cap, NOT "
            "charged friction"
        ),
        "quantity": SIZING_RULE,
        "depth_rule": "q >= 1 and q <= the INTENT top-of-book ASK depth (known data only)",
        "spread_rule": "spread_bps + 25 <= 10000 * predicted_gross",
        "spread_overhead_bps": ENTRY_OVERHEAD_BPS,
        "arrival_latency_us": LATENCY_US,
        "max_age_s": MAX_AGE_S,
        "limit_cap": LIMIT_CAP,
        "arrival_rule": (
            "ONE observation of the latest RAW state at the arrival clock: ask <= limit and "
            "arrival ASK depth >= q -> conditional full-touch IOC-model fill at the ACTUAL "
            "ask; ask > limit -> NO_MATCH_AT_L1 unfilled cash; partial depth / non-acquired "
            "stream / stale-but-valid / invalid / non-regular / absent book -> execution "
            "UNKNOWN, because an IOC LIMIT CANNOT REST and no later print is ever used to "
            "fill it"
        ),
        "no_order_rule": (
            "q == 0 is a KNOWN no-order (no order sent, cash unfilled, never a priced "
            "zero-return fill, never an UNKNOWN)"
        ),
    },
    "exit": {
        "hold": (
            "min(entry_minute + head, session_end) with entry_minute = t+1+d: the head's "
            "hold starts at the DELAYED entry minute, still capped at the RTH close"
        ),
        "due_intent_clock": "clock_us(day, min(entry_minute + head, session_end), 0)",
        "submission": (
            "FRESH-QUOTE-GATED: submit at the due intent if the latest raw state is firm "
            "and fresh, else hold to the FIRST eligible regular print at/after the due "
            "intent inside RTH and submit at that print's own timestamp"
        ),
        "arrival_clock": "actual submission + 250ms",
        "pricing": (
            "latest raw state at the arrival clock; stale-but-valid -> UNKNOWN; invalid / "
            "non-regular / absent book -> the submitted MARKET order conditionally rests to "
            "the first eligible print at/after arrival; the REAL execution clock is that "
            "priced print's own timestamp, so the position, its reserved cash and the "
            "ticker cooldown are held until then"
        ),
        "quantity": "the causal entry q, never re-sized",
        "depth_rule": (
            "exit BID top-of-book < q -> UNKNOWN partial capacity, never a guaranteed full fill"
        ),
    },
    "book": {
        "slots": MAX_SLOTS,
        "ticket_usd": ORDER_BUDGET,
        "book_usd": BOOK,
        "leverage": False,
        "one_position_per_ticker": True,
        "cooldown": "flat 5 minutes after the ACTUAL exit clock",
        "max_attempts_per_ticker_per_day": MAX_ATTEMPTS,
        "cash_reservation": RESERVATION_RULE,
        "annualization": (
            f"mean daily known contribution x {ANNUAL_SESSIONS} sessions on a "
            f"${int(BOOK)} nominal book: a simple session-count convention, NOT a CAGR "
            "and NOT an account claim"
        ),
        "simultaneous_clock_rule": (
            "highest-score eligible intents are funded (up to the slot limit) BEFORE any "
            "arrival outcome is observed; no same-clock substitution after an unfilled intent"
        ),
        "unknown_rule": (
            "an UNKNOWN position keeps its slot and blocks its ticker until it resolves or "
            "the session ends; cash is never freed at a planned time"
        ),
        "later_clock_rule": "a later signal may use cash released by an earlier ACTUAL exit",
    },
    "selection": SELECTION_RULE,
    "costs": {
        "rung_bps": list(RUNG_COSTS),
        "primary_selection_rung_bps": SELECT_COST,
        "selection_rung_is_one_scenario_not_a_hurdle": True,
        "cost_or_count_kill": None,
        "scenario_nature": COST_NATURE,
        "low_fee_opportunity_label": LOW_FEE_LABEL,
        "quantity_sizing_rung_bps": MAX_RUNG_COST,
        "reservation_rule": RESERVATION_RULE,
    },
    "prior_data": {
        "proxy_producer_finding": (
            "alpha_retained_entry_delay measured delayed entries on minute-open PROXY bars "
            "(not actual quotes): validation repeat d2 +8.73 $/day @25bps vs d0 +6.24, late "
            "d2 +12.29 @25bps and +8.69 @100bps vs d0 +8.89 / +5.29, a d1-d4 plateau that "
            "decays by d8; this study re-tests the SAME axis on actual NBBO touches and the "
            "proxy numbers are the prior being tested, never evidence about the touch lane"
        ),
        "validation_block": (
            "2023-01-01..2023-12-31: explored before this replay; not pristine, not "
            "previously unknown"
        ),
        "late_block": (
            "2025-02-01..2026-05-31: previously explored BEFORE this replay; NOT pristine "
            "and NOT previously unknown, so every late number is DISCOVERY-NOT-VALIDATED"
        ),
    },
    "labels": {
        "traded_basis": "real NBBO ASK entry -> BID exit at the declared clocks",
        "model_basis": "minute-open payoff proxy (t -> t+h actual minute opens)",
        "proxy_labels_used_as_entry_filter": False,
        "unfilled_entry_status_used_as_entry_filter": False,
    },
    "supplements": {
        "merge_order": ["ranked SIP day cache"] + [str(r) for r in QUOTE_SUPPLEMENT_ROOTS],
        "dedup_rule": (
            "unique (symbol, ts_utc) keep=first in that order: the ORIGINAL print always "
            "wins, a supplement may ONLY add timestamps and never silently replaces or "
            "revises an original print"
        ),
        "roots": [str(r) for r in QUOTE_SUPPLEMENT_ROOTS],
        "roots_existing": [str(r) for r in QUOTE_SUPPLEMENT_ROOTS if r.exists()],
        "quote_age_rule": (
            "age > 2s is an operational freshness gate for THIS policy, never a claim that "
            "an NBBO expired or that a submitted order was cancelled"
        ),
    },
    "no_live_orders": True,
    "schema": {"states": STATES_SCHEMA, "replay": REPLAY_SCHEMA},
    "cli": (
        "uv run --no-sync python factory/scripts/alpha_quote_entry_delay.py "
        "[--out DIR] [--resume] [--skip-late] [--days ...]"
    ),
}


# ----- shared helpers ---------------------------------------------------------
ENGINE_MODULE = "alpha_quote_aware_frequency"
# The engine's published execution surface: every helper this lane uses is IMPORTED from the
# shared engine and never re-implemented (the replay legs, clocks, sizing, gates and per-view
# aggregation are the engine's own code paths).
ENGINE_HELPERS = (
    ("quote_day_frame", quote_day_frame),
    ("symbol_quotes", symbol_quotes),
    ("SymbolQuotes", SymbolQuotes),
    ("limit_price_of", limit_price_of),
    ("causal_quantity", causal_quantity),
    ("reserved_usd", reserved_usd),
    ("entry_price_leg", entry_price_leg),
    ("priced_leg", priced_leg),
    ("submission_clock", submission_clock),
    ("minute_us", minute_us),
    ("arrival_us", arrival_us),
    ("load_stored_models", load_stored_models),
)
# The engine's book constants this study's contract copies verbatim.
ENGINE_CONSTANTS = {
    "BOOK": BOOK,
    "MAX_SLOTS": MAX_SLOTS,
    "ORDER_BUDGET": ORDER_BUDGET,
    "COOLDOWN_MIN": COOLDOWN_MIN,
    "MAX_ATTEMPTS": MAX_ATTEMPTS,
    "SELECT_COST": SELECT_COST,
    "MAX_RUNG_COST": MAX_RUNG_COST,
    "ANNUAL_SESSIONS": ANNUAL_SESSIONS,
    "LATENCY_US": LATENCY_US,
    "MAX_AGE_S": MAX_AGE_S,
    "LIMIT_CAP": LIMIT_CAP,
    "ENTRY_OVERHEAD_BPS": ENTRY_OVERHEAD_BPS,
}


def verify_engine_contract() -> None:
    """Pin the shared engine's published surface this lane's contract text claims.

    The helpers must really come from ``alpha_quote_aware_frequency`` (a same-named local
    shim would silently make this a different study) and the book constants must still be
    the engine's own. If the shared lane ever moves one of them this producer fails loudly
    at startup instead of quietly reporting a differently-executed study under the same
    contract.
    """
    for name, fn in ENGINE_HELPERS:
        owner = getattr(fn, "__module__", None)
        if owner != ENGINE_MODULE:
            raise SystemExit(f"[{STUDY}] helper {name} does not come from {ENGINE_MODULE}: {owner}")
    expected = {
        "BOOK": 750.0,
        "MAX_SLOTS": 3.0,
        "ORDER_BUDGET": 250.0,
        "COOLDOWN_MIN": 5.0,
        "MAX_ATTEMPTS": 5.0,
        "SELECT_COST": 25.0,
        "MAX_RUNG_COST": 150.0,
        "ANNUAL_SESSIONS": 252.0,
        "LATENCY_US": 250_000.0,
        "MAX_AGE_S": 2.0,
        "LIMIT_CAP": 0.01,
        "ENTRY_OVERHEAD_BPS": 25.0,
    }
    for name, value in ENGINE_CONSTANTS.items():
        if float(value) != float(expected[name]):
            raise SystemExit(
                f"[{STUDY}] engine constant {name} = {value}, expected {expected[name]}; "
                "the shared execution lane moved under this study's contract"
            )
    if list(RUNG_COSTS) != [0.0, 5.0, 10.0, 25.0, 50.0, 75.0, 100.0, 125.0, 150.0]:
        raise SystemExit(
            f"[{STUDY}] engine rung ladder moved: {list(RUNG_COSTS)}; the contract's "
            "pre-declared ladder no longer matches the shared lane"
        )
    if tuple(HEADS) != (15, 60):
        raise SystemExit(f"[{STUDY}] engine heads moved: {tuple(HEADS)}")


def producer_sha256() -> str:
    """This producer's own content hash (never the engine's)."""
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


def _fmt(value, spec: str = "+.2f") -> str:
    """Format a possibly-absent statistic (a view with no known fill) without crashing."""
    return "n/a" if value is None else format(value, spec)


def _fmt_mult(value) -> str:
    """Format a possibly-absent ratio with its multiplier suffix (never "n/ax")."""
    return "n/a" if value is None else f"{float(value):.3f}x"


def guard_days(days: list[str]) -> None:
    """Refuse any day outside the allowed window BEFORE a single file is opened."""
    for day in days:
        if not allowed(day):
            raise SystemExit(f"[{STUDY}] refused day outside the allowed window: {day}")
        if any(day.startswith(prefix) for prefix in PROTECTED_UNREAD):
            raise SystemExit(f"[{STUDY}] refused protected day: {day}")


def _check_intent_sizing(rec: dict) -> None:
    """Pin the engine's published sizing contract on this study's own scored state.

    ``intent_facts`` derives the causal quantity from the INTENT quote (never from the
    arrival price): ``limit_price_of(ask)`` -> ``causal_quantity(limit)`` at the 150bps
    sizing rung -> ``reserved_usd(qty, limit)``, so the same causal q is funded at every
    reported rung. If the shared engine ever changed that derivation this lane fails loudly
    instead of quietly reporting a differently-sized study under the same contract.
    """
    ask = rec["intent_ask"]
    if ask is None:
        empty = (
            rec["limit_price"] is None
            and int(rec["qty"]) == 0
            and float(rec["reserved_usd"]) == 0.0
        )
        if not empty:
            raise RuntimeError(
                f"[{STUDY}] unscored intent carries a size: {rec['day']} {rec['ticker']} "
                f"status={rec['intent_status']}"
            )
        return
    limit = limit_price_of(ask)
    qty = causal_quantity(limit)
    reserved = reserved_usd(qty, limit)
    if float(rec["limit_price"]) != float(limit) or int(rec["qty"]) != int(qty):
        raise RuntimeError(
            f"[{STUDY}] intent sizing drifted from the engine's published helpers: "
            f"{rec['day']} {rec['ticker']} ask={ask} stored=({rec['limit_price']},"
            f"{rec['qty']}) recomputed=({limit},{qty})"
        )
    if abs(float(rec["reserved_usd"]) - float(reserved)) > 1e-9:
        raise RuntimeError(
            f"[{STUDY}] reservation drifted from the engine's rung-150 helper: "
            f"{rec['day']} {rec['ticker']} stored={rec['reserved_usd']} "
            f"recomputed={reserved}"
        )


# ----- per-day scored states (one delay-aware frame per session) ---------------
STATE_COLUMNS = (
    "day",
    "ticker",
    "t",
    "delay_min",
    "entry_minute",
    "intent_us",
    "session_end",
    "entry_status",
    "entry_open",
    "proxy_gross_15",
    "proxy_gross_60",
    "pred_15",
    "pred_60",
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
    "rule_15",
    "rule_60",
)
STATE_TYPES = {
    "day": pl.String,
    "ticker": pl.String,
    "t": pl.Int64,
    "delay_min": pl.Int64,
    "entry_minute": pl.Int64,
    "intent_us": pl.Int64,
    "session_end": pl.Int64,
    "entry_status": pl.String,
    "entry_open": pl.Float64,
    "proxy_gross_15": pl.Float64,
    "proxy_gross_60": pl.Float64,
    "pred_15": pl.Float64,
    "pred_60": pl.Float64,
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
    "rule_15": pl.Boolean,
    "rule_60": pl.Boolean,
}


def empty_states() -> pl.DataFrame:
    return pl.DataFrame(schema={c: STATE_TYPES[c] for c in STATE_COLUMNS})


def state_paths(out_dir: Path, day: str) -> tuple[Path, Path]:
    return out_dir / "states" / f"{day}.parquet", out_dir / "states" / f"{day}.cov.json"


def score_day(
    day: str, models: dict, data_root: Path, supplemental_roots: tuple[Path, ...]
) -> tuple[pl.DataFrame, dict]:
    """Score one session's liquidity-qualified states at every delayed intent clock.

    ONE panel frame, one quote frame and one day's states are resident at a time; the full
    corpus is never concatenated. The panel candidate set is the engine's own causal
    liquidity-qualified state ``t`` - IDENTICAL for every delay, so a delay moves only the
    observation clock and never the signal set. For each delay the engine's ``intent_facts``
    observes the latest RAW NBBO at ``clock_us(day, t+1+d, 0)`` and the engine's
    ``entry_rule_status`` decides each head; the panel's minute-open labels ride along for an
    explicit basis comparison only and are never an entry filter.
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
        "tickers_with_quote_stream": 0,
        "quote_day_file_present": True,
        "intent_status_counts": {},
        "entry_rule_counts": {},
        "intent_reason_counts": {},
        "no_order_min_capital": 0,
        "quote_not_acquired": 0,
    }
    if not cand.height:
        return empty_states(), cov
    tickers = sorted(set(cand["ticker"].to_list()))
    cov["tickers_qualified"] = len(tickers)
    preds = {
        head: np.asarray(models[head]["lgbm"].predict(feature_matrix(cand)), dtype=float)
        for head in HEADS
    }
    quote_frame = quote_day_frame(data_root, day, set(tickers), supplemental_roots)
    streams = symbol_quotes(quote_frame, day)
    cov["tickers_with_quote_stream"] = len(streams)
    if not engine.day_quote_path(data_root, day).exists():
        cov["quote_day_file_present"] = False
    del quote_frame, frame

    rows = cand.select(
        [
            "day",
            "ticker",
            "t",
            "entry_open",
            "entry_status",
            "session_end",
            "gross_15",
            "gross_60",
        ]
    ).to_dicts()
    session_end = int(rows[0]["session_end"])
    intent_counts: Counter = Counter()
    rule_counts: Counter = Counter()
    reason_counts: Counter = Counter()
    out: list[dict] = []
    for i, r in enumerate(rows):
        t = int(r["t"])
        sq: SymbolQuotes | None = streams.get(r["ticker"])
        base = {
            "day": day,
            "ticker": r["ticker"],
            "t": t,
            "session_end": session_end,
            "entry_status": r["entry_status"],
            "entry_open": r["entry_open"],
            "proxy_gross_15": r["gross_15"],
            "proxy_gross_60": r["gross_60"],
            "pred_15": float(preds[15][i]),
            "pred_60": float(preds[60][i]),
        }
        for delay in DELAYS:
            entry_minute = t + 1 + int(delay)
            intent_us = minute_us(day, entry_minute)
            facts = intent_facts(sq, intent_us)
            intent_counts[f"d{int(delay)}|{facts['intent_status']}"] += 1
            rec = dict(base)
            rec.update(
                {
                    "delay_min": int(delay),
                    "entry_minute": int(entry_minute),
                    "intent_us": int(intent_us),
                }
            )
            rec.update(facts)
            for head in HEADS:
                reason, ok = entry_rule_status(facts, rec[f"pred_{head}"])
                rule_counts[f"d{int(delay)}|{reason}_h{head}"] += 1
                rec[f"rule_{head}"] = bool(ok)
            reason_counts[f"d{int(delay)}|{facts['intent_reason']}"] += 1
            _check_intent_sizing(rec)
            out.append(rec)
    cov["intent_status_counts"] = dict(intent_counts)
    cov["entry_rule_counts"] = dict(rule_counts)
    cov["intent_reason_counts"] = dict(reason_counts)
    cov["no_order_min_capital"] = int(
        sum(v for k, v in reason_counts.items() if k.endswith("|no_order_min_capital"))
    )
    cov["quote_not_acquired"] = int(
        sum(v for k, v in reason_counts.items() if k.endswith("|quote_not_acquired"))
    )
    states = pl.DataFrame(
        {c: [rec[c] for rec in out] for c in STATE_COLUMNS},
        schema={c: STATE_TYPES[c] for c in STATE_COLUMNS},
    )
    return states, cov


def _model_shas() -> dict[str, str]:
    return {f"h{h}": sha256_file(MODEL_DIR / f"payoff_h{h}.joblib") for h in HEADS}


def _resume_payload(day: str, model_shas: dict, contract_sha: str, sup_digest: str) -> dict:
    return {
        "producer": producer_sha256(),
        "contract": contract_sha,
        "models": model_shas,
        "supplements": sup_digest,
        "day": day,
    }


def score_block(
    out_dir: Path,
    models: dict,
    days: list[str],
    resume: bool,
    supplemental_roots: tuple[Path, ...],
) -> dict:
    """Build (or resume) every day's delay-aware scored states, one day at a time."""
    producer = producer_sha256()
    contract_sha = _digest_bytes(CONTRACT)
    sup_digest, sup_entries = supplement_digest(supplemental_roots)
    model_shas = _model_shas()
    todo, infos = [], []
    coverage = {
        "days_total": len(days),
        "days_cached": 0,
        "days_scored": 0,
        "qualified_rows": 0,
        "intent_status_counts": {},
        "entry_rule_counts": {},
        "missing_quote_day_files": [],
        "days_without_quote_stream": [],
    }
    intent_totals: Counter = Counter()
    rule_totals: Counter = Counter()
    for day in days:
        qpath, cpath = state_paths(out_dir, day)
        expect = _digest_bytes(
            {
                **_resume_payload(day, model_shas, contract_sha, sup_digest),
                "schema": STATES_SCHEMA,
            }
        )
        fresh, prior = False, None
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
        states, cov = score_day(day, models, DATA_ROOT, supplemental_roots)
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
                        **_resume_payload(day, model_shas, contract_sha, sup_digest),
                        "schema": STATES_SCHEMA,
                    }
                ),
                "producer_sha256": producer,
                "contract_sha256": contract_sha,
                "supplement_digest": sup_digest,
                "states_sha256": sha256_file(qpath),
                "rows_schema": STATES_SCHEMA,
                "coverage": cov,
            },
        )
        infos.append({"day": day, "resumed": False, "rows": int(states.height), "coverage": cov})
        if n % PROGRESS_EVERY == 0 or n == len(todo):
            print(
                f"[score] {n}/{len(todo)} days, last {day}, qualified_rows="
                f"{cov['qualified_rows']}, quoted_streams={cov['tickers_with_quote_stream']}",
                flush=True,
            )
            print(
                "[score-day] "
                + json.dumps(
                    {
                        "day": day,
                        "rows": int(states.height),
                        "coverage": cov,
                        "artifact": str(qpath),
                        "states_sha256": sha256_file(qpath),
                        "producer_sha256": producer,
                    },
                    default=_json_default,
                ),
                flush=True,
            )
    for info in infos:
        cov = info["coverage"] or {}
        coverage["qualified_rows"] += int(cov.get("qualified_rows", 0))
        intent_totals.update(cov.get("intent_status_counts") or {})
        rule_totals.update(cov.get("entry_rule_counts") or {})
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
            "days_scored": len(todo),
            "intent_status_counts": dict(intent_totals),
            "entry_rule_counts": dict(rule_totals),
            "supplements": sup_entries,
            "supplement_digest": sup_digest,
        }
    )
    return coverage


def read_day_states(out_dir: Path, day: str) -> pl.DataFrame:
    path, _ = state_paths(out_dir, day)
    if not path.exists():
        return empty_states()
    return pl.read_parquet(path)


# ----- per-view causal replay (the engine's own replay, one delay at a time) ---
DAILY_COLUMNS = tuple(engine.DAILY_COLUMNS) + ("delay_min",)
DAILY_TYPES = {c: engine.DAILY_TYPES[c] for c in engine.DAILY_COLUMNS}
DAILY_TYPES["delay_min"] = pl.Int64
TRADE_COLUMNS = tuple(engine.TRADE_COLUMNS) + ("delay_min",)
TRADE_TYPES = {c: engine.TRADE_TYPES[c] for c in engine.TRADE_COLUMNS}
TRADE_TYPES["delay_min"] = pl.Int64


def empty_daily() -> pl.DataFrame:
    return pl.DataFrame(schema={c: DAILY_TYPES[c] for c in DAILY_COLUMNS})


def empty_trades() -> pl.DataFrame:
    return pl.DataFrame(schema={c: TRADE_TYPES[c] for c in TRADE_COLUMNS})


def _check_trade_chronology(records: list[dict], session_end: int) -> None:
    """Pin this study's clock contract on every funded intent of one delayed view.

    Three invariants carry the whole delay axis: the entry intent clock is exactly
    ``clock_us(day, t+1+d, 0)``, the arrival is exactly intent + 250ms, and the exit due
    minute is exactly ``min(entry_minute + head, session_end)`` - the head's hold starting
    at the DELAYED entry minute, still capped at the RTH close. If any of them moved, the 24
    views would no longer be a delay grid, so the lane fails loudly instead of reporting it.
    """
    for rec in records:
        delay = int(rec["delay_min"])
        t = int(rec["t"])
        head = int(rec["head"])
        entry_minute = int(rec["entry_minute"])
        if entry_minute != t + 1 + delay:
            raise RuntimeError(
                f"[{STUDY}] delayed intent clock drifted: {rec['day']} {rec['ticker']} "
                f"t={t} delay={delay} entry_minute={entry_minute}"
            )
        if int(rec["entry_arrival_us"]) != arrival_us(int(rec["intent_us"])):
            raise RuntimeError(
                f"[{STUDY}] entry arrival drifted from intent + 250ms: {rec['day']} "
                f"{rec['ticker']} intent_us={rec['intent_us']} "
                f"arrival={rec['entry_arrival_us']}"
            )
        if int(rec["exit_minute"]) != min(entry_minute + head, session_end):
            raise RuntimeError(
                f"[{STUDY}] exit hold drifted from min(entry_minute+head, session_end): "
                f"{rec['day']} {rec['ticker']} entry_minute={entry_minute} head={head} "
                f"exit_minute={rec['exit_minute']} session_end={session_end}"
            )


def replay_view_day(
    day: str,
    states: pl.DataFrame,
    streams: dict[str, SymbolQuotes],
    view: View,
    session_end: int,
) -> tuple[dict, list[dict]]:
    """One (delay, head, threshold) view on one session, on the engine's own replay.

    The engine's published ``replay_day`` performs the whole funded-$750 causal replay -
    fresh-quote-gated snapshot funding of a simultaneous clock, the IOC-limit entry leg, the
    fresh-quote-gated market exit leg, the real priced clocks, the cooldown and the
    UNKNOWN/session-end boundaries - and is handed only the rows of THIS delay, so the
    intent clocks (and therefore the exit minutes) are this view's own.
    """
    daily, records = engine.replay_day(day, states, streams, view)
    for rec in records:
        rec["delay_min"] = int(view.delay_min)
    _check_trade_chronology(records, session_end)
    return daily, records


def replay_block(
    out_dir: Path,
    block: str,
    days: list[str],
    resume: bool,
    supplemental_roots: tuple[Path, ...],
) -> dict:
    """Replay every pre-declared view on every day of one block, with resumable parts."""
    producer = producer_sha256()
    contract_sha = _digest_bytes(CONTRACT)
    sup_digest, sup_entries = supplement_digest(supplemental_roots)
    model_shas = _model_shas()
    root = out_dir / "replay" / block
    root.mkdir(parents=True, exist_ok=True)
    todo, infos, expects = [], [], {}
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
        qpath, _ = state_paths(out_dir, day)
        if not qpath.exists():
            raise SystemExit(
                f"[{STUDY}] scored states missing for {day} ({qpath}); run the score stage "
                "for the whole block before replaying it"
            )
        cpath = root / f"{day}.cov.json"
        states_sha = None
        _, scov = state_paths(out_dir, day)
        if scov.exists():
            try:
                states_sha = json.loads(scov.read_text()).get("states_sha256")
            except (json.JSONDecodeError, OSError):
                states_sha = None
        if states_sha is None:
            states_sha = sha256_file(qpath)
        expect = _digest_bytes(
            {
                **_resume_payload(day, model_shas, contract_sha, sup_digest),
                "states": states_sha,
                "schema": REPLAY_SCHEMA,
            }
        )
        expects[day] = expect
        dpath = root / f"{day}.parquet"
        tpath = root / f"{day}.trades.parquet"
        fresh, prior = False, None
        if resume and cpath.exists() and dpath.exists() and tpath.exists():
            try:
                prior = json.loads(cpath.read_text())
                fresh = prior.get("resume_hash") == expect and prior.get("schema") == REPLAY_SCHEMA
            except (json.JSONDecodeError, OSError):
                fresh = False
        if fresh:
            infos.append({"day": day, "resumed": True, "coverage": prior.get("coverage", {})})
        else:
            todo.append(day)
    for n, day in enumerate(todo, 1):
        dpath = root / f"{day}.parquet"
        tpath = root / f"{day}.trades.parquet"
        cpath = root / f"{day}.cov.json"
        states = read_day_states(out_dir, day)
        tickers = sorted(set(states["ticker"].to_list())) if states.height else []
        quote_frame = (
            quote_day_frame(DATA_ROOT, day, set(tickers), supplemental_roots) if tickers else None
        )
        streams = symbol_quotes(quote_frame, day)
        del quote_frame
        session_end = int(states["session_end"][0]) if states.height else 0
        daily_rows, trade_rows = [], []
        day_cov = {
            "states_rows": int(states.height) if states.height else 0,
            "quote_streams": len(streams),
            "views": {},
        }
        for delay in DELAYS:
            delay_states = states.filter(pl.col("delay_min") == delay) if states.height else states
            for view in VIEWS:
                if view.delay_min != delay:
                    continue
                daily, trades = replay_view_day(day, delay_states, streams, view, session_end)
                daily_rows.append(daily)
                trade_rows.extend(trades)
                day_cov["views"][view.key] = {
                    "delay_min": int(delay),
                    "signals": daily["signals"],
                    "intents_funded": daily["intents_funded"],
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
                "supplement_digest": sup_digest,
                "states_sha256": states_sha,
                "schema": REPLAY_SCHEMA,
                "coverage": day_cov,
            },
        )
        infos.append({"day": day, "resumed": False, "coverage": day_cov})
        if n % PROGRESS_EVERY == 0 or n == len(todo):
            funded = sum(v["attempts"] for v in day_cov["views"].values())
            print(
                f"[replay:{block}] {n}/{len(todo)} days, last {day}, "
                f"funded_intents={funded} over {len(VIEWS)} views",
                flush=True,
            )
            print(
                f"[replay-day:{block}] "
                + json.dumps(
                    {
                        "day": day,
                        "coverage": day_cov,
                        "daily_artifact": str(dpath),
                        "trades_artifact": str(tpath),
                        "resume_hash": expects[day],
                        "producer_sha256": producer,
                    },
                    default=_json_default,
                ),
                flush=True,
            )
    for info in infos:
        cov = info["coverage"] or {}
        for key in ("signals", "attempts", "fills", "unknown_fills", "no_match_fills"):
            coverage[key] += sum(v.get(key, 0) for v in (cov.get("views") or {}).values())
        coverage["intents_funded"] = coverage["attempts"]
        coverage["known_fills"] += sum(
            v.get("known_fills", 0) for v in (cov.get("views") or {}).values()
        )
    coverage.update(
        {"days_cached": len(infos), "days_replayed": len(todo), "supplements": sup_entries}
    )
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


# The engine's own per-day skip-reason columns (the same list its ``view_cell`` reports):
# the head-independent intent gate first, then the funded-book gates.
SKIP_REASONS = (
    "no_order_min_capital",
    "quote_not_acquired",
    "intent_not_firm_fresh",
    "intent_depth_unsupported",
    "intent_depth_unknown",
    "spread_above_prediction",
    "overlap_skips",
    "cooldown_skips",
    "max_attempt_skips",
    "cash_or_slot_skips",
)


def _funnel(cell: dict) -> dict:
    """The signals -> funded -> known / UNKNOWN / no-match composition of one view.

    Reported beside every delay delta so a delay effect can never be an artifact of a
    smaller or differently-composed fill set: the same signal set, a different observation
    clock, and an explicit accounting of where each intent went.
    """
    funded = int(cell["intents_funded"])
    stats = cell.get("trade_stats") or {}

    def share(value: int) -> float | None:
        return (float(value) / funded) if funded else None

    return {
        "signals": int(cell["signals"]),
        "funded_intents": funded,
        "attempts": int(cell["attempts"]),
        "known_fills": int(cell["known_fills"]),
        "unknown_fills": int(cell["unknown_fills"]),
        "no_match_cash_fills": int(cell["no_match_fills"]),
        "traded_days": int(cell["traded_days"]),
        "days_replayed": int(cell["days_replayed"]),
        "known_fill_share_of_funded": share(cell["known_fills"]),
        "unknown_share_of_funded": share(cell["unknown_fills"]),
        "no_match_share_of_funded": share(cell["no_match_fills"]),
        "skip_reasons": cell.get("skip_reasons", {}),
        "fill_status_counts": stats.get("fill_status_counts", {}),
        "entry_status_counts": stats.get("entry_status_counts", {}),
        "exit_submit_status_counts": stats.get("exit_submit_status_counts", {}),
    }


def delay_cell(daily: pl.DataFrame, trades: pl.DataFrame, view: View, days: list[str]) -> dict:
    """The engine's own per-view surface plus this study's delay cell.

    Everything causal is the engine's ``view_cell`` verbatim; this wrapper only adds the
    delay coordinate, the profit-factor / positive-month trade statistics the engine's cell
    does not carry, and the funnel composition the delay axis is judged on.
    """
    cell = engine.view_cell(daily, trades, view, days)
    cell["delay_min"] = int(view.delay_min)
    cell["head"] = int(view.head)
    cell["threshold"] = float(view.threshold)
    cell["control_view_key"] = view.control_key
    rows = daily.filter(pl.col("view_key") == view.key)
    filled = (
        trades.filter(
            (pl.col("view_key") == view.key) & (pl.col("fill_status") == "conditional_fill")
        )
        if trades.height
        else trades
    )
    rung_keys = [int(r) for r in RUNG_COSTS]
    nets = {r: (filled[f"net_usd_{r}"].to_list() if filled.height else []) for r in rung_keys}
    profit_factor: dict[str, float | None] = {}
    profit_factor_notes: dict[str, str | None] = {}
    for r in rung_keys:
        values = nets[r]
        gross_win = sum(v for v in values if v > 0.0)
        gross_loss = -sum(v for v in values if v < 0.0)
        if gross_loss > 0.0:
            profit_factor[str(r)] = gross_win / gross_loss
            profit_factor_notes[str(r)] = None
        elif gross_win > 0.0:
            profit_factor[str(r)] = None
            profit_factor_notes[str(r)] = "no losing known fill at this rung"
        else:
            profit_factor[str(r)] = None
            profit_factor_notes[str(r)] = "no known fill" if not values else "no nonzero known fill"
    month_totals: dict[int, dict[str, float]] = {r: {} for r in rung_keys}
    day_list = [str(d) for d in rows["day"].to_list()]
    rung_values = {r: rows[f"known_usd_{r}"].to_list() for r in rung_keys}
    for i, day in enumerate(day_list):
        month = day[:7]
        for r in rung_keys:
            bucket = month_totals[r]
            bucket[month] = bucket.get(month, 0.0) + float(rung_values[r][i])
    cell["trade_stats"]["profit_factor_known_fills"] = profit_factor
    cell["trade_stats"]["profit_factor_notes"] = profit_factor_notes
    cell["trade_stats"]["positive_months"] = {
        str(r): sum(1 for v in month_totals[r].values() if v > 0.0) for r in rung_keys
    }
    cell["trade_stats"]["months_replayed"] = len(month_totals[rung_keys[0]]) if rung_keys else 0
    cell["funnel"] = _funnel(cell)
    return cell


def aggregate_block(out_dir: Path, block: str, days: list[str]) -> tuple[dict, dict, dict]:
    """The full pre-declared surface of one block, its delay axis, and its coverage."""
    daily, trades = read_block_frames(out_dir, block)
    surface = {view.key: delay_cell(daily, trades, view, days) for view in VIEWS}
    effect = delay_effect(surface)
    funded = int(daily["intents_funded"].sum()) if daily.height else 0
    coverage = {
        "block": block,
        "days": len(days),
        "signals": int(daily["signals"].sum()) if daily.height else 0,
        "intents_funded": funded,
        "attempts": int(daily["attempts"].sum()) if daily.height else 0,
        "fills": int(daily["fills"].sum()) if daily.height else 0,
        "known_fills": int(daily["known_fills"].sum()) if daily.height else 0,
        "unknown_fills": int(daily["unknown_fills"].sum()) if daily.height else 0,
        "no_match_fills": int(daily["no_match_fills"].sum()) if daily.height else 0,
        "unknown_share_of_funded_intents": (
            float(daily["unknown_fills"].sum() / funded) if funded else None
        ),
        "causal_skips": {reason: int(daily[reason].sum()) for reason in SKIP_REASONS},
        "funnel_by_delay": {
            str(d): {
                "signals": int(daily.filter(pl.col("delay_min") == d)["signals"].sum())
                if daily.height
                else 0,
                "funded_intents": int(
                    daily.filter(pl.col("delay_min") == d)["intents_funded"].sum()
                )
                if daily.height
                else 0,
                "known_fills": int(daily.filter(pl.col("delay_min") == d)["known_fills"].sum())
                if daily.height
                else 0,
                "unknown_fills": int(daily.filter(pl.col("delay_min") == d)["unknown_fills"].sum())
                if daily.height
                else 0,
                "no_match_fills": int(
                    daily.filter(pl.col("delay_min") == d)["no_match_fills"].sum()
                )
                if daily.height
                else 0,
            }
            for d in DELAYS
        },
    }
    return surface, effect, coverage


def _delta_num(new, old) -> float | None:
    if new is None or old is None:
        return None
    return float(new) - float(old)


def _delta_cell(cell: dict, control: dict) -> dict:
    """One delayed view measured against its OWN d=0 control on the identical basis."""
    sel = str(int(SELECT_COST))
    rung_keys = [int(r) for r in RUNG_COSTS]
    delta_day = {}
    for r in rung_keys:
        key = str(r)
        delta_day[key] = _delta_num(
            cell["known_usd_per_calendar_day"][key],
            control["known_usd_per_calendar_day"][key],
        )
    retained = {}
    for r in rung_keys:
        base = control["known_usd_per_calendar_day"][str(r)]
        new = cell["known_usd_per_calendar_day"][str(r)]
        retained[str(r)] = (float(new) / float(base)) if base else None
    stats = cell.get("trade_stats") or {}
    cstats = control.get("trade_stats") or {}
    # The d=0 twin is ALWAYS resolved (it is the control of this same (head, threshold)
    # cell); its identity is derived from the cell's own coordinates so a missing or
    # renamed engine field can never leave a None in the reported delta.
    control_head = int(cell["head"])
    control_threshold = float(cell["threshold"])
    return {
        "view_key": cell["view_key"],
        "label": cell["label"],
        "delay_min": cell["delay_min"],
        "head": cell["head"],
        "threshold": cell["threshold"],
        "control_view_key": control.get("view_key")
        or view_key(0, control_head, control_threshold),
        "control_label": control.get("label")
        or view_label(0, control_head, control_threshold),
        "delta_known_usd": {
            str(r): _delta_num(cell["known_usd"][str(r)], control["known_usd"][str(r)])
            for r in rung_keys
        },
        "delta_known_usd_per_calendar_day": delta_day,
        "delta_known_usd_per_year_252_on_book_750": {
            str(r): _delta_num(
                cell["dollars_per_year_252_on_book_750"][str(r)],
                control["dollars_per_year_252_on_book_750"][str(r)],
            )
            for r in rung_keys
        },
        "delta_full_loss_lower_bound_usd_per_calendar_day": {
            str(r): _delta_num(
                cell["full_loss_lower_bound_usd_per_calendar_day"][str(r)],
                control["full_loss_lower_bound_usd_per_calendar_day"][str(r)],
            )
            for r in rung_keys
        },
        "retained_share_of_control_known_usd_per_day": retained,
        "delta_known_fills": int(cell["known_fills"] - control["known_fills"]),
        "delta_attempts": int(cell["attempts"] - control["attempts"]),
        "delta_funded_intents": int(cell["intents_funded"] - control["intents_funded"]),
        "delta_unknown_fills": int(cell["unknown_fills"] - control["unknown_fills"]),
        "delta_no_match_fills": int(cell["no_match_fills"] - control["no_match_fills"]),
        "delta_signals": int(cell["signals"] - control["signals"]),
        "delta_traded_days": int(cell["traded_days"] - control["traded_days"]),
        "delta_mean_net_usd_per_conditional_fill": {
            str(r): _delta_num(
                (stats.get("mean_net_usd_per_conditional_fill") or {}).get(str(r)),
                (cstats.get("mean_net_usd_per_conditional_fill") or {}).get(str(r)),
            )
            for r in rung_keys
        },
        "delta_win_rate_known_fill": {
            str(r): _delta_num(
                (stats.get("win_rate_known_fill") or {}).get(str(r)),
                (cstats.get("win_rate_known_fill") or {}).get(str(r)),
            )
            for r in rung_keys
        },
        "selection_rung_bps": int(SELECT_COST),
        "delta_at_selection_rung_usd_per_day": delta_day[sel],
        "retained_share_at_selection_rung": retained[sel],
        "composition": {"control": _funnel(control), "delayed": _funnel(cell)},
    }


def delay_effect(surface: dict) -> dict:
    """Every delayed view against the d=0 control of the SAME (head, threshold) cell."""
    out: dict[str, dict] = {}
    for head in HEADS:
        for threshold in THRESHOLDS:
            control = surface[view_key(0, head, threshold)]
            entry = {
                "control_view_key": control["view_key"],
                "control_label": control["label"],
                "control_known_usd_per_calendar_day": control["known_usd_per_calendar_day"],
                "by_delay": {},
            }
            for delay in DELAYS:
                if int(delay) == 0:
                    continue
                entry["by_delay"][str(int(delay))] = _delta_cell(
                    surface[view_key(delay, head, threshold)], control
                )
            out[base_key(head, threshold)] = entry
    return out


def _axis_delta(entry: dict, delay, missing: float | None = None) -> float | None:
    """One base cell's delay delta at the selection rung, never assuming the twin exists.

    Used by the pooled axis summary and by the decision text, where a d=0 control with no
    known fill at the rung legitimately has no delta: the caller supplies the fallback
    (``-inf`` / ``+inf`` for max/min, ``None`` for reporting).
    """
    cell = (entry.get("by_delay") or {}).get(str(int(delay))) or {}
    value = cell.get("delta_at_selection_rung_usd_per_day")
    if value is None:
        return missing
    return float(value)


def delay_axis_summary(effect: dict) -> dict:
    """The plateau / decay reading of the delay axis across the base (head, threshold) cells."""
    by_delay: dict[str, dict] = {}
    sel_key = str(int(SELECT_COST))
    for delay in DELAYS:
        if int(delay) == 0:
            per_day = [
                entry["control_known_usd_per_calendar_day"][sel_key] for entry in effect.values()
            ]
            by_delay["0"] = {
                "role": "control (the engine's own t+1 intent clock)",
                "views": len(per_day),
                "mean_known_usd_per_calendar_day_at_selection_rung": (
                    float(np.mean(per_day)) if per_day else None
                ),
                "median_known_usd_per_calendar_day_at_selection_rung": (
                    float(np.median(per_day)) if per_day else None
                ),
                "note": (
                    "a delay is judged against its OWN d=0 twin, never against these "
                    "pooled controls"
                ),
            }
            continue
        raw_deltas = [_axis_delta(entry, delay) for entry in effect.values()]
        deltas = [d for d in raw_deltas if d is not None]
        missing = len(raw_deltas) - len(deltas)
        shares = [
            entry["by_delay"][str(int(delay))]["retained_share_at_selection_rung"]
            for entry in effect.values()
        ]
        shares = [s for s in shares if s is not None]
        improved = [d for d in deltas if d > 0]
        regressed = [d for d in deltas if d < 0]
        by_delay[str(int(delay))] = {
            "role": "delayed entry vs its own d=0 control",
            "views": len(raw_deltas),
            "views_with_delta": len(deltas),
            "views_missing_delta": missing,
            "views_improved": len(improved),
            "views_regressed": len(regressed),
            "mean_delta_known_usd_per_calendar_day_at_selection_rung": (
                float(np.mean(deltas)) if deltas else None
            ),
            "median_delta_known_usd_per_calendar_day_at_selection_rung": (
                float(np.median(deltas)) if deltas else None
            ),
            "mean_retained_share_of_control": (float(np.mean(shares)) if shares else None),
            "best_base_view": max(
                effect.items(),
                key=lambda kv: _axis_delta(kv[1], delay, float("-inf")),
            )[0],
            "worst_base_view": min(
                effect.items(),
                key=lambda kv: _axis_delta(kv[1], delay, float("inf")),
            )[0],
        }
    return {"by_delay": by_delay, "selection_rung_bps": int(SELECT_COST), "rule": FUNNEL_RULE}


def choose_view(surface: dict) -> tuple[View, list[dict]]:
    """Deterministic argmax of the frozen validation objective; no floors, no gates."""
    sel = str(int(SELECT_COST))
    ranked = sorted(
        VIEWS,
        key=lambda v: (
            -surface[v.key]["known_usd_per_calendar_day"][sel],
            -surface[v.key]["known_fills"],
            surface[v.key]["attempts"],
            v.key,
        ),
    )
    ranking = [
        {
            "rank": i,
            "view_key": v.key,
            "label": v.label,
            "delay_min": v.delay_min,
            "head": v.head,
            "threshold": v.threshold,
            "validation_known_usd_per_calendar_day_at_selection_rung": surface[v.key][
                "known_usd_per_calendar_day"
            ][sel],
            "known_fills": surface[v.key]["known_fills"],
            "unknown_fills": surface[v.key]["unknown_fills"],
            "no_match_fills": surface[v.key]["no_match_fills"],
            "attempts": surface[v.key]["attempts"],
            "signals": surface[v.key]["signals"],
            "traded_days": surface[v.key]["traded_days"],
        }
        for i, v in enumerate(ranked, 1)
    ]
    return ranked[0], ranking


def decision_text(
    chosen: View,
    val: dict,
    late: dict | None,
    frozen: bool,
    val_effect: dict,
    late_effect: dict | None,
    axis: dict,
    late_reason: str = "Late block not run (--skip-late).",
) -> str:
    sel = str(int(SELECT_COST))
    objective = val["known_usd_per_calendar_day"][sel]
    fills = val["known_fills"]
    boot = val["bootstrap_daily_known_usd"][f"{int(SELECT_COST)}bps"]
    if fills == 0 or objective is None:
        return (
            "NO_EDGE_EVIDENCE: the chosen validation view has no known fill, so an empty "
            "signal set is cash, not a positive edge. No actual-touch positive formulation "
            "was measured; the retained lead stays the reference and nothing here is "
            "promoted."
        )
    control = (
        val_effect[base_key(chosen.head, chosen.threshold)]["by_delay"].get(
            str(int(chosen.delay_min))
        )
        if chosen.delay_min
        else None
    )
    if objective <= 0:
        return (
            f"NO_ACTUAL_TOUCH_POSITIVE_FORMULATION: the best of the {len(VIEWS)} pre-declared "
            f"views ({chosen.label}) measures {_fmt(objective)} $/calendar day of KNOWN "
            f"contribution on 2023 validation at {int(SELECT_COST)}bps over {fills} known "
            f"fills (day bootstrap p>0 = {boot.get('p_gt_zero')}). All {len(VIEWS)} views are "
            "measured and none is positive, so the retained lead stays the reference; the "
            "unknown share is reported beside this number and is NOT assumed zero."
        )
    text = (
        f"{STATUS}: chosen {chosen.label} (entry delay {chosen.delay_min} min, head "
        f"{chosen.head}, threshold {chosen.threshold:.4f}) by 2023 validation actual-touch "
        f"known contribution: {_fmt(objective)} $/calendar day at {int(SELECT_COST)}bps = "
        f"{_fmt(val['dollars_per_year_252_on_book_750'][sel], '+.0f')} $/year on the "
        f"${int(BOOK)} "
        f"nominal book (simple 252-session convention, not a CAGR), {fills} known fills on "
        f"{val['traded_days']} traded days of {val['days_replayed']} calendared days, "
        f"{val['unknown_fills']} UNKNOWN executions "
        f"({100.0 * val['unknown_fills'] / max(1, val['attempts']):.1f}% of attempts) and "
        f"{val['no_match_fills']} no-match unfilled intents. The separately labeled "
        "full-loss lower bound over the UNKNOWN executions is "
        f"{_fmt(val['full_loss_lower_bound_usd_per_calendar_day'][sel])} $/calendar day at "
        f"the same rung. Day bootstrap p>0 = {boot.get('p_gt_zero')}. This is the measured "
        "KNOWN-contribution expectation on a PARTIAL basis (unknowns excluded from the "
        "numerator), not an actual account CAGR."
    )
    if control is not None:
        ctrl = val_effect[base_key(chosen.head, chosen.threshold)]
        control_funnel = (control.get("composition") or {}).get("control") or {}
        control_label = ctrl.get("control_label") or view_label(
            0, chosen.head, chosen.threshold
        )
        text += (
            f" The SAME (head, threshold) d=0 control view ({control_label}) "
            "measures "
            f"{_fmt((ctrl.get('control_known_usd_per_calendar_day') or {}).get(sel))} "
            "$/calendar day with "
            f"{control_funnel.get('known_fills', 'n/a')} known fills on "
            "the identical basis, so the chosen view's delay is worth "
            f"{_fmt(control.get('delta_at_selection_rung_usd_per_day'))} $/calendar day at "
            f"{int(SELECT_COST)}bps (retained share "
            f"{_fmt_mult(control.get('retained_share_at_selection_rung'))}, "
            f"{int(control.get('delta_known_fills') or 0):+d} known fills, "
            f"{int(control.get('delta_unknown_fills') or 0):+d} UNKNOWN fills, "
            f"{int(control.get('delta_no_match_fills') or 0):+d} no-match cash)."
        )
    plateau = [
        d
        for d in ("1", "2", "4")
        if (axis["by_delay"][d]["mean_delta_known_usd_per_calendar_day_at_selection_rung"] or 0.0)
        > 0
    ]
    if plateau:
        first = axis["by_delay"][plateau[0]]
        last = axis["by_delay"][plateau[-1]]
        text += (
            f" On 2023 validation the delay axis improves "
            f"{first['views_improved']}-{last['views_improved']} of "
            f"{len(VIEWS) // len(DELAYS)} base cells at d={','.join(plateau)} "
            "(mean delta "
            f"{_fmt(first['mean_delta_known_usd_per_calendar_day_at_selection_rung'])}"
            " $/calendar day at the selection rung over the pooled controls)."
        )
    if frozen and late is not None:
        late_obj = late["known_usd_per_calendar_day"][sel]
        text += (
            f" The frozen late block measures {_fmt(late_obj)} $/calendar day at "
            f"{int(SELECT_COST)}bps over {late['known_fills']} known fills of "
            f"{late['attempts']} attempts on the previously explored 2025-02..2026-05 "
            "window; the choice was frozen before any late file was read, so this is "
            "confirmation of an already-frozen decision, not a re-selection."
        )
        if late_effect is not None:
            late_entry = late_effect[base_key(chosen.head, chosen.threshold)]
            late_ctrl = (late_entry.get("by_delay") or {}).get(str(int(chosen.delay_min)))
            if late_ctrl is not None:
                control_day = (late_entry.get("control_known_usd_per_calendar_day") or {}).get(
                    sel
                )
                late_label = late_ctrl.get("control_label") or view_label(
                    0, chosen.head, chosen.threshold
                )
                text += (
                    " On the same late window the d=0 control of this (head, threshold) "
                    f"cell ({late_label}) measures {_fmt(control_day)} $/calendar day, so "
                    f"the delay is worth "
                    f"{_fmt(late_ctrl.get('delta_at_selection_rung_usd_per_day'))} "
                    "$/calendar day there (retained share "
                    f"{_fmt_mult(late_ctrl.get('retained_share_at_selection_rung'))})."
                )
    elif not frozen:
        text += f" {late_reason}"
    text += (
        f" COSTS: the {int(RUNG_COSTS[0])}..{int(RUNG_COSTS[-1])} bps rungs are {COST_NATURE}; "
        f"25 bps is ONE pre-declared comparison scenario, never a hurdle, and no rung (100 "
        "bps included) vetoes a candidate. " + LOW_FEE_LABEL + "."
    )
    return text


def provenance_block(
    out_dir: Path,
    days_val: list[str],
    days_late: list[str] | None,
    supplemental_roots: tuple[Path, ...],
    val_cov: dict,
    late_cov: dict | None,
) -> dict:
    sup_digest, sup_entries = supplement_digest(supplemental_roots)
    all_days = days_val + list(days_late or [])
    return {
        "producer_sha256": producer_sha256(),
        "producer_script": str(Path(__file__).resolve()),
        "contract_sha256": _digest_bytes(CONTRACT),
        "engine_producer_sha256": engine_producer_sha256(),
        "panel_root": str(PANEL_ROOT),
        "panel_days_root": str(PANEL_DAYS),
        "panel_contract_sha256": (sha256_file(PANEL_CONTRACT) if PANEL_CONTRACT.exists() else None),
        "quote_cache_root": str(QUOTE_CACHE_ROOT),
        "quote_cache_days_present": sum(
            1 for d in all_days if (QUOTE_CACHE_ROOT / f"{d}.parquet").exists()
        ),
        "supplemental_roots": [str(r) for r in supplemental_roots],
        "supplemental_roots_existing": [str(r) for r in supplemental_roots if r.exists()],
        "supplemental_files": sup_entries,
        "supplement_digest": sup_digest,
        "model_dir": str(MODEL_DIR),
        "models": {
            f"h{h}": {
                "path": str(MODEL_DIR / f"payoff_h{h}.joblib"),
                "sha256": sha256_file(MODEL_DIR / f"payoff_h{h}.joblib"),
            }
            for h in HEADS
        },
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
def _block_print(block_tag: str, surface: dict, axis: dict) -> None:
    """One block's views at the selection rung, then its delay-axis summary."""
    sel = str(int(SELECT_COST))
    for view in VIEWS:
        cell = surface[view.key]
        boot = cell["bootstrap_daily_known_usd"][f"{int(SELECT_COST)}bps"]
        print(
            f"[{block_tag}@{int(SELECT_COST)}] {view.label:<28} "
            f"{_fmt(cell['known_usd_per_calendar_day'][sel], '+8.2f')} $/day "
            f"known={cell['known_fills']} unknown={cell['unknown_fills']} "
            f"nomatch={cell['no_match_fills']} attempts={cell['attempts']} "
            f"signals={cell['signals']} traded_days={cell['traded_days']} "
            f"p>0={boot.get('p_gt_zero')}",
            flush=True,
        )
    for delay in ("1", "2", "4"):
        row = axis["by_delay"][delay]
        print(
            f"[{block_tag}-delay@{int(SELECT_COST)}] d={delay}min "
            f"improved={row['views_improved']}/{row['views']} "
            "mean_delta="
            f"{_fmt(row['mean_delta_known_usd_per_calendar_day_at_selection_rung'])} $/day "
            f"mean_retained={_fmt(row['mean_retained_share_of_control'], '.3f')}x "
            f"best={row['best_base_view']} worst={row['worst_base_view']}",
            flush=True,
        )


def _chosen_curve(
    chosen: View,
    surface: dict,
    effect: dict,
    late_surface: dict | None,
    late_effect: dict | None,
) -> dict:
    """Per rung: the frozen view, its d=0 twin and the delta, on both blocks."""
    rung_keys = [int(r) for r in RUNG_COSTS]
    base = base_key(chosen.head, chosen.threshold)
    val_control = surface[chosen.control_key]
    val_cell = surface[chosen.key]
    late_control = late_surface[chosen.control_key] if late_surface else None
    late_cell = late_surface[chosen.key] if late_surface else None
    out: dict[str, dict] = {}
    for r in rung_keys:
        key = str(r)
        control_per_day = effect[base]["control_known_usd_per_calendar_day"][key]
        late_control_per_day = (
            late_effect[base]["control_known_usd_per_calendar_day"][key]
            if late_effect is not None
            else None
        )
        late_delta_entry = (
            late_effect[base]["by_delay"].get(str(int(chosen.delay_min)))
            if late_effect is not None
            else None
        )
        late_delta = (
            late_delta_entry["delta_known_usd_per_calendar_day"][key]
            if late_delta_entry is not None
            else None
        )
        out[key] = {
            "rung_bps": r,
            "validation_control_known_usd_per_calendar_day": control_per_day,
            "validation_chosen_known_usd_per_calendar_day": val_cell["known_usd_per_calendar_day"][
                key
            ],
            "validation_delta_known_usd_per_calendar_day": val_cell["known_usd_per_calendar_day"][
                key
            ]
            - control_per_day,
            "validation_retained_share": (
                float(val_cell["known_usd_per_calendar_day"][key] / control_per_day)
                if control_per_day
                else None
            ),
            "validation_control_full_loss_lower_bound_usd_per_calendar_day": val_control[
                "full_loss_lower_bound_usd_per_calendar_day"
            ][key],
            "validation_chosen_full_loss_lower_bound_usd_per_calendar_day": val_cell[
                "full_loss_lower_bound_usd_per_calendar_day"
            ][key],
            "validation_control_known_fills": val_control["known_fills"],
            "validation_chosen_known_fills": val_cell["known_fills"],
            "late_control_known_usd_per_calendar_day": late_control_per_day,
            "late_chosen_known_usd_per_calendar_day": (
                late_cell["known_usd_per_calendar_day"][key] if late_cell else None
            ),
            "late_delta_known_usd_per_calendar_day": late_delta,
            "late_control_known_fills": late_control["known_fills"] if late_control else None,
            "late_chosen_known_fills": late_cell["known_fills"] if late_cell else None,
            "note": (
                "the frozen choice IS the d=0 control view"
                if chosen.delay_min == 0
                else "chosen vs the d=0 twin of the same (head, threshold) cell"
            ),
        }
    return {"selection_rung_bps": int(SELECT_COST), "by_rung": out, "chosen_view_key": chosen.key}


def run(args: argparse.Namespace) -> None:
    t0 = time.time()

    out = args.out or OUTPUT
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")

    models, model_report = load_stored_models()
    verify_engine_contract()
    print(
        "[model] immutable heads loaded (no refit): "
        + ", ".join(
            f"h{h} sha256 {model_report[f'h{h}']['sha256'][:12]} "
            f"({model_report[f'h{h}']['n_features']} features)"
            for h in HEADS
        ),
        flush=True,
    )

    supplemental_roots = QUOTE_SUPPLEMENT_ROOTS
    existing = [r for r in supplemental_roots if r.exists()]
    print(
        "[supplements] " + (", ".join(str(r) for r in existing) if existing else "none present"),
        flush=True,
    )

    # The ENTIRE pre-declared grid (32 views) and the 0..150 bps rung ladder are frozen
    # validation day is scored: the selection can never re-shape the grid it is judged on.
    days_val = block_days(args.days, VAL_BLOCK)
    guard_days(days_val)
    if args.days is None and len(days_val) != EXPECTED_DAYS[VAL_BLOCK]:
        raise SystemExit(
            f"[{STUDY}] requires {EXPECTED_DAYS[VAL_BLOCK]} {VAL_BLOCK} days, got {len(days_val)}"
        )
    if not days_val:
        raise SystemExit(f"[{STUDY}] no {VAL_BLOCK} days selected")

    (out / "contract.json").write_text(json.dumps(CONTRACT, indent=1, default=_json_default) + "\n")
    print(
        f"[freeze-grid] contract -> {out / 'contract.json'} "
        f"({len(VIEWS)} views x {len(RUNG_COSTS)} rungs, before any outcome)",
        flush=True,
    )

    # 1) validation only: no late panel or quote file is touched before the freeze.
    sel = str(int(SELECT_COST))
    print(
        f"[blocks] validation={len(days_val)} days (late block deferred until after the freeze)",
        flush=True,
    )
    val_score_cov = score_block(out, models, days_val, args.resume, supplemental_roots)
    val_replay_cov = replay_block(out, VAL_BLOCK, days_val, args.resume, supplemental_roots)
    val_surface, val_effect, val_cov = aggregate_block(out, VAL_BLOCK, days_val)
    val_axis = delay_axis_summary(val_effect)
    _block_print("val", val_surface, val_axis)

    chosen, ranking = choose_view(val_surface)
    frozen_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    freeze = {
        "frozen_before_late_file_access": True,
        "frozen_at": frozen_at,
        "study": STUDY,
        "status": STATUS,
        "chosen": {
            "view_key": chosen.key,
            "label": chosen.label,
            "delay_min": chosen.delay_min,
            "head": chosen.head,
            "threshold": chosen.threshold,
            "control_view_key": chosen.control_key,
        },
        "selection": {
            "objective": SELECTION_RULE["objective"],
            "selection_cost_bps": SELECT_COST,
            "cost_ladder_bps": list(RUNG_COSTS),
            "basis": SELECTION_RULE["basis"],
            "power_floors": None,
            "median_or_tail_gates": None,
            "cost_or_count_kill": None,
            "tie_break": SELECTION_RULE["tie_break"],
            "all_views_reported": len(VIEWS),
            "ranking": ranking,
        },
        "views": [
            {
                "view_key": v.key,
                "label": v.label,
                "delay_min": v.delay_min,
                "head": v.head,
                "threshold": v.threshold,
                "control_view_key": v.control_key,
            }
            for v in VIEWS
        ],
        "validation_surface": val_surface,
        "validation_delay_effect_vs_d0": val_effect,
        "validation_delay_axis": val_axis,
        "model": model_report,
        "provenance": provenance_block(out, days_val, None, supplemental_roots, val_cov, None),
    }
    # Written AFTER the validation surface and BEFORE any late file is read.
    write_json_atomic(out / "selection_freeze.json", freeze)
    print(
        f"[freeze] selection_freeze.json -> chosen {chosen.label} "
        f"({_fmt(val_surface[chosen.key]['known_usd_per_calendar_day'][sel])} $/day "
        f"@{int(SELECT_COST)}) AFTER validation, BEFORE any late file is read",
        flush=True,
    )

    late_surface: dict | None = None
    late_effect: dict | None = None
    late_axis: dict | None = None
    days_late: list[str] | None = None
    late_note: str | None = None
    if args.skip_late:
        late_note = "late block not run (--skip-late)"
    else:
        # 2) late block: the same pre-declared views for transparency, choice frozen.
        #    This is the FIRST access to any late panel or quote file.
        days_late = block_days(args.days, LATE_BLOCK)
        guard_days(days_late)
        if args.days is None and len(days_late) != EXPECTED_DAYS[LATE_BLOCK]:
            raise SystemExit(
                f"[{STUDY}] requires {EXPECTED_DAYS[LATE_BLOCK]} {LATE_BLOCK} days, "
                f"got {len(days_late)}"
            )
        if not days_late:
            days_late, late_note = None, ("late block not run: --days selected no confirmation day")
        else:
            print(f"[blocks] late={len(days_late)} days (after the freeze)", flush=True)
            late_score_cov = score_block(out, models, days_late, args.resume, supplemental_roots)
            late_replay_cov = replay_block(
                out, LATE_BLOCK, days_late, args.resume, supplemental_roots
            )
            late_surface, late_effect, late_cov = aggregate_block(out, LATE_BLOCK, days_late)
            late_axis = delay_axis_summary(late_effect)
            _block_print("late", late_surface, late_axis)

    decision = decision_text(
        chosen,
        val_surface[chosen.key],
        late_surface[chosen.key] if late_surface is not None else None,
        late_surface is not None,
        val_effect,
        late_effect,
        val_axis,
        late_reason=late_note or "Late block not run.",
    )
    print(f"[decision] {decision}", flush=True)

    cost_ladder = {
        view.key: {
            str(int(r)): {
                "validation_known_usd": val_surface[view.key]["known_usd"][str(int(r))],
                "validation_usd_per_calendar_day": val_surface[view.key][
                    "known_usd_per_calendar_day"
                ][str(int(r))],
                "validation_full_loss_lower_bound_usd_per_calendar_day": val_surface[view.key][
                    "full_loss_lower_bound_usd_per_calendar_day"
                ][str(int(r))],
                "late_known_usd": (
                    late_surface[view.key]["known_usd"][str(int(r))]
                    if late_surface is not None
                    else None
                ),
                "late_usd_per_calendar_day": (
                    late_surface[view.key]["known_usd_per_calendar_day"][str(int(r))]
                    if late_surface is not None
                    else None
                ),
                "late_full_loss_lower_bound_usd_per_calendar_day": (
                    late_surface[view.key]["full_loss_lower_bound_usd_per_calendar_day"][
                        str(int(r))
                    ]
                    if late_surface is not None
                    else None
                ),
            }
            for r in RUNG_COSTS
        }
        for view in VIEWS
    }
    coverages = {
        "validation": {
            "aggregate": val_cov,
            "score": val_score_cov,
            "replay": val_replay_cov,
        }
    }
    if late_note is not None:
        coverages["late"] = late_note
    elif days_late is not None:
        coverages["late"] = {
            "aggregate": late_cov,
            "score": late_score_cov,
            "replay": late_replay_cov,
        }
    results = {
        "study": STUDY,
        "status": STATUS,
        "decision": decision,
        "chosen": freeze["chosen"],
        "frozen_choice_immutable": True,
        "contract": CONTRACT,
        "model": model_report,
        "grid": [
            {
                "view_key": v.key,
                "label": v.label,
                "delay_min": v.delay_min,
                "head": v.head,
                "threshold": v.threshold,
                "control_view_key": v.control_key,
            }
            for v in VIEWS
        ],
        "validation": val_surface,
        "validation_delay_effect_vs_d0": val_effect,
        "validation_delay_axis": val_axis,
        "validation_ranking": ranking,
        "late": late_surface,
        "late_delay_effect_vs_d0": late_effect,
        "late_delay_axis": late_axis,
        "late_note": late_note,
        "late_ranking": (
            [
                {
                    "view_key": v.key,
                    "label": v.label,
                    "delay_min": v.delay_min,
                    "late_known_usd_per_calendar_day_at_selection_rung": late_surface[v.key][
                        "known_usd_per_calendar_day"
                    ][sel],
                    "known_fills": late_surface[v.key]["known_fills"],
                    "unknown_fills": late_surface[v.key]["unknown_fills"],
                }
                for v in sorted(
                    VIEWS,
                    key=lambda v: (
                        -late_surface[v.key]["known_usd_per_calendar_day"][sel],
                        -late_surface[v.key]["known_fills"],
                        late_surface[v.key]["attempts"],
                        v.key,
                    ),
                )
            ]
            if late_surface is not None
            else None
        ),
        "chosen_late_block": (late_surface[chosen.key] if late_surface is not None else None),
        "chosen_vs_its_d0_control": _chosen_curve(
            chosen, val_surface, val_effect, late_surface, late_effect
        ),
        "cost_ladder_whole_calendar_known_contributions": cost_ladder,
        "coverage": coverages,
        "provenance": provenance_block(
            out, days_val, days_late, supplemental_roots, val_cov, late_cov if days_late else None
        ),
        "artifacts": {
            "contract": str(out / "contract.json"),
            "selection_freeze": str(out / "selection_freeze.json"),
            "producer_snapshot": str(out / "producer_snapshot.py"),
            "states_root": str(out / "states"),
            "replay_root": str(out / "replay"),
            "results": str(out / "results.json"),
        },
        "runtime_s": round(time.time() - t0, 1),
    }
    write_json_atomic(out / "results.json", results)
    print(
        f"[done] results -> {out / 'results.json'} ({results['runtime_s']}s)",
        flush=True,
    )


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--out", type=Path, default=None, help=f"output root (default: {OUTPUT})")
    p.add_argument(
        "--resume", action="store_true", help="resume from the per-day artifacts on disk"
    )
    p.add_argument(
        "--skip-late",
        action="store_true",
        help="stop after the frozen selection (validation block only)",
    )
    p.add_argument(
        "--days",
        nargs="+",
        default=None,
        help="restrict the analysis calendar to these ET days (smoke/debug only)",
    )
    run(p.parse_args())


if __name__ == "__main__":
    main()
