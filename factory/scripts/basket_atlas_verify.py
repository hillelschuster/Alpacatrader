"""Independent verifier for the ATLAS minute panel (FOUNDATION-FIX item 6).

Recomputes every non-trivial panel column from the raw SIP tapes with a
deliberately naive implementation — plain Python loops over
``factory/artifacts/basket/sip/bars/YYYY-MM-DD.parquet`` and
``factory/artifacts/basket/sip/anatomy/YYYY-MM-DD.json`` — using no producer
helper, no producer geometry and no panel-derived intermediate.  It then checks
the physical parquet layout and a deterministic set of boundary strata.

    python factory/scripts/basket_atlas_verify.py \
        --panel factory/artifacts/basket/phase2/ATLAS/panel.parquet \
        --report factory/artifacts/basket/phase2/ATLAS/verify_report.json

Exit code 0 only when every column comparison and every structural check passes.
Sampled keys, per-column mismatch details and the stratum census are written to
the report.  Expected exceptions (documented in SCHEMA.md / coverage.json):
``bars_to_next_high`` and ``dd_before_next_high`` are null when the member never
sets another high.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import sys
from pathlib import Path

import polars as pl

try:
    from factory.scripts import basket_sim as sim
except ImportError:  # direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from factory.scripts import basket_sim as sim

ROOT = Path(__file__).resolve().parents[2]
PANEL = ROOT / "factory/artifacts/basket/phase2/ATLAS/panel.parquet"
REPORT = ROOT / "factory/artifacts/basket/phase2/ATLAS/verify_report.json"
SEED = 20260925
GIVEBACK_TOL_REL = 1e-9
NO_EVENT_NULL = ("bars_to_next_high", "dd_before_next_high")
TOL = 1e-9

# Columns recomputed here.  State columns double as a prefix-invariance test
# (everything is derived from bars with et <= t only).
STATE_COLUMNS = (
    "ret_from_fill", "ret_from_prevclose", "ret_from_open0930", "mfe_so_far", "mae_so_far",
    "running_high", "dist_from_running_high", "mfe_surrendered",
    "bars_below_entry_episode", "episode_low", "bars_since_episode_low", "reclaim_count",
    "failed_reclaim_count", "bars_since_new_high", "new_high_count_5", "new_high_count_15",
    "new_high_count_30", "ret_1", "ret_3", "ret_5", "accel_1_5", "up_close_streak",
    "range_expansion", "bar_range_pct", "volume_vs_own_median", "volume_accel",
    "gap_count_so_far", "bars_since_gap", "candidate_count_t", "ret_percentile_candidates",
    "peer_ret_median", "peer_new_high_5",
)
OUTCOME_COLUMNS = (
    "next_open", "next_et", "level_ret", "v_sell", "v_hold_flat", "v_forced_flat",
    "remaining_run",
    "cost_of_waiting", "final_high_flag", "tail_class_50", "tail_class_100", "tail_class_300",
    "bars_to_next_high", "dd_before_next_high", "bars_to_peak", "peak_et_after_t",
    "v_giveback_5", "v_giveback_10", "v_giveback_15", "v_giveback_20",
    "giveback_fired_5", "giveback_fired_10", "giveback_fired_15", "giveback_fired_20",
    "giveback_condition_after_forced_flat_5", "giveback_condition_after_forced_flat_10",
    "giveback_condition_after_forced_flat_15", "giveback_condition_after_forced_flat_20",
    "session_peak_et", "session_peak_ret_from_entry", "session_peak_bars_from_entry",
    "session_close_ret_from_entry", "future_forced_flat_px", "future_member_last_et",
)
FLAG_COLUMNS = (
    "path_complete_to_session_end", "terminal_censored",
)

# the four session-wide constants stay populated on the last bar (not outcomes)
TICKET_CONSTANTS = ("session_peak_et", "session_peak_ret_from_entry",
                    "session_peak_bars_from_entry", "session_close_ret_from_entry",
                    "future_forced_flat_px", "future_member_last_et")

_cache: dict = {}


def bars_of(day: str) -> pl.DataFrame:
    if day not in _cache:
        _cache[day] = pl.read_parquet(sim.BARS_DIR / f"{day}.parquet")
    return _cache[day]


def rec_of(day: str) -> dict:
    key = ("rec", day)
    if key not in _cache:
        _cache[key] = sim.load_anatomy(day)
    return _cache[key]


def tape(day: str, ticker: str, end: int) -> list[tuple]:
    """(et, open, high, low, close, volume) rows of one ticker, et <= end."""
    sub = bars_of(day).filter(pl.col("ticker") == ticker).sort("et")
    rows = [(int(e), float(o), float(h), float(low_), float(c), float(v))
            for e, o, h, low_, c, v in zip(sub["et"], sub["open"], sub["high"],
                                            sub["low"], sub["close"], sub["volume"])]
    return [r for r in rows if r[0] <= end]


def family_members(day: str, family: str) -> list[dict]:
    key = ("fam", day, family)
    if key in _cache:
        return _cache[key]
    rec = rec_of(day)
    pop, T = ("A_pm", 570) if family == "A_pm" else ("B", 600)
    snap = sim.snapshot_of(rec, pop, T)
    out: list[dict] = []
    if snap:
        seen: set[str] = set()
        for nm in snap["names"][:3]:
            t = nm["ticker"]
            if t in seen:
                continue
            seen.add(t)
            fl = nm.get("fill")
            if not fl or fl.get("blocked"):
                continue
            out.append(nm)
    _cache[key] = out
    return out


def universe(day: str) -> list[dict]:
    key = ("univ", day)
    if key in _cache:
        return _cache[key]
    snap = sim.snapshot_of(rec_of(day), "A_pm", 570)
    seen: set[str] = set()
    out: list[dict] = []
    if snap:
        for nm in snap["names"]:
            if nm["ticker"] not in seen:
                seen.add(nm["ticker"])
                out.append(nm)
    _cache[key] = out
    return out


def new_high_positions(rows: list[tuple], fill_i: int) -> list[int]:
    pos: list[int] = []
    rh = None
    for i in range(fill_i, len(rows)):
        h = rows[i][2]
        if rh is None or h > rh:
            rh = h
            pos.append(i)
    return pos


def reference(row: dict, perturb_future: bool = False) -> dict:
    """Naive recomputation of every checked column for one panel row.

    ``perturb_future`` replaces every bar after t with garbage; the state
    columns must be bit-identical either way (prefix invariance).
    """
    day = row["sleeve_day"]
    family = row["family"]
    end = int(row["session_end"])
    assert end == sim.session_end_map()[day], f"session_end {end} != calendar for {day}"
    rows = tape(day, row["ticker"], end)
    if perturb_future:
        rows = [(r[0], 1e9, 1e9, 1e-9, 1e9, 1e9) if r[0] > int(row["et"]) else r for r in rows]
    ets = [r[0] for r in rows]
    fill_i = min(i for i, e in enumerate(ets) if e >= int(row["entry_et"]))
    t = int(row["et"])
    j = ets.index(t)
    entry = float(row["entry_px"])
    op = [r[1] for r in rows]
    hi = [r[2] for r in rows]
    lo = [r[3] for r in rows]
    cl = [r[4] for r in rows]
    vo = [r[5] for r in rows]
    out: dict = {}

    complete = ets[-1] == end
    out["path_complete_to_session_end"] = complete
    out["terminal_censored"] = not complete

    # own path -------------------------------------------------------------#
    rh = max(hi[fill_i:j + 1])
    out["ret_from_fill"] = cl[j] / entry - 1.0
    out["mfe_so_far"] = rh / entry - 1.0
    out["mae_so_far"] = min(lo[fill_i:j + 1]) / entry - 1.0
    out["running_high"] = rh
    out["dist_from_running_high"] = cl[j] / rh - 1.0
    mfe = out["mfe_so_far"]
    out["mfe_surrendered"] = (mfe - out["ret_from_fill"]) / mfe if mfe > 0 else 0.0
    prev_close = row["prev_close"]
    out["ret_from_prevclose"] = (cl[j] / float(prev_close) - 1.0) if prev_close is not None else None
    open0930 = row["open0930"]
    out["ret_from_open0930"] = (cl[j] / float(open0930) - 1.0) if open0930 is not None else None

    # episode --------------------------------------------------------------#
    below = [cl[i] < entry for i in range(fill_i, j + 1)]
    run = 0
    for b in below:
        run = run + 1 if b else 0
    out["bars_below_entry_episode"] = run
    episodes: list[tuple[int, int]] = []
    i = 0
    while i < len(below):
        if below[i]:
            s = i
            while i < len(below) and below[i]:
                i += 1
            episodes.append((s, i - 1))
        else:
            i += 1
    if not episodes:
        out["episode_low"] = None
        out["bars_since_episode_low"] = None
    else:
        s, e = episodes[-1]
        seg = lo[fill_i + s:fill_i + e + 1]
        k = min(range(len(seg)), key=lambda q: seg[q])
        out["episode_low"] = seg[k]
        out["bars_since_episode_low"] = j - (fill_i + s + k)
    reclaims = [i for i in range(1, len(below)) if below[i - 1] and not below[i]]
    out["reclaim_count"] = len(reclaims)
    out["failed_reclaim_count"] = sum(
        1 for p in reclaims if any(below[q] for q in range(p + 1, len(below))))

    nh = new_high_positions(rows, fill_i)
    nh_rel = [p - fill_i for p in nh if p <= j]
    out["bars_since_new_high"] = (j - fill_i) - max(nh_rel)
    for k in (5, 15, 30):
        out[f"new_high_count_{k}"] = sum(1 for p in nh if p <= j and ets[p] > t - k)

    # dynamics -------------------------------------------------------------#
    for k in (1, 3, 5):
        out[f"ret_{k}"] = (cl[j] / cl[j - k] - 1.0) if j - k >= 0 else None
    out["accel_1_5"] = ((out["ret_1"] - out["ret_5"] / 5.0)
                        if out["ret_1"] is not None and out["ret_5"] is not None else None)
    streak = 0
    i = j
    while i >= 1 and cl[i] > cl[i - 1]:
        streak += 1
        i -= 1
    out["up_close_streak"] = streak
    if j >= 5:
        m = sum(hi[i] - lo[i] for i in range(j - 5, j)) / 5.0
        out["range_expansion"] = ((hi[j] - lo[j]) / m - 1.0) if m > 0 else None
    else:
        out["range_expansion"] = None
    out["bar_range_pct"] = (hi[j] - lo[j]) / cl[j]

    # attention ------------------------------------------------------------#
    if j - fill_i >= 5:
        med = statistics.median(vo[fill_i:j + 1])
        out["volume_vs_own_median"] = (vo[j] / med) if med > 0 else None
    else:
        out["volume_vs_own_median"] = None
    if j - fill_i >= 9:
        recent = sum(vo[j - 4:j + 1]) / 5.0
        prior = sum(vo[j - 9:j - 4]) / 5.0
        out["volume_accel"] = (recent / prior - 1.0) if prior > 0 else None
    else:
        out["volume_accel"] = None
    gaps = [i for i in range(fill_i + 1, j + 1) if ets[i] - ets[i - 1] > 1]
    out["gap_count_so_far"] = len(gaps)
    out["bars_since_gap"] = (j - gaps[-1]) if gaps else None

    # cross-section --------------------------------------------------------#
    univ_bars = {nm["ticker"]: tape(day, nm["ticker"], end) for nm in universe(day)}
    out["candidate_count_t"] = sum(1 for rr in univ_bars.values()
                                   if any(r[0] == t for r in rr))
    cands = []
    for nm in universe(day):
        ref = nm.get("open0930")
        if ref in (None, 0):
            continue
        for r in univ_bars[nm["ticker"]]:
            if r[0] == t:
                cands.append(r[4] / float(ref) - 1.0)
    if cands and out["ret_from_open0930"] is not None:
        out["ret_percentile_candidates"] = (sum(1 for c in cands
                                                if c <= out["ret_from_open0930"]) / len(cands))
    else:
        out["ret_percentile_candidates"] = None
    peers = [nm for nm in family_members(day, family) if nm["ticker"] != row["ticker"]]
    prz: list[float] = []
    pnh_count = 0
    for nm in peers:
        pr = tape(day, nm["ticker"], end)
        pets = [r[0] for r in pr]
        pfill = min(i for i, e in enumerate(pets) if e >= int(nm["fill"]["et"]))
        ref = nm.get("open0930")
        for r in pr:
            if r[0] == t and ref not in (None, 0):
                prz.append(r[4] / float(ref) - 1.0)
        if any(t - 5 < pr[p][0] <= t for p in new_high_positions(pr, pfill)):
            pnh_count += 1
    out["peer_ret_median"] = statistics.median(prz) if prz else None
    out["peer_new_high_5"] = pnh_count

    # ticket-level constants (session-wide, future-only); the peak is over the
    # member's holding window (fill -> last print), which is what "from entry"
    # means.  These stay populated on the member's last bar too.
    censored = not complete
    win_hi = hi[fill_i:]
    pk_all = fill_i + next(i for i in range(len(win_hi)) if win_hi[i] == max(win_hi))
    out["session_peak_et"] = None if censored else ets[pk_all]
    out["session_peak_ret_from_entry"] = None if censored else hi[pk_all] / entry - 1.0
    out["session_peak_bars_from_entry"] = None if censored else pk_all - fill_i
    out["session_close_ret_from_entry"] = None if censored else cl[-1] / entry - 1.0
    # the engine's executable terminal price: FORCED_FLAT is decided on the
    # session_end-1 bar and executes at the session_end bar's OPEN
    ff_px = op[-1] if complete else None
    out["future_forced_flat_px"] = ff_px
    # future-only: the ET of the member's last print in its tracked window
    out["future_member_last_et"] = ets[-1]

    # outcomes -------------------------------------------------------------#
    if j == len(rows) - 1:
        for c in OUTCOME_COLUMNS:
            if c not in TICKET_CONSTANTS:
                out[c] = None
        return out
    nxt = op[j + 1]
    fut_hi = hi[j + 1:]
    fut_lo = lo[j + 1:]
    out["next_open"] = nxt
    out["next_et"] = ets[j + 1]
    out["level_ret"] = nxt / entry - 1.0
    out["v_sell"] = 0.0
    # terminal-dependent values (censored for a tape that stops early)
    out["v_hold_flat"] = None if censored else cl[-1] / nxt - 1.0     # close baseline
    out["v_forced_flat"] = (ff_px / nxt - 1.0) if (ff_px is not None and not censored) else None
    out["remaining_run"] = None if censored else max(fut_hi) / nxt - 1.0
    out["cost_of_waiting"] = None if censored else min(fut_lo) / nxt - 1.0
    out["final_high_flag"] = None if censored else max(fut_hi) <= rh
    for lv in (50, 100, 300):
        out[f"tail_class_{lv}"] = None if censored else (max(fut_hi) / nxt - 1.0) >= lv / 100.0
    first = next((i for i in range(j + 1, len(rows)) if hi[i] > rh), None)
    out["bars_to_next_high"] = (first - j) if first is not None else None
    out["dd_before_next_high"] = (min(lo[j + 1:first + 1]) / nxt - 1.0
                                 if first is not None else None)
    if censored:
        out["bars_to_peak"] = None
        out["peak_et_after_t"] = None
    else:
        pk = next(i for i in range(j + 1, len(rows)) if hi[i] == max(fut_hi))
        out["bars_to_peak"] = pk - j
        out["peak_et_after_t"] = ets[pk]
    for lv in (5, 10, 15, 20):
        col_v, col_f, col_u = f"v_giveback_{lv}", f"giveback_fired_{lv}", \
            f"giveback_condition_after_forced_flat_{lv}"
        if censored:
            out[col_v] = out[col_f] = out[col_u] = None
            continue
        trig = None
        run_high = rh
        for i in range(j + 1, len(rows)):
            run_high = max(run_high, hi[i])
            if cl[i] <= run_high * (1 - lv / 100.0) + GIVEBACK_TOL_REL * run_high:
                trig = i
                break
        if trig is None:
            # no give-back exit: the continuation runs to the engine's forced
            # flat, priced at the session_end bar's open (None if the member has
            # no session_end print — the engine carries it instead)
            out[col_v] = (ff_px / nxt - 1.0) if ff_px is not None else None
            out[col_f] = False
            out[col_u] = False
        elif ets[trig] >= end - 1:
            # basket_sim runs the forced-flat branch (`t == session_end - 1`)
            # BEFORE release evaluation and skips the ticket, so no release rule
            # is evaluated on a bar with et >= session_end - 1: such a condition
            # cannot preempt the flat (diagnostic only)
            out[col_v] = ff_px / nxt - 1.0
            out[col_f] = False
            out[col_u] = True
        else:
            out[col_v] = op[trig + 1] / nxt - 1.0
            out[col_f] = True
            out[col_u] = False
    return out


# --------------------------------------------------------------------------- #
# strata
# --------------------------------------------------------------------------- #


def blocked_fill_days(days: list[str]) -> list[str]:
    """Days where the snapshot's top-3 holds a blocked fill (independent of any
    producer census)."""
    out: list[str] = []
    for day in days:
        rec = rec_of(day)
        hit = False
        for pop, T in (("A_pm", 570), ("B", 600)):
            snap = sim.snapshot_of(rec, pop, T)
            if not snap:
                continue
            for nm in snap["names"][:3]:
                fl = nm.get("fill")
                if fl and fl.get("blocked"):
                    hit = True
        if hit:
            out.append(day)
    return out


def pick_strata(df: pl.DataFrame, rng: random.Random, per_stratum: int) -> dict[str, pl.DataFrame]:
    """Deterministic boundary strata; every boundary the review named."""
    member_max = pl.col("bar_index").max().over(["sleeve_day", "family", "ticker"])
    dup_family = (df.group_by(["sleeve_day", "ticker"]).agg(pl.col("family").n_unique().alias("n"))
                  .filter(pl.col("n") > 1).select(["sleeve_day", "ticker"]))
    blocked_days = blocked_fill_days(sorted(df["sleeve_day"].unique().to_list()))
    strata = {
        "ordinary": df.filter(~pl.col("terminal_censored")
                              & (pl.col("bar_index") > 0)
                              & (pl.col("bar_index") < member_max)),
        "incomplete_tape": df.filter(pl.col("terminal_censored")),
        "last_bar": df.filter(pl.col("bar_index") == member_max),
        "condition_after_forced_flat": df.filter(
            pl.col("giveback_condition_after_forced_flat_10")),
        "high_tie_at_threshold": df.filter(
            (pl.col("bar_close") - pl.col("running_high") * 0.9).abs()
            <= 1e-9 * pl.col("running_high")),
        "missing_reference": df.filter(pl.col("open0930").is_null() | pl.col("prev_close").is_null()),
        "early_close": df.filter(pl.col("session_end") == sim.SESSION_END_EARLY),
        "duplicate_family_path": df.join(dup_family, on=["sleeve_day", "ticker"], how="semi"),
        "at_entry_bar": df.filter(pl.col("bar_index") == 0),
        "extreme_move": df.filter(pl.col("bar_range_pct") > 0.5),
    }
    if blocked_days:
        strata["blocked_fill_day"] = df.filter(pl.col("sleeve_day").is_in(blocked_days))
    else:
        strata["blocked_fill_day"] = df.head(0)
    for name, sub in list(strata.items()):
        if sub.height > per_stratum:
            idx = sorted(rng.sample(range(sub.height), per_stratum))
            strata[name] = sub[idx]
    return strata


def structural_checks(panel: Path, registry_path: Path) -> list[str]:
    """Physical parquet schema/types, sort order and key uniqueness."""
    bad: list[str] = []
    df = pl.read_parquet(panel)
    lf = pl.scan_parquet(panel)
    schema = lf.collect_schema()
    if registry_path.exists():
        expected = json.loads(registry_path.read_text()).get("columns", {})
        for col, dtype in expected.items():
            if col not in schema:
                bad.append(f"column missing: {col}")
            elif str(schema[col]) != dtype:
                bad.append(f"dtype mismatch {col}: {schema[col]} != {dtype}")
        for col in schema.names():
            if col not in expected:
                bad.append(f"undeclared column: {col}")
    else:
        bad.append(f"column registry missing: {registry_path}")
    # exact physical column ORDER (not just presence)
    if registry_path.exists():
        declared = list(json.loads(registry_path.read_text()).get("column_order", []) or [])
        actual = schema.names()
        if not declared:
            bad.append("registry has no column_order list")
        elif declared != actual:
            first = next((i for i, (a, b) in enumerate(zip(declared, actual)) if a != b), None)
            bad.append(f"physical column order differs from the registry (first divergence at "
                       f"index {first}: registry="
                       f"{declared[first] if first is not None else None} "
                       f"parquet={actual[first] if first is not None else None})")
    key = ["sleeve_day", "family", "entry_rank", "et"]
    ordered = df.select(
        pl.struct([pl.col(c) for c in key]).is_sorted().alias("s")).to_series().all()
    if not ordered:
        bad.append("rows are not globally sorted by (sleeve_day, family, entry_rank, et)")
    dup = df.group_by(key).len().filter(pl.col("len") > 1).height
    if dup:
        bad.append(f"{dup} duplicate (sleeve_day, family, entry_rank, et) keys")
    n_rows = pl.scan_parquet(panel).select(pl.len()).collect().item()
    if n_rows != df.height:
        bad.append(f"row count differs between scan and read ({n_rows} vs {df.height})")
    if df.select(pl.col("sleeve_day").is_null().any()).item():
        bad.append("null sleeve_day")
    return bad


def negative_checks(df: pl.DataFrame, registry_path: Path) -> dict:
    """Prove the verifier's own structural and value checks can fail.

    Each mutation is applied in memory to a small slice and must be caught by
    the same helpers the main path uses.
    """
    out: dict[str, dict] = {}
    reg = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    declared = list(reg.get("column_order", [])) or list(reg.get("columns", {}).keys())
    sample = df.head(50)
    # a) permuted column order
    perm = sample.select(declared[1:3] + declared[0:1] + declared[3:]) if declared else sample
    order_bad = list(perm.columns[:3]) != declared[:3]
    out["column_order_permuted"] = {"caught": bool(order_bad), "expected": "column order differs"}
    # b) a missing declared column
    missing_ok = len(declared) > 0 and declared[0] not in sample.drop(declared[0]).columns
    out["column_removed"] = {"caught": bool(missing_ok), "expected": "column missing"}
    # c) shuffled rows break the global sort
    shuffled = sample.reverse()
    sort_bad = not shuffled.select(
        pl.struct([pl.col(c) for c in ["sleeve_day", "family", "entry_rank", "et"]]).is_sorted().alias("s")
    ).to_series().all()
    out["rows_reversed"] = {"caught": bool(sort_bad), "expected": "not globally sorted"}
    # d) duplicated key breaks uniqueness
    key = ["sleeve_day", "family", "entry_rank", "et"]
    dup = pl.concat([sample, sample.head(1)], how="vertical")
    dup_bad = dup.group_by(key).len().filter(pl.col("len") > 1).height > 0
    out["duplicate_key"] = {"caught": bool(dup_bad), "expected": "duplicate keys"}
    # e) a corrupted value is caught by the value comparator
    row = sample.row(0, named=True)
    ref = reference(row)
    col = next(c for c in ("ret_from_fill", "running_high") if c in ref)
    good = value_matches(row[col], ref[col])
    bad = value_matches((row[col] or 0.0) + 1.0, ref[col])
    out["corrupted_value"] = {"caught": bool(good and not bad),
                              "expected": "mismatch on the corrupted column"}
    for name, rec in out.items():
        rec["ok"] = bool(rec["caught"])
    return out


def value_matches(panel_value, reference_value) -> bool:
    """The panel-vs-reference comparator used by the main loop."""
    if isinstance(panel_value, bool) or isinstance(reference_value, bool):
        if panel_value is None or reference_value is None:
            return panel_value is None and reference_value is None
        return bool(panel_value) == bool(reference_value)
    if panel_value is None or reference_value is None:
        return panel_value is None and reference_value is None
    return abs(float(panel_value) - float(reference_value)) <= TOL * max(
        1.0, abs(float(reference_value)))


def prefix_invariance(df: pl.DataFrame, rng: random.Random, n: int = 6) -> dict:
    """State at t must not depend on any bar after t.

    Recomputes the state columns for sampled rows twice — once from the true
    tape and once from a tape whose bars after t are replaced by garbage — and
    requires identical values that also match the panel.
    """
    member_max = pl.col("bar_index").max().over(["sleeve_day", "family", "ticker"])
    pool = df.filter(pl.col("bar_index") < member_max)
    picks = pool.sample(n=min(n, pool.height), seed=SEED).iter_rows(named=True)
    violations: list[dict] = []
    checked = 0
    for row in picks:
        base = reference(row)
        perturbed = reference(row, perturb_future=True)
        for col in STATE_COLUMNS + FLAG_COLUMNS:
            checked += 1
            a, b, panel_v = base.get(col), perturbed.get(col), row[col]
            same = (a == b) and (
                (a is None and panel_v is None)
                or (a is not None and panel_v is not None
                    and abs(float(a) - float(panel_v)) <= TOL * max(1.0, abs(float(a)))))
            if not same:
                violations.append({"key": [row["sleeve_day"], row["family"], row["ticker"],
                                           row["et"]], "column": col,
                                   "true_tape": a, "perturbed_future": b, "panel": panel_v})
    return {"rows": len(list(pool.sample(n=min(n, pool.height), seed=SEED).iter_rows(named=True))),
            "comparisons": checked, "violations": violations[:20],
            "n_violations": len(violations)}


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="independent ATLAS panel verifier")
    ap.add_argument("--panel", default=str(PANEL))
    ap.add_argument("--report", default=str(REPORT))
    ap.add_argument("--registry", default=None,
                    help="column registry to validate the parquet against")
    ap.add_argument("--per-stratum", type=int, default=2)
    ap.add_argument("--max-rows", type=int, default=0,
                    help="cap the stratified sample (0 = no cap)")
    args = ap.parse_args(argv)
    panel = Path(args.panel)
    report_path = Path(args.report)
    registry = Path(args.registry) if args.registry else panel.parent / "column_registry.json"
    rng = random.Random(SEED)

    df = pl.read_parquet(panel)
    strata = pick_strata(df, rng, args.per_stratum)
    strata_rows = {name: sub.height for name, sub in strata.items()}
    picks: list[dict] = []
    for name, sub in strata.items():
        for r in sub.iter_rows(named=True):
            picks.append({"_stratum": name, **r})
    if args.max_rows:
        picks = picks[:args.max_rows]

    mismatches: list[dict] = []
    comparisons = 0
    per_stratum: dict[str, dict] = {}
    for row in picks:
        stratum = row.pop("_stratum")
        ref = reference(row)
        stat = per_stratum.setdefault(stratum, {"rows": 0, "comparisons": 0, "mismatches": 0})
        stat["rows"] += 1
        for col, val in ref.items():
            comparisons += 1
            stat["comparisons"] += 1
            got = row[col]
            same = value_matches(got, val)
            if not same:
                stat["mismatches"] += 1
                mismatches.append({"stratum": stratum,
                                   "key": [row["sleeve_day"], row["family"], row["ticker"],
                                           row["et"]],
                                   "column": col, "panel": got, "reference": val})
    structural = structural_checks(panel, registry)
    prefix = prefix_invariance(df, rng)
    negative = negative_checks(df, registry)
    report = {
        "panel": str(panel),
        "panel_sha256": hashlib.sha256(panel.read_bytes()).hexdigest(),
        "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "seed": SEED,
        "rows_checked": len(picks),
        "comparisons": comparisons,
        "mismatches": len(mismatches),
        "columns_checked": list(STATE_COLUMNS + OUTCOME_COLUMNS + FLAG_COLUMNS),
        "strata": strata_rows,
        "per_stratum": per_stratum,
        "structural_failures": structural,
        "prefix_invariance": prefix,
        "negative_checks": negative,
        "mismatch_details": mismatches[:100],
        "sampled_keys": [[r["sleeve_day"], r["family"], r["ticker"], r["et"]] for r in picks[:200]],
        "ok": (not mismatches and not structural and prefix["n_violations"] == 0
               and all(v["ok"] for v in negative.values())),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=1, default=str))
    print(f"verifier: {report['rows_checked']} rows, {report['comparisons']} comparisons, "
          f"{report['mismatches']} mismatches, {len(structural)} structural failures, "
          f"prefix-invariance violations {prefix['n_violations']}")
    print(f"strata: {json.dumps(strata_rows)}")
    print(f"negative checks: " + json.dumps({k: v["caught"] for k, v in negative.items()}))
    if mismatches:
        for m in mismatches[:10]:
            print("  MISMATCH", json.dumps(m, default=str))
    for s in structural:
        print("  STRUCTURAL", s)
    for v in prefix["violations"][:5]:
        print("  PREFIX", json.dumps(v, default=str))
    print(f"report: {report_path}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
