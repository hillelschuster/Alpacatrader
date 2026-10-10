#!/usr/bin/env python3
"""Multi-session CARRY study on causally strong close gainers (2/3/5 TRADING sessions).

ONE question: does a LONG carry of the panel's causal open-anchored gainers, qualified by
their own session's close strength, earn positive net dollars when the position is entered
at the LAST REGULAR MINUTE at the actual ASK (IOC-limit, causal quote) and exited at a
PREDECLARED 15:30 ET BID on the 2nd/3rd/5th following TRADING session on a self-financing
3 x $250 research book?

This is NOT the one-night qA resurrection (factory/scripts/alpha_open_overnight.py) and not
the retained minute-proxy h60 repeat: the mechanism here is a multi-session overnight-to-
overnight CARRY of a close-strength signal, quote-priced on both legs, held across sessions
with no daily reset.

What is fixed and what varies (nothing else moves):
* UNIVERSE: the day's admitted names, read from the open-anchored top-gainer panel day file
  (full-PIT B-snapshot admission, score >= 0.05, px >= 1, px_et fresh at admission). "Gainer"
  is that causal within-day admission; NO previous-close feature is used anywhere, so no
  split-adjustment hazard enters the qualification (the same convention as the panel
  contract's no_previous_close_features).
* TWO fixed entry qualities, computed from the day's own SIP minute tape
  (et in [570, session_end]; the calendar's early-close session_end is honoured, so an
  after-hours print is never a "close"):
    A: close in the upper 20% of the day range (close-in-range >= 0.80) AND close above the
       session VWAP AND cumulative dollar volume >= $10M AND close >= $3.
    B: A AND the name is on its FIRST EMERGENCE day (no A qualification in the prior 10
       in-segment trading sessions) AND the day's qualifying breadth is at least its trailing
       10-session median. Both additions are observable at the close; no future or
       prior-leader status is ever used.
  A SIP minute bar stamped m covers [m, m+1), so the day aggregates read only the bars
  STRICTLY before the DECISION minute (session_end - 1 = 15:58, i.e. the last OBSERVED bar
  is stamped 15:57): the session's final 15:59 bar is still open while the intent is priced
  and its eventual 16:00 print must never reach the qualification. Panel membership is read
  the same way (admit_t <= 15:58), so a name admitted inside the last minute is not known
  at the signal and is never financed at the entry clock.
* ENTRY: last regular minute of the session (session_end, 15:59 ET normally, 12:59 on the
  seven early closes), one full observation minute after the 15:58 decision. Send clock =
  that minute's 00.000s; arrival = +250 ms. The IOC-limit
  is pre-declared from the INTENT print (ask * 1.01 rounded UP to the cent) and the quantity
  is fee-funded at the MAXIMUM reported fee rung from the LIMIT, fixed BEFORE arrival and
  never re-floored from the arrival price. At the arrival clock the latest RAW state is
  evaluated ONCE (an IOC limit cannot rest into a later print): firm/fresh valid print with
  ask <= limit and displayed depth >= q -> conditional full-touch fill at the ACTUAL ask;
  ask > limit -> modeled no-L1-match, unfilled cash; partial depth, stale-valid, invalid,
  non-regular or absent book at arrival -> execution UNKNOWN (the position is retained and
  the ticket reserved; no future print is substituted, no quantity is resized). A non-firm
  or depth-short INTENT is a KNOWN policy skip (no order is sent at all), never a fill and
  never an UNKNOWN.
* EXIT: MARKET order due at 15:30 ET on the predeclared 2nd/3rd/5th following session
  (compressed to the last regular minute on an early-close day). Fresh-quote-gated
  submission (submit at the due intent when the book is firm and fresh, otherwise hold to
  the FIRST eligible regular print at/after the due intent inside RTH), arrival +250 ms
  after the ACTUAL submission clock, priced from the latest raw state at that arrival
  (a MARKET order may rest to the first eligible print when the book is genuinely absent;
  a stale-but-valid print at arrival is UNKNOWN). The fill clock is the REAL execution
  clock actually returned by the pricing leg (arrival when fresh, the rested print's own
  timestamp when the order rested) - never an older quote's timestamp. Exit BID only: no
  best-high or any favourable later print.
* CALENDAR ADJACENCY is the trading-session calendar artifact (1066 sessions, mechanical
  session_end per session). The exit session is the Nth NEXT session of the same calendar
  segment. A protected-crossing intent (one whose hold window would run into a protected
  month or past the segment end, i.e. 2024 / 2025-01 / 2026-06..08) is removed BEFORE
  qualification, so no qualified count ever contains an intent that could not be executed
  and no selection uses future availability. An absent later tape is never dropped as cash:
  the position is RETAINED (slot and ticker blocked, ticket reserved) and its P&L stays
  UNKNOWN.
* SPLIT LEDGER: data/harvest01/base/splits.parquet (the existing Alpaca corporate-action
  ledger) changes the position QUANTITY at the actual ex-dates in (entry_day, exit_day]:
  shares multiply by new/old, and the identity is cross-checked against the tape's own
  overnight discontinuity - the tape's ratio is a PRICE ratio (open(day)/close(prev)) and
  the ledger factor is a SHARE ratio, so a balanced action shows price x shares^-1 and the
  residual to explain is ratio * (new/old): a 1:10 reverse (price x 10, shares x 0.1) and a
  2:1 forward (price x 0.5, shares x 2) both leave 1.0 and are verified. An unrecorded
  split-like discontinuity inside the hold, a recorded factor that does not explain one, a
  fractional post-event share count / cash-in-lieu, or an unverifiable rename/delisting
  makes that trade's exit UNKNOWN - never a phony fixed share count. The verified POST-action
  share count is what gets priced: the exit's displayed BID depth is tested against the
  shares that would really be sold (50 entry shares become 100 after a 2:1 forward and only
  5 after a 1:10 reverse), never against the pre-action quantity, and a fill's ``gross`` is
  the economic TOTAL wealth return (exit shares x exit BID / entry shares x entry ASK - 1,
  so a balanced split is wealth-neutral), with the raw price ratio reported separately as
  ``gross_price``.
* BOOK: 3 slots x $250 = $750, ONE position per ticker at a time, self-financing cash that
  recycles across sessions (no daily reset, no forced exit to free a held stock). Cash is
  reserved at the worst-case rung before an arrival outcome is observed, a no-match entry
  releases its reservation at its own arrival, and an UNKNOWN position stays reserved
  forever (it never frees a slot or cash at a planned time). Starting $750 margin cash reuse
  is assumed as a research convention; no old PDT / $25k floor is imposed and none is
  verified.
* COSTS: the traded prices ARE the observed NBBO ASK (entry) and BID (exit), so the spread
  is already in the measured cash flows and is reported separately (mean touch spread per
  leg). On top of that, separate provider fee scenarios 0/5/10/25/50/75/100/125/150 bps
  ROUND TRIP (halved per leg) are replayed on the same legs; the 25 bps scenario is the
  primary conservative comparison and a 0/low fee scenario is supported.
* SELECTION: 2023 validation net cash flow per full calendar day at 25 bps over the six
  (quality x hold) views, deterministic argmax, tie-break (- objective, - known fills,
  view key), no power floors, no median gates, no count or cost kill thresholds. The choice
  is frozen to disk AFTER the validation surface and BEFORE any late file is read. The
  previously-explored 2025-02..2026-05 block then runs the frozen view (and all six for
  transparency) at every fee rung. A >= 6 month 2021-05..2021-10 development window is
  replayed first for context only and is never used for selection.
* ACCOUNTING BASIS: dollars land on their ACTUAL action dates - the entry day's outlay and
  the exit day's proceeds. UNKNOWN executions are excluded from the known numerator and
  reported beside it; the full-loss lower bound is an optional guard, never the objective,
  and no expected value is ever computed across unknowns. ROI/CAGR are reported only for
  the self-financing KNOWN-only flow and are labelled as such.

Quotes measure the touch cost of a hypothetical order: they are not exchange fills, never a
fill guarantee, and displayed top-of-book depth is the only capacity evidence (hidden
liquidity and the deeper book are unmeasured). No live order, no broker call, no account
write, and no protected outcome (2024, 2025-01, 2026-06..08) is ever touched. This is
research evidence, not a validated strategy.

Data reality disclosed up front: the ranked SIP quote cache misses a material share of
ticker streams on qualified sessions (~46% of the 2023 and ~48% of the 2025-02..2026-05
qualified states by the parent's measurement; raw trades exist for names the quote cache
never acquired, banks included). Every such leg is an execution UNKNOWN, the position is
retained and the ticket stays reserved, and the leg is written to
``missing_legs/<block>.json`` for the read-only ``--fetch-missing`` re-acquisition. The
manifest is PIT-complete for qualified names: a missing ENTRY leg is recorded before any
funding decision, so no future outcome filter can shrink the requested set.

Usage:
  uv run --no-sync python factory/scripts/alpha_multi_session_carry.py
      # dev + validation -> freeze -> late; full quote corpus, per-day atomic parts
  uv run --no-sync python factory/scripts/alpha_multi_session_carry.py --resume
      # resume every phase from the per-day artifacts already on disk
  uv run --no-sync python factory/scripts/alpha_multi_session_carry.py --days 2023-05-15 2023-05-16
      # smoke subset (debug only; the expected-day counts are waived)
  uv run --no-sync python factory/scripts/alpha_multi_session_carry.py --skip-late
      # stop after the frozen selection (validation only)
  uv run --no-sync python factory/scripts/alpha_multi_session_carry.py --print-missing
      # print the missing-tape manifest (nothing is fetched)
  uv run --no-sync python factory/scripts/alpha_multi_session_carry.py --fetch-missing
      # READ-ONLY Alpaca REST re-acquisition of the manifest legs into <out>/fetch_cache
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))

from alpha_open_learned import PANEL_ROOT, bootstrap_daily
from alpha_open_overnight import SPLIT_HI, SPLIT_LO, load_actions, split_like
from alpha_open_panel import ROOT, allowed
from alpha_quote_aware_frequency import (
    MAX_RUNG_COST,
    arrival_us,
    causal_quantity,
    entry_price_leg,
    limit_price_of,
    minute_us,
    priced_leg,
    quote_day_frame,
    reserved_usd,
    submission_clock,
    symbol_quotes,
)
from alpha_sparse_execution_frontier import fetch_missing_legs

# ----- fixed configuration (no HPO, no grid search, no refit) -----------------
HOLDS = (2, 3, 5)  # the only carry horizons: sessions held after the entry session
QUALS = ("A", "B")  # the two fixed entry qualities
FEES = (0.0, 5.0, 10.0, 25.0, 50.0, 75.0, 100.0, 125.0, 150.0)  # round-trip bps, per leg / 2
SELECT_FEE = 25.0  # primary selection rung (conservative comparison)
ORDER_BUDGET = 250.0  # research ticket (book size only, never a capacity claim)
MAX_SLOTS = 3  # concurrent positions across sessions
BOOK = MAX_SLOTS * ORDER_BUDGET  # $750 nominal research sub-book
LATENCY_US = 250_000  # order arrival latency after the send clock
MAX_AGE_S = 2.0  # operational freshness gate (NOT an NBBO-expiry claim)
CTR_MIN = 0.80  # close must sit in the upper 20% of the day range
VWAP_MIN_DIST = 0.0  # close strictly above the session VWAP
CUM_DV_MIN = 10_000_000.0  # causal session dollar volume
PRICE_MIN = 3.0  # session close price
EMERGENCE_LOOKBACK = 10  # sessions of A history behind the first-emergence test
EXIT_MINUTE = 930  # 15:30 ET predeclared exit (bounded by the session's last minute)
ANNUAL_SESSIONS = 252  # simple session-count convention, NOT a CAGR claim
BOOT_N = 1000
BOOT_SEED = 20261009
DEV_WINDOW = ("2021-05-01", "2021-10-31")  # >= 6 month development context (never selected on)
VAL_WINDOW = ("2023-01-01", "2023-12-31")
LATE_WINDOW = ("2025-02-01", "2026-05-31")
LOOKBACK_BUILD_FROM = "2021-04-01"  # candidate-build start covering every block's lookback
BLOCK_WINDOWS = {
    "dev": DEV_WINDOW,
    "validation": VAL_WINDOW,
    "confirmation": LATE_WINDOW,
}
EXPECTED_DAYS = {"validation": 250, "confirmation": 332}
MIN_DEV_DAYS = 120  # the >= 6 month dev claim needs at least this many sessions
PROTECTED_MONTHS = ("2024", "2025-01", "2026-06", "2026-07", "2026-08")
STATUS = "DISCOVERY-NOT-VALIDATED"
VERSION = 1
CAND_SCHEMA = 1
QUAL_SCHEMA = 1
REPLAY_SCHEMA = 1

DATA_ROOT = ROOT / "data"
PANEL_DAYS = PANEL_ROOT / "days"
CALENDAR = ROOT / "factory" / "artifacts" / "basket" / "sip" / "phase2_session_calendar.json"
BARS_ROOT = DATA_ROOT / "sip" / "net" / "bars"
QUOTE_CACHE_ROOT = DATA_ROOT / "sip" / "net" / "quotes"
SPLITS = DATA_ROOT / "harvest01" / "base" / "splits.parquet"
OUTPUT = PANEL_ROOT / "multi_session_carry"
# Existing read-only fetch caches, merged as supplements (originals always win). A root
# that does not exist yet is simply skipped, so the neutral ALL-PIT acquisition root owned
# by TouchHourlyPayoffLearning is picked up automatically the moment it lands.
SUPPLEMENT_ROOTS = (
    PANEL_ROOT / "sparse_execution_frontier" / "fetched_quotes" / "quotes",
    PANEL_ROOT / "quote_completeness_audit" / "quotes",
    PANEL_ROOT / "quote_completeness_audit" / "fetched_quotes" / "quotes",
    PANEL_ROOT / "quote_universe_acquire" / "quotes",
)
FETCH_CACHE_NAME = "fetch_cache"  # <out>/fetch_cache/quotes/<day>.parquet, this study's own
ENV_PATH = ROOT / ".env"


@dataclass(frozen=True)
class View:
    """One (quality, hold) cell. Same universe, same execution policy; only these differ."""

    qual: str
    hold: int

    @property
    def key(self) -> str:
        return f"{self.qual}h{self.hold}"

    @property
    def label(self) -> str:
        return f"qual{self.qual}_hold{self.hold}"


VIEWS = tuple(View(q, h) for q in QUALS for h in HOLDS)
VIEWS_BY_KEY = {v.key: v for v in VIEWS}
FEE_KEYS = tuple(str(int(f)) for f in FEES)


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


def write_parquet_atomic(path: Path, frame: pl.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    frame.write_parquet(tmp)
    os.replace(tmp, path)


def guard_day(day: str) -> None:
    """Refuse any session outside the traversable, non-protected research windows."""
    if not allowed(day):
        raise SystemExit(f"[multi-session-carry] protected/out-of-scope session refused: {day}")
    if day[:7] in PROTECTED_MONTHS or day[:4] == "2024":
        raise SystemExit(f"[multi-session-carry] protected month refused: {day}")


def outlay_usd(quantity: int, ask: float, fee_bps: float) -> float:
    return float(quantity) * float(ask) * (1.0 + fee_bps / 20_000.0)


def proceeds_usd(quantity: float, bid: float, fee_bps: float) -> float:
    return float(quantity) * float(bid) * (1.0 - fee_bps / 20_000.0)


def net_round_trip(quantity: int, exit_bid: float, entry_ask: float, fee_bps: float) -> float:
    return proceeds_usd(quantity, exit_bid, fee_bps) - outlay_usd(quantity, entry_ask, fee_bps)


# ----- the frozen contract (written before any outcome is read) ---------------
CONTRACT = {
    "study": "alpha_multi_session_carry",
    "version": VERSION,
    "status": STATUS,
    "question": (
        "long carry of causally strong close gainers over the next 2/3/5 TRADING sessions, "
        "entered at the last regular minute at the actual ASK and exited at a predeclared "
        "15:30 ET BID, on a self-financing 3 x $250 research book"
    ),
    "universe": {
        "source": "open-anchored top-gainer panel day files (causal B-snapshot admission)",
        "admission": "full-PIT SIP B snapshot top10, score >= 0.05, px >= 1, px_et >= T-2",
        "gainer_basis": (
            "causal within-day admission; NO previous-close feature is used anywhere, so "
            "no split-adjustment hazard enters the qualification"
        ),
        "panel_days_root": str(PANEL_DAYS),
    },
    "day_aggregates": {
        "tape": "data/sip/net/bars/<day>.parquet, et in [570, session_end]",
        "session_end": "calendar artifact (959 normally, 779 on the seven early closes)",
        "decision_minute": "session_end - 1 (15:58): the last fully observable close",
        "observed_bars": "et in [570, decision_minute): a bar stamped m covers [m, m+1), so "
        "the last OBSERVED bar is stamped 15:57 and closes at the 15:58 decision",
        "entry_minute": "session_end (15:59), one full observation minute after the "
        "decision, so the session's final - still open - 15:59 bar can never leak its "
        "16:00 print into the qualification or the IOC intent",
        "close": "the last OBSERVED bar's close (the decision-minute price), never the "
        "session's final bar close",
        "high_low": "max high / min low of the observed bars strictly before the decision",
        "vwap": "sum(close*volume)/sum(volume) over the observed bars",
        "cum_dv": "sum(close*volume) over the observed bars",
        "membership": "panel admission observable at the decision minute (admit_t <= "
        "session_end - 1): a name admitted in the session's last minute is not known at "
        "the signal and is never financed at the entry clock",
    },
    "qualities": {
        "A": (
            "close_in_range >= 0.80 AND close > session VWAP AND cum_dv >= 10_000_000 AND "
            "close >= 3"
        ),
        "B": (
            "A AND first emergence (no A qualification in the prior "
            f"{EMERGENCE_LOOKBACK} in-segment sessions) AND breadth strength (today's "
            "qualifying count >= the trailing in-segment median of the same count)"
        ),
        "observable_only": True,
        "future_prior_leader_status_used": False,
    },
    "calendar": {
        "artifact": str(CALENDAR),
        "sessions": 1066,
        "adjacency": "the Nth next session of the same calendar segment",
        "segments": "2021-02-01..2023-12-29 and 2025-02-03..2026-05-29",
        "protected_crossing_removal": (
            "an intent whose hold window would cross a protected month or the segment end "
            "is removed BEFORE qualification (no future availability selection)"
        ),
        "protected_unread": list(PROTECTED_MONTHS) + ["2024"],
    },
    "entry": {
        "send_clock": "last regular minute of the entry session at 00.000s ET",
        "arrival_clock": "send + 250ms",
        "intent_observation": "latest RAW NBBO state at the send clock (never pre-filtered)",
        "firm_fresh_rule": "valid, un-crossed, R-only, age <= 2s",
        "limit_price": (
            "ceil_to_cent(intent_ask * 1.01): pre-declared marketability cap, NOT charged friction"
        ),
        "quantity": "floor(250 / (limit * (1 + 150bps/2))): fee-funded at the MAXIMUM rung",
        "quantity_rule": (
            "fixed BEFORE arrival from the INTENT limit; never re-floored from the arrival price"
        ),
        "depth_rule": "q >= 1 and q <= intent ASK top-of-book depth",
        "arrival_rule": (
            "IOC LIMIT: the latest RAW state is evaluated ONCE at the arrival clock. "
            "firm/fresh + ask <= limit + depth >= q -> conditional full-touch fill at the "
            "ACTUAL ask; ask > limit -> modeled no-L1-match, unfilled cash; partial depth / "
            "stale-valid / invalid / non-regular / absent book -> execution UNKNOWN "
            "(position retained, ticket reserved): no later print is substituted and the "
            "quantity is never resized, because an IOC limit cannot rest"
        ),
        "known_skip_rule": (
            "a non-firm or depth-short INTENT, or q == 0, is a KNOWN policy skip: no order "
            "is sent, cash stays unfilled, and it is never a priced zero-return fill"
        ),
        "fill_clock": "the arrival clock itself (an IOC cannot rest into a future print)",
    },
    "exit": {
        "due": (
            "15:30 ET on the predeclared Nth next session (bounded by the session's last "
            "regular minute)"
        ),
        "submission": (
            "FRESH-QUOTE-GATED MARKET: submit at the due intent when the latest raw state is "
            "firm and fresh, else hold to the FIRST eligible regular print at/after the due "
            "intent inside RTH"
        ),
        "arrival_clock": "actual submission + 250ms",
        "pricing": (
            "latest raw state at the arrival clock; stale-but-valid -> UNKNOWN; invalid / "
            "non-regular / absent book -> the working MARKET order rests to the first "
            "eligible print at/after arrival (never a later favourable print); nothing "
            "before the session close -> UNKNOWN"
        ),
        "fill_clock": (
            "the REAL execution clock returned by the pricing leg: the arrival clock when "
            "fresh, the rested print's own timestamp when the order rested; a quote's own "
            "timestamp is never used as the fill clock when it predates the arrival"
        ),
        "price_basis": "BID only; no best-high and no favourable later print",
        "depth_rule": (
            "the verified POST-action exit BID top-of-book must carry the POST-action "
            "share count; a shortfall is an UNKNOWN partial capacity, and an unverified / "
            "fractional share count is an UNKNOWN with no original-quantity fallback"
        ),
        "gross_definition": (
            "economic TOTAL wealth return over the hold: post-action exit shares x exit BID "
            "/ (entry shares x entry ASK) - 1, so a balanced split is wealth-neutral; the "
            "raw price ratio is reported separately as gross_price"
        ),
    },
    "split_ledger": {
        "source": str(SPLITS),
        "window": "(entry_day, exit_day]",
        "quantity_rule": (
            "shares multiply by new/old at each recorded ex-date; a fractional post-event "
            "share count (cash-in-lieu), an unverified rename/delisting, an unrecorded "
            "split-like overnight discontinuity inside the hold, or a recorded factor that "
            "does not explain one makes the exit UNKNOWN"
        ),
        "identity_check": (
            "the tape's overnight ratio is a PRICE ratio and the ledger factor a SHARE "
            "ratio, so the unexplained part is ratio * (new/old): a balanced 1:10 reverse "
            "or 2:1 forward leaves 1.0 and is verified, while an unexplained discontinuity "
            "is UNKNOWN"
        ),
        "depth_basis": (
            "the verified POST-action share count: the exit BID depth must carry the "
            "shares that would really be sold, never the pre-action entry quantity"
        ),
        "split_like_band": [SPLIT_LO, SPLIT_HI],
        "phony_fixed_shares": False,
    },
    "book": {
        "slots": MAX_SLOTS,
        "ticket_usd": ORDER_BUDGET,
        "book_usd": BOOK,
        "leverage": False,
        "one_position_per_ticker": True,
        "self_financing": "cash recycles across sessions; no daily reset, no forced exit",
        "cash_reservation": "the causal worst-case rung-150 outlay q * limit * (1 + 150bps/2)",
        "simultaneous_clock_rule": (
            "the day's qualified intents are funded in the frozen rank order BEFORE any "
            "arrival outcome is observed; no same-clock substitution after an unfilled intent"
        ),
        "unknown_rule": (
            "an UNKNOWN execution keeps its slot, blocks its ticker and keeps its "
            "reservation: it is retained, never dropped as cash, never freed at a planned "
            "time and never re-priced from later tapes"
        ),
        "pdt": (
            "starting $750 margin cash reuse assumed as a research convention; no old "
            "PDT / $25k floor imposed or verified"
        ),
    },
    "rank_key": ["-close_in_range", "-vwap_dist", "-cum_dv", "ticker asc"],
    "costs": {
        "observed_spread": "already inside the traded ASK/BID prices; reported separately per leg",
        "fee_scenarios_bps_round_trip": list(FEES),
        "fee_convention": "round-trip bps, halved per leg",
        "select_fee_bps": SELECT_FEE,
        "max_fee_bps": MAX_RUNG_COST,
        "no_fixed_hurdle": True,
    },
    "selection": {
        "objective": "2023 validation net cash flow per full calendar day at 25 bps",
        "basis": (
            "PARTIAL: unknown executions are excluded from the numerator and reported "
            "beside them; the full-loss lower bound is an optional guard, never the "
            "objective, and no expected value is computed across unknowns"
        ),
        "power_floors": None,
        "median_or_tail_gates": None,
        "cost_or_count_kill": None,
        "tie_break": "(- objective, - known_fills, view_key)",
        "frozen_before_late_inspection": True,
        "freeze_artifact": "selection_freeze.json",
    },
    "blocks": {
        "dev": {"window": list(DEV_WINDOW), "role": "context only, never selected on"},
        "validation": {"window": list(VAL_WINDOW)},
        "confirmation": {
            "window": list(LATE_WINDOW),
            "note": "previously explored market periods, NOT pristine holdout",
        },
    },
    "annualization": (
        f"mean daily known contribution x {ANNUAL_SESSIONS} sessions on a ${int(BOOK)} "
        "nominal book: a simple session-count convention, NOT a CAGR and NOT an account claim"
    ),
    "no_live_orders": True,
    "coverage_expectation": (
        "the ranked SIP quote cache misses a material share of ticker streams on "
        "qualified sessions (the parent's measurement: ~45.8% of 2023 and ~48.5% of "
        "2025-02..2026-05 qualified states have no resident ticker stream; a 17-name "
        "same-day sample had raw trades for all 17 but quotes for only 8, banks "
        "included). Every such leg is an execution UNKNOWN (never a cash zero, never a "
        "drop, never an ex-post entry exclusion), the position is retained, and the leg "
        "is listed in missing_legs/<block>.json for the read-only --fetch-missing "
        "re-acquisition. The manifest is PIT-complete for qualified names: a missing "
        "ENTRY leg is recorded before any funding decision, so no future outcome filter "
        "can shrink the requested set."
    ),
    "reused_helpers": [
        "alpha_quote_aware_frequency: quote_day_frame / symbol_quotes / minute_us / "
        "arrival_us / submission_clock / priced_leg (exit MARKET leg only)",
        "alpha_open_overnight.load_actions: the existing corporate-action ledger reader",
        "alpha_open_learned.bootstrap_daily: the shared day-level bootstrap",
        "alpha_sparse_execution_frontier.fetch_missing_legs: read-only Alpaca REST fetch",
    ],
    "not_reused": (
        "the entry leg is NOT priced with the MARKET-style priced_leg helper: an IOC limit "
        "is evaluated once at its arrival clock inside this producer (explicit, tested)"
    ),
}


# ----- calendar ---------------------------------------------------------------
_SESSIONS: dict[str, int] | None = None


def load_sessions() -> dict[str, int]:
    """Every traversable trading session and its last regular minute (calendar artifact)."""
    global _SESSIONS
    if _SESSIONS is not None:
        return _SESSIONS
    artifact = json.loads(CALENDAR.read_text())
    evidence = artifact["evidence"]
    sessions: dict[str, int] = {}
    for day in sorted(evidence):
        guard_day(day)
        sessions[day] = int(evidence[day]["session_end"])
    _SESSIONS = sessions
    return sessions


def _months_between(a: str, b: str) -> set[str]:
    ya, ma = int(a[:4]), int(a[5:7])
    yb, mb = int(b[:4]), int(b[5:7])
    out = set()
    while (ya, ma) <= (yb, mb):
        out.add(f"{ya:04d}-{ma:02d}")
        ma += 1
        if ma == 13:
            ya, ma = ya + 1, 1
    return out


def protected_between(a: str, b: str) -> bool:
    """True when any protected month lies strictly between sessions a and b (a < b)."""
    if a >= b:
        return False
    return bool(_months_between(a, b) & set(PROTECTED_MONTHS) | ({"2024"} & _months_between(a, b)))


def build_segments(sessions: dict[str, int]) -> list[list[str]]:
    """Maximal runs of sessions that are true calendar neighbours with no protected month."""
    days = sorted(sessions)
    if not days:
        return []
    segments: list[list[str]] = []
    current = [days[0]]
    for prev, day in zip(days, days[1:], strict=False):  # the intentional one-offset walk
        gap = (_date.fromisoformat(day) - _date.fromisoformat(prev)).days
        if protected_between(prev, day):
            segments.append(current)
            current = [day]
            continue
        if gap > 4:
            raise SystemExit(
                f"[multi-session-carry] unexplained session gap {prev} -> {day} "
                f"({gap} calendar days): the calendar artifact is expected to be complete"
            )
        current.append(day)
    segments.append(current)
    return segments


@dataclass(frozen=True)
class SegmentMap:
    """Session -> (segment index, position) plus the exit-session lookup."""

    segments: tuple[tuple[str, ...], ...]
    _index: dict[str, tuple[int, int]]

    @classmethod
    def build(cls, sessions: dict[str, int]) -> SegmentMap:
        segments = build_segments(sessions)
        index: dict[str, tuple[int, int]] = {}
        for si, seg in enumerate(segments):
            for pos, day in enumerate(seg):
                index[day] = (si, pos)
        return cls(tuple(tuple(s) for s in segments), index)

    def segment_of(self, day: str) -> int:
        return self._index[day][0]

    def next_nth(self, day: str, n: int) -> str | None:
        """The Nth NEXT session in the same segment, or None when it does not exist."""
        si, pos = self._index[day]
        seg = self.segments[si]
        j = pos + int(n)
        return seg[j] if j < len(seg) else None

    def prior_n(self, day: str, n: int) -> list[str]:
        """The up-to-n prior sessions of the same segment, most recent last."""
        si, pos = self._index[day]
        seg = self.segments[si]
        lo = max(0, pos - int(n))
        return list(seg[lo:pos])


def block_days(sessions: dict[str, int], block: str, days: list[str] | None = None) -> list[str]:
    lo, hi = BLOCK_WINDOWS[block]
    keep = set(days) if days else None
    out = []
    for day in sorted(sessions):
        if not (lo <= day <= hi):
            continue
        if keep is not None and day not in keep:
            continue
        out.append(day)
    return out


# ----- phase 1: per-day candidates (tape aggregates + the quality-A flag) -----
CAND_COLUMNS = (
    "day",
    "ticker",
    "admit_t",
    "admit_gain",
    "session_end",
    "decision_minute",
    "last_observed_bar",
    "entry_minute",
    "last_et",
    "n_bars",
    "close",
    "high",
    "low",
    "vwap",
    "cum_dv",
    "ctr",
    "vwap_dist",
    "quality_A",
)
CAND_TYPES = {
    "day": pl.String,
    "ticker": pl.String,
    "admit_t": pl.Int64,
    "admit_gain": pl.Float64,
    "session_end": pl.Int64,
    "decision_minute": pl.Int64,
    "last_observed_bar": pl.Int64,
    "entry_minute": pl.Int64,
    "last_et": pl.Int64,
    "n_bars": pl.Int64,
    "close": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "vwap": pl.Float64,
    "cum_dv": pl.Float64,
    "ctr": pl.Float64,
    "vwap_dist": pl.Float64,
    "quality_A": pl.Boolean,
}


def empty_candidates() -> pl.DataFrame:
    return pl.DataFrame(schema={c: CAND_TYPES[c] for c in CAND_COLUMNS})


def cand_paths(out_dir: Path, day: str) -> tuple[Path, Path]:
    return out_dir / "candidates" / f"{day}.parquet", out_dir / "candidates" / f"{day}.cov.json"


def quality_a(ctr: float, vwap_dist: float, cum_dv: float, close: float) -> bool:
    """The fixed quality-A flag: close strength, VWAP support, liquidity and price.

    ``ctr`` is the close's position inside the day range, ``vwap_dist`` the close's
    distance above the session VWAP (strictly positive required), ``cum_dv`` the session
    dollar volume and ``close`` the session close price. A pure function of one session's
    own OBSERVED tape (the bars strictly before the decision minute) - never of a future
    session and never of the session's final, still-open bar.
    """
    return bool(
        ctr >= CTR_MIN and vwap_dist > VWAP_MIN_DIST and cum_dv >= CUM_DV_MIN and close >= PRICE_MIN
    )


def candidate_rows(day: str, sessions: dict[str, int]) -> tuple[list[dict], dict]:
    """One session's decision-known names with tape-derived aggregates and the A flag.

    Bars only, and only the bars strictly before the DECISION minute (``session_end - 1``,
    15:58 normally): the close, range, VWAP and cumulative dollar volume are computed from
    that observed prefix, so ``close`` is the last OBSERVED bar's close (the 15:58 price
    from the bar stamped 15:57), an after-hours print is never a close, and the session's
    final 15:59 bar - still open while the IOC intent is priced - can never leak its
    eventual 16:00 print into the qualification. The entry is the last regular minute
    (15:59), one full observation minute after the decision. Quality A is a pure function
    of this session's own observed tape.
    """
    guard_day(day)
    session_end = sessions[day]
    panel_path = PANEL_DAYS / f"{day}.parquet"
    bars_path = BARS_ROOT / f"{day}.parquet"
    if not panel_path.exists():
        raise SystemExit(f"[multi-session-carry] panel day missing: {panel_path}")
    if not bars_path.exists():
        raise SystemExit(f"[multi-session-carry] tape day missing: {bars_path}")
    panel = pl.read_parquet(panel_path)
    first: dict[str, tuple[int, float]] = {}
    for r in panel.iter_rows(named=True):
        first.setdefault(str(r["ticker"]), (int(r["admit_t"]), float(r["admit_gain"])))
    # The qualification DECISION clock is session_end - 1 (15:58 normally): the entry is
    # the last regular minute (15:59), sent one full observation minute after the decision,
    # so the decision may only use bars that START strictly before it - a bar stamped m
    # covers [m, m+1), so both the 15:58 and the 15:59 bar (whose OHLC is not even complete
    # yet at the decision clock) are excluded, and the still-open 15:59 bar's eventual
    # 16:00 close can never leak into the 15:59 IOC intent either. Membership is read the
    # same way: a name admitted after the decision minute is not known at the signal and
    # is never a candidate, so no later-admitted name can be financed at the entry clock.
    decision = int(session_end) - 1
    admit = {t: v for t, v in first.items() if v[0] <= decision}
    admitted_after_decision = sorted(t for t, v in first.items() if v[0] > decision)
    tape = (
        pl.scan_parquet(bars_path)
        .filter(pl.col("ticker").is_in(sorted(admit)))
        .filter(pl.col("et") >= 570)
        .filter(pl.col("et") < decision)
        .collect()
        .sort("ticker", "et")
    )
    rows: list[dict] = []
    missing = sorted(set(admit) - set(tape["ticker"].to_list()))
    for (ticker,), f in tape.partition_by("ticker", as_dict=True).items():
        et = f["et"].to_numpy()
        o, h, low, c, v = (
            f[x].to_numpy().astype(float) for x in ("open", "high", "low", "close", "volume")
        )
        if len(et) == 0 or et[0] != 570:
            continue
        if not np.all(np.isfinite(np.column_stack([o, h, low, c, v]))) or np.any(c <= 0):
            raise SystemExit(f"[multi-session-carry] nonfinite/nonpositive tape: {day} {ticker}")
        # the last OBSERVED bar of the known prefix: its close is the decision-minute
        # price, and its own stamp is the newest information the decision may use
        close = float(c[-1])
        last_observed_bar = int(et[-1])
        high = float(h.max())
        low_ = float(low.min())
        dv = float((c * v).sum())
        vol = float(v.sum())
        if not (np.isfinite(close) and close > 0 and np.isfinite(dv) and vol > 0):
            continue
        vwap = dv / vol
        ctr = (close - low_) / (high - low_) if high > low_ else 0.0
        vwap_dist = close / vwap - 1.0
        quality = quality_a(ctr, vwap_dist, dv, close)
        admit_t, admit_gain = admit[ticker]
        rows.append(
            {
                "day": day,
                "ticker": str(ticker),
                "admit_t": admit_t,
                "admit_gain": admit_gain,
                "session_end": int(session_end),
                "decision_minute": int(decision),
                "last_observed_bar": last_observed_bar,
                "entry_minute": int(session_end),
                "last_et": int(et[-1]),
                "n_bars": int(len(et)),
                "close": close,
                "high": high,
                "low": low_,
                "vwap": vwap,
                "cum_dv": dv,
                "ctr": float(ctr),
                "vwap_dist": float(vwap_dist),
                "quality_A": quality,
            }
        )
    cov = {
        "admitted": len(admit),
        "candidates": len(rows),
        "missing_admitted_tapes": missing,
        "qualified_A": int(sum(r["quality_A"] for r in rows)),
        "session_end": int(session_end),
        "decision_minute": int(decision),
        "admitted_after_decision": admitted_after_decision,
    }
    return rows, cov


def candidate_block(
    out_dir: Path,
    days: list[str],
    resume: bool,
    sessions: dict[str, int],
    producer: str,
    contract_sha: str,
) -> dict:
    """Build (or resume) every session's candidate part atomically, one day at a time."""
    todo, infos = [], []
    coverage = {
        "days_total": len(days),
        "days_cached": 0,
        "days_built": 0,
        "candidates": 0,
        "qualified_A": 0,
        "missing_admitted_tape_days": 0,
    }
    for day in days:
        path, cpath = cand_paths(out_dir, day)
        expect = _digest_bytes(
            {
                "day": day,
                "producer": producer,
                "contract": contract_sha,
                "panel": sha256_file(PANEL_DAYS / f"{day}.parquet"),
                "bars": sha256_file(BARS_ROOT / f"{day}.parquet"),
                "calendar_session_end": sessions[day],
                "schema": CAND_SCHEMA,
            }
        )
        fresh = False
        if resume and path.exists() and cpath.exists():
            try:
                fresh = json.loads(cpath.read_text()).get("resume_hash") == expect
            except (json.JSONDecodeError, OSError):
                fresh = False
        if fresh:
            infos.append(
                {
                    "day": day,
                    "resumed": True,
                    "cov": json.loads(cpath.read_text()).get("coverage", {}),
                }
            )
        else:
            todo.append((day, expect))
    for n, (day, expect) in enumerate(todo, 1):
        rows, cov = candidate_rows(day, sessions)
        path, cpath = cand_paths(out_dir, day)
        frame = (
            pl.DataFrame(rows, schema={c: CAND_TYPES[c] for c in CAND_COLUMNS})
            if rows
            else empty_candidates()
        )
        write_parquet_atomic(path, frame)
        write_json_atomic(
            cpath,
            {
                "day": day,
                "resume_hash": expect,
                "producer_sha256": producer,
                "contract_sha256": contract_sha,
                "schema": CAND_SCHEMA,
                "coverage": cov,
            },
        )
        infos.append({"day": day, "resumed": False, "cov": cov})
        if n % 50 == 0 or n == len(todo):
            print(
                f"[candidates] {n}/{len(todo)} days, last {day}, "
                f"A-qualified today {cov['qualified_A']}",
                flush=True,
            )
    for info in infos:
        cov = info["cov"]
        coverage["candidates"] += int(cov.get("candidates", 0))
        coverage["qualified_A"] += int(cov.get("qualified_A", 0))
        coverage["missing_admitted_tape_days"] += int(bool(cov.get("missing_admitted_tapes")))
    coverage.update({"days_cached": len(infos) - len(todo), "days_built": len(todo)})
    return coverage


def read_candidate_day(out_dir: Path, day: str) -> pl.DataFrame:
    path, _ = cand_paths(out_dir, day)
    if not path.exists():
        raise SystemExit(
            f"[multi-session-carry] candidate part missing for {day} ({path}); run the "
            "candidate phase for the whole lookback + block before qualifying it"
        )
    return pl.read_parquet(path)


# ----- phase 2: qualification (protected-crossing removal, then the flags) -----
QUAL_COLUMNS = (
    "day",
    "ticker",
    "view_key",
    "qual",
    "hold",
    "exit_day",
    "ctr",
    "vwap_dist",
    "cum_dv",
    "close",
)
QUAL_TYPES = {
    "day": pl.String,
    "ticker": pl.String,
    "view_key": pl.String,
    "qual": pl.String,
    "hold": pl.Int64,
    "exit_day": pl.String,
    "ctr": pl.Float64,
    "vwap_dist": pl.Float64,
    "cum_dv": pl.Float64,
    "close": pl.Float64,
}


def empty_qual() -> pl.DataFrame:
    return pl.DataFrame(schema={c: QUAL_TYPES[c] for c in QUAL_COLUMNS})


def qual_path(out_dir: Path, block: str) -> Path:
    return out_dir / "qual" / f"{block}.parquet"


def a_flags_by_day(out_dir: Path, days: list[str]) -> dict[str, set[str]]:
    """The hold-independent quality-A ticker set of each needed session."""
    flags: dict[str, set[str]] = {}
    for day in days:
        frame = read_candidate_day(out_dir, day).filter(pl.col("quality_A"))
        flags[day] = set(frame["ticker"].to_list())
    return flags


def build_qual_block(
    out_dir: Path,
    block: str,
    days: list[str],
    segmap: SegmentMap,
    resume: bool,
    producer: str,
    contract_sha: str,
) -> pl.DataFrame:
    """One block's qualified, executable intents for every view.

    The protected-crossing removal happens FIRST (an intent whose Nth next session does
    not exist inside the segment is dropped before any flag is counted), then the quality
    flags, the first-emergence test and the breadth test are evaluated on the surviving
    set - so no reported qualified count ever contains an intent that could not be taken.
    """
    if not days:
        write_parquet_atomic(qual_path(out_dir, block), empty_qual())
        return empty_qual()
    path = qual_path(out_dir, block)
    lookback = segmap.prior_n(days[0], EMERGENCE_LOOKBACK)
    read_days = sorted(set(days) | set(lookback))
    flags = a_flags_by_day(out_dir, read_days)
    per_view_counts = {v.key: 0 for v in VIEWS}
    rows: list[dict] = []
    for day in days:
        prior = segmap.prior_n(day, EMERGENCE_LOOKBACK)
        day_flags = flags[day]
        for view in VIEWS:
            exit_day = segmap.next_nth(day, view.hold)
            executable = day_flags if exit_day is not None else set()
            prior_counts = [
                len(flags[pd]) if segmap.next_nth(pd, view.hold) is not None else 0 for pd in prior
            ]
            breadth_ok = (
                bool(len(executable) >= float(np.median(prior_counts))) if prior_counts else True
            )
            if view.qual == "A":
                chosen = sorted(executable)
            else:
                chosen = [
                    t
                    for t in sorted(executable)
                    if not any(t in flags[pd] for pd in prior) and breadth_ok
                ]
            per_view_counts[view.key] += len(chosen)
            for ticker in chosen:
                rows.append(
                    {
                        "day": day,
                        "ticker": ticker,
                        "view_key": view.key,
                        "qual": view.qual,
                        "hold": int(view.hold),
                        "exit_day": exit_day,
                        "ctr": None,
                        "vwap_dist": None,
                        "cum_dv": None,
                        "close": None,
                    }
                )
    frame = pl.DataFrame(rows, schema={c: QUAL_TYPES[c] for c in QUAL_COLUMNS})
    # the rank keys are read back from the candidate parts (never recomputed here)
    parts = [read_candidate_day(out_dir, day) for day in days]
    ranks = pl.concat(parts, how="vertical").select(
        ["day", "ticker", "ctr", "vwap_dist", "cum_dv", "close"]
    )
    frame = (
        frame.drop(["ctr", "vwap_dist", "cum_dv", "close"])
        .join(ranks, on=["day", "ticker"], how="left")
        .select(QUAL_COLUMNS)
    )
    if frame.height and frame.filter(pl.col("ctr").is_null()).height:
        raise SystemExit("[multi-session-carry] qualified row lost its rank keys")
    write_parquet_atomic(path, frame)
    write_json_atomic(
        out_dir / "qual" / f"{block}.cov.json",
        {
            "block": block,
            "days": len(days),
            "rows": int(frame.height),
            "qualified_per_view": per_view_counts,
            "producer_sha256": producer,
            "contract_sha256": contract_sha,
            "schema": QUAL_SCHEMA,
            "resume": bool(resume),
        },
    )
    print(
        f"[qual:{block}] {len(days)} days -> {frame.height} qualified intents over "
        f"{len(VIEWS)} views ("
        + ", ".join(f"{v.key}={per_view_counts[v.key]}" for v in VIEWS)
        + ")",
        flush=True,
    )
    return frame


# ----- phase 3: leg resolution (explicit IOC entry, MARKET exit, split ledger) --
def resolve_intent(sq, send_us: int) -> dict:
    """The causal intent observation at the send clock: the limit, the quantity or a KNOWN skip.

    An IOC limit must carry a price, so a non-firm, non-fresh or depth-short intent never
    becomes an order at all: it is a KNOWN policy skip with the cash simply unfilled.
    """
    out = {
        "intent_us": int(send_us),
        "intent_arrival_us": int(arrival_us(send_us)),
        "intent_status": None,
        "intent_ask": None,
        "intent_bid": None,
        "intent_age_s": None,
        "intent_ask_shares": None,
        "intent_spread_bps": None,
        "limit_price": None,
        "qty": 0,
        "reserved_usd": 0.0,
        "entry_status": None,
        "entry_ask": None,
        "entry_bid": None,
        "entry_age_s": None,
        "entry_quote_us": None,
        "fill_clock_us": None,
    }
    if sq is None:
        out["intent_status"] = "quote_not_acquired"
        out["entry_status"] = "quote_not_acquired"
        return out
    q, status = sq.latest_raw(int(send_us))
    out["intent_status"] = status
    if q is None:
        out["entry_status"] = status
        return out
    out["intent_ask"] = float(q["ask"])
    out["intent_bid"] = float(q["bid"])
    out["intent_age_s"] = float(q["age_s"])
    out["intent_ask_shares"] = float(q["ask_shares"])
    out["intent_spread_bps"] = None if q["spread_bps"] is None else float(q["spread_bps"])
    if float(q["age_s"]) > MAX_AGE_S:
        out["entry_status"] = "intent_not_firm_fresh"
        return out
    limit = limit_price_of(q["ask"])
    qty = causal_quantity(limit)
    out["limit_price"] = float(limit)
    out["qty"] = int(qty)
    out["reserved_usd"] = reserved_usd(qty, limit)
    if qty < 1:
        out["entry_status"] = "no_order_min_capital"
        return out
    depth = q["ask_shares"]
    if depth is None or not math.isfinite(float(depth)):
        out["entry_status"] = "intent_depth_unknown"
        return out
    if int(qty) > float(depth):
        out["entry_status"] = "intent_depth_unsupported"
        return out
    return out


def resolve_entry_arrival(sq, facts: dict) -> None:
    """One-shot IOC evaluation at the actual arrival clock.

    An IOC LIMIT cannot rest into a later eligible print, so the once-only arrival read is
    the published IOC leg (``entry_price_leg``): a firm/fresh print inside the pre-declared
    limit with supported depth is the conditional full-touch fill at the ACTUAL ask; a
    higher ask is a modeled no-L1-match; a partial depth, a stale-valid, an invalid, a
    non-regular or an absent book is an execution UNKNOWN. No future print is substituted
    and the causal quantity is never resized.

    This stays a real consumer and not a forwarding wrapper: the limit match, the depth
    check, the fill clock and this study's ``unknown_entry_*`` status vocabulary are this
    producer's own accounting, layered on the shared once-only read.
    """
    if facts["entry_status"] is not None:
        return
    arrival = int(facts["intent_arrival_us"])
    if sq is None:
        facts["entry_status"] = "unknown_entry_quote_not_acquired"
        return
    q, status = entry_price_leg(sq, arrival)
    if q is None:
        # the kernel's stale-arrival verdict, kept in this study's own status vocabulary
        facts["entry_status"] = (
            "unknown_entry_stale_valid_at_arrival"
            if status == "unknown_stale_but_valid_at_arrival"
            else f"unknown_entry_{status}"
        )
        return
    facts["entry_ask"] = float(q["ask"])
    facts["entry_bid"] = float(q["bid"])
    facts["entry_age_s"] = float(q["age_s"])
    facts["entry_quote_us"] = int(q["quote_us"])
    if float(q["ask"]) > float(facts["limit_price"]) + 1e-12:
        facts["entry_status"] = "no_match_at_l1"
        return
    if float(q["ask_shares"]) < float(facts["qty"]):
        facts["entry_status"] = "unknown_entry_partial_depth_at_arrival"
        return
    facts["entry_status"] = "conditional_ioc_fill"
    # the real execution clock of an IOC fill is the arrival clock itself
    facts["fill_clock_us"] = arrival


def resolve_exit_leg(sq, qty: float | None, exit_day: str, session_end: int) -> dict:
    """The predeclared MARKET exit leg on its own session (fresh-quote-gated, +250ms).

    ``qty`` is the ACTUAL post-corporate-action share count being sold, so the displayed
    BID depth is tested against the shares that would really be sold: after a 2:1 forward
    the entry's 50 shares become 100 and a 50-deep bid is a partial capacity UNKNOWN,
    while after a 1:10 reverse they become 5 and a 10-deep bid carries them. An unverified
    action or a fractional share count is an UNKNOWN with no original-quantity fallback
    priced against the post-split book.
    """
    due_us = minute_us(exit_day, min(EXIT_MINUTE, int(session_end)))
    session_end_us = minute_us(exit_day, int(session_end))
    out = {
        "exit_submit_us": None,
        "exit_submit_status": None,
        "exit_arrival_us": None,
        "exit_bid": None,
        "exit_age_s": None,
        "exit_quote_us": None,
        "exit_spread_bps": None,
        "exit_status": None,
        "exit_fill_clock_us": None,
        "exit_shares": None if qty is None else float(qty),
    }
    if qty is None or not math.isfinite(float(qty)) or float(qty) <= 0.0:
        out["exit_status"] = "unknown_exit_share_count_unverified"
        return out
    if sq is None:
        out["exit_submit_status"] = "quote_not_acquired"
        out["exit_status"] = "unknown_exit_quote_not_acquired"
        return out
    submit_us, submit_status = submission_clock(sq, due_us, session_end_us)
    out["exit_submit_us"] = submit_us
    out["exit_submit_status"] = submit_status
    if submit_us is None:
        out["exit_status"] = "unknown_exit_no_valid_regular_quote"
        return out
    arrival = arrival_us(int(submit_us))
    out["exit_arrival_us"] = int(arrival)
    q, status, priced_at = priced_leg(sq, arrival, session_end_us)
    if q is None:
        out["exit_status"] = status
        return out
    out["exit_bid"] = float(q["bid"])
    out["exit_age_s"] = float(q["age_s"])
    out["exit_quote_us"] = int(q["quote_us"])
    out["exit_spread_bps"] = None if q["spread_bps"] is None else float(q["spread_bps"])
    if float(q["bid_shares"]) < float(qty):
        out["exit_status"] = "unknown_exit_partial_depth"
        return out
    out["exit_status"] = "conditional_market_fill"
    # the REAL execution clock actually returned by the pricing leg: the arrival clock
    # when fresh, the rested print's own timestamp when the order rested - never an
    # older quote's own timestamp
    out["exit_fill_clock_us"] = int(priced_at if priced_at is not None else arrival)
    return out


class BoundaryBook:
    """Bounded cache of the tape's own session-boundary ratios (split identity evidence)."""

    def __init__(self, sessions: dict[str, int], keep: int = 8):
        self.sessions = sessions
        self.keep = keep
        self._anchors: dict[str, dict[str, tuple[float, float]]] = {}
        self._order: list[str] = []

    def _anchor(self, day: str) -> dict[str, tuple[float, float]]:
        if day in self._anchors:
            return self._anchors[day]
        guard_day(day)
        path = BARS_ROOT / f"{day}.parquet"
        if not path.exists():
            anchors: dict[str, tuple[float, float]] = {}
        else:
            session_end = self.sessions[day]
            frame = (
                pl.scan_parquet(path)
                .filter((pl.col("et") >= 570) & (pl.col("et") <= session_end))
                .select(["ticker", "et", "open", "close"])
                .collect()
                .sort("ticker", "et")
            )
            if frame.height:
                opens = frame.filter(pl.col("et") == 570).select(["ticker", "open"])
                closes = frame.group_by("ticker").agg(pl.col("close").last())
                merged = opens.join(closes, on="ticker", how="inner")
                anchors = {
                    str(t): (float(o), float(c))
                    for t, o, c in zip(
                        merged["ticker"].to_list(),
                        merged["open"].to_list(),
                        merged["close"].to_list(),
                        strict=True,
                    )
                    if o is not None and c is not None and o > 0 and c > 0
                }
            else:
                anchors = {}
        self._anchors[day] = anchors
        self._order.append(day)
        while len(self._order) > self.keep:
            self._anchors.pop(self._order.pop(0), None)
        return anchors

    def ratio(self, ticker: str, prev_day: str, day: str) -> float | None:
        """open(day) / close(prev_day) from the tape, or None when unobservable."""
        prev = self._anchor(prev_day).get(ticker)
        cur = self._anchor(day).get(ticker)
        if prev is None or cur is None:
            return None
        return float(cur[0]) / float(prev[1])


def window_boundaries(segmap: SegmentMap, entry_day: str, exit_day: str) -> list[tuple[str, str]]:
    """Every (prior session, session] boundary strictly inside the hold window."""
    pairs: list[tuple[str, str]] = []
    cur = entry_day
    while True:
        nxt = segmap.next_nth(cur, 1)
        if nxt is None or nxt > exit_day:
            return pairs
        pairs.append((cur, nxt))
        cur = nxt
        if cur >= exit_day:
            return pairs


def exit_action_state(
    ticker: str,
    entry_day: str,
    exit_day: str,
    actions: dict[str, list[dict]],
    ratios: dict[tuple[str, str], float | None],
) -> dict:
    """Corporate-action accounting over (entry_day, exit_day] against the tape's own gaps."""
    events = [e for e in actions.get(ticker, []) if entry_day < e["ex_date"] <= exit_day]
    dedup, seen = [], set()
    for e in events:
        key = (e["action_type"], e["new_rate"], e["old_rate"], e["ex_date"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(e)
    state = {
        "action_events": len(dedup),
        "action_verified": True,
        "shares_mult": 1.0,
        "action_note": "no recorded corporate action inside the hold",
        "unknown_reason": None,
    }
    if not dedup:
        # an unrecorded split-like discontinuity inside the hold is still unverifiable
        for (prev, day), ratio in ratios.items():
            if ratio is None or not split_like(ratio):
                continue
            state.update(
                {
                    "action_verified": False,
                    "shares_mult": None,
                    "unknown_reason": "unverified_action_identity",
                    "action_note": (
                        f"unrecorded split-like discontinuity between {prev} and {day} "
                        f"(tape ratio {ratio:.4g}) with no recorded corporate action"
                    ),
                }
            )
            return state
        state["action_note"] = f"no recorded action; {len(ratios)} in-hold tape boundaries checked"
        return state
    mult = 1.0
    for e in dedup:
        if not (
            math.isfinite(float(e["new_rate"]))
            and math.isfinite(float(e["old_rate"]))
            and float(e["new_rate"]) > 0
            and float(e["old_rate"]) > 0
        ):
            state.update(
                {
                    "action_verified": False,
                    "shares_mult": None,
                    "unknown_reason": "unverified_action_identity",
                    "action_note": "recorded action rates invalid",
                }
            )
            return state
        mult *= float(e["new_rate"]) / float(e["old_rate"])
    state["shares_mult"] = mult
    state["action_note"] = f"{len(dedup)} recorded action(s) in (entry, exit]: shares x {mult:.6g}"
    for (prev, day), ratio in ratios.items():
        if ratio is None or not split_like(ratio):
            continue
        evs_b = [e for e in dedup if prev < e["ex_date"] <= day]
        emult = 1.0
        for e in evs_b:
            emult *= float(e["new_rate"]) / float(e["old_rate"])
        # The tape's own ratio is a PRICE ratio (open(day) / close(prev)) while the ledger
        # factor is a SHARE ratio (new/old). A balanced action therefore shows up as
        # price = shares^-1: a 1:10 reverse has shares x 0.1 and price x 10, a 2:1 forward
        # has shares x 2 and price x 0.5 - so the unexplained part is ratio * emult, NOT
        # ratio / emult (dividing would flag every correctly recorded split as unverified).
        residual = ratio * emult
        if split_like(residual):
            state.update(
                {
                    "action_verified": False,
                    "shares_mult": None,
                    "unknown_reason": "unverified_action_identity",
                    "action_note": (
                        f"recorded factor {emult:.6g} does not explain the discontinuity "
                        f"between {prev} and {day} (residual {residual:.4g})"
                    ),
                }
            )
            return state
    return state


def exit_quantity(qty: int, shares_mult: float | None) -> tuple[float | None, str | None]:
    """The post-event share count; a fractional count is an UNKNOWN, never a phony integer."""
    if shares_mult is None:
        return None, "unverified_action_identity"
    new_qty = float(qty) * float(shares_mult)
    if abs(new_qty - round(new_qty)) > 1e-9:
        return None, "fractional_shares_cash_in_lieu_unverified"
    return float(round(new_qty)), None


# ----- the funded book --------------------------------------------------------
@dataclass
class Position:
    ticker: str
    entry_day: str
    exit_day: str | None
    qty: int
    limit: float
    entry_ask: float
    entry_status: str
    reserved: float
    intent: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "entry_day": self.entry_day,
            "exit_day": self.exit_day,
            "qty": int(self.qty),
            "limit": self.limit,
            "entry_ask": self.entry_ask,
            "entry_status": self.entry_status,
            "reserved": self.reserved,
            "intent": dict(self.intent),
        }

    @classmethod
    def from_dict(cls, d: dict) -> Position:
        return cls(
            ticker=str(d["ticker"]),
            entry_day=str(d["entry_day"]),
            exit_day=None if d["exit_day"] is None else str(d["exit_day"]),
            qty=int(d["qty"]),
            limit=float(d["limit"]),
            entry_ask=float(d["entry_ask"]),
            entry_status=str(d["entry_status"]),
            reserved=float(d["reserved"]),
            intent=dict(d.get("intent") or {}),
        )


@dataclass
class Book:
    """One view's self-financing 3 x $250 book (cash recycles; no daily reset)."""

    view_key: str
    cash: float = BOOK
    open: list = field(default_factory=list)
    retained: list = field(default_factory=list)

    def blocked(self) -> set[str]:
        return {p.ticker for p in self.open} | {p.ticker for p in self.retained}

    def slots(self) -> int:
        return len(self.open) + len(self.retained)

    def reserved(self) -> float:
        return float(sum(p.reserved for p in self.open) + sum(p.reserved for p in self.retained))

    def to_dict(self) -> dict:
        return {
            "view_key": self.view_key,
            "cash": float(self.cash),
            "open": [p.to_dict() for p in self.open],
            "retained": [p.to_dict() for p in self.retained],
        }

    @classmethod
    def from_dict(cls, d: dict) -> Book:
        return cls(
            view_key=str(d["view_key"]),
            cash=float(d["cash"]),
            open=[Position.from_dict(p) for p in d.get("open", [])],
            retained=[Position.from_dict(p) for p in d.get("retained", [])],
        )


DAILY_COLUMNS = (
    "day",
    "view_key",
    "qual",
    "hold",
    "fee_bps",
    "cash_end",
    "reserved_end",
    "positions_open_end",
    "positions_retained_end",
    "qualified_today",
    "entries_funded",
    "entry_fills",
    "entry_unknown",
    "entry_no_match",
    "exits_resolved",
    "exit_fills",
    "exit_unknown",
    "entry_skips",
    "known_usd",
    "lower_bound_usd",
    "no_order_min_capital",
    "quote_not_acquired",
    "intent_not_firm_fresh",
    "intent_depth_unsupported",
    "intent_depth_unknown",
    "ticker_held_skips",
    "cash_or_slot_skips",
)
DAILY_TYPES = {
    "day": pl.String,
    "view_key": pl.String,
    "qual": pl.String,
    "hold": pl.Int64,
    "fee_bps": pl.Float64,
    "cash_end": pl.Float64,
    "reserved_end": pl.Float64,
    "positions_open_end": pl.Int64,
    "positions_retained_end": pl.Int64,
    "qualified_today": pl.Int64,
    "entries_funded": pl.Int64,
    "entry_fills": pl.Int64,
    "entry_unknown": pl.Int64,
    "entry_no_match": pl.Int64,
    "exits_resolved": pl.Int64,
    "exit_fills": pl.Int64,
    "exit_unknown": pl.Int64,
    "entry_skips": pl.Int64,
    "known_usd": pl.Float64,
    "lower_bound_usd": pl.Float64,
    "no_order_min_capital": pl.Int64,
    "quote_not_acquired": pl.Int64,
    "intent_not_firm_fresh": pl.Int64,
    "intent_depth_unsupported": pl.Int64,
    "intent_depth_unknown": pl.Int64,
    "ticker_held_skips": pl.Int64,
    "cash_or_slot_skips": pl.Int64,
}
TRADE_COLUMNS = (
    "kind",
    "day",
    "view_key",
    "qual",
    "hold",
    "fee_bps",
    "ticker",
    "entry_day",
    "exit_day",
    "hold_sessions",
    "intent_us",
    "intent_arrival_us",
    "intent_status",
    "intent_ask",
    "intent_bid",
    "intent_age_s",
    "intent_ask_shares",
    "intent_spread_bps",
    "limit_price",
    "qty",
    "reserved_usd",
    "entry_status",
    "entry_ask",
    "entry_bid",
    "entry_age_s",
    "entry_quote_us",
    "fill_clock_us",
    "outlay_usd",
    "exit_submit_us",
    "exit_submit_status",
    "exit_arrival_us",
    "exit_bid",
    "exit_age_s",
    "exit_quote_us",
    "exit_spread_bps",
    "exit_status",
    "exit_fill_clock_us",
    "exit_shares",
    "shares_mult",
    "action_events",
    "action_verified",
    "action_note",
    "gross",
    "gross_price",
    "net_usd",
)
TRADE_TYPES = {
    "kind": pl.String,
    "day": pl.String,
    "view_key": pl.String,
    "qual": pl.String,
    "hold": pl.Int64,
    "fee_bps": pl.Float64,
    "ticker": pl.String,
    "entry_day": pl.String,
    "exit_day": pl.String,
    "hold_sessions": pl.Int64,
    "intent_us": pl.Int64,
    "intent_arrival_us": pl.Int64,
    "intent_status": pl.String,
    "intent_ask": pl.Float64,
    "intent_bid": pl.Float64,
    "intent_age_s": pl.Float64,
    "intent_ask_shares": pl.Float64,
    "intent_spread_bps": pl.Float64,
    "limit_price": pl.Float64,
    "qty": pl.Int64,
    "reserved_usd": pl.Float64,
    "entry_status": pl.String,
    "entry_ask": pl.Float64,
    "entry_bid": pl.Float64,
    "entry_age_s": pl.Float64,
    "entry_quote_us": pl.Int64,
    "fill_clock_us": pl.Int64,
    "outlay_usd": pl.Float64,
    "exit_submit_us": pl.Int64,
    "exit_submit_status": pl.String,
    "exit_arrival_us": pl.Int64,
    "exit_bid": pl.Float64,
    "exit_age_s": pl.Float64,
    "exit_quote_us": pl.Int64,
    "exit_spread_bps": pl.Float64,
    "exit_status": pl.String,
    "exit_fill_clock_us": pl.Int64,
    "exit_shares": pl.Float64,
    "shares_mult": pl.Float64,
    "action_events": pl.Int64,
    "action_verified": pl.Boolean,
    "action_note": pl.String,
    "gross": pl.Float64,
    "gross_price": pl.Float64,
    "net_usd": pl.Float64,
}


def empty_daily() -> pl.DataFrame:
    return pl.DataFrame(schema={c: DAILY_TYPES[c] for c in DAILY_COLUMNS})


def empty_trades() -> pl.DataFrame:
    return pl.DataFrame(schema={c: TRADE_TYPES[c] for c in TRADE_COLUMNS})


def _daily_row(day: str, view: View, fee: float, book: Book, counts: dict, known: float) -> dict:
    row = {
        "day": day,
        "view_key": view.key,
        "qual": view.qual,
        "hold": int(view.hold),
        "fee_bps": float(fee),
        "cash_end": float(book.cash),
        "reserved_end": float(book.reserved()),
        "positions_open_end": len(book.open),
        "positions_retained_end": len(book.retained),
        "known_usd": float(known),
        "lower_bound_usd": float(known)
        - ORDER_BUDGET * float(counts["entry_unknown"] + counts["exit_unknown"]),
    }
    for key in DAILY_COLUMNS:
        if key not in row:
            row[key] = int(counts.get(key, 0))
    return row


def _entry_row(day: str, view: View, fee: float, r: dict, facts: dict, kind: str) -> dict:
    row = {
        "kind": kind,
        "day": day,
        "view_key": view.key,
        "qual": view.qual,
        "hold": int(view.hold),
        "fee_bps": float(fee),
        "ticker": str(r["ticker"]),
        "entry_day": day,
        "exit_day": str(r["exit_day"]),
        "hold_sessions": int(view.hold),
        **{k: facts.get(k) for k in TRADE_COLUMNS if k not in _ENTRY_ROW_FIXED},
    }
    return row


_ENTRY_ROW_FIXED = {
    "kind",
    "day",
    "view_key",
    "qual",
    "hold",
    "fee_bps",
    "ticker",
    "entry_day",
    "exit_day",
    "hold_sessions",
}


def _exit_row(day: str, view: View, fee: float, pos: Position, leg: dict, act: dict) -> dict:
    row = {
        "kind": "exit",
        "day": day,
        "view_key": view.key,
        "qual": view.qual,
        "hold": int(view.hold),
        "fee_bps": float(fee),
        "ticker": pos.ticker,
        "entry_day": pos.entry_day,
        "exit_day": day,
        "hold_sessions": int(view.hold),
    }
    row.update({k: pos.intent.get(k) for k in TRADE_COLUMNS if k not in _EXIT_ROW_FIXED})
    row.update(
        {
            "exit_submit_us": leg["exit_submit_us"],
            "exit_submit_status": leg["exit_submit_status"],
            "exit_arrival_us": leg["exit_arrival_us"],
            "exit_bid": leg["exit_bid"],
            "exit_age_s": leg["exit_age_s"],
            "exit_quote_us": leg["exit_quote_us"],
            "exit_spread_bps": leg["exit_spread_bps"],
            "exit_status": leg["exit_status"],
            "exit_fill_clock_us": leg["exit_fill_clock_us"],
            "exit_shares": leg["exit_shares"],
            "shares_mult": act["shares_mult"],
            "action_events": int(act["action_events"]),
            "action_verified": bool(act["action_verified"]),
            "action_note": str(act["action_note"]),
        }
    )
    return row


_EXIT_ROW_FIXED = {
    "kind",
    "day",
    "view_key",
    "qual",
    "hold",
    "fee_bps",
    "ticker",
    "entry_day",
    "exit_day",
    "hold_sessions",
    "exit_submit_us",
    "exit_submit_status",
    "exit_arrival_us",
    "exit_bid",
    "exit_age_s",
    "exit_quote_us",
    "exit_spread_bps",
    "exit_status",
    "exit_fill_clock_us",
    "exit_shares",
    "shares_mult",
    "action_events",
    "action_verified",
    "action_note",
}


def process_book_day(
    book: Book,
    day: str,
    view: View,
    streams: dict,
    sessions: dict[str, int],
    segmap: SegmentMap,
    actions: dict[str, list[dict]],
    boundaries: BoundaryBook,
    missing: list[dict],
    fee: float,
    qual_today: list[dict],
) -> tuple[dict, list[dict]]:
    """One view-book on one session: exits first (15:30), then the last-minute entries."""
    counts: dict[str, int] = dict.fromkeys(DAILY_COLUMNS, 0)
    counts["qualified_today"] = len(qual_today)
    known = 0.0
    trades: list[dict] = []
    session_end = int(sessions[day])

    # ---- 1) the predeclared exits due on this session (they precede the 15:59 entries)
    still_open: list[Position] = []
    for pos in list(book.open):
        if pos.exit_day != day:
            still_open.append(pos)
            continue
        counts["exits_resolved"] += 1
        sq = streams.get(pos.ticker)
        if sq is None:
            missing.append(
                {
                    "day": day,
                    "ticker": pos.ticker,
                    "leg": "exit",
                    "minute": int(min(EXIT_MINUTE, session_end)),
                    "reason": "quote_not_acquired",
                }
            )
        ratios = {
            pair: boundaries.ratio(pos.ticker, pair[0], pair[1])
            for pair in window_boundaries(segmap, pos.entry_day, day)
        }
        act = exit_action_state(pos.ticker, pos.entry_day, day, actions, ratios)
        # The ACTUAL post-action share count is verified BEFORE the leg is priced: the
        # displayed BID depth is only meaningful against the shares that would really be
        # sold, and an unverified action / fractional count is an UNKNOWN with no
        # original-quantity fallback priced against the post-event book.
        shares, qty_reason = exit_quantity(pos.qty, act["shares_mult"])
        leg = resolve_exit_leg(sq, shares, day, session_end)
        row = _exit_row(day, view, fee, pos, leg, act)
        if (
            leg["exit_status"] == "conditional_market_fill"
            and act["action_verified"]
            and shares is not None
        ):
            proceeds = proceeds_usd(shares, leg["exit_bid"], fee)
            known += proceeds - outlay_usd(pos.qty, pos.entry_ask, fee)
            book.cash += proceeds
            counts["exit_fills"] += 1
            # gross is the economic TOTAL wealth return over the hold: what the position
            # is worth at the exit divided by what it cost at the entry, so a balanced
            # corporate action is wealth-neutral; the raw price ratio alone (which would
            # report a fake -50% on a balanced 2:1) is kept separately as gross_price.
            row["gross"] = (
                float(shares) * float(leg["exit_bid"]) / (float(pos.qty) * float(pos.entry_ask))
                - 1.0
            )
            row["gross_price"] = float(leg["exit_bid"]) / float(pos.entry_ask) - 1.0
            row["net_usd"] = float(proceeds - outlay_usd(pos.qty, pos.entry_ask, fee))
        else:
            # the most specific unknown wins: an unverified/partial action accounting
            # (share count) explains the UNKNOWN before the leg's own status does
            reason = (
                act.get("unknown_reason")
                or qty_reason
                or (
                    leg["exit_status"]
                    if leg["exit_status"] != "conditional_market_fill"
                    else "unknown_exit"
                )
            )
            row["exit_status"] = f"unknown:{reason}"
            counts["exit_unknown"] += 1
            # the position is RETAINED: slot and ticker stay blocked, the ticket stays
            # reserved, and the P&L stays UNKNOWN forever (never re-priced later)
            retained_pos = Position(
                ticker=pos.ticker,
                entry_day=pos.entry_day,
                exit_day=None,
                qty=pos.qty,
                limit=pos.limit,
                entry_ask=pos.entry_ask,
                entry_status=f"unknown_exit:{reason}",
                reserved=pos.reserved,
                intent=dict(pos.intent),
            )
            book.retained.append(retained_pos)
        trades.append(row)
    book.open = still_open

    # ---- 2) the last regular minute entries, funded in the frozen rank order
    send_us = minute_us(day, session_end)
    blocked = book.blocked()
    funded: list[tuple[dict, dict, object]] = []
    for r in qual_today:
        ticker = str(r["ticker"])
        sq = streams.get(ticker)
        if sq is None:
            missing.append(
                {
                    "day": day,
                    "ticker": ticker,
                    "leg": "entry",
                    "minute": int(session_end),
                    "reason": "quote_not_acquired",
                }
            )
        facts = resolve_intent(sq, send_us)
        if facts["entry_status"] is not None:
            counts["entry_skips"] += 1
            if facts["entry_status"] in counts:
                counts[facts["entry_status"]] += 1
            trades.append(_entry_row(day, view, fee, r, facts, "skip"))
            continue
        if ticker in blocked:
            counts["ticker_held_skips"] += 1
            continue
        if book.slots() >= MAX_SLOTS or book.cash + 1e-9 < float(facts["reserved_usd"]):
            counts["cash_or_slot_skips"] += 1
            continue
        book.cash -= float(facts["reserved_usd"])
        counts["entries_funded"] += 1
        blocked.add(ticker)
        funded.append((r, facts, sq))
    # every intent of this clock is funded before any arrival outcome is observed
    for r, facts, sq in funded:
        resolve_entry_arrival(sq, facts)
        status = facts["entry_status"]
        if status == "conditional_ioc_fill":
            facts["outlay_usd"] = outlay_usd(int(facts["qty"]), float(facts["entry_ask"]), fee)
        row = _entry_row(day, view, fee, r, facts, "entry")
        row["outlay_usd"] = facts.get("outlay_usd")
        if status == "conditional_ioc_fill":
            book.cash += float(facts["reserved_usd"]) - outlay_usd(
                int(facts["qty"]), float(facts["entry_ask"]), fee
            )
            counts["entry_fills"] += 1
            pos = Position(
                ticker=str(r["ticker"]),
                entry_day=day,
                exit_day=str(r["exit_day"]),
                qty=int(facts["qty"]),
                limit=float(facts["limit_price"]),
                entry_ask=float(facts["entry_ask"]),
                entry_status=status,
                reserved=float(facts["reserved_usd"]),
            )
            pos.intent = dict(facts)
            book.open.append(pos)
        elif status == "no_match_at_l1":
            book.cash += float(facts["reserved_usd"])
            counts["entry_no_match"] += 1
        else:
            counts["entry_unknown"] += 1
            pos = Position(
                ticker=str(r["ticker"]),
                entry_day=day,
                exit_day=None,
                qty=int(facts["qty"]),
                limit=float(facts["limit_price"]),
                entry_ask=float(facts["entry_ask"]) if facts["entry_ask"] is not None else 0.0,
                entry_status=status,
                reserved=float(facts["reserved_usd"]),
            )
            pos.intent = dict(facts)
            book.retained.append(pos)
        trades.append(row)
    return _daily_row(day, view, fee, book, counts, known), trades


# ----- phase 4: the self-financing replay, per-day atomic parts ---------------
def supplement_digest(roots: tuple[Path, ...]) -> tuple[str, list[dict]]:
    entries = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.glob("????-??-??.parquet")):
            entries.append({"path": str(path), "sha256": sha256_file(path)})
    return _digest_bytes({"supplements": entries}), entries


def replay_paths(out_dir: Path, block: str, fee: float, day: str) -> tuple[Path, Path, Path]:
    root = out_dir / "replay" / block / f"{int(fee)}bps"
    return (
        root / f"{day}.parquet",
        root / f"{day}.trades.parquet",
        root / f"{day}.state.json",
    )


def replay_digest(
    day: str,
    block: str,
    fee: float,
    producer: str,
    contract_sha: str,
    qual_sha: str,
    sup_digest: str,
) -> str:
    return _digest_bytes(
        {
            "day": day,
            "block": block,
            "fee_bps": float(fee),
            "producer": producer,
            "contract": contract_sha,
            "qual": qual_sha,
            "supplements": sup_digest,
            "schema": REPLAY_SCHEMA,
        }
    )


def day_done(
    out_dir: Path,
    block: str,
    fee: float,
    day: str,
    expect: str,
    resume: bool,
) -> dict | None:
    if not resume:
        return None
    dpath, tpath, spath = replay_paths(out_dir, block, fee, day)
    if not (dpath.exists() and tpath.exists() and spath.exists()):
        return None
    try:
        state = json.loads(spath.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if state.get("resume_hash") != expect or state.get("schema") != REPLAY_SCHEMA:
        return None
    return state


def replay_block(
    out_dir: Path,
    block: str,
    days: list[str],
    sessions: dict[str, int],
    segmap: SegmentMap,
    supplemental_roots: tuple[Path, ...],
    producer: str,
    contract_sha: str,
    resume: bool,
    actions: dict[str, list[dict]],
) -> dict:
    """Replay every view and fee rung over the block's calendar, one day part at a time.

    The quote frame of a session is read exactly once and shared by every fee rung and
    every view; the legs are quote-priced identically across rungs and each rung keeps its
    own self-financing book (only the fees and the cash differ). An UNKNOWN position keeps
    its slot, ticker and reservation: it is retained, never dropped and never re-priced.
    """
    if not days:
        return {"block": block, "days": 0}
    qual = pl.read_parquet(qual_path(out_dir, block))
    by_day: dict[str, list[dict]] = {}
    for r in qual.iter_rows(named=True):
        by_day.setdefault(str(r["day"]), []).append(r)
    for day_rows in by_day.values():
        day_rows.sort(
            key=lambda r: (
                -float(r["ctr"]),
                -float(r["vwap_dist"]),
                -float(r["cum_dv"]),
                str(r["ticker"]),
            )
        )
    loop_end = days[-1]
    for hold in HOLDS:
        nxt = segmap.next_nth(days[-1], hold)
        if nxt is not None and nxt > loop_end:
            loop_end = nxt
    loop_days = [d for d in sorted(sessions) if days[0] <= d <= loop_end]
    if segmap.segment_of(loop_days[0]) != segmap.segment_of(loop_days[-1]):
        raise SystemExit(
            f"[multi-session-carry] block {block} replay would cross a calendar segment: "
            f"{loop_days[0]} -> {loop_days[-1]}"
        )
    sup_digest, _ = supplement_digest(supplemental_roots)
    qual_sha = sha256_file(qual_path(out_dir, block))
    boundaries = BoundaryBook(sessions)
    books = {fee: {v.key: Book(v.key) for v in VIEWS} for fee in FEES}
    missing: list[dict] = []
    coverage = {
        "block": block,
        "days_in_calendar": len(loop_days),
        "window_days": len(days),
        "days_replayed": 0,
        "days_cached": 0,
        "entry_fills": 0,
        "entry_unknown": 0,
        "entry_no_match": 0,
        "exit_fills": 0,
        "exit_unknown": 0,
        "missing_legs": 0,
    }
    for day in loop_days:
        guard_day(day)
        todo = []
        for fee in FEES:
            expect = replay_digest(day, block, fee, producer, contract_sha, qual_sha, sup_digest)
            state = day_done(out_dir, block, fee, day, expect, resume)
            if state is None:
                todo.append((fee, expect))
            else:
                resumed_books = state.get("books") or []
                if len(resumed_books) != len(VIEWS):
                    raise SystemExit(
                        f"[multi-session-carry] resumed book state has "
                        f"{len(resumed_books)} books, expected {len(VIEWS)} on {day}"
                    )
                books[fee] = {
                    v.key: Book.from_dict(b) for v, b in zip(VIEWS, resumed_books, strict=True)
                }
                coverage["days_cached"] += 1
        if not todo:
            continue
        tickers = {str(r["ticker"]) for r in by_day.get(day, [])}
        for fee, _ in todo:
            for v in VIEWS:
                for pos in books[fee][v.key].open:
                    if pos.exit_day == day:
                        tickers.add(pos.ticker)
        frame = quote_day_frame(DATA_ROOT, day, tickers, supplemental_roots) if tickers else None
        streams = symbol_quotes(frame, day)
        del frame
        for fee, expect in todo:
            daily_rows, trade_rows = [], []
            for view in VIEWS:
                book = books[fee][view.key]
                qual_today = [r for r in by_day.get(day, []) if str(r["view_key"]) == view.key]
                daily, trades = process_book_day(
                    book,
                    day,
                    view,
                    streams,
                    sessions,
                    segmap,
                    actions,
                    boundaries,
                    missing,
                    fee,
                    qual_today,
                )
                daily_rows.append(daily)
                trade_rows.extend(trades)
            dpath, tpath, spath = replay_paths(out_dir, block, fee, day)
            write_parquet_atomic(
                dpath,
                pl.DataFrame(daily_rows, schema={c: DAILY_TYPES[c] for c in DAILY_COLUMNS}),
            )
            write_parquet_atomic(
                tpath,
                pl.DataFrame(trade_rows, schema={c: TRADE_TYPES[c] for c in TRADE_COLUMNS})
                if trade_rows
                else empty_trades(),
            )
            write_json_atomic(
                spath,
                {
                    "day": day,
                    "block": block,
                    "fee_bps": float(fee),
                    "resume_hash": expect,
                    "producer_sha256": producer,
                    "contract_sha256": contract_sha,
                    "qual_sha256": qual_sha,
                    "supplement_digest": sup_digest,
                    "schema": REPLAY_SCHEMA,
                    "books": [books[fee][v.key].to_dict() for v in VIEWS],
                    "coverage": {
                        "entry_fills": sum(r["entry_fills"] for r in daily_rows),
                        "entry_unknown": sum(r["entry_unknown"] for r in daily_rows),
                        "entry_no_match": sum(r["entry_no_match"] for r in daily_rows),
                        "exit_fills": sum(r["exit_fills"] for r in daily_rows),
                        "exit_unknown": sum(r["exit_unknown"] for r in daily_rows),
                    },
                },
            )
        coverage["days_replayed"] += 1
        if coverage["days_replayed"] % 25 == 0 or day == loop_days[-1]:
            print(
                f"[replay:{block}] {coverage['days_replayed']} new day(s), last {day}, "
                f"{len(todo)} fee rung(s) x {len(VIEWS)} views",
                flush=True,
            )
    missing_path = out_dir / "missing_legs"
    missing_path.mkdir(parents=True, exist_ok=True)
    prior = []
    pfile = missing_path / f"{block}.json"
    if pfile.exists():
        try:
            prior = json.loads(pfile.read_text()).get("legs", [])
        except (json.JSONDecodeError, OSError):
            prior = []
    seen, legs = set(), []
    for leg in prior + missing:
        key = (leg["day"], leg["ticker"], leg["leg"], leg["minute"], leg["reason"])
        if key in seen:
            continue
        seen.add(key)
        legs.append(leg)
    write_json_atomic(
        pfile,
        {
            "block": block,
            "legs": legs,
            "count": len(legs),
            "note": (
                "read-only re-acquisition candidates: the ticker's quote stream is absent "
                "from the ranked cache and every supplement on that session"
            ),
        },
    )
    coverage["missing_legs"] = len(legs)
    return coverage


# ----- aggregation ------------------------------------------------------------
def _frame_mean(frame: pl.DataFrame, column: str) -> float | None:
    """The mean of a day-end column, or None when no day part carries it (never 0.0)."""
    if column not in frame.columns or not frame.height:
        return None
    mean = frame[column].drop_nulls().mean()
    return None if mean is None else float(mean)


def _frame_max(frame: pl.DataFrame, column: str) -> int | None:
    """The peak of a day-end column, or None when no day part carries it (never 0)."""
    if column not in frame.columns or not frame.height:
        return None
    peak = frame[column].drop_nulls().max()
    return None if peak is None else int(peak)


def _frame_last(frame: pl.DataFrame, column: str) -> int | None:
    """The last calendar day's value of a day-end column, or None when unobserved."""
    if column not in frame.columns or not frame.height:
        return None
    last = frame.sort("day")[column].drop_nulls()
    return None if not last.len() else int(last[-1])


def read_block_frames(out_dir: Path, block: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    """All per-day replay parts of one block: the daily rows and the trade rows."""
    daily_parts: list[Path] = []
    trade_parts: list[Path] = []
    for fee in FEES:
        root = out_dir / "replay" / block / f"{int(fee)}bps"
        if not root.exists():
            continue
        daily_parts.extend(sorted(root.glob("????-??-??.parquet")))
        trade_parts.extend(sorted(root.glob("????-??-??.trades.parquet")))
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


def view_cell(
    daily: pl.DataFrame,
    trades: pl.DataFrame,
    view: View,
    days: list[str],
    fee: float,
) -> dict:
    """Every reported number for one view at one fee rung on one block's calendar."""
    n_days = len(days)
    keep = set(days)
    rows = daily.filter(
        (pl.col("view_key") == view.key)
        & (pl.col("fee_bps") == float(fee))
        & (pl.col("day").is_in(sorted(keep)))
    )
    if int(rows.height) != n_days:
        raise SystemExit(
            f"[multi-session-carry] view {view.key} fee {fee}: {rows.height} daily rows on "
            f"disk for {n_days} calendar days; replay the whole block before aggregating"
        )
    view_trades = trades.filter(
        (pl.col("view_key") == view.key) & (pl.col("fee_bps") == float(fee))
    )
    known = float(rows["known_usd"].sum())
    bound = float(rows["lower_bound_usd"].sum())
    entry_fills = int(rows["entry_fills"].sum())
    exit_fills = int(rows["exit_fills"].sum())
    entry_unknown = int(rows["entry_unknown"].sum())
    exit_unknown = int(rows["exit_unknown"].sum())
    entry_no_match = int(rows["entry_no_match"].sum())
    entries_funded = int(rows["entries_funded"].sum())
    window_exits = view_trades.filter(
        (pl.col("kind") == "exit") & (pl.col("day").is_in(sorted(keep)))
    )
    filled = window_exits.filter(pl.col("exit_status") == "conditional_market_fill")
    nets = filled["net_usd"].to_list() if filled.height else []
    gross = filled["gross"].to_list() if filled.height else []
    gross_price = filled["gross_price"].to_list() if filled.height else []
    exit_shares = filled["exit_shares"].to_list() if filled.height else []
    entry_rows = view_trades.filter(pl.col("kind") == "entry")
    intent_spreads = [s for s in entry_rows["intent_spread_bps"].to_list() if s is not None]
    exit_spreads = [s for s in window_exits["exit_spread_bps"].to_list() if s is not None]
    deployed = float(sum(v for v in filled["outlay_usd"].to_list() if v is not None))
    cash_series = rows.sort("day")["cash_end"].to_list()
    monthly: dict[str, dict] = {}
    unknown_inventory_days: list[str] = []
    for r in rows.sort("day").iter_rows(named=True):
        m = monthly.setdefault(
            str(r["day"])[:7],
            {
                "days": 0,
                "traded_days": 0,
                "entries_funded": 0,
                "entry_fills": 0,
                "entry_unknown": 0,
                "entry_no_match": 0,
                "exit_fills": 0,
                "exit_unknown": 0,
                "known_usd": 0.0,
                "positions_open_end": [],
                "positions_retained_end": [],
                "inventory_unknown_days": 0,
            },
        )
        m["days"] += 1
        m["traded_days"] += int(r["entries_funded"] > 0)
        m["entries_funded"] += int(r["entries_funded"])
        m["entry_fills"] += int(r["entry_fills"])
        m["entry_unknown"] += int(r["entry_unknown"])
        m["entry_no_match"] += int(r["entry_no_match"])
        m["exit_fills"] += int(r["exit_fills"])
        m["exit_unknown"] += int(r["exit_unknown"])
        m["known_usd"] += float(r["known_usd"])
        # The book-state section is read defensively: a day part that does not carry it
        # is UNKNOWN coverage for the inventory (never a fabricated 0-position day), and
        # it is reported as such instead of crashing the whole block's aggregation.
        open_end = r.get("positions_open_end")
        retained_end = r.get("positions_retained_end")
        if open_end is None or retained_end is None:
            m["inventory_unknown_days"] += 1
            unknown_inventory_days.append(str(r["day"]))
            continue
        m["positions_open_end"].append(int(open_end))
        m["positions_retained_end"].append(int(retained_end))
    for m in monthly.values():
        # each day-end series is consumed exactly once: the mean and the peak are the
        # mean and the max of the SAME observed series (popping a key twice used to be a
        # KeyError, and 0 is never invented for an unobserved month)
        open_series = m.pop("positions_open_end")
        retained_series = m.pop("positions_retained_end")
        m["positions_open_mean"] = float(np.mean(open_series)) if open_series else None
        m["positions_open_peak"] = int(max(open_series)) if open_series else None
        m["positions_retained_final"] = int(retained_series[-1]) if retained_series else None
    skip_rows = view_trades.filter(pl.col("kind") == "skip")
    skip_counts = dict(Counter(skip_rows["entry_status"].to_list()).most_common())
    cell = {
        "view_key": view.key,
        "label": view.label,
        "qual": view.qual,
        "hold": int(view.hold),
        "fee_bps": float(fee),
        "days_replayed": n_days,
        "opportunity_days": int((rows["qualified_today"] > 0).sum()),
        "qualified_intents": int(rows["qualified_today"].sum()),
        "entries_funded": entries_funded,
        "entry_fills": entry_fills,
        "entry_unknown": entry_unknown,
        "entry_no_match": entry_no_match,
        "entry_skips": int(rows["entry_skips"].sum()),
        "exits_resolved": int(rows["exits_resolved"].sum()),
        "exit_fills": exit_fills,
        "exit_unknown": exit_unknown,
        "traded_days": int((rows["entries_funded"] > 0).sum()),
        "known_usd": known,
        "known_usd_per_calendar_day": known / n_days,
        "dollars_per_year_252_on_book_750": known / n_days * ANNUAL_SESSIONS,
        "annualization": (
            f"mean daily known contribution x {ANNUAL_SESSIONS} sessions on a "
            f"${int(BOOK)} nominal book; a simple session-count convention, NOT a CAGR"
        ),
        "full_loss_lower_bound_usd_per_calendar_day": bound / n_days,
        "lower_bound_note": (
            "a guard bound that charges every UNKNOWN execution a full ticket; it is NOT "
            "an expected value and the unknown share is never assumed to be zero"
        ),
        "bootstrap_daily_known_usd": bootstrap_daily(
            rows.sort("day")["known_usd"].to_list(), n=BOOT_N, seed=BOOT_SEED
        ),
        "inventory": {
            "positions_open_mean": _frame_mean(rows, "positions_open_end"),
            "positions_open_peak": _frame_max(rows, "positions_open_end"),
            "positions_retained_final": _frame_last(rows, "positions_retained_end"),
            "reserved_usd_mean": _frame_mean(rows, "reserved_end"),
            "reserved_usd_peak": _frame_max(rows, "reserved_end"),
        },
        "inventory_coverage": {
            "days_with_book_state": int(
                rows.height - rows["positions_open_end"].is_null().sum()
            )
            if "positions_open_end" in rows.columns
            else 0,
            "unknown_coverage_days": sorted(unknown_inventory_days),
            "note": (
                "a day part without the book-state section is UNKNOWN coverage for the "
                "inventory only; its dollars, fills and skip reasons are still counted"
            ),
        },
        "cash": {
            "start_usd": BOOK,
            "end_usd": float(cash_series[-1]) if cash_series else None,
            "min_end_usd": float(min(cash_series)) if cash_series else None,
            "self_financing": True,
        },
        "trade_stats": {
            "known_round_trips": len(nets),
            "mean_net_usd_per_known_round_trip": float(np.mean(nets)) if nets else None,
            "median_net_usd_per_known_round_trip": float(np.median(nets)) if nets else None,
            "worst_known_round_trip_usd": float(np.min(nets)) if nets else None,
            "best_known_round_trip_usd": float(np.max(nets)) if nets else None,
            "win_rate_known_round_trip": (float(np.mean(np.asarray(nets) > 0)) if nets else None),
            "mean_gross_round_trip": float(np.mean(gross)) if gross else None,
            "mean_exit_shares_per_known_round_trip": (
                float(np.mean(exit_shares)) if exit_shares else None
            ),
            "mean_gross_round_trip_note": (
                "economic TOTAL wealth return over the hold: post-action exit shares x "
                "exit BID / (entry shares x entry ASK) - 1, so a balanced corporate "
                "action is wealth-neutral"
            ),
            "mean_gross_price_round_trip": (float(np.mean(gross_price)) if gross_price else None),
            "mean_gross_price_round_trip_note": (
                "the RAW price ratio exit BID / entry ASK - 1 with no share adjustment: "
                "a lookahead-unsafe wealth proxy across a corporate action, reported "
                "separately and never aggregated as the return"
            ),
            "mean_entry_intent_spread_bps": (
                float(np.mean(intent_spreads)) if intent_spreads else None
            ),
            "mean_exit_spread_bps": float(np.mean(exit_spreads)) if exit_spreads else None,
            "skip_reason_counts": skip_counts,
            "exit_status_counts": dict(
                window_exits.group_by("exit_status").len().sort("len", descending=True).iter_rows()
            ),
        },
        "self_financing_known_only": {
            "note": (
                "KNOWN-only partial series: unknown executions are excluded from both the "
                "numerator and the cash path, so these are NOT an account CAGR"
            ),
            "deployed_usd": deployed,
            "known_net_usd": known,
            "roi_on_deployed": (known / deployed) if deployed else None,
            "end_cash_usd": float(cash_series[-1]) if cash_series else None,
            "cagr_known_only": (
                (float(cash_series[-1]) / BOOK) ** (ANNUAL_SESSIONS / n_days) - 1.0
                if cash_series and float(cash_series[-1]) > 0
                else None
            ),
        },
        "monthly": monthly,
    }
    return cell


def aggregate_block(out_dir: Path, block: str, days: list[str]) -> dict:
    """The full view x fee surface of one block."""
    daily, trades = read_block_frames(out_dir, block)
    return {
        view.key: {str(int(fee)): view_cell(daily, trades, view, days, fee) for fee in FEES}
        for view in VIEWS
    }


def choose_view(surface: dict) -> tuple[View, list[dict]]:
    """Deterministic argmax of the frozen validation objective; no floors, no gates."""
    ranked = sorted(
        VIEWS,
        key=lambda v: (
            -surface[v.key][str(int(SELECT_FEE))]["known_usd_per_calendar_day"],
            -surface[v.key][str(int(SELECT_FEE))]["entry_fills"],
            v.key,
        ),
    )
    ranking = [
        {
            "view_key": v.key,
            "label": v.label,
            "qual": v.qual,
            "hold": int(v.hold),
            "validation_known_usd_per_calendar_day_25": surface[v.key][str(int(SELECT_FEE))][
                "known_usd_per_calendar_day"
            ],
            "known_round_trips": surface[v.key][str(int(SELECT_FEE))]["trade_stats"][
                "known_round_trips"
            ],
            "entry_unknown": surface[v.key][str(int(SELECT_FEE))]["entry_unknown"],
            "exit_unknown": surface[v.key][str(int(SELECT_FEE))]["exit_unknown"],
            "traded_days": surface[v.key][str(int(SELECT_FEE))]["traded_days"],
        }
        for v in ranked
    ]
    return ranked[0], ranking


def decision_text(chosen: View, val: dict, late: dict | None) -> str:
    objective = val["known_usd_per_calendar_day"]
    fills = val["trade_stats"]["known_round_trips"]
    boot = val["bootstrap_daily_known_usd"]
    # the UNKNOWN share is reported beside the measured contribution and never assumed zero
    unknown_count = int(val["entry_unknown"] + val["exit_unknown"])
    unknown_share_pct = (
        100.0 * unknown_count / max(1, int(val["entries_funded"] + val["exits_resolved"]))
    )
    if fills == 0 or objective is None:
        return (
            "NO_EDGE_EVIDENCE: the chosen validation view has no known round trip, so an "
            "empty signal set is cash, not a positive edge. No actual-touch positive "
            "formulation was measured; the retained rare h60 lead stays the reference and "
            "nothing here is promoted."
        )
    if objective <= 0:
        return (
            f"NO_ACTUAL_TOUCH_POSITIVE_FORMULATION: the best of the six views "
            f"({chosen.label}) measures {objective:+.4f} $/calendar day of KNOWN "
            f"contribution on 2023 validation at 25bps over {fills} known round trips "
            f"(day bootstrap p>0 = {boot.get('p_gt_zero')}). All six views are measured and "
            "none is positive, so the retained rare h60 lead stays the reference; the "
            "unknown share is reported beside this number and is NOT assumed zero."
        )
    text = (
        f"{STATUS}: chosen {chosen.label} by 2023 validation net cash flow: "
        f"{objective:+.4f} $/calendar day at 25bps = "
        f"{val['dollars_per_year_252_on_book_750']:+.2f} $/year on the ${int(BOOK)} nominal "
        f"book (simple {ANNUAL_SESSIONS}-session convention, not a CAGR), {fills} known round "
        f"trips on {val['traded_days']} traded days of {val['days_replayed']} calendared days, "
        f"{unknown_count} UNKNOWN executions ({unknown_share_pct:.1f}% "
        f"of funded intents) and {val['entry_no_match']} no-match unfilled intents. Day "
        f"bootstrap p>0 = {boot.get('p_gt_zero')}. This is the measured KNOWN-contribution "
        "expectation on a PARTIAL basis (unknowns excluded from the numerator), not an "
        "actual account CAGR."
    )
    if late is not None:
        text += (
            f" The frozen late block measures "
            f"{late['known_usd_per_calendar_day']:+.4f} $/calendar day at 25bps over "
            f"{late['trade_stats']['known_round_trips']} known round trips of "
            f"{late['entries_funded']} funded intents on the previously explored "
            "2025-02..2026-05 window; the choice was frozen before any late file was read, "
            "so this is confirmation of an already-frozen decision, not a re-selection."
        )
    else:
        text += " Late block not run (--skip-late)."
    return text


# ----- prerequisites and provenance -------------------------------------------
def prerequisites(sessions: dict[str, int], days: list[str], out_dir: Path) -> dict:
    """Read-only manifest of every input this study needs, before anything is written."""
    panel_missing = [d for d in days if not (PANEL_DAYS / f"{d}.parquet").exists()]
    bars_missing = [d for d in days if not (BARS_ROOT / f"{d}.parquet").exists()]
    quotes_missing = [d for d in days if not (QUOTE_CACHE_ROOT / f"{d}.parquet").exists()]
    splits_ok = SPLITS.exists()
    return {
        "calendar": {
            "artifact": str(CALENDAR),
            "sessions": len(sessions),
            "first": min(sessions),
            "last": max(sessions),
            "early_closes": sorted(d for d, e in sessions.items() if e != 959),
            "session_end_values": sorted(set(sessions.values())),
        },
        "panel_days_root": str(PANEL_DAYS),
        "panel_days_present": len(days) - len(panel_missing),
        "panel_days_missing": panel_missing,
        "bars_root": str(BARS_ROOT),
        "bars_days_present": len(days) - len(bars_missing),
        "bars_days_missing": bars_missing,
        "quote_cache_root": str(QUOTE_CACHE_ROOT),
        "quote_days_present": len(days) - len(quotes_missing),
        "quote_days_missing": quotes_missing,
        "quote_note": (
            "a day file existing is not per-ticker coverage: a ticker absent from a "
            "resident day stream is an execution UNKNOWN (or a fetch manifest leg), "
            "never a fabricated quote"
        ),
        "split_ledger": {
            "path": str(SPLITS),
            "present": bool(splits_ok),
            "sha256": sha256_file(SPLITS) if splits_ok else None,
        },
        "protected_unread": list(PROTECTED_MONTHS) + ["2024"],
        "credentials": {
            "env_path": str(ENV_PATH),
            "present": ENV_PATH.exists(),
            "note": "only needed by the optional read-only --fetch-missing re-acquisition",
        },
        "output_root": str(out_dir),
    }


def provenance_block(
    out_dir: Path,
    sessions: dict[str, int],
    segmap: SegmentMap,
    days_val: list[str],
    days_late: list[str] | None,
    supplemental_roots: tuple[Path, ...],
) -> dict:
    sup_digest, sup_entries = supplement_digest(supplemental_roots)
    return {
        "producer_sha256": producer_sha256(),
        "producer_script": str(Path(__file__).resolve()),
        "contract_sha256": _digest_bytes(CONTRACT),
        "calendar": str(CALENDAR),
        "segments": [{"first": s[0], "last": s[-1], "sessions": len(s)} for s in segmap.segments],
        "early_closes": sorted(d for d, e in sessions.items() if e != 959),
        "panel_days_root": str(PANEL_DAYS),
        "quote_cache_root": str(QUOTE_CACHE_ROOT),
        "supplemental_roots": [str(r) for r in supplemental_roots],
        "supplemental_roots_existing": [str(r) for r in supplemental_roots if r.exists()],
        "supplemental_files": sup_entries,
        "supplement_digest": sup_digest,
        "split_ledger": str(SPLITS),
        "validation_days": len(days_val),
        "late_days": (
            len(days_late)
            if days_late is not None
            else "not_yet_inspected: the freeze precedes any late file access"
        ),
        "output_root": str(out_dir),
    }


# ----- optional read-only re-acquisition of the manifest legs -----------------
def fetch_missing(out_dir: Path, req_sleep: float) -> dict:
    legs: list[dict] = []
    root = out_dir / "missing_legs"
    for path in sorted(root.glob("*.json")) if root.exists() else []:
        try:
            legs.extend(json.loads(path.read_text()).get("legs", []))
        except (json.JSONDecodeError, OSError):
            continue
    unique, seen = [], set()
    for leg in legs:
        key = (leg["day"], leg["ticker"], leg["leg"], leg["minute"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(
            {
                "day": leg["day"],
                "ticker": leg["ticker"],
                "leg": leg["leg"],
                "minute": int(leg["minute"]),
            }
        )
    if not unique:
        print("[fetch] nothing to fetch: no missing legs on disk", flush=True)
        return {"legs": 0, "status": "no_missing_legs"}
    print(
        f"[fetch] {len(unique)} read-only Alpaca REST leg(s) -> {out_dir / FETCH_CACHE_NAME}",
        flush=True,
    )
    manifest = fetch_missing_legs(
        unique, out_dir / FETCH_CACHE_NAME, ENV_PATH, int(LATENCY_US / 1000), req_sleep
    )
    counts = dict(Counter(str(m.get("status")) for m in manifest).most_common())
    summary = {
        "legs_requested": len(unique),
        "legs_returned": len(manifest),
        "status_counts": counts,
        "quotes_root": str(out_dir / FETCH_CACHE_NAME / "quotes"),
        "note": (
            "read-only market-data acquisition: raw pages and the normalized per-day "
            "parquet stay on disk; an HTTP 200 with zero events is a genuine 'no quotes "
            "received in this window' observation, not proof that no market existed"
        ),
    }
    write_json_atomic(out_dir / "fetch_cache" / "manifest.json", summary)
    print(f"[fetch] {json.dumps(counts)}", flush=True)
    return summary


def print_missing(out_dir: Path) -> None:
    root = out_dir / "missing_legs"
    total, by_reason, by_month = 0, Counter(), Counter()
    for path in sorted(root.glob("*.json")) if root.exists() else []:
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for leg in payload.get("legs", []):
            total += 1
            by_reason[str(leg.get("reason"))] += 1
            by_month[str(leg.get("day"))[:7]] += 1
    print(f"[missing] {total} legs across {len(by_month)} month(s)")
    print(f"[missing] reasons: {dict(by_reason.most_common())}")
    print(f"[missing] months: {dict(sorted(by_month.items()))}")


# ----- run --------------------------------------------------------------------
def run(args: argparse.Namespace) -> None:
    t0 = time.time()
    out = args.out or OUTPUT
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")
    producer = producer_sha256()
    contract_sha = _digest_bytes(CONTRACT)

    if args.print_missing:
        print_missing(out)
        return
    if args.fetch_missing:
        fetch_missing(out, args.fetch_sleep)
        return

    sessions = load_sessions()
    segmap = SegmentMap.build(sessions)
    (out / "contract.json").write_text(json.dumps(CONTRACT, indent=1, default=_default) + "\n")
    print(
        f"[cal] {len(sessions)} sessions in {len(segmap.segments)} segment(s): "
        + " | ".join(f"{s[0]}..{s[-1]}" for s in segmap.segments)
        + f"; {sum(1 for e in sessions.values() if e != 959)} early closes",
        flush=True,
    )

    supplemental = list(SUPPLEMENT_ROOTS)
    for extra in args.supplement or []:
        if extra not in supplemental:
            supplemental.append(extra)
    fetched = out / FETCH_CACHE_NAME / "quotes"
    if fetched.exists() and fetched not in supplemental:
        supplemental.append(fetched)
    supplemental_roots = tuple(supplemental)
    existing = [r for r in supplemental_roots if r.exists()]
    print(
        "[supplements] " + (", ".join(str(r) for r in existing) if existing else "none present"),
        flush=True,
    )

    blocks = [b.strip() for b in args.blocks.split(",") if b.strip()]
    if args.skip_late and "confirmation" in blocks:
        blocks.remove("confirmation")
    if args.skip_dev and "dev" in blocks:
        blocks.remove("dev")
    for b in blocks:
        if b not in BLOCK_WINDOWS:
            raise SystemExit(f"[multi-session-carry] unknown block: {b}")

    pre_freeze = [b for b in blocks if b != "confirmation"]
    post_freeze = [b for b in blocks if b == "confirmation"]
    if post_freeze and "validation" not in pre_freeze:
        raise SystemExit(
            "[multi-session-carry] the confirmation block requires the validation block: "
            "the choice must be frozen on 2023 before any late file is read"
        )
    cal: dict[str, list[str]] = {
        b: block_days(sessions, b, args.days) for b in pre_freeze + post_freeze
    }
    for b, d in cal.items():
        if not d:
            raise SystemExit(f"[multi-session-carry] no sessions selected for block {b}")
    if "dev" in cal and len(cal["dev"]) < MIN_DEV_DAYS:
        raise SystemExit(
            f"[multi-session-carry] dev block has {len(cal['dev'])} sessions; the "
            f">=6 month claim needs at least {MIN_DEV_DAYS}"
        )
    for b in ("validation", "confirmation"):
        if b in cal and args.days is None and len(cal[b]) != EXPECTED_DAYS[b]:
            raise SystemExit(
                f"[multi-session-carry] requires {EXPECTED_DAYS[b]} {b} sessions, got {len(cal[b])}"
            )
    print(
        "[blocks] " + ", ".join(f"{b}={len(cal[b])}" for b in pre_freeze + post_freeze),
        flush=True,
    )

    pre_freeze_days = sorted({d for b in pre_freeze for d in cal[b]})
    prereq = prerequisites(sessions, pre_freeze_days, out)
    write_json_atomic(out / "prerequisites.json", prereq)
    print(
        f"[prereq] panel {prereq['panel_days_present']}/{len(pre_freeze_days)} days, "
        f"bars {prereq['bars_days_present']}/{len(pre_freeze_days)}, "
        f"quotes {prereq['quote_days_present']}/{len(pre_freeze_days)}, "
        f"split ledger {prereq['split_ledger']['present']} (pre-freeze manifest; the late "
        "sessions are not even stat-ed until after the freeze)",
        flush=True,
    )
    for key in ("panel_days_missing", "bars_days_missing"):
        if prereq[key]:
            raise SystemExit(f"[multi-session-carry] {key}: {prereq[key][:5]}")

    actions = load_actions()
    print(
        f"[ledger] corporate-action events for {len(actions)} symbols (read-only split ledger)",
        flush=True,
    )

    # ---- pre-freeze: dev context + validation candidates/qual/replay
    def candidate_days_for(blocks_: list[str]) -> list[str]:
        days: set[str] = set()
        for b in blocks_:
            days.update(cal[b])
            first = cal[b][0]
            days.update(segmap.prior_n(first, EMERGENCE_LOOKBACK))
        return sorted(days)

    def run_block(b: str) -> dict:
        return replay_block(
            out,
            b,
            cal[b],
            sessions,
            segmap,
            supplemental_roots,
            producer,
            contract_sha,
            args.resume,
            actions,
        )

    pre_cov = candidate_block(
        out, candidate_days_for(pre_freeze), args.resume, sessions, producer, contract_sha
    )
    dev_surface = None
    dev_cov = None
    if "dev" in pre_freeze:
        build_qual_block(out, "dev", cal["dev"], segmap, args.resume, producer, contract_sha)
        dev_cov = run_block("dev")
        dev_surface = aggregate_block(out, "dev", cal["dev"])
        for view in VIEWS:
            c = dev_surface[view.key][str(int(SELECT_FEE))]
            print(
                f"[dev@25] {view.label:<16} {c['known_usd_per_calendar_day']:+8.4f} $/day "
                f"known trips={c['trade_stats']['known_round_trips']} "
                f"unknown={c['entry_unknown'] + c['exit_unknown']} "
                f"nomatch={c['entry_no_match']} funded={c['entries_funded']} "
                f"traded_days={c['traded_days']}/{c['days_replayed']}",
                flush=True,
            )
    build_qual_block(
        out, "validation", cal["validation"], segmap, args.resume, producer, contract_sha
    )
    val_cov_replay = run_block("validation")
    val_cov = {"candidates": pre_cov, "replay": val_cov_replay, "block": "validation"}
    val_surface = aggregate_block(out, "validation", cal["validation"])
    for view in VIEWS:
        c = val_surface[view.key][str(int(SELECT_FEE))]
        print(
            f"[val@25] {view.label:<16} {c['known_usd_per_calendar_day']:+8.4f} $/day "
            f"known trips={c['trade_stats']['known_round_trips']} "
            f"unknown={c['entry_unknown'] + c['exit_unknown']} "
            f"nomatch={c['entry_no_match']} funded={c['entries_funded']} "
            f"traded_days={c['traded_days']}/{c['days_replayed']} "
            f"p>0={c['bootstrap_daily_known_usd'].get('p_gt_zero')}",
            flush=True,
        )

    # ---- the freeze happens here: after validation, before any late file is read
    chosen, ranking = choose_view(val_surface)
    freeze = {
        "frozen_before_late_file_access": True,
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "study": "alpha_multi_session_carry",
        "status": STATUS,
        "chosen": {
            "view_key": chosen.key,
            "label": chosen.label,
            "qual": chosen.qual,
            "hold": int(chosen.hold),
        },
        "selection": {
            "objective": "2023 validation net cash flow per full calendar day at 25 bps",
            "selection_cost_bps": SELECT_FEE,
            "cost_ladder_bps": list(FEES),
            "basis": (
                "PARTIAL: unknown executions are excluded from the numerator and reported "
                "beside them; the full-loss lower bound is an optional guard, never the "
                "objective"
            ),
            "power_floors": None,
            "median_or_tail_gates": None,
            "cost_or_count_kill": None,
            "tie_break": "(- objective, - entry_fills, view_key)",
            "ranking": ranking,
        },
        "views": [
            {"view_key": v.key, "label": v.label, "qual": v.qual, "hold": int(v.hold)}
            for v in VIEWS
        ],
        "validation_surface": val_surface,
        "provenance": provenance_block(
            out, sessions, segmap, cal["validation"], None, supplemental_roots
        ),
    }
    write_json_atomic(out / "selection_freeze.json", freeze)
    print(
        f"[freeze] selection_freeze.json -> chosen {chosen.label} "
        f"({val_surface[chosen.key][str(int(SELECT_FEE))]['known_usd_per_calendar_day']:+.4f} "
        "$/day @25) AFTER validation, BEFORE any late file is read",
        flush=True,
    )

    late_surface = None
    late_cov = None
    if "confirmation" in post_freeze:
        # the FIRST access to any late panel / bar / quote file happens here
        late_cov_cand = candidate_block(
            out, candidate_days_for(["confirmation"]), args.resume, sessions, producer, contract_sha
        )
        build_qual_block(
            out, "confirmation", cal["confirmation"], segmap, args.resume, producer, contract_sha
        )
        late_cov = run_block("confirmation")
        _ = late_cov_cand
        late_surface = aggregate_block(out, "confirmation", cal["confirmation"])
        for view in VIEWS:
            c = late_surface[view.key][str(int(SELECT_FEE))]
            print(
                f"[late@25] {view.label:<16} {c['known_usd_per_calendar_day']:+8.4f} $/day "
                f"known trips={c['trade_stats']['known_round_trips']} "
                f"unknown={c['entry_unknown'] + c['exit_unknown']} "
                f"nomatch={c['entry_no_match']} funded={c['entries_funded']} "
                f"traded_days={c['traded_days']}/{c['days_replayed']}",
                flush=True,
            )
    prereq = prerequisites(sessions, sorted({d for b in cal for d in cal[b]}), out)
    write_json_atomic(out / "prerequisites.json", prereq)

    late_cell = late_surface[chosen.key][str(int(SELECT_FEE))] if late_surface else None
    results = {
        "study": "alpha_multi_session_carry",
        "status": STATUS,
        "decision": decision_text(chosen, val_surface[chosen.key][str(int(SELECT_FEE))], late_cell),
        "chosen": freeze["chosen"],
        "frozen_choice_immutable": True,
        "contract": CONTRACT,
        "cli": (
            "uv run --no-sync python factory/scripts/alpha_multi_session_carry.py "
            "[--out DIR] [--resume] [--days D...] [--blocks a,b,c] [--skip-late] "
            "[--skip-dev] [--supplement DIR] [--print-missing] [--fetch-missing] "
            "[--fetch-sleep S]"
        ),
        "dev": dev_surface,
        "validation": val_surface,
        "validation_ranking": ranking,
        "late": late_surface,
        "late_ranking": (
            [
                {
                    "view_key": v.key,
                    "label": v.label,
                    "late_known_usd_per_calendar_day_25": late_surface[v.key][str(int(SELECT_FEE))][
                        "known_usd_per_calendar_day"
                    ],
                    "known_round_trips": late_surface[v.key][str(int(SELECT_FEE))]["trade_stats"][
                        "known_round_trips"
                    ],
                    "entry_unknown": late_surface[v.key][str(int(SELECT_FEE))]["entry_unknown"],
                    "exit_unknown": late_surface[v.key][str(int(SELECT_FEE))]["exit_unknown"],
                }
                for v in sorted(
                    VIEWS,
                    key=lambda v: (
                        -late_surface[v.key][str(int(SELECT_FEE))]["known_usd_per_calendar_day"],
                        v.key,
                    ),
                )
            ]
            if late_surface
            else None
        ),
        "chosen_late_block": (late_surface[chosen.key] if late_surface else None),
        "cost_ladder_whole_calendar_known_contributions": {
            view.key: {
                fee: {
                    "validation_usd_per_calendar_day": val_surface[view.key][fee][
                        "known_usd_per_calendar_day"
                    ],
                    "validation_known_usd": val_surface[view.key][fee]["known_usd"],
                    "validation_lower_bound_usd_per_calendar_day": val_surface[view.key][fee][
                        "full_loss_lower_bound_usd_per_calendar_day"
                    ],
                    "late_usd_per_calendar_day": (
                        late_surface[view.key][fee]["known_usd_per_calendar_day"]
                        if late_surface
                        else None
                    ),
                    "late_known_usd": (
                        late_surface[view.key][fee]["known_usd"] if late_surface else None
                    ),
                    "late_lower_bound_usd_per_calendar_day": (
                        late_surface[view.key][fee]["full_loss_lower_bound_usd_per_calendar_day"]
                        if late_surface
                        else None
                    ),
                }
                for fee in FEE_KEYS
            }
            for view in VIEWS
        },
        "coverage": {
            "candidates_pre_freeze": pre_cov,
            "dev": dev_cov,
            "validation": val_cov,
            "confirmation": late_cov,
        },
        "prerequisites": prereq,
        "provenance": provenance_block(
            out,
            sessions,
            segmap,
            cal["validation"],
            cal.get("confirmation"),
            supplemental_roots,
        ),
        "missing_legs": {
            str(p.stem): int(len(json.loads(p.read_text()).get("legs", [])))
            for p in sorted((out / "missing_legs").glob("*.json"))
        }
        if (out / "missing_legs").exists()
        else {},
        "artifacts": {
            "contract": str(out / "contract.json"),
            "selection_freeze": str(out / "selection_freeze.json"),
            "producer_snapshot": str(out / "producer_snapshot.py"),
            "prerequisites": str(out / "prerequisites.json"),
            "candidates_root": str(out / "candidates"),
            "qual_root": str(out / "qual"),
            "replay_root": str(out / "replay"),
            "missing_legs_root": str(out / "missing_legs"),
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
        "--blocks",
        default="dev,validation,confirmation",
        help="comma-separated blocks to run (default: dev,validation,confirmation)",
    )
    p.add_argument(
        "--resume", action="store_true", help="resume every phase from the per-day artifacts"
    )
    p.add_argument("--skip-late", action="store_true", help="stop after the frozen selection")
    p.add_argument("--skip-dev", action="store_true", help="skip the context-only dev block")
    p.add_argument(
        "--supplement",
        type=Path,
        action="append",
        default=None,
        help="extra quote supplement cache root (<root>/<day>.parquet); originals still win",
    )
    p.add_argument(
        "--print-missing",
        action="store_true",
        help="print the missing-tape manifest and exit (nothing is fetched)",
    )
    p.add_argument(
        "--fetch-missing",
        action="store_true",
        help="read-only Alpaca REST re-acquisition of the manifest legs into <out>/fetch_cache",
    )
    p.add_argument(
        "--fetch-sleep",
        type=float,
        default=0.2,
        help="seconds between read-only fetch requests (default: 0.2)",
    )
    run(p.parse_args())


if __name__ == "__main__":
    main()
