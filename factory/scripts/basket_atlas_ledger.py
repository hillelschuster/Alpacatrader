#!/usr/bin/env python3
"""ATLAS dollar ledger v2 — judge a candidate stopping rule by the tail value it preserves.

Reads the frozen ATLAS panel (`factory/artifacts/basket/phase2/ATLAS/panel.parquet`, SCHEMA.md v2,
sha256 recorded in `coverage.json`) and replays one candidate stopping rule causally member-by-member,
pricing it against the *executable* hold-to-flat baseline in return units per committed dollar, per
development block.

Ledger definition (contract given by Main, v2 semantics):

    Δ_member = (return of the rule, entry -> the rule's executable exit, net of friction)
             - (return of the executable hold-to-flat, entry -> the engine's forced flat, net of friction)

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

v2 panel semantics this tool binds to (SCHEMA.md amendment v2 — binding, not re-derived):

- **Executable baseline is `v_forced_flat`.** The engine schedules FORCED_FLAT on the completed bar
  `session_end - 1` and executes it at the *open of the `session_end` bar*; that price is
  `future_forced_flat_px`. `v_hold_flat` (close of the session-end bar) is a **labelled close-only
  reference** and is NEVER the executable baseline here; it is reported only as the explicit
  diagnostic `close_reference_*`.
- **Forced flat has precedence by clock ET.** No release rule is evaluated on a bar with
  `et >= session_end - 1`; a first condition there is non-firing and the value stays `v_forced_flat`.
  The ledger applies the same precedence to every rule (`exit_at_bar`, `exit_at_et`, specs).
- **Terminal censoring.** A member whose tape stops before the session close has no terminal value
  and no executable liquidation (`terminal_censored`). Such members are excluded from every
  executable ledger aggregate and reported separately as **unresolved** (counts and the rule-side
  executable dollars), never zero-imputed and never last-print-imputed.
- **Giveback rules bind to the panel's declared fields.** For `giveback:G` the member's rule side is
  the panel's `v_giveback_G` / `giveback_fired_G` / `giveback_condition_after_forced_flat_G` at the
  fill row; the exit price is `next_open(fill) * (1 + v_giveback_G(fill))`. The ledger's own
  locator (scan from bar 1, forced-flat precedence) exists only to locate the exit *bar* for the
  forward-tail measurement, and every member's located exit price is reconciled against the panel's
  implied exit price to machine precision.

Friction (`bps_total`, default 100.0 = the contract's minimum base): per side `side = bps_total/2/10000`;
a committed dollar buys `1/(1+side)` of exposure and an exit returns `(1+gross)*(1-side)`. The entry
charge is identical on both legs, so Δ = k*(gross_rule - gross_hold) exactly, with
k = (1-side)/(1+side).

Causality: a rule decides on a *completed* bar `t`; execution is the open of the next printed bar
(`next_open`). Rules may read causal state columns only: the outcome-leak guard refuses outcome
columns, the `future_` prefix, and every family the coverage registry lists as causally excluded
(including the frozen `session_peak_*` / `session_close_ret_*` constants and the censor flags).

Rules (CLI `--rule`):
    hold_flat          never trigger; the executable forced flat -> Δ = 0 by construction
    exit_at_bar:N      decision at the bar with bar_index == N (0 = the fill bar), executed at its
                       next_open (N=0 exits at the first open after the fill)
    exit_at_et:HHMM    decision at the first bar with et >= HHMM (minutes-of-day ET), executed at
                       its next_open
    giveback:G         the panel's declared `v_giveback_G` continuation from the fill row
    spec:<path.json>   declarative per-minute stopping rule over state columns:
                       {"name": str,
                        "conditions": [{"column": str, "op": ">|>=|<|<=|==|!=", "value": num}],
                        "logic": "all"|"any" (default all),
                        "consecutive": K >= 1 (default 1; trigger bar and the K-1 bars before it
                                               must all satisfy the condition set),
                        "min_et": int (optional, inclusive trigger-bar et floor),
                        "max_et": int (optional, inclusive trigger-bar et ceiling)}

Usage:
    .venv/bin/python factory/scripts/basket_atlas_ledger.py --rule hold_flat
    .venv/bin/python factory/scripts/basket_atlas_ledger.py --rule giveback:10 --json /tmp/led.json
    .venv/bin/python factory/scripts/basket_atlas_ledger.py --rule spec:/tmp/rule.json
    .venv/bin/python factory/scripts/basket_atlas_ledger.py --self-test
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import resource
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
ATLAS = ROOT / "factory/artifacts/basket/phase2/ATLAS"
DEFAULT_PANEL = ATLAS / "panel.parquet"
ARTIFACT = ATLAS / "ledger_selftest.json"
COLUMN_REGISTRY = ATLAS / "column_registry.json"
COVERAGE = ATLAS / "coverage.json"

# Panel v2 (2026-09-25) — the build this ledger is written against.
PANEL_SHA_EXPECTED = "2a021eda878cdbe53e41d4cc88e2d8ba3b14d9a3a819723d9f679c8aca7c3488"

MEMBER_KEYS = ("sleeve_day", "family", "entry_rank", "ticker")

# Giveback trigger tolerance, exactly the producer's: `close <= running_high*(1-g) + 1e-9*running_high`
# (panel producer GIVEBACK_TOL_REL, SCHEMA v2 "a close exactly g% below is a hit"). It only matters
# for a bar sitting on the threshold; the relative form keeps it scale invariant.
GIVEBACK_TOL_REL = 1e-9

# Fallback guard sets, used only if the coverage registry is unavailable. The registry is
# authoritative (SCHEMA.md amendment v2.5).
FALLBACK_EXCLUDED_FAMILIES = ("outcome_level", "outcome_continuation", "outcome_path",
                              "ticket_constant", "future_meta", "censor")
FALLBACK_BLOCKED_PREFIXES = ("v_", "final_", "remaining_", "cost_", "bars_to_", "dd_before_",
                             "tail_class_", "peak_", "session_", "future_", "giveback_")
FALLBACK_FUTURE_ONLY = ("session_peak_et", "session_peak_ret_from_entry",
                        "session_peak_bars_from_entry", "session_close_ret_from_entry",
                        "future_member_last_et", "future_forced_flat_px",
                        "path_complete_to_session_end", "terminal_censored")
FALLBACK_STATE_COLUMNS = (
    "et", "bar_index", "bars_since_entry", "entry_et", "entry_px",
    "bar_open", "bar_high", "bar_low", "bar_close", "prev_close", "open0930",
    "volume", "dollar_volume",
    "ret_from_fill", "ret_from_prevclose", "ret_from_open0930", "mfe_so_far", "mae_so_far",
    "running_high", "dist_from_running_high", "mfe_surrendered", "bars_below_entry_episode",
    "episode_low", "bars_since_episode_low", "reclaim_count", "failed_reclaim_count",
    "bars_since_new_high", "new_high_count_5", "new_high_count_15", "new_high_count_30",
    "ret_1", "ret_3", "ret_5", "accel_1_5", "up_close_streak", "range_expansion",
    "bar_range_pct", "volume_vs_own_median", "volume_accel", "gap_count_so_far", "bars_since_gap",
    "candidate_count_t", "ret_percentile_candidates", "peer_ret_median", "peer_new_high_5",
)

# Columns the ledger itself needs in every panel (identifiers + execution + executable baseline).
CORE_COLUMNS = (
    "sleeve_day", "ticker", "family", "entry_rank", "block", "month",
    "entry_et", "entry_px", "et", "bar_index", "session_end",
    "next_open", "next_et", "future_forced_flat_px", "terminal_censored",
)
# Measurement/reporting conveniences; absent -> reported as null, never fabricated.
OPTIONAL_COLUMNS = (
    "bar_open", "bar_high", "bar_low", "bar_close", "prev_close", "open0930",
    "running_high", "dist_from_running_high", "mfe_so_far", "remaining_run",
    "session_peak_ret_from_entry", "session_peak_et", "session_close_ret_from_entry",
    "future_member_last_et", "path_complete_to_session_end",
    "v_forced_flat", "v_hold_flat", "v_sell", "level_ret",
    "tail_class_50", "tail_class_100", "tail_class_300",
    "v_giveback_5", "v_giveback_10", "v_giveback_15", "v_giveback_20",
    "giveback_fired_5", "giveback_fired_10", "giveback_fired_15", "giveback_fired_20",
    "giveback_condition_after_forced_flat_5", "giveback_condition_after_forced_flat_10",
    "giveback_condition_after_forced_flat_15", "giveback_condition_after_forced_flat_20",
)


class PanelSchemaError(RuntimeError):
    """The panel does not carry a column the ledger/rule requires."""


class OutcomeLeakError(RuntimeError):
    """A rule tried to condition on post-t information."""


class RuleSpecError(RuntimeError):
    """The rule specification is malformed."""


# --------------------------------------------------------------------------- #
# registry-driven guard
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Registry:
    """Causality guard source: the panel's coverage registry when available, else the fallback."""

    families: dict
    excluded_families: tuple
    blocked_prefixes: tuple
    future_only: tuple
    source: str

    def blocked_reason(self, column: str) -> "str | None":
        for pre in self.blocked_prefixes:
            if column.startswith(pre):
                return (f"column {column!r} starts with the blocked prefix {pre!r} "
                        f"(outcome / future-only namespace)")
        if column in self.future_only:
            return (f"column {column!r} is listed as future-only by the coverage registry "
                    f"(never causal state)")
        if self.families:
            fam = self.families.get(column)
            if fam is None:
                return (f"column {column!r} is not registered in the column registry: unknown "
                        f"provenance is refused fail-closed, even if the column exists in the "
                        f"parquet")
            if fam in self.excluded_families:
                return (f"column {column!r} belongs to the causally excluded family {fam!r} "
                        f"(post-t information)")
        return None

    def is_state(self, column: str) -> bool:
        """Fail-closed: with a registry loaded, only registered, non-excluded columns qualify."""
        if self.blocked_reason(column) is not None:
            return False
        if self.families:
            return column in self.families
        return column in FALLBACK_STATE_COLUMNS

    def allowed_state_columns(self) -> tuple:
        if not self.families:
            return tuple(c for c in FALLBACK_STATE_COLUMNS
                         if self.blocked_reason(c) is None)
        return tuple(sorted(c for c in self.families if self.is_state(c)))


def load_registry(atlas_dir: Path = ATLAS) -> Registry:
    """Read `coverage.json` + `column_registry.json`; fall back to the frozen snapshot."""
    families, excluded, prefixes, future_only = {}, (), (), ()
    source = "fallback snapshot (registry files absent)"
    reg_file = atlas_dir / "column_registry.json"
    cov_file = atlas_dir / "coverage.json"
    if reg_file.exists():
        reg = json.loads(reg_file.read_text())
        families = dict(reg.get("families", {}))
        source = f"{reg_file.name} v{reg.get('schema_version')}"
    if cov_file.exists():
        cov = json.loads(cov_file.read_text())
        excluded = tuple(cov.get("causal_excluded_families", ()))
        prefixes = tuple(cov.get("future_only_prefixes", ()))
        future_only = tuple(cov.get("future_only_columns", ()))
        source += f" + {cov_file.name} v{cov.get('schema_version')}"
    if not families:
        families = {}
    if not excluded:
        excluded = FALLBACK_EXCLUDED_FAMILIES
    if not future_only:
        future_only = FALLBACK_FUTURE_ONLY
    prefixes = tuple(dict.fromkeys(prefixes + FALLBACK_BLOCKED_PREFIXES))
    return Registry(families=families, excluded_families=tuple(excluded),
                    blocked_prefixes=prefixes, future_only=tuple(future_only), source=source)


def guard_column(column: str, where: str, reg: Registry) -> str:
    """Refuse any rule input carrying post-t information. Loud, never silent."""
    reason = reg.blocked_reason(column)
    if reason is not None:
        raise OutcomeLeakError(
            f"{where}: rule input column {column!r} is not causal state — {reason}. "
            f"Forbidden prefixes: {' '.join(reg.blocked_prefixes)}; "
            f"causally excluded families: {' '.join(reg.excluded_families)}"
        )
    return column


def guard_columns(columns, reg: Registry, where: str = "rule") -> None:
    for c in columns:
        guard_column(c, where, reg)


# --------------------------------------------------------------------------- #
# rules
# --------------------------------------------------------------------------- #
@dataclass
class Rule:
    name: str
    kind: str = "scan"             # scan | giveback | hold
    state_columns: tuple = ()      # columns the rule conditions on (guarded)
    measure_columns: tuple = ()    # extra panel columns the ledger needs to price this rule
    params: dict = field(default_factory=dict)

    @property
    def panel_columns(self) -> tuple:
        return tuple(dict.fromkeys(list(self.state_columns) + list(self.measure_columns)))

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
        return None if self.params["bar"] < 0 else (int(idx[0]) if idx.size else None)


@dataclass
class ExitAtEt(Rule):
    def trigger(self, cols: dict):
        idx = np.flatnonzero(cols["et"] >= self.params["et"])
        return int(idx[0]) if idx.size else None


@dataclass
class Giveback(Rule):
    """The panel's declared `v_giveback_G` continuation from the fill row (v2 binding).

    The ledger does not re-derive the rule: it reads the panel's `v_giveback_G`,
    `giveback_fired_G` and `giveback_condition_after_forced_flat_G` at the fill row. The locator
    below re-walks the same condition only to identify the exit *bar*, so the forward tail can be
    measured from the exit price; every located exit price is reconciled against the panel's
    implied exit price (`next_open(fill) * (1 + v_giveback_G(fill))`) to machine precision.
    """

    def trigger(self, cols: dict):
        raise RuleSpecError("giveback rules do not use the generic trigger path")

    @property
    def level(self) -> int:
        return int(self.params["pct"])

    def v_column(self) -> str:
        return f"v_giveback_{self.level}"

    def fired_column(self) -> str:
        return f"giveback_fired_{self.level}"

    def after_forced_flat_column(self) -> str:
        return f"giveback_condition_after_forced_flat_{self.level}"

    def locate_exit(self, cols: dict) -> int:
        """Row index of the exit bar under the panel's v2 semantics; -1 -> the forced-flat bar.

        Scan starts at bar 1: the panel's continuation from row t looks at bars after t, and the
        ledger binds the rule to the panel's continuation *from the fill row*. A first condition on
        a bar with `et >= session_end - 1` is preempted by the engine's forced flat.
        """
        n = len(cols["et"])
        if n < 2:
            return -1
        mask = giveback_mask(cols, self.level)[1:]
        hits = np.flatnonzero(mask)
        if hits.size == 0:
            return -1
        t = int(hits[0]) + 1
        se = int(cols["session_end"][0])
        if int(cols["et"][t]) >= se - 1:
            return -1
        j = t + 1
        if j >= n:
            return -1
        return j


def giveback_mask(cols: dict, pct: float) -> np.ndarray:
    """Causal mask of bars whose close is >= pct% below the running high as of that bar.

    The comparison is the producer's exactly: `close <= running_high*(1-g) + 1e-9*running_high`,
    so the ledger's locator and the panel's declared continuation agree on boundary bars.
    """
    g = pct / 100.0
    if "bar_close" in cols and "running_high" in cols:
        c, rh = cols["bar_close"].astype(float), cols["running_high"].astype(float)
        return np.isfinite(c) & np.isfinite(rh) & (c <= rh * (1.0 - g) + GIVEBACK_TOL_REL * rh)
    dd = cols["dist_from_running_high"]                     # documented fallback (no tape column)
    return np.isfinite(dd) & (dd <= -g + GIVEBACK_TOL_REL)


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


def parse_rule(text: str, reg: Registry) -> Rule:
    """Parse the CLI rule string into a Rule, enforcing the causality guard."""
    if text == "hold_flat":
        return HoldFlat(name="hold_flat", kind="hold")
    if text.startswith("exit_at_bar:"):
        n = int(text.split(":", 1)[1])
        if n < 0:
            raise RuleSpecError("exit_at_bar:N requires N >= 0")
        return ExitAtBar(name=text, state_columns=("bar_index",), params={"bar": n})
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
        return ExitAtEt(name=text, state_columns=("et",),
                        params={"et": hh * 60 + mm, "hhmm": raw})
    if text.startswith("giveback:"):
        g = float(text.split(":", 1)[1])
        if not (0 < g < 100) or not float(g).is_integer():
            raise RuleSpecError("giveback:G requires an integer 0 < G < 100")
        rule = Giveback(
            name=text, kind="giveback",
            state_columns=("bar_close", "running_high", "dist_from_running_high", "et"),
            measure_columns=(f"v_giveback_{int(g)}", f"giveback_fired_{int(g)}",
                             f"giveback_condition_after_forced_flat_{int(g)}",
                             "bar_open", "future_forced_flat_px", "remaining_run"),
            params={"pct": g})
        guard_columns(rule.state_columns, reg, f"rule {text}")
        return rule
    if text.startswith("spec:"):
        path = Path(text.split(":", 1)[1])
        if not path.exists():
            raise RuleSpecError(f"spec file not found: {path}")
        return spec_rule(json.loads(path.read_text()), reg, origin=str(path))
    raise RuleSpecError(
        f"unknown rule {text!r}; expected hold_flat | exit_at_bar:N | exit_at_et:HHMM | "
        f"giveback:G | spec:<path.json>"
    )


def spec_rule(spec: dict, reg: Registry, origin: str = "<inline>") -> SpecRule:
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
        guard_column(col, f"{origin}: condition {i}", reg)      # <- guard before anything else
        if not reg.is_state(col):
            raise RuleSpecError(
                f"{origin}: condition {i} column {col!r} is not declared causal state. "
                f"Declared state columns: {', '.join(reg.allowed_state_columns())}"
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
                    state_columns=tuple(dict.fromkeys(columns)),
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
    missing_rule = [c for c in rule.panel_columns if c not in cols]
    if missing_rule:
        raise PanelSchemaError(
            f"{path}: rule {rule.name!r} needs panel columns {missing_rule}, absent from the panel. "
            f"Available: {sorted(cols)}"
        )
    keep = [c for c in dict.fromkeys(list(CORE_COLUMNS) + list(OPTIONAL_COLUMNS)
                                     + list(rule.panel_columns)) if c in cols]
    df = pl.read_parquet(path, columns=keep)
    return df.sort(["block", "sleeve_day", "family", "entry_rank", "ticker", "bar_index"])


def members_of(df: pl.DataFrame):
    """Deterministic per-member iteration over a sorted panel frame."""
    for key, sub in df.partition_by(list(MEMBER_KEYS), maintain_order=True, as_dict=True).items():
        yield key, sub


# --------------------------------------------------------------------------- #
# replay
# --------------------------------------------------------------------------- #
def _finite(x) -> bool:
    return x is not None and math.isfinite(float(x))


def _observed_censored_exit(cols: dict, rule: Rule) -> "tuple[float | None, float | None, int | None]":
    """Observed rule-side exit on a terminal-censored tape: (gross return, price, et) or (None,...).

    Censored members have no forced-flat baseline, so no delta and no net can be formed. But a
    trigger with an executable next open is still a real event on the causal tape: it is reported
    as an observation, never mixed into the executable ledger.
    """
    if rule.kind == "hold":
        return None, None, None
    if rule.kind == "giveback":
        j = rule.locate_exit(cols)
        if j < 0 or "bar_open" not in cols:
            return None, None, None
        px = float(cols["bar_open"][j])
        return px / float(cols["entry_px"][0]) - 1.0, px, int(cols["et"][j])
    res = _resolve_scan(cols, rule)
    if res["kind"] != "exit":
        return None, None, None
    px = float(res["exit_px"])
    return px / float(cols["entry_px"][0]) - 1.0, px, int(res["exit_et"])


def replay_member(sub: pl.DataFrame, rule: Rule, k: float) -> dict:
    """One member's causal replay -> its row of the dollar ledger (v2 semantics)."""
    cols = {c: sub[c].to_numpy() for c in sub.columns}
    entry_px = float(sub["entry_px"][0])
    day, ticker = str(sub["sleeve_day"][0]), str(sub["ticker"][0])
    family, rank = str(sub["family"][0]), int(sub["entry_rank"][0])
    block = str(sub["block"][0])
    session_end = int(sub["session_end"][0])
    et = cols["et"]
    tape_end = int(et[-1])
    n_bars = sub.height
    ffp = cols["future_forced_flat_px"][0]
    censored = bool(cols["terminal_censored"][0]) if "terminal_censored" in cols else False

    row = {"sleeve_day": day, "ticker": ticker, "family": family, "entry_rank": rank,
           "block": block, "entry_px": entry_px, "entry_et": int(sub["entry_et"][0]),
           "n_bars": n_bars, "session_end": session_end, "tape_end_et": tape_end,
           "terminal_censored": censored}

    # ---- resolve the rule's exit ---------------------------------------------------------- #
    if rule.kind == "hold":
        resolved = {"kind": "hold"}
    elif rule.kind == "giveback":
        resolved = _resolve_giveback(cols, rule)
    else:
        resolved = _resolve_scan(cols, rule)
    row.update({kk: vv for kk, vv in resolved.items()
                if kk in ("exit_kind", "trigger_bar_index", "trigger_et", "exit_bar_index",
                          "exit_et", "exit_px", "early_exit", "preempted_by_forced_flat",
                          "giveback_fired", "giveback_after_forced_flat")})

    # ---- rule-side return (executable) ---------------------------------------------------- #
    rule_side_gross = None
    if resolved["kind"] == "hold_via_giveback":
        if _finite(ffp) and not censored:
            rule_side_gross = float(ffp) / entry_px - 1.0
        row.update({"status": "giveback_non_firing_valued_at_forced_flat",
                    "exit_kind": "forced_flat", "exit_et": session_end,
                    "exit_px": float(ffp) if _finite(ffp) else None,
                    "early_exit": False, "gross_rule": rule_side_gross})
    elif resolved["kind"] == "hold":
        if _finite(ffp) and not censored:
            rule_side_gross = float(ffp) / entry_px - 1.0
            resolved["exit_px"] = float(ffp)
            resolved["exit_et"] = session_end
            resolved["exit_bar_index"] = int(np.flatnonzero(et == session_end)[0]) \
                if np.any(et == session_end) else n_bars - 1
        row.update({"status": "hold_to_forced_flat", "exit_kind": "forced_flat",
                    "exit_et": session_end, "exit_px": float(ffp) if _finite(ffp) else None,
                    "early_exit": False, "gross_rule": rule_side_gross})
    elif resolved["kind"] == "exit":
        rule_side_gross = float(resolved["exit_px"]) / entry_px - 1.0
        row.update({"status": "early_exit", "gross_rule": rule_side_gross})
    elif resolved["kind"] == "hold_equivalent":
        if censored or not _finite(ffp):
            # the position rides to the forced flat, which does not exist on a censored tape ->
            # unresolved, never a fabricated zero
            row.update({"status": "unresolved_terminal_censored",
                        "exit_kind": resolved["exit_kind"], "exit_et": None, "exit_px": None,
                        "early_exit": False, "gross_rule": None, "delta": None,
                        "net_rule": None, "censored_rule_side_net": None,
                        "triggered_after_forced_flat": bool(resolved.get("preempted_by_forced_flat")),
                        "giveback_after_forced_flat": row.get("giveback_after_forced_flat")})
            return row
        rule_side_gross = float(ffp) / entry_px - 1.0
        row.update({"status": "hold_equivalent", "exit_kind": resolved["exit_kind"],
                    "exit_et": tape_end, "exit_px": None, "early_exit": False,
                    "gross_rule": rule_side_gross, "delta": 0.0, "avoided": 0.0,
                    "destroyed": 0.0, "readmissible": False, "forward_mfe_from_exit": None,
                    "triggered_after_forced_flat": bool(resolved.get("preempted_by_forced_flat"))})
        return row
    else:                                                       # unscored (no rule value at all)
        row.update({"status": resolved["status"], "unscored_reason": resolved.get("reason"),
                    "early_exit": bool(resolved.get("early_exit", False)),
                    "gross_rule": None, "delta": None})
        if censored:
            # the panel's own value is null here, but the causal tape may still carry an
            # executable rule exit: report it as an observation (never as a delta)
            observed, px, et_o = _observed_censored_exit(cols, rule)
            row.update({"observed_rule_exit_px": px, "observed_rule_exit_et": et_o,
                        "observed_rule_side_net": ((1.0 + observed) * k - 1.0)
                        if observed is not None else None})
        return row

    # ---- executable hold baseline: the engine's forced flat, from entry -------------------- #
    baseline_ok = _finite(ffp) and not censored
    net_rule = (1.0 + rule_side_gross) * k - 1.0 if rule_side_gross is not None else None
    if not baseline_ok:
        # The delta stays unresolved (no terminal value, so no forced-flat baseline). But when the
        # causal tape carries an executable rule exit (a known trigger with a next open), that
        # observed rule-side return is real and is reported in the separate observed-exit partition.
        observed, observed_px, observed_et = _observed_censored_exit(cols, rule)
        row.update({"status": "unresolved_terminal_censored",
                    "net_rule": net_rule, "delta": None,
                    "observed_rule_exit_px": observed_px, "observed_rule_exit_et": observed_et,
                    "observed_rule_side_net": ((1.0 + observed) * k - 1.0)
                    if observed is not None else None})
        return row
    gross_hold = float(ffp) / entry_px - 1.0
    net_hold = (1.0 + gross_hold) * k - 1.0
    delta = net_rule - net_hold
    row.update({"net_rule": net_rule, "gross_hold": gross_hold, "net_hold": net_hold,
                "delta": delta, "avoided": max(delta, 0.0), "destroyed": max(-delta, 0.0)})

    # ---- tail measurement from the exit point (giant classification / readmissibility) ----- #
    mfe_exit, peak_from_entry = None, None
    if resolved["kind"] == "exit":
        j = resolved["exit_bar_index"]
        if "bar_high" in cols:
            mfe_exit = float(np.max(cols["bar_high"][j:])) / float(resolved["exit_px"]) - 1.0
        if "session_peak_ret_from_entry" in cols:
            p = cols["session_peak_ret_from_entry"][0]
            peak_from_entry = float(p) if _finite(p) else None
        row.update({"forward_mfe_from_exit": mfe_exit,
                    "forward_peak_after_exit_px": float(resolved["exit_px"]) * (1.0 + mfe_exit)
                    if mfe_exit is not None else None,
                    "forward_peak_from_entry_pct": peak_from_entry,
                    "forward_peak_from_entry_px": entry_px * (1.0 + peak_from_entry)
                    if peak_from_entry is not None else None,
                    "readmissible": bool(mfe_exit is not None and mfe_exit >= 0.10)})
        # reconciliations against the panel's own declared fields (machine precision)
        t = resolved.get("trigger_bar_index")
        if t is not None and "remaining_run" in cols and mfe_exit is not None:
            rr = cols["remaining_run"][t]
            if _finite(rr):
                row["fwd_mfe_checked"] = True
                if abs(float(rr) - mfe_exit) > 1e-9:
                    row["fwd_mfe_mismatch"] = True
        if "level_ret" in cols:
            lr = cols["level_ret"][t] if t is not None else None
            if _finite(lr):
                row["level_ret_checked"] = True
                if abs(float(lr) - rule_side_gross) > 1e-12:
                    row["level_ret_mismatch"] = True
    else:
        row.update({"forward_mfe_from_exit": None, "forward_peak_after_exit_px": None,
                    "forward_peak_from_entry_pct": peak_from_entry,
                    "forward_peak_from_entry_px": None, "readmissible": False})

    # forced-flat precedence / giveback non-firing diagnostics
    if resolved.get("preempted_by_forced_flat"):
        row["triggered_after_forced_flat"] = True
    # baseline reconciliation: future_forced_flat_px must agree with the panel's v_forced_flat
    if "v_forced_flat" in cols and resolved.get("exit_bar_index") is not None:
        pass
    # labelled close-only reference (v_hold_flat): diagnostic ONLY, never the executable baseline
    if "session_close_ret_from_entry" in cols and rule_side_gross is not None:
        scr = cols["session_close_ret_from_entry"][0]
        if _finite(scr):
            row["delta_close_reference"] = net_rule - ((1.0 + float(scr)) * k - 1.0)
    no0 = cols["next_open"][0] if "next_open" in cols else None
    if _finite(no0) and "v_forced_flat" in cols:
        vf = cols["v_forced_flat"][0]
        if _finite(vf):
            implied = (1.0 + float(vf)) * (float(no0) / entry_px) - 1.0
            row["forced_flat_checked"] = True
            if abs(implied - gross_hold) > 1e-12:
                row["forced_flat_mismatch"] = True
    # definition-drift detector: the panel's own tail_class_* flags must agree with remaining_run
    if resolved["kind"] == "exit" and mfe_exit is not None:
        t = resolved.get("trigger_bar_index")
        for flag, thr in (("tail_class_100", 1.0), ("tail_class_300", 3.0)):
            if flag in cols and t is not None:
                tc = cols[flag][t]
                if tc is not None:
                    row[f"{flag}_checked"] = True
                    if bool(tc) != bool(mfe_exit >= thr):
                        row[f"{flag}_mismatch"] = True
    return row


def _resolve_scan(cols: dict, rule: Rule) -> dict:
    """Generic rule: decision at a completed bar, executed at the next printed bar's open."""
    t = rule.trigger(cols)
    if t is None:
        return {"kind": "hold"}
    se = int(cols["session_end"][0])
    et_t = int(cols["et"][t])
    n = len(cols["et"])
    # v2 amendment: the engine's forced flat on the completed bar session_end-1 preempts any
    # release evaluation at et >= session_end-1.
    if et_t >= se - 1:
        return {"kind": "hold_equivalent", "exit_kind": "preempted_by_forced_flat",
                "trigger_bar_index": t, "trigger_et": et_t, "preempted_by_forced_flat": True}
    if t + 1 >= n or not _finite(cols["next_open"][t]):
        return {"kind": "hold_equivalent", "exit_kind": "no_next_bar_forces_flat",
                "trigger_bar_index": t, "trigger_et": et_t, "preempted_by_forced_flat": False}
    j = t + 1
    exit_px = float(cols["next_open"][t])
    return {"kind": "exit", "exit_kind": rule.name, "trigger_bar_index": t, "trigger_et": et_t,
            "exit_bar_index": j, "exit_et": int(cols["et"][j]), "exit_px": exit_px,
            "early_exit": True, "preempted_by_forced_flat": False}


def _resolve_giveback(cols: dict, rule: Giveback) -> dict:
    """Bind the member's rule side to the panel's declared v2 giveback continuation."""
    vcol, fcol, acol = rule.v_column(), rule.fired_column(), rule.after_forced_flat_column()
    gv = cols[vcol][0]
    fired = cols[fcol][0] if fcol in cols else None
    after = cols[acol][0] if acol in cols else None
    if not _finite(gv):
        return {"kind": "unscored", "status": "unscored_giveback_value_null",
                "reason": f"{vcol} is null at the fill row (terminal-censored member or a "
                          f"single-bar tape): the rule value is unresolved, never imputed"}
    no0 = cols["next_open"][0]
    if not _finite(no0):
        return {"kind": "unscored", "status": "unscored_no_next_open",
                "reason": "no executable next bar after the fill bar"}
    exit_px = float(no0) * (1.0 + float(gv))
    j = rule.locate_exit(cols)                       # locator, for the forward tail measurement
    located_px = None
    if j >= 0 and "bar_open" in cols:
        located_px = float(cols["bar_open"][j])
    se = int(cols["session_end"][0])
    out = {"exit_kind": f"giveback:{rule.level}", "giveback_fired": bool(fired) if fired is not None
           else None, "giveback_after_forced_flat": bool(after) if after is not None else None}
    if j < 0:
        # non-firing (no condition, or the condition was preempted by the forced flat): the exit
        # is the engine's forced flat
        out.update({"kind": "exit_forced_flat" if fired is False or fired is None else "exit",
                    "early_exit": False, "exit_bar_index": int(np.flatnonzero(cols["et"] == se)[0])
                    if np.any(cols["et"] == se) else len(cols["et"]) - 1,
                    "exit_et": se, "exit_px": exit_px, "preempted_by_forced_flat": bool(after)})
        out["kind"] = "hold_via_giveback"
        return out
    t = j - 1
    out.update({"kind": "exit", "trigger_bar_index": t, "trigger_et": int(cols["et"][t]),
                "exit_bar_index": j, "exit_et": int(cols["et"][j]), "early_exit": True,
                "exit_px": exit_px, "preempted_by_forced_flat": False})
    if located_px is not None:
        out["locator_exit_px"] = located_px
        out["locator_matches_panel"] = abs(located_px - exit_px) <= max(1e-9, 1e-9 * abs(exit_px))
    return out


def _view(judged: list) -> dict:
    avoided = sum(r["avoided"] for r in judged)
    destroyed = sum(r["destroyed"] for r in judged)
    net = sum(r["delta"] for r in judged)
    return {
        "n_members": len(judged),
        "failure_tax_avoided": avoided,
        "dollars_destroyed": destroyed,
        "net_dollar_ledger": net,
        "n_giants_cut": sum(1 for r in judged if r["destroyed"] > 0
                            and r["forward_mfe_from_exit"] is not None
                            and r["forward_mfe_from_exit"] >= 1.0),
        "n_giants_cut_300": sum(1 for r in judged if r["destroyed"] > 0
                                and r["forward_mfe_from_exit"] is not None
                                and r["forward_mfe_from_exit"] >= 3.0),
        "n_reentry_candidates": sum(1 for r in judged if r.get("readmissible")),
        "n_early_exits": sum(1 for r in judged if r["early_exit"]),
    }


def _dedup_key(r: dict, a_pm_first: bool = True):
    """Declared deterministic duplicate rule: family priority, then entry_rank, then family."""
    pref = 0 if r["family"] == "A_pm" else 1
    return (pref if a_pm_first else 1 - pref, r["entry_rank"], r["family"])


def accounting_views(rows: list) -> dict:
    """Sleeve accounting vs independent-path accounting, with the duplicate exposure visible.

    A sleeve is one filled member (day, family, entry_rank, ticker). The same (day, ticker) market
    path can be held by more than one sleeve (A_pm and B600 both fill it, or the same name fills two
    ranks), so sleeve totals are NOT independent-path inference and are labelled as such.
    """
    judged = [r for r in rows if r.get("delta") is not None]
    paths: dict = {}
    for r in judged:
        paths.setdefault((r["sleeve_day"], r["ticker"]), []).append(r)
    shared = {k: v for k, v in paths.items() if len(v) > 1}
    all_paths: dict = {}
    for r in rows:
        all_paths.setdefault((r["sleeve_day"], r["ticker"]), []).append(r)
    shared_all = {k: v for k, v in all_paths.items() if len(v) > 1}
    kept = [min(v, key=lambda r: _dedup_key(r, True)) for v in paths.values()]
    kept_b = [min(v, key=lambda r: _dedup_key(r, False)) for v in paths.values()]
    sleeve = _view(judged)
    indep = _view(kept)
    indep_b = _view(kept_b)
    return {
        "sleeve_accounting": {
            **sleeve,
            "note": ("one row per filled sleeve member: the same (day,ticker) path can be held by "
                     "more than one sleeve, so these totals are NOT independent-path inference"),
            "n_paths": len(paths),
            "n_shared_paths": len(shared),
            "n_members_on_shared_paths": sum(len(v) for v in shared.values()),
            "shared_path_net_sum": sum(r["delta"] for v in shared.values() for r in v),
            "n_paths_all_members": len(all_paths),
            "n_shared_paths_all_members": len(shared_all),
            "n_members_on_shared_paths_all_members": sum(len(v) for v in shared_all.values()),
        },
        "independent_path_accounting": {
            **indep,
            "note": ("one row per (day,ticker) market path: duplicates collapsed by the declared "
                     "deterministic rule, so this view is free of sleeve double counting"),
            "rule": "keep A_pm over B600, then the lower entry_rank, then the alphabetically first family",
            "n_paths": len(paths),
            "n_shared_paths": len(shared),
            "n_members_dropped_by_dedup": len(judged) - len(kept),
        },
        "priority_sensitivity": {
            "note": ("the duplicate rule is a choice, so both priority orders are reported; a small "
                     "delta means the sleeve totals are not driven by which sleeve is kept"),
            "net_B600_first": indep_b["net_dollar_ledger"],
            "net_A_pm_first": indep["net_dollar_ledger"],
            "delta": indep_b["net_dollar_ledger"] - indep["net_dollar_ledger"],
        },
    }


def aggregate_block(rows: list, rule_side_source: str) -> dict:
    """Dollar ledger for one development block (executable aggregates + unresolved censored set)."""
    judged = [r for r in rows if r.get("delta") is not None]
    censored = [r for r in rows if r.get("terminal_censored")]
    other_unscored = [r for r in rows if r.get("delta") is None and not r.get("terminal_censored")]
    avoided = sum(r["avoided"] for r in judged)
    destroyed = sum(r["destroyed"] for r in judged)
    net = sum(r["delta"] for r in judged)
    giants100 = [r for r in judged if r["destroyed"] > 0 and r["forward_mfe_from_exit"] is not None
                 and r["forward_mfe_from_exit"] >= 1.0]
    giants300 = [r for r in giants100 if r["forward_mfe_from_exit"] >= 3.0]
    giants_any = [r for r in judged if r["early_exit"] and r["forward_mfe_from_exit"] is not None
                  and r["forward_mfe_from_exit"] >= 1.0]
    reentry = [r for r in judged if r.get("readmissible")]
    censored_observed = [r for r in censored
                         if r.get("observed_rule_side_net") is not None]

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
            # raw post-exit MFE ratio (dimensionless). NOT a committed-dollar payoff, never
            # subtracted from any ledger total; it is an oracle diagnostic of the tail left behind.
            "oracle_forward_mfe_ratio": r["forward_mfe_from_exit"],
        }

    blocks = {
        "n_members": len(judged),
        "n_members_total": len(rows),
        "n_members_unresolved_censored": len(censored),
        "n_members_unscored_other": len(other_unscored),
        "n_early_exits": sum(1 for r in judged if r["early_exit"]),
        "n_hold_like": sum(1 for r in judged if not r["early_exit"]),
        "n_triggered_after_forced_flat": sum(1 for r in judged
                                             if r.get("triggered_after_forced_flat")),
        "n_giveback_after_forced_flat": sum(1 for r in judged
                                            if r.get("giveback_after_forced_flat")),
        "failure_tax_avoided": avoided,
        "dollars_destroyed": destroyed,
        "net_dollar_ledger": net,
        "giant_dollars_destroyed_100": sum(r["destroyed"] for r in giants100),
        "giant_dollars_destroyed_300": sum(r["destroyed"] for r in giants300),
        "n_giants_cut": len(giants100),
        "n_giants_cut_300": len(giants300),
        "n_giants_cut_any_delta_sign": len(giants_any),
        # ORACLE diagnostics (never part of avoided/destroyed/net):
        #  * ratio_sum_*: sum of the raw post-exit MFE ratios (dimensionless, not dollars)
        #  * entry_scaled_*: the same tail expressed in committed dollars AT ENTRY
        #    ((peak_after_exit - exit_px)/entry_px). Still an oracle upper bound: it assumes the
        #    peak is capturable, and carries no next-open, friction or re-entry semantics.
        "oracle_forward_mfe_ratio_sum_100": sum(r["forward_mfe_from_exit"] for r in giants100),
        "oracle_forward_mfe_ratio_sum_300": sum(r["forward_mfe_from_exit"] for r in giants300),
        "oracle_entry_scaled_forward_mfe_100": sum(
            (r["forward_peak_after_exit_px"] - r["exit_px"]) / r["entry_px"] for r in giants100),
        "oracle_entry_scaled_forward_mfe_300": sum(
            (r["forward_peak_after_exit_px"] - r["exit_px"]) / r["entry_px"] for r in giants300),
        "n_reentry_candidates": len(reentry),
        "n_reentry_candidates_giants": sum(1 for r in giants100 if r["readmissible"]),
        "unresolved_censored": {
            "n_members": len(censored),
            "delta_status": ("unresolved for every censored member: the forced-flat baseline does "
                             "not exist on a tape without a terminal print, so no net and no delta "
                             "is formed (null is never read as zero or as the last print)"),
            "observed_exits": {
                "note": ("SEPARATE PARTITION — an observed rule-side exit on the causal tape (known "
                         "trigger plus an executable next open). These are observations only: they "
                         "carry no baseline, are never combined with a fabricated one, and are "
                         "excluded from failure_tax_avoided / dollars_destroyed / "
                         "net_dollar_ledger."),
                "source": rule_side_source,
                "n_exits": len(censored_observed),
                "n_members_without_executable_exit": len(censored) - len(censored_observed),
                "rule_side_net_sum": sum(r["observed_rule_side_net"] for r in censored_observed),
                "mean_rule_side_net": (sum(r["observed_rule_side_net"] for r in censored_observed)
                                       / len(censored_observed)) if censored_observed else None,
            },
        },
        "close_reference_baseline": {
            "note": ("LABELLED DIAGNOSTIC ONLY — the same per-member differences computed against "
                     "the close-only reference `v_hold_flat` (close of the session-end bar). This "
                     "is NOT the executable baseline and is never used in failure_tax_avoided, "
                     "dollars_destroyed or net_dollar_ledger."),
            "net_dollar_ledger": sum(r["delta_close_reference"] for r in judged
                                     if r.get("delta_close_reference") is not None),
            "n_members": sum(1 for r in judged if r.get("delta_close_reference") is not None),
        },
        "mean_delta": net / len(judged) if judged else None,
        "worst_delta": min((r["delta"] for r in judged), default=None),
        "best_delta": max((r["delta"] for r in judged), default=None),
        "fwd_mfe_check_n": sum(1 for r in judged if r.get("fwd_mfe_checked")),
        "fwd_mfe_mismatch_n": sum(1 for r in judged if r.get("fwd_mfe_mismatch")),
        "forced_flat_check_n": sum(1 for r in judged if r.get("forced_flat_checked")),
        "forced_flat_mismatch_n": sum(1 for r in judged if r.get("forced_flat_mismatch")),
        "level_ret_check_n": sum(1 for r in judged if r.get("level_ret_checked")),
        "level_ret_mismatch_n": sum(1 for r in judged if r.get("level_ret_mismatch")),
        "tail_class_100_check_n": sum(1 for r in judged if r.get("tail_class_100_checked")),
        "tail_class_100_mismatch_n": sum(1 for r in judged if r.get("tail_class_100_mismatch")),
        "tail_class_300_mismatch_n": sum(1 for r in judged if r.get("tail_class_300_mismatch")),
        "giveback_locator_check_n": sum(1 for r in judged
                                        if r.get("locator_matches_panel") is not None),
        "giveback_locator_mismatch_n": sum(1 for r in judged
                                           if r.get("locator_matches_panel") is False),
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
            "n_members_unresolved_censored": sum(1 for r in censored if r["family"] == fam),
        }
    blocks["accounting_views"] = accounting_views(rows)
    tol = 1e-9 * max(1.0, abs(net), avoided, destroyed)
    blocks["checks"] = {
        "net_identity": abs(net - (avoided - destroyed)) <= tol,
        "giants_100_subset_of_destroyed": blocks["giant_dollars_destroyed_100"] <= destroyed + 1e-12,
        "giants_300_subset_of_100": blocks["giant_dollars_destroyed_300"]
        <= blocks["giant_dollars_destroyed_100"] + 1e-12,
        "no_censored_in_executable_aggregates": True,
    }
    return blocks


def continuation_crosscheck(df: pl.DataFrame, rule: Rule) -> "dict | None":
    """Reconcile the ledger's per-member rule returns with the panel's v2 continuation columns.

    For hold_flat and giveback:G the ledger's rule side is derived from the panel's own
    continuation value measured at the fill row; this check re-derives the same value from the
    panel's *other* fields (`next_open`, `v_forced_flat`, `bar_open`, the tape) and reports the
    per-member residual, so the binding is verified rather than assumed. For other rules it
    verifies the exit price against the tape (`next_open(t)` == `bar_open(t+1)`).
    """
    if rule.kind == "giveback":
        vcol = rule.v_column()
    elif rule.kind == "hold":
        vcol = "v_forced_flat"
    else:
        return _tape_exit_check(df, rule)
    cols = set(df.columns)
    if vcol not in cols or "next_open" not in cols:
        return None
    diffs, n, skipped = [], 0, 0
    for key, sub in members_of(df):
        if bool(sub["terminal_censored"][0]):
            skipped += 1
            continue
        c = {c: sub[c].to_numpy() for c in sub.columns}
        v = c[vcol][0]
        if not _finite(v):
            skipped += 1
            continue
        no0 = c["next_open"][0]
        if not _finite(no0):
            skipped += 1
            continue
        ledger_side = float(no0) * (1.0 + float(v)) / float(c["entry_px"][0]) - 1.0
        if rule.kind == "hold":
            other = float(c["future_forced_flat_px"][0]) / float(c["entry_px"][0]) - 1.0
        else:
            j = rule.locate_exit(c)
            other = float(c["bar_open"][j]) / float(c["entry_px"][0]) - 1.0 if j >= 0 else \
                float(c["future_forced_flat_px"][0]) / float(c["entry_px"][0]) - 1.0
        diffs.append(abs(ledger_side - other))
        n += 1
    if not diffs:
        return None
    arr = np.array(diffs)
    return {"column": vcol, "n_compared": n, "n_skipped": skipped,
            "mean_abs_diff": float(arr.mean()), "max_abs_diff": float(arr.max()),
            "n_within_1e-12": int((arr <= 1e-12).sum())}


def _tape_exit_check(df: pl.DataFrame, rule: Rule) -> "dict | None":
    """For scan rules: the executed exit price must be the next printed bar's open."""
    cols = set(df.columns)
    if "bar_open" not in cols:
        return None
    diffs, n = [], 0
    for key, sub in members_of(df):
        c = {c: sub[c].to_numpy() for c in sub.columns}
        if bool(c["terminal_censored"][0]):
            continue
        res = _resolve_scan(c, rule)
        if res["kind"] != "exit":
            continue
        j = res["exit_bar_index"]
        diffs.append(abs(float(c["next_open"][res["trigger_bar_index"]]) - float(c["bar_open"][j])))
        n += 1
    if not diffs:
        return None
    arr = np.array(diffs)
    return {"column": "next_open vs bar_open", "n_compared": n,
            "mean_abs_diff": float(arr.mean()), "max_abs_diff": float(arr.max()),
            "n_within_1e-12": int((arr <= 1e-12).sum())}


def run_ledger(df: pl.DataFrame, rule: Rule, bps_total: float = 100.0,
               with_crosscheck: bool = True) -> dict:
    """Full ledger over a sorted panel frame (one entry per development block)."""
    k = fric_k(bps_total)
    side = bps_total / 2.0 / 10000.0
    rule_side_source = (
        "giveback rule observed from the causal tape with the panel's own trigger semantics "
        "(scan from bar 1, forced-flat precedence); the panel nulls v_giveback_G for censored "
        "members, so this is an observation, not a panel continuation"
        if rule.kind == "giveback" else
        "rule exit observed on the causal tape (trigger + next open); the missing piece is the "
        "baseline, not the exit"
        if rule.kind != "hold" else
        "hold_flat takes no exit, so no observed rule-side dollars exist")
    by_block: dict[str, list] = {}
    for key, sub in members_of(df):
        by_block.setdefault(str(sub["block"][0]), []).append(replay_member(sub, rule, k))
    out_blocks = {}
    for block in sorted(by_block, key=_block_sort_key):
        out_blocks[block] = aggregate_block(by_block[block], rule_side_source)
    total_rows = [r for rows in by_block.values() for r in rows]
    judged_all = [r for r in total_rows if r.get("delta") is not None]
    censored_all = [r for r in total_rows if r.get("terminal_censored")]
    by_family_total = {}
    for fam in sorted({r["family"] for r in total_rows}):
        sub = [r for r in total_rows if r["family"] == fam]
        sub_judged = [r for r in sub if r.get("delta") is not None]
        by_family_total[fam] = {
            "n_members": len(sub_judged),
            "n_members_unresolved_censored": sum(1 for r in sub if r.get("terminal_censored")),
            "n_early_exits": sum(1 for r in sub_judged if r.get("early_exit")),
            "failure_tax_avoided": sum(r.get("avoided", 0.0) for r in sub_judged),
            "dollars_destroyed": sum(r.get("destroyed", 0.0) for r in sub_judged),
            "net_dollar_ledger": sum(r.get("delta", 0.0) or 0.0 for r in sub_judged),
        }
    drift = sum(1 for r in judged_all if r.get("tail_class_100_mismatch")
                or r.get("tail_class_300_mismatch") or r.get("level_ret_mismatch")
                or r.get("fwd_mfe_mismatch") or r.get("forced_flat_mismatch"))
    out = {
        "rule": {"cli": rule.name, "kind": rule.kind,
                 "state_columns": list(rule.state_columns),
                 "panel_columns": list(rule.panel_columns),
                 "params": _rule_params(rule)},
        "friction_bps_total": bps_total,
        "friction_side": side,
        "friction_convention": ("per side side=bps_total/2/10000; a committed dollar buys "
                               "1/(1+side) of exposure and returns (1+gross)*(1-side) on exit; "
                               "entry friction is identical on both legs, so "
                               "delta = k*(gross_rule - gross_hold) with k=(1-side)/(1+side)"),
        "n_members_total": len(total_rows),
        "n_members_judged": len(judged_all),
        "n_members_unresolved_censored": len(censored_all),
        "by_family_total": by_family_total,
        "accounting_views": accounting_views(total_rows),
        "reconciliation_drift_n": drift,
        "reconciliation_clean": drift == 0,
        "blocks": out_blocks,
    }
    if with_crosscheck:
        cc = continuation_crosscheck(df, rule)
        if cc is not None:
            out["continuation_crosscheck"] = cc
    return out


def _rule_params(rule: Rule) -> dict:
    p = dict(rule.params)
    if rule.kind == "spec":
        return {"conditions": p["conditions"], "logic": p["logic"],
                "consecutive": p["consecutive"], "min_et": p.get("min_et"),
                "max_et": p.get("max_et"), "origin": p.get("origin"), "notes": p.get("notes")}
    return p


def _block_sort_key(b: str):
    if b.startswith("block") and b[5:].isdigit():
        return (0, int(b[5:]), b)
    return (1, 0, b)


# --------------------------------------------------------------------------- #
# self-test fixtures
# --------------------------------------------------------------------------- #
SYNTH_COLUMNS = ("sleeve_day", "ticker", "family", "entry_rank", "month", "block", "entry_et",
                 "entry_px", "et", "bar_index", "session_end", "next_open", "next_et",
                 "remaining_run", "session_close_ret_from_entry", "dist_from_running_high",
                 "ret_from_fill", "mfe_so_far", "session_peak_ret_from_entry",
                 "tail_class_100", "tail_class_300", "level_ret", "v_forced_flat", "v_hold_flat",
                 "v_giveback_10", "v_giveback_20", "giveback_fired_10", "giveback_fired_20",
                 "giveback_condition_after_forced_flat_10", "giveback_condition_after_forced_flat_20",
                 "future_forced_flat_px", "future_member_last_et",
                 "bar_open", "bar_high", "bar_low", "bar_close", "running_high")
GIVEBACK_LEVELS = (10, 20)


def synth_frame(members: list, block: str = "block1") -> pl.DataFrame:
    """Build a v2-conformant panel frame from explicit bars, independently of the ledger path.

    members: [{"sleeve_day","ticker","family","entry_rank","entry_px","bars":[(et,o,h,l,c), ...],
               "session_end": int (optional; default the last bar's et),
               "censored": bool (optional; tape stops before session_end)}]

    Every derived column is computed here from the raw bars with the SCHEMA v2 definitions,
    including the giveback continuations (scan from t+1; a first condition at `et >= session_end-1`
    is preempted by the forced flat and valued at `v_forced_flat`).
    """
    rows = []
    for m in members:
        bars = m["bars"]
        ets = [int(b[0]) for b in bars]
        opens = [float(b[1]) for b in bars]
        highs = [float(b[2]) for b in bars]
        lows = [float(b[3]) for b in bars]
        closes = [float(b[4]) for b in bars]
        entry_px = float(m["entry_px"])
        censored = bool(m.get("censored", False))
        session_end = int(m.get("session_end", ets[-1]))
        n = len(bars)
        run_high = np.maximum.accumulate(np.array(highs, dtype=float))
        ff_j = next((i for i, e in enumerate(ets) if e == session_end), n - 1)
        ffp = None if censored else opens[ff_j]
        if not censored and ets[-1] != session_end:
            raise ValueError(f"{m['ticker']}: complete tape must carry a bar at session_end")
        if censored and ets[-1] >= session_end:
            raise ValueError(f"{m['ticker']}: censored tape must stop before session_end")
        close_end = closes[-1]
        gb = {}
        for g in GIVEBACK_LEVELS:
            v = [None] * n
            fired = [None] * n
            after = [None] * n
            for i in range(n):
                if censored or i == n - 1:
                    continue
                hit = None
                for jj in range(i + 1, n):
                    if closes[jj] <= run_high[jj] * (1.0 - g / 100.0) \
                            + GIVEBACK_TOL_REL * run_high[jj]:
                        hit = jj
                        break
                if hit is None:
                    v[i] = ffp / opens[i + 1] - 1.0
                    fired[i] = False
                    after[i] = False
                elif ets[hit] >= session_end - 1:
                    v[i] = ffp / opens[i + 1] - 1.0
                    fired[i] = False
                    after[i] = True
                else:
                    v[i] = opens[hit + 1] / opens[i + 1] - 1.0
                    fired[i] = True
                    after[i] = False
            gb[g] = (v, fired, after)
        for i in range(n):
            nxt = opens[i + 1] if i + 1 < n else None
            rem = (max(highs[i + 1:]) / nxt - 1.0) if nxt is not None and not censored else None
            row = {
                "sleeve_day": m["sleeve_day"], "ticker": m["ticker"], "family": m["family"],
                "entry_rank": int(m["entry_rank"]), "month": m["sleeve_day"][:7], "block": block,
                "entry_et": ets[0], "entry_px": entry_px, "et": int(ets[i]), "bar_index": i,
                "session_end": session_end,
                "next_open": nxt, "next_et": (ets[i + 1] if i + 1 < n else None),
                "level_ret": (nxt / entry_px - 1.0) if nxt is not None else None,
                "remaining_run": rem,
                "tail_class_100": (rem >= 1.0) if rem is not None else None,
                "tail_class_300": (rem >= 3.0) if rem is not None else None,
                "v_forced_flat": (ffp / nxt - 1.0) if (nxt is not None and not censored) else None,
                "v_hold_flat": (close_end / nxt - 1.0) if (nxt is not None and not censored) else None,
                "session_close_ret_from_entry": None if censored else close_end / entry_px - 1.0,
                "session_peak_ret_from_entry": None if censored else max(highs) / entry_px - 1.0,
                "future_forced_flat_px": ffp,
                "future_member_last_et": ets[-1],
                "terminal_censored": censored,
                "path_complete_to_session_end": not censored,
                "dist_from_running_high": closes[i] / run_high[i] - 1.0,
                "ret_from_fill": closes[i] / entry_px - 1.0,
                "mfe_so_far": run_high[i] / entry_px - 1.0,
                "running_high": float(run_high[i]),
                "bar_open": opens[i], "bar_high": highs[i], "bar_low": lows[i],
                "bar_close": closes[i],
            }
            for g in GIVEBACK_LEVELS:
                v, fired, after = gb[g]
                row[f"v_giveback_{g}"] = v[i]
                row[f"giveback_fired_{g}"] = fired[i]
                row[f"giveback_condition_after_forced_flat_{g}"] = after[i]
            rows.append(row)
    df = pl.DataFrame(rows, schema={c: pl.Float64 for c in SYNTH_COLUMNS}
                      | {"sleeve_day": pl.String, "ticker": pl.String, "family": pl.String,
                         "entry_rank": pl.Int64, "month": pl.String, "block": pl.String,
                         "entry_et": pl.Int64, "et": pl.Int64, "bar_index": pl.Int64,
                         "session_end": pl.Int64, "next_et": pl.Int64,
                         "future_member_last_et": pl.Int64,
                         "tail_class_100": pl.Boolean, "tail_class_300": pl.Boolean,
                         "terminal_censored": pl.Boolean,
                         "path_complete_to_session_end": pl.Boolean,
                         "giveback_fired_10": pl.Boolean, "giveback_fired_20": pl.Boolean,
                         "giveback_condition_after_forced_flat_10": pl.Boolean,
                         "giveback_condition_after_forced_flat_20": pl.Boolean})
    return df.sort(["block", "sleeve_day", "family", "entry_rank", "ticker", "bar_index"])


def _giant_fixture():
    """A single synthetic giant: quiet first bars, then a +180% run into the close."""
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


def _failure_fixture():
    """A deep failure that keeps falling and a whip that reclaims after a >10% dip."""
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


def _down10_rule(reg: Registry) -> SpecRule:
    return spec_rule({"name": "down10", "conditions": [{"column": "ret_from_fill", "op": "<=",
                                                        "value": -0.10}]}, reg, origin="<selftest>")


def _close(x, y, tol=1e-9):
    return x is not None and y is not None and abs(float(x) - float(y)) <= tol


def _case(name: str, description: str, expected: dict, actual: dict, extra_ok=True) -> dict:
    ok = all(x == y if isinstance(y, (bool, int, str)) or y is None else _close(x, y)
             for x, y in ((actual.get(k), v) for k, v in expected.items()))
    return {"description": description, "expected": expected, "actual": actual,
            "passed": bool(ok and extra_ok)}


def self_test(reg: Registry) -> dict:
    """Acceptance tests with hand-computed expected numbers, plus v2 boundary cases."""
    k = fric_k(100.0)
    results = {}

    # -- (a) hold_flat: zero avoided, zero destroyed ------------------------------
    giant = synth_frame(_giant_fixture())
    led_a = run_ledger(giant, parse_rule("hold_flat", reg), 100.0, with_crosscheck=False)
    blk = led_a["blocks"]["block1"]
    exp_a = {"failure_tax_avoided": 0.0, "dollars_destroyed": 0.0, "net_dollar_ledger": 0.0,
             "giant_dollars_destroyed_100": 0.0, "giant_dollars_destroyed_300": 0.0,
             "n_giants_cut": 0, "n_reentry_candidates": 0, "n_members": 1,
             "n_early_exits": 0, "n_members_unresolved_censored": 0}
    results["a_hold_flat_zero"] = _case(
        "a", "hold_flat must produce zero avoided, zero destroyed",
        exp_a, {kk: blk[kk] for kk in exp_a})

    # -- (b) exit_at_bar:5 on the synthetic giant: large destroyed ----------------
    led_b = run_ledger(giant, parse_rule("exit_at_bar:5", reg), 100.0, with_crosscheck=False)
    blk_b = led_b["blocks"]["block1"]
    exit_px = 10.6                       # next_open of bar_index 5 (open of bar 6)
    ffp = 24.0                           # future_forced_flat_px = OPEN of the session_end bar (28.0
    #                                      is the close-only reference, deliberately not used)
    expected_delta = k * (exit_px - ffp) / 10.0
    exp_b = {"failure_tax_avoided": 0.0, "dollars_destroyed": -expected_delta,
             "net_dollar_ledger": expected_delta,
             "giant_dollars_destroyed_100": -expected_delta, "giant_dollars_destroyed_300": 0.0,
             "n_giants_cut": 1, "n_giants_cut_300": 0, "n_reentry_candidates": 1, "n_members": 1}
    act_b = {kk: blk_b[kk] for kk in exp_b}
    gr = blk_b["giants_cut"][0] if blk_b["giants_cut"] else {}
    exp_giant = {"exit_et": 576, "exit_px": exit_px, "forward_peak_after_exit_px": 30.0,
                 "forward_mfe_from_exit": 30.0 / exit_px - 1.0, "readmissible": True,
                 "oracle_forward_mfe_ratio": 30.0 / exit_px - 1.0}
    act_giant = {kk: gr.get(kk) for kk in exp_giant}
    ok_b = all((act_b[kk] == v) if isinstance(v, int) else _close(act_b[kk], v)
               for kk, v in exp_b.items()) and all(
        (act_giant[kk] is v) if isinstance(v, bool) else _close(act_giant[kk], v)
        for kk, v in exp_giant.items())
    results["b_exit_at_bar_5_giant"] = {
        "description": ("exit_at_bar:5 on a synthetic giant: exit at the next open 10.6 while the "
                        "executable forced flat is 24.0 (the session-end bar's open), forward MFE "
                        "from the exit +183.0%"),
        "expected": {**exp_b, "giants_cut[0]": exp_giant},
        "actual": {**act_b, "giants_cut[0]": act_giant}, "passed": bool(ok_b)}

    # -- (c) a rule that exits members already down 10% beats destruction ---------
    fail_set = synth_frame(_failure_fixture())
    led_c = run_ledger(fail_set, _down10_rule(reg), 100.0, with_crosscheck=False)
    blk_c = led_c["blocks"]["block1"]
    d_fail = k * ((8.5 / 10.0 - 1.0) - (7.0 / 10.0 - 1.0))    # FAIL1: exit 8.5, forced flat 7.0
    d_whip = k * ((9.2 / 10.0 - 1.0) - (10.4 / 10.0 - 1.0))   # WHIP: exit 9.2, forced flat 10.4
    exp_c = {"failure_tax_avoided": d_fail, "dollars_destroyed": -d_whip,
             "net_dollar_ledger": d_fail + d_whip, "n_members": 2, "n_early_exits": 2,
             "n_giants_cut": 0, "n_reentry_candidates": 1}
    act_c = {kk: blk_c[kk] for kk in exp_c}
    results["c_down10_avoids_more_than_it_destroys"] = _case(
        "c", ("rule 'exit when ret_from_fill <= -10%' on one deep failure and one whip: "
              "avoided > destroyed"),
        exp_c, act_c, extra_ok=act_c["failure_tax_avoided"] > act_c["dollars_destroyed"])

    # -- (d) the causality guard fires (v2 registry names) ------------------------
    guard_cases = []
    for col in ("v_hold_flat", "v_forced_flat", "final_high_flag", "remaining_run",
                "cost_of_waiting", "bars_to_next_high", "dd_before_next_high", "tail_class_100",
                "tail_class_300", "peak_et_after_t", "session_peak_et", "session_peak_ret_from_entry",
                "session_close_ret_from_entry", "future_member_last_et", "future_forced_flat_px",
                "terminal_censored", "path_complete_to_session_end", "giveback_fired_10",
                "giveback_condition_after_forced_flat_10", "level_ret", "session_end"):
        try:
            spec_rule({"name": f"leak_{col}",
                       "conditions": [{"column": col, "op": ">=", "value": 0.0}]}, reg,
                      origin="<selftest>")
            guard_cases.append({"column": col, "raised": False, "error": None})
        except OutcomeLeakError as exc:
            guard_cases.append({"column": col, "raised": True, "error": str(exc)})
    ok_col = spec_rule({"name": "ok", "conditions": [{"column": "dist_from_running_high",
                                                      "op": "<=", "value": -0.1}]},
                       reg, origin="<selftest>").state_columns == ("dist_from_running_high",)
    cli = _cli_guard_probe(reg)
    unregistered_in_process = False
    try:
        spec_rule({"name": "oracle", "conditions": [{"column": "oracle_alias", "op": ">",
                                                     "value": 0.0}]}, reg, origin="<selftest>")
    except OutcomeLeakError:
        unregistered_in_process = True
    ok_d = (all(c["raised"] for c in guard_cases) and ok_col and unregistered_in_process
            and all(v["exit_code"] != 0 and v["outcome_leak_in_stderr"] for v in cli.values())
            and not reg.is_state("oracle_alias"))
    results["d_causality_guard_fires"] = {
        "description": ("a rule that reads an outcome/future/censor column must raise, in-process "
                        "and via the CLI (non-zero exit) — registry-driven, not prefix-only"),
        "expected": {"raised_for_all_blocked_columns": True, "state_column_accepted": True,
                     "unregistered_column_refused_fail_closed": True,
                     "cli_exit_nonzero": True},
        "actual": {"guard_cases": guard_cases, "state_column_accepted": bool(ok_col),
                   "unregistered_column_refused_fail_closed": bool(unregistered_in_process),
                   "is_state(oracle_alias)": bool(reg.is_state("oracle_alias")),
                   "cli_probes": cli, "guard_source": reg.source},
        "passed": bool(ok_d)}

    # -- (e1) terminal censoring: excluded, reported as unresolved -----------------
    cens = {"sleeve_day": "2021-03-01", "ticker": "HALTED", "family": "A_pm", "entry_rank": 1,
            "entry_px": 10.0, "censored": True, "session_end": 580,
            "bars": [(570, 10.0, 10.0, 9.0, 9.2), (571, 9.2, 9.3, 8.0, 8.2),
                     (572, 8.2, 8.4, 7.5, 7.8), (573, 7.8, 7.9, 7.0, 7.2)]}
    led_e1 = run_ledger(synth_frame([cens]), _down10_rule(reg), 100.0, with_crosscheck=False)
    b1 = led_e1["blocks"]["block1"]
    # rule exits at the open after bar 1 (close 8.2 <= -10%): entry 10 -> 8.0
    exp_e1 = {"n_members": 0, "n_members_total": 1, "n_members_unresolved_censored": 1,
              "failure_tax_avoided": 0.0, "dollars_destroyed": 0.0, "net_dollar_ledger": 0.0}
    act_e1 = {kk: b1[kk] for kk in exp_e1}
    obs = b1["unresolved_censored"]["observed_exits"]
    act_e1["observed_exits"] = obs["n_exits"]
    act_e1["observed_without_exit"] = obs["n_members_without_executable_exit"]
    act_e1["observed_rule_side_net_sum"] = obs["rule_side_net_sum"]
    exp_e1["observed_exits"] = 1
    exp_e1["observed_without_exit"] = 0
    exp_e1["observed_rule_side_net_sum"] = (8.2 / 10.0) * k - 1.0   # exit = open of bar 2
    ok_e1 = all((act_e1[kk] == v) if isinstance(v, int) else _close(act_e1[kk], v)
                for kk, v in exp_e1.items())
    results["e1_terminal_censored_unresolved"] = {
        "description": ("a terminal-censored member contributes nothing to the executable ledger; "
                        "its counts and its executable rule-side dollars are reported separately as "
                        "unresolved (never zero-imputed, never last-print-imputed)"),
        "expected": exp_e1, "actual": act_e1, "passed": bool(ok_e1)}

    # -- (e2) giveback non-firing after the forced flat ----------------------------
    bars = [(570, 10.0, 10.5, 9.9, 10.4), (571, 10.4, 10.6, 10.2, 10.5),
            (572, 10.5, 10.7, 10.4, 10.6), (573, 10.6, 10.8, 10.5, 10.7),
            (574, 10.7, 10.8, 9.0, 9.2)]        # condition (close 9.2 <= 0.9*running_high) at the
    #                                            last bar, whose et == session_end -> post forced flat
    fx = synth_frame([{"sleeve_day": "2021-04-01", "ticker": "POSTFF", "family": "A_pm",
                       "entry_rank": 1, "entry_px": 10.0, "bars": bars}])
    led_e2 = run_ledger(fx, parse_rule("giveback:10", reg), 100.0, with_crosscheck=False)
    b2 = led_e2["blocks"]["block1"]
    exp_e2 = {"n_members": 1, "n_early_exits": 0, "n_hold_like": 1,
              "n_giveback_after_forced_flat": 1, "failure_tax_avoided": 0.0,
              "dollars_destroyed": 0.0, "net_dollar_ledger": 0.0, "n_giants_cut": 0}
    act_e2 = {kk: b2[kk] for kk in exp_e2}
    ok_e2 = all((act_e2[kk] == v) if isinstance(v, int) else _close(act_e2[kk], v)
                for kk, v in exp_e2.items())
    results["e2_giveback_post_forced_flat_non_firing"] = {
        "description": ("a giveback condition first appearing on a bar with et >= session_end-1 is "
                        "preempted by the engine's forced flat: non-firing, valued at v_forced_flat, "
                        "delta 0, hold-like"),
        "expected": exp_e2, "actual": act_e2, "passed": bool(ok_e2)}

    # -- (e3) giveback genuine firing whose exit bar IS the session_end bar --------
    # tape gaps: no bar at et 573, so session_end = 574 and the condition first appears at 572
    # (= session_end - 2, executable); its exit is the next printed bar -- the session_end bar
    # itself, whose open is the forced-flat price.
    bars3 = [(570, 10.0, 10.5, 9.9, 10.4), (571, 10.4, 10.6, 10.2, 10.5),
             (572, 10.5, 10.7, 9.4, 9.5),              # close 9.5 <= 0.9*running_high -> fires
             (574, 9.5, 9.6, 9.4, 9.5)]                # session_end bar: exit = its open (9.5)
    fx3 = synth_frame([{"sleeve_day": "2021-04-02", "ticker": "GAPFIRED", "family": "A_pm",
                        "entry_rank": 1, "entry_px": 10.0, "bars": bars3}])
    led_e3 = run_ledger(fx3, parse_rule("giveback:10", reg), 100.0, with_crosscheck=False)
    b3 = led_e3["blocks"]["block1"]
    exp_e3 = {"n_members": 1, "n_early_exits": 1, "n_hold_like": 0, "n_giants_cut": 0,
              "failure_tax_avoided": 0.0, "dollars_destroyed": 0.0, "net_dollar_ledger": 0.0}
    act_e3 = {kk: b3[kk] for kk in exp_e3}
    ok_e3 = all((act_e3[kk] == v) if isinstance(v, int) else _close(act_e3[kk], v)
                for kk, v in exp_e3.items())
    results["e3_giveback_fires_into_session_end_bar"] = {
        "description": ("a first condition at et == session_end-2 is executable: the exit is the open "
                        "of the next printed bar (the session_end bar), so the rule is a genuine "
                        "firing worth exactly the forced flat -> delta 0, early exit 1"),
        "expected": exp_e3, "actual": act_e3, "passed": bool(ok_e3)}

    # -- (e4) forced-flat precedence for scan rules --------------------------------
    fx4 = synth_frame([{"sleeve_day": "2021-04-03", "ticker": "LATE", "family": "A_pm",
                        "entry_rank": 1, "entry_px": 10.0,
                        "bars": [(570, 10.0, 10.5, 9.9, 10.4), (571, 10.4, 10.9, 10.3, 10.8),
                                 (572, 10.8, 12.0, 10.7, 11.6), (573, 11.6, 13.0, 11.5, 12.8),
                                 (574, 12.8, 15.0, 12.7, 14.5)]}])
    # et 574 = the session_end bar of this fixture, so the decision is preempted by the forced flat
    led_e4 = run_ledger(fx4, parse_rule("exit_at_et:0934", reg), 100.0, with_crosscheck=False)
    b4 = led_e4["blocks"]["block1"]
    exp_e4 = {"n_members": 1, "n_early_exits": 0, "n_hold_like": 1,
              "n_triggered_after_forced_flat": 1, "failure_tax_avoided": 0.0,
              "dollars_destroyed": 0.0, "net_dollar_ledger": 0.0}
    act_e4 = {kk: b4[kk] for kk in exp_e4}
    ok_e4 = all((act_e4[kk] == v) if isinstance(v, int) else _close(act_e4[kk], v)
                for kk, v in exp_e4.items())
    results["e4_forced_flat_precedence"] = {
        "description": ("a decision at a bar with et >= session_end-1 is preempted by the engine's "
                        "forced flat: delta 0, hold-equivalent, counted"),
        "expected": exp_e4, "actual": act_e4, "passed": bool(ok_e4)}

    # -- (e5) the executable baseline is the session-end bar's OPEN, not the close --
    fx5 = synth_frame([{"sleeve_day": "2021-04-04", "ticker": "OPENvsCLOSE", "family": "A_pm",
                        "entry_rank": 1, "entry_px": 10.0,
                        "bars": [(570, 10.0, 10.0, 9.9, 10.0), (571, 9.0, 9.1, 8.9, 9.0),
                                 (572, 9.5, 12.0, 9.4, 11.5), (573, 11.5, 13.0, 11.4, 12.4)]}])
    led_e5 = run_ledger(fx5, parse_rule("exit_at_bar:0", reg), 100.0, with_crosscheck=False)
    b5 = led_e5["blocks"]["block1"]
    # exit at the open of bar 1 (9.0); executable forced flat = open of the session_end bar (11.5);
    # the close-only reference would be 12.4
    exp_delta = k * ((9.0 / 10.0 - 1.0) - (11.5 / 10.0 - 1.0))
    ref_delta = k * ((9.0 / 10.0 - 1.0) - (12.4 / 10.0 - 1.0))
    exp_e5 = {"net_dollar_ledger": exp_delta, "n_early_exits": 1, "forced_flat_mismatch_n": 0}
    act_e5 = {kk: b5[kk] for kk in exp_e5}
    ok_e5 = (all((act_e5[kk] == v) if isinstance(v, int) else _close(act_e5[kk], v)
                 for kk, v in exp_e5.items())
             and abs(exp_delta - ref_delta) > 1e-6)      # the two baselines really differ here
    results["e5_executable_baseline_is_forced_flat_open"] = {
        "description": ("the baseline is the engine's forced-flat price (the session-end bar's "
                        "OPEN), not the close: delta uses 11.5, the close-only reference would use "
                        "12.4 (and is reported separately, never as the executable baseline)"),
        "expected": {**exp_e5, "delta_from_forced_flat_open": exp_delta,
                     "delta_from_close_reference": ref_delta},
        "actual": {**act_e5, "delta_from_forced_flat_open": b5["net_dollar_ledger"],
                   "delta_from_close_reference": ref_delta},
        "passed": bool(ok_e5)}

    # -- (e6) giveback trigger tolerance matches the producer exactly -------------------
    # running high 10.0 -> 9% below = 9.0; the producer's hit test is
    # `close <= running_high*(1-g) + 1e-9*running_high` = 9.0 + 1e-8.
    for tag, close_px, expect_hit in (("exact", 9.0, True),
                                      ("inside_tol", 9.0 + 5e-9, True),
                                      ("outside_tol", 9.0 + 2e-8, False)):
        bars = [(570, 10.0, 10.0, 9.9, 10.0),
                (571, 10.0, 10.0, 9.9, 10.0),           # running high stays exactly 10.0
                (572, 10.0, 10.0, close_px, close_px),  # the tested close
                (573, close_px, close_px, close_px - 0.1, close_px),
                (574, close_px, close_px, close_px - 0.1, close_px)]
        fx = synth_frame([{"sleeve_day": "2021-05-01", "ticker": f"TOL_{tag}", "family": "A_pm",
                           "entry_rank": 1, "entry_px": 10.0, "bars": bars}])
        led = run_ledger(fx, parse_rule("giveback:10", reg), 100.0, with_crosscheck=True)
        row = led["blocks"]["block1"]
        # the rule is the continuation from the FILL row: read the fixture's field at row 0
        fired_field = bool(fx["giveback_fired_10"][0])
        # the same tape is non-firing when the continuation starts at row 2: the first condition
        # bar (et 573) is at et >= session_end - 1, so the engine's forced flat preempts it
        after_ff_from_row2 = bool(fx["giveback_condition_after_forced_flat_10"][2])
        ledger_fired = row["n_early_exits"] == 1
        results[f"e6_tolerance_{tag}"] = {
            "description": (f"giveback:10 boundary case {tag}: close={close_px!r} against the "
                            f"running high 10.0 -> threshold 9.0 with the producer's relative "
                            f"tolerance 1e-9 (hit test close <= 9.0 + 1e-8)"),
            "expected": {"fixture_fires_from_fill_row": expect_hit, "ledger_fires": expect_hit,
                         "fixture_condition_after_ff_from_row2": expect_hit,
                         "reconciliation_clean": True},
            "actual": {"fixture_fires_from_fill_row": fired_field, "ledger_fires": ledger_fired,
                       "fixture_condition_after_ff_from_row2": after_ff_from_row2,
                       "reconciliation_clean": bool(led["reconciliation_clean"]),
                       "xcheck": led.get("continuation_crosscheck")},
            "passed": bool(fired_field is expect_hit and ledger_fired is expect_hit
                           and after_ff_from_row2 is expect_hit
                           and led["reconciliation_clean"]),
        }

    # -- (e7) duplicate paths: sleeve vs independent-path accounting --------------------
    dup = [{"sleeve_day": "2021-02-17", "ticker": "ISPO", "family": "A_pm", "entry_rank": 1,
            "entry_px": 10.0,
            "bars": [(570, 10.0, 10.0, 9.0, 9.2), (571, 9.2, 9.3, 8.0, 8.2),
                     (572, 8.2, 12.0, 8.2, 11.5), (573, 11.5, 14.0, 11.4, 13.5)]},
           {"sleeve_day": "2021-02-17", "ticker": "ISPO", "family": "B600", "entry_rank": 1,
            "entry_px": 9.2,
            "bars": [(570, 9.2, 9.3, 8.1, 8.3), (571, 8.3, 8.4, 7.2, 7.4),
                     (572, 7.4, 11.0, 7.4, 10.5), (573, 10.5, 13.0, 10.4, 12.5)]}]
    fx7 = synth_frame(dup)
    led_e7 = run_ledger(fx7, parse_rule("exit_at_bar:0", reg), 100.0, with_crosscheck=False)
    b7 = led_e7["blocks"]["block1"]
    views = b7["accounting_views"]
    sleeve, indep, sens = views["sleeve_accounting"], views["independent_path_accounting"], \
        views["priority_sensitivity"]
    exp_e7 = {"sleeve_n_members": 2, "n_shared_paths": 1, "n_members_on_shared_paths": 2}
    act_e7 = {"sleeve_n_members": sleeve["n_members"], "n_shared_paths": sleeve["n_shared_paths"],
              "n_members_on_shared_paths": sleeve["n_members_on_shared_paths"],
              "indep_n_members": indep["n_members"],
              "indep_n_members_dropped": indep["n_members_dropped_by_dedup"],
              "indep_net": indep["net_dollar_ledger"],
              "b600_first_net": sens["net_B600_first"],
              "sleeve_net": sleeve["net_dollar_ledger"]}
    # the kept A_pm sleeve exits at the open of bar 1 (9.2) vs its forced flat (open of bar 3 = 11.5)
    a_pm_delta = k * ((9.2 / 10.0 - 1.0) - (11.5 / 10.0 - 1.0))
    exp_e7["indep_net"] = a_pm_delta
    exp_e7["indep_n_members"] = 1
    exp_e7["indep_n_members_dropped"] = 1
    ok_e7 = (act_e7["sleeve_n_members"] == 2 and act_e7["n_shared_paths"] == 1
             and act_e7["n_members_on_shared_paths"] == 2
             and act_e7["indep_n_members"] == 1 and act_e7["indep_n_members_dropped"] == 1
             and _close(act_e7["indep_net"], a_pm_delta)
             and abs(sens["net_B600_first"] - a_pm_delta) > 1e-9)
    results["e7_duplicate_paths_two_views"] = {
        "description": ("the same (day,ticker) path held by two sleeves: the sleeve view keeps "
                        "both, the independent-path view collapses them by the declared "
                        "deterministic rule (A_pm first), and the priority flip is reported"),
        "expected": exp_e7,
        "actual": {**act_e7, "sleeve_note_is_independent_free":
                   "NOT independent-path inference" in sleeve["note"]},
        "passed": bool(ok_e7 and "NOT independent-path inference" in sleeve["note"]),
    }
    return results


def _cli_guard_probe(reg: Registry) -> dict:
    """Run the CLI on a leaking spec file and on a synthetic panel; prove it exits non-zero."""
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        panel = tdp / "panel.parquet"
        # a column that EXISTS in the parquet but is NOT in the column registry: fail-closed
        synth_frame(_giant_fixture()).with_columns(
            pl.lit(1.0).alias("oracle_alias")).write_parquet(panel)
        out = {}
        for name, column in (("registry_future", "future_member_last_et"),
                             ("unregistered_present", "oracle_alias")):
            spec = tdp / f"{name}.json"
            spec.write_text(json.dumps({"name": name,
                                        "conditions": [{"column": column, "op": "==",
                                                        "value": 1.0}]}))
            proc = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--rule", f"spec:{spec}",
                 "--panel", str(panel), "--no-crosscheck"],
                capture_output=True, text=True)
            err = (proc.stderr or "").strip().splitlines()
            out[name] = {"column": column, "exit_code": proc.returncode,
                         "stderr_tail": err[-2:] if err else [],
                         "outcome_leak_in_stderr": "OutcomeLeakError" in (proc.stderr or "")}
        return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cmd_run(args, reg: Registry) -> int:
    rule = parse_rule(args.rule, reg)
    panel = Path(args.panel)
    if not panel.exists():
        print(f"panel absent: {panel} (tool ready; nothing to score)", file=sys.stderr)
        return 3
    df = load_panel(panel, rule)
    ledger = run_ledger(df, rule, args.bps, with_crosscheck=not args.no_crosscheck)
    ledger["panel"] = str(panel)
    ledger["panel_rows"] = df.height
    ledger["guard_source"] = reg.source
    if args.json:
        _write_json(Path(args.json), ledger)
    _print_ledger(ledger)
    return 0


def _print_ledger(ledger: dict) -> None:
    print(f"rule={ledger['rule']['cli']}  bps_total={ledger['friction_bps_total']}  "
          f"members={ledger['n_members_judged']} judged / "
          f"{ledger['n_members_unresolved_censored']} censored-unresolved")
    print(f"{'block':>8} {'n':>6} {'early':>6} {'avoided':>11} {'destroyed':>11} {'net':>11} "
          f"{'giant$100':>10} {'#g100':>5} {'#g300':>5} {'#reentry':>8} {'cens':>5}")
    for block, b in ledger["blocks"].items():
        print(f"{block:>8} {b['n_members']:>6} {b['n_early_exits']:>6} "
              f"{b['failure_tax_avoided']:>11.4f} {b['dollars_destroyed']:>11.4f} "
              f"{b['net_dollar_ledger']:>11.4f} {b['giant_dollars_destroyed_100']:>10.4f} "
              f"{b['n_giants_cut']:>5} {b['n_giants_cut_300']:>5} "
              f"{b['n_reentry_candidates']:>8} {b['n_members_unresolved_censored']:>5}")
    cc = ledger.get("continuation_crosscheck")
    if cc:
        print(f"reconcile vs {cc['column']}: n={cc['n_compared']} "
              f"(skipped {cc.get('n_skipped', 0)}) "
              f"mean|diff|={cc['mean_abs_diff']:.3e} max|diff|={cc['max_abs_diff']:.3e} "
              f"within 1e-12: {cc['n_within_1e-12']}/{cc['n_compared']}")
    for fam, f in ledger.get("by_family_total", {}).items():
        print(f"  family {fam:>5}: n={f['n_members']:>5} censored={f['n_members_unresolved_censored']:>4} "
              f"early={f['n_early_exits']:>5} avoided={f['failure_tax_avoided']:>10.4f} "
              f"destroyed={f['dollars_destroyed']:>10.4f} net={f['net_dollar_ledger']:>10.4f}")
    if not ledger.get("reconciliation_clean", True):
        print(f"  WARNING: reconciliation drift in {ledger['reconciliation_drift_n']} member rows")


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
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _smoke_dry_run(smoke: Path, reg: Registry, bps: float) -> dict:
    """Provisional end-to-end run against the panel producer's *smoke* panel, if it exists."""
    if not smoke.exists():
        return {"status": "absent", "panel": str(smoke)}
    out = {"status": "present", "panel": str(smoke), "panel_sha256": _sha256(smoke),
           "note": "producer smoke panel, NOT the deliverable panel.parquet"}
    for spec in ("hold_flat", "giveback:10"):
        try:
            rule = parse_rule(spec, reg)
            df = load_panel(smoke, rule)
            led = run_ledger(df, rule, bps)
            led["panel_rows"] = df.height
            out[spec] = led
        except Exception as exc:                          # noqa: BLE001 - reported, not swallowed
            out[spec] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


V1_REFERENCE = {
    "note": ("v1 panel (sha 8ce46159…) numbers from the same ledger pre-migration, kept so the "
             "change is visible. v1 used the close-only baseline (v_hold_flat), scanned giveback "
             "from the fill bar, and scored terminal-censored members as if their tape ended at "
             "the session close."),
    "giveback:10": {"block1": {"failure_tax_avoided": 287.7281, "dollars_destroyed": 209.5338,
                               "net_dollar_ledger": 78.1942, "n_giants_cut": 70,
                               "n_reentry_candidates": 1471, "n_members": 4218},
                    "block2": {"failure_tax_avoided": 173.1803, "dollars_destroyed": 140.2783,
                               "net_dollar_ledger": 32.9019, "n_giants_cut": 66,
                               "n_reentry_candidates": 919, "n_members": 1942}},
    "hold_flat": {"block1": {"net_dollar_ledger": 0.0, "n_members": 4218},
                  "block2": {"net_dollar_ledger": 0.0, "n_members": 1942}},
}


def cmd_self_test(args, reg: Registry) -> int:
    tests = self_test(reg)
    payload = {
        "tool": "factory/scripts/basket_atlas_ledger.py",
        "tool_version": "v2 (panel schema_version 2)",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "worktree": str(ROOT),
        "guard_source": reg.source,
        "guard": {"blocked_prefixes": list(reg.blocked_prefixes),
                  "causally_excluded_families": list(reg.excluded_families),
                  "future_only_columns": list(reg.future_only),
                  "allowed_state_columns": list(reg.allowed_state_columns())},
        "friction": {"bps_total_default": 100.0, "side": 0.005, "k_two_sided": fric_k(100.0),
                     "convention": ("per side side=bps_total/2/10000; committed dollar buys "
                                    "1/(1+side) exposure; exit returns (1+gross)*(1-side); the "
                                    "entry charge is identical on both legs so "
                                    "delta = k*(gross_rule - gross_hold), k=(1-side)/(1+side)")},
        "ledger_definition": {
            "delta_member": ("(rule return, entry_px -> the rule's executable exit, net of friction) "
                             "- (executable hold-to-flat return, entry_px -> the engine's forced "
                             "flat, net of friction); both measured from entry_px and priced as "
                             "(1+gross)*k - 1, k=(1-side)/(1+side)"),
            "failure_tax_avoided": "sum of positive delta only",
            "dollars_destroyed": "-sum of negative delta only (reported positive)",
            "net_dollar_ledger": "sum of all delta = avoided - destroyed",
            "giant_dollars_destroyed_100/_300": (
                "the subset of dollars_destroyed for members whose forward MFE from the exit point "
                "(max(bar_high[exit_bar:])/exit_px - 1, reconciled against the panel's remaining_run) "
                "reached +100% / +300%"),
            "giants_cut": ("that subset with day, ticker, exit et, exit price, forward peak after "
                           "exit, forward peak from entry, readmissible"),
            "giants_cut_fields": {
                "day": "sleeve_day (the panel's session-date column)",
                "exit_et": "ET minute-of-day of the exit bar",
                "exit_px": "the executed exit price (the exit bar's open)",
                "forward_peak_after_exit": "forward_peak_after_exit_px = exit_px*(1+forward_mfe_from_exit)",
                "forward_peak_from_entry": "forward_peak_from_entry_pct / _px",
                "readmissible": "exited early and traded >= +10% above exit_px afterwards",
                "oracle_forward_mfe_ratio": ("the raw post-exit MFE ratio (dimensionless). An ORACLE "
                                             "diagnostic of the tail left behind: not a committed-dollar "
                                             "payoff, never subtracted from any ledger total"),
            },
            "n_reentry_candidates": "count of members with readmissible true (any delta sign)",
            "units": "return units per committed dollar; one equal-weight committed dollar per member",
            "baseline": ("EXECUTABLE: the engine's forced flat, `future_forced_flat_px` (the open of "
                         "the session_end bar) / entry_px - 1. `v_hold_flat` (the session-close "
                         "baseline) is a labelled reference ONLY and is never the executable "
                         "baseline: see `close_reference` in each block."),
            "censoring": ("terminal-censored members have no terminal value and no executable "
                          "liquidation. They are excluded from every executable aggregate and "
                          "reported separately as `unresolved_censored` (counts plus the executable "
                          "rule-side dollars). Nulls are never read as zero or as the last print."),
            "forced_flat_precedence": ("no release decision at a bar with et >= session_end - 1 is "
                                       "evaluated (the engine schedules FORCED_FLAT there): such a "
                                       "decision is non-firing and the position rides to the forced "
                                       "flat, delta = 0"),
            "execution": ("decision on completed bar t executes at the open of the next printed bar "
                          "(the panel's `next_open`); giveback rules bind to the panel's declared "
                          "`v_giveback_G` at the fill row"),
            "giveback_trigger_tolerance": ("the producer's exact hit test, "
                                           "`close <= running_high*(1-g) + 1e-9*running_high` "
                                           "(GIVEBACK_TOL_REL); boundary self-tests e6 pin it"),
            "accounting_views": {
                "sleeve_accounting": ("one row per filled sleeve member (day, family, entry_rank, "
                                      "ticker). A (day,ticker) market path can be held by more than "
                                      "one sleeve, so these totals are NOT independent-path "
                                      "inference and are labelled as such"),
                "independent_path_accounting": ("one row per (day,ticker) path, duplicates collapsed "
                                                "by a declared deterministic rule (A_pm over B600, "
                                                "then lower entry_rank, then family name)"),
                "priority_sensitivity": ("both priority orders are reported; a non-trivial delta "
                                         "means the sleeve total is sensitive to which duplicate "
                                         "is kept"),
                "duplicate_census": ("`n_shared_paths_all_members` is the path universe (255 on the "
                                     "v2 panel); `n_shared_paths` is the judged subset"),
            },
            "unresolved_censored": ("terminal-censored members carry no delta (the forced-flat "
                                    "baseline does not exist). The nested `observed_exits` "
                                    "partition reports rule exits observed on the causal tape "
                                    "(trigger + executable next open) as observations only, never "
                                    "combined with a fabricated baseline and never in the ledger "
                                    "totals"),
        },
        "self_tests": tests,
        "ambiguities_resolved": [
            ("giant_dollars_destroyed_* is the subset of the destroyed amount (|delta|) restricted "
             "to members whose forward MFE from the exit reached the threshold (Main's "
             "clarification). Two clearly-labelled ORACLE diagnostics accompany it and are never "
             "part of any ledger total: `oracle_forward_mfe_ratio_sum_100/_300` (sum of the raw "
             "post-exit MFE ratios, dimensionless) and `oracle_entry_scaled_forward_mfe_100/_300` "
             "((peak after exit - exit_px)/entry_px, i.e. committed dollars at entry). Neither is "
             "executable: they assume the peak is capturable and carry no next-open, friction or "
             "re-entry semantics."),
            ("giveback:G is bound to the panel's declared v2 continuation (`v_giveback_G` at the "
             "fill row), not recomputed: the panel's continuation scans from bar t+1, so a condition "
             "on the fill bar itself is not part of the rule (159 members in the v2 panel have one). "
             "The ledger's locator only identifies the exit BAR for the forward-tail measurement and "
             "is reconciled against the panel's implied exit price for every member."),
            ("a giveback condition first appearing at et >= session_end - 1 is non-firing and valued "
             "at v_forced_flat (3 members in the v2 panel carry that flag); the same forced-flat "
             "precedence is applied to exit_at_bar / exit_at_et / spec rules."),
            ("the ledger's exit tail is measured from the exit bar's open over bars up to the "
             "member's own tape end (max(bar_high[exit_bar:])/exit_px - 1); it is reconciled "
             "against the panel's `remaining_run` at the trigger row to machine precision."),
            ("`session_end` (a causal day-level key) is blocked by the `session_` prefix rule: the "
             "guard over-blocks rather than under-blocks, and a rule that needs the session clock "
             "can express it through `et`."),
            ("spec comparators are exact IEEE comparisons on the panel's computed values (no "
             "epsilon); specs should not sit exactly on a computed boundary."),
        ],
        "changed_vs_v1": V1_REFERENCE,
    }

    panel = Path(args.panel)
    smoke = panel.parent / "smoke" / "panel.parquet"
    if panel.exists():
        real, sha = {}, _sha256(panel)
        for spec in ("hold_flat", "giveback:10"):
            try:
                rule = parse_rule(spec, reg)
                df = load_panel(panel, rule)
                led = run_ledger(df, rule, args.bps, with_crosscheck=not args.no_crosscheck)
                led["panel"] = str(panel)
                led["panel_rows"] = df.height
                real[spec] = led
            except Exception as exc:                     # noqa: BLE001 - reported, not swallowed
                real[spec] = {"error": f"{type(exc).__name__}: {exc}"}
        payload["real_panel"] = {
            "status": "present", "panel": str(panel), "panel_sha256": sha,
            "panel_sha_expected": PANEL_SHA_EXPECTED,
            "panel_sha_match": bool(sha == PANEL_SHA_EXPECTED),
            "panel_bytes": panel.stat().st_size, "blocks": real}
    else:
        payload["real_panel"] = {"status": "absent", "panel": str(panel),
                                 "note": "panel.parquet absent; tool ready"}
        payload["dry_run_smoke_panel"] = _smoke_dry_run(smoke, reg, args.bps)

    payload["peak_rss_mb"] = _peak_rss_mb()
    payload["reproduce_commands"] = [
        ".venv/bin/python factory/scripts/basket_atlas_ledger.py --self-test",
        ".venv/bin/python factory/scripts/basket_atlas_ledger.py --rule hold_flat",
        ".venv/bin/python factory/scripts/basket_atlas_ledger.py --rule giveback:10",
        ".venv/bin/python factory/scripts/basket_atlas_ledger.py --rule exit_at_bar:30 "
        "--json /tmp/ledger_exit30.json",
        ".venv/bin/python factory/scripts/basket_atlas_ledger.py --rule exit_at_et:1130",
        "# guard (must exit 2): spec reading a future/outcome column",
        "echo '{\"conditions\":[{\"column\":\"future_member_last_et\",\"op\":\"==\",\"value\":1}]}' "
        "> /tmp/leak.json && .venv/bin/python factory/scripts/basket_atlas_ledger.py "
        "--rule spec:/tmp/leak.json",
    ]
    payload["self_test_summary"] = {
        "n_passed": sum(1 for t in tests.values() if t["passed"]), "n_tests": len(tests),
        "all_passed": all(t["passed"] for t in tests.values())}
    out = Path(args.json or ARTIFACT)
    _write_json(out, payload)
    print(f"self-tests: {payload['self_test_summary']['n_passed']}/"
          f"{payload['self_test_summary']['n_tests']} passed -> {out}  "
          f"(peak rss {payload['peak_rss_mb']:.0f} MB)")
    for name, t in tests.items():
        print(f"  [{'PASS' if t['passed'] else 'FAIL'}] {name}")
    rp = payload["real_panel"]
    if rp["status"] == "present":
        print(f"  panel sha256 {rp['panel_sha256'][:16]}… expected-match={rp['panel_sha_match']}")
        for spec, led in rp["blocks"].items():
            if "error" in led:
                print(f"  real panel {spec}: ERROR {led['error']}")
            else:
                net = sum(b["net_dollar_ledger"] for b in led["blocks"].values())
                cens = led["n_members_unresolved_censored"]
                print(f"  real panel {spec}: judged={led['n_members_judged']} "
                      f"censored-unresolved={cens} net={net:.4f}")
    else:
        print(f"  real panel: ABSENT ({panel})")
    return 0 if payload["self_test_summary"]["all_passed"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rule", help="hold_flat | exit_at_bar:N | exit_at_et:HHMM | giveback:G | "
                                   "spec:<path.json>")
    ap.add_argument("--panel", default=str(DEFAULT_PANEL))
    ap.add_argument("--bps", type=float, default=100.0, help="total round-trip bps (per side /2)")
    ap.add_argument("--json", default=None, help="write the ledger JSON here")
    ap.add_argument("--no-crosscheck", action="store_true",
                    help="skip the v2 continuation reconciliation")
    ap.add_argument("--self-test", action="store_true",
                    help=f"run the acceptance tests and write {ARTIFACT.name}")
    args = ap.parse_args(argv)
    reg = load_registry()
    try:
        if args.self_test:
            return cmd_self_test(args, reg)
        if not args.rule:
            ap.error("--rule is required (or --self-test)")
        return cmd_run(args, reg)
    except OutcomeLeakError as exc:
        print(f"OutcomeLeakError: {exc}", file=sys.stderr)
        return 2
    except (PanelSchemaError, RuleSpecError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
