#!/usr/bin/env python3
"""SHALLOW-DEPTH entry filter on the adaptive depth-participation lane (no refit).

This producer is the PRE-REGISTERED refinement of the completed adaptive depth-participation
lane (``alpha_adaptive_depth_sizing``). It keeps that lane's machinery EXACTLY as published -
the immutable h60 head, the clock conventions, the IOC entry leg, the MARKET-style exit leg,
the fee/rung ladder, the UNKNOWN conventions, the book rules, the nine adaptive views and the
three fixed-size reference arms - and changes ONE thing: an additional arm-independent causal
entry filter on the DISPLAYED DEPTH observed at the intent clock.

* THE FILTER: a candidate intent is KEPT iff the thinner displayed side at the (t+1) intent
  clock - ``min(intent_ask_shares, intent_bid_shares)`` from the latest RAW NBBO state, the
  very same stored observation the participation cap reads - is STRICTLY LESS THAN
  ``DEPTH_FILTER_MAX_SHARES`` (500) shares. Every candidate whose thinner displayed side is
  >= 500 shares is skipped as ``depth_not_thin_skips``. The filter is evaluated once per
  candidate, AFTER the parent lane's arm-independent intent gate (quote acquired, firm,
  fresh, min capital) and BEFORE the arm's own quantity and spread rules; it never re-reads,
  re-prices or re-sizes anything after the intent clock. It is registered in
  ``factory/artifacts/overnight/shallow_depth_prereg.json`` BEFORE any outcome exists, and
  this run REFUSES to start unless that artifact is present and canonicalises to the content
  embedded in this file.

* PRE-REGISTRATION AND KILL RULE: the exact filter, the arm grid (the parent lane's score
  bars 150/200/300bps x participation 10/25/50%, plus the three fixed-size reference arms),
  the selection rule (validation known-contribution $/panel session at 25bps; ties -> more
  known fills -> fewer attempts -> arm_key) and the kill rule (dies if validation is
  negative or if the late block is negative at 100bps) are frozen in the artifact above and
  embedded here. Validation is scored, replayed and aggregated FIRST, the single view is
  frozen to ``selection_freeze.json``, and only then is the late block (2025-02..2026-05)
  touched - exactly once, for transparency, with the choice immutable.

* Because the filter changes entry admission, it can free slots, cash and cooldown that the
  parent lane spent on deep-book intents, so this study's numbers are NOT a re-slice of the
  parent's funded attempts. Both are reported: the parent lane's post-hoc depth<500 slice
  (+3.51 validation / +6.23 late $/session) stays the registered prior, and this lane's own
  re-run is the measurement. A tail re-selection audit (funded-intent key overlaps against
  the parent arm and against the parent's post-hoc slice) answers "does the filter merely
  re-select the tail" with set intersections rather than adjectives.

The parent lane's own description of the inherited convention follows; everything below the
filter bullet is that lane's published contract, unchanged.

* ONE immutable head (h60) is loaded and scored ONCE per day over the
  liquidity-qualified, strictly-past panel states: no refit, no HPO, no new features, no
  ticker/date identifiers, no label features. The stored ``feature_order`` is verified
  against ``alpha_open_learned.FEATURES_ALL`` and the matrix is built with the same
  ``feature_matrix`` helper used at fit time (26 causal features, null-filled, Float64).
  The 2021-02..2022-12 fit block is never replayed.
* NINE fixed views = 3 score bars (0.015 / 0.020 / 0.030) x 3 causal participation rates
  (0.10 / 0.25 / 0.50). The bar is a bar on the model's own predicted gross payoff; the
  participation rate is a book-participation cap, never a re-fit knob. THREE further
  fixed-size reference arms replay the SAME signals, book and rules with the capital cap
  only (no depth participation) so the adaptive result is compared against a SIMULATED
  fixed-size baseline and never against a linear rescale of itself. The two arms do NOT
  trade the same intent set: the fixed-size arm SKIPS an intent whose capital-cap ticket
  exceeds the displayed ask depth (intent_depth_unsupported) while the adaptive arm caps
  the ticket down and ATTEMPTS it, so every baseline comparison carries both arms'
  intent-set sizes and admission rules beside the numbers and is never readable as a
  like-for-like 'adaptive beats fixed' claim.
* SIZING is causal and fixed BEFORE the arrival print is seen. At the OBSERVED intent
  quote: q = min( floor(1000 / (limit * 1.0075)),
                  floor(participation * min(INTENT ask_shares, INTENT bid_shares)) ).
  The capital cap is fee-funded at the 150bps round-trip reserve (75bps on the funding
  side, so the reservation q * limit * 1.0075 always covers the highest reported rung);
  the participation cap uses BOTH displayed top-of-book sides at the intent clock, which
  is the only current-bid/ask liquidity information that exists before the position does.
  q, limitPrice and the reserved cash are never re-floored from a future arrival price or
  a future exit depth. A causal q of zero is a KNOWN NO ORDER (no order sent, cash stays
  unfilled, never a priced zero-return fill and never an UNKNOWN): from the capital cap it
  is ``no_order_min_capital``, from the depth participation cap it is
  ``no_order_zero_depth``.
* ENTRY is a fresh-quote-gated MARKETABLE IOC LIMIT with its OWN pricing leg: the intent is
  the clock (t+1) at 00.000s, the arrival print is read ONCE at intent + 250ms, and an IOC
  limit order that did not execute there is CANCELLED - it never rests into a future print.
  LimitPrice = observed ASK * 1.01 rounded UP to the cent is a pre-declared marketability
  cap, not charged friction. At the arrival read: firm+fresh and ask <= limit and displayed
  ASK depth >= q is a CONDITIONAL full-touch IOC-model fill at the ACTUAL ask; ask > limit
  is a NO_MATCH_AT_L1 unfilled cash outcome released at the arrival clock; a partial depth,
  a stale-but-valid print, an invalid / crossed / non-regular / absent book or a
  non-acquired stream is an execution UNKNOWN that stays reserved to the session end. The
  once-only arrival read is the published IOC leg (``aqf.entry_price_leg``, which never
  rests); this producer adds only its OWN limit-match, displayed-depth and fill-clock
  consumer rules on top of that read (``price_entry_ioc``) and never inherits the
  MARKET-style rest branch of the shared ``priced_leg`` helper.
* EXIT is a FRESH-QUOTE-GATED SUBMISSION of a market order at the deterministic hold
  (entry minute + 60 minutes, capped at the RTH close). Age > 2s does not expire a valid
  NBBO and does not cancel a submitted order: the order is submitted at the due intent if
  the latest raw state is firm and fresh, else held to the FIRST eligible regular print
  observed at/after the due intent inside RTH and submitted at that timestamp. Pricing
  happens +250ms after the ACTUAL submission; a stale-but-valid print at arrival is an
  UNKNOWN (never a later, more favourable print), a genuinely unavailable book lets the
  already-working market order rest to the first eligible print at/after arrival. The
  REAL execution clock of that priced leg - the arrival clock when the book was fresh, the
  first future regular print's timestamp when the order had to rest - is preserved as the
  release/cooldown anchor (``actual_exit_us``); cash, the slot and the cooldown never free
  at a merely planned time.
* BOOK: 3 slots x $1,000 = $3,000 nominal research allocation, no leverage, one position
  per ticker, flat 15-minute cooldown after the ACTUAL exit, at most 3 attempts per ticker
  per session. Simultaneous clock candidates are chosen by the current forecast (pred_60)
  and funded (up to three intents, cash reserved first) BEFORE any arrival outcome is
  observed; a fourth same-clock candidate is never substituted after seeing an unfilled
  one. A later clock may use cash released by an earlier ACTUAL exit. An UNKNOWN position
  keeps its slot and its ticker blocked until it resolves or the session ends.

Feasibility/selection: the ENTIRE nine-view contract (plus the three fixed-size reference
arms) is frozen to disk BEFORE any validation outcome exists. The single view is then
chosen ONLY by 2023 validation actual-touch known contribution in dollars per panel
session (the historical "per full calendar day" wording; the per-session and the true
per-calendar-day-span rates are both reported under explicit keys) at the 25bps residual
rung, on an explicit PARTIAL basis (unknown executions are excluded from the numerator and
reported beside it). No synthetic full-loss UNKNOWN veto, no minimum
known-fill count, no median or CI gate, no cost/count kill on the SELECTION. The late
block then runs every arm for transparency while the choice stays immutable. The KILL RULE
registered for this refinement lane is separate from the selection and has exactly two
conditions: the refinement DIES if the chosen view's validation known contribution per
panel session at 25bps is non-positive, or if its late-block known contribution per panel
session at the 100bps rung is non-positive. The verdict is carried in results.json under
``kill_rule`` and in the status/decision text.

Reported per view and per 0/5/10/25/50/75/100/125/150bps round-trip residual rung over the
whole calendar: known contributions, fills / known fills / traded days / attempts, the
supported share and the UNKNOWN and no-match counts, causal skip reasons, deployed capital,
its density (known $ per deployed dollar) and the peak concurrently reserved cash, the
simulated fixed-size baseline comparison (an explicitly NON-like-for-like arm: see
``baseline_comparison``), monthly and yearly accounting, a day-level bootstrap, the worst
known fill and the simple $/(year on a $3,000 book) at 252 sessions. Per-day rates are
reported under explicit names: ``*_per_panel_session`` divides by the block's panel
SESSIONS and ``*_per_calendar_day_span`` by the block's calendar-day span (first..last
session day inclusive); the historical ``*_per_calendar_day`` keys are kept with their
original per-session values for backward compatibility and are labelled as such in every
cell. The headline is the measured
KNOWN-contribution expectation of the chosen view - not an actual account CAGR, not a
self-financing return, and not a claim that the unknown share is zero.

The model predicts a minute-open payoff (t -> t+60 minute opens); the traded label here is
the ASK-to-BID NBBO touch. The two bases are NOT the same quantity: the panel's stored
minute-open proxy labels are carried into the artifacts for an explicit basis comparison
and are NEVER used as an entry filter.

Fees: the entry rule charges the OBSERVED spread plus a reasonable 25bps residual; the
funding quantity and the cash reservation are sourced at the 150bps round-trip rung in
EVERY scenario; the reported 0-150bps ladder is an additional round-trip residual on the
actual ask/bid prices (the spread is already inside them - no spread recharge) and is a
comparison set, never a hurdle. Current provider commissions are NOT applied here: they
are sourced separately (provider fee ledger) and stay UNKNOWN in this study, so the low-fee
rungs are read from the same ladder instead of an assumed fee schedule.

This is a research replay of a historical conditional L1 IOC model. No live order, no
broker call, no account write, no protected outcome (2024, 2025-01, 2026-06..08) is ever
touched. Quotes measure the touch cost of a hypothetical order; they are not exchange
fills and never a fill guarantee.

Usage:
  uv run --no-sync python factory/scripts/alpha_shallow_depth_lane.py
      # full 250 validation + 332 late days -> ~/alpha-data/open-search-v1/shallow_depth_lane
  uv run --no-sync python factory/scripts/alpha_shallow_depth_lane.py --resume
      # resume from the per-day state / daily / trade parts already on disk
  uv run --no-sync python factory/scripts/alpha_shallow_depth_lane.py --days 2023-05-15
      # smoke subset (debug only)
  uv run --no-sync python factory/scripts/alpha_shallow_depth_lane.py --stage shard --days ...
      # score+replay only (no aggregate / freeze / results), for parallel day shards
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
from datetime import date
from fractions import Fraction
from pathlib import Path

import joblib
import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))

from alpha_open_learned import FEATURES_ALL, PANEL_ROOT, bootstrap_daily, feature_matrix
from alpha_open_panel import allowed
from alpha_open_sim import causal_liquidity, period

# The complete quote-aware substrate and the MARKET-style exit policy are imported from the
# completed quote-aware producer. The entry's once-only arrival read is also the published
# IOC leg (``entry_price_leg``, which never rests); ``price_entry_ioc`` adds this
# producer's OWN limit-match / displayed-depth / fill-clock consumer rules on top of it,
# so a BUY IOC never inherits the market-style rest branch of ``priced_leg``.
from alpha_quote_aware_frequency import (  # noqa: E402
    MAX_AGE_S,
    MAX_RUNG_COST,
    SymbolQuotes,
    arrival_us,
    entry_price_leg,
    limit_price_of,
    minute_us,
    priced_leg,
    quote_day_frame,
    submission_clock,
    symbol_quotes,
)
from alpha_sparse_daily import day_context
from alpha_sparse_quote_service import day_quote_path

# ----- fixed configuration (no HPO, no refit, no grid search). -----------------
HEAD = 60  # the only stored payoff head ever loaded
THRESHOLDS = (0.015, 0.020, 0.030)  # three fixed bars on the model's own prediction
PARTICIPATIONS = (0.10, 0.25, 0.50)  # three fixed causal depth-participation rates
# Reported round-trip residual ladder (bps, both legs): a COMPARISON SET, never a hurdle.
# The spread is already inside the actual ask/bid prices, so a rung is additional
# fee/slippage only - no spread is ever recharged.
RUNG_COSTS = (0.0, 5.0, 10.0, 25.0, 50.0, 75.0, 100.0, 125.0, 150.0)
SELECT_COST = 25.0  # primary selection / entry-rule rung
ENTRY_OVERHEAD_BPS = 25.0  # reasonable residual added to the observed spread in the entry rule
# the aqf.MAX_RUNG_COST (150bps) round trip, 75bps on the ticket's funding side
FUNDING_SIDE_FACTOR = 1.0075
MAX_SLOTS = 3  # concurrent positions
ORDER_BUDGET = 1000.0  # per-position ticket
BOOK = MAX_SLOTS * ORDER_BUDGET  # $3,000 nominal research sub-book
COOLDOWN_MIN = 15  # flat minutes after the ACTUAL exit
MAX_ATTEMPTS = 3  # attempts per ticker per session
ANNUAL_SESSIONS = 252  # simple session-count convention, NOT a CAGR claim
BOOT_N = 1000
BOOT_SEED = 20261009
VAL_BLOCK = "validation"
LATE_BLOCK = "confirmation"
EXPECTED_DAYS = {VAL_BLOCK: 250, LATE_BLOCK: 332}
MODEL_DIR = PANEL_ROOT / "learned" / "models"
OUTPUT = PANEL_ROOT / "shallow_depth_lane"
PARENT_ROOT = PANEL_ROOT / "adaptive_depth_sizing"  # read-only reference for the tail audit
DATA_ROOT = Path("/home/hillel/projects/Alpacatrader") / "data"
PANEL_DAYS = PANEL_ROOT / "days"
PANEL_CONTRACT = PANEL_ROOT / "contract.json"
STATUS = "REFINEMENT-PREREGISTERED"
VERSION = 2
STATES_SCHEMA = 1
REPLAY_SCHEMA = 2  # the daily parts carry one more causal skip reason column
PROTECTED_UNREAD = ["2024", "2025-01", "2026-06", "2026-07", "2026-08"]
# ----- the registered refinement: the shallow-displayed-depth entry filter -----
# A candidate intent is KEPT iff the thinner displayed side at the (t+1) intent clock is
# STRICTLY below this many shares; everything else is skipped as depth_not_thin_skips. The
# observation is the latest RAW NBBO state at the intent clock - the same stored intent fact
# the participation cap consumes - so the filter adds no clock, no data and no look-ahead.
DEPTH_FILTER_MAX_SHARES = 500.0
DEPTH_FILTER_SKIP = "depth_not_thin_skips"
# The kill rule registered in factory/artifacts/overnight/shallow_depth_prereg.json: dies if
# validation is negative at the 25bps selection rung, or if the late block is negative at the
# 100bps rung. Nothing else kills it and nothing else promotes it.
KILL_VALIDATION_COST_BPS = SELECT_COST
KILL_LATE_COST_BPS = 100.0
PREREG_PATH = (
    Path(__file__).resolve().parents[1] / "artifacts" / "overnight" / "shallow_depth_prereg.json"
)
# Existing fetch caches merged as supplements (original prints always win; a supplement may
# only ADD timestamps, never silently replace or revise a print). The acquisition root is
# the panel-universe harvest (alpha_quote_universe_acquire, run --universe panel): it holds
# EVERY panel ticker, a SUPERSET of the qualified harvest (extra names, never fewer). It is
# DISCOVERED when present; the harvest is complete (1,003 day manifests on disk at
# 2026-10-10). A ticker whose historical stream is still absent after every root is an
# ACQUISITION UNKNOWN - never known cash and never a future-availability entry filter.
SUPPLEMENT_ROOTS = (
    PANEL_ROOT / "sparse_execution_frontier" / "fetched_quotes" / "quotes",
    PANEL_ROOT / "quote_completeness_audit" / "quotes",
    PANEL_ROOT / "quote_completeness_audit" / "fetched_quotes" / "quotes",
    PANEL_ROOT / "quote_universe_acquire" / "quotes",
)
QUOTE_CACHE_ROOT = DATA_ROOT / "sip" / "net" / "quotes"
# Displayed depth is an integer share count; the participation product is evaluated in
# EXACT rational arithmetic so a 10% x 30-share book is exactly 3 shares and can never
# drift one share low from binary floating point at a consumer boundary.
PARTICIPATION_EXACT: dict[float, Fraction] = {
    0.10: Fraction(1, 10),
    0.25: Fraction(1, 4),
    0.50: Fraction(1, 2),
}

# ----- the pre-registration (frozen BEFORE any outcome; the run fails closed) ---
# The full registered artifact is embedded here verbatim. Before scoring the first day the
# producer loads factory/artifacts/overnight/shallow_depth_prereg.json and requires it to
# canonicalise (json.dumps(..., sort_keys=True)) to this exact content: an edited, missing or
# unreadable registration aborts the run rather than silently widening the search. The
# registered SHA-256 of that file is carried into results.json as provenance.
PREREG = {
    "preregistration": {
        "study": "alpha_shallow_depth_lane",
        "study_root": "~/alpha-data/open-search-v1/shallow_depth_lane",
        "registered_at": "2026-10-12",
        "registered_by": "overnight alpha program (adaptive-depth lane refinement)",
        "parent_lane": {
            "study": "alpha_adaptive_depth_sizing",
            "output_root": "~/alpha-data/open-search-v1/adaptive_depth_sizing",
            "chosen_arm": "thr300bps|p50",
            "posthoc_source": "factory/artifacts/overnight/edge_anatomy.json filter depth<500_sh__posthoc (a POST-HOC mirror of the pre-assigned depth_100_500_sh filter, discovered by slicing 9 dimensions on 2 blocks; NOT in the parent's assigned filter set)",
        },
        "status_at_registration": "PRE-REGISTERED, NOT YET RUN: no validation state, no late state, no replay part of the shallow_depth_lane root exists at registration time; the parent lane's artifacts are the only inputs and are never mutated.",
        "refinement": {
            "name": "shallow displayed depth at the intent clock",
            "exact_filter": "keep a candidate intent iff min(intent_ask_shares, intent_bid_shares) < 500 shares, both read from the latest RAW NBBO state at the (t+1) intent clock (the same stored intent observation the participation cap reads); every candidate whose thinner displayed side is >= 500 shares is skipped as depth_not_thin_skips",
            "filter_stage": "arm-independent, evaluated once per candidate at the intent clock AFTER the parent's arm-independent intent gate (quote acquired, firm, fresh, min capital) and BEFORE the arm's own quantity and spread rules; it never re-reads, re-prices or re-sizes anything after the intent clock",
            "threshold_units": "shares of displayed top-of-book depth on one side; 500 is registered a priori as the post-hoc bucket edge (the edge_anatomy depth buckets 100-200 / 200-500 / 500-1k ...)",
            "comparability_note": "the post-hoc parent-slice numbers (validation +3.51 vs +1.36 $/session, late +6.23 vs +5.95, bootstrap p 0.86/0.96, full-loss lower bound -34.6/-54.3 -> -8.5/-8.8 $/session) were computed by RE-SLICING the parent's already-funded attempts; this study instead re-runs the whole lane with the filter in the entry rule, so intents freed by the filter (slot/cash/cooldown released because a deep-book intent is no longer taken) can be admitted. The two numbers are expected to differ and BOTH are reported.",
        },
        "arm_grid": {
            "selectable_views": "NINE adaptive views = 3 score bars x 3 causal participation rates, identical to the parent lane: thresholds 0.015 / 0.020 / 0.030 (150 / 200 / 300 bps on the immutable h60 prediction) x participation 0.10 / 0.25 / 0.50 (book-participation cap on min(intent ask_shares, intent bid_shares) in exact rational arithmetic)",
            "fixed_size_reference_arms": "THREE capital-cap-only arms (same signals, entry rule, exit rule, book, ladder; no depth participation): reference baselines, never selectable",
            "book": "3 slots x $1,000 tickets = $3,000 nominal research book, 15-minute cooldown after the ACTUAL exit, max 3 attempts per ticker per session",
            "unchanged_from_parent": "clock conventions (intent at (t+1) 00.000s, arrival at intent+250ms, exit at entry_minute+60 capped at RTH close), IOC entry pricing, MARKET-style exit pricing, fee/rung ladder 0/5/10/25/50/75/100/125/150bps, funding factor 1.0075, UNKNOWN conventions, acquisition-unknown rule, supplements merge order",
        },
        "selection_rule": {
            "objective": "2023 validation (250 panel sessions) actual-touch KNOWN contribution dollars per panel session at the 25bps residual rung (the legacy key known_usd_per_calendar_day, whose value divides by sessions)",
            "basis": "PARTIAL: UNKNOWN executions excluded from the numerator and reported beside it",
            "tie_break": "(- objective $/session, - known_fills, + attempts, arm_key)",
            "selectable": "the nine adaptive views only; the three fixed-size arms are reference baselines and are excluded from selection",
            "frozen": "selection_freeze.json is written after the validation aggregate and BEFORE any late panel or quote file is read; the choice is immutable afterwards",
        },
        "kill_rule": {
            "description": "the refinement DIES if either condition holds",
            "condition_1_validation_negative": "the chosen arm's validation known_usd_per_panel_session at 25bps <= 0",
            "condition_2_late_negative_at_100bps": "the chosen arm's late-block (2025-02..2026-05, 332 sessions) known_usd_per_panel_session at the 100bps residual rung <= 0",
            "no_other_gates": "no power floor, no median gate, no bootstrap gate, no full-loss veto: exactly these two conditions and nothing else",
        },
        "diagnostics_promise": [
            "known/unknown/no-match counts per arm per block",
            "full-loss lower bound per session (unknown attempts charged the full $1,000 ticket, the study's coding convention, an UNKNOWN-charged bound and never an EV)",
            "top-5 fill share of known PnL (also top-1/3/10)",
            "drop-best-1 sensitivity (known $/session after removing the single best fill)",
            "drop-top-5 sensitivity (known $/session after removing the five best fills)",
            "known $ per deployed dollar (per-deployment-capital return) and deployed capital per session/calendar-day span",
            "tail-re-selection audit: the new chosen arm's funded intent keys (day, ticker, intent_us) compared against the parent chosen arm's funded intent keys and against the post-hoc depth<500 slice of the parent arm, so 'the filter merely re-selects the tail' is answered with set overlaps, not adjectives",
        ],
        "blocks": {
            "validation": "2023, 250 panel sessions (train/dev family: 2021-05..2022-12 fit, never replayed)",
            "late": "2025-02..2026-05, 332 panel sessions, previously explored, read exactly once and only after the freeze",
            "sealed_never_read": ["2024", "2025-01", "2026-06", "2026-07", "2026-08"],
        },
        "enforcement": "factory/scripts/alpha_shallow_depth_lane.py embeds this artifact's content and REFUSES to run (SystemExit) unless factory/artifacts/overnight/shallow_depth_prereg.json exists and canonicalises to the embedded content; the artifact sha256 is recorded in results.json",
    }
}


def canonical_prereg(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True)


def prereg_sha256() -> str:
    return hashlib.sha256(canonical_prereg(PREREG).encode()).hexdigest()


def verify_preregistration() -> dict:
    """Fail closed: the registered artifact must exist and equal the embedded content."""
    if not PREREG_PATH.exists():
        raise SystemExit(
            f"[shallow-depth] PRE-REGISTRATION MISSING: {PREREG_PATH} does not exist; "
            "write the registration before running the study"
        )
    try:
        on_disk = json.loads(PREREG_PATH.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise SystemExit(f"[shallow-depth] PRE-REGISTRATION UNREADABLE: {exc}") from exc
    if canonical_prereg(on_disk) != canonical_prereg(PREREG):
        raise SystemExit(
            f"[shallow-depth] PRE-REGISTRATION MISMATCH: {PREREG_PATH} does not equal the "
            "content embedded in this producer; the registration is frozen, refusing to run"
        )
    return {
        "path": str(PREREG_PATH),
        "sha256": prereg_sha256(),
        "canonical_bytes": len(canonical_prereg(PREREG)),
        "verified_before_run": True,
        "artifact": PREREG,
    }


# ----- the fixed arms (9 selectable views + 3 fixed-size reference arms) -------
def arm_key(threshold: float, participation: float | None) -> str:
    """Stable arm key: the score bar and either the participation rate or the fixed size."""
    bar = f"thr{int(round(threshold * 10_000))}bps"
    if participation is None:
        return f"{bar}|fixedsize"
    return f"{bar}|p{int(round(participation * 100)):02d}"


def arm_label(threshold: float, participation: float | None) -> str:
    bar = f"h60_thr{int(round(threshold * 10_000))}bps"
    if participation is None:
        return f"{bar}_fixedsize"
    return f"{bar}_part{int(round(participation * 100))}pct"


CONTRACT = {
    "version": VERSION,
    "study": "alpha_shallow_depth_lane",
    "relationship_to_parent": (
        "a PRE-REGISTERED refinement of alpha_adaptive_depth_sizing: the parent lane's "
        "clock/fill/fee/unknown conventions, arms, book and selection objective are "
        "inherited verbatim; ONE arm-independent causal entry filter is added (see "
        "depth_filter); a new output root is written and the parent lane's outputs are "
        "never mutated"
    ),
    "hypothesis": "the immutable learned h60 payoff head, traded at three fixed score bars "
    "with a fresh-quote-gated marketable-IOC-limit entry whose integer quantity is "
    "causally capped by BOTH displayed top-of-book sides at the intent clock (capital cap "
    "and depth-participation cap), reduces the future exit L1 shortfall of the displayed "
    "book and keeps the known actual-touch contribution positive at low residual rungs, "
    "against a SIMULATED fixed-size baseline under identical book rules - AND the "
    "shallow-displayed-depth entry filter (thinner displayed side < 500 shares at the "
    "intent clock) keeps most of that money while removing the deep-book losers and most "
    "UNKNOWN executions",
    "depth_filter": {
        "registered_in": str(PREREG_PATH),
        "registered_sha256_embedded": prereg_sha256(),
        "keep_rule": "min(intent_ask_shares, intent_bid_shares) < 500 shares, both read "
        "from the latest RAW NBBO state at the (t+1) intent clock (the same stored intent "
        "observation the participation cap consumes)",
        "skip_reason": DEPTH_FILTER_SKIP,
        "stage": "arm-independent, after the parent lane's arm-independent intent gate "
        "(quote acquired, firm, fresh, min capital) and before the arm's own quantity and "
        "spread rules; evaluated once per candidate; never re-reads anything after the "
        "intent clock",
        "max_shares": DEPTH_FILTER_MAX_SHARES,
        "posthoc_prior": (
            "parent-lane post-hoc slice depth<500_sh (edge_anatomy.json): validation "
            "+3.51 vs +1.36 $/session (p=0.86, 36 known fills, full-loss bound "
            "-8.49 $/session), confirmation +6.23 vs +5.95 $/session (p=0.96, 67 known "
            "fills, bound -8.83 $/session); that slice re-cut the parent's funded "
            "attempts, while this lane re-runs the entry rule, so the numbers are not "
            "required to agree"
        ),
    },
    "kill_rule": {
        "description": "dies if the chosen view's validation known contribution per panel "
        "session at 25bps <= 0, or if its late-block known contribution per panel session "
        "at 100bps <= 0",
        "validation_negative_cost_bps": KILL_VALIDATION_COST_BPS,
        "late_negative_cost_bps": KILL_LATE_COST_BPS,
        "other_gates": "none: no power floor, no median gate, no bootstrap gate, no "
        "full-loss veto",
    },
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
        "heads": [HEAD],
        "feature_order": list(FEATURES_ALL),
        "feature_matrix_helper": "alpha_open_learned.feature_matrix (Float64, null-filled)",
        "fit_block": "2021-02..2022-12 (never replayed, never re-scored as outcomes)",
        "target": "panel minute-open gross payoff label gross_60",
        "clip_fit": [-0.5, 2.0],
    },
    "sizing": {
        "capital_cap": "q_capital = floor(1000 / (limit_price * 1.0075)) at the INTENT limit",
        "participation_cap": "q_depth = floor(participation * min(intent ask_shares, "
        "intent bid_shares)) in EXACT rational arithmetic",
        "quantity": "q = min(q_capital, q_depth) for an adaptive view; q = q_capital for a "
        "fixed-size reference arm; fixed BEFORE the arrival print is seen",
        "participation_rates": list(PARTICIPATIONS),
        "funding_factor": FUNDING_SIDE_FACTOR,
        "funding_factor_meaning": "150bps round-trip reserve with 75bps charged on the "
        "funding side, sourced consistently in EVERY scenario and rung",
        "reservation": "reserved_usd = q * limit_price * 1.0075 (the 150bps rung), so no "
        "rung is unfundable",
        "no_refloor_rule": "the quantity, the limit price and the reservation are never "
        "re-floored from a future arrival price or a future exit depth",
    },
    "entry": {
        "intent_clock": "clock_us(day, t+1, 0)",
        "arrival_clock": "intent + 250ms",
        "observation": "latest RAW NBBO state at the intent clock (never pre-filtered)",
        "firm_fresh_rule": "valid, un-crossed, R-only, age <= 2s",
        "limit_price": "ceil_to_cent(observed_ask * 1.01): pre-declared marketability cap, "
        "NOT charged friction",
        "quantity_rule": "q = min(floor(1000/(limit*1.0075)), floor(participation * "
        "min(intent ask_shares, intent bid_shares)))",
        "spread_rule": "spread_bps + 25 <= 10000 * predicted_gross (observed spread plus a "
        "reasonable 25bps residual)",
        "arrival_rule": "ONE latest-raw read at intent+250ms, then the IOC is CANCELLED if "
        "unexecuted (it never rests into a future print): firm+fresh and ask <= limit and "
        "arrival ASK depth >= q -> CONDITIONAL full-touch IOC-model fill at the ACTUAL ask; "
        "firm+fresh and ask > limit -> NO_MATCH_AT_L1 unfilled cash released at the arrival "
        "clock; partial depth / stale-but-valid / invalid / non-regular / absent book / "
        "non-acquired stream -> execution UNKNOWN reserved to the session end",
        "no_order_rule": "a causal q == 0 is a KNOWN NO ORDER (no order sent, cash unfilled, "
        "never a priced zero-return fill, never an UNKNOWN): from the capital cap it is "
        "no_order_min_capital, from the participation cap it is no_order_zero_depth",
        "ioc_rest_rule": "a BUY IOC LIMIT order never rests into a future eligible print; "
        "the rest branch of the shared MARKET-style priced_leg helper is NOT used for the "
        "entry leg",
    },
    "exit": {
        "hold": "deterministic min(entry_minute + 60, session_end) minutes, RTH close capped",
        "due_intent_clock": "clock_us(day, entry_minute + 60, 0)",
        "submission": "FRESH-QUOTE-GATED: submit at the due intent if the latest raw state "
        "is firm and fresh, else hold to the FIRST eligible regular print at/after the due "
        "intent inside RTH and submit at its timestamp",
        "arrival_clock": "actual submission + 250ms",
        "pricing": "latest raw state at the arrival clock; stale-but-valid -> UNKNOWN; "
        "invalid / non-regular / absent book -> the working MARKET order conditionally rests "
        "to the first eligible print at/after arrival (never a later favourable print)",
        "real_execution_clock_rule": "the priced leg's OWN returned price clock is the "
        "release anchor (actual_exit_us): the arrival clock when the book was fresh, the "
        "first future regular print's timestamp when the order had already been resting; "
        "cash, the slot and the cooldown never free at the planned arrival when the order "
        "actually rested later, and an UNKNOWN leg stays reserved to the session end",
        "quantity": "the causal entry q, never re-sized",
        "depth_rule": "exit BID top-of-book < q -> UNKNOWN partial capacity, never a "
        "guaranteed full fill",
    },
    "book": {
        "slots": MAX_SLOTS,
        "ticket_usd": ORDER_BUDGET,
        "book_usd": BOOK,
        "leverage": False,
        "one_position_per_ticker": True,
        "cooldown": "flat 15 minutes after the ACTUAL exit clock",
        "max_attempts_per_ticker_per_day": MAX_ATTEMPTS,
        "cash_reservation": "the causal 150bps outlay q * limit * 1.0075, sourced in every "
        "scenario",
        "simultaneous_clock_rule": "highest-forecast eligible intents are funded (up to the "
        "slot and cash limits, cash reserved first) BEFORE any arrival outcome is observed; "
        "no same-clock substitution after an unfilled intent",
        "unknown_rule": "an UNKNOWN position keeps its slot and blocks its ticker until it "
        "resolves or the session ends; cash is never freed at a planned time",
        "later_clock_rule": "a later signal may use cash released by an earlier ACTUAL exit",
    },
    "views": {
        "selectable": [
            {
                "view_key": arm_key(t, p),
                "label": arm_label(t, p),
                "threshold": t,
                "participation": p,
            }
            for t in THRESHOLDS
            for p in PARTICIPATIONS
        ],
        "fixed_size_reference_arms": [
            {
                "view_key": arm_key(t, None),
                "label": arm_label(t, None),
                "threshold": t,
                "participation": None,
            }
            for t in THRESHOLDS
        ],
        "fixed_size_reference_rule": "the same signals, entry rule, exit rule, book and "
        "ladder as the adaptive views with the capital-cap quantity only (no depth "
        "participation): a SIMULATED baseline, never a linear rescale of the adaptive fills",
    },
    "selection": {
        "objective": "2023 validation actual-touch known contribution dollars per panel "
        "session (the legacy key known_usd_per_calendar_day divides by sessions) at the "
        "25bps residual rung",
        "selection_cost_bps": SELECT_COST,
        "cost_ladder_bps": list(RUNG_COSTS),
        "basis": "PARTIAL: unknown executions are excluded from the numerator and reported "
        "beside it; the full-loss lower bound is a coded convention, never a veto and never "
        "the objective",
        "synthetic_unknown_loss_veto": False,
        "power_floors": None,
        "median_or_tail_gates": None,
        "cost_or_count_kill": None,
        "tie_break": "(- objective $/session, - known_fills, + attempts, arm_key)",
        "frozen_before_late_inspection": True,
        "freeze_artifact": "selection_freeze.json (written after the validation surface, "
        "before any late file is read)",
    },
    "labels": {
        "traded_basis": "real NBBO ASK entry -> BID exit at the declared clocks",
        "model_basis": "minute-open payoff proxy (t -> t+60 actual minute opens)",
        "proxy_labels_used_as_entry_filter": False,
        "unfilled_entry_status_used_as_entry_filter": False,
    },
    "fees": {
        "entry_rule_residual_bps": ENTRY_OVERHEAD_BPS,
        "funding_and_reserve_rung_bps": MAX_RUNG_COST,
        "rung_ladder_bps": list(RUNG_COSTS),
        "rung_meaning": "additional round-trip residual on the actual ask/bid prices; the "
        "spread is already inside those prices, so no spread is recharged",
        "provider_fees_applied": False,
        "provider_fee_note": "current provider commissions/regulatory fees are NOT assumed "
        "here: they are sourced separately by the provider fee ledger and stay UNKNOWN in "
        "this study; the 0-150bps ladder spans the low-fee providers' effective rungs so a "
        "sourced fee can be read off the same artifacts later",
        "historical_backdating": False,
    },
    "no_live_orders": True,
    "acquisition_unknown_rule": (
        "a ticker with no acquired historical quote stream in ANY root is an ACQUISITION "
        "UNKNOWN (intent_status quote_not_acquired): it is excluded from the known "
        "numerator and reported as a causal skip, it is NEVER known cash and NEVER a "
        "future-availability entry filter; the parent's measured raw-quote coverage "
        "(~54% of qualified validation states, ~51% late) is reported per day in the "
        "score coverage"
    ),
    "supplements": {
        "merge_order": ["ranked SIP day cache"] + [str(r) for r in SUPPLEMENT_ROOTS],
        "discovery": (
            "every configured root is discovered when present; an absent root (e.g. the "
            "pending quote_universe_acquire harvest) contributes no prints and no resume "
            "invalidation until its files exist"
        ),
        "dedup_rule": (
            "unique (symbol, ts_utc) keep=first in that order: the ORIGINAL print always "
            "wins, a supplement may ONLY add timestamps and never silently replaces or "
            "revises an original print"
        ),
        "resume_invalidation": (
            "the per-file sha256/bytes of every existing supplement file are aggregated "
            "into the supplement digest, which is part of both the score and the replay "
            "resume hash"
        ),
        "quote_age_rule": (
            "age > 2s is an operational freshness gate for THIS policy, never a claim that "
            "an NBBO expired or that a submitted order was cancelled"
        ),
    },
}


# ----- shared helpers ---------------------------------------------------------
def producer_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _digest_bytes(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _default(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (set, tuple)):
        return list(obj)
    raise TypeError(f"not serializable: {type(obj)}")


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=1, default=_default) + "\n")
    os.replace(tmp, path)


# ----- the fixed arms (9 selectable views + 3 fixed-size reference arms) -------
@dataclass(frozen=True)
class View:
    """One (threshold, participation) cell.

    ``participation is None`` marks a FIXED-SIZE reference arm: the same signals, entry
    rule, exit rule, book and ladder as the adaptive views with the capital-cap quantity
    only (no depth participation). It is a simulated comparison baseline, never selectable.
    """

    threshold: float
    participation: float | None

    @property
    def is_baseline(self) -> bool:
        return self.participation is None

    @property
    def key(self) -> str:
        return arm_key(self.threshold, self.participation)

    @property
    def label(self) -> str:
        return arm_label(self.threshold, self.participation)


VIEWS = tuple(View(t, p) for t in THRESHOLDS for p in PARTICIPATIONS)  # 9 selectable views
BASELINE_VIEWS = tuple(View(t, None) for t in THRESHOLDS)  # 3 fixed-size reference arms
ARMS = tuple(
    View(t, p) for t in THRESHOLDS for p in (*PARTICIPATIONS, None)
)  # 12 simulated arms, deterministic order
BASELINE_BY_THRESHOLD = {v.threshold: v for v in BASELINE_VIEWS}


# ----- immutable stored model -------------------------------------------------
def load_stored_model() -> tuple[dict, dict]:
    """Load the one immutable head; verify order/horizon; disclose every fit fact."""
    path = MODEL_DIR / f"payoff_h{HEAD}.joblib"
    if not path.exists():
        raise SystemExit(f"[shallow-depth] stored model missing: {path}")
    bundle = joblib.load(path)
    order = list(bundle.get("feature_order") or [])
    if order != list(FEATURES_ALL):
        raise SystemExit(
            f"[shallow-depth] h{HEAD} stored feature_order != current FEATURES_ALL "
            f"({len(order)} vs {len(FEATURES_ALL)})"
        )
    if int(bundle.get("horizon", -1)) != HEAD:
        raise SystemExit(f"[shallow-depth] h{HEAD} stored horizon {bundle.get('horizon')}")
    booster = bundle["lgbm"]
    # The stored head was fitted from a numpy matrix, so the booster itself carries generic
    # column names: the ORDER contract is bundle["feature_order"] (verified against
    # FEATURES_ALL) plus the shared feature_matrix helper, and the booster is checked to be
    # pure-matrix-named (never a different symbolic order).
    names = list(getattr(booster, "feature_name_", []) or [])
    generic = [f"Column_{i}" for i in range(len(order))]
    if names and names != order and names != generic:
        raise SystemExit(
            f"[shallow-depth] h{HEAD} booster feature names match neither the stored "
            "feature_order nor the generic matrix names"
        )
    n_feat = int(getattr(booster, "n_features_in_", 0) or len(order))
    if n_feat != len(order):
        raise SystemExit(f"[shallow-depth] h{HEAD} booster feature count {n_feat} != {len(order)}")
    report = {
        f"h{HEAD}": {
            "path": str(path),
            "sha256": sha256_file(path),
            "horizon": HEAD,
            "feature_order": order,
            "n_features": n_feat,
            "booster_feature_names": (
                "generic matrix names (fitted from a numpy matrix); the order contract is "
                "the bundle's feature_order verified against alpha_open_learned.FEATURES_ALL"
            ),
            "params": bundle.get("params"),
            "clip_fit": bundle.get("clip_fit"),
            "seed": bundle.get("seed"),
            "refit": False,
        },
        "feature_order_source": (
            "alpha_open_learned.FEATURES_ALL (22 path + 4 past-only peer context)"
        ),
        "feature_matrix": (
            "alpha_open_learned.feature_matrix: Float64 in the frozen order, nulls filled "
            "with 0.0 exactly as at fit time; no per-day or per-symbol imputation"
        ),
        "training_immutability": {
            "fitted_on": "2021-02..2022-12 panel days, t%15==0, filled_proxy entries, known labels",
            "replayed_or_rescored_as_outcomes": False,
            "refit": False,
            "hpo": False,
            "feature_changes": False,
            "ticker_or_date_features": False,
        },
    }
    return {HEAD: bundle}, report


# ----- causal execution policy: sizing ----------------------------------------
def capital_quantity(limit_price: float) -> int:
    """Fee-funded capital cap: floor(1000 / (limit * 1.0075)) at the INTENT limit price.

    The 1.0075 factor is the 150bps round-trip reserve with 75bps charged on the funding
    side, sourced in EVERY scenario; the quantity is never re-floored from a later price.
    """
    if not math.isfinite(limit_price) or limit_price <= 0.0:
        return 0
    return int(math.floor(ORDER_BUDGET / (float(limit_price) * FUNDING_SIDE_FACTOR)))


def participation_quantity(participation: float, depth_shares: float) -> int:
    """floor(participation * depth_shares) in EXACT rational arithmetic (no float drift)."""
    if not math.isfinite(depth_shares) or depth_shares < 0.0:
        raise ValueError(f"non-finite displayed depth: {depth_shares!r}")
    exact = PARTICIPATION_EXACT.get(float(participation))
    if exact is None:
        raise ValueError(f"unconfigured participation rate: {participation!r}")
    return int(exact * Fraction(depth_shares))


def view_quantity(
    limit_price: float, participation: float, ask_shares: float, bid_shares: float
) -> int:
    """q = min(capital cap, participation cap on min(intent ask, intent bid) depth)."""
    quantity = capital_quantity(limit_price)
    if participation is not None:
        quantity = min(quantity, participation_quantity(participation, min(ask_shares, bid_shares)))
    return int(quantity)


def reserved_usd(quantity: int, limit_price: float) -> float:
    """Worst-case reservation at the 150bps rung, so no reported rung is unfundable."""
    return float(quantity) * float(limit_price) * FUNDING_SIDE_FACTOR


def intent_gate(facts: dict) -> str:
    """The arm-independent causal gate: acquisition, firmness, freshness, capital cap."""
    if facts["intent_status"] == "quote_not_acquired":
        return "quote_not_acquired"
    if facts["intent_status"] != "quoted":
        return "intent_not_firm_fresh"
    age = facts["intent_age_s"]
    if age is None or not math.isfinite(age) or float(age) > MAX_AGE_S:
        return "intent_not_firm_fresh"
    if int(facts["qty_capital"]) < 1:
        return "no_order_min_capital"
    return "eligible"


def intent_facts(sq: SymbolQuotes | None, intent_us: int) -> dict:
    """The causal intent observation and the sizes it implies (sizing, part 1).

    The limit price, the capital cap and the displayed depths are all read from the latest
    RAW state at the (t+1) clock 0; the depth-participation cap is applied per arm by
    ``arm_eligibility`` because the participation rate is part of the view.
    """
    base = {
        "intent_ask": None,
        "intent_bid": None,
        "intent_age_s": None,
        "intent_spread_bps": None,
        "intent_ask_shares": None,
        "intent_bid_shares": None,
        "limit_price": None,
        "qty_capital": 0,
    }
    if sq is None:
        base["intent_status"] = "quote_not_acquired"
    else:
        q, status = sq.latest_raw(intent_us)
        if q is None:
            base["intent_status"] = status
        else:
            base["intent_status"] = "quoted"
            ask_shares = float(q["ask_shares"])
            bid_shares = float(q["bid_shares"])
            if not (math.isfinite(ask_shares) and math.isfinite(bid_shares)):
                raise ValueError("quoted print without finite displayed sizes")
            limit = limit_price_of(q["ask"])
            base.update(
                {
                    "intent_ask": float(q["ask"]),
                    "intent_bid": float(q["bid"]),
                    "intent_age_s": float(q["age_s"]),
                    "intent_spread_bps": q["spread_bps"],
                    "intent_ask_shares": ask_shares,
                    "intent_bid_shares": bid_shares,
                    "limit_price": limit,
                    "qty_capital": capital_quantity(limit),
                }
            )
    base["intent_reason"] = intent_gate(base)
    return base


def arm_eligibility(facts: dict, arm: View, pred: float) -> tuple[str, int]:
    """Arm outcome for one intent: a causal skip reason or ``"eligible"`` plus its quantity.

    The shared intent gate first, then the arm's own quantity rules (the capital cap for a
    fixed-size arm, the participation cap for an adaptive view), then the spread bar on the
    model's own prediction. The freshness gate is an operational eligibility gate, not a
    claim that an older NBBO expired.
    """
    if facts["intent_reason"] != "eligible":
        return facts["intent_reason"], 0
    ask_shares = float(facts["intent_ask_shares"])
    bid_shares = float(facts["intent_bid_shares"])
    if arm.participation is None:
        quantity = int(facts["qty_capital"])
        if quantity > ask_shares:
            return "intent_depth_unsupported", 0
    else:
        quantity = view_quantity(facts["limit_price"], arm.participation, ask_shares, bid_shares)
        if quantity < 1:
            return "no_order_zero_depth", 0
        if quantity > ask_shares:  # defensive: participation < 1 makes this unreachable
            return "intent_depth_unsupported", 0
    spread = facts["intent_spread_bps"]
    if spread is None or float(spread) + ENTRY_OVERHEAD_BPS > 10_000.0 * float(pred):
        return "spread_above_prediction", 0
    return "eligible", quantity


def depth_thin_keep(row: dict) -> bool:
    """The registered refinement's ONLY new gate: keep the thin displayed side, drop the thick.

    ``min(intent_ask_shares, intent_bid_shares) < DEPTH_FILTER_MAX_SHARES``, both values read
    from the stored causal intent observation at the (t+1) clock - the same latest-RAW state
    the participation cap consumes, so the filter adds no clock, no data and no look-ahead.
    A candidate whose thinner displayed side is 500 shares or more never reaches the arm's
    own quantity or spread rules; it is counted under ``DEPTH_FILTER_SKIP``.
    """
    ask_shares = row["intent_ask_shares"]
    bid_shares = row["intent_bid_shares"]
    if ask_shares is None or bid_shares is None:
        return False
    ask_shares = float(ask_shares)
    bid_shares = float(bid_shares)
    if not (math.isfinite(ask_shares) and math.isfinite(bid_shares)):
        return False
    return min(ask_shares, bid_shares) < DEPTH_FILTER_MAX_SHARES


def price_entry_ioc(
    sq: SymbolQuotes, arrival_us_: int, limit_price: float, quantity: int
) -> tuple[dict | None, str, int | None]:
    """Price the ENTRY IOC limit at its arrival clock: ONE latest-raw read, never a rest.

    An immediate-or-cancel limit order that did not execute at its arrival is cancelled:
    it can never be repriced from a later, more favourable print. The once-only arrival
    read is the published IOC leg (``entry_price_leg``), which evaluates the latest RAW
    state exactly once and never rests; the MARKET-style rest branch of the shared
    ``priced_leg`` helper is deliberately not inherited.

    The signature and the return triple stay this producer's OWN rather than a call
    through to the published helper: this leg also carries the pre-declared limit match,
    the displayed-ASK-depth check, this study's ``unknown_entry_*`` status vocabulary and
    the fill-clock rule (the arrival clock for an executed or no-matched order, never the
    prior print's own timestamp; ``None`` for an UNKNOWN), so it is a real consumer of the
    published read, not an alias or a forwarding wrapper for it.
    """
    q, status = entry_price_leg(sq, arrival_us_)
    if q is None:
        if status == "unknown_stale_but_valid_at_arrival":
            # the kernel's stale-arrival verdict, in this study's own status vocabulary
            return None, "unknown_entry_stale_but_valid", None
        return None, f"unknown_entry_{status}", None
    if float(q["ask"]) > float(limit_price) + 1e-12:
        return None, "no_match_at_l1", arrival_us_
    if float(q["ask_shares"]) < quantity:
        return None, "unknown_entry_partial_depth", None
    return q, "conditional_ioc_fill", arrival_us_


def net_usd(quantity: int, exit_bid: float, entry_ask: float, cost_bps: float) -> float:
    """Realized dollars of the causal round trip at one residual rung (fees both legs)."""
    side = cost_bps / 20_000.0
    return float(quantity) * (float(exit_bid) * (1.0 - side) - float(entry_ask) * (1.0 + side))


def resolve_trade(
    day: str,
    r: dict,
    arm: View,
    quantity: int,
    sq: SymbolQuotes,
    session_end: int,
    session_end_us: int,
) -> dict:
    """Resolve one funded intent: the entry IOC limit leg, then the scheduled exit leg.

    Both legs read only clocks at/after their own submission; nothing here re-sizes the
    causal quantity or substitutes a more favourable print. The exit's REAL execution clock
    (the priced leg's own returned price clock) is preserved: the arrival clock when the
    book was fresh, the first future regular print's timestamp when the working market
    order had to rest - never the planned arrival and never a prior print's quote stamp.
    """
    intent_us = int(r["intent_us"])
    limit_price = float(r["limit_price"])
    reserved = reserved_usd(quantity, limit_price)
    record = {
        "day": day,
        "arm_key": arm.key,
        "threshold": arm.threshold,
        "participation": arm.participation,
        "ticker": r["ticker"],
        "t": int(r["t"]),
        "entry_minute": int(r["entry_minute"]),
        "intent_us": intent_us,
        "score": float(r["pred_60"]),
        "pred": float(r["pred_60"]),
        "limit_price": limit_price,
        "qty": int(quantity),
        "qty_capital": int(r["qty_capital"]),
        "deployed_reserved_usd": reserved,
        "intent_ask_shares": float(r["intent_ask_shares"]),
        "intent_bid_shares": float(r["intent_bid_shares"]),
        "proxy_gross": r["proxy_gross_60"],
        "proxy_entry_status": r["entry_status"],
        "entry_status": None,
        "entry_ask": None,
        "entry_bid": None,
        "entry_age_s": None,
        "entry_quote_us": None,
        "entry_arrival_us": arrival_us(intent_us),
        "exit_minute": min(int(r["entry_minute"]) + HEAD, session_end),
        "exit_submit_us": None,
        "exit_submit_status": None,
        "exit_arrival_us": None,
        "exit_bid": None,
        "exit_bid_shares": None,
        "exit_age_s": None,
        "exit_quote_us": None,
        "exit_status": None,
        "exit_priced_at_status": None,
        "actual_exit_us": None,
        "fill_status": None,
    }
    for rung in RUNG_COSTS:
        record[f"net_usd_{int(rung)}"] = None

    # ---- entry leg: the causal IOC limit was fixed at the intent clock and is priced
    #      ONCE at the arrival clock (no resting into any future print).
    entry_arrival = record["entry_arrival_us"]
    eq, estat, _fill_clock = price_entry_ioc(sq, entry_arrival, limit_price, int(quantity))
    if eq is None:
        record["entry_status"] = estat
        if estat == "no_match_at_l1":
            # A firm, fresh book above the cap: the live IOC did not execute and was
            # cancelled, so the ticket is cash released at the arrival clock itself.
            record["fill_status"] = "no_match_at_l1_unfilled_cash"
            record["actual_exit_us"] = entry_arrival
        else:
            record["fill_status"] = "unknown_entry_execution"
            record["exit_status"] = estat
            record["actual_exit_us"] = session_end_us + 1
        return record
    record["entry_ask"] = float(eq["ask"])
    record["entry_bid"] = float(eq["bid"])
    record["entry_age_s"] = float(eq["age_s"])
    record["entry_quote_us"] = int(eq["quote_us"])
    record["entry_status"] = "conditional_ioc_fill"

    # ---- exit leg: fresh-quote-gated submission, +250ms after the ACTUAL submission.
    exit_minute = record["exit_minute"]
    due_us = minute_us(day, exit_minute)
    submit_us, submit_status = submission_clock(sq, due_us, session_end_us)
    record["exit_submit_us"] = submit_us
    record["exit_submit_status"] = submit_status
    if submit_us is None:
        record["fill_status"] = "unknown_exit_execution"
        record["exit_status"] = "unknown_exit_no_valid_regular_quote"
        record["actual_exit_us"] = session_end_us + 1
        return record
    exit_arrival = arrival_us(submit_us)
    record["exit_arrival_us"] = exit_arrival
    xq, xstat, price_clock = priced_leg(sq, exit_arrival, session_end_us)
    if xq is None:
        record["fill_status"] = "unknown_exit_execution"
        record["exit_status"] = xstat
        record["actual_exit_us"] = session_end_us + 1
        return record
    record["exit_priced_at_status"] = xstat
    record["exit_bid"] = float(xq["bid"])
    record["exit_bid_shares"] = float(xq["bid_shares"])
    record["exit_age_s"] = float(xq["age_s"])
    record["exit_quote_us"] = int(xq["quote_us"])
    if float(xq["bid_shares"]) < int(quantity):
        record["fill_status"] = "unknown_exit_execution"
        record["exit_status"] = "unknown_exit_partial_depth"
        record["actual_exit_us"] = session_end_us + 1
        return record
    record["exit_status"] = "conditional_market_fill"
    record["fill_status"] = "conditional_fill"
    # The REAL execution clock: the arrival clock when priced fresh, the first future
    # regular print's timestamp when the working order had to rest. Cash, the slot and the
    # cooldown are anchored here - never at a planned or earlier clock.
    record["actual_exit_us"] = int(price_clock)
    for rung in RUNG_COSTS:
        record[f"net_usd_{int(rung)}"] = net_usd(
            int(quantity), record["exit_bid"], record["entry_ask"], rung
        )
    return record


SKIP_REASONS = (
    "no_order_min_capital",
    "no_order_zero_depth",
    "quote_not_acquired",
    "intent_not_firm_fresh",
    "intent_depth_unsupported",
    DEPTH_FILTER_SKIP,
    "spread_above_prediction",
    "overlap_skips",
    "cooldown_skips",
    "max_attempt_skips",
    "cash_or_slot_skips",
)


def replay_day(
    day: str, states: pl.DataFrame, streams: dict[str, SymbolQuotes], arm: View
) -> tuple[dict, list[dict]]:
    """One arm, one session: fresh-quote-gated entries under the funded $3,000 book.

    Chronological by intent clock; the highest-forecast eligible intents of a simultaneous
    clock are funded (up to the slot and cash limits, cash reserved first) BEFORE any
    arrival outcome is seen, so a fourth same-clock candidate is never substituted after an
    unfilled one. Cash comes back only at the REAL execution clock of a position (an actual
    exit price clock, an unmatched entry's arrival, or the session end).

    This lane's ONE change from the parent: between the arm-independent intent gate and the
    arm's own quantity/spread rules, the registered shallow-displayed-depth filter
    (``depth_thin_keep``) drops every candidate whose thinner displayed side at the intent
    clock is ``DEPTH_FILTER_MAX_SHARES`` shares or more. Everything downstream - funding,
    arrival pricing, exit pricing, UNKNOWNs - is the parent lane's verbatim.
    """
    daily = dict.fromkeys(DAILY_COLUMNS, 0)
    daily.update(
        {
            "day": day,
            "arm_key": arm.key,
            "threshold": arm.threshold,
            "participation": arm.participation,
            "is_fixed_size_baseline": bool(arm.is_baseline),
            "deployed_reserved_usd": 0.0,
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
    candidates = states.filter(pl.col("pred_60") >= arm.threshold).sort(
        ["entry_minute", "pred_60", "ticker"], descending=[False, True, False]
    )
    groups: dict[int, list[dict]] = {}
    for r in candidates.iter_rows(named=True):
        groups.setdefault(int(r["entry_minute"]), []).append(r)
    daily["signals"] = int(candidates.height)

    cash, active = float(BOOK), set()
    attempts: dict[str, int] = {}
    cooldown_until: dict[str, int] = {}
    queue: list[tuple[int, int, str, float]] = []
    open_reserved: dict[str, float] = {}  # per-ticker reserved cash of the OPEN positions
    seq = 0
    trades: list[dict] = []
    positions_samples: list[int] = []
    for minute in sorted(groups):
        intent_us = minute_us(day, minute)
        while queue and queue[0][0] <= intent_us:
            _, _, sym, proceeds = heapq.heappop(queue)
            active.discard(sym)
            cash += proceeds
            open_reserved.pop(sym, None)
        admitted: list[tuple[dict, int]] = []
        for r in groups[minute]:
            sym = r["ticker"]
            # (1) the parent lane's arm-independent causal intent gate, verbatim.
            if r["intent_reason"] != "eligible":
                daily[r["intent_reason"]] += 1
                continue
            # (2) THE registered refinement: the shallow-displayed-depth filter at the
            #     intent clock (the only added gate; nothing else in the loop changed).
            if not depth_thin_keep(r):
                daily[DEPTH_FILTER_SKIP] += 1
                continue
            # (3) the arm's own quantity and spread rules, unchanged.
            reason, quantity = arm_eligibility(r, arm, float(r["pred_60"]))
            if reason != "eligible":
                daily[reason] += 1
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
            reserved = reserved_usd(quantity, float(r["limit_price"]))
            if len(active) >= MAX_SLOTS or cash + 1e-9 < reserved:
                daily["cash_or_slot_skips"] += 1
                continue
            attempts[sym] = int(attempts.get(sym, 0)) + 1
            cash -= reserved
            active.add(sym)
            open_reserved[sym] = reserved
            positions_samples.append(len(active))
            # Reserved cash only grows at an admission and shrinks at a release, so the
            # true concurrent peak is the running sum of the OPEN reservations sampled at
            # every admission - never the day's cumulative deployed total.
            daily["peak_reserved_usd"] = max(
                daily["peak_reserved_usd"], float(sum(open_reserved.values()))
            )
            daily["deployed_reserved_usd"] += reserved
            daily["intents_funded"] += 1
            daily["attempts"] += 1
            admitted.append((r, quantity))
        # Every intent of this clock is funded before any arrival outcome is observed.
        for r, quantity in admitted:
            record = resolve_trade(
                day, r, arm, quantity, streams[r["ticker"]], session_end, session_end_us
            )
            record["attempt_index"] = int(attempts[r["ticker"]])
            record["positions_at_entry"] = len(active)
            status = record["fill_status"]
            release = int(record["actual_exit_us"])
            if status == "no_match_at_l1_unfilled_cash":
                # No position was opened: the ticket is released at the arrival clock and
                # there is no exit to anchor a cooldown on (the attempt still counts).
                daily["no_match_fills"] += 1
            else:
                daily["unknown_fills" if "unknown" in status else "fills"] += 1
                if status == "conditional_fill":
                    daily["known_fills"] += 1
                    for rung in RUNG_COSTS:
                        daily[f"known_usd_{int(rung)}"] += float(record[f"net_usd_{int(rung)}"])
                # A position - conditional or UNKNOWN - is open until its ACTUAL
                # resolution, and the ticker is then blocked for a flat 15 minutes.
                cooldown_until[r["ticker"]] = release + COOLDOWN_MIN * 60_000_000
            heapq.heappush(
                queue, (release, seq, r["ticker"], float(record["deployed_reserved_usd"]))
            )
            seq += 1
            trades.append(record)
    daily["positions_peak"] = max(positions_samples) if positions_samples else 0
    daily["positions_mean"] = float(np.mean(positions_samples)) if positions_samples else 0.0
    for rung in RUNG_COSTS:
        daily[f"lower_bound_usd_{int(rung)}"] = (
            daily[f"known_usd_{int(rung)}"] - ORDER_BUDGET * daily["unknown_fills"]
        )
    return daily, trades


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
    "pred_60",
    "entry_minute",
    "intent_us",
    "intent_status",
    "intent_reason",
    "intent_ask",
    "intent_bid",
    "intent_age_s",
    "intent_spread_bps",
    "intent_ask_shares",
    "intent_bid_shares",
    "limit_price",
    "qty_capital",
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
    "pred_60": pl.Float64,
    "entry_minute": pl.Int64,
    "intent_us": pl.Int64,
    "intent_status": pl.String,
    "intent_reason": pl.String,
    "intent_ask": pl.Float64,
    "intent_bid": pl.Float64,
    "intent_age_s": pl.Float64,
    "intent_spread_bps": pl.Float64,
    "intent_ask_shares": pl.Float64,
    "intent_bid_shares": pl.Float64,
    "limit_price": pl.Float64,
    "qty_capital": pl.Int64,
}


def empty_states() -> pl.DataFrame:
    return pl.DataFrame(schema={c: STATE_TYPES[c] for c in STATE_COLUMNS})


def supplement_digest(roots: tuple[Path, ...]) -> tuple[str, list[dict]]:
    """Digest of the supplement caches that actually exist (drives resume invalidation)."""
    entries = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.glob("????-??-??.parquet")):
            entries.append(
                {
                    "path": str(path),
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                }
            )
    return _digest_bytes({"supplements": entries}), entries


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


def score_day(
    day: str, models: dict, data_root: Path, supplemental_roots: tuple[Path, ...]
) -> tuple[pl.DataFrame, dict]:
    """Score one session's liquidity-qualified panel states and observe each intent quote.

    One panel frame, one quote frame and one day's states are resident at a time; the full
    corpus is never concatenated. The intent facts are causal (the latest RAW state at the
    (t+1) clock 0), the per-threshold eligibility is reported, and the panel's own
    minute-open label is carried along for the basis comparison only - never as a filter.
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
        "no_order_min_capital": 0,
        "quote_not_acquired": 0,
    }
    if not cand.height:
        return empty_states(), cov
    tickers = sorted(set(cand["ticker"].to_list()))
    cov["tickers_qualified"] = len(tickers)
    preds = np.asarray(models[HEAD]["lgbm"].predict(feature_matrix(cand)), dtype=float)
    quote_frame = quote_day_frame(data_root, day, set(tickers), supplemental_roots)
    streams = symbol_quotes(quote_frame, day)
    cov["tickers_with_quote_stream"] = len(streams)
    if not day_quote_path(data_root, day).exists():
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
    intent_counts: Counter = Counter()
    reason_counts: Counter = Counter()
    eligible_counts: Counter = Counter()
    out: list[dict] = []
    session_end = int(rows[0]["session_end"])
    for i, r in enumerate(rows):
        entry_minute = int(r["t"]) + 1
        intent_us = minute_us(day, entry_minute)
        sq = streams.get(r["ticker"])
        facts = intent_facts(sq, intent_us)
        intent_counts[facts["intent_status"]] += 1
        reason_counts[facts["intent_reason"]] += 1
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
            "pred_60": float(preds[i]),
            "entry_minute": entry_minute,
            "intent_us": intent_us,
        }
        rec.update(facts)
        if facts["intent_reason"] == "eligible":
            spread = float(facts["intent_spread_bps"])
            for threshold in THRESHOLDS:
                if spread + ENTRY_OVERHEAD_BPS <= 10_000.0 * float(preds[i]):
                    eligible_counts[f"thr{int(round(threshold * 10_000))}bps"] += 1
        out.append(rec)
    cov["intent_status_counts"] = dict(intent_counts)
    cov["intent_reason_counts"] = dict(reason_counts)
    cov["entry_rule_counts"] = dict(eligible_counts)
    cov["no_order_min_capital"] = int(reason_counts.get("no_order_min_capital", 0))
    cov["quote_not_acquired"] = int(reason_counts.get("quote_not_acquired", 0))
    frame = pl.DataFrame(
        {c: [rec[c] for rec in out] for c in STATE_COLUMNS},
        schema={c: STATE_TYPES[c] for c in STATE_COLUMNS},
    )
    return frame, cov


def state_paths(out_dir: Path, day: str) -> tuple[Path, Path]:
    return out_dir / "states" / f"{day}.parquet", out_dir / "states" / f"{day}.cov.json"


def day_states_sha256(out_dir: Path, day: str) -> str | None:
    """The day's OWN scored-states sha256, read per day (never a value left from another day).

    The states cov record carries score_block's ``sha256_file(states parquet)``; an
    unreadable record falls back to hashing that record itself, exactly as the resume
    lookup does. Every pass calls this per day so a replay coverage record can only ever
    carry its own day's states hash, and the recorded hash is by construction the same
    value the day's resume hash was computed from.
    """
    _, spath = state_paths(out_dir, day)
    if not spath.exists():
        return None
    try:
        sha = json.loads(spath.read_text()).get("states_sha256")
    except (json.JSONDecodeError, OSError):
        sha = None
    if sha is None:
        sha = sha256_file(spath)
    return sha


def score_block(
    out_dir: Path, models: dict, days: list[str], resume: bool, supplemental_roots: tuple[Path, ...]
) -> dict:
    """Build (or resume) every day's scored states atomically, one day at a time."""
    producer = producer_sha256()
    contract_sha = _digest_bytes(CONTRACT)
    sup_digest, sup_entries = supplement_digest(supplemental_roots)
    model_shas = {f"h{HEAD}": sha256_file(MODEL_DIR / f"payoff_h{HEAD}.joblib")}
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
                "day": day,
                "producer": producer,
                "contract": contract_sha,
                "supplements": sup_digest,
                "models": model_shas,
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
                        "day": day,
                        "producer": producer,
                        "contract": contract_sha,
                        "supplements": sup_digest,
                        "models": model_shas,
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
        if n % 25 == 0 or n == len(todo):
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
                    default=_default,
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


# ----- per-arm causal replay --------------------------------------------------
DAILY_COLUMNS = (
    "day",
    "arm_key",
    "threshold",
    "participation",
    "is_fixed_size_baseline",
    "signals",
    "intents_funded",
    "attempts",
    "fills",
    "known_fills",
    "unknown_fills",
    "no_match_fills",
    *(f"known_usd_{int(r)}" for r in RUNG_COSTS),
    *(f"lower_bound_usd_{int(r)}" for r in RUNG_COSTS),
    "deployed_reserved_usd",
    "peak_reserved_usd",
    "positions_peak",
    "positions_mean",
    *SKIP_REASONS,
)
DAILY_TYPES = {
    "day": pl.String,
    "arm_key": pl.String,
    "threshold": pl.Float64,
    "participation": pl.Float64,
    "is_fixed_size_baseline": pl.Boolean,
    "signals": pl.Int64,
    "intents_funded": pl.Int64,
    "attempts": pl.Int64,
    "fills": pl.Int64,
    "known_fills": pl.Int64,
    "unknown_fills": pl.Int64,
    "no_match_fills": pl.Int64,
    "deployed_reserved_usd": pl.Float64,
    "peak_reserved_usd": pl.Float64,
    "positions_peak": pl.Int64,
    "positions_mean": pl.Float64,
    **dict.fromkeys(SKIP_REASONS, pl.Int64),
    **{f"known_usd_{int(r)}": pl.Float64 for r in RUNG_COSTS},
    **{f"lower_bound_usd_{int(r)}": pl.Float64 for r in RUNG_COSTS},
}
TRADE_COLUMNS = (
    "day",
    "arm_key",
    "threshold",
    "participation",
    "ticker",
    "t",
    "entry_minute",
    "intent_us",
    "score",
    "pred",
    "limit_price",
    "qty",
    "qty_capital",
    "deployed_reserved_usd",
    "intent_ask_shares",
    "intent_bid_shares",
    "attempt_index",
    "positions_at_entry",
    "entry_status",
    "entry_ask",
    "entry_bid",
    "entry_age_s",
    "entry_quote_us",
    "entry_arrival_us",
    "exit_minute",
    "exit_submit_us",
    "exit_submit_status",
    "exit_arrival_us",
    "exit_bid",
    "exit_bid_shares",
    "exit_age_s",
    "exit_quote_us",
    "exit_status",
    "exit_priced_at_status",
    "actual_exit_us",
    "fill_status",
    *(f"net_usd_{int(r)}" for r in RUNG_COSTS),
    "proxy_gross",
    "proxy_entry_status",
)
TRADE_TYPES = {
    "day": pl.String,
    "arm_key": pl.String,
    "threshold": pl.Float64,
    "participation": pl.Float64,
    "ticker": pl.String,
    "t": pl.Int64,
    "entry_minute": pl.Int64,
    "intent_us": pl.Int64,
    "score": pl.Float64,
    "pred": pl.Float64,
    "limit_price": pl.Float64,
    "qty": pl.Int64,
    "qty_capital": pl.Int64,
    "deployed_reserved_usd": pl.Float64,
    "intent_ask_shares": pl.Float64,
    "intent_bid_shares": pl.Float64,
    "attempt_index": pl.Int64,
    "positions_at_entry": pl.Int64,
    "entry_status": pl.String,
    "entry_ask": pl.Float64,
    "entry_bid": pl.Float64,
    "entry_age_s": pl.Float64,
    "entry_quote_us": pl.Int64,
    "entry_arrival_us": pl.Int64,
    "exit_minute": pl.Int64,
    "exit_submit_us": pl.Int64,
    "exit_submit_status": pl.String,
    "exit_arrival_us": pl.Int64,
    "exit_bid": pl.Float64,
    "exit_bid_shares": pl.Float64,
    "exit_age_s": pl.Float64,
    "exit_quote_us": pl.Int64,
    "exit_status": pl.String,
    "exit_priced_at_status": pl.String,
    "actual_exit_us": pl.Int64,
    "fill_status": pl.String,
    "proxy_gross": pl.Float64,
    "proxy_entry_status": pl.String,
    **{f"net_usd_{int(r)}": pl.Float64 for r in RUNG_COSTS},
}


def empty_daily() -> pl.DataFrame:
    return pl.DataFrame(schema={c: DAILY_TYPES[c] for c in DAILY_COLUMNS})


def empty_trades() -> pl.DataFrame:
    return pl.DataFrame(schema={c: TRADE_TYPES[c] for c in TRADE_COLUMNS})


def replay_block(
    out_dir: Path, block: str, days: list[str], resume: bool, supplemental_roots: tuple[Path, ...]
) -> dict:
    """Replay every arm on every day of one block, writing resumable per-day parts."""
    producer = producer_sha256()
    contract_sha = _digest_bytes(CONTRACT)
    sup_digest, _ = supplement_digest(supplemental_roots)
    model_shas = {f"h{HEAD}": sha256_file(MODEL_DIR / f"payoff_h{HEAD}.joblib")}
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
        "no_match_fills": 0,
    }
    for day in days:
        dpath = root / f"{day}.parquet"
        tpath = root / f"{day}.trades.parquet"
        cpath = root / f"{day}.cov.json"
        states_sha = day_states_sha256(out_dir, day)
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
                f"[shallow-depth] scored states missing for {day} ({states_path}); run the "
                "score stage for the whole block before replaying it"
            )
        # THIS day's states hash, recomputed per day: the record must never carry the
        # value left in a loop variable from another day's lookup.
        day_states_sha = day_states_sha256(out_dir, day)
        tickers = sorted(set(states["ticker"].to_list())) if states.height else []
        quote_frame = (
            quote_day_frame(DATA_ROOT, day, set(tickers), supplemental_roots) if tickers else None
        )
        streams = symbol_quotes(quote_frame, day)
        del quote_frame
        daily_rows, trade_rows = [], []
        day_cov = {
            "qualified_rows": int(states.height) if states.height else 0,
            "quote_streams": len(streams),
            "arms": {},
        }
        for arm in ARMS:
            daily, trades = replay_day(day, states, streams, arm)
            daily_rows.append(daily)
            trade_rows.extend(trades)
            day_cov["arms"][arm.key] = {
                "signals": daily["signals"],
                "attempts": daily["attempts"],
                "fills": daily["fills"],
                "unknown_fills": daily["unknown_fills"],
                "no_match_fills": daily["no_match_fills"],
                "deployed_reserved_usd": daily["deployed_reserved_usd"],
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
                "resume_hash": expects[day],
                "producer_sha256": producer,
                "contract_sha256": contract_sha,
                "supplement_digest": sup_digest,
                "states_sha256": day_states_sha,
                "schema": REPLAY_SCHEMA,
                "coverage": day_cov,
            },
        )
        infos.append({"day": day, "resumed": False, "coverage": day_cov})
        if n % 25 == 0 or n == len(todo):
            funded = sum(v["attempts"] for v in day_cov["arms"].values())
            print(
                f"[replay:{block}] {n}/{len(todo)} days, last {day}, "
                f"funded_intents={funded} over {len(ARMS)} arms",
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
                    default=_default,
                ),
                flush=True,
            )
    for info in infos:
        cov = info["coverage"] or {}
        coverage["signals"] += sum(v.get("signals", 0) for v in (cov.get("arms") or {}).values())
        coverage["intents_funded"] += sum(
            v.get("attempts", 0) for v in (cov.get("arms") or {}).values()
        )
        coverage["fills"] += sum(v.get("fills", 0) for v in (cov.get("arms") or {}).values())
        coverage["unknown_fills"] += sum(
            v.get("unknown_fills", 0) for v in (cov.get("arms") or {}).values()
        )
        coverage["no_match_fills"] += sum(
            v.get("no_match_fills", 0) for v in (cov.get("arms") or {}).values()
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
    """Per-month traded-day / fill / UNKNOWN accounting with the known contribution."""
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
            "known_usd_25": float(rows["known_usd_25"].sum()),
            "deployed_reserved_usd": float(rows["deployed_reserved_usd"].sum()),
            "mean_daily_known_usd_25": float(rows["known_usd_25"].mean()),
        }
    return out


def yearly_view(daily: pl.DataFrame) -> dict:
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
            "known_usd_25": float(rows["known_usd_25"].sum()),
            "deployed_reserved_usd": float(rows["deployed_reserved_usd"].sum()),
            "mean_daily_known_usd_25": float(rows["known_usd_25"].mean()),
        }
    return out


def _empty_trade_stats(rung_keys: list[int]) -> dict:
    return {
        "funded_intents": 0,
        "conditional_fills": 0,
        "mean_net_usd_per_conditional_fill": {str(r): None for r in rung_keys},
        "worst_known_fill_usd": {str(r): None for r in rung_keys},
        "win_rate_known_fill": {str(r): None for r in rung_keys},
        "mean_proxy_gross_minute_open_basis": None,
        "mean_touch_gross_25bps": None,
        "mean_qty": None,
        "mean_deployed_usd_per_funded_intent": None,
        "mean_hold_minutes": None,
        "exit_l1_bid_depth_shortfall_unknowns": 0,
        "fill_status_counts": {},
        "exit_submit_status_counts": {},
        "exit_priced_at_status_counts": {},
    }


def calendar_days_spanned(days: list[str]) -> int:
    """The block's calendar-day count: first..last session day INCLUSIVE.

    A panel session is a trading day, so this count includes every weekend and holiday
    between the block's first and last session. It is a DIFFERENT denominator from the
    panel-session count and the two rates must never share one key name: a per-session
    rate answers "per trading session", a per-calendar-day rate answers "per day of
    wall-clock time". Computed from the block's own day list, so a restricted smoke
    calendar is labelled honestly too.
    """
    if not days:
        return 0
    return (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days + 1


def view_cell(daily: pl.DataFrame, trades: pl.DataFrame, arm: View, days: list[str]) -> dict:
    """Every reported number for one arm on one block's calendar."""
    rows = daily.filter(pl.col("arm_key") == arm.key)
    n_sessions = len(days)
    n_calendar_days = calendar_days_spanned(days)
    if int(rows.height) != n_sessions:
        raise ValueError(
            f"arm {arm.key}: {rows.height} daily rows on disk for {n_sessions} panel "
            "sessions; replay the whole block before aggregating"
        )
    rung_keys = [int(r) for r in RUNG_COSTS]
    known_total = {r: float(rows[f"known_usd_{r}"].sum()) for r in rung_keys}
    known_per_session = {r: known_total[r] / n_sessions for r in rung_keys}
    known_per_calendar_day = {r: known_total[r] / n_calendar_days for r in rung_keys}
    bound_total = {r: float(rows[f"lower_bound_usd_{r}"].sum()) for r in rung_keys}
    bound_per_session = {r: bound_total[r] / n_sessions for r in rung_keys}
    bound_per_calendar_day = {r: bound_total[r] / n_calendar_days for r in rung_keys}
    deployed_total = float(rows["deployed_reserved_usd"].sum())
    funded = int(rows["intents_funded"].sum())
    known_fills = int(rows["known_fills"].sum())
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
        "arm_key": arm.key,
        "label": arm.label,
        "threshold": arm.threshold,
        "participation": arm.participation,
        "is_fixed_size_baseline": bool(arm.is_baseline),
        "days_replayed": n_sessions,
        "calendar_days_spanned": n_calendar_days,
        "signals": int(rows["signals"].sum()),
        "intents_funded": funded,
        "attempts": int(rows["attempts"].sum()),
        "fills": int(rows["fills"].sum()),
        "known_fills": known_fills,
        "unknown_fills": int(rows["unknown_fills"].sum()),
        "no_match_fills": int(rows["no_match_fills"].sum()),
        "traded_days": int((rows["attempts"] > 0).sum()),
        "positions_peak": int(rows["positions_peak"].max() or 0),
        "positions_mean": float(rows["positions_mean"].mean()),
        "known_usd": {str(r): known_total[r] for r in rung_keys},
        # The three per-day families: the legacy keys keep their historical VALUES (a
        # per-session rate) for backward compatibility, and the explicitly named keys say
        # which denominator they actually use. See per_day_denominator_note below.
        "known_usd_per_calendar_day": {str(r): known_per_session[r] for r in rung_keys},
        "known_usd_per_panel_session": {str(r): known_per_session[r] for r in rung_keys},
        "known_usd_per_calendar_day_span": {str(r): known_per_calendar_day[r] for r in rung_keys},
        "full_loss_lower_bound_usd_per_calendar_day": {
            str(r): bound_per_session[r] for r in rung_keys
        },
        "full_loss_lower_bound_usd_per_panel_session": {
            str(r): bound_per_session[r] for r in rung_keys
        },
        "full_loss_lower_bound_usd_per_calendar_day_span": {
            str(r): bound_per_calendar_day[r] for r in rung_keys
        },
        "lower_bound_is_coding_convention_not_ev": (
            "the lower bound charges every UNKNOWN execution a full $1,000 ticket loss; it "
            "is a coded convention driven by how many legs stayed unpriced, NOT observed "
            "PnL and NOT an expected loss of that size"
        ),
        "per_day_denominator_note": (
            "the historical *_per_calendar_day keys divide by PANEL SESSIONS "
            f"(days_replayed = {n_sessions}), NOT calendar days - they are kept verbatim for "
            "backward compatibility and are repeated under the honest name "
            "*_per_panel_session; the *_per_calendar_day_span keys divide by the block's "
            f"calendar-day span, first..last session day inclusive (calendar_days_spanned = "
            f"{n_calendar_days})"
        ),
        "supported_share_of_funded_intents": (known_fills / funded) if funded else None,
        "unknown_share_of_funded_intents": (
            float(rows["unknown_fills"].sum() / funded) if funded else None
        ),
        "deployed_capital_usd_total": deployed_total,
        "deployed_capital_usd_per_calendar_day": deployed_total / n_sessions,
        "deployed_capital_usd_per_panel_session": deployed_total / n_sessions,
        "deployed_capital_usd_per_calendar_day_span": deployed_total / n_calendar_days,
        "known_usd_25_per_deployed_dollar": (
            known_total[25] / deployed_total if deployed_total > 0 else None
        ),
        "dollars_per_year_252_on_book_3000": {
            str(r): known_per_session[r] * ANNUAL_SESSIONS for r in rung_keys
        },
        "annualization": (
            f"mean per-session known contribution x {ANNUAL_SESSIONS} sessions on a "
            f"${int(BOOK)} nominal book; a simple session-count convention on a daily-reset "
            "research book, NOT a CAGR and NOT an account claim"
        ),
        "bootstrap_daily_known_usd": bootstrap,
        "bootstrap_daily_return_on_book": series_book,
        "skip_reasons": {reason: int(rows[reason].sum()) for reason in SKIP_REASONS},
        "monthly": monthly_view(rows),
        "yearly": yearly_view(rows),
    }
    trades_view = trades.filter(pl.col("arm_key") == arm.key)
    if not trades_view.height:
        cell["trade_stats"] = _empty_trade_stats(rung_keys)
        return cell
    filled = trades_view.filter(pl.col("fill_status") == "conditional_fill")
    nets = {str(r): (filled[f"net_usd_{r}"].to_list() if filled.height else []) for r in rung_keys}
    shortfalls = int(
        (trades_view["exit_status"] == "unknown_exit_partial_depth").sum()
        if trades_view.height
        else 0
    )
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
            str(r): (float(np.mean(np.asarray(v) > 0)) if len(v) else None) for r, v in nets.items()
        },
        "mean_proxy_gross_minute_open_basis": (
            float(filled["proxy_gross"].mean()) if filled.height else None
        ),
        "mean_touch_gross_25bps": (
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
        "mean_qty": (float(filled["qty"].mean()) if filled.height else None),
        "mean_deployed_usd_per_funded_intent": (
            float(trades_view["deployed_reserved_usd"].mean()) if trades_view.height else None
        ),
        "mean_hold_minutes": (
            float((filled["actual_exit_us"] - filled["intent_us"]).mean() / 60_000_000.0)
            if filled.height
            else None
        ),
        "exit_l1_bid_depth_shortfall_unknowns": shortfalls,
        "fill_status_counts": dict(
            trades_view.group_by("fill_status").len().sort("len", descending=True).iter_rows()
        ),
        "exit_submit_status_counts": dict(
            trades_view.group_by("exit_submit_status")
            .len()
            .sort("len", descending=True)
            .iter_rows()
        ),
        "exit_priced_at_status_counts": dict(
            trades_view.group_by("exit_priced_at_status")
            .len()
            .sort("len", descending=True)
            .iter_rows()
        ),
    }
    return cell


def baseline_comparison(surface: dict) -> dict:
    """Known net $ absolute and deployed capital density vs the fixed-size baseline arms.

    The baseline is a SIMULATED arm (same signals, entry rule, exit rule, book and ladder,
    capital-cap quantity only), so a smaller adaptive ticket is never assumed to scale PnL
    linearly: at a fixed size the displayed depth support and the exit capacity bind, which
    is exactly the shortfall the participation cap is meant to reduce.

    The two arms also do NOT trade the same intent set: the fixed-size arm SKIPS an intent
    whose capital-cap ticket exceeds the displayed ask depth (``intent_depth_unsupported``)
    while the adaptive arm caps the ticket down and ATTEMPTS it. Every entry therefore
    carries both arms' intent-set sizes and admission rules beside the numbers, and the
    dollar comparison is explicitly annotated as NOT a like-for-like 'adaptive beats
    fixed' claim: the honest reads are which arm is positive on a block and the known $
    per deployed dollar, never the absolute $/day delta.
    """
    out = {}
    for arm in VIEWS:
        base = BASELINE_BY_THRESHOLD[arm.threshold]
        adaptive, fixed = surface[arm.key], surface[base.key]
        a_known = adaptive["known_usd"]["25"]
        f_known = fixed["known_usd"]["25"]
        a_deployed = adaptive["deployed_capital_usd_total"]
        f_deployed = fixed["deployed_capital_usd_total"]
        f_depth_skips = fixed["skip_reasons"]["intent_depth_unsupported"]
        out[arm.key] = {
            "adaptive_view": arm.label,
            "fixed_size_baseline_arm": base.label,
            "intent_set": {
                "adaptive_attempts": adaptive["attempts"],
                "fixed_size_baseline_attempts": fixed["attempts"],
                "adaptive_intents_funded": adaptive["intents_funded"],
                "fixed_size_baseline_intents_funded": fixed["intents_funded"],
                "adaptive_intent_depth_unsupported_skips": adaptive["skip_reasons"][
                    "intent_depth_unsupported"
                ],
                "fixed_size_baseline_intent_depth_unsupported_skips": f_depth_skips,
            },
            "admission_rule": {
                "adaptive": (
                    "q = min(capital cap, participation x displayed depth): a capital-cap "
                    "ticket wider than the displayed ask is CAPPED DOWN and ATTEMPTED"
                ),
                "fixed_size_baseline": (
                    "q = capital cap only: an intent whose capital-cap ticket exceeds the "
                    "displayed ask depth is SKIPPED (intent_depth_unsupported) and NEVER "
                    "ATTEMPTED, so this arm trades a SMALLER intent set"
                ),
            },
            "known_net_usd_absolute_25": {
                "adaptive": a_known,
                "fixed_size_baseline": f_known,
                "absolute_delta_usd": a_known - f_known,
            },
            "known_usd_per_panel_session_25": {
                "adaptive": adaptive["known_usd_per_panel_session"]["25"],
                "fixed_size_baseline": fixed["known_usd_per_panel_session"]["25"],
            },
            "known_usd_per_calendar_day_span_25": {
                "adaptive": adaptive["known_usd_per_calendar_day_span"]["25"],
                "fixed_size_baseline": fixed["known_usd_per_calendar_day_span"]["25"],
            },
            "deployed_capital_usd_per_panel_session": {
                "adaptive": adaptive["deployed_capital_usd_per_panel_session"],
                "fixed_size_baseline": fixed["deployed_capital_usd_per_panel_session"],
            },
            "deployed_capital_usd_per_calendar_day_span": {
                "adaptive": adaptive["deployed_capital_usd_per_calendar_day_span"],
                "fixed_size_baseline": fixed["deployed_capital_usd_per_calendar_day_span"],
            },
            "deployed_capital_usd_total": {
                "adaptive": a_deployed,
                "fixed_size_baseline": f_deployed,
            },
            "known_usd_25_per_deployed_dollar": {
                "adaptive": adaptive["known_usd_25_per_deployed_dollar"],
                "fixed_size_baseline": fixed["known_usd_25_per_deployed_dollar"],
            },
            "funded_intents": {
                "adaptive": adaptive["intents_funded"],
                "fixed_size_baseline": fixed["intents_funded"],
            },
            "unknown_fills": {
                "adaptive": adaptive["unknown_fills"],
                "fixed_size_baseline": fixed["unknown_fills"],
            },
            "exit_l1_bid_depth_shortfall_unknowns": {
                "adaptive": adaptive["trade_stats"]["exit_l1_bid_depth_shortfall_unknowns"],
                "fixed_size_baseline": fixed["trade_stats"]["exit_l1_bid_depth_shortfall_unknowns"],
            },
            "not_assumed_linear": (
                "the baseline is simulated, not rescaled: a fixed $1,000 ticket loses intent "
                "depth support and exit bid capacity that the participation cap keeps, so "
                "PnL is NOT assumed to scale with ticket size"
            ),
            "comparison_caveat": (
                "NOT a like-for-like 'adaptive beats fixed' claim: the arms trade DIFFERENT "
                f"intent sets ({adaptive['attempts']} adaptive attempts vs "
                f"{fixed['attempts']} fixed-size attempts here; the fixed-size arm skips "
                f"{f_depth_skips} intents outright as intent_depth_unsupported while the "
                "adaptive arm caps down and attempts them), so the absolute $/day delta "
                "mixes intent-set composition with sizing. The defensible reads are which "
                "arm is positive on the block and the known $ per deployed dollar."
            ),
        }
    return out


def aggregate_block(out_dir: Path, block: str, days: list[str]) -> tuple[dict, dict]:
    """The full twelve-arm surface of one block, plus the block's data coverage."""
    daily, trades = read_block_frames(out_dir, block)
    surface = {arm.key: view_cell(daily, trades, arm, days) for arm in ARMS}
    coverage = {
        "block": block,
        "days": len(days),
        "calendar_days_spanned": calendar_days_spanned(days),
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


def fill_diagnostics(cell: dict, trades: pl.DataFrame, arm: View, n_sessions: int) -> dict:
    """The registered diagnoses for one arm on one block, from the funded-intent trades.

    Same definitions as the parent lane's post-hoc anatomy: the known PnL is the sum of the
    ``conditional_fill`` executions' 25bps net dollars; a fill is one such execution. The
    top-k share is the top-k fills' dollars over the block's total known dollars (it can
    exceed 1 when losers are present, exactly as in the parent's concentration block), and
    the drop sensitivities re-divide the block total with the top-k fills removed by the
    block's panel-session count.
    """
    view = trades.filter(pl.col("arm_key") == arm.key)
    rung_keys = [int(r) for r in RUNG_COSTS]
    base = {
        "known_fills": int(cell["known_fills"]),
        "unknown_fills": int(cell["unknown_fills"]),
        "no_match_fills": int(cell["no_match_fills"]),
        "attempts": int(cell["attempts"]),
        "known_usd_25": float(cell["known_usd"]["25"]),
        "full_loss_lower_bound_usd_per_panel_session_25": cell[
            "full_loss_lower_bound_usd_per_panel_session"
        ]["25"],
        "known_usd_25_per_deployed_dollar": cell["known_usd_25_per_deployed_dollar"],
        "deployed_capital_usd_per_panel_session": cell[
            "deployed_capital_usd_per_panel_session"
        ],
        "deployed_capital_usd_per_calendar_day_span": cell[
            "deployed_capital_usd_per_calendar_day_span"
        ],
        "known_usd_per_panel_session_25": cell["known_usd_per_panel_session"]["25"],
        "bootstrap_daily_p_gt0_25bps": cell["bootstrap_daily_known_usd"]["25bps"][
            "p_gt_zero"
        ],
    }
    if not view.height:
        return {**base, "conditional_fills": 0, "top_fill_shares": {}, "drop_sensitivities": {}}
    filled = view.filter(pl.col("fill_status") == "conditional_fill")
    nets = sorted((filled["net_usd_25"].to_list() if filled.height else []), reverse=True)
    total = float(sum(nets)) if nets else 0.0
    shares = {
        f"top{k}": (float(sum(nets[:k])) / total if total != 0 else None) for k in (1, 3, 5, 10)
    }
    drops = {
        str(k): {
            "drop_usd": float(sum(nets[:k])),
            "known_usd_25_ex_drop": total - float(sum(nets[:k])),
            "known_usd_per_panel_session_25_ex_drop": (
                total - float(sum(nets[:k]))
            )
            / n_sessions,
            "known_fills_ex_drop": max(len(nets) - k, 0),
        }
        for k in (1, 5)
    }
    return {
        **base,
        "conditional_fills": int(filled.height),
        "top_fill_shares": shares,
        "top_fill_usd": (float(nets[0]) if nets else None),
        "worst_fill_usd": (float(nets[-1]) if nets else None),
        "median_fill_usd": (float(np.median(nets)) if nets else None),
        "win_rate_known_fill_25": (
            float(np.mean(np.asarray(nets) > 0)) if nets else None
        ),
        "winners_usd": float(sum(v for v in nets if v > 0)) if nets else 0.0,
        "losers_usd": float(sum(v for v in nets if v < 0)) if nets else 0.0,
        "drop_sensitivities": drops,
        "top_fill_keys": [
            {
                "day": str(row["day"]),
                "ticker": str(row["ticker"]),
                "entry_minute": int(row["entry_minute"]),
                "net_usd_25": float(row["net_usd_25"]),
                "intent_ask_shares": float(row["intent_ask_shares"]),
                "intent_bid_shares": float(row["intent_bid_shares"]),
            }
            for row in filled.sort("net_usd_25", descending=True).head(5).iter_rows(named=True)
        ],
        "rung_keys": rung_keys,
    }


def kill_rule_verdict(val_cell: dict, late_cell: dict | None) -> dict:
    """The registered kill rule: dies on a negative validation arm or a negative late 100bps.

    Exactly two conditions, nothing else: no power floor, no median gate, no bootstrap gate,
    no full-loss veto. A ``died_*`` verdict is fatal (the refinement is not promoted); a
    ``passed`` verdict promotes nothing by itself but reports the measured edge.
    """
    val_rate = val_cell["known_usd_per_panel_session"][str(int(KILL_VALIDATION_COST_BPS))]
    died_validation = not (val_rate is not None and val_rate > 0)
    late_rate = None
    died_late = None
    if late_cell is not None:
        late_rate = late_cell["known_usd_per_panel_session"][str(int(KILL_LATE_COST_BPS))]
        died_late = not (late_rate is not None and late_rate > 0)
    if died_validation:
        verdict = "DIED_VALIDATION_NEGATIVE"
    elif died_late is True:
        verdict = "DIED_LATE_NEGATIVE_100BPS"
    else:
        verdict = "PASSED_BOTH_KILL_CONDITIONS"
    return {
        "verdict": verdict,
        "died": verdict.startswith("DIED"),
        "condition_1_validation_negative": {
            "cost_bps": int(KILL_VALIDATION_COST_BPS),
            "known_usd_per_panel_session": val_rate,
            "condition_met": died_validation,
        },
        "condition_2_late_negative_at_100bps": {
            "cost_bps": int(KILL_LATE_COST_BPS),
            "known_usd_per_panel_session": late_rate,
            "condition_met": died_late,
        },
    }


def parent_funded_keys(parent_root: Path, block: str, arm_key_: str) -> set[tuple] | None:
    """The parent lane's funded-intent keys for one arm and block (read-only reference)."""
    if not parent_root.exists():
        return None
    parts = sorted((parent_root / "replay" / block).glob("????-??-??.trades.parquet"))
    if not parts:
        return None
    frames = []
    for p in parts:
        frame = pl.read_parquet(p, columns=["day", "ticker", "intent_us", "arm_key", "fill_status"])
        frames.append(frame.filter(pl.col("arm_key") == arm_key_))
    if not frames:
        return None
    frame = pl.concat(frames, how="vertical_relaxed")
    return set(frame.select(["day", "ticker", "intent_us"]).iter_rows())


def block_trade_frames(out_dir: Path, block: str) -> pl.DataFrame:
    parts = sorted((out_dir / "replay" / block).glob("????-??-??.trades.parquet"))
    if not parts:
        return empty_trades()
    return pl.concat([pl.read_parquet(p) for p in parts], how="vertical_relaxed")


def reselection_audit(
    out_dir: Path, chosen: View, block: str, parent_root: Path | None, n_sessions: int
) -> dict:
    """Does the filter merely re-select the parent's tail? Set overlaps, not adjectives.

    Compares this lane's chosen-arm funded intents (day, ticker, intent_us) with the parent
    lane's chosen arm on the same block, and splits this lane's known PnL into the fills that
    also exist in the parent arm and the fills that exist only here (admitted because the
    filter freed a slot, cash or a cooldown). A new-only share near zero means the filter is
    a re-selection of the parent's tail; a material new-only share means the entry rule
    actually changed the traded set.
    """
    own = block_trade_frames(out_dir, block).filter(pl.col("arm_key") == chosen.key)
    if not own.height:
        return {"note": "no funded intents", "block": block}
    own_keys = set(own.select(["day", "ticker", "intent_us"]).iter_rows())
    own_filled = own.filter(pl.col("fill_status") == "conditional_fill")
    own_fill_keys = set(own_filled.select(["day", "ticker", "intent_us"]).iter_rows())
    total = float(own_filled["net_usd_25"].sum()) if own_filled.height else 0.0
    out = {
        "block": block,
        "own_funded_intents": len(own_keys),
        "own_known_fills": len(own_fill_keys),
        "own_known_usd_25": total,
        "own_known_usd_per_panel_session_25": total / n_sessions if n_sessions else None,
    }
    if parent_root is None:
        out["parent_comparison"] = "parent root not available"
        return out
    parent_all = parent_funded_keys(parent_root, block, "thr300bps|p50")
    if parent_all is None:
        out["parent_comparison"] = "parent arm parts not found"
        return out
    # the parent's post-hoc thin slice is recomputed from the parent arm's stored intent facts
    parent_parts = []
    for p in sorted((parent_root / "replay" / block).glob("????-??-??.trades.parquet")):
        frame = pl.read_parquet(p)
        frame = frame.filter(pl.col("arm_key") == "thr300bps|p50").select(
            [
                "day",
                "ticker",
                "intent_us",
                "fill_status",
                "net_usd_25",
                "intent_ask_shares",
                "intent_bid_shares",
            ]
        )
        parent_parts.append(frame)
    parent_frame = (
        pl.concat(parent_parts, how="vertical_relaxed") if parent_parts else empty_trades()
    )
    parent_thin_frame = parent_frame.filter(
        pl.min_horizontal(
            pl.col("intent_ask_shares").fill_null(1e18), pl.col("intent_bid_shares").fill_null(1e18)
        )
        < DEPTH_FILTER_MAX_SHARES
    )
    parent_thin_keys = set(parent_thin_frame.select(["day", "ticker", "intent_us"]).iter_rows())
    parent_thin_filled = parent_thin_frame.filter(pl.col("fill_status") == "conditional_fill")
    shared_fill_keys = own_fill_keys & parent_fill_key_set(parent_frame)
    own_only_usd = (
        float(
            own_filled.join(
                parent_frame.filter(pl.col("fill_status") == "conditional_fill").select(
                    ["day", "ticker", "intent_us"]
                ),
                on=["day", "ticker", "intent_us"],
                how="anti",
            )["net_usd_25"].sum()
        )
        if own_filled.height
        else 0.0
    )
    out.update(
        {
            "parent_arm": "thr300bps|p50",
            "parent_funded_intents": len(parent_all),
            "shared_funded_intents": len(own_keys & parent_all),
            "new_only_funded_intents": len(own_keys - parent_all),
            "parent_funded_intents_missed_here": len(parent_all - own_keys),
            "parent_posthoc_thin_slice": {
                "funded_intents": len(parent_thin_keys),
                "known_fills": int(parent_thin_filled.height),
                "known_usd_25": float(parent_thin_filled["net_usd_25"].sum())
                if parent_thin_filled.height
                else 0.0,
                "known_usd_per_panel_session_25": float(
                    parent_thin_filled["net_usd_25"].sum()
                )
                / n_sessions
                if parent_thin_filled.height and n_sessions
                else None,
            },
            "own_fills_also_in_parent": len(shared_fill_keys),
            "own_fills_new_only": len(own_fill_keys - shared_fill_keys),
            "own_new_only_known_usd_25": own_only_usd,
            "own_new_only_share_of_known_usd": (own_only_usd / total) if total != 0 else None,
            "own_funded_intents_in_parent_thin_slice": len(own_keys & parent_thin_keys),
            "tail_reselection_answer": (
                "the filter re-selects the parent's thin tail: every kept intent already "
                "exists in the parent lane's thin<500 slice (a pure re-slice)"
                if own_keys and own_keys <= parent_thin_keys
                else "the filter is NOT a pure re-slice: some kept intents were never "
                "admitted by the parent lane (slot/cash/cooldown freed by the filter)"
            ),
        }
    )
    return out


def parent_fill_key_set(parent_frame: pl.DataFrame) -> set[tuple]:
    return set(
        parent_frame.filter(pl.col("fill_status") == "conditional_fill")
        .select(["day", "ticker", "intent_us"])
        .iter_rows()
    )


def choose_view(surface: dict) -> tuple[View, list[dict]]:
    """Deterministic argmax of the frozen validation objective; no floors, no gates.

    Only the NINE adaptive views are selectable: the three fixed-size arms are reference
    baselines and are never candidates. The registered tie-break is
    (- objective $/session, - known_fills, + attempts, arm_key).
    """
    ranked = sorted(
        VIEWS,
        key=lambda v: (
            -surface[v.key]["known_usd_per_calendar_day"]["25"],
            -surface[v.key]["known_fills"],
            surface[v.key]["attempts"],
            v.key,
        ),
    )
    ranking = [
        {
            "view_key": v.key,
            "label": v.label,
            "head": HEAD,
            "threshold": v.threshold,
            "participation": v.participation,
            "validation_known_usd_per_calendar_day_25": surface[v.key][
                "known_usd_per_calendar_day"
            ]["25"],
            "known_fills": surface[v.key]["known_fills"],
            "unknown_fills": surface[v.key]["unknown_fills"],
            "traded_days": surface[v.key]["traded_days"],
            "fixed_size_baseline_arm": BASELINE_BY_THRESHOLD[v.threshold].key,
        }
        for v in ranked
    ]
    return ranked[0], ranking


def decision_text(
    chosen: View,
    val: dict,
    late: dict | None,
    frozen: bool,
    val_comparison: dict | None = None,
    late_comparison: dict | None = None,
) -> str:
    """The decision paragraph: the honest denominator, the confirmation, the comparator.

    ``val_comparison`` / ``late_comparison`` are the FULL per-arm
    ``baseline_comparison`` maps; the chosen arm's entry is looked up here by key so no
    call site can pass the whole map where one arm's entry belongs.
    """
    objective = val["known_usd_per_calendar_day"]["25"]
    fills = val["known_fills"]
    boot = val["bootstrap_daily_known_usd"]["25bps"]
    if fills == 0 or objective is None:
        return (
            "NO_EDGE_EVIDENCE: the chosen view has no known fill, so an empty signal set is "
            "cash, not a positive edge. No actual-touch positive formulation was measured; "
            "the retained rare h60 lead stays the reference and nothing here is promoted."
        )
    if objective <= 0:
        return (
            f"NO_ACTUAL_TOUCH_POSITIVE_FORMULATION: the best of the nine views "
            f"({chosen.label}) measures {objective:+.2f} $/panel session of KNOWN "
            f"contribution on 2023 validation at 25bps over {fills} known fills "
            f"(day bootstrap p>0 = {boot.get('p_gt_zero')}). All nine views are measured "
            "and none is positive, so the retained rare h60 lead stays the reference; the "
            "unknown share is reported beside this number and is NOT assumed zero."
        )
    text = (
        f"{STATUS}: chosen {chosen.label} (h{HEAD} at threshold {chosen.threshold:.4f}, "
        f"participation {chosen.participation:.2f}) by 2023 validation actual-touch known "
        f"contribution: {objective:+.2f} $/panel session at 25bps "
        f"(= {val['known_usd_per_calendar_day_span']['25']:+.2f} $/calendar day over the "
        f"{val['calendar_days_spanned']}-calendar-day span; {val['days_replayed']} panel "
        f"sessions) = "
        f"{val['dollars_per_year_252_on_book_3000']['25']:+.0f} $/year on the "
        f"${int(BOOK)} nominal book (simple 252-session daily-reset convention, not a CAGR), "
        f"{fills} known fills on {val['traded_days']} traded days of {val['days_replayed']} "
        f"panel sessions, {val['unknown_fills']} UNKNOWN executions "
        f"({100.0 * val['unknown_fills'] / max(1, val['attempts']):.1f}% of attempts) and "
        f"{val['no_match_fills']} no-match unfilled intents. Day bootstrap p>0 = "
        f"{boot.get('p_gt_zero')}. This is the measured KNOWN-contribution expectation on a "
        "PARTIAL basis (unknowns excluded from the numerator), not an actual account CAGR."
    )
    if frozen and late is not None:
        late_obj = late["known_usd_per_calendar_day"]["25"]
        text += (
            f" The frozen late block measures {late_obj:+.2f} $/panel session at 25bps "
            f"(= {late['known_usd_per_calendar_day_span']['25']:+.2f} $/calendar day over "
            f"the {late['calendar_days_spanned']}-calendar-day span) over "
            f"{late['known_fills']} known fills of {late['attempts']} attempts on the "
            "previously explored 2025-02..2026-05 window; the choice was frozen before any "
            "late file was read, so this is confirmation of an already-frozen decision, not "
            "a re-selection."
        )
    elif not frozen:
        text += " Late block not run (--skip-late)."
    if val_comparison is not None:
        val_cmp = val_comparison[chosen.key]
        late_cmp = late_comparison[chosen.key] if late_comparison is not None else None
        text += _comparator_caveat_sentence(val_cmp, late_cmp)
    return text


def _signed_or_na(value: float | None) -> str:
    """A signed four-decimal rendering, or ``n/a`` when the ratio has no denominator."""
    return f"{value:+.4f}" if value is not None else "n/a"


def _comparator_caveat_sentence(val_comparison: dict, late_comparison: dict | None) -> str:
    """The fixed-size comparator caveat for the decision text (never a like-for-like claim).

    The fixed-size reference arm skips an intent whose capital-cap ticket exceeds the
    displayed ask depth while the adaptive arm caps down and attempts it, so the two arms
    trade DIFFERENT intent sets and the absolute $/day delta mixes intent-set composition
    with sizing: it is not an 'adaptive beats fixed' result, only the per-block sign and
    the known $ per deployed dollar are defensible reads.
    """
    v_int = val_comparison["intent_set"]
    v_skips = v_int["fixed_size_baseline_intent_depth_unsupported_skips"]
    parts = [
        " Comparator caveat - NOT a like-for-like 'adaptive beats fixed' claim: the "
        f"fixed-size reference arm skips {v_skips} "
        "intents outright (intent_depth_unsupported: its capital-cap ticket exceeds the "
        "displayed ask depth) while the adaptive arm caps down and attempts them, so the "
        f"arms trade different intent sets ({v_int['adaptive_attempts']} adaptive vs "
        f"{v_int['fixed_size_baseline_attempts']} fixed-size attempts on validation"
    ]
    if late_comparison is not None:
        l_int = late_comparison["intent_set"]
        l_density = late_comparison["known_usd_25_per_deployed_dollar"]
        parts.append(
            f", {l_int['adaptive_attempts']} vs {l_int['fixed_size_baseline_attempts']} on "
            "the late block"
        )
    else:
        l_density = None
    v_density = val_comparison["known_usd_25_per_deployed_dollar"]
    parts.append(
        "); the absolute $/day delta therefore mixes intent-set composition with sizing, "
        "and the defensible reads are the block-by-block sign and the known $ per deployed "
        f"dollar (validation {_signed_or_na(v_density['adaptive'])} vs "
        f"{_signed_or_na(v_density['fixed_size_baseline'])}"
    )
    if l_density is not None:
        parts.append(
            f", late {_signed_or_na(l_density['adaptive'])} vs "
            f"{_signed_or_na(l_density['fixed_size_baseline'])}"
        )
    parts.append(").")
    return "".join(parts)


def provenance_block(
    out_dir: Path,
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
        "study": "alpha_shallow_depth_lane",
        "parent_lane": {
            "study": "alpha_adaptive_depth_sizing",
            "output_root": str(PARENT_ROOT),
            "relationship": (
                "this lane's clock/fill/fee/unknown conventions, arms, book and selection "
                "objective are inherited verbatim; the parent's outputs are READ-ONLY here "
                "(the tail re-selection audit alone reads its replay parts) and are never "
                "mutated"
            ),
        },
        "depth_filter": CONTRACT["depth_filter"],
        "kill_rule": CONTRACT["kill_rule"],
        "contract_sha256": _digest_bytes(CONTRACT),
        "panel_root": str(PANEL_ROOT),
        "panel_days_root": str(PANEL_DAYS),
        "panel_contract_sha256": (sha256_file(PANEL_CONTRACT) if PANEL_CONTRACT.exists() else None),
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
        "model_dir": str(MODEL_DIR),
        "models": {
            f"h{HEAD}": {
                "path": str(MODEL_DIR / f"payoff_h{HEAD}.joblib"),
                "sha256": sha256_file(MODEL_DIR / f"payoff_h{HEAD}.joblib"),
            }
        },
        "imported_quote_aware_helpers": [
            "alpha_quote_aware_frequency.quote_day_frame",
            "alpha_quote_aware_frequency.symbol_quotes",
            "alpha_quote_aware_frequency.SymbolQuotes",
            "alpha_quote_aware_frequency.minute_us",
            "alpha_quote_aware_frequency.arrival_us",
            "alpha_quote_aware_frequency.limit_price_of",
            "alpha_quote_aware_frequency.submission_clock",
            "alpha_quote_aware_frequency.priced_leg (MARKET-style exit leg ONLY)",
            "alpha_quote_aware_frequency.MAX_AGE_S",
        ],
        "own_entry_pricing": (
            "alpha_shallow_depth_lane.price_entry_ioc (inherited verbatim from "
            "alpha_adaptive_depth_sizing.price_entry_ioc): the entry leg reads the latest "
            "raw print ONCE at the arrival clock and never rests, so a BUY IOC does not "
            "inherit the market-style rest branch of the shared priced_leg helper"
        ),
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

    # 0) the registration is verified BEFORE anything is scored, replayed or aggregated.
    prereg = verify_preregistration()
    print(
        f"[prereg] verified {prereg['path']} sha256 {prereg['sha256'][:12]} "
        f"({prereg['canonical_bytes']} canonical bytes) before any outcome",
        flush=True,
    )

    models, model_report = load_stored_model()
    print(
        f"[model] immutable head loaded (no refit): h{HEAD} sha256 "
        f"{model_report[f'h{HEAD}']['sha256'][:12]} "
        f"({model_report[f'h{HEAD}']['n_features']} features)",
        flush=True,
    )

    supplemental = list(SUPPLEMENT_ROOTS)
    for extra in args.supplement or []:
        if extra not in supplemental:
            supplemental.append(extra)
    supplemental_roots = tuple(supplemental)
    existing = [r for r in supplemental_roots if r.exists()]
    print(
        "[supplements] " + (", ".join(str(r) for r in existing) if existing else "none present"),
        flush=True,
    )

    days_val = block_days(args.days, VAL_BLOCK)
    if args.days is None and len(days_val) != EXPECTED_DAYS[VAL_BLOCK]:
        raise SystemExit(
            f"[shallow-depth] requires {EXPECTED_DAYS[VAL_BLOCK]} {VAL_BLOCK} days, "
            f"got {len(days_val)}"
        )
    if args.stage != "shard" and not days_val:
        raise SystemExit("[shallow-depth] no validation days selected")

    (out / "contract.json").write_text(json.dumps(CONTRACT, indent=1, default=_default) + "\n")
    print(
        f"[freeze-grid] contract -> {out / 'contract.json'} "
        f"({len(VIEWS)} selectable views + {len(BASELINE_VIEWS)} fixed-size reference arms "
        f"x {len(RUNG_COSTS)} rungs, before any outcome)",
        flush=True,
    )

    # 1) validation only: no late panel or quote file is touched before the freeze.
    print(
        f"[blocks] validation={len(days_val)} days (late block deferred until after the freeze)",
        flush=True,
    )
    if args.stage == "shard":
        # A parallel day-shard worker: parts only, never an aggregate, never a freeze. A
        # validation shard must complete (and the full --skip-late run must have written
        # selection_freeze.json) BEFORE any late shard is started.
        score_block(out, models, days_val, args.resume, supplemental_roots)
        replay_block(out, VAL_BLOCK, days_val, args.resume, supplemental_roots)
        days_late_shard = block_days(args.days, LATE_BLOCK)
        if days_late_shard:
            freeze_path = out / "selection_freeze.json"
            if not freeze_path.exists():
                raise SystemExit(
                    "[shallow-depth] refusing to score/replay late days before "
                    f"{freeze_path} exists: run the full validation pass first"
                )
            score_block(out, models, days_late_shard, args.resume, supplemental_roots)
            replay_block(out, LATE_BLOCK, days_late_shard, args.resume, supplemental_roots)
        print(
            f"[shard] score+replay parts written ({len(days_val)} validation + "
            f"{len(block_days(args.days, LATE_BLOCK))} late day(s)); no aggregate/freeze/"
            "results produced",
            flush=True,
        )
        return
    val_score_cov = score_block(out, models, days_val, args.resume, supplemental_roots)
    val_replay_cov = replay_block(out, VAL_BLOCK, days_val, args.resume, supplemental_roots)
    val_surface, val_cov = aggregate_block(out, VAL_BLOCK, days_val)
    for arm in ARMS:
        cell = val_surface[arm.key]
        boot = cell["bootstrap_daily_known_usd"]["25bps"]
        tag = "fixed-size baseline" if arm.is_baseline else "adaptive view"
        print(
            f"[val@25] {arm.label:<28} {cell['known_usd_per_calendar_day']['25']:+8.2f} $/session "
            f"({tag}; fills={cell['known_fills']} unknown={cell['unknown_fills']} "
            f"nomatch={cell['no_match_fills']} attempts={cell['attempts']} "
            f"signals={cell['signals']} traded_days={cell['traded_days']} "
            f"skipped_depth_thick={cell['skip_reasons'][DEPTH_FILTER_SKIP]} "
            f"p>0={boot.get('p_gt_zero')})",
            flush=True,
        )

    chosen, ranking = choose_view(val_surface)
    val_comparison = baseline_comparison(val_surface)
    frozen_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    freeze = {
        "frozen_before_late_file_access": True,
        "frozen_at": frozen_at,
        "study": "alpha_shallow_depth_lane",
        "status": STATUS,
        "preregistration": prereg,
        "chosen": {
            "arm_key": chosen.key,
            "label": chosen.label,
            "head": HEAD,
            "threshold": chosen.threshold,
            "participation": chosen.participation,
        },
        "selection": {
            "objective": (
                "2023 validation actual-touch known contribution dollars per panel "
                "session at the 25bps residual rung"
            ),
            "selection_cost_bps": SELECT_COST,
            "cost_ladder_bps": list(RUNG_COSTS),
            "basis": (
                "PARTIAL: unknown executions are excluded from the numerator and reported "
                "beside it; the full-loss lower bound is a coded convention, never a veto "
                "and never the objective"
            ),
            "synthetic_unknown_loss_veto": False,
            "power_floors": None,
            "median_or_tail_gates": None,
            "cost_or_count_kill": None,
            "kill_rule_registered": CONTRACT["kill_rule"],
            "selectable_views": len(VIEWS),
            "fixed_size_reference_arms_excluded_from_selection": len(BASELINE_VIEWS),
            "tie_break": "(- objective $/session, - known_fills, + attempts, arm_key)",
            "ranking": ranking,
        },
        "arms": [
            {
                "arm_key": v.key,
                "label": v.label,
                "threshold": v.threshold,
                "participation": v.participation,
                "is_fixed_size_baseline": bool(v.is_baseline),
            }
            for v in ARMS
        ],
        "validation_surface": val_surface,
        "validation_fixed_size_baseline_comparison": val_comparison,
        "model": model_report,
        "provenance": provenance_block(out, days_val, None, supplemental_roots, val_cov, None),
    }
    freeze_path = out / "selection_freeze.json"
    if freeze_path.exists():
        # An earlier pass already froze the choice: the recomputed deterministic argmax
        # must agree exactly, otherwise the freeze has been violated.
        prior = json.loads(freeze_path.read_text())
        if prior.get("chosen") != freeze["chosen"]:
            raise SystemExit(
                f"[shallow-depth] FROZEN CHOICE CHANGED: {prior.get('chosen')} vs "
                f"{freeze['chosen']}; the selection is frozen before any late inspection"
            )
        freeze["frozen_at"] = prior.get("frozen_at", frozen_at)
        print(
            f"[freeze] existing selection_freeze.json kept (chosen {freeze['chosen']['label']} "
            f"frozen at {freeze['frozen_at']}, recomputed choice identical)",
            flush=True,
        )
    else:
        write_json_atomic(freeze_path, freeze)
        print(
            f"[freeze] selection_freeze.json -> chosen {chosen.label} "
            f"({val_surface[chosen.key]['known_usd_per_calendar_day']['25']:+.2f} $/session @25) "
            "AFTER validation, BEFORE any late file is read",
            flush=True,
        )

    late_surface = None
    late_cov = None
    late_comparison = None
    days_late = None
    late_score_cov = None
    late_replay_cov = None
    if not args.skip_late:
        # 2) late block: the same twelve arms for transparency, the choice already frozen.
        #    This is the FIRST access to any late panel or quote file.
        days_late = block_days(args.days, LATE_BLOCK)
        if args.days is None and len(days_late) != EXPECTED_DAYS[LATE_BLOCK]:
            raise SystemExit(
                f"[shallow-depth] requires {EXPECTED_DAYS[LATE_BLOCK]} {LATE_BLOCK} days, "
                f"got {len(days_late)}"
            )
        if not days_late:
            # A restricted smoke calendar can name validation days only; the frozen
            # validation choice then stands alone and no late file is read at all.
            print(
                "[blocks] no late day selected: validation-only results, no late file read",
                flush=True,
            )
        else:
            print(f"[blocks] late={len(days_late)} days (after the freeze)", flush=True)
            late_score_cov = score_block(out, models, days_late, args.resume, supplemental_roots)
            late_replay_cov = replay_block(
                out, LATE_BLOCK, days_late, args.resume, supplemental_roots
            )
            late_surface, late_cov = aggregate_block(out, LATE_BLOCK, days_late)
            late_comparison = baseline_comparison(late_surface)
            for arm in ARMS:
                cell = late_surface[arm.key]
                boot = cell["bootstrap_daily_known_usd"]["25bps"]
                tag = "fixed-size baseline" if arm.is_baseline else "adaptive view"
                print(
                    f"[late@25] {arm.label:<28} "
                    f"{cell['known_usd_per_calendar_day']['25']:+8.2f} $/session "
                    f"({tag}; fills={cell['known_fills']} unknown={cell['unknown_fills']} "
                    f"nomatch={cell['no_match_fills']} attempts={cell['attempts']} "
                    f"signals={cell['signals']} traded_days={cell['traded_days']} "
                    f"skipped_depth_thick={cell['skip_reasons'][DEPTH_FILTER_SKIP]} "
                    f"p>0={boot.get('p_gt_zero')})",
                    flush=True,
                )

    # 3) the registered diagnoses and the kill rule, on the chosen arm only.
    _, val_trades = read_block_frames(out, VAL_BLOCK)
    val_diagnostics = fill_diagnostics(val_surface[chosen.key], val_trades, chosen, len(days_val))
    late_trades = None
    late_diagnostics = None
    if late_surface is not None:
        _, late_trades = read_block_frames(out, LATE_BLOCK)
        late_diagnostics = fill_diagnostics(
            late_surface[chosen.key], late_trades, chosen, len(days_late)
        )
    kill = kill_rule_verdict(
        val_surface[chosen.key], late_surface[chosen.key] if late_surface is not None else None
    )
    diagnostics = {
        "arm": chosen.key,
        "label": chosen.label,
        "validation": val_diagnostics,
        "late": late_diagnostics,
    }
    tail_audit = {
        "validation": reselection_audit(out, chosen, VAL_BLOCK, PARENT_ROOT, len(days_val)),
        "late": (
            reselection_audit(out, chosen, LATE_BLOCK, PARENT_ROOT, len(days_late))
            if late_surface is not None
            else "not computed: the late block has not been run yet"
        ),
    }
    print(
        f"[diagnostics] {chosen.label} validation known/unknown/no-match "
        f"{val_diagnostics['known_fills']}/{val_diagnostics['unknown_fills']}/"
        f"{val_diagnostics['no_match_fills']}, full-loss bound "
        f"{val_diagnostics['full_loss_lower_bound_usd_per_panel_session_25']:+.2f} $/session, "
        f"top5 share {val_diagnostics['top_fill_shares'].get('top5')}, drop-best-1 "
        f"{val_diagnostics['drop_sensitivities']['1']['known_usd_per_panel_session_25_ex_drop']:+.2f} "
        f"$/session, $/deployed "
        f"{val_diagnostics['known_usd_25_per_deployed_dollar']}",
        flush=True,
    )
    if late_diagnostics is not None:
        print(
            f"[diagnostics] {chosen.label} late known/unknown/no-match "
            f"{late_diagnostics['known_fills']}/{late_diagnostics['unknown_fills']}/"
            f"{late_diagnostics['no_match_fills']}, full-loss bound "
            f"{late_diagnostics['full_loss_lower_bound_usd_per_panel_session_25']:+.2f} $/session, "
            f"top5 share {late_diagnostics['top_fill_shares'].get('top5')}, drop-best-1 "
            f"{late_diagnostics['drop_sensitivities']['1']['known_usd_per_panel_session_25_ex_drop']:+.2f} "
            f"$/session, $/deployed "
            f"{late_diagnostics['known_usd_25_per_deployed_dollar']}",
            flush=True,
        )
    print(f"[kill-rule] {kill['verdict']}", flush=True)

    results = {
        "study": "alpha_shallow_depth_lane",
        "status": STATUS,
        "decision": decision_text(
            chosen,
            val_surface[chosen.key],
            late_surface[chosen.key] if late_surface else None,
            frozen=late_surface is not None,
            val_comparison=val_comparison,
            late_comparison=late_comparison,
        ),
        "chosen": freeze["chosen"],
        "frozen_choice_immutable": True,
        "preregistration": prereg,
        "depth_filter": CONTRACT["depth_filter"],
        "kill_rule": kill,
        "diagnostics": diagnostics,
        "tail_reselection_audit": tail_audit,
        "contract": CONTRACT,
        "model": model_report,
        "validation": {v.key: val_surface[v.key] for v in VIEWS},
        "validation_fixed_size_baseline": {v.key: val_surface[v.key] for v in BASELINE_VIEWS},
        "validation_baseline_comparison": val_comparison,
        "validation_ranking": ranking,
        "late": ({v.key: late_surface[v.key] for v in VIEWS} if late_surface else None),
        "late_fixed_size_baseline": (
            {v.key: late_surface[v.key] for v in BASELINE_VIEWS} if late_surface else None
        ),
        "late_baseline_comparison": late_comparison,
        "late_ranking": (
            [
                {
                    "arm_key": v.key,
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
            ]
            if late_surface
            else None
        ),
        "chosen_late_block": (late_surface[chosen.key] if late_surface else None),
        "cost_ladder_whole_calendar_known_contributions": {
            arm.key: {
                str(int(r)): {
                    "validation_usd": val_surface[arm.key]["known_usd"][str(int(r))],
                    "late_usd": (
                        late_surface[arm.key]["known_usd"][str(int(r))] if late_surface else None
                    ),
                    "validation_usd_per_calendar_day": val_surface[arm.key][
                        "known_usd_per_calendar_day"
                    ][str(int(r))],
                    "late_usd_per_calendar_day": (
                        late_surface[arm.key]["known_usd_per_calendar_day"][str(int(r))]
                        if late_surface
                        else None
                    ),
                    "validation_full_loss_lower_bound_usd_per_day": val_surface[arm.key][
                        "full_loss_lower_bound_usd_per_calendar_day"
                    ][str(int(r))],
                    "late_full_loss_lower_bound_usd_per_day": (
                        late_surface[arm.key]["full_loss_lower_bound_usd_per_calendar_day"][
                            str(int(r))
                        ]
                        if late_surface
                        else None
                    ),
                }
                for r in RUNG_COSTS
            }
            for arm in ARMS
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
    results["decision"] += (
        f" KILL RULE: {kill['verdict']} (validation "
        f"{kill['condition_1_validation_negative']['known_usd_per_panel_session']:+.2f} "
        f"$/session @25bps; late "
        f"{kill['condition_2_late_negative_at_100bps']['known_usd_per_panel_session']} "
        f"$/session @100bps)."
        if kill["condition_2_late_negative_at_100bps"]["known_usd_per_panel_session"] is not None
        else f" KILL RULE: {kill['verdict']} (validation "
        f"{kill['condition_1_validation_negative']['known_usd_per_panel_session']:+.2f} "
        "$/session @25bps; late block not run)."
    )
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
        "--stage",
        choices=("aggregate", "shard"),
        default="aggregate",
        help=(
            "aggregate (default): score, replay, aggregate, freeze and write results; "
            "shard: score+replay ONLY the given --days (no aggregate/freeze/results), for "
            "parallel day workers feeding a later aggregate pass; a late shard refuses to "
            "start unless selection_freeze.json already exists"
        ),
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
