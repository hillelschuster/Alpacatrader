#!/usr/bin/env python3
"""Cross-name LEADER-ROTATION payoff study on the open-anchored top-gainer panel.

This producer asks a new question about the same liquidity-qualified, open-anchored panel
that the retained learned heads trade: does the SNAPSHOT-CROSS-SECTION carry tradable
information that a per-name model cannot see? Every admitted name at one 5-minute decision
clock ``t`` is one observable watchlist name; the study freezes twelve small peer-relative
numeric features computed from that past snapshot ONLY (the subject row is always excluded
from its own peer statistics), fits three fixed learner families on top of the canonical
actual-touch labels, and replays the learned score through the SAME true-quote execution
physics as the quote-aware engine.

WHAT IS NEW HERE (and nothing else):
* TWELVE frozen peer-relative features on top of the base path's quote context (the 26
  causal ``FEATURES_ALL`` path features plus the five observed-intent quote features that
  the canonical touch labels already carry): relative ret3/ret15/gain versus the OTHER
  watchlist names' snapshot means, cross-sectional rank snapshots (ret3/ret15 percentiles
  and the dominance change ``rank_now - rank_snapshot``), the gap to the 15-minute leader,
  peer breadth and dispersion (others only), the subject's ownership of the watchlist's
  causal dollar volume, and the subject's arrival age relative to its peers. All of them
  are computed from rows of the SAME ``(day, t)`` snapshot with strictly-past bars, so
  there is no future winning name, no ticker identifier and no date identifier anywhere.
  A single-name snapshot has NO peers: every peer feature is exactly 0.0 (the frozen
  null-fill), never NaN and never the subject's own value.
* THREE fixed learner families x TWO heads (15/60 minutes): a standardized fixed Ridge
  (alpha=1.0) and two small fixed LightGBM configurations (regression and lambdarank with
  a fit-set-only linear calibration of the rank score to return units). The old trained
  ``FEATURES_ALL`` semantics are NOT reinterpreted: the 26 base features keep their
  original order and meaning and the twelve additions are appended after them in one
  frozen 43-column order.
* The model input space is the canonical label space (26 base + 5 observed-intent quote
  features) plus the twelve additions; the target is the canonical day label
  ``y_touch_h15`` / ``y_touch_h60`` (PRICE-GROSS gross-before-extra actual ASK->BID
  NBBO touch return: a dimensionless exit_bid/entry_ask - 1 price ratio, never a
  per-budget fraction). The touch already carries the spread, so NO cost parameter ever
  subtracts the spread a second time: the reported rungs (0/5/10/25/50/75/100/125/150 bps)
  are EXTRA provider fees and slippage on both legs, the primary rung is 25, the reserve
  rung is 150, and a zero/low-fee provider scenario is a valid measured view.

TRUE-DATA DEPENDENCY (not a placeholder): the fit block is the canonical labels producer's
train window 2021-05-01..2022-12-31 (exactly 20 months), read from
``<labels-root>/<day>.parquet`` with the producer's frozen per-day schema. The selection
surface is all 250 allowed 2023 validation days, the frozen choice is then confirmed on
the 332 allowed late (2025-02..2026-05) days. If the labels root or any needed day file
is absent the run stops with a precise missing-prerequisite message instead of silently
inventing labels; the producer must run first.

EXECUTION PHYSICS (imported from the quote-aware engine, not copied): the entry intent is
the (t+1) clock at 0, the order is a marketable IOC LIMIT with the quantity and the limit
fixed BEFORE arrival, and the entry is priced ONCE at the actual arrival clock - an IOC
never rests to a later quote, never re-sizes and never substitutes a more favourable print
(firm/fresh + ask<=limit + depth>=q is a conditional L1 fill, ask>limit is a modeled no
L1 match, everything else is an execution UNKNOWN with conservative reservation). The exit
is a genuine MARKET order: fresh-quote-gated submission, priced +250ms after the actual
submission, allowed to rest only after a genuinely unavailable book, never solely because
a valid print is aged. Book: 3 slots x $250 = $750 nominal, no leverage, one position per
ticker, 5-minute flat cooldown after the ACTUAL exit, 5 attempts per ticker per session,
simultaneous-clock intents funded by score BEFORE any arrival outcome is observed, an
UNKNOWN position keeps its slot and its reserved cash until it resolves or the session
ends.

COMPARISON AND SELECTION: the frozen view grid (3 families x 2 heads x 4 score bars
0/0.0025/0.005/0.01) is written to ``contract.json`` BEFORE any outcome. The PRIMARY entry
clearance is the touch-payoff floor ``10_000 * pred >= 25bps`` (the fixed "25-extra"):
because the predicted touch payoff already nets the observed spread (the entry is the ASK
and the exit is the BID), the spread is NEVER charged at the gate a second time: the only
entry clearance is the extra-fee touch-payoff floor plus the view's fixed net score bar,
with no spread penalty layered on top. The single view is chosen ONLY by 2023 validation
actual-touch known contribution in dollars per full
calendar day at the primary 25bps extra rung, on an explicit PARTIAL basis (unknown
executions excluded from the numerator
and reported beside it; the full-loss lower bound is a disclosed guard, never the
objective). No power floor, no count, median, top-day or CI gate and no 100bps hurdle is
applied. ``selection_freeze.json`` is written AFTER the validation surface and BEFORE any
late file is read. A model-free TOUCH-ONLY provider baseline (enter wherever the quote is
observable and depth-supported, no learned score, no spread bar) is measured on the SAME
PARTIAL basis and reported separately, never folded into a full-EV loss.

This is a research replay of historical conditional L1 IOC/market orders. No live order,
no broker call, no account write, no protected outcome (2024, 2025-01, 2026-06..08) is
ever touched. Quotes measure the touch cost of a hypothetical order; they are not exchange
fills and never a fill guarantee.

Usage:
  uv run --no-sync python factory/scripts/alpha_cross_name_rotation.py
      # fit the frozen families on the labeled train window, select on validation 2023,
      # confirm on late 332 -> ~/alpha-data/open-search-v1/cross_name_rotation
  uv run --no-sync python factory/scripts/alpha_cross_name_rotation.py --resume
      # resume from the per-day feature / state / replay parts already on disk
  uv run --no-sync python factory/scripts/alpha_cross_name_rotation.py --skip-late
      # stop after the frozen selection (validation block only, no late file touched)
  uv run --no-sync python factory/scripts/alpha_cross_name_rotation.py --days 2023-05-15
      # smoke subset (debug only; requires the label file for that day)
  uv run --no-sync python factory/scripts/alpha_cross_name_rotation.py --force-fit
      # refit the models even when a matching frozen model card exists

Prerequisite: the canonical labels producer must have written
``~/alpha-data/open-search-v1/quote_hourly_payoff/labels/<day>.parquet`` for every
requested day; otherwise this run stops with a precise MISSING PREREQUISITE message.

Key CLI flags: ``--out`` (output root), ``--labels-root`` (canonical label day files root,
default ``~/alpha-data/open-search-v1/quote_hourly_payoff/labels``), ``--panel-root``
(panel days root parent, default ``~/alpha-data/open-search-v1``), ``--data-root`` (quote
cache data root, default the repository's ``data``), ``--days`` (smoke subset),
``--resume``, ``--skip-late``, ``--force-fit``, ``--supplement`` (extra quote supplement
cache root; original prints always win).
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
from lightgbm import LGBMRanker, LGBMRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))

import alpha_quote_aware_frequency as _engine  # noqa: E402  (imported, never edited)
from alpha_open_learned import FEATURES_ALL, bootstrap_daily  # noqa: E402
from alpha_open_panel import allowed  # noqa: E402
from alpha_open_sim import causal_liquidity, period  # noqa: E402
from alpha_quote_aware_frequency import (  # noqa: E402
    MAX_AGE_S,
    SymbolQuotes,
    arrival_us,
    causal_quantity,
    entry_price_leg,
    limit_price_of,
    minute_us,
    net_usd,
    priced_leg,
    quote_day_frame,
    reserved_usd,
    submission_clock,
    symbol_quotes,
    write_json_atomic,
)

# ----- fixed configuration (no HPO, no refit of the base path, no grid search). --
HEADS = (15, 60)  # the two canonical touch payoff heads
MODEL_FAMILIES = ("ridge", "lgbm_reg", "lgbm_rank")
BASELINE_FAMILY = "touch_only"
SCORE_THRESHOLDS = (0.0, 0.0025, 0.005, 0.01)  # four fixed score bars, gross-touch units
TOUCH_CLEARANCE_BPS = 25.0  # the "25-extra": the fixed gross-touch clearance floor
EXTRA_FEE_RUNGS = (0.0, 5.0, 10.0, 25.0, 50.0, 75.0, 100.0, 125.0, 150.0)
PRIMARY_RUNG = 25.0  # primary measured extra rung (selection + headline)
RESERVE_BPS = 150.0  # worst-case reservation for EVERY rung's funding
FEE_CAP_BPS = 150.0  # the teacher's intent fee cap: causal quantity sizing
MAX_SLOTS = 3  # concurrent positions
ORDER_BUDGET = 250.0  # per-position ticket
BOOK = MAX_SLOTS * ORDER_BUDGET  # $750 nominal research book
COOLDOWN_MIN = 5  # flat minutes after the ACTUAL exit
MAX_ATTEMPTS = 5  # attempts per ticker per session
CLIP = (-0.5, 1.0)  # fit-only clip on the touch target
RANK_GRADES = 32  # lambdarank relevance grades over the clipped target range
ANNUAL_SESSIONS = 252  # simple session-count convention, NOT a CAGR claim
BOOT_N = 1000
BOOT_SEED = 20261010
TRAIN_PERIOD = "train"
VAL_BLOCK = "validation"
LATE_BLOCK = "confirmation"
TRAIN_FIT_START = "2021-05-01"  # the labels producer's train window (exactly 20 months)
EXPECTED_DAYS = {VAL_BLOCK: 250, LATE_BLOCK: 332}
UNDERPOWERED_KNOWN_FILLS = 25  # label only; NEVER a drop or auto-kill threshold
PROTECTED_UNREAD = ["2024", "2025-01", "2026-06", "2026-07", "2026-08"]
RIDGE_ALPHA = 1.0
LGB_PARAMS = {
    "num_leaves": 15,
    "min_data_in_leaf": 300,
    "learning_rate": 0.03,
    "n_estimators": 200,
    "num_threads": 2,
    "seed": 20261010,
    "verbosity": -1,
    "deterministic": True,
    "force_row_wise": True,
}
PANEL_ROOT = Path("/home/hillel/alpha-data/open-search-v1")
PANEL_DAYS = PANEL_ROOT / "days"
PANEL_CONTRACT = PANEL_ROOT / "contract.json"
LABELS_ROOT = PANEL_ROOT / "quote_hourly_payoff" / "labels"
OUTPUT = PANEL_ROOT / "cross_name_rotation"
DATA_ROOT = Path("/home/hillel/projects/Alpacatrader") / "data"
QUOTE_CACHE_ROOT = DATA_ROOT / "sip" / "net" / "quotes"
SUPPLEMENT_ROOTS = (
    PANEL_ROOT / "sparse_execution_frontier" / "fetched_quotes" / "quotes",
    PANEL_ROOT / "quote_completeness_audit" / "quotes",
    PANEL_ROOT / "quote_completeness_audit" / "fetched_quotes" / "quotes",
    # The ALL-PIT missing-symbol acquisition cache (TouchHourlyPayoffLearning's producer).
    # Discovered when present; the original PRIMARY print always wins (PRIMARY_KEEPFIRST),
    # so a supplement can only ADD timestamps and never revises an observed print. A day
    # whose stream is still absent afterwards is an acquisition UNKNOWN - reported as
    # coverage, never known cash and never a future-availability entry filter.
    PANEL_ROOT / "quote_universe_acquire" / "quotes",
)
STATUS = "DISCOVERY-NOT-VALIDATED"
VERSION = 1
FEATURES_SCHEMA = 1
STATES_SCHEMA = 1
REPLAY_SCHEMA = 1

# ----- the frozen feature order ------------------------------------------------
PEER_GROUP = ("day", "t")  # the causal snapshot: same session, same 5-minute clock
PEER_FEATURES = (
    "rel_ret3",  # subject ret3 minus the OTHER watchlist names' snapshot mean ret3
    "rel_ret15",  # subject ret15 minus the OTHERS' snapshot mean ret15
    "rel_gain",  # subject gain_open minus the OTHERS' snapshot mean gain_open
    "ret3_pctile",  # fraction of OTHERS strictly below the subject's ret3 (rank snapshot)
    "ret15_pctile",  # fraction of OTHERS strictly below the subject's ret15
    "rank_delta",  # dominance change: current snapshot rank by gain - rank_snapshot
    "lead_gap15",  # subject ret15 minus the snapshot's max ret15 (0 = it is the leader)
    "peer_pos3_share",  # share of OTHERS with ret3 > 0 (breadth, self excluded)
    "peer_ret3_std",  # dispersion of the OTHERS' ret3
    "peer_gain_std",  # dispersion of the OTHERS' gain_open
    "vol_ownership",  # subject's share of the watchlist's causal dollar volume
    "arrival_age_rel",  # subject admission_age minus the OTHERS' mean admission_age
)
LABEL_QUOTE_FEATURES = (
    "spread_bps",
    "log_ask_shares",
    "log_bid_shares",
    "quote_age_s",
    "depth_imbalance",
)
FEATURE_ORDER = list(FEATURES_ALL) + list(LABEL_QUOTE_FEATURES) + list(PEER_FEATURES)  # 43
LABEL_COLUMNS = (
    "day",
    "t",
    "ticker",
    "entry_intent_us",
    *FEATURES_ALL,
    *LABEL_QUOTE_FEATURES,
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

CONTRACT = {
    "version": VERSION,
    "status": STATUS,
    "hypothesis": "the cross-section of observable watchlist names at the same past "
    "snapshot (relative ret3/ret15/gain, rank snapshots and dominance change, peer "
    "breadth/dispersion, arrival age and ownership of volume, self excluded) predicts the "
    "actual NBBO touch payoff of a 15/60-minute hold better than the touch-only "
    "provider baseline, under the same true-quote IOC entry / market exit physics",
    "features": {
        "base_path": "FEATURES_ALL (22 path features) + the 5 observed-intent quote "
        "features carried by the canonical touch labels",
        "old_semantics_reinterpreted": False,
        "additions": list(PEER_FEATURES),
        "feature_order": FEATURE_ORDER,
        "n_features": len(FEATURE_ORDER),
        "peer_group": "all observable watchlist rows of the same (day, t) snapshot",
        "self_exclusion": "every peer statistic is computed over the OTHER rows only "
        "(sum/count/minus-own, strict rank percentiles); a single-name snapshot has no "
        "peers and yields exactly 0.0, never NaN and never the subject's own value",
        "causality": "strictly-past bars at the same snapshot; no future winning name, "
        "no ticker identifier, no date identifier",
    },
    "labels": {
        "root": str(LABELS_ROOT),
        "columns": list(LABEL_COLUMNS),
        "target": "y_touch_h15 / y_touch_h60: PRICE-GROSS gross-before-extra actual "
        "ASK->BID NBBO touch return = exit_bid / entry_ask - 1 (a dimensionless price "
        "ratio, never a per-budget fraction; the spread is already inside the target)",
        "target_normalization": "price_gross_return_for_filled; zero_for_known_cash; "
        "null_for_unknown",
        "known_flag": "label_known_h15 / label_known_h60; unknown legs are excluded from "
        "the fit and disclosed, never imputed",
        "producer_first": "the canonical labels producer must run before this study",
    },
    "fit": {
        "block": "the labels producer's train window 2021-05-01..2022-12-31 (20 months)",
        "clip": list(CLIP),
        "clip_fit_only": True,
        "sample_weights": None,
        "ticker_or_date_features": False,
    },
    "models": {
        "families": list(MODEL_FAMILIES),
        "heads": list(HEADS),
        "ridge": {"alpha": RIDGE_ALPHA, "scaler": "StandardScaler fit on the train block"},
        "lgbm_reg": {"objective": "regression", **LGB_PARAMS},
        "lgbm_rank": {
            "objective": "lambdarank",
            "relevance_grades": RANK_GRADES,
            "calibration": "fit-set-only linear map raw rank score -> touch return, frozen "
            "into the bundle; disclosed, never refit on validation or late",
            **LGB_PARAMS,
        },
    },
    "views": {
        "families": list(MODEL_FAMILIES),
        "heads": list(HEADS),
        "score_thresholds": list(SCORE_THRESHOLDS),
        "baseline_family": BASELINE_FAMILY,
        "baseline_policy": "model-free: enter wherever the intent quote is firm/fresh, the "
        "causal quantity is >= 1 and the intent ASK depth supports it; no learned score "
        "and no payoff bar",
        "entry_clearance": "10000 * predicted_touch_payoff >= TOUCH_CLEARANCE_BPS (25bps, "
        "the fixed '25-extra' gross-touch clearance): the predicted touch payoff already "
        "nets the observed spread (ASK entry -> BID exit), so the spread is NEVER charged "
        "at the gate a second time",
        "entry_clearance_policy": {
            "primary": "touch-payoff clearance only: 10_000 * pred >= 25bps + the view's "
            "fixed score bar; the observed spread is not re-charged because it is already "
            "inside the touch target",
        },
    },
    "entry": {
        "intent_clock": "clock_us(day, t+1, 0): latest RAW NBBO state, never pre-filtered",
        "firm_fresh_rule": "valid, un-crossed, R-only, age <= 2s (an operational gate, not "
        "a claim that an older NBBO expired)",
        "limit_price": "ceil_to_cent(observed_ask * 1.01): a pre-declared marketability cap, "
        "NOT charged friction",
        "quantity": "q = floor(250 / (limit * (1 + 150bps/2))): the teacher's intent fee "
        "cap, fixed BEFORE arrival and never re-floored",
        "depth_rule": "q >= 1 and q <= intent ASK top-of-book depth",
        "arrival_rule": "IOC LIMIT priced ONCE at the actual arrival clock (+250ms): "
        "firm/fresh + ask <= limit + arrival ASK depth >= q -> conditional L1 fill at the "
        "ACTUAL ask; ask > limit -> modeled no L1 match (unfilled cash, the IOC cancels); "
        "partial depth / non-acquired / invalid / non-regular / stale-but-valid arrival -> "
        "execution UNKNOWN (an IOC never rests to a later print, never substitutes one and "
        "never re-sizes; the unknown exposure stays conservatively reserved)",
        "no_order_rule": "q == 0 is a KNOWN no order: no order is sent, the cash stays "
        "unfilled, it is never a priced zero-return fill and never an UNKNOWN",
    },
    "exit": {
        "hold": "deterministic min(entry_minute + head, session_end) minutes, RTH capped",
        "submission": "FRESH-QUOTE-GATED genuine MARKET order: submit at the due intent if "
        "the latest raw state is firm and fresh, else hold to the FIRST eligible regular "
        "print at/after the due intent inside RTH",
        "arrival_clock": "actual submission + 250ms",
        "pricing": "priced_leg semantics: fresh print at arrival -> priced at that print; "
        "valid-but-STALE at arrival -> UNKNOWN (never a later, more favourable print); "
        "invalid / non-regular / absent book -> the working order conditionally rests to "
        "the first eligible print at/after arrival (never solely because of age)",
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
        "cash_reservation": "q * limit * (1 + 150bps/2): the worst-case rung outlay, so "
        "every reported rung stays funded",
        "simultaneous_clock_rule": "highest-score eligible intents are funded (up to the "
        "slot and cash limits) BEFORE any arrival outcome is observed; no same-clock "
        "substitution after an unfilled intent",
        "unknown_rule": "an UNKNOWN execution (entry or exit) keeps its slot, blocks its "
        "ticker and keeps its reserved cash until it resolves or the session ends; cash is "
        "never freed at a planned time",
        "capital_accounting": "peak_reserved_usd is the true intraday peak of concurrently "
        "reserved cash swept from the day's own funding/release events, never a last-day "
        "re-zeroed accumulator",
    },
    "costs": {
        "basis": "the observed ASK->BID touch already carries the spread, so the rungs are "
        "EXTRA provider fees / slippage applied to both legs; the spread is never charged "
        "a second time",
        "extra_fee_rungs_bps": list(EXTRA_FEE_RUNGS),
        "primary_rung_bps": PRIMARY_RUNG,
        "reserve_rung_bps": RESERVE_BPS,
        "zero_low_fee_valid": True,
        "no_fixed_hurdle": "explore 25-150 bps; a provider with a low commission is a "
        "valid scenario, there is no mandatory 100-150 bps promotion hurdle",
    },
    "selection": {
        "objective": "2023 validation actual-touch known contribution dollars per full "
        "calendar day at the primary 25bps extra rung",
        "selection_cost_bps": PRIMARY_RUNG,
        "candidate_views": "the 24 model views (3 families x 2 heads x 4 score bars); the "
        "touch-only baseline is a separate comparator, never a selection candidate",
        "basis": "PARTIAL: unknown executions are excluded from the numerator and reported "
        "beside it; the full-loss lower bound is a disclosed guard, never the objective",
        "power_floors": None,
        "median_or_tail_gates": None,
        "cost_or_count_kill": None,
        "tie_break": "(- objective, - known_fills, view_key)",
        "frozen_before_late_inspection": True,
        "freeze_artifact": "selection_freeze.json (written after the validation surface, "
        "before any late file is read)",
    },
    "periods": {
        TRAIN_PERIOD: "2021-05-01..2022-12-31 (the labels producer's 20-month train window)",
        VAL_BLOCK: "2023-01-01..2023-12-31 (all 250 allowed panel days)",
        LATE_BLOCK: "2025-02-01..2026-05-31 (all 332 allowed panel days, previously "
        "explored, not pristine)",
    },
    "protected_unread": PROTECTED_UNREAD,
    "no_live_orders": True,
}


# ----- shared helpers ---------------------------------------------------------
def producer_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def engine_sha256() -> str:
    return hashlib.sha256(Path(_engine.__file__).read_bytes()).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


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


def label_day_path(labels_root: Path, day: str) -> Path:
    return labels_root / f"{day}.parquet"


def block_days(days: list[str] | None, block: str) -> list[str]:
    """All allowed panel days of one research block (``days`` restricts for smoke runs)."""
    keep = set(days) if days else None
    out = []
    for path in sorted(PANEL_DAYS.glob("????-??-??.parquet")):
        day = path.stem
        if not allowed(day) or period(day) != block:
            continue
        if block == TRAIN_PERIOD and day < TRAIN_FIT_START:
            continue  # outside the canonical labels producer's train window
        if keep is not None and day not in keep:
            continue
        out.append(day)
    return out


@dataclass(frozen=True)
class View:
    """One (family, head, threshold) cell; the baseline has no score bar."""

    family: str
    head: int
    threshold: float

    @property
    def baseline(self) -> bool:
        return self.family == BASELINE_FAMILY

    @property
    def key(self) -> str:
        if self.baseline:
            return f"{BASELINE_FAMILY}|h{self.head}"
        return f"{self.family}|h{self.head}|{self.threshold:.4f}"

    @property
    def label(self) -> str:
        if self.baseline:
            return f"touchonly_h{self.head}"
        return f"{self.family}_h{self.head}_thr{int(round(self.threshold * 10000))}bps"

    @property
    def pred_col(self) -> str:
        return f"pred_{self.family}_h{self.head}"

    @property
    def rule_col(self) -> str:
        if self.baseline:
            return "rule_touch_only"
        return f"rule_{self.family}_h{self.head}"


MODEL_VIEWS = tuple(
    View(family, head, thr)
    for family in MODEL_FAMILIES
    for head in HEADS
    for thr in SCORE_THRESHOLDS
)
BASELINE_VIEWS = tuple(View(BASELINE_FAMILY, head, 0.0) for head in HEADS)
VIEWS = MODEL_VIEWS + BASELINE_VIEWS
VIEWS_BY_KEY = {v.key: v for v in VIEWS}


# ----- the twelve peer-relative leader-rotation features ----------------------
def peer_features(frame: pl.DataFrame) -> pl.DataFrame:
    """Cross-sectional leader-rotation features at every ``(day, t)`` snapshot.

    The peer group is ALL observable watchlist rows of the SAME snapshot and every peer
    statistic EXCLUDES the subject row: window sums/counts have the subject's own value
    subtracted, the percentiles are strict-rank based, and the dispersion is the
    population standard deviation of the others. A single-name snapshot (``n_all == 1``)
    has no peers at all, so every peer-relative feature is exactly ``0.0``: the frozen
    null-fill, never NaN and never the subject's own value leaking in.
    """
    g = list(PEER_GROUP)
    needed = ("ret3", "ret15", "gain_open", "admission_age", "rank_snapshot", "log_cum_dv")
    missing = [c for c in needed if c not in frame.columns]
    if missing:
        raise ValueError(f"peer features need panel columns {missing}")
    own_cumdv = pl.col("log_cum_dv").exp() - 1.0
    marked = frame.with_columns(
        [
            pl.len().over(g).alias("_n_all"),
            pl.col("ret3").sum().over(g).alias("_s_ret3"),
            pl.col("ret15").sum().over(g).alias("_s_ret15"),
            pl.col("gain_open").sum().over(g).alias("_s_gain"),
            pl.col("admission_age").sum().over(g).alias("_s_age"),
            (pl.col("ret3") ** 2).sum().over(g).alias("_q_ret3"),
            (pl.col("gain_open") ** 2).sum().over(g).alias("_q_gain"),
            (pl.col("ret3") > 0).sum().over(g).alias("_n_pos3"),
            (pl.col("ret3") > 0).cast(pl.Int64).alias("_own_pos3"),
            pl.col("ret15").max().over(g).alias("_max_ret15"),
            pl.col("ret3").rank(method="min").over(g).alias("_rank_ret3"),
            pl.col("ret15").rank(method="min").over(g).alias("_rank_ret15"),
            # dominance rank: 1 = STRONGEST gainer in the snapshot, matching the panel's own
            # rank_snapshot convention (rank 1 = the top name at its admission checkpoint).
            pl.col("gain_open").rank(method="min", descending=True).over(g).alias("_rank_gain_now"),
            own_cumdv.sum().over(g).alias("_s_cumdv"),
            own_cumdv.alias("_own_cumdv"),
        ]
    )
    n_o = pl.col("_n_all") - 1
    has_peers = pl.col("_n_all") > 1

    def others_mean(sum_col: str, own_expr: pl.Expr) -> pl.Expr:
        return (pl.col(sum_col) - own_expr) / n_o

    def others_std(sum_col: str, sq_col: str, own_expr: pl.Expr) -> pl.Expr:
        mean_o = (pl.col(sum_col) - own_expr) / n_o
        sumsq_o = pl.col(sq_col) - own_expr**2
        var_o = (sumsq_o - n_o * mean_o**2) / n_o
        return var_o.clip(lower_bound=0.0).sqrt()

    out = marked.with_columns(
        [
            pl.when(has_peers)
            .then(pl.col("ret3") - others_mean("_s_ret3", pl.col("ret3")))
            .otherwise(0.0)
            .alias("rel_ret3"),
            pl.when(has_peers)
            .then(pl.col("ret15") - others_mean("_s_ret15", pl.col("ret15")))
            .otherwise(0.0)
            .alias("rel_ret15"),
            pl.when(has_peers)
            .then(pl.col("gain_open") - others_mean("_s_gain", pl.col("gain_open")))
            .otherwise(0.0)
            .alias("rel_gain"),
            pl.when(has_peers)
            .then((pl.col("_rank_ret3") - 1) / n_o)
            .otherwise(0.0)
            .alias("ret3_pctile"),
            pl.when(has_peers)
            .then((pl.col("_rank_ret15") - 1) / n_o)
            .otherwise(0.0)
            .alias("ret15_pctile"),
            (pl.col("_rank_gain_now") - pl.col("rank_snapshot").fill_null(11.0)).alias(
                "rank_delta"
            ),
            (pl.col("ret15") - pl.col("_max_ret15")).alias("lead_gap15"),
            pl.when(has_peers)
            .then((pl.col("_n_pos3") - pl.col("_own_pos3")) / n_o)
            .otherwise(0.0)
            .alias("peer_pos3_share"),
            pl.when(has_peers)
            .then(others_std("_s_ret3", "_q_ret3", pl.col("ret3")))
            .otherwise(0.0)
            .alias("peer_ret3_std"),
            pl.when(has_peers)
            .then(others_std("_s_gain", "_q_gain", pl.col("gain_open")))
            .otherwise(0.0)
            .alias("peer_gain_std"),
            pl.when(pl.col("_s_cumdv") > 0)
            .then(pl.col("_own_cumdv") / pl.col("_s_cumdv"))
            .otherwise(0.0)
            .alias("vol_ownership"),
            pl.when(has_peers)
            .then(pl.col("admission_age") - others_mean("_s_age", pl.col("admission_age")))
            .otherwise(0.0)
            .alias("arrival_age_rel"),
        ]
    )
    return out.select(["day", "ticker", "t", *PEER_FEATURES])


# ----- the canonical label dependency ------------------------------------------
FEATURE_TYPES = {
    "day": pl.String,
    "ticker": pl.String,
    "t": pl.Int64,
    "entry_intent_us": pl.Int64,
    "session_end": pl.Int64,
    "y_touch_h15": pl.Float64,
    "y_touch_h60": pl.Float64,
    "label_known_h15": pl.Boolean,
    "label_known_h60": pl.Boolean,
    "causal_qty": pl.Int64,
    "label_price_basis": pl.String,
    **dict.fromkeys(FEATURE_ORDER, pl.Float64),
}


def read_day_labels(day: str, labels_root: Path) -> tuple[pl.DataFrame, dict]:
    """One canonical label day (the true data dependency), with its digest facts."""
    path = label_day_path(labels_root, day)
    if not path.exists():
        raise SystemExit(
            f"[cross-rotation] MISSING PREREQUISITE: canonical label file {path} is absent. "
            "The labels producer (factory/scripts/alpha_touch_hourly_payoff.py) must run "
            "first for this day; this study never synthesizes labels."
        )
    frame = pl.read_parquet(path)
    missing = [c for c in LABEL_COLUMNS if c not in frame.columns]
    if missing:
        raise SystemExit(
            f"[cross-rotation] LABEL SCHEMA MISMATCH in {path}: missing {missing}; "
            f"expected the frozen contract columns {list(LABEL_COLUMNS)}"
        )
    info = {
        "path": str(path),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "rows": int(frame.height),
    }
    return frame.select(LABEL_COLUMNS), info


def build_day_features(day: str, labels_root: Path, panel_root: Path) -> tuple[pl.DataFrame, dict]:
    """One session's frozen feature matrix: labels x self-excluded peer features."""
    panel_path = panel_root / "days" / f"{day}.parquet"
    if not panel_path.exists():
        raise SystemExit(f"[cross-rotation] MISSING PREREQUISITE: panel day {panel_path}")
    panel = pl.read_parquet(panel_path)
    labels, label_info = read_day_labels(day, labels_root)
    session_ends = panel["session_end"].unique().to_list()
    if len(session_ends) != 1:
        raise SystemExit(
            f"[cross-rotation] contract violation: {day} has session_end values {session_ends}"
        )
    session_end = int(session_ends[0])
    peers = peer_features(panel)
    feat = labels.join(peers, on=["day", "ticker", "t"], how="left")
    cov = {
        "panel_rows": int(panel.height),
        "panel_qualified_rows": int(panel.filter(causal_liquidity(panel)).height),
        "label_rows": int(labels.height),
        "feature_rows": int(feat.height),
        "peer_join_missing_rows": int(feat["rel_ret3"].is_null().sum()),
        "intent_clock_mismatches": int(
            (
                feat["entry_intent_us"]
                != pl.Series([minute_us(day, int(t) + 1) for t in feat["t"].to_list()])
            ).sum()
            if feat.height
            else 0
        ),
        "label_price_basis_values": sorted(set(feat["label_price_basis"].unique().to_list())),
        "session_end": session_end,
        "labels": label_info,
        "causal_qty_label_vs_engine_note": (
            "the label's causal_qty is the teacher's sizing; the replay sizes from its own "
            "intent observation with the same fee cap and reports any disagreement"
        ),
    }
    feat = feat.with_columns(pl.lit(session_end).cast(pl.Int64).alias("session_end"))
    return feat, cov


def feature_paths(out: Path, day: str) -> tuple[Path, Path]:
    return out / "features" / f"{day}.parquet", out / "features" / f"{day}.cov.json"


def features_block(
    days: list[str],
    out: Path,
    labels_root: Path,
    panel_root: Path,
    resume: bool,
    contract_sha: str,
) -> dict:
    """Build (or resume) every day's frozen feature matrix atomically, one day at a time."""
    producer = producer_sha256()
    engine = engine_sha256()
    missing = [d for d in days if not label_day_path(labels_root, d).exists()]
    if missing:
        raise SystemExit(
            f"[cross-rotation] MISSING PREREQUISITE: {len(missing)} canonical label day(s) "
            f"absent under {labels_root}: {missing[:10]}{' ...' if len(missing) > 10 else ''}; "
            "the labels producer must run first for every requested day"
        )
    coverage = {
        "days_total": len(days),
        "days_cached": 0,
        "days_built": 0,
        "label_rows": 0,
        "feature_rows": 0,
        "panel_rows": 0,
        "panel_qualified_rows": 0,
        "peer_join_missing_rows": 0,
        "intent_clock_mismatches": 0,
        "label_price_basis_values": [],
        "missing_quote_day_files": [],
        "labels": {},
    }
    todo = []
    for day in days:
        qpath, cpath = feature_paths(out, day)
        fresh = False
        prior = None
        if resume and cpath.exists() and qpath.exists():
            try:
                prior = json.loads(cpath.read_text())
                fresh = (
                    prior.get("resume_hash")
                    == _digest_bytes(
                        {
                            "day": day,
                            "producer": producer,
                            "contract": contract_sha,
                            "engine": engine,
                            "labels": prior.get("coverage", {}).get("labels", {}).get("sha256"),
                            "schema": FEATURES_SCHEMA,
                        }
                    )
                    and prior.get("schema") == FEATURES_SCHEMA
                )
            except (json.JSONDecodeError, OSError):
                fresh = False
        if fresh:
            coverage["days_cached"] += 1
            cov = prior.get("coverage", {})
            coverage["label_rows"] += int(cov.get("label_rows", 0))
            coverage["feature_rows"] += int(cov.get("feature_rows", 0))
            coverage["panel_rows"] += int(cov.get("panel_rows", 0))
            coverage["panel_qualified_rows"] += int(cov.get("panel_qualified_rows", 0))
            coverage["peer_join_missing_rows"] += int(cov.get("peer_join_missing_rows", 0))
            coverage["intent_clock_mismatches"] += int(cov.get("intent_clock_mismatches", 0))
            for b in cov.get("label_price_basis_values", []):
                if b not in coverage["label_price_basis_values"]:
                    coverage["label_price_basis_values"].append(b)
            coverage["labels"][day] = cov.get("labels", {})
        else:
            todo.append(day)
    for n, day in enumerate(todo, 1):
        feat, cov = build_day_features(day, labels_root, panel_root)
        qpath, cpath = feature_paths(out, day)
        qpath.parent.mkdir(parents=True, exist_ok=True)
        tmp = qpath.with_name(f"{qpath.name}.tmp{os.getpid()}")
        feat.write_parquet(tmp)
        os.replace(tmp, qpath)
        write_json_atomic(
            cpath,
            {
                "day": day,
                "schema": FEATURES_SCHEMA,
                "resume_hash": _digest_bytes(
                    {
                        "day": day,
                        "producer": producer,
                        "contract": contract_sha,
                        "engine": engine,
                        "labels": cov["labels"]["sha256"],
                        "schema": FEATURES_SCHEMA,
                    }
                ),
                "producer_sha256": producer,
                "engine_sha256": engine,
                "contract_sha256": contract_sha,
                "features_sha256": sha256_file(qpath),
                "coverage": cov,
            },
        )
        coverage["days_built"] += 1
        coverage["label_rows"] += int(cov["label_rows"])
        coverage["feature_rows"] += int(cov["feature_rows"])
        coverage["panel_rows"] += int(cov["panel_rows"])
        coverage["panel_qualified_rows"] += int(cov["panel_qualified_rows"])
        coverage["peer_join_missing_rows"] += int(cov["peer_join_missing_rows"])
        coverage["intent_clock_mismatches"] += int(cov["intent_clock_mismatches"])
        for b in cov["label_price_basis_values"]:
            if b not in coverage["label_price_basis_values"]:
                coverage["label_price_basis_values"].append(b)
        coverage["labels"][day] = cov["labels"]
        if n % 50 == 0 or n == len(todo):
            print(
                f"[features] {n}/{len(todo)} days, last {day}, rows={cov['label_rows']}",
                flush=True,
            )
    digest_path = out / "labels_digest.json"
    prior_days = {}
    if digest_path.exists():
        try:
            prior_days = json.loads(digest_path.read_text()).get("days", {})
        except (json.JSONDecodeError, OSError):
            prior_days = {}
    prior_days.update(coverage["labels"])
    write_json_atomic(digest_path, {"labels_root": str(labels_root), "days": prior_days})
    return coverage


def read_feature_days(out: Path, days: list[str]) -> pl.DataFrame:
    """All per-day feature parts of one day list (one day frame resident at a time)."""
    frames = []
    for day in days:
        qpath, _ = feature_paths(out, day)
        if not qpath.exists():
            raise SystemExit(
                f"[cross-rotation] feature part missing for {day} ({qpath}); rerun the "
                "feature stage for this block"
            )
        frames.append(pl.read_parquet(qpath))
    if frames:
        return pl.concat(frames, how="vertical_relaxed")
    return pl.DataFrame(schema=FEATURE_TYPES)


# ----- the fixed learner families ---------------------------------------------
def rotation_matrix(df: pl.DataFrame) -> np.ndarray:
    """The frozen 43-column matrix: base path + label quote context + peer additions.

    Same convention as the path's own ``feature_matrix`` helper: Float64 in the frozen
    order, nulls filled with 0.0 exactly once, no per-day or per-symbol imputation.
    """
    missing = [c for c in FEATURE_ORDER if c not in df.columns]
    if missing:
        raise ValueError(f"feature matrix missing columns {missing}")
    return (
        df.select([pl.col(c).cast(pl.Float64, strict=False) for c in FEATURE_ORDER])
        .fill_null(0.0)
        .to_numpy()
    )


def relevance_grades(y_clipped: np.ndarray) -> np.ndarray:
    """Fixed relevance grades over the clipped target range (ranker target only)."""
    span = CLIP[1] - CLIP[0]
    raw = np.floor((y_clipped - CLIP[0]) / span * RANK_GRADES)
    return np.clip(raw, 0, RANK_GRADES - 1).astype(np.int32)


def _fit_ridge(x: np.ndarray, y: np.ndarray) -> object:
    return make_pipeline(StandardScaler(), Ridge(alpha=RIDGE_ALPHA)).fit(x, y)


def _fit_lgbm_reg(x: np.ndarray, y: np.ndarray) -> object:
    return LGBMRegressor(objective="regression", **LGB_PARAMS).fit(x, y)


def _fit_lgbm_rank(x: np.ndarray, y: np.ndarray, groups: list[int]) -> tuple[object, dict]:
    grades = relevance_grades(y)
    # LightGBM's lambdarank ``label_gain`` DEFAULT is the exponential DCG gain
    # ``[2**i - 1]``, auto-sized to the number of label mappings it INFERS from the data;
    # that inference under-counts by one when the top grade (``RANK_GRADES - 1``) actually
    # occurs, so the fit aborts with "Label N is not less than the number of label
    # mappings". Pin the gain vector to the FULL frozen grade range 0..RANK_GRADES-1 so
    # every relevance grade is a valid mapping (LightGBM requires max(label) <
    # len(label_gain)); this keeps the default exponential DCG gain, changes only the
    # size, and does not touch the fit-set raw->return calibration below.
    label_gain = [float(2**g - 1) for g in range(RANK_GRADES)]
    if grades.size and int(grades.max()) >= len(label_gain):
        raise ValueError(
            f"[cross-rotation] lambdarank grade {int(grades.max())} >= label_gain length "
            f"{len(label_gain)}; relevance grades must stay in 0..RANK_GRADES-1 "
            f"(RANK_GRADES={RANK_GRADES})"
        )
    model = LGBMRanker(objective="lambdarank", label_gain=label_gain, **LGB_PARAMS).fit(
        x, grades, group=groups
    )
    # Fit-set-only linear calibration of the raw rank score to touch-return units so the
    # SAME fixed clearance and score bars apply to every family. Frozen into the bundle;
    # never re-fit on validation or late data.
    raw = np.asarray(model.predict(x), dtype=float)
    if raw.size >= 2 and float(np.std(raw)) > 0.0:
        a, b = np.polyfit(raw, y, 1)
        calib = {"a": float(a), "b": float(b), "n_fit_rows": int(raw.size)}
    else:
        calib = {"a": 0.0, "b": float(np.mean(y)) if raw.size else 0.0, "n_fit_rows": int(raw.size)}
    return model, calib


def calibrate(bundle: dict, raw_score: float) -> float:
    """The frozen fit-set calibration of one raw rank score (identity for other families)."""
    calib = bundle.get("calibration")
    if calib is None:
        return float(raw_score)
    return float(calib["a"]) * float(raw_score) + float(calib["b"])


def model_bundle_name(family: str, head: int) -> str:
    return f"{family}_h{head}"


def fit_models(
    train_frame: pl.DataFrame,
    out: Path,
    resume: bool,
    force_fit: bool,
    labels_digest: dict,
) -> tuple[dict, dict]:
    """Fit the three fixed families on the labeled train window (two heads each).

    The fit is entry-policy independent (the replay gate never changes the targets), so
    the frozen model card is keyed on the labels digest, the feature parts and the feature
    order only.
    """
    models_dir = out / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    card_path = models_dir / "model_card.json"
    engine = engine_sha256()
    features_digest = {
        day: json.loads((out / "features" / f"{day}.cov.json").read_text())["features_sha256"]
        for day in sorted(train_frame["day"].unique().to_list())
    }
    card_expect = {
        "engine_sha256": engine,
        "labels_digest": labels_digest,
        "features_digest": features_digest,
        "feature_order": FEATURE_ORDER,
        "families": list(MODEL_FAMILIES),
        "heads": list(HEADS),
    }
    if resume and not force_fit and card_path.exists():
        try:
            prior = json.loads(card_path.read_text())
            if prior.get("expect") == card_expect and prior.get("bundles"):
                bundles = {}
                ok = True
                for key, meta in prior["bundles"].items():
                    path = Path(meta["path"])
                    if not path.exists() or sha256_file(path) != meta["sha256"]:
                        ok = False
                        break
                    bundles[key] = joblib.load(path)
                if ok:
                    print(
                        f"[fit] reused {len(bundles)} frozen bundles from {card_path}",
                        flush=True,
                    )
                    return bundles, prior["report"]
        except (json.JSONDecodeError, OSError):
            pass

    x_all = rotation_matrix(train_frame)
    days_all = train_frame["day"].to_list()
    bundles: dict[str, dict] = {}
    report: dict = {
        "refit": True,
        "fit_block": f"{TRAIN_FIT_START}..2022-12-31 (the labels producer's 20-month train window)",
        "fit_days": int(train_frame["day"].n_unique()),
        "fit_rows": int(train_frame.height),
        "clip_fit_only": list(CLIP),
        "sample_weights": None,
        "feature_order": FEATURE_ORDER,
        "families": {},
    }
    for head in HEADS:
        known = train_frame.get_column(f"label_known_h{head}").to_numpy().astype(bool)
        y_raw = train_frame.get_column(f"y_touch_h{head}").to_numpy().astype(float)
        # The producer's fit-unusable bucket: a KNOWN label whose intent book was never
        # observed (entry_execution_status = no_order_intent_not_firm_fresh) carries NULL
        # quote features. Such a row is a known-cash outcome, NOT a tight book, so it is
        # excluded here rather than null-filled into a fabricated spread=0 observation.
        quote_seen = np.ones(train_frame.height, dtype=bool)
        for col in LABEL_QUOTE_FEATURES:
            quote_seen &= train_frame.get_column(col).is_not_null().to_numpy()
        ok_rows = known & np.isfinite(y_raw) & quote_seen
        if int(ok_rows.sum()) < 1:
            raise SystemExit(
                f"[cross-rotation] MISSING PREREQUISITE: zero usable h{head} label rows in "
                "the train window (known label, finite target, observed intent book); the "
                "labels producer must run first (no arbitrary count floor is applied - a "
                "small fit set is disclosed, never vetoed)"
            )
        idx = np.flatnonzero(ok_rows)
        x = x_all[idx]
        y = np.clip(y_raw[idx], CLIP[0], CLIP[1])
        days = [days_all[i] for i in idx]
        order = sorted(range(len(days)), key=lambda i: days[i])
        x, y = x[order], y[order]
        counted = Counter(days[i] for i in order)
        groups = [counted[d] for d in sorted(counted)]
        head_report = {
            "n_label_rows": int(train_frame.height),
            "n_known_rows": int(known.sum()),
            "n_known_but_quote_unobserved_excluded": int((known & ~quote_seen).sum()),
            "n_fit_rows": int(ok_rows.sum()),
            "known_row_fraction": float(known.mean()),
            "fit_usable_fraction": float(ok_rows.mean()),
            "n_fit_days": len(counted),
            "y_mean_unclipped": float(np.mean(y_raw[idx])),
            "y_std_unclipped": float(np.std(y_raw[idx])),
            "y_min_unclipped": float(np.min(y_raw[idx])),
            "y_max_unclipped": float(np.max(y_raw[idx])),
            "missingness_note": "rows with an unknown/partial leg for this head, and known "
            "rows whose intent book was never observed (NULL quote features: a known-cash "
            "outcome, not a tight book), are excluded from this head's fit and disclosed "
            "here, never imputed",
        }
        for family in MODEL_FAMILIES:
            if family == "ridge":
                model = _fit_ridge(x, y)
                calib = None
            elif family == "lgbm_reg":
                model = _fit_lgbm_reg(x, y)
                calib = None
            else:
                model, calib = _fit_lgbm_rank(x, y, groups)
            bundle = {
                "family": family,
                "head": head,
                "feature_order": list(FEATURE_ORDER),
                "clip_fit_only": list(CLIP),
                "calibration": calib,
                "model": model,
                "fit": head_report,
            }
            key = model_bundle_name(family, head)
            path = models_dir / f"{key}.joblib"
            joblib.dump(bundle, path)
            bundles[key] = bundle
            head_report.setdefault("bundles", {})[family] = {
                "path": str(path),
                "sha256": sha256_file(path),
                "calibration": calib,
            }
        report["families"][f"h{head}"] = head_report
        print(
            f"[fit] h{head}: {int(ok_rows.sum())} known rows / {len(counted)} days -> "
            + ", ".join(f"{fam} ok" for fam in MODEL_FAMILIES),
            flush=True,
        )
    card = {
        "expect": card_expect,
        "bundles": {
            key: {
                "path": str(models_dir / f"{key}.joblib"),
                "sha256": sha256_file(models_dir / f"{key}.joblib"),
            }
            for key in bundles
        },
        "report": report,
    }
    write_json_atomic(card_path, card)
    return bundles, report


def predict_bundles(bundles: dict, frame: pl.DataFrame) -> dict[str, np.ndarray]:
    """Calibrated predictions of every (family, head) bundle on one day's rows."""
    if not frame.height:
        return {model_bundle_name(f, h): np.zeros(0) for f in MODEL_FAMILIES for h in HEADS}
    x = rotation_matrix(frame)
    out = {}
    for key, bundle in bundles.items():
        raw = np.asarray(bundle["model"].predict(x), dtype=float)
        out[key] = np.asarray([calibrate(bundle, float(v)) for v in raw], dtype=float)
    return out


# ----- the causal execution policy --------------------------------------------
def intent_facts(sq: SymbolQuotes | None, intent_us: int) -> dict:
    """The causal intent observation and the size it implies (entry rule, part 1).

    Identical observation physics to the shared engine (latest RAW state at the intent
    clock, never pre-filtered); the sizing uses THIS study's frozen fee cap: the teacher's
    intent fee cap ``FEE_CAP_BPS`` for the causal quantity and the worst-case
    ``RESERVE_BPS`` for the reservation, so every reported rung stays funded.
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
            limit = limit_price_of(q["ask"])
            qty = causal_quantity(limit, FEE_CAP_BPS)
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
                    "reserved_usd": reserved_usd(qty, limit, RESERVE_BPS),
                }
            )
    return base


def touch_only_rule_status(facts: dict) -> tuple[str, bool]:
    """The model-free baseline gate: an observable book and a fundable depth, no score."""
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
    return "eligible", True


def touch_clearance_status(facts: dict, pred: float) -> tuple[str, bool]:
    """The PRIMARY entry gate for a forecast trained on the actual-touch payoff.

    The predicted touch payoff ALREADY nets the observed spread: the entry is the ASK and
    the exit is the BID, so the spread is paid inside the predicted number. Charging the
    observed spread at the gate as well would double-count it, so the primary gate is the
    economic residual instead: the predicted gross-before-extra touch payoff must clear
    the fixed ``TOUCH_CLEARANCE_BPS`` (the "25-extra" provider residual plus clearance).
    The observable-book gates (firm/fresh intent, min-capital quantity, intent depth) are
    identical to the shared engine's operational checks.
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
    if 10_000.0 * float(pred) < TOUCH_CLEARANCE_BPS:
        return "touch_payoff_below_clearance", False
    return "eligible", True


# ----- per-day state artifacts (incremental, resumable) -----------------------
STATE_COLUMNS = (
    "day",
    "ticker",
    "t",
    "entry_minute",
    "intent_us",
    "session_end",
    "intent_status",
    "intent_ask",
    "intent_bid",
    "intent_age_s",
    "intent_spread_bps",
    "intent_ask_shares",
    "limit_price",
    "qty",
    "reserved_usd",
    *(f"pred_{model_bundle_name(f, h)}" for f in MODEL_FAMILIES for h in HEADS),
    *(f"rule_{model_bundle_name(f, h)}" for f in MODEL_FAMILIES for h in HEADS),
    "rule_touch_only",
    "y_touch_h15",
    "y_touch_h60",
    "label_known_h15",
    "label_known_h60",
)
STATE_TYPES = {
    "day": pl.String,
    "ticker": pl.String,
    "t": pl.Int64,
    "entry_minute": pl.Int64,
    "intent_us": pl.Int64,
    "session_end": pl.Int64,
    "intent_status": pl.String,
    "intent_ask": pl.Float64,
    "intent_bid": pl.Float64,
    "intent_age_s": pl.Float64,
    "intent_spread_bps": pl.Float64,
    "intent_ask_shares": pl.Float64,
    "limit_price": pl.Float64,
    "qty": pl.Int64,
    "reserved_usd": pl.Float64,
    **{f"pred_{model_bundle_name(f, h)}": pl.Float64 for f in MODEL_FAMILIES for h in HEADS},
    **{f"rule_{model_bundle_name(f, h)}": pl.Boolean for f in MODEL_FAMILIES for h in HEADS},
    "rule_touch_only": pl.Boolean,
    "y_touch_h15": pl.Float64,
    "y_touch_h60": pl.Float64,
    "label_known_h15": pl.Boolean,
    "label_known_h60": pl.Boolean,
}


def empty_states() -> pl.DataFrame:
    return pl.DataFrame(schema={c: STATE_TYPES[c] for c in STATE_COLUMNS})


def state_paths(out: Path, day: str) -> tuple[Path, Path]:
    return out / "states" / f"{day}.parquet", out / "states" / f"{day}.cov.json"


def score_day(
    day: str,
    bundles: dict,
    data_root: Path,
    supplemental_roots: tuple[Path, ...],
    out: Path,
) -> tuple[pl.DataFrame, dict]:
    """Score one session's feature matrix, then observe each intent's RAW quote state.

    One feature frame, one quote frame and one day's states are resident at a time; the
    full corpus is never concatenated. The intent facts are causal (latest RAW state at
    the (t+1) clock 0) and the label's own quote observation is cross-checked against this
    replay observation (disclosed, never silently trusted).
    """
    if not allowed(day):
        raise ValueError(f"protected/out-of-scope day refused: {day}")
    qpath, _ = feature_paths(out, day)
    if not qpath.exists():
        raise SystemExit(
            f"[cross-rotation] feature part missing for {day} ({qpath}); run the feature "
            "stage for this block first"
        )
    feat = pl.read_parquet(qpath)
    cov = {
        "qualified_rows": int(feat.height),
        "tickers": 0,
        "quote_streams": 0,
        "quote_day_file_present": True,
        "intent_status_counts": {},
        "rule_counts": {},
        "label_quote_disagreements": 0,
        "label_qty_disagreements": 0,
        "label_known_fraction_h15": None,
        "label_known_fraction_h60": None,
    }
    if not feat.height:
        return empty_states(), cov
    preds = predict_bundles(bundles, feat)
    tickers = sorted(set(feat["ticker"].to_list()))
    cov["tickers"] = len(tickers)
    quote_frame = quote_day_frame(data_root, day, set(tickers), supplemental_roots)
    streams = symbol_quotes(quote_frame, day)
    cov["quote_streams"] = len(streams)
    if not (data_root / "sip" / "net" / "quotes" / f"{day}.parquet").exists():
        cov["quote_day_file_present"] = False
    del quote_frame
    rows = feat.select(
        [
            "day",
            "ticker",
            "t",
            "session_end",
            "spread_bps",
            "causal_qty",
            "y_touch_h15",
            "y_touch_h60",
            "label_known_h15",
            "label_known_h60",
        ]
    ).to_dicts()
    intent_counts: Counter = Counter()
    rule_counts: Counter = Counter()
    disagreements = 0
    qty_disagreements = 0
    out_rows: list[dict] = []
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
            "entry_minute": entry_minute,
            "intent_us": intent_us,
            "session_end": int(r["session_end"]),
            "y_touch_h15": r["y_touch_h15"],
            "y_touch_h60": r["y_touch_h60"],
            "label_known_h15": bool(r["label_known_h15"]),
            "label_known_h60": bool(r["label_known_h60"]),
        }
        rec.update({k: facts[k] for k in STATE_TYPES if k in facts})
        for family in MODEL_FAMILIES:
            for head in HEADS:
                key = model_bundle_name(family, head)
                pred = float(preds[key][i])
                rec[f"pred_{key}"] = pred
                reason, ok = touch_clearance_status(facts, pred)
                rec[f"rule_{key}"] = bool(ok)
                rule_counts[f"{family}_h{head}:{reason}"] += 1
        reason, ok = touch_only_rule_status(facts)
        rec["rule_touch_only"] = bool(ok)
        rule_counts[f"touch_only:{reason}"] += 1
        lbl_spread = r["spread_bps"]
        if (
            lbl_spread is not None
            and facts["intent_spread_bps"] is not None
            and abs(float(lbl_spread) - float(facts["intent_spread_bps"])) > 1e-6
        ):
            disagreements += 1
        if r["causal_qty"] is not None and int(r["causal_qty"]) != int(facts["qty"]):
            qty_disagreements += 1
        out_rows.append(rec)
    cov["intent_status_counts"] = dict(intent_counts)
    cov["rule_counts"] = dict(rule_counts)
    cov["label_quote_disagreements"] = disagreements
    cov["label_qty_disagreements"] = qty_disagreements
    cov["label_known_fraction_h15"] = float(np.mean([bool(r["label_known_h15"]) for r in rows]))
    cov["label_known_fraction_h60"] = float(np.mean([bool(r["label_known_h60"]) for r in rows]))
    frame = pl.DataFrame(
        {c: [rec[c] for rec in out_rows] for c in STATE_COLUMNS},
        schema={c: STATE_TYPES[c] for c in STATE_COLUMNS},
    )
    return frame, cov


def card_model_shas(out: Path) -> dict[str, str]:
    """The authoritative per-bundle SHA registry from the frozen model card."""
    card_path = out / "models" / "model_card.json"
    if not card_path.exists():
        raise SystemExit(
            f"[cross-rotation] model card missing ({card_path}); run the fit stage first"
        )
    card = json.loads(card_path.read_text())
    shas = {key: meta["sha256"] for key, meta in card["bundles"].items()}
    expected = {model_bundle_name(f, h) for f in MODEL_FAMILIES for h in HEADS}
    if set(shas) != expected:
        raise SystemExit(
            f"[cross-rotation] model card {card_path} has bundles {sorted(shas)}, "
            f"expected {sorted(expected)}; rerun the fit stage (--force-fit)"
        )
    return shas


def score_block(
    out: Path,
    bundles: dict,
    days: list[str],
    resume: bool,
    supplemental_roots: tuple[Path, ...],
    contract_sha: str,
    data_root: Path,
) -> dict:
    """Build (or resume) every day's scored states atomically, one day at a time."""
    producer = producer_sha256()
    engine = engine_sha256()
    model_shas = card_model_shas(out)
    sup_digest, _ = supplement_digest(supplemental_roots)
    coverage = {
        "days_total": len(days),
        "days_cached": 0,
        "days_scored": 0,
        "qualified_rows": 0,
        "intent_status_counts": {},
        "rule_counts": {},
        "label_quote_disagreements": 0,
        "label_qty_disagreements": 0,
        "days_without_quote_stream": [],
        "missing_quote_day_files": [],
        "supplement_digest": sup_digest,
        "supplemental_roots_existing": [str(r) for r in supplemental_roots if r.exists()],
    }
    intent_totals: Counter = Counter()
    rule_totals: Counter = Counter()
    todo, cached = [], []
    for day in days:
        qpath, cpath = state_paths(out, day)
        _, fpath = feature_paths(out, day)
        features_sha = None
        if fpath.exists():
            try:
                features_sha = json.loads((out / "features" / f"{day}.cov.json").read_text())[
                    "features_sha256"
                ]
            except (json.JSONDecodeError, OSError):
                features_sha = None
        fresh = False
        prior = None
        if resume and cpath.exists() and qpath.exists():
            try:
                prior = json.loads(cpath.read_text())
                fresh = (
                    prior.get("resume_hash")
                    == _digest_bytes(
                        {
                            "day": day,
                            "producer": producer,
                            "contract": contract_sha,
                            "engine": engine,
                            "features": features_sha,
                            "models": model_shas,
                            "supplements": sup_digest,
                            "schema": STATES_SCHEMA,
                        }
                    )
                    and prior.get("schema") == STATES_SCHEMA
                )
            except (json.JSONDecodeError, OSError):
                fresh = False
        if fresh:
            cached.append((day, prior))
        else:
            todo.append(day)
    for day, prior in cached:
        cov = prior.get("coverage", {})
        coverage["days_cached"] += 1
        coverage["qualified_rows"] += int(cov.get("qualified_rows", 0))
        coverage["label_quote_disagreements"] += int(cov.get("label_quote_disagreements", 0))
        coverage["label_qty_disagreements"] += int(cov.get("label_qty_disagreements", 0))
        intent_totals.update(cov.get("intent_status_counts") or {})
        rule_totals.update(cov.get("rule_counts") or {})
        if cov.get("quote_day_file_present") is False:
            coverage["missing_quote_day_files"].append(day)
        if int(cov.get("quote_streams", 0)) == 0 and int(cov.get("qualified_rows", 0)) > 0:
            coverage["days_without_quote_stream"].append(day)
    for n, day in enumerate(todo, 1):
        states, cov = score_day(day, bundles, data_root, supplemental_roots, out)
        qpath, cpath = state_paths(out, day)
        qpath.parent.mkdir(parents=True, exist_ok=True)
        tmp = qpath.with_name(f"{qpath.name}.tmp{os.getpid()}")
        states.write_parquet(tmp)
        os.replace(tmp, qpath)
        features_sha = None
        try:
            features_sha = json.loads((out / "features" / f"{day}.cov.json").read_text())[
                "features_sha256"
            ]
        except (json.JSONDecodeError, OSError):
            features_sha = None
        write_json_atomic(
            cpath,
            {
                "day": day,
                "schema": STATES_SCHEMA,
                "resume_hash": _digest_bytes(
                    {
                        "day": day,
                        "producer": producer,
                        "contract": contract_sha,
                        "engine": engine,
                        "features": features_sha,
                        "models": model_shas,
                        "supplements": sup_digest,
                        "schema": STATES_SCHEMA,
                    }
                ),
                "producer_sha256": producer,
                "engine_sha256": engine,
                "contract_sha256": contract_sha,
                "states_sha256": sha256_file(qpath),
                "features_sha256": features_sha,
                "model_shas": model_shas,
                "coverage": cov,
            },
        )
        coverage["days_scored"] += 1
        coverage["qualified_rows"] += int(cov["qualified_rows"])
        coverage["label_quote_disagreements"] += int(cov["label_quote_disagreements"])
        coverage["label_qty_disagreements"] += int(cov["label_qty_disagreements"])
        intent_totals.update(cov.get("intent_status_counts") or {})
        rule_totals.update(cov.get("rule_counts") or {})
        if cov.get("quote_day_file_present") is False:
            coverage["missing_quote_day_files"].append(day)
        if int(cov.get("quote_streams", 0)) == 0 and int(cov.get("qualified_rows", 0)) > 0:
            coverage["days_without_quote_stream"].append(day)
        if n % 50 == 0 or n == len(todo):
            print(
                f"[score] {n}/{len(todo)} days, last {day}, rows={cov['qualified_rows']}, "
                f"quote_streams={cov['quote_streams']}, "
                f"label_quote_disagreements={cov['label_quote_disagreements']}",
                flush=True,
            )
    coverage.update(
        {
            "intent_status_counts": dict(intent_totals),
            "rule_counts": dict(rule_totals),
            "model_shas": model_shas,
        }
    )
    return coverage


# ----- per-view causal replay -------------------------------------------------
DAILY_COLUMNS = (
    "day",
    "view_key",
    "family",
    "head",
    "threshold",
    "signals",
    "below_threshold",
    "intents_funded",
    "attempts",
    "fills",
    "known_fills",
    "unknown_fills",
    "no_match_fills",
    "unknown_reserved_usd",
    "total_reserved_usd",
    "peak_reserved_usd",
    "positions_peak",
    "positions_mean",
    *[f"known_usd_{int(r)}" for r in EXTRA_FEE_RUNGS],
    *[f"lower_bound_usd_{int(r)}" for r in EXTRA_FEE_RUNGS],
    "no_order_min_capital",
    "quote_not_acquired",
    "intent_not_firm_fresh",
    "intent_depth_unsupported",
    "intent_depth_unknown",
    "touch_payoff_below_clearance",
    "overlap_skips",
    "cooldown_skips",
    "max_attempt_skips",
    "cash_or_slot_skips",
)
DAILY_TYPES = {
    "day": pl.String,
    "view_key": pl.String,
    "family": pl.String,
    "head": pl.Int64,
    "threshold": pl.Float64,
    "signals": pl.Int64,
    "below_threshold": pl.Int64,
    "intents_funded": pl.Int64,
    "attempts": pl.Int64,
    "fills": pl.Int64,
    "known_fills": pl.Int64,
    "unknown_fills": pl.Int64,
    "no_match_fills": pl.Int64,
    "unknown_reserved_usd": pl.Float64,
    "total_reserved_usd": pl.Float64,
    "peak_reserved_usd": pl.Float64,
    "positions_peak": pl.Int64,
    "positions_mean": pl.Float64,
    **{f"known_usd_{int(r)}": pl.Float64 for r in EXTRA_FEE_RUNGS},
    **{f"lower_bound_usd_{int(r)}": pl.Float64 for r in EXTRA_FEE_RUNGS},
    "no_order_min_capital": pl.Int64,
    "quote_not_acquired": pl.Int64,
    "intent_not_firm_fresh": pl.Int64,
    "intent_depth_unsupported": pl.Int64,
    "intent_depth_unknown": pl.Int64,
    "touch_payoff_below_clearance": pl.Int64,
    "overlap_skips": pl.Int64,
    "cooldown_skips": pl.Int64,
    "max_attempt_skips": pl.Int64,
    "cash_or_slot_skips": pl.Int64,
}
TRADE_COLUMNS = (
    "day",
    "view_key",
    "family",
    "head",
    "threshold",
    "ticker",
    "t",
    "entry_minute",
    "intent_us",
    "score",
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
    "exit_price_status",
    "exit_bid",
    "exit_age_s",
    "exit_quote_us",
    "exit_status",
    "actual_exit_us",
    "fill_status",
    *[f"net_usd_{int(r)}" for r in EXTRA_FEE_RUNGS],
    "y_touch_h15",
    "y_touch_h60",
    "label_known_h15",
    "label_known_h60",
)
TRADE_TYPES = {
    "day": pl.String,
    "view_key": pl.String,
    "family": pl.String,
    "head": pl.Int64,
    "threshold": pl.Float64,
    "ticker": pl.String,
    "t": pl.Int64,
    "entry_minute": pl.Int64,
    "intent_us": pl.Int64,
    "score": pl.Float64,
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
    "exit_price_status": pl.String,
    "exit_bid": pl.Float64,
    "exit_age_s": pl.Float64,
    "exit_quote_us": pl.Int64,
    "exit_status": pl.String,
    "actual_exit_us": pl.Int64,
    "fill_status": pl.String,
    **{f"net_usd_{int(r)}": pl.Float64 for r in EXTRA_FEE_RUNGS},
    "y_touch_h15": pl.Float64,
    "y_touch_h60": pl.Float64,
    "label_known_h15": pl.Boolean,
    "label_known_h60": pl.Boolean,
}


def empty_daily() -> pl.DataFrame:
    return pl.DataFrame(schema={c: DAILY_TYPES[c] for c in DAILY_COLUMNS})


def empty_trades() -> pl.DataFrame:
    return pl.DataFrame(schema={c: TRADE_TYPES[c] for c in TRADE_COLUMNS})


def state_facts(r: dict) -> dict:
    """The head-independent intent facts carried in one scored state row."""
    return {
        "intent_status": r["intent_status"],
        "intent_age_s": r["intent_age_s"],
        "qty": int(r["qty"]),
        "intent_ask_shares": r["intent_ask_shares"],
        "intent_spread_bps": r["intent_spread_bps"],
    }


def view_reason(facts: dict, view: View, score: float) -> str:
    """The skip reason of one view's failed candidate (the barrier it tripped first).

    The primary policy's payoff floor is ``touch_clearance_status`` (no spread charged -
    it is already inside the touch target); there is no alternative gate.
    """
    if view.baseline:
        return touch_only_rule_status(facts)[0]
    return touch_clearance_status(facts, score)[0]


def resolve_trade(
    day: str, r: dict, view: View, sq: SymbolQuotes, session_end: int, session_end_us: int
) -> dict:
    """Resolve one funded intent: the entry IOC-limit leg, then the scheduled MARKET exit.

    The entry leg prices the latest RAW state at the arrival clock EXACTLY ONCE (an IOC
    never rests to a later eligible print, never substitutes one and never re-sizes); the
    exit leg is a genuine MARKET order whose submission is fresh-quote-gated and whose
    arrival may rest only after a genuinely unavailable book. Nothing here re-sizes the
    causal quantity.
    """
    head = view.head
    score = 0.0 if view.baseline else float(r[view.pred_col])
    record = {
        "day": day,
        "view_key": view.key,
        "family": view.family,
        "head": head,
        "threshold": view.threshold,
        "ticker": r["ticker"],
        "t": int(r["t"]),
        "entry_minute": int(r["entry_minute"]),
        "intent_us": int(r["intent_us"]),
        "score": score,
        "limit_price": float(r["limit_price"]),
        "qty": int(r["qty"]),
        "reserved_usd": float(r["reserved_usd"]),
        "y_touch_h15": r["y_touch_h15"],
        "y_touch_h60": r["y_touch_h60"],
        "label_known_h15": bool(r["label_known_h15"]),
        "label_known_h60": bool(r["label_known_h60"]),
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
        "exit_price_status": None,
        "exit_bid": None,
        "exit_age_s": None,
        "exit_quote_us": None,
        "exit_status": None,
        "actual_exit_us": None,
        "fill_status": None,
    }
    for rung in EXTRA_FEE_RUNGS:
        record[f"net_usd_{int(rung)}"] = None

    # ---- entry leg: the causal IOC limit was fixed at the intent clock; it is priced
    #      exactly once at the arrival clock and never rests to a later print.
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

    # ---- exit leg: fresh-quote-gated MARKET submission, +250ms after the ACTUAL
    #      submission; a genuinely unavailable book may rest, an aged-but-valid one
    #      never does (stale-but-valid at arrival is an UNKNOWN, not a later print).
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
    # the working order had already rested. It is NEVER the prior quote's stamp (which can
    # predate arrival) and never the original exit arrival when the order rested: cash,
    # the slot and the cooldown all anchor to this real clock, or stay reserved.
    record["actual_exit_us"] = int(priced_at) if priced_at is not None else exit_arrival
    for rung in EXTRA_FEE_RUNGS:
        record[f"net_usd_{int(rung)}"] = net_usd(
            record["qty"], record["exit_bid"], record["entry_ask"], rung
        )
    return record


def _new_daily_row(day: str, view: View) -> dict:
    row = dict.fromkeys(DAILY_COLUMNS, 0)
    row.update(
        {
            "day": day,
            "view_key": view.key,
            "family": view.family,
            "head": view.head,
            "threshold": view.threshold,
            "unknown_reserved_usd": 0.0,
            "total_reserved_usd": 0.0,
            "peak_reserved_usd": 0.0,
            "positions_mean": 0.0,
        }
    )
    for rung in EXTRA_FEE_RUNGS:
        row[f"known_usd_{int(rung)}"] = 0.0
        row[f"lower_bound_usd_{int(rung)}"] = 0.0
    return row


def replay_day(
    day: str,
    states: pl.DataFrame,
    streams: dict[str, SymbolQuotes],
    view: View,
) -> tuple[dict, list[dict]]:
    """One view, one session: fresh-quote-gated entries under the funded $750 book.

    Chronological by intent clock; the highest-score eligible intents of a simultaneous
    clock are funded (up to the slot and cash limits) BEFORE any arrival outcome is seen,
    so a fourth same-clock candidate is never substituted after an unfilled one. Cash comes
    back only at an ACTUAL resolution clock (a conditional exit, an unfilled no-match
    arrival, or the session end); an UNKNOWN execution keeps its slot, its ticker block
    and its reserved cash until the session ends. ``peak_reserved_usd`` and
    ``positions_peak`` are swept from this day's own funding/release events, so a later
    month can never silently aggregate a re-zeroed last-day accumulator.
    """
    daily = _new_daily_row(day, view)
    if not states.height:
        return daily, []
    session_end = int(states["session_end"][0])
    session_end_us = minute_us(day, session_end)
    rule_col = view.rule_col
    if view.baseline:
        candidates = states.sort(["entry_minute", "ticker"])
    else:
        candidates = states.filter(pl.col(view.pred_col) >= view.threshold).sort(
            ["entry_minute", view.pred_col, "ticker"], descending=[False, True, False]
        )
    daily["signals"] = int(candidates.height)
    daily["below_threshold"] = int(states.height - candidates.height)
    groups: dict[int, list[dict]] = {}
    for r in candidates.iter_rows(named=True):
        groups.setdefault(int(r["entry_minute"]), []).append(r)

    cash, active = float(BOOK), set()
    attempts: dict[str, int] = {}
    cooldown_until: dict[str, int] = {}
    queue: list[tuple[int, int, str, float]] = []
    events: list[tuple[int, float, int]] = []  # (clock, d_reserved, d_positions)
    seq = 0
    trades: list[dict] = []
    positions_samples: list[int] = []
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
                daily["intents_funded"] += 1
                daily["attempts"] += 1
                daily["total_reserved_usd"] += float(r["reserved_usd"])
                events.append((intent_us, float(r["reserved_usd"]), 1))
                admitted.append(r)
            else:
                score = 0.0 if view.baseline else float(r[view.pred_col])
                daily[view_reason(state_facts(r), view, score)] += 1
        # Every intent of this clock is funded before any arrival outcome is observed.
        for r in admitted:
            record = resolve_trade(day, r, view, streams[r["ticker"]], session_end, session_end_us)
            record["attempt_index"] = int(attempts[r["ticker"]])
            record["positions_at_entry"] = len(active)
            status = record["fill_status"]
            release = int(record["actual_exit_us"])
            events.append((release, -float(r["reserved_usd"]), -1))
            if status == "no_match_at_l1_unfilled_cash":
                # No position was opened: the ticket is released at the arrival clock and
                # there is no exit to anchor a cooldown on (the attempt still counts).
                daily["no_match_fills"] += 1
            else:
                daily["unknown_fills" if "unknown" in status else "fills"] += 1
                if status == "conditional_fill":
                    daily["known_fills"] += 1
                    for rung in EXTRA_FEE_RUNGS:
                        daily[f"known_usd_{int(rung)}"] += float(record[f"net_usd_{int(rung)}"])
                else:
                    daily["unknown_reserved_usd"] += float(r["reserved_usd"])
                # A position - conditional or UNKNOWN - is open until its ACTUAL
                # resolution, and the ticker is then blocked for a flat 5 minutes.
                cooldown_until[r["ticker"]] = release + COOLDOWN_MIN * 60_000_000
            heapq.heappush(queue, (release, seq, r["ticker"], float(r["reserved_usd"])))
            seq += 1
            trades.append(record)
    # True intraday capital facts: sweep this day's own events (releases before fundings
    # at the same clock), never a re-zeroed accumulator carried across days.
    running_reserved, running_positions = 0.0, 0
    peak_reserved, peak_positions = 0.0, 0
    for _, d_reserved, d_positions in sorted(events, key=lambda e: (e[0], e[1], e[2])):
        running_reserved += d_reserved
        running_positions += d_positions
        peak_reserved = max(peak_reserved, running_reserved)
        peak_positions = max(peak_positions, running_positions)
    daily["peak_reserved_usd"] = float(peak_reserved)
    daily["positions_peak"] = int(peak_positions)
    daily["positions_mean"] = float(np.mean(positions_samples)) if positions_samples else 0.0
    for rung in EXTRA_FEE_RUNGS:
        daily[f"lower_bound_usd_{int(rung)}"] = (
            daily[f"known_usd_{int(rung)}"] - daily["unknown_reserved_usd"]
        )
    return daily, trades


def replay_block(
    out: Path,
    block: str,
    days: list[str],
    resume: bool,
    supplemental_roots: tuple[Path, ...],
    contract_sha: str,
    data_root: Path,
) -> dict:
    """Replay every view on every day of one block, writing resumable per-day parts."""
    producer = producer_sha256()
    engine = engine_sha256()
    contract = contract_sha
    root = out / "replay" / block
    root.mkdir(parents=True, exist_ok=True)
    coverage = {
        "block": block,
        "days_total": len(days),
        "days_cached": 0,
        "days_replayed": 0,
        "signals": 0,
        "intents_funded": 0,
        "attempts": 0,
        "fills": 0,
        "unknown_fills": 0,
        "no_match_fills": 0,
    }
    model_shas = card_model_shas(out)
    sup_digest, _ = supplement_digest(supplemental_roots)
    todo, cached = [], []
    for day in days:
        dpath, tpath, cpath = (
            root / f"{day}.parquet",
            root / f"{day}.trades.parquet",
            root / f"{day}.cov.json",
        )
        states_path, states_cov = state_paths(out, day)
        states_sha = None
        if states_path.exists():
            try:
                states_sha = json.loads(states_cov.read_text()).get("states_sha256")
            except (json.JSONDecodeError, OSError):
                states_sha = None
            if states_sha is None:
                states_sha = sha256_file(states_path)
        fresh = False
        prior = None
        if resume and cpath.exists() and dpath.exists() and tpath.exists():
            try:
                prior = json.loads(cpath.read_text())
                fresh = (
                    prior.get("resume_hash")
                    == _digest_bytes(
                        {
                            "day": day,
                            "producer": producer,
                            "contract": contract,
                            "engine": engine,
                            "states": states_sha,
                            "models": model_shas,
                            "supplements": sup_digest,
                            "views": [v.key for v in VIEWS],
                            "schema": REPLAY_SCHEMA,
                        }
                    )
                    and prior.get("schema") == REPLAY_SCHEMA
                )
            except (json.JSONDecodeError, OSError):
                fresh = False
        if fresh:
            cached.append((day, prior))
        else:
            todo.append(day)
    for _day, prior in cached:
        cov = prior.get("coverage", {})
        coverage["days_cached"] += 1
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
    for n, day in enumerate(todo, 1):
        states_path, _ = state_paths(out, day)
        if not states_path.exists():
            raise SystemExit(
                f"[cross-rotation] scored states missing for {day} ({states_path}); run the "
                "score stage for the whole block before replaying it"
            )
        states = pl.read_parquet(states_path)
        tickers = sorted(set(states["ticker"].to_list())) if states.height else []
        quote_frame = (
            quote_day_frame(data_root, day, set(tickers), supplemental_roots) if tickers else None
        )
        streams = symbol_quotes(quote_frame, day)
        del quote_frame
        daily_rows, trade_rows = [], []
        day_cov = {"qualified_rows": int(states.height), "quote_streams": len(streams), "views": {}}
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
                "known_usd_25": daily["known_usd_25"],
                "peak_reserved_usd": daily["peak_reserved_usd"],
                "positions_peak": daily["positions_peak"],
            }
        daily_frame = pl.DataFrame(daily_rows, schema={c: DAILY_TYPES[c] for c in DAILY_COLUMNS})
        trade_frame = (
            pl.DataFrame(trade_rows, schema={c: TRADE_TYPES[c] for c in TRADE_COLUMNS})
            if trade_rows
            else empty_trades()
        )
        dpath = root / f"{day}.parquet"
        tpath = root / f"{day}.trades.parquet"
        cpath = root / f"{day}.cov.json"
        tmp_d = dpath.with_name(f"{dpath.name}.tmp{os.getpid()}")
        daily_frame.write_parquet(tmp_d)
        os.replace(tmp_d, dpath)
        tmp_t = tpath.with_name(f"{tpath.name}.tmp{os.getpid()}")
        trade_frame.write_parquet(tmp_t)
        os.replace(tmp_t, tpath)
        states_sha = None
        try:
            states_sha = json.loads((out / "states" / f"{day}.cov.json").read_text()).get(
                "states_sha256"
            )
        except (json.JSONDecodeError, OSError):
            states_sha = sha256_file(states_path)
        write_json_atomic(
            cpath,
            {
                "day": day,
                "schema": REPLAY_SCHEMA,
                "resume_hash": _digest_bytes(
                    {
                        "day": day,
                        "producer": producer,
                        "contract": contract,
                        "engine": engine,
                        "states": states_sha,
                        "models": model_shas,
                        "supplements": sup_digest,
                        "views": [v.key for v in VIEWS],
                        "schema": REPLAY_SCHEMA,
                    }
                ),
                "producer_sha256": producer,
                "engine_sha256": engine,
                "contract_sha256": contract,
                "states_sha256": states_sha,
                "model_shas": model_shas,
                "coverage": day_cov,
            },
        )
        coverage["days_replayed"] += 1
        coverage["signals"] += sum(v["signals"] for v in day_cov["views"].values())
        coverage["intents_funded"] += sum(v["attempts"] for v in day_cov["views"].values())
        coverage["fills"] += sum(v["fills"] for v in day_cov["views"].values())
        coverage["unknown_fills"] += sum(v["unknown_fills"] for v in day_cov["views"].values())
        coverage["no_match_fills"] += sum(v["no_match_fills"] for v in day_cov["views"].values())
        if n % 25 == 0 or n == len(todo):
            funded = sum(v["attempts"] for v in day_cov["views"].values())
            print(
                f"[replay:{block}] {n}/{len(todo)} days, last {day}, "
                f"funded_intents={funded} over {len(VIEWS)} views",
                flush=True,
            )
    return coverage


# ----- block aggregation ------------------------------------------------------
def read_block_frames(out: Path, block: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    """All per-day parts of one block: the daily rows and the funded-intent trades."""
    root = out / "replay" / block
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
            "mean_daily_known_usd_25": float(rows["known_usd_25"].mean()),
            "peak_reserved_usd_day_max": float(rows["peak_reserved_usd"].max() or 0.0),
            "positions_peak_day_max": int(rows["positions_peak"].max() or 0),
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
            "peak_reserved_usd_day_max": float(rows["peak_reserved_usd"].max() or 0.0),
            "positions_peak_day_max": int(rows["positions_peak"].max() or 0),
            "months_positive_known_usd_25": int(
                sum(1 for m, cell in monthly_view(rows).items() if cell["known_usd_25"] > 0)
            ),
            "months_total": len(monthly_view(rows)),
        }
    return out


def view_cell(daily: pl.DataFrame, trades: pl.DataFrame, view: View, days: list[str]) -> dict:
    """Every reported number for one view on one block's calendar, at every rung."""
    rows = daily.filter(pl.col("view_key") == view.key)
    n_days = len(days)
    if int(rows.height) != n_days:
        raise ValueError(
            f"view {view.key}: {rows.height} daily rows on disk for {n_days} calendar days; "
            "replay the whole block before aggregating"
        )
    rung_keys = [int(r) for r in EXTRA_FEE_RUNGS]
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
        "family": view.family,
        "head": view.head,
        "threshold": view.threshold,
        "days_replayed": n_days,
        "signals": int(rows["signals"].sum()),
        "below_threshold": int(rows["below_threshold"].sum()),
        "intents_funded": int(rows["intents_funded"].sum()),
        "attempts": int(rows["attempts"].sum()),
        "fills": int(rows["fills"].sum()),
        "known_fills": int(rows["known_fills"].sum()),
        "unknown_fills": int(rows["unknown_fills"].sum()),
        "no_match_fills": int(rows["no_match_fills"].sum()),
        "traded_days": int((rows["attempts"] > 0).sum()),
        "positions_peak": int(rows["positions_peak"].max() or 0),
        "positions_mean": float(rows["positions_mean"].mean()),
        "peak_reserved_usd_day_max": float(rows["peak_reserved_usd"].max() or 0.0),
        "unknown_share_of_attempts": (
            float(rows["unknown_fills"].sum() / rows["attempts"].sum())
            if int(rows["attempts"].sum())
            else None
        ),
        "known_usd": {str(r): known_total[r] for r in rung_keys},
        "known_usd_per_calendar_day": {str(r): known_per_day[r] for r in rung_keys},
        "dollars_per_year_252": {str(r): known_per_day[r] * ANNUAL_SESSIONS for r in rung_keys},
        "full_loss_lower_bound_usd_per_calendar_day": {str(r): bound_per_day[r] for r in rung_keys},
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
                "touch_payoff_below_clearance",
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
        touch_gross = (
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
            if filled.height
            else []
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
                str(r): (float(np.mean(np.asarray(v) > 0)) if len(v) else None)
                for r, v in nets.items()
            },
            "mean_predicted_gross_touch": (
                float(filled["score"].mean()) if filled.height else None
            ),
            "mean_realized_touch_gross": (float(np.mean(touch_gross)) if touch_gross else None),
            "mean_overprediction_pp": (
                float((filled["score"].mean() - np.mean(touch_gross)) * 100.0)
                if touch_gross
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
            "entry_status_counts": dict(
                trades_view.group_by("entry_status").len().sort("len", descending=True).iter_rows()
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
            "mean_predicted_gross_touch": None,
            "mean_realized_touch_gross": None,
            "mean_overprediction_pp": None,
            "fill_status_counts": {},
            "exit_submit_status_counts": {},
            "entry_status_counts": {},
            "mean_hold_minutes": None,
        }
    return cell


def aggregate_block(out: Path, block: str, days: list[str]) -> tuple[dict, dict]:
    """The full view surface of one block (model views plus the touch-only baseline)."""
    daily, trades = read_block_frames(out, block)
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
        "unknown_share_of_attempts": (
            float(daily["unknown_fills"].sum() / daily["attempts"].sum())
            if int(daily["attempts"].sum())
            else None
        ),
        "peak_reserved_usd_day_max": float(daily["peak_reserved_usd"].max() or 0.0),
        "positions_peak_day_max": int(daily["positions_peak"].max() or 0),
        "causal_skips": {
            reason: int(daily[reason].sum())
            for reason in (
                "no_order_min_capital",
                "quote_not_acquired",
                "intent_not_firm_fresh",
                "intent_depth_unsupported",
                "intent_depth_unknown",
                "touch_payoff_below_clearance",
                "overlap_skips",
                "cooldown_skips",
                "max_attempt_skips",
                "cash_or_slot_skips",
            )
        },
    }
    return surface, coverage


def choose_view(surface: dict) -> tuple[View, list[dict]]:
    """Deterministic argmax of the frozen validation objective; no floors, no gates.

    The touch-only baseline is a comparator, never a selection candidate.
    """
    ranked = sorted(
        MODEL_VIEWS,
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
            "family": v.family,
            "head": v.head,
            "threshold": v.threshold,
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


def _coverage_caveat(score_covs: list[tuple[str, dict]]) -> str:
    """The data-absence caveat: a missing historical stream is coverage, not falsification."""
    parts = []
    for label, cov in score_covs:
        if not cov:
            continue
        rows = int(cov.get("qualified_rows", 0))
        missing = int((cov.get("intent_status_counts") or {}).get("quote_not_acquired", 0))
        if rows and missing:
            parts.append(
                f"{label}: {100.0 * missing / rows:.1f}% of {rows} qualified states had no "
                "acquired quote stream"
            )
    if not parts:
        return ""
    return (
        " Data-absence caveat: "
        + "; ".join(parts)
        + ". A missing historical stream is an acquisition UNKNOWN reported as coverage, "
        "never known cash, never a future-availability entry filter, and never a strategy "
        "falsification (data absence is not strategy falsification)."
    )


def decision_text(
    chosen: View,
    val: dict,
    late: dict | None,
    frozen: bool,
    baseline: dict,
    coverage_note: str = "",
) -> str:
    """The honest measured statement: PARTIAL known contribution, unknowns disclosed."""
    objective = val["known_usd_per_calendar_day"]["25"]
    fills = val["known_fills"]
    boot = val["bootstrap_daily_known_usd"]["25bps"]
    unknown_share = val["unknown_share_of_attempts"]
    base_obj = baseline["known_usd_per_calendar_day"]["25"]
    base_note = (
        f"the touch-only provider baseline measures {base_obj:+.2f} $/calendar day at the "
        f"same primary rung over {baseline['known_fills']} known fills of "
        f"{baseline['attempts']} attempts (reported separately, never folded into a "
        "full-EV loss)"
    )
    if fills == 0 or objective is None:
        return (
            "NO_EDGE_EVIDENCE: the chosen validation view has no known fill, so an empty "
            "signal set is cash, not a positive edge. No actual-touch positive formulation "
            "was measured; nothing here is promoted. " + base_note
        )
    if objective <= 0:
        return (
            f"NO_ACTUAL_TOUCH_POSITIVE_FORMULATION: the best model view ({chosen.label}) "
            f"measures {objective:+.2f} $/calendar day of KNOWN contribution on 2023 "
            f"validation at the 25bps extra rung over {fills} known fills (day bootstrap "
            f"p>0 = {boot.get('p_gt_zero')}); all model views are measured and none is "
            "positive, so nothing is promoted. " + base_note
        )
    text = (
        f"{STATUS}: chosen {chosen.label} (family {chosen.family}, h{chosen.head}, score bar "
        f"{chosen.threshold:.4f}) by 2023 validation actual-touch known contribution: "
        f"{objective:+.2f} $/calendar day at the 25bps extra rung = "
        f"{val['dollars_per_year_252']['25']:+.0f} $/year on the ${int(BOOK)} nominal book "
        f"(simple 252-session convention, not a CAGR), {fills} known fills on "
        f"{val['traded_days']} traded days of {val['days_replayed']} calendared days, "
        f"{val['unknown_fills']} UNKNOWN executions ({100.0 * (unknown_share or 0.0):.1f}% "
        f"of attempts) and {val['no_match_fills']} no-match unfilled intents. Day bootstrap "
        f"p>0 = {boot.get('p_gt_zero')}. This is the measured KNOWN-contribution "
        "expectation on a PARTIAL basis (unknowns excluded from the numerator), not an "
        "actual account CAGR. " + base_note
    )
    if frozen and late is not None:
        late_obj = late["known_usd_per_calendar_day"]["25"]
        text += (
            f" The frozen late block measures {late_obj:+.2f} $/calendar day at the 25bps "
            f"extra rung over {late['known_fills']} known fills of {late['attempts']} "
            "attempts on the previously explored 2025-02..2026-05 window; the choice was "
            "frozen before any late file was read, so this is confirmation of an "
            "already-frozen decision, not a re-selection."
        )
    elif not frozen:
        text += " Late block not run (--skip-late)."
    return text


# ----- provenance and run -----------------------------------------------------
def provenance_block(
    out: Path,
    days_val: list[str],
    days_late: list[str] | None,
    supplemental_roots: tuple[Path, ...],
    val_cov: dict,
    late_cov: dict | None,
    model_report: dict,
) -> dict:
    sup_digest, sup_entries = supplement_digest(supplemental_roots)
    bundle_shas = {}
    for family in MODEL_FAMILIES:
        for head in HEADS:
            key = model_bundle_name(family, head)
            bundle_shas[key] = model_report["families"][f"h{head}"]["bundles"][family]["sha256"]
    return {
        "producer_sha256": producer_sha256(),
        "producer_script": str(Path(__file__).resolve()),
        "quote_engine_sha256": engine_sha256(),
        "quote_engine_script": str(Path(_engine.__file__).resolve()),
        "contract_sha256": _digest_bytes(CONTRACT),
        "panel_root": str(PANEL_ROOT),
        "panel_days_root": str(PANEL_DAYS),
        "panel_contract_sha256": (sha256_file(PANEL_CONTRACT) if PANEL_CONTRACT.exists() else None),
        "labels_root": str(LABELS_ROOT),
        "labels_digest_artifact": str(out / "labels_digest.json"),
        "models": bundle_shas,
        "model_card": str(out / "models" / "model_card.json"),
        "supplemental_roots": [str(r) for r in supplemental_roots],
        "supplemental_roots_existing": [str(r) for r in supplemental_roots if r.exists()],
        "supplemental_files": sup_entries,
        "supplement_digest": sup_digest,
        "quote_cache_root": str(QUOTE_CACHE_ROOT),
        "validation_days": len(days_val),
        "late_days": (
            len(days_late)
            if days_late is not None
            else "not_yet_inspected: the freeze precedes any late file access"
        ),
        "coverage_validation": val_cov,
        "coverage_late": late_cov,
        "output_root": str(out),
    }


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


def run(args: argparse.Namespace) -> None:
    t0 = time.time()
    out = args.out or OUTPUT
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")

    labels_root = args.labels_root or LABELS_ROOT
    panel_root = args.panel_root or PANEL_ROOT
    data_root = args.data_root or DATA_ROOT
    global PANEL_DAYS  # the smoke subset may point at a synthetic panel root
    PANEL_DAYS = panel_root / "days"
    contract_sha = _digest_bytes(CONTRACT)

    (out / "contract.json").write_text(json.dumps(CONTRACT, indent=1, default=_default) + "\n")
    print(
        f"[freeze-grid] contract -> {out / 'contract.json'} "
        f"({len(MODEL_VIEWS)} model views + {len(BASELINE_VIEWS)} baseline views x "
        f"{len(EXTRA_FEE_RUNGS)} rungs, before any outcome)",
        flush=True,
    )

    supplemental = list(SUPPLEMENT_ROOTS)
    for extra in args.supplement or []:
        if extra not in supplemental:
            supplemental.append(extra)
    supplemental_roots = tuple(supplemental)

    train_days = block_days(args.days, TRAIN_PERIOD)
    days_val = block_days(args.days, VAL_BLOCK)
    if args.days is None and len(days_val) != EXPECTED_DAYS[VAL_BLOCK]:
        raise SystemExit(
            f"[cross-rotation] requires {EXPECTED_DAYS[VAL_BLOCK]} {VAL_BLOCK} days, "
            f"got {len(days_val)}"
        )
    if not train_days:
        raise SystemExit("[cross-rotation] no train-window days selected")
    if not days_val:
        raise SystemExit("[cross-rotation] no validation days selected")
    print(
        f"[blocks] train={len(train_days)} days (labels window from {TRAIN_FIT_START}), "
        f"validation={len(days_val)} days (late block deferred until after the freeze)",
        flush=True,
    )

    # 1) features for the fit + validation blocks only; no late file is touched here.
    feat_cov_train = features_block(
        train_days, out, labels_root, panel_root, args.resume, contract_sha
    )
    train_frame = read_feature_days(out, train_days)
    feat_cov_val = features_block(days_val, out, labels_root, panel_root, args.resume, contract_sha)
    # 2) the three fixed families x two heads on the labeled train window.
    bundles, model_report = fit_models(
        train_frame, out, args.resume, args.force_fit, feat_cov_train["labels"]
    )
    del train_frame
    # 3) validation score + replay + aggregation.
    score_cov_val = score_block(
        out,
        bundles,
        days_val,
        args.resume,
        supplemental_roots,
        contract_sha,
        data_root,
    )
    replay_cov_val = replay_block(
        out,
        VAL_BLOCK,
        days_val,
        args.resume,
        supplemental_roots,
        contract_sha,
        data_root,
    )
    val_surface, val_cov = aggregate_block(out, VAL_BLOCK, days_val)
    for view in MODEL_VIEWS:
        cell = val_surface[view.key]
        boot = cell["bootstrap_daily_known_usd"]["25bps"]
        print(
            f"[val@25] {view.label:<26} {cell['known_usd_per_calendar_day']['25']:+8.2f} $/day "
            f"known fills={cell['known_fills']} unknown={cell['unknown_fills']} "
            f"nomatch={cell['no_match_fills']} attempts={cell['attempts']} "
            f"signals={cell['signals']} traded_days={cell['traded_days']} "
            f"p>0={boot.get('p_gt_zero')}",
            flush=True,
        )
    for view in BASELINE_VIEWS:
        cell = val_surface[view.key]
        print(
            f"[val@25] {view.label:<26} {cell['known_usd_per_calendar_day']['25']:+8.2f} $/day "
            f"(touch-only baseline) known fills={cell['known_fills']} "
            f"unknown={cell['unknown_fills']} attempts={cell['attempts']}",
            flush=True,
        )

    chosen, ranking = choose_view(val_surface)
    frozen_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    freeze = {
        "frozen_before_late_file_access": True,
        "frozen_at": frozen_at,
        "study": "alpha_cross_name_rotation",
        "status": STATUS,
        "chosen": {
            "view_key": chosen.key,
            "label": chosen.label,
            "family": chosen.family,
            "head": chosen.head,
            "threshold": chosen.threshold,
        },
        "selection": {
            "objective": (
                "2023 validation actual-touch known contribution dollars per full "
                "calendar day at the primary 25bps extra-fee rung"
            ),
            "selection_cost_bps": PRIMARY_RUNG,
            "cost_ladder_bps": list(EXTRA_FEE_RUNGS),
            "basis": (
                "PARTIAL: unknown executions are excluded from the numerator and reported "
                "beside it; the full-loss lower bound is a disclosed guard, never the "
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
                "family": v.family,
                "head": v.head,
                "threshold": v.threshold,
            }
            for v in VIEWS
        ],
        "validation_surface": val_surface,
        "model": model_report,
        "provenance": provenance_block(
            out, days_val, None, supplemental_roots, val_cov, None, model_report
        ),
    }
    write_json_atomic(out / "selection_freeze.json", freeze)
    print(
        f"[freeze] selection_freeze.json -> chosen {chosen.label} "
        f"({val_surface[chosen.key]['known_usd_per_calendar_day']['25']:+.2f} $/day @25) "
        "AFTER validation, BEFORE any late file is read",
        flush=True,
    )

    head = chosen.head
    base_val = val_surface[View(BASELINE_FAMILY, head, 0.0).key]
    caveat = _coverage_caveat([("validation", score_cov_val)])
    if args.skip_late:
        results = {
            "study": "alpha_cross_name_rotation",
            "status": STATUS,
            "decision": decision_text(
                chosen,
                val_surface[chosen.key],
                None,
                frozen=False,
                baseline=base_val,
                coverage_note=caveat,
            ),
            "chosen": freeze["chosen"],
            "frozen_choice_immutable": True,
            "contract": CONTRACT,
            "model": model_report,
            "validation": val_surface,
            "validation_ranking": ranking,
            "baseline_touch_only": {v.key: val_surface[v.key] for v in BASELINE_VIEWS},
            "late": None,
            "coverage": {
                "validation": val_cov,
                "late": None,
                "features_train": feat_cov_train,
                "features_validation": feat_cov_val,
                "score_validation": score_cov_val,
                "replay_validation": replay_cov_val,
            },
            "artifacts": {
                "contract": str(out / "contract.json"),
                "selection_freeze": str(out / "selection_freeze.json"),
                "producer_snapshot": str(out / "producer_snapshot.py"),
                "features_root": str(out / "features"),
                "states_root": str(out / "states"),
                "replay_root": str(out / "replay"),
            },
            "runtime_s": round(time.time() - t0, 1),
        }
        write_json_atomic(out / "results.json", results)
        print(f"[decision] {results['decision']}", flush=True)
        print(f"[done] {results['runtime_s']}s -> {out / 'results.json'}", flush=True)
        return

    # 4) late block: the same views for transparency, the choice already frozen.
    #    This is the FIRST access to any late panel, label or quote file.
    days_late = block_days(args.days, LATE_BLOCK)
    if args.days is None and len(days_late) != EXPECTED_DAYS[LATE_BLOCK]:
        raise SystemExit(
            f"[cross-rotation] requires {EXPECTED_DAYS[LATE_BLOCK]} {LATE_BLOCK} days, "
            f"got {len(days_late)}"
        )
    print(f"[blocks] late={len(days_late)} days (after the freeze)", flush=True)
    feat_cov_late = features_block(
        days_late, out, labels_root, panel_root, args.resume, contract_sha
    )
    score_cov_late = score_block(
        out,
        bundles,
        days_late,
        args.resume,
        supplemental_roots,
        contract_sha,
        data_root,
    )
    replay_cov_late = replay_block(
        out,
        LATE_BLOCK,
        days_late,
        args.resume,
        supplemental_roots,
        contract_sha,
        data_root,
    )
    late_surface, late_cov = aggregate_block(out, LATE_BLOCK, days_late)
    for view in MODEL_VIEWS:
        cell = late_surface[view.key]
        print(
            f"[late@25] {view.label:<26} {cell['known_usd_per_calendar_day']['25']:+8.2f} $/day "
            f"known fills={cell['known_fills']} unknown={cell['unknown_fills']} "
            f"nomatch={cell['no_match_fills']} attempts={cell['attempts']} "
            f"signals={cell['signals']} traded_days={cell['traded_days']}",
            flush=True,
        )
    for view in BASELINE_VIEWS:
        cell = late_surface[view.key]
        print(
            f"[late@25] {view.label:<26} {cell['known_usd_per_calendar_day']['25']:+8.2f} $/day "
            f"(touch-only baseline) known fills={cell['known_fills']} "
            f"unknown={cell['unknown_fills']} attempts={cell['attempts']}",
            flush=True,
        )

    base_late = late_surface[View(BASELINE_FAMILY, head, 0.0).key]
    caveat = _coverage_caveat([("validation", score_cov_val), ("late", score_cov_late)])
    results = {
        "study": "alpha_cross_name_rotation",
        "status": STATUS,
        "decision": decision_text(
            chosen,
            val_surface[chosen.key],
            late_surface[chosen.key],
            frozen=True,
            baseline=base_val,
            coverage_note=caveat,
        ),
        "chosen": freeze["chosen"],
        "frozen_choice_immutable": True,
        "contract": CONTRACT,
        "model": model_report,
        "validation": val_surface,
        "validation_ranking": ranking,
        "baseline_touch_only": {v.key: val_surface[v.key] for v in BASELINE_VIEWS},
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
                MODEL_VIEWS,
                key=lambda v: (
                    -late_surface[v.key]["known_usd_per_calendar_day"]["25"],
                    v.key,
                ),
            )
        ],
        "baseline_late": {v.key: late_surface[v.key] for v in BASELINE_VIEWS},
        "chosen_late_block": late_surface[chosen.key],
        "chosen_vs_baseline": {
            "head": head,
            "chosen_view_key": chosen.key,
            "chosen_validation_usd_per_calendar_day_25": val_surface[chosen.key][
                "known_usd_per_calendar_day"
            ]["25"],
            "baseline_validation_usd_per_calendar_day_25": base_val["known_usd_per_calendar_day"][
                "25"
            ],
            "chosen_late_usd_per_calendar_day_25": late_surface[chosen.key][
                "known_usd_per_calendar_day"
            ]["25"],
            "baseline_late_usd_per_calendar_day_25": base_late["known_usd_per_calendar_day"]["25"],
            "note": (
                "compared separately on the same PARTIAL known basis; an unknown execution "
                "is never charged as a full EV loss in either arm"
            ),
        },
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
                    "validation_dollars_per_year_252": val_surface[view.key][
                        "dollars_per_year_252"
                    ][str(int(r))],
                    "late_dollars_per_year_252": late_surface[view.key]["dollars_per_year_252"][
                        str(int(r))
                    ],
                }
                for r in EXTRA_FEE_RUNGS
            }
            for view in VIEWS
        },
        "coverage": {
            "validation": val_cov,
            "late": late_cov,
            "features_train": feat_cov_train,
            "features_validation": feat_cov_val,
            "features_late": feat_cov_late,
            "score_validation": score_cov_val,
            "score_late": score_cov_late,
            "replay_validation": replay_cov_val,
            "replay_late": replay_cov_late,
        },
        "provenance": provenance_block(
            out, days_val, days_late, supplemental_roots, val_cov, late_cov, model_report
        ),
        "artifacts": {
            "contract": str(out / "contract.json"),
            "selection_freeze": str(out / "selection_freeze.json"),
            "producer_snapshot": str(out / "producer_snapshot.py"),
            "features_root": str(out / "features"),
            "states_root": str(out / "states"),
            "replay_root": str(out / "replay"),
            "models_root": str(out / "models"),
            "labels_digest": str(out / "labels_digest.json"),
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
        "--labels-root",
        type=Path,
        default=None,
        help=f"canonical label day files root (default: {LABELS_ROOT})",
    )
    p.add_argument(
        "--panel-root",
        type=Path,
        default=None,
        help=f"panel days root parent (default: {PANEL_ROOT})",
    )
    p.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help=f"quote cache data root (default: {DATA_ROOT})",
    )
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
        "--force-fit",
        action="store_true",
        help="refit the models even when a matching frozen model card exists",
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
