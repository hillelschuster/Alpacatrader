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

SCHEMA_VERSION = 1
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
    ("v_giveback_5", pl.Float64, "outcome_continuation"),
    ("v_giveback_10", pl.Float64, "outcome_continuation"),
    ("v_giveback_15", pl.Float64, "outcome_continuation"),
    ("v_giveback_20", pl.Float64, "outcome_continuation"),
    ("giveback_fired_5", pl.Boolean, "outcome_continuation"),
    ("giveback_fired_10", pl.Boolean, "outcome_continuation"),
    ("giveback_fired_15", pl.Boolean, "outcome_continuation"),
    ("giveback_fired_20", pl.Boolean, "outcome_continuation"),
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
    ("member_last_et", pl.Int32, "key"),
]

# outcome columns that SCHEMA.md places under "Outcome columns (strictly after t)"
OUTCOME_COLUMNS = tuple(
    name for name, _d, fam in COLUMNS
    if fam in ("outcome_level", "outcome_continuation", "outcome_path")
)
# SCHEMA.md declares these two "null if none" (no later high at all), so they may
# be null while next_open exists; every other outcome column may not.
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
    "member_last_et": ("ET of the last bar of this member's tracked window; equals "
                       "session_end when the member traded into the close"),
}

CONVENTIONS = {
    "causality": "state uses only bars with et <= t plus the fill; no session aggregate after t",
    "execution": "a decision at completed bar t executes at the open of bar t+1",
    "last_bar": "at the member's last tracked bar, next_open/next_et and every outcome column are null",
    "friction": "none in the panel; analyses apply bps_total/2 per side on the executed action",
    "no_fabrication": "a missing input yields null, never a filled or carried value",
    "fill_bar": "first bar of the member with et >= the anatomy fill et",
    "session_end": "day-level session end (959, or 779 on the 7 half-days), repeated per row",
    "member_window": "fill bar .. last member bar with et <= session_end (see member_last_et)",
    "block1": "days <= 2023-12-31; block2: days >= 2025-02-01",
    "sort": "rows sorted by (sleeve_day, family, entry_rank, et)",
}

AMBIGUITIES = [
    {
        "schema": "`family` | `A_pm` (fill at ET 571) or `B600` (fill at ET 601)",
        "issue": "the anatomy fill et is not always 571/601 (A_pm fills at 570 on most days, "
                 "B600 at 600, and a name can fill later inside a gapped tape)",
        "resolution": "entry_et/entry_px are taken verbatim from the anatomy fill (the engine's "
                      "own source); the ET in the schema text is read as nominal. The realised "
                      "entry_et distribution is reported under tape_shape.entry_et_histogram.",
    },
    {
        "schema": "`session_end` | ET of the last bar used for this day",
        "issue": "ambiguous between the day-level close (959/779) and the member's own last bar "
                 "(a halted name may stop printing before the close)",
        "resolution": "session_end is the day-level close from phase2_session_calendar.json, "
                      "repeated on every row; the member's actual tape end is exposed as the "
                      "producer addition member_last_et. Members whose tape ends before the close "
                      "are counted under tape_shape.members_without_final_bar.",
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
                      "first close <= running_high * (1 - g/100); when the trigger is the last bar, "
                      "or when it never triggers, the exit is the forced flat close of the member's "
                      "last bar (giveback_fired_* records whether it fired).",
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

    # --- outcomes -------------------------------------------------------- #
    nan1 = np.full(m, np.nan)
    next_open = nan1.copy()
    next_et = nan1.copy()
    v_hold_flat = nan1.copy()
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
    v_giveback = {lv: nan1.copy() for lv in GIVEBACK_LEVELS}

    if m > 1:
        nxt = op_w[1:]
        next_open[:-1] = nxt
        next_et[:-1] = et[1:]
        level_ret[:-1] = nxt / entry_px - 1.0
        close_end = cl_w[-1]
        v_hold_flat[:-1] = close_end / nxt - 1.0
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
                px, did = _giveback_exit(cl_w, hi_w, running_high, op_w, j, lv)
                fired[lv][j] = 1.0 if did else 0.0
                v_giveback[lv][j] = px / op_w[j + 1] - 1.0
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
        "giveback_fired_5": fired[5],
        "giveback_fired_10": fired[10],
        "giveback_fired_15": fired[15],
        "giveback_fired_20": fired[20],
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
        "member_last_et": np.full(m, int(et[-1]), dtype=np.int64),
    }
    return cols


def _giveback_exit(cl_w: np.ndarray, hi_w: np.ndarray, rh_w: np.ndarray,
                   op_w: np.ndarray, j: int, level: int) -> tuple[float, bool]:
    """Exit price of "leave at the open after the first close g% below the
    running high as of that bar"; the forced flat close when it never triggers
    (or when it triggers on the session's last bar)."""
    g = level / 100.0
    c = cl_w[j + 1:]
    h = np.maximum.accumulate(hi_w[j + 1:])
    run = np.maximum(h, rh_w[j])
    hit = np.flatnonzero(c <= run * (1.0 - g) + GIVEBACK_TOL_REL * run)
    if hit.size == 0:
        return float(cl_w[-1]), False
    gi = j + 1 + int(hit[0])
    if gi < cl_w.size - 1:
        return float(op_w[gi + 1]), True
    return float(cl_w[-1]), True


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


def tape_shape(df: pl.DataFrame) -> dict:
    """Descriptive shape of the tracked tape (coverage diagnostics)."""
    members = df.group_by(["sleeve_day", "family", "ticker"]).agg([
        pl.col("member_last_et").first().alias("last_et"),
        pl.col("session_end").first().alias("session_end"),
        pl.col("entry_et").first().alias("entry_et"),
        pl.col("entry_px").first().alias("entry_px"),
        pl.col("open0930").first().alias("open0930"),
        pl.len().alias("n_bars"),
        pl.col("session_peak_et").first().alias("peak_et"),
        pl.col("session_peak_ret_from_entry").first().alias("peak_ret"),
        pl.col("session_close_ret_from_entry").first().alias("close_ret"),
    ])
    short = members.filter(pl.col("last_et") < pl.col("session_end"))
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
            ["sleeve_day", "family", "ticker", "last_et", "session_end"]).head(20).to_dicts(),
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
                    c1_check: dict | None = None, progress: dict | None = None) -> dict:
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

    # (a2) member set == the C1 engine's entries, on the days the panel covers
    for fam, rec in compare_c1(panel_path).items():
        if not rec.get("available"):
            results.append({"check": f"(a2) {fam}: C1 tickets unavailable", "ok": True})
            continue
        _check(rec["n_only_in_panel"] == 0 and rec["n_only_in_c1"] == 0
               and rec["n_entries_metrics"] == rec["expected_n_entries"],
               f"(a2) {fam} member set == C1 {rec['cell']} tickets "
               f"(panel-only {rec['n_only_in_panel']}, c1-only {rec['n_only_in_c1']}, "
               f"n_entries {rec['n_entries_metrics']})", results)

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
             .agg([pl.col("bar_index").diff().drop_nulls().min().alias("dmin"),
                   pl.col("bar_index").first().alias("first"),
                   pl.col("et").diff().drop_nulls().min().alias("et_min_diff"),
                   pl.len().alias("n")]))
    bad_mono = chk.filter((pl.col("dmin") != 1) | (pl.col("first") != 0) | (pl.col("et_min_diff") <= 0))
    _check(bad_mono.height == 0,
           f"(c) bar_index is 0..n-1 and et strictly increasing ({bad_mono.height} offenders)", results)

    # (d) five pseudo-random rows recomputed from the raw bars ------------- #
    import random
    rng = random.Random(20260925)
    pick = df.sample(n=min(5, df.height), seed=20260925)
    recomputed = []
    mismatches = []
    for row in pick.iter_rows(named=True):
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
           f"(d) 5 sampled rows recompute exactly from raw bars ({len(mismatches)} mismatches)", results)

    # (e) outcome columns null exactly when next_open is null -------------- #
    # Two directions.  `next_open is null` => null is absolute (no exception).
    # The converse has exactly one documented exception: SCHEMA.md defines
    # `bars_to_next_high` / `dd_before_next_high` as "null if none" — they are
    # null when the member never sets another high, which is not a missing input.
    offenders_forward = {}
    offenders_backward = {}
    for col in OUTCOME_COLUMNS:
        bad_fwd = df.filter(pl.col("next_open").is_null() & pl.col(col).is_not_null())
        if bad_fwd.height:
            offenders_forward[col] = int(bad_fwd.height)
        if col in NO_EVENT_NULL_OUTCOMES:
            continue
        bad_bwd = df.filter(pl.col("next_open").is_not_null() & pl.col(col).is_null())
        if bad_bwd.height:
            offenders_backward[col] = int(bad_bwd.height)
    _check(not offenders_forward,
           f"(e) no outcome column is populated when next_open is null ({offenders_forward})",
           results)
    _check(not offenders_backward,
           f"(e) every event-defined outcome column is populated when next_open exists "
           f"({offenders_backward}; no-event-null exceptions: {list(NO_EVENT_NULL_OUTCOMES)})",
           results)
    no_next_high = df.filter(pl.col("next_open").is_not_null()
                             & pl.col("bars_to_next_high").is_null()).height
    results.append({"check": f"(e) bars_to_next_high null without a next high: {no_next_high} rows "
                             f"(documented no-event-null; {100.0 * no_next_high / max(df.height, 1):.1f}%)",
                    "ok": True})

    rep = {
        "panel": str(panel_path),
        "panel_sha256": sha256_file(panel_path),
        "rows": int(df.height),
        "members": int(df.select(["sleeve_day", "family", "ticker"]).unique().height),
        "family_member_counts": counts,
        "checks": results,
        "sampled_rows": recomputed,
        "all_ok": True,
    }
    if verbose:
        for r in results:
            print(f"  [ok] {r['check']}", flush=True)
    return rep


def recompute_row(row: dict, sem: dict[str, int]) -> dict:
    """Independent, literal recomputation of seven path statistics from raw bars.

    Deliberately naive: reads the day's parquet directly (no producer geometry)
    and walks the tape with plain Python loops.
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
    if j + 1 >= len(ets):
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


def compare_c1(panel_path: Path) -> dict:
    """Cross-check the panel's member set against the C1 engine's tickets.

    The comparison is restricted to the days the panel covers, so it is valid
    for the smoke subset as well as for the full 1,066-day panel.
    """
    df = pl.read_parquet(panel_path)
    days_in_panel = set(df["sleeve_day"].unique().to_list())
    out = {}
    for fam, (cell, expected) in C1_CELLS.items():
        cell_dir = ROOT / "factory/artifacts/basket/phase2/F1_C1/F1" / cell
        tickets = cell_dir / "tickets.parquet"
        if not tickets.exists():
            out[fam] = {"cell": cell, "available": False, "expected_n_entries": expected}
            continue
        tk = pl.read_parquet(tickets).filter(pl.col("sleeve_day").is_in(list(days_in_panel)))
        metrics = json.loads((cell_dir / "metrics.json").read_text())
        panel_keys = set(map(tuple, df.filter(pl.col("family") == fam)
                             .select(["sleeve_day", "ticker"]).unique().rows()))
        c1_keys = set(map(tuple, tk.select(["sleeve_day", "ticker"]).unique().rows()))
        px_panel = {(r["sleeve_day"], r["ticker"]): round(float(r["entry_px"]), 10)
                    for r in df.filter(pl.col("family") == fam)
                    .select(["sleeve_day", "ticker", "entry_px"]).unique().iter_rows(named=True)}
        px_c1 = {(r["sleeve_day"], r["ticker"]): round(float(r["entry_px"]), 10)
                 for r in tk.select(["sleeve_day", "ticker", "entry_px"]).unique().iter_rows(named=True)}
        px_bad = [k for k in (panel_keys & c1_keys) if px_panel.get(k) != px_c1.get(k)]
        out[fam] = {
            "cell": cell,
            "available": True,
            "n_entries_metrics": int(metrics.get("n_entries", -1)),
            "expected_n_entries": expected,
            "panel_members": len(panel_keys),
            "c1_members": len(c1_keys),
            "only_in_panel": sorted(panel_keys - c1_keys)[:50],
            "only_in_c1": sorted(c1_keys - panel_keys)[:50],
            "n_only_in_panel": len(panel_keys - c1_keys),
            "n_only_in_c1": len(c1_keys - panel_keys),
            "entry_px_mismatches": sorted(px_bad)[:20],
        }
    return out


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


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
    c1 = compare_c1(panel)
    for fam, rec in c1.items():
        if rec.get("available"):
            print(f"  [c1] {fam}: panel={rec['panel_members']} c1_subset={rec['c1_members']} "
                  f"only_in_panel={rec['n_only_in_panel']} only_in_c1={rec['n_only_in_c1']}",
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
    atomic_write_json(out / "selftest.json", {**rep, "determinism": determinism, "c1_check": c1})
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
    doc = coverage_report(panel, out, c1_check=c1, progress=progress)
    print(f"coverage: {out}", flush=True)
    print(json.dumps({"rows": doc["rows"], "members": doc["members"],
                      "per_family": {k: v["members"] for k, v in doc["per_family"].items()}},
                     indent=1), flush=True)
    return 0


def cmd_selftest(args) -> int:
    panel = Path(args.panel) if args.panel else PANEL
    rep = selftest(panel, verbose=True)
    c1 = compare_c1(panel)
    rep["c1_check"] = c1
    out = panel.parent / "selftest.json"
    atomic_write_json(out, rep)
    print(f"selftest report: {out}", flush=True)
    return 0


def cmd_all(args) -> int:
    """Full deliverable: smoke + self-tests + determinism, full build, merge, coverage."""
    days = sim.dev_days()
    smoke = smoke_days(days)
    print(f"[1/4] smoke panel ({len(smoke)} days)", flush=True)
    build_parts(smoke, SMOKE_DIR, force=True)
    smoke_panel = merge_parts(SMOKE_DIR)
    rep = selftest(smoke_panel, anchors=None, verbose=True)
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

    print(f"[2/4] full build: {len(days)} days", flush=True)
    progress = build_parts(days, ATLAS, force=args.force)
    progress["census_detail"] = progress_census(progress)
    atomic_write_json(ATLAS / "_progress.json", progress)

    print("[3/4] merge", flush=True)
    panel = merge_parts(ATLAS)
    print(f"  panel: {panel} ({sha256_file(panel)[:16]})", flush=True)

    print("[4/4] full-panel self-tests + coverage", flush=True)
    full = selftest(panel, verbose=True)
    full["c1_check"] = compare_c1(panel)
    atomic_write_json(ATLAS / "selftest.json", full)
    doc = coverage_report(panel, COVERAGE, c1_check=full["c1_check"], progress=progress)
    print(json.dumps({"rows": doc["rows"], "members": doc["members"],
                      "per_family": {k: {"members": v["members"], "rows": v["rows"],
                                         "anchor_match": v["anchor_match"]}
                                     for k, v in doc["per_family"].items()},
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
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("all", help="smoke + self-tests + full build + merge + coverage")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_all)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
