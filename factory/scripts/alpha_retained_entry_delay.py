#!/usr/bin/env python3
"""Execution-delay decay study of the retained top-gainer mechanism (no refit).

QUESTION. The retained lead is the ONE stored h60 payoff model
(``learned/models/payoff_h60.joblib``, fit 2021-02..2022-12 on 26 causal features,
sha256 c5493c6c043a22e544ca98aa4fb5620292d4a1aeb370f63f6492907334f0bf96, never refit) scored
at threshold 0.030 on the liquidity-qualified, strictly past-only panel state ``t`` and
replayed under the retained repeat form (flat 15-minute cooldown after the ACTUAL exit, max
3 attempts per ticker per session, 3 x $1,000 book, one position per symbol, fees on both
legs, same-clock exits before buys) plus its once-per-day twin. This study moves exactly ONE
axis: the EXECUTION DELAY of the entry. The entry executes at the open of minute ``t + d``
for delay ``d in {0, 1, 2, 4, 8}`` minutes; ``d = 0`` is the frozen baseline's own minute-``t``
open (the state's last observed bar is ``t-1``, so the decision fires at the open of minute
``t`` itself and needs zero reaction time), and every added minute relaxes the reaction-time
and quote-timing requirement. Ten fixed cells = {once, repeat} x {0, 1, 2, 4, 8}; no HPO, no
refit, no new features, no threshold change, no cadence change, no second model.

LABELS are recomputed per cell from the day's own SIP net minute bars (the same tape the
panel was built from), never copied from the panel's stored entry: the entry price is the
open of the bar at the entry minute when it prints, else the NEXT available bar open at or
before session_end (a filled-open rule, recorded per row as ``filled_proxy`` vs
``filled_next_open``), else unfilled cash (never a guessed price, never an interpolation);
the exit is the last actual bar open at or before entry_minute + 60, UNKNOWN when the
ticker's tape stream is absent that day. EVERY fill is a MINUTE-OPEN PROXY on those bars -
these are NOT actual quotes and NOT actual exchange fills.

SELECTION (pre-declared, 2023 validation block only): rank the ten fixed cells by
KNOWN-contribution dollars per full calendar day at the 25 bps rung (the summed realized net
of the KNOWN fills divided by every calendar day of the block - no-signal days stay in the
denominator), ties broken by more known fills, then fewer attempts, then cell key. The choice
is frozen to ``contract.json`` and to ``results.json`` BEFORE any 2025-02..2026-05 outcome is
computed; that late block was already explored by earlier producers, so it is transparency
only and is NOT a pristine holdout. The 2025-02..2026-05 traversal reports the same ten
cells with the choice immutable.

COSTS are a COMPARISON SET, never a hurdle: 25/50/75/100/125/150 bps are TOTAL round-trip
modeled friction scenarios on the minute-open proxy prices; no rung closes a candidate and no
rung falsifies anything. 25..150 bps describe a LOW-FEE OPPORTUNITY set, not a fee claim - a
primary US broker's regular schedule (zero commission, SEC 20.6 per $1M, CAT 0.000003 per
side, daily cent rounding) is typically well under 1 bp on a $1,000 ticket, so actual
provider fees are sourced separately, not here. 200 bps is reported only as the historical
diagnostic the stored late ladder already carried. Returns are a RESEARCH NORMALIZATION on a
fixed $3,000 reserve (each session restarts from the same reserve), annualized by the simple
252-session convention - NOT a CAGR - with an additive fixed-ticket carry-equity check beside
it. UNKNOWN exits are charged one full ticket in a SEPARATELY LABELED full-loss lower bound
that is never blended into known contribution and never called an EV.

FROZEN REFERENCE is checked two ways, never assumed: (1) the d=0 cells replayed from the
PANEL's own stored h60 labels must reproduce the stored baseline artifacts field-for-field
(a hard gate: ``learned/surface_validation.json`` 60|0.03 for the once cadence and the
parent's ``learned_sparse_daily`` run for the repeat cadence, both blocks, 100 bps); (2) the
recomputed d=0 cell is compared row-by-row against those panel labels so the filled-open and
exit-minute gap rules are quantified (unfilled rows that now fill at the next open, exit
minutes that move inside a tape gap) instead of being silently absorbed.

Outputs -> ``~/alpha-data/open-search-v1/retained_entry_delay``: ``contract.json`` (the full
fixed grid and cost ladder, written before any outcome), ``results.json`` (surface, deltas,
selection, reproduction, decision), ``signal_parts/<day>.parquet`` + ``<day>.json``
(per-day atomic checkpoints with resume hashes), ``signals.parquet`` (the scored corpus),
``daily_known_*.npz`` (per-cell per-day known contribution). Protected days (2024, 2025-01,
2026-06..08) are never read; every day passes ``allowed()`` before any file is opened.

Usage:
  uv run --no-sync python factory/scripts/alpha_retained_entry_delay.py
      # outputs -> ~/alpha-data/open-search-v1/retained_entry_delay
  uv run --no-sync python factory/scripts/alpha_retained_entry_delay.py --resume
      # top up from the per-day parts already on disk
  uv run --no-sync python factory/scripts/alpha_retained_entry_delay.py --skip-late
      # validation + frozen selection only
  uv run --no-sync python factory/scripts/alpha_retained_entry_delay.py --out <dir>
      # relocate the lane root
  uv run --no-sync python factory/scripts/alpha_retained_entry_delay.py --days 2023-01-03 2023-01-04
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
from alpha_open_sim import CONTEXT_FEATURES, causal_liquidity, period

# ----- fixed configuration (no HPO, no refit) --------------------------------
STUDY = "alpha_retained_entry_delay"
STATUS = "DISCOVERY-NOT-VALIDATED"
MODEL_HORIZON = 60  # the one stored head that is ever loaded
MODEL_SHA256 = "c5493c6c043a22e544ca98aa4fb5620292d4a1aeb370f63f6492907334f0bf96"
THRESHOLD = 0.03  # unchanged retained admission bar
DELAYS = (0, 1, 2, 4, 8)  # minutes added to the state minute t; d=0 is the frozen entry
MAX_ATTEMPTS_ONCE = 1  # once-per-day cadence (the frozen control)
MAX_ATTEMPTS_REPEAT = 3  # retained repeat form: attempts per ticker per session
COOLDOWN_MIN = 15  # flat minutes after the ACTUAL exit
MAX_POSITIONS = 3  # one position per symbol, at most three at once
ORDER_BUDGET = 1000.0  # research ticket size (book only, not capacity)
RESERVE_USD = MAX_POSITIONS * ORDER_BUDGET
SELECT_COST_BPS = 25.0  # the pre-declared 2023 validation selection rung
REPRO_COST_BPS = 100.0  # the rung the stored frozen baseline cells were computed at
COST_SCENARIOS_BPS = (25.0, 50.0, 75.0, 100.0, 125.0, 150.0)
# Reported cost scenarios in bps (TOTAL round-trip modeled friction on the minute-open
# proxy prices). The ladder is a COMPARISON SET, never a hurdle: no scenario closes a
# candidate and no rung falsifies anything. 25..150 bps describe a LOW-FEE OPPORTUNITY
# set, not a fee claim - a primary US broker's regular schedule (zero commission, SEC
# 20.6 per $1M, CAT 0.000003 per side, daily cent rounding) is typically well under 1 bp
# on a $250-$1,000 ticket, so the actual broker fee is sourced separately, not here.
HISTORICAL_COST_BPS = 200.0  # historical diagnostic only, never a modern fee claim
LADDER = COST_SCENARIOS_BPS + (HISTORICAL_COST_BPS,)
TRADING_DAYS_PER_YEAR = 252  # session-count convention for $/year, NOT a CAGR
BARS_ROOT = ROOT / "data" / "sip" / "net" / "bars"
OUTPUT = PANEL_ROOT / "retained_entry_delay"
EXPECTED_DAYS = {VAL_PERIOD: 250, CONF_PERIOD: 332}
ANALYSIS_PERIODS = (VAL_PERIOD, CONF_PERIOD)
FLOAT_TOL = 1e-12
LABEL_SCHEMA = 1  # per-day part schema version pinned by the resume hash
PROGRESS_EVERY = 25  # collection progress cadence (days)
COST_SCENARIO_NATURE = (
    "each rung is a TOTAL modeled minute-proxy friction scenario on the proxy prices, "
    "NOT an actual broker fee: a primary US broker's regular schedule (zero commission, "
    "SEC 20.6 per $1M, CAT 0.000003 per side, daily cent rounding) is typically well "
    "under 1 bp on a $1,000 ticket, so the low rungs describe a LOW-FEE OPPORTUNITY set, "
    "not a fee claim; actual provider fees are sourced in the separate provider-fee ledger"
)
LABEL_RULES = {
    "entry_minute": (
        "t + delay with delay in {0,1,2,4,8}; d=0 is the frozen baseline's own minute-t "
        "open (the state's last observed bar is t-1, so the decision fires at the open "
        "of minute t and needs zero reaction time)"
    ),
    "entry_price": (
        "the open of the bar at the entry minute when present, else the NEXT available "
        "bar open at or before session_end (filled-open rule, recorded), else unfilled "
        "cash; never a guessed or interpolated price"
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
            "the ticker has no tape stream that day: never cash, never a guessed price"
        ),
    },
    "exit": "the last actual bar open at or before entry_minute + 60 (an actual print)",
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
}
CONTRACT_PAYLOAD = {
    "study": STUDY,
    "version": 1,
    "status": STATUS,
    "model": {
        "path": "learned/models/payoff_h60.joblib",
        "horizon": MODEL_HORIZON,
        "refit": False,
        "hpo": False,
        "expected_sha256": MODEL_SHA256,
    },
    "threshold": THRESHOLD,
    "delays": list(DELAYS),
    "cadences": {
        "once": {
            "max_attempts": MAX_ATTEMPTS_ONCE,
            "cooldown_min": COOLDOWN_MIN,
            "control": True,
        },
        "repeat": {
            "max_attempts": MAX_ATTEMPTS_REPEAT,
            "cooldown_min": COOLDOWN_MIN,
            "control": False,
        },
    },
    "book": {
        "max_positions": MAX_POSITIONS,
        "order_budget_usd": ORDER_BUDGET,
        "reserve_usd": RESERVE_USD,
        "leverage": False,
        "one_position_per_symbol": True,
        "fees_both_legs": True,
        "same_clock_exits_before_buys": True,
    },
    "cost_scenarios_bps": list(COST_SCENARIOS_BPS),
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
    "label_schema": LABEL_SCHEMA,
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


# ----- the fixed grid: two cadences x five delays -----------------------------
@dataclass(frozen=True)
class Cadence:
    """One admission/exit cadence. Same score, same threshold; only cadence differs."""

    name: str
    max_attempts: int
    cooldown_min: int
    control: bool = False


CADENCES = (
    Cadence("once", MAX_ATTEMPTS_ONCE, COOLDOWN_MIN, control=True),
    Cadence("repeat", MAX_ATTEMPTS_REPEAT, COOLDOWN_MIN),
)


@dataclass(frozen=True)
class LabelView:
    """Where one replay reads its entry/exit labels from."""

    status: str  # entry status column
    open: str  # entry open column
    gross: str  # realized gross payoff column
    exit_et: str  # exit minute column
    hold_minute: str  # the minute the position was actually held from
    unfilled: str  # the status value that means "no fill, cash"
    entry_et: str | None = None  # actual entry minute column (None => the state minute t)


def delayed_label_view(delay: int) -> LabelView:
    """Labels recomputed from the day's tape for one execution delay."""
    return LabelView(
        status=f"entry_status_{delay}",
        open=f"entry_open_{delay}",
        gross=f"gross_{delay}",
        exit_et=f"exit_et_{delay}",
        hold_minute=f"entry_et_{delay}",
        unfilled="unfilled_cash",
        entry_et=f"entry_et_{delay}",
    )


FROZEN_LABELS = LabelView(
    status="panel_entry_status",
    open="panel_entry_open",
    gross="panel_gross_60",
    exit_et="panel_exit_et_60",
    hold_minute="t",
    unfilled="unfilled_expired",
    entry_et=None,
)


@dataclass(frozen=True)
class Cell:
    """One fixed grid cell: a cadence at one execution delay."""

    key: str
    cadence: Cadence
    delay: int
    labels: LabelView


CELLS = tuple(Cell(f"{c.name}_d{d}", c, d, delayed_label_view(d)) for c in CADENCES for d in DELAYS)
FROZEN_CELLS = {c.name: Cell(f"frozen_{c.name}_d0", c, 0, FROZEN_LABELS) for c in CADENCES}

SIGNAL_TYPES = {
    "day": pl.String,
    "ticker": pl.String,
    "t": pl.Int64,
    "session_end": pl.Int64,
    "score": pl.Float64,
    "panel_entry_status": pl.String,
    "panel_entry_open": pl.Float64,
    "panel_gross_60": pl.Float64,
    "panel_exit_et_60": pl.Int64,
    "panel_exit_status_60": pl.String,
}
for _d in DELAYS:
    SIGNAL_TYPES.update(
        {
            f"entry_et_{_d}": pl.Int64,
            f"entry_open_{_d}": pl.Float64,
            f"entry_status_{_d}": pl.String,
            f"gross_{_d}": pl.Float64,
            f"exit_et_{_d}": pl.Int64,
            f"exit_status_{_d}": pl.String,
        }
    )
SIGNAL_COLUMNS = list(SIGNAL_TYPES)

# Columns kept from the liquidity-qualified panel frame (the panel's own stored h60 labels
# are carried only for the frozen reference reproduction and the gap-rule diagnostic).
PANEL_KEEP = [
    "day",
    "ticker",
    "t",
    "session_end",
    pl.col("entry_status").alias("panel_entry_status"),
    pl.col("entry_open").alias("panel_entry_open"),
    pl.col("gross_60").alias("panel_gross_60"),
    pl.col("exit_et_60").alias("panel_exit_et_60"),
    pl.col("exit_status_60").alias("panel_exit_status_60"),
    "score",
]

# Stored frozen baseline cells this study must reproduce when fed the panel's own labels.
# once  <- learned/surface_validation.json ["60|0.03"] (the rare-study surface cell)
# both  <- learned_sparse_daily/results.json (the parent's alpha_sparse_daily run)
EXPECTED_FROZEN = {
    "once": {
        "source": (
            "learned/surface_validation.json ['60|0.03'] (stored rare-study surface; "
            "identical to learned_sparse_daily/results.json validation.once_h60 @100)"
        ),
        VAL_PERIOD: {
            "days": 250,
            "attempts": 62,
            "fills": 61,
            "known_fills": 61,
            "unknown_fills": 0,
            "traded_days": 55,
            "n_signals": 107,
            "mean_daily_lower_bound": 0.0009073964119989598,
            "dollars_per_day": 2.722189235996879,
            "mean_net_known_fill": 0.011156513262282296,
            "skip_reasons": {"cooldown": 4, "max_attempts": 7, "slot_busy": 34},
            "cash_or_slot_skips": 0,
        },
        CONF_PERIOD: {
            "days": 332,
            "attempts": 143,
            "fills": 143,
            "known_fills": 143,
            "unknown_fills": 0,
            "traded_days": 124,
            "n_signals": 244,
            "mean_daily_lower_bound": 0.001416926248383037,
            "dollars_per_day": 4.250778745149111,
            "mean_net_known_fill": 0.009868940862863676,
            "skip_reasons": {"cooldown": 12, "max_attempts": 22, "slot_busy": 67},
            "cash_or_slot_skips": 0,
        },
    },
    "repeat": {
        "source": "learned_sparse_daily/results.json (the parent's alpha_sparse_daily run)",
        VAL_PERIOD: {
            "days": 250,
            "attempts": 68,
            "fills": 67,
            "known_fills": 67,
            "unknown_fills": 0,
            "traded_days": 55,
            "n_signals": 107,
            "mean_daily_lower_bound": 0.0009462389471762678,
            "dollars_per_day": 2.8387168415288033,
            "mean_net_known_fill": 0.010592227020629865,
            "skip_reasons": {"cooldown": 4, "slot_busy": 35},
            "cash_or_slot_skips": 0,
        },
        CONF_PERIOD: {
            "days": 332,
            "attempts": 157,
            "fills": 157,
            "known_fills": 157,
            "unknown_fills": 0,
            "traded_days": 124,
            "n_signals": 244,
            "mean_daily_lower_bound": 0.0018925893908656555,
            "dollars_per_day": 5.677768172596966,
            "mean_net_known_fill": 0.012006490657975754,
            "skip_reasons": {"cooldown": 12, "slot_busy": 75},
            "cash_or_slot_skips": 0,
        },
    },
}


# ----- labels recomputed from the day's own minute bars ------------------------
def compute_delay_labels(
    rows: list[dict],
    tapes: dict[str, tuple[np.ndarray, np.ndarray]],
    delay: int,
) -> tuple[dict[str, list], dict]:
    """One day's h60 payoff labels for one execution delay, read from the tape only.

    Entry: first actual bar open at/after the entry minute ``t + delay`` when that bar is
    at or before ``session_end`` (``filled_proxy`` when it is exactly the entry minute,
    ``filled_next_open`` when a tape gap pushed the fill to the next print, recorded);
    ``unfilled_cash`` when no bar prints from the entry minute through session_end;
    ``unknown_pending`` when the ticker has no tape stream that day (never cash, never a
    guessed price). Exit: the last actual bar open at or before ``entry_et + 60``;
    UNKNOWN only when the tape itself is absent. ``et`` must be ascending minute stamps.
    At delay 0 the row is also compared against the panel's own stored labels so the
    filled-open and exit-minute gap rules are quantified, never silently absorbed.
    """
    entry_et: list = []
    entry_open: list = []
    entry_status: list = []
    gross: list = []
    exit_et: list = []
    exit_status: list = []
    entry_counts: Counter = Counter()
    exit_counts: Counter = Counter()
    d0 = {
        "entry_transitions": Counter(),
        "exit_et_changed": 0,
        "exit_status_changed": 0,
        "gross_changed": 0,
        "gross_max_abs_delta": 0.0,
        "unfilled_to_filled": 0,
        "unfilled_stayed_cash": 0,
    }
    for r in rows:
        ticker = r["ticker"]
        tape = tapes.get(ticker)
        if tape is None:
            # No tape stream for this ticker on this day: unknowable, never a guess.
            entry_et.append(None)
            entry_open.append(None)
            entry_status.append("unknown_pending")
            gross.append(None)
            exit_et.append(None)
            exit_status.append("unknown_pending")
            entry_counts["unknown_pending"] += 1
            exit_counts["unknown_pending"] += 1
            if delay == 0:
                d0["entry_transitions"][f"{r['panel_entry_status']}->unknown_pending"] += 1
            continue
        et, opens = tape
        target = int(r["t"]) + delay
        session_end = int(r["session_end"])
        j = int(np.searchsorted(et, target, side="left"))
        if j >= len(et) or int(et[j]) > session_end:
            entry_et.append(None)
            entry_open.append(None)
            entry_status.append("unfilled_cash")
            gross.append(None)
            exit_et.append(None)
            exit_status.append("unfilled_cash")
            entry_counts["unfilled_cash"] += 1
            exit_counts["unfilled_cash"] += 1
            if delay == 0:
                d0["entry_transitions"][f"{r['panel_entry_status']}->unfilled_cash"] += 1
                if r["panel_entry_status"] == "unfilled_expired":
                    d0["unfilled_stayed_cash"] += 1
            continue
        e_et = int(et[j])
        e_open = float(opens[j])
        if not np.isfinite(e_open) or e_open <= 0:
            # A nonpositive/nonfinite print can never price a position: UNKNOWN, not cash.
            entry_et.append(None)
            entry_open.append(None)
            entry_status.append("unknown_pending")
            gross.append(None)
            exit_et.append(None)
            exit_status.append("unknown_pending")
            entry_counts["unknown_pending_invalid_open"] += 1
            exit_counts["unknown_pending"] += 1
            if delay == 0:
                d0["entry_transitions"][f"{r['panel_entry_status']}->unknown_pending"] += 1
            continue
        e_status = "filled_proxy" if e_et == target else "filled_next_open"
        entry_et.append(e_et)
        entry_open.append(e_open)
        entry_status.append(e_status)
        entry_counts[e_status] += 1
        k = int(np.searchsorted(et, e_et + MODEL_HORIZON, side="right")) - 1
        if k < 0:
            gross.append(None)
            exit_et.append(None)
            exit_status.append("unknown_pending")
            exit_counts["unknown_pending"] += 1
            continue
        g = float(opens[k]) / e_open - 1.0
        gross.append(g)
        exit_et.append(int(et[k]))
        exit_status.append("observed_open_proxy")
        exit_counts["observed_open_proxy"] += 1
        if delay == 0:
            panel_status = r["panel_entry_status"]
            d0["entry_transitions"][f"{panel_status}->{e_status}"] += 1
            if panel_status == "unfilled_expired":
                d0["unfilled_to_filled"] += 1
            if r["panel_exit_status_60"] != "observed_open_proxy":
                d0["exit_status_changed"] += 1
            panel_gross = r["panel_gross_60"]
            if r["panel_exit_status_60"] == "observed_open_proxy" and panel_gross is not None:
                if r["panel_exit_et_60"] is not None and int(r["panel_exit_et_60"]) != int(et[k]):
                    d0["exit_et_changed"] += 1
                if abs(float(panel_gross) - g) > FLOAT_TOL:
                    d0["gross_changed"] += 1
                    d0["gross_max_abs_delta"] = max(
                        d0["gross_max_abs_delta"], abs(float(panel_gross) - g)
                    )
    out = {
        f"entry_et_{delay}": entry_et,
        f"entry_open_{delay}": entry_open,
        f"entry_status_{delay}": entry_status,
        f"gross_{delay}": gross,
        f"exit_et_{delay}": exit_et,
        f"exit_status_{delay}": exit_status,
    }
    return out, {
        "entry_counts": dict(entry_counts),
        "exit_counts": dict(exit_counts),
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
    path = model_dir / f"payoff_h{MODEL_HORIZON}.joblib"
    if not path.exists():
        raise SystemExit(f"[{STUDY}] stored model missing: {path}")
    bundle = joblib.load(path)
    order = list(bundle.get("feature_order") or [])
    if order != list(FEATURES_ALL):
        raise SystemExit(f"[{STUDY}] stored feature_order != current FEATURES_ALL")
    if int(bundle.get("horizon", -1)) != MODEL_HORIZON:
        raise SystemExit(f"[{STUDY}] stored horizon {bundle.get('horizon')} != {MODEL_HORIZON}")
    sha = sha256_file(path)
    if sha != MODEL_SHA256:
        raise SystemExit(f"[{STUDY}] stored model sha256 {sha} != pinned {MODEL_SHA256}")
    return bundle, path


# ----- memory-bounded per-day collection with atomic resume -------------------
def day_context(frame: pl.DataFrame) -> pl.DataFrame:
    """Re-derive the four past-only peer-context columns the stored model needs.

    ``alpha_open_sim.load_panel`` computes them at load time over ``over(["day","t"])``
    windows, so the per-day panel files do not carry them. Recomputing them per day on
    the SAME partition key reproduces the exact values (the window is entirely inside one
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


def _empty_signal_frame() -> pl.DataFrame:
    return pl.DataFrame(schema=dict(SIGNAL_TYPES))


def _empty_day_coverage() -> dict:
    return {
        "rows_scored": 0,
        "rows_kept": 0,
        "tape_day_file_present": True,
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


def collect_day(day: str, model) -> tuple[pl.DataFrame, dict]:
    """Score one session ONCE and recompute every delay's labels from its own tape.

    One panel frame, one tape frame and one day's signals are resident at a time; the full
    corpus is never concatenated here. The thr-clearing states are the SAME candidate set
    for all ten cells - a delay can only change where the entry price comes from, never
    what the state means or which states clear the bar.
    """
    if not allowed(day):
        raise ValueError(f"protected/out-of-scope day refused: {day}")
    frame = pl.read_parquet(PANEL_ROOT / "days" / f"{day}.parquet")
    frame = day_context(frame)
    cand = frame.filter(causal_liquidity(frame))
    cov = _empty_day_coverage()
    cov["rows_scored"] = int(cand.height)
    if not cand.height:
        return _empty_signal_frame(), cov
    pred = np.asarray(model.predict(feature_matrix(cand)), dtype=float)
    scored = cand.with_columns(pl.Series("score", pred))
    sig = scored.filter(pl.col("score") >= THRESHOLD).select(PANEL_KEEP)
    cov["rows_kept"] = int(sig.height)
    if not sig.height:
        return _empty_signal_frame(), cov
    tickers = sorted(set(sig["ticker"].to_list()))
    tapes, present = load_day_opens(day, tickers)
    cov["tape_day_file_present"] = present
    cov["missing_symbol_streams"] = sorted({t for t in sig["ticker"].to_list() if t not in tapes})
    rows = sig.to_dicts()
    series: list[pl.Series] = []
    for delay in DELAYS:
        out, stats = compute_delay_labels(rows, tapes, delay)
        cov["entry_counts_by_delay"][str(delay)] = stats["entry_counts"]
        cov["exit_counts_by_delay"][str(delay)] = stats["exit_counts"]
        if delay == 0:
            cov["d0_vs_panel"] = stats["d0_vs_panel"]
        for name, values in out.items():
            series.append(pl.Series(name, values, dtype=SIGNAL_TYPES[name]))
    sig = sig.with_columns(series).select(SIGNAL_COLUMNS).sort(["ticker", "t"])
    return sig, cov


def _merge_coverage(coverage: dict, cov: dict) -> None:
    coverage["rows_scored"] += int(cov.get("rows_scored", 0))
    coverage["rows_kept"] += int(cov.get("rows_kept", 0))
    if cov.get("tape_day_file_present") is False:
        coverage["missing_tape_day_files"].append(cov.get("day", "?"))
    for ticker in cov.get("missing_symbol_streams") or []:
        coverage["missing_ticker_stream_counts"][ticker] = (
            coverage["missing_ticker_stream_counts"].get(ticker, 0) + 1
        )
    for d in DELAYS:
        entry = (cov.get("entry_counts_by_delay") or {}).get(str(d), {})
        exit_ = (cov.get("exit_counts_by_delay") or {}).get(str(d), {})
        coverage["entry_counts_by_delay"][str(d)].update(entry)
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


def collect_signals(
    model,
    days: list[str],
    out_dir: Path,
    resume: bool,
    producer: str,
    contract_sha: str,
    model_sha: str,
) -> tuple[pl.DataFrame, dict]:
    """Score the panel ONCE per day and keep only thr-clearing states, resumably.

    Each finished day is written as an atomic per-day part plus a JSON sidecar whose
    ``resume_hash`` binds the producer sha, the contract sha, the model sha, the day and
    the schema version, so a resumed run reuses exactly the days that still match and
    recomputes everything else. Bounded memory: one day of every frame at a time.
    """
    parts_dir = out_dir / "signal_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)

    def expect(day: str) -> str:
        return _digest_bytes(
            {
                "day": day,
                "producer": producer,
                "contract": contract_sha,
                "model": model_sha,
                "schema": LABEL_SCHEMA,
            }
        )

    coverage = {
        "days_total": len(days),
        "days_cached": 0,
        "days_collected": 0,
        "rows_scored": 0,
        "rows_kept": 0,
        "missing_tape_day_files": [],
        "missing_ticker_stream_counts": {},
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
    todo: list[str] = []
    for day in days:
        if not allowed(day):
            raise ValueError(f"protected/out-of-scope day refused: {day}")
        part = parts_dir / f"{day}.parquet"
        side = parts_dir / f"{day}.json"
        fresh = False
        prior: dict = {}
        if resume and part.exists() and side.exists():
            try:
                prior = json.loads(side.read_text())
                fresh = (
                    prior.get("resume_hash") == expect(day)
                    and prior.get("schema") == LABEL_SCHEMA
                    and prior.get("model_sha256") == model_sha
                )
            except (json.JSONDecodeError, OSError):
                fresh = False
        if fresh:
            coverage["days_cached"] += 1
            _merge_coverage(coverage, prior.get("coverage") or {})
        else:
            todo.append(day)
    for n, day in enumerate(todo, 1):
        if not allowed(day):
            raise ValueError(f"protected/out-of-scope day refused: {day}")
        sig, cov = collect_day(day, model)
        cov["day"] = day
        part = parts_dir / f"{day}.parquet"
        tmp = part.with_name(f"{part.name}.tmp{os.getpid()}")
        sig.write_parquet(tmp)
        os.replace(tmp, part)
        _json_atomic(
            parts_dir / f"{day}.json",
            {
                "day": day,
                "rows": int(sig.height),
                "resume_hash": expect(day),
                "producer_sha256": producer,
                "contract_sha256": contract_sha,
                "model_sha256": model_sha,
                "schema": LABEL_SCHEMA,
                "coverage": cov,
            },
        )
        _merge_coverage(coverage, cov)
        if n % PROGRESS_EVERY == 0 or n == len(todo):
            print(
                f"[collect] {n}/{len(todo)} days, {coverage['rows_kept']} signals (last {day})",
                flush=True,
            )
    parts = []
    for day in days:
        part = parts_dir / f"{day}.parquet"
        if part.exists():
            parts.append(part)
    frames = [pl.read_parquet(p) for p in parts]
    signals = (
        pl.concat(frames, how="vertical").sort(
            ["day", "t", "score", "ticker"], descending=[False, False, True, False]
        )
        if frames
        else _empty_signal_frame()
    )
    for f in frames:
        del f
    coverage["days_collected"] = len(todo)
    coverage["entry_counts_by_delay"] = {
        k: dict(v) for k, v in coverage["entry_counts_by_delay"].items()
    }
    coverage["exit_counts_by_delay"] = {
        k: dict(v) for k, v in coverage["exit_counts_by_delay"].items()
    }
    return signals, coverage


# ----- funded-reserve replay: the delay is the only axis -----------------------
def replay_block(
    signals: pl.DataFrame,
    days: list[str],
    cell: Cell,
    cost_bps: float,
    block: str,
    n_signals: int,
) -> tuple[dict, list[dict]]:
    """Replay one cell on one block under the shared funded-reserve conventions.

    Long-only, one position per symbol, fees on BOTH legs, same-clock exits precede buys,
    no leverage: a symbol may not be re-entered until its ACTUAL exit minute plus the
    cadence's flat cooldown. An UNKNOWN exit keeps its slot and blocks its ticker for the
    rest of the session and is charged a full unit in the separately labeled lower bound;
    an unfilled entry reserves its ticket at the decision minute and releases it one minute
    after its own (delayed) entry minute, fee-free, still counting as an attempt with no
    cooldown. Every calendar day is replayed, so no-signal days stay in the denominator.

    The ONLY thing a delay changes is where the entry price and the exit anchor come from:
    the ticket is reserved at the decision minute ``t`` exactly as in the frozen baseline,
    the intent is funded by descending score at the same minute, and the cooldown is
    anchored to the ACTUAL (delayed) exit minute.
    """
    labels = cell.labels
    gcol, xcol, scol, ocol, hcol = (
        labels.gross,
        labels.exit_et,
        labels.status,
        labels.open,
        labels.hold_minute,
    )
    if not signals.height:
        signals = pl.DataFrame(
            {
                c: pl.Series([], dtype=SIGNAL_TYPES[c])
                for c in (gcol, xcol, scol, ocol, hcol, "day", "ticker", "t", "session_end")
            }
        )
    missing = [c for c in (gcol, xcol, scol, ocol, hcol) if c not in signals.columns]
    if missing:
        raise ValueError(f"[{STUDY}] {cell.key} signals lack {missing}")
    ordered = signals.sort(["day", "t", "score", "ticker"], descending=[False, False, True, False])
    by_day = ordered.partition_by("day", as_dict=True)
    side = cost_bps / 20_000.0
    trades: list[dict] = []
    daily: list[dict] = []
    skips: Counter = Counter()
    reserve = RESERVE_USD
    equity = reserve
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
                if int(used.get(ticker, 0)) >= cell.cadence.max_attempts:
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
                if r[scol] == labels.unfilled:
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
                blocked[ticker] = release + cell.cadence.cooldown_min
                heapq.heappush(queue, (release, seq, ticker, proceeds, True))
                seq += 1
                fills += 1
                entry_notional += ORDER_BUDGET
                exit_notional += proceeds
                if net is not None:
                    known_nets.append(net)
                actual_entry = t
                if labels.entry_et:
                    raw_entry = r[labels.entry_et]
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
                        "cadence": cell.cadence.name,
                        "delay": cell.delay,
                        "block": block,
                        "positions_at_entry": len(active),
                    }
                )
        lower_pnl = known_pnl - ORDER_BUDGET * unknown
        equity += lower_pnl
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
                "carry_equity": equity,
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
        "cadence": cell.cadence.name,
        "delay": cell.delay,
        "max_attempts": cell.cadence.max_attempts,
        "cooldown_min": cell.cadence.cooldown_min,
        "control": cell.cadence.control and cell.delay == 0,
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
        "carry_equity_end_usd": daily[-1]["carry_equity"] if daily else reserve,
        "carry_equity_return": ((daily[-1]["carry_equity"] / reserve - 1.0) if daily else None),
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
        "daily": daily,
    }
    return metrics, trades


def cell_view(metrics: dict) -> dict:
    """Stored per-cell view without the bulky per-day list (npz keeps the series)."""
    return {k: v for k, v in metrics.items() if k != "daily"}


def block_signals(signals: pl.DataFrame, days: list[str]) -> int:
    """Candidate intents inside one block; the corpus count would cross blocks."""
    return int(signals.filter(pl.col("day").is_in(days)).height) if signals.height else 0


# ----- frozen reference: exact reproduction + named gap rules ------------------
def compare_frozen(name: str, block: str, view: dict) -> dict:
    """Field-by-field reproduction check of a stored frozen baseline cell."""
    exp = EXPECTED_FROZEN[name][block]
    skips = view.get("no_order_skips") or {}
    pairs = {
        "days": (exp["days"], view.get("days")),
        "attempts": (exp["attempts"], view.get("attempts")),
        "fills": (exp["fills"], view.get("fills")),
        "known_fills": (exp["known_fills"], view.get("known_fills")),
        "unknown_fills": (exp["unknown_fills"], view.get("unknown_fills")),
        "traded_days": (exp["traded_days"], view.get("traded_days")),
        "n_signals": (exp["n_signals"], view.get("n_signals")),
        "cash_or_slot_skips": (exp["cash_or_slot_skips"], skips.get("cash_or_slot", 0)),
        "skip_reasons": (dict(exp["skip_reasons"]), dict(skips)),
        "mean_daily_lower_bound": (
            exp["mean_daily_lower_bound"],
            view["full_loss_lower_bound"]["mean_daily_lower_bound_return"],
        ),
        "dollars_per_day": (
            exp["dollars_per_day"],
            view["full_loss_lower_bound"]["lower_bound_usd_per_day"],
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


def gap_effect(frozen_by_rung: dict, recomputed_by_rung: dict) -> dict:
    """Frozen panel-label d=0 cell vs the recomputed d=0 cell, rung by rung."""
    out = {}
    for rung, fv in frozen_by_rung.items():
        rv = recomputed_by_rung[rung]
        f_day = fv["known_contribution_usd_per_day"]
        r_day = rv["known_contribution_usd_per_day"]
        out[rung] = {
            "frozen_known_fills": fv["known_fills"],
            "recomputed_d0_known_fills": rv["known_fills"],
            "delta_known_fills": rv["known_fills"] - fv["known_fills"],
            "frozen_known_usd_per_day": f_day,
            "recomputed_d0_known_usd_per_day": r_day,
            "delta_known_usd_per_day": (
                (r_day - f_day) if f_day is not None and r_day is not None else None
            ),
            "frozen_mean_net_known_fill": fv["mean_net_known_fill"],
            "recomputed_d0_mean_net_known_fill": rv["mean_net_known_fill"],
            "frozen_traded_days": fv["traded_days"],
            "recomputed_d0_traded_days": rv["traded_days"],
            "frozen_attempts": fv["attempts"],
            "recomputed_d0_attempts": rv["attempts"],
            "note": (
                "the recomputed d=0 cell applies the prescribed filled-open entry and "
                "last-bar-at-or-before-exit rules; every difference from the frozen "
                "panel-label cell is a named gap rule, quantified row-by-row in "
                "coverage.d0_vs_panel"
            ),
        }
    return out


def frozen_reference_block(
    signals: pl.DataFrame,
    days: list[str],
    block: str,
    surface: dict,
) -> tuple[dict, dict]:
    """Panel-label d=0 replays for both cadences on one block, with the hard gate."""
    n_sig = block_signals(signals, days)
    out: dict[str, dict] = {}
    for name, fcell in FROZEN_CELLS.items():
        per_rung: dict[str, dict] = {}
        for cost in LADDER:
            metrics, _ = replay_block(signals, days, fcell, cost, block, n_sig)
            per_rung[str(int(cost))] = cell_view(metrics)
        check = compare_frozen(name, block, per_rung[str(int(REPRO_COST_BPS))])
        if not check["all_match"]:
            bad = {k: v for k, v in check.items() if k != "all_match" and not v.get("match")}
            raise SystemExit(
                f"[{STUDY}] frozen {name} d=0 panel-label replay does not reproduce the "
                f"stored {block} cell: {bad}"
            )
        out[name] = {
            "source": EXPECTED_FROZEN[name]["source"],
            "replayed": per_rung,
            "reproduction_check": check,
            "gap_rule_effect_vs_recomputed_d0": gap_effect(per_rung, surface[f"{name}_d0"]),
        }
        fv = per_rung[str(int(REPRO_COST_BPS))]
        print(
            f"[frozen] {name}_d0 panel-label replay reproduces the stored {block} cell "
            f"exactly ({fv['known_fills']} known fills, "
            f"{_fmt(fv['full_loss_lower_bound']['mean_daily_lower_bound_return'])}/day "
            f"@{int(REPRO_COST_BPS)})",
            flush=True,
        )
    return out, n_sig


# ----- selection + decay surfaces ----------------------------------------------
def rank_cells(surface: dict) -> list[Cell]:
    """Rank the ten fixed cells by validation known-contribution $/calendar day at the
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


def selection_section(ranked: list[Cell], surface: dict) -> dict:
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
        "status": STATUS,
        "ranking": [
            {
                "rank": i,
                "cell_key": cell.key,
                "cadence": cell.cadence.name,
                "delay": cell.delay,
                "known_contribution_usd_per_day": surface[cell.key][rung][
                    "known_contribution_usd_per_day"
                ],
                "known_contribution_usd_per_year_252": surface[cell.key][rung][
                    "known_contribution_usd_per_year_252"
                ],
                "known_fills": surface[cell.key][rung]["known_fills"],
                "unknown_fills": surface[cell.key][rung]["unknown_fills"],
                "unfilled_cash_attempts": surface[cell.key][rung]["unfilled_cash_attempts"],
                "attempts": surface[cell.key][rung]["attempts"],
                "traded_days": surface[cell.key][rung]["traded_days"],
                "mean_net_known_fill": surface[cell.key][rung]["mean_net_known_fill"],
            }
            for i, cell in enumerate(ranked, 1)
        ],
        "chosen": {
            "cell_key": chosen.key,
            "cadence": chosen.cadence.name,
            "delay": chosen.delay,
            "entry_minute": f"t+{chosen.delay}",
            "max_attempts": chosen.cadence.max_attempts,
            "cooldown_min": chosen.cadence.cooldown_min,
            "known_contribution_usd_per_day": c["known_contribution_usd_per_day"],
            "known_contribution_usd_per_year_252": c["known_contribution_usd_per_year_252"],
            "known_fills": c["known_fills"],
            "traded_days": c["traded_days"],
            "mean_net_known_fill": c["mean_net_known_fill"],
        },
    }


def decay_table(surface: dict) -> dict:
    """Per cadence: every delay at every rung, with the explicit delta vs that cadence's
    d=0 cell (the frozen reference inside this study)."""
    out: dict[str, dict] = {}
    for cadence in CADENCES:
        base_key = f"{cadence.name}_d0"
        per_delay: dict[str, dict] = {}
        for delay in DELAYS:
            key = f"{cadence.name}_d{delay}"
            per_rung: dict[str, dict] = {}
            for cost in LADDER:
                rung = str(int(cost))
                cell_rung = surface[key][rung]
                base_rung = surface[base_key][rung]
                cell_day = cell_rung["known_contribution_usd_per_day"]
                base_day = base_rung["known_contribution_usd_per_day"]
                per_rung[rung] = {
                    "known_fills": cell_rung["known_fills"],
                    "unknown_fills": cell_rung["unknown_fills"],
                    "unfilled_cash_attempts": cell_rung["unfilled_cash_attempts"],
                    "attempts": cell_rung["attempts"],
                    "traded_days": cell_rung["traded_days"],
                    "mean_net_known_fill": cell_rung["mean_net_known_fill"],
                    "known_contribution_usd_per_day": cell_day,
                    "known_contribution_usd_per_year_252": cell_rung[
                        "known_contribution_usd_per_year_252"
                    ],
                    "delta_known_fills_vs_d0": cell_rung["known_fills"] - base_rung["known_fills"],
                    "delta_known_usd_per_day_vs_d0": (
                        (cell_day - base_day)
                        if cell_day is not None and base_day is not None
                        else None
                    ),
                    "delta_annual_usd_vs_d0": (
                        (cell_day - base_day) * TRADING_DAYS_PER_YEAR
                        if cell_day is not None and base_day is not None
                        else None
                    ),
                    "retained_share_of_d0_known_usd_per_day": (
                        (cell_day / base_day)
                        if cell_day is not None and base_day not in (None, 0.0)
                        else None
                    ),
                }
            per_delay[str(delay)] = per_rung
        out[cadence.name] = {"reference_cell": base_key, "by_delay": per_delay}
    return out


def cadence_spread(surface: dict) -> dict:
    """Repeat minus once at the same delay and rung (fills, $/day)."""
    out: dict[str, dict] = {}
    for delay in DELAYS:
        per_rung: dict[str, dict] = {}
        for cost in LADDER:
            rung = str(int(cost))
            once = surface[f"once_d{delay}"][rung]["known_contribution_usd_per_day"]
            repeat = surface[f"repeat_d{delay}"][rung]["known_contribution_usd_per_day"]
            per_rung[rung] = {
                "once_known_usd_per_day": once,
                "repeat_known_usd_per_day": repeat,
                "repeat_minus_once_usd_per_day": (
                    (repeat - once) if once is not None and repeat is not None else None
                ),
                "once_known_fills": surface[f"once_d{delay}"][rung]["known_fills"],
                "repeat_known_fills": surface[f"repeat_d{delay}"][rung]["known_fills"],
                "once_traded_days": surface[f"once_d{delay}"][rung]["traded_days"],
                "repeat_traded_days": surface[f"repeat_d{delay}"][rung]["traded_days"],
            }
        out[str(delay)] = per_rung
    return out


# ----- block reporting ---------------------------------------------------------
def print_cell_line(tag: str, key: str, by_rung: dict) -> None:
    sel = by_rung[str(int(SELECT_COST_BPS))]
    ladder = " ".join(
        f"{r}:{_fmt(by_rung[r]['known_contribution_usd_per_day'], '+.2f')}"
        for r in sorted(by_rung, key=float)
    )
    print(
        f"[{tag}] {key:<12} $/day {ladder} known={sel['known_fills']} "
        f"unk={sel['unknown_fills']} unfilled={sel['unfilled_cash_attempts']} "
        f"no_order={sel['no_order_skips_total']} traded={sel['traded_days']}/{sel['days']} "
        f"attempts={sel['attempts']}",
        flush=True,
    )


def decision_text(
    chosen: Cell,
    val_surface: dict,
    late_surface: dict | None,
    val_decay: dict,
    late_decay: dict | None,
) -> str:
    rung = str(int(SELECT_COST_BPS))
    c = val_surface[chosen.key][rung]
    base_key = f"{chosen.cadence.name}_d0"
    base = val_surface[base_key][rung]

    def shares(decay: dict) -> str:
        node = decay[chosen.cadence.name]["by_delay"][str(chosen.delay)]
        parts = []
        for r in sorted(node, key=float):
            s = node[r]["retained_share_of_d0_known_usd_per_day"]
            parts.append(f"{int(float(r))}bps={'n/a' if s is None else format(s, '.3f')}")
        return " ".join(parts)

    text = (
        f"{STATUS}: {chosen.key} selected by 2023 validation known-contribution $/calendar "
        f"day at {int(SELECT_COST_BPS)}bps ({_fmt(c['known_contribution_usd_per_day'], '+.2f')} "
        f"$/day, {c['known_fills']} known fills on {c['traded_days']} traded days of "
        f"{c['days']}); frozen d=0 reference {base_key} "
        f"{_fmt(base['known_contribution_usd_per_day'], '+.2f')} $/day "
        f"({base['known_fills']} known fills); retained share of the d=0 $/day by rung: "
        f"{shares(val_decay)}."
    )
    if late_surface is not None:
        lc = late_surface[chosen.key][rung]
        lb = late_surface[base_key][rung]
        text += (
            f" Late block (2025-02..2026-05, previously explored, transparency only): "
            f"{_fmt(lc['known_contribution_usd_per_day'], '+.2f')} $/day vs "
            f"{_fmt(lb['known_contribution_usd_per_day'], '+.2f')} $/day at d=0, "
            f"{lc['known_fills']} known fills on {lc['traded_days']} traded days of "
            f"{lc['days']}; retained share by rung: "
            + (shares(late_decay) if late_decay is not None else "n/a")
            + "."
        )
    else:
        text += " Late block not run (--skip-late)."
    return text


def analysis_blocks(days_arg, need_late: bool) -> dict[str, list[str]]:
    files = sorted(
        p.stem for p in (PANEL_ROOT / "days").glob("????-??-??.parquet") if allowed(p.stem)
    )
    keep = set(days_arg) if days_arg else None
    blocks: dict[str, list[str]] = {}
    for day in files:
        if keep is not None and day not in keep:
            continue
        p = period(day)
        if p in ANALYSIS_PERIODS:
            blocks.setdefault(p, []).append(day)
    if keep is None:
        for p, expected in EXPECTED_DAYS.items():
            if p == CONF_PERIOD and not need_late:
                continue
            if len(blocks.get(p, [])) != expected:
                raise SystemExit(
                    f"[{STUDY}] requires {expected} {p} days, got {len(blocks.get(p, []))}"
                )
    if not blocks.get(VAL_PERIOD):
        raise SystemExit(
            f"[{STUDY}] no {VAL_PERIOD} days selected; the $/day selection objective "
            "requires the 2023 validation block"
        )
    return blocks


# ----- main pipeline -----------------------------------------------------------
def run(args: argparse.Namespace) -> None:
    t0 = time.time()
    out = args.out or OUTPUT
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")

    # 1) ONE stored model, loaded and scored (never fitted).
    bundle, model_path = load_stored_model(PANEL_ROOT / "learned" / "models")
    model_sha = sha256_file(model_path)
    producer = _producer_sha256()
    contract_sha = _digest_bytes(CONTRACT_PAYLOAD)
    print(
        f"[model] h{MODEL_HORIZON} {model_path} (sha256 {model_sha[:12]}), "
        f"seed={bundle.get('seed')} clip={bundle.get('clip_fit')}",
        flush=True,
    )
    blocks = analysis_blocks(args.days, need_late=not args.skip_late)
    val_days = blocks.get(VAL_PERIOD, [])
    conf_days = blocks.get(CONF_PERIOD, [])
    collect_days = sorted(set(val_days) | (set(conf_days) if not args.skip_late else set()))
    print(
        f"[blocks] validation={len(val_days)} days, late={len(conf_days)} days, "
        f"collect={len(collect_days)} days (skip_late={args.skip_late})",
        flush=True,
    )

    # 2) one scoring pass over the analysis corpus; per-day atomic parts, resumable.
    signals, coverage = collect_signals(
        bundle["lgbm"], collect_days, out, args.resume, producer, contract_sha, model_sha
    )
    signals_file = out / "signals.parquet"
    signals.write_parquet(signals_file)
    print(
        f"[collect] {signals.height} thr-clearing states on {len(collect_days)} days "
        f"(missing tape days {len(coverage['missing_tape_day_files'])}, missing ticker "
        f"streams {sum(coverage['missing_ticker_stream_counts'].values())})",
        flush=True,
    )

    # 3) the frozen contract: the full fixed grid + the cost ladder, BEFORE any outcome.
    contract = {
        "study": STUDY,
        "label": (
            "execution-delay decay of the retained top-gainer mechanism: the same "
            "immutable h60 model, the same 0.030 threshold and the retained repeat / "
            "once cadences on a 3 x $1,000 book, with the entry delayed d in "
            "{0,1,2,4,8} minutes past the state minute t (d=0 = the frozen baseline's "
            "own minute-t open)"
        ),
        "status": STATUS,
        "frozen_before_any_outcome": True,
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "grid": {
            "cells": [
                {
                    "key": c.key,
                    "cadence": c.cadence.name,
                    "delay": c.delay,
                    "entry_minute": f"t+{c.delay}",
                    "max_attempts": c.cadence.max_attempts,
                    "cooldown_min": c.cadence.cooldown_min,
                    "control": c.cadence.control and c.delay == 0,
                }
                for c in CELLS
            ],
            "n_cells": len(CELLS),
            "delays": list(DELAYS),
            "only_axis": "execution delay of the entry minute",
        },
        "fixed": CONTRACT_PAYLOAD,
        "cost_ladder_bps": [float(c) for c in LADDER],
        "cost_scenarios_bps": [float(c) for c in COST_SCENARIOS_BPS],
        "historical_diagnostic_cost_bps": [HISTORICAL_COST_BPS],
        "cost_ladder_is_comparison_not_hurdle": True,
        "selection": {
            "block": VAL_PERIOD,
            "cost_bps": SELECT_COST_BPS,
            "objective": CONTRACT_PAYLOAD["selection_rule"]["objective"],
            "tie_break": CONTRACT_PAYLOAD["selection_rule"]["tie_break"],
            "frozen_before_late_traversal": True,
        },
        "frozen_reference": {
            "definition": (
                "the d=0 cells replayed from the PANEL's own stored h60 labels (entry at "
                "the open of minute t, no bar at t => unfilled_expired cash, exit = first "
                "actual open >= min(t+60, session_end)) - exactly the frozen baseline's "
                "labels and replay"
            ),
            "expected": EXPECTED_FROZEN,
        },
        "model": {
            "refit": False,
            "hpo": False,
            "feature_changes": False,
            "horizons_scored": [MODEL_HORIZON],
            "scored_once_over_analysis_corpus": True,
            "fit_block_2021_2022_scored": False,
            "fit_block_note": (
                "the 2021-2022 fit block is never replayed (weights are frozen), so it "
                "is not scored; the single prediction pass is shared by all ten cells"
            ),
            "stored_path": str(model_path),
            "stored_sha256": model_sha,
            "feature_order": FEATURES_ALL,
            "stored_params": bundle.get("params"),
            "clip_fit": bundle.get("clip_fit"),
            "seed": bundle.get("seed"),
        },
        "replay": {
            "engine": f"{STUDY}.replay_block (shared funded-reserve conventions)",
            "reserve_usd": RESERVE_USD,
            "max_positions": MAX_POSITIONS,
            "order_budget": ORDER_BUDGET,
            "leverage": False,
            "fees_both_legs": True,
            "one_position_per_symbol": True,
            "same_clock_exits_before_buys": True,
            "cooldown_rule": (
                "no reopening until the ACTUAL exit minute + cooldown_min; a "
                "pending/UNKNOWN exit keeps its slot and blocks its ticker for the rest "
                "of the session"
            ),
            "unknown_exit_rule": (
                "full-unit lower-bound charge at the day's end, never cash, separately "
                "labeled and never called an EV"
            ),
            "unfilled_rule": (
                "the ticket is reserved at the decision minute and released one minute "
                "after the delayed entry minute, fee-free, still counted as an attempt, "
                "no cooldown"
            ),
            "all_calendar_days_retained": True,
            "daily_reset_is_not_self_financing_cagr": True,
            "carry_equity_check": "additive fixed-ticket equity, no compounding",
        },
        "labels": LABEL_RULES,
        "disclosures": DISCLOSURES,
        "coverage": coverage,
        "provenance": {
            "panel_root": str(PANEL_ROOT),
            "bars_root": str(BARS_ROOT),
            "producer_sha256": producer,
            "producer_snapshot": str(out / "producer_snapshot.py"),
            "contract_payload_sha256": contract_sha,
            "resume_hash_recipe": (
                "sha256({day, producer, contract_payload_sha256, model_sha256, schema})"
            ),
            "analysis_days": len(collect_days),
            "validation_days": len(val_days),
            "late_days": len(conf_days),
            "signals_kept": int(signals.height),
        },
    }
    _json_atomic(out / "contract.json", contract)
    print(f"[freeze] contract -> {out / 'contract.json'} (before any outcome)", flush=True)

    # 4) 2023 validation: ten cells x the cost ladder, no median/tail/count gating.
    n_val = block_signals(signals, val_days)
    val_surface: dict[str, dict[str, dict]] = {}
    val_daily: dict[str, np.ndarray] = {}
    for cell in CELLS:
        val_surface[cell.key] = {}
        daily_sel = None
        for cost in LADDER:
            metrics, _ = replay_block(signals, val_days, cell, cost, VAL_PERIOD, n_val)
            val_surface[cell.key][str(int(cost))] = cell_view(metrics)
            if cost == SELECT_COST_BPS:
                daily_sel = np.asarray([d["known_pnl"] for d in metrics["daily"]], dtype=float)
        val_daily[cell.key] = daily_sel
        print_cell_line("val", cell.key, val_surface[cell.key])
    np.savez(out / "daily_known_validation.npz", **val_daily)

    # 5) the frozen reference on the validation block: exact reproduction is a gate.
    frozen_val, _ = frozen_reference_block(signals, val_days, VAL_PERIOD, val_surface)
    d0p = coverage["d0_vs_panel"]
    print(
        f"[frozen-gap] d=0 recomputed vs panel labels: unfilled->filled rows "
        f"{d0p['unfilled_to_filled']}, exit-minute changes {d0p['exit_et_changed']}, "
        f"gross changes {d0p['gross_changed']} (max |delta| "
        f"{d0p['gross_max_abs_delta']:.3e})",
        flush=True,
    )
    for name in FROZEN_CELLS:
        g = frozen_val[name]["gap_rule_effect_vs_recomputed_d0"][str(int(REPRO_COST_BPS))]
        print(
            f"[frozen-gap] {name}_d0 @{int(REPRO_COST_BPS)}: frozen "
            f"{_fmt(g['frozen_known_usd_per_day'], '+.4f')} vs recomputed "
            f"{_fmt(g['recomputed_d0_known_usd_per_day'], '+.4f')} $/day "
            f"(delta {_fmt(g['delta_known_usd_per_day'], '+.4f')}, fills "
            f"{g['frozen_known_fills']}->{g['recomputed_d0_known_fills']})",
            flush=True,
        )

    # 6) the pre-declared selection, frozen here BEFORE any late outcome exists.
    ranked = rank_cells(val_surface)
    chosen = ranked[0]
    selection = selection_section(ranked, val_surface)
    val_decay = decay_table(val_surface)
    val_spread = cadence_spread(val_surface)
    sel_cell = val_surface[chosen.key][str(int(SELECT_COST_BPS))]
    print(
        f"[select] {int(SELECT_COST_BPS)}bps validation choice {chosen.key} by "
        f"known-contribution $/calendar day "
        f"({_fmt(sel_cell['known_contribution_usd_per_day'], '+.2f')} $/day) "
        f"of {len(CELLS)} cells; no count/median/CI gate applied",
        flush=True,
    )

    if args.skip_late:
        results = {
            "study": STUDY,
            "status": STATUS,
            "contract": contract,
            "selection": selection,
            "validation": {
                "surface": val_surface,
                "decay_vs_d0_frozen_reference": val_decay,
                "cadence_spread_repeat_minus_once": val_spread,
                "frozen_reference": frozen_val,
                "daily_known_npz": str(out / "daily_known_validation.npz"),
            },
            "late": None,
            "decision": decision_text(chosen, val_surface, None, val_decay, None),
            "artifacts": {
                "contract": str(out / "contract.json"),
                "signals": str(signals_file),
                "collect_parts": str(out / "signal_parts"),
                "daily_known_validation_npz": str(out / "daily_known_validation.npz"),
                "producer_snapshot": str(out / "producer_snapshot.py"),
            },
            "runtime_s": round(time.time() - t0, 1),
        }
        _json_atomic(out / "results.json", results)
        print(f"[decision] {results['decision']}", flush=True)
        print(f"[done] results -> {out / 'results.json'} ({results['runtime_s']}s)", flush=True)
        return

    # 7) late block: all ten cells at the full ladder, the choice immutable.
    n_late = block_signals(signals, conf_days)
    late_surface: dict[str, dict[str, dict]] = {}
    late_daily: dict[str, np.ndarray] = {}
    for cell in CELLS:
        late_surface[cell.key] = {}
        daily_sel = None
        for cost in LADDER:
            metrics, _ = replay_block(signals, conf_days, cell, cost, CONF_PERIOD, n_late)
            late_surface[cell.key][str(int(cost))] = cell_view(metrics)
            if cost == SELECT_COST_BPS:
                daily_sel = np.asarray([d["known_pnl"] for d in metrics["daily"]], dtype=float)
        late_daily[cell.key] = daily_sel
        print_cell_line("late", cell.key, late_surface[cell.key])
    np.savez(out / "daily_known_late.npz", **late_daily)

    # 8) frozen reference on the late block: same hard reproduction gate.
    frozen_late, _ = frozen_reference_block(signals, conf_days, CONF_PERIOD, late_surface)

    late_decay = decay_table(late_surface)
    late_spread = cadence_spread(late_surface)
    decision = decision_text(chosen, val_surface, late_surface, val_decay, late_decay)
    results = {
        "study": STUDY,
        "status": STATUS,
        "contract": contract,
        "selection": selection,
        "validation": {
            "surface": val_surface,
            "decay_vs_d0_frozen_reference": val_decay,
            "cadence_spread_repeat_minus_once": val_spread,
            "frozen_reference": frozen_val,
            "daily_known_npz": str(out / "daily_known_validation.npz"),
        },
        "late": {
            "surface": late_surface,
            "decay_vs_d0_frozen_reference": late_decay,
            "cadence_spread_repeat_minus_once": late_spread,
            "frozen_reference": frozen_late,
            "daily_known_npz": str(out / "daily_known_late.npz"),
            "disclosure": DISCLOSURES["late_block"],
        },
        "decision": decision,
        "artifacts": {
            "contract": str(out / "contract.json"),
            "signals": str(signals_file),
            "collect_parts": str(out / "signal_parts"),
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
        "--days",
        nargs="+",
        default=None,
        help="restrict the analysis corpus to these days (debug)",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="resume collection from the per-day parts already on disk",
    )
    p.add_argument(
        "--skip-late",
        action="store_true",
        help="stop after the frozen contract and the validation block (no late traversal)",
    )
    run(p.parse_args())


if __name__ == "__main__":
    main()
