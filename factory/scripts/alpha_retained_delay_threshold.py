#!/usr/bin/env python3
"""Delay x threshold grid of the retained top-gainer mechanism (no refit).

QUESTION. The retained lane (factory/scripts/alpha_sparse_daily.py) pins ONE admission
threshold (0.030), ONE exit horizon (h60) and the repeat cadence on the ONE stored h60
payoff model. Two neighbours then moved one axis each: the exit-grid lane
(factory/scripts/alpha_retained_exit_grid.py) swept thresholds 0.020..0.040 x horizons
15..390 and found the lower-threshold higher-frequency cells (thr 0.020 at h30/h60)
essentially flat-to-negative on 2023 validation at d=0, while thr 0.025/h30 won validation
and collapsed on the late block; the execution-delay lane
(factory/scripts/alpha_retained_entry_delay.py) delayed the ENTRY by 1-4 minutes past the
state minute t and found d=2 strictly better than d=0 on BOTH blocks at the SAME 0.030
threshold. This study crosses the two axes: does the 1-4 minute entry delay RESCUE the
lower-threshold higher-frequency variants that failed validation at d=0?

Predeclared grid = thresholds {0.015, 0.020, 0.025, 0.030} x entry delay {0, 2, 4} x
horizons {30, 60}, all at the repeat cadence (flat 15-minute cooldown after the ACTUAL
exit, max 3 attempts per ticker per session, 3 x $1,000 book, one position per symbol,
simultaneous intents funded by descending score before any fill outcome, unfilled cash
retained, UNKNOWN exits hold the slot and the ticker to session end) = 24 fixed cells.
Nothing else moves: the SAME immutable stored model (``learned/models/payoff_h60.joblib``,
sha256 c5493c6c043a22e544ca98aa4fb5620292d4a1aeb370f63f6492907334f0bf96, verified at load,
never refit, never HPO'd, no new features) is scored ONCE per day; a cell can only change
WHICH states are admitted (the threshold), HOW LONG a position is held (the exit horizon)
and WHEN the entry price is read (the delay). The score is the stored h60 model's
prediction regardless of the exit horizon, exactly as the parent's repeat_h30 view uses
it: no second model, no refit, no horizon-specific head.

LABEL BASIS (one rule, deliberately documented): every cell of the grid, at EVERY delay
and at BOTH horizons, recomputes its labels from the day's own SIP net minute bars - the
same tape the panel was built from. Entry minute = t + d; entry price = the open of the
bar at the entry minute when it prints (``filled_proxy``), else the NEXT actual bar open at
or before session_end (``filled_next_open``, a tape gap, recorded), else ``unfilled_cash``
(no bar from the entry minute through session_end: no position, cash retained, the
attempt still counts, no cooldown, no fee); ``unknown_pending`` when the ticker has no
tape stream that day or the print is nonfinite/nonpositive (never cash, never a guess).
Exit = the LAST actual bar open at or before the ACTUAL entry minute + horizon, applied
identically at h30 and h60 and at every delay. h60 does NOT read the panel's stored
``gross_60``/``exit_et_60`` here: that label is anchored at minute t and would not follow a
delayed entry, so using it at d>0 and tape-recomputed labels at d=0 would mix two bases
inside one grid. h30 is recomputed from the day's bars exactly like h60 (the delay lane's
rule), NOT the parent lane's first-open-at-or-after rule. Keeping ONE basis across the two
axes makes every reported delta a same-basis difference. The panel's own stored h60 labels
are replayed separately as the FROZEN REFERENCE ROW (theta 0.030 / h60 / d=0 / repeat),
which must reproduce the stored baseline field-for-field, and the row-by-row gap between
the two bases is quantified in ``coverage.d0_vs_panel`` and in
``frozen_reference.gap_rule_effect_vs_recomputed_d0`` instead of being silently absorbed.
When the tape ends before entry_minute + horizon the exit is the last actual print, so a
realized hold can be shorter than the nominal horizon - that is the stated rule, never a
guess. EVERY fill is a MINUTE-OPEN PROXY: none of these are actual quotes or actual
exchange fills.

d=0 TWINS AND DELTAS: every cell is reported beside its own d=0 twin at the same
threshold and horizon (the in-study control), with the explicit per-cell delta at every
cost rung (known fills, known-contribution $/calendar day, 252-session annual $, mean net
per known fill, traded days, attempts and the retained share of the twin's $/day), on both
blocks. The frozen theta.030/h60/d0 reference row (the stored baseline's own labels) is
reported beside them as the external control.

Two reported bases, never blended: the KNOWN CONTRIBUTION (the dollars of the fills whose
exit actually printed - partial by construction, UNKNOWN fills excluded) and the
FULL-LOSS LOWER BOUND (the same dollars minus one whole ticket per UNKNOWN fill). An
UNKNOWN exit is never treated as cash and its loss is never called an EV.

COSTS are a COMPARISON SET, never a hurdle: 25/50/75/100/125/150 bps are TOTAL round-trip
modeled friction scenarios on the minute-open proxy prices; no rung closes a candidate and
no rung falsifies anything. 25..150 bps describe a LOW-FEE OPPORTUNITY set, not a fee
claim - a primary US broker's regular schedule (zero commission, SEC 20.6 per $1M, CAT
0.000003 per side, daily cent rounding) is typically well under 1 bp on a $1,000 ticket,
so actual provider fees are sourced separately, not here. 200 bps is reported only as the
historical diagnostic the stored late ladder already carried. Returns are a RESEARCH
NORMALIZATION on a fixed $3,000 reserve (each session restarts from the same reserve),
annualized by the simple 252-session convention - NOT a CAGR - with an additive
fixed-ticket carry-equity check beside it.

SELECTION (pre-declared, 2023 validation block only): rank the 24 fixed cells by
KNOWN-contribution dollars per full calendar day at the 25 bps rung, ties broken by more
known fills, then fewer attempts, then the cell key. The choice is frozen to
``contract.json`` and to ``selection.json`` BEFORE any 2025-02..2026-05 outcome is
computed; that late block was already explored by earlier producers, so it is transparency
only and is NOT a pristine holdout. The late traversal reports the same 24 cells with the
choice immutable. The rescue read is diagnostic arithmetic on the reported cells, never a
second selection.

Day-parallel-safe by construction: one process, one resident day at collection time,
per-day atomic parts (parquet + coverage json) keyed by a resume hash over the producer
sha, the declared contract sha, the model sha, the day and the schema, so --resume skips
intact days and a changed producer/contract/model invalidates the stale parts. The corpus
is never concatenated across blocks; each block concatenates only its own days. Protected
days (2024, 2025-01, 2026-06..2026-08) are never read; every day passes ``allowed()``
before any file is opened.

Usage:
  uv run --no-sync python factory/scripts/alpha_retained_delay_threshold.py
      # outputs -> ~/alpha-data/open-search-v1/retained_delay_threshold
  uv run --no-sync python factory/scripts/alpha_retained_delay_threshold.py --resume
      # top up from the per-day parts already on disk
  uv run --no-sync python factory/scripts/alpha_retained_delay_threshold.py --skip-late
      # validation + frozen selection only
  uv run --no-sync python factory/scripts/alpha_retained_delay_threshold.py --out <dir>
      # relocate the lane root
  uv run --no-sync python factory/scripts/alpha_retained_delay_threshold.py \
      --days 2023-05-01 2023-05-02
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
STUDY = "alpha_retained_delay_threshold"
STATUS = "DISCOVERY-NOT-VALIDATED"
MODEL_HORIZON = 60  # the one stored head that is ever loaded
MODEL_NAME = "payoff_h60.joblib"
# Pinned digest of the ONE immutable stored model: verified at load, never refit.
EXPECTED_MODEL_SHA256 = "c5493c6c043a22e544ca98aa4fb5620292d4a1aeb370f63f6492907334f0bf96"
THRESHOLDS = (0.015, 0.020, 0.025, 0.030)  # admission bars on the SAME score
MIN_THRESHOLD = min(THRESHOLDS)  # collection keeps every state any cell could admit
DELAYS = (0, 2, 4)  # minutes added to the state minute t; d=0 is the frozen entry
HORIZONS = (30, 60)  # exit horizons in minutes, both recomputed from the day's tape
REFERENCE_THRESHOLD = 0.030  # the stored lane's admission bar
REFERENCE_DELAY = 0  # the frozen entry minute
REFERENCE_HORIZON = 60  # the stored lane's exit horizon
COOLDOWN_MIN = 15  # flat minutes after the ACTUAL exit
ATTEMPTS_REPEAT = 3  # attempts per ticker per session (the retained repeat form)
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
# on a $1,000 ticket, so the actual broker fee is sourced separately, not here.
HISTORICAL_COST_BPS = 200.0  # historical diagnostic only, never a modern fee claim
LADDER = COST_SCENARIOS_BPS + (HISTORICAL_COST_BPS,)
TRADING_DAYS_PER_YEAR = 252  # session-count convention for $/year, NOT a CAGR
BARS_ROOT = ROOT / "data" / "sip" / "net" / "bars"
OUTPUT = PANEL_ROOT / "retained_delay_threshold"
EXPECTED_DAYS = {VAL_PERIOD: 250, CONF_PERIOD: 332}
ANALYSIS_PERIODS = (VAL_PERIOD, CONF_PERIOD)
FLOAT_TOL = 1e-9
SCHEMA = "retained_delay_threshold_parts_v1"  # pinned by the resume hash
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
        "t + delay with delay in {0,2,4}; d=0 is the state minute t itself, the same "
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
    "exit": (
        "the LAST actual bar open at or before the ACTUAL entry minute + horizon (an actual print)"
    ),
    "exit_basis_choice": (
        "ONE uniform rule for both horizons and every delay, recomputed from the day's "
        "own SIP net minute bars. h60 does NOT read the panel's stored gross_60 / "
        "exit_et_60 (that label is anchored at minute t and would not follow a delayed "
        "entry), and h30 is NOT the parent lane's first-open-at-or-after rule: keeping "
        "one last-open rule across the delay and horizon axes makes every delta in this "
        "study a same-basis difference. The panel-label d=0 row is replayed separately "
        "as the frozen reference, and the row-by-row gap between the two bases is "
        "quantified in coverage.d0_vs_panel and "
        "frozen_reference.gap_rule_effect_vs_recomputed_d0"
    ),
    "hold_can_be_shorter_than_nominal": (
        "when the tape ends before entry_minute + horizon the exit is the last actual "
        "print, so a realized hold can be shorter than the nominal horizon; that is the "
        "stated rule, never a guess"
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
    "one_model": (
        "the stored h60 payoff model's score admits every cell at every horizon; no "
        "second model, no horizon-specific head, no refit, no HPO, no new features"
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
    "horizons": [int(h) for h in HORIZONS],
    "cadence": {
        "kind": "repeat",
        "max_attempts": ATTEMPTS_REPEAT,
        "cooldown_min": COOLDOWN_MIN,
        "cooldown_anchor": "the ACTUAL exit minute of the position",
    },
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

# Stored baseline cells this study hard-gates against. Both sources are READ-ONLY
# artifacts of already-published lanes; neither is recomputed, refit or re-estimated here.
# (1) The frozen panel-label reference row: the stored lane's chosen cell (theta 0.030,
#     h60, repeat, d=0) replayed from the PANEL's own stored h60 labels, as pinned by
#     alpha_retained_entry_delay.EXPECTED_FROZEN['repeat'] (itself the parent's
#     learned_sparse_daily run) at the 100 bps reproduction rung.
EXPECTED_FROZEN = {
    "source": (
        "factory/scripts/alpha_retained_entry_delay.py EXPECTED_FROZEN['repeat'] "
        "(= learned_sparse_daily/results.json, the parent's alpha_sparse_daily run; "
        "identical figures to alpha_retained_exit_grid.REFERENCE_LANE)"
    ),
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
}
# (2) The delay lane's own recomputed-basis cells: this study's (theta 0.030, h60,
#     d in {0,2,4}) cells ARE the delay lane's repeat_d{d} cells (same model, same
#     threshold, same tape-recomputed labels, same funded-reserve engine), so they must
#     reproduce its published figures field-for-field on full blocks.
EXPECTED_DELAY_LANE = {
    "source": (
        "~/alpha-data/open-search-v1/retained_entry_delay/results.json "
        "(alpha_retained_entry_delay repeat_d{d}: h60 labels recomputed from the day's "
        "tape, the same last-actual-open exit rule this study uses at both horizons)"
    ),
    VAL_PERIOD: {
        "repeat_h60_d0_thr030": {
            "n_signals": 107,
            "attempts": 68,
            "known_fills": 68,
            "unknown_fills": 0,
            "unfilled_cash_attempts": 0,
            "traded_days": 55,
            "skip_reasons": {"cooldown": 4, "slot_busy": 35},
            "by_rung": {
                "25": {
                    "known_contribution_usd_per_day": 6.236834306014678,
                    "mean_net_known_fill": 0.022929537889759842,
                    "lower_bound_usd_per_day": 6.236834306014678,
                },
                "50": {
                    "known_contribution_usd_per_day": 5.542108456543221,
                    "mean_net_known_fill": 0.020375398737291255,
                    "lower_bound_usd_per_day": 5.542108456543221,
                },
                "75": {
                    "known_contribution_usd_per_day": 4.849112932973267,
                    "mean_net_known_fill": 0.017827621077107596,
                    "lower_bound_usd_per_day": 4.849112932973267,
                },
                "100": {
                    "known_contribution_usd_per_day": 4.157841278864969,
                    "mean_net_known_fill": 0.015286181172297676,
                    "lower_bound_usd_per_day": 4.157841278864969,
                },
                "125": {
                    "known_contribution_usd_per_day": 3.4682870698599806,
                    "mean_net_known_fill": 0.012751055403896988,
                    "lower_bound_usd_per_day": 3.4682870698599806,
                },
                "150": {
                    "known_contribution_usd_per_day": 2.780443913482869,
                    "mean_net_known_fill": 0.010222220270157609,
                    "lower_bound_usd_per_day": 2.780443913482869,
                },
                "200": {
                    "known_contribution_usd_per_day": 1.409865346939318,
                    "mean_net_known_fill": 0.005183328481394558,
                    "lower_bound_usd_per_day": 1.409865346939318,
                },
            },
        },
        "repeat_h60_d2_thr030": {
            "n_signals": 107,
            "attempts": 68,
            "known_fills": 68,
            "unknown_fills": 0,
            "unfilled_cash_attempts": 0,
            "traded_days": 55,
            "skip_reasons": {"cooldown": 3, "slot_busy": 36},
            "by_rung": {
                "25": {
                    "known_contribution_usd_per_day": 8.7292951402287,
                    "mean_net_known_fill": 0.0320929968390761,
                    "lower_bound_usd_per_day": 8.7292951402287,
                },
                "100": {
                    "known_contribution_usd_per_day": 6.63167837942526,
                    "mean_net_known_fill": 0.024381170512592868,
                    "lower_bound_usd_per_day": 6.63167837942526,
                },
            },
        },
        "repeat_h60_d4_thr030": {
            "n_signals": 107,
            "attempts": 68,
            "known_fills": 68,
            "unknown_fills": 0,
            "unfilled_cash_attempts": 0,
            "traded_days": 55,
            "skip_reasons": {"cooldown": 3, "slot_busy": 36},
            "by_rung": {
                "25": {
                    "known_contribution_usd_per_day": 8.524364088725761,
                    "mean_net_known_fill": 0.03133957385560941,
                    "lower_bound_usd_per_day": 8.524364088725761,
                },
                "100": {
                    "known_contribution_usd_per_day": 6.428278578190397,
                    "mean_net_known_fill": 0.023633377125699986,
                    "lower_bound_usd_per_day": 6.428278578190397,
                },
            },
        },
    },
    CONF_PERIOD: {
        "repeat_h60_d0_thr030": {
            "n_signals": 244,
            "attempts": 157,
            "known_fills": 157,
            "unknown_fills": 0,
            "unfilled_cash_attempts": 0,
            "traded_days": 124,
            "skip_reasons": {"cooldown": 12, "slot_busy": 75},
            "by_rung": {
                "25": {
                    "known_contribution_usd_per_day": 8.893954450504618,
                    "mean_net_known_fill": 0.01880759794629002,
                    "lower_bound_usd_per_day": 8.893954450504618,
                },
                "50": {
                    "known_contribution_usd_per_day": 7.690990594273974,
                    "mean_net_known_fill": 0.016263750810821397,
                    "lower_bound_usd_per_day": 7.690990594273974,
                },
                "75": {
                    "known_contribution_usd_per_day": 6.491022912031394,
                    "mean_net_known_fill": 0.013726239533722439,
                    "lower_bound_usd_per_day": 6.491022912031394,
                },
                "100": {
                    "known_contribution_usd_per_day": 5.294040224023295,
                    "mean_net_known_fill": 0.011195040473730789,
                    "lower_bound_usd_per_day": 5.294040224023295,
                },
                "125": {
                    "known_contribution_usd_per_day": 4.100031406047418,
                    "mean_net_known_fill": 0.008670130107055689,
                    "lower_bound_usd_per_day": 4.100031406047418,
                },
                "150": {
                    "known_contribution_usd_per_day": 2.9089853891088038,
                    "mean_net_known_fill": 0.006151485026650462,
                    "lower_bound_usd_per_day": 2.9089853891088038,
                },
                "200": {
                    "known_contribution_usd_per_day": 0.5357377563474046,
                    "mean_net_known_fill": 0.0011328976758429189,
                    "lower_bound_usd_per_day": 0.5357377563474046,
                },
            },
        },
        "repeat_h60_d2_thr030": {
            "n_signals": 244,
            "attempts": 156,
            "known_fills": 156,
            "unknown_fills": 0,
            "unfilled_cash_attempts": 0,
            "traded_days": 124,
            "skip_reasons": {"cooldown": 10, "slot_busy": 78},
            "by_rung": {
                "25": {
                    "known_contribution_usd_per_day": 12.294616410063389,
                    "mean_net_known_fill": 0.026165465693211827,
                    "lower_bound_usd_per_day": 12.294616410063389,
                },
                "100": {
                    "known_contribution_usd_per_day": 8.691798451179343,
                    "mean_net_known_fill": 0.018497930037125264,
                    "lower_bound_usd_per_day": 8.691798451179343,
                },
            },
        },
        "repeat_h60_d4_thr030": {
            "n_signals": 244,
            "attempts": 156,
            "known_fills": 156,
            "unknown_fills": 0,
            "unfilled_cash_attempts": 0,
            "traded_days": 124,
            "skip_reasons": {"cooldown": 9, "slot_busy": 79},
            "by_rung": {
                "25": {
                    "known_contribution_usd_per_day": 9.289098083837764,
                    "mean_net_known_fill": 0.019769106178423958,
                    "lower_bound_usd_per_day": 9.289098083837764,
                },
                "100": {
                    "known_contribution_usd_per_day": 5.708737437835307,
                    "mean_net_known_fill": 0.012149364430777705,
                    "lower_bound_usd_per_day": 5.708737437835307,
                },
            },
        },
    },
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


# ----- the fixed grid: 4 thresholds x 3 delays x 2 horizons --------------------
@dataclass(frozen=True)
class Cell:
    """One fixed grid cell: an admission threshold at one entry delay and one exit horizon.

    Same stored score, same repeat cadence and same funded reserve for every cell; only the
    admission bar, the entry delay and the exit horizon differ.
    """

    key: str
    threshold: float
    delay: int
    horizon: int
    cooldown_min: int
    max_attempts: int
    control: bool = False

    @property
    def cadence_str(self) -> str:
        return (
            f"thr{self.threshold:.3f}|exit_h{self.horizon}|entry_d{self.delay}|"
            f"cadence_repeat|flat_cooldown_{self.cooldown_min}min|"
            f"max_attempts_{self.max_attempts}"
        )


def cell_key(horizon: int, delay: int, threshold: float) -> str:
    return f"repeat_h{horizon}_d{delay}_thr{int(round(threshold * 1000)):03d}"


CELLS = tuple(
    Cell(
        key=cell_key(h, d, t),
        threshold=float(t),
        delay=int(d),
        horizon=int(h),
        cooldown_min=COOLDOWN_MIN,
        max_attempts=ATTEMPTS_REPEAT,
        control=(
            abs(float(t) - REFERENCE_THRESHOLD) < 1e-12
            and int(d) == REFERENCE_DELAY
            and int(h) == REFERENCE_HORIZON
        ),
    )
    for h in HORIZONS
    for d in DELAYS
    for t in THRESHOLDS
)
FROZEN_KEY = (
    f"frozen_repeat_h{REFERENCE_HORIZON}_d{REFERENCE_DELAY}"
    f"_thr{int(round(REFERENCE_THRESHOLD * 1000)):03d}"
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
    basis: str = "recomputed_from_the_day's_tape"


def recomputed_view(cell: Cell) -> LabelView:
    """Labels recomputed from the day's tape for one cell's own delay and horizon."""
    d, h = cell.delay, cell.horizon
    return LabelView(
        gross=f"gross_{d}_h{h}",
        exit_et=f"exit_et_{d}_h{h}",
        status=f"entry_status_{d}",
        open=f"entry_open_{d}",
        hold_minute=f"entry_et_{d}",
        unfilled="unfilled_cash",
        entry_et=f"entry_et_{d}",
        basis=LABEL_RULES["exit_basis_choice"],
    )


# The frozen reference row: the PANEL's own stored h60 labels at theta 0.030 / d=0, i.e.
# exactly the stored baseline's labels and replay (entry at the open of minute t, no bar
# at t => unfilled_expired cash, exit = the panel producer's own h60 print).
FROZEN_CELL = Cell(
    key=FROZEN_KEY,
    threshold=REFERENCE_THRESHOLD,
    delay=REFERENCE_DELAY,
    horizon=REFERENCE_HORIZON,
    cooldown_min=COOLDOWN_MIN,
    max_attempts=ATTEMPTS_REPEAT,
    control=True,
)
FROZEN_VIEW = LabelView(
    gross="panel_gross_60",
    exit_et="panel_exit_et_60",
    status="panel_entry_status",
    open="panel_entry_open",
    hold_minute="t",
    unfilled="unfilled_expired",
    entry_et=None,
    basis="the panel's own stored h60 labels (the frozen baseline's labels, unchanged)",
)

# ----- columns of one per-day signal part ------------------------------------
SIGNAL_BASE = ["day", "ticker", "t", "session_end", "score"]
PANEL_SELECT = [
    pl.col("entry_status").alias("panel_entry_status"),
    pl.col("entry_open").alias("panel_entry_open"),
    pl.col("gross_60").alias("panel_gross_60"),
    pl.col("exit_et_60").alias("panel_exit_et_60"),
    pl.col("exit_status_60").alias("panel_exit_status_60"),
]
PANEL_KEEP = [e.meta.output_name() for e in PANEL_SELECT]
DELAY_COLUMNS = [
    f"{kind}_{d}" for d in DELAYS for kind in ("entry_et", "entry_open", "entry_status")
]
LABEL_COLUMNS = [
    f"{kind}_{d}_h{h}"
    for d in DELAYS
    for h in HORIZONS
    for kind in ("gross", "exit_et", "exit_status")
]
PART_COLUMNS = SIGNAL_BASE + DELAY_COLUMNS + LABEL_COLUMNS + PANEL_KEEP
PART_SCHEMA = {
    "day": pl.String,
    "ticker": pl.String,
    "t": pl.Int64,
    "session_end": pl.Int64,
    "score": pl.Float64,
    **{f"entry_et_{d}": pl.Int64 for d in DELAYS},
    **{f"entry_open_{d}": pl.Float64 for d in DELAYS},
    **{f"entry_status_{d}": pl.String for d in DELAYS},
    **{f"gross_{d}_h{h}": pl.Float64 for d in DELAYS for h in HORIZONS},
    **{f"exit_et_{d}_h{h}": pl.Int64 for d in DELAYS for h in HORIZONS},
    **{f"exit_status_{d}_h{h}": pl.String for d in DELAYS for h in HORIZONS},
    "panel_entry_status": pl.String,
    "panel_entry_open": pl.Float64,
    "panel_gross_60": pl.Float64,
    "panel_exit_et_60": pl.Int64,
    "panel_exit_status_60": pl.String,
}


def _empty_part_frame() -> pl.DataFrame:
    return pl.DataFrame(schema={k: PART_SCHEMA[k] for k in PART_COLUMNS})


# ----- labels recomputed from the day's own minute bars ------------------------
def compute_labels(rows: list[dict], tapes: dict) -> tuple[dict[str, list], dict]:
    """One day's labels for EVERY delay x horizon pair, read from the tape only.

    Per delay ``d`` and horizon ``h``:
      * entry minute ``t + d``; the first actual bar open at/after it, at or before
        ``session_end``, is the entry price (``filled_proxy`` when it is exactly the entry
        minute, ``filled_next_open`` when a tape gap pushed the fill to the next print,
        recorded); ``unfilled_cash`` when no bar prints from the entry minute through
        session_end; ``unknown_pending`` when the ticker has no tape stream that day or the
        print is nonfinite/nonpositive (never cash, never a guess).
      * exit = the LAST actual bar open at or before ``entry_et + h`` (an actual print);
        UNKNOWN only when the tape itself is absent.
    ``et`` must be the tape's own ascending minute stamps. The d=0 / h60 recomputed labels
    are also compared against the panel's own stored h60 labels so the filled-open and
    exit-minute gap rules are quantified row by row, never silently absorbed.
    """
    out: dict[str, list] = {}
    for d in DELAYS:
        for kind in ("entry_et", "entry_open", "entry_status"):
            out[f"{kind}_{d}"] = []
        for h in HORIZONS:
            for kind in ("gross", "exit_et", "exit_status"):
                out[f"{kind}_{d}_h{h}"] = []
    entry_counts: dict[str, Counter] = {str(d): Counter() for d in DELAYS}
    exit_counts: dict[str, Counter] = {f"{d}_h{h}": Counter() for d in DELAYS for h in HORIZONS}
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
        for h in HORIZONS:
            out[f"gross_{d}_h{h}"].append(None)
            out[f"exit_et_{d}_h{h}"].append(None)
            out[f"exit_status_{d}_h{h}"].append("unknown_pending")
            exit_counts[f"{d}_h{h}"]["unknown_pending"] += 1

    def append_unfilled(d: int) -> None:
        out[f"entry_et_{d}"].append(None)
        out[f"entry_open_{d}"].append(None)
        out[f"entry_status_{d}"].append("unfilled_cash")
        entry_counts[str(d)]["unfilled_cash"] += 1
        for h in HORIZONS:
            out[f"gross_{d}_h{h}"].append(None)
            out[f"exit_et_{d}_h{h}"].append(None)
            out[f"exit_status_{d}_h{h}"].append("unfilled_cash")
            exit_counts[f"{d}_h{h}"]["unfilled_cash"] += 1

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
                # A nonpositive/nonfinite print can never price a position: UNKNOWN, not cash.
                append_unknown(d, "unknown_pending_invalid_open")
                if d == 0:
                    d0["entry_transitions"][f"{r['panel_entry_status']}->unknown_pending"] += 1
                continue
            e_status = "filled_proxy" if e_et == target else "filled_next_open"
            out[f"entry_et_{d}"].append(e_et)
            out[f"entry_open_{d}"].append(e_open)
            out[f"entry_status_{d}"].append(e_status)
            entry_counts[str(d)][e_status] += 1
            for h in HORIZONS:
                k = int(np.searchsorted(et, e_et + h, side="right")) - 1
                if k < 0:
                    out[f"gross_{d}_h{h}"].append(None)
                    out[f"exit_et_{d}_h{h}"].append(None)
                    out[f"exit_status_{d}_h{h}"].append("unknown_pending")
                    exit_counts[f"{d}_h{h}"]["unknown_pending"] += 1
                    continue
                g = float(opens[k]) / e_open - 1.0
                out[f"gross_{d}_h{h}"].append(g)
                out[f"exit_et_{d}_h{h}"].append(int(et[k]))
                out[f"exit_status_{d}_h{h}"].append("observed_open_proxy")
                exit_counts[f"{d}_h{h}"]["observed_open_proxy"] += 1
            if d == 0:
                panel_status = r["panel_entry_status"]
                d0["entry_transitions"][f"{panel_status}->{e_status}"] += 1
                if panel_status == "unfilled_expired":
                    d0["unfilled_to_filled"] += 1
                if r["panel_exit_status_60"] != "observed_open_proxy":
                    d0["exit_status_changed"] += 1
                panel_gross = r["panel_gross_60"]
                if r["panel_exit_status_60"] == "observed_open_proxy" and panel_gross is not None:
                    recomputed_exit_et = int(out["exit_et_0_h60"][-1])
                    recomputed_gross = float(out["gross_0_h60"][-1])
                    if (
                        r["panel_exit_et_60"] is not None
                        and int(r["panel_exit_et_60"]) != recomputed_exit_et
                    ):
                        d0["exit_et_changed"] += 1
                    if abs(float(panel_gross) - recomputed_gross) > FLOAT_TOL:
                        d0["gross_changed"] += 1
                        d0["gross_max_abs_delta"] = max(
                            d0["gross_max_abs_delta"], abs(float(panel_gross) - recomputed_gross)
                        )
    return out, {
        "entry_counts_by_delay": {k: dict(v) for k, v in entry_counts.items()},
        "exit_counts_by_delay_horizon": {k: dict(v) for k, v in exit_counts.items()},
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
    """Load the ONE immutable stored head; verify its digest and frozen feature order."""
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


# ----- past-only peer context, per day (same partition key as the panel) -----
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
            "exit_counts_by_delay_horizon": {},
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
        out, info = compute_labels(rows, tapes)
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
        "exit_counts_by_delay_horizon": info["exit_counts_by_delay_horizon"],
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
        "exit_counts_by_delay_horizon": {f"{d}_h{h}": Counter() for d in DELAYS for h in HORIZONS},
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
    coverage["exit_counts_by_delay_horizon"] = {
        k: dict(v) for k, v in coverage["exit_counts_by_delay_horizon"].items()
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
        missing_streams = cov.get("missing_symbol_streams")
        if missing_streams:
            coverage["missing_symbol_streams"][cov.get("day", "?")] = list(missing_streams)
    for d in DELAYS:
        entry = (cov.get("entry_counts_by_delay") or {}).get(str(d), {})
        coverage["entry_counts_by_delay"][str(d)].update(entry)
    for h in HORIZONS:
        for d in DELAYS:
            exit_ = (cov.get("exit_counts_by_delay_horizon") or {}).get(f"{d}_h{h}", {})
            coverage["exit_counts_by_delay_horizon"][f"{d}_h{h}"].update(exit_)
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


# ----- funded-reserve replay: delay, threshold and horizon are the only axes --
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
    the entry price comes from (``t + delay``) and how long the position is held.
    """
    view = labels or recomputed_view(cell)
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
                        "delay": cell.delay,
                        "threshold": cell.threshold,
                        "horizon": cell.horizon,
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
        "threshold": cell.threshold,
        "delay": cell.delay,
        "horizon": cell.horizon,
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
    frame: pl.DataFrame, days: list[str], block: str, tag: str
) -> tuple[dict[str, dict[str, dict]], dict[str, np.ndarray]]:
    """Every fixed cell on one block: one full replay at every rung of the ladder."""
    surface: dict[str, dict[str, dict]] = {}
    daily: dict[str, np.ndarray] = {}
    for cell in CELLS:
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
        f"[{tag}] {key:<28} $/day {ladder} known={sel['known_fills']} "
        f"unk={sel['unknown_fills']} unfilled={sel['unfilled_cash_attempts']} "
        f"no_order={sel['no_order_skips_total']} traded={sel['traded_days']}/{sel['days']} "
        f"attempts={sel['attempts']} signals={sel['n_signals']}",
        flush=True,
    )


# ----- selection + the d=0 twin deltas -----------------------------------------
def rank_cells(surface: dict) -> list[Cell]:
    """Rank the fixed cells by validation known-contribution $/calendar day at the
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
        "ranking": [
            {
                "rank": i,
                "cell_key": cell.key,
                "threshold": cell.threshold,
                "delay": cell.delay,
                "horizon": cell.horizon,
                "entry_minute": f"t+{cell.delay}",
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
            "threshold": chosen.threshold,
            "delay": chosen.delay,
            "horizon": chosen.horizon,
            "entry_minute": f"t+{chosen.delay}",
            "max_attempts": chosen.max_attempts,
            "cooldown_min": chosen.cooldown_min,
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


def delta_vs_d0(surface: dict) -> dict:
    """Per (horizon, threshold): every delay at every rung, with the explicit delta vs
    that pair's own d=0 twin (the in-study control)."""
    out: dict[str, dict] = {}
    for horizon in HORIZONS:
        for threshold in THRESHOLDS:
            base_key = cell_key(horizon, 0, threshold)
            by_delay: dict[str, dict] = {}
            for delay in DELAYS:
                key = cell_key(horizon, delay, threshold)
                per_rung: dict[str, dict] = {}
                for cost in LADDER:
                    rung = str(int(cost))
                    c, b = surface[key][rung], surface[base_key][rung]
                    cd = c["known_contribution_usd_per_day"]
                    bd = b["known_contribution_usd_per_day"]
                    cn = c["mean_net_known_fill"]
                    bn = b["mean_net_known_fill"]
                    per_rung[rung] = {
                        "known_fills": c["known_fills"],
                        "unknown_fills": c["unknown_fills"],
                        "unfilled_cash_attempts": c["unfilled_cash_attempts"],
                        "no_order_skips_total": c["no_order_skips_total"],
                        "attempts": c["attempts"],
                        "traded_days": c["traded_days"],
                        "mean_net_known_fill": cn,
                        "known_contribution_usd_per_day": cd,
                        "known_contribution_usd_per_year_252": c[
                            "known_contribution_usd_per_year_252"
                        ],
                        "full_loss_lower_bound_usd_per_day": c["full_loss_lower_bound"][
                            "lower_bound_usd_per_day"
                        ],
                        "delta_known_fills_vs_d0": c["known_fills"] - b["known_fills"],
                        "delta_known_usd_per_day_vs_d0": (
                            (cd - bd) if cd is not None and bd is not None else None
                        ),
                        "delta_known_usd_per_year_252_vs_d0": (
                            (cd - bd) * TRADING_DAYS_PER_YEAR
                            if cd is not None and bd is not None
                            else None
                        ),
                        "delta_mean_net_known_fill_vs_d0": (
                            (cn - bn) if cn is not None and bn is not None else None
                        ),
                        "delta_traded_days_vs_d0": c["traded_days"] - b["traded_days"],
                        "delta_attempts_vs_d0": c["attempts"] - b["attempts"],
                        "retained_share_of_d0_known_usd_per_day": (
                            (cd / bd) if cd is not None and bd not in (None, 0.0) else None
                        ),
                    }
                by_delay[str(delay)] = per_rung
            out[f"h{horizon}_thr{int(round(threshold * 1000)):03d}"] = {
                "horizon": horizon,
                "threshold": threshold,
                "reference_cell": base_key,
                "by_delay": by_delay,
            }
    return out


# ----- the rescue read: diagnostic arithmetic on the reported cells ------------
def _compact_cell(view: dict) -> dict:
    c = view[str(int(SELECT_COST_BPS))]
    return {
        "known_fills": c["known_fills"],
        "unknown_fills": c["unknown_fills"],
        "unfilled_cash_attempts": c["unfilled_cash_attempts"],
        "attempts": c["attempts"],
        "traded_days": c["traded_days"],
        "mean_net_known_fill": c["mean_net_known_fill"],
        "known_contribution_usd_per_day": c["known_contribution_usd_per_day"],
        "known_contribution_usd_per_year_252": c["known_contribution_usd_per_year_252"],
        "full_loss_lower_bound_usd_per_day": c["full_loss_lower_bound"]["lower_bound_usd_per_day"],
    }


def rescue_table(val_surface: dict, late_surface: dict | None) -> dict:
    """Does the delay rescue each (threshold, horizon) pair? d=0 twin vs d=2/d=4, on both
    blocks, at the pre-declared rung; plain arithmetic on the already-reported cells, not
    a second selection."""
    out: dict[str, dict] = {}
    for threshold in THRESHOLDS:
        for horizon in HORIZONS:
            node: dict = {
                "threshold": threshold,
                "horizon": horizon,
                "rung_bps": SELECT_COST_BPS,
                "note": (
                    "diagnostic arithmetic on the reported cells at the pre-declared "
                    f"{int(SELECT_COST_BPS)} bps comparison rung; the d=0 twin is the "
                    "in-study control and the per-rung deltas live in "
                    "delta_vs_d0_twin"
                ),
            }
            for tag, surface in (("validation", val_surface), ("late", late_surface)):
                if surface is None:
                    node[tag] = None
                    continue
                by_delay = {
                    str(d): _compact_cell(surface[cell_key(horizon, d, threshold)]) for d in DELAYS
                }
                base = by_delay["0"]
                best = max(
                    DELAYS,
                    key=lambda d: (
                        by_delay[str(d)]["known_contribution_usd_per_day"] or 0.0,
                        by_delay[str(d)]["known_fills"],
                        -by_delay[str(d)]["attempts"],
                        -d,
                    ),
                )
                best_row = by_delay[str(best)]
                d0_day = base["known_contribution_usd_per_day"]
                best_day = best_row["known_contribution_usd_per_day"]
                node[tag] = {
                    "by_delay": by_delay,
                    "best_delay": int(best),
                    "delta_best_minus_d0_known_usd_per_day": (
                        (best_day - d0_day) if d0_day is not None and best_day is not None else None
                    ),
                    "delta_best_minus_d0_known_fills": (
                        best_row["known_fills"] - base["known_fills"]
                    ),
                    "delay_lifts_known_usd_per_day": (
                        None if d0_day is None or best_day is None else bool(best_day > d0_day)
                    ),
                }
            out[f"thr{threshold:.3f}_h{horizon}"] = node
    return out


# ----- frozen reference row + the same-basis cross-check ------------------------
def compare_frozen(block: str, view: dict) -> dict:
    """Field-by-field reproduction check of the frozen panel-label reference cell."""
    exp = EXPECTED_FROZEN[block]
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
                "panel-label cell is a named gap rule, quantified row by row in "
                "coverage.d0_vs_panel"
            ),
        }
    return out


REFERENCE_CELL_KEY = cell_key(REFERENCE_HORIZON, REFERENCE_DELAY, REFERENCE_THRESHOLD)


def frozen_reference_block(frame: pl.DataFrame, days: list[str], surface: dict, block: str) -> dict:
    """The frozen theta.030/h60/d=0 reference row: the panel's own stored h60 labels,
    replayed at every rung, with the hard reproduction gate on a full block."""
    per_rung: dict[str, dict] = {}
    for cost in LADDER:
        metrics, _ = replay_block(frame, days, FROZEN_CELL, cost, block, labels=FROZEN_VIEW)
        per_rung[str(int(cost))] = cell_view(metrics)
    expected_days = EXPECTED_DAYS[block]
    if len(days) != expected_days:
        gate = {
            "skipped": True,
            "reason": (
                f"corpus restricted to {len(days)} of the block's {expected_days} days; "
                "a full-block reproduction needs the full block"
            ),
        }
    else:
        gate = compare_frozen(block, per_rung[str(int(REPRO_COST_BPS))])
        if not gate["all_match"]:
            bad = {k: v for k, v in gate.items() if k != "all_match" and not v.get("match")}
            raise SystemExit(
                f"[{STUDY}] frozen {FROZEN_KEY} panel-label replay does not reproduce "
                f"the stored {block} cell: {bad}"
            )
    out = {
        "cell_key": FROZEN_CELL.key,
        "source": EXPECTED_FROZEN["source"],
        "label_basis": FROZEN_VIEW.basis,
        "replayed": per_rung,
        "reproduction_check": gate,
        "gap_rule_effect_vs_recomputed_d0": gap_effect(per_rung, surface[REFERENCE_CELL_KEY]),
    }
    if gate.get("skipped"):
        print(
            f"[frozen] {FROZEN_KEY} not comparable to the stored baseline ({gate['reason']})",
            flush=True,
        )
    else:
        fv = per_rung[str(int(REPRO_COST_BPS))]
        print(
            f"[frozen] {FROZEN_KEY} panel-label replay reproduces the stored {block} cell "
            f"exactly ({fv['known_fills']} known fills, "
            f"{_fmt(fv['full_loss_lower_bound']['mean_daily_lower_bound_return'])}/day "
            f"@{int(REPRO_COST_BPS)})",
            flush=True,
        )
    return out


def delay_lane_check(surface: dict, days: list[str], block: str) -> dict:
    """Same-basis cross-check: this grid's (theta 0.030, h60, d in {0,2,4}) cells are the
    delay lane's published repeat_d{d} cells and must reproduce them field-for-field on a
    full block (skipped when the corpus is restricted)."""
    expected_days = EXPECTED_DAYS[block]
    if len(days) != expected_days:
        return {
            "skipped": True,
            "reason": (
                f"corpus restricted to {len(days)} of the block's {expected_days} days; "
                "a full-block cross-check needs the full block"
            ),
            "source": EXPECTED_DELAY_LANE["source"],
        }
    checks: dict[str, dict] = {}
    all_ok = True
    for key, exp in EXPECTED_DELAY_LANE[block].items():
        fields: dict[str, dict] = {}
        for f in (
            "n_signals",
            "attempts",
            "known_fills",
            "unknown_fills",
            "unfilled_cash_attempts",
            "traded_days",
        ):
            got = surface[key][str(int(SELECT_COST_BPS))].get(f)
            fields[f] = {"expected": exp[f], "replayed": got, "match": exp[f] == got}
        got_skips = surface[key][str(int(SELECT_COST_BPS))].get("no_order_skips")
        fields["skip_reasons"] = {
            "expected": dict(exp["skip_reasons"]),
            "replayed": dict(got_skips or {}),
            "match": dict(exp["skip_reasons"]) == dict(got_skips or {}),
        }
        for rung, want in exp["by_rung"].items():
            v = surface[key][rung]
            got_map = {
                "known_contribution_usd_per_day": v["known_contribution_usd_per_day"],
                "mean_net_known_fill": v["mean_net_known_fill"],
                "lower_bound_usd_per_day": v["full_loss_lower_bound"]["lower_bound_usd_per_day"],
            }
            for f, want_v in want.items():
                got = got_map[f]
                ok = got is not None and abs(float(want_v) - float(got)) <= FLOAT_TOL
                fields[f"{f}_{rung}bps"] = {
                    "expected": want_v,
                    "replayed": got,
                    "match": bool(ok),
                }
        fields["all_match"] = all(v.get("match") for v in fields.values())
        all_ok = all_ok and bool(fields["all_match"])
        checks[key] = fields
    out = {
        "source": EXPECTED_DELAY_LANE["source"],
        "block": block,
        "cells": checks,
        "all_match": bool(all_ok),
        "note": (
            "the (theta 0.030, h60, d in {0,2,4}) cells of this grid share the delay "
            "lane's model, threshold, tape-recomputed labels and funded-reserve engine, "
            "so a mismatch would mean this producer drifted from the frozen engine"
        ),
    }
    if not all_ok:
        bad = {
            k: {f: v for f, v in c.items() if f != "all_match" and not v.get("match")}
            for k, c in checks.items()
            if not c["all_match"]
        }
        raise SystemExit(
            f"[{STUDY}] the theta.030/h60 delay cells do not reproduce the delay lane on "
            f"{block}: {bad}"
        )
    return out


# ----- decision text + block discovery -----------------------------------------
def _rescue_line(node: dict, tag: str) -> str:
    by_delay = node.get(tag) or {}
    rows = by_delay.get("by_delay") or {}
    if not rows:
        return f"{tag} n/a"
    parts = []
    for d in DELAYS:
        row = rows.get(str(d)) or {}
        day = row.get("known_contribution_usd_per_day")
        parts.append(f"d{d}={_fmt(day, '+.2f')}({row.get('known_fills')})")
    best = by_delay.get("best_delay")
    delta = by_delay.get("delta_best_minus_d0_known_usd_per_day")
    return f"{tag}: " + " ".join(parts) + f" best=d{best} delta={_fmt(delta, '+.2f')}"


def decision_text(
    chosen: Cell,
    val_surface: dict,
    late_surface: dict | None,
    rescue: dict,
    frozen_repro: dict,
) -> str:
    rung = str(int(SELECT_COST_BPS))
    c = val_surface[chosen.key][rung]
    base_key = cell_key(chosen.horizon, 0, chosen.threshold)
    base = val_surface[base_key][rung]
    if frozen_repro.get("all_match"):
        repro_txt = "the frozen theta.030/h60/d0 row reproduces the stored baseline exactly"
    elif frozen_repro.get("skipped"):
        repro_txt = (
            "the frozen reference row was not comparable to the stored baseline (restricted corpus)"
        )
    else:
        repro_txt = "WARNING: the frozen reference row does not reproduce the stored baseline"
    text = (
        f"{STATUS}: {chosen.key} selected by 2023 validation known-contribution $/calendar "
        f"day at {int(SELECT_COST_BPS)}bps ({_fmt(c['known_contribution_usd_per_day'], '+.2f')} "
        f"$/day, {_fmt(c['known_contribution_usd_per_year_252'], '+.2f')} $/yr by the simple "
        f"252-session daily-reset convention, {c['known_fills']} known fills on "
        f"{c['traded_days']} traded days of {c['days']}); its own d=0 twin {base_key} "
        f"{_fmt(base['known_contribution_usd_per_day'], '+.2f')} $/day "
        f"({base['known_fills']} known fills); {repro_txt}."
    )
    if late_surface is not None:
        lc = late_surface[chosen.key][rung]
        lb = late_surface[base_key][rung]
        text += (
            f" Late block (2025-02..2026-05, previously explored, transparency only): "
            f"{_fmt(lc['known_contribution_usd_per_day'], '+.2f')} $/day vs "
            f"{_fmt(lb['known_contribution_usd_per_day'], '+.2f')} $/day at d=0, "
            f"{lc['known_fills']} known fills on {lc['traded_days']} traded days of "
            f"{lc['days']}."
        )
    else:
        text += " Late block not run (--skip-late)."
    text += " RESCUE READ (diagnostic, not a second selection) - " + "; ".join(
        f"thr{t:.3f}/h{h}: {_rescue_line(rescue[f'thr{t:.3f}_h{h}'], 'validation')} | "
        f"{_rescue_line(rescue[f'thr{t:.3f}_h{h}'], 'late')}"
        for t in THRESHOLDS
        for h in HORIZONS
    )
    return text + "."


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
            "entry-delay x admission-threshold x exit-horizon grid of the retained "
            "top-gainer mechanism: 24 fixed cells (thresholds 0.015/0.020/0.025/0.030 x "
            "entry delays 0/2/4 minutes past the state minute t x horizons 30/60), all at "
            "the retained repeat cadence on the ONE immutable stored h60 payoff model"
        ),
        "status": STATUS,
        "declared_before_any_validation_outcome": True,
        "question": (
            "does the 1-4 minute entry delay rescue the higher-frequency lower-threshold "
            "variants (theta 0.020/0.015) that failed validation at d=0? Every cell is "
            "reported beside its own d=0 twin with the explicit delta at every rung, and "
            "the frozen theta.030/h60/d0 reference row is the external control"
        ),
        "grid": {
            "n_cells": len(CELLS),
            "axes": {
                "thresholds": [float(t) for t in THRESHOLDS],
                "entry_delays_minutes": [int(d) for d in DELAYS],
                "exit_horizons_minutes": [int(h) for h in HORIZONS],
                "cadence": {
                    "kind": "repeat",
                    "max_attempts": ATTEMPTS_REPEAT,
                    "cooldown_min": COOLDOWN_MIN,
                    "cooldown_anchor": "the ACTUAL exit minute of the position",
                },
            },
            "cells": [
                {
                    "key": c.key,
                    "cadence": c.cadence_str,
                    "threshold": c.threshold,
                    "delay": c.delay,
                    "horizon": c.horizon,
                    "entry_minute": f"t+{c.delay}",
                    "max_attempts": c.max_attempts,
                    "cooldown_min": c.cooldown_min,
                    "control": c.control,
                }
                for c in CELLS
            ],
            "fixed_in_code": True,
            "no_hpo": True,
            "no_refit": True,
            "every_cell_reads_the_same_score": True,
            "only_axes": [
                "which states clear the admission bar (threshold)",
                "when the entry price is read (delay, minutes past t)",
                "how long a position is held (exit horizon)",
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
            "frozen_panel_label_row": {
                "cell_key": FROZEN_CELL.key,
                "definition": (
                    "the panel's OWN stored h60 labels at theta 0.030 / d=0 / repeat: "
                    "entry at the open of minute t, no bar at t => unfilled_expired cash, "
                    "exit = the panel producer's own h60 print - exactly the stored "
                    "baseline's labels and replay; a hard reproduction gate on a full "
                    "block, and the gap to this study's recomputed basis is quantified "
                    "instead of being silently absorbed"
                ),
                "expected": EXPECTED_FROZEN,
            },
            "delay_lane_crosscheck": {
                "cells": [
                    "repeat_h"
                    f"{REFERENCE_HORIZON}_d{d}"
                    f"_thr{int(round(REFERENCE_THRESHOLD * 1000)):03d}"
                    for d in DELAYS
                ],
                "source": EXPECTED_DELAY_LANE["source"],
                "expected": EXPECTED_DELAY_LANE,
            },
        },
        "model": {
            "refit": False,
            "hpo": False,
            "feature_changes": False,
            "ticker_features_added": False,
            "horizons_scored_by_the_model": [MODEL_HORIZON],
            "horizon_specific_heads": False,
            "scored_once_over_analysis_corpus": True,
            "fit_block_2021_2022_scored": False,
            "fit_block_note": (
                "the 2021-2022 fit block is never replayed (the weights are frozen), so "
                "it is not scored; the single prediction pass is shared by all 24 cells "
                "and the stored h60 score admits the h30 cells exactly as the parent's "
                "repeat_h30 view does - no second model, no refit"
            ),
            "stored_path": str(model_path),
            "stored_sha256": model_sha,
            "expected_sha256": EXPECTED_MODEL_SHA256,
            "feature_order": list(FEATURES_ALL),
            "feature_order_verified_at_load": True,
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
        "disclosures": DISCLOSURES,
        "novelty": {
            "vs_frozen_lane": {
                "prior": "factory/scripts/alpha_sparse_daily.py",
                "prior_axes": (
                    "one threshold (0.030) x one scored horizon (h60) x the repeat cadence"
                ),
                "this_study": (
                    "four thresholds x two exit horizons x three entry delays = 24 cells, "
                    "all labels recomputed from the day's own tape on ONE last-open rule"
                ),
                "unchanged": (
                    "the stored model, the funded-reserve account and the admission score"
                ),
            },
            "vs_exit_grid": {
                "prior": "factory/scripts/alpha_retained_exit_grid.py",
                "prior_axes": (
                    "five thresholds x five horizons x two cadences at the frozen d=0 "
                    "entry minute, h60 read from the panel's stored labels"
                ),
                "this_study": (
                    "the delay axis crossed with the threshold and horizon axes; the "
                    "lower thresholds (0.015/0.020) that were flat-to-negative there are "
                    "re-read here with a 2-4 minute later entry"
                ),
            },
            "vs_delay_lane": {
                "prior": "factory/scripts/alpha_retained_entry_delay.py",
                "prior_axes": ("one threshold (0.030) x five delays x both cadences"),
                "this_study": (
                    "the delay axis crossed with the threshold and horizon axes at the "
                    "repeat cadence; its repeat_d{d} cells are reproduced here as the "
                    "theta.030/h60 cross-check"
                ),
            },
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
        f"known-contribution $/calendar day) -- before any validation score",
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

    # 4) 2023 validation: every cell at every rung, no median/tail/count gating.
    val_frame = block_frame(out / "collect_parts", val_days)
    print(f"[signals] validation block rows {val_frame.height}", flush=True)
    val_surface, val_daily = replay_surface(val_frame, val_days, VAL_PERIOD, "val")
    frozen_val = frozen_reference_block(val_frame, val_days, val_surface, VAL_PERIOD)
    delay_val = delay_lane_check(val_surface, val_days, VAL_PERIOD)
    del val_frame
    np.savez(out / "daily_known_validation.npz", **val_daily)
    d0p = coverage["d0_vs_panel"]
    print(
        f"[frozen-gap] d=0 recomputed vs panel labels: unfilled->filled rows "
        f"{d0p['unfilled_to_filled']}, exit-minute changes {d0p['exit_et_changed']}, "
        f"gross changes {d0p['gross_changed']} (max |delta| "
        f"{d0p['gross_max_abs_delta']:.3e})",
        flush=True,
    )
    gap = frozen_val["gap_rule_effect_vs_recomputed_d0"][str(int(REPRO_COST_BPS))]
    print(
        f"[frozen-gap] {FROZEN_KEY} @{int(REPRO_COST_BPS)}: frozen "
        f"{_fmt(gap['frozen_known_usd_per_day'], '+.4f')} vs recomputed "
        f"{_fmt(gap['recomputed_d0_known_usd_per_day'], '+.4f')} $/day "
        f"(delta {_fmt(gap['delta_known_usd_per_day'], '+.4f')}, fills "
        f"{gap['frozen_known_fills']}->{gap['recomputed_d0_known_fills']})",
        flush=True,
    )
    if delay_val.get("all_match"):
        print(
            "[control] the theta.030/h60 d{0,2,4} cells reproduce the published delay "
            f"lane on {VAL_PERIOD} exactly (same basis, same engine)",
            flush=True,
        )

    # 5) the pre-declared selection, frozen here BEFORE any late outcome exists.
    ranked = rank_cells(val_surface)
    chosen = ranked[0]
    selection = selection_section(ranked, val_surface, model_sha, producer, contract_sha)
    _json_atomic(out / "selection.json", selection)
    val_decay = delta_vs_d0(val_surface)
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
        rescue = rescue_table(val_surface, None)
        decision = decision_text(
            chosen, val_surface, None, rescue, frozen_val["reproduction_check"]
        )
        results = {
            "study": STUDY,
            "status": STATUS,
            "contract": contract,
            "coverage": coverage,
            "selection": selection,
            "validation": {
                "surface": val_surface,
                "delta_vs_d0_twin": val_decay,
                "frozen_reference": frozen_val,
                "delay_lane_crosscheck": delay_val,
                "daily_known_npz": str(out / "daily_known_validation.npz"),
            },
            "late": None,
            "rescue": rescue,
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
    late_surface, late_daily = replay_surface(conf_frame, conf_days, CONF_PERIOD, "late")
    frozen_late = frozen_reference_block(conf_frame, conf_days, late_surface, CONF_PERIOD)
    delay_late = delay_lane_check(late_surface, conf_days, CONF_PERIOD)
    del conf_frame
    np.savez(out / "daily_known_late.npz", **late_daily)
    if delay_late.get("all_match"):
        print(
            "[control] the theta.030/h60 d{0,2,4} cells reproduce the published delay "
            f"lane on {CONF_PERIOD} exactly (same basis, same engine)",
            flush=True,
        )
    late_decay = delta_vs_d0(late_surface)
    rescue = rescue_table(val_surface, late_surface)
    decision = decision_text(
        chosen, val_surface, late_surface, rescue, frozen_val["reproduction_check"]
    )
    results = {
        "study": STUDY,
        "status": STATUS,
        "contract": contract,
        "coverage": coverage,
        "selection": selection,
        "validation": {
            "surface": val_surface,
            "delta_vs_d0_twin": val_decay,
            "frozen_reference": frozen_val,
            "delay_lane_crosscheck": delay_val,
            "daily_known_npz": str(out / "daily_known_validation.npz"),
        },
        "late": {
            "surface": late_surface,
            "delta_vs_d0_twin": late_decay,
            "frozen_reference": frozen_late,
            "delay_lane_crosscheck": delay_late,
            "daily_known_npz": str(out / "daily_known_late.npz"),
            "disclosure": DISCLOSURES["late_block"],
        },
        "rescue": rescue,
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
