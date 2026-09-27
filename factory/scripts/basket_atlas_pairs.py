#!/usr/bin/env python3
"""ATLAS matched pairs — what separates divergent-up from divergent-down members.

Reads the frozen ATLAS panel v2 (`factory/artifacts/basket/phase2/ATLAS/panel.parquet`, sha
2a021eda…, contract in `SCHEMA.md`, column families in `column_registry.json`) and answers the
next-branch question: at the same minute, in the same family, two members look alike in coarse
causal state — the oracle continuation value of one lands in the top decile of that minute, the
other in the bottom decile. Which state discriminators separate them, by how much, in which
direction, in both development blocks, and was the divergent-down side's exit right (did the price
come back to the exit price, how soon, and before a further decline)?

Panel v2 conventions honoured here: features come only from the registry's causal state families
(prefix rules are insufficient in v2); terminal-censored members are excluded from strata, labels,
cells, tiers and pairs and are counted per cell (never null-as-zero); `v_forced_flat` is the
executable hold-to-flat baseline while `v_hold_flat` is close-reference anatomy and is not read;
give-back conditions at `et >= session_end - 1` are non-firing by the engine's forced-flat clock
precedence; `future_member_last_et` and the other future-only columns are never features.

Labels (oracle continuation value; strictly future columns; quantile-defined within the minute —
no session-close outcome, no absolute return threshold):

    oc(t) = max over later bars u > t of bar_close(u) / next_open(t) - 1      [primary exit price]
    per (et, family) stratum with n >= min_stratum rows:
        divergent_up   = oc >= q90 of that minute's distribution
        divergent_down = oc <= q10 of that minute's distribution
    robustness: the same deciles on oc_open (exit at the next open, the panel's execution
    convention) and oc_high (exit at a bar high); agreement with the primary is reported.

Matching (never across families, never across minutes):
    tier A (primary)  cell = (et, family, ret_from_fill bucket, bars_below_entry_episode bucket,
                      dist_from_running_high bucket, reclaim_count bucket, bar_index bucket);
                      usable with >= min_per_label rows of EACH decile and >= min_cell_rows rows;
                      day units = (cell, day) with both deciles present, pairs within the cell.
    tier B            units = (sleeve_day, et, family): the sharpest control — same day, same
                      minute, same family, both deciles present; pairs within the unit.
    tier C            strata = (et, family): the population view; pairs aligned across days.

Uncertainty: every CI is a Poisson bootstrap clustered on the DAY (the conservative unit); minute
rows are not independent; ticket-clustered CIs are reported as the secondary unit for pair hit
rates. Magnitudes are always reported with medians and tail concentration.

Divergence timing: whether the matched moments sit within 5 bars of a print gap (microstructure /
halts) or are diffuse, against the base rate of that state in the same minute-family stratum.

Recovery after divergent_down (exit price = next_open(t)): bars until a later bar's high touches the
exit price again, the deepest low before that touch (the cost of waiting), and whether a new low
below the pre-exit session low came first — timely-and-executable versus merely eventual.

Usage:
    .venv/bin/python factory/scripts/basket_atlas_pairs.py                 # writes the artifact
    .venv/bin/python factory/scripts/basket_atlas_pairs.py --self-test
    .venv/bin/python factory/scripts/basket_atlas_pairs.py --verify
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PANEL = ROOT / "factory/artifacts/basket/phase2/ATLAS/panel.parquet"
DEFAULT_OUT = ROOT / "factory/artifacts/basket/phase2/ATLAS/matched_pairs.json"

EXPECTED_PANEL_SHA256 = "2a021eda878cdbe53e41d4cc88e2d8ba3b14d9a3a819723d9f679c8aca7c3488"
EXPECTED_SCHEMA_VERSION = 2
EXPECTED_PANEL_ROWS = 1_900_432
EXPECTED_PANEL_MEMBERS = 6_160

# Causal feature families per column_registry.json (v2). A feature may only come from these; the
# registry - not a name prefix - decides, because v2 outcome/censor/future columns do not all share
# the v1 prefixes (`level_ret`, `path_complete_to_session_end`, `future_member_last_et`, ...).
CAUSAL_FEATURE_FAMILIES = ("state_path", "state_episode", "state_dynamics", "state_attention",
                           "state_cross")
# Matching dimensions may also be identity/tenure keys (et, family, bar_index): a key is not a
# predictor, it is the coordinate of the match.
MATCHING_KEY_FAMILIES = ("key",)
NON_FEATURE_FAMILIES = ("outcome_level", "outcome_path", "outcome_continuation", "censor",
                        "future_meta", "ticket_constant", "tape", "key")

DISCRIMINATORS = (
    "volume_vs_own_median",
    "volume_accel",
    "new_high_count_5",
    "new_high_count_15",
    "bars_since_new_high",
    "ret_percentile_candidates",
    "peer_ret_median",
    "peer_new_high_5",
    "bars_since_gap",
    "gap_count_so_far",
    "up_close_streak",
    "accel_1_5",
    "bar_range_pct",
    "mfe_surrendered",
)

# Pre-registered expected sign of (divergent_up - divergent_down); None = no thesis prior.
PRIORS = {
    "volume_vs_own_median": ("+", "continuing attention: up trades above its own median volume"),
    "volume_accel": ("+", "attention still expanding at the matched moment"),
    "new_high_count_5": ("+", "fresh highs in the last 5 minutes: the tape is still making progress"),
    "new_high_count_15": ("+", "same over a 15-minute horizon"),
    "bars_since_new_high": ("-", "up set its high more recently (smaller bars_since_new_high)"),
    "ret_percentile_candidates": ("+", "up is the stronger name in the day's cross-section"),
    "peer_ret_median": ("+", "exploratory: spillover/attention cluster rather than rotation"),
    "peer_new_high_5": ("+", "exploratory: peers breaking too = sector-wide move"),
    "bars_since_gap": (None, "no thesis prior: microstructure proximity versus staleness"),
    "gap_count_so_far": (None, "no thesis prior: halt/sparsity incidence"),
    "up_close_streak": ("+", "momentum of consecutive up closes"),
    "accel_1_5": ("+", "short-horizon acceleration"),
    "bar_range_pct": (None, "no thesis prior: volatility can feed continuation or exhaustion"),
    "mfe_surrendered": ("-", "up has surrendered less of its MFE; down already gave the spike back"),
}

def edge_labels(edges) -> tuple:
    labs = [f"<={edges[0]:g}"]
    labs += [f"({a:g},{b:g}]" for a, b in zip(edges, edges[1:])]
    labs.append(f">{edges[-1]:g}")
    return tuple(labs)


# Primary coarse-state edges, and a documented alternate set used as the edge-perturbation check.
# A survivor whose out-of-block transfer does not hold under the alternate edges is edge-sensitive.
PRIMARY_EDGES = {
    "ret_from_fill": (-0.15, -0.05, 0.0, 0.05, 0.15, 0.50),
    "bars_below_entry_episode": (0, 5, 15, 30, 60, 120),
    "dist_from_running_high": (-0.40, -0.20, -0.10, -0.03, -0.005, 0.0),
    "reclaim_count": (0, 1, 2, 5, 10),
    "bar_index": (0, 1, 2, 4, 8, 16, 32, 64, 128),
}
ALTERNATE_EDGES = {
    "ret_from_fill": (-0.20, -0.08, 0.0, 0.08, 0.20, 0.60),
    "bars_below_entry_episode": (0, 3, 10, 20, 40, 80, 160),
    "dist_from_running_high": (-0.30, -0.15, -0.07, -0.02, -0.003, 0.0),
    "reclaim_count": (0, 1, 3, 7, 15),
    "bar_index": (0, 1, 3, 6, 12, 24, 48, 96),
}
EDGE_SETS = {"primary": PRIMARY_EDGES, "alternate": ALTERNATE_EDGES}
EDGE_SPECS = {name: {dim: (edges, edge_labels(edges)) for dim, edges in spec.items()}
              for name, spec in EDGE_SETS.items()}
STATE_BUCKETS = EDGE_SPECS["primary"]
GAP_EDGES = (0, 1, 2, 3, 6, 11, 16, 31, 61, 121)
GAP_BUCKETS = (GAP_EDGES, edge_labels(GAP_EDGES))
CLOCK_REPORT_MINUTES = 15

ID_COLUMNS = ("sleeve_day", "ticker", "family", "entry_rank", "et", "block", "session_end",
              "entry_px", "next_open", "bar_high", "bar_low", "bar_close", "running_high",
              "bar_index", "terminal_censored", "path_complete_to_session_end",
              "future_member_last_et")
# Outcome/path columns read only as measures or labels, never as features. `v_forced_flat` is the
# executable baseline; `v_hold_flat` is close-reference anatomy and is deliberately NOT read.
PATH_COLUMNS = ("remaining_run", "cost_of_waiting", "bars_to_next_high", "dd_before_next_high",
                "final_high_flag", "bars_since_gap", "gap_count_so_far", "bars_to_peak",
                "v_forced_flat", "session_close_ret_from_entry")

MEMBER_KEYS = ["sleeve_day", "family", "entry_rank", "ticker"]
CELL_DIMS = ("ret_from_fill", "bars_below_entry_episode", "dist_from_running_high",
             "reclaim_count", "bar_index")


class PanelSchemaError(RuntimeError):
    """The panel does not carry the columns this analysis contract requires."""


class FeatureGuardError(RuntimeError):
    """A configured feature column is not in a causal state family per the column registry."""


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _hm(et: int) -> str:
    return f"{et // 60:02d}:{et % 60:02d}"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def _np(x):
    if x is None:
        return None
    x = float(x)
    return None if (math.isnan(x) or math.isinf(x)) else x


def _sanitize(obj):
    if isinstance(obj, dict):
        return {str(k): _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [_sanitize(v) for v in obj.tolist()]
    if isinstance(obj, (np.floating, np.integer, np.bool_)):
        return _sanitize(obj.item())
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(_sanitize(payload), indent=2, allow_nan=False))
    tmp.replace(path)


def core_hash(payload: dict) -> str:
    core = {k: v for k, v in payload.items()
            if k not in ("generated_utc", "runtime_seconds", "deterministic_core_sha256")}
    blob = json.dumps(_sanitize(core), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def bucket_expr(col: str, edges) -> pl.Expr:
    expr = pl.lit(len(edges), dtype=pl.Int32)
    for i in range(len(edges) - 1, -1, -1):
        expr = pl.when(pl.col(col) <= edges[i]).then(pl.lit(i, dtype=pl.Int32)).otherwise(expr)
    return pl.when(pl.col(col).is_null()).then(pl.lit(-1, dtype=pl.Int32)).otherwise(expr)


def bucket_text(key: str, idx: int) -> str:
    _, texts = STATE_BUCKETS[key]
    return "null" if idx < 0 else (texts[idx] if idx < len(texts) else "beyond")


def _cutname(c) -> str:
    s = f"{c:g}".replace("-", "m").replace(".", "p")
    return s


def _dist_summary(vals: np.ndarray, cuts=None) -> dict:
    v = np.asarray(vals, dtype=np.float64)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return {"n": 0}
    out = {"n": int(v.size), "mean": _np(float(v.mean())), "median": _np(float(np.median(v))),
           "p10": _np(float(np.percentile(v, 10))), "p90": _np(float(np.percentile(v, 90)))}
    for c in (cuts or ()):
        out[f"share_le_{_cutname(c)}"] = _np(float((v <= c).mean()))
    return out


def load_registry(panel_path: Path) -> dict:
    reg_path = Path(panel_path).parent / "column_registry.json"
    if not reg_path.exists():
        raise PanelSchemaError(f"column registry missing: {reg_path}")
    reg = json.loads(reg_path.read_text())
    if int(reg.get("schema_version", 0)) != EXPECTED_SCHEMA_VERSION:
        raise PanelSchemaError(f"registry schema_version {reg.get('schema_version')} != "
                               f"{EXPECTED_SCHEMA_VERSION}")
    return reg


def load_panel_meta(panel_path: Path) -> dict:
    cov_path = Path(panel_path).parent / "coverage.json"
    meta = {"panel": str(panel_path)}
    if cov_path.exists():
        cov = json.loads(cov_path.read_text())
        meta.update({"rows": cov.get("rows"), "members": cov.get("members"),
                     "days": cov.get("days_dev_total"),
                     "schema_version": cov.get("schema_version"),
                     "panel_sha256_declared": cov.get("panel_sha256"),
                     "future_only_columns": cov.get("future_only_columns"),
                     "per_family": cov.get("per_family")})
    return meta


def guard_features(columns, families_map: dict, allowed=CAUSAL_FEATURE_FAMILIES) -> dict:
    """Registry-based causal guard: every feature must map to an allowed family. Prefix rules are
    insufficient in v2 (level_ret, path_complete_to_session_end, future_member_last_et, ...)."""
    violations = []
    for c in columns:
        fam = families_map.get(c)
        if fam is None:
            violations.append(f"{c}: absent from the registry")
        elif fam not in allowed:
            violations.append(f"{c}: family '{fam}' is not in {list(allowed)}")
    return {"features_checked": list(columns), "violations": violations,
            "families": {c: families_map.get(c) for c in columns},
            "allowed_families": list(allowed)}


def required_columns() -> list:
    return sorted(set(ID_COLUMNS) | set(PATH_COLUMNS) | set(CELL_DIMS) | set(DISCRIMINATORS))


# --------------------------------------------------------------------------- #
# frame preparation: oracle continuation value, decile labels, exclusions
# --------------------------------------------------------------------------- #
def prepare(df: pl.DataFrame, cfg: dict) -> pl.DataFrame:
    df = df.sort(MEMBER_KEYS + ["et"])
    df = df.with_columns([
        pl.col("bar_close").reverse().cum_max().reverse().shift(-1).over(MEMBER_KEYS)
          .alias("fwd_close_max"),
        pl.col("next_open").reverse().cum_max().reverse().shift(-1).over(MEMBER_KEYS)
          .alias("fwd_open_max"),
        pl.col("bar_high").reverse().cum_max().reverse().shift(-1).over(MEMBER_KEYS)
          .alias("fwd_high_max"),
    ])
    df = df.with_columns([
        (pl.col("fwd_close_max") / pl.col("next_open") - 1).alias("oc_close"),
        (pl.col("fwd_open_max") / pl.col("next_open") - 1).alias("oc_open"),
        (pl.col("fwd_high_max") / pl.col("next_open") - 1).alias("oc_high"),
    ])
    # Decile strata are computed on complete tapes only: a terminal-censored member has no
    # terminal value and no executable terminal liquidation, so it cannot be scored on the future
    # path and never enters a stratum, a label, a cell comparison or a pair.
    complete = ~pl.col("terminal_censored")
    q = df.filter(complete).group_by(["et", "family"]).agg([
        pl.col("oc_close").quantile(0.9).alias("q90"),
        pl.col("oc_close").quantile(0.1).alias("q10"),
        pl.col("oc_open").quantile(0.9).alias("q90o"),
        pl.col("oc_open").quantile(0.1).alias("q10o"),
        pl.col("oc_high").quantile(0.9).alias("q90h"),
        pl.col("oc_high").quantile(0.1).alias("q10h"),
        pl.len().alias("stratum_rows"),
    ])
    df = df.join(q, on=["et", "family"], how="left")
    big = (pl.col("stratum_rows") >= cfg["min_stratum"]) & complete
    df = df.with_columns([
        (big & (pl.col("oc_close") >= pl.col("q90"))).fill_null(False).alias("up"),
        (big & (pl.col("oc_close") <= pl.col("q10"))).fill_null(False).alias("down"),
        (big & (pl.col("oc_open") >= pl.col("q90o"))).fill_null(False).alias("up_o"),
        (big & (pl.col("oc_open") <= pl.col("q10o"))).fill_null(False).alias("down_o"),
        (big & (pl.col("oc_high") >= pl.col("q90h"))).fill_null(False).alias("up_h"),
        (big & (pl.col("oc_high") <= pl.col("q10h"))).fill_null(False).alias("down_h"),
        pl.col("terminal_censored").alias("censored"),
    ])
    uniq = df.select(["sleeve_day", "ticker", "family"]).unique()
    dup = (uniq.group_by(["sleeve_day", "ticker"])
           .agg(pl.col("family").n_unique().alias("nf"))
           .filter(pl.col("nf") > 1)
           .select(["sleeve_day", "ticker"])
           .with_columns(pl.lit(True).alias("dup_cross_family")))
    df = df.join(dup, on=["sleeve_day", "ticker"], how="left")
    df = df.with_columns(pl.col("dup_cross_family").fill_null(False))
    df = df.with_columns([
        pl.concat_str([pl.col("et").cast(pl.Utf8), pl.col("family")], separator="|")
          .alias("stratum"),
        pl.concat_str(MEMBER_KEYS, separator="|").alias("member"),
        bucket_expr("bars_since_gap", GAP_BUCKETS[0]).alias("b_gap"),
        ((pl.col("et") // CLOCK_REPORT_MINUTES) * CLOCK_REPORT_MINUTES).alias("clk"),
    ])
    df = df.with_columns(((pl.col("up") & ~pl.col("down")).cast(pl.Int8)
                          - (pl.col("down") & ~pl.col("up")).cast(pl.Int8)).alias("side"))
    return df


def add_cells(df: pl.DataFrame, spec_name: str) -> pl.DataFrame:
    """Bucket the coarse state and build the matching cell key under the named edge set. The
    decile labels never depend on the edge set (they are within (et, family) only)."""
    spec = EDGE_SPECS[spec_name]
    for name, (edges, _) in spec.items():
        df = df.with_columns(bucket_expr(name, edges).alias(f"b_{name}"))
    return df.with_columns(
        pl.concat_str([pl.col("et").cast(pl.Utf8), pl.col("family")]
                      + [pl.col(f"b_{n}").cast(pl.Utf8) for n in CELL_DIMS],
                      separator="|").alias("cell"))


def label_counts(df: pl.DataFrame) -> dict:
    def counts(d: pl.DataFrame) -> dict:
        n = d.height
        return {"rows": n,
                "divergent_up": int(d["up"].sum()),
                "divergent_down": int(d["down"].sum()),
                "up_share": _np(float(d["up"].sum()) / n if n else None),
                "down_share": _np(float(d["down"].sum()) / n if n else None),
                "both_deciles": int((d["up"] & d["down"]).sum()),
                "neither": int((~d["up"] & ~d["down"]).sum()),
                "rows_without_future": int(d["oc_close"].is_null().sum())}

    out = {"all": counts(df)}
    for b in ("block1", "block2"):
        out[b] = counts(df.filter(pl.col("block") == b))
    for fam in sorted(df["family"].unique().to_list()):
        out[f"family_{fam}"] = counts(df.filter(pl.col("family") == fam))
    return out


# --------------------------------------------------------------------------- #
# recovery after divergent_down
# --------------------------------------------------------------------------- #
REC_FIELDS = ("bars_to_recovery", "cost_before_recovery", "bars_to_new_low",
              "new_low_before_recovery", "new_low_within_30_after_recovery", "recovered",
              "bars_available", "touch_bars_to_reclaim")


def recovery_stats(labeled: pl.DataFrame, log=lambda *a: None) -> dict:
    """Forward scan per member for divergent_down rows only (up rows get nulls to keep alignment).
    Primary recovery = first later bar whose CLOSE is back at or above the exit price (close-based,
    executable; the exit price is next_open(t) = the open of bar t+1, so a touch by that same bar's
    high is trivially true and is kept only as a labelled secondary variant). Also records the
    deepest low before the recovery, the first later bar below the pre-exit session low, whether
    that new low came first, and the bars actually available after t (the censoring horizon)."""
    sub = labeled.select(["sleeve_day", "family", "entry_rank", "ticker", "et", "next_open",
                          "bar_high", "bar_low", "bar_close", "up", "down"])
    rec = {k: [] for k in REC_FIELDS}
    n_scan = 0
    for part in sub.partition_by(MEMBER_KEYS, maintain_order=True):
        h = part["bar_high"].to_numpy()
        lo = part["bar_low"].to_numpy()
        c = part["bar_close"].to_numpy()
        p0 = part["next_open"].to_numpy()
        up = part["up"].to_numpy()
        down = part["down"].to_numpy()
        n = h.shape[0]
        prior_low = np.minimum.accumulate(lo)
        for i in range(n):
            if not down[i]:
                for k in REC_FIELDS:
                    rec[k].append(None)
                continue
            n_scan += 1
            thr = p0[i]
            if not np.isfinite(thr) or i + 1 >= n:
                for k in REC_FIELDS:
                    rec[k].append(None)
                continue
            rec["bars_available"].append(n - 1 - i)
            close_hit = np.flatnonzero(c[i + 1:] >= thr)     # primary: a close back at the exit
            touch_hit = np.flatnonzero(h[i + 1:] >= thr)     # secondary: a high touching it
            recl = int(i + 1 + close_hit[0]) if close_hit.size else None
            touch = int(i + 1 + touch_hit[0]) if touch_hit.size else None
            newlow = np.flatnonzero(lo[i + 1:] < prior_low[i])
            first_low = int(i + 1 + newlow[0]) if newlow.size else None
            cost = float(lo[i + 1:(recl + 1 if recl is not None else n)].min() / thr - 1.0)
            if recl is None:
                after = None
            else:
                tail = lo[recl + 1:recl + 31]
                after = bool(tail.size and (tail < prior_low[i]).any())
            rec["bars_to_recovery"].append(None if recl is None else recl - i)
            rec["cost_before_recovery"].append(cost)
            rec["bars_to_new_low"].append(None if first_low is None else first_low - i)
            rec["new_low_before_recovery"].append(
                None if (first_low is None or recl is None) else bool(first_low < recl))
            rec["new_low_within_30_after_recovery"].append(after)
            rec["recovered"].append(bool(recl is not None))
            rec["touch_bars_to_reclaim"].append(None if touch is None else touch - i)
    log(f"recovery scan: {n_scan} divergent_down rows over {sub.select(pl.struct(MEMBER_KEYS).n_unique()).item()} members")
    return rec


def _discrete_survival(bars: np.ndarray, tail: np.ndarray) -> dict:
    """One-minute discrete survival of bars-to-recovery with right-censoring at each row's tape
    end: at risk at k = rows whose tape still runs at k and that had not recovered before k."""
    store = np.isfinite(bars)
    k_max = int(np.nanmax(tail)) if np.isfinite(tail).any() else 0
    if k_max <= 0:
        return {"k": [], "at_risk": [], "reclaimed_at_k": [], "hazard": [], "survival": []}
    ks = np.arange(1, min(k_max, 240) + 1)
    n = bars.shape[0]
    at_risk = np.zeros(ks.shape[0])
    reclaimed = np.zeros(ks.shape[0])
    chunk = 20000
    for s in range(0, n, chunk):
        b = bars[s:s + chunk]
        t = tail[s:s + chunk]
        ok_b = np.isfinite(b)
        ok_t = np.isfinite(t)
        for j, k in enumerate(ks):
            at_risk[j] += np.sum(ok_t & (t >= k) & ~(ok_b & (b < k)))
            reclaimed[j] += np.sum(ok_b & (b == k))
    keep = np.ones(ks.shape[0], dtype=bool)
    last = np.max(np.flatnonzero(at_risk >= 20)) if (at_risk >= 20).any() else -1
    keep[last + 1:] = False
    ks, at_risk, reclaimed = ks[keep], at_risk[keep], reclaimed[keep]
    hazard = np.where(at_risk > 0, reclaimed / np.where(at_risk > 0, at_risk, 1.0), 0.0)
    survival = np.cumprod(1.0 - np.clip(hazard, 0.0, 1.0))
    return {"k": ks.tolist(), "at_risk": at_risk.tolist(), "reclaimed_at_k": reclaimed.tolist(),
            "hazard": hazard.tolist(), "survival": survival.tolist(),
            "note": ("one-minute discrete survival, right-censored at each row's tape end; "
                     "truncated where at_risk < 20")}


def _drawdown_hist(vals: np.ndarray, step: float = 0.01, max_depth: float = 0.30) -> dict:
    """Depth histogram: depth = -value, bin i covers [i*step, (i+1)*step); last bin deeper than
    max_depth. Values above zero (depth < 0) are counted separately."""
    v = vals[np.isfinite(vals)]
    if v.size == 0:
        return {"n": 0}
    depth = -v
    edges = np.concatenate((np.arange(0.0, max_depth + step / 2.0, step), [np.inf]))
    counts, _ = np.histogram(depth, bins=edges)
    return {"n": int(v.size),
            "bin_edges": [float(x) for x in edges[:-1]] + [None],
            "counts": [int(c) for c in counts],
            "shares": [float(c) / v.size for c in counts],
            "share_shallower_than_zero": _np(float((depth < 0).mean())),
            "note": ("1%-bins of depth ~ -value from 0 down to -30%; the last bin is deeper than "
                     "-30%; share_shallower_than_zero counts values above the exit price")}


def _joint_surface(bars: np.ndarray, cost: np.ndarray, n_rows: int, k_values: list,
                   dd_cuts: np.ndarray) -> dict:
    """share(recovery within k bars AND deepest pre-recovery drawdown >= c) for every (k, c),
    built by a 2-D accumulate + cumulative sums (no per-pair loop)."""
    store = np.isfinite(bars)
    ks = np.asarray(k_values, dtype=np.float64)
    cuts = np.sort(np.asarray(dd_cuts, dtype=np.float64))
    K, C = ks.shape[0], cuts.shape[0]
    if K == 0 or n_rows == 0:
        return {"k_values": [], "drawdown_cuts": [float(c) for c in cuts], "share": [],
                "share_never_recovered": _np(float(1.0 - store.mean())) if n_rows else None}
    k_arr = bars[store]
    c_arr = cost[store]
    ki = np.searchsorted(ks, k_arr, side="left")          # first k index with k >= event k
    ci = np.searchsorted(cuts, c_arr, side="right")       # number of cuts <= event cost
    Q = np.zeros((K + 1, C + 1))
    np.add.at(Q, (ki, ci), 1.0)
    A = Q.cumsum(axis=0)                                  # prefix over k
    B = A[:, ::-1].cumsum(axis=1)[:, ::-1]                # suffix over cost
    S = B[:K, 1:]                                         # (k index, cut index)
    return {"definition": ("share of divergent_down rows whose CLOSE-based recovery happened "
                           "within k bars AND whose deepest low before the recovery stayed at or "
                           "above the drawdown cut c (fraction); the exact one-minute survival "
                           "curve above is the primary evidence, this is the joint path in "
                           "compact form"),
            "k_values": [int(k) for k in ks],
            "drawdown_cuts": [float(c) for c in cuts],
            "share": [[_np(float(x) / n_rows) for x in row] for row in S],
            "share_never_recovered": _np(float(1.0 - store.mean()))}


def recovery_report(lab: pl.DataFrame) -> dict:
    dn = lab.filter(pl.col("down"))
    up = lab.filter(pl.col("up"))
    bars = dn["bars_to_recovery"].to_numpy()
    store = np.isfinite(bars)
    tail = dn["bars_available"].to_numpy()
    cost = dn["cost_before_recovery"].to_numpy()
    order = dn["new_low_before_recovery"].to_numpy().astype(float)
    order_nan = np.where(np.isnan(order), 0.0, order)
    nl = dn["bars_to_new_low"].to_numpy()
    n_dn = dn.height
    survival = _discrete_survival(bars, tail)
    dd_cuts = np.round(np.arange(0.0, -0.151, -0.005), 4)   # 0.5% steps down to -15%
    joint = _joint_surface(bars, cost, n_dn, survival["k"], dd_cuts) if survival["k"] else \
        {"k_values": [], "drawdown_cuts": [float(c) for c in dd_cuts], "share": []}
    # compact reference rulers (explicitly secondary)
    rulers = {}
    for nb in (5, 15, 30):
        for ccut in (0.0, -0.01, -0.03, -0.05, -0.10):
            ok = store & (bars <= nb) & (cost >= ccut)
            rulers[f"within_{nb}_bars_and_drawdown_ge_{_cutname(ccut)}"] = \
                _np(float(ok.sum()) / n_dn) if n_dn else None
    per_block = {}
    for b in ("block1", "block2"):
        d = dn.filter(pl.col("block") == b)
        bb = d["bars_to_recovery"].to_numpy()
        cc = d["cost_before_recovery"].to_numpy()
        tt = d["bars_available"].to_numpy()
        cl = np.isfinite(bb)
        per_block[b] = {
            "n": d.height,
            "share_never_recovered": _np(float(1.0 - cl.mean()) if d.height else None),
            "median_bars_to_recovery": _np(float(np.median(bb[cl])) if cl.any() else None),
            "median_cost_before_recovery": _np(float(np.median(cc[cl])) if cl.any() else None),
            "reference_share_recovery_within_15_bars_and_drawdown_ge_m1pct": _np(
                float((cl & (bb <= 15) & (cc >= -0.01)).mean()) if d.height else None),
            "median_bars_available": _dist_summary(tt).get("median"),
            "share_new_low_within_30_bars_after_recovery": _np(
                _dist_summary(np.where(d["new_low_within_30_after_recovery"].to_numpy()[cl],
                                       1.0, 0.0)).get("mean")) if cl.any() else None,
            "survival_curve": _discrete_survival(bb, tt)}
    after = dn["new_low_within_30_after_recovery"].to_numpy()
    touch = dn["touch_bars_to_reclaim"].to_numpy()
    up_bars = up["bars_to_next_high"].to_numpy()
    up_fin = np.isfinite(up_bars)
    return {
        "exit_price": "next_open(t) — the panel's own execution price for a decision at bar t",
        "recovery_measure_close_based": ("primary: first later bar u > t whose CLOSE is back at or "
                                          "above the exit price next_open(t); right-censored at the "
                                          "tape end; the high-touch variant is reported separately "
                                          "and is degenerate by construction"),
        "down": {
            "n": n_dn,
            "recovery_measure": ("primary = first later bar whose CLOSE returns to or above the "
                                 "exit price (close-based, executable); see secondary_touch for "
                                 "the touch-based variant and why it is degenerate"),
            "bars_to_recovery_survival_primary": survival,
            "joint_path_bars_to_recovery_x_drawdown_before_recovery": joint,
            "drawdown_before_recovery_distribution_among_recovered": _drawdown_hist(cost[store]),
            "drawdown_distribution_when_never_recovered": _drawdown_hist(cost[~store]),
            "bars_available_distribution": {
                **_dist_summary(tail),
                "share_one_bar_window": _np(float((tail == 1).mean()) if tail.size else None),
                "note": ("bars actually available after t before the tape ends; a one-bar window "
                         "cannot express any later recovery, which is what makes the raw "
                         "never-recovered share an upper bound rather than a fact")},
            "adjusted_recovery_share_by_horizon": {
                "value": _np(float(1.0 - survival["survival"][-1]))
                if survival.get("survival") else None,
                "horizon_k": survival["k"][-1] if survival.get("k") else None,
                "at_risk_at_horizon": survival["at_risk"][-1] if survival.get("at_risk") else None,
                "note": ("censoring-adjusted (discrete/survival-curve) estimate of the share that "
                         "ever recovers, to be read next to the raw share_never_recovered")},
            "share_new_low_within_30_bars_after_recovery": _np(
                float(np.nanmean(after[store].astype(float))) if store.any() else None),
            "bars_to_recovery_distribution_among_recovered": _dist_summary(bars[store]),
            "cost_before_recovery_distribution_among_recovered": _dist_summary(cost[store]),
            "share_never_recovered": _np(float(1.0 - store.mean()) if n_dn else None),
            "secondary_touch_variant": {
                "definition": ("first later bar whose HIGH touches the exit price; the exit price "
                               "is next_open(t) = the open of bar t+1, so that same bar's high "
                               "almost always touches it — the touch variant is degenerate and is "
                               "kept only to document that, never as evidence"),
                "bars_to_touch_distribution": _dist_summary(touch),
                "share_touched": _np(float(np.isfinite(touch).mean()))},
            "first_event": {
                "n_rows": n_dn,
                "share_new_low_before_recovery": _np(float(order_nan.mean()) if n_dn else None),
                "share_new_low_before_recovery_among_recovered": _np(
                    float(np.nanmean(order[store])) if (store.any()
                                                        and np.isfinite(order[store]).any())
                    else None),
                "share_new_low_within_30_bars_after_recovery": _np(
                    _dist_summary(np.where(after[store], 1.0, 0.0)).get("mean"))
                if store.any() else None,
                "median_bars_to_new_low": _dist_summary(nl).get("median")},
            "reference_rulers": {
                "note": ("compact reference rulers only (5/15/30-bar horizons x 0/-1/-3/-5/-10% "
                         "drawdown cuts); the one-minute survival curve and the joint surface "
                         "above are the primary evidence"),
                "share_within_bars_and_drawdown": rulers},
            "per_block": per_block},
        "up_contrast": {
            "definition": ("the mirror for divergent_up uses the panel's own columns: "
                           "bars_to_next_high = bars to the first later bar above running_high(t); "
                           "dd_before_next_high = deepest low until then; distributions and "
                           "survival sense are the primary numbers"),
            "n": up.height,
            "share_without_next_high": _np(float(1.0 - up_fin.mean()) if up.height else None),
            "bars_to_next_high": _dist_summary(up_bars),
            "dd_before_next_high": _dist_summary(up["dd_before_next_high"].to_numpy()),
            "dd_before_next_high_distribution": _drawdown_hist(
                up["dd_before_next_high"].to_numpy())},
    }


# --------------------------------------------------------------------------- #
# unit-level aggregation and day-clustered statistics
# --------------------------------------------------------------------------- #
def unit_aggregates(df: pl.DataFrame, unit_cols: list) -> pl.DataFrame:
    mu = pl.col("side") == 1
    md = pl.col("side") == -1
    aggs = [pl.len().alias("n_total"), mu.sum().alias("cnt_up"), md.sum().alias("cnt_dn"),
            pl.col("block").first().alias("block")]
    for d in DISCRIMINATORS:
        for tag, mask in (("u", mu), ("d", md)):
            aggs += [pl.col(d).filter(mask).count().alias(f"{d}__cnt_{tag}"),
                     pl.col(d).filter(mask).sum().alias(f"{d}__sum_{tag}"),
                     (pl.col(d) ** 2).filter(mask).sum().alias(f"{d}__ssq_{tag}")]
    # explicit sort: group_by row order is not guaranteed, and these rows feed bootstrap draws
    return df.filter(pl.col("side") != 0).group_by(unit_cols).agg(aggs).sort(unit_cols)


def sum_units(units: pl.DataFrame, parent_cols: list) -> pl.DataFrame:
    num = [c for c, t in units.schema.items()
           if c not in parent_cols and c != "block" and t.is_numeric()]
    aggs = [pl.col(c).sum() for c in num]
    aggs.append(pl.col("block").first().alias("block"))
    return units.group_by(parent_cols).agg(aggs).sort(parent_cols)


def _unit_arrays(units: pl.DataFrame, disc: str) -> dict:
    cu = units[f"{disc}__cnt_u"].to_numpy().astype(np.float64)
    cd = units[f"{disc}__cnt_d"].to_numpy().astype(np.float64)
    su = units[f"{disc}__sum_u"].to_numpy().astype(np.float64)
    sd = units[f"{disc}__sum_d"].to_numpy().astype(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        mu = np.where(cu > 0, su / np.where(cu > 0, cu, 1.0), np.nan)
        md = np.where(cd > 0, sd / np.where(cd > 0, cd, 1.0), np.nan)
    valid = (cu > 0) & (cd > 0) & np.isfinite(mu) & np.isfinite(md)
    return {"cu": cu, "cd": cd, "su": su, "sd": sd, "mu": mu, "md": md, "valid": valid,
            "ssq_u": units[f"{disc}__ssq_u"].to_numpy().astype(np.float64),
            "ssq_d": units[f"{disc}__ssq_d"].to_numpy().astype(np.float64)}


def _chunked_ratio_bootstrap(num: np.ndarray, den: np.ndarray, draws: int,
                             rng: np.random.Generator, chunk: int = 250) -> np.ndarray:
    """Poisson bootstrap over the leading axis (clusters) of sum(num)/sum(den)."""
    out = np.empty(draws, dtype=np.float64)
    n = num.shape[0]
    for s in range(0, draws, chunk):
        b = min(chunk, draws - s)
        mult = rng.poisson(1.0, size=(b, n))
        nn = mult @ num
        dd = mult @ den
        with np.errstate(invalid="ignore", divide="ignore"):
            out[s:s + b] = np.where(dd > 0, nn / np.where(dd > 0, dd, 1.0), np.nan)
    return out


def unit_diff_stats(units: pl.DataFrame, disc: str, days: np.ndarray, draws: int,
                    rng: np.random.Generator) -> dict:
    a = _unit_arrays(units, disc)
    valid = a["valid"]
    n_units = int(valid.sum())
    if n_units == 0:
        return {"n_units": 0}
    d = (a["mu"] - a["md"])[valid]
    w = np.minimum(a["cu"], a["cd"])[valid]
    day_idx = days[valid]
    n_days = int(day_idx.max()) + 1
    boot = day_bootstrap_weighted(d * w, w, day_idx, n_days, draws, rng)
    ci = [float(np.nanpercentile(boot, 2.5)), float(np.nanpercentile(boot, 97.5))]
    var_num = float((a["ssq_u"][valid] - a["su"][valid] ** 2 / a["cu"][valid]).sum()
                    + (a["ssq_d"][valid] - a["sd"][valid] ** 2 / a["cd"][valid]).sum())
    var_den = float((a["cu"][valid] - 1).sum() + (a["cd"][valid] - 1).sum())
    sd = math.sqrt(var_num / var_den) if var_den > 0 and var_num > 0 else None
    weighted = float((w * d).sum() / w.sum())
    return {"n_units": n_units, "n_rows_up": int(a["cu"][valid].sum()),
            "n_rows_down": int(a["cd"][valid].sum()),
            "diff_weighted": _np(weighted), "diff_median_unit": _np(float(np.median(d))),
            "ci95_day_clustered": ci, "within_unit_sd": _np(sd),
            "effect_size": _np(weighted / sd) if sd else None,
            "units_up_higher": int((d > 0).sum()), "units_down_higher": int((d < 0).sum()),
            "mean_up": _np(float((a["su"][valid] / a["cu"][valid]).mean())),
            "mean_down": _np(float((a["sd"][valid] / a["cd"][valid]).mean()))}


def day_bootstrap_weighted(num: np.ndarray, den: np.ndarray, day_idx: np.ndarray, n_days: int,
                           draws: int, rng: np.random.Generator, chunk: int = 250) -> np.ndarray:
    """Poisson bootstrap over days: units carry their day; each unit appears exactly once."""
    out = np.empty(draws, dtype=np.float64)
    for s in range(0, draws, chunk):
        b = min(chunk, draws - s)
        mult = rng.poisson(1.0, size=(b, n_days))
        m = mult[:, day_idx]
        nn = (m * num).sum(axis=1)
        dd = (m * den).sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[s:s + b] = np.where(dd > 0, nn / np.where(dd > 0, dd, 1.0), np.nan)
    return out


def cell_pooled_point(cells: pl.DataFrame, disc: str) -> dict:
    a = _unit_arrays(cells, disc)
    valid = a["valid"]
    if not valid.any():
        return {"n_cells": 0}
    d = (a["mu"] - a["md"])[valid]
    w = np.minimum(a["cu"], a["cd"])[valid]
    var_num = float((a["ssq_u"][valid] - a["su"][valid] ** 2 / a["cu"][valid]).sum()
                    + (a["ssq_d"][valid] - a["sd"][valid] ** 2 / a["cd"][valid]).sum())
    var_den = float((a["cu"][valid] - 1).sum() + (a["cd"][valid] - 1).sum())
    sd = math.sqrt(var_num / var_den) if var_den > 0 and var_num > 0 else None
    weighted = float((w * d).sum() / w.sum())
    return {"n_cells": int(valid.sum()), "diff_weighted": _np(weighted),
            "diff_median_cell": _np(float(np.median(d))), "within_cell_sd": _np(sd),
            "effect_size": _np(weighted / sd) if sd else None,
            "cells_up_higher": int((d > 0).sum()), "cells_down_higher": int((d < 0).sum())}


# --------------------------------------------------------------------------- #
# pairing and pair-level statistics
# --------------------------------------------------------------------------- #
PAIR_CARRY = ("sleeve_day", "family", "entry_rank", "ticker", "et", "bar_index", "bar_close",
              "bar_high", "bar_low", "next_open", "oc_close", "final_high_flag",
              "v_forced_flat", "ret_from_fill", "block", "clk", "member",
              "b_ret_from_fill", "b_bars_below_entry_episode", "b_dist_from_running_high",
              "b_reclaim_count", "b_bar_index",
              "bars_to_recovery", "cost_before_recovery", "bars_available", "bars_to_new_low",
              "touch_bars_to_reclaim", "recovered")


def pair_frame(labeled: pl.DataFrame, unit_cols: list, distinct_members: bool) -> pl.DataFrame:
    key = unit_cols + ["sleeve_day", "family", "entry_rank", "ticker", "et"]
    m = labeled.sort(key)
    if distinct_members:
        m = m.unique(subset=unit_cols + ["side", "member"], keep="first", maintain_order=True)
    cols = list(dict.fromkeys([c for c in PAIR_CARRY if c in m.columns]
                              + list(DISCRIMINATORS)))
    m = m.with_columns(pl.col("et").cum_count().over(unit_cols + ["side"]).alias("k"))
    up = m.filter(pl.col("side") == 1).select(
        unit_cols + ["k"] + [pl.col(c).alias(c + "_u") for c in cols])
    dn = m.filter(pl.col("side") == -1).select(
        unit_cols + ["k"] + [pl.col(c).alias(c + "_d") for c in cols])
    pairs = up.join(dn, on=unit_cols + ["k"], how="inner")
    same = (pl.col("sleeve_day_u") == pl.col("sleeve_day_d")) & \
           (pl.col("ticker_u") == pl.col("ticker_d"))
    # explicit sort: the join order can vary across processes and the pair rows feed bootstraps
    return pairs.with_columns(same.alias("same_member")).sort(unit_cols + ["k"])


def _dyadic_bootstrap(hit: np.ndarray, cu: np.ndarray, cd: np.ndarray, n_clusters: int,
                      draws: int, rng: np.random.Generator, chunk: int = 64) -> np.ndarray:
    """Multiplicative two-way (both-sides) cluster bootstrap: the weight of a pair is the product
    of its two sides' Poisson multiplicities, so a draw resamples BOTH members of every pair.
    Used for cross-day pairings, where one-sided clustering would ignore the other side's
    dependence. Same-day pairings keep the one-sided cluster bootstrap (both sides share a day)."""
    out = np.empty(draws, dtype=np.float64)
    for s in range(0, draws, chunk):
        b = min(chunk, draws - s)
        mu = rng.poisson(1.0, size=(b, n_clusters))
        md = rng.poisson(1.0, size=(b, n_clusters))
        w = mu[:, cu] * md[:, cd]                      # (b, n_pairs)
        num = (w * hit).sum(axis=1)
        den = w.sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[s:s + b] = np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)
    return out


def pair_stats(pairs: pl.DataFrame, disc: str, days: dict, tickets: dict, draws: int,
               rng: np.random.Generator, unit_cols: list, dyadic: bool = False) -> dict:
    """Pair hit rate and mean difference with cluster bootstrap CIs. dyadic=False: clusters are the
    up side's day and ticket (valid when both sides share the day, and used as the one-sided view
    otherwise); dyadic=True: multiplicative two-way bootstrap resampling both sides."""
    if pairs.height == 0:
        return {"n_pairs": 0}
    xu = pairs[f"{disc}_u"].cast(pl.Float64).to_numpy()
    xd = pairs[f"{disc}_d"].cast(pl.Float64).to_numpy()
    valid = np.isfinite(xu) & np.isfinite(xd)
    n = int(valid.sum())
    if n == 0:
        return {"n_pairs": 0}
    wins_v = (xu[valid] > xd[valid]).astype(np.float64)
    ties_v = (xu[valid] == xd[valid]).astype(np.float64)
    hit = wins_v + 0.5 * ties_v
    diff = xu[valid] - xd[valid]
    wins = int(wins_v.sum()); ties = int(ties_v.sum()); losses = n - wins - ties
    day_of = {d: i for i, d in enumerate(sorted(days))}
    day_u = np.array([day_of[d] for d in pairs["sleeve_day_u"].to_list()],
                     dtype=np.int64)[valid]
    day_d = np.array([day_of[d] for d in pairs["sleeve_day_d"].to_list()],
                     dtype=np.int64)[valid]
    n_days = int(max(day_u.max(), day_d.max())) + 1
    tk_of = {t: i for i, t in enumerate(sorted(tickets))}
    tk_u = np.array([tk_of[t] for t in pairs["member_u"].to_list()], dtype=np.int64)[valid]
    tk_d = np.array([tk_of[t] for t in pairs["member_d"].to_list()], dtype=np.int64)[valid]
    n_tk = int(max(tk_u.max(), tk_d.max())) + 1
    if dyadic:
        boot_hit = _dyadic_bootstrap(hit, day_u, day_d, n_days, draws, rng)
        boot_diff = _dyadic_bootstrap(diff, day_u, day_d, n_days, draws, rng)
        boot_hit_tk = _dyadic_bootstrap(hit, tk_u, tk_d, n_tk, draws, rng)
        scheme = "dyadic two-way (up-side x down-side) day/ticket bootstrap"
    else:
        w_d = np.bincount(day_u, weights=hit, minlength=n_days)
        n_d = np.bincount(day_u, weights=np.ones_like(day_u, dtype=np.float64),
                          minlength=n_days)
        d_d = np.bincount(day_u, weights=diff, minlength=n_days)
        boot_hit = _chunked_ratio_bootstrap(w_d, n_d, draws, rng)
        boot_diff = _chunked_ratio_bootstrap(d_d, n_d, draws, rng)
        tk_w = np.bincount(tk_u, weights=hit, minlength=n_tk)
        tk_n = np.bincount(tk_u, weights=np.ones_like(tk_u, dtype=np.float64), minlength=n_tk)
        boot_hit_tk = _chunked_ratio_bootstrap(tk_w, tk_n, draws, rng)
        scheme = "one-sided up-side day/ticket bootstrap (valid when both sides share the day)"
    return {
        "n_pairs": n, "share_up_higher": _np(float((wins + 0.5 * ties) / n)),
        "ci95_day_clustered": [float(np.nanpercentile(boot_hit, 2.5)),
                               float(np.nanpercentile(boot_hit, 97.5))],
        "ci95_ticket_clustered": [float(np.nanpercentile(boot_hit_tk, 2.5)),
                                  float(np.nanpercentile(boot_hit_tk, 97.5))],
        "ci95_scheme": scheme,
        "pair_wins": wins, "pair_losses": losses, "pair_ties": ties,
        "mean_diff": _np(float(diff.mean())), "median_diff": _np(float(np.median(diff))),
        "ci95_mean_diff_day_clustered": [float(np.nanpercentile(boot_diff, 2.5)),
                                         float(np.nanpercentile(boot_diff, 97.5))],
        "n_day_clusters_both_sides": int(len(set(day_u.tolist()) | set(day_d.tolist()))),
        "n_ticket_clusters_both_sides": int(len(set(tk_u.tolist()) | set(tk_d.tolist())))}


# --------------------------------------------------------------------------- #
# one matching tier
# --------------------------------------------------------------------------- #
def _tenure_stats(pairs: pl.DataFrame) -> dict:
    """The actual within-pair bar_index (tenure) difference distribution: the bucket edges make
    tenure comparable, this reports by how much the two sides actually differ."""
    if pairs.height == 0 or "bar_index_u" not in pairs.columns:
        return {"n_pairs": 0}
    du = pairs["bar_index_u"].cast(pl.Float64).to_numpy()
    dd = pairs["bar_index_d"].cast(pl.Float64).to_numpy()
    ok = np.isfinite(du) & np.isfinite(dd)
    diff = du[ok] - dd[ok]
    if diff.size == 0:
        return {"n_pairs": 0}
    absd = np.abs(diff)
    return {"n_pairs": int(diff.size), "median": _np(float(np.median(diff))),
            "mean": _np(float(diff.mean())), "p10": _np(float(np.percentile(diff, 10))),
            "p90": _np(float(np.percentile(diff, 90))), "min": _np(float(diff.min())),
            "max": _np(float(diff.max())),
            "share_identical": _np(float((absd == 0).mean())),
            "share_within_1_bar": _np(float((absd <= 1).mean())),
            "share_within_5_bars": _np(float((absd <= 5).mean())),
            "note": ("bar_index buckets are a comparability device, not a policy boundary; this "
                     "distribution is the authoritative tenure-comparability evidence")}


def tier_report(units: pl.DataFrame, pairs: pl.DataFrame, pairs_distinct: pl.DataFrame,
                cfg: dict, rng: np.random.Generator, days: dict, tickets: dict,
                unit_cols: list, mode: str = "units", log=print,
                cell_level: "pl.DataFrame | None" = None, dyadic: bool = False,
                primacy: str = "primary", inference_note: str = "") -> dict:
    """mode 'units': day-carrying units give the day-clustered difference CI.
    mode 'pairs': the tier pools days by construction (population view), so the group difference
    is a point estimate over strata and the day-clustered uncertainty comes from the pair
    statistic (each pair is attached to the day of its divergent_up row)."""
    out = {"mode": mode, "primacy": primacy,
           "inference": {"pair_clustering": ("dyadic two-way (both sides)" if dyadic else
                                             "one-sided (up side)"),
                         "unit_clustering": ("day, both sides share the day" if mode == "units"
                                             else "n/a - point estimate"),
                         "note": inference_note},
           "n_pairs": int(pairs.height),
           "n_pairs_member_distinct": int(pairs_distinct.height)}
    if mode == "units":
        usable = (units["cnt_up"].to_numpy() > 0) & (units["cnt_dn"].to_numpy() > 0)
        units = units.filter(pl.Series(usable))
        day_arr = np.array([days[d] for d in units["sleeve_day"].to_list()], dtype=np.int64)
        blocks = units["block"].to_numpy()
        out.update({"n_units_with_both_deciles": int(units.height),
                    "rows_up_in_units": int(units["cnt_up"].sum()),
                    "rows_down_in_units": int(units["cnt_dn"].sum())})
    else:
        day_arr = None
        blocks = None
        out.update({"n_strata": int(units.height),
                    "rows_up_in_strata": int(units["cnt_up"].sum()),
                    "rows_down_in_strata": int(units["cnt_dn"].sum())})
    if cell_level is not None:
        out["unit_level_pooled_point_estimate"] = {d: cell_pooled_point(cell_level, d)
                                                   for d in DISCRIMINATORS}
    out["tenure_difference_bars"] = _tenure_stats(pairs)
    out["tenure_difference_bars_member_distinct"] = _tenure_stats(pairs_distinct)
    out["tenure_difference_bars_per_block"] = {
        b: _tenure_stats(pairs.filter(pl.col("block_u") == b)) for b in ("block1", "block2")}
    res = {}
    for disc in DISCRIMINATORS:
        if mode == "units":
            st = unit_diff_stats(units, disc, day_arr, cfg["bootstrap"], rng)
        else:
            st = cell_pooled_point(units, disc)
        pr = pair_stats(pairs, disc, days, tickets, cfg["bootstrap"], rng, unit_cols,
                        dyadic=dyadic)
        pr_md = pair_stats(pairs_distinct, disc, days, tickets, cfg["bootstrap"], rng, unit_cols,
                           dyadic=dyadic)
        direction = st.get("diff_weighted") or 0.0
        blocks_out = {}
        for b in ("block1", "block2"):
            if mode == "units":
                bmask = blocks == b
                bst = unit_diff_stats(units.filter(pl.Series(bmask)), disc, day_arr[bmask],
                                      cfg["bootstrap"], rng)
            else:
                bst = {}
            bpr = pair_stats(pairs.filter(pl.col("block_u") == b), disc, days, tickets,
                             cfg["bootstrap"], rng, unit_cols, dyadic=dyadic)
            blocks_out[b] = {"diff_weighted": bst.get("diff_weighted"),
                             "ci95_day_clustered": bst.get("ci95_day_clustered"),
                             "n_units": bst.get("n_units"), "n_pairs": bpr.get("n_pairs"),
                             "share_up_higher": bpr.get("share_up_higher"),
                             "ci95_day_clustered_pairs": bpr.get("ci95_day_clustered")}
        # ---- ONE verdict vocabulary; the survivor lists derive from exactly this ----
        ci = pr.get("ci95_day_clustered") or [None, None]
        share = pr.get("share_up_higher")
        unit_dir = st.get("diff_weighted") or 0.0
        up_side = ((share is not None and share > 0.5) if mode == "pairs" else (unit_dir >= 0))
        hit_sig = (ci[0] is not None and ci[1] is not None
                   and ((up_side and ci[0] > 0.5) or (not up_side and ci[1] < 0.5)))
        b1 = blocks_out["block1"]["share_up_higher"]
        b2 = blocks_out["block2"]["share_up_higher"]
        blocks_agree = (b1 is not None and b2 is not None
                        and (b1 > 0.5) == (b2 > 0.5) == up_side)
        c1 = blocks_out["block1"]["ci95_day_clustered_pairs"] or [None, None]
        c2 = blocks_out["block2"]["ci95_day_clustered_pairs"] or [None, None]

        def _side_ci(c) -> bool:
            if c[0] is None or c[1] is None:
                return False
            return (c[0] > 0.5) if up_side else (c[1] < 0.5)

        blocks_sig = _side_ci(c1) and _side_ci(c2)
        # OUT-OF-BLOCK: the sign of one block's pair share applied to the other block's pairs
        oob = {}
        for src, dst in (("block1", "block2"), ("block2", "block1")):
            s_src = blocks_out[src]["share_up_higher"]
            entry_oob = {"sign_source": None, "share_points_sign": None,
                         "ci95_day_clustered": [None, None],
                         "n_pairs": blocks_out[dst]["n_pairs"],
                         "other_block_diff": blocks_out[dst]["diff_weighted"],
                         "other_block_diff_ci95": blocks_out[dst]["ci95_day_clustered"],
                         "holds": False}
            other = blocks_out[dst]["share_up_higher"]
            pci = blocks_out[dst]["ci95_day_clustered_pairs"] or [None, None]
            if s_src is not None and other is not None and s_src != 0.5:
                src_up = s_src > 0.5
                entry_oob["sign_source"] = "up higher" if src_up else "down higher"
                if src_up:
                    entry_oob["share_points_sign"] = _np(other)
                    entry_oob["ci95_day_clustered"] = pci
                else:
                    entry_oob["share_points_sign"] = _np(1.0 - other)
                    entry_oob["ci95_day_clustered"] = [
                        None if pci[1] is None else _np(1.0 - pci[1]),
                        None if pci[0] is None else _np(1.0 - pci[0])]
                lo = entry_oob["ci95_day_clustered"][0]
                entry_oob["holds"] = bool(lo is not None and lo > 0.5)
            oob[f"{src}_sign_on_{dst}"] = entry_oob
        transfer = all(v["holds"] for v in oob.values())
        if hit_sig and blocks_sig and transfer:
            verdict = "separates out-of-block"
        elif hit_sig and transfer:
            verdict = "soft out-of-block"
        elif hit_sig and blocks_agree:
            verdict = "in-block only"
        elif b1 is not None and b2 is not None and (b1 > 0.5) != (b2 > 0.5):
            verdict = "flips across blocks"
        else:
            verdict = "no separation"
        separator_entry = {
            "discriminator": disc, "sign": "up" if up_side else "down",
            "n_pairs": pr.get("n_pairs"), "hit_rate": _np(share),
            "ci95_day_clustered": ci,
            "block1": {"n_pairs": blocks_out["block1"]["n_pairs"], "hit_rate": b1,
                       "ci95": c1},
            "block2": {"n_pairs": blocks_out["block2"]["n_pairs"], "hit_rate": b2,
                       "ci95": c2},
            "sign_transfer_both_directions": bool(transfer),
            "verdict": verdict}
        prior, note = PRIORS[disc]
        prior_met = (bool(direction > 0) if prior == "+" else
                     bool(direction < 0) if prior == "-" else None)
        res[disc] = {
            "prior_sign": prior, "prior_note": note, "prior_met": prior_met,
            "direction": ("up higher" if up_side else "down higher"),
            "units": st, "pairs": pr, "pairs_member_distinct": pr_md,
            "block1": blocks_out["block1"], "block2": blocks_out["block2"],
            "blocks_agree_sign": bool(blocks_agree),
            "out_of_block": oob,
            "sign_transfer_both_directions": bool(transfer),
            "separator_entry": separator_entry,
            "verdict": verdict}
        log(f"    {disc:<28} {res[disc]['direction']:<11} "
            f"diff={0.0 if direction is None else direction:+.5g} "
            f"pair={pr.get('share_up_higher')} ({pr.get('n_pairs')}) [{verdict}]")
    out["discriminators"] = res
    return out


# --------------------------------------------------------------------------- #
# divergence timing
# --------------------------------------------------------------------------- #
def timing_report(df: pl.DataFrame, labeled: pl.DataFrame, pairs: pl.DataFrame,
                  cfg: dict) -> dict:
    base = df.filter(pl.col("stratum_rows") >= cfg["min_stratum"])
    rows = []
    grp = {}
    for name, frame, filt in (("base_same_minute_all_rows", base, pl.lit(True)),
                              ("up", labeled, pl.col("up")),
                              ("down", labeled, pl.col("down"))):
        g = (frame.filter(filt).group_by(["clk", "block"])
             .agg([pl.len().alias("n"), pl.col("bars_since_gap").count().alias("nn"),
                   (pl.col("bars_since_gap") <= 5).sum().alias("le5"),
                   pl.col("bars_since_gap").median().alias("med"),
                   pl.col("bars_since_gap").mean().alias("mean")]))
        for r in g.iter_rows(named=True):
            grp[(name, int(r["clk"]), r["block"])] = r
    for clk in sorted({k[1] for k in grp}):
        entry = {"clock_bucket": f"{_hm(clk)}-{_hm(clk + CLOCK_REPORT_MINUTES - 1)}",
                 "clk": int(clk)}
        tot = {}
        for name in ("base_same_minute_all_rows", "up", "down"):
            n = nn = le5 = 0
            per_block = {}
            for b in ("block1", "block2"):
                r = grp.get((name, clk, b))
                if r:
                    n += int(r["n"]); nn += int(r["nn"]); le5 += int(r["le5"])
                per_block[b] = {"n": int(r["n"]) if r else 0,
                                "share_le5": _np(r["le5"] / r["nn"] if r and r["nn"] else None)}
            tot[name] = {"n": n, "share_le5": _np(le5 / nn if nn else None),
                         "per_block": per_block}
        entry["base_same_minute_all_rows"] = {"n": tot["base_same_minute_all_rows"]["n"],
                                              "share_le5": tot["base_same_minute_all_rows"]["share_le5"]}
        b0 = tot["base_same_minute_all_rows"]["share_le5"]
        for side in ("up", "down"):
            entry[side] = tot[side]
            v = tot[side]["share_le5"]
            entry[f"enrichment_{side}_vs_base"] = _np(v / b0) if (v is not None and b0) else None
        rows.append(entry)
    overall = {}
    for name, frame, filt in (("base_same_minute_all_rows", base, pl.lit(True)),
                              ("up", labeled, pl.col("up")), ("down", labeled, pl.col("down"))):
        s = frame.filter(filt)["bars_since_gap"]
        nn = int(s.count())
        overall[name] = {"n": frame.filter(filt).height, "n_nonnull": nn,
                         "share_le5": _np((s <= 5).sum() / nn if nn else None),
                         "mean": _np(s.mean()), "median": _np(s.median())}
    pair_timing = {}
    if pairs.height:
        gu = pairs["bars_since_gap_u"].cast(pl.Float64).to_numpy()
        gd = pairs["bars_since_gap_d"].cast(pl.Float64).to_numpy()
        ok = np.isfinite(gu) & np.isfinite(gd)
        n = int(ok.sum())
        if n:
            pair_timing["state_matched_pairs"] = {
                "n_pairs": n,
                "share_up_side_le5": _np(float((gu[ok] <= 5).mean())),
                "share_down_side_le5": _np(float((gd[ok] <= 5).mean())),
                "share_both_le5": _np(float(((gu[ok] <= 5) & (gd[ok] <= 5)).mean())),
                "median_gap_up": _np(float(np.median(gu[ok]))),
                "median_gap_down": _np(float(np.median(gd[ok]))),
                "mean_gap_up": _np(float(gu[ok].mean())),
                "mean_gap_down": _np(float(gd[ok].mean()))}
    b0 = overall["base_same_minute_all_rows"]["share_le5"]
    u = overall["up"]["share_le5"]
    d = overall["down"]["share_le5"]
    if b0 and u is not None and d is not None:
        if max(u, d) <= 1.25 * b0:
            verdict = (f"diffuse: matched moments are not enriched for gap proximity "
                       f"(up={u:.4f}, down={d:.4f} vs matched base={b0:.4f})")
        else:
            side = "up" if u >= d else "down"
            verdict = (f"clustered after gaps on the {side} side "
                       f"(up={u:.4f}, down={d:.4f} vs matched base={b0:.4f})")
    else:
        verdict = "indeterminate"
    return {"definition": ("bars_since_gap at the matched (divergent-decile) moments versus the "
                           "base rate of the same state among ALL rows of the same stratum "
                           "(min_stratum respected)"),
            "reference_ruler_only": True,
            "clock_note": ("the 15-minute clock split below is an explicitly labelled compact "
                           "reference ruler; the matching itself is exact-minute and "
                           "within-family, and the exact-minute results are authoritative"),
            "overall": overall, "pair_level": pair_timing,
            "by_clock_bucket_reference_ruler": rows,
            "verdict": verdict}


# --------------------------------------------------------------------------- #
# deterministic sample of concrete pairs
# --------------------------------------------------------------------------- #
def choose_sample(pairs: pl.DataFrame, unit_col: str, n_sample: int) -> list:
    if pairs.height == 0:
        return []
    cells = (pairs.select(["clk_u", unit_col]).unique().sort(["clk_u", unit_col]))
    chosen = []
    for i, part in enumerate(cells.partition_by("clk_u", maintain_order=True)):
        keys = part[unit_col].to_list()
        picks = [0] if len(keys) == 1 else sorted({len(keys) // 3, (2 * len(keys)) // 3,
                                                   len(keys) - 1})
        for pos in picks:
            chosen.append(keys[pos])
    if len(chosen) > n_sample:
        idx = np.linspace(0, len(chosen) - 1, n_sample).round().astype(int)
        chosen = [chosen[i] for i in sorted(set(idx.tolist()))]
    out = []
    seen = set()
    for j, cell_key in enumerate(chosen):
        if cell_key in seen:
            continue
        seen.add(cell_key)
        sub = pairs.filter(pl.col(unit_col) == cell_key).sort(["block_u", "k"])
        if sub.height == 0:
            continue
        pref = "block1" if j % 2 == 0 else "block2"
        sel = sub.filter(pl.col("block_u") == pref)
        row = (sel if sel.height else sub).row(0, named=True)
        clk = int(row["clk_u"])
        out.append({
            "cell_key": cell_key,
            "clock_bucket": f"{_hm(clk)}-{_hm(clk + CLOCK_REPORT_MINUTES - 1)}",
            "block": row["block_u"],
            "cell_text": " | ".join([
                f"{_hm(int(row['et_u']))} {row['family_u']}",
                f"ret_from_fill {bucket_text('ret_from_fill', int(row['b_ret_from_fill_u']))}",
                f"below_entry_ep {bucket_text('bars_below_entry_episode', int(row['b_bars_below_entry_episode_u']))}",
                f"dist_from_high {bucket_text('dist_from_running_high', int(row['b_dist_from_running_high_u']))}",
                f"reclaim {bucket_text('reclaim_count', int(row['b_reclaim_count_u']))}",
                f"bar_index {bucket_text('bar_index', int(row['b_bar_index_u']))}"]),
            "cell_buckets": {
                "ret_from_fill": bucket_text("ret_from_fill", int(row["b_ret_from_fill_u"])),
                "bars_below_entry_episode": bucket_text(
                    "bars_below_entry_episode", int(row["b_bars_below_entry_episode_u"])),
                "dist_from_running_high": bucket_text(
                    "dist_from_running_high", int(row["b_dist_from_running_high_u"])),
                "reclaim_count": bucket_text("reclaim_count", int(row["b_reclaim_count_u"])),
                "bar_index": bucket_text("bar_index", int(row["b_bar_index_u"]))},
            "up": {"sleeve_day": row["sleeve_day_u"], "ticker": row["ticker_u"],
                   "family": row["family_u"], "entry_rank": int(row["entry_rank_u"]),
                   "et": int(row["et_u"]), "oc_close": _np(row["oc_close_u"]),
                   "bar_close": _np(row["bar_close_u"]), "next_open": _np(row["next_open_u"])},
            "down": {"sleeve_day": row["sleeve_day_d"], "ticker": row["ticker_d"],
                     "family": row["family_d"], "entry_rank": int(row["entry_rank_d"]),
                     "et": int(row["et_d"]), "oc_close": _np(row["oc_close_d"]),
                     "bar_close": _np(row["bar_close_d"]), "next_open": _np(row["next_open_d"])},
            "tenure_bars": {"up": int(row["bar_index_u"]), "down": int(row["bar_index_d"])},
            "down_recovery": {
                "bars_to_recovery": _np(row.get("bars_to_recovery_d")),
                "cost_before_recovery": _np(row.get("cost_before_recovery_d")),
                "touch_bars_to_reclaim": _np(row.get("touch_bars_to_reclaim_d")),
                "bars_available_after_t": _np(row.get("bars_available_d")),
                "bars_to_new_low": _np(row.get("bars_to_new_low_d"))},
            "discriminators": {c: {"up": _np(row.get(f"{c}_u")), "down": _np(row.get(f"{c}_d"))}
                               for c in DISCRIMINATORS}})
        if len(out) >= n_sample:
            break
    return out


# --------------------------------------------------------------------------- #
# the analysis (shared by the CLI and the self-test)
# --------------------------------------------------------------------------- #
def _group_summary(frame: pl.DataFrame, cols: list) -> list:
    out = []
    for b in ("block1", "block2"):
        for lab_name, filt in (("divergent_up", pl.col("up")), ("divergent_down", pl.col("down"))):
            sub = frame.filter(filt & (pl.col("block") == b))
            row = {"block": b, "label": lab_name, "rows": sub.height}
            for c in cols:
                if sub.height == 0 or c not in sub.columns:
                    row[c] = None
                    continue
                series = sub[c]
                if series.dtype == pl.Boolean:
                    series = series.cast(pl.Float64)
                if series.dtype == pl.Null or series.count() == 0:
                    row[c] = {"n_nonnull": 0, "mean": None, "median": None, "p10": None,
                              "p90": None, "sum": None, "top5_share_of_sum": None}
                    continue
                tot = series.sum()
                row[c] = {"n_nonnull": int(series.count()), "mean": _np(series.mean()),
                          "median": _np(series.median()), "p10": _np(series.quantile(0.10)),
                          "p90": _np(series.quantile(0.90)), "sum": _np(tot),
                          "top5_share_of_sum": _np(series.top_k(5).sum() / tot)
                          if (tot is not None and tot > 0) else None}
            out.append(row)
    return out


PRIMARY_UNIT = ["sleeve_day", "et", "family", "b_bar_index"]
MIDDLE_AXIS_CARRY = ["sleeve_day", "member", "bar_index", "bar_high", "bar_low", "bar_close",
                     "v_forced_flat", "oc_close", "final_high_flag", "block", "clk"]
CLV_BUCKETS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


def add_pair_outcomes(pairs: pl.DataFrame) -> pl.DataFrame:
    """Pair-level REALIZED post-t outcome labels (strictly future path facts, no session close):
      outcome_up_new_high       the up-side member made a new high after the match minute
      outcome_down_failure_path the down-side member made no new high after t AND its best later
                                CLOSE never regained its next_open (the executable exit price)
      outcome_exec_continuation the up-side member's executable hold-to-flat value (engine
                                forced-flat price, future) exceeds the down-side member's
    plus the close-location-within-bar of both sides for conditioning."""
    rng_u = pl.col("bar_high_u") - pl.col("bar_low_u")
    rng_d = pl.col("bar_high_d") - pl.col("bar_low_d")
    return pairs.with_columns([
        (pl.col("final_high_flag_u") == False).alias("outcome_up_new_high"),          # noqa: E712
        ((pl.col("final_high_flag_d") == True)                                       # noqa: E712
         & (pl.col("oc_close_d") < 0)).alias("outcome_down_failure_path"),
        (pl.col("v_forced_flat_u") > pl.col("v_forced_flat_d"))
        .alias("outcome_exec_continuation"),
        (pl.when(rng_u > 0).then((pl.col("bar_close_u") - pl.col("bar_low_u")) / rng_u)
           .otherwise(None)).alias("clv_u"),
        (pl.when(rng_d > 0).then((pl.col("bar_close_d") - pl.col("bar_low_d")) / rng_d)
           .otherwise(None)).alias("clv_d"),
    ]).with_columns([
        bucket_expr("clv_u", CLV_BUCKETS).alias("b_clv_u"),
        bucket_expr("clv_d", CLV_BUCKETS).alias("b_clv_d"),
    ])


def _boot_diff_of_ratios(a_d: np.ndarray, na_d: np.ndarray, b_d: np.ndarray, nb_d: np.ndarray,
                         draws: int, rng: np.random.Generator, chunk: int = 64) -> np.ndarray:
    """Day-clustered Poisson bootstrap of (sum a / sum na) - (sum b / sum nb), i.e. exactly the
    estimator the reported point uses (with multiplicity 1). Never use a fused cross-product ratio
    here: that targets a different functional and can put the point outside its own CI."""
    out = np.empty(draws, dtype=np.float64)
    n_days = a_d.shape[0]
    for s0 in range(0, draws, chunk):
        b = min(chunk, draws - s0)
        m = rng.poisson(1.0, size=(b, n_days))
        n1 = m @ a_d
        d1 = m @ na_d
        n2 = m @ b_d
        d2 = m @ nb_d
        with np.errstate(invalid="ignore", divide="ignore"):
            r1 = np.where(d1 > 0, n1 / np.where(d1 > 0, d1, 1.0), np.nan)
            r2 = np.where(d2 > 0, n2 / np.where(d2 > 0, d2, 1.0), np.nan)
        out[s0:s0 + b] = r1 - r2
    return out


def realized_outcome_stats(pairs: pl.DataFrame, disc: str, days: dict, outcome_col: str,
                           draws: int, rng: np.random.Generator,
                           suf_a: str = "u", suf_b: str = "d") -> dict:
    """Does the discriminator point the way of the REALIZED post-t outcome? Concordance between
    (x_up > x_down) and the pair outcome, plus the two-group difference of share_up_higher,
    both with day-clustered bootstrap CIs (same-day pairs share a day)."""
    if pairs.height == 0:
        return {"n_pairs": 0}
    xu = pairs[f"{disc}_{suf_a}"].cast(pl.Float64).to_numpy()
    xd = pairs[f"{disc}_{suf_b}"].cast(pl.Float64).to_numpy()
    y = pairs[outcome_col].cast(pl.Float64).to_numpy()
    valid = np.isfinite(xu) & np.isfinite(xd) & np.isfinite(y)
    n = int(valid.sum())
    if n == 0:
        return {"n_pairs": 0}
    yv = y[valid]
    up_higher = (xu[valid] > xd[valid]).astype(np.float64)
    ties = (xu[valid] == xd[valid]).astype(np.float64)
    conc = (up_higher == yv).astype(np.float64)
    conc[ties.astype(bool)] = 0.5
    day_of = {d: i for i, d in enumerate(sorted(days))}
    day_col = "sleeve_day_u" if suf_a == "u" else f"sleeve_day_{suf_a}"
    day_arr = np.array([day_of[d] for d in pairs[day_col].to_list()], dtype=np.int64)[valid]
    n_days = int(day_arr.max()) + 1
    ones = np.ones_like(day_arr, dtype=np.float64)
    c_d = np.bincount(day_arr, weights=conc, minlength=n_days)
    n_d = np.bincount(day_arr, weights=ones, minlength=n_days)
    a_d = np.bincount(day_arr, weights=up_higher * yv, minlength=n_days)
    na_d = np.bincount(day_arr, weights=yv, minlength=n_days)
    b_d = np.bincount(day_arr, weights=up_higher * (1.0 - yv), minlength=n_days)
    nb_d = np.bincount(day_arr, weights=1.0 - yv, minlength=n_days)
    boot_conc = _chunked_ratio_bootstrap(c_d, n_d, draws, rng)
    have_both_groups = na_d.sum() > 0 and nb_d.sum() > 0
    boot_diff = (_boot_diff_of_ratios(a_d, na_d, b_d, nb_d, draws, rng)
                 if have_both_groups else np.full(draws, np.nan))
    share_a = float(a_d.sum() / na_d.sum()) if na_d.sum() > 0 else None
    share_b = float(b_d.sum() / nb_d.sum()) if nb_d.sum() > 0 else None
    diff = (share_a - share_b) if (share_a is not None and share_b is not None) else None
    return {
        "n_pairs": n, "n_outcome_true": int(na_d.sum()), "n_outcome_false": int(nb_d.sum()),
        "concordance_hit_rate": _np(float(c_d.sum() / n)),
        "concordance_ci95_day_clustered": [float(np.nanpercentile(boot_conc, 2.5)),
                                           float(np.nanpercentile(boot_conc, 97.5))],
        "share_up_higher_when_outcome_true": _np(share_a),
        "share_up_higher_when_outcome_false": _np(share_b),
        "difference_true_minus_false": _np(diff),
        "difference_ci95_day_clustered": ([float(np.nanpercentile(boot_diff, 2.5)),
                                           float(np.nanpercentile(boot_diff, 97.5))]
                                          if have_both_groups else [None, None]),
        "direction": ("up higher when outcome true" if (diff or 0) > 0 else
                      ("down higher when outcome true" if (diff or 0) < 0 else "none")),
    }


def family_analysis(df: pl.DataFrame, labeled: pl.DataFrame, cfg: dict,
                    rng: np.random.Generator, days: dict, tickets: dict, log=print,
                    n_sample: int = 0, edge_set: str = "primary",
                    want: tuple = ("state_matched", "same_day_minute", "minute_matched"),
                    want_timing: bool = True) -> dict:
    """Matched-pair computations for ONE family. Families are never pooled: this runs once per
    family and the results are reported side by side. The primary inference is the same-day,
    same-minute, same-family tier (both sides share the day); cross-day pairings use the dyadic
    (both-sides) multiplicative bootstrap."""
    fam = sorted(df["family"].unique().to_list())
    assert len(fam) == 1, f"family_analysis expects one family, got {fam}"
    out = {"cells": None, "tiers": {}}
    cells_use = cells_all = None
    if "state_matched" in want:
        units_cell_day = unit_aggregates(labeled, ["cell", "sleeve_day"])
        cells_all = sum_units(units_cell_day, ["cell"])
        usable = ((cells_all["cnt_up"] >= cfg["min_per_label"])
                  & (cells_all["cnt_dn"] >= cfg["min_per_label"])
                  & (cells_all["n_total"] >= cfg["min_cell_rows"]))
        cells_use = cells_all.filter(pl.Series(usable)).sort("cell")
        # censored census straight from the FULL frame: censored rows never enter `labeled`, so an
        # aggregate over `labeled` would be structurally zero
        cens_cells = (df.filter(pl.col("censored")).group_by("cell")
                      .agg(pl.len().alias("censored_rows")))
        cells_all = cells_all.join(cens_cells, on="cell", how="left").with_columns(
            pl.col("censored_rows").fill_null(0))
        cells_use = cells_use.join(cens_cells, on="cell", how="left").with_columns(
            pl.col("censored_rows").fill_null(0))
        cells_use = cells_use.with_columns(
            (pl.col("censored_rows") / (pl.col("n_total") + pl.col("censored_rows")))
            .alias("censored_share"))
        max_cens = cfg.get("max_cell_censored_share", 0.25)
        cens_share_vals = cells_use["censored_share"].to_numpy()
        cens_dist = {
            "n_usable_cells": int(cells_use.height),
            "p50": _np(float(np.percentile(cens_share_vals, 50))) if cells_use.height else None,
            "p90": _np(float(np.percentile(cens_share_vals, 90))) if cells_use.height else None,
            "max": _np(float(cens_share_vals.max())) if cells_use.height else None,
            "n_above_max_allowed": int((cens_share_vals > max_cens).sum()),
            "max_allowed": max_cens,
        }
        excluded_high = cells_use.filter(pl.col("censored_share") > max_cens)
        cells_use = cells_use.filter(pl.col("censored_share") <= max_cens)
        keys = cells_use["cell"].to_list()
        units_cell_day_u = units_cell_day.filter(
            pl.Series(units_cell_day["cell"].is_in(keys)))
        pairs_state = pair_frame(labeled, ["cell"], False).filter(~pl.col("same_member"))
        pairs_state = pairs_state.filter(pl.Series(pairs_state["cell"].is_in(keys)))
        pairs_state_distinct = (pair_frame(labeled, ["cell"], True)
                                .filter(~pl.col("same_member"))
                                .filter(pl.col("cell").is_in(keys)))
        log(f"  [{fam}] state cells {cells_all.height}->{cells_use.height}, pairs "
            f"{pairs_state.height}")
        cell_days_per_cell = units_cell_day.group_by("cell").agg(pl.len().alias("n_cell_days"))
        cells_use = cells_use.join(cell_days_per_cell, on="cell", how="left")
        out["tiers"]["state_matched"] = tier_report(
            units_cell_day_u, pairs_state, pairs_state_distinct, cfg, rng, days, tickets,
            ["cell"], mode="units", log=log, cell_level=cells_use, dyadic=True,
            primacy="secondary (state-matched cells; cross-day pairs use the dyadic bootstrap)",
            inference_note=("cell x day unit contrasts have both deciles on the same day; the "
                            "within-cell pair hit rate is cross-day and resamples both sides"))
        out["cells"] = {
            "edge_set": edge_set,
            "definition": ("cell = (et, family, ret_from_fill bucket, bars_below_entry_episode "
                           "bucket, dist_from_running_high bucket, reclaim_count bucket, "
                           "bar_index bucket)"),
            "bucket_edges": {k: {"edges": list(v[0]), "labels": list(v[1])}
                             for k, v in EDGE_SPECS[edge_set].items()},
            "bucket_rule": ("bin i holds edges[i-1] < value <= edges[i]; a null value gets its "
                            "own bucket and is reported in checks"),
            "n_cells_nonempty": int(cells_all.height), "n_cells_usable": int(cells_use.height),
            "rows_in_usable_cells": int(cells_use["n_total"].sum()),
            "rows_up": int(cells_use["cnt_up"].sum()),
            "rows_down": int(cells_use["cnt_dn"].sum()),
            "cell_days_in_usable_cells": int(units_cell_day_u.height),
            "rows_in_usable_cell_days": int(units_cell_day_u["n_total"].sum()),
            "censored_rows_in_cells": int(cells_all["censored_rows"].sum()),
            "censored_rows_usable_cells": int(cells_use["censored_rows"].sum()),
            "censored_rows_max_usable_cell": int(cells_use["censored_rows"].max())
            if cells_use.height else 0,
            "usable_cell_census": [
                {"cell": r["cell"], "n_total": int(r["n_total"]), "up": int(r["cnt_up"]),
                 "down": int(r["cnt_dn"]), "censored": int(r["censored_rows"]),
                 "censored_share": _np(float(r["censored_rows"])
                                       / (r["n_total"] + r["censored_rows"]))
                 if (r["n_total"] + r["censored_rows"]) else None,
                 "cell_days": int(r["n_cell_days"])}
                for r in cells_use.sort(["censored_rows", "n_total"], descending=[True, True])
                              .head(500).iter_rows(named=True)],
            "usability_rule": (f"cell has >= {cfg['min_per_label']} rows of EACH decile and >= "
                               f"{cfg['min_cell_rows']} rows in total"),
            "min_per_label": cfg["min_per_label"], "min_cell_rows": cfg["min_cell_rows"],
            "usability_censoring_filter": {
                "rule": ("usable state cells with censored_share > max_allowed are excluded from "
                         "the state-matched tier (they would otherwise contribute at full weight); "
                         "all usable cells keep their censored count and share in the census"),
                "censored_share_distribution_over_usable_cells": cens_dist,
                "excluded_cells": [{"cell": r["cell"], "n_total": int(r["n_total"]),
                                    "censored_rows": int(r["censored_rows"]),
                                    "censored_share": _np(float(r["censored_share"]))}
                                   for r in excluded_high.iter_rows(named=True)],
            },
            "sensitivity": []}
        for mpl, mcr in cfg.get("sensitivity", []):
            u = cells_all.filter(pl.Series((cells_all["cnt_up"] >= mpl)
                                           & (cells_all["cnt_dn"] >= mpl)
                                           & (cells_all["n_total"] >= mcr)))
            out["cells"]["sensitivity"].append({
                "min_per_label": mpl, "min_cell_rows": mcr, "n_cells": int(u.height),
                "rows": int(u["n_total"].sum()), "up_rows": int(u["cnt_up"].sum()),
                "down_rows": int(u["cnt_dn"].sum()),
                "censored_rows": int(u["censored_rows"].sum()),
                "pair_potential": int(np.minimum(u["cnt_up"].to_numpy(),
                                                 u["cnt_dn"].to_numpy()).sum())})
        if want_timing:
            out["divergence_timing"] = timing_report(df, labeled, pairs_state, cfg)
        if n_sample:
            out["sample_pairs"] = choose_sample(pairs_state, "cell", n_sample)
    if "same_day_minute" in want:
        units_minute_day = unit_aggregates(labeled, PRIMARY_UNIT)
        pairs_day = pair_frame(labeled, PRIMARY_UNIT, False).filter(~pl.col("same_member"))
        pairs_day = add_pair_outcomes(pairs_day)
        log(f"  [{fam}] primary units {units_minute_day.height}, pairs {pairs_day.height}")
        out["tiers"]["same_day_minute"] = tier_report(
            units_minute_day, pairs_day, pairs_day, cfg, rng, days, tickets, PRIMARY_UNIT,
            mode="units", log=log,
            primacy="PRIMARY (same day, same minute, same family, same tenure bucket)",
            inference_note=("each unit is one day's minute inside one bar_index (tenure) bucket: "
                            "both deciles share the day and the tenure bucket, so day clustering "
                            "is exact and the tenure confound is matched out"))
        # --- realized post-t outcomes: strictly future path facts, NOT decile identity, NOT close
        realized_defs = (
            ("up_side_new_high", "outcome_up_new_high",
             "up-side member made a new high after the match minute (final_high_flag_u == false)"),
            ("down_side_failure_path", "outcome_down_failure_path",
             "down-side member made no new high after t (final_high_flag_d == true) AND its best "
             "later CLOSE never regained next_open(t) (oc_close_d < 0) - path-based, no close"))
        out["realized_outcomes_reading_warning"] = (
            "These axes are VOLATILITY-SELECTED (max-threshold crossings of the decile labels) and "
            "must not be read as directional state: no realized separator here is trustworthy "
            "evidence that a discriminator tells continuation from decay.")
        out["realized_outcomes_note"] = (
            "the decile-pair executable comparison (v_forced_flat_u > v_forced_flat_d) was removed: "
            "it was label-implied (95.4% true) and tested a 4.6% minority; the middle-decile "
            "winner/loser axis below replaces it with real two-sided support")
        realized = {}
        for name, col, definition in realized_defs:
            realized[name] = {
                "definition": definition,
                "n_pairs_with_outcome": int(pairs_day[col].is_not_null().sum()),
                "n_outcome_true": int(pairs_day[col].cast(pl.Float64).sum()),
                "per_discriminator": {disc: realized_outcome_stats(pairs_day, disc, days, col,
                                                                   cfg["bootstrap"], rng)
                                      for disc in DISCRIMINATORS}}
        out["realized_outcomes_primary"] = realized
        # --- disjoint outcome cells (a partition, not a split)
        m_cont = pl.col("outcome_up_new_high") == True                       # noqa: E712
        m_fail = pl.col("outcome_down_failure_path") == True                 # noqa: E712
        cells_out = {}
        for tag, mask in (("both", m_cont & m_fail), ("continuation_only", m_cont & ~m_fail),
                          ("failure_only", ~m_cont & m_fail), ("neither", ~m_cont & ~m_fail)):
            sub = pairs_day.filter(mask.fill_null(False))
            cells_out[tag] = {
                "n_pairs": int(sub.height),
                "share_of_primary_pairs": _np(float(sub.height) / pairs_day.height)
                if pairs_day.height else None,
                "per_discriminator": {disc: pair_stats(sub, disc, days, tickets,
                                                       cfg["bootstrap"], rng, PRIMARY_UNIT)
                                      for disc in DISCRIMINATORS},
                "realized": {name: {disc: realized_outcome_stats(sub, disc, days, col,
                                                                 cfg["bootstrap"], rng)
                                    for disc in DISCRIMINATORS}
                             for name, col in (("up_side_new_high", "outcome_up_new_high"),
                                               ("executable_continuation",
                                                "outcome_exec_continuation"))}}
        out["outcome_cells_primary"] = {
            "definition": ("DISJOINT partition of the primary pairs (mutually exclusive cells): "
                           "continuation = up-side new high after t; failure = down-side no new "
                           "high after t and its best later close never regained next_open(t)"),
            "cells": cells_out}
        # --- conditioning: does a separation survive the state the decile label encodes?
        cond_subsets = {
            "all_primary_pairs": pairs_day,
            "both_no_new_high_5": pairs_day.filter((pl.col("new_high_count_5_u") == 0)
                                                   & (pl.col("new_high_count_5_d") == 0)),
            "both_bars_since_new_high_gt0": pairs_day.filter(
                (pl.col("bars_since_new_high_u") > 0) & (pl.col("bars_since_new_high_d") > 0)),
            "same_close_location_bucket": pairs_day.filter(
                (pl.col("b_clv_u") >= 0) & (pl.col("b_clv_u") == pl.col("b_clv_d"))),
        }
        # --- replacement executable axis: middle-decile winner/loser, two-sided support --------
        mid_cols_keep = list(dict.fromkeys(PRIMARY_UNIT + ["family", "entry_rank", "ticker", "et"]
                                           + [c for c in MIDDLE_AXIS_CARRY if c != "sleeve_day"]
                                           + list(DISCRIMINATORS)))
        mid = (df.filter(~pl.col("up") & ~pl.col("down") & ~pl.col("censored")
                         & ~pl.col("dup_cross_family"))
                 .select([c for c in mid_cols_keep if c in df.columns]))
        mid = mid.sort(PRIMARY_UNIT + ["family", "entry_rank", "ticker", "et"])
        mid = mid.with_columns((pl.col("et").cum_count().over(PRIMARY_UNIT) - 1).alias("kk"))
        mid_side = mid.with_columns(
            ((pl.col("kk") + 1) // 2).alias("idx"),
            (pl.col("kk") % 2 == 0).alias("side_a"))
        mid_cols = list(dict.fromkeys([c for c in MIDDLE_AXIS_CARRY + list(DISCRIMINATORS)
                                       if c in mid_side.columns]))
        mid_a = mid_side.filter(pl.col("side_a")).select(
            PRIMARY_UNIT + ["idx"] + [pl.col(c).alias(c + "_A") for c in mid_cols])
        mid_b = mid_side.filter(~pl.col("side_a")).select(
            PRIMARY_UNIT + ["idx"] + [pl.col(c).alias(c + "_B") for c in mid_cols])
        pairs_mid = (mid_a.join(mid_b, on=PRIMARY_UNIT + ["idx"], how="inner")
                     .filter(pl.col("member_A") != pl.col("member_B"))
                     .with_columns((pl.col("v_forced_flat_A") > pl.col("v_forced_flat_B"))
                                   .alias("outcome_exec_A_wins")))
        out["middle_decile_executable_axis"] = {
            "reading_warning": ("This axis exists to give the executable comparison two-sided "
                                "support; it is still a post-t outcome selected comparison and is "
                                "not a directional state claim."),
            "why": ("the decile-pair executable comparison was label-implied (95.4% true) and ran "
                    "on a 4.6% minority; this axis pairs MIDDLE-decile members at the same "
                    "day/minute/family/tenure bucket and lets the realized executable value pick "
                    "the winner, so both classes have real support"),
            "unit_scope": ("all (day, minute, family, tenure-bucket) units with at least two "
                           "middle-decile members; one pair per unit (the first two in "
                           "deterministic order), so the axis is not restricted to units that "
                           "contain an extreme-decile pair"),
            "definition": ("outcome = A-side executable hold-to-flat value exceeds the B-side "
                           "(v_forced_flat_A > v_forced_flat_B); sides come from a deterministic "
                           "split inside the unit and are blind to the discriminators"),
            "n_pairs": int(pairs_mid.height),
            "n_A_wins": int(pairs_mid["outcome_exec_A_wins"].sum()) if pairs_mid.height else 0,
            "share_A_wins": _np(float(pairs_mid["outcome_exec_A_wins"].mean()))
            if pairs_mid.height else None,
            "per_discriminator": {disc: realized_outcome_stats(
                pairs_mid, disc, days, "outcome_exec_A_wins", cfg["bootstrap"], rng,
                suf_a="A", suf_b="B") for disc in DISCRIMINATORS},
        }
        out["conditioning_primary"] = {
            "reading_warning": ("Conditioning was added because bar_range_pct's decile separation "
                                "is coupled to the max-threshold label construction; the result "
                                "(no collapse) does NOT make it a behavioral separator and the "
                                "close-location hypothesis is empirically dead."),
            "purpose": ("does a discriminator's separation survive conditioning on the state the "
                        "decile label itself encodes (a just-made new high) and on the "
                        "close-location-within-bar control?"),
            "subsets": {tag: {
                "n_pairs": int(sub.height),
                "per_discriminator": {disc: pair_stats(sub, disc, days, tickets,
                                                       cfg["bootstrap"], rng, PRIMARY_UNIT)
                                      for disc in DISCRIMINATORS},
                "realized_up_side_new_high": {disc: realized_outcome_stats(
                    sub, disc, days, "outcome_up_new_high", cfg["bootstrap"], rng)
                    for disc in DISCRIMINATORS},
            } for tag, sub in cond_subsets.items()}}
    if "minute_matched" in want:
        units_stratum = sum_units(unit_aggregates(labeled, ["et", "family", "sleeve_day"]),
                                  ["et", "family"])
        pairs_minute = pair_frame(labeled, ["stratum"], False).filter(~pl.col("same_member"))
        pairs_minute_distinct = pair_frame(labeled, ["stratum"], True).filter(
            ~pl.col("same_member"))
        log(f"  [{fam}] strata {units_stratum.height}, pairs {pairs_minute.height}")
        out["tiers"]["minute_matched"] = tier_report(
            units_stratum, pairs_minute, pairs_minute_distinct, cfg, rng, days, tickets,
            ["stratum"], mode="pairs", log=log, cell_level=units_stratum, dyadic=True,
            primacy="sensitivity (population, cross-day pairs)",
            inference_note=("pairs are aligned across days inside (et, family); the two-way "
                            "bootstrap resamples both sides"))
    return out


def _edge_compare(primary_disc: dict, alternate_disc: dict, alternate_cells) -> dict:
    """Edge-perturbation on the tier whose survivors are being judged: a survivor keeps its place
    only if it also clears the same verdict rule under the alternate edge set."""
    robust = [d for d in DISCRIMINATORS
              if primary_disc[d]["verdict"].startswith(("separates", "soft"))
              and alternate_disc[d]["verdict"].startswith(("separates", "soft"))]
    demoted = [d for d in DISCRIMINATORS
               if primary_disc[d]["verdict"].startswith(("separates", "soft"))
               and not alternate_disc[d]["verdict"].startswith(("separates", "soft"))]
    return {
        "alternate_cells": alternate_cells,
        "survivors_primary_edges": [d for d in DISCRIMINATORS
                                    if primary_disc[d]["verdict"].startswith(("separates", "soft"))],
        "survivors_alternate_edges": [d for d in DISCRIMINATORS
                                      if alternate_disc[d]["verdict"].startswith(("separates",
                                                                                  "soft"))],
        "edge_robust_survivors": robust,
        "demoted_by_edge_perturbation": demoted,
        "per_discriminator": {
            d: {"primary_verdict": primary_disc[d]["verdict"],
                "alternate_verdict": alternate_disc[d]["verdict"],
                "primary_hit": primary_disc[d]["pairs"].get("share_up_higher"),
                "primary_n": primary_disc[d]["pairs"].get("n_pairs"),
                "alternate_hit": alternate_disc[d]["pairs"].get("share_up_higher"),
                "alternate_n": alternate_disc[d]["pairs"].get("n_pairs"),
                "edge_robust": bool(primary_disc[d]["verdict"].startswith(("separates", "soft"))
                                    and alternate_disc[d]["verdict"].startswith(("separates",
                                                                                 "soft")))}
            for d in DISCRIMINATORS}}


def _oob_summary(tiers: dict) -> dict:
    """Survivor lists derive from the SINGLE verdict vocabulary, so a discriminator can never be
    both 'no separation' and 'separates out-of-block' in the same artifact."""
    oob = {}
    for tier_name, tier in tiers.items():
        seps = [d for d, r in tier["discriminators"].items()
                if r["verdict"].startswith("separates")]
        soft = [d for d, r in tier["discriminators"].items()
                if r["verdict"].startswith("soft")]
        transfer_only = [d for d, r in tier["discriminators"].items()
                         if r["sign_transfer_both_directions"]
                         and not r["verdict"].startswith(("separates", "soft"))]

        def _entry(d: str) -> str:
            e = tier["discriminators"][d]["separator_entry"]
            ci = e.get("ci95_day_clustered") or [None, None]
            return (f"{d}({e['sign']}, hit={e['hit_rate']:.3f} "
                    f"[{ci[0]:.3f},{ci[1]:.3f}], n={e['n_pairs']})"
                    if e.get("hit_rate") is not None and ci[0] is not None else f"{d}(n/a)")

        oob[tier_name] = {
            "primacy": tier.get("primacy"),
            "survivors": seps,
            "soft": soft,
            "sign_transfer_both_directions_but_not_separating": transfer_only,
            "decision": ("separates out-of-block: " + "; ".join(_entry(d) for d in seps)
                         if seps else
                         "no separator clears the full verdict rule (day-CI excluding 0.5 in both "
                         "blocks and out-of-block sign transfer)")}
    return oob


def analyse(df: pl.DataFrame, cfg: dict, log=print) -> dict:
    t0 = time.time()
    rng = np.random.default_rng(cfg["seed"])
    n_rows = df.height
    registry = cfg.get("registry") or {}
    families_map = registry.get("families", {})
    guard = guard_features(list(DISCRIMINATORS), families_map)
    match_guard = guard_features(list(CELL_DIMS), families_map,
                                 allowed=CAUSAL_FEATURE_FAMILIES + MATCHING_KEY_FAMILIES)
    if guard["violations"]:
        raise FeatureGuardError(f"causal feature guard failed: {guard['violations']}")
    if match_guard["violations"]:
        raise FeatureGuardError(f"matching-dimension guard failed: {match_guard['violations']}")
    df = prepare(df, cfg)
    checks = {"n_rows": int(n_rows), "causal_guard": guard, "matching_dim_guard": match_guard,
              "ci_containment": {}}
    checks["causal_guard"]["registry_basis"] = ("column_registry.json families; prefix rules are "
                                                "not sufficient in v2")
    checks["matching_dim_guard"]["note"] = ("cell dimensions may be state families or identity/"
                                            "tenure keys (et, family, bar_index)")
    checks["label_measure_columns"] = {c: families_map.get(c) for c in
                                       ("bar_close", "bar_high", "bar_low", "next_open",
                                        "remaining_run", "v_forced_flat", "terminal_censored",
                                        "future_member_last_et", "final_high_flag",
                                        "session_close_ret_from_entry")}
    checks["member_key_unique"] = bool(
        df.select(pl.struct(["sleeve_day", "family", "entry_rank"]).n_unique()).item()
        == df.select(pl.struct(MEMBER_KEYS).n_unique()).item())
    labels = label_counts(df)
    log(f"labels: up={labels['all']['divergent_up']} down={labels['all']['divergent_down']} "
        f"neither={labels['all']['neither']} no_future={labels['all']['rows_without_future']}")
    agree = {}
    for tag, a, b in (("up_close_vs_open", "up", "up_o"), ("up_close_vs_high", "up", "up_h"),
                      ("down_close_vs_open", "down", "down_o"),
                      ("down_close_vs_high", "down", "down_h")):
        agree[tag] = _np(float((df[a] == df[b]).mean()))
    strat = df.group_by(["et", "family"]).agg(pl.len().alias("n")).select(
        [pl.col("n").min().alias("min"), pl.col("n").median().alias("median"),
         pl.col("n").max().alias("max"), (pl.col("n") < cfg["min_stratum"]).sum().alias("below")])
    sm = strat.row(0, named=True)
    strata_info = {"n_strata": int(df.select(pl.struct(["et", "family"]).n_unique()).item()),
                   "min_rows": int(sm["min"]), "median_rows": float(sm["median"]),
                   "max_rows": int(sm["max"]),
                   "strata_below_min_stratum": int(sm["below"]),
                   "min_stratum": cfg["min_stratum"]}
    dup_rows = df.filter(pl.col("dup_cross_family"))
    dup_info = {"rows_excluded": int(dup_rows.height),
                "members_excluded": int(dup_rows.select(pl.struct(MEMBER_KEYS).n_unique()).item()),
                "day_tickers_in_both_families": int(
                    dup_rows.select(pl.struct(["sleeve_day", "ticker"]).n_unique()).item()),
                "per_block": {b: int(dup_rows.filter(pl.col("block") == b).height)
                              for b in ("block1", "block2")}}
    # recovery scans the full member tape (the future bars of a down row are rarely labelled)
    rec = recovery_stats(df, log)
    df = df.with_columns([
        pl.Series("bars_to_recovery", rec["bars_to_recovery"], dtype=pl.Float64),
        pl.Series("cost_before_recovery", rec["cost_before_recovery"], dtype=pl.Float64),
        pl.Series("bars_to_new_low", rec["bars_to_new_low"], dtype=pl.Float64),
        pl.Series("new_low_before_recovery", rec["new_low_before_recovery"], dtype=pl.Boolean),
        pl.Series("new_low_within_30_after_recovery", rec["new_low_within_30_after_recovery"],
                  dtype=pl.Boolean),
        pl.Series("recovered", rec["recovered"], dtype=pl.Boolean),
        pl.Series("bars_available", rec["bars_available"], dtype=pl.Float64),
        pl.Series("touch_bars_to_reclaim", rec["touch_bars_to_reclaim"], dtype=pl.Float64),
    ])
    df = add_cells(df, "primary")
    checks["null_buckets_primary_edges"] = {
        dim: int(df[f"b_{dim}"].eq(-1).sum()) for dim in CELL_DIMS}
    censored_members = int(df.filter(pl.col("censored"))
                           .select(pl.struct(MEMBER_KEYS).n_unique()).item())
    checks["censored"] = {
        "rows": int(df["censored"].sum()),
        "members": censored_members,
        "per_family": {fam: int(df.filter((pl.col("family") == fam)
                                          & pl.col("censored")).height)
                       for fam in sorted(df["family"].unique().to_list())},
        "v_forced_flat_null_on_censored": bool(
            df.filter(pl.col("censored"))["v_forced_flat"].is_null().all()),
        "future_member_last_et_equals_tape_end": bool(
            df.group_by(MEMBER_KEYS)
              .agg((pl.col("et").max() == pl.col("future_member_last_et").max()).all().alias("ok"))
              ["ok"].all()),
        "rule": ("terminal-censored rows are excluded from strata, labels, cells, tiers and "
                 "pairs; they are counted per cell; no null is ever treated as zero"),
        "no_null_as_zero": {
            "unscored_rows_have_null_recovery": bool(
                df.filter(pl.col("next_open").is_null())
                  .select(pl.col("bars_to_recovery").is_null().all().alias("ok"))["ok"].all()),
            "unit_counts_are_nonnull_counts": ("all unit and cell means use non-null counts "
                                               "(.count()), never filled zeros"),
        },
    }
    labeled = df.filter((pl.col("up") | pl.col("down")) & ~pl.col("dup_cross_family")
                        & ~pl.col("censored"))
    log(f"labeled rows after exclusions: {labeled.height} "
        f"(censored rows excluded: {checks['censored']['rows']})")
    prim_cols = ["oc_close", "oc_open", "oc_high", "remaining_run", "cost_of_waiting",
                 "bars_to_next_high", "dd_before_next_high", "bars_to_peak", "final_high_flag",
                 "bars_to_recovery", "cost_before_recovery", "v_forced_flat"]

    families = {}
    for fam in sorted(df["family"].unique().to_list()):
        df_f = df.filter(pl.col("family") == fam)
        labeled_f = labeled.filter(pl.col("family") == fam)
        days = {d: i for i, d in enumerate(sorted(df_f["sleeve_day"].unique().to_list()))}
        tickets = {m: i for i, m in enumerate(sorted(labeled_f["member"].unique().to_list()))}
        log(f"[{fam}] labeled rows: {labeled_f.height}, days: {len(days)}, tickets: {len(tickets)}")
        res = family_analysis(df_f, labeled_f, cfg, rng, days, tickets, log=log,
                              n_sample=cfg["sample_pairs"] // 2)
        res.pop("pairs_state", None)
        res["labels"] = labels.get(f"family_{fam}")
        res["censored_rows"] = int(df_f["censored"].sum())
        res["recovery"] = recovery_report(labeled_f)
        res["group_summaries"] = {
            "primitives": _group_summary(labeled_f, prim_cols),
            "discriminators": _group_summary(labeled_f, list(DISCRIMINATORS))}
        res["out_of_block"] = _oob_summary(res["tiers"])
        # edge perturbation: rebuild the coarse state under the documented alternate edges and
        # re-run the state-matched tier for this family only
        d2 = add_cells(df_f, "alternate")
        l2 = d2.filter((pl.col("up") | pl.col("down")) & ~pl.col("dup_cross_family"))
        t2 = family_analysis(d2, l2, cfg, rng, days, tickets, log=log, edge_set="alternate",
                             want=("state_matched", "same_day_minute"), want_timing=False)
        res["edge_sensitivity"] = {
            "primary_edges": {k: list(v) for k, v in PRIMARY_EDGES.items()},
            "alternate_edge_definition": {k: list(v) for k, v in ALTERNATE_EDGES.items()},
            "state_matched_alternate_edges": _edge_compare(
                res["tiers"]["state_matched"]["discriminators"],
                t2["tiers"]["state_matched"]["discriminators"], t2["cells"]),
            "primary_tier_alternate_edges": _edge_compare(
                res["tiers"]["same_day_minute"]["discriminators"],
                t2["tiers"]["same_day_minute"]["discriminators"], t2["cells"])}
        for tier_key, edge_info in (
                ("state_matched", res["edge_sensitivity"]["state_matched_alternate_edges"]),
                ("same_day_minute", res["edge_sensitivity"]["primary_tier_alternate_edges"])):
            ob = res["out_of_block"][tier_key]
            ob["edge_robust_survivors"] = [d for d in ob["survivors"]
                                           if edge_info["per_discriminator"][d]["edge_robust"]]
            ob["edge_demoted_survivors"] = [d for d in ob["survivors"]
                                            if not edge_info["per_discriminator"][d]["edge_robust"]]
            ob["reading_warning"] = ("decile-split statistics only: volatility-selected, not a "
                                     "directional claim (see top-level auditor_conclusion)")
            if ob["edge_demoted_survivors"]:
                ob["decision"] += (" | DEMOTED (edge-sensitive, do not promote): "
                                   + ", ".join(ob["edge_demoted_survivors"]))
        families[fam] = res

    # family interaction: the primary (same-day) tier, side by side
    family_interaction = {}
    for disc in DISCRIMINATORS:
        row = {}
        for fam, res in families.items():
            r = res["tiers"]["same_day_minute"]["discriminators"][disc]
            row[fam] = {"direction": r["direction"],
                        "pair_share_up_higher": r["pairs"].get("share_up_higher"),
                        "pair_ci95": r["pairs"].get("ci95_day_clustered"),
                        "verdict": r["verdict"],
                        "sign_transfer_both_directions": r["sign_transfer_both_directions"],
                        "prior_met": r["prior_met"]}
        dirs = {v["direction"] for v in row.values() if v}
        row["families_agree_direction"] = bool(len(dirs) == 1 and "none" not in dirs)
        row["sign_transfers_in_all_families"] = bool(
            all(v["sign_transfer_both_directions"] for v in row.values()
                if isinstance(v, dict)))
        row["separates_in_all_families"] = bool(
            all(v["verdict"].startswith(("separates", "soft")) for v in row.values()
                if isinstance(v, dict)))
        family_interaction[disc] = row
    headline = {fam: {disc: {"verdict": res["tiers"]["same_day_minute"]["discriminators"][disc]
                             ["verdict"],
                             "verdict_state_matched": res["tiers"]["state_matched"]
                             ["discriminators"][disc]["verdict"],
                             "pair_share_up_higher": res["tiers"]["same_day_minute"]
                             ["discriminators"][disc]["pairs"].get("share_up_higher")}
                      for disc in DISCRIMINATORS} for fam, res in families.items()}
    auditor_support = {}
    for fam, res in families.items():
        rl = res.get("realized_outcomes_primary", {})
        br = rl.get("up_side_new_high", {}).get("per_discriminator", {}).get("bar_range_pct", {})
        cond = res.get("conditioning_primary", {}).get("subsets", {})
        auditor_support[fam] = {
            "bar_range_pct_realized_up_side_new_high": {
                "difference_true_minus_false": br.get("difference_true_minus_false"),
                "ci95_day_clustered": br.get("difference_ci95_day_clustered"),
                "concordance_hit_rate": br.get("concordance_hit_rate")},
            "bar_range_pct_decile_hit_all": cond.get("all_primary_pairs", {})
            .get("per_discriminator", {}).get("bar_range_pct", {}).get("share_up_higher"),
            "bar_range_pct_decile_hit_no_fresh_high": cond.get("both_no_new_high_5", {})
            .get("per_discriminator", {}).get("bar_range_pct", {}).get("share_up_higher"),
            "bar_range_pct_decile_hit_same_close_location": cond.get("same_close_location_bucket", {})
            .get("per_discriminator", {}).get("bar_range_pct", {}).get("share_up_higher"),
            "middle_decile_executable_axis": {
                "n_pairs": res.get("middle_decile_executable_axis", {}).get("n_pairs"),
                "share_A_wins": res.get("middle_decile_executable_axis", {}).get("share_A_wins")},
        }
    auditor_conclusion = {
        "verdict": ("AUDITOR CONCLUSION, recorded prominently: the realized-outcome axes are "
                    "volatility-selected (max-threshold crossings), NO realized separator here is "
                    "trustworthy, bar_range_pct shows no realized separation on up_side_new_high, "
                    "and the close-location hypothesis is empirically dead. No section of this "
                    "artifact may be read as a directional claim about state at the match minute; "
                    "the decile split and everything derived from it is a volatility/attention "
                    "axis, not a continuation-versus-decay signal."),
        "supporting_numbers": auditor_support,
        "auditor_quotes": {
            "bar_range_pct_up_side_new_high": "+0.101, CI [-0.064, +0.217] (no realized separation)",
            "close_location_hypothesis": "decile separation 0.708 -> 0.711 / 0.710 under new-high "
                                         "conditioning and 0.693 under same-close-location: dead",
        },
        "reading_rules": [
            "survivor lists are decile-split statistics (volatility-selected), not directional ones",
            "realized-outcome concordances are descriptive and multiplicity-uncontrolled",
            "conditioning results do not repair the label-construction coupling",
            "no policy, gate or directional rule may be derived from these sections",
        ],
    }
    ci_violations = []
    for fam, res in families.items():
        grouped = [("realized_outcomes_primary." + n, r["per_discriminator"])
                   for n, r in res.get("realized_outcomes_primary", {}).items()]
        grouped.append(("middle_decile_executable_axis",
                        res.get("middle_decile_executable_axis", {}).get("per_discriminator", {})))
        for name, per in grouped:
            for disc, d in per.items():
                ci = d.get("concordance_ci95_day_clustered") or [None, None]
                pt = d.get("concordance_hit_rate")
                if ci[0] is not None and pt is not None and not (ci[0] <= pt <= ci[1]):
                    ci_violations.append(f"{fam}.{name}.{disc}.concordance")
                ci2 = d.get("difference_ci95_day_clustered") or [None, None]
                pt2 = d.get("difference_true_minus_false")
                if ci2[0] is not None and pt2 is not None and not (ci2[0] <= pt2 <= ci2[1]):
                    ci_violations.append(f"{fam}.{name}.{disc}.difference")
    checks["ci_containment"]["violations"] = ci_violations
    checks["ci_containment"]["rule"] = ("the reported point must be the same estimator as the "
                                        "day-clustered bootstrap and must lie inside its own CI")
    payload = {
        "auditor_conclusion": auditor_conclusion,
        "artifact": str(DEFAULT_OUT.relative_to(ROOT)),
        "tool": "factory/scripts/basket_atlas_pairs.py",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "panel": {"path": cfg.get("panel_path"), "sha256": cfg.get("panel_sha256"),
                  "rows": int(n_rows),
                  "members": cfg.get("panel_meta", {}).get("members"),
                  "days": cfg.get("panel_meta", {}).get("days"),
                  "schema_version": cfg.get("panel_meta", {}).get("schema_version"),
                  "declared_sha256": cfg.get("panel_meta", {}).get("panel_sha256_declared")},
        "config": {
            "seed": cfg["seed"], "bootstrap_draws": cfg["bootstrap"],
            "min_stratum": cfg["min_stratum"], "min_per_label": cfg["min_per_label"],
            "min_cell_rows": cfg["min_cell_rows"], "sample_pairs": cfg["sample_pairs"],
            "multiplicity": {
                "primary_tests": ("per family: 3 tiers x 14 discriminators = 42 verdict tests, "
                                  "plus 3 realized-outcome x 14, 4 disjoint outcome cells x 14, "
                                  "4 conditioning subsets x 14, and 14 edge-perturbation tests"),
                "note": ("no multiple-comparison control is applied; the day-clustered CIs are "
                         "descriptive and a separator should be required to hold across families, "
                         "across the edge perturbation and against realized outcomes before it is "
                         "promoted")},
            "families": ("families are NEVER pooled for inference: every tier estimate, "
                         "separator list and shadow hold happens inside one family; only "
                         "descriptive counts (label counts, censoring, duplicates) span families"),
            "primary_unit": ("(sleeve_day, et, family, bar_index bucket): same day, same minute, "
                             "same family AND comparable tenure"),
            "primary_inference": ("the same-day, same-minute, same-family, same-tenure-bucket "
                                  "tier is the ONLY primary inference (both sides share the day "
                                  "and the tenure bucket); the state-matched tier is secondary "
                                  "and its cross-day pairs use the dyadic bootstrap; the "
                                  "population minute-matched tier is a sensitivity analysis"),
            "dyadic_bootstrap": ("cross-day pairings use a multiplicative two-way bootstrap: the "
                                 "pair weight is the product of its two sides' Poisson "
                                 "multiplicities (day x day, and ticket x ticket)"),
            "edge_perturbation": ("the state-matched tier is re-run under a documented alternate "
                                  "edge set; survivors that do not transfer under both edge sets "
                                  "are edge-sensitive and are demoted"),
            "clustering": ("day is the conservative resampling unit; ticket-clustered CIs are the "
                           "secondary unit; minute rows are not independent"),
            "pairing": ("pairs are formed by listing the two deciles' rows inside the matching "
                        "unit in deterministic order (sleeve_day, family, entry_rank, ticker, et) "
                        "and aligning i-th with i-th; same-member pairs are dropped"),
            "out_of_block": ("the decision statistic is out-of-block: the sign estimated in one "
                             "block is applied to the other block's pairs; both directions are "
                             "reported"),
            "no_eod_semantics": ("labels are within-minute, within-family quantiles of the oracle "
                                 "future path (max later close / next_open); no session-close "
                                 "outcome and no absolute return threshold is used; exact clock "
                                 "and family matching, comparable tenure via bar_index buckets"),
            "panel_v2_conventions": {
                "terminal_censoring": ("terminal_censored rows have no terminal value and no "
                                       "executable terminal liquidation: they are excluded from "
                                       "strata, labels, cells, tiers and pairs, counted per cell, "
                                       "and never treated as null-as-zero"),
                "executable_baseline": ("v_forced_flat is the executable hold-to-flat baseline and "
                                       "is reported as the value reference in the primitives"),
                "close_reference_anatomy": ("v_hold_flat is close-reference anatomy only and is "
                                            "not read by this analysis"),
                "giveback_precedence": ("give-back conditions first appearing at "
                                        "et >= session_end - 1 are non-firing (the engine's "
                                        "forced flat has clock precedence and the value is "
                                        "v_forced_flat); rows on a member's last tracked row and "
                                        "rows without next_open are unscored"),
                "future_only": ("future_member_last_et, future_forced_flat_px, the session_* "
                                "constants and the censor flags are future-only and are never "
                                "used as features"),
                "causal_guard": ("features are checked against column_registry.json families "
                                 "(state_* only); prefix rules are not sufficient in v2"),
            },
            "recovery_primary": ("the primary timely/executable-recovery evidence is the "
                                 "one-minute discrete survival curve of close-based recovery "
                                 "(right-censored at each tape end) and the joint surface of "
                                 "recovery timing x deepest drawdown before it; the 5/15/30-bar "
                                 "shares and the 15-minute clock table are explicitly labelled "
                                 "compact reference rulers only"),
            "verdict_rule": ("'separates out-of-block' = the day-clustered CI of the unit "
                             "difference and of the pair hit rate both exclude their null, both "
                             "blocks share the sign, and both out-of-block transfers have a "
                             "day-clustered CI above 0.5; 'soft' = sign transfers out-of-block and "
                             "the pooled day-CI excludes 0; 'in-block only' = pooled separation "
                             "without out-of-block transfer; 'flips' = block signs disagree; else "
                             "'no separation'")},
        "labels": {
            "definition": {
                "oc_close": "max over later bars u > t of bar_close(u) / next_open(t) - 1 "
                            "(primary; exit price = a later bar's close)",
                "oc_open": "max over later bars u > t of next_open(u) / next_open(t) - 1 "
                           "(exit at the next open — the panel's own execution convention)",
                "oc_high": "max over later bars u > t of bar_high(u) / next_open(t) - 1",
                "quantile_rule": ("within each (et, family) stratum with at least "
                                  f"{cfg['min_stratum']} rows: divergent_up = oc_close >= q90, "
                                  "divergent_down = oc_close <= q10"),
                "selector": "oc_close",
                "status": ("DISCOVERY LABELS ONLY. The decile tails of the oracle future path "
                           "select sharply divergent matched cases for inspection; they are NOT a "
                           "policy target, NOT an executable information value, and no dollar "
                           "ledger is built on them. The deliverable is whether causal "
                           "contemporaneous state separates the cases out-of-block.")},
            "agreement_with_alternative_exit_prices": agree,
            "counts": labels, "strata": strata_info,
            "exclusions_cross_family_duplicates": dup_info},
        "families": families,
        "family_interaction": family_interaction,
        "headline": headline,
        "checks": checks,
        "runtime_seconds": round(time.time() - t0, 1)}
    for fam, res in families.items():
        res["headline_note"] = ("headline verdicts are decile-split statistics; DISCOVERY-ONLY and "
                                "volatility-selected - see auditor_conclusion")
    payload["deterministic_core_sha256"] = core_hash(payload)
    return payload


# --------------------------------------------------------------------------- #
# self-test on a synthetic tape with hand-checkable answers
# --------------------------------------------------------------------------- #
def synth_frame(rows: list, needed: list) -> pl.DataFrame:
    defaults = {"sleeve_day": "2021-03-01", "ticker": "T", "family": "A_pm", "entry_rank": 1,
                "et": 600, "block": "block1", "session_end": 959, "entry_px": 10.0,
                "next_open": 10.0, "bar_high": 10.0, "bar_low": 10.0, "bar_close": 10.0,
                "running_high": 10.0, "bar_index": 0, "remaining_run": 0.0,
                "cost_of_waiting": 0.0, "bars_to_next_high": None, "dd_before_next_high": None,
                "final_high_flag": False, "bars_since_gap": 0, "gap_count_so_far": 0,
                "bars_to_peak": 1, "terminal_censored": False, "session_close_ret_from_entry": 0.0,
                "path_complete_to_session_end": True, "future_member_last_et": 959,
                "v_forced_flat": 0.0,
                "ret_from_fill": 0.0, "bars_below_entry_episode": 0,
                "dist_from_running_high": -0.001, "reclaim_count": 0}
    for d in DISCRIMINATORS:
        defaults.setdefault(d, 0.0)
    rows = [dict(defaults, **r) for r in rows]
    return pl.DataFrame({c: [r[c] for r in rows] for c in needed})


def self_test() -> dict:
    needed = required_columns()
    rows = []
    # Two families, ten members each at et=600 with crafted forward paths. Member i's best later
    # close is 10 * (1 + (i - 5 + off) / 100); polars quantile interpolation is "nearest", so the
    # q10/q90 deciles hold two members each (i=0,1 down; i=8,9 up) in every family. Later bars sit
    # at unique ets so only et=600 forms a stratum above min_stratum.
    for fam, off in (("A_pm", 0.0), ("B600", 0.5)):
        for i in range(10):
            best = 10.0 * (1.0 + (i - 5 + off) / 100.0)
            low = 9.0 if i == 0 else min(9.5, best)
            high1 = 9.5 if i == 0 else best
            term_low = 9.7 if i == 1 else 9.0
            fhf = i in (0, 1)   # the low-path members never set another high; the high ones do
            rows += [
                dict(ticker=f"{fam[:2]}{i:02d}", family=fam, entry_rank=1 + (i % 3), et=600,
                     final_high_flag=fhf,
                     bar_index=30, next_open=10.0, bar_high=10.0, bar_low=9.9, bar_close=10.0),
                dict(ticker=f"{fam[:2]}{i:02d}", family=fam, entry_rank=1 + (i % 3),
                     et=700 + i * 10, bar_index=31, next_open=best, bar_high=high1, bar_low=low,
                     bar_close=best, final_high_flag=fhf),
                dict(ticker=f"{fam[:2]}{i:02d}", family=fam, entry_rank=1 + (i % 3),
                     et=701 + i * 10, bar_index=32, next_open=None, bar_high=9.0, bar_low=term_low,
                     bar_close=9.0, final_high_flag=fhf),
            ]
    # a terminal-censored member: its tape ends early and it must never enter a stratum, a label,
    # a cell, a pair or a tier - only the censored counts
    for et in (600, 640, 641):
        rows.append(dict(ticker="CEN00", family="A_pm", entry_rank=3, et=et, bar_index=30,
                         next_open=None, bar_high=9.0, bar_low=8.5, bar_close=8.8,
                         terminal_censored=True, path_complete_to_session_end=False,
                         v_forced_flat=None, remaining_run=None, cost_of_waiting=None,
                         bars_to_next_high=None, dd_before_next_high=None, bars_to_peak=None,
                         final_high_flag=None))
    df = synth_frame(rows, needed)
    df = df.with_columns(pl.col("et").max().over(
        ["sleeve_day", "family", "entry_rank", "ticker"]).alias("future_member_last_et"))
    registry = {"families": {c: "state_dynamics" for c in DISCRIMINATORS}}
    registry["families"].update({c: "state_path" for c in CELL_DIMS})
    registry["families"]["bar_index"] = "key"
    registry["families"].update({c: "outcome_path" for c in
                                 ("bar_close", "bar_high", "bar_low", "next_open", "remaining_run",
                                  "bars_to_next_high", "dd_before_next_high", "bars_to_peak",
                                  "final_high_flag")})
    registry["families"].update({"v_forced_flat": "outcome_continuation",
                                 "terminal_censored": "censor",
                                 "future_member_last_et": "future_meta"})
    cfg = {"seed": 11, "bootstrap": 200, "min_stratum": 4, "min_per_label": 1,
           "min_cell_rows": 2, "sample_pairs": 4, "max_cell_censored_share": 0.25,
           "sensitivity": [], "panel_path": "<synthetic>", "panel_sha256": "0" * 64,
           "registry": registry, "panel_meta": {}}
    payload = analyse(df, cfg, log=lambda *a: None)
    assert set(payload["families"]) == {"A_pm", "B600"}, list(payload["families"])
    assert payload["checks"]["censored"]["rows"] == 3, payload["checks"]["censored"]
    assert payload["checks"]["censored"]["members"] == 1, payload["checks"]["censored"]
    assert payload["checks"]["censored"]["v_forced_flat_null_on_censored"] is True
    assert payload["checks"]["censored"]["future_member_last_et_equals_tape_end"] is True
    assert payload["checks"]["causal_guard"]["violations"] == [], payload["checks"]["causal_guard"]
    assert payload["checks"]["matching_dim_guard"]["violations"] == [], \
        payload["checks"]["matching_dim_guard"]
    assert payload["checks"]["matching_dim_guard"]["families"]["bar_index"] == "key"
    guard_bad = guard_features(["v_forced_flat"], registry["families"])
    assert guard_bad["violations"], guard_bad
    guard_bad2 = guard_features(["level_ret"], registry["families"])
    assert guard_bad2["violations"], guard_bad2
    for fam, fres in payload["families"].items():
        counts = fres["labels"]
        assert counts["divergent_up"] == 2 and counts["divergent_down"] == 2, (fam, counts)
        if fam == "A_pm":
            assert fres["censored_rows"] == 3, (fam, fres["censored_rows"])
        assert counts["both_deciles"] == 0, (fam, counts)
        down = fres["recovery"]["down"]
        assert down["n"] == 2 and down["share_never_recovered"] == 1.0, (fam, down)
        dd = down["drawdown_distribution_when_never_recovered"]
        assert dd["n"] == 2 and dd["counts"][5] == 1 and sum(dd["counts"][9:11]) == 1, (fam, dd)
        assert "bars_to_recovery_survival_primary" in down, (fam, down)
        assert "joint_path_bars_to_recovery_x_drawdown_before_recovery" in down, (fam, down)
        assert all(v == 0.0 for v in down["reference_rulers"]
                   ["share_within_bars_and_drawdown"].values()), (fam, down)
        t_a = fres["tiers"]["state_matched"]
        assert t_a["n_units_with_both_deciles"] == 1 and t_a["n_pairs"] == 2, (fam, t_a)
        assert t_a["inference"]["pair_clustering"].startswith("dyadic"), (fam, t_a["inference"])
        assert t_a["tenure_difference_bars"]["share_identical"] == 1.0, (fam, t_a)
        for disc, rep in t_a["discriminators"].items():
            assert rep["units"].get("n_units", 0) == 1, (fam, disc, rep["units"])
            assert rep["pairs"].get("n_pairs", 0) == 2, (fam, disc, rep["pairs"])
            assert rep["pairs"].get("share_up_higher") == 0.5, (fam, disc, rep["pairs"])
        t_b = fres["tiers"]["same_day_minute"]
        assert t_b["n_units_with_both_deciles"] == 1, (fam, t_b)
        assert t_b["primacy"].startswith("PRIMARY"), (fam, t_b["primacy"])
        assert t_b["inference"]["pair_clustering"].startswith("one-sided"), (fam, t_b["inference"])
        assert len(fres["sample_pairs"]) == 1, (fam, fres["sample_pairs"])
        assert "edge_sensitivity" in fres, fam
        assert len(fres["edge_sensitivity"]["state_matched_alternate_edges"]
                   ["per_discriminator"]) == len(DISCRIMINATORS), fam
        assert fres["out_of_block"]["same_day_minute"]["decision"], fam
        rl = fres["realized_outcomes_primary"]
        assert set(rl) == {"up_side_new_high", "down_side_failure_path"}, (fam, list(rl))
        assert "removed" in fres["realized_outcomes_note"], (fam, fres["realized_outcomes_note"])
        for name, r in rl.items():
            assert set(r["per_discriminator"]) == set(DISCRIMINATORS), (fam, name)
            for disc, d in r["per_discriminator"].items():
                assert "concordance_hit_rate" in d and "difference_ci95_day_clustered" in d, \
                    (fam, name, disc, d)
        for name, r in rl.items():
            for disc, d in r["per_discriminator"].items():
                ci = d.get("concordance_ci95_day_clustered") or [None, None]
                if ci[0] is not None and d.get("concordance_hit_rate") is not None:
                    assert ci[0] <= d["concordance_hit_rate"] <= ci[1], (fam, name, disc, d)
                ci2 = d.get("difference_ci95_day_clustered") or [None, None]
                if ci2[0] is not None and d.get("difference_true_minus_false") is not None:
                    assert ci2[0] <= d["difference_true_minus_false"] <= ci2[1], \
                        (fam, name, disc, d)
        mid_axis = fres["middle_decile_executable_axis"]
        assert "per_discriminator" in mid_axis and "why" in mid_axis, (fam, mid_axis)
        for disc, d in mid_axis["per_discriminator"].items():
            ci = d.get("concordance_ci95_day_clustered") or [None, None]
            if ci[0] is not None and d.get("concordance_hit_rate") is not None:
                assert ci[0] <= d["concordance_hit_rate"] <= ci[1], (fam, disc, d)
        oc = fres["outcome_cells_primary"]["cells"]
        assert set(oc) == {"both", "continuation_only", "failure_only", "neither"}, (fam, list(oc))
        total = sum(v["n_pairs"] for v in oc.values())
        assert total == fres["tiers"]["same_day_minute"]["n_pairs"], (fam, total)
        for tag, cell in oc.items():
            assert set(cell["per_discriminator"]) == set(DISCRIMINATORS), (fam, tag)
        cond = fres["conditioning_primary"]["subsets"]
        assert set(cond) == {"all_primary_pairs", "both_no_new_high_5",
                             "both_bars_since_new_high_gt0", "same_close_location_bucket"}, (fam,)
        assert fres["tiers"]["same_day_minute"]["primacy"].startswith("PRIMARY"), (fam,)
        assert fres["tiers"]["state_matched"]["primacy"].startswith("secondary"), (fam,)
        for disc, r in fres["tiers"]["same_day_minute"]["discriminators"].items():
            assert "separator_entry" in r and "verdict" in r, (fam, disc)
            assert "sign_transfer_both_directions" in r, (fam, disc)
        assert "edge_robust_survivors" in fres["out_of_block"]["same_day_minute"], fam
        if fam == "A_pm":
            assert rl["up_side_new_high"]["n_outcome_true"] == 2, (fam, rl["up_side_new_high"])
            assert rl["down_side_failure_path"]["n_outcome_true"] == 2, \
                (fam, rl["down_side_failure_path"])
            assert fres["cells"]["censored_rows_usable_cells"] == 1, \
                (fam, fres["cells"]["censored_rows_usable_cells"],
                 fres["cells"]["usable_cell_census"])
            entry = [e for e in fres["cells"]["usable_cell_census"] if e["censored"] == 1]
            assert entry and abs(entry[0]["censored_share"] - 1.0 / 5.0) < 1e-12, entry
    assert set(payload["family_interaction"]) == set(DISCRIMINATORS)
    assert payload["checks"]["ci_containment"]["violations"] == [], \
        payload["checks"]["ci_containment"]
    assert payload["auditor_conclusion"]["verdict"].startswith("AUDITOR CONCLUSION")
    # differential check of the vectorised joint surface against a brute-force recomputation
    rng = np.random.default_rng(0)
    b = rng.integers(1, 50, size=200).astype(np.float64)
    b[::7] = np.nan
    c = -rng.random(200) * 0.2
    js = _joint_surface(b, c, 200, list(range(1, 21)), np.array([0.0, -0.01, -0.05, -0.1]))
    for j, k in enumerate(js["k_values"]):
        for i, cut in enumerate(js["drawdown_cuts"]):
            expect = float(np.mean(np.isfinite(b) & (b <= k) & (c >= cut)))
            assert abs(js["share"][j][i] - expect) < 1e-12, (j, i, js["share"][j][i], expect)
    # differential check of the dyadic bootstrap weights (both sides resampled)
    hit = np.array([1.0, 0.0, 0.5, 1.0])
    cu = np.array([0, 1, 0, 1], dtype=np.int64)
    cd = np.array([1, 0, 1, 0], dtype=np.int64)
    boot = _dyadic_bootstrap(hit, cu, cd, 2, 50, np.random.default_rng(3))
    finite = boot[np.isfinite(boot)]
    assert finite.size > 0 and finite.min() >= 0 and finite.max() <= 1, boot
    p2 = analyse(df, cfg, log=lambda *a: None)
    assert p2["deterministic_core_sha256"] == payload["deterministic_core_sha256"]
    return {"ok": True,
            "checks": ["oracle oc_close + within-minute deciles", "family separation (no pooling)",
                       "registry causal guard (v2 families)",
                       "terminal censoring excluded + real per-cell censored census",
                       "realized outcomes + disjoint outcome cells + conditioning",
                       "CI point containment + middle-decile executable axis",
                       "disjoint deciles", "never-recovered recovery + drawdown histogram",
                       "state-cell pairing", "same-day primary tier", "dyadic bootstrap",
                       "edge perturbation present", "joint surface vs brute force",
                       "tenure identity", "sample", "determinism"]}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _fmt(x):
    return "None" if x is None else f"{x:.4g}"


def print_summary(payload: dict) -> None:
    lab = payload["labels"]["counts"]["all"]
    print(f"labels: up={lab['divergent_up']} ({lab['up_share']:.4f}) "
          f"down={lab['divergent_down']} ({lab['down_share']:.4f}) "
          f"no_future={lab['rows_without_future']} "
          f"xfamily_dups_excluded={payload['labels']['exclusions_cross_family_duplicates']['rows_excluded']}")
    print(f"panel: schema v{payload['panel'].get('schema_version')} "
          f"sha {str(payload['panel'].get('sha256'))[:12]}… | censored rows "
          f"{payload['checks'].get('censored', {}).get('rows')} in "
          f"{payload['checks'].get('censored', {}).get('members')} members (excluded)")
    for fam, res in payload["families"].items():
        cells = res["cells"]
        rec = res["recovery"]["down"]
        surv = rec["bars_to_recovery_survival_primary"]
        print(f"[{fam}] censored_rows={res.get('censored_rows')} | cells: "
              f"{cells['n_cells_nonempty']} non-empty -> {cells['n_cells_usable']} "
              f"usable, {cells['rows_in_usable_cells']} rows, "
              f"{cells['cell_days_in_usable_cells']} cell-days | recovery(down): "
              f"never_recovered={_fmt(rec['share_never_recovered'])} "
              f"median_bars={_fmt((rec['bars_to_recovery_distribution_among_recovered'] or {}).get('median'))} "
              f"survival_points={len(surv.get('k', []))}")
        print(f"   timing: {res['divergence_timing']['verdict']}")
        for tier_name in ("same_day_minute", "state_matched", "minute_matched"):
            rep = res["tiers"][tier_name]
            print(f"   -- {tier_name} [{rep.get('primacy')}] pairs={rep.get('n_pairs')} "
                  f"units={rep.get('n_units_with_both_deciles')}")
            for disc, r in rep["discriminators"].items():
                ci = r["pairs"].get("ci95_day_clustered") or [None, None]
                print(f"      {disc:<28} {r['direction']:<11} "
                      f"diff={_fmt(r['units'].get('diff_weighted'))} "
                      f"pair={_fmt(r['pairs'].get('share_up_higher'))} "
                      f"CIpair=[{_fmt(ci[0])},{_fmt(ci[1])}] "
                      f"b1={_fmt(r['block1']['share_up_higher'])} "
                      f"b2={_fmt(r['block2']['share_up_higher'])} "
                      f"[{r['verdict']}]")
        print("   out_of_block: " + " | ".join(
            f"{k}: {v['decision'][:90]}" for k, v in res["out_of_block"].items()))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--panel", default=str(DEFAULT_PANEL))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--seed", type=int, default=20260925)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--min-stratum", type=int, default=20)
    ap.add_argument("--min-per-label", type=int, default=5)
    ap.add_argument("--min-cell-rows", type=int, default=10)
    ap.add_argument("--sample", type=int, default=50)
    ap.add_argument("--max-days", type=int, default=0,
                    help="debug aid: keep only the first N sleeve_days (0 = all)")
    ap.add_argument("--allow-panel-drift", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        print(json.dumps(self_test(), indent=2))
        return 0
    panel = Path(args.panel)
    if not panel.exists():
        print(f"panel not found: {panel}", file=sys.stderr)
        return 2
    sha = sha256_file(panel)
    if not args.allow_panel_drift and sha != EXPECTED_PANEL_SHA256:
        print(f"panel sha256 mismatch: {sha}", file=sys.stderr)
        return 2
    registry = load_registry(panel)
    meta = load_panel_meta(panel)
    if meta.get("panel_sha256_declared") and meta["panel_sha256_declared"] != sha:
        print(f"panel sha256 differs from coverage.json declared: {meta['panel_sha256_declared']}",
              file=sys.stderr)
        return 2
    if meta.get("rows") is not None and int(meta["rows"]) != EXPECTED_PANEL_ROWS:
        print(f"panel rows {meta['rows']} != {EXPECTED_PANEL_ROWS}", file=sys.stderr)
        return 2
    if meta.get("members") is not None and int(meta["members"]) != EXPECTED_PANEL_MEMBERS:
        print(f"panel members {meta['members']} != {EXPECTED_PANEL_MEMBERS}", file=sys.stderr)
        return 2
    df = pl.read_parquet(panel, columns=required_columns())
    if args.max_days:
        keep = sorted(df["sleeve_day"].unique().to_list())[:args.max_days]
        df = df.filter(pl.col("sleeve_day").is_in(keep))
        print(f"[--max-days] kept {len(keep)} days, {df.height} rows")
    cfg = {"seed": args.seed, "bootstrap": args.bootstrap, "min_stratum": args.min_stratum,
           "min_per_label": args.min_per_label, "min_cell_rows": args.min_cell_rows,
           "sample_pairs": args.sample,
           "sensitivity": [(3, 6), (10, 20)], "panel_path": str(panel), "panel_sha256": sha,
           "registry": registry, "panel_meta": meta}
    payload = analyse(df, cfg)
    out = Path(args.out)
    if args.verify:
        if not out.exists():
            print(f"artifact missing: {out}", file=sys.stderr)
            return 2
        old = json.loads(out.read_text())
        same = (old.get("deterministic_core_sha256") == core_hash(old)
                == payload["deterministic_core_sha256"])
        print(f"verify: artifact core hash {'MATCHES' if same else 'DIFFERS'}")
        print_summary(payload)
        return 0 if same else 1
    _write_json(out, payload)
    written = json.loads(out.read_text())
    ok = written.get("deterministic_core_sha256") == core_hash(written)
    print_summary(written)
    print(f"wrote {out} (core sha256 {written['deterministic_core_sha256'][:12]}…, "
          f"self-consistent={ok}, {payload['runtime_seconds']}s)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
