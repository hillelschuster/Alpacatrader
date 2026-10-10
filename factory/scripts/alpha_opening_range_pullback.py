#!/usr/bin/env python3
"""Opening-range BREAKOUT / PULLBACK continuation study (PIT, quote-aware, no refit).

A new PIT mechanism family over the existing open-anchored top-gainer panel: the
trade is never the 09:30 future leader and never the old same-bar flush. From the
existing PIT B watchlist AFTER admission, two frozen qualifications decide in the
09:45-11:00 window, both anchored on the FIRST 15-MINUTE range (09:30-09:44):

  * ``pullback_retest``: the first post-09:45 minute ``b`` with ``high[b]`` above
    the first-15m high, then the first causal RETEST ``r > b`` that touches the
    open VWAP from above (``low[r] <= VWAP[r] < close[r]``) on CONTRACTED volume
    versus the breakout bar (``volume[r] < volume[b]``), confirmed by a positive
    prior bar (``close[r] > open[r]``); the decision minute is ``r + 1`` and the
    entry minute is ``t = r + 2``.
  * ``direct_breakout``: the same first post-09:45 break traded DIRECTLY as the
    first-15m range expansion itself (the break bar's high extends the range
    above the first-15m high; no retest wait) with a positive prior bar
    (``close[b] > open[b]``); the decision minute is ``b + 1`` and the entry
    minute is ``t = b + 2``.

Clock chain: a minute bar stamped ``m`` covers ``[m, m+1)`` and closes at ``m+1``,
so the signal bar's close is the DECISION minute ``m+1`` (the first minute the bar
is fully observable, with the first-15m reference 570..584 fully observed after
585) and the funded IOC intent is sent at the NEXT minute clock, the ENTRY minute
``m+2`` -- one full observation-minute buffer for the unmeasured SIP bar
publication delay. Admission must be known at the decision minute
(``admit_t <= decision_minute``), the decision window is ET 09:45-11:00, and no
future admission, winner or first retest is ever read. Every feature is computed
from bars strictly before the decision minute: the ``return_signal`` is the return
of the last bar before it, the open VWAP is the panel's own close-price
dollar-volume VWAP accumulated from the 09:30 anchor. The IOC entry is priced once
from the latest RAW NBBO state at the entry clock 0 (+250ms arrival; an IOC limit
cannot rest): firm/fresh, ``ask <= causal limit`` and depth-supported gives
a conditional L1 touch fill at the ACTUAL ask; a partial, invalid, non-firm or
unacquired arrival is an UNKNOWN execution, never a rested or resized fill. The
exit is a genuine MARKET order: fresh-quote-gated submission at the due intent,
+250ms arrival, priced at the observed BID (it may rest only when the book is
genuinely absent, never solely because the latest print is aged-but-valid).

Holds are fixed at 15/30/60 minutes; the study has one first-attempt per ticker
per day per view under a $750 three-slot book. The stored LightGBM heads are
OPTIONAL (``--with-model-gate``, real forecasts only, never a synthetic score);
the primary study is a typed rules engine. Selection uses the 2023 validation
actual-touch KNOWN contribution per calendar day at the 25bps residual rung, with
UNKNOWN executions reported beside it, the observed ASK/BID touch already paying
the spread and the residual ladder (0/5/10/25/50/75/100/125/150 bps, both legs)
charging provider fees/slippage separately. Nothing is promoted and no protected
outcome (2024, 2025-01, 2026-06..08) is ever touched.

Usage:
  uv run --no-sync python factory/scripts/alpha_opening_range_pullback.py
      # full 250-day validation -> frozen selection -> 187-day late confirmation
      # -> ~/alpha-data/open-search-v1/opening_range_pullback
  uv run --no-sync python factory/scripts/alpha_opening_range_pullback.py --resume
      # resume from the per-day state / daily / trade parts already on disk
  uv run --no-sync python factory/scripts/alpha_opening_range_pullback.py
      --days 2023-05-15 2025-09-02
      # smoke subset (debug only)
  uv run --no-sync python factory/scripts/alpha_opening_range_pullback.py --with-model-gate
      # optional real stored-forecast gate (h15/h60 heads only; h30 stays rules-only)
  uv run --no-sync python factory/scripts/alpha_opening_range_pullback.py --include-dev
      # also report the 2021-05..2021-10 development window (never a selection block)
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

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Published quote-book helpers are imported, never re-derived or edited here.
import alpha_quote_aware_frequency as aqf
from alpha_open_learned import PANEL_ROOT, bootstrap_daily, feature_matrix
from alpha_open_panel import allowed
from alpha_open_sim import period
from alpha_sparse_daily import day_context
from alpha_sparse_quote_service import day_quote_path

# ----- fixed configuration (no HPO, no refit, no grid search). -----------------
STUDY = "alpha_opening_range_pullback"
STATUS = "DISCOVERY-NOT-VALIDATED"
QUALIFICATIONS = ("pullback_retest", "direct_breakout")
HEADS = (15, 30, 60)  # fixed holds from the entry minute
STORED_HEADS = (15, 60)  # the only stored payoff heads (h30 has none)
RUNG_COSTS = (0.0, 5.0, 10.0, 25.0, 50.0, 75.0, 100.0, 125.0, 150.0)
SELECT_COST = 25.0  # primary selection / entry-rule rung
MAX_SLOTS = 3  # concurrent positions
ORDER_BUDGET = 250.0  # per-position ticket
BOOK = MAX_SLOTS * ORDER_BUDGET  # $750 nominal research sub-book
MAX_ATTEMPTS = 1  # first (and only) attempt per ticker per day per view
ENTRY_OVERHEAD_BPS = 25.0  # residual added to the observed spread in the model-gated rule
ANNUAL_SESSIONS = 252  # simple session-count convention, NOT a CAGR claim
BOOT_N = 1000
BOOT_SEED = 20261009
VAL_BLOCK = "validation"
LATE_BLOCK = "confirmation"
DEV_BLOCK = "development"
EXPECTED_DAYS = {VAL_BLOCK: 250, LATE_BLOCK: 187, DEV_BLOCK: 127}
LATE_FROM = "2025-09-01"  # nine-month confirmation window 2025-09..2026-05
DEV_FROM, DEV_TO = "2021-05-01", "2021-10-31"  # development window, never a selection block
DECISION_FIRST = 585  # 09:45 ET decision window open (entry minutes)
DECISION_LAST = 660  # 11:00 ET decision window close
FIRST15_FIRST, FIRST15_LAST = 570, 584  # the first 15 minutes (09:30-09:44)
MIN_OR_BARS = 10  # at least 10 of the 15 opening minutes must carry a trade bar
MIN_CUM_DV = 1_000_000.0  # causal cumulative dollar volume gate at the signal bar
MIN_PRICE = 1.0  # causal price floor at the signal bar
MODEL_DIR = PANEL_ROOT / "learned" / "models"
OUTPUT = PANEL_ROOT / "opening_range_pullback"
DATA_ROOT = Path("/home/hillel/projects/Alpacatrader") / "data"
BARS_ROOT = DATA_ROOT / "sip" / "net" / "bars"
PANEL_DAYS = PANEL_ROOT / "days"
PANEL_CONTRACT = PANEL_ROOT / "contract.json"
STATES_SCHEMA = 1
REPLAY_SCHEMA = 1
UNDERPOWERED_KNOWN_FILLS = 25  # label only; NEVER a drop or auto-kill threshold
PROTECTED_UNREAD = ["2024", "2025-01", "2026-06", "2026-07", "2026-08"]
SUPPLEMENT_ROOTS = tuple(aqf.SUPPLEMENT_ROOTS) + (
    # Neutral full-PIT quote-harvest root (TouchHourlyPayoffLearning owner). The raw
    # SIP captures only a minority of PIT-qualified names; this root may ADD
    # timestamps for the rest. Missing today is fine: every merge helper skips a
    # non-existent root, and its files (when present) enter the supplement digest
    # that drives resume invalidation. Original prints still always win.
    PANEL_ROOT / "quote_universe_acquire" / "quotes",
)


def _assert_engine_parity() -> None:
    """The kernel's funded-ticket sizing must equal this study's declared book constants.

    Only the constants this study actually consumes locally are compared: the kernel sizes
    every quantity at its own ORDER_BUDGET while this study funds/slot-caps the same
    $250 x 3 book, so a divergence there would silently un-fund the replay. The entry
    physics (marketability cap, tick grid, arrival latency, freshness gate) is no longer
    copied here at all: the limit price, the quantity, the reservation and the IOC leg are
    all taken from the published helpers, so there is nothing left to keep in parity by
    assertion - a stale duplicate copy is a second convention, not a safety net.
    """
    mine = {
        "ORDER_BUDGET": ORDER_BUDGET,
        "MAX_SLOTS": MAX_SLOTS,
    }
    theirs = {
        "ORDER_BUDGET": aqf.ORDER_BUDGET,
        "MAX_SLOTS": aqf.MAX_SLOTS,
    }
    for key, value in mine.items():
        if float(theirs[key]) != float(value):
            raise SystemExit(
                f"[{STUDY}] published helper physics diverged: {key} "
                f"{theirs[key]!r} != {value!r}; update this study or the helper"
            )


_assert_engine_parity()

CONTRACT = {
    "version": STATES_SCHEMA,
    "study": STUDY,
    "hypothesis": "an opening-range BREAKOUT/PULLBACK continuation entered from the "
    "PIT B watchlist after admission, decided only inside 09:45-11:00 from strictly "
    "past minute bars, clears the observed NBBO touch plus a residual round-trip fee "
    "ladder more often than the retained rare h60 lead",
    "mechanism": {
        "not_buying_0930_leader": True,
        "not_same_bar_flush": True,
        "anchor": "first 15 minutes (09:30-09:44) of the same tape",
        "decision_window_et": [DECISION_FIRST, DECISION_LAST],
        "clock_chain": "a minute bar stamped m covers [m, m+1) and closes at m+1: "
        "the DECISION minute is signal_bar+1 (first fully-observable minute) and the "
        "funded IOC INTENT clock is the ENTRY minute decision+1 = signal_bar+2, one "
        "full observation-minute buffer for the unmeasured SIP publication delay; "
        "the intent quote is the latest RAW state at the entry clock 0 and the "
        "arrival is entry+250ms",
        "admission_known_at_decision": "the watchlist membership must be observable "
        "at the decision minute: admit_t <= decision_minute",
        "breakout": "the first minute b in [585,659] with high[b] > OR_HIGH (the first "
        "15m high); a break before 09:45 never counts",
        "vwap": "open VWAP: cumDV(minute)/cumV(minute) with close-price dollar volume "
        "accumulated from the 09:30 anchor (the panel's own vwap_dist definition)",
        "pullback_retest": "after the breakout b, the first minute r in (b,659] with "
        "low[r] <= VWAP[r] and close[r] > VWAP[r] (a touch-and-hold above the open "
        "VWAP); the decision minute is r+1, the entry minute is t = r+2, and the "
        "qualification additionally requires volume[r] < volume[b] (volume "
        "contraction versus the direct breakout bar), close[r] > open[r] (confirmed "
        "positive prior bar), cumDV(r) >= 1e6, close(r) >= 1 and admit_t <= r+1",
        "direct_breakout": "the break bar itself traded DIRECTLY: the first-15m range "
        "expansion is the break (bar b is the first minute in [585,660] whose high "
        "extends above the first-15m high), so the decision minute is b+1 and the "
        "entry minute is t = b+2 with close[b] > open[b] (confirmed positive prior "
        "bar), cumDV(b) >= 1e6, close(b) >= 1 and admit_t <= b+1; the bar's range "
        "versus the average opening bar range is carried informationally "
        "(range_ratio), never as a filter",
        "first15_observed_floor": "the first-15m reference is computed over the "
        "OBSERVED opening trade bars (a no-trade minute carries no high); at least "
        "MIN_OR_BARS=10 of the 15 opening minutes must carry a bar, and every signal "
        "carries or_bars so the window's completeness is auditable",
        "first_attempt_only": "each qualification emits at most one candidate per "
        "ticker-day (the first causal retest / the first breakout); the book allows "
        "MAX_ATTEMPTS=1 per ticker per day per view and labels it first_pullback",
        "strict_past": "every field uses bars strictly before the entry minute t; the "
        "return_signal is the return of the last bar before t; no future day high, no "
        "future first pullback and no post-t tape is ever read",
    },
    "periods": {
        DEV_BLOCK: "2021-05-01..2021-10-31 (development window: construction and "
        "transparency only; never enters selection)",
        VAL_BLOCK: "2023-01-01..2023-12-31 (all 250 allowed panel days)",
        LATE_BLOCK: "2025-09-01..2026-05-31 (187 allowed panel days, nine months, "
        "previously explored, not pristine)",
    },
    "protected_unread": PROTECTED_UNREAD,
    "entry": {
        "intent_clock": "minute_us(day, t, 0): the open of the entry minute t "
        "(t = signal_bar + 2, one full minute after the signal bar's close)",
        "arrival_clock": "intent + 250ms",
        "observation": "latest RAW NBBO state at the arrival clock, evaluated ONCE "
        "(an IOC limit cannot rest)",
        "firm_fresh_rule": "valid, un-crossed, R-only, age <= 2s",
        "limit_price": "ceil_to_cent(observed_intent_ask * 1.01): pre-declared "
        "marketability cap, NOT charged friction",
        "quantity": "q = floor(250 / (limit_price * (1 + 150bps/2))), fixed BEFORE "
        "arrival at the intent clock and sized at the MAXIMUM REPORTED rung, so the "
        "reserved ticket never exceeds $250 and every rung trades the same causal "
        "quantity/limit with no future price; never re-floored from the arrival price",
        "depth_rule": "q >= 1 and q <= intent ASK top-of-book depth",
        "arrival_rule": "firm/fresh valid print with ask <= limit and arrival ASK "
        "depth >= q -> conditional L1 touch fill at the ACTUAL ask; ask > limit -> "
        "NO_MATCH_AT_L1 modelled unfilled cash; partial depth, data unavailable, "
        "invalid or non-firm arrival -> execution UNKNOWN. The unfilled IOC portion "
        "cancels: it never rests to a later eligible print and q is never resized.",
        "exit_arrival_rest_rule": "only the EXIT (a genuine MARKET order) may "
        "conditionally rest to the first eligible print at/after arrival when the "
        "book is genuinely absent or invalid; it never waits solely because the "
        "latest print is aged-but-valid",
        "model_gate": "optional (--with-model-gate): the published entry rule "
        "spread_bps + 25 <= 10000 * pred with the REAL stored h15/h60 LightGBM "
        "forecast at the last 5-minute panel clock <= t; the h30 view has no stored "
        "head and stays rules-only; a missing real forecast is a causal skip, never "
        "a synthetic score",
        "no_order_rule": "q == 0 is a KNOWN no-order (cash retained, never a priced "
        "zero-return fill, never an UNKNOWN)",
    },
    "exit": {
        "hold": "fixed minutes from the entry minute: min(t + h, session_end)",
        "due_intent_clock": "minute_us(day, min(t + h, session_end), 0)",
        "submission": "FRESH-QUOTE-GATED market order: submit at the due intent if "
        "the latest raw state is firm and fresh, else hold to the FIRST eligible "
        "regular print at/after the due intent inside RTH and submit at its timestamp",
        "arrival_clock": "actual submission + 250ms",
        "pricing": "latest raw state at the arrival clock; stale-but-valid -> "
        "UNKNOWN; invalid / non-regular / absent book -> conditional rest to the "
        "first eligible print at/after arrival (never a later favourable print)",
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
        "max_attempts_per_ticker_per_day": MAX_ATTEMPTS,
        "cooldown": "none needed: one attempt per ticker per day per view",
        "cash_reservation": "the causal worst-case rung-150 outlay q * limit * (1 + 150bps/2)",
        "simultaneous_clock_rule": "same-minute intents are funded up to the slot and "
        "cash limits in ticker order BEFORE any arrival outcome is observed; no "
        "same-clock substitution after an unfilled intent",
        "unknown_rule": "an UNKNOWN entry or exit keeps its slot and blocks its ticker "
        "until it resolves or the session ends; cash is never freed at a planned time",
        "later_clock_rule": "a later signal may use cash released by an earlier ACTUAL "
        "exit or no-match arrival",
    },
    "selection": {
        "objective": "2023 validation actual-touch known contribution dollars per full "
        "calendar day at the 25bps residual rung",
        "selection_cost_bps": SELECT_COST,
        "cost_ladder_bps": list(RUNG_COSTS),
        "basis": "PARTIAL: unknown executions are excluded from the numerator and "
        "reported beside it; the full-loss lower bound is an optional guard, never "
        "the objective",
        "power_floors": None,
        "median_or_tail_gates": None,
        "cost_or_count_kill": None,
        "frozen_before_late_inspection": True,
        "freeze_artifact": "selection_freeze.json (written after the validation "
        "surface, before any late file is read)",
        "tie_break": "(- objective, - known_fills, view_key)",
    },
    "labels": {
        "traded_basis": "real NBBO ASK entry -> BID exit at the declared clocks; the "
        "observed spread is already paid inside those prices",
        "residual_ladder_basis": "extra provider fees/slippage on both legs; a "
        "supported zero/low-fee provider scenario is reported as a diagnostic rung, "
        "never a promotion",
        "proxy_labels_used_as_entry_filter": False,
        "unfilled_entry_status_used_as_entry_filter": False,
        "minute_proxy_basis": "the panel's own minute-open gross convention carried "
        "per signal for an explicit basis comparison only",
    },
    "no_live_orders": True,
    "supplements": {
        "merge_order": ["ranked SIP day cache"] + [str(r) for r in SUPPLEMENT_ROOTS],
        "dedup_rule": (
            "unique (symbol, ts_utc) keep=first in that order: the ORIGINAL print "
            "always wins, a supplement may ONLY add timestamps and never silently "
            "replaces or revises an original print"
        ),
        "universe_acquire_root": (
            "optional neutral full-PIT harvest root "
            "PANEL_ROOT/quote_universe_acquire/quotes (owned by the Touch study); the "
            "raw SIP captures only a minority of PIT-qualified names, and this root "
            "may only ADD those timestamps. A historical stream that was never "
            "acquired is a DATA UNKNOWN at that clock, never a known cash fill"
        ),
        "resume_invalidation": (
            "supplement_digest = sha256 over every existing supplement file's "
            "(path, sha256, bytes); it is part of every per-day resume hash, so a "
            "harvested or revised cache invalidates the affected day's artifacts"
        ),
        "quote_age_rule": (
            "age > 2s is an operational freshness gate for THIS policy, never a claim "
            "that an NBBO expired or that a submitted order was cancelled"
        ),
    },
}


# ----- shared helpers ---------------------------------------------------------
def producer_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def digest_bytes(payload: dict) -> str:
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


@dataclass(frozen=True)
class View:
    """One (qualification, hold) cell. Same universe, same tape; only the bar differs."""

    qual: str
    head: int

    @property
    def key(self) -> str:
        return f"{self.qual}|h{self.head}"

    @property
    def label(self) -> str:
        return f"{self.qual}@{self.head}m"


VIEWS = tuple(View(q, h) for q in QUALIFICATIONS for h in HEADS)
VIEWS_BY_KEY = {v.key: v for v in VIEWS}


# ----- tape substrate (strictly past minute bars) ------------------------------
@dataclass(frozen=True)
class Tape:
    """One ticker's ET minute bars for one session (sorted by ``et``, gaps allowed)."""

    day: str
    ticker: str
    et: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray

    @property
    def n(self) -> int:
        return int(len(self.et))

    def position(self, minute: int) -> int | None:
        return int(np.searchsorted(self.et, int(minute)))


def build_tape(day: str, ticker: str, frame: pl.DataFrame) -> Tape | None:
    """One ticker's sorted minute tape (None when the 09:30 open anchor is absent)."""
    if frame.is_empty():
        return None
    part = frame.sort("et")
    et = part["et"].cast(pl.Int64).to_numpy().astype(np.int64)
    o = part["open"].to_numpy().astype(float)
    h = part["high"].to_numpy().astype(float)
    low = part["low"].to_numpy().astype(float)
    c = part["close"].to_numpy().astype(float)
    v = part["volume"].to_numpy().astype(float)
    if len(et) == 0 or int(et[0]) != FIRST15_FIRST:
        return None  # B admission explicitly uses the 09:30 opening anchor
    if not np.all(np.isfinite(np.column_stack([o, h, low, c, v]))) or np.any(c <= 0):
        raise ValueError(f"nonfinite/nonpositive tape: {day} {ticker}")
    return Tape(
        day=day,
        ticker=ticker,
        et=et,
        open=o,
        high=h,
        low=low,
        close=c,
        volume=v,
    )


def proxy_gross(
    tape: Tape, entry_minute: int, head: int, session_end: int
) -> tuple[float | None, str]:
    """The panel's own minute-open proxy at one hold: entry open at ``t``, first
    actual open at/after ``min(t+head, session_end)``. Basis comparison only."""
    i = tape.position(entry_minute)
    if i >= tape.n or int(tape.et[i]) != int(entry_minute):
        return None, "unfilled_expired"
    entry = float(tape.open[i])
    target = min(int(entry_minute) + int(head), int(session_end))
    j = tape.position(target)
    if j < tape.n and int(tape.et[j]) <= int(session_end):
        return float(tape.open[j]) / entry - 1.0, "observed_open_proxy"
    return None, "unknown_pending"


def _signal_record(
    tape: Tape,
    *,
    qual: str,
    entry_minute: int,
    decision_minute: int,
    breakout_idx: int,
    signal_idx: int,
    or_high: float,
    or_low: float,
    or_range: float,
    or_volume: float,
    or_bars: int,
    vwap: np.ndarray,
    cumdv: np.ndarray,
    admit_t: int,
    session_end: int,
) -> dict:
    """One frozen-qualification signal at the declared decision/entry clock chain.

    Clock chain (a minute bar stamped ``m`` covers ``[m, m+1)`` and closes at ``m+1``):
    the signal bar closes at ``signal_bar + 1`` = the DECISION minute (the first
    minute the bar is fully observable), and the funded IOC intent is sent at the
    NEXT minute clock, the ENTRY minute ``decision + 1`` = ``signal_bar + 2`` -- one
    full observation-minute buffer for the unmeasured SIP publication delay. Every
    field below is computed from bars strictly before the decision minute.
    """
    s, b = int(signal_idx), int(breakout_idx)
    et, o, h, low, c, v = tape.et, tape.open, tape.high, tape.low, tape.close, tape.volume
    rec = {
        "qual": qual,
        "entry_minute": int(entry_minute),
        "decision_minute": int(decision_minute),
        "signal_bar_minute": int(et[s]),
        "signal_minute": int(decision_minute),
        "breakout_minute": int(et[b]),
        "admit_t": int(admit_t),
        "session_end": int(session_end),
        "or_high": float(or_high),
        "or_low": float(or_low),
        "or_range": float(or_range),
        "or_volume": float(or_volume),
        "or_bars": int(or_bars),
        "signal_open": float(o[s]),
        "signal_high": float(h[s]),
        "signal_low": float(low[s]),
        "signal_close": float(c[s]),
        "signal_volume": float(v[s]),
        "signal_ret": float(c[s] / o[s] - 1.0),
        "vwap_signal": float(vwap[s]),
        "vwap_dist_signal": float(c[s] / vwap[s] - 1.0),
        "cum_dv_signal": float(cumdv[s]),
        "breakout_open": float(o[b]),
        "breakout_close": float(c[b]),
        "breakout_volume": float(v[b]),
        "breakout_ret": float(c[b] / o[b] - 1.0),
        "breakout_range": float(h[b] - low[b]),
        "range_ratio": (
            float((h[s] - low[s]) / (or_range / or_bars)) if or_range > 0 and or_bars else None
        ),
        "volume_ratio": float(v[s] / v[b]) if v[b] > 0 else None,
        "retest_depth": (
            float((low[s] - vwap[s]) / vwap[s]) if qual == "pullback_retest" else None
        ),
        "first_pullback": True,
    }
    for head in HEADS:
        gross, status = proxy_gross(tape, int(entry_minute), head, int(session_end))
        rec[f"proxy_gross_{head}"] = gross
        rec[f"proxy_status_{head}"] = status
    return rec


def tape_signals(tape: Tape, admit_t: int, session_end: int) -> tuple[list[dict], Counter]:
    """Both frozen qualifications for one ticker-day, decided on strictly past bars.

    Returns ``(signals, reasons)`` where every skip reason is counted, so a day with
    no signal still reports WHY (insufficient first-15m, no post-09:45 break, no
    causal retest, non-positive prior bar, liquidity/price/admission/window gates).
    No future day high and no future first pullback is consulted.
    """
    reasons: Counter = Counter()
    et, h, low, c, o, v = tape.et, tape.high, tape.low, tape.close, tape.open, tape.volume
    n = tape.n
    pos = {int(et[i]): i for i in range(n)}
    first15 = [pos.get(m) for m in range(FIRST15_FIRST, FIRST15_LAST + 1)]
    observed = [int(i) for i in first15 if i is not None]
    if len(observed) < MIN_OR_BARS:
        # The first-15m reference is only defined over OBSERVED opening trade bars (a
        # no-trade minute carries no high); fewer than the declared floor makes the
        # range unstable, so the ticker-day is skipped with an explicit reason.
        reasons["insufficient_first_15m_bars"] += 1
        return [], reasons
    or_high = float(h[observed].max())
    or_low = float(low[observed].min())
    or_range = or_high - or_low
    or_volume = float(v[observed].sum())
    or_bars = len(observed)
    cumdv = np.cumsum(c * v)
    cumvol = np.cumsum(v)
    with np.errstate(divide="ignore", invalid="ignore"):
        vwap = np.where(cumvol > 0, cumdv / cumvol, np.nan)

    # the first post-09:45 break of the first-15m high. A minute bar stamped m
    # covers [m, m+1) and closes at m+1, so the DECISION minute is b+1 and the
    # funded IOC intent (ENTRY minute) is b+2: one full observation-minute buffer
    # after the bar's close for the unmeasured SIP publication delay.
    b_idx: int | None = None
    for i in range(n):
        minute = int(et[i])
        if minute < DECISION_FIRST:
            continue
        if minute > DECISION_LAST:
            break
        if h[i] > or_high:
            b_idx = i
            break
    if b_idx is None:
        reasons["no_post_0945_breakout"] += 1
        return [], reasons
    b = int(et[b_idx])
    decision_b = b + 1
    t2 = decision_b + 1
    signals: list[dict] = []

    # ---- qualification 1: direct first-15m range expansion (the break bar itself).
    # The range expansion IS the break: bar b is the first minute whose high extends
    # above the first-15m high, traded directly (no retest wait). ``breakout_range``
    # versus the average opening bar range is carried informationally.
    if not (c[b_idx] > o[b_idx]):
        reasons["direct_prior_bar_not_positive"] += 1
    elif not (cumdv[b_idx] >= MIN_CUM_DV):
        reasons["direct_liquidity_unsupported"] += 1
    elif not (c[b_idx] >= MIN_PRICE):
        reasons["direct_price_below_min"] += 1
    elif decision_b > DECISION_LAST:
        reasons["direct_decision_after_window"] += 1
    elif admit_t > decision_b:
        reasons["direct_before_admission"] += 1
    else:
        signals.append(
            _signal_record(
                tape,
                qual="direct_breakout",
                entry_minute=t2,
                decision_minute=decision_b,
                breakout_idx=b_idx,
                signal_idx=b_idx,
                or_high=or_high,
                or_low=or_low,
                or_range=or_range,
                or_volume=or_volume,
                or_bars=or_bars,
                vwap=vwap,
                cumdv=cumdv,
                admit_t=admit_t,
                session_end=int(session_end),
            )
        )
        reasons["direct_signal"] += 1

    # ---- qualification 2: first causal retest above the open VWAP.
    r_idx: int | None = None
    for j in range(b_idx + 1, n):
        minute = int(et[j])
        if minute > DECISION_LAST:
            break
        w = vwap[j]
        if not np.isfinite(w):
            continue
        if low[j] <= w and c[j] > w:
            r_idx = j
            break
    if r_idx is None:
        reasons["pullback_retest_not_found"] += 1
    else:
        decision_r = int(et[r_idx]) + 1
        t1 = decision_r + 1
        if not (v[r_idx] < v[b_idx]):
            reasons["pullback_volume_not_contracted"] += 1
        elif not (c[r_idx] > o[r_idx]):
            reasons["pullback_prior_bar_not_positive"] += 1
        elif not (cumdv[r_idx] >= MIN_CUM_DV):
            reasons["pullback_liquidity_unsupported"] += 1
        elif not (c[r_idx] >= MIN_PRICE):
            reasons["pullback_price_below_min"] += 1
        elif decision_r > DECISION_LAST:
            reasons["pullback_decision_after_window"] += 1
        elif admit_t > decision_r:
            reasons["pullback_before_admission"] += 1
        else:
            signals.append(
                _signal_record(
                    tape,
                    qual="pullback_retest",
                    entry_minute=t1,
                    decision_minute=decision_r,
                    breakout_idx=b_idx,
                    signal_idx=r_idx,
                    or_high=or_high,
                    or_low=or_low,
                    or_range=or_range,
                    or_volume=or_volume,
                    or_bars=or_bars,
                    vwap=vwap,
                    cumdv=cumdv,
                    admit_t=admit_t,
                    session_end=int(session_end),
                )
            )
            reasons["pullback_signal"] += 1
    return signals, reasons


# ----- causal execution policy (entry is an IOC LIMIT; never rests) ------------
def intent_facts(sq: aqf.SymbolQuotes | None, intent_us: int) -> dict:
    """The causal intent observation at the entry-minute clock and the size it implies.

    Same keys as the published helper; the reservation is charged at THIS study's
    highest reported rung (150bps) so no lower rung is unfundable.
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
            limit = aqf.limit_price_of(q["ask"])
            # Sized at the MAXIMUM REPORTED rung (150bps both legs) BEFORE arrival, so
            # the reserved ticket is at most $250 at every reported rung and every rung
            # trades the same causal quantity/limit with no future price. (Sizing at
            # 25bps instead would over-fund a cheap share once 150bps is charged.)
            qty = aqf.causal_quantity(limit, aqf.MAX_RUNG_COST)
            base.update(
                {
                    "intent_status": "quoted",
                    "intent_ask": q["ask"],
                    "intent_bid": q["bid"],
                    "intent_age_s": q["age_s"],
                    "intent_spread_bps": q["spread_bps"],
                    "intent_ask_shares": q["ask_shares"],
                    "limit_price": limit,
                    "qty": qty,
                    "reserved_usd": aqf.reserved_usd(qty, limit, aqf.MAX_RUNG_COST),
                }
            )
    base["intent_reason"] = aqf.entry_rule_status(base, float("inf"))[0]
    return base


def state_facts(row: dict) -> dict:
    """The stored intent facts as the published entry-rule helper expects them."""
    return {
        "intent_status": row["intent_status"],
        "intent_age_s": row["intent_age_s"],
        "qty": row["qty"],
        "intent_ask_shares": row["intent_ask_shares"],
        "intent_spread_bps": row["intent_spread_bps"],
    }


def view_rule(row: dict, view: View, model_gate: bool) -> tuple[str, bool]:
    """Per-view entry rule: the causal quote/depth gate, optionally the real spread bar.

    Without the model gate the head-independent intent reason IS the rule (no
    synthetic prediction is ever invented). With the gate, the published rule adds
    ``spread_bps + 25 <= 10000 * pred`` at the head-matched real forecast; the h30
    view has no stored head, so a missing real forecast is a causal skip.
    """
    facts = state_facts(row)
    if not model_gate:
        reason = str(row["intent_reason"])
        return reason, reason == "eligible"
    pred = row.get(f"pred_{view.head}")
    if pred is None:
        return "model_forecast_unavailable", False
    return aqf.entry_rule_status(facts, float(pred))


def net_usd(quantity: int, exit_bid: float, entry_ask: float, cost_bps: float) -> float:
    """Realized dollars of the causal round trip at one residual rung (fees both legs)."""
    side = cost_bps / 20_000.0
    return float(quantity) * (float(exit_bid) * (1.0 - side) - float(entry_ask) * (1.0 + side))


def resolve_trade(
    day: str,
    r: dict,
    view: View,
    sq: aqf.SymbolQuotes,
    session_end: int,
    session_end_us: int,
) -> dict:
    """Resolve one funded intent: the one-shot IOC limit entry, then the market exit."""
    head = view.head
    entry_minute = int(r["entry_minute"])
    intent_us = int(r["intent_us"])
    record = {
        "day": day,
        "view_key": view.key,
        "qual": view.qual,
        "head": head,
        "ticker": r["ticker"],
        "entry_minute": entry_minute,
        "decision_minute": int(r["decision_minute"]),
        "signal_bar_minute": int(r["signal_bar_minute"]),
        "signal_minute": int(r["decision_minute"]),
        "breakout_minute": int(r["breakout_minute"]),
        "intent_us": intent_us,
        "limit_price": float(r["limit_price"]),
        "qty": int(r["qty"]),
        "reserved_usd": float(r["reserved_usd"]),
        "attempt_index": 1,
        "positions_at_entry": None,
        "entry_status": None,
        "entry_ask": None,
        "entry_bid": None,
        "entry_age_s": None,
        "entry_spread_bps": None,
        "entry_quote_us": None,
        "entry_arrival_us": aqf.arrival_us(intent_us),
        "exit_minute": min(entry_minute + head, int(session_end)),
        "exit_submit_us": None,
        "exit_submit_status": None,
        "exit_arrival_us": None,
        "exit_bid": None,
        "exit_age_s": None,
        "exit_spread_bps": None,
        "exit_quote_us": None,
        "exit_status": None,
        "actual_exit_us": None,
        "fill_status": None,
        "touch_gross_bps": None,
        "proxy_gross": r.get(f"proxy_gross_{head}"),
        "proxy_entry_status": r.get(f"proxy_status_{head}"),
        "signal_ret": float(r["signal_ret"]),
        "volume_ratio": r.get("volume_ratio"),
        "range_ratio": r.get("range_ratio"),
    }
    for rung in RUNG_COSTS:
        record[f"net_usd_{int(rung)}"] = None

    # ---- entry leg: the causal IOC limit is evaluated ONCE at the arrival clock.
    entry_arrival = record["entry_arrival_us"]
    # the published IOC leg (never rests into a later print); this study keeps only
    # its own limit-match and arrival-depth consumer rules below.
    eq, estat = aqf.entry_price_leg(sq, entry_arrival)
    if eq is None:
        record["entry_status"] = estat
        record["fill_status"] = "unknown_entry_execution"
        record["exit_status"] = estat
        record["actual_exit_us"] = session_end_us + 1
        return record
    record["entry_ask"] = float(eq["ask"])
    record["entry_bid"] = float(eq["bid"])
    record["entry_age_s"] = float(eq["age_s"])
    record["entry_spread_bps"] = float(eq["spread_bps"]) if eq["spread_bps"] is not None else None
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

    # ---- exit leg: fresh-quote-gated MARKET submission, +250ms after the submission.
    due_us = aqf.minute_us(day, record["exit_minute"])
    submit_us, submit_status = aqf.submission_clock(sq, due_us, session_end_us)
    record["exit_submit_us"] = submit_us
    record["exit_submit_status"] = submit_status
    if submit_us is None:
        record["fill_status"] = "unknown_exit_execution"
        record["exit_status"] = "unknown_exit_no_valid_regular_quote"
        record["actual_exit_us"] = session_end_us + 1
        return record
    exit_arrival = aqf.arrival_us(submit_us)
    record["exit_arrival_us"] = exit_arrival
    xq, xstat, priced_at = aqf.priced_leg(sq, exit_arrival, session_end_us)
    if xq is None:
        record["fill_status"] = "unknown_exit_execution"
        record["exit_status"] = xstat
        record["actual_exit_us"] = session_end_us + 1
        return record
    record["exit_bid"] = float(xq["bid"])
    record["exit_age_s"] = float(xq["age_s"])
    record["exit_spread_bps"] = float(xq["spread_bps"]) if xq["spread_bps"] is not None else None
    record["exit_quote_us"] = int(xq["quote_us"])
    if float(xq["bid_shares"]) < record["qty"]:
        record["fill_status"] = "unknown_exit_execution"
        record["exit_status"] = "unknown_exit_partial_depth"
        record["actual_exit_us"] = session_end_us + 1
        return record
    record["exit_status"] = "conditional_market_fill"
    record["fill_status"] = "conditional_fill"
    # The funding/cooldown clock is the clock the price was ACTUALLY observed at:
    # the arrival when the latest raw state was fresh, or the first eligible print's
    # own timestamp when the market order had already rested. The quote's own stamp
    # is kept for disclosure but is never used as the fill clock (it can predate the
    # arrival), so cash/slot release can never be lookahead-early.
    record["actual_exit_us"] = int(priced_at if priced_at is not None else exit_arrival)
    for rung in RUNG_COSTS:
        record[f"net_usd_{int(rung)}"] = net_usd(
            record["qty"], record["exit_bid"], record["entry_ask"], rung
        )
    record["touch_gross_bps"] = (record["exit_bid"] / record["entry_ask"] - 1.0) * 10_000.0
    return record


# ----- per-day state artifacts (incremental, resumable) -----------------------
STATE_COLUMNS = (
    "day",
    "ticker",
    "qual",
    "entry_minute",
    "decision_minute",
    "signal_bar_minute",
    "signal_minute",
    "breakout_minute",
    "admit_t",
    "session_end",
    "or_high",
    "or_low",
    "or_range",
    "or_volume",
    "or_bars",
    "signal_open",
    "signal_high",
    "signal_low",
    "signal_close",
    "signal_volume",
    "signal_ret",
    "vwap_signal",
    "vwap_dist_signal",
    "cum_dv_signal",
    "breakout_open",
    "breakout_close",
    "breakout_volume",
    "breakout_ret",
    "breakout_range",
    "range_ratio",
    "volume_ratio",
    "retest_depth",
    "proxy_gross_15",
    "proxy_gross_30",
    "proxy_gross_60",
    "proxy_status_15",
    "proxy_status_30",
    "proxy_status_60",
    "first_pullback",
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
    "pred_15",
    "pred_60",
    "panel_clock",
)
STATE_TYPES = {
    "day": pl.String,
    "ticker": pl.String,
    "qual": pl.String,
    "entry_minute": pl.Int64,
    "decision_minute": pl.Int64,
    "signal_bar_minute": pl.Int64,
    "signal_minute": pl.Int64,
    "breakout_minute": pl.Int64,
    "admit_t": pl.Int64,
    "session_end": pl.Int64,
    "or_high": pl.Float64,
    "or_low": pl.Float64,
    "or_range": pl.Float64,
    "or_volume": pl.Float64,
    "or_bars": pl.Int64,
    "signal_open": pl.Float64,
    "signal_high": pl.Float64,
    "signal_low": pl.Float64,
    "signal_close": pl.Float64,
    "signal_volume": pl.Float64,
    "signal_ret": pl.Float64,
    "vwap_signal": pl.Float64,
    "vwap_dist_signal": pl.Float64,
    "cum_dv_signal": pl.Float64,
    "breakout_open": pl.Float64,
    "breakout_close": pl.Float64,
    "breakout_volume": pl.Float64,
    "breakout_ret": pl.Float64,
    "breakout_range": pl.Float64,
    "range_ratio": pl.Float64,
    "volume_ratio": pl.Float64,
    "retest_depth": pl.Float64,
    "proxy_gross_15": pl.Float64,
    "proxy_gross_30": pl.Float64,
    "proxy_gross_60": pl.Float64,
    "proxy_status_15": pl.String,
    "proxy_status_30": pl.String,
    "proxy_status_60": pl.String,
    "first_pullback": pl.Boolean,
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
    "pred_15": pl.Float64,
    "pred_60": pl.Float64,
    "panel_clock": pl.Int64,
}


def empty_states() -> pl.DataFrame:
    return pl.DataFrame(schema={c: STATE_TYPES[c] for c in STATE_COLUMNS})


def forecast_rows(panel: pl.DataFrame, signals: list[dict], models: dict) -> list[dict | None]:
    """The REAL stored forecast for one signal: the panel row at the last 5-minute
    clock at or before the entry minute (features strictly past; no synthetic score).

    A missing panel row yields None: the model gate then skips the signal instead of
    inventing a prediction.
    """
    if not signals:
        return []
    ctx = day_context(panel)
    index: dict[tuple[str, int], dict] = {}
    for row in ctx.iter_rows(named=True):
        index[(str(row["ticker"]), int(row["t"]))] = row
    rows: list[dict | None] = []
    for sig in signals:
        clock = (int(sig["entry_minute"]) // 5) * 5
        row = index.get((sig["ticker"], clock))
        rows.append(row if row is not None and clock <= int(sig["entry_minute"]) else None)
    matched = [i for i, r in enumerate(rows) if r is not None]
    if not matched:
        return rows
    matrix = pl.DataFrame([rows[i] for i in matched])
    preds = {
        head: np.asarray(models[head]["lgbm"].predict(feature_matrix(matrix)), dtype=float)
        for head in STORED_HEADS
    }
    for k, i in enumerate(matched):
        rows[i] = {
            "panel_clock": int(rows[i]["t"]),
            "pred_15": float(preds[15][k]),
            "pred_60": float(preds[60][k]),
        }
    return rows


def score_day(
    day: str, models: dict, model_gate: bool, data_root: Path, supplemental_roots: tuple[Path, ...]
) -> tuple[pl.DataFrame, dict]:
    """One session's watchlist states: both frozen qualifications plus intent facts.

    One panel frame, one bars frame, one quote frame and one day's states are resident
    at a time; the full corpus is never concatenated. Every qualification field uses
    bars strictly before the entry minute; the intent observation is the latest RAW
    state at the entry-minute clock.
    """
    if not allowed(day):
        raise ValueError(f"protected/out-of-scope day refused: {day}")
    if any(day.startswith(p) for p in PROTECTED_UNREAD):
        raise ValueError(f"protected outcome month refused: {day}")
    panel_path = PANEL_DAYS / f"{day}.parquet"
    if not panel_path.exists():
        raise ValueError(f"panel day missing: {panel_path}")
    bars_path = data_root / "sip" / "net" / "bars" / f"{day}.parquet"
    if not bars_path.exists():
        raise SystemExit(f"[{STUDY}] bars day file missing: {bars_path}")

    panel = pl.read_parquet(panel_path)
    cov = {
        "panel_rows": int(panel.height),
        "watchlist_tickers": 0,
        "tickers_with_tape": 0,
        "tickers_insufficient_first_15m": 0,
        "tickers_without_open_anchor": 0,
        "tickers_with_breakout": 0,
        "signals_pullback_retest": 0,
        "signals_direct_breakout": 0,
        "qualification_reasons": {},
        "tickers_with_quote_stream": 0,
        "quote_day_file_present": True,
        "intent_status_counts": {},
        "intent_reason_counts": {},
        "model_gate": bool(model_gate),
        "model_forecasts_available": 0,
        "model_forecasts_missing": 0,
    }
    if not panel.height:
        # an empty panel day is a real reportable state (no watchlist, no tape, no
        # intent), so it returns an empty state frame and the zeroed coverage - never
        # a crash on an unbound coverage dict
        cov["session_end"] = None
        return empty_states(), cov
    watch = (
        panel.group_by("ticker").agg(pl.col("admit_t").min()).sort("ticker").drop_nulls("admit_t")
    )
    session_end = int(panel["session_end"][0])
    cov["watchlist_tickers"] = int(watch.height)
    if not watch.height:
        return empty_states(), cov

    tape_frame = (
        pl.scan_parquet(bars_path)
        .filter(pl.col("ticker").is_in(watch["ticker"].to_list()))
        .collect()
        .sort("ticker", "et")
    )
    quote_frame = aqf.quote_day_frame(
        data_root, day, set(watch["ticker"].to_list()), supplemental_roots
    )
    streams = aqf.symbol_quotes(quote_frame, day)
    cov["tickers_with_quote_stream"] = len(streams)
    if not day_quote_path(data_root, day).exists():
        cov["quote_day_file_present"] = False
    del quote_frame

    admit = {str(t): int(a) for t, a in watch.iter_rows()}
    signals: list[dict] = []
    reasons: Counter = Counter()
    for (ticker,), part in tape_frame.partition_by("ticker", as_dict=True).items():
        ticker = str(ticker)
        tape = build_tape(day, ticker, part)
        if tape is None:
            cov["tickers_without_open_anchor"] += 1
            reasons["no_open_anchor"] += 1
            continue
        cov["tickers_with_tape"] += 1
        sigs, ticker_reasons = tape_signals(tape, admit[ticker], session_end)
        reasons.update(ticker_reasons)
        if ticker_reasons.get("direct_signal") or ticker_reasons.get("pullback_signal"):
            cov["tickers_with_breakout"] += 1
        if ticker_reasons.get("insufficient_first_15m_bars"):
            cov["tickers_insufficient_first_15m"] += 1
        for sig in sigs:
            sig["day"] = day
            sig["ticker"] = ticker
            signals.append(sig)
    cov["signals_pullback_retest"] = int(reasons.get("pullback_signal", 0))
    cov["signals_direct_breakout"] = int(reasons.get("direct_signal", 0))
    cov["qualification_reasons"] = dict(reasons)
    del tape_frame

    if not signals:
        return empty_states(), cov

    if model_gate:
        preds = forecast_rows(panel, signals, models)
        if len(preds) != len(signals):
            raise SystemExit(
                f"[{STUDY}] forecast rows {len(preds)} != signals {len(signals)} on {day}"
            )
        for sig, pred in zip(signals, preds, strict=True):
            if pred is None:
                sig.update({"pred_15": None, "pred_60": None, "panel_clock": None})
                cov["model_forecasts_missing"] += 1
            else:
                sig.update(pred)
                cov["model_forecasts_available"] += 1
    else:
        for sig in signals:
            sig.update({"pred_15": None, "pred_60": None, "panel_clock": None})

    intent_counts: Counter = Counter()
    reason_counts: Counter = Counter()
    for sig in signals:
        intent_us = aqf.minute_us(day, int(sig["entry_minute"]))
        sig["intent_us"] = intent_us
        facts = intent_facts(streams.get(sig["ticker"]), intent_us)
        sig.update(facts)
        intent_counts[facts["intent_status"]] += 1
        reason_counts[facts["intent_reason"]] += 1
    cov["intent_status_counts"] = dict(intent_counts)
    cov["intent_reason_counts"] = dict(reason_counts)

    frame = pl.DataFrame(
        {c: [rec[c] for rec in signals] for c in STATE_COLUMNS},
        schema={c: STATE_TYPES[c] for c in STATE_COLUMNS},
    )
    return frame, cov


def state_paths(out_dir: Path, day: str) -> tuple[Path, Path]:
    return out_dir / "states" / f"{day}.parquet", out_dir / "states" / f"{day}.cov.json"


def score_block(
    out_dir: Path,
    models: dict,
    model_gate: bool,
    days: list[str],
    resume: bool,
    supplemental_roots: tuple[Path, ...],
) -> dict:
    """Build (or resume) every day's watchlist states atomically, one day at a time."""
    producer = producer_sha256()
    contract_sha = digest_bytes(CONTRACT)
    sup_digest, sup_entries = aqf.supplement_digest(supplemental_roots)
    model_shas = {}
    for head in STORED_HEADS:
        path = MODEL_DIR / f"payoff_h{head}.joblib"
        model_shas[f"h{head}"] = aqf.sha256_file(path) if path.exists() else None
    todo, infos = [], []
    coverage = {
        "days_total": len(days),
        "days_cached": 0,
        "days_scored": 0,
        "signals_pullback_retest": 0,
        "signals_direct_breakout": 0,
        "intent_status_counts": {},
        "intent_reason_counts": {},
        "missing_quote_day_files": [],
        "days_without_quote_stream": [],
    }
    intent_totals: Counter = Counter()
    reason_totals: Counter = Counter()
    for day in days:
        qpath, cpath = state_paths(out_dir, day)
        expect = digest_bytes(
            {
                "day": day,
                "producer": producer,
                "contract": contract_sha,
                "supplements": sup_digest,
                "models": model_shas,
                "model_gate": bool(model_gate),
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
        states, cov = score_day(day, models, model_gate, DATA_ROOT, supplemental_roots)
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
                "resume_hash": digest_bytes(
                    {
                        "day": day,
                        "producer": producer,
                        "contract": contract_sha,
                        "supplements": sup_digest,
                        "models": model_shas,
                        "model_gate": bool(model_gate),
                        "schema": STATES_SCHEMA,
                    }
                ),
                "producer_sha256": producer,
                "contract_sha256": contract_sha,
                "supplement_digest": sup_digest,
                "states_sha256": aqf.sha256_file(qpath),
                "rows_schema": STATES_SCHEMA,
                "model_gate": bool(model_gate),
                "coverage": cov,
            },
        )
        infos.append({"day": day, "resumed": False, "rows": int(states.height), "coverage": cov})
        if n % 25 == 0 or n == len(todo):
            print(
                f"[score] {n}/{len(todo)} days, last {day}, "
                f"pullback_signals={cov['signals_pullback_retest']} "
                f"direct_signals={cov['signals_direct_breakout']} "
                f"quoted_streams={cov['tickers_with_quote_stream']}",
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
                        "states_sha256": aqf.sha256_file(qpath),
                        "producer_sha256": producer,
                    },
                    default=_default,
                ),
                flush=True,
            )
    for info in infos:
        cov = info["coverage"] or {}
        intent_totals.update(cov.get("intent_status_counts") or {})
        reason_totals.update(cov.get("intent_reason_counts") or {})
        if cov.get("quote_day_file_present") is False:
            coverage["missing_quote_day_files"].append(info["day"])
        if (
            int(cov.get("tickers_with_quote_stream", 0)) == 0
            and int(cov.get("watchlist_tickers", 0)) > 0
        ):
            coverage["days_without_quote_stream"].append(info["day"])
    coverage.update(
        {
            "days_cached": len(infos),
            "days_scored": len(todo),
            "signals_pullback_retest": sum(
                int((i["coverage"] or {}).get("signals_pullback_retest", 0)) for i in infos
            ),
            "signals_direct_breakout": sum(
                int((i["coverage"] or {}).get("signals_direct_breakout", 0)) for i in infos
            ),
            "intent_status_counts": dict(intent_totals),
            "intent_reason_counts": dict(reason_totals),
            "supplements": sup_entries,
            "supplement_digest": sup_digest,
            "model_gate": bool(model_gate),
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
    "qual",
    "head",
    "signals",
    "intents_funded",
    "attempts",
    "fills",
    "known_fills",
    "unknown_fills",
    "no_match_fills",
    "known_usd_0",
    "known_usd_5",
    "known_usd_10",
    "known_usd_25",
    "known_usd_50",
    "known_usd_75",
    "known_usd_100",
    "known_usd_125",
    "known_usd_150",
    "lower_bound_usd_0",
    "lower_bound_usd_5",
    "lower_bound_usd_10",
    "lower_bound_usd_25",
    "lower_bound_usd_50",
    "lower_bound_usd_75",
    "lower_bound_usd_100",
    "lower_bound_usd_125",
    "lower_bound_usd_150",
    "peak_reserved_usd",
    "positions_peak",
    "positions_mean",
    "no_order_min_capital",
    "quote_not_acquired",
    "intent_not_firm_fresh",
    "intent_depth_unsupported",
    "intent_depth_unknown",
    "spread_above_prediction",
    "model_forecast_unavailable",
    "overlap_skips",
    "max_attempt_skips",
    "cash_or_slot_skips",
)
DAILY_TYPES = {
    "day": pl.String,
    "view_key": pl.String,
    "qual": pl.String,
    "head": pl.Int64,
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
    "model_forecast_unavailable": pl.Int64,
    "overlap_skips": pl.Int64,
    "max_attempt_skips": pl.Int64,
    "cash_or_slot_skips": pl.Int64,
}
for _rung in RUNG_COSTS:
    DAILY_COLUMNS += (f"known_usd_{int(_rung)}", f"lower_bound_usd_{int(_rung)}")
    DAILY_TYPES[f"known_usd_{int(_rung)}"] = pl.Float64
    DAILY_TYPES[f"lower_bound_usd_{int(_rung)}"] = pl.Float64

SKIP_REASONS = (
    "no_order_min_capital",
    "quote_not_acquired",
    "intent_not_firm_fresh",
    "intent_depth_unsupported",
    "intent_depth_unknown",
    "spread_above_prediction",
    "model_forecast_unavailable",
    "overlap_skips",
    "max_attempt_skips",
    "cash_or_slot_skips",
)

TRADE_COLUMNS = (
    "day",
    "view_key",
    "qual",
    "head",
    "ticker",
    "entry_minute",
    "decision_minute",
    "signal_bar_minute",
    "signal_minute",
    "breakout_minute",
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
    "entry_spread_bps",
    "entry_quote_us",
    "entry_arrival_us",
    "exit_minute",
    "exit_submit_us",
    "exit_submit_status",
    "exit_arrival_us",
    "exit_bid",
    "exit_age_s",
    "exit_spread_bps",
    "exit_quote_us",
    "exit_status",
    "actual_exit_us",
    "fill_status",
    "touch_gross_bps",
    "proxy_gross",
    "proxy_entry_status",
    "signal_ret",
    "volume_ratio",
    "range_ratio",
)
TRADE_TYPES = {
    "day": pl.String,
    "view_key": pl.String,
    "qual": pl.String,
    "head": pl.Int64,
    "ticker": pl.String,
    "entry_minute": pl.Int64,
    "decision_minute": pl.Int64,
    "signal_bar_minute": pl.Int64,
    "signal_minute": pl.Int64,
    "breakout_minute": pl.Int64,
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
    "entry_spread_bps": pl.Float64,
    "entry_quote_us": pl.Int64,
    "entry_arrival_us": pl.Int64,
    "exit_minute": pl.Int64,
    "exit_submit_us": pl.Int64,
    "exit_submit_status": pl.String,
    "exit_arrival_us": pl.Int64,
    "exit_bid": pl.Float64,
    "exit_age_s": pl.Float64,
    "exit_spread_bps": pl.Float64,
    "exit_quote_us": pl.Int64,
    "exit_status": pl.String,
    "actual_exit_us": pl.Int64,
    "fill_status": pl.String,
    "touch_gross_bps": pl.Float64,
    "proxy_gross": pl.Float64,
    "proxy_entry_status": pl.String,
    "signal_ret": pl.Float64,
    "volume_ratio": pl.Float64,
    "range_ratio": pl.Float64,
}
for _rung in RUNG_COSTS:
    TRADE_COLUMNS += (f"net_usd_{int(_rung)}",)
    TRADE_TYPES[f"net_usd_{int(_rung)}"] = pl.Float64


def empty_daily() -> pl.DataFrame:
    return pl.DataFrame(schema={c: DAILY_TYPES[c] for c in DAILY_COLUMNS})


def empty_trades() -> pl.DataFrame:
    return pl.DataFrame(schema={c: TRADE_TYPES[c] for c in TRADE_COLUMNS})


def replay_day(
    day: str,
    states: pl.DataFrame,
    streams: dict[str, aqf.SymbolQuotes],
    view: View,
    model_gate: bool,
) -> tuple[dict, list[dict]]:
    """One view, one session: one-shot IOC entries under the funded $750 book.

    Chronological by entry minute; the same-minute intents are funded (up to the slot
    and cash limits, in ticker order) BEFORE any arrival outcome is seen, so a fourth
    same-clock candidate is never substituted after an unfilled one. An UNKNOWN or
    filled position keeps its slot and its ticker until its ACTUAL resolution; a
    no-match arrival releases the ticket at the arrival clock.
    """
    daily = dict.fromkeys(DAILY_COLUMNS, 0)
    daily.update(
        {
            "day": day,
            "view_key": view.key,
            "qual": view.qual,
            "head": view.head,
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
    session_end_us = aqf.minute_us(day, session_end)
    candidates = states.filter(pl.col("qual") == view.qual).sort(
        ["entry_minute", "ticker"], descending=[False, False]
    )
    groups: dict[int, list[dict]] = {}
    for r in candidates.iter_rows(named=True):
        groups.setdefault(int(r["entry_minute"]), []).append(r)
    daily["signals"] = int(candidates.height)

    cash, active = float(BOOK), set()
    attempts: dict[str, int] = {}
    queue: list[tuple[int, int, str, float]] = []
    seq = 0
    trades: list[dict] = []
    positions_samples: list[int] = []
    reserved_total = 0.0
    for minute in sorted(groups):
        intent_us = aqf.minute_us(day, minute)
        while queue and queue[0][0] <= intent_us:
            _, _, sym, proceeds = heapq.heappop(queue)
            active.discard(sym)
            cash += proceeds
        admitted: list[dict] = []
        for r in groups[minute]:
            reason, ok = view_rule(r, view, model_gate)
            if not ok:
                if reason not in SKIP_REASONS:
                    raise SystemExit(
                        f"[{STUDY}] unmapped entry-rule skip reason {reason!r} for "
                        f"{r['ticker']} on {day}"
                    )
                daily[reason] += 1
                continue
            sym = r["ticker"]
            if sym in active:
                daily["overlap_skips"] += 1
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
        # Every intent of this clock is funded before any arrival outcome is observed.
        for r in admitted:
            record = resolve_trade(day, r, view, streams[r["ticker"]], session_end, session_end_us)
            record["positions_at_entry"] = len(active)
            record["attempt_index"] = int(attempts[r["ticker"]])
            status = record["fill_status"]
            release = int(record["actual_exit_us"])
            if status == "no_match_at_l1_unfilled_cash":
                # No position was opened: the ticket is released at the arrival clock
                # (the first-attempt label still consumed this ticker-day's attempt).
                daily["no_match_fills"] += 1
            else:
                daily["unknown_fills" if "unknown" in status else "fills"] += 1
                if status == "conditional_fill":
                    daily["known_fills"] += 1
                    for rung in RUNG_COSTS:
                        daily[f"known_usd_{int(rung)}"] += float(record[f"net_usd_{int(rung)}"])
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
    model_gate: bool,
    resume: bool,
    supplemental_roots: tuple[Path, ...],
) -> dict:
    """Replay every view on every day of one block, writing resumable per-day parts."""
    producer = producer_sha256()
    contract_sha = digest_bytes(CONTRACT)
    sup_digest, _ = aqf.supplement_digest(supplemental_roots)
    model_shas = {}
    for head in STORED_HEADS:
        path = MODEL_DIR / f"payoff_h{head}.joblib"
        model_shas[f"h{head}"] = aqf.sha256_file(path) if path.exists() else None
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
                states_sha = aqf.sha256_file(spath)
        expect = digest_bytes(
            {
                "day": day,
                "producer": producer,
                "contract": contract_sha,
                "supplements": sup_digest,
                "models": model_shas,
                "model_gate": bool(model_gate),
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
                f"[{STUDY}] scored states missing for {day} ({states_path}); run the "
                "score stage for the whole block before replaying it"
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
            "signals": int(states.height) if states.height else 0,
            "quote_streams": len(streams),
            "views": {},
        }
        for view in VIEWS:
            daily, trades = replay_day(day, states, streams, view, model_gate)
            daily_rows.append(daily)
            trade_rows.extend(trades)
            day_cov["views"][view.key] = {
                "signals": daily["signals"],
                "attempts": daily["attempts"],
                "fills": daily["fills"],
                "unknown_fills": daily["unknown_fills"],
                "no_match_fills": daily["no_match_fills"],
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
                "model_gate": bool(model_gate),
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
        coverage["signals"] += int(cov.get("signals", 0))
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


# ----- block aggregation ------------------------------------------------------
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
            "mean_daily_known_usd_25": float(rows["known_usd_25"].mean()),
        }
    return out


def view_cell(daily: pl.DataFrame, trades: pl.DataFrame, view: View, days: list[str]) -> dict:
    """Every reported number for one (qualification, hold) view on one block's calendar."""
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
        "qual": view.qual,
        "head": view.head,
        "days_replayed": n_days,
        "signals": int(rows["signals"].sum()),
        "intents_funded": int(rows["intents_funded"].sum()),
        "attempts": int(rows["attempts"].sum()),
        "fills": int(rows["fills"].sum()),
        "known_fills": int(rows["known_fills"].sum()),
        "unknown_fills": int(rows["unknown_fills"].sum()),
        "no_match_fills": int(rows["no_match_fills"].sum()),
        "traded_days": int((rows["attempts"] > 0).sum()),
        "traded_months": len(monthly_view(rows)),
        "known_fills_per_year_252": float(rows["known_fills"].sum()) * ANNUAL_SESSIONS / n_days,
        "attempts_per_year_252": float(rows["attempts"].sum()) * ANNUAL_SESSIONS / n_days,
        "underpowered_known_fills": bool(int(rows["known_fills"].sum()) < UNDERPOWERED_KNOWN_FILLS),
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
            f"${int(BOOK)} nominal book; a simple session-count convention, NOT a CAGR "
            "and NOT an account claim"
        ),
        "cost_basis": (
            "the observed ASK entry / BID exit prices already pay the NBBO spread; the "
            "residual ladder adds provider fees/slippage on both legs separately"
        ),
        "bootstrap_daily_known_usd": bootstrap,
        "bootstrap_daily_return_on_book": series_book,
        "skip_reasons": {reason: int(rows[reason].sum()) for reason in SKIP_REASONS},
        "monthly": monthly_view(rows),
        "yearly": yearly_view(rows),
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
            "observed_entry_price_usd_mean": (
                float(np.mean(entry_asks)) if len(entry_asks) else None
            ),
            "observed_exit_price_usd_mean": float(np.mean(exit_bids)) if len(exit_bids) else None,
            "observed_entry_spread_bps_mean": (
                float(filled["entry_spread_bps"].mean()) if filled.height else None
            ),
            "observed_exit_spread_bps_mean": (
                float(filled["exit_spread_bps"].mean()) if filled.height else None
            ),
            "mean_touch_gross_bps": (
                float(filled["touch_gross_bps"].mean()) if filled.height else None
            ),
            "mean_proxy_gross_minute_open_basis": (
                float(filled["proxy_gross"].mean()) if filled.height else None
            ),
            "mean_signal_ret": float(filled["signal_ret"].mean()) if filled.height else None,
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
            "observed_entry_price_usd_mean": None,
            "observed_exit_price_usd_mean": None,
            "observed_entry_spread_bps_mean": None,
            "observed_exit_spread_bps_mean": None,
            "mean_touch_gross_bps": None,
            "mean_proxy_gross_minute_open_basis": None,
            "mean_signal_ret": None,
            "fill_status_counts": {},
            "exit_submit_status_counts": {},
            "mean_hold_minutes": None,
        }
    return cell


def aggregate_block(out_dir: Path, block: str, days: list[str]) -> tuple[dict, dict]:
    """The full six-view surface of one block, plus the block's data coverage."""
    daily, trades = read_block_frames(out_dir, block)
    surface = {view.key: view_cell(daily, trades, view, days) for view in VIEWS}
    coverage = {
        "block": block,
        "days": len(days),
        "signals": int(daily["signals"].sum()),
        "intents_funded": int(daily["intents_funded"].sum()),
        "attempts": int(daily["attempts"].sum()),
        "fills": int(daily["fills"].sum()),
        "known_fills": int(daily["known_fills"].sum()),
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
            "qual": v.qual,
            "head": v.head,
            "validation_known_usd_per_calendar_day_25": surface[v.key][
                "known_usd_per_calendar_day"
            ]["25"],
            "known_fills": surface[v.key]["known_fills"],
            "unknown_fills": surface[v.key]["unknown_fills"],
            "no_match_fills": surface[v.key]["no_match_fills"],
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
            f"NO_ACTUAL_TOUCH_POSITIVE_FORMULATION: the best of the six views "
            f"({chosen.label}) measures {objective:+.2f} $/calendar day of KNOWN "
            f"contribution on 2023 validation at 25bps over {fills} known fills "
            f"(day bootstrap p>0 = {boot.get('p_gt_zero')}). All six views are measured "
            "and none is positive, so the retained rare h60 lead stays the reference; the "
            "unknown share is reported beside this number and is NOT assumed zero."
        )
    text = (
        f"{STATUS}: chosen {chosen.label} (qualification {chosen.qual}, hold "
        f"{chosen.head}m) by 2023 validation actual-touch known contribution: "
        f"{objective:+.2f} $/calendar day at 25bps = "
        f"{val['dollars_per_year_252_on_book_750']['25']:+.0f} $/year on the "
        f"${int(BOOK)} nominal book (simple 252-session convention, not a CAGR), "
        f"{fills} known fills on {val['traded_days']} traded days of {val['days_replayed']} "
        f"calendared days, {val['unknown_fills']} UNKNOWN executions "
        f"({100.0 * val['unknown_fills'] / max(1, val['attempts']):.1f}% of attempts) and "
        f"{val['no_match_fills']} no-match unfilled intents. Day bootstrap p>0 = "
        f"{boot.get('p_gt_zero')}. This is the measured KNOWN-contribution expectation on "
        "a PARTIAL basis (unknowns excluded from the numerator), not an actual account "
        "CAGR; the zero/low-fee diagnostic rungs are reported beside the 25bps primary."
    )
    if frozen and late is not None:
        late_obj = late["known_usd_per_calendar_day"]["25"]
        text += (
            f" The frozen late block measures {late_obj:+.2f} $/calendar day at 25bps over "
            f"{late['known_fills']} known fills of {late['attempts']} attempts on the "
            "previously explored 2025-09..2026-05 window; the choice was frozen before any "
            "late file was read, so this is confirmation of an already-frozen decision, "
            "not a re-selection."
        )
    elif not frozen:
        text += " Late block not run (--skip-late)."
    return text


def provenance_block(
    out_dir: Path,
    days_val: list[str],
    days_late: list[str] | None,
    days_dev: list[str] | None,
    supplemental_roots: tuple[Path, ...],
    val_cov: dict,
    late_cov: dict | None,
    dev_cov: dict | None,
) -> dict:
    sup_digest, sup_entries = aqf.supplement_digest(supplemental_roots)
    out = {
        "producer_sha256": producer_sha256(),
        "producer_script": str(Path(__file__).resolve()),
        "contract_sha256": digest_bytes(CONTRACT),
        "panel_root": str(PANEL_ROOT),
        "panel_days_root": str(PANEL_DAYS),
        "panel_contract_sha256": (
            aqf.sha256_file(PANEL_CONTRACT) if PANEL_CONTRACT.exists() else None
        ),
        "quote_cache_root": str(DATA_ROOT / "sip" / "net" / "quotes"),
        "bars_root": str(BARS_ROOT),
        "quote_cache_days_present": sum(
            1
            for d in days_val + list(days_late or []) + list(days_dev or [])
            if (DATA_ROOT / "sip" / "net" / "quotes" / f"{d}.parquet").exists()
        ),
        "bars_days_present": sum(
            1
            for d in days_val + list(days_late or []) + list(days_dev or [])
            if (BARS_ROOT / f"{d}.parquet").exists()
        ),
        "supplemental_roots": [str(r) for r in supplemental_roots],
        "supplemental_roots_existing": [str(r) for r in supplemental_roots if r.exists()],
        "supplemental_files": sup_entries,
        "supplement_digest": sup_digest,
        "published_helper": {
            "module": "alpha_quote_aware_frequency",
            "sha256": aqf.sha256_file(Path(aqf.__file__).resolve()),
            "role": "quote substrate, causal sizing, fresh-quote-gated MARKET exit, "
            "published entry-rule helper; imported, never edited",
        },
        "model_dir": str(MODEL_DIR),
        "models": {
            f"h{head}": {
                "path": str(MODEL_DIR / f"payoff_h{head}.joblib"),
                "sha256": (
                    aqf.sha256_file(MODEL_DIR / f"payoff_h{head}.joblib")
                    if (MODEL_DIR / f"payoff_h{head}.joblib").exists()
                    else None
                ),
            }
            for head in STORED_HEADS
        },
        "validation_days": len(days_val),
        "late_days": (
            len(days_late)
            if days_late is not None
            else "not_yet_inspected: the freeze precedes any late file access"
        ),
        "development_days": len(days_dev) if days_dev is not None else None,
        "coverage_validation": val_cov,
        "coverage_late": late_cov,
        "coverage_development": dev_cov,
        "output_root": str(out_dir),
    }
    return out


# ----- run --------------------------------------------------------------------
def block_days(days: list[str] | None, block: str) -> list[str]:
    """All allowed panel days of one research block (``days`` restricts for smoke runs)."""
    keep = set(days) if days else None
    out = []
    for path in sorted(PANEL_DAYS.glob("????-??-??.parquet")):
        day = path.stem
        if not allowed(day):
            continue
        if block == VAL_BLOCK:
            if period(day) != "validation":
                continue
        elif block == LATE_BLOCK:
            if period(day) != "confirmation" or day < LATE_FROM:
                continue
        elif block == DEV_BLOCK:
            if not (DEV_FROM <= day <= DEV_TO):
                continue
        else:
            raise ValueError(f"unknown block {block!r}")
        if keep is not None and day not in keep:
            continue
        out.append(day)
    return out


def load_stored_models() -> tuple[dict, dict]:
    """Load the two immutable stored heads through the published loader (no refit)."""
    return aqf.load_stored_models()


def run(args: argparse.Namespace) -> None:
    t0 = time.time()

    out = args.out or OUTPUT
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")

    model_gate = bool(args.with_model_gate)
    models: dict = {}
    model_report: dict = {}
    if model_gate:
        models, model_report = load_stored_models()
        print(
            "[model] optional real-forecast gate ON (no refit): "
            + ", ".join(
                f"h{h} sha256 {model_report[f'h{h}']['sha256'][:12]}" for h in STORED_HEADS
            ),
            flush=True,
        )
    else:
        model_report = {
            "gate_enabled": False,
            "note": "rules engine only: no model score is used or invented; "
            "--with-model-gate turns on the published real-forecast spread bar",
        }

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

    # Preregistration: the frozen contract is written BEFORE any outcome file is read.
    (out / "contract.json").write_text(json.dumps(CONTRACT, indent=1, default=_default) + "\n")
    print(
        f"[preregister] contract -> {out / 'contract.json'} "
        f"({len(VIEWS)} views x {len(RUNG_COSTS)} rungs, before any outcome)",
        flush=True,
    )

    days_val = block_days(args.days, VAL_BLOCK)
    if args.days is None and len(days_val) != EXPECTED_DAYS[VAL_BLOCK]:
        raise SystemExit(
            f"[{STUDY}] requires {EXPECTED_DAYS[VAL_BLOCK]} {VAL_BLOCK} days, got {len(days_val)}"
        )
    if not days_val:
        raise SystemExit(f"[{STUDY}] no {VAL_BLOCK} days selected")

    # 1) validation only: no late panel or quote file is touched before the freeze.
    print(
        f"[blocks] validation={len(days_val)} days (late block deferred until after the freeze)",
        flush=True,
    )
    val_score_cov = score_block(out, models, model_gate, days_val, args.resume, supplemental_roots)
    val_replay_cov = replay_block(
        out, VAL_BLOCK, days_val, model_gate, args.resume, supplemental_roots
    )
    val_surface, val_cov = aggregate_block(out, VAL_BLOCK, days_val)
    for view in VIEWS:
        cell = val_surface[view.key]
        boot = cell["bootstrap_daily_known_usd"]["25bps"]
        print(
            f"[val@25] {view.label:<28} {cell['known_usd_per_calendar_day']['25']:+8.2f} $/day "
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
        "study": STUDY,
        "status": STATUS,
        "chosen": {
            "view_key": chosen.key,
            "label": chosen.label,
            "qual": chosen.qual,
            "head": chosen.head,
        },
        "selection": {
            "objective": (
                "2023 validation actual-touch known contribution dollars per full "
                "calendar day at the 25bps residual rung"
            ),
            "selection_cost_bps": SELECT_COST,
            "cost_ladder_bps": list(RUNG_COSTS),
            "basis": (
                "PARTIAL: unknown executions are excluded from the numerator and "
                "reported beside it; the full-loss lower bound is an optional guard, "
                "never the objective"
            ),
            "power_floors": None,
            "median_or_tail_gates": None,
            "cost_or_count_kill": None,
            "tie_break": "(- objective, - known_fills, view_key)",
            "ranking": ranking,
        },
        "views": [
            {"view_key": v.key, "label": v.label, "qual": v.qual, "head": v.head} for v in VIEWS
        ],
        "validation_surface": val_surface,
        "model": model_report,
        "model_gate": model_gate,
        "provenance": provenance_block(
            out, days_val, None, None, supplemental_roots, val_cov, None, None
        ),
    }
    write_json_atomic(out / "selection_freeze.json", freeze)
    print(
        f"[freeze] selection_freeze.json -> chosen {chosen.label} "
        f"({val_surface[chosen.key]['known_usd_per_calendar_day']['25']:+.2f} $/day @25) "
        "AFTER validation, BEFORE any late file is read",
        flush=True,
    )

    if args.skip_late:
        results = {
            "study": STUDY,
            "status": STATUS,
            "decision": decision_text(chosen, val_surface[chosen.key], None, frozen=False),
            "chosen": freeze["chosen"],
            "frozen_choice_immutable": True,
            "contract": CONTRACT,
            "model": model_report,
            "model_gate": model_gate,
            "validation": val_surface,
            "validation_ranking": ranking,
            "late": None,
            "development": None,
            "coverage": {"validation": val_cov, "late": None, "development": None},
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

    # 2) late block: the same six views for transparency, the choice already frozen.
    #    This is the FIRST access to any late panel or quote file.
    days_late = block_days(args.days, LATE_BLOCK)
    if args.days is None and len(days_late) != EXPECTED_DAYS[LATE_BLOCK]:
        raise SystemExit(
            f"[{STUDY}] requires {EXPECTED_DAYS[LATE_BLOCK]} {LATE_BLOCK} days, "
            f"got {len(days_late)}"
        )
    print(f"[blocks] late={len(days_late)} days (after the freeze)", flush=True)
    late_score_cov = score_block(
        out, models, model_gate, days_late, args.resume, supplemental_roots
    )
    late_replay_cov = replay_block(
        out, LATE_BLOCK, days_late, model_gate, args.resume, supplemental_roots
    )
    late_surface, late_cov = aggregate_block(out, LATE_BLOCK, days_late)
    for view in VIEWS:
        cell = late_surface[view.key]
        boot = cell["bootstrap_daily_known_usd"]["25bps"]
        print(
            f"[late@25] {view.label:<28} {cell['known_usd_per_calendar_day']['25']:+8.2f} $/day "
            f"known fills={cell['known_fills']} unknown={cell['unknown_fills']} "
            f"nomatch={cell['no_match_fills']} attempts={cell['attempts']} "
            f"signals={cell['signals']} traded_days={cell['traded_days']} "
            f"p>0={boot.get('p_gt_zero')}",
            flush=True,
        )

    # 3) development window (optional, transparency only; never a selection block).
    days_dev: list[str] | None = None
    dev_surface: dict | None = None
    dev_cov: dict | None = None
    dev_score_cov: dict | None = None
    dev_replay_cov: dict | None = None
    if args.include_dev:
        days_dev = block_days(args.days, DEV_BLOCK)
        if args.days is None and len(days_dev) != EXPECTED_DAYS[DEV_BLOCK]:
            raise SystemExit(
                f"[{STUDY}] requires {EXPECTED_DAYS[DEV_BLOCK]} {DEV_BLOCK} days, "
                f"got {len(days_dev)}"
            )
        print(f"[blocks] development={len(days_dev)} days (transparency only)", flush=True)
        dev_score_cov = score_block(
            out, models, model_gate, days_dev, args.resume, supplemental_roots
        )
        dev_replay_cov = replay_block(
            out, DEV_BLOCK, days_dev, model_gate, args.resume, supplemental_roots
        )
        dev_surface, dev_cov = aggregate_block(out, DEV_BLOCK, days_dev)
        for view in VIEWS:
            cell = dev_surface[view.key]
            print(
                f"[dev@25] {view.label:<28} {cell['known_usd_per_calendar_day']['25']:+8.2f} "
                f"$/day known fills={cell['known_fills']} unknown={cell['unknown_fills']} "
                f"attempts={cell['attempts']} traded_days={cell['traded_days']}",
                flush=True,
            )

    results = {
        "study": STUDY,
        "status": STATUS,
        "decision": decision_text(
            chosen, val_surface[chosen.key], late_surface[chosen.key], frozen=True
        ),
        "chosen": freeze["chosen"],
        "frozen_choice_immutable": True,
        "contract": CONTRACT,
        "model": model_report,
        "model_gate": model_gate,
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
        "development": dev_surface,
        "cost_ladder_whole_calendar_known_contributions": {
            view.key: {
                str(int(r)): {
                    "validation_usd": val_surface[view.key]["known_usd"][str(int(r))],
                    "late_usd": late_surface[view.key]["known_usd"][str(int(r))],
                    "development_usd": (
                        dev_surface[view.key]["known_usd"][str(int(r))]
                        if dev_surface is not None
                        else None
                    ),
                    "validation_usd_per_calendar_day": val_surface[view.key][
                        "known_usd_per_calendar_day"
                    ][str(int(r))],
                    "late_usd_per_calendar_day": late_surface[view.key][
                        "known_usd_per_calendar_day"
                    ][str(int(r))],
                    "development_usd_per_calendar_day": (
                        dev_surface[view.key]["known_usd_per_calendar_day"][str(int(r))]
                        if dev_surface is not None
                        else None
                    ),
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
            "development": dev_cov,
            "score_validation": val_score_cov,
            "score_late": late_score_cov,
            "score_development": dev_score_cov,
            "replay_validation": val_replay_cov,
            "replay_late": late_replay_cov,
            "replay_development": dev_replay_cov,
        },
        "provenance": provenance_block(
            out,
            days_val,
            days_late,
            days_dev,
            supplemental_roots,
            val_cov,
            late_cov,
            dev_cov,
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
        "--include-dev",
        action="store_true",
        help="also report the 2021-05..2021-10 development window (never a selection block)",
    )
    p.add_argument(
        "--with-model-gate",
        action="store_true",
        help="optional real stored-forecast gate (h15/h60 heads only; h30 stays rules-only)",
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
