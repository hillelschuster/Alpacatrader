"""ATLAS minute-panel producer — `PLAN-ATLAS-01` stage 3 / `ATLAS/SCHEMA.md`.

Builds `factory/artifacts/basket/phase2/ATLAS/panel.parquet`: one row per
(member, completed bar) for every A_pm top-3 and B600 top-3 filled member of
every development day, tracked from the fill bar to the session's last bar.

Contract: `factory/artifacts/basket/phase2/ATLAS/SCHEMA.md` (frozen).  This
producer implements that schema literally; every place the schema was ambiguous
is listed in `coverage.json["ambiguities"]` and in `ATLAS/README.md`.

Conventions implemented (see SCHEMA.md):
  * causality  — state uses bars with ``et <= t`` plus the fill only.
  * execution  — a decision at completed bar ``t`` executes at the open of bar
    ``t+1``; at the session's last bar every outcome column is ``null``.
  * friction   — none here; analyses apply ``bps_total/2`` per side.
  * no fabrication — a missing input yields ``null``.
  * determinism — months are written in day order, rows sorted by
    ``(sleeve_day, family, entry_rank, et)``; identical inputs give byte-identical
    parquet.

Usage (from the repository root)::

    # the whole deliverable, one line (smoke -> self-tests -> 1,066 days ->
    # merge -> coverage -> self-tests on the full panel)
    python factory/scripts/basket_atlas_panel.py all

    # individually
    python factory/scripts/basket_atlas_panel.py smoke [--determinism]
    python factory/scripts/basket_atlas_panel.py selftest --panel <path>
    python factory/scripts/basket_atlas_panel.py build --all [--force]
    python factory/scripts/basket_atlas_panel.py merge
    python factory/scripts/basket_atlas_panel.py coverage

Long computes write one parquet part per month under ``ATLAS/parts/`` and record
each finished month in ``ATLAS/_progress.json``; re-running resumes at the first
month that is missing or was built from a different day list.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import random
import re
import shutil
import sys
import tempfile
import time
import warnings
from pathlib import Path

import numpy as np
import polars as pl

try:
    from factory.scripts import basket_sim as sim
except ImportError:  # direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from factory.scripts import basket_sim as sim

ROOT = Path(__file__).resolve().parents[2]
ATLAS = ROOT / "factory/artifacts/basket/phase2/ATLAS"
SMOKE_DIR = ATLAS / "smoke"
PANEL = ATLAS / "panel.parquet"
COVERAGE = ATLAS / "coverage.json"

SCHEMA_VERSION = 2
GIVEBACK_LEVELS = (5, 10, 15, 20)
TAIL_LEVELS = (50, 100, 300)
FIRST_ET = 570

# (family label, anatomy snapshot pop, snapshot T, nominal fill et)
FAMILIES = (("A_pm", "A_pm", 570), ("B600", "B", 600))
FAMILY_KEYS = tuple(f[0] for f in FAMILIES)
CROSS_POP, CROSS_T = "A_pm", 570  # the day's candidate universe (SCHEMA.md)

# Anchored population counts (C1 engine `n_entries`, N3/R0 cells, 1,066 days).
ANCHORS = {"A_pm": 3188, "B600": 2972}
C1_CELLS = {
    "A_pm": ("A_pm_N3_R0_bps100", 3188),
    "B600": ("B600_N3_R0_bps100", 2972),
}
BLOCK1_LAST_DAY = "2023-12-31"
SMOKE_PER_BLOCK = 10

# --------------------------------------------------------------------------- #
# Column registry: (name, polars dtype, family).  `family` groups columns for
# the coverage report and marks which columns are producer additions.
# --------------------------------------------------------------------------- #

COLUMNS: list[tuple[str, object, str]] = [
    ("sleeve_day", pl.Utf8, "key"),
    ("month", pl.Utf8, "key"),
    ("block", pl.Utf8, "key"),
    ("family", pl.Utf8, "key"),
    ("ticker", pl.Utf8, "key"),
    ("entry_rank", pl.Int32, "key"),
    ("entry_et", pl.Int32, "key"),
    ("entry_px", pl.Float64, "key"),
    ("et", pl.Int32, "key"),
    ("bar_index", pl.Int32, "key"),
    ("bars_since_entry", pl.Int32, "key"),
    ("session_end", pl.Int32, "key"),
    # raw tape at the completed bar (causal; producer addition, see README)
    ("bar_open", pl.Float64, "tape"),
    ("bar_high", pl.Float64, "tape"),
    ("bar_low", pl.Float64, "tape"),
    ("bar_close", pl.Float64, "tape"),
    ("volume", pl.Float64, "tape"),
    ("dollar_volume", pl.Float64, "tape"),
    ("prev_close", pl.Float64, "tape"),
    ("open0930", pl.Float64, "tape"),
    # own path
    ("ret_from_fill", pl.Float64, "state_path"),
    ("ret_from_prevclose", pl.Float64, "state_path"),
    ("ret_from_open0930", pl.Float64, "state_path"),
    ("mfe_so_far", pl.Float64, "state_path"),
    ("mae_so_far", pl.Float64, "state_path"),
    ("running_high", pl.Float64, "state_path"),
    ("dist_from_running_high", pl.Float64, "state_path"),
    ("mfe_surrendered", pl.Float64, "state_path"),
    # episode state
    ("bars_below_entry_episode", pl.Int32, "state_episode"),
    ("episode_low", pl.Float64, "state_episode"),
    ("bars_since_episode_low", pl.Int32, "state_episode"),
    ("reclaim_count", pl.Int32, "state_episode"),
    ("failed_reclaim_count", pl.Int32, "state_episode"),
    ("bars_since_new_high", pl.Int32, "state_episode"),
    ("new_high_count_5", pl.Int32, "state_episode"),
    ("new_high_count_15", pl.Int32, "state_episode"),
    ("new_high_count_30", pl.Int32, "state_episode"),
    # dynamics
    ("ret_1", pl.Float64, "state_dynamics"),
    ("ret_3", pl.Float64, "state_dynamics"),
    ("ret_5", pl.Float64, "state_dynamics"),
    ("accel_1_5", pl.Float64, "state_dynamics"),
    ("up_close_streak", pl.Int32, "state_dynamics"),
    ("range_expansion", pl.Float64, "state_dynamics"),
    ("bar_range_pct", pl.Float64, "state_dynamics"),
    # attention
    ("volume_vs_own_median", pl.Float64, "state_attention"),
    ("volume_accel", pl.Float64, "state_attention"),
    ("gap_count_so_far", pl.Int32, "state_attention"),
    ("bars_since_gap", pl.Int32, "state_attention"),
    # cross-section (live, causal)
    ("candidate_count_t", pl.Int32, "state_cross"),
    ("ret_percentile_candidates", pl.Float64, "state_cross"),
    ("peer_ret_median", pl.Float64, "state_cross"),
    ("peer_new_high_5", pl.Int32, "state_cross"),
    # outcomes (strictly after t, from next_open)
    ("next_open", pl.Float64, "outcome_level"),
    ("next_et", pl.Int32, "outcome_level"),
    ("v_hold_flat", pl.Float64, "outcome_continuation"),
    ("v_forced_flat", pl.Float64, "outcome_continuation"),
    ("v_giveback_5", pl.Float64, "outcome_continuation"),
    ("v_giveback_10", pl.Float64, "outcome_continuation"),
    ("v_giveback_15", pl.Float64, "outcome_continuation"),
    ("v_giveback_20", pl.Float64, "outcome_continuation"),
    ("giveback_fired_5", pl.Boolean, "outcome_continuation"),
    ("giveback_fired_10", pl.Boolean, "outcome_continuation"),
    ("giveback_fired_15", pl.Boolean, "outcome_continuation"),
    ("giveback_fired_20", pl.Boolean, "outcome_continuation"),
    ("giveback_condition_after_forced_flat_5", pl.Boolean, "outcome_continuation"),
    ("giveback_condition_after_forced_flat_10", pl.Boolean, "outcome_continuation"),
    ("giveback_condition_after_forced_flat_15", pl.Boolean, "outcome_continuation"),
    ("giveback_condition_after_forced_flat_20", pl.Boolean, "outcome_continuation"),
    ("v_sell", pl.Float64, "outcome_continuation"),
    ("level_ret", pl.Float64, "outcome_continuation"),
    ("final_high_flag", pl.Boolean, "outcome_path"),
    ("remaining_run", pl.Float64, "outcome_path"),
    ("cost_of_waiting", pl.Float64, "outcome_path"),
    ("bars_to_next_high", pl.Int32, "outcome_path"),
    ("dd_before_next_high", pl.Float64, "outcome_path"),
    ("bars_to_peak", pl.Int32, "outcome_path"),
    ("peak_et_after_t", pl.Int32, "outcome_path"),
    ("tail_class_50", pl.Boolean, "outcome_path"),
    ("tail_class_100", pl.Boolean, "outcome_path"),
    ("tail_class_300", pl.Boolean, "outcome_path"),
    # ticket-level constants (session-wide; deliberately look-ahead)
    ("session_peak_et", pl.Int32, "ticket_constant"),
    ("session_peak_ret_from_entry", pl.Float64, "ticket_constant"),
    ("session_peak_bars_from_entry", pl.Int32, "ticket_constant"),
    ("session_close_ret_from_entry", pl.Float64, "ticket_constant"),
    # producer additions that make the tape end explicit
    ("path_complete_to_session_end", pl.Boolean, "censor"),
    ("terminal_censored", pl.Boolean, "censor"),
    ("future_member_last_et", pl.Int32, "future_meta"),
    ("future_forced_flat_px", pl.Float64, "future_meta"),
]

# Outcome columns that assume the member's tape runs to the session close (or an
# executable terminal liquidation).  For a member without a session-end print
# (`terminal_censored`) every one of them is null: the tape cannot establish the
# remaining session or a terminal price.
CENSORED_OUTCOME_COLUMNS = (
    "v_hold_flat", "v_forced_flat",
    "v_giveback_5", "v_giveback_10", "v_giveback_15", "v_giveback_20",
    "giveback_fired_5", "giveback_fired_10", "giveback_fired_15", "giveback_fired_20",
    "giveback_condition_after_forced_flat_5", "giveback_condition_after_forced_flat_10",
    "giveback_condition_after_forced_flat_15", "giveback_condition_after_forced_flat_20",
    "final_high_flag", "remaining_run", "cost_of_waiting",
    "bars_to_peak", "peak_et_after_t",
    "tail_class_50", "tail_class_100", "tail_class_300",
    "session_peak_et", "session_peak_ret_from_entry", "session_peak_bars_from_entry",
    "session_close_ret_from_entry",
)

# Columns that carry information from after t and must never be used as causal
# state.  `future_` is the naming prefix for such columns; the four session-wide
# ticket constants keep their SCHEMA.md names (frozen interface) and are listed
# here so the ledger guard can exclude them by registry rather than by prefix.
# The two censor flags are future-derived as well: whether a tape reaches the
# session close is only known after the session, so a row at t < last bar that
# could read them would see its own future halt.
FUTURE_ONLY_PREFIXES = ("future_",)
FUTURE_ONLY_COLUMNS = (
    "session_peak_et", "session_peak_ret_from_entry",
    "session_peak_bars_from_entry", "session_close_ret_from_entry",
    "future_member_last_et", "future_forced_flat_px",
    "path_complete_to_session_end", "terminal_censored",
)
CAUSAL_EXCLUDED_FAMILIES = ("outcome_level", "outcome_continuation", "outcome_path",
                            "ticket_constant", "future_meta", "censor")
# Columns read from *other* artifacts (the C1 engine's tickets) rather than from
# the panel; the source-level audit below allows these explicitly.
EXTERNAL_COLUMN_REFS = ("exit_day", "exit_et", "exit_px", "exit_reason", "n_entries")
CAUSAL_SAFETY_RULE = (
    "no market-state feature may be drawn from a column whose family is in "
    "causal_excluded_families (outcome_level, outcome_continuation, outcome_path, "
    "ticket_constant, future_meta, censor) nor from any column named in "
    "future_only_columns / prefixed `future_`.  Censoring flags are cohort labels, "
    "not state: they describe the tape's future completeness."
)


def causal_feature_columns(columns) -> list[str]:
    """Columns a generic causal feature selector is allowed to admit."""
    return [c for c in columns
            if c in COLUMN_FAMILIES
            and COLUMN_FAMILIES[c] not in CAUSAL_EXCLUDED_FAMILIES
            and not c.startswith(FUTURE_ONLY_PREFIXES)
            and c not in FUTURE_ONLY_COLUMNS]

# outcome columns that SCHEMA.md places under "Outcome columns (strictly after t)"
OUTCOME_COLUMNS = tuple(
    name for name, _d, fam in COLUMNS
    if fam in ("outcome_level", "outcome_continuation", "outcome_path")
)
# SCHEMA.md declares these two "null if none" (no later high at all), so they may
# be null while next_open exists; every other outcome column of a complete tape
# is populated whenever next_open exists.
NO_EVENT_NULL_OUTCOMES = ("bars_to_next_high", "dd_before_next_high")
COLUMN_FAMILIES = {name: fam for name, _d, fam in COLUMNS}
COLUMN_DTYPES = {name: dt for name, dt, _f in COLUMNS}

TOL = 1e-12
# A give-back trigger compares a close against a *computed product*
# running_high * (1 - g/100), so an exact tie (a close precisely g% below the
# running high) can land on either side of the comparison because of binary
# rounding of the product.  The trigger therefore carries a relative tolerance:
# a drop that is exactly g% is a hit.
GIVEBACK_TOL_REL = 1e-9

# Producer additions: columns that are not in SCHEMA.md's frozen list but are
# needed to read the panel exactly (documented in README.md and coverage.json).
PRODUCER_ADDITIONS = {
    "bar_open": "raw tape: open of the completed bar t (causal; = next_open of row t-1)",
    "bar_high": "raw tape: high of the completed bar t",
    "bar_low": "raw tape: low of the completed bar t",
    "bar_close": "raw tape: close of the completed bar t (= the panel's mark price)",
    "prev_close": "anatomy day constant, used by ret_from_prevclose (repeated per row)",
    "open0930": "anatomy day constant, used by ret_from_open0930 (repeated per row)",
    "path_complete_to_session_end": ("member-level: the member printed at the session's last "
                                     "minute, so its tape carries the whole session"),
    "terminal_censored": ("member-level flag = not path_complete_to_session_end; every "
                          "terminal-dependent outcome is null for that member"),
    "future_member_last_et": ("FUTURE-ONLY: ET of the member's last print. Known only after "
                              "the session; never an eligible causal input."),
    "giveback_condition_after_forced_flat": ("the close condition of the give-back rule appeared "
                                             "only on the session_end bar's close, after the engine "
                                             "had already gone flat — diagnostic, never a firing"),
    "future_forced_flat_px": ("FUTURE-ONLY: the engine's executable terminal price — the open of "
                              "the session_end bar (basket_sim schedules FORCED_FLAT at "
                              "session_end-1 and executes it at the next bar's open). Null when "
                              "the member has no print at session_end (the engine carries it)."),
}

CONVENTIONS = {
    "causality": "state uses only bars with et <= t plus the fill; no session aggregate after t",
    "execution": "a decision at completed bar t executes at the open of bar t+1",
    "last_bar": "at the member's last tracked bar, next_open/next_et and every outcome column are null",
    "terminal_execution": ("the engine schedules FORCED_FLAT on the completed bar at "
                           "session_end-1 and executes it at the session_end bar's OPEN; "
                           "future_forced_flat_px is that price and v_forced_flat is the "
                           "hold-to-flat continuation measured from it. v_hold_flat is the "
                           "labelled CLOSE baseline (close of the session_end bar), kept only "
                           "for comparability — it is not the engine's execution price."),
    "friction": "none in the panel; analyses apply bps_total/2 per side on the executed action",
    "no_fabrication": "a missing input yields null, never a filled or carried value",
    "fill_bar": "first bar of the member with et >= the anatomy fill et (anatomy is authoritative)",
    "session_end": "day-level session end (959, or 779 on the 7 half-days), repeated per row",
    "member_window": "fill bar .. last member bar with et <= session_end",
    "terminal_censoring": ("a member whose tape stops before the session close has every "
                           "terminal-dependent outcome null (path_complete_to_session_end = "
                           "false); state columns are unaffected"),
    "giveback_execution": ("the trigger bar's next bar must exist for the exit to execute. The "
                           "engine's forced flat goes flat at the session_end bar's OPEN, so a "
                           "give-back close condition first seen on that bar's CLOSE cannot "
                           "preempt it: v_giveback_g = v_forced_flat, giveback_fired_g = false, "
                           "recorded in giveback_condition_after_forced_flat_g (diagnostic only). "
                           "Only terminal-censored tapes have null give-back values."),
    "future_columns": ("future_member_last_et and the four session_peak_*/session_close_ret_* "
                       "constants carry post-t information and must never be used as causal "
                       "state (registry: coverage.json.future_only_columns)"),
    "block1": "days <= 2023-12-31; block2: days >= 2025-02-01",
    "sort": "rows sorted by (sleeve_day, family, entry_rank, et)",
}

AMBIGUITIES = [
    {
        "schema": "`family` | `A_pm` (fill at ET 571) or `B600` (fill at ET 601)",
        "issue": "the anatomy fill et is not 571/601 (A_pm actually fills at 570 on most days, "
                 "B600 at 600, and a name can fill later inside a gapped tape)",
        "resolution": "the schema's 571/601 is NOMINAL; the anatomy fill is authoritative and is "
                      "what the C1 engine reads (entry_et/entry_px are the anatomy fill, asserted "
                      "equal to the C1 tickets). Realised entry_et distribution: "
                      "coverage.tape_shape.entry_et_histogram. SCHEMA.md carries an amendment "
                      "saying exactly this.",
    },
    {
        "schema": "tapes that stop before the session close",
        "issue": "SCHEMA.md assumes the tape runs to the session end. For 273 members the last "
                 "print is earlier (halts/no-print), so close(session_end) and the remaining "
                 "session are unobservable and a terminal liquidation there is not executable.",
        "resolution": "member-level `path_complete_to_session_end` / `terminal_censored` flags; "
                      "for a censored member every terminal-dependent outcome is null "
                      "(v_hold_flat, v_giveback_*, giveback_fired_*, "
                      "giveback_condition_after_forced_flat_*, final_high_flag, remaining_run, "
                      "cost_of_waiting, bars_to_peak, peak_et_after_t, tail_class_*, and the four "
                      "session_peak_*/session_close_* constants). State columns are unaffected, "
                      "and single-step quantities that a real bar supports (next_open, next_et, "
                      "level_ret, v_sell, bars_to_next_high/dd_before_next_high when the event "
                      "exists inside the tape) stay. Censored members and nulled cells: "
                      "coverage.censor_census.",
    },
    {
        "schema": "`v_giveback_*` execution and the terminal action",
        "issue": "a trigger on the member's final bar has no next bar to execute in, and the "
                 "engine's forced flat is decided on the completed bar session_end-1 and executed "
                 "at the session_end bar's OPEN — so the terminal action precedes any condition "
                 "observed on that bar's close.",
        "resolution": "a give-back exit is the open of the bar AFTER the trigger bar. basket_sim "
                      "runs its forced-flat branch at `t == session_end - 1` BEFORE release "
                      "evaluation and skips the ticket, so no release rule is evaluated on a bar "
                      "with `et >= session_end - 1`: a first condition there is non-firing "
                      "(`giveback_fired_g` false, `v_giveback_g` = `v_forced_flat`, diagnostic "
                      "`giveback_condition_after_forced_flat_g`). A first condition at "
                      "`et <= session_end - 2` IS executable, and its exit is the open of the next "
                      "printed bar (which may be the session_end bar itself when the tape gaps). "
                      "Values are null only for terminal-censored tapes and on the member's own "
                      "last tracked row.",
    },
    {
        "schema": "`member_last_et` (producer addition)",
        "issue": "it reveals in advance whether/when the member stops printing — future "
                 "metadata, previously registered as a `key`.",
        "resolution": "renamed `future_member_last_et`, family `future_meta`, listed in "
                      "coverage.future_only_columns and covered by the `future_` leakage prefix; "
                      "check (h) asserts no future-only column is classified as causal state.",
    },
    {
        "schema": "`session_end` | ET of the last bar used for this day",
        "issue": "ambiguous between the day-level close (959/779) and the member's own last bar "
                 "(a halted name may stop printing before the close)",
        "resolution": "session_end is the day-level close from phase2_session_calendar.json, "
                      "repeated on every row; the member's actual tape end is the future-only "
                      "producer addition future_member_last_et, and terminal_censored marks the "
                      "members whose tape stops early (enumerated in coverage.censor_census).",
    },
    {
        "schema": "`ret_from_prevclose`, `ret_from_open0930`",
        "issue": "no definition of the reference prices; bars are per-day only",
        "resolution": "the anatomy name fields `prev_close` and `open0930` are used (canonical "
                      "day constants; open0930 equals the 09:30 bar open). Both are repeated per "
                      "row as producer additions so the reference is auditable.",
    },
    {
        "schema": "`new_high_count_5/15/30` (bars in the last 5/15/30 minutes that set a new high)",
        "issue": "clock minutes vs bar count, and whether the bar at t counts",
        "resolution": "clock minutes: bars with et in (t-k, t] that set a new running high since "
                      "the fill (strictly above the running high). The fill bar counts as setting "
                      "the running high.",
    },
    {
        "schema": "`v_giveback_5/10/15/20` ('the running high as of that bar')",
        "issue": "whether the re-evaluated running high starts at t or from the fill",
        "resolution": "the running high is the same object as the state column: max high from the "
                      "fill through the bar being tested. Exit is the open of the bar after the "
                      "first close <= running_high * (1 - g/100). When no condition appears before "
                      "the engine's terminal decision, the continuation is the engine's forced flat: "
                      "`future_forced_flat_px / next_open - 1` (the session_end bar's open), and "
                      "`giveback_fired_g` records whether a give-back exit actually fired.",
    },
    {
        "schema": "`v_giveback_*` trigger equality",
        "issue": "the threshold is a computed product (running_high * (1 - g/100)), so a close "
                 "exactly g% below the running high can fall on either side of the comparison "
                 "because of binary rounding",
        "resolution": "a drop of exactly g% is a hit: the comparison carries a 1e-9 relative "
                       "tolerance on the running high (far below any economically meaningful "
                       "difference; the alternative is a float artifact deciding the exit).",
    },
    {
        "schema": "`v_sell` | 0 by construction",
        "issue": "interacts with check (e): every outcome column null iff next_open is null",
        "resolution": "v_sell is 0.0 whenever next_open exists and null on the member's last bar, "
                      "so the null rule holds literally.",
    },
    {
        "schema": "`bars_to_next_high`, `dd_before_next_high` ('null if none')",
        "issue": "these are the only outcome columns defined null when an event never occurs",
        "resolution": "kept as null-with-event semantics; the self-test enforces nullness whenever "
                      "next_open is null for every outcome column, and enforces non-null only for "
                      "the event-defined columns other than these two.",
    },
    {
        "schema": "`candidate_count_t` / `ret_percentile_candidates` ('the day's A_pm snapshot')",
        "issue": "stated for both families although B600 entries come from the `B` T=600 snapshot",
        "resolution": "implemented literally: the cross-section universe is the day's A_pm "
                      "snapshot for A_pm and B600 rows alike. `peer_*` use the member's own family.",
    },
    {
        "schema": "`ret_percentile_candidates` (percentile)",
        "issue": "percentile convention unspecified",
        "resolution": "share of the universe names with a bar at t and a defined 09:30 return whose "
                      "return is <= the member's return, in [0, 1].",
    },
    {
        "schema": "`peer_new_high_5` ('how many of those others set a new high')",
        "issue": "count of peers vs count of new-high bars",
        "resolution": "count of distinct peer members with at least one new-high bar in (t-5, t].",
    },
    {
        "schema": "`bars_below_entry_episode`, `episode_low`, `bars_since_episode_low`",
        "issue": "what 'the most recent episode' means when the member is currently below entry",
        "resolution": "episode = maximal run of completed closes < entry; while the run is open its "
                      "own running low is used, otherwise the low of the last closed run. "
                      "bars_since_episode_low counts bars from the bar that set that low.",
    },
    {
        "schema": "`range_expansion`, `up_close_streak`, `ret_1/3/5`, `accel_1_5`",
        "issue": "whether 'prior bars' may reach back before the fill",
        "resolution": "yes: they use the member's bars with et <= t, including bars before the fill "
                      "(those bars are known at t, so causality holds); null when the history does "
                      "not exist. The attention columns follow their 'since entry' wording and are "
                      "clamped at the fill bar.",
    },
    {
        "schema": "`volume_vs_own_median` ('bar volume / median volume since entry')",
        "issue": "median window and whether the value is a ratio or a return",
        "resolution": "ratio; denominator = median of bar volumes from the fill through t "
                      "inclusive; null until 5 prior bars exist since the fill.",
    },
    {
        "schema": "`volume_accel` (mean volume of the last 5 bars / mean of the prior 5 - 1)",
        "issue": "positions with fewer than 10 bars since the fill",
        "resolution": "null until 10 bars since the fill exist; both windows are positional and "
                      "clamped at the fill.",
    },
    {
        "schema": "`bars_since_gap`, `bars_since_episode_low`",
        "issue": "value before the first event",
        "resolution": "null (no fabrication), not 0.",
    },
    {
        "schema": "`bars_to_peak`, `bars_to_next_high`, `session_peak_bars_from_entry`",
        "issue": "bar-counting convention",
        "resolution": "index differences: the bar immediately after t is 1; the fill bar is 0. "
                      "Ties on the maximum high resolve to the first (earliest) bar.",
    },
    {
        "schema": "ticket-level constants (`session_peak_*`, `session_close_ret_from_entry`)",
        "issue": "they are session-wide, i.e. look-ahead relative to a row at t < peak",
        "resolution": "kept as documented ticket constants for the fall analysis; they are not "
                      "state and must not be used as causal features. They are non-null on every "
                      "row including the member's last bar (they are not outcome columns).",
    },
    {
        "schema": "`dollar_volume`",
        "issue": "bar dollar volume has no exact definition from OHLCV bars",
        "resolution": "bar close * bar volume.",
    },
]


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def block_of(day: str) -> str:
    return "block1" if day <= BLOCK1_LAST_DAY else "block2"


def month_of(day: str) -> str:
    return day[:7]


def nan_div(num: np.ndarray, den: np.ndarray, zero_null: bool = True) -> np.ndarray:
    """Elementwise divide that maps division by zero (and non-finite) to NaN."""
    num = np.asarray(num, dtype=np.float64)
    den = np.asarray(den, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = num / den
    bad = ~np.isfinite(out)
    if zero_null:
        out = np.where(bad, np.nan, out)
    return out


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            buf = fh.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def atomic_write_json(path: Path, obj) -> None:
    atomic_write_bytes(path, json.dumps(obj, indent=1, sort_keys=True, default=str).encode())


def series(name: str, arr: np.ndarray, dtype) -> pl.Series:
    """Build a polars Series where NaN means null (never a fabricated value)."""
    arr = np.asarray(arr)
    if dtype == pl.Utf8:
        return pl.Series(name, arr.astype(object))
    if dtype == pl.Float64:
        return pl.Series(name, arr.astype(np.float64), nan_to_null=True)
    if np.issubdtype(arr.dtype, np.floating):
        return pl.Series(name, arr.astype(np.float64), nan_to_null=True).cast(dtype)
    return pl.Series(name, arr).cast(dtype)


def frame_from_columns(cols: dict[str, np.ndarray], n: int) -> pl.DataFrame:
    data = {}
    for name, dtype in COLUMN_DTYPES.items():
        arr = cols.get(name)
        if arr is None:
            arr = np.full(n, np.nan) if dtype != pl.Utf8 else np.array([""] * n, dtype=object)
        data[name] = series(name, arr, dtype)
    return pl.DataFrame(data, schema={n_: COLUMN_DTYPES[n_] for n_ in COLUMN_DTYPES})


# --------------------------------------------------------------------------- #
# population
# --------------------------------------------------------------------------- #


def family_members(rec: dict, day: str, pop: str, T: int, bars, session_end: int,
                   census: dict) -> list[dict]:
    """Canonical top-3 filled members of one family (engine entry semantics).

    Mirrors ``basket_sim.simulate_day``: iterate ``snap["names"][:3]`` in rank
    order, dedupe tickers, skip a missing/blocked fill.  A member whose tape
    cannot carry the fill is reported in ``census`` instead of being dropped
    silently.
    """
    snap = sim.snapshot_of(rec, pop, T)
    if snap is None:
        census["snapshot_missing"].append({"day": day, "pop": pop, "T": T})
        return []
    names = snap["names"][:3]
    if len(names) < 3:
        census["snapshot_short"].append({"day": day, "pop": pop, "T": T, "n_names": len(names)})
    out: list[dict] = []
    seen: set[str] = set()
    for rank_index, nm in enumerate(names):
        ticker = nm["ticker"]
        if ticker in seen:
            census["dup_ticker_in_top3"].append({"day": day, "pop": pop, "ticker": ticker})
            continue
        seen.add(ticker)
        fl = nm.get("fill")
        if not fl:
            census["no_fill"].append({"day": day, "pop": pop, "ticker": ticker})
            continue
        if fl.get("blocked"):
            census["blocked"].append({"day": day, "pop": pop, "ticker": ticker,
                                      "et": int(fl["et"])})
            continue
        tkb = bars.ticker(ticker)
        if tkb is None:
            census["no_bars"].append({"day": day, "pop": pop, "ticker": ticker})
            continue
        ets = tkb["et"]
        entry_et = int(fl["et"])
        fill_idx = int(np.searchsorted(ets, entry_et, side="left"))
        if fill_idx >= len(ets) or int(ets[fill_idx]) != entry_et:
            # engine still enters (its pending executes at the first later bar);
            # the anatomy fill bar itself is absent, so the panel starts at the
            # first bar at or after the fill et and reports the member here.
            census["no_fill_bar"].append({
                "day": day, "pop": pop, "ticker": ticker, "entry_et": entry_et,
                "first_bar_et": int(ets[fill_idx]) if fill_idx < len(ets) else None,
            })
            if fill_idx >= len(ets):
                census["no_bar_after_fill_et"].append(
                    {"day": day, "pop": pop, "ticker": ticker, "entry_et": entry_et})
                continue
        out.append({
            "day": day,
            "rank": int(nm.get("rank", rank_index + 1)),
            "ticker": ticker,
            "entry_et": entry_et,
            "entry_px": float(fl["px"]),
            "fill_idx": fill_idx,
            "last_idx": len(ets) - 1,
            "prev_close": nm.get("prev_close"),
            "open0930": nm.get("open0930"),
            "session_end": session_end,
        })
    return out


def cross_universe(rec: dict, bars, session_end: int) -> tuple[np.ndarray, np.ndarray]:
    """The day's candidate universe (A_pm snapshot): bar presence and 09:30 return.

    Returns ``(has_bar, ret0930)`` matrices of shape (n_universe, n_minutes),
    indexed by ``et - FIRST_ET``; NaN / False where the name has no bar.
    """
    snap = sim.snapshot_of(rec, CROSS_POP, CROSS_T)
    width = session_end - FIRST_ET + 1
    names: list[str] = []
    if snap is not None:
        for nm in snap["names"]:
            t = nm["ticker"]
            if t not in names:
                names.append(t)
    has_bar = np.zeros((len(names), width), dtype=bool)
    ret = np.full((len(names), width), np.nan)
    closes = {}
    for i, t in enumerate(names):
        tkb = bars.ticker(t)
        if tkb is None:
            continue
        ets = tkb["et"]
        keep = (ets >= FIRST_ET) & (ets <= session_end)
        cols = ets[keep] - FIRST_ET
        has_bar[i, cols] = True
        closes[t] = tkb["close"][keep]
    for i, t in enumerate(names):
        tkb = bars.ticker(t)
        if tkb is None or t not in closes:
            continue
        ref = None
        for nm in snap["names"]:
            if nm["ticker"] == t:
                ref = nm.get("open0930")
                break
        if ref is None or not np.isfinite(float(ref)) or float(ref) <= 0:
            continue
        ets = tkb["et"]
        keep = (ets >= FIRST_ET) & (ets <= session_end)
        cols = ets[keep] - FIRST_ET
        ret[i, cols] = closes[t] / float(ref) - 1.0
    return has_bar, ret


def peer_matrices(members: list[dict], bars, session_end: int
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-family peer matrices over the day's minutes.

    ``has_bar``/``ret0930`` mirror :func:`cross_universe` but restricted to the
    family's own members; ``nh_prefix`` is the cumulative count of each peer's
    new-high bars (its own running high since its own fill).
    """
    width = session_end - FIRST_ET + 1
    k = len(members)
    has_bar = np.zeros((k, width), dtype=bool)
    ret = np.full((k, width), np.nan)
    nh_prefix = np.zeros((k, width + 1), dtype=np.int32)
    for i, mb in enumerate(members):
        tkb = bars.ticker(mb["ticker"])
        if tkb is None:
            continue
        ets = tkb["et"][mb["fill_idx"]:mb["last_idx"] + 1]
        hi = tkb["high"][mb["fill_idx"]:mb["last_idx"] + 1]
        cl = tkb["close"][mb["fill_idx"]:mb["last_idx"] + 1]
        cols = ets - FIRST_ET
        has_bar[i, cols] = True
        ref = mb["open0930"]
        if ref is not None and np.isfinite(float(ref)) and float(ref) > 0:
            ret[i, cols] = cl / float(ref) - 1.0
        rh = np.maximum.accumulate(hi)
        flags = np.r_[True, hi[1:] > rh[:-1]].astype(np.int32)
        by_col = np.zeros(width, dtype=np.int32)
        by_col[cols] = flags
        # nh_prefix[i, c + 1] = new-high bars of peer i at et column <= c
        nh_prefix[i, 1:] = np.cumsum(by_col)
    return has_bar, ret, nh_prefix


# --------------------------------------------------------------------------- #
# per-member rows
# --------------------------------------------------------------------------- #


def member_columns(mb: dict, bars, ctx: dict) -> dict[str, np.ndarray]:
    """Every panel column for one member, as numpy arrays of length n_rows."""
    tk = bars.ticker(mb["ticker"])
    fill_idx = mb["fill_idx"]
    last = mb["last_idx"]
    entry_px = float(mb["entry_px"])
    idx = np.arange(fill_idx, last + 1)
    m = idx.size

    et = tk["et"][idx].astype(np.int64)
    op_w = tk["open"][idx].astype(np.float64)
    hi_w = tk["high"][idx].astype(np.float64)
    lo_w = tk["low"][idx].astype(np.float64)
    cl_w = tk["close"][idx].astype(np.float64)
    vo_w = tk["volume"][idx].astype(np.float64)

    ar = np.arange(m, dtype=np.int64)

    # --- own path -------------------------------------------------------- #
    ret_from_fill = cl_w / entry_px - 1.0
    prev_close = mb["prev_close"]
    open0930 = mb["open0930"]
    ret_from_prevclose = (nan_div(cl_w, np.full(m, float(prev_close))) - 1.0
                          if prev_close not in (None, 0) else np.full(m, np.nan))
    ret_from_open0930 = (nan_div(cl_w, np.full(m, float(open0930))) - 1.0
                         if open0930 not in (None, 0) else np.full(m, np.nan))
    running_high = np.maximum.accumulate(hi_w)
    mfe_so_far = running_high / entry_px - 1.0
    mae_so_far = np.minimum.accumulate(lo_w) / entry_px - 1.0
    dist_from_running_high = cl_w / running_high - 1.0
    mfe_surrendered = np.where(
        mfe_so_far > 0.0, (mfe_so_far - ret_from_fill) / np.where(mfe_so_far > 0.0, mfe_so_far, 1.0), 0.0)

    # --- new highs ------------------------------------------------------- #
    is_new_high = np.r_[True, hi_w[1:] > running_high[:-1]]
    last_nh = np.maximum.accumulate(np.where(is_new_high, ar, -1))
    bars_since_new_high = (ar - last_nh).astype(np.int64)
    cs_nh = np.r_[0, np.cumsum(is_new_high.astype(np.int64))]
    new_high_counts = {}
    for k in (5, 15, 30):
        lb = np.searchsorted(et, et - k, side="right")
        new_high_counts[k] = (cs_nh[ar + 1] - cs_nh[lb]).astype(np.int64)

    # --- episode --------------------------------------------------------- #
    below = cl_w < entry_px
    reset = np.where(~below, ar, -1)
    last_reset = np.maximum.accumulate(reset)
    bars_below = (ar - last_reset).astype(np.int64)
    episode_low = np.full(m, np.nan)
    bars_since_episode_low = np.full(m, np.nan)
    run_open = False
    run_best = None      # (low, window index) of the episode in progress
    prev_best = None     # (low, window index) of the most recent finished episode
    for j in range(m):
        if below[j]:
            if not run_open:
                run_open = True
                run_best = (lo_w[j], j)
            elif lo_w[j] < run_best[0]:
                run_best = (lo_w[j], j)
            episode_low[j] = run_best[0]
            bars_since_episode_low[j] = j - run_best[1]
        else:
            if run_open:
                run_open = False
                prev_best = run_best
                run_best = None
            if prev_best is not None:
                episode_low[j] = prev_best[0]
                bars_since_episode_low[j] = j - prev_best[1]
    reclaim = np.r_[False, below[:-1]] & ~below
    reclaim_count = np.cumsum(reclaim.astype(np.int64))
    below_positions = np.flatnonzero(below)
    fail_increment = np.zeros(m, dtype=np.int64)
    for p in np.flatnonzero(reclaim):
        nxt = below_positions[below_positions > p]
        if nxt.size:
            fail_increment[nxt[0]] += 1
    failed_reclaim_count = np.cumsum(fail_increment)

    # --- dynamics -------------------------------------------------------- #
    def k_back(k: int) -> np.ndarray:
        src = idx - k
        out = np.full(m, np.nan)
        ok = src >= 0
        if ok.any():
            out[ok] = cl_w[ok] / tk["close"][src[ok]] - 1.0
        return out

    ret_1, ret_3, ret_5 = k_back(1), k_back(3), k_back(5)
    accel_1_5 = ret_1 - ret_5 / 5.0

    up = np.r_[False, tk["close"][1:] > tk["close"][:-1]]
    allpos = np.arange(tk["et"].size, dtype=np.int64)
    last_down = np.maximum.accumulate(np.where(~up, allpos, -1))
    up_close_streak = (allpos - last_down)[idx].astype(np.int64)

    rng_all = (tk["high"] - tk["low"]).astype(np.float64)
    cs_rng = np.r_[0.0, np.cumsum(rng_all)]
    valid5 = idx >= 5
    mean5 = np.where(valid5, (cs_rng[idx] - cs_rng[np.maximum(idx - 5, 0)]) / 5.0, np.nan)
    range_expansion = np.where(valid5 & (mean5 > 0), nan_div(rng_all[idx], mean5) - 1.0, np.nan)
    bar_range_pct = nan_div(hi_w - lo_w, cl_w)

    # --- attention ------------------------------------------------------- #
    volume_vs_own_median = np.full(m, np.nan)
    sorted_vol: list[float] = []
    for i in range(m):
        bisect.insort(sorted_vol, float(vo_w[i]))
        if i >= 5:
            k = len(sorted_vol)
            med = sorted_vol[k // 2] if k % 2 else 0.5 * (sorted_vol[k // 2 - 1] + sorted_vol[k // 2])
            if med > 0:
                volume_vs_own_median[i] = vo_w[i] / med
    volume_accel = np.full(m, np.nan)
    for i in range(m):
        if i >= 9:
            recent = vo_w[i - 4:i + 1].mean()
            prior = vo_w[i - 9:i - 4].mean()
            if prior > 0:
                volume_accel[i] = recent / prior - 1.0
    gap_flags = np.zeros(m, dtype=bool)
    if m > 1:
        gap_flags[1:] = (et[1:] - et[:-1]) > 1
    gap_count_so_far = np.cumsum(gap_flags.astype(np.int64))
    last_gap = np.maximum.accumulate(np.where(gap_flags, ar, -1))
    bars_since_gap = (ar - last_gap).astype(np.float64)
    bars_since_gap[last_gap < 0] = np.nan

    # --- cross-section --------------------------------------------------- #
    cols_et = et - FIRST_ET
    univ_has, univ_ret = ctx["univ_has"], ctx["univ_ret"]
    candidate_count_t = univ_has[:, cols_et].sum(axis=0).astype(np.int64)
    mat = univ_ret[:, cols_et]
    valid = ~np.isnan(mat)
    n_valid = valid.sum(axis=0)
    self_defined = ~np.isnan(ret_from_open0930)
    with np.errstate(invalid="ignore"):
        le = (mat <= ret_from_open0930[None, :]) & valid
    ret_percentile_candidates = np.where(
        (n_valid > 0) & self_defined, le.sum(axis=0) / np.maximum(n_valid, 1), np.nan).astype(np.float64)
    peer_has, peer_ret, peer_nh = ctx["peer_has"], ctx["peer_ret"], ctx["peer_nh"]
    others = np.array([i for i in range(peer_has.shape[0]) if i != ctx["self_index"]], dtype=np.int64)
    peer_ret_median = np.full(m, np.nan)
    peer_new_high_5 = np.zeros(m, dtype=np.int64)
    if others.size:
        pmat = peer_ret[others][:, cols_et]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            peer_ret_median = np.nanmedian(pmat, axis=0)
        pmed_valid = (~np.isnan(pmat)).sum(axis=0) == 0
        peer_ret_median = np.where(pmed_valid, np.nan, peer_ret_median)
        # how many of the *other* members set a new high with et in (t - 5, t]
        lo_prefix_idx = np.maximum(cols_et - 4, 0)
        pnh = peer_nh[others]
        delta = pnh[:, cols_et + 1] - pnh[:, lo_prefix_idx]
        peer_new_high_5 = (delta > 0).sum(axis=0).astype(np.int64)

    # --- censoring: a tape that stops before the session close ------------ #
    # The member has no print at the session's last minute, so nothing that
    # needs the remaining session (or an executable terminal liquidation) can be
    # valued.  State columns stay; every terminal-dependent outcome is null.
    complete = bool(int(et[-1]) == int(mb["session_end"]))
    last_et = int(et[-1])
    # The engine's forced flat is decided on the completed bar session_end-1 and
    # executes at the open of the session_end bar (basket_sim: `t == session_end
    # - 1` schedules, the pending executes on the next bar at its open).  That
    # price only exists when the member has a session_end print.
    forced_flat_px = float(op_w[-1]) if complete else None

    # --- outcomes -------------------------------------------------------- #
    nan1 = np.full(m, np.nan)
    next_open = nan1.copy()
    next_et = nan1.copy()
    v_hold_flat = nan1.copy()
    v_forced_flat = nan1.copy()
    level_ret = nan1.copy()
    final_high_flag = nan1.copy()
    remaining_run = nan1.copy()
    cost_of_waiting = nan1.copy()
    bars_to_next_high = nan1.copy()
    dd_before_next_high = nan1.copy()
    bars_to_peak = nan1.copy()
    peak_et_after_t = nan1.copy()
    tail_class = {lv: nan1.copy() for lv in TAIL_LEVELS}
    fired = {lv: nan1.copy() for lv in GIVEBACK_LEVELS}
    unexecutable = {lv: nan1.copy() for lv in GIVEBACK_LEVELS}
    v_giveback = {lv: nan1.copy() for lv in GIVEBACK_LEVELS}

    if m > 1:
        nxt = op_w[1:]
        next_open[:-1] = nxt
        next_et[:-1] = et[1:]
        level_ret[:-1] = nxt / entry_px - 1.0
        close_end = cl_w[-1]
        # v_hold_flat is the labelled CLOSE baseline (not an execution price).
        v_hold_flat[:-1] = close_end / nxt - 1.0
        # v_forced_flat is the engine's executable terminal continuation: exit at
        # the forced-flat execution price (session_end bar open).
        if forced_flat_px is not None:
            v_forced_flat[:-1] = forced_flat_px / nxt - 1.0
        suf_hi = np.maximum.accumulate(hi_w[::-1])[::-1]
        suf_lo = np.minimum.accumulate(lo_w[::-1])[::-1]
        fut_max = suf_hi[1:]
        fut_min = suf_lo[1:]
        remaining_run[:-1] = fut_max / nxt - 1.0
        cost_of_waiting[:-1] = fut_min / nxt - 1.0
        final_high_flag[:-1] = (fut_max <= running_high[:-1]).astype(np.float64)
        for lv in TAIL_LEVELS:
            tail_class[lv][:-1] = ((fut_max / nxt - 1.0) >= lv / 100.0).astype(np.float64)
        # bars/et of the session's max high after t (first occurrence on ties)
        peak_idx = np.empty(m, dtype=np.int64)
        peak_idx[m - 1] = -1
        best = m - 1
        for i in range(m - 2, -1, -1):
            if hi_w[i + 1] >= hi_w[best]:
                best = i + 1
            peak_idx[i] = best
        bars_to_peak[:-1] = (peak_idx[:-1] - ar[:-1]).astype(np.float64)
        peak_et_after_t[:-1] = et[peak_idx[:-1]]
        for j in range(m - 1):
            rh = running_high[j]
            hit = np.flatnonzero(hi_w[j + 1:] > rh)
            if hit.size:
                i_hit = j + 1 + int(hit[0])
                bars_to_next_high[j] = i_hit - j
                dd_before_next_high[j] = lo_w[j + 1:i_hit + 1].min() / op_w[j + 1] - 1.0
            for lv in GIVEBACK_LEVELS:
                px, did, after_flat = _giveback_exit(cl_w, hi_w, running_high, op_w, et, j, lv,
                                                     forced_flat_px, mb["session_end"])
                fired[lv][j] = 1.0 if did else 0.0
                unexecutable[lv][j] = 1.0 if after_flat else 0.0
                v_giveback[lv][j] = (px / op_w[j + 1] - 1.0) if px is not None else np.nan
    v_sell = np.where(np.isnan(next_open), np.nan, 0.0)

    # --- ticket constants (whole member window, deliberately look-ahead) -- #
    peak_all = int(np.argmax(hi_w))
    session_peak_et = np.full(m, float(et[peak_all]))
    session_peak_ret_from_entry = np.full(m, hi_w[peak_all] / entry_px - 1.0)
    session_peak_bars_from_entry = np.full(m, float(peak_all))
    session_close_ret_from_entry = np.full(m, cl_w[-1] / entry_px - 1.0)

    cols: dict[str, np.ndarray] = {
        "sleeve_day": np.array([mb["day"]] * m, dtype=object),
        "month": np.array([month_of(mb["day"])] * m, dtype=object),
        "block": np.array([block_of(mb["day"])] * m, dtype=object),
        "family": np.array([mb["family"]] * m, dtype=object),
        "ticker": np.array([mb["ticker"]] * m, dtype=object),
        "entry_rank": np.full(m, mb["rank"], dtype=np.int64),
        "entry_et": np.full(m, mb["entry_et"], dtype=np.int64),
        "entry_px": np.full(m, entry_px, dtype=np.float64),
        "et": et.astype(np.int64),
        "bar_index": ar,
        "bars_since_entry": ar,
        "session_end": np.full(m, mb["session_end"], dtype=np.int64),
        "bar_open": op_w,
        "bar_high": hi_w,
        "bar_low": lo_w,
        "bar_close": cl_w,
        "volume": vo_w,
        "dollar_volume": vo_w * cl_w,
        "prev_close": np.full(m, float(prev_close) if prev_close is not None else np.nan),
        "open0930": np.full(m, float(open0930) if open0930 is not None else np.nan),
        "ret_from_fill": ret_from_fill,
        "ret_from_prevclose": ret_from_prevclose,
        "ret_from_open0930": ret_from_open0930,
        "mfe_so_far": mfe_so_far,
        "mae_so_far": mae_so_far,
        "running_high": running_high,
        "dist_from_running_high": dist_from_running_high,
        "mfe_surrendered": mfe_surrendered,
        "bars_below_entry_episode": bars_below,
        "episode_low": episode_low,
        "bars_since_episode_low": bars_since_episode_low,
        "reclaim_count": reclaim_count,
        "failed_reclaim_count": failed_reclaim_count,
        "bars_since_new_high": bars_since_new_high,
        "new_high_count_5": new_high_counts[5],
        "new_high_count_15": new_high_counts[15],
        "new_high_count_30": new_high_counts[30],
        "ret_1": ret_1,
        "ret_3": ret_3,
        "ret_5": ret_5,
        "accel_1_5": accel_1_5,
        "up_close_streak": up_close_streak,
        "range_expansion": range_expansion,
        "bar_range_pct": bar_range_pct,
        "volume_vs_own_median": volume_vs_own_median,
        "volume_accel": volume_accel,
        "gap_count_so_far": gap_count_so_far,
        "bars_since_gap": bars_since_gap,
        "candidate_count_t": candidate_count_t,
        "ret_percentile_candidates": ret_percentile_candidates,
        "peer_ret_median": peer_ret_median,
        "peer_new_high_5": peer_new_high_5,
        "next_open": next_open,
        "next_et": next_et,
        "v_hold_flat": v_hold_flat,
        "v_forced_flat": v_forced_flat,
        "giveback_fired_5": fired[5],
        "giveback_fired_10": fired[10],
        "giveback_fired_15": fired[15],
        "giveback_fired_20": fired[20],
        "giveback_condition_after_forced_flat_5": unexecutable[5],
        "giveback_condition_after_forced_flat_10": unexecutable[10],
        "giveback_condition_after_forced_flat_15": unexecutable[15],
        "giveback_condition_after_forced_flat_20": unexecutable[20],
        "v_giveback_5": v_giveback[5],
        "v_giveback_10": v_giveback[10],
        "v_giveback_15": v_giveback[15],
        "v_giveback_20": v_giveback[20],
        "v_sell": v_sell,
        "level_ret": level_ret,
        "final_high_flag": final_high_flag,
        "remaining_run": remaining_run,
        "cost_of_waiting": cost_of_waiting,
        "bars_to_next_high": bars_to_next_high,
        "dd_before_next_high": dd_before_next_high,
        "bars_to_peak": bars_to_peak,
        "peak_et_after_t": peak_et_after_t,
        "tail_class_50": tail_class[50],
        "tail_class_100": tail_class[100],
        "tail_class_300": tail_class[300],
        "session_peak_et": session_peak_et,
        "session_peak_ret_from_entry": session_peak_ret_from_entry,
        "session_peak_bars_from_entry": session_peak_bars_from_entry,
        "session_close_ret_from_entry": session_close_ret_from_entry,
        "path_complete_to_session_end": np.full(m, 1.0 if complete else 0.0),
        "terminal_censored": np.full(m, 0.0 if complete else 1.0),
        "future_member_last_et": np.full(m, float(last_et)),
        "future_forced_flat_px": np.full(m, forced_flat_px if forced_flat_px is not None
                                         else np.nan),
    }
    if not complete:
        for name in CENSORED_OUTCOME_COLUMNS:
            arr = cols[name]
            arr[:] = np.nan
    return cols


def _giveback_exit(cl_w: np.ndarray, hi_w: np.ndarray, rh_w: np.ndarray,
                   op_w: np.ndarray, et_w: np.ndarray, j: int, level: int,
                   forced_flat_px: float | None,
                   session_end: int) -> tuple[float | None, bool, bool]:
    """Exit of the give-back continuation from row ``j``.

    The first completed bar whose close is ``g%`` below the running high
    *as of that bar* triggers the exit at the **open of the next bar**; a close
    exactly ``g%`` below is a hit (1e-9 relative tolerance, see AMBIGUITIES).

    Precedence is by **clock ET**, mirroring ``basket_sim``: the engine's loop
    runs the forced-flat branch (`if t == session_end - 1`) *before* release
    evaluation and skips the ticket, so no release rule is ever evaluated on a
    bar with ``et >= session_end - 1``.

    Returns ``(px, fired, condition_after_forced_flat)``:
      * first trigger on a bar with ``et <= session_end - 2`` -> (open of the
        next printed bar, True, False) — that bar may be the ``session_end`` bar
        itself when the tape gaps, which is still a genuine firing;
      * no trigger at all -> (the engine's forced-flat execution price — the open
        of the session_end bar — or None when the member has no session_end
        print, False, False);
      * first trigger on a bar with ``et >= session_end - 1`` -> (forced-flat
        price, False, True): the engine had already gone flat, so the rule cannot
        preempt it and the close condition is only the
        `giveback_condition_after_forced_flat_g` diagnostic.
    """
    g = level / 100.0
    c = cl_w[j + 1:]
    h = np.maximum.accumulate(hi_w[j + 1:])
    run = np.maximum(h, rh_w[j])
    hit = np.flatnonzero(c <= run * (1.0 - g) + GIVEBACK_TOL_REL * run)
    if hit.size == 0:
        return forced_flat_px, False, False
    gi = j + 1 + int(hit[0])
    if int(et_w[gi]) >= int(session_end) - 1:
        # the forced-flat decision on that bar precedes release evaluation
        return forced_flat_px, False, True
    if gi < cl_w.size - 1:
        return float(op_w[gi + 1]), True, False
    # unreachable for a complete tape: a bar with et <= session_end-2 has a
    # later printed bar because the tape ends at session_end
    return forced_flat_px, False, True


# --------------------------------------------------------------------------- #
# day / month builders
# --------------------------------------------------------------------------- #


def build_day(day: str, session_end: int, census: dict) -> pl.DataFrame:
    rec = sim.load_anatomy(day)
    bars = sim.load_bars(day, session_end)
    univ_has, univ_ret = cross_universe(rec, bars, session_end)
    frames = []
    for family, pop, T in FAMILIES:
        members = family_members(rec, day, pop, T, bars, session_end, census)
        for mb in members:
            mb["family"] = family
        if not members:
            continue
        peer_has, peer_ret, peer_nh = peer_matrices(members, bars, session_end)
        for i, mb in enumerate(members):
            ctx = {
                "univ_has": univ_has,
                "univ_ret": univ_ret,
                "peer_has": peer_has,
                "peer_ret": peer_ret,
                "peer_nh": peer_nh,
                "self_index": i,
            }
            cols = member_columns(mb, bars, ctx)
            n = len(cols["et"])
            frames.append(frame_from_columns(cols, n))
    if not frames:
        return pl.DataFrame(schema={n_: d for n_, d in COLUMN_DTYPES.items()})
    return pl.concat(frames, how="vertical")


def smoke_days(all_days: list[str]) -> list[str]:
    """10 deterministic days per block: evenly spaced, endpoints included."""
    b1 = [d for d in all_days if block_of(d) == "block1"]
    b2 = [d for d in all_days if block_of(d) == "block2"]
    out = []
    for block in (b1, b2):
        n = len(block)
        if n == 0:
            continue
        if n <= SMOKE_PER_BLOCK:
            picked = block
        else:
            pos = sorted({round(k * (n - 1) / (SMOKE_PER_BLOCK - 1)) for k in range(SMOKE_PER_BLOCK)})
            picked = [block[p] for p in pos]
        out.extend(picked)
    return out


def load_progress(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def parts_dir(out: Path) -> Path:
    return out / "parts"


def part_path(out: Path, month: str) -> Path:
    return parts_dir(out) / f"month={month}.parquet"


def build_parts(days: list[str], out: Path, force: bool = False,
                verbose: bool = True) -> dict:
    """Build one parquet part per month, resumable, updating `_progress.json`."""
    sem = sim.session_end_map()
    progress_path = out / "_progress.json"
    progress = load_progress(progress_path)
    months: dict[str, dict] = progress.get("months", {}) if progress.get("schema_version") == SCHEMA_VERSION else {}
    if force:
        months = {}
        if parts_dir(out).exists():
            shutil.rmtree(parts_dir(out))
    by_month: dict[str, list[str]] = {}
    for d in days:
        by_month.setdefault(month_of(d), []).append(d)
    parts_dir(out).mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    for month in sorted(by_month):
        wanted = by_month[month]
        rec = months.get(month)
        p = part_path(out, month)
        if (rec and rec.get("days") == wanted and p.exists()
                and rec.get("rows") is not None):
            if verbose:
                print(f"  {month}: resume (rows={rec['rows']})", flush=True)
            continue
        census: dict[str, list] = _empty_census()
        frames = []
        for day in wanted:
            if day not in sem:
                census["missing_calendar"].append({"day": day})
                continue
            frames.append(build_day(day, sem[day], census))
        df = pl.concat(frames, how="vertical") if frames else pl.DataFrame(
            schema={n_: d for n_, d in COLUMN_DTYPES.items()})
        tmp = p.with_suffix(".parquet.tmp")
        df.write_parquet(tmp, compression="zstd")
        tmp.replace(p)
        rows = df.height
        members = df.select(["sleeve_day", "family", "ticker"]).unique().height if rows else 0
        months[month] = {
            "days": wanted, "rows": int(rows), "members": int(members),
            "sha256": sha256_file(p), "census": _census_counts(census),
            "census_detail": {k: v for k, v in census.items() if v},
        }
        progress = {
            "schema_version": SCHEMA_VERSION,
            "months": months,
            "ancillary_notes": progress.get("ancillary_notes", {}),
        }
        atomic_write_json(progress_path, progress)
        if verbose:
            print(f"  {month}: {len(wanted)} days, {rows} rows, {members} members "
                  f"({time.time() - t0:.0f}s)", flush=True)
    return progress


def write_column_registry(out: Path) -> Path:
    """Physical contract of the panel: column -> dtype + family, for verifiers."""
    path = out / "column_registry.json"
    atomic_write_json(path, {
        "schema_version": SCHEMA_VERSION,
        "schema": "factory/artifacts/basket/phase2/ATLAS/SCHEMA.md",
        "columns": {name: str(dtype) for name, dtype, _fam in COLUMNS},
        "column_order": [name for name, _d, _f in COLUMNS],
        "families": {name: fam for name, _d, fam in COLUMNS},
        "sort_key": ["sleeve_day", "family", "entry_rank", "et"],
    })
    return path


def merge_parts(out: Path) -> Path:
    files = sorted(parts_dir(out).glob("month=*.parquet"))
    if not files:
        raise SystemExit(f"no month parts under {parts_dir(out)}; run build first")
    df = pl.concat([pl.read_parquet(f) for f in files], how="vertical")
    df = df.sort(["sleeve_day", "family", "entry_rank", "et"])
    target = out / "panel.parquet"
    tmp = target.with_suffix(".parquet.tmp")
    df.write_parquet(tmp, compression="zstd")
    tmp.replace(target)
    write_column_registry(out)
    return target


# --------------------------------------------------------------------------- #
# census / coverage
# --------------------------------------------------------------------------- #


CENSUS_KEYS = ("blocked", "no_fill", "no_bars", "no_fill_bar", "no_bar_after_fill_et",
               "snapshot_missing", "snapshot_short", "dup_ticker_in_top3",
               "missing_calendar")


def _empty_census() -> dict[str, list]:
    return {k: [] for k in CENSUS_KEYS}


def _census_counts(census: dict[str, list]) -> dict[str, int]:
    return {k: len(v) for k, v in census.items()}


VERIFIER_SCRIPT = ROOT / "factory/scripts/basket_atlas_verify.py"


def verification_registry() -> dict:
    """Commands and content hashes of the checks that certify this panel."""
    return {
        "producer": {
            "path": "factory/scripts/basket_atlas_panel.py",
            "sha256": sha256_file(ROOT / "factory/scripts/basket_atlas_panel.py"),
            "selftest_command": ("python factory/scripts/basket_atlas_panel.py selftest "
                                 "--panel factory/artifacts/basket/phase2/ATLAS/panel.parquet"),
            "corruption_command": ("python factory/scripts/basket_atlas_panel.py corruption "
                                   "--panel factory/artifacts/basket/phase2/ATLAS/panel.parquet"),
        },
        "independent_verifier": {
            "path": "factory/scripts/basket_atlas_verify.py",
            "sha256": sha256_file(VERIFIER_SCRIPT) if VERIFIER_SCRIPT.exists() else None,
            "command": ("python factory/scripts/basket_atlas_verify.py "
                        "--panel factory/artifacts/basket/phase2/ATLAS/panel.parquet "
                        "--report factory/artifacts/basket/phase2/ATLAS/verify_report.json"),
        },
    }


def censor_census(df: pl.DataFrame) -> dict:
    """Enumerate the censored members and quantify the censored cells."""
    members_all = df.group_by(["sleeve_day", "family", "ticker"]).agg([
        pl.col("terminal_censored").first().alias("terminal_censored"),
        pl.col("path_complete_to_session_end").first().alias("path_complete_to_session_end"),
        pl.col("future_member_last_et").first().alias("derived_last_et"),
        pl.col("session_end").first().alias("session_end"),
        pl.col("entry_et").first().alias("entry_et"),
        pl.col("entry_px").first().alias("entry_px"),
        pl.len().alias("derived_rows"),
    ])
    censored = members_all.filter(pl.col("terminal_censored")).sort(
        ["sleeve_day", "family", "ticker"])
    censored_rows = int(censored["derived_rows"].sum()) if censored.height else 0
    censored_cells = censored_rows * len(CENSORED_OUTCOME_COLUMNS)
    blocked_without_print = censored.filter(pl.col("derived_last_et") < pl.col("entry_et"))
    unexec = {}
    for lv in GIVEBACK_LEVELS:
        unexec[f"giveback_condition_after_forced_flat_{lv}"] = int(
            df.filter(pl.col(f"giveback_condition_after_forced_flat_{lv}") == True).height)  # noqa: E712
    return {
        "censored_members": int(censored.height),
        "censored_rows": censored_rows,
        "censored_rows_share": censored_rows / df.height if df.height else None,
        "censored_cells_nulled": censored_cells,
        "censored_columns": list(CENSORED_OUTCOME_COLUMNS),
        "censored_stopped_before_entry": int(blocked_without_print.height),
        "censored_member_list": censored.select(
            ["sleeve_day", "family", "ticker", "entry_et", "derived_last_et", "session_end",
             "derived_rows"]
        ).to_dicts(),
        "condition_after_forced_flat_rows": unexec,
    }


def tape_shape(df: pl.DataFrame) -> dict:
    """Descriptive shape of the tracked tape (coverage diagnostics)."""
    members = df.group_by(["sleeve_day", "family", "ticker"]).agg([
        pl.col("future_member_last_et").first().alias("derived_last_et"),
        pl.col("session_end").first().alias("session_end"),
        pl.col("entry_et").first().alias("entry_et"),
        pl.col("entry_px").first().alias("entry_px"),
        pl.col("open0930").first().alias("open0930"),
        pl.len().alias("n_bars"),
        pl.col("session_peak_et").first().alias("peak_et"),
        pl.col("session_peak_ret_from_entry").first().alias("peak_ret"),
        pl.col("session_close_ret_from_entry").first().alias("close_ret"),
    ])
    short = members.filter(pl.col("derived_last_et") < pl.col("session_end"))
    entry_hist = (df.filter(pl.col("bar_index") == 0)
                  .group_by(["family", "entry_et"]).len()
                  .sort(["family", "entry_et"])
                  .to_dicts())
    open_match = members.filter(pl.col("entry_et") == FIRST_ET)
    open_agree = (open_match.filter(
        (pl.col("entry_px") - pl.col("open0930")).abs() <= 1e-9).height) if open_match.height else 0
    peak_q = {}
    for fam in FAMILY_KEYS:
        sub = members.filter(pl.col("family") == fam)
        if not sub.height:
            continue
        peak_q[fam] = {
            "peak_et_q25": float(sub["peak_et"].quantile(0.25)),
            "peak_et_median": float(sub["peak_et"].median()),
            "peak_et_q75": float(sub["peak_et"].quantile(0.75)),
            "peak_ret_from_entry_median": float(sub["peak_ret"].median()),
            "close_ret_from_entry_median": float(sub["close_ret"].median()),
            "peak_ret_from_entry_mean": float(sub["peak_ret"].mean()),
            "close_ret_from_entry_mean": float(sub["close_ret"].mean()),
        }
    return {
        "entry_et_histogram": entry_hist,
        "rows_per_member": {
            "min": int(members["n_bars"].min()), "median": float(members["n_bars"].median()),
            "max": int(members["n_bars"].max()),
        },
        "members_without_final_bar": int(short.height),
        "members_without_final_bar_examples": short.select(
            ["sleeve_day", "family", "ticker", "derived_last_et", "session_end"]
        ).head(20).to_dicts(),
        "members_missing_anatomy_constants": {
            "open0930": int(members.filter(pl.col("open0930").is_null()).height),
            "prev_close": int(df.filter(pl.col("prev_close").is_null())
                              .select(["sleeve_day", "family", "ticker"]).unique().height),
            "columns_affected": ["ret_from_open0930", "ret_percentile_candidates",
                                 "ret_from_prevclose"],
        },
        "fill_px_equals_open0930_at_570": {
            "members_with_entry_et_570": int(open_match.height),
            "agreeing": int(open_agree),
        },
        "session_end_distribution": (df.group_by("session_end").len().sort("session_end").to_dicts()),
        "peak_clock_by_family": peak_q,
    }


def coverage_report(panel_path: Path, out: Path, anchors: dict | None = None,
                    c1_check: dict | None = None, progress: dict | None = None,
                    verification: dict | None = None) -> dict:
    df = pl.read_parquet(panel_path)
    anchors = anchors or ANCHORS
    per_family = {}
    for fam in FAMILY_KEYS:
        sub = df.filter(pl.col("family") == fam)
        members = sub.select(["sleeve_day", "ticker"]).unique().height if sub.height else 0
        per_family[fam] = {
            "members": int(members),
            "rows": int(sub.height),
            "days": int(sub["sleeve_day"].n_unique()) if sub.height else 0,
            "anchor_expected_members": int(anchors.get(fam, 0)),
            "anchor_match": bool(members == anchors.get(fam)),
            "rows_per_member_mean": float(sub.height / members) if members else None,
        }
    blocks = {}
    for blk in ("block1", "block2"):
        sub = df.filter(pl.col("block") == blk)
        blocks[blk] = {
            "rows": int(sub.height),
            "members": int(sub.select(["sleeve_day", "family", "ticker"]).unique().height) if sub.height else 0,
            "days": int(sub["sleeve_day"].n_unique()) if sub.height else 0,
        }
    null_rates = {}
    for name in COLUMN_DTYPES:
        s = df[name]
        null_rates[name] = {
            "null_rate": float(s.null_count() / df.height) if df.height else None,
            "null_count": int(s.null_count()),
            "family": COLUMN_FAMILIES[name],
        }
    grouped: dict[str, dict] = {}
    for name, rec in null_rates.items():
        g = grouped.setdefault(rec["family"], {"columns": 0, "null_count": 0, "cells": 0,
                                               "max_null_rate": 0.0, "mean_null_rate": 0.0})
        g["columns"] += 1
        g["null_count"] += rec["null_count"]
        g["cells"] += df.height
        g["max_null_rate"] = max(g["max_null_rate"], rec["null_rate"] or 0.0)
        g["mean_null_rate"] += (rec["null_rate"] or 0.0)
    for g in grouped.values():
        g["mean_null_rate"] /= max(g["columns"], 1)
    doc = {
        "producer": "factory/scripts/basket_atlas_panel.py",
        "schema_version": SCHEMA_VERSION,
        "schema": "factory/artifacts/basket/phase2/ATLAS/SCHEMA.md",
        "panel": str(panel_path.relative_to(ROOT)) if panel_path.is_relative_to(ROOT) else str(panel_path),
        "panel_sha256": sha256_file(panel_path),
        "days_dev_total": len(sim.dev_days()),
        "days_in_panel": int(df["sleeve_day"].n_unique()) if df.height else 0,
        "members": int(df.select(["sleeve_day", "family", "ticker"]).unique().height) if df.height else 0,
        "rows": int(df.height),
        "per_family": per_family,
        "per_block": blocks,
        "months": int(df["month"].n_unique()) if df.height else 0,
        "per_month_rows": ({m: int(c) for m, c in sorted(
            zip(df.group_by("month").len()["month"].to_list(),
                df.group_by("month").len()["len"].to_list()))} if df.height else {}),
        "column_families": COLUMN_FAMILIES,
        "null_rates_by_column": null_rates,
        "null_rates_by_column_family": grouped,
        "outcome_columns": list(OUTCOME_COLUMNS),
        "c1_verification": c1_check or {},
        "conventions": CONVENTIONS,
        "ambiguities": AMBIGUITIES,
        "producer_additions": PRODUCER_ADDITIONS,
        "future_only_columns": list(FUTURE_ONLY_COLUMNS),
        "future_only_prefixes": list(FUTURE_ONLY_PREFIXES),
        "causal_excluded_families": list(CAUSAL_EXCLUDED_FAMILIES),
        "causal_safety_rule": ("no market-state feature may be drawn from any column whose "
                              "family is in causal_excluded_families, nor from any column named "
                              "in future_only_columns / prefixed future_"),
        "censor_census": censor_census(df) if df.height else {},
        "verification": {**verification_registry(),
                         "last_run": verification or {},
                         "corruption_command": ("python factory/scripts/basket_atlas_panel.py "
                                                "corruption --panel "
                                                "factory/artifacts/basket/phase2/ATLAS/"
                                                "panel.parquet")},
        "tape_shape": tape_shape(df) if df.height else {},
    }
    if progress is not None:
        doc["census"] = {m: r.get("census") for m, r in progress.get("months", {}).items()}
        agg: dict[str, int] = {}
        for r in progress.get("months", {}).values():
            for k, v in (r.get("census") or {}).items():
                agg[k] = agg.get(k, 0) + int(v)
        doc["census_totals"] = agg
        doc["census_detail"] = progress.get("census_detail", {})
        doc["part_sha256"] = {m: r.get("sha256") for m, r in progress.get("months", {}).items()}
    atomic_write_json(out, doc)
    return doc


# --------------------------------------------------------------------------- #
# self-tests
# --------------------------------------------------------------------------- #


class SelftestFailure(RuntimeError):
    pass


def _check(cond: bool, msg: str, results: list) -> None:
    results.append({"check": msg, "ok": bool(cond)})
    if not cond:
        raise SelftestFailure(msg)


def selftest(panel_path: Path, anchors: dict | None = ANCHORS, verbose: bool = True) -> dict:
    """Acceptance checks (a)-(e) from the task, run against a built panel.

    ``anchors=None`` skips the absolute-count comparison (used for the smoke
    subset, where the counts are per-day-subset); check (a2), the member set
    against the C1 engine's tickets restricted to the panel's days, always runs.
    """
    results: list[dict] = []
    df = pl.read_parquet(panel_path)
    sem = sim.session_end_map()

    # (a) coverage -------------------------------------------------------- #
    counts = {fam: df.filter(pl.col("family") == fam)
              .select(["sleeve_day", "ticker"]).unique().height for fam in FAMILY_KEYS}
    for fam in FAMILY_KEYS:
        if anchors and fam in anchors:
            _check(counts[fam] == int(anchors[fam]),
                   f"(a) {fam} members {counts[fam]} == anchor {anchors[fam]}", results)
        else:
            results.append({"check": f"(a) {fam} members = {counts[fam]} "
                                     f"(anchor check only on the full panel)", "ok": True})

    # (a2) member set, entry_px, entry_et and entry_rank == the C1 engine, on the
    # days the panel covers.  A missing reference is a failure, not a skip.
    c1 = compare_c1(panel_path)
    c1_bad = c1_failures(c1)
    _check(not c1_bad,
           f"(a2) exact C1 equality on keys/entry_px/entry_et/entry_rank/forced-flat execution "
           f"({'; '.join(c1_bad) if c1_bad else 'all fields exact'}; "
           f"ranks checked: {sum(int(r.get('entry_rank_members_checked', 0)) for r in c1.values())}, "
           f"engine exits reconciled: "
           f"{sum(int(r.get('forced_flat_checked', 0)) for r in c1.values())})",
           results)

    # (a3) engine precedence reconciliation, fired and non-firing rows ------- #
    unit = giveback_unit_tests()
    unit_bad = [u["case"] for u in unit if not u["ok"]]
    _check(not unit_bad,
           f"(a3) synthetic boundary cases of the give-back rule "
           f"(session_end-2 executable / session_end-1 preempted / session_end condition "
           f"preempted / gap session_end-2->session_end pending / no trigger): "
           f"{[u['case'] for u in unit if u['ok']]} all pass", results)
    recon = reconcile_engine_precedence(df)
    recon_bad = [f"{fam}: {rec['n_mismatches']} continuation mismatches"
                 for fam, rec in recon.items()
                 if rec.get("available") and rec.get("n_mismatches")]
    stats_all: dict[str, int] = {}
    for rec in recon.values():
        for k, v in (rec.get("stats") or {}).items():
            stats_all[k] = stats_all.get(k, 0) + int(v)
    _check(not recon_bad,
           f"(a3) engine precedence reconciliation over every fill row, fired rows included "
           f"({'; '.join(recon_bad) if recon_bad else 'exact'}; cases checked: "
           f"{sum(int(r.get('checked', 0)) for r in recon.values())}; breakdown {stats_all})",
           results)

    # (b) rows per member == bars from fill bar to session end ------------- #
    bad_rows = []
    members = df.select(["sleeve_day", "family", "ticker", "entry_et",
                         "session_end"]).unique(subset=["sleeve_day", "family", "ticker"])
    row_counts = df.group_by(["sleeve_day", "family", "ticker"]).len()
    key = row_counts.join(members, on=["sleeve_day", "family", "ticker"], how="left")
    per_day_cache: dict[str, object] = {}
    for rec in key.iter_rows(named=True):
        day = rec["sleeve_day"]
        if day not in per_day_cache:
            per_day_cache[day] = sim.load_bars(day, sem[day])
        bars = per_day_cache[day]
        tk = bars.ticker(rec["ticker"])
        if tk is None:
            bad_rows.append({**rec, "expected": None, "reason": "no_bars"})
            continue
        ets = tk["et"]
        n_expect = int(((ets >= rec["entry_et"]) & (ets <= rec["session_end"])).sum())
        if n_expect != rec["len"]:
            bad_rows.append({**rec, "expected": n_expect})
    _check(not bad_rows,
           f"(b) rows per member == bars from fill et to session end "
           f"({len(bad_rows)} mismatches)", results)

    # (c) bar_index monotone increasing per member ------------------------ #
    chk = (df.sort(["sleeve_day", "family", "entry_rank", "bar_index"])
             .group_by(["sleeve_day", "family", "ticker"], maintain_order=True)
             .agg([pl.col("bar_index").diff().drop_nulls().min().alias("derived_dmin"),
                   pl.col("bar_index").first().alias("derived_first"),
                   pl.col("et").diff().drop_nulls().min().alias("derived_et_min_diff"),
                   pl.len().alias("n")]))
    bad_mono = chk.filter((pl.col("derived_dmin") != 1) | (pl.col("derived_first") != 0) | (pl.col("derived_et_min_diff") <= 0))
    _check(bad_mono.height == 0,
           f"(c) bar_index is 0..n-1 and et strictly increasing ({bad_mono.height} offenders)", results)

    # (d) sampled rows recomputed from the raw bars, incl. target strata ---- #
    picks = []
    rng = random.Random(20260925)
    picks.extend(df.sample(n=4, seed=20260925).iter_rows(named=True))
    strata = {
        "terminal_censored": df.filter(pl.col("terminal_censored")),
        "last_bar": df.filter(pl.col("bar_index") == pl.col("bar_index").max().over(
            ["sleeve_day", "family", "ticker"])),
        "condition_after_forced_flat": df.filter(
            pl.col("giveback_condition_after_forced_flat_10") == True),  # noqa: E712
        "early_close": df.filter(pl.col("session_end") == sim.SESSION_END_EARLY),
        "missing_reference": df.filter(pl.col("open0930").is_null() | pl.col("prev_close").is_null()),
        "threshold_tie": df.filter(
            (pl.col("bar_close") - pl.col("running_high") * 0.9).abs()
            <= 1e-9 * pl.col("running_high")),
    }
    for name, sub in strata.items():
        if sub.height:
            picks.append(sub.row(int(rng.randrange(sub.height)), named=True))
    recomputed = []
    mismatches = []
    for row in picks:
        got = recompute_row(row, sem)
        recomputed.append({"key": [row["sleeve_day"], row["family"], row["ticker"], row["et"]],
                           "panel": {k: row[k] for k in got},
                           "recomputed": got})
        for k, v in got.items():
            pv = row[k]
            if pv is None or v is None:
                if pv is not None or v is not None:
                    mismatches.append({"key": recomputed[-1]["key"], "col": k,
                                       "panel": pv, "recomputed": v})
                continue
            if abs(float(pv) - float(v)) > 1e-12:
                mismatches.append({"key": recomputed[-1]["key"], "col": k,
                                   "panel": pv, "recomputed": v,
                                   "abs_diff": abs(float(pv) - float(v))})
    _check(not mismatches,
           f"(d) {len(picks)} sampled rows (random + censored/last-bar/unexecutable/early-close/"
           f"missing-reference strata) recompute exactly from raw bars "
           f"({len(mismatches)} mismatches)", results)

    # (e) outcome columns null exactly when next_open is null -------------- #
    # Three groups now.
    #  1. `next_open is null` => null: absolute, no exception.
    #  2. `next_open` exists and the member's tape reaches the session close:
    #     every event-defined outcome column is populated.  The only exceptions
    #     are the schema-declared "null if none" pair.
    #  3. terminal-censored members: every terminal-dependent outcome is null by
    #     construction (check f), so they are excluded from group 2 only.
    offenders_forward = {}
    offenders_backward = {}
    for col in OUTCOME_COLUMNS:
        bad_fwd = df.filter(pl.col("next_open").is_null() & pl.col(col).is_not_null())
        if bad_fwd.height:
            offenders_forward[col] = int(bad_fwd.height)
        if col in NO_EVENT_NULL_OUTCOMES:
            continue
        sub = df.filter(pl.col("next_open").is_not_null()
                        & pl.col("path_complete_to_session_end") & pl.col(col).is_null())
        if sub.height:
            offenders_backward[col] = int(sub.height)
    _check(not offenders_forward,
           f"(e) no outcome column is populated when next_open is null ({offenders_forward})",
           results)
    _check(not offenders_backward,
           f"(e) on complete tapes every event-defined outcome column is populated when "
           f"next_open exists ({offenders_backward}; no-event-null exceptions: "
           f"{list(NO_EVENT_NULL_OUTCOMES)})", results)

    # (f) terminal censoring ------------------------------------------------ #
    censored = df.filter(pl.col("terminal_censored"))
    complete = df.filter(~pl.col("terminal_censored"))
    _check(df["terminal_censored"].null_count() == 0
           and df["path_complete_to_session_end"].null_count() == 0,
           "(f) censor flags are never null", results)
    inconsistent = df.filter(pl.col("terminal_censored") == pl.col("path_complete_to_session_end"))
    _check(inconsistent.height == 0,
           f"(f) terminal_censored and path_complete_to_session_end are complementary "
           f"({inconsistent.height} rows violate)", results)
    flag_bad = df.filter(pl.col("terminal_censored")
                         != (pl.col("future_member_last_et") < pl.col("session_end")))
    _check(flag_bad.height == 0,
           f"(f) terminal_censored == (future_member_last_et < session_end) "
           f"({flag_bad.height} rows violate)", results)
    leaked = {}
    if censored.height:
        for col in CENSORED_OUTCOME_COLUMNS:
            n = censored.filter(pl.col(col).is_not_null()).height
            if n:
                leaked[col] = int(n)
    _check(not leaked,
           f"(f) every terminal-dependent outcome is null on censored tapes ({leaked})", results)
    censored_members = int(censored.select(["sleeve_day", "family", "ticker"]).unique().height)
    results.append({
        "check": f"(f) censored: {censored_members} members / {censored.height} rows "
                 f"({100.0 * censored.height / max(df.height, 1):.2f}% of rows); "
                 f"complete: {complete.height} rows",
        "ok": True})

    # (g) forced-flat precedence over the give-back rule -------------------- #
    # The engine goes flat at the session_end bar's OPEN; a give-back close
    # condition first seen on that bar's close cannot preempt it.  Such rows must
    # carry the forced-flat continuation, fired = false, and the diagnostic flag.
    bad_g: dict[str, int] = {}
    diag_counts = {}
    for lv in GIVEBACK_LEVELS:
        col_d = f"giveback_condition_after_forced_flat_{lv}"
        col_v = f"v_giveback_{lv}"
        col_f = f"giveback_fired_{lv}"
        diag_counts[col_d] = int(df.filter(pl.col(col_d) == True).height)  # noqa: E712
        bad = df.filter((pl.col(col_d) == True)  # noqa: E712
                        & ((pl.col(col_f) == True)  # noqa: E712
                           | pl.col(col_v).is_null()
                           | ((pl.col(col_v) - pl.col("v_forced_flat")).abs() > 1e-12)))
        if bad.height:
            bad_g[col_d] = int(bad.height)
        # a firing rule always has an execution bar and equals the next open
        fired_no_next = df.filter((pl.col(col_f) == True)  # noqa: E712
                                  & pl.col("next_open").is_null())
        if fired_no_next.height:
            bad_g[f"{col_f}_without_next_bar"] = int(fired_no_next.height)
    _check(not bad_g,
           f"(g) a session_end-close give-back condition cannot preempt the forced flat: "
           f"value == v_forced_flat, fired = false ({bad_g})", results)
    results.append({"check": f"(g) condition-after-forced-flat rows per threshold: {diag_counts}",
                    "ok": True})

    # (h) causal safety of the future-only registry ------------------------- #
    future_cols = [c for c in df.columns if c.startswith(FUTURE_ONLY_PREFIXES)]
    unregistered = [c for c in future_cols if c not in FUTURE_ONLY_COLUMNS]
    _check(not unregistered,
           f"(h) every future_* column is in the leakage registry ({unregistered})", results)
    registry_bad = [c for c in FUTURE_ONLY_COLUMNS
                    if c in COLUMN_DTYPES and COLUMN_FAMILIES[c] not in CAUSAL_EXCLUDED_FAMILIES]
    _check(not registry_bad,
           f"(h) every registered future-only column is non-causal by family ({registry_bad})",
           results)
    state_prefixed = [c for c in df.columns
                      if c in FUTURE_ONLY_COLUMNS and COLUMN_FAMILIES[c].startswith("state")]
    _check(not state_prefixed,
           f"(h) no future-only column is classified as causal state ({state_prefixed})", results)

    # (i) source-level column registry ------------------------------------- #
    stale = audit_column_references()
    _check(not stale,
           f"(i) every column referenced by this producer exists in the registry "
           f"({stale})", results)
    selector = causal_feature_columns(df.columns)
    defi = [c for c in ("terminal_censored", "path_complete_to_session_end",
                        "future_member_last_et", "future_forced_flat_px",
                        "session_peak_et", "session_close_ret_from_entry",
                        "v_hold_flat", "final_high_flag", "tail_class_100") if c in selector]
    _check(not defi,
           f"(i) a generic causal selector admits no future-only/outcome column ({defi})", results)
    expected_state = [c for c in ("ret_from_fill", "running_high", "bars_since_new_high",
                                  "peer_ret_median", "volume_accel") if c not in selector]
    _check(not expected_state,
           f"(i) the causal selector still admits the state columns ({expected_state})", results)

    rep = {
        "panel": str(panel_path),
        "panel_sha256": sha256_file(panel_path),
        "rows": int(df.height),
        "members": int(df.select(["sleeve_day", "family", "ticker"]).unique().height),
        "family_member_counts": counts,
        "checks": results,
        "sampled_rows": recomputed,
        "forced_flat_precedence": recon,
        "giveback_unit_tests": unit,
        "all_ok": True,
    }
    if verbose:
        for r in results:
            print(f"  [ok] {r['check']}", flush=True)
    return rep


def recompute_row(row: dict, sem: dict[str, int]) -> dict:
    """Independent, literal recomputation of the sampled path statistics.

    Deliberately naive: reads the day's parquet directly (no producer geometry)
    and walks the tape with plain Python loops.  Mirrors the panel's censoring
    contract: a tape that stops before the session close has no terminal value,
    so every terminal-dependent statistic is None (never a same-close value).
    """
    day = row["sleeve_day"]
    bars_df = pl.read_parquet(sim.BARS_DIR / f"{day}.parquet")
    sub = bars_df.filter(pl.col("ticker") == row["ticker"]).sort("et")
    ets = [int(e) for e in sub["et"].to_list()]
    opens = [float(v) for v in sub["open"].to_list()]
    highs = [float(v) for v in sub["high"].to_list()]
    lows = [float(v) for v in sub["low"].to_list()]
    closes = [float(v) for v in sub["close"].to_list()]
    session_end = int(row["session_end"])
    assert session_end == sem[day], (session_end, sem[day])
    k = [i for i, e in enumerate(ets) if e <= session_end]
    ets = [ets[i] for i in k]
    opens = [opens[i] for i in k]
    highs = [highs[i] for i in k]
    closes = [closes[i] for i in k]
    lows = [lows[i] for i in k]
    t = int(row["et"])
    j = [i for i, e in enumerate(ets) if e == t]
    assert len(j) == 1, f"bar et={t} not found for {row['ticker']} on {day}"
    j = j[0]
    fill = [i for i, e in enumerate(ets) if e >= int(row["entry_et"])][0]
    entry_px = float(row["entry_px"])
    run_high = max(highs[fill:j + 1])
    out = {
        "ret_from_fill": closes[j] / entry_px - 1.0,
        "mfe_so_far": run_high / entry_px - 1.0,
        "dist_from_running_high": closes[j] / run_high - 1.0,
    }
    complete = ets[-1] == session_end
    if j + 1 >= len(ets) or not complete:
        # last bar, or a member whose tape does not reach the session close
        out.update({"final_high_flag": None, "remaining_run": None,
                    "cost_of_waiting": None, "v_hold_flat": None})
        return out
    nxt = opens[j + 1]
    fut_hi = highs[j + 1:]
    fut_lo = lows[j + 1:]
    out.update({
        "final_high_flag": bool(max(fut_hi) <= run_high),
        "remaining_run": max(fut_hi) / nxt - 1.0,
        "cost_of_waiting": min(fut_lo) / nxt - 1.0,
        "v_hold_flat": closes[-1] / nxt - 1.0,
    })
    return out


def _c1_rank_map(days: list[str], family: str) -> dict[tuple[str, str], int]:
    """Canonical snapshot rank per (day, ticker) — the source ``basket_sim``
    reads for ``entry_rank`` (``nm["rank"]``)."""
    pop, T = ("A_pm", 570) if family == "A_pm" else ("B", 600)
    out: dict[tuple[str, str], int] = {}
    for day in days:
        snap = sim.snapshot_of(sim.load_anatomy(day), pop, T)
        if not snap:
            continue
        for nm in snap["names"]:
            out[(day, nm["ticker"])] = int(nm.get("rank", 0))
    return out


def compare_c1_frame(df: pl.DataFrame, rank_check: bool = True) -> dict:
    """Exact comparison of the panel against the C1 engine's own tickets.

    Compares, per family: the member key set (both directions) and, on the
    intersection, ``entry_px``, ``entry_et`` and ``entry_rank``.  ``entry_rank``
    is checked against the anatomy snapshot ranks that the engine copies into
    the ticket (``tickets.parquet`` carries no rank column).

    The comparison is restricted to the days the panel covers, so it is valid
    for the smoke subset as well as for the full 1,066-day panel.
    """
    days_in_panel = sorted(df["sleeve_day"].unique().to_list())
    out = {}
    for fam, (cell, expected) in C1_CELLS.items():
        cell_dir = ROOT / "factory/artifacts/basket/phase2/F1_C1/F1" / cell
        tickets = cell_dir / "tickets.parquet"
        if not (tickets.exists() and (cell_dir / "metrics.json").exists()):
            out[fam] = {"cell": cell, "available": False, "expected_n_entries": expected,
                        "panel_members": int(df.filter(pl.col("family") == fam)
                                             .select(["sleeve_day", "ticker"]).unique().height)}
            continue
        tk = pl.read_parquet(tickets).filter(pl.col("sleeve_day").is_in(days_in_panel))
        metrics = json.loads((cell_dir / "metrics.json").read_text())
        pf = df.filter(pl.col("family") == fam).select(
            ["sleeve_day", "ticker", "entry_px", "entry_et", "entry_rank"]).unique()
        panel_keys = set(map(tuple, pf.select(["sleeve_day", "ticker"]).rows()))
        c1_keys = set(map(tuple, tk.select(["sleeve_day", "ticker"]).unique().rows()))
        common = panel_keys & c1_keys
        px_panel = {(r["sleeve_day"], r["ticker"]): float(r["entry_px"])
                    for r in pf.select(["sleeve_day", "ticker", "entry_px"]).unique().iter_rows(named=True)}
        px_c1 = {(r["sleeve_day"], r["ticker"]): float(r["entry_px"])
                 for r in tk.select(["sleeve_day", "ticker", "entry_px"]).unique().iter_rows(named=True)}
        et_panel = {(r["sleeve_day"], r["ticker"]): int(r["entry_et"])
                    for r in pf.select(["sleeve_day", "ticker", "entry_et"]).unique().iter_rows(named=True)}
        et_c1 = {(r["sleeve_day"], r["ticker"]): int(r["entry_et"])
                 for r in tk.select(["sleeve_day", "ticker", "entry_et"]).unique().iter_rows(named=True)}
        px_bad = sorted(k for k in common
                        if abs(px_panel.get(k, float("nan")) - px_c1.get(k, float("nan"))) > 1e-9)
        et_bad = sorted(k for k in common if et_panel.get(k) != et_c1.get(k))
        rank_bad: list = []
        ranks_checked = 0
        if rank_check:
            ranks = _c1_rank_map(days_in_panel, fam)
            rk_panel = {(r["sleeve_day"], r["ticker"]): int(r["entry_rank"])
                        for r in pf.select(["sleeve_day", "ticker", "entry_rank"]).unique().iter_rows(named=True)}
            for k in sorted(common):
                if k in ranks:
                    ranks_checked += 1
                    if rk_panel.get(k) != ranks[k]:
                        rank_bad.append(k)
        ff_bad: list = []
        ff_checked = 0
        if "future_forced_flat_px" in df.columns:
            ff = {(r["sleeve_day"], r["ticker"]): r["future_forced_flat_px"]
                  for r in df.filter(pl.col("family") == fam)
                  .select(["sleeve_day", "ticker", "future_forced_flat_px", "session_end"])
                  .unique().iter_rows(named=True)}
            ses = {(r["sleeve_day"], r["ticker"]): int(r["session_end"])
                   for r in df.filter(pl.col("family") == fam)
                   .select(["sleeve_day", "ticker", "session_end"]).unique().iter_rows(named=True)}
            tickets = tk.select(["sleeve_day", "ticker", "exit_day", "exit_et", "exit_px",
                                 "exit_reason"]).unique()
            for r in tickets.iter_rows(named=True):
                key = (r["sleeve_day"], r["ticker"])
                if key not in ff:
                    continue
                ff_checked += 1
                in_session = (r["exit_day"] == r["sleeve_day"]
                              and r["exit_reason"] == "FORCED_FLAT")
                px = ff[key]
                if in_session:
                    if px is None or abs(float(px) - float(r["exit_px"])) > 1e-9:
                        ff_bad.append({"key": list(key), "panel_forced_flat_px": px,
                                       "engine_exit_px": float(r["exit_px"]),
                                       "engine_exit_et": int(r["exit_et"])})
                    elif ses.get(key) != int(r["exit_et"]):
                        ff_bad.append({"key": list(key), "panel_session_end": ses.get(key),
                                       "engine_exit_et": int(r["exit_et"])})
                else:
                    if px is not None:
                        ff_bad.append({"key": list(key), "panel_forced_flat_px": px,
                                       "engine_exit": "carried (no session_end print)"})
        out[fam] = {
            "cell": cell,
            "available": True,
            "n_entries_metrics": int(metrics.get("n_entries", -1)),
            "expected_n_entries": expected,
            "panel_members": len(panel_keys),
            "c1_members": len(c1_keys),
            "common_members": len(common),
            "only_in_panel": sorted(panel_keys - c1_keys)[:50],
            "only_in_c1": sorted(c1_keys - panel_keys)[:50],
            "n_only_in_panel": len(panel_keys - c1_keys),
            "n_only_in_c1": len(c1_keys - panel_keys),
            "entry_px_mismatches": px_bad[:20],
            "n_entry_px_mismatches": len(px_bad),
            "entry_et_mismatches": et_bad[:20],
            "n_entry_et_mismatches": len(et_bad),
            "entry_rank_mismatches": rank_bad[:20],
            "n_entry_rank_mismatches": len(rank_bad),
            "entry_rank_members_checked": ranks_checked,
            "forced_flat_checked": ff_checked,
            "n_forced_flat_mismatches": len(ff_bad),
            "forced_flat_mismatches": ff_bad[:20],
        }
    return out


def giveback_unit_tests() -> list[dict]:
    """Synthetic boundary tests of the give-back/precedence rule.

    session_end = 959 throughout.  Five cases named by the reviewer:
      A trigger at session_end-2            -> executable, fired
      B trigger at session_end-1            -> preempted, forced flat, diagnostic
      C condition first at session_end      -> preempted, forced flat, diagnostic
      D gap session_end-2 -> session_end    -> the pending executes at 959 open
      E no trigger                          -> forced flat, no diagnostic
    """
    end = sim.SESSION_END_NORMAL                     # 959
    cases: list[dict] = []

    def build(trigger_et: int | None, skip_ets: tuple[int, ...] = ()) -> tuple:
        """Bars 950..end minus ``skip_ets``, with a 10% drop from trigger_et on."""
        ets, op, hi, lo, cl = [], [], [], [], []
        price = 100.0
        for e in range(950, end + 1):
            if e in skip_ets:
                continue
            drop = trigger_et is not None and e >= trigger_et
            c = 88.5 if drop else price                      # ~11.5% below the 100 high
            ets.append(e)
            op.append(price)
            hi.append(price)
            lo.append(c)
            cl.append(c)
        return (np.array(ets), np.array(op), np.array(hi), np.array(lo), np.array(cl))

    def run(trigger_et, skip_ets, expect_fired, expect_px, expect_diag):
        ets, op, hi, lo, cl = build(trigger_et, skip_ets)
        forced = float(op[-1]) if ets[-1] == end else None
        px, fired, diag = _giveback_exit(cl, hi, np.maximum.accumulate(hi), op, ets, 0, 10,
                                         forced, end)
        ok = (fired == expect_fired and diag == expect_diag
              and ((px is None and expect_px is None)
                   or (px is not None and expect_px is not None and abs(px - expect_px) < 1e-9)))
        return {"case": cases[-1]["case"] if False else None, "ok": ok, "fired": fired,
                "diagnostic": diag, "px": px, "expected_px": expect_px,
                "expected_fired": expect_fired, "expected_diagnostic": expect_diag,
                "trigger_et": trigger_et, "skipped_ets": list(skip_ets)}

    def add(name, *args, **kw):
        rec = run(*args, **kw)
        rec["case"] = name
        cases.append(rec)

    flat_px = float(build(None)[1][-1])          # open of the session_end bar
    # A: first trigger at session_end-2 -> executable, exit at the next bar's open
    add("A_trigger_at_session_end_minus_2", end - 2, (), True, flat_px, False)
    # B: first trigger at session_end-1 -> preempted by the forced-flat decision
    add("B_trigger_at_session_end_minus_1", end - 1, (), False, flat_px, True)
    # C: condition first appears at session_end -> preempted as well
    add("C_condition_at_session_end", end, (), False, flat_px, True)
    # D: trigger at session_end-2 but the tape gaps straight to session_end:
    #    still a firing, and its pending executes at the session_end open
    ets_d, op_d, hi_d, lo_d, cl_d = build(end - 2, (end - 1,))
    assert ets_d[-1] == end and ets_d[-2] == end - 2, ets_d[-5:]
    add("D_gap_session_end_minus_2_to_session_end", end - 2, (end - 1,), True,
        float(op_d[-1]), False)
    # E: no trigger at all -> forced flat, no diagnostic
    add("E_no_trigger", None, (), False, flat_px, False)
    return cases


def reconcile_engine_precedence(df: pl.DataFrame) -> dict:
    """Reconcile every fill-row give-back continuation with the engine's actions.

    Fired rows are reconciled too (not skipped): the naive forward walk over the
    raw tape re-derives the first trigger bar by clock ET, applies the engine's
    precedence (a trigger at ``et >= session_end-1`` never fires), and compares
    the resulting continuation with the panel's value and with the C1 engine's
    realized exit for the non-firing rows.
    """
    out: dict[str, dict] = {}
    days_in_panel = sorted(df["sleeve_day"].unique().to_list())
    for fam, (cell, _expected) in C1_CELLS.items():
        cell_dir = ROOT / "factory/artifacts/basket/phase2/F1_C1/F1" / cell
        tickets_path = cell_dir / "tickets.parquet"
        if not tickets_path.exists():
            out[fam] = {"available": False, "cell": cell}
            continue
        tk = pl.read_parquet(tickets_path).filter(pl.col("sleeve_day").is_in(days_in_panel))
        engine = {(r["sleeve_day"], r["ticker"]): (r["exit_day"], float(r["exit_px"]))
                  for r in tk.select(["sleeve_day", "ticker", "exit_day", "exit_px"])
                  .unique().iter_rows(named=True)}
        fill = df.filter((pl.col("family") == fam) & (pl.col("bar_index") == 0)
                         & (~pl.col("terminal_censored")))
        stats = {"non_firing": 0, "preempted_at_session_end_minus_1": 0,
                 "preempted_at_session_end": 0, "fired": 0}
        mismatches: list[dict] = []
        bars_cache: dict[str, object] = {}
        for row in fill.iter_rows(named=True):
            key = (row["sleeve_day"], row["ticker"])
            if key not in engine:
                continue
            exit_day, exit_px = engine[key]
            if exit_day != row["sleeve_day"] or row["next_open"] is None:
                continue
            if row["sleeve_day"] not in bars_cache:
                bars_cache[row["sleeve_day"]] = sim.load_bars(row["sleeve_day"],
                                                              row["session_end"])
            bars = bars_cache[row["sleeve_day"]]
            tkb = bars.ticker(row["ticker"])
            ets = [int(e) for e in tkb["et"]]
            op = [float(v) for v in tkb["open"]]
            hi = [float(v) for v in tkb["high"]]
            cl = [float(v) for v in tkb["close"]]
            fi = next(i for i, e in enumerate(ets) if e >= int(row["entry_et"]))
            session_end = int(row["session_end"])
            forced = op[ets.index(session_end)] if session_end in ets else None
            nxt = float(row["next_open"])
            for lv in GIVEBACK_LEVELS:
                run_high = max(hi[fi:fi + 1])
                trig = None
                for i in range(fi + 1, len(ets)):
                    run_high = max(run_high, hi[i])
                    if cl[i] <= run_high * (1 - lv / 100.0) + 1e-9 * run_high:
                        trig = i
                        break
                if trig is None:
                    stats["non_firing"] += 1
                    expected = exit_px / nxt - 1.0
                    exp_fired, exp_diag = False, False
                elif ets[trig] >= session_end - 1:
                    stats["preempted_at_session_end_minus_1" if ets[trig] == session_end - 1
                          else "preempted_at_session_end"] += 1
                    expected = exit_px / nxt - 1.0 if forced is not None else None
                    exp_fired, exp_diag = False, True
                else:
                    stats["fired"] += 1
                    nxt_bar = next((j for j in range(trig + 1, len(ets))), None)
                    if nxt_bar is None:
                        expected = None
                    else:
                        expected = op[nxt_bar] / nxt - 1.0
                    exp_fired, exp_diag = True, False
                got = row[f"v_giveback_{lv}"]
                got_fired = row[f"giveback_fired_{lv}"]
                got_diag = row[f"giveback_condition_after_forced_flat_{lv}"]
                ok = ((got is None and expected is None)
                      or (got is not None and expected is not None
                          and abs(float(got) - expected) <= 1e-9)
                      ) and bool(got_fired) == exp_fired and bool(got_diag) == exp_diag
                if not ok:
                    mismatches.append({"name": [row["sleeve_day"], fam, row["ticker"], lv],
                                       "panel": got, "expected": expected,
                                       "fired": got_fired, "expected_fired": exp_fired,
                                       "diagnostic": got_diag, "expected_diagnostic": exp_diag,
                                       "trigger_et": None if trig is None else ets[trig]})
        out[fam] = {"available": True, "cell": cell, "stats": stats,
                    "checked": sum(stats.values()),
                    "n_mismatches": len(mismatches), "mismatches": mismatches[:20]}
    return out


def compare_c1(panel_path: Path) -> dict:
    return compare_c1_frame(pl.read_parquet(panel_path))


def c1_failures(c1: dict) -> list[str]:
    """Every way the C1 acceptance comparison can fail — empty means exact."""
    bad: list[str] = []
    for fam, rec in c1.items():
        if not rec.get("available"):
            bad.append(f"{fam}: C1 reference missing ({rec.get('cell')})")
            continue
        for key, label in (("n_only_in_panel", "members only in the panel"),
                           ("n_only_in_c1", "members only in C1"),
                           ("n_entry_px_mismatches", "entry_px mismatches"),
                           ("n_entry_et_mismatches", "entry_et mismatches"),
                           ("n_entry_rank_mismatches", "entry_rank mismatches"),
                           ("n_forced_flat_mismatches",
                            "forced-flat execution price mismatches vs the engine's exits")):
            val = int(rec.get(key, 0))
            if val:
                bad.append(f"{fam}: {val} {label}")
        if rec.get("n_entries_metrics") != rec.get("expected_n_entries"):
            bad.append(f"{fam}: n_entries {rec.get('n_entries_metrics')} != anchor "
                       f"{rec.get('expected_n_entries')}")
        if rec.get("panel_members") != rec.get("c1_members"):
            bad.append(f"{fam}: panel members {rec.get('panel_members')} != C1 members "
                       f"{rec.get('c1_members')}")
    return bad


def audit_column_references() -> list[str]:
    """Source-level check that every column name this file references exists.

    Two passes: ``pl.col(<name>)`` expressions, and quoted names inside the
    list arguments of ``select`` / ``group_by`` / ``sort`` (where a stale rename
    would otherwise only surface after the expensive rebuild).  Derived aliases
    (`derived_*`) and the C1 ticket columns in ``EXTERNAL_COLUMN_REFS`` are
    allowed explicitly.
    """
    src = Path(__file__).read_text()
    refs = set(re.findall(r'pl\.col\("([a-zA-Z0-9_]+)"\)', src))
    for call in re.finditer(r'\.(?:select|group_by|sort)\(\s*\[([^\]]*)\]', src):
        refs.update(re.findall(r'"([a-zA-Z0-9_]+)"', call.group(1)))
    return sorted(r for r in refs
                  if r not in COLUMN_DTYPES and r not in EXTERNAL_COLUMN_REFS
                  and not r.startswith("derived_"))


def cmd_corruption(args) -> int:
    """Prove the C1 acceptance assertion fails on each corrupted field.

    Loads the panel once, corrupts one member's key / entry_px / entry_et /
    entry_rank in memory, and requires :func:`c1_failures` to report it.
    """
    panel = Path(args.panel) if args.panel else PANEL
    df = pl.read_parquet(panel, columns=["sleeve_day", "family", "ticker", "entry_px",
                                         "entry_et", "entry_rank", "session_end",
                                         "future_forced_flat_px"])
    keys = df.select(["sleeve_day", "family", "ticker"]).unique().sort(
        ["sleeve_day", "family", "ticker"])
    victim = keys.row(0, named=True)
    mask = ((pl.col("sleeve_day") == victim["sleeve_day"])
            & (pl.col("family") == victim["family"])
            & (pl.col("ticker") == victim["ticker"]))
    print(f"corruption victim: {victim}", flush=True)
    cases = {
        "member_key": df.with_columns(
            pl.when(mask).then(pl.col("ticker") + "X").otherwise(pl.col("ticker")).alias("ticker")),
        "entry_px": df.with_columns(
            pl.when(mask).then(pl.col("entry_px") * 1.0001).otherwise(pl.col("entry_px")).alias("entry_px")),
        "entry_et": df.with_columns(
            pl.when(mask).then(pl.col("entry_et") + 1).otherwise(pl.col("entry_et")).alias("entry_et")),
        "entry_rank": df.with_columns(
            pl.when(mask).then(pl.col("entry_rank") + 1).otherwise(pl.col("entry_rank")).alias("entry_rank")),
        "forced_flat_px": df.with_columns(
            pl.when(mask).then(pl.col("future_forced_flat_px") * 1.0001)
            .otherwise(pl.col("future_forced_flat_px")).alias("future_forced_flat_px")),
    }
    baseline = c1_failures(compare_c1_frame(df))
    out = {"baseline_failures": baseline, "cases": {}}
    ok = not baseline
    if baseline:
        print(f"  [FAIL] clean panel already fails C1: {baseline}", flush=True)
    for name, corrupted in cases.items():
        bad = c1_failures(compare_c1_frame(corrupted))
        expected = {"member_key": "members only in", "forced_flat_px": "forced-flat"}.get(name, name)
        caught = any(expected in b for b in bad)
        out["cases"][name] = {"caught": caught, "failures": bad[:5]}
        print(f"  [{'ok' if caught else 'FAIL'}] corruption '{name}' -> "
              f"{bad[:3] if bad else 'NOT CAUGHT'}", flush=True)
        ok = ok and caught
    out["all_caught"] = ok
    target = panel.parent / "corruption_check.json"
    atomic_write_json(target, out)
    print(f"corruption report: {target}", flush=True)
    if not ok:
        raise SelftestFailure("corruption test failed: a corrupted field passed the C1 assertion")
    return 0


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


def run_verifier(panel: Path, report: Path, extra_args: list[str] | None = None) -> dict:
    """Run the independent verifier script in a subprocess and return its report."""
    if not VERIFIER_SCRIPT.exists():
        raise SelftestFailure(f"independent verifier missing: {VERIFIER_SCRIPT}")
    cmd = [sys.executable, str(VERIFIER_SCRIPT), "--panel", str(panel), "--report", str(report)]
    cmd.extend(extra_args or [])
    import subprocess
    proc = subprocess.run(cmd, capture_output=True, text=True)
    tail = (proc.stdout or "").strip().splitlines()[-8:]
    print("  [verifier] " + " | ".join(tail[-3:]), flush=True)
    rep = json.loads(report.read_text()) if report.exists() else {"ok": False,
                                                                  "error": "no report"}
    if proc.returncode != 0 or not rep.get("ok"):
        raise SelftestFailure(
            f"independent verifier failed (exit {proc.returncode}): "
            f"{rep.get('failures') or rep.get('error') or (proc.stderr or '')[-400:]}")
    return {"command": " ".join(cmd), "report": str(report),
            "verifier_sha256": sha256_file(VERIFIER_SCRIPT),
            "mismatches": rep.get("mismatches", 0),
            "comparisons": rep.get("comparisons", 0),
            "rows_checked": rep.get("rows_checked", 0),
            "strata": rep.get("strata", {}),
            "all_ok": bool(rep.get("ok"))}


def cmd_smoke(args) -> int:
    days = smoke_days(sim.dev_days())
    print(f"smoke: {len(days)} days "
          f"({sum(1 for d in days if block_of(d) == 'block1')} block1, "
          f"{sum(1 for d in days if block_of(d) == 'block2')} block2)", flush=True)
    out = Path(args.out) if args.out else SMOKE_DIR
    if args.out and Path(args.out).exists() and args.force:
        shutil.rmtree(args.out)
    build_parts(days, out, force=args.force)
    panel = merge_parts(out)
    print(f"smoke panel: {panel}", flush=True)
    rep = selftest(panel, anchors=None, verbose=True)
    coverage_report(panel, out / "coverage.json", anchors=None)   # exercise the report path
    verification = run_verifier(panel, out / "verify_report.json")
    print(f"  [ok] verifier: {verification['rows_checked']} rows, "
          f"{verification['comparisons']} comparisons, {verification['mismatches']} mismatches",
          flush=True)
    determinism = None
    if args.determinism:
        tmpdir = Path(tempfile.mkdtemp(prefix="atlas_smoke_det_"))
        try:
            build_parts(days, tmpdir, force=True, verbose=False)
            panel2 = merge_parts(tmpdir)
            h1, h2 = sha256_file(panel), sha256_file(panel2)
            determinism = {"sha256_run1": h1, "sha256_run2": h2, "identical": h1 == h2}
            print(f"  [{'ok' if h1 == h2 else 'FAIL'}] (f) determinism: {h1[:16]} vs {h2[:16]}",
                  flush=True)
            if h1 != h2:
                raise SelftestFailure("(f) smoke runs are not byte-identical")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    rep["verification"] = verification
    rep["determinism"] = determinism
    rep["c1_check"] = compare_c1(panel)
    atomic_write_json(out / "selftest.json", rep)
    return 0


def cmd_build(args) -> int:
    out = Path(args.out) if args.out else ATLAS
    days = sim.dev_days() if args.all else (
        [d.strip() for d in args.days.split(",") if d.strip()] if args.days else smoke_days(sim.dev_days()))
    print(f"build: {len(days)} days -> {out}", flush=True)
    progress = build_parts(days, out, force=args.force)
    progress["census_detail"] = progress_census(progress)
    atomic_write_json(out / "_progress.json", progress)
    print(f"progress written: {out / '_progress.json'}", flush=True)
    return 0


def progress_census(progress: dict) -> dict:
    """Aggregate the per-month census recorded while building the parts."""
    counts: dict[str, int] = {}
    detail: dict[str, list] = {}
    for rec in progress.get("months", {}).values():
        for k, v in (rec.get("census") or {}).items():
            counts[k] = counts.get(k, 0) + int(v)
        for k, v in (rec.get("census_detail") or {}).items():
            detail.setdefault(k, []).extend(v)
    return {"counts": counts, "detail": detail}


def cmd_merge(args) -> int:
    out = Path(args.out) if args.out else ATLAS
    panel = merge_parts(out)
    print(f"panel: {panel} ({sha256_file(panel)[:16]})", flush=True)
    return 0


def cmd_coverage(args) -> int:
    panel = Path(args.panel) if args.panel else PANEL
    out = Path(args.out) if args.out else (panel.parent / "coverage.json")
    progress = None
    ppath = panel.parent / "_progress.json"
    if ppath.exists():
        progress = load_progress(ppath)
    c1 = None if args.no_c1 else compare_c1(panel)
    verification = None
    spath = panel.parent / "selftest.json"
    if spath.exists():
        verification = load_progress(spath).get("verification")
    doc = coverage_report(panel, out, c1_check=c1, progress=progress, verification=verification)
    print(f"coverage: {out}", flush=True)
    print(json.dumps({"rows": doc["rows"], "members": doc["members"],
                      "per_family": {k: v["members"] for k, v in doc["per_family"].items()}},
                     indent=1), flush=True)
    return 0


def cmd_selftest(args) -> int:
    panel = Path(args.panel) if args.panel else PANEL
    rep = selftest(panel, verbose=True)
    rep["c1_check"] = compare_c1(panel)
    if not args.no_verifier:
        rep["verification"] = run_verifier(panel, panel.parent / "verify_report.json")
    out = panel.parent / "selftest.json"
    atomic_write_json(out, rep)
    print(f"selftest report: {out}", flush=True)
    return 0


def cmd_all(args) -> int:
    """Full deliverable: smoke + self-tests + determinism, full build, merge, coverage."""
    days = sim.dev_days()
    smoke = smoke_days(days)
    print(f"[1/5] smoke panel ({len(smoke)} days)", flush=True)
    build_parts(smoke, SMOKE_DIR, force=True)
    smoke_panel = merge_parts(SMOKE_DIR)
    rep = selftest(smoke_panel, anchors=None, verbose=True)
    rep["verification"] = run_verifier(smoke_panel, SMOKE_DIR / "verify_report.json")
    tmpdir = Path(tempfile.mkdtemp(prefix="atlas_smoke_det_"))
    try:
        build_parts(smoke, tmpdir, force=True, verbose=False)
        panel2 = merge_parts(tmpdir)
        h1, h2 = sha256_file(smoke_panel), sha256_file(panel2)
        if h1 != h2:
            raise SelftestFailure("(f) smoke runs are not byte-identical")
        print(f"  [ok] (f) determinism: {h1[:16]}", flush=True)
        rep["determinism"] = {"sha256_run1": h1, "sha256_run2": h2, "identical": True}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    rep["c1_check"] = compare_c1(smoke_panel)
    atomic_write_json(SMOKE_DIR / "selftest.json", rep)

    print(f"[2/5] full build: {len(days)} days", flush=True)
    progress = build_parts(days, ATLAS, force=args.force)
    progress["census_detail"] = progress_census(progress)
    atomic_write_json(ATLAS / "_progress.json", progress)

    print("[3/5] merge", flush=True)
    panel = merge_parts(ATLAS)
    print(f"  panel: {panel} ({sha256_file(panel)[:16]})", flush=True)

    print("[4/5] full-panel self-tests + independent verification", flush=True)
    full = selftest(panel, verbose=True)
    full["verification"] = run_verifier(panel, ATLAS / "verify_report.json")
    full["c1_check"] = compare_c1(panel)
    atomic_write_json(ATLAS / "selftest.json", full)

    print("[5/5] C1 corruption tests + coverage", flush=True)
    corruption_rc = cmd_corruption(argparse.Namespace(panel=str(panel)))
    if corruption_rc != 0:
        raise SelftestFailure("corruption tests failed")
    doc = coverage_report(panel, COVERAGE, c1_check=full["c1_check"], progress=progress,
                          verification=full["verification"])
    print(json.dumps({"rows": doc["rows"], "members": doc["members"],
                      "per_family": {k: {"members": v["members"], "rows": v["rows"],
                                         "anchor_match": v["anchor_match"]}
                                     for k, v in doc["per_family"].items()},
                      "censored": doc["censor_census"]["censored_members"],
                      "censored_rows": doc["censor_census"]["censored_rows"],
                      "condition_after_forced_flat_rows":
                          doc["censor_census"]["condition_after_forced_flat_rows"],
                      "census_totals": doc.get("census_totals")}, indent=1), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ATLAS minute-panel producer")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("smoke", help="build the 20-day smoke panel and test it")
    p.add_argument("--out", default=None)
    p.add_argument("--force", action="store_true")
    p.add_argument("--determinism", action="store_true", help="build twice and compare hashes")
    p.set_defaults(func=cmd_smoke)

    p = sub.add_parser("build", help="build month parts (resumable)")
    p.add_argument("--out", default=None)
    p.add_argument("--all", action="store_true", help="all dev days")
    p.add_argument("--days", default=None, help="explicit comma-separated list")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("merge", help="merge month parts into panel.parquet")
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("coverage", help="write coverage.json")
    p.add_argument("--panel", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--no-c1", action="store_true")
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("selftest", help="run acceptance checks on a built panel")
    p.add_argument("--panel", default=None)
    p.add_argument("--no-verifier", action="store_true",
                   help="skip the independent verifier subprocess")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("corruption", help="prove the C1 assertion fails on corrupted fields")
    p.add_argument("--panel", default=None)
    p.set_defaults(func=cmd_corruption)

    p = sub.add_parser("all", help="smoke + self-tests + full build + merge + coverage")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_all)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
