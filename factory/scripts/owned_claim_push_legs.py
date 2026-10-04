#!/usr/bin/env python3
"""Dollar anatomy of the FIRST causal +5% push and every later leg of the same claim.

Discovery-only descriptive ruler.  It answers, per originally-owned claim that already
traded +5% above its own fill, WHERE the finite-window money actually sits after that
clean/damaged first push: the liquidation dollars available at each declared horizon,
the giveaway of one-shot banking versus indiscriminate waiting, and the same for every
later leg with an observed genuine pullback in between.

Prices are causal and executable, never highs:
  * the causal state at grid minute ``t`` is the close of the last COMPLETED bar with
    ``et <= t-1`` (the panel's ``px``);
  * every liquidation executes at the OBSERVED next open: the panel's ``sell_px`` /
    ``sell_et`` = open of the first valid bar with ``et >= t`` (a one-bar lag behind the
    decision), in-session only.  A horizon whose executable bar does not exist inside the
    session is UNKNOWN (null), never zero;
  * the flat/terminal wealth executes at the panel's ``exit_px`` / ``exit_et`` only when
    that observed terminal open actually falls inside the declared session
    (``exit_et <= session_end``); otherwise terminal wealth and every comparison against
    it is UNKNOWN (null), never a later out-of-session open;
  * the retrospective best is the max over the chronological actual opens after the push
    (an explicitly UNATTAINABLE hindsight bound, carried only for scale).

Dollars are per $100 of the original equal-dollar claim budget.  That budget bought
``100 / (fill_px * (1 + SIDE))`` inherited shares, so the original entry fee is embedded
in the fixed share count (SUNK: it is never charged again) and every modelled sale nets
``(1 - SIDE)`` per inherited share.  All original-cash values therefore carry the
fixed-share factor ``(1 - SIDE) / (1 + SIDE)``; no new capital is deployed and no fresh
buy fee is charged.

The push/leg/damage rulers are the producer's existing declarative rulers, not new tuned
thresholds: first causal +5% = ``ret_fill >= 0.05``; a push = upward crossing of
``ret5 >= 0.03``; genuine pullback/damage = ``dd_from_high <= -0.05``; repair = ``ret5 > 0
and dd_velocity5 > 0 and dd_from_high < -0.03``.  A later leg is a *second leg after a
genuine pullback* only when at least one observed minute strictly between the previous
leg and it printed damage; otherwise it is an *immediate extension*.  N3 and N5 are
independent ownership universes and are never pooled.  No winner classifier, no policy,
no protected outcome.

CLI producer: one day at a time, atomic per-day staging parquet + ``_done`` marker keyed
by an input hash (panel + roster + events + source), so a re-run resumes cheaply and a
changed input invalidates exactly the affected days.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path

os.environ.setdefault("POLARS_MAX_THREADS", "2")
import lifecycle_study as ls
import numpy as np
import polars as pl

CLOCKS = (540, 560, 569, 571)
PREOPEN_CLOCKS = (540, 560, 569)
HORIZONS = (1, 3, 5, 10, 15, 30, 45, 60, 90, 120)
CONTEXT_H = (30, 60, 120)
PARTITIONS = ("clean", "damaged", "all")
SIDE = 0.005  # modelled per-side cost; declared ruler, not optimized
FEE = (1.0 - SIDE) / (1.0 + SIDE)  # original buy fee (sunk in the share count) + exit fee
PROFIT5 = 0.05
PUSH = 0.03
DAMAGE = -0.05
REPAIR_DD = -0.03
NOON = 780  # 13:00 ET: the "pre13 vs later" split for the first push

PANEL_COLS = [
    "day",
    "clock",
    "rank",
    "ticker",
    "status",
    "t",
    "session_end",
    "fill_et",
    "fill_px",
    "filled_asof",
    "px",
    "px_et",
    "ret_fill",
    "dd_from_high",
    "ret5",
    "peak_gain",
    "dd_velocity5",
    "dvol5",
    "dvol15",
    "sell_px",
    "sell_et",
    "exit_px",
    "exit_et",
    "mark_px",
]
ROSTER_COLS = [
    "day",
    "clock",
    "rank",
    "ticker",
    "status",
    "decision_px",
    "decision_et",
    "fill_et",
    "fill_px",
    "session_end",
    "rank_all",
    "source",
]
EVENT_COLS = [
    "day",
    "clock",
    "rank",
    "ticker",
    "t",
    "feature_event_profit5",
    "feature_event_damage",
    "feature_hist_count_damage",
]


def finite(x) -> bool:
    return x is not None and math.isfinite(float(x))


def _none(x):
    return None if x is None or not math.isfinite(float(x)) else float(x)


def _read_cols(path: Path, cols: list[str]) -> pl.DataFrame:
    """Read only the intersection of the requested columns with the live schema."""
    schema = pl.read_parquet_schema(path)
    keep = [c for c in cols if c in schema]
    return pl.read_parquet(path, columns=keep)


# --------------------------------------------------------------------------- per claim
def _open_series(sub: pl.DataFrame):
    t = sub["t"].to_numpy().astype(np.int64)
    if t.size and not np.all(np.diff(t) == 1):
        raise AssertionError("noncontiguous minute grid")
    px = sub["px"].to_numpy().astype(float)
    ret = sub["ret_fill"].to_numpy().astype(float)
    dd = sub["dd_from_high"].to_numpy().astype(float)
    r5 = sub["ret5"].to_numpy().astype(float)
    ddv5 = sub["dd_velocity5"].to_numpy().astype(float)
    pg = sub["peak_gain"].to_numpy().astype(float)
    dv5 = sub["dvol5"].to_numpy().astype(float)
    dv15 = sub["dvol15"].to_numpy().astype(float)
    spx = sub["sell_px"].to_numpy().astype(float)
    set_ = sub["sell_et"].to_numpy().astype(float)
    return t, px, ret, dd, r5, ddv5, pg, dv5, dv15, spx, set_


def _first_true(mask: np.ndarray):
    w = np.flatnonzero(mask)
    return None if w.size == 0 else int(w[0])


def _repair_mask(r5, ddv5, dd) -> np.ndarray:
    return (
        np.isfinite(r5)
        & np.isfinite(ddv5)
        & np.isfinite(dd)
        & (r5 > 0)
        & (ddv5 > 0)
        & (dd < REPAIR_DD)
    )


def _crossings(vals: np.ndarray, level: float) -> np.ndarray:
    """Upward crossings of ``level`` (previous value missing or below)."""
    prev = np.concatenate(([np.nan], vals[:-1]))
    return np.isfinite(vals) & (vals >= level) & (~np.isfinite(prev) | (prev < level))


def _down_crossings(vals: np.ndarray, level: float) -> np.ndarray:
    """Downward crossings of ``level`` (previous value missing or above)."""
    prev = np.concatenate(([np.nan], vals[:-1]))
    return np.isfinite(vals) & (vals <= level) & (~np.isfinite(prev) | (prev > level))


def claim_anatomy(sub: pl.DataFrame, push_anchor: int | None):
    """Scalar dollar anatomy of one filled claim.  ``push_anchor`` is the event-minute of
    the first causal +5% (or None); a recomputed fallback keeps the claim usable."""
    t, _px, ret, dd, r5, ddv5, pg, dv5, dv15, spx, set_ = _open_series(sub)
    n = t.size
    row = sub.row(0, named=True)
    day = row["day"]
    clock = int(row["clock"])
    rank = int(row["rank"])
    ticker = row["ticker"]
    session_end = int(row["session_end"])
    fill_et = int(row["fill_et"])
    fill_px = float(row["fill_px"])
    t0 = int(t[0])
    exit_px = float(row["exit_px"]) if finite(row["exit_px"]) else None
    exit_et = int(row["exit_et"]) if finite(row["exit_et"]) else None

    trail = _first_true(np.isfinite(ret) & (ret >= PROFIT5))
    push_rec = None if trail is None else int(t0 + trail)
    push_t = None
    checks = {"push_event_only": 0, "push_recompute_only": 0, "push_agree": 0}
    if push_anchor is not None:
        push_t = int(push_anchor)
        if push_rec is not None and push_rec == push_t:
            checks["push_agree"] = 1
        elif push_rec is None or push_rec != push_t:
            checks["push_event_only"] = 1
    elif push_rec is not None:
        push_t = push_rec
        checks["push_recompute_only"] = 1

    base = {
        "day": day,
        "clock": clock,
        "rank": rank,
        "ticker": ticker,
        "n3": rank <= 3,
        "n5": rank <= 5,
        "session_end": session_end,
        "fill_et": fill_et,
        "fill_px": fill_px,
        "admission_group": "preopen" if clock in PREOPEN_CLOCKS else "near_open",
        "has_push": push_t is not None,
    }
    if push_t is None:
        return base, [], checks

    i = push_t - t0
    if not (0 <= i < n):
        base["has_push"] = False
        return base, [], checks

    # --- prior owned 5% damage strictly before the first push (clean vs damaged) --------
    pre = np.zeros(n, dtype=bool)
    pre[:i] = True
    before = pre & np.isfinite(dd)
    min_dd_before = float(np.min(dd[before])) if before.any() else None
    prior_damage = bool(before.any() and min_dd_before is not None and min_dd_before <= DAMAGE)
    damage_cross = _down_crossings(dd, DAMAGE)
    prior_count = int(np.count_nonzero(damage_cross & pre))
    clean = not prior_damage

    # --- actual next open at the push ---------------------------------------------------
    push_px = float(spx[i]) if finite(spx[i]) else None
    push_et = int(set_[i]) if finite(set_[i]) else None
    push_ret = float(ret[i]) if finite(ret[i]) else None
    known_open = (
        push_px is not None
        and push_px > 0
        and push_et is not None
        and push_et <= session_end
        and fill_px > 0
    )

    tod = (
        "pm"
        if push_t < 570
        else "open_30m"
        if push_t < 600
        else "10_11"
        if push_t < 660
        else "11_13"
        if push_t < NOON
        else "after13"
    )
    base.update(
        {
            "push_t": push_t,
            "push_et": push_et,
            "push_px": push_px,
            "push_ret": push_ret,
            "push_tod": tod,
            "push_before13": push_t < NOON,
            "prior_damage": prior_damage,
            "prior_damage_count": prior_count,
            "min_dd_before": _none(min_dd_before),
            "clean": clean,
            "flow_expanding": bool(finite(dv5[i]) and finite(dv15[i]) and dv5[i] > dv15[i] / 3.0),
            "known_open": known_open,
        }
    )

    # --- time alive / price above push / giveback / repair ------------------------------
    rel = np.arange(n) > i
    below = rel & np.isfinite(ret) & (ret < PROFIT5)
    k = _first_true(below)
    above_minutes = (t[k] - push_t) if k is not None else None
    above_censored = k is None
    breaker = rel & np.isfinite(dd) & (dd <= DAMAGE)
    kb = _first_true(breaker)
    alive_minutes = (t[kb] - push_t) if kb is not None else None
    alive_censored = kb is None
    after_dd = dd[rel & np.isfinite(dd)]
    max_dd_after = float(np.min(after_dd)) if after_dd.size else None
    repair = _repair_mask(r5, ddv5, dd)
    kr = _first_true(rel & repair)
    repaired_after = kr is not None
    minutes_to_repair = (t[kr] - push_t) if kr is not None else None
    base.update(
        {
            "above_push_minutes": None if above_minutes is None else int(above_minutes),
            "above_push_censored": bool(above_censored),
            "alive_minutes": None if alive_minutes is None else int(alive_minutes),
            "alive_censored": bool(alive_censored),
            "max_dd_after": _none(max_dd_after),
            "repaired_after": bool(repaired_after),
            "minutes_to_repair": None if minutes_to_repair is None else int(minutes_to_repair),
        }
    )

    # --- later legs: push crossings after the first push, with observed separation -------
    pushes = _crossings(r5, PUSH)
    later = [int(idx) for idx in np.flatnonzero(pushes) if idx > i]
    legs = []
    prev = i
    span = np.arange(n)
    for leg_idx in later:
        seg = (span > prev) & (span < leg_idx)
        sep_ok = seg & np.isfinite(dd)
        sep_min_dd = float(np.min(dd[sep_ok])) if sep_ok.any() else None
        after_pullback = bool(seg.any() and sep_min_dd is not None and sep_min_dd <= DAMAGE)
        new_high = bool(finite(pg[leg_idx]) and finite(pg[prev]) and pg[leg_idx] > pg[prev])
        legs.append(
            {
                "day": day,
                "clock": clock,
                "rank": rank,
                "ticker": ticker,
                "n3": rank <= 3,
                "n5": rank <= 5,
                "leg_t": int(t[leg_idx]),
                "leg_ret": _none(ret[leg_idx]),
                "before13": int(t[leg_idx]) < NOON,
                "after_pullback": after_pullback,
                "separation_min": int(t[leg_idx] - t[prev]),
                "sep_min_dd": _none(sep_min_dd),
                "new_high": new_high,
                "leg_known": bool(
                    finite(spx[leg_idx])
                    and spx[leg_idx] > 0
                    and finite(set_[leg_idx])
                    and set_[leg_idx] <= session_end
                ),
                **_leg_horizons(leg_idx, t, spx, set_, session_end, fill_px),
            }
        )
        prev = leg_idx
    base.update(
        {
            "legs_total": len(legs),
            "legs_immediate": sum(1 for g in legs if not g["after_pullback"]),
            "legs_after_pullback": sum(1 for g in legs if g["after_pullback"]),
        }
    )

    # --- dollars: bank at push, wait to horizons, wait to flat, hindsight bound ----------
    if not known_open:
        base["bank_value"] = None
        base["flat_value"] = None
        base["flat_minus_bank"] = None
        base["best_horizon_adv"] = None
        base["retro_known"] = False
        base["retro_minus_bank"] = None
        base["retro_minus_flat"] = None
        for h in HORIZONS:
            base[f"inc_{h}"] = None
            base[f"known_{h}"] = False
            base[f"dd_{h}"] = None
            base[f"repair_{h}"] = None
            base[f"above_{h}"] = None
        return base, legs, checks

    bank_value = 100.0 * push_px / fill_px * FEE
    base["bank_value"] = bank_value
    flat_known = (
        exit_px is not None and exit_px > 0 and exit_et is not None and exit_et <= session_end
    )
    flat_value = (100.0 * exit_px / fill_px * FEE) if flat_known else None
    base["flat_value"] = flat_value
    base["flat_minus_bank"] = None if flat_value is None else flat_value - bank_value

    best = None
    chrono_bad = 0
    last_et = push_et
    for h in HORIZONS:
        j = i + h
        inc = None
        known = False
        dd_h = None
        rep_h = None
        abv_h = None
        if (
            j < n
            and finite(spx[j])
            and finite(set_[j])
            and set_[j] > push_et
            and set_[j] <= session_end
            and push_et <= t[j]
        ):
            inc = 100.0 * (spx[j] - push_px) / fill_px * FEE
            known = True
            if set_[j] < last_et:
                chrono_bad += 1
            last_et = set_[j]
            if best is None or inc > best:
                best = inc
        if j < n:
            dd_h = _none(dd[j])
            rep_h = bool(repair[j]) if finite(r5[j]) and finite(ddv5[j]) and finite(dd[j]) else None
            abv_h = bool(ret[j] >= PROFIT5) if finite(ret[j]) else None
        base[f"inc_{h}"] = inc
        base[f"known_{h}"] = known
        base[f"dd_{h}"] = dd_h
        base[f"repair_{h}"] = rep_h
        base[f"above_{h}"] = abv_h
    base["best_horizon_adv"] = best
    checks["chronology_violations"] = chrono_bad

    valid = np.isfinite(spx) & (set_ <= session_end) & (np.arange(n) > i)
    if valid.any():
        retro = float(np.max(spx[valid]))
        base["retro_known"] = True
        base["retro_minus_bank"] = 100.0 * (retro - push_px) / fill_px * FEE
        base["retro_minus_flat"] = (
            None if flat_value is None else 100.0 * (retro - exit_px) / fill_px * FEE
        )
    else:
        base["retro_known"] = False
        base["retro_minus_bank"] = None
        base["retro_minus_flat"] = None
    return base, legs, checks


def _leg_horizons(leg_idx, t, spx, set_, session_end, fill_px):
    out = {}
    for h in HORIZONS:
        j = leg_idx + h
        if (
            j < t.size
            and finite(spx[j])
            and finite(set_[j])
            and finite(spx[leg_idx])
            and spx[leg_idx] > 0
            and fill_px > 0
            and set_[j] > set_[leg_idx]
            and set_[j] <= session_end
            and set_[leg_idx] <= t[j]
        ):
            out[f"leg_inc_{h}"] = 100.0 * (spx[j] - spx[leg_idx]) / fill_px * FEE
        else:
            out[f"leg_inc_{h}"] = None
    return out


def stage_day(day: str, data_root: Path, events_root: Path):
    # ``load_panel`` augments the stored grid with the causal route features that the
    # event producer itself consumes (dd_velocity5, ret5, ...), so the rulers here are
    # byte-identical to the canonical producer rather than re-derived from raw columns.
    panel = ls.load_panel([day], data_root, clocks=CLOCKS, with_roster=False, with_tape=False)
    panel = panel.select([c for c in PANEL_COLS if c in panel.columns])
    roster = _read_cols(ls.v2_dir(data_root) / "roster" / f"{day}.parquet", ROSTER_COLS)
    roster = roster.filter(pl.col("clock").is_in(CLOCKS))
    events = _read_cols(events_root / "events" / f"{day}.parquet", EVENT_COLS)
    if not panel.height:
        return [], [], {"claims": 0}, {}

    def keyset(df):
        return set(map(tuple, df.select(["clock", "rank", "ticker"]).unique().iter_rows()))

    filled = panel.filter(pl.col("status") == "filled")
    panel_keys = keyset(filled)
    event_keys = keyset(events)
    roster_keys = keyset(roster)
    panel_all = keyset(panel)
    if panel_keys != event_keys:
        raise SystemExit(
            f"{day}: panel-filled roster != event roster ({len(panel_keys)} vs {len(event_keys)})"
        )
    if not event_keys <= roster_keys:
        raise SystemExit(f"{day}: events reference claims absent from roster")
    if not panel_all <= roster_keys:
        raise SystemExit(
            f"{day}: panel references claims absent from roster "
            f"({len(panel_all - roster_keys)} extra)"
        )
    if len(roster["day"].unique().to_list()) != 1:
        raise SystemExit(f"{day}: roster spans multiple days")

    anchors = {}
    if events.height and "feature_event_profit5" in events.columns:
        prof = events.filter(pl.col("feature_event_profit5") > 0)
        for r in prof.iter_rows(named=True):
            anchors.setdefault((int(r["clock"]), int(r["rank"]), r["ticker"]), int(r["t"]))
    hist = {}
    dmg_at = {}
    if events.height and "feature_hist_count_damage" in events.columns:
        for r in events.iter_rows(named=True):
            key2 = (int(r["clock"]), int(r["rank"]), r["ticker"], int(r["t"]))
            if r.get("feature_hist_count_damage") is not None:
                hist[key2] = int(r["feature_hist_count_damage"])
            dmg_at[key2] = int(r.get("feature_event_damage") or 0)

    claims, legs = [], []
    checks = {
        "push_event_only": 0,
        "push_recompute_only": 0,
        "push_agree": 0,
        "chronology_violations": 0,
        "damage_count_checked": 0,
        "damage_count_mismatch": 0,
        "damage_co_printed_at_push": 0,
    }
    for key, sub in filled.partition_by(["clock", "rank", "ticker"], as_dict=True).items():
        k = (int(key[0]), int(key[1]), key[2])
        sub = sub.sort("t")
        row, leg_rows, chk = claim_anatomy(sub, anchors.get(k))
        claims.append(row)
        legs.extend(leg_rows)
        for name in checks:
            checks[name] += chk.get(name, 0)
        if row.get("has_push"):
            key2 = (k[0], k[1], k[2], row["push_t"])
            seen = hist.get(key2)
            if seen is not None:
                checks["damage_count_checked"] += 1
                co = dmg_at.get(key2, 0)
                checks["damage_co_printed_at_push"] += co
                if int(seen) - co != (row.get("prior_damage_count") or 0):
                    checks["damage_count_mismatch"] += 1
    census = {
        "claims": len(claims),
        "pushes": sum(1 for r in claims if r["has_push"]),
        "clean": sum(1 for r in claims if r.get("clean") is True),
        "damaged": sum(1 for r in claims if r.get("clean") is False),
        "push_open_known": sum(1 for r in claims if r.get("known_open")),
        "legs": len(legs),
        "legs_after_pullback": sum(1 for g in legs if g["after_pullback"]),
        "legs_immediate": sum(1 for g in legs if not g["after_pullback"]),
        "roster_candidates": len(roster_keys),
        "roster_filled": len(panel_keys),
        "roster_only_unpriced": len(roster_keys - panel_all),
    }
    return claims, legs, census, checks


# --------------------------------------------------------------------------- aggregation
def _dist(values):
    v = np.asarray([x for x in values if finite(x)], dtype=float)
    if v.size == 0:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "p10": None,
            "p25": None,
            "p75": None,
            "p90": None,
            "min": None,
            "max": None,
            "share_pos": None,
        }
    return {
        "n": int(v.size),
        "mean": float(v.mean()),
        "median": float(np.median(v)),
        "p10": float(np.percentile(v, 10)),
        "p25": float(np.percentile(v, 25)),
        "p75": float(np.percentile(v, 75)),
        "p90": float(np.percentile(v, 90)),
        "min": float(v.min()),
        "max": float(v.max()),
        "share_pos": float((v > 0).mean()),
    }


def _day_balanced(days, values, boot: int = 500, seed: int = 17):
    pairs = [(d, float(v)) for d, v in zip(days, values, strict=False) if finite(v)]
    if not pairs:
        return {"n_days": 0, "mean_day": None, "ci_lo": None, "ci_hi": None}
    by = {}
    for d, v in pairs:
        by.setdefault(d, []).append(v)
    means = np.asarray([float(np.mean(x)) for x in by.values()], dtype=float)
    if boot <= 0 or means.size < 2:
        return {
            "n_days": int(means.size),
            "mean_day": float(means.mean()),
            "ci_lo": None,
            "ci_hi": None,
        }
    rng = np.random.default_rng(seed)
    bs = np.array([rng.choice(means, size=means.size, replace=True).mean() for _ in range(boot)])
    return {
        "n_days": int(means.size),
        "mean_day": float(means.mean()),
        "ci_lo": float(np.percentile(bs, 2.5)),
        "ci_hi": float(np.percentile(bs, 97.5)),
    }


def _block(rows, value, boot=500):
    days = [r["day"] for r in rows]
    vals = [r.get(value) for r in rows]
    out = _day_balanced(days, vals, boot=boot)
    out["n_claims"] = sum(1 for v in vals if finite(v))
    out["n_missing"] = sum(1 for v in vals if not finite(v))
    out["claim"] = _dist(vals)
    return out


def _concentration(rows, value):
    vals = [(r["day"], r.get(value)) for r in rows if finite(r.get(value))]
    if not vals:
        return {"n": 0}
    pos = sorted((v for _, v in vals if v > 0), reverse=True)
    total = sum(pos)
    by_day = {}
    for d, v in vals:
        by_day.setdefault(d, []).append(v)
    day_pos = sorted((sum(x) for x in (np.asarray(y) for y in by_day.values())), reverse=True)
    posdays = sum(1 for x in day_pos if x > 0)
    negdays = sum(1 for x in day_pos if x < 0)

    def share(arr, frac):
        if not arr:
            return None
        k = max(1, int(math.ceil(len(arr) * frac)))
        return float(sum(arr[:k]) / total) if total > 0 else None

    return {
        "n": len(vals),
        "n_days": len(by_day),
        "n_pos_days": posdays,
        "n_neg_days": negdays,
        "positive_sum": float(total),
        "day_top1_share": (float(day_pos[0] / total) if total > 0 and day_pos else None),
        "day_top5_share": (float(sum(day_pos[:5]) / total) if total > 0 else None),
        "claim_top1pct_share": share(pos, 0.01),
        "claim_top5pct_share": share(pos, 0.05),
        "claim_max": (float(pos[0]) if pos else None),
    }


def finalize(stage: Path, days: list[str], source_sha: str, provenance: dict, census: dict):
    claims = pl.read_parquet(stage / "claims.parquet")
    legs = pl.read_parquet(stage / "legs.parquet")
    rows = claims.to_dicts()
    leg_rows = legs.to_dicts() if legs.height else []

    def select(n, partition):
        sub = [r for r in rows if r["n3"]] if n == 3 else [r for r in rows if r["n5"]]
        if partition == "clean":
            return [r for r in sub if r.get("has_push") and r.get("clean") is True]
        if partition == "damaged":
            return [r for r in sub if r.get("has_push") and r.get("clean") is False]
        return sub

    continuation, paths, contexts = [], [], []
    for n in (3, 5):
        for part in PARTITIONS:
            sub = select(n, part)
            for h in HORIZONS:
                col = f"inc_{h}"
                block = _block([r for r in sub if r["has_push"]], col)
                continuation.append({"n": n, "partition": part, "horizon": h, **block})
                dds = [r.get(f"dd_{h}") for r in sub if r["has_push"]]
                reps = [r.get(f"repair_{h}") for r in sub if r["has_push"]]
                abvs = [r.get(f"above_{h}") for r in sub if r["has_push"]]
                dd_known = [x for x in dds if finite(x)]
                rep_known = [x for x in reps if x is not None]
                abv_known = [x for x in abvs if x is not None]
                paths.append(
                    {
                        "n": n,
                        "partition": part,
                        "horizon": h,
                        "n_path": len(abv_known),
                        "n_above_known": len(abv_known),
                        "n_above_unknown": len(abvs) - len(abv_known),
                        "n_repair_known": len(rep_known),
                        "n_repair_unknown": len(reps) - len(rep_known),
                        "n_dd_known": len(dd_known),
                        "n_dd_unknown": len(dds) - len(dd_known),
                        "mean_dd": float(np.mean(dd_known)) if dd_known else None,
                        "share_damaged": (
                            float(np.mean([x <= DAMAGE for x in dd_known])) if dd_known else None
                        ),
                        "share_repairing": (
                            float(np.mean([bool(x) for x in rep_known])) if rep_known else None
                        ),
                        "share_above_push": (
                            float(np.mean([bool(x) for x in abv_known])) if abv_known else None
                        ),
                    }
                )

    def levels(r, dim):
        if dim == "admission_group":
            return r.get("admission_group")
        if dim == "push_before13":
            return "before13" if r.get("push_before13") else "at_or_after13"
        if dim == "push_tod":
            return r.get("push_tod")
        if dim == "flow":
            return "expanding" if r.get("flow_expanding") else "not_expanding"
        if dim == "history":
            c = r.get("prior_damage_count") or 0
            return "0" if c == 0 else ("1" if c == 1 else "2+")
        if dim == "rank":
            return str(r.get("rank"))
        raise KeyError(dim)

    for n in (3, 5):
        for part in ("clean", "damaged"):
            sub = [r for r in select(n, part) if r["has_push"] and r.get("known_open")]
            for dim in ("admission_group", "push_before13", "push_tod", "flow", "history", "rank"):
                for lev in sorted({levels(r, dim) for r in sub}, key=str):
                    grp = [r for r in sub if levels(r, dim) == lev]
                    for h in CONTEXT_H:
                        contexts.append(
                            {
                                "n": n,
                                "partition": part,
                                "dim": dim,
                                "level": lev,
                                "horizon": h,
                                **_block(grp, f"inc_{h}", boot=0),
                            }
                        )

    leg_stats = []
    for n in (3, 5):
        sub = [g for g in leg_rows if (g["n3"] if n == 3 else g["n5"])]
        for cls, label in ((True, "second_leg_after_pullback"), (False, "immediate_extension")):
            grp = [g for g in sub if g["after_pullback"] is cls]
            entry = {
                "n": n,
                "class": label,
                "legs": len(grp),
                "mean_separation": (
                    float(np.mean([g["separation_min"] for g in grp])) if grp else None
                ),
                "mean_sep_min_dd": (_dist([g["sep_min_dd"] for g in grp])["mean"] if grp else None),
                "share_new_high": (float(np.mean([g["new_high"] for g in grp])) if grp else None),
                "share_before13": (float(np.mean([g["before13"] for g in grp])) if grp else None),
                "legs_known_open": sum(1 for g in grp if g["leg_known"]),
                "curve": [],
            }
            for h in HORIZONS:
                entry["curve"].append({"horizon": h, **_block(grp, f"leg_inc_{h}", boot=0)})
            leg_stats.append(entry)

    timing = []
    for n in (3, 5):
        for part in PARTITIONS:
            sub = [r for r in select(n, part) if r.get("has_push")]
            if not sub:
                timing.append({"n": n, "partition": part, "claims": 0})
                continue
            timing.append(
                {
                    "n": n,
                    "partition": part,
                    "claims": len(sub),
                    "push_ret": _dist([r.get("push_ret") for r in sub]),
                    "above_push_minutes": _dist([r.get("above_push_minutes") for r in sub]),
                    "above_push_censored_share": float(
                        np.mean([bool(r.get("above_push_censored")) for r in sub])
                    ),
                    "alive_minutes": _dist([r.get("alive_minutes") for r in sub]),
                    "alive_censored_share": float(
                        np.mean([bool(r.get("alive_censored")) for r in sub])
                    ),
                    "minutes_to_repair": _dist([r.get("minutes_to_repair") for r in sub]),
                    "share_repaired_after_push": float(
                        np.mean([bool(r.get("repaired_after")) for r in sub])
                    ),
                    "max_dd_after_push": _dist([r.get("max_dd_after") for r in sub]),
                    "legs_per_claim_mean": float(np.mean([r.get("legs_total") or 0 for r in sub])),
                    "legs_immediate_per_claim_mean": float(
                        np.mean([r.get("legs_immediate") or 0 for r in sub])
                    ),
                    "legs_after_pullback_per_claim_mean": float(
                        np.mean([r.get("legs_after_pullback") or 0 for r in sub])
                    ),
                }
            )

    surrender = []
    for n in (3, 5):
        for part in PARTITIONS:
            sub = [r for r in select(n, part) if r["has_push"]]
            surrender.append(
                {
                    "n": n,
                    "partition": part,
                    "bank_value": _block(sub, "bank_value", boot=0),
                    "wait_to_flat_minus_bank": _block(sub, "flat_minus_bank"),
                    "best_fixed_horizon_minus_bank": _block(sub, "best_horizon_adv"),
                    "retro_max_minus_bank_BOUND": _block(sub, "retro_minus_bank", boot=0),
                    "retro_max_minus_flat_BOUND": _block(sub, "retro_minus_flat", boot=0),
                }
            )

    concentration = {}
    for n in (3, 5):
        key = f"n{n}"
        concentration[key] = {}
        for part in PARTITIONS:
            sub = [r for r in select(n, part) if r["has_push"] and r.get("known_open")]
            concentration[key][part] = {
                "wait_to_flat_minus_bank": _concentration(sub, "flat_minus_bank"),
                "best_fixed_horizon_minus_bank": _concentration(sub, "best_horizon_adv"),
            }

    headline = {}
    for n in (3, 5):
        for part in ("clean", "damaged"):
            sub = [r for r in select(n, part) if r["has_push"] and r.get("known_open")]
            headline[f"n{n}_{part}"] = {
                "claims": len(sub),
                "mean_bank_dollars": _dist([r.get("bank_value") for r in sub])["mean"],
                "mean_flat_dollars": _dist([r.get("flat_value") for r in sub])["mean"],
                "mean_wait_to_flat_minus_bank": _block(sub, "flat_minus_bank", boot=0)["claim"][
                    "mean"
                ],
                "share_wait_to_flat_better": _dist([r.get("flat_minus_bank") for r in sub])[
                    "share_pos"
                ],
                "mean_best_fixed_horizon_minus_bank": _block(sub, "best_horizon_adv", boot=0)[
                    "claim"
                ]["mean"],
                "mean_retro_max_minus_bank_BOUND": _block(sub, "retro_minus_bank", boot=0)["claim"][
                    "mean"
                ],
            }

    artifact = {
        "kind": "CAUSAL-FIRST-PUSH-DOLLAR-ANATOMY-DISCOVERY-NOT-POLICY",
        "days": days,
        "source_sha256": source_sha,
        "provenance": provenance,
        "units": (
            "dollars per $100 original equal-dollar claim budget on the fixed inherited "
            "share count 100/(fill_px*(1+SIDE)); entry fee SUNK (no second charge); each "
            "modelled sale nets (1-SIDE), so original-cash values carry (1-SIDE)/(1+SIDE); "
            "no new-capital buy fee"
        ),
        "prices": (
            "causal state = close of last completed bar et<=t-1; every liquidation = "
            "observed next open (panel sell_px/sell_et, first valid bar et>=t, one-bar lag, "
            "in-session); terminal flat = exit_px/exit_et only when exit_et<=session_end, "
            "else UNKNOWN null; missing horizon/open = UNKNOWN null, never 0; retrospective "
            "best = max over chronological actual opens after the push (explicitly "
            "UNATTAINABLE)"
        ),
        "rulers": (
            "first causal +5%: ret_fill>=0.05; push: ret5>=0.03 upward crossing; genuine "
            "pullback/damage: dd_from_high<=-0.05; repair: ret5>0 & dd_velocity5>0 & "
            "dd_from_high<-0.03; second leg requires >=1 observed damage minute "
            "between legs"
        ),
        "census": census,
        "headline": headline,
        "continuation_curve": continuation,
        "paths": paths,
        "timing": timing,
        "contexts": contexts,
        "legs": leg_stats,
        "surrender": surrender,
        "concentration": concentration,
        "caveats": [
            "Descriptive rulers and dollars only: no policy, no tuned threshold, no winner class.",
            "Banking at the push is the zero-continuation reference. The wait-to-flat and "
            "per-horizon values execute at actual opens; best_fixed_horizon_minus_bank selects the "
            "best DECLARED horizon ex post and the retrospective max is a pure hindsight bound — "
            "both are rulers/upper references, never attainable strategies.",
            "N3 and N5 are independent ownership universes (nested cohorts) and are never pooled.",
            "Headline, surrender and path figures pool the four admission clocks explicitly "
            "within a single N3 or N5 view; per-clock detail stays in the contexts table and no "
            "N3+N5 pooled figure is emitted.",
            "Each horizon path statistic uses its own known support: above-push needs finite "
            "ret_fill, repair needs finite ret5/dd_velocity5/dd_from_high, damage needs finite "
            "dd_from_high; UNKNOWN values stay null and are counted separately.",
            "Day-balanced means weight dates equally; claim-balanced means weight claims equally; "
            "both are reported. UNKNOWN prices/horizons stay null and are counted.",
            "The staged cross-checks re-derive the first +5% minute and the prior damage count "
            "against the canonical event producer; mismatches would be counted, never hidden.",
            "No protected/validation outcomes are read; discovery days only.",
        ],
    }
    return artifact


# --------------------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--events-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--stage", type=Path, default=None)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    days = ls.discovery_days(a.data_root)
    if a.limit:
        days = days[: a.limit]
    ls.assert_coverage(days, a.data_root)
    em = json.loads((a.events_root / "manifest.json").read_text())
    if [d for d in days if d not in em["discovery_days"]]:
        raise SystemExit("events root does not cover the requested discovery days")
    stage = a.stage or a.out.parent / (a.out.stem + ".stage")
    (stage / "_done").mkdir(parents=True, exist_ok=True)
    source_sha = hashlib.sha256(
        Path(__file__).read_bytes() + Path(ls.__file__).read_bytes()
    ).hexdigest()
    provenance = {
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "lifecycle_study_sha256": hashlib.sha256(Path(ls.__file__).read_bytes()).hexdigest(),
        "events_manifest_sha256": hashlib.sha256(
            (a.events_root / "manifest.json").read_bytes()
        ).hexdigest(),
        "split_sha256": hashlib.sha256(
            (ls.v2_dir(a.data_root) / "split.json").read_bytes()
        ).hexdigest(),
        "clocks": list(CLOCKS),
        "horizons": list(HORIZONS),
        "side_fee": SIDE,
    }
    census = {
        "claims": 0,
        "pushes": 0,
        "clean": 0,
        "damaged": 0,
        "push_open_known": 0,
        "legs": 0,
        "legs_after_pullback": 0,
        "legs_immediate": 0,
        "roster_candidates": 0,
        "roster_filled": 0,
        "roster_only_unpriced": 0,
    }
    checks = {
        "push_event_only": 0,
        "push_recompute_only": 0,
        "push_agree": 0,
        "chronology_violations": 0,
        "damage_count_checked": 0,
        "damage_count_mismatch": 0,
        "damage_co_printed_at_push": 0,
    }
    resumed = 0
    for i, day in enumerate(days):
        cpath = stage / f"{day}.claims.parquet"
        lpath = stage / f"{day}.legs.parquet"
        marker = stage / "_done" / f"{day}.json"
        srcs = [
            ls.v2_dir(a.data_root) / "panel" / f"{day}.parquet",
            ls.v2_dir(a.data_root) / "roster" / f"{day}.parquet",
            a.events_root / "events" / f"{day}.parquet",
        ]
        input_hash = hashlib.sha256(
            b"".join(hashlib.sha256(p.read_bytes()).digest() for p in srcs)
        ).hexdigest()
        if marker.exists() and cpath.exists() and lpath.exists():
            info = json.loads(marker.read_text())
            if info.get("source_sha256") == source_sha and info.get("input_hash") == input_hash:
                for name in census:
                    census[name] += info["census"].get(name, 0)
                for name in checks:
                    checks[name] += info["checks"].get(name, 0)
                resumed += 1
                continue
        claims, legs, day_census, day_checks = stage_day(day, a.data_root, a.events_root)
        _write(cpath, claims)
        _write(lpath, legs)
        for name in census:
            census[name] += day_census.get(name, 0)
        for name in checks:
            checks[name] += day_checks.get(name, 0)
        marker.write_text(
            json.dumps(
                {
                    "status": "ok" if claims else "empty",
                    "source_sha256": source_sha,
                    "input_hash": input_hash,
                    "census": day_census,
                    "checks": day_checks,
                }
            )
            + "\n"
        )
        if i % 25 == 0:
            print(
                f"{i + 1}/{len(days)} {day} claims={len(claims)} push={day_census['pushes']} "
                f"legs={day_census['legs']}",
                flush=True,
            )

    claim_frames = [pl.read_parquet(stage / f"{d}.claims.parquet") for d in days]
    leg_frames = [pl.read_parquet(stage / f"{d}.legs.parquet") for d in days]
    claims = pl.concat(claim_frames, how="diagonal_relaxed") if claim_frames else pl.DataFrame()
    legs = pl.concat(leg_frames, how="diagonal_relaxed") if leg_frames else pl.DataFrame()
    claims.write_parquet(stage / "claims.parquet")
    legs.write_parquet(stage / "legs.parquet")
    provenance["staged_days"] = len(days)
    provenance["resumed_days"] = resumed
    artifact = finalize(
        stage,
        days,
        source_sha,
        provenance,
        {"days": len(days), "per_day": census, "checks": checks},
    )
    a.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.out.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(artifact, indent=2, allow_nan=False) + "\n")
    tmp.replace(a.out)
    print(
        "Push-leg dollar anatomy written",
        a.out,
        "claims",
        census["claims"],
        "pushes",
        census["pushes"],
        "clean",
        census["clean"],
        "damaged",
        census["damaged"],
        "legs",
        census["legs"],
    )


def _write(path: Path, rows: list[dict]):
    if rows:
        frame = pl.DataFrame(rows, infer_schema_length=None)
    else:
        frame = pl.DataFrame(
            {"day": pl.Series([], dtype=pl.Utf8), "clock": pl.Series([], dtype=pl.Int64)}
        )
    tmp = path.with_suffix(".tmp.parquet")
    frame.write_parquet(tmp)
    tmp.replace(path)


if __name__ == "__main__":
    main()
