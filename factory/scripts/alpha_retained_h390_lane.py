#!/usr/bin/env python3
"""Retained h390 hold-to-session-close lane: threshold x entry-delay x cadence grid (no refit).

QUESTION. Every retained lane so far scored a stored head and exited on a FIXED horizon
(h15/h30/h60). The stored h390 head (``learned/models/payoff_h390.joblib``, fit
2021-02..2022-12 on the 26 causal features, sha256
90c796db9fd6880aa8ae85abd676f93857015c186d3e1155a1434605c82344c7, verified at load,
never refit, never HPO'd) predicts the payoff of a hold that runs to the session close:
on this panel ``min(t + 390, session_end)`` is ``session_end`` for every state minute
``t`` (``t`` >= 570, ``session_end`` = 959 on every panel day), so the panel's stored
h390 label already IS the session-close label. This study asks exactly one question:
does a HOLD-TO-SESSION-CLOSE lane on that head earn its keep at low modeled round-trip
friction, and does a 2-minute-later entry or the retained repeat cadence change the
answer?

Predeclared grid = thresholds {0.020, 0.030, 0.050} x entry delay {0, 2} x cadence
{once, repeat} = 12 fixed cells, scored ONCE per day on the SAME causal panel states
(``alpha_sparse_daily.day_context`` -> ``alpha_open_sim.causal_liquidity`` ->
``alpha_open_learned.feature_matrix``) by the one h390 head. A cell can only change
WHICH states clear the admission bar, WHEN the entry price is read (minute ``t`` or
``t + 2``) and HOW OFTEN a ticker may recur - never what the state means. No refit, no
HPO, no new features, no second model. The 484 development days (2021-02..2022-12) are
the fit block and are never scored; the analysis corpus is the 250 validation sessions
of 2023 and the 332 late sessions of 2025-02..2026-05.

LABEL BASIS (stated): EVERY cell holds to the session close - exit = the ticker's LAST
actual bar open at or before ``session_end``. The d=0 cells read the PANEL's own stored
``gross_390`` / ``exit_et_390`` / ``exit_status_390`` labels (entry = the open of the
bar printed at minute ``t``, no bar at ``t`` => ``unfilled_expired`` cash; exit = the
first actual bar open at/after ``min(t + 390, session_end)`` = the ticker's last actual
bar of the session) - an accepted basis for the undelayed entry, stated here. The d=2
cells recompute from the day's own SIP net minute bars
(``data/sip/net/bars/<day>.parquet``, columns ticker/et/open) because the delay moves
the entry minute: entry = the first actual bar open at/after ``t + 2`` at or before
``session_end`` (``filled_proxy`` at exactly ``t + 2``, else ``filled_next_open``,
recorded per row), exit = the same session-close bar; a ticker whose tape stream is
absent that day is ``unknown_pending`` (never cash, never a guessed price). The
row-by-row gap between the two bases is quantified in ``coverage.d0_vs_panel`` and in
the gap tables instead of being silently absorbed, and the d=0 once cells must reproduce
the stored ``learned/surface_validation.json`` h390 surface cells field-for-field at the
100 bps reproduction rung (a hard gate on the full validation block).

ACCOUNT CONVENTIONS are the reference lane's, unchanged: $3,000 research reserve
(3 slots x $1,000 tickets), no leverage, one position per ticker, fees on BOTH legs,
same-clock exits before buys, simultaneous intents funded by DESCENDING SCORE before any
fill outcome, unfilled cash retained (the attempt still counts, no fee, no cooldown), an
UNKNOWN exit holds its cash and its slot to the session end and is charged one full
ticket in a SEPARATELY LABELED full-loss lower bound (never cash, never an EV), a flat
15-minute cooldown after the ACTUAL exit, max 3 attempts per ticker per day on the
repeat cadence (1 on the once control). Because every exit is the session close, a
filled ticker cannot re-enter within the session: the repeat cadence differs from once
ONLY by re-attempts after an UNFILLED minute (or a genuine early print on an incomplete
stream) - the grid reports that difference as it is, in attempts and in dollars.

THE FROZEN REFERENCE ROW: the retained lead ``repeat_h60`` of
factory/scripts/alpha_sparse_daily.py (the frozen h60 lane at threshold 0.030, exit_h60,
repeat cadence, same $3,000 book and the same conventions) is pinned as an external
comparison row on the identical basis: VALIDATION 67 known fills +4.8777 $/day @25 bps
on the $3,000 book (250 days, 55 traded); LATE 157 known fills +9.2806 $/day @25 bps
(332 days, 124 traded). Every cell is reported beside that row with its explicit delta;
the row is a comparison object, never a gate and never a re-fit target.

COSTS are a COMPARISON SET, never a hurdle: 25/50/75/100/125/150 bps TOTAL round-trip
modeled friction on the minute-open proxy prices, with 200 bps kept only as the
historical diagnostic the stored late ladder already carried. No rung closes a candidate
and no rung falsifies anything. Returns are a RESEARCH NORMALIZATION on a daily-reset
$3,000 reserve, annualized by the simple 252-session convention - NOT a CAGR - with an
additive fixed-ticket carry-equity check beside it.

SELECTION (pre-declared): rank the 12 fixed cells by 2023-validation KNOWN-contribution
dollars per full calendar day at the 25 bps rung; ties break on more known fills, then
fewer attempts, then the cell key. The contract and the choice are frozen to
``contract.json`` and ``selection.json`` BEFORE any 2025-02..2026-05 outcome is
computed; that late block was already explored by earlier producers, so its traversal is
transparency only with the choice immutable, NOT a pristine holdout.

NOTES. (1) A whole-day hold carries intraday risk the minute-open proxy understates: the
proxy prices only the entry print and the session-close print, so it cannot see the
intraday path of a position held to the close (drawdowns, halts, adverse gaps, or
intraday recoveries). (2) This is a NEW formulation - the stored h390 head held to the
session close under a threshold x entry-delay x cadence grid - not a resurrection of any
closed study: no closed cell, threshold or outcome is re-pinned, re-admitted or
relabelled; nothing here is a live order, a fill guarantee or a capacity claim. No
network access: the producer reads only local panel, tape and artifact files.

Outputs -> ``~/alpha-data/open-search-v1/retained_h390_lane``: ``contract.json`` (the
full 12-cell grid, the cost ladder and the selection rule, written before any validation
score), ``selection.json`` (the frozen validation choice, written before any late
outcome), ``results.json`` (surfaces, gap tables, gate, reference comparison, decision),
``collect_parts/<day>.parquet`` + ``collect_parts/<day>.cov.json`` (per-day atomic parts
with resume hashes), ``daily_known_validation.npz`` / ``daily_known_late.npz`` (per-cell
per-day known contribution), ``producer_snapshot.py``. Protected days (2024, 2025-01,
2026-06..08) are never read; ``allowed(day)`` guards every day before any file is opened.

Usage:
  uv run --no-sync python factory/scripts/alpha_retained_h390_lane.py
      # outputs -> ~/alpha-data/open-search-v1/retained_h390_lane
  uv run --no-sync python factory/scripts/alpha_retained_h390_lane.py --resume
      # top up from the per-day parts already on disk
  uv run --no-sync python factory/scripts/alpha_retained_h390_lane.py --skip-late
      # contract + validation replay + frozen selection only (no late traversal)
  uv run --no-sync python factory/scripts/alpha_retained_h390_lane.py --out <dir>
      # relocate the lane root
  uv run --no-sync python factory/scripts/alpha_retained_h390_lane.py --days 2023-05-15 2023-05-16
      # restrict the analysis corpus (debug)
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import os
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
    sha256_file,
)
from alpha_open_panel import ROOT, allowed
from alpha_open_sim import causal_liquidity, period
from alpha_sparse_daily import day_context

# ----- fixed configuration (no HPO, no refit) --------------------------------
STUDY = "alpha_retained_h390_lane"
STATUS = "DISCOVERY-NOT-VALIDATED"
MODEL_HORIZON = 390  # the one stored head that is ever loaded
MODEL_NAME = "payoff_h390.joblib"
# Pinned digest of the ONE immutable stored model: verified at load, never refit.
EXPECTED_MODEL_SHA256 = "90c796db9fd6880aa8ae85abd676f93857015c186d3e1155a1434605c82344c7"
THRESHOLDS = (0.020, 0.030, 0.050)  # admission bars on the SAME score
MIN_THRESHOLD = min(THRESHOLDS)  # collection keeps every state any cell could admit
DELAYS = (0, 2)  # minutes added to the state minute t; d=0 is the frozen entry
COOLDOWN_MIN = 15  # flat minutes after the ACTUAL exit
ATTEMPTS_ONCE = 1  # the once-per-day control cadence
ATTEMPTS_REPEAT = 3  # attempts per ticker per session (the retained repeat form)
MAX_POSITIONS = 3  # one position per symbol, at most three at once
ORDER_BUDGET = 1000.0  # research ticket size (book only, not capacity)
RESERVE_USD = MAX_POSITIONS * ORDER_BUDGET
SELECT_COST_BPS = 25.0  # the pre-declared 2023 validation selection rung
REPRO_COST_BPS = 100.0  # the rung the stored baseline surface cells were computed at
COST_SCENARIOS_BPS = (25.0, 50.0, 75.0, 100.0, 125.0, 150.0)
# Reported cost scenarios in bps (TOTAL round-trip modeled friction on the minute-open
# proxy prices). The ladder is a COMPARISON SET, never a hurdle: no scenario closes a
# candidate and no rung falsifies anything. 25..150 bps describe a LOW-FEE OPPORTUNITY
# set, not a fee claim - a primary US broker's regular schedule (zero commission, SEC
# 20.6 per $1M, CAT 0.000003 per side, daily cent rounding) is typically well under 1 bp
# on a $1,000 ticket, so the actual broker fee is sourced separately, not here.
HISTORICAL_COST_BPS = 200.0  # historical diagnostic only, never a modern fee claim
LADDER = COST_SCENARIOS_BPS + (HISTORICAL_COST_BPS,)
TRADING_DAYS_PER_YEAR = 252  # session-count convention for $/year, NOT a CAGR
BARS_ROOT = ROOT / "data" / "sip" / "net" / "bars"
OUTPUT = PANEL_ROOT / "retained_h390_lane"
EXPECTED_DAYS = {"train": 484, VAL_PERIOD: 250, CONF_PERIOD: 332}
ANALYSIS_PERIODS = (VAL_PERIOD, CONF_PERIOD)
FLOAT_TOL = 1e-9
SCHEMA = "retained_h390_lane_parts_v1"  # pinned by the resume hash
PROGRESS_EVERY = 25  # collection progress cadence (days)
COST_SCENARIO_NATURE = (
    "each rung is a TOTAL modeled minute-proxy friction scenario on the proxy prices, "
    "NOT an actual broker fee: a primary US broker's regular schedule (zero commission, "
    "SEC 20.6 per $1M, CAT 0.000003 per side, daily cent rounding) is typically well "
    "under 1 bp on a $1,000 ticket, so the low rungs describe a LOW-FEE OPPORTUNITY set, "
    "not a fee claim; actual provider fees are sourced in the separate provider-fee ledger"
)
LABEL_RULES = {
    "exit": (
        "hold to the session close: the exit is the ticker's LAST actual bar open at or "
        "before session_end (session_end = 959 on every panel day, so for a complete "
        "stream the exit open is the session-close print's open)"
    ),
    "delay_changes_only_the_entry_minute": (
        "every cell holds to the same session-close bar; the entry-delay axis moves only "
        "the entry minute (t or t + 2) and therefore only the entry price"
    ),
    "entry_minute": (
        "t + delay with delay in {0, 2}; d=0 is the state minute t itself, the same "
        "entry minute the frozen baseline uses"
    ),
    "entry_price": (
        "the open of the bar at the entry minute when present, else the NEXT available "
        "bar open at or before session_end (filled-open rule, recorded as "
        "filled_next_open), else unfilled cash; never a guessed or interpolated price"
    ),
    "entry_statuses": {
        "filled_proxy": "a bar printed exactly at the entry minute",
        "filled_next_open": (
            "no bar at the entry minute; the NEXT actual bar open at or before "
            "session_end is used and recorded (tape gap, not a guess)"
        ),
        "unfilled_cash": (
            "no bar from the entry minute through session_end: no position, cash "
            "retained, the attempt still counts, no cooldown, no fee"
        ),
        "unknown_pending": (
            "the ticker has no tape stream that day, or the print is nonfinite or "
            "nonpositive: never cash, never a guessed price"
        ),
    },
    "d0_basis": (
        "the d=0 cells read the PANEL's own stored h390 labels: entry = the open of the "
        "bar printed at minute t (panel entry_status filled_proxy; no bar at t => "
        "unfilled_expired cash), exit = the first actual bar open at/after "
        "min(t + 390, session_end) = the ticker's last actual bar of the session "
        "(because t + 390 > session_end for every state minute on this panel). "
        "gross_390 / exit_et_390 / exit_status_390 are carried unchanged"
    ),
    "d2_basis": (
        "the d=2 cells recompute from the day's own SIP net minute bars "
        "(data/sip/net/bars/<day>.parquet, columns ticker/et/open) because the delay "
        "moves the entry minute to t + 2; the exit is the same session-close bar as d=0"
    ),
    "basis_parquet": "data/sip/net/bars/<day>.parquet, columns ticker/et/open",
    "gap_quantification": (
        "the row-by-row gap between the stored-label basis and the tape-recomputed "
        "basis is quantified in coverage.d0_vs_panel and in the gap tables; it is never "
        "silently absorbed, and the stored-surface reproduction gate pins the d=0 once "
        "cells to learned/surface_validation.json"
    ),
    "exit_unknown": "the ticker's tape stream is absent that day (never silently dropped)",
    "basis": "the day's SIP net minute bars - the same tape the panel itself was built from",
    "proxy_warning": (
        "EVERY fill is a minute-open proxy; none of these are actual quotes or actual "
        "exchange fills"
    ),
}
DISCLOSURES = {
    "execution": ("minute-open proxy fills only; never actual quotes, never actual exchange fills"),
    "capacity": (
        "$1,000 tickets on a $3,000 research reserve; book size, NOT a scalability "
        "claim, scaling is not assumed linear"
    ),
    "late_block": (
        "2025-02..2026-05 was already explored before this study, so its outcomes are "
        "DISCOVERY-NOT-VALIDATED - NOT pristine and NOT previously unknown"
    ),
    "costs": (
        "25..150 bps are a comparison set, never a hurdle; 200 bps is the historical "
        "diagnostic only; actual provider fees are typically well under 1 bp on these "
        "tickets and are sourced separately"
    ),
    "annualization": (
        "simple 252-session convention on a daily-reset research reserve, NOT a CAGR; "
        "the additive carry-equity check is beside it"
    ),
    "lower_bound": (
        "the full-loss lower bound over UNKNOWN exits is separately labeled, never "
        "blended into known contribution, and never called an EV"
    ),
    "no_gates": (
        "no count floor, no median gate, no tail gate, no CI gate: every cell is "
        "reported at every rung with its full sample beside it"
    ),
    "whole_day_hold_risk": (
        "a whole-day hold carries intraday risk the minute-open proxy understates: only "
        "the entry print and the session-close print are priced, so the intraday path "
        "(drawdowns, halts, adverse gaps, or intraday recoveries) of a position held to "
        "the close is invisible to this proxy"
    ),
    "new_formulation": (
        "this is a NEW formulation - the stored h390 head held to the session close "
        "under a threshold x entry-delay x cadence grid - not a resurrection of any "
        "closed study: no closed cell, threshold or outcome is re-pinned, re-admitted or "
        "relabelled; the stored h390 once-per-day surface row is replayed only as the "
        "stated-basis d=0 reference and the frozen repeat_h60 lane is an external "
        "comparison row"
    ),
    "no_network": (
        "the producer opens no network connection; it reads only local panel day files, "
        "local SIP net minute bars and local stored artifacts"
    ),
}
CONTRACT_PAYLOAD = {
    "study": STUDY,
    "version": 1,
    "status": STATUS,
    "model": {
        "path": f"learned/models/{MODEL_NAME}",
        "horizon": MODEL_HORIZON,
        "refit": False,
        "hpo": False,
        "expected_sha256": EXPECTED_MODEL_SHA256,
    },
    "thresholds": [float(t) for t in THRESHOLDS],
    "delays": [int(d) for d in DELAYS],
    "cadences": {
        "once": {
            "max_attempts": ATTEMPTS_ONCE,
            "cooldown_min": COOLDOWN_MIN,
            "control": True,
        },
        "repeat": {
            "max_attempts": ATTEMPTS_REPEAT,
            "cooldown_min": COOLDOWN_MIN,
            "control": False,
        },
    },
    "exit_rule": LABEL_RULES["exit"],
    "book": {
        "max_positions": MAX_POSITIONS,
        "order_budget_usd": ORDER_BUDGET,
        "reserve_usd": RESERVE_USD,
        "leverage": False,
        "one_position_per_symbol": True,
        "fees_both_legs": True,
        "same_clock_exits_before_buys": True,
        "simultaneous_intents_funded_before_fill_outcome": True,
    },
    "cost_scenarios_bps": [float(c) for c in COST_SCENARIOS_BPS],
    "historical_diagnostic_cost_bps": HISTORICAL_COST_BPS,
    "cost_ladder_is_comparison_not_hurdle": True,
    "selection_cost_bps": SELECT_COST_BPS,
    "selection_rule": {
        "objective": (
            "2023 validation KNOWN-contribution dollars per full calendar day "
            "(summed realized net of known fills / every calendar day of the block)"
        ),
        "tie_break": [
            "known_contribution_usd_per_day desc",
            "known_fills desc",
            "attempts asc",
            "cell_key asc",
        ],
    },
    "reproduction_cost_bps": REPRO_COST_BPS,
    "label_rules": LABEL_RULES,
    "periods": {
        "development_fit_never_replayed": "2021-02..2022-12 (weights frozen, never scored)",
        VAL_PERIOD: "2023-01-01..2023-12-31 (the only selection block)",
        CONF_PERIOD: "2025-02-01..2026-05-31 (previously explored, transparency only)",
    },
    "label_schema": SCHEMA,
    "panel_root": str(PANEL_ROOT),
    "bars_root": str(BARS_ROOT),
}


def _digest_bytes(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _producer_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _default(obj):
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return str(obj)


def _json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=1, default=_default) + "\n")
    os.replace(tmp, path)


def _fmt(value, spec="+.6f"):
    return "n/a" if value is None else format(value, spec)


def _ref_ladder(days: int, rows: tuple[tuple[float, ...], ...]) -> dict:
    """One block's frozen repeat_h60 reference ladder, as published by the parent lane."""
    ladder: dict[str, dict] = {}
    for row in rows:
        (
            rung,
            attempts,
            fills,
            known,
            unknown,
            traded,
            dpd,
            mnet,
            win,
            pf,
            worst,
            pos,
        ) = row
        ladder[str(int(rung))] = {
            "days": days,
            "attempts": int(attempts),
            "fills": int(fills),
            "known_fills": int(known),
            "unknown_fills": int(unknown),
            "traded_days": int(traded),
            "dollars_per_day": dpd,
            "dollars_per_year_252": dpd * TRADING_DAYS_PER_YEAR,
            "mean_net_known_fill": mnet,
            "known_win_rate": win,
            "known_profit_factor": pf,
            "worst_known_fill": worst,
            "positive_months_lower_bound": int(pos),
        }
    return ladder


# The frozen repeat_h60 reference row: validation.repeat_h60 and
# confirmation_by_cost.repeat_h60 of the parent's alpha_sparse_daily run (the retained
# lead lane), pinned at full precision so the comparison is byte-stable and the artifact
# path is recorded. 0 UNKNOWN fills on both blocks, so its published lower-bound
# dollars/day equals its known contribution on the identical basis. Per rung: (rung_bps,
# attempts, fills, known_fills, unknown_fills, traded_days, dollars_per_day,
# mean_net_known_fill, known_win_rate, known_profit_factor, worst_known_fill,
# positive_months_lower_bound).
_REF_VAL_ROWS = (
    (
        25,
        68,
        67,
        67,
        0,
        55,
        4.877666020694502,
        0.018200246345875006,
        0.44776119402985076,
        1.3800468896644766,
        -0.3134593903369777,
        4,
    ),
    (
        50,
        68,
        67,
        67,
        0,
        55,
        4.196321407207728,
        0.0156579156985363,
        0.44776119402985076,
        1.3185819876252889,
        -0.3151736044504124,
        4,
    ),
    (
        75,
        68,
        67,
        67,
        0,
        55,
        3.5166737915129054,
        0.013121917132510838,
        0.43283582089552236,
        1.260147351348617,
        -0.31688354903928084,
        4,
    ),
    (
        100,
        68,
        67,
        67,
        0,
        55,
        2.8387168415288033,
        0.010592227020629865,
        0.43283582089552236,
        1.204757685467114,
        -0.3185892400346446,
        4,
    ),
    (
        125,
        68,
        67,
        67,
        0,
        55,
        2.16244425663777,
        0.008068821853126008,
        0.417910447761194,
        1.1521458659043555,
        -0.3202906932884051,
        4,
    ),
    (
        150,
        68,
        67,
        67,
        0,
        55,
        1.4878497674909514,
        0.0055516782369065334,
        0.40298507462686567,
        1.1020753882423475,
        -0.32198792457379377,
        4,
    ),
    (
        200,
        68,
        67,
        67,
        0,
        55,
        0.14367015421568652,
        0.0005360826649839037,
        0.3880597014925373,
        1.0093870134204121,
        -0.32536978394195704,
        4,
    ),
)
_REF_LATE_ROWS = (
    (
        25,
        157,
        157,
        157,
        0,
        124,
        9.28057120978782,
        0.01962515695318189,
        0.40764331210191085,
        1.2767123894575394,
        -0.584192455169393,
        11,
    ),
    (
        50,
        157,
        157,
        157,
        0,
        124,
        8.076642015315093,
        0.017079268465507072,
        0.40764331210191085,
        1.2364745791380205,
        -0.5852306794955129,
        11,
    ),
    (
        75,
        157,
        157,
        157,
        0,
        124,
        6.875711399159793,
        0.014539720920516253,
        0.40764331210191085,
        1.1977567432498946,
        -0.5862663179578068,
        11,
    ),
    (
        100,
        157,
        157,
        157,
        0,
        124,
        5.677768172596966,
        0.012006490657975754,
        0.40764331210191085,
        1.160474353266842,
        -0.5872993802050204,
        11,
    ),
    (
        125,
        157,
        157,
        157,
        0,
        124,
        4.482801202497562,
        0.00947955413521777,
        0.4012738853503185,
        1.1245353364549563,
        -0.5883298758379553,
        10,
    ),
    (
        150,
        157,
        157,
        157,
        0,
        124,
        3.2907994109841003,
        0.006958887926412239,
        0.4012738853503185,
        1.0898798492164545,
        -0.5893578144097662,
        10,
    ),
    (
        200,
        157,
        157,
        157,
        0,
        124,
        0.9156473264089741,
        0.0019362733271833105,
        0.3885350318471338,
        1.0241909467421524,
        -0.5914060583461715,
        8,
    ),
)
REFERENCE_REPEAT_H60 = {
    "source": (
        "~/alpha-data/open-search-v1/learned_sparse_daily/results.json "
        "(validation.repeat_h60 and confirmation_by_cost.repeat_h60; the parent's "
        "alpha_sparse_daily run of the frozen h60 lane)"
    ),
    "lane": (
        "repeat_h60: the one immutable stored h60 model at threshold 0.030, exit_h60, "
        "the retained repeat cadence (flat 15-minute cooldown after the ACTUAL exit, "
        "max 3 attempts per ticker per session), same $3,000 reserve and same "
        "funded-reserve conventions as this study"
    ),
    "basis": (
        "identical basis: same $3,000 reserve (3 x $1,000 tickets), one position per "
        "symbol, fees on both legs, same-clock exits before buys, unfilled cash "
        "retained, UNKNOWN holds cash and slot to session end, minute-open proxy, same "
        "full calendar-day denominators; the reference lane recorded 0 UNKNOWN fills on "
        "both blocks, so its published lower-bound dollars/day equals its known "
        "contribution"
    ),
    VAL_PERIOD: _ref_ladder(250, _REF_VAL_ROWS),
    CONF_PERIOD: _ref_ladder(332, _REF_LATE_ROWS),
}

# Stored surface cells this study hard-gates against: the once cadence on the PANEL's own
# stored h390 labels, i.e. this study's d=0 once cells at thresholds 0.030 and 0.050, as
# published by alpha_open_learned in learned/surface_validation.json at the 100 bps
# reproduction rung. READ-ONLY artifact; never recomputed, refit or re-estimated here.
EXPECTED_STORED_SURFACE = {
    "source": (
        "~/alpha-data/open-search-v1/learned/surface_validation.json ['390|0.03'] and "
        "['390|0.05'] (alpha_open_learned stored validation surface for the h390 head, "
        "once cadence, minute-open proxy, 100 bps)"
    ),
    VAL_PERIOD: {
        "once_d0_thr030": {
            "days": 250,
            "attempts": 184,
            "fills": 179,
            "known_fills": 176,
            "unknown_fills": 3,
            "traded_days": 113,
            "n_signals": 1588,
            "cash_or_slot_skips": 44,
            "mean_daily_lower_bound": -0.010789701996598773,
            "mean_net_known_fill": -0.028933389190051586,
        },
        "once_d0_thr050": {
            "days": 250,
            "attempts": 76,
            "fills": 73,
            "known_fills": 72,
            "unknown_fills": 1,
            "traded_days": 57,
            "n_signals": 548,
            "cash_or_slot_skips": 0,
            "mean_daily_lower_bound": -0.006815248242868245,
            "mean_net_known_fill": -0.057103280307655324,
        },
    },
}


# ----- the fixed grid: 3 thresholds x 2 entry delays x 2 cadences -------------
@dataclass(frozen=True)
class Cadence:
    """One admission cadence. Same score, same threshold, same exit; only cadence differs."""

    name: str
    max_attempts: int
    cooldown_min: int
    control: bool = False


CADENCES = (
    Cadence("once", ATTEMPTS_ONCE, COOLDOWN_MIN, control=True),
    Cadence("repeat", ATTEMPTS_REPEAT, COOLDOWN_MIN),
)


@dataclass(frozen=True)
class LabelView:
    """Where one replay reads its entry/exit labels from."""

    gross: str  # realized gross payoff column
    exit_et: str  # exit minute column
    status: str  # entry status column
    open: str  # entry open column
    hold_minute: str  # the minute the position was actually held from
    unfilled: str  # the status value that means "no fill, cash"
    entry_et: str | None = None  # actual entry minute column (None => the state minute t)
    basis: str = ""


# The d=0 basis: the PANEL's own stored h390 labels, carried unchanged. The panel's h390
# exit is the first actual bar open at/after min(t + 390, session_end) = the ticker's
# last actual bar of the session (t + 390 > session_end for every state minute on this
# panel), so the stored label already holds to the session close.
STORED_VIEW = LabelView(
    gross="panel_gross_390",
    exit_et="panel_exit_et_390",
    status="panel_entry_status",
    open="panel_entry_open",
    hold_minute="panel_entry_et",
    unfilled="unfilled_expired",
    entry_et=None,
    basis=LABEL_RULES["d0_basis"],
)


def close_view(delay: int) -> LabelView:
    """Labels recomputed from the day's tape for one entry delay, held to the session close."""
    return LabelView(
        gross=f"gross_{delay}",
        exit_et=f"exit_et_{delay}",
        status=f"entry_status_{delay}",
        open=f"entry_open_{delay}",
        hold_minute=f"entry_et_{delay}",
        unfilled="unfilled_cash",
        entry_et=f"entry_et_{delay}",
        basis=LABEL_RULES["d2_basis"],
    )


@dataclass(frozen=True)
class Cell:
    """One fixed grid cell: a cadence at one entry delay and one admission threshold.

    Same stored score, same session-close exit and same funded reserve for every cell;
    only the admission bar, the entry delay and the cadence differ.
    """

    key: str
    cadence: str
    max_attempts: int
    cooldown_min: int
    delay: int
    threshold: float
    labels: LabelView
    control: bool = False

    @property
    def cadence_str(self) -> str:
        return (
            f"thr{self.threshold:.3f}|exit_session_close|entry_d{self.delay}|"
            f"cadence_{self.cadence}|flat_cooldown_{self.cooldown_min}min|"
            f"max_attempts_{self.max_attempts}"
        )


def cell_key(cadence: str, delay: int, threshold: float) -> str:
    return f"{cadence}_d{delay}_thr{int(round(threshold * 1000)):03d}"


CELLS = tuple(
    Cell(
        key=cell_key(c.name, d, t),
        cadence=c.name,
        max_attempts=c.max_attempts,
        cooldown_min=c.cooldown_min,
        delay=int(d),
        threshold=float(t),
        labels=STORED_VIEW if d == 0 else close_view(d),
        control=(c.name == "once" and d == 0),
    )
    for c in CADENCES
    for t in THRESHOLDS
    for d in DELAYS
)

# The diagnostic gap family: the same d=0 cells recomputed from the tape, so the
# stored-label basis and the tape-recomputed basis are compared rung by rung instead of
# the wedge being silently absorbed. Never part of the grid and never selectable.
GAP_CELLS = tuple(
    Cell(
        key=f"recomputed_{cell_key(c.name, 0, t)}",
        cadence=c.name,
        max_attempts=c.max_attempts,
        cooldown_min=c.cooldown_min,
        delay=0,
        threshold=float(t),
        labels=close_view(0),
        control=False,
    )
    for c in CADENCES
    for t in THRESHOLDS
)

# ----- columns of one per-day signal part ------------------------------------
SIGNAL_BASE = ["day", "ticker", "t", "session_end", "score"]
PANEL_SELECT = [
    pl.col("entry_status").alias("panel_entry_status"),
    pl.col("entry_open").alias("panel_entry_open"),
    pl.col("entry_et").alias("panel_entry_et"),
    pl.col("gross_390").alias("panel_gross_390"),
    pl.col("exit_et_390").alias("panel_exit_et_390"),
    pl.col("exit_status_390").alias("panel_exit_status_390"),
]
PANEL_KEEP = [e.meta.output_name() for e in PANEL_SELECT]
CLOSE_COLUMNS = [
    f"{kind}_{d}"
    for d in DELAYS
    for kind in ("entry_et", "entry_open", "entry_status", "gross", "exit_et", "exit_status")
]
PART_COLUMNS = SIGNAL_BASE + PANEL_KEEP + CLOSE_COLUMNS
PART_SCHEMA = {
    "day": pl.String,
    "ticker": pl.String,
    "t": pl.Int64,
    "session_end": pl.Int64,
    "score": pl.Float64,
    "panel_entry_status": pl.String,
    "panel_entry_open": pl.Float64,
    "panel_entry_et": pl.Int64,
    "panel_gross_390": pl.Float64,
    "panel_exit_et_390": pl.Int64,
    "panel_exit_status_390": pl.String,
    **{f"entry_et_{d}": pl.Int64 for d in DELAYS},
    **{f"entry_open_{d}": pl.Float64 for d in DELAYS},
    **{f"entry_status_{d}": pl.String for d in DELAYS},
    **{f"gross_{d}": pl.Float64 for d in DELAYS},
    **{f"exit_et_{d}": pl.Int64 for d in DELAYS},
    **{f"exit_status_{d}": pl.String for d in DELAYS},
}


def _empty_part_frame() -> pl.DataFrame:
    return pl.DataFrame(schema={k: PART_SCHEMA[k] for k in PART_COLUMNS})


# ----- labels recomputed from the day's own minute bars ------------------------
def compute_close_labels(rows: list[dict], tapes: dict) -> tuple[dict[str, list], dict]:
    """One day's hold-to-session-close labels for BOTH entry delays, read from the tape only.

    Per delay ``d``: entry minute ``t + d``; the first actual bar open at/after it, at or
    before ``session_end``, is the entry price (``filled_proxy`` when it is exactly the
    entry minute, ``filled_next_open`` when a tape gap pushed the fill to the next print,
    recorded); ``unfilled_cash`` when no bar prints from the entry minute through
    session_end; ``unknown_pending`` when the ticker has no tape stream that day or the
    print is nonfinite/nonpositive (never cash, never a guess). Exit = the LAST actual
    bar open at or before ``session_end`` - the session close - for EVERY delay: the exit
    print does not depend on the delay, only the entry price does. ``et`` must be the
    tape's own ascending minute stamps. The d=0 recomputed labels are also compared
    against the panel's own stored h390 labels so the filled-open and exit gap rules are
    quantified row by row, never silently absorbed.
    """
    out: dict[str, list] = {}
    for d in DELAYS:
        for kind in ("entry_et", "entry_open", "entry_status", "gross", "exit_et", "exit_status"):
            out[f"{kind}_{d}"] = []
    entry_counts: dict[str, Counter] = {str(d): Counter() for d in DELAYS}
    exit_counts: dict[str, Counter] = {str(d): Counter() for d in DELAYS}
    d0 = {
        "entry_transitions": Counter(),
        "exit_et_changed": 0,
        "exit_status_changed": 0,
        "gross_changed": 0,
        "gross_max_abs_delta": 0.0,
        "unfilled_to_filled": 0,
        "unfilled_stayed_cash": 0,
    }

    def append_unknown(d: int, reason: str) -> None:
        out[f"entry_et_{d}"].append(None)
        out[f"entry_open_{d}"].append(None)
        out[f"entry_status_{d}"].append("unknown_pending")
        entry_counts[str(d)][reason] += 1
        out[f"gross_{d}"].append(None)
        out[f"exit_et_{d}"].append(None)
        out[f"exit_status_{d}"].append("unknown_pending")
        exit_counts[str(d)]["unknown_pending"] += 1

    def append_unfilled(d: int) -> None:
        out[f"entry_et_{d}"].append(None)
        out[f"entry_open_{d}"].append(None)
        out[f"entry_status_{d}"].append("unfilled_cash")
        entry_counts[str(d)]["unfilled_cash"] += 1
        out[f"gross_{d}"].append(None)
        out[f"exit_et_{d}"].append(None)
        out[f"exit_status_{d}"].append("unfilled_cash")
        exit_counts[str(d)]["unfilled_cash"] += 1

    for r in rows:
        ticker = r["ticker"]
        tape = tapes.get(ticker)
        if tape is None:
            # No tape stream for this ticker on this day: unknowable, never a guess.
            for d in DELAYS:
                append_unknown(d, "unknown_pending")
                if d == 0:
                    d0["entry_transitions"][f"{r['panel_entry_status']}->unknown_pending"] += 1
            continue
        et, opens = tape
        session_end = int(r["session_end"])
        # The session-close print: the last actual bar open at or before session_end.
        ex = int(np.searchsorted(et, session_end, side="right")) - 1
        exit_open = float(opens[ex]) if ex >= 0 else None
        exit_et = int(et[ex]) if ex >= 0 else None
        exit_usable = exit_open is not None and np.isfinite(exit_open) and exit_open > 0
        for d in DELAYS:
            target = int(r["t"]) + d
            j = int(np.searchsorted(et, target, side="left"))
            if j >= len(et) or int(et[j]) > session_end:
                append_unfilled(d)
                if d == 0:
                    d0["entry_transitions"][f"{r['panel_entry_status']}->unfilled_cash"] += 1
                    if r["panel_entry_status"] == "unfilled_expired":
                        d0["unfilled_stayed_cash"] += 1
                continue
            e_et = int(et[j])
            e_open = float(opens[j])
            if not np.isfinite(e_open) or e_open <= 0:
                # A nonpositive/nonfinite print can never price a position: UNKNOWN.
                append_unknown(d, "unknown_pending_invalid_open")
                if d == 0:
                    d0["entry_transitions"][f"{r['panel_entry_status']}->unknown_pending"] += 1
                continue
            e_status = "filled_proxy" if e_et == target else "filled_next_open"
            out[f"entry_et_{d}"].append(e_et)
            out[f"entry_open_{d}"].append(e_open)
            out[f"entry_status_{d}"].append(e_status)
            entry_counts[str(d)][e_status] += 1
            if not exit_usable:
                out[f"gross_{d}"].append(None)
                out[f"exit_et_{d}"].append(None)
                out[f"exit_status_{d}"].append("unknown_pending")
                exit_counts[str(d)]["unknown_pending"] += 1
            else:
                g = float(exit_open) / e_open - 1.0
                out[f"gross_{d}"].append(g)
                out[f"exit_et_{d}"].append(exit_et)
                out[f"exit_status_{d}"].append("observed_close_proxy")
                exit_counts[str(d)]["observed_close_proxy"] += 1
            if d == 0:
                panel_status = r["panel_entry_status"]
                d0["entry_transitions"][f"{panel_status}->{e_status}"] += 1
                if panel_status == "unfilled_expired":
                    d0["unfilled_to_filled"] += 1
                if r["panel_exit_status_390"] != "observed_open_proxy":
                    d0["exit_status_changed"] += 1
                panel_gross = r["panel_gross_390"]
                recomputed_status = out["exit_status_0"][-1]
                recomputed_gross = out["gross_0"][-1]
                recomputed_known = (
                    recomputed_status == "observed_close_proxy" and recomputed_gross is not None
                )
                if (
                    r["panel_exit_status_390"] == "observed_open_proxy"
                    and panel_gross is not None
                    and recomputed_known
                ):
                    if r["panel_exit_et_390"] is not None and int(r["panel_exit_et_390"]) != int(
                        out["exit_et_0"][-1]
                    ):
                        d0["exit_et_changed"] += 1
                    delta = abs(float(panel_gross) - float(recomputed_gross))
                    if delta > FLOAT_TOL:
                        d0["gross_changed"] += 1
                        d0["gross_max_abs_delta"] = max(d0["gross_max_abs_delta"], float(delta))
    return out, {
        "entry_counts_by_delay": {k: dict(v) for k, v in entry_counts.items()},
        "exit_counts_by_delay": {k: dict(v) for k, v in exit_counts.items()},
        "d0_vs_panel": {
            "entry_transitions": dict(d0["entry_transitions"]),
            "exit_et_changed": d0["exit_et_changed"],
            "exit_status_changed": d0["exit_status_changed"],
            "gross_changed": d0["gross_changed"],
            "gross_max_abs_delta": d0["gross_max_abs_delta"],
            "unfilled_to_filled": d0["unfilled_to_filled"],
            "unfilled_stayed_cash": d0["unfilled_stayed_cash"],
        },
    }


# ----- stored model (loaded, never fitted) ------------------------------------
def load_stored_model(model_dir: Path) -> tuple[dict, Path]:
    """Load the ONE immutable stored h390 head; verify its digest and frozen feature order."""
    path = model_dir / MODEL_NAME
    if not path.exists():
        raise SystemExit(f"[{STUDY}] stored model missing: {path}")
    digest = sha256_file(path)
    if digest != EXPECTED_MODEL_SHA256:
        raise SystemExit(
            f"[{STUDY}] stored model sha256 {digest} != pinned {EXPECTED_MODEL_SHA256}"
        )
    bundle = joblib.load(path)
    order = list(bundle.get("feature_order") or [])
    if order != list(FEATURES_ALL):
        raise SystemExit(f"[{STUDY}] stored feature_order != current FEATURES_ALL")
    if int(bundle.get("horizon", -1)) != MODEL_HORIZON:
        raise SystemExit(f"[{STUDY}] stored horizon {bundle.get('horizon')} != {MODEL_HORIZON}")
    return bundle, path


# ----- per-day tape opens ------------------------------------------------------
def load_day_opens(day: str, tickers: list[str]) -> tuple[dict, bool]:
    """One day's tape minute opens per ticker; never more than one day resident."""
    path = BARS_ROOT / f"{day}.parquet"
    if not path.exists():
        return {}, False
    frame = (
        pl.read_parquet(path).filter(pl.col("ticker").is_in(tickers)).select("ticker", "et", "open")
    )
    if frame.height and frame.select(pl.struct("ticker", "et").is_duplicated().any()).item():
        raise ValueError(f"duplicate tape bars: {day}")
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    if frame.height:
        for key, part in frame.sort("et").partition_by("ticker", as_dict=True).items():
            out[key[0]] = (
                part["et"].to_numpy().astype(np.int64),
                part["open"].to_numpy().astype(np.float64),
            )
    return out, True


# ----- memory-bounded per-day collection with atomic resume -------------------
def resume_hash(day: str, producer_sha: str, contract_sha: str, model_sha: str) -> str:
    """Identity of one per-day part: producer, declared contract, model, day and schema."""
    payload = "|".join([SCHEMA, producer_sha, contract_sha, model_sha, day])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def part_intact(day: str, parts_dir: Path, expected: str) -> bool:
    """A cached day part counts as intact only if its hash and its columns still match."""
    parquet = parts_dir / f"{day}.parquet"
    cov = parts_dir / f"{day}.cov.json"
    if not parquet.exists() or not cov.exists():
        return False
    try:
        meta = json.loads(cov.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if meta.get("resume_hash") != expected:
        return False
    try:
        cols = set(pl.read_parquet_schema(parquet).keys())
    except Exception:
        return False
    return cols == set(PART_COLUMNS)


def collect_day(
    day: str,
    model,
    parts_dir: Path,
    producer_sha: str,
    contract_sha: str,
    model_sha: str,
) -> dict:
    """Score ONE panel day and keep every state any grid cell could admit.

    The protected guard runs BEFORE any file read. One day frame, one tape frame and one
    day's signal part are resident at a time; the part is written atomically (parquet +
    coverage json) so an interrupted run resumes exactly where it stopped.
    """
    if not allowed(day):
        raise ValueError(f"protected/out-of-scope day refused: {day}")
    frame = pl.read_parquet(PANEL_ROOT / "days" / f"{day}.parquet")
    frame = day_context(frame)
    cand = frame.filter(causal_liquidity(frame))
    rows_scored = int(cand.height)
    if rows_scored:
        pred = np.asarray(model.predict(feature_matrix(cand)), dtype=float)
        sig = cand.with_columns(pl.Series("score", pred)).filter(pl.col("score") >= MIN_THRESHOLD)
        del cand
    else:
        sig = _empty_part_frame()
    del frame
    rows_kept = int(sig.height)
    if not rows_kept:
        # No state cleared the lowest admission bar: a cash day, still written with the
        # full part schema so the block frames always concat cleanly.
        sig = _empty_part_frame()
        info = {
            "day_file_present": (BARS_ROOT / f"{day}.parquet").exists(),
            "missing_symbol_streams": [],
            "entry_counts_by_delay": {},
            "exit_counts_by_delay": {},
            "d0_vs_panel": {
                "entry_transitions": {},
                "exit_et_changed": 0,
                "exit_status_changed": 0,
                "gross_changed": 0,
                "gross_max_abs_delta": 0.0,
                "unfilled_to_filled": 0,
                "unfilled_stayed_cash": 0,
            },
        }
    else:
        sig = sig.select(SIGNAL_BASE + PANEL_SELECT)
        tickers = sorted(set(sig["ticker"].to_list()))
        tapes, present = load_day_opens(day, tickers)
        missing = sorted({t for t in sig["ticker"].to_list() if t not in tapes})
        rows = sig.to_dicts()
        out, info = compute_close_labels(rows, tapes)
        info["day_file_present"] = present
        info["missing_symbol_streams"] = missing
        series: list[pl.Series] = []
        for name, values in out.items():
            series.append(pl.Series(name, values, dtype=PART_SCHEMA[name]))
        sig = sig.with_columns(series)
    sig = sig.select(PART_COLUMNS).sort(["ticker", "t"])
    threshold_counts = {
        str(t): int((sig["score"] >= t).sum() or 0) if sig.height else 0 for t in THRESHOLDS
    }
    dest = parts_dir / f"{day}.parquet"
    temp = dest.with_suffix(".parquet.tmp")
    sig.write_parquet(temp)
    temp.replace(dest)
    del sig
    digest = resume_hash(day, producer_sha, contract_sha, model_sha)
    cov = {
        "schema": SCHEMA,
        "day": day,
        "resume_hash": digest,
        "producer_sha256": producer_sha,
        "contract_sha256": contract_sha,
        "model_sha256": model_sha,
        "rows_scored": rows_scored,
        "rows_kept": rows_kept,
        "rows_kept_by_threshold": threshold_counts,
        "day_file_present": bool(info["day_file_present"]),
        "missing_symbol_streams": list(info["missing_symbol_streams"]),
        "entry_counts_by_delay": info["entry_counts_by_delay"],
        "exit_counts_by_delay": info["exit_counts_by_delay"],
        "d0_vs_panel": info["d0_vs_panel"],
        "period": period(day),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    cov_dest = parts_dir / f"{day}.cov.json"
    cov_temp = cov_dest.with_suffix(".json.tmp")
    cov_temp.write_text(json.dumps(cov, indent=2, default=_default) + "\n")
    cov_temp.replace(cov_dest)
    return cov


def collect_parts(
    model,
    days: list[str],
    parts_dir: Path,
    producer_sha: str,
    contract_sha: str,
    model_sha: str,
    resume: bool = False,
) -> dict:
    """Score the corpus once (one predict per day) and persist per-day atomic parts."""
    parts_dir.mkdir(parents=True, exist_ok=True)
    done = set()
    if resume:
        for day in days:
            if part_intact(day, parts_dir, resume_hash(day, producer_sha, contract_sha, model_sha)):
                done.add(day)
    todo = [d for d in days if d not in done]
    coverage = {
        "days_total": len(days),
        "days_cached": len(done),
        "days_collected": len(todo),
        "rows_scored": 0,
        "rows_kept": 0,
        "rows_kept_by_threshold": {str(t): 0 for t in THRESHOLDS},
        "missing_day_files": [],
        "missing_symbol_streams": {},
        "entry_counts_by_delay": {str(d): Counter() for d in DELAYS},
        "exit_counts_by_delay": {str(d): Counter() for d in DELAYS},
        "d0_vs_panel": {
            "entry_transitions": {},
            "exit_et_changed": 0,
            "exit_status_changed": 0,
            "gross_changed": 0,
            "gross_max_abs_delta": 0.0,
            "unfilled_to_filled": 0,
            "unfilled_stayed_cash": 0,
        },
    }
    for n, day in enumerate(todo, 1):
        cov = collect_day(day, model, parts_dir, producer_sha, contract_sha, model_sha)
        _merge_coverage(coverage, cov)
        if n % PROGRESS_EVERY == 0 or n == len(todo):
            print(
                f"[collect] {n}/{len(todo)} days, {coverage['rows_kept']} states "
                f"(last {day}, cached {coverage['days_cached']})",
                flush=True,
            )
    for day in days:
        if day in done:
            cov_path = parts_dir / f"{day}.cov.json"
            cov = json.loads(cov_path.read_text())
            _merge_coverage(coverage, cov)
    coverage["entry_counts_by_delay"] = {
        k: dict(v) for k, v in coverage["entry_counts_by_delay"].items()
    }
    coverage["exit_counts_by_delay"] = {
        k: dict(v) for k, v in coverage["exit_counts_by_delay"].items()
    }
    return coverage


def _merge_coverage(coverage: dict, cov: dict) -> None:
    coverage["rows_scored"] += int(cov.get("rows_scored", 0))
    coverage["rows_kept"] += int(cov.get("rows_kept", 0))
    for key, count in (cov.get("rows_kept_by_threshold") or {}).items():
        coverage["rows_kept_by_threshold"][key] = coverage["rows_kept_by_threshold"].get(
            key, 0
        ) + int(count)
    if cov.get("day_file_present") is False:
        coverage["missing_day_files"].append(cov.get("day", "?"))
    if cov.get("missing_symbol_streams"):
        coverage["missing_symbol_streams"][cov.get("day", "?")] = list(
            cov.get("missing_symbol_streams")
        )
    for d in DELAYS:
        entry = (cov.get("entry_counts_by_delay") or {}).get(str(d), {})
        coverage["entry_counts_by_delay"][str(d)].update(entry)
        exit_ = (cov.get("exit_counts_by_delay") or {}).get(str(d), {})
        coverage["exit_counts_by_delay"][str(d)].update(exit_)
    d0 = cov.get("d0_vs_panel") or {}
    tgt = coverage["d0_vs_panel"]
    for k, v in (d0.get("entry_transitions") or {}).items():
        tgt["entry_transitions"][k] = tgt["entry_transitions"].get(k, 0) + int(v)
    for k in (
        "exit_et_changed",
        "exit_status_changed",
        "gross_changed",
        "unfilled_to_filled",
        "unfilled_stayed_cash",
    ):
        tgt[k] += int(d0.get(k, 0))
    tgt["gross_max_abs_delta"] = max(
        float(tgt["gross_max_abs_delta"]), float(d0.get("gross_max_abs_delta", 0.0))
    )


def block_frame(parts_dir: Path, days: list[str]) -> pl.DataFrame:
    """One block's signals; the whole corpus is never concatenated, only one block."""
    frames = []
    for day in days:
        path = parts_dir / f"{day}.parquet"
        if path.exists():
            frames.append(pl.read_parquet(path))
    if not frames:
        return _empty_part_frame()
    out = pl.concat(frames, how="vertical")
    for f in frames:
        del f
    return out


# ----- funded-reserve replay: delay, threshold and cadence are the only axes --
def replay_block(
    frame: pl.DataFrame,
    days: list[str],
    cell: Cell,
    cost_bps: float,
    block: str,
    labels: LabelView | None = None,
) -> tuple[dict, list[dict]]:
    """Replay ONE cell on ONE block at ONE cost rung under the shared conventions.

    Faithful to the frozen engine of ``alpha_retained_entry_delay.replay_block`` (itself
    the funded-reserve conventions of ``alpha_sparse_daily.replay_view``): a fill's
    realized, both-legs net is the cash that flows back into the book, so the funding
    available to a later minute depends on the rung; every rung is replayed in full and
    each cell carries its own honest fill counts. Long-only, one position per symbol,
    fees on BOTH legs, same-clock exits precede buys, no leverage: a symbol may not be
    re-entered until its ACTUAL exit minute plus the flat cooldown. Simultaneous intents
    are funded by DESCENDING SCORE at their own minute, before any fill outcome is known.
    An UNKNOWN exit keeps its slot and blocks its ticker for the rest of the session and
    is charged a full unit in the separately labeled lower bound; an unfilled entry
    reserves its ticket at the decision minute and releases it one minute after its own
    (delayed) entry minute, fee-free, still counting as an attempt with no cooldown.
    Every calendar day is replayed, so no-signal days stay in the denominator as cash
    days. The ONLY things a cell changes are which states clear the admission bar, where
    the entry price comes from (``t + delay``) and how often a ticker may recur; every
    cell exits at the session close.
    """
    view = labels or cell.labels
    gcol, xcol, scol, ocol, hcol = (
        view.gross,
        view.exit_et,
        view.status,
        view.open,
        view.hold_minute,
    )
    needed = ["day", "ticker", "t", "session_end", "score", gcol, xcol, scol, ocol, hcol]
    missing = [c for c in needed if c not in frame.columns]
    if missing:
        raise ValueError(f"[{STUDY}] {cell.key} block frame lacks {missing}")
    sig = frame.filter(pl.col("score") >= cell.threshold)
    n_signals = int(sig.height)
    if sig.height:
        ordered = sig.sort(["day", "t", "score", "ticker"], descending=[False, False, True, False])
        by_day = ordered.partition_by("day", as_dict=True)
        del ordered
    else:
        by_day = {}
    reserve = RESERVE_USD
    side = cost_bps / 20_000.0  # total round-trip friction, charged on BOTH legs
    trades: list[dict] = []
    daily: list[dict] = []
    skips: Counter = Counter()
    # Block-wide ground-truth accumulators: EVERY day of the block adds to these, so the
    # turnover/hold sample is the block's, never the last day's.
    entry_notional = 0.0
    exit_notional = 0.0
    holds: list[int] = []
    known_nets: list[float] = []
    for day in days:
        rows = by_day.get((day,))
        cash, active, blocked, used = reserve, set(), {}, {}
        queue: list[tuple] = []
        seq = 0
        known_pnl = 0.0
        unknown = 0
        fills = 0
        attempts = 0
        unfilled = 0
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
                if int(used.get(ticker, 0)) >= cell.max_attempts:
                    skips["max_attempts"] += 1
                    continue
                if len(active) >= MAX_POSITIONS or cash + 1e-8 < ORDER_BUDGET:
                    skips["cash_or_slot"] += 1
                    continue
                used[ticker] = int(used.get(ticker, 0)) + 1
                attempts += 1
                cash -= ORDER_BUDGET
                active.add(ticker)
                min_cash = min(min_cash, cash)
                pos_samples.append(len(active))
                entry_minute = t + cell.delay
                if r[scol] == view.unfilled:
                    # No position opened: the attempt minute reserves the ticket and
                    # releases it one minute after its own entry minute, fee-free.
                    unfilled += 1
                    heapq.heappush(queue, (entry_minute + 1, seq, ticker, ORDER_BUDGET, False))
                    seq += 1
                    continue
                gross, xet = r[gcol], r[xcol]
                if gross is None:
                    unknown += 1
                    net, status, proceeds = None, "unknown_pending", 0.0
                    release = int(r["session_end"]) + 1
                else:
                    net = (1.0 + float(gross)) * (1.0 - side) / (1.0 + side) - 1.0
                    status, proceeds = "known_open_proxy", ORDER_BUDGET * (1.0 + net)
                    known_pnl += ORDER_BUDGET * net
                    release = int(xet)
                # Pending (unknown) exits keep the slot AND block the ticker: no reopening
                # until the actual exit prints, and never within the session otherwise.
                blocked[ticker] = release + cell.cooldown_min
                heapq.heappush(queue, (release, seq, ticker, proceeds, True))
                seq += 1
                fills += 1
                entry_notional += ORDER_BUDGET
                exit_notional += proceeds
                if net is not None:
                    known_nets.append(net)
                actual_entry = t
                if view.entry_et:
                    raw_entry = r[view.entry_et]
                    if raw_entry is not None:
                        actual_entry = int(raw_entry)
                if xet is not None and r[hcol] is not None:
                    holds.append(int(xet) - int(r[hcol]))
                trades.append(
                    {
                        "day": day,
                        "ticker": ticker,
                        "t": t,
                        "entry_minute": entry_minute,
                        "entry_et": actual_entry,
                        "entry_open": r[ocol],
                        "entry_status": r[scol],
                        "exit_et": xet,
                        "gross": gross,
                        "net": net,
                        "cost_bps": cost_bps,
                        "order_budget": ORDER_BUDGET,
                        "score": r["score"],
                        "status": status,
                        "attempt_index": int(used[ticker]),
                        "cell": cell.key,
                        "cadence": cell.cadence,
                        "delay": cell.delay,
                        "threshold": cell.threshold,
                        "block": block,
                        "positions_at_entry": len(active),
                    }
                )
        lower_pnl = known_pnl - ORDER_BUDGET * unknown
        daily.append(
            {
                "day": day,
                "known_pnl": known_pnl,
                "unknown": unknown,
                "fills": fills,
                "attempts": attempts,
                "unfilled": unfilled,
                "lower_pnl": lower_pnl,
                "lower_return": lower_pnl / reserve,
                "positions_peak": max(pos_samples) if pos_samples else 0,
                "positions_mean": float(np.mean(pos_samples)) if pos_samples else 0.0,
                "peak_deployed": reserve - min_cash,
            }
        )
    known = np.asarray(known_nets, dtype=float)
    daily_known = np.asarray([d["known_pnl"] for d in daily], dtype=float)
    daily_lower = np.asarray([d["lower_pnl"] for d in daily], dtype=float)
    wins = float(known[known > 0].sum()) if known.size else 0.0
    losses = float(-known[known < 0].sum()) if known.size else 0.0
    months_known: dict[str, list[float]] = {}
    months_lower: dict[str, list[float]] = {}
    for d in daily:
        months_known.setdefault(d["day"][:7], []).append(d["known_pnl"])
        months_lower.setdefault(d["day"][:7], []).append(d["lower_pnl"])
    per_day_known = float(daily_known.mean()) if daily_known.size else None
    per_day_lower = float(daily_lower.mean()) if daily_lower.size else None
    turnover = entry_notional + exit_notional  # ALL days of the block, never the last one
    metrics = {
        "cell_key": cell.key,
        "cadence": cell.cadence,
        "threshold": cell.threshold,
        "delay": cell.delay,
        "max_attempts": cell.max_attempts,
        "cooldown_min": cell.cooldown_min,
        "control": cell.control,
        "block": block,
        "cost_bps": cost_bps,
        "days": int(len(days)),
        "n_signals": int(n_signals),
        "attempts": int(sum(d["attempts"] for d in daily)),
        "fills": int(len(trades)),
        "known_fills": int(known.size),
        "unknown_fills": int(sum(d["unknown"] for d in daily)),
        "unfilled_cash_attempts": int(sum(d["unfilled"] for d in daily)),
        "no_order_skips": {k: int(v) for k, v in sorted(skips.items())},
        "no_order_skips_total": int(sum(skips.values())),
        "traded_days": int(len({tr["day"] for tr in trades})),
        "no_signal_days": int(sum(1 for d in daily if d["attempts"] == 0)),
        "known_contribution_usd": float(daily_known.sum()),
        "known_contribution_usd_per_day": per_day_known,
        "known_contribution_usd_per_year_252": (
            per_day_known * TRADING_DAYS_PER_YEAR if per_day_known is not None else None
        ),
        "mean_net_known_fill": float(known.mean()) if known.size else None,
        "known_win_rate": float((known > 0).mean()) if known.size else None,
        "known_profit_factor": (wins / losses) if losses else None,
        "worst_known_fill": float(known.min()) if known.size else None,
        "positive_months_known": int(sum(1 for v in months_known.values() if sum(v) > 0)),
        "months": int(len(months_known)),
        "monthly_known": {
            m: {
                "days_replayed": int(len(v)),
                "known_contribution_usd": float(sum(v)),
                "positive": bool(sum(v) > 0),
            }
            for m, v in sorted(months_known.items())
        },
        "full_loss_lower_bound": {
            "note": (
                "SEPARATELY LABELED full-loss lower bound over UNKNOWN exits: known "
                "contribution minus one full ticket per UNKNOWN fill. Never blended into "
                "known contribution, never called an EV, never a cash zero."
            ),
            "unknown_fills": int(sum(d["unknown"] for d in daily)),
            "unknown_charge_usd": float(sum(d["unknown"] for d in daily) * ORDER_BUDGET),
            "lower_bound_usd": float(daily_lower.sum()),
            "lower_bound_usd_per_day": per_day_lower,
            "lower_bound_usd_per_year_252": (
                per_day_lower * TRADING_DAYS_PER_YEAR if per_day_lower is not None else None
            ),
            "mean_daily_lower_bound_return": (
                per_day_lower / reserve if per_day_lower is not None else None
            ),
            "positive_months_lower_bound": int(sum(1 for v in months_lower.values() if sum(v) > 0)),
        },
        "turnover_usd": turnover,
        "turnover_usd_per_day": turnover / len(days) if days else None,
        "mean_hold_min": float(np.mean(holds)) if holds else None,
        "median_hold_min": float(np.median(holds)) if holds else None,
        "min_hold_min": int(min(holds)) if holds else None,
        "max_hold_min": int(max(holds)) if holds else None,
        "mean_positions_open": (
            float(np.mean([d["positions_mean"] for d in daily])) if daily else None
        ),
        "peak_positions_open": max(d["positions_peak"] for d in daily) if daily else None,
        "mean_peak_deployed_usd": (
            float(np.mean([d["peak_deployed"] for d in daily])) if daily else None
        ),
        "reserve_usd": reserve,
        "carry_equity_end_usd": reserve + float(daily_lower.sum()) if daily else reserve,
        "carry_equity_return": ((reserve + float(daily_lower.sum())) / reserve - 1.0)
        if daily
        else None,
        "carry_equity_is_compounding": False,
        "daily_reset_normalization": (
            "research only: each session restarts from the same reserve, so this is NOT "
            "a self-financing CAGR"
        ),
        "bootstrap_daily_known": bootstrap_daily((daily_known / reserve).tolist()),
        "execution_proxy_only": True,
        "execution_proxy_note": (
            "every fill is a minute-open proxy on the day's SIP net bars; none of these "
            "are actual quotes or actual exchange fills"
        ),
        "capacity_note": (
            "book-sized tickets on a research reserve; displayed top-of-book depth and "
            "$1,000 tickets are NOT an executable capacity claim and scaling is not "
            "assumed linear"
        ),
        "label_basis": view.basis,
        "daily": daily,
    }
    return metrics, trades


def cell_view(metrics: dict) -> dict:
    """Stored per-cell view without the bulky per-day list (npz keeps the series)."""
    return {k: v for k, v in metrics.items() if k != "daily"}


def replay_surface(
    frame: pl.DataFrame, days: list[str], cells: tuple[Cell, ...], block: str, tag: str
) -> tuple[dict[str, dict[str, dict]], dict[str, np.ndarray]]:
    """Every cell of one family on one block: one full replay at every rung of the ladder."""
    surface: dict[str, dict[str, dict]] = {}
    daily: dict[str, np.ndarray] = {}
    for cell in cells:
        surface[cell.key] = {}
        daily_sel = None
        for cost in LADDER:
            metrics, _ = replay_block(frame, days, cell, cost, block)
            surface[cell.key][str(int(cost))] = cell_view(metrics)
            if cost == SELECT_COST_BPS:
                daily_sel = np.asarray([d["known_pnl"] for d in metrics["daily"]], dtype=float)
        daily[cell.key] = daily_sel
        print_cell_line(tag, cell.key, surface[cell.key])
    return surface, daily


# ----- block reporting ---------------------------------------------------------
def print_cell_line(tag: str, key: str, by_rung: dict) -> None:
    sel = by_rung[str(int(SELECT_COST_BPS))]
    ladder = " ".join(
        f"{r}:{_fmt(by_rung[r]['known_contribution_usd_per_day'], '+.2f')}"
        for r in sorted(by_rung, key=float)
    )
    print(
        f"[{tag}] {key:<30} $/day {ladder} known={sel['known_fills']} "
        f"unk={sel['unknown_fills']} unfilled={sel['unfilled_cash_attempts']} "
        f"no_order={sel['no_order_skips_total']} traded={sel['traded_days']}/{sel['days']} "
        f"attempts={sel['attempts']} signals={sel['n_signals']}",
        flush=True,
    )


# ----- selection (2023 validation only, frozen before any late outcome) --------
def rank_cells(surface: dict) -> list[Cell]:
    """Rank the 12 fixed cells by validation known-contribution $/calendar day at the
    pre-declared rung; ties break on known fills, then fewer attempts, then cell key."""
    rung = str(int(SELECT_COST_BPS))
    return sorted(
        CELLS,
        key=lambda c: (
            -float(surface[c.key][rung]["known_contribution_usd_per_day"] or 0.0),
            -int(surface[c.key][rung]["known_fills"]),
            int(surface[c.key][rung]["attempts"]),
            c.key,
        ),
    )


def selection_section(
    ranked: list[Cell], surface: dict, model_sha: str, producer: str, contract_sha: str
) -> dict:
    rung = str(int(SELECT_COST_BPS))
    chosen = ranked[0]
    c = surface[chosen.key][rung]
    return {
        "objective": CONTRACT_PAYLOAD["selection_rule"]["objective"],
        "block": VAL_PERIOD,
        "calendar_days": c["days"],
        "selection_cost_bps": SELECT_COST_BPS,
        "cost_scenario_nature": COST_SCENARIO_NATURE,
        "tie_break": CONTRACT_PAYLOAD["selection_rule"]["tie_break"],
        "no_count_median_or_ci_gate": True,
        "frozen_before_late_traversal": True,
        "grid_size": len(CELLS),
        "status": STATUS,
        "reference_row": {
            "lane": "repeat_h60 (the frozen h60 lane, factory/scripts/alpha_sparse_daily.py)",
            "source": REFERENCE_REPEAT_H60["source"],
            "basis": REFERENCE_REPEAT_H60["basis"],
            VAL_PERIOD: REFERENCE_REPEAT_H60[VAL_PERIOD][rung],
            CONF_PERIOD: REFERENCE_REPEAT_H60[CONF_PERIOD][rung],
        },
        "ranking": [
            {
                "rank": i,
                "cell_key": cell.key,
                "cadence": cell.cadence,
                "threshold": cell.threshold,
                "delay": cell.delay,
                "entry_minute": f"t+{cell.delay}",
                "label_basis": "panel stored h390 labels" if cell.delay == 0 else "tape-recomputed",
                "known_contribution_usd_per_day": surface[cell.key][rung][
                    "known_contribution_usd_per_day"
                ],
                "known_contribution_usd_per_year_252": surface[cell.key][rung][
                    "known_contribution_usd_per_year_252"
                ],
                "full_loss_lower_bound_usd_per_day": surface[cell.key][rung][
                    "full_loss_lower_bound"
                ]["lower_bound_usd_per_day"],
                "known_fills": surface[cell.key][rung]["known_fills"],
                "unknown_fills": surface[cell.key][rung]["unknown_fills"],
                "unfilled_cash_attempts": surface[cell.key][rung]["unfilled_cash_attempts"],
                "attempts": surface[cell.key][rung]["attempts"],
                "n_signals": surface[cell.key][rung]["n_signals"],
                "traded_days": surface[cell.key][rung]["traded_days"],
                "mean_net_known_fill": surface[cell.key][rung]["mean_net_known_fill"],
            }
            for i, cell in enumerate(ranked, 1)
        ],
        "chosen": {
            "cell_key": chosen.key,
            "cadence": chosen.cadence,
            "threshold": chosen.threshold,
            "delay": chosen.delay,
            "entry_minute": f"t+{chosen.delay}",
            "exit": LABEL_RULES["exit"],
            "max_attempts": chosen.max_attempts,
            "cooldown_min": chosen.cooldown_min,
            "label_basis": "panel stored h390 labels" if chosen.delay == 0 else "tape-recomputed",
            "known_contribution_usd_per_day": c["known_contribution_usd_per_day"],
            "known_contribution_usd_per_year_252": c["known_contribution_usd_per_year_252"],
            "known_fills": c["known_fills"],
            "traded_days": c["traded_days"],
            "mean_net_known_fill": c["mean_net_known_fill"],
        },
        "late_block_disclosure": (
            "2025-02-01..2026-05-31 was already explored before this producer, so any "
            "late outcome is DISCOVERY-NOT-VALIDATED - not pristine and not previously "
            "unknown; the late traversal is a transparency read with the choice "
            "immutable, not a holdout"
        ),
        "provenance": {
            "producer_sha256": producer,
            "contract_sha256": contract_sha,
            "model_sha256": model_sha,
        },
    }


# ----- the stored-surface reproduction gate (d=0 once cells, validation) -------
def compare_stored(key: str, view: dict) -> dict:
    """Field-by-field reproduction check of one stored h390 surface cell."""
    exp = EXPECTED_STORED_SURFACE[VAL_PERIOD][key]
    skips = view.get("no_order_skips") or {}
    lower = view["full_loss_lower_bound"]
    pairs = {
        "days": (exp["days"], view.get("days")),
        "attempts": (exp["attempts"], view.get("attempts")),
        "fills": (exp["fills"], view.get("fills")),
        "known_fills": (exp["known_fills"], view.get("known_fills")),
        "unknown_fills": (exp["unknown_fills"], view.get("unknown_fills")),
        "traded_days": (exp["traded_days"], view.get("traded_days")),
        "n_signals": (exp["n_signals"], view.get("n_signals")),
        "cash_or_slot_skips": (exp["cash_or_slot_skips"], skips.get("cash_or_slot", 0)),
        "mean_daily_lower_bound": (
            exp["mean_daily_lower_bound"],
            lower["mean_daily_lower_bound_return"],
        ),
        "dollars_per_day": (
            exp["mean_daily_lower_bound"] * RESERVE_USD,
            lower["lower_bound_usd_per_day"],
        ),
        "mean_net_known_fill": (exp["mean_net_known_fill"], view.get("mean_net_known_fill")),
    }
    checks: dict[str, dict] = {}
    for f, (o, m) in pairs.items():
        if isinstance(o, float) or isinstance(m, float):
            ok = (o is None and m is None) or (
                o is not None and m is not None and abs(float(o) - float(m)) <= FLOAT_TOL
            )
        else:
            ok = o == m
        checks[f] = {"expected": o, "replayed": m, "match": bool(ok)}
    checks["all_match"] = all(c.get("match") for c in checks.values())
    return checks


def stored_gate_block(val_surface: dict, days: list[str]) -> dict:
    """Hard gate: the d=0 once cells on the panel's stored h390 labels must reproduce the
    stored alpha_open_learned surface cells field-for-field on the full validation block
    at the 100 bps reproduction rung (skipped when the corpus is restricted)."""
    rung = str(int(REPRO_COST_BPS))
    if len(days) != EXPECTED_DAYS[VAL_PERIOD]:
        gate = {
            "skipped": True,
            "reason": (
                f"corpus restricted to {len(days)} of the block's "
                f"{EXPECTED_DAYS[VAL_PERIOD]} days; a full-block reproduction needs the full block"
            ),
            "source": EXPECTED_STORED_SURFACE["source"],
        }
        print(f"[gate] stored-surface reproduction not comparable ({gate['reason']})", flush=True)
        return gate
    checks: dict[str, dict] = {}
    for key in EXPECTED_STORED_SURFACE[VAL_PERIOD]:
        check = compare_stored(key, val_surface[key][rung])
        checks[key] = check
        if not check["all_match"]:
            bad = {k: v for k, v in check.items() if k != "all_match" and not v.get("match")}
            raise SystemExit(
                f"[{STUDY}] the stored-label d=0 once cell {key} does not reproduce the "
                f"stored {VAL_PERIOD} surface cell at {int(REPRO_COST_BPS)}bps: {bad}"
            )
        v = val_surface[key][rung]
        print(
            f"[gate] {key} (panel stored h390 labels) reproduces the stored surface cell "
            f"exactly @{int(REPRO_COST_BPS)}bps ({v['known_fills']} known fills, "
            f"{_fmt(v['full_loss_lower_bound']['mean_daily_lower_bound_return'])}/day)",
            flush=True,
        )
    return {
        "skipped": False,
        "source": EXPECTED_STORED_SURFACE["source"],
        "rung_bps": REPRO_COST_BPS,
        "cells": checks,
        "all_match": all(c["all_match"] for c in checks.values()),
    }


# ----- the label-basis gap: stored-label d=0 vs tape-recomputed d=0 -------------
def gap_effect(stored_by_rung: dict, recomputed_by_rung: dict) -> dict:
    """Stored-label d=0 cell vs the tape-recomputed d=0 cell, rung by rung."""
    out = {}
    for rung, fv in stored_by_rung.items():
        rv = recomputed_by_rung[rung]
        f_day = fv["known_contribution_usd_per_day"]
        r_day = rv["known_contribution_usd_per_day"]
        out[rung] = {
            "stored_known_fills": fv["known_fills"],
            "recomputed_d0_known_fills": rv["known_fills"],
            "delta_known_fills": rv["known_fills"] - fv["known_fills"],
            "stored_known_usd_per_day": f_day,
            "recomputed_d0_known_usd_per_day": r_day,
            "delta_known_usd_per_day": (
                (r_day - f_day) if f_day is not None and r_day is not None else None
            ),
            "stored_mean_net_known_fill": fv["mean_net_known_fill"],
            "recomputed_d0_mean_net_known_fill": rv["mean_net_known_fill"],
            "stored_traded_days": fv["traded_days"],
            "recomputed_d0_traded_days": rv["traded_days"],
            "stored_attempts": fv["attempts"],
            "recomputed_d0_attempts": rv["attempts"],
            "stored_unknown_fills": fv["unknown_fills"],
            "recomputed_d0_unknown_fills": rv["unknown_fills"],
            "note": (
                "the recomputed d=0 cell applies the prescribed filled-open entry and "
                "last-bar-at-or-before-session_end exit rules; every difference from "
                "the stored-label cell is a named gap rule, quantified row by row in "
                "coverage.d0_vs_panel"
            ),
        }
    return out


def gap_tables(surface: dict, gap_surface: dict) -> dict:
    """Per cadence and threshold: the stored-label d=0 cell vs its tape-recomputed d=0
    twin, at every rung - the label-basis wedge, quantified instead of absorbed."""
    out: dict[str, dict] = {}
    for cadence in CADENCES:
        for threshold in THRESHOLDS:
            stored_key = cell_key(cadence.name, 0, threshold)
            recomputed_key = f"recomputed_{stored_key}"
            out[stored_key] = {
                "cadence": cadence.name,
                "threshold": threshold,
                "stored_cell": stored_key,
                "recomputed_cell": recomputed_key,
                "by_rung": gap_effect(surface[stored_key], gap_surface[recomputed_key]),
            }
    return out


# ----- the frozen repeat_h60 comparison row, on the identical basis -------------
def reference_comparison(block: str, surface: dict) -> dict:
    """Every grid cell beside the frozen repeat_h60 reference row at the selection rung."""
    rung = str(int(SELECT_COST_BPS))
    ref = REFERENCE_REPEAT_H60[block][rung]
    rows = []
    for cell in CELLS:
        c = surface[cell.key][rung]
        day = c["known_contribution_usd_per_day"]
        rows.append(
            {
                "cell_key": cell.key,
                "cadence": cell.cadence,
                "threshold": cell.threshold,
                "delay": cell.delay,
                "label_basis": "panel stored h390 labels" if cell.delay == 0 else "tape-recomputed",
                "known_contribution_usd_per_day": day,
                "known_contribution_usd_per_year_252": c["known_contribution_usd_per_year_252"],
                "delta_vs_repeat_h60_usd_per_day": (
                    (day - ref["dollars_per_day"]) if day is not None else None
                ),
                "delta_vs_repeat_h60_usd_per_year_252": (
                    (day - ref["dollars_per_day"]) * TRADING_DAYS_PER_YEAR
                    if day is not None
                    else None
                ),
                "beats_repeat_h60_at_selection_rung": bool(
                    day is not None and day > ref["dollars_per_day"]
                ),
                "known_fills": c["known_fills"],
                "delta_known_fills_vs_repeat_h60": c["known_fills"] - ref["known_fills"],
                "traded_days": c["traded_days"],
                "delta_traded_days_vs_repeat_h60": c["traded_days"] - ref["traded_days"],
                "attempts": c["attempts"],
                "mean_net_known_fill": c["mean_net_known_fill"],
                "full_loss_lower_bound_usd_per_day": c["full_loss_lower_bound"][
                    "lower_bound_usd_per_day"
                ],
            }
        )
    return {
        "rung_bps": SELECT_COST_BPS,
        "block": block,
        "calendar_days": ref["days"],
        "reference_row": {
            "lane": REFERENCE_REPEAT_H60["lane"],
            "source": REFERENCE_REPEAT_H60["source"],
            "basis": REFERENCE_REPEAT_H60["basis"],
            "selection_rung": ref,
            "full_ladder": REFERENCE_REPEAT_H60[block],
        },
        "cells": rows,
        "note": (
            "one comparison row per grid cell at the pre-declared 25 bps rung against "
            "the frozen repeat_h60 lane on the identical basis (same $3,000 book, same "
            "funded-reserve conventions, same minute-open proxy, same calendar-day "
            "denominators); the row is a comparison object, never a gate, and the h390 "
            "cells hold to the session close while the h60 reference row exits at "
            "entry + 60 minutes"
        ),
    }


# ----- decision text + block discovery -----------------------------------------
def _ref_line(tag: str, block: str) -> str:
    c = REFERENCE_REPEAT_H60[block][str(int(SELECT_COST_BPS))]
    return (
        f"{tag} {c['known_fills']} known fills "
        f"{_fmt(c['dollars_per_day'], '+.2f')} $/day @{int(SELECT_COST_BPS)}bps "
        f"on {c['traded_days']}/{c['days']} traded days"
    )


def decision_text(
    chosen: Cell,
    val_surface: dict,
    late_surface: dict | None,
    gap_val: dict,
    gate: dict,
    ref_late: dict | None,
) -> str:
    rung = str(int(SELECT_COST_BPS))
    c = val_surface[chosen.key][rung]
    d0_key = cell_key(chosen.cadence, 0, chosen.threshold)
    d0 = val_surface[d0_key][rung]
    if gate.get("all_match"):
        gate_txt = (
            "the stored-label d=0 once cells reproduce the stored h390 surface cells "
            "exactly (field-for-field gate)"
        )
    elif gate.get("skipped"):
        gate_txt = "the stored-surface gate was not comparable (restricted corpus)"
    else:
        gate_txt = "WARNING: the stored-surface gate did not reproduce the stored cells"
    gap_node = gap_val[d0_key]["by_rung"][rung]
    text = (
        f"{STATUS}: {chosen.key} selected by 2023 validation known-contribution "
        f"$/calendar day at {int(SELECT_COST_BPS)}bps "
        f"({_fmt(c['known_contribution_usd_per_day'], '+.2f')} $/day, "
        f"{_fmt(c['known_contribution_usd_per_year_252'], '+.2f')} $/yr by the simple "
        f"252-session daily-reset convention, {c['known_fills']} known fills "
        f"({c['unknown_fills']} unknown, {c['unfilled_cash_attempts']} unfilled, "
        f"{c['no_order_skips_total']} no-order) on {c['traded_days']} traded days of "
        f"{c['days']}); its own d=0 twin {d0_key} "
        f"{_fmt(d0['known_contribution_usd_per_day'], '+.2f')} $/day "
        f"({d0['known_fills']} known fills); {gate_txt}; label-basis gap at d=0 "
        f"{_fmt(gap_node['delta_known_usd_per_day'], '+.4f')} $/day "
        f"(fills {gap_node['stored_known_fills']}->{gap_node['recomputed_d0_known_fills']}). "
        f"Frozen repeat_h60 reference row on the identical basis: "
        f"{_ref_line('VAL', VAL_PERIOD)}; {_ref_line('LATE', CONF_PERIOD)}."
    )
    if late_surface is not None:
        lc = late_surface[chosen.key][rung]
        lb = late_surface[d0_key][rung]
        best = max(ref_late["cells"], key=lambda r: r["known_contribution_usd_per_day"] or 0.0)
        text += (
            f" Late block (2025-02..2026-05, previously explored, transparency only): "
            f"{_fmt(lc['known_contribution_usd_per_day'], '+.2f')} $/day vs "
            f"{_fmt(lb['known_contribution_usd_per_day'], '+.2f')} $/day at d=0, "
            f"{lc['known_fills']} known fills on {lc['traded_days']} traded days of "
            f"{lc['days']}; best late cell vs the reference row: {best['cell_key']} "
            f"{_fmt(best['known_contribution_usd_per_day'], '+.2f')} $/day vs "
            f"{_fmt(REFERENCE_REPEAT_H60[CONF_PERIOD][rung]['dollars_per_day'], '+.2f')} "
            f"$/day for repeat_h60."
        )
    else:
        text += " Late block not run (--skip-late)."
    return text


def analysis_blocks(days_arg, need_late: bool) -> dict[str, list[str]]:
    """Group the allowed panel days into the analysis blocks.

    The 2021-02..2022-12 fit block is NEVER collected, replayed or scored: only days
    whose period is an analysis period enter a block, so the 484 development sessions
    are structurally excluded (their count is still verified when the corpus is
    unrestricted). A bare run requires the full panel composition; ``--days`` restricts
    the corpus for debugging and skips the composition check.
    """
    files = sorted(
        p.stem for p in (PANEL_ROOT / "days").glob("????-??-??.parquet") if allowed(p.stem)
    )
    keep = set(days_arg) if days_arg else None
    if keep is not None:
        known = set(files)
        stale = sorted(d for d in keep if d not in known)
        if stale:
            raise SystemExit(f"[{STUDY}] requested days are not allowed panel days: {stale}")
    buckets: dict[str, list[str]] = {}
    for day in files:
        buckets.setdefault(period(day), []).append(day)
    blocks: dict[str, list[str]] = {}
    for day in files:
        if keep is not None and day not in keep:
            continue
        p = period(day)
        if p not in ANALYSIS_PERIODS:
            # the fit block (and anything outside the analysis periods) is never replayed
            continue
        blocks.setdefault(p, []).append(day)
    if keep is None:
        for p, expected in EXPECTED_DAYS.items():
            if p == CONF_PERIOD and not need_late:
                continue
            if len(buckets.get(p, [])) != expected:
                raise SystemExit(
                    f"[{STUDY}] requires {expected} {p} panel days, got {len(buckets.get(p, []))}"
                )
    if not blocks.get(VAL_PERIOD):
        raise SystemExit(
            f"[{STUDY}] no {VAL_PERIOD} days selected; the $/day selection objective "
            "requires the 2023 validation block"
        )
    return blocks


# ----- the predeclared contract (written before any validation score) ----------
def contract_document(
    model_path: Path,
    model_sha: str,
    producer: str,
    blocks: dict[str, list[str]],
) -> dict:
    """The full predeclared grid, ladder and selection rule. Deterministic on purpose:
    no wall-clock field, so a --resume run rewrites the byte-identical document and the
    per-day parts stay valid."""
    return {
        "study": STUDY,
        "label": (
            "retained h390 hold-to-session-close lane: 12 fixed cells (thresholds "
            "0.020/0.030/0.050 x entry delays 0/2 minutes past the state minute t x "
            "cadences once/repeat) on the ONE immutable stored h390 payoff model, every "
            "cell holding to the session close (exit = the last actual bar open at or "
            "before session_end)"
        ),
        "status": STATUS,
        "declared_before_any_validation_outcome": True,
        "question": (
            "does a hold-to-session-close lane on the stored h390 head earn its keep at "
            "low modeled round-trip friction, and do a 2-minute-later entry or the "
            "retained repeat cadence change the answer? The panel's stored h390 label "
            "already expires at the session close, so this study formulates the lane "
            "explicitly and reports every cell at every rung against the frozen "
            "repeat_h60 reference row"
        ),
        "panel_root_days": {
            "development_fit_never_scored": EXPECTED_DAYS["train"],
            "development_note": (
                "484 development sessions (2021-02-01..2022-12-30) are the fit block of "
                "the stored model; their weights are frozen and the block is never "
                "replayed, scored or outcome-reported"
            ),
            VAL_PERIOD: EXPECTED_DAYS[VAL_PERIOD],
            CONF_PERIOD: EXPECTED_DAYS[CONF_PERIOD],
            "analysis_corpus": EXPECTED_DAYS[VAL_PERIOD] + EXPECTED_DAYS[CONF_PERIOD],
        },
        "scoring_chain": (
            "alpha_sparse_daily.day_context -> alpha_open_sim.causal_liquidity -> "
            "alpha_open_learned.feature_matrix -> the stored h390 head (one predict per "
            "panel day, shared by all 12 cells)"
        ),
        "grid": {
            "n_cells": len(CELLS),
            "axes": {
                "thresholds": [float(t) for t in THRESHOLDS],
                "entry_delays_minutes": [int(d) for d in DELAYS],
                "cadences": {
                    "once": ATTEMPTS_ONCE,
                    "repeat": ATTEMPTS_REPEAT,
                },
                "exit": LABEL_RULES["exit"],
            },
            "cells": [
                {
                    "key": c.key,
                    "cadence": c.cadence_str,
                    "threshold": c.threshold,
                    "delay": c.delay,
                    "entry_minute": f"t+{c.delay}",
                    "max_attempts": c.max_attempts,
                    "cooldown_min": c.cooldown_min,
                    "label_basis": (
                        "panel stored h390 labels" if c.delay == 0 else "tape-recomputed"
                    ),
                    "control": c.control,
                }
                for c in CELLS
            ],
            "gap_cells": [
                {
                    "key": c.key,
                    "purpose": (
                        "the d=0 cells recomputed from the day's tape; the label-basis "
                        "gap vs the stored labels, quantified per cadence and threshold"
                    ),
                    "threshold": c.threshold,
                    "cadence": c.cadence,
                }
                for c in GAP_CELLS
            ],
            "fixed_in_code": True,
            "no_hpo": True,
            "no_refit": True,
            "every_cell_reads_the_same_score": True,
            "only_axes": [
                "which states clear the admission bar (threshold)",
                "when the entry price is read (delay, minutes past t)",
                "how often a ticker may recur (cadence: once or repeat)",
            ],
        },
        "selection": {
            "objective": CONTRACT_PAYLOAD["selection_rule"]["objective"],
            "selection_block": "validation 2023 only (250 sessions), never the late block",
            "selection_cost_bps": SELECT_COST_BPS,
            "rung_nature": COST_SCENARIO_NATURE,
            "tie_breaks": CONTRACT_PAYLOAD["selection_rule"]["tie_break"],
            "gates_applied": {
                "cost_floor": None,
                "count_floor": None,
                "median_gate": None,
                "ci_gate": None,
                "tail_gate": None,
                "note": "no gate of any kind is applied; every cell of the grid is reported",
            },
            "frozen_before_late_inspection": True,
            "output": "selection.json (frozen before any late-block outcome is computed)",
        },
        "cost_ladder": {
            "cost_scenarios_bps": [float(c) for c in COST_SCENARIOS_BPS],
            "historical_diagnostic_cost_bps": [HISTORICAL_COST_BPS],
            "full_ladder_bps": [float(c) for c in LADDER],
            "selection_rung_bps": SELECT_COST_BPS,
            "reference_rung_bps": REPRO_COST_BPS,
            "is_comparison_not_hurdle": True,
            "stress_vetoes": None,
            "note": (
                "every rung is a TOTAL round-trip modeled friction scenario charged on "
                "both legs of the minute-open proxy prices; 25..150 bps describe a "
                "LOW-FEE OPPORTUNITY set (a primary US broker's regular schedule - zero "
                "commission, SEC 20.6 per $1M, CAT 0.000003 per side, daily cent rounding "
                "- is typically well under 1 bp on a $1,000 ticket), and the actual "
                "provider fee is sourced separately in the provider-fee ledger"
            ),
        },
        "replay": {
            "engine": f"{STUDY}.replay_block (the funded-reserve conventions of "
            "alpha_retained_entry_delay.replay_block / alpha_sparse_daily.replay_view)",
            "conventions_source": "factory/scripts/alpha_retained_entry_delay.py (frozen engine)",
            "reserve_usd": RESERVE_USD,
            "max_positions": MAX_POSITIONS,
            "order_budget_usd": ORDER_BUDGET,
            "leverage": False,
            "fees_both_legs": True,
            "one_position_per_symbol": True,
            "same_clock_exits_before_buys": True,
            "simultaneous_clock_funded_before_fill_outcome": True,
            "highest_score_eligible_intents_funded_first": True,
            "cooldown_rule": (
                "no reopening until the ACTUAL exit minute + cooldown_min; an unfilled "
                "attempt minute has no exit and therefore no cooldown"
            ),
            "unknown_exit_rule": (
                "keeps its slot and blocks its ticker for the rest of the session; the "
                "lower bound charges one full ticket, never cash and never an expected "
                "value"
            ),
            "unfilled_rule": (
                "the ticket is reserved at the decision minute and released one minute "
                "after the delayed entry minute, fee-free, and still counts the attempt"
            ),
            "session_close_structure": (
                "every exit is the session close, so a filled ticker cannot re-enter "
                "within the session; the repeat cadence differs from once only by "
                "re-attempts after an unfilled minute (or an early print on an "
                "incomplete stream), and that difference is reported as it is"
            ),
            "all_calendar_days_retained": True,
            "cost_rung_rule": (
                "a fill's both-legs net is the cash that flows back into the book, so "
                "the funding available to a later minute depends on the rung; every "
                "rung is therefore replayed in full (fees on both legs, "
                "net = (1+gross)*(1-side)/(1+side)-1, side = cost_bps/20_000, exactly "
                "as the frozen engine charges them) and each cell carries its own "
                "honest fill counts"
            ),
            "daily_reset_is_not_self_financing_cagr": True,
            "carry_equity_check": "additive fixed-ticket equity, no compounding",
        },
        "labels": LABEL_RULES,
        "reported_bases": {
            "known_contribution": (
                "dollars of the fills whose exit actually printed; UNKNOWN fills are "
                "excluded, so the figure is partial by construction"
            ),
            "full_loss_lower_bound": (
                "known contribution minus one whole ticket per UNKNOWN fill; a labelled "
                "lower bound over unknowns, never blended with the known contribution"
            ),
            "never_blended": True,
        },
        "annualization": {
            "trading_days_per_year": TRADING_DAYS_PER_YEAR,
            "convention": (
                "simple dollars/day x 252 sessions on a fixed $3,000 reserve where every "
                "session restarts from the same reserve - a DAILY-RESET CONVENTION, "
                "explicitly NOT a CAGR and not a self-financing return"
            ),
        },
        "capacity_note": (
            "book-sized tickets on a research reserve; displayed top-of-book depth and "
            "$1,000 tickets are NOT an executable capacity claim and scaling is not "
            "assumed linear"
        ),
        "execution_proxy_only": True,
        "reference_rows": {
            "stored_h390_surface": {
                "cells": list(EXPECTED_STORED_SURFACE[VAL_PERIOD]),
                "definition": (
                    "the d=0 once cells replayed on the PANEL's own stored h390 labels; "
                    "they must reproduce learned/surface_validation.json ['390|0.03'] "
                    "and ['390|0.05'] field-for-field at the 100 bps reproduction rung "
                    "on the full 2023 validation block (a hard gate)"
                ),
                "expected": EXPECTED_STORED_SURFACE,
            },
            "repeat_h60": {
                "definition": (
                    "the frozen external comparison row: the retained lead lane of "
                    "factory/scripts/alpha_sparse_daily.py (h60 head, threshold 0.030, "
                    "exit_h60, repeat cadence) on the identical $3,000 book and "
                    "conventions; pinned at full precision from the parent's stored "
                    "learned_sparse_daily run and never recomputed here"
                ),
                "expected": REFERENCE_REPEAT_H60,
            },
        },
        "model": {
            "refit": False,
            "hpo": False,
            "feature_changes": False,
            "ticker_features_added": False,
            "horizon_scored_by_the_model": MODEL_HORIZON,
            "horizon_specific_heads": False,
            "scored_once_over_analysis_corpus": True,
            "fit_block_2021_2022_scored": False,
            "fit_block_note": (
                "the 2021-02..2022-12 fit block (484 sessions) is never replayed - the "
                "weights are frozen - so it is not scored; the single prediction pass is "
                "shared by all 12 cells"
            ),
            "stored_path": str(model_path),
            "stored_sha256": model_sha,
            "expected_sha256": EXPECTED_MODEL_SHA256,
            "feature_order": list(FEATURES_ALL),
            "feature_order_verified_at_load": True,
            "horizon_metadata_verified_at_load": True,
        },
        "provenance": {
            "panel_root": str(PANEL_ROOT),
            "bars_root": str(BARS_ROOT),
            "producer_sha256": producer,
            "analysis_days": sum(len(v) for v in blocks.values()),
            "validation_days": len(blocks.get(VAL_PERIOD, [])),
            "late_days": len(blocks.get(CONF_PERIOD, [])),
            "protected_outcomes_never_read": (
                "allowed(day) guards every day before any file read; 2024, 2025-01 and "
                "2026-06..2026-08 are outside the allowed window and are never opened"
            ),
        },
        "resume": {
            "schema": SCHEMA,
            "per_day_parts": "collect_parts/<day>.parquet + collect_parts/<day>.cov.json",
            "atomicity": (
                "temporary file then rename, so an interrupted day never lands half-written"
            ),
            "resume_hash": (
                "sha256 over 'schema|producer_sha256|contract_sha256|model_sha256|day'; a "
                "day part is intact only when its stored hash and its columns still match"
            ),
            "block_concatenation": "one block at a time; the corpus is never concatenated",
            "progress_every_days": PROGRESS_EVERY,
        },
        "no_network": True,
        "disclosures": DISCLOSURES,
        "novelty": {
            "vs_sparse_daily_lane": {
                "prior": "factory/scripts/alpha_sparse_daily.py",
                "prior_axes": (
                    "one threshold (0.030) x one scored horizon (h60) x the repeat cadence"
                ),
                "this_study": (
                    "three thresholds x two entry delays x two cadences on the h390 head, "
                    "every cell held to the session close; the repeat_h60 lane is the "
                    "external comparison row"
                ),
                "unchanged": (
                    "the funded-reserve account, the minute-open proxy and the causal panel"
                ),
            },
            "vs_exit_grid_lane": {
                "prior": "factory/scripts/alpha_retained_exit_grid.py",
                "prior_axes": (
                    "five thresholds x five horizons x two cadences at the frozen d=0 "
                    "entry minute, h60 read from the panel's stored labels"
                ),
                "this_study": (
                    "the session-close hold formulated explicitly on the h390 head with "
                    "the threshold, entry-delay and cadence axes crossed"
                ),
            },
            "vs_delay_lane": {
                "prior": "factory/scripts/alpha_retained_entry_delay.py",
                "prior_axes": "one threshold (0.030) x five delays x both cadences",
                "this_study": (
                    "the threshold and cadence axes crossed with a 0/2-minute entry delay "
                    "on a different head and a different exit rule"
                ),
            },
            "not_a_resurrection": DISCLOSURES["new_formulation"],
        },
    }


# ----- main pipeline -----------------------------------------------------------
def run(args: argparse.Namespace) -> None:
    t0 = time.time()
    out = args.out or OUTPUT
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")

    # 1) ONE stored model, loaded, digest-verified and scored (never fitted).
    bundle, model_path = load_stored_model(PANEL_ROOT / "learned" / "models")
    model_sha = sha256_file(model_path)
    producer = _producer_sha256()
    print(
        f"[model] h{MODEL_HORIZON} {model_path} (sha256 {model_sha[:12]}), "
        f"seed={bundle.get('seed')} clip={bundle.get('clip_fit')}",
        flush=True,
    )
    blocks = analysis_blocks(args.days, need_late=not args.skip_late)
    val_days = blocks.get(VAL_PERIOD, [])
    conf_days = blocks.get(CONF_PERIOD, [])
    print(
        f"[blocks] validation={len(val_days)} days, late={len(conf_days)} days, "
        f"cells={len(CELLS)} (skip_late={args.skip_late})",
        flush=True,
    )

    # 2) the predeclared contract (grid + ladder + selection rule) BEFORE any score.
    contract = contract_document(model_path, model_sha, producer, blocks)
    _json_atomic(out / "contract.json", contract)
    contract_sha = sha256_file(out / "contract.json")
    print(
        f"[freeze] contract -> {out / 'contract.json'} ({len(CELLS)} cells, ladder "
        f"{[int(c) for c in LADDER]} bps, selection@{int(SELECT_COST_BPS)} by 2023 "
        "known-contribution $/calendar day) -- before any validation score",
        flush=True,
    )

    # 3) one scoring pass over the analysis corpus; per-day atomic parts, resumable.
    collect_days = sorted(set(val_days) | (set(conf_days) if not args.skip_late else set()))
    coverage = collect_parts(
        bundle["lgbm"],
        collect_days,
        out / "collect_parts",
        producer,
        contract_sha,
        model_sha,
        args.resume,
    )
    print(
        f"[collect] {coverage['rows_kept']} admitted states (score >= {MIN_THRESHOLD}) on "
        f"{len(collect_days)} days; kept by threshold {coverage['rows_kept_by_threshold']}; "
        f"missing tape days {len(coverage['missing_day_files'])}; missing streams "
        f"{sum(len(v) for v in coverage['missing_symbol_streams'].values())}",
        flush=True,
    )
    d0p = coverage["d0_vs_panel"]
    print(
        f"[basis-gap] d=0 recomputed vs panel stored labels: unfilled->filled rows "
        f"{d0p['unfilled_to_filled']}, exit-minute changes {d0p['exit_et_changed']}, "
        f"exit-status changes {d0p['exit_status_changed']}, gross changes "
        f"{d0p['gross_changed']} (max |delta| {d0p['gross_max_abs_delta']:.3e})",
        flush=True,
    )

    # 4) 2023 validation: every cell at every rung, no median/tail/count gating.
    val_frame = block_frame(out / "collect_parts", val_days)
    print(f"[signals] validation block rows {val_frame.height}", flush=True)
    val_surface, val_daily = replay_surface(val_frame, val_days, CELLS, VAL_PERIOD, "val")
    gap_val_surface, _ = replay_surface(val_frame, val_days, GAP_CELLS, VAL_PERIOD, "gap")
    gate = stored_gate_block(val_surface, val_days)
    gap_val = gap_tables(val_surface, gap_val_surface)
    ref_val = reference_comparison(VAL_PERIOD, val_surface)
    del val_frame, gap_val_surface
    np.savez(out / "daily_known_validation.npz", **val_daily)
    g = gap_val[cell_key("once", 0, 0.030)]["by_rung"][str(int(REPRO_COST_BPS))]
    print(
        f"[basis-gap] once_d0_thr030 @{int(REPRO_COST_BPS)}: stored "
        f"{_fmt(g['stored_known_usd_per_day'], '+.4f')} vs recomputed "
        f"{_fmt(g['recomputed_d0_known_usd_per_day'], '+.4f')} $/day "
        f"(delta {_fmt(g['delta_known_usd_per_day'], '+.4f')}, fills "
        f"{g['stored_known_fills']}->{g['recomputed_d0_known_fills']})",
        flush=True,
    )
    rv = REFERENCE_REPEAT_H60[VAL_PERIOD][str(int(SELECT_COST_BPS))]
    rl = REFERENCE_REPEAT_H60[CONF_PERIOD][str(int(SELECT_COST_BPS))]
    print(
        f"[ref] frozen repeat_h60 reference row (identical basis): VAL "
        f"{rv['known_fills']} known fills {_fmt(rv['dollars_per_day'], '+.2f')} $/day "
        f"@{int(SELECT_COST_BPS)}bps on {rv['traded_days']}/{rv['days']} traded days; "
        f"LATE {rl['known_fills']} known fills "
        f"{_fmt(rl['dollars_per_day'], '+.2f')} $/day @{int(SELECT_COST_BPS)}bps on "
        f"{rl['traded_days']}/{rl['days']} traded days",
        flush=True,
    )

    # 5) the pre-declared selection, frozen here BEFORE any late outcome exists.
    ranked = rank_cells(val_surface)
    chosen = ranked[0]
    selection = selection_section(ranked, val_surface, model_sha, producer, contract_sha)
    selection["reference_comparison"] = ref_val
    _json_atomic(out / "selection.json", selection)
    sel_cell = val_surface[chosen.key][str(int(SELECT_COST_BPS))]
    print(
        f"[select] {int(SELECT_COST_BPS)}bps validation choice {chosen.key} by "
        f"known-contribution $/calendar day "
        f"({_fmt(sel_cell['known_contribution_usd_per_day'], '+.2f')} $/day) "
        f"of {len(CELLS)} cells; ties -> known fills -> fewest attempts -> cell key; "
        "no count/median/CI gate applied",
        flush=True,
    )

    if args.skip_late:
        decision = decision_text(chosen, val_surface, None, gap_val, gate, None)
        results = {
            "study": STUDY,
            "status": STATUS,
            "contract": contract,
            "coverage": coverage,
            "selection": selection,
            "validation": {
                "surface": val_surface,
                "gap_vs_panel_labels": gap_val,
                "stored_surface_gate": gate,
                "reference_comparison": ref_val,
                "daily_known_npz": str(out / "daily_known_validation.npz"),
            },
            "late": None,
            "decision": decision,
            "artifacts": {
                "contract": str(out / "contract.json"),
                "selection": str(out / "selection.json"),
                "collect_parts": str(out / "collect_parts"),
                "daily_known_validation_npz": str(out / "daily_known_validation.npz"),
                "producer_snapshot": str(out / "producer_snapshot.py"),
            },
            "runtime_s": round(time.time() - t0, 1),
        }
        _json_atomic(out / "results.json", results)
        print(f"[decision] {decision}", flush=True)
        print(f"[done] results -> {out / 'results.json'} ({results['runtime_s']}s)", flush=True)
        return

    # 6) late block: every cell at the full ladder, the choice immutable.
    conf_frame = block_frame(out / "collect_parts", conf_days)
    print(f"[signals] late block rows {conf_frame.height}", flush=True)
    late_surface, late_daily = replay_surface(conf_frame, conf_days, CELLS, CONF_PERIOD, "late")
    gap_late_surface, _ = replay_surface(conf_frame, conf_days, GAP_CELLS, CONF_PERIOD, "gap")
    gap_late = gap_tables(late_surface, gap_late_surface)
    ref_late = reference_comparison(CONF_PERIOD, late_surface)
    del conf_frame, gap_late_surface
    np.savez(out / "daily_known_late.npz", **late_daily)
    decision = decision_text(chosen, val_surface, late_surface, gap_val, gate, ref_late)
    results = {
        "study": STUDY,
        "status": STATUS,
        "contract": contract,
        "coverage": coverage,
        "selection": selection,
        "validation": {
            "surface": val_surface,
            "gap_vs_panel_labels": gap_val,
            "stored_surface_gate": gate,
            "reference_comparison": ref_val,
            "daily_known_npz": str(out / "daily_known_validation.npz"),
        },
        "late": {
            "surface": late_surface,
            "gap_vs_panel_labels": gap_late,
            "reference_comparison": ref_late,
            "daily_known_npz": str(out / "daily_known_late.npz"),
            "disclosure": DISCLOSURES["late_block"],
        },
        "decision": decision,
        "artifacts": {
            "contract": str(out / "contract.json"),
            "selection": str(out / "selection.json"),
            "collect_parts": str(out / "collect_parts"),
            "daily_known_validation_npz": str(out / "daily_known_validation.npz"),
            "daily_known_late_npz": str(out / "daily_known_late.npz"),
            "producer_snapshot": str(out / "producer_snapshot.py"),
        },
        "runtime_s": round(time.time() - t0, 1),
    }
    _json_atomic(out / "results.json", results)
    print(f"[decision] {decision}", flush=True)
    print(f"[done] results -> {out / 'results.json'} ({results['runtime_s']}s)", flush=True)


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--out", type=Path, default=None, help=f"output root (default: {OUTPUT})")
    p.add_argument(
        "--resume",
        action="store_true",
        help="resume collection from the intact per-day parts already on disk",
    )
    p.add_argument(
        "--skip-late",
        action="store_true",
        help="stop after the frozen contract, the validation block and the selection "
        "(no late traversal; the late days are not even collected)",
    )
    p.add_argument(
        "--days",
        nargs="+",
        default=None,
        help="restrict the analysis corpus to these days (debug)",
    )
    run(p.parse_args())


if __name__ == "__main__":
    main()
