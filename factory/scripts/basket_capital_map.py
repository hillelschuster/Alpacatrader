#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "factory/artifacts/basket/phase2/capital_allocation_map"
PANEL = ROOT / "factory/artifacts/basket/phase2/F2_F12/state_panel.parquet"
PANEL_COVERAGE = ROOT / "factory/artifacts/basket/phase2/F2_F12/coverage.json"
JOINT = ROOT / "factory/artifacts/basket/phase2/F8/joint_daily.parquet"
JOINT_SURFACE = ROOT / "factory/artifacts/basket/phase2/F8/joint_surface.json"
JOINT_PROVENANCE = ROOT / "factory/artifacts/basket/phase2/F8/provenance.json"
PANEL_SOURCE = ROOT / "factory/scripts/basket_f2_f12.py"
JOINT_SOURCE = ROOT / "factory/scripts/basket_f8_joint.py"
CONTRACT = ROOT / "factory/BASKET-SIM-CONTRACT.md"
PRE_REG = ROOT / "researches/PRE-REG-BASKET-02.md"
CHECKPOINTS = (585, 600)
FAMILIES = ("A_pm", "A_pm31", "A_open", "B585", "B600")
BLOCKS = {"block1": ("2021-02", "2023-12"), "block2": ("2025-02", "2026-05")}
FEATURES = (
    "ret_from_fill", "ret_from_prevclose", "ret_from_open0930", "rank", "rank_change",
    "mfe_so_far", "mae_so_far", "dd_from_running_high", "dd_from_day_high",
    "time_since_running_high", "pos_in_range_fill", "velocity_1", "velocity_3",
    "velocity_5", "accel_proxy", "new_high_count", "new_high_freq",
    "time_below_recent_high", "bar_persistence", "recovery_5", "recovery_10",
    "rel_strength", "basket_score_disp", "basket_breadth_10", "basket_breadth_30",
)
COHORTS = ("remaining_H100", "remaining_H50_plus", "clear_failure", "ordinary")
INTERACTIONS = (("ret_from_fill", "rank"), ("dd_from_running_high", "recovery_5"),
                ("rel_strength", "basket_breadth_10"))
OUTCOME_FIELDS = frozenset({"rem_mfe", "rem_mae", "touch50", "touch100", "adverse_first", "nhba"})
EXECUTION_PNL_FIELDS = frozenset({"net", "net_return", "r_day", "pnl_c0"})


def block_for(day: str) -> str:
    if not (("2021-02-01" <= day <= "2023-12-31") or
            ("2025-02-01" <= day <= "2026-05-31")):
        raise ValueError(f"day outside permitted development blocks: {day}")
    for name, (lo, hi) in BLOCKS.items():
        if lo <= day[:7] <= hi:
            return name
    raise ValueError(f"day outside frozen blocks: {day}")


def outcome_indices(ets: list[int], checkpoint: int) -> list[int]:
    future = [i for i, et in enumerate(ets) if et > checkpoint]
    if not future:
        raise ValueError("checkpoint must have strictly later outcomes")
    return future


def expected_dimensions() -> set[tuple[str, int, str]]:
    return {(fam, cp, block) for fam in FAMILIES for cp in CHECKPOINTS
            if not (fam == "B600" and cp == 585)
            for block in BLOCKS}


def validate_alignment(panel_rows: list[dict], joint_rows: list[dict]) -> None:
    panel_days = {(r["family"], r["date"]) for r in panel_rows}
    joint_days = {(r["entry"], r["date"]) for r in joint_rows}
    if not panel_days or not panel_days <= joint_days:
        raise ValueError("F2/F12 and F8 entry/date grids do not align")


def validate_dimensions(rows: list[dict]) -> None:
    got = {(r["family"], int(r["et"]), r["block"]) for r in rows}
    expected = expected_dimensions()
    if got != expected:
        raise ValueError(f"map family/checkpoint/block dimensions differ: {got ^ expected}")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cohort(row: dict) -> str:
    if row["rem_mfe"] >= 1.0:
        return "remaining_H100"
    if row["rem_mfe"] >= .5:
        return "remaining_H50_plus"
    if row["adverse_first"]:
        return "clear_failure"
    return "ordinary"


def _stats(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0, "remaining_mfe_mean": None, "remaining_mfe_median": None,
                "remaining_mae_mean": None, "p_touch50": None, "p_touch100": None,
                "p_adverse_first": None, "p_new_high_before_adverse": None,
                "tail_to_failure_payoff_contrast": None}
    mfes = np.asarray([r["rem_mfe"] for r in rows], dtype=float)
    maes = np.asarray([r["rem_mae"] for r in rows], dtype=float)
    tail = [r["rem_mfe"] for r in rows if r["rem_mfe"] >= .5]
    fail = [abs(r["rem_mae"]) for r in rows if r["cohort"] == "clear_failure"]
    mean = float(np.mean(mfes))
    return {"n": n, "remaining_mfe_mean": mean,
            "remaining_mfe_median": float(np.median(mfes)),
            "remaining_mae_mean": float(np.mean(maes)),
            "p_touch50": float(np.mean([r["touch50"] for r in rows])),
            "p_touch100": float(np.mean([r["touch100"] for r in rows])),
            "p_adverse_first": float(np.mean([r["adverse_first"] for r in rows])),
            "p_new_high_before_adverse": float(np.mean([r["nhba"] for r in rows])),
            "tail_to_failure_payoff_contrast": float(np.mean(tail) / np.mean(fail)) if tail and fail and np.mean(fail) > 0 else None}


def _cohort_rows(rows: list[dict], cohort: str) -> list[dict]:
    if cohort == "remaining_H50_plus":
        return [r for r in rows if r["rem_mfe"] >= .5]
    return [r for r in rows if r["cohort"] == cohort]


def _feature_edges(df: pl.DataFrame, family: str, checkpoint: int, feature: str) -> list[float]:
    vals = df.filter((pl.col("family") == family) & (pl.col("et") == checkpoint))[feature].cast(pl.Float64).drop_nulls().to_numpy()
    vals = vals[np.isfinite(vals)]
    return sorted(set(float(x) for x in np.quantile(vals, [0, .2, .4, .6, .8, 1]))) if len(vals) else []


def _assign(value: float, edges: list[float]) -> int | None:
    if not np.isfinite(value) or len(edges) < 2:
        return None
    for i, (lo, hi) in enumerate(zip(edges, edges[1:])):
        if lo <= value <= hi if i == len(edges) - 2 else lo <= value < hi:
            return i
    return None


def _summaries(rows: list[dict], edges: list[float], feature: str) -> list[dict]:
    result = []
    labels = [f"q{i+1}" for i in range(max(0, len(edges)-1))]
    for idx, label in enumerate(labels):
        cell = [r for r in rows if _assign(float(r[feature]) if r[feature] is not None else np.nan, edges) == idx]
        result.append({"bin": label, "lo": edges[idx], "hi": edges[idx+1],
                       "cohorts": {cohort: _stats(_cohort_rows(cell, cohort))
                                   for cohort in COHORTS}, "all": _stats(cell)})
    return result


def build_tables(df: pl.DataFrame) -> dict:
    rows = df.to_dicts()
    for row in rows:
        row["cohort"] = _cohort(row)
    tables = {"fixed_feature_tables": [], "fixed_interaction_tables": []}
    pairs = {(fam, cp) for fam in FAMILIES for cp in CHECKPOINTS
             if not (fam == "B600" and cp == 585)}
    for family, checkpoint in sorted(pairs):
        subset = [r for r in rows if r["family"] == family and int(r["et"]) == checkpoint]
        for feature in FEATURES:
            edges = _feature_edges(df, family, checkpoint, feature)
            for block in BLOCKS:
                part = [r for r in subset if r["block"] == block]
                tables["fixed_feature_tables"].append({"family": family, "checkpoint": checkpoint,
                    "block": block, "feature": feature, "pooled_edges": edges,
                    "bins": _summaries(part, edges, feature)})
        for left, right in INTERACTIONS:
            left_edges, right_edges = (_feature_edges(df, family, checkpoint, f) for f in (left, right))
            for block in BLOCKS:
                part = [r for r in subset if r["block"] == block]
                cells = []
                for li, ri in itertools.product(range(max(0, len(left_edges)-1)), range(max(0, len(right_edges)-1))):
                    cell = [r for r in part if _assign(float(r[left]) if r[left] is not None else np.nan, left_edges) == li
                            and _assign(float(r[right]) if r[right] is not None else np.nan, right_edges) == ri]
                    cells.append({"left_bin": f"q{li+1}", "right_bin": f"q{ri+1}",
                                  "cohorts": {c: _stats(_cohort_rows(cell, c)) for c in COHORTS},
                                  "all": _stats(cell)})
                tables["fixed_interaction_tables"].append({"family": family, "checkpoint": checkpoint,
                    "block": block, "features": [left, right], "left_edges": left_edges,
                    "right_edges": right_edges, "cells": cells})
    return tables


def build_joint_context(joint: pl.DataFrame) -> list[dict]:
    context = []
    for entry in FAMILIES:
        for n in (2, 3, 4):
            part = joint.filter((pl.col("entry") == entry) & (pl.col("N") == n))
            for block, (lo, hi) in BLOCKS.items():
                dates = part.filter((pl.col("date").str.slice(0, 7) >= lo) &
                                    (pl.col("date").str.slice(0, 7) <= hi))
                if not dates.height:
                    context.append({"family": entry, "N": n, "block": block, "n_cell_days": 0,
                                    "p_ge2_reach20": None, "p_ge2_reach30": None,
                                    "note": "empty; preserved"})
                    continue
                per_day = dates.group_by("date").agg(
                    pl.col("ge2_reach_20").mean().alias("p20"),
                    pl.col("ge2_reach_30").mean().alias("p30"))
                context.append({"family": entry, "N": n, "block": block,
                    "n_cell_days": dates.height, "n_dates": per_day.height,
                    "p_ge2_reach20": float(per_day["p20"].mean()),
                    "p_ge2_reach30": float(per_day["p30"].mean()),
                    "note": "F8 descriptive already-run simulation frequencies; not conditional causal effects"})
    return context


def run() -> dict:
    coverage = json.loads(PANEL_COVERAGE.read_text())
    f8 = pl.read_parquet(JOINT)
    panel = pl.read_parquet(PANEL).filter(pl.col("et").is_in(CHECKPOINTS))
    validate_dimensions(panel.to_dicts())
    if coverage["days"] != 1066 or coverage["rows"] != 182094 or coverage["block_days"] != {"block1": 734, "block2": 332}:
        raise ValueError("F2/F12 coverage differs from frozen input contract")
    validate_alignment(panel.select("family", "date").unique().to_dicts(), f8.select("entry", "date").unique().to_dicts())
    tables = build_tables(panel)
    tables["f8_joint_survivor_context"] = build_joint_context(f8)
    tables["not_applicable"] = [{"family": "B600", "checkpoint": 585,
        "reason": "entry is at 600; no candidate is already entered at 585"}]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "relationship_tables.json").write_text(json.dumps(tables, indent=2, allow_nan=False))
    config = {"label": "RUN", "purpose": "descriptive capital-allocation information map; not gate selection or execution P&L",
              "checkpoints": list(CHECKPOINTS), "families": list(FAMILIES), "blocks": BLOCKS,
              "cohorts": {"remaining_H100": "rem_mfe >= 100% after completed checkpoint",
                          "remaining_H50_plus": "rem_mfe >= 50% after completed checkpoint, including H100",
                          "clear_failure": "adverse-first and rem_mfe < 50%", "ordinary": "remaining MFE < 50%, not adverse-first"},
              "features": list(FEATURES), "interactions": [list(pair) for pair in INTERACTIONS],
              "binning": "pooled family/checkpoint quintile edges, identical across blocks; equal edges collapsed; all fixed tables preserved",
              "timing": "state observable through completed checkpoint bar et<=t; all outcome bars strictly et>t through session end",
              "limitations": ["shared dates and overlapping names across families/checkpoints/cells are non-independent",
                              "F8 member summaries are aligned by entry/N/date but not by ticker state; joint context is unconditional over its already-run exit/friction cells",
                              "MFE touches are not executable fills; no strategy P&L, action effect, gate, threshold search, or promotion"],
              "inputs": {str(PANEL.relative_to(ROOT)): _hash(PANEL), str(PANEL_COVERAGE.relative_to(ROOT)): _hash(PANEL_COVERAGE),
                         str(JOINT.relative_to(ROOT)): _hash(JOINT), str(JOINT_SURFACE.relative_to(ROOT)): _hash(JOINT_SURFACE),
                         str(JOINT_PROVENANCE.relative_to(ROOT)): _hash(JOINT_PROVENANCE),
                         str(PANEL_SOURCE.relative_to(ROOT)): _hash(PANEL_SOURCE),
                         str(JOINT_SOURCE.relative_to(ROOT)): _hash(JOINT_SOURCE),
                         str(Path(__file__).relative_to(ROOT)): _hash(Path(__file__)),
                         str(CONTRACT.relative_to(ROOT)): _hash(CONTRACT), str(PRE_REG.relative_to(ROOT)): _hash(PRE_REG)}}
    (OUT / "run_config.json").write_text(json.dumps(config, indent=2, sort_keys=True))
    provenance = {"inputs": config["inputs"], "panel_rows": panel.height,
                  "family_checkpoint_block_dimensions": len(expected_dimensions()),
                  "feature_table_count": len(tables["fixed_feature_tables"]),
                  "interaction_table_count": len(tables["fixed_interaction_tables"]),
                  "days": coverage["days"], "block_days": coverage["block_days"]}
    (OUT / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True))
    (OUT / "README.md").write_text(
        "# Phase-2 golden-window capital-allocation information map [RUN]\n\n"
        "Descriptive relationship map only: no gate, threshold selection, action effect, strategy, "
        "P&L or live claim. `relationship_tables.json` preserves fixed feature quintiles, all "
        "four cohort summaries, every registered low-order interaction (including empty cells), "
        "and aligned F8 multiple-survivor context. See `run_config.json` for the fixed vocabulary "
        "and `provenance.json` for input hashes and completeness.\n\n"
        "## Timing and boundaries\n\n"
        "Only completed 585/600 checkpoints are mapped. F2/F12 state uses bars through the "
        "checkpoint; outcomes are strictly later bars to canonical session end. H100 and H50+ "
        "are remaining-MFE cohorts (H50+ includes H100); clear failure is adverse-first without "
        "a 50% remaining tail; ordinary is neither. Adverse-first means a 10% pullback from "
        "the checkpoint day-high precedes a new high; it is not an actual loss/cost estimate. "
        "A touched high is not an executable fill. "
        "B600 at 585 is explicitly not applicable because its entry is at 600.\n\n"
        "## Interpretation limits\n\n"
        "F8 context is grouped by entry family, N and development block across its existing "
        "exit/friction cells, not linked to an individual member's checkpoint state. Shared "
        "dates, overlapping names/families/checkpoints and repeated F8 implementation cells "
        "are non-independent. Block differences are descriptive stability flags, not validation. "
        "Later adds, holds, reductions, reserves or recycling need separately bounded F5/F6/F7 "
        "next-open execution tests; these tables alone are descriptive and imply no action.\n"
    )
    return provenance


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
