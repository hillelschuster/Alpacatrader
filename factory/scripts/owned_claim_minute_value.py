#!/usr/bin/env python3
"""FULL-MINUTE OWNED-CLAIM VALUE — minute-resolution continuation value, discovery only.

Why this exists
---------------
The tested owned-claim machines (``owned_claim_events`` -> thin to ~8 event hops ->
``owned_claim_value`` -> ``owned_claim_replay``) observe a claim at a handful of event
minutes.  The economic claim under test is that monetary continuation lives inside the
1..30 actual-minute windows between those observations and that observation thinning can
miss its death.  That is a HYPOTHESIS, not a finding.  This producer builds the full
minute grid so the question can be asked honestly; it does not classify monsters and it
does not substitute the roster.

Two producers, one contract
--------------------------
``owned_claim_minute_value.py build``  -> ``<out>/minutes/<day>.parquet`` + ``manifest.json``
    Every actual minute of every filled original claim, past-only features, past-only
    event memory, and the fixed forward-dollar labels.
``owned_claim_minute_value.py fit``    -> ``<out>/scores_n3/<day>.parquet``,
    ``<out>/scores_n5/<day>.parquet``, ``metadata.json``, ``minute_diagnostics.json``
    Direct mean-dollar models per horizon, per roster size, per fixed chronological fold.

Causal feature provenance (nothing here is invented)
----------------------------------------------------
* Every ``feature_*`` column comes from ``owned_claim_events.load_day`` — the corrected
  causal panel, the as-of observable peer/scanner context (``owned_claim_observable_peers``)
  and the past-history grid.  Those columns are read verbatim; none is recomputed, renamed,
  selected or dropped here.
* The event memory reuses ``owned_claim_events.flags_for(row, prev, memory)`` — the
  canonical past-only flag function — called on EVERY post-fill minute.  Core is not
  modified.  No minute is dropped for being uneventful.

Declared memory semantics (dense vs thinned — read this before comparing to core)
--------------------------------------------------------------------------------
``flags_for`` receives the same ``memory`` dict core builds and the same ``prev`` row, so
the flag values, the cumulative per-kind counters and ``feature_event_heartbeat`` are
byte-identical to core's rule at any minute.  Two dwell variables are ADDED rather than
substituted:

  ``feature_minutes_since_emitted``  t - the last EMITTED core event, where "emitted"
     replicates ``owned_claim_events.build_events`` exactly (any flag, or an important
     flag, or 5 clock minutes since the last emitted event).  This is core's own
     ``memory["last_event"]``; nothing is thinned in this corpus.
  ``feature_minutes_since_last_event``  t - the last minute at which ANY canonical flag
     fired, with NO thinning.  This is the dense ruler: the two variables differ exactly
     where core's 5-minute throttle suppressed a quiet-but-real event.
  ``feature_event_ever``  0/1 whether any flag has fired yet.
Both are past-only (they depend on flags at minutes <= t) and neither reads a future
price, future status or label.  ``heartbeat`` keeps core's THINNED-EMITTED semantics on
purpose: if it had been redefined on the dense clock it would no longer be comparable to
the eight-event-hop control.

Labels (fixed forward actual minutes, UNKNOWN is not zero)
----------------------------------------------------------
For a claim minute ``t`` with an executable current liquidation (``sell_px`` finite and
positive, ``sell_et`` known and inside the session) and horizon ``h`` in
``(1,3,5,10,15,30,60,120)`` actual clock minutes:

    j    = first grid minute with time >= t + h
    value   = sell_px[j] / sell_px[t] - 1
    label_h = sell_px[t] / fill_px * value * (1 - s) / (1 + s),   s = 0.005

with every one of core's ``build_events`` guard clauses retained: ``sell_et[t]`` must fall
inside the horizon, ``sell_et[j] > sell_et[t]``, ``sell_et[j] <= session_end``, and the
label is NaN (UNKNOWN) otherwise.  ``(1-s)/(1+s)`` reproduces core's inline ``* 0.995 /
1.005`` exactly.  UNKNOWN is never filled with zero, never imputed, never carried into a
fit mask as a value.

Units: per ORIGINAL CLAIM CASH dollar, not per gross entry dollar
----------------------------------------------------------
``label_h`` is the canonical ``owned_claim_events`` ``increment_{h}_original_dollar`` unit:
the dollar gain of holding ``h`` minutes instead of selling at ``t``, expressed per unit of
CLAIM CASH, i.e. the gross-entry-dollar gain multiplied by ``(1-s)/(1+s) = 0.995/1.005``.
Every per-dollar diagnostic in this producer (baselines, means, premiums) is in that same
cash unit.  No magnitude unit identity is claimed against the engine and nothing is rescaled: the
unchanged stop-only engine consumes the SIGN of ``pred_*`` alone (release when ``pred < 0``),
so any positive constant transform of the forecast produces identical actions.  Only the stop
mode is replayed here; a magnitude-consuming mode (core's reentry/allocate hurdle arithmetic)
is not exercised, and would require its own explicit unit decision.
Models
------
Independent direct regressions per horizon (no value iteration, no iteration over a policy,
no invented framework): ``objective=regression`` on the raw dollar increment, with
``owned_claim_value.PARAMS`` and 80 rounds taken verbatim as the declared existing
benchmark.  Fits are TRAIN-only per the fixed expanding folds
``0:150 -> 150:300``, ``0:300 -> 300:450``, ``0:450 -> 450:533`` of the 533 discovery
days, separately for N3 and N5 (separate corpora, separate score directories — an N3
trained view is never scored onto an N5 book by this producer, and the replay consumer is
contractually forbidden from crossing them).  No clipping, no winsorization, no
hyper-parameter search, no TEST tuning.  The declared baseline is the WEIGHTED train mean
under the same day/claim weights the learner sees, so a naive constant forecast and the
fitted model are compared in matched units and matched weighting.

Calendar feasibility, forced exits, and UNKNOWN forecasts
---------------------------------------------------------
``t + h <= session_end`` is a calendar fact known at ``t``, and it is the ONLY feasibility
guard -- it gates ACTIONS, never labels.  Where a horizon's target is calendar-infeasible the
claim cannot be held to it and the declared action is exit now, whose owned increment is zero;
because the unchanged engine releases only on ``pred < 0``, a 0 would silently read as HOLD, so
those rows publish the declared action sentinel ``CALENDAR_EXIT`` (an action code, never a
forecast, never a padded horizon).  ``pred_max`` excludes infeasible horizons from its maximum
and uses ``CALENDAR_EXIT`` only where NO horizon is feasible.  Feasibility is recoverable from
the published ``feasible_h{h}`` / ``n_feasible_h`` columns, and no diagnostic statistic is
computed from sentinel rows.

UNKNOWN is never an action: if a row has calendar-feasible horizons but no finite forecast the
producer aborts instead of publishing a value the engine would read as a silent HOLD.

``pred_max`` is an explicit optimism ASSUMPTION, not an optimum.  The gap between its mean and
the mean of the per-horizon means is a MAX-SELECTION PREMIUM -- pure arithmetic on forecasts,
with no outcomes involved.  Converting it into a quantified statistical bias would require
realized outcomes and is deliberately not done here.  The per-horizon views ship beside it so
the assumption can be tested instead of believed.

Isolation and TEST discipline
-----------------------------
* Only ``lifecycle_study`` discovery days are ever read.  Validation days, the protected
  half and FREEZE markers are never opened.
* Scores contain NO label, status or outcome column at all, so a downstream action cannot
  be gated on label availability.  Test rows are scored whether or not their labels exist.
* Test-outcome diagnostics are reported in a block explicitly marked as not used for any
  selection; the horizon set, the feature set, the parameters and the policy view are all
  fixed before any TEST number is read.
* ``--smoke-days`` produces a non-evidence corpus under ``<out>/smoke`` with rescaled fold
  windows, identical model parameters; ``--folds``/``--days`` subsets are marked
  non-evidence in metadata.

State plane / memory budget
---------------------------
The full minute grid is ~4-5M rows x ~130 float features, so nothing is held as a Float64
polars frame plus a global copy: ``build`` walks one claim at a time into bounded float32
columns and writes one day parquet per write (no all-grid python row dicts); ``fit``
projects only the needed columns per day, stages the fold corpus into a disk-backed
``np.memmap`` (float32), gathers only the rows a horizon actually fits, and releases the
model, the LightGBM Dataset and the training matrix between horizons and between N3/N5.
Native threads are pinned to 2 before numpy/lightgbm import.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import sys
from pathlib import Path

# bound native threads before numpy/polars/lightgbm import (parent runs one heavy job)
for _v in (
    "POLARS_MAX_THREADS",
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(_v, "2")

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lifecycle_study as ls  # noqa: E402
import lightgbm as lgb  # noqa: E402
import owned_claim_events as ec  # noqa: E402
import owned_claim_value as benchmark  # noqa: E402  (declared existing LightGBM params)

SCRIPT = Path(__file__).resolve()
EVENTS_SRC = Path(ec.__file__).resolve()
PEERS_SRC = Path(ec.observable_peers.__file__).resolve()
BENCH_SRC = Path(benchmark.__file__).resolve()

# ------------------------------------------------------------------ predeclared config
CLOCKS = tuple(ec.CLOCKS)  # (540, 560, 569, 571) — disclosed admission rulers, not truths
NS = (
    3,
    5,
)  # separate original rosters -> separate corpora, models and score directories
FOLDS = ((0, 150, 150, 300), (0, 300, 300, 450), (0, 450, 450, 533))
N_DISCOVERY = 533
HORIZONS = (1, 3, 5, 10, 15, 30, 60, 120)  # ACTUAL clock minutes, fixed before any fit
ROUNDS = 80  # identical to owned_claim_value's fixed rounds; not a CLI knob
PARAMS = dict(benchmark.PARAMS)  # declared existing benchmark, imported verbatim and pinned
QUANTILES = (0.0, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 1.0)
VIEW_NAMES = ("max",) + tuple(f"h{h}" for h in HORIZONS)

FEE_SIDE = 0.005
FEE_RATIO = (1.0 - FEE_SIDE) / (1.0 + FEE_SIDE)
assert FEE_RATIO == 0.995 / 1.005, "fee ratio must equal owned_claim_events' 0.995/1.005"
# ACTION sentinel, not a forecast.  owned_claim_replay.simulate releases only on `pred < 0`,
# so a calendar-forced exit whose owned increment is exactly zero cannot be expressed as 0
# (which would silently read as HOLD).  Where a horizon's target is calendar-INFEASIBLE
# (t + h > session_end) the claim cannot be held to that target and the declared action is
# exit now, published as this constant.  It is applied ONLY to calendar-infeasible targets;
# no diagnostic mean, quantile or calibration is computed from sentinel-padded rows.
CALENDAR_EXIT = -1.0e6

# Keys `owned_claim_events.flags_for` reads off its row/prev/memory arguments.  A missing
# key is a hard error here, never a silent substitute or a re-implementation.
FLAG_KEYS = (
    "t",
    "session_end",
    "fill_et",
    "ret_fill",
    "dd_from_high",
    "ret5",
    "peak_gain",
    "minutes_since_high",
    "dd_velocity5",
    "drank5",
    "reclaim_fill_now",
)
# owned_claim_events.build_events' thinning rule, replicated ONLY to expose a core-comparable
# emitted-event dwell next to the dense one.  Nothing in this corpus is thinned.
EMIT_IMPORTANT = (
    "entry",
    "profit5",
    "flush",
    "reclaim",
    "resurrection",
    "terminal",
    "fade_ruler",
)
EMIT_GAP = 5
# ---------------------------------------------------------------------------
# DERIVED-PLANE HARD WHITELIST (causality contract, enforced, not advisory).
#
# Every fitted predictor is either (a) a `feature_*` column produced by
# owned_claim_events.load_day, which core's causality harness already certifies as
# past-only, or (b) one of the names below, each derived exclusively from canonical
# flag memory over completed minutes at or before t plus the calendar session end and
# the already-executed fill time.
#
# NOTHING derived from sell_px / sell_et / sell_volume / exit_* / mark_* may enter the
# predictor plane.  Those columns say whether and when the NEXT observed open arrives --
# a future-availability predicate, not a state observable at t.  (A former
# `feature_sellable` computed exactly that and was removed; nothing replaces it.)
# ---------------------------------------------------------------------------
DERIVED_FEATURES = (
    tuple(f"feature_event_{k}" for k in ec.KINDS)
    + tuple(f"feature_event_count_{k}" for k in ec.KINDS)
    + (
        "feature_event_ever",
        "feature_minutes_since_last_event",
        "feature_minutes_since_emitted",
        "feature_minutes_since_fill",
    )
)
# provenance of every derived name, asserted by check_features
DERIVED_PROVENANCE = {
    "feature_event_ever": "canonical flags_for fired at or before t",
    "feature_minutes_since_last_event": "dense dwell to the last canonical flag minute <= t",
    "feature_minutes_since_emitted": (
        "core's emitted-event timer (build_events thinning rule replicated), still past-only"
    ),
    "feature_minutes_since_fill": (
        "t - fill_et with fill_et already executed at or before t (core's own fade ruler term)"
    ),
}
KEY_COLS = ("day", "clock", "rank", "ticker", "t")
EXTRA_COLS = ("session_end", "fill_et", "fill_px")
LABEL_COLS = tuple(f"label_h{h}" for h in HORIZONS)
# future-execution columns: reading any of these into a predictor is a causality break
EXECUTION_COLUMNS = frozenset(
    {
        "sell_px",
        "sell_et",
        "sell_volume",
        "exit_px",
        "exit_et",
        "mark_px",
        "mark_et",
        "fill_volume",
        "status",
    }
)
# substrings that may never appear in a fitted predictor name (leakage/future tripwire)
FORBIDDEN_SUBSTRINGS = (
    "next_sell",
    "sell_px",
    "sell_et",
    "sell_volume",
    "sellable",
    "exit_px",
    "exit_et",
    "mark_px",
    "mark_et",
    "available",
    "availability",
    "fillable",
    "has_print",
    "next_open",
    "label_",
    "_target",
    "outcome",
    "status",
)
# `feature_fill_px` (the entry price, known at the fill and therefore strictly past at every
# post-fill minute) is a legitimate canonical causal feature and is kept, exactly as
# owned_claim_value's allowlist keeps it.  Only execution-LABEL columns are forbidden here.
# smoke fold windows rescale the SAME fixed proportions as the full folds (150/533, 450/533)
SMOKE_TRAIN1 = (281, 1000)
SMOKE_TRAIN2 = (563, 1000)


# ------------------------------------------------------------------ small helpers
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def qstats(a) -> dict | None:
    a = np.asarray(a, dtype=np.float64)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return None
    out = {f"p{int(q * 100):02d}": float(np.quantile(a, q)) for q in QUANTILES}
    out.update({"n": int(a.size), "mean": float(a.mean()), "std": float(a.std())})
    return out


def tail_exposure(a) -> dict:
    """Unscaled dollar-tail exposure. No clipping, no winsorization, anywhere."""
    a = np.asarray(a, dtype=np.float64)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return {"n": 0}
    out = {"n": int(a.size), "min": float(a.min()), "max": float(a.max())}
    for thr in (0.10, 0.30, 1.0, 2.0, 5.0):
        out[f"frac_gt_{thr:g}"] = float((a > thr).mean())
    pos = a[a > 0]
    if pos.size >= 20:
        k = max(1, int(0.01 * pos.size))
        top = np.sort(pos)[-k:]
        out["top1pct_share_of_positive_mass"] = float(top.sum() / pos.sum())
    else:
        out["top1pct_share_of_positive_mass"] = None
    return out


def observed_coverage(frame: pl.DataFrame, feats: list[str]) -> dict:
    if not feats or frame.height == 0:
        return dict.fromkeys(feats)
    row = frame.select(
        [
            pl.col(c).cast(pl.Float64, strict=False).is_finite().fill_null(False).mean().alias(c)
            for c in feats
        ]
    ).row(0)
    return {c: (None if v is None else round(float(v), 6)) for c, v in zip(feats, row, strict=True)}


def num(frame: pl.DataFrame, col: str) -> np.ndarray:
    return frame[col].cast(pl.Float64, strict=False).to_numpy().astype(np.float64)


def frame_schema(feats: list[str]) -> dict:
    schema: dict = {
        "day": pl.Utf8,
        "clock": pl.Int64,
        "rank": pl.Int64,
        "ticker": pl.Utf8,
        "t": pl.Int64,
        "session_end": pl.Int64,
        "fill_et": pl.Int64,
        "fill_px": pl.Float32,
    }
    for c in feats:
        schema[c] = pl.Float32
    for c in LABEL_COLS:
        schema[c] = pl.Float32
    return schema


def score_schema() -> dict:
    """Explicit dtypes of a published scores_n{n}/<day>.parquet partition.

    Declared so a test day with zero rows still writes a typed, hashable file instead of going
    missing, and so every partition shares one schema.
    """
    schema: dict = {
        "day": pl.Utf8,
        "clock": pl.Int64,
        "rank": pl.Int64,
        "ticker": pl.Utf8,
        "t": pl.Int64,
        "session_end": pl.Int64,
        "fill_et": pl.Int64,
    }
    for h in HORIZONS:
        schema[f"pred_h{h}"] = pl.Float32
    for h in HORIZONS:
        schema[f"feasible_h{h}"] = pl.Int8
    schema.update(
        {
            "pred_max": pl.Float32,
            "sel_h": pl.Int16,
            "n_feasible_h": pl.Int8,
            "calendar_exit_cells": pl.Int8,
            "fold": pl.Int32,
        }
    )
    return schema


SCORE_SCHEMA = score_schema()


def check_features(feats: list[str], panel_feats: list[str] | None = None) -> list[str]:
    """Hard predictor allowlist.

    Refuses any future-execution/label-shaped name outright, and refuses any derived column
    that is not one of the whitelisted event-memory names.  Nothing is advisory.
    """
    for c in feats:
        low = c.lower()
        hit = [s for s in FORBIDDEN_SUBSTRINGS if s in low]
        if hit:
            raise SystemExit(
                f"predictor allowlist violation: '{c}' matches future-execution/label tripwire "
                f"{hit}; refusing to fit on it"
            )
    derived = [c for c in feats if panel_feats is None or c not in panel_feats]
    unknown = [
        c
        for c in derived
        if c not in DERIVED_PROVENANCE
        and not c.startswith(("feature_event_", "feature_event_count_"))
    ]
    if unknown:
        raise SystemExit(
            f"derived predictors outside the hard whitelist: {unknown}; the derived plane may "
            "contain only the declared past-only event-memory columns"
        )
    if len(feats) < 10:
        raise SystemExit(f"implausible feature count {len(feats)}: {feats[:8]}")
    return feats


# ------------------------------------------------------------------ per-claim minute walk
def claim_minute_frame(claim: pl.DataFrame, feats: list[str], day: str) -> pl.DataFrame | None:
    """One filled claim's FULL post-fill minute grid: features, memory, forward labels."""
    n = claim.height
    if n == 0:
        return None
    times = claim["t"].to_numpy().astype(np.int64)
    # Grid completeness matters: histories are clock minutes, not printed bars.
    if np.any(np.diff(times) != 1):
        raise SystemExit(f"{day}: noncontiguous minute grid for a claim; refusing")
    if claim["status"][0] != "filled":
        return None
    fill_px = float(claim["fill_px"][0])
    fill_et = float(claim["fill_et"][0])
    session_end = float(claim["session_end"][0])
    if not (np.isfinite(fill_et) and np.isfinite(session_end)):
        return None
    fill_et, session_end = int(fill_et), int(session_end)

    # sell_px / sell_et are the NEXT observed open: they belong to the label plane only and
    # are never read into a predictor (see DERIVED_FEATURES / EXECUTION_COLUMNS).
    sell_px = num(claim, "sell_px")
    sell_et = num(claim, "sell_et")

    # ---- forward fixed-minute labels (UNKNOWN = NaN, never zero, never imputed)
    labels: dict[int, np.ndarray] = {}
    current_ok = np.isfinite(sell_px) & (sell_px > 0) & np.isfinite(sell_et)
    for h in HORIZONS:
        j = np.searchsorted(times, times + h, side="left")
        inside = j < n
        jj = np.minimum(j, n - 1)
        future_ok = (
            inside
            & np.isfinite(sell_px[jj])
            & (sell_px[jj] > 0)
            & np.isfinite(sell_et[jj])
            & (sell_et[jj] > sell_et)
            & (sell_et[jj] <= session_end)
            & (sell_et <= times + h)  # core's rule: the current sale must land inside the horizon
        )
        ok = current_ok & future_ok
        value = np.full(n, np.nan, dtype=np.float64)
        if np.isfinite(fill_px) and fill_px > 0 and ok.any():
            idx = ok
            # current_sell/fill * (future_sell/current_sell - 1) == (future_sell -
            # current_sell)/fill is the GROSS-entry-dollar gain of holding instead of selling
            # now; multiplying by (1-s)/(1+s) restates it per ORIGINAL CLAIM CASH dollar,
            # which is the canonical owned_claim_events increment_{h}_original_dollar unit
            # and the unit owned_claim_replay.simulate denominates in (amount = 1/n cash).
            value[idx] = (
                (sell_px[idx] / fill_px) * (sell_px[jj][idx] / sell_px[idx] - 1.0) * FEE_RATIO
            )
        labels[h] = value

    # ---- past-only event memory, canonical flags_for on EVERY post-fill minute
    missing = [k for k in FLAG_KEYS if k not in claim.columns]
    if missing:
        raise SystemExit(f"{day}: panel lacks canonical flag columns {missing}; refusing")
    memo = claim.select(list(FLAG_KEYS)).to_dicts()
    kinds_index = {k: i for i, k in enumerate(ec.KINDS)}
    ev_flag = np.zeros((n, len(ec.KINDS)), dtype=np.float32)
    ev_count = np.zeros((n, len(ec.KINDS)), dtype=np.float32)
    ever = np.zeros(n, dtype=np.float32)
    dense_gap = np.full(n, np.nan, dtype=np.float32)
    emitted_gap = np.full(n, np.nan, dtype=np.float32)
    since_fill = np.full(n, np.nan, dtype=np.float32)
    memory = {
        "seen": False,
        "profit5": False,
        "damaged_below": False,
        "repair": False,
        "fade": False,
        "last_event": -(10**9),
        "count": dict.fromkeys(ec.KINDS, 0),
    }
    dense_last: int | None = None
    prev: dict = {}
    for i, row in enumerate(memo):
        t = int(row["t"])
        if t <= fill_et:
            prev = row
            continue
        # dwell measured BEFORE this minute's flags are folded in (causal as-of t)
        dense_gap[i] = np.nan if dense_last is None else float(t - dense_last)
        emitted_gap[i] = (
            np.nan if memory["last_event"] <= -(10**8) else float(t - memory["last_event"])
        )
        kinds = ec.flags_for(row, prev, memory)
        for k in ec.KINDS:
            j = kinds_index[k]
            if kinds[k]:
                ev_flag[i, j] = 1.0
                if k not in ("heartbeat", "terminal"):
                    memory["count"][k] += 1
            ev_count[i, j] = float(memory["count"][k])
        chosen = any(kinds.values())
        important = any(kinds[k] for k in EMIT_IMPORTANT)
        # core's own emitted-event timer, replicated for comparability (nothing is thinned)
        if chosen and (important or t - memory["last_event"] >= EMIT_GAP):
            memory["last_event"] = t
        if chosen:
            dense_last = t
            ever[i] = 1.0
        since_fill[i] = float(t - fill_et)
        prev = row

    keep = times > fill_et
    if not keep.any():
        return None

    # `feats` = the panel's own causal columns (read verbatim) followed by the event-memory
    # columns this producer derives; the two blocks are concatenated in exactly that order.
    panel_feats = [c for c in feats if c in claim.columns]
    derived_feats = [c for c in feats if c not in claim.columns]
    if derived_feats != list(DERIVED_FEATURES):
        raise SystemExit(
            "declared feature list does not decompose into panel columns plus the whitelisted "
            f"derived columns: {derived_feats}"
        )
    base = claim.select([pl.col(c).cast(pl.Float32, strict=False) for c in panel_feats]).to_numpy()
    if base.dtype != np.float32:
        base = base.astype(np.float32)
    # the derived plane reads ONLY canonical flag memory, the calendar session end and the
    # already-executed fill time.  Column order matches DERIVED_FEATURES exactly.
    extra = np.column_stack(
        [
            ev_flag,
            ev_count,
            ever[:, None],
            dense_gap[:, None],
            emitted_gap[:, None],
            since_fill[:, None],
        ]
    ).astype(np.float32)
    x = np.hstack([base, extra])[keep]

    m = int(keep.sum())
    clock = int(claim["clock"][0])
    rank = int(claim["rank"][0])
    ticker = str(claim["ticker"][0])
    data: dict = {
        "day": pl.Series([day] * m, dtype=pl.Utf8),
        "clock": pl.Series(np.full(m, clock, dtype=np.int64)),
        "rank": pl.Series(np.full(m, rank, dtype=np.int64)),
        "ticker": pl.Series([ticker] * m, dtype=pl.Utf8),
        "t": pl.Series(times[keep].astype(np.int64)),
        "session_end": pl.Series(np.full(m, session_end, dtype=np.int64)),
        "fill_et": pl.Series(np.full(m, fill_et, dtype=np.int64)),
        "fill_px": pl.Series(np.full(m, fill_px, dtype=np.float32)),
    }
    for j, c in enumerate(feats):
        data[c] = pl.Series(x[:, j])
    for h in HORIZONS:
        data[f"label_h{h}"] = pl.Series(labels[h][keep].astype(np.float32))
    return pl.DataFrame(data, schema=frame_schema(feats))


def build_day(day: str, data_root: Path) -> tuple[pl.DataFrame, list[str], list[str], int]:
    """Full minute grid for one day, staged as float32 columns (no all-day row dicts)."""
    panel = ec.load_day(day, data_root)
    base = [c for c in panel.columns if c.startswith("feature_")]
    if not base:
        raise SystemExit(f"{day}: owned_claim_events.load_day produced no feature_* columns")
    # A certified-past-only feature may not be a renamed future-execution column: if the
    # canonical producer ever emits feature_sell_px / feature_exit_et / feature_status, that is
    # a causality break and the build stops instead of fitting on it.
    leaked = [c for c in base if c[len("feature_") :] in EXECUTION_COLUMNS]
    if leaked:
        raise SystemExit(
            f"{day}: load_day emitted future-execution-shaped features {leaked}; refusing to "
            "build a predictor plane from them"
        )
    feats = base + list(DERIVED_FEATURES)
    check_features(feats, base)
    frames, claims = [], 0
    for _, part in panel.partition_by(list(ec.KEYS), as_dict=True, maintain_order=True).items():
        frame = claim_minute_frame(part.sort("t"), feats, day)
        if frame is not None:
            frames.append(frame)
            claims += 1
    del panel
    if not frames:
        return pl.DataFrame(schema=frame_schema(feats)), feats, base, 0
    return pl.concat(frames, how="vertical"), feats, base, claims


# ------------------------------------------------------------------ build command
def cmd_build(a: argparse.Namespace) -> int:
    data_root = ls.bps.resolve_data_root(a.data_root)
    disc = list(ls.load_split(data_root)["discovery_days"])
    if len(disc) != N_DISCOVERY:
        raise SystemExit(f"discovery day count {len(disc)} != {N_DISCOVERY}")
    out = Path(a.out)
    evidence, note = True, None
    if a.smoke_days:
        n = int(a.smoke_days)
        if n < 12:
            raise SystemExit("--smoke-days must be >= 12 to form a tiny train/test split")
        days = disc[:n]
        out = out / "smoke"
        evidence = False
        note = f"non-evidence smoke corpus over the first {n} discovery days"
    elif a.days:
        days = [d.strip() for d in str(a.days).split(",") if d.strip()]
        unknown = [d for d in days if d not in set(disc)]
        if unknown:
            raise SystemExit(f"--days must be discovery days; rejected {unknown[:5]}")
        if not days:
            raise SystemExit("--days selected nothing")
        evidence = False
        note = f"explicit day subset ({len(days)} days), non-evidence"
    else:
        days = list(disc)
    if len(days) != len(set(days)):
        raise SystemExit("duplicate days requested")
    ls.assert_coverage(days, data_root)

    pin = hashlib.sha256(
        (sha256_file(SCRIPT) + sha256_file(EVENTS_SRC) + sha256_file(PEERS_SRC)).encode()
    ).hexdigest()
    minutes_dir = out / "minutes"
    done_dir = out / "_done"
    minutes_dir.mkdir(parents=True, exist_ok=True)
    done_dir.mkdir(parents=True, exist_ok=True)

    rows_by_day: dict[str, int] = {}
    claims_by_day: dict[str, int] = {}
    feats: list[str] | None = None
    panel_feats_seen: list[str] = []
    for i, day in enumerate(days):
        marker = done_dir / f"{day}.json"
        path = minutes_dir / f"{day}.parquet"
        sources = [
            ls.v2_dir(data_root) / kind / f"{day}.parquet" for kind in ("panel", "roster", "tape")
        ]
        sources.append(data_root / "sip/candidates" / f"{day}.json")
        input_hash = hashlib.sha256(
            b"".join(hashlib.sha256(p.read_bytes()).digest() for p in sources)
        ).hexdigest()
        if marker.exists() and path.exists():
            info = json.loads(marker.read_text())
            if (
                info.get("source_sha256") == pin
                and info.get("input_hash") == input_hash
                and info.get("status") == "ok"
            ):
                rows_by_day[day] = int(info["rows"])
                claims_by_day[day] = int(info.get("claims", 0))
                feats = feats or info.get("feature_columns")
                panel_feats_seen = panel_feats_seen or list(info.get("panel_feature_columns") or [])
                continue
        frame, feats, base_feats, claims = build_day(day, data_root)
        panel_feats_seen = base_feats
        tmp = path.with_suffix(".tmp.parquet")
        frame.write_parquet(tmp)
        tmp.replace(path)
        marker.write_text(
            json.dumps(
                {
                    "status": "ok" if frame.height else "empty",
                    "source_sha256": pin,
                    "input_hash": input_hash,
                    "rows": frame.height,
                    "claims": claims,
                    "feature_columns": feats,
                    "panel_feature_columns": base_feats,
                    "columns": frame.columns,
                }
            )
            + "\n"
        )
        rows_by_day[day] = frame.height
        claims_by_day[day] = claims
        if i % 10 == 0:
            print(
                f"[minute build] {i + 1}/{len(days)} {day} minutes={frame.height} claims={claims}",
                flush=True,
            )
    if feats is None:
        raise SystemExit("no day was built; nothing to declare in the manifest")
    manifest = {
        "status": "complete",
        "kind": "DISCOVERY-FULL-MINUTE-OWNED-CLAIM-CORPUS-NOT-VALIDATED-EDGE",
        "producer": "factory/scripts/owned_claim_minute_value.py build",
        "source_sha256": pin,
        "source_hashes": {
            "minute_value_sha256": sha256_file(SCRIPT),
            "events_sha256": sha256_file(EVENTS_SRC),
            "observable_peers_sha256": sha256_file(PEERS_SRC),
        },
        "evidence": evidence,
        "non_evidence_note": note,
        "days": days,
        "rows_by_day": rows_by_day,
        "claims_by_day": claims_by_day,
        "rows_total": int(sum(rows_by_day.values())),
        "feature_columns": feats,
        "panel_feature_columns": panel_feats_seen,
        "derived_feature_columns": list(DERIVED_FEATURES),
        "derived_feature_provenance": DERIVED_PROVENANCE,
        "predictor_plane_contract": (
            "predictors = load_day feature_* (core-certified past-only) + the hard-whitelisted "
            "derived columns; NO sell_px/sell_et/sell_volume/exit_*/mark_*/status-derived or "
            "other future-availability predicate enters the predictor plane (those columns "
            "define labels and engine execution only)"
        ),
        "label_columns": list(LABEL_COLS),
        "event_kinds": list(ec.KINDS),
        "grid": (
            "every actual post-fill minute of every filled original claim; NO thinning, "
            "NO event-hop sampling, one row per (day,clock,rank,ticker,t)"
        ),
        "memory_semantics": (
            "flags_for/memory/counters/heartbeat are core's own rule; "
            "feature_minutes_since_emitted replicates core's thinned emitted-event timer for "
            "comparability while feature_minutes_since_last_event is the dense "
            "(unthinned) dwell. Both are past-only."
        ),
        "labels": (
            "fixed forward actual minutes h=1,3,5,10,15,30,60,120 from the current executable "
            "liquidation to a strictly later in-session observed open; current sell_et must "
            "fall inside the horizon; unavailable = NaN UNKNOWN, never zero"
        ),
        "label_units": (
            "incremental inherited dollars per ORIGINAL CLAIM CASH dollar at the modeled 50bps "
            "fee -- the canonical owned_claim_events increment_{h}_original_dollar unit, i.e. "
            f"cash = GROSS delta x (1-s)/(1+s) = GROSS delta x {FEE_RATIO:.6f}. The raw GROSS "
            f"equivalent of any cash figure is cash/{FEE_RATIO:.6f}. The unchanged stop-only "
            "engine consumes the SIGN of a prediction only (release when pred < 0), so any "
            "positive constant transform gives identical actions; no magnitude unit identity is "
            "claimed and nothing is rescaled."
        ),
        # consumed by the fit: these three are REQUIRED keys of this manifest and the fit reads
        # them without defaults, so they are declared here explicitly.
        "horizons": list(HORIZONS),
        "ns": list(NS),
        "clocks": list(CLOCKS),
        "fee_side": FEE_SIDE,
        "fee_ratio": FEE_RATIO,
        "gross_over_cash": 1.0 / FEE_RATIO,
        "folds": [list(f) for f in FOLDS],
        "split_sha256": sha256_file(ls.v2_dir(data_root) / "split.json"),
        "data_root": str(data_root),
        "outputs": {
            "minutes_dir": str(minutes_dir),
            "manifest": str(out / "manifest.json"),
        },
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
    print(
        f"[minute build] complete days={len(days)} minutes={manifest['rows_total']} "
        f"features={len(feats)} evidence={evidence} -> {out}",
        flush=True,
    )
    return 0


# ------------------------------------------------------------------ fit: corpus staging
def corpus_counts(days: list[str], minutes_dir: Path, rank_max: int) -> list[int]:
    counts = []
    for day in days:
        p = minutes_dir / f"{day}.parquet"
        if not p.exists():
            raise SystemExit(f"minute corpus missing {p}; run the build command first")
        counts.append(
            int(pl.scan_parquet(p).select((pl.col("rank") <= rank_max).sum()).collect().item())
        )
    return counts


def stage_corpus(
    days: list[str],
    feats: list[str],
    rank_max: int,
    minutes_dir: Path,
    cache_dir: Path,
    tag: str,
) -> dict:
    """Project only the needed columns per day into a disk-backed float32 memmap corpus."""
    counts = corpus_counts(days, minutes_dir, rank_max)
    total = int(sum(counts))
    if total == 0:
        raise SystemExit(f"empty corpus for rank<={rank_max} over {len(days)} days")
    cache_dir.mkdir(parents=True, exist_ok=True)
    x_path = cache_dir / f"X_{tag}.f32"
    y_path = cache_dir / f"Y_{tag}.f32"
    x = np.memmap(x_path, dtype=np.float32, mode="w+", shape=(total, len(feats)))
    y = np.memmap(y_path, dtype=np.float32, mode="w+", shape=(total, len(LABEL_COLS)))
    day_idx = np.empty(total, dtype=np.int16)
    claim_idx = np.empty(total, dtype=np.int32)
    cols = [*KEY_COLS, "session_end", "fill_et", *feats, *LABEL_COLS]
    keys_frames = []
    offset = 0
    claim_offset = 0
    for di, (day, cnt) in enumerate(zip(days, counts, strict=True)):
        if cnt == 0:
            continue
        sub = (
            pl.read_parquet(minutes_dir / f"{day}.parquet", columns=cols)
            .filter(pl.col("rank") <= rank_max)
            .sort(["clock", "rank", "ticker", "t"])
        )
        m = sub.height
        if m != cnt:
            raise SystemExit(f"{day}: row count changed between the counting and staging passes")
        xb = sub.select([pl.col(c).cast(pl.Float32, strict=False) for c in feats]).to_numpy()
        if xb.dtype != np.float32:
            xb = xb.astype(np.float32)
        yb = (
            sub.select([pl.col(c).cast(pl.Float32, strict=False) for c in LABEL_COLS])
            .to_numpy()
            .astype(np.float32, copy=False)
        )
        x[offset : offset + m] = xb
        y[offset : offset + m] = yb
        clock = sub["clock"].to_numpy()
        rank = sub["rank"].to_numpy()
        ticker = sub["ticker"].to_numpy()
        fresh = np.ones(m, dtype=bool)
        fresh[1:] = (
            (clock[1:] != clock[:-1]) | (rank[1:] != rank[:-1]) | (ticker[1:] != ticker[:-1])
        )
        local_claim = np.cumsum(fresh) - 1
        claim_idx[offset : offset + m] = (claim_offset + local_claim).astype(np.int32)
        claim_offset += int(fresh.sum())
        day_idx[offset : offset + m] = di
        keys_frames.append(
            sub.select([*KEY_COLS, "session_end", "fill_et"]).with_columns(
                pl.lit(di, dtype=pl.Int32).alias("_dayi"),
                pl.Series("_claimi", local_claim.astype(np.int32)),
            )
        )
        offset += m
        del sub, xb, yb
    if offset != total:
        raise SystemExit(f"staged {offset} rows, expected {total}")
    x.flush()
    y.flush()
    keys = pl.concat(keys_frames, how="vertical")
    keys.write_parquet(cache_dir / f"keys_{tag}.parquet")
    return {
        "x": x,
        "y": y,
        "day_idx": day_idx,
        "claim_idx": claim_idx,
        "keys": keys,
        "rows": total,
        "claims": claim_offset,
        "path": cache_dir,
        "tag": tag,
    }


def read_keys(
    days: list[str], rank_max: int, minutes_dir: Path, cache_dir: Path, tag: str
) -> pl.DataFrame:
    """Score-side row keys in exactly the staged corpus order (cache first, else rebuild).

    Rebuilding costs one narrow parquet projection per day and no feature columns, so a
    resumed run can assemble its score frame without re-staging the float32 memmaps.
    """
    cached = cache_dir / f"keys_{tag}.parquet"
    if cached.exists():
        return pl.read_parquet(cached)
    cols = [*KEY_COLS, "session_end", "fill_et"]
    frames = [
        pl.read_parquet(minutes_dir / f"{day}.parquet", columns=cols)
        .filter(pl.col("rank") <= rank_max)
        .sort(["clock", "rank", "ticker", "t"])
        for day in days
    ]
    keys = pl.concat(frames, how="vertical")
    keys.write_parquet(cached)
    return keys


def minute_weights(
    day_idx: np.ndarray, claim_idx: np.ndarray, mask: np.ndarray, n_days: int
) -> np.ndarray:
    """Inverse claim-minute-count, then exactly one unit per day (core's weight shape)."""
    d = day_idx[mask]
    c = claim_idx[mask]
    counts = np.bincount(c)
    counts[counts == 0] = 1
    w = 1.0 / counts[c]
    per_day = np.bincount(d, weights=w, minlength=n_days)
    if np.any(per_day[d] <= 0):
        raise SystemExit("a train day has no fitted rows at this horizon")
    w = w / per_day[d]
    return (w * w.size / w.sum()).astype(np.float64)


def feasibility(t: np.ndarray, session_end: np.ndarray) -> np.ndarray:
    """Calendar-only feasibility: t + h <= session_end, a fact known at t."""
    hs = np.asarray(HORIZONS, dtype=np.float64)
    return (session_end.astype(np.float64)[:, None] - t.astype(np.float64)[:, None]) >= hs


def horizon_views(
    preds: dict[int, np.ndarray], t: np.ndarray, session_end: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """pred_max over calendar-feasible horizons, with UNKNOWN never collapsed to HOLD.

    Three outcomes, kept distinct:
      * some feasible horizon with a finite forecast -> that horizon's prediction;
      * NO calendar-feasible horizon -> CALENDAR_EXIT (exit now is the only admissible action,
        and its owned increment is zero, which the engine's `pred < 0` rule cannot express);
      * feasible horizons exist but every one of them is non-finite -> UNKNOWN, which is NOT
        publishable: a non-finite prediction makes the engine hold silently.  Refuse instead.
    """
    stack = np.column_stack([preds[h] for h in HORIZONS]).astype(np.float64)
    feas = feasibility(t, session_end)
    finite = np.isfinite(stack)
    any_feasible = feas.any(axis=1)
    usable_values = np.where(feas & finite, stack, -np.inf)
    unknown = any_feasible & ~np.isfinite(usable_values).any(axis=1)
    if unknown.any():
        rows = np.flatnonzero(unknown)[:3]
        raise SystemExit(
            f"{int(unknown.sum())} scored minutes have calendar-feasible horizons but no finite "
            f"forecast (first t values {t[rows].tolist()}); refusing to publish a prediction the "
            "engine would silently read as HOLD"
        )
    best = usable_values.argmax(axis=1)
    row_ix = np.arange(stack.shape[0])
    value = usable_values[row_ix, best]
    p_max = np.where(any_feasible, value, CALENDAR_EXIT).astype(np.float32)
    sel = np.where(any_feasible, np.asarray(HORIZONS, dtype=np.int16)[best], np.int16(-1))
    return p_max, sel, feas


def encode_horizon_view(pred: np.ndarray, feas_col: np.ndarray) -> np.ndarray:
    """A fixed-horizon view's ACTION column: raw forecast where the target is calendar
    reachable, CALENDAR_EXIT where it is not.  Never pads an unreachable target with a
    forecast, and never leaves a 0 that the engine would read as HOLD."""
    out = np.where(feas_col, np.asarray(pred, dtype=np.float64), CALENDAR_EXIT)
    if not np.isfinite(out).all():
        bad = int((~np.isfinite(out)).sum())
        raise SystemExit(
            f"{bad} published horizon predictions are non-finite; refusing to publish UNKNOWN "
            "as a decision value"
        )
    return out.astype(np.float32)


def fit_horizon(
    corpus: dict,
    feats: list[str],
    h: int,
    n_days: int,
) -> tuple[np.ndarray, dict, np.ndarray]:
    """One direct mean-dollar model for one horizon. Releases its own state before return."""
    y_all = corpus["y"][:, HORIZONS.index(h)]
    mask = np.isfinite(y_all)
    rows = int(mask.sum())
    if rows == 0:
        raise SystemExit(f"horizon {h}: no labelled TRAIN rows at all; refusing to fit")
    w = minute_weights(corpus["day_idx"], corpus["claim_idx"], mask, n_days)
    x_fit = corpus["x"][mask]
    y_fit = y_all[mask].astype(np.float64)
    dataset = lgb.Dataset(
        x_fit,
        label=y_fit,
        weight=w,
        feature_name=feats,
        params=PARAMS,
        free_raw_data=False,
    ).construct()
    model = lgb.train(PARAMS, dataset, num_boost_round=ROUNDS)
    train_pred = model.predict(x_fit, num_threads=PARAMS["num_threads"]).astype(np.float64)
    test_pred = model.predict(corpus["x_te"], num_threads=PARAMS["num_threads"]).astype(np.float32)
    gain = np.asarray(model.feature_importance(importance_type="gain"), dtype=np.float64)
    # The declared baseline is the WEIGHTED train mean under the SAME weights the learner
    # sees (inverse claim-minute-count, one unit per day), so a naive constant forecast and the
    # fitted model are compared in matched units AND matched weighting.  The unweighted mean is
    # kept alongside it as a secondary reference, never as the baseline.
    weighted_mean = float(np.average(y_fit, weights=w))
    diag = {
        "horizon": h,
        "rows_fit": rows,
        "features": len(feats),
        "rounds": ROUNDS,
        "train_target": qstats(y_fit),
        "train_target_tail": tail_exposure(y_fit),
        "train_pred": qstats(train_pred),
        "train_mean_label_baseline": weighted_mean,
        "train_mean_label_baseline_units": (
            "cash dollars per original claim cash dollar, weighted like the fit"
        ),
        "train_mean_label_unweighted": float(y_fit.mean()),
        "train_mean_pred": float(train_pred.mean()),
        "train_calibration_gap": float(train_pred.mean() - weighted_mean),
        "train_pearson": _pearson(train_pred, y_fit),
        "train_sign_agreement": float(np.mean(np.sign(train_pred) == np.sign(y_fit))),
    }
    del dataset, x_fit, y_fit, model
    gc.collect()
    return test_pred, diag, gain


def _pearson(a: np.ndarray, b: np.ndarray) -> float | None:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.size < 3:
        return None
    sa, sb = a.std(), b.std()
    if sa <= 0 or sb <= 0:
        return None
    return float(np.mean((a - a.mean()) * (b - b.mean())) / (sa * sb))


# ------------------------------------------------------------------ fit command
def cmd_fit(a: argparse.Namespace) -> int:
    data_root = ls.bps.resolve_data_root(a.data_root)
    disc = list(ls.load_split(data_root)["discovery_days"])
    if len(disc) != N_DISCOVERY:
        raise SystemExit(f"discovery day count {len(disc)} != {N_DISCOVERY}")
    out = Path(a.out)
    base_root = Path(a.minutes_root) if a.minutes_root else Path(a.out)
    # A smoke build publishes its corpus under <root>/smoke; a smoke fit must read that exact
    # manifest and publish its scores beside it.  No path is guessed past a missing manifest.
    if a.smoke_days:
        root = (
            base_root / "smoke" if (base_root / "smoke" / "manifest.json").exists() else base_root
        )
        out = root
    else:
        root = base_root
    man_path = root / "manifest.json"
    if not man_path.exists():
        raise SystemExit(f"minute corpus manifest missing {man_path}; run the build command")
    man = json.loads(man_path.read_text())
    if man.get("status") != "complete":
        raise SystemExit("minute corpus manifest is not complete")
    corpus_panel_feats = list(man.get("panel_feature_columns") or [])
    if not corpus_panel_feats:
        raise SystemExit(
            "corpus manifest lacks panel_feature_columns (pre-whitelist corpus): rebuild it"
        )
    corpus_derived = list(man.get("derived_feature_columns") or [])
    if corpus_derived != list(DERIVED_FEATURES):
        raise SystemExit(
            f"corpus derived plane {corpus_derived} != whitelist {list(DERIVED_FEATURES)}"
        )
    feats = check_features(list(man["feature_columns"]), corpus_panel_feats)
    horizons = tuple(int(h) for h in man["horizons"])
    if horizons != HORIZONS:
        raise SystemExit(f"corpus horizons {horizons} != this producer's {HORIZONS}")
    if list(man["label_columns"]) != list(LABEL_COLS):
        raise SystemExit(f"corpus label columns {man['label_columns']} != {list(LABEL_COLS)}")
    corpus_days = [str(d) for d in man["days"]]
    if not set(corpus_days) <= set(disc):
        raise SystemExit("minute corpus contains non-discovery days; refusing")
    # fold index windows are indices into the canonical chronological discovery list, so the
    # corpus must be stored in that exact order (not merely be a subset).
    if corpus_days != [d for d in disc if d in set(corpus_days)]:
        raise SystemExit("minute corpus days are not in canonical discovery order; refusing")
    minutes_dir = Path(man["outputs"]["minutes_dir"])
    if not minutes_dir.is_dir():
        raise SystemExit(f"corpus minutes dir missing: {minutes_dir}")

    evidence, notes = True, []
    if a.smoke_days:
        n = int(a.smoke_days)
        if n < 12:
            raise SystemExit("--smoke-days must be >= 12 to form a tiny train/test split")
        if n > len(corpus_days):
            raise SystemExit(f"corpus holds {len(corpus_days)} days, not the requested {n}")
        days = corpus_days[:n]
        b1 = max(2, -(-SMOKE_TRAIN1[0] * n // SMOKE_TRAIN1[1]))
        b2 = max(4, -(-SMOKE_TRAIN2[0] * n // SMOKE_TRAIN2[1]))
        folds = ((0, b1, b1, b2), (0, b2, b2, n))
        evidence = False
        notes.append(f"non-evidence smoke fit over the first {n} corpus days; rescaled folds")
        # `out` already points at the corpus root that holds this manifest
    else:
        days = corpus_days
        folds = FOLDS
        if len(days) != N_DISCOVERY:
            raise SystemExit(
                f"corpus holds {len(days)} days; a full run needs all {N_DISCOVERY} "
                "(pass --smoke-days for a labelled non-evidence run)"
            )
    # a smoke run has fewer fold windows; out-of-range indices are dropped (as in
    # owned_claim_value) rather than silently remapped to a different window.
    requested = [int(x) for x in str(a.folds).split(",") if str(x).strip() != ""]
    if not requested:
        raise SystemExit(f"--folds must index {list(range(len(folds)))}")
    fold_ids = [i for i in requested if 0 <= i < len(folds)]
    if not fold_ids:
        raise SystemExit(f"--folds {requested} selects no window of {list(range(len(folds)))}")
    if fold_ids != requested or fold_ids != list(range(len(folds))):
        evidence = False
        notes.append(
            f"fold subset {fold_ids} of {list(range(len(folds)))} (requested {requested}, "
            "diagnostic only)"
        )

    windows = []
    for i in fold_ids:
        tr0, tr1, te0, te1 = folds[i]
        tr0, tr1, te0, te1 = (
            min(tr0, len(days)),
            min(tr1, len(days)),
            min(te0, len(days)),
            min(te1, len(days)),
        )
        if te0 >= te1:
            raise SystemExit(f"fold {i}: empty test window after clamping ({te0},{te1})")
        windows.append(
            {
                "fold": i,
                "train_days": days[tr0:tr1],
                "test_days": days[te0:te1],
                "train_index": [tr0, tr1],
                "test_index": [te0, te1],
            }
        )
    days_needed = sorted({d for w in windows for d in w["train_days"] + w["test_days"]})
    digests = {}
    for day in days_needed:
        p = minutes_dir / f"{day}.parquet"
        if not p.exists():
            raise SystemExit(f"minute corpus missing {p}; run the build command")
        digests[day] = sha256_file(p)

    run_id = hashlib.sha256(
        "|".join(
            [
                sha256_file(SCRIPT),
                sha256_file(EVENTS_SRC),
                sha256_file(BENCH_SRC),
                sha256_file(man_path),
                # the corpus bytes this run actually consumes, not just the build manifest:
                # a rebuilt minute parquet must invalidate every cached prediction
                json.dumps(digests, sort_keys=True),
                json.dumps(windows, sort_keys=True),
                json.dumps(list(VIEW_NAMES)),
                json.dumps(PARAMS, sort_keys=True),
                str(ROUNDS),
                str(FEE_RATIO),
            ]
        ).encode()
    ).hexdigest()

    out.mkdir(parents=True, exist_ok=True)
    cache = out / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (out / "metadata.json").write_text(
        json.dumps({"status": "running", "run_id": run_id, "evidence": evidence}) + "\n"
    )

    scores: dict[int, list[pl.DataFrame]] = {n: [] for n in NS}
    horizon_diags: list[dict] = []
    max_diags: list[dict] = []
    profile: list[dict] = []
    importance_rows: list[dict] = []

    for w in windows:
        k = w["fold"]
        for n in NS:
            # serial per-horizon cache: a resumed run refits nothing whose run_id still matches
            preds: dict[int, np.ndarray] = {}
            diags: dict[int, dict] = {}
            gains: dict[int, list[float]] = {}
            pending: list[int] = []
            for h in HORIZONS:
                cj = cache / f"preds_n{n}_f{k}_h{h}.json"
                cp = cache / f"preds_n{n}_f{k}_h{h}.parquet"
                if cj.exists() and cp.exists():
                    blob = json.loads(cj.read_text())
                    if blob.get("run_id") == run_id:
                        arr = pl.read_parquet(cp)["pred"].to_numpy()
                        if arr.shape[0] > 0:
                            preds[h] = arr
                            diags[h] = blob["diag"]
                            gains[h] = blob["importance"]
                            continue
                pending.append(h)
            te_tag = f"te_n{n}_f{k}"
            corpus = test = None
            if pending:
                corpus = stage_corpus(
                    w["train_days"], feats, n, minutes_dir, cache, f"tr_n{n}_f{k}"
                )
                test = stage_corpus(w["test_days"], feats, n, minutes_dir, cache, te_tag)
                corpus["x_te"] = test["x"]
                corpus["y_te"] = test["y"]
            test_keys = read_keys(w["test_days"], n, minutes_dir, cache, te_tag)
            t_col = test_keys["t"].to_numpy().astype(np.int64)
            se_col = test_keys["session_end"].to_numpy().astype(np.int64)
            for h in pending:
                pred, diag, gain = fit_horizon(corpus, feats, h, len(w["train_days"]))
                y_te = test["y"][:, HORIZONS.index(h)]
                support = np.isfinite(y_te)
                outcome = {
                    "fold": k,
                    "n": n,
                    "horizon": h,
                    "test_rows": int(pred.shape[0]),
                    "test_label_support": int(support.sum()),
                    "test_label_unknown": int((~support).sum()),
                }
                if support.any():
                    realized = y_te[support].astype(np.float64)
                    # the declared baseline is the TRAIN mean of this horizon's label,
                    # evaluated on exactly the same TEST rows the model is scored on.
                    base_value = float(diag["train_mean_label_baseline"])
                    model_pred = pred[support].astype(np.float64)
                    delta = model_pred - base_value
                    outcome.update(
                        {
                            "realized_mean": float(realized.mean()),
                            "train_mean_baseline": base_value,
                            "model_mean_on_support": float(model_pred.mean()),
                            "model_minus_baseline": float(model_pred.mean()) - base_value,
                            "model_minus_baseline_se": float(
                                delta.std(ddof=1) / np.sqrt(delta.size)
                            )
                            if delta.size > 1
                            else None,
                            "pearson": _pearson(model_pred, realized),
                            "sign_agreement": float(
                                np.mean(np.sign(model_pred) == np.sign(realized))
                            ),
                        }
                    )
                diag["test_outcome_diagnostic_not_used_for_selection"] = outcome
                preds[h], diags[h], gains[h] = pred, diag, gain.tolist()
                pl.DataFrame({"pred": pred}).write_parquet(cache / f"preds_n{n}_f{k}_h{h}.parquet")
                (cache / f"preds_n{n}_f{k}_h{h}.json").write_text(
                    json.dumps(
                        {
                            "run_id": run_id,
                            "fold": k,
                            "n": n,
                            "horizon": h,
                            "rows": int(pred.shape[0]),
                            "importance": gain.tolist(),
                            "diag": diag,
                        },
                        allow_nan=False,
                    )
                )
                print(
                    f"[minute fit] fold{k} n{n} h{h}: fit={diag['rows_fit']} "
                    f"test_rows={int(pred.shape[0])} support={outcome['test_label_support']}",
                    flush=True,
                )
            missing_h = [h for h in HORIZONS if h not in preds]
            if missing_h:
                raise SystemExit(f"fold{k} n{n}: no predictions for horizons {missing_h}")
            horizon_diags.extend(diags[h] for h in HORIZONS)
            stacked = np.column_stack([preds[h] for h in HORIZONS]).astype(np.float64)
            p_max, sel, feasible = horizon_views(preds, t_col, se_col)
            per_h_mean = {int(h): float(preds[h].astype(np.float64).mean()) for h in HORIZONS}
            # raw forecast means, split by whether the target horizon is calendar-reachable.
            # No statistic here is computed on sentinel-padded values.
            per_h_mean_feasible = {
                int(h): (
                    float(preds[h][feasible[:, HORIZONS.index(h)]].astype(np.float64).mean())
                    if feasible[:, HORIZONS.index(h)].any()
                    else None
                )
                for h in HORIZONS
            }
            horizon_mean_all_rows = float(np.mean(list(per_h_mean.values())))
            # selection margin between the best and second-best FEASIBLE horizon: an honest
            # uncertainty read on a max-selection, reported without touching TEST labels.
            candidates = np.where(feasible, np.nan_to_num(stacked, nan=-np.inf), -np.inf)
            ranked = np.sort(candidates, axis=1)
            feasible_count = feasible.sum(axis=1)
            margin_top = np.full(ranked.shape[0], np.nan, dtype=np.float64)
            has_two = feasible_count >= 2
            margin_top[has_two] = ranked[has_two, -1] - ranked[has_two, -2]
            sel_counts = {str(h): int((sel == h).sum()) for h in HORIZONS}
            sel_counts["none_feasible"] = int((sel < 0).sum())
            # Every max-view statistic is computed on rows where at least one horizon is
            # calendar-feasible, so the CALENDAR_EXIT rows can never enter a mean.  The premium
            # compares mean(pred_max) against the mean of the per-horizon means on their own
            # feasible rows (row sets differ slightly per horizon; declared, not hidden).
            any_feasible_rows = sel >= 0
            pred_on_feasible = p_max.astype(np.float64)[any_feasible_rows]
            horizon_mean_feasible = float(
                np.mean([v for v in per_h_mean_feasible.values() if v is not None])
            )
            predicted_mean = float(pred_on_feasible.mean()) if pred_on_feasible.size else None
            max_diags.append(
                {
                    "fold": k,
                    "n": n,
                    "test_rows": int(p_max.shape[0]),
                    "rows_with_a_feasible_horizon": int(any_feasible_rows.sum()),
                    "rows_calendar_exit_only": int((~any_feasible_rows).sum()),
                    "predicted_mean": predicted_mean,
                    "mean_horizon_predicted_on_feasible_rows": horizon_mean_feasible,
                    "share_predicted_positive": (
                        float((pred_on_feasible > 0).mean()) if pred_on_feasible.size else None
                    ),
                    "max_selection_premium_vs_mean_horizon": (
                        predicted_mean - horizon_mean_feasible
                        if predicted_mean is not None
                        else None
                    ),
                    "units": "cash dollars per original claim cash dollar",
                    "per_horizon_predicted_mean_all_rows": per_h_mean,
                    "per_horizon_predicted_mean_calendar_feasible": per_h_mean_feasible,
                    "mean_horizon_predicted_all_rows": horizon_mean_all_rows,
                    "selected_horizon_counts": sel_counts,
                    "selection_margin": qstats(margin_top),
                    "note": (
                        "max_selection_premium is PURE ARITHMETIC over forecasts: mean of the "
                        "row-wise max (rows with a feasible horizon) minus the mean over "
                        "horizons of each horizon's mean on ITS feasible rows. It is NOT a "
                        "measured statistical bias -- quantifying that would require realized "
                        "outcomes and is not done on TEST. It sizes the optimism the "
                        "max-selection assumption buys; reported, never corrected, and the "
                        "per-horizon views ship beside it so the assumption can be tested"
                    ),
                }
            )
            profile_cache = cache / f"profile_n{n}_f{k}.json"
            if (
                profile_cache.exists()
                and json.loads(profile_cache.read_text()).get("run_id") == run_id
            ):
                profile.append(json.loads(profile_cache.read_text())["profile"])
            else:
                if corpus is None:
                    raise SystemExit(
                        f"fold{k} n{n}: no cached train label profile and no corpus staged; "
                        "rerun with the cache removed"
                    )
                train_profile = {}
                for j, h in enumerate(HORIZONS):
                    column = corpus["y"][:, j]
                    known = np.isfinite(column)
                    train_profile[str(h)] = {
                        "known_rows": int(known.sum()),
                        "unknown_rows": int((~known).sum()),
                        "label_mean": float(column[known].astype(np.float64).mean())
                        if known.any()
                        else None,
                        "label_median": float(np.median(column[known].astype(np.float64)))
                        if known.any()
                        else None,
                        "share_positive": float((column[known] > 0).mean())
                        if known.any()
                        else None,
                    }
                entry = {"fold": k, "n": n, "train_minute_label_profile": train_profile}
                profile.append(entry)
                profile_cache.write_text(
                    json.dumps({"run_id": run_id, "profile": entry}, allow_nan=False)
                )

            # A fixed-horizon view's published column is an ACTION column: the raw forecast
            # where the target horizon is calendar-reachable, CALENDAR_EXIT where it is not.
            # `feasible_h{h}` and `n_feasible_h` keep the encoding fully recoverable.
            frame = test_keys.select([*KEY_COLS, "session_end", "fill_et"]).with_columns(
                [
                    pl.Series(
                        f"pred_h{h}",
                        encode_horizon_view(preds[h], feasible[:, HORIZONS.index(h)]),
                    )
                    for h in HORIZONS
                ]
                + [
                    pl.Series(f"feasible_h{h}", feasible[:, HORIZONS.index(h)].astype(np.int8))
                    for h in HORIZONS
                ]
                + [
                    pl.Series("pred_max", p_max),
                    pl.Series("sel_h", sel),
                    pl.Series("n_feasible_h", feasible.sum(axis=1).astype(np.int8)),
                    pl.Series("calendar_exit_cells", (~feasible).sum(axis=1).astype(np.int8)),
                    pl.lit(k, dtype=pl.Int32).alias("fold"),
                ]
            )
            if not np.isfinite(p_max.astype(np.float64)).all():
                raise SystemExit(f"fold{k} n{n}: non-finite pred_max after encoding; refusing")
            scores[n].append(frame)
            mean_gain = np.mean(np.stack([np.asarray(gains[h]) for h in HORIZONS]), axis=0)
            importance_rows.append(
                pl.DataFrame(
                    {
                        "fold": pl.Series(np.full(len(feats), k, dtype=np.int32)),
                        "n": pl.Series(np.full(len(feats), n, dtype=np.int32)),
                        "feature": pl.Series(feats, dtype=pl.Utf8),
                        "gain_mean_over_horizons": mean_gain.astype(np.float64),
                    }
                )
            )
            for stale in (
                cache / f"X_tr_n{n}_f{k}.f32",
                cache / f"Y_tr_n{n}_f{k}.f32",
                cache / f"keys_tr_n{n}_f{k}.parquet",
                cache / f"X_{te_tag}.f32",
                cache / f"Y_{te_tag}.f32",
            ):
                if stale.exists():
                    stale.unlink()
            corpus = test = None
            gc.collect()
            print(
                f"[minute fit] fold{k} n{n}: scored, corpus released "
                f"(cached horizons: {len(HORIZONS) - len(pending)})",
                flush=True,
            )

    # Every TEST day gets a scores_n{n}/<day>.parquet partition, typed even when it has zero
    # rows (a test day with no filled rank<=n claim).  Consumers may then hash every expected
    # score file without a "missing" case, and an absent file can never be mistaken for an
    # empty cohort.
    test_days = sorted({d for w in windows for d in w["test_days"]})
    written: dict[str, list] = {}
    for n in NS:
        if not scores[n]:
            raise SystemExit(f"no scored rows for N{n}; refusing to publish an empty view")
        frame = pl.concat(scores[n], how="vertical").sort([*KEY_COLS, "fold"])
        by_day = {
            (k[0] if isinstance(k, tuple) else k): v
            for k, v in frame.partition_by("day", as_dict=True, maintain_order=True).items()
        }
        target = out / f"scores_n{n}"
        target.mkdir(parents=True, exist_ok=True)
        entries = []
        for day in test_days:
            part = by_day.get(day)
            part = (
                part.select(list(SCORE_SCHEMA)).cast(SCORE_SCHEMA)
                if part is not None
                else pl.DataFrame(schema=SCORE_SCHEMA)
            )
            path = target / f"{day}.parquet"
            tmp = path.with_suffix(".tmp.parquet")
            part.write_parquet(tmp)
            tmp.replace(path)
            # rows AND content hash: a zero-row partition is a real, verifiable statement that
            # this (N, day) had no filled claim, never a missing file to be guessed at.
            entries.append(
                {
                    "day": str(day),
                    "rows": int(part.height),
                    "empty_typed_partition": part.height == 0,
                    "sha256": sha256_file(path),
                }
            )
        extra = sorted(set(by_day) - set(test_days))
        if extra:
            raise SystemExit(
                f"scores_n{n} holds days outside the published test windows: {extra[:5]}"
            )
        written[f"n{n}"] = entries
    if importance_rows:
        imp = pl.concat(importance_rows, how="vertical")
        for (k, n), part in imp.partition_by(["fold", "n"], as_dict=True).items():
            part.sort("gain_mean_over_horizons", descending=True).write_parquet(
                out / f"importance_f{k}_n{n}.parquet"
            )

    coverage = {}
    probe_day = days_needed[-1]
    probe = pl.read_parquet(
        minutes_dir / f"{probe_day}.parquet",
        columns=[*KEY_COLS, "fill_et", "session_end", *feats],
    )
    for n in NS:
        sub = probe.filter(pl.col("rank") <= n)
        coverage[f"n{n}"] = {
            "probe_day": probe_day,
            "rows": int(sub.height),
            "observed_fraction": observed_coverage(sub, feats),
        }
    del probe
    gc.collect()

    meta = {
        "status": "complete",
        "kind": "DISCOVERY-FULL-MINUTE-MEAN-DOLLAR-VALUE-NOT-VALIDATED-EDGE",
        "run_id": run_id,
        "producer": "factory/scripts/owned_claim_minute_value.py fit",
        "script_sha256": sha256_file(SCRIPT),
        "source_hashes": {
            "events_sha256": sha256_file(EVENTS_SRC),
            "owned_claim_value_sha256": sha256_file(BENCH_SRC),
            "observable_peers_sha256": sha256_file(PEERS_SRC),
        },
        "evidence": evidence,
        "non_evidence_notes": notes,
        "corpus_manifest": str(man_path),
        "corpus_manifest_sha256": sha256_file(man_path),
        "corpus_day_sha256": digests,
        "data_root": str(data_root),
        "split_sha256": sha256_file(ls.v2_dir(data_root) / "split.json"),
        "days": days,
        "clocks": list(CLOCKS),
        "horizons": list(HORIZONS),
        "views": list(VIEW_NAMES),
        "action_encoding": (
            "every pred_* column is an ACTION column for the unchanged engine's `pred < 0` "
            "release rule: a forecast is never replaced by zero, and a calendar-forced exit is "
            "never encoded as a forecast"
        ),
        "score_schema": {
            "keys": [*KEY_COLS, "session_end", "fill_et", "fold"],
            "pred_h{h}": (
                "ACTION column of the fixed h{h} view: raw mean-dollar forecast where the target "
                "is calendar-reachable, CALENDAR_EXIT where it is not"
            ),
            "pred_max": (
                "ACTION column: row-wise max over calendar-feasible horizons, CALENDAR_EXIT only "
                "where no horizon is feasible at all"
            ),
            "feasible_h{h}": "0/1 calendar feasibility of that horizon at that minute",
            "n_feasible_h": "how many horizons are calendar-feasible at that minute",
            "calendar_exit_cells": "how many of the eight targets are infeasible at that minute",
            "sel_h": "argmax horizon, -1 when none is feasible",
            "contains_no_label_column": True,
            "partition_guarantee": (
                "scores_n{n}/<day>.parquet is written for EVERY published test day, with this "
                "exact schema and zero rows when the day has no filled rank<=n claim; consumers "
                "can hash every expected file without a missing-file case"
            ),
        },
        "views_definition": {
            "max": "max over calendar-feasible horizons (t+h<=session_end) of the predicted "
            "mean-dollar increment; CALENDAR_EXIT (exit now) when no horizon is feasible",
            **{
                f"h{h}": f"predicted mean-dollar increment of holding exactly {h} actual minutes"
                for h in HORIZONS
            },
        },
        "prediction_units": (
            "incremental inherited dollars per ORIGINAL CLAIM CASH dollar: the canonical "
            "owned_claim_events increment_{h}_original_dollar unit, i.e. the gross-entry-dollar "
            f"increment x {FEE_RATIO:.6f} ((1-s)/(1+s), s={FEE_SIDE}). owned_claim_replay."
            "simulate spends an `amount = 1/n` CASH budget (q = (1/n)/(fill_px*(1+s))), so its "
            "consumed SIGN-ONLY by the unchanged stop-only engine, which releases on "
            "pred < 0; no magnitude unit identity is claimed and no rescaling is applied, since "
            "any positive constant transform yields identical actions. Per-dollar diagnostics "
            "here (baselines, means, premiums) are all in this same cash unit."
        ),
        "gross_unit_equivalent": (
            f"any cash figure C corresponds to gross-entry dollars C/{FEE_RATIO:.6f}"
        ),
        "calendar_exit_sentinel": CALENDAR_EXIT,
        "calendar_exit_sentinel_meaning": (
            "ACTION code, not a forecast: the fixed-horizon views publish it exactly where "
            "t + h > session_end, i.e. the target horizon is calendar-infeasible and exit-now "
            "is the only admissible action (owned increment zero, which the engine's "
            "`pred < 0` release rule cannot otherwise express). pred_max publishes it only "
            "where NO horizon is feasible. No diagnostic statistic is computed from these rows."
        ),
        "objective": "direct mean-dollar regression per horizon; no value iteration",
        "baseline_definition": (
            "the declared naive forecast is the WEIGHTED train mean of each horizon's label "
            "under the same day/claim weights the learner sees; the unweighted mean is reported "
            "separately and is never the baseline"
        ),
        "params": PARAMS,
        "params_source": "factory/scripts/owned_claim_value.py PARAMS (imported verbatim)",
        "params_source_sha256": sha256_file(BENCH_SRC),
        "rounds_per_model": ROUNDS,
        "seed": PARAMS.get("seed"),
        "num_threads": PARAMS.get("num_threads"),
        "feature_view": (
            "owned_claim_events.load_day feature_* verbatim (corrected causal panel, as-of "
            "observable peers/scanner, past-history grid) plus the per-minute event-memory "
            "columns built from owned_claim_events.flags_for on every post-fill minute; no "
            "feature invented, renamed, selected or dropped"
        ),
        "feature_columns": feats,
        "forbidden_substrings": list(FORBIDDEN_SUBSTRINGS),
        "folds": windows,
        "ns": list(NS),
        "n_isolation": (
            "N3 and N5 are fit on separate corpora and published in separate directories "
            "(scores_n3/, scores_n5/). Consumers must use scores_n{n} for an N{n} book; "
            "cross-applying a view trained on the other roster size is forbidden."
        ),
        "test_discipline": (
            "models are TRAIN-only per fold; scores contain no label/status/outcome column, "
            "every test minute is scored whether or not its label exists, and no test outcome "
            "gates any action or selection"
        ),
        "policy_assumptions": [
            "pred_max takes a max over forecasts and therefore buys an optimism premium: "
            "max_selection_premium_vs_mean_horizon is that premium as pure arithmetic (mean of "
            "the row-wise max minus the mean of the per-horizon means). It is NOT a measured "
            "statistical bias -- that would need realized outcomes -- and it is never corrected "
            "on TEST.",
            "pred_max is an assumption that some horizon is worth holding to, not a claim of "
            "optimality; the per-horizon views are the comparison without the max-selection "
            "premium.",
            "calendar feasibility gates ACTIONS, never labels: a fixed-horizon view whose "
            "target is calendar-infeasible exits (CALENDAR_EXIT), which is a calendar fact at "
            "t, not a label-availability fact.",
            "a non-finite forecast is never published: feasible-but-unknown rows abort the fit "
            "instead of silently reading as HOLD.",
            "the horizon set, features, parameters and folds were fixed before any test number "
            "was read; no clipping, no hyper-parameter search, no view selection.",
        ],
        "weights": "inverse claim-minute-count then exactly one unit per train day",
        "state_plane": (
            "per-claim float32 columnar build; per-day parquet staging; fold corpora staged "
            "into disk-backed np.memmap float32; only the fitted horizon's rows are gathered "
            "into RAM; model/Dataset/matrix released between horizons and between N3/N5"
        ),
        "outputs": {
            "scores": {f"n{n}": str(out / f"scores_n{n}") for n in NS},
            "diagnostics": str(out / "minute_diagnostics.json"),
            "importance_glob": "importance_f<k>_n<n>.parquet",
        },
        "test_days_written": written,
        "coverage": coverage,
    }
    (out / "metadata.json").write_text(json.dumps(meta, indent=2, allow_nan=False) + "\n")
    diag = {
        "kind": meta["kind"],
        "evidence": evidence,
        "non_evidence_notes": notes,
        "search": {
            "horizons": list(HORIZONS),
            "features": len(feats),
            "rounds": ROUNDS,
            "params": PARAMS,
            "tuning": "none",
            "feature_or_horizon_selection": "none",
            "folds_run": fold_ids,
            "test_selection_used": False,
        },
        "train_minute_label_profile": profile,
        "models": horizon_diags,
        "policy_view": max_diags,
    }
    (out / "minute_diagnostics.json").write_text(json.dumps(diag, indent=2, allow_nan=False) + "\n")
    print(
        f"[minute fit] complete evidence={evidence} days={len(days)} features={len(feats)} "
        f"folds={fold_ids} ns={list(NS)} -> {out}",
        flush=True,
    )
    return 0


# ------------------------------------------------------------------ CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="full-minute owned-claim continuation value (build corpus / fit models)"
    )
    sub = ap.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="stage the full minute grid, one day parquet per write")
    b.add_argument("--data-root", default=None)
    b.add_argument("--out", required=True)
    b.add_argument("--days", default=None, help="explicit comma-separated discovery days")
    b.add_argument("--smoke-days", type=int, default=0, help="tiny non-evidence corpus prefix")
    b.set_defaults(func=cmd_build)

    f = sub.add_parser("fit", help="fit the declared mean-dollar models and publish scores")
    f.add_argument("--data-root", default=None)
    f.add_argument("--out", required=True)
    f.add_argument("--minutes-root", default=None, help="default: --out")
    f.add_argument("--folds", default=",".join(str(i) for i in range(len(FOLDS))))
    f.add_argument("--smoke-days", type=int, default=0)
    f.set_defaults(func=cmd_fit)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
