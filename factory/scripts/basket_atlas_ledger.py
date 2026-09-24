#!/usr/bin/env python3
"""ATLAS dollar ledger — judge a candidate stopping rule by the tail value it preserves.

Reads the frozen ATLAS panel (`factory/artifacts/basket/phase2/ATLAS/panel.parquet`, schema in
`SCHEMA.md`), replays one candidate stopping rule causally member-by-member, and prices it against
the hold-to-flat baseline in return units per committed dollar, per development block.

Ledger definition (contract given by Main, verbatim semantics):

    Δ_member = (return of the rule, entry -> the rule's exit, net of friction)
             - (return of hold-to-flat, entry -> session close, net of friction)

    both measured from the same entry price `entry_px`, in return units per committed dollar.

    failure_tax_avoided      = sum of positive Δ only
    dollars_destroyed        = -sum of negative Δ only (reported positive)
    net_dollar_ledger        = sum of all Δ  (= avoided - destroyed)
    giant_dollars_destroyed_100/_300 = the subset of `dollars_destroyed` for members whose forward
                               MFE from the exit point reached +100% / +300%
    giants_cut               = that subset, each with day, ticker, exit et, exit price, forward peak
                               after the exit, forward peak from entry, `readmissible`
                               (price traded >= +10% above the exit price after the exit)
    n_reentry_candidates     = count of members with `readmissible` true

Friction (`bps_total`, default 100.0 = the contract's minimum base): charged per side as
`side = bps_total / 2 / 10000`; a committed dollar buys `1/(1+side)` unit of exposure and an exit
returns `(1 + gross_move) * (1 - side)`. Entry friction is identical on both legs of Δ, so Δ is an
exact per-member attribution and the ledger sums to the total EV difference versus the baseline.

Causality: a rule decides on a *completed* bar `t`; execution is at that row's `next_open` (the open
of bar t+1, the panel's own execution convention). If the trigger bar is the member's last tracked
bar there is no next open — the position rides to the forced flat and Δ = 0 exactly (reported as
`hold_equivalent`). Rules may read state columns only; outcome columns are refused (see GUARD).

Rules (CLI `--rule`):
    hold_flat          never trigger; the forced flat -> Δ = 0 by construction
    exit_at_bar:N      decision at the bar with bar_index == N (0 = the fill bar), executed at its
                       next_open (N=0 therefore exits at the first open after the fill)
    exit_at_et:HHMM    decision at the first bar with et >= HHMM (minutes-of-day ET), executed at
                       its next_open
    giveback:G         decision at the first bar whose close is >= G% below the running high as of
                       that bar, executed at its next_open — mirrors the panel's declared
                       `v_giveback_G` continuation exactly (same comparison and tolerance)
    spec:<path.json>   declarative per-minute stopping rule over state columns:
                       {"name": str,
                        "conditions": [{"column": str, "op": ">|>=|<|<=|==|!=", "value": num}],
                        "logic": "all"|"any" (default all),
                        "consecutive": K >= 1 (default 1; trigger bar and the K-1 bars before it
                                               must all satisfy the condition set),
                        "min_et": int (optional, inclusive trigger-bar et floor),
                        "max_et": int (optional, inclusive trigger-bar et ceiling)}

GUARD — outcome columns are refused as rule inputs, loudly:
    a rule condition naming a column that starts with one of
    v_ final_ remaining_ cost_ bars_to_ dd_before_ tail_class_ peak_ session_
    raises `OutcomeLeakError` and the CLI exits non-zero. The ledger's own *measurement* (the
    baseline, the forward MFE from the exit, readmissibility) necessarily reads outcome columns —
    that is the point of the instrument — but no rule may condition on them.

Usage:
    .venv/bin/python factory/scripts/basket_atlas_ledger.py --rule hold_flat
    .venv/bin/python factory/scripts/basket_atlas_ledger.py --rule giveback:10 --json /tmp/led.json
    .venv/bin/python factory/scripts/basket_atlas_ledger.py --rule spec:/tmp/rule.json
    .venv/bin/python factory/scripts/basket_atlas_ledger.py --self-test
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PANEL = ROOT / "factory/artifacts/basket/phase2/ATLAS/panel.parquet"
ARTIFACT = ROOT / "factory/artifacts/basket/phase2/ATLAS/ledger_selftest.json"

# Outcome-column prefixes. A *rule* may never condition on any column with one of these prefixes.
OUTCOME_PREFIXES = ("v_", "final_", "remaining_", "cost_", "bars_to_", "dd_before_",
                    "tail_class_", "peak_", "session_")

MEMBER_KEYS = ("sleeve_day", "family", "entry_rank", "ticker")

# Trigger-comparison tolerance, matching the panel producer's TOL for the giveback continuation.
# It only ever matters for a bar sitting exactly on a threshold (float representation), where the
# economic reading is unambiguous: the bar is at the level.
TRIG_TOL = 1e-12

# Columns the ledger itself needs in every panel (identifiers + execution + baseline + measurement).
CORE_COLUMNS = (
    "sleeve_day", "ticker", "family", "entry_rank", "block", "month",
    "entry_et", "entry_px", "et", "bar_index", "session_end",
    "next_open", "next_et", "remaining_run", "session_close_ret_from_entry",
)
# Measurement/reporting conveniences; absent -> reported as null, never fabricated.
OPTIONAL_COLUMNS = (
    "bar_open", "bar_high", "bar_low", "bar_close", "prev_close", "open0930",
    "mfe_so_far", "running_high", "dist_from_running_high", "session_peak_ret_from_entry",
    "session_peak_et",
    "tail_class_50", "tail_class_100", "tail_class_300", "v_hold_flat", "level_ret",
    "v_giveback_5", "v_giveback_10", "v_giveback_15", "v_giveback_20",
    "member_last_et",
)
# State columns a rule implementation may read (schema section "State columns", plus the tape).
STATE_COLUMNS = (
    "et", "bar_index", "bars_since_entry", "entry_et", "entry_px",
    "bar_open", "bar_high", "bar_low", "bar_close", "prev_close", "open0930",
    "ret_from_fill", "ret_from_prevclose", "ret_from_open0930", "mfe_so_far", "mae_so_far",
    "running_high", "dist_from_running_high", "mfe_surrendered", "bars_below_entry_episode",
    "episode_low", "bars_since_episode_low", "reclaim_count", "failed_reclaim_count",
    "bars_since_new_high", "new_high_count_5", "new_high_count_15", "new_high_count_30",
    "ret_1", "ret_3", "ret_5", "accel_1_5", "up_close_streak", "range_expansion",
    "bar_range_pct", "volume", "dollar_volume", "volume_vs_own_median", "volume_accel",
    "gap_count_so_far", "bars_since_gap", "candidate_count_t", "ret_percentile_candidates",
    "peer_ret_median", "peer_new_high_5",
)


class PanelSchemaError(RuntimeError):
    """The panel does not carry a column the ledger/rule requires."""


class OutcomeLeakError(RuntimeError):
    """A rule tried to condition on an outcome column."""


class RuleSpecError(RuntimeError):
    """The rule specification is malformed."""


# --------------------------------------------------------------------------- #
# guard
# --------------------------------------------------------------------------- #
def guard_column(column: str, where: str) -> str:
    """Refuse any rule input column carrying an outcome prefix. Loud, never silent."""
    for pre in OUTCOME_PREFIXES:
        if column.startswith(pre):
            raise OutcomeLeakError(
                f"{where}: rule input column {column!r} starts with outcome prefix {pre!r}. "
                f"Outcome columns are measurements, not state; a rule may not condition on them. "
                f"Forbidden prefixes: {' '.join(OUTCOME_PREFIXES)}"
            )
    return column


def guard_columns(columns) -> None:
    for c in columns:
        guard_column(c, "rule spec")


# --------------------------------------------------------------------------- #
# rules
# --------------------------------------------------------------------------- #
@dataclass
class Rule:
    name: str
    kind: str = "custom"
    columns: tuple = ()            # state columns the rule reads (validated against the panel)
    params: dict = field(default_factory=dict)

    def trigger(self, cols: dict) -> "int | None":
        """Positional index of the decision bar inside this member's row order, or None."""
        raise NotImplementedError


@dataclass
class HoldFlat(Rule):
    def trigger(self, cols: dict):
        return None


@dataclass
class ExitAtBar(Rule):
    def trigger(self, cols: dict):
        idx = np.flatnonzero(cols["bar_index"] == self.params["bar"])
        return int(idx[0]) if idx.size else None


@dataclass
class ExitAtEt(Rule):
    def trigger(self, cols: dict):
        idx = np.flatnonzero(cols["et"] >= self.params["et"])
        return int(idx[0]) if idx.size else None


@dataclass
class Giveback(Rule):
    """Exit the next open after the first close ≥ G% below the running high as of that bar.

    Mirrors the panel's declared `v_giveback_G` continuation exactly, including the producer's
    comparison form (`close <= running_high * (1 - G/100) + TRIG_TOL`) so the ledger's giveback:G
    and the panel's v_giveback_G agree on boundary bars instead of disagreeing by float noise.
    """

    def trigger(self, cols: dict):
        ok = giveback_mask(cols, float(self.params["pct"]))
        idx = np.flatnonzero(ok)
        return int(idx[0]) if idx.size else None


def giveback_mask(cols: dict, pct: float) -> np.ndarray:
    """Causal mask of bars whose close is >= pct% below the running high as of that bar."""
    g = pct / 100.0
    if "bar_close" in cols and "running_high" in cols:
        c, rh = cols["bar_close"].astype(float), cols["running_high"].astype(float)
        return np.isfinite(c) & np.isfinite(rh) & (c <= rh * (1.0 - g) + TRIG_TOL)
    dd = cols["dist_from_running_high"]                     # documented fallback (no tape column)
    return np.isfinite(dd) & (dd <= -g + TRIG_TOL)


@dataclass
class SpecRule(Rule):
    def trigger(self, cols: dict):
        conds = self.params["conditions"]
        masks = []
        for c in conds:
            v = cols[c["column"]].astype(float)
            op, thr = c["op"], float(c["value"])
            with np.errstate(invalid="ignore"):
                if op == ">":
                    m = v > thr
                elif op == ">=":
                    m = v >= thr
                elif op == "<":
                    m = v < thr
                elif op == "<=":
                    m = v <= thr
                elif op == "==":
                    m = v == thr
                elif op == "!=":
                    m = v != thr
                else:                                     # pragma: no cover - parser rejects first
                    raise RuleSpecError(f"unknown op {op!r}")
            masks.append(np.isfinite(v) & m)
        logic = self.params.get("logic", "all")
        mask = masks[0]
        for m in masks[1:]:
            mask = (mask & m) if logic == "all" else (mask | m)
        k = int(self.params.get("consecutive", 1))
        if k > 1:
            if mask.size < k:
                return None
            run = np.convolve(mask.astype(np.int64), np.ones(k, dtype=np.int64), mode="valid")
            hit = np.flatnonzero(run == k)
            if hit.size == 0:
                return None
            mask = np.zeros_like(mask)
            mask[hit + (k - 1)] = True
        et = cols["et"]
        if self.params.get("min_et") is not None:
            mask &= et >= int(self.params["min_et"])
        if self.params.get("max_et") is not None:
            mask &= et <= int(self.params["max_et"])
        idx = np.flatnonzero(mask)
        return int(idx[0]) if idx.size else None


OPS = (">", ">=", "<", "<=", "==", "!=")


def parse_rule(text: str) -> Rule:
    """Parse the CLI rule string into a Rule, enforcing the outcome-column guard."""
    if text == "hold_flat":
        return HoldFlat(name="hold_flat", columns=())
    if text.startswith("exit_at_bar:"):
        n = int(text.split(":", 1)[1])
        if n < 0:
            raise RuleSpecError("exit_at_bar:N requires N >= 0")
        return ExitAtBar(name=text, columns=("bar_index",), params={"bar": n})
    if text.startswith("exit_at_et:"):
        raw = text.split(":", 1)[1]
        try:
            if raw.count(":") == 1:
                hh, mm = (int(x) for x in raw.split(":"))
            elif len(raw) == 4 and raw.isdigit():
                hh, mm = int(raw[:2]), int(raw[2:])
            else:
                raise ValueError(raw)
        except ValueError:
            raise RuleSpecError(
                f"exit_at_et:{raw} is not a valid ET time; expected HHMM (e.g. 0930, 1130) or "
                f"HH:MM (e.g. 09:30). The panel's `et` minutes-of-day is not accepted here."
            ) from None
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise RuleSpecError(f"exit_at_et:{raw} is not a valid HHMM ET time")
        return ExitAtEt(name=text, columns=("et",), params={"et": hh * 60 + mm, "hhmm": raw})
    if text.startswith("giveback:"):
        g = float(text.split(":", 1)[1])
        if not (0 < g < 100):
            raise RuleSpecError("giveback:G requires 0 < G < 100")
        return Giveback(name=text, columns=("bar_close", "running_high", "dist_from_running_high"),
                        params={"pct": g})
    if text.startswith("spec:"):
        path = Path(text.split(":", 1)[1])
        if not path.exists():
            raise RuleSpecError(f"spec file not found: {path}")
        spec = json.loads(path.read_text())
        return spec_rule(spec, origin=str(path))
    raise RuleSpecError(
        f"unknown rule {text!r}; expected hold_flat | exit_at_bar:N | exit_at_et:HHMM | "
        f"giveback:G | spec:<path.json>"
    )


def spec_rule(spec: dict, origin: str = "<inline>") -> SpecRule:
    """Build a declarative rule from a spec dict, guarding every column it names."""
    if not isinstance(spec, dict):
        raise RuleSpecError(f"{origin}: spec must be a JSON object")
    conds = spec.get("conditions")
    if not isinstance(conds, list) or not conds:
        raise RuleSpecError(f"{origin}: spec needs a non-empty 'conditions' list")
    columns = []
    for i, c in enumerate(conds):
        if not isinstance(c, dict) or "column" not in c or "op" not in c or "value" not in c:
            raise RuleSpecError(f"{origin}: condition {i} needs 'column', 'op' and 'value'")
        col = str(c["column"])
        guard_column(col, f"{origin}: condition {i}")          # <- outcome guard, before anything else
        if col not in STATE_COLUMNS:
            raise RuleSpecError(
                f"{origin}: condition {i} column {col!r} is not a declared state column. "
                f"Declared state columns: {', '.join(STATE_COLUMNS)}"
            )
        if c["op"] not in OPS:
            raise RuleSpecError(f"{origin}: condition {i} op {c['op']!r} not in {OPS}")
        if not isinstance(c["value"], (int, float)) or isinstance(c["value"], bool):
            raise RuleSpecError(f"{origin}: condition {i} value must be a number")
        columns.append(col)
    logic = spec.get("logic", "all")
    if logic not in ("all", "any"):
        raise RuleSpecError(f"{origin}: logic must be 'all' or 'any'")
    k = int(spec.get("consecutive", 1))
    if k < 1:
        raise RuleSpecError(f"{origin}: consecutive must be >= 1")
    return SpecRule(name=str(spec.get("name") or f"spec:{Path(origin).name}"),
                    columns=tuple(dict.fromkeys(columns)),
                    params={"conditions": conds, "logic": logic, "consecutive": k,
                            "min_et": spec.get("min_et"), "max_et": spec.get("max_et"),
                            "notes": spec.get("notes"), "origin": origin})


# --------------------------------------------------------------------------- #
# panel
# --------------------------------------------------------------------------- #
def fric_k(bps_total: float) -> float:
    """(1 - side) / (1 + side): the fraction of the gross move that survives both sides."""
    side = bps_total / 2.0 / 10000.0
    return (1.0 - side) / (1.0 + side)


def load_panel(path: Path, rule: Rule) -> pl.DataFrame:
    cols = set(pl.read_parquet_schema(path))
    missing = [c for c in CORE_COLUMNS if c not in cols]
    if missing:
        raise PanelSchemaError(
            f"{path}: panel is missing ledger columns {missing}. Available: {sorted(cols)}"
        )
    missing_state = [c for c in rule.columns if c not in cols]
    if missing_state:
        raise PanelSchemaError(
            f"{path}: rule {rule.name!r} needs state columns {missing_state}, absent from the panel. "
            f"Available: {sorted(cols)}"
        )
    keep = [c for c in dict.fromkeys(list(CORE_COLUMNS) + list(OPTIONAL_COLUMNS)
                                      + list(rule.columns)) if c in cols]
    df = pl.read_parquet(path, columns=keep)
    return df.sort(["block", "sleeve_day", "family", "entry_rank", "ticker", "bar_index"])


def members_of(df: pl.DataFrame):
    """Deterministic per-member iteration over a sorted panel frame."""
    for key, sub in df.partition_by(list(MEMBER_KEYS), maintain_order=True, as_dict=True).items():
        yield key, sub


# --------------------------------------------------------------------------- #
# replay
# --------------------------------------------------------------------------- #
def replay_member(sub: pl.DataFrame, rule: Rule, k: float) -> dict:
    """One member's causal replay -> its row of the dollar ledger."""
    cols = {c: sub[c].to_numpy() for c in sub.columns}
    entry_px = float(sub["entry_px"][0])
    close_ret = sub["session_close_ret_from_entry"][0]
    day = str(sub["sleeve_day"][0])
    ticker = str(sub["ticker"][0])
    family = str(sub["family"][0])
    rank = int(sub["entry_rank"][0])
    block = str(sub["block"][0])
    session_end = int(sub["session_end"][0])
    tape_end = int(sub["et"][-1])          # the member's own last bar (may precede session_end)

    trig = rule.trigger(cols)
    row = {
        "sleeve_day": day, "ticker": ticker, "family": family, "entry_rank": rank,
        "block": block, "entry_px": entry_px, "entry_et": int(sub["entry_et"][0]),
        "n_bars": sub.height, "session_end": session_end, "tape_end_et": tape_end,
    }

    if close_ret is None or not math.isfinite(close_ret):
        row.update({"status": "excluded_no_baseline", "delta": None})
        return row
    net_hold = (1.0 + float(close_ret)) * k - 1.0        # hold-to-flat, net of entry+exit friction

    if trig is None:
        exit_px = None
        if "bar_close" in cols:
            exit_px = float(cols["bar_close"][-1])
        else:
            exit_px = entry_px * (1.0 + float(close_ret))      # exact, from the ticket constant
        row.update({"status": "hold_to_flat", "exit_kind": "forced_flat",
                    "exit_et": tape_end, "exit_px": exit_px,
                    "gross_rule": float(close_ret), "net_rule": net_hold,
                    "delta": 0.0, "readmissible": False, "early_exit": False,
                    "forward_mfe_from_exit": None, "destroyed": 0.0, "avoided": 0.0})
        return row

    next_open = cols["next_open"][trig]
    trig_bar = int(cols["bar_index"][trig])
    if next_open is None or not math.isfinite(float(next_open)):
        # Trigger on the session's last completed bar: no next open exists, so the decision cannot
        # execute. The position rides to the forced flat -> economically identical to the baseline.
        row.update({"status": "hold_equivalent", "exit_kind": "no_next_open_forces_flat",
                    "exit_et": tape_end, "exit_px": None, "trigger_bar_index": trig_bar,
                    "gross_rule": float(close_ret), "net_rule": net_hold,
                    "delta": 0.0, "readmissible": False, "early_exit": False,
                    "forward_mfe_from_exit": None, "destroyed": 0.0, "avoided": 0.0})
        return row

    exit_px = float(next_open)
    exit_et = int(cols["next_et"][trig])
    gross_rule = exit_px / entry_px - 1.0
    net_rule = (1.0 + gross_rule) * k - 1.0
    delta = net_rule - net_hold
    mfe_exit = cols["remaining_run"][trig]
    mfe_exit = float(mfe_exit) if mfe_exit is not None and math.isfinite(float(mfe_exit)) else None
    peak_from_entry = None
    if "session_peak_ret_from_entry" in cols:
        p = cols["session_peak_ret_from_entry"][trig]
        peak_from_entry = float(p) if p is not None and math.isfinite(float(p)) else None
    row.update({
        "status": "early_exit", "exit_kind": rule.kind, "exit_et": exit_et, "exit_px": exit_px,
        "trigger_bar_index": trig_bar, "trigger_et": int(cols["et"][trig]),
        "gross_rule": gross_rule, "net_rule": net_rule, "gross_hold": float(close_ret),
        "net_hold": net_hold, "delta": delta,
        "avoided": max(delta, 0.0), "destroyed": max(-delta, 0.0),
        "forward_mfe_from_exit": mfe_exit,
        "forward_peak_after_exit_px": exit_px * (1.0 + mfe_exit) if mfe_exit is not None else None,
        "forward_peak_from_entry_pct": peak_from_entry,
        "forward_peak_from_entry_px": entry_px * (1.0 + peak_from_entry)
        if peak_from_entry is not None else None,
        "early_exit": True,
        "readmissible": bool(mfe_exit is not None and mfe_exit >= 0.10),
    })
    # definition-drift detector: the panel's own tail_class_* flags must agree with remaining_run
    for flag, thr in (("tail_class_100", 1.0), ("tail_class_300", 3.0)):
        if flag in cols and mfe_exit is not None:
            row[f"{flag}_checked"] = True
            tc = cols[flag][trig]
            if tc is not None and bool(tc) != bool(mfe_exit >= thr):
                row[f"{flag}_mismatch"] = True
    # ... and the panel's `level_ret` (next_open/entry_px - 1) must equal the ledger's exit gross
    if "level_ret" in cols:
        lr = cols["level_ret"][trig]
        if lr is not None and math.isfinite(float(lr)):
            row["level_ret_checked"] = True
            if abs(float(lr) - gross_rule) > 1e-12:
                row["level_ret_mismatch"] = True
    return row


def aggregate_block(rows: list) -> dict:
    """Dollar ledger for one development block."""
    judged = [r for r in rows if r.get("delta") is not None]
    avoided = sum(r["avoided"] for r in judged)
    destroyed = sum(r["destroyed"] for r in judged)
    net = sum(r["delta"] for r in judged)
    giants100 = [r for r in judged if r["destroyed"] > 0 and r["forward_mfe_from_exit"] is not None
                 and r["forward_mfe_from_exit"] >= 1.0]
    giants300 = [r for r in giants100 if r["forward_mfe_from_exit"] >= 3.0]
    giants_any = [r for r in judged if r["early_exit"] and r["forward_mfe_from_exit"] is not None
                  and r["forward_mfe_from_exit"] >= 1.0]
    reentry = [r for r in judged if r.get("readmissible")]

    def giant_row(r: dict) -> dict:
        return {
            "sleeve_day": r["sleeve_day"], "ticker": r["ticker"], "family": r["family"],
            "entry_rank": r["entry_rank"], "exit_et": r["exit_et"], "exit_px": r["exit_px"],
            "trigger_bar_index": r.get("trigger_bar_index"),
            "forward_mfe_from_exit": r["forward_mfe_from_exit"],
            "forward_peak_after_exit_px": r["forward_peak_after_exit_px"],
            "forward_peak_from_entry_px": r["forward_peak_from_entry_px"],
            "forward_peak_from_entry_pct": r["forward_peak_from_entry_pct"],
            "readmissible": bool(r["readmissible"]),
            "delta": r["delta"], "destroyed_dollars": r["destroyed"],
            "forgone_run_dollars": r["forward_mfe_from_exit"],   # gross run skipped, per committed $
        }

    blocks = {
        "n_members": len(judged),
        "n_excluded_no_baseline": len(rows) - len(judged),
        "n_early_exits": sum(1 for r in judged if r["early_exit"]),
        "n_hold_like": sum(1 for r in judged if not r["early_exit"]),
        "n_triggered_unexecutable": sum(1 for r in judged
                                        if r["status"] == "hold_equivalent"),
        "failure_tax_avoided": avoided,
        "dollars_destroyed": destroyed,
        "net_dollar_ledger": net,
        "giant_dollars_destroyed_100": sum(r["destroyed"] for r in giants100),
        "giant_dollars_destroyed_300": sum(r["destroyed"] for r in giants300),
        "n_giants_cut": len(giants100),
        "n_giants_cut_300": len(giants300),
        "n_giants_cut_any_delta_sign": len(giants_any),
        "forgone_run_dollars_100": sum(r["forward_mfe_from_exit"] for r in giants100),
        "forgone_run_dollars_300": sum(r["forward_mfe_from_exit"] for r in giants300),
        "n_reentry_candidates": len(reentry),
        "n_reentry_candidates_giants": sum(1 for r in giants100 if r["readmissible"]),
        "tail_class_100_check_n": sum(1 for r in judged if r.get("tail_class_100_checked")),
        "tail_class_100_mismatch_n": sum(1 for r in judged
                                         if r.get("tail_class_100_mismatch")),
        "tail_class_300_mismatch_n": sum(1 for r in judged
                                         if r.get("tail_class_300_mismatch")),
        "level_ret_check_n": sum(1 for r in judged if r.get("level_ret_checked")),
        "level_ret_mismatch_n": sum(1 for r in judged if r.get("level_ret_mismatch")),
        "mean_delta": net / len(judged) if judged else None,
        "worst_delta": min((r["delta"] for r in judged), default=None),
        "best_delta": max((r["delta"] for r in judged), default=None),
        "giants_cut": [giant_row(r) for r in giants100],
        "reentry_candidates": [
            {"sleeve_day": r["sleeve_day"], "ticker": r["ticker"], "exit_et": r["exit_et"],
             "exit_px": r["exit_px"], "forward_mfe_from_exit": r["forward_mfe_from_exit"],
             "delta": r["delta"]} for r in reentry],
        "by_family": {},
    }
    for fam in sorted({r["family"] for r in judged}):
        sub = [r for r in judged if r["family"] == fam]
        blocks["by_family"][fam] = {
            "n_members": len(sub),
            "failure_tax_avoided": sum(r["avoided"] for r in sub),
            "dollars_destroyed": sum(r["destroyed"] for r in sub),
            "net_dollar_ledger": sum(r["delta"] for r in sub),
            "n_early_exits": sum(1 for r in sub if r["early_exit"]),
        }
    # internal identity: avoided - destroyed == net, and the giant subsets are subsets of destroyed
    tol = 1e-9 * max(1.0, abs(net), avoided, destroyed)
    checks = {
        "net_identity": abs(net - (avoided - destroyed)) <= tol,
        "giants_100_subset_of_destroyed": blocks["giant_dollars_destroyed_100"] <= destroyed + 1e-12,
        "giants_300_subset_of_100": blocks["giant_dollars_destroyed_300"]
        <= blocks["giant_dollars_destroyed_100"] + 1e-12,
    }
    blocks["checks"] = checks
    return blocks


def continuation_crosscheck(df: pl.DataFrame, rule: Rule) -> "dict | None":
    """Compare the ledger's own replay against the panel's declared v_ continuation columns.

    The panel's v_* columns measure from the next_open of the *row* they sit on and scan forward
    from t+1; the ledger's rule replay scans from the member's fill bar (bar_index 0). This check
    therefore re-derives the panel's convention on the ledger's own paths: it is a definition-drift
    detector for hold_flat and giveback:G, not a substitute for the ledger.
    """
    name = rule.name
    if name == "hold_flat":
        vcol = "v_hold_flat"
    elif name.startswith("giveback:"):
        g = rule.params["pct"]
        vcol = f"v_giveback_{int(g)}" if float(g).is_integer() else None
    else:
        return None
    cols = set(df.columns)
    has_state = ("bar_close" in cols and "running_high" in cols) or "dist_from_running_high" in cols
    if vcol is None or vcol not in cols or not has_state:
        return None
    diffs, n = [], 0
    for key, sub in members_of(df):
        i0 = np.flatnonzero(sub["bar_index"].to_numpy() == 0)
        if i0.size == 0:
            continue
        i0 = int(i0[0])
        first_v = sub[vcol][i0]
        if first_v is None or not math.isfinite(float(first_v)):
            continue
        nxt0 = sub["next_open"][i0]
        if nxt0 is None or not math.isfinite(float(nxt0)):
            continue
        nxt0 = float(nxt0)
        if name == "hold_flat":
            gross = float(sub["session_close_ret_from_entry"][i0])
            gross = (1.0 + gross) / (1.0 + (nxt0 / float(sub["entry_px"][i0]) - 1.0)) - 1.0
        else:
            gb = giveback_mask({c: sub[c].to_numpy() for c in sub.columns}, rule.params["pct"])
            hits = np.flatnonzero(gb[i0 + 1:])
            hit = int(hits[0]) + i0 + 1 if hits.size else None
            if hit is None:
                gross_abs = float(sub["session_close_ret_from_entry"][i0])
                gross = (1.0 + gross_abs) / (nxt0 / float(sub["entry_px"][i0])) - 1.0
            else:
                no_hit = sub["next_open"][hit]
                if no_hit is None or not math.isfinite(float(no_hit)):
                    # the panel's convention: a trigger on the session's last bar exits at the
                    # forced flat close (never fabricated as a next open)
                    gross = (1.0 + float(sub["session_close_ret_from_entry"][i0])) / (
                        nxt0 / float(sub["entry_px"][i0])) - 1.0
                else:
                    gross = float(no_hit) / nxt0 - 1.0
        diffs.append(abs(gross - float(first_v)))
        n += 1
    if not diffs:
        return None
    arr = np.array(diffs)
    return {"column": vcol, "n_compared": n, "mean_abs_diff": float(arr.mean()),
            "max_abs_diff": float(arr.max()), "n_within_1e-9": int((arr <= 1e-9).sum())}


def run_ledger(df: pl.DataFrame, rule: Rule, bps_total: float = 100.0,
               with_crosscheck: bool = True) -> dict:
    """Full ledger over a sorted panel frame (one entry per development block)."""
    guard_columns(rule.columns)          # every rule, built-in or declarative, passes the guard
    k = fric_k(bps_total)
    side = bps_total / 2.0 / 10000.0
    by_block: dict[str, list] = {}
    for key, sub in members_of(df):
        by_block.setdefault(str(sub["block"][0]), []).append(
            replay_member(sub, rule, k))
    out_blocks = {}
    for block in sorted(by_block, key=_block_sort_key):
        out_blocks[block] = aggregate_block(by_block[block])
    total_rows = [r for rows in by_block.values() for r in rows]
    judged_all = [r for r in total_rows if r.get("delta") is not None]
    by_family_total = {}
    for fam in sorted({r["family"] for r in total_rows}):
        sub = [r for r in total_rows if r["family"] == fam]
        by_family_total[fam] = {
            "n_members": len(sub),
            "n_early_exits": sum(1 for r in sub if r.get("early_exit")),
            "failure_tax_avoided": sum(r.get("avoided", 0.0) for r in sub),
            "dollars_destroyed": sum(r.get("destroyed", 0.0) for r in sub),
            "net_dollar_ledger": sum(r.get("delta", 0.0) or 0.0 for r in sub),
        }
    drift = sum(1 for r in judged_all if r.get("tail_class_100_mismatch")
                or r.get("tail_class_300_mismatch") or r.get("level_ret_mismatch"))
    out = {
        "rule": {"cli": rule.name, "kind": rule.kind, "state_columns": list(rule.columns),
                 "params": {kk: vv for kk, vv in rule.params.items() if kk != "conditions"}
                 if rule.kind != "spec" else
                 {"conditions": rule.params["conditions"], "logic": rule.params["logic"],
                  "consecutive": rule.params["consecutive"], "min_et": rule.params.get("min_et"),
                  "max_et": rule.params.get("max_et"), "origin": rule.params.get("origin"),
                  "notes": rule.params.get("notes")}},
        "friction_bps_total": bps_total,
        "friction_side": side,
        "friction_convention": ("per side side=bps_total/2/10000; a committed dollar buys "
                               "1/(1+side) of exposure and returns (1+gross)*(1-side) on exit; "
                               "entry friction is identical on both legs, so "
                               "delta = k*(gross_rule - gross_hold) with k=(1-side)/(1+side)"),
        "n_members_total": len(total_rows),
        "n_excluded_no_baseline_total": len(total_rows) - len(judged_all),
        "by_family_total": by_family_total,
        "tail_class_drift_n": drift,
        "tail_class_consistent": drift == 0,
        "blocks": out_blocks,
    }
    if with_crosscheck:
        cc = continuation_crosscheck(df, rule)
        if cc is not None:
            out["panel_continuation_crosscheck"] = cc
    return out


def _block_sort_key(b: str):
    if b.startswith("block") and b[5:].isdigit():
        return (0, int(b[5:]), b)
    return (1, 0, b)


# --------------------------------------------------------------------------- #
# self-test
# --------------------------------------------------------------------------- #
SYNTH_COLUMNS = ("sleeve_day", "ticker", "family", "entry_rank", "month", "block", "entry_et",
                 "entry_px", "et", "bar_index", "session_end", "next_open", "next_et",
                 "remaining_run", "session_close_ret_from_entry", "dist_from_running_high",
                 "ret_from_fill", "mfe_so_far", "session_peak_ret_from_entry",
                 "tail_class_100", "tail_class_300", "level_ret",
                 "bar_open", "bar_high", "bar_low", "bar_close", "running_high")


def synth_frame(members: list, block: str = "block1") -> pl.DataFrame:
    """Build a schema-conformant panel frame from explicit bars (independent of the ledger path).

    members: [{"sleeve_day","ticker","family","entry_rank","entry_px","bars":[(et,o,h,l,c), ...]}]
    Every derived column is computed here from the raw bars with the SCHEMA definitions.
    """
    rows = []
    for m in members:
        bars = m["bars"]
        ets = [int(b[0]) for b in bars]
        highs = [float(b[2]) for b in bars]
        closes = [float(b[4]) for b in bars]
        entry_px = float(m["entry_px"])
        session_end = ets[-1]
        close_ret = closes[-1] / entry_px - 1.0
        peak = max(highs) / entry_px - 1.0
        run_high = -np.inf
        for i, b in enumerate(bars):
            et, o, hi, lo, c = (float(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]))
            run_high = max(run_high, hi)
            nxt_o = float(bars[i + 1][1]) if i + 1 < len(bars) else None
            nxt_et = ets[i + 1] if i + 1 < len(bars) else None
            rem = (max(highs[i + 1:]) / nxt_o - 1.0) if nxt_o is not None else None
            rows.append({
                "sleeve_day": m["sleeve_day"], "ticker": m["ticker"], "family": m["family"],
                "entry_rank": int(m["entry_rank"]), "month": m["sleeve_day"][:7], "block": block,
                "entry_et": ets[0], "entry_px": entry_px, "et": int(et), "bar_index": i,
                "session_end": session_end, "next_open": nxt_o, "next_et": nxt_et,
                "remaining_run": rem, "session_close_ret_from_entry": close_ret,
                "dist_from_running_high": c / run_high - 1.0,
                "ret_from_fill": c / entry_px - 1.0, "mfe_so_far": run_high / entry_px - 1.0,
                "session_peak_ret_from_entry": peak,
                "tail_class_100": (rem >= 1.0) if rem is not None else None,
                "tail_class_300": (rem >= 3.0) if rem is not None else None,
                "level_ret": (nxt_o / entry_px - 1.0) if nxt_o is not None else None,
                "bar_open": o, "bar_high": hi, "bar_low": lo, "bar_close": c,
                "running_high": run_high,
            })
    df = pl.DataFrame(rows, schema={c: pl.Float64 for c in SYNTH_COLUMNS}
                      | {"sleeve_day": pl.String, "ticker": pl.String, "family": pl.String,
                         "entry_rank": pl.Int64, "month": pl.String, "block": pl.String,
                         "entry_et": pl.Int64, "et": pl.Int64, "bar_index": pl.Int64,
                         "session_end": pl.Int64, "next_et": pl.Int64,
                         "tail_class_100": pl.Boolean, "tail_class_300": pl.Boolean})
    return df.sort(["block", "sleeve_day", "family", "entry_rank", "ticker", "bar_index"])


# --- synthetic fixtures with hand-computed answers -------------------------- #
def _giant_giant():
    """A single synthetic giant: climbs to +200% into the close after a quiet first 6 bars."""
    return [{"sleeve_day": "2021-02-01", "ticker": "GIANT", "family": "A_pm", "entry_rank": 1,
             "entry_px": 10.0,
             "bars": [(570, 10.0, 10.1, 9.9, 10.0),
                      (571, 10.0, 10.2, 9.9, 10.1),
                      (572, 10.1, 10.3, 10.0, 10.2),
                      (573, 10.2, 10.4, 10.1, 10.3),
                      (574, 10.3, 10.5, 10.2, 10.4),
                      (575, 10.4, 10.6, 10.3, 10.5),
                      (576, 10.6, 11.0, 10.5, 10.8),
                      (577, 10.8, 14.0, 10.8, 13.0),
                      (578, 13.0, 25.0, 12.9, 24.0),
                      (579, 24.0, 30.0, 23.9, 28.0)]}]


def _failure_set():
    """One deep failure that keeps falling and one whip that reclaims after a >10% dip.

    Closes are kept off the exact -10.0% boundary: comparators are exact IEEE comparisons on the
    panel's computed values (no epsilon), so a fixture sitting on the boundary would test float
    representation instead of the ledger.
    """
    fail = {"sleeve_day": "2021-02-02", "ticker": "FAIL1", "family": "A_pm", "entry_rank": 1,
            "entry_px": 10.0,
            "bars": [(570, 10.0, 10.0, 9.5, 9.6),
                     (571, 9.6, 9.6, 8.8, 8.9),
                     (572, 8.5, 8.5, 7.7, 7.8),
                     (573, 7.8, 7.8, 6.9, 7.0),
                     (574, 7.0, 7.0, 5.9, 6.0)]}
    whip = {"sleeve_day": "2021-02-02", "ticker": "WHIP", "family": "A_pm", "entry_rank": 2,
            "entry_px": 10.0,
            "bars": [(570, 10.0, 10.0, 9.5, 9.6),
                     (571, 9.6, 9.6, 8.8, 8.9),
                     (572, 9.2, 9.6, 9.1, 9.5),
                     (573, 9.5, 10.5, 9.4, 10.4),
                     (574, 10.4, 11.0, 10.3, 11.0)]}
    return [fail, whip]


def _down10_rule() -> SpecRule:
    return spec_rule({"name": "down10", "conditions": [{"column": "ret_from_fill", "op": "<=",
                                                        "value": -0.10}]}, origin="<selftest>")


def _close(x, y, tol=1e-9):
    return x is not None and y is not None and abs(float(x) - float(y)) <= tol


def self_test() -> dict:
    """Four acceptance tests with hand-computed expected numbers, plus a CLI guard proof."""
    k = fric_k(100.0)
    results = {}

    # -- (a) hold_flat: zero avoided, zero destroyed ------------------------------
    giant = synth_frame(_giant_giant())
    led_a = run_ledger(giant, parse_rule("hold_flat"), 100.0, with_crosscheck=False)
    blk = led_a["blocks"]["block1"]
    exp_a = {"failure_tax_avoided": 0.0, "dollars_destroyed": 0.0, "net_dollar_ledger": 0.0,
             "giant_dollars_destroyed_100": 0.0, "giant_dollars_destroyed_300": 0.0,
             "n_giants_cut": 0, "n_reentry_candidates": 0, "n_members": 1,
             "n_early_exits": 0}
    act_a = {kk: blk[kk] for kk in exp_a}
    ok_a = all(_close(act_a[kk], v) if isinstance(v, float) else act_a[kk] == v
               for kk, v in exp_a.items())
    results["a_hold_flat_zero"] = {
        "description": "hold_flat must produce zero avoided, zero destroyed",
        "expected": exp_a, "actual": act_a, "passed": bool(ok_a),
    }

    # -- (b) exit_at_bar:5 on the synthetic giant: large destroyed ---------------
    led_b = run_ledger(giant, parse_rule("exit_at_bar:5"), 100.0, with_crosscheck=False)
    blk_b = led_b["blocks"]["block1"]
    exit_px = 10.6                       # next_open of bar_index 5 (open of bar 6)
    close_end = 28.0                     # close of the session's last bar
    expected_delta = k * (exit_px - close_end) / 10.0
    exp_b = {"failure_tax_avoided": 0.0, "dollars_destroyed": -expected_delta,
             "net_dollar_ledger": expected_delta,
             "giant_dollars_destroyed_100": -expected_delta, "giant_dollars_destroyed_300": 0.0,
             "n_giants_cut": 1, "n_giants_cut_300": 0, "n_reentry_candidates": 1,
             "n_members": 1}
    act_b = {kk: blk_b[kk] for kk in exp_b}
    giant_row = blk_b["giants_cut"][0] if blk_b["giants_cut"] else {}
    exp_giant = {"exit_et": 576, "exit_px": exit_px, "forward_peak_after_exit_px": 30.0,
                 "forward_mfe_from_exit": 30.0 / exit_px - 1.0, "readmissible": True,
                 "forgone_run_dollars": 30.0 / exit_px - 1.0}
    act_giant = {kk: giant_row.get(kk) for kk in exp_giant}
    ok_b = (all(_close(act_b[kk], v) if isinstance(v, float) else act_b[kk] == v
                for kk, v in exp_b.items())
            and all(_close(act_giant[kk], v) if not isinstance(v, bool) else act_giant[kk] is v
                    for kk, v in exp_giant.items()))
    results["b_exit_at_bar_5_giant"] = {
        "description": ("exit_at_bar:5 on a synthetic giant: exit at 10.6 while the close is 28.0, "
                        "forward MFE from the exit +183.0% -> giant_100 destroyed"),
        "expected": {**exp_b, "giants_cut[0]": exp_giant}, "actual": {**act_b, "giants_cut[0]": act_giant},
        "passed": bool(ok_b),
    }

    # -- (c) a rule that exits members already down 10% beats destruction --------
    fail_set = synth_frame(_failure_set(), block="block1")
    led_c = run_ledger(fail_set, _down10_rule(), 100.0, with_crosscheck=False)
    blk_c = led_c["blocks"]["block1"]
    # FAIL1: trigger at close 8.9 (ret -11%) -> exit at next open 8.5; hold to close 6.0
    d_fail = k * ((8.5 / 10.0 - 1.0) - (6.0 / 10.0 - 1.0))
    # WHIP: trigger at close 8.9 (ret -11%) -> exit at next open 9.2; hold to close 11.0
    d_whip = k * ((9.2 / 10.0 - 1.0) - (11.0 / 10.0 - 1.0))
    exp_c = {"failure_tax_avoided": d_fail, "dollars_destroyed": -d_whip,
             "net_dollar_ledger": d_fail + d_whip, "n_members": 2, "n_early_exits": 2,
             "n_giants_cut": 0, "n_reentry_candidates": 1}
    act_c = {kk: blk_c[kk] for kk in exp_c}
    ok_c = (all(_close(act_c[kk], v) if isinstance(v, float) else act_c[kk] == v
                for kk, v in exp_c.items())
            and act_c["failure_tax_avoided"] > act_c["dollars_destroyed"]
            and _close(blk_c["net_dollar_ledger"], d_fail + d_whip))
    results["c_down10_avoids_more_than_it_destroys"] = {
        "description": ("rule 'exit when ret_from_fill <= -10%' on one deep failure and one whip: "
                        "avoided > destroyed"),
        "expected": {**exp_c, "identity": "avoided - destroyed == net"},
        "actual": {**act_c, "passes_avoided_gt_destroyed":
                   bool(act_c["failure_tax_avoided"] > act_c["dollars_destroyed"])},
        "passed": bool(ok_c),
    }

    # -- (d) the outcome-column guard fires -------------------------------------
    guard_cases = []
    for col in ("v_hold_flat", "final_high_flag", "remaining_run", "cost_of_waiting",
                "bars_to_next_high", "dd_before_next_high", "tail_class_100",
                "peak_et_after_t", "session_close_ret_from_entry"):
        spec = {"name": f"leak_{col}",
                "conditions": [{"column": col, "op": ">=", "value": 0.0}]}
        try:
            spec_rule(spec, origin="<selftest>")
            guard_cases.append({"column": col, "raised": False, "error": None})
        except OutcomeLeakError as exc:
            guard_cases.append({"column": col, "raised": True, "error": str(exc)})
    # a legitimate state column must pass the guard
    ok_col = spec_rule({"name": "ok", "conditions": [{"column": "dist_from_running_high",
                                                      "op": "<=", "value": -0.1}]},
                       origin="<selftest>").columns == ("dist_from_running_high",)
    # and the same guard must fire through the real CLI, with a non-zero exit
    cli = _cli_guard_probe()
    ok_d = (all(c["raised"] for c in guard_cases) and ok_col and cli["exit_code"] != 0
            and cli["outcome_leak_in_stderr"])
    results["d_outcome_column_raises"] = {
        "description": ("a rule that reads an outcome column must raise, in-process and via the "
                        "CLI (which must exit non-zero)"),
        "expected": {"raised_for_all_prefixes": True, "state_column_accepted": True,
                     "cli_exit_nonzero": True},
        "actual": {"guard_cases": guard_cases, "state_column_accepted": bool(ok_col),
                   "cli_exit_code": cli["exit_code"], "cli_stderr_tail": cli["stderr_tail"],
                   "cli_outcome_leak_in_stderr": cli["outcome_leak_in_stderr"]},
        "passed": bool(ok_d),
    }
    return results


def _cli_guard_probe() -> dict:
    """Run the CLI on a leaking spec file and on a synthetic panel; prove it exits non-zero."""
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        spec = tdp / "leak.json"
        spec.write_text(json.dumps({"name": "leak",
                                    "conditions": [{"column": "tail_class_100", "op": "==",
                                                    "value": 1.0}]}))
        panel = tdp / "panel.parquet"
        synth_frame(_giant_giant()).write_parquet(panel)
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--rule", f"spec:{spec}",
             "--panel", str(panel)],
            capture_output=True, text=True)
        err = (proc.stderr or "").strip().splitlines()
        return {"exit_code": proc.returncode,
                "stderr_tail": err[-3:] if err else [],
                "outcome_leak_in_stderr": "OutcomeLeakError" in (proc.stderr or "")
                or "outcome prefix" in (proc.stderr or "")}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cmd_run(args) -> int:
    rule = parse_rule(args.rule)
    panel = Path(args.panel)
    if not panel.exists():
        print(f"panel absent: {panel} (tool ready; nothing to score)", file=sys.stderr)
        return 3
    df = load_panel(panel, rule)
    ledger = run_ledger(df, rule, args.bps, with_crosscheck=not args.no_crosscheck)
    ledger["panel"] = str(panel)
    ledger["panel_rows"] = df.height
    if args.json:
        _write_json(Path(args.json), ledger)
    _print_ledger(ledger)
    return 0


def _print_ledger(ledger: dict) -> None:
    print(f"rule={ledger['rule']['cli']}  bps_total={ledger['friction_bps_total']}  "
          f"members={ledger['n_members_total']}")
    hdr = (f"{'block':>8} {'n':>6} {'early':>6} {'avoided':>11} {'destroyed':>11} {'net':>11} "
           f"{'giant$100':>10} {'#g100':>5} {'#g300':>5} {'#reentry':>8}")
    print(hdr)
    for block, b in ledger["blocks"].items():
        print(f"{block:>8} {b['n_members']:>6} {b['n_early_exits']:>6} "
              f"{b['failure_tax_avoided']:>11.4f} {b['dollars_destroyed']:>11.4f} "
              f"{b['net_dollar_ledger']:>11.4f} {b['giant_dollars_destroyed_100']:>10.4f} "
              f"{b['n_giants_cut']:>5} {b['n_giants_cut_300']:>5} "
              f"{b['n_reentry_candidates']:>8}")
    cc = ledger.get("panel_continuation_crosscheck")
    if cc:
        print(f"continuation cross-check vs {cc['column']}: n={cc['n_compared']} "
              f"mean|diff|={cc['mean_abs_diff']:.3e} max|diff|={cc['max_abs_diff']:.3e}")
    for fam, f in ledger.get("by_family_total", {}).items():
        print(f"  family {fam:>5}: n={f['n_members']:>5} early={f['n_early_exits']:>5} "
              f"avoided={f['failure_tax_avoided']:>10.4f} destroyed={f['dollars_destroyed']:>10.4f} "
              f"net={f['net_dollar_ledger']:>10.4f}")
    if not ledger.get("tail_class_consistent", True):
        print(f"  WARNING: tail_class drift in {ledger['tail_class_drift_n']} member rows")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, default=_json_default))
    tmp.replace(path)


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, (np.bool_,)):
        return bool(o)
    raise TypeError(f"not JSON serialisable: {type(o)}")


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def _smoke_dry_run(smoke: Path, bps: float) -> dict:
    """Provisional end-to-end run against the panel producer's *smoke* panel, if it exists.

    This is NOT the deliverable panel; it proves the ledger consumes real producer output with the
    frozen column names, and reports the panel-continuation cross-check residual for that output.
    """
    if not smoke.exists():
        return {"status": "absent", "panel": str(smoke)}
    out = {"status": "present", "panel": str(smoke), "panel_sha256": _sha256(smoke),
           "note": "producer smoke panel, NOT the deliverable panel.parquet; provisional evidence only"}
    for spec in ("hold_flat", "giveback:10"):
        try:
            rule = parse_rule(spec)
            df = load_panel(smoke, rule)
            led = run_ledger(df, rule, bps)
            led["panel_rows"] = df.height
            out[spec] = led
        except Exception as exc:                          # noqa: BLE001 - reported, not swallowed
            out[spec] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


def cmd_self_test(args) -> int:
    tests = self_test()
    payload = {
        "tool": "factory/scripts/basket_atlas_ledger.py",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "worktree": str(ROOT),
        "friction": {"bps_total_default": 100.0, "side": 0.005,
                     "k_two_sided": fric_k(100.0),
                     "convention": ("per side side=bps_total/2/10000; committed dollar buys "
                                    "1/(1+side) exposure; exit returns (1+gross)*(1-side); the "
                                    "entry charge is identical on both legs so "
                                    "delta = k*(gross_rule - gross_hold), k=(1-side)/(1+side)"),
                     "note": ("the rule may take one exit; the baseline's exit (forced flat) and "
                              "the rule's exit are each charged one side, so the two-legged k "
                              "applies to both returns and the difference is exact")},
        "ledger_definition": {
            "delta_member": ("(rule return, entry_px -> rule exit, net of friction) - "
                             "(hold-to-flat return, entry_px -> session close, net of friction)"),
            "failure_tax_avoided": "sum of positive delta only",
            "dollars_destroyed": "-sum of negative delta only (positive number)",
            "net_dollar_ledger": "sum of all delta = avoided - destroyed",
            "giant_dollars_destroyed_100/_300": (
                "the subset of dollars_destroyed for members whose forward MFE from the exit "
                "point (panel `remaining_run` at the trigger row = max(high[t+1..end])/next_open-1) "
                "reached +100% / +300%"),
            "giants_cut": ("that subset with day, ticker, exit et, exit price, forward peak after "
                           "exit, forward peak from entry, readmissible"),
            "giants_cut_fields": {
                "day": "sleeve_day (the panel's session-date column)",
                "ticker": "ticker", "family": "family", "entry_rank": "entry_rank",
                "exit_et": "exit_et (ET minute-of-day of the exit bar)",
                "exit_px": "exit_px (that bar's next_open)",
                "forward_peak_after_exit": "forward_peak_after_exit_px = exit_px*(1+remaining_run)",
                "forward_mfe_from_exit": "remaining_run at the trigger row",
                "forward_peak_from_entry": "forward_peak_from_entry_pct / _px "
                                           "(session_peak_ret_from_entry, the ticket constant)",
                "readmissible": "readmissible (exit early and >= +10% above exit_px afterwards)",
                "delta": "delta", "destroyed_dollars": "destroyed_dollars = max(-delta, 0)",
                "forgone_run_dollars": "forgone_run_dollars = remaining_run (gross diagnostic)",
            },
            "readmissible": ("true iff the member exited early and max(high after the exit) >= "
                             "+10% above the exit price before the close (remaining_run >= 0.10)"),
            "n_reentry_candidates": "count of members with readmissible true (any delta sign)",
            "units": "return units per committed dollar; one equal-weight committed dollar per member",
            "baseline": ("hold-to-flat = `session_close_ret_from_entry` (close(session_end)/"
                         "entry_px - 1); the ledger's measurement may read outcome columns - the "
                         "baseline is one by definition - but no rule may"),
            "execution": ("decision on completed bar t executes at that row's `next_open` (open of "
                          "bar t+1); a trigger on the session's last bar has no next open, cannot "
                          "execute, and is scored as the forced flat (delta = 0, `hold_equivalent`)"),
        },
        "self_tests": tests,
        "ambiguities_resolved": [
            ("giant_dollars_destroyed_* is the subset of the destroyed amount (|delta|) restricted "
             "to members whose forward MFE from the exit reached the threshold, per Main's "
             "clarification, not the raw forgone run. The raw forgone run is additionally reported "
             "as `forgone_run_dollars_100/_300` (gross, per committed dollar) so both readings are "
             "visible and cannot be confused."),
            ("giants_cut lists the destroyed-and-giant members (delta < 0). Members that ran "
             "+100% after the exit yet whose delta was positive (the name spiked then closed below "
             "the exit price) are not 'destroyed'; their count is reported as "
             "`n_giants_cut_any_delta_sign` so the difference is explicit rather than silent."),
            ("n_reentry_candidates counts readmissible members over all early exits in the block; "
             "the giant-only restriction is reported separately as `n_reentry_candidates_giants`."),
            ("a rule trigger on the session's last completed bar cannot execute (no next open); it "
             "is scored as the forced flat, never fabricated as a close fill."),
            ("spec comparators are exact IEEE comparisons on the panel's computed column values; "
             "no epsilon is applied, so a spec threshold should not sit exactly on a computed "
             "boundary (the self-test fixture is kept off the boundary for this reason)."),
        ],
    }

    panel = Path(args.panel)
    smoke = panel.parent / "smoke" / "panel.parquet"
    if panel.exists():
        real = {}
        for spec in ("hold_flat", "giveback:10"):
            try:
                rule = parse_rule(spec)
                df = load_panel(panel, rule)
                led = run_ledger(df, rule, args.bps, with_crosscheck=not args.no_crosscheck)
                led["panel"] = str(panel)
                led["panel_rows"] = df.height
                real[spec] = led
            except Exception as exc:                     # noqa: BLE001 - reported, not swallowed
                real[spec] = {"error": f"{type(exc).__name__}: {exc}"}
        payload["real_panel"] = {"status": "present", "panel": str(panel),
                                 "panel_sha256": _sha256(panel),
                                 "panel_bytes": panel.stat().st_size,
                                 "blocks": real}
    else:
        payload["real_panel"] = {
            "status": "absent",
            "panel": str(panel),
            "note": ("panel.parquet did not exist when this artifact was written; the tool is "
                     "ready and `--rule hold_flat` / `--rule giveback:10` will run unchanged once "
                     "the panel lands"),
        }
        payload["dry_run_smoke_panel"] = _smoke_dry_run(smoke, args.bps)
    payload["reproduce_commands"] = [
        ".venv/bin/python factory/scripts/basket_atlas_ledger.py --self-test",
        ".venv/bin/python factory/scripts/basket_atlas_ledger.py --rule hold_flat",
        ".venv/bin/python factory/scripts/basket_atlas_ledger.py --rule giveback:10",
        ".venv/bin/python factory/scripts/basket_atlas_ledger.py --rule exit_at_bar:30 "
        "--json /tmp/ledger_exit30.json",
        ".venv/bin/python factory/scripts/basket_atlas_ledger.py --rule exit_at_et:1130",
        "# declarative spec: {\"name\":\"dd10_3bars\",\"conditions\":[{\"column\":"
        "\"dist_from_running_high\",\"op\":\"<=\",\"value\":-0.10}],\"consecutive\":3}",
        ".venv/bin/python factory/scripts/basket_atlas_ledger.py --rule spec:/tmp/dd10_3bars.json",
        "# outcome-column guard (must exit 2 with OutcomeLeakError):",
        "echo '{\"conditions\":[{\"column\":\"remaining_run\",\"op\":\">=\",\"value\":0.1}]}' "
        "> /tmp/leak.json && .venv/bin/python factory/scripts/basket_atlas_ledger.py "
        "--rule spec:/tmp/leak.json",
    ]
    payload["self_test_summary"] = {
        "n_passed": sum(1 for t in tests.values() if t["passed"]), "n_tests": len(tests),
        "all_passed": all(t["passed"] for t in tests.values())}
    out = Path(args.json or ARTIFACT)
    _write_json(out, payload)
    print(f"self-tests: {payload['self_test_summary']['n_passed']}/{payload['self_test_summary']['n_tests']} "
          f"passed -> {out}")
    for name, t in tests.items():
        print(f"  [{'PASS' if t['passed'] else 'FAIL'}] {name}")
    if panel.exists():
        for spec, led in payload["real_panel"]["blocks"].items():
            if "error" in led:
                print(f"  real panel {spec}: ERROR {led['error']}")
            else:
                tot_net = sum(b["net_dollar_ledger"] for b in led["blocks"].values())
                print(f"  real panel {spec}: members={led['n_members_total']} "
                      f"net={tot_net:.4f}")
    else:
        print(f"  real panel: ABSENT ({panel}) — recorded, tool ready")
    return 0 if payload["self_test_summary"]["all_passed"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rule", help="hold_flat | exit_at_bar:N | exit_at_et:HHMM | giveback:G | "
                                   "spec:<path.json>")
    ap.add_argument("--panel", default=str(DEFAULT_PANEL))
    ap.add_argument("--bps", type=float, default=100.0, help="total round-trip bps (per side /2)")
    ap.add_argument("--json", default=None, help="write the ledger JSON here")
    ap.add_argument("--no-crosscheck", action="store_true",
                    help="skip the v_* continuation cross-check")
    ap.add_argument("--self-test", action="store_true",
                    help=f"run the four acceptance tests and write {ARTIFACT.name}")
    args = ap.parse_args(argv)
    try:
        if args.self_test:
            return cmd_self_test(args)
        if not args.rule:
            ap.error("--rule is required (or --self-test)")
        return cmd_run(args)
    except OutcomeLeakError as exc:
        print(f"OutcomeLeakError: {exc}", file=sys.stderr)
        return 2
    except (PanelSchemaError, RuleSpecError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
