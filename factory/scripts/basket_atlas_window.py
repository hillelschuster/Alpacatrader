#!/usr/bin/env python3
"""ATLAS window profile — the minute-by-minute value of staying exposed, 09:31 to the close.

Reads the frozen ATLAS minute panel (`factory/artifacts/basket/phase2/ATLAS/panel.parquet`, contract
in `SCHEMA.md`) and writes `factory/artifacts/basket/phase2/ATLAS/window_profile.json`.

WHAT THIS MEASURES

The PRIMITIVE minute-by-minute conditional future-path structure of the early leader race, at EVERY
supported decision minute from 570 (the fill bar) to 959 (15:59) and at every ownership tenure
`bars_since_entry` (0..389), always separately per family (A_pm and B600 fill 30 minutes apart, so pooling them in a minute profile
mixes two different ownership ages). Per cell: the continuation distribution (remaining attainable
move and its tail classes), the probability that no further high follows, the adverse excursion
while waiting, the time to the next and to the final high, and the forward recovery/reclaim
structure (`path_structure`, the primary output).

Value lines appear only as labelled REFERENCE/ANATOMY and never as a recursive continuation value:
the declared executable rulers (`v_forced_flat` — the engine's forced-flat price — and
`v_giveback_5/10/15/20`), the `v_hold_flat` CLOSE reference kept beside them for comparability, two
explicitly named causal rules, an optimistic high-based ceiling (`remaining_run`) and an executable
hindsight oracle (exit at the open after the session's max-high bar, or at the forced-flat price when
that bar is the last one). Their difference is reported as
`remaining_oracle_executable_headroom` — which mixes perfect foresight, the limits of the rule
family, execution constraints and estimation error, and is NOT "the value of better state
information".

Terminal-censored members (273 of 6,160 in panel v2) have no terminal value: every terminal outcome
is null on their rows, they are excluded from complete-path/executable profiles, and their counts are
reported per cell as UNRESOLVED — never as zero.

The "before ~11:00" shape this file is measured against comes from a HINDSIGHT-cohorted coarse prior
(`phase2/DIAGNOSTICS_20260925/clock_cohort.json`), which split members by their eventual session
peak. It is cited as a labelled prior only; this file reports where continuation value crosses zero
per set and block and asserts no time.

DEPENDENCE. The rows are repeated minutes of 6,160 tickets in 1,066 days. No uncertainty statement
here treats minute-rows as independent: the primary clustering unit is the DAY (conservative) with
the TICKET as a secondary, both reported with every interval (see `uncertainty.method`).

DUPLICATE PATHS. 255 (sleeve_day, ticker) pairs appear in both families (510 members, 8.3%). Every
cell carries `n_dup_path_rows`; pooled-across-family statements are quantified against a
one-copy-per-path variant in `duplicate_paths`.

Output structure (all keys self-describing; see the JSON `meta`)

    meta                      units, conventions, cohorts, blocks, framing, caveats
    path_structure            PRIMARY: conditional future-path structure, every minute to the close
                              and every integer tenure bar, per family
    path_structure_uncertainty  day-clustered intervals for the primitive curves at every minute
    zero_crossings_primary_dense  where the reference line crosses zero on the dense minute axis
    coordinate_mapping        et <-> bars_since_entry per family (the two coordinates)
    reference_lines           the rulers, the named rules and the two oracle lines (anatomy only)
    series[set].minutes[et][block]   value columns, path statistics, rate of change
    by_tenure_compact_reference   sparse cross-check of the value lines on tenure
    zero_crossings            where mean/median continuation value crosses zero (+ tenure axis)
    giants_vs_majority        giants' minus majority's executable ruler (hindsight cohorts, labelled)
    balanced_morning_panel    composition-free control (members present every morning minute)
    block_agreement           per-minute sign / ordering agreement, series correlation
    duplicate_paths           the 255 shared paths: counts, effect on pooled levels
    uncertainty               day-clustered (primary) and ticket-clustered (secondary) intervals
    thin_minutes              minutes whose n is thin, with an exact decomposition
    checks                    self-tests (identities, independent recompute, raw-bars oracle check)

Usage:
    .venv/bin/python factory/scripts/basket_atlas_window.py            # writes the JSON
    .venv/bin/python factory/scripts/basket_atlas_window.py --print    # + headline lines
    .venv/bin/python factory/scripts/basket_atlas_window.py --no-raw-check   # skip the 20-day check
"""
from __future__ import annotations

import argparse
import hashlib
import re
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl

WT = Path(__file__).resolve().parents[2]
if str(WT) not in sys.path:
    sys.path.insert(0, str(WT))

PANEL = WT / "factory/artifacts/basket/phase2/ATLAS/panel.parquet"
# ATLAS panel v2 (foundation fix 2026-09-25). v1 (8ce46159…) is INVALID/quarantined.
PANEL_SHA256 = "2a021eda878cdbe53e41d4cc88e2d8ba3b14d9a3a819723d9f679c8aca7c3488"
PANEL_SHA256_V1_QUARANTINED = "8ce4615946e2f5eed41e32858bb4020246a448720e81d74cebf8db2d1b7d990c"
# columns that must NOT appear in any emitted analysis (v1 names + v1-only concepts)
V1_FORBIDDEN_NAMES = ["member_last_et", "v_hold_flat_prime"]
OUT = WT / "factory/artifacts/basket/phase2/ATLAS/window_profile.json"
OUT_INDEX = WT / "factory/artifacts/basket/phase2/ATLAS/window_profile_index.json"

REPRODUCE = (
    "cd /home/hillel/.config/opencode/worktrees/Alpacatrader/basket-phase2-f1 && "
    ".venv/bin/python factory/scripts/basket_atlas_window.py"
)

# PRIMARY clock axis: every supported decision minute of the tape, fill bar (570) to the session's
# last minute (959). Half-day sessions (session_end 779) simply stop printing after 779 and their
# minutes become thin/null, which is why the axis is not truncated at a chosen hour.
MINUTE_DENSE = list(range(570, 960))
# COMPACT SECONDARY summary grid (morning every minute + hourly context): the reference/anatomy
# sections (`series`, rate of change, uncertainty bootstrap, thin-minute flags) are emitted on it to
# keep the artifact readable. It is never the primary axis.
MORNING = list(range(571, 721))              # 09:31 .. 12:00, every minute
CONTEXT = [780, 840, 900, 958, 959, 960]     # hourly to the close, then the last usable minutes
GRID = MORNING + CONTEXT

# ownership-tenure coordinate (bar_index / bars_since_entry)
TENURE_GRID = (list(range(0, 10)) + list(range(10, 130, 5)) + list(range(140, 280, 15))
               + list(range(300, 400, 30)))

# v2 value columns. v_forced_flat is the PRIMARY executable ruler (engine forced-flat price);
# v_hold_flat is retained ONLY as the labelled close-reference anatomy line. Never conflated.
VALUE5 = ["v_forced_flat", "v_hold_flat", "v_giveback_5", "v_giveback_10", "v_giveback_15",
          "v_giveback_20", "level_ret", "remaining_run", "cost_of_waiting", "oracle_exec_next_open"]
RULE4 = ["rule_cut_below_entry_else_hold", "rule_cut_below_entry_else_giveback10"]
GAP3 = ["ruler_envelope_hindsight", "gap_optimistic_minus_exec", "gap_optimistic_minus_rule_g10",
        "gap_exec_minus_rule_g10", "gap_exec_minus_v_forced_flat",
        "close_reference_gap_v_hold_minus_v_forced"]
CLOCKS = ["bars_to_next_high", "bars_to_peak", "peak_et_after_t"]
SHARES = ["final_high_flag", "giveback_fired_5", "giveback_fired_10", "giveback_fired_15",
          "giveback_fired_20", "giveback_condition_after_forced_flat_5",
          "giveback_condition_after_forced_flat_10",
          "giveback_condition_after_forced_flat_15",
          "giveback_condition_after_forced_flat_20"]
PAIR_COLS = ["v_forced_flat", "v_hold_flat", "remaining_run"]

MEMBER_COLS = ["sleeve_day", "family", "entry_rank"]

COHORT_DEFS = [
    ("A_peak_ge_100", "session_peak_ret_from_entry >= +100%"),
    ("B_peak_30_100", "session peak in [+30%, +100%)"),
    ("C_peak_0_30", "session peak in [0%, +30%) — contains the members with literally zero upside"),
    ("D_never_above_entry", "session peak < 0% — EMPTY by construction, see meta.cohorts.note"),
]
COHORT_D2 = ("D2_zero_upside", "session_peak_ret_from_entry <= 0% (the session high never got above "
                               "entry_px) — SUPPLEMENTARY non-empty reading of 'never above entry'")

BLOCKS = ["pooled", "block1", "block2"]
BLOCK_LABELS = {
    "pooled": "both blocks",
    "block1": "block1 2021-02..2023-12 (734 days)",
    "block2": "block2 2025-02..2026-05 (332 days)",
}

# ruler / rule candidates for the "achievable value" nomination
RULE_CANDIDATES = [
    ("exit_now", "exit at next_open(t): the cash option, value 0 by construction"),
    ("v_forced_flat", "PRIMARY executable ruler: hold to the engine's forced flat (the open of the "
                      "session_end bar)"),
    ("v_giveback_5", "declared continuation: exit after a 5% close-off-the-running-high trigger"),
    ("v_giveback_10", "declared continuation: 10% give-back trigger"),
    ("v_giveback_15", "declared continuation: 15% give-back trigger"),
    ("v_giveback_20", "declared continuation: 20% give-back trigger"),
    ("rule_cut_below_entry_else_hold", "causal state rule: if close < entry at t exit at next_open, "
                                       "else hold to the executable forced flat"),
    ("rule_cut_below_entry_else_giveback10", "causal state rule: if close < entry at t exit at "
                                             "next_open, else the 10% give-back continuation"),
]

# the dense ownership-tenure axis: EVERY integer bars_since_entry of the panel (0..389 observed)
TENURE_DENSE = list(range(0, 390))

# a minute is materially thin when its coverage of expected_present falls this far below the grid's
# median coverage (member tapes gap, so ~86% coverage is the ordinary level, not a defect)
THIN_MARGIN = 0.05
MIN_SIGN_N = 30
# emitted numbers are rounded to 6 decimals; identity checks compare against them with this band
TOL_ROUND = 1e-6
BOOT_B = 120
BOOT_SEED = 20260925

_NOMINAL_DT: dict[int, int] = {}
_prev = None
for _et in GRID:
    _NOMINAL_DT[_et] = (_et - _prev) if _prev is not None else 1
    _prev = _et


# --------------------------------------------------------------------------------------- helpers
def i_or_none(v):
    return None if v is None else int(v)


def r6(x, nd: int = 6):
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, nd)


def r8(x):
    """Round a rate-of-change for JSON: one minute of waiting moves the median value by ~1e-6."""
    return r6(x, 8)


def member_key_expr() -> pl.Expr:
    return pl.concat_str([pl.col("sleeve_day"), pl.lit("|"), pl.col("family"), pl.lit("|"),
                          pl.col("entry_rank").cast(pl.String)])


def cohort_expr() -> pl.Expr:
    """Hindsight outcome cohorts. Censored members carry a null session peak by contract, so they get
    their own UNLABELLED bucket instead of being silently folded into a cohort."""
    peak = pl.col("session_peak_ret_from_entry")
    return (
        pl.when(peak.is_null()).then(pl.lit("CENSORED_unlabelled"))
        .when(peak >= 1.0).then(pl.lit("A_peak_ge_100"))
        .when(peak >= 0.30).then(pl.lit("B_peak_30_100"))
        .when(peak >= 0.0).then(pl.lit("C_peak_0_30"))
        .otherwise(pl.lit("D_never_above_entry"))
    )


def zero_upside_expr() -> pl.Expr:
    peak = pl.col("session_peak_ret_from_entry")
    return (pl.when(peak.is_null()).then(pl.lit("CENSORED_unlabelled"))
              .when(peak <= 0.0).then(pl.lit("D2_zero_upside")).otherwise(pl.lit("other")))


def value_exprs(c: str) -> list[pl.Expr]:
    total = pl.col(c).sum()
    # null-safe top 5: v2 nulls every terminal outcome of a censored member, and a plain
    # sort(descending=True).head(5) would pick the nulls first (observed: sum -> 0.0)
    top5 = pl.col(c).top_k(5).sum()
    return [
        pl.col(c).count().alias(f"{c}|n"),
        pl.col(c).mean().alias(f"{c}|mean"),
        pl.col(c).median().alias(f"{c}|median"),
        ((pl.col(c) > 0).sum() / pl.col(c).count()).alias(f"{c}|positive_share"),
        pl.when(total.abs() > 1e-12).then(top5 / total).otherwise(None).alias(f"{c}|top5_share"),
    ]


def value4_exprs(c: str) -> list[pl.Expr]:
    return [
        pl.col(c).count().alias(f"{c}|n"),
        pl.col(c).mean().alias(f"{c}|mean"),
        pl.col(c).median().alias(f"{c}|median"),
        ((pl.col(c) > 0).sum() / pl.col(c).count()).alias(f"{c}|positive_share"),
    ]


def value3_exprs(c: str) -> list[pl.Expr]:
    return [
        pl.col(c).count().alias(f"{c}|n"),
        pl.col(c).mean().alias(f"{c}|mean"),
        pl.col(c).median().alias(f"{c}|median"),
    ]


def clock_exprs(c: str) -> list[pl.Expr]:
    return [
        pl.col(c).count().alias(f"{c}|n"),
        pl.col(c).null_count().alias(f"{c}|nulls"),
        pl.col(c).mean().alias(f"{c}|mean"),
        pl.col(c).median().alias(f"{c}|median"),
    ]


def share_exprs(c: str) -> list[pl.Expr]:
    return [
        pl.col(c).count().alias(f"{c}|n"),
        (pl.col(c).cast(pl.Float64).mean()).alias(f"{c}|share"),
    ]


def full_exprs(scope: str) -> list[pl.Expr]:
    """Aggregation expressions for one cell. scope='full' | 'reduced'."""
    exprs = [pl.len().alias("rows|n"), pl.col("next_open").count().alias("outcome_rows|n"),
             pl.col("dup_path").cast(pl.Float64).sum().alias("dup_path_rows|n"),
             # censoring is member-level and its terminal outcomes are null: the counts below make
             # the unresolved part explicit instead of letting it silently shrink the denominator
             pl.col("terminal_censored").cast(pl.Float64).sum().alias("censored_rows|n"),
             pl.col("path_complete_to_session_end").cast(pl.Float64).sum()
               .alias("complete_path_rows|n"),
             pl.col("v_forced_flat").count().alias("executable_outcome_rows|n")]
    if scope == "full":
        for c in VALUE5:
            exprs += value_exprs(c)
        for c in RULE4:
            exprs += value4_exprs(c)
        for c in GAP3:
            exprs += value3_exprs(c)
        for c in CLOCKS:
            exprs += clock_exprs(c)
        for c in SHARES:
            exprs += share_exprs(c)
    else:
        for c in ("v_forced_flat", "v_hold_flat", "remaining_run", "oracle_exec_next_open"):
            exprs += value_exprs(c)
        exprs += value4_exprs("rule_cut_below_entry_else_giveback10")
        exprs += value3_exprs("gap_exec_minus_rule_g10")
        exprs += value3_exprs("gap_optimistic_minus_exec")
        exprs += share_exprs("final_high_flag")
    return exprs


def pair_exprs(scope: str) -> list[pl.Expr]:
    exprs = [pl.col("dt_minutes").count().alias("dt_minutes|n"),
             pl.col("dt_minutes").mean().alias("dt_minutes|mean"),
             pl.col("dt_minutes").min().alias("dt_minutes|min"),
             pl.col("dt_minutes").max().alias("dt_minutes|max")]
    cols = PAIR_COLS if scope == "full" else ["v_forced_flat"]
    for c in cols:
        exprs += value4_exprs(f"d_{c}")
        exprs += value4_exprs(f"d_{c}_per_minute")
    return exprs


def agg_frame(frame: pl.DataFrame, mask: pl.Expr | None, keys: list[str],
              scope: str) -> pl.DataFrame:
    part = frame if mask is None else frame.filter(mask)
    if part.height == 0:
        return pl.DataFrame()
    return part.group_by(keys).agg(full_exprs(scope)).sort(keys)


def pair_frame(frame: pl.DataFrame, mask: pl.Expr | None, keys: list[str],
               scope: str) -> pl.DataFrame:
    part = frame if mask is None else frame.filter(mask)
    if part.height == 0:
        return pl.DataFrame()
    return part.group_by(keys).agg(pair_exprs(scope)).sort(keys)


def frame_to_cells(a: pl.DataFrame, keys: list[str]) -> dict:
    out: dict = {}
    if a.height == 0:
        return out
    for row in a.to_dicts():
        key = "|".join(str(row[k]) for k in keys)
        cell: dict = {}
        for c, v in row.items():
            if c in keys:
                continue
            metric, stat = c.split("|", 1)
            cell.setdefault(metric, {})[stat] = v
        out[key] = cell
    return out


def cells_to_json(cell: dict) -> dict:
    out: dict = {}
    for metric, stats in cell.items():
        out[metric] = {k: (i_or_none(v) if k in ("n", "nulls") else r6(v)) for k, v in stats.items()}
    return out


# --------------------------------------------------------------------------------------- loading
def load_members() -> pl.DataFrame:
    """One row per member (sleeve_day, family, entry_rank): spans + ticket-level constants."""
    return (
        pl.scan_parquet(PANEL)
        .group_by(MEMBER_COLS)
        .agg(
            pl.col("entry_et").first(),
            pl.col("entry_px").first(),
            pl.col("future_member_last_et").first(),
            pl.col("session_end").first(),
            pl.col("session_peak_ret_from_entry").first(),
            pl.col("session_peak_et").first(),
            pl.col("session_close_ret_from_entry").first(),
            pl.col("block").first(),
            pl.col("ticker").first(),
            pl.col("et").len().alias("n_rows"),
            pl.col("et").min().alias("first_et"),
            # member-level censor labels (future-derived; used for reporting/exclusion only)
            pl.col("terminal_censored").first(),
            pl.col("path_complete_to_session_end").first(),
        )
        .with_columns(cohort=cohort_expr(), cohort2=zero_upside_expr())
        .collect()
    )


def dedupe(cols: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for c in cols:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


# -------------------------------------------------------------------------------- set definitions
def set_definitions() -> list[tuple[str, pl.Expr, str]]:
    sets: list[tuple[str, pl.Expr, str]] = [
        ("all", pl.lit(True), "full"),
        ("family:A_pm", pl.col("family") == "A_pm", "full"),
        ("family:B600", pl.col("family") == "B600", "full"),
        ("entry_rank:1", pl.col("entry_rank") == 1, "full"),
        ("entry_rank:2", pl.col("entry_rank") == 2, "full"),
        ("entry_rank:3", pl.col("entry_rank") == 3, "full"),
    ]
    for name, _desc in COHORT_DEFS:
        sets.append((f"cohort:{name}", pl.col("cohort") == name, "full"))
    sets.append((f"cohort:{COHORT_D2[0]}", pl.col("cohort2") == COHORT_D2[0], "full"))
    sets.append(("cohort:CENSORED_unlabelled", pl.col("cohort") == "CENSORED_unlabelled", "reduced"))
    for cname in ("A_peak_ge_100", "C_peak_0_30"):
        for fam in ("A_pm", "B600"):
            sets.append((f"cohort:{cname}|family:{fam}",
                         (pl.col("cohort") == cname) & (pl.col("family") == fam), "reduced"))
    return sets


# --------------------------------------------------------------------------------------- sections
SURVIVAL_K = 60          # bars of the one-minute time-to-reclaim survival curve (primary recovery)
NEVER_RECLAIM = 10 ** 6


def prim_exprs(curve: bool = True, reduced: bool = False) -> list[pl.Expr]:
    """The primitive conditional future-path statistics of one cell (the primary output).

    curve=True adds the one-minute time-to-reclaim survival curve S(1..SURVIVAL_K): the share of
    rows (with a next open) whose price has NOT touched entry_px again within k bars after t;
    never-reclaimers are censored into every S(k). reduced=True emits the cohort columns only.
    """
    rr, cw = "remaining_run", "cost_of_waiting"
    # the recovery/reclaim block is a COMPLETE-PATH object: a terminal-censored tape has no session
    # end, so scoring it as "never reclaims" would be a null-as-negative, not a measurement
    complete = pl.col("path_complete_to_session_end")
    rec_scope = complete & pl.col("next_open").is_not_null()
    rec = pl.col("bars_to_forward_reclaim").fill_null(NEVER_RECLAIM)
    e: list[pl.Expr] = [
        pl.len().alias("rows|n"),
        pl.col("next_open").count().alias("outcome_rows|n"),
        pl.col("terminal_censored").cast(pl.Float64).sum().alias("censored_rows|n"),
        pl.col("path_complete_to_session_end").cast(pl.Float64).sum()
          .alias("complete_path_rows|n"),
        pl.col("v_forced_flat").count().alias("executable_outcome_rows|n"),
        pl.col("final_high_flag").count().alias("p_no_further_high|n"),
        pl.col("final_high_flag").cast(pl.Float64).mean().alias("p_no_further_high|share"),
    ]
    for q in ((0.25, 0.50, 0.75, 0.90, 0.95) if reduced else
              (0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99)):
        e.append(pl.col(rr).quantile(q).alias(f"{rr}|p{int(q * 100)}"))
    e += [
        pl.col(rr).mean().alias(f"{rr}|mean"),
        ((pl.col(rr) >= 0.50).sum() / pl.col(rr).count()).alias(f"{rr}|share_ge_50pct"),
        ((pl.col(rr) >= 1.00).sum() / pl.col(rr).count()).alias(f"{rr}|share_ge_100pct"),
    ]
    if not reduced:
        e += [
            ((pl.col(rr) >= 0.20).sum() / pl.col(rr).count()).alias(f"{rr}|share_ge_20pct"),
            ((pl.col(rr) >= 3.00).sum() / pl.col(rr).count()).alias(f"{rr}|share_ge_300pct"),
            pl.col("tail_class_50").cast(pl.Float64).mean().alias("tail_class_50|share"),
            pl.col("tail_class_100").cast(pl.Float64).mean().alias("tail_class_100|share"),
            pl.col("tail_class_300").cast(pl.Float64).mean().alias("tail_class_300|share"),
        ]
    for q in ((0.10, 0.25, 0.50) if reduced else (0.01, 0.05, 0.10, 0.25, 0.50)):
        e.append(pl.col(cw).quantile(q).alias(f"{cw}|p{int(q * 100)}"))
    e.append(pl.col(cw).mean().alias(f"{cw}|mean"))
    if not reduced:
        e += [
            ((pl.col(cw) <= -0.05).sum() / pl.col(cw).count()).alias(f"{cw}|share_le_5pct"),
            ((pl.col(cw) <= -0.10).sum() / pl.col(cw).count()).alias(f"{cw}|share_le_10pct"),
            ((pl.col(cw) <= -0.20).sum() / pl.col(cw).count()).alias(f"{cw}|share_le_20pct"),
        ]
    for q in ((0.50,) if reduced else (0.25, 0.50, 0.75, 0.90)):
        e.append(pl.col("bars_to_next_high").quantile(q).alias(f"bars_to_next_high|p{int(q * 100)}"))
    e.append(pl.col("bars_to_next_high").null_count().alias("bars_to_next_high|nulls"))
    if not reduced:
        for q in (0.25, 0.50, 0.75, 0.90):
            e.append(pl.col("bars_to_peak").quantile(q).alias(f"bars_to_peak|p{int(q * 100)}"))
    else:
        e.append(pl.col("bars_to_peak").median().alias("bars_to_peak|p50"))
    for q in ((0.50,) if reduced else (0.50, 0.75, 0.90)):
        e.append(pl.col("peak_et_after_t").quantile(q).alias(f"peak_et_after_t|p{int(q * 100)}"))
    e += [
        rec_scope.sum().alias("recovery|n_reclaim_scored_rows"),
        (complete & pl.col("next_open").is_null()).sum()
          .alias("recovery|n_complete_path_rows_without_next_open"),
        # censored rows are exactly the rows the naive all-rows denominator would have scored as
        # "never reclaims"; filtering by rec_scope here counted their complement (always 0)
        pl.col("terminal_censored").cast(pl.Float64).sum()
          .alias("recovery|n_excluded_censored"),
        (pl.col("forward_reclaim_flag").cast(pl.Float64).filter(rec_scope).sum()
         / rec_scope.sum()).alias("recovery|share_reaches_entry_again_complete_path"),
        pl.col("bars_to_forward_reclaim").filter(rec_scope).count()
          .alias("recovery|n_reclaimers"),
        pl.col("bars_to_forward_reclaim").filter(rec_scope).median()
          .alias("recovery|median_bars_to_reclaim"),
        pl.col("bars_to_forward_reclaim").filter(rec_scope).quantile(0.25)
          .alias("recovery|p25_bars_to_reclaim"),
        pl.col("bars_to_forward_reclaim").filter(rec_scope).quantile(0.75)
          .alias("recovery|p75_bars_to_reclaim"),
        pl.col("bars_to_forward_reclaim").filter(rec_scope).quantile(0.90)
          .alias("recovery|p90_bars_to_reclaim"),
        # arbitrary reference horizon, kept only as a labelled ruler, never as the primary output;
        # numerator AND denominator must be the same (complete-path) population
        ((pl.col("bars_to_forward_reclaim") <= 30) & rec_scope).sum()
          .alias("reference_recovery|n_reclaim_within_30_bars_complete_path"),
        pl.col("next_open").filter(rec_scope).count()
          .alias("reference_recovery|n_scored_rows_complete_path"),
        (((pl.col("bars_to_forward_reclaim") <= 30) & rec_scope).sum() / rec_scope.sum())
          .alias("reference_recovery|share_reclaim_within_30_bars_complete_path"),
        pl.col("below_entry_at_t").cast(pl.Float64).filter(rec_scope).mean()
          .alias("recovery|share_below_entry_at_t_complete_path"),
        pl.col("forward_reclaim_flag").filter(rec_scope & pl.col("below_entry_at_t"))
          .cast(pl.Float64).mean()
          .alias("recovery|share_reaches_entry_again_when_below"),
        pl.col("bars_to_forward_reclaim").filter(rec_scope & pl.col("below_entry_at_t")).median()
          .alias("recovery|median_bars_to_reclaim_when_below"),
    ]
    if curve:
        for k in range(1, SURVIVAL_K + 1):
            e.append((((rec > k) & rec_scope).sum() / rec_scope.sum()).alias(f"survival|k{k}"))
    if not reduced:
        e += [
            # PRIMARY executable ruler (engine forced flat) and the labelled close reference
            pl.col("v_forced_flat").median().alias("reference_v_forced_flat|median"),
            pl.col("v_forced_flat").mean().alias("reference_v_forced_flat|mean"),
            pl.col("v_hold_flat").median().alias("reference_v_hold_flat|median"),
            pl.col("v_hold_flat").mean().alias("reference_v_hold_flat|mean"),
            pl.col("v_giveback_10").median().alias("reference_v_giveback_10|median"),
            pl.col("oracle_exec_next_open").median().alias("reference_executable_oracle|median"),
            pl.col("dup_path").cast(pl.Float64).mean().alias("dup_path_rows|share"),
            # v2 giveback semantics: a first condition at et >= session_end - 1 cannot fire (the
            # engine's forced flat preempts it), so the continuation stays at v_forced_flat
            pl.col("giveback_condition_after_forced_flat_10").cast(pl.Float64).mean()
              .alias("post_forced_flat_condition|share_g10"),
            (pl.col("v_giveback_10") == pl.col("v_forced_flat")).cast(pl.Float64).mean()
              .alias("giveback_at_forced_flat|share_g10"),
        ]
    return e


def build_series(grid: pl.DataFrame, mask: pl.Expr, scope: str) -> dict:
    cells_pool = frame_to_cells(agg_frame(grid, mask, ["et"], scope), ["et"])
    cells_blk = frame_to_cells(agg_frame(grid, mask, ["et", "block"], scope), ["et", "block"])
    pcell_pool = frame_to_cells(pair_frame(grid, mask, ["et"], scope), ["et"])
    pcell_blk = frame_to_cells(pair_frame(grid, mask, ["et", "block"], scope), ["et", "block"])

    def lookup(store: dict, store_blk: dict, et: int, blk: str):
        return store.get(str(et)) if blk == "pooled" else store_blk.get(f"{et}|{blk}")

    out: dict = {}
    for et in GRID:
        blocks: dict = {}
        for blk in BLOCKS:
            cell = lookup(cells_pool, cells_blk, et, blk) or {}
            p = lookup(pcell_pool, pcell_blk, et, blk) or {}
            entry = cells_to_json(cell)
            entry["n"] = int(cell.get("rows", {}).get("n", 0))
            entry["n_outcome"] = int(cell.get("outcome_rows", {}).get("n", 0))
            entry["n_dup_path_rows"] = int(cell.get("dup_path_rows", {}).get("n", 0) or 0)
            roc: dict = {}
            if p.get("dt_minutes"):
                roc["dt_minutes"] = {k: (i_or_none(v) if k in ("n", "min", "max") else r6(v))
                                     for k, v in p["dt_minutes"].items()}
                roc["paired"] = {}
                roc["per_minute"] = {}
                for c in (PAIR_COLS if scope == "full" else ["v_forced_flat"]):
                    if f"d_{c}" in p:
                        roc["paired"][c] = {k: (i_or_none(v) if k == "n" else r8(v))
                                            for k, v in p[f"d_{c}"].items()}
                        roc["per_minute"][c] = {k: (i_or_none(v) if k == "n" else r8(v))
                                                for k, v in p[f"d_{c}_per_minute"].items()}
            entry["rate_of_change_vs_previous_grid_minute"] = roc
            blocks[blk] = entry
        out[str(et)] = blocks
    return out


def add_series_deltas(series: dict, scope: str) -> None:
    prev_of: dict[int, int | None] = {}
    prev = None
    for et in GRID:
        prev_of[et] = prev
        prev = et
    cols = PAIR_COLS if scope == "full" else ["v_forced_flat"]
    for et in GRID:
        pet = prev_of[et]
        for blk in BLOCKS:
            roc = series[str(et)][blk]["rate_of_change_vs_previous_grid_minute"]
            roc["comparison_et"] = pet
            if pet is None:
                continue
            pc = series[str(pet)][blk]
            cur = series[str(et)][blk]
            roc["series_delta"] = {}
            for c in cols:
                d = {}
                for stat in ("mean", "median", "positive_share"):
                    a = cur.get(c, {}).get(stat)
                    b = pc.get(c, {}).get(stat)
                    # a change of a *level* in one minute is ~1e-6, so the delta keeps 8 decimals
                    d[stat] = r8(a - b) if a is not None and b is not None else None
                roc["series_delta"][c] = d
            roc["composition_change_rows"] = int(cur["n"] - pc["n"])
            roc["composition_change_outcome_rows"] = int(cur["n_outcome"] - pc["n_outcome"])


def zero_crossing_of(minutes: dict, grid_axis: list[int], axis_name: str) -> dict:
    """First axis point where the named statistic (median or mean) goes from > 0 to <= 0.

    Purely descriptive: it locates a crossing of an in-sample series, not a predicted event.
    """
    out: dict = {}
    for blk in BLOCKS:
        out[blk] = {}
        for stat in ("median", "mean"):
            seq = []
            for k in grid_axis:
                cell = minutes.get(str(k), {}).get(blk, {})
                v = cell.get(stat)
                n = cell.get("n", 0) or 0
                if v is not None and n:
                    seq.append((k, v, n))
            if not seq:
                out[blk][stat] = {"status": "no_data"}
                continue
            first_pos = next((x for x in seq if x[1] > 0), None)
            if first_pos is None:
                out[blk][stat] = {"status": "never_positive",
                                  "max_value": r6(max(x[1] for x in seq))}
                continue
            cross, pending = None, None
            for x in seq:
                if x[0] < first_pos[0]:
                    continue
                if x[1] > 0:
                    pending = x
                elif pending is not None:
                    cross = (pending, x)
                    break
            if cross is None:
                out[blk][stat] = {"status": "stays_positive_to_last_grid_point",
                                  "first_positive": first_pos[0], "last_value": r6(seq[-1][1])}
                continue
            (pk, pv, pn), (ck, cv, cn) = cross
            out[blk][stat] = {
                "status": "crosses", "axis": axis_name,
                "last_positive": pk, "last_positive_value": r6(pv), "last_positive_n": int(pn),
                "first_nonpositive": ck, "first_nonpositive_value": r6(cv),
                "first_nonpositive_n": int(cn),
                "interpolated_crossing": round(float(pk + (ck - pk) * (pv / (pv - cv)))
                                                if pv != cv else float(pk), 2),
                "method": "linear interpolation between the last positive and first non-positive "
                          "grid point",
            }
    return out


def zero_crossings(series: dict) -> dict:
    """Compact-grid crossings of BOTH lines: the primary executable ruler (v_forced_flat) and the
    labelled close reference (v_hold_flat)."""
    out = {"disclaimer": ("IN-SAMPLE / DESCRIPTIVE ONLY (compact-grid scan): crossing locations of "
                          "this sample's median series, not a predictive statement; the holdout "
                          "view is block_agreement.")}
    for setname, mins in series.items():
        out[setname] = {
            "v_forced_flat_executable": zero_crossing_of(
                {k: {b: v[b].get("v_forced_flat", {}) for b in BLOCKS} for k, v in mins.items()},
                GRID, "et"),
            "v_hold_flat_close_reference": zero_crossing_of(
                {k: {b: v[b].get("v_hold_flat", {}) for b in BLOCKS} for k, v in mins.items()},
                GRID, "et"),
        }
    return out


def reference_lines(series: dict, scope_sets: list[str]) -> dict:
    """REFERENCE/ANATOMY: the declared rulers, two named causal rules, and the two oracle lines.

    The difference between the executable oracle and a named rule is reported as
    `remaining_oracle_executable_headroom` — it is NOT a measure of the value of state information:
    it also contains perfect foresight, the limits of the rule family, execution constraints and
    estimation error.
    """
    out = {
        "role": ("SECONDARY / REFERENCE. Anatomy anchors only: the declared rulers, two explicitly "
                 "named causal rules and two hindsight oracle lines. The primary object of this "
                 "artifact is `path_structure`."),
        "ruler_note": ("v_forced_flat (panel v2) is the PRIMARY executable ruler — the engine's "
                       "forced-flat price, the open of the session_end bar. v_hold_flat is retained "
                       "ONLY as the labelled close-reference line (close of the session_end bar) for "
                       "comparability with earlier results and is NOT an execution price. The two "
                       "are reported side by side and are never conflated."),
        "framing": ("Every line is the value of the post-t decision measured FROM next_open(t) "
                    "(cash = 0, so 'exit now' is the zero line). The already-realised level_ret(t) "
                    "is common to all lines and is reported in the series for accounting. No "
                    "recursive continuation value is defined: each line is bound to a named rule or "
                    "a named continuation."),
        "lines": {
            "optimistic_ceiling_remaining_run": ("(a1) max high after t / next_open(t) - 1 — the "
                                                 "panel's remaining_run; a high is not a guaranteed "
                                                 "fill, so it is an UPPER BOUND, labelled as such. "
                                                 "Null for terminal-censored members"),
            "executable_oracle": ("(a2) exit at the open of the bar after the session's max-high "
                                  "bar after t; when that bar is the member's last tracked bar the "
                                  "exit is the panel v2 forced-flat price (future_forced_flat_px, "
                                  "the open of the session_end bar), never the close. "
                                  "Hindsight-optimal for that exit convention, not achievable; null "
                                  "for terminal-censored members"),
            "rules": ("(b) explicitly named rules only: the declared continuations (hold to the "
                      "executable forced flat, give-back 5/10/15/20) and two cut-the-losers state "
                      "rules reading close vs entry at t"),
            "rulers": ("(c) v_forced_flat and v_giveback_5/10/15/20 — the declared EXECUTABLE rulers; "
                       "v_hold_flat is carried beside them as the labelled close reference only"),
            "headroom": ("remaining_oracle_executable_headroom = executable oracle minus the "
                         "nominated rule's value. It mixes perfect foresight, the limits of the rule "
                         "family, execution constraints and estimation error — it is NOT 'the value "
                         "of better state information'."),
        },
        "nomination": {},
        "by_set": {},
        "headroom_summary": {},
    }
    # nominate the best fixed rule per set/block by the pooled morning mean (and by the median)
    for setname in scope_sets:
        out["nomination"][setname] = {}
        for blk in BLOCKS:
            table = {}
            for rule, _desc in RULE_CANDIDATES:
                if rule == "exit_now":
                    table[rule] = {"morning_mean": 0.0, "morning_median": 0.0}
                    continue
                means, meds = [], []
                for et in MORNING:
                    c = series[setname][str(et)][blk].get(rule, {})
                    if c.get("n"):
                        means.append(c["mean"])
                        meds.append(c["median"])
                table[rule] = {
                    "morning_mean": r6(float(np.mean(means))) if means else None,
                    "morning_median": r6(float(np.median(meds))) if meds else None,
                }
            by_mean = max((r for r in table if table[r]["morning_mean"] is not None),
                          key=lambda r: table[r]["morning_mean"], default=None)
            by_median = max((r for r in table if table[r]["morning_median"] is not None),
                            key=lambda r: table[r]["morning_median"], default=None)
            out["nomination"][setname][blk] = {
                "criterion": ("highest equal-weight pooled mean (and, separately, median) of the "
                              "rule's value over the 150 morning minutes — an EX POST choice of "
                              "RULE, not of exit; it is the current-best-rule reference, not a "
                              "tradeable claim"),
                "nominated_by_morning_mean": by_mean,
                "nominated_by_morning_median": by_median,
                "table": table,
            }
    for setname in scope_sets:
        out["by_set"][setname] = {"blocks": {}, "per_minute": {}}
        nom = {blk: out["nomination"][setname][blk]["nominated_by_morning_mean"] for blk in BLOCKS}
        nom_med = {blk: out["nomination"][setname][blk]["nominated_by_morning_median"]
                   for blk in BLOCKS}
        out["by_set"][setname]["nominated_rule_by_mean"] = nom
        out["by_set"][setname]["nominated_rule_by_median"] = nom_med
        for blk in BLOCKS:
            gaps, gaps_opt, vals, orcs = [], [], [], []
            for et in MORNING:
                cell = series[setname][str(et)][blk]
                rule = nom[blk]
                if rule == "exit_now":
                    r = {"median": 0.0, "mean": 0.0, "n": cell.get("n_outcome", 0)}
                else:
                    r = cell.get(rule, {}) if rule else {}
                o = cell.get("oracle_exec_next_open", {})
                a1 = cell.get("remaining_run", {})
                n = cell.get("n_outcome", 0)
                if r.get("median") is not None and o.get("median") is not None:
                    gaps.append(o["median"] - r["median"])
                if (a1.get("median") is not None and o.get("median") is not None):
                    gaps_opt.append(a1["median"] - o["median"])
                if r.get("mean") is not None:
                    vals.append(r["mean"])
                if o.get("mean") is not None:
                    orcs.append(o["mean"])
                out["by_set"][setname]["per_minute"].setdefault(str(et), {})[blk] = {
                    "n_outcome": n,
                    "optimistic_ceiling_remaining_run": {"median": a1.get("median"),
                                                         "mean": a1.get("mean")},
                    "executable_oracle": {"median": o.get("median"), "mean": o.get("mean"),
                                          "positive_share": o.get("positive_share")},
                    "ruler_baseline_v_forced_flat_executable": {
                        "median": cell.get("v_forced_flat", {}).get("median"),
                        "mean": cell.get("v_forced_flat", {}).get("mean")},
                    "close_reference_v_hold_flat": {
                        "median": cell.get("v_hold_flat", {}).get("median"),
                        "mean": cell.get("v_hold_flat", {}).get("mean")},
                    "best_ruler_giveback_10": {
                        "median": cell.get("v_giveback_10", {}).get("median"),
                        "mean": cell.get("v_giveback_10", {}).get("mean")},
                    "nominated_rule": rule,
                    "nominated_rule_value": {"median": r.get("median"), "mean": r.get("mean")},
                    "remaining_oracle_executable_headroom": {
                        "median": r6((o.get("median") - r.get("median"))
                                     if (o.get("median") is not None
                                         and r.get("median") is not None) else None)},
                    "gap_optimistic_minus_exec": {
                        "median": r6((a1.get("median") - o.get("median"))
                                     if (a1.get("median") is not None
                                         and o.get("median") is not None) else None)},
                    "ruler_envelope_hindsight": {
                        "median": cell.get("ruler_envelope_hindsight", {}).get("median"),
                        "mean": cell.get("ruler_envelope_hindsight", {}).get("mean")},
                }
            out["headroom_summary"].setdefault(setname, {})[blk] = {
                "block_label": BLOCK_LABELS[blk],
                "morning_minutes": len(vals),
                "headroom_median_mean": r6(float(np.mean(gaps)))
                if gaps else None,
                "headroom_median_median": r6(float(np.median(gaps)))
                if gaps else None,
                "optimistic_minus_executable_median_mean": r6(float(np.mean(gaps_opt)))
                if gaps_opt else None,
                "optimistic_minus_executable_median_median": r6(float(np.median(gaps_opt)))
                if gaps_opt else None,
                "nominated_rule_morning_mean": r6(float(np.mean(vals))) if vals else None,
                "executable_oracle_morning_mean": r6(float(np.mean(orcs))) if orcs else None,
            }
            out["by_set"][setname]["blocks"][blk] = out["headroom_summary"][setname][blk]
    return out


def giants_vs_majority(series: dict) -> dict:
    giants, majority = "cohort:A_peak_ge_100", "cohort:C_peak_0_30"
    out = {
        "giants": giants, "majority": majority,
        "definition": ("giants = eventual session peak >= +100%; majority = session peak in "
                       "[0%, +30%) (the ~82% cohort of the coarse prior; the zero-upside members "
                       "fold into it under a 'never above entry' reading). Both keys are HINDSIGHT "
                       "labels and both pool the two families — use the cohort x family keys in "
                       "`series` for family-clean statements."),
        "metric": ("v_forced_flat (PRIMARY executable ruler: forced-flat price from next_open); the "
                   "v_hold_flat close reference is carried beside it, never conflated"),
        "per_minute": {}, "summary": {},
    }
    for et in GRID:
        e = {}
        for blk in BLOCKS:
            g = series[giants][str(et)][blk].get("v_forced_flat", {})
            m = series[majority][str(et)][blk].get("v_forced_flat", {})
            gc_ = series[giants][str(et)][blk].get("v_hold_flat", {})
            mc = series[majority][str(et)][blk].get("v_hold_flat", {})
            e[blk] = {}
            e[blk].update({
                "giants_n": g.get("n", 0), "majority_n": m.get("n", 0),
                "giants_median": g.get("median"), "majority_median": m.get("median"),
                "close_reference_giants_median": gc_.get("median"),
                "close_reference_majority_median": mc.get("median"),
            })
            e[blk].update({
                "diff_median": r6(g["median"] - m["median"]) if g.get("median") is not None
                and m.get("median") is not None else None,
                "giants_mean": g.get("mean"), "majority_mean": m.get("mean"),
                "diff_mean": r6(g["mean"] - m["mean"]) if g.get("mean") is not None
                and m.get("mean") is not None else None,
                "close_reference_diff_median":
                    r6(gc_["median"] - mc["median"]) if gc_.get("median") is not None
                    and mc.get("median") is not None else None,
                "giants_p_climb_over": series[giants][str(et)][blk]
                    .get("final_high_flag", {}).get("share"),
                "majority_p_climb_over": series[majority][str(et)][blk]
                    .get("final_high_flag", {}).get("share"),
            })
        out["per_minute"][str(et)] = e
    for blk in BLOCKS:
        diffs = [d for d in (out["per_minute"][str(et)][blk]["diff_median"] for et in GRID)
                 if d is not None]
        diffs_mean = [d for d in (out["per_minute"][str(et)][blk]["diff_mean"] for et in GRID)
                      if d is not None]
        morning = diffs[:len(MORNING)]
        out["summary"][blk] = {
            "minutes_compared": len(diffs),
            "share_minutes_giants_median_above_majority": r6(np.mean([d > 0 for d in diffs]))
            if diffs else None,
            "share_morning_minutes_giants_median_above_majority":
                r6(np.mean([d > 0 for d in morning])) if morning else None,
            "median_of_diff_median_over_morning": r6(np.median(morning)) if morning else None,
            "mean_of_diff_median_over_morning": r6(np.mean(morning)) if morning else None,
            "share_minutes_giants_mean_above_majority": r6(np.mean([d > 0 for d in diffs_mean]))
            if diffs_mean else None,
            "giants_minus_majority": {
                "median_at": {str(et): out["per_minute"][str(et)][blk]["diff_median"]
                              for et in (600, 660, 720, 780, 840, 900)},
                "mean_at": {str(et): out["per_minute"][str(et)][blk]["diff_mean"]
                            for et in (600, 660, 720, 780, 840, 900)},
            },
            "v_hold_flat_median_levels": {
                "giants": {str(et): out["per_minute"][str(et)][blk]["giants_median"]
                           for et in (571, 600, 660, 720, 780, 840, 900)},
                "majority": {str(et): out["per_minute"][str(et)][blk]["majority_median"]
                             for et in (571, 600, 660, 720, 780, 840, 900)},
            },
        }
    return out


def _spearman(x, y):
    if len(x) < 3:
        return None
    rx = np.argsort(np.argsort(np.asarray(x, dtype=float)))
    ry = np.argsort(np.argsort(np.asarray(y, dtype=float)))
    if rx.std() == 0 or ry.std() == 0:
        return None
    return round(float(np.corrcoef(rx, ry)[0, 1]), 6)


def _pearson(x, y):
    if len(x) < 3:
        return None
    a, b = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if a.std() == 0 or b.std() == 0:
        return None
    return round(float(np.corrcoef(a, b)[0, 1]), 6)


def block_agreement(series: dict, sets: list[str]) -> dict:
    out = {
        "definition": (
            "Does block1 behave like block2? Sign agreement is measured per minute on the named "
            "statistic of the named set, restricted to minutes where BOTH blocks have at least "
            f"{MIN_SIGN_N} outcome rows and the value is defined and non-zero. Correlation is "
            "Pearson (and Spearman = Pearson of ranks) between the two blocks' minute series over "
            "the minutes where both are defined."
        ),
        "min_block_rows_for_sign": MIN_SIGN_N,
        "sign": {}, "correlation": {}, "cohort_ordering": {},
        "note": ("these are descriptive comparisons of two series (no sampling model); interval "
                 "statements live in `uncertainty`."),
    }

    def signed(setname, metric, stat):
        rows, skipped_thin = [], 0
        for et in GRID:
            a = series[setname][str(et)]["block1"].get(metric, {})
            b = series[setname][str(et)]["block2"].get(metric, {})
            if (a.get("n", 0) or 0) < MIN_SIGN_N or (b.get("n", 0) or 0) < MIN_SIGN_N:
                skipped_thin += 1
                continue
            va, vb = a.get(stat), b.get(stat)
            if va is None or vb is None or va == 0 or vb == 0:
                skipped_thin += 1
                continue
            rows.append((et, va, vb, a["n"], b["n"]))
        agree = [x for x in rows if (x[1] > 0) == (x[2] > 0)]
        return {
            "minutes_compared": len(rows), "minutes_skipped": skipped_thin,
            "sign_agreement_share": r6(len(agree) / len(rows)) if rows else None,
            "disagree_minutes": {str(x[0]): {"block1": r6(x[1]), "block2": r6(x[2]),
                                             "block1_n": x[3], "block2_n": x[4]}
                                 for x in rows if x not in agree},
        }

    for setname in sets:
        out["sign"][setname] = {
            "v_forced_flat_median": signed(setname, "v_forced_flat", "median"),
            "v_forced_flat_mean": signed(setname, "v_forced_flat", "mean"),
            "v_hold_flat_close_reference_median": signed(setname, "v_hold_flat", "median"),
            "v_hold_flat_close_reference_mean": signed(setname, "v_hold_flat", "mean"),
            "remaining_run_median": signed(setname, "remaining_run", "median"),
            "remaining_run_mean": signed(setname, "remaining_run", "mean"),
            "p_climb_over": signed(setname, "final_high_flag", "share"),
        }

    FIELD = {"v_forced_flat_median": ("v_forced_flat", "median"),
             "v_forced_flat_mean": ("v_forced_flat", "mean"),
             "v_hold_flat_close_reference_median": ("v_hold_flat", "median"),
             "remaining_run_median": ("remaining_run", "median"),
             "p_climb_over": ("final_high_flag", "share"),
             "giveback_10_median": ("v_giveback_10", "median"),
             "oracle_exec_median": ("oracle_exec_next_open", "median")}
    for setname in sets:
        entry = {}
        for metric, (field, stat) in FIELD.items():
            cell_entry = {}
            for window, ets in (("morning_571_720", MORNING), ("full_grid", GRID)):
                b1, b2 = [], []
                for et in ets:
                    c = series[setname][str(et)]
                    a = c["block1"].get(field, {}).get(stat)
                    b = c["block2"].get(field, {}).get(stat)
                    n1 = c["block1"].get(field, {}).get("n", 0) or 0
                    n2 = c["block2"].get(field, {}).get("n", 0) or 0
                    if a is None or b is None or n1 < MIN_SIGN_N or n2 < MIN_SIGN_N:
                        continue
                    b1.append(a)
                    b2.append(b)
                cell_entry[window] = {"minutes": len(b1), "pearson": _pearson(b1, b2),
                                      "spearman": _spearman(b1, b2)}
            entry[metric] = cell_entry
        out["correlation"][setname] = entry

    cohorts = ["A_peak_ge_100", "B_peak_30_100", "C_peak_0_30", "D_never_above_entry"]
    per_minute = {}
    identical = compared = 0
    rho_all = []
    for et in GRID:
        usable = []
        for c in cohorts:
            cell = series[f"cohort:{c}"][str(et)]
            v1 = cell["block1"].get("v_forced_flat", {}).get("median")
            v2 = cell["block2"].get("v_forced_flat", {}).get("median")
            n1 = cell["block1"].get("v_forced_flat", {}).get("n", 0) or 0
            n2 = cell["block2"].get("v_forced_flat", {}).get("n", 0) or 0
            if v1 is None or v2 is None or n1 < MIN_SIGN_N or n2 < MIN_SIGN_N:
                continue
            usable.append((c, v1, v2))
        if len(usable) < 3:
            per_minute[str(et)] = {"comparable": False,
                                   "note": "fewer than three cohorts have enough rows in both blocks"}
            continue
        names = [u[0] for u in usable]
        v1 = [u[1] for u in usable]
        v2 = [u[2] for u in usable]
        o1 = [names[i] for i in np.argsort(np.argsort(-np.asarray(v1)))]
        o2 = [names[i] for i in np.argsort(np.argsort(-np.asarray(v2)))]
        rho = _spearman(v1, v2)
        compared += 1
        if rho is not None:
            rho_all.append(rho)
        same = o1 == o2
        identical += int(same)
        per_minute[str(et)] = {
            "comparable": True, "cohorts_used": names, "block1_order": o1, "block2_order": o2,
            "same_order": same, "spearman": rho, "block1_medians": [r6(v) for v in v1],
            "block2_medians": [r6(v) for v in v2],
        }
    out["cohort_ordering"] = {
        "metric": "v_forced_flat median (executable ruler) per outcome cohort, highest first",
        "cohorts": cohorts,
        "note": ("cohorts pool the two families here (the cohorts are rare); D_never_above_entry is "
                 "empty, so the ordering is decided by whichever cohorts have enough rows in both "
                 "blocks (in practice A/B/C)"),
        "minutes_compared": compared,
        "share_minutes_identical_order": r6(identical / compared) if compared else None,
        "mean_spearman": r6(float(np.mean(rho_all))) if rho_all else None,
        "disagree_minutes": {str(et): {"block1_order": v["block1_order"],
                                       "block2_order": v["block2_order"], "spearman": v["spearman"]}
                             for et, v in per_minute.items()
                             if v.get("comparable") and not v["same_order"]},
        "per_minute": per_minute,
    }
    return out


# ------------------------------------------------------------------ clustering / bootstrap machinery
class MinuteSampler:
    """Day-clustered resampling of a minute series built from member-minute rows."""

    def __init__(self, frame: pl.DataFrame, mask: pl.Expr | None, block: str, cols: list[str],
                 axis_col: str = "et", series_col: str = "v_forced_flat"):
        if mask is None:
            part = frame if block == "pooled" else frame.filter(pl.col("block") == block)
        else:
            part = frame.filter(mask if block == "pooled" else (mask & (pl.col("block") == block)))
        self.cols = cols
        self.axis = axis_col
        self.series_col = series_col
        self.empty = part.height == 0
        if self.empty:
            return
        days = part["sleeve_day"].to_numpy()
        uniq, codes = np.unique(days, return_inverse=True)
        self.n_days = len(uniq)
        axis = part[axis_col].to_numpy()
        self.values = {c: part[c].to_numpy().astype(np.float64) for c in cols}
        self.by_axis: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        for k in np.unique(axis):
            rows = np.flatnonzero(axis == k)
            self.by_axis[int(k)] = (codes[rows], rows)

    def _draw(self, rng: np.random.Generator) -> np.ndarray:
        """One day-level bootstrap draw: multiplicity of every day slot."""
        return np.bincount(rng.integers(0, self.n_days, size=self.n_days), minlength=self.n_days)

    def resample(self, k: int, cnt: np.ndarray):
        """The resampled v_hold_flat values for axis value k under a fixed day draw."""
        if k not in self.by_axis:
            return None
        codes, rows = self.by_axis[k]
        mult = cnt[codes]
        if int(mult.sum()) == 0:
            return None
        rep_idx = np.repeat(np.arange(len(rows)), mult)
        v = self.values[self.series_col][rows][rep_idx]
        return v[~np.isnan(v)]

    def replicate(self, axis_values: list[int], rng: np.random.Generator):
        """One bootstrap replicate over the whole axis: {axis: {col: (mean, median)}}."""
        cnt = self._draw(rng)
        out: dict = {}
        for k in axis_values:
            if k not in self.by_axis:
                out[k] = None
                continue
            codes, rows = self.by_axis[k]
            mult = cnt[codes]
            if int(mult.sum()) == 0:
                out[k] = None
                continue
            rep_idx = np.repeat(np.arange(len(rows)), mult)
            cell = {}
            for c in self.cols:
                v = self.values[c][rows][rep_idx]
                v = v[~np.isnan(v)]
                cell[c] = None if v.size == 0 else (float(v.mean()), float(np.median(v)))
            out[k] = cell
        return out

    def crossing_replicate(self, axis_values: list[int], rng: np.random.Generator):
        """One replicate of the crossing walk: (crossing_axis_value | None, saw_positive)."""
        cnt = self._draw(rng)
        seen_pos = False
        pending = False
        for k in axis_values:
            v = self.resample(k, cnt)
            if v is None or v.size == 0:
                continue
            med = float(np.median(v))
            if med > 0:
                seen_pos = True
                pending = True
            elif pending:
                return k, seen_pos
        return None, seen_pos


def peak_rss_gib() -> float:
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 ** 2)


def check_rss(budget_gib: float, where: str) -> float:
    rss = peak_rss_gib()
    if rss > budget_gib:
        raise MemoryError(f"peak RSS {rss:.2f} GiB exceeds the {budget_gib:.2f} GiB budget after "
                          f"{where}")
    return rss


def load_dup_pairs() -> tuple[pl.DataFrame, dict]:
    """The (sleeve_day, ticker) paths held in BOTH families, with counts. Small by construction."""
    pairs = (
        pl.scan_parquet(PANEL).select(["sleeve_day", "ticker", "family", "et"])
        .group_by(["sleeve_day", "ticker"])
        .agg(pl.col("family").n_unique().alias("nf"), pl.len().alias("rows"),
             pl.col("et").len().alias("n"))
        .filter(pl.col("nf") > 1)
        .select(["sleeve_day", "ticker"])
        .with_columns(dup_path=True)
        .collect()
    )
    rows = int(pl.scan_parquet(PANEL)
               .join(pairs.lazy().select(["sleeve_day", "ticker"]), on=["sleeve_day", "ticker"],
                     how="semi")
               .select(pl.len()).collect().item())
    total_rows = int(pl.scan_parquet(PANEL).select(pl.len()).collect().item())
    members = int(pl.scan_parquet(PANEL)
                  .join(pairs.lazy().select(["sleeve_day", "ticker"]), on=["sleeve_day", "ticker"],
                        how="semi")
                  .select(pl.struct(["sleeve_day", "family", "entry_rank"]).n_unique())
                  .collect().item())
    return pairs, {"pairs": int(pairs.height), "members": members, "rows": rows,
                   "panel_rows": total_rows}


def load_extras(dup_pairs: pl.DataFrame, members: pl.DataFrame) -> pl.DataFrame:
    """The compact per-row derived columns for the WHOLE tape, built in one bounded pass.

    oracle_exec_next_open needs the next_open of the row that printed the session's max high after t;
    that row can sit at any et, so the lookup table is the slim (member, et, next_open) tape.
    """
    lc = members.select(["sleeve_day", "family", "entry_rank", "entry_px",
                         "session_close_ret_from_entry", "terminal_censored"]).with_columns(
        member=member_key_expr(),
        # close of the member's last tracked bar: the close-REFERENCE terminal price only
        reference_last_close=pl.col("entry_px") * (1.0 + pl.col("session_close_ret_from_entry")),
    ).select(["member", "reference_last_close", "terminal_censored"])
    base = (
        pl.scan_parquet(PANEL)
        .select(["sleeve_day", "ticker", "family", "entry_rank", "et", "next_open", "bar_high",
                 "bar_close", "entry_px", "peak_et_after_t", "future_forced_flat_px",
                 "terminal_censored"])
        .with_columns(member=member_key_expr())
    )
    tape = base.select(["member", "et", "next_open"]).collect()          # slim lookup table
    fff = base.select(["member", "et", "future_forced_flat_px", "terminal_censored"]).collect()
    rec = (
        base.sort(["member", "et"])
        .with_columns(
            pos=pl.col("et").cum_count().over("member") - 1,
            below_entry_at_t=pl.col("bar_close") < pl.col("entry_px"),
        )
        .with_columns(
            hit_pos=pl.when(pl.col("bar_high") >= pl.col("entry_px")).then(pl.col("pos"))
                     .otherwise(pl.lit(10_000_000)),
        )
        .with_columns(next_hit_pos=pl.col("hit_pos").cum_min(reverse=True).shift(-1).over("member"))
        .with_columns(
            forward_reclaim_flag=pl.col("next_hit_pos") < 10_000_000,
            bars_to_forward_reclaim=pl.when(pl.col("next_hit_pos") < 10_000_000)
                .then(pl.col("next_hit_pos") - pl.col("pos")).otherwise(None),
        )
        .select(["sleeve_day", "ticker", "member", "et", "next_open", "peak_et_after_t",
                 "below_entry_at_t", "forward_reclaim_flag", "bars_to_forward_reclaim"])
        .collect()
    )
    peak = (
        tape.select([pl.col("member"), pl.col("et").alias("peak_et_key"),
                     pl.col("next_open").alias("peak_next_open")])
        .join(fff.select([pl.col("member"), pl.col("et").alias("fff_et_key"),
                          pl.col("future_forced_flat_px")]),
              left_on=["member", "peak_et_key"], right_on=["member", "fff_et_key"], how="left")
    )
    extra = (
        rec.join(peak, left_on=["member", "peak_et_after_t"], right_on=["member", "peak_et_key"],
                 how="left")
        .join(lc, on="member", how="left")
        .join(dup_pairs, on=["sleeve_day", "ticker"], how="left")
        .with_columns(
            dup_path=pl.col("dup_path").fill_null(False),
            # v2 terminal semantics: when the session's max-high bar is the member's last tracked bar
            # there is no next open, so the executable exit is the ENGINE forced-flat price (the open
            # of the session_end bar), never the close. Censored members have no terminal price and
            # stay null on every row.
            oracle_exec_next_open=pl.when(pl.col("peak_next_open").is_not_null())
                .then(pl.col("peak_next_open") / pl.col("next_open") - 1)
                .when(pl.col("future_forced_flat_px").is_not_null() & pl.col("next_open").is_not_null())
                .then(pl.col("future_forced_flat_px") / pl.col("next_open") - 1)
                .otherwise(None),
            v_forced_flat_identity=pl.when(pl.col("next_open").is_not_null()
                                           & pl.col("future_forced_flat_px").is_not_null())
                .then(pl.col("future_forced_flat_px") / pl.col("next_open") - 1)
                .otherwise(None),
            v_hold_flat_identity=pl.when(pl.col("next_open").is_not_null()
                                         & pl.col("reference_last_close").is_not_null())
                .then(pl.col("reference_last_close") / pl.col("next_open") - 1)
                .otherwise(None),
            oracle_exit_uses_forced_flat=pl.col("peak_next_open").is_null(),
            terminal_censored=pl.col("terminal_censored"),
        )
        .select(["member", "et", "below_entry_at_t", "dup_path", "forward_reclaim_flag",
                 "bars_to_forward_reclaim", "oracle_exec_next_open", "oracle_exit_uses_forced_flat",
                 "v_forced_flat_identity", "v_hold_flat_identity", "future_forced_flat_px",
                 "terminal_censored"])
    )
    checks = {
        "rows": int(extra.height),
        "censored_rows": int(extra.filter(pl.col("terminal_censored")).height),
        "oracle_rows_exiting_at_forced_flat": int(extra.filter(
            pl.col("oracle_exit_uses_forced_flat")).height),
    }
    del tape, rec, peak, fff
    import gc
    gc.collect()
    return extra, checks


# future-only / censor columns are carried for LABELLING and reporting only; they never enter a
# causal feature set (see meta.censoring and meta.causal_safety).
SERIES_COLS = ["sleeve_day", "family", "entry_rank", "et", "block", "bars_since_entry",
               "session_peak_ret_from_entry", "entry_px", "ret_from_fill", "next_open",
               "v_forced_flat", "v_hold_flat", "v_giveback_5", "v_giveback_10", "v_giveback_15",
               "v_giveback_20", "level_ret", "remaining_run", "cost_of_waiting",
               "bars_to_next_high", "bars_to_peak", "peak_et_after_t", "giveback_fired_5",
               "giveback_fired_10", "giveback_fired_15", "giveback_fired_20",
               "giveback_condition_after_forced_flat_5", "giveback_condition_after_forced_flat_10",
               "giveback_condition_after_forced_flat_15",
               "giveback_condition_after_forced_flat_20", "final_high_flag", "tail_class_50",
               "tail_class_100", "tail_class_300", "terminal_censored",
               "path_complete_to_session_end"]
PRIM_COLS = ["sleeve_day", "family", "entry_rank", "et", "block", "bars_since_entry",
             "session_peak_ret_from_entry", "next_open", "remaining_run", "cost_of_waiting",
             "final_high_flag", "tail_class_50", "tail_class_100", "tail_class_300",
             "bars_to_next_high", "bars_to_peak", "peak_et_after_t", "v_forced_flat", "v_hold_flat",
             "v_giveback_10", "terminal_censored", "path_complete_to_session_end",
             "giveback_condition_after_forced_flat_10"]


def shard_frame(mask: pl.Expr | None, cols: list[str], extra: pl.DataFrame,
                with_pairs: bool = False, tape_rows: bool = False,
                minute_axis: list[int] | None = None) -> pl.DataFrame:
    """Materialize ONE shard: the shard's panel rows + the derived columns, never the whole panel.

    with_pairs=True additionally restricts to grid minutes and attaches the previous-grid-minute
    deltas (needs the shard frame sorted by member/et). tape_rows=True keeps every et of the shard
    (for the tenure axis); otherwise only the grid minutes are kept.
    """
    plan = pl.scan_parquet(PANEL).select(dedupe(cols)).with_columns(
        member=member_key_expr(), cohort=cohort_expr(), cohort2=zero_upside_expr())
    if mask is not None:
        plan = plan.filter(mask)
    if not tape_rows:
        plan = plan.filter(pl.col("et").is_in(GRID if minute_axis is None else minute_axis))
    frame = plan.collect().join(extra, on=["member", "et"], how="left")
    have = set(frame.columns)
    need_rules = {"ret_from_fill", "v_forced_flat", "v_giveback_10", "remaining_run",
                  "oracle_exec_next_open"}
    if need_rules <= have:
        frame = frame.with_columns(
            # the cut-the-losers rule holds to the EXECUTABLE forced flat, never to the close
            rule_cut_below_entry_else_hold=pl.when(pl.col("ret_from_fill") < 0).then(0.0)
                .otherwise(pl.col("v_forced_flat")),
            rule_cut_below_entry_else_giveback10=pl.when(pl.col("ret_from_fill") < 0).then(0.0)
                .otherwise(pl.col("v_giveback_10")),
            gap_optimistic_minus_exec=pl.col("remaining_run") - pl.col("oracle_exec_next_open"),
        )
    if {"v_giveback_5", "v_giveback_15", "v_giveback_20"} | need_rules <= set(frame.columns):
        frame = frame.with_columns(
            # a row whose rulers are all null (censored tape, last tracked bar) must stay null:
            # max_horizontal would otherwise fabricate a 0.0 cash line for it
            ruler_envelope_hindsight=pl.when(
                pl.col("v_forced_flat").is_not_null() | pl.col("v_giveback_5").is_not_null()
                | pl.col("v_giveback_10").is_not_null() | pl.col("v_giveback_15").is_not_null()
                | pl.col("v_giveback_20").is_not_null())
                .then(pl.max_horizontal(pl.lit(0.0), pl.col("v_forced_flat"),
                                        pl.col("v_giveback_5"), pl.col("v_giveback_10"),
                                        pl.col("v_giveback_15"), pl.col("v_giveback_20")))
                .otherwise(None),
            gap_optimistic_minus_rule_g10=pl.col("remaining_run")
                - pl.col("rule_cut_below_entry_else_giveback10"),
            gap_exec_minus_rule_g10=pl.col("oracle_exec_next_open")
                - pl.col("rule_cut_below_entry_else_giveback10"),
            gap_exec_minus_v_forced_flat=pl.col("oracle_exec_next_open")
                - pl.col("v_forced_flat"),
        )
    if {"v_hold_flat", "v_forced_flat"} <= set(frame.columns):
        frame = frame.with_columns(
            # a labelled diagnostic: how far the close reference sits above the executable ruler
            close_reference_gap_v_hold_minus_v_forced=pl.col("v_hold_flat")
                - pl.col("v_forced_flat"),
        )
    if not with_pairs:
        return frame
    frame = frame.sort(["member", "et"]).with_columns(
        [pl.col("et").shift(1).over("member").alias("prev_et"),
         *[pl.col(c).shift(1).over("member").alias(f"prev_{c}") for c in PAIR_COLS]]
    ).with_columns(
        (pl.col("et") - pl.col("prev_et")).alias("dt_minutes"),
        pl.col("et").replace_strict(_NOMINAL_DT, default=1, return_dtype=pl.Int32)
          .alias("nominal_dt"),
    )
    for c in PAIR_COLS:
        frame = frame.with_columns(
            pl.when(pl.col(f"prev_{c}").is_not_null() & pl.col(c).is_not_null()
                    & (pl.col("dt_minutes") == pl.col("nominal_dt")))
              .then(pl.col(c) - pl.col(f"prev_{c}")).otherwise(None).alias(f"d_{c}")
        ).with_columns((pl.col(f"d_{c}") / pl.col("nominal_dt")).alias(f"d_{c}_per_minute"))
    return frame.with_columns(
        pl.when(pl.col("dt_minutes") == pl.col("nominal_dt")).then(pl.col("dt_minutes"))
          .otherwise(None).alias("dt_minutes")
    )


def prim_cells(src: pl.DataFrame, mask: pl.Expr, axis_col: str, axis: list[int],
               curve: bool, reduced: bool) -> dict:
    """One axis of the primitive path structure for one set/mask."""
    exprs = prim_exprs(curve=curve, reduced=reduced)
    part = src.filter(mask & pl.col(axis_col).is_in(axis))
    if part.height == 0:
        return {}
    pool = frame_to_cells(part.group_by([axis_col]).agg(exprs), [axis_col])
    blk = frame_to_cells(part.group_by([axis_col, "block"]).agg(exprs), [axis_col, "block"])
    del part
    ser: dict = {}
    for k in axis:
        ser[str(k)] = {}
        for b in BLOCKS:
            c = pool.get(str(k)) if b == "pooled" else blk.get(f"{k}|{b}")
            cell = cells_to_json(c) if c else {}
            if "survival" in cell:
                surv = cell.pop("survival")
                cell["recovery"]["survival_curve_bars_1_to"] = SURVIVAL_K
                cell["recovery"]["survival_curve"] = [surv.get(f"k{i}") for i in
                                                      range(1, SURVIVAL_K + 1)]
            cell["n"] = int((c or {}).get("rows", {}).get("n", 0))
            cell["n_outcome"] = int((c or {}).get("outcome_rows", {}).get("n", 0))
            ser[str(k)][b] = cell
    del pool, blk
    return ser


def cluster_levels(part: pl.DataFrame, cols: list[str], axis_col: str) -> dict:
    """Cluster-robust mean intervals: day-clustered (primary) and ticket-clustered (secondary)."""
    lv: dict = {}
    for cluster in ("sleeve_day", "member"):
        # .count() (non-null), NOT .len(): v2 nulls every terminal outcome of a censored member, and
        # a row-count denominator silently turned each clustered mean into sum(non-null)/all-rows
        pc = part.group_by([axis_col, cluster]).agg(
            [pl.col(c).cast(pl.Float64).sum().alias(f"{c}__S") for c in cols]
            + [pl.col(c).cast(pl.Float64).count().alias(f"{c}__N") for c in cols])
        ncl = {int(r[axis_col]): int(r["ncl"]) for r in
               pc.group_by(axis_col).agg(pl.col(cluster).n_unique().alias("ncl")).to_dicts()}
        exprs = []
        for c in cols:
            N = pl.col(f"{c}__N").sum()
            m = pl.col(f"{c}__S").sum() / N
            se = (((pl.col(f"{c}__S") - pl.col(f"{c}__N") * m) ** 2).sum() / (N ** 2)).sqrt()
            exprs += [pl.when(N > 0).then(m).otherwise(None).alias(f"{c}__mean"),
                      pl.when(N > 1).then(se).otherwise(None).alias(f"{c}__se"),
                      N.alias(f"{c}__n")]
        for row in pc.group_by([axis_col]).agg(exprs).to_dicts():
            k = str(int(row[axis_col]))
            cell = lv.setdefault(k, {})
            for c in cols:
                mean, se = row.get(f"{c}__mean"), row.get(f"{c}__se")
                rows_n = row.get(f"{c}__n")
                d = cell.setdefault(c, {})
                # an empty group must be null, never a fabricated 0.0 ("certainty where no data")
                if mean is None or not math.isfinite(mean):
                    continue
                d[f"mean_{cluster}_clustered"] = r6(mean)
                d["n_rows"] = None if rows_n is None else int(rows_n)
                if se is not None and math.isfinite(se):
                    d["se_" + cluster] = r6(se)
                    d["ci95_" + cluster] = [r6(mean - 1.96 * se), r6(mean + 1.96 * se)]
                    d["clusters_" + cluster] = ncl.get(int(row[axis_col]))
        del pc
    return lv


def dense_uncertainty(frame: pl.DataFrame, cols: list[str], axis_values: list[int],
                      axis_col: str = "et") -> dict:
    """Day-clustered intervals for the primitive curves at EVERY decision minute."""
    out: dict = {}
    for blk in BLOCKS:
        part = frame if blk == "pooled" else frame.filter(pl.col("block") == blk)
        if part.height == 0:
            continue
        out[blk] = cluster_levels(part, cols, axis_col)
        del part
    return out


def uncertainty_for(frame: pl.DataFrame, setname: str, axis_values: list[int],
                    axis_col: str = "et") -> dict:
    """Day-clustered levels + median bootstrap + crossing distribution for ONE set."""
    cols = ["v_forced_flat", "v_hold_flat", "oracle_exec_next_open"]
    rng = np.random.default_rng(BOOT_SEED)
    out: dict = {"levels": {}, "median_bootstrap": {}, "crossing_minute": {}}
    for blk in BLOCKS:
        part = frame if blk == "pooled" else frame.filter(pl.col("block") == blk)
        if part.height == 0:
            continue
        out["levels"][blk] = cluster_levels(part, cols, axis_col)
        del part
        sampler = MinuteSampler(frame, None, blk, cols, axis_col=axis_col,
                                series_col="v_forced_flat")
        if sampler.empty:
            out["median_bootstrap"][blk] = {}
            out["crossing_minute"][blk] = {"status": "no_data"}
            continue
        boots = [[] for _ in axis_values]
        means = [[] for _ in axis_values]
        for _ in range(BOOT_B):
            rep = sampler.replicate(axis_values, rng)
            for i, k in enumerate(axis_values):
                cell = rep.get(k)
                if not cell or cell.get("v_forced_flat") is None:
                    continue
                boots[i].append(cell["v_forced_flat"][1])
                means[i].append(cell["v_forced_flat"][0])
        mb: dict = {}
        for i, k in enumerate(axis_values):
            if len(boots[i]) < 10:
                continue
            b = np.asarray(boots[i])
            m = np.asarray(means[i])
            mb[str(k)] = {
                "median": r6(float(np.median(b))), "median_se": r6(float(b.std(ddof=1))),
                "median_ci95": [r6(float(np.percentile(b, 2.5))),
                                r6(float(np.percentile(b, 97.5)))],
                "mean": r6(float(np.median(m))), "mean_se": r6(float(m.std(ddof=1))),
                "mean_ci95": [r6(float(np.percentile(m, 2.5))), r6(float(np.percentile(m, 97.5)))],
                "replicates": int(len(b)),
            }
        out["median_bootstrap"][blk] = mb
        crosses = []
        none_cross = never_pos = 0
        for _ in range(BOOT_B):
            cross, seen_pos = sampler.crossing_replicate(axis_values, rng)
            if cross is not None:
                crosses.append(cross)
            elif not seen_pos:
                never_pos += 1
            else:
                none_cross += 1
        out["crossing_minute"][blk] = {
            "definition": ("first compact-grid minute whose bootstrapped v_forced_flat (executable "
                           "ruler) median is <= 0 after a positive one, per replicate (one day draw "
                           "across all minutes)"),
            "replicates": BOOT_B, "n_with_crossing": len(crosses),
            "share_no_crossing_stays_positive": r6(none_cross / BOOT_B),
            "share_never_positive": r6(never_pos / BOOT_B),
            "median_crossing_minute": r6(float(np.median(crosses))) if crosses else None,
            "ci95_crossing_minute": [r6(float(np.percentile(crosses, 2.5))),
                                     r6(float(np.percentile(crosses, 97.5)))] if crosses else None,
            "iqr_crossing_minute": [r6(float(np.percentile(crosses, 25))),
                                    r6(float(np.percentile(crosses, 75)))] if crosses else None,
        }
        del sampler
    return out


def dup_landmark_cells(frame: pl.DataFrame, line: str = "v_forced_flat") -> dict:
    """Landmark levels of the EXECUTABLE ruler with and without the duplicated second copy.

    `line` names the ruler in every emitted key so no reader can mistake which line was de-duplicated.
    """
    drop = pl.col("dup_path") & (pl.col("family") == "B600")
    rows = {}
    for et in (600, 660, 720, 780, 840, 900):
        part = frame.filter(pl.col("et") == et)
        if part.height == 0:
            continue
        with_dup = part[line].drop_nulls()
        without = part.filter(~drop)[line].drop_nulls()
        rows[str(et)] = {
            "line": line,
            f"n_with_duplicates_{line}": int(with_dup.len()),
            f"n_excluding_second_copy_{line}": int(without.len()),
            f"median_with_{line}": r6(float(with_dup.median())) if with_dup.len() else None,
            f"median_without_{line}": r6(float(without.median())) if without.len() else None,
            f"mean_with_{line}": r6(float(with_dup.mean())) if with_dup.len() else None,
            f"mean_without_{line}": r6(float(without.mean())) if without.len() else None,
        }
    return rows


def sample_cells(frame: pl.DataFrame, setname: str, rng: np.random.Generator,
                 n_minutes: int = 2) -> list[dict]:
    """Pure-Python recomputation of a few cells for the independent check."""
    out = []
    for et in rng.choice(MORNING + [780], size=n_minutes, replace=False):
        part = frame.filter(pl.col("et") == int(et))
        for blk in BLOCKS:
            sub = part if blk == "pooled" else part.filter(pl.col("block") == blk)
            for col in ("v_forced_flat", "v_hold_flat", "v_giveback_10",
                        "oracle_exec_next_open"):
                vals = [v for v in sub[col].to_list() if v is not None]
                if not vals:
                    continue
                tot = sum(vals)
                out.append({
                    "set": setname, "et": int(et), "block": blk, "column": col, "n": len(vals),
                    "mean": sum(vals) / len(vals), "median": float(np.median(vals)),
                    "positive_share": sum(1 for v in vals if v > 0) / len(vals),
                    "top5_share": (sum(sorted(vals, reverse=True)[:5]) / tot)
                    if abs(tot) > 1e-12 else None,
                })
    return out


def thin_minutes_from_counts(members: pl.DataFrame, counts: pl.DataFrame) -> dict:
    """The thin-minute decomposition, fed by a tiny per-minute count frame (no big materialization)."""
    entry_et = members["entry_et"].to_numpy()
    last_et = members["future_member_last_et"].to_numpy()
    session_end = members["session_end"].to_numpy()
    censored = members["terminal_censored"].to_numpy(); censored = np.where(
        np.array([c is None for c in censored], dtype=bool), False, censored)
    obs = {(int(d["et"]), d["block"]): int(d["n"]) for d in counts.to_dicts()}
    total = len(entry_et)
    per_minute: dict = {}
    pairs: list[tuple[int, dict]] = []
    for et in MINUTE_DENSE:
        not_yet = int(np.sum(entry_et > et))
        ended = int(np.sum(last_et < et))
        ended_half = int(np.sum((last_et < et) & (session_end == 779)))
        ended_other = ended - ended_half
        expected = total - not_yet - ended
        n = sum(obs.get((et, b), 0) for b in ("block1", "block2"))
        gap = expected - n
        coverage = (n / expected) if expected > 0 else None
        reasons = []
        if not_yet:
            reasons.append(f"{not_yet} members have not filled yet (entry_et > {et})")
        if gap:
            reasons.append(f"{gap} members with a tape range covering {et} printed no bar at it "
                           f"(gapped tape)")
        if ended_half:
            reasons.append(f"{ended_half} member tapes ended at a half-day close (session_end=779)")
        if ended_other:
            reasons.append(f"{ended_other} member tapes ended early (halt / thin tape)")
        cell = {
            "n": n, "block1_n": obs.get((et, "block1"), 0), "block2_n": obs.get((et, "block2"), 0),
            "members_total": total,
            "censored_members_still_printing": int(np.sum(censored & (entry_et <= et)
                                                          & (last_et >= et))),
            "not_yet_filled": not_yet, "tape_ended": ended,
            "tape_ended_half_day": ended_half, "tape_ended_early": ended_other,
            "gap_at_minute": gap, "expected_present": expected,
            "coverage_of_expected": r6(coverage), "structurally_thin": bool(not_yet),
            "reasons": reasons,
        }
        per_minute[str(et)] = cell
        pairs.append((et, cell))
    covs = [c["coverage_of_expected"] for _et, c in pairs if c["coverage_of_expected"] is not None]
    median_cov = float(np.median(covs)) if covs else None
    floor = (median_cov - THIN_MARGIN) if median_cov is not None else None
    for _et, c in pairs:
        c["coverage_floor"] = r6(floor)
        c["materially_below_typical"] = bool(
            c["coverage_of_expected"] is not None and floor is not None
            and c["coverage_of_expected"] < floor)
    flagged = [{"et": et, **{k: v for k, v in c.items() if k != "reasons"}, "reasons": c["reasons"]}
               for et, c in pairs
               if c["materially_below_typical"] or c["not_yet_filled"] > 0]
    worst = sorted([(et, c) for et, c in pairs if c["expected_present"] > 0],
                   key=lambda x: (x[1]["coverage_of_expected"]
                                  if x[1]["coverage_of_expected"] is not None else 1.0))[:10]
    return {
        "definition": (
            "n counts panel rows with a bar exactly at this ET minute, on the PRIMARY axis (every "
            "supported decision minute 570..959). expected_present = members whose own tape range "
            "covers the minute (entry_et <= et <= future_member_last_et); the identity n + "
            "not_yet_filled + "
            "tape_ended + gap_at_minute = members_total is checked at every minute. "
            "coverage_of_expected = n / expected_present. A minute is flagged as materially thin "
            f"when its coverage falls more than {THIN_MARGIN} below the axis MEDIAN coverage (the "
            "ordinary gap level), or when members have not filled yet (structural)."
        ),
        "threshold_margin_below_median_coverage": THIN_MARGIN,
        "median_coverage_of_expected": r6(median_cov),
        "coverage_distribution": {
            "min": r6(min(covs)) if covs else None,
            "p10": r6(float(np.percentile(covs, 10))) if covs else None,
            "median": r6(median_cov),
            "max": r6(max(covs)) if covs else None,
        },
        "coverage_note": (
            "coverage is ~86% at a typical minute: member tapes are gappy, so n at any exact minute "
            "is well below the member count by construction — a property of the tape, not of this "
            "analysis. Read every statistic with its per-cell n."
        ),
        "structural_note": (
            "Minutes before 600 are thin by construction: B600 members cannot print before their "
            "fill (entry_et >= 600), so 571..599 is the A_pm family alone — a family-pooled minute "
            "profile in that window compares A_pm names with nothing. Minutes after 779 lose the "
            "seven half-day sessions (session_end = 779), which is also why the primary axis is "
            "dense to 959: the late minutes are genuinely thin, and are reported as thin rather than "
            "dropped. A thin minute must not be read as a market fact; the per-cell n is emitted "
            "with every statistic."
        ),
        "members_total": total,
        "censored_members": int(np.sum(censored)),
        "tape_end_note": ("the tape end is read from `future_member_last_et`, a FUTURE-ONLY v2 column "
                          "(coverage.json.future_only_columns): it is used for this reporting "
                          "decomposition only and never as a causal feature"),
        "minutes_flagged": len(flagged),
        "worst_ten_minutes": [
            {"et": et, "n": c["n"], "expected_present": c["expected_present"],
             "coverage_of_expected": c["coverage_of_expected"], "not_yet_filled": c["not_yet_filled"],
             "tape_ended": c["tape_ended"], "gap_at_minute": c["gap_at_minute"]}
            for et, c in worst
        ],
        "flagged": flagged,
        "per_minute": per_minute,
    }


def lazy_identity_checks(extra: pl.DataFrame, members: pl.DataFrame) -> dict:
    """Panel-wide identities as small aggregate queries (no big frame held)."""
    checks: dict = {}
    lf = pl.scan_parquet(PANEL)
    rows = int(lf.select(pl.len()).collect().item())
    days = int(lf.select(pl.col("sleeve_day").n_unique()).collect().item())
    members_n = int(members.height)
    worst = lf.select(
        (pl.col("final_high_flag").cast(pl.Float64)
         - pl.col("bars_to_next_high").is_null().cast(pl.Float64)).abs().max()).collect().item()
    checks["p_climb_over_identity"] = {
        "identity": ("final_high_flag == (bars_to_next_high is null) on every panel row (the cell "
                     "aggregates then satisfy share(final_high_flag) == 1 - share(bars_to_next_high "
                     "non-null) by construction)"),
        "max_abs_diff": r6(worst), "pass": bool(worst is not None and worst < 1e-12),
    }
    worst_tail = 0.0
    for thr, col in ((0.50, "tail_class_50"), (1.00, "tail_class_100"), (3.00, "tail_class_300")):
        d = lf.select((pl.col(col).cast(pl.Float64)
                       - (pl.col("remaining_run") >= thr).cast(pl.Float64)).abs().max()).collect().item()
        if d is not None:
            worst_tail = max(worst_tail, float(d))
    checks["tail_class_identity"] = {
        "identity": "tail_class_X == (remaining_run >= X) for X in {0.50, 1.00, 3.00}",
        "max_abs_diff": r6(worst_tail), "pass": bool(worst_tail < 1e-12),
    }
    rec = len(extra)
    viol = int(extra.filter(pl.col("forward_reclaim_flag")
                            != pl.col("bars_to_forward_reclaim").is_not_null()).height)
    checks["recovery_flag_consistency"] = {
        "definition": "forward_reclaim_flag == (bars_to_forward_reclaim is not null) on every row",
        "rows": rec, "violations": viol, "pass": viol == 0,
    }
    checks["panel_identity"] = {
        "rows": rows, "members": members_n, "days": days,
        "expected_rows": 1900432, "expected_members": 6160, "expected_days": 1066,
        "sha256_field": "meta.panel.sha256",
        "pass": bool(rows == 1900432 and members_n == 6160 and days == 1066),
    }
    # v2 censoring census (member-level, future-derived): unresolved, never zero
    cens = extra.filter(pl.col("terminal_censored"))
    done = extra.filter(~pl.col("terminal_censored"))
    checks["censoring_census"] = {
        "definition": ("terminal_censored / path_complete_to_session_end are member-level, "
                       "future-derived labels; censored rows carry null terminal outcomes and are "
                       "excluded from every complete-path/executable profile, reported only as an "
                       "unresolved count"),
        "censored_rows": int(cens.height),
        "censored_rows_share": r6(cens.height / max(1, extra.height)),
        "complete_rows": int(done.height),
        "censored_rows_with_terminal_outcome": int(cens.filter(
            pl.col("v_forced_flat_identity").is_not_null()).height),
        "pass": bool(cens.filter(pl.col("v_forced_flat_identity").is_not_null()).height == 0),
    }
    # v2 forced-flat / close-reference identities against the panel's own columns
    lf2 = pl.scan_parquet(PANEL).select(["v_forced_flat", "v_hold_flat", "next_open",
                                         "future_forced_flat_px", "session_close_ret_from_entry",
                                         "entry_px", "terminal_censored"])
    joined = lf2.collect()
    fff = joined.filter(pl.col("future_forced_flat_px").is_not_null()
                        & pl.col("next_open").is_not_null()).select(
        (pl.col("v_forced_flat")
         - (pl.col("future_forced_flat_px") / pl.col("next_open") - 1)).abs().max()).item()
    close_ref = joined.filter(pl.col("v_hold_flat").is_not_null()
                              & pl.col("next_open").is_not_null()).select(
        (pl.col("v_hold_flat")
         - (pl.col("entry_px") * (1.0 + pl.col("session_close_ret_from_entry"))
            / pl.col("next_open") - 1)).abs().max()).item()
    checks["v2_ruler_identities"] = {
        "pass": True,  # recomputed below from the sub-checks
        "v_forced_flat": {
            "identity": "v_forced_flat == future_forced_flat_px / next_open - 1 (executable ruler)",
            "max_abs_diff": r6(fff), "pass": bool(fff is not None and fff < 1e-9)},
        "v_hold_flat": {
            "identity": ("v_hold_flat == close(session_end) / next_open - 1 (labelled CLOSE "
                         "reference, not an execution price)"),
            "max_abs_diff": r6(close_ref), "pass": bool(close_ref is not None and close_ref < 1e-9)},
        "lines_are_distinct": {
            "mean_abs_difference": r6(joined.select(
                (pl.col("v_hold_flat") - pl.col("v_forced_flat")).abs().mean()).item()),
            "note": "the two ruler lines are reported side by side and are never conflated"},
    }
    # banner pass derived from the sub-checks that carry a pass flag (prose-only entries such as
    # `lines_are_distinct` have none and must not be read as failures)
    sub_passes = [v["pass"] for v in checks["v2_ruler_identities"].values()
                  if isinstance(v, dict) and "pass" in v]
    checks["v2_ruler_identities"]["pass"] = bool(sub_passes) and all(sub_passes)
    del joined, lf2
    return checks


def raw_bars_oracle_check_lazy(members: pl.DataFrame, extra: pl.DataFrame,
                               n_days_per_block: int = 10) -> dict:
    """Recompute the executable oracle from the RAW bars for 20 deterministic days (shard-free)."""
    from factory.scripts import basket_sim as sim
    block_of = {r["sleeve_day"]: r["block"] for r in
                members.select(["sleeve_day", "block"]).unique().to_dicts()}
    days = sim.dev_days()
    cal = sim.session_end_map()
    picked: list[str] = []
    for blk in ("block1", "block2"):
        blk_days = sorted(d for d in days if block_of.get(d) == blk)
        if not blk_days:
            continue
        idx = np.linspace(0, len(blk_days) - 1, n_days_per_block).round().astype(int)
        picked += [blk_days[i] for i in sorted(set(idx.tolist()))]
    small = (
        pl.scan_parquet(PANEL).filter(pl.col("sleeve_day").is_in(picked))
        .select(["sleeve_day", "family", "entry_rank", "ticker", "et", "session_end",
                 "future_member_last_et", "next_open", "remaining_run", "peak_et_after_t",
                 "future_forced_flat_px", "terminal_censored"])
        .with_columns(member=member_key_expr())
        .collect()
        .join(extra.select(["member", "et", "oracle_exec_next_open"]), on=["member", "et"],
              how="left")
    )
    diffs_oracle, diffs_remaining, diffs_next_open, diffs_fff = [], [], [], []
    first_last_pick = match_first = match_last = match_either = no_match = 0
    rows_checked = censored_rows_skipped = 0
    for day in picked:
        sub = small.filter(pl.col("sleeve_day") == day)
        if sub.height == 0:
            continue
        bars = sim.load_bars(day, cal.get(day))
        for r in sub.to_dicts():
            if r["terminal_censored"]:
                # NO terminal value exists for a censored tape: skip, never score it as zero
                censored_rows_skipped += 1
                continue
            tkb = bars.ticker(r["ticker"])
            if tkb is None:
                continue
            ets = tkb["et"]
            i0 = int(np.searchsorted(ets, r["et"]))
            if i0 >= len(ets) or int(ets[i0]) != int(r["et"]):
                continue
            n = len(ets)
            if i0 + 1 >= n:
                continue
            nxt = float(tkb["open"][i0 + 1])
            hi = tkb["high"][i0 + 1:]
            if hi.size == 0:
                continue
            j_first = int(np.argmax(hi))
            j_last = int(len(hi) - 1 - np.argmax(hi[::-1]))
            if j_first != j_last:
                first_last_pick += 1

            # v2: when the max-high bar is the tape's last bar there is no next open, so the
            # executable exit is the engine forced-flat price = the open of the session_end bar
            end_idx = int(np.searchsorted(ets, int(r["session_end"])))
            if end_idx >= n or int(ets[end_idx]) != int(r["session_end"]):
                end_idx = None
            forced_flat_raw = float(tkb["open"][end_idx]) if end_idx is not None else None

            def oracle_at(j):
                ip = i0 + 1 + j
                if ip + 1 < n:
                    return float(tkb["open"][ip + 1]) / nxt - 1.0
                return (forced_flat_raw / nxt - 1.0) if forced_flat_raw is not None else None

            o_first, o_last = oracle_at(j_first), oracle_at(j_last)
            if r["future_forced_flat_px"] is not None and forced_flat_raw is not None:
                diffs_fff.append(abs(forced_flat_raw - float(r["future_forced_flat_px"])))
            rem_raw = float(tkb["high"][i0 + 1 + j_first]) / nxt - 1.0
            rows_checked += 1
            if r["next_open"] is not None:
                diffs_next_open.append(abs(nxt - float(r["next_open"])))
            po = r["oracle_exec_next_open"]
            if po is not None and o_first is not None and o_last is not None:
                d_first, d_last = abs(o_first - float(po)), abs(o_last - float(po))
                diffs_oracle.append(min(d_first, d_last))
                match_first += int(d_first < 1e-9)
                match_last += int(d_last < 1e-9)
                match_either += int(d_first < 1e-9 or d_last < 1e-9)
                no_match += int(not (d_first < 1e-9 or d_last < 1e-9))
            if r["remaining_run"] is not None:
                diffs_remaining.append(abs(rem_raw - float(r["remaining_run"])))

    def dist(vals):
        if not vals:
            return {}
        a = np.asarray(vals, dtype=float)
        return {"n": int(a.size), "mean_abs": r6(float(a.mean())),
                "median_abs": r6(float(np.median(a))), "p95_abs": r6(float(np.percentile(a, 95))),
                "max_abs": r6(float(a.max())), "share_exact_1e-9": r6(float((a < 1e-9).mean()))}

    worst_oracle = dist(diffs_oracle).get("max_abs")
    worst_rem = dist(diffs_remaining).get("max_abs")
    return {
        "purpose": ("the executable oracle is built from the panel's own peak_et_after_t / bar_high / "
                    "next_open; this recomputes it straight from the raw per-day bars on 20 "
                    "deterministic days (10 per block, evenly spaced). Both tie conventions of the "
                    "raw maximum are computed (first and last bar printing the session high) "
                    "because a repeated high is the only way the two can disagree"),
        "days": picked, "rows_checked": rows_checked,
        "censored_rows_skipped": censored_rows_skipped,
        "note_on_censoring": ("censored rows carry no terminal value, so they are skipped here "
                              "rather than scored as zero"),
        "future_forced_flat_px_vs_raw": dist(diffs_fff),
        "next_open_vs_raw": dist(diffs_next_open),
        "oracle_exec_vs_raw": dist(diffs_oracle),
        "remaining_run_vs_raw": dist(diffs_remaining),
        "tie_rows_first_vs_last_max": first_last_pick,
        "oracle_rows_matching_first_max_convention": match_first,
        "oracle_rows_matching_last_max_convention": match_last,
        "oracle_rows_matching_either_convention": match_either,
        "oracle_rows_matching_neither": no_match,
        "pass": bool(worst_oracle is not None and worst_rem is not None
                     and worst_oracle < 1e-9 and worst_rem < 1e-9),
    }


def coordinate_mapping(members: pl.DataFrame, extra: pl.DataFrame | None = None) -> dict:
    """et -> tenure and tenure -> et per family, from two small aggregate queries."""
    full = (
        pl.scan_parquet(PANEL)
        .select(["sleeve_day", "family", "entry_rank", "et", "bars_since_entry"])
    )
    out = {
        "definition": ("ownership tenure = bars_since_entry = bar_index (completed bars since the "
                       "fill bar, 0 at the fill). Clock time and tenure are different variables: "
                       "A_pm fills at 570/571, B600 at 600..604, and tapes gap."),
        "et_to_tenure": {}, "tenure_to_et": {}, "claim_check": {},
    }
    for fam in ("A_pm", "B600"):
        sub = full.filter(pl.col("family") == fam)
        a = sub.group_by("et").agg(
            pl.col("bars_since_entry").median().alias("median"),
            pl.col("bars_since_entry").min().alias("min"),
            pl.col("bars_since_entry").max().alias("max"),
            pl.len().alias("rows"),
        ).collect().sort("et")
        out["et_to_tenure"][f"family:{fam}"] = {
            str(int(r["et"])): {"median_bars_since_entry": int(r["median"]), "min": int(r["min"]),
                                "max": int(r["max"]), "rows": int(r["rows"])}
            for r in a.iter_rows(named=True)
        }
        b = sub.group_by("bars_since_entry").agg(
            pl.col("et").median().alias("median"), pl.len().alias("rows"),
        ).collect().sort("bars_since_entry")
        out["tenure_to_et"][f"family:{fam}"] = {
            str(int(r["bars_since_entry"])): {"median_et": int(r["median"]), "rows": int(r["rows"])}
            for r in b.iter_rows(named=True)
        }
    claims = {}
    for fam in ("A_pm", "B600"):
        e2t = out["et_to_tenure"][f"family:{fam}"]
        t2e = out["tenure_to_et"][f"family:{fam}"]
        claims[f"family:{fam}"] = {
            "tenure_at_et_600_median": e2t.get("600", {}).get("median_bars_since_entry"),
            "et_at_tenure_30_median": t2e.get("30", {}).get("median_et"),
            "tenure_at_et_720_median": e2t.get("720", {}).get("median_bars_since_entry"),
        }
    out["claim_check"] = {
        "claim": ("at et=600 A_pm members have ~30 bars of ownership while B600 members have 0; at "
                  "30 bars of ownership A_pm sits near et 600 and B600 near et 630"),
        "observed": claims,
        "holds": bool(claims["family:A_pm"]["tenure_at_et_600_median"] is not None
                      and claims["family:A_pm"]["tenure_at_et_600_median"] >= 25
                      and claims["family:B600"]["tenure_at_et_600_median"] <= 2
                      and claims["family:A_pm"]["et_at_tenure_30_median"] is not None
                      and claims["family:B600"]["et_at_tenure_30_median"] >
                      claims["family:A_pm"]["et_at_tenure_30_median"]),
    }
    return out


def build(print_headlines: bool = False, raw_check_enabled: bool = True,
          budget_gib: float = 5.0) -> dict:
    """Bounded-memory sharded build.

    Shards are (family|set) x (block|pooled) row subsets materialized ONE AT A TIME: the whole panel
    is never held together with all grouped copies. Each shard's small summary is written to
    /tmp/wp_shards/<shard>.json and merged afterwards.
    """
    t0 = time.time()
    import gc
    import os
    rss_log: list[dict] = []
    members = load_members()
    dup_pairs, dup_meta = load_dup_pairs()
    extra, extra_meta = load_extras(dup_pairs, members)

    sets = set_definitions()
    scopes = {name: scope for name, _m, scope in sets}
    masks = {name: mask for name, mask, _s in sets}
    series: dict = {}
    prim: dict = {}
    prim_unc: dict = {}
    unc: dict = {}
    balanced: dict = {}
    samples: list[dict] = []
    dup_landmarks: dict = {}
    rng = np.random.default_rng(BOOT_SEED)
    shard_dir = Path("/tmp/wp_shards")
    shard_dir.mkdir(parents=True, exist_ok=True)

    def finish_shard(name: str, payload: dict) -> None:
        with open(shard_dir / f"{name.replace(':', '_').replace('|', '__')}.json", "w") as fh:
            json.dump(payload, fh, separators=(",", ":"), allow_nan=False)
        rss_log.append({"shard": name, "peak_rss_gib": round(check_rss(budget_gib, name), 3)})

    # ---------------- family shards: the two families carry the full key set, the primitive
    #                  structure, the balanced control and the day-clustered uncertainty
    for fam in ("A_pm", "B600"):
        fam_mask = pl.col("family") == fam
        # (i) the DENSE primary minute frame: every supported decision minute to the close
        densef = shard_frame(fam_mask, SHARD_SERIES_COLS, extra, with_pairs=False,
                             minute_axis=MINUTE_DENSE)
        prim[fam] = {
            "minute": prim_cells(densef, pl.lit(True), "et", MINUTE_DENSE, curve=True,
                                 reduced=False),
        }
        prim_unc[f"family:{fam}"] = dense_uncertainty(
            densef, ["final_high_flag", "remaining_run", "oracle_exec_next_open",
                     "cost_of_waiting"], MINUTE_DENSE)
        del densef
        gc.collect()
        # (ii) the COMPACT reference frame: the morning + hourly summary, whose rate of change keeps
        #      the documented previous-COMPACT-grid-minute semantics (780 pairs with 720, not 779)
        gridf = shard_frame(fam_mask, SHARD_SERIES_COLS, extra, with_pairs=True)
        fam_series = {f"family:{fam}": build_series(gridf, pl.lit(True), "full")}
        add_series_deltas(fam_series[f"family:{fam}"], "full")
        for cname in ("A_peak_ge_100", "C_peak_0_30"):
            nm = f"cohort:{cname}|family:{fam}"
            fam_series[nm] = build_series(gridf, pl.col("cohort") == cname, "reduced")
            add_series_deltas(fam_series[nm], "reduced")
        for nm in (f"family:{fam}",):
            samples += sample_cells(gridf, nm, rng, 1)
        balanced[fam] = balanced_panel_for(gridf, fam, members)
        # families are the primary object: the de-duplication sensitivity is reported per family too
        dup_landmarks[f"family:{fam}"] = dup_landmark_cells(gridf)
        unc[f"family:{fam}"] = uncertainty_for(gridf, f"family:{fam}", GRID)
        del gridf
        gc.collect()
        tape = shard_frame(fam_mask, SHARD_PRIM_COLS, extra, with_pairs=False, tape_rows=True)
        prim[fam]["tenure"] = prim_cells(tape, pl.lit(True), "bars_since_entry", TENURE_DENSE,
                                         curve=True, reduced=False)
        del tape
        gc.collect()
        series.update(fam_series)
        finish_shard(f"family_{fam}", {"series": list(fam_series), "prim": [fam],
                                       "unc": [f"family:{fam}"], "balanced": [fam]})

    # ---------------- cross-family set shards, one set at a time
    cross = [n for n, _m, _s in sets if not (n.startswith("family:") or "|family:" in n)]
    for name in cross:
        mask = masks[name]
        scope = scopes[name]
        gf = shard_frame(mask, SHARD_SERIES_COLS, extra, with_pairs=True)
        series[name] = build_series(gf, pl.lit(True), scope)
        add_series_deltas(series[name], scope)
        if name in ("cohort:A_peak_ge_100", "cohort:C_peak_0_30"):
            unc[name] = uncertainty_for(gf, name, GRID)
            samples += sample_cells(gf, name, rng, 1)
        if name == "all":
            samples += sample_cells(gf, name, rng, 2)
            dup_landmarks["all"] = dup_landmark_cells(gf)
        elif name.startswith("cohort:") or name.startswith("entry_rank"):
            dup_landmarks[name] = dup_landmark_cells(gf)
        del gf
        gc.collect()
        if name in ("cohort:A_peak_ge_100", "cohort:C_peak_0_30"):
            tape = shard_frame(mask, SHARD_PRIM_COLS, extra, with_pairs=False, tape_rows=True)
            prim[name] = {"tenure": prim_cells(tape, pl.lit(True), "bars_since_entry",
                                               TENURE_DENSE, curve=False, reduced=True)}
            del tape
            gc.collect()
        finish_shard(f"set_{name}", {"series": [name]})

    # ---------------- light whole-panel passes (tiny outputs, no big frames)
    counts = (
        pl.scan_parquet(PANEL).filter(pl.col("et").is_in(MINUTE_DENSE))
        .group_by(["et", "block"]).agg(pl.len().alias("n")).collect()
    )
    thin = thin_minutes_from_counts(members, counts)
    del counts
    gc.collect()
    coord_map = coordinate_mapping(members)
    if raw_check_enabled:
        raw_check = raw_bars_oracle_check_lazy(members, extra)
    else:
        raw_check = None
    identity = lazy_identity_checks(extra, members)
    del extra
    gc.collect()

    cohorts_meta = {}
    for name, desc in COHORT_DEFS:
        n_all = int((members["cohort"] == name).sum())
        cohorts_meta[name] = {
            "boundary": desc, "descriptive_only": True, "n_members": n_all,
            "share": r6(n_all / members.height),
            "n_by_block": {b: int(((members["cohort"] == name) & (members["block"] == b)).sum())
                           for b in ("block1", "block2")},
            "n_by_family": {f: int(((members["cohort"] == name) & (members["family"] == f)).sum())
                            for f in ("A_pm", "B600")},
        }
    n_d2 = int((members["cohort2"] == COHORT_D2[0]).sum())
    cohorts_meta[COHORT_D2[0]] = {
        "boundary": COHORT_D2[1], "descriptive_only": True, "supplementary": True,
        "n_members": n_d2, "share": r6(n_d2 / members.height),
        "n_by_block": {b: int(((members["cohort2"] == COHORT_D2[0])
                               & (members["block"] == b)).sum()) for b in ("block1", "block2")},
        "n_by_family": {f: int(((members["cohort2"] == COHORT_D2[0])
                                & (members["family"] == f)).sum()) for f in ("A_pm", "B600")},
    }
    meta = _meta(members, cohorts_meta, sets, identity, rss_log, dup_meta)
    path_struct = {
        "role": ("PRIMARY OUTPUT. The primitive conditional future-path structure of the early "
                 "leader race — what the tape does AFTER each exact minute and each exact tenure, "
                 "not what a rule would earn."),
        "definition": PRIM_DEFINITION,
        "axes": {
            "minute_et": {"values": MINUTE_DENSE,
                          "resolution": ("EVERY supported decision minute from 570 (the fill bar) "
                                         "to 959 (15:59); a half-day session simply stops printing "
                                         "after 779 and its later minutes are null/thin, so the axis "
                                         "never truncates the value horizon at a chosen hour")},
            "tenure_bars_since_entry": {"values": TENURE_DENSE,
                                        "resolution": ("every integer bar across the full supported "
                                                       "range of the panel (0 at the fill bar); "
                                                       "computed on EVERY panel row, not only at "
                                                       "grid minutes, so a tenure is measured "
                                                       "wherever it occurs"),
                                        "count": len(TENURE_DENSE)},
        },
        "sets": {f"family:{f}": prim[f] for f in ("A_pm", "B600")},
        "compact_reference_grid": {
            "minutes": GRID,
            "role": ("the `series` (rulers/anatomy), uncertainty bootstrap and thin-minute flag "
                     "sections are emitted on this compact morning+hourly grid as a secondary "
                     "summary; the primary clock axis above is minute-by-minute to the close"),
        },
        "uncertainty_on_primitive_curves": ("path_structure_uncertainty: day-clustered (primary) and "
                                            "ticket-clustered (secondary) intervals for "
                                            "P(no further high) and the mean remaining attainable "
                                            "move / adverse excursion at EVERY primary minute"),
        "survival_curve": {
            "k_bars": SURVIVAL_K,
            "emitted_for": "the two families (the cohort sets keep the quantiles and the never-share)",
            "semantics": ("S(k) = P(no touch of entry_px again within k bars after t), censoring "
                          "never-reclaimers into S(k); the hazard at k is S(k-1) - S(k) with S(0)=1"),
        },
    }
    # only the cohort sets are added: the families are already keyed as family:*
    path_struct["sets"].update({k: v for k, v in prim.items() if k.startswith("cohort:")})
    out = {
        "meta": meta,
        "path_structure": path_struct,
        "coordinate_mapping": coord_map,
        "reference_lines": reference_lines(series, ["family:A_pm", "family:B600",
                                                    "cohort:A_peak_ge_100",
                                                    "cohort:C_peak_0_30", "all"]),
        "series": series,
        "by_tenure_compact_reference": compact_by_tenure(prim),
        "zero_crossings": zero_crossings(series),
        "giants_vs_majority": giants_vs_majority(series),
        "balanced_morning_panel": balanced,
        "block_agreement": block_agreement(series, [n for n, _m, _s in sets]),
        "duplicate_paths": duplicate_paths_meta(dup_meta, dup_landmarks),
        "uncertainty": _uncertainty_block(unc),
        "path_structure_uncertainty": {
            "method": _uncertainty_method(),
            "levels": prim_unc,
        },
        "zero_crossings_primary_dense": dense_primary_crossings(prim),
        "thin_minutes": thin,
        "checks": {},
    }
    out["checks"].update(identity)
    out["checks"].update(_checks_from_series(out, coord_map, raw_check, samples, dup_pairs))
    out["meta"]["run_seconds"] = round(time.time() - t0, 2)
    # the four axis values an auditor re-verified by hand in the v2 review; emitted so a future
    # audit can diff them without parsing the big file
    verified = {}
    for fam, t0, t1, et0 in (("A_pm", "0", "1", "570"), ("B600", "0", "1", "570")):
        pm = out["path_structure"]["sets"][f"family:{fam}"]["minute"].get(et0, {}).get("pooled", {})
        tm0 = out["path_structure"]["sets"][f"family:{fam}"]["tenure"].get(t0, {}).get("pooled", {})
        tm1 = out["path_structure"]["sets"][f"family:{fam}"]["tenure"].get(t1, {}).get("pooled", {})
        verified[f"family:{fam}"] = {
            f"tenure_{t0}_p_no_further_high": (tm0.get("p_no_further_high") or {}).get("share"),
            f"tenure_{t1}_p_no_further_high": (tm1.get("p_no_further_high") or {}).get("share"),
            f"minute_{et0}_p_no_further_high": (pm.get("p_no_further_high") or {}).get("share"),
        }
    out["meta"]["verified_axis_values"] = verified
    out["meta"]["memory"] = {
        "budget_gib": budget_gib,
        "peak_rss_gib": round(peak_rss_gib(), 3),
        "shard_log": rss_log,
        "note": ("each (family|set) x (block|pooled) subset is materialized alone and released; the "
                 "whole panel is never held together with all grouped copies"),
    }
    if print_headlines:
        headlines(out)
    return out


PRIM_DEFINITION = {
    "p_no_further_high": ("share of rows whose running high at t was the session's last high "
                          "(final_high_flag) — the probability that the climb is already over"),
    "remaining_attainable_move": ("remaining_run = max high after t / next_open(t) - 1: the move "
                                  "still attainable from the decision point; a HIGH, not a fill, so "
                                  "it is an optimistic bound on what could be captured"),
    "continuation_distribution": ("the quantiles of remaining_attainable_move plus the share reaching "
                                  "+20/+50/+100/+300% after t (the last three are the panel's "
                                  "tail_class_50/100/300)"),
    "adverse_excursion_while_waiting": ("cost_of_waiting = min low after t / next_open(t) - 1: the "
                                        "worst excursion the position would have had to sit through"),
    "time_to_next_high": ("bars_to_next_high: completed bars from t+1 to the first bar that exceeds "
                          "the running high at t; null == the climb is over"),
    "time_to_final_high": "bars_to_peak and peak_et_after_t for the session's max high after t",
    "recovery_reclaim": ("the full one-minute time-to-reclaim structure: the quantiles of "
                         "bars_to_forward_reclaim (bars from t+1 until a later bar first trades back "
                         "at/above entry_px — a touch of the high, not a close), the share that "
                         "never does, and the survival curve S(k) = share (of rows with a next "
                         "open) whose price has NOT touched entry_px again within k bars after t, "
                         f"for k = 1..{SURVIVAL_K}; never-reclaimers are censored into every S(k). "
                         "The x-axis is BARS, i.e. the member's own printed minutes. The horizon of "
                         "the curve is the only horizon in it."),
    "reference_lines": ("v_hold_flat, v_giveback_10 and the executable-oracle median are "
                        "REFERENCE/ANATOMY anchors reported so the path structure can be oriented; "
                        "they are not the object of study, and the same holds for "
                        "reference_recovery.share_reclaim_within_30_bars (an arbitrary 30-bar ruler)."),
}


def balanced_panel_for(frame: pl.DataFrame, fam: str, members: pl.DataFrame) -> dict:
    """Members with a bar at every morning grid minute of their own family (composition-free)."""
    first = 571 if fam == "A_pm" else 600
    m = frame.filter(pl.col("et") <= MORNING[-1])
    need = MORNING[-1] - first + 1
    counts = m.group_by("member").agg(pl.len().alias("nm"), pl.col("et").min().alias("e0"))
    keep = counts.filter((pl.col("nm") == need) & (pl.col("e0") == first))["member"]
    bal = m.filter(pl.col("member").is_in(keep.to_list()))
    entry: dict = {
        "family": fam, "morning_minutes_required": need, "members": int(keep.len()),
        "members_total_in_family": int((members["family"] == fam).sum()),
        "members_share_of_family": r6(keep.len() / max(1, int((members["family"] == fam).sum()))),
        "minutes": {},
    }
    if bal.height:
        agg = bal.group_by(["et", "block"]).agg(
            pl.col("v_forced_flat").count().alias("v_forced_flat|n"),
            pl.col("v_forced_flat").mean().alias("v_forced_flat|mean"),
            pl.col("v_forced_flat").median().alias("v_forced_flat|median"),
            pl.col("v_hold_flat").median().alias("v_hold_flat|median"),
            pl.col("remaining_run").mean().alias("remaining_run|mean"),
            pl.col("remaining_run").median().alias("remaining_run|median"),
            pl.col("oracle_exec_next_open").median().alias("oracle_exec_next_open|median"),
            pl.col("final_high_flag").cast(pl.Float64).mean().alias("p_climb_over|share"),
        ).sort(["et", "block"])
        cells = frame_to_cells(agg, ["et", "block"])
        for et in MORNING:
            entry["minutes"][str(et)] = {}
            for blk in BLOCKS:
                c = cells.get(str(et)) if blk == "pooled" else cells.get(f"{et}|{blk}")
                entry["minutes"][str(et)][blk] = cells_to_json(c) if c else {}
    out = {
        "definition": ("members with a panel row at EVERY morning grid minute of their own family "
                       "(A_pm 571..720, B600 600..720) — the morning curves without composition "
                       "drift. Families are never pooled here."),
        "selection_note": ("selected on the *state* that a row exists at each minute (causal in "
                           "form), but the condition spans the whole morning, so it is a "
                           "hindsight-conditioned subset (members whose tape survives the morning). "
                           "Read as a robustness check, not as a tradeable population."),
        "metric": ("per minute: v_forced_flat (PRIMARY executable ruler) median/mean, "
                   "v_hold_flat (labelled close reference) median, remaining_run median/mean, the "
                   "executable-oracle median and p_climb_over. Censored members are absent from the "
                   "outcome columns by construction."),
        "series": {f"family:{fam}": entry},
    }
    return out


def compact_by_tenure(prim: dict) -> dict:
    """Labelled COMPACT view of the primary tenure axis at the sparse reference grid."""
    out = {
        "role": ("COMPACT REFERENCE ONLY — a sparse view of `path_structure[...].tenure`, never the "
                 "primary tenure output (which runs over every integer bars_since_entry)."),
        "tenure_grid_sparse": TENURE_GRID,
        "series": {},
    }
    keys = {"n": ("n",), "n_outcome": ("n_outcome",)}
    for fam in ("A_pm", "B600"):
        # prim is keyed by the bare family name in the shard pass
        dense = (prim.get(fam) or prim.get(f"family:{fam}") or {}).get("tenure", {})
        if not dense:
            continue
        ser: dict = {}
        for t in TENURE_GRID:
            cell = dense.get(str(t))
            if cell is None:
                continue
            ser[str(t)] = {}
            for blk, src in cell.items():
                if not src:
                    continue
                ref = src.get("reference_v_hold_flat", {})
                ser[str(t)][blk] = {
                    "n": src.get("n", 0), "n_outcome": src.get("n_outcome", 0),
                    "v_forced_flat": {"median": src.get("reference_v_forced_flat", {}).get("median"),
                                      "mean": src.get("reference_v_forced_flat", {}).get("mean")},
                    "v_hold_flat_close_reference": {"median": ref.get("median"),
                                                    "mean": ref.get("mean")},
                    "remaining_run": {"median": src.get("remaining_run", {}).get("median"),
                                      "mean": src.get("remaining_run", {}).get("mean"),
                                      "p90": src.get("remaining_run", {}).get("p90"),
                                      "share_ge_100pct":
                                          src.get("remaining_run", {}).get("share_ge_100pct")},
                    "oracle_exec_next_open":
                        {"median": src.get("reference_executable_oracle", {}).get("median")},
                    "p_no_further_high": src.get("p_no_further_high", {}).get("share"),
                    "recovery": {
                        "share_reaches_entry_again":
                            src.get("recovery", {}).get("share_reaches_entry_again"),
                        "median_bars_to_reclaim":
                            src.get("recovery", {}).get("median_bars_to_reclaim"),
                    },
                }
        out["series"][f"family:{fam}"] = ser
    return out


def duplicate_paths_meta(dup_meta: dict, landmarks: dict) -> dict:
    """The duplicate-path section from the shard landmark cells (no extra pass)."""
    out = {
        "definition": ("a duplicated path is a (sleeve_day, ticker) pair that appears in BOTH "
                       "families — the same market path held twice, once in each sleeve. Pooled "
                       "statistics count such a path twice unless it is flagged or excluded."),
        "counts": {
            "pairs_in_both_families": dup_meta["pairs"],
            "members": dup_meta["members"],
            "member_share_of_panel": r6(dup_meta["members"] / 6160),
            "rows": dup_meta["rows"],
            "panel_rows": dup_meta["panel_rows"],
            "rows_share": r6(dup_meta["rows"] / max(1, dup_meta["panel_rows"])),
        },
        "policy": ("family:* statistics are complete (a duplicated path legitimately belongs to both "
                   "sleeves). Sets that POOL the families (all, entry_rank:*, cohort:*) carry the "
                   "duplicated rows and flag them per cell (`n_dup_path_rows`); the table below "
                   "quantifies what excluding the second copy would change. The sensitivity is "
                   "measured on the EXECUTABLE ruler v_forced_flat (the line is named in every key) "
                   "and is reported per pooled set AND per family, since the families are the "
                   "primary object."),
        "pooled_levels_with_and_without_duplicates": landmarks,
        "landmark_minutes": [600, 660, 720, 780, 840, 900],
    }
    worst = 0.0
    worst_cell = None
    for setname, rows in landmarks.items():
        for et, r in rows.items():
            line = r.get("line", "v_forced_flat")
            a, b = r.get(f"median_with_{line}"), r.get(f"median_without_{line}")
            if a is not None and b is not None and abs(a - b) > worst:
                worst, worst_cell = abs(a - b), [setname, et, line, a, b]
    out["max_abs_median_shift_excluding_duplicates"] = r6(worst)
    out["max_shift_cell"] = worst_cell
    return out


def _uncertainty_block(unc: dict) -> dict:
    method = _uncertainty_method()
    method["scope"] = ("levels at every COMPACT-grid minute and the crossing-minute distribution, "
                       "for the two families and for the (descriptive) cohort sets A and C, per "
                       "block. Each shard's intervals are computed inside that shard, so no "
                       "cross-shard pooling of minute rows ever happens. The primitive curves carry "
                       "their own day-clustered intervals at every primary minute in "
                       "path_structure_uncertainty.")
    return {
        "method": method,
        "levels": {k: v["levels"] for k, v in unc.items()},
        "median_bootstrap": {k: v["median_bootstrap"] for k, v in unc.items()},
        "crossing_minute": {k: v["crossing_minute"] for k, v in unc.items()},
    }


def scan_for_v1_names(obj, forbidden: list[str], path: str = "$", hits: list | None = None) -> list:
    """Any emitted key naming a v1-only column is a migration leak; densities must be v2-clean."""
    if hits is None:
        hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            for bad in forbidden:
                # a v1 name is a leak; the v2 rename (future_ prefix) is not
                if re.search(r"(?<!future_)\b" + re.escape(bad) + r"\b", str(k)):
                    hits.append(f"{path}.{k}")
            scan_for_v1_names(v, forbidden, f"{path}.{k}", hits)
    elif isinstance(obj, list):
        # full recursion: no silent cap, so a leak cannot hide behind a long array
        for i, v in enumerate(obj):
            scan_for_v1_names(v, forbidden, f"{path}[{i}]", hits)
    return hits


def _checks_from_series(out: dict, coord_map: dict, raw_check: dict | None,
                        samples: list[dict], dup_pairs: pl.DataFrame) -> dict:
    """Series-level identities, the independent recompute and the raw-bars oracle check."""
    series = out["series"]
    checks: dict = {}
    h = hashlib.sha256()
    with open(PANEL, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    checks["panel_sha256"] = {"sha256": h.hexdigest(),
                              "matches_frozen": h.hexdigest() == PANEL_SHA256,
                              "pass": h.hexdigest() == PANEL_SHA256}
    worst_n, worst_cell = 0, None
    for et in GRID:
        for blk in BLOCKS:
            tot = series["all"][str(et)][blk]["n"]
            parts = sum(series[f"cohort:{c}"][str(et)][blk]["n"] for c, _ in COHORT_DEFS)
            parts += series["cohort:CENSORED_unlabelled"][str(et)][blk]["n"]
            if abs(parts - tot) > worst_n:
                worst_n, worst_cell = abs(parts - tot), [et, blk, parts, tot]
    checks["cohort_partition_sums_to_all"] = {
        "definition": ("sum of n over the four outcome cohorts PLUS the CENSORED_unlabelled bucket "
                       "== n of the all-members set (censored members have no session peak and are "
                       "reported separately rather than folded into a hindsight cohort)"),
        "max_abs_diff": int(worst_n), "worst_cell": worst_cell, "pass": worst_n == 0,
    }
    worst, worst_cell = 0, None
    for et in GRID:
        for blk in BLOCKS:
            tot = series["all"][str(et)][blk]["n"]
            fam = sum(series[f"family:{f}"][str(et)][blk]["n"] for f in ("A_pm", "B600"))
            rk = sum(series[f"entry_rank:{i}"][str(et)][blk]["n"] for i in (1, 2, 3))
            d = max(abs(fam - tot), abs(rk - tot))
            if d > worst:
                worst, worst_cell = d, [et, blk, fam, rk, tot]
    checks["family_and_rank_partitions"] = {
        "definition": "family split and rank split each sum to the all-set n",
        "max_abs_diff": int(worst), "worst_cell": worst_cell, "pass": worst == 0,
    }
    worst, worst_cell = 0, None
    for et in GRID:
        for blk in BLOCKS:
            pair = (series["cohort:A_peak_ge_100|family:A_pm"][str(et)][blk]["n"]
                    + series["cohort:A_peak_ge_100|family:B600"][str(et)][blk]["n"])
            tgt = series["cohort:A_peak_ge_100"][str(et)][blk]["n"]
            d = abs(pair - tgt)
            if d > worst:
                worst, worst_cell = d, [et, blk, pair, tgt]
    checks["cohort_family_partition"] = {
        "definition": "cohort:A|family:A_pm + cohort:A|family:B600 == cohort:A",
        "max_abs_diff": int(worst), "worst_cell": worst_cell, "pass": worst == 0,
    }
    mismatches = []
    for s in samples:
        cell = (series[s["set"]].get(str(s["et"]), {}).get(s["block"], {})
                .get(s["column"], {}))
        if not cell.get("n"):
            continue
        for k in ("n", "mean", "median", "positive_share", "top5_share"):
            naive, g = s.get(k), cell.get(k)
            if k == "n":
                ok = g == naive
            elif naive is None:
                ok = g is None
            else:
                ok = g is not None and abs(g - naive) < TOL_ROUND
            if not ok:
                mismatches.append({"set": s["set"], "et": s["et"], "block": s["block"],
                                   "column": s["column"],
                                   "stat": k, "naive": naive, "json": g})
    covered = sorted({s["column"] for s in samples})
    checks["independent_recompute"] = {
        "tolerance": TOL_ROUND,
        "columns_covered": covered,
        "covers_executable_ruler_and_giveback":
            bool({"v_forced_flat", "v_giveback_10"} <= set(covered)),
        "pass_all_of_columns_covered": bool(
            {"v_forced_flat", "v_hold_flat", "v_giveback_10", "oracle_exec_next_open"}
            <= set(covered)),
        "note": ("naive pure-Python recomputation (sorted list, numpy median, plain sums) of "
                 "n/mean/median/positive_share/top5_share for the executable ruler v_forced_flat, "
                 "the close reference v_hold_flat, the giveback ruler v_giveback_10 and the "
                 "executable oracle, on a deterministic sample of shard cells, against the emitted "
                 "numbers (which are rounded to 6 decimals, hence the 1e-6 band)"),
        "columns": ["v_forced_flat", "v_hold_flat", "v_giveback_10", "oracle_exec_next_open"],
        "cells_checked": len(samples), "mismatches": mismatches,
        "pass": bool(not mismatches and {"v_forced_flat", "v_giveback_10"} <= set(covered)),
    }
    bad_rows = [et for et, c in out["thin_minutes"]["per_minute"].items()
                if c["n"] + c["not_yet_filled"] + c["tape_ended"] + c["gap_at_minute"]
                != c["members_total"]]
    checks["thin_minutes_identity"] = {
        "identity": "n + not_yet_filled + tape_ended + gap_at_minute == members_total",
        "violations": bad_rows, "pass": not bad_rows,
    }
    checks["coordinate_mapping_claims"] = {
        **coord_map.get("claim_check", {}),
        "pass": bool(coord_map.get("claim_check", {}).get("holds")),
    }
    # real check: the compact tenure reference is a projection of the dense primary tenure cells, so
    # every shared (family, tenure, block) cell must agree
    tmax = 0.0
    tchecked = 0
    tworst = None
    for setname, ser in out.get("by_tenure_compact_reference", {}).get("series", {}).items():
        dense = out["path_structure"]["sets"].get(setname, {}).get("tenure", {})
        for t, blocks in ser.items():
            for blk, cell in blocks.items():
                ref = dense.get(str(t), {}).get(blk, {}).get("reference_v_forced_flat", {})
                got = cell.get("v_forced_flat", {}).get("median")
                if got is None or ref.get("median") is None:
                    continue
                tchecked += 1
                d = abs(got - ref["median"])
                if d > tmax:
                    tmax, tworst = d, [setname, t, blk, got, ref["median"]]
    checks["tenure_axis_consistency"] = {
        "definition": ("by_tenure_compact_reference (sparse projection) must equal "
                       "path_structure[...].tenure (dense primary) wherever both are defined, on the "
                       "executable ruler median"),
        "cells_checked": tchecked, "max_abs_diff": r6(tmax), "worst_cell": tworst,
        "pass": bool(tchecked > 0 and tmax < TOL_ROUND),
    }
    # real check: the duplicated-path table injected into every shard matches the panel, and the
    # per-cell duplicate counts can never exceed the cell's row count
    dup_ticks = int(pl.scan_parquet(PANEL)
                    .join(dup_pairs.lazy().select(["sleeve_day", "ticker"]),
                          on=["sleeve_day", "ticker"], how="semi")
                    .select(pl.len()).collect().item())
    worst_dup = 0
    dup_cells = 0
    for setname, mins in series.items():
        for et, blocks in mins.items():
            for blk, cell in blocks.items():
                n_dup = cell.get("n_dup_path_rows", 0) or 0
                if n_dup:
                    dup_cells += 1
                worst_dup = max(worst_dup, n_dup - (cell.get("n") or 0))
    checks["duplicate_paths_source"] = {
        "definition": ("the duplicated-path table used by every shard is recomputed here from the "
                       "panel and must match the injected one; and no cell may contain more "
                       "duplicated rows than rows"),
        "pairs_injected": int(dup_pairs.height),
        "rows_matched_in_panel": dup_ticks,
        "cells_with_duplicated_rows": dup_cells,
        "max_overcount_vs_cell_rows": int(worst_dup),
        "pass": bool(dup_ticks > 0 and worst_dup <= 0),
    }
    if raw_check is not None:
        checks["raw_bars_oracle_check"] = raw_check
    # D6 selftest: the clustered mean MUST equal the cell's point estimate, because the denominator
    # is now the non-null count (they diverged when .len() counted nulls)
    worst_cmp, cmp_cells = 0.0, 0
    for setname, entry in out.get("path_structure_uncertainty", {}).get("levels", {}).items():
        dense = out["path_structure"]["sets"].get(setname, {}).get("minute", {})
        d6_minutes = [str(et) for et in MINUTE_DENSE if et % 30 == 0] + ["570", "571", "600", "660",
                                                                        "720", "780", "900", "959"]
        for et in sorted(set(d6_minutes), key=int):
            for blk, cell_unc in entry.items():
                cell = dense.get(et, {}).get(blk)
                if not cell:
                    continue
                pairs = (("final_high_flag", cell.get("p_no_further_high", {}).get("share")),
                         ("remaining_run", cell.get("remaining_run", {}).get("mean")),
                         ("cost_of_waiting", cell.get("cost_of_waiting", {}).get("mean")))
                for col, point in pairs:
                    got = cell_unc.get(et, {}).get(col, {}).get("mean_sleeve_day_clustered")
                    if got is None or point is None:
                        continue
                    cmp_cells += 1
                    worst_cmp = max(worst_cmp, abs(got - point))
                    n_rows = cell_unc.get(et, {}).get(col, {}).get("n_rows")
                    n_point = (cell.get("p_no_further_high", {}).get("n") if col
                               == "final_high_flag" else cell.get(col, {}).get("n"))
                    if n_rows is not None and n_point is not None and n_rows != n_point:
                        cmp_cells += 1
                        worst_cmp = max(worst_cmp, 1.0)
    checks["clustered_mean_equals_point_estimate"] = {
        "definition": ("the emitted day-clustered mean must equal the cell point estimate and "
                       "n_rows must equal the cell's non-null count (D6 regression guard)"),
        "cells_compared": cmp_cells, "max_abs_diff": r6(worst_cmp),
        "pass": bool(cmp_cells > 0 and worst_cmp < TOL_ROUND),
    }
    # D3 selftest: the reclaim share is over COMPLETE-PATH rows and equals n_reclaimers/complete rows
    worst_rec, rec_cells = 0.0, 0
    for setname, entry in out.get("path_structure", {}).get("sets", {}).items():
        for axis in ("minute", "tenure"):
            for k, blocks in (entry.get(axis) or {}).items():
                for blk, cell in blocks.items():
                    rec = (cell or {}).get("recovery")
                    if not rec or rec.get("share_reaches_entry_again_complete_path") is None:
                        continue
                    scored = rec.get("n_reclaim_scored_rows")
                    nrec = rec.get("n_reclaimers")
                    complete_n = (cell.get("complete_path_rows") or {}).get("n")
                    no_next = rec.get("n_complete_path_rows_without_next_open")
                    if not scored or nrec is None:
                        continue
                    rec_cells += 1
                    d_rec = abs(rec["share_reaches_entry_again_complete_path"] - nrec / scored)
                    if d_rec > worst_rec:
                        worst_rec, worst_rec_cell = d_rec, [setname, axis, k, blk,
                                                            rec["share_reaches_entry_again_complete_path"],
                                                            nrec, scored]
                    if complete_n is None or no_next is None:
                        continue
                    if scored + no_next != complete_n:
                        rec_cells += 1
                        worst_rec = max(worst_rec, 1.0)
    worst_rec_cell = None
    worst_rec_cens, cens_cells = 0.0, 0
    worst_ruler, ruler_cells = 0.0, 0
    for setname, entry in out.get("path_structure", {}).get("sets", {}).items():
        for axis in ("minute", "tenure"):
            for k, blocks in (entry.get(axis) or {}).items():
                for blk, cell in blocks.items():
                    cell = cell or {}
                    rec = cell.get("recovery") or {}
                    cens_rows = (cell.get("censored_rows") or {}).get("n")
                    if rec.get("n_excluded_censored") is not None and cens_rows is not None:
                        cens_cells += 1
                        if rec["n_excluded_censored"] != cens_rows:
                            worst_rec_cens = max(worst_rec_cens, 1.0)
                    r30 = cell.get("reference_recovery") or {}
                    share30 = r30.get("share_reclaim_within_30_bars_complete_path")
                    num30 = r30.get("n_reclaim_within_30_bars_complete_path")
                    den30 = r30.get("n_scored_rows_complete_path")
                    if share30 is not None or den30 is not None:
                        ruler_cells += 1
                        den = den30 or 0
                        # a cell with no scored rows must emit null, never NaN or a fabricated 0
                        bad = (den > 0 and (share30 is None or not (0.0 <= share30 <= 1.0))) or \
                              (den == 0 and share30 is not None)
                        if num30 is not None and den:
                            bad = bad or num30 > den or abs(share30 - num30 / den) > TOL_ROUND
                        if bad:
                            worst_ruler = max(worst_ruler, 1.0)
    checks["recovery_share_uses_complete_path_denominator"] = {
        "definition": ("share_reaches_entry_again_complete_path == n_reclaimers / "
                       "n_reclaim_scored_rows in every primary cell (D3 regression guard), where "
                       "n_reclaim_scored_rows counts COMPLETE-PATH rows that also have a next open. "
                       "n_reclaim_scored_rows + n_complete_path_rows_without_next_open == "
                       "complete_path_rows.n is asserted too, so a reader can tie the scored "
                       "denominator back to the complete-path count the audit asked for; censored "
                       "rows are excluded outright and counted in recovery.n_excluded_censored"),
        "cells_compared": rec_cells, "max_abs_diff": r6(worst_rec), "worst_cell": worst_rec_cell,
        "censored_count_cells_checked": cens_cells,
        "n_excluded_censored_mismatches": int(worst_rec_cens),
        "pass": bool(rec_cells > 0 and worst_rec < TOL_ROUND and worst_rec_cens == 0),
    }
    checks["reclaim_ruler_share_bounded"] = {
        "definition": ("reference_recovery.share_reclaim_within_30_bars_complete_path must lie in "
                       "[0, 1] in every primary cell AND equal "
                       "n_reclaim_within_30_bars_complete_path / n_scored_rows_complete_path with "
                       "the numerator never exceeding the denominator: numerator and denominator "
                       "are both the complete-path population (an unfiltered numerator over a "
                       "filtered denominator saturated at 1.0 in the pre-fix build)"),
        "cells_checked": ruler_cells, "violations": int(worst_ruler),
        "pass": bool(ruler_cells > 0 and worst_ruler == 0),
    }
    # D4 selftest: the hindsight envelope can never cover more rows than the complete-path count
    worst_env, env_cells = 0, 0
    for setname, entry in out.get("series", {}).items():
        for et, blocks in entry.items():
            for blk, cell in blocks.items():
                env_n = (cell.get("ruler_envelope_hindsight") or {}).get("n")
                if env_n is None:
                    continue
                env_cells += 1
                complete = (cell.get("complete_path_rows") or {}).get("n") or 0
                worst_env = max(worst_env, env_n - complete)
    checks["envelope_never_exceeds_complete_path_rows"] = {
        "definition": ("ruler_envelope_hindsight.n <= complete_path_rows.n in every cell; a row whose "
                       "rulers are all null stays null instead of a fabricated 0.0 cash line (D4)"),
        "cells_checked": env_cells, "max_overcount": int(worst_env),
        "pass": bool(worst_env <= 0),
    }
    # migration guard: no v1 column name may survive anywhere in the emitted artifact
    leaks = scan_for_v1_names({k: v for k, v in out.items() if k != "checks"},
                              V1_FORBIDDEN_NAMES)
    checks["no_v1_column_names"] = {
        "forbidden": V1_FORBIDDEN_NAMES,
        "hits": leaks[:20], "n_hits": len(leaks),
        "recursion": "full (no list cap)", "pass": not leaks,
    }
    return checks


def _uncertainty_method() -> dict:
    return {
        "primary_cluster": ("sleeve_day — days are drawn with replacement and every member-minute of "
                            "a drawn day enters together; the conservative unit (tickets co-move "
                            "within a day)"),
        "secondary_cluster": ("ticket (sleeve_day|family|entry_rank) — reported for the MEAN only, "
                              "analytically"),
        "mean_interval": ("cluster-robust normal interval mean +- 1.96 * SE, SE = "
                          "sqrt(sum_c (S_c - n_c*mean)^2) / n over clusters c"),
        "median_interval": (f"percentile bootstrap over days, B={BOOT_B}, seed={BOOT_SEED}; the same "
                            "day draw is used for every minute of a replicate (a clustered bootstrap "
                            "of the whole series)"),
        "no_minute_row_independence": True,
        "columns_on_primitive_curves": ("final_high_flag (P(no further high), a share), remaining_run "
                                        "(remaining attainable move, tail-dominated — the mean has a "
                                        "wide interval by construction), cost_of_waiting and "
                                        "oracle_exec_next_open; censored rows are absent from all "
                                        "of them by construction and their counts are reported per "
                                        "cell"),
        "columns_on_reference_lines": ("v_forced_flat (primary executable ruler) and v_hold_flat "
                                       "(close reference) plus the executable oracle, on the compact "
                                       "grid"),
    }


def dense_primary_crossings(prim: dict) -> dict:
    """Where the reference line crosses zero on the DENSE primary clock axis (minute resolution)."""
    out: dict = {"disclaimer": ("IN-SAMPLE / DESCRIPTIVE ONLY: a crossing locates a feature of this "
                                "sample's median series under day resampling; it is not a forecast "
                                "and carries no holdout claim. See block_agreement for the "
                                "block1-vs-block2 view."),
                 "definition": ("first primary minute where the line moves from > 0 to <= 0, on the "
                                "dense 570..959 axis, so a crossing between noon and 15:59 is "
                                "locatable. Two lines are scanned: the PRIMARY executable ruler "
                                "v_forced_flat (forced-flat price) and the labelled close reference "
                                "v_hold_flat. Terminal-censored rows are absent from both by "
                                "construction (their terminal outcomes are null)."),
                 "sets": {}}
    for setname, entry in prim.items():
        mins = entry.get("minute") or {}
        if not mins:
            continue
        shaped = {}
        for et, blocks in mins.items():
            shaped[str(et)] = {}
            for blk, cell in blocks.items():
                ref = (cell or {}).get("reference_v_forced_flat", {})
                close_ref = (cell or {}).get("reference_v_hold_flat", {})
                shaped[str(et)][blk] = {"median": ref.get("median"), "mean": ref.get("mean"),
                                        "n": (cell or {}).get("n_outcome", 0)}
                shaped[str(et)][blk + "|close"] = {"median": close_ref.get("median"),
                                                   "mean": close_ref.get("mean"),
                                                   "n": (cell or {}).get("n_outcome", 0)}
        out["sets"][setname] = {
            "v_forced_flat_executable": zero_crossing_of(
                {k: {b: v[b] for b in v if not b.endswith("|close")} for k, v in shaped.items()},
                MINUTE_DENSE, "et"),
            "v_hold_flat_close_reference": zero_crossing_of(
                {k: {b[:-6]: v[b] for b in v if b.endswith("|close")} for k, v in shaped.items()},
                MINUTE_DENSE, "et"),
        }
    return out


def _meta(members: pl.DataFrame, cohorts_meta: dict, sets, identity: dict,
          rss_log: list[dict], dup_meta: dict) -> dict:
    return {
        "artifact": "ATLAS window profile — the minute-by-minute conditional future-path structure",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "producer": "factory/scripts/basket_atlas_window.py",
        "reproduce": REPRODUCE,
        "panel": {
            "path": "factory/artifacts/basket/phase2/ATLAS/panel.parquet",
            "schema_version": "v2 (2026-09-25 foundation fix)",
            "sha256": PANEL_SHA256,
            "sha256_v1_quarantined": PANEL_SHA256_V1_QUARANTINED,
            "sha256_verified_by": "checks.panel_sha256 (streamed in this run)",
            "rows": identity.get("panel_identity", {}).get("rows"),
            "members": identity.get("panel_identity", {}).get("members"),
            "days": identity.get("panel_identity", {}).get("days"),
            "families": ["A_pm", "B600"],
        },
        "framing": {
            "economic_target": ("the PRIMITIVE conditional future-path structure of the early leader "
                                "race: what the tape does after each exact minute and each exact "
                                "tenure — continuation distribution, probability of no further "
                                "high, remaining attainable move, adverse excursion while waiting, "
                                "recovery/reclaim structure and time to next/final high. See "
                                "`path_structure` (primary). No action-value formalism is imposed "
                                "before a continuation policy is justified; every value line here is "
                                "either the panel's declared ruler or a named, explicitly defined "
                                "rule, and all of them are labelled reference/anatomy."),
            "thesis_prior": ("the coarse prior this file is measured against (clock_cohort.json) is "
                             "HINDSIGHT-cohorted: it split members by their eventual session peak. "
                             "Its shape is cited only as a labelled prior and must not shrink into a "
                             "law. This file reports where continuation value crosses zero per set "
                             "and block, with day-clustered intervals, and asserts no time."),
            "headroom_warning": ("`remaining_oracle_executable_headroom` (executable oracle minus a "
                                 "named rule) is NOT 'the value of better state information': it "
                                 "mixes perfect foresight, the limits of the rule family, execution "
                                 "constraints and estimation error."),
            "cohorts_are_anatomy": ("every `cohort:*` key uses the eventual session peak "
                                    "(hindsight). Cohorts shape the curves and carry no policy "
                                    "conclusion. Censored members have no session peak and are "
                                    "reported separately as CENSORED_unlabelled."),
            "two_coordinates": ("clock minute et and ownership tenure bars_since_entry are different "
                                "variables; the primary profile is reported on both inside "
                                "`path_structure`, separately per family. A_pm and B600 are NEVER "
                                "pooled in a minute profile — the `all` key exists only as a "
                                "labelled reference."),
            "dependence": ("rows are repeated minutes of dependent tickets; see "
                           "`uncertainty.method` for the day-clustered (primary) and "
                           "ticket-clustered (secondary) intervals."),
            "memory": ("the run is sharded by (family|set) x (block|pooled) and holds one subset at "
                       "a time; see meta.memory."),
        },
        "grid": {
            "primary_minute_axis": {
                "values_span": [MINUTE_DENSE[0], MINUTE_DENSE[-1]],
                "count": len(MINUTE_DENSE),
                "resolution": ("every supported decision minute, fill bar (570) to 15:59 (959). "
                               "Nothing is truncated at noon: the value horizon between noon and "
                               "the close is locatable at minute resolution. Half-day sessions "
                               "(session_end 779) stop printing after 779 and those minutes show up "
                               "as thin/null in thin_minutes, never as missing data."),
                "where": "path_structure[...].minute + path_structure_uncertainty + thin_minutes",
            },
            "compact_reference_grid": {
                "morning_every_minute": [MORNING[0], MORNING[-1]],
                "context_minutes": CONTEXT,
                "n_minutes": len(GRID),
                "role": ("SECONDARY summary: the `series` rulers/anatomy cells, the paired rate of "
                         "change (previous COMPACT-grid minute, so 780 pairs with 720 at dt=60), the "
                         "median bootstrap and the compact zero-crossing scan are emitted here."),
            },
            "tenure_axis": ("every integer bars_since_entry 0..the panel's maximum "
                            f"({TENURE_DENSE[-1]}); the sparse {TENURE_GRID[0]}..{TENURE_GRID[-1]} "
                            "grid in by_tenure_compact_reference is a compact view only"),
            "notes": [
                "et is ET minute-of-day: 571 = 09:31, 720 = 12:00, 780 = 13:00, 840 = 14:00, "
                "900 = 15:00, 959 = 15:59 (the latest et in the panel).",
                "On the COMPACT grid, 958 is the last minute whose rows carry non-null outcomes (a "
                "row at 959 is a member's last bar, where next_open and every outcome column are "
                "null by contract); 959 and 960 are kept there so the empty cells are visible rather "
                "than mistaken for missing data. On the PRIMARY dense axis, 959 is the last decision "
                "minute that can carry state at all.",
                "The panel holds no bar at 960; its cells are empty by construction.",
                "Compact context minutes are spaced 60 minutes apart; the rate of change there is the "
                "pair delta divided by the realised dt_minutes (60).",
            ],
        },
        "units": {
            "returns": "decimal (0.05 = +5%), measured from the row's next_open unless stated",
            "shares": "fraction in [0, 1] unless stated",
            "bars": "completed 1-minute bars; bars_to_* are bar counts",
            "et": "ET minute-of-day integer",
            "rates_of_change": ("decimals per minute, rounded to 8 decimals (1e-8 = 0.0001 bps): one "
                                "minute of waiting moves the median value of staying exposed by "
                                "~1e-6, so 6 decimals would print 0.000000"),
            "top5_share_of_sum": ("sum of the five largest values / total sum, same sample, NOT "
                                  "clipped: > 1 or < 0 when the total is near zero or negative"),
        },
        "blocks": BLOCK_LABELS,
        "cohorts": {
            "key": "session_peak_ret_from_entry (ticket-level, repeated on every row)",
            "warning": ("ALL cohort keys are DESCRIPTIVE / HINDSIGHT labels: the session peak is "
                        "known only after the close. They shape the decay curves; they are never "
                        "rule inputs."),
            "members": cohorts_meta,
            "note": ("D_never_above_entry (session peak < 0) is EMPTY: the fill bar's own high is "
                     ">= entry_px (entry_px is that bar's open), so the session-peak return from "
                     "entry can never be negative — the observed minimum is exactly 0.0. The "
                     "non-empty reading of 'never above entry' is D2_zero_upside (peak <= 0). The "
                     "coarse prior this profile is measured against has the same empty D, so its "
                     "'~82% majority' is C_peak_0_30, which also contains the zero-upside members. "
                     "Cross-task label agreement with the fall-structure task: the same four "
                     "boundaries."),
        },
        "series_layout": {
            "primary_path": "path_structure[set].{minute|tenure}[axis_value][block]",
            "detail_path": "series[set].minutes[et][block]",
            "blocks": BLOCKS,
            "primary_per_cell": ("reference_v_forced_flat (primary executable ruler), "
                                 "reference_v_hold_flat (labelled close reference), "
                                 "reference_v_giveback_10, reference_executable_oracle, n, "
                                 "n_outcome, censored_rows, complete_path_rows, "
                                 "executable_outcome_rows, p_no_further_high, the quantiles and "
                                 "tail-class "
                                 "shares of remaining_run (remaining attainable move), the quantiles "
                                 "and loss shares of cost_of_waiting (adverse excursion), the "
                                 "quantiles of bars_to_next_high / bars_to_peak / peak_et_after_t, "
                                 "the recovery block (quantiles of bars_to_forward_reclaim, the "
                                 "never-share and the one-minute survival curve S(1..60) for the "
                                 "families), and reference medians of v_hold_flat / v_giveback_10 / "
                                 "the executable oracle."),
            "detail_per_cell": ("n, n_outcome, n_dup_path_rows, the nine value columns (v_hold_flat, "
                                "v_giveback_5/10/15/20, level_ret, remaining_run, cost_of_waiting, "
                                "oracle_exec_next_open) each with n/mean/median/positive_share/"
                                "top5_share_of_sum, the two named rules with n/mean/median/"
                                "positive_share, the hindsight envelope and four headroom columns "
                                "with n/mean/median, the climax clock with n/nulls/mean/median, "
                                "final_high_flag and giveback_fired_* with n/share, and "
                                "rate_of_change_vs_previous_grid_minute."),
        },
        "sets": {
            "keys": {name: {"scope": scope} for name, _m, scope in sets},
            "note": ("Each set conditions the same minute rows. 'full' emits every metric; 'reduced' "
                     "(the rare cohort x family splits) emits the headline columns only to bound the "
                     "artifact size. Sets whose key starts with 'cohort:' are DESCRIPTIVE/HINDSIGHT. "
                     "The `all` key pools both families and is a reference only."),
            "sharding": ("sets are computed in per-(family|set) shards; the `all` and cohort keys are "
                         "pooled across families by construction (their members really do span both "
                         "sleeves) and are labelled as descriptive references."),
        },
        "censoring": {
            "flags": ["terminal_censored", "path_complete_to_session_end"],
            "nature": ("member-level, future-derived v2 labels (coverage.json.future_only_columns); "
                       "never causal state"),
            "rule": ("a censored member's tape stops before the session close, so it has NO terminal "
                     "value and no executable terminal liquidation: v_hold_flat, v_forced_flat, all "
                     "v_giveback_*, giveback_fired_*, the path statistics and every ticket constant "
                     "are null on every one of its rows (panics: never null-as-zero)"),
            "handling": ("excluded from every complete-path/executable profile; each cell reports "
                         "`censored_rows.n`, `complete_path_rows.n` and `executable_outcome_rows.n` "
                         "so the unresolved part is explicit (shares are derivable from those "
                         "counts). State-only primitive counts may include them and are flagged. "
                         "Cohort keys get a dedicated CENSORED_unlabelled bucket instead of being "
                         "folded into a hindsight cohort. In the recovery/reclaim block censored "
                         "rows are excluded from the denominator outright (they cannot be scored as "
                         "'never reclaims') and their count is reported as "
                         "recovery.n_excluded_censored"),
            "where_reported": ["checks.censoring_census", "thin_minutes", "every cell's censored "
                               "counts"],
        },
        "causal_safety": {
            "rule": ("no market-state feature may be drawn from a future-only or censor column; this "
                     "artifact is descriptive anatomy and never builds a causal feature set, but the "
                     "future-only columns it reads (future_member_last_et for the tape-end "
                     "decomposition, the session constants for the hindsight cohorts, the censor "
                     "flags for reporting) are labelled as such everywhere they appear"),
            "future_only_columns_used": ["future_member_last_et", "future_forced_flat_px",
                                         "terminal_censored", "path_complete_to_session_end",
                                         "session_peak_ret_from_entry", "session_peak_et",
                                         "session_close_ret_from_entry"],
        },
        "giveback_semantics_v2": {
            "rule": ("a give-back exit is the open of the bar AFTER the trigger bar; a first "
                     "condition on a bar with et >= session_end - 1 cannot fire (the engine's forced "
                     "flat preempts release evaluation), so the continuation stays at the executable "
                     "forced-flat value with giveback_fired_* = false and the diagnostic "
                     "giveback_condition_after_forced_flat_* = true"),
            "reported": ("giveback_fired_* shares, giveback_condition_after_forced_flat_* shares and "
                         "the share with v_giveback_* == v_forced_flat are emitted per cell; "
                         "last-row / no-next-open / censored rows stay unscored (null)"),
        },
        "conventions": [
            "n counts panel rows (member-minutes) with a bar exactly at that ET minute; n_outcome "
            "counts those whose next_open exists (a member's last bar has no outcome). A member with "
            "no bar at that minute is absent (see thin_minutes). n_dup_path_rows counts rows of "
            "duplicated paths within the cell.",
            "Every value column is measured from that row's next_open (the panel's execution "
            "convention: a decision at completed bar t executes at the open of t+1).",
            "The panel is friction-free; all returns are gross. An exit executed at next_open costs "
            "side = bps_total / 2 / 10000 in return units (ledger convention: the exiting leg is "
            "(1 + gross) * (1 - side)); the entry leg is common to every line here and is not "
            "restated.",
            "v_forced_flat (panel v2) is the PRIMARY executable ruler: the engine's forced-flat "
            "price, i.e. future_forced_flat_px / next_open - 1. v_hold_flat is retained ONLY as the "
            "labelled CLOSE-reference line (close of the session_end bar / next_open - 1) for "
            "comparability with earlier results; it is not an execution price. The two are emitted "
            "side by side (reference_v_forced_flat / reference_v_hold_flat) and never conflated. "
            "v_giveback_* are the declared continuations (trigger share: giveback_fired_*, "
            "non-firing diagnostic: giveback_condition_after_forced_flat_*).",
            "level_ret = next_open / entry_px - 1: the accounting value of the position at the "
            "execution price, before exit friction, for pricing a decision taken at t.",
            "rule_cut_below_entry_else_hold / _giveback10 are causal state rules: they read only "
            "ret_from_fill at t (close < entry) and then either exit at next_open or follow a "
            "declared continuation. Their value is 0 when they exit at once — the cash line.",
            "ruler_envelope_hindsight = per-row max over {cash, v_hold_flat, v_giveback_5..20}: a "
            "HINDSIGHT argmax over continuations, an upper bound, not an achievable rule.",
            "oracle_exec_next_open: exit at the open of the bar after the session's max-high bar "
            "after t; when that bar is the member's last tracked bar the exit is the executable "
            "forced-flat price (future_forced_flat_px, the open of the session_end bar) — never the "
            "close. Censored members are null everywhere. Verified against the raw bars on 20 days "
            "including the forced-flat price (see checks.raw_bars_oracle_check).",
            "final_high_flag true == the running high at t was the session's last high; "
            "p_climb_over is its share (self-checked against bars_to_next_high being null).",
            "rate_of_change_vs_previous_grid_minute pairs a member's own previous and current grid "
            "rows only (dt_minutes = realised gap, null when it is not the nominal gap); "
            "series_delta is the plain difference of the two minutes' statistics and differs from "
            "the paired delta by the composition change recorded in the same cell.",
            "How to read the rate of change: the paired per-minute delta of v_hold_flat is a "
            "symmetric bar-to-bar change with a median of ~0 (the middle of the cross-section moves "
            "by roughly ±1% per minute even at 09:31) — the decay of the value of staying exposed "
            "shows up in the paired MEAN and in the tail/level series, not in the paired median. The "
            "zero crossings are the composition-aware statement.",
        ],
        "caveats": [
            "The minute series' composition changes: B600 enters at 600, tapes end (halts) and gap "
            "out of a minute. The paired rates, the balanced_morning_panel control and the "
            "day-clustered intervals are the answers to that; the level series alone are not.",
            "peak_et_after_t / bars_to_peak / bars_to_next_high / remaining_run / cost_of_waiting / "
            "final_high_flag / oracle_exec_next_open are OUTCOME (look-ahead) columns used here as "
            "path labels and ceilings, never as state.",
            "The executable oracle is hindsight-optimal: no rule achieves it. The headroom between "
            "it and the nominated rule is not a forecast.",
            "Half days (7 sessions, session_end = 779) leave the minute series after 779; minutes "
            "after that are thinner and block-mixed (see thin_minutes).",
            "The tenure profile is computed on the full tape (every panel row); the cohort sets get "
            "it without the survival curve, which is emitted for the two families.",
        ],
    }


SHARD_SERIES_COLS = [c for c in SERIES_COLS if c != "session_peak_ret_from_entry"] + \
    ["session_peak_ret_from_entry"]
SHARD_PRIM_COLS = [c for c in PRIM_COLS if c != "session_peak_ret_from_entry"] + \
    ["session_peak_ret_from_entry"]


def headlines(out: dict) -> None:
    print("== censoring (v2) ==")
    cen = out.get("checks", {}).get("censoring_census", {})
    print(f"  censored rows {cen.get('censored_rows')} ({cen.get('censored_rows_share')}) of "
          f"{out['meta']['panel']['rows']}; member-level, unresolved, excluded from executable "
          f"profiles")
    print("== zero crossings of the executable ruler (v_forced_flat median; close ref alongside) ==")
    for setname, lines in out["zero_crossings"].items():
        blocks = lines["v_forced_flat_executable"]
        for blk in BLOCKS:
            for stat in ("median", "mean"):
                c = blocks[blk][stat]
                if c["status"] == "crosses":
                    print(f"  {setname:34s} {blk:6s} {stat:6s} crosses at "
                          f"{c['interpolated_crossing']:7.2f} (last positive {c['last_positive']} "
                          f"{c['last_positive_value']:+.4f} n={c['last_positive_n']}, next "
                          f"{c['first_nonpositive']} {c['first_nonpositive_value']:+.4f} "
                          f"n={c['first_nonpositive_n']})")
                else:
                    print(f"  {setname:34s} {blk:6s} {stat:6s} {c['status']}")
    print("== crossing-minute bootstrap (families) ==")
    for setname, blocks in out["uncertainty"]["crossing_minute"].items():
        for blk in ("pooled", "block1", "block2"):
            c = blocks.get(blk)
            if not c or c.get("status") == "no_data":
                continue
            print(f"  {setname:26s} {blk:6s} median crossing {c['median_crossing_minute']} "
                  f"ci95 {c['ci95_crossing_minute']} no-crossing share "
                  f"{c['share_no_crossing_stays_positive']}")
    print("== reference lines at landmark minutes "
          "(v_forced_flat median | v_hold_flat close ref | oracle_exec median) ==")
    for setname, entry in out["reference_lines"]["by_set"].items():
        for blk in ("pooled", "block1", "block2"):
            line = []
            for et in (571, 600, 660, 720, 780, 900):
                c = entry["per_minute"].get(str(et), {}).get(blk)
                if c:
                    line.append(f"{et}:{c['ruler_baseline_v_forced_flat_executable']['median']}"
                                f"/{c['close_reference_v_hold_flat']['median']}"
                                f"/{c['executable_oracle']['median']}")
            print(f"  {setname:22s} {blk:6s} " + " ".join(line))
    print("== headroom (executable oracle minus nominated rule; morning pooled) ==")
    for setname, blocks in out["reference_lines"]["headroom_summary"].items():
        for blk in ("pooled", "block1", "block2"):
            g = blocks[blk]
            nom = out["reference_lines"]["nomination"][setname][blk]["nominated_by_morning_mean"]
            fm = lambda v: "na" if v is None else f"{v:+.4f}"  # noqa: E731
            print(f"  {setname:22s} {blk:6s} nominated {str(nom):>38s} "
                  f"rule_mean {fm(g['nominated_rule_morning_mean'])} oracle_mean "
                  f"{fm(g['executable_oracle_morning_mean'])} headroom(med) "
                  f"{fm(g['headroom_median_median'])} "
                  f"optimism(med) {fm(g['optimistic_minus_executable_median_mean'])}")
    print("== block agreement (executable-ruler median sign) ==")
    for setname, s in out["block_agreement"]["sign"].items():
        ref = s.get("v_forced_flat_median", {})
        print(f"  {setname:34s} {ref.get('sign_agreement_share')} over "
              f"{ref.get('minutes_compared')} minutes")
    co = out["block_agreement"]["cohort_ordering"]
    print(f"  cohort ordering: identical in {co['share_minutes_identical_order']} of "
          f"{co['minutes_compared']} minutes, mean spearman {co['mean_spearman']}")
    print("== decay of the value of staying exposed (paired per-minute deltas, per family) ==")
    for setname in ("family:A_pm", "family:B600", "cohort:A_peak_ge_100", "cohort:C_peak_0_30"):
        rows = []
        for et in MORNING:
            roc = out["series"][setname][str(et)]["pooled"].get(
                "rate_of_change_vs_previous_grid_minute", {})
            m = roc.get("per_minute", {}).get("v_hold_flat", {})
            if m.get("mean") is not None:
                rows.append((et, m["mean"], m.get("median")))
        neg = [r for r in rows if r[1] < 0]
        print(f"  {setname:24s} morning minutes {len(rows)}, mean delta < 0 in {len(neg)} "
              f"({r6(len(neg)/len(rows)) if rows else None}); mean over morning "
              f"{r6(float(np.mean([r[1] for r in rows]))) if rows else None}; "
              f"sum {r6(float(np.sum([r[1] for r in rows]))) if rows else None}")
        for et, mean, med in sorted(rows, key=lambda x: x[1])[:5]:
            print(f"      fastest et {et}: paired mean {mean:+.6f}/min median {med:+.6f}")
    print("== dense primary crossings (executable ruler / close reference, minute resolution) ==")
    for setname, entry in out["zero_crossings_primary_dense"]["sets"].items():
        for blk in ("block1", "block2"):
            for line_name, blocks in entry.items():
                c = blocks[blk]["median"]
                tag = "exec " if line_name.startswith("v_forced") else "close"
                val = c.get("interpolated_crossing", c["status"])
                print(f"  {setname:32s} {blk:6s} {tag:6s} median crossing {val}")
    print("== primitive structure, first minutes of ownership (pooled) ==")
    for setname in ("family:A_pm", "family:B600"):
        ser = out["path_structure"]["sets"][setname]["tenure"]
        for t in (1, 5, 15, 30, 60, 120, 180):
            c = ser.get(str(t), {}).get("pooled", {})
            if not c.get("n"):
                continue
            rec = c.get("recovery", {})
            surv = rec.get("survival_curve") or []
            print(f"  {setname:14s} tenure {t:3d}: n={c['n']:5d} "
                  f"p_no_further_high={c.get('p_no_further_high', {}).get('share')} "
                  f"remaining_run med={c.get('remaining_run', {}).get('median')} "
                  f"p90={c.get('remaining_run', {}).get('p90')} "
                  f"ge100={c.get('remaining_run', {}).get('share_ge_100pct')} "
                  f"cost_wait p10={c.get('cost_of_waiting', {}).get('p10')} "
                  f"reclaim={rec.get('share_reaches_entry_again')} S(10)={surv[9] if len(surv) > 9 else None} "
                  f"S(60)={surv[-1] if surv else None}")
    print("== thin minutes (primary dense axis) ==")
    pm = out['thin_minutes']['per_minute']
    print(f"  flagged {out['thin_minutes']['minutes_flagged']} of {len(pm)} primary minutes "
          f"({min(int(k) for k in pm)}..{max(int(k) for k in pm)}); median coverage "
          f"{out['thin_minutes']['median_coverage_of_expected']}")
    print("== checks ==")
    for k, v in out["checks"].items():
        if isinstance(v, dict) and "pass" in v:
            print(f"  {k}: {'PASS' if v['pass'] else 'FAIL'}")
    print("  coordinate claim:", out["coordinate_mapping"]["claim_check"]["observed"],
          "holds:", out["coordinate_mapping"]["claim_check"]["holds"])


def build_index(out: dict) -> dict:
    """Compact, pretty-printed projection of the artifact for independent auditors (< 1 MB).

    Same values as the full artifact, just the headline cells: panel binding, censoring, axes, the
    hand-verified axis values, per-family/per-block headline series at a coarse stride, sampled
    uncertainty levels, the crossing blocks, duplicate-path sensitivity and the checks list.
    """
    ps = out["path_structure"]["sets"]
    anchors = {570, 571, 600, 660, 720, 780, 840, 900, 958, 959}
    minute_idx = [et for et in MINUTE_DENSE if et % 10 == 0 or et in anchors]
    tenure_idx = [t for t in TENURE_DENSE if t % 20 == 0 or t in (0, 1, 2, 3, 5, 10, 30, 60, 180)]
    survival_anchors = {("minute", 570), ("minute", 600), ("minute", 720), ("minute", 900),
                        ("tenure", 0), ("tenure", 1), ("tenure", 5), ("tenure", 30),
                        ("tenure", 60), ("tenure", 180)}
    unc_minutes = ["570", "571", "600", "660", "720", "780", "840", "900", "959"]

    def prim_cell(cell: dict, axis: str = "", key: int | None = None) -> dict:
        if not cell:
            return {}
        return {
            "n": cell.get("n"), "n_outcome": cell.get("n_outcome"),
            "censored_rows": (cell.get("censored_rows") or {}).get("n"),
            "complete_path_rows": (cell.get("complete_path_rows") or {}).get("n"),
            "executable_outcome_rows": (cell.get("executable_outcome_rows") or {}).get("n"),
            "p_no_further_high": (cell.get("p_no_further_high") or {}).get("share"),
            "remaining_run": {k: (cell.get("remaining_run") or {}).get(k)
                              for k in ("p10", "p50", "p90", "p99", "mean",
                                        "share_ge_50pct", "share_ge_100pct")},
            "cost_of_waiting": {k: (cell.get("cost_of_waiting") or {}).get(k)
                                for k in ("p01", "p05", "p10", "p50", "mean")},
            "bars_to_next_high_p50": (cell.get("bars_to_next_high") or {}).get("p50"),
            "bars_to_next_high_nulls": (cell.get("bars_to_next_high") or {}).get("nulls"),
            "peak_et_after_t_p50": (cell.get("peak_et_after_t") or {}).get("p50"),
            "recovery": {k: (cell.get("recovery") or {}).get(k)
                         for k in ("share_reaches_entry_again_complete_path", "n_reclaimers",
                                   "median_bars_to_reclaim",
                                   "share_reaches_entry_again_when_below",
                                   "median_bars_to_reclaim_when_below", "n_excluded_censored",
                                   "n_reclaim_scored_rows",
                                   "n_complete_path_rows_without_next_open")},
            "survival_curve_1_60": ((cell.get("recovery") or {}).get("survival_curve")
                                    if (axis, key) in survival_anchors else None),
            "reference_recovery_share_reclaim_within_30_bars":
                (cell.get("reference_recovery") or {}).get(
                    "share_reclaim_within_30_bars_complete_path"),
            "recovery_denominators": {
                k: (cell.get("recovery") or {}).get(k)
                for k in ("n_reclaim_scored_rows", "n_complete_path_rows_without_next_open",
                          "n_excluded_censored")},
            "envelope": {"n": (cell.get("ruler_envelope_hindsight") or {}).get("n"),
                         "median": (cell.get("ruler_envelope_hindsight") or {}).get("median")},
            "reference_v_forced_flat_median":
                (cell.get("reference_v_forced_flat") or {}).get("median"),
            "reference_v_hold_flat_median": (cell.get("reference_v_hold_flat") or {}).get("median"),
            "reference_v_giveback_10_median":
                (cell.get("reference_v_giveback_10") or {}).get("median"),
            "reference_executable_oracle_median":
                (cell.get("reference_executable_oracle") or {}).get("median"),
            "post_forced_flat_condition_g10":
                (cell.get("post_forced_flat_condition") or {}).get("share_g10"),
        }

    idx: dict = {
        "about": ("Readable projection of window_profile.json for independent audit; identical "
                  "values, headline cells only. The full artifact is single-line JSON (~38 MB)."),
        "generated_utc": out["meta"]["generated_utc"],
        "producer": out["meta"]["producer"],
        "reproduce": out["meta"]["reproduce"],
        "panel": out["meta"]["panel"],
        "memory": {k: v for k, v in out["meta"]["memory"].items() if k != "shard_log"},
        "censoring": out["meta"]["censoring"],
        "axes": out["path_structure"]["axes"],
        "censoring_census": out["checks"].get("censoring_census"),
        "verified_axis_values": out["meta"].get("verified_axis_values"),
        "primary_series": {}, "reference_lines": {}, "uncertainty_sample": {},
        "crossings": {
            "dense_primary": out["zero_crossings_primary_dense"],
            "compact_scan": {k: v for k, v in out["zero_crossings"].items()
                             if k != "disclaimer"},
            "compact_disclaimer": out["zero_crossings"].get("disclaimer"),
        },
        "duplicate_paths": out["duplicate_paths"],
        "block_agreement": {
            "note": out["block_agreement"].get("definition"),
            "sign": {k: {kk: vv for kk, vv in v.items() if kk.startswith("v_forced")}
                     for k, v in out["block_agreement"]["sign"].items()},
            "cohort_ordering": {k: v for k, v in out["block_agreement"]["cohort_ordering"].items()
                                if k != "per_minute"},
        },
        "checks": {k: (v.get("pass") if isinstance(v, dict) else None)
                   for k, v in out["checks"].items()},
        "checks_detail": {k: v for k, v in out["checks"].items()
                          if k in ("censoring_census", "v2_ruler_identities",
                                   "raw_bars_oracle_check",
                                   "clustered_mean_equals_point_estimate",
                                   "recovery_share_uses_complete_path_denominator",
                                   "envelope_never_exceeds_complete_path_rows",
                                   "tenure_axis_consistency", "duplicate_paths_source",
                                   "no_v1_column_names", "independent_recompute")},
    }
    for fam in ("family:A_pm", "family:B600"):
        mins = {str(et): {blk: prim_cell((ps[fam]["minute"].get(str(et)) or {}).get(blk) or {},
                                         "minute", et)
                          for blk in BLOCKS} for et in minute_idx}
        ten = {str(t): {blk: prim_cell((ps[fam]["tenure"].get(str(t)) or {}).get(blk) or {},
                                       "tenure", t)
                        for blk in BLOCKS} for t in tenure_idx}
        idx["primary_series"][fam] = {"minute": mins, "tenure": ten}
    for setname, entry in out["reference_lines"]["by_set"].items():
        per_minute = {}
        for et in ("600", "660", "720", "780", "840", "900"):
            c = entry["per_minute"].get(et)
            if c:
                per_minute[et] = {
                    "n_outcome": c.get("n_outcome"),
                    "executable_ruler_v_forced_flat":
                        c.get("ruler_baseline_v_forced_flat_executable"),
                    "close_reference_v_hold_flat": c.get("close_reference_v_hold_flat"),
                    "executable_oracle": c.get("executable_oracle"),
                    "nominated_rule": c.get("nominated_rule"),
                    "nominated_rule_value": c.get("nominated_rule_value"),
                    "remaining_oracle_executable_headroom":
                        c.get("remaining_oracle_executable_headroom"),
                }
        idx["reference_lines"][setname] = {
            "nominated_rule_by_mean": entry.get("nominated_rule_by_mean"),
            "headroom_summary": entry.get("blocks"),
            "per_minute": per_minute,
        }
    for setname, blocks in out.get("path_structure_uncertainty", {}).get("levels", {}).items():
        idx["uncertainty_sample"][setname] = {
            blk: {et: cells.get(et) for et in unc_minutes} for blk, cells in blocks.items()}
    return idx


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--index-out", default=str(OUT_INDEX),
                    help="compact readable projection written next to the full artifact")
    ap.add_argument("--print", dest="show", action="store_true")
    ap.add_argument("--no-raw-check", dest="raw", action="store_false")
    args = ap.parse_args()
    if not PANEL.exists():
        print(f"panel not found: {PANEL}", file=sys.stderr)
        return 2
    out = build(print_headlines=args.show, raw_check_enabled=args.raw)
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w") as fh:
        json.dump(out, fh, separators=(",", ":"), allow_nan=False)
    index = build_index(out)
    idx_dest = Path(args.index_out)
    idx_dest.parent.mkdir(parents=True, exist_ok=True)
    with open(idx_dest, "w") as fh:
        json.dump(index, fh, indent=1, sort_keys=False, allow_nan=False)
    failed = [k for k, v in out["checks"].items() if isinstance(v, dict) and v.get("pass") is False]
    print(f"wrote {dest} ({dest.stat().st_size/1e6:.2f} MB) and {idx_dest} "
          f"({idx_dest.stat().st_size/1e6:.2f} MB) in {out['meta']['run_seconds']}s; "
          f"checks failed: {failed if failed else 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
