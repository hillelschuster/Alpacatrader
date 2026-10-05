#!/usr/bin/env python3
"""Per-CLOCK episode dollars for claims the roster already owns (discovery, not a policy).

The push/leg producer (``owned_claim_push_legs.py``) pools the four admission clocks
inside every headline, which conceals the only contrast that matters causally: a claim
admitted at 09:00 (clock 540) is admitted hours before a claim admitted at 09:31 (clock
571), and both are then scored on the same "first +5% push" ruler.  This producer
re-reads that staged evidence day by day (no raw tape, no re-derivation of the episode
tape) and reports the continuation ladder **per clock**, per ownership universe, never
pooled across clocks.

What it produces, all from the staged claims/legs parquet:

  * signed continuation profiles at 1/3/5/10/15/30/45/60/90/120 ET minutes after the
    causal episode minute, for every (N3|N5) x clock x {clean, damaged, all} cell of the
    first causal +5% push, and for every (N3|N5) x clock x {immediate extension, leg after
    an observed pullback} cell of the later-leg episodes;
  * BOTH money channels, separated on purpose:
      - ``inherited`` = the original owned share count from the original equal-dollar
        claim budget.  The original entry fee is SUNK (it is already inside that fixed
        share count) and is never charged again, so only the exit fee is re-priced:
        ``dollars_f = (bank + inc_stage) * (1-side)/(1-STAGE_SIDE)``, i.e. at the staged
        100bps round trip it equals the staged dollars exactly and at 150bps it is an
        exact algebraic rescale of them;
      - ``fresh`` = a NEW $100 of CASH deployed at that episode's own executable entry open
        P0.  This is a LEVEL against the cash put in, so BOTH fees are charged: with
        F = (1-side)/(1+side), shares = 100/(P0*(1+side)) and net cash change =
        100 * (P/P0) * F - 100 for executable exit open P.  Since
        ``inc_stage / bank_value == (P - P0) / P0`` holds exactly in the staged dollars,
        ``dollars_f = 100 * inc/bank_value * F - 100 * (1 - F)``.  A DIFFERENCE between two
        exits on the same P0 cancels the entry fee and keeps the plain
        ``100 * gap/bank_value * F`` form.  These are per EXECUTED $100 at the modelled
        size: not sizing, not a capital plan, not an order.  Later-leg episodes carry no
        stored entry open, so their fresh channel is UNSUPPORTED (null, counted), never
        imputed;
  * profit surrender (bank at the push vs declared horizons vs hold-to-terminal, plus the
    ex-post "best declared horizon" and the retrospective max, carried strictly as
    unreachable rulers) and lifetime exposure (minutes above basis, minutes alive before
    -5% damage, minutes to repair, declared holding window, dollars x minutes);
  * the causal coordinates that exist in the stage, kept as cells and never re-tuned:
    admission clock/group, rank, above-basis path flags, volume-flow expansion, owned
    damage history, and attention timing (push time-of-day / before-13:00, leg
    separation);
  * day-balanced means, day-clustered standard errors and a deterministic day bootstrap,
    with support (known / UNKNOWN) counts beside every statistic;
  * pairwise clock contrasts (09:31 against each premarket clock) paired BY DAY - the
    clock contrast a pooled view cannot show.

Rulers are inherited verbatim from the stage (first causal +5%, a +3% ret5 crossing, a
genuine -5% drawdown as damage, the repair rule, and the "a later leg needs an observed
damage minute in between" separation rule).  Nothing here is a policy: no best-horizon
selection per claim, no tuned threshold, no exit rule, no winner class.  Leg episodes are
OVERLAPPING rulers on claims that are already counted in the first-push cells, so their
overlap is measured and reported rather than assumed away.  UNKNOWN is always null plus a
count, never zero.

How to read it: every number here is the average of ONE declared ruler, conditioned on
nothing observed after the episode minute.  A negative mean at a horizon is not a proof
that no adaptive management could have done better, and a CI that excludes zero among
hundreds of scored cells is a hypothesis rather than a finding.  No exit rule, sizing
rule or clock rule is derived from this artifact.

CLI:
  python factory/scripts/owned_claim_clock_windows.py \
      --data-root data/harvest01 --out factory/artifacts/owned_claim_clock_windows.json
  [--stage-root <push_legs_stage dir>] [--stage <this producer's stage dir>]
  [--limit N]   # bounded smoke: the artifact is stamped smoke_non_evidence
  [--boot N] [--seed N]

Only discovery days are ever opened: the requested set must be a subset of the canonical
533-day discovery list AND must equal the staged producer's own discovery manifest, so a
protected/validation day cannot be read through this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path

os.environ.setdefault("POLARS_MAX_THREADS", "2")
import lifecycle_study as ls  # noqa: E402  (sibling producer module, same directory)
import numpy as np  # noqa: E402
import polars as pl  # noqa: E402

# --- declared rulers -----------------------------------------------------------------
# The staged increments are produced at a modelled 50bps per side (100bps round trip); the
# stage manifest states that fee basis in words and it is asserted below.
STAGE_SIDE = 0.005
# Round-trip friction ladder: 100bps (identity with the stage) and 150bps.
FRICTIONS_BPS = (100, 150)
# Inherited dollars: sunk entry fee inside the share count, exit fee re-priced.
K_FEE = {bps: (1.0 - bps / 2e4) / (1.0 - STAGE_SIDE) for bps in FRICTIONS_BPS}
# A fresh buyer pays BOTH sides on new cash.  For a LEVEL (net cash change against the
# cash it put in) both fees must be charged:
#   shares  = 100 / (P0 * (1 + side))
#   net     = shares * P * (1 - side) - 100 = 100 * (P / P0) * F - 100,  F = FRESH_FEE
#            = 100 * F * inc / bank_value - 100 * (1 - F)
# where P0 is the episode's executable entry open, P the executable exit open and
# inc / bank_value == (P - P0) / P0 holds exactly in the staged dollars.  A DIFFERENCE
# between two exits (value_h - flat) cancels the entry fee and keeps the plain
# 100 * F * gap / bank_value form; only LEVEL quantities carry the entry charge.
FRESH_FEE = {bps: (1.0 - bps / 2e4) / (1.0 + bps / 2e4) for bps in FRICTIONS_BPS}
FRESH_BUDGET = 100.0
FRESH_ENTRY_CHARGE = {bps: FRESH_BUDGET * (1.0 - FRESH_FEE[bps]) for bps in FRICTIONS_BPS}

HORIZONS = (1, 3, 5, 10, 15, 30, 45, 60, 90, 120)
CONTEXT_H = (15, 30, 60, 120)
CONTEXT_DIMS = ("push_before13", "push_tod", "flow", "history", "rank")
PARTITIONS = ("clean", "damaged", "all")
LEG_CLASSES = (("immediate_extension", False), ("after_pullback_leg", True))
UNIVERSES = (("n3", "n3"), ("n5", "n5"))
BOOT_DEFAULT = 1000
SEED_DEFAULT = 17
DAMAGE = -0.05

CLAIM_BASE = [
    "day",
    "clock",
    "rank",
    "ticker",
    "n3",
    "n5",
    "admission_group",
    "fill_et",
    "fill_px",
    "session_end",
    "has_push",
    "known_open",
    "push_t",
    "push_et",
    "push_px",
    "push_ret",
    "push_tod",
    "push_before13",
    "prior_damage",
    "prior_damage_count",
    "min_dd_before",
    "clean",
    "flow_expanding",
    "above_push_minutes",
    "above_push_censored",
    "alive_minutes",
    "alive_censored",
    "max_dd_after",
    "repaired_after",
    "minutes_to_repair",
    "legs_total",
    "legs_immediate",
    "legs_after_pullback",
    "bank_value",
    "flat_value",
    "flat_minus_bank",
    "best_horizon_adv",
    "retro_minus_bank",
    "retro_minus_flat",
]
LEG_BASE = [
    "day",
    "clock",
    "rank",
    "ticker",
    "n3",
    "n5",
    "leg_t",
    "leg_ret",
    "before13",
    "after_pullback",
    "separation_min",
    "sep_min_dd",
    "new_high",
    "leg_known",
]
CLAIM_HORIZON_COLS = [
    f"{stem}_{h}" for h in HORIZONS for stem in ("known", "inc", "dd", "above", "repair")
]
LEG_HORIZON_COLS = [f"leg_inc_{h}" for h in HORIZONS]


# --------------------------------------------------------------------------- helpers
def finite(x) -> bool:
    return x is not None and math.isfinite(float(x))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _read_frame(
    path: Path,
    cols: list[str],
    what: str,
    cache: dict[str, pl.DataType],
    missing_log: list,
) -> pl.DataFrame:
    """Read the requested columns from a staged day.

    A staged day on which NO claim ever printed a +5% push is written by the sibling
    producer with a narrower frame (the push columns simply do not exist).  Those columns
    are added back as typed NULLs - UNKNOWN, never zero - and the day is counted, so a
    support difference is visible instead of being an error or a silent zero.
    """
    schema = pl.read_parquet_schema(path)
    have = [c for c in cols if c in schema]
    frame = pl.read_parquet(path, columns=have)
    missing = [c for c in cols if c not in schema]
    if missing:
        unknown = [c for c in missing if c not in cache]
        if unknown:
            raise SystemExit(
                f"{path}: staged {what} lacks columns {unknown[:8]} and their dtypes were never "
                "observed; refusing to guess a UNKNOWN dtype"
            )
        for col in missing:
            frame = frame.with_columns(pl.lit(None, dtype=cache[col]).alias(col))
        missing_log.append({"day": path.stem.split(".")[0], "what": what, "columns": missing})
    for col in have:
        cache.setdefault(col, frame.schema[col])
    return frame.select(cols)


def _write_parquet(path: Path, frame: pl.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.parquet")
    frame.write_parquet(tmp)
    tmp.replace(path)


def _f64(frame: pl.DataFrame, col: str) -> np.ndarray:
    """Float64 column as numpy, NaN standing in for UNKNOWN (never 0)."""
    return frame[col].fill_null(float("nan")).to_numpy().astype(float)


def _days(frame: pl.DataFrame) -> np.ndarray:
    return frame["day"].to_numpy()


def _count_known(values: np.ndarray) -> tuple[int, int]:
    k = int(np.isfinite(values).sum())
    return k, int(values.size) - k


# --------------------------------------------------------------------------- statistics
def _stats(days: np.ndarray, vals: np.ndarray, boot: int, seed: int) -> dict:
    """Day-balanced block for one cell: day-clustered SE + deterministic day bootstrap."""
    vals = np.asarray(vals, dtype=float)
    known = np.isfinite(vals)
    n_unknown = int((~known).sum())
    d = np.asarray(days)[known]
    v = vals[known]
    if v.size == 0:
        return {
            "n_ep": 0,
            "n_unknown": n_unknown,
            "n_days": 0,
            "mean_day": None,
            "se_day": None,
            "ci_lo": None,
            "ci_hi": None,
            "median_day": None,
            "mean_ep": None,
            "median_ep": None,
            "share_pos_ep": None,
            "share_pos_day": None,
        }
    uniq, inv = np.unique(d, return_inverse=True)
    cnt = np.bincount(inv).astype(float)
    means = np.bincount(inv, weights=v) / cnt
    share_pos = np.bincount(inv, weights=(v > 0).astype(float)) / cnt
    out = {
        "n_ep": int(v.size),
        "n_unknown": n_unknown,
        "n_days": int(uniq.size),
        "mean_day": float(means.mean()),
        "se_day": float(means.std(ddof=1) / math.sqrt(means.size)) if means.size > 1 else None,
        "median_day": float(np.median(means)),
        "mean_ep": float(v.mean()),
        "median_ep": float(np.median(v)),
        "share_pos_ep": float((v > 0).mean()),
        "share_pos_day": float(share_pos.mean()),
    }
    if boot > 0 and uniq.size > 1:
        rng = np.random.default_rng(seed)
        idx = rng.integers(0, means.size, size=(boot, means.size))
        bs = means[idx].mean(axis=1)
        out["ci_lo"] = float(np.percentile(bs, 2.5))
        out["ci_hi"] = float(np.percentile(bs, 97.5))
    else:
        out["ci_lo"] = None
        out["ci_hi"] = None
    return out


def _flag_block(frame: pl.DataFrame, col: str, days: np.ndarray) -> dict:
    """Share of a path flag among the episodes where that flag is KNOWN (else UNKNOWN)."""
    raw = frame[col]
    n_unknown = int(raw.null_count())
    if frame.height == n_unknown:
        return {
            "n_known": 0,
            "n_unknown": n_unknown,
            "share_all_known": None,
            "share_day_balanced": None,
        }
    filled = raw.fill_null(False).to_numpy().astype(bool)
    known = raw.is_not_null().to_numpy().astype(bool)
    _, inv = np.unique(np.asarray(days)[known], return_inverse=True)
    cnt = np.bincount(inv).astype(float)
    share = np.bincount(inv, weights=filled[known].astype(float)) / cnt
    return {
        "n_known": int(known.sum()),
        "n_unknown": n_unknown,
        "share_all_known": float(filled[known].mean()),
        "share_day_balanced": float(share.mean()),
    }


def _dist(vals: np.ndarray) -> dict:
    v = np.asarray(vals, dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return {"n": 0, "mean": None, "median": None, "p10": None, "p90": None}
    return {
        "n": int(v.size),
        "mean": float(v.mean()),
        "median": float(np.median(v)),
        "p10": float(np.percentile(v, 10)),
        "p90": float(np.percentile(v, 90)),
    }


def _contrast(days_a, vals_a, days_b, vals_b, boot: int, seed: int) -> dict:
    """Paired-by-day difference (A - B) between two clock cohorts of the same cell."""

    def daymeans(days, vals):
        vals = np.asarray(vals, dtype=float)
        keep = np.isfinite(vals)
        d = np.asarray(days)[keep]
        v = vals[keep]
        if v.size == 0:
            return {}, 0
        uniq, inv = np.unique(d, return_inverse=True)
        cnt = np.bincount(inv).astype(float)
        means = np.bincount(inv, weights=v) / cnt
        return dict(zip(uniq.tolist(), means.tolist(), strict=True)), int(v.size)

    ma, na = daymeans(days_a, vals_a)
    mb, nb = daymeans(days_b, vals_b)
    shared = sorted(set(ma) & set(mb))
    out = {
        "n_ep_a": na,
        "n_ep_b": nb,
        "n_days_a": len(ma),
        "n_days_b": len(mb),
        "n_days_paired": len(shared),
        "mean_day_a": None,
        "mean_day_b": None,
        "mean_diff_day": None,
        "se_diff_day": None,
        "ci_lo": None,
        "ci_hi": None,
        "share_days_diff_positive": None,
    }
    if not shared:
        return out
    da = np.array([ma[s] for s in shared], dtype=float)
    db = np.array([mb[s] for s in shared], dtype=float)
    diff = da - db
    out["mean_day_a"] = float(da.mean())
    out["mean_day_b"] = float(db.mean())
    out["mean_diff_day"] = float(diff.mean())
    out["share_days_diff_positive"] = float((diff > 0).mean())
    if len(shared) > 1:
        out["se_diff_day"] = float(diff.std(ddof=1) / math.sqrt(diff.size))
    if boot > 0 and len(shared) > 1:
        rng = np.random.default_rng(seed)
        idx = rng.integers(0, diff.size, size=(boot, diff.size))
        bs = diff[idx].mean(axis=1)
        out["ci_lo"] = float(np.percentile(bs, 2.5))
        out["ci_hi"] = float(np.percentile(bs, 97.5))
    return out


# --------------------------------------------------------------------------- staging
def _derive_channels(frame: pl.DataFrame, is_leg: bool) -> pl.DataFrame:
    """Attach the friction-ladder money channels to one staged increment column.

    ``inc_{bps}``     inherited dollars of the increment on the ORIGINAL budget's fixed
                      share count: the original entry fee is sunk inside that share count
                      and only the exit fee is re-priced, so at the staged 100bps this is
                      the staged number itself and at 150bps it is (bank + inc_stage) times
                      (1-side)/(1-0.005).
    ``value_{bps}``   inherited dollars of the whole position at that horizon,
                      (bank_value + inc_stage) rescaled by the same factor.
    ``fresh_{bps}``   LEVEL net cash change of a NEW $100 deployed at the episode's own
                      executable entry open P0 and sold at the executable exit open P:
                      100 * (P/P0) * (1-side)/(1+side) - 100, i.e. both the buy fee on the
                      fresh cash and the exit fee are charged.  Since inc/bank == (P-P0)/P0
                      exactly, that is 100 * inc/bank * F - 100 * (1 - F).
    """
    out = frame
    for bps in FRICTIONS_BPS:
        k = K_FEE[bps]
        out = out.with_columns((pl.col("inc_stage") * k).alias(f"inc_{bps}"))
        if is_leg:
            out = out.with_columns(
                pl.lit(None, dtype=pl.Float64).alias(f"value_{bps}")
            ).with_columns(pl.lit(None, dtype=pl.Float64).alias(f"fresh_{bps}"))
            continue
        out = out.with_columns(
            ((pl.col("bank_value") + pl.col("inc_stage")) * k).alias(f"value_{bps}")
        ).with_columns(
            pl.when(pl.col("bank_value") > 0)
            .then(
                pl.lit(FRESH_BUDGET) * pl.col("inc_stage") / pl.col("bank_value") * FRESH_FEE[bps]
                - pl.lit(FRESH_ENTRY_CHARGE[bps])
            )
            .otherwise(None)
            .alias(f"fresh_{bps}")
        )
    return out


def stage_day(
    day: str,
    cpath: Path,
    lpath: Path,
    cache: dict[str, pl.DataType],
    missing_log: list,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, dict]:
    claims = _read_frame(
        cpath, CLAIM_BASE + CLAIM_HORIZON_COLS, "claims.parquet", cache, missing_log
    )
    legs = _read_frame(lpath, LEG_BASE + LEG_HORIZON_COLS, "legs.parquet", cache, missing_log)

    profiles = claims
    if profiles.height:
        profiles = profiles.with_columns(
            pl.when(pl.col("push_et").is_not_null() & pl.col("session_end").is_not_null())
            .then(pl.col("session_end") - pl.col("push_et"))
            .otherwise(None)
            .cast(pl.Int64)
            .alias("hold_window_minutes")
        )

    if claims.height:
        pushed = claims.filter(pl.col("has_push"))
        frames = [
            pushed.select(
                [
                    "day",
                    "clock",
                    "rank",
                    "ticker",
                    "n3",
                    "n5",
                    "admission_group",
                    "fill_et",
                    "fill_px",
                    "session_end",
                    "known_open",
                    "push_t",
                    "push_et",
                    "push_px",
                    "push_ret",
                    "push_tod",
                    "push_before13",
                    "prior_damage",
                    "prior_damage_count",
                    "clean",
                    "flow_expanding",
                    "bank_value",
                    "flat_value",
                    pl.lit(h).alias("horizon"),
                    pl.col(f"known_{h}").alias("known"),
                    pl.col(f"inc_{h}").alias("inc_stage"),
                    pl.col(f"dd_{h}").alias("dd"),
                    pl.col(f"above_{h}").alias("above"),
                    pl.col(f"repair_{h}").alias("repair"),
                ]
            )
            for h in HORIZONS
        ]
        ce = _derive_channels(pl.concat(frames, how="diagonal_relaxed"), is_leg=False).with_columns(
            (pl.col("dd") <= DAMAGE).alias("damaged")
        )
    else:
        ce = pl.DataFrame(
            {
                "day": pl.Series([], dtype=pl.Utf8),
                "clock": pl.Series([], dtype=pl.Int64),
                "horizon": pl.Series([], dtype=pl.Int64),
                "inc_stage": pl.Series([], dtype=pl.Float64),
            }
        )

    if legs.height:
        keys = claims.select(["clock", "rank", "ticker", "clean", "push_t", "session_end"]).rename(
            {"session_end": "claim_session_end"}
        )
        joined = legs.join(keys, on=["clock", "rank", "ticker"], how="left")
        frames = [
            joined.select(
                [
                    "day",
                    "clock",
                    "rank",
                    "ticker",
                    "n3",
                    "n5",
                    "leg_t",
                    "leg_ret",
                    "before13",
                    "after_pullback",
                    "separation_min",
                    "sep_min_dd",
                    "new_high",
                    "leg_known",
                    "clean",
                    "push_t",
                    "claim_session_end",
                    pl.lit(h).alias("horizon"),
                    pl.col(f"leg_inc_{h}").alias("inc_stage"),
                ]
            )
            for h in HORIZONS
        ]
        le = _derive_channels(pl.concat(frames, how="diagonal_relaxed"), is_leg=True)
    else:
        le = pl.DataFrame(
            {
                "day": pl.Series([], dtype=pl.Utf8),
                "clock": pl.Series([], dtype=pl.Int64),
                "horizon": pl.Series([], dtype=pl.Int64),
                "inc_stage": pl.Series([], dtype=pl.Float64),
            }
        )

    checks = {
        "claims": int(claims.height),
        "claims_with_push": int(claims.filter(pl.col("has_push")).height) if claims.height else 0,
        "claims_push_open_known": int(
            claims.filter(pl.col("has_push") & pl.col("known_open")).height
        )
        if claims.height
        else 0,
        "claims_no_push": int(claims.filter(~pl.col("has_push")).height) if claims.height else 0,
        "legs": int(legs.height),
        "legs_immediate": int(legs.filter(~pl.col("after_pullback")).height) if legs.height else 0,
        "legs_after_pullback": int(legs.filter(pl.col("after_pullback")).height)
        if legs.height
        else 0,
        "legs_without_claim": int(le.filter(pl.col("clean").is_null()).height // len(HORIZONS))
        if le.height
        else 0,
        "episode_rows_claims": int(ce.height),
        "episode_rows_legs": int(le.height),
        "first_push_open_known_but_horizon_increment_UNKNOWN": int(
            ce.filter(pl.col("known") & pl.col("inc_stage").is_null()).height
        )
        if ce.height
        else 0,
        "leg_entry_open_known_but_horizon_increment_UNKNOWN": int(
            le.filter(pl.col("leg_known") & pl.col("inc_stage").is_null()).height
        )
        if le.height
        else 0,
        "chronology_violations": int(
            ce.filter(pl.col("known") & (pl.col("push_et") >= pl.col("session_end"))).height
        )
        if ce.height
        else 0,
        "clocks_seen": sorted(claims["clock"].unique().to_list()) if claims.height else [],
    }
    return profiles, ce, le, checks


# --------------------------------------------------------------------------- grouping
def _group(frame: pl.DataFrame, keys: list[str]) -> dict:
    out: dict = {}
    if not frame.height:
        return out
    for key, sub in frame.partition_by(keys, as_dict=True).items():
        k = key if isinstance(key, tuple) else (key,)
        out[tuple(k)] = sub
    return out


def _frames_for(groups: dict, clock: int, part: str, flag: bool | None = None) -> pl.DataFrame:
    keys = [(clock, True), (clock, False)] if part == "all" else [(clock, part == "clean")]
    if flag is not None:
        keys = [(c, f, flag) for c, f in keys]
    got = [groups[k] for k in keys if k in groups and groups[k].height]
    if not got:
        return None
    return got[0] if len(got) == 1 else pl.concat(got, how="diagonal_relaxed")


def _level(frame: pl.DataFrame, dim: str) -> np.ndarray:
    if dim == "push_before13":
        return np.where(
            frame["push_before13"].fill_null(False).to_numpy().astype(bool),
            "before13",
            "at_or_after13",
        )
    if dim == "push_tod":
        return np.asarray(frame["push_tod"].fill_null("unknown").to_list(), dtype=object)
    if dim == "flow":
        return np.where(
            frame["flow_expanding"].fill_null(False).to_numpy().astype(bool),
            "expanding",
            "not_expanding",
        )
    if dim == "history":
        cnt = frame["prior_damage_count"].fill_null(-1).to_numpy().astype(float)
        return np.asarray(
            np.where(cnt < 0, "unknown", np.where(cnt == 0, "0", np.where(cnt == 1, "1", "2+"))),
            dtype=object,
        )
    if dim == "rank":
        return np.asarray([str(x) for x in frame["rank"].to_list()], dtype=object)
    raise KeyError(dim)


# --------------------------------------------------------------------------- finalize
def finalize(stage: Path, days: list[str], lineage: dict, boot: int, seed: int, smoke: bool):
    profiles = pl.read_parquet(stage / "claim_profiles.parquet")
    ce = pl.read_parquet(stage / "claim_horizons.parquet")
    le = pl.read_parquet(stage / "leg_horizons.parquet")
    clocks = sorted(set(lineage["clocks"]) | set(ce["clock"].unique().to_list()))

    push_profiles = profiles.filter(pl.col("has_push"))
    prof_groups = _group(push_profiles, ["clock", "clean"])
    ce_groups = _group(ce, ["clock", "clean"]) if ce.height else {}
    le_groups = _group(le, ["clock", "clean", "after_pullback"]) if le.height else {}
    leg_one = le.filter(pl.col("horizon") == 1) if le.height else le
    flat_lookup = profiles.select(["day", "clock", "rank", "ticker", "flat_value"])

    # ---- census -------------------------------------------------------------------
    census = {
        "days": len(days),
        "claims_all": int(profiles.height),
        "claims_with_push": int(push_profiles.height),
        "claims_no_push": int(profiles.height - push_profiles.height),
        "claims_push_open_known": int(profiles.filter(pl.col("known_open")).height),
        "claims_clean": int(push_profiles.filter(pl.col("clean")).height),
        "claims_damaged": int(push_profiles.filter(~pl.col("clean")).height),
        "episode_rows_claims": int(ce.height),
        "episode_rows_legs": int(le.height),
        "legs": int(leg_one.height) if leg_one.height else 0,
        "legs_immediate": int(leg_one.filter(~pl.col("after_pullback")).height)
        if leg_one.height
        else 0,
        "legs_after_pullback": int(leg_one.filter(pl.col("after_pullback")).height)
        if leg_one.height
        else 0,
        "per_clock": {},
    }
    for clock in clocks:
        cp = push_profiles.filter(pl.col("clock") == clock)
        cl = leg_one.filter(pl.col("clock") == clock) if leg_one.height else leg_one
        groups_seen = cp["admission_group"].drop_nulls().unique().to_list()
        census["per_clock"][str(clock)] = {
            "claims_with_push": int(cp.height),
            "claims_push_open_known": int(cp.filter(pl.col("known_open")).height),
            "clean": int(cp.filter(pl.col("clean")).height),
            "legs": int(cl.height) if cl.height else 0,
            "legs_immediate": int(cl.filter(~pl.col("after_pullback")).height) if cl.height else 0,
            "legs_after_pullback": int(cl.filter(pl.col("after_pullback")).height)
            if cl.height
            else 0,
            "admission_groups": sorted(groups_seen),
        }

    # ---- first-push continuation, per clock ---------------------------------------
    first_push = []
    for uname, ucol in UNIVERSES:
        for clock in clocks:
            for part in PARTITIONS:
                cohort = _frames_for(prof_groups, clock, part)
                cohort = cohort.filter(pl.col(ucol)) if cohort is not None else None
                cell_frame = _frames_for(ce_groups, clock, part)
                cell_frame = cell_frame.filter(pl.col(ucol)) if cell_frame is not None else None
                base = {
                    "universe": uname,
                    "clock": int(clock),
                    "partition": part,
                    "n_push_episodes": int(cohort.height) if cohort is not None else 0,
                    "n_push_open_known": int(cohort.filter(pl.col("known_open")).height)
                    if cohort is not None and cohort.height
                    else 0,
                    "curve": [],
                }
                if cell_frame is None or not cell_frame.height:
                    base["curve"] = [
                        {
                            "horizon": h,
                            "n_known": 0,
                            "n_unknown": 0,
                            "inherited": {},
                            "inherited_value": {},
                            "fresh": {},
                            "path": {},
                        }
                        for h in HORIZONS
                    ]
                    first_push.append(base)
                    continue
                for h in HORIZONS:
                    rows_h = cell_frame.filter(pl.col("horizon") == h)
                    if not rows_h.height:
                        base["curve"].append(
                            {
                                "horizon": h,
                                "n_known": 0,
                                "n_unknown": 0,
                                "inherited": {},
                                "inherited_value": {},
                                "fresh": {},
                                "path": {},
                            }
                        )
                        continue
                    days_arr = _days(rows_h)
                    known, unknown = _count_known(_f64(rows_h, "inc_stage"))
                    cell = {
                        "horizon": h,
                        "n_known": known,
                        "n_unknown": unknown,
                        "inherited": {},
                        "inherited_value": {},
                        "fresh": {},
                        "path": {
                            "above_push": _flag_block(rows_h, "above", days_arr),
                            "repairing": _flag_block(rows_h, "repair", days_arr),
                            "damaged": _flag_block(rows_h, "damaged", days_arr),
                        },
                    }
                    for bps in FRICTIONS_BPS:
                        cell["inherited"][str(bps)] = _stats(
                            days_arr, _f64(rows_h, f"inc_{bps}"), boot, seed
                        )
                        cell["inherited_value"][str(bps)] = _stats(
                            days_arr, _f64(rows_h, f"value_{bps}"), boot, seed
                        )
                        cell["fresh"][str(bps)] = _stats(
                            days_arr, _f64(rows_h, f"fresh_{bps}"), boot, seed
                        )
                    base["curve"].append(cell)
                first_push.append(base)

    # ---- later-leg episodes, per clock --------------------------------------------
    leg_curves = []
    for uname, ucol in UNIVERSES:
        for clock in clocks:
            for label, flag in LEG_CLASSES:
                sub = _frames_for(le_groups, clock, "all", flag=flag)
                sub = sub.filter(pl.col(ucol)) if sub is not None else None
                base = {
                    "universe": uname,
                    "clock": int(clock),
                    "class": label,
                    "legs": int(sub.filter(pl.col("horizon") == 1).height)
                    if sub is not None and sub.height
                    else 0,
                    "fresh_channel_supported": False,
                    "fresh_channel_reason": (
                        "a staged leg row carries the episode increment in original budget "
                        "dollars but not the episode's own entry open, so fresh-entry dollars "
                        "would need a price the stage never stored: reported UNKNOWN (null) and "
                        "counted, never imputed"
                    ),
                    "curve": [],
                }
                if sub is None or not sub.height:
                    base["curve"] = [
                        {"horizon": h, "n_known": 0, "inherited": {}, "fresh": {}} for h in HORIZONS
                    ]
                    leg_curves.append(base)
                    continue
                for h in HORIZONS:
                    rows_h = sub.filter(pl.col("horizon") == h)
                    if not rows_h.height:
                        base["curve"].append(
                            {
                                "horizon": h,
                                "n_known": 0,
                                "n_unknown": 0,
                                "inherited": {},
                                "fresh": {},
                                "overlaps_first_push_window": None,
                            }
                        )
                        continue
                    days_arr = _days(rows_h)
                    known, unknown = _count_known(_f64(rows_h, "inc_stage"))
                    leg_t = rows_h["leg_t"].to_numpy().astype(float)
                    push_t = rows_h["push_t"].to_numpy().astype(float)
                    overlap = None
                    ok = np.isfinite(leg_t) & np.isfinite(push_t)
                    if ok.any():
                        # the leg's [leg_t, leg_t+h] observation window against the
                        # first-push [push_t, push_t+h] window on the same claim
                        ov = (leg_t[ok] <= push_t[ok] + h) & (leg_t[ok] + h >= push_t[ok])
                        overlap = {
                            "n_known": int(ok.sum()),
                            "n_unknown": int((~ok).sum()),
                            "share_all_known": float(ov.mean()),
                        }
                    base["curve"].append(
                        {
                            "horizon": h,
                            "n_known": known,
                            "n_unknown": unknown,
                            "inherited": {
                                str(b): _stats(days_arr, _f64(rows_h, f"inc_{b}"), boot, seed)
                                for b in FRICTIONS_BPS
                            },
                            "fresh": {
                                str(b): _stats(days_arr, _f64(rows_h, f"fresh_{b}"), 0, seed)
                                for b in FRICTIONS_BPS
                            },
                            "overlaps_first_push_window": overlap,
                        }
                    )
                leg_curves.append(base)

    # ---- pairwise clock contrasts, paired by day -----------------------------------
    contrasts = []
    if 571 in clocks:
        pm_clocks = [c for c in clocks if c != 571]
        for uname, ucol in UNIVERSES:
            for part in PARTITIONS:
                for h in HORIZONS:
                    frames_by_clock = {}
                    for clock in pm_clocks + [571]:
                        f = _frames_for(ce_groups, clock, part)
                        if f is None or not f.height:
                            continue
                        f = f.filter(pl.col(ucol) & (pl.col("horizon") == h))
                        if f.height:
                            frames_by_clock[clock] = f
                    if 571 not in frames_by_clock:
                        continue
                    b = frames_by_clock[571]
                    for pm in pm_clocks:
                        a = frames_by_clock.get(pm)
                        for bps in FRICTIONS_BPS:
                            contrasts.append(
                                {
                                    "universe": uname,
                                    "partition": part,
                                    "horizon": h,
                                    "clock_a": int(pm),
                                    "clock_b": 571,
                                    "channel": f"inherited_inc_{bps}bps",
                                    **_contrast(
                                        _days(a) if a is not None else [],
                                        _f64(a, f"inc_{bps}") if a is not None else [],
                                        _days(b),
                                        _f64(b, f"inc_{bps}"),
                                        boot,
                                        seed,
                                    ),
                                }
                            )
                            contrasts.append(
                                {
                                    "universe": uname,
                                    "partition": part,
                                    "horizon": h,
                                    "clock_a": int(pm),
                                    "clock_b": 571,
                                    "channel": f"fresh_{bps}bps",
                                    **_contrast(
                                        _days(a) if a is not None else [],
                                        _f64(a, f"fresh_{bps}") if a is not None else [],
                                        _days(b),
                                        _f64(b, f"fresh_{bps}"),
                                        boot,
                                        seed,
                                    ),
                                }
                            )

    # ---- causal coordinate contexts (per clock, never pooled) ---------------------
    contexts = []
    for uname, ucol in UNIVERSES:
        for clock in clocks:
            for part in ("clean", "damaged"):
                sub = _frames_for(ce_groups, clock, part)
                if sub is None or not sub.height:
                    continue
                sub = sub.filter(pl.col(ucol) & pl.col("horizon").is_in(CONTEXT_H))
                if not sub.height:
                    continue
                hor = sub["horizon"].to_numpy()
                days_arr = _days(sub)
                for dim in CONTEXT_DIMS:
                    lab = _level(sub, dim)
                    for lev in sorted(set(lab.tolist()), key=str):
                        for h in CONTEXT_H:
                            mask = (lab == lev) & (hor == h)
                            if not mask.any():
                                continue
                            rh = sub.filter(pl.Series(mask))
                            contexts.append(
                                {
                                    "universe": uname,
                                    "clock": int(clock),
                                    "partition": part,
                                    "dim": dim,
                                    "level": lev,
                                    "horizon": h,
                                    "inherited_100bps": _stats(
                                        _days(rh),
                                        _f64(rh, f"inc_{FRICTIONS_BPS[0]}"),
                                        boot,
                                        seed,
                                    ),
                                    "fresh_100bps": _stats(
                                        _days(rh),
                                        _f64(rh, f"fresh_{FRICTIONS_BPS[0]}"),
                                        boot,
                                        seed,
                                    ),
                                    "above_push": _flag_block(rh, "above", _days(rh)),
                                }
                            )

    # ---- surrender and lifetime exposure ------------------------------------------
    surrender = []
    exposure = []
    for uname, ucol in UNIVERSES:
        for clock in clocks:
            for part in PARTITIONS:
                f = _frames_for(prof_groups, clock, part)
                if f is None or not f.height:
                    continue
                f = f.filter(pl.col(ucol))
                if not f.height:
                    continue
                d = _days(f)
                flat = _f64(f, "flat_value")
                row = {
                    "universe": uname,
                    "clock": int(clock),
                    "partition": part,
                    "n_claims": int(f.height),
                    "bank_at_push": _stats(d, _f64(f, "bank_value"), boot, seed),
                    "wait_to_flat_minus_bank": _stats(d, _f64(f, "flat_minus_bank"), boot, seed),
                    "flat_vs_best_declared_horizon_RULER_NOT_POLICY": _stats(
                        d, _f64(f, "flat_minus_bank") - _f64(f, "best_horizon_adv"), 0, seed
                    ),
                    "retro_max_minus_bank_BOUND": _stats(d, _f64(f, "retro_minus_bank"), 0, seed),
                    "retro_max_minus_flat_BOUND": _stats(d, _f64(f, "retro_minus_flat"), 0, seed),
                    "value_h_minus_flat": [],
                    # LEVEL fresh channel: a NEW $100 at the push open held to the
                    # terminal = 100 * (Pflat/P0) * F - 100, so the fresh buy fee is
                    # charged here exactly as in the first-push fresh_* columns.
                    "fresh_wait_to_flat_minus_bank": _stats(
                        d,
                        np.where(
                            _f64(f, "bank_value") > 0,
                            FRESH_BUDGET
                            * (flat - _f64(f, "bank_value"))
                            / _f64(f, "bank_value")
                            * FRESH_FEE[FRICTIONS_BPS[0]]
                            - FRESH_ENTRY_CHARGE[FRICTIONS_BPS[0]],
                            np.nan,
                        ),
                        boot,
                        seed,
                    ),
                    "fresh_value_h_minus_fresh_flat": [],
                }
                sub = _frames_for(ce_groups, clock, part)
                if sub is not None and sub.height:
                    sub = sub.filter(pl.col(ucol)).join(
                        flat_lookup, on=["day", "clock", "rank", "ticker"], how="left"
                    )
                    for h in CONTEXT_H:
                        sh = sub.filter(pl.col("horizon") == h)
                        if not sh.height:
                            row["value_h_minus_flat"].append({"horizon": h, "block": None})
                            row["fresh_value_h_minus_fresh_flat"].append(
                                {"horizon": h, "block": None}
                            )
                            continue
                        inherited_gap = _f64(sh, f"value_{FRICTIONS_BPS[0]}") - _f64(
                            sh, "flat_value_right"
                        )
                        bank_h = _f64(sh, "bank_value")
                        row["value_h_minus_flat"].append(
                            {"horizon": h, "block": _stats(_days(sh), inherited_gap, boot, seed)}
                        )
                        # DIFFERENCE fresh channel: the same exit gap on a NEW $100 at the
                        # push open.  Both exits are ratios against the same P0, so the
                        # fresh buy fee cancels and no entry charge applies here.
                        row["fresh_value_h_minus_fresh_flat"].append(
                            {
                                "horizon": h,
                                "block": _stats(
                                    _days(sh),
                                    np.where(
                                        bank_h > 0,
                                        FRESH_BUDGET
                                        * inherited_gap
                                        / bank_h
                                        * FRESH_FEE[FRICTIONS_BPS[0]],
                                        np.nan,
                                    ),
                                    boot,
                                    seed,
                                ),
                            }
                        )
                surrender.append(row)

                alive = f["alive_minutes"].to_numpy().astype(float)
                erow = {
                    "universe": uname,
                    "clock": int(clock),
                    "partition": part,
                    "n_claims": int(f.height),
                    "above_push_minutes": _stats(
                        d, f["above_push_minutes"].to_numpy().astype(float), boot, seed
                    ),
                    "above_push_censored_share": float(
                        f["above_push_censored"].fill_null(False).to_numpy().astype(bool).mean()
                    ),
                    "alive_minutes": _stats(d, alive, boot, seed),
                    "alive_censored_share": float(
                        f["alive_censored"].fill_null(False).to_numpy().astype(bool).mean()
                    ),
                    "minutes_to_repair": _stats(
                        d, f["minutes_to_repair"].to_numpy().astype(float), boot, seed
                    ),
                    "share_repaired_after_push": float(
                        f["repaired_after"].fill_null(False).to_numpy().astype(bool).mean()
                    ),
                    "hold_window_minutes_push_to_session_end": _dist(
                        f["hold_window_minutes"].to_numpy().astype(float)
                    ),
                    "exposure_dollar_minutes_per_100": _stats(
                        d, flat * np.where(np.isfinite(alive), alive, np.nan), 0, seed
                    ),
                    "exposure_unit": (
                        "flat dollars per $100 original budget x minutes alive before the "
                        "first -5% drawdown from the running high (UNKNOWN minutes excluded)"
                    ),
                    "legs_per_claim_total": _dist(f["legs_total"].to_numpy().astype(float)),
                    "legs_per_claim_immediate": _dist(f["legs_immediate"].to_numpy().astype(float)),
                    "legs_per_claim_after_pullback": _dist(
                        f["legs_after_pullback"].to_numpy().astype(float)
                    ),
                }
                exposure.append(erow)

    # ---- leg timing ----------------------------------------------------------------
    leg_timing = []
    for uname, ucol in UNIVERSES:
        for clock in clocks:
            for label, flag in LEG_CLASSES:
                one = _frames_for(le_groups, clock, "all", flag=flag)
                if one is None or not one.height:
                    continue
                one = one.filter((pl.col("horizon") == 1) & pl.col(ucol))
                if not one.height:
                    continue
                d = _days(one)
                leg_timing.append(
                    {
                        "universe": uname,
                        "clock": int(clock),
                        "class": label,
                        "legs": int(one.height),
                        "separation_min": _stats(
                            d, one["separation_min"].to_numpy().astype(float), boot, seed
                        ),
                        "sep_min_dd": _dist(one["sep_min_dd"].to_numpy().astype(float)),
                        "share_new_high": float(
                            one["new_high"].fill_null(False).to_numpy().astype(bool).mean()
                        ),
                        "share_before13": float(
                            one["before13"].fill_null(False).to_numpy().astype(bool).mean()
                        ),
                        "leg_ret_at_episode": _dist(one["leg_ret"].to_numpy().astype(float)),
                    }
                )

    # ---- per-clock summary ---------------------------------------------------------
    clock_summary = {}
    for uname, ucol in UNIVERSES:
        clock_summary[uname] = {}
        for clock in clocks:
            key = {"clock": int(clock)}
            f = _frames_for(prof_groups, clock, "all")
            if f is not None and f.height:
                f = f.filter(pl.col(ucol))
            if f is None or not f.height:
                key["push_episodes"] = 0
                clock_summary[uname][str(clock)] = key
                continue
            d = _days(f)
            key["push_episodes"] = int(f.height)
            key["claims_no_push"] = int(
                profiles.filter((pl.col("clock") == clock) & ~pl.col("has_push")).height
            )
            key["clean"] = int(f.filter(pl.col("clean")).height)
            key["damaged"] = int(f.filter(~pl.col("clean")).height)
            key["bank_at_push"] = _stats(d, _f64(f, "bank_value"), boot, seed)
            key["wait_to_flat_minus_bank"] = _stats(d, _f64(f, "flat_minus_bank"), boot, seed)
            key["best_declared_horizon_minus_bank_RULER_NOT_POLICY"] = _stats(
                d, _f64(f, "best_horizon_adv"), 0, seed
            )
            key["retro_max_minus_bank_BOUND"] = _stats(d, _f64(f, "retro_minus_bank"), 0, seed)
            key["legs"] = int(leg_one.filter((pl.col("clock") == clock) & pl.col(ucol)).height)
            ladder = {}
            sub = _frames_for(ce_groups, clock, "all")
            if sub is not None and sub.height:
                sub = sub.filter(pl.col(ucol))
                for h in CONTEXT_H:
                    sh = sub.filter(pl.col("horizon") == h)
                    if not sh.height:
                        continue
                    dd = _days(sh)
                    ladder[str(h)] = {
                        "inherited_inc_100bps": _stats(
                            dd, _f64(sh, f"inc_{FRICTIONS_BPS[0]}"), boot, seed
                        ),
                        "fresh_100bps": _stats(
                            dd, _f64(sh, f"fresh_{FRICTIONS_BPS[0]}"), boot, seed
                        ),
                    }
            key["ladder"] = ladder
            clock_summary[uname][str(clock)] = key

    f100 = FRICTIONS_BPS[0]
    support = {
        "policy": (
            "UNKNOWN is always null plus an explicit count, never read as zero.  Every cell "
            "reports n_known and n_unknown next to its mean so a support change across clocks "
            "or horizons stays visible instead of being averaged over."
        ),
        "inherited_known": int(ce.filter(pl.col("inc_stage").is_not_null()).height),
        "inherited_unknown": int(ce.filter(pl.col("inc_stage").is_null()).height),
        "inherited_value_known": int(ce.filter(pl.col(f"value_{f100}").is_not_null()).height),
        "inherited_value_unknown": int(ce.filter(pl.col(f"value_{f100}").is_null()).height),
        "fresh_known": int(ce.filter(pl.col(f"fresh_{f100}").is_not_null()).height),
        "fresh_unknown": int(ce.filter(pl.col(f"fresh_{f100}").is_null()).height),
        "leg_inherited_known": int(le.filter(pl.col("inc_stage").is_not_null()).height),
        "leg_inherited_unknown": int(le.filter(pl.col("inc_stage").is_null()).height),
        "leg_fresh_supported": 0,
        "leg_fresh_reason": "leg entry open is not stored in the stage; fresh dollars stay null",
    }

    checks = {
        "clocks_in_stage": clocks,
        "first_push_cells": len(first_push),
        "leg_cells": len(leg_curves),
        "context_cells": len(contexts),
        "contrast_cells": len(contrasts),
        "pooled_clock_cells": 0,
        "claim_episode_rows": int(ce.height),
        "leg_episode_rows": int(le.height),
        "first_push_open_known_but_horizon_increment_UNKNOWN": int(
            ce.filter(pl.col("known") & pl.col("inc_stage").is_null()).height
        )
        if ce.height
        else 0,
        "leg_entry_open_known_but_horizon_increment_UNKNOWN": int(
            le.filter(pl.col("leg_known") & pl.col("inc_stage").is_null()).height
        )
        if le.height
        else 0,
        "legs_without_matching_claim": int(
            le.filter(pl.col("clean").is_null()).height // len(HORIZONS)
        )
        if le.height
        else 0,
        "first_push_curves": sum(len(r["curve"]) for r in first_push),
        "leg_curves": sum(len(r["curve"]) for r in leg_curves),
    }

    artifact = {
        "kind": "OWNED-CLAIM-PER-CLOCK-EPISODE-DOLLARS-DISCOVERY-NOT-POLICY",
        "status": "smoke_non_evidence" if smoke else "full_discovery_evidence",
        "days": days,
        "lineage": lineage,
        "units": (
            "dollars per $100 of original equal-dollar claim budget on the fixed inherited "
            "share count 100/(fill_px*(1+0.005)); the original entry fee is SUNK inside that "
            "share count and never charged again.  inherited_* rescales only the exit fee "
            "(k=(1-side)/(1-0.005)), so at the staged 100bps round trip it equals the staged "
            "dollars exactly and at 150bps it is the exact algebraic rescale of them.  "
            "fresh_* is a separate NEW $100 of CASH deployed at that episode's own executable "
            "entry open P0 (sell_px at the episode minute, one-bar lag) and it is a LEVEL, i.e. "
            "net cash change against the cash put in: with F=(1-side)/(1+side), shares = "
            "100/(P0*(1+side)) and net = 100*(P/P0)*F - 100, where P is the executable exit "
            "open; because inc_stage/bank_value == (P-P0)/P0 holds exactly in the staged "
            "dollars this is fresh = 100*inc_stage/bank_value*F - 100*(1-F), so BOTH the buy fee "
            "on the fresh cash and the exit fee are charged.  The same LEVEL arithmetic at the "
            "session terminal gives fresh_wait_to_flat_minus_bank = "
            "100*(flat_value-bank_value)/bank_value*F - 100*(1-F).  fresh_value_h_minus_"
            "fresh_flat is a DIFFERENCE between two exits on the same P0, so the entry fee "
            "cancels and it stays 100*(value_h-flat)/bank_value*F with no entry charge.  "
            "inherited_* and fresh_* are therefore not interchangeable: the first keeps the "
            "sunk original entry fee and is denominated in the ORIGINAL budget's share count, "
            "the second pays the full round trip on new cash."
        ),
        "fresh_channel_is_not_sizing": (
            "the fresh_* columns are per EXECUTED $100 of new cash at the episode's own entry "
            "open - a conditional ruler conditional on that fill existing at the modelled size. "
            "They size nothing: no capital constraint, no share count, no capacity or "
            "fill-probability model is in this artifact, and a fresh quantity would have to be "
            "fixed at the causal mark with both fees charged before any of it is a policy."
        ),
        "friction_ladder_round_trip_bps": list(FRICTIONS_BPS),
        "horizons_et_minutes": list(HORIZONS),
        "prices": (
            "inherited verbatim from the staged causal episode tape: the causal state at grid "
            "minute t is the close of the last completed bar et<=t-1, and every liquidation "
            "executes at the observed next open (first valid bar et>=t, one-bar lag, "
            "in-session).  This producer recomputes no price and re-runs no raw tape: it "
            "re-reads the staged per-day claims/legs parquet and re-prices only the fee ladder, "
            "which is an exact algebraic function of the staged dollars."
        ),
        "rulers": (
            "inherited verbatim from owned_claim_push_legs.py: first causal +5% = "
            "ret_fill>=0.05; a push = upward crossing of ret5>=0.03; genuine damage/pullback = "
            "dd_from_high<=-0.05; repair = ret5>0 & dd_velocity5>0 & dd_from_high<-0.03; a "
            "later leg needs at least one observed damage minute strictly between the previous "
            "leg and it, otherwise it is an immediate extension.  No threshold is re-tuned "
            "here."
        ),
        "clock_policy": (
            "every continuation, leg, contrast, surrender and exposure cell is per admission "
            "clock (540/560/569 premarket, 571 near-open).  No cell pools clocks and no "
            "pooled-clock fallback is emitted; the 09:31-vs-premarket difference is reported "
            "as a day-paired contrast."
        ),
        "census": census,
        "clock_summary": clock_summary,
        "first_push_continuation": first_push,
        "leg_continuation": leg_curves,
        "clock_contrasts": contrasts,
        "contexts": contexts,
        "surrender": surrender,
        "lifetime_exposure": exposure,
        "leg_timing": leg_timing,
        "support": support,
        "checks": checks,
        "caveats": [
            "Descriptive rulers only: no policy, no tuned threshold, no winner class, no exit "
            "rule.  'best declared horizon' and the retrospective max are ex-post rulers and an "
            "explicitly unreachable bound; neither is attainable and neither selects a horizon "
            "per claim anywhere in this artifact.",
            "A negative mean at a declared horizon is NOT a prohibition and NOT a proof that no "
            "adaptive or state-dependent management could have done better: these are averages "
            "over one declared ruler with no conditioning on anything observed after the "
            "episode minute.  Reading them as 'never hold', 'never take a later leg' or 'always "
            "exit at h' would be a conclusion this artifact does not support.",
            "Any cell whose day-bootstrap CI excludes zero is a HYPOTHESIS, not a finding: 24 "
            "first-push cells x 10 horizons x 2 channels x 2 universes were scored without "
            "multiplicity control, so a single significant positive cell is expected by "
            "chance alone and must not be generalised into a rule.",
            "fresh_* dollars are net cash change on a NEW $100 already filled at the episode's "
            "own entry open.  They are a conditional per-executed-dollar ruler, not sizing, "
            "not a capital plan and not an implementable order: no quantity, capacity or "
            "fill-probability constraint is modelled here.",
            "N3 and N5 are independent nested ownership universes and are never pooled; no "
            "N3+N5 figure exists in this artifact.",
            "Later-leg episodes are OVERLAPPING rulers on claims already counted in the "
            "first-push cells: their n must not be added to the first-push episode count as if "
            "they were independent trades.  overlaps_first_push_window measures the overlap "
            "against the first-push observation window at each horizon.",
            "The fresh-entry channel exists only for first-push episodes, where the staged row "
            "carries the episode's executable entry open (push_px -> bank_value).  Leg rows do "
            "not carry their entry open, so their fresh channel is UNSUPPORTED and reported as "
            "null, never imputed from the original fill.",
            "Inherited dollars keep the sunk original entry fee while a fresh buyer pays it "
            "again, so an inherited increment and a fresh increment at the same horizon are "
            "NOT apples to apples; both channels are emitted side by side instead of merged.",
            "Day-balanced means weight dates equally (a busy name-day cannot dominate); the "
            "day-clustered SE and the deterministic day bootstrap (seeded, resampling days) "
            "carry the day dependence.  Claim-balanced mean/median are reported alongside.",
            "Support genuinely changes across clocks and horizons (late pushes have fewer "
            "in-session horizons).  Known and UNKNOWN counts ride with every cell so a "
            "short-support cell cannot masquerade as a precise one.",
            "A declared-horizon comparison such as value_h - flat_value says which of two "
            "specific declared exits held more dollars on average; it does not establish that "
            "the better exit is reachable, sized or worth taking, nor that a third, adaptive "
            "exit could not beat both.",
            "Clocks, events and horizons here are declared rulers, not truths; the four "
            "admission clocks are compared, not ranked by size.",
            "No protected/validation outcomes are read: the day set must equal the canonical "
            "533-day discovery list AND the staged producer's own discovery manifest.",
        ],
    }
    return artifact


# --------------------------------------------------------------------------- CLI
def main() -> None:
    ap = argparse.ArgumentParser(description="per-clock episode dollars for owned claims")
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument(
        "--stage-root",
        type=Path,
        default=None,
        help="push_legs_stage dir written by owned_claim_push_legs.py",
    )
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--stage", type=Path, default=None, help="this producer's own stage dir")
    ap.add_argument("--limit", type=int, default=0, help="bounded smoke; marks non-evidence")
    ap.add_argument("--boot", type=int, default=BOOT_DEFAULT)
    ap.add_argument("--seed", type=int, default=SEED_DEFAULT)
    args = ap.parse_args()

    stage_root = args.stage_root or (
        args.data_root / "harvest01" / "lifecycle" / "v2" / "owned_claim" / "push_legs_stage"
    )
    if not stage_root.is_dir():
        raise SystemExit(f"push_legs_stage not found: {stage_root}")

    # ---- lineage: discovery-only, exact, pinned -----------------------------------
    discovery = ls.discovery_days(args.data_root)
    manifest_path = stage_root.parent / "manifest.json"
    if not manifest_path.is_file():
        manifest_path = stage_root / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"staged producer manifest.json not found near {stage_root}")
    manifest = json.loads(manifest_path.read_text())
    if list(manifest.get("discovery_days", [])) != list(discovery):
        raise SystemExit(
            "staged manifest discovery_days != canonical discovery split; refusing a partial "
            "or protected read"
        )
    basis = str(manifest.get("increment_dollars", ""))
    if "50bps" not in basis:
        raise SystemExit(
            f"staged dollar basis changed: manifest increment_dollars={basis!r} (expected the "
            "declared 50bps per side)"
        )
    clocks = list(manifest.get("clocks", [540, 560, 569, 571]))
    days = discovery[: args.limit] if args.limit else list(discovery)
    smoke = bool(args.limit)
    for day in days:
        for suffix in ("claims.parquet", "legs.parquet"):
            if not (stage_root / f"{day}.{suffix}").is_file():
                raise SystemExit(f"stage missing {day}.{suffix}")
        if not (stage_root / "_done" / f"{day}.json").is_file():
            raise SystemExit(f"stage missing _done marker for {day}")

    script_sha = _sha(Path(__file__).resolve())
    ls_sha = _sha(Path(ls.__file__).resolve())
    split_path = ls.v2_dir(args.data_root) / "split.json"
    marker_sources = sorted(
        {
            json.loads((stage_root / "_done" / f"{d}.json").read_text()).get("source_sha256")
            for d in days
        }
        - {None}
    )
    lineage = {
        "this_script_sha256": script_sha,
        "lifecycle_study_sha256": ls_sha,
        "staged_manifest_sha256": _sha(manifest_path),
        "staged_manifest_source_sha256": manifest.get("source_sha256"),
        "staged_producer_marker_source_sha256": marker_sources,
        "staged_manifest_source_hashes": manifest.get("source_hashes"),
        "split_sha256": _sha(split_path) if split_path.is_file() else None,
        "manifest_declared_split_sha256": manifest.get("split_sha256"),
        "manifest_discovery_days_sha256": _canonical_sha(discovery),
        "manifest_increment_dollars": basis,
        "manifest_thinning": manifest.get("thinning"),
        "manifest_labels": manifest.get("labels"),
        "clocks": clocks,
        "horizons": list(HORIZONS),
        "stage_side": STAGE_SIDE,
        "friction_ladder_round_trip_bps": list(FRICTIONS_BPS),
        "stage_root": str(stage_root),
        "discovery_days_expected": len(discovery),
        "discovery_days_used": len(days),
        "protected_days_read": 0,
        "raw_tape_reread": False,
    }
    stage_pin = _canonical_sha(
        {
            "script": script_sha,
            "staged_manifest": lineage["staged_manifest_sha256"],
            "horizons": list(HORIZONS),
            "stage_side": STAGE_SIDE,
            "ladder": list(FRICTIONS_BPS),
        }
    )
    lineage["stage_pin"] = stage_pin

    stage = args.stage or args.out.parent / (args.out.stem + ".clock_stage")
    stage.mkdir(parents=True, exist_ok=True)
    checks = {
        "claims": 0,
        "claims_with_push": 0,
        "claims_push_open_known": 0,
        "claims_no_push": 0,
        "legs": 0,
        "legs_immediate": 0,
        "legs_after_pullback": 0,
        "legs_without_claim": 0,
        "first_push_open_known_but_horizon_increment_UNKNOWN": 0,
        "leg_entry_open_known_but_horizon_increment_UNKNOWN": 0,
        "chronology_violations": 0,
    }
    schema_cache: dict[str, pl.DataType] = {}
    missing_columns: list = []
    resumed = 0
    for i, day in enumerate(days):
        pp = stage / "claim_profiles" / f"{day}.parquet"
        pe = stage / "claim_horizons" / f"{day}.parquet"
        lg = stage / "leg_horizons" / f"{day}.parquet"
        marker = stage / "_done" / f"{day}.json"
        cpath = stage_root / f"{day}.claims.parquet"
        lpath = stage_root / f"{day}.legs.parquet"
        input_hash = hashlib.sha256((_sha(cpath) + _sha(lpath) + stage_pin).encode()).hexdigest()
        if marker.is_file() and pp.is_file() and pe.is_file() and lg.is_file():
            info = json.loads(marker.read_text())
            if info.get("stage_pin") == stage_pin and info.get("input_hash") == input_hash:
                missing_columns.extend(info.get("missing_columns", []))
                for name in checks:
                    checks[name] += int(info.get("checks", {}).get(name, 0))
                resumed += 1
                continue
        day_missing: list = []
        profiles, ce, le, day_checks = stage_day(day, cpath, lpath, schema_cache, day_missing)
        missing_columns.extend(day_missing)
        _write_parquet(pp, profiles)
        _write_parquet(pe, ce)
        _write_parquet(lg, le)
        for name in checks:
            checks[name] += int(day_checks.get(name, 0))
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(
                {
                    "status": "ok" if profiles.height else "empty",
                    "day": day,
                    "stage_pin": stage_pin,
                    "input_hash": input_hash,
                    "stage_claims_sha256": _sha(cpath),
                    "stage_legs_sha256": _sha(lpath),
                    "checks": day_checks,
                    "missing_columns": day_missing,
                }
            )
            + "\n"
        )
        if i % 50 == 0:
            print(
                f"{i + 1}/{len(days)} {day} claims={day_checks['claims']} "
                f"push={day_checks['claims_with_push']} legs={day_checks['legs']}",
                flush=True,
            )

    def concat(sub: str, name: str) -> pl.DataFrame:
        frames = []
        for day in days:
            path = stage / sub / f"{day}.parquet"
            if path.is_file():
                frames.append(pl.read_parquet(path))
        out = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()
        out.write_parquet(stage / name)
        return out

    concat("claim_profiles", "claim_profiles.parquet")
    concat("claim_horizons", "claim_horizons.parquet")
    concat("leg_horizons", "leg_horizons.parquet")
    lineage["staged_days"] = len(days)
    lineage["resumed_days"] = resumed
    lineage["per_day_checks"] = checks

    artifact = finalize(stage, days, lineage, args.boot, args.seed, smoke)
    artifact["checks"]["per_day"] = checks
    artifact["checks"]["staged_days_with_narrow_schema"] = sorted(
        {m["day"] for m in missing_columns}
    )
    artifact["checks"]["narrow_schema_events"] = len(missing_columns)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(artifact, indent=2, allow_nan=False) + "\n")
    tmp.replace(args.out)
    print(
        "clock-window episode dollars written",
        args.out,
        "status",
        artifact["status"],
        "days",
        len(days),
        "push_episodes",
        artifact["census"]["claims_with_push"],
        "legs",
        artifact["census"]["legs"],
        "first_push_cells",
        len(artifact["first_push_continuation"]),
        "leg_cells",
        len(artifact["leg_continuation"]),
        "contrast_cells",
        len(artifact["clock_contrasts"]),
        "context_cells",
        len(artifact["contexts"]),
    )


if __name__ == "__main__":
    main()
