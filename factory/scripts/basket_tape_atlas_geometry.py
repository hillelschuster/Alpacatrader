#!/usr/bin/env python
"""Freeze-R within-name geometry: selected-path sequence extraction + five hand-view
exact nearest-100 retrieval (PLAN-TAPE-ATLAS.md sections 2, 8, 10; Stage 1).

Scope
-----
Stage-1 consumer of a genuinely FROZEN Freeze-R record.  It

  * extracts one constrained selected-path sequence per (path, declared scale) from the
    canonical observation corpus lineage (``selected_path_grid`` + ``selected_path_prints``),
    applying session/entry/asof selectability BEFORE any feature value is used;
  * builds the five hand views (magnitude / normalized-shape / duration-event /
    activity-microstructure / balanced equal-weight reference) exactly as the frozen R
    record declares them - channel tuple, transforms, metric weights, metric, scale ladder,
    segmentation, radii and neighbours-k are READ, never invented;
  * performs EXACT masked pairwise distance retrieval (no ANN, no approximate index) with
    bounded memory (query-chunked Gram identity, see ``masked_sq_dists``);
  * publishes nearest-100 neighbours, density, recurrence/mutual support, unique + uncertain
    mass, view agreement (including normalized-vs-magnitude neighbour disagreement), the
    measured magnitude-rank association and every rare/unique object's own
    path/interval/clock/bar references;
  * persists deterministic portable arrays (fixed-date-time zip of ``.npy`` members) with a
    separate metadata sidecar per array and a lineage manifest;
  * accepts externally generated SSL embeddings ("learned view") through ``--embeddings``
    without implementing any model family; and emits the masked-TCN input archive
    (``values (N,C,T)`` / ``valid (N,C,T)`` + separate episode arrays + scalar provenance)
    when the frozen record binds ``learned_family.inputs``.

No outcome, censor, future, ticket-constant or anatomy column is read and no outcome module is
imported.  No gate is evaluated and no finding is reported: every number written is a
measurement of the observed tape under the frozen record.

Refusals
--------
``ContractRefusalError`` (exit 2, nothing written): the Freeze-R record is a template/draft,
carries a null (UNBOUND) binding, names a channel that is not a positively eligible tape/print
channel, names a coordinate/key/clock/coverage/quote column, names an unsupported
transform/metric/segmentation mode, declares a non-equal balanced weight, or the
corpus/blind-report hash chain does not match the files.

``IntegrityRefusalError`` (exit 3, REFUSED manifest/report written): a declared payload is
absent, its sha256 or row count mismatches the manifest, the manifest day set disagrees with
the frozen record, or the grid disagrees with the paths table.

Frozen-R input contract (exact required fields, fail-closed)
-----------------------------------------------------------
There is exactly ONE record shape and ONE corpus scope: ``binding_slots.corpus.scope`` MUST be
``"full"`` with a built full manifest whose day set is exactly the guarded dev calendar plus a
complete blind report (Freeze R exists only after the full canonical corpus plus the outcome-blind
report).  There is no partial/canary lane, no test lane, no bypass flag and no default: the
template's own ``template_only`` / ``frozen`` / ``status`` fields carry the draft-versus-frozen
distinction, and nothing else is needed.

Every required numeric choice must be explicitly BOUND: a JSON ``null`` is UNBOUND and refuses
(never a default).  ``numeric_one_shot_gates`` must carry all nine gate keys
(``recurrence, mutual_neighbour_support, block_stability, coordinate_leakage, family_leakage,
magnitude_preservation, sparse_exact_recall, ssl_over_random_improvement, measured_runtime``),
each non-null, because the gates are committed before any training run.  ``learned_family.inputs``
must be bound (the learned family is mandatory in Freeze R; its training belongs to the sequence
sibling, its input archive is emitted here).  Every hand view declares ALL FOUR tier cells
(``selected_paths, candidate_net_minute, broad_provider_minute, full_checkpoint``) from
``{materialized, delegated, unsupported, not_applicable}`` - an unsupported tier is an explicit
declaration, never an absent or null default - and ``selected_paths`` must be ``materialized``
for the views this node fabricates.

Structure (::
    {
      "matrix_version": "<id>", "template_only": false, "frozen": true,
      "status": "<not DRAFT/NOT-RUN/TEMPLATE>",
      "binding_slots": {
        "corpus": {"node_id": str, "version": str, "manifest_sha256": hex64, "day_count": int,
                   "built": true, "scope": "full", "day_set_sha256": hex64},
        "blind_report": {"node_id": str, "path": str, "sha256": hex64, "complete": true,
                         "permitted_summaries_only": true},
        "transform_training_blocks": {"split_key": "block", "fit_blocks": [...],
            "eval_blocks": [...], "no_overlapping_source_interval_crosses_split": true,
            "normalization_and_model_params_fit_on_fit_blocks_only": true, "frozen": true},
        "scale_choice": {"scale_ladder": [int in 1..401], "scale_unit": "minute",
            "scale_anchor": "path_first_valid", "anchor_channel": "<channel id>",
            "alignment": "left", "frozen": true},
        "segmentation": {"segment_rules": {"mode": "whole_source_interval",
            "crop": null | [t0,t1] in 565..965}, "change_point_budget": 0, "frozen": true},
        "metric_weights": {"per_view_weights": {4 primitive views: equal float > 0},
            "balanced_reference_aggregation":
                "fixed equal weight across standardized view distances",
            "balanced_reference_tuned_to_best_geometry": false, "min_common_views": int 1..4},
        "radius": {"neighbour_radius": float|null, "radius_units": str,
                   "radius_per_view": {all 5 views: positive float}},
        "ann_sparse_recall": {"index_build": "exact_bruteforce_no_ann"|"none",
            "density_decile_definition": str, "exact_search_strata": "all",
            "sparse_exact_recall_floor": 1.0},
        "integer_seed": int,
        "geometric_units": {"distance_units": "standardized_channel_l2",
                            "balanced_units": "standardized_view_distance"},
        "numeric_one_shot_gates": {all nine gate keys present, each non-null}
      },
      "hand_views_meta": {"view_count": 5},
      "hand_views": {
        "<magnitude_dominant|normalized_shape_dominant|duration_event_dominant|
          activity_microstructure_dominant>": {
          "geometry_channels": [{"id": str, "source": "grid"|"prints",
              "column": str|null, "aggregate": null|"count"|"size_sum"|"size_max"|
                  "notional_sum"|"vwap", "derive": null|"presence"|"gap_minutes",
              "of": null|"<channel id>", "transform": "identity"|"log1p"|"log_level"|
                  "log_ratio_to_reference"|"first_difference",
              "reference": null|{"of": "<channel id>", "rule": "first_valid"},
              "valid_when": null|{"column": str,
                  "op": "eq|neq|gt|ge|lt|le|is_null|is_not_null", "value": any}}],
          "transforms": [<non-empty list: the declared view-level transform steps>],
          "scale_reference": {"statistic": "median_fit_kth_neighbour_distance", "k": int>=1},
          "metric": "masked_euclidean_zscore_fitblock",
          "metric_weights": {"<channel id>": finite float >= 0},
          "radius": positive float,
          "tier_availability": {selected_paths: "materialized",
              candidate_net_minute: <cell>, broad_provider_minute: <cell>,
              full_checkpoint: <cell>}  # <cell> in
                                         # materialized|delegated|unsupported|not_applicable
        },
        "balanced_multichannel_reference": {"geometry_channels": null,
          "aggregation": "equal_weight_mean_standardized_view_distances",
          "tier_availability": {<all four cells, selected_paths: "materialized">}}
      },
      "learned_family": {"inputs": [<channel spec objects>] (required, non-null),
                         "modes": {"causal": {...}, "retrospective": {...}}},
      "first_retrieval_proof": {"neighbours_k": int >= 1,
          "independent_day_spread_required": true, "path_interval_disagreement_preserved": true}
    }

Masked distance (the only supported metric).  Per channel with fit-block valid-entry mean
``mu`` and std ``sd`` (sd > 0 required), frozen weight ``w`` and ``z = sqrt(w)*(x-mu)/sd``::

    D_ij = sum_c sum_{t : valid_ict and valid_jct} (z_ict - z_jct)**2

Absent entries are EXCLUDED, never imputed: a pair sharing no commonly valid entry in a view
gets an undefined (infinite) view distance and can never be a neighbour, and a balanced
distance needs at least ``min_common_views`` finite view distances.  A channel is a distance
channel only when the frozen record positively names it and the frozen observation schema flags
it ``namespace in {tape, print}`` with ``coordinate_only=false`` and ``retrospective_only=false``.

Masked-TCN / SSL archive layout (one archive per frozen scale x mode)
---------------------------------------------------------------------
Emitted under ``<out>/ssl/<scale>_<mode>.npz`` when ``learned_family.inputs`` is bound::

    values      float32 (N, C, T)   transformed channel values; 0.0 at valid==False
    valid       bool    (N, C, T)   same shape; False is a COMPUTATIONAL pad, never a market 0
    span_allowed bool   (N, T)      RECONSTRUCTION-MASK sampling barrier ONLY (never a scope or
                                     embedding cutoff): True where the observation layer recorded a
                                     physical tape update in the minute (a SIP bar row or at least
                                     one print: bar_state != 'none' or print_state != 'no_print',
                                     axes recorded as span_allowed_axes), False at a silent minute
                                     / provider gap / unknown coverage / padding, and it MAY
                                     become True again after a silent run (contiguous barrier-free
                                     runs are the maskable spans). A known zero (n_prints == 0,
                                     zero volume) stays a legitimate INPUT where valid==True but
                                     never marks a minute observed. Absence is never turned into a
                                     halt flag. COORDINATE ONLY: never a model input, never a
                                     distance channel, never a reconstruction target
    prefix_len  int32       (N,)    ASOF clock cutoff / padding length: the declared window length
                                     truncated at the decision-clock cutoff (--asof-cut). It is a
                                     function of the window and the cutoff ONLY - it never stops at
                                     a silence, halt or provider gap, so the whole declared window
                                     up to the query instant stays usable with per-minute validity
                                     carried explicitly in `valid` (known-zero activity included)
    query_t     int32       (N,)    the mode's decision clock: causal = the COMPLETION clock of
                                     the last included bucket (last bucket label + 1, i.e. the
                                     instant that bucket's close/counts become known per the
                                     observation contract's decision_close_rule; no bucket at or
                                     after it enters the episode); retrospective = end_t
    channels    <U          (C,)    channel ids (value order)
    episode_id  <U          (N,)    "<path_id>@<scale>"
    day         <U          (N,)    session date              (coordinate, never a model input)
    ticker      <U          (N,)    instrument                 (coordinate, never a model input)
    path_id     <U          (N,)    day|ticker                 (coordinate, never a model input)
    block       <U          (N,)    block1|block2              (coordinate, never a model input)
    start_t     int32       (N,)    window start minute label in [565,965]
    end_t       int32       (N,)    window end minute label
    mode        <U          (N,)    causal|retrospective
    fit         bool        (N,)    True => fit day block, False => held-out eval block
    manifest_sha256, freeze_r_sha256, schema_sha256 : scalar <U64 provenance hashes

Causal mode keeps the whole declared window up to the ASOF cutoff (rule:
``declared_window_truncated_at_asof_cutoff``, i.e. ``prefix_len`` buckets - it never stops at a
silence/gap); retrospective mode keeps the whole window.  Fit/eval episodes are FLAGGED (``fit``),
never pooled silently.

CLI
---
  .venv/bin/python factory/scripts/basket_tape_atlas_geometry.py --selftest
  .venv/bin/python factory/scripts/basket_tape_atlas_geometry.py \\
      --root /home/hillel/projects/Alpacatrader/data/atlas/observation/v0 \\
      --manifest <corpus manifest.json> --freeze-r <frozen R record.json> \\
      --out /home/hillel/projects/Alpacatrader/data/atlas/geometry/v0 \\
      --out-evidence factory/artifacts/basket/phase2/ATLAS/TAPE/GEOMETRY/v0

Exit codes: 0 complete, 2 contract refusal (nothing written), 3 integrity refusal
(REFUSED manifest/report written), 1 selftest failure.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import zipfile
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
DEFAULT_ROOT = Path("/home/hillel/projects/Alpacatrader/data/atlas/observation/v0")
DEFAULT_OUT = Path("/home/hillel/projects/Alpacatrader/data/atlas/geometry/v0")
DEFAULT_OUT_EVIDENCE = TAPE / "GEOMETRY/v0"
MANIFEST_NAME = "geometry_manifest.json"
REPORT_NAME = "retrieval_report.json"
RARE_NAME = "rare_refs.parquet"
OBJECTS_NAME = "objects.parquet"

ET_LO = 565
ET_HI = 965
T_SPAN = ET_HI - ET_LO + 1  # 401
HEX64 = re.compile(r"[0-9a-f]{64}")
SAFE_ID = re.compile(r"[A-Za-z0-9_.:@\-]{1,64}")
BLOCK1_MAX_DAY = "2023-12-31"  # observation contract.json day_guard.block_gap_rule

MAGNITUDE = "magnitude_dominant"
SHAPE = "normalized_shape_dominant"
DURATION = "duration_event_dominant"
ACTIVITY = "activity_microstructure_dominant"
BALANCED = "balanced_multichannel_reference"
PRIMITIVE_VIEWS = (MAGNITUDE, SHAPE, DURATION, ACTIVITY)
HAND_VIEWS = PRIMITIVE_VIEWS + (BALANCED,)

METRIC = "masked_euclidean_zscore_fitblock"
SCALE_STATISTIC = "median_fit_kth_neighbour_distance"
BALANCED_AGG = "fixed equal weight across standardized view distances"
BALANCED_DECL = "equal_weight_mean_standardized_view_distances"
CAUSAL_PREFIX_RULE = "declared_window_truncated_at_asof_cutoff"

TRANSFORMS = ("identity", "log1p", "log_level", "log_ratio_to_reference", "first_difference")
DERIVES = ("presence", "gap_minutes")
OPS = ("eq", "neq", "gt", "ge", "lt", "le", "is_null", "is_not_null")
PRINT_AGGREGATES = ("count", "size_sum", "size_max", "notional_sum", "vwap")
SOURCES = ("grid", "prints")
# freeze-r.representation-matrix.template binding_slots.numeric_one_shot_gates: every gate is
# committed once, before any training run, so a null/absent gate refuses (never a default).
REQUIRED_GATES = (
    "recurrence",
    "mutual_neighbour_support",
    "block_stability",
    "coordinate_leakage",
    "family_leakage",
    "magnitude_preservation",
    "sparse_exact_recall",
    "ssl_over_random_improvement",
    "measured_runtime",
)
# observation contract.json tier_support_matrix tier keys; a cell is an explicit declaration,
# never an absent/null default.
TIER_CELLS = ("selected_paths", "candidate_net_minute", "broad_provider_minute", "full_checkpoint")
TIER_CELL_VALUES = ("materialized", "delegated", "unsupported", "not_applicable")
# schema.json#field_attributes.coordinate_only_rule + representation_matrix_template#coordinate_deny
COORDINATE_DENY = frozenset(
    {
        "month", "block", "feed_era", "dow", "et", "et_min", "minute_index", "coverage_class",
        "family", "entry_rank", "entry_et", "day", "ticker", "path_id", "member_id",
        "session_end", "within_session", "within_observed_span", "canary_member",
        "in_projection_window", "source_row_ordinal", "trade_id",
    }
)
QUOTE_DENY = ("quote", "bid_", "ask_", "spread_", "nbbo")

try:  # canonical sealed/reserved guard + dev calendar (observation contract.json day_guard)
    from factory.scripts.basket_sim import dev_days, guard_day
except ImportError:  # direct script execution
    sys.path.insert(0, str(ROOT))
    from factory.scripts.basket_sim import dev_days, guard_day


class ContractRefusalError(RuntimeError):
    """The frozen record or the evidence chain is illegal: refuse before reading rows."""


class IntegrityRefusalError(RuntimeError):
    """The corpus disagrees with its own manifest: write a REFUSED report and stop."""

    def __init__(self, message: str, problems: list | None = None):
        super().__init__(message)
        self.problems = problems or [{"kind": "integrity_refusal", "message": message}]


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def sha256_file(path: Path, chunk: int = 1 << 22) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path) -> dict:
    return json.loads(Path(path).read_text())


def canon_bytes(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()


def day_set_sha(days) -> str:
    return sha256_bytes("\n".join(sorted(days)).encode())


def resolve_under(root: Path, rel: str) -> Path:
    r = Path(rel)
    if r.is_absolute() or ".." in r.parts:
        raise ContractRefusalError(f"payload path must be relative and traversal-free: {rel!r}")
    p = (root / r).resolve()
    if p != root.resolve() and root.resolve() not in p.parents:
        raise ContractRefusalError(f"payload path escapes --root: {rel!r}")
    return p


def block_of(day: str) -> str:
    return "block1" if day <= BLOCK1_MAX_DAY else "block2"


def quantiles(
    values, probs=(0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 1.0)
) -> dict:
    v = np.asarray(values, dtype=np.float64)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return {"n": 0, "mean": None, "quantiles": {str(p): None for p in probs}}
    q = np.quantile(v, probs)
    return {
        "n": int(v.size),
        "mean": float(np.mean(v)),
        "quantiles": {str(p): float(x) for p, x in zip(probs, q, strict=True)},
    }


def spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 3:
        return None
    x, y = a[ok], b[ok]
    rx = np.argsort(np.argsort(x, kind="stable"), kind="stable").astype(np.float64)
    ry = np.argsort(np.argsort(y, kind="stable"), kind="stable").astype(np.float64)
    if rx.std() == 0 or ry.std() == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def deterministic_npz(path: Path, arrays: dict) -> None:
    """Portable ``.npz`` with a fixed member timestamp: byte-identical across reruns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
        for name in sorted(arrays):
            buf = io.BytesIO()
            np.save(buf, np.asarray(arrays[name]), allow_pickle=False)
            info = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            zf.writestr(info, buf.getvalue())
    tmp.replace(path)


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(canon_bytes(obj) + b"\n")
    tmp.replace(path)


def write_parquet(path: Path, df: pl.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.write_parquet(tmp)
    tmp.replace(path)


def read_payload(root: Path, info: dict, *, verify: bool, problems: list) -> bytes | None:
    rel = info.get("path")
    if not rel:
        return None
    p = resolve_under(root, str(rel))
    if not p.is_file():
        problems.append({"kind": "payload_absent", "path": rel})
        return None
    data = p.read_bytes()
    if verify and info.get("sha256") and sha256_bytes(data) != info["sha256"]:
        problems.append({"kind": "payload_sha256_mismatch", "path": rel})
        return None
    return data


# --------------------------------------------------------------------------- #
# frozen-record validation
# --------------------------------------------------------------------------- #
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


def validate_channel_spec(spec, where: str, *, schema: dict, registry: dict) -> dict:
    """Validate one declared channel against the frozen schema and the causal registry."""
    if not isinstance(spec, dict):
        raise ContractRefusalError(f"{where} must be an object")
    cid = _req(spec, "id", str, where)
    if not SAFE_ID.fullmatch(cid):
        raise ContractRefusalError(f"{where}.id is not a safe identifier: {cid!r}")
    source = _req(spec, "source", str, where)
    if source not in SOURCES:
        raise ContractRefusalError(f"{where}.source {source!r} is not one of {SOURCES}")
    derive = spec.get("derive")
    if derive is not None and derive not in DERIVES:
        raise ContractRefusalError(f"{where}.derive {derive!r} is not one of {DERIVES}")
    transform = _req(spec, "transform", str, where)
    if transform not in TRANSFORMS:
        raise ContractRefusalError(f"{where}.transform {transform!r} is not one of {TRANSFORMS}")
    if derive is not None:
        if transform not in ("identity", "log1p"):
            raise ContractRefusalError(
                f"{where}: a derived channel may only use transform identity|log1p"
            )
        if spec.get("valid_when") is not None:
            raise ContractRefusalError(f"{where}: a derived channel must not declare valid_when")
        _req(spec, "of", str, where)
    elif spec.get("of") is not None:
        raise ContractRefusalError(f"{where}.of is only allowed on a derived channel")
    ref = spec.get("reference")
    if transform == "log_ratio_to_reference":
        if not isinstance(ref, dict):
            raise ContractRefusalError(
                f"{where}.reference is required for transform log_ratio_to_reference"
            )
        _req(ref, "of", str, f"{where}.reference")
        if _req(ref, "rule", str, f"{where}.reference") != "first_valid":
            raise ContractRefusalError(f"{where}.reference.rule must be 'first_valid'")
    elif ref is not None:
        raise ContractRefusalError(f"{where}.reference is only allowed with log_ratio_to_reference")

    table = "selected_path_grid" if source == "grid" else "selected_path_prints"
    fields = schema["tables"][table]["fields"]
    column = spec.get("column")
    aggregate = spec.get("aggregate")
    if source == "prints":
        if aggregate is None:
            raise ContractRefusalError(
                f"{where}.aggregate is required for source=prints (one of {PRINT_AGGREGATES})"
            )
        if aggregate not in PRINT_AGGREGATES:
            raise ContractRefusalError(f"{where}.aggregate {aggregate!r} is not supported")
        if aggregate == "count" and column is not None:
            raise ContractRefusalError(f"{where}.column must be null for aggregate=count")
        if aggregate in ("size_sum", "size_max", "vwap") and column != "size":
            raise ContractRefusalError(f"{where}.column must be 'size' for {aggregate}")
        if aggregate == "notional_sum" and column is not None:
            raise ContractRefusalError(f"{where}.column must be null for notional_sum")
    else:
        if aggregate is not None:
            raise ContractRefusalError(f"{where}.aggregate is only allowed for source=prints")
        column = _req(spec, "column", str, where)
        if column not in fields:
            raise ContractRefusalError(f"{where}.column {column!r} is not a {table} column")

    vw = spec.get("valid_when")
    if derive is None:
        if not isinstance(vw, dict):
            raise ContractRefusalError(
                f"{where}.valid_when is required - a channel must declare its validity "
                "explicitly; absence is never a market zero"
            )
        op = _req(vw, "op", str, f"{where}.valid_when")
        if op not in OPS:
            raise ContractRefusalError(f"{where}.valid_when.op {op!r} is not one of {OPS}")
        vcol = _req(vw, "column", str, f"{where}.valid_when")
        if vcol not in fields:
            raise ContractRefusalError(f"{where}.valid_when.column {vcol!r} not in {table}")
        if op in ("is_null", "is_not_null"):
            if "value" in vw:
                raise ContractRefusalError(f"{where}.valid_when.value not allowed with {op}")
        else:
            _req(vw, "value", (str, int, float, bool), f"{where}.valid_when")

    for name in (column, spec.get("of"), (ref or {}).get("of")):
        if name is None:
            continue
        if name in COORDINATE_DENY:
            raise ContractRefusalError(
                f"{where}: {name!r} is a clock/coordinate column, never a distance channel"
            )
    if derive is None and column is not None:
        for bad in QUOTE_DENY:
            if column.startswith(bad):
                raise ContractRefusalError(
                    f"{where}.column {column!r}: no quote channel may enter geometry "
                    "(quote_channel_ready is not established; a quote absence is never a value)"
                )
        fa = fields[column]
        if fa["namespace"] not in ("tape", "print"):
            raise ContractRefusalError(
                f"{where}.column {column!r}: namespace {fa['namespace']!r} is metadata, "
                "never a distance channel"
            )
        if fa["coordinate_only"] or fa["retrospective_only"] or not fa["prospective_allowed"]:
            raise ContractRefusalError(
                f"{where}.column {column!r}: not a positively eligible prospective tape/print "
                f"channel (coordinate_only={fa['coordinate_only']}, "
                f"retrospective_only={fa['retrospective_only']}, "
                f"prospective_allowed={fa['prospective_allowed']})"
            )
    deny_prefixes = tuple(registry.get("denied_prefixes") or ())
    deny_exact = set(registry.get("denied_exact_columns") or ())
    for name in (column, (vw or {}).get("column")):
        if name is None:
            continue
        if name in deny_exact or any(str(name).startswith(p) for p in deny_prefixes):
            raise ContractRefusalError(
                f"{where}: column {name!r} is denied by the frozen causal registry"
            )
    return {
        "id": cid,
        "source": source,
        "column": column,
        "aggregate": aggregate,
        "derive": derive,
        "of": spec.get("of"),
        "transform": transform,
        "reference": ref,
        "valid_when": vw,
    }


def tier_cells_of(spec: dict, where: str) -> dict:
    """Every hand view must declare ALL FOUR tier cells explicitly (never absent/null)."""
    ta = _req(spec, "tier_availability", dict, where)
    cells = {}
    for cell in TIER_CELLS:
        val = _req(ta, cell, str, f"{where}.tier_availability")
        if val not in TIER_CELL_VALUES:
            raise ContractRefusalError(
                f"{where}.tier_availability.{cell} {val!r} is not one of {TIER_CELL_VALUES} "
                "(an unsupported tier is declared explicitly, never left UNBOUND)"
            )
        cells[cell] = val
    if cells["selected_paths"] != "materialized":
        raise ContractRefusalError(
            f"{where}.tier_availability.selected_paths must be 'materialized': this node "
            "fabricates the selected-path tier"
        )
    return cells


def load_freeze_r(path: Path, *, schema: dict, registry: dict) -> dict:
    """Load and strictly validate a frozen Freeze-R record.  Never fills a default."""
    if not path.is_file():
        raise ContractRefusalError(f"Freeze-R record not found: {path}")
    raw = load_json(path)
    if not isinstance(raw, dict):
        raise ContractRefusalError("Freeze-R record must be a JSON object")
    if raw.get("template_only"):
        raise ContractRefusalError(
            "the record is a template (template_only=true) - it carries no frozen choice"
        )
    if raw.get("frozen") is not True:
        raise ContractRefusalError(
            f"frozen must be true (found {raw.get('frozen')!r}) - a draft refuses"
        )
    status = str(raw.get("status") or "")
    if not status or re.search(r"DRAFT|TEMPLATE|NOT-RUN|NOT_RUN|UNFROZEN", status, re.I):
        raise ContractRefusalError(f"status {status!r} is a draft/template marker")
    matrix_version = _req(raw, "matrix_version", str, "freeze_r")
    if raw.get("freeze") not in (None, "R"):
        raise ContractRefusalError(f"freeze must be 'R' (found {raw.get('freeze')!r})")
    bs = _req(raw, "binding_slots", dict, "freeze_r")

    cb = _req(bs, "corpus", dict, "binding_slots")
    corpus = {
        "node_id": _req(cb, "node_id", str, "binding_slots.corpus"),
        "version": _req(cb, "version", str, "binding_slots.corpus"),
        "manifest_sha256": _req_hex(cb, "manifest_sha256", "binding_slots.corpus"),
        "day_count": int(_req(cb, "day_count", int, "binding_slots.corpus")),
        "built": _req_true(cb, "built", "binding_slots.corpus"),
        "scope": _req(cb, "scope", str, "binding_slots.corpus"),
        "day_set_sha256": _req_hex(cb, "day_set_sha256", "binding_slots.corpus"),
    }
    if corpus["scope"] != "full":
        raise ContractRefusalError(
            f"binding_slots.corpus.scope {corpus['scope']!r} must be 'full': Freeze R exists only "
            "after the full canonical corpus plus the complete outcome-blind report, and there is "
            "no canary, partial or test scope"
        )
    if corpus["day_count"] <= 0:
        raise ContractRefusalError("binding_slots.corpus.day_count must be > 0")

    br = _req(bs, "blind_report", dict, "binding_slots")
    blind = {
        "node_id": _req(br, "node_id", str, "binding_slots.blind_report"),
        "path": _req(br, "path", str, "binding_slots.blind_report"),
        "sha256": _req_hex(br, "sha256", "binding_slots.blind_report"),
        "complete": _req_true(br, "complete", "binding_slots.blind_report"),
        "permitted_summaries_only": _req_true(
            br, "permitted_summaries_only", "binding_slots.blind_report"
        ),
    }

    tb = _req(bs, "transform_training_blocks", dict, "binding_slots")
    fit_blocks = list(_req(tb, "fit_blocks", list, "binding_slots.transform_training_blocks"))
    eval_blocks = list(_req(tb, "eval_blocks", list, "binding_slots.transform_training_blocks"))
    if not fit_blocks or not eval_blocks:
        raise ContractRefusalError("fit_blocks and eval_blocks must both be non-empty")
    for b in fit_blocks + eval_blocks:
        if b not in ("block1", "block2"):
            raise ContractRefusalError(f"block label {b!r} is not block1|block2")
    if set(fit_blocks) & set(eval_blocks):
        raise ContractRefusalError("fit_blocks and eval_blocks must be disjoint")
    if _req(tb, "split_key", str, "binding_slots.transform_training_blocks") != "block":
        raise ContractRefusalError("transform_training_blocks.split_key must be 'block'")
    _req_true(tb, "no_overlapping_source_interval_crosses_split",
              "binding_slots.transform_training_blocks")
    _req_true(tb, "normalization_and_model_params_fit_on_fit_blocks_only",
              "binding_slots.transform_training_blocks")
    _req_true(tb, "frozen", "binding_slots.transform_training_blocks")
    blocks = {"fit": fit_blocks, "eval": eval_blocks}

    sc = _req(bs, "scale_choice", dict, "binding_slots")
    ladder = list(_req(sc, "scale_ladder", list, "binding_slots.scale_choice"))
    if not ladder or any((not isinstance(s, int)) or s <= 0 or s > T_SPAN for s in ladder):
        raise ContractRefusalError(
            f"scale_ladder must be ints in [1,{T_SPAN}]: {ladder!r}"
        )
    if _req(sc, "scale_anchor", str, "binding_slots.scale_choice") != "path_first_valid":
        raise ContractRefusalError("scale_anchor must be 'path_first_valid'")
    if _req(sc, "scale_unit", str, "binding_slots.scale_choice") != "minute":
        raise ContractRefusalError("scale_unit must be 'minute'")
    if _req(sc, "alignment", str, "binding_slots.scale_choice") != "left":
        raise ContractRefusalError("scale_alignment must be 'left'")
    scale = {
        "ladder": [int(s) for s in ladder],
        "anchor": "path_first_valid",
        "anchor_channel": _req(sc, "anchor_channel", str, "binding_slots.scale_choice"),
    }
    _req_true(sc, "frozen", "binding_slots.scale_choice")

    sg = _req(bs, "segmentation", dict, "binding_slots")
    rules = _req(sg, "segment_rules", dict, "binding_slots.segmentation")
    mode = _req(rules, "mode", str, "binding_slots.segmentation.segment_rules")
    if mode != "whole_source_interval":
        raise ContractRefusalError(
            f"segmentation mode {mode!r} is not supported by the v0 hand-view extractor"
        )
    budget = int(_req(sg, "change_point_budget", int, "binding_slots.segmentation"))
    if budget != 0:
        raise ContractRefusalError(
            f"change_point_budget must be 0 for mode 'whole_source_interval' (found {budget})"
        )
    crop = rules.get("crop")
    if crop is not None and (
        not isinstance(crop, list)
        or len(crop) != 2
        or not all(isinstance(x, int) for x in crop)
        or not (ET_LO <= crop[0] <= crop[1] <= ET_HI)
    ):
        raise ContractRefusalError(
            f"segment_rules.crop must be null or [t0,t1] inside [{ET_LO},{ET_HI}]"
        )
    _req_true(sg, "frozen", "binding_slots.segmentation")
    seg = {"mode": mode, "budget": budget, "crop": crop}

    mw = _req(bs, "metric_weights", dict, "binding_slots")
    per_view = _req(mw, "per_view_weights", dict, "binding_slots.metric_weights")
    if set(per_view) != set(PRIMITIVE_VIEWS):
        raise ContractRefusalError(
            "binding_slots.metric_weights.per_view_weights must name exactly the four "
            f"primitive views {PRIMITIVE_VIEWS} (found {sorted(per_view)})"
        )
    weights = [float(v) for v in per_view.values()]
    if not all(np.isfinite(w) and w > 0 for w in weights):
        raise ContractRefusalError("per_view_weights must be finite and > 0")
    if max(weights) - min(weights) > 0:
        raise ContractRefusalError(
            "per_view_weights must be EQUAL (fixed equal weight across standardized view "
            "distances, never tuned into the best-looking geometry)"
        )
    if (
        _req(mw, "balanced_reference_aggregation", str, "binding_slots.metric_weights")
        != BALANCED_AGG
    ):
        raise ContractRefusalError(f"balanced_reference_aggregation must be {BALANCED_AGG!r}")
    if mw.get("balanced_reference_tuned_to_best_geometry") is not False:
        raise ContractRefusalError("balanced_reference_tuned_to_best_geometry must be false")
    min_common = int(_req(mw, "min_common_views", int, "binding_slots.metric_weights"))
    if not 1 <= min_common <= len(PRIMITIVE_VIEWS):
        raise ContractRefusalError(f"min_common_views must be in [1,{len(PRIMITIVE_VIEWS)}]")

    rad = _req(bs, "radius", dict, "binding_slots")
    rpv = _req(rad, "radius_per_view", dict, "binding_slots.radius")
    if not set(HAND_VIEWS) <= set(rpv):
        raise ContractRefusalError(
            "binding_slots.radius.radius_per_view must declare all five hand views "
            f"(missing {sorted(set(HAND_VIEWS) - set(rpv))}); optional extra view radii "
            "(e.g. 'learned') are allowed"
        )
    radius = {v: _req_num(rpv, v, "binding_slots.radius.radius_per_view", positive=True)
              for v in rpv}
    radius_units = _req(rad, "radius_units", str, "binding_slots.radius")
    if rad.get("neighbour_radius") is not None:
        _req_num(rad, "neighbour_radius", "binding_slots.radius", positive=True)

    sar = _req(bs, "ann_sparse_recall", dict, "binding_slots")
    if _req(sar, "index_build", str, "binding_slots.ann_sparse_recall") not in (
        "exact_bruteforce_no_ann",
        "none",
    ):
        raise ContractRefusalError(
            "ann_sparse_recall.index_build must be 'exact_bruteforce_no_ann' or 'none': this "
            "consumer builds no ANN index"
        )
    _req(sar, "density_decile_definition", str, "binding_slots.ann_sparse_recall")
    if _req(sar, "exact_search_strata", str, "binding_slots.ann_sparse_recall") != "all":
        raise ContractRefusalError("exact_search_strata must be 'all' when no ANN is built")
    if _req_num(sar, "sparse_exact_recall_floor", "binding_slots.ann_sparse_recall") != 1.0:
        raise ContractRefusalError("sparse_exact_recall_floor must be 1.0 for exact search")

    seed = int(_req(bs, "integer_seed", int, "binding_slots"))
    gates = _req(bs, "numeric_one_shot_gates", dict, "binding_slots")
    unbound = [g for g in REQUIRED_GATES if gates.get(g) is None or g not in gates]
    if unbound:
        raise ContractRefusalError(
            "binding_slots.numeric_one_shot_gates is not fully BOUND (UNBOUND/absent: "
            f"{unbound}); the one-shot gates are committed once, before any training run, so a "
            "null gate refuses rather than defaulting"
        )
    gu = _req(bs, "geometric_units", dict, "binding_slots")
    distance_units = _req(gu, "distance_units", str, "binding_slots.geometric_units")
    balanced_units = _req(gu, "balanced_units", str, "binding_slots.geometric_units")
    if distance_units != "standardized_channel_l2":
        raise ContractRefusalError("distance_units must be 'standardized_channel_l2'")
    if balanced_units != "standardized_view_distance":
        raise ContractRefusalError("balanced_units must be 'standardized_view_distance'")

    hv_meta = _req(raw, "hand_views_meta", dict, "freeze_r")
    if int(_req(hv_meta, "view_count", int, "freeze_r.hand_views_meta")) != len(HAND_VIEWS):
        raise ContractRefusalError(f"hand_views_meta.view_count must be {len(HAND_VIEWS)}")
    hv = _req(raw, "hand_views", dict, "freeze_r")
    if set(hv) != set(HAND_VIEWS):
        raise ContractRefusalError(
            f"hand_views must name exactly {HAND_VIEWS} (found {sorted(hv)})"
        )

    channels: dict[str, dict] = {}
    views: dict[str, dict] = {}
    for vname in PRIMITIVE_VIEWS:
        spec = _req(hv, vname, dict, f"hand_views.{vname}")
        raw_channels = _req(spec, "geometry_channels", list, f"hand_views.{vname}")
        if not raw_channels:
            raise ContractRefusalError(f"hand_views.{vname}.geometry_channels is empty")
        vids = []
        for i, cs in enumerate(raw_channels):
            ch = validate_channel_spec(
                cs, f"hand_views.{vname}.geometry_channels[{i}]", schema=schema, registry=registry
            )
            if ch["id"] in channels:
                if channels[ch["id"]] != ch:
                    raise ContractRefusalError(
                        f"channel id {ch['id']!r} is declared twice with different definitions"
                    )
            else:
                channels[ch["id"]] = ch
            vids.append(ch["id"])
        declared_transforms = _req(spec, "transforms", list, f"hand_views.{vname}")
        if not declared_transforms:
            raise ContractRefusalError(
                f"hand_views.{vname}.transforms must be a non-empty declared list"
            )
        if _req(spec, "metric", str, f"hand_views.{vname}") != METRIC:
            raise ContractRefusalError(
                f"hand_views.{vname}.metric must be {METRIC!r} (the only supported metric)"
            )
        wts = _req(spec, "metric_weights", dict, f"hand_views.{vname}")
        if set(wts) != set(vids):
            raise ContractRefusalError(
                f"hand_views.{vname}.metric_weights must weight exactly its channels"
            )
        for cid in vids:
            _req_num(wts, cid, f"hand_views.{vname}.metric_weights", lo=0.0)
        if not any(float(wts[c]) > 0 for c in vids):
            raise ContractRefusalError(f"hand_views.{vname}.metric_weights are all zero")
        sref = _req(spec, "scale_reference", dict, f"hand_views.{vname}")
        if _req(sref, "statistic", str, f"hand_views.{vname}.scale_reference") != SCALE_STATISTIC:
            raise ContractRefusalError(
                f"hand_views.{vname}.scale_reference.statistic must be {SCALE_STATISTIC!r}"
            )
        sref_k = int(_req(sref, "k", int, f"hand_views.{vname}.scale_reference"))
        if sref_k < 1:
            raise ContractRefusalError("scale_reference.k must be >= 1")
        tier = tier_cells_of(spec, f"hand_views.{vname}")
        views[vname] = {
            "channels": vids,
            "weights": {c: float(wts[c]) for c in vids},
            "scale_reference": {"statistic": SCALE_STATISTIC, "k": sref_k},
            "declared_transforms": list(declared_transforms),
            "radius": radius[vname],
            "tier_cells": tier,
            "declared": spec,
        }
    bspec = _req(hv, BALANCED, dict, f"hand_views.{BALANCED}")
    if bspec.get("geometry_channels") is not None:
        raise ContractRefusalError(
            f"hand_views.{BALANCED}.geometry_channels must be null: the balanced view is the "
            "fixed equal-weight combination of the standardized primitive view distances"
        )
    if _req(bspec, "aggregation", str, f"hand_views.{BALANCED}") != BALANCED_DECL:
        raise ContractRefusalError(f"hand_views.{BALANCED}.aggregation must be {BALANCED_DECL!r}")
    b_tier = tier_cells_of(bspec, f"hand_views.{BALANCED}")
    views[BALANCED] = {"channels": [], "weights": {}, "radius": radius[BALANCED],
                       "tier_cells": b_tier, "declared": bspec, "scale_reference": None,
                       "declared_transforms": []}

    if scale["anchor_channel"] not in channels:
        raise ContractRefusalError(
            f"scale_choice.anchor_channel {scale['anchor_channel']!r} is not a declared channel"
        )
    ach = channels[scale["anchor_channel"]]
    if ach["derive"] is not None or ach["transform"] not in ("identity", "log1p"):
        raise ContractRefusalError(
            "the anchor channel must be a base channel with transform identity|log1p so its "
            "validity is transform-stable"
        )
    for cid, ch in channels.items():
        if ch["of"] is not None:
            if ch["of"] not in channels:
                raise ContractRefusalError(
                    f"channel {cid!r} references unknown channel {ch['of']!r}"
                )
            if channels[ch["of"]]["derive"] is not None:
                raise ContractRefusalError(f"channel {cid!r} must reference a base channel")
        if ch["reference"] is not None:
            rof = ch["reference"]["of"]
            if rof not in channels:
                raise ContractRefusalError(f"channel {cid!r} references unknown channel {rof!r}")
            if channels[rof]["derive"] is not None:
                raise ContractRefusalError(
                    f"channel {cid!r} must reference a base channel for log_ratio_to_reference"
                )
    lf = _req(raw, "learned_family", dict, "freeze_r")
    raw_inputs = _req(lf, "inputs", list, "learned_family")
    if not raw_inputs:
        raise ContractRefusalError(
            "learned_family.inputs must be a non-empty list: the learned sequence family is "
            "mandatory in Freeze R, so an UNBOUND (null) input tuple refuses rather than "
            "defaulting"
        )
    learned_inputs = []
    for i, cs in enumerate(raw_inputs):
        ch = validate_channel_spec(
            cs, f"learned_family.inputs[{i}]", schema=schema, registry=registry
        )
        if ch["id"] in channels:
            if channels[ch["id"]] != ch:
                raise ContractRefusalError(
                    f"learned_family.inputs[{i}].id {ch['id']!r} clashes with a "
                    "hand-view channel"
                )
        else:
            channels[ch["id"]] = ch
        learned_inputs.append(ch["id"])
    raw_modes = _req(lf, "modes", dict, "learned_family")
    if not {"causal", "retrospective"} <= set(raw_modes):
        raise ContractRefusalError(
            "learned_family.modes must declare both 'causal' and 'retrospective'"
        )
    modes = ("retrospective", "causal")

    frp = _req(raw, "first_retrieval_proof", dict, "freeze_r")
    k = int(_req(frp, "neighbours_k", int, "freeze_r.first_retrieval_proof"))
    if k < 1:
        raise ContractRefusalError("first_retrieval_proof.neighbours_k must be >= 1")
    for vname in PRIMITIVE_VIEWS:
        srk = views[vname]["scale_reference"]["k"]
        if srk > k:
            raise ContractRefusalError(
                f"hand_views.{vname}.scale_reference.k ({srk}) exceeds "
                f"first_retrieval_proof.neighbours_k ({k})"
            )

    base_ids = [
        cid for cid, ch in channels.items() if ch["derive"] is None
    ]
    derived_ids = [cid for cid, ch in channels.items() if ch["derive"] is not None]
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "matrix_version": matrix_version,
        "corpus": corpus,
        "blind_report": blind,
        "blocks": blocks,
        "scale": scale,
        "segmentation": seg,
        "min_common_views": min_common,
        "radius": radius,
        "radius_units": radius_units,
        "balanced_units": balanced_units,
        "seed": seed,
        "gates": gates,
        "views": views,
        "channels": channels,
        "base_ids": base_ids,
        "derived_ids": derived_ids,
        "all_ids": base_ids + derived_ids,
        "learned_inputs": learned_inputs,
        "learned_modes": modes,
        "neighbours_k": k,
        "raw": raw,
    }


# --------------------------------------------------------------------------- #
# corpus inputs
# --------------------------------------------------------------------------- #
def load_corpus(root: Path, manifest_path: Path, r: dict, *, verify: bool) -> dict:
    """Validate the observation manifest against the frozen record and list its payloads."""
    if not manifest_path.is_file():
        raise ContractRefusalError(f"observation manifest not found: {manifest_path}")
    manifest = load_json(manifest_path)
    if sha256_file(manifest_path) != r["corpus"]["manifest_sha256"]:
        raise ContractRefusalError(
            f"corpus manifest sha256 does not match the frozen record ({manifest_path}) - "
            "the record is stale"
        )
    blind_path = Path(r["blind_report"]["path"])
    if not blind_path.is_absolute():
        blind_path = ROOT / blind_path
    if not blind_path.is_file():
        raise ContractRefusalError(f"blind corpus report not found: {blind_path}")
    if sha256_file(blind_path) != r["blind_report"]["sha256"]:
        raise ContractRefusalError(
            "blind corpus report sha256 does not match the frozen record - the record is stale"
        )
    if manifest.get("node_id") != r["corpus"]["node_id"]:
        raise ContractRefusalError(
            f"manifest node_id {manifest.get('node_id')!r} != frozen {r['corpus']['node_id']!r}"
        )
    payloads = manifest.get("payload_sha256") or manifest.get("payloads") or {}
    if not payloads:
        raise ContractRefusalError("the observation manifest declares no payload_sha256")

    for p, label in (
        (SCHEMA_PATH, "observation schema"),
        (OBS_CONTRACT_PATH, "observation contract"),
        (REGISTRY_PATH, "causal registry"),
        (LOCK_PATH, "contract lock"),
    ):
        if not p.is_file():
            raise ContractRefusalError(f"{label} missing: {p}")
    lock = load_json(LOCK_PATH)
    if lock.get("status") != "frozen":
        raise ContractRefusalError("contract_lock.json is not frozen")
    for name, want in (lock.get("hashes") or {}).items():
        if sha256_file(OBS_EVIDENCE / name) != want:
            raise ContractRefusalError(
                f"contract lock mismatch for {name}: the Freeze-O chain moved under the record"
            )

    grid_days = sorted(
        key.split("/", 1)[1]
        for key in payloads
        if key.startswith("selected_path_grid/") and (payloads[key] or {}).get("path")
    )
    if not grid_days:
        raise ContractRefusalError("the manifest declares no selected_path_grid payload")
    for d in grid_days:
        try:
            guard_day(d)
        except PermissionError as exc:
            raise ContractRefusalError(str(exc)) from exc

    problems: list = []
    if len(grid_days) != r["corpus"]["day_count"]:
        problems.append({"kind": "day_count_mismatch", "declared": r["corpus"]["day_count"],
                         "observed": len(grid_days)})
    if day_set_sha(grid_days) != r["corpus"]["day_set_sha256"]:
        problems.append({"kind": "day_set_sha256_mismatch"})
    if r["corpus"]["scope"] == "full":
        dev = dev_days()
        if grid_days != dev:
            problems.append({
                "kind": "corpus_not_full_dev_calendar",
                "n_dev": len(dev),
                "n_built": len(grid_days),
                "missing": sorted(set(dev) - set(grid_days))[:10],
            })
        if len(dev) != r["corpus"]["day_count"]:
            problems.append({"kind": "canonical_day_count_mismatch", "calendar": len(dev),
                             "declared": r["corpus"]["day_count"]})
        census = load_json(OBS_CONTRACT_PATH).get("reconciliation_guards", {})
        want_rows = census.get("day_registry_rows")
        if want_rows is not None and int(want_rows) != len(grid_days):
            problems.append({"kind": "day_count_vs_frozen_census", "expected": int(want_rows),
                             "observed": len(grid_days)})
    for need in ("paths",):
        if need not in payloads:
            problems.append({"kind": "shared_payload_absent", "layer": need})
    if problems:
        raise IntegrityRefusalError("corpus does not match the frozen record", problems)

    paths_bytes = read_payload(root, payloads["paths"], verify=verify, problems=problems)
    if problems or paths_bytes is None:
        raise IntegrityRefusalError("paths payload integrity", problems)
    paths_df = pl.read_parquet(io.BytesIO(paths_bytes))
    missing = [c for c in ("day", "ticker") if c not in paths_df.columns]
    if missing:
        raise IntegrityRefusalError(
            "paths payload lacks the prospective key column(s)",
            [{"kind": "paths_missing_key", "columns": missing}],
        )
    if "path_id" not in paths_df.columns:
        paths_df = paths_df.with_columns(
            (pl.col("day") + pl.lit("|") + pl.col("ticker")).alias("path_id")
        )
    paths_df = paths_df.filter(pl.col("day").is_in(grid_days)).select(
        ["day", "ticker", "path_id"]
    ).sort(["day", "ticker"])
    if paths_df.height == 0:
        raise IntegrityRefusalError("paths table carries no row for the frozen day set",
                                    [{"kind": "paths_empty"}])
    problems = []
    reg_bytes = read_payload(root, payloads.get("day_registry") or {},
                             verify=verify, problems=problems)
    era_of: dict[str, str] = {}
    if reg_bytes is not None:
        reg_df = pl.read_parquet(io.BytesIO(reg_bytes))
        cols = [c for c in ("day", "feed_era") if c in reg_df.columns]
        if "day" in cols:
            era_of = {
                str(row["day"]): str(row.get("feed_era") or "unknown")
                for row in reg_df.select(cols).iter_rows(named=True)
            }
    if r["corpus"]["scope"] == "full":
        census = load_json(OBS_CONTRACT_PATH).get("identity", {}).get("expected_panel_census", {})
        if census.get("paths") is not None and paths_df.height != int(census["paths"]):
            problems.append({"kind": "paths_census_mismatch", "expected": int(census["paths"]),
                             "observed": paths_df.height})
    if problems:
        raise IntegrityRefusalError("paths/day_registry integrity", problems)
    return {
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "blind_path": str(blind_path),
        "blind_sha256": sha256_file(blind_path),
        "payloads": payloads,
        "days": grid_days,
        "paths_df": paths_df,
        "era_of": era_of,
    }


# --------------------------------------------------------------------------- #
# extraction
# --------------------------------------------------------------------------- #
def asof_prefix_len(window_lo: int, window_len: int, asof_cut) -> int:
    """The declared window's usable prefix length at the decision clock (the ASOF cutoff).

    It is a function of the window and the cutoff ONLY - it never inspects validity, silence or
    gaps, so a silence/halt/provider gap can not truncate the causal window (the whole declared
    window up to the query instant stays usable, with per-minute validity carried separately).
    ``asof_cut`` is the last included bucket label (see ``--asof-cut``); ``None`` means the whole
    declared window is usable (retrospective / whole-span causal prefix).
    """
    n = max(0, int(window_len))
    if asof_cut is None:
        return n
    avail = int(asof_cut) - int(window_lo) + 1
    return max(0, min(n, avail))


def causal_keep_mask(n_pos: int, prefix_len) -> np.ndarray:
    """Causal-mode usable positions: ``position < prefix_len``, i.e. exactly the ASOF-available
    prefix of the declared window.

    ``prefix_len == 0`` (an empty as-of scope: no bucket is available at the decision clock) keeps
    NOTHING - it does not fall back to one observable bucket.  The tensor keeps its fixed ``n_pos``
    shape; with an empty scope every ``valid`` cell and every ``span_allowed`` cell is False
    (padding only, `values` are the computational 0.0), and ``query_t == start_t`` is the correct
    empty completion clock.
    """
    plen = np.asarray(prefix_len, dtype=np.int64)
    return np.arange(int(n_pos))[None, :] < plen[:, None]


def causal_query_t(window_lo: int, prefix_len: int) -> int:
    """The causal episode's decision clock, expressed in the observation contract's vocabulary.

    ``window_lo`` is the first bucket label of the window and ``prefix_len`` the usable prefix
    length (the ASOF cutoff length, never gap-truncated), so the last included bucket is
    ``window_lo + prefix_len - 1`` and its COMPLETION clock - the instant at which that bucket's
    close/counts become known, i.e. the decision instant of
    ``observation contract.time_convention.decision_close_rule`` ("a decision at completed bar
    et_cut is taken at (et_cut+1)*60 seconds after ET midnight") - is ``window_lo + prefix_len``.
    ``prefix_len == 0`` means no bucket is available, so the clock is the window start itself and
    the causal view exposes no usable position.
    """
    return int(window_lo) + max(int(prefix_len), 0)


def physical_activity_expr(grid_columns) -> tuple:
    """The declared physical-activity expression for one minute of the selected-path grid.

    A minute counts as OBSERVED only when the observation layer itself recorded a physical tape
    update in it: a SIP bar row (``bar_state != 'none'``) or at least one print
    (``print_state != 'no_print'``; ``excluded_prints_only`` is still a physical print update and
    is never collapsed into silence).  A known zero (``n_prints == 0``, zero volume) is a
    legitimate model INPUT value but must never bridge a fully silent minute, so validity of a
    count/volume channel is deliberately NOT the barrier test.  Absence is never turned into a
    halt flag: only the declared state vocabulary is read, and a grid that lacks both state
    columns is refused rather than guessed.
    """
    cols = set(grid_columns)
    axes = []
    expr = None
    if "bar_state" in cols:
        expr = pl.col("bar_state") != "none"
        axes.append("bar_state")
    if "print_state" in cols:
        p = pl.col("print_state") != "no_print"
        expr = p if expr is None else (expr | p)
        axes.append("print_state")
    return expr, axes


def _op_expr(col: str, op: str, value):
    c = pl.col(col)
    if op == "is_null":
        return c.is_null()
    if op == "is_not_null":
        return c.is_not_null()
    if op == "eq":
        return c == value
    if op == "neq":
        return c != value
    if op == "gt":
        return c > value
    if op == "ge":
        return c >= value
    if op == "lt":
        return c < value
    if op == "le":
        return c <= value
    raise ContractRefusalError(f"unsupported valid_when op {op!r}")


def _print_agg_expr(ch: dict) -> pl.Expr:
    agg = ch["aggregate"]
    if agg == "count":
        return pl.len().cast(pl.Float64)
    if agg == "size_sum":
        return pl.col("size").sum()
    if agg == "size_max":
        return pl.col("size").max()
    if agg == "notional_sum":
        return (pl.col("price") * pl.col("size")).sum()
    if agg == "vwap":
        vol = pl.col("size").sum()
        tot = (pl.col("price") * pl.col("size")).sum()
        return pl.when(vol > 0).then(tot / vol).otherwise(None)
    raise ContractRefusalError(f"unsupported prints aggregate {agg!r}")


def _selectable_expr(*, et_col: str, asof_cut, crop):
    sel = pl.col("within_session") & (pl.col(et_col) >= pl.col("_entry_et"))
    if asof_cut is not None:
        sel = sel & (pl.col(et_col) <= int(asof_cut))
    if crop is not None:
        sel = sel & (pl.col(et_col) >= int(crop[0])) & (pl.col(et_col) <= int(crop[1]))
    return sel


def extract_base_tensors(root: Path, corpus: dict, r: dict, *, asof_cut, verify: bool) -> dict:
    """Stream the grid/prints payloads into per-path base-channel raw vectors (NaN = absent).

    Selectability is evaluated on the coordinate columns (``within_session``,
    ``first_entry_et`` from the grid / the membership entry clock, the asof cut and the crop)
    and applied BEFORE any feature value enters a statistic: unselectable feature cells are
    dropped, never zero-filled.
    """
    channels = r["channels"]
    grid_ch = {
        cid: ch for cid, ch in channels.items() if ch["source"] == "grid" and not ch["derive"]
    }
    print_ch = {
        cid: ch for cid, ch in channels.items() if ch["source"] == "prints" and not ch["derive"]
    }
    base_ids = list(r["base_ids"])
    crop = r["segmentation"]["crop"]

    paths_df = corpus["paths_df"]
    n_paths = paths_df.height
    path_index = {str(p): i for i, p in enumerate(paths_df["path_id"].to_list())}
    by_day = {}
    for row in paths_df.group_by("day", maintain_order=True).agg(
        pl.col("ticker"), pl.col("path_id")
    ).sort("day").iter_rows(named=True):
        by_day[str(row["day"])] = ([str(t) for t in row["ticker"]],
                                   [str(p) for p in row["path_id"]])
    raw: dict[str, np.ndarray] = {cid: np.full((n_paths, T_SPAN), np.nan) for cid in base_ids}
    obs = np.zeros((n_paths, T_SPAN), dtype=bool)
    observed_axes: set = set()
    entry_et = np.full(n_paths, -1, dtype=np.int64)
    session_end = np.full(n_paths, -1, dtype=np.int64)
    problems: list = []
    days_read = 0
    bytes_read = 0
    prints_days_absent = 0

    # membership entry clock (authoritative: member_id = day|family|ticker|entry_et|entry_rank)
    mem_entry: dict[str, int] = {}
    opt_problems: list = []
    mem_bytes = read_payload(root, corpus["payloads"].get("memberships") or {},
                             verify=verify, problems=opt_problems)
    if mem_bytes is not None:
        mdf = pl.read_parquet(io.BytesIO(mem_bytes))
        if {"path_id", "entry_et"} <= set(mdf.columns):
            g = mdf.group_by("path_id").agg(pl.col("entry_et").min())
            mem_entry = {
                str(a): int(b)
                for a, b in zip(g["path_id"].to_list(), g["entry_et"].to_list(), strict=True)
            }

    for day in corpus["days"]:
        pinfo = (corpus["payloads"].get(f"selected_path_grid/{day}") or {})
        if not pinfo.get("path"):
            problems.append({"kind": "grid_payload_absent", "day": day})
            break
        data = read_payload(root, pinfo, verify=verify, problems=problems)
        if data is None or problems:
            break
        days_read += 1
        bytes_read += len(data)
        gdf = pl.read_parquet(io.BytesIO(data))
        if pinfo.get("rows") is not None and gdf.height != int(pinfo["rows"]):
            problems.append({"kind": "payload_rows_mismatch", "day": day,
                             "declared": pinfo["rows"], "scanned": gdf.height})
            break
        if day not in by_day:
            problems.append({"kind": "paths_missing_for_day", "day": day})
            break
        tickers, pids = by_day[day]
        loc = {t: i for i, t in enumerate(tickers)}
        gcols = set(gdf.columns)
        if "et" not in gcols or "ticker" not in gcols or "within_session" not in gcols:
            problems.append({"kind": "grid_missing_coordinate_columns", "day": day,
                             "columns": sorted({"et", "ticker", "within_session"} - gcols)})
            break
        grid_tickers = {str(t) for t in gdf["ticker"].unique().to_list()}
        if grid_tickers != set(tickers):
            problems.append({"kind": "grid_paths_disagree", "day": day,
                             "only_grid": sorted(grid_tickers - set(tickers))[:5],
                             "only_paths": sorted(set(tickers) - grid_tickers)[:5]})
            break
        # entry clock: the grid's own prospective column when present, else the membership clock
        if "first_entry_et" in gcols:
            gdf = gdf.with_columns(pl.col("first_entry_et").alias("_entry_et"))
        elif pids:
            vals = [int(mem_entry.get(p, -1)) for p in pids]
            if any(v < 0 for v in vals):
                problems.append({"kind": "entry_clock_unavailable", "day": day})
                break
            gdf = gdf.with_columns(
                pl.col("ticker").replace_strict(
                    dict(zip(tickers, vals, strict=True)), default=-1
                ).alias("_entry_et")
            )
        else:
            problems.append({"kind": "entry_clock_unavailable", "day": day})
            break
        aggs = [pl.col("_entry_et").min().alias("_ent")]
        if "session_end" in gcols:
            aggs.append(pl.col("session_end").first().alias("_se"))
        m = gdf.group_by("ticker").agg(aggs)
        ent_map = {str(r["ticker"]): int(r["_ent"]) for r in m.iter_rows(named=True)}
        se_map = ({str(r["ticker"]): int(r["_se"]) for r in m.iter_rows(named=True)}
                  if "session_end" in gcols else {})
        for t, p in zip(tickers, pids, strict=True):
            idx_p = path_index[p]
            if t in ent_map:
                entry_et[idx_p] = ent_map[t]
            if t in se_map:
                session_end[idx_p] = se_map[t]
        sel = _selectable_expr(et_col="et", asof_cut=asof_cut, crop=crop)
        act_expr, act_axes = physical_activity_expr(gcols)
        if act_expr is None:
            problems.append({
                "kind": "grid_missing_physical_state_axes",
                "day": day,
                "detail": "neither bar_state nor print_state is present, so a silent minute "
                          "cannot be distinguished from a known zero; refusing rather than "
                          "fabricating a barrier map",
            })
            break
        observed_axes = sorted(set(observed_axes) | set(act_axes))
        exprs = []
        for cid, ch in grid_ch.items():
            vw = ch["valid_when"]
            if ch["column"] not in gcols or (vw["column"] not in gcols):
                problems.append({"kind": "grid_missing_channel_column", "day": day,
                                 "channel": cid})
                break
            exprs.append(
                pl.when(sel & _op_expr(vw["column"], vw["op"], vw.get("value")))
                .then(pl.col(ch["column"])).otherwise(None).alias(cid)
            )
        if problems:
            break
        needed = ["ticker", "et", "within_session", "_entry_et"]
        for ch in grid_ch.values():
            needed.append(ch["column"])
            needed.append(ch["valid_when"]["column"])
        needed = list(dict.fromkeys(needed))
        fdf = gdf.select(needed).with_columns(exprs)
        fdf = fdf.with_columns((sel & act_expr).alias("_act"))
        t_np = np.array([loc.get(str(t), -1) for t in fdf["ticker"].to_list()], dtype=np.int64)
        e_np = fdf["et"].to_numpy().astype(np.int64) - ET_LO
        ok = (e_np >= 0) & (e_np < T_SPAN) & (t_np >= 0)
        act_np = fdf["_act"].to_numpy().astype(bool)
        act_good = ok & act_np
        act_rows = np.array([path_index[pids[i]] for i in t_np[act_good]], dtype=np.int64)
        obs[act_rows, e_np[act_good]] = True
        for cid in grid_ch:
            vals = fdf[cid].to_numpy().astype(np.float64)
            good = ok & np.isfinite(vals)
            rows = np.array([path_index[pids[i]] for i in t_np[good]], dtype=np.int64)
            raw[cid][rows, e_np[good]] = vals[good]
        del fdf, gdf

        if print_ch:
            pp = corpus["payloads"].get(f"selected_path_prints/{day}") or {}
            if not pp.get("path"):
                prints_days_absent += 1
            else:
                pbytes = read_payload(root, pp, verify=verify, problems=problems)
                if pbytes is None or problems:
                    break
                bytes_read += len(pbytes)
                pdf = pl.read_parquet(io.BytesIO(pbytes))
                if pp.get("rows") is not None and pdf.height != int(pp["rows"]):
                    problems.append(
                        {"kind": "payload_rows_mismatch", "layer": "prints", "day": day}
                    )
                    break
                need = {"ticker", "et_min", "within_session"}
                if "first_entry_et" not in pdf.columns and "first_entry_et" not in gcols:
                    problems.append({"kind": "prints_entry_clock_unavailable", "day": day})
                    break
                if not need <= set(pdf.columns):
                    problems.append({"kind": "prints_missing_columns", "day": day})
                    break
                if "first_entry_et" in pdf.columns:
                    pdf = pdf.with_columns(pl.col("first_entry_et").alias("_entry_et"))
                    psel = _selectable_expr(et_col="et_min", asof_cut=asof_cut, crop=crop)
                else:
                    pdf = pdf.with_columns(
                        pl.col("ticker").replace_strict(
                            {t: int(mem_entry.get(p, -1))
                             for t, p in zip(tickers, pids, strict=True)}, default=-1
                        ).alias("_entry_et")
                    )
                    psel = _selectable_expr(et_col="et_min", asof_cut=asof_cut, crop=crop)
                g = (pdf.filter(psel)
                     .group_by(["ticker", "et_min"])
                     .agg([_print_agg_expr(ch).alias(cid) for cid, ch in print_ch.items()])
                     .sort(["ticker", "et_min"]))
                if g.height:
                    gt = np.array([loc.get(str(t), -1) for t in g["ticker"].to_list()],
                                  dtype=np.int64)
                    ge = g["et_min"].to_numpy().astype(np.int64) - ET_LO
                    gok = (ge >= 0) & (ge < T_SPAN) & (gt >= 0)
                    # a stored print row is itself a physical tape update (never silence)
                    prows = np.array([path_index[pids[i]] for i in gt[gok]], dtype=np.int64)
                    obs[prows, ge[gok]] = True
                    observed_axes.add("print_rows")
                    for cid in print_ch:
                        vals = g[cid].to_numpy().astype(np.float64)
                        good = gok & np.isfinite(vals)
                        rows = np.array([path_index[pids[i]] for i in gt[good]], dtype=np.int64)
                        raw[cid][rows, ge[good]] = vals[good]
                del pdf, g
        if problems:
            break

    if problems:
        raise IntegrityRefusalError("extraction integrity", problems)
    anchor_cid = r["scale"]["anchor_channel"]
    anchor_pos = np.full(n_paths, -1, dtype=np.int64)
    valid = np.isfinite(raw[anchor_cid])
    has = valid.any(axis=1)
    anchor_pos[has] = np.argmax(valid[has], axis=1)
    return {
        "path_index": path_index,
        "raw": raw,
        "obs": obs,
        "observed_axes": sorted(observed_axes),
        "base_ids": base_ids,
        "anchor_pos": anchor_pos,
        "entry_et": entry_et,
        "session_end": session_end,
        "stats": {"days_read": days_read, "bytes_read": bytes_read,
                  "prints_days_absent": prints_days_absent},
    }


def _window(arr: np.ndarray, anchor: int, scale: int, crop) -> tuple[np.ndarray, np.ndarray]:
    """Slice [anchor, anchor+scale) out of the 401-minute raw axis, offset-preserving: index
    ``i`` always means minute ``anchor+i`` (a crop shrinks the window, it never re-aligns it)."""
    out = np.full(scale, np.nan)
    msk = np.zeros(scale, dtype=bool)
    if anchor < 0:
        return out, msk
    lo, hi = anchor, min(anchor + scale - 1, ET_HI)
    if crop is not None:
        lo, hi = max(lo, int(crop[0])), min(hi, int(crop[1]))
    if hi < lo:
        return out, msk
    src = arr[lo - ET_LO: hi - ET_LO + 1]
    off = lo - anchor
    out[off: off + src.size] = src
    msk[off: off + src.size] = np.isfinite(src)
    return out, msk


def apply_transforms(win_raw: dict, win_msk: dict, r: dict) -> tuple[dict, dict]:
    """Per-object transforms on the windowed raw values; validity travels with the value."""
    out: dict[str, np.ndarray] = {}
    msk: dict[str, np.ndarray] = {}
    for cid in r["base_ids"]:
        ch = r["channels"][cid]
        x = win_raw.get(cid)
        vm = win_msk.get(cid)
        v = np.zeros(0, dtype=bool) if vm is None else vm.copy()
        if x is None:
            raise IntegrityRefusalError(f"channel {cid!r} missing from the extraction")
        t = ch["transform"]
        if t == "identity":
            y = x.copy()
        elif t == "log1p":
            with np.errstate(invalid="ignore", divide="ignore"):
                y = np.log1p(x)
            v = v & np.isfinite(y)
        elif t == "log_level":
            y = np.full_like(x, np.nan)
            g = v & (x > 0)
            y[g] = np.log(x[g])
            v = g
        elif t == "first_difference":
            y = np.full_like(x, np.nan)
            g = v.copy()
            if g.size:
                g[0] = False
            pair = v[:-1] & g[1:]
            y[1:][pair] = x[1:][pair] - x[:-1][pair]
            v = g & np.isfinite(y)
        elif t == "log_ratio_to_reference":
            rx = win_raw.get(ch["reference"]["of"])
            rm = win_msk.get(ch["reference"]["of"])
            if rx is None or rm is None:
                raise IntegrityRefusalError(f"reference channel {ch['reference']['of']!r} missing")
            ok = np.flatnonzero(rm & (rx > 0))
            y = np.full_like(x, np.nan)
            if ok.size == 0:
                v = np.zeros_like(v)
            else:
                ref = float(rx[ok[0]])
                g = v & (x > 0)
                y[g] = np.log(x[g] / ref)
                v = g & np.isfinite(y)
        else:  # validated vocabulary
            raise ContractRefusalError(f"unsupported transform {t!r}")
        out[cid] = np.where(v, y, np.nan)
        msk[cid] = v
    for cid in r["derived_ids"]:
        ch = r["channels"][cid]
        src = msk[ch["of"]]
        if ch["derive"] == "presence":
            y = src.astype(np.float64)
        elif ch["derive"] == "gap_minutes":
            y = np.full(src.size, np.nan)
            last = -1
            for i in range(src.size):
                if src[i]:
                    last = i
                    y[i] = 0.0
                elif last >= 0:
                    y[i] = float(i - last)
        else:  # validated vocabulary
            raise ContractRefusalError(f"unsupported derive {ch['derive']!r}")
        v = np.isfinite(y)
        if ch["transform"] == "log1p":
            with np.errstate(invalid="ignore", divide="ignore"):
                y = np.log1p(y)
            v = v & np.isfinite(y)
        out[cid] = np.where(v, y, np.nan)
        msk[cid] = v
    return out, msk


def build_object_tensors(extract: dict, corpus: dict, r: dict, *, asof_cut) -> dict:
    """Slice every object's scale window and apply the declared transforms."""
    all_ids = r["all_ids"]
    ladder = r["scale"]["ladder"]
    paths_df = corpus["paths_df"]
    pids = [str(p) for p in paths_df["path_id"].to_list()]
    tickers = [str(t) for t in paths_df["ticker"].to_list()]
    days = [str(d) for d in paths_df["day"].to_list()]
    n_paths = len(pids)
    n_scales = len(ladder)
    t_max = max(ladder)
    n_objects = n_paths * n_scales
    n_ch = len(all_ids)
    values = np.full((n_objects, n_ch, t_max), np.nan, dtype=np.float64)
    valid = np.zeros((n_objects, n_ch, t_max), dtype=bool)
    scale_of = np.zeros(n_objects, dtype=np.int64)
    path_of = np.zeros(n_objects, dtype=np.int64)
    anchor_et = np.full(n_objects, -1, dtype=np.int64)
    win_lo = np.full(n_objects, -1, dtype=np.int64)
    win_hi = np.full(n_objects, -1, dtype=np.int64)
    prefix_len = np.zeros(n_objects, dtype=np.int64)
    observed = np.zeros((n_objects, t_max), dtype=bool)
    crop = r["segmentation"]["crop"]
    names = {cid: i for i, cid in enumerate(all_ids)}
    anchor_pos = extract["anchor_pos"]
    for pi in range(n_paths):
        a = int(anchor_pos[pi])
        base_raw = {cid: extract["raw"][cid][pi] for cid in r["base_ids"]}
        for si, s in enumerate(ladder):
            oi = pi * n_scales + si
            scale_of[oi] = s
            path_of[oi] = pi
            if a < 0:
                continue
            wr, wm = {}, {}
            anchor_label = a + ET_LO
            for cid in r["base_ids"]:
                wr[cid], wm[cid] = _window(base_raw[cid], anchor_label, s, crop)
            out, msk = apply_transforms(wr, wm, r)
            l_win = max(0, min(s, ET_HI - anchor_label + 1))
            for cid in all_ids:
                ci = names[cid]
                values[oi, ci, :l_win] = out[cid][:l_win]
                valid[oi, ci, :l_win] = msk[cid][:l_win]
            off = a  # object position i is the raw-axis position a+i (anchor_pos is an index)
            obs_win = extract["obs"][pi, off: off + l_win]
            observed[oi, :l_win] = obs_win
            anchor_et[oi] = anchor_label
            win_lo[oi] = anchor_label
            win_hi[oi] = min(anchor_label + s - 1, ET_HI)
            # prefix_len is the ASOF clock cutoff / padding length: the declared window length
            # truncated at the decision-clock cutoff.  It NEVER stops at a silence, halt or
            # provider gap - the whole declared window up to the query instant stays usable, with
            # per-minute validity carried explicitly in `valid` (legitimate known-zero activity
            # included).  Silence only bars contiguous reconstruction masking (span_allowed).
            prefix_len[oi] = asof_prefix_len(anchor_label, l_win, asof_cut)
    return {
        "values": values, "valid": valid, "scale": scale_of, "path_of": path_of,
        "observed": observed,
        "observed_axes": extract["observed_axes"],
        "anchor_et": anchor_et, "window_lo": win_lo, "window_hi": win_hi,
        "prefix_len": prefix_len, "channel_ids": all_ids, "path_ids": pids,
        "tickers": tickers, "days": days, "asof_cut": asof_cut,
        "entry_et": extract["entry_et"], "session_end": extract["session_end"],
    }


def object_metadata(tensors: dict, corpus: dict) -> pl.DataFrame:
    rows = []
    for oi in range(tensors["values"].shape[0]):
        pi = int(tensors["path_of"][oi])
        day = tensors["days"][pi]
        scale = int(tensors["scale"][oi])
        lo, hi = int(tensors["window_lo"][oi]), int(tensors["window_hi"][oi])
        rows.append({
            "object_id": f"{tensors['path_ids'][pi]}@{scale}",
            "path_id": tensors["path_ids"][pi],
            "day": day,
            "ticker": tensors["tickers"][pi],
            "block": block_of(day),
            "era": corpus["era_of"].get(day, "unknown"),
            "scale": scale,
            "segment_id": f"{tensors['path_ids'][pi]}@{scale}[{lo},{hi}]",
            "start_et": lo,
            "end_et": hi,
            "anchor_et": int(tensors["anchor_et"][oi]),
            "anchor_bar_index": (
                int(tensors["anchor_et"][oi]) - ET_LO if tensors["anchor_et"][oi] >= 0 else None
            ),
            "prefix_len": int(tensors["prefix_len"][oi]),
            "session_end": int(tensors["session_end"][pi]),
            "first_entry_et": int(tensors["entry_et"][pi]),
            "retrospective_only": bool(tensors["asof_cut"] is None),
            "live": bool(tensors["asof_cut"] is not None),
            "detected_asof_et": None if tensors["asof_cut"] is None else int(tensors["asof_cut"]),
            # completion clock: the ET minute at which the last included (completed) bucket's
            # close/counts become known - observation contract decision_close_rule
            "decision_instant_et": (
                None if tensors["asof_cut"] is None else int(tensors["asof_cut"]) + 1
            ),
        })
    return pl.DataFrame(rows)


# --------------------------------------------------------------------------- #
# metric: exact masked squared distances
# --------------------------------------------------------------------------- #
def fit_normalization(tensors: dict, r: dict, fit_mask: np.ndarray) -> dict:
    """Per-channel z-score statistics from the fit-block valid entries only."""
    values, valid = tensors["values"], tensors["valid"]
    stats = {}
    for ci, cid in enumerate(tensors["channel_ids"]):
        m = valid[:, ci, :] & fit_mask[:, None]
        vals = values[:, ci, :][m]
        if vals.size == 0:
            raise IntegrityRefusalError(
                f"channel {cid!r} has no valid entry in the fit block; it cannot be "
                "standardized on held-out-only evidence",
                [{"kind": "channel_fit_support_empty", "channel": cid}],
            )
        mu, sd = float(np.mean(vals)), float(np.std(vals))
        if not np.isfinite(sd) or sd <= 0:
            raise IntegrityRefusalError(
                f"channel {cid!r} has zero variance over the fit block; a constant channel "
                "carries no geometry",
                [{"kind": "channel_fit_zero_variance", "channel": cid}],
            )
        stats[cid] = {"mean": mu, "std": sd, "n_fit": int(vals.size)}
    weights = {}
    for vname in PRIMITIVE_VIEWS:
        for cid, w in r["views"][vname]["weights"].items():
            weights[cid] = float(w)
    return {"channels": stats, "weights": weights}


def view_gram(tensors: dict, norm: dict, channel_ids: list, weights: dict) -> tuple:
    """Standardized, weight-folded, mask-zeroed arrays for the Gram identity.

    Returns ``(u_z, q_z2, m_valid, m_3d)``: ``u_z = mask * z``, ``q_z2 = mask * z**2``,
    ``m_valid`` the flattened validity mask.  Matrix parameter names are part of the frozen
    cross-module NN interface.
    """
    values, valid = tensors["values"], tensors["valid"]
    idx = {cid: i for i, cid in enumerate(tensors["channel_ids"])}
    n_rows, _, n_pos = values.shape
    n_ch = len(channel_ids)
    z = np.zeros((n_rows, n_ch, n_pos), dtype=np.float64)
    m = np.zeros((n_rows, n_ch, n_pos), dtype=bool)
    for k, cid in enumerate(channel_ids):
        st = norm["channels"][cid]
        w = float(weights.get(cid, 1.0))
        ci = idx[cid]
        v = valid[:, ci, :]
        z[:, k, :] = np.where(v, (values[:, ci, :] - st["mean"]) / st["std"], 0.0) * np.sqrt(w)
        m[:, k, :] = v
    u_z = z.reshape(n_rows, n_ch * n_pos)
    q_z2 = np.where(m, z * z, 0.0).reshape(n_rows, n_ch * n_pos)
    m_valid = m.reshape(n_rows, n_ch * n_pos).astype(np.float64)
    return u_z, q_z2, m_valid, m


def masked_sq_dists(u_z: np.ndarray, q_z2: np.ndarray, m_valid: np.ndarray,
                    block: slice) -> np.ndarray:
    """Exact masked squared L2 for a query block: q.m + m.q - 2 u.u, undefined when no
    commonly valid position exists (never imputed to zero)."""
    dist2 = (q_z2[block] @ m_valid.T + m_valid[block] @ q_z2.T
             - 2.0 * (u_z[block] @ u_z.T))
    common = m_valid[block] @ m_valid.T
    np.maximum(dist2, 0.0, out=dist2)
    dist2[common <= 0.0] = np.inf
    return dist2


def topk_from_dist(dist2: np.ndarray, k: int) -> tuple:
    rows, n_cols = dist2.shape
    k_eff = max(1, min(k, n_cols))
    part = np.argpartition(dist2, k_eff - 1, axis=1)[:, :k_eff]
    vals = np.take_along_axis(dist2, part, axis=1)
    order = np.argsort(vals, axis=1, kind="stable")
    idx = np.take_along_axis(part, order, axis=1)
    val = np.take_along_axis(vals, order, axis=1)
    good = np.isfinite(val)
    out_idx = np.full((rows, k), -1, dtype=np.int32)
    out_val = np.full((rows, k), np.nan, dtype=np.float64)
    for r in range(rows):
        g = good[r]
        cnt = int(g.sum())
        if cnt:
            out_idx[r, :cnt] = idx[r][g][:k].astype(np.int32)
            out_val[r, :cnt] = val[r][g][:k]
    return out_idx, out_val


def search_view(u_z, q_z2, m_valid, *, k, radius, chunk, same_tape_cols,
                candidate_mask=None) -> dict:
    """Exact chunked retrieval for one view: neighbours at ``k``, density at the frozen
    ``radius``, k-th distance and the comparable-candidate count.

    ``same_tape_cols`` is a per-row list of column indices to exclude (the caller's own
    independence unit: overlapping source intervals for within-name geometry, same day for
    race geometry).  ``candidate_mask`` optionally restricts the candidate set (eval-only
    retrieval).  Public interface: callers import this name (matrix args positional).
    """
    n_rows = u_z.shape[0]
    nn_idx = np.full((n_rows, k), -1, dtype=np.int32)
    nn_dist = np.full((n_rows, k), np.nan, dtype=np.float64)
    density = np.zeros(n_rows, dtype=np.int64)
    kth = np.full(n_rows, np.nan, dtype=np.float64)
    n_finite = np.zeros(n_rows, dtype=np.int64)
    for s in range(0, n_rows, chunk):
        e = min(n_rows, s + chunk)
        dist2 = masked_sq_dists(u_z, q_z2, m_valid, slice(s, e))
        for r in range(e - s):
            cols = same_tape_cols[s + r]
            if cols.size:
                dist2[r, cols] = np.inf
        if candidate_mask is not None:
            dist2[:, ~candidate_mask] = np.inf
        density[s:e] = np.count_nonzero(dist2 <= radius, axis=1)
        n_finite[s:e] = np.count_nonzero(np.isfinite(dist2), axis=1)
        idx, val = topk_from_dist(dist2, k)
        nn_idx[s:e] = idx
        nn_dist[s:e] = val
        cnt = np.sum(np.isfinite(val), axis=1)
        for r in range(e - s):
            c = int(cnt[r])
            if c:
                kth[s + r] = val[r, c - 1]
    return {"nn_idx": nn_idx, "nn_dist": nn_dist, "density": density,
            "kth_dist": kth, "n_comparable": n_finite}


def _same_tape_columns(tensors: dict) -> list:
    """Overlapping-source-interval exclusion: every object sharing the query's tape (same
    day+ticker, same path) has an interval that overlaps the query window, so all of them are
    excluded - self always, and sibling scales always."""
    path_of = tensors["path_of"]
    by_path: dict[int, list] = {}
    for i, p in enumerate(path_of):
        by_path.setdefault(int(p), []).append(i)
    return [np.asarray(by_path[int(p)], dtype=np.int64) for p in path_of]


def distance_scale(u_z, q_z2, m_valid, sub_idx: np.ndarray, *, k: int, chunk: int,
                   same_tape_cols) -> float:
    """Per-view balanced-aggregation scale: the median over fit objects of their k-th neighbour
    distance computed inside the fit block (a fitted normalization constant, never a gate).

    ``sub_idx`` are GLOBAL object-row indices; the per-row exclusion list is remapped locally.
    """
    if sub_idx.size < 3:
        raise IntegrityRefusalError("the fit block has fewer than three objects",
                                    [{"kind": "fit_block_too_small"}])
    local = {int(v): i for i, v in enumerate(sub_idx)}
    sub_cols = []
    for v in sub_idx:
        sub_cols.append(np.asarray([local[j] for j in same_tape_cols[int(v)] if j in local],
                                   dtype=np.int64))
    u_sub, q_sub, m_sub = u_z[sub_idx], q_z2[sub_idx], m_valid[sub_idx]
    n_sub = u_sub.shape[0]
    kk = max(1, min(k, n_sub - 1))
    kth = []
    for s in range(0, n_sub, chunk):
        e = min(n_sub, s + chunk)
        dist2 = masked_sq_dists(u_sub, q_sub, m_sub, slice(s, e))
        for r in range(e - s):
            cols = sub_cols[s + r]
            if cols.size:
                dist2[r, cols] = np.inf
        part = np.argpartition(dist2, kk - 1, axis=1)[:, :kk]
        vals = np.take_along_axis(dist2, part, axis=1)
        finite = np.isfinite(vals)
        for r in range(e - s):
            f = vals[r][finite[r]]
            if f.size:
                kth.append(float(np.max(f)))
    if not kth:
        raise IntegrityRefusalError(
            "no fit-block object has a neighbour inside the fit block; the balanced view cannot "
            "be standardized", [{"kind": "fit_block_isolated"}])
    scale = float(np.median(np.asarray(kth, dtype=np.float64)))
    if not np.isfinite(scale) or scale <= 0:
        raise IntegrityRefusalError("the fitted view scale is not positive and finite",
                                    [{"kind": "view_scale_not_positive"}])
    return scale


# --------------------------------------------------------------------------- #
# external SSL embeddings (helper interface; no model family implemented here)
# --------------------------------------------------------------------------- #
def load_embeddings(path: Path, object_ids: list, *, fit_days: set, eval_days: set) -> dict:
    """Load an externally generated embedding archive.

    Required layout (the ``values`` / ``valid`` / ``channels`` layout this script emits for a
    masked TCN, plus ``object_ids``)::

        values     float32/float64 (N, C, T_max)  concatenated per-scale episodes, padded
        valid      bool            (N, C, T_max)  False on every padding position
        object_ids unicode         (N,)           the ``episode_id`` values ("<path_id>@<scale>")

    ``object_ids`` must be unique, non-empty and a subset of the corpus object set; episodes the
    archive omits are aligned to the corpus object order and get ``valid=False`` everywhere
    (they then have no learned-view neighbour - an explicit absence, never a fabricated one).
    A ``<path>.npz.meta.json`` sidecar must declare ``provenance.fit_days`` and
    ``provenance.eval_days``; an embedding trained across the frozen split is refused.
    """
    if not path.is_file():
        raise ContractRefusalError(f"embedding archive not found: {path}")
    with np.load(path, allow_pickle=False) as z:
        for key in ("values", "valid", "object_ids"):
            if key not in z:
                raise ContractRefusalError(f"embedding archive lacks required key {key!r}")
        values = np.asarray(z["values"], dtype=np.float64)
        valid = np.asarray(z["valid"]).astype(bool)
        ids = [str(x) for x in z["object_ids"]]
    if values.shape != valid.shape or values.ndim != 3:
        raise ContractRefusalError(
            f"embedding values/valid must share shape (N,C,T); got {values.shape}/{valid.shape}"
        )
    if not ids or len(set(ids)) != len(ids):
        raise ContractRefusalError("embedding object_ids must be non-empty and unique")
    unknown = set(ids) - set(object_ids)
    if unknown:
        raise ContractRefusalError(
            f"embedding object_ids contain {len(unknown)} id(s) not in the corpus object set: "
            f"{sorted(unknown)[:3]}"
        )
    order = {oid: i for i, oid in enumerate(ids)}
    n_objs = len(object_ids)
    n_ch, n_pos = values.shape[1], values.shape[2]
    out_vals = np.zeros((n_objs, n_ch, n_pos), dtype=np.float64)
    out_mask = np.zeros((n_objs, n_ch, n_pos), dtype=bool)
    missing = np.ones(n_objs, dtype=bool)
    for i, oid in enumerate(object_ids):
        src = order.get(oid)
        if src is None:
            continue
        out_vals[i] = values[src]
        out_mask[i] = valid[src]
        missing[i] = False
    side = Path(str(path) + ".meta.json")
    if not side.is_file():
        raise ContractRefusalError(
            f"embedding archive needs a metadata sidecar {side.name} declaring provenance"
        )
    meta = load_json(side)
    prov = (meta or {}).get("provenance")
    if not isinstance(prov, dict):
        raise ContractRefusalError("embedding sidecar must declare a provenance object")
    efit = set(prov.get("fit_days") or [])
    eeval = set(prov.get("eval_days") or [])
    if not efit or not eeval:
        raise ContractRefusalError("embedding provenance must declare fit_days and eval_days")
    if efit & eeval:
        raise ContractRefusalError("embedding fit_days and eval_days overlap")
    if not efit <= fit_days or not eeval <= eval_days:
        raise ContractRefusalError(
            "embedding fit/eval days are not inside the frozen fit/eval blocks "
            "(an embedding crossing the split is refused)"
        )
    return {
        "values": out_vals,
        "valid": out_mask,
        "missing": missing,
        "meta": meta,
        "provenance": {"fit_days": sorted(efit), "eval_days": sorted(eeval),
                       "model": prov.get("model"), "sha256": sha256_file(path),
                       "n_episodes_declared": int(len(ids)),
                       "n_objects_missing": int(missing.sum())},
    }


def embedding_gram(emb: dict, *, fit_mask: np.ndarray) -> tuple:
    values, valid = emb["values"], emb["valid"]
    n_rows, n_ch, n_pos = values.shape
    z = np.zeros((n_rows, n_ch, n_pos), dtype=np.float64)
    m = valid.copy()
    stats = []
    for c in range(n_ch):
        mc = m[:, c, :] & fit_mask[:, None]
        vals = values[:, c, :][mc]
        if vals.size == 0:
            raise IntegrityRefusalError(
                f"embedding channel {c} has no valid fit-block entry",
                [{"kind": "embedding_fit_support_empty", "channel": c}])
        mu, sd = float(np.mean(vals)), float(np.std(vals))
        if not np.isfinite(sd) or sd <= 0:
            raise IntegrityRefusalError(f"embedding channel {c} has zero fit-block variance",
                                        [{"kind": "embedding_fit_zero_variance", "channel": c}])
        z[:, c, :] = np.where(m[:, c, :], (values[:, c, :] - mu) / sd, 0.0)
        stats.append({"channel": c, "mean": mu, "std": sd, "n_fit": int(vals.size)})
    u_z = z.reshape(n_rows, n_ch * n_pos)
    q_z2 = np.where(m, z * z, 0.0).reshape(n_rows, n_ch * n_pos)
    m_valid = m.reshape(n_rows, n_ch * n_pos).astype(np.float64)
    return u_z, q_z2, m_valid, m, stats


# --------------------------------------------------------------------------- #
# main pipeline
# --------------------------------------------------------------------------- #
def run_geometry(corpus: dict, r: dict, *, root: Path, out_dir: Path, out_evidence: Path,
                 asof_cut, embeddings_path, verify: bool, chunk: int, k_override,
                 write_outputs: bool) -> dict:
    k = int(r["neighbours_k"] if k_override is None else k_override)
    if k < 1:
        raise ContractRefusalError("--top-k must be >= 1")
    extract = extract_base_tensors(root, corpus, r, asof_cut=asof_cut, verify=verify)
    tensors = build_object_tensors(extract, corpus, r, asof_cut=asof_cut)
    objects_df = object_metadata(tensors, corpus)
    n_objs = tensors["values"].shape[0]
    if n_objs < 3:
        raise IntegrityRefusalError("fewer than three objects",
                                    [{"kind": "object_count_too_small"}])
    day_of = np.asarray(tensors["days"])[tensors["path_of"]]
    block = np.asarray([block_of(d) for d in day_of])
    fit_mask = np.isin(block, r["blocks"]["fit"])
    eval_mask = np.isin(block, r["blocks"]["eval"])
    fit_days = set(day_of[fit_mask].tolist())
    eval_days = set(day_of[eval_mask].tolist())
    if not fit_mask.any() or not eval_mask.any():
        raise IntegrityRefusalError(
            "the frozen fit/eval blocks do not both cover the corpus days",
            [{"kind": "split_blocks_uncovered",
              "fit_objects": int(fit_mask.sum()), "eval_objects": int(eval_mask.sum())}])
    norm = fit_normalization(tensors, r, fit_mask)
    same_tape = _same_tape_columns(tensors)

    grams: dict[str, tuple] = {}
    view_scales: dict[str, float] = {}
    scales_out: dict[str, dict] = {}
    for vname in PRIMITIVE_VIEWS:
        v = r["views"][vname]
        grams[vname] = view_gram(tensors, norm, v["channels"], v["weights"])
        try:
            sc = distance_scale(grams[vname][0], grams[vname][1], grams[vname][2],
                                np.flatnonzero(fit_mask), k=v["scale_reference"]["k"],
                                chunk=chunk, same_tape_cols=same_tape)
        except IntegrityRefusalError as exc:
            raise IntegrityRefusalError(
                f"view {vname!r}: {exc} (channels {v['channels']}, k "
                f"{v['scale_reference']['k']}): the balanced reference cannot standardize a "
                "view whose typical fit-block neighbour distance is not positive",
                [{"kind": "view_scale_unfittable", "view": vname,
                  "channels": v["channels"], "k": v["scale_reference"]["k"],
                  "detail": exc.problems}]) from exc
        view_scales[vname] = sc
        scales_out[vname] = {"scale": sc, "k": v["scale_reference"]["k"],
                             "statistic": SCALE_STATISTIC, "fit_objects": int(fit_mask.sum())}

    learned = None
    if embeddings_path is not None:
        object_ids = objects_df["object_id"].to_list()
        learned = load_embeddings(Path(embeddings_path), object_ids,
                                  fit_days=fit_days, eval_days=eval_days)
        u_z, q_z2, m_valid, _, estats = embedding_gram(learned, fit_mask=fit_mask)
        grams["learned"] = (u_z, q_z2, m_valid, None)
        view_scales["learned"] = distance_scale(u_z, q_z2, m_valid, np.flatnonzero(fit_mask),
                                                k=k, chunk=chunk, same_tape_cols=same_tape)
        scales_out["learned"] = {"scale": view_scales["learned"], "k": k,
                                 "statistic": SCALE_STATISTIC,
                                 "fit_objects": int(fit_mask.sum()),
                                 "fit_channel_stats": estats,
                                 "provenance": learned["provenance"]}

    views_search = [*PRIMITIVE_VIEWS]
    if learned is not None:
        views_search.append("learned")
    results: dict[str, dict] = {}
    eval_only: dict[str, dict] = {}
    for vname in views_search + [BALANCED]:
        results[vname] = {
            "nn_idx": np.full((n_objs, k), -1, dtype=np.int32),
            "nn_dist": np.full((n_objs, k), np.nan, dtype=np.float64),
            "density": np.zeros(n_objs, dtype=np.int64),
            "kth_dist": np.full(n_objs, np.nan, dtype=np.float64),
            "n_comparable": np.zeros(n_objs, dtype=np.int64),
            "n_common_views": np.zeros(n_objs, dtype=np.int64),
        }
        eval_only[vname] = np.full((n_objs, k), -1, dtype=np.int32)

    learned_radius = r["radius"].get("learned")
    has_learned_radius = learned is not None and learned_radius is not None
    for s in range(0, n_objs, chunk):
        e = min(n_objs, s + chunk)
        dists_by_view = {}
        for vname in PRIMITIVE_VIEWS + (("learned",) if learned is not None else ()):
            u_z, q_z2, m_valid, _ = grams[vname]
            dist2 = masked_sq_dists(u_z, q_z2, m_valid, slice(s, e))
            for rr in range(e - s):
                cols = same_tape[rr + s]
                if cols.size:
                    dist2[rr, cols] = np.inf
            dists_by_view[vname] = dist2
        stack = np.stack([dists_by_view[v] / view_scales[v] for v in PRIMITIVE_VIEWS])
        finite = np.isfinite(stack)
        cnt = finite.sum(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            bal = np.where(finite, stack, 0.0).sum(axis=0) / np.maximum(cnt, 1)
        bal = np.where(cnt >= r["min_common_views"], bal, np.inf)
        dists_all = dict(dists_by_view)
        dists_all[BALANCED] = bal
        for vname, dist2 in dists_all.items():
            res = results[vname]
            if vname != "learned" or has_learned_radius:
                rad_v = learned_radius if vname == "learned" else r["radius"][vname]
                res["density"][s:e] = np.count_nonzero(dist2 <= rad_v, axis=1)
            else:
                res["density"][s:e] = -1  # no frozen radius for the external learned view
            res["n_comparable"][s:e] = np.count_nonzero(np.isfinite(dist2), axis=1)
            if vname == BALANCED:
                res["n_common_views"][s:e] = cnt.max(axis=1)
            idx, val = topk_from_dist(dist2, k)
            res["nn_idx"][s:e] = idx
            res["nn_dist"][s:e] = val
            cntk = np.sum(np.isfinite(val), axis=1)
            for rr in range(e - s):
                c = int(cntk[rr])
                if c:
                    res["kth_dist"][s + rr] = val[rr, c - 1]
            dist_eval = dist2.copy()
            dist_eval[:, ~eval_mask] = np.inf
            idxe, _ = topk_from_dist(dist_eval, k)
            eval_only[vname][s:e] = idxe

    # ---- published measurements -------------------------------------------------
    def nn_sets(idx_row):
        return {int(x) for x in idx_row if x >= 0}

    agreement = {}
    for i, name_a in enumerate(views_search + [BALANCED]):
        for name_b in (views_search + [BALANCED])[i + 1:]:
            jac = []
            for q in range(n_objs):
                set_a = nn_sets(results[name_a]["nn_idx"][q])
                set_b = nn_sets(results[name_b]["nn_idx"][q])
                if not set_a and not set_b:
                    continue
                jac.append(len(set_a & set_b) / max(1, len(set_a | set_b)))
            agreement[f"{name_a}|{name_b}"] = {
                "mean_jaccard": float(np.mean(jac)) if jac else None,
                "n_queries": len(jac),
            }

    view_stats = {}
    for vname in views_search + [BALANCED]:
        res = results[vname]
        finite_nn = np.isfinite(res["nn_dist"])
        sets = [nn_sets(row) for row in res["nn_idx"]]
        mutual = 0
        for q, nbrs in enumerate(sets):
            for j in nbrs:
                if q in sets[j]:
                    mutual += 1
        mutual //= 2
        eval_q = np.flatnonzero(eval_mask)
        ov = []
        for q in eval_q:
            set_a = nn_sets(results[vname]["nn_idx"][q])
            set_b = nn_sets(eval_only[vname][q])
            if not set_a and not set_b:
                continue
            ov.append(len(set_a & set_b) / max(1, len(set_a | set_b)))
        day_spread = []
        for q in range(n_objs):
            ds = {str(day_of[j]) for j in sets[q]}
            day_spread.append(len(ds))
        block_stats = {}
        for bname, bmask in (("block1", block == "block1"), ("block2", block == "block2")):
            sel = np.flatnonzero(bmask)
            if sel.size == 0:
                continue
            block_stats[bname] = {
                "n_objects": int(sel.size),
                "median_density": float(np.median(res["density"][sel])),
                "median_kth_dist": float(np.nanmedian(res["kth_dist"][sel]))
                if np.isfinite(res["kth_dist"][sel]).any() else None,
                "unique_zero_density": int(np.count_nonzero(res["density"][sel] == 0)),
            }
        era_stats = {}
        eras = sorted({str(e) for e in np.asarray(objects_df["era"])})
        for era in eras:
            sel = np.flatnonzero(np.asarray(objects_df["era"]) == era)
            if sel.size == 0:
                continue
            era_stats[era] = {
                "n_objects": int(sel.size),
                "median_density": float(np.median(res["density"][sel])),
                "unique_zero_density": int(np.count_nonzero(res["density"][sel] == 0)),
            }
        no_radius = vname == "learned" and not has_learned_radius
        view_stats[vname] = {
            "units": (r["balanced_units"] if vname == BALANCED else r["radius_units"]),
            "radius": (learned_radius if vname == "learned" else r["radius"][vname]),
            "density_status": ("not_published_no_frozen_radius" if no_radius else "published"),
            "n_objects": n_objs,
            "n_objects_with_a_neighbour": int(np.count_nonzero(finite_nn.any(axis=1))),
            "n_objects_with_fewer_than_k": int(np.count_nonzero(finite_nn.sum(axis=1) < k)),
            "n_objects_with_no_comparable_candidate": int(
                np.count_nonzero(res["n_comparable"] == 0)
            ),
            "density": ({"n": 0, "mean": None, "quantiles": {}, "note": "no frozen radius"}
                        if no_radius else quantiles(res["density"])),
            "kth_dist": quantiles(res["kth_dist"]),
            "mutual_neighbour_pairs": int(mutual),
            "mutual_rate": float(mutual / max(1, n_objs)),
            "neighbour_day_spread": quantiles(np.asarray(day_spread, dtype=np.float64)),
            "eval_topk_overlap_all_vs_eval_only": {
                "mean_jaccard": float(np.mean(ov)) if ov else None,
                "n_queries": len(ov),
            },
            "by_block": block_stats,
            "by_era": era_stats,
        }

    mag = results[MAGNITUDE]
    anchor_cid = r["scale"]["anchor_channel"]
    anchor_raw = extract["raw"][anchor_cid]
    anchor_vals = np.full(n_objs, np.nan)
    for oi in range(n_objs):
        pi = int(tensors["path_of"][oi])
        a = int(tensors["anchor_et"][oi])
        if a >= 0:
            anchor_vals[oi] = anchor_raw[pi, a - ET_LO]
    first_nn = mag["nn_idx"][:, 0].astype(np.int64)
    ok = first_nn >= 0
    gap = np.full(n_objs, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        a_ok = ok & (anchor_vals > 0)
        nnv = np.where(a_ok, anchor_vals[np.clip(first_nn, 0, n_objs - 1)], np.nan)
        gap[a_ok] = np.abs(np.log(nnv[a_ok]) - np.log(anchor_vals[a_ok]))
    magnitude_rank = {
        "audit_coordinate": f"{anchor_cid} (raw first valid window value: an audit "
                            "coordinate, never a distance channel)",
        "measure": "spearman(magnitude-view top-1 distance, |log anchor raw value gap|) at the "
                   "magnitude view's own top-1 neighbour",
        "spearman": spearman(mag["nn_dist"][:, 0], gap),
        "n_pairs": int(np.isfinite(gap).sum()),
    }

    insufficient = {}
    for vname in views_search:
        if vname == "learned":
            insufficient[vname] = int(np.count_nonzero(results[vname]["n_comparable"] == 0))
            continue
        cols = [tensors["channel_ids"].index(c) for c in r["views"][vname]["channels"]]
        if cols:
            empty = ~tensors["valid"][:, cols, :].any(axis=(1, 2))
            insufficient[vname] = int(np.count_nonzero(empty))
        else:
            insufficient[vname] = 0
    insufficient[BALANCED] = int(np.count_nonzero(results[BALANCED]["n_comparable"] == 0))
    unique_mass = {}
    for vname in views_search + [BALANCED]:
        den = results[vname]["density"]
        published = not (vname == "learned" and not has_learned_radius)
        zero = np.flatnonzero(den == 0) if published else np.zeros(0, dtype=np.int64)
        unique_mass[vname] = {
            "status": "published" if published else "not_published_no_frozen_radius",
            "n_unique_within_radius": int(zero.size) if published else None,
            "fraction": float(zero.size / n_objs) if published else None,
            "n_uncertain_fewer_than_k": int(np.count_nonzero(
                np.sum(np.isfinite(results[vname]["nn_dist"]), axis=1) < k)),
        }

    report = {
        "report_version": "freeze-r.geometry.v0",
        "node_id": "geometry.tape_atlas.hand_views",
        "status": "complete",
        "scope": r["corpus"]["scope"],
        "claims": "none: measurements of the observed tape under the frozen record only; no "
                  "finding, density gate or Freeze-R claim is made here",
        "retrospective_only": bool(asof_cut is None),
        "live": bool(asof_cut is not None),
        "asof_cut": None if asof_cut is None else int(asof_cut),
        "sources": {
            "freeze_r": {"path": r["path"], "sha256": r["sha256"],
                         "matrix_version": r["matrix_version"]},
            "observation_manifest": {"path": corpus["manifest_path"],
                                     "sha256": corpus["manifest_sha256"]},
            "blind_corpus_report": {"path": corpus["blind_path"],
                                    "sha256": corpus["blind_sha256"]},
            "observation_schema_sha256": sha256_file(SCHEMA_PATH),
            "observation_contract_sha256": sha256_file(OBS_CONTRACT_PATH),
            "causal_registry_sha256": sha256_file(REGISTRY_PATH),
            "contract_lock_sha256": sha256_file(LOCK_PATH),
            "script_sha256": sha256_file(HERE),
            "payloads": {"days": len(corpus["days"]), **extract["stats"]},
        },
        "input_contract": {
            "views": {
                vname: {
                    "channels": r["views"][vname]["channels"],
                    "metric_weights": r["views"][vname]["weights"],
                    "declared_transforms": r["views"][vname]["declared_transforms"],
                    "scale_reference": r["views"][vname]["scale_reference"],
                    "radius": r["views"][vname]["radius"],
                    "tier_cells": r["views"][vname]["tier_cells"],
                }
                for vname in HAND_VIEWS
            },
            "channel_declarations": r["channels"],
            "metric": METRIC,
            "scaler": "zscore fit on the fit-block valid entries only, weight-folded by sqrt(w)",
            "scale_ladder": r["scale"]["ladder"],
            "scale_anchor": r["scale"]["anchor"],
            "anchor_channel": r["scale"]["anchor_channel"],
            "segmentation": r["segmentation"],
            "split": {"fit_blocks": r["blocks"]["fit"], "eval_blocks": r["blocks"]["eval"],
                      "fit_days": sorted(fit_days), "eval_days": sorted(eval_days),
                      "fit_objects": int(fit_mask.sum()), "eval_objects": int(eval_mask.sum()),
                      "no_fit_day_in_eval": not (fit_days & eval_days)},
            "min_common_views": r["min_common_views"],
            "top_k": k,
            "declared_units": {"distance_units": r["radius_units"],
                               "balanced_units": r["balanced_units"]},
            "learned_view": (None if learned is None else {
                "source": "external SSL embedding archive (no model family implemented here)",
                "provenance": learned["provenance"],
                "radius": learned_radius,
                "density_status": ("published" if has_learned_radius
                                   else "not_published_no_frozen_radius"),
                "fit_channel_stats": scales_out["learned"].get("fit_channel_stats"),
                "not_in_balanced_reference": True,
            }),
            "neighbour_exclusion": "same tape (day+ticker/path) with an overlapping source "
                                   "interval, i.e. self and every sibling scale of the query",
            "fitted_channel_stats": norm["channels"],
            "fitted_view_scales": scales_out,
        },
        "objects": {
            "n_paths": int(corpus["paths_df"].height),
            "n_objects": int(n_objs),
            "n_scales": len(r["scale"]["ladder"]),
            "objects_per_path": {str(s): int((tensors["scale"] == s).sum())
                                 for s in r["scale"]["ladder"]},
            "n_objects_without_anchor": int(np.count_nonzero(tensors["anchor_et"] < 0)),
            "by_block": {b: int(np.count_nonzero(block == b)) for b in sorted(set(block))},
            "by_era": {e: int(np.count_nonzero(np.asarray(objects_df["era"]) == e))
                       for e in sorted({str(x) for x in objects_df["era"]})},
            "insufficient_per_view_n": dict(insufficient),
            "objects_parquet": OBJECTS_NAME,
        },
        "exactness": {
            "search": "exact_bruteforce_chunked",
            "ann_index": None,
            "recall_vs_ann": 1.0,
            "note": "no ANN index is built, so exactness is by construction; density-decile "
                    "ANN recall is not measurable without an ANN and is not fabricated",
            "density_decile_recall": "not_applicable_no_ann",
            "distance_identity": "D_ij = q_i.m_j + m_i.q_j - 2 u_i.u_j over "
                                 "mask-zeroed standardized channels; pairs with no commonly "
                                 "valid position are infinite, never zero",
            "chunk_rows": int(chunk),
        },
        "views": view_stats,
        "view_agreement": {
            "mean_topk_jaccard": agreement,
            "normalized_vs_magnitude_disagreement": agreement.get(f"{MAGNITUDE}|{SHAPE}"),
        },
        "recurrence": {v: {"mutual_neighbour_pairs": view_stats[v]["mutual_neighbour_pairs"],
                           "mutual_rate": view_stats[v]["mutual_rate"]}
                       for v in view_stats},
        "unique_and_uncertain_mass": unique_mass,
        "magnitude_rank_preservation": magnitude_rank,
        "rare_refs": {"path": RARE_NAME, "rule": "every object keeps its own path/interval/"
                     "clock/bar references whether or not it has neighbours; no neighbour is "
                     "ever fabricated"},
        "declared_gates": r["gates"],
        "adaptive_choice_ledger_required": True,
        "run_choices": {
            "asof_cut": None if asof_cut is None else int(asof_cut),
            "top_k": k,
            "chunk_rows": int(chunk),
            "embeddings": None if embeddings_path is None else str(embeddings_path),
            "note": "query parameters only; no representation choice is made or defaulted here",
        },
        "refusals": [],
    }
    if write_outputs:
        _write_outputs(out_dir, out_evidence, corpus, r, tensors, objects_df, results,
                       view_scales, scales_out, norm, report, learned, fit_mask, k)
    return report


def _write_outputs(out_dir, out_evidence, corpus, r, tensors, objects_df, results,
                   view_scales, scales_out, norm, report, learned, fit_mask, k) -> None:
    payloads = {}

    def reg(name, path: Path, rows=None):
        payloads[name] = {"path": str(path.relative_to(out_dir)) if out_dir in path.parents
                          else path.name, "rows": rows, "sha256": sha256_file(path)}

    objects_path = out_dir / OBJECTS_NAME
    write_parquet(objects_path, objects_df)
    reg("objects", objects_path, objects_df.height)
    write_json(out_dir / "objects.meta.json", {
        "keys": ["object_id"], "sort_order": ["day", "ticker", "scale"],
        "units": {"start_et": "et minute label", "end_et": "et minute label",
                  "anchor_et": "et minute label", "session_end": "et minute label"},
        "retrospective_only": report["retrospective_only"],
        "note": "coordinates and identity references are metadata; they are never distance "
                "channels or model inputs",
    })

    rare_rows = []
    viewnames = list(results)
    for oi in range(objects_df.height):
        if any(results[v]["density"][oi] == 0 for v in viewnames):
            row = objects_df.row(oi, named=True)
            row = {key: row[key] for key in (
                "object_id", "path_id", "day", "ticker", "block", "era", "scale", "segment_id",
                "start_et", "end_et", "anchor_et", "anchor_bar_index", "prefix_len",
                "session_end", "first_entry_et", "retrospective_only", "live", "detected_asof_et")}
            row["views_unique"] = ",".join(v for v in viewnames if results[v]["density"][oi] == 0)
            for v in viewnames:
                row[f"density_{v}"] = int(results[v]["density"][oi])
                row[f"n_comparable_{v}"] = int(results[v]["n_comparable"][oi])
            rare_rows.append(row)
    rare_df = pl.DataFrame(rare_rows) if rare_rows else pl.DataFrame(
        schema={"object_id": pl.String, "path_id": pl.String, "day": pl.String,
                "ticker": pl.String})
    write_parquet(out_dir / RARE_NAME, rare_df)
    reg("rare_refs", out_dir / RARE_NAME, rare_df.height)

    idx = {cid: i for i, cid in enumerate(tensors["channel_ids"])}
    for vname in PRIMITIVE_VIEWS:
        vcols = [idx[c] for c in r["views"][vname]["channels"]]
        vvals = tensors["values"][:, vcols, :]
        vmask = tensors["valid"][:, vcols, :]
        arrs = {
            "values": np.where(vmask, vvals, 0.0).astype(np.float32),
            "valid": vmask,
            "channels": np.asarray(r["views"][vname]["channels"], dtype="U64"),
        }
        path = out_dir / f"view_{vname}.tensor.npz"
        deterministic_npz(path, arrs)
        reg(f"view_{vname}_tensor", path, int(arrs["values"].shape[0]))
        write_json(out_dir / f"view_{vname}.tensor.meta.json", {
            "view": vname, "channels": r["views"][vname]["channels"],
            "metric_weights": r["views"][vname]["weights"], "metric": METRIC,
            "units": "standardized_channel_l2 (sqrt of the sum of squared z differences)",
            "fitted_channel_stats": {c: norm["channels"][c]
                                     for c in r["views"][vname]["channels"]},
            "layout": {"values": "float32 (N,C,T) 0.0 where valid=False",
                       "valid": "bool (N,C,T)", "channels": "unicode (C,)"},
            "object_order": "objects.parquet row order (day, ticker, scale)",
        })
    for vname in list(results):
        res = results[vname]
        path = out_dir / f"view_{vname}.nn.npz"
        arrs = {
            "nn_idx": res["nn_idx"], "nn_dist": res["nn_dist"].astype(np.float64),
            "density": res["density"], "kth_dist": res["kth_dist"],
            "n_comparable": res["n_comparable"], "n_common_views": res["n_common_views"],
        }
        deterministic_npz(path, arrs)
        reg(f"view_{vname}_nn", path, int(res["nn_idx"].shape[0]))
        write_json(out_dir / f"view_{vname}.nn.meta.json", {
            "view": vname, "k": k, "radius": r["radius"].get(vname),
            "density_status": ("published" if (vname in r["radius"])
                               else "not_published_no_frozen_radius (-1 sentinel)"),
            "units": ("standardized_channel_l2" if vname != BALANCED
                      else "standardized_view_distance"),
            "nn_idx": "int32 (N,k) objects.parquet row indices, -1 = no such neighbour",
            "nn_dist": "float64 (N,k) squared distance in the view's own units, NaN = absent",
            "self_and_overlapping_interval_excluded": True,
            "neighbour_own_refs": "resolve through objects.parquet[nn_idx] "
                                  "(path_id/segment_id/anchor_et/anchor_bar_index)",
        })

    if r["learned_inputs"]:
        observed = tensors["observed"]
        for scale in r["scale"]["ladder"]:
            sel = np.flatnonzero(tensors["scale"] == scale)
            n_pos = int(scale)
            chans = [idx[c] for c in r["learned_inputs"]]
            vals = tensors["values"][sel][:, chans, :n_pos]
            vmask = tensors["valid"][sel][:, chans, :n_pos]
            vals = np.where(vmask, vals, 0.0)
            obs = observed[sel][:, :n_pos]
            for mode in r["learned_modes"]:
                if mode == "causal":
                    plen = tensors["prefix_len"][sel]
                    keep = causal_keep_mask(n_pos, plen)
                    vmode = vmask & keep[:, None, :]
                    valsmode = np.where(vmode, vals, 0.0)
                    qt = np.array(
                        [causal_query_t(int(lo), int(p))
                         for lo, p in zip(tensors["window_lo"][sel], plen, strict=True)],
                        dtype=np.int64,
                    )
                    span_allowed = obs & keep
                else:
                    vmode = vmask
                    valsmode = vals
                    qt = tensors["window_hi"][sel]
                    span_allowed = obs.copy()
                path = out_dir / "ssl" / f"{scale}_{mode}.npz"
                arrs = {
                    "values": valsmode.astype(np.float32),
                    "valid": vmode,
                    "span_allowed": span_allowed,
                    "channels": np.asarray(r["learned_inputs"], dtype="U64"),
                    "episode_id": np.asarray([f"{tensors['path_ids'][p]}@{scale}"
                                              for p in tensors["path_of"][sel]], dtype="U128"),
                    "day": np.asarray([tensors["days"][p] for p in tensors["path_of"][sel]],
                                      dtype="U16"),
                    "ticker": np.asarray([tensors["tickers"][p] for p in tensors["path_of"][sel]],
                                         dtype="U32"),
                    "path_id": np.asarray([tensors["path_ids"][p] for p in tensors["path_of"][sel]],
                                          dtype="U64"),
                    "block": np.asarray([block_of(tensors["days"][p])
                                         for p in tensors["path_of"][sel]], dtype="U16"),
                    "start_t": tensors["window_lo"][sel].astype(np.int32),
                    "end_t": tensors["window_hi"][sel].astype(np.int32),
                    "query_t": qt.astype(np.int32),
                    "mode": np.asarray([mode] * sel.size, dtype="U16"),
                    "prefix_len": tensors["prefix_len"][sel].astype(np.int32),
                    "fit": fit_mask[sel],
                    "manifest_sha256": np.asarray(corpus["manifest_sha256"], dtype="U64"),
                    "freeze_r_sha256": np.asarray(r["sha256"], dtype="U64"),
                    "schema_sha256": np.asarray(sha256_file(SCHEMA_PATH), dtype="U64"),
                }
                deterministic_npz(path, arrs)
                reg(f"ssl_{scale}_{mode}", path, int(sel.size))
                write_json(out_dir / "ssl" / f"{scale}_{mode}.meta.json", {
                    "scale": int(scale), "mode": mode,
                    "layout":
                        {"values": "float32 (N,C,T) 0.0 at valid==False",
                         "valid": "bool (N,C,T) padding is a COMPUTATIONAL zero, never a "
                                  "market zero",
                         "span_allowed": "bool (N,T) RECONSTRUCTION-MASK sampling barrier ONLY "
                                         "(never a scope/embedding cutoff): True only where the "
                                         "observation layer recorded a PHYSICAL tape update in "
                                         "the minute (a SIP bar row or at least one print, i.e. "
                                         "bar_state != 'none' or print_state != 'no_print'; axes "
                                         "recorded as span_allowed_axes), False at a silent "
                                         "minute / provider gap / unknown coverage / padding, and "
                                         "it MAY become True again after a silent run. A known "
                                         "zero (n_prints == 0, zero volume) is a legitimate "
                                         "model INPUT where valid==True but never marks a minute "
                                         "observed. Absence is never turned into a halt flag.",
                         "prefix_len": "ASOF clock cutoff / padding length: the declared window "
                                       "length truncated at the decision-clock cutoff. It is a "
                                       "function of the window and the cutoff ONLY and never "
                                       "stops at a silence/halt/provider gap, so the whole "
                                       "declared window up to the query instant stays usable "
                                       "with per-minute validity carried in `valid`.",
                         "channels": "unicode (C,)"},
                    "span_allowed_axes": tensors["observed_axes"],
                    "span_allowed_semantics":
                        "a COORDINATE ONLY: never a model input, never a distance channel and "
                        "never a reconstruction target; consecutive True runs are the maximal "
                        "barrier-free PHYSICALLY OBSERVED spans of the episode (a legitimate "
                        "known-zero input value does not extend them)",
                    "episodes": "episode_id/day/ticker/path_id/block/start_t/end_t/query_t/"
                                "mode/prefix_len/fit are episode metadata arrays, never model "
                                "inputs and never distance channels",
                    "causal_prefix_rule": CAUSAL_PREFIX_RULE if mode == "causal" else None,
                    "causal_clock_rule": (
                        "causal query_t is the COMPLETION clock of the last included bucket "
                        "(last bucket label + 1), i.e. the instant at which that bucket's close "
                        "and counts become known per the observation contract's "
                        "decision_close_rule; the causal tensors expose the declared window up to "
                        "the ASOF cutoff (prefix_len buckets, never truncated at a silence), so no "
                        "bucket at or after the decision instant enters the episode. "
                        "prefix_len == 0 means the as-of scope is EMPTY (no bucket available): "
                        "every valid and span_allowed cell is False, values are computational "
                        "padding, and query_t == start_t" if mode == "causal" else None),
                    "retrospective_clock_rule": (
                        "retrospective query_t is the LABEL of the last included bucket (= end_t, "
                        "not a completion clock), because this variant is whole-span and is never "
                        "used live; the one-bucket lag is not relaxed for it" if mode != "causal"
                        else None),
                    "mask_vs_scope": (
                        "span_allowed is a reconstruction-mask sampling barrier only; the usable "
                        "region / scope cutoff is prefix_len (the ASOF clock cutoff). The two are "
                        "independent: silence bars masking but never narrows the usable window."),
                    "sanitized": "values are emitted as 0.0 exactly where valid==False; no "
                                 "in-window junk position can influence any emitted array "
                                 "(the extraction drops unselectable/absent cells before any "
                                 "transform), and the .npz is byte-deterministic (sorted "
                                 "members, fixed member timestamp)",
                    "scalar_provenance": ["manifest_sha256", "freeze_r_sha256", "schema_sha256"],
                    "fit_eval": "flag the fit/eval day blocks explicitly; never pool silently",
                })
    elif report is not None:
        report["ssl_archives"] = {"status": "UNBOUND",
                                  "reason": "learned_family.inputs is null in the frozen record"}

    write_json(out_evidence / REPORT_NAME, report)
    manifest = {
        "node_id": "geometry.tape_atlas.hand_views",
        "version": r["matrix_version"],
        "contract": "freeze-r-v0",
        "freeze_r": {"path": r["path"], "sha256": r["sha256"],
                     "matrix_version": r["matrix_version"]},
        "parents": {
            "observation_manifest": {"source_uri": Path(corpus["manifest_path"]).as_uri(),
                                     "sha256": corpus["manifest_sha256"], "tracked": False},
            "blind_corpus_report": {"source_uri": Path(corpus["blind_path"]).as_uri(),
                                    "sha256": corpus["blind_sha256"], "tracked": True},
            "observation_schema": {"source_uri": SCHEMA_PATH.as_uri(),
                                   "sha256": sha256_file(SCHEMA_PATH), "tracked": True},
            "observation_contract": {"source_uri": OBS_CONTRACT_PATH.as_uri(),
                                     "sha256": sha256_file(OBS_CONTRACT_PATH), "tracked": True},
            "causal_registry": {"source_uri": REGISTRY_PATH.as_uri(),
                                "sha256": sha256_file(REGISTRY_PATH), "tracked": True},
            "contract_lock": {"source_uri": LOCK_PATH.as_uri(),
                              "sha256": sha256_file(LOCK_PATH), "tracked": True},
        },
        "code_sha256": sha256_file(HERE),
        "schema": {
            "metric": METRIC,
            "views": {v: {"channels": r["views"][v]["channels"],
                          "weights": r["views"][v]["weights"],
                          "radius": r["views"][v]["radius"]} for v in HAND_VIEWS},
            "learned_inputs": r["learned_inputs"],
            "learned_modes": r["learned_modes"],
            "scale_ladder": r["scale"]["ladder"],
            "anchor_channel": r["scale"]["anchor_channel"],
        },
        "keys": {"objects": "object_id", "neighbours": "object row index"},
        "sort_order": {"objects": ["day", "ticker", "scale"]},
        "coverage_classes": sorted({str(x) for x in objects_df["block"]}),
        "selection_classes": {"scope": r["corpus"]["scope"], "asof_cut": report["asof_cut"],
                              "retrospective_only": report["retrospective_only"]},
        "payload_sha256": payloads,
        "status": "geometries_built",
        "supersedes": None,
        "withdraws": None,
    }
    manifest["core_hash"] = sha256_bytes(canon_bytes(manifest))
    write_json(out_evidence / MANIFEST_NAME, manifest)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Freeze-R within-name geometry (Stage 1)")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="absolute observation data root")
    ap.add_argument("--manifest", required=False, help="observation node manifest.json")
    ap.add_argument("--freeze-r", required=False, help="the frozen Freeze-R record")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="large deterministic output root")
    ap.add_argument("--out-evidence", default=str(DEFAULT_OUT_EVIDENCE))
    ap.add_argument("--top-k", type=int, default=None,
                    help="override first_retrieval_proof.neighbours_k (query parameter)")
    ap.add_argument("--asof-cut", type=int, default=None,
                    help="prospective read: the LAST INCLUDED BUCKET LABEL (inclusive). The "
                         "decision instant is its completion clock (asof_cut+1)*60 s after ET "
                         "midnight, so bucket-label filtering is exact and no bucket at or after "
                         "the decision instant enters the tensors; default = retrospective whole "
                         "span")
    ap.add_argument("--embeddings", default=None,
                    help="externally generated SSL embedding archive (.npz + .meta.json)")
    ap.add_argument("--chunk-rows", type=int, default=512, help="query chunk (bounded memory)")
    ap.add_argument("--no-verify-payload-sha", action="store_true",
                    help="skip only the payload sha256 re-read (rows/days/parents still checked)")
    ap.add_argument("--plan-only", action="store_true",
                    help="validate inputs and report the plan without writing outputs")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return run_selftest()
    if not args.manifest or not args.freeze_r:
        print("--manifest and --freeze-r are required (no default frozen record exists)",
              file=sys.stderr)
        return 2
    r = None
    try:
        schema = load_json(SCHEMA_PATH)
        registry = load_json(REGISTRY_PATH)
        r = load_freeze_r(Path(args.freeze_r), schema=schema, registry=registry)
        corpus = load_corpus(Path(args.root), Path(args.manifest), r,
                             verify=not args.no_verify_payload_sha)
    except ContractRefusalError as exc:
        print("geometry refusal (contract): " + str(exc), file=sys.stderr)
        return 2
    except IntegrityRefusalError as exc:
        print("geometry refusal (integrity): " + str(exc), file=sys.stderr)
        _write_refused(Path(args.out_evidence), r, exc.problems)
        return 3
    out_dir = Path(args.out)
    out_evidence = Path(args.out_evidence)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    if not out_evidence.is_absolute():
        out_evidence = ROOT / out_evidence
    try:
        report = run_geometry(corpus, r, root=Path(args.root), out_dir=out_dir,
                              out_evidence=out_evidence, asof_cut=args.asof_cut,
                              embeddings_path=args.embeddings,
                              verify=not args.no_verify_payload_sha,
                              chunk=int(args.chunk_rows), k_override=args.top_k,
                              write_outputs=not args.plan_only)
    except ContractRefusalError as exc:
        print("geometry refusal (contract): " + str(exc), file=sys.stderr)
        return 2
    except IntegrityRefusalError as exc:
        print("geometry refusal (integrity): " + str(exc), file=sys.stderr)
        _write_refused(out_evidence, r, exc.problems)
        return 3
    print(json.dumps({
        "status": report["status"],
        "scope": report["scope"],
        "objects": report["objects"],
        "exactness": report["exactness"]["search"],
        "views": sorted(report["views"]),
    }, indent=1)[:3000])
    return 0


def _write_refused(out_evidence: Path, r, problems: list) -> None:
    report = {
        "report_version": "freeze-r.geometry.v0",
        "node_id": "geometry.tape_atlas.hand_views",
        "status": "REFUSED",
        "claims": "none",
        "refusals": problems,
        "sources": {},
        "freeze_r": None if r is None else {"path": r["path"], "sha256": r["sha256"]},
    }
    write_json(out_evidence / REPORT_NAME, report)
    manifest = {
        "node_id": "geometry.tape_atlas.hand_views",
        "version": None if r is None else r["matrix_version"],
        "status": "REFUSED",
        "refusals": problems,
        "payload_sha256": {},
        "code_sha256": sha256_file(HERE),
    }
    manifest["core_hash"] = sha256_bytes(canon_bytes(manifest))
    write_json(out_evidence / MANIFEST_NAME, manifest)


# --------------------------------------------------------------------------- #
# synthetic regression (masking / independence / invariance / refusal boundaries)
# --------------------------------------------------------------------------- #
def run_selftest() -> int:
    checks: list = []

    def chk(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail or {}})

    schema = load_json(SCHEMA_PATH)
    registry = load_json(REGISTRY_PATH)

    def chan(**kw):
        base = {"id": "c1", "source": "grid", "column": "bar_close", "aggregate": None,
                "derive": None, "of": None, "transform": "identity", "reference": None,
                "valid_when": {"column": "bar_state", "op": "neq", "value": "none"}}
        base.update(kw)
        return base

    def frozen_record(**over):
        view = {
            "geometry_channels": [chan(id="close", transform="identity"),
                                  chan(id="vol", column="bar_volume", transform="log1p"),
                                  chan(id="hi", column="bar_high", transform="log_level")],
            "transforms": ["identity", "log1p", "log_level"],
            "scale_reference": {"statistic": SCALE_STATISTIC, "k": 5},
            "metric": METRIC,
            "metric_weights": {"close": 1.0, "vol": 1.0, "hi": 1.0},
            "radius": 10.0,
            "tier_availability": {"selected_paths": "materialized",
                                  "candidate_net_minute": "delegated",
                                  "broad_provider_minute": "unsupported",
                                  "full_checkpoint": "not_applicable"},
        }
        rec = {
            "matrix_version": "v0",
            "template_only": False,
            "frozen": True,
            "status": "FROZEN",
            "freeze": "R",
            "binding_slots": {
                "corpus": {"node_id": "observations.tape_atlas", "version": "v0",
                           "manifest_sha256": "0" * 64, "day_count": 1, "built": True,
                           "scope": "full", "day_set_sha256": "1" * 64},
                "blind_report": {"node_id": "x", "path": "/tmp/x.json", "sha256": "2" * 64,
                                 "complete": True, "permitted_summaries_only": True},
                "transform_training_blocks": {
                    "split_key": "block", "fit_blocks": ["block1"], "eval_blocks": ["block2"],
                    "no_overlapping_source_interval_crosses_split": True,
                    "normalization_and_model_params_fit_on_fit_blocks_only": True,
                    "frozen": True},
                "scale_choice": {"scale_ladder": [30], "scale_unit": "minute",
                                 "scale_anchor": "path_first_valid", "anchor_channel": "close",
                                 "alignment": "left", "frozen": True},
                "segmentation": {"segment_rules": {"mode": "whole_source_interval", "crop": None},
                                 "change_point_budget": 0, "frozen": True},
                "metric_weights": {
                    "per_view_weights": dict.fromkeys(PRIMITIVE_VIEWS, 1.0),
                    "balanced_reference_aggregation": BALANCED_AGG,
                    "balanced_reference_tuned_to_best_geometry": False,
                    "min_common_views": 1},
                "radius": {"neighbour_radius": None, "radius_units": "standardized_channel_l2",
                           "radius_per_view": dict.fromkeys(HAND_VIEWS, 10.0)},
                "ann_sparse_recall": {"index_build": "exact_bruteforce_no_ann",
                                      "density_decile_definition": "exact deciles of density",
                                      "exact_search_strata": "all",
                                      "sparse_exact_recall_floor": 1.0},
                "integer_seed": 1,
                "geometric_units": {"distance_units": "standardized_channel_l2",
                                    "balanced_units": "standardized_view_distance"},
                "numeric_one_shot_gates": dict.fromkeys(REQUIRED_GATES, 0.0)},
            "hand_views_meta": {"view_count": 5},
            "hand_views": {v: dict(view) for v in PRIMITIVE_VIEWS} | {
                BALANCED: {"geometry_channels": None, "aggregation": BALANCED_DECL,
                           "tier_availability": dict.fromkeys(
                               TIER_CELLS, "not_applicable") | {
                                   "selected_paths": "materialized"}}},
            "learned_family": {"inputs": [chan(id="lvl", column="bar_close",
                                               transform="log_level")],
                               "modes": {"causal": {}, "retrospective": {}}},
            "first_retrieval_proof": {"neighbours_k": 100,
                                      "independent_day_spread_required": True,
                                      "path_interval_disagreement_preserved": True},
        }
        for path_keys, value in over.items():
            node = rec
            keys = path_keys.split(".")
            for k in keys[:-1]:
                node = node[k]
            node[keys[-1]] = value
        return rec

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # ---- frozen-record refusals --------------------------------------- #
        def refuses(record, label):
            p = tmp / f"{label}.json"
            write_json(p, record)
            try:
                load_freeze_r(p, schema=schema, registry=registry)
                return None
            except ContractRefusalError as exc:
                return str(exc)

        ok = frozen_record()
        p = tmp / "ok.json"
        write_json(p, ok)
        try:
            loaded = load_freeze_r(p, schema=schema, registry=registry)
            chk("frozen_record_accepted", loaded["matrix_version"] == "v0")
        except ContractRefusalError as exc:
            chk("frozen_record_accepted", False, {"error": str(exc)})
        chk("template_refused", refuses(frozen_record(template_only=True), "t") is not None)
        chk("non_full_scope_refused",
            refuses(frozen_record(**{"binding_slots.corpus.scope": "canary"}), "sc1") is not None
            and refuses(frozen_record(**{"binding_slots.corpus.scope": "partial"}), "sc2")
            is not None)
        chk("unbound_numeric_gate_refused",
            refuses(frozen_record(**{"binding_slots.numeric_one_shot_gates":
                                     {**dict.fromkeys(REQUIRED_GATES, 0.0),
                                      "ssl_over_random_improvement": None}}), "g") is not None)
        chk("undeclared_tier_cell_refused",
            refuses(frozen_record(**{"hand_views.magnitude_dominant.tier_availability":
                                     {"selected_paths": "materialized"}}), "tc") is not None
            and refuses(frozen_record(**{"hand_views.magnitude_dominant.tier_availability": {
                "selected_paths": "materialized", "candidate_net_minute": "delegated",
                "broad_provider_minute": "unsupported", "full_checkpoint": "yes"}}), "tv")
            is not None
            and refuses(frozen_record(**{"hand_views.magnitude_dominant.tier_availability": {
                "selected_paths": "unsupported", "candidate_net_minute": "delegated",
                "broad_provider_minute": "unsupported", "full_checkpoint": "not_applicable"}}),
                "sp") is not None)
        chk("unbound_learned_inputs_refused",
            refuses(frozen_record(**{"learned_family.inputs": None}), "li") is not None)
        chk("unfrozen_refused", refuses(frozen_record(frozen=False), "u") is not None)
        chk("draft_status_refused", refuses(frozen_record(status="DRAFT-NOT-RUN"), "d") is not None)
        chk("null_binding_refused",
            refuses(frozen_record(**{"binding_slots.corpus.manifest_sha256": None}), "n")
            is not None)
        chk("bad_hex_refused",
            refuses(frozen_record(**{"binding_slots.blind_report.sha256": "zz"}), "h") is not None)
        chk("split_overlap_refused",
            refuses(frozen_record(**{"binding_slots.transform_training_blocks.eval_blocks":
                                     ["block1"]}), "s") is not None)
        chk("non_equal_weights_refused",
            refuses(frozen_record(**{"binding_slots.metric_weights.per_view_weights":
                                     {MAGNITUDE: 1.0, SHAPE: 2.0, DURATION: 1.0,
                                      ACTIVITY: 1.0}}), "w") is not None)
        chk("non_zero_budget_refused",
            refuses(frozen_record(**{"binding_slots.segmentation.change_point_budget": 3}),
                    "b") is not None)
        quote_rec = frozen_record()
        for v in PRIMITIVE_VIEWS:
            quote_rec["hand_views"][v]["geometry_channels"] = [
                chan(id="q", column="quote_mid", transform="identity",
                     valid_when={"column": "bar_state", "op": "neq", "value": "none"})]
            quote_rec["hand_views"][v]["metric_weights"] = {"q": 1.0}
        chk("quote_channel_refused", refuses(quote_rec, "q") is not None)
        coord_rec = frozen_record()
        for v in PRIMITIVE_VIEWS:
            coord_rec["hand_views"][v]["geometry_channels"] = [
                chan(id="blk", column="block", transform="identity",
                     valid_when={"column": "bar_state", "op": "neq", "value": "none"})]
            coord_rec["hand_views"][v]["metric_weights"] = {"blk": 1.0}
        chk("coordinate_channel_refused", refuses(coord_rec, "co") is not None)
        retro_rec = frozen_record()
        for v in PRIMITIVE_VIEWS:
            retro_rec["hand_views"][v]["geometry_channels"] = [
                chan(id="tc", column="terminal_censored", transform="identity",
                     valid_when={"column": "bar_state", "op": "neq", "value": "none"})]
            retro_rec["hand_views"][v]["metric_weights"] = {"tc": 1.0}
        chk("retrospective_channel_refused", refuses(retro_rec, "r") is not None)
        leak_rec = frozen_record()
        for v in PRIMITIVE_VIEWS:
            leak_rec["hand_views"][v]["geometry_channels"] = [
                chan(id="tv", column="tail_class_50", transform="identity",
                     valid_when={"column": "bar_state", "op": "neq", "value": "none"})]
            leak_rec["hand_views"][v]["metric_weights"] = {"tv": 1.0}
        chk("outcome_channel_refused", refuses(leak_rec, "le") is not None)
        chk("unknown_metric_refused",
            refuses(frozen_record(**{"hand_views.magnitude_dominant.metric": "cosine"}), "m")
            is not None)
        chk("unsupported_segmentation_refused",
            refuses(frozen_record(**{"binding_slots.segmentation.segment_rules":
                                     {"mode": "sliding", "crop": None}}), "seg") is not None)

        # ---- masked distance semantics ------------------------------------ #
        rng = np.random.default_rng(20260930)
        n_ch, n_pos, n_objs = 3, 7, 6
        z = rng.normal(size=(n_objs, n_ch, n_pos))
        m = rng.random(size=(n_objs, n_ch, n_pos)) > 0.3
        u_z = np.where(m, z, 0.0).reshape(n_objs, n_ch * n_pos)
        q_z2 = np.where(m, z * z, 0.0).reshape(n_objs, n_ch * n_pos)
        m_flat = m.reshape(n_objs, n_ch * n_pos).astype(np.float64)
        dist2 = masked_sq_dists(u_z, q_z2, m_flat, slice(0, n_objs))
        brute = np.zeros((n_objs, n_objs))
        for i in range(n_objs):
            for j in range(n_objs):
                common = m[i] & m[j]
                if common.any():
                    brute[i, j] = float(np.sum((z[i][common] - z[j][common]) ** 2))
                else:
                    brute[i, j] = np.inf
        diff = np.abs(dist2 - brute)
        finite = np.isfinite(brute)
        chk("gram_identity_matches_bruteforce",
            bool(np.allclose(dist2[finite], brute[finite], atol=1e-10)
                 and np.array_equal(np.isfinite(dist2), finite)),
            {"max_abs_diff": float(diff[finite].max()) if finite.any() else None})

        z0 = np.array([[[1.0, 2.0, 3.0]]])
        m0 = np.array([[[True, True, True]]])
        z1 = np.array([[[1.0, 99.0, 99.0]]])
        m1 = np.array([[[True, False, False]]])
        z2 = np.array([[[1.0, 0.0, 0.0]]])
        m2 = np.array([[[True, False, False]]])
        pairs = ((z0, m0), (z1, m1), (z2, m2))
        u3 = np.concatenate([np.where(mm, zz, 0.0).reshape(1, -1) for zz, mm in pairs])
        q3 = np.concatenate([np.where(mm, zz * zz, 0.0).reshape(1, -1) for zz, mm in pairs])
        m3 = np.concatenate([mm.reshape(1, -1).astype(np.float64) for mm in (m0, m1, m2)])
        d3 = masked_sq_dists(u3, q3, m3, slice(0, 3))
        chk("invalid_positions_excluded", float(d3[0, 1]) == 0.0, {"d01": float(d3[0, 1])})
        chk("garbage_under_mask_ignored", float(d3[1, 2]) == 0.0, {"d12": float(d3[1, 2])})
        naive = np.sum((np.where(m0, z0, 0.0) - np.where(m1, z1, 0.0)) ** 2)
        chk("absence_is_not_a_market_zero", float(naive) > 0.0 and float(d3[0, 1]) == 0.0,
            {"zero_imputation_distance": float(naive)})
        zc = np.array([[[1.0, 2.0, 3.0]], [[4.0, 5.0, 6.0]]])
        mc = np.array([[[True, False, False]], [[False, True, True]]])
        uc = (zc * mc).reshape(2, -1)
        qc = (zc * zc * mc).reshape(2, -1)
        mcc = mc.reshape(2, -1).astype(np.float64)
        dc = masked_sq_dists(uc, qc, mcc, slice(0, 2))
        chk("no_common_support_is_infinite", not np.isfinite(dc[0, 1]), {"d": float(dc[0, 1])})

        # ---- top-k / exclusion / independence ----------------------------- #
        dw = np.array([[0.0, 1.0, 2.0, 3.0], [1.0, 0.0, 1.5, 4.0]])
        idx, val = topk_from_dist(dw, 2)
        chk("topk_orders_by_distance",
            sorted(idx[0].tolist()) == [0, 1] and sorted(idx[1].tolist()) == [0, 1]
            and list(np.round(val[1], 6)) == [0.0, 1.0],
            {"idx0": idx[0].tolist(), "idx1": idx[1].tolist(), "val1": val[1].tolist()})
        di = np.array([[0.0, 1.0, 2.0, 3.0]])
        di[0, 0] = np.inf
        i2, v2 = topk_from_dist(di, 3)
        chk("self_never_a_neighbour", 0 not in list(i2[0]) and int(i2[0][0]) == 1)

        tens = {"path_of": np.array([0, 0, 1, 1, 2])}
        cols = _same_tape_columns(tens)
        chk("overlapping_interval_excluded",
            set(cols[0].tolist()) == {0, 1} and set(cols[2].tolist()) == {2, 3}
            and set(cols[4].tolist()) == {4})

        # ---- transforms / invariance -------------------------------------- #
        rr = {"base_ids": ["a"], "derived_ids": [], "channels": {
            "a": {"id": "a", "derive": None, "transform": "log_ratio_to_reference",
                  "reference": {"of": "a", "rule": "first_valid"}}}}
        x = np.array([2.0, 3.0, 5.0, np.nan])
        v = np.isfinite(x)
        out, msk = apply_transforms({"a": x}, {"a": v}, rr)
        x2 = x * 4.0
        out2, _ = apply_transforms({"a": x2}, {"a": np.isfinite(x2)}, rr)
        chk("log_ratio_is_scale_free",
            bool(np.allclose(out["a"][msk["a"]], out2["a"][msk["a"]], atol=1e-12)))
        rr2 = {"base_ids": ["a"], "derived_ids": [], "channels": {
            "a": {"id": "a", "derive": None, "transform": "log_level", "reference": None}}}
        la, _ = apply_transforms({"a": x}, {"a": v}, rr2)
        lb, _ = apply_transforms({"a": x2}, {"a": np.isfinite(x2)}, rr2)
        chk("log_level_is_not_scale_free",
            not np.allclose(la["a"][msk["a"]], lb["a"][msk["a"]], atol=1e-12))
        rr3 = {"base_ids": ["b"], "derived_ids": ["p", "g"], "channels": {
            "b": {"id": "b", "derive": None, "transform": "identity"},
            "p": {"id": "p", "derive": "presence", "of": "b", "transform": "identity"},
            "g": {"id": "g", "derive": "gap_minutes", "of": "b", "transform": "identity"}}}
        xb = np.array([1.0, 2.0, np.nan, np.nan, 5.0])
        vb = np.isfinite(xb)
        o3, m3 = apply_transforms({"b": xb}, {"b": vb}, rr3)
        chk("presence_and_gap_derive",
            list(m3["p"]) == [True, True, True, True, True]
            and list(o3["p"]) == [1.0, 1.0, 0.0, 0.0, 1.0]
            and list(np.nan_to_num(o3["g"], nan=-1))
            == [0.0, 0.0, 1.0, 2.0, 0.0])

        # ---- split / embedding refusals ----------------------------------- #
        emb = tmp / "emb.npz"
        ev = np.zeros((3, 1, 2), dtype=np.float32)
        ev[0, 0, 0], ev[1, 0, 0], ev[2, 0, 0] = 1.0, 2.0, 3.0
        deterministic_npz(emb, {"values": ev, "valid": np.ones((3, 1, 2), dtype=bool),
                                "object_ids": np.asarray(["a", "b", "c"], dtype="U8")})
        write_json(Path(str(emb) + ".meta.json"),
                   {"provenance": {"fit_days": ["2021-02-08"], "eval_days": ["2025-02-03"]}})
        got = load_embeddings(emb, ["c", "b", "a"], fit_days={"2021-02-08"},
                              eval_days={"2025-02-03"})
        chk("embedding_reordered_to_object_order",
            [float(x) for x in got["values"][:, 0, 0]] == [3.0, 2.0, 1.0]
            and got["valid"].shape == (3, 1, 2) and not got["missing"].any())
        sub = load_embeddings(emb, ["c", "b", "a", "d"], fit_days={"2021-02-08"},
                              eval_days={"2025-02-03"})
        chk("embedding_missing_episode_is_explicit_absence",
            bool(sub["missing"][3]) and not sub["valid"][3].any()
            and float(sub["values"][3].sum()) == 0.0
            and int(sub["provenance"]["n_objects_missing"]) == 1)
        deterministic_npz(emb, {"values": np.zeros((3, 1, 2), dtype=np.float32),
                                "valid": np.ones((3, 1, 2), dtype=bool),
                                "object_ids": np.asarray(["x", "y", "z"], dtype="U8")})
        try:
            load_embeddings(emb, ["a", "b", "c"], fit_days={"2021-02-08"},
                            eval_days={"2025-02-03"})
            chk("embedding_unknown_ids_refused", False)
        except ContractRefusalError:
            chk("embedding_unknown_ids_refused", True)
        deterministic_npz(emb, {"values": np.zeros((3, 1, 2), dtype=np.float32),
                                "valid": np.ones((3, 1, 2), dtype=bool),
                                "object_ids": np.asarray(["a", "b", "c"], dtype="U8")})
        write_json(Path(str(emb) + ".meta.json"),
                   {"provenance": {"fit_days": ["2025-02-03"], "eval_days": ["2025-02-03"]}})
        try:
            load_embeddings(emb, ["a", "b", "c"], fit_days={"2021-02-08"},
                            eval_days={"2025-02-03"})
            chk("embedding_split_leak_refused", False)
        except ContractRefusalError:
            chk("embedding_split_leak_refused", True)

        # ---- barrier map semantics ---------------------------------------- #
        _act, ax = physical_activity_expr({"bar_state", "print_state", "n_prints"})
        probe = pl.DataFrame({
            "bar_state": ["raw", "none", "provider", "none", "none"],
            "print_state": ["no_print", "no_print", "no_print", "path_print",
                            "excluded_prints_only"],
            "n_prints": [0, 0, 0, 3, 1],
            "bar_volume": [0.0, 0.0, 0.0, 0.0, 0.0],
        }).select(_act.alias("act"))
        got_act = probe["act"].to_list()
        chk("span_allowed_requires_physical_activity",
            got_act == [True, False, True, True, True] and ax == ["bar_state", "print_state"],
            {"activity": got_act, "axes": ax})
        chk("known_zero_does_not_bridge_silence",
            got_act[1] is False and got_act[2] is True,
            {"detail": "n_prints==0 with bar_state=none and print_state=no_print is silent even "
                       "though a count column is present and zero; a bar with zero volume is "
                       "still a physical update"})
        _none, ax_none = physical_activity_expr({"n_prints", "bar_volume"})
        chk("missing_state_axes_refused",
            _none is None and ax_none == [],
            {"detail": "no bar_state/print_state means silence cannot be told from a known zero; "
                       "the extractor refuses instead of guessing"})
        chk("causal_clock_is_completion_clock",
            causal_query_t(600, 3) == 603 and causal_query_t(600, 1) == 601
            and causal_query_t(600, 0) == 600,
            {"detail": "last included bucket is window_lo+prefix_len-1 = 602 for prefix 3, so the "
                       "decision instant is its completion clock 603; prefix 0 means no bucket "
                       "available and the causal view has no usable position"})
        chk("prefix_len_is_asof_cutoff_only",
            asof_prefix_len(600, 10, None) == 10 and asof_prefix_len(600, 10, 604) == 5
            and asof_prefix_len(600, 10, 599) == 0 and asof_prefix_len(600, 10, 999) == 10,
            {"detail": "prefix_len is a function of the window and the cutoff ONLY - it has no "
                       "validity/silence input, so a halt or provider gap can never truncate the "
                       "usable causal window (whole declared window up to the query instant stays "
                       "usable, with per-minute validity in `valid`)"})
        resume = pl.DataFrame({
            "bar_state": ["raw", "none", "raw", "none", "provider"],
            "print_state": ["no_print", "no_print", "path_print", "no_print", "no_print"],
        }).select(_act.alias("act"))["act"].to_list()
        chk("span_allowed_resumes_after_silence",
            resume == [True, False, True, False, True],
            {"detail": "the physical-activity barrier is evaluated per minute and becomes True "
                       "again after a silent run (positions 2 and 4 after silence at 1 and 3), so "
                       "it is a reconstruction-mask sampling barrier and never a prefix cutoff "
                       "that would discard the post-gap tail", "activity": resume})
        empty_keep = causal_keep_mask(4, np.array([0]))
        empty_valid = np.ones((4,), dtype=bool)[None, :] & empty_keep
        chk("empty_asof_scope_keeps_nothing",
            not empty_keep.any() and int(empty_valid.sum()) == 0
            and list(causal_keep_mask(4, np.array([2]))[0]) == [True, True, False, False]
            and causal_query_t(600, 0) == 600,
            {"detail": "prefix_len == 0 (nothing available at the decision clock) keeps NO "
                       "position even when the raw bucket is observed and positive: valid is "
                       "all-False, span_allowed is all-False, the tensor keeps its fixed T shape "
                       "as padding, and query_t == start_t is the correct empty completion clock "
                       "- no forced one-bucket 'availability' fallback"})
        valid3 = np.array([[[True, True, False, False], [False, True, False, False]],
                           [[True, False, False, False], [False, False, False, False]]])
        obs = valid3.any(axis=1)
        plen = np.array([2, 1])
        keep = causal_keep_mask(4, plen)
        causal_span = obs & keep
        chk("span_allowed_marks_barriers_and_is_a_coordinate",
            list(causal_span[0]) == [True, True, False, False]
            and list(causal_span[1]) == [True, False, False, False]
            and list(obs[1]) == [True, False, False, False])
        vals3 = np.where(valid3, np.asarray(valid3, dtype=np.float32) * 5.0, 0.0)
        chk("padding_is_a_computational_zero",
            bool((vals3[~valid3] == 0.0).all()) and float(vals3[valid3].min()) == 5.0)

        # ---- tcn archive layout ------------------------------------------- #
        arch = tmp / "ssl_test.npz"
        arrs = {"values": np.zeros((2, 3, 4), dtype=np.float32),
                "valid": np.zeros((2, 3, 4), dtype=bool),
                "span_allowed": np.zeros((2, 4), dtype=bool),
                "channels": np.asarray(["a", "b", "c"], dtype="U64"),
                "episode_id": np.asarray(["p@4", "q@4"], dtype="U128"),
                "day": np.asarray(["2021-02-08", "2025-02-03"], dtype="U16"),
                "ticker": np.asarray(["AAA", "BBB"], dtype="U32"),
                "path_id": np.asarray(["d|AAA", "d|BBB"], dtype="U64"),
                "block": np.asarray(["block1", "block2"], dtype="U16"),
                "start_t": np.asarray([600, 600], dtype=np.int32),
                "end_t": np.asarray([603, 603], dtype=np.int32),
                "query_t": np.asarray([603, 603], dtype=np.int32),
                "mode": np.asarray(["retrospective", "retrospective"], dtype="U16"),
                "prefix_len": np.asarray([2, 0], dtype=np.int32),
                "fit": np.asarray([True, False]),
                "manifest_sha256": np.asarray("0" * 64, dtype="U64"),
                "freeze_r_sha256": np.asarray("1" * 64, dtype="U64"),
                "schema_sha256": np.asarray("2" * 64, dtype="U64")}
        deterministic_npz(arch, arrs)
        again = tmp / "ssl_test2.npz"
        deterministic_npz(again, arrs)
        chk("npz_is_byte_deterministic", sha256_file(arch) == sha256_file(again))
        with np.load(arch, allow_pickle=False) as z:
            chk("npz_layout_roundtrips",
                set(z.files) == set(arrs)
                and z["values"].shape == (2, 3, 4) and z["valid"].dtype == np.bool_
                and z["span_allowed"].shape == (2, 4) and z["mode"].dtype.kind == "U")

    bad = [c for c in checks if not c["ok"]]
    print(json.dumps({"checks": len(checks), "failed": len(bad),
                      "failures": bad}, indent=1)[:4000])
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
