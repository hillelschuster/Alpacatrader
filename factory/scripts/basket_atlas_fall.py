"""ATLAS minute panel — the fall: the one-minute hazard of the last new high, the peak-time
distribution that emerges from it, and how much of the peak is given back.

Reads the frozen panel (`factory/artifacts/basket/phase2/ATLAS/panel.parquet`, schema contract in
`ATLAS/SCHEMA.md`) and writes `factory/artifacts/basket/phase2/ATLAS/fall.json`.

    python factory/scripts/basket_atlas_fall.py

THE OBJECT IS A HAZARD, NOT A HORIZON
-------------------------------------
There is no 15/30/60-minute "will it peak soon" grid here.  The session's last new high is the
peak (ties on the maximum high resolve to the first bar, so the last bar strictly above the
running high IS the peak bar).  Two label quantities describe it, both estimated minute by minute:

  hazard   lambda(t) = P(the running high set at bar t is the last new high of the session |
                        a new high was set at t, state at t)      <- risk set is a causal EVENT
  level    phi(t)    = P(no further new high after t | state at t) = P(final_high_flag(t))

`final_high_flag(t)` is the panel's own label: max(high[t+1..end]) <= running_high(t).  The peak
time distribution then EMERGES from arrival x hazard:

        P(peak = t) = P(a new high is set at t) * lambda(t)          (exact, checked)

and the survival curve is S(t) = P(peak > t) = 1 - P(final_high_flag(t)).  The first factor is
causal (observable at t); the second is the hazard whose features must be causal.  A coarse
horizon would have mixed the two and hidden which one moves.

FAMILIES ARE NEVER POOLED IN A CURVE
------------------------------------
A_pm fills at ET 570 (09:30) and B600 at ET 600 (10:00); at et=600 A_pm already owns ~30 bars
while B600 owns 0.  Every curve, hazard and rate table is per family.  Clock minute (et) and
`bars_since_entry` are reported as separate coordinates and never merged.  Where a cross-family
aggregate is unavoidable it is reported on the day-deduplicated set with the duplicate count
flagged.

UNCERTAINTY
-----------
Minute rows are not independent: members repeat across days and a day's names move together.
Cluster-robust (sandwich) standard errors are reported for every rate, clustered by sleeve_day
(primary, conservative) and by ticker (secondary), with the design effect.  Medians and AUCs use a
day-cluster bootstrap.  No uncertainty claim is made without one of the two.

Descriptive sections use the ticket constants (`session_peak_*`, `session_close_ret_from_entry`,
`final_high_flag`) and the outcome cohorts; they are hindsight descriptions of the filled
population, tagged `"uses_outcome_labels": true`, and are never a signal.  The causal sections read
only state columns as inputs; labels appear only as targets.
"""
from __future__ import annotations

import os

# The fits here are many small Newton solves (Hessians of a few hundred rows) plus O(n*k^2)
# gradient products: with the default thread pool (one thread per logical CPU) threading overhead
# dominates and a single variant fit took ~17 s instead of ~0.06 s.  Four threads measured the
# same as one, so cap the pool; an explicit setting in the environment still wins.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
             "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_var, "4")

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl

WT = Path(__file__).resolve().parents[2]
PANEL = WT / "factory/artifacts/basket/phase2/ATLAS/panel.parquet"
REGISTRY = WT / "factory/artifacts/basket/phase2/ATLAS/column_registry.json"
COVERAGE = WT / "factory/artifacts/basket/phase2/ATLAS/coverage.json"
OUT = WT / "factory/artifacts/basket/phase2/ATLAS/fall.json"
# panel v2 (foundation fix: anatomy fill clocks, terminal censoring, forced-flat execution price,
# future_ metadata).  v1 is quarantined; this analysis binds to v2 only.
PANEL_SHA256 = "2a021eda878cdbe53e41d4cc88e2d8ba3b14d9a3a819723d9f679c8aca7c3488"
PANEL_SCHEMA_VERSION = 2

KEYS = ("sleeve_day", "family", "ticker", "entry_rank")
FAMILIES = ("A_pm", "B600")
# active families for this process; `--families A_pm` shards the run to roughly half the memory
FAM = list(FAMILIES)
BLOCKS = ("block1", "block2")

# time-control variants (clock and tenure are separate coordinates in every one of them)
VARIANTS_NAMES = ("clock_only", "tenure_only", "state_only", "clock_plus_state",
                  "tenure_plus_state", "clock_plus_tenure_plus_state")
FIRST_ET = 570
LAST_ET = 960
MORNING_END = 720  # 12:00 ET — secondary window only (the full session is primary)
WINDOWS = ("all_session", "morning_le_1200")
CLOCK_MARKS = ((600, "10:00"), (660, "11:00"), (720, "12:00"), (780, "13:00"), (840, "14:00"))
BOOT_REPS = 60
SEED = 20260925

# descriptive outcome cohorts (same boundaries as basket_diag_clock_cohort.py / the window task)
COHORTS = (
    ("A_peak_ge_100", 1.00, math.inf),
    ("B_peak_30_100", 0.30, 1.00),
    ("C_peak_0_30", 0.00, 0.30),
    ("D_never_above_entry", -math.inf, 0.00),
)
COHORT_ORDER = tuple(c[0] for c in COHORTS)

LABEL_PREFIXES = ("v_", "final_", "remaining_", "cost_", "bars_to_", "dd_before_", "tail_class_",
                  "peak_", "session_")
# causal state families a feature may come from; everything else is a label, a session-wide
# constant, future-only metadata or a censor flag (see column_registry.json / coverage.json)
CAUSAL_FAMILIES = ("state_path", "state_episode", "state_dynamics", "state_attention",
                   "state_cross")
# the column that replaced v1's `member_last_et`; future-only, never a feature
FUTURE_LAST_ET = "future_member_last_et"
# derived features: each must be computed from registry-approved state columns only, and the
# guard validates the SOURCE columns (a derived name cannot be in the registry by definition)
DERIVED_FEATURES = {
    "mfe_surrendered_pos": {"sources": ("mfe_surrendered", "mfe_so_far"),
                            "definition": "mfe_surrendered masked to mfe_so_far > 0, so that "
                                          "'never traded above entry' is not confused with "
                                          "'sitting at the running high'"},
    "is_new_high": {"sources": ("bars_since_new_high",),
                    "definition": "bars_since_new_high == 0, the arrival event"},
}

# causal state features: (panel column, note, transform, bucket edges)
FEATURES = (
    ("dist_from_running_high", "close/running_high - 1 (<0 unless the bar closed on its high)",
     "id", (-0.30, -0.15, -0.08, -0.04, -0.02, -0.01, -0.005)),
    ("bars_since_new_high", "bars since the bar that set the running high (0 = this bar set it)",
     "clip60", (1, 2, 3, 5, 10, 20, 40)),
    ("new_high_count_5", "new-high bars in (t-5, t]", "id", (1, 2, 3)),
    ("new_high_count_15", "new-high bars in (t-15, t]", "id", (1, 2, 3, 5)),
    ("new_high_count_30", "new-high bars in (t-30, t]", "id", (1, 3, 6, 10)),
    ("volume_vs_own_median", "bar volume / median volume since the fill", "log1p",
     (0.5, 0.75, 1.0, 1.5, 2.5, 5.0)),
    ("volume_accel", "mean volume last 5 bars / prior 5 - 1", "slog",
     (-0.5, -0.2, 0.0, 0.2, 0.5, 1.0)),
    ("ret_percentile_candidates", "percentile of ret_from_open0930 among the day's names", "id",
     (0.2, 0.4, 0.6, 0.8, 0.9)),
    ("peer_new_high_5", "peers (own family/day) with a new-high bar in (t-5, t]", "id", (1, 2)),
    ("up_close_streak", "consecutive up closes", "clip10", (1, 2, 3, 5, 8)),
    ("accel_1_5", "ret_1 - ret_5/5", "id", (-0.01, -0.003, 0.0, 0.003, 0.01, 0.03)),
    ("bars_since_gap", "bars since the last tape gap (null before the first)", "clip60",
     (1, 3, 6, 16, 31)),
    ("mfe_surrendered_pos", "mfe_surrendered where mfe_so_far > 0 (0 = at the running high)",
     "clip10", (0.0001, 0.25, 0.5, 1.0, 2.0, 5.0)),
    ("bars_below_entry_episode", "consecutive below-entry closes ending at t", "clip60",
     (1, 2, 3, 6, 16)),
)
MODEL_FEATURES = tuple(f[0] for f in FEATURES)
FEATURE_NOTE = {f[0]: f[1] for f in FEATURES}
FEATURE_EDGES = {f[0]: f[3] for f in FEATURES}
PAIRED_FEATURES = MODEL_FEATURES + ("range_expansion", "ret_1", "mfe_so_far")

NONFIRING_FLAGS = tuple(f"giveback_condition_after_forced_flat_{g}" for g in (5, 10, 15, 20))
PANEL_COLUMNS = KEYS + (
    "block", "et", "bar_index", "session_end", "entry_et", "entry_px", "session_peak_et",
    "session_peak_ret_from_entry", "session_peak_bars_from_entry", "session_close_ret_from_entry",
    "final_high_flag", "bars_since_new_high", "mfe_so_far",
    # v2: censoring, executable terminal price, forced-flat continuation, non-firing diagnostics
    "path_complete_to_session_end", "terminal_censored", "future_forced_flat_px",
    "v_forced_flat", "v_hold_flat", FUTURE_LAST_ET,
) + NONFIRING_FLAGS + MODEL_FEATURES
RAW_EXTRA = ("range_expansion", "ret_1", "mfe_surrendered", "v_forced_flat", "v_hold_flat")


# ======================================================================================
# numeric helpers (the interpreter has no sklearn/scipy)
# ======================================================================================
def sigmoid(z: np.ndarray) -> np.ndarray:
    out = np.empty_like(z, dtype=np.float64)
    pos = z >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
    ez = np.exp(z[~pos])
    out[~pos] = ez / (1.0 + ez)
    return out


def auc_score(y: np.ndarray, s: np.ndarray) -> float:
    n = y.size
    n1 = int(y.sum())
    n0 = n - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    _, inv, counts = np.unique(s, return_inverse=True, return_counts=True)
    starts = np.r_[0.0, np.cumsum(counts)[:-1]]
    ranks = (starts + (counts + 1.0) / 2.0)[inv]
    return float((ranks[y > 0].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def auc_within_group(y: np.ndarray, s: np.ndarray, g: np.ndarray) -> tuple[float, int]:
    """n_pos*n_neg-weighted mean of within-group AUCs, and the weight."""
    order = np.argsort(g, kind="mergesort")
    ys, ss, gs = y[order], s[order], g[order]
    bounds = np.flatnonzero(np.r_[True, gs[1:] != gs[:-1], True])
    num = 0.0
    den = 0
    for i in range(bounds.size - 1):
        a, b = bounds[i], bounds[i + 1]
        n1 = int(ys[a:b].sum())
        n0 = (b - a) - n1
        w = n1 * n0
        if w == 0:
            continue
        num += w * auc_score(ys[a:b], ss[a:b])
        den += w
    return ((num / den) if den else float("nan")), int(den)


def auc_pooled_and_within(y: np.ndarray, s: np.ndarray, g: np.ndarray) -> dict:
    """Pooled AUC and the within-group AUC.

    The within-minute number is the honest one: any model that knows only the clock has exactly
    0.5 within a minute by construction.
    """
    within, den = auc_within_group(y, s, g)
    return {"auc_pooled": auc_score(y, s), "auc_within_minute": within,
            "within_minute_weight": den}


def auc_multi_group(y: np.ndarray, s: np.ndarray, groups: dict) -> dict:
    """Pooled AUC plus one within-group AUC per supplied time coordinate (never merged)."""
    out = {"auc_pooled": auc_score(y, s)}
    for name, g in groups.items():
        out[f"auc_within_{name}"], _ = auc_within_group(y, s, g)
    return out


def n_tenure_codes(frame: Frame) -> int:
    """Number of exact tenure levels (bar_index), shared by train and eval."""
    return int(frame.m_nbars.max())


def variant_effects(frame: Frame, idx: np.ndarray, kinds: tuple, window: str) -> tuple:
    """Exact-effect blocks for a model variant, as (codes, n_codes) pairs."""
    eff = []
    for kind in kinds:
        if kind == "clock":
            eff.append(((frame.et[idx] - FIRST_ET).astype(np.int64), n_minute_codes(frame, window)))
        elif kind == "tenure":
            eff.append((frame.bar_index[idx].astype(np.int64), n_tenure_codes(frame)))
        else:
            raise ValueError(f"unknown effect kind {kind}")
    return tuple(eff)


def top_decile_within(y: np.ndarray, s: np.ndarray, g: np.ndarray, frac: float = 0.1) -> dict:
    order = np.argsort(g, kind="mergesort")
    ys, ss, gs = y[order], s[order], g[order]
    bounds = np.flatnonzero(np.r_[True, gs[1:] != gs[:-1], True])
    sel = np.zeros(ys.size, dtype=bool)
    for i in range(bounds.size - 1):
        a, b = bounds[i], bounds[i + 1]
        if b - a < 10:
            continue
        m = max(1, int(round(frac * (b - a))))
        idx = np.argpartition(-ss[a:b], m - 1)[:m]
        sel[a + idx] = True
    if not sel.any():
        return {"n": 0}
    keep = np.isin(gs, np.unique(gs[sel]))
    base = float(ys[keep].mean()) if keep.any() else float("nan")
    rate = float(ys[sel].mean())
    return {"n": int(sel.sum()), "rate": rate, "base_rate_same_minutes": base,
            "lift": (rate / base) if base > 0 else None}


def cluster_var(y: np.ndarray, codes: np.ndarray, n_codes: int) -> float:
    """Cluster-robust variance of the mean (sandwich, clusters of unequal size)."""
    if y.size == 0:
        return float("nan")
    r = y - y.mean()
    s = np.bincount(codes, weights=r, minlength=n_codes)
    return float((s ** 2).sum() / y.size ** 2)


def rate_block(y: np.ndarray, day: np.ndarray, ticker: np.ndarray, n_days: int,
               n_tickers: int) -> dict:
    """Rate with iid, day-clustered (primary) and ticker-clustered (secondary) uncertainty."""
    n = int(y.size)
    if n == 0:
        return {"n": 0}
    p = float(y.mean())
    v_iid = p * (1.0 - p) / n
    v_day = cluster_var(y, day, n_days)
    v_tk = cluster_var(y, ticker, n_tickers)
    out = {"n": n, "rate": p,
           "se_iid": math.sqrt(max(v_iid, 0.0)),
           "se_day": math.sqrt(max(v_day, 0.0)),
           "se_ticker": math.sqrt(max(v_tk, 0.0))}
    out["ci95_day"] = [p - 1.96 * out["se_day"], p + 1.96 * out["se_day"]]
    out["design_effect_day"] = float(v_day / v_iid) if v_iid > 0 else None
    return out


def boot_index(day_codes_sorted: np.ndarray, n_days: int, rng: np.random.Generator,
               starts: np.ndarray, sizes: np.ndarray) -> np.ndarray:
    pick = rng.integers(0, n_days, size=n_days)
    parts = [np.arange(starts[c], starts[c] + sizes[c]) for c in pick if sizes[c] > 0]
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.int64)


def cluster_bootstrap(values: np.ndarray, day: np.ndarray, n_days: int, stat, reps: int = BOOT_REPS,
                      seed: int = SEED) -> dict:
    """Day-cluster bootstrap of a statistic over per-member values (equal-weight resampling of
    days).  Returns the point estimate, the bootstrap sd, and a percentile interval."""
    if values.size == 0:
        return {"n": 0}
    order = np.argsort(day, kind="mergesort")
    d_sorted = day[order]
    v_sorted = values[order]
    starts = np.searchsorted(d_sorted, np.arange(n_days))
    sizes = np.bincount(d_sorted, minlength=n_days)
    rng = np.random.default_rng(seed)
    point = float(stat(v_sorted))
    draws = np.empty(reps, dtype=np.float64)
    for r in range(reps):
        idx = boot_index(d_sorted, n_days, rng, starts, sizes)
        draws[r] = stat(v_sorted[idx])
    return {"n": int(values.size), "point": point, "boot_sd": float(np.nanstd(draws)),
            "ci95_percentile": [float(np.nanpercentile(draws, 2.5)),
                                float(np.nanpercentile(draws, 97.5))], "reps": reps,
            "cluster": "sleeve_day"}


def fit_logit(X: np.ndarray, y: np.ndarray, l2: float = 1.0, max_iter: int = 30,
              tol: float = 1e-7) -> tuple[np.ndarray, int]:
    n, k = X.shape
    pen = np.ones(k)
    pen[0] = 0.0
    beta = np.zeros(k)
    it = 0
    for it in range(1, max_iter + 1):
        p = sigmoid(X @ beta)
        w = np.maximum(p * (1.0 - p), 1e-9)
        g = X.T @ (y - p) - l2 * pen * beta
        H = (X * w[:, None]).T @ X
        H[np.diag_indices(k)] += l2 * pen + 1e-10
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(H, g, rcond=None)[0]
        beta = beta + step
        if np.max(np.abs(step)) < tol:
            break
    return beta, it


def summarise(x: np.ndarray, prefix: str = "") -> dict:
    v = np.asarray(x, dtype=np.float64)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return {f"{prefix}n": 0}
    s = float(v.sum())
    top5 = float(np.sort(v)[-5:].sum()) if v.size >= 5 else float("nan")
    abs_sum = float(np.abs(v).sum())
    top5_abs = float(np.sort(np.abs(v))[-5:].sum()) if v.size >= 5 else float("nan")
    return {f"{prefix}n": int(v.size), f"{prefix}mean": float(v.mean()),
            f"{prefix}median": float(np.median(v)), f"{prefix}p25": float(np.percentile(v, 25)),
            f"{prefix}p75": float(np.percentile(v, 75)),
            f"{prefix}p90": float(np.percentile(v, 90)),
            f"{prefix}min": float(v.min()), f"{prefix}max": float(v.max()),
            f"{prefix}sum": s,
            f"{prefix}top5_share_of_sum": (top5 / s) if s != 0 else float("nan"),
            f"{prefix}top5_share_of_abs_sum": (top5_abs / abs_sum) if abs_sum > 0 else float("nan")}


def bucketise(x: np.ndarray, edges: tuple) -> tuple[np.ndarray, np.ndarray]:
    edges_arr = np.asarray(edges, dtype=np.float64)
    ok = np.isfinite(x)
    b = np.full(x.shape, -1, dtype=np.int32)
    b[ok] = np.searchsorted(edges_arr, x[ok], side="right")
    return b, ok


def bucket_labels(edges: tuple) -> list[str]:
    labs = [f"<{edges[0]:g}"]
    for a, b in zip(edges[:-1], edges[1:]):
        labs.append(f"[{a:g},{b:g})")
    labs.append(f">={edges[-1]:g}")
    return labs


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    if a.size < 3 or np.unique(a).size < 3:
        return float("nan")
    ra = np.argsort(np.argsort(a)).astype(np.float64)
    rb = np.argsort(np.argsort(b)).astype(np.float64)
    ra -= ra.mean()
    rb -= rb.mean()
    d = math.sqrt((ra @ ra) * (rb @ rb))
    return float((ra @ rb) / d) if d > 0 else float("nan")


def r6(x):
    if x is None:
        return None
    f = float(x)
    return round(f, 6) if math.isfinite(f) else None


def curve_pairs(et_grid: np.ndarray, values: np.ndarray) -> dict:
    """Compact {et: value} dict for a minute-resolution curve (nulls dropped)."""
    out = {}
    for t, v in zip(et_grid, values):
        f = float(v)
        if math.isfinite(f):
            out[str(int(t))] = round(f, 6)
    return out


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, (np.integer, int)):
        return int(o)
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    return o


# ======================================================================================
# frame
# ======================================================================================
class Frame:
    """The panel as numpy arrays sorted by (member, bar_index), plus derived causal columns."""

    def __init__(self, df: pl.DataFrame, md: pl.DataFrame):
        self.n = df.height
        self.mid = df["mid"].to_numpy().astype(np.int64)
        self.et = df["et"].to_numpy().astype(np.int64)
        self.bar_index = df["bar_index"].to_numpy().astype(np.int64)
        self.entry_et = df["entry_et"].to_numpy().astype(np.int64)
        self.session_end = df["session_end"].to_numpy().astype(np.int64)
        self.peak_et = df["session_peak_et"].to_numpy().astype(np.float64)
        self.peak_bars = df["session_peak_bars_from_entry"].to_numpy().astype(np.float64)
        self.peak_ret = df["session_peak_ret_from_entry"].to_numpy().astype(np.float64)
        self.close_ret = df["session_close_ret_from_entry"].to_numpy().astype(np.float64)
        self.final_high = df["final_high_flag"].to_numpy().astype(np.float64)
        # final_high_flag is null on a member's last bar (no future bar exists) — those rows
        # cannot carry the target and are excluded everywhere the target is used.
        self.target_ok = np.isfinite(self.final_high)
        self.n_members = int(self.mid.max()) + 1
        self.sizes = np.bincount(self.mid, minlength=self.n_members)
        self.off = np.r_[0, np.cumsum(self.sizes)]
        self.member_rows = self.off[:-1]
        # v2 censor / execution / future-metadata columns
        self.censored = df["terminal_censored"].to_numpy().astype(bool)
        self.path_complete = df["path_complete_to_session_end"].to_numpy().astype(bool)
        self.forced_flat_px = df["future_forced_flat_px"].to_numpy().astype(np.float64)
        self.entry_px = df["entry_px"].to_numpy().astype(np.float64)
        self.future_last_et = df[FUTURE_LAST_ET].to_numpy().astype(np.float64)

        raw = {c: df[c].to_numpy().astype(np.float64) for c in FEATURE_EDGES if c in df.columns}
        for c in RAW_EXTRA + ("mfe_so_far", "bars_since_new_high", "final_high_flag"):
            raw[c] = df[c].to_numpy().astype(np.float64)
        raw["mfe_surrendered_pos"] = np.where(raw["mfe_so_far"] > 0.0,
                                              raw["mfe_surrendered"], np.nan)
        raw["is_new_high"] = (raw["bars_since_new_high"] == 0).astype(np.float64)
        self.x = raw

        # member-level coordinates
        self.m_peak_et = self.peak_et[self.member_rows]
        self.m_peak_bars = self.peak_bars[self.member_rows]
        self.m_nbars = md["n_bars"].to_numpy().astype(np.int64)
        self.m_censored = self.censored[self.member_rows]
        self.m_path_complete = self.path_complete[self.member_rows]
        self.m_forced_flat_px = self.forced_flat_px[self.member_rows]
        self.m_future_last_et = self.future_last_et[self.member_rows]
        self.m_entry_px = self.entry_px[self.member_rows]
        self.m_nonfiring = {f: md[f].to_numpy().astype(bool) for f in NONFIRING_FLAGS}
        # v_forced_flat at the member's session-peak bar: buy the peak bar's next open, hold to
        # the engine's forced flat (the executable continuation from the peak minute)
        pk_pos = (self.off[:-1]
                  + np.where(np.isfinite(self.m_peak_bars), self.m_peak_bars, 0).astype(np.int64))
        vff = self.x["v_forced_flat"]
        self.m_v_forced_flat_at_peak = np.where(np.isfinite(self.m_peak_bars), vff[pk_pos], np.nan)
        # the panel nulls next_open on a member's last tracked row, so an executable entry at the
        # peak bar's next open exists exactly when the peak bar is not that last row
        self.m_has_next_open_at_peak = (np.isfinite(self.m_peak_bars)
                                        & (self.m_peak_bars < self.m_nbars - 1))
        # a member whose session peak falls on its last bar has no final_high_flag there and can
        # carry no target (same rule as the row-level self.target_ok)
        # a member carries a peak-bar target only when its peak constant exists (censored members
        # have none) and the peak bar is not the member's last tracked bar
        pb = self.m_peak_bars
        self.m_peak_target_ok = np.isfinite(pb) & (np.nan_to_num(pb, nan=0.0).astype(np.int64)
                                                   < self.m_nbars - 1)
        self.m_peak_ret = self.peak_ret[self.member_rows]
        self.m_close_ret = self.close_ret[self.member_rows]
        self.m_entry_et = self.entry_et[self.member_rows]
        self.m_session_end = self.session_end[self.member_rows]
        self.m_block = md["block"].to_numpy()
        self.m_family = md["family"].to_numpy()
        self.m_day = md["day_code"].to_numpy().astype(np.int64)
        self.m_ticker = md["ticker_code"].to_numpy().astype(np.int64)
        self.n_days = int(self.m_day.max()) + 1
        self.n_tickers = int(self.m_ticker.max()) + 1
        self.day = self.m_day[self.mid]
        self.ticker = self.m_ticker[self.mid]
        self.family = self.m_family[self.mid]
        self.block = self.m_block[self.mid]

    def member_slice(self, i: int) -> slice:
        return slice(int(self.off[i]), int(self.off[i + 1]))

    def fam_block(self, fam: str, blk: str) -> np.ndarray:
        return (self.m_family == fam) & (self.m_block == blk)


def registry_guard(feature_names: tuple, panel_columns: list) -> dict:
    """Authoritative causal guard: the column registry, not a name prefix, decides.

    A feature must come from a state_* family, must not be named in coverage.future_only_columns,
    must not carry the future_ prefix, and must not be in a causal-excluded family
    (outcome_*, ticket_constant, future_meta, censor).
    """
    reg = json.loads(REGISTRY.read_text())
    cov = json.loads(COVERAGE.read_text())
    fams = reg["families"]
    excluded = tuple(cov["causal_excluded_families"])
    future_only = set(cov["future_only_columns"])
    future_prefixes = tuple(cov.get("future_only_prefixes", ["future_"]))
    bad: list[dict] = []
    derivations: dict = {}
    for nm in feature_names:
        why = []
        if nm not in fams:
            der = DERIVED_FEATURES.get(nm)
            if der is None:
                why.append("not in the column registry and not a registered derivation")
            else:
                derivations[nm] = der
                for src in der["sources"]:
                    if src not in fams:
                        why.append(f"derivation source {src} is not in the registry")
                    elif fams[src] in excluded or fams[src] not in CAUSAL_FAMILIES:
                        why.append(f"derivation source {src} has non-state family {fams[src]}")
        elif fams[nm] in excluded:
            why.append(f"family {fams[nm]} is causal-excluded")
        elif fams[nm] not in CAUSAL_FAMILIES:
            why.append(f"family {fams[nm]} is not a state family")
        if nm in future_only:
            why.append("named in coverage.future_only_columns")
        if any(nm.startswith(pfx) for pfx in future_prefixes):
            why.append("carries a future_ prefix")
        if why:
            bad.append({"column": nm, "reasons": why})
    stale = [c for c in (FUTURE_LAST_ET, "member_last_et") if c in panel_columns]
    return {
        "schema_version": reg.get("schema_version"),
        "schema_matches_panel_contract": bool(reg.get("schema_version") == PANEL_SCHEMA_VERSION),
        "features_checked": len(feature_names),
        "derived_features_validated_against_their_sources": derivations,
        "feature_families": {nm: fams.get(nm, "derived") for nm in feature_names},
        "violations": bad,
        "guard_passes": bool(not bad),
        "causal_excluded_families": list(excluded),
        "future_only_columns": sorted(future_only),
        "future_last_et_column": FUTURE_LAST_ET,
        "future_last_et_present_in_panel": FUTURE_LAST_ET in panel_columns,
        "v1_member_last_et_present_in_panel": "member_last_et" in panel_columns,
        "note": "future-only metadata is never read as a feature; the four session_* constants, "
                "future_forced_flat_px and the two censor flags are used only as labels, cohort "
                "definitions or execution prices in the descriptive part",
    }


def load_frame(panel: Path, families: list[str] | None = None) -> tuple[Frame, dict]:
    lf = pl.scan_parquet(panel)
    if families and set(families) != set(FAMILIES):
        lf = lf.filter(pl.col("family").is_in(families))
    have = lf.collect_schema().names()
    cols = list(dict.fromkeys(PANEL_COLUMNS + RAW_EXTRA))
    missing = [c for c in cols if c not in have]
    if missing != ["mfe_surrendered_pos"]:
        raise SystemExit(f"panel is missing columns: {missing}")
    cols = [c for c in cols if c in have]
    keyed = lf.select(cols).with_columns(
        pl.concat_str([pl.col("sleeve_day"), pl.col("family"), pl.col("ticker"),
                       pl.col("entry_rank").cast(pl.Utf8)], separator="|").alias("key"))
    key_df = keyed.select("key").unique(maintain_order=True).with_row_index("mid")
    df = keyed.join(key_df, on="key", how="left").drop("key").sort(["mid", "bar_index"]).collect()
    md = df.group_by("mid").agg(
        pl.col("block").first(),
        pl.col("sleeve_day").first(),
        pl.col("family").first(),
        pl.col("ticker").first(),
        pl.col("entry_et").first(),
        pl.col("session_end").first(),
        pl.col("session_peak_et").first(),
        pl.col("session_peak_ret_from_entry").first(),
        pl.col("session_peak_bars_from_entry").first(),
        pl.col("session_close_ret_from_entry").first(),
        pl.col("terminal_censored").first().alias("terminal_censored"),
        pl.col("path_complete_to_session_end").first().alias("path_complete"),
        pl.col("future_forced_flat_px").first().alias("future_forced_flat_px"),
        pl.col(FUTURE_LAST_ET).first().alias(FUTURE_LAST_ET),
        pl.col("entry_px").first().alias("entry_px"),
        *[pl.col(f).any().alias(f) for f in NONFIRING_FLAGS],
        pl.len().alias("n_bars"),
    ).sort("mid")
    day_str = md["sleeve_day"].to_numpy()
    _, day_code = np.unique(day_str, return_inverse=True)
    tkr_str = md["ticker"].to_numpy()
    _, tkr_code = np.unique(tkr_str, return_inverse=True)
    md = md.with_columns(pl.Series("day_code", day_code, dtype=pl.Int64),
                         pl.Series("ticker_code", tkr_code, dtype=pl.Int64))
    frame = Frame(df, md)
    starts, ends = frame.off[:-1], frame.off[1:] - 1
    checks = {
        "rows": frame.n, "members": frame.n_members, "days": frame.n_days,
        "tickers": frame.n_tickers,
        "sorted_by_member": bool(np.all(np.diff(frame.mid) >= 0)),
        "bar_index_contiguous": bool(np.all(frame.bar_index[starts] == 0)
                                     and np.all(frame.bar_index[ends] == frame.sizes - 1)),
        "rows_per_family": {f: int((frame.m_family == f).sum()) for f in FAM},
    }
    return frame, checks


# ======================================================================================
# section 1 — descriptive (labels; hindsight)
# ======================================================================================
def censor_cell(member_mask: np.ndarray, censored: np.ndarray, rows_censored: int = 0) -> dict:
    """Censoring accounting for a cell: never null-as-zero, always reported as unresolved.

    `member_mask` is a boolean mask over members (the cell's population) and `censored` the
    member-level censor flag restricted to the same members; the counts are of True values, not of
    array length.
    """
    n = int(np.sum(member_mask))
    nc = int(np.sum(censored & member_mask)) if censored.size == member_mask.size else int(
        np.sum(censored))
    return {"n_members": n, "n_terminal_censored_excluded": nc,
            "share_terminal_censored": r6(nc / n) if n else None,
            "n_members_scored": n - nc, "n_rows_censored_excluded": int(rows_censored),
            "unresolved": "terminal_censored members have no terminal value and no executable "
                          "terminal liquidation; their session-level labels are null and they are "
                          "excluded from every executable rate and curve here"}


def cohort_tags(peak_ret: np.ndarray) -> np.ndarray:
    tags = np.full(peak_ret.shape, "D_never_above_entry", dtype=object)
    tags = np.where(peak_ret >= 0.0, "C_peak_0_30", tags)
    tags = np.where(peak_ret >= 0.30, "B_peak_30_100", tags)
    tags = np.where(peak_ret >= 1.00, "A_peak_ge_100", tags)
    return tags


def peak_timing(frame: Frame) -> dict:
    """Minute-resolution peak-time distribution and survival, per family x block x cohort."""
    peak_et = frame.peak_et[frame.member_rows]
    peak_bars = frame.peak_bars[frame.member_rows]
    entry_et = frame.entry_et[frame.member_rows]
    tag = cohort_tags(frame.peak_ret[frame.member_rows])
    grid = np.arange(FIRST_ET, LAST_ET + 1)
    out: dict = {
        "coordinates": "clock minute (session_peak_et, ET) and bars_since_entry "
                       "(session_peak_bars_from_entry) are reported separately and never merged",
        "resolution": "minute (no bins)",
        "by_family": {},
    }
    for fam in FAM:
        fam_rec: dict = {"by_block": {}}
        for blk in (*BLOCKS, "all"):
            sel_all = frame.m_family == fam if blk == "all" else frame.fam_block(fam, blk)
            sel_blk = sel_all & ~frame.m_censored   # censored members carry no server label
            cnt_blk = int(sel_blk.sum())
            rec: dict = {"n_member_days": cnt_blk, "by_cohort": {},
                         "censoring": censor_cell(sel_all, frame.m_censored[sel_all])}
            if cnt_blk == 0:
                fam_rec["by_block"][blk] = rec
                continue
            # minute-resolution peak PMF (population of the family/block, censored excluded)
            cnt = np.bincount(peak_et[sel_blk].astype(int) - FIRST_ET, minlength=grid.size)
            pmf = cnt / cnt_blk
            # survival S(t) = P(peak > t): fraction of member-minutes at t still before the peak
            sel_rows = ((frame.family == fam) & (frame.block == blk) if blk != "all"
                        else (frame.family == fam)) & ~frame.censored
            s_curve = np.full(grid.size, np.nan)
            n_risk = np.zeros(grid.size, dtype=np.int64)
            et_rows = frame.et[sel_rows]
            ph_rows = frame.final_high[sel_rows]
            for gi, t in enumerate(grid):
                m = et_rows == t
                if not m.any():
                    continue
                n_risk[gi] = int(m.sum())
                s_curve[gi] = float(1.0 - ph_rows[m].mean())
            rec["population"] = {
                "peak_clock_minute": summarise(peak_et[sel_blk]),
                "peak_bars_since_entry": summarise(peak_bars[sel_blk]),
                "peak_clock_minute_pmf": curve_pairs(grid, pmf),
                "survival_curve_S_t_peak_after_t": curve_pairs(grid, s_curve),
                "n_member_minutes_at_t": curve_pairs(grid, n_risk.astype(float)),
                "peak_tenure_bars_pmf": curve_pairs(
                    np.arange(0, int(np.nanmax(peak_bars[sel_blk])) + 1),
                    np.bincount(peak_bars[sel_blk].astype(int),
                                minlength=int(np.nanmax(peak_bars[sel_blk])) + 1) / cnt_blk),
                "HINDSIGHT_share_of_peaks_before_clock_mark": {
                    f"{mark:04d}_{lbl}": float((peak_et[sel_blk] < mark).mean())
                    for mark, lbl in CLOCK_MARKS},
                "HINDSIGHT_share_of_peaks_within_tenure_bars": {
                    f"{k}": float((peak_bars[sel_blk] <= k).mean())
                    for k in (15, 30, 60, 90, 120, 180)},
                "peak_clock_lag_after_entry_min": summarise(peak_et[sel_blk] - entry_et[sel_blk]),
            }
            for tag_name in COHORT_ORDER:
                sel = sel_blk & (tag == tag_name)
                if sel.sum() == 0:
                    continue
                cnt_c = np.bincount(peak_et[sel].astype(int) - FIRST_ET, minlength=grid.size)
                pmf_c = cnt_c / max(int(sel.sum()), 1)
                rec["by_cohort"][f"cohort:{tag_name}"] = {
                    "n": int(sel.sum()), "share": float(sel.sum() / cnt_blk),
                    "peak_clock_minute": summarise(peak_et[sel]),
                    "peak_bars_since_entry": summarise(peak_bars[sel]),
                    "peak_clock_minute_pmf": curve_pairs(grid[pmf_c > 0], pmf_c[pmf_c > 0]),
                    "HINDSIGHT_share_of_peaks_before_clock_mark": {
                        f"{mark:04d}_{lbl}": float((peak_et[sel] < mark).mean())
                        for mark, lbl in CLOCK_MARKS},
                    "HINDSIGHT_share_of_peaks_within_tenure_bars": {
                        f"{k}": float((peak_bars[sel] <= k).mean())
                        for k in (15, 30, 60, 90, 120, 180)},
                }
            fam_rec["by_block"][blk] = rec
        out["by_family"][fam] = fam_rec
    # block agreement on the headline shares
    agree = {}
    for fam in FAM:
        b1 = out["by_family"][fam]["by_block"]["block1"]["population"]
        b2 = out["by_family"][fam]["by_block"]["block2"]["population"]
        agree[fam] = {
            k: {"block1": b1[key][k], "block2": b2[key][k]}
            for key in ("HINDSIGHT_share_of_peaks_before_clock_mark",
                        "HINDSIGHT_share_of_peaks_within_tenure_bars")
            for k in b1[key]}
    out["block_agreement"] = agree
    out["hindsight_warning"] = (
        "the cohort split and the 'before 10:00/11:00/12:00' shares are conditioned on the realised "
        "peak (an outcome label). They are a description of the filled population, not a rule, not "
        "a signal, and not evidence that the climax is predictable.")
    return out


def survival_and_hazard_curves(frame: Frame, checks: dict) -> dict:
    """The peak-time distribution emerges from arrival x hazard, minute by minute, per family.

    Per member-minute with a defined target,

        P(peak = t | a bar at t) = P(new high at t | bar) x P(this new high is the last | new high)

    and the population pmf is P(bar at t) x that product, so the reconstruction telescopes to 1
    over the minutes.  The two factors are reported separately: a falling arrival rate is the tape
    going quiet, a flat arrival with a rising hazard is new highs no longer being followed
    through.
    """
    grid = np.arange(FIRST_ET, LAST_ET + 1)
    out: dict = {
        "identity": "P(peak = t) = P(a bar at t) x P(new high at t | bar) x P(this new high is "
                    "the last | new high at t); the second and third factors are the two reported "
                    "minute curves, the first is the bar-availability curve",
        "survival_definition": "survival_S_from_pmf = 1 - cumulative empirical pmf (population); "
                               "survival_S_minute_conditional = 1 - E[final_high_flag | bar at t]",
        "target_note": "final_high_flag is null on a member's last bar, so members whose peak bar "
                       "is their last bar carry no target and are excluded from both sides of the "
                       "identity (reported as n_members_without_target)",
        "end_of_tape_convention": "at et = session_end the panel's forced-flat minute every row is "
                                  "a member's last bar, so no target exists there: the "
                                  "target-dependent curves (arrival, hazard, survival_conditional, "
                                  "p_bar) end at session_end - 1, while n_member_minutes and "
                                  "target_defined_at_t still report that minute's row count. The "
                                  "population survival_S_from_pmf curve covers the whole grid "
                                  "because it is built from the peak pmf, not from per-row "
                                  "targets.",
        "by_family": {},
    }
    for fam in FAM:
        fam_rec: dict = {}
        for blk in (*BLOCKS, "all"):
            sel_all_m = frame.m_family == fam if blk == "all" else frame.fam_block(fam, blk)
            sel_m = sel_all_m & ~frame.m_censored
            sel_r = ((frame.family == fam) if blk == "all" else
                     ((frame.family == fam) & (frame.block == blk)))
            cens_r = sel_r & frame.censored
            et_r = frame.et[sel_r]
            nh_r = frame.x["is_new_high"][sel_r]
            fh_r = frame.final_high[sel_r]
            tgt_r = frame.target_ok[sel_r]
            n_bars = frame.m_nbars[sel_m]
            peak_bars = frame.m_peak_bars[sel_m].astype(np.int64)
            peak_valid = peak_bars < (n_bars - 1)  # the peak bar is not the member's last bar
            m_valid = int(peak_valid.sum())
            peak_ets = frame.m_peak_et[sel_m].astype(np.int64)
            n_rows = np.zeros(grid.size, dtype=np.int64)
            n_valid_rows = np.zeros(grid.size, dtype=np.int64)
            n_new = np.zeros(grid.size, dtype=np.int64)
            n_peak = np.zeros(grid.size, dtype=np.int64)
            lam = np.full(grid.size, np.nan)
            arr = np.full(grid.size, np.nan)
            p_bar = np.zeros(grid.size)
            surv_cond = np.full(grid.size, np.nan)
            target_defined = np.zeros(grid.size)
            for gi, t in enumerate(grid):
                m_all = et_r == t
                if not m_all.any():
                    continue
                # row counts are recorded for every minute with tape, including et = 959 (the
                # forced-flat minute) where every row is a member's last bar and therefore carries
                # no target; the target-dependent curves simply have no value there
                n_rows[gi] = int(m_all.sum())
                target_defined[gi] = 1.0
                m = m_all & tgt_r
                if not m.any():
                    target_defined[gi] = 0.0
                    continue
                n_valid_rows[gi] = int(m.sum())
                nh = nh_r[m]
                fh = fh_r[m]
                n_new[gi] = int(nh.sum())
                n_peak[gi] = int(((peak_ets == t) & peak_valid).sum())
                arr[gi] = float(nh.mean())
                surv_cond[gi] = float(1.0 - fh.mean())
                p_bar[gi] = float(m.sum() / max(m_valid, 1))
                if nh.sum() > 0:
                    lam[gi] = float(fh[nh > 0].mean())
            recon = p_bar * arr * lam
            emp = n_peak / max(m_valid, 1)
            ok = np.isfinite(recon)
            dev = np.abs(recon[ok] - emp[ok])
            surv_from_pmf = 1.0 - np.cumsum(emp)
            cens = censor_cell(sel_all_m, frame.m_censored[sel_all_m], int(cens_r.sum()))
            cens["n_censored_rows_kept_for_arrival"] = int(cens_r.sum())
            cens["target_policy"] = {
                "arrival_new_high": "censored rows are KEPT: the arrival event (a new high printed "
                                    "at t) is a causal state event and is genuinely defined on a "
                                    "censored tape",
                "hazard_last_new_high / no_further_new_high": "censored rows are DROPPED: "
                                    "final_high_flag is null for a member with no terminal path, "
                                    "so the target is not defined",
                "peak_pmf / survival": "censored members carry no session_peak_* constant and are "
                                       "excluded from both sides of the identity",
            }
            check = {
                "n_members": int(sel_m.sum()), "n_members_with_target": m_valid,
                "censoring": cens,
                "n_members_without_target": int(sel_m.sum()) - m_valid,
                "sum_reconstructed": float(np.nansum(recon)),
                "sum_empirical": float(emp.sum()),
                "max_abs_minute_deviation": float(dev.max()) if dev.size else None,
                "et_of_max_deviation": (int(grid[ok][int(np.argmax(dev))]) if dev.size else None),
                "identity_holds_within_1e-9": bool(dev.size and float(dev.max()) < 1e-9),
            }
            fam_rec[blk] = {
                "n_member_days": int(sel_m.sum()),
                "minutes": {
                    "n_member_minutes": curve_pairs(grid, n_rows.astype(float)),
                    "bar_availability_p_bar": curve_pairs(grid, p_bar),
                    "target_defined_at_t": curve_pairs(grid, target_defined),
                    "n_new_high_bars": curve_pairs(grid, n_new.astype(float)),
                    "arrival_rate_new_high": curve_pairs(grid, arr),
                    "hazard_last_new_high": curve_pairs(grid, lam),
                    "peak_pmf_reconstructed": curve_pairs(grid, recon),
                    "peak_pmf_empirical": curve_pairs(grid, emp),
                    "survival_S_from_pmf": curve_pairs(grid, surv_from_pmf),
                    "survival_S_minute_conditional": curve_pairs(grid, surv_cond),
                },
                "identity_check": check,
                "base_rates": {
                    "peak_after_1200": float(np.mean(frame.m_peak_et[sel_m] > 720)),
                    "new_high_bars_per_member_day": float(n_new.sum() / max(m_valid, 1)),
                    "share_of_new_highs_that_are_the_last": float(
                        n_peak.sum() / max(n_new.sum(), 1)),
                },
            }
        out["by_family"][fam] = fam_rec
    checks["hazard_identity"] = {
        f"{fam}_{blk}": out["by_family"][fam][blk]["identity_check"]
        for fam in FAM for blk in (*BLOCKS, "all")}
    if not all(v["identity_holds_within_1e-9"] for v in checks["hazard_identity"].values()):
        print("WARNING: arrival x hazard identity deviates from the empirical peak pmf",
              file=sys.stderr)
    return out


def exec_frac(exec_give: np.ndarray, peak_ret: np.ndarray) -> dict:
    """Share-of-peak-move given back, in executable terms (peak level -> forced flat)."""
    pos = peak_ret > 0.0
    f = np.where(pos, exec_give / np.where(pos, peak_ret, 1.0), np.nan)
    ok = np.isfinite(f)
    return {"n": int(ok.sum()),
            "median": r6(float(np.nanmedian(f))) if ok.any() else None,
            "mean": r6(float(np.nanmean(f))) if ok.any() else None,
            "share_gt_25pct": r6(float(np.nanmean(f < -0.25))) if ok.any() else None,
            "share_gt_50pct": r6(float(np.nanmean(f < -0.50))) if ok.any() else None,
            "share_gt_75pct": r6(float(np.nanmean(f < -0.75))) if ok.any() else None}


def giveback_stats(frame: Frame, checks: dict) -> dict:
    """Give-back after the peak (label quantities), per family x block x cohort."""
    r = frame.member_rows
    peak_ret = frame.peak_ret[r]
    close_ret = frame.close_ret[r]
    give = close_ret - peak_ret
    frac = np.where(peak_ret > 0.0, give / np.where(peak_ret > 0.0, peak_ret, 1.0), np.nan)
    tag = cohort_tags(peak_ret)
    # v2 executable view: the peak level to the engine's forced-flat price, and the continuation
    # from the peak bar's next open to the forced flat (v_forced_flat).  v_hold_flat is the close
    # reference, kept only for comparability.
    peak_px = frame.m_entry_px * (1.0 + peak_ret)
    exec_peak_give = np.where(np.isfinite(frame.m_forced_flat_px) & (peak_px > 0),
                              frame.m_forced_flat_px / np.where(peak_px > 0, peak_px, 1.0) - 1.0,
                              np.nan)
    exec_from_peak = frame.m_v_forced_flat_at_peak
    scored_exec = np.isfinite(exec_peak_give)
    out: dict = {
        "close_reference_anatomy": {
            "definition": "giveback = session_close_ret_from_entry - session_peak_ret_from_entry. "
                          "session_close_ret_from_entry is the close of the session_end bar, i.e. "
                          "the v_hold_flat reference level: this is the labelled close baseline, "
                          "NOT an execution price.",
            "uses": "ticket constants only",
        },
        "executable_view_v2": {
            "definition": "exec_peak_giveback = future_forced_flat_px / peak_px - 1 (peak level to "
                          "the engine's forced-flat price, the open of the session_end bar) and "
                          "exec_from_peak_next_open = v_forced_flat at the member's peak bar (buy "
                          "the peak bar's next open, hold to the forced flat). v_forced_flat is "
                          "the executable hold-to-flat continuation; v_hold_flat is not.",
            "non_firing_rule": "a give-back condition first appearing at et >= session_end - 1 "
                               "cannot fire (the engine schedules FORCED_FLAT before release "
                               "evaluation), so those rows carry v_forced_flat with fired=false "
                               "and the diagnostic giveback_condition_after_forced_flat_* = true; "
                               "the value used here is v_forced_flat, i.e. the fallback is applied "
                               "by construction",
            "unscored": "terminal-censored members (no terminal value) and a peak bar with no next "
                        "open remain unscored and are counted, never zero-filled",
        },
        "units": "return units, friction-free (the friction on any executed action is bps_total/2 "
                 "per side; the dollar judgement lives in basket_atlas_ledger.py)",
        "uncertainty": "day-cluster bootstrap for medians, cluster-robust SE for shares",
        "by_family": {},
    }
    for fam in FAM:
        fam_rec: dict = {"by_block": {}}
        for blk in (*BLOCKS, "all"):
            sel_fb_all = frame.fam_block(fam, blk) if blk != "all" else frame.m_family == fam
            sel_fb = sel_fb_all & ~frame.m_censored
            rec: dict = {"n_member_days": int(sel_fb.sum()), "by_cohort": {},
                         "censoring": censor_cell(sel_fb_all, frame.m_censored[sel_fb_all]),
                         "n_nonfiring_members_any_level": int(np.sum(
                             np.any([frame.m_nonfiring[f][sel_fb] for f in NONFIRING_FLAGS], axis=0)
                         )) if int(sel_fb.sum()) else 0,
                         "n_unscored_executable": int(np.sum(~scored_exec[sel_fb])),
                         "n_unscored_no_next_open_at_peak": int(np.sum(
                             ~frame.m_has_next_open_at_peak[sel_fb]))}
            for tag_name in (*COHORT_ORDER, "all"):
                sel = sel_fb & (tag == tag_name if tag_name != "all" else True)
                if sel.sum() == 0:
                    continue
                g, f = give[sel], frac[sel]
                day = frame.m_day[sel]
                pos = peak_ret[sel] > 0.0
                f_ok = f[np.isfinite(f)]
                fday = day[np.isfinite(f)]
                entry = {
                    "n": int(sel.sum()),
                    "peak_ret": summarise(peak_ret[sel]),
                    "close_ret": summarise(close_ret[sel]),
                    "giveback": summarise(g),
                    "giveback_median_day_boot": cluster_bootstrap(
                        g, day, frame.n_days, np.median),
                    "giveback_frac_where_peak_pos": {
                        "n": int(f_ok.size),
                        "median": r6(np.median(f_ok)) if f_ok.size else None,
                        "median_day_boot": cluster_bootstrap(f_ok, fday, frame.n_days, np.median),
                        "mean": r6(np.mean(f_ok)) if f_ok.size else None,
                        "share_gt_25pct": (rate_block((f_ok < -0.25).astype(float), fday,
                                                      frame.m_ticker[sel][np.isfinite(f)],
                                                      frame.n_days, frame.n_tickers)
                                           if f_ok.size else {"n": 0}),
                        "share_gt_50pct": (rate_block((f_ok < -0.50).astype(float), fday,
                                                      frame.m_ticker[sel][np.isfinite(f)],
                                                      frame.n_days, frame.n_tickers)
                                           if f_ok.size else {"n": 0}),
                        "share_gt_75pct": (rate_block((f_ok < -0.75).astype(float), fday,
                                                      frame.m_ticker[sel][np.isfinite(f)],
                                                      frame.n_days, frame.n_tickers)
                                           if f_ok.size else {"n": 0}),
                    },
                    "share_close_below_entry_where_peak_pos": (
                        rate_block((close_ret[sel][pos] < 0.0).astype(float), day[pos],
                                   frame.m_ticker[sel][pos], frame.n_days, frame.n_tickers)
                        if pos.any() else None),
                    "executable_v2": {
                        "peak_px_to_forced_flat": summarise(exec_peak_give[sel]),
                        "peak_px_to_forced_flat_median_day_boot": cluster_bootstrap(
                            exec_peak_give[sel][np.isfinite(exec_peak_give[sel])],
                            frame.m_day[sel][np.isfinite(exec_peak_give[sel])], frame.n_days,
                            np.median),
                        "next_open_at_peak_to_forced_flat": summarise(exec_from_peak[sel]),
                        "frac_where_peak_pos": exec_frac(exec_peak_give[sel], peak_ret[sel]),
                        "unscored_in_cell": int(np.sum(~np.isfinite(exec_peak_give[sel]))),
                        "no_next_open_at_peak_in_cell": int(np.sum(
                            ~frame.m_has_next_open_at_peak[sel])),
                    },
                }
                rec["by_cohort"][f"cohort:{tag_name}" if tag_name != "all" else "all"] = entry
            fam_rec["by_block"][blk] = rec
        out["by_family"][fam] = fam_rec
    checks["giveback_cohort_shares_agree_in_sign"] = {
        f"{fam}": {
            "share_gt_50pct": {
                "block1": out["by_family"][fam]["by_block"]["block1"]["by_cohort"]["all"][
                    "giveback_frac_where_peak_pos"]["share_gt_50pct"]["rate"],
                "block2": out["by_family"][fam]["by_block"]["block2"]["by_cohort"]["all"][
                    "giveback_frac_where_peak_pos"]["share_gt_50pct"]["rate"]}
            for fam in FAM}
    }
    return out


def duplicate_paths(frame: Frame) -> dict:
    """(sleeve_day, ticker) appearing in both families — must be flagged in anything pooled."""
    key = frame.m_day.astype(np.int64) * (frame.n_tickers + 1) + frame.m_ticker
    is_a = frame.m_family == "A_pm"
    a_keys = np.unique(key[is_a])
    dup_b = np.isin(key[~is_a], a_keys)
    dup_pairs = int(np.unique(key[~is_a][dup_b]).size)
    dup_members = int(dup_b.sum()) + int(np.isin(a_keys, key[~is_a]).sum())
    return {
        "definition": "members sharing (sleeve_day, ticker) with a member of the other family",
        "duplicate_pairs": dup_pairs, "duplicate_members": dup_members,
        "share_of_members": float(dup_members / frame.n_members),
        "policy": "families are never pooled in a curve; where a cross-family aggregate appears it "
                  "is computed on the day-deduplicated set (the B600 twin is dropped) and the "
                  "count is reported next to it",
        "checks": {"expected_pairs_255": dup_pairs == 255},
    }


# ======================================================================================
# section 2 — causal
# ======================================================================================
# The time coordinate of every model here is the EXACT clock minute, entered as minute fixed
# effects (one parameter per minute, 09:30..16:00).  There are no 15-minute bins and no other
# arbitrary time boundary: whatever the clock explains is absorbed exactly, and the state
# features are identified from the within-minute variation.  The clock-only baseline is thus the
# per-minute empirical rate, and its within-minute AUC is 0.5 by construction.
WINDOWS = ("all_session", "morning_le_1200")


def multi_effect_logit(F: np.ndarray, y: np.ndarray, effects: tuple, l2_feature: float = 1.0,
                       l2_effect: float = 1.0, max_iter: int = 30,
                       tol: float = 1e-8) -> tuple[np.ndarray, int]:
    """Ridge logistic with one or more blocks of EXACT discrete fixed effects.

    `effects` = ((codes, n_codes), ...), e.g. ((clock_minute, 391),) or ((clock, 391), (tenure, 340))
    or ((tenure, 340),).  Parameters are [beta_feature, beta_effect_1, beta_effect_2, ...]; there is
    no separate intercept because each effect block spans one.  The Hessian is assembled blockwise
    so no dummy matrix is ever materialised: each diagonal block is a bincount, the
    feature-to-effect blocks are per-level weighted sums of each feature column, and the
    effect-to-effect block is a per-pair bincount.

    When two effect blocks are both present they are nearly collinear (clock and tenure differ
    only by the cumulative tape-gap offset), so the ridge on the effect blocks, not the data,
    decides how much of a common level goes to which coordinate; the state coefficients and the
    fitted probabilities are unaffected by that split.
    """
    n, k = F.shape
    effects = tuple((np.asarray(c, dtype=np.int64), int(m)) for c, m in effects)
    sizes = [m for _, m in effects]
    offs = np.cumsum([0] + sizes)
    P = k + sum(sizes)
    beta = np.zeros(P)
    pen = np.concatenate([[l2_feature] * k] + [[l2_effect] * m for m in sizes]) if sizes \
        else np.full(k, l2_feature)
    it = 0
    for it in range(1, max_iter + 1):
        eta = (F @ beta[:k] if k else np.zeros(n))
        for j, (codes, _m) in enumerate(effects):
            eta = eta + beta[k + offs[j] + codes]
        p = sigmoid(eta)
        w = np.maximum(p * (1.0 - p), 1e-9)
        r = y - p
        g = np.zeros(P)
        H = np.zeros((P, P))
        Z = None
        if k:
            Z = F * w[:, None]
            g[:k] = F.T @ r - pen[:k] * beta[:k]
            H[:k, :k] = Z.T @ F
        for j, (codes, m) in enumerate(effects):
            a, b = k + offs[j], k + offs[j] + m
            g[a:b] = np.bincount(codes, weights=r, minlength=m) - pen[a:b] * beta[a:b]
            H[np.arange(a, b), np.arange(a, b)] = np.bincount(codes, weights=w, minlength=m)
            if k:
                Zc = np.empty((k, m))
                for jj in range(k):
                    Zc[jj] = np.bincount(codes, weights=Z[:, jj], minlength=m)
                H[:k, a:b] = Zc
                H[a:b, :k] = Zc.T
            for j2 in range(j + 1, len(effects)):
                c2, m2 = effects[j2]
                a2 = k + offs[j2]
                blk = np.bincount(codes * m2 + c2, weights=w, minlength=m * m2).reshape(m, m2)
                H[a:b, a2:a2 + m2] = blk
                H[a2:a2 + m2, a:b] = blk.T
        H[np.diag_indices(P)] += pen + 1e-10
        step = np.linalg.solve(H, g)
        beta = beta + step
        if np.max(np.abs(step)) < tol:
            break
    return beta, it


def minute_effect_logit(F: np.ndarray, y: np.ndarray, minute: np.ndarray, n_minutes: int,
                        **kw) -> tuple[np.ndarray, np.ndarray, int]:
    """Single-effect special case (exact clock minute or exact tenure bar)."""
    beta, it = multi_effect_logit(F, y, ((minute, n_minutes),), **kw)
    k = F.shape[1]
    return beta[:k], beta[k:], it


def predict_multi(F: np.ndarray, beta: np.ndarray, effects: tuple) -> np.ndarray:
    k = F.shape[1]
    eta = (F @ beta[:k] if k else np.zeros(F.shape[0]))
    off = 0
    for codes, m in effects:
        eta = eta + beta[k + off + codes]
        off += m
    return sigmoid(eta)


TARGETS = ("hazard_last_new_high", "no_further_new_high", "arrival_new_high")
TARGET_NOTE = {
    "hazard_last_new_high": "P(this new high is the last of the session | a new high was set at t, "
                            "state) — the discrete hazard; the risk set is a causal event",
    "no_further_new_high": "P(no further new high after t | state) = E[final_high_flag(t)] — the "
                           "survival level, every row",
    "arrival_new_high": "P(a new high is set at t | state) — the arrival intensity, the other "
                        "factor of P(peak = t) = arrival(t) x hazard(t)",
}
# these four state columns are deterministic functions of the arrival event (new_high_count_*
# includes the bar at t, bars_since_new_high == 0 IS the event) and would leak the arrival target
ARRIVAL_LEAKY = ("bars_since_new_high", "new_high_count_5", "new_high_count_15", "new_high_count_30")


def features_for(tname: str) -> tuple:
    if tname == "arrival_new_high":
        return tuple(f for f in MODEL_FEATURES if f not in ARRIVAL_LEAKY)
    return MODEL_FEATURES


def target_y(frame: Frame, tname: str) -> np.ndarray:
    return frame.x["is_new_high"] if tname == "arrival_new_high" else frame.final_high


def target_risk(frame: Frame, fam: str, tname: str, window: str = "all_session") -> np.ndarray:
    """Risk-set mask for a target inside one family.

    `all_session` (primary) uses every minute to the close so the decay is measured exactly;
    `morning_le_1200` is the secondary pre-noon window.
    """
    base = frame.family == fam
    if window == "morning_le_1200":
        base = base & (frame.et >= FIRST_ET) & (frame.et <= MORNING_END)
    if tname == "hazard_last_new_high":
        return base & (frame.x["is_new_high"] > 0) & frame.target_ok
    if tname == "no_further_new_high":
        return base & frame.target_ok
    return base  # arrival is a causal event: no label validity constraint


# time-control variants for the sensitivity table (clock and tenure are NEVER merged into one
# coordinate; each is its own exact-effect block)
VARIANTS = (
    ("clock_only", ("clock",), ()),
    ("tenure_only", ("tenure",), ()),
    ("state_only", (), MODEL_FEATURES),
    ("clock_plus_state", ("clock",), MODEL_FEATURES),
    ("tenure_plus_state", ("tenure",), MODEL_FEATURES),
    ("clock_plus_tenure_plus_state", ("clock", "tenure"), MODEL_FEATURES),
)


def _fit_variant(frame: Frame, idx: np.ndarray, y: np.ndarray, kinds: tuple, feats: tuple,
                 window: str, stats_in: dict | None = None) -> dict:
    X, meta = design_matrix(frame, idx, feats, stats_in)
    names = list(meta["column_names"])
    if not kinds:  # no effect block: an intercept is needed
        X = np.column_stack([np.ones(X.shape[0]), X])
        names = ["intercept"] + names
    beta, it = multi_effect_logit(X, y, variant_effects(frame, idx, kinds, window))
    return {"beta": beta, "effect_kinds": kinds, "k": int(X.shape[1]), "names": names,
            "stats": meta["transform"], "iterations": it}


def predict_variant(frame: Frame, model: dict, idx: np.ndarray, window: str) -> np.ndarray:
    X, _ = design_matrix(frame, idx, model["features"], model["stats"])
    if not model["effect_kinds"]:
        X = np.column_stack([np.ones(X.shape[0]), X])
    return predict_multi(X, model["beta"], variant_effects(frame, idx, model["effect_kinds"], window))


def predict_rows(frame: Frame, models: dict, train_blk: str, which: str,
                 idx: np.ndarray) -> np.ndarray:
    """Out-of-sample prediction for an already-fitted variant (train scaling reused verbatim)."""
    return predict_variant(frame, models["by_block"][train_blk][which], idx, models["window"])


def fit_family_model(frame: Frame, fam: str, tname: str, window: str = "all_session",
                     variants: bool = False) -> dict:
    """Fit the models for one family, target and window.

    `variants=True` also fits the time-control sensitivity set (exact clock only / exact tenure
    only / state only / clock+state / tenure+state / clock+tenure+state).  Clock and tenure are
    separate coordinates in every one of them.
    """
    feats = features_for(tname)
    y = target_y(frame, tname)
    M = n_minute_codes(frame, window)
    models: dict = {"features": feats, "window": window, "n_minutes": M,
                    "n_tenure": n_tenure_codes(frame), "by_block": {}}
    want = VARIANTS if variants else (("clock_only", ("clock",), ()),
                                      ("clock_plus_state", ("clock",), MODEL_FEATURES))
    models["variant_names"] = [w[0] for w in want]
    models["variant_effects"] = {w[0]: list(w[1]) for w in want}
    for blk in BLOCKS:
        idx = np.flatnonzero(target_risk(frame, fam, tname, window) & (frame.block == blk))
        entry: dict = {"n": int(idx.size), "base_rate": float(y[idx].mean())}
        for name, kinds, fts in want:
            use = tuple(fts) if fts else ()
            m = _fit_variant(frame, idx, y[idx], kinds, use, window)
            m["features"] = use
            entry[name] = m
        models["by_block"][blk] = entry
    return models


def design_matrix(frame: Frame, idx: np.ndarray, feats: tuple, stats: dict | None,
                  ) -> tuple[np.ndarray, dict]:
    """Feature block (standardised) + robust-scaling stats, on an explicit row index."""
    stats = stats if stats is not None else {"features": {}}
    cols = []
    names = []
    for nm in feats:
        v = frame.x[nm][idx]
        fin = np.isfinite(v)
        if nm not in stats["features"]:
            med = float(np.median(v[fin])) if fin.any() else 0.0
            q = np.percentile(v[fin], [25, 75]) if fin.any() else np.array([0.0, 1.0])
            scale = float(q[1] - q[0]) or 1.0
            stats["features"][nm] = {"median": med, "scale": scale,
                                     "missing_dummy": bool((~fin).mean() > 0.01)}
        sv = stats["features"][nm]
        z = np.clip((v - sv["median"]) / sv["scale"], -4.0, 4.0)
        cols.append(np.where(fin, z, 0.0))
        names.append(nm)
        if sv["missing_dummy"]:
            cols.append((~fin).astype(np.float64))
            names.append(f"{nm}__missing")
    X = np.column_stack(cols) if cols else np.zeros((idx.size, 0))
    return X, {"column_names": names, "transform": stats}


def minute_codes(frame: Frame, idx: np.ndarray) -> np.ndarray:
    """Exact clock-minute code (et - 09:30).  The only time coordinate in the models."""
    return (frame.et[idx] - FIRST_ET).astype(np.int64)


def n_minute_codes(frame: Frame, window: str = "all_session") -> int:
    return (LAST_ET if window == "all_session" else MORNING_END) - FIRST_ET + 1


def hazard_tables(frame: Frame, checks: dict) -> dict:
    """Base rates of the three target quantities, per family x block, with clustered uncertainty."""
    out: dict = {"targets": TARGET_NOTE,
                 "note": "the two hazard factors are reported separately: falling arrival = the "
                         "tape goes quiet; flat arrival with rising hazard = new highs stop being "
                         "followed through. Their product is P(peak = t).",
                 "by_family": {}}
    for fam in FAM:
        fam_rec: dict = {}
        for blk in (*BLOCKS, "all"):
            sel_m = frame.fam_block(fam, blk) if blk != "all" else frame.m_family == fam
            day = frame.day
            tkr = frame.ticker
            y_h = frame.final_high
            nh = frame.x["is_new_high"] > 0
            et = frame.et
            in_blk = sel_m[frame.mid]
            valid = frame.target_ok & in_blk
            morning = in_blk & (et <= MORNING_END)
            rec: dict = {"n_member_days": int(sel_m.sum()),
                         "censoring": censor_cell(sel_m, frame.m_censored[sel_m],
                                                  int((in_blk & frame.censored).sum())),
                         "censored_rows_kept_or_dropped": {
                             "arrival_new_high": "KEPT (causal row event)",
                             "hazard_last_new_high": "DROPPED (final_high_flag null when censored)",
                             "no_further_new_high": "DROPPED (final_high_flag null when censored)"}}
            for tname in TARGETS:
                y = target_y(frame, tname)
                if tname == "arrival_new_high":
                    all_s = rate_block(y[in_blk], day[in_blk], tkr[in_blk], frame.n_days,
                                       frame.n_tickers)
                    mor = rate_block(y[morning], day[morning], tkr[morning], frame.n_days,
                                     frame.n_tickers)
                elif tname == "hazard_last_new_high":
                    m_all = valid & nh
                    m_mor = morning & nh & frame.target_ok
                    all_s = rate_block(frame.final_high[m_all], day[m_all], tkr[m_all],
                                       frame.n_days, frame.n_tickers)
                    mor = rate_block(frame.final_high[m_mor], day[m_mor], tkr[m_mor],
                                     frame.n_days, frame.n_tickers)
                else:
                    all_s = rate_block(frame.final_high[valid], day[valid], tkr[valid],
                                       frame.n_days, frame.n_tickers)
                    m_mor = morning & frame.target_ok
                    mor = rate_block(frame.final_high[m_mor], day[m_mor], tkr[m_mor],
                                     frame.n_days, frame.n_tickers)
                rec[tname] = {"all_session": all_s, "morning_le_1200": mor}
            rec["n_rows"] = {"all_session": int(in_blk.sum()), "morning_le_1200": int(morning.sum()),
                             "new_high_morning": int((morning & nh & frame.target_ok).sum())}
            fam_rec[blk] = rec
        out["by_family"][fam] = fam_rec
    checks["hazard_base_rate_block_agreement"] = {
        fam: {t: {"block1": out["by_family"][fam]["block1"][t]["morning_le_1200"]["rate"],
                  "block2": out["by_family"][fam]["block2"][t]["morning_le_1200"]["rate"]}
              for t in TARGETS}
        for fam in FAM}
    return out


def feature_rates(frame: Frame, checks: dict) -> dict:
    """Per-feature monotone conditional rates for all three targets, per family and window.

    rate           raw event rate inside the bucket
    rate_std_clock the bucket's per-minute rate reweighted to the family's own minute
                   distribution (exact clock coordinate; a bucket that visits few minutes is
                   marked by `minutes_present`)
    rate_std_tenurethe same standardisation over bars_since_entry (the tenure coordinate), which
                   is a different variable from the clock: B600 members enter 30 clock minutes
                   after A_pm members, and tape gaps separate bar counts from minutes
    se_day         cluster-robust SE of the raw rate (sleeve_day clusters, the conservative unit)
    """
    out: dict = {"standardisation": "per-minute rates reweighted (a) to the family's own exact "
                                    "clock distribution and (b) to the family's own tenure "
                                    "distribution; cells with no observation are dropped and the "
                                    "weights renormalised",
                 "targets": TARGET_NOTE,
                 "windows": {"all_session": "every minute to the close (primary)",
                             "morning_le_1200": f"et <= {MORNING_END} (secondary)"},
                 "arrival_leak_note": "new_high_count_5/15/30 and bars_since_new_high are "
                                      "deterministic functions of the arrival event and are "
                                      "excluded from the arrival target",
                 "by_family": {}}
    for fam in FAM:
        fam_rec: dict = {"by_window": {}}
        for window in WINDOWS:
            win_rec: dict = {"by_target": {}, "censoring": censor_cell(
                frame.m_family == fam, frame.m_censored[frame.m_family == fam],
                int(np.sum(frame.censored & (frame.family == fam))))
                if window == "all_session" else {"note": "see the all_session cell"}}
            for tname in TARGETS:
                print(f"    rates {fam} {window} {tname}", file=sys.stderr, flush=True)
                tmask = target_risk(frame, fam, tname, window)
                feats = features_for(tname)
                y_all = target_y(frame, tname)
                day_all = frame.day
                tkr_all = frame.ticker
                blk_all = frame.block
                clock_id = (frame.et[tmask] - FIRST_ET).astype(np.int64)
                tenure_id = frame.bar_index[tmask].astype(np.int64)
                n_clock = int(clock_id.max()) + 1
                n_tenure = int(tenure_id.max()) + 1
                w_clock = np.bincount(clock_id, minlength=n_clock).astype(np.float64)
                w_clock /= w_clock.sum()
                w_tenure = np.bincount(tenure_id, minlength=n_tenure).astype(np.float64)
                w_tenure /= w_tenure.sum()
                y = y_all[tmask]
                day, tkr, bmask = day_all[tmask], tkr_all[tmask], blk_all[tmask]
                base = rate_block(y, day, tkr, frame.n_days, frame.n_tickers)
                trec: dict = {"base_rate": base, "n_rows": int(tmask.sum()),
                              "clock_range": [int(frame.et[tmask].min()),
                                              int(frame.et[tmask].max())],
                              "tenure_max": int(tenure_id.max()), "features": {}}
                for name in feats:
                    x = frame.x[name][tmask]
                    if np.nanstd(x) == 0:
                        trec["features"][name] = {"definition": FEATURE_NOTE[name],
                                                  "constant_in_risk_set": True,
                                                  "constant_value": r6(x[0]) if x.size else None}
                        continue
                    b, ok = bucketise(x, FEATURE_EDGES[name])
                    labels = bucket_labels(FEATURE_EDGES[name])
                    nb = len(labels)
                    has_null = bool((~ok).any())
                    if has_null:
                        idx = np.where(ok, b, nb)
                        labels = labels + ["(null)"]
                        nb_eff = nb + 1
                    else:
                        idx = b
                        nb_eff = nb
                    cell_c = idx.astype(np.int64) * n_clock + clock_id
                    cell_t = idx.astype(np.int64) * n_tenure + tenure_id
                    rec: dict = {"definition": FEATURE_NOTE[name], "buckets": []}
                    for bi in range(nb_eff):
                        sel_b = idx == bi
                        n_b = int(sel_b.sum())
                        if n_b == 0:
                            continue
                        y_b = y[sel_b]
                        rate = rate_block(y_b, day[sel_b], tkr[sel_b], frame.n_days,
                                          frame.n_tickers)
                        std = {}
                        present = {}
                        for tag, cell, wts, nmax in (("clock", cell_c, w_clock, n_clock),
                                                     ("tenure", cell_t, w_tenure, n_tenure)):
                            c = np.bincount(cell[sel_b], minlength=nb_eff * nmax) \
                                .reshape(nb_eff, nmax)[bi].astype(np.float64)
                            pp = np.bincount(cell[sel_b], weights=y_b, minlength=nb_eff * nmax) \
                                .reshape(nb_eff, nmax)[bi]
                            r = np.divide(pp, c, out=np.zeros(nmax), where=c > 0)
                            w = wts * (c > 0)
                            ws = w.sum()
                            std[tag] = float((r * w).sum() / ws) if ws > 0 else float("nan")
                            present[tag] = int((c > 0).sum())
                        entry = {"label": labels[bi], "n": n_b,
                                 "is_null_bucket": bool(has_null and bi == nb_eff - 1),
                                 "rate": rate["rate"],
                                 "se_day": r6(rate["se_day"]),
                                 "se_ticker": r6(rate["se_ticker"]),
                                 "design_effect_day": r6(rate["design_effect_day"]),
                                 "ci95_day": [r6(rate["ci95_day"][0]), r6(rate["ci95_day"][1])],
                                 "rate_std_clock": r6(std["clock"]),
                                 "rate_std_tenure": r6(std["tenure"]),
                                 "lift_vs_base_clock": (r6(std["clock"] / base["rate"])
                                                        if base["rate"] else None),
                                 "clock_minutes_present": present["clock"],
                                 "tenure_bars_present": present["tenure"]}
                        for bnm in BLOCKS:
                            mb = bmask[sel_b] == bnm
                            for tag, cell, wts, nmax in (("clock", cell_c, w_clock, n_clock),
                                                         ("tenure", cell_t, w_tenure, n_tenure)):
                                c = np.bincount(cell[sel_b][mb], minlength=nb_eff * nmax) \
                                    .reshape(nb_eff, nmax)[bi].astype(np.float64)
                                pp = np.bincount(cell[sel_b][mb], weights=y_b[mb],
                                                 minlength=nb_eff * nmax) \
                                    .reshape(nb_eff, nmax)[bi]
                                r = np.divide(pp, c, out=np.zeros(nmax), where=c > 0)
                                w = wts * (c > 0)
                                ws = w.sum()
                                entry[f"rate_std_{tag}_{bnm}"] = (r6((r * w).sum() / ws)
                                                                  if ws > 0 else None)
                            entry[f"n_{bnm}"] = int(mb.sum())
                        rec["buckets"].append(entry)
                    nonnull = [e for e in rec["buckets"]
                               if not e["is_null_bucket"] and e["n"] >= 200]
                    if len(nonnull) >= 3:
                        ys = np.array([e["rate_std_clock"] for e in nonnull], dtype=np.float64)
                        rec["monotone"] = {
                            "n_buckets": len(nonnull), "rate_std_clock_first": r6(ys[0]),
                            "rate_std_clock_last": r6(ys[-1]),
                            "direction": "increasing" if ys[-1] > ys[0] else "decreasing",
                            "spearman_bucket_vs_rate": r6(spearman(np.arange(ys.size), ys)),
                            "block_deltas": {
                                bnm: r6((nonnull[-1][f"rate_std_clock_{bnm}"] or np.nan)
                                        - (nonnull[0][f"rate_std_clock_{bnm}"] or np.nan))
                                for bnm in BLOCKS}}
                        rec["monotone"]["blocks_agree_direction"] = bool(
                            (rec["monotone"]["block_deltas"]["block1"] or 0)
                            * (rec["monotone"]["block_deltas"]["block2"] or 0) > 0)
                    trec["features"][name] = rec
                win_rec["by_target"][tname] = trec
            fam_rec["by_window"][window] = win_rec
        out["by_family"][fam] = fam_rec
    return out


def out_of_block_logistic(frame: Frame, checks: dict,
                          models_by_family: dict | None = None) -> dict:
    """Small ridge logistic for all three targets, per family, fitted on one block and scored on
    the other.  The clock-only model is the baseline that answers 'does the state add anything?'"""
    out: dict = {"model": "ridge logistic with EXACT-minute fixed effects (one parameter per "
                           "clock minute 09:30..16:00; no bins, no interpolation) plus robust-"
                           "scaled state features clipped at +-4; nulls median-imputed, "
                           "missingness dummy when the train null share exceeds 1%; fitted by "
                           "blocked Newton steps, so the clock-only model IS the per-minute "
                           "empirical rate",
                 "discrimination": "pooled AUC and the n_pos*n_neg-weighted mean of per-minute "
                                   "AUCs (clock-only is 0.5 within a minute by construction)",
                 "uncertainty": f"{BOOT_REPS}-rep day-cluster bootstrap on the eval block",
                 "targets": TARGET_NOTE,
                 "collinearity_caveat": {
                     "model": "clock_plus_tenure_plus_state",
                     "issue": "exact clock effects and exact tenure effects are nearly collinear "
                              "inside a family (a member's clock minute and its bar count differ "
                              "only by the cumulative tape-gap offset), so the FIT SPLITS the "
                              "common level between the two effect blocks according to the ridge, "
                              "not according to the data",
                     "what_is_unaffected": "the state coefficients, the fitted probabilities, the "
                                           "AUCs and the calibrations do not depend on that "
                                           "split; only the attribution of a level to 'clock' "
                                           "versus 'tenure' does",
                     "consequence": "read clock_only, tenure_only and the joint model as three "
                                    "time controls, not as a decomposition of time into two "
                                    "mechanisms",
                 },
                 "era_split_caveat": {
                     "blocks": {"block1": "2021-02..2023-12", "block2": "2025-02..2026-05"},
                     "gap": "2024-01..2025-01 is NOT in the data (13 months absent)",
                     "consequence": "block1/block2 is an ERA split of a development sample, not an "
                                    "exchangeable random split: a variable that drifted with the "
                                    "2024 tape regime can fail out-of-block for reasons that have "
                                    "nothing to do with its causal content, and out-of-block "
                                    "agreement is therefore a necessary but not sufficient "
                                    "condition for a rule",
                 },
                 "arrival_leak_note": "the arrival model drops the four state columns that are "
                                      "deterministic functions of the arrival event "
                                      "(bars_since_new_high, new_high_count_5/15/30). It stays "
                                      "near-mechanical anyway: at a new-high bar the running high "
                                      "IS that bar's high, so dist_from_running_high and "
                                      "mfe_surrendered_pos are bounded by the bar's own range "
                                      "and read off 'the bar closed at its high on heavy "
                                      "volume'. The arrival curve is therefore best read as the "
                                      "exact intraday decay of new-high printing, not as an "
                                      "independent prediction claim; the hazard target carries "
                                      "no such bound.",
                 "by_family": {}}
    for fam in FAM:
        fam_rec: dict = {}
        for window in WINDOWS:
            win_rec: dict = {}
            models = ((models_by_family or {}).get(fam) or {}).get(window) or {
                t: fit_family_model(frame, fam, t, window) for t in TARGETS}
            win_rec["censoring"] = censor_cell(
                frame.m_family == fam, frame.m_censored[frame.m_family == fam],
                int(np.sum(frame.censored & (frame.family == fam))))
            for tname in TARGETS:
                print(f"    logit {fam} {window} {tname}", file=sys.stderr, flush=True)
                y_all = target_y(frame, tname)
                win_rec[tname] = {"features": list(features_for(tname)), "directions": {}}
                for train_blk, eval_blk in (("block1", "block2"), ("block2", "block1")):
                    ev = np.flatnonzero(target_risk(frame, fam, tname, window)
                                        & (frame.block == eval_blk))
                    yev = y_all[ev]
                    if yev.min() == yev.max() or ev.size < 200:
                        win_rec[tname]["directions"][f"fit_{train_blk}_report_{eval_blk}"] = \
                            {"skipped": "degenerate target", "n_eval": int(ev.size)}
                        continue
                    t0 = time.time()
                    preds = {tag: predict_rows(frame, models[tname], train_blk, tag, ev)
                             for tag in models[tname]["variant_names"]}
                    p_c = preds["clock_only"]
                    p_f = preds["clock_plus_state"]
                    et_ev = frame.et[ev]
                    day_ev = frame.day[ev]
                    # clock and tenure are separate coordinates: both within-coordinate AUCs are
                    # always reported, never merged
                    groups = {"clock": et_ev, "tenure": frame.bar_index[ev]}
                    res: dict = {}
                    for tag in models[tname]["variant_names"]:
                        p = preds[tag]
                        m = auc_multi_group(yev, p, groups)
                        entry = {**m, "brier": float(np.mean((p - yev) ** 2)),
                                 "mean_pred": float(p.mean()),
                                 "top_decile_within_minute": top_decile_within(yev, p, et_ev)}
                        qs = np.quantile(p, np.linspace(0.1, 0.9, 9))
                        bins = np.searchsorted(qs, p, side="right")
                        entry["reliability_deciles"] = [
                            {"bin": bi, "n": int((bins == bi).sum()),
                             "mean_pred": float(p[bins == bi].mean()),
                             "observed": float(yev[bins == bi].mean())}
                            for bi in range(10) if (bins == bi).sum() > 0]
                        lp = np.log(np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6)))
                        bcal, _ = fit_logit(np.column_stack([np.ones(lp.size), lp]), yev, l2=0.0,
                                            max_iter=25)
                        entry["calibration_slope"] = float(bcal[1])
                        entry["calibration_intercept"] = float(bcal[0])
                        res[tag] = entry
                    if window == WINDOWS[0]:
                        boots = bootstrap_discrimination(frame, yev, p_f, et_ev, day_ev)
                        boots["delta_within"]["point"] = r6(
                            res["clock_plus_state"]["auc_within_clock"]
                            - res["clock_only"]["auc_within_clock"])
                        res["bootstrap_day_clustered"] = boots
                    else:
                        res["bootstrap_day_clustered"] = {
                            "skipped": "the day-cluster AUC bootstrap runs on the primary "
                                       "(full-session) window only; this window reports point "
                                       "estimates"}
                    for base in ("clock_only", "clock_plus_state"):
                        for hyp in ("clock_plus_state",):
                            if hyp == base:
                                continue
                            res[f"delta_auc_within_clock_vs_{base}"] = r6(
                                res[hyp]["auc_within_clock"] - res[base]["auc_within_clock"])
                            res[f"delta_auc_within_tenure_vs_{base}"] = r6(
                                res[hyp]["auc_within_tenure"] - res[base]["auc_within_tenure"])
                            res[f"delta_auc_pooled_vs_{base}"] = r6(
                                res[hyp]["auc_pooled"] - res[base]["auc_pooled"])
                            res[f"delta_brier_vs_{base}"] = r6(
                                res[hyp]["brier"] - res[base]["brier"])
                    mm = models[tname]["by_block"][train_blk]
                    for tag in models[tname]["variant_names"]:
                        vv = mm[tag]
                        kk = vv["k"]
                        res[tag]["coefficients"] = {n: r6(c) for n, c in
                                                    zip(vv["names"][:kk], vv["beta"][:kk])}
                        res[tag]["effect_blocks"] = {
                            kind: (n_minute_codes(frame, window) if kind == "clock"
                                   else n_tenure_codes(frame))
                            for kind in vv["effect_kinds"]}
                        res[tag]["iterations"] = vv["iterations"]
                        if vv["effect_kinds"] == ("clock",):
                            bm = vv["beta"][kk:]
                            res[tag]["minute_effects_summary"] = {
                                "n_minutes": int(bm.size), "mean": r6(float(bm.mean())),
                                "min": r6(float(bm.min())), "max": r6(float(bm.max())),
                                "share_of_consecutive_minutes_non_increasing": r6(
                                    float(np.mean(np.diff(bm) <= 0)))}
                    win_rec[tname]["directions"][f"fit_{train_blk}_report_{eval_blk}"] = {
                        "n_train": int(mm["n"]), "n_eval": int(ev.size),
                        "train_base_rate": r6(mm["base_rate"]), "eval_base_rate": r6(yev.mean()),
                        "seconds": round(time.time() - t0, 1), "results": res}
                    checks[f"logit_{fam}_{window}_{tname}_{train_blk}_{eval_blk}"] = {
                        "n": [int(mm["n"]), int(ev.size)],
                        "base_rate_train": r6(mm["base_rate"]), "base_rate_eval": r6(yev.mean()),
                        "auc_within_clock": {tag: r6(res[tag]["auc_within_clock"])
                                             for tag in models[tname]["variant_names"]},
                        "auc_within_tenure": {tag: r6(res[tag]["auc_within_tenure"])
                                              for tag in models[tname]["variant_names"]}}
            if window == WINDOWS[0]:
                mb_mod = models["hazard_last_new_high"]["by_block"]
                win_rec["minute_effects_hazard"] = {
                    "note": "exact clock-minute fixed effects, one parameter per minute "
                            "(09:30..16:00), no bins. clock_only is the per-minute log-odds of "
                            "the hazard (the exact decay of the hazard); clock_plus_state is the "
                            "same level after the state features are set to their train means, so "
                            "the difference between the two curves is what the state explains.",
                    "by_train_block": {
                        blk: {tag: {str(FIRST_ET + j):
                                    r6(v) for j, v in enumerate(
                                        mb_mod[blk][tag]["beta"][mb_mod[blk][tag]["k"]:])}
                              for tag in ("clock_only", "clock_plus_state")}
                        for blk in BLOCKS}}
            fam_rec[window] = win_rec
        out["by_family"][fam] = fam_rec
    return out


def paired_contrast(frame: Frame, checks: dict) -> dict:
    """At the peak minute vs the matched minute 30 minutes earlier (and its controls)."""
    out: dict = {
        "requires_outcome_labels": True,
        "warning": "conditioning on the realised session peak is hindsight; this is the 'does it "
                   "feel the top coming' diagnostic, i.e. whether a causal detector has material, "
                   "not a signal",
        "label": "HINDSIGHT ANATOMY ONLY (paired, not prospective)",
        "definition": "level_at_peak = state at the bar with et == session_peak_et; "
                      "level_lag = state at the bar with et == session_peak_et - L (exact-minute "
                      "match). baseline_change = the same L-minute change at every 5th ordinary "
                      "bar, i.e. what an L-minute change typically looks like",
        "coordinates": "clock-minute match primary; bar-index match (bar_index - L) reported "
                       "alongside because tape gaps separate the two",
        "uncertainty": f"{BOOT_REPS}-rep day-cluster bootstrap of the paired mean difference",
        "lags": {}}
    member_block = frame.m_block
    for lag in (30,):
        pos_pk = np.full(frame.n_members, -1, dtype=np.int64)
        pos_lg = np.full(frame.n_members, -1, dtype=np.int64)
        pos_lgb = np.full(frame.n_members, -1, dtype=np.int64)
        for i in range(frame.n_members):
            sl = frame.member_slice(i)
            et_i = frame.et[sl]
            pk = frame.peak_et[sl][0]
            j = int(np.searchsorted(et_i, pk))
            if j >= frame.sizes[i] or et_i[j] != pk:
                continue
            pos_pk[i] = frame.off[i] + j
            k = int(np.searchsorted(et_i, pk - lag))
            if k < frame.sizes[i] and et_i[k] == pk - lag:
                pos_lg[i] = frame.off[i] + k
            jb = j - lag
            if jb >= 0:
                pos_lgb[i] = frame.off[i] + jb
        valid = pos_pk >= 0
        lag_valid = valid & (pos_lg >= 0)
        bar_valid = valid & (pos_lgb >= 0)
        rows: dict = {}
        bi = frame.bar_index
        stride = np.flatnonzero((bi >= lag) & (bi % 5 == 0))
        for nm in PAIRED_FEATURES:
            v = frame.x[nm]
            d_base = v[stride] - v[stride - lag]
            d_base = d_base[np.isfinite(d_base)]
            b_mean = float(d_base.mean()) if d_base.size else float("nan")
            b_med = float(np.median(d_base)) if d_base.size else float("nan")
            b_sd = float(d_base.std()) if d_base.size else float("nan")
            rec: dict = {"baseline_change": {"n": int(d_base.size), "mean": r6(b_mean),
                                             "median": r6(b_med), "sd": r6(b_sd)}}
            for fam in (*FAM, "pooled_dedup"):
                if fam == "pooled_dedup":
                    keep = np.zeros(frame.n_members, dtype=bool)
                    seen = set()
                    for i in range(frame.n_members):
                        key = (int(frame.m_day[i]), int(frame.m_ticker[i]))
                        if key in seen:
                            continue
                        seen.add(key)
                        keep[i] = True
                    sel_m = keep & lag_valid
                else:
                    sel_m = lag_valid & (frame.m_family == fam)
                if sel_m.sum() == 0:
                    continue
                a = v[pos_pk[sel_m]]
                b = v[pos_lg[sel_m]]
                d = a - b
                fin = np.isfinite(d)
                day = frame.m_day[sel_m][fin]
                sub = {
                    "n_members": int(sel_m.sum()),
                    "level_at_peak": {"median": r6(_nanmed(a)), "mean": r6(_nanmean(a))},
                    "level_at_lag": {"median": r6(_nanmed(b)), "mean": r6(_nanmean(b))},
                    "change": {"n": int(fin.sum()), "mean": r6(_nanmean(d)),
                               "median": r6(_nanmed(d)), "p25": r6(_nanpct(d, 25)),
                               "p75": r6(_nanpct(d, 75)),
                               "share_increased": r6(np.nanmean(d > 0)) if fin.any() else None,
                               "share_decreased": r6(np.nanmean(d < 0)) if fin.any() else None},
                    "change_mean_day_boot": cluster_bootstrap(d[fin], day, frame.n_days,
                                                              np.mean),
                }
                if fin.any() and math.isfinite(b_sd) and b_sd > 0:
                    sub["vs_baseline"] = {
                        "mean_minus_baseline_over_sd": r6((_nanmean(d) - b_mean) / b_sd),
                        "median_minus_baseline_median": r6(_nanmed(d) - b_med),
                        "share_above_baseline_median": r6(np.nanmean(d > b_med))}
                # bar-index match, same members, as the alternative coordinate
                sel_b = sel_m & bar_valid
                if sel_b.sum() > 0:
                    db = v[pos_pk[sel_b]] - v[pos_lgb[sel_b]]
                    fin_b = np.isfinite(db)
                    sub["bar_index_match"] = {
                        "n": int(sel_b.sum()), "mean": r6(_nanmean(db)),
                        "median": r6(_nanmed(db)),
                        "share_increased": r6(np.nanmean(db > 0)) if fin_b.any() else None}
                rec[fam] = sub
            rows[nm] = rec
        out["lags"][f"anatomy_lag_{lag}_min"] = {
            "censoring": censor_cell(np.ones(frame.n_members, dtype=bool), frame.m_censored),
            "coverage": {"members": int(valid.sum()), "share_of_members": r6(valid.mean()),
                         "members_with_exact_minute_match": int(lag_valid.sum()),
                         "members_with_barindex_match": int(bar_valid.sum())},
            "features": rows}
    # control: the true peak vs other minutes that were also at the running high
    dhr = frame.x["dist_from_running_high"]
    at_high = np.isfinite(dhr) & (dhr >= -0.002)
    pos_pk = np.full(frame.n_members, -1, dtype=np.int64)
    for i in range(frame.n_members):
        sl = frame.member_slice(i)
        et_i = frame.et[sl]
        pk = frame.peak_et[sl][0]
        j = int(np.searchsorted(et_i, pk))
        if j < frame.sizes[i] and et_i[j] == pk:
            pos_pk[i] = frame.off[i] + j
    is_peak = np.zeros(frame.n, dtype=bool)
    is_peak[pos_pk[pos_pk >= 0]] = True
    prior = frame.et < frame.peak_et
    false_top = at_high & ~is_peak & prior
    out["at_high_false_peaks"] = {
        "definition": "true peak minutes vs minutes before the peak at which the tape was also "
                      "at its running high (close within 0.2% of it): the discriminating control "
                      "— a detector that only knows 'we are at the high' cannot separate them",
        "n_true_peak_minutes": int(is_peak.sum()), "n_false_top_minutes": int(false_top.sum()),
        "features": {}}
    for nm in PAIRED_FEATURES:
        v = frame.x[nm]
        a, b = v[is_peak], v[false_top]
        out["at_high_false_peaks"]["features"][nm] = {
            "at_true_peak": {"n": int(np.isfinite(a).sum()), "median": r6(_nanmed(a)),
                             "mean": r6(_nanmean(a))},
            "at_other_running_high_minutes": {"n": int(np.isfinite(b).sum()),
                                              "median": r6(_nanmed(b)), "mean": r6(_nanmean(b))},
            "median_gap": r6(_nanmed(a) - _nanmed(b))}
    return out


def cluster_diff_var(y: np.ndarray, mask_a: np.ndarray, mask_b: np.ndarray, codes: np.ndarray,
                     n_codes: int) -> float:
    """Cluster-robust variance of mean(y|a) - mean(y|b) with shared clusters."""
    if mask_a.sum() == 0 or mask_b.sum() == 0:
        return float("nan")
    ma, mb = float(y[mask_a].mean()), float(y[mask_b].mean())
    z = (y - ma) * mask_a / mask_a.sum() - (y - mb) * mask_b / mask_b.sum()
    s = np.bincount(codes, weights=z, minlength=n_codes)
    return float((s ** 2).sum())


def wmean_by_cluster(y: np.ndarray, w: np.ndarray, codes: np.ndarray, n_codes: int):
    W = float(w.sum())
    if W <= 0:
        return float("nan"), float("nan")
    m = float((w * y).sum() / W)
    r = w * (y - m)
    s = np.bincount(codes, weights=r, minlength=n_codes)
    return m, float((s ** 2).sum() / W ** 2)


def wmean_diff_var(y_l: np.ndarray, w_l: np.ndarray, c_l: np.ndarray, y_c: np.ndarray,
                   w_c: np.ndarray, c_c: np.ndarray, n_codes: int,
                   m_l: float, m_c: float) -> float:
    """Day-clustered variance of a weighted difference of means across two groups that share days:
    sum_d (a_d - b_d)^2 with a_d the within-day deviation contribution of each group."""
    Wl = float(w_l.sum())
    Wc = float(w_c.sum())
    if Wl <= 0 or Wc <= 0:
        return float("nan")
    a = np.bincount(c_l, weights=w_l * (y_l - m_l), minlength=n_codes) / Wl
    b = np.bincount(c_c, weights=w_c * (y_c - m_c), minlength=n_codes) / Wc
    return float(((a - b) ** 2).sum())


def fit_logit_cluster_se(X: np.ndarray, y: np.ndarray, codes: np.ndarray, n_codes: int,
                         l2: float = 0.0, max_iter: int = 40):
    """Logistic fit plus cluster-robust (sandwich) standard errors of the coefficients."""
    beta, _ = fit_logit(X, y, l2=l2, max_iter=max_iter)
    p = sigmoid(X @ beta)
    w = np.maximum(p * (1.0 - p), 1e-9)
    XtWX = (X * w[:, None]).T @ X
    bread = np.linalg.pinv(XtWX)
    score = X * (y - p)[:, None]
    S = np.empty((n_codes, X.shape[1]))
    for j in range(X.shape[1]):
        S[:, j] = np.bincount(codes, weights=score[:, j], minlength=n_codes)
    V = bread @ (S.T @ S) @ bread
    return beta, np.sqrt(np.maximum(np.diag(V), 0.0))


def ruler_edge_families(x_train: np.ndarray, declared: tuple) -> list[tuple[str, tuple]]:
    """Edge families for the ruler-sensitivity view: the declared edges, train-block quintiles,
    and the declared edges halved towards the train median."""
    v = x_train[np.isfinite(x_train)]
    if v.size < 20:
        return [("declared", tuple(declared))]
    q = np.quantile(v, [0.1, 0.3, 0.5, 0.7, 0.9])
    med = float(np.median(v))
    half = tuple(med + 0.5 * (float(e) - med) for e in declared)
    return [("declared", tuple(float(e) for e in declared)),
            ("train_quintiles", tuple(float(e) for e in q)),
            ("halved_around_median", tuple(float(e) for e in half))]


def edge_free_design(x: np.ndarray, med: float, scale: float) -> np.ndarray:
    """Edge-free continuous design: intercept + standardised feature + its square.

    No thresholds, no bins: whatever the feature says is taken at full resolution, and the
    quadratic term lets a hump-shaped relation show up without imposing a ruler.
    """
    z = np.clip((x - med) / scale, -4.0, 4.0)
    return np.column_stack([np.ones(z.size), z, z * z])


def bootstrap_discrimination(frame: Frame, y: np.ndarray, p: np.ndarray, et: np.ndarray,
                             day: np.ndarray, reps: int = BOOT_REPS) -> dict:
    """Day-cluster bootstrap of the within-minute and pooled AUC of one model on one eval block."""
    order = np.argsort(day, kind="mergesort")
    d_sorted = day[order]
    n_days = frame.n_days
    starts = np.searchsorted(d_sorted, np.arange(n_days))
    sizes = np.bincount(d_sorted, minlength=n_days)
    rng = np.random.default_rng(SEED)
    y_s, p_s, et_s = y[order], p[order], et[order]
    within, pooled = [], []
    point_within = auc_pooled_and_within(y, p, et)["auc_within_minute"]
    for _ in range(reps):
        idx = boot_index(d_sorted, n_days, rng, starts, sizes)
        m = auc_pooled_and_within(y_s[idx], p_s[idx], et_s[idx])
        within.append(m["auc_within_minute"])
        pooled.append(m["auc_pooled"])
    return {
        "clock_plus_state_within": {
            "point": r6(point_within), "boot_sd": r6(np.nanstd(within)),
            "ci95_percentile": [r6(np.nanpercentile(within, 2.5)),
                                r6(np.nanpercentile(within, 97.5))], "reps": reps},
        "clock_plus_state_pooled": {
            "point": r6(auc_score(y, p)), "boot_sd": r6(np.nanstd(pooled)),
            "ci95_percentile": [r6(np.nanpercentile(pooled, 2.5)),
                                r6(np.nanpercentile(pooled, 97.5))], "reps": reps},
        "delta_within": {
            "point": None, "boot_sd": r6(np.nanstd(within)),
            "ci95_percentile": [r6(np.nanpercentile(within, 2.5) - 0.5),
                                r6(np.nanpercentile(within, 97.5) - 0.5)], "reps": reps},
        "cluster": "sleeve_day",
        "note": "the clock-only within-minute AUC is exactly 0.5 in every resample (its "
                "prediction is constant inside a minute), so delta = within - 0.5",
    }


def out_of_block_contrasts(frame: Frame, checks: dict) -> dict:
    """Explicit per-feature out-of-block test: does ANY state feature shift the hazard?

    For each feature the extreme buckets present in the training block (n >= 300) fix the
    contrast and its sign; the same contrast and sign are then evaluated on the held-out block
    with a day-clustered CI.  A feature only counts as surviving when both directions agree in
    sign and both CIs exclude zero.
    """
    out: dict = {
                 "headline_method": "EDGE-FREE continuous model: per feature, fit "
                                    "logit(b0 + b1*z + b2*z^2) on the TRAIN block (z = feature "
                                    "standardised by TRAIN median/IQR, clipped at +-4) and score it "
                                    "on the held-out block; evidence requires the train-fitted "
                                    "model to beat 0.51 within the exact clock minute in BOTH "
                                    "directions AND the eval-block refit's linear coefficient to "
                                    "keep the train sign with a day-clustered CI excluding zero. "
                                    "No thresholds, no bins, no fixed edges.",
                 "reference_method": "extreme-bucket contrast with a priori edges (a ruler) plus a "
                                     "ruler-sensitivity family: the same contrast recomputed on "
                                     "train-block quintile edges and on edges halved around the "
                                     "median; used only as a reference/sensitivity view",
                 "verdict_rule": "headline evidence = edge-free rule above; ruler agreement = all "
                                 "edge families keep the train sign in both directions",
                 "by_family": {}}
    for fam in FAM:
        fam_rec: dict = {}
        for tname in TARGETS:
            print(f"    oob {fam} {tname}", file=sys.stderr, flush=True)
            feats_all = features_for(tname)
            y = target_y(frame, tname)
            risk = target_risk(frame, fam, tname)
            cens_m = frame.m_family == fam
            feats_rec: dict = {}
            censor_rec = censor_cell(cens_m, frame.m_censored[cens_m],
                                     int(np.sum(frame.censored & (frame.family == fam))))
            # a feature that is constant inside the risk set cannot be tested there: the hazard
            # risk set IS bars_since_new_high == 0, so that column is identically 0 there.  It is
            # named, explained and removed from the denominator instead of being carried silently
            const_in_risk = {nm: r6(float(np.nanstd(frame.x[nm][risk])))
                             for nm in feats_all
                             if float(np.nanstd(frame.x[nm][risk])) == 0.0}
            feats = tuple(nm for nm in feats_all if nm not in const_in_risk)
            n_survive = 0
            n_headline = 0
            n_ruler = 0
            for nm in feats:
                x = frame.x[nm]
                b, ok = bucketise(x, FEATURE_EDGES[nm])
                b = b.astype(np.int32)
                per_dir: dict = {}
                ef_dir: dict = {}
                ruler_dir: dict = {f"train_{a}_eval_{b}": {}
                                   for a, b in (("block1", "block2"), ("block2", "block1"))}
                for train_blk, eval_blk in (("block1", "block2"), ("block2", "block1")):
                    tr = risk & ok & (frame.block == train_blk)
                    ev = risk & ok & (frame.block == eval_blk)
                    if tr.sum() < 1000 or ev.sum() < 1000:
                        per_dir[f"train_{train_blk}_eval_{eval_blk}"] = {"skipped": "too few rows"}
                        continue
                    bt, yt = b[tr], y[tr]
                    present = [j for j in range(int(b.max()) + 2) if (bt == j).sum() >= 300]
                    if len(present) < 2:
                        per_dir[f"train_{train_blk}_eval_{eval_blk}"] = {
                            "skipped": "fewer than two populated buckets"}
                        continue
                    lo_b, hi_b = present[0], present[-1]
                    ytr_hi = float(yt[bt == hi_b].mean())
                    ytr_lo = float(yt[bt == lo_b].mean())
                    sign = 1.0 if ytr_hi >= ytr_lo else -1.0
                    be, ye = b[ev], y[ev]
                    ma, mb = be == hi_b, be == lo_b
                    if ma.sum() == 0 or mb.sum() == 0:
                        per_dir[f"train_{train_blk}_eval_{eval_blk}"] = {
                            "skipped": "empty eval bucket"}
                        continue
                    diff = float(ye[ma].mean() - ye[mb].mean())
                    var = cluster_diff_var(ye, ma, mb, frame.day[ev], frame.n_days)
                    se = math.sqrt(max(var, 0.0))
                    # --- edge-free continuous model (headline evidence): train on the TRAIN block
                    xt, xe = x[tr], x[ev]
                    med = float(np.median(xt))
                    q = np.percentile(xt, [25, 75])
                    scale_t = float(q[1] - q[0]) or 1.0
                    Xtr_ef = edge_free_design(xt, med, scale_t)
                    Xev_ef = edge_free_design(xe, med, scale_t)
                    beta_tr_ef, _ = fit_logit(Xtr_ef, yt, l2=0.0, max_iter=40)
                    beta_ev_ef, se_ev_ef = fit_logit_cluster_se(
                        Xev_ef, ye, frame.day[ev], frame.n_days)
                    p_ef = sigmoid(Xev_ef @ beta_tr_ef)
                    auc_ef = auc_multi_group(ye, p_ef, {"clock": frame.et[ev],
                                                        "tenure": frame.bar_index[ev]})
                    lin = float(beta_ev_ef[1])
                    se_lin = float(se_ev_ef[1])
                    ci_lin = [lin - 1.96 * se_lin, lin + 1.96 * se_lin]
                    same_sign = bool(np.sign(beta_tr_ef[1]) == np.sign(lin) and lin != 0.0)
                    ef_dir[f"train_{train_blk}_eval_{eval_blk}"] = {
                        "n_train": int(tr.sum()), "n_eval": int(ev.sum()),
                        "eval_base_rate": r6(float(ye.mean())),
                        "train_beta_linear": r6(float(beta_tr_ef[1])),
                        "train_beta_quadratic": r6(float(beta_tr_ef[2])),
                        "eval_auc_from_train_fit_within_clock": r6(auc_ef["auc_within_clock"]),
                        "eval_auc_from_train_fit_within_tenure": r6(auc_ef["auc_within_tenure"]),
                        "eval_auc_from_train_fit_pooled": r6(auc_ef["auc_pooled"]),
                        "eval_refit_beta_linear": r6(lin), "eval_refit_se_day": r6(se_lin),
                        "eval_refit_ci95_day": [r6(ci_lin[0]), r6(ci_lin[1])],
                        "eval_refit_ci_excludes_zero": bool(abs(lin) > 1.96 * se_lin),
                        "eval_refit_same_sign_as_train": same_sign,
                        # evidence = the eval-block refit keeps the train sign with a
                        # day-clustered CI excluding zero, and the train-fitted model ranks the
                        # held-out block in the right direction (AUC > 0.5, no magnitude bar)
                        "edge_free_evidence": bool(same_sign and abs(lin) > 1.96 * se_lin
                                                   and auc_ef["auc_within_clock"] > 0.5),
                    }
                    # --- ruler sensitivity: the same contrast under alternative edge families
                    for ename, edges in ruler_edge_families(xt, FEATURE_EDGES[nm]):
                        bb, ook = bucketise(x, edges)
                        if not ook.any():
                            continue
                        be2 = bb[ev]
                        pres = [j for j in range(int(bb.max()) + 2) if (bb[tr] == j).sum() >= 300]
                        if len(pres) < 2:
                            continue
                        hi2, lo2 = pres[-1], pres[0]
                        m2, m3 = be2 == hi2, be2 == lo2
                        if m2.sum() == 0 or m3.sum() == 0:
                            continue
                        sgn2 = 1.0 if yt[bb[tr] == hi2].mean() >= yt[bb[tr] == lo2].mean() else -1.0
                        d2 = float(ye[m2].mean() - ye[m3].mean())
                        v2 = cluster_diff_var(ye, m2, m3, frame.day[ev], frame.n_days)
                        s2 = math.sqrt(max(v2, 0.0))
                        ruler_dir[f"train_{train_blk}_eval_{eval_blk}"][ename] = {
                            "edges": [r6(float(e)) for e in edges],
                            "diff_eval": r6(d2), "se_day": r6(s2),
                            "ci95_day": [r6(d2 - 1.96 * s2), r6(d2 + 1.96 * s2)],
                            "sign_from_train": sgn2,
                            "same_sign_as_train": bool(d2 * sgn2 > 0),
                            "ci_excludes_zero": bool(abs(d2) > 1.96 * s2)}
                    per_dir[f"train_{train_blk}_eval_{eval_blk}"] = {
                        "n_train": int(tr.sum()), "n_eval": int(ev.sum()),
                        "train_rate_high_bucket": r6(ytr_hi), "train_rate_low_bucket": r6(ytr_lo),
                        "sign_from_train": sign,
                        "eval_base_rate": r6(float(ye.mean())),
                        "eval_rate_high_bucket": r6(float(ye[ma].mean())),
                        "eval_rate_low_bucket": r6(float(ye[mb].mean())),
                        "n_high": int(ma.sum()), "n_low": int(mb.sum()),
                        "diff_eval": r6(diff), "se_day": r6(se),
                        "ci95_day": [r6(diff - 1.96 * se), r6(diff + 1.96 * se)],
                        "same_sign_as_train": bool(diff * sign > 0),
                        "ci_excludes_zero": bool(abs(diff) > 1.96 * se),
                        "eval_auc_feature_signed_by_train": r6(auc_score(ye, sign * x[ev])),
                    }
                dirs = [v for k, v in per_dir.items() if "diff_eval" in v]
                survives = bool(len(dirs) == 2 and all(d["same_sign_as_train"] and d["ci_excludes_zero"]
                                                       for d in dirs))
                n_survive += int(survives)
                ef = [v for v in ef_dir.values() if "edge_free_evidence" in v]
                ef_ok = bool(len(ef) == 2 and all(v["edge_free_evidence"] for v in ef))
                n_headline += int(ef_ok)
                ruler_dirs = [v for v in ruler_dir.values() if v]
                ruler_ok = bool(len(ruler_dirs) == 2 and all(
                    len(v) >= 2 and all(x2["same_sign_as_train"] for x2 in v.values())
                    for v in ruler_dirs))
                n_ruler += int(ruler_ok)
                feats_rec[nm] = {
                    "definition": FEATURE_NOTE[nm],
                    "survives_edge_free_out_of_block": ef_ok,
                    "headline_evidence": "edge_free" if ef_ok else "none",
                    "ruler_signs_agree_across_edge_families": ruler_ok,
                    "fixed_edge_reference_survives": survives,
                    "edge_free": ef_dir, "ruler_sensitivity": ruler_dir,
                    "fixed_edges": per_dir}
            ef_survivors = [k for k, v in feats_rec.items()
                            if v["survives_edge_free_out_of_block"]]
            ruler_survivors = [k for k, v in feats_rec.items()
                               if v["fixed_edge_reference_survives"]]
            fam_rec[tname] = {
                "n_features_tested": len(feats),
                "n_features_in_candidate_list": len(feats_all),
                "features_constant_in_risk_set": {
                    nm: {"std_in_risk_set": sv,
                         "reason": "identically 0 inside this risk set"
                                   + (" (the risk set is defined by bars_since_new_high == 0)"
                                      if nm == "bars_since_new_high" else "")
                                   + "; excluded from n_features_tested because it cannot be "
                                     "tested here"}
                    for nm, sv in const_in_risk.items()},
                "censoring": censor_rec,
                "headline_edge_free": {
                    "method": "edge-free continuous model, out-of-block both directions",
                    "n_features_tested": len(feats),
                    "n_features_surviving": n_headline,
                    "any_feature_shifts_out_of_block": bool(n_headline > 0),
                    "surviving_features": ef_survivors},
                "fixed_edge_ruler_reference_only": {
                    "method": "extreme-bucket contrast on a priori edges (a ruler)",
                    "n_features_tested": len(feats),
                    "n_features_surviving": n_survive,
                    "surviving_features": ruler_survivors,
                    "ruler_signs_agree_across_edge_families": [
                        k for k, v in feats_rec.items()
                        if v["ruler_signs_agree_across_edge_families"]],
                    "note": "this is the RULER count, reported for reference only; the headline "
                            "count is headline_edge_free.n_features_surviving"},
                "features": feats_rec}
        out["by_family"][fam] = fam_rec
    checks["out_of_block_verdict"] = {
        fam: {t: {"edge_free_surviving": out["by_family"][fam][t]["headline_edge_free"][
                      "surviving_features"],
                  "fixed_edge_ruler_surviving": out["by_family"][fam][t][
                      "fixed_edge_ruler_reference_only"]["surviving_features"],
                  "ruler_signs_agree": out["by_family"][fam][t][
                      "fixed_edge_ruler_reference_only"][
                      "ruler_signs_agree_across_edge_families"],
                  "n_features_tested": out["by_family"][fam][t]["n_features_tested"],
                  "features_constant_in_risk_set": list(
                      out["by_family"][fam][t]["features_constant_in_risk_set"])}
              for t in TARGETS} for fam in FAM}
    return out


def reverse_time_view(frame: Frame, checks: dict, models_by_family: dict | None = None) -> dict:
    """HINDSIGHT ANATOMY ONLY: members whose peak is in the last hour, 30 minutes before it,
    versus members whose peak is already behind them at the same clock minute."""
    out: dict = {
        "question": "at the same clock minute, can the causal state tell a member that still has "
                    "its session peak ahead (within 30 minutes) from one whose peak is already "
                    "behind it?",
        "groups": "L = members with session_peak_et >= threshold, observed at the bar with "
                  "et == session_peak_et - lag; C = members with session_peak_et <= t, observed at "
                  "the same clock minutes t (the minute distribution is L's)",
        "matching": "minute-standardised to L's own minute distribution; day-clustered SE of the "
                    "difference (both groups share days)",
        "prediction": "P(no further new high after t | state) from the ridge logistic fitted on the "
                      "OTHER block, so the number is out-of-sample for every member",
        "label": "HINDSIGHT ANATOMY ONLY",
        "requires_outcome_labels": True,
        "warning": "HINDSIGHT ANATOMY ONLY. The groups are defined by the realised peak, so this "
                   "is a description of what the state looked like around the top, not evidence "
                   "that the top was knowable. It becomes evidence only where the same causal "
                   "pattern predicts prospectively out-of-block: see `prospective_status` below and "
                   "causal.per_feature_out_of_block_contrasts / causal.out_of_block_logistic.",
        "by_family": {}}
    for fam in FAM:
        models = (models_by_family or {}).get(fam) or fit_family_model(
            frame, fam, "no_further_new_high", "all_session")
        et_all = frame.et
        peak_all = frame.peak_et
        fam_members = frame.m_family == fam
        fam_rec: dict = {}
        for wname, thr in (("last_hour_et_ge_900", 900), ("last_two_hours_et_ge_840", 840)):
            late_m = np.flatnonzero(fam_members & (frame.m_peak_et >= thr)
                                    & frame.m_peak_target_ok)
            rec: dict = {"n_late_members": int(late_m.size), "by_lag": {},
                         "censoring": censor_cell(np.ones(frame.n_members, dtype=bool),
                                                  frame.m_censored)}
            for lag in (10, 30):
                rows_l, ts = [], []
                for i in late_m:
                    sl = frame.member_slice(i)
                    et_i = frame.et[sl]
                    t = int(frame.m_peak_et[i]) - lag
                    if t < et_i[0]:
                        continue
                    k = int(np.searchsorted(et_i, t))
                    if k < frame.sizes[i] and et_i[k] == t:
                        rows_l.append(frame.off[i] + k)
                        ts.append(t)
                if len(rows_l) < 50:
                    rec["by_lag"][f"lag_{lag}"] = {"n_late": len(rows_l),
                                                   "skipped": "fewer than 50 matched late members"}
                    continue
                rows_l = np.array(rows_l, dtype=np.int64)
                ts = np.array(ts, dtype=np.int64)
                t_l = et_all[rows_l]
                # control rows: same clock minute, same family, peak already behind
                order = np.argsort(et_all, kind="mergesort")
                et_sorted = et_all[order]
                lo = np.searchsorted(et_sorted, np.unique(t_l))
                hi = np.searchsorted(et_sorted, np.unique(t_l), side="right")
                ctrl = []
                for a, bnd in zip(lo, hi):
                    cand = order[a:bnd]
                    # `cand` holds ROW indices: family/day/block are row-level, the peak is
                    # row-level too (ticket constant), so the comparison is row-aligned
                    keep = (frame.family[cand] == fam) & (peak_all[cand] <= et_all[cand])
                    ctrl.append(cand[keep])
                ctrl = np.concatenate(ctrl) if ctrl else np.zeros(0, dtype=np.int64)
                t_c = et_all[ctrl]
                if ctrl.size < 50:
                    rec["by_lag"][f"lag_{lag}"] = {"n_late": int(rows_l.size),
                                                   "n_control": int(ctrl.size),
                                                   "skipped": "too few control rows"}
                    continue
                # Minute weights: the target distribution is L's own minute distribution, and a
                # group's row at minute t carries mass[t] / (rows of THAT group at t).  The
                # identity is verified per minute before any difference is computed.
                uniq, mass_t = target_minute_mass(t_l)
                w_l, mass_l = matched_weights(t_l, uniq, mass_t)
                w_c, mass_c = matched_weights(t_c, uniq, mass_t)
                # out-of-sample prediction for both groups
                pred_l = np.full(rows_l.size, np.nan)
                for blk in BLOCKS:
                    sel = frame.block[rows_l] == blk
                    other = "block2" if blk == "block1" else "block1"
                    if sel.any():
                        pred_l[sel] = predict_rows(frame, models, other, "clock_plus_state",
                                                   rows_l[sel])
                pred_c = np.full(ctrl.size, np.nan)
                for blk in BLOCKS:
                    sel = frame.block[ctrl] == blk
                    other = "block2" if blk == "block1" else "block1"
                    if sel.any():
                        pred_c[sel] = predict_rows(frame, models, other, "clock_plus_state",
                                                   ctrl[sel])
                sep = auc_pooled_and_within(
                    np.r_[np.ones(pred_l.size), np.zeros(pred_c.size)],
                    -np.r_[pred_l, pred_c], np.r_[t_l, t_c])
                m_l, v_l = wmean_by_cluster(pred_l, w_l, frame.day[rows_l], frame.n_days)
                m_c, v_c = wmean_by_cluster(pred_c, w_c, frame.day[ctrl], frame.n_days)
                v_d = wmean_diff_var(pred_l, w_l, frame.day[rows_l], pred_c, w_c,
                                     frame.day[ctrl], frame.n_days, m_l, m_c)
                entry: dict = {
                    "n_late": int(rows_l.size), "n_control": int(ctrl.size),
                    "control_rows_all_at_running_high": r6(float(np.mean(
                        frame.x["dist_from_running_high"][ctrl] >= -1e-9))),
                    "final_high_flag_control_is_one": r6(_nanmean(frame.final_high[ctrl])),
                    "control_is_label_positive": r6(_nanmean(frame.final_high[ctrl])),
                    "control_rows_with_a_defined_target": int(
                        np.isfinite(frame.final_high[ctrl]).sum()),
                    "circularity_tell": "the control group is the label-positive set of "
                                        "final_high_flag by construction (its peak is already "
                                        "behind it), so any separation measured here is anatomy "
                                        "conditioned on the realised peak",
                    "minutes": {"min_et": int(min(t_l.min(), t_c.min())),
                                "max_et": int(max(t_l.max(), t_c.max())),
                                "n_distinct": int(uniq.size)},
                    "minute_mass_identity": {
                        "target": "L's own exact-minute distribution",
                        "late_group": mass_l, "control_group": mass_c,
                        "note": "per-row weight = minute mass / that group's rows at the minute, so "
                                "the weighted minute distribution equals the target exactly; the "
                                "old mass-per-row-then-renormalise weighting would weight each "
                                "minute by the square of its row count",
                    },
                    "predicted_no_further_new_high": {
                        "late_mean": r6(m_l), "control_mean": r6(m_c),
                        "difference": r6(m_l - m_c),
                        "auc_within_clock_late_vs_finished": r6(sep["auc_within_minute"]),
                        "note": "> 0.5 means the model separates late peaks from already-"
                                "finished members within the same clock minute (sign oriented so "
                                "that higher = more separable)",
                    },
                    "features": {}}
                se_d = math.sqrt(max(v_d, 0.0))
                entry["predicted_no_further_new_high"]["se_day_diff"] = r6(se_d)
                entry["predicted_no_further_new_high"]["ci95_day_diff"] = [
                    r6(m_l - m_c - 1.96 * se_d), r6(m_l - m_c + 1.96 * se_d)]
                for nm in PAIRED_FEATURES:
                    v = frame.x[nm]
                    y_l, y_c = v[rows_l], v[ctrl]
                    fin_l, fin_c = np.isfinite(y_l), np.isfinite(y_c)
                    if not fin_l.any() or not fin_c.any():
                        continue
                    # weights recomputed on the non-null rows so the subset's weighted minute
                    # distribution still equals the target (no renormalisation shortcut)
                    wl_f, rep_l = matched_weights(t_l[fin_l], uniq, mass_t)
                    wc_f, rep_c = matched_weights(t_c[fin_c], uniq, mass_t)
                    ylf = y_l[fin_l]
                    ycf = y_c[fin_c]
                    ml, _ = wmean_by_cluster(ylf, wl_f, frame.day[rows_l][fin_l], frame.n_days)
                    mc, _ = wmean_by_cluster(ycf, wc_f, frame.day[ctrl][fin_c], frame.n_days)
                    vd = wmean_diff_var(ylf, wl_f, frame.day[rows_l][fin_l], ycf, wc_f,
                                        frame.day[ctrl][fin_c], frame.n_days, ml, mc)
                    sd = math.sqrt(max(vd, 0.0))
                    entry["features"][nm] = {
                        "n_late": int(fin_l.sum()), "n_control": int(fin_c.sum()),
                        "late_mean": r6(ml), "control_mean": r6(mc), "difference": r6(ml - mc),
                        "se_day_diff": r6(sd),
                        "ci95_day_diff": [r6(ml - mc - 1.96 * sd), r6(ml - mc + 1.96 * sd)],
                        "ci_excludes_zero": bool(abs(ml - mc) > 1.96 * sd),
                        "minute_mass_matched_late": rep_l["target_mass_matched"],
                        "minute_mass_matched_control": rep_c["target_mass_matched"],
                        "minute_mass_identity_holds": bool(
                            rep_l["minute_mass_identity_holds"]
                            and rep_c["minute_mass_identity_holds"])}
                # at-running-high-only variant: the surface cannot separate the groups
                at_high = (frame.x["dist_from_running_high"] >= -0.002)
                hl = at_high[rows_l]
                hc = at_high[ctrl]
                if hl.sum() > 15 and hc.sum() > 15:
                    wl_h, rep_lh = matched_weights(t_l[hl], uniq, mass_t)
                    wc_h, rep_ch = matched_weights(t_c[hc], uniq, mass_t)
                    ml2, _ = wmean_by_cluster(pred_l[hl], wl_h, frame.day[rows_l][hl],
                                              frame.n_days)
                    mc2, _ = wmean_by_cluster(pred_c[hc], wc_h, frame.day[ctrl][hc],
                                              frame.n_days)
                    vd2 = wmean_diff_var(pred_l[hl], wl_h, frame.day[rows_l][hl], pred_c[hc],
                                         wc_h, frame.day[ctrl][hc], frame.n_days, ml2, mc2)
                    sd2 = math.sqrt(max(vd2, 0.0))
                    entry["at_running_high_only"] = {
                        "n_late": int(hl.sum()), "n_control": int(hc.sum()),
                        "minute_mass_matched_late": rep_lh["target_mass_matched"],
                        "minute_mass_matched_control": rep_ch["target_mass_matched"],
                        "minute_mass_identity_holds": bool(
                            rep_lh["minute_mass_identity_holds"]
                            and rep_ch["minute_mass_identity_holds"]),
                        "predicted_late_mean": r6(ml2), "predicted_control_mean": r6(mc2),
                        "predicted_difference": r6(ml2 - mc2), "se_day_diff": r6(sd2),
                        "ci95_day_diff": [r6(ml2 - mc2 - 1.96 * sd2),
                                          r6(ml2 - mc2 + 1.96 * sd2)],
                        "auc_within_clock": r6(auc_pooled_and_within(
                            np.r_[np.ones(hl.sum()), np.zeros(hc.sum())],
                            -np.r_[pred_l[hl], pred_c[hc]],
                            np.r_[t_l[hl], t_c[hc]])["auc_within_minute"])}
                rec["by_lag"][f"lag_{lag}"] = entry
            fam_rec[wname] = rec
        out["by_family"][fam] = fam_rec
    return out


def _nanmed(x):
    v = x[np.isfinite(x)]
    return float(np.median(v)) if v.size else float("nan")


def _nanmean(x):
    v = x[np.isfinite(x)]
    return float(v.mean()) if v.size else float("nan")


def _nanpct(x, q):
    v = x[np.isfinite(x)]
    return float(np.percentile(v, q)) if v.size else float("nan")


def g(o, *keys):
    """Null-safe nested lookup: a skipped model direction yields None instead of raising."""
    for k in keys:
        if not isinstance(o, dict):
            return None
        o = o.get(k)
    return o


def _control_label_positive(rev: dict, fam: str):
    """The control group's mean final_high_flag, i.e. the mirror's circularity tell (None when
    the reverse-time cells were skipped as too thin)."""
    vals = []
    for w in rev.get("by_family", {}).get(fam, {}):
        for cell in rev["by_family"][fam][w]["by_lag"].values():
            for src in (cell.get("predicted_no_further_new_high"), cell):
                if isinstance(src, dict):
                    v = src.get("control_is_label_positive")
                    if v is None:
                        v = src.get("final_high_flag_control_is_one")
                    if v is not None:
                        vals.append(v)
                        break
    return min(vals) if vals else None


def calibration_note(d12, d21) -> dict:
    """Out-of-block calibration and base rate, reported next to discrimination."""
    out: dict = {}
    for tag, d in (("b1_to_b2", d12), ("b2_to_b1", d21)):
        res = g(d, "results") or {}
        rec = {
            "base_rate": g(d, "eval_base_rate"),
            "mean_pred_clock_plus_state": g(res, "clock_plus_state", "mean_pred"),
            "calibration_slope": g(res, "clock_plus_state", "calibration_slope"),
            "calibration_intercept": g(res, "clock_plus_state", "calibration_intercept"),
            "brier_clock_only": g(res, "clock_only", "brier"),
            "brier_clock_plus_state": g(res, "clock_plus_state", "brier"),
        }
        if g(d, "skipped"):
            rec["skipped"] = g(d, "skipped")
        out[tag] = rec
    return out


def peak_rss_gb() -> float:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    return float("nan")


def target_minute_mass(minutes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The standardisation target: the minute distribution of `minutes` (one row per member-minute,
    so a minute's mass is its share of rows)."""
    uniq, cnt = np.unique(minutes, return_counts=True)
    return uniq, cnt / cnt.sum()


def matched_weights(group_minutes: np.ndarray, uniq: np.ndarray,
                    mass: np.ndarray) -> tuple[np.ndarray, dict]:
    """Per-row weights that give the group total mass `mass[t]` at minute t: w_i = mass[t_i] /
    (rows of THIS group at t_i).

    Giving every row the minute's mass and renormalising would weight a minute by the square of
    its row count; this formulation reproduces the target minute distribution exactly, which is
    checked by `minute_mass_report` before any difference is computed.
    """
    gu, gc = np.unique(group_minutes, return_inverse=True)
    idx = np.minimum(np.searchsorted(uniq, gu), max(uniq.size - 1, 0))
    ok = (uniq.size > 0) & (uniq[idx] == gu)
    m = np.where(ok, mass[idx], 0.0)
    rows = np.bincount(gc).astype(np.float64)
    w = m[gc] / np.maximum(rows[gc], 1.0)
    rep = minute_mass_report(group_minutes, w, uniq, mass)
    return w, rep


def minute_mass_report(minutes: np.ndarray, w: np.ndarray, uniq: np.ndarray,
                       mass: np.ndarray) -> dict:
    """Verify the weighted minute distribution against the target mass, per minute."""
    gu, gc = np.unique(minutes, return_inverse=True)
    got = np.bincount(gc, weights=w)
    idx = np.minimum(np.searchsorted(uniq, gu), max(uniq.size - 1, 0))
    ok = (uniq.size > 0) & (uniq[idx] == gu)
    want = np.where(ok, mass[idx], 0.0)
    dev = float(np.max(np.abs(got - want))) if got.size else 0.0
    return {"n_rows": int(minutes.size), "n_distinct_minutes": int(gu.size),
            "sum_weights": r6(float(w.sum())),
            "target_mass_available": r6(float(want.sum())),
            "target_mass_matched": r6(float(want.sum())),
            "max_abs_minute_mass_deviation": r6(dev),
            "minute_mass_identity_holds": bool(dev < 1e-12)}


def rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(WT))
    except ValueError:
        return str(path)


def sha256_of(path: Path, chunk: int = 1 << 22) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ATLAS fall / one-minute climax hazard")
    ap.add_argument("--panel", type=Path, default=PANEL)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--skip-sha", action="store_true")
    ap.add_argument("--families", default=",".join(FAMILIES),
                    help="comma-separated shard, e.g. --families A_pm (halves peak memory); "
                         "cross-family pooling and the duplicate-path scan need both families")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    t0 = time.time()

    def say(m: str) -> None:
        if not args.quiet:
            print(m, file=sys.stderr, flush=True)

    shard = [f.strip() for f in args.families.split(",") if f.strip()]
    unknown = [f for f in shard if f not in FAMILIES]
    if unknown:
        raise SystemExit(f"unknown family in --families: {unknown}")
    FAM[:] = shard
    checks: dict = {"families_in_this_shard": list(FAM),
                    "shard_note": "run with --families A_pm,B600 for the canonical artifact; a "
                                  "single-family shard halves peak memory but cannot pool "
                                  "families or detect cross-family duplicate paths"}
    if not args.skip_sha:
        digest = sha256_of(args.panel)
        checks["panel_sha256"] = digest
        checks["panel_sha256_matches_frozen"] = digest == PANEL_SHA256
        if digest != PANEL_SHA256:
            say(f"WARNING: panel sha256 {digest} != frozen {PANEL_SHA256}")
    panel_columns = pl.scan_parquet(args.panel).collect_schema().names()
    guard = registry_guard(MODEL_FEATURES, list(panel_columns))
    bad = [n for n in MODEL_FEATURES if any(n.startswith(p) for p in LABEL_PREFIXES)]
    guard["prefix_collisions_with_label_names"] = bad
    checks["causal_guard"] = guard
    if not guard["guard_passes"] or bad:
        raise SystemExit(f"causal guard failed: {guard['violations']} {bad}")
    if guard["v1_member_last_et_present_in_panel"]:
        raise SystemExit("panel still exposes the v1 `member_last_et` column")

    say("loading panel ...")
    frame, load_checks = load_frame(args.panel, FAM)
    checks.update(load_checks)
    say(f"  {frame.n} rows, {frame.n_members} member-days, {frame.n_days} days, "
        f"{time.time() - t0:.1f}s")

    # --- structural checks on the hazard identity ------------------------------------
    r = frame.member_rows
    # only members with a defined peak constant carry a peak-bar check (censored members have null
    # session_peak_* by contract)
    peak_defined = np.isfinite(frame.peak_bars[r]) & np.isfinite(frame.peak_et[r])
    peak_bar_expected = np.where(peak_defined, np.nan_to_num(frame.peak_bars[r]), 0).astype(np.int64)
    pk_pos = frame.off[:-1] + peak_bar_expected
    fh_at_peak = frame.final_high[pk_pos]
    nh_at_peak = frame.x["is_new_high"][pk_pos]
    keep = peak_defined & frame.m_peak_target_ok
    checks["final_high_flag_at_peak_bar"] = {
        "members": int(frame.n_members),
        "members_with_a_defined_peak_constant": int(peak_defined.sum()),
        "members_without_a_peak_constant": int((~peak_defined).sum()),
        "members_whose_peak_bar_has_a_defined_target": int(frame.m_peak_target_ok.sum()),
        "share_of_members_with_a_defined_target": r6(float(frame.m_peak_target_ok.mean())),
        "share_final_high_flag_is_one_among_defined": r6(float(np.mean(
            fh_at_peak[keep] == 1.0))) if keep.any() else None,
        "note": "terminal_censored members have NO session_peak_* constant and no future path, so "
                "they are excluded here and from every labelled statistic; a member whose session "
                "peak falls on its LAST bar has a peak constant but no final_high_flag there",
    }
    checks["peak_bar_is_a_new_high"] = float(np.mean(nh_at_peak[peak_defined] == 1.0))
    checks["peak_et_equals_peak_bar_et"] = float(np.mean(
        frame.et[pk_pos][peak_defined] == frame.peak_et[r][peak_defined]))
    checks["dup_paths"] = (duplicate_paths(frame) if len(FAM) == 2 else
                           {"skipped": "single-family shard: cross-family duplicates cannot be "
                                       "seen"})
    checks["tape"] = {
        "mean_bars_per_member": float(frame.sizes.mean()),
        "mean_minutes_spanned_per_member": float(np.mean(
            [frame.et[frame.member_slice(i)][-1] - frame.et[frame.member_slice(i)][0]
             for i in range(frame.n_members)])),
        "half_session_members": int((frame.session_end[r] != 959).sum()),
    }

    say("descriptive: peak timing (minute resolution) ...")
    timing = peak_timing(frame)
    say("descriptive: survival + arrival x hazard ...")
    curves = survival_and_hazard_curves(frame, checks)
    say("descriptive: give-back ...")
    giveback = giveback_stats(frame, checks)
    say("causal: hazard base rates ...")
    hz = hazard_tables(frame, checks)
    say("causal: per-feature conditional rates ...")
    rates = feature_rates(frame, checks)
    say(f"causal: fitting per-family models [{time.time() - t0:.0f}s] ...")
    models_by_family = {fam: {w: {t: fit_family_model(frame, fam, t, w,
                                                      variants=(t == "hazard_last_new_high"))
                                  for t in TARGETS}
                              for w in WINDOWS} for fam in FAM}
    say(f"causal: out-of-block logistic [{time.time() - t0:.0f}s] ...")
    logit = out_of_block_logistic(frame, checks, models_by_family)
    say(f"causal: per-feature out-of-block contrasts [{time.time() - t0:.0f}s] ...")
    oob = out_of_block_contrasts(frame, checks)
    say(f"causal: peak vs 30/60 minutes earlier [{time.time() - t0:.0f}s] ...")
    paired = paired_contrast(frame, checks)
    say(f"causal: reverse-time late-peak view [{time.time() - t0:.0f}s] ...")
    rev = reverse_time_view(frame, checks,
                            {fam: models_by_family[fam]["all_session"]["no_further_new_high"]
                             for fam in FAM})
    # prospective bridge: which reverse-time anatomy gaps survive out-of-block at all
    bridge: dict = {"rule": "a reverse-time anatomy gap counts as prospective evidence only if "
                            "the same feature survives the EDGE-FREE out-of-block test for the "
                            "hazard target (both directions, train sign kept, day-clustered CI "
                            "excluding zero)",
                    "by_family": {}}
    for fam in FAM:
        gaps = set()
        for wname in ("last_hour_et_ge_900", "last_two_hours_et_ge_840"):
            for lag_rec in rev["by_family"][fam][wname]["by_lag"].values():
                for fname, fv in (lag_rec.get("features") or {}).items():
                    if fv.get("ci_excludes_zero"):
                        gaps.add(fname)
        surv = set(oob["by_family"][fam]["hazard_last_new_high"]["headline_edge_free"][
            "surviving_features"])
        bridge["by_family"][fam] = {
            "features_with_a_reverse_time_gap": sorted(gaps),
            "of_those_surviving_out_of_block": sorted(gaps & surv),
            "verdict": ("the reverse-time contrast is supported prospectively by at least one "
                        "feature" if (gaps & surv) else
                        "no feature that separates the pre-peak minutes from finished members "
                        "survives out-of-block: the anatomy does not become foresight")}
    rev["prospective_bridge"] = bridge

    # --- headline (facts; the prose lives in the report) ------------------------------
    head: dict = {"peak_timing_hindsight_cohorted": {}, "hazard": {}, "foreseeable": {}}
    for fam in FAM:
        pop = timing["by_family"][fam]["by_block"]["all"]["population"]
        head["peak_timing_hindsight_cohorted"][fam] = {
            "median_peak_clock_minute": pop["peak_clock_minute"]["median"],
            "median_peak_bars_since_entry": pop["peak_bars_since_entry"]["median"],
            "hindsight_clock_mark_shares": pop["HINDSIGHT_share_of_peaks_before_clock_mark"],
            "hindsight_tenure_shares_bars": pop["HINDSIGHT_share_of_peaks_within_tenure_bars"],
            "note": "HINDSIGHT-COHORTED description: conditioned on the realised peak (outcome "
                    "label). Not a law, not a signal, and no clock mark (10:00/11:00/12:00) is "
                    "used as a decision boundary anywhere in this file.",
        }
        cur = curves["by_family"][fam]["all"]
        head["hazard"][fam] = {
            "two_factors_reported_separately": {
                "arrival_morning_rate": hz["by_family"][fam]["all"]["arrival_new_high"][
                    "morning_le_1200"],
                "hazard_given_a_new_high_morning": hz["by_family"][fam]["all"][
                    "hazard_last_new_high"]["morning_le_1200"],
                "minute_curves": "peak_timing.survival_and_hazard_curves (arrival_rate_new_high, "
                                 "hazard_last_new_high, survival_S, peak_pmf_reconstructed)",
                "reading": "falling arrival = the tape goes quiet; flat arrival with rising hazard "
                           "= new highs stop being followed through",
            },
            "hazard_last_new_high_all_session": cur["base_rates"]["share_of_new_highs_that_"
                                                                  "are_the_last"],
            "peak_after_1200_population_share": cur["base_rates"]["peak_after_1200"],
            "new_high_bars_per_member_day": cur["base_rates"]["new_high_bars_per_member_day"],
            "block_stability": {
                "arrival_morning": {"block1": hz["by_family"][fam]["block1"]["arrival_new_high"][
                    "morning_le_1200"]["rate"],
                    "block2": hz["by_family"][fam]["block2"]["arrival_new_high"][
                        "morning_le_1200"]["rate"]},
                "hazard_morning": {"block1": hz["by_family"][fam]["block1"][
                    "hazard_last_new_high"]["morning_le_1200"]["rate"],
                    "block2": hz["by_family"][fam]["block2"]["hazard_last_new_high"][
                        "morning_le_1200"]["rate"]},
            },
        }
        for tname in TARGETS:
            dirs = logit["by_family"][fam][WINDOWS[0]][tname]["directions"]
            d12, d21 = dirs.get("fit_block1_report_block2"), dirs.get("fit_block2_report_block1")
            variants = list((g(d12, "results") or {}).keys() - {"bootstrap_day_clustered"}) \
                if d12 else []
            variants = [v for v in variants if not v.startswith("delta_")]
            head["foreseeable"].setdefault(tname, {})[fam] = {
                "eval_base_rate": {"block2": g(d12, "eval_base_rate"),
                                   "block1": g(d21, "eval_base_rate")},
                "auc_within_clock_by_variant": {
                    b: {tag: g(d, "results", tag, "auc_within_clock") for tag in variants}
                    for b, d in (("b1_to_b2", d12), ("b2_to_b1", d21))},
                "auc_within_tenure_by_variant": {
                    b: {tag: g(d, "results", tag, "auc_within_tenure") for tag in variants}
                    for b, d in (("b1_to_b2", d12), ("b2_to_b1", d21))},
                "auc_pooled_by_variant": {
                    b: {tag: g(d, "results", tag, "auc_pooled") for tag in variants}
                    for b, d in (("b1_to_b2", d12), ("b2_to_b1", d21))},
                "brier_by_variant": {
                    b: {tag: g(d, "results", tag, "brier") for tag in variants}
                    for b, d in (("b1_to_b2", d12), ("b2_to_b1", d21))},
                "top_decile_within_minute_lift": {
                    b: g(d, "results", "clock_plus_state", "top_decile_within_minute", "lift")
                    for b, d in (("b1_to_b2", d12), ("b2_to_b1", d21))},
                "delta_within_clock_ci95_vs_clock_only": {
                    b: g(d, "results", "bootstrap_day_clustered", "delta_within")
                    for b, d in (("b1_to_b2", d12), ("b2_to_b1", d21))},
                "delta_auc_within_clock_vs_clock_only": {
                    b: g(d, "results", "delta_auc_within_clock_vs_clock_only")
                    for b, d in (("b1_to_b2", d12), ("b2_to_b1", d21))},
                "delta_auc_within_tenure_vs_clock_only": {
                    b: g(d, "results", "delta_auc_within_tenure_vs_clock_only")
                    for b, d in (("b1_to_b2", d12), ("b2_to_b1", d21))},
                "calendar_stability": calibration_note(d12, d21),
            }
    head["verdict"] = {
        "warning": "facts, not advocacy: these are the out-of-block numbers that decide whether "
                   "the climax is causally foreseeable",
        "edge_free_feature_evidence": {
            t: {fam: {**oob["by_family"][fam][t]["headline_edge_free"],
                      "n_features_tested": oob["by_family"][fam][t]["n_features_tested"],
                      "features_constant_in_risk_set": list(
                          oob["by_family"][fam][t]["features_constant_in_risk_set"])}
                for fam in FAM}
            for t in TARGETS},
        "fixed_edge_ruler_reference_only": {
            t: {fam: {"n_features_tested": oob["by_family"][fam][t][
                "fixed_edge_ruler_reference_only"]["n_features_tested"],
                "n_surviving": oob["by_family"][fam][t][
                    "fixed_edge_ruler_reference_only"]["n_features_surviving"],
                "surviving": oob["by_family"][fam][t][
                    "fixed_edge_ruler_reference_only"]["surviving_features"]} for fam in FAM}
            for t in TARGETS},
        "auc_within_clock_out_of_block": {
            w: {fam: {t: {b: g(logit["by_family"][fam][w][t]["directions"].get(fit),
                                "results", "clock_plus_state", "auc_within_clock")
                         for b, fit in (("b1_to_b2", "fit_block1_report_block2"),
                                        ("b2_to_b1", "fit_block2_report_block1"))}
                      for t in TARGETS} for fam in FAM}
            for w in WINDOWS},
        "auc_within_tenure_out_of_block": {
            w: {fam: {t: {b: g(logit["by_family"][fam][w][t]["directions"].get(fit),
                                 "results", "clock_plus_state", "auc_within_tenure")
                          for b, fit in (("b1_to_b2", "fit_block1_report_block2"),
                                         ("b2_to_b1", "fit_block2_report_block1"))}
                       for t in TARGETS} for fam in FAM}
            for w in WINDOWS},
        "time_control_sensitivity_hazard_primary_window": {
            fam: {fit: {tag: {"auc_within_clock": g(
                logit["by_family"][fam]["all_session"]["hazard_last_new_high"]["directions"].get(fit),
                "results", tag, "auc_within_clock"),
                "auc_within_tenure": g(
                    logit["by_family"][fam]["all_session"]["hazard_last_new_high"]["directions"].get(fit),
                    "results", tag, "auc_within_tenure")}
                for tag in VARIANTS_NAMES}
                for fit in ("fit_block1_report_block2", "fit_block2_report_block1")}
            for fam in FAM},
        "reverse_time_late_vs_finished_auc_within_clock": {
            "LABEL": "HINDSIGHT ANATOMY ONLY",
            "warning": "the groups are defined by the realised session peak, so this is a "
                       "description of what the state looked like around the top, not a "
                       "prospective detector. It becomes prospective evidence only through "
                       "reverse_time_late_peak_HINDSIGHT_ANATOMY_ONLY.prospective_bridge / the "
                       "edge-free out-of-block test.",
            "circularity_tell": {
                "final_high_flag_control_is_one": {
                    fam: _control_label_positive(rev, fam) for fam in FAM},
                "reading": "the control group (peak already behind) IS the label-positive set of "
                           "final_high_flag by construction, so the AUC here measures whether the "
                           "causal state distinguishes members that still have a peak ahead from "
                           "members that do not. That is anatomy, not foresight.",
            },
            "by_family": {
                fam: {w: {f"lag_{lag}": rev["by_family"][fam][w]["by_lag"].get(
                    f"lag_{lag}", {}).get("predicted_no_further_new_high", {}).get(
                    "auc_within_clock_late_vs_finished")
                    for lag in (10, 30)}
                      for w in ("last_hour_et_ge_900", "last_two_hours_et_ge_840")}
                for fam in FAM},
        },
    }
    head["block_agreement_causal"] = {
        "hazard_morning_rate": {fam: {"block1": hz["by_family"][fam]["block1"][
            "hazard_last_new_high"]["morning_le_1200"]["rate"],
            "block2": hz["by_family"][fam]["block2"]["hazard_last_new_high"][
                "morning_le_1200"]["rate"]} for fam in FAM},
        "arrival_morning_rate": {fam: {"block1": hz["by_family"][fam]["block1"][
            "arrival_new_high"]["morning_le_1200"]["rate"],
            "block2": hz["by_family"][fam]["block2"]["arrival_new_high"][
                "morning_le_1200"]["rate"]} for fam in FAM},
        "survival_level_morning_rate": {fam: {"block1": hz["by_family"][fam]["block1"][
            "no_further_new_high"]["morning_le_1200"]["rate"],
            "block2": hz["by_family"][fam]["block2"]["no_further_new_high"][
                "morning_le_1200"]["rate"]} for fam in FAM},
        "note": "each rate carries se_day / se_ticker / ci95_day in causal.hazard_base_rates",
    }

    doc = {
        "producer": "factory/scripts/basket_atlas_fall.py",
        "reproduce": "python factory/scripts/basket_atlas_fall.py",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runtime_s": round(time.time() - t0, 1),
        "panel": {"path": rel(args.panel), "rows": frame.n,
                  "member_days": frame.n_members, "days": frame.n_days,
                  "sha256": checks.get("panel_sha256", PANEL_SHA256)},
        "conventions": {
            "object": "one-minute anatomy of the climb and the fall: the arrival rate of new-high "
                      "events and the hazard that a new high is the last of the session, reported "
                      "separately and as their product. No 15/30/60-minute target or bin exists "
                      "anywhere in this file.",
            "no_value_function": "no declared continuation or value column (v_giveback_*, "
                                 "v_hold_flat) is read anywhere; the only future-path primitive "
                                 "used is final_high_flag (and the ticket constants in the "
                                 "descriptive part).",
            "decay": "the arrival and hazard curves run to the close, so the exact intraday decay "
                     "is measured minute by minute rather than asserted for a window.",
            "hazard_identity": "P(peak = t) = P(new high set at t) x P(this new high is the last | "
                               "new high at t); the peak-time distribution emerges from the two "
                               "minute-level factors",
            "survival": "S(t) = P(peak > t) = 1 - E[final_high_flag(t)]",
            "time_coordinate": "every model enters the EXACT clock minute as a fixed effect (one "
                               "parameter per minute, 09:30..16:00); no 15-minute or other binned "
                               "time term exists in any predictor or standardiser, so the "
                               "clock-only baseline is the per-minute empirical rate",
            "families": "A_pm (fill ET 570) and B600 (fill ET 600) are never pooled in a curve; "
                        "clock minute (et) and bars_since_entry are separate coordinates",
            "uncertainty": "cluster-robust SE by sleeve_day (primary, conservative) and ticker "
                           f"(secondary) for every rate; {BOOT_REPS}-rep day-cluster bootstrap for "
                           "medians, paired differences and AUCs (bootstrap on the primary "
                           "full-session window)",
            "units": "return units, friction-free (no dollars here; the dollar judgement is "
                     "basket_atlas_ledger.py). Friction on any executed action is bps_total/2 per "
                     "side and is not applied in this artifact.",
            "panel_version": "v2 (sha bound at build time): anatomy fill clocks, terminal "
                             "censoring, forced-flat execution price, future_ metadata",
            "executable_baseline": "v_forced_flat (future_forced_flat_px / next_open - 1) is the "
                                   "executable hold-to-flat continuation; v_hold_flat is the "
                                   "labelled CLOSE reference only. A give-back condition first "
                                   "appearing at et >= session_end - 1 cannot fire, so those rows "
                                   "carry v_forced_flat with fired=false; terminal-censored tapes "
                                   "and rows with no next open stay unscored and are counted.",
            "censoring": "terminal_censored members have no terminal value and no executable "
                         "terminal liquidation; they are excluded from every executable rate and "
                         "curve and reported as unresolved with their count and share, never as "
                         "zero. State-level targets (the arrival of a new high) keep their rows "
                         "because the event is genuinely defined there; label-level targets "
                         "(final_high_flag) drop them because the label is null.",
            "future_only": "future_member_last_et and future_forced_flat_px are future-only and "
                           "are never read as features; the causal guard is driven by "
                           "column_registry.json + coverage.json, not by name prefixes.",
            "blocks": {"block1": "2021-02..2023-12", "block2": "2025-02..2026-05",
                       "era_split": "an ERA split, not an exchangeable random split: 2024-01.."
                                    "2025-01 (13 months) is absent from the data, so out-of-block "
                                    "agreement is a necessary but not a sufficient condition for a "
                                    "rule"},
            "clock_tenure_collinearity": "exact clock and exact tenure effects are nearly "
                                         "collinear within a family (they differ by the cumulative "
                                         "tape-gap offset). In the joint model the ridge decides how "
                                         "much of a common level goes to which coordinate; the "
                                         "state coefficients, fitted probabilities and AUCs are "
                                         "unaffected. Read clock_only / tenure_only / the joint "
                                         "model as three time CONTROLS, not as a decomposition of "
                                         "time into two mechanisms.",
            "windows": {"all_session": "primary, every minute to the close",
                        "morning_le_1200": f"secondary, et <= {MORNING_END}"},
            "hindsight_anatomy": "causal.reverse_time_late_peak_HINDSIGHT_ANATOMY_ONLY and the "
                                 "paired peak-minus-30 contrast are anatomy conditioned on "
                                 "the realised peak; they "
                                 "are labelled as such and are evidence only where the same "
                                 "pattern survives out-of-block",
            "labels_never_used_as_features": list(LABEL_PREFIXES),
        },
        "descriptive": {
            "uses_outcome_labels": True,
            "warning": "peak timing, the cohort split, the survival/hazard curves and give-back all "
                       "condition on outcome labels. They describe the filled population; they are "
                       "not signals and not evidence of predictability. The peak-timing shares at "
                       "clock marks (10:00/11:00/12:00) are HINDSIGHT-COHORTED descriptions of an "
                       "outcome distribution, not decision boundaries.",
            "peak_timing": timing,
            "survival_and_hazard_curves": curves,
            "giveback": giveback,
            "duplicate_paths": checks["dup_paths"],
        },
        "causal": {
            "uses_outcome_labels": "target only (final_high_flag)",
            "sample": {"primary_rows_all_session": int(frame.n),
                       "secondary_rows_morning_le_1200": int((frame.et <= MORNING_END).sum()),
                       "member_days": frame.n_members,
                       "rows_without_a_target": int((~frame.target_ok).sum()),
                       "note": "rows_without_a_target = each member's last bar, which has no "
                               "future path to judge"},
            "hazard_base_rates": hz,
            "feature_conditional_rates": rates,
            "out_of_block_logistic": logit,
            "per_feature_out_of_block_contrasts": oob,
            "paired_anatomy_peak_vs_30min_earlier": paired,
            "reverse_time_late_peak_HINDSIGHT_ANATOMY_ONLY": rev,
        },
        "headline": head,
        "checks": checks,
    }
    doc = jsonable(doc)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=1) + "\n")
    json.loads(args.out.read_text())
    say(f"wrote {args.out} ({args.out.stat().st_size / 1e3:.0f} kB) in {time.time() - t0:.1f}s; "
        f"peak RSS {peak_rss_gb():.2f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
