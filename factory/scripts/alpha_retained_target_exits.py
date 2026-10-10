#!/usr/bin/env python3
"""Predeclared take-profit target exits on the frozen repeat-cadence top-gainer selector.

One question, asked once and answered on predeclared cells: on the SAME immutable h60
payoff model (2021-2022 fit, 26 causal features, never refit), read at the SAME frozen
admission (threshold 0.030, liquidity-qualified, strictly past-only panel), under the
SAME repeat cadence (flat 15-minute cooldown after the ACTUAL exit, max 3 attempts per
ticker per session, one position per ticker, 3 slots x $1,000 = $3,000 research reserve,
simultaneous intents funded by descending score BEFORE any fill outcome, unfilled cash
retained, UNKNOWN holds cash+slot to session end) -- does a take-profit target beat the
frozen h60 timeout?

Nine predeclared exit cells, fixed in code, differing ONLY in the exit rule:
  target_pct1_full / target_pct2_full / target_pct3_full / target_pct5_full
                              full position sold at the target: the FIRST bar strictly
                              after the entry minute whose HIGH >= entry_open*(1+target)
                              triggers the take-profit, priced AT the target
                              (conservative for a resting sell limit); otherwise the
                              h60 timeout.
  target_pct1_scale50 / target_pct2_scale50 / target_pct3_scale50 / target_pct5_scale50
                              50/50 scale-out: half sold at the target, half at the
                              h60 timeout, with exit-side friction charged on BOTH
                              legs' proceeds (one entry-side charge, two exit legs).
  h60_timeout_control         no target at all: every fill exits at the h60 timeout.

The h60 timeout prices at the OPEN of the LAST bar at/before min(entry+60, session_end);
a bar whose high touches the target is priced AT the target, never at its high, so a gap
through a target is never credited beyond the target. All of this is minute-bar work:
bars cannot resolve intrabar ordering, so a bar touching the target could also have
gapped through it, and whether the target bar preceded a later adverse or favorable move
inside the same minute cannot be known -- that path-order ambiguity is disclosed as an
UNKNOWN-affecting approximation and never presented as exact execution. Every fill here
is a minute-bar proxy, not a quote and not an actual fill.

The frozen stored-label h60 exit (the panel's own label: first actual minute open at/after
min(entry+60, session_end)) is replayed beside the grid, on the identical cadence, as the
reproduction anchor for the parent study's validated repeat_h60 numbers
(2023 validation: 67 known fills on 55 traded days, +$2.84/day at 100 bps; late
2025-02..2026-05: 157 known fills on 124 traded days, +$5.677768/day at 100 bps), and the
grid's bar-rule timeout vs that frozen label is quantified divergence-by-divergence. It is
a reproduction anchor, NOT a candidate cell.

Selection: 2023 validation ONLY, frozen to disk before any late-block outcome is computed.
The metric is the known-contribution dollars per FULL calendar day at the pre-declared
25 bps rung, where the daily contribution is the known net P&L minus a separately reported
full-$1,000-ticket loss charge on every UNKNOWN fill (the conservative lower bound; the
known-only figure is reported beside it and never blended). Ties break deterministically
on known fills, then fewer attempts, then cell key. There is no sample-size floor, no
median or tail gate and no CI veto anywhere in this producer: the cost ladder is a
COMPARISON SET, never a hurdle. Each rung is a TOTAL modeled minute-proxy friction
scenario, NOT an actual broker fee -- a primary US broker's regular schedule (zero
commission, SEC 20.6 per $1M, CAT 0.000003 per side, daily cent rounding) is typically
well under 1 bp on a $1,000 ticket, so actual provider fees are sourced separately (the
provider-fee ledger), never claimed here. The late block (2025-02..2026-05) was already
explored before this study, so it is NOT pristine and NOT previously unknown: it is
traversed for transparency only, after the choice is frozen, and everything late stays
DISCOVERY-NOT-VALIDATED.

Annualization is a simple 252-session multiplication of the observed daily dollars, never
a compounded CAGR; the daily-reset research normalization (every session restarts from the
same $3,000 reserve) is not a self-financing return, and an additive fixed-ticket carry
equity is reported beside it. $1,000 tickets are book size on a research reserve, not a
capacity claim: displayed top-of-book depth may not support them and scaling is not
assumed linear.

Novelty vs the pre-owned exit roster: factory/scripts/alpha_sparse_exit_management.py
evaluated quote-triggered adverse STOPS on the ORIGINAL fixed entries of the exploratory
sparse extension (once-per-session admission, as-of NBBO quotes, 250 ms arrival latency,
0/10/25 bps residual ladder). This study is a different mechanism on a different
substrate: FAVORABLE take-profit targets (not adverse stops), minute bars (not quotes), a
repeat-cadence re-selected entry stream (not fixed original entries), and a cooldown
anchored to the target-accelerated actual exit, so the target changes the trade clock
itself rather than only the exit price of an immutable entry list.

Usage (exact):
  uv run --no-sync python factory/scripts/alpha_retained_target_exits.py
      [--out DIR] [--resume] [--skip-late] [--days ...]
    # bare form -> ~/alpha-data/open-search-v1/retained_target_exits:
    #   contract.json (full fixed grid + cost ladder) -> 2023 validation replay ->
    #   selection.json (frozen choice) -> late-block transparency -> results.json
  uv run --no-sync python factory/scripts/alpha_retained_target_exits.py --resume
      # resume collection from the per-day parts already on disk
  uv run --no-sync python factory/scripts/alpha_retained_target_exits.py --skip-late
      # frozen contract + 2023 validation selection only (no late traversal)
  uv run --no-sync python factory/scripts/alpha_retained_target_exits.py --out <dir>
      # relocate the lane root
  uv run --no-sync python factory/scripts/alpha_retained_target_exits.py --days 2023-01-03
      # restrict the analysis corpus (debug; skips the reproduction check)
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import shutil
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import polars as pl
from alpha_open_learned import (
    CONF_PERIOD,
    FEATURES_ALL,
    PANEL_ROOT,
    VAL_PERIOD,
    bootstrap_daily,
    feature_matrix,
    metrics_view,
    monthly_accounting,
    sha256_file,
)
from alpha_open_panel import ROOT, allowed
from alpha_open_sim import CONTEXT_FEATURES, causal_liquidity, period

# ----- fixed configuration (no HPO, no refit) --------------------------------
MODEL_HORIZON = 60  # the one stored head that is ever loaded
THRESHOLD = 0.03  # unchanged retained admission bar
COOLDOWN_MIN = 15  # flat minutes after the ACTUAL exit
MAX_ATTEMPTS = 3  # attempts per ticker per session
MAX_POSITIONS = 3  # one position per symbol, at most three at once
ORDER_BUDGET = 1000.0  # research ticket size (book only, not capacity)
RESERVE_USD = MAX_POSITIONS * ORDER_BUDGET
# Reported cost scenarios in bps (TOTAL round-trip modeled friction on the minute-open
# proxy prices). The ladder is a COMPARISON SET, never a hurdle: no scenario closes a
# candidate and no rung falsifies anything. 25..150 bps describe a LOW-FEE OPPORTUNITY
# set, not a fee claim - a primary US broker's regular schedule (zero commission, SEC
# 20.6 per $1M, CAT 0.000003 per side, daily cent rounding) is typically well under 1 bp
# on a $250-$1,000 ticket, so the actual broker fee is sourced separately, not here.
COST_SCENARIOS_BPS = (25.0, 50.0, 75.0, 100.0, 125.0, 150.0)
# Historical diagnostic only: the 200 bp rung the stored late ladder already carried
# (alpha_open_learned.CONFIRM_COSTS). Reported for continuity, never a modern fee claim.
HISTORICAL_COST_BPS = 200.0
VAL_COSTS = COST_SCENARIOS_BPS + (HISTORICAL_COST_BPS,)  # validation cost ladder
LATE_COSTS = COST_SCENARIOS_BPS + (HISTORICAL_COST_BPS,)  # late ladder = scenarios + diagnostic
SELECT_COST_BPS = 25.0  # the ONE pre-declared validation selection rung
TRADING_DAYS_PER_YEAR = 252  # session-count convention for $/year (never a CAGR)
BARS_ROOT = ROOT / "data" / "sip" / "net" / "bars"
OUTPUT = PANEL_ROOT / "retained_target_exits"
EXPECTED_DAYS = {VAL_PERIOD: 250, CONF_PERIOD: 332}
ANALYSIS_PERIODS = (VAL_PERIOD, CONF_PERIOD)
FLOAT_TOL = 1e-12
STATUS = "DISCOVERY-NOT-VALIDATED"
PROGRESS_EVERY = 25  # days between collection progress prints
PART_SCHEMA_VERSION = "retained_target_exits/parts/v1"

# ----- the predeclared exit grid ---------------------------------------------
# target_bps is the take-profit distance in bps above the entry open; target_share is the
# share of the position sold AT the target (1.0 = full position, 0.5 = 50/50 scale-out).
TARGETS_BPS = (100.0, 200.0, 300.0, 500.0)  # +1%, +2%, +3%, +5%
SCALE_SHARE = 0.5


@dataclass(frozen=True)
class Cell:
    """One predeclared exit cell. Same admission, same clock; only the exit differs."""

    name: str
    target_bps: float | None  # None => the plain h60 timeout control
    target_share: float = 1.0
    control: bool = False

    @property
    def hit_col(self) -> str | None:
        if self.target_bps is None:
            return None
        return f"hit_et_pct{int(round(self.target_bps / 100.0))}"

    @property
    def scale_out(self) -> bool:
        return self.target_bps is not None and self.target_share < 1.0

    @property
    def legs_per_known_fill(self) -> int:
        # legs that must be priced for a fill to count as fully priced: one for a
        # single-exit cell, two for a 50/50 scale-out.
        return 2 if self.scale_out else 1

    @property
    def exit_rule(self) -> str:
        if self.control:
            return (
                "no target: every fill exits at the h60 timeout (open of the LAST bar "
                "at/before min(entry+60, session_end))"
            )
        share = "full position" if self.target_share >= 1.0 else "50/50 scale-out"
        return (
            f"{share} sold at +{self.target_bps / 100.0:g}%: first bar strictly after the "
            "entry minute with high >= entry_open*(1+target), priced AT the target; "
            "otherwise the h60 timeout"
        )


CELLS = (
    Cell("target_pct1_full", 100.0, 1.0),
    Cell("target_pct2_full", 200.0, 1.0),
    Cell("target_pct3_full", 300.0, 1.0),
    Cell("target_pct5_full", 500.0, 1.0),
    Cell("target_pct1_scale50", 100.0, SCALE_SHARE),
    Cell("target_pct2_scale50", 200.0, SCALE_SHARE),
    Cell("target_pct3_scale50", 300.0, SCALE_SHARE),
    Cell("target_pct5_scale50", 500.0, SCALE_SHARE),
    Cell("h60_timeout_control", None, 1.0, control=True),
)
CELLS_BY_NAME = {c.name: c for c in CELLS}
CONTROL_CELL = CELLS_BY_NAME["h60_timeout_control"]
# Reproduction anchor only (never a candidate): the panel's frozen stored h60 label.
FROZEN_LABEL_CELL_NAME = "frozen_h60_stored_label"

# The parent study's validated repeat_h60 reference numbers (alpha_sparse_daily, same
# immutable model, same threshold, same cadence, panel stored h60 labels) at the 100 bps
# historical rung. Recorded verbatim from that run; the panel and the model are immutable,
# so these are stable reproduction targets, not a moving target.
FROZEN_REFERENCE = {
    VAL_PERIOD: {
        "days": 250,
        "attempts": 68,
        "fills": 67,
        "known_fills": 67,
        "unknown_fills": 0,
        "traded_days": 55,
        "cash_or_slot_skips": 0,
        "skips_total": 39,
        "n_signals": 107,
        "mean_net_known_fill": 0.010592227020629865,
        "mean_daily_lower_bound": 0.0009462389471762678,
        "dollars_per_day": 2.8387168415288033,
        "known_win_rate": 0.43283582089552236,
        "known_profit_factor": 1.204757685467114,
        "worst_known_fill": -0.3185892400346446,
        "positive_months_lower_bound": 4,
        "months": 12,
    },
    CONF_PERIOD: {
        "days": 332,
        "attempts": 157,
        "fills": 157,
        "known_fills": 157,
        "unknown_fills": 0,
        "traded_days": 124,
        "cash_or_slot_skips": 0,
        "skips_total": 87,
        "n_signals": 244,
        "mean_net_known_fill": 0.012006490657975754,
        "mean_daily_lower_bound": 0.0018925893908656555,
        "dollars_per_day": 5.677768172596966,
        "known_win_rate": 0.40764331210191085,
        "known_profit_factor": 1.160474353266842,
        "worst_known_fill": -0.5872993802050204,
        "positive_months_lower_bound": 11,
        "months": 16,
    },
}

HONEST_LIMITS = {
    "fills_are_minute_bar_proxies": (
        "every fill in this study is priced from minute-bar OHLC aggregates on the day's "
        "tape (data/sip/net/bars); they are NOT quotes, NOT NBBO touches and NOT actual "
        "exchange fills"
    ),
    "intrabar_ordering_unresolved": (
        "bars cannot resolve intrabar ordering: a bar whose high reaches the target could "
        "also have gapped through it within the same minute, and whether the target bar "
        "preceded a later adverse or favorable print is unknowable from aggregates"
    ),
    "priced_at_target_not_high": (
        "a touched target is priced AT the target, never at the bar high, which is the "
        "conservative treatment for a resting sell limit"
    ),
    "path_order_ambiguity_is_unknown_affecting": (
        "path-order ambiguity is disclosed as an UNKNOWN-affecting approximation and is "
        "never presented as exact execution"
    ),
    "timeout_rule": (
        "the h60 timeout prices at the OPEN of the LAST bar at/before "
        "min(entry_et+60, session_end); when the tape has no bar in "
        "(entry_et, min(entry_et+60, session_end)] the fill is UNKNOWN, never cash and "
        "never a quiet zero"
    ),
    "scale_out_legs": (
        "a 50/50 scale-out accounts as one blended ticket: half at the target, half at the "
        "h60 timeout, exit-side friction charged on both legs' proceeds and one entry-side "
        "charge; the ticket's cash and the ticker's slot are released at the FULL close "
        "(the timeout minute), so the cooldown anchors there"
    ),
    "cost_scenarios_are_not_fees": (
        "25/50/75/100/125/150 bps (plus the 200 bp historical diagnostic) are TOTAL modeled "
        "minute-proxy friction scenarios used as a comparison set; no rung closes a "
        "candidate. Actual provider fees are sourced in the separate provider-fee ledger: "
        "a primary US broker's regular schedule (zero commission, SEC 20.6 per $1M, CAT "
        "0.000003 per side, daily cent rounding) is typically well under 1 bp on a $1,000 "
        "ticket"
    ),
    "unknown_rule": (
        "an UNKNOWN fill holds its cash and its slot to session end and is charged a full "
        "$1,000 ticket loss in the separately labeled full-loss lower bound; it is never "
        "blended into an EV and never called one"
    ),
    "annualization": (
        "dollars/year are a simple 252-session multiplication of the observed daily "
        "dollars, never a compounded CAGR; the daily-reset research normalization is not a "
        "self-financing return"
    ),
    "capacity": (
        "$1,000 tickets are book size on a $3,000 research reserve; displayed top-of-book "
        "depth may not support them and scaling is not assumed linear"
    ),
    "late_block_not_pristine": (
        "2025-02..2026-05 was already explored before this study, so late outcomes are "
        "DISCOVERY-NOT-VALIDATED transparency, never a fresh holdout"
    ),
    "selection_has_no_vetoes": (
        "no sample-size floor, no median or tail gate and no confidence-interval veto is "
        "applied to any cell at any rung"
    ),
}

# ----- per-day parts: schema, bar facts, collection --------------------------
PART_SCHEMA = {
    "day": pl.String,
    "ticker": pl.String,
    "t": pl.Int64,
    "entry_et": pl.Int64,
    "entry_open": pl.Float64,
    "entry_status": pl.String,
    "session_end": pl.Int64,
    "score": pl.Float64,
    # the bar-rule h60 timeout (last bar open at/before min(entry+60, session_end))
    "tp_et": pl.Int64,
    "tp_open": pl.Float64,
    "tp_gross": pl.Float64,
    "tp_status": pl.String,
    # first bar strictly after the entry minute with high >= entry_open*(1+target)
    "hit_et_pct1": pl.Int64,
    "hit_et_pct2": pl.Int64,
    "hit_et_pct3": pl.Int64,
    "hit_et_pct5": pl.Int64,
    # bar-window excursion context over (entry, min(entry+60, session_end)]
    "mfe": pl.Float64,
    "mae": pl.Float64,
    "window_bars": pl.Int64,
    # the panel's frozen stored h60 labels (reproduction anchor, never re-derived)
    "stored_gross_60": pl.Float64,
    "stored_exit_et_60": pl.Int64,
    "stored_exit_status_60": pl.String,
}
PART_BASE = [
    "day",
    "ticker",
    "t",
    "entry_et",
    "entry_open",
    "entry_status",
    "session_end",
    "score",
    # the PANEL's own column names for its stored h60 labels; they are mapped onto the
    # part's non-conflicting stored_* names when the part columns are built below.
    "gross_60",
    "exit_et_60",
    "exit_status_60",
]


def empty_part_frame() -> pl.DataFrame:
    return pl.DataFrame(schema=dict(PART_SCHEMA))


def day_context(frame: pl.DataFrame) -> pl.DataFrame:
    """Re-derive the four past-only peer-context columns the stored model needs.

    ``alpha_open_sim.load_panel`` computes them at load time over ``over(["day","t"])``
    windows, so the per-day panel files do not carry them. Recomputing them per day on the
    SAME partition key reproduces the exact values (the window is entirely inside one
    session, and peer_positive3 includes the subject itself, exactly as
    ``CONTEXT_FEATURES`` was defined).
    """
    if all(c in frame.columns for c in CONTEXT_FEATURES):
        return frame
    out = frame.with_columns(
        [
            (pl.col("ret3") > 0).sum().over(["day", "t"]).alias("peer_positive3"),
            pl.col("gain_open").mean().over(["day", "t"]).alias("peer_gain_mean"),
            (pl.col("gain_open") >= 0.10).sum().over(["day", "t"]).alias("peer_breadth10"),
            pl.col("range5").median().over(["day", "t"]).alias("peer_heat"),
        ]
    )
    missing = [c for c in CONTEXT_FEATURES if c not in out.columns]
    if missing:
        raise ValueError(f"peer context not derivable, missing {missing}")
    return out


def load_stored_model(model_dir: Path) -> tuple[dict, Path]:
    path = model_dir / f"payoff_h{MODEL_HORIZON}.joblib"
    if not path.exists():
        raise SystemExit(f"[target-exits] stored model missing: {path}")
    bundle = joblib.load(path)
    order = list(bundle.get("feature_order") or [])
    if order != list(FEATURES_ALL):
        raise SystemExit("[target-exits] stored feature_order != current FEATURES_ALL")
    if int(bundle.get("horizon", -1)) != MODEL_HORIZON:
        raise SystemExit(
            f"[target-exits] stored horizon {bundle.get('horizon')} != {MODEL_HORIZON}"
        )
    return bundle, path


def bar_exit_facts(
    et: np.ndarray,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    entry_et: int,
    entry_open: float,
    session_end: int,
) -> dict:
    """Bar-rule exit facts for one entry: the h60 timeout, the target hits and MFE/MAE.

    The window is the bars strictly after the entry minute up to
    ``min(entry_et + MODEL_HORIZON, session_end)``. The timeout is the OPEN of the LAST bar
    in that window; the target hit is the FIRST bar in it whose HIGH reaches
    ``entry_open * (1 + target)``. An empty window (no bar strictly after the entry minute
    inside the horizon) leaves the fill UNKNOWN -- never cash, never an interpolation.
    """
    hi = min(int(entry_et) + MODEL_HORIZON, int(session_end))
    lo = int(entry_et) + 1
    i = int(np.searchsorted(et, lo))
    j = int(np.searchsorted(et, hi, side="right"))
    facts: dict = {
        "tp_et": None,
        "tp_open": None,
        "tp_gross": None,
        "tp_status": "unknown_pending",
        "mfe": None,
        "mae": None,
        "window_bars": int(max(0, j - i)),
        "hit_ets": {},
    }
    if i >= j:
        for target in TARGETS_BPS:
            facts["hit_ets"][target] = None
        return facts
    entry = float(entry_open)
    facts["tp_et"] = int(et[j - 1])
    facts["tp_open"] = float(opens[j - 1])
    facts["tp_gross"] = facts["tp_open"] / entry - 1.0
    facts["tp_status"] = "observed_open_proxy"
    facts["mfe"] = float(highs[i:j].max()) / entry - 1.0
    facts["mae"] = float(lows[i:j].min()) / entry - 1.0
    window_high = highs[i:j]
    for target in TARGETS_BPS:
        px = entry * (1.0 + target / 10_000.0)
        touched = np.flatnonzero(window_high >= px)
        first = int(touched[0]) if touched.size else -1
        facts["hit_ets"][target] = int(et[i + first]) if first >= 0 else None
    return facts


def load_day_bars(day: str, tickers: list[str]) -> tuple[dict, bool]:
    """One day's tape OHLC per ticker; never more than one day resident."""
    path = BARS_ROOT / f"{day}.parquet"
    if not path.exists():
        return {}, False
    frame = pl.read_parquet(path).filter(pl.col("ticker").is_in(tickers))
    if frame.height and frame.select(pl.struct("ticker", "et").is_duplicated().any()).item():
        raise ValueError(f"duplicate tape bars: {day}")
    out: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
    if frame.height:
        for key, part in frame.sort("et").partition_by("ticker", as_dict=True).items():
            out[key[0]] = (
                part["et"].to_numpy().astype(np.int64),
                part["open"].to_numpy().astype(np.float64),
                part["high"].to_numpy().astype(np.float64),
                part["low"].to_numpy().astype(np.float64),
            )
    return out, True


def collect_day(day: str, model) -> tuple[pl.DataFrame, dict]:
    """Score one day's liquidity-qualified panel states and attach the bar exit facts."""
    if not allowed(day):
        raise ValueError(f"protected/out-of-scope day refused: {day}")
    info = {
        "rows_scored": 0,
        "rows_kept": 0,
        "bar_file_present": True,
        "missing_symbol_streams": [],
        "tp_status": Counter(),
        "stored_status": Counter(),
        "target_hits": {str(int(t)): 0 for t in TARGETS_BPS},
        "timeout_only": 0,
    }
    frame = pl.read_parquet(PANEL_ROOT / "days" / f"{day}.parquet")
    frame = day_context(frame)
    cand = frame.filter(causal_liquidity(frame))
    info["rows_scored"] = int(cand.height)
    del frame
    if not cand.height:
        return empty_part_frame(), info
    pred = np.asarray(model.predict(feature_matrix(cand)), dtype=float)
    scored = cand.with_columns(pl.Series("score", pred))
    sig = scored.filter(pl.col("score") >= THRESHOLD)
    del cand, scored
    if not sig.height:
        return empty_part_frame(), info
    info["rows_kept"] = int(sig.height)
    rows = sig.select(PART_BASE).to_dicts()
    tickers = sorted({r["ticker"] for r in rows})
    tapes, present = load_day_bars(day, tickers)
    info["bar_file_present"] = bool(present)
    info["missing_symbol_streams"] = sorted(t for t in tickers if t not in tapes)
    cols: dict[str, list] = {k: [] for k in PART_SCHEMA}
    for r in rows:
        cols["day"].append(r["day"])
        cols["ticker"].append(r["ticker"])
        cols["t"].append(r["t"])
        cols["entry_et"].append(r["entry_et"])
        cols["entry_open"].append(r["entry_open"])
        cols["entry_status"].append(r["entry_status"])
        cols["session_end"].append(r["session_end"])
        cols["score"].append(r["score"])
        cols["stored_gross_60"].append(r["gross_60"])
        cols["stored_exit_et_60"].append(r["exit_et_60"])
        cols["stored_exit_status_60"].append(r["exit_status_60"])
        info["stored_status"][str(r["exit_status_60"])] += 1
        tape = tapes.get(r["ticker"])
        filled = r["entry_status"] == "filled_proxy"
        if not filled or r["entry_open"] is None:
            cols["tp_et"].append(None)
            cols["tp_open"].append(None)
            cols["tp_gross"].append(None)
            cols["tp_status"].append("unfilled_cash")
            for target in TARGETS_BPS:
                cols[f"hit_et_pct{int(round(target / 100.0))}"].append(None)
            cols["mfe"].append(None)
            cols["mae"].append(None)
            cols["window_bars"].append(0)
            info["tp_status"]["unfilled_cash"] += 1
            continue
        if tape is None:
            # a placed order with no observable tape stream: UNKNOWN, never cash
            cols["tp_et"].append(None)
            cols["tp_open"].append(None)
            cols["tp_gross"].append(None)
            cols["tp_status"].append("unknown_pending")
            for target in TARGETS_BPS:
                cols[f"hit_et_pct{int(round(target / 100.0))}"].append(None)
            cols["mfe"].append(None)
            cols["mae"].append(None)
            cols["window_bars"].append(0)
            info["tp_status"]["unknown_pending"] += 1
            continue
        facts = bar_exit_facts(
            tape[0],
            tape[1],
            tape[2],
            tape[3],
            r["entry_et"],
            r["entry_open"],
            r["session_end"],
        )
        cols["tp_et"].append(facts["tp_et"])
        cols["tp_open"].append(facts["tp_open"])
        cols["tp_gross"].append(facts["tp_gross"])
        cols["tp_status"].append(facts["tp_status"])
        cols["mfe"].append(facts["mfe"])
        cols["mae"].append(facts["mae"])
        cols["window_bars"].append(facts["window_bars"])
        info["tp_status"][facts["tp_status"]] += 1
        hit_any = False
        for target in TARGETS_BPS:
            hit_et = facts["hit_ets"][target]
            cols[f"hit_et_pct{int(round(target / 100.0))}"].append(hit_et)
            if hit_et is not None:
                hit_any = True
                info["target_hits"][str(int(target))] += 1
        if not hit_any and facts["tp_status"] == "observed_open_proxy":
            info["timeout_only"] += 1
    part = pl.DataFrame(
        {k: pl.Series(k, v, dtype=PART_SCHEMA[k]) for k, v in cols.items()},
    ).select(list(PART_SCHEMA.keys()))
    # plain dicts so the per-day sidecar round-trips through JSON on resume
    info["tp_status"] = dict(info["tp_status"])
    info["stored_status"] = dict(info["stored_status"])
    return part, info


def resume_hash(day: str, producer_sha: str, contract_sha: str, model_sha: str) -> str:
    """A part is reusable only if the producer, contract, model, day and schema all match."""
    payload = "|".join([producer_sha, contract_sha, model_sha, day, PART_SCHEMA_VERSION])
    return hashlib.sha256(payload.encode()).hexdigest()


def collect_parts(
    days: list[str],
    out_dir: Path,
    *,
    resume: bool,
    hasher,
    model,
    tag: str,
) -> dict:
    """Per-day atomic parts with a hash-guarded resume; one day resident at a time."""
    parts_dir = out_dir / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    coverage = {
        "tag": tag,
        "days_total": len(days),
        "days_collected": 0,
        "days_cached": 0,
        "days_stale": 0,
        "rows_scored": 0,
        "rows_kept": 0,
        "missing_bar_files": [],
        "missing_symbol_streams": {},
        "tp_status": {},
        "stored_status": {},
        "target_hits": {str(int(t)): 0 for t in TARGETS_BPS},
        "timeout_only": 0,
        "resume_hash_prefix": None,
    }
    tp: Counter = Counter()
    stored: Counter = Counter()
    hits = {str(int(t)): 0 for t in TARGETS_BPS}
    timeout_only = 0
    for n, day in enumerate(days, 1):
        if not allowed(day):
            raise ValueError(f"protected/out-of-scope day refused: {day}")
        want = hasher(day)
        dest = parts_dir / f"{day}.parquet"
        sidecar = parts_dir / f"{day}.json"
        if resume and dest.exists() and sidecar.exists():
            try:
                meta = json.loads(sidecar.read_text())
            except Exception:
                meta = {}
            if meta.get("resume_hash") == want and meta.get("schema") == PART_SCHEMA_VERSION:
                coverage["days_cached"] += 1
                c = meta.get("coverage") or {}
                coverage["rows_scored"] += int(c.get("rows_scored", 0))
                coverage["rows_kept"] += int(c.get("rows_kept", 0))
                if not c.get("bar_file_present", True):
                    coverage["missing_bar_files"].append(day)
                if c.get("missing_symbol_streams"):
                    coverage["missing_symbol_streams"][day] = c["missing_symbol_streams"]
                tp.update(c.get("tp_status") or {})
                stored.update(c.get("stored_status") or {})
                for k in hits:
                    hits[k] += int((c.get("target_hits") or {}).get(k, 0))
                timeout_only += int(c.get("timeout_only", 0))
                continue
        part, info = collect_day(day, model)
        if not info["bar_file_present"]:
            coverage["missing_bar_files"].append(day)
        if info["missing_symbol_streams"]:
            coverage["missing_symbol_streams"][day] = info["missing_symbol_streams"]
        coverage["rows_scored"] += info["rows_scored"]
        coverage["rows_kept"] += info["rows_kept"]
        tp.update(info["tp_status"])
        stored.update(info["stored_status"])
        for k in hits:
            hits[k] += info["target_hits"][k]
        timeout_only += info["timeout_only"]
        tmp = parts_dir / f"{day}.parquet.tmp"
        part.write_parquet(tmp)
        tmp.replace(dest)
        meta = {
            "day": day,
            "resume_hash": want,
            "schema": PART_SCHEMA_VERSION,
            "rows": int(part.height),
            "coverage": info,
        }
        stmp = parts_dir / f"{day}.json.tmp"
        stmp.write_text(json.dumps(meta, indent=2, default=str) + "\n")
        stmp.replace(sidecar)
        del part
        coverage["days_collected"] += 1
        if n % PROGRESS_EVERY == 0 or n == len(days):
            print(
                f"[collect:{tag}] {n}/{len(days)} days, {coverage['rows_kept']} "
                f"thr-clearing states (last {day})",
                flush=True,
            )
    coverage["tp_status"] = dict(tp)
    coverage["stored_status"] = dict(stored)
    coverage["target_hits"] = hits
    coverage["timeout_only"] = timeout_only
    coverage["resume_hash_prefix"] = hasher(days[0])[:12] if days else None
    missing = [d for d in days if not (parts_dir / f"{d}.parquet").exists()]
    if missing:
        raise SystemExit(
            f"[target-exits:{tag}] parts missing for {len(missing)} days: {missing[:5]}"
        )
    return coverage


def load_block(days: list[str], parts_dir: Path, hasher) -> pl.DataFrame:
    """Concat ONLY this block's current parts; bounded memory, one block resident."""
    frames = []
    stale = 0
    for day in days:
        dest = parts_dir / f"{day}.parquet"
        sidecar = parts_dir / f"{day}.json"
        want = hasher(day)
        try:
            meta = json.loads(sidecar.read_text()) if sidecar.exists() else {}
        except Exception:
            meta = {}
        if meta.get("resume_hash") != want:
            stale += 1
            continue
        frames.append(pl.read_parquet(dest))
    if stale:
        raise SystemExit(
            f"[target-exits] {stale} part files do not match the current resume hash; "
            "re-run collection (drop --resume to rebuild)"
        )
    if not frames:
        return empty_part_frame()
    out = pl.concat(frames, how="vertical")
    for f in frames:
        del f
    return out.sort(["day", "t", "score", "ticker"], descending=[False, False, True, False])


# ----- funded-reserve replay, one exit cell at a time ------------------------
def cell_exit(row: dict, cell: Cell, *, stored: bool = False):
    """Exit of one intent under one cell -> (gross, exit_et, status, exit_kind, legs).

    ``stored=True`` replays the panel's frozen h60 label instead of the bar rule; that
    path exists only for the reproduction anchor. An empty bar window (or an absent tape)
    is UNKNOWN -- never cash, never a quiet zero.
    """
    if stored:
        status = row["stored_exit_status_60"]
        if status == "observed_open_proxy":
            return (
                float(row["stored_gross_60"]),
                int(row["stored_exit_et_60"]),
                "observed_open_proxy",
                "timeout",
                1,
            )
        return None, None, "unknown_pending", "unknown", 0
    if cell.control:
        if row["tp_status"] != "observed_open_proxy":
            return None, None, "unknown_pending", "unknown", 0
        return float(row["tp_gross"]), int(row["tp_et"]), "observed_open_proxy", "timeout", 1
    target = cell.target_bps / 10_000.0 if cell.target_bps is not None else None
    hit_et = row[cell.hit_col] if cell.hit_col is not None else None
    if hit_et is not None:
        if cell.target_share >= 1.0:
            return target, int(hit_et), "observed_open_proxy", "target", 1
        gross = cell.target_share * target + (1.0 - cell.target_share) * float(row["tp_gross"])
        return gross, int(row["tp_et"]), "observed_open_proxy", "target", 2
    if row["tp_status"] != "observed_open_proxy":
        return None, None, "unknown_pending", "unknown", 0
    return (
        float(row["tp_gross"]),
        int(row["tp_et"]),
        "observed_open_proxy",
        "timeout",
        2 if cell.scale_out else 1,
    )


def replay_cell(
    signals: pl.DataFrame,
    days: list[str],
    cell: Cell,
    cost_bps: float,
    *,
    stored: bool = False,
    max_positions: int = MAX_POSITIONS,
    order_budget: float = ORDER_BUDGET,
) -> tuple[dict, list[dict]]:
    """Replay one exit cell on a block under the shared funded-reserve conventions.

    Long-only, one position per symbol, fees on BOTH legs, same-clock exits precede buys,
    no leverage: a symbol may not be re-entered until its ACTUAL exit minute plus the flat
    15-minute cooldown. An UNKNOWN exit keeps its slot and blocks its ticker for the rest of
    the session and is charged a full unit at the day's end; an unfilled minute reserves and
    releases its ticket and still counts as an attempt. Every calendar day is replayed, so
    no-signal days stay in the denominator as cash days.

    Daily lower-bound returns are the RESEARCH NORMALIZATION on a fixed reserve (each
    session restarts from RESERVE_USD) -- not a self-financing return. ``carry_equity`` is
    the additive fixed-ticket equity check (initial reserve + summed dollars, no
    compounding, no reinvestment of proceeds).
    """
    if not signals.height:
        signals = empty_part_frame()
    ordered = signals.sort(["day", "t", "score", "ticker"], descending=[False, False, True, False])
    by_day = ordered.partition_by("day", as_dict=True)
    side = cost_bps / 20_000.0
    trades: list[dict] = []
    daily: list[dict] = []
    skips: Counter = Counter()
    reserve = max_positions * order_budget
    equity = reserve
    entry_notional = 0.0
    exit_notional = 0.0
    holds: list[int] = []
    target_hits = 0
    timeout_fills = 0
    unfilled_attempts = 0
    legs_full = 0
    legs_partial = 0
    mfes: list[float] = []
    maes: list[float] = []
    stored_grosses: list[float] = []
    for day in days:
        rows = by_day.get((day,))
        cash, active, blocked, used = reserve, set(), {}, {}
        queue: list[tuple] = []
        seq = 0
        known_pnl = 0.0
        unknown = 0
        fills = 0
        attempts = 0
        day_target_hits = 0
        day_timeout = 0
        day_unfilled = 0
        min_cash = reserve
        pos_samples: list[int] = []
        if rows is not None:
            for r in rows.iter_rows(named=True):
                t = int(r["t"])
                while queue and queue[0][0] <= t:
                    _, _, sym, proceeds, _ = heapq.heappop(queue)
                    active.discard(sym)
                    cash += proceeds
                pos_samples.append(len(active))
                ticker = r["ticker"]
                if ticker in active:
                    skips["slot_busy"] += 1
                    continue
                if t < int(blocked.get(ticker, -1)):
                    skips["cooldown"] += 1
                    continue
                if int(used.get(ticker, 0)) >= MAX_ATTEMPTS:
                    skips["max_attempts"] += 1
                    continue
                if len(active) >= max_positions or cash + 1e-8 < order_budget:
                    skips["cash_or_slot"] += 1
                    continue
                used[ticker] = int(used.get(ticker, 0)) + 1
                attempts += 1
                cash -= order_budget
                active.add(ticker)
                min_cash = min(min_cash, cash)
                pos_samples.append(len(active))
                if r["entry_status"] != "filled_proxy":
                    # No position was opened: the attempt minute reserves the ticket and
                    # releases it one minute later, fee-free. No exit => no cooldown.
                    heapq.heappush(queue, (t + 1, seq, ticker, order_budget, False))
                    seq += 1
                    day_unfilled += 1
                    continue
                gross, exit_et, status, exit_kind, legs = cell_exit(r, cell, stored=stored)
                if gross is None:
                    unknown += 1
                    net, proceeds = None, 0.0
                    release = int(r["session_end"]) + 1
                else:
                    net = (1.0 + gross) * (1.0 - side) / (1.0 + side) - 1.0
                    status, proceeds = "known_open_proxy", order_budget * (1.0 + net)
                    known_pnl += order_budget * net
                    release = int(exit_et)
                    if exit_kind == "target":
                        target_hits += 1
                        day_target_hits += 1
                    else:
                        timeout_fills += 1
                        day_timeout += 1
                    if legs >= cell.legs_per_known_fill:
                        legs_full += 1
                    else:
                        legs_partial += 1
                    if r["mfe"] is not None:
                        mfes.append(float(r["mfe"]))
                    if r["mae"] is not None:
                        maes.append(float(r["mae"]))
                    if (
                        r["stored_gross_60"] is not None
                        and r["stored_exit_status_60"] == "observed_open_proxy"
                    ):
                        stored_grosses.append(float(r["stored_gross_60"]))
                # Pending (unknown) exits keep the slot AND block the ticker: no reopening
                # until the actual exit prints, and never within the session otherwise.
                blocked[ticker] = release + COOLDOWN_MIN
                heapq.heappush(queue, (release, seq, ticker, proceeds, True))
                seq += 1
                fills += 1
                entry_notional += order_budget
                exit_notional += proceeds
                if exit_et is not None and r["entry_et"] is not None:
                    holds.append(int(exit_et) - int(r["entry_et"]))
                trades.append(
                    {
                        "day": day,
                        "ticker": ticker,
                        "t": t,
                        "entry_et": r["entry_et"],
                        "entry_open": r["entry_open"],
                        "exit_et": exit_et,
                        "exit_kind": exit_kind,
                        "gross": gross,
                        "net": net,
                        "cost_bps": cost_bps,
                        "order_budget": order_budget,
                        "score": r["score"],
                        "status": status,
                        "attempt_index": int(used[ticker]),
                        "cell": FROZEN_LABEL_CELL_NAME if stored else cell.name,
                        "target_bps": cell.target_bps,
                        "target_share": cell.target_share,
                        "legs_priced": legs,
                        "hold_min": (int(exit_et) - int(r["entry_et"]))
                        if exit_et is not None and r["entry_et"] is not None
                        else None,
                        "mfe": r["mfe"],
                        "mae": r["mae"],
                        "positions_at_entry": len(active),
                    }
                )
        unfilled_attempts += day_unfilled
        lower_pnl = known_pnl - order_budget * unknown
        equity += lower_pnl
        daily.append(
            {
                "day": day,
                "known_pnl": known_pnl,
                "unknown": unknown,
                "fills": fills,
                "attempts": attempts,
                "unfilled_attempts": day_unfilled,
                "target_hits": day_target_hits,
                "timeout_fills": day_timeout,
                "lower_bound_pnl": lower_pnl,
                "lower_bound_return": lower_pnl / reserve,
                "carry_equity": equity,
                "positions_peak": max(pos_samples) if pos_samples else 0,
                "positions_mean": float(np.mean(pos_samples)) if pos_samples else 0.0,
                "peak_deployed_usd": reserve - min_cash,
            }
        )
    known = np.array([tr["net"] for tr in trades if tr["net"] is not None], dtype=float)
    returns = np.array([d["lower_bound_return"] for d in daily], dtype=float)
    known_only = np.array([d["known_pnl"] for d in daily], dtype=float)
    monthly: dict[str, list[float]] = {}
    for d in daily:
        monthly.setdefault(d["day"][:7], []).append(d["lower_bound_return"])
    wins = float(known[known > 0].sum()) if len(known) else 0.0
    losses = float(-known[known < 0].sum()) if len(known) else 0.0
    turnover = entry_notional + exit_notional  # ALL days of the block, never the last one
    dollars_per_day = float(returns.mean()) * reserve if len(returns) else None
    known_fills = int(len(known))
    metrics = {
        "days": len(days),
        "attempts": sum(d["attempts"] for d in daily),
        "fills": len(trades),
        "known_fills": known_fills,
        "unknown_fills": sum(d["unknown"] for d in daily),
        "unfilled_attempts": unfilled_attempts,
        "target_hit_fills": target_hits,
        "timeout_fills": timeout_fills,
        "legs_full_fills": legs_full,
        "legs_partial_fills": legs_partial,
        "cash_or_slot_skips": int(skips["cash_or_slot"]),
        "skips_total": int(sum(skips.values())),
        "skip_reasons": {k: int(v) for k, v in sorted(skips.items())},
        "cost_bps": cost_bps,
        "cell": FROZEN_LABEL_CELL_NAME if stored else cell.name,
        "control": (not stored) and cell.control,
        "stored_label_mode": bool(stored),
        "mean_net_known_fill": float(known.mean()) if len(known) else None,
        "mean_daily_lower_bound": float(returns.mean()) if len(returns) else None,
        "mean_daily_known_only": float(known_only.mean()) if len(known_only) else None,
        "daily_se": (
            float(returns.std(ddof=1) / np.sqrt(len(returns))) if len(returns) > 1 else None
        ),
        "known_win_rate": float((known > 0).mean()) if len(known) else None,
        "known_profit_factor": (wins / losses) if losses else None,
        "worst_known_fill": float(known.min()) if len(known) else None,
        "positive_months_lower_bound": int(sum(np.mean(v) > 0 for v in monthly.values())),
        "months": len(monthly),
        "monthly_mean_lower_bound": {k: float(np.mean(v)) for k, v in monthly.items()},
        "traded_days": len({tr["day"] for tr in trades}),
        "days_with_attempts": sum(1 for d in daily if d["attempts"] > 0),
        "cash_days_no_fill": sum(1 for d in daily if d["fills"] == 0),
        "mean_hold_min": float(np.mean(holds)) if holds else None,
        "median_hold_min": float(np.median(holds)) if holds else None,
        "min_hold_min": int(min(holds)) if holds else None,
        "max_hold_min": int(max(holds)) if holds else None,
        "mfe_mean_known_fills": float(np.mean(mfes)) if mfes else None,
        "mae_mean_known_fills": float(np.mean(maes)) if maes else None,
        "stored_gross_60_mean_known_fills": float(np.mean(stored_grosses))
        if stored_grosses
        else None,
        "entry_notional_usd": entry_notional,
        "exit_notional_usd": exit_notional,
        "turnover_usd": turnover,
        "turnover_usd_per_day": turnover / len(days) if days else None,
        "positions_mean": float(np.mean([d["positions_mean"] for d in daily])) if daily else None,
        "positions_peak": max(d["positions_peak"] for d in daily) if daily else None,
        "peak_deployed_usd": float(np.mean([d["peak_deployed_usd"] for d in daily]))
        if daily
        else None,
        "reserve_usd": reserve,
        "dollars_per_day": dollars_per_day,
        "dollars_per_year_252": dollars_per_day * TRADING_DAYS_PER_YEAR
        if dollars_per_day is not None
        else None,
        "known_only_dollars_per_day": float(known_only.mean()) if len(known_only) else None,
        "known_only_dollars_per_year_252": (
            float(known_only.mean()) * TRADING_DAYS_PER_YEAR if len(known_only) else None
        ),
        "capital_density_annual": (
            (float(returns.mean()) * TRADING_DAYS_PER_YEAR) if len(returns) else None
        ),
        "carry_equity_end": daily[-1]["carry_equity"] if daily else reserve,
        "carry_equity_return": ((daily[-1]["carry_equity"] / reserve - 1.0) if daily else None),
        "carry_equity_is_compounding": False,
        "daily_reset_normalization": (
            "research only: each session restarts from the same "
            "reserve, so this is NOT a self-financing CAGR"
        ),
        "capacity_note": (
            "book-sized tickets on a research reserve; displayed top-of-book "
            "depth and $1,000 tickets are NOT an executable capacity claim "
            "and scaling is not assumed linear"
        ),
        "execution_proxy_only": True,
        "daily": daily,
    }
    return metrics, trades


def yearly_accounting(metrics: dict, trades: list[dict]) -> dict:
    """Per-year replayed-day / traded-day / fill / UNKNOWN accounting for one block."""
    years: dict[str, dict] = {}
    for d in metrics["daily"]:
        e = years.setdefault(
            d["day"][:4],
            {
                "days_replayed": 0,
                "traded_days": set(),
                "fills": 0,
                "unknown": 0,
                "known_fills": 0,
                "lower_bound_pnl": 0.0,
                "lower_bound_return_sum": 0.0,
            },
        )
        e["days_replayed"] += 1
        e["unknown"] += d["unknown"]
        e["fills"] += d["fills"]
        e["lower_bound_pnl"] += d["lower_bound_pnl"]
        e["lower_bound_return_sum"] += d["lower_bound_return"]
    for tr in trades:
        e = years.setdefault(
            tr["day"][:4],
            {
                "days_replayed": 0,
                "traded_days": set(),
                "fills": 0,
                "unknown": 0,
                "known_fills": 0,
                "lower_bound_pnl": 0.0,
                "lower_bound_return_sum": 0.0,
            },
        )
        e["traded_days"].add(tr["day"])
        if tr["net"] is not None:
            e["known_fills"] += 1
    out = {}
    for y in sorted(years):
        e = years[y]
        out[y] = {
            "days_replayed": e["days_replayed"],
            "traded_days": len(e["traded_days"]),
            "fills": e["fills"],
            "known_fills": e["known_fills"],
            "unknown_fills": e["unknown"],
            "lower_bound_pnl": round(e["lower_bound_pnl"], 2),
            "mean_daily_lower_bound": (
                e["lower_bound_return_sum"] / e["days_replayed"] if e["days_replayed"] else None
            ),
        }
    return out


# ----- reporting ---------------------------------------------------------------
COST_NATURE = (
    "each rung is a TOTAL modeled minute-proxy friction scenario on the proxy prices, NOT "
    "an actual broker fee: a primary US broker's regular schedule (zero commission, SEC "
    "20.6 per $1M, CAT 0.000003 per side, daily cent rounding) is typically well under 1 bp "
    "on a $1,000 ticket, so the low rungs describe a LOW-FEE OPPORTUNITY set, not a fee "
    "claim; actual provider fees are sourced in the separate provider-fee ledger"
)


def cell_report(
    metrics: dict, trades: list[dict], cell: Cell, cost_bps: float, n_signals: int, *, stored: bool
) -> dict:
    """Stored metrics for one (cell, cost, block) replay with its economics attached."""
    v = metrics_view(metrics)
    name = FROZEN_LABEL_CELL_NAME if stored else cell.name
    v["cell"] = {
        "name": name,
        "target_bps": cell.target_bps,
        "target_share": cell.target_share,
        "control": cell.control,
        "stored_label_anchor": bool(stored),
        "exit_rule": cell.exit_rule if not stored else "panel's frozen stored h60 label",
        "legs_per_known_fill": cell.legs_per_known_fill,
    }
    v["cost_scenario_nature"] = COST_NATURE
    known = int(v["known_fills"])
    hits = int(v["target_hit_fills"])
    v["target_hit_count"] = hits
    v["target_hit_share_of_known_fills"] = (hits / known) if known else None
    v["timeout_count"] = int(v["timeout_fills"])
    v["timeout_share_of_known_fills"] = (int(v["timeout_fills"]) / known) if known else None
    v["sample"] = {
        "known_fills": known,
        "unknown_fills": int(v["unknown_fills"]),
        "unfilled_attempts_cash": int(v["unfilled_attempts"]),
        "fills": int(v["fills"]),
        "attempts": int(v["attempts"]),
        "days_with_attempts": int(v["days_with_attempts"]),
        "cash_days_no_fill": int(v["cash_days_no_fill"]),
        "traded_days": int(v["traded_days"]),
        "traded_days_of_total": (int(v["traded_days"]) / int(v["days"])) if v["days"] else None,
        "days_replayed": int(v["days"]),
        "n_signals": int(n_signals),
        "known_fills_per_traded_day": (known / int(v["traded_days"])) if v["traded_days"] else None,
        "legs_full_fills": int(v["legs_full_fills"]),
        "legs_partial_fills": int(v["legs_partial_fills"]),
    }
    lb_day = v["dollars_per_day"]
    lb_year = v["dollars_per_year_252"]
    v["economics"] = {
        "selection_metric": (
            "the full-loss lower bound below (known net P&L minus a $1,000 full-ticket "
            "charge per UNKNOWN fill) per FULL calendar day; the known-only figure beside "
            "it excludes that charge and is never blended with it"
        ),
        "known_fills_only": {
            "dollars_per_day": v["known_only_dollars_per_day"],
            "dollars_per_year_252": v["known_only_dollars_per_year_252"],
            "unknown_charge_excluded": True,
            "note": (
                "known-contribution dollars only; UNKNOWN fills contribute 0 here and their "
                "full-loss charge is reported separately below"
            ),
        },
        "full_loss_lower_bound_over_unknowns": {
            "dollars_per_day": lb_day,
            "dollars_per_year_252": lb_year,
            "unknown_fills": int(v["unknown_fills"]),
            "unknown_full_loss_charge_usd_total": int(v["unknown_fills"]) * ORDER_BUDGET,
            "rule": (
                "every UNKNOWN fill is charged a full $1,000 ticket loss at the day's end "
                "and holds its slot and ticker to session end; this bound is separately "
                "labeled, is never blended into an EV and is never called one"
            ),
        },
        "reserve_usd": v["reserve_usd"],
        "max_positions": MAX_POSITIONS,
        "order_budget_usd": ORDER_BUDGET,
        "turnover_usd": v["turnover_usd"],
        "turnover_usd_per_day": v["turnover_usd_per_day"],
        "turnover_per_day_of_capital": (
            v["turnover_usd_per_day"] / RESERVE_USD
            if v["turnover_usd_per_day"] is not None
            else None
        ),
        "time_held_min_mean": v["mean_hold_min"],
        "time_held_min_median": v["median_hold_min"],
        "attempts_per_traded_day": (int(v["attempts"]) / int(v["traded_days"]))
        if v["traded_days"]
        else None,
        "fills_per_attempt": (int(v["fills"]) / int(v["attempts"])) if v["attempts"] else None,
        "mean_positions_open": v["positions_mean"],
        "peak_positions_open": v["positions_peak"],
        "mean_peak_deployed_usd": v["peak_deployed_usd"],
        "carry_equity_end_usd": v["carry_equity_end"],
        "carry_equity_return": v["carry_equity_return"],
    }
    v["bootstrap_daily_context"] = {
        **bootstrap_daily([d["lower_bound_return"] for d in metrics["daily"]]),
        "not_a_selection_or_veto_input": True,
        "note": (
            "day-level bootstrap context only; this producer applies no CI veto, no median "
            "gate and no sample-size floor to any cell at any rung"
        ),
    }
    v["excursion_and_label_context"] = {
        "mfe_mean_known_fills": v["mfe_mean_known_fills"],
        "mae_mean_known_fills": v["mae_mean_known_fills"],
        "mfe_mae_source": (
            "bar high/low over (entry_et, min(entry_et+60, session_end)] on the day's tape; "
            "an excursion context, not an execution measurement"
        ),
        "stored_gross_60_mean_known_fills": v["stored_gross_60_mean_known_fills"],
        "stored_label_source": (
            "mean of the panel's frozen stored h60 label over this cell's known fills, "
            "where that label is observed"
        ),
    }
    v["monthly"] = monthly_accounting(metrics, trades)
    v["yearly"] = yearly_accounting(metrics, trades)
    v["execution_proxy"] = {
        "fills_are_minute_bar_proxies": True,
        "priced_at_target_not_high": True,
        "intrabar_ordering_unresolved": True,
        "path_order_ambiguity_unknown_affecting": True,
        "not_quotes": True,
        "not_actual_fills": True,
        "capacity_note": v["capacity_note"],
    }
    return v


def replay_block(
    signals: pl.DataFrame, days: list[str], costs: tuple[float, ...], n_signals: int
) -> tuple[dict, dict, dict, dict]:
    """All nine cells plus the frozen stored-label anchor, at every rung of the ladder."""
    reports: dict[str, dict[str, dict]] = {}
    trades: dict[str, list[dict]] = {}
    daily: dict[str, np.ndarray] = {}
    for cell in CELLS:
        reports[cell.name] = {}
        for cost in costs:
            metrics, tr = replay_cell(signals, days, cell, cost)
            reports[cell.name][str(int(cost))] = cell_report(
                metrics, tr, cell, cost, n_signals, stored=False
            )
            if cost == SELECT_COST_BPS:
                trades[cell.name] = tr
                daily[cell.name] = np.array(
                    [d["lower_bound_return"] for d in metrics["daily"]], dtype=float
                )
        print_block("cell", reports[cell.name][str(int(SELECT_COST_BPS))])
    frozen: dict[str, dict] = {}
    frozen_trades: list[dict] = []
    for cost in costs:
        metrics, tr = replay_cell(signals, days, CONTROL_CELL, cost, stored=True)
        frozen[str(int(cost))] = cell_report(
            metrics, tr, CONTROL_CELL, cost, n_signals, stored=True
        )
        if cost == SELECT_COST_BPS:
            frozen_trades = tr
    print_block("anchor", frozen[str(int(SELECT_COST_BPS))])
    return reports, trades, {"reports": frozen, "trades": frozen_trades}, daily


def rank_cells(reports: dict, cost_bps: float) -> list[tuple[str, dict]]:
    """Deterministic ranking: lower-bound $/calendar day, then known fills, then attempts,
    then cell key. No sample floor, no median gate, no CI veto."""
    key = str(int(cost_bps))
    ranked = sorted(
        reports.items(),
        key=lambda kv: (
            -float(
                kv[1][key]["economics"]["full_loss_lower_bound_over_unknowns"]["dollars_per_day"]
                if kv[1][key]["economics"]["full_loss_lower_bound_over_unknowns"]["dollars_per_day"]
                is not None
                else 0.0
            ),
            -int(kv[1][key]["known_fills"]),
            int(kv[1][key]["attempts"]),
            kv[0],
        ),
    )
    return ranked


def cell_delta(a: dict, b: dict) -> dict:
    """Like-for-like delta of one cell's economics against a reference cell's."""
    la = a["economics"]["full_loss_lower_bound_over_unknowns"]
    lb = b["economics"]["full_loss_lower_bound_over_unknowns"]
    ka = a["economics"]["known_fills_only"]
    kb = b["economics"]["known_fills_only"]
    return {
        "delta_dollars_per_day_lower_bound": (
            la["dollars_per_day"] - lb["dollars_per_day"]
            if la["dollars_per_day"] is not None and lb["dollars_per_day"] is not None
            else None
        ),
        "delta_dollars_per_year_252_lower_bound": (
            la["dollars_per_year_252"] - lb["dollars_per_year_252"]
            if la["dollars_per_year_252"] is not None and lb["dollars_per_year_252"] is not None
            else None
        ),
        "delta_dollars_per_day_known_only": (
            ka["dollars_per_day"] - kb["dollars_per_day"]
            if ka["dollars_per_day"] is not None and kb["dollars_per_day"] is not None
            else None
        ),
        "delta_known_fills": a["known_fills"] - b["known_fills"],
        "delta_unknown_fills": a["unknown_fills"] - b["unknown_fills"],
        "delta_attempts": a["attempts"] - b["attempts"],
        "delta_target_hit_fills": a["target_hit_count"] - b["target_hit_count"],
        "delta_mean_net_known_fill": (
            a["mean_net_known_fill"] - b["mean_net_known_fill"]
            if a["mean_net_known_fill"] is not None and b["mean_net_known_fill"] is not None
            else None
        ),
    }


def timeout_rule_divergence(bar_trades: list[dict], stored_trades: list[dict]) -> dict:
    """How far the grid's bar-rule timeout sits from the panel's frozen h60 label."""
    stored_by_key = {(t["day"], t["ticker"], t["t"]): t for t in stored_trades}
    compared = differing = 0
    deltas: list[float] = []
    for t in bar_trades:
        s = stored_by_key.get((t["day"], t["ticker"], t["t"]))
        if s is None or s["gross"] is None or t["gross"] is None:
            continue
        compared += 1
        d = float(t["gross"]) - float(s["gross"])
        deltas.append(d)
        if abs(d) > FLOAT_TOL:
            differing += 1
    arr = np.array(deltas, dtype=float)
    return {
        "compared_known_fills": compared,
        "differing_fills": differing,
        "differing_share_of_compared": (differing / compared) if compared else None,
        "mean_gross_delta": float(arr.mean()) if arr.size else None,
        "mean_abs_gross_delta": float(np.abs(arr).mean()) if arr.size else None,
        "max_abs_gross_delta": float(np.abs(arr).max()) if arr.size else None,
        "note": (
            "the bar-rule timeout prices the LAST bar at/before min(entry+60, session_end) "
            "while the frozen label prices the FIRST actual minute open at/after "
            "min(entry+60, session_end); they agree unless the exact entry+60 minute is "
            "absent from the tape"
        ),
    }


def reproduction_check(reports: dict, expected: dict, checked: bool) -> dict:
    """Field-by-field reproduction of the parent study's validated repeat_h60 cell."""
    key = "100"  # the historical rung the parent's validated numbers were recorded at
    if not checked:
        return {"checked": False, "reason": "restricted day set (--days); comparison skipped"}
    cell = reports[key]
    checks: dict[str, dict] = {}
    checks["n_signals"] = {
        "expected": expected.get("n_signals"),
        "reproduced": cell["sample"]["n_signals"],
        "match": expected.get("n_signals") == cell["sample"]["n_signals"],
    }
    for f in (
        "days",
        "attempts",
        "fills",
        "known_fills",
        "unknown_fills",
        "traded_days",
        "cash_or_slot_skips",
        "skips_total",
        "positive_months_lower_bound",
        "months",
    ):
        o = expected.get(f)
        m = cell.get(f)
        checks[f] = {"expected": o, "reproduced": m, "match": o == m}
    for f in (
        "mean_net_known_fill",
        "mean_daily_lower_bound",
        "dollars_per_day",
        "known_win_rate",
        "known_profit_factor",
        "worst_known_fill",
    ):
        o, m = expected.get(f), cell.get(f)
        ok = (o is None and m is None) or (
            o is not None and m is not None and abs(float(o) - float(m)) <= FLOAT_TOL
        )
        checks[f] = {"expected": o, "reproduced": m, "match": ok}
    return {
        "checked": True,
        "reference_rung_bps": 100.0,
        "reference_provenance": (
            "alpha_sparse_daily repeat_h60, same immutable model/threshold/cadence, panel "
            "stored h60 labels, recorded from the parent study's validated run"
        ),
        "checks": checks,
        "all_match": all(c.get("match") for c in checks.values()),
    }


def print_block(tag: str, report: dict) -> None:
    cell = report["cell"]
    econ = report["economics"]["full_loss_lower_bound_over_unknowns"]
    known = report["economics"]["known_fills_only"]
    tgt = (
        f"tgt+{cell['target_bps'] / 100.0:g}%x{cell['target_share']:g}"
        if cell["target_bps"] is not None
        else "h60-control"
    )
    print(
        f"[{tag}] {cell['name']:<24} {tgt:<14} "
        f"$/day={_fmt(econ['dollars_per_day'], '+.2f')} "
        f"known_only={_fmt(known['dollars_per_day'], '+.2f')} "
        f"yr252={_fmt(econ['dollars_per_year_252'], '+.0f')} "
        f"known={report['known_fills']} unk={report['unknown_fills']} "
        f"hits={report['target_hit_count']} timeouts={report['timeout_count']} "
        f"traded={report['traded_days']}/{report['days']} "
        f"attempts={report['attempts']} unfilled={report['unfilled_attempts']} "
        f"signals={report['sample']['n_signals']} "
        f"skips={report['cash_or_slot_skips']}/{report['skips_total']}",
        flush=True,
    )


def predeclared_contract(model_path: Path, model_sha: str, producer_sha: str, bundle: dict) -> dict:
    """The full fixed grid + cost ladder + rules, with no outcome field anywhere."""
    return {
        "study": "alpha_retained_target_exits",
        "label": (
            "predeclared take-profit target exits on the frozen repeat-cadence h60 "
            "learned top-gainer selector (same model, same threshold, same cadence; "
            "only the exit rule differs)"
        ),
        "status": STATUS,
        "objective": (
            "does a predeclared take-profit target beat the frozen h60 timeout on the "
            "same admission stream, measured as known-contribution dollars per FULL "
            "calendar day on a $3,000 research reserve"
        ),
        "frozen_signal": {
            "model": "payoff_h60.joblib (loaded, never refit, never re-fitted)",
            "threshold": THRESHOLD,
            "max_attempts_per_ticker_per_session": MAX_ATTEMPTS,
            "cooldown_min": COOLDOWN_MIN,
            "cooldown_anchor": (
                "the ACTUAL exit minute of the cell (target-accelerated when the target "
                "hits)"
            ),
            "max_positions": MAX_POSITIONS,
            "order_budget_usd": ORDER_BUDGET,
            "reserve_usd": RESERVE_USD,
            "one_position_per_ticker": True,
            "funding_rule": (
                "simultaneous intents funded by descending score before any fill outcome"
            ),
            "unfilled_rule": (
                "cash retained: reserve and release the ticket one minute later, "
                "fee-free, still an attempt"
            ),
            "unknown_rule": (
                "holds cash+slot to session end, charged a full $1,000 ticket loss in "
                "the lower bound"
            ),
        },
        "cells": [
            {
                "name": c.name,
                "target_bps": c.target_bps,
                "target_share": c.target_share,
                "control": c.control,
                "exit_rule": c.exit_rule,
                "legs_per_known_fill": c.legs_per_known_fill,
            }
            for c in CELLS
        ],
        "control_cell": CONTROL_CELL.name,
        "reproduction_anchor_cell": FROZEN_LABEL_CELL_NAME,
        "cost_scenarios_bps": [float(c) for c in COST_SCENARIOS_BPS],
        "historical_diagnostic_cost_bps": [HISTORICAL_COST_BPS],
        "cost_ladder_validation_bps": [float(c) for c in VAL_COSTS],
        "cost_ladder_late_bps": [float(c) for c in LATE_COSTS],
        "selection_cost_bps": SELECT_COST_BPS,
        "selection_rule": {
            "metric": (
                "known-contribution dollars per FULL calendar day on the 2023 validation "
                "block at the 25 bps rung = mean over all validation days of (known net "
                "P&L - $1,000 x UNKNOWN fills); the known-only figure (same but without "
                "the UNKNOWN full-loss charge) is reported beside every cell and ranked "
                "as a separate diagnostic"
            ),
            "tie_break": [
                "dollars_per_day desc",
                "known_fills desc",
                "attempts asc",
                "cell key asc",
            ],
            "frozen_before": "any late-block (2025-02..2026-05) outcome is computed or read",
            "stress_vetoes": None,
            "power_floors": None,
            "median_or_tail_gates": None,
            "ci_vetoes": None,
            "cost_ladder_is_comparison_not_hurdle": True,
        },
        "exit_mechanics": {
            "target_detection": (
                "first bar STRICTLY after the entry minute with high >= "
                "entry_open*(1+target), within (entry_et, min(entry_et+60, session_end)]"
            ),
            "target_pricing": (
                "AT the target (conservative for a resting sell limit), never at the bar "
                "high"
            ),
            "timeout": "open of the LAST bar at/before min(entry_et+60, session_end)",
            "unknown": (
                "no bar strictly after the entry minute inside the horizon, or an absent "
                "tape stream"
            ),
            "scale_out": (
                "half at the target, half at the h60 timeout; one entry-side charge and "
                "exit-side friction on both legs' proceeds; the ticket and the slot are "
                "released at the full close (the timeout minute)"
            ),
            "costs": (
                "fees charged on BOTH legs; net = (1+gross)*(1-side)/(1+side)-1, "
                "side = bps/20000"
            ),
        },
        "honest_limits": HONEST_LIMITS,
        "novelty": {
            "vs_exit_management_roster": {
                "prior": "factory/scripts/alpha_sparse_exit_management.py",
                "prior_mechanism": (
                    "quote-triggered adverse STOPS (10%/15%) on the ORIGINAL fixed entries "
                    "of the exploratory sparse extension, priced at as-of NBBO touches with "
                    "250 ms arrival latency and a 0/10/25 bps residual ladder"
                ),
                "this_study": (
                    "favorable take-profit TARGETS (1/2/3/5%) on a repeat-cadence "
                    "re-selected entry stream, priced from minute bars, with the cooldown "
                    "anchored to the target-accelerated actual exit so the target changes "
                    "the trade clock itself"
                ),
                "why_not_the_same_experiment": (
                    "favorable not adverse triggers, minute bars not quotes, re-selected "
                    "repeated entries not fixed original entries, and the exit changes the "
                    "cooldown clock so the whole attempt sequence is cell-specific"
                ),
            },
            "not_resurrected": ["quote-triggered stop roster", "legacy h15 freeze"],
        },
        "model": {
            "refit": False,
            "hpo": False,
            "feature_changes": False,
            "ticker_features_added": False,
            "horizons_scored": [MODEL_HORIZON],
            "fit_block_2021_2022_scored": False,
            "fit_block_note": (
                "the 2021-2022 fit block is never replayed (weights are frozen), so it is "
                "not scored; the single prediction pass over the analysis corpus feeds "
                "every cell identically"
            ),
            "stored_path": str(model_path),
            "stored_sha256": model_sha,
            "feature_order": FEATURES_ALL,
            "stored_params": bundle.get("params"),
            "clip_fit": bundle.get("clip_fit"),
            "seed": bundle.get("seed"),
        },
        "replay": {
            "engine": "alpha_retained_target_exits.replay_cell (shared funded-reserve conventions)",
            "reserve_usd": RESERVE_USD,
            "max_positions": MAX_POSITIONS,
            "order_budget_usd": ORDER_BUDGET,
            "leverage": False,
            "fees_both_legs": True,
            "one_position_per_symbol": True,
            "same_clock_exits_before_buys": True,
            "cooldown_rule": (
                "no reopening until the ACTUAL exit minute + 15 flat minutes; a "
                "pending/UNKNOWN exit keeps its slot and blocks its ticker for the rest of "
                "the session"
            ),
            "unknown_exit_rule": "full-unit lower-bound charge at the day's end, never cash",
            "unfilled_rule": (
                "reserve and release the ticket one minute later, fee-free, and still "
                "count the attempt"
            ),
            "all_calendar_days_retained": True,
            "daily_reset_is_not_self_financing_cagr": True,
            "carry_equity_check": "additive fixed-ticket equity, no compounding",
            "annualization": f"simple {TRADING_DAYS_PER_YEAR}-session multiplication, never a CAGR",
        },
        "part_schema_version": PART_SCHEMA_VERSION,
        "resume_hash_inputs": [
            "producer_sha256",
            "predeclared_contract_sha256",
            "model_sha256",
            "day",
            "schema_version",
        ],
    }


def selection_section(
    ranked: list[tuple[str, dict]],
    reports: dict,
    costs: tuple[float, ...],
    val_days: list[str],
    repro: dict,
) -> dict:
    """The frozen 2023-only choice: ranked cells, the chosen cell, and every veto that is NOT."""
    chosen_name, chosen_cell = ranked[0]
    key = str(int(SELECT_COST_BPS))
    known_ranked = sorted(
        reports.items(),
        key=lambda kv: (
            -float(
                kv[1][key]["economics"]["known_fills_only"]["dollars_per_day"]
                if kv[1][key]["economics"]["known_fills_only"]["dollars_per_day"] is not None
                else 0.0
            ),
            -int(kv[1][key]["known_fills"]),
            int(kv[1][key]["attempts"]),
            kv[0],
        ),
    )
    chosen_known_only = known_ranked[0][0]
    return {
        "objective": (
            "2023 validation known-contribution dollars per FULL calendar day on the "
            "research reserve at the 25 bps rung (the full-loss lower bound over unknowns; "
            "the known-only figure is ranked beside it as a diagnostic)"
        ),
        "selection_cost_bps": SELECT_COST_BPS,
        "cost_scenario_nature": COST_NATURE,
        "predeclared_before_late_inspection": True,
        "status": STATUS,
        "validation_days": len(val_days),
        "cost_ladder_validation_bps": [float(c) for c in costs],
        "cost_ladder_is_comparison_not_hurdle": True,
        "stress_vetoes": None,
        "power_floors": None,
        "median_or_tail_gates": None,
        "ci_vetoes": None,
        "ranked_dollars_per_day": {
            name: reports[name][key]["economics"]["full_loss_lower_bound_over_unknowns"][
                "dollars_per_day"
            ]
            for name, _ in ranked
        },
        "ranked_known_fills": {name: reports[name][key]["known_fills"] for name, _ in ranked},
        "ranked_attempts": {name: reports[name][key]["attempts"] for name, _ in ranked},
        "known_only_metric_ranking": {
            "ranked_dollars_per_day": {
                name: reports[name][key]["economics"]["known_fills_only"]["dollars_per_day"]
                for name, _ in known_ranked
            },
            "chosen_under_known_only_metric": chosen_known_only,
            "choice_differs_from_selection": chosen_known_only != chosen_name,
        },
        "chosen": chosen_cell[key]["cell"],
        "chosen_mean_daily_lower_bound": chosen_cell[key]["mean_daily_lower_bound"],
        "chosen_dollars_per_day": chosen_cell[key]["economics"][
            "full_loss_lower_bound_over_unknowns"
        ]["dollars_per_day"],
        "chosen_dollars_per_year_252": chosen_cell[key]["economics"][
            "full_loss_lower_bound_over_unknowns"
        ]["dollars_per_year_252"],
        "chosen_known_fills": chosen_cell[key]["known_fills"],
        "chosen_target_hit_fills": chosen_cell[key]["target_hit_count"],
        "chosen_timeout_fills": chosen_cell[key]["timeout_count"],
        "chosen_traded_days": chosen_cell[key]["traded_days"],
        "chosen_full_ladder": reports[chosen_name],
        "control_cell_comparison": {
            "control": CONTROL_CELL.name,
            "control_dollars_per_day": reports[CONTROL_CELL.name][key]["economics"][
                "full_loss_lower_bound_over_unknowns"
            ]["dollars_per_day"],
            "delta_dollars_per_day": (
                chosen_cell[key]["economics"]["full_loss_lower_bound_over_unknowns"][
                    "dollars_per_day"
                ]
                - reports[CONTROL_CELL.name][key]["economics"][
                    "full_loss_lower_bound_over_unknowns"
                ]["dollars_per_day"]
            ),
        },
        "frozen_reference_reproduction": repro,
    }


def decision_text(
    chosen_name: str,
    val: dict,
    late: dict | None,
    frozen_val: dict,
    frozen_late: dict | None,
    control_name: str,
) -> str:
    key = str(int(SELECT_COST_BPS))
    v = val[chosen_name][key]["economics"]["full_loss_lower_bound_over_unknowns"]
    c = val[control_name][key]["economics"]["full_loss_lower_bound_over_unknowns"]
    f = frozen_val[key]["economics"]["full_loss_lower_bound_over_unknowns"]
    text = (
        f"{STATUS}: chosen {chosen_name} by 2023 validation known-contribution $/full "
        f"calendar day at {int(SELECT_COST_BPS)}bps: {_fmt(v['dollars_per_day'], '+.2f')} $/day "
        f"= {_fmt(v['dollars_per_year_252'], '+.0f')} $/yr simple, "
        f"{val[chosen_name][key]['known_fills']} known fills "
        f"({val[chosen_name][key]['target_hit_count']} target-hit, "
        f"{val[chosen_name][key]['timeout_count']} timeout) on "
        f"{val[chosen_name][key]['traded_days']}/250 traded days; "
        f"control {control_name} {_fmt(c['dollars_per_day'], '+.2f')} $/day, "
        f"frozen stored-label anchor {_fmt(f['dollars_per_day'], '+.2f')} $/day; "
        f"no sample floor, median gate or CI veto applied and the cost ladder is a "
        f"comparison set, never a hurdle"
    )
    if late is None:
        return (
            text + "; late block not traversed (--skip-late, or no late days selected), so the "
            "choice is a validation-only object"
        )
    late_lb = late[chosen_name][key]["economics"]["full_loss_lower_bound_over_unknowns"]
    fl = frozen_late[key]["economics"]["full_loss_lower_bound_over_unknowns"]
    return (
        text + f"; late transparency (2025-02..2026-05, previously explored, NOT pristine): "
        f"{_fmt(late_lb['dollars_per_day'], '+.2f')} $/day on 332 days with "
        f"{late[chosen_name][key]['known_fills']} known fills on "
        f"{late[chosen_name][key]['traded_days']} traded days, against the frozen "
        f"stored-label anchor's {_fmt(fl['dollars_per_day'], '+.2f')} $/day"
    )


def _fmt(value, spec="+.6f"):
    return "n/a" if value is None else format(value, spec)


def analysis_blocks(days: list[str] | None) -> dict[str, list[str]]:
    files = sorted(
        p.stem for p in (PANEL_ROOT / "days").glob("????-??-??.parquet") if allowed(p.stem)
    )
    keep = set(days) if days else None
    blocks: dict[str, list[str]] = {}
    for day in files:
        if keep is not None and day not in keep:
            continue
        p = period(day)
        if p in ANALYSIS_PERIODS:
            blocks.setdefault(p, []).append(day)
    if keep is None:
        for p, expected in EXPECTED_DAYS.items():
            if len(blocks.get(p, [])) != expected:
                raise SystemExit(
                    f"[target-exits] requires {expected} {p} days, got {len(blocks.get(p, []))}"
                )
    if not blocks:
        raise SystemExit("[target-exits] no analysis days selected")
    return blocks


# ----- main pipeline -----------------------------------------------------------
def assemble_results(
    *,
    contract: dict,
    selection: dict,
    val_reports: dict,
    ranked_val: list,
    late_reports: dict,
    late_rank: list,
    repro_val: dict,
    repro_late: dict,
    divergences: dict,
    deltas: dict,
    chosen_name: str,
    decision: str,
    cov_val: dict,
    cov_late: dict,
    out: Path,
    parts_dir: Path,
    trades_path: Path,
    t0: float,
) -> dict:
    """The final results object. One place, so the per-cell report shape is exercised."""
    key = str(int(SELECT_COST_BPS))
    # the chosen cell's OWN report at the selection rung, in both blocks (a report is
    # keyed by cost first; every chosen field below is read through that rung).
    chosen_val = val_reports[chosen_name][key]
    chosen_late = late_reports[chosen_name][key]
    return {
        "contract": contract,
        "honest_limits": HONEST_LIMITS,
        "selection": selection,
        "validation": val_reports,
        "validation_ranking_at_25bps": [name for name, _ in ranked_val],
        "late_transparency": late_reports,
        "late_ranking_at_25bps": late_rank,
        "frozen_reference_reproduction": {"validation": repro_val, "late": repro_late},
        "timeout_rule_divergence": divergences,
        "delta_vs_control_cell": {
            "validation": {
                n: {c: d["vs_control_cell"] for c, d in v.items()}
                for n, v in deltas["validation"].items()
            },
            "late": {
                n: {c: d["vs_control_cell"] for c, d in v.items()}
                for n, v in deltas["late"].items()
            },
        },
        "delta_vs_frozen_reference": {
            "validation": {
                n: {c: d["vs_frozen_reference"] for c, d in v.items()}
                for n, v in deltas["validation"].items()
            },
            "late": {
                n: {c: d["vs_frozen_reference"] for c, d in v.items()}
                for n, v in deltas["late"].items()
            },
        },
        "chosen": {
            **chosen_val["cell"],
            "selection_cost_bps": SELECT_COST_BPS,
            "validation_dollars_per_day": chosen_val["economics"][
                "full_loss_lower_bound_over_unknowns"
            ]["dollars_per_day"],
            "validation_dollars_per_year_252": chosen_val["economics"][
                "full_loss_lower_bound_over_unknowns"
            ]["dollars_per_year_252"],
            "late_dollars_per_day": chosen_late["economics"]["full_loss_lower_bound_over_unknowns"][
                "dollars_per_day"
            ],
            "late_known_fills": chosen_late["known_fills"],
            "late_traded_days": chosen_late["traded_days"],
            "late_target_hit_fills": chosen_late["target_hit_count"],
            "late_timeout_fills": chosen_late["timeout_count"],
        },
        "decision": decision,
        "status": STATUS,
        "coverage": {"validation": cov_val, "late": cov_late},
        "artifacts": {
            "contract": str(out / "contract.json"),
            "selection": str(out / "selection.json"),
            "signals_validation": str(out / "signals_validation.parquet"),
            "signals_late": str(out / "signals_late.parquet"),
            "chosen_trades": str(trades_path),
            "parts": str(parts_dir),
            "producer_snapshot": str(out / "producer_snapshot.py"),
        },
        "runtime_s": round(time.time() - t0, 1),
    }


def run(args: argparse.Namespace) -> None:
    t0 = time.time()
    out = args.out or OUTPUT
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")
    producer_sha = sha256_file(Path(__file__))

    # 0) ONE stored model, loaded and scored (never fitted). The 2021-2022 fit block is
    #    never read: only the 2023 validation and 2025-02..2026-05 late blocks are.
    bundle, model_path = load_stored_model(PANEL_ROOT / "learned" / "models")
    model_sha = sha256_file(model_path)
    print(
        f"[model] h{MODEL_HORIZON} {model_path} (sha256 {model_sha[:12]}), "
        f"seed={bundle.get('seed')} clip={bundle.get('clip_fit')}",
        flush=True,
    )
    blocks = analysis_blocks(args.days)
    val_days = blocks.get(VAL_PERIOD, [])
    conf_days = blocks.get(CONF_PERIOD, [])
    if not val_days:
        raise SystemExit(
            "[target-exits] no 2023 validation days selected; the selection objective "
            "requires the validation block"
        )
    checked = bool(args.days is None)
    print(
        f"[blocks] validation={len(val_days)} days, late={len(conf_days)} days "
        f"(reproduction check {'on' if checked else 'off: restricted day set'})",
        flush=True,
    )

    # 1) the predeclared contract (full fixed grid + cost ladder, zero outcome fields) is
    #    written BEFORE any validation outcome exists, and its sha feeds the resume hash.
    pre = predeclared_contract(model_path, model_sha, producer_sha, bundle)
    pre_sha = hashlib.sha256(
        json.dumps(pre, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    def hasher(day: str) -> str:
        return resume_hash(day, producer_sha, pre_sha, model_sha)

    contract = {
        **pre,
        "predeclared_sha256": pre_sha,
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "late_block_state": "NOT YET TRAVERSED at contract time",
        "selection": {
            "status": (
                "PENDING: frozen in selection.json after the 2023-only validation replay "
                "and BEFORE any late-block outcome is computed or read"
            )
        },
        "provenance": {
            "panel_root": str(PANEL_ROOT),
            "producer_path": str(Path(__file__)),
            "producer_sha256": producer_sha,
            "model_sha256": model_sha,
            "tape_root": str(BARS_ROOT),
            "analysis_days": len(val_days) + len(conf_days),
            "validation_days": len(val_days),
            "late_days": len(conf_days),
        },
    }
    (out / "contract.json").write_text(json.dumps(contract, indent=2, default=str) + "\n")
    print(
        f"[freeze] contract -> {out / 'contract.json'} (grid + cost ladder, before any "
        f"validation outcome; predeclared sha256 {pre_sha[:12]})",
        flush=True,
    )

    # 2) score + replay the 2023 validation block ONLY.
    parts_dir = out / "parts"
    cov_val = collect_parts(
        val_days, out, resume=args.resume, hasher=hasher, model=bundle["lgbm"], tag="validation"
    )
    sig_val = load_block(val_days, parts_dir, hasher)
    n_val = int(sig_val.height)
    sig_val.write_parquet(out / "signals_validation.parquet")
    print(
        f"[collect:validation] {n_val} thr-clearing states on {len(val_days)} days "
        f"(timeout status {cov_val['tp_status']}, bar files missing "
        f"{len(cov_val['missing_bar_files'])}, streams missing "
        f"{sum(len(v) for v in cov_val['missing_symbol_streams'].values())})",
        flush=True,
    )
    val_reports, val_trades, val_frozen, val_daily = replay_block(
        sig_val, val_days, VAL_COSTS, n_val
    )
    for name in CELLS_BY_NAME:
        got = val_reports[name]["25"]["sample"]["n_signals"]
        if got != n_val:
            raise SystemExit(
                f"[target-exits] cell {name} saw {got} signals, expected the shared {n_val}"
            )
    np.savez(out / "daily_validation.npz", **val_daily)
    del sig_val  # only this block's signals are ever resident; the reports are plain dicts
    repro_val = reproduction_check(
        val_frozen["reports"], FROZEN_REFERENCE[VAL_PERIOD], checked=checked
    )
    if repro_val["checked"]:
        print(
            "[anchor] frozen stored-label h60 control reproduces the parent study's "
            f"validated repeat_h60 cell (all_match={repro_val['all_match']}): "
            f"{val_frozen['reports']['100']['known_fills']} known fills, "
            f"{_fmt(val_frozen['reports']['100']['dollars_per_day'], '+.4f')} $/day @100",
            flush=True,
        )

    # 3) freeze the choice on 2023 validation ONLY, then write selection.json BEFORE any
    #    late-block outcome is computed or read.
    ranked_val = rank_cells(val_reports, SELECT_COST_BPS)
    chosen_name = ranked_val[0][0]
    selection = {
        "study": "alpha_retained_target_exits",
        "status": STATUS,
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "predeclared_sha256": pre_sha,
        "contract_path": str(out / "contract.json"),
        "late_block_state": (
            "NOT YET TRAVERSED at freeze time (transparency only, choice immutable)"
        ),
        **selection_section(ranked_val, val_reports, VAL_COSTS, val_days, repro_val),
    }
    (out / "selection.json").write_text(json.dumps(selection, indent=2, default=str) + "\n")
    print(
        f"[select] {chosen_name} by 2023 validation known-contribution $/full calendar day "
        f"at {int(SELECT_COST_BPS)}bps: "
        f"{_fmt(selection['chosen_dollars_per_day'], '+.2f')} $/day of "
        f"{len(CELLS)} cells; control {CONTROL_CELL.name} "
        f"{_fmt(selection['control_cell_comparison']['control_dollars_per_day'], '+.2f')} $/day; "
        f"choice differs under the known-only diagnostic metric: "
        f"{selection['known_only_metric_ranking']['choice_differs_from_selection']}",
        flush=True,
    )
    print(
        f"[freeze] selection -> {out / 'selection.json'} (before any late read; the choice "
        f"is immutable for the rest of this run)",
        flush=True,
    )

    if args.skip_late or not conf_days:
        note = "late block not traversed" + (
            "" if args.skip_late else " (no late days selected under --days)"
        )
        results = {
            "contract": contract,
            "late_block_note": note,
            "honest_limits": HONEST_LIMITS,
            "selection": selection,
            "validation": val_reports,
            "validation_ranking_at_25bps": [name for name, _ in ranked_val],
            "frozen_reference_reproduction": {"validation": repro_val},
            "timeout_rule_divergence": {
                "validation": timeout_rule_divergence(
                    val_trades[CONTROL_CELL.name], val_frozen["trades"]
                )
            },
            "delta_vs_control_cell": {
                "validation": {
                    name: {
                        cost: cell_delta(
                            val_reports[name][cost], val_reports[CONTROL_CELL.name][cost]
                        )
                        for cost in (str(int(c)) for c in VAL_COSTS)
                    }
                    for name in val_reports
                }
            },
            "delta_vs_frozen_reference": {
                "validation": {
                    name: {
                        cost: cell_delta(val_reports[name][cost], val_frozen["reports"][cost])
                        for cost in (str(int(c)) for c in VAL_COSTS)
                    }
                    for name in val_reports
                }
            },
            "decision": decision_text(
                chosen_name, val_reports, None, val_frozen["reports"], None, CONTROL_CELL.name
            ),
            "status": STATUS,
            "coverage": {"validation": cov_val},
            "artifacts": {
                "contract": str(out / "contract.json"),
                "selection": str(out / "selection.json"),
                "signals_validation": str(out / "signals_validation.parquet"),
                "parts": str(parts_dir),
                "producer_snapshot": str(out / "producer_snapshot.py"),
            },
            "runtime_s": round(time.time() - t0, 1),
        }
        (out / "results.json").write_text(json.dumps(results, indent=2, default=str) + "\n")
        print(f"[decision] {results['decision']}", flush=True)
        print(f"[done] results -> {out / 'results.json'} ({results['runtime_s']}s)", flush=True)
        return

    # 4) late block: transparency only, with the choice immutable from step 3.
    cov_late = collect_parts(
        conf_days, out, resume=args.resume, hasher=hasher, model=bundle["lgbm"], tag="late"
    )
    sig_late = load_block(conf_days, parts_dir, hasher)
    n_late = int(sig_late.height)
    sig_late.write_parquet(out / "signals_late.parquet")
    print(
        f"[collect:late] {n_late} thr-clearing states on {len(conf_days)} days "
        f"(timeout status {cov_late['tp_status']})",
        flush=True,
    )
    late_reports, late_trades, late_frozen, late_daily = replay_block(
        sig_late, conf_days, LATE_COSTS, n_late
    )
    np.savez(out / "daily_late.npz", **late_daily)
    repro_late = reproduction_check(
        late_frozen["reports"], FROZEN_REFERENCE[CONF_PERIOD], checked=checked
    )
    if repro_late["checked"]:
        print(
            "[anchor:late] frozen stored-label h60 control on the late block "
            f"(all_match={repro_late['all_match']}): "
            f"{late_frozen['reports']['100']['known_fills']} known fills, "
            f"{_fmt(late_frozen['reports']['100']['dollars_per_day'], '+.4f')} $/day @100",
            flush=True,
        )

    divergences = {}
    for tag, (bar_tr, stored_tr) in {
        "validation": (val_trades[CONTROL_CELL.name], val_frozen["trades"]),
        "late": (late_trades[CONTROL_CELL.name], late_frozen["trades"]),
    }.items():
        d = timeout_rule_divergence(bar_tr, stored_tr)
        d["bar_rule_control_fills"] = len(bar_tr)
        d["frozen_label_anchor_fills"] = len(stored_tr)
        divergences[tag] = d
    print(
        "[divergence] bar-rule timeout vs the frozen stored h60 label on the control "
        f"cadence: validation {divergences['validation']['differing_fills']}/"
        f"{divergences['validation']['compared_known_fills']} fills differ "
        f"({_fmt(divergences['validation']['differing_share_of_compared'], '.1%')}), "
        f"late {divergences['late']['differing_fills']}/"
        f"{divergences['late']['compared_known_fills']} "
        f"({_fmt(divergences['late']['differing_share_of_compared'], '.1%')})",
        flush=True,
    )

    cost_keys = tuple(str(int(c)) for c in LATE_COSTS)
    val_cost_keys = tuple(str(int(c)) for c in VAL_COSTS)
    deltas = {
        "validation": {
            name: {
                cost: {
                    "vs_control_cell": cell_delta(
                        val_reports[name][cost], val_reports[CONTROL_CELL.name][cost]
                    ),
                    "vs_frozen_reference": cell_delta(
                        val_reports[name][cost], val_frozen["reports"][cost]
                    ),
                }
                for cost in val_cost_keys
            }
            for name in val_reports
        },
        "late": {
            name: {
                cost: {
                    "vs_control_cell": cell_delta(
                        late_reports[name][cost], late_reports[CONTROL_CELL.name][cost]
                    ),
                    "vs_frozen_reference": cell_delta(
                        late_reports[name][cost], late_frozen["reports"][cost]
                    ),
                }
                for cost in cost_keys
            }
            for name in late_reports
        },
    }

    trades_out = []
    for tag, tr in (("validation", val_trades), ("late", late_trades)):
        for t in tr[chosen_name]:
            trades_out.append({"block": tag, **t})
    trades_path = out / "chosen_trades.parquet"
    if trades_out:
        pl.DataFrame(trades_out).write_parquet(trades_path)
    else:
        pl.DataFrame(schema={"block": pl.String, "day": pl.String}).write_parquet(trades_path)

    late_rank = [name for name, _ in rank_cells(late_reports, SELECT_COST_BPS)]
    decision = decision_text(
        chosen_name,
        val_reports,
        late_reports,
        val_frozen["reports"],
        late_frozen["reports"],
        CONTROL_CELL.name,
    )
    results = assemble_results(
        contract=contract,
        selection=selection,
        val_reports=val_reports,
        ranked_val=ranked_val,
        late_reports=late_reports,
        late_rank=late_rank,
        repro_val=repro_val,
        repro_late=repro_late,
        divergences=divergences,
        deltas=deltas,
        chosen_name=chosen_name,
        decision=decision,
        cov_val=cov_val,
        cov_late=cov_late,
        out=out,
        parts_dir=parts_dir,
        trades_path=trades_path,
        t0=t0,
    )
    (out / "results.json").write_text(json.dumps(results, indent=2, default=str) + "\n")
    print(f"[decision] {decision}", flush=True)
    print(f"[done] results -> {out / 'results.json'} ({results['runtime_s']}s)", flush=True)


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--out", type=Path, default=None, help=f"output root (default: {OUTPUT})")
    p.add_argument(
        "--days", nargs="+", default=None, help="restrict the analysis corpus to these days (debug)"
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="resume collection from the per-day parts already on disk",
    )
    p.add_argument(
        "--skip-late",
        action="store_true",
        help="stop after the frozen contract + validation selection",
    )
    run(p.parse_args())


if __name__ == "__main__":
    main()
