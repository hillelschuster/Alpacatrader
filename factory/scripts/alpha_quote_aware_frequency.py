#!/usr/bin/env python3
"""Quote-aware FREQUENT payoff study on the open-anchored top-gainer panel (no refit).

The retained lead in this lane is the frozen learned h60 minute-open model read at
threshold 0.03: ~2 trades/week. This producer asks one new question instead of
re-tuning the old minute-open proxy: can the SAME two immutable learned heads (h15 and
h60) be traded MORE OFTEN once the entry is gated on an OBSERVABLE, FIRM, FRESH quote and
the exit is a fresh-quote-gated market order priced at the real NBBO touch?

Nothing about the model moves. What moves is the execution book:

* TWO immutable heads are loaded from ``learned/models`` (h15, h60) and scored ONCE per
  day over the liquidity-qualified, strictly-past panel states. No refit, no HPO, no new
  features, no ticker/date identifiers, no label features. The stored ``feature_order``
  is verified against ``alpha_open_learned.FEATURES_ALL`` and the matrix is built with the
  very same ``feature_matrix`` helper used at fit time (26 causal features, null-filled,
  Float64). The 2021-02..2022-12 fit block is never replayed.
* TWELVE fixed views = 2 heads x 6 thresholds (0.003/0.005/0.010/0.015/0.020/0.030).
  The threshold is a bar on the model's own predicted gross payoff, never a re-fit knob.
* ENTRY is a fresh-quote-gated MARKETABLE IOC LIMIT. The order intent is the clock
  (t+1) at 00.000s: the latest RAW NBBO state is observed there (never pre-filtered for
  eligibility, so a stale, crossed or non-regular latest print is still observable), and
  the live entry rule is (a) firm and fresh (<=2s old, valid, un-crossed, R-only),
  (b) a fee-funded integer quantity q = floor(250 / (limitPrice * (1 + 150bps/2))) with
  q >= 1, (c) q <= the INTENT top-of-book ASK depth (known entry data only), and
  (d) spread + 25bps <= 10000 * predicted gross (the forecast must clear the observed
  touch plus the primary residual). q is sized at the HIGHEST reported residual rung, so
  the same causal q is funded at every rung and the worst-case reservation never exceeds
  the $250 ticket. LimitPrice = observed ASK * 1.01 rounded up to the cent: a
  PRE-DECLARED marketability cap, not charged friction. q, limitPrice and the
  reserved cash are all fixed BEFORE the arrival print is seen and are never re-floored
  from a future price; the arrival price can only make the conditional fill cheaper or
  turn the order into an unfilled no-match.
* At intent + 250ms the arrival print decides, from ONE observation of the latest RAW
  state at that clock: ask <= limit AND arrival ASK depth >= q is a CONDITIONAL
  full-touch IOC-model fill at the ACTUAL ask; ask > limit is a model NO_MATCH_AT_L1
  (unfilled cash, hidden liquidity and routed exchange fills unmeasured); a partial depth
  or a non-acquired stream is an execution UNKNOWN. A latest print that is still valid and
  R-only but STALE at arrival is an execution UNKNOWN - never a later, more favourable
  print. AN IOC LIMIT CANNOT REST: a genuinely invalid / non-regular / absent book at the
  arrival clock is an execution UNKNOWN (the unfilled remainder cancels), never a fill
  priced from a later print. Resting belongs to the MARKET exit leg only, where a
  submitted market order may rest through a genuinely unavailable book.
* EXIT is a FRESH-QUOTE-GATED SUBMISSION, not a "the book must be open" claim. Age > 2s
  does NOT expire a valid NBBO and does NOT cancel a submitted market order. The due
  intent is the deterministic hold (entry minute + h, capped at the RTH close) at clock
  00.000s. If the latest raw state there is firm and fresh, the market sell is submitted
  at the due intent; otherwise it is held until the FIRST eligible regular print observed
  at/after the due intent inside RTH and submitted at THAT timestamp. Pricing happens
  +250ms after the ACTUAL submission from the latest raw state at that arrival clock,
  with the fresh / stale / invalid trichotomy of a MARKET order (stale-but-valid at
  arrival is an UNKNOWN, an absent book lets the submitted market order conditionally
  rest). When the exit prices from a rested print, the REAL execution clock is that
  print's own timestamp: the position, its reserved cash and the ticker cooldown are held
  until then, never released at the planned arrival. Every wait - the exit submission
  hold and any resting print - is bounded by the LAST regular session minute
  (the panel's own session_end), and an unresolved position stays reserved to that
  boundary. The exit quantity is the causal q, never re-sized.
* BOOK: 3 slots x $250 = $750 nominal research allocation, no leverage, one position per
  ticker, flat 5-minute cooldown after the ACTUAL exit, at most 5 attempts per ticker per
  session. Simultaneous clock candidates are chosen by score and funded (up to three
  intents) BEFORE any arrival outcome is observed; a fourth same-clock candidate is never
  substituted after seeing an unfilled one. A later clock may use cash released by an
  earlier actual exit. An UNKNOWN position keeps its slot and its ticker blocked until it
  resolves or the session ends; the cash is never freed at a merely planned time.
* A causal quantity of zero (min-capital at the quoted limit) is a KNOWN NO ORDER: no
  order is sent, the cash stays unfilled, it is never a priced zero-return fill and never
  an UNKNOWN. A ticker whose quote stream was never acquired for that session is a DATA
  UNKNOWN acquisition, reported separately from both cash and rule skips.

Selection: the ENTIRE twelve-view grid contract is frozen to disk BEFORE any validation
outcome exists. The single view is then chosen ONLY by 2023 validation actual-touch known
contribution in dollars per full calendar day at the 25bps residual rung, on an explicit
PARTIAL basis (unknown executions are excluded from the numerator and reported beside it;
the full-loss lower bound is an optional guard, never the objective). No cost, count,
median, top-day or CI floor is applied: 25bps is ONE pre-declared scenario among the
reported rungs, not a hurdle, and no rung closes a candidate. ``selection_freeze.json``
is written AFTER the validation surface and BEFORE any late file is read; the late block
then runs all twelve views for transparency while the choice stays immutable.

Cost wording (human-financial accuracy, no imagined purity): the 0/5/10/25/50/75/100/
125/150 bps rungs are EXTRA residual fee/slippage scenarios charged ON TOP of the ACTUAL
ASK entry / BID exit touch prices, NOT total modeled friction and NOT actual broker fees.
The observed touch prices already carry the spread, so each rung charges only the extra
residual and the spread is never double-charged; weighing these rungs against the panel's
minute-open PROXY payoff labels is a SEPARATE produce, never a fudge layered on the touch.
A primary US broker's regular schedule (zero commission, SEC 20.6 per $1M, CAT 0.000003
per side, daily cent rounding) is typically well under 1 bp on a $250-$1,000 ticket, so
the low rungs describe a LOW-FEE OPPORTUNITY set rather than a fee claim: the 25 bps
primary rung is ONE pre-declared comparison scenario, never an enforced minimum, and no
rung - 100 bps included - vetoes a candidate. Actual provider fees are sourced in the
separate provider-fee ledger.

Prior data (no imagined purity): the 2023 validation block and the 2025-02..2026-05 late
block were BOTH explored before this replay. Neither is pristine or previously unknown;
the late block is a previously explored window and every late number is
DISCOVERY-NOT-VALIDATED. A pre-fix pilot of this producer also ran before the execution
semantics below were corrected (its arrival-rule IOC resting and its planned-exit-clock
cash release were both wrong); its artifacts live under the HISTORICAL root
``~/alpha-data/open-search-v1/quote_aware_frequency`` (contract version 1, kept for the
record, never the current baseline); its numbers are a PRE-FIX historical artifact, are
NOT runnable-IOC evidence, and must not be read as "no edge in this family". The fixed
semantics are deliberately untested against that pilot: a full re-run validates them, and
this producer's default output root is now the versioned ``quote_aware_frequency_v2`` so
an ordinary run can never overwrite the pilot's files.

Reported per view and per 0/5/10/25/50/75/100/125/150 rung over the whole calendar: known
contributions, fills / known fills / traded days / attempts, UNKNOWN and no-match counts,
causal skip reasons, monthly and yearly accounting, a day-level bootstrap, the worst known
fill and the simple $/(year on a $750 book) at 252 sessions. The headline is the measured
KNOWN-contribution expectation of the chosen view - not an actual account CAGR, not a
self-financing return, and not a claim that the unknown share is zero.

The model predicts a minute-open payoff (t -> t+h minute opens); the traded label here is
the ASK-to-BID NBBO touch. The two bases are NOT the same quantity: the panel's stored
minute-open proxy labels are carried into the artifacts for an explicit basis comparison
and are NEVER used as an entry filter.

This is a research replay of a historical conditional L1 IOC model. No live order, no
broker call, no account write, no protected outcome (2024, 2025-01, 2026-06..08) is ever
touched. Quotes measure the touch cost of a hypothetical order; they are not exchange
fills and never a fill guarantee.

Usage:
  uv run --no-sync python factory/scripts/alpha_quote_aware_frequency.py
      # full 250 validation + 332 late days -> ~/alpha-data/open-search-v1/quote_aware_frequency_v2
  uv run --no-sync python factory/scripts/alpha_quote_aware_frequency.py --resume
      # resume from the per-day state / daily / trade parts already on disk
  uv run --no-sync python factory/scripts/alpha_quote_aware_frequency.py --days 2023-05-15
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
from pathlib import Path

import joblib
import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))

from alpha_open_learned import FEATURES_ALL, PANEL_ROOT, bootstrap_daily, feature_matrix
from alpha_open_panel import allowed
from alpha_open_sim import causal_liquidity, period
from alpha_quote_audit import clock_us
from alpha_sparse_daily import day_context
from alpha_sparse_quote_service import (
    QUOTE_COLS,
    SOURCE_RANK_CACHE,
    SOURCE_RANK_SUPPLEMENT,
    UNIT_EPOCH,
    day_quote_path,
    dedup_quote_frames,
    regular_mask,
    supplement_day_path,
)

# ----- fixed configuration (no HPO, no refit, no grid search). -----------------
HEADS = (15, 60)  # the only two stored payoff heads ever loaded
THRESHOLDS = (0.003, 0.005, 0.010, 0.015, 0.020, 0.030)  # six fixed entry bars
RUNG_COSTS = (0.0, 5.0, 10.0, 25.0, 50.0, 75.0, 100.0, 125.0, 150.0)  # residual ladder, bps
SELECT_COST = 25.0  # primary selection / entry-rule rung: ONE scenario, never a hurdle
MAX_RUNG_COST = float(max(RUNG_COSTS))  # 150 bps: causal sizing + worst-case reservation
MAX_SLOTS = 3  # concurrent positions
ORDER_BUDGET = 250.0  # per-position ticket
BOOK = MAX_SLOTS * ORDER_BUDGET  # $750 nominal research sub-book
COOLDOWN_MIN = 5  # flat minutes after the ACTUAL exit
MAX_ATTEMPTS = 5  # attempts per ticker per session
LIMIT_CAP = 0.01  # pre-declared marketability cap on the entry limit price
TICK_DECIMALS = 2  # the limit price is rounded UP to the cent (marketable cap)
LATENCY_US = 250_000  # order arrival latency after the send clock
MAX_AGE_S = 2.0  # conservative freshness gate (NOT a quote-validity expiry)
ENTRY_OVERHEAD_BPS = 25.0  # residual added to the observed spread in the entry rule
ANNUAL_SESSIONS = 252  # simple session-count convention, NOT a CAGR claim
BOOT_N = 1000
BOOT_SEED = 20261009
VAL_BLOCK = "validation"
LATE_BLOCK = "confirmation"
EXPECTED_DAYS = {VAL_BLOCK: 250, LATE_BLOCK: 332}
MODEL_DIR = PANEL_ROOT / "learned" / "models"
OUTPUT = PANEL_ROOT / "quote_aware_frequency_v2"  # repaired-semantics root: the pre-fix
# pilot root "quote_aware_frequency" (contract version 1) is HISTORICAL and must never be
# overwritten by an ordinary default run
DATA_ROOT = Path("/home/hillel/projects/Alpacatrader") / "data"
PANEL_DAYS = PANEL_ROOT / "days"
PANEL_CONTRACT = PANEL_ROOT / "contract.json"
STATUS = "DISCOVERY-NOT-VALIDATED"
VERSION = 2  # 2: repaired execution semantics (IOC no-rest entry, actual priced-exit
# clock); 1 is the pre-fix pilot contract under the HISTORICAL quote_aware_frequency root
STATES_SCHEMA = 1
REPLAY_SCHEMA = 2  # 2: IOC no-rest entry, actual priced-exit clock, 9-rung fee grid
UNDERPOWERED_KNOWN_FILLS = 25  # label only; NEVER a drop or auto-kill threshold
PROTECTED_UNREAD = ["2024", "2025-01", "2026-06", "2026-07", "2026-08"]
# Existing fetch caches that are merged as supplements (original prints always win; a
# supplement may only ADD timestamps, never silently replace or revise a print).
SUPPLEMENT_ROOTS = (
    PANEL_ROOT / "sparse_execution_frontier" / "fetched_quotes" / "quotes",
    PANEL_ROOT / "quote_completeness_audit" / "quotes",
    PANEL_ROOT / "quote_completeness_audit" / "fetched_quotes" / "quotes",
)
QUOTE_CACHE_ROOT = DATA_ROOT / "sip" / "net" / "quotes"

CONTRACT = {
    "version": VERSION,
    "hypothesis": "the same two immutable learned payoff heads (h15/h60), traded at six "
    "fixed score bars with a fresh-quote-gated marketable-IOC-limit entry and a "
    "fresh-quote-gated market exit priced at the real NBBO touch, clear a residual "
    "round-trip cost more often than the retained rare h60 study",
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
        "heads": list(HEADS),
        "feature_order": list(FEATURES_ALL),
        "feature_matrix_helper": "alpha_open_learned.feature_matrix (Float64, null-filled)",
        "fit_block": "2021-02..2022-12 (never replayed, never re-scored as outcomes)",
        "target": "panel minute-open gross payoff labels gross_15 / gross_60",
        "clip_fit": [-0.5, 2.0],
    },
    "entry": {
        "intent_clock": "clock_us(day, t+1, 0)",
        "arrival_clock": "intent + 250ms",
        "observation": "latest RAW NBBO state at the intent clock (never pre-filtered)",
        "firm_fresh_rule": "valid, un-crossed, R-only, age <= 2s",
        "limit_price": "ceil_to_cent(observed_ask * 1.01): pre-declared marketability cap, "
        "NOT charged friction",
        "quantity": "q = floor(250 / (limit_price * (1 + 150bps/2))) at the HIGHEST reported "
        "rung, fixed BEFORE arrival",
        "quantity_rule": (
            "never re-floored from the arrival price; the same causal q is funded at every "
            "rung and the worst-case reservation never exceeds the $250 ticket"
        ),
        "depth_rule": "q >= 1 and q <= intent ASK top-of-book depth",
        "spread_rule": "spread_bps + 25 <= 10000 * predicted_gross",
        "arrival_rule": "ONE observation of the latest RAW state at the arrival clock: "
        "ask <= limit and arrival ASK depth >= q -> conditional full-touch IOC-model fill "
        "at the ACTUAL ask; ask > limit -> NO_MATCH_AT_L1 unfilled cash; partial depth / "
        "non-acquired stream -> execution UNKNOWN; valid-but-STALE at arrival -> UNKNOWN; "
        "invalid / non-regular / absent book at arrival -> execution UNKNOWN, because an "
        "IOC LIMIT CANNOT REST (no later print is ever used to fill it)",
        "no_order_rule": "q == 0 is a KNOWN no-order (no order sent, cash unfilled, never a "
        "priced zero-return fill, never an UNKNOWN)",
    },
    "exit": {
        "hold": "deterministic min(entry_minute + h, session_end) minutes, RTH close capped",
        "due_intent_clock": "clock_us(day, entry_minute + h, 0)",
        "submission": "FRESH-QUOTE-GATED: submit at the due intent if the latest raw state "
        "is firm and fresh, else hold to the FIRST eligible regular print at/after the due "
        "intent inside RTH and submit at its timestamp",
        "arrival_clock": "actual submission + 250ms",
        "pricing": "latest raw state at the arrival clock; stale-but-valid -> UNKNOWN; "
        "invalid / non-regular / absent book -> the submitted MARKET order conditionally "
        "rests to the first eligible print at/after arrival (never a later favourable "
        "print); the priced clock - that print's own timestamp when rested - is the REAL "
        "execution clock, so the position, its reserved cash and the ticker cooldown are "
        "held until then",
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
        "cooldown": "flat 5 minutes after the ACTUAL exit clock",
        "max_attempts_per_ticker_per_day": MAX_ATTEMPTS,
        "cash_reservation": "the causal worst-case rung-150 outlay q * limit * "
        "(1 + 150bps/2), so every reported rung stays inside the $250 ticket",
        "simultaneous_clock_rule": "highest-score eligible intents are funded (up to the "
        "slot limit) BEFORE any arrival outcome is observed; no same-clock substitution "
        "after an unfilled intent",
        "unknown_rule": "an UNKNOWN position keeps its slot and blocks its ticker until it "
        "resolves or the session ends; cash is never freed at a planned time",
        "later_clock_rule": "a later signal may use cash released by an earlier ACTUAL exit",
    },
    "selection": {
        "objective": "2023 validation actual-touch known contribution dollars per full "
        "calendar day",
        "selection_cost_bps": SELECT_COST,
        "cost_ladder_bps": list(RUNG_COSTS),
        "basis": "PARTIAL: unknown executions are excluded from the numerator and reported "
        "beside it; the full-loss lower bound is an optional guard, never the objective",
        "power_floors": None,
        "median_or_tail_gates": None,
        "cost_or_count_kill": None,
        "frozen_before_late_inspection": True,
        "freeze_artifact": "selection_freeze.json (written after the validation surface, "
        "before any late file is read)",
    },
    "costs": {
        "rung_bps": list(RUNG_COSTS),
        "primary_selection_rung_bps": SELECT_COST,
        "selection_rung_is_one_scenario_not_a_hurdle": True,
        "cost_or_count_kill": None,
        "scenario_nature": (
            "every rung is an EXTRA residual fee/slippage scenario charged ON TOP of the "
            "ACTUAL observed ASK entry / BID exit touch prices, NOT total modeled "
            "minute-proxy friction and NOT an actual broker fee; the touch prices already "
            "carry the spread, so each rung charges only the extra residual and the spread "
            "is never double-charged; the minute-proxy comparison is a separate produce"
        ),
        "low_fee_opportunity_label": (
            "0..150 bps describe a LOW-FEE OPPORTUNITY set, not minimum real provider fees: "
            "a primary US broker's regular schedule (zero commission, SEC 20.6 per $1M, "
            "CAT 0.000003 per side, daily cent rounding) is typically well under 1 bp on a "
            "$250-$1,000 ticket; the 25 bps primary rung is ONE pre-declared comparison "
            "scenario, never an enforced minimum, and no rung (100 included) vetoes a "
            "candidate; actual provider fees are sourced in the separate provider-fee "
            "ledger, never asserted here"
        ),
        "quantity_sizing_rung_bps": MAX_RUNG_COST,
        "reservation_rule": (
            "reserved = q * limit * (1 + rung/2) with q sized at the highest rung, so "
            "reserved <= $250 for every rung"
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
        "pre_fix_pilot": {
            "exists": True,
            "semantics": (
                "a full-run PILOT of this producer completed before the execution "
                "semantics below were repaired; its arrival rule let the IOC entry REST "
                "into a future print and it released cash/cooldown at the planned exit "
                "arrival instead of the priced clock"
            ),
            "status": "PRE-FIX HISTORICAL ARTIFACT - not runnable-IOC evidence",
            "reading": (
                "the pilot's (VAL chosen h15 thr 0.010 at 25bps, +1.27 $/day known over "
                "208 known fills / 108 traded days / 10 UNKNOWN; late 517 known of 540 "
                "attempts, -1.38 $/day) numbers measure the BUGGY execution model and must "
                "not be read as 'no edge in this family'; they are neither a validation nor "
                "a falsification of the fixed semantics"
            ),
            "artifact": (
                "producer_snapshot.py of the pre-repair run under the HISTORICAL root "
                "~/alpha-data/open-search-v1/quote_aware_frequency (contract version 1, "
                "kept by the parent, never the current baseline; this producer's default "
                "root is quote_aware_frequency_v2)"
            ),
        },
    },
    "labels": {
        "traded_basis": "real NBBO ASK entry -> BID exit at the declared clocks",
        "model_basis": "minute-open payoff proxy (t -> t+h actual minute opens)",
        "proxy_labels_used_as_entry_filter": False,
        "unfilled_entry_status_used_as_entry_filter": False,
    },
    "no_live_orders": True,
    "supplements": {
        "merge_order": ["ranked SIP day cache"] + [str(r) for r in SUPPLEMENT_ROOTS],
        "dedup_rule": (
            "sort the merged frame by (source rank in merge_order, then every quoted "
            "field) and keep the first row per (symbol, ts_utc) with the order "
            "maintained: the ORIGINAL print always wins, a supplement may ONLY add "
            "timestamps and never silently replaces or revises an original print, and "
            "the winner inside one source is the smallest print of the quoted-field "
            "order (never the friendlier side)"
        ),
        "completeness_audit_quotes": str(SUPPLEMENT_ROOTS[1]),
        "audit_results_awaited": False,
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


def view_key(head: int, threshold: float) -> str:
    return f"h{head}|{threshold:.4f}"


def view_label(head: int, threshold: float) -> str:
    return f"h{head}_thr{int(round(threshold * 10000))}bps"


@dataclass(frozen=True)
class View:
    """One (head, threshold) cell. Same model, same universe; only the bar differs."""

    head: int
    threshold: float

    @property
    def key(self) -> str:
        return view_key(self.head, self.threshold)

    @property
    def label(self) -> str:
        return view_label(self.head, self.threshold)


VIEWS = tuple(View(h, thr) for h in HEADS for thr in THRESHOLDS)
VIEWS_BY_KEY = {v.key: v for v in VIEWS}

# ----- clock cache ------------------------------------------------------------
_CLOCK: dict[tuple[str, int], int] = {}


def minute_us(day: str, minute: int) -> int:
    """ET ``minute`` at clock 00.000s, in UTC microseconds (cached per day/minute)."""
    key = (day, int(minute))
    out = _CLOCK.get(key)
    if out is None:
        out = clock_us(day, int(minute), 0)
        _CLOCK[key] = out
    return out


def arrival_us(send_us: int) -> int:
    return int(send_us) + LATENCY_US


# ----- quote substrate --------------------------------------------------------
def _read_quote_day(path: Path, tickers: set[str]) -> pl.DataFrame:
    """Raw quote rows of one cache file for ``tickers`` (no eligibility pre-filter)."""
    frame = (
        pl.scan_parquet(path)
        .filter(pl.col("symbol").is_in(sorted(tickers)))
        .select(list(QUOTE_COLS))
        .collect()
    )
    missing = [c for c in QUOTE_COLS if c not in frame.columns]
    if missing:
        raise ValueError(f"quote frame {path} missing raw columns {missing}")
    return frame


def quote_day_frame(
    data_root: Path, day: str, tickers: set[str], supplemental_roots: tuple[Path, ...]
) -> pl.DataFrame | None:
    """One ET day's raw quotes: the ranked SIP cache first, then supplement-only prints.

    Mirrors ``alpha_sparse_quote_service.load_day_quotes`` through the same shared
    deterministic merge (``dedup_quote_frames``): the ORIGINAL print wins for a
    duplicated (symbol, ts_utc) and a supplement may only add timestamps, so no original
    print is ever silently replaced or revised. Inside one source the surviving print is
    the minimum of the quoted-field order of ``DEDUP_SORT`` (bid, bid size, ask, ask
    size, exchanges, conditions, tape) - a stable documented rule, never the friendlier
    side of the market - and the resolution no longer depends on the order the frames
    are read or concatenated in. The full raw history is returned; the R-only /
    un-crossed eligibility mask is applied at READ time (never by dropping rows),
    so the latest RAW state at any clock stays observable.
    """
    ranked: list[tuple[int, pl.DataFrame]] = []
    primary = day_quote_path(data_root, day)
    if primary.exists():
        ranked.append((SOURCE_RANK_CACHE, _read_quote_day(primary, tickers)))
    for rank, root in enumerate(supplemental_roots, start=SOURCE_RANK_SUPPLEMENT):
        spath = supplement_day_path(root, day)
        if spath.exists():
            ranked.append((rank, _read_quote_day(spath, tickers)))
    if not ranked:
        return None
    merged = dedup_quote_frames(ranked)
    return None if merged.is_empty() else merged


@dataclass(frozen=True)
class SymbolQuotes:
    """One ticker's sorted, deduplicated RAW NBBO prints for one ET session.

    ``regular``/``valid``/``eligible`` are masks computed at read time; the rows themselves
    are never dropped, so the latest RAW state (stale, crossed, non-regular) is still
    observable and reported instead of silently falling back to an older regular print.
    """

    ticker: str
    stamps: np.ndarray
    bid: np.ndarray
    ask: np.ndarray
    bid_shares: np.ndarray
    ask_shares: np.ndarray
    regular: np.ndarray
    valid: np.ndarray

    @property
    def eligible(self) -> np.ndarray:
        return self.regular & self.valid

    def raw_index(self, target_us: int) -> int:
        return int(np.searchsorted(self.stamps, int(target_us), side="right")) - 1

    def quote(self, i: int, ref_us: int) -> dict:
        ask, bid = float(self.ask[i]), float(self.bid[i])
        return {
            "ticker": self.ticker,
            "bid": bid,
            "ask": ask,
            "bid_shares": float(self.bid_shares[i]),
            "ask_shares": float(self.ask_shares[i]),
            "age_s": (int(ref_us) - int(self.stamps[i])) / 1_000_000,
            "quote_us": int(self.stamps[i]),
            "spread_bps": (ask / bid - 1.0) * 10_000.0 if bid else None,
        }

    def latest_raw(self, target_us: int) -> tuple[dict | None, str]:
        """Latest RAW print at/before the target clock with its eligibility verdict.

        No age cap and no fallback to an older regular print: exactly what
        ``alpha_quote_audit.asof_quote`` observes, minus its age filter.
        """
        i = self.raw_index(target_us)
        if i < 0:
            return None, "no_prior_quote"
        if not self.valid[i]:
            return None, "invalid_or_crossed_quote"
        if not self.regular[i]:
            return None, "nonregular_quote"
        return self.quote(i, int(target_us)), "quoted"

    def first_eligible_at_or_after(self, target_us: int, end_us: int) -> tuple[dict | None, str]:
        """First eligible (valid, un-crossed, R-only) print in [target_us, end_us]."""
        i = int(np.searchsorted(self.stamps, int(target_us), side="left"))
        eligible = self.eligible
        while i < len(self.stamps) and int(self.stamps[i]) <= int(end_us):
            if eligible[i]:
                return self.quote(i, int(self.stamps[i])), "regular_quote"
            i += 1
        return None, "no_valid_regular_quote_before_session_end"


def symbol_quotes(frame: pl.DataFrame | None, day: str) -> dict[str, SymbolQuotes]:
    """Per-ticker raw quote arrays for one ET day (lot-unit epoch aware)."""
    if frame is None or frame.is_empty():
        return {}
    multiplier = 100 if day < UNIT_EPOCH else 1
    marked = regular_mask(frame)
    out: dict[str, SymbolQuotes] = {}
    for (symbol,), part in marked.partition_by("symbol", as_dict=True).items():
        stamps = part["ts_utc"].cast(pl.Int64).to_numpy().astype(np.int64)
        bid = part["bid_price"].to_numpy().astype(float)
        ask = part["ask_price"].to_numpy().astype(float)
        bid_size = part["bid_size"].to_numpy().astype(float) * multiplier
        ask_size = part["ask_size"].to_numpy().astype(float) * multiplier
        regular = part["regular"].to_numpy().astype(bool)
        valid = (
            np.isfinite(bid)
            & np.isfinite(ask)
            & np.isfinite(bid_size)
            & np.isfinite(ask_size)
            & (bid > 0)
            & (ask > 0)
            & (bid <= ask)
        )
        out[str(symbol)] = SymbolQuotes(
            ticker=str(symbol),
            stamps=stamps,
            bid=bid,
            ask=ask,
            bid_shares=bid_size,
            ask_shares=ask_size,
            regular=regular,
            valid=valid,
        )
    return out


# ----- causal execution policy ------------------------------------------------
def limit_price_of(ask: float) -> float:
    """Pre-declared marketability cap: observed ASK * 1.01 rounded UP to the cent."""
    return math.ceil(float(ask) * (1.0 + LIMIT_CAP) * 10**TICK_DECIMALS) / 10**TICK_DECIMALS


def causal_quantity(limit_price: float, cost_bps: float = MAX_RUNG_COST) -> int:
    """Fee-funded integer quantity from the INTENT limit price (never the arrival price).

    The quantity is sized at the HIGHEST reported rung by default, so the same causal q is
    funded at every rung and no rung can be unfundable on the $250 ticket. Callers that
    need a different sizing rung pass it explicitly.
    """
    return int(ORDER_BUDGET // (float(limit_price) * (1.0 + cost_bps / 20_000.0)))


def reserved_usd(quantity: int, limit_price: float, cost_bps: float = MAX_RUNG_COST) -> float:
    """Worst-case reservation at the highest reported rung, so no rung is unfundable."""
    return float(quantity) * float(limit_price) * (1.0 + cost_bps / 20_000.0)


def intent_facts(sq: SymbolQuotes | None, intent_us: int) -> dict:
    """The causal intent observation and the size it implies (entry rule, part 1).

    ``intent_reason`` is the head-independent part of the entry rule (the intent observation,
    the funded quantity and the intent depth); the spread bar is the only head-dependent
    part and is decided per head by ``entry_rule_status``.
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
    if sq is None:
        base["intent_status"] = "quote_not_acquired"
    else:
        q, status = sq.latest_raw(intent_us)
        if q is None:
            base["intent_status"] = status
        else:
            base["intent_status"] = "quoted"
            limit = limit_price_of(q["ask"])
            qty = causal_quantity(limit)
            base.update(
                {
                    "intent_ask": q["ask"],
                    "intent_bid": q["bid"],
                    "intent_age_s": q["age_s"],
                    "intent_spread_bps": q["spread_bps"],
                    "intent_ask_shares": q["ask_shares"],
                    "limit_price": limit,
                    "qty": qty,
                    "reserved_usd": reserved_usd(qty, limit),
                }
            )
    base["intent_reason"] = entry_rule_status(base, float("inf"))[0]
    return base


def entry_rule_status(facts: dict, pred: float) -> tuple[str, bool]:
    """Entry rule outcome for one head: a causal skip reason or ``"eligible"``.

    The freshness gate is an operational eligibility gate (age <= 2s), NOT a claim that an
    older NBBO expired: it only says this policy does not send into an unobservable book.
    """
    if facts["intent_status"] == "quote_not_acquired":
        return "quote_not_acquired", False
    if facts["intent_status"] != "quoted":
        return "intent_not_firm_fresh", False
    age = facts["intent_age_s"]
    if age is None or not math.isfinite(age) or float(age) > MAX_AGE_S:
        return "intent_not_firm_fresh", False
    if facts["qty"] < 1:
        return "no_order_min_capital", False
    depth = facts["intent_ask_shares"]
    if depth is None or not math.isfinite(depth):
        return "intent_depth_unknown", False
    if facts["qty"] > depth:
        return "intent_depth_unsupported", False
    spread = facts["intent_spread_bps"]
    if spread is None or spread + ENTRY_OVERHEAD_BPS > 10_000.0 * float(pred):
        return "spread_above_prediction", False
    return "eligible", True


def submission_clock(sq: SymbolQuotes, due_us: int, session_end_us: int) -> tuple[int | None, str]:
    """FRESH-QUOTE-GATED submission of a market order at its due intent.

    Age > 2s does not expire a valid NBBO and does not cancel a submitted order: it only
    means this policy does not send blindly into an unobservable book. A firm, fresh latest
    raw state submits at the due intent; anything else holds the order until the FIRST
    eligible regular print observed at/after the due intent inside RTH and submits at that
    print's own timestamp.
    """
    q, status = sq.latest_raw(due_us)
    if q is not None and q["age_s"] <= MAX_AGE_S:
        return int(due_us), "submit_at_due_intent_firm_fresh"
    rested, _ = sq.first_eligible_at_or_after(due_us, session_end_us)
    if rested is None:
        return None, "no_valid_regular_quote_before_session_end"
    return int(rested["quote_us"]), "submit_at_first_regular_after_due_intent"


def priced_leg(
    sq: SymbolQuotes, arrival_us_: int, session_end_us: int
) -> tuple[dict | None, str, int | None]:
    """Price one leg from the LATEST raw state at the order's arrival clock.

    MARKET-order semantics (EXIT leg only): a submitted market order may rest through a
    genuinely unavailable book.

    * firm/fresh valid print            -> priced at that print, clock = the arrival
    * valid but STALE print at arrival   -> UNKNOWN (never a later, more favourable print)
    * invalid / non-regular / no book    -> the working order conditionally rests to the
      first eligible print at/after arrival (already-elapsed ack, depth support only);
      the returned third value is THAT print's clock, never the planned arrival
    * nothing before the session close   -> UNKNOWN
    """
    q, status = sq.latest_raw(arrival_us_)
    if q is not None:
        if q["age_s"] <= MAX_AGE_S:
            return q, "priced_fresh_at_arrival", arrival_us_
        return None, "unknown_stale_but_valid_at_arrival", arrival_us_
    rested, _ = sq.first_eligible_at_or_after(arrival_us_, session_end_us)
    if rested is None:
        return None, f"unknown_no_valid_regular_at_or_after_arrival:{status}", None
    return rested, "priced_rested_until_regular", int(rested["quote_us"])


def entry_price_leg(sq: SymbolQuotes, arrival_us_: int) -> tuple[dict | None, str]:
    """Price the IOC LIMIT entry: ONE observation of the latest RAW state at arrival.

    An IOC limit cannot rest: there is no waiting for a later, more favourable print, so
    this helper never looks past the arrival clock.

    * firm, fresh, valid, R-only print at arrival -> priced at that print
    * valid but STALE print at arrival             -> UNKNOWN
    * invalid / non-regular / absent book          -> UNKNOWN (the unfilled remainder of an
      IOC cancels; hidden liquidity and routed exchange fills are unmeasured)

    The MARKET-exit counterpart is ``priced_leg``, where resting is legitimate.
    """
    q, status = sq.latest_raw(arrival_us_)
    if q is None:
        return None, status
    if q["age_s"] > MAX_AGE_S:
        return None, "unknown_stale_but_valid_at_arrival"
    return q, "quoted_fresh_at_arrival"


# ----- immutable stored models ------------------------------------------------
def load_stored_models() -> tuple[dict, dict]:
    """Load the two immutable heads; verify order/horizon; disclose every fit fact."""
    models: dict[int, dict] = {}
    report: dict[str, dict] = {}
    for head in HEADS:
        path = MODEL_DIR / f"payoff_h{head}.joblib"
        if not path.exists():
            raise SystemExit(f"[quote-aware] stored model missing: {path}")
        bundle = joblib.load(path)
        order = list(bundle.get("feature_order") or [])
        if order != list(FEATURES_ALL):
            raise SystemExit(
                f"[quote-aware] h{head} stored feature_order != current FEATURES_ALL "
                f"({len(order)} vs {len(FEATURES_ALL)})"
            )
        if int(bundle.get("horizon", -1)) != head:
            raise SystemExit(f"[quote-aware] h{head} stored horizon {bundle.get('horizon')}")
        booster = bundle["lgbm"]
        # The stored heads were fitted from a numpy matrix, so the booster itself carries
        # generic column names: the ORDER contract is bundle["feature_order"] (verified
        # against FEATURES_ALL) plus the shared feature_matrix helper, and the booster is
        # checked to be pure-matrix-named (never a different symbolic order).
        names = list(getattr(booster, "feature_name_", []) or [])
        generic = [f"Column_{i}" for i in range(len(order))]
        if names and names != order and names != generic:
            raise SystemExit(
                f"[quote-aware] h{head} booster feature names match neither the stored "
                "feature_order nor the generic matrix names"
            )
        n_feat = int(getattr(booster, "n_features_in_", 0) or len(order))
        if n_feat != len(order):
            raise SystemExit(
                f"[quote-aware] h{head} booster feature count {n_feat} != {len(order)}"
            )
        models[head] = bundle
        report[f"h{head}"] = {
            "path": str(path),
            "sha256": sha256_file(path),
            "horizon": head,
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
        }
    report["feature_order_source"] = (
        "alpha_open_learned.FEATURES_ALL (22 path + 4 past-only peer context)"
    )
    report["feature_matrix"] = (
        "alpha_open_learned.feature_matrix: Float64 in the frozen order, nulls filled with "
        "0.0 exactly as at fit time; no per-day or per-symbol imputation"
    )
    report["training_immutability"] = {
        "fitted_on": "2021-02..2022-12 panel days, t%15==0, filled_proxy entries, known labels",
        "replayed_or_rescored_as_outcomes": False,
        "refit": False,
        "hpo": False,
        "feature_changes": False,
        "ticker_or_date_features": False,
    }
    return models, report


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
    "proxy_gross_15",
    "proxy_gross_60",
    "pred_15",
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
    "admit_t": pl.Int64,
    "entry_et": pl.Int64,
    "entry_open": pl.Float64,
    "entry_status": pl.String,
    "session_end": pl.Int64,
    "proxy_gross_15": pl.Float64,
    "proxy_gross_60": pl.Float64,
    "pred_15": pl.Float64,
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
    "limit_price": pl.Float64,
    "qty": pl.Int64,
    "reserved_usd": pl.Float64,
    "rule_15": pl.Boolean,
    "rule_60": pl.Boolean,
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
    (t+1) clock 0), the entry rule is evaluated per head, and the panel's own minute-open
    labels are carried along for the basis comparison only - never as an entry filter.
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
    preds = {
        head: np.asarray(models[head]["lgbm"].predict(feature_matrix(cand)), dtype=float)
        for head in HEADS
    }
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
            "gross_15",
            "gross_60",
        ]
    ).to_dicts()
    intent_counts: Counter = Counter()
    rule_counts: Counter = Counter()
    reason_counts: Counter = Counter()
    out: list[dict] = []
    session_end = int(rows[0]["session_end"])
    for i, r in enumerate(rows):
        entry_minute = int(r["t"]) + 1
        intent_us = minute_us(day, entry_minute)
        sq = streams.get(r["ticker"])
        facts = intent_facts(sq, intent_us)
        intent_counts[facts["intent_status"]] += 1
        rec = {
            "day": day,
            "ticker": r["ticker"],
            "t": int(r["t"]),
            "admit_t": int(r["admit_t"]) if r["admit_t"] is not None else None,
            "entry_et": int(r["entry_et"]) if r["entry_et"] is not None else None,
            "entry_open": r["entry_open"],
            "entry_status": r["entry_status"],
            "session_end": session_end,
            "proxy_gross_15": r["gross_15"],
            "proxy_gross_60": r["gross_60"],
            "pred_15": float(preds[15][i]),
            "pred_60": float(preds[60][i]),
            "entry_minute": entry_minute,
            "intent_us": intent_us,
        }
        rec.update(facts)
        for head in HEADS:
            reason, ok = entry_rule_status(facts, rec[f"pred_{head}"])
            rule_counts[f"{reason}_h{head}"] += 1
            rec[f"rule_{head}"] = bool(ok)
        reason_counts[rec["intent_reason"]] += 1
        out.append(rec)
    cov["intent_status_counts"] = dict(intent_counts)
    cov["entry_rule_counts"] = dict(rule_counts)
    cov["intent_reason_counts"] = dict(reason_counts)
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
    model_shas = {f"h{h}": sha256_file(MODEL_DIR / f"payoff_h{h}.joblib") for h in HEADS}
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


# ----- per-view causal replay -------------------------------------------------
DAILY_COLUMNS = (
    "day",
    "view_key",
    "head",
    "threshold",
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
DAILY_TYPES = {
    "day": pl.String,
    "view_key": pl.String,
    "head": pl.Int64,
    "threshold": pl.Float64,
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
    "no_order_min_capital": pl.Int64,
    "quote_not_acquired": pl.Int64,
    "intent_not_firm_fresh": pl.Int64,
    "intent_depth_unsupported": pl.Int64,
    "intent_depth_unknown": pl.Int64,
    "spread_above_prediction": pl.Int64,
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
    "exit_age_s",
    "exit_quote_us",
    "exit_price_status",
    "exit_status",
    "actual_exit_us",
    "fill_status",
    "proxy_gross",
    "proxy_entry_status",
)
TRADE_TYPES = {
    "day": pl.String,
    "view_key": pl.String,
    "head": pl.Int64,
    "threshold": pl.Float64,
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
    "exit_age_s": pl.Float64,
    "exit_quote_us": pl.Int64,
    "exit_price_status": pl.String,
    "exit_status": pl.String,
    "actual_exit_us": pl.Int64,
    "fill_status": pl.String,
    "proxy_gross": pl.Float64,
    "proxy_entry_status": pl.String,
}
for _rung in RUNG_COSTS:
    TRADE_COLUMNS += (f"net_usd_{int(_rung)}",)
    TRADE_TYPES[f"net_usd_{int(_rung)}"] = pl.Float64


def empty_daily() -> pl.DataFrame:
    return pl.DataFrame(schema={c: DAILY_TYPES[c] for c in DAILY_COLUMNS})


def empty_trades() -> pl.DataFrame:
    return pl.DataFrame(schema={c: TRADE_TYPES[c] for c in TRADE_COLUMNS})


def view_reason(intent_reason: str, rule_ok: bool) -> str:
    """Head-specific skip reason: the intent gate first, then the spread bar."""
    if rule_ok:
        return "eligible"
    return intent_reason if intent_reason != "eligible" else "spread_above_prediction"


def net_usd(quantity: int, exit_bid: float, entry_ask: float, cost_bps: float) -> float:
    """Realized dollars of the causal round trip at one residual rung (fees both legs)."""
    side = cost_bps / 20_000.0
    return float(quantity) * (float(exit_bid) * (1.0 - side) - float(entry_ask) * (1.0 + side))


def resolve_trade(
    day: str, r: dict, view: View, sq: SymbolQuotes, session_end: int, session_end_us: int
) -> dict:
    """Resolve one funded intent: the entry IOC-limit leg, then the scheduled exit leg.

    The ENTRY is an IOC limit priced from ONE observation of the latest raw state at its
    arrival clock (it never rests, so no future print can fill it). The EXIT is a MARKET
    order that may rest, and its position, reserved cash and ticker cooldown are released
    at the clock its price actually came from - never at the planned arrival. Nothing here
    re-sizes the causal quantity or substitutes a more favourable print.
    """
    head = view.head
    record = {
        "day": day,
        "view_key": view.key,
        "head": head,
        "threshold": view.threshold,
        "ticker": r["ticker"],
        "t": int(r["t"]),
        "entry_minute": int(r["entry_minute"]),
        "intent_us": int(r["intent_us"]),
        "score": float(r[f"pred_{head}"]),
        "pred": float(r[f"pred_{head}"]),
        "limit_price": float(r["limit_price"]),
        "qty": int(r["qty"]),
        "reserved_usd": float(r["reserved_usd"]),
        "proxy_gross": r[f"proxy_gross_{head}"],
        "proxy_entry_status": r["entry_status"],
        "entry_status": None,
        "entry_ask": None,
        "entry_bid": None,
        "entry_age_s": None,
        "entry_quote_us": None,
        "entry_arrival_us": arrival_us(int(r["intent_us"])),
        "exit_minute": min(int(r["entry_minute"]) + head, session_end),
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
    }
    for rung in RUNG_COSTS:
        record[f"net_usd_{int(rung)}"] = None

    # ---- entry leg: the causal IOC limit was fixed at the intent clock. An IOC cannot
    # rest, so the arrival clock is priced from ONE observation of the latest raw state;
    # a stale/invalid/absent book there is an UNKNOWN, never a later print's fill.
    entry_arrival = record["entry_arrival_us"]
    eq, estat = entry_price_leg(sq, entry_arrival)
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
    record["entry_status"] = "conditional_ioc_fill"

    # ---- exit leg: fresh-quote-gated submission, +250ms after the actual submission.
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
    xq, xstat, priced_at = priced_leg(sq, exit_arrival, session_end_us)
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
    record["exit_status"] = "conditional_market_fill"
    record["fill_status"] = "conditional_fill"
    # The REAL execution clock is the third value priced_leg returns: the arrival clock
    # when the print was fresh, or the first future eligible print's own timestamp when
    # the submitted market order had already rested. It is NEVER the prior quote's stamp
    # (which can predate the arrival) and never the planned arrival when the order rested:
    # the position, its reserved cash and the ticker cooldown are released only here. A
    # priced exit therefore ALWAYS carries its price clock; a priced quote with NO clock is
    # a protocol violation, raised here and never closed at the planned arrival.
    if priced_at is None:
        raise RuntimeError(
            f"[quote-aware] priced exit without a price clock: {day} {r['ticker']} status={xstat}"
        )
    record["actual_exit_us"] = int(priced_at)
    for rung in RUNG_COSTS:
        record[f"net_usd_{int(rung)}"] = net_usd(
            record["qty"], record["exit_bid"], record["entry_ask"], rung
        )
    return record


def replay_day(
    day: str, states: pl.DataFrame, streams: dict[str, SymbolQuotes], view: View
) -> tuple[dict, list[dict]]:
    """One view, one session: fresh-quote-gated entries under the funded $750 book.

    Chronological by intent clock; the highest-score eligible intents of a simultaneous
    clock are funded (up to the slot and cash limits) BEFORE any arrival outcome is seen,
    so a fourth same-clock candidate is never substituted after an unfilled one. Cash comes
    back only at an ACTUAL exit clock, an unmatched entry's arrival, or the session end.
    """
    daily = dict.fromkeys(DAILY_COLUMNS, 0)
    daily.update(
        {
            "day": day,
            "view_key": view.key,
            "head": view.head,
            "threshold": view.threshold,
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
    rule_col, pred_col = f"rule_{view.head}", f"pred_{view.head}"
    candidates = states.filter(pl.col(pred_col) >= view.threshold).sort(
        ["entry_minute", pred_col, "ticker"], descending=[False, True, False]
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
            if bool(r[rule_col]):
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
                reason = view_reason(r["intent_reason"], False)
                daily[reason] += 1
        # Every intent of this clock is funded before any arrival outcome is observed.
        for r in admitted:
            record = resolve_trade(day, r, view, streams[r["ticker"]], session_end, session_end_us)
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
                # resolution, and the ticker is then blocked for a flat 5 minutes.
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
    out_dir: Path, block: str, days: list[str], resume: bool, supplemental_roots: tuple[Path, ...]
) -> dict:
    """Replay every view on every day of one block, writing resumable per-day parts."""
    producer = producer_sha256()
    contract_sha = _digest_bytes(CONTRACT)
    sup_digest, _ = supplement_digest(supplemental_roots)
    model_shas = {f"h{h}": sha256_file(MODEL_DIR / f"payoff_h{h}.joblib") for h in HEADS}
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
                f"[quote-aware] scored states missing for {day} ({states_path}); run the "
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
        if n % 25 == 0 or n == len(todo):
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
                    default=_default,
                ),
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
            col: float(rows[col].sum()),
            f"mean_daily_{col}": float(rows[col].mean()),
        }
    return out


def view_cell(daily: pl.DataFrame, trades: pl.DataFrame, view: View, days: list[str]) -> dict:
    """Every reported number for one (head, threshold) view on one block's calendar."""
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
        "threshold": view.threshold,
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
        "dollars_per_year_252_on_book_750": {
            str(r): known_per_day[r] * ANNUAL_SESSIONS for r in rung_keys
        },
        "annualization": (
            f"mean daily known contribution x {ANNUAL_SESSIONS} sessions on a "
            f"${int(BOOK)} nominal book; a simple session-count convention, NOT a CAGR and "
            "NOT an account claim"
        ),
        "bootstrap_daily_known_usd": bootstrap,
        "bootstrap_daily_return_on_book": series_book,
        "skip_reasons": {
            reason: int(rows[reason].sum())
            for reason in (
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
        },
        "monthly": monthly_view(rows),
        "yearly": yearly_view(rows),
    }
    trades_view = trades.filter(pl.col("view_key") == view.key)
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
            "mean_proxy_gross_minute_open_basis": None,
            "mean_touch_gross_0bps": None,
            "fill_status_counts": {},
            "exit_submit_status_counts": {},
            "mean_hold_minutes": None,
        }
    return cell


def aggregate_block(out_dir: Path, block: str, days: list[str]) -> tuple[dict, dict]:
    """The full twelve-view surface of one block, plus the block's data coverage."""
    daily, trades = read_block_frames(out_dir, block)
    surface = {view.key: view_cell(daily, trades, view, days) for view in VIEWS}
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
        "causal_skips": {
            reason: int(daily[reason].sum())
            for reason in (
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
            "head": v.head,
            "threshold": v.threshold,
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
            "signal set is cash, not a positive edge. No actual-touch positive formulation "
            "was measured; the retained rare h60 lead stays the reference and nothing here "
            "is promoted."
        )
    if objective <= 0:
        return (
            f"NO_ACTUAL_TOUCH_POSITIVE_FORMULATION: the best of the twelve views "
            f"({chosen.label}) measures {objective:+.2f} $/calendar day of KNOWN "
            f"contribution on 2023 validation at {int(SELECT_COST)}bps over {fills} known "
            f"fills (day bootstrap p>0 = {boot.get('p_gt_zero')}). All twelve views are "
            "measured and none is positive, so the retained rare h60 lead stays the "
            "reference; the unknown share is reported beside this number and is NOT assumed "
            "zero."
        )
    text = (
        f"{STATUS}: chosen {chosen.label} (h{chosen.head} at threshold {chosen.threshold:.4f}) "
        f"by 2023 validation actual-touch known contribution: {objective:+.2f} $/calendar "
        f"day at {int(SELECT_COST)}bps = "
        f"{val['dollars_per_year_252_on_book_750'][sel_key]:+.0f} $/year on the "
        f"${int(BOOK)} nominal book (simple 252-session convention, not a CAGR), "
        f"{fills} known fills on {val['traded_days']} traded days of {val['days_replayed']} "
        f"calendared days, {val['unknown_fills']} UNKNOWN executions "
        f"({100.0 * val['unknown_fills'] / max(1, val['attempts']):.1f}% of attempts) and "
        f"{val['no_match_fills']} no-match unfilled intents. Day bootstrap p>0 = "
        f"{boot.get('p_gt_zero')}. This is the measured KNOWN-contribution expectation on a "
        "PARTIAL basis (unknowns excluded from the numerator), not an actual account CAGR."
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
def run(args: argparse.Namespace) -> None:
    t0 = time.time()

    out = args.out or OUTPUT
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")

    models, model_report = load_stored_models()
    print(
        "[model] immutable heads loaded (no refit): "
        + ", ".join(
            f"h{h} sha256 {model_report[f'h{h}']['sha256'][:12]} "
            f"({model_report[f'h{h}']['n_features']} features)"
            for h in HEADS
        ),
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
            f"[quote-aware] requires {EXPECTED_DAYS[VAL_BLOCK]} {VAL_BLOCK} days, "
            f"got {len(days_val)}"
        )
    if not days_val:
        raise SystemExit("[quote-aware] no validation days selected")

    (out / "contract.json").write_text(json.dumps(CONTRACT, indent=1, default=_default) + "\n")
    print(
        f"[freeze-grid] contract -> {out / 'contract.json'} "
        f"(12 views x {len(RUNG_COSTS)} rungs, before any outcome)",
        flush=True,
    )

    # 1) validation only: no late panel or quote file is touched before the freeze.
    sel_key = str(int(SELECT_COST))
    print(
        f"[blocks] validation={len(days_val)} days (late block deferred until after the freeze)",
        flush=True,
    )
    val_score_cov = score_block(out, models, days_val, args.resume, supplemental_roots)
    val_replay_cov = replay_block(out, VAL_BLOCK, days_val, args.resume, supplemental_roots)
    val_surface, val_cov = aggregate_block(out, VAL_BLOCK, days_val)
    for view in VIEWS:
        cell = val_surface[view.key]
        boot = cell["bootstrap_daily_known_usd"][f"{int(SELECT_COST)}bps"]
        print(
            f"[val@{int(SELECT_COST)}] {view.label:<16} "
            f"{cell['known_usd_per_calendar_day'][sel_key]:+8.2f} $/day "
            f"known fills={cell['known_fills']} unknown={cell['unknown_fills']} "
            f"nomatch={cell['no_match_fills']} attempts={cell['attempts']} "
            f"signals={cell['signals']} traded_days={cell['traded_days']} "
            f"p>0={boot.get('p_gt_zero')}",
            flush=True,
        )

    chosen, ranking = choose_view(val_surface)
    frozen_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    freeze = {
        "frozen_before_late_file_access": True,
        "frozen_at": frozen_at,
        "study": "alpha_quote_aware_frequency",
        "status": STATUS,
        "chosen": {
            "view_key": chosen.key,
            "label": chosen.label,
            "head": chosen.head,
            "threshold": chosen.threshold,
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
                "beside it; the full-loss lower bound is an optional guard, never the "
                "objective"
            ),
            "power_floors": None,
            "median_or_tail_gates": None,
            "cost_or_count_kill": None,
            "tie_break": "(- objective, - known_fills, view_key)",
            "ranking": ranking,
        },
        "views": [
            {
                "view_key": v.key,
                "label": v.label,
                "head": v.head,
                "threshold": v.threshold,
            }
            for v in VIEWS
        ],
        "validation_surface": val_surface,
        "model": model_report,
        "provenance": provenance_block(out, days_val, None, supplemental_roots, val_cov, None),
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
            "study": "alpha_quote_aware_frequency",
            "status": STATUS,
            "decision": decision_text(chosen, val_surface[chosen.key], None, frozen=False),
            "chosen": freeze["chosen"],
            "frozen_choice_immutable": True,
            "contract": CONTRACT,
            "model": model_report,
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

    # 2) late block: the same twelve views for transparency, the choice already frozen.
    #    This is the FIRST access to any late panel or quote file.
    days_late = block_days(args.days, LATE_BLOCK)
    if args.days is None and len(days_late) != EXPECTED_DAYS[LATE_BLOCK]:
        raise SystemExit(
            f"[quote-aware] requires {EXPECTED_DAYS[LATE_BLOCK]} {LATE_BLOCK} days, "
            f"got {len(days_late)}"
        )
    print(f"[blocks] late={len(days_late)} days (after the freeze)", flush=True)
    late_score_cov = score_block(out, models, days_late, args.resume, supplemental_roots)
    late_replay_cov = replay_block(out, LATE_BLOCK, days_late, args.resume, supplemental_roots)
    late_surface, late_cov = aggregate_block(out, LATE_BLOCK, days_late)
    for view in VIEWS:
        cell = late_surface[view.key]
        boot = cell["bootstrap_daily_known_usd"][f"{int(SELECT_COST)}bps"]
        print(
            f"[late@{int(SELECT_COST)}] {view.label:<16} "
            f"{cell['known_usd_per_calendar_day'][sel_key]:+8.2f} $/day "
            f"known fills={cell['known_fills']} unknown={cell['unknown_fills']} "
            f"nomatch={cell['no_match_fills']} attempts={cell['attempts']} "
            f"signals={cell['signals']} traded_days={cell['traded_days']} "
            f"p>0={boot.get('p_gt_zero')}",
            flush=True,
        )

    results = {
        "study": "alpha_quote_aware_frequency",
        "status": STATUS,
        "decision": decision_text(
            chosen, val_surface[chosen.key], late_surface[chosen.key], frozen=True
        ),
        "chosen": freeze["chosen"],
        "frozen_choice_immutable": True,
        "contract": CONTRACT,
        "model": model_report,
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
