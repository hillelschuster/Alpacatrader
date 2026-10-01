#!/usr/bin/env python3
"""CV01 shared current-dollar base — causal states / execution lattice / member census.

Owner: SharedValuationBuilder.  Consumers: ContinuationAnatomy (A), OwnershipContinuity (B).
Frozen contract: factory/artifacts/basket/phase2/ATLAS/CV01/contract.json
Panel: factory/artifacts/basket/phase2/ATLAS/panel.parquet (SCHEMA.md v2; sha pinned below).

Produces exactly three files under ``data/atlas/continuation/cv01/base/`` plus a manifest:

  states.parquet      one row per member per OBSERVED completed bar, positive causal allowlist only
                      (no future price, no tail/censor label, no session constant).
  executions.parquet  one row per member per observed bar; pricing only: bar_open of this bar and
                      next_open/next_et of the SUBSEQUENT observed bar (null when there is none).
  members.parquet     one row per member: keys, entry clock/price, census, plus AUDIT-ONLY
                      (future-derived) tail/censor attribution that must never be a filter.

Deliberately NOT produced: any member x horizon Cartesian table (consumers stream per member), and
any Atlas window / whole-day volume normalisation / new round-trip fee.

Clock contract (contract.json):
  * a bar's completed state is available at ``decision_et = et + 1``;
  * a voluntary decision exists only while ``et <= session_end - 2`` (``clock_eligible``); the
    engine's forced flat on bar ``session_end - 1`` preempts every release rule;
  * ``decision_eligible`` is exactly the causal clock predicate ``clock_eligible``
    (``et <= session_end - 2``): it never depends on the future tape.  Whether an executable next
    print exists is a pricing-support fact carried by ``executions.has_next_open``; the open of that
    next OBSERVED bar is the execution (never a same-bar fill, never an invented minute across a gap,
    never a last-print endpoint), and a clock-eligible state whose tape simply ends stays eligible and
    is priced as unresolved — never dropped, never zero-filled.  A pre-flat decision whose next print
    is the ``session_end`` bar is a genuine sale at the SESSION_END open (basket_atlas_panel.py case
    D) and is flagged ``next_et_ge_session_end``; only a re-entry buy at/after ``session_end`` is
    forbidden.
  * missing minutes are never imputed: gaps are absent rows, and the next-open linkage is audited to
    machine precision against the member's own lattice.

Usage:
    .venv/bin/python factory/scripts/basket_current_dollar_base.py             # build (resumable)
    .venv/bin/python factory/scripts/basket_current_dollar_base.py --rebuild   # ignore checkpoints
    .venv/bin/python factory/scripts/basket_current_dollar_base.py --self-test # smoke, needs base
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
ATLAS = ROOT / "factory/artifacts/basket/phase2/ATLAS"
PANEL = ATLAS / "panel.parquet"
CONTRACT = ATLAS / "CV01/contract.json"
COVERAGE = ATLAS / "coverage.json"
DEFAULT_OUT = ROOT / "data/atlas/continuation/cv01/base"
DEFAULT_EVIDENCE = ATLAS / "CV01/base"

# FROZEN panel pin — checked, never discovered.
PANEL_SHA_EXPECTED = "2a021eda878cdbe53e41d4cc88e2d8ba3b14d9a3a819723d9f679c8aca7c3488"

VERSION = "cv01-base-v0"
MEMBER_KEYS = ["sleeve_day", "family", "entry_rank", "ticker"]
REL_TOL = 1e-9

KEY_COLUMNS = ["sleeve_day", "month", "block", "family", "ticker", "entry_rank",
               "entry_et", "entry_px", "session_end"]
STATE_SOURCE = [
    "bar_close", "running_high", "dist_from_running_high",
    "ret_from_fill", "mfe_so_far", "mfe_surrendered",
    "bars_since_entry", "bars_since_new_high", "reclaim_count", "failed_reclaim_count",
    "ret_1", "ret_3", "ret_5", "up_close_streak",
    "volume_vs_own_median", "volume_accel", "ret_percentile_candidates",
    "peer_ret_median", "peer_new_high_5",
]
EXEC_SOURCE = ["bar_open", "next_open", "next_et"]
MEMBER_AUDIT_SOURCE = [
    "terminal_censored", "path_complete_to_session_end", "future_member_last_et",
    "future_forced_flat_px", "session_peak_et", "session_peak_ret_from_entry",
    "session_peak_bars_from_entry", "session_close_ret_from_entry",
    "tail_class_50", "tail_class_100", "tail_class_300", "giveback_fired_10",
]
LOAD_COLUMNS = list(dict.fromkeys(
    KEY_COLUMNS + ["et", "bar_index"] + STATE_SOURCE + EXEC_SOURCE + MEMBER_AUDIT_SOURCE))

# Declared output schema.  Dtypes are carried through from the panel and asserted, never coerced,
# so a null stays a null (unknown) instead of becoming a number.
STATES_SCHEMA = {
    "member_id": pl.Utf8, "sleeve_day": pl.Utf8, "family": pl.Utf8, "entry_rank": pl.Int32,
    "ticker": pl.Utf8, "block": pl.Utf8, "month": pl.Utf8,
    "et": pl.Int32, "bar_index": pl.Int32, "decision_et": pl.Int32,
    "entry_et": pl.Int32, "session_end": pl.Int32,
    "clock_eligible": pl.Boolean, "decision_eligible": pl.Boolean,
    "bar_close": pl.Float64, "running_high": pl.Float64, "dist_from_running_high": pl.Float64,
    "ret_from_fill": pl.Float64, "mfe_so_far": pl.Float64, "mfe_surrendered": pl.Float64,
    "bars_since_entry": pl.Int32, "bars_since_new_high": pl.Int32,
    "reclaim_count": pl.Int32, "failed_reclaim_count": pl.Int32,
    "ret_1": pl.Float64, "ret_3": pl.Float64, "ret_5": pl.Float64,
    "up_close_streak": pl.Int32, "volume_vs_own_median": pl.Float64,
    "volume_accel": pl.Float64, "ret_percentile_candidates": pl.Float64,
    "peer_ret_median": pl.Float64, "peer_new_high_5": pl.Int32,
}
EXEC_SCHEMA = {
    "member_id": pl.Utf8, "sleeve_day": pl.Utf8, "family": pl.Utf8, "entry_rank": pl.Int32,
    "ticker": pl.Utf8, "et": pl.Int32, "bar_index": pl.Int32,
    "bar_open": pl.Float64, "next_open": pl.Float64, "next_et": pl.Int32,
    "has_next_open": pl.Boolean, "next_et_ge_session_end": pl.Boolean,
    "entry_et": pl.Int32, "session_end": pl.Int32,
}
MEMBERS_SCHEMA = {
    "member_id": pl.Utf8, "sleeve_day": pl.Utf8, "family": pl.Utf8, "entry_rank": pl.Int32,
    "ticker": pl.Utf8, "block": pl.Utf8, "month": pl.Utf8,
    "entry_et": pl.Int32, "session_end": pl.Int32, "past_entry_px": pl.Float64,
    "n_bars": pl.Int32, "observed_last_bar_index": pl.Int32, "observed_last_et": pl.Int32,
    "source_hash": pl.Utf8,
    "terminal_censored": pl.Boolean, "path_complete_to_session_end": pl.Boolean,
    "future_member_last_et": pl.Int32, "future_forced_flat_px": pl.Float64,
    "session_peak_et": pl.Int32, "session_peak_ret_from_entry": pl.Float64,
    "session_peak_bars_from_entry": pl.Int32, "session_close_ret_from_entry": pl.Float64,
    "tail_class_50": pl.Boolean, "tail_class_100": pl.Boolean, "tail_class_300": pl.Boolean,
    "giveback_fired_10": pl.Boolean,
}
AUDIT_ONLY_MEMBERS = ["terminal_censored", "path_complete_to_session_end", "future_member_last_et",
                      "future_forced_flat_px", "session_peak_et", "session_peak_ret_from_entry",
                      "session_peak_bars_from_entry", "session_close_ret_from_entry",
                      "tail_class_50", "tail_class_100", "tail_class_300", "giveback_fired_10"]

# Future-derived panel columns used by the perturbation smoke.  None of them may reach states or
# executions; next_open/next_et are deliberately NOT here (they define executability, a
# contract-level decision flag, not a state).
FUTURE_FLOAT = ["future_forced_flat_px", "session_peak_ret_from_entry", "session_close_ret_from_entry",
                "remaining_run", "cost_of_waiting", "dd_before_next_high", "level_ret",
                "v_forced_flat", "v_hold_flat", "v_sell",
                "v_giveback_5", "v_giveback_10", "v_giveback_15", "v_giveback_20"]
FUTURE_INT = ["future_member_last_et", "session_peak_et", "session_peak_bars_from_entry",
              "bars_to_peak", "peak_et_after_t", "bars_to_next_high"]
FUTURE_BOOL = ["terminal_censored", "path_complete_to_session_end", "final_high_flag",
               "tail_class_50", "tail_class_100", "tail_class_300",
               "giveback_fired_5", "giveback_fired_10", "giveback_fired_15", "giveback_fired_20",
               "giveback_condition_after_forced_flat_5", "giveback_condition_after_forced_flat_10",
               "giveback_condition_after_forced_flat_15", "giveback_condition_after_forced_flat_20"]

FORBIDDEN_PREFIXES = ("future_", "v_", "session_peak", "session_close_ret", "tail_class_",
                      "giveback_", "remaining_", "cost_of_waiting", "final_high",
                      "path_complete", "terminal_censored", "bars_to_", "peak_et")

# --------------------------------------------------------------------------- #
# pins / contract / guard
# --------------------------------------------------------------------------- #
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_contract() -> dict:
    return json.loads(CONTRACT.read_text())


def verify_inputs() -> dict:
    contract = load_contract()
    if not PANEL.exists():
        raise SystemExit(f"panel missing: {PANEL}")
    got = sha256_file(PANEL)
    want = contract["panel_sha256"]
    if got != want or want != PANEL_SHA_EXPECTED:
        raise SystemExit(f"panel sha mismatch: got {got}, contract {want}, pinned {PANEL_SHA_EXPECTED}")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import basket_sim  # noqa: E402  — guard_day is the sealed/reserved-day gate
    days = sorted(pl.scan_parquet(PANEL).select("sleeve_day").unique().collect()["sleeve_day"].to_list())
    for d in days:
        basket_sim.guard_day(d)
    return {"panel_sha256": got, "n_days_guarded": len(days), "first_day": days[0],
            "last_day": days[-1], "sealed_prefixes": list(basket_sim.SEALED_PREFIXES),
            "reserved_months": list(basket_sim.RESERVED_MONTHS)}


# --------------------------------------------------------------------------- #
# derivation (one code path shared by build and smoke)
# --------------------------------------------------------------------------- #
def derive(frame: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Causal states + execution lattice for any frame holding whole members."""
    f = frame.sort(MEMBER_KEYS + ["bar_index"])
    f = f.with_columns(
        pl.concat_str([pl.col("sleeve_day"), pl.col("ticker"), pl.col("family"),
                       pl.col("entry_rank").cast(pl.Utf8)], separator="|").alias("member_id"),
        (pl.col("et") + 1).cast(pl.Int32).alias("decision_et"),
        (pl.col("bar_index") < pl.col("bar_index").max().over(MEMBER_KEYS)).alias("has_successor"),
        pl.col("bar_open").shift(-1).over(MEMBER_KEYS).alias("nxt_open_obs"),
        pl.col("et").shift(-1).over(MEMBER_KEYS).alias("nxt_et_obs"),
    ).with_columns(
        nx_ok=(pl.col("next_open").is_not_null() & pl.col("next_open").is_finite()
               & pl.col("next_et").is_not_null()),
    ).with_columns(
        has_next_open=(pl.col("has_successor") & pl.col("nx_ok")),
        clock_eligible=(pl.col("et") <= (pl.col("session_end") - 2)),
    ).with_columns(
        next_et_ge_session_end=(pl.col("has_next_open") & (pl.col("next_et") >= pl.col("session_end"))),
        # CAUSAL: the voluntary-decision clock only.  Whether an executable next print exists is a
        # pricing-support fact and lives in executions.has_next_open, never in the state.
        decision_eligible=pl.col("clock_eligible"),
    )
    states = f.select([pl.col(n).alias(n) for n in STATES_SCHEMA])
    execs = f.select([pl.col(n).alias(n) for n in EXEC_SCHEMA])
    if dict(states.schema) != STATES_SCHEMA:
        raise RuntimeError(f"states schema drift: {dict(states.schema)}")
    if dict(execs.schema) != EXEC_SCHEMA:
        raise RuntimeError(f"exec schema drift: {dict(execs.schema)}")
    return states, execs


def member_id_of(day: str, ticker: str, family: str, rank) -> str:
    return f"{day}|{ticker}|{family}|{int(rank)}"


def derive_members(frame: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    """Member census + AUDIT-ONLY tail fields; returns (members, consistency counts)."""
    f = frame.with_columns(
        tape_row=pl.concat_str([pl.col("et").cast(pl.Utf8), pl.col("bar_open").cast(pl.Utf8),
                                pl.col("bar_close").cast(pl.Utf8)], separator=","))
    g = f.group_by(MEMBER_KEYS, maintain_order=True).agg(
        pl.col("block").first(), pl.col("month").first(), pl.col("entry_et").first(),
        pl.col("entry_px").first().alias("past_entry_px"), pl.col("session_end").first(),
        pl.len().cast(pl.Int32).alias("n_bars"),
        pl.col("bar_index").max().alias("observed_last_bar_index"),
        pl.col("et").max().alias("observed_last_et"),
        pl.col("tape_row").str.join(";").alias("_tape"),
        *[pl.col(c).first() for c in MEMBER_AUDIT_SOURCE],
        pl.col("entry_px").n_unique().alias("_entry_px_nuniq"),
        pl.col("entry_et").n_unique().alias("_entry_et_nuniq"),
        pl.col("session_end").n_unique().alias("_session_end_nuniq"),
        pl.col("block").n_unique().alias("_block_nuniq"),
        pl.col("bar_index").min().alias("_first_bar_index"),
        pl.col("et").min().alias("_first_et"),
        pl.col("bar_index").n_unique().alias("_n_unique_bars"),
    )
    recs = []
    for r in g.to_dicts():
        mid = member_id_of(r["sleeve_day"], r["ticker"], r["family"], r["entry_rank"])
        tape = r.pop("_tape")
        r["member_id"] = mid
        r["source_hash"] = hashlib.sha256(
            f"{mid}|{int(r['entry_et'])}|{r['past_entry_px']!r}\n{tape}".encode()).hexdigest()
        recs.append(r)
    mem = pl.DataFrame(recs).select([pl.col(n).cast(t).alias(n) for n, t in MEMBERS_SCHEMA.items()])
    if dict(mem.schema) != MEMBERS_SCHEMA:
        raise RuntimeError(f"members schema drift: {dict(mem.schema)}")
    _c = lambda e: int(g.filter(e).height)  # noqa: E731
    cons = {
        "members_with_nonconstant_entry_px": _c(pl.col("_entry_px_nuniq") > 1),
        "members_with_nonconstant_entry_et": _c(pl.col("_entry_et_nuniq") > 1),
        "members_with_nonconstant_session_end": _c(pl.col("_session_end_nuniq") > 1),
        "members_with_nonconstant_block": _c(pl.col("_block_nuniq") > 1),
        "members_first_bar_index_not_zero": _c(pl.col("_first_bar_index") != 0),
        "members_first_et_not_entry_et": _c(pl.col("_first_et") != pl.col("entry_et")),
        "members_duplicate_bar_index": _c(pl.col("_n_unique_bars") != pl.col("n_bars")),
        "members_observed_last_et_ne_future_member_last_et":
            _c(pl.col("observed_last_et") != pl.col("future_member_last_et")),
    }
    return mem, cons


AUDIT_KEYS = ("rows", "rows_et_gt_session_end", "rows_in_forced_flat_region", "cells_clock_eligible",
              "cells_decision_eligible", "cells_has_next_open", "cells_next_et_ge_session_end",
              "cells_successor_without_next_open", "cells_next_open_without_successor",
              "mismatch_next_open", "mismatch_next_et", "cells_next_et_not_after_et",
              "cells_gap_skipped", "gap_minutes_skipped")


def audit_lattice(frame: pl.DataFrame) -> dict:
    """Next-open/next-ET linkage + clock audits on a raw member frame (whole members, any size)."""
    f = frame.sort(MEMBER_KEYS + ["bar_index"]).with_columns(
        nxt_open_obs=pl.col("bar_open").shift(-1).over(MEMBER_KEYS),
        nxt_et_obs=pl.col("et").shift(-1).over(MEMBER_KEYS),
        has_successor=(pl.col("bar_index") < pl.col("bar_index").max().over(MEMBER_KEYS)),
    ).with_columns(
        nx_ok=(pl.col("next_open").is_not_null() & pl.col("next_open").is_finite()
               & pl.col("next_et").is_not_null()),
    ).with_columns(
        cell_ok=(pl.col("has_successor") & pl.col("nx_ok")),
        clock_eligible=(pl.col("et") <= (pl.col("session_end") - 2)),
    )
    tol = pl.max_horizontal(pl.lit(1.0), pl.col("nxt_open_obs").abs()) * REL_TOL
    d = f.select(
        pl.len().alias("rows"),
        (pl.col("et") > pl.col("session_end")).sum().alias("rows_et_gt_session_end"),
        (pl.col("et") >= (pl.col("session_end") - 1)).sum().alias("rows_in_forced_flat_region"),
        pl.col("clock_eligible").sum().alias("cells_clock_eligible"),
        (pl.col("clock_eligible") & pl.col("cell_ok")).sum().alias("cells_decision_eligible"),
        pl.col("cell_ok").sum().alias("cells_has_next_open"),
        (pl.col("cell_ok") & (pl.col("next_et") >= pl.col("session_end"))).sum()
        .alias("cells_next_et_ge_session_end"),
        (pl.col("has_successor") & ~pl.col("nx_ok")).sum().alias("cells_successor_without_next_open"),
        (~pl.col("has_successor") & pl.col("nx_ok")).sum().alias("cells_next_open_without_successor"),
        (pl.col("cell_ok") & ((pl.col("next_open") - pl.col("nxt_open_obs")).abs() > tol)).sum()
        .alias("mismatch_next_open"),
        (pl.col("cell_ok") & (pl.col("next_et") != pl.col("nxt_et_obs"))).sum().alias("mismatch_next_et"),
        (pl.col("cell_ok") & (pl.col("next_et") <= pl.col("et"))).sum().alias("cells_next_et_not_after_et"),
        (pl.col("cell_ok") & (pl.col("next_et") != (pl.col("et") + 1))).sum().alias("cells_gap_skipped"),
        (((pl.col("next_et") - pl.col("et") - 1).clip(lower_bound=0)) * pl.col("cell_ok")).sum()
        .alias("gap_minutes_skipped"),
    ).to_dicts()[0]
    return {k: int(d[k]) for k in AUDIT_KEYS}


def perturb_future(frame: pl.DataFrame) -> pl.DataFrame:
    """Maximally perturb every future-derived panel column (never next_open/next_et)."""
    cols = set(frame.columns)
    out = frame
    for c in FUTURE_FLOAT:
        if c in cols:
            out = out.with_columns(
                (pl.col(c).cast(pl.Float64) * 1.37 + 7.0).fill_null(42.0).alias(c))
    for c in FUTURE_INT:
        if c in cols:
            out = out.with_columns(
                (pl.col(c).cast(pl.Int64).fill_null(7919) + 7919).cast(pl.Int32).alias(c))
    for c in FUTURE_BOOL:
        if c in cols:
            out = out.with_columns(pl.col(c).cast(pl.Boolean).fill_null(True).not_().alias(c))
    return out


def _concat(parts: list[Path], dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    schema = pq.ParquetFile(parts[0]).schema_arrow
    with pq.ParquetWriter(tmp, schema, compression="zstd") as w:
        for p in parts:
            pf = pq.ParquetFile(p)
            for batch in pf.iter_batches(batch_size=250_000):
                w.write_batch(batch)
    os.replace(tmp, dest)


def audit_linkage(path: Path) -> dict:
    """next_open/next_et linkage against the member's own observed lattice (final executions file)."""
    f = pl.scan_parquet(path).with_columns(
        nxt_open_obs=pl.col("bar_open").shift(-1).over(MEMBER_KEYS),
        nxt_et_obs=pl.col("et").shift(-1).over(MEMBER_KEYS),
        has_successor=(pl.col("bar_index") < pl.col("bar_index").max().over(MEMBER_KEYS)),
    ).with_columns(
        nx_ok=(pl.col("next_open").is_not_null() & pl.col("next_open").is_finite()
               & pl.col("next_et").is_not_null()),
    ).with_columns(
        cell_ok=(pl.col("has_successor") & pl.col("nx_ok")),
    )
    tol = pl.max_horizontal(pl.lit(1.0), pl.col("nxt_open_obs").abs()) * REL_TOL
    return f.select(
        (pl.col("cell_ok") & ((pl.col("next_open") - pl.col("nxt_open_obs")).abs() > tol)).sum()
        .alias("mismatch_next_open"),
        (pl.col("cell_ok") & (pl.col("next_et") != pl.col("nxt_et_obs"))).sum().alias("mismatch_next_et"),
        (pl.col("has_successor") & ~pl.col("nx_ok")).sum().alias("cells_successor_without_next_open"),
        (~pl.col("has_successor") & pl.col("nx_ok")).sum().alias("cells_next_open_without_successor"),
        (pl.col("cell_ok") & (pl.col("next_et") <= pl.col("et"))).sum().alias("cells_next_et_not_after_et"),
        (pl.col("cell_ok") & (pl.col("next_et") != (pl.col("et") + 1))).sum().alias("cells_gap_skipped"),
        (((pl.col("next_et") - pl.col("et") - 1).clip(lower_bound=0)) * pl.col("cell_ok")).sum()
        .alias("gap_minutes_skipped"),
        pl.col("cell_ok").sum().alias("cells_has_next_open"),
        (pl.col("cell_ok") & (pl.col("next_et") >= pl.col("session_end"))).sum()
        .alias("cells_next_et_ge_session_end"),
        (~pl.col("has_next_open")).sum().alias("cells_missing_next_open"),
    ).collect().to_dicts()[0]


def audit_states(path: Path) -> dict:
    st = pl.scan_parquet(path)
    d = st.select(
        pl.len().alias("rows"),
        (pl.col("et") > pl.col("session_end")).sum().alias("rows_et_gt_session_end"),
        (pl.col("et") >= (pl.col("session_end") - 1)).sum().alias("rows_in_forced_flat_region"),
        pl.col("clock_eligible").sum().alias("cells_clock_eligible"),
        pl.col("decision_eligible").sum().alias("cells_decision_eligible"),
        (pl.col("decision_eligible") != pl.col("clock_eligible")).sum()
        .alias("mismatch_decision_eligible_rule"),
        (pl.col("decision_et") != (pl.col("et") + 1)).sum().alias("mismatch_decision_et"),
    ).collect().to_dicts()[0]
    return {k: int(v) for k, v in d.items()}


def counts_of(path: Path, cols: dict) -> dict:
    return (pl.scan_parquet(path)
            .select([expr.alias(name) for name, expr in cols.items()])
            .collect().to_dicts()[0])


def build(out: Path, evidence: Path, rebuild: bool) -> dict:
    t0 = time.time()
    t_start = now_utc()
    guard = verify_inputs()
    contract = load_contract()
    coverage = json.loads(COVERAGE.read_text())
    out.mkdir(parents=True, exist_ok=True)
    ev = evidence
    ev.mkdir(parents=True, exist_ok=True)
    parts = out / "_parts"
    for sub in ("states", "executions", "members"):
        (parts / sub).mkdir(parents=True, exist_ok=True)

    months = sorted(pl.scan_parquet(PANEL).select("month").unique().collect()["month"].to_list())
    built, skipped = [], []
    for m in months:
        sp = parts / "states" / f"month={m}.parquet"
        ep = parts / "executions" / f"month={m}.parquet"
        mp = parts / "members" / f"month={m}.parquet"
        if not rebuild and sp.exists() and ep.exists() and mp.exists():
            skipped.append(m)
            continue
        df = pl.scan_parquet(PANEL).filter(pl.col("month") == m).select(LOAD_COLUMNS).collect()
        states, execs = derive(df)
        members, _cons = derive_members(df)
        states.write_parquet(sp, compression="zstd")
        execs.write_parquet(ep, compression="zstd")
        members.write_parquet(mp, compression="zstd")
        built.append(m)

    finals = {}
    for name in ("states", "executions", "members"):
        plist = [parts / name / f"month={m}.parquet" for m in months]
        dest = out / f"{name}.parquet"
        _concat(plist, dest)
        finals[name] = dest

    st_audit = audit_states(finals["states"])
    ex_audit = audit_linkage(finals["executions"])
    st_counts = counts_of(finals["states"], {
        "rows": pl.len(),
        "n_members": pl.col("member_id").n_unique(),
        "n_days": pl.col("sleeve_day").n_unique(),
        "n_blocks": pl.col("block").n_unique(),
        "n_months": pl.col("month").n_unique(),
    })
    cell_members = pl.scan_parquet(finals["states"]).group_by("member_id").agg(
        pl.col("decision_eligible").any().alias("e")).collect()
    st_counts["members_with_any_eligible_decision"] = int(cell_members.filter(pl.col("e")).height)
    st_counts["members_without_eligible_decision"] = int(cell_members.filter(~pl.col("e")).height)
    ex_counts = counts_of(finals["executions"], {
        "rows": pl.len(), "n_members": pl.col("member_id").n_unique(),
        "cells_has_next_open": pl.col("has_next_open").sum(),
        "cells_missing_next_open": (~pl.col("has_next_open")).sum(),
        "cells_next_et_ge_session_end": pl.col("next_et_ge_session_end").sum(),
    })
    mem_counts = counts_of(finals["members"], {
        "rows": pl.len(), "n_members": pl.col("member_id").n_unique(),
        "n_days": pl.col("sleeve_day").n_unique(),
        "n_terminal_censored": pl.col("terminal_censored").sum(),
        "n_path_complete": pl.col("path_complete_to_session_end").sum(),
        "n_members_with_session_end_bar": (pl.col("observed_last_et") == pl.col("session_end")).sum(),
        "n_members_forced_flat_region_terminal": (pl.col("observed_last_et")
                                                  >= (pl.col("session_end") - 1)).sum(),
        "n_family_A_pm": (pl.col("family") == "A_pm").sum(),
        "n_family_B600": (pl.col("family") == "B600").sum(),
        "n_block1": (pl.col("block") == "block1").sum(),
        "n_block2": (pl.col("block") == "block2").sum(),
        "n_audit_future_member_last_et_nonnull": pl.col("future_member_last_et").is_not_null().sum(),
        "n_audit_future_forced_flat_px_nonnull": pl.col("future_forced_flat_px").is_not_null().sum(),
        "n_audit_session_peak_ret_nonnull": pl.col("session_peak_ret_from_entry").is_not_null().sum(),
        "n_audit_giveback_fired_10": pl.col("giveback_fired_10").sum(),
        "n_observed_last_et_ne_future_member_last_et":
            (pl.col("observed_last_et") != pl.col("future_member_last_et")).sum(),
        "n_observed_last_et_ne_session_end": (pl.col("observed_last_et") != pl.col("session_end")).sum(),
    })
    dup_members = int(pl.scan_parquet(finals["members"]).select(pl.len() - pl.col("member_id").n_unique()).collect().item())
    forbidden = {
        "states": [c for c in STATES_SCHEMA if c.startswith(FORBIDDEN_PREFIXES)],
        "executions": [c for c in EXEC_SCHEMA if c.startswith(FORBIDDEN_PREFIXES)],
        "members_audit_only": AUDIT_ONLY_MEMBERS,
    }
    outs = {}
    for name, p in finals.items():
        schema = pq.ParquetFile(p).schema_arrow
        outs[name] = {"path": str(p), "rows": int(pl.scan_parquet(p).select(pl.len()).collect().item()),
                      "sha256": sha256_file(p), "bytes": p.stat().st_size,
                      "columns": [f.name for f in schema],
                      "dtypes": [str(f.type) for f in schema]}

    def chk(name, expected, actual, note=""):
        return {"name": name, "expected": expected, "actual": actual,
                "pass": bool(expected == actual), "note": note}

    checks = [
        chk("panel_sha256", PANEL_SHA_EXPECTED, guard["panel_sha256"]),
        chk("coverage_panel_sha256", coverage.get("panel_sha256"), guard["panel_sha256"]),
        chk("members_reconcile", coverage["members"], st_counts["n_members"]),
        chk("members_rows_reconcile", coverage["members"], mem_counts["rows"]),
        chk("days_reconcile", coverage["days_in_panel"], st_counts["n_days"]),
        chk("states_rows_reconcile", coverage["rows"], st_counts["rows"]),
        chk("states_rows_equal_exec_rows", st_counts["rows"], ex_counts["rows"]),
        chk("duplicate_member_ids", 0, dup_members),
        chk("next_open_linkage_mismatch", 0, ex_audit["mismatch_next_open"],
            "next_open[t] == bar_open[t+1] of the member's own observed lattice"),
        chk("next_et_linkage_mismatch", 0, ex_audit["mismatch_next_et"],
            "next_et[t] == et[t+1] (integer ET)"),
        chk("successor_without_next_open", 0, ex_audit["cells_successor_without_next_open"]),
        chk("next_open_without_successor", 0, ex_audit["cells_next_open_without_successor"]),
        chk("next_et_not_after_et", 0, ex_audit["cells_next_et_not_after_et"]),
        chk("decision_eligible_rule_mismatch", 0, st_audit["mismatch_decision_eligible_rule"],
            "decision_eligible == clock_eligible (et <= session_end-2): a causal clock predicate "
            "only; executable-next-print support lives in executions.has_next_open"),
        chk("decision_et_rule_mismatch", 0, st_audit["mismatch_decision_et"]),
        chk("no_forbidden_columns_in_states", [], forbidden["states"]),
        chk("no_forbidden_columns_in_executions", [], forbidden["executions"]),
    ]
    overall = all(c["pass"] for c in checks)
    manifest = {
        "version": VERSION, "generated_utc": now_utc(), "build_started_utc": t_start,
        "owner": "SharedValuationBuilder",
        "contract": {"path": str(CONTRACT.relative_to(ROOT)), "sha256": sha256_file(CONTRACT),
                     "purpose": contract["purpose"]},
        "panel": {"path": str(PANEL.relative_to(ROOT)), "sha256": guard["panel_sha256"],
                  "sha256_expected": PANEL_SHA_EXPECTED, "verified": True},
        "code": {"path": str(Path(__file__).relative_to(ROOT)), "sha256": sha256_file(Path(__file__))},
        "guard": guard,
        "counts": {"states": {k: int(v) for k, v in st_counts.items()},
                   "executions": {k: int(v) for k, v in ex_counts.items()},
                   "members": {k: int(v) for k, v in mem_counts.items()},
                   "coverage_reference": {"members": coverage["members"], "rows": coverage["rows"],
                                          "days_in_panel": coverage["days_in_panel"],
                                          "days_dev_total": coverage["days_dev_total"]}},
        "audit": {"states": {k: int(v) for k, v in st_audit.items()},
                  "linkage": {k: int(v) for k, v in ex_audit.items()}},
        "months_built_this_run": built, "months_reused_from_checkpoint": skipped,
        "outputs": outs,
        "schema": {"states": {k: str(v) for k, v in STATES_SCHEMA.items()},
                   "executions": {k: str(v) for k, v in EXEC_SCHEMA.items()},
                   "members": {k: str(v) for k, v in MEMBERS_SCHEMA.items()}},
        "audit_only_columns": {"members": AUDIT_ONLY_MEMBERS, "states": [], "executions": [],
                               "note": "future-derived; labels for cross-checks only, never a "
                                       "selection rule, filter or state"},
        "checks": checks, "overall_pass": overall,
        "risk_set": {
            "states_cells_clock_eligible": st_audit["cells_clock_eligible"],
            "states_cells_decision_eligible": st_audit["cells_decision_eligible"],
            "states_cells_clock_eligible_without_pricing_support":
                int(pl.scan_parquet(finals["states"])
                    .select("member_id", "bar_index", "clock_eligible")
                    .join(pl.scan_parquet(finals["executions"])
                          .select("member_id", "bar_index", "has_next_open"),
                          on=["member_id", "bar_index"], how="inner")
                    .filter(pl.col("clock_eligible") & ~pl.col("has_next_open"))
                    .select(pl.len()).collect().item()),
            "states_cells_in_forced_flat_region": st_audit["rows_in_forced_flat_region"],
            "states_cells_et_gt_session_end": st_audit["rows_et_gt_session_end"],
            "members_with_any_eligible_decision": st_counts["members_with_any_eligible_decision"],
            "members_without_eligible_decision": st_counts["members_without_eligible_decision"],
            "members_terminal_censored": mem_counts["n_terminal_censored"],
            "members_with_session_end_bar": mem_counts["n_members_with_session_end_bar"],
            "exec_cells_missing_next_open": ex_audit["cells_missing_next_open"],
            "exec_cells_next_et_ge_session_end": ex_audit["cells_next_et_ge_session_end"],
            "exec_cells_gap_skipped": ex_audit["cells_gap_skipped"],
            "gap_minutes_skipped": ex_audit["gap_minutes_skipped"],
        },
        "column_notes": {
            "states.decision_et": "et+1 — the completed bar's state is available at this ET minute",
            "states.clock_eligible": "et <= session_end-2; the forced flat on bar session_end-1 preempts every release rule",
            "states.decision_eligible": "identical to clock_eligible by construction (verified 0 mismatches): a causal clock predicate only, never a function of the future tape. Whether an executable next print exists is a pricing-support fact carried by executions.has_next_open; a clock-eligible state whose tape ends keeps its eligibility and is priced as unresolved, never dropped or zero-filled",
            "states.bar_close": "close of this completed bar (causal); carried so consumers reproduce giveback_mask exactly",
            "states.running_high": "running high as of this bar (causal); giveback trigger close <= running_high*(1-g)+1e-9*running_high",
            "executions.bar_open": "execution price at this bar's open (the hold endpoint price)",
            "executions.next_open": "open of the subsequent observed bar = execution of a decision at this bar; null at the tape end",
            "executions.next_et": "integer ET of that next observed bar; null at the tape end",
            "executions.has_next_open": "next_open/next_et present; equivalently bar_index < the member's max bar_index",
            "executions.next_et_ge_session_end": "the next print is the session_end bar or later — true for every complete member's session_end-1 bar, and for a genuine pre-flat firing that gaps over session_end-1 (panel case D); combine with clock_eligible",
            "members.source_hash": "sha256 over member_id|entry_et|entry_px and the observed tape et|bar_open|bar_close per bar",
            "members.audit_only": "terminal_censored / path_complete_to_session_end / future_* / session_peak_* / session_close_ret_from_entry / tail_class_* / giveback_fired_10 are future-derived labels: never a filter, selection rule or state",
        },
        "elapsed_s": round(time.time() - t0, 2),
        "caveats": [
            "Descriptive anatomy/pricing base only: no optimal value, no alpha claim, no ML.",
            "states carries bar_close/running_high as causal coordinates so consumers can reproduce "
            "the giveback/reclaim detection exactly; every other column is the contract allowlist.",
            "next_open/next_et are pricing outcomes: they live in executions, never in states.",
        ],
    }
    mpath = out / "manifest.json"
    mpath.write_text(json.dumps(manifest, indent=1, sort_keys=False))
    ready = {
        "ready": True, "version": VERSION, "generated_utc": manifest["generated_utc"],
        "base_root": str(out), "manifest": str(mpath),
        "manifest_sha256": sha256_file(mpath),
        "files": {k: {"sha256": v["sha256"], "rows": v["rows"]} for k, v in outs.items()},
        "schema": manifest["schema"],
        "audit_only_columns": manifest["audit_only_columns"],
        "checks_passed": all(c["pass"] for c in checks),
    }
    (out / "ready.json").write_text(json.dumps(ready, indent=1, sort_keys=False))
    (ev / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=False))
    (ev / "ready.json").write_text(json.dumps(ready, indent=1, sort_keys=False))
    return {"manifest": manifest, "manifest_path": str(mpath), "out": str(out)}


# --------------------------------------------------------------------------- #
# smoke (real specimens, published artifacts)
# --------------------------------------------------------------------------- #
def frames_equal(a: pl.DataFrame, b: pl.DataFrame) -> bool:
    """Bitwise/null-safe frame equality (NaN == NaN), order-insensitive after sorting."""
    if a.shape != b.shape or a.columns != b.columns:
        return False
    if a.height == 0:
        return True
    ha, hb = a.hash_rows(), b.hash_rows()
    return bool((ha == hb).all())


def load_member(keys: dict, columns: list[str] | None = None) -> pl.DataFrame:
    f = (pl.scan_parquet(PANEL)
         .filter((pl.col("sleeve_day") == keys["sleeve_day"]) & (pl.col("family") == keys["family"])
                 & (pl.col("entry_rank") == keys["entry_rank"]) & (pl.col("ticker") == keys["ticker"])))
    return (f.select(columns) if columns else f).collect()


def artifact_member(path: Path, mid: str, columns: list[str] | None = None) -> pl.DataFrame:
    f = pl.scan_parquet(path).filter(pl.col("member_id") == mid)
    return (f.select(columns) if columns else f).collect()


def keys_of_mid(mid: str) -> dict:
    day, ticker, family, rank = mid.split("|")
    return {"sleeve_day": day, "ticker": ticker, "family": family, "entry_rank": int(rank)}


def pick_specimens(out: Path) -> dict:
    mem = pl.scan_parquet(out / "members.parquet")
    ex = pl.scan_parquet(out / "executions.parquet")
    st = pl.scan_parquet(out / "states.parquet")
    specs = {}
    half = (mem.filter(pl.col("session_end") == 779).sort("member_id").head(1).collect())
    if half.height:
        specs["early_close_half_day"] = half["member_id"][0]
    cens = mem.filter(pl.col("terminal_censored")).sort("member_id").head(1).collect()
    if cens.height:
        specs["censored"] = cens["member_id"][0]
    weld_ex = ex.filter(pl.col("next_et_ge_session_end")).select("member_id", "bar_index", "et",
                                                                 "session_end")
    st_clock = pl.scan_parquet(out / "states.parquet").select("member_id", "bar_index",
                                                             "clock_eligible", "decision_eligible")
    weld_d = (weld_ex.join(st_clock, on=["member_id", "bar_index"], how="inner")
              .filter(pl.col("clock_eligible")).sort("member_id").head(1).collect())
    if weld_d.height:
        specs["weld_sale_at_session_end"] = weld_d["member_id"][0]
    else:
        weld = weld_ex.sort("member_id").head(1).collect()
        if weld.height:
            specs["weld_sale_at_session_end"] = weld["member_id"][0]
    gapped = (ex.filter(pl.col("has_next_open") & ((pl.col("next_et") - pl.col("et")) > 1))
              .with_columns(span=(pl.col("next_et") - pl.col("et")))
              .sort(["span", "member_id"], descending=[True, False])
              .head(1).select("member_id").collect())
    if gapped.height:
        specs["gap"] = gapped["member_id"][0]
    if "gap" not in specs:
        # fall back: a member whose observed lattice is shorter than its span
        g = (st.group_by("member_id")
             .agg(n=pl.len(), span=(pl.col("et").max() - pl.col("et").min() + 1))
             .filter(pl.col("n") != pl.col("span")).sort("member_id").head(1).collect())
        if g.height:
            specs["gap"] = g["member_id"][0]
    return specs


def suffix_invariance(raw: pl.DataFrame) -> dict:
    """Delete the future tape suffix / append a synthetic future tape.

    Earlier causal rows must be unchanged: the full states tape of the truncated member equals the
    published rows, and an appended future tape leaves every original row alone.  Only the boundary
    row's pricing support (has_next_open / next_open / next_et) may differ, and only because the tape
    end moved.
    """
    full_states, full_execs = derive(raw)
    f = raw.sort(MEMBER_KEYS + ["bar_index"])
    n = f.height
    if n < 3:
        return {"applicable": False, "n_bars": n}
    cut = n // 2
    t_states, t_execs = derive(f.head(cut))
    boundary = t_execs.tail(1).to_dicts()[0]
    boundary_full = (full_execs.filter(pl.col("bar_index") == boundary["bar_index"])
                     .to_dicts()[0])
    last_row = f.tail(1)

    def _scaled(k: int) -> pl.DataFrame:
        exprs = [(pl.col("et") + k).alias("et"), (pl.col("bar_index") + k).alias("bar_index")]
        for c in ("bar_open", "bar_close", "bar_high", "bar_low", "running_high",
                  "dist_from_running_high"):
            if c in last_row.columns:
                exprs.append((pl.col(c) * (1 + 0.01 * k)).alias(c))
        return last_row.with_columns(exprs)

    extra = pl.concat([_scaled(k) for k in (1, 2, 3)])
    a_states, a_execs = derive(pl.concat([f, extra]).sort(MEMBER_KEYS + ["bar_index"]))
    return {
        "applicable": True,
        "n_bars": n, "cut": cut,
        "states_after_suffix_deletion_equal_published": frames_equal(t_states, full_states.head(cut)),
        "states_before_boundary_after_deletion_identical": frames_equal(
            t_states.head(cut - 1), full_states.head(cut - 1)),
        "executions_before_boundary_after_deletion_identical": frames_equal(
            t_execs.head(cut - 1), full_execs.head(cut - 1)),
        "boundary_row_only_pricing_support_differs": {
            "bar_index": int(boundary["bar_index"]),
            "has_next_open_truncated": bool(boundary["has_next_open"]),
            "has_next_open_full": bool(boundary_full["has_next_open"]),
            "next_open_truncated": boundary["next_open"],
            "next_open_full": boundary_full["next_open"],
            "decision_eligible_truncated": bool(t_states.tail(1)["decision_eligible"][0]),
            "decision_eligible_full": bool(
                full_states.filter(pl.col("bar_index") == boundary["bar_index"])
                ["decision_eligible"][0]),
            "pricing_support_only": (not boundary["has_next_open"]) and boundary_full["has_next_open"],
        },
        "states_unchanged_after_future_tape_append": frames_equal(a_states.head(n), full_states),
        "executions_unchanged_after_future_tape_append": frames_equal(a_execs.head(n), full_execs),
        "last_observed_bar_eligible_by_clock": bool(
            t_states.tail(1)["decision_eligible"][0]
            == (int(t_states.tail(1)["et"][0]) <= int(t_states.tail(1)["session_end"][0]) - 2)),
    }



def recompute_vs_artifact(out: Path, mid: str) -> dict:
    """Independently re-derive this member from the raw panel and compare with the published rows."""
    raw = load_member(keys_of_mid(mid), LOAD_COLUMNS)
    states, execs = derive(raw)
    a_st = artifact_member(out / "states.parquet", mid).sort("bar_index")
    a_ex = artifact_member(out / "executions.parquet", mid).sort("bar_index")
    return {
        "member_id": mid,
        "panel_rows": raw.height,
        "artifact_states_rows": a_st.height,
        "artifact_exec_rows": a_ex.height,
        "states_match_panel_recompute": frames_equal(states, a_st),
        "executions_match_panel_recompute": frames_equal(execs, a_ex),
        "nullable_state_cells": {c: int(raw.select(pl.col(c).is_null().sum()).item())
                                 for c in ("ret_1", "ret_3", "ret_5", "bars_since_new_high")},
    }


def perturbation_check(out: Path, mid: str) -> dict:
    """Future-perturbation invariance + causal sensitivity control, on the published rows."""
    raw = load_member(keys_of_mid(mid))
    base_states, base_execs = derive(raw)
    pert_states, pert_execs = derive(perturb_future(raw))
    a_st = artifact_member(out / "states.parquet", mid).sort("bar_index")
    # causal control 1: a causal state column changes -> derived states must differ (non-vacuous)
    ctrl = raw.with_columns((pl.col("bar_close") * 1.011).alias("bar_close"))
    c_states, _ = derive(ctrl)
    # causal control 2: move the session clock -> the eligibility flag must flip somewhere
    ctrl_se = raw.with_columns((pl.col("session_end") - 2).alias("session_end"))
    se_states, _ = derive(ctrl_se)
    flag_flip = bool((se_states["clock_eligible"] != base_states["clock_eligible"]).any())
    se = int(raw["session_end"][0])
    flip_applicable = bool(raw.filter(pl.col("et").is_in([se - 2, se - 3])).height > 0)
    future_cols = [c for c in raw.columns
                   if c in FUTURE_FLOAT + FUTURE_INT + FUTURE_BOOL]
    return {
        "member_id": mid,
        "future_columns_perturbed": future_cols,
        "n_future_columns_perturbed": len(future_cols),
        "states_identical_under_future_perturbation": frames_equal(base_states, pert_states),
        "executions_identical_under_future_perturbation": frames_equal(base_execs, pert_execs),
        "artifact_states_identical_under_future_perturbation": frames_equal(a_st, pert_states),
        "causal_control_bar_close_changes_states": not frames_equal(base_states, c_states),
        "causal_control_session_end_flips_clock_eligible": flag_flip,
        "causal_control_session_end_applicable": flip_applicable,
        "note": "the invariant is the future-perturbation identity; the two causal controls prove the "
                "comparison is not vacuous (the session-clock control only applies when the member "
                "trades at et in {session_end-2, session_end-3})",
    }


def self_test(out: Path, evidence: Path) -> dict:
    """Actual smoke on the published base: linkage, gap horizon, session-end weld sale, censoring,
    future-perturbation invariance.  Writes base_smoke.json/.txt with expected-vs-actual records."""
    t0 = time.time()
    gen = now_utc()
    specs = pick_specimens(out)
    manifest = json.loads((out / "manifest.json").read_text())
    checks = []

    def chk(name, expected, actual, evidence_block=None, note=""):
        checks.append({"name": name, "expected": expected, "actual": actual,
                       "pass": bool(expected == actual), "note": note,
                       "evidence": evidence_block})

    chk("base_files_present", True,
        all((out / f).exists() for f in ("states.parquet", "executions.parquet", "members.parquet",
                                         "manifest.json")))
    chk("manifest_overall_pass", True, bool(manifest["overall_pass"]))
    for role in ("early_close_half_day", "censored", "weld_sale_at_session_end", "gap"):
        chk(f"specimen_found_{role}", True, role in specs)

    st_all = pl.scan_parquet(out / "states.parquet")
    chk("forced_flat_precedence_no_eligible_decision_at_or_after_se_minus_1", 0,
        int(st_all.filter((pl.col("et") >= (pl.col("session_end") - 1)) & pl.col("decision_eligible"))
            .select(pl.len()).collect().item()),
        note="et >= session_end-1 is never a voluntary decision (no rebuy at the close)")
    chk("executions_missing_next_open_only_at_tape_end", 0,
        int(pl.scan_parquet(out / "executions.parquet")
            .filter(~pl.col("has_next_open")
                    & (pl.col("bar_index") < pl.col("bar_index").max().over(MEMBER_KEYS)))
            .select(pl.len()).collect().item()),
        note="a missing next open occurs only at the member's last observed bar")

    # genuine pre-flat welding (panel case D) vs the ordinary terminal session_end-1 bar
    weld_rows = (pl.scan_parquet(out / "executions.parquet")
                 .join(pl.scan_parquet(out / "states.parquet")
                       .select("member_id", "bar_index", "clock_eligible", "decision_eligible"),
                       on=["member_id", "bar_index"], how="inner"))
    n_case_d = int(weld_rows.filter(pl.col("next_et_ge_session_end") & pl.col("clock_eligible"))
                   .select(pl.len()).collect().item())
    n_terminal_weld = int(weld_rows.filter(pl.col("next_et_ge_session_end")
                                           & ~pl.col("clock_eligible")).select(pl.len()).collect().item())
    chk("case_D_preflat_weld_rows_are_decision_eligible", 0,
        int(weld_rows.filter(pl.col("next_et_ge_session_end") & pl.col("clock_eligible")
                            & ~pl.col("decision_eligible")).select(pl.len()).collect().item()),
        {"n_case_D_preflat_weld_rows": n_case_d,
         "n_terminal_session_end_minus_1_weld_rows": n_terminal_weld,
         "note": "case D = a pre-flat decision whose next print is the session_end bar; it is a "
                 "genuine sale at the SESSION_END open. The other welds are the forced-flat bar's "
                 "own terminal row, where no voluntary decision exists."})
    link_sample = [{"case_D_count": n_case_d, "terminal_weld_count": n_terminal_weld}]
    specimens, pert_ok, recompute_ok, suffix_ok = {}, True, True, True
    for role, mid in specs.items():
        k = keys_of_mid(mid)
        raw = load_member(k, LOAD_COLUMNS)
        st = artifact_member(out / "states.parquet", mid).sort("bar_index")
        ex = artifact_member(out / "executions.parquet", mid).sort("bar_index")
        mem = artifact_member(out / "members.parquet", mid).to_dicts()[0]
        info = {"role": role, "member_id": mid, "session_end": int(mem["session_end"]),
                "n_observed_bars": int(st.height),
                "observed_last_et": int(mem["observed_last_et"]),
                "terminal_censored": bool(mem["terminal_censored"]),
                "recompute": recompute_vs_artifact(out, mid),
                "suffix_invariance": suffix_invariance(raw),
                "perturbation": perturbation_check(out, mid)}
        s = info["suffix_invariance"]
        if s.get("applicable"):
            suffix_ok &= (s["states_after_suffix_deletion_equal_published"]
                          and s["states_before_boundary_after_deletion_identical"]
                          and s["executions_before_boundary_after_deletion_identical"]
                          and s["boundary_row_only_pricing_support_differs"]["pricing_support_only"]
                          and s["boundary_row_only_pricing_support_differs"]["decision_eligible_truncated"]
                          == s["boundary_row_only_pricing_support_differs"]["decision_eligible_full"]
                          and s["states_unchanged_after_future_tape_append"]
                          and s["executions_unchanged_after_future_tape_append"]
                          and s["last_observed_bar_eligible_by_clock"])
        recompute_ok &= (info["recompute"]["states_match_panel_recompute"]
                         and info["recompute"]["executions_match_panel_recompute"])
        p = info["perturbation"]
        pert_ok &= (p["states_identical_under_future_perturbation"]
                    and p["executions_identical_under_future_perturbation"]
                    and p["artifact_states_identical_under_future_perturbation"]
                    and p["causal_control_bar_close_changes_states"]
                    and (p["causal_control_session_end_flips_clock_eligible"]
                         or not p["causal_control_session_end_applicable"]))

        rows = st.to_dicts()
        exd = ex.to_dicts()
        # concrete next-printed linkage samples (expected vs actual)
        for i in (0, len(exd) // 2, len(exd) - 1):
            if i < 0:
                continue
            r = exd[i]
            nxt = exd[i + 1] if i + 1 < len(exd) else None
            link_sample.append({
                "member_id": mid, "bar_index": r["bar_index"], "et": r["et"],
                "decision_et": r["et"] + 1,
                "next_open_published": r["next_open"], "next_et_published": r["next_et"],
                "next_observed_bar_open": None if nxt is None else nxt["bar_open"],
                "next_observed_et": None if nxt is None else nxt["et"],
                "expected_next_open": None if nxt is None else nxt["bar_open"],
                "expected_next_et": None if nxt is None else nxt["et"],
                "next_open_matches": (nxt is None and r["next_open"] is None)
                or (nxt is not None and r["next_open"] is not None
                    and abs(r["next_open"] - nxt["bar_open"]) <= 1e-9 * max(1.0, abs(nxt["bar_open"]))),
                "next_et_matches": (nxt is None and r["next_et"] is None)
                or (nxt is not None and r["next_et"] == nxt["et"]),
                "has_next_open": bool(r["has_next_open"]),
                "next_et_ge_session_end": bool(r["next_et_ge_session_end"]),
            })
        if role == "gap":
            g = (ex.with_columns(span=(pl.col("next_et") - pl.col("et")))
                 .filter(pl.col("has_next_open") & (pl.col("span") > 1))
                 .sort(["span", "bar_index"], descending=[True, False]).head(1).to_dicts())
            if g:
                gr = g[0]
                missing = list(range(gr["et"] + 1, gr["next_et"]))
                info["gap"] = {
                    "row": {"bar_index": gr["bar_index"], "et": gr["et"],
                            "decision_et": gr["et"] + 1, "next_et": gr["next_et"],
                            "next_open": gr["next_open"]},
                    "bar_steps_to_execution": 1,
                    "elapsed_et_minutes_from_decision": gr["next_et"] - (gr["et"] + 1),
                    "missing_minutes_not_imputed": missing,
                    "states_rows_inside_gap": int(st.filter(pl.col("et").is_in(missing)).height),
                    "expected_states_rows_inside_gap": 0,
                    "note": "horizon is elapsed ET from decision_et to the next printed bar; the "
                            "gap minute is absent, never filled or interpolated",
                }
        if role == "weld_sale_at_session_end":
            w = ex.filter(pl.col("next_et_ge_session_end")).sort("bar_index").head(1).to_dicts()
            if w:
                wr = w[0]
                se_row = ex.filter(pl.col("et") == pl.col("session_end")).to_dicts()
                sale_px = wr["next_open"]
                se_open = se_row[0]["bar_open"] if se_row else None
                ffp = mem["future_forced_flat_px"]
                info["session_end_weld"] = {
                    "decision_row": {"bar_index": wr["bar_index"], "et": wr["et"],
                                     "decision_et": wr["et"] + 1, "session_end": wr["session_end"]},
                    "clock_eligible": bool(st.filter(pl.col("bar_index") == wr["bar_index"])
                                           ["clock_eligible"][0]),
                    "decision_eligible": bool(st.filter(pl.col("bar_index") == wr["bar_index"])
                                              ["decision_eligible"][0]),
                    "next_et_ge_session_end": bool(wr["next_et_ge_session_end"]),
                    "sale_price_next_open": sale_px,
                    "session_end_bar_open": se_open,
                    "future_forced_flat_px_audit": ffp,
                    "sale_price_equals_session_end_open":
                        se_open is not None and abs(sale_px - se_open) <= 1e-9 * max(1.0, abs(se_open)),
                    "sale_price_equals_forced_flat_audit": ffp is not None
                    and abs(sale_px - ffp) <= 1e-9 * max(1.0, abs(ffp)),
                    "note": "a pre-flat decision whose next printed bar is the session_end bar is a "
                            "genuine sale at the SESSION_END open (panel case D) and COUNTS: it is "
                            "eligible and executable, never excluded for welding into the close. "
                            "Only a re-entry BUY at/after session_end is forbidden; the base marks "
                            "et >= session_end-1 as clock-ineligible (0 eligible rows there)",
                }
        if role == "censored":
            se = int(mem["session_end"])
            last = rows[-1] if rows else {}
            info["censoring"] = {
                "terminal_censored": bool(mem["terminal_censored"]),
                "session_end": se,
                "observed_last_et": int(mem["observed_last_et"]),
                "states_rows": len(rows),
                "panel_rows_for_member": raw.height,
                "partial_rows_retained": len(rows) == raw.height,
                "rows_at_session_end": int(st.filter(pl.col("et") == se).height),
                "forced_flat_bar_present": bool(rows) and int(rows[-1]["et"]) >= se - 1,
                "last_row": {"bar_index": last.get("bar_index"), "et": last.get("et"),
                             "bar_close": last.get("bar_close"),
                             "decision_eligible": last.get("decision_eligible"),
                             "clock_eligible": last.get("clock_eligible")},
                "decision_eligible_matches_clock": bool(
                    last.get("decision_eligible") == (int(last.get("et")) <= se - 2)),
                "audit_session_peak_ret_from_entry_is_null": mem["session_peak_ret_from_entry"] is None,
                "audit_future_forced_flat_px_is_null": mem["future_forced_flat_px"] is None,
                "note": "every observed bar and its price are retained; the missing terminal "
                        "endpoint stays unknown (never zero, never last-print computed)",
            }
        specimens[role] = info

    chk("specimens_states_match_panel_recompute", True, bool(recompute_ok),
        {"specimens": list(specs.values())},
        "every specimen's published states/executions equal an independent re-derivation from the "
        "raw panel")
    chk("decision_eligible_equals_clock_eligible", 0,
        int(st_all.filter(pl.col("decision_eligible") != pl.col("clock_eligible"))
            .select(pl.len()).collect().item()),
        note="the state flag is the causal clock predicate only")
    chk("last_observed_bar_keeps_clock_eligibility", 0,
        int(st_all.filter(pl.col("bar_index") == pl.col("bar_index").max().over("member_id"))
            .filter(pl.col("clock_eligible") & ~pl.col("decision_eligible"))
            .select(pl.len()).collect().item()),
        {"n_clock_eligible_tape_end_states": int(
            st_all.filter(pl.col("bar_index") == pl.col("bar_index").max().over("member_id"))
            .filter(pl.col("clock_eligible")).select(pl.len()).collect().item()),
         "note": "a state whose future is unknown stays clock-eligible; its pricing is unresolved "
                 "(executions.has_next_open false), never dropped or zero-filled"},
        "future-tape availability must not make an earlier causal state ineligible")
    chk("future_tape_suffix_cannot_alter_earlier_states", True, bool(suffix_ok),
        {"specimens": list(specs.values())},
        "deleting the member's future tape suffix and appending a synthetic future tape leave every "
        "earlier state row (and its decision_eligible) unchanged; only the boundary row's pricing "
        "support may move with the tape end")
    chk("future_perturbation_cannot_alter_states", True, bool(pert_ok),
        {"specimens": list(specs.values())},
        "all future-derived panel columns maximally perturbed; states/executions unchanged, and a "
        "causal control still changes them")
    if "weld_sale_at_session_end" in specimens and "session_end_weld" in specimens[
            "weld_sale_at_session_end"]:
        w = specimens["weld_sale_at_session_end"]["session_end_weld"]
        chk("weld_sale_executes_at_session_end_open", True, w["sale_price_equals_session_end_open"], w)
        if w["future_forced_flat_px_audit"] is not None:
            chk("weld_sale_price_equals_audit_forced_flat", True,
                w["sale_price_equals_forced_flat_audit"], w)
    if "gap" in specimens and "gap" in specimens["gap"]:
        g = specimens["gap"]["gap"]
        chk("gap_has_no_imputed_minute", True, g["states_rows_inside_gap"] == 0, g)
        chk("gap_horizon_is_elapsed_et_not_bar_count", True,
            g["bar_steps_to_execution"] == 1
            and g["elapsed_et_minutes_from_decision"] == len(g["missing_minutes_not_imputed"])
            and g["elapsed_et_minutes_from_decision"] > g["bar_steps_to_execution"], g,
            "the executable endpoint is the next printed bar (one bar step) but the horizon is the "
            "elapsed ET from decision_et to it; every skipped minute in between is an absent row, "
            "never filled or interpolated")
    if "censored" in specimens and "censoring" in specimens["censored"]:
        c = specimens["censored"]["censoring"]
        chk("censored_partial_rows_retained", True, c["partial_rows_retained"], c)
        chk("censored_last_bar_eligible_by_clock", True, c["decision_eligible_matches_clock"], c,
            "the tape ending is not a reason to mark an earlier causal state ineligible")
        chk("censored_has_no_terminal_bar", False, c["forced_flat_bar_present"], c)

    overall = all(c["pass"] for c in checks)
    report = {
        "version": VERSION, "generated_utc": gen, "smoke_of": "published base artifacts",
        "base_root": str(out), "panel": {"path": str(PANEL), "sha256": sha256_file(PANEL),
                                         "expected": PANEL_SHA_EXPECTED},
        "code_sha256": sha256_file(Path(__file__)),
        "manifest_sha256": sha256_file(out / "manifest.json"),
        "artifact_sha256": {f: sha256_file(out / f) for f in
                            ("states.parquet", "executions.parquet", "members.parquet")},
        "counts": manifest["counts"],
        "build_audit": manifest["audit"],
        "checks": checks, "linkage_samples": link_sample, "specimens": specimens,
        "command": cmd_line(),
        "env": {"python": sys.version.split()[0], "polars": pl.__version__,
                "pyarrow": pa.__version__, "cwd": os.getcwd()},
        "overall_pass": overall, "elapsed_s": round(time.time() - t0, 2),
    }
    (out / "base_smoke.json").write_text(json.dumps(report, indent=1, sort_keys=False, default=str))
    (evidence / "base_smoke.json").write_text(json.dumps(report, indent=1, sort_keys=False, default=str))
    lines = [f"CV01 base smoke — overall_pass={overall}   ({gen})",
             f"base_root: {out}",
             f"panel sha256: {report['panel']['sha256']}",
             f"counts: members={manifest['counts']['states']['n_members']} "
             f"days={manifest['counts']['states']['n_days']} "
             f"states_rows={manifest['counts']['states']['rows']} "
             f"exec_rows={manifest['counts']['executions']['rows']}",
             ""]
    for c in checks:
        lines.append(f"[{'PASS' if c['pass'] else 'FAIL'}] {c['name']}: expected={c['expected']!r} "
                     f"actual={c['actual']!r}")
        if not c["pass"] and c.get("evidence"):
            lines.append(f"        evidence={json.dumps(c['evidence'], default=str)[:600]}")
    lines.append("")
    lines.append("next-printed linkage samples (published vs the member's own next observed bar):")
    for s in link_sample:
        lines.append("  " + json.dumps(s, default=str))
    for role, info in specimens.items():
        lines.append(f"\n[{role}] {info['member_id']} session_end={info['session_end']} "
                     f"bars={info['n_observed_bars']} censored={info['terminal_censored']}")
        lines.append("  recompute: " + json.dumps(info["recompute"], default=str))
        lines.append("  suffix: " + json.dumps(info["suffix_invariance"], default=str)[:900])
        lines.append("  perturbation: " + json.dumps(info["perturbation"], default=str)[:800])
        if "gap" in info:
            lines.append("  gap: " + json.dumps(info["gap"], default=str))
        if "session_end_weld" in info:
            lines.append("  weld: " + json.dumps(info["session_end_weld"], default=str))
        if "censoring" in info:
            lines.append("  censoring: " + json.dumps(info["censoring"], default=str))
    txt = "\n".join(lines) + "\n"
    (out / "base_smoke.txt").write_text(txt)
    (evidence / "base_smoke.txt").write_text(txt)
    cmd_txt = "\n".join([
        "# CV01 current-dollar base — captured command report (real run, not asserted)",
        f"generated_utc: {gen}",
        f"cwd: {os.getcwd()}",
        f"python: {sys.version.split()[0]}   polars: {pl.__version__}   pyarrow: {pa.__version__}",
        "",
        f"BUILD : {sys.executable} factory/scripts/basket_current_dollar_base.py --out {out}",
        f"SMOKE : {cmd_line()}",
        f"smoke_exit_status: {0 if overall else 1}",
        "",
        "artifacts (sha256):",
        *[f"  {n}.parquet: {v}" for n, v in report["artifact_sha256"].items()],
        f"  manifest.json: {sha256_file(out / 'manifest.json')}",
        f"  base_smoke.json: {sha256_file(out / 'base_smoke.json')}",
        "",
        "smoke checks (expected vs actual):",
        *[f"  [{'PASS' if c['pass'] else 'FAIL'}] {c['name']}: expected={c['expected']!r} "
          f"actual={c['actual']!r}" for c in checks],
    ]) + "\n"
    (out / "base_smoke_command.txt").write_text(cmd_txt)
    (evidence / "base_smoke_command.txt").write_text(cmd_txt)
    return report


def cmd_line() -> str:
    return " ".join([sys.executable] + sys.argv)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="CV01 current-dollar base producer")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--evidence", default=str(DEFAULT_EVIDENCE))
    ap.add_argument("--rebuild", action="store_true", help="ignore month checkpoints")
    ap.add_argument("--self-test", action="store_true", help="smoke the published base artifacts")
    a = ap.parse_args(argv)
    out, ev = Path(a.out), Path(a.evidence)
    if a.self_test:
        if not (out / "manifest.json").exists():
            build(out, ev, a.rebuild)
        rep = self_test(out, ev)
        print(json.dumps({"overall_pass": rep["overall_pass"],
                          "failed": [c["name"] for c in rep["checks"] if not c["pass"]],
                          "specimens": rep["specimens"] and list(rep["specimens"].keys()),
                          "smoke": str(out / "base_smoke.json")}, indent=1))
        return 0 if rep["overall_pass"] else 1
    msg = build(out, ev, a.rebuild)
    m = msg["manifest"]
    print(json.dumps({"overall_pass": m["overall_pass"],
                      "failed": [c["name"] for c in m["checks"] if not c["pass"]],
                      "counts": m["counts"], "manifest": msg["manifest_path"],
                      "elapsed_s": m["elapsed_s"]}, indent=1))
    return 0 if m["overall_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
