#!/usr/bin/env python3
"""Spread-saving PASSIVE limit-entry confirmation on the immutable h60 forecast (no refit).

The retained lane takes the spread: a marketable IOC at the observed ASK. This producer
asks the orthogonal execution question the IOC lane cannot ask - does a LONG entry that
RESTS as a posted passive DAY limit, priced at the intent BID or MID instead of the ASK,
fill often enough, and at a price that saves enough of the observed spread, to pay for
itself on the same immutable h60 forecast? Nothing about the model moves; only the
order's resting behaviour does.

* ONE immutable model. The stored learned h60 head (payoff_h60.joblib, fitted on the
  2021-02..2022-12 panel block, never replayed, never re-scored as outcomes) is read at
  TWO fixed bars theta in {0.020, 0.030}. No refit, no HPO, no new features, no
  ticker/date identifiers. The shared immutable loader
  (``alpha_quote_aware_frequency.load_stored_models``) is reused verbatim, so the stored
  feature order and the fit-time matrix helper are verified exactly as in the IOC lane;
  the h15 head it also loads is verified and then never scored here.
* EIGHT fixed views = 2 bars x 2 placements x 2 TTLs. Placement is where the limit is
  posted relative to the intent NBBO: ``bid`` = the observed intent BID, ``mid`` = the
  observed intent MID, both floored DOWN to the cent (a passive limit is never rounded
  up into the ask). TTL is the working order's time-to-live: 30s or 120s after the
  acknowledgement. Identical bid/mid limits are deduplicated so one order is never
  booked twice (a locked or sub-cent-spread book floors both placements to the same
  tick, and within a view the candidate key is (ticker, intent clock, posted limit)).
* NOT the old h025 flush form. There is no flush anchor, no target, no minimum future
  price and no high-touch profit credit anywhere in this producer: the exit is a
  scheduled 60-minute HOLD bounded by the RTH last clock, priced from the real NBBO BID.
* ENTRY PHYSICS (the whole typed lifetime). At the intent clock - clock (t+1) at
  00.000s - the latest RAW NBBO state is observed once (never pre-filtered for
  eligibility, so a stale, crossed or non-regular latest print stays observable and is
  reported). From that observation, and BEFORE the order is acknowledged, the posted
  limit, the causal quantity q = floor(1000 / (limit * (1 + 150bps/2))) and the reserved
  cash are fixed; they are never re-derived from any later print. The order is
  acknowledged at intent + 250ms (``submit_ack_us``), works as a DAY limit (it
  legitimately RESTS, unlike an IOC), and its cancellation is submitted at
  ``submit_ack + TTL`` and acknowledged at ``cancel_submit + 250ms``.
  The PRIMARY price proof is the FIRST print at/after the acknowledgement and at/before
  the cancel acknowledgement whose ASK is at or below the posted limit. A print strictly
  between the intent and the acknowledgement is invisible to the order (it is not
  working yet), and a print after the cancel acknowledgement is invisible too (the order
  is gone), so the visible window is exactly [submit_ack, cancel_ack] - the fill can
  occur during the cancel-latency window. At that first potential touch the print must
  be FIRM (valid, un-crossed, regular-only) with ASK depth >= q: a conditional
  quote-touch limit fill at the ACTUAL ask of that print - never at the optimistic bid
  that was merely touched, never at a same-bar low and never at a trade price - and the
  scan stops there: no best future quote, no later, cheaper or deeper print is ever
  substituted. If the state already on the book at the acknowledgement is firm with
  ASK <= limit, the order is marketable at placement and fills at that print's own ask
  under the same depth rule; if that state is not firm, the order's own fill state is
  unobservable and the execution is an UNKNOWN. If the first potential touch is
  ambiguous (that print is not firm/valid) or its displayed depth is short of q, the fill
  is a PARTIAL/UNKNOWN execution: the position and its cash stay reserved to the session
  end and the scan does NOT skip forward to a later, better, full-size print. If no ASK at
  or below the limit is visible inside the window, the resting DAY limit is cancelled
  unfilled - cash is released at the CANCEL ACK clock, never at the cancel submission,
  because a fill can happen inside that latency. The explicit L1 model is the only fill
  evidence used: hidden queue position, hidden liquidity and routed-exchange fills are
  unmeasured and never invented.
* QUEUE / TRADE-VOLUME BOUNDS are a diagnostic, never the primary basis. Venue routing
  and queue position are not observable in this substrate, so no queue advance is
  modeled and no market-maker rebate is assumed; the reported bounds use displayed L1
  depth only and are labelled as such beside the primary counts.
* EXIT PHYSICS. After an ACTUAL fill the position is held 60 minutes, bounded by the RTH
  last clock: the due intent is the minute clock at/after (fill clock + 60min), capped
  at the panel's own session_end, and never earlier than the fill. The sell is a MARKET
  order with the shared fresh-quote-gated submission (submit at the due intent when the
  latest raw state is firm and fresh, else hold to the FIRST eligible regular print
  at/after the due intent inside RTH), priced +250ms after the ACTUAL submission from the
  latest raw state there, at the causal q. Age > 2s is an operational gate on SENDING,
  never a claim that an NBBO expired or that a submitted order was cancelled. The exit
  BID depth must support q; a short book is an UNKNOWN, never a guaranteed full fill.
* BOOK. 3 slots x $1,000 = $3,000 nominal research allocation, no leverage, one position
  per ticker (so a working order holds its ticker), a flat 15-minute cooldown after the
  ACTUAL exit and at most 3 attempts per ticker per session. Every intent of a clock is
  funded BEFORE any fill, cancel or unknown outcome is observed, so a fourth same-clock
  candidate is never substituted after an unfilled one. Cash returns only at the actual
  priced exit, at the cancel acknowledgement of an order with no possible visible fill,
  or at the session end for an unresolved position; a chronic working order consumes a
  slot for its whole TTL. A quote stream that was never acquired for a session is a DATA
  UNKNOWN acquisition, reported separately from cash and from causal rule skips.

Selection: the ENTIRE eight-view contract is frozen to disk BEFORE any validation
outcome exists. The single view is then chosen ONLY by 2023 validation actual-touch
known contribution in dollars per full calendar day at the 25bps extra-cost rung, on an
explicit PARTIAL basis (unknown executions are excluded from the numerator and reported
beside it; the whole-day full-loss lower bound is a separate bound, never the objective
and never an expected-loss estimate). No cost, count, median, top-day or CI floor is
applied anywhere, no n=100 power gate, no stress kill: 25bps is ONE pre-declared
scenario among the reported rungs, never a hurdle, and no rung closes a candidate.
``selection_freeze.json`` is written AFTER the validation surface and BEFORE any late
file is read; the late block then runs all eight views for transparency while the
choice stays immutable.

Cost wording (human-financial accuracy, no imagined purity): the 0/5/10/25/50/75/100/125/
150 bps rungs are EXTRA residual fee/slippage scenarios charged ON TOP of the ACTUAL ASK
entry / BID exit touch prices, NOT total modeled friction and NOT actual broker fees.
The observed touch prices already carry the spread, so every rung charges only the extra
residual and the spread is never re-charged - the spread saving of the passive placement
is already inside the entry price, not added back as a credit. A primary US broker's
regular schedule (zero commission, SEC 20.6 per $1M of sell value, CAT 0.000003 per
side, daily cent rounding per account/type) is typically well under 1bp on a
$1,000-$3,000 ticket, so the 0/5 bps sensitivity is reported beside the 25bps scenario:
the low rungs describe a LOW-FEE OPPORTUNITY set, the actual provider fees are far below
25bps and are sourced in the separate provider-fee ledger, never asserted here.

Prior data (no imagined purity): the 2023 validation block and the 2025-02..2026-05 late
block were BOTH explored before this replay, and each is far longer than six calendar
months, so neither is pristine or previously unknown; every late number is
DISCOVERY-NOT-VALIDATED and only confirms an already-frozen choice. The stored model
itself was fitted once on the 2021-02..2022-12 block (the two recorded fit years): this
producer never refits, extends or re-weights it and never replays the fit block.

Missing quotes are UNKNOWN, not cash: a ticker whose quote stream was never acquired for
a session is reported as ``quote_not_acquired`` and excluded from the PARTIAL numerator,
never scored as an unfilled order. The full PIT panel pool is used as-is, merged with the
existing supplement caches and the read-only quote-universe acquisition root when they
exist (original prints always win; a supplement may only add timestamps), each merged
file digested by sha256.

This is a research replay of a historical conditional L1 resting-limit model. No live
order, no broker call, no account write, no protected outcome (2024, 2025-01, 2026-06..
08) is ever touched. Quotes measure the touch cost of a hypothetical order; they are not
exchange fills and never a fill guarantee.

Usage:
  uv run --no-sync python factory/scripts/alpha_passive_quote_confirmation.py
      # full 250 validation + 332 late days
      # -> ~/alpha-data/open-search-v1/passive_quote_confirmation
  uv run --no-sync python factory/scripts/alpha_passive_quote_confirmation.py --resume
      # resume from the per-day state / daily / trade parts already on disk
  uv run --no-sync python factory/scripts/alpha_passive_quote_confirmation.py --days 2023-05-15
      # smoke subset (debug only)
  uv run --no-sync python factory/scripts/alpha_passive_quote_confirmation.py --skip-late
      # stop after the frozen selection (validation block only)
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

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))

import alpha_quote_aware_frequency as aqf
from alpha_open_learned import FEATURES_ALL, PANEL_ROOT, feature_matrix
from alpha_open_panel import allowed
from alpha_open_sim import causal_liquidity, period
from alpha_sparse_daily import day_context

# ----- fixed configuration (no HPO, no refit, no grid search). -----------------
HEAD = 60  # the ONE immutable learned head this study trades
THRESHOLDS = (0.020, 0.030)  # two fixed bars on the model's own predicted payoff
PLACEMENTS = ("bid", "mid")  # where the passive limit is posted relative to the NBBO
TTLS_S = (30, 120)  # the working order's time-to-live after its acknowledgement
MAX_SLOTS = 3  # concurrent positions (a working order holds one)
ORDER_BUDGET = 1000.0  # per-position ticket
BOOK = MAX_SLOTS * ORDER_BUDGET  # $3,000 nominal research sub-book
COOLDOWN_MIN = 15  # flat minutes after the ACTUAL exit
MAX_ATTEMPTS = 3  # attempts per ticker per session
HOLD_MIN = 60  # post-fill hold, bounded by the RTH last clock
SELECT_COST = 25.0  # primary selection rung: ONE pre-declared scenario, never a hurdle
TICK_FLOOR_DECIMALS = 2  # a passive limit is floored DOWN to the cent, never rounded up
ANNUAL_SESSIONS = 252  # simple session-count convention, NOT a CAGR claim
VAL_BLOCK = "validation"
LATE_BLOCK = "confirmation"
EXPECTED_DAYS = {VAL_BLOCK: 250, LATE_BLOCK: 332}
OUTPUT = PANEL_ROOT / "passive_quote_confirmation"
STATUS = "DISCOVERY-NOT-VALIDATED"
VERSION = 1
STATES_SCHEMA = 1
REPLAY_SCHEMA = 1
UNDERPOWERED_KNOWN_FILLS = 25  # label only; NEVER a drop or auto-kill threshold
PROTECTED_UNREAD = ["2024", "2025-01", "2026-06", "2026-07", "2026-08"]
PANEL_DAYS = aqf.PANEL_DAYS
MODEL_DIR = aqf.MODEL_DIR
# The shared residual ladder and latency are published by the quote-aware kernel so every
# producer of this family reports the SAME rungs and the SAME 250ms order latency; the
# selection rung and the ticket are declared here, in this study's own contract.
RUNG_COSTS = aqf.RUNG_COSTS
MAX_RUNG_COST = float(max(RUNG_COSTS))
LATENCY_US = aqf.LATENCY_US
MAX_AGE_S = aqf.MAX_AGE_S
DATA_ROOT = aqf.DATA_ROOT
QUOTE_CACHE_ROOT = aqf.QUOTE_CACHE_ROOT
# The ranked SIP day cache plus the existing supplement caches, plus the read-only
# quote-universe acquisition root when it exists (originals always win; a supplement may
# only add timestamps, never revise an original print).
ACQUIRE_ROOT = PANEL_ROOT / "quote_universe_acquire" / "quotes"
SUPPLEMENT_ROOTS = tuple(aqf.SUPPLEMENT_ROOTS) + (ACQUIRE_ROOT,)

CONTRACT = {
    "version": VERSION,
    "hypothesis": "a LONG entry that rests as a posted passive DAY limit at the intent "
    "BID or MID (tick-floored, never rounded up), held for a 30s or 120s TTL on the same "
    "immutable h60 forecast at two fixed bars, fills often enough at a spread-saving "
    "price to beat the marketable IOC touch of the retained lane",
    "periods": {
        VAL_BLOCK: "2023-01-01..2023-12-31 (all 250 allowed panel days)",
        LATE_BLOCK: "2025-02-01..2026-05-31 (all 332 allowed panel days, previously "
        "explored, not pristine)",
    },
    "protected_unread": PROTECTED_UNREAD,
    "model": {
        "refit": False,
        "hpo": False,
        "feature_changes": False,
        "new_features": False,
        "ticker_or_date_features": False,
        "loaded_from": str(MODEL_DIR),
        "head": HEAD,
        "feature_order": list(FEATURES_ALL),
        "feature_matrix_helper": "alpha_open_learned.feature_matrix (Float64, null-filled)",
        "fit_block": "2021-02..2022-12 (never replayed, never re-scored as outcomes)",
        "target": "panel minute-open gross payoff label gross_60 (minute-open proxy)",
        "clip_fit": [-0.5, 2.0],
        "sibling_heads_loaded_but_unused": (
            "the shared immutable loader also loads and verifies payoff_h15; only h60 is "
            "ever scored in this producer"
        ),
    },
    "views": {
        "count": len(THRESHOLDS) * len(PLACEMENTS) * len(TTLS_S),
        "thresholds": list(THRESHOLDS),
        "placements": list(PLACEMENTS),
        "ttls_s": list(TTLS_S),
        "view_key": "h60|<theta>|<placement>|ttl<seconds>",
        "identical_limit_dedup": (
            "bid and mid placements floor to the same cent on a locked or sub-cent-spread "
            "book (placement_collapsed), and inside a view the candidate key is "
            "(ticker, intent clock, posted limit): one order is never booked twice"
        ),
    },
    "entry": {
        "form": "posted passive DAY LONG limit that RESTS (never the old h025 flush "
        "anchor/target/minimum-future-price form; no high-touch profit credit)",
        "intent_clock": "clock_us(day, t+1, 0)",
        "observation": "latest RAW NBBO state at the intent clock (never pre-filtered)",
        "firm_fresh_rule": "valid, un-crossed, R-only, age <= 2s (an operational gate on "
        "SENDING, never a claim that an older NBBO expired)",
        "placement_price": "floor_to_cent(intent bid) or floor_to_cent(intent mid); a "
        "passive limit is NEVER rounded up into the ask",
        "quantity": "q = floor(1000 / (limit * (1 + 150bps/2))) fixed at the INTENT, "
        "before the acknowledgement, never re-floored from a later print",
        "reservation": "q * limit * (1 + 150bps/2), worst-case, so every reported rung "
        "stays inside the $1,000 ticket",
        "intent_depth_gate": None,
        "spread_gate": None,
        "spread_gate_note": (
            "the passive placement is not a marketable take, so no intent-ask depth or "
            "spread-vs-forecast bar gates the send; the binding capacity test happens at "
            "the first potential touch print, and the spread saving is realized inside "
            "the entry price (never added back as a credit)"
        ),
        "submit_ack_clock": "intent + 250ms",
        "cancel_submit_clock": "submit_ack + TTL",
        "cancel_ack_clock": "cancel_submit + 250ms (a fill can occur inside this window)",
        "visible_window": "[submit_ack, cancel_ack]; a print between the intent and the "
        "acknowledgement is invisible (the order is not working yet)",
        "primary_price_proof": "the FIRST print in the visible window whose ASK <= posted "
        "limit: FIRM (valid, un-crossed, R-only) with ASK depth >= q -> conditional "
        "quote-touch limit fill at the ACTUAL ask of that print (never the touched bid, "
        "never a same-bar low, never a trade price); the scan stops there (no best future "
        "quote); ambiguous book at the touch or depth < q -> PARTIAL/UNKNOWN, cash held to "
        "the session end, no skip to a later full-size print; no visible ASK <= limit -> "
        "rested unfilled, cash released at the CANCEL ACK",
        "marketable_at_placement": "a firm state with ASK <= limit at the acknowledgement "
        "fills at that print's own ask (locked/sub-cent book), under the same depth rule; "
        "an ASK already inside the limit on a NON-firm state at the acknowledgement is an "
        "UNKNOWN (the order's own fill state is unobservable)",
        "unknown_entry_rule": "hidden liquidity, routed-exchange fills and queue position "
        "are unmeasured; the explicit L1 model is the only fill evidence used",
        "no_order_rule": "q == 0 is a KNOWN no-order (no order sent, cash unfilled, never "
        "a priced zero-return fill, never an UNKNOWN)",
        "session_bound_rule": "an order is only placed when its whole working window "
        "closes strictly before the RTH last minute begins, so the capped 60-minute exit "
        "due intent is always strictly after the actual fill clock",
    },
    "exit": {
        "hold": "60 minutes after the ACTUAL fill clock, due at the minute clock at/after "
        "(fill + 60min), capped at the panel's session_end, never earlier than the fill",
        "submission": "FRESH-QUOTE-GATED (shared submission_clock): submit at the due "
        "intent if the latest raw state is firm and fresh, else hold to the FIRST eligible "
        "regular print at/after the due intent inside RTH and submit at its timestamp",
        "arrival_clock": "actual submission + 250ms",
        "pricing": "shared priced_leg MARKET semantics: fresh print priced at the arrival; "
        "valid-but-stale at arrival -> UNKNOWN (never a later favourable print); invalid / "
        "non-regular / absent book -> the submitted order conditionally rests to the first "
        "eligible print, whose own timestamp is the REAL execution clock",
        "quantity": "the causal entry q, never re-sized",
        "depth_rule": "exit BID top-of-book < q -> UNKNOWN partial capacity, never a "
        "guaranteed full fill",
        "high_target_fiction": None,
    },
    "book": {
        "slots": MAX_SLOTS,
        "ticket_usd": ORDER_BUDGET,
        "book_usd": BOOK,
        "leverage": False,
        "one_position_per_ticker": True,
        "cooldown": "flat 15 minutes after the ACTUAL exit clock",
        "max_attempts_per_ticker_per_day": MAX_ATTEMPTS,
        "cash_reservation": "q * limit * (1 + 150bps/2) at the highest reported rung",
        "simultaneous_clock_rule": "every intent of a clock is funded BEFORE any fill, "
        "cancel or unknown outcome is observed; no fourth same-clock substitution after an "
        "unfilled intent",
        "unknown_rule": "an UNKNOWN position keeps its slot and blocks its ticker until it "
        "resolves or the session ends; cash is never freed at a planned time",
        "release_rule": "cash returns at the actual priced exit, at the cancel ACK of an "
        "order with no possible visible fill, or at the session end for an unresolved "
        "position; a chronic working order consumes its slot for its whole TTL",
    },
    "selection": {
        "objective": "2023 validation actual-touch known contribution dollars per full "
        "calendar day",
        "selection_cost_bps": SELECT_COST,
        "cost_ladder_bps": list(RUNG_COSTS),
        "basis": "PARTIAL: unknown executions are excluded from the numerator and reported "
        "beside it; the whole-day full-loss lower bound is a separate bound, never an "
        "expected-loss estimate and never the objective",
        "power_floors": None,
        "median_or_tail_gates": None,
        "bootstrap_or_ci": None,
        "n100_gate": None,
        "cost_or_count_kill": None,
        "stress_kill": None,
        "frozen_before_late_inspection": True,
        "freeze_artifact": "selection_freeze.json (written after the validation surface, "
        "before any late file is read)",
    },
    "costs": {
        "rung_bps": list(RUNG_COSTS),
        "primary_selection_rung_bps": SELECT_COST,
        "selection_rung_is_one_scenario_not_a_hurdle": True,
        "spread_recharge": False,
        "spread_note": (
            "the observed ASK/BID touch prices already carry the spread; each rung charges "
            "only the extra residual, so the spread is never re-charged and the passive "
            "placement's spread saving is never added back as a credit"
        ),
        "scenario_nature": (
            "every rung is an EXTRA residual fee/slippage scenario charged ON TOP of the "
            "ACTUAL observed ASK entry / BID exit touch prices, NOT total modeled friction "
            "and NOT an actual broker fee"
        ),
        "low_fee_opportunity_label": (
            "0..150 bps describe a LOW-FEE OPPORTUNITY set, not minimum real provider fees: "
            "a primary US broker's regular schedule (zero commission, SEC 20.6 per $1M of "
            "sell value, CAT 0.000003 per side, daily cent rounding) is typically well "
            "under 1bp on a $1,000-$3,000 ticket, so the actual provider fees are far "
            "below the 25bps scenario; the 0/5bps sensitivity is reported beside it and no "
            "rung vetoes a candidate; actual provider fees are sourced in the separate "
            "provider-fee ledger, never asserted here"
        ),
        "quantity_sizing_rung_bps": MAX_RUNG_COST,
        "reservation_rule": "reserved = q * limit * (1 + 150bps/2) with q sized at the "
        "highest rung, so reserved <= $1,000 for every rung",
    },
    "queue_bounds_diagnostic": {
        "primary_basis": "visible displayed L1 ASK depth at the first potential touch "
        "print",
        "venue_known": False,
        "queue_position_known": False,
        "queue_advance_model": None,
        "maker_rebate_assumed": False,
        "selected_as_primary": False,
        "note": (
            "venue routing and time priority are unmeasured in this substrate, so no queue "
            "advance is modeled and no market-maker rebate is invented; the reported "
            "bounds use displayed L1 depth only and sit beside - never in place of - the "
            "primary fill counts"
        ),
    },
    "prior_data": {
        "validation_block": (
            "2023-01-01..2023-12-31: explored before this replay; not pristine, not "
            "previously unknown"
        ),
        "late_block": (
            "2025-02-01..2026-05-31: previously explored BEFORE this replay; NOT pristine "
            "and NOT previously unknown, so every late number is DISCOVERY-NOT-VALIDATED"
        ),
        "model_fit_block": (
            "2021-02..2022-12, the two years the stored bundle records; this producer "
            "never refits, extends or re-weights it and never replays the fit block"
        ),
    },
    "labels": {
        "traded_basis": "real NBBO ASK (passive limit) entry -> BID exit at the declared "
        "clocks",
        "model_basis": "minute-open payoff proxy (t -> t+60 actual minute opens)",
        "proxy_labels_used_as_entry_filter": False,
        "unfilled_entry_status_used_as_entry_filter": False,
    },
    "no_live_orders": True,
    "supplements": {
        "merge_order": ["ranked SIP day cache"] + [str(r) for r in SUPPLEMENT_ROOTS],
        "dedup_rule": (
            "unique (symbol, ts_utc) keep=first in that order: the ORIGINAL print always "
            "wins, a supplement may ONLY add timestamps and never silently replaces or "
            "revises an original print"
        ),
        "quote_universe_acquire_root": str(ACQUIRE_ROOT),
        "acquisition_awaited": (
            "the read-only quote-universe acquisition root is merged when it exists; a "
            "ticker with no merged stream is a DATA UNKNOWN acquisition, never cash"
        ),
    },
}


def sha256_bytes(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def producer_sha256() -> str:
    return aqf.sha256_file(Path(__file__).resolve())


def write_json_atomic(path: Path, payload: dict) -> None:
    aqf.write_json_atomic(path, payload)


def supplement_digest(roots: tuple[Path, ...]) -> tuple[str, list[dict]]:
    return aqf.supplement_digest(roots)


def view_key(theta: float, placement: str, ttl_s: int) -> str:
    return f"h{HEAD}|{theta:.4f}|{placement}|ttl{int(ttl_s)}"


def view_label(theta: float, placement: str, ttl_s: int) -> str:
    return f"h{HEAD}_thr{int(round(theta * 10000))}bps_{placement}_ttl{int(ttl_s)}s"


@dataclass(frozen=True)
class View:
    """One (bar, placement, TTL) cell. Same model, same universe; only the order differs."""

    threshold: float
    placement: str
    ttl_s: int

    @property
    def key(self) -> str:
        return view_key(self.threshold, self.placement, self.ttl_s)

    @property
    def label(self) -> str:
        return view_label(self.threshold, self.placement, self.ttl_s)


VIEWS = tuple(
    View(thr, placement, ttl)
    for thr in THRESHOLDS
    for placement in PLACEMENTS
    for ttl in TTLS_S
)
VIEWS_BY_KEY = {v.key: v for v in VIEWS}


# ----- passive placement physics ----------------------------------------------
def passive_limit_price(placement: str, bid: float, ask: float) -> float:
    """Posted passive limit: the intent BID or MID, floored DOWN to the cent.

    A passive limit is never rounded up: rounding up could push a sub-cent-spread order
    onto (or through) the ask and silently turn a resting order into a marketable take.
    Floored, the limit is at or below the observed bid (or exactly the ask on a locked
    book, which is then reported as a placement that saved nothing).
    """
    ref = float(bid) if placement == "bid" else 0.5 * (float(bid) + float(ask))
    if not math.isfinite(ref) or ref <= 0:
        return 0.0
    return math.floor(ref * 10**TICK_FLOOR_DECIMALS) / 10**TICK_FLOOR_DECIMALS


def placement_saving_bps(limit_price: float, ask: float) -> float:
    """Bps of the observed ask the placement gives up by resting below it.

    Realized inside the fill price, never added back as a credit on top of a rung.
    """
    if not ask:
        return None
    return (float(ask) - float(limit_price)) / float(ask) * 10_000.0


def causal_quantity(limit_price: float, cost_bps: float = MAX_RUNG_COST) -> int:
    """Fee-funded integer quantity from the INTENT limit at this study's $1,000 ticket.

    The shared kernel helper hardcodes the $250 ticket, so the ticket is restated here:
    identical formula (highest reported rung, so one causal q funds every rung), this
    study's own budget. q is fixed BEFORE the acknowledgement and never re-floored from
    a later print; a limit at or below zero is a KNOWN no-order.
    """
    if limit_price <= 0:
        return 0
    return int(ORDER_BUDGET // (float(limit_price) * (1.0 + cost_bps / 20_000.0)))


def intent_gate(facts: dict, placement: str) -> tuple[str, bool]:
    """The send gate for one placement of the intent observation.

    The intent-level part (an acquired stream, a firm, fresh, valid, un-crossed, R-only
    latest print) is shared by both placements; the funded quantity is placement-specific
    because the posted limit - and therefore q - differs between the bid and the mid. The
    freshness gate is an operational gate on SENDING, never a claim that an older NBBO
    expired.
    """
    if facts["intent_status"] == "quote_not_acquired":
        return "quote_not_acquired", False
    if facts["intent_status"] != "quoted":
        return "intent_not_firm_fresh", False
    age = facts["intent_age_s"]
    if age is None or not math.isfinite(age) or float(age) > MAX_AGE_S:
        return "intent_not_firm_fresh", False
    limit = facts[f"limit_{placement}"]
    if limit is None or float(limit) <= 0 or int(facts[f"qty_{placement}"]) < 1:
        return "no_order_min_capital", False
    return "eligible", True


def intent_state_reason(facts: dict) -> str:
    """The placement-independent intent verdict reported in the day coverage."""
    if facts["intent_status"] == "quote_not_acquired":
        return "quote_not_acquired"
    if facts["intent_status"] != "quoted":
        return "intent_not_firm_fresh"
    age = facts["intent_age_s"]
    if age is None or not math.isfinite(age) or float(age) > MAX_AGE_S:
        return "intent_not_firm_fresh"
    return "eligible"


def intent_facts(sq: aqf.SymbolQuotes | None, intent_us: int) -> dict:
    """The causal intent observation and the passive limits/quantities it implies.

    Both placements are priced from the SAME intent print, so the bid and mid views of a
    state are comparable and a locked book (identical floored limits) is visible.
    ``intent_reason`` is the placement-independent verdict; the replay re-evaluates the
    funded quantity per placement.
    """
    base = {
        "intent_bid": None,
        "intent_ask": None,
        "intent_age_s": None,
        "intent_spread_bps": None,
        "intent_ask_shares": None,
        "limit_bid": None,
        "qty_bid": 0,
        "reserved_bid": 0.0,
        "limit_mid": None,
        "qty_mid": 0,
        "reserved_mid": 0.0,
        "placement_collapsed": False,
    }
    if sq is None:
        base["intent_status"] = "quote_not_acquired"
        base["intent_reason"] = "quote_not_acquired"
        return base
    q, status = sq.latest_raw(intent_us)
    if q is None:
        base["intent_status"] = status
        base["intent_reason"] = "intent_not_firm_fresh"
        return base
    base["intent_status"] = "quoted"
    base.update(
        {
            "intent_bid": q["bid"],
            "intent_ask": q["ask"],
            "intent_age_s": q["age_s"],
            "intent_spread_bps": q["spread_bps"],
            "intent_ask_shares": q["ask_shares"],
        }
    )
    for placement in PLACEMENTS:
        limit = passive_limit_price(placement, q["bid"], q["ask"])
        qty = causal_quantity(limit)
        base[f"limit_{placement}"] = limit
        base[f"qty_{placement}"] = int(qty)
        base[f"reserved_{placement}"] = (
            float(aqf.reserved_usd(qty, limit)) if qty >= 1 else 0.0
        )
    base["placement_collapsed"] = bool(base["limit_bid"] == base["limit_mid"])
    base["intent_reason"] = intent_state_reason(base)
    return base


def _touch_facts(sq: aqf.SymbolQuotes, i: int) -> dict:
    """The observable facts of one RAW print, referenced to its OWN timestamp."""
    return sq.quote(i, int(sq.stamps[i]))


def passive_entry_leg(
    sq: aqf.SymbolQuotes,
    submit_ack_us: int,
    cancel_ack_us: int,
    limit_price: float,
    qty: int,
) -> tuple[dict | None, str]:
    """The passive working window: the FIRST visible potential touch, adjudicated once.

    A DAY limit legitimately RESTS, so the window is every RAW print in
    [submit_ack, cancel_ack] (the cancellation's own latency is inside the window, so a
    fill there still counts). The scan is chronological and stops at the FIRST print whose
    ASK is at or below the posted limit - the first potential touch - and that print is
    adjudicated exactly once:

    * firm (valid, un-crossed, R-only) with ASK depth >= q -> a conditional quote-touch
      limit fill, priced at the ACTUAL ask of THAT print (never the touched bid, never a
      same-bar low, never a trade price, and never a later, cheaper or deeper print);
    * firm but ASK depth < q -> PARTIAL/UNKNOWN (no skip forward to a full-size print);
    * not firm (crossed / non-regular / non-positive) -> an ambiguous book UNKNOWN;
    * a firm state already on the book at the acknowledgement with ASK <= limit -> the
      order is marketable at placement and fills at that print's own ask under the same
      depth rule;
    * no ASK at or below the limit anywhere in the window -> the resting order is
      cancelled unfilled, so the cash is released at the CANCEL ACK (hidden queue
      position, hidden liquidity and routed-exchange fills are unmeasured).

    There is no freshness gate inside the window: a working order's clock IS each print's
    own timestamp, so a touch print is fresh by construction. The operational freshness
    gate applies to SENDING (the intent observation) and to the market exit only.
    """
    limit = float(limit_price)
    # (1) the state already on the book when the order is acknowledged.
    i0 = sq.raw_index(int(submit_ack_us))
    if i0 >= 0 and bool(sq.eligible[i0]):
        if float(sq.ask[i0]) <= limit + 1e-12:
            return _adjudicate_touch(sq, i0, limit, int(qty))
    elif i0 >= 0 and float(sq.ask[i0]) <= limit + 1e-12:
        # The book is not firm at the acknowledgement and the printed ask is already
        # inside the limit: the order's own fill state is not observable.
        return None, "unknown_entry_ambiguous_book_at_ack"
    # (2) the working order rests until the first potential touch inside the window.
    i = int(np.searchsorted(sq.stamps, int(submit_ack_us), side="right"))
    n = len(sq.stamps)
    while i < n and int(sq.stamps[i]) <= int(cancel_ack_us):
        if float(sq.ask[i]) <= limit + 1e-12:
            return _adjudicate_touch(sq, i, limit, int(qty))
        i += 1
    return None, "rested_unfilled_no_visible_touch"


def _adjudicate_touch(
    sq: aqf.SymbolQuotes, i: int, limit_price: float, qty: int
) -> tuple[dict | None, str]:
    """One adjudication of the first potential touch: firmness, then displayed depth."""
    if not bool(sq.eligible[i]):
        return None, "unknown_entry_ambiguous_book_at_touch"
    if float(sq.ask_shares[i]) < qty:
        return None, "unknown_entry_partial_depth_at_touch"
    return _touch_facts(sq, i), "conditional_passive_fill"


# ----- clocks -----------------------------------------------------------------
# The ET-minute clock cache and the 250ms order latency are the SHARED published kernel
# helpers, so every producer of this family stamps the same clocks; this module adds only
# the one clock the passive order needs: the minute index of a fill timestamp.
minute_us = aqf.minute_us
arrival_us = aqf.arrival_us


def minute_of(day: str, ts_us: int) -> int:
    """ET minute index of one UTC-microsecond clock, using the panel's own indexing."""
    return int((int(ts_us) - minute_us(day, 0)) // 60_000_000)


def net_usd(quantity: int, exit_bid: float, entry_ask: float, cost_bps: float) -> float:
    """Realized dollars of the causal round trip at one residual rung (fees both legs)."""
    return aqf.net_usd(quantity, exit_bid, entry_ask, cost_bps)

# ----- per-day state artifacts (incremental, resumable) -----------------------
STATE_COLUMNS = (
    "day",
    "ticker",
    "t",
    "admit_t",
    "entry_et",
    "entry_open",
    "entry_status",
    "session_end",
    "proxy_gross_60",
    "pred",
    "entry_minute",
    "intent_us",
    "intent_status",
    "intent_reason",
    "intent_bid",
    "intent_ask",
    "intent_age_s",
    "intent_spread_bps",
    "intent_ask_shares",
    "limit_bid",
    "qty_bid",
    "reserved_bid",
    "limit_mid",
    "qty_mid",
    "reserved_mid",
    "placement_collapsed",
)
STATE_TYPES = {
    "day": pl.String,
    "ticker": pl.String,
    "t": pl.Int64,
    "admit_t": pl.Int64,
    "entry_et": pl.Int64,
    "entry_open": pl.Float64,
    "entry_status": pl.String,
    "session_end": pl.Int64,
    "proxy_gross_60": pl.Float64,
    "pred": pl.Float64,
    "entry_minute": pl.Int64,
    "intent_us": pl.Int64,
    "intent_status": pl.String,
    "intent_reason": pl.String,
    "intent_bid": pl.Float64,
    "intent_ask": pl.Float64,
    "intent_age_s": pl.Float64,
    "intent_spread_bps": pl.Float64,
    "intent_ask_shares": pl.Float64,
    "limit_bid": pl.Float64,
    "qty_bid": pl.Int64,
    "reserved_bid": pl.Float64,
    "limit_mid": pl.Float64,
    "qty_mid": pl.Int64,
    "reserved_mid": pl.Float64,
    "placement_collapsed": pl.Boolean,
}


def empty_states() -> pl.DataFrame:
    return pl.DataFrame(schema={c: STATE_TYPES[c] for c in STATE_COLUMNS})


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


def score_day(day: str, model: dict, data_root: Path, supplemental_roots: tuple[Path, ...]):
    """Score one session's liquidity-qualified states and observe each intent quote.

    One panel frame, one quote frame and one day's states are resident at a time; the full
    corpus is never concatenated. Both passive placements are priced from the SAME causal
    intent print, and the panel's own minute-open labels are carried along for the basis
    comparison only - never as an entry filter.
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
        "intent_reason_counts": {},
        "placement_collapsed": 0,
        "quote_not_acquired": 0,
    }
    if not cand.height:
        return empty_states(), cov
    tickers = sorted(set(cand["ticker"].to_list()))
    cov["tickers_qualified"] = len(tickers)
    preds = np.asarray(model["lgbm"].predict(feature_matrix(cand)), dtype=float)
    quote_frame = aqf.quote_day_frame(data_root, day, set(tickers), supplemental_roots)
    streams = aqf.symbol_quotes(quote_frame, day)
    cov["tickers_with_quote_stream"] = len(streams)
    if not aqf.day_quote_path(data_root, day).exists():
        cov["quote_day_file_present"] = False
    del quote_frame

    rows = cand.select(
        [
            "day",
            "ticker",
            "t",
            "admit_t",
            "entry_et",
            "entry_open",
            "entry_status",
            "session_end",
            "gross_60",
        ]
    ).to_dicts()
    session_end = int(rows[0]["session_end"])
    intent_counts: Counter = Counter()
    reason_counts: Counter = Counter()
    out: list[dict] = []
    for i, r in enumerate(rows):
        entry_minute = int(r["t"]) + 1
        intent_us = minute_us(day, entry_minute)
        sq = streams.get(r["ticker"])
        facts = intent_facts(sq, intent_us)
        intent_counts[facts["intent_status"]] += 1
        reason_counts[facts["intent_reason"]] += 1
        if facts["placement_collapsed"]:
            cov["placement_collapsed"] += 1
        rec = {
            "day": day,
            "ticker": r["ticker"],
            "t": int(r["t"]),
            "admit_t": int(r["admit_t"]) if r["admit_t"] is not None else None,
            "entry_et": int(r["entry_et"]) if r["entry_et"] is not None else None,
            "entry_open": r["entry_open"],
            "entry_status": r["entry_status"],
            "session_end": session_end,
            "proxy_gross_60": r["gross_60"],
            "pred": float(preds[i]),
            "entry_minute": entry_minute,
            "intent_us": intent_us,
        }
        rec.update(facts)
        out.append(rec)
    cov["intent_status_counts"] = dict(intent_counts)
    cov["intent_reason_counts"] = dict(reason_counts)
    cov["quote_not_acquired"] = int(reason_counts.get("quote_not_acquired", 0))
    frame = pl.DataFrame(
        {c: [rec[c] for rec in out] for c in STATE_COLUMNS},
        schema={c: STATE_TYPES[c] for c in STATE_COLUMNS},
    )
    return frame, cov


def state_paths(out_dir: Path, day: str) -> tuple[Path, Path]:
    return out_dir / "states" / f"{day}.parquet", out_dir / "states" / f"{day}.cov.json"


def score_block(
    out_dir: Path, model: dict, days: list[str], resume: bool, supplemental_roots: tuple[Path, ...]
) -> dict:
    """Build (or resume) every day's scored states atomically, one day at a time."""
    producer = producer_sha256()
    contract_sha = sha256_bytes(CONTRACT)
    sup_digest, sup_entries = supplement_digest(supplemental_roots)
    model_sha = aqf.sha256_file(MODEL_DIR / f"payoff_h{HEAD}.joblib")
    todo, infos = [], []
    coverage = {
        "days_total": len(days),
        "days_cached": 0,
        "days_scored": 0,
        "qualified_rows": 0,
        "intent_status_counts": {},
        "intent_reason_counts": {},
        "placement_collapsed": 0,
        "missing_quote_day_files": [],
        "days_without_quote_stream": [],
    }
    intent_totals: Counter = Counter()
    reason_totals: Counter = Counter()
    for day in days:
        qpath, cpath = state_paths(out_dir, day)
        expect = sha256_bytes(
            {
                "day": day,
                "producer": producer,
                "contract": contract_sha,
                "supplements": sup_digest,
                "model": model_sha,
                "schema": STATES_SCHEMA,
            }
        )
        fresh = False
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
        states, cov = score_day(day, model, DATA_ROOT, supplemental_roots)
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
                "resume_hash": sha256_bytes(
                    {
                        "day": day,
                        "producer": producer,
                        "contract": contract_sha,
                        "supplements": sup_digest,
                        "model": model_sha,
                        "schema": STATES_SCHEMA,
                    }
                ),
                "producer_sha256": producer,
                "contract_sha256": contract_sha,
                "supplement_digest": sup_digest,
                "states_sha256": aqf.sha256_file(qpath),
                "rows_schema": STATES_SCHEMA,
                "coverage": cov,
            },
        )
        infos.append({"day": day, "resumed": False, "rows": int(states.height), "coverage": cov})
        if n % 25 == 0 or n == len(todo):
            print(
                f"[score] {n}/{len(todo)} days, last {day}, qualified_rows="
                f"{cov['qualified_rows']}, quoted_streams={cov['tickers_with_quote_stream']}",
                flush=True,
            )
    for info in infos:
        cov = info["coverage"] or {}
        coverage["qualified_rows"] += int(cov.get("qualified_rows", 0))
        coverage["placement_collapsed"] += int(cov.get("placement_collapsed", 0))
        intent_totals.update(cov.get("intent_status_counts") or {})
        reason_totals.update(cov.get("intent_reason_counts") or {})
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
            "intent_reason_counts": dict(reason_totals),
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
# ----- per-view causal replay -------------------------------------------------
DAILY_COLUMNS = (
    "day",
    "view_key",
    "head",
    "threshold",
    "placement",
    "ttl_s",
    "signals",
    "dedup_identical_limits",
    "intents_funded",
    "attempts",
    "fills",
    "known_fills",
    "unknown_fills",
    "rested_unfilled",
    "below_threshold_states",
    "peak_reserved_usd",
    "positions_peak",
    "positions_mean",
    "no_order_min_capital",
    "quote_not_acquired",
    "intent_not_firm_fresh",
    "ttl_window_beyond_session",
    "overlap_skips",
    "cooldown_skips",
    "max_attempt_skips",
    "cash_or_slot_skips",
)
DAILY_TYPES = {
    "day": pl.String,
    "view_key": pl.String,
    "head": pl.Int64,
    "threshold": pl.Float64,
    "placement": pl.String,
    "ttl_s": pl.Int64,
    "signals": pl.Int64,
    "dedup_identical_limits": pl.Int64,
    "intents_funded": pl.Int64,
    "attempts": pl.Int64,
    "fills": pl.Int64,
    "known_fills": pl.Int64,
    "unknown_fills": pl.Int64,
    "rested_unfilled": pl.Int64,
    "below_threshold_states": pl.Int64,
    "peak_reserved_usd": pl.Float64,
    "positions_peak": pl.Int64,
    "positions_mean": pl.Float64,
    "no_order_min_capital": pl.Int64,
    "quote_not_acquired": pl.Int64,
    "intent_not_firm_fresh": pl.Int64,
    "ttl_window_beyond_session": pl.Int64,
    "overlap_skips": pl.Int64,
    "cooldown_skips": pl.Int64,
    "max_attempt_skips": pl.Int64,
    "cash_or_slot_skips": pl.Int64,
}
for _rung in RUNG_COSTS:
    DAILY_COLUMNS += (f"known_usd_{int(_rung)}", f"lower_bound_usd_{int(_rung)}")
    DAILY_TYPES[f"known_usd_{int(_rung)}"] = pl.Float64
    DAILY_TYPES[f"lower_bound_usd_{int(_rung)}"] = pl.Float64
TRADE_COLUMNS = (
    "day",
    "view_key",
    "head",
    "threshold",
    "placement",
    "ttl_s",
    "ticker",
    "t",
    "entry_minute",
    "intent_us",
    "score",
    "pred",
    "limit_price",
    "qty",
    "reserved_usd",
    "attempt_index",
    "positions_at_entry",
    "intent_status",
    "intent_bid",
    "intent_ask",
    "intent_age_s",
    "intent_spread_bps",
    "placement_saving_bps",
    "placement_collapsed",
    "submit_ack_us",
    "cancel_submit_us",
    "cancel_ack_us",
    "entry_status",
    "entry_touched",
    "entry_touch_kind",
    "entry_ask",
    "entry_bid",
    "entry_age_s",
    "entry_quote_us",
    "entry_fill_us",
    "entry_depth_shares",
    "entry_depth_ratio",
    "fill_delay_s",
    "exit_minute",
    "exit_submit_us",
    "exit_submit_status",
    "exit_arrival_us",
    "exit_bid",
    "exit_age_s",
    "exit_quote_us",
    "exit_price_status",
    "exit_status",
    "exit_depth_shares",
    "actual_exit_us",
    "release_reason",
    "hold_minutes",
    "fill_status",
    "proxy_gross",
)
TRADE_TYPES = {
    "day": pl.String,
    "view_key": pl.String,
    "head": pl.Int64,
    "threshold": pl.Float64,
    "placement": pl.String,
    "ttl_s": pl.Int64,
    "ticker": pl.String,
    "t": pl.Int64,
    "entry_minute": pl.Int64,
    "intent_us": pl.Int64,
    "score": pl.Float64,
    "pred": pl.Float64,
    "limit_price": pl.Float64,
    "qty": pl.Int64,
    "reserved_usd": pl.Float64,
    "attempt_index": pl.Int64,
    "positions_at_entry": pl.Int64,
    "intent_status": pl.String,
    "intent_bid": pl.Float64,
    "intent_ask": pl.Float64,
    "intent_age_s": pl.Float64,
    "intent_spread_bps": pl.Float64,
    "placement_saving_bps": pl.Float64,
    "placement_collapsed": pl.Boolean,
    "submit_ack_us": pl.Int64,
    "cancel_submit_us": pl.Int64,
    "cancel_ack_us": pl.Int64,
    "entry_status": pl.String,
    "entry_touched": pl.Boolean,
    "entry_touch_kind": pl.String,
    "entry_ask": pl.Float64,
    "entry_bid": pl.Float64,
    "entry_age_s": pl.Float64,
    "entry_quote_us": pl.Int64,
    "entry_fill_us": pl.Int64,
    "entry_depth_shares": pl.Float64,
    "entry_depth_ratio": pl.Float64,
    "fill_delay_s": pl.Float64,
    "exit_minute": pl.Int64,
    "exit_submit_us": pl.Int64,
    "exit_submit_status": pl.String,
    "exit_arrival_us": pl.Int64,
    "exit_bid": pl.Float64,
    "exit_age_s": pl.Float64,
    "exit_quote_us": pl.Int64,
    "exit_price_status": pl.String,
    "exit_status": pl.String,
    "exit_depth_shares": pl.Float64,
    "actual_exit_us": pl.Int64,
    "release_reason": pl.String,
    "hold_minutes": pl.Float64,
    "fill_status": pl.String,
    "proxy_gross": pl.Float64,
}
for _rung in RUNG_COSTS:
    TRADE_COLUMNS += (f"net_usd_{int(_rung)}",)
    TRADE_TYPES[f"net_usd_{int(_rung)}"] = pl.Float64

UNKNOWN_ENTRY_STATUSES = (
    "unknown_entry_ambiguous_book_at_ack",
    "unknown_entry_ambiguous_book_at_touch",
    "unknown_entry_partial_depth_at_touch",
)


def empty_daily() -> pl.DataFrame:
    return pl.DataFrame(schema={c: DAILY_TYPES[c] for c in DAILY_COLUMNS})


def empty_trades() -> pl.DataFrame:
    return pl.DataFrame(schema={c: TRADE_TYPES[c] for c in TRADE_COLUMNS})


def new_trade_record(day: str, r: dict, view: View, sq: aqf.SymbolQuotes) -> dict:
    """Every clock and price of one funded passive order, typed up front."""
    placement = view.placement
    intent_us = int(r["intent_us"])
    submit_ack_us = arrival_us(intent_us)
    cancel_submit_us = submit_ack_us + int(view.ttl_s) * 1_000_000
    cancel_ack_us = cancel_submit_us + LATENCY_US
    limit_price = float(r[f"limit_{placement}"])
    qty = int(r[f"qty_{placement}"])
    record = {
        "day": day,
        "view_key": view.key,
        "head": HEAD,
        "threshold": view.threshold,
        "placement": placement,
        "ttl_s": int(view.ttl_s),
        "ticker": r["ticker"],
        "t": int(r["t"]),
        "entry_minute": int(r["entry_minute"]),
        "intent_us": intent_us,
        "score": float(r["pred"]),
        "pred": float(r["pred"]),
        "limit_price": limit_price,
        "qty": qty,
        "reserved_usd": float(r[f"reserved_{placement}"]),
        "proxy_gross": r["proxy_gross_60"],
        "attempt_index": None,
        "positions_at_entry": None,
        "intent_status": r["intent_status"],
        "intent_bid": r["intent_bid"],
        "intent_ask": r["intent_ask"],
        "intent_age_s": r["intent_age_s"],
        "intent_spread_bps": r["intent_spread_bps"],
        "placement_saving_bps": placement_saving_bps(limit_price, r["intent_ask"]),
        "placement_collapsed": bool(r["placement_collapsed"]),
        # the typed order lifetime: acknowledgement, working window, cancellation
        "submit_ack_us": submit_ack_us,
        "cancel_submit_us": cancel_submit_us,
        "cancel_ack_us": cancel_ack_us,
        "entry_status": None,
        "entry_touched": False,
        "entry_touch_kind": None,
        "entry_ask": None,
        "entry_bid": None,
        "entry_age_s": None,
        "entry_quote_us": None,
        "entry_fill_us": None,
        "entry_depth_shares": None,
        "entry_depth_ratio": None,
        "fill_delay_s": None,
        "exit_minute": None,
        "exit_submit_us": None,
        "exit_submit_status": None,
        "exit_arrival_us": None,
        "exit_bid": None,
        "exit_age_s": None,
        "exit_quote_us": None,
        "exit_price_status": None,
        "exit_status": None,
        "exit_depth_shares": None,
        "actual_exit_us": None,
        "release_reason": None,
        "hold_minutes": None,
        "fill_status": None,
    }
    for rung in RUNG_COSTS:
        record[f"net_usd_{int(rung)}"] = None
    return record


def resolve_trade(
    day: str, r: dict, view: View, sq: aqf.SymbolQuotes, session_end: int, session_end_us: int
) -> dict:
    """Resolve one funded passive intent: the resting limit window, then the exit.

    The limit, the quantity and the reservation were fixed at the intent clock, before the
    acknowledgement; nothing here re-derives them from a later print. The working window
    ends at the cancellation acknowledgement, so a fill inside the cancellation's own
    latency still counts; a resting order that never shows a visible ASK at or below its
    limit is cancelled unfilled and releases its cash at the CANCEL ACK. An UNKNOWN
    position keeps its slot, its cash and its ticker until it resolves or the session
    ends.
    """
    record = new_trade_record(day, r, view, sq)
    submit_ack_us = int(record["submit_ack_us"])
    cancel_ack_us = int(record["cancel_ack_us"])

    # ---- entry leg: the passive working window, adjudicated at its first visible touch
    eq, estat = passive_entry_leg(
        sq, submit_ack_us, cancel_ack_us, float(record["limit_price"]), int(record["qty"])
    )
    record["entry_status"] = estat
    record["entry_touched"] = bool(eq is not None or estat.startswith("unknown_entry_"))
    record["entry_touch_kind"] = {
        "conditional_passive_fill": "firm_full_displayed_depth",
        "unknown_entry_partial_depth_at_touch": "firm_short_displayed_depth",
        "unknown_entry_ambiguous_book_at_touch": "ambiguous_book",
        "unknown_entry_ambiguous_book_at_ack": "ambiguous_book_at_ack",
        "rested_unfilled_no_visible_touch": "no_visible_touch",
    }[estat]
    if eq is not None:
        record["entry_ask"] = float(eq["ask"])
        record["entry_bid"] = float(eq["bid"])
        record["entry_age_s"] = float(eq["age_s"])
        record["entry_quote_us"] = int(eq["quote_us"])
        # The order cannot be filled before it is acknowledged, so a marketable-at-
        # placement fill (whose price comes from the print already on the book) carries the
        # acknowledgement as its own clock and the print's timestamp as its price source.
        fill_us = max(int(eq["quote_us"]), submit_ack_us)
        record["entry_fill_us"] = fill_us
        record["entry_depth_shares"] = float(eq["ask_shares"])
        record["entry_depth_ratio"] = (
            float(eq["ask_shares"]) / float(record["qty"]) if int(record["qty"]) else None
        )
        record["fill_delay_s"] = (fill_us - submit_ack_us) / 1_000_000.0
    if estat in UNKNOWN_ENTRY_STATUSES:
        record["fill_status"] = "unknown_entry_execution"
        record["exit_status"] = estat
        record["actual_exit_us"] = session_end_us + 1
        record["release_reason"] = "session_end_unresolved"
        return record
    if estat == "rested_unfilled_no_visible_touch":
        # No position was ever opened: the ticket returns at the CANCEL ACK (never at the
        # cancellation submission, because a fill can occur inside that latency) and there
        # is no exit to anchor a cooldown on (the attempt still counts).
        record["fill_status"] = "rested_unfilled_no_visible_touch"
        record["actual_exit_us"] = cancel_ack_us
        record["release_reason"] = "cancel_ack_no_visible_touch"
        return record

    # ---- exit leg: 60-minute hold bounded by the RTH last clock, fresh-quote-gated.
    fill_us = int(record["entry_fill_us"])
    due_minute = min(minute_of(day, fill_us) + HOLD_MIN, session_end)
    due_us = minute_us(day, due_minute)
    if due_us <= fill_us:
        raise RuntimeError(
            f"[passive] exit due intent precedes the fill clock: {day} {r['ticker']} "
            f"fill={fill_us} due={due_us}"
        )
    record["exit_minute"] = int(due_minute)
    submit_us, submit_status = aqf.submission_clock(sq, due_us, session_end_us)
    record["exit_submit_us"] = submit_us
    record["exit_submit_status"] = submit_status
    if submit_us is None:
        record["fill_status"] = "unknown_exit_execution"
        record["exit_status"] = "unknown_exit_no_valid_regular_quote"
        record["actual_exit_us"] = session_end_us + 1
        record["release_reason"] = "session_end_unresolved"
        return record
    exit_arrival = arrival_us(submit_us)
    record["exit_arrival_us"] = exit_arrival
    xq, xstat, priced_at = aqf.priced_leg(sq, exit_arrival, session_end_us)
    record["exit_price_status"] = xstat
    if xq is None:
        record["fill_status"] = "unknown_exit_execution"
        record["exit_status"] = xstat
        record["actual_exit_us"] = session_end_us + 1
        record["release_reason"] = "session_end_unresolved"
        return record
    record["exit_bid"] = float(xq["bid"])
    record["exit_age_s"] = float(xq["age_s"])
    record["exit_quote_us"] = int(xq["quote_us"])
    record["exit_depth_shares"] = float(xq["bid_shares"])
    if float(xq["bid_shares"]) < int(record["qty"]):
        record["fill_status"] = "unknown_exit_execution"
        record["exit_status"] = "unknown_exit_partial_depth"
        record["actual_exit_us"] = session_end_us + 1
        record["release_reason"] = "session_end_unresolved"
        return record
    record["exit_status"] = "conditional_market_fill"
    record["fill_status"] = "conditional_fill"
    if priced_at is None:
        raise RuntimeError(
            f"[passive] priced exit without a price clock: {day} {r['ticker']} status={xstat}"
        )
    record["actual_exit_us"] = int(priced_at)
    record["release_reason"] = "actual_exit_priced_clock"
    record["hold_minutes"] = (int(priced_at) - fill_us) / 60_000_000.0
    for rung in RUNG_COSTS:
        record[f"net_usd_{int(rung)}"] = net_usd(
            record["qty"], record["exit_bid"], record["entry_ask"], rung
        )
    return record


def replay_day(
    day: str, states: pl.DataFrame, streams: dict[str, aqf.SymbolQuotes], view: View
) -> tuple[dict, list[dict]]:
    """One view, one session: passive resting entries under the funded $3,000 book.

    Chronological by intent clock. Within a clock the highest-forecast intents are funded
    (up to the slot and cash limits) BEFORE any fill, cancellation or unknown outcome is
    observed, so a fourth same-clock candidate is never substituted after an unfilled one.
    Identical (ticker, intent clock, posted limit) candidates are deduplicated to a single
    order. Cash returns only at an ACTUAL priced exit, at the cancel acknowledgement of an
    order with no possible visible fill, or at the session end.
    """
    daily = dict.fromkeys(DAILY_COLUMNS, 0)
    daily.update(
        {
            "day": day,
            "view_key": view.key,
            "head": HEAD,
            "threshold": view.threshold,
            "placement": view.placement,
            "ttl_s": int(view.ttl_s),
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
    signals = states.filter(pl.col("pred") >= view.threshold).sort(
        ["entry_minute", "pred", "ticker"], descending=[False, True, False]
    )
    daily["signals"] = int(signals.height)
    daily["below_threshold_states"] = int(states.height - signals.height)
    if not signals.height:
        return daily, []
    groups: dict[int, list[dict]] = {}
    for r in signals.iter_rows(named=True):
        groups.setdefault(int(r["entry_minute"]), []).append(r)

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
        seen: dict[tuple[str, int, float], str] = {}
        admitted: list[dict] = []
        for r in groups[minute]:
            sym = r["ticker"]
            reason, ok = intent_gate(r, view.placement)
            if not ok:
                # a non-acquired stream, a non-firm intent print or an unfundable
                # (non-positive / sub-share) limit: all KNOWN causal outcomes, never an
                # UNKNOWN and never a priced zero-return fill
                daily[reason] += 1
                continue
            limit_price = float(r[f"limit_{view.placement}"])
            dedup_key = (sym, intent_us, limit_price)
            if dedup_key in seen:
                # identical bid/mid limits (or an identical posted limit) are ONE order
                daily["dedup_identical_limits"] += 1
                continue
            seen[dedup_key] = sym
            if arrival_us(intent_us) + int(view.ttl_s) * 1_000_000 + LATENCY_US >= session_end_us:
                # the whole working window must close before the RTH last minute begins
                daily["ttl_window_beyond_session"] += 1
                continue
            if sym in active:
                daily["overlap_skips"] += 1
                continue
            if intent_us < int(cooldown_until.get(sym, -(1 << 62))):
                daily["cooldown_skips"] += 1
                continue
            if int(attempts.get(sym, 0)) >= MAX_ATTEMPTS:
                daily["max_attempt_skips"] += 1
                continue
            if len(active) >= MAX_SLOTS or cash + 1e-9 < float(r[f"reserved_{view.placement}"]):
                daily["cash_or_slot_skips"] += 1
                continue
            attempts[sym] = int(attempts.get(sym, 0)) + 1
            cash -= float(r[f"reserved_{view.placement}"])
            active.add(sym)
            positions_samples.append(len(active))
            reserved_total += float(r[f"reserved_{view.placement}"])
            daily["intents_funded"] += 1
            daily["attempts"] += 1
            admitted.append(r)
        # Every intent of this clock is reserved before any outcome is observed.
        for r in admitted:
            record = resolve_trade(
                day, r, view, streams[r["ticker"]], session_end, session_end_us
            )
            record["attempt_index"] = int(attempts[r["ticker"]])
            record["positions_at_entry"] = len(active)
            status = record["fill_status"]
            release = int(record["actual_exit_us"])
            if status == "rested_unfilled_no_visible_touch":
                daily["rested_unfilled"] += 1
            else:
                daily["unknown_fills" if "unknown" in status else "fills"] += 1
                if status == "conditional_fill":
                    daily["known_fills"] += 1
                    for rung in RUNG_COSTS:
                        daily[f"known_usd_{int(rung)}"] += float(record[f"net_usd_{int(rung)}"])
                # A position - conditional or UNKNOWN - is open until its ACTUAL
                # resolution, and the ticker is then blocked for a flat 15 minutes.
                cooldown_until[r["ticker"]] = release + COOLDOWN_MIN * 60_000_000
            reserved = float(r[f"reserved_{view.placement}"])
            heapq.heappush(queue, (release, seq, r["ticker"], reserved))
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
    out_dir: Path, block: str, days: list[str], resume: bool, supplemental_roots: tuple[Path, ...]
) -> dict:
    """Replay every view on every day of one block, writing resumable per-day parts."""
    producer = producer_sha256()
    contract_sha = sha256_bytes(CONTRACT)
    sup_digest, _ = supplement_digest(supplemental_roots)
    model_sha = aqf.sha256_file(MODEL_DIR / f"payoff_h{HEAD}.joblib")
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
        "fills": 0,
        "unknown_fills": 0,
        "rested_unfilled": 0,
    }
    for day in days:
        dpath = root / f"{day}.parquet"
        tpath = root / f"{day}.trades.parquet"
        cpath = root / f"{day}.cov.json"
        _, spath = state_paths(out_dir, day)
        states_sha = None
        if spath.exists():
            try:
                states_sha = json.loads(spath.read_text()).get("states_sha256")
            except (json.JSONDecodeError, OSError):
                states_sha = None
            if states_sha is None:
                states_sha = aqf.sha256_file(spath)
        expect = sha256_bytes(
            {
                "day": day,
                "producer": producer,
                "contract": contract_sha,
                "supplements": sup_digest,
                "model": model_sha,
                "states": states_sha,
                "schema": REPLAY_SCHEMA,
            }
        )
        expects[day] = expect
        fresh = False
        prior = None
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
            todo.append(day)
    for n, day in enumerate(todo, 1):
        dpath = root / f"{day}.parquet"
        tpath = root / f"{day}.trades.parquet"
        cpath = root / f"{day}.cov.json"
        states = read_day_states(out_dir, day)
        states_path, _ = state_paths(out_dir, day)
        if not states_path.exists():
            raise SystemExit(
                f"[passive] scored states missing for {day} ({states_path}); run the score "
                "stage for the whole block before replaying it"
            )
        tickers = sorted(set(states["ticker"].to_list())) if states.height else []
        quote_frame = (
            aqf.quote_day_frame(DATA_ROOT, day, set(tickers), supplemental_roots)
            if tickers
            else None
        )
        streams = aqf.symbol_quotes(quote_frame, day)
        del quote_frame
        daily_rows, trade_rows = [], []
        day_cov = {
            "qualified_rows": int(states.height) if states.height else 0,
            "quote_streams": len(streams),
            "views": {},
        }
        for view in VIEWS:
            daily, trades = replay_day(day, states, streams, view)
            daily_rows.append(daily)
            trade_rows.extend(trades)
            day_cov["views"][view.key] = {
                "signals": daily["signals"],
                "attempts": daily["attempts"],
                "fills": daily["fills"],
                "unknown_fills": daily["unknown_fills"],
                "rested_unfilled": daily["rested_unfilled"],
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
        coverage["intents_funded"] += sum(
            v.get("attempts", 0) for v in (cov.get("views") or {}).values()
        )
        coverage["fills"] += sum(v.get("fills", 0) for v in (cov.get("views") or {}).values())
        coverage["unknown_fills"] += sum(
            v.get("unknown_fills", 0) for v in (cov.get("views") or {}).values()
        )
        coverage["rested_unfilled"] += sum(
            v.get("rested_unfilled", 0) for v in (cov.get("views") or {}).values()
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


def monthly_view(daily: pl.DataFrame) -> dict:
    """Per-month traded-day / fill / UNKNOWN / rested-unfilled accounting."""
    col = f"known_usd_{int(SELECT_COST)}"
    out = {}
    for month in sorted({str(d)[:7] for d in daily["day"].to_list()}):
        rows = daily.filter(pl.col("day").str.starts_with(month))
        out[month] = {
            "days_replayed": int(rows.height),
            "traded_days": int((rows["attempts"] > 0).sum()),
            "attempts": int(rows["attempts"].sum()),
            "fills": int(rows["fills"].sum()),
            "known_fills": int(rows["known_fills"].sum()),
            "unknown_fills": int(rows["unknown_fills"].sum()),
            "rested_unfilled": int(rows["rested_unfilled"].sum()),
            col: float(rows[col].sum()),
            f"mean_daily_{col}": float(rows[col].mean()),
        }
    return out


def yearly_view(daily: pl.DataFrame) -> dict:
    col = f"known_usd_{int(SELECT_COST)}"
    out = {}
    for year in sorted({str(d)[:4] for d in daily["day"].to_list()}):
        rows = daily.filter(pl.col("day").str.starts_with(year))
        out[year] = {
            "days_replayed": int(rows.height),
            "traded_days": int((rows["attempts"] > 0).sum()),
            "attempts": int(rows["attempts"].sum()),
            "fills": int(rows["fills"].sum()),
            "known_fills": int(rows["known_fills"].sum()),
            "unknown_fills": int(rows["unknown_fills"].sum()),
            col: float(rows[col].sum()),
            f"mean_daily_{col}": float(rows[col].mean()),
        }
    return out


def queue_bounds_diagnostic(trades_view: pl.DataFrame) -> dict:
    """Displayed-L1 BOUNDS only - a diagnostic, never the primary fill basis.

    Venue routing and time priority are unmeasured here, so nothing about queue advance
    or maker rebates is invented: the only observable capacity is the displayed top-of-book
    ASK depth at the first potential touch print. These bounds sit beside the primary
    counts and are never selected as the objective.
    """
    funded = int(trades_view.height)
    out = {
        "primary_basis": "visible displayed L1 ASK depth at the first potential touch print",
        "venue_known": False,
        "queue_position_known": False,
        "queue_advance_model": None,
        "maker_rebate_assumed": False,
        "selected_as_primary": False,
        "funded_intents": funded,
    }
    if not funded:
        out.update(
            {
                "first_touch_prints": 0,
                "conditional_fills": 0,
                "short_depth_touch_unknown": 0,
                "ambiguous_touch_unknown": 0,
                "no_visible_touch": 0,
                "mean_visible_ask_depth_at_touch_shares": None,
                "median_visible_ask_depth_at_touch_shares": None,
                "mean_depth_ratio_to_order_qty": None,
                "upper_bound_fills_if_displayed_depth_available": 0,
            }
        )
        return out
    kinds = dict(
        trades_view.group_by("entry_touch_kind").len().sort("len", descending=True).iter_rows()
    )
    touched = trades_view.filter(pl.col("entry_touched"))
    depths = (
        touched["entry_depth_shares"].drop_nulls().to_list() if touched.height else []
    )
    ratios = touched["entry_depth_ratio"].drop_nulls().to_list() if touched.height else []
    out.update(
        {
            "entry_touch_kind_counts": kinds,
            "first_touch_prints": int(touched.height),
            "conditional_fills": int(
                (touched["entry_touch_kind"] == "firm_full_displayed_depth").sum()
            ),
            "short_depth_touch_unknown": int(
                (touched["entry_touch_kind"] == "firm_short_displayed_depth").sum()
            ),
            "ambiguous_touch_unknown": int(
                touched["entry_touch_kind"].is_in(["ambiguous_book", "ambiguous_book_at_ack"]).sum()
            ),
            "no_visible_touch": int(
                (trades_view["entry_touch_kind"] == "no_visible_touch").sum()
            ),
            "mean_visible_ask_depth_at_touch_shares": (
                float(np.mean(depths)) if depths else None
            ),
            "median_visible_ask_depth_at_touch_shares": (
                float(np.median(depths)) if depths else None
            ),
            "mean_depth_ratio_to_order_qty": (float(np.mean(ratios)) if ratios else None),
            # the displayed book is the only capacity evidence, so the upper bound equals
            # the primary conditional count; a queue-position bound is NOT computable
            "upper_bound_fills_if_displayed_depth_available": int(
                (touched["entry_touch_kind"] == "firm_full_displayed_depth").sum()
            ),
        }
    )
    return out


def view_cell(daily: pl.DataFrame, trades: pl.DataFrame, view: View, days: list[str]) -> dict:
    """Every reported number for one view on one block's calendar (no floors, no gates)."""
    rows = daily.filter(pl.col("view_key") == view.key)
    n_days = len(days)
    if int(rows.height) != n_days:
        raise ValueError(
            f"view {view.key}: {rows.height} daily rows on disk for {n_days} calendar days; "
            "replay the whole block before aggregating"
        )
    rung_keys = [int(r) for r in RUNG_COSTS]
    known_total = {r: float(rows[f"known_usd_{r}"].sum()) for r in rung_keys}
    known_per_day = {r: known_total[r] / n_days for r in rung_keys}
    bound_per_day = {r: float(rows[f"lower_bound_usd_{r}"].sum()) / n_days for r in rung_keys}
    sel_series = rows[f"known_usd_{int(SELECT_COST)}"].to_list()
    cell = {
        "view_key": view.key,
        "label": view.label,
        "head": HEAD,
        "threshold": view.threshold,
        "placement": view.placement,
        "ttl_s": int(view.ttl_s),
        "days_replayed": n_days,
        "signals": int(rows["signals"].sum()),
        "dedup_identical_limits": int(rows["dedup_identical_limits"].sum()),
        "below_threshold_states": int(rows["below_threshold_states"].sum()),
        "intents_funded": int(rows["intents_funded"].sum()),
        "attempts": int(rows["attempts"].sum()),
        "fills": int(rows["fills"].sum()),
        "known_fills": int(rows["known_fills"].sum()),
        "unknown_fills": int(rows["unknown_fills"].sum()),
        "rested_unfilled": int(rows["rested_unfilled"].sum()),
        "traded_days": int((rows["attempts"] > 0).sum()),
        "positions_peak": int(rows["positions_peak"].max() or 0),
        "positions_mean": float(rows["positions_mean"].mean()),
        "known_usd": {str(r): known_total[r] for r in rung_keys},
        "known_usd_per_calendar_day": {str(r): known_per_day[r] for r in rung_keys},
        "full_loss_lower_bound_usd_per_calendar_day": {str(r): bound_per_day[r] for r in rung_keys},
        "dollars_per_year_252_on_book": {
            str(r): known_per_day[r] * ANNUAL_SESSIONS for r in rung_keys
        },
        "annualization": (
            f"mean daily known contribution x {ANNUAL_SESSIONS} sessions on the "
            f"${int(BOOK)} nominal book; a simple session-count convention, NOT a CAGR and "
            "NOT an account claim"
        ),
        "daily_known_usd_at_selection_rung": {
            "mean": (float(np.mean(sel_series)) if sel_series else None),
            "median": (float(np.median(sel_series)) if sel_series else None),
            "min": (float(np.min(sel_series)) if sel_series else None),
            "max": (float(np.max(sel_series)) if sel_series else None),
            "share_of_calendar_days_positive": (
                float(np.mean(np.asarray(sel_series) > 0)) if sel_series else None
            ),
            "note": "descriptive day-level accounting only; no CI, no median gate, no "
            "power floor and no bootstrap is used for selection or reporting of an edge",
        },
        "skip_reasons": {
            reason: int(rows[reason].sum())
            for reason in (
                "no_order_min_capital",
                "quote_not_acquired",
                "intent_not_firm_fresh",
                "ttl_window_beyond_session",
                "overlap_skips",
                "cooldown_skips",
                "max_attempt_skips",
                "cash_or_slot_skips",
            )
        },
        "monthly": monthly_view(rows),
        "yearly": yearly_view(rows),
    }
    trades_view = trades.filter(pl.col("view_key") == view.key)
    cell["queue_bounds_diagnostic"] = queue_bounds_diagnostic(trades_view)
    if trades_view.height:
        filled = trades_view.filter(pl.col("fill_status") == "conditional_fill")
        nets = {
            str(r): (filled[f"net_usd_{r}"].to_list() if filled.height else []) for r in rung_keys
        }
        touched = trades_view.filter(pl.col("entry_touched"))
        cell["trade_stats"] = {
            "funded_intents": int(trades_view.height),
            "conditional_fills": int(filled.height),
            "fill_rate_of_funded_intents": float(filled.height / trades_view.height),
            "fill_rate_of_touched_intents": (
                float(filled.height / touched.height) if touched.height else None
            ),
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
            "mean_placement_saving_bps_at_intent": (
                float(trades_view["placement_saving_bps"].mean())
                if trades_view.height
                else None
            ),
            "mean_proxy_gross_minute_open_basis": (
                float(filled["proxy_gross"].mean()) if filled.height else None
            ),
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
            "mean_fill_delay_s": (
                float(filled["fill_delay_s"].mean()) if filled.height else None
            ),
            "median_fill_delay_s": (
                float(filled["fill_delay_s"].median()) if filled.height else None
            ),
            "mean_hold_minutes": (
                float(filled["hold_minutes"].mean()) if filled.height else None
            ),
            "placement_collapsed_orders": int(trades_view["placement_collapsed"].sum()),
            "fill_status_counts": dict(
                trades_view.group_by("fill_status").len().sort("len", descending=True).iter_rows()
            ),
            "release_reason_counts": dict(
                trades_view.group_by("release_reason")
                .len()
                .sort("len", descending=True)
                .iter_rows()
            ),
            "entry_status_counts": dict(
                trades_view.group_by("entry_status")
                .len()
                .sort("len", descending=True)
                .iter_rows()
            ),
            "exit_submit_status_counts": dict(
                trades_view.group_by("exit_submit_status")
                .len()
                .sort("len", descending=True)
                .iter_rows()
            ),
        }
    else:
        cell["trade_stats"] = {
            "funded_intents": 0,
            "conditional_fills": 0,
            "fill_rate_of_funded_intents": None,
            "fill_rate_of_touched_intents": None,
            "mean_net_usd_per_conditional_fill": {str(r): None for r in rung_keys},
            "worst_known_fill_usd": {str(r): None for r in rung_keys},
            "win_rate_known_fill": {str(r): None for r in rung_keys},
            "mean_placement_saving_bps_at_intent": None,
            "mean_proxy_gross_minute_open_basis": None,
            "mean_touch_gross_0bps": None,
            "mean_fill_delay_s": None,
            "median_fill_delay_s": None,
            "mean_hold_minutes": None,
            "placement_collapsed_orders": 0,
            "fill_status_counts": {},
            "release_reason_counts": {},
            "entry_status_counts": {},
            "exit_submit_status_counts": {},
        }
    return cell


def aggregate_block(out_dir: Path, block: str, days: list[str]) -> tuple[dict, dict]:
    """The full eight-view surface of one block, plus the block's data coverage."""
    daily, trades = read_block_frames(out_dir, block)
    surface = {view.key: view_cell(daily, trades, view, days) for view in VIEWS}
    coverage = {
        "block": block,
        "days": len(days),
        "signals": int(daily["signals"].sum()),
        "dedup_identical_limits": int(daily["dedup_identical_limits"].sum()),
        "intents_funded": int(daily["intents_funded"].sum()),
        "attempts": int(daily["attempts"].sum()),
        "fills": int(daily["fills"].sum()),
        "unknown_fills": int(daily["unknown_fills"].sum()),
        "rested_unfilled": int(daily["rested_unfilled"].sum()),
        "unknown_share_of_funded_intents": (
            float(daily["unknown_fills"].sum() / daily["intents_funded"].sum())
            if int(daily["intents_funded"].sum())
            else None
        ),
        "causal_skips": {
            reason: int(daily[reason].sum())
            for reason in (
                "no_order_min_capital",
                "quote_not_acquired",
                "intent_not_firm_fresh",
                "ttl_window_beyond_session",
                "overlap_skips",
                "cooldown_skips",
                "max_attempt_skips",
                "cash_or_slot_skips",
            )
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
            "placement": v.placement,
            "ttl_s": int(v.ttl_s),
            "validation_known_usd_per_calendar_day_at_selection_rung": surface[v.key][
                "known_usd_per_calendar_day"
            ][sel_key],
            "known_fills": surface[v.key]["known_fills"],
            "unknown_fills": surface[v.key]["unknown_fills"],
            "rested_unfilled": surface[v.key]["rested_unfilled"],
            "traded_days": surface[v.key]["traded_days"],
        }
        for v in ranked
    ]
    return ranked[0], ranking


def decision_text(chosen: View, val: dict, late: dict | None, frozen: bool) -> str:
    sel_key = str(int(SELECT_COST))
    objective = val["known_usd_per_calendar_day"][sel_key]
    fills = val["known_fills"]
    if fills == 0 or objective is None:
        return (
            "NO_EDGE_EVIDENCE: the chosen validation view has no known fill, so an empty "
            "signal set is cash, not a positive edge. No actual-touch positive formulation "
            "was measured; the retained marketable-IOC lead stays the reference and nothing "
            "here is promoted."
        )
    if objective <= 0:
        return (
            f"NO_ACTUAL_TOUCH_POSITIVE_FORMULATION: the best of the eight passive views "
            f"({chosen.label}) measures {objective:+.2f} $/calendar day of KNOWN "
            f"contribution on 2023 validation at {int(SELECT_COST)}bps over {fills} known "
            f"fills. All eight views are measured and none is positive, so the retained "
            "marketable-IOC lead stays the reference; the unknown share and the rested-"
            "unfilled count are reported beside this number and are NOT assumed to be "
            "cash or fills."
        )
    text = (
        f"{STATUS}: chosen {chosen.label} (theta {chosen.threshold:.4f}, placement "
        f"{chosen.placement}, TTL {int(chosen.ttl_s)}s) by 2023 validation actual-touch "
        f"known contribution: {objective:+.2f} $/calendar day at {int(SELECT_COST)}bps = "
        f"{val['dollars_per_year_252_on_book'][sel_key]:+.0f} $/year on the "
        f"${int(BOOK)} nominal book (simple 252-session convention, not a CAGR), "
        f"{fills} known fills on {val['traded_days']} traded days of {val['days_replayed']} "
        f"calendared days, {val['unknown_fills']} UNKNOWN executions "
        f"({100.0 * val['unknown_fills'] / max(1, val['attempts']):.1f}% of attempts) and "
        f"{val['rested_unfilled']} resting orders cancelled unfilled. This is the measured "
        "KNOWN-contribution expectation on a PARTIAL basis (unknowns excluded from the "
        "numerator, never assumed to be fills or cash), not an actual account CAGR, not a "
        "self-financing return and not an expected-loss estimate."
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
    supplemental_roots: tuple[Path, ...],
    val_cov: dict,
    late_cov: dict | None,
) -> dict:
    sup_digest, sup_entries = supplement_digest(supplemental_roots)
    model_path = MODEL_DIR / f"payoff_h{HEAD}.joblib"
    return {
        "producer_sha256": producer_sha256(),
        "producer_script": str(Path(__file__).resolve()),
        "contract_sha256": sha256_bytes(CONTRACT),
        "panel_root": str(PANEL_ROOT),
        "panel_days_root": str(PANEL_DAYS),
        "quote_cache_root": str(QUOTE_CACHE_ROOT),
        "quote_cache_days_present": sum(
            1
            for d in days_val + list(days_late or [])
            if (QUOTE_CACHE_ROOT / f"{d}.parquet").exists()
        ),
        "supplemental_roots": [str(r) for r in supplemental_roots],
        "supplemental_roots_existing": [str(r) for r in supplemental_roots if r.exists()],
        "supplemental_files": sup_entries,
        "supplement_digest": sup_digest,
        "quote_universe_acquire_root": str(ACQUIRE_ROOT),
        "quote_universe_acquire_root_present": ACQUIRE_ROOT.exists(),
        "model_dir": str(MODEL_DIR),
        "models": {f"h{HEAD}": {"path": str(model_path), "sha256": aqf.sha256_file(model_path)}},
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
def run(args: argparse.Namespace) -> None:
    t0 = time.time()

    out = args.out or OUTPUT
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")

    models, model_report = aqf.load_stored_models()
    if HEAD not in models:
        raise SystemExit(f"[passive] immutable head h{HEAD} not loaded from {MODEL_DIR}")
    model = models[HEAD]
    head_report = dict(model_report[f"h{HEAD}"])
    head_report["scored_by_this_producer"] = True
    head_report["unused_sibling_heads"] = [
        f"h{h}" for h in sorted(models) if h != HEAD
    ]
    print(
        f"[model] immutable h{HEAD} loaded (no refit): sha256 "
        f"{head_report['sha256'][:12]} ({head_report['n_features']} features, horizon "
        f"{head_report['horizon']}); unused sibling heads verified and ignored: "
        f"{head_report['unused_sibling_heads']}",
        flush=True,
    )

    supplemental_roots = tuple(SUPPLEMENT_ROOTS)
    for extra in args.supplement or []:
        if extra not in supplemental_roots:
            supplemental_roots = supplemental_roots + (extra,)
    existing = [r for r in supplemental_roots if r.exists()]
    print(
        "[supplements] " + (", ".join(str(r) for r in existing) if existing else "none present"),
        flush=True,
    )

    days_val = block_days(args.days, VAL_BLOCK)
    if args.days is None and len(days_val) != EXPECTED_DAYS[VAL_BLOCK]:
        raise SystemExit(
            f"[passive] requires {EXPECTED_DAYS[VAL_BLOCK]} {VAL_BLOCK} days, "
            f"got {len(days_val)}"
        )
    if not days_val:
        raise SystemExit("[passive] no validation days selected")

    write_json_atomic(out / "contract.json", CONTRACT)
    print(
        f"[freeze-grid] contract -> {out / 'contract.json'} "
        f"({len(VIEWS)} views x {len(RUNG_COSTS)} rungs, before any outcome)",
        flush=True,
    )

    # 1) validation only: no late panel or quote file is touched before the freeze.
    sel_key = str(int(SELECT_COST))
    print(
        f"[blocks] validation={len(days_val)} days (late block deferred until after the freeze)",
        flush=True,
    )
    val_score_cov = score_block(out, model, days_val, args.resume, supplemental_roots)
    val_replay_cov = replay_block(out, VAL_BLOCK, days_val, args.resume, supplemental_roots)
    val_surface, val_cov = aggregate_block(out, VAL_BLOCK, days_val)
    for view in VIEWS:
        cell = val_surface[view.key]
        print(
            f"[val@{int(SELECT_COST)}] {cell['label']:<34} "
            f"{cell['known_usd_per_calendar_day'][sel_key]:+8.2f} $/day "
            f"known fills={cell['known_fills']} unknown={cell['unknown_fills']} "
            f"rested={cell['rested_unfilled']} attempts={cell['attempts']} "
            f"signals={cell['signals']} traded_days={cell['traded_days']}",
            flush=True,
        )

    chosen, ranking = choose_view(val_surface)
    frozen_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    freeze = {
        "frozen_before_late_file_access": True,
        "frozen_at": frozen_at,
        "study": "alpha_passive_quote_confirmation",
        "status": STATUS,
        "chosen": {
            "view_key": chosen.key,
            "label": chosen.label,
            "head": HEAD,
            "threshold": chosen.threshold,
            "placement": chosen.placement,
            "ttl_s": int(chosen.ttl_s),
        },
        "selection": {
            "objective": (
                "2023 validation actual-touch known contribution dollars per full "
                "calendar day at the primary residual rung (ONE pre-declared scenario, "
                "never a hurdle)"
            ),
            "selection_cost_bps": SELECT_COST,
            "cost_ladder_bps": list(RUNG_COSTS),
            "basis": (
                "PARTIAL: unknown executions are excluded from the numerator and reported "
                "beside it; the whole-day full-loss lower bound is a separate bound, never "
                "an expected-loss estimate and never the objective"
            ),
            "power_floors": None,
            "median_or_tail_gates": None,
            "bootstrap_or_ci": None,
            "n100_gate": None,
            "stress_kill": None,
            "tie_break": "(- objective, - known_fills, view_key)",
            "ranking": ranking,
        },
        "views": [
            {
                "view_key": v.key,
                "label": v.label,
                "threshold": v.threshold,
                "placement": v.placement,
                "ttl_s": int(v.ttl_s),
            }
            for v in VIEWS
        ],
        "validation_surface": val_surface,
        "model": head_report,
        "provenance": provenance_block(
            out, days_val, None, supplemental_roots, val_cov, None
        ),
    }
    write_json_atomic(out / "selection_freeze.json", freeze)
    print(
        f"[freeze] selection_freeze.json -> chosen {chosen.label} "
        f"({val_surface[chosen.key]['known_usd_per_calendar_day'][sel_key]:+.2f} $/day "
        f"@{int(SELECT_COST)}) AFTER validation, BEFORE any late file is read",
        flush=True,
    )

    if args.skip_late:
        results = {
            "study": "alpha_passive_quote_confirmation",
            "status": STATUS,
            "decision": decision_text(chosen, val_surface[chosen.key], None, frozen=False),
            "chosen": freeze["chosen"],
            "frozen_choice_immutable": True,
            "contract": CONTRACT,
            "model": head_report,
            "validation": val_surface,
            "validation_ranking": ranking,
            "late": None,
            "coverage": {"validation": val_cov, "late": None},
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

    # 2) late block: the same eight views for transparency, the choice already frozen.
    #    This is the FIRST access to any late panel or quote file.
    days_late = block_days(args.days, LATE_BLOCK)
    if args.days is None and len(days_late) != EXPECTED_DAYS[LATE_BLOCK]:
        raise SystemExit(
            f"[passive] requires {EXPECTED_DAYS[LATE_BLOCK]} {LATE_BLOCK} days, "
            f"got {len(days_late)}"
        )
    if not days_late:
        # A smoke subset can select no late day at all (e.g. --days inside 2023). The
        # freeze already happened, so finish the validation-only artifacts instead of
        # dividing by an empty calendar; a full run always reaches this point with 332.
        print(
            "[blocks] late=0 days selected (smoke subset): finishing after the freeze",
            flush=True,
        )
        results = {
            "study": "alpha_passive_quote_confirmation",
            "status": STATUS,
            "decision": decision_text(chosen, val_surface[chosen.key], None, frozen=False),
            "chosen": freeze["chosen"],
            "frozen_choice_immutable": True,
            "contract": CONTRACT,
            "model": head_report,
            "validation": val_surface,
            "validation_ranking": ranking,
            "late": None,
            "coverage": {"validation": val_cov, "late": None},
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
    print(f"[blocks] late={len(days_late)} days (after the freeze)", flush=True)
    late_score_cov = score_block(out, model, days_late, args.resume, supplemental_roots)
    late_replay_cov = replay_block(out, LATE_BLOCK, days_late, args.resume, supplemental_roots)
    late_surface, late_cov = aggregate_block(out, LATE_BLOCK, days_late)
    for view in VIEWS:
        cell = late_surface[view.key]
        print(
            f"[late@{int(SELECT_COST)}] {cell['label']:<34} "
            f"{cell['known_usd_per_calendar_day'][sel_key]:+8.2f} $/day "
            f"known fills={cell['known_fills']} unknown={cell['unknown_fills']} "
            f"rested={cell['rested_unfilled']} attempts={cell['attempts']} "
            f"signals={cell['signals']} traded_days={cell['traded_days']}",
            flush=True,
        )

    results = {
        "study": "alpha_passive_quote_confirmation",
        "status": STATUS,
        "decision": decision_text(
            chosen, val_surface[chosen.key], late_surface[chosen.key], frozen=True
        ),
        "chosen": freeze["chosen"],
        "frozen_choice_immutable": True,
        "contract": CONTRACT,
        "model": head_report,
        "validation": val_surface,
        "validation_ranking": ranking,
        "late": late_surface,
        "late_ranking": [
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
            "score_validation": val_score_cov,
            "score_late": late_score_cov,
            "replay_validation": val_replay_cov,
            "replay_late": late_replay_cov,
        },
        "provenance": provenance_block(
            out, days_val, days_late, supplemental_roots, val_cov, late_cov
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
    run(p.parse_args())


if __name__ == "__main__":
    main()
