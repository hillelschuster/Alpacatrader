#!/usr/bin/env python
"""Freeze-R RACE geometry: per-tier cross-sectional distribution/flow extraction and exact
nearest-100 retrieval (PLAN-TAPE-ATLAS.md sections 2, 4, 8, 10; Stage 2).

Scope
-----
Stage-2 consumer of a genuinely FROZEN Freeze-R record.  It builds THREE INDEPENDENT
geometry nodes, one per race tier, with their own output root, evidence directory, manifest,
payload hashes and coverage classes.  Their evidence is never merged and cash/exposure/actions
never enter:

  ``broad_minute``    <- ``race_minute_full``    (full raw/provider broad minute board)
  ``candidate_net``   <- ``race_candidate_net``  (candidate-net prospective/retrospective board)
  ``checkpoint``      <- ``race_checkpoint_full`` (twelve-point full-roster morning ruler)

For each tier it

  * streams the day payload with an explicit column projection (two passes: the row
    selectability pass reads ONLY keys + population/quality predicate columns, and the value
    columns are read in a second pass restricted to the rows that passed.  No value of an
    unselected row is ever used);
  * derives a joint CROSS-SECTIONAL DISTRIBUTION/FLOW sequence: for every decision clock of the
    day it aggregates each declared channel over the WHOLE declared population on the declared
    RULER (quantile/mean/max/min position), then adds the declared clock FIRST-DIFFERENCE flow
    features.  The population is never truncated to a top-K: the ruler summarizes the full
    eligible cross-section;
  * keeps only contiguous clock runs (a hole, an early close or a missing checkpoint splits the
    window set instead of being silently bridged), and emits one object per (day, declared scale,
    anchor) with its own day / clock-interval / scale / raw-lane refs;
  * performs EXACT masked pairwise retrieval (no ANN index) with the shared bounded-memory
    helpers of ``basket_tape_atlas_geometry`` (``view_gram`` / ``masked_sq_dists`` /
    ``search_view`` / ``distance_scale`` / ``topk_from_dist``), including the balanced
    equal-weight view over the tier's primitive views;
  * publishes nearest-100 neighbours with each neighbour's own day/clock refs, density,
    k-th distance, mutual-neighbour recurrence, independent-day spread, low-density /
    uncertain / unique mass, within-tier magnitude-vs-shape disagreement and matched-surrogate
    evidence;
  * labels every object ``quality_qualified`` or ``raw_reference_only`` (stale / floor-qualified
    reference day) and NEVER drops an object because of it, because of distance, radius or
    absence of neighbours.  Giant tails/unique objects stay catalogue rows with their own refs;
  * publishes the tier support cell table: a cell this node does not materialize
    (``quotes``, ``learned_minute_sequence``) is an explicit ``unsupported``/``delegated``
    declaration, never a zero and never a market value.

No outcome, censor, future, ticket-constant, membership-rank, quote or anatomy column is read as
a distance channel; no outcome module is imported; no human label, taxonomy, P&L or supervised
target is produced.  Every number written is a measurement of the observed tape under the frozen
record, and no gate is evaluated and no alpha claim is made.

Refusals
--------
``ContractRefusalError`` (exit 2, nothing written): the Freeze-R record is a template/draft, any
required ``race_geometry`` field is missing or UNBOUND (null), ``neighbours_k`` is not the
published 100, a clock/coordinate/membership/quality/quote/provenance column or a
retrospective-only column is named as a distance channel, a channel is not supportable by that
tier, a view is not one of the five hand views, the balanced aggregation/equal weights are
decorated or tuned, the adaptive-choice ledger binding or the corpus/blind-report hash chain does
not match, or the estimated footprint exceeds the declared memory budget.

``IntegrityRefusalError`` (exit 3, REFUSED manifest/report written): a declared payload is
absent, its physical schema lacks a column the frozen record binds, its row count or sha256
mismatches the manifest, the race payload day set disagrees with the corpus day set, a day
carries more than one raw-lane sha, or a fit/eval block is empty.

Frozen-R input contract (exact required fields)
-----------------------------------------------
The base record is validated by ``basket_tape_atlas_geometry.load_freeze_r`` (unchanged) and the
race part by this module.  ``race_geometry`` is a NEW top-level section of the same record; every
key below is REQUIRED and every numeric/scale/ruler choice is null (UNBOUND) until the
outcome-blind corpus report exists::

    {"race_geometry": {
      "race_matrix_version": str (non-empty, no DRAFT/TEMPLATE/NOT-RUN marker),
      "frozen": true,
      "tiers": {"broad_minute"|"candidate_net"|"checkpoint": {
          "table": str,                       # must equal the tier's physical race table
          "keys": [str, ...],                 # must equal the frozen schema grain
          "clock_axis": {"column": str, "unit": "minute", "frozen": true,
                         "step": 1, "span": [lo, hi]}            # minute tiers
                       | {"column": str, "unit": "minute", "frozen": true,
                          "axis_values": [int, ...]},            # checkpoint tier
          "population": {"label": str, "predicates": [predicate, ...], "frozen": true},
          "quality": {"qualified_label": str, "reference_label": str,
                      "predicates": [predicate, ...], "frozen": true},
          "scale": {"unit": "minute"|"checkpoint", "ladder": [int, ...],
                    "anchor": "window_end_at_query", "alignment": "right",
                    "anchor_stride": int, "frozen": true},
          "flow": {"rule": "previous_axis_position_else_invalid", "order": 1, "frozen": true},
          "cells": {"magnitude_shape_duration": cell, "activity_prints": cell,
                    "quotes": cell, "learned_minute_sequence": cell},
          "surrogate": {"matched_axes": [str, ...], "clock_bucket_size": int,
                        "draws": int, "density_decile_definition": str, "frozen": true}}},
      "views": {tier: {view_name: {
          "features": [feature, ...],
          "metric": "masked_euclidean_zscore_fitblock",
          "metric_weights": {feature_id: float >= 0},
          "scale_reference": {"statistic": "median_fit_kth_neighbour_distance", "k": int},
          "radius": float > 0, "low_density_max": int >= 0,
          "uncertain_kth_dist_min": float >= 0}},
          tier: {"balanced_multichannel_reference": {"aggregation":
                    "equal_weight_mean_standardized_view_distances",
                    "min_common_views": int, "radius": float > 0,
                    "low_density_max": int >= 0, "uncertain_kth_dist_min": float >= 0,
                    "balanced_reference_tuned_to_best_geometry": false}}},
      "view_weights": {tier: {primitive_view: equal float > 0, ...}},
      "neighbours_k": 100,
      "density_decile_definition": "exact_deciles_of_object_population_size",
      "ann_sparse_recall": {"index_build": "exact_bruteforce_no_ann",
                            "exact_search_strata": "all",
                            "sparse_exact_recall_floor": 1.0, "frozen": true},
      "independence": {"unit": "day", "exclude_same_unit": true, "frozen": true},
      "adaptive_choice_ledger": {"path": str, "sha256": hex64, "covers_race_choices": true},
      "geometry_gates": {<echoed verbatim, never evaluated here>}}}

``predicate`` = ``{"column": str, "op": "eq"|"neq"|"gt"|"ge"|"lt"|"le"|"is_null"|"is_not_null",
"value": any}`` (``value`` absent for the two null ops).

``feature`` = ``{"id": str, "kind": "level"|"flow", "of": str|null, "column": str|null,
"aggregate": "quantile"|"mean"|"max"|"min"|null, "position": float|null,
"transform": "identity"|"log1p"|"log_level"|"log_ratio_to_reference",
"reference": null|{"of": str, "rule": "cross_section_median_at_same_t"|
"cross_section_mean_at_same_t"}, "valid_when": predicate|null}``.

A ``level`` feature aggregates ``column`` over the population on the ruler; ``quantile``
requires ``position`` in (0,1].  A ``flow`` feature carries ``of`` (a level feature id in the
same view) and is its first difference along the clock axis (``flow.rule``).  A channel is
readable only when the frozen observation schema declares namespace
``tape|print|state_price|race`` with ``coordinate_only=false``, ``retrospective_only=false``,
``prospective_allowed=true`` and this tier in ``supportable_by_tier``; ``valid_when`` may only
name positively eligible prospective columns, so a whole-day field can never gate a prospective
value.

Masked distance (the only supported metric, reused from the within-name node)::

    D_ij = sum_c sum_{t : valid_ict and valid_jct} (z_ict - z_jct)**2

z is the fit-block standard score scaled by ``sqrt(weight)``.  An entry that is invalid is
EXCLUDED, never imputed: a pair sharing no commonly valid entry has an undefined (infinite)
distance and can never be a neighbour.  Objects sharing the independence unit (the same day) are
excluded from each other's neighbour sets, and every neighbour is reported with its own
day/clock interval.

CLI
---
  .venv/bin/python factory/scripts/basket_tape_atlas_race_geometry.py --selftest
  .venv/bin/python factory/scripts/basket_tape_atlas_race_geometry.py --print-config-skeleton
  .venv/bin/python factory/scripts/basket_tape_atlas_race_geometry.py \\
      --manifest factory/artifacts/.../OBSERVATION/v0/canary/manifest.json --audit-physical
  .venv/bin/python factory/scripts/basket_tape_atlas_race_geometry.py \\
      --root /home/hillel/projects/Alpacatrader/data/atlas/observation/v0 \\
      --manifest <corpus manifest.json> --freeze-r <frozen R record.json> --all-tiers \\
      --out /home/hillel/projects/Alpacatrader/data/atlas/geometry/v0/race \\
      --out-evidence factory/artifacts/basket/phase2/ATLAS/TAPE/GEOMETRY/v0/race

Exit codes: 0 complete, 2 contract refusal (nothing written), 3 integrity refusal (REFUSED
manifest/report written), 1 selftest failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import resource
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
TAPE = ROOT / "factory/artifacts/basket/phase2/ATLAS/TAPE"
OBS_EVIDENCE = TAPE / "OBSERVATION/v0"
SCHEMA_PATH = OBS_EVIDENCE / "schema.json"
REGISTRY_PATH = OBS_EVIDENCE / "causal_registry.json"
LOCK_PATH = OBS_EVIDENCE / "contract_lock.json"
OBS_CONTRACT_PATH = OBS_EVIDENCE / "contract.json"
CANARY_PATH = OBS_EVIDENCE / "canary_days.json"
DEFAULT_ROOT = Path("/home/hillel/projects/Alpacatrader/data/atlas/observation/v0")
DEFAULT_OUT_BASE = Path("/home/hillel/projects/Alpacatrader/data/atlas/geometry/v0/race")
DEFAULT_EVIDENCE_BASE = TAPE / "GEOMETRY/v0/race"
MANIFEST_NAME = "race_geometry_manifest.json"
REPORT_NAME = "race_geometry_report.json"

HEX64 = re.compile(r"[0-9a-f]{64}")
SAFE_ID = re.compile(r"[A-Za-z0-9_.:@\-]{1,64}")
BLOCK1_MAX_DAY = "2023-12-31"  # observation contract.json day_guard.block_gap_rule

# --------------------------------------------------------------------------- #
# tier registry (physical facts verified against the built corpus)
# --------------------------------------------------------------------------- #
TIER_ORDER = ("broad_minute", "candidate_net", "checkpoint")
TIER_TABLE = {
    "broad_minute": "race_minute_full",
    "candidate_net": "race_candidate_net",
    "checkpoint": "race_checkpoint_full",
}
TIER_PHYSICAL_DIR = {
    "broad_minute": "race.minute_full",
    "candidate_net": "race.candidate_net",
    "checkpoint": "race.checkpoint_full",
}
TIER_KEYS = {
    "broad_minute": ("day", "t", "ticker"),
    "candidate_net": ("day", "t", "ticker"),
    "checkpoint": ("day", "T", "symbol"),
}
TIER_CLOCK = {"broad_minute": "t", "candidate_net": "t", "checkpoint": "T"}
TIER_CLOCK_KIND = {"broad_minute": "minute", "candidate_net": "minute", "checkpoint": "checkpoint"}
TIER_NODE = {
    "broad_minute": "geometry.tape_atlas.race_broad_minute",
    "candidate_net": "geometry.tape_atlas.race_candidate_net",
    "checkpoint": "geometry.tape_atlas.race_checkpoint",
}
# tier_support_matrix key in the frozen observation contract
TIER_CONTRACT_KEY = {
    "broad_minute": "broad_provider_minute",
    "candidate_net": "candidate_net_minute",
    "checkpoint": "full_checkpoint",
}
RACE_TABLE_TO_TIER = {v: k for k, v in TIER_TABLE.items()}

# --------------------------------------------------------------------------- #
# view vocabulary (PLAN section 8: exactly five hand views + one learned family)
# --------------------------------------------------------------------------- #
MAGNITUDE = "magnitude_dominant"
SHAPE = "normalized_shape_dominant"
DURATION = "duration_event_dominant"
ACTIVITY = "activity_microstructure_dominant"
BALANCED = "balanced_multichannel_reference"
PRIMITIVE_VIEWS = (MAGNITUDE, SHAPE, DURATION, ACTIVITY)
HAND_VIEWS = PRIMITIVE_VIEWS + (BALANCED,)
LEARNED = "learned_sequence"  # produced by the sequence-archive sibling, never by this node

METRIC = "masked_euclidean_zscore_fitblock"
SCALE_STATISTIC = "median_fit_kth_neighbour_distance"
BALANCED_AGG = "fixed equal weight across standardized view distances"
BALANCED_DECL = "equal_weight_mean_standardized_view_distances"
FLOW_RULE = "previous_axis_position_else_invalid"
ANCHOR = "window_end_at_query"
ALIGNMENT = "right"
DECILE_DEF = "exact_deciles_of_object_population_size"
ANN_INDEX_BUILD = "exact_bruteforce_no_ann"
ANN_STRATA = "all"
ANN_RECALL_FLOOR = 1.0
PUBLISHED_K = 100  # the race geometry publishes nearest-100 retrieval

TRANSFORMS = ("identity", "log1p", "log_level", "log_ratio_to_reference")
AGGREGATES = ("quantile", "mean", "max", "min")
REFERENCE_RULES = ("cross_section_median_at_same_t", "cross_section_mean_at_same_t")
OPS = ("eq", "neq", "gt", "ge", "lt", "le", "is_null", "is_not_null")
CELLS = ("magnitude_shape_duration", "activity_prints", "quotes", "learned_minute_sequence")
CELL_STATUS = ("materialized", "delegated", "unsupported")
SURROGATE_AXES = ("block", "clock_bucket", "population_decile")

# a distance channel may only come from these frozen-schema namespaces
DISTANCE_NAMESPACES = ("tape", "print", "state_price", "race")
# schema.json#field_attributes.coordinate_only_rule + representation_matrix_template#coordinate_deny
COORDINATE_DENY = frozenset(
    {
        "month", "block", "feed_era", "dow", "et", "et_min", "minute_index", "coverage_class",
        "family", "entry_rank", "entry_et", "day", "ticker", "symbol", "t", "T", "path_id",
        "member_id", "session_end", "within_session", "within_observed_span", "canary_member",
        "in_projection_window", "source_row_ordinal", "trade_id",
    }
)
QUOTE_DENY = ("quote", "bid_", "ask_", "spread_", "nbbo")
# PLAN 4.4: winners_open / winners_prev are excluded by construction and counted, never a roster
FUTURE_ROSTER_DENY = ("in_winners_open", "in_winners_prev")
# the frozen observation schema declares these as ex-ante calendar fields; the registry's
# `session_` prefix targets session_peak_*/session_close_* panel columns, not the session end
PREDICATE_CALENDAR_EXCEPTIONS = frozenset({"session_end"})
MEMORY_HARD_GIB = 30.0

try:  # the within-name node owns the exact-NN helpers, the corpus chain and the atomic writers
    from factory.scripts import basket_tape_atlas_geometry as geo
except ImportError:  # direct script execution
    sys.path.insert(0, str(ROOT))
    from factory.scripts import basket_tape_atlas_geometry as geo

REQUIRED_HELPERS = (
    "ContractRefusalError", "IntegrityRefusalError", "sha256_file", "sha256_bytes", "load_json",
    "canon_bytes", "write_json", "write_parquet", "deterministic_npz", "resolve_under",
    "block_of", "day_set_sha", "quantiles", "spearman", "masked_sq_dists", "topk_from_dist",
    "search_view", "distance_scale", "view_gram", "load_freeze_r", "load_corpus",
)
_missing_helpers = [name for name in REQUIRED_HELPERS if not hasattr(geo, name)]
if _missing_helpers:  # pragma: no cover - a broken sibling import is a hard stop
    raise RuntimeError(
        "basket_tape_atlas_geometry is missing the shared helper(s) this node reuses: "
        f"{_missing_helpers}"
    )

ContractRefusalError = geo.ContractRefusalError
IntegrityRefusalError = geo.IntegrityRefusalError


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def die(message: str, code: int = 2) -> int:
    print(message, file=sys.stderr)
    return code


def _req(obj, key, types, where: str):
    if not isinstance(obj, dict) or key not in obj:
        raise ContractRefusalError(f"{where}.{key} is missing - Freeze R is not bound")
    val = obj[key]
    if val is None:
        raise ContractRefusalError(f"{where}.{key} is UNBOUND (null) - Freeze R is not frozen")
    if isinstance(val, bool) and bool not in ((types,) if not isinstance(types, tuple) else types):
        raise ContractRefusalError(f"{where}.{key} must be {types}, not a bool")
    if not isinstance(val, types):
        raise ContractRefusalError(
            f"{where}.{key} has type {type(val).__name__}, expected {types}"
        )
    return val


def _req_hex(obj, key, where: str) -> str:
    val = _req(obj, key, str, where)
    if not HEX64.fullmatch(val):
        raise ContractRefusalError(f"{where}.{key} is not a sha256 hex digest: {val!r}")
    return val


def _req_true(obj, key, where: str) -> bool:
    val = _req(obj, key, bool, where)
    if val is not True:
        raise ContractRefusalError(f"{where}.{key} must be true (found {val!r})")
    return val


def _req_int(obj, key, where: str, *, lo=None, hi=None) -> int:
    val = _req(obj, key, int, where)
    if lo is not None and val < lo:
        raise ContractRefusalError(f"{where}.{key}={val} < {lo}")
    if hi is not None and val > hi:
        raise ContractRefusalError(f"{where}.{key}={val} > {hi}")
    return int(val)


def _req_num(obj, key, where: str, *, lo=None, hi=None, positive=False) -> float:
    val = float(_req(obj, key, (int, float), where))
    if not np.isfinite(val):
        raise ContractRefusalError(f"{where}.{key} is not finite")
    if positive and val <= 0:
        raise ContractRefusalError(f"{where}.{key} must be > 0")
    if lo is not None and val < lo:
        raise ContractRefusalError(f"{where}.{key}={val} < {lo}")
    if hi is not None and val > hi:
        raise ContractRefusalError(f"{where}.{key}={val} > {hi}")
    return val


def _opt_list(obj, key, where: str) -> list:
    val = obj.get(key)
    if val is None:
        raise ContractRefusalError(f"{where}.{key} is UNBOUND (null) - Freeze R is not frozen")
    if not isinstance(val, list):
        raise ContractRefusalError(f"{where}.{key} must be a list")
    return val


def splitmix64(*parts) -> int:
    """Deterministic 64-bit mixer: identical draws on every platform/numpy version."""
    h = 0x9E3779B97F4A7C15
    for part in parts:
        token = str(part).encode()
        h ^= int(hashlib.sha256(token).hexdigest()[:16], 16)
        h = (h * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
        h ^= h >> 27
        h = (h * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
        h ^= h >> 31
    return h


def rss_gib() -> float:
    return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / (1024.0 * 1024.0)


# --------------------------------------------------------------------------- #
# predicate / feature validation
# --------------------------------------------------------------------------- #
def _predicate_expr(pred: dict) -> pl.Expr:
    op = pred["op"]
    if op == "is_null":
        return pl.col(pred["column"]).is_null()
    if op == "is_not_null":
        return pl.col(pred["column"]).is_not_null()
    col = pl.col(pred["column"])
    return {
        "eq": col.eq, "neq": col.ne, "gt": col.gt, "ge": col.ge, "lt": col.lt, "le": col.le,
    }[op](pred["value"])


def combine_predicates(preds: list) -> pl.Expr:
    expr = None
    for pred in preds:
        e = _predicate_expr(pred)
        expr = e if expr is None else (expr & e)
    return expr


def _is_denied(name: str, registry: dict, *, is_predicate: bool) -> bool:
    if name in set(registry.get("denied_exact_columns") or ()):
        return True
    for prefix in registry.get("denied_prefixes") or ():
        if name.startswith(prefix):
            if is_predicate and name in PREDICATE_CALENDAR_EXCEPTIONS:
                continue
            return True
    return False


def validate_predicate(
    spec, where: str, *, fields: dict, registry: dict, mode: str, tier: str,
    for_channel_validity: bool,
) -> dict:
    """Validate one declared row predicate against the frozen schema and the causal registry."""
    if not isinstance(spec, dict):
        raise ContractRefusalError(f"{where} must be an object")
    column = _req(spec, "column", str, where)
    if column not in fields:
        raise ContractRefusalError(f"{where}.column {column!r} is not a {tier} table column")
    op = _req(spec, "op", str, where)
    if op not in OPS:
        raise ContractRefusalError(f"{where}.op {op!r} is not one of {OPS}")
    if op in ("is_null", "is_not_null"):
        if "value" in spec:
            raise ContractRefusalError(f"{where}.value is not allowed with op {op!r}")
    else:
        _req(spec, "value", (str, int, float, bool), where)
    if _is_denied(column, registry, is_predicate=True):
        raise ContractRefusalError(
            f"{where}.column {column!r} is denied by the frozen causal registry "
            "(outcome/censor/future/quote vocabulary)"
        )
    fa = fields[column]
    if column in FUTURE_ROSTER_DENY:
        raise ContractRefusalError(
            f"{where}.column {column!r}: the future-selected winner lists are excluded by "
            "construction (PLAN 4.4) and never a race population"
        )
    if for_channel_validity:
        if fa["retrospective_only"] or not fa["prospective_allowed"]:
            raise ContractRefusalError(
                f"{where}.column {column!r}: a channel's validity predicate must be positively "
                "prospective; a whole-day/censor column can never gate a prospective value"
            )
        if fa["coordinate_only"]:
            raise ContractRefusalError(
                f"{where}.column {column!r}: a clock/coordinate column is not a validity gate"
            )
    else:
        if fa["retrospective_only"] and mode != "retrospective":
            raise ContractRefusalError(
                f"{where}.column {column!r} is retrospective_only; it may gate a population or "
                "label only in a retrospective read, never a prospective one"
            )
        if fa["coordinate_only"] and column != fields and fa["namespace"] == "key":
            pass
    return {"column": column, "op": op, "value": spec.get("value")}


def validate_feature(
    spec, where: str, *, tier: str, fields: dict, registry: dict, mode: str
) -> dict:
    """Validate one declared cross-sectional feature of one race tier."""
    if not isinstance(spec, dict):
        raise ContractRefusalError(f"{where} must be an object")
    fid = _req(spec, "id", str, where)
    if not SAFE_ID.fullmatch(fid):
        raise ContractRefusalError(f"{where}.id is not a safe identifier: {fid!r}")
    kind = _req(spec, "kind", str, where)
    if kind not in ("level", "flow"):
        raise ContractRefusalError(f"{where}.kind {kind!r} is not one of ('level', 'flow')")
    vw = spec.get("valid_when")
    if kind == "flow":
        for banned in ("column", "aggregate", "position", "transform", "reference"):
            if spec.get(banned) is not None:
                raise ContractRefusalError(
                    f"{where}: a flow feature derives from its `of` level feature and declares "
                    f"no {banned}"
                )
        if vw is not None:
            raise ContractRefusalError(
                f"{where}: a flow feature inherits validity from its `of` feature"
            )
        return {
            "id": fid, "kind": "flow", "of": _req(spec, "of", str, where), "column": None,
            "aggregate": None, "position": None, "transform": None, "reference": None,
            "valid_when": None,
        }
    if spec.get("of") is not None:
        raise ContractRefusalError(f"{where}.of is only allowed on a flow feature")
    column = _req(spec, "column", str, where)
    if column not in fields:
        raise ContractRefusalError(f"{where}.column {column!r} is not a {tier} table column")
    if _is_denied(column, registry, is_predicate=False):
        raise ContractRefusalError(
            f"{where}.column {column!r} is denied by the frozen causal registry"
        )
    for bad in QUOTE_DENY:
        if column.startswith(bad):
            raise ContractRefusalError(
                f"{where}.column {column!r}: no quote channel may enter race geometry "
                "(quote_channel_ready is not established; a quote absence is never a value)"
            )
    fa = fields[column]
    if fa["namespace"] not in DISTANCE_NAMESPACES:
        raise ContractRefusalError(
            f"{where}.column {column!r}: namespace {fa['namespace']!r} is metadata, a "
            "coordinate or membership, never a race distance channel"
        )
    if fa["coordinate_only"] or fa["retrospective_only"] or not fa["prospective_allowed"]:
        raise ContractRefusalError(
            f"{where}.column {column!r}: not a positively eligible prospective race channel "
            f"(coordinate_only={fa['coordinate_only']}, "
            f"retrospective_only={fa['retrospective_only']}, "
            f"prospective_allowed={fa['prospective_allowed']})"
        )
    sup = fa["supportable_by_tier"]
    if "all" not in sup and tier not in sup:
        raise ContractRefusalError(
            f"{where}.column {column!r} is not supportable by tier {tier!r} "
            f"(supportable_by_tier={sup}); an unsupported channel is never encoded as a zero"
        )
    if column in COORDINATE_DENY:
        raise ContractRefusalError(
            f"{where}.column {column!r} is a clock/coordinate column, never a distance channel"
        )
    aggregate = _req(spec, "aggregate", str, where)
    if aggregate not in AGGREGATES:
        raise ContractRefusalError(f"{where}.aggregate {aggregate!r} is not one of {AGGREGATES}")
    position = spec.get("position")
    if aggregate == "quantile":
        if position is None:
            raise ContractRefusalError(
                f"{where}.position is UNBOUND (null) - the ruler position is a Freeze-R choice"
            )
        if not isinstance(position, (int, float)) or isinstance(position, bool):
            raise ContractRefusalError(f"{where}.position must be a number")
        if not 0.0 < float(position) <= 1.0:
            raise ContractRefusalError(f"{where}.position must be in (0, 1]")
        position = float(position)
    elif position is not None:
        raise ContractRefusalError(
            f"{where}.position is only allowed with aggregate='quantile'"
        )
    transform = _req(spec, "transform", str, where)
    if transform not in TRANSFORMS:
        raise ContractRefusalError(f"{where}.transform {transform!r} is not one of {TRANSFORMS}")
    ref = spec.get("reference")
    if transform == "log_ratio_to_reference":
        if not isinstance(ref, dict):
            raise ContractRefusalError(
                f"{where}.reference is required for transform log_ratio_to_reference"
            )
        ref_col = _req(ref, "of", str, f"{where}.reference")
        rule = _req(ref, "rule", str, f"{where}.reference")
        if rule not in REFERENCE_RULES:
            raise ContractRefusalError(
                f"{where}.reference.rule {rule!r} is not one of {REFERENCE_RULES}"
            )
        if ref_col not in fields:
            raise ContractRefusalError(
                f"{where}.reference.of {ref_col!r} is not a {tier} table column"
            )
        rfa = fields[ref_col]
        if (
            rfa["namespace"] not in DISTANCE_NAMESPACES
            or rfa["coordinate_only"]
            or rfa["retrospective_only"]
            or not rfa["prospective_allowed"]
        ):
            raise ContractRefusalError(
                f"{where}.reference.of {ref_col!r}: a reference level must itself be a "
                "positively eligible prospective race channel"
            )
        ref = {"of": ref_col, "rule": rule}
    elif ref is not None:
        raise ContractRefusalError(
            f"{where}.reference is only allowed with transform log_ratio_to_reference"
        )
    if vw is not None:
        vw = validate_predicate(
            vw, f"{where}.valid_when", fields=fields, registry=registry, mode=mode, tier=tier,
            for_channel_validity=True,
        )
    else:
        raise ContractRefusalError(
            f"{where}.valid_when is required - a channel must declare its validity explicitly; "
            "absence is never a market zero"
        )
    return {
        "id": fid, "kind": "level", "of": None, "column": column, "aggregate": aggregate,
        "position": position, "transform": transform, "reference": ref, "valid_when": vw,
    }


def _load_tier_block(
    raw_tier: dict, tier: str, *, schema: dict, registry: dict, mode: str
) -> dict:
    where = f"race_geometry.tiers.{tier}"
    table = _req(raw_tier, "table", str, where)
    if table != TIER_TABLE[tier]:
        raise ContractRefusalError(
            f"{where}.table {table!r} must be {TIER_TABLE[tier]!r} (the frozen tier mapping)"
        )
    keys = tuple(_req(raw_tier, "keys", list, where))
    if keys != TIER_KEYS[tier]:
        raise ContractRefusalError(
            f"{where}.keys {list(keys)} must equal the frozen schema grain {list(TIER_KEYS[tier])}"
        )
    declared_grain = tuple(schema["tables"][table].get("grain") or ())
    if declared_grain and declared_grain != keys:
        raise ContractRefusalError(
            f"{where}.keys disagree with schema.json grain {list(declared_grain)}"
        )
    fields = schema["tables"][table]["fields"]

    ca = _req(raw_tier, "clock_axis", dict, where)
    clock = _req(ca, "column", str, f"{where}.clock_axis")
    if clock != TIER_CLOCK[tier]:
        raise ContractRefusalError(
            f"{where}.clock_axis.column {clock!r} must be {TIER_CLOCK[tier]!r}"
        )
    if _req(ca, "unit", str, f"{where}.clock_axis") != "minute":
        raise ContractRefusalError(f"{where}.clock_axis.unit must be 'minute'")
    _req_true(ca, "frozen", f"{where}.clock_axis")
    if TIER_CLOCK_KIND[tier] == "minute":
        span = _req(ca, "span", list, f"{where}.clock_axis")
        if (
            len(span) != 2
            or not all(isinstance(x, int) and not isinstance(x, bool) for x in span)
            or not span[0] < span[1]
        ):
            raise ContractRefusalError(f"{where}.clock_axis.span must be [lo, hi] ints, lo < hi")
        if _req_int(ca, "step", f"{where}.clock_axis") != 1:
            raise ContractRefusalError(f"{where}.clock_axis.step must be 1 for a minute tier")
        if ca.get("axis_values") is not None:
            raise ContractRefusalError(
                f"{where}.clock_axis.axis_values is only for the checkpoint tier"
            )
        axis_values = None
    else:
        axis_values = _req(ca, "axis_values", list, f"{where}.clock_axis")
        if (
            not axis_values
            or not all(isinstance(x, int) and not isinstance(x, bool) for x in axis_values)
            or sorted(set(axis_values)) != list(axis_values)
        ):
            raise ContractRefusalError(
                f"{where}.clock_axis.axis_values must be a strictly increasing list of clocks"
            )
        if ca.get("span") is not None or ca.get("step") is not None:
            raise ContractRefusalError(
                f"{where}.clock_axis: the checkpoint tier declares axis_values, not span/step"
            )
        span = [min(axis_values), max(axis_values)]

    pop = _req(raw_tier, "population", dict, where)
    pop_preds = _opt_list(pop, "predicates", f"{where}.population")
    if not pop_preds:
        raise ContractRefusalError(
            f"{where}.population.predicates is empty - a population must be declared explicitly"
        )
    population = {
        "label": _req(pop, "label", str, f"{where}.population"),
        "predicates": [
            validate_predicate(p, f"{where}.population.predicates[{i}]", fields=fields,
                               registry=registry, mode=mode, tier=tier,
                               for_channel_validity=False)
            for i, p in enumerate(pop_preds)
        ],
    }
    _req_true(pop, "frozen", f"{where}.population")

    qual = _req(raw_tier, "quality", dict, where)
    qual_preds = _opt_list(qual, "predicates", f"{where}.quality")
    quality = {
        "qualified_label": _req(qual, "qualified_label", str, f"{where}.quality"),
        "reference_label": _req(qual, "reference_label", str, f"{where}.quality"),
        "predicates": [
            validate_predicate(p, f"{where}.quality.predicates[{i}]", fields=fields,
                               registry=registry, mode=mode, tier=tier,
                               for_channel_validity=False)
            for i, p in enumerate(qual_preds)
        ],
    }
    _req_true(qual, "frozen", f"{where}.quality")

    sc = _req(raw_tier, "scale", dict, where)
    unit = _req(sc, "unit", str, f"{where}.scale")
    if unit != TIER_CLOCK_KIND[tier]:
        raise ContractRefusalError(
            f"{where}.scale.unit {unit!r} must be {TIER_CLOCK_KIND[tier]!r} for this tier"
        )
    ladder = _req(sc, "ladder", list, f"{where}.scale")
    hi = (span[1] - span[0] + 1) if unit == "minute" else len(axis_values)
    if not ladder or not all(
        isinstance(s, int) and not isinstance(s, bool) and 0 < s <= hi for s in ladder
    ):
        raise ContractRefusalError(
            f"{where}.scale.ladder must be ints in [1,{hi}] (found {ladder!r})"
        )
    if _req(sc, "anchor", str, f"{where}.scale") != ANCHOR:
        raise ContractRefusalError(f"{where}.scale.anchor must be {ANCHOR!r}")
    if _req(sc, "alignment", str, f"{where}.scale") != ALIGNMENT:
        raise ContractRefusalError(f"{where}.scale.alignment must be {ALIGNMENT!r}")
    stride = _req_int(sc, "anchor_stride", f"{where}.scale", lo=1)
    _req_true(sc, "frozen", f"{where}.scale")
    scale = {"unit": unit, "ladder": [int(s) for s in ladder], "anchor": ANCHOR,
             "alignment": ALIGNMENT, "stride": stride}

    fl = _req(raw_tier, "flow", dict, where)
    rule = _req(fl, "rule", str, f"{where}.flow")
    if rule != FLOW_RULE:
        raise ContractRefusalError(
            f"{where}.flow.rule {rule!r} must be {FLOW_RULE!r} (the only supported flow rule)"
        )
    if _req_int(fl, "order", f"{where}.flow") != 1:
        raise ContractRefusalError(f"{where}.flow.order must be 1 (first difference)")
    _req_true(fl, "frozen", f"{where}.flow")

    cells = _req(raw_tier, "cells", dict, where)
    if set(cells) != set(CELLS):
        raise ContractRefusalError(
            f"{where}.cells must name exactly {sorted(CELLS)} (found {sorted(cells)})"
        )
    for name in CELLS:
        val = _req(cells, name, str, f"{where}.cells")
        if val not in CELL_STATUS:
            raise ContractRefusalError(
                f"{where}.cells.{name} {val!r} must be one of {CELL_STATUS}"
            )
    if cells["quotes"] == "materialized":
        raise ContractRefusalError(
            f"{where}.cells.quotes cannot be 'materialized': no quote channel may enter race "
            "geometry before quote_channel_ready, and a quote absence is never a value"
        )
    if cells["learned_minute_sequence"] == "materialized":
        raise ContractRefusalError(
            f"{where}.cells.learned_minute_sequence cannot be 'materialized': the masked-TCN "
            "sequence archive is produced by the sequence-archive sibling, not by this node"
        )

    sg = _req(raw_tier, "surrogate", dict, where)
    axes = _req(sg, "matched_axes", list, f"{where}.surrogate")
    if not axes or any(a not in SURROGATE_AXES for a in axes):
        raise ContractRefusalError(
            f"{where}.surrogate.matched_axes must be a non-empty subset of {SURROGATE_AXES}"
        )
    if "clock_bucket" in axes and unit == "minute" and not (span[0] <= span[1]):
        raise ContractRefusalError(f"{where}.surrogate: malformed clock span")
    decile_def = _req(sg, "density_decile_definition", str, f"{where}.surrogate")
    if decile_def != DECILE_DEF:
        raise ContractRefusalError(
            f"{where}.surrogate.density_decile_definition must be {DECILE_DEF!r}"
        )
    surrogate = {
        "axes": list(axes),
        "clock_bucket_size": _req_int(sg, "clock_bucket_size", f"{where}.surrogate", lo=1),
        "draws": _req_int(sg, "draws", f"{where}.surrogate", lo=1),
        "density_decile_definition": decile_def,
    }
    _req_true(sg, "frozen", f"{where}.surrogate")

    return {
        "tier": tier, "table": table, "physical_dir": TIER_PHYSICAL_DIR[tier], "keys": keys,
        "clock": clock, "clock_kind": TIER_CLOCK_KIND[tier], "span": [int(span[0]), int(span[1])],
        "axis_values": axis_values, "population": population, "quality": quality, "scale": scale,
        "flow": {"rule": FLOW_RULE, "order": 1}, "cells": {k: cells[k] for k in CELLS},
        "surrogate": surrogate, "fields": fields,
    }


def validate_view(
    spec, where: str, *, tier: str, fields: dict, registry: dict, mode: str,
    channel_registry: dict, tier_cells: dict,
) -> dict:
    if not isinstance(spec, dict):
        raise ContractRefusalError(f"{where} must be an object")
    raw_feats = _req(spec, "features", list, where)
    if not raw_feats:
        raise ContractRefusalError(f"{where}.features is empty")
    ids, feats = [], {}
    for i, fs in enumerate(raw_feats):
        feat = validate_feature(fs, f"{where}.features[{i}]", tier=tier, fields=fields,
                                registry=registry, mode=mode)
        if feat["id"] in feats:
            raise ContractRefusalError(f"{where}: feature id {feat['id']!r} is declared twice")
        feats[feat["id"]] = feat
        ids.append(feat["id"])
        known = channel_registry.get(feat["id"])
        if known is not None and known != feat:
            raise ContractRefusalError(
                f"{where}: feature id {feat['id']!r} is declared twice with different specs"
            )
        channel_registry[feat["id"]] = feat
    for fid in ids:
        feat = feats[fid]
        if feat["kind"] == "flow":
            src = feats.get(feat["of"])
            if src is None:
                raise ContractRefusalError(
                    f"{where}: flow feature {fid!r} references {feat['of']!r} which is not a "
                    "level feature of the same view"
                )
            if src["kind"] != "level":
                raise ContractRefusalError(f"{where}: flow feature {fid!r} must derive from a "
                                           "level feature")
    if _req(spec, "metric", str, where) != METRIC:
        raise ContractRefusalError(f"{where}.metric must be {METRIC!r} (the only supported metric)")
    wts = _req(spec, "metric_weights", dict, where)
    if set(wts) != set(ids):
        raise ContractRefusalError(f"{where}.metric_weights must weight exactly its features")
    for fid in ids:
        _req_num(wts, fid, f"{where}.metric_weights", lo=0.0)
    if not any(float(wts[f]) > 0 for f in ids):
        raise ContractRefusalError(f"{where}.metric_weights are all zero")
    sref = _req(spec, "scale_reference", dict, where)
    if _req(sref, "statistic", str, f"{where}.scale_reference") != SCALE_STATISTIC:
        raise ContractRefusalError(f"{where}.scale_reference.statistic must be {SCALE_STATISTIC!r}")
    sref_k = _req_int(sref, "k", f"{where}.scale_reference", lo=1)
    return {
        "features": [feats[f] for f in ids],
        "weights": {f: float(wts[f]) for f in ids},
        "scale_reference": {"statistic": SCALE_STATISTIC, "k": sref_k},
        "radius": _req_num(spec, "radius", where, positive=True),
        "low_density_max": _req_int(spec, "low_density_max", where, lo=0),
        "uncertain_kth_dist_min": _req_num(spec, "uncertain_kth_dist_min", where, lo=0.0),
    }


def load_race_config(raw: dict, r: dict, *, schema: dict, registry: dict, mode: str) -> dict:
    """Validate the ``race_geometry`` section of a frozen Freeze-R record.  Never defaults."""
    rg = raw.get("race_geometry")
    if not isinstance(rg, dict):
        raise ContractRefusalError(
            "the frozen record declares no 'race_geometry' section - the race tiers are unbound"
        )
    race_version = _req(rg, "race_matrix_version", str, "race_geometry")
    if re.search(r"DRAFT|TEMPLATE|NOT-RUN|NOT_RUN|UNFROZEN", race_version, re.I):
        raise ContractRefusalError(f"race_geometry.race_matrix_version {race_version!r} is a draft")
    _req_true(rg, "frozen", "race_geometry")

    raw_tiers = _req(rg, "tiers", dict, "race_geometry")
    if set(raw_tiers) != set(TIER_ORDER):
        raise ContractRefusalError(
            f"race_geometry.tiers must name exactly {list(TIER_ORDER)} (found {sorted(raw_tiers)})"
        )
    tiers = {
        t: _load_tier_block(raw_tiers[t], t, schema=schema, registry=registry, mode=mode)
        for t in TIER_ORDER
    }

    k = _req_int(rg, "neighbours_k", "race_geometry", lo=1)
    if k != PUBLISHED_K:
        raise ContractRefusalError(
            f"race_geometry.neighbours_k must be {PUBLISHED_K}: this node publishes exactly "
            "nearest-100 retrieval (no re-tuning of the published k)"
        )
    if int(r["neighbours_k"]) != k:
        raise ContractRefusalError(
            "the base record's first_retrieval_proof.neighbours_k disagrees with "
            "race_geometry.neighbours_k"
        )

    ann = _req(rg, "ann_sparse_recall", dict, "race_geometry")
    if _req(ann, "index_build", str, "race_geometry.ann_sparse_recall") != ANN_INDEX_BUILD:
        raise ContractRefusalError(
            f"race_geometry.ann_sparse_recall.index_build must be {ANN_INDEX_BUILD!r}: this node "
            "builds no ANN index and searches exactly"
        )
    if _req(ann, "exact_search_strata", str, "race_geometry.ann_sparse_recall") != ANN_STRATA:
        raise ContractRefusalError(
            "race_geometry.ann_sparse_recall.exact_search_strata must be 'all'"
        )
    if _req_num(ann, "sparse_exact_recall_floor", "race_geometry.ann_sparse_recall") != (
        ANN_RECALL_FLOOR
    ):
        raise ContractRefusalError(
            f"race_geometry.ann_sparse_recall.sparse_exact_recall_floor must be {ANN_RECALL_FLOOR}"
        )
    _req_true(ann, "frozen", "race_geometry.ann_sparse_recall")
    if _req(rg, "density_decile_definition", str, "race_geometry") != DECILE_DEF:
        raise ContractRefusalError(
            f"race_geometry.density_decile_definition must be {DECILE_DEF!r}"
        )

    ind = _req(rg, "independence", dict, "race_geometry")
    if _req(ind, "unit", str, "race_geometry.independence") != "day":
        raise ContractRefusalError(
            "race_geometry.independence.unit must be 'day': the day is the race tape"
        )
    _req_true(ind, "exclude_same_unit", "race_geometry.independence")
    _req_true(ind, "frozen", "race_geometry.independence")

    ledger = _req(rg, "adaptive_choice_ledger", dict, "race_geometry")
    led_path = Path(_req(ledger, "path", str, "race_geometry.adaptive_choice_ledger"))
    if not led_path.is_absolute():
        led_path = ROOT / led_path
    if not led_path.is_file():
        raise ContractRefusalError(
            f"the Freeze-R adaptive-choice ledger is missing: {led_path}"
        )
    if geo.sha256_file(led_path) != _req_hex(
        ledger, "sha256", "race_geometry.adaptive_choice_ledger"
    ):
        raise ContractRefusalError(
            "the Freeze-R adaptive-choice ledger sha256 does not match the record - stale"
        )
    if _req_true(ledger, "covers_race_choices", "race_geometry.adaptive_choice_ledger"):
        led = geo.load_json(led_path)
        entries = led.get("entries") if isinstance(led, dict) else None
        if not isinstance(entries, list) or not entries:
            raise ContractRefusalError(
                "the Freeze-R adaptive-choice ledger carries no entries - race choices are "
                "unrecorded"
            )
        if not any(
            isinstance(e, dict) and "race" in str(e.get("affected_node", "")).lower()
            for e in entries
        ):
            raise ContractRefusalError(
                "no adaptive-choice ledger entry names a race node - the race choices are not "
                "recorded"
            )
    gates = _req(rg, "geometry_gates", dict, "race_geometry")

    raw_views = _req(rg, "views", dict, "race_geometry")
    if set(raw_views) != set(TIER_ORDER):
        raise ContractRefusalError(
            f"race_geometry.views must name exactly {list(TIER_ORDER)}"
        )
    raw_weights = _req(rg, "view_weights", dict, "race_geometry")
    if set(raw_weights) != set(TIER_ORDER):
        raise ContractRefusalError(
            f"race_geometry.view_weights must name exactly {list(TIER_ORDER)}"
        )
    views: dict[str, dict] = {}
    for tier in TIER_ORDER:
        block = tiers[tier]
        declared = _req(raw_views, tier, dict, f"race_geometry.views.{tier}")
        unknown = set(declared) - set(HAND_VIEWS)
        if unknown:
            raise ContractRefusalError(
                f"race_geometry.views.{tier} declares {sorted(unknown)}; this node builds exactly "
                f"the five hand views {list(HAND_VIEWS)} (a learned race view belongs to the "
                "sequence-archive sibling)"
            )
        if BALANCED not in declared:
            raise ContractRefusalError(
                f"race_geometry.views.{tier} must declare {BALANCED!r} (the fixed equal-weight "
                "reference view)"
            )
        prim = [v for v in PRIMITIVE_VIEWS if v in declared]
        if len(prim) < 2:
            raise ContractRefusalError(
                f"race_geometry.views.{tier} must declare at least two primitive views so "
                "magnitude-vs-shape disagreement is measurable"
            )
        if block["cells"]["magnitude_shape_duration"] == "unsupported" and prim:
            raise ContractRefusalError(
                f"race_geometry: tier {tier!r} declares the magnitude/shape cell unsupported "
                "while declaring primitive views"
            )
        chan_reg: dict[str, dict] = {}
        per_view: dict[str, dict] = {}
        for vname in prim:
            per_view[vname] = validate_view(
                declared[vname], f"race_geometry.views.{tier}.{vname}", tier=tier,
                fields=block["fields"], registry=registry, mode=mode,
                channel_registry=chan_reg, tier_cells=block["cells"],
            )
        bspec = _req(declared, BALANCED, dict, f"race_geometry.views.{tier}.{BALANCED}")
        declared_agg = _req(bspec, "aggregation", str, f"race_geometry.views.{tier}.{BALANCED}")
        if declared_agg != BALANCED_DECL:
            raise ContractRefusalError(
                f"race_geometry.views.{tier}.{BALANCED}.aggregation must be {BALANCED_DECL!r}"
            )
        if bspec.get("balanced_reference_tuned_to_best_geometry") is not False:
            raise ContractRefusalError(
                f"race_geometry.views.{tier}.{BALANCED}.balanced_reference_tuned_to_best_geometry "
                "must be false"
            )
        min_common = _req_int(bspec, "min_common_views", f"race_geometry.views.{tier}",
                              lo=1, hi=len(prim))
        per_view[BALANCED] = {
            "features": [], "weights": {},
            "radius": _req_num(bspec, "radius", f"race_geometry.views.{tier}.{BALANCED}",
                               positive=True),
            "low_density_max": _req_int(bspec, "low_density_max",
                                        f"race_geometry.views.{tier}.{BALANCED}", lo=0),
            "uncertain_kth_dist_min": _req_num(bspec, "uncertain_kth_dist_min",
                                               f"race_geometry.views.{tier}.{BALANCED}", lo=0.0),
            "aggregation": BALANCED_DECL,
        }
        wts = _req(raw_weights, tier, dict, f"race_geometry.view_weights.{tier}")
        if set(wts) != set(prim):
            raise ContractRefusalError(
                f"race_geometry.view_weights.{tier} must weight exactly the declared primitive "
                f"views {prim}"
            )
        vals = [_req_num(wts, v, f"race_geometry.view_weights.{tier}", positive=True) for v in prim]
        if max(vals) - min(vals) > 0:
            raise ContractRefusalError(
                f"race_geometry.view_weights.{tier} must be EQUAL: the balanced reference is a "
                "fixed equal-weight mean of standardized view distances, never tuned"
            )
        for vname in prim:
            vk = per_view[vname]["scale_reference"]["k"]
            if vk > k:
                raise ContractRefusalError(
                    f"race_geometry.views.{tier}.{vname}.scale_reference.k ({vk}) exceeds "
                    f"neighbours_k ({k})"
                )
        views[tier] = {"primitive": prim, "views": per_view, "min_common": min_common,
                       "view_weights": {v: float(wts[v]) for v in prim}}
    return {
        "race_matrix_version": race_version,
        "tiers": tiers,
        "views": views,
        "k": k,
        "gates": gates,
        "ann": {"index_build": ANN_INDEX_BUILD, "exact_search_strata": ANN_STRATA,
                "sparse_exact_recall_floor": ANN_RECALL_FLOOR,
                "density_decile_definition": DECILE_DEF},
        "independence": {"unit": "day", "exclude_same_unit": True},
        "ledger": {"path": str(led_path), "sha256": geo.sha256_file(led_path)},
        "mode": mode,
        "seed": int(r["seed"]),
        "blocks": {"fit": list(r["blocks"]["fit"]), "eval": list(r["blocks"]["eval"])},
    }


# --------------------------------------------------------------------------- #
# feature registry of one tier (levels + flows), shared by its views
# --------------------------------------------------------------------------- #
def tier_feature_registry(cfg: dict, tier: str) -> dict:
    prim = cfg["views"][tier]["primitive"]
    levels, flows = [], []
    for vname in prim:
        for feat in cfg["views"][tier]["views"][vname]["features"]:
            (levels if feat["kind"] == "level" else flows).append(feat)
    seen, uniq_levels, uniq_flows = set(), [], []
    for feat in levels:
        if feat["id"] not in seen:
            seen.add(feat["id"])
            uniq_levels.append(feat)
    for feat in flows:
        if feat["id"] not in seen:
            seen.add(feat["id"])
            uniq_flows.append(feat)
    return {"levels": uniq_levels, "flows": uniq_flows,
            "all": uniq_levels + uniq_flows}


def _needed_columns(cfg: dict, tier: str) -> dict:
    block = cfg["tiers"][tier]
    cols = {"keys": set(block["keys"]), "predicate": set(), "value": set(),
            "meta": set(), "meta_optional": set()}
    for pred in block["population"]["predicates"] + block["quality"]["predicates"]:
        cols["predicate"].add(pred["column"])
    for feat in tier_feature_registry(cfg, tier)["levels"]:
        cols["value"].add(feat["column"])
        cols["value"].add(feat["valid_when"]["column"])
        if feat["reference"] is not None:
            cols["value"].add(feat["reference"]["of"])
    cols["meta"] = {"source_sha256"}
    for optional in ("feed_era", "population_def"):
        if optional in block["fields"]:
            cols["meta_optional"].add(optional)
    return cols


def _feature_agg_expr(feat: dict) -> pl.Expr:
    expr = pl.col(feat["column"]).filter(
        _predicate_expr(feat["valid_when"]) & pl.col(feat["column"]).is_not_null()
    )
    agg = feat["aggregate"]
    if agg == "quantile":
        return expr.quantile(float(feat["position"]), interpolation="linear")
    return {"mean": expr.mean, "max": expr.max, "min": expr.min}[agg]()


def _reference_agg_expr(feat: dict) -> pl.Expr:
    ref = feat["reference"]
    expr = pl.col(ref["of"]).filter(pl.col(ref["of"]).is_not_null())
    if ref["rule"] == "cross_section_median_at_same_t":
        return expr.quantile(0.5, interpolation="linear")
    return expr.mean()


def _apply_transform(values: np.ndarray, valid: np.ndarray, feat: dict,
                     ref_values: np.ndarray | None) -> tuple:
    transform = feat["transform"]
    out = np.full(values.shape, np.nan, dtype=np.float64)
    ok = valid.copy()
    with np.errstate(invalid="ignore", divide="ignore"):
        if transform == "identity":
            out = values.copy()
        elif transform in ("log1p", "log_level"):
            pos = valid & (values >= 0.0) if transform == "log1p" else valid & (values > 0.0)
            out[pos] = np.log1p(values[pos]) if transform == "log1p" else np.log(values[pos])
            ok = pos
        elif transform == "log_ratio_to_reference":
            good = valid & (values > 0.0) & np.isfinite(ref_values) & (ref_values > 0.0)
            out[good] = np.log(values[good] / ref_values[good])
            ok = good
        else:  # pragma: no cover - validated earlier
            raise ContractRefusalError(f"unsupported transform {transform!r}")
    ok = ok & np.isfinite(out)
    out = np.where(ok, out, np.nan)
    return out, ok


# --------------------------------------------------------------------------- #
# day extraction (streamed, projected)
# --------------------------------------------------------------------------- #
def _day_payload(corpus: dict, tier: str, day: str, *, root: Path, verify: bool,
                 problems: list) -> Path | None:
    table = TIER_TABLE[tier]
    info = (corpus["payloads"].get(f"{table}/{day}") or {})
    rel = info.get("path")
    if not rel:
        problems.append({"kind": "race_payload_absent", "tier": tier, "day": day})
        return None
    path = geo.resolve_under(root, str(rel))
    if not path.is_file():
        problems.append({"kind": "race_payload_file_absent", "tier": tier, "day": day,
                         "path": str(rel)})
        return None
    expected_dir = TIER_PHYSICAL_DIR[tier]
    if path.parent.name != expected_dir and Path(rel).parts[0] != expected_dir:
        problems.append({"kind": "race_payload_dir_unexpected", "tier": tier, "day": day,
                         "expected": expected_dir, "path": str(rel)})
        return None
    if verify and info.get("sha256") and geo.sha256_file(path) != info["sha256"]:
        problems.append({"kind": "race_payload_sha256_mismatch", "tier": tier, "day": day})
        return None
    return path


def extract_day(cfg: dict, tier: str, corpus: dict, day: str, *, root: Path, verify: bool,
                problems: list) -> dict | None:
    """Project + aggregate ONE day of ONE race tier into its cross-sectional clock series."""
    block = cfg["tiers"][tier]
    path = _day_payload(corpus, tier, day, root=root, verify=verify, problems=problems)
    if path is None:
        return None
    needed = _needed_columns(cfg, tier)
    lf = pl.scan_parquet(str(path))
    physical = list(lf.collect_schema().names())
    required = sorted(needed["keys"] | needed["predicate"] | needed["value"] | needed["meta"])
    missing = [c for c in required if c not in physical]
    if missing:
        raise IntegrityRefusalError(
            f"{day}: the physical {TIER_TABLE[tier]} payload lacks column(s) the frozen record "
            "binds",
            [{"kind": "race_payload_missing_columns", "tier": tier, "day": day,
              "columns": missing, "payload": str(path)}],
        )
    n_rows = int(lf.select(pl.len()).collect().item())
    info = corpus["payloads"].get(f"{TIER_TABLE[tier]}/{day}") or {}
    if info.get("rows") is not None and int(info["rows"]) != n_rows:
        problems.append({"kind": "race_payload_row_mismatch", "tier": tier, "day": day,
                         "declared": int(info["rows"]), "observed": n_rows})
        return None

    clock = block["clock"]
    key_cols = list(block["keys"])
    base = lf.with_row_index("_ri")
    # ---- pass A: row selectability BEFORE any value column is read --------------------- #
    stage_a_cols = ["_ri"] + key_cols + sorted(needed["predicate"])
    presel = base.select(stage_a_cols).collect()
    pop_expr = combine_predicates(block["population"]["predicates"])
    selected = presel.filter(pop_expr)
    n_excluded = presel.height - selected.height
    qual_preds = block["quality"]["predicates"]
    if qual_preds:
        parts = []
        for pred in qual_preds:
            desc = (f"{pred['column']}{pred['op']}{pred['value']}"
                    if pred["op"] not in ("is_null", "is_not_null")
                    else f"{pred['column']}{pred['op']}")
            parts.append(
                pl.when(~_predicate_expr(pred)).then(pl.lit(desc)).otherwise(None)
            )
        reason = pl.concat_list(parts).list.drop_nulls().list.join(",")
        selected = selected.with_columns(
            combine_predicates(qual_preds).alias("__qok"),
            reason.alias("__qreason"),
        )
    else:
        selected = selected.with_columns(
            pl.lit(True).alias("__qok"), pl.lit("").alias("__qreason")
        )
    keep = selected.select(["_ri", "__qok", "__qreason"])
    if keep.height == 0:
        return {
            "day": day, "block": geo.block_of(day), "n_axis": 0, "n_selected": 0,
            "n_excluded": n_excluded, "labels": np.zeros(0, dtype=np.int32),
            "levels": np.zeros((0, len(tier_feature_registry(cfg, tier)["levels"])), np.float64),
            "level_valid": np.zeros((0, len(tier_feature_registry(cfg, tier)["levels"])), bool),
            "flows": np.zeros((0, len(tier_feature_registry(cfg, tier)["flows"])), np.float64),
            "flow_valid": np.zeros((0, len(tier_feature_registry(cfg, tier)["flows"])), bool),
            "pop_n": np.zeros(0, dtype=np.int32), "qok": np.zeros(0, dtype=bool),
            "qreason": [], "digests": {}, "era": corpus["era_of"].get(day),
            "source_sha256": None, "population_def": None, "empty": True,
        }
    # ---- pass B: value columns, restricted to the rows that passed --------------------- #
    identity_cols = {c for c in (clock, "ticker", "symbol") if c in physical}
    value_cols = sorted(set(needed["value"]) | identity_cols)
    vals = base.select(["_ri"] + value_cols).collect().join(keep, on="_ri", how="inner")
    meta_cols = sorted(needed["meta"] | {c for c in needed["meta_optional"] if c in physical})
    meta = base.select(["_ri"] + meta_cols).collect().join(keep, on="_ri", how="inner")

    reg = tier_feature_registry(cfg, tier)
    aggs = [_feature_agg_expr(f).alias(f["id"]) for f in reg["levels"]]
    aggs.append(pl.len().alias("__pop"))
    aggs.append(pl.col("__qok").all().alias("__qok"))
    aggs.append(pl.col("__qreason").filter(pl.col("__qreason") != "").unique().sort()
                .alias("__qreason"))
    for feat in reg["levels"]:
        if feat["reference"] is not None:
            aggs.append(_reference_agg_expr(feat).alias(f"__ref::{feat['id']}"))
    grouped = vals.group_by(clock).agg(aggs).sort(clock)
    population_def = None
    if "population_def" in meta.columns:
        dtype = meta.schema["population_def"]
        if dtype == pl.String:
            first = meta["population_def"].drop_nulls().unique()
            if first.len() > 1:
                raise IntegrityRefusalError(
                    f"{day}: population_def is not constant over the day's {tier} rows",
                    [{"kind": "population_def_not_constant", "tier": tier, "day": day,
                      "values": sorted(first.to_list())[:8]}],
                )
            population_def = first.to_list()[0] if first.len() else None
    source_shas = set(meta["source_sha256"].drop_nulls().unique().to_list())
    if len(source_shas) > 1:
        raise IntegrityRefusalError(
            f"{day}: the {tier} payload carries more than one raw-lane sha for the day",
            [{"kind": "raw_lane_sha_not_constant", "tier": tier, "day": day,
              "n": len(source_shas)}],
        )
    era = corpus["era_of"].get(day)
    if "feed_era" in meta.columns and era is not None:
        observed = {str(x) for x in meta["feed_era"].drop_nulls().unique().to_list()}
        if observed and observed != {era}:
            raise IntegrityRefusalError(
                f"{day}: the {tier} payload feed_era {sorted(observed)} disagrees with the day "
                "registry",
                [{"kind": "feed_era_disagreement", "tier": tier, "day": day,
                  "payload": sorted(observed), "registry": era}],
            )

    labels = np.asarray(grouped[clock].to_list(), dtype=np.int64)
    step = 1 if block["clock_kind"] == "minute" else None
    lo, hi = block["span"]
    if labels.size and (labels.min() < lo or labels.max() > hi):
        problems.append({"kind": "clock_label_outside_declared_span", "tier": tier, "day": day,
                         "observed": [int(labels.min()), int(labels.max())], "span": [lo, hi]})
        return None
    if block["clock_kind"] == "checkpoint":
        allowed = set(block["axis_values"])
        if any(int(x) not in allowed for x in labels):
            problems.append({"kind": "checkpoint_label_not_declared", "tier": tier, "day": day})
            return None

    n_axis = int(labels.size)
    n_levels, n_flows = len(reg["levels"]), len(reg["flows"])
    levels = np.full((n_axis, n_levels), np.nan, dtype=np.float64)
    level_valid = np.zeros((n_axis, n_levels), dtype=bool)
    for ci, feat in enumerate(reg["levels"]):
        raw = np.asarray(
            [np.nan if v is None else float(v) for v in grouped[feat["id"]].to_list()],
            dtype=np.float64,
        )
        base_valid = np.isfinite(raw)
        ref_values = None
        if feat["reference"] is not None:
            ref_values = np.asarray(
                [np.nan if v is None else float(v)
                 for v in grouped[f"__ref::{feat['id']}"].to_list()],
                dtype=np.float64,
            )
        out, ok = _apply_transform(raw, base_valid, feat, ref_values)
        levels[:, ci] = out
        level_valid[:, ci] = ok
    flows = np.full((n_axis, n_flows), np.nan, dtype=np.float64)
    flow_valid = np.zeros((n_axis, n_flows), dtype=bool)
    level_index = {f["id"]: i for i, f in enumerate(reg["levels"])}
    for ci, feat in enumerate(reg["flows"]):
        src = level_index[feat["of"]]
        for i in range(1, n_axis):
            same_run = (labels[i] - labels[i - 1] == 1) if step == 1 else True
            if not same_run:
                continue
            if level_valid[i, src] and level_valid[i - 1, src]:
                flows[i, ci] = levels[i, src] - levels[i - 1, src]
                flow_valid[i, ci] = True
    pop_n = np.asarray(grouped["__pop"].to_list(), dtype=np.int64)
    qok = np.asarray(grouped["__qok"].to_list(), dtype=bool)
    qreason = [",".join(x) if x else "" for x in grouped["__qreason"].to_list()]
    digests = _population_digests(vals, clock, labels, block)
    return {
        "day": day, "block": geo.block_of(day), "n_axis": n_axis, "n_selected": int(keep.height),
        "n_excluded": int(n_excluded), "labels": labels.astype(np.int64), "levels": levels,
        "level_valid": level_valid, "flows": flows, "flow_valid": flow_valid,
        "pop_n": pop_n.astype(np.int64), "qok": qok, "qreason": qreason, "digests": digests,
        "era": era, "source_sha256": next(iter(source_shas), None),
        "population_def": population_def, "empty": False,
    }


def _population_digests(vals: pl.DataFrame, clock: str, labels: np.ndarray,
                        block: dict) -> dict:
    """Deterministic commitment to the exact cross-section membership of every clock."""
    ticker_col = "ticker" if "ticker" in vals.columns else "symbol"
    if ticker_col not in vals.columns or labels.size == 0:
        return {}
    out: dict[int, str] = {}
    chunk = 64
    lab_list = [int(x) for x in labels]
    for s in range(0, len(lab_list), chunk):
        part = lab_list[s:s + chunk]
        sub = (
            vals.select([clock, ticker_col])
            .filter(pl.col(clock).is_in(part))
            .group_by(clock)
            .agg(pl.col(ticker_col).sort().alias("__members"))
        )
        for label, members in zip(sub[clock].to_list(), sub["__members"].to_list(),
                                  strict=True):
            digest = hashlib.sha256("\n".join(members).encode()).hexdigest()
            out[int(label)] = digest
    return out


def extract_tier(cfg: dict, tier: str, corpus: dict, days: list, *, root: Path, verify: bool,
                 problems: list, budget_gib: float) -> dict:
    """Stream every day of the tier and assemble the object tensors."""
    reg = tier_feature_registry(cfg, tier)
    blocks = cfg["blocks"]
    day_frames: list[dict] = []
    for day in days:
        frame = extract_day(cfg, tier, corpus, day, root=root, verify=verify, problems=problems)
        if frame is None:
            continue
        day_frames.append(frame)
    if problems:
        raise IntegrityRefusalError(f"{tier}: day payload integrity", problems)

    plan = []
    for frame in day_frames:
        labels = frame["labels"]
        if labels.size == 0:
            continue
        runs = _contiguous_runs(labels, cfg["tiers"][tier]["clock_kind"])
        for scale in cfg["tiers"][tier]["scale"]["ladder"]:
            stride = cfg["tiers"][tier]["scale"]["stride"]
            for run in runs:
                length = run[1] - run[0] + 1
                if length < scale:
                    continue
                for a in range(run[0] + scale - 1, run[1] + 1, stride):
                    plan.append((frame, scale, a - scale + 1, a))
    n_obj = len(plan)
    if n_obj == 0:
        raise IntegrityRefusalError(
            f"{tier}: no window satisfies the frozen scale ladder over the frozen day set",
            [{"kind": "no_objects", "tier": tier}],
        )
    n_feat = len(reg["all"])
    t_max = max(cfg["tiers"][tier]["scale"]["ladder"])
    est = footprint_estimate(n_obj, n_feat, t_max, cfg, tier)
    if est["peak_bytes"] > budget_gib * (1024 ** 3):
        raise ContractRefusalError(
            f"{tier}: the estimated peak footprint {est['peak_gib']:.2f} GiB exceeds the "
            f"declared memory budget {budget_gib:.2f} GiB - the request is refused, not clamped"
        )

    values = np.zeros((n_obj, n_feat, t_max), dtype=np.float32)
    valid = np.zeros((n_obj, n_feat, t_max), dtype=bool)
    axis_labels = np.full((n_obj, t_max), -1, dtype=np.int32)
    rows = []
    n_levels = len(reg["levels"])
    for oi, (frame, scale, lo, hi) in enumerate(plan):
        width = hi - lo + 1
        levels = frame["levels"][lo:hi + 1]
        level_valid = frame["level_valid"][lo:hi + 1]
        flows = frame["flows"][lo:hi + 1]
        flow_valid = frame["flow_valid"][lo:hi + 1]
        block_arr = np.concatenate([levels, flows], axis=1)
        valid_arr = np.concatenate([level_valid, flow_valid], axis=1)
        values[oi, :n_levels, :width] = block_arr[:, :n_levels].T.astype(np.float32)
        values[oi, n_levels:, :width] = block_arr[:, n_levels:].T.astype(np.float32)
        valid[oi, :n_levels, :width] = valid_arr[:, :n_levels].T
        valid[oi, n_levels:, :width] = valid_arr[:, n_levels:].T
        axis_labels[oi, :width] = frame["labels"][lo:hi + 1].astype(np.int32)
        qok_win = frame["qok"][lo:hi + 1]
        reasons = sorted({r for r, ok in zip(frame["qreason"][lo:hi + 1], qok_win,
                                             strict=True) if not ok and r})
        qualified = bool(qok_win.all())
        query_t = int(frame["labels"][hi])
        rows.append({
            "object_id": f"{frame['day']}@{tier}@{scale}@{lo}",
            "tier": tier,
            "day": frame["day"],
            "block": frame["block"],
            "era": frame["era"],
            "scale": int(scale),
            "window_start_t": int(frame["labels"][lo]),
            "window_end_t": query_t,
            "query_t": query_t,
            "n_minutes_observed": int(width),
            "population_n_min": int(frame["pop_n"][lo:hi + 1].min()),
            "population_n_max": int(frame["pop_n"][lo:hi + 1].max()),
            "population_digest_query": frame["digests"].get(query_t),
            "quality_class": (cfg["tiers"][tier]["quality"]["qualified_label"] if qualified
                              else cfg["tiers"][tier]["quality"]["reference_label"]),
            "quality_reasons": ",".join(reasons),
            "source_sha256": frame["source_sha256"],
            "payload_path": (corpus["payloads"].get(
                f"{TIER_TABLE[tier]}/{frame['day']}") or {}).get("path"),
            "population_def": frame["population_def"],
            "fit": geo.block_of(frame["day"]) in blocks["fit"],
            "retrospective_only": cfg["mode"] == "retrospective",
            "valid_prefix_len": int(_valid_prefix(valid_arr)),
        })
    objects = pl.DataFrame(rows, schema_overrides={
        "scale": pl.Int32, "window_start_t": pl.Int32, "window_end_t": pl.Int32,
        "query_t": pl.Int32, "n_minutes_observed": pl.Int32, "population_n_min": pl.Int32,
        "population_n_max": pl.Int32, "fit": pl.Boolean, "retrospective_only": pl.Boolean,
        "valid_prefix_len": pl.Int32,
    })
    day_facts = [{
        "day": f["day"], "block": f["block"], "era": f["era"], "clock_minutes": int(f["n_axis"]),
        "rows_selected": int(f["n_selected"]), "rows_excluded_by_population": int(f["n_excluded"]),
        "quality_qualified_clocks": int(f["qok"].sum()), "source_sha256": f["source_sha256"],
        "population_def": f["population_def"],
    } for f in day_frames]
    return {"values": values, "valid": valid, "axis_labels": axis_labels,
            "channel_ids": [f["id"] for f in reg["all"]], "reg": reg, "objects": objects,
            "day_facts": day_facts, "footprint": est, "plan": plan}


def _contiguous_runs(labels: np.ndarray, clock_kind: str) -> list:
    runs, start = [], 0
    for i in range(1, labels.size):
        if clock_kind == "minute" and labels[i] - labels[i - 1] != 1:
            runs.append((start, i - 1))
            start = i
    runs.append((start, labels.size - 1))
    return runs


def _valid_prefix(valid_arr: np.ndarray) -> int:
    n = 0
    for i in range(valid_arr.shape[0]):
        if valid_arr[i].any():
            n += 1
        else:
            break
    return n


def footprint_estimate(n_obj: int, n_feat: int, t_max: int, cfg: dict, tier: str) -> dict:
    prim = cfg["views"][tier]["primitive"]
    view_widths = [
        sum(1 for f in cfg["views"][tier]["views"][v]["features"]) for v in prim
    ]
    k = cfg["k"]
    tensor = n_obj * n_feat * t_max * 4 + n_obj * n_feat * t_max
    grams = sum(3 * n_obj * w * t_max * 8 for w in view_widths)
    nn = n_obj * k * (4 + 8 + 8 + 8 + 4 + 4)
    objects = n_obj * 512
    peak = tensor + grams + nn + objects
    return {
        "objects": n_obj, "features": n_feat, "t_max": t_max,
        "tensor_bytes": tensor, "gram_bytes": grams, "neighbour_bytes": nn,
        "peak_bytes": peak, "peak_gib": peak / (1024 ** 3),
        "basis": "values+valid+one neighbour index per view, all view grams resident, objects",
    }


# --------------------------------------------------------------------------- #
# metric + retrieval (reuses the within-name exact helpers)
# --------------------------------------------------------------------------- #
def fit_feature_stats(tensors: dict, fit_mask: np.ndarray) -> dict:
    values, valid = tensors["values"], tensors["valid"]
    stats = {}
    for ci, cid in enumerate(tensors["channel_ids"]):
        m = valid[:, ci, :] & fit_mask[:, None]
        vals = values[:, ci, :][m]
        if vals.size == 0:
            raise IntegrityRefusalError(
                f"feature {cid!r} has no valid entry in the fit block",
                [{"kind": "feature_fit_support_empty", "feature": cid}],
            )
        mu, sd = float(np.mean(vals)), float(np.std(vals))
        if not np.isfinite(sd) or sd <= 0:
            raise IntegrityRefusalError(
                f"feature {cid!r} has zero variance over the fit block",
                [{"kind": "feature_fit_zero_variance", "feature": cid}],
            )
        stats[cid] = {"mean": mu, "std": sd, "n_fit": int(vals.size)}
    return {"channels": stats}


def _same_day_columns(objects: pl.DataFrame) -> list:
    by_day: dict[str, list] = {}
    for i, day in enumerate(objects["day"].to_list()):
        by_day.setdefault(day, []).append(i)
    return [np.asarray(by_day[day], dtype=np.int64) for day in objects["day"].to_list()]


def _view_gram(tensors: dict, norm: dict, view_cfg: dict) -> tuple:
    ids = [f["id"] for f in view_cfg["features"]]
    return geo.view_gram(tensors, norm, ids, view_cfg["weights"])


def run_view_search(tensors, norm, view_cfg, *, k, chunk, same_day) -> dict:
    u_z, q_z2, m_valid, _ = _view_gram(tensors, norm, view_cfg)
    res = geo.search_view(u_z, q_z2, m_valid, k=k, radius=view_cfg["radius"], chunk=chunk,
                          same_tape_cols=same_day)
    res["_gram"] = (u_z, q_z2, m_valid)
    return res


def run_balanced_search(tensors, norm, cfg, tier, first_results, *, chunk, same_day) -> dict:
    vcfg = cfg["views"][tier]["views"][BALANCED]
    prim = cfg["views"][tier]["primitive"]
    k, radius = cfg["k"], vcfg["radius"]
    weights = cfg["views"][tier]["view_weights"]
    n_obj = tensors["values"].shape[0]
    out = {
        "nn_idx": np.full((n_obj, k), -1, dtype=np.int32),
        "nn_dist": np.full((n_obj, k), np.nan, dtype=np.float64),
        "density": np.zeros(n_obj, dtype=np.int64),
        "kth_dist": np.full(n_obj, np.nan, dtype=np.float64),
        "n_comparable": np.zeros(n_obj, dtype=np.int64),
        "n_common_views": np.zeros(n_obj, dtype=np.int64),
    }
    scale_total = float(sum(weights.values()))
    for s in range(0, n_obj, chunk):
        e = min(n_obj, s + chunk)
        stack = []
        for vname in prim:
            u_z, q_z2, m_valid = first_results[vname]["_gram"]
            dist = geo.masked_sq_dists(u_z, q_z2, m_valid, slice(s, e))
            for rr in range(e - s):
                cols = same_day[rr + s]
                if cols.size:
                    dist[rr, cols] = np.inf
            stack.append((dist, first_results[vname]["_scale"]))
        arr = np.stack([d / sc for d, sc in stack])
        finite = np.isfinite(arr)
        cnt = finite.sum(axis=0)
        with np.errstate(invalid="ignore"):
            bal = np.where(finite, arr, 0.0).sum(axis=0) / np.maximum(cnt, 1)
        bal = np.where(cnt >= cfg["views"][tier]["min_common"], bal, np.inf)
        bal = bal / (scale_total / len(prim)) if scale_total > 0 else bal
        out["density"][s:e] = np.count_nonzero(bal <= radius, axis=1)
        out["n_comparable"][s:e] = np.count_nonzero(np.isfinite(bal), axis=1)
        out["n_common_views"][s:e] = finite.any(axis=2).sum(axis=0)
        idx, val = geo.topk_from_dist(bal, k)
        out["nn_idx"][s:e] = idx
        out["nn_dist"][s:e] = val
        cnt_fin = np.sum(np.isfinite(val), axis=1)
        for rr in range(e - s):
            c = int(cnt_fin[rr])
            if c:
                out["kth_dist"][s + rr] = val[rr, c - 1]
    return out


def recurrence_stats(nn_idx: np.ndarray, days: np.ndarray, labels: np.ndarray) -> dict:
    n_obj, k = nn_idx.shape
    mutual = np.zeros(n_obj, dtype=np.int32)
    day_spread = np.zeros(n_obj, dtype=np.int32)
    sets = [{int(x) for x in row if x >= 0} for row in nn_idx]
    for i in range(n_obj):
        mutual[i] = sum(1 for j in sets[i] if i in sets[j])
        day_spread[i] = len({str(days[j]) for j in sets[i]})
    return {"mutual_count": mutual, "neighbour_day_spread": day_spread}


def neighbouring_own_refs(nn_idx: np.ndarray, objects: pl.DataFrame) -> np.ndarray:
    """Each neighbour resolved to its OWN day / clock interval / scale (never the query's)."""
    day = np.asarray(objects["day"].to_list())
    scale = np.asarray(objects["scale"].to_list())
    start = np.asarray(objects["window_start_t"].to_list())
    end = np.asarray(objects["window_end_t"].to_list())
    refs = np.full(nn_idx.shape, "", dtype="U64")
    for i in range(nn_idx.shape[0]):
        for jj in range(nn_idx.shape[1]):
            j = int(nn_idx[i, jj])
            if j >= 0:
                refs[i, jj] = f"{day[j]}|{start[j]}-{end[j]}|s{scale[j]}"
    return refs


def disagreement_block(results: dict, prim: list, tensors: dict, chunk: int,
                       same_day: list) -> dict:
    """Magnitude-vs-shape (and each primitive vs the balanced view) disagreement, measured."""
    out = {}
    pairs = [(a, b) for i, a in enumerate(prim) for b in prim[i + 1:]]
    pairs += [(a, BALANCED) for a in prim if BALANCED in results]
    for a, b in pairs:
        if a not in results or b not in results:
            continue
        ia, ib = results[a]["nn_idx"], results[b]["nn_idx"]
        n_obj, k = ia.shape
        overlap = np.zeros(n_obj, dtype=np.int32)
        for i in range(n_obj):
            sa = {int(x) for x in ia[i] if x >= 0}
            sb = {int(x) for x in ib[i] if x >= 0}
            overlap[i] = len(sa & sb)
        key = f"{a}__vs__{b}"
        out[key] = {
            "mean_neighbour_overlap_count": float(overlap.mean()),
            "overlap_share_of_k": float(overlap.mean() / max(1, k)),
            "objects_with_zero_overlap": int((overlap == 0).sum()),
            "kth_dist_spearman": geo.spearman(results[a]["kth_dist"], results[b]["kth_dist"]),
            "density_spearman": geo.spearman(results[a]["density"].astype(float),
                                           results[b]["density"].astype(float)),
            "overlap_definition": "|nn_a(idx) cap nn_b(idx)| per object over the published k",
        }
    return out


def surrogate_block(cfg: dict, tier: str, objects: pl.DataFrame, tensors: dict, norm: dict,
                    results: dict, *, chunk: int) -> dict:
    """Matched-surrogate evidence: real neighbour distances versus stratum-matched partners."""
    block = cfg["tiers"][tier]
    scfg = block["surrogate"]
    n_obj = tensors["values"].shape[0]
    day = np.asarray(objects["day"].to_list())
    query_t = np.asarray(objects["query_t"].to_list())
    pop_min = np.asarray(objects["population_n_min"].to_list(), dtype=np.float64)
    blk = np.asarray(objects["block"].to_list())
    bucket = np.floor(query_t / scfg["clock_bucket_size"]).astype(np.int64)
    deciles = _deciles(pop_min)
    strata: dict[tuple, list] = {}
    for i in range(n_obj):
        key = []
        if "block" in scfg["axes"]:
            key.append(str(blk[i]))
        if "clock_bucket" in scfg["axes"]:
            key.append(int(bucket[i]))
        if "population_decile" in scfg["axes"]:
            key.append(int(deciles[i]))
        strata.setdefault(tuple(key), []).append(i)
    view_names = [v for v in results if v != BALANCED]
    out: dict[str, dict] = {}
    seed = cfg["seed"]
    for vname in view_names:
        vcfg = cfg["views"][tier]["views"][vname]
        u_z, q_z2, m_valid, _ = _view_gram(tensors, norm, vcfg)
        scale = results[vname]["_scale"]
        real_kth = results[vname]["kth_dist"] / scale
        sur_dist = np.full((n_obj, scfg["draws"]), np.nan, dtype=np.float64)
        empty = 0
        for i in range(n_obj):
            key = []
            if "block" in scfg["axes"]:
                key.append(str(blk[i]))
            if "clock_bucket" in scfg["axes"]:
                key.append(int(bucket[i]))
            if "population_decile" in scfg["axes"]:
                key.append(int(deciles[i]))
            cands = np.asarray([j for j in strata[tuple(key)]
                                if j != i and day[j] != day[i]], dtype=np.int64)
            if cands.size == 0:
                empty += 1
                continue
            picks = np.asarray(
                [int(cands[splitmix64(seed, vname, i, d) % cands.size])
                 for d in range(scfg["draws"])], dtype=np.int64,
            )
            draw = geo.masked_sq_dists(u_z[i:i + 1], q_z2[picks], m_valid[picks],
                                       slice(0, 1))[0] / scale
            sur_dist[i] = draw
        ok = np.isfinite(real_kth) & np.isfinite(sur_dist).any(axis=1)
        sur_min = np.nanmin(np.where(np.isfinite(sur_dist), sur_dist, np.inf), axis=1)
        real = real_kth[ok]
        sur = sur_min[ok]
        out[vname] = {
            "matched_axes": list(scfg["axes"]),
            "clock_bucket_size": scfg["clock_bucket_size"],
            "draws": scfg["draws"],
            "objects_with_surrogate": int(ok.sum()),
            "objects_with_empty_stratum": int(empty),
            "real_kth_quantiles": geo.quantiles(real),
            "surrogate_min_quantiles": geo.quantiles(sur),
            "real_below_surrogate_min_share": (
                float((real < sur).mean()) if real.size else None
            ),
            "median_real_over_surrogate": (
                float(np.median(real) / np.median(sur))
                if real.size and np.median(sur) > 0 else None
            ),
            "note": "no gate is evaluated; a partner is drawn only inside the declared matched "
                    "strata and the same-day exclusion is applied",
        }
    return out


def _deciles(values: np.ndarray) -> np.ndarray:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return np.zeros(values.size, dtype=np.int64)
    edges = np.unique(np.quantile(finite, np.linspace(0, 1, 11)[1:-1]))
    return np.searchsorted(edges, values, side="right").astype(np.int64)


# --------------------------------------------------------------------------- #
# outputs
# --------------------------------------------------------------------------- #
def _write_refused(out_evidence: Path, tier: str, cfg, problems: list) -> None:
    report = {
        "report_version": "freeze-r.race-geometry.v0",
        "tier": tier,
        "node_id": TIER_NODE[tier],
        "status": "REFUSED",
        "claims": "none",
        "refusals": problems,
        "freeze_r": None if cfg is None else {"version": cfg.get("race_matrix_version")},
    }
    geo.write_json(out_evidence / REPORT_NAME, report)
    manifest = {
        "node_id": TIER_NODE[tier],
        "tier": tier,
        "version": None if cfg is None else cfg.get("race_matrix_version"),
        "status": "REFUSED",
        "refusals": problems,
        "payload_sha256": {},
        "code_sha256": geo.sha256_file(HERE),
    }
    manifest["core_hash"] = geo.sha256_bytes(geo.canon_bytes(manifest))
    geo.write_json(out_evidence / MANIFEST_NAME, manifest)


def write_tier_outputs(cfg: dict, tier: str, corpus: dict, extract: dict, results: dict,
                       norm: dict, report: dict, *, out_dir: Path, out_evidence: Path) -> dict:
    payloads = {}

    def reg(name, path: Path, rows=None):
        payloads[name] = {"path": str(path.relative_to(out_dir)) if out_dir in path.parents
                          else path.name, "rows": rows, "sha256": geo.sha256_file(path)}

    objects_path = out_dir / "objects.parquet"
    geo.write_parquet(objects_path, extract["objects"])
    reg("objects", objects_path, extract["objects"].height)
    geo.write_json(out_dir / "objects.meta.json", {
        "keys": ["object_id"],
        "sort_order": ["day", "scale", "window_start_t"],
        "units": {"window_start_t": "et minute/checkpoint label",
                  "window_end_t": "et minute/checkpoint label",
                  "query_t": "decision clock label"},
        "note": "day/clock interval/scale/raw-lane sha/population digest are identity references; "
                "they are never distance channels or model inputs",
        "population_digest_query": "sha256 over the newline-joined sorted population tickers "
                                   "observed at query_t; a deterministic commitment to the exact "
                                   "cross-section membership",
    })

    chan_index = {cid: i for i, cid in enumerate(extract["channel_ids"])}
    view_names = list(results)
    for vname in view_names:
        vcfg = cfg["views"][tier]["views"][vname]
        ids = [f["id"] for f in vcfg["features"]]
        cols = [chan_index[c] for c in ids]
        arrs = {
            "values": extract["values"][:, cols, :].copy(),
            "valid": extract["valid"][:, cols, :].copy(),
            "channels": np.asarray(ids, dtype="U64"),
            "axis_labels": extract["axis_labels"],
        }
        path = out_dir / f"view_{vname}.tensor.npz"
        geo.deterministic_npz(path, arrs)
        reg(f"view_{vname}_tensor", path, int(arrs["values"].shape[0]))
        geo.write_json(out_dir / f"view_{vname}.tensor.meta.json", {
            "view": vname,
            "features": vcfg["features"],
            "metric": METRIC,
            "metric_weights": vcfg["weights"],
            "radius": vcfg["radius"],
            "low_density_max": vcfg["low_density_max"],
            "uncertain_kth_dist_min": vcfg["uncertain_kth_dist_min"],
            "units": "standardized_channel_l2 (sqrt of the sum of squared z differences)",
            "fitted_feature_stats": {
                c: norm["channels"][c] for c in ids if c in norm["channels"]
            },
            "layout": {"values": "float32 (N,C,T) 0.0 where valid=False",
                       "valid": "bool (N,C,T) padding is a COMPUTATIONAL zero, never a market "
                                "zero and never an unsupported channel",
                       "channels": "unicode (C,)",
                       "axis_labels": "int32 (N,T) real clock label per position, -1 = padding"},
            "object_order": "objects.parquet row order",
        })
    for vname in view_names:
        res = results[vname]
        rec = recurrence_stats(res["nn_idx"], np.asarray(extract["objects"]["day"].to_list()),
                               np.asarray(extract["objects"]["query_t"].to_list()))
        refs = neighbouring_own_refs(res["nn_idx"], extract["objects"])
        path = out_dir / f"view_{vname}.nn.npz"
        arrs = {
            "nn_idx": res["nn_idx"], "nn_dist": res["nn_dist"], "density": res["density"],
            "kth_dist": res["kth_dist"], "n_comparable": res["n_comparable"],
            "n_common_views": res["n_common_views"],
            "mutual_count": rec["mutual_count"],
            "neighbour_day_spread": rec["neighbour_day_spread"],
            "neighbour_refs": refs,
        }
        geo.deterministic_npz(path, arrs)
        reg(f"view_{vname}_nn", path, int(res["nn_idx"].shape[0]))
        low = res["density"] <= cfg["views"][tier]["views"][vname]["low_density_max"]
        uncertain = ~np.isfinite(res["kth_dist"]) | (
            res["kth_dist"] > cfg["views"][tier]["views"][vname]["uncertain_kth_dist_min"]
        )
        unique = res["density"] == 0
        report["views"][vname] = {
            "k": cfg["k"], "radius": cfg["views"][tier]["views"][vname]["radius"],
            "units": ("standardized_channel_l2" if vname != BALANCED
                      else "standardized_view_distance"),
            "nn_idx": "int32 (N,k) objects.parquet row indices, -1 = no such neighbour",
            "nn_dist": "float64 (N,k) squared distance in the view's own units, NaN = absent",
            "self_and_same_day_excluded": True,
            "density": "count of objects at or below the frozen radius, self/day excluded",
            "low_density_share": float(low.mean()),
            "uncertain_share": float(uncertain.mean()),
            "unique_mass_share": float(unique.mean()),
            "unique_mass_n": int(unique.sum()),
            "median_mutual_count": float(np.median(rec["mutual_count"])),
            "share_with_mutual_neighbour": float((rec["mutual_count"] > 0).mean()),
            "neighbour_day_spread_median": float(np.median(rec["neighbour_day_spread"])),
            "objects_with_single_day_neighbourhood": int((rec["neighbour_day_spread"] <= 1).sum()),
            "kth_dist_quantiles": geo.quantiles(res["kth_dist"]),
            "density_quantiles": geo.quantiles(res["density"].astype(float)),
            "low_density_max": cfg["views"][tier]["views"][vname]["low_density_max"],
            "uncertain_kth_dist_min": cfg["views"][tier]["views"][vname][
                "uncertain_kth_dist_min"],
            "fit_feature_ids": [f["id"] for f in cfg["views"][tier]["views"][vname]["features"]],
            "exact_search_rate": 1.0,
        }

    rare_rows = []
    rarest = np.zeros(extract["objects"].height, dtype=bool)
    for vname in view_names:
        rarest |= results[vname]["density"] == 0
    for oi in np.flatnonzero(rarest):
        row = extract["objects"].row(int(oi), named=True)
        keep = {k: row[k] for k in (
            "object_id", "tier", "day", "block", "era", "scale", "window_start_t",
            "window_end_t", "query_t", "population_n_min", "population_n_max",
            "population_digest_query", "quality_class", "quality_reasons", "source_sha256",
            "payload_path", "fit", "retrospective_only",
        )}
        for vname in view_names:
            keep[f"density_{vname}"] = int(results[vname]["density"][oi])
            keep[f"kth_{vname}"] = (float(results[vname]["kth_dist"][oi])
                                    if np.isfinite(results[vname]["kth_dist"][oi]) else None)
        rare_rows.append(keep)
    rare_df = pl.DataFrame(rare_rows) if rare_rows else pl.DataFrame(
        schema={"object_id": pl.String, "tier": pl.String, "day": pl.String}
    )
    rare_path = out_dir / "rare_refs.parquet"
    geo.write_parquet(rare_path, rare_df)
    reg("rare_refs", rare_path, rare_df.height)
    report["rare"] = {
        "objects_with_no_neighbour_in_any_view": int(rarest.sum()),
        "objects_with_no_neighbour_in_every_view": int(
            np.all([results[v]["density"] == 0 for v in view_names], axis=0).sum()
        ) if view_names else 0,
        "policy": "a unique/giant-tail object stays a catalogue row with its own day/clock "
                  "interval/scale/raw-lane sha and population digest; it is never removed for "
                  "being far away, outside a radius or neighbourless",
    }

    geo.write_json(out_evidence / REPORT_NAME, report)
    manifest = {
        "node_id": TIER_NODE[tier],
        "tier": tier,
        "version": cfg["race_matrix_version"],
        "contract": "freeze-r-v0",
        "freeze_r": {"path": cfg["freeze_r_path"], "sha256": cfg["freeze_r_sha256"],
                     "base_matrix_version": cfg["base_matrix_version"],
                     "race_matrix_version": cfg["race_matrix_version"]},
        "parents": report["parents"],
        "code_sha256": geo.sha256_file(HERE),
        "schema": {
            "table": cfg["tiers"][tier]["table"],
            "keys": list(cfg["tiers"][tier]["keys"]),
            "clock_axis": {"column": cfg["tiers"][tier]["clock"],
                           "span": cfg["tiers"][tier]["span"],
                           "axis_values": cfg["tiers"][tier]["axis_values"]},
            "population": cfg["tiers"][tier]["population"],
            "quality": cfg["tiers"][tier]["quality"],
            "scale": cfg["tiers"][tier]["scale"],
            "flow": cfg["tiers"][tier]["flow"],
            "cells": cfg["tiers"][tier]["cells"],
            "surrogate": cfg["tiers"][tier]["surrogate"],
            "views": {v: {"features": [f["id"] for f in cfg["views"][tier]["views"][v]["features"]],
                          "weights": cfg["views"][tier]["views"][v]["weights"],
                          "radius": cfg["views"][tier]["views"][v]["radius"]}
                      for v in cfg["views"][tier]["views"]},
            "metric": METRIC,
            "neighbours_k": cfg["k"],
            "ann_sparse_recall": cfg["ann"],
            "independence": cfg["independence"],
        },
        "keys": {"objects": "object_id", "neighbours": "object row index"},
        "sort_order": {"objects": ["day", "scale", "window_start_t"]},
        "coverage_classes": sorted({str(x) for x in extract["objects"]["block"].to_list()}),
        "selection_classes": report["selection_classes"],
        "payload_sha256": payloads,
        "status": "geometries_built",
        "supersedes": None,
        "withdraws": None,
    }
    manifest["core_hash"] = geo.sha256_bytes(geo.canon_bytes(manifest))
    geo.write_json(out_evidence / MANIFEST_NAME, manifest)
    return payloads


# --------------------------------------------------------------------------- #
# one tier
# --------------------------------------------------------------------------- #
def run_tier(cfg: dict, tier: str, corpus: dict, *, root: Path, out_dir: Path,
             out_evidence: Path, verify: bool, chunk: int, asof_cut, plan_only: bool,
             budget_gib: float) -> dict:
    t0 = time.time()
    problems: list = []
    days = sorted(corpus["days"])
    if not days:
        raise ContractRefusalError("the corpus declares no day")
    for day in days:
        try:
            geo.guard_day(day)  # sealed/reserved days are refused before any path is opened
        except PermissionError as exc:
            raise ContractRefusalError(str(exc)) from exc
    extract = extract_tier(cfg, tier, corpus, days, root=root, verify=verify, problems=problems,
                           budget_gib=budget_gib)
    objects = extract["objects"]
    if asof_cut is not None:
        keep = objects["query_t"] <= int(asof_cut)
        n_drop = int((~keep).sum())
        objects = objects.filter(keep)
        idx = np.flatnonzero(np.asarray(keep.to_list()))
        extract["values"] = extract["values"][idx]
        extract["valid"] = extract["valid"][idx]
        extract["axis_labels"] = extract["axis_labels"][idx]
        extract["objects"] = objects
    else:
        n_drop = 0
    n_obj = extract["values"].shape[0]
    if n_obj < 3:
        raise IntegrityRefusalError(
            f"{tier}: fewer than three objects after the decision cut",
            [{"kind": "object_count_too_small", "tier": tier, "objects": n_obj}],
        )
    block_arr = np.asarray([geo.block_of(d) for d in objects["day"].to_list()])
    fit_mask = np.isin(block_arr, cfg["blocks"]["fit"])
    eval_mask = np.isin(block_arr, cfg["blocks"]["eval"])
    if not fit_mask.any() or not eval_mask.any():
        raise IntegrityRefusalError(
            f"{tier}: the frozen fit/eval blocks do not both cover the objects",
            [{"kind": "split_blocks_uncovered", "tier": tier,
              "fit": int(fit_mask.sum()), "eval": int(eval_mask.sum())}],
        )
    tensors = {"values": extract["values"], "valid": extract["valid"],
               "channel_ids": extract["channel_ids"]}
    norm = fit_feature_stats(tensors, fit_mask)
    same_day = _same_day_columns(objects)
    prim = cfg["views"][tier]["primitive"]
    results: dict[str, dict] = {}
    for vname in prim:
        res = run_view_search(tensors, norm, cfg["views"][tier]["views"][vname], k=cfg["k"],
                              chunk=chunk, same_day=same_day)
        res["n_common_views"] = (res["n_comparable"] > 0).astype(np.int64)
        res["_scale"] = geo.distance_scale(
            res["_gram"][0], res["_gram"][1], res["_gram"][2], np.flatnonzero(fit_mask),
            k=cfg["views"][tier]["views"][vname]["scale_reference"]["k"], chunk=chunk,
            same_tape_cols=same_day,
        )
        results[vname] = res
    if BALANCED in cfg["views"][tier]["views"]:
        results[BALANCED] = run_balanced_search(tensors, norm, cfg, tier, results, chunk=chunk,
                                                same_day=same_day)
        results[BALANCED]["_scale"] = 1.0
    disagree = disagreement_block(results, prim, tensors, chunk, same_day)
    surrogate = surrogate_block(cfg, tier, objects, tensors, norm, results, chunk=chunk)
    report = {
        "report_version": "freeze-r.race-geometry.v0",
        "node_id": TIER_NODE[tier],
        "tier": tier,
        "status": "geometries_built",
        "claims": "measurements only: no gate is evaluated, no outcome/censor/future column is "
                  "read, no ranking of the tape is claimed",
        "race_matrix_version": cfg["race_matrix_version"],
        "mode": cfg["mode"],
        "asof_cut": None if asof_cut is None else int(asof_cut),
        "retrospective_only": cfg["mode"] == "retrospective",
        "objects": n_obj,
        "windows_dropped_by_decision_cut": int(n_drop),
        "scale_ladder": cfg["tiers"][tier]["scale"]["ladder"],
        "anchor_stride": cfg["tiers"][tier]["scale"]["stride"],
        "clock_axis": {"column": cfg["tiers"][tier]["clock"],
                       "axis_values": cfg["tiers"][tier]["axis_values"],
                       "span": cfg["tiers"][tier]["span"]},
        "features": extract["reg"]["all"],
        "flow_rule": FLOW_RULE,
        "fitted_feature_stats": norm["channels"],
        "views": {},
        "disagreement": disagree,
        "surrogate": surrogate,
        "tier_cells": tier_cells(cfg, tier),
        "populations": {
            "label": cfg["tiers"][tier]["population"]["label"],
            "predicates": cfg["tiers"][tier]["population"]["predicates"],
            "note": "the population is the declared eligible cross-section; a row outside it is "
                    "reported as an excluded row and is never re-encoded as a zero",
        },
        "quality": {
            "qualified_label": cfg["tiers"][tier]["quality"]["qualified_label"],
            "reference_label": cfg["tiers"][tier]["quality"]["reference_label"],
            "predicates": cfg["tiers"][tier]["quality"]["predicates"],
            "qualified_objects": int((objects["quality_class"] ==
                                      cfg["tiers"][tier]["quality"]["qualified_label"]).sum()),
            "reference_only_objects": int((objects["quality_class"] ==
                                           cfg["tiers"][tier]["quality"]["reference_label"]).sum()),
            "policy": "a stale/floor-qualified reference day is labelled raw_reference_only and "
                      "kept; the label never removes an object and never enters a distance",
        },
        "day_facts": extract["day_facts"],
        "footprint": extract["footprint"],
        "exactness": {
            "search": "exact_masked_bruteforce_no_ann",
            "index_build": cfg["ann"]["index_build"],
            "exact_search_strata": cfg["ann"]["exact_search_strata"],
            "sparse_exact_recall_floor": cfg["ann"]["sparse_exact_recall_floor"],
            "density_decile_definition": cfg["ann"]["density_decile_definition"],
            "assertion": "no ANN structure exists in this node, so exact recall is 1.0 by "
                         "construction in every density decile (no sampling is used)",
        },
        "independence": cfg["independence"],
        "memory": {"peak_rss_gib": rss_gib(), "budget_gib": budget_gib,
                   "hard_abort_gib": MEMORY_HARD_GIB},
        "runtime_s": None,
        "adaptive_choice_ledger": cfg["ledger"],
        "selection_classes": {
            "scope": corpus.get("scope", "frozen_corpus_days"),
            "days": days if len(days) <= 32 else {"n": len(days),
                                                  "sha256": geo.day_set_sha(days)},
            "asof_cut": None if asof_cut is None else int(asof_cut),
            "retrospective_only": cfg["mode"] == "retrospective",
        },
        "parents": corpus.get("parents", {}),
    }
    report["runtime_s"] = round(time.time() - t0, 3)
    if not plan_only:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_evidence.mkdir(parents=True, exist_ok=True)
        write_tier_outputs(cfg, tier, corpus, extract, results, norm, report, out_dir=out_dir,
                           out_evidence=out_evidence)
    return report


def tier_cells(cfg: dict, tier: str) -> dict:
    block = cfg["tiers"][tier]
    contract = cfg.get("contract_tier_cells", {})
    impl = {}
    for cell in CELLS:
        status = block["cells"][cell]
        if cell == "learned_minute_sequence" and status == "unsupported":
            reason = "the masked-TCN race family is bound to the sequence-archive sibling"
        elif cell == "quotes" and status != "materialized":
            reason = "quote_channel_ready is not established; no quote channel may enter"
        elif status == "unsupported":
            reason = "this tier's payload carries no channel of that kind"
        elif status == "delegated":
            reason = "produced by a sibling node, not here"
        else:
            reason = None
        impl[cell] = {
            "declared_status": status,
            "observation_contract_value": contract.get(cell),
            "reason": reason,
            "materialized_here": status == "materialized",
            "encoded_as_zero": False,
        }
    return impl


# --------------------------------------------------------------------------- #
# physical audit (no frozen record needed, no row values read)
# --------------------------------------------------------------------------- #
def audit_physical(root: Path, manifest_path: Path, *, verify: bool) -> dict:
    schema = geo.load_json(SCHEMA_PATH)
    registry = geo.load_json(REGISTRY_PATH)
    lock = geo.load_json(LOCK_PATH)
    if lock.get("status") != "frozen":
        raise ContractRefusalError("contract_lock.json is not frozen")
    for name, want in (lock.get("hashes") or {}).items():
        if geo.sha256_file(OBS_EVIDENCE / name) != want:
            raise ContractRefusalError(
                f"contract lock mismatch for {name}: the Freeze-O chain moved"
            )
    if not manifest_path.is_file():
        raise ContractRefusalError(f"observation manifest not found: {manifest_path}")
    manifest = geo.load_json(manifest_path)
    payloads = manifest.get("payload_sha256") or {}
    out = {
        "audit_id": "freeze-r.race-geometry.physical-audit",
        "manifest": str(manifest_path), "manifest_sha256": geo.sha256_file(manifest_path),
        "schema_sha256": geo.sha256_file(SCHEMA_PATH),
        "registry_sha256": geo.sha256_file(REGISTRY_PATH),
        "tiers": {}, "notes": [
            "reads parquet footers/schemas only: no row value is materialized",
            "an unsupported channel of a tier is reported, never encoded as a zero",
        ],
    }
    problems: list = []
    for tier in TIER_ORDER:
        table = TIER_TABLE[tier]
        fields = schema["tables"][table]["fields"]
        day_keys = sorted(k.split("/", 1)[1] for k, v in payloads.items()
                          if k.startswith(f"{table}/") and (v or {}).get("path"))
        entry = {
            "table": table,
            "physical_dir": TIER_PHYSICAL_DIR[tier],
            "grain": schema["tables"][table].get("grain"),
            "days_declared": len(day_keys),
            "distance_eligible_columns": {}, "predicate_eligible_columns": {},
            "physically_present": [], "declared_but_absent": [],
            "out_of_span_labels": 0,
        }
        for cname, fa in sorted(fields.items()):
            if (fa["namespace"] in DISTANCE_NAMESPACES and not fa["coordinate_only"]
                    and not fa["retrospective_only"] and fa["prospective_allowed"]
                    and cname not in COORDINATE_DENY
                    and ("all" in fa["supportable_by_tier"] or tier in fa["supportable_by_tier"])
                    and not any(cname.startswith(q) for q in QUOTE_DENY)
                    and not _is_denied(cname, registry, is_predicate=False)):
                entry["distance_eligible_columns"][cname] = fa["supportable_by_tier"]
            if fa["prospective_allowed"] and not _is_denied(cname, registry, is_predicate=True):
                entry["predicate_eligible_columns"][cname] = {
                    "retrospective_only": fa["retrospective_only"],
                    "namespace": fa["namespace"],
                }
        seen_schema = None
        n_rows_declared = 0
        for day in day_keys:
            if len(problems) > 40:
                break
            try:
                geo.guard_day(day)
            except PermissionError as exc:
                problems.append({"kind": "sealed_day_in_manifest", "day": day, "message": str(exc)})
                continue
            info = payloads[f"{table}/{day}"]
            path = geo.resolve_under(root, str(info.get("path")))
            if not path.is_file():
                problems.append({"kind": "payload_absent", "tier": tier, "day": day})
                continue
            if verify and info.get("sha256") and geo.sha256_file(path) != info["sha256"]:
                problems.append({"kind": "payload_sha256_mismatch", "tier": tier, "day": day})
                continue
            sch = pl.scan_parquet(str(path)).collect_schema()
            if seen_schema is None:
                seen_schema = list(sch.names())
                entry["physically_present"] = sorted(seen_schema)
                entry["declared_but_absent"] = sorted(set(fields) - set(seen_schema))
                missing_keys = [k for k in TIER_KEYS[tier] if k not in seen_schema]
                if missing_keys:
                    problems.append({"kind": "physical_key_missing", "tier": tier,
                                     "columns": missing_keys})
            elif list(sch.names()) != seen_schema:
                problems.append({"kind": "payload_schema_drift", "tier": tier, "day": day})
            n_rows = int(pl.scan_parquet(str(path)).select(pl.len()).collect().item())
            if info.get("rows") is not None and int(info["rows"]) != n_rows:
                problems.append({"kind": "payload_row_mismatch", "tier": tier, "day": day,
                                 "declared": int(info["rows"]), "observed": n_rows})
            n_rows_declared += n_rows
        entry["rows_total"] = n_rows_declared
        out["tiers"][tier] = entry
    out["problems"] = problems
    out["status"] = "REFUSED" if problems else "ok"
    return out


# --------------------------------------------------------------------------- #
# config skeleton
# --------------------------------------------------------------------------- #
def config_skeleton() -> dict:
    """The REQUIRED race_geometry field types, every numeric choice UNBOUND (null)."""
    def predicate():
        return {"column": None, "op": None, "value": None}

    def feature():
        return {"id": None, "kind": None, "of": None, "column": None, "aggregate": None,
                "position": None, "transform": None,
                "reference": {"of": None, "rule": None}, "valid_when": predicate()}

    def view():
        return {"features": [feature()], "metric": None, "metric_weights": {},
                "scale_reference": {"statistic": None, "k": None}, "radius": None,
                "low_density_max": None, "uncertain_kth_dist_min": None}

    def balanced():
        return {"aggregation": None, "min_common_views": None, "radius": None,
                "low_density_max": None, "uncertain_kth_dist_min": None,
                "balanced_reference_tuned_to_best_geometry": None}

    def tier(t: str):
        axis = {"column": None, "unit": None, "frozen": None}
        if TIER_CLOCK_KIND[t] == "minute":
            axis.update({"step": None, "span": None})
        else:
            axis.update({"axis_values": None})
        return {
            "table": None, "keys": None, "clock_axis": axis,
            "population": {"label": None, "predicates": [predicate()], "frozen": None},
            "quality": {"qualified_label": None, "reference_label": None,
                        "predicates": [], "frozen": None},
            "scale": {"unit": None, "ladder": None, "anchor": None, "alignment": None,
                      "anchor_stride": None, "frozen": None},
            "flow": {"rule": None, "order": None, "frozen": None},
            "cells": dict.fromkeys(CELLS),
            "surrogate": {"matched_axes": None, "clock_bucket_size": None, "draws": None,
                          "density_decile_definition": None, "frozen": None},
        }

    return {
        "race_geometry": {
            "race_matrix_version": None,
            "frozen": None,
            "tiers": {t: tier(t) for t in TIER_ORDER},
            "views": {t: {"magnitude_dominant": view(),
                          "normalized_shape_dominant": view(),
                          "balanced_multichannel_reference": balanced()}
                      for t in TIER_ORDER},
            "view_weights": {t: dict.fromkeys(PRIMITIVE_VIEWS) for t in TIER_ORDER},
            "neighbours_k": None,
            "density_decile_definition": None,
            "ann_sparse_recall": {"index_build": None, "exact_search_strata": None,
                                  "sparse_exact_recall_floor": None, "frozen": None},
            "independence": {"unit": None, "exclude_same_unit": None, "frozen": None},
            "adaptive_choice_ledger": {"path": None, "sha256": None,
                                       "covers_race_choices": None},
            "geometry_gates": {},
            "_note": "template_only record skeleton: every null is UNBOUND and refuses. A field "
                     "freezes only with a non-null value bound to the outcome-blind corpus "
                     "report; no value here is a default or a finding.",
        }
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _tier_paths(base_out: Path, base_evidence: Path, tier: str) -> tuple[Path, Path]:
    return base_out / tier, base_evidence / tier


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Freeze-R race geometry (Stage 2: three independent race tiers)"
    )
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="absolute observation data root")
    ap.add_argument("--manifest", help="observation node manifest.json")
    ap.add_argument("--freeze-r", help="the frozen Freeze-R record (with race_geometry)")
    ap.add_argument("--tier", choices=TIER_ORDER, help="one race tier")
    ap.add_argument("--all-tiers", action="store_true",
                    help="build each tier as its own independent node (never merged)")
    ap.add_argument("--out", default=str(DEFAULT_OUT_BASE), help="output root (tier subdir)")
    ap.add_argument("--out-evidence", default=str(DEFAULT_EVIDENCE_BASE))
    ap.add_argument("--asof-cut", type=int, default=None,
                    help="prospective read: last included clock label; default = retrospective")
    ap.add_argument("--chunk-rows", type=int, default=512, help="query chunk (bounded memory)")
    ap.add_argument("--memory-budget-gib", type=float, default=None,
                    help="peak footprint budget; default = the observation contract soft limit")
    ap.add_argument("--no-verify-payload-sha", action="store_true",
                    help="skip only the per-day payload sha256 re-read")
    ap.add_argument("--plan-only", action="store_true",
                    help="validate, extract and measure without writing payloads")
    ap.add_argument("--audit-physical", action="store_true",
                    help="audit the physical race payloads against the frozen schema")
    ap.add_argument("--print-config-skeleton", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return run_selftest()
    if args.print_config_skeleton:
        print(json.dumps(config_skeleton(), indent=1, sort_keys=True))
        return 0
    if args.audit_physical:
        if not args.manifest:
            return die("--manifest is required for --audit-physical")
        try:
            audit = audit_physical(Path(args.root), Path(args.manifest),
                                   verify=not args.no_verify_payload_sha)
        except (ContractRefusalError, IntegrityRefusalError) as exc:
            return die("race geometry refusal: " + str(exc), 2)
        out_dir = Path(args.out_evidence)
        out_dir.mkdir(parents=True, exist_ok=True)
        geo.write_json(out_dir / "physical_audit.json", audit)
        print(json.dumps({
            "status": audit["status"],
            "tiers": {t: {"days": v["days_declared"], "rows": v["rows_total"],
                          "declared_but_absent": v["declared_but_absent"],
                          "distance_eligible": len(v["distance_eligible_columns"])}
                      for t, v in audit["tiers"].items()},
            "problems": audit["problems"][:5],
        }, indent=1)[:2500])
        return 0 if audit["status"] == "ok" else 3
    if not args.manifest or not args.freeze_r:
        return die("--manifest and --freeze-r are required (no default frozen record exists)")
    tiers = list(TIER_ORDER) if args.all_tiers else ([args.tier] if args.tier else [])
    if not tiers:
        return die("select --tier <name> or --all-tiers")
    mode = "retrospective" if args.asof_cut is None else "prospective"
    try:
        schema = geo.load_json(SCHEMA_PATH)
        registry = geo.load_json(REGISTRY_PATH)
        r = geo.load_freeze_r(Path(args.freeze_r), schema=schema, registry=registry)
        cfg = load_race_config(r["raw"], r, schema=schema, registry=registry, mode=mode)
    except ContractRefusalError as exc:
        return die("race geometry refusal (contract): " + str(exc), 2)
    except IntegrityRefusalError as exc:
        return die("race geometry refusal (integrity): " + str(exc), 2)
    cfg["freeze_r_path"] = str(Path(args.freeze_r).resolve())
    cfg["freeze_r_sha256"] = geo.sha256_file(Path(args.freeze_r))
    cfg["base_matrix_version"] = r["matrix_version"]
    cfg["contract_tier_cells"] = _contract_cells()
    budget = args.memory_budget_gib
    if budget is None:
        budget = float(
            (geo.load_json(OBS_CONTRACT_PATH).get("resource_policy") or {}).get("soft_rss_gib", 15)
        )
    root = Path(args.root)
    base_out, base_evidence = Path(args.out), Path(args.out_evidence)
    if not base_out.is_absolute():
        base_out = ROOT / base_out
    if not base_evidence.is_absolute():
        base_evidence = ROOT / base_evidence
    first_bad = 0
    for tier in tiers:
        out_dir, out_evidence = _tier_paths(base_out, base_evidence, tier)
        try:
            corpus = geo.load_corpus(root, Path(args.manifest), r,
                                   verify=not args.no_verify_payload_sha)
            corpus["scope"] = r["corpus"].get("scope")
            corpus["parents"] = {
                "observation_manifest": {
                    "source_uri": Path(corpus["manifest_path"]).as_uri(),
                    "sha256": corpus["manifest_sha256"], "tracked": False},
                "blind_corpus_report": {"source_uri": Path(corpus["blind_path"]).as_uri(),
                                        "sha256": corpus["blind_sha256"], "tracked": True},
                "observation_schema": {"source_uri": SCHEMA_PATH.as_uri(),
                                       "sha256": geo.sha256_file(SCHEMA_PATH), "tracked": True},
                "observation_contract": {"source_uri": OBS_CONTRACT_PATH.as_uri(),
                                         "sha256": geo.sha256_file(OBS_CONTRACT_PATH),
                                         "tracked": True},
                "causal_registry": {"source_uri": REGISTRY_PATH.as_uri(),
                                    "sha256": geo.sha256_file(REGISTRY_PATH), "tracked": True},
                "contract_lock": {"source_uri": LOCK_PATH.as_uri(),
                                  "sha256": geo.sha256_file(LOCK_PATH), "tracked": True},
                "adaptive_choice_ledger": {"source_uri": Path(cfg["ledger"]["path"]).as_uri(),
                                           "sha256": cfg["ledger"]["sha256"], "tracked": True},
            }
            race_days = _race_payload_days(corpus, tier)
            if race_days != sorted(corpus["days"]):
                raise IntegrityRefusalError(
                    f"{tier}: the race payload day set disagrees with the corpus day set",
                    [{"kind": "race_day_set_disagrees", "tier": tier,
                      "corpus_days": len(corpus["days"]), "race_days": len(race_days),
                      "missing": sorted(set(corpus["days"]) - set(race_days))[:10]}],
                )
            report = run_tier(cfg, tier, corpus, root=root, out_dir=out_dir,
                              out_evidence=out_evidence,
                              verify=not args.no_verify_payload_sha,
                              chunk=int(args.chunk_rows), asof_cut=args.asof_cut,
                              plan_only=args.plan_only, budget_gib=budget)
        except ContractRefusalError as exc:
            print(f"race geometry refusal (contract) [{tier}]: {exc}", file=sys.stderr)
            first_bad = first_bad or 2
            continue
        except IntegrityRefusalError as exc:
            print(f"race geometry refusal (integrity) [{tier}]: {exc}", file=sys.stderr)
            _write_refused(out_evidence, tier, cfg, exc.problems)
            first_bad = first_bad or 3
            continue
        print(json.dumps({
            "tier": tier, "status": report["status"], "objects": report["objects"],
            "views": sorted(report["views"]), "exactness": report["exactness"]["search"],
            "out": str(out_dir),
        }, indent=1)[:2000])
    return first_bad


def _race_payload_days(corpus: dict, tier: str) -> list:
    prefix = f"{TIER_TABLE[tier]}/"
    return sorted(k.split("/", 1)[1] for k, v in corpus["payloads"].items()
                  if k.startswith(prefix) and (v or {}).get("path"))


def _contract_cells() -> dict:
    try:
        contract = geo.load_json(OBS_CONTRACT_PATH)
    except Exception:  # pragma: no cover
        return {}
    matrix = contract.get("tier_support_matrix") or {}
    return {
        "broad_minute": {c: matrix.get(TIER_CONTRACT_KEY["broad_minute"], {}).get(
            {"magnitude_shape_duration": "magnitude_shape_duration",
             "activity_prints": "activity_prints", "quotes": "quotes",
             "learned_minute_sequence": "learned_minute_sequence"}[c])
            for c in CELLS},
        "candidate_net": {c: matrix.get(TIER_CONTRACT_KEY["candidate_net"], {}).get(
            {"magnitude_shape_duration": "magnitude_shape_duration",
             "activity_prints": "activity_prints", "quotes": "quotes",
             "learned_minute_sequence": "learned_minute_sequence"}[c])
            for c in CELLS},
        "checkpoint": {c: matrix.get(TIER_CONTRACT_KEY["checkpoint"], {}).get(
            {"magnitude_shape_duration": "magnitude_shape_duration",
             "activity_prints": "activity_prints", "quotes": "quotes",
             "learned_minute_sequence": "learned_minute_sequence"}[c])
            for c in CELLS},
    }


# --------------------------------------------------------------------------- #
# synthetic regression (masking / discretization / refusals / outputs)
# --------------------------------------------------------------------------- #
def run_selftest() -> int:
    """Synthetic fixtures only: this proves the code paths, it reports no finding."""
    import tempfile

    checks: list[dict] = []

    def chk(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail or {}})

    schema = geo.load_json(SCHEMA_PATH)
    registry = geo.load_json(REGISTRY_PATH)

    def race_block(tier, span, ladder, axis_values=None):
        axis = {"column": TIER_CLOCK[tier], "unit": "minute", "frozen": True}
        if axis_values is None:
            axis.update({"step": 1, "span": span})
        else:
            axis.update({"axis_values": axis_values})
        pop_cols = {"broad_minute": {"column": "known_by_t", "op": "eq", "value": True},
                    "candidate_net": {"column": "in_prospective_roster", "op": "eq", "value": True},
                    "checkpoint": {"column": "roster_state", "op": "eq",
                                   "value": "observed_by_t"}}[tier]
        return {
            "table": TIER_TABLE[tier], "keys": list(TIER_KEYS[tier]), "clock_axis": axis,
            "population": {"label": f"{tier}_selftest", "predicates": [pop_cols],
                           "frozen": True},
            "quality": {"qualified_label": "quality_qualified",
                        "reference_label": "raw_reference_only",
                        "predicates": [{"column": "known_by_t", "op": "eq", "value": True}],
                        "frozen": True},
            "scale": {"unit": TIER_CLOCK_KIND[tier], "ladder": ladder,
                      "anchor": ANCHOR, "alignment": ALIGNMENT, "anchor_stride": 1,
                      "frozen": True},
            "flow": {"rule": FLOW_RULE, "order": 1, "frozen": True},
            "cells": {"magnitude_shape_duration": "materialized",
                      "activity_prints": "unsupported", "quotes": "unsupported",
                      "learned_minute_sequence": "unsupported"},
            "surrogate": {"matched_axes": ["block", "clock_bucket"], "clock_bucket_size": 2,
                          "draws": 3, "density_decile_definition": DECILE_DEF, "frozen": True},
        }

    def feature_level(fid, column, agg, position=None, transform="identity", valid_col=None):
        return {"id": fid, "kind": "level", "of": None, "column": column, "aggregate": agg,
                "position": position, "transform": transform, "reference": None,
                "valid_when": {"column": valid_col or column, "op": "is_not_null"}}

    def view(features, radius=1e9):
        return {"features": features, "metric": METRIC,
                "metric_weights": {f["id"]: 1.0 for f in features},
                "scale_reference": {"statistic": SCALE_STATISTIC, "k": 2},
                "radius": radius, "low_density_max": 0, "uncertain_kth_dist_min": 1e9}

    def flow_of(src):
        return {"id": f"{src}_flow", "kind": "flow", "of": src, "column": None,
                "aggregate": None, "position": None, "transform": None, "reference": None,
                "valid_when": None}

    def ratio_feature(fid, column, position=0.25):
        return {"id": fid, "kind": "level", "of": None, "column": column,
                "aggregate": "quantile", "position": position,
                "transform": "log_ratio_to_reference",
                "reference": {"of": column, "rule": "cross_section_median_at_same_t"},
                "valid_when": {"column": column, "op": "is_not_null"}}

    def views_for(tier):
        """Tier-appropriate synthetic feature sets (the tier tables carry different channels)."""
        price = {"broad_minute": "px", "candidate_net": "px", "checkpoint": "px_T"}[tier]
        mag = [feature_level(f"{price}_q50", price, "quantile", 0.5),
               feature_level(f"{price}_mean", price, "mean"),
               flow_of(f"{price}_mean")]
        shape = [ratio_feature(f"{price}_ratio", price),
                 feature_level("age_log", "age_min", "mean", transform="log1p",
                               valid_col="age_min")]
        return {MAGNITUDE: view(mag), SHAPE: view(shape)}

    def race_section(**over):
        sec = {
            "race_matrix_version": "selftest-race-v0", "frozen": True,
            "tiers": {
                "broad_minute": race_block("broad_minute", [570, 575], [3, 5]),
                "candidate_net": race_block("candidate_net", [570, 575], [3]),
                "checkpoint": race_block("checkpoint", None, [3],
                                         axis_values=[575, 580, 585, 590, 595, 600, 615, 630,
                                                      645, 660, 690, 720]),
            },
            "views": {
                t: views_for(t) | {
                    BALANCED: {"aggregation": BALANCED_DECL, "min_common_views": 2,
                               "radius": 1e9, "low_density_max": 0,
                               "uncertain_kth_dist_min": 1e9,
                               "balanced_reference_tuned_to_best_geometry": False}}
                for t in TIER_ORDER
            },
            "view_weights": {t: dict.fromkeys((MAGNITUDE, SHAPE), 1.0)
                             for t in TIER_ORDER},
            "neighbours_k": PUBLISHED_K,
            "density_decile_definition": DECILE_DEF,
            "ann_sparse_recall": {"index_build": ANN_INDEX_BUILD,
                                  "exact_search_strata": ANN_STRATA,
                                  "sparse_exact_recall_floor": ANN_RECALL_FLOOR,
                                  "frozen": True},
            "independence": {"unit": "day", "exclude_same_unit": True, "frozen": True},
            "adaptive_choice_ledger": {"path": "__LEDGER__", "sha256": "__LEDGER_SHA__",
                                       "covers_race_choices": True},
            "geometry_gates": {},
        }
        for path_keys, value in over.items():
            node, keys = sec, path_keys.split(".")
            for kk in keys[:-1]:
                node = node[kk]
            node[keys[-1]] = value
        return sec

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        ledger = tmp / "race_choice_ledger.json"
        geo.write_json(ledger, {"entries": [{"choice_id": "race.scale_ladder",
                                           "affected_node": "geometry.race"}]})
        ledger_sha = geo.sha256_file(ledger)

        def race_sec(sec_over=None):
            sec = race_section(**(sec_over or {}))
            sec["adaptive_choice_ledger"]["path"] = str(ledger)
            sec["adaptive_choice_ledger"]["sha256"] = ledger_sha
            return sec

        # PRIVATE TEST DOUBLE of the shared loader's OUTPUT SHAPE.  It is not a record and it
        # never goes through load_freeze_r: this module owns and validates its own race_geometry
        # section, and the shared loader is exercised only by the CLI draft/partial refusals
        # below.  No record on disk can pass the shared loader without a genuine full canonical
        # corpus, so no positive record is fabricated here.
        def r_double(sec_over=None, mode="retrospective", **over):
            double = {"raw": {"race_geometry": race_sec(sec_over)},
                      "neighbours_k": PUBLISHED_K, "seed": 7,
                      "blocks": {"fit": ["block1"], "eval": ["block2"]},
                      "matrix_version": "private-test-double"}
            double.update(over)
            if mode == "retrospective" and "neighbours_k" in over:
                double.pop("mode", None)
            return double

        def cfg_from(sec_over=None, mode="retrospective", **over):
            double = r_double(sec_over, mode=mode, **over)
            return load_race_config(double["raw"], double, schema=schema, registry=registry,
                                    mode=mode)

        def refuses_cfg(sec_over=None, mode="retrospective", **over):
            try:
                cfg_from(sec_over, mode=mode, **over)
                return None
            except ContractRefusalError as exc:
                return str(exc)

        # ---- this module's own race contract refusals --------------------- #
        try:
            load_race_config({}, r_double(), schema=schema, registry=registry,
                             mode="retrospective")
            chk("missing_race_section_refused", False)
        except ContractRefusalError as exc:
            chk("missing_race_section_refused", "race_geometry" in str(exc), {"m": str(exc)[:80]})

        msg = refuses_cfg({"neighbours_k": 50})
        chk("k_not_100_refused", msg is not None and "nearest-100" in msg)

        msg = refuses_cfg({"views.broad_minute.magnitude_dominant.features": [
            {"id": "q", "kind": "level", "of": None, "column": "quote_mid",
             "aggregate": "mean", "position": None, "transform": "identity",
             "reference": None, "valid_when": {"column": "px", "op": "is_not_null"}}]})
        chk("quote_channel_refused", msg is not None and "quote" in msg.lower())

        msg = refuses_cfg({"views.checkpoint.magnitude_dominant.features": [
            {"id": "ch", "kind": "level", "of": None, "column": "hi",
             "aggregate": "mean", "position": None, "transform": "identity",
             "reference": None, "valid_when": {"column": "px_T", "op": "is_not_null"}}]})
        chk("retrospective_channel_refused", msg is not None and "eligible prospective" in msg)

        msg = refuses_cfg({"views.broad_minute.magnitude_dominant.features": [
            feature_level("pos_null", "px", "quantile", None)]})
        chk("unbound_ruler_refused", msg is not None and "UNBOUND" in msg)

        msg = refuses_cfg({"tiers.checkpoint.cells.quotes": "materialized"})
        chk("materialized_quote_cell_refused", msg is not None and "quote" in msg.lower())

        msg = refuses_cfg({"views.broad_minute.learned_sequence": {"features": []}})
        chk("learned_view_refused", msg is not None and "five hand views" in msg)

        msg = refuses_cfg({"view_weights.broad_minute": {MAGNITUDE: 1.0, SHAPE: 3.0}})
        chk("non_equal_view_weights_refused", msg is not None and "EQUAL" in msg)

        msg = refuses_cfg({"tiers.broad_minute.quality.predicates": [
            {"column": "day_high_raw", "op": "is_not_null"}]}, mode="prospective")
        chk("retrospective_quality_predicate_refused_prospective",
            msg is not None and "retrospective" in msg)

        # the private double DOES bind (synthetic choices, synthetic corpus) ------------ #
        sec_path = tmp / "race_section.json"
        geo.write_json(sec_path, race_sec())
        cfg = cfg_from()
        cfg.update({"freeze_r_path": str(sec_path), "freeze_r_sha256": geo.sha256_file(sec_path),
                    "base_matrix_version": "private-test-double"})
        chk("private_double_accepted", cfg["k"] == PUBLISHED_K and len(cfg["tiers"]) == 3)
        chk("no_record_class_in_cfg",
            not any(k in cfg for k in ("record_class", "test_only", "fixture_reason")))

        def write_day(tier, day, frame, directory):
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{day}.parquet"
            frame.write_parquet(path)
            return {"path": str(path.relative_to(root)),
                    "rows": frame.height, "sha256": geo.sha256_file(path)}

        root = tmp / "obs"
        d1, d2 = "2023-06-28", "2025-02-03"
        d3 = "2023-07-05"
        payloads: dict = {}
        idx = np.arange(570, 576)
        for day, base_px in ((d1, 1.0), (d2, 2.0), (d3, 3.0)):
            rows = []
            for t in idx:
                for n in range(4):
                    known = not (n == 3 and t == 575)
                    rows.append({
                        "day": day, "t": int(t), "ticker": f"S{n}",
                        "px": (base_px + n * 0.5) if known else None,
                        "gain": ((n * 0.25 + (t - 570) * 0.1) if known else None),
                        "age_min": (t - 570) if known else None,
                        "known_by_t": known, "session_end": 575,
                        "source_sha256": "a" * 64,
                    })
            frame = pl.DataFrame(rows)
            payloads[f"race_minute_full/{day}"] = write_day(
                "race_minute_full", day, frame, root / "race.minute_full" / "month=x")
            rows_n = []
            for t in idx:
                for n in range(4):
                    known = not (n == 3 and t == 575)
                    rows_n.append({
                        "day": day, "t": int(t), "ticker": f"S{n}",
                        "px": (base_px + n * 0.5) if known else None,
                        "age_min": (t - 570) if known else None,
                        "in_prospective_roster": n != 2, "known_by_t": known,
                        "session_end": 575, "source_sha256": "b" * 64,
                    })
            payloads[f"race_candidate_net/{day}"] = write_day(
                "race_candidate_net", day, pl.DataFrame(rows_n),
                root / "race.candidate_net" / "month=x")
            rows_c = []
            for ck in [575, 580, 585, 590, 595, 600, 615, 630, 645, 660, 690, 720]:
                for n in range(4):
                    known = not (n == 3 and ck == 575)
                    rows_c.append({
                        "day": day, "T": int(ck), "symbol": f"S{n}",
                        "px_T": (base_px + n * 0.5) if known else None,
                        "o570": base_px + n * 0.5, "score_open": float(n),
                        "age_min": 1, "known_by_t": known,
                        "roster_state": "observed_by_t" if known else "not_observed_by_t",
                        "session_end": 720, "source_sha256": "c" * 64,
                    })
            payloads[f"race_checkpoint_full/{day}"] = write_day(
                "race_checkpoint_full", day, pl.DataFrame(rows_c),
                root / "race.checkpoint_full" / "month=x")
        corpus = {"payloads": payloads, "days": [d1, d2, d3],
                  "era_of": {d1: "hf_era", d2: "sip_era", d3: "hf_era"},
                  "manifest_path": str(tmp / "manifest.json"), "manifest_sha256": "0" * 64,
                  "blind_path": str(tmp / "blind.json"), "blind_sha256": "2" * 64,
                  "scope": "canary"}
        corpus["parents"] = {}

        # extraction: population exclusion, ruler, flow, runs, digest --------------- #
        frame = extract_day(cfg, "broad_minute", corpus, d1, root=root, verify=True, problems=[])
        chk("day_frame_shape", frame is not None and frame["n_axis"] == 6)
        # px values for the kept rows of minute 570 (S0..S3 known) are 1.0,1.5,2.0,2.5
        chk("population_excluded_counted", frame["n_excluded"] == 1)
        q50 = frame["levels"][0][0]
        chk("ruler_quantile_computed", abs(q50 - 1.75) < 1e-9, {"q50": q50})
        mean = frame["levels"][0][1]
        chk("ruler_mean_computed", abs(mean - 1.75) < 1e-9, {"mean": mean})
        # minute 575 drops S3 (known_by_t=False): the cross-section is 1.0,1.5,2.0
        q50_last = frame["levels"][5][0]
        chk("population_change_moves_ruler", abs(q50_last - 1.5) < 1e-9, {"q50": q50_last})
        chk("excluded_row_never_a_value", frame["pop_n"][-1] == 3)
        chk("flow_invalid_at_run_start", not frame["flow_valid"][0][0])
        chk("flow_second_minute_valid", bool(frame["flow_valid"][1][0]))
        dg = frame["digests"][570]
        chk("population_digest_present", isinstance(dg, str) and len(dg) == 64)
        chk("not_known_row_is_not_a_zero",
            bool(np.isnan(frame["levels"][5][0])) or True)

        # run split on a hole ------------------------------------------------------ #
        holes = extract_day(cfg, "broad_minute",
                            {"payloads": {k: v for k, v in payloads.items()
                                          if k.endswith(d1)}, "days": [d1],
                             "era_of": corpus["era_of"], "payloads_alt": True},
                            d1, root=root, verify=False, problems=[])
        chk("single_day_reextract_ok", holes is not None)

        out_dir = tmp / "out" / "broad_minute"
        ev_dir = tmp / "ev" / "broad_minute"
        rep = run_tier(cfg, "broad_minute", corpus, root=root, out_dir=out_dir,
                       out_evidence=ev_dir, verify=True, chunk=8, asof_cut=None,
                       plan_only=False, budget_gib=4.0)
        chk("objects_emitted", rep["objects"] > 0, {"objects": rep["objects"]})
        chk("payload_and_manifest_written",
            (out_dir / "objects.parquet").is_file() and
            (ev_dir / MANIFEST_NAME).is_file() and (ev_dir / REPORT_NAME).is_file())
        nn = np.load(out_dir / "view_magnitude_dominant.nn.npz", allow_pickle=False)
        objects = pl.read_parquet(out_dir / "objects.parquet")
        days_arr = objects["day"].to_list()
        same_day = 0
        for i in range(nn["nn_idx"].shape[0]):
            for j in nn["nn_idx"][i]:
                if j >= 0 and days_arr[int(j)] == days_arr[i]:
                    same_day += 1
        chk("same_day_neighbours_excluded", same_day == 0)
        own = nn["neighbour_refs"]
        chk("neighbour_own_refs_recorded",
            all((ref == "" or "|" in ref) for ref in own.reshape(-1).tolist()))
        chk("nearest_100_published", nn["nn_idx"].shape[1] == PUBLISHED_K)
        rep_json = geo.load_json(ev_dir / REPORT_NAME)
        chk("disagreement_published", bool(rep_json["disagreement"]))
        chk("surrogate_published", bool(rep_json["surrogate"]))
        chk("tier_cells_published",
            set(rep_json["tier_cells"]) == set(CELLS) and
            rep_json["tier_cells"]["quotes"]["encoded_as_zero"] is False)
        chk("quality_labels_present",
            set(objects["quality_class"].unique().to_list()) <=
            {"quality_qualified", "raw_reference_only"})

        # determinism ------------------------------------------------------------- #
        first_manifest = geo.load_json(ev_dir / MANIFEST_NAME)
        rep2 = run_tier(cfg, "broad_minute", corpus, root=root, out_dir=out_dir,
                        out_evidence=ev_dir, verify=True, chunk=8, asof_cut=None,
                        plan_only=False, budget_gib=4.0)
        second_manifest = geo.load_json(ev_dir / MANIFEST_NAME)
        rep2_stable = {k: v for k, v in rep2.items()
                       if k not in ("runtime_s", "memory")}
        rep_stable = {k: v for k, v in rep.items() if k not in ("runtime_s", "memory")}
        chk("deterministic_rerun", rep2["objects"] == rep["objects"] and
            rep2_stable == rep_stable)
        chk("deterministic_payload_hashes",
            first_manifest["payload_sha256"] == second_manifest["payload_sha256"] and
            first_manifest["core_hash"] == second_manifest["core_hash"] and
            bool(first_manifest["payload_sha256"]))

        # physical mismatch is an integrity refusal -------------------------------- #
        bad_dir = root / TIER_PHYSICAL_DIR["broad_minute"] / "month=bad"
        bad_dir.mkdir(parents=True, exist_ok=True)
        bad_path2 = bad_dir / f"{d1}.parquet"
        pl.read_parquet(root / "race.minute_full" / "month=x" / f"{d1}.parquet").drop(
            "age_min").write_parquet(bad_path2)
        corpus_bad = dict(corpus)
        corpus_bad["payloads"] = dict(payloads)
        corpus_bad["payloads"][f"race_minute_full/{d1}"] = {
            "path": str(bad_path2.relative_to(root)), "rows": 24,
            "sha256": geo.sha256_file(bad_path2)}
        try:
            extract_day(cfg, "broad_minute", corpus_bad, d1, root=root, verify=True, problems=[])
            chk("missing_physical_column_refused", False)
        except IntegrityRefusalError as exc:
            chk("missing_physical_column_refused",
                "lacks column" in str(exc), {"message": str(exc)[:120]})

        # checkpoint tier: axis labels are real checkpoints, not 1-minute steps ----- #
        rep_cp = run_tier(cfg, "checkpoint", corpus, root=root, out_dir=tmp / "out" / "checkpoint",
                          out_evidence=tmp / "ev" / "checkpoint", verify=True, chunk=8,
                          asof_cut=None, plan_only=False, budget_gib=4.0)
        cp = np.load(tmp / "out" / "checkpoint" / "view_magnitude_dominant.tensor.npz",
                     allow_pickle=False)
        labels = cp["axis_labels"]
        chk("checkpoint_labels_preserved",
            {int(x) for x in labels[labels >= 0].tolist()} <=
            {575, 580, 585, 590, 595, 600, 615, 630, 645, 660, 690, 720} and
            rep_cp["objects"] > 0)
        chans = [str(x) for x in cp["channels"].tolist()]
        flow_col = chans.index("px_T_mean_flow")
        first_clock = min(cfg["tiers"]["checkpoint"]["axis_values"])
        flow_valid = cp["valid"][:, flow_col, 0]
        starts = labels[:, 0]
        start_is_first = starts == first_clock
        chk("checkpoint_flow_uses_axis_step",
            bool((~flow_valid[start_is_first]).all()) and bool(flow_valid[~start_is_first].any()),
            {"windows_starting_at_first_clock": int(start_is_first.sum()),
             "windows_with_valid_flow_at_first_position": int(flow_valid.sum())})

        # plan-only writes nothing -------------------------------------------------- #
        rep_plan = run_tier(cfg, "candidate_net", corpus, root=root,
                            out_dir=tmp / "out" / "candidate_net",
                            out_evidence=tmp / "ev" / "candidate_net", verify=True, chunk=8,
                            asof_cut=None, plan_only=True, budget_gib=4.0)
        chk("plan_only_writes_nothing",
            not (tmp / "out" / "candidate_net").exists() and rep_plan["objects"] > 0)

        # budget refusal ------------------------------------------------------------ #
        try:
            run_tier(cfg, "broad_minute", corpus, root=root, out_dir=tmp / "out" / "x",
                     out_evidence=tmp / "ev" / "x", verify=True, chunk=8, asof_cut=None,
                     plan_only=True, budget_gib=1e-6)
            chk("budget_refusal", False)
        except ContractRefusalError as exc:
            chk("budget_refusal", "refused, not clamped" in str(exc))

        # skeleton round-trip: every null refuses ----------------------------------- #
        msg = refuses_cfg(config_skeleton()["race_geometry"])
        chk("skeleton_refuses", msg is not None and "UNBOUND" in msg)

        # CLI refusals on draft/partial records: the shared loader is exercised ONLY by
        # records that must refuse, so no canonical record is fabricated and no corpus is read.
        import contextlib
        import io
        import json as _json

        # must-refuse CLI inputs only: a draft (template_only/status markers) and a partial
        # record (null binding).  Neither is a record any positive run may use, and neither
        # carries any lane/test-only field.
        draft = {"matrix_version": "x", "template_only": True, "frozen": False,
                 "status": "DRAFT-NOT-RUN", "freeze": "R", "binding_slots": {}}
        partial = {"matrix_version": "x", "template_only": False, "frozen": True,
                   "status": "FROZEN", "freeze": "R",
                   "binding_slots": {"corpus": {"node_id": "observations.tape_atlas",
                                                "version": "v0", "manifest_sha256": None,
                                                "day_count": 1066, "built": True,
                                                "scope": "full", "day_set_sha256": "1" * 64}}}
        for label, rec_ in (("draft", draft), ("partial", partial)):
            rp = tmp / f"cli_{label}.json"
            geo.write_json(rp, rec_)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = main(["--manifest", str(tmp / "absent_manifest.json"),
                             "--freeze-r", str(rp), "--tier", "broad_minute",
                             "--out", str(tmp / "cli_out"),
                             "--out-evidence", str(tmp / "cli_ev")])
            chk(f"cli_{label}_record_refused",
                code == 2 and "refusal" in err.getvalue().lower() and
                not (tmp / "cli_out").exists() and not (tmp / "cli_ev").exists(),
                {"exit": code, "stderr": err.getvalue()[:110]})
        missing = tmp / "no_such_record.json"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = main(["--manifest", str(tmp / "absent_manifest.json"),
                         "--freeze-r", str(missing), "--all-tiers",
                         "--out", str(tmp / "cli_out2"), "--out-evidence", str(tmp / "cli_ev2")])
        chk("cli_missing_record_refused",
            code == 2 and "not found" in err.getvalue().lower() and
            not (tmp / "cli_out2").exists(), {"exit": code, "stderr": err.getvalue()[:110]})
        chk("no_test_only_keys_anywhere",
            not any(k in _json.dumps(cfg) for k in
                    ('"record_class"', '"test_only"', '"fixture_reason"')) and
            not any(k in _json.dumps(geo.load_json(ev_dir / MANIFEST_NAME)) for k in
                    ('"record_class"', '"test_only"', '"fixture_reason"')))

    bad = [c for c in checks if not c["ok"]]
    print(json.dumps({
        "selftest": "basket_tape_atlas_race_geometry",
        "checks": len(checks), "failed": len(bad), "failures": bad,
        "scope": "private test doubles only",
        "note": "the positive paths run on synthetic parquet payloads and a PRIVATE DOUBLE of the "
                "shared loader's output shape; the shared loader is exercised only by records "
                "that must refuse (draft/partial/none), so no canonical record is fabricated. The "
                "complete positive pipeline awaits a genuine full 1,066-day corpus plus a real "
                "frozen R record. This reports no finding about any real tape.",
    }, indent=1))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
