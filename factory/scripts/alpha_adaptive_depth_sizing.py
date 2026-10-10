#!/usr/bin/env python3
"""ADAPTIVE depth-participation sizing on the immutable h60 top-gainer model (no refit).

This lane keeps the frozen learned h60 minute-open head EXACTLY as stored
(``learned/models/payoff_h60.joblib``, fit block 2021-02..2022-12, immutable) and changes
one thing only: the SIZE of each ticket is now derived from the OBSERVED top-of-book
liquidity at the intent clock, so the entry no longer asks the displayed book for a
notional it may not carry and the exit is sized against the bid depth that is visible
BEFORE the position exists.

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
  fixed-size baseline and never against a linear rescale of itself.
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
chosen ONLY by 2023 validation actual-touch known contribution in dollars per full
calendar day at the 25bps residual rung, on an explicit PARTIAL basis (unknown executions
are excluded from the numerator and reported beside it). No synthetic full-loss UNKNOWN
veto, no minimum known-fill count, no median or CI gate, no cost/count kill. The late
block then runs every arm for transparency while the choice stays immutable.

Reported per view and per 0/5/10/25/50/75/100/125/150bps round-trip residual rung over the
whole calendar: known contributions, fills / known fills / traded days / attempts, the
supported share and the UNKNOWN and no-match counts, causal skip reasons, deployed capital
and its density (known $ per deployed dollar), the simulated fixed-size baseline
comparison, monthly and yearly accounting, a day-level bootstrap, the worst known fill and
the simple $/(year on a $3,000 book) at 252 sessions. The headline is the measured
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
  uv run --no-sync python factory/scripts/alpha_adaptive_depth_sizing.py
      # full 250 validation + 332 late days -> ~/alpha-data/open-search-v1/adaptive_depth_sizing
  uv run --no-sync python factory/scripts/alpha_adaptive_depth_sizing.py --resume
      # resume from the per-day state / daily / trade parts already on disk
  uv run --no-sync python factory/scripts/alpha_adaptive_depth_sizing.py --days 2023-05-15
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
from collections import Counter
from dataclasses import dataclass
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
OUTPUT = PANEL_ROOT / "adaptive_depth_sizing"
DATA_ROOT = Path("/home/hillel/projects/Alpacatrader") / "data"
PANEL_DAYS = PANEL_ROOT / "days"
PANEL_CONTRACT = PANEL_ROOT / "contract.json"
STATUS = "DISCOVERY-NOT-VALIDATED"
VERSION = 1
STATES_SCHEMA = 1
REPLAY_SCHEMA = 1
PROTECTED_UNREAD = ["2024", "2025-01", "2026-06", "2026-07", "2026-08"]
# Existing fetch caches merged as supplements (original prints always win; a supplement may
# only ADD timestamps, never silently replace or revise a print). The acquisition root is
# the neutral ALL-PIT missing-symbol harvest (alpha_quote_universe_acquire): it is DISCOVERED
# when present and contributes nothing while the harvest is still pending. A ticker whose
# historical stream is still absent after every root is an ACQUISITION UNKNOWN - never known
# cash and never a future-availability entry filter.
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
    "hypothesis": "the immutable learned h60 payoff head, traded at three fixed score bars "
    "with a fresh-quote-gated marketable-IOC-limit entry whose integer quantity is "
    "causally capped by BOTH displayed top-of-book sides at the intent clock (capital cap "
    "and depth-participation cap), reduces the future exit L1 shortfall of the displayed "
    "book and keeps the known actual-touch contribution positive at low residual rungs, "
    "against a SIMULATED fixed-size baseline under identical book rules",
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
        "objective": "2023 validation actual-touch known contribution dollars per full "
        "calendar day at the 25bps residual rung",
        "selection_cost_bps": SELECT_COST,
        "cost_ladder_bps": list(RUNG_COSTS),
        "basis": "PARTIAL: unknown executions are excluded from the numerator and reported "
        "beside it; the full-loss lower bound is a coded convention, never a veto and never "
        "the objective",
        "synthetic_unknown_loss_veto": False,
        "power_floors": None,
        "median_or_tail_gates": None,
        "cost_or_count_kill": None,
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
        raise SystemExit(f"[adaptive-depth] stored model missing: {path}")
    bundle = joblib.load(path)
    order = list(bundle.get("feature_order") or [])
    if order != list(FEATURES_ALL):
        raise SystemExit(
            f"[adaptive-depth] h{HEAD} stored feature_order != current FEATURES_ALL "
            f"({len(order)} vs {len(FEATURES_ALL)})"
        )
    if int(bundle.get("horizon", -1)) != HEAD:
        raise SystemExit(f"[adaptive-depth] h{HEAD} stored horizon {bundle.get('horizon')}")
    booster = bundle["lgbm"]
    # The stored head was fitted from a numpy matrix, so the booster itself carries generic
    # column names: the ORDER contract is bundle["feature_order"] (verified against
    # FEATURES_ALL) plus the shared feature_matrix helper, and the booster is checked to be
    # pure-matrix-named (never a different symbolic order).
    names = list(getattr(booster, "feature_name_", []) or [])
    generic = [f"Column_{i}" for i in range(len(order))]
    if names and names != order and names != generic:
        raise SystemExit(
            f"[adaptive-depth] h{HEAD} booster feature names match neither the stored "
            "feature_order nor the generic matrix names"
        )
    n_feat = int(getattr(booster, "n_features_in_", 0) or len(order))
    if n_feat != len(order):
        raise SystemExit(f"[adaptive-depth] h{HEAD} booster feature count {n_feat} != {len(order)}")
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
    seq = 0
    trades: list[dict] = []
    positions_samples: list[int] = []
    for minute in sorted(groups):
        intent_us = minute_us(day, minute)
        while queue and queue[0][0] <= intent_us:
            _, _, sym, proceeds = heapq.heappop(queue)
            active.discard(sym)
            cash += proceeds
        admitted: list[tuple[dict, int]] = []
        for r in groups[minute]:
            sym = r["ticker"]
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
            positions_samples.append(len(active))
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
    daily["peak_reserved_usd"] = float(daily["deployed_reserved_usd"])
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
        _, spath = state_paths(out_dir, day)
        states_sha = None
        if spath.exists():
            try:
                states_sha = json.loads(spath.read_text()).get("states_sha256")
            except (json.JSONDecodeError, OSError):
                states_sha = None
            if states_sha is None:
                states_sha = sha256_file(spath)
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
                f"[adaptive-depth] scored states missing for {day} ({states_path}); run the "
                "score stage for the whole block before replaying it"
            )
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
                "states_sha256": states_sha,
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


def view_cell(daily: pl.DataFrame, trades: pl.DataFrame, arm: View, days: list[str]) -> dict:
    """Every reported number for one arm on one block's calendar."""
    rows = daily.filter(pl.col("arm_key") == arm.key)
    n_days = len(days)
    if int(rows.height) != n_days:
        raise ValueError(
            f"arm {arm.key}: {rows.height} daily rows on disk for {n_days} calendar days; "
            "replay the whole block before aggregating"
        )
    rung_keys = [int(r) for r in RUNG_COSTS]
    known_total = {r: float(rows[f"known_usd_{r}"].sum()) for r in rung_keys}
    known_per_day = {r: known_total[r] / n_days for r in rung_keys}
    bound_per_day = {r: float(rows[f"lower_bound_usd_{r}"].sum()) / n_days for r in rung_keys}
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
        "days_replayed": n_days,
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
        "known_usd_per_calendar_day": {str(r): known_per_day[r] for r in rung_keys},
        "full_loss_lower_bound_usd_per_calendar_day": {str(r): bound_per_day[r] for r in rung_keys},
        "lower_bound_is_coding_convention_not_ev": (
            "the lower bound charges every UNKNOWN execution a full $1,000 ticket loss; it "
            "is a coded convention driven by how many legs stayed unpriced, NOT observed "
            "PnL and NOT an expected loss of that size"
        ),
        "supported_share_of_funded_intents": (known_fills / funded) if funded else None,
        "unknown_share_of_funded_intents": (
            float(rows["unknown_fills"].sum() / funded) if funded else None
        ),
        "deployed_capital_usd_total": deployed_total,
        "deployed_capital_usd_per_calendar_day": deployed_total / n_days,
        "known_usd_25_per_deployed_dollar": (
            known_total[25] / deployed_total if deployed_total > 0 else None
        ),
        "dollars_per_year_252_on_book_3000": {
            str(r): known_per_day[r] * ANNUAL_SESSIONS for r in rung_keys
        },
        "annualization": (
            f"mean daily known contribution x {ANNUAL_SESSIONS} sessions on a "
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
    """
    out = {}
    for arm in VIEWS:
        base = BASELINE_BY_THRESHOLD[arm.threshold]
        adaptive, fixed = surface[arm.key], surface[base.key]
        a_known = adaptive["known_usd"]["25"]
        f_known = fixed["known_usd"]["25"]
        a_deployed = adaptive["deployed_capital_usd_total"]
        f_deployed = fixed["deployed_capital_usd_total"]
        out[arm.key] = {
            "adaptive_view": arm.label,
            "fixed_size_baseline_arm": base.label,
            "known_net_usd_absolute_25": {
                "adaptive": a_known,
                "fixed_size_baseline": f_known,
                "absolute_delta_usd": a_known - f_known,
            },
            "known_usd_per_calendar_day_25": {
                "adaptive": adaptive["known_usd_per_calendar_day"]["25"],
                "fixed_size_baseline": fixed["known_usd_per_calendar_day"]["25"],
            },
            "deployed_capital_usd_per_calendar_day": {
                "adaptive": adaptive["deployed_capital_usd_per_calendar_day"],
                "fixed_size_baseline": fixed["deployed_capital_usd_per_calendar_day"],
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
        }
    return out


def aggregate_block(out_dir: Path, block: str, days: list[str]) -> tuple[dict, dict]:
    """The full twelve-arm surface of one block, plus the block's data coverage."""
    daily, trades = read_block_frames(out_dir, block)
    surface = {arm.key: view_cell(daily, trades, arm, days) for arm in ARMS}
    coverage = {
        "block": block,
        "days": len(days),
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
    """Deterministic argmax of the frozen validation objective; no floors, no gates.

    Only the NINE adaptive views are selectable: the three fixed-size arms are reference
    baselines and are never candidates.
    """
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


def decision_text(chosen: View, val: dict, late: dict | None, frozen: bool) -> str:
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
            f"({chosen.label}) measures {objective:+.2f} $/calendar day of KNOWN "
            f"contribution on 2023 validation at 25bps over {fills} known fills "
            f"(day bootstrap p>0 = {boot.get('p_gt_zero')}). All nine views are measured "
            "and none is positive, so the retained rare h60 lead stays the reference; the "
            "unknown share is reported beside this number and is NOT assumed zero."
        )
    text = (
        f"{STATUS}: chosen {chosen.label} (h{HEAD} at threshold {chosen.threshold:.4f}, "
        f"participation {chosen.participation:.2f}) by 2023 validation actual-touch known "
        f"contribution: {objective:+.2f} $/calendar day at 25bps = "
        f"{val['dollars_per_year_252_on_book_3000']['25']:+.0f} $/year on the "
        f"${int(BOOK)} nominal book (simple 252-session daily-reset convention, not a CAGR), "
        f"{fills} known fills on {val['traded_days']} traded days of {val['days_replayed']} "
        f"calendared days, {val['unknown_fills']} UNKNOWN executions "
        f"({100.0 * val['unknown_fills'] / max(1, val['attempts']):.1f}% of attempts) and "
        f"{val['no_match_fills']} no-match unfilled intents. Day bootstrap p>0 = "
        f"{boot.get('p_gt_zero')}. This is the measured KNOWN-contribution expectation on a "
        "PARTIAL basis (unknowns excluded from the numerator), not an actual account CAGR."
    )
    if frozen and late is not None:
        late_obj = late["known_usd_per_calendar_day"]["25"]
        text += (
            f" The frozen late block measures {late_obj:+.2f} $/calendar day at 25bps over "
            f"{late['known_fills']} known fills of {late['attempts']} attempts on the "
            "previously explored 2025-02..2026-05 window; the choice was frozen before any "
            "late file was read, so this is confirmation of an already-frozen decision, not "
            "a re-selection."
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
            "alpha_adaptive_depth_sizing.price_entry_ioc: the entry leg reads the latest raw "
            "print ONCE at the arrival clock and never rests, so a BUY IOC does not inherit "
            "the market-style rest branch of the shared priced_leg helper"
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
            f"[adaptive-depth] requires {EXPECTED_DAYS[VAL_BLOCK]} {VAL_BLOCK} days, "
            f"got {len(days_val)}"
        )
    if not days_val:
        raise SystemExit("[adaptive-depth] no validation days selected")

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
    val_score_cov = score_block(out, models, days_val, args.resume, supplemental_roots)
    val_replay_cov = replay_block(out, VAL_BLOCK, days_val, args.resume, supplemental_roots)
    val_surface, val_cov = aggregate_block(out, VAL_BLOCK, days_val)
    for arm in ARMS:
        cell = val_surface[arm.key]
        boot = cell["bootstrap_daily_known_usd"]["25bps"]
        tag = "fixed-size baseline" if arm.is_baseline else "adaptive view"
        print(
            f"[val@25] {arm.label:<28} {cell['known_usd_per_calendar_day']['25']:+8.2f} $/day "
            f"({tag}; fills={cell['known_fills']} unknown={cell['unknown_fills']} "
            f"nomatch={cell['no_match_fills']} attempts={cell['attempts']} "
            f"signals={cell['signals']} traded_days={cell['traded_days']} "
            f"p>0={boot.get('p_gt_zero')})",
            flush=True,
        )

    chosen, ranking = choose_view(val_surface)
    frozen_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    freeze = {
        "frozen_before_late_file_access": True,
        "frozen_at": frozen_at,
        "study": "alpha_adaptive_depth_sizing",
        "status": STATUS,
        "chosen": {
            "arm_key": chosen.key,
            "label": chosen.label,
            "head": HEAD,
            "threshold": chosen.threshold,
            "participation": chosen.participation,
        },
        "selection": {
            "objective": (
                "2023 validation actual-touch known contribution dollars per full "
                "calendar day at the 25bps residual rung"
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
            "selectable_views": len(VIEWS),
            "fixed_size_reference_arms_excluded_from_selection": len(BASELINE_VIEWS),
            "tie_break": "(- objective, - known_fills, arm_key)",
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
        "validation_fixed_size_baseline_comparison": baseline_comparison(val_surface),
        "model": model_report,
        "provenance": provenance_block(out, days_val, None, supplemental_roots, val_cov, None),
    }
    write_json_atomic(out / "selection_freeze.json", freeze)
    print(
        f"[freeze] selection_freeze.json -> chosen {chosen.label} "
        f"({val_surface[chosen.key]['known_usd_per_calendar_day']['25']:+.2f} $/day @25) "
        "AFTER validation, BEFORE any late file is read",
        flush=True,
    )

    late_surface = None
    late_cov = None
    days_late = None
    late_score_cov = None
    late_replay_cov = None
    if not args.skip_late:
        # 2) late block: the same twelve arms for transparency, the choice already frozen.
        #    This is the FIRST access to any late panel or quote file.
        days_late = block_days(args.days, LATE_BLOCK)
        if args.days is None and len(days_late) != EXPECTED_DAYS[LATE_BLOCK]:
            raise SystemExit(
                f"[adaptive-depth] requires {EXPECTED_DAYS[LATE_BLOCK]} {LATE_BLOCK} days, "
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
            for arm in ARMS:
                cell = late_surface[arm.key]
                boot = cell["bootstrap_daily_known_usd"]["25bps"]
                tag = "fixed-size baseline" if arm.is_baseline else "adaptive view"
                print(
                    f"[late@25] {arm.label:<28} "
                    f"{cell['known_usd_per_calendar_day']['25']:+8.2f} $/day "
                    f"({tag}; fills={cell['known_fills']} unknown={cell['unknown_fills']} "
                    f"nomatch={cell['no_match_fills']} attempts={cell['attempts']} "
                    f"signals={cell['signals']} traded_days={cell['traded_days']} "
                    f"p>0={boot.get('p_gt_zero')})",
                    flush=True,
                )

    results = {
        "study": "alpha_adaptive_depth_sizing",
        "status": STATUS,
        "decision": decision_text(
            chosen,
            val_surface[chosen.key],
            late_surface[chosen.key] if late_surface else None,
            frozen=late_surface is not None,
        ),
        "chosen": freeze["chosen"],
        "frozen_choice_immutable": True,
        "contract": CONTRACT,
        "model": model_report,
        "validation": {v.key: val_surface[v.key] for v in VIEWS},
        "validation_fixed_size_baseline": {v.key: val_surface[v.key] for v in BASELINE_VIEWS},
        "validation_baseline_comparison": baseline_comparison(val_surface),
        "validation_ranking": ranking,
        "late": ({v.key: late_surface[v.key] for v in VIEWS} if late_surface else None),
        "late_fixed_size_baseline": (
            {v.key: late_surface[v.key] for v in BASELINE_VIEWS} if late_surface else None
        ),
        "late_baseline_comparison": (baseline_comparison(late_surface) if late_surface else None),
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
