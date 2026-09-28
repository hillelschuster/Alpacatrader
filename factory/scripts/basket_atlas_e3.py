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
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
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
    ap.add_argument("--stage", choices=("census", "freeze", "all"), default="all")
    ap.add_argument("--days", help="comma-separated day subset: writes *_trial.* only (trials)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--bars-check", action="store_true",
                    help="reconcile every census member-day against the committed derived bars")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    E3.mkdir(parents=True, exist_ok=True)
    if args.self_test:
        obj = selftest()
        print(json.dumps(obj["summary"], indent=1))
        return 0 if not obj["summary"]["failed"] else 1
    days = [d.strip() for d in args.days.split(",")] if args.days else None
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
