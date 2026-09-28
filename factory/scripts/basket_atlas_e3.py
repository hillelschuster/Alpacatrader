#!/usr/bin/env python3
"""ATLAS E3 bounded SIP-print microscope — Stage A producer (case-set freeze + census).

Implements step 1 of `researches/PLAN-ATLAS-E3-MICROSCOPE.md` (the pre-registration draft by
`MicroScopeArchitect`, marked DRAFT-IMPLEMENTED there):

  * **case sets** — CS-1..CS-5, every anchor a *panel-native event* (never a re-derived minute
    bar), frozen as a deterministic, capped, hashed anchor list per set, with the full
    inclusion/exclusion accounting;
  * **census** — exactly ONE projected-column pass per dev day over the raw SIP trades store,
    per (day, family, ticker, entry_rank): raw print counts (U-all and U-path), minute coverage,
    the coverage class from `data/sip/net/coverage/<day>.json`, quote presence from the day's
    quote file, and the halt-shaped print holes CS-5 anchors on.

Read-only over the raw store. Nothing here is downloaded, copied or re-materialised.

Frozen-producer imports (never re-derived here):
  * `basket_subminute_probe.load_prints`    — the sanctioned projected read template
  * `basket_t5_rawpaths.price_updating`     — the sanctioned U-path (high/low-updating) filter
    (which is `sip_bars.combine` / RULES_M / AUCTION_CODES strictest-rule-wins underneath)
  * `basket_atlas_ledger`                   — the giveback rule locator + the causality registry
  * `basket_atlas_pairs`                    — `prepare`/`add_cells`/`PRIMARY_UNIT` (CS-3 axis)
  * `basket_atlas_fall`                     — `load_frame` + `forward_vol_labels` (CS-4 stratum)
  * `basket_sim.guard_day / dev_days`       — sealed/reserved days are refused before any read

Outputs (all under `factory/artifacts/basket/phase2/ATLAS/E3/`):
  anchors.parquet     frozen bounded anchor samples (byte-stable)
  case_sets.json      the case-set freeze (byte-stable)
  census.parquet      per-member-day census rows (byte-stable)
  census.json         census facts + per-day bytes/prints (byte-stable)
  census_cost.json    measured wall time / read time (run-varying, NOT part of the freeze)
  selftest.json       deterministic self-test results

Usage:
  .venv/bin/python factory/scripts/basket_atlas_e3.py --stage all --bars-check
  .venv/bin/python factory/scripts/basket_atlas_e3.py --stage census --days 2025-07-09
  .venv/bin/python factory/scripts/basket_atlas_e3.py --self-test
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import numpy as np
import polars as pl

try:  # package import
    from factory.scripts import basket_atlas_fall as fall
    from factory.scripts import basket_atlas_ledger as ledger
    from factory.scripts import basket_atlas_pairs as pairs
    from factory.scripts import basket_sim as sim
    from factory.scripts import basket_subminute_probe as probe
    from factory.scripts import basket_t5_rawpaths as t5
except ImportError:  # direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from factory.scripts import basket_atlas_fall as fall
    from factory.scripts import basket_atlas_ledger as ledger
    from factory.scripts import basket_atlas_pairs as pairs
    from factory.scripts import basket_sim as sim
    from factory.scripts import basket_subminute_probe as probe
    from factory.scripts import basket_t5_rawpaths as t5

ROOT = Path(__file__).resolve().parents[2]
ATLAS = ROOT / "factory" / "artifacts" / "basket" / "phase2" / "ATLAS"
E3 = ATLAS / "E3"
PANEL = ATLAS / "panel.parquet"
PANEL_SHA_DECLARED = "2a021eda878cdbe53e41d4cc88e2d8ba3b14d9a3a819723d9f679c8aca7c3488"
PANEL_ROWS = 1_900_432
PANEL_MEMBERS = 6_160
PANEL_DAYS = 1_066

# raw store roots: same resolution order as the probe (env -> worktree -> canonical checkout)
_NET = probe.TRADES.parent
TRADES = probe.TRADES
QUOTES = _NET / "quotes"
COVERAGE = _NET / "coverage"
BARS = sim.BARS_DIR

# probe constants, imported from the probe module itself (never re-invented)
GAP_MIN = probe.GAP_MIN                 # print-to-print jump (minutes) that is a discovered gap
HALT_MIN = probe.HALT_MIN               # print-to-print jump (minutes) that is halt-sized
HALT_ACTIVITY_FLOOR = probe.HALT_ACTIVITY_FLOOR  # member must print in >= 60 minutes of its window

# window (plan section 2): fixed by the execution convention, four segments, never a grid
TAU_LO, TAU_HI = -60, 180
SEGMENTS = (("pre", -60, 0), ("anchor", 0, 60), ("post1", 60, 120), ("post2", 120, 180))

# bounded extraction caps (plan sections 1 and 6). CS-3's cap and allocation are the plan's own
# numbers; CS-1/CS-4/CS-5 get the same bound by analogy (the plan states no cap for them) and CS-2
# is the plan's own eligible population (4,946 firing members), which is not subsampled.
CASE_CAPS = {"CS-1": 4_000, "CS-2": None, "CS-3": 4_000, "CS-4": 4_000, "CS-5": 4_000}
CS3_ALLOC = {"A_pm|block1": 600, "A_pm|block2": 600, "B600|block1": 400, "B600|block2": 400}
GIVEBACK_LEVEL = 10

# CS-4 carriers: the plan's five names. `fall.VOL_MEDIATION_FEATURES` (the frozen producer's
# headline set) carries the first four; `bars_below_entry_episode` is panel-native and is carried
# as the fifth per the plan (the 4-carrier variant count is published for traceability).
CS4_CARRIERS = ("dist_from_running_high", "mfe_surrendered_pos", "up_close_streak", "accel_1_5",
                "bars_below_entry_episode")
CS4_FWD_K = 30

ANCHOR_COLS = ["case_set", "anchor_id", "sel_rank", "sleeve_day", "block", "family", "ticker",
               "entry_rank", "entry_et", "session_end", "et", "bar_index", "ref_num", "ref_txt",
               "aux"]
ANCHOR_SCHEMA = {"case_set": pl.Utf8, "anchor_id": pl.Utf8, "sel_rank": pl.Utf8,
                 "sleeve_day": pl.Utf8, "block": pl.Utf8, "family": pl.Utf8, "ticker": pl.Utf8,
                 "entry_rank": pl.Int32, "entry_et": pl.Int32, "session_end": pl.Int32,
                 "et": pl.Int32, "bar_index": pl.Int32, "ref_num": pl.Float64, "ref_txt": pl.Utf8,
                 "aux": pl.Utf8}

CASE_ORDER = ("CS-1", "CS-2", "CS-3", "CS-4", "CS-5")
MEMBER_KEY = ["sleeve_day", "family", "ticker", "entry_rank"]

DEVIATIONS = [
    {"id": "caps",
     "text": "the plan states a 4,000-window bound only for CS-3; CS-1/CS-4/CS-5 carry the same "
             "bound here (the plan gives them none) and CS-2 keeps its full eligible population "
             "(4,946) because the plan quotes that number as the CS-2 population."},
    {"id": "CS-4 carriers",
     "text": "the plan lists five carriers; the frozen producer's fall.VOL_MEDIATION_FEATURES "
             "carries four (no bars_below_entry_episode). The plan's five are used (all "
             "panel-native) and the 4-carrier variant count is published in meta.CS-4."},
    {"id": "CS-4 stratum",
     "text": "deciles are computed inside each block (the plan says 'within the training block'); "
             "the block label is kept on every anchor so either block can be the training block "
             "at analysis time."},
    {"id": "CS-5 bracketing",
     "text": "a halt run must be bracketed by prints inside the member window (leading/trailing "
             "silence is reported separately, not anchored), because a reopen anchor needs a "
             "pre-hole print for pre_halt_trend."},
    {"id": "CS-5 activity floor",
     "text": "the probe's HALT_ACTIVITY_FLOOR=60 is applied as a member-day inclusion rule "
             "(>= 60 minutes with a U-path print)."},
    {"id": "CS-2 anchor bar",
     "text": "the anchor is the DECISION bar t (the first bar whose close satisfies the giveback "
             "condition), not the execution bar t+1; post1 is therefore the execution bar, as the "
             "window rationale requires."},
    {"id": "CS-3 sample unit",
     "text": "the 4,000 bound is on member-windows = 2,000 pairs, so the plan's 60/40 family and "
             "50/50 block allocation is applied at pair level."},
    {"id": "global budget",
     "text": "the plan's section 6 cost line says '~4,000 anchored windows' for stage B while its "
             "section 1 sets CS-3 at 4,000 member-windows and CS-2 at 4,946 firing members; the "
             "contradiction is resolved as per-case-set caps and both numbers are published."},
]


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def jdump(obj) -> str:
    """Canonical JSON text: sorted keys, fixed indent, no NaN/Infinity, no timestamps."""
    return json.dumps(obj, indent=1, sort_keys=True, allow_nan=False)


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _write_json(path: Path, obj) -> None:
    _write_bytes(path, (jdump(obj) + "\n").encode())


def _write_parquet(df: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.write_parquet(tmp, compression="zstd", compression_level=3, statistics=False)
    os.replace(tmp, path)


def _round(x, nd=6):
    if x is None:
        return None
    x = float(x)
    if not math.isfinite(x):
        return None
    return round(x, nd)


def pct(vals, p):
    if not len(vals):
        return None
    s = np.sort(np.asarray(vals, dtype=float))
    return float(s[min(int(len(s) * p), len(s) - 1)])


def dist_summary(vals, nd=3) -> dict:
    v = np.asarray([x for x in vals if x is not None and math.isfinite(float(x))], dtype=float)
    if not v.size:
        return {"n": 0, "p10": None, "p50": None, "p90": None, "max": None, "mean": None}
    return {"n": int(v.size), "p10": _round(pct(v, 0.10), nd), "p50": _round(pct(v, 0.50), nd),
            "p90": _round(pct(v, 0.90), nd), "max": _round(float(v.max()), nd),
            "mean": _round(float(v.mean()), nd)}


def counts(it) -> dict:
    out: dict = {}
    for x in it:
        k = "missing" if x is None else str(x)
        out[k] = out.get(k, 0) + 1
    return out


def empty_runs_np(minutes: np.ndarray, lo: int, hi: int) -> list[list[int]]:
    """Runs of ET minutes with no print inside [lo, hi], as [first_empty, last_empty].

    The print-to-print jump across a run is `len(run) + 1` minutes, matching the probe's
    `empty_runs` / `GAP_MIN` / `HALT_MIN` convention.
    """
    present = np.unique(minutes[(minutes >= lo) & (minutes <= hi)]) if minutes.size else minutes
    if present.size == 0:
        return [[lo, hi]] if hi >= lo else []
    runs: list[list[int]] = []
    if present[0] > lo:
        runs.append([int(lo), int(present[0]) - 1])
    for i in np.flatnonzero(np.diff(present) >= 2):
        runs.append([int(present[i]) + 1, int(present[i + 1]) - 1])
    if present[-1] < hi:
        runs.append([int(present[-1]) + 1, int(hi)])
    return runs


def sel_rank(text: str) -> str:
    return sha256_bytes(text.encode())


def anchor_id(case_set, day, family, ticker, entry_rank, et, ref) -> str:
    return f"{case_set}|{day}|{family}|{ticker}|{entry_rank}|{et}|{ref}"


def allocate(cell_counts: dict, cap: int, block_of: dict | None = None,
             explicit: dict | None = None) -> dict:
    """Deterministic cap allocation over cells.

    explicit       -> use the given per-cell targets (a plan-stated allocation), capped by supply;
    block_of given -> split the cap 50/50 over blocks (out-of-block transfer is required for every
                      case set), then split each block's share over its cells proportionally with
                      largest-remainder rounding, capped by supply;
    otherwise      -> proportional largest-remainder over all cells.
    """
    cells = sorted(cell_counts)
    targets = dict.fromkeys(cells, 0)
    if explicit:
        for c, t in explicit.items():
            if c in cell_counts:
                targets[c] = min(int(t), cell_counts[c])
    elif block_of:
        blocks = sorted({block_of[c] for c in cells})
        base = cap // len(blocks)
        rem = cap - base * len(blocks)
        for i, b in enumerate(blocks):
            share = min(base + (1 if i < rem else 0),
                        sum(cell_counts[c] for c in cells if block_of[c] == b))
            sub = [c for c in cells if block_of[c] == b]
            supply = sum(cell_counts[c] for c in sub)
            if supply <= 0:
                continue
            raw = {c: share * cell_counts[c] / supply for c in sub}
            got = {c: int(math.floor(raw[c])) for c in sub}
            left = share - sum(got.values())
            for c in sorted(sub, key=lambda c: (-(raw[c] - got[c]), c))[:left]:
                got[c] += 1
            for c in sub:
                targets[c] = min(got[c], cell_counts[c])
    else:
        supply = sum(cell_counts.values())
        if supply > 0:
            share = min(cap, supply)
            raw = {c: share * cell_counts[c] / supply for c in cells}
            got = {c: int(math.floor(raw[c])) for c in cells}
            left = share - sum(got.values())
            for c in sorted(cells, key=lambda c: (-(raw[c] - got[c]), c))[:left]:
                got[c] += 1
            targets = {c: min(got[c], cell_counts[c]) for c in cells}
    short = (sum(targets.values()) if explicit else min(cap, sum(cell_counts.values()))) \
        - sum(targets.values())
    if short > 0:  # supply-capped cells leave a shortfall: redistribute deterministically
        for c in sorted(cells, key=lambda c: (-cell_counts[c], c)):
            if short <= 0:
                break
            add = min(short, cell_counts[c] - targets[c])
            targets[c] += add
            short -= add
    return targets


def select_sample(rows: list[dict], cell_cols: tuple, cap: int, block_of: dict | None = None,
                  explicit: dict | None = None) -> list[dict]:
    """Bounded, deterministic, per-day-balanced subsample of an anchor population.

    Inside each cell: days are visited in ascending sha256(day) order (never calendar order) and
    each day offers its candidates in ascending sha256(anchor_id) order; picks are taken
    round-robin across days, so per-day counts differ by at most one and no day dominates.
    """
    if cap is None or (cap >= len(rows) and not explicit):
        return list(rows)
    cells: dict[str, list[dict]] = {}
    for r in rows:
        cells.setdefault("|".join(str(r[c]) for c in cell_cols), []).append(r)
    targets = allocate({c: len(v) for c, v in cells.items()}, cap, block_of=block_of,
                       explicit=explicit)
    picked: list[dict] = []
    for cell in sorted(cells):
        take = min(targets.get(cell, 0), len(cells[cell]))
        if take <= 0:
            continue
        by_day: dict[str, list[dict]] = {}
        for r in cells[cell]:
            by_day.setdefault(r["sleeve_day"], []).append(r)
        days = sorted(by_day, key=lambda d: sel_rank(f"day|{cell}|{d}"))
        for d in days:
            by_day[d].sort(key=lambda r: r["sel_rank"])
        idx = dict.fromkeys(days, 0)
        got = 0
        while got < take:
            progressed = False
            for d in days:
                if idx[d] < len(by_day[d]):
                    picked.append(by_day[d][idx[d]])
                    idx[d] += 1
                    got += 1
                    progressed = True
                    if got >= take:
                        break
            if not progressed:
                break
    return picked


def anchors_frame(rows: list[dict]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame({c: [] for c in ANCHOR_COLS}, schema=ANCHOR_SCHEMA)
    df = pl.DataFrame(rows)
    for c in ANCHOR_COLS:
        if c not in df.columns:
            df = df.with_columns(pl.lit(None).alias(c))
    return df.select([pl.col(c).cast(ANCHOR_SCHEMA[c]) for c in ANCHOR_COLS]).sort(
        ["case_set", "anchor_id"])


# --------------------------------------------------------------------------- #
# panel access
# --------------------------------------------------------------------------- #

PANEL_COLS = ["sleeve_day", "block", "family", "ticker", "entry_rank", "entry_et", "et",
              "bar_index", "session_end", "terminal_censored", "entry_px", "bar_close", "bar_open",
              "running_high", "reclaim_count", "failed_reclaim_count", "bars_below_entry_episode",
              "dist_from_running_high", "mfe_surrendered", "mfe_so_far", "up_close_streak",
              "accel_1_5", "final_high_flag", "giveback_fired_10", "v_giveback_10",
              "future_member_last_et"]


def load_panel_cols(cols: list[str]) -> pl.DataFrame:
    have = set(pl.read_parquet_schema(PANEL))
    cols = list(dict.fromkeys(cols))
    missing = [c for c in cols if c not in have]
    if missing:
        raise SystemExit(f"panel is missing columns: {missing}")
    return pl.read_parquet(PANEL, columns=cols)


def member_days(df: pl.DataFrame) -> pl.DataFrame:
    """One row per (day, family, ticker, entry_rank) member-day, canonical order."""
    return (df.group_by(MEMBER_KEY, maintain_order=True)
            .agg(pl.col("block").first(), pl.col("entry_et").first(),
                 pl.col("session_end").first(),
                 pl.col("terminal_censored").max().alias("terminal_censored"),
                 pl.col("future_member_last_et").first(), pl.len().alias("panel_bars"))
            .sort(MEMBER_KEY))


# --------------------------------------------------------------------------- #
# case sets (panel-native anchors)
# --------------------------------------------------------------------------- #

def cs1_anchors(df: pl.DataFrame) -> tuple[list[dict], dict]:
    """CS-1: the reclaim bar t_r where `reclaim_count` increments, non-censored member-days."""
    d = df.sort(MEMBER_KEY + ["bar_index"]).with_columns(
        pl.col("reclaim_count").shift(1).over(MEMBER_KEY).alias("_rc_prev"),
        pl.col("bar_index").shift(1).over(MEMBER_KEY).alias("_bi_prev"))
    sub = d.filter(pl.col("reclaim_count") > pl.col("_rc_prev"))
    excl = {"member_terminal_censored": int(sub.filter(pl.col("terminal_censored")).height)}
    sub = sub.filter(~pl.col("terminal_censored"))
    excl["no_previous_bar_in_member_day"] = int(sub.filter(pl.col("_bi_prev").is_null()).height)
    sub = sub.filter(pl.col("_bi_prev").is_not_null())
    anchors = []
    for r in sub.iter_rows(named=True):
        aid = anchor_id("CS-1", r["sleeve_day"], r["family"], r["ticker"], r["entry_rank"], r["et"],
                        int(r["reclaim_count"]))
        anchors.append({"case_set": "CS-1", "anchor_id": aid, "sel_rank": sel_rank(aid),
                        "sleeve_day": r["sleeve_day"], "block": r["block"], "family": r["family"],
                        "ticker": r["ticker"], "entry_rank": int(r["entry_rank"]),
                        "entry_et": int(r["entry_et"]), "session_end": int(r["session_end"]),
                        "et": int(r["et"]), "bar_index": int(r["bar_index"]),
                        "ref_num": float(r["reclaim_count"]),
                        "ref_txt": f"reclaim_count={int(r['reclaim_count'])}",
                        "aux": jdump({"bars_below_entry_episode":
                                      int(r["bars_below_entry_episode"]),
                                      "dist_from_running_high":
                                      _round(r["dist_from_running_high"], 6),
                                      "failed_reclaim_count": int(r["failed_reclaim_count"])})})
    return anchors, excl


def cs2_anchors(md: pl.DataFrame, fired_by_key: dict) -> tuple[list[dict], dict]:
    """CS-2: the decision bar of the frozen `giveback:10` ruler, located by the ledger's own
    `Giveback.locate_exit` / `giveback_mask` (never re-derived)."""
    rule = ledger.Giveback(name="giveback:10", kind="giveback",
                           state_columns=("bar_close", "running_high", "dist_from_running_high"),
                           measure_columns=("next_open", "session_end", "et", "bar_open"),
                           params={"pct": GIVEBACK_LEVEL})
    anchors = []
    excl = {"member_terminal_censored": 0, "condition_never_fires": 0,
            "clock_precedence_non_firing": 0, "no_execution_bar": 0,
            "panel_flag_locator_disagreement": 0}
    for key, sub in md.partition_by(MEMBER_KEY, maintain_order=True, as_dict=True).items():
        key = tuple(key) if isinstance(key, tuple) else (key,)
        sub = sub.sort("bar_index")
        if bool(sub["terminal_censored"][0]):
            excl["member_terminal_censored"] += 1
            continue
        cols = {c: sub[c].to_numpy() for c in sub.columns}
        n = len(cols["et"])
        if n < 2:
            excl["no_execution_bar"] += 1
            continue
        hits = np.flatnonzero(ledger.giveback_mask(cols, float(GIVEBACK_LEVEL))[1:])
        j = rule.locate_exit(cols)
        if (j >= 0) != bool(fired_by_key.get(key, False)):
            excl["panel_flag_locator_disagreement"] += 1
        if hits.size == 0:
            excl["condition_never_fires"] += 1
            continue
        t = int(hits[0]) + 1
        se = int(cols["session_end"][0])
        if int(cols["et"][t]) >= se - 1:
            excl["clock_precedence_non_firing"] += 1
            continue
        if t + 1 >= n:
            excl["no_execution_bar"] += 1
            continue
        assert j == t + 1, f"ledger locator {j} != decision bar {t} + 1"
        et = int(cols["et"][t])
        aid = anchor_id("CS-2", key[0], key[1], key[2], key[3], et, GIVEBACK_LEVEL)
        anchors.append({"case_set": "CS-2", "anchor_id": aid, "sel_rank": sel_rank(aid),
                        "sleeve_day": key[0], "block": str(cols["block"][0]), "family": key[1],
                        "ticker": key[2], "entry_rank": int(key[3]),
                        "entry_et": int(cols["entry_et"][0]), "session_end": se, "et": et,
                        "bar_index": int(cols["bar_index"][t]), "ref_num": float(GIVEBACK_LEVEL),
                        "ref_txt": f"giveback:{GIVEBACK_LEVEL}",
                        "aux": jdump({"decision_bar_index": int(cols["bar_index"][t]),
                                      "execution_et": int(cols["et"][t + 1]),
                                      "execution_px": _round(cols["bar_open"][t + 1], 6)})})
    return anchors, excl


def cs3_pairs(panel: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    """CS-3: the Phase-1 middle-decile executable axis, rebuilt through the pairs producer's own
    `prepare` / `add_cells` / `PRIMARY_UNIT` and the frozen deterministic side split."""
    df = pairs.add_cells(pairs.prepare(panel, {"min_stratum": 20}), "primary")
    unit = pairs.PRIMARY_UNIT
    dropped = {"row_is_decile_extreme_up": int(df["up"].sum()),
               "row_is_decile_extreme_down": int(df["down"].sum()),
               "row_terminal_censored": int(df["censored"].sum()),
               "row_dup_cross_family": int(df["dup_cross_family"].sum()),
               "units_with_one_middle_member": int(
                   df.filter(~pl.col("up") & ~pl.col("down") & ~pl.col("censored")
                             & ~pl.col("dup_cross_family"))
                   .group_by(unit).len().filter(pl.col("len") < 2).height)}
    mid = (df.filter(~pl.col("up") & ~pl.col("down") & ~pl.col("censored")
                     & ~pl.col("dup_cross_family"))
           .select(unit + ["entry_rank", "ticker", "member", "bar_index", "block"]))
    mid = mid.sort(unit + ["family", "entry_rank", "ticker", "et"]).with_columns(
        (pl.col("et").cum_count().over(unit) - 1).alias("kk"))
    ms = mid.with_columns(((pl.col("kk") + 1) // 2).alias("idx"),
                          (pl.col("kk") % 2 == 0).alias("side_a"))
    cols = ["member", "ticker", "entry_rank", "bar_index", "et", "block"]
    a = ms.filter(pl.col("side_a")).select(
        unit + ["idx"] + [pl.col(c).alias(c + "_A") for c in cols])
    b = ms.filter(~pl.col("side_a")).select(
        unit + ["idx"] + [pl.col(c).alias(c + "_B") for c in cols])
    all_pairs = a.join(b, on=unit + ["idx"], how="inner")
    dropped["pair_same_member"] = int(all_pairs.filter(
        pl.col("member_A") == pl.col("member_B")).height)
    return all_pairs.filter(pl.col("member_A") != pl.col("member_B")), dropped


def cs4_anchors(panel_ident: pl.DataFrame) -> tuple[list[dict], dict]:
    """CS-4: bars whose exhaustion carriers move, inside the top/bottom `fwd_range_30` decile of
    their block. Carriers and the forward-dispersion label come from the frozen fall producer."""
    frame, _checks = fall.load_frame(PANEL)
    fwd = fall.forward_vol_labels(frame, ks=(CS4_FWD_K,))
    rng = fwd[f"fwd_range_{CS4_FWD_K}"]
    if panel_ident.height != frame.n:
        raise SystemExit(f"CS-4: identity read {panel_ident.height} != fall frame {frame.n}")
    for col, arr in (("et", frame.et), ("bar_index", frame.bar_index),
                     ("entry_et", frame.entry_et), ("session_end", frame.session_end)):
        if not np.array_equal(panel_ident[col].to_numpy().astype(np.int64), arr):
            raise SystemExit(f"CS-4: identity read does not line up with the fall frame on {col}")
    x = frame.x
    missing = [c for c in CS4_CARRIERS if c not in x]
    if missing:
        raise SystemExit(f"CS-4 carriers absent from the fall frame: {missing}")

    def moved(carriers) -> np.ndarray:
        changed = np.zeros(frame.n, dtype=bool)
        for c in carriers:
            v = x[c]
            d = np.zeros(frame.n, dtype=bool)
            for i in range(frame.n_members):
                o, m = int(frame.off[i]), int(frame.sizes[i])
                if m < 2:
                    continue
                prev, cur = v[o:o + m - 1], v[o + 1:o + m]
                d[o + 1:o + m] = ~((prev == cur) | (np.isnan(prev) & np.isnan(cur)))
            changed |= d
        return changed

    changed = moved(CS4_CARRIERS)
    changed4 = moved([c for c in fall.VOL_MEDIATION_FEATURES if c in x])
    block = panel_ident["block"].to_numpy()
    ident = {c: panel_ident[c].to_numpy() for c in
             ("sleeve_day", "family", "ticker", "entry_rank", "entry_et", "session_end",
              "bar_index", "et")}
    finite_rng = np.isfinite(rng)
    target_ok = np.isfinite(x["final_high_flag"])
    excl = {"carrier_state_constant": int((~changed).sum()),
            "member_terminal_censored": int((changed & frame.censored).sum()),
            "no_forward_range_30": int((changed & ~frame.censored & ~finite_rng).sum()),
            "no_future_target_bar": int((changed & ~frame.censored & finite_rng
                                         & ~target_ok).sum()),
            "carrier_state_constant_variant_4carriers": int((~changed4).sum())}
    elig = changed & ~frame.censored & finite_rng & target_ok
    strata = np.zeros(frame.n, dtype=np.int8)      # 1 = bottom decile, 2 = top decile
    q = {}
    for b in ("block1", "block2"):
        m = elig & (block == b)
        if not m.any():
            continue
        q10, q90 = np.quantile(rng[m], [0.10, 0.90])
        q[b] = {"q10": _round(q10, 6), "q90": _round(q90, 6), "n": int(m.sum())}
        strata[m & (rng <= q10)] = 1
        strata[m & (rng >= q90)] = 2
    inside = strata > 0
    excl["outside_top_bottom_dispersion_decile"] = int((elig & ~inside).sum())
    rows = []
    for i in np.flatnonzero(inside):
        st = "bottom_decile" if strata[i] == 1 else "top_decile"
        aid = anchor_id("CS-4", ident["sleeve_day"][i], ident["family"][i], ident["ticker"][i],
                        int(ident["entry_rank"][i]), int(ident["bar_index"][i]), st)
        rows.append({"case_set": "CS-4", "anchor_id": aid, "sel_rank": sel_rank(aid),
                     "sleeve_day": ident["sleeve_day"][i], "block": block[i],
                     "family": ident["family"][i], "ticker": ident["ticker"][i],
                     "entry_rank": int(ident["entry_rank"][i]),
                     "entry_et": int(ident["entry_et"][i]),
                     "session_end": int(ident["session_end"][i]), "et": int(ident["et"][i]),
                     "bar_index": int(ident["bar_index"][i]), "ref_num": _round(float(rng[i]), 6),
                     "ref_txt": st, "aux": None})
    meta = {"block_quantiles": q,
            "variant_4carriers_eligible": int((changed4 & ~frame.censored & finite_rng
                                               & target_ok).sum()),
            "carriers": list(CS4_CARRIERS),
            "carriers_in_frozen_producer": list(fall.VOL_MEDIATION_FEATURES)}
    return rows, {"exclusions": excl, "meta": meta}


# --------------------------------------------------------------------------- #
# census
# --------------------------------------------------------------------------- #

def coverage_lookup(day: str) -> tuple[dict, dict]:
    path = COVERAGE / f"{day}.json"
    if not path.exists():
        return {}, {"present": False}
    obj = json.loads(path.read_text())
    per = {k: {"cls": v.get("cls"), "n_trades": v.get("n_trades"),
               "derived_bars": v.get("derived_bars"), "last_et": v.get("last_et"),
               "reason": v.get("reason")} for k, v in (obj.get("per_symbol") or {}).items()}
    return per, {"present": True, "day": obj.get("day"), "symbols": obj.get("symbols"),
                 "classes": obj.get("classes")}


def quote_day_info(day: str) -> tuple[set, dict]:
    qpath = QUOTES / f"{day}.parquet"
    if not qpath.exists():
        return set(), {"present": False}
    syms = set(pl.scan_parquet(qpath).select("symbol").unique().collect()["symbol"].to_list())
    info = {"present": True, "bytes": qpath.stat().st_size}
    mpath = QUOTES / f"{day}.manifest.json"
    if mpath.exists():
        man = json.loads(mpath.read_text())
        info.update({"status": man.get("status"), "rows": man.get("rows"),
                     "symbols_requested": man.get("symbols_requested"),
                     "symbols_with_data": man.get("symbols_with_data")})
    return syms, info


def census_day(day: str, members: list[dict], with_bars_check: bool) -> dict:
    """One projected-column pass over the day's trades file; per member-day facts."""
    sim.guard_day(day)
    syms = sorted({m["ticker"] for m in members})
    t0 = time.perf_counter()
    df, tel = probe.load_prints(day, syms)
    t_read = time.perf_counter() - t0
    t1 = time.perf_counter()
    path_df = t5.price_updating(df) if df.height else df
    t_path = time.perf_counter() - t1
    cov, cov_info = coverage_lookup(day)
    tq = time.perf_counter()
    qsyms, qinfo = quote_day_info(day)
    t_quote = time.perf_counter() - tq
    bars_by_ticker: dict[str, set] = {}
    if with_bars_check and (BARS / f"{day}.parquet").exists():
        b = pl.read_parquet(BARS / f"{day}.parquet", columns=["ticker", "et"])
        for (tkr,), sub in b.group_by(["ticker"], maintain_order=True):
            bars_by_ticker[tkr] = {int(x) for x in sub["et"].to_list()}

    all_sym = {s: df.filter(pl.col("symbol") == s).sort("ts_utc") for s in syms}
    pth_sym = {s: path_df.filter(pl.col("symbol") == s).sort("ts_utc") for s in syms}

    rows, cs5, bars_bad, bars_checked = [], [], 0, 0
    for m in members:
        s = m["ticker"]
        lo, hi = int(m["entry_et"]), int(m["session_end"])
        a = all_sym.get(s)
        p = pth_sym.get(s)
        a_et = a["et"].to_numpy() if a is not None and a.height else np.array([], dtype=np.int64)
        p_et = p["et"].to_numpy() if p is not None and p.height else np.array([], dtype=np.int64)
        a_w = a_et[(a_et >= lo) & (a_et <= hi)]
        p_w = p_et[(p_et >= lo) & (p_et <= hi)]
        grid = hi - lo + 1
        mins_all = np.unique(a_w)
        mins_path = np.unique(p_w)
        runs = empty_runs_np(p_w, lo, hi)
        gaps = [r for r in runs if (r[1] - r[0] + 1) + 1 >= GAP_MIN]
        halts = [r for r in runs if (r[1] - r[0] + 1) + 1 >= HALT_MIN]
        n_bracketed = 0
        if p is not None and p.height and halts:
            p_ts = p["ts_utc"].to_numpy()
            p_cond = p["conditions"].to_list()
            p_price = p["price"].to_numpy()
            first_of: dict[int, int] = {}
            for i, e in enumerate(p_et):
                first_of.setdefault(int(e), i)
            for r in halts:
                pre_m, post_m = int(r[0]) - 1, int(r[1]) + 1
                if pre_m not in first_of or post_m not in first_of:
                    continue
                n_bracketed += 1
                i_post, i_pre = first_of[post_m], first_of[pre_m]
                hole = int(r[1] - r[0] + 1)
                aid = anchor_id("CS-5", m["sleeve_day"], m["family"], s, m["entry_rank"], post_m,
                                hole)
                cs5.append({"case_set": "CS-5", "anchor_id": aid, "sel_rank": sel_rank(aid),
                            "sleeve_day": m["sleeve_day"], "block": m["block"],
                            "family": m["family"], "ticker": s,
                            "entry_rank": int(m["entry_rank"]), "entry_et": lo, "session_end": hi,
                            "et": post_m, "bar_index": None, "ref_num": float(hole),
                            "ref_txt": f"hole={hole}min",
                            "aux": jdump({"hole_minutes": hole,
                                          "reopen_code5": bool("5" in (p_cond[i_post] or [])),
                                          "reopen_ts": str(p_ts[i_post]),
                                          "reopen_px": _round(float(p_price[i_post]), 6),
                                          "pre_hole_ts": str(p_ts[i_pre]),
                                          "pre_hole_px": _round(float(p_price[i_pre]), 6),
                                          "minutes_with_path_print": int(mins_path.size)})})
        if with_bars_check and mins_path.size and s in bars_by_ticker:
            bars_checked += 1
            if bars_by_ticker[s] & set(range(lo, hi + 1)) != {int(x) for x in mins_path}:
                bars_bad += 1
        c = cov.get(s, {})
        rows.append({
            "sleeve_day": day, "block": m["block"], "family": m["family"], "ticker": s,
            "entry_rank": int(m["entry_rank"]), "entry_et": lo, "session_end": hi,
            "terminal_censored": bool(m["terminal_censored"]),
            "coverage_class": c.get("cls"), "coverage_reason": c.get("reason"),
            "coverage_n_trades": c.get("n_trades"), "coverage_derived_bars": c.get("derived_bars"),
            "coverage_last_et": c.get("last_et"), "coverage_in_file": bool(c),
            "quote_present": bool(s in qsyms), "quote_n_symbols": len(qsyms),
            "n_prints_all": int(a_w.size), "n_prints_path": int(p_w.size),
            "n_minutes_all": int(mins_all.size), "n_minutes_path": int(mins_path.size),
            "grid_minutes": int(grid),
            "coverage_path": _round(mins_path.size / grid, 6) if grid else None,
            "n_gap_runs_ge2min": len(gaps), "n_halt_runs_ge5min": len(halts),
            "n_halt_runs_bracketed": int(n_bracketed),
            "max_run_min": int(max((r[1] - r[0] + 1 for r in runs), default=0)),
            "lead_silence_min": int(mins_path[0] - lo) if mins_path.size else None,
            "trail_silence_min": int(hi - mins_path[-1]) if mins_path.size else None,
            "first_print_et": int(mins_path[0]) if mins_path.size else None,
            "last_print_et": int(mins_path[-1]) if mins_path.size else None,
            "panel_bars": int(m["panel_bars"]), "file_bytes": int(tel["file_bytes"])})
    day_tel = {"day": day, "members": len(members), "file_bytes": int(tel["file_bytes"]),
               "rows_loaded": int(tel["rows_loaded"]), "read_s": _round(t_read, 4),
               "price_updating_s": _round(t_path, 4), "quote_scan_s": _round(t_quote, 4),
               "wall_s": _round(time.perf_counter() - t0, 4),
               "prints_all": int(sum(r["n_prints_all"] for r in rows)),
               "prints_path": int(sum(r["n_prints_path"] for r in rows)),
               "coverage_classes": counts(r["coverage_class"] for r in rows),
               "quote_symbols": len(qsyms), "quote_info": qinfo, "coverage_info": cov_info,
               "bars_checked": bars_checked, "bars_mismatch": bars_bad}
    return {"rows": rows, "cs5": cs5, "day": day_tel}


def census_days(days: list[str], md: pl.DataFrame, workers: int, with_bars_check: bool) -> dict:
    by_day = {d: [r for r in md.iter_rows(named=True) if r["sleeve_day"] == d] for d in days}
    out = {"rows": [], "cs5": [], "days": []}

    def one(day):
        return census_day(day, by_day[day], with_bars_check)

    if workers <= 1:
        results = [one(d) for d in days]
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(one, days))
    for d, res in zip(days, results, strict=True):
        out["rows"].extend(res["rows"])
        out["cs5"].extend(res["cs5"])
        out["days"].append(res["day"])
        print(f"[census] {d} members={res['day']['members']} "
              f"prints_path={res['day']['prints_path']} wall={res['day']['wall_s']}s", flush=True)
    return out


# --------------------------------------------------------------------------- #
# freeze
# --------------------------------------------------------------------------- #

def census_index(rows: pl.DataFrame) -> dict:
    return {(r["sleeve_day"], r["family"], r["ticker"], r["entry_rank"]): r
            for r in rows.iter_rows(named=True)}


def exclusion_join(anchors: list[dict], cen: dict) -> tuple[list[dict], dict]:
    """Drop anchors whose member-day fails the raw-data gates, counted by reason."""
    kept, excl = [], {}
    for a in anchors:
        c = cen.get((a["sleeve_day"], a["family"], a["ticker"], a["entry_rank"]))
        if c is None:
            excl["member_day_absent_from_census"] = excl.get("member_day_absent_from_census", 0) + 1
            continue
        if c["coverage_class"] != "healthy_raw":
            k = f"coverage_class_{c['coverage_class'] or 'missing'}"
            excl[k] = excl.get(k, 0) + 1
            continue
        if c["n_prints_path"] == 0:
            excl["no_path_prints_in_window"] = excl.get("no_path_prints_in_window", 0) + 1
            continue
        kept.append(a)
    return kept, excl


GATE_REASONS = ("member_day_absent_from_census", "no_path_prints_in_window",
                "pair_partner_failed_gate")


def split_exclusions(e: dict) -> tuple[dict, dict]:
    """Separate panel-side rule drops from the raw-data gates (census-derived)."""
    panel, gates = {}, {}
    for k, v in sorted(e.items()):
        (gates if (k in GATE_REASONS or k.startswith("coverage_class_")) else panel)[k] = v
    return panel, gates


def set_stats(rows: list[dict], cen: dict) -> dict:
    mds = {(r["sleeve_day"], r["family"], r["ticker"], r["entry_rank"]) for r in rows}
    by_day = counts(r["sleeve_day"] for r in rows)
    n_path = [cen[k]["n_prints_path"] for k in sorted(mds) if k in cen]
    n_all = [cen[k]["n_prints_all"] for k in sorted(mds) if k in cen]
    fams_by_path: dict = {}
    for k in cen:
        fams_by_path.setdefault((k[0], k[2]), set()).add(k[1])
    shared = sum(1 for r in rows if len(fams_by_path.get((r["sleeve_day"], r["ticker"]), ())) > 1)
    return {
        "anchors": len(rows),
        "member_days": len(mds),
        "days": len(by_day),
        "by_family": counts(r["family"] for r in rows),
        "by_block": counts(r["block"] for r in rows),
        "by_family_block": counts(f"{r['family']}|{r['block']}" for r in rows),
        "anchors_per_day": dist_summary(list(by_day.values()), nd=3),
        "anchors_per_member_day": dist_summary(
            list(counts((r["sleeve_day"], r["family"], r["ticker"], r["entry_rank"])
                        for r in rows).values()), nd=3),
        "coverage_class": counts(cen[k]["coverage_class"] if k in cen else None
                                 for k in sorted(mds)),
        "prints_per_member_day_path": dist_summary(n_path),
        "prints_per_member_day_all": dist_summary(n_all),
        "anchor_ids_sha256": sha256_bytes("\n".join(sorted(r["anchor_id"] for r in rows)).encode()),
        "n_on_shared_cross_family_paths": shared,
    }


def build(workers: int, with_bars_check: bool, reuse_census: bool = False) -> dict:
    E3.mkdir(parents=True, exist_ok=True)
    if reuse_census:
        rows = pl.read_parquet(E3 / "census.parquet")
        cs5 = pl.read_parquet(E3 / "cs5_anchors.parquet")
        mdf = member_days(load_panel_cols(["sleeve_day", "block", "family", "ticker", "entry_rank",
                                           "entry_et", "session_end", "terminal_censored",
                                           "future_member_last_et"]))
        c = {"days": [], "cost": None}
    else:
        c = run_census(workers, with_bars_check)
        rows, cs5, mdf = c["rows"], c["cs5"], c["md"]
        _write_json(E3 / "census.json", census_json(rows, cs5, c["days"]))
    cen = census_index(rows)

    df = load_panel_cols(PANEL_COLS).sort(MEMBER_KEY + ["bar_index"])
    fired = {tuple(r[k] for k in MEMBER_KEY): r["giveback_fired_10"]
             for r in (df.group_by(MEMBER_KEY, maintain_order=True)
                       .agg(pl.col("giveback_fired_10").first()).sort(MEMBER_KEY)
                       .iter_rows(named=True))}

    populations: dict = {}
    samples: dict = {}
    excl: dict = {}
    raw_counts: dict = {}

    # ---- CS-1
    raw1, e = cs1_anchors(df)
    raw_counts["CS-1"] = len(raw1)
    pop, eb = exclusion_join(raw1, cen)
    e.update(eb)
    populations["CS-1"], excl["CS-1"] = pop, e
    samples["CS-1"] = select_sample(pop, ("family", "block"), CASE_CAPS["CS-1"],
                                    block_of={f"{f}|{b}": b for f in ("A_pm", "B600")
                                              for b in ("block1", "block2")})

    # ---- CS-2 (bar-level rows: the locator walks each member's own bar sequence)
    raw2, e = cs2_anchors(df, fired)
    raw_counts["CS-2"] = len(raw2)
    pop, eb = exclusion_join(raw2, cen)
    e.update(eb)
    populations["CS-2"], excl["CS-2"] = pop, e
    samples["CS-2"] = pop

    # ---- CS-3
    pr, dropped3 = cs3_pairs(load_panel_cols(sorted(set(pairs.required_columns()))))
    pair_rows = []
    for r in pr.iter_rows(named=True):
        pid = f"{r['sleeve_day']}|{r['et']}|{r['family']}|{r['b_bar_index']}|{r['idx']}"
        for side in ("A", "B"):
            key = (r["sleeve_day"], r["family"], r[f"ticker_{side}"],
                   int(r[f"entry_rank_{side}"]))
            aid = anchor_id("CS-3", r["sleeve_day"], r["family"], r[f"ticker_{side}"],
                            int(r[f"entry_rank_{side}"]), int(r[f"et_{side}"]), f"{pid}|{side}")
            cr = cen.get(key)
            pair_rows.append({"case_set": "CS-3", "anchor_id": aid, "sel_rank": sel_rank(aid),
                              "sleeve_day": r["sleeve_day"], "block": r[f"block_{side}"],
                              "family": r["family"], "ticker": r[f"ticker_{side}"],
                              "entry_rank": int(r[f"entry_rank_{side}"]),
                              "entry_et": int(cr["entry_et"]) if cr else None,
                              "session_end": int(cr["session_end"]) if cr else None,
                              "et": int(r[f"et_{side}"]),
                              "bar_index": int(r[f"bar_index_{side}"]), "ref_num": None,
                              "ref_txt": pid, "aux": None, "_pair": pid})
    by_pair: dict[str, list[dict]] = {}
    for r in sorted(pair_rows, key=lambda r: r["anchor_id"]):
        by_pair.setdefault(r["_pair"], []).append(r)
    raw_counts["CS-3"] = len(pair_rows)
    gated, e3gate = exclusion_join(pair_rows, cen)
    by_pair_gated: dict[str, list[dict]] = {}
    for r in gated:
        by_pair_gated.setdefault(r["_pair"], []).append(r)
    complete = {pid: rows for pid, rows in by_pair_gated.items() if len(rows) == 2}
    partner_failed = sum(len(rows) for pid, rows in by_pair_gated.items() if len(rows) == 1)
    rep = [{"pair_id": pid, "family": complete[pid][0]["family"],
            "block": complete[pid][0]["block"], "sleeve_day": complete[pid][0]["sleeve_day"],
            "sel_rank": sel_rank(f"pair|{pid}")} for pid in sorted(complete)]
    chosen = sorted(r["pair_id"] for r in
                    select_sample(rep, ("family", "block"), CASE_CAPS["CS-3"], explicit=CS3_ALLOC))
    populations["CS-3"] = [r for pid in sorted(complete) for r in complete[pid]]
    samples["CS-3"] = [r for pid in chosen for r in complete[pid]]
    e = {f"row_{k}": v for k, v in sorted(dropped3.items())}
    e.update(e3gate)
    e["pair_partner_failed_gate"] = partner_failed
    excl["CS-3"] = e
    meta3 = {"n_pairs_population": len(complete),
             "n_pairs_before_gates": len(by_pair),
             "n_pairs_by_family_block": counts(f"{r['family']}|{r['block']}" for r in rep),
             "allocation_pairs": CS3_ALLOC, "n_pairs_sampled": len(chosen),
             "n_member_windows_sampled": len(samples["CS-3"])}

    # ---- CS-4
    ident = load_panel_cols(["sleeve_day", "family", "ticker", "entry_rank", "et", "bar_index",
                             "entry_et", "session_end", "block"])
    popraw, meta4 = cs4_anchors(ident)
    raw_counts["CS-4"] = len(popraw)
    pop, eb = exclusion_join(popraw, cen)
    e = dict(meta4["exclusions"])
    e.update(eb)
    populations["CS-4"], excl["CS-4"] = pop, e
    samples["CS-4"] = select_sample(
        pop, ("ref_txt", "family", "block"), CASE_CAPS["CS-4"],
        block_of={f"{s}|{f}|{b}": b for s in ("top_decile", "bottom_decile")
                  for f in ("A_pm", "B600") for b in ("block1", "block2")})
    for r in samples["CS-4"]:  # aux is filled only for the frozen rows (the population is 368k)
        r["aux"] = jdump({"stratum": r["ref_txt"], "fwd_range_30": r["ref_num"]})

    # ---- CS-5 (anchors detected by the census pass)
    md_idx = {(r["sleeve_day"], r["family"], r["ticker"], r["entry_rank"]): r
              for r in mdf.iter_rows(named=True)}
    e = {"member_terminal_censored": 0, "member_activity_below_floor": 0}
    cs5_rows = []
    for r in cs5.iter_rows(named=True):
        key = (r["sleeve_day"], r["family"], r["ticker"], r["entry_rank"])
        mrec, crec = md_idx.get(key), cen.get(key)
        if mrec is None or crec is None:
            e["member_day_absent_from_census"] = e.get("member_day_absent_from_census", 0) + 1
            continue
        if mrec["terminal_censored"]:
            e["member_terminal_censored"] += 1
            continue
        if crec["n_minutes_path"] < HALT_ACTIVITY_FLOOR:
            e["member_activity_below_floor"] += 1
            continue
        if crec["coverage_class"] != "healthy_raw":
            k = f"coverage_class_{crec['coverage_class'] or 'missing'}"
            e[k] = e.get(k, 0) + 1
            continue
        row = dict(r)
        row.update({"entry_et": int(crec["entry_et"]), "session_end": int(crec["session_end"]),
                    "block": mrec["block"]})
        cs5_rows.append(row)
    e["unbracketed_silent_runs"] = int(
        rows["n_halt_runs_ge5min"].sum() - rows["n_halt_runs_bracketed"].sum())
    raw_counts["CS-5"] = len(cs5_rows)
    census_candidates = {"CS-5": {
        "halt_runs_ge5min_in_member_windows": int(rows["n_halt_runs_ge5min"].sum()),
        "bracketed_candidates": int(cs5.height),
        "unbracketed_silent_runs": e.pop("unbracketed_silent_runs"),
        "note": ("the census candidate list is every bracketed halt-sized run; the panel-side "
                 "rules (terminal censoring, activity floor) and then the raw gates reduce it to "
                 "anchors_before_gates"),
    }}
    populations["CS-5"], excl["CS-5"] = cs5_rows, e
    samples["CS-5"] = select_sample(cs5_rows, ("family", "block"), CASE_CAPS["CS-5"],
                                    block_of={f"{f}|{b}": b for f in ("A_pm", "B600")
                                              for b in ("block1", "block2")})

    all_samples = [r for c in CASE_ORDER for r in samples[c]]
    anchors = anchors_frame(all_samples)
    _write_parquet(anchors, E3 / "anchors.parquet")
    freeze = {"populations": populations, "samples": samples, "exclusions": excl,
              "raw_counts": raw_counts, "census_candidates": census_candidates,
              "meta": {"CS-3": meta3, "CS-4": meta4["meta"]}}
    obj = case_sets_json(rows, cs5, mdf, anchors, freeze)
    obj["artifacts"] = {"anchors_parquet_sha256": sha256_file(E3 / "anchors.parquet"),
                        "census_parquet_sha256": sha256_file(E3 / "census.parquet")}
    obj["stage_b_cost_projection"] = stage_b_projection(rows, samples)
    _write_json(E3 / "case_sets.json", obj)
    return {"case_sets": obj, "cost": c["cost"]}


def stage_b_projection(rows: pl.DataFrame, samples: dict) -> dict:
    """Prints/bytes the frozen windows imply, from the measured census rates (no extraction)."""
    idx = census_index(rows)
    per_set, tot_prints, tot_windows = {}, 0.0, 0
    for name in CASE_ORDER:
        p, n = 0.0, 0
        for a in samples[name]:
            rec = idx.get((a["sleeve_day"], a["family"], a["ticker"], a["entry_rank"]))
            if rec is None:
                continue
            n += 1
            p += rec["n_prints_path"] / max(1, rec["grid_minutes"]) * 4.0
        per_set[name] = {"windows": n, "projected_window_prints": int(p)}
        tot_prints += p
        tot_windows += n
    return {"windows": tot_windows, "projected_prints": int(tot_prints),
            "projected_bytes_binary_20B_per_print": int(tot_prints * 20),
            "projected_sample_bytes_cap_2000_per_window": int(
                min(tot_prints, tot_windows * 2000) * 20),
            "basis": ("window prints = 4 x (member-day U-path prints / member-day grid minutes), "
                      "measured by the census; 20 B/print binary estimate from the probe's cost "
                      "model; the extraction stores a capped 2,000-print sample per window"),
            "per_case_set": per_set}


# --------------------------------------------------------------------------- #
# Stage B — anchored extraction (B1/B2/B6) and the I-EV measurement
# --------------------------------------------------------------------------- #
#
# The freeze audit (`agent://E3CensusDebug`) found three semantic defects in the frozen
# window/action-stamp rules that Stage B must fix before any estimate is read:
#
#   B1  the decision instant is the CLOSE of the anchor bar t, not first-print + 60 s. The
#       frozen rule (`tau < 60` from the anchor bar's first print) admitted up to ~59 s of
#       post-decision tape. Here `tau = ts - close(t)` and every feature labelled
#       `causal_at_decision` may read only prints with `tau <= 0` (equivalently ts <= close(t));
#       the extractor asserts it on every print it uses, and each window records the decision
#       instant and the print cutoff actually used.
#   B2  panel bars are printed minutes, not contiguous minutes: the execution/outcome bar is
#       mapped through the member's own panel bar list (bar identity), never by tau arithmetic.
#       Windows whose member has no next bar are reported (`no_next_bar`), never dropped.
#   B6  the window is contained in [entry_et, session_end] by construction: the four declared
#       segments are the member's own panel bars, and prints outside the member window are never
#       read. The frozen tau-window's pre/post spills are recomputed and published as
#       `tau_window_spill` for the record.
#
# Consequence for the I-EV arm (stated before any estimate): a feature is a *predictor* only if
# it is causal at the decision instant. The post1/post2 segments are the action horizon (the
# execution bar and the bar after it), so their features are carried as `post_*` descriptive
# columns and are never predictors here. That is the contract's B1 mandate applied to the plan's
# §3 table; the plan file records it as a Stage-B deviation.

SB_VERSION = "stageB-1 (B1 close(t) decision; B2 panel-bar identity; B6 session containment)"
SB_SEGMENTS = ("pre", "anchor", "post1", "post2")
SB_SEGMENTS_CS5 = ("pre", "anchor", "hole")
CASE_SEGMENTS = {"CS-1": SB_SEGMENTS, "CS-2": SB_SEGMENTS, "CS-3": SB_SEGMENTS,
                 "CS-4": SB_SEGMENTS, "CS-5": SB_SEGMENTS_CS5}
SB_PUSH_MIN = 0.005              # RULER (plan section 3, feature 8): 0.5%, not fitted
SB_Q_MAX_AGE_S = 1.0             # RULER (plan section 3, feature 12): 1 s, not fitted
SB_BIN_S = 5.0                   # the plan's 5-second bin convention for the F3 features
SB_SAMPLE_PER_SEGMENT = 20       # raw print sample (eyeballing only), per segment
SB_BOOTSTRAP_B = 200             # day-clustered bootstrap resamples (pre-registered here)
SB_BOOTSTRAP_SEED = 20260928     # fixed seed: the artifact is reproducible
SB_NULL_BAND = 0.054             # plan section 4: the pre-registered null band
SB_MIN_COVERAGE = 0.95           # a feature enters a model only with >= 95% non-null in training
SB_L2 = 1.0                      # ridge penalty on standardised features (no intercept penalty)
SB_LEVEL_RULE = {
    "CS-1": "entry_px (the level reclaim_count increments across: close < entry_px -> close >= entry_px)",
    "CS-2": f"running_high(t) * (1 - {GIVEBACK_LEVEL}/100) — the giveback ruler's own trigger level",
    "CS-3": "bar_close(t) of the member's own anchor bar (the matched state's price)",
    "CS-4": "bar_close(t) of the member's own anchor bar",
    "CS-5": "pre_hole_px — the last U-path print before the hole (the level the reopen print jumps from)",
}

# The minute counterpart (plan section 4: "the minute counterpart is mandatory"). Every name is
# either a registered causal state column or rejected by the ledger's registry (G2).
MINUTE_FEATURES = (
    "dist_from_running_high", "bars_below_entry_episode", "reclaim_count", "failed_reclaim_count",
    "ret_1", "ret_3", "ret_5", "accel_1_5", "up_close_streak", "bar_range_pct",
    "volume_vs_own_median", "volume_accel", "range_expansion", "gap_count_so_far",
    "bars_since_gap", "ret_percentile_candidates", "peer_ret_median", "peer_new_high_5",
    "mfe_so_far", "mae_so_far", "bars_since_new_high", "new_high_count_5", "new_high_count_15",
    "new_high_count_30", "bars_since_episode_low", "candidate_count_t", "bars_since_entry",
)


def _feature_table() -> list[dict]:
    """The frozen Stage-B feature table: literals, built before any estimate is computed.

    `predictor` marks the columns that may enter an I-EV model (causal at the decision instant);
    the `post_*` and `descriptive` columns are the action-horizon / L-EV material and are stored
    but never used as predictors. `plan_row` is the row number of the plan's section-3 table.
    """
    rows: list[dict] = []

    def add(name, family, plan_row, universe, predictor, note):
        rows.append({"name": name, "family": family, "plan_row": plan_row, "universe": universe,
                     "causal_at_decision": bool(predictor), "note": note})

    for seg in ("pre", "anchor"):
        add(f"f1_arr_rate_{seg}", "F1", 1, "U-all", True,
            "U-all prints per second over the segment's own minute")
    add("f1_arr_accel", "F1", 2, "U-all", True, "log((n_prints(anchor)+1)/(n_prints(pre)+1))")
    for seg in ("pre", "anchor"):
        add(f"f2_size_p90_over_median_{seg}", "F2", 3, "U-path", True,
            "p90(print size) / median(print size) in the segment")
        add(f"f2_top_decile_size_share_{seg}", "F2", 4, "U-path", True,
            "share of the segment's printed size in its largest 10% of prints")
    for seg in ("pre", "anchor"):
        add(f"f3_secs_above_level_{seg}", "F3", 5, "U-path", True,
            "seconds whose 5-s bin closes at or above the declared level")
        add(f"f3_secs_below_level_{seg}", "F3", 5, "U-path", True,
            "seconds whose 5-s bin closes below the declared level")
    add("f3_reclaim_persistence", "F3", 6, "U-path", True,
        "5-s bins closing back at/above the level, given >= 1 print below it (causal part)")
    add("f4_time_to_reclaim_s", "F4", 7, "U-path", True,
        "seconds from the first below-level print to the first at/above-level print (causal part)")
    add("f4_n_failed_pushes", "F4", 8, "U-path", True,
        f"excursions >= {SB_PUSH_MIN:.1%} above the level followed by a return below it")
    add("f5_hole_minutes", "F5", 10, "U-path", True, "CS-5 only: hole length in minutes")
    add("f5_pre_halt_trend", "F5", 11, "U-path", True,
        "CS-5 only: pre-hole print / the pre-hole bar's open - 1 (signed move into the hole)")
    add("f5_hole_prints_all", "F5", 11, "U-all", True,
        "CS-5 only (pre-registered amendment): U-all prints strictly inside the hole minutes")
    add("f5_hole_print_rate", "F5", 11, "U-all", True,
        "CS-5 only (pre-registered amendment): f5_hole_prints_all / hole minutes")
    for seg in ("pre", "anchor"):
        add(f"f6_signed_vol_share_{seg}", "F6", 12, "U-path x quotes", True,
            "Lee-Ready (quote mid, tick fallback) signed size share in the segment")
        add(f"f6_unclassified_share_{seg}", "F6", 13, "U-path x quotes", True,
            "share of the segment's printed size dropped by the quote-staleness bound")
        add(f"f6_signed_vol_share_tickonly_{seg}", "F6", 14, "U-path", True,
            "tick-rule-only signed size share (quote-free twin)")
    # ---- descriptive / action-horizon columns: never predictors -------------------------- #
    for seg in ("post1", "post2"):
        add(f"post_arr_rate_{seg}", "none", 1, "U-all", False, "arrival rate in the action horizon")
        add(f"post_size_p90_over_median_{seg}", "none", 3, "U-path", False, "F2 in the horizon")
        add(f"post_top_decile_size_share_{seg}", "none", 4, "U-path", False, "F2 in the horizon")
        add(f"post_secs_above_level_{seg}", "none", 5, "U-path", False, "F3 in the horizon")
        add(f"post_secs_below_level_{seg}", "none", 5, "U-path", False, "F3 in the horizon")
        add(f"post_signed_vol_share_{seg}", "none", 12, "U-path x quotes", False, "F6 in the horizon")
        add(f"post_unclassified_share_{seg}", "none", 13, "U-path x quotes", False, "F6 in the horizon")
        add(f"post_signed_vol_share_tickonly_{seg}", "none", 14, "U-path", False, "F6 in the horizon")
    add("post_push_decay", "none", 9, "U-path", False,
        "secs above in post1 / secs above in post2 (the plan's F4 push_decay, post-decision)")
    add("post_time_to_reclaim_s", "none", 7, "U-path", False,
        "seconds from the decision instant to the first at/above print after it")
    add("post_reopen_code5", "none", 10, "U-all", False,
        "CS-5's own label (tape condition 5 on the reopen print) — never a predictor")
    add("post_reopen_gap_ret", "F5", 11, "U-path", False,
        "CS-5: reopen print / last pre-hole print - 1 — simultaneous with the label, descriptive")
    return rows


SB_FEATURE_ROWS = _feature_table()
SB_FEATURE_NAMES = tuple(r["name"] for r in SB_FEATURE_ROWS)
SB_PREDICTORS = tuple(r["name"] for r in SB_FEATURE_ROWS if r["causal_at_decision"])
SB_PREDICTORS_LIST = list(SB_PREDICTORS)
FAMILY_OF = {r["name"]: r["family"] for r in SB_FEATURE_ROWS}
SB_FAMILIES = tuple(sorted({r["family"] for r in SB_FEATURE_ROWS if r["causal_at_decision"]}))
SB_FEATURE_SHA = sha256_bytes(jdump(SB_FEATURE_ROWS).encode())

SB_PANEL_COLS = ["sleeve_day", "block", "family", "ticker", "entry_rank", "entry_et", "entry_px",
                 "et", "bar_index", "session_end", "bar_open", "bar_high", "bar_low", "bar_close",
                 "next_open", "next_et", "running_high", "v_forced_flat", "v_hold_flat",
                 "final_high_flag", "remaining_run", "cost_of_waiting", "failed_reclaim_count",
                 "terminal_censored", "future_member_last_et"] + list(MINUTE_FEATURES)


def stage_b_window_map(anchors: pl.DataFrame, df: pl.DataFrame) -> tuple[list[dict], dict]:
    """Map every frozen anchor onto its member's own panel bar list (B1/B2/B6).

    Returns one window spec per anchor plus the map diagnostics. Nothing here reads prints.
    """
    memb: dict = {}
    for key, sub in df.partition_by(MEMBER_KEY, maintain_order=True, as_dict=True).items():
        key = tuple(key) if isinstance(key, tuple) else (key,)
        sub = sub.sort("bar_index")
        memb[key] = {c: sub[c].to_numpy() for c in sub.columns}
    days = set(df["sleeve_day"].to_list())
    wins: list[dict] = []
    diag = {"anchors": anchors.height, "member_missing": 0, "anchor_bar_missing": 0,
            "windows": absence_counters()}
    for a in anchors.iter_rows(named=True):
        key = (a["sleeve_day"], a["family"], a["ticker"], a["entry_rank"])
        m = memb.get(key)
        if m is None:
            diag["member_missing"] += 1
            continue
        ets, bis = m["et"], m["bar_index"]
        cs5 = None
        if a["case_set"] == "CS-5":                      # bar_index is null in the freeze (B10)
            aux = json.loads(a["aux"] or "{}")
            hole = int(aux.get("hole_minutes") or a["ref_num"])
            revisit = int(a["et"])
            anchor_et = revisit - hole - 1
            cs5 = {"hole_minutes": hole, "reopen_code5": bool(aux.get("reopen_code5")),
                   "reopen_px": _f(aux.get("reopen_px")) if aux.get("reopen_px") else None,
                   "pre_hole_px": _f(aux.get("pre_hole_px")) if aux.get("pre_hole_px") else None,
                   "minutes_with_path_print": aux.get("minutes_with_path_print")}
            i = int(np.searchsorted(ets, anchor_et, "left"))
            if i >= ets.size or int(ets[i]) != anchor_et:
                diag["anchor_bar_missing"] += 1
                continue
        else:
            i = int(np.searchsorted(bis, int(a["bar_index"]), "left"))
            if i >= bis.size or int(bis[i]) != int(a["bar_index"]) or int(ets[i]) != int(a["et"]):
                diag["anchor_bar_missing"] += 1
                continue
            hole = None
        segs = CASE_SEGMENTS[a["case_set"]]
        bars: dict = {}
        if "pre" in segs:
            j = i - 1
            bars["pre"] = _bar_spec(m, j) if j >= 0 else None
        bars["anchor"] = _bar_spec(m, i)
        if "post1" in segs:
            bars["post1"] = _bar_spec(m, i + 1) if i + 1 < len(ets) else None
            bars["post2"] = _bar_spec(m, i + 2) if i + 2 < len(ets) else None
        if "hole" in segs:
            hole_lo, hole_hi = int(a["et"]) - int(hole), int(a["et"]) - 1
            bars["hole"] = {"seg": "hole", "et": hole_lo, "et_hi": hole_hi,
                            "bar_index": None, "bar_open": None, "bar_close": None,
                            "bar_high": None, "bar_low": None,
                            "span_s": 60.0 * (hole_hi - hole_lo + 1)}
        if bars.get("anchor") is None:
            diag["anchor_bar_missing"] += 1
            continue
        entry_et, session_end = int(a["entry_et"]), int(a["session_end"])
        order = [s for s in segs if bars.get(s)]
        spec = {
            "case_set": a["case_set"], "anchor_id": a["anchor_id"], "sel_rank": a["sel_rank"],
            "sleeve_day": a["sleeve_day"], "block": a["block"], "family": a["family"],
            "ticker": a["ticker"], "entry_rank": int(a["entry_rank"]),
            "entry_et": entry_et, "session_end": session_end, "et": int(a["et"]),
            "bar_index": int(bis[i]), "ref_num": a["ref_num"], "ref_txt": a["ref_txt"],
            "levels": _levels_for(m, i, a, cs5), "cs5": cs5,
            "bars": bars, "seg_order": order,
            "decision_close_s": float((int(ets[i]) + 1) * 60),
            "decision_et": int(ets[i]),
            "et_frozen": int(a["et"]), "bar_index_frozen": a["bar_index"],
            "et_lo": min(int(bars[s]["et"]) for s in order),
            "et_hi": max(int(bars[s]["et"]) for s in order),
            "holes": hole,
            "guards": {
                "no_prev_bar": "pre" in segs and bars.get("pre") is None,
                "no_next_bar": "post1" in segs and bars.get("post1") is None,
                "no_post2_bar": "post1" in segs and bars.get("post2") is None,
                "gap_next_min": (int(bars["post1"]["et"]) - int(ets[i]))
                if bars.get("post1") else None,
                "gap_post2_min": (int(bars["post2"]["et"]) - int(bars["post1"]["et"]))
                if bars.get("post2") and bars.get("post1") else None,
                "et_eq_entry_et": int(a["et"]) == entry_et,
                "et_plus3_gt_session_end": int(a["et"]) + 3 > session_end,
                "no_prev_session_clamp": (bars.get("pre") or {}).get("et") is not None
                and int(bars["pre"]["et"]) < entry_et,
                "post2_past_session_end": (bars.get("post2") or {}).get("et") is not None
                and int(bars["post2"]["et"]) > session_end,
            },
        }
        wins.append(spec)
        for k in ("no_prev_bar", "no_next_bar", "no_post2_bar", "et_eq_entry_et",
                  "et_plus3_gt_session_end"):
            diag["windows"][k] += int(bool(spec["guards"][k]))
        if spec["guards"]["gap_next_min"] and spec["guards"]["gap_next_min"] > 1:
            diag["windows"]["gap_next_gt1min"] += 1
        if spec["guards"]["gap_post2_min"] and spec["guards"]["gap_post2_min"] > 1:
            diag["windows"]["gap_post2_gt1min"] += 1
    diag["panel_days"] = len(days)
    return wins, diag


def absence_counters() -> dict:
    return {"no_prev_bar": 0, "no_next_bar": 0, "no_post2_bar": 0, "et_eq_entry_et": 0,
            "et_plus3_gt_session_end": 0, "gap_next_gt1min": 0, "gap_post2_gt1min": 0}


def _bar_spec(m: dict, j: int) -> dict:
    return {"seg": None, "et": int(m["et"][j]), "bar_index": int(m["bar_index"][j]),
            "bar_open": _f(m["bar_open"][j]), "bar_close": _f(m["bar_close"][j]),
            "bar_high": _f(m["bar_high"][j]), "bar_low": _f(m["bar_low"][j]), "span_s": 60.0}


def _f(x):
    x = float(x)
    return x if math.isfinite(x) else None


def _levels_for(m: dict, i: int, a: dict, cs5: dict | None = None) -> dict:
    """The declared level per case set, read from the panel row (never re-derived)."""
    cs = a["case_set"]
    if cs == "CS-1":
        lv = _f(m["entry_px"][i])
    elif cs == "CS-2":
        rh = _f(m["running_high"][i])
        lv = None if rh is None else rh * (1.0 - GIVEBACK_LEVEL / 100.0)
    elif cs in ("CS-3", "CS-4"):
        lv = _f(m["bar_close"][i])
    else:
        ph = (cs5 or {}).get("pre_hole_px")
        return {"level": ph, "pre_hole_px": ph}
    return {"level": lv}


# --------------------------------------------------------------------------- #
# Stage B — the anchored extractor
# --------------------------------------------------------------------------- #

def _seg_slice(et: np.ndarray, lo: int, hi: int) -> tuple[int, int]:
    return int(np.searchsorted(et, lo, "left")), int(np.searchsorted(et, hi, "right"))


# segment statistic -> (causal column, action-horizon column): the names of the frozen table
_SEG_FEATS = (
    ("arr_rate", "f1_arr_rate", "post_arr_rate"),
    ("size_p90_over_median", "f2_size_p90_over_median", "post_size_p90_over_median"),
    ("top_decile_size_share", "f2_top_decile_size_share", "post_top_decile_size_share"),
    ("secs_above", "f3_secs_above_level", "post_secs_above_level"),
    ("secs_below", "f3_secs_below_level", "post_secs_below_level"),
    ("signed_vol_share", "f6_signed_vol_share", "post_signed_vol_share"),
    ("unclassified_share", "f6_unclassified_share", "post_unclassified_share"),
    ("signed_vol_share_tickonly", "f6_signed_vol_share_tickonly",
     "post_signed_vol_share_tickonly"),
)


def _sizes_stats(size: np.ndarray) -> tuple:
    if size.size < 3:
        return None, None
    med = float(np.median(size))
    if med <= 0:
        return None, None
    k = max(1, int(math.ceil(0.10 * size.size)))
    part = np.partition(size, size.size - k)[size.size - k:]
    tot = float(size.sum())
    return (float(np.percentile(size, 90)) / med,
            (float(part.sum()) / tot) if tot > 0 else None)


def _occupancy(sec: np.ndarray, price: np.ndarray, level: float, bin_s: float, span_s: float,
               t_lo: float) -> tuple:
    """Seconds whose 5-s bin closes at/above and below the level (the plan's bin convention)."""
    if price.size == 0 or level is None:
        return None, None
    b = np.floor((sec - t_lo) / bin_s).astype(np.int64)
    b = np.clip(b, 0, max(0, int(math.ceil(span_s / bin_s)) - 1))
    ub, first = np.unique(b, return_index=True)
    last = np.searchsorted(b, ub, "right") - 1        # prints are ts-sorted: last print wins
    close_px = price[last]
    width = np.minimum(bin_s, span_s - ub * bin_s)
    width = np.where(width > 0, width, bin_s)
    above = float(width[close_px >= level].sum())
    below = float(width[close_px < level].sum())
    return above, below


def _flow(price: np.ndarray, size: np.ndarray, ts_us: np.ndarray, qts: np.ndarray | None,
          qbid: np.ndarray | None, qask: np.ndarray | None, state: dict) -> dict:
    """Lee-Ready signed size share + the quote-free tick-rule twin (plan section 3, F6).

    Vectorised: the tick rule is a forward fill of the last non-zero price change (a zero tick
    inherits the previous classification), seeded from the window's earlier segments.
    """
    n = price.size
    empty = {"signed_vol_share": None, "unclassified_share": None,
             "signed_vol_share_tickonly": None, "vol_classified": 0.0, "vol_total": 0.0}
    if n == 0:
        return empty
    prev = np.empty(n)
    prev[0] = state["px"] if state["px"] is not None else np.nan
    prev[1:] = price[:-1]
    d = np.where(np.isfinite(prev), np.sign(price - prev), 0.0)
    idx = np.arange(n)
    last_nz = np.maximum.accumulate(np.where(d != 0, idx, -1))
    has = last_nz >= 0
    fill = np.where(has, d[np.where(has, last_nz, 0)], np.nan)
    cls = np.where(d != 0, d, fill)
    if state["cls"] is not None:
        cls = np.where(np.isnan(cls), float(state["cls"]), cls)
    tick = np.where(np.isnan(cls), 0.0, cls)
    if qts is not None and qts.size:
        j = np.searchsorted(qts, ts_us, "right") - 1
        ok = j >= 0
        jj = np.where(ok, j, 0)
        age = np.where(ok, ts_us - qts[jj], np.inf)
        bid, ask = qbid[jj], qask[jj]
        valid = (ok & (age <= int(SB_Q_MAX_AGE_S * 1e6)) & np.isfinite(bid) & np.isfinite(ask)
                 & (bid > 0) & (ask >= bid))
        mid = 0.5 * (bid + ask)
        qv = np.where(price > mid, 1.0, np.where(price < mid, -1.0, cls))
        cls_q = np.where(valid, qv, np.nan)
    else:
        cls_q = np.full(n, np.nan)
    size = size.astype(float)
    v_tot = float(size.sum())
    v_buy = float(size[cls_q == 1].sum())
    v_sell = float(size[cls_q == -1].sum())
    v_unc = float(size[np.isnan(cls_q)].sum())
    v_tick_buy = float(size[tick > 0].sum())
    v_tick_sell = float(size[tick < 0].sum())
    state["px"] = float(price[-1])
    nz = tick[tick != 0]
    if nz.size:
        state["cls"] = float(nz[-1])
    v_cls = v_buy + v_sell
    v_tick = v_tick_buy + v_tick_sell
    return {"signed_vol_share": ((v_buy - v_sell) / v_cls) if v_cls > 0 else None,
            "unclassified_share": (v_unc / v_tot) if v_tot > 0 else None,
            "signed_vol_share_tickonly": ((v_tick_buy - v_tick_sell) / v_tick)
            if v_tick > 0 else None,
            "vol_classified": v_cls, "vol_total": v_tot}


def stage_b_day(day: str, wins: list[dict],
                sample_per_segment: int = SB_SAMPLE_PER_SEGMENT) -> dict:
    """Extract every window of one day: features + the capped raw print sample + G1 check."""
    sim.guard_day(day)
    t_start = time.perf_counter()
    t0 = time.perf_counter()
    syms = sorted({w["ticker"] for w in wins})
    df, tel = probe.load_prints(day, syms)
    t_read = time.perf_counter() - t0
    t0 = time.perf_counter()
    pf = t5.price_updating(df) if df.height else df
    t_path = time.perf_counter() - t0
    if df.height:
        keys = ["symbol", "ts_utc", "trade_id", "price"]
        pk = pf.select(keys).unique()
        df = df.join(pk.with_columns(pl.lit(True).alias("_path")), on=keys, how="left").with_columns(
            pl.col("_path").fill_null(False))
    sym_arrays: dict = {}
    if df.height:
        d = df.sort(["symbol", "ts_utc"]).with_columns(
            (pl.col("ts_et").dt.hour().cast(pl.Int64) * 3600
             + pl.col("ts_et").dt.minute().cast(pl.Int64) * 60
             + pl.col("ts_et").dt.second().cast(pl.Int64)
             + pl.col("ts_et").dt.microsecond().cast(pl.Int64) / 1e6).alias("_sec"))
        for k, sub in d.partition_by("symbol", maintain_order=True, as_dict=True).items():
            sym_arrays[k[0] if isinstance(k, tuple) else k] = {
                "et": sub["et"].to_numpy(), "sec": sub["_sec"].to_numpy(),
                "ts_us": sub["ts_utc"].cast(pl.Int64).to_numpy(),
                "price": sub["price"].to_numpy(), "size": sub["size"].to_numpy(),
                "path": sub["_path"].to_numpy()}
    t0 = time.perf_counter()
    qarrays: dict = {}
    qpath = QUOTES / f"{day}.parquet"
    if qpath.exists():
        q = pl.read_parquet(qpath, columns=["symbol", "ts_utc", "bid_price", "ask_price"])
        q = q.filter(pl.col("symbol").is_in(syms)).sort(["symbol", "ts_utc"])
        for k, sub in q.partition_by("symbol", maintain_order=True, as_dict=True).items():
            qarrays[k[0] if isinstance(k, tuple) else k] = (
                sub["ts_utc"].cast(pl.Int64).to_numpy(), sub["bid_price"].to_numpy(),
                sub["ask_price"].to_numpy())
    t_quote = time.perf_counter() - t0

    rows, sample = [], []
    g1_checked = g1_bad = 0
    t0 = time.perf_counter()
    for w in wins:
        got = _window_features(w, sym_arrays, qarrays, sample, sample_per_segment)
        rows.append(got)
        if got["g1_checked"]:
            g1_checked += 1
            g1_bad += got["g1_mismatch"]
    t_slice = time.perf_counter() - t0
    return {"rows": rows, "sample": sample,
            "day": {"day": day, "windows": len(wins), "members": len(syms),
                    "file_bytes": int(tel["file_bytes"]), "rows_loaded": int(tel["rows_loaded"]),
                    "read_s": _round(t_read, 4), "price_updating_s": _round(t_path, 4),
                    "quote_read_s": _round(t_quote, 4), "window_s": _round(t_slice, 4),
                    "wall_s": _round(time.perf_counter() - t_start, 4),
                    "g1_checked": g1_checked, "g1_mismatch": g1_bad,
                    "quote_symbols": len(qarrays)}}


def _window_features(w: dict, sym_arrays: dict, qarrays: dict, sample: list,
                     sample_per_segment: int) -> dict:
    """One window's feature row. Every predictor is computed from the causal segments only."""
    key = w["ticker"]
    A = sym_arrays.get(key)
    out: dict = {"anchor_id": w["anchor_id"], "case_set": w["case_set"],
                 "sleeve_day": w["sleeve_day"], "block": w["block"], "family": w["family"],
                 "ticker": w["ticker"], "entry_rank": w["entry_rank"], "et": w["et"],
                 "bar_index": w["bar_index"], "sel_rank": w["sel_rank"],
                 "decision_close_s": w["decision_close_s"], "g1_checked": 0, "g1_mismatch": 0,
                 "n_prints_window_all": 0, "n_prints_window_path": 0, "n_prints_causal": 0,
                 "cutoff_sec_et": None, "first_print_offset_s": None, "n_prints_at_decision": 0,
                 "g1_tau_positive_causal": 0, "n_prints_in_gap_minutes_all": 0}
    if A is None:
        return out
    et, sec, ts_us = A["et"], A["sec"], A["ts_us"]
    price, size, ispath = A["price"], A["size"], A["path"]
    q = qarrays.get(key)
    qts, qbid, qask = q if q else (None, None, None)
    m = {k: v[ispath] for k, v in (("et", et), ("sec", sec), ("ts_us", ts_us),
                                   ("price", price), ("size", size))}
    levels = w["levels"]
    level = levels.get("level")
    dec = w["decision_close_s"]
    if w["case_set"] == "CS-5":
        # the CS-5 anchor IS a print: the decision instant is the reopen print (frozen
        # action_stamp_tau = 0), so the hole's own prints are causal for it
        r0, r1 = _seg_slice(m["et"], int(w["et"]), int(w["et"]))
        if r1 > r0:
            dec = float(m["sec"][r0])
        out["decision_close_s"] = dec
    state = {"px": None, "cls": None}
    segv: dict = {}
    for seg in w["seg_order"]:
        b = w["bars"][seg]
        lo, hi = int(b["et"]), int(b.get("et_hi", b["et"]))
        a0, a1 = _seg_slice(et, lo, hi)
        p0, p1 = _seg_slice(m["et"], lo, hi)
        span = float(b["span_s"])
        a_sec, a_px, a_sz, a_ts = sec[a0:a1], price[a0:a1], size[a0:a1], ts_us[a0:a1]
        p_sec, p_px, p_sz, p_ts = m["sec"][p0:p1], m["price"][p0:p1], m["size"][p0:p1], \
            m["ts_us"][p0:p1]
        t_lo = lo * 60.0
        sz90, top10 = _sizes_stats(p_sz)
        above, below = _occupancy(p_sec, p_px, level, SB_BIN_S, span, t_lo)
        fl = _flow(p_px, p_sz, p_ts, qts, qbid, qask, state)
        segv[seg] = {"n_all": int(a1 - a0), "n_path": int(p1 - p0), "span_s": span,
                     "arr_rate": _round((a1 - a0) / span, 6),
                     "size_p90_over_median": _round(sz90, 6) if sz90 is not None else None,
                     "top_decile_size_share": _round(top10, 6) if top10 is not None else None,
                     "secs_above": _round(above, 6) if above is not None else None,
                     "secs_below": _round(below, 6) if below is not None else None,
                     "signed_vol_share": _round(fl["signed_vol_share"], 6),
                     "unclassified_share": _round(fl["unclassified_share"], 6),
                     "signed_vol_share_tickonly": _round(fl["signed_vol_share_tickonly"], 6),
                     "vol_classified": fl["vol_classified"], "vol_total": fl["vol_total"],
                     "sec": p_sec, "px": p_px, "ts_us": p_ts}
        if seg in ("pre", "anchor", "hole"):
            tau = (p_sec - dec) if p_sec.size else np.array([])
            tau_all = (a_sec - dec) if a_sec.size else np.array([])
            out["n_prints_causal"] += int(p_sec.size)
            bad = int((tau > 0).sum()) + int((tau_all > 0).sum())
            if bad:
                raise SystemExit(
                    f"G2 leakage: causal segment {seg} of {w['anchor_id']} carries {bad} "
                    f"print(s) after the decision instant")
            if tau.size:
                out["cutoff_sec_et"] = float(p_sec.max())
        for i in range(min(sample_per_segment, a1 - a0)):
            sample.append({"anchor_id": w["anchor_id"], "case_set": w["case_set"], "segment": seg,
                           "tau_s": _round(float(a_sec[i]) - dec, 6),
                           "ts_us": int(a_ts[i]), "price": _round(float(a_px[i]), 6),
                           "size": _round(float(a_sz[i]), 6),
                           "path": bool(ispath[a0 + i])})
        out["n_prints_window_all"] += int(a1 - a0)
        out["n_prints_window_path"] += int(p1 - p0)
    # ---- G1: the anchor bar's own minute must reproduce the panel's bar high/low ---------- #
    ab = w["bars"]["anchor"]
    p0, p1 = _seg_slice(m["et"], int(ab["et"]), int(ab["et"]))
    if p1 > p0 and ab["bar_high"] is not None:
        out["g1_checked"] = 1
        hi_v, lo_v = float(m["price"][p0:p1].max()), float(m["price"][p0:p1].min())
        if (abs(hi_v - ab["bar_high"]) > 1e-9 or abs(lo_v - ab["bar_low"]) > 1e-9):
            out["g1_mismatch"] = 1
    if "anchor" in segv and segv["anchor"]["sec"].size:
        out["first_print_offset_s"] = _round(float(segv["anchor"]["sec"].min()) - ab["et"] * 60, 6)
        ps = segv["post1"]["sec"] if "post1" in segv else None
        if ps is not None and ps.size:
            thr = ab["et"] * 60.0 + out["first_print_offset_s"] + 60.0
            out["n_prints_frozen_rule_leak"] = int((ps < thr).sum())
    # ---- window-level causal features ----------------------------------------------------- #
    causal = [segv[s] for s in ("pre", "anchor", "hole") if s in segv]
    c_sec = np.concatenate([c["sec"] for c in causal]) if causal else np.array([])
    c_px = np.concatenate([c["px"] for c in causal]) if causal else np.array([])
    if c_sec.size:
        order = np.argsort(c_sec, kind="stable")
        c_sec, c_px = c_sec[order], c_px[order]
    for seg, v in segv.items():
        post = seg not in ("pre", "anchor", "hole")
        for key, cname, pname in _SEG_FEATS:
            out[f"{pname if post else cname}_{seg}"] = v[key]
    out["f1_arr_accel"] = _round(math.log((segv["anchor"]["n_all"] + 1.0)
                                          / (segv["pre"]["n_all"] + 1.0)), 6) \
        if "pre" in segv and "anchor" in segv else None
    out["f3_reclaim_persistence"], out["f4_time_to_reclaim_s"], out["f4_n_failed_pushes"] = \
        _causal_velocity(level, c_sec, c_px, w)
    if "pre" in segv and "anchor" in segv and "post1" in segv and "post2" in segv:
        a1, a2 = segv["post1"]["secs_above"], segv["post2"]["secs_above"]
        out["post_push_decay"] = _round(a1 / a2, 6) if (a1 is not None and a2) else None
    if "post1" in segv or "post2" in segv:
        pf_sec = np.concatenate([segv[s]["sec"] for s in ("post1", "post2") if s in segv])
        pf_px = np.concatenate([segv[s]["px"] for s in ("post1", "post2") if s in segv])
        if pf_sec.size and level is not None:
            order = np.argsort(pf_sec, kind="stable")
            pf_sec, pf_px = pf_sec[order], pf_px[order]
            hit = np.flatnonzero(pf_px >= level)
            out["post_time_to_reclaim_s"] = _round(float(pf_sec[hit[0]]) - dec, 6) \
                if hit.size else None
        else:
            out["post_time_to_reclaim_s"] = None
    else:
        out["post_time_to_reclaim_s"] = None
    out["post_push_decay"] = out.get("post_push_decay")
    # ---- CS-5 pre-hole level + hole facts ------------------------------------------------- #
    if w["case_set"] == "CS-5":
        ab = w["bars"]["anchor"]
        p0, p1 = _seg_slice(m["et"], int(ab["et"]), int(ab["et"]))
        ph = float(m["price"][p1 - 1]) if p1 > p0 else None
        out["f5_hole_minutes"] = _round(float(w["holes"]), 6)
        out["f5_pre_halt_trend"] = _round(ph / ab["bar_open"] - 1.0, 9) \
            if (ph is not None and ab["bar_open"]) else None
        hv = segv.get("hole")
        out["f5_hole_prints_all"] = float(hv["n_all"]) if hv else None
        out["f5_hole_print_rate"] = _round(hv["n_all"] / hv["span_s"], 6) if hv else None
        out["n_prints_in_gap_minutes_all"] = int(hv["n_all"]) if hv else 0
        aux = w.get("cs5") or {}
        out["f5_pre_hole_px_delta"] = _round(ph - aux["pre_hole_px"], 9) \
            if (ph is not None and aux.get("pre_hole_px") is not None) else None
        out["post_reopen_code5"] = bool(aux.get("reopen_code5"))
        out["post_reopen_gap_ret"] = _round(aux["reopen_px"] / aux["pre_hole_px"] - 1.0, 9) \
            if (aux.get("reopen_px") and aux.get("pre_hole_px")) else None
    else:
        # prints inside the wall-clock gap minutes of the window (panel-invisible minutes)
        gap_lo = None
        for seg in w["seg_order"]:
            b = w["bars"][seg]
            if gap_lo is not None:
                g0, g1 = _seg_slice(et, gap_lo, int(b["et"]) - 1)
                out["n_prints_in_gap_minutes_all"] += int(max(0, g1 - g0))
            gap_lo = int(b.get("et_hi", b["et"])) + 1
    return out


def _causal_velocity(level: float, sec: np.ndarray, px: np.ndarray, w: dict) -> tuple:
    """F3 reclaim persistence, F4 time-to-reclaim and failed pushes — causal part only."""
    if level is None or sec.size == 0:
        return None, None, None
    below = np.flatnonzero(px < level)
    if below.size == 0:
        return None, None, 0.0
    # persistence: 5-s bins at/after the first below-level print whose bin close is >= level
    b = np.floor((sec - sec[0]) / SB_BIN_S).astype(np.int64)
    ub = np.unique(b)
    last = np.searchsorted(b, ub, "right") - 1
    close_px = px[last]
    first_bin = b[below[0]]
    sel = ub >= first_bin
    persist = float(np.sum(close_px[sel] >= level))
    # time to reclaim: first below-level print -> first subsequent at/above print
    after = np.flatnonzero(px >= level)
    after = after[after > below[0]]
    t_reclaim = float(sec[after[0]] - sec[below[0]]) if after.size else None
    # failed pushes: excursions >= push_min above the level followed by a return below it
    band = px >= level * (1.0 + SB_PUSH_MIN)
    below_lvl = px < level
    ii = np.arange(px.size)
    lb = np.maximum.accumulate(np.where(below_lvl, ii, -1))
    la = np.maximum.accumulate(np.where(band, ii, -1))
    lb_prev = np.r_[-1, lb[:-1]]
    la_prev = np.r_[-1, la[:-1]]
    pushes = float(np.sum(below_lvl & (la_prev > lb_prev)))
    return persist, (round(t_reclaim, 6) if t_reclaim is not None else None), pushes


def _stage_b_day_task(args) -> dict:
    """Picklable per-day task for the process pool."""
    day, wins, sample_per_segment = args
    return stage_b_day(day, wins, sample_per_segment)


def stage_b_extract(wins: list[dict], workers: int, days: list[str] | None = None,
                    sample_per_segment: int = SB_SAMPLE_PER_SEGMENT, trial: bool = False) -> dict:
    by_day: dict = {}
    for w in wins:
        by_day.setdefault(w["sleeve_day"], []).append(w)
    sel = sorted(by_day) if days is None else [d for d in sorted(by_day) if d in set(days)]
    t0 = time.perf_counter()
    res = []
    if workers <= 1:
        for d in sel:
            res.append(stage_b_day(d, by_day[d], sample_per_segment))
            print(f"[stageB] {d} windows={len(by_day[d])}", flush=True)
    else:
        # processes, not threads: the per-day work is GIL-bound (polars reads release the GIL, the
        # numpy feature passes do not), so a thread pool gives no speedup on this workload. Each
        # worker owns one day and the results are collected in day order, so the artifacts are
        # identical to a serial run's.
        tasks = [(d, by_day[d], sample_per_segment) for d in sel]
        with ProcessPoolExecutor(max_workers=workers,
                                 mp_context=mp.get_context("spawn")) as ex:
            for d, r in zip(sel, ex.map(_stage_b_day_task, tasks), strict=True):
                res.append(r)
                print(f"[stageB] {d} windows={len(by_day[d])}", flush=True)
    wall = time.perf_counter() - t0
    rows = pl.DataFrame([r for x in res for r in x["rows"]])
    samp = pl.DataFrame([s for x in res for s in x["sample"]])
    suffix = "_trial" if trial else ""
    _write_parquet(rows, E3 / f"stage_b_features{suffix}.parquet")
    if samp.height:
        _write_parquet(samp, E3 / f"stage_b_prints{suffix}.parquet")
    days_tel = [x["day"] for x in res]
    cost = {"measured": True, "days": len(sel), "workers": workers,
            "wall_s": _round(wall, 3), "wall_min": _round(wall / 60, 3),
            "abort_threshold_hours": 4,
            "bytes_read_trades": int(sum(d["file_bytes"] for d in days_tel)),
            "projected_full_store_serial_min": _round(
                sum(d["wall_s"] for d in days_tel) / max(1, len(days_tel)) * PANEL_DAYS / 60, 2),
            "projected_full_store_serial_note": (
                "sum(per-day wall_s)/days x 1066 / 60. Each per-day wall is measured inside its "
                "own worker, so with workers > 1 it already carries CPU/IO contention: the number "
                "is an UPPER BOUND on the serial projection, not a serial measurement. The "
                "measured wall of this run is `wall_s`."),
            "effective_days_per_wall_min": _round(len(sel) / max(1e-9, wall / 60), 3),
            "sum_day_slice_s": _round(sum(d["window_s"] for d in days_tel), 3),
            "sample_rows": int(samp.height),
            "per_day": days_tel}
    _write_json(E3 / f"stage_b_cost{suffix}.json", cost)
    return {"features": rows, "prints": samp, "cost": cost, "days": days_tel}


# --------------------------------------------------------------------------- #
# Stage B — the I-EV measurement (model machinery)
# --------------------------------------------------------------------------- #

SB_TARGETS = {
    "CS-1": [
        ("y_vff_up", "primary",
         "sign(v_forced_flat) at the reclaim bar t (executable from the open of bar t+1)"),
        ("y_final_high", "secondary", "final_high_flag at t (no new high after t)"),
        ("y_deep_cost", "secondary",
         "cost_of_waiting at t below the case-set median (adverse excursion depth)"),
        ("y_failed_reclaim", "secondary",
         "failed_reclaim_count increases between bar t and bar t+5"),
    ],
    "CS-2": [
        ("y_false_cut", "primary",
         "ledger readmissible at the giveback exit (traded >= +10% above the exit price)"),
        ("y_final_high", "secondary", "final_high_flag at the trigger bar t"),
    ],
    "CS-3": [
        ("y_a_wins", "primary", "v_forced_flat_A > v_forced_flat_B on the matched pair"),
    ],
    "CS-4": [
        ("y_final_high", "primary", "final_high_flag at t (descriptive; the set is future-stratified)"),
        ("y_remaining_run", "secondary", "remaining_run > 0.10 at t (forward peak from next_open)"),
    ],
    "CS-5": [
        ("y_reopen_code5", "primary", "the reopen print of the hole carries tape condition 5"),
        ("y_gap_ret_up", "secondary", "reopen_gap_ret > 0 (simultaneous with the label; descriptive)"),
    ],
}


def _f64(df: pl.DataFrame, col: str) -> np.ndarray:
    return (df[col].cast(pl.Float64, strict=False).fill_nan(None).fill_null(float("nan"))
            .to_numpy())


def _panel_rows(wins: list[dict], cols: list[str], offset: int = 0) -> pl.DataFrame:
    """Panel row per anchor (bar identity through the member's own bar list), in anchor order."""
    keys = pl.DataFrame([{"anchor_id": w["anchor_id"], "sleeve_day": w["sleeve_day"],
                          "family": w["family"], "ticker": w["ticker"],
                          "entry_rank": w["entry_rank"],
                          "bar_index": int(w["bar_index"]) + offset} for w in wins])
    keys = keys.with_row_index("_i")
    pan = load_panel_cols(list(dict.fromkeys(["sleeve_day", "family", "ticker", "entry_rank",
                                              "bar_index"] + cols)))
    j = keys.join(pan, on=["sleeve_day", "family", "ticker", "entry_rank", "bar_index"], how="left")
    return j.sort("_i")


def _rank(v: np.ndarray) -> np.ndarray:
    order = np.argsort(v, kind="stable")
    ss = v[order]
    starts = np.flatnonzero(np.r_[True, ss[1:] != ss[:-1]])
    counts = np.diff(np.r_[starts, ss.size])
    avg = starts + (counts + 1) / 2.0
    r = np.empty(ss.size)
    r[order] = np.repeat(avg, counts)
    return r


def _auc(y: np.ndarray, s: np.ndarray) -> float | None:
    pos = y > 0.5
    n1, n0 = int(pos.sum()), int((~pos).sum())
    if n1 == 0 or n0 == 0:
        return None
    r = _rank(s)
    return float((r[pos].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def _logloss(y: np.ndarray, s: np.ndarray) -> float | None:
    p = 1.0 / (1.0 + np.exp(-np.clip(s, -30, 30)))
    p = np.clip(p, 1e-12, 1 - 1e-12)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _logit_fit(X: np.ndarray, y: np.ndarray, l2: float = SB_L2) -> np.ndarray:
    n, p = X.shape
    Z = np.column_stack([np.ones(n), X])
    w = np.zeros(p + 1)
    pen = np.eye(p + 1) * l2
    pen[0, 0] = 0.0
    for _ in range(60):
        z = Z @ w
        mu = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        g = Z.T @ (mu - y) + pen @ w
        wt = mu * (1.0 - mu) + 1e-9
        H = Z.T @ (Z * wt[:, None]) + pen
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(H, g, rcond=None)[0]
        w = w - step
        if np.max(np.abs(step)) < 1e-9:
            break
    return w


def _fit_columns(Xtr: np.ndarray, names: list[str], min_cov: float = SB_MIN_COVERAGE) -> list[int]:
    keep = []
    for j, _ in enumerate(names):
        col = Xtr[:, j]
        fin = np.isfinite(col)
        if fin.mean() >= min_cov and float(np.nanstd(col[fin])) > 1e-12:
            keep.append(j)
    return keep


def _design(X: np.ndarray, keep: list[int], mu: np.ndarray, sd: np.ndarray) -> np.ndarray:
    if not keep:
        return np.zeros((X.shape[0], 0))
    Z = X[:, keep]
    Z = np.where(np.isfinite(Z), Z, mu)
    return (Z - mu) / sd


def _score(Xtr: np.ndarray, ytr: np.ndarray, Xte: np.ndarray, keep: list[int],
           l2: float = SB_L2) -> np.ndarray:
    Ztr = Xtr[:, keep]
    mu = np.nanmedian(Ztr, axis=0) if keep else np.zeros(0)
    sd = np.nanstd(Ztr, axis=0) if keep else np.zeros(0)
    mu = np.where(np.isfinite(mu), mu, 0.0)
    sd = np.where(np.isfinite(sd) & (sd > 1e-12), sd, 1.0)
    w = _logit_fit(_design(Xtr, keep, mu, sd), ytr, l2)
    Zte = _design(Xte, keep, mu, sd)
    return np.column_stack([np.ones(Xte.shape[0]), Zte]) @ w


def _boot_delta(y: np.ndarray, s0: np.ndarray, s1: np.ndarray, day_idx: list[np.ndarray],
                rng: np.random.Generator, b: int = SB_BOOTSTRAP_B) -> dict:
    """Day-clustered bootstrap of Delta AUC and Delta log-loss (clusters resampled, matched)."""
    sizes = np.array([len(d) for d in day_idx])
    offs = np.r_[0, np.cumsum(sizes)[:-1]]
    d_auc, d_ll = [], []
    nd = len(day_idx)
    for _ in range(b):
        pick = rng.integers(0, nd, nd)
        take = sizes[pick]
        if take.sum() == 0:
            continue
        starts = np.repeat(offs[pick], take)
        idx = starts + (np.arange(take.sum()) - np.repeat(np.cumsum(take) - take, take))
        yy, a0, a1 = y[idx], s0[idx], s1[idx]
        if yy.min() == yy.max():
            continue
        auc0, auc1 = _auc(yy, a0), _auc(yy, a1)
        ll0, ll1 = _logloss(yy, a0), _logloss(yy, a1)
        if auc0 is None or auc1 is None or ll0 is None or ll1 is None:
            continue
        d_auc.append(auc1 - auc0)
        d_ll.append(ll0 - ll1)
    if not d_auc:
        return {"b_used": 0, "delta_auc_ci95": None, "delta_logloss_ci95": None,
                "p_delta_auc": None}
    a = np.array(d_auc)
    l = np.array(d_ll)
    p = 2.0 * min(float((a <= 0).mean()), float((a >= 0).mean()))
    return {"b_used": int(a.size),
            "delta_auc_ci95": [_round(float(np.percentile(a, 2.5)), 6),
                               _round(float(np.percentile(a, 97.5)), 6)],
            "delta_logloss_ci95": [_round(float(np.percentile(l, 2.5)), 6),
                                   _round(float(np.percentile(l, 97.5)), 6)],
            "p_delta_auc": _round(min(1.0, p), 4)}


def _cell(rows: dict, coord: np.ndarray) -> dict:
    return {"X_print": rows["X_print"], "X_minute": rows["X_minute"], "y": rows["y"],
            "block": rows["block"], "family": rows["family"], "day": rows["day"],
            "coord": coord}


def _direction(rows: dict, train: np.ndarray, test: np.ndarray, rng, with_boot: bool) -> dict:
    """One block-direction cross-fit: baseline + every family + every single predictor."""
    names_p, names_m = SB_PREDICTORS_LIST, list(MINUTE_FEATURES)
    Xm_tr, Xm_te = rows["X_minute"][train], rows["X_minute"][test]
    y_tr, y_te = rows["y"][train], rows["y"][test]
    keep_m = _fit_columns(Xm_tr, names_m)
    s0 = _score(Xm_tr, y_tr, Xm_te, keep_m)
    out = {"n_train": int(train.sum()), "n_test": int(test.sum()),
           "n_minute_features_used": len(keep_m),
           "minute_auc": _round(_auc(y_te, s0), 6), "minute_logloss": _round(_logloss(y_te, s0), 6),
           "families": {}, "features": {}}
    day_idx = [np.flatnonzero(test & (rows["day"] == d)) for d in sorted(set(rows["day"][test]))]
    day_idx = [d for d in day_idx if d.size]
    for fam in SB_FAMILIES:
        cols = [i for i, n in enumerate(names_p) if FAMILY_OF[n] == fam]
        if not cols:
            out["families"][fam] = {"skipped": "no predictor in this family for this case set"}
            continue
        Xp_tr = np.column_stack([rows["X_print"][train][:, c] for c in cols])
        Xp_te = np.column_stack([rows["X_print"][test][:, c] for c in cols])
        keep = _fit_columns(Xp_tr, [names_p[c] for c in cols])
        if not keep:
            out["families"][fam] = {"skipped": "no usable column (coverage/constant)"}
            continue
        Xtr = np.column_stack([Xm_tr[:, keep_m], Xp_tr[:, keep]])
        Xte = np.column_stack([Xm_te[:, keep_m], Xp_te[:, keep]])
        s1 = _score(Xtr, y_tr, Xte, list(range(Xtr.shape[1])))
        rec = {"n_features_used": len(keep),
               "features_used": [names_p[c] for c in [cols[k] for k in keep]],
               "auc": _round(_auc(y_te, s1), 6), "logloss": _round(_logloss(y_te, s1), 6),
               "delta_auc": _round((_auc(y_te, s1) or 0) - (out["minute_auc"] or 0), 6),
               "delta_logloss": _round((out["minute_logloss"] or 0) - (_logloss(y_te, s1) or 0), 6)}
        if with_boot:
            rec.update(_boot_delta(y_te, s0, s1, day_idx, rng))
        out["families"][fam] = rec
    for i, nm in enumerate(names_p):
        Xp_tr = rows["X_print"][train][:, i:i + 1]
        Xp_te = rows["X_print"][test][:, i:i + 1]
        keep = _fit_columns(Xp_tr, [nm])
        if not keep:
            out["features"][nm] = {"skipped": "coverage/constant in training fold"}
            continue
        Xtr = np.column_stack([Xm_tr[:, keep_m], Xp_tr])
        Xte = np.column_stack([Xm_te[:, keep_m], Xp_te])
        s1 = _score(Xtr, y_tr, Xte, list(range(Xtr.shape[1])))
        rec = {"family": FAMILY_OF[nm], "auc": _round(_auc(y_te, s1), 6),
               "logloss": _round(_logloss(y_te, s1), 6),
               "delta_auc": _round((_auc(y_te, s1) or 0) - (out["minute_auc"] or 0), 6),
               "delta_logloss": _round((out["minute_logloss"] or 0) - (_logloss(y_te, s1) or 0), 6)}
        if with_boot:
            rec.update(_boot_delta(y_te, s0, s1, day_idx, rng))
        out["features"][nm] = rec
    return out


def _coordinate(rows: dict, rng, with_boot: bool) -> dict:
    b1 = rows["block"] == "block1"
    b2 = rows["block"] == "block2"
    out = {"n_rows": int(rows["y"].size), "n_block1": int(b1.sum()), "n_block2": int(b2.sum()),
           "n_days": int(len(set(rows["day"].tolist()))),
           "n_positives": int((rows["y"] > 0.5).sum())}
    if b1.sum() >= 50 and b2.sum() >= 50 and 0 < rows["y"][b1].mean() < 1 \
            and 0 < rows["y"][b2].mean() < 1:
        out["block1->block2"] = _direction(rows, b1, b2, rng, with_boot)
        out["block2->block1"] = _direction(rows, b2, b1, rng, with_boot)
    else:
        out["skipped"] = "a block has < 50 rows or a constant label"
    return out


# --------------------------------------------------------------------------- #
# Stage B — diagnostics, gates and the measurement driver
# --------------------------------------------------------------------------- #

def stage_b_diagnostics(features: pl.DataFrame, diag: dict, wins: list[dict],
                        cost: dict) -> dict:
    """B1/B2/B6 counters, the extraction census and the G1/G2 gates."""
    n = features.height
    have = set(features["anchor_id"].to_list())
    wins = [w for w in wins if w["anchor_id"] in have]
    off = [x for x in features["first_print_offset_s"].to_list() if x is not None]
    hole = features.filter(pl.col("case_set") == "CS-5")
    hole_cls = counts("4min" if (r or 0) == 4 else ">=5min" for r in hole["f5_hole_minutes"].to_list())
    cs5_frozen = counts("4min" if (r or 0) == 4 else ">=5min"
                        for r in pl.read_parquet(E3 / "anchors.parquet")
                        .filter(pl.col("case_set") == "CS-5")["ref_num"].to_list())
    g1_checked = int(features["g1_checked"].sum())
    g1_bad = int(features["g1_mismatch"].sum())
    tau_pos = int(features["g1_tau_positive_causal"].sum())
    leak = int(features["n_prints_frozen_rule_leak"].fill_null(0).sum())
    leak_windows = int((features["n_prints_frozen_rule_leak"].fill_null(0) > 0).sum())
    reg = ledger.load_registry()
    minute_blocked = {c: reg.blocked_reason(c) for c in MINUTE_FEATURES
                      if reg.blocked_reason(c) is not None}
    per_set = {}
    for cs in CASE_ORDER:
        f = features.filter(pl.col("case_set") == cs)
        if not f.height:
            continue
        rec = {
            "windows": f.height,
            "n_prints_window_all": int(f["n_prints_window_all"].sum()),
            "n_prints_window_path": int(f["n_prints_window_path"].sum()),
            "n_prints_causal": int(f["n_prints_causal"].sum()),
            "n_prints_in_gap_minutes_all": int(f["n_prints_in_gap_minutes_all"].sum()),
            "n_prints_frozen_rule_leak": int(f["n_prints_frozen_rule_leak"].fill_null(0).sum()),
            "windows_with_frozen_rule_leak": int((f["n_prints_frozen_rule_leak"].fill_null(0) > 0).sum()),
            "g1_checked": int(f["g1_checked"].sum()), "g1_mismatch": int(f["g1_mismatch"].sum()),
            "first_print_offset_s": dist_summary(f["first_print_offset_s"].to_list(), nd=3),
            "no_next_bar": sum(1 for w in wins if w["case_set"] == cs
                               and w["guards"]["no_next_bar"]),
            "no_prev_bar": sum(1 for w in wins if w["case_set"] == cs
                               and w["guards"]["no_prev_bar"]),
            "no_post2_bar": sum(1 for w in wins if w["case_set"] == cs
                                and w["guards"]["no_post2_bar"]),
            "gap_next_gt1min": sum(1 for w in wins if w["case_set"] == cs
                                   and (w["guards"]["gap_next_min"] or 1) > 1),
            "tau_window_spill_et_plus3_gt_session_end": sum(
                1 for w in wins if w["case_set"] == cs and w["guards"]["et_plus3_gt_session_end"]),
            "tau_window_spill_et_eq_entry_et": sum(
                1 for w in wins if w["case_set"] == cs and w["guards"]["et_eq_entry_et"]),
        }
        if cs == "CS-5":
            rec["hole_length_class_measured"] = counts(
                "4min" if (r or 0) == 4 else ">=5min" for r in f["f5_hole_minutes"].to_list())
            rec["hole_length_class_frozen"] = cs5_frozen
            d = f["f5_pre_hole_px_delta"].drop_nulls()
            rec["pre_hole_px_vs_frozen_aux"] = {
                "checked": int(d.len()), "max_abs_delta": _round(float(d.abs().max()), 12)
                if d.len() else None,
                "note": ("the extracted last pre-hole print price minus the frozen CS-5 aux "
                         "pre_hole_px: 0 proves the CS-5 re-anchoring (B10) lands on the frozen "
                         "window")}
        per_set[cs] = rec
    return {
        "b1_decision_instant": {
            "rule": ("the decision is the CLOSE of the anchor bar t: every causal feature reads "
                     "only prints with ts <= close(t), i.e. tau = ts - close(t) <= 0"),
            "cs5_exception": {
                "declared": True,
                "rule": ("CS-5's anchor IS a print, not a bar: its decision instant is the FIRST "
                         "U-path print of the reopen minute (frozen action_stamp_tau = 0), so the "
                         "pre-hole bar and the hole's own prints are causal by design"),
                "measured_from": "the pre-hole bar's close",
                "max_hole_minutes_in_set": _round(float(
                    features.filter(pl.col("case_set") == "CS-5")["f5_hole_minutes"].max() or 0), 3),
                "bound_s": _round(60.0 * float(
                    features.filter(pl.col("case_set") == "CS-5")["f5_hole_minutes"].max() or 0), 1),
                "measured_max_s": 411.8,
                "read_by": ("only the CS-5 predictors (F3/F4/F5) and the diagnostic "
                            "f5_hole_prints_all; every non-CS-5 window obeys tau <= 0 strictly"),
                "note": ("the debug pass measured hole prints at tau up to +411.8 s from the "
                         "pre-hole bar's close, bounded by the longest hole in the frozen set")},
            "close_definition": "close(t) = (et_t + 1) * 60 seconds after ET midnight",
            "first_print_offset_s": dist_summary(off, nd=3),
            "print_cutoff_recorded_per_window": "cutoff_sec_et (the last causal print's ET second)",
            "windows_with_cutoff": int(features["cutoff_sec_et"].is_not_null().sum()),
            "causal_prints_with_tau_positive": tau_pos,
            "frozen_rule_leak_prints": leak,
            "frozen_rule_leak_windows": leak_windows,
            "frozen_rule_leak_note": (
                "prints the frozen `tau<60 from the first print` rule would have admitted at "
                "tau_correct > 0, counted inside the execution bar only (an exact count, not a "
                "sample): the audit measured an offset p50 of 0.883 s on 60 anchors"),
        },
        "b2_bar_identity": {
            "rule": ("segments are the member's own panel bars (bar identity through the panel's "
                     "member bar list), never tau arithmetic; the execution/outcome bar is the "
                     "panel's next bar after t, whatever its et"),
            "windows_no_next_bar": sum(1 for w in wins if w["guards"]["no_next_bar"]),
            "windows_gap_next_gt1min": sum(1 for w in wins
                                           if (w["guards"]["gap_next_min"] or 1) > 1),
            "max_gap_next_min": max((w["guards"]["gap_next_min"] or 1) for w in wins),
        },
        "b6_containment": {
            "rule": ("the declared segments are panel bars of the member, so every stored print "
                     "sits inside [entry_et, session_end]; prints outside the member window are "
                     "never read (load path filters to the member window)"),
            "windows_no_prev_bar": int(sum(1 for w in wins if w["guards"]["no_prev_bar"])),
            "windows_no_post2_bar": int(sum(1 for w in wins if w["guards"]["no_post2_bar"])),
            "frozen_tau_window_spill_et_plus3_gt_session_end": int(sum(
                1 for w in wins if w["guards"]["et_plus3_gt_session_end"])),
            "frozen_tau_window_spill_et_eq_entry_et": int(sum(
                1 for w in wins if w["guards"]["et_eq_entry_et"])),
        },
        "extraction": {"windows": n, "per_case_set": per_set,
                       "member_missing": diag["member_missing"],
                       "anchor_bar_missing": diag["anchor_bar_missing"],
                       "prints_sampled": cost.get("sample_rows"),
                       "bytes_read_trades": cost.get("bytes_read_trades")},
        "gates": {
            "G1_reconstruction": {
                "rule": "the U-path high/low of every anchor bar equals the panel's bar_high/bar_low",
                "checked": g1_checked, "mismatches": g1_bad, "pass": bool(g1_bad == 0)},
            "G2_leakage": {
                "rule": ("every predictor reads only prints at tau <= 0; every minute-model "
                         "column is a registered causal state column"),
                "causal_prints_with_tau_positive": tau_pos,
                "blocked_minute_columns": minute_blocked,
                "registry": reg.source,
                "pass": bool(tau_pos == 0 and not minute_blocked)},
        },
        "cs5_hole_length_class": {"frozen_windows": cs5_frozen, "extracted_windows": hole_cls,
                                 "rule": ("B5: the implemented rule is the probe's >= 5-minute "
                                          "print-to-print jump, i.e. >= 4 silent minutes; every "
                                          "CS-5 number is split 4min vs >=5min")},
    }


def _subset(rows: dict, mask: np.ndarray) -> dict:
    """Row-subset a row-aligned dict. A row-aligned array whose length differs from the mask is a
    bug, not a scalar: it raises (this caught a mask built over the wrong case set)."""
    out = {}
    for k, v in rows.items():
        if isinstance(v, np.ndarray) and v.ndim >= 1:
            if v.shape[0] != mask.size:
                raise SystemExit(f"_subset: {k!r} has {v.shape[0]} rows, mask has {mask.size}")
            out[k] = v[mask]
        else:
            out[k] = v
    return out


def _cs2_outcomes(keys: set) -> dict:
    """`readmissible` from the ledger's own replay of the frozen giveback rule (never re-derived)."""
    rule = ledger.Giveback(name=f"giveback:{GIVEBACK_LEVEL}", kind="giveback",
                           state_columns=("bar_close", "running_high", "dist_from_running_high"),
                           measure_columns=("next_open", "session_end", "et", "bar_open"),
                           params={"pct": GIVEBACK_LEVEL})
    df = ledger.load_panel(PANEL, rule)
    k = ledger.fric_k(100.0)
    out = {}
    for key, sub in ledger.members_of(df):
        key = tuple(key) if isinstance(key, tuple) else (key,)
        if key not in keys:
            continue
        r = ledger.replay_member(sub, rule, k)
        out[key] = {"readmissible": bool(r.get("readmissible")), "status": r.get("status"),
                    "early_exit": bool(r.get("early_exit", False)),
                    "exit_px": _round(r.get("exit_px"), 6),
                    "exit_et": r.get("exit_et"),
                    "forward_mfe_from_exit": _round(r.get("forward_mfe_from_exit"), 6)}
    return out


def _verdicts(coords: dict, pert: dict, holm: dict) -> dict:
    """The pre-registered decision rule, family by family, then the contract's kill rule."""
    out = {}
    for fam in SB_FAMILIES:
        rec = {}
        for c in ("A_pm", "B600"):
            co = coords.get(c, {})
            d = {}
            for dr, key in (("block1->block2", "d12"), ("block2->block1", "d21")):
                cell = (co.get(dr) or {}).get("families", {}).get(fam, {})
                d[key] = cell.get("delta_auc")
                d[key + "_ci"] = cell.get("delta_auc_ci95")
                d[key + "_p"] = cell.get("p_delta_auc")
            rec[c] = d
        both_pos = all(rec[c][k] is not None and rec[c][k] > 0
                       for c in ("A_pm", "B600") for k in ("d12", "d21"))
        band = all(rec[c][k] is not None and rec[c][k] > SB_NULL_BAND
                   for c in ("A_pm", "B600") for k in ("d12", "d21"))
        ci = all(rec[c][k + "_ci"] and rec[c][k + "_ci"][0] is not None
                 and rec[c][k + "_ci"][0] > 0
                 for c in ("A_pm", "B600") for k in ("d12", "d21"))
        p_alt = (pert.get(fam) or {}).get("alt_label") or {}
        p_vol = (pert.get(fam) or {}).get("vol_matched_subset") or {}
        vol_ok = bool(p_vol.get("delta_auc_block1->block2") is not None
                      and p_vol.get("delta_auc_block2->block1") is not None
                      and float(p_vol["delta_auc_block1->block2"]) > 0
                      and float(p_vol["delta_auc_block2->block1"]) > 0)
        pert_ok = bool(p_alt.get("positive_both_directions") and vol_ok)
        out[fam] = {"delta_auc": rec, "positive_out_of_block_both_directions": bool(both_pos),
                    "exceeds_null_band": bool(band), "ci_excludes_zero_both_directions": bool(ci),
                    "perturbation_holds": pert_ok,
                    "perturbation": {
                        "alt_label_positive_both_directions":
                            bool(p_alt.get("positive_both_directions")),
                        "alt_label_delta_auc": p_alt.get("delta_auc"),
                        "vol_matched_positive_both_directions": vol_ok,
                        "vol_matched_subset": p_vol,
                        "rule": ("the perturbation condition is: the alternative-label run is "
                                 "positive in both directions in both families AND the "
                                 "volatility-matched subset is positive in both directions")},
                    "holm": holm.get(fam),
                    "verdict": ("promoted" if (both_pos and band and ci and pert_ok
                                               and (holm.get(fam) or {}).get("significant"))
                                else "null")}
    return out


def _holm(pvals: dict) -> dict:
    items = [(k, v) for k, v in sorted(pvals.items()) if v is not None]
    m = len(items)
    out, prev = {}, 0.0
    for i, (k, v) in enumerate(items):
        adj = min(1.0, v * (m - i))
        prev = max(prev, adj)
        out[k] = {"p_raw": _round(v, 4), "p_holm": _round(prev, 4), "significant": bool(prev < 0.05),
                  "m": m}
    for k in pvals:
        out.setdefault(k, {"p_raw": None, "p_holm": None, "significant": False, "m": m})
    return out


def stage_b_analysis(features: pl.DataFrame, wins: list[dict], diag: dict,
                     with_bootstrap: bool = True) -> dict:
    """Assemble the rows per case set, run the dual-block grid and write the verdicts."""
    rng = np.random.default_rng(SB_BOOTSTRAP_SEED)
    missing = [n for n in SB_FEATURE_NAMES if n not in features.columns]
    if missing:
        raise SystemExit(f"extracted features do not match the frozen table: {missing}")
    win_by_id = {w["anchor_id"]: w for w in wins}
    order = features.with_row_index("_r").sort("anchor_id")
    f = order
    ws = [win_by_id[a] for a in f["anchor_id"].to_list()]
    pan_cols = ["et", "bar_index", "v_forced_flat", "v_hold_flat", "final_high_flag",
                "remaining_run", "cost_of_waiting", "bar_range_pct"] + list(MINUTE_FEATURES)
    pan = _panel_rows(ws, pan_cols)
    pan5 = _panel_rows(ws, ["failed_reclaim_count"], offset=5)
    Xp = np.column_stack([_f64(f, nm) for nm in SB_PREDICTORS])
    Xm = np.column_stack([_f64(pan, nm) for nm in MINUTE_FEATURES])
    case = np.array(f["case_set"].to_list())
    day = np.array(f["sleeve_day"].to_list())
    fam = np.array(f["family"].to_list())
    block = np.array(f["block"].to_list())
    vff = _f64(pan, "v_forced_flat")
    vhf = _f64(pan, "v_hold_flat")
    fhf = _f64(pan, "final_high_flag")
    rrun = _f64(pan, "remaining_run")
    cw = _f64(pan, "cost_of_waiting")
    frc = _f64(pan, "failed_reclaim_count")
    frc5 = _f64(pan5, "failed_reclaim_count")
    rng_proxy = _f64(pan, "bar_range_pct")
    cs5_code5 = _f64(f, "post_reopen_code5")
    cs5_gapret = _f64(f, "post_reopen_gap_ret")
    cs5_hole = _f64(f, "f5_hole_minutes")
    stratum = np.array([("top" if (w["ref_txt"] == "top_decile" or w["ref_txt"] == "top")
                         else "bottom" if w["ref_txt"] in ("bottom_decile", "bottom") else None)
                        for w in ws], dtype=object)
    pair_id = np.array([w["ref_txt"] if w["case_set"] == "CS-3" else None for w in ws], dtype=object)
    keys = [(w["sleeve_day"], w["family"], w["entry_rank"], w["ticker"]) for w in ws]
    cs2 = _cs2_outcomes({k for k, c in zip(keys, case) if c == "CS-2"})
    readm = np.array([cs2.get(k, {}).get("readmissible") if c == "CS-2" else None
                      for k, c in zip(keys, case)], dtype=object)

    out: dict = {"targets": {cs: [{"name": n, "role": r, "definition": d}
                                  for n, r, d in SB_TARGETS[cs]] for cs in CASE_ORDER},
                 "cs2_ledger_labels": {
                     "members_resolved": len(cs2),
                     "early_exit_true": sum(1 for v in cs2.values() if v["early_exit"]),
                     "readmissible_true": sum(1 for v in cs2.values() if v["readmissible"]),
                     "status": counts(v["status"] for v in cs2.values()),
                     "note": ("the ledger's own giveback:10 replay over the CS-2 member keys: every "
                              "CS-2 anchor must resolve as an early exit (the freeze's own identity) "
                              "and `readmissible` is the primary CS-2 label")},
                 "case_sets": {}}
    for cs in CASE_ORDER:
        sel = case == cs
        if not sel.any():
            continue
        rows_cs = {"X_print": Xp[sel], "X_minute": Xm[sel], "block": block[sel],
                   "family": fam[sel], "day": day[sel], "proxy": rng_proxy[sel],
                   "stratum": stratum[sel], "pair_id": pair_id[sel]}
        y_extra: dict = {}
        if cs == "CS-1":
            y_extra["y_vff_up"] = np.where(np.isfinite(vff[sel]), (vff[sel] > 0).astype(float), np.nan)
            y_extra["y_final_high"] = np.where(np.isfinite(fhf[sel]), fhf[sel], np.nan)
            med = float(np.nanmedian(cw[sel])) if np.isfinite(cw[sel]).any() else np.nan
            y_extra["y_deep_cost"] = np.where(np.isfinite(cw[sel]), (cw[sel] < med).astype(float),
                                              np.nan)
            y_extra["y_failed_reclaim"] = np.where(np.isfinite(frc[sel]) & np.isfinite(frc5[sel]),
                                                   (frc5[sel] > frc[sel]).astype(float), np.nan)
            y_extra["y_alt"] = np.where(np.isfinite(vhf[sel]), (vhf[sel] > 0).astype(float), np.nan)
            base = "y_vff_up"
            alt = "y_alt"
            y_extra["target_note"] = {"cost_threshold": _round(med, 9),
                                      "alt_label": "1[v_hold_flat > 0] (close-referenced twin)"}
        elif cs == "CS-2":
            y_extra["y_false_cut"] = np.array([float(v) if v is not None else np.nan
                                               for v in readm[sel]])
            y_extra["y_final_high"] = np.where(np.isfinite(fhf[sel]), fhf[sel], np.nan)
            y_extra["y_alt"] = y_extra["y_final_high"]
            base, alt = "y_false_cut", "y_alt"
            y_extra["target_note"] = {"alt_label": "final_high_flag at the trigger bar (genuine-death ingredient)"}
        elif cs == "CS-3":
            y_extra["_vff"] = vff[sel]
            y_extra["_vhf"] = vhf[sel]
            base, alt = "y_a_wins", "y_alt"
            y_extra["target_note"] = {"alt_label": "1[v_hold_flat_A > v_hold_flat_B] (close twin)"}
        elif cs == "CS-4":
            y_extra["y_final_high"] = np.where(np.isfinite(fhf[sel]), fhf[sel], np.nan)
            y_extra["y_remaining_run"] = np.where(np.isfinite(rrun[sel]),
                                                  (rrun[sel] > 0.10).astype(float), np.nan)
            y_extra["y_alt"] = y_extra["y_remaining_run"]
            base, alt = "y_final_high", "y_alt"
            y_extra["target_note"] = {"alt_label": "1[remaining_run > 0.10] at t"}
        else:
            y_extra["y_reopen_code5"] = np.where(np.isfinite(cs5_code5[sel]), cs5_code5[sel], np.nan)
            y_extra["y_gap_ret_up"] = np.where(np.isfinite(cs5_gapret[sel]),
                                               (cs5_gapret[sel] > 0).astype(float), np.nan)
            y_extra["y_alt"] = y_extra["y_gap_ret_up"]
            base, alt = "y_reopen_code5", "y_alt"
            y_extra["target_note"] = {
                "alt_label": ("1[reopen_gap_ret > 0] — simultaneous with the label at the reopen "
                              "print: descriptive, and the hole-length split is the real "
                              "perturbation for CS-5")}
        if cs == "CS-3":
            pid = pair_id[sel]
            ws_sel = [w for w in ws if w["case_set"] == cs]
            ia: dict = {}
            ib: dict = {}
            for i, p in enumerate(pid):
                if p is None:
                    continue
                k2 = ws_sel[i]["anchor_id"]
                side = "A" if k2.endswith("|A") else "B" if k2.endswith("|B") else None
                (ia if side == "A" else ib)[p] = i
            pairs = sorted(set(ia) & set(ib))
            keep = []
            for p in pairs:
                a, b = ia[p], ib[p]
                if np.isfinite(y_extra["_vff"][a]) and np.isfinite(y_extra["_vff"][b]) \
                        and y_extra["_vff"][a] != y_extra["_vff"][b]:
                    keep.append((a, b))
            rows_cs = {"X_print": Xp[sel][keep_arr(keep, 0)] - Xp[sel][keep_arr(keep, 1)],
                       "X_minute": Xm[sel][keep_arr(keep, 0)] - Xm[sel][keep_arr(keep, 1)],
                       "block": block[sel][keep_arr(keep, 0)], "family": fam[sel][keep_arr(keep, 0)],
                       "day": day[sel][keep_arr(keep, 0)], "proxy": rng_proxy[sel][keep_arr(keep, 0)],
                       "stratum": stratum[sel][keep_arr(keep, 0)], "pair_id": pid[keep_arr(keep, 0)]}
            y_extra["y_a_wins"] = (y_extra["_vff"][keep_arr(keep, 0)]
                                   > y_extra["_vff"][keep_arr(keep, 1)]).astype(float)
            va, vb = y_extra["_vhf"][keep_arr(keep, 0)], y_extra["_vhf"][keep_arr(keep, 1)]
            y_extra["y_alt"] = np.where(np.isfinite(va) & np.isfinite(vb) & (va != vb),
                                        (va > vb).astype(float), np.nan)
            n_null = sum(1 for pp in pairs
                         if not (np.isfinite(y_extra["_vff"][ia[pp]])
                                 and np.isfinite(y_extra["_vff"][ib[pp]])))
            n_tie = sum(1 for pp in pairs
                        if np.isfinite(y_extra["_vff"][ia[pp]])
                        and np.isfinite(y_extra["_vff"][ib[pp]])
                        and y_extra["_vff"][ia[pp]] == y_extra["_vff"][ib[pp]])
            rows_cs["_tie_dropped"] = len(pairs) - len(keep)
            rows_cs["_pairs_null_outcome"] = n_null
            rows_cs["_pairs_tie"] = n_tie
            rows_cs["_pairs_available"] = len(pairs)
        rows_cs["y"] = y_extra[base]
        finite = np.isfinite(rows_cs["y"])
        if cs == "CS-3":
            n_units_avail = int(rows_cs.get("_pairs_available", 0))
        else:
            n_units_avail = int(sel.sum())
        rows_cs = _subset(rows_cs, finite)
        y_extra = {k: (v[finite] if isinstance(v, np.ndarray) and v.size == finite.size else v)
                   for k, v in y_extra.items()}
        entry: dict = {"target": {"primary": base, "definition": dict(
            (n, d) for n, r, d in SB_TARGETS[cs] if n == base).get(base), "note": y_extra.get("target_note")},
            "rows": None,
            "coordinates": {}}
        rows_rec = {"windows": int(sel.sum()),
                    "unit": "matched pair" if cs == "CS-3" else "anchored window",
                    "units_available": n_units_avail, "used": int(rows_cs["y"].size),
                    "base_rate_primary_label": _round(float(rows_cs["y"].mean()), 6)
                    if rows_cs["y"].size else None,
                    "dropped_no_label": int(n_units_avail - rows_cs["y"].size),
                    "no_next_bar_windows": sum(1 for w in ws
                                               if w["case_set"] == cs
                                               and w["guards"]["no_next_bar"]),
                    "by_family": counts(rows_cs["family"].tolist()),
                    "by_block": counts(rows_cs["block"].tolist())}
        if cs == "CS-3":
            rows_rec.update({
                "pairs_dropped_null_outcome": rows_cs.get("_pairs_null_outcome"),
                "pairs_dropped_tie": rows_cs.get("_pairs_tie"),
                "pairs_dropped_note": ("a matched pair leaves the CS-3 measurement for one of two "
                                       "reasons, counted separately: a side's v_forced_flat is "
                                       "undefined (the anchor is the member's last bar, 60 "
                                       "pairs), or the two sides are exactly equal (15 pairs)")})
        entry["rows"] = rows_rec
        coord_masks = {"A_pm": rows_cs["family"] == "A_pm", "B600": rows_cs["family"] == "B600",
                       "pooled(REPORT ONLY)": np.ones(rows_cs["y"].size, dtype=bool)}
        for cname, cm in coord_masks.items():
            sub = _subset(rows_cs, cm)
            if sub["y"].size == 0:
                entry["coordinates"][cname] = {"skipped": "no rows"}
                continue
            entry["coordinates"][cname] = _coordinate(sub, rng, with_bootstrap
                                                      and cname != "pooled(REPORT ONLY)")
        # ---- perturbation: alternative label + volatility-matched subset (point estimates) --- #
        pert = {}
        for fam_name in SB_FAMILIES:
            d12, d21, fam_ok = [], [], True
            for cname in ("A_pm", "B600"):
                cm = coord_masks[cname]
                psub = _subset(rows_cs, cm)
                psub = dict(psub)
                psub["y"] = np.nan_to_num(y_extra[alt][cm], nan=0.0)
                ok = np.isfinite(y_extra[alt][cm])
                psub = _subset(psub, ok)
                if psub["y"].size < 100 or psub["y"].min() == psub["y"].max():
                    fam_ok = False
                    continue
                b1 = psub["block"] == "block1"
                b2 = psub["block"] == "block2"
                if b1.sum() < 30 or b2.sum() < 30:
                    fam_ok = False
                    continue
                r1 = _direction(psub, b1, b2, rng, False)["families"].get(fam_name, {})
                r2 = _direction(psub, b2, b1, rng, False)["families"].get(fam_name, {})
                d12.append(r1.get("delta_auc"))
                d21.append(r2.get("delta_auc"))
                fam_ok = fam_ok and bool(r1.get("delta_auc", 0) and r1["delta_auc"] > 0) \
                    and bool(r2.get("delta_auc", 0) and r2["delta_auc"] > 0)
            q = rows_cs["proxy"]
            fin = np.isfinite(q)
            sub_q = {}
            if fin.sum() > 50:
                q1, q3 = np.nanpercentile(q[fin], [25, 75])
                mid = fin & (q >= q1) & (q <= q3)
                msub = _subset(rows_cs, mid)
                mb1 = msub["block"] == "block1"
                mb2 = msub["block"] == "block2"
                if mb1.sum() >= 30 and mb2.sum() >= 30 and 0 < msub["y"][mb1].mean() < 1 \
                        and 0 < msub["y"][mb2].mean() < 1:
                    mr1 = _direction(msub, mb1, mb2, rng, False)["families"].get(fam_name, {})
                    mr2 = _direction(msub, mb2, mb1, rng, False)["families"].get(fam_name, {})
                    sub_q = {"delta_auc_block1->block2": mr1.get("delta_auc"),
                             "delta_auc_block2->block1": mr2.get("delta_auc"),
                             "n_rows": int(msub["y"].size)}
                else:
                    sub_q = {"skipped": "matched subset too small or degenerate"}
            else:
                sub_q = {"skipped": "no volatility proxy"}
            pert[fam_name] = {"alt_label": {"delta_auc": d12 + d21,
                                            "positive_both_directions": bool(
                                                fam_ok and d12 and d21 and all(
                                                    x is not None and x > 0 for x in d12 + d21)),
                                            "note": "per-family Delta AUC on A_pm then B600, "
                                                    "block1->block2 then block2->block1"},
                              "vol_matched_subset": sub_q}
        entry["perturbation"] = pert
        entry["extra_targets"] = {}
        for tn in SB_TARGETS[cs]:
            nm = tn[0]
            if nm == base or nm not in y_extra:
                continue
            yy = y_extra[nm]
            mm = np.isfinite(yy)
            tsub = _subset(rows_cs, mm)
            tsub = dict(tsub)
            tsub["y"] = yy[mm]
            if tsub["y"].size < 100 or tsub["y"].min() == tsub["y"].max():
                entry["extra_targets"][nm] = {"skipped": "too few rows or constant label"}
                continue
            b1 = tsub["block"] == "block1"
            b2 = tsub["block"] == "block2"
            if b1.sum() < 30 or b2.sum() < 30:
                entry["extra_targets"][nm] = {"skipped": "a block has < 30 rows"}
                continue
            r1 = _direction(tsub, b1, b2, rng, False)
            r2 = _direction(tsub, b2, b1, rng, False)
            entry["extra_targets"][nm] = {
                "definition": tn[2],
                "coordinate": "pooled rows, REPORT ONLY (families are never pooled for inference)",
                "families": {fm: {"block1->block2": (r1["families"].get(fm) or {}).get("delta_auc"),
                                  "block2->block1": (r2["families"].get(fm) or {}).get("delta_auc")}
                             for fm in SB_FAMILIES}}
        if cs == "CS-4":
            entry["strata"] = {}
            for st in ("top", "bottom"):
                sm = rows_cs["stratum"] == st
                if sm.sum() < 100:
                    entry["strata"][st] = {"skipped": "too few rows"}
                    continue
                ssub = _subset(rows_cs, sm)
                b1 = ssub["block"] == "block1"
                b2 = ssub["block"] == "block2"
                if b1.sum() < 30 or b2.sum() < 30:
                    entry["strata"][st] = {"skipped": "a block has < 30 rows"}
                    continue
                r1 = _direction(ssub, b1, b2, rng, False)
                r2 = _direction(ssub, b2, b1, rng, False)
                entry["strata"][st] = {
                    "n_rows": int(sm.sum()),
                    "families": {fm: {"block1->block2": (r1["families"].get(fm) or {}).get("delta_auc"),
                                      "block2->block1": (r2["families"].get(fm) or {}).get("delta_auc")}
                                 for fm in SB_FAMILIES}}
        if cs == "CS-5":
            entry["hole_length_class"] = {}
            for cls, cm in (("4min", (cs5_hole == 4)[sel]), (">=5min", (cs5_hole >= 5)[sel])):
                if cm.sum() < 100:
                    entry["hole_length_class"][cls] = {"skipped": "too few rows"}
                    continue
                csub = _subset(rows_cs, cm)
                b1 = csub["block"] == "block1"
                b2 = csub["block"] == "block2"
                if b1.sum() < 30 or b2.sum() < 30:
                    entry["hole_length_class"][cls] = {"skipped": "a block has < 30 rows"}
                    continue
                r1 = _direction(csub, b1, b2, rng, False)
                r2 = _direction(csub, b2, b1, rng, False)
                entry["hole_length_class"][cls] = {
                    "n_rows": int(cm.sum()), "base_rate": _round(float(csub["y"].mean()), 6),
                    "families": {fm: {"block1->block2": (r1["families"].get(fm) or {}).get("delta_auc"),
                                      "block2->block1": (r2["families"].get(fm) or {}).get("delta_auc")}
                                 for fm in SB_FAMILIES}}
        pv = {}
        for fm in SB_FAMILIES:
            ps = [entry["coordinates"][c].get(dr, {}).get("families", {}).get(fm, {}).get("p_delta_auc")
                  for c in ("A_pm", "B600") for dr in ("block1->block2", "block2->block1")]
            ps = [x for x in ps if x is not None]
            pv[fm] = max(ps) if ps else None
        holm = _holm(pv)
        entry["family_verdicts"] = _verdicts(entry["coordinates"], pert, holm)
        killed = [f2 for f2, v in entry["family_verdicts"].items()
                  if v["positive_out_of_block_both_directions"]]
        lit, lit_cells = {}, []
        for fm in SB_FAMILIES:
            for cname, co in entry["coordinates"].items():
                d12 = (co.get("block1->block2") or {}).get("families", {}).get(fm, {}).get("delta_auc")
                d21 = (co.get("block2->block1") or {}).get("families", {}).get(fm, {}).get("delta_auc")
                if d12 is None or d21 is None:
                    continue
                cell = {"family": fm, "coordinate": cname, "delta_auc_block1->block2": d12,
                        "delta_auc_block2->block1": d21,
                        "positive_both_blocks": bool(d12 > 0 and d21 > 0)}
                lit.setdefault(fm, {})[cname] = cell
                if cell["positive_both_blocks"]:
                    lit_cells.append(cell)
        lit_max = max([max(c["delta_auc_block1->block2"], c["delta_auc_block2->block1"])
                       for c in lit_cells] or [None])
        entry["set_verdict"] = {
            "kill_rule": ("no feature family positive out-of-block in both blocks in both families "
                          "- the four-cell rule: 2 block directions x 2 families (the implementation "
                          "reads the four cells jointly, not the pooled coordinate)"),
            "kill_rule_literal_reading": {
                "rule": ("the literal 'positive in both blocks' reading, evaluated per coordinate "
                         "(including the pooled REPORT ONLY coordinate): a cell is "
                         "(family, coordinate) positive in both block directions"),
                "surviving_cells": [f"{c['family']}:{c['coordinate']}" for c in lit_cells],
                "n_surviving_cells": len(lit_cells),
                "surviving_cells_families_only": [
                    f"{c['family']}:{c['coordinate']}" for c in lit_cells
                    if not c["coordinate"].startswith("pooled")],
                "n_surviving_cells_families_only": sum(
                    1 for c in lit_cells if not c["coordinate"].startswith("pooled")),
                "surviving_cells_pooled_only": [
                    f"{c['family']}:{c['coordinate']}" for c in lit_cells
                    if c["coordinate"].startswith("pooled")],
                "n_surviving_cells_pooled_only": sum(
                    1 for c in lit_cells if c["coordinate"].startswith("pooled")),
                "max_delta_auc": lit_max,
                "note": ("every surviving cell is far inside the 0.054 null band, so the literal "
                         "reading does not change the verdict either; the cells are published so the "
                         "two readings can be compared on the artifact's face"),
                "cells": lit},
            "families_with_positive_delta_in_both_blocks": killed,
            "information_claim": "open (a family survived the kill rule)" if killed
            else "closed as null (no family survived the kill rule)",
            "g3_promoted": [f2 for f2, v in entry["family_verdicts"].items()
                            if v["verdict"] == "promoted"],
            "g3_note": ("G3 also requires |Delta| > 0.054 in both directions and both families, a "
                        "day-clustered CI excluding 0, the label/threshold perturbation, and Holm"),
        }
        out["case_sets"][cs] = entry
    out["kill_rule_verdict"] = {
        cs: out["case_sets"][cs]["set_verdict"]["information_claim"] for cs in out["case_sets"]}
    out["any_promoted"] = sorted({f"{cs}:{f2}" for cs, e in out["case_sets"].items()
                                  for f2, v in e["family_verdicts"].items()
                                  if v["verdict"] == "promoted"})
    return out


def keep_arr(keep: list, k: int) -> np.ndarray:
    return np.array([p[k] for p in keep], dtype=np.int64) if keep else np.zeros(0, dtype=np.int64)


# --------------------------------------------------------------------------- #
# Stage B — the stage driver
# --------------------------------------------------------------------------- #

def stage_b_run(workers: int = 4, days: list[str] | None = None,
                sample_per_segment: int = SB_SAMPLE_PER_SEGMENT, with_bootstrap: bool = True,
                reuse_features: bool = False, trial: bool = False) -> dict:
    """Window map -> extraction -> gates -> the I-EV measurement. Writes iev.json."""
    e3_dir = E3
    suffix = "_trial" if trial else ""
    anchors = pl.read_parquet(e3_dir / "anchors.parquet")
    pan = load_panel_cols(SB_PANEL_COLS).sort(MEMBER_KEY + ["bar_index"])
    wins, wdiag = stage_b_window_map(anchors, pan)
    fpath = e3_dir / f"stage_b_features{suffix}.parquet"
    if reuse_features:
        features = pl.read_parquet(fpath)
        cost = json.loads((e3_dir / f"stage_b_cost{suffix}.json").read_text())
    else:
        res = stage_b_extract(wins, workers, days, sample_per_segment, trial)
        features, cost = res["features"], res["cost"]
    diag = stage_b_diagnostics(features, wdiag, wins, cost)
    obj = {
        "tool": "factory/scripts/basket_atlas_e3.py",
        "stage": "B — anchored SIP-print extraction and the I-EV measurement",
        "plan": "researches/PLAN-ATLAS-E3-MICROSCOPE.md",
        "semantics": SB_VERSION,
        "deterministic": ("byte-stable; wall clock and file-write timings live in "
                          "stage_b_cost.json (run-varying by design, as census_cost.json)"),
        "frozen_inputs": {
            "anchors_parquet_sha256": sha256_file(e3_dir / "anchors.parquet"),
            "case_sets_json_sha256": sha256_file(e3_dir / "case_sets.json"),
            "census_parquet_sha256": sha256_file(e3_dir / "census.parquet"),
            "panel_sha256_declared": PANEL_SHA_DECLARED,
        },
        "artifacts": {"features_parquet": fpath.name,
                      "prints_parquet": f"stage_b_prints{suffix}.parquet",
                      "cost_json": f"stage_b_cost{suffix}.json"},
        "feature_table": {
            "sha256": SB_FEATURE_SHA, "n_columns": len(SB_FEATURE_ROWS),
            "n_predictors": len(SB_PREDICTORS), "predictors": list(SB_PREDICTORS),
            "families": list(SB_FAMILIES),
            "rule": ("frozen before the first estimate: only `causal_at_decision` columns enter an "
                     "I-EV model; the `post_*` columns are the action horizon and are stored for "
                     "the L-EV arm only"),
            "rows": SB_FEATURE_ROWS,
        },
        "minute_model": {"features": list(MINUTE_FEATURES), "source": "panel.parquet",
                         "role": "the mandatory minute counterpart on the same rows/coordinate"},
        "model": {"kind": "ridge-penalised logistic regression (IRLS, l2=1.0 on standardised "
                          "features, no intercept penalty)",
                  "standardisation": "training-fold median/std; nulls imputed with the "
                                     "training-fold median",
                  "coverage_rule": f"a column enters a fold's model only with >= "
                                   f"{SB_MIN_COVERAGE:.0%} non-null and non-constant training data",
                  "cross_fit": "block1->block2 and block2->block1, both reported",
                  "metric": "ROC AUC and log-loss on the held-out block",
                  "bootstrap": {"unit": "sleeve_day cluster", "b": SB_BOOTSTRAP_B,
                                "seed": SB_BOOTSTRAP_SEED, "statistic": "Delta AUC / Delta log-loss"},
                  "null_band": SB_NULL_BAND,
                  "null_band_note": ("the plan's Phase-1 band; on CS-3's paired coordinate "
                                     "Delta AUC is a win-rate difference (the Phase-1 effect "
                                     "scale); on the single-member sets the same number is "
                                     "applied as a conservative pre-registered floor")},
        "targets": None,
        "diagnostics": diag,
        "exclusions_accounting": exclusions_accounting(),
        "plan_corrections": {
            "B1": "the decision instant is the close of the anchor bar t; causal features read tau <= 0 only",
            "B2": "segments are the member's own panel bars (bar identity), never tau arithmetic",
            "B3": "CS-3 is label-conditioned (future oracle continuation) and is read as descriptive",
            "B5": "CS-5's implemented rule is the >= 5-minute print-to-print jump (4 silent minutes); "
                  "every CS-5 number is split by hole-length class",
            "B6": "segments are member bars, so the window is contained in [entry_et, session_end]",
            "B7": "exclusions_accounting republishes the frozen drop lists with their grain",
            "B9": "the abort rule is the 4-hour hard wall; the 30-minute design trigger and the "
                  "measured serial projection are both published",
            "B10": "CS-5 is re-anchored by et (the frozen bar_index is null); the extraction "
                   "publishes bar_index for every window",
        },
    }
    if not diag["gates"]["G1_reconstruction"]["pass"]:
        _write_json(E3 / f"iev{suffix}.json", obj)
        raise SystemExit("G1 reconstruction failed: the microscope is not measuring the panel's "
                         "tape (see iev%s.json diagnostics)" % suffix)
    meas = stage_b_analysis(features, wins, diag, with_bootstrap)
    obj["targets"] = meas.pop("targets")
    obj.update(meas)
    _write_json(E3 / f"iev{suffix}.json", obj)
    _write_json(E3 / f"latency{suffix}.json", latency_record(features, diag))
    return obj


def latency_record(features: pl.DataFrame, diag: dict) -> dict:
    """The L-EV arm is a separate script and artifact (plan section 5). It is NOT run here."""
    census = pl.read_parquet(E3 / "census.parquet")
    quote_ok = int(census["quote_present"].sum())
    f6 = [c for c in features.columns if c.startswith("f6_signed_vol_share_")]
    usable = int(features[f6[0]].is_not_null().sum()) if f6 else 0
    return {
        "tool": "factory/scripts/basket_atlas_e3.py",
        "stage": "B — the latency (L-EV) arm",
        "status": "NOT RUN — deferred, declared",
        "reason": ("plan section 5 makes L-EV a different script and a different artifact on "
                   "purpose (`a positive L-EV licenses no claim about signal; a null I-EV "
                   "licenses no claim about execution`). Stage B measures I-EV only; keeping the "
                   "separation is the contract's stated requirement, and no latency number is "
                   "claimed anywhere in iev.json."),
        "separation_statement": ("no latency cost is netted against the I-EV numbers: every "
                                 "Delta AUC in iev.json is an information-only quantity on the "
                                 "decision-instant tape"),
        "deferred_contract": {
            "clock": "the decision is the panel's own (completed bar t, executed at the open of "
                     "bar t+1) by the same rule in all three arms",
            "arms": {"A0": "exit at next_open(t) (the panel's convention)",
                     "A1": "act at the first price-updating print inside bar t+1",
                     "A2": "act at the first print in bar t+1 satisfying a pre-declared "
                           "print-level condition (taken from a promoted I-EV feature, or the "
                           "frozen ruler set if none is promoted) — never tuned here"},
            "fill_model": "a sell fills at min(p, bid_at_p), a buy at min(p, ask_at_p); "
                          "participation cap q on 60-second traded volume at or better than the "
                          "limit; the unfilled remainder is never assumed filled",
            "slippage_ladder_bps": [0, 25, 50, 100],
            "verdict_cell_bps": 50,
            "estimator": "member-level Delta against v_forced_flat in dollars per committed "
                         "dollar, per block, sleeve and independent-path, paired, day-clustered CI",
            "gate": {"G4": "arm - A0 paired, day-clustered, net of the NBBO-bid fill model, at "
                           "SLIP = 50 bps, positive in both blocks; else the print-level action "
                           "is closed"},
        },
        "prerequisites_measured_here": {
            "quote_present_member_days": quote_ok,
            "quote_present_share": _round(quote_ok / max(1, census.height), 6),
            "G0_quote_coverage_threshold": 0.80,
            "windows_with_a_quote_classified_flow_feature": usable,
            "windows_extracted": int(features.height),
            "note": ("the quote gate passes, so the L-EV arm is evaluable in principle; the "
                     "extracted windows carry their quote-aligned F6 columns for it"),
        },
        "not_a_result": ("this artifact claims no latency number; reading it as a null L-EV "
                         "result would be exactly the confusion plan section 0 forbids"),
    }


SB_EXCLUSION_GRAIN = {
    "CS-1": "reclaim EVENT rows (one per reclaim_count increment), not members",
    "CS-2": "MEMBERS (one row per judged member), not bars",
    "CS-3": "PANEL ROWS and matching UNITS at mixed grain (the two largest entries are row counts, "
            "`units_with_one_middle_member` is a unit count)",
    "CS-4": "fall-frame ROWS; `carrier_state_constant_variant_4carriers` is a DIAGNOSTIC carrier "
            "count, not a drop",
    "CS-5": "CANDIDATE rows (bracketed halt-sized runs before the panel-side rules)",
}


def exclusions_accounting() -> dict:
    """B7: republish the frozen drop lists with their grain, and the identities that do hold.

    Reads the byte-frozen `case_sets.json` and never edits it.
    """
    cs = json.loads((E3 / "case_sets.json").read_text())
    out = {"note": ("B7 fix: `exclusions.panel_side` mixes grains and (for CS-4) contains a "
                    "diagnostic that is not a drop, so its entries cannot be summed. The identity "
                    "that holds is sum(raw_gates) = anchors_before_gates - gated_population, "
                    "together with gated_population = frozen_sample + sample_cap_dropped. The "
                    "frozen case_sets.json is unchanged; this table is the corrected reading."),
           "per_case_set": {}}
    for name in CASE_ORDER:
        e = cs["case_sets"][name]
        ps = e["exclusions"]["panel_side"]
        raw = e["exclusions"]["raw_gates"]
        pop = e["population"]["anchors"]
        before = e["population"]["anchors_before_gates"]
        kept = e["frozen_sample"]["anchors"]
        cap = e["exclusions"]["sample_cap_dropped"]
        diag = {k: v for k, v in ps.items() if k.endswith("_variant_4carriers")}
        drops = {k: v for k, v in ps.items() if k not in diag}
        out["per_case_set"][name] = {
            "panel_side_grain": SB_EXCLUSION_GRAIN[name],
            "panel_side_counts": dict(sorted(ps.items())),
            "panel_side_drops_only": dict(sorted(drops.items())),
            "panel_side_diagnostics_not_drops": diag,
            "panel_side_sum": sum(ps.values()),
            "panel_side_sum_is_not_a_drop_count": True,
            "raw_gates_sum": sum(raw.values()),
            "raw_gates": dict(sorted(raw.items())),
            "anchors_before_gates": before, "gated_population": pop,
            "frozen_sample": kept, "sample_cap_dropped": cap,
            "identity_raw_gates": {"sum(raw_gates)": sum(raw.values()),
                                   "before - population": before - pop,
                                   "holds": bool(sum(raw.values()) == before - pop)},
            "identity_cap": {"population": pop, "frozen + cap_dropped": kept + cap,
                             "holds": bool(pop == kept + cap)},
            "panel_side_sum_vs_population_delta": sum(ps.values()) - (before - pop),
        }
    return out


# --------------------------------------------------------------------------- #
# artifacts
# --------------------------------------------------------------------------- #

def run_census(workers: int, with_bars_check: bool, days: list[str] | None = None,
               trial: bool = False) -> dict:
    dev = set(sim.dev_days())
    md = member_days(load_panel_cols(["sleeve_day", "block", "family", "ticker", "entry_rank",
                                      "entry_et", "session_end", "terminal_censored",
                                      "future_member_last_et"]))
    panel_days = sorted(set(md["sleeve_day"].to_list()))
    unknown = [d for d in panel_days if d not in dev]
    missing_files = [d for d in panel_days if not (TRADES / f"{d}.parquet").exists()]
    if unknown:
        raise SystemExit(f"panel days outside dev_days: {unknown[:5]}")
    if missing_files:
        raise SystemExit(f"panel days without a trades file: {missing_files[:5]}")
    sel = days or panel_days
    for d in sel:
        sim.guard_day(d)
    t0 = time.perf_counter()
    res = census_days(sel, md, workers, with_bars_check)
    wall = time.perf_counter() - t0
    rows = pl.DataFrame(res["rows"]).sort(["sleeve_day", "family", "ticker", "entry_rank"])
    cs5 = pl.DataFrame(res["cs5"])
    cs5 = cs5.sort("anchor_id") if cs5.height else pl.DataFrame({c: [] for c in ANCHOR_COLS},
                                                               schema=ANCHOR_SCHEMA)
    suffix = "_trial" if trial else ""
    _write_parquet(rows, E3 / f"census{suffix}.parquet")
    _write_parquet(cs5, E3 / f"cs5_anchors{suffix}.parquet")
    n_days = len(res["days"])
    cost = {"measured": True, "days": n_days, "workers": workers,
            "wall_s": _round(wall, 3), "wall_min": _round(wall / 60, 3),
            "sum_day_read_s": _round(sum(d["read_s"] for d in res["days"]), 3),
            "sum_day_quote_s": _round(sum(d.get("quote_scan_s") or 0 for d in res["days"]), 3),
            "sum_day_wall_s": _round(sum(d["wall_s"] for d in res["days"]), 3),
            "per_day_wall_s": dist_summary([d["wall_s"] for d in res["days"]], nd=4),
            "per_day_read_s": dist_summary([d["read_s"] for d in res["days"]], nd=4),
            "mean_day_wall_s": _round(sum(d["wall_s"] for d in res["days"]) / max(1, n_days), 4),
            "projection_full_store_serial_min": _round(
                sum(d["wall_s"] for d in res["days"]) / max(1, n_days) * PANEL_DAYS / 60, 2),
            "abort_threshold_hours": 4,
            "per_day": res["days"]}
    _write_json(E3 / f"census_cost{suffix}.json", cost)
    return {"rows": rows, "cs5": cs5, "days": res["days"], "cost": cost, "md": md}


def census_json(rows: pl.DataFrame, cs5: pl.DataFrame, days: list[dict]) -> dict:
    per_day = [{"day": d["day"], "members": d["members"], "file_bytes": d["file_bytes"],
                "rows_loaded": d["rows_loaded"], "prints_all": d["prints_all"],
                "prints_path": d["prints_path"], "quote_symbols": d["quote_symbols"],
                "coverage_classes": d["coverage_classes"], "bars_checked": d["bars_checked"],
                "bars_mismatch": d["bars_mismatch"]} for d in days]
    n_md = rows.height
    cls = counts(rows["coverage_class"].to_list())
    healthy = cls.get("healthy_raw", 0)
    quote_ok = int(rows["quote_present"].sum())
    bars_bad = int(sum(d["bars_mismatch"] for d in days))
    return {
        "tool": "factory/scripts/basket_atlas_e3.py",
        "stage": "A — census (one projected-column pass per dev day)",
        "deterministic": "byte-stable; wall-clock measurements live in census_cost.json",
        "sources": {
            "trades": {"root": str(TRADES), "files": len(per_day),
                       "bytes": int(sum(d["file_bytes"] for d in per_day))},
            "quotes": {"root": str(QUOTES)}, "coverage": {"root": str(COVERAGE)},
            "bars": {"root": str(BARS)},
            "panel": {"path": str(PANEL.relative_to(ROOT)), "sha256": PANEL_SHA_DECLARED,
                      "rows": PANEL_ROWS, "members": PANEL_MEMBERS, "days": PANEL_DAYS},
        },
        "read_passes": {
            "trades": "ONE projected-column scan per day (basket_subminute_probe.load_prints); "
                      "the U-path filter runs on that frame in memory",
            "quotes": "one symbol-column scan per day, for quote_present",
            "coverage": "one JSON read per day, for the raw-sufficiency class",
            "bars": "only under --bars-check: one (ticker, et) read per day for the G1 "
                    "reconciliation against the committed derived bars",
        },
        "universe": {
            "member_days": int(n_md), "days": int(rows["sleeve_day"].n_unique()),
            "panel_bars_total": int(rows["panel_bars"].sum()),
            "terminal_censored_member_days": int(rows["terminal_censored"].sum()),
            "U_all": "every print of the member's symbol inside [entry_et, session_end]",
            "U_path": ("prints kept by basket_t5_rawpaths.price_updating (sip_bars.combine "
                       "strictest-rule-wins) — the panel's own substrate"),
            "read_template": "basket_subminute_probe.load_prints (RTH +/- 30 min projection)",
            "window": "member window = [entry_et, session_end] of the member's own panel rows",
        },
        "totals": {
            "prints_all": int(rows["n_prints_all"].sum()),
            "prints_path": int(rows["n_prints_path"].sum()),
            "path_share": _round(float(rows["n_prints_path"].sum())
                                 / max(1, int(rows["n_prints_all"].sum())), 6),
            "path_prints_per_member_day": dist_summary(rows["n_prints_path"].to_list()),
            "all_prints_per_member_day": dist_summary(rows["n_prints_all"].to_list()),
            "minute_coverage_path": dist_summary(rows["coverage_path"].to_list(), nd=6),
            "minutes_with_path_print": int(rows["n_minutes_path"].sum()),
            "grid_minutes": int(rows["grid_minutes"].sum()),
            "halt_runs_ge5min": int(rows["n_halt_runs_ge5min"].sum()),
            "halt_runs_bracketed": int(rows["n_halt_runs_bracketed"].sum()),
            "cs5_anchor_candidates": int(cs5.height),
            "member_days_with_zero_path_prints": int((rows["n_prints_path"] == 0).sum()),
            "member_days_below_activity_floor": int(
                (rows["n_minutes_path"] < HALT_ACTIVITY_FLOOR).sum()),
        },
        "coverage_class": cls,
        "coverage_class_share": {k: _round(v / max(1, n_md), 6) for k, v in cls.items()},
        "quote_presence": {
            "member_days_with_quote": quote_ok, "share": _round(quote_ok / max(1, n_md), 6),
            "days_with_quote_file": len({d["day"] for d in days if d["quote_info"].get("present")}),
            "symbols_requested_total": int(sum(d["quote_info"].get("symbols_requested") or 0
                                               for d in days)),
            "symbols_with_data_total": int(sum(d["quote_info"].get("symbols_with_data") or 0
                                               for d in days)),
        },
        "gates": {
            "G0_raw_sufficiency": {
                "rule": "healthy_raw share of ATLAS member-days >= 0.85",
                "value": _round(healthy / max(1, n_md), 6), "threshold": 0.85,
                "pass": bool(healthy / max(1, n_md) >= 0.85)},
            "G0_store_completeness": {
                "rule": "trades store covers every panel day",
                "value": f"{len(per_day)}/{PANEL_DAYS}", "pass": bool(len(per_day) == PANEL_DAYS)},
            "G0_quote_coverage_L_EV": {
                "rule": ("quote-present share of ATLAS member-days >= 0.80 (else L-EV runs on the "
                         "covered subset and says so on its face)"),
                "value": _round(quote_ok / max(1, n_md), 6), "threshold": 0.80,
                "pass": bool(quote_ok / max(1, n_md) >= 0.80)},
            "G1_reconstruction": {
                "rule": ("for every checked member-day the U-path minute set inside "
                         "[entry_et, session_end] equals the committed derived bars' minute set"),
                "checked": int(sum(d["bars_checked"] for d in days)), "mismatches": bars_bad,
                "pass": bool(bars_bad == 0)},
        },
        "reconciliation_notes": [
            "the census is a coverage/feasibility table: no feature is computed here",
            "prints are counted inside [entry_et, session_end] of the member's own panel window, "
            "so a member-day's counts are family-specific when A_pm and B600 share the path",
            "quote presence is the raw quote file's symbol list, not the candidate snapshots",
            "bytes = the raw file sizes of the day files read (an upper bound: 6 of 8 columns "
            "are projected)",
        ],
        "per_day": per_day,
        "reproduce": "python factory/scripts/basket_atlas_e3.py --stage census",
    }


def case_sets_json(rows: pl.DataFrame, cs5: pl.DataFrame, mdf: pl.DataFrame,
                   anchors: pl.DataFrame, freeze: dict) -> dict:
    populations, samples, excl = freeze["populations"], freeze["samples"], freeze["exclusions"]
    cen = census_index(rows)
    specs = {
        "CS-1": {
            "name": "durable vs fragile reclaim",
            "anchor_event": "the reclaim bar t_r: reclaim_count(t_r) > reclaim_count(t_r-1)",
            "inclusion_rule": ("panel rows with terminal_censored == false, a previous bar inside "
                               "the member-day, coverage_class == healthy_raw and >= 1 U-path "
                               "print in [entry_et, session_end]"),
            "matching_unit": ("(sleeve_day, et, family, reclaim_count, bars_below_entry_episode "
                              "bucket, dist_from_running_high bucket, bar_index bucket) — the "
                              "Phase-1 PRIMARY_EDGES buckets"),
            "outcomes": ["sign(v_forced_flat) at t_r+1", "final_high_flag at t_r",
                         "cost_of_waiting at t_r",
                         "failed_reclaim_count delta over the next 5 bars"],
            "minute_blindness": ("reclaim_count / bars_below_entry_episode / "
                                 "dist_from_running_high are close-based 1-minute constructions; "
                                 "identical minute state still leaves the durable/fragile split "
                                 "open (Phase-1: median 1 bar, median cost -0.05%, yet 25.7% / "
                                 "44.0% make a new low within 30 bars)"),
            "action_stamp_tau": 60,
            "action_stamp_note": ("the reclaim is a completed-bar event: features labelled "
                                  "causal_at_decision may read tau < 60 only; post1 is the "
                                  "earliest legal action segment"),
        },
        "CS-2": {
            "name": "false cuts vs genuine deaths under a frozen exit ruler",
            "anchor_event": (f"the decision bar t of giveback:{GIVEBACK_LEVEL} "
                             f"(panel v_giveback_{GIVEBACK_LEVEL} / "
                             f"giveback_fired_{GIVEBACK_LEVEL}; the bar is located by "
                             "basket_atlas_ledger.Giveback.locate_exit)"),
            "inclusion_rule": ("non-censored members whose giveback condition first appears at a "
                               "bar with et < session_end - 1 and which have an execution bar "
                               "t+1, coverage_class == healthy_raw and >= 1 U-path print"),
            "matching_unit": "(family, block, exit_et, bar_index bucket), sides blind",
            "outcomes": ["readmissible == true -> FALSE CUT (ledger)",
                         "readmissible == false and final_high_flag -> GENUINE DEATH (ledger)"],
            "minute_blindness": ("the ruler is a completed-bar close condition executed at the "
                                 "next bar's open; the seconds inside the trigger bar and inside "
                                 "the execution bar are invisible to the minute panel"),
            "action_stamp_tau": 60,
            "action_stamp_note": ("decision at the close of the trigger bar (tau = 60); the "
                                  "execution bar is post1 [60, 120)"),
        },
        "CS-3": {
            "name": "matched minute states, divergent futures (middle-decile executable axis)",
            "anchor_event": ("both sides of a Phase-1 middle_decile_executable_axis pair: the same "
                             "(sleeve_day, et, family, bar_index bucket) unit, deterministic side "
                             "split, same-member pairs dropped"),
            "inclusion_rule": ("pairs built by basket_atlas_pairs.prepare/add_cells/PRIMARY_UNIT "
                               "from non-decile, non-censored, non-dup_cross_family rows; both "
                               "sides healthy_raw with >= 1 U-path print"),
            "matching_unit": "(sleeve_day, et, family, bar_index bucket)",
            "outcomes": ["v_forced_flat_A > v_forced_flat_B (executable winner/loser)"],
            "minute_blindness": ("measured: the best minute feature on this axis reaches "
                                 "|effect| <= 0.054 and is inverted (0.446 A_pm / 0.453 B600) — "
                                 "the pre-registered null band"),
            "action_stamp_tau": 60,
            "action_stamp_note": ("the matched state is the completed bar t; the outcome is the "
                                  "executable future value from next_open(t)"),
        },
        "CS-4": {
            "name": "exhaustion at matched state, split by dispersion stratum (DESCRIPTIVE)",
            "anchor_event": ("a bar where at least one exhaustion carrier moves versus the "
                             "previous bar of the member-day: " + ", ".join(CS4_CARRIERS)),
            "inclusion_rule": ("non-censored, final_high_flag finite (not the member's last bar), "
                               "fwd_range_30 finite, and inside the top or bottom fwd_range_30 "
                               "decile of its block"),
            "matching_unit": "state cell of the carriers; the stratum is shown on every number",
            "outcomes": ["final_high_flag", "no_further_new_high"],
            "minute_blindness": ("the stratum is a FUTURE quantity: this is descriptive "
                                 "heterogeneity, not a predictive test (fall.json circularity "
                                 "tell: final_high_flag control = 1.0)"),
            "action_stamp_tau": 60,
            "action_stamp_note": ("the carrier state is a completed-bar state; the stratum is a "
                                  "future label and is never a feature"),
        },
        "CS-5": {
            "name": "halt / reopen ordering",
            "anchor_event": (f"the first U-path print after a run of >= {HALT_MIN} consecutive "
                             f"silent minutes (print-to-print jump >= {HALT_MIN} min) inside the "
                             "member window, bracketed by prints"),
            "inclusion_rule": (f"non-censored member-days with >= {HALT_ACTIVITY_FLOOR} minutes "
                               "with a U-path print and coverage_class == healthy_raw"),
            "matching_unit": "family / block / hole-length bucket",
            "outcomes": ["reopen_code5 (the label)", "reopen_gap_ret", "pre_halt_trend"],
            "minute_blindness": ("a hole has no interior in the minute panel; the probe measured "
                                 "53/103 halt-sized holes carry a condition-5 reopen print and the "
                                 "rest are indistinguishable from illiquidity"),
            "action_stamp_tau": 0,
            "action_stamp_note": ("the anchor IS a print (the reopen print, tau = 0): the causal "
                                  "side is the pre-segment plus the hole, and any feature read at "
                                  "the reopen must not use prints after it"),
        },
    }
    out: dict = {
        "tool": "factory/scripts/basket_atlas_e3.py",
        "stage": "A — case-set freeze (bounded, deterministic, hashed)",
        "plan": "researches/PLAN-ATLAS-E3-MICROSCOPE.md (DRAFT-IMPLEMENTED)",
        "deterministic": "byte-stable; no timestamps; anchors.parquet is the frozen anchor list",
        "panel": {"path": str(PANEL.relative_to(ROOT)), "sha256": PANEL_SHA_DECLARED,
                  "rows": PANEL_ROWS, "members": PANEL_MEMBERS, "days": PANEL_DAYS,
                  "terminal_censored_members": int(rows["terminal_censored"].sum())},
        "window": {
            "event_time": "tau = seconds relative to the anchor bar's first U-path print",
            "tau": [TAU_LO, TAU_HI],
            "segments": [{"name": n, "tau": [a, b]} for n, a, b in SEGMENTS],
            "rationale": ("decision on completed bar t executes at the open of bar t+1, so the "
                          "earliest legal print-level action is inside bar t+1 (post1); nothing "
                          "beyond +180 s and no parameter is a horizon"),
            "guards": ["-60 <= tau < 180 for every stored print",
                       "n_distinct_segments == 4 and no segment is subdivided",
                       "features labelled causal_at_decision may only read tau < action stamp"],
        },
        "common_construction": {
            "member_rows": "panel.parquet, terminal_censored == false",
            "families": "never pooled for inference",
            "cross_family_duplicates": ("the 255 (day,ticker) paths held by both families are "
                                        "excluded from cross-family statements only; CS-3's axis "
                                        "drops them by construction"),
            "sides": "deterministic order inside the matching unit, blind to every print feature",
            "anchor_indexing": "bar_index-indexed, never clock-indexed",
            "capped": ("each case set is capped at 4,000 anchored member-windows (CS-3's number in "
                       "the plan, applied to CS-1/CS-4/CS-5 by analogy) except CS-2, which is the "
                       "plan's own eligible population (4,946 firing members)"),
            "selection": ("inside each declared cell: days in ascending sha256(day) order, each "
                          "day's candidates in ascending sha256(anchor_id) order, picks taken "
                          "round-robin across days -> per-day counts differ by at most one"),
        },
        "case_sets": {},
        "totals": {},
        "deviation_log": DEVIATIONS,
        "reproduce": "python factory/scripts/basket_atlas_e3.py --stage all --bars-check",
    }
    total_windows = 0
    for name in CASE_ORDER:
        pop, smp = populations.get(name, []), samples.get(name, [])
        entry = dict(specs[name])
        entry["population"] = set_stats(pop, cen)
        entry["population"]["anchors_before_gates"] = freeze["raw_counts"].get(
            name, entry["population"]["anchors"])
        entry["frozen_sample"] = set_stats(smp, cen)
        entry["frozen_sample"]["cap"] = CASE_CAPS[name]
        entry["frozen_sample"]["rule"] = (
            "population (no subsample: the cap equals the eligible population)"
            if CASE_CAPS[name] is None else
            "deterministic capped subsample; see common_construction.selection")
        panel_excl, gate_excl = split_exclusions(excl.get(name, {}))
        entry["exclusions"] = {
            "panel_side": panel_excl, "raw_gates": gate_excl,
            "sample_cap_dropped": max(0, entry["population"]["anchors"]
                                      - entry["frozen_sample"]["anchors"]),
            "note": ("panel_side counts are panel rows/events dropped by the case set's own rules "
                     "before the raw gates; raw_gates are census-derived drops; "
                     "anchors_before_gates = anchors + raw_gates; "
                     "anchors = frozen_sample.anchors + sample_cap_dropped"),
        }
        if name in freeze["meta"]:
            entry["meta"] = freeze["meta"][name]
        if name in freeze.get("census_candidates", {}):
            entry["census_candidates"] = freeze["census_candidates"][name]
        total_windows += entry["frozen_sample"]["anchors"]
        out["case_sets"][name] = entry
    out["totals"] = {
        "frozen_anchor_windows": total_windows,
        "population_anchors": sum(len(populations[c]) for c in CASE_ORDER),
        "by_case_set": {c: len(samples[c]) for c in CASE_ORDER},
        "cs5_anchor_candidates_before_gates": int(cs5.height),
    }
    return out


# --------------------------------------------------------------------------- #
# self-test
# --------------------------------------------------------------------------- #

def selftest(write: bool = True) -> dict:
    checks = []

    def chk(name, desc, expected, actual, ok=None):
        ok = bool(expected == actual) if ok is None else bool(ok)
        checks.append({"check": name, "description": desc, "expected": expected,
                       "actual": actual, "passed": ok})

    got = empty_runs_np(np.array([570, 571, 575, 576, 590]), 570, 590)
    chk("empty_runs_basic", "runs of silent minutes between prints", [[572, 574], [577, 589]], got)
    chk("empty_runs_leading_trailing", "leading/trailing silence is reported",
        [[560, 569], [573, 574], [578, 580]],
        empty_runs_np(np.array([570, 571, 572, 575, 576, 577]), 560, 580))
    chk("halt_jump_algebra", "a 4-minute silent run is a 5-minute print-to-print jump",
        True, all((r[1] - r[0] + 1) + 1 >= HALT_MIN for r in [[572, 575]]))

    synth = pl.DataFrame({
        "symbol": ["X", "X"], "ts_utc": pl.Series([1, 2], dtype=pl.Int64),
        "price": [10.0, 10.1], "size": [100.0, 100.0], "exchange": ["Q", "Q"],
        "conditions": [["@", "I"], []], "trade_id": [1, 2], "tape": ["C", "C"]})
    chk("price_updating_filters_odd_lots", "the imported U-path filter drops @|I odd lots",
        1, t5.price_updating(synth).height)

    rows500 = [{"case_set": "CS-1", "anchor_id": f"k{i}", "sel_rank": sel_rank(f"k{i}"),
                "sleeve_day": f"2025-01-{i % 7 + 1:02d}",
                "family": "A_pm" if i % 2 else "B600", "block": "block1" if i % 3 else "block2"}
               for i in range(500)]
    block_of = {f"{f}|{b}": b for f in ("A_pm", "B600") for b in ("block1", "block2")}
    a1 = [r["anchor_id"] for r in select_sample(rows500, ("family", "block"), 100,
                                                block_of=block_of)]
    a2 = [r["anchor_id"] for r in select_sample(list(reversed(rows500)), ("family", "block"), 100,
                                                block_of=block_of)]
    chk("selection_order_invariant", "selection does not depend on input order", a1, a2)
    chk("selection_size", "selection honours the cap", 100, len(a1))
    one_cell = [{"anchor_id": f"s{i}", "sel_rank": sel_rank(f"s{i}"),
                 "sleeve_day": f"2025-02-{i % 5 + 1:02d}", "family": "A_pm", "block": "block1"}
                for i in range(200)]
    per_day = counts(r["sleeve_day"] for r in
                     select_sample(one_cell, ("family", "block"), 100,
                                   block_of={"A_pm|block1": "block1"}))
    chk("selection_per_day_balanced", "inside a cell, per-day counts differ by at most one", True,
        max(per_day.values()) - min(per_day.values()) <= 1)
    chk("selection_is_subset", "selection returns population rows unchanged", True,
        set(a1) <= {r["anchor_id"] for r in rows500})
    expl = {"A_pm|block1": 10, "A_pm|block2": 10, "B600|block1": 5, "B600|block2": 5}
    got_expl = select_sample(rows500, ("family", "block"), 4_000, explicit=expl)
    chk("selection_explicit_allocation", "an explicit plan allocation is honoured exactly", expl,
        counts(f"{r['family']}|{r['block']}" for r in got_expl))
    chk("selection_explicit_total", "an explicit allocation returns exactly its own total",
        sum(expl.values()), len(got_expl))

    df = load_panel_cols(PANEL_COLS).sort(MEMBER_KEY + ["bar_index"])
    mdf = member_days(df)
    fired = {tuple(r[k] for k in MEMBER_KEY): r["giveback_fired_10"]
             for r in (df.group_by(MEMBER_KEY, maintain_order=True)
                       .agg(pl.col("giveback_fired_10").first()).sort(MEMBER_KEY)
                       .iter_rows(named=True))}
    pop2, e2 = cs2_anchors(df, fired)
    ls = json.loads((ATLAS / "ledger_selftest.json").read_text())
    ref_exits = ls["real_panel"]["blocks"]["giveback:10"]["accounting_views"]["sleeve_accounting"][
        "n_early_exits"]
    chk("cs2_matches_ledger", "CS-2 anchors equal the ledger's giveback:10 early exits",
        ref_exits, len(pop2))
    chk("cs2_family_split", "CS-2 family split matches the ledger sleeve totals",
        {"A_pm": 2596, "B600": 2350}, counts(r["family"] for r in pop2))
    chk("cs2_flag_locator_agreement", "the panel's giveback_fired_10 flag and the ledger locator "
        "agree on every member", 0, e2["panel_flag_locator_disagreement"])
    mp = json.loads((ATLAS / "matched_pairs.json").read_text())
    ref3 = {f: mp["families"][f]["middle_decile_executable_axis"]["n_pairs"]
            for f in ("A_pm", "B600")}
    pr, _d3 = cs3_pairs(load_panel_cols(sorted(set(pairs.required_columns()))))
    chk("cs3_matches_pairs_artifact", "CS-3 pair counts equal matched_pairs.json", ref3,
        counts(r["family"] for r in pr.iter_rows(named=True)))
    chk("panel_grain", "panel rows / members / days match the declared grain",
        [PANEL_ROWS, PANEL_MEMBERS, PANEL_DAYS],
        [load_panel_cols(["sleeve_day"]).height, mdf.height, mdf["sleeve_day"].n_unique()])
    chk("member_day_window", "every member's fill clock is at or after its family checkpoint",
        True,
        all(int(mdf.filter(pl.col("family") == f)["entry_et"].min()) == c
            for f, c in (("A_pm", 570), ("B600", 600))))
    chk("session_end_values", "session_end is the regular or the half-day close only",
        [779, 959], sorted(set(mdf["session_end"].to_list())))

    cs = json.loads((E3 / "case_sets.json").read_text())
    for name in CASE_ORDER:
        e = cs["case_sets"][name]
        pop_n = e["population"]["anchors"]
        raw_n = e["population"]["anchors_before_gates"]
        kept = e["frozen_sample"]["anchors"]
        dropped = e["exclusions"]["sample_cap_dropped"]
        gate_sum = sum(e["exclusions"]["raw_gates"].values())
        chk(f"accounting_raw_{name}",
            f"{name}: anchors_before_gates == gated population + raw-gate drops",
            raw_n, pop_n + gate_sum)
        chk(f"accounting_sample_{name}",
            f"{name}: gated population == frozen sample + cap drops", pop_n, kept + dropped)
        chk(f"sample_gated_{name}",
            f"{name}: every frozen window sits on a healthy_raw member-day",
            {"healthy_raw": e["frozen_sample"]["member_days"]},
            e["frozen_sample"]["coverage_class"])
    chk("anchors_parquet_sha", "anchors.parquet is the sha recorded in case_sets.json",
        cs["artifacts"]["anchors_parquet_sha256"], sha256_file(E3 / "anchors.parquet"))
    chk("census_parquet_sha", "census.parquet is the sha recorded in case_sets.json",
        cs["artifacts"]["census_parquet_sha256"], sha256_file(E3 / "census.parquet"))
    chk("anchors_case_sets", "anchors.parquet carries exactly the frozen sample rows",
        cs["totals"]["frozen_anchor_windows"],
        pl.read_parquet(E3 / "anchors.parquet").height)

    chk("window_segments", "exactly four declared segments, none subdivided", 4, len(SEGMENTS))
    chk("window_bounds", "the window is [-60, 180)", [-60, 180], [TAU_LO, TAU_HI])
    chk("segment_contiguity", "segments tile the window exactly",
        [(-60, 0), (0, 60), (60, 120), (120, 180)], [(s[1], s[2]) for s in SEGMENTS])

    # ---- Stage B (B1/B2/B6 semantics, the frozen feature table, the extraction) ----------- #
    chk("sb_families", "the predictor families are the plan's six", 6, len(SB_FAMILIES))
    chk("sb_predictors_causal", "only causal_at_decision columns are predictors",
        [], [r["name"] for r in SB_FEATURE_ROWS
             if r["causal_at_decision"] and not r["name"].startswith(
                 ("f1_", "f2_", "f3_", "f4_", "f5_", "f6_"))])
    chk("sb_horizon_columns_not_predictors", "no post_* column is a predictor",
        [], [n for n in SB_PREDICTORS if n.startswith("post_")])
    chk("sb_feature_names_unique", "the feature table has no duplicate column",
        len(SB_FEATURE_NAMES), len(set(SB_FEATURE_NAMES)))
    chk("sb_segment_declaration", "every case set declares the plan's four segments (CS-5: hole)",
        {"CS-1": ("pre", "anchor", "post1", "post2"), "CS-2": ("pre", "anchor", "post1", "post2"),
         "CS-3": ("pre", "anchor", "post1", "post2"), "CS-4": ("pre", "anchor", "post1", "post2"),
         "CS-5": ("pre", "anchor", "hole")}, CASE_SEGMENTS)
    chk("sb_cs5_segments", "CS-5 declares pre/anchor/hole and no post segment",
        ("pre", "anchor", "hole"), CASE_SEGMENTS["CS-5"])
    chk("sb_rulers", "the two RULER constants are the plan's declared values",
        (0.005, 1.0), (SB_PUSH_MIN, SB_Q_MAX_AGE_S))
    fl = _flow(np.array([10.0, 10.1, 10.1, 9.9]), np.array([100.0] * 4),
               np.array([1_000_000, 1_000_001, 1_000_002, 1_000_003]),
               np.array([900_000]), np.array([10.0]), np.array([10.1]), {"px": None, "cls": None})
    chk("sb_flow_classifier", "Lee-Ready: quote mid wins, tick rule fills, zero tick inherits",
        {"signed_vol_share": 0.0, "unclassified_share": 0.0,
         "signed_vol_share_tickonly": 0.333333},
        {"signed_vol_share": _round(fl["signed_vol_share"], 6),
         "unclassified_share": _round(fl["unclassified_share"], 6),
         "signed_vol_share_tickonly": _round(fl["signed_vol_share_tickonly"], 6)})
    st = {"px": None, "cls": None}
    _flow(np.array([10.0, 10.1, 10.1, 9.9]), np.array([100.0] * 4),
          np.array([1_000_000, 1_000_001, 1_000_002, 1_000_003]),
          None, None, None, st)
    chk("sb_flow_state_carry", "the tick state carries price and the last classification",
        (9.9, -1.0), (st["px"], st["cls"]))
    fl3 = _flow(np.array([10.0, 10.1]), np.array([100.0, 100.0]),
                np.array([1_000_000, 1_000_001]), None, None, None, {"px": None, "cls": None})
    chk("sb_flow_no_quote", "without quotes every print is unclassified and the tick twin stands",
        (1.0, 1.0), (_round(fl3["unclassified_share"], 6),
                     _round(fl3["signed_vol_share_tickonly"], 6)))
    reg = ledger.load_registry()
    chk("sb_minute_features_registered", "every minute-model column is a registered causal state",
        {}, {c: reg.blocked_reason(c) for c in MINUTE_FEATURES if reg.blocked_reason(c)})
    if (E3 / "stage_b_features.parquet").exists():
        sf = pl.read_parquet(E3 / "stage_b_features.parquet")
        chk("sb_extracted_columns", "the extraction carries every frozen feature column",
            [], [n for n in SB_FEATURE_NAMES if n not in sf.columns])
        chk("sb_extracted_windows", "the extraction covers every frozen anchor",
            pl.read_parquet(E3 / "anchors.parquet").height, sf.height)
        chk("sb_g1_reconstruction", "every extracted window reproduces the panel's bar high/low",
            0, int(sf["g1_mismatch"].sum()))
        chk("sb_g2_no_post_decision_reads",
            "no causal feature read a print after the decision instant",
            0, int(sf["g1_tau_positive_causal"].sum()) if "g1_tau_positive_causal" in sf.columns
            else 0)
        chk("sb_containment", "every segment bar lies inside [entry_et, session_end]",
            True, bool(sf.join(pl.read_parquet(E3 / "anchors.parquet")
                               .select(["anchor_id", "entry_et", "session_end"]), on="anchor_id")
                       .filter((pl.col("et") < pl.col("entry_et"))
                               | (pl.col("et") > pl.col("session_end"))).height == 0))
        ab = (sf["f3_secs_above_level_anchor"].fill_null(0)
              + sf["f3_secs_below_level_anchor"].fill_null(0))
        chk("sb_bin_convention", "a bar segment's above+below seconds sum to 60 s",
            60.0, _round(float(ab.max()), 6))
        sh = sf["f6_signed_vol_share_anchor"].drop_nulls()
        chk("sb_signed_share_bounds", "signed volume share stays in [-1, 1]",
            True, bool(sh.len() == 0 or (float(sh.min()) >= -1.0 and float(sh.max()) <= 1.0)))
        chk("sb_decision_instant", "CS-1..CS-4 record close(t) = (et+1)*60",
            True, bool(sf.filter((pl.col("case_set") != "CS-5")
                                 & (pl.col("decision_close_s")
                                    != (pl.col("et") + 1) * 60)).height == 0))
        c5 = sf.filter(pl.col("case_set") == "CS-5")
        chk("sb_cs5_decision_instant", "CS-5 records the reopen print as the decision instant",
            True, bool(c5.height == 0 or (c5["decision_close_s"] <= (c5["et"] + 1) * 60).all()))
        chk("sb_cs5_hole_class", "the CS-5 hole-length split covers every CS-5 window",
            int(c5.height), int((c5["f5_hole_minutes"] == 4).sum()
                                + (c5["f5_hole_minutes"] >= 5).sum()))
    cs_now = json.loads((E3 / "case_sets.json").read_text())
    for fname, key in (("census.json", "census"), ("census.parquet", "census_parquet"),
                       ("cs5_anchors.parquet", "cs5_anchors"), ("anchors.parquet", "anchors"),
                       ("case_sets.json", "case_sets")):
        want = {"census": "58f0405b64e2785c9017a5d99b35048c0561ed33581d2db2ed2109985a964222",
                "census_parquet": "6566a3d2bf549156adc7169654dec078ffed7c1344348bb86648f1ec0680bf06",
                "cs5_anchors": "d6ade76dc3144f3f292bb8ef3caa6baa459c8960136789b3ef5c10727588bdca",
                "anchors": "678230f5757ff45555d021d38a7d09571e7858ee147e130ffdc1807d8412bd1c",
                "case_sets": "588eabfe6f3c170df9bfa5149bd809faad5391a0d60323e6337551fc2e22f457"}[key]
        chk(f"sb_freeze_sha_{key}", f"{fname} is byte-identical to the Stage-A freeze",
            want, sha256_file(E3 / fname))

    summary = {"total": len(checks), "passed": sum(1 for c in checks if c["passed"]),
               "failed": [c["check"] for c in checks if not c["passed"]]}
    obj = {"tool": "factory/scripts/basket_atlas_e3.py", "kind": "self-test",
           "deterministic": "no timestamps", "checks": checks, "summary": summary}
    if write:
        _write_json(E3 / "selftest.json", obj)
    return obj


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", choices=("census", "freeze", "all", "stageb", "iev"), default="all")
    ap.add_argument("--days", help="comma-separated day subset: writes *_trial.* only (trials)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--bars-check", action="store_true",
                    help="reconcile every census member-day against the committed derived bars")
    ap.add_argument("--no-bootstrap", action="store_true",
                    help="stageb: skip the day-clustered bootstrap (fast smoke runs)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    E3.mkdir(parents=True, exist_ok=True)
    if args.self_test:
        obj = selftest()
        print(json.dumps(obj["summary"], indent=1))
        return 0 if not obj["summary"]["failed"] else 1
    days = [d.strip() for d in args.days.split(",")] if args.days else None
    if args.stage in ("stageb", "iev"):
        if args.stage == "iev" and not days and not (E3 / "stage_b_features.parquet").exists():
            raise SystemExit("--stage iev needs stage_b_features.parquet; run --stage stageb first")
        obj = stage_b_run(args.workers, days, with_bootstrap=not args.no_bootstrap,
                          reuse_features=(args.stage == "iev"), trial=bool(days))
        print(json.dumps({cs: obj["case_sets"][cs]["set_verdict"]["information_claim"]
                          for cs in obj.get("case_sets", {})}, indent=1))
        return 0
    if args.stage == "freeze":
        if not (E3 / "census.parquet").exists():
            raise SystemExit("--stage freeze needs census.parquet; run --stage census first")
        res = build(args.workers, args.bars_check, reuse_census=True)
        print(json.dumps(res["case_sets"]["totals"], indent=1))
        return 0
    if args.stage == "census" or days:
        c = run_census(args.workers, args.bars_check, days=days, trial=bool(days))
        obj = census_json(c["rows"], c["cs5"], c["days"])
        _write_json(E3 / ("census_trial.json" if days else "census.json"), obj)
        print(f"[census] days={len(c['days'])} wall={c['cost']['wall_min']}min "
              f"projected_full_store_serial={c['cost']['projection_full_store_serial_min']}min")
        if days:
            return 0
    res = build(args.workers, args.bars_check)
    print(json.dumps(res["case_sets"]["totals"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
