#!/usr/bin/env python
"""Freeze-R constrained learned sequence family: small masked-sequence temporal convolutional
encoder (masked TCN), causal and retrospective variants of ONE architecture
(PLAN-TAPE-ATLAS.md sections 2, 8, 8.1, 10; representation_matrix_template.json
``learned_family``).

Scope
-----
Stage-0C/1 consumer of (a) a genuinely FROZEN Freeze-R record and (b) the masked-TCN input archive
emitted by ``factory/scripts/basket_tape_atlas_geometry.py`` under ``<out>/ssl/<scale>_<mode>.npz``.
It

  * READS every numeric/scale/parameter that shapes the family from the frozen record
    (``learned_family.modes.<mode>.config`` + ``binding_slots.integer_seed`` +
    ``binding_slots.transform_training_blocks``); no implicit null/default is ever substituted, and
    an unknown key refuses the run;
  * trains ONE configuration and ONE run per fold (exactly ``epochs`` epochs, no architecture or
    hyperparameter retry, no silent second seed);
  * fits normalization and every model parameter on the frozen fit blocks only, using valid
    in-window cells; eval days must be disjoint from fit days and an archive whose blocks cross the
    split is refused;
  * reconstructs masked spans only: invalid/padded positions contribute exactly zero loss, the
    target is the observed value at masked positions and NEVER the ``span_allowed`` coordinate, and
    no mask span crosses a silence/halt/provider-gap/unknown-coverage boundary
    (``span_allowed`` from the extractor is the barrier map);
  * emits the causal variant with left-only receptive fields (garbage-suffix invariant) and the
    retrospective variant labelled and never live (out-of-window junk insensitive);
  * compares the trained model against the SAME architecture with frozen random weights on the SAME
    held-out masks and publishes the actual reconstruction losses;
  * writes portable NumPy embeddings (``values (N,E,T)`` / ``valid (N,E,T)`` / ``object_ids`` +
    a ``.meta.json`` provenance sidecar) directly consumable by
    ``basket_tape_atlas_geometry.py:load_embeddings``, plus the weights sha256, the input channel
    tuple, the parameter count and the code/config/archive shas;
  * checkpoints every epoch and resumes exactly; a rerun of a claimed fold with a different
    configuration, seed or archive set is REFUSED rather than silently retrained.

No outcome, censor, future, ticket-constant or anatomy column is read; no clock/block/family/
entry-rank input is ever fed to the model (episode metadata is audit-only). ``span_allowed`` is a
coordinate: it constrains masking and sanitization, and is never a model input, never a distance
channel and never a reconstruction target.

CPU dependency
--------------
This is the only module in the tape-atlas path that needs PyTorch, and it must be the CPU build::

    .venv/bin/python -m pip install "torch>=2.0" --index-url https://download.pytorch.org/whl/cpu

Installed in the canonical venv (verified by StorageDependencyOps,
``local://storage-runtime-status.md``):
``torch==2.11.0+cpu`` (CPU-only, ``torch.version.cuda is None``, ``torch.cuda.is_available()``
False) with ``numpy 2.4.6`` and python 3.11.15; scipy / torchvision / torchaudio / triton are
absent. NumPy and PyTorch are the only imports; no SciPy, no Polars, no ANN index, no clustering.

Input archive (producer contract, one file per frozen scale x mode)
-------------------------------------------------------------------
``<archive-dir>/<scale>_<mode>.npz`` exactly as emitted by
``basket_tape_atlas_geometry.py``::

    values       float32 (N,C,T)  normalized-ready channel values; 0.0 at valid==False
    valid        bool    (N,C,T)  per-channel observation mask (False is a COMPUTATIONAL pad).
                                  A KNOWN ZERO is a legitimate value where valid==True and does
                                  NOT by itself mark the minute observed
    span_allowed bool    (N,T)    the contiguous RECONSTRUCTION-MASK barrier only: True where the
                                  observation layer recorded physical tape activity, False at a
                                  silent minute, provider gap, unknown coverage, padding or
                                  out-of-window position. It may turn True again after a silent
                                  run and must NEVER be used as a scope/embedding/usable-region
                                  cutoff; never a model input, never a distance channel, never a
                                  reconstruction target
    channels     <U      (C,)     == the frozen learned_family.inputs ids, in order
    episode_id/day/ticker/path_id/block/start_t/end_t/query_t/mode/prefix_len/fit  (N,) metadata
    manifest_sha256, freeze_r_sha256, schema_sha256  scalar <U64 provenance hashes

Metadata arrays are audit-only and are never assembled into a model input. Two independent
concerns, never conflated: the SCOPE/USABLE region is ``prefix_len``, the ASOF clock cutoff /
padding length (a function of the declared window and the asof instant ONLY - no validity, silence
or gap input), and the MASKABLE region is ``span_allowed``, which only bars a masked span from
bridging a silent run.

The scope cut is applied INDEPENDENTLY here, not trusted to the producer's truncation. Verified
relation (geometry ``asof_prefix_len`` / ``causal_query_t`` / the SSL writer): ``start_t`` is the
window's first bucket label; ``end_t`` is the FULL declared window end (``min(anchor + scale - 1,
965)``) and is NOT scope-bound for the causal variant; ``prefix_len`` is the ASOF cutoff length and
``query_t`` is the completion clock of the last included bucket (causal ``start_t + prefix_len``,
retrospective ``end_t``). So for causal this consumer computes
``seen = valid AND t < prefix_len`` (in addition to the window), which keeps known zeros and every
observation that resumes after a gap up to the cutoff while excluding everything after it; for
retrospective ``prefix_len`` equals the window width exactly and ``query_t == end_t``. Those two
relations are asserted in the loader (an archive that contradicts its own clock relations is an
integrity refusal) and the relation actually used is published per archive under
``metrics.archives[].scope``. Values that are valid beyond the cutoff therefore cannot influence
fit normalization, the forward embedding or the loss. The producer's causal prefix-rule label
(currently ``declared_window_truncated_at_asof_cutoff``) and the ``span_allowed`` axes are recorded
from the companion ``<scale>_<mode>.meta.json`` sidecar without requiring a specific literal; a
missing or contradicting sidecar is an integrity refusal. No fixture / test-only marker is read or
produced anywhere. The published embedding archive is ``values (N,E,T)`` / ``valid (N,E,T)`` /
``object_ids`` (== the ``episode_id`` values, every scale of one mode, padded to T_max with
``valid=False``) plus the ``.npz.meta.json`` provenance sidecar that ``load_embeddings`` requires.

Masking policy (published, never tuned). Masked spans are drawn per episode from that episode's
``span_allowed`` runs, of length in the frozen ``mask_span`` ``[min_len, max_len]``, targeting the
frozen ``mask_fraction`` of the episode's maskable cells. Because a span can never be shorter than
``min_len`` and the final span may overshoot the remaining budget by up to ``max_len - 1`` cells,
the REALIZED rate can exceed the declared fraction - bound ``masked <= sum_i target_i +
(max_len - 1) * K`` with K = episodes receiving at least one span - and it falls short only where
an episode has no contiguous room for a legal span. The plan and the run metrics both publish the
declared fraction, the realized rate, the mask span, and that bound (``mask_plan``). The metric
``known_zero_cells`` counts observed cells whose value is exactly 0.0: it is live but can
legitimately read 0 for a channel set whose validity rules require activity, and it never implies
that missing coverage was treated as zero (unknown coverage is always a computational pad).

Frozen-R input contract (exact required fields)
-----------------------------------------------
The record must be non-template, frozen and NOT a draft (``template_only`` false, ``frozen`` true,
``status`` free of DRAFT/TEMPLATE) and bind::

    {
      "matrix_version": "<id>",
      "binding_slots": {
        "corpus": {"node_id": str, "version": str, "manifest_sha256": hex64, "day_count": int,
                   "built": true, "scope": "full"|"canary", "day_set_sha256": hex64},
        "blind_report": {"node_id": str, "path": str, "sha256": hex64, "complete": true,
                         "permitted_summaries_only": true},
        "transform_training_blocks": {"split_key": "block", "fit_blocks": ["block1"|"block2"],
            "eval_blocks": [...], "no_overlapping_source_interval_crosses_split": true,
            "normalization_and_model_params_fit_on_fit_blocks_only": true, "frozen": true},
        "scale_choice": {"scale_ladder": [int], ...},
        "integer_seed": int,
        "numeric_one_shot_gates": {"recurrence": num, "mutual_neighbour_support": num,
            "block_stability": num, "coordinate_leakage": num, "family_leakage": num,
            "magnitude_preservation": num, "sparse_exact_recall": num,
            "ssl_over_random_improvement": num, "measured_runtime": num}
      },
      "learned_family": {
        "family_count": 1,
        "architecture": "small masked-sequence temporal convolutional encoder (masked TCN)",
        "invalid_padded_positions_contribute_zero_loss": true,
        "inputs": [<channel spec>, ...],          # emits the archive's `channels` tuple
        "modes": {
          "causal":        {"receptive_field": "left-only",
                            "garbage_suffix_invariance_required": true,
                            "prospective_live_use": true,
                            "config": { ...see learned_family.modes.<mode>.config below... }},
          "retrospective": {"labelled": true, "prospective_live_use": false,
                            "out_of_window_junk_insensitive_required": true,
                            "config": { ...see learned_family.modes.<mode>.config below... }}
        }
      }
    }

Scope (no fixture bypass): ``binding_slots.corpus.scope`` must be ``"full"`` - this consumer trains
only on the genuinely frozen full-corpus record (genuine full manifest + blind report + all nine
one-shot gates). A canary-scoped record is refused outright; a canary is never trained here and is
never tagged into a fixture class. All nine numeric one-shot gates must be present and non-null;
``ssl_over_random_improvement`` must be a finite number and is compared once against the measured
improvement (never re-tuned, never retried).

``learned_family.modes.<mode>.config`` (every key REQUIRED, explicit, non-null; unknown key
refuses)::

    {
      "config_version": str,            # version of this training configuration
      "family": "masked_tcn",
      "mode": "causal"|"retrospective",
      "labelled": bool,                 # false for causal, true for retrospective
      "label": null (causal) | non-empty str (retrospective),
      "objective": "masked_span_reconstruction",
      "width": int >= 1,                # TCN layer width
      "depth": int >= 1,                # number of dilated blocks
      "kernel_size": int >= 2,
      "embedding_dim": int >= 1,        # emitted embedding channels C
      "dilation_growth": int >= 1,
      "activation": "gelu"|"relu"|"silu",
      "norm": "none"|"layer_norm",
      "dropout": float in [0,1),
      "optimizer": {"name": "adam"|"adamw", "lr": float>0, "weight_decay": float>=0,
                    "betas": [float,float], "eps": float>0}
                 | {"name": "sgd", "lr": float>0, "weight_decay": float>=0,
                    "momentum": float in [0,1)},
      "epochs": int >= 1,
      "batch_size": int >= 1,
      "mask_span": {"min_len": int >= 1, "max_len": int >= min_len},
      "mask_fraction": float in (0,1],
      "seed": int,                      # must equal binding_slots.integer_seed
      "fold": str,                      # must equal --fold and split_key
      "input_planes": ["value", ..."valid"...],   # explicit model inputs; "value" required
      "hparams": {"weight_init": "kaiming_uniform"|"xavier_uniform"|"orthogonal",
                  "residual": bool, "grad_clip_norm": float > 0,
                  "eval_every": int >= 1}       # held-out loss cadence (full eval set, never a
                                                # subsample and never a model-selection criterion)
    }

CLI
---
  .venv/bin/python factory/scripts/basket_tape_atlas_sequence.py --selftest
  .venv/bin/python factory/scripts/basket_tape_atlas_sequence.py \\
      --freeze-r <frozen R record.json> --archive-dir <geometry out>/ssl \\
      --mode causal --fold block --tier selected_paths \\
      --out /home/hillel/projects/Alpacatrader/data/atlas/sequence/v0 \\
      --out-evidence factory/artifacts/basket/phase2/ATLAS/TAPE/SEQUENCE/v0
  .venv/bin/python factory/scripts/basket_tape_atlas_sequence.py ... --plan-only
  .venv/bin/python factory/scripts/basket_tape_atlas_sequence.py ... --resume

Exit codes: 0 complete, 2 contract refusal (nothing written), 3 integrity refusal (REFUSED report
written), 1 selftest failure.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import sys
import time
import zipfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
TAPE = ROOT / "factory/artifacts/basket/phase2/ATLAS/TAPE"
OBS_EVIDENCE = TAPE / "OBSERVATION/v0"
SCHEMA_PATH = OBS_EVIDENCE / "schema.json"
CONTRACT_PATH = OBS_EVIDENCE / "contract.json"
DEFAULT_OUT = Path("/home/hillel/projects/Alpacatrader/data/atlas/sequence/v0")
DEFAULT_OUT_EVIDENCE = TAPE / "SEQUENCE/v0"
MANIFEST_NAME = "sequence_manifest.json"
REPORT_NAME = "sequence_report.json"
PLAN_NAME = "sequence_plan.json"

HEX64 = re.compile(r"[0-9a-f]{64}")
MODES = ("causal", "retrospective")
BLOCKS = ("block1", "block2")
FAMILY = "masked_tcn"
ARCHITECTURE = "small masked-sequence temporal convolutional encoder (masked TCN)"
OBJECTIVE = "masked_span_reconstruction"
PLANES = ("value", "valid")
ACTIVATIONS = ("gelu", "relu", "silu")
NORMS = ("none", "layer_norm")
WEIGHT_INITS = ("kaiming_uniform", "xavier_uniform", "orthogonal")
CAUSAL_PREFIX_RULE = "declared_window_truncated_at_asof_cutoff"
TRAIN_TIER_KEY = "learned_minute_sequence"
TORCH_MIN = (2, 0)
# binding_slots.numeric_one_shot_gates: committed once, before any training run
GATE_KEYS = (
    "recurrence", "mutual_neighbour_support", "block_stability", "coordinate_leakage",
    "family_leakage", "magnitude_preservation", "sparse_exact_recall",
    "ssl_over_random_improvement", "measured_runtime",
)

CONFIG_KEYS = (
    "config_version", "family", "mode", "labelled", "label", "objective", "width", "depth",
    "kernel_size", "embedding_dim", "dilation_growth", "activation", "norm", "dropout",
    "optimizer", "epochs", "batch_size", "mask_span", "mask_fraction", "seed", "fold",
    "input_planes", "hparams",
)
MASK_SPAN_KEYS = ("min_len", "max_len")
HPARAM_KEYS = ("weight_init", "residual", "grad_clip_norm", "eval_every")
ARCHIVE_KEYS = (
    "values", "valid", "channels", "span_allowed", "episode_id", "day", "ticker", "path_id",
    "block", "start_t", "end_t", "query_t", "mode", "prefix_len", "fit",
)
ARCHIVE_SCALARS = ("manifest_sha256", "freeze_r_sha256", "schema_sha256")
# episode metadata arrays are audit-only; they must never reach the model or a distance
METADATA_KEYS = ("episode_id", "day", "ticker", "path_id", "block", "start_t", "end_t", "query_t",
                 "mode", "prefix_len", "fit", "scale")


class ContractRefusalError(RuntimeError):
    """The frozen record, the configuration or the tier is illegal: refuse before reading rows."""


class IntegrityRefusalError(RuntimeError):
    """The archive disagrees with its own declared provenance: write REFUSED and stop."""

    def __init__(self, message: str, problems: list | None = None):
        super().__init__(message)
        self.problems = problems or [{"kind": "integrity_refusal", "message": message}]


# --------------------------------------------------------------------------- #
# small helpers (self-contained; the geometry producer owns its own copies)
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


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(canon_bytes(obj) + b"\n")
    tmp.replace(path)


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


def block_of(day: str) -> str:
    return "block1" if str(day) <= "2023-12-31" else "block2"


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


def _req_false(obj, key, where: str) -> bool:
    val = _req(obj, key, bool, where)
    if val is not False:
        raise ContractRefusalError(f"{where}.{key} must be false (found {val!r})")
    return val


def _req_int(obj, key, where: str, *, lo=None, hi=None) -> int:
    val = _req(obj, key, int, where)
    if isinstance(val, bool):
        raise ContractRefusalError(f"{where}.{key} must be an int, not a bool")
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


def _strict_keys(obj: dict, allowed, where: str) -> None:
    extra = sorted(set(obj) - set(allowed))
    if extra:
        raise ContractRefusalError(
            f"{where} declares unknown key(s) {extra}; every parameter is explicit and no "
            f"implicit choice is accepted (allowed: {list(allowed)})"
        )


# --------------------------------------------------------------------------- #
# frozen-record validation
# --------------------------------------------------------------------------- #
def load_frozen_family(path: Path, *, mode: str, fold: str) -> dict:
    """Validate the frozen Freeze-R record and return the bound learned-family configuration."""
    if not Path(path).is_file():
        raise ContractRefusalError(f"frozen Freeze-R record not found: {path}")
    raw = load_json(path)
    if not isinstance(raw, dict):
        raise ContractRefusalError("Freeze R must be a JSON object")
    if raw.get("template_only") is not False:
        raise ContractRefusalError("Freeze R is still a template (template_only is not false)")
    if raw.get("frozen") is not True:
        raise ContractRefusalError("Freeze R is not frozen (frozen is not true)")
    status = str(raw.get("status") or "")
    if not status or "DRAFT" in status.upper() or "TEMPLATE" in status.upper():
        raise ContractRefusalError(f"Freeze R status {status!r} is a draft/template")
    matrix_version = _req(raw, "matrix_version", str, "freeze_r")
    if "template" in matrix_version:
        raise ContractRefusalError(f"matrix_version {matrix_version!r} is a template id")
    if str(raw.get("freeze") or "R") != "R":
        raise ContractRefusalError("this consumer is bound to Freeze R only")

    bs = _req(raw, "binding_slots", dict, "freeze_r")
    corpus = _req(bs, "corpus", dict, "binding_slots")
    if _req_true(corpus, "built", "binding_slots.corpus") is not True:
        raise ContractRefusalError("binding_slots.corpus.built must be true")
    scope = _req(corpus, "scope", str, "binding_slots.corpus")
    # genuinely frozen Freeze R only: the canonical full corpus. A canary-scoped record is not a
    # frozen R and there is no fixture bypass - a canary is never trained here.
    if scope != "full":
        raise ContractRefusalError(
            f"binding_slots.corpus.scope must be 'full' (found {scope!r}); this consumer trains "
            "only on the genuinely frozen full-corpus record"
        )
    corpus_out = {
        "node_id": _req(corpus, "node_id", str, "binding_slots.corpus"),
        "version": _req(corpus, "version", str, "binding_slots.corpus"),
        "manifest_sha256": _req_hex(corpus, "manifest_sha256", "binding_slots.corpus"),
        "day_count": _req_int(corpus, "day_count", "binding_slots.corpus", lo=1),
        "scope": scope,
    }

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
        if b not in BLOCKS:
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
    split_key = "block"
    if fold != split_key:
        raise ContractRefusalError(
            f"--fold {fold!r} must equal the frozen split key {split_key!r}"
        )

    scale_binding = _req(bs, "scale_choice", dict, "binding_slots")
    ladder = list(_req(scale_binding, "scale_ladder", list, "binding_slots.scale_choice"))
    if not ladder:
        raise ContractRefusalError("binding_slots.scale_choice.scale_ladder is empty")
    ladder = [int(s) for s in ladder]
    if any(s < 1 for s in ladder):
        raise ContractRefusalError("scale ladder entries must be >= 1")

    seed = _req_int(bs, "integer_seed", "binding_slots", lo=0)
    gates = _req(bs, "numeric_one_shot_gates", dict, "binding_slots")
    missing_gates = [g for g in GATE_KEYS if gates.get(g) is None]
    if missing_gates:
        raise ContractRefusalError(
            f"binding_slots.numeric_one_shot_gates must carry all nine gate keys non-null "
            f"(missing/UNBOUND: {missing_gates}); the gates are committed once, before training"
        )
    ssl_gate = gates["ssl_over_random_improvement"]
    if isinstance(ssl_gate, bool) or not isinstance(ssl_gate, (int, float)):
        raise ContractRefusalError(
            "binding_slots.numeric_one_shot_gates.ssl_over_random_improvement must be a number"
        )
    if not np.isfinite(float(ssl_gate)):
        raise ContractRefusalError(
            "binding_slots.numeric_one_shot_gates.ssl_over_random_improvement must be finite"
        )

    lf = _req(raw, "learned_family", dict, "freeze_r")
    if _req_int(lf, "family_count", "learned_family", lo=1) != 1:
        raise ContractRefusalError("learned_family.family_count must be exactly 1 - this consumer "
                                   "implements ONE family and adds no new family")
    if _req(lf, "architecture", str, "learned_family") != ARCHITECTURE:
        raise ContractRefusalError(
            f"learned_family.architecture must be {ARCHITECTURE!r} (no architecture hunt)"
        )
    _req_true(lf, "invalid_padded_positions_contribute_zero_loss", "learned_family")
    inputs = _req(lf, "inputs", list, "learned_family")
    if not inputs:
        raise ContractRefusalError("learned_family.inputs must be a non-empty list")
    input_ids = []
    for i, spec in enumerate(inputs):
        if not isinstance(spec, dict) or not spec.get("id"):
            raise ContractRefusalError(f"learned_family.inputs[{i}] must be an object with an id")
        input_ids.append(str(spec["id"]))
    if len(set(input_ids)) != len(input_ids):
        raise ContractRefusalError("learned_family.inputs ids must be unique")
    modes = _req(lf, "modes", dict, "learned_family")
    if mode not in MODES:
        raise ContractRefusalError(f"--mode must be one of {MODES}")
    mrec = _req(modes, mode, dict, "learned_family.modes")
    if mode == "causal":
        if _req(mrec, "receptive_field", str, "learned_family.modes.causal") != "left-only":
            raise ContractRefusalError(
                "the causal variant must declare receptive_field 'left-only'"
            )
        _req_true(mrec, "garbage_suffix_invariance_required", "learned_family.modes.causal")
        _req_true(mrec, "prospective_live_use", "learned_family.modes.causal")
    else:
        _req_true(mrec, "labelled", "learned_family.modes.retrospective")
        _req_false(mrec, "prospective_live_use", "learned_family.modes.retrospective")
        _req_true(mrec, "out_of_window_junk_insensitive_required",
                  "learned_family.modes.retrospective")
    if mrec.get("config") is None:
        raise ContractRefusalError(
            f"learned_family.modes.{mode}.config is UNBOUND (null): the frozen record must bind "
            "every layer/optimizer/epoch/mask/seed/fold/hyperparameter explicitly"
        )

    cfg = validate_config(mrec["config"], mode=mode, fold=fold, seed=seed,
                          where=f"learned_family.modes.{mode}.config")
    return {
        "path": str(path),
        "sha256": sha256_file(Path(path)),
        "matrix_version": matrix_version,
        "corpus": corpus_out,
        "blind_report": blind,
        "split_key": split_key,
        "fit_blocks": fit_blocks,
        "eval_blocks": eval_blocks,
        "ladder": sorted(set(ladder)),
        "seed": seed,
        "gates": gates,
        "input_ids": input_ids,
        "config": cfg,
        "mode": mode,
        "fold": fold,
    }


def validate_config(cfg, *, mode: str, fold: str, seed: int, where: str) -> dict:
    """Strictly validate the bound training configuration: no implicit null, no unknown key."""
    if not isinstance(cfg, dict):
        raise ContractRefusalError(f"{where} must be an object")
    _strict_keys(cfg, CONFIG_KEYS, where)
    missing = [k for k in CONFIG_KEYS if k not in cfg]
    if missing:
        raise ContractRefusalError(f"{where} is missing required key(s) {missing}")
    for k in CONFIG_KEYS:
        # `label` is the one explicit null the contract allows (causal is unlabelled); every other
        # key must be a bound value and no default is ever substituted.
        if k != "label" and cfg[k] is None:
            raise ContractRefusalError(f"{where}.{k} is UNBOUND (null) - no default is accepted")

    out = {}
    out["config_version"] = _req(cfg, "config_version", str, where)
    if not out["config_version"]:
        raise ContractRefusalError(f"{where}.config_version must be non-empty")
    if _req(cfg, "family", str, where) != FAMILY:
        raise ContractRefusalError(f"{where}.family must be {FAMILY!r} - no new model family")
    if _req(cfg, "mode", str, where) != mode:
        raise ContractRefusalError(f"{where}.mode must match --mode {mode!r}")
    labelled = _req(cfg, "labelled", bool, where)
    if mode == "causal":
        if labelled:
            raise ContractRefusalError(f"{where}.labelled must be false for the causal variant")
        if cfg.get("label") is not None:
            raise ContractRefusalError(f"{where}.label must be null for the causal variant")
        out["label"] = None
    else:
        if not labelled:
            raise ContractRefusalError(
                f"{where}.labelled must be true: the retrospective variant is labelled"
            )
        label = cfg.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ContractRefusalError(
                f"{where}.label must be a non-empty string for the labelled retrospective variant"
            )
        out["label"] = label
    out["labelled"] = labelled
    if _req(cfg, "objective", str, where) != OBJECTIVE:
        raise ContractRefusalError(
            f"{where}.objective must be {OBJECTIVE!r}: no future-return prediction, no "
            "contrastive outcome proxy, no cluster loss and no policy loss"
        )
    out["width"] = _req_int(cfg, "width", where, lo=1)
    out["depth"] = _req_int(cfg, "depth", where, lo=1)
    out["kernel_size"] = _req_int(cfg, "kernel_size", where, lo=2)
    out["embedding_dim"] = _req_int(cfg, "embedding_dim", where, lo=1)
    out["dilation_growth"] = _req_int(cfg, "dilation_growth", where, lo=1)
    act = _req(cfg, "activation", str, where)
    if act not in ACTIVATIONS:
        raise ContractRefusalError(f"{where}.activation must be one of {ACTIVATIONS}")
    out["activation"] = act
    norm = _req(cfg, "norm", str, where)
    if norm not in NORMS:
        raise ContractRefusalError(f"{where}.norm must be one of {NORMS}")
    out["norm"] = norm
    out["dropout"] = _req_num(cfg, "dropout", where, lo=0.0, hi=1.0)
    if out["dropout"] >= 1.0:
        raise ContractRefusalError(f"{where}.dropout must be < 1")

    opt = _req(cfg, "optimizer", dict, where + ".optimizer")
    name = _req(opt, "name", str, where + ".optimizer")
    if name not in ("adam", "adamw", "sgd"):
        raise ContractRefusalError(f"{where}.optimizer.name must be adam|adamw|sgd")
    allowed = ({"name", "lr", "weight_decay", "betas", "eps"} if name in ("adam", "adamw")
               else {"name", "lr", "weight_decay", "momentum"})
    _strict_keys(opt, allowed, where + ".optimizer")
    for k in allowed:
        if k not in opt or opt[k] is None:
            raise ContractRefusalError(f"{where}.optimizer.{k} is missing/UNBOUND for {name}")
    out["optimizer"] = {
        "name": name,
        "lr": _req_num(opt, "lr", where + ".optimizer", positive=True),
        "weight_decay": _req_num(opt, "weight_decay", where + ".optimizer", lo=0.0),
    }
    if name in ("adam", "adamw"):
        betas = _req(opt, "betas", list, where + ".optimizer")
        if len(betas) != 2 or not all(isinstance(b, (int, float)) and 0 <= float(b) < 1
                                      for b in betas):
            raise ContractRefusalError(f"{where}.optimizer.betas must be two floats in [0,1)")
        out["optimizer"]["betas"] = [float(betas[0]), float(betas[1])]
        out["optimizer"]["eps"] = _req_num(opt, "eps", where + ".optimizer", positive=True)
    else:
        out["optimizer"]["momentum"] = _req_num(opt, "momentum", where + ".optimizer",
                                                lo=0.0, hi=1.0)

    out["epochs"] = _req_int(cfg, "epochs", where, lo=1)
    out["batch_size"] = _req_int(cfg, "batch_size", where, lo=1)
    ms = _req(cfg, "mask_span", dict, where + ".mask_span")
    _strict_keys(ms, MASK_SPAN_KEYS, where + ".mask_span")
    mn = _req_int(ms, "min_len", where + ".mask_span", lo=1)
    mx = _req_int(ms, "max_len", where + ".mask_span", lo=1)
    if mx < mn:
        raise ContractRefusalError(f"{where}.mask_span.max_len must be >= min_len")
    out["mask_span"] = {"min_len": mn, "max_len": mx}
    out["mask_fraction"] = _req_num(cfg, "mask_fraction", where, lo=0.0, hi=1.0)
    if out["mask_fraction"] <= 0.0:
        raise ContractRefusalError(f"{where}.mask_fraction must be in (0,1]")
    cfg_seed = _req_int(cfg, "seed", where, lo=0)
    if cfg_seed != seed:
        raise ContractRefusalError(
            f"{where}.seed ({cfg_seed}) must equal binding_slots.integer_seed ({seed})"
        )
    out["seed"] = cfg_seed
    if _req(cfg, "fold", str, where) != fold:
        raise ContractRefusalError(f"{where}.fold must equal --fold {fold!r}")
    out["fold"] = fold
    planes = _req(cfg, "input_planes", list, where)
    if not planes or any(p not in PLANES for p in planes):
        raise ContractRefusalError(
            f"{where}.input_planes must be a non-empty subset of {PLANES}; span_allowed is a "
            "COORDINATE and is never a model input"
        )
    if "value" not in planes:
        raise ContractRefusalError(f"{where}.input_planes must include 'value'")
    if len(set(planes)) != len(planes):
        raise ContractRefusalError(f"{where}.input_planes has duplicates")
    out["input_planes"] = list(planes)

    hp = _req(cfg, "hparams", dict, where + ".hparams")
    _strict_keys(hp, HPARAM_KEYS, where + ".hparams")
    for k in HPARAM_KEYS:
        if k not in hp or hp[k] is None:
            raise ContractRefusalError(f"{where}.hparams.{k} is missing/UNBOUND")
    wi = _req(hp, "weight_init", str, where + ".hparams")
    if wi not in WEIGHT_INITS:
        raise ContractRefusalError(f"{where}.hparams.weight_init must be one of {WEIGHT_INITS}")
    out["hparams"] = {
        "weight_init": wi,
        "residual": _req(hp, "residual", bool, where + ".hparams"),
        "grad_clip_norm": _req_num(hp, "grad_clip_norm", where + ".hparams", positive=True),
        "eval_every": _req_int(hp, "eval_every", where + ".hparams", lo=1),
    }
    return out


# --------------------------------------------------------------------------- #
# archive loading
# --------------------------------------------------------------------------- #
def load_archives(archive_dir: Path, *, r: dict, mode: str, manifest: Path | None,
                  schema_path: Path, verify: bool) -> dict:
    """Load one input archive per frozen scale; validate provenance, shapes and barrier map."""
    if not archive_dir.is_dir():
        raise ContractRefusalError(f"--archive-dir not found: {archive_dir}")
    expected = {}
    freeze_r_sha = sha256_file(Path(r["path"]))
    schema_sha = sha256_file(schema_path) if schema_path.is_file() else None
    manifest_sha = sha256_file(manifest) if manifest is not None else None
    if manifest_sha is not None and manifest_sha != r["corpus"]["manifest_sha256"]:
        raise ContractRefusalError(
            "--manifest sha256 does not match binding_slots.corpus.manifest_sha256"
        )
    for scale in r["ladder"]:
        path = archive_dir / f"{scale}_{mode}.npz"
        if not path.is_file():
            raise IntegrityRefusalError(
                f"masked-TCN input archive missing for scale {scale} mode {mode}: {path}",
                [{"kind": "archive_absent", "scale": scale, "mode": mode, "path": str(path)}],
            )
        expected[scale] = path

    problems: list = []
    archives = []
    for scale in r["ladder"]:
        path = expected[scale]
        # the producer's companion `<scale>_<mode>.meta.json` sidecar carries the archive layout,
        # the causal prefix rule and the provenance context: it must exist and must not contradict
        # the archive it describes. No fixture/test-only marker is read anywhere.
        side = path.with_suffix(".meta.json")
        side_info = {}
        if not side.is_file():
            problems.append({"kind": "archive_sidecar_absent", "path": str(path),
                             "expected": str(side)})
        else:
            try:
                loaded_side = load_json(side)
            except Exception as exc:  # a malformed sidecar is not provenance
                problems.append({"kind": "archive_sidecar_unreadable", "path": str(side),
                                 "error": str(exc)})
            else:
                if not isinstance(loaded_side, dict):
                    problems.append({"kind": "archive_sidecar_not_object", "path": str(side)})
                else:
                    side_info = loaded_side
                    if side_info.get("scale") is not None and int(side_info["scale"]) != scale:
                        problems.append({"kind": "archive_sidecar_scale_mismatch",
                                         "path": str(side),
                                         "sidecar": side_info["scale"], "archive": scale})
                    if side_info.get("mode") is not None and str(side_info["mode"]) != mode:
                        problems.append({"kind": "archive_sidecar_mode_mismatch", "path": str(side),
                                         "sidecar": str(side_info["mode"]), "requested": mode})
        if problems:
            break
        with np.load(path, allow_pickle=False) as z:
            keys = set(z.files)
            missing = sorted(set(ARCHIVE_KEYS) - keys)
            if missing:
                raise IntegrityRefusalError(
                    f"{path.name} lacks required array(s) {missing}; the producer must emit the "
                    "documented masked-TCN layout",
                    [{"kind": "archive_missing_keys", "path": str(path), "missing": missing}],
                )
            raw_values = np.asarray(z["values"])
            values = np.array(raw_values, dtype=np.float32, copy=True)
            valid = np.array(z["valid"], dtype=bool, copy=True)
            span_allowed = np.array(z["span_allowed"], dtype=bool, copy=True)
            channels = [str(c) for c in z["channels"]]
            meta = {k: np.asarray(z[k]) for k in METADATA_KEYS if k in keys}
            scalars = {k: (str(np.asarray(z[k]).reshape(-1)[0]) if k in keys else None)
                       for k in ARCHIVE_SCALARS}
        if values.ndim != 3:
            problems.append({"kind": "values_rank", "path": str(path), "shape": list(values.shape)})
        if values.shape != valid.shape or span_allowed.shape != (values.shape[0], values.shape[2]):
            problems.append({"kind": "mask_shape_mismatch", "path": str(path),
                             "values": list(values.shape), "valid": list(valid.shape),
                             "span_allowed": list(span_allowed.shape),
                             "expected_span_allowed": [int(values.shape[0]), int(values.shape[2])]})
        if not problems:
            n, c, t = values.shape
            if c != len(channels):
                problems.append({"kind": "channels_len", "path": str(path),
                                 "channels": len(channels), "C": c})
            if channels != r["input_ids"]:
                problems.append({"kind": "channels_not_frozen_inputs", "path": str(path),
                                 "archive": channels, "frozen": r["input_ids"]})
            if t != scale:
                problems.append({"kind": "archive_time_dim", "path": str(path), "T": t,
                                 "scale": scale})
            for k in ("episode_id", "day", "ticker", "path_id", "block", "start_t", "end_t",
                      "query_t", "mode", "prefix_len", "fit"):
                if k not in meta or meta[k].shape != (n,):
                    problems.append({"kind": "metadata_array", "path": str(path), "array": k,
                                     "shape": list(meta.get(k, np.asarray([])).shape), "N": n})
            if not problems:
                if set(np.unique(meta["mode"]).tolist()) != {mode}:
                    problems.append({"kind": "archive_mode_mismatch", "path": str(path),
                                     "mode": sorted(set(map(str, np.unique(meta["mode"])))),
                                     "requested": mode})
                bad = np.flatnonzero(meta["start_t"] > meta["end_t"])
                if bad.size:
                    problems.append({"kind": "window_order", "path": str(path),
                                     "rows": bad[:5].tolist()})
                width = meta["end_t"] - meta["start_t"] + 1
                # no observed or maskable cell may sit beyond the declared window
                tail = np.zeros((n, t), dtype=bool)
                for i in range(n):
                    tail[i, int(width[i]):] = True
                if bool((span_allowed & tail).any()):
                    problems.append({"kind": "span_allowed_beyond_window", "path": str(path)})
                if bool((valid & tail[:, None, :]).any()):
                    problems.append({"kind": "valid_beyond_window", "path": str(path)})
                if meta["fit"].dtype.kind != "b":
                    problems.append({"kind": "fit_not_bool", "path": str(path)})
                # the producer's own clock relations, asserted rather than trusted
                plen = meta["prefix_len"]
                qt = meta["query_t"]
                if bool((plen < 0).any()) or bool((plen > width).any()):
                    problems.append({"kind": "prefix_len_out_of_window", "path": str(path),
                                     "max_prefix": int(plen.max()) if plen.size else None,
                                     "max_width": int(width.max()) if width.size else None})
                if mode == "causal":
                    # prefix_len is the ASOF cutoff, NOT a silence run: end_t stays the full
                    # declared window end, so the scope cut must be applied independently.
                    # query_t is the COMPLETION clock of the last included bucket.
                    expect_qt = meta["start_t"] + plen
                    if not np.array_equal(qt, expect_qt):
                        bad = np.flatnonzero(qt != expect_qt)[:5].tolist()
                        problems.append({"kind": "causal_query_t_relation", "path": str(path),
                                         "rows": bad})
                else:
                    # retrospective: the whole declared window is in scope, so prefix_len is
                    # exactly the window width and query_t is end_t
                    if not np.array_equal(plen, width):
                        bad = np.flatnonzero(plen != width)[:5].tolist()
                        problems.append({"kind": "retrospective_prefix_len_relation",
                                         "path": str(path), "rows": bad})
                    if not np.array_equal(qt, meta["end_t"]):
                        bad = np.flatnonzero(qt != meta["end_t"])[:5].tolist()
                        problems.append({"kind": "retrospective_query_t_relation",
                                         "path": str(path), "rows": bad})
            if verify:
                if scalars.get("freeze_r_sha256") != freeze_r_sha:
                    problems.append({"kind": "freeze_r_sha_mismatch", "path": str(path),
                                     "archive": scalars.get("freeze_r_sha256"),
                                     "frozen_record": freeze_r_sha})
                if schema_sha is not None and scalars.get("schema_sha256") != schema_sha:
                    problems.append({"kind": "schema_sha_mismatch", "path": str(path),
                                     "archive": scalars.get("schema_sha256"), "schema": schema_sha})
                if manifest_sha is not None and scalars.get("manifest_sha256") != manifest_sha:
                    problems.append({"kind": "manifest_sha_mismatch", "path": str(path),
                                     "archive": scalars.get("manifest_sha256"),
                                     "manifest": manifest_sha})
        if problems:
            break
        archives.append({
            "scale": scale,
            "path": str(path),
            "sha256": sha256_file(path),
            "sidecar": {"path": str(side), "sha256": sha256_file(side),
                        "scale": side_info.get("scale"), "mode": side_info.get("mode"),
                        "causal_prefix_rule": side_info.get("causal_prefix_rule"),
                        "retrospective_clock_rule": side_info.get("retrospective_clock_rule"),
                        "span_allowed_axes": side_info.get("span_allowed_axes"),
                        "mask_vs_scope": side_info.get("mask_vs_scope")},
            "scope": {
                "start_t": int(meta["start_t"].min()), "end_t": int(meta["end_t"].max()),
                "query_t": int(meta["query_t"].min()),
                "prefix_len": int(meta["prefix_len"].min()),
                "prefix_len_is_window_width": bool(np.array_equal(meta["prefix_len"], width)),
                "asof_cut_applied_independently": mode == "causal",
                "relation": ("causal: end_t is the full declared window end (NOT scope-bound), "
                             "prefix_len is the ASOF cutoff, query_t = start_t + prefix_len "
                             "(completion clock)"
                             if mode == "causal" else
                             "retrospective: prefix_len == window width and query_t == end_t, so "
                             "the whole declared window is in scope"),
            },
            "values": values,
            "valid": valid,
            "span_allowed": span_allowed,
            "channels": channels,
            "meta": meta,
            "rows": int(values.shape[0]),
        })
    if problems:
        raise IntegrityRefusalError(
            "the masked-TCN input archives disagree with their declared provenance", problems
        )
    return {"archives": archives, "freeze_r_sha256": freeze_r_sha, "schema_sha256": schema_sha,
            "manifest_sha256": manifest_sha, "bytes": int(sum(
                a["values"].nbytes + a["valid"].nbytes + a["span_allowed"].nbytes
                for a in archives))}


def assign_split(loaded: dict, *, r: dict) -> dict:
    """Flag every episode fit/eval from the frozen blocks; refuse a split-crossing archive.

    The frozen split is by DAY BLOCK: ``block_of(day)`` must lie in ``fit_blocks`` or
    ``eval_blocks`` (a block outside the split is refused rather than silently pooled), the
    archive's own ``fit`` flag must agree with that derivation, and the two day sets must be
    disjoint. Because an episode's window is confined to its own session day (checked in
    ``load_archives``), no source interval can straddle the split once the day sets are disjoint.
    """
    fit_days: set = set()
    eval_days: set = set()
    problems: list = []
    seen_blocks: set = set()
    for arch in loaded["archives"]:
        meta = arch["meta"]
        days = [str(d) for d in meta["day"]]
        blocks = [str(b) for b in meta["block"]]
        for d, b in zip(days, blocks, strict=True):
            seen_blocks.add(b)
            if b != block_of(d):
                problems.append({"kind": "block_label_disagrees_with_day", "day": d,
                                 "archive_block": b, "expected": block_of(d)})
        for d in days:
            if block_of(d) in r["fit_blocks"]:
                fit_days.add(d)
            elif block_of(d) in r["eval_blocks"]:
                eval_days.add(d)
            else:
                problems.append({"kind": "day_outside_frozen_split", "day": d,
                                 "block": block_of(d)})
        declared_fit = np.asarray(meta["fit"], dtype=bool)
        derived_fit = np.asarray([block_of(str(d)) in r["fit_blocks"] for d in days], dtype=bool)
        if not np.array_equal(declared_fit, derived_fit):
            bad = np.flatnonzero(declared_fit != derived_fit)[:5].tolist()
            problems.append({"kind": "fit_flag_disagrees_with_frozen_blocks", "path": arch["path"],
                             "rows": bad})
    uncovered = sorted(seen_blocks - set(r["fit_blocks"]) - set(r["eval_blocks"]))
    if uncovered:
        problems.append({"kind": "archive_block_outside_split", "blocks": uncovered})
    overlap = sorted(fit_days & eval_days)
    if overlap:
        problems.append({"kind": "fit_eval_day_overlap", "days": overlap[:10]})
    if not fit_days or not eval_days:
        problems.append({"kind": "split_side_empty", "fit_days": len(fit_days),
                         "eval_days": len(eval_days)})
    if problems:
        raise IntegrityRefusalError(
            "the frozen fit/eval blocks do not partition the archive days (an overlapping "
            "source interval would cross the split)",
            problems,
        )
    return {"fit_days": sorted(fit_days), "eval_days": sorted(eval_days)}


# --------------------------------------------------------------------------- #
# encoder region, barrier map, masking
# --------------------------------------------------------------------------- #
def window_mask(arch: dict) -> np.ndarray:
    """(N,T) the declared episode window: [start_t, end_t] only. Padding beyond it is a
    computational pad, never a market zero."""
    width = (arch["meta"]["end_t"] - arch["meta"]["start_t"] + 1)
    t = np.arange(arch["span_allowed"].shape[1], dtype=np.int64)
    return t[None, :] < width[:, None]


def asof_prefix_mask(arch: dict, mode: str) -> np.ndarray:
    """(N,T) the independent SCOPE cut for the causal variant: positions strictly before the ASOF
    clock cutoff ``prefix_len``.

    ``prefix_len`` is the ASOF cutoff / padding length (a function of the declared window and the
    asof instant only - never a silence or gap), so this keeps every observation inside the cutoff,
    including known zeros and runs that resume after a gap, while excluding everything after it.
    For the retrospective variant the whole declared window is in scope (``prefix_len`` equals the
    window width, asserted in the loader).
    """
    t = np.arange(arch["span_allowed"].shape[1], dtype=np.int64)
    if mode == "causal":
        return t[None, :] < arch["meta"]["prefix_len"][:, None]
    return np.ones_like(t[None, :] < arch["span_allowed"].shape[1], dtype=bool)


def seen_mask(arch: dict, mode: str) -> np.ndarray:
    """(N,C,T) cells the model may use: observed (``valid``), inside the declared window, and inside
    the ASOF scope cut. Independent of ``span_allowed`` (mask barrier) and applied to fit
    normalization, the forward embedding and the loss alike, so a value that is valid beyond the
    cutoff can never influence any of them."""
    return arch["valid"] & window_mask(arch)[:, None, :] & asof_prefix_mask(arch, mode)[:, None, :]


def observed_mask(arch: dict, mode: str) -> np.ndarray:
    """(N,T) positions the ENCODER may see and that may be published as valid embedding cells.

    This is the observation region, NOT the reconstruction barrier: a position counts when at least
    one learned input channel is observed there (``valid``), inside the declared window and inside
    the ASOF scope cut. That keeps legitimate known zeros and every observation that resumes after a
    gap/halt up to the cutoff, so the post-gap giant tail is never discarded; unknown coverage stays
    a computational pad and is never a market zero. ``span_allowed`` is deliberately NOT applied
    here - it is only the reconstruction-span barrier.
    """
    return seen_mask(arch, mode).any(axis=1)


def fit_normalization(archives, *, fit_ids, mode: str) -> dict:
    """Per-channel z-score statistics from fit-block, in-scope observed cells only (a known zero is
    an observation and is included; a pad or a post-cutoff cell is not)."""
    c = archives[0]["values"].shape[1]
    sums = np.zeros(c, dtype=np.float64)
    sqs = np.zeros(c, dtype=np.float64)
    counts = np.zeros(c, dtype=np.int64)
    for arch in archives:
        fit = np.asarray(arch["meta"]["fit"], dtype=bool)
        m = (seen_mask(arch, mode) & fit[:, None, None])
        v = arch["values"].astype(np.float64)
        sums += np.where(m, v, 0.0).sum(axis=(0, 2))
        sqs += np.where(m, v * v, 0.0).sum(axis=(0, 2))
        counts += m.sum(axis=(0, 2))
    stats = []
    for ci in range(c):
        if counts[ci] == 0:
            raise IntegrityRefusalError(
                f"channel {archives[0]['channels'][ci]!r} has no valid fit-block cell",
                [{"kind": "fit_support_empty", "channel": ci}],
            )
        mu = sums[ci] / counts[ci]
        var = max(sqs[ci] / counts[ci] - mu * mu, 0.0)
        sd = math.sqrt(var)
        if not np.isfinite(sd) or sd <= 0:
            raise IntegrityRefusalError(
                f"channel {archives[0]['channels'][ci]!r} has zero fit-block variance",
                [{"kind": "fit_zero_variance", "channel": ci}],
            )
        stats.append({"channel": ci, "id": archives[0]["channels"][ci], "mean": float(mu),
                      "std": float(sd), "n_fit": int(counts[ci])})
    return {"stats": stats, "fit_ids": sorted(fit_ids)}


def normalized_values(arch: dict, norm: dict) -> np.ndarray:
    v = arch["values"].astype(np.float32)
    out = np.zeros_like(v)
    for ci, st in enumerate(norm["stats"]):
        out[:, ci, :] = (v[:, ci, :] - np.float32(st["mean"])) / np.float32(st["std"])
    return out


def plan_spans(*, allowed: np.ndarray, mask_span: dict, mask_fraction: float,
               rng: np.random.Generator) -> list:
    """Deterministic masked spans, each wholly inside ONE maximal allowed run (never crossing a
    silence/halt/provider-gap/unknown-coverage boundary).

    ``allowed`` is the row's ``span_allowed`` barrier map: the ONLY region a masked span may cover.
    It is not an input-validity mask - a position outside a span is still a perfectly good encoder
    input (known zero or resumed observation), it simply is not masked.
    """
    t = int(allowed.shape[0])
    runs = []
    lo = None
    for i in range(t + 1):
        on = bool(allowed[i]) if i < t else False
        if on and lo is None:
            lo = i
        elif not on and lo is not None:
            runs.append((lo, i))
            lo = None
    total = int(allowed.sum())
    if total == 0 or not runs:
        return []
    target = max(1, int(round(mask_fraction * total)))
    mn, mx = int(mask_span["min_len"]), int(mask_span["max_len"])
    spans = []
    budget = target
    for ri in rng.permutation(len(runs)):
        if budget <= 0:
            break
        lo, hi = runs[int(ri)]
        pos = lo
        offset = int(rng.integers(0, 1 + max(0, min(3, (hi - lo) - mn)))) if hi - lo >= mn else 0
        pos += offset
        while budget > 0 and hi - pos >= mn:
            length = int(rng.integers(mn, min(mx, hi - pos) + 1))
            spans.append((pos, pos + length))
            pos += length
            budget -= length
    return spans


def mask_matrix(n: int, t: int, plan) -> np.ndarray:
    out = np.zeros((n, t), dtype=bool)
    for i in range(n):
        for lo, hi in plan[i]:
            out[i, lo:hi] = True
    return out


def episode_plan(arch: dict, *, mask_span: dict, mask_fraction: float, seed: int,
                 stream: int, epoch: int, mode: str) -> list:
    """One deterministic mask plan per episode; the RNG stream is keyed by
    (seed, stream, epoch, scale, row) so a resumed run reproduces the identical masks. Spans are
    drawn from the row's ``span_allowed`` barrier map (never crossing a False position) and are
    confined to the ASOF scope, so nothing beyond the cutoff is ever masked."""
    allowed = arch["span_allowed"] & asof_prefix_mask(arch, mode)
    plan = []
    for row in range(allowed.shape[0]):
        rng = np.random.default_rng([seed, stream, epoch, arch["scale"], row])
        plan.append(plan_spans(allowed=allowed[row], mask_span=mask_span,
                               mask_fraction=mask_fraction, rng=rng))
    return plan


# --------------------------------------------------------------------------- #
# model (one family: masked TCN, causal or retrospective)
# --------------------------------------------------------------------------- #
def _require_torch():
    try:
        import torch
        from torch import nn
        from torch.nn import functional as fn
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ContractRefusalError(
            "CPU PyTorch is required for the learned sequence family and is ABSENT "
            f"({exc}). Install it with: pip install 'torch>=2.0' --index-url "
            "https://download.pytorch.org/whl/cpu ; --plan-only validates the frozen record "
            "and the archives without PyTorch."
        ) from exc
    version = tuple(int(x) for x in torch.__version__.split("+")[0].split(".")[:2] if x.isdigit())
    if version and version < TORCH_MIN:
        raise ContractRefusalError(
            f"torch {torch.__version__} is older than the required {TORCH_MIN[0]}.{TORCH_MIN[1]}"
        )
    return torch, nn, fn


def make_model(torch, nn, fn, cfg: dict, *, mode: str, in_planes: int, n_value_channels: int):
    """Build the ONE family: small masked-sequence TCN with a left-only (causal) or symmetric
    (retrospective) receptive field of the SAME shape."""
    causal = mode == "causal"

    class ChanNorm(nn.Module):
        def __init__(self, channels):
            super().__init__()
            self.norm = nn.LayerNorm(channels)

        def forward(self, x):  # (B,C,T)
            return self.norm(x.transpose(1, 2)).transpose(1, 2)

    class Block(nn.Module):
        def __init__(self, channels, kernel, dilation):
            super().__init__()
            self.conv = nn.Conv1d(channels, channels, kernel, dilation=dilation, padding=0)
            self.pad = dilation * (kernel - 1)
            self.norm = ChanNorm(channels) if cfg["norm"] == "layer_norm" else None
            self.residual = cfg["hparams"]["residual"]
            self.drop = nn.Dropout(cfg["dropout"]) if cfg["dropout"] > 0 else None

        def forward(self, x):
            h = x
            if self.pad:
                if causal:
                    # left-only receptive field: never sees a future position
                    h = fn.pad(h, (self.pad, 0))
                else:
                    # same block, symmetric receptive field split as evenly as the kernel allows
                    left = self.pad // 2
                    h = fn.pad(h, (left, self.pad - left))
            h = self.conv(h)
            if self.norm is not None:
                h = self.norm(h)
            h = fn.gelu(h) if cfg["activation"] == "gelu" else (
                fn.relu(h) if cfg["activation"] == "relu" else fn.silu(h))
            if self.drop is not None:
                h = self.drop(h)
            return x + h if self.residual else h

    class MaskedTcn(nn.Module):
        def __init__(self):
            super().__init__()
            self.in_planes = in_planes
            self.inp = nn.Conv1d(in_planes, cfg["width"], 1)
            self.blocks = nn.ModuleList()
            dilation = 1
            for _ in range(cfg["depth"]):
                self.blocks.append(Block(cfg["width"], cfg["kernel_size"], dilation))
                dilation *= cfg["dilation_growth"]
            self.encoder_head = nn.Conv1d(cfg["width"], cfg["embedding_dim"], 1)
            self.decoder = nn.Conv1d(cfg["embedding_dim"], n_value_channels, 1)

        def encode(self, x):
            h = self.inp(x)
            for blk in self.blocks:
                h = blk(h)
            return self.encoder_head(h)

        def forward(self, x):
            emb = self.encode(x)
            return self.decoder(emb), emb

    model = MaskedTcn()
    init = cfg["hparams"]["weight_init"]
    for m in model.modules():
        if isinstance(m, nn.Conv1d):
            if init == "kaiming_uniform":
                nn.init.kaiming_uniform_(m.weight, nonlinearity="relu")
            elif init == "xavier_uniform":
                nn.init.xavier_uniform_(m.weight)
            else:
                nn.init.orthogonal_(m.weight)
            if m.bias is not None:
                nn.init.zeros_(m.bias)
    return model


def build_inputs(torch, arch, norm: dict, *, planes, hidden: np.ndarray, mode: str):
    """Assemble the model input (values + explicit validity planes) and the masked-span target.

    The encoder sees every in-scope observed cell (``seen_mask``: inside the declared window and
    inside the ASOF cutoff, known zeros and observations that resume after a gap/halt included);
    ``span_allowed`` is NOT an input-validity mask - it only bounds where a masked span may lie. A
    masked cell is hidden from the encoder (value plane 0, validity plane 0) and is a reconstruction
    target only where the true value was observed; invalid/padded/post-cutoff cells contribute
    exactly zero loss. Episode metadata never enters and ``span_allowed`` is never itself a model
    input or a reconstruction target.
    """
    v = normalized_values(arch, norm)
    seen = seen_mask(arch, mode)
    n_obj, _, t_len = seen.shape
    if hidden.shape != (n_obj, t_len):
        raise ContractRefusalError(
            f"the mask plan has shape {tuple(hidden.shape)}, expected (N,T)=({n_obj},{t_len}) "
            "matching the archive's time axis; a mask that does not match must never broadcast"
        )
    obs = seen & ~hidden[:, None, :]
    val_plane = np.where(obs, v, 0.0)
    parts = []
    if "value" in planes:
        parts.append(val_plane)
    if "valid" in planes:
        parts.append(obs.astype(np.float32))
    x = np.concatenate(parts, axis=1)
    loss_mask = hidden[:, None, :] & seen
    target = np.where(loss_mask, v, 0.0)
    return (torch.from_numpy(np.ascontiguousarray(x, dtype=np.float32)),
            torch.from_numpy(np.ascontiguousarray(loss_mask)),
            torch.from_numpy(np.ascontiguousarray(target, dtype=np.float32)))


def masked_reconstruction_loss(pred, target, loss_mask):
    se = (pred - target) ** 2 * loss_mask.to(pred.dtype)
    denom = loss_mask.sum()
    if float(denom) == 0.0:
        return pred.sum() * 0.0, 0
    return se.sum() / denom, int(denom)


def plane_channels(planes, n_value: int) -> int:
    return (n_value if "value" in planes else 0) + (n_value if "valid" in planes else 0)


# --------------------------------------------------------------------------- #
# training / evaluation
# --------------------------------------------------------------------------- #
def make_optimizer(torch, model, ocfg):
    if ocfg["name"] == "adam":
        return torch.optim.Adam(model.parameters(), lr=ocfg["lr"],
                                betas=tuple(ocfg["betas"]), eps=ocfg["eps"],
                                weight_decay=ocfg["weight_decay"])
    if ocfg["name"] == "adamw":
        return torch.optim.AdamW(model.parameters(), lr=ocfg["lr"],
                                 betas=tuple(ocfg["betas"]), eps=ocfg["eps"],
                                 weight_decay=ocfg["weight_decay"])
    return torch.optim.SGD(model.parameters(), lr=ocfg["lr"], momentum=ocfg["momentum"],
                           weight_decay=ocfg["weight_decay"])


def archive_tensors(torch, arch, norm, cfg, *, seed, stream, epoch, mode):
    """Plan the masks once and build the whole-archive model input / loss mask / target."""
    plan = episode_plan(arch, mask_span=cfg["mask_span"], mask_fraction=cfg["mask_fraction"],
                        seed=seed, stream=stream, epoch=epoch, mode=mode)
    hidden = mask_matrix(arch["span_allowed"].shape[0], arch["span_allowed"].shape[1], plan)
    x, lm, tg = build_inputs(torch, arch, norm, planes=cfg["input_planes"], hidden=hidden,
                             mode=mode)
    return plan, x, lm, tg


def iter_batches(torch, archives, *, which: str, cfg, seed: int, epoch: int, mode: str,
                 norm: dict, stream: int):
    """Deterministic scale-grouped batches (episodes of one scale share T)."""
    if which == "fit":
        order_pool = [i for i, a in enumerate(archives)
                      if int(np.asarray(a["meta"]["fit"], dtype=bool).sum()) > 0]
    else:
        order_pool = [i for i, a in enumerate(archives)
                      if int((~np.asarray(a["meta"]["fit"], dtype=bool)).sum()) > 0]
    rng = np.random.default_rng([seed, stream, epoch, 7919])
    order = [int(x) for x in rng.permutation(len(order_pool))]
    for si in order:
        ai = order_pool[si]
        arch = archives[ai]
        fit = np.asarray(arch["meta"]["fit"], dtype=bool)
        rows = np.flatnonzero(fit if which == "fit" else ~fit)
        if rows.size == 0:
            continue
        _, x, lm, tg = archive_tensors(torch, arch, norm, cfg, seed=seed, stream=stream,
                                       epoch=epoch, mode=mode)
        perm = [int(p) for p in rng.permutation(rows.size)]
        for s in range(0, len(perm), cfg["batch_size"]):
            sel = torch.from_numpy(np.asarray([rows[p] for p in perm[s:s + cfg["batch_size"]]],
                                              dtype=np.int64))
            yield x[sel], lm[sel], tg[sel]


def evaluate(torch, model, archives, *, mode: str, norm: dict, cfg, seed: int, stream: int,
             which: str) -> dict:
    """Held-out masked reconstruction loss with ONE fixed mask set shared by every comparator."""
    total = 0.0
    n = 0
    spans = 0
    was_training = model.training
    model.eval()
    with torch.no_grad():
        for arch in archives:
            fit = np.asarray(arch["meta"]["fit"], dtype=bool)
            rows = np.flatnonzero(fit if which == "fit" else ~fit)
            if rows.size == 0:
                continue
            plan, x, lm, tg = archive_tensors(torch, arch, norm, cfg, seed=seed,
                                              stream=stream, epoch=0, mode=mode)
            for s in range(0, rows.size, cfg["batch_size"]):
                sel = torch.from_numpy(np.asarray(rows[s:s + cfg["batch_size"]], dtype=np.int64))
                pred, _ = model(x[sel])
                loss, count = masked_reconstruction_loss(pred, tg[sel], lm[sel])
                total += float(loss.detach()) * count
                n += count
            spans += sum(len(plan[int(r)]) for r in rows)
    model.train(was_training)
    return {"loss": (total / n) if n else None, "masked_cells": int(n), "spans": int(spans)}


def encode_all(torch, model, archives, norm: dict, mode: str, cfg) -> dict:
    """Encode every episode once over its full observed region.

    Embedding validity is the OBSERVED region (known zeros and post-gap resumed observations
    included) - never ``span_allowed``, which is only the reconstruction-span barrier, and never a
    prefix cutoff. Padding is a computational zero (valid=False), never a market zero.
    """
    t_max = max(a["values"].shape[2] for a in archives)
    n_total = sum(a["values"].shape[0] for a in archives)
    e = cfg["embedding_dim"]
    values = np.zeros((n_total, e, t_max), dtype=np.float32)
    valid = np.zeros((n_total, e, t_max), dtype=bool)
    ids, days, blocks, scales = [], [], [], []
    starts, ends, queries, prefixes, fits, modes = [], [], [], [], [], []
    at = 0
    model.eval()
    with torch.no_grad():
        for arch in archives:
            x, _, _ = build_inputs(
                torch, arch, norm, planes=cfg["input_planes"],
                hidden=np.zeros((arch["valid"].shape[0], arch["valid"].shape[2]), dtype=bool),
                mode=mode,
            )
            emb = model.encode(x).cpu().numpy()
            n, _, t = emb.shape
            u = observed_mask(arch, mode)
            values[at:at + n, :, :t] = emb
            valid[at:at + n, :, :t] = u[:, None, :]
            meta = arch["meta"]
            ids.extend(str(v) for v in meta["episode_id"])
            days.extend(str(v) for v in meta["day"])
            blocks.extend(str(v) for v in meta["block"])
            scales.extend([int(arch["scale"])] * n)
            starts.extend(int(v) for v in meta["start_t"])
            ends.extend(int(v) for v in meta["end_t"])
            queries.extend(int(v) for v in meta["query_t"])
            prefixes.extend(int(v) for v in meta["prefix_len"])
            fits.extend(bool(v) for v in meta["fit"])
            modes.extend([mode] * n)
            at += n
    model.train()
    return {
        "values": values, "valid": valid, "object_ids": np.asarray(ids, dtype="U160"),
        "episode_id": np.asarray(ids, dtype="U160"),
        "day": np.asarray(days, dtype="U16"), "block": np.asarray(blocks, dtype="U16"),
        "scale": np.asarray(scales, dtype=np.int32),
        "start_t": np.asarray(starts, dtype=np.int32), "end_t": np.asarray(ends, dtype=np.int32),
        "query_t": np.asarray(queries, dtype=np.int32),
        "prefix_len": np.asarray(prefixes, dtype=np.int32),
        "fit": np.asarray(fits, dtype=bool), "mode": np.asarray(modes, dtype="U16"),
        "channels": np.asarray([f"emb{i}" for i in range(e)], dtype="U16"),
    }


# --------------------------------------------------------------------------- #
# run identity / resume
# --------------------------------------------------------------------------- #
def run_identity(r: dict, cfg: dict, loaded: dict, code_sha: str, tier: str) -> dict:
    return {
        "mode": r["mode"], "fold": r["fold"], "tier": tier,
        "matrix_version": r["matrix_version"],
        "config_version": cfg["config_version"],
        "config_sha256": sha256_bytes(canon_bytes(cfg)),
        "seed": cfg["seed"], "code_sha256": code_sha,
        "freeze_r_sha256": r["sha256"],
        "fit_blocks": r["fit_blocks"], "eval_blocks": r["eval_blocks"],
        "archives": [{"scale": a["scale"], "sha256": a["sha256"], "rows": a["rows"]}
                     for a in loaded["archives"]],
    }


# --------------------------------------------------------------------------- #
# pipeline
# --------------------------------------------------------------------------- #
def mask_plan_stats(archives, cfg: dict, mode: str, *, seed: int, stream: int = 0,
                    epoch: int = 0) -> dict:
    """REALIZED masking statistics for the canonical plan, plus the quantization bound.

    Reporting only - this never influences training (the training loop uses its own per-epoch
    stream). It records what the frozen ``mask_fraction``/``mask_span`` policy actually produces:
    spans are drawn per episode from that episode's ``span_allowed`` runs, of length in
    ``[min_len, max_len]``. A span cannot be shorter than ``min_len`` and the final span may
    overshoot the remaining budget by up to ``max_len - 1`` cells, so the realized rate can exceed
    the declared fraction (bound ``masked <= sum_i target_i + (max_len - 1) * K`` with K = episodes
    receiving at least one span); it can fall short only where an episode has no contiguous room for
    a legal span.
    """
    spans = masked = maskable = observed = row_targets = rows_with_spans = 0
    for arch in archives:
        allowed = arch["span_allowed"] & asof_prefix_mask(arch, mode)
        o = observed_mask(arch, mode)
        plan = episode_plan(arch, mask_span=cfg["mask_span"], mask_fraction=cfg["mask_fraction"],
                            seed=seed, stream=stream, epoch=epoch, mode=mode)
        maskable += int(allowed.sum())
        observed += int(o.sum())
        for i in range(allowed.shape[0]):
            a_i = int(allowed[i].sum())
            if a_i:
                row_targets += max(1, int(round(cfg["mask_fraction"] * a_i)))
            rows_with_spans += 1 if plan[i] else 0
            spans += len(plan[i])
            masked += sum(hi - lo for lo, hi in plan[i])
    mx = int(cfg["mask_span"]["max_len"])
    bound = row_targets + (mx - 1) * rows_with_spans
    return {
        "spans": spans,
        "masked_cells": masked,
        "maskable_cells": maskable,
        "observed_cells": observed,
        "mask_fraction_declared": cfg["mask_fraction"],
        "mask_fraction_measured": (masked / maskable) if maskable else None,
        "mask_span": dict(cfg["mask_span"]),
        "mask_fraction_bound": {
            "rule": "masked_total <= sum_i target_i + (max_len - 1) * K, with K = episodes "
                    "receiving at least one span (spans are quantized by min_len/max_len)",
            "target_sum": row_targets,
            "rows_with_spans": rows_with_spans,
            "per_row_overshoot_max_cells": mx - 1,
            "worst_case_fraction": (bound / maskable) if maskable else None,
            "note": "the realized rate may exceed the declared fraction because a span cannot be "
                    "shorter than min_len and the final span may overshoot the remaining budget by "
                    "up to max_len - 1 cells; it falls short only where an episode has no "
                    "contiguous room for a legal span",
        },
    }


def plan_only(r: dict, cfg: dict, loaded: dict, split: dict, norm: dict, *, mode: str) -> dict:
    stats = mask_plan_stats(loaded["archives"], cfg, mode, seed=cfg["seed"])
    known_zeros = 0
    observed_outside_spans = 0
    for arch in loaded["archives"]:
        o = observed_mask(arch, mode)
        plan = episode_plan(arch, mask_span=cfg["mask_span"], mask_fraction=cfg["mask_fraction"],
                            seed=cfg["seed"], stream=0, epoch=0, mode=mode)
        hidden = mask_matrix(arch["span_allowed"].shape[0], arch["span_allowed"].shape[1], plan)
        known_zeros += int(((arch["values"] == 0.0) & arch["valid"]).sum())
        observed_outside_spans += int((o & ~hidden).sum())
    per_channel = plane_channels(cfg["input_planes"], len(r["input_ids"]))
    return {"status": "PLAN-ONLY (no model trained, no embedding written)",
            "mode": mode, "fold": r["fold"], "tier": None,
            "matrix_version": r["matrix_version"], "config": cfg,
            "architecture": ARCHITECTURE,
            "input_channels": r["input_ids"], "input_planes": cfg["input_planes"],
            "model_input_channels": per_channel,
            "model_parameters": None,
            "fit_days": split["fit_days"], "eval_days": split["eval_days"],
            "episodes": {"fit": int(sum(
                np.asarray(a["meta"]["fit"], dtype=bool).sum() for a in loaded["archives"])),
                "eval": int(sum(
                    (~np.asarray(a["meta"]["fit"], dtype=bool)).sum()
                    for a in loaded["archives"]))},
            "scales": [{"scale": a["scale"], "rows": a["rows"],
                        "sha256": a["sha256"]} for a in loaded["archives"]],
            "normalization": norm["stats"],
            "regions": {
                "observed_cells": stats["observed_cells"],
                "known_zero_cells": known_zeros,
                "maskable_cells": stats["maskable_cells"],
                "observed_outside_mask_spans": observed_outside_spans,
                "rule": "observed = encoder/embedding region (valid, in-window; known zeros and "
                        "post-gap resumed observations included, so the giant tail is preserved); "
                        "maskable = span_allowed barrier map only",
                "known_zero_cells_note": "counts observed cells whose value is exactly 0.0; it can "
                                         "legitimately be 0 for a channel set whose validity rules "
                                         "require activity, and it never implies that missing "
                                         "coverage was treated as zero",
            },
            "mask_plan": stats,
            "input_bytes": loaded["bytes"]}


def train_and_publish(r: dict, cfg: dict, loaded: dict, split: dict, *, mode: str, tier: str,
                      out: Path, out_evidence: Path, resume: bool,
                      manifest: Path | None, schema_path: Path, code_sha: str) -> int:
    torch, nn, fn = _require_torch()
    torch.set_num_threads(1)
    torch.manual_seed(cfg["seed"])
    torch.use_deterministic_algorithms(True)

    identity = run_identity(r, cfg, loaded, code_sha, tier)
    run_dir = out / "runs" / mode / r["fold"]
    state_path = run_dir / "run_state.json"
    checkpoint_path = run_dir / "checkpoint.pt"
    metrics_path = run_dir / "metrics.json"

    if state_path.is_file():
        state = load_json(state_path)
        if state.get("identity") != identity:
            raise ContractRefusalError(
                f"the fold {mode}/{r['fold']} is already claimed by a different configuration, "
                "seed, archive set or code revision; ONE configuration and ONE run per fold - "
                "no silent second seed and no hyperparameter retry"
            )
        if state.get("status") == "complete":
            if not resume:
                raise ContractRefusalError(
                    f"run {run_dir} is already complete; --resume verifies it and never retrains"
                )
            report = load_json(metrics_path) if metrics_path.is_file() else {}
            write_json(out_evidence / REPORT_NAME, report)
            print(f"already complete (exact resume verified, no retrain): {run_dir}")
            return 0
        if state.get("status") != "running":
            raise ContractRefusalError(
                f"unknown run status {state.get('status')!r} in {state_path}"
            )
        if not resume:
            raise ContractRefusalError(
                f"an interrupted run exists at {run_dir}; pass --resume to continue the exact run"
            )
    else:
        if resume:
            raise ContractRefusalError(f"--resume given but no run state exists at {state_path}")
        run_dir.mkdir(parents=True, exist_ok=True)
        write_json(state_path, {"status": "running", "identity": identity, "epoch": 0})

    n_value = len(r["input_ids"])
    in_planes = plane_channels(cfg["input_planes"], n_value)
    model = make_model(torch, nn, fn, cfg, mode=mode, in_planes=in_planes,
                       n_value_channels=n_value)
    n_params = int(sum(p.numel() for p in model.parameters()))
    optimizer = make_optimizer(torch, model, cfg["optimizer"])
    start_epoch = 0
    if checkpoint_path.is_file() and resume:
        ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        model.load_state_dict(ckpt["model"])
        optimizer.load_state_dict(ckpt["optimizer"])
        torch.set_rng_state(ckpt["torch_rng"])
        start_epoch = int(ckpt["epoch"]) + 1
        print(f"resuming from epoch {start_epoch} of {cfg['epochs']}")

    norm = fit_normalization(loaded["archives"], fit_ids=split["fit_days"], mode=mode)
    model.train()
    history = []
    t0 = time.time()
    for epoch in range(start_epoch, cfg["epochs"]):
        total = 0.0
        n = 0
        for x, lm, tg in iter_batches(torch, loaded["archives"], which="fit", cfg=cfg,
                                      seed=cfg["seed"], epoch=epoch, mode=mode, norm=norm,
                                      stream=0):
            optimizer.zero_grad(set_to_none=True)
            pred, _ = model(x)
            loss, count = masked_reconstruction_loss(pred, tg, lm)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), cfg["hparams"]["grad_clip_norm"])
            optimizer.step()
            total += float(loss.detach()) * count
            n += count
        epoch_loss = (total / n) if n else None
        entry = {"epoch": epoch, "train_masked_loss": epoch_loss, "masked_cells": int(n)}
        if (epoch + 1) % cfg["hparams"]["eval_every"] == 0 or epoch == cfg["epochs"] - 1:
            snap_fit = evaluate(torch, model, loaded["archives"], mode=mode, norm=norm, cfg=cfg,
                               seed=cfg["seed"], stream=0, which="fit")
            snap_eval = evaluate(torch, model, loaded["archives"], mode=mode, norm=norm, cfg=cfg,
                                seed=cfg["seed"], stream=1, which="eval")
            entry["heldout_fit_masked_loss"] = snap_fit["loss"]
            entry["heldout_eval_masked_loss"] = snap_eval["loss"]
        history.append(entry)
        torch.save({"model": model.state_dict(), "optimizer": optimizer.state_dict(),
                    "epoch": epoch, "torch_rng": torch.get_rng_state(), "identity": identity},
                   checkpoint_path)
        write_json(state_path, {"status": "running", "identity": identity, "epoch": epoch + 1,
                                "last_train_loss": epoch_loss})
        print(f"epoch {epoch + 1}/{cfg['epochs']} train_masked_loss={epoch_loss} cells={n}")

    heldout_train = evaluate(torch, model, loaded["archives"], mode=mode, norm=norm, cfg=cfg,
                             seed=cfg["seed"], stream=0, which="fit")
    heldout_eval = evaluate(torch, model, loaded["archives"], mode=mode, norm=norm, cfg=cfg,
                            seed=cfg["seed"], stream=1, which="eval")

    # frozen random comparator: SAME architecture, SAME held-out mask, never trained
    torch.manual_seed(cfg["seed"] ^ 0x5EED)
    rand_model = make_model(torch, nn, fn, cfg, mode=mode, in_planes=in_planes,
                            n_value_channels=n_value)
    for p in rand_model.parameters():
        p.requires_grad_(False)
    random_eval = evaluate(torch, rand_model, loaded["archives"], mode=mode, norm=norm, cfg=cfg,
                           seed=cfg["seed"], stream=1, which="eval")
    random_train = evaluate(torch, rand_model, loaded["archives"], mode=mode, norm=norm, cfg=cfg,
                            seed=cfg["seed"], stream=0, which="fit")

    weights_path = run_dir / "weights.pt"
    torch.save(model.state_dict(), weights_path)
    weights_sha = sha256_file(weights_path)
    rand_weights_path = run_dir / "random_weights.pt"
    torch.save(dict(rand_model.state_dict()), rand_weights_path)

    emb = encode_all(torch, model, loaded["archives"], norm, mode, cfg)
    emb_path = run_dir / "embeddings.npz"
    deterministic_npz(emb_path, dict(emb))
    emb_sha = sha256_file(emb_path)
    var_stats = []
    for c in range(emb["values"].shape[1]):
        m = emb["valid"][:, c, :]
        vals = emb["values"][:, c, :][m]
        var_stats.append({"channel": c, "n": int(vals.size),
                          "std": float(np.std(vals)) if vals.size else None,
                          "mean": float(np.mean(vals)) if vals.size else None})
    degenerate = [s["channel"] for s in var_stats if not s["std"]]

    improvement = None
    if heldout_eval["loss"] is not None and random_eval["loss"] not in (None, 0.0):
        improvement = (random_eval["loss"] - heldout_eval["loss"]) / random_eval["loss"]
    # the frozen record guarantees all nine gates are bound; this one is compared once, no retry
    gate_threshold = float(r["gates"]["ssl_over_random_improvement"])
    gate = {"threshold": gate_threshold, "measured": improvement,
            "status": "PASS" if (improvement is not None and improvement >= gate_threshold)
            else "FAIL"}

    metrics = {
        "node_id": "sequence.tape_atlas.masked_tcn",
        "matrix_version": r["matrix_version"],
        "family": FAMILY, "architecture": ARCHITECTURE, "objective": OBJECTIVE,
        "mode": mode, "labelled": cfg["labelled"], "label": cfg["label"],
        "prospective_live_use": mode == "causal",
        "retrospective_live_use": False,
        "fold": r["fold"], "tier": tier, "seed": cfg["seed"],
        "config": cfg, "config_version": cfg["config_version"],
        "config_sha256": identity["config_sha256"],
        "code_sha256": code_sha,
        "freeze_r_sha256": r["sha256"],
        "inputs": {
            "channels": r["input_ids"],
            "planes": cfg["input_planes"],
            "model_input_channels": in_planes,
            "rule": "selected tape/print channels plus explicit validity masks; no clock, block, "
                    "family, entry_rank, outcome or censor input ever",
            "span_allowed": "coordinate barrier map for the RECONSTRUCTION SPAN only: a masked "
                            "span "
                            "may never cross a False position; never a model input, never a "
                            "distance channel, never a target, and never the encoder/embedding "
                            "validity (observed cells outside a span stay fully visible)",
        },
        "parameters": {"count": n_params, "weights_sha256": weights_sha,
                       "weights_path": str(weights_path),
                       "random_weights_path": str(rand_weights_path)},
        "split": {"split_key": r["split_key"], "fit_blocks": r["fit_blocks"],
                  "eval_blocks": r["eval_blocks"], "fit_days": split["fit_days"],
                  "eval_days": split["eval_days"],
                  "fit_eval_day_overlap": sorted(set(split["fit_days"]) & set(split["eval_days"])),
                  "normalization": norm["stats"]},
        "training": {"epochs": cfg["epochs"], "history": history,
                     "wall_seconds": time.time() - t0,
                     "checkpoint": str(checkpoint_path), "resumed_from_epoch": start_epoch},
        "heldout_masked_loss": {
            "fit_block": {"trained": heldout_train["loss"], "random": random_train["loss"],
                          "masked_cells": heldout_train["masked_cells"],
                          "spans": heldout_train["spans"]},
            "eval_block": {"trained": heldout_eval["loss"], "random": random_eval["loss"],
                           "masked_cells": heldout_eval["masked_cells"],
                           "spans": heldout_eval["spans"]},
            "same_heldout_mask_for_both": True,
            "unit": "mean squared error on masked valid in-span cells",
        },
        "ssl_over_random_improvement": improvement,
        "one_shot_gates": {"echoed_from_freeze_r": r["gates"],
                           "evaluated": {"ssl_over_random_improvement": gate},
                           "runs_per_fold": 1, "configurations_per_fold": 1, "retries": 0,
                           "family_retirement_note":
                               "a gate failure retires this learned family for v0 only; it does "
                               "not retire the Atlas"},
        "embedding": {"path": str(emb_path), "sha256": emb_sha,
                      "shape": list(emb["values"].shape),
                      "channels": [str(c) for c in emb["channels"]],
                      "pad_rule": "valid=False is a computational pad, never a market zero",
                      "channel_stats": var_stats, "degenerate_channels": degenerate,
                      "consumed_by": "basket_tape_atlas_geometry.py:load_embeddings "
                                     "(values/valid/object_ids + <path>.meta.json provenance)"},
        "archives": [{"scale": a["scale"], "path": a["path"], "sha256": a["sha256"],
                      "rows": a["rows"], "sidecar": a["sidecar"], "scope": a["scope"]}
                     for a in loaded["archives"]],
        "seed_rule": "exactly one seed, one configuration and one run per fold; resumed runs "
                     "reuse the identical identity",
        "mask_plan": mask_plan_stats(loaded["archives"], cfg, mode, seed=cfg["seed"]),
        "scope_rule": {
            "encoder_and_embedding": "valid AND inside [start_t, end_t] AND (causal: position < "
                                     "prefix_len) - the ASOF cutoff is applied INDEPENDENTLY, "
                                     "never trusted to the producer's truncation",
            "maskable": "span_allowed runs only, confined to the ASOF scope (the barrier map is "
                        "never a scope cutoff)",
            "prefix_len": "ASOF clock cutoff / padding length (window + asof instant only)",
            "query_t": "completion clock of the last included bucket: causal start_t + prefix_len, "
                       "retrospective end_t; audit metadata only, never incremented, never an "
                       "input",
            "end_t": "the full declared window end; NOT scope-bound for the causal variant",
            "asserted": ["causal: query_t == start_t + prefix_len and prefix_len <= window width",
                         "retrospective: prefix_len == window width and query_t == end_t"],
        },
        "outcome_columns_read": [],
    }

    meta = {
        "node_id": "sequence.tape_atlas.masked_tcn.embeddings",
        "matrix_version": r["matrix_version"],
        "layout": {"values": "float32 (N,E,T) 0.0 at valid==False",
                   "valid": "bool (N,E,T) padding is a COMPUTATIONAL zero, never a market zero",
                   "object_ids": "unicode (N,) = geometry corpus object_id (path_id@scale)",
                   "channels": "unicode (E,) embedding channels"},
        "episodes": "episode_id/day/block/scale/start_t/end_t/query_t/prefix_len/fit/mode are "
                    "audit metadata, never model inputs and never distance channels",
        "provenance": {
            "model": f"masked_tcn.{FAMILY}.{mode}",
            "mode": mode, "labelled": cfg["labelled"], "label": cfg["label"],
            "fit_days": split["fit_days"], "eval_days": split["eval_days"],
            "fit_blocks": r["fit_blocks"], "eval_blocks": r["eval_blocks"],
            "fit_eval_day_overlap": [],
            "config_version": cfg["config_version"],
            "config_sha256": identity["config_sha256"],
            "code_sha256": code_sha,
            "freeze_r_sha256": r["sha256"],
            "weights_sha256": weights_sha,
            "matrix_version": r["matrix_version"],
            "tier": tier,
            "normalization": "per-channel fit-block z-score (see metrics.json split.normalization)",
            "seed": cfg["seed"], "epochs": cfg["epochs"],
        },
    }
    write_json(Path(str(emb_path) + ".meta.json"), meta)
    write_json(metrics_path, metrics)
    write_json(state_path, {"status": "complete", "identity": identity, "epoch": cfg["epochs"],
                            "metrics_path": str(metrics_path),
                            "embeddings_path": str(emb_path),
                            "embedding_sha256": emb_sha, "weights_sha256": weights_sha})
    write_json(out_evidence / REPORT_NAME, metrics)
    write_json(out_evidence / MANIFEST_NAME, {
        "node_id": "sequence.tape_atlas.masked_tcn",
        "version": r["matrix_version"],
        "contract": "freeze-r-v0",
        "freeze_r": {"path": r["path"], "sha256": r["sha256"],
                     "matrix_version": r["matrix_version"]},
        "parents": {
            "schema": {"path": str(schema_path), "sha256": loaded["schema_sha256"]},
            "manifest": ({"path": str(manifest), "sha256": loaded["manifest_sha256"]}
                         if manifest is not None else None),
            "input_archives": [{"path": a["path"], "sha256": a["sha256"],
                                "rows": a["rows"], "scale": a["scale"]}
                               for a in loaded["archives"]],
            "input_archive_sidecars": [{"path": a["sidecar"]["path"],
                                        "sha256": a["sidecar"]["sha256"],
                                        "scale": a["scale"], "mode": mode,
                                        "causal_prefix_rule":
                                            a["sidecar"]["causal_prefix_rule"],
                                        "retrospective_clock_rule":
                                            a["sidecar"]["retrospective_clock_rule"],
                                        "span_allowed_axes": a["sidecar"]["span_allowed_axes"]}
                                       for a in loaded["archives"]],
        },
        "outputs": {
            "embeddings": {"path": str(emb_path), "sha256": emb_sha},
            "embeddings_meta": {"path": str(emb_path) + ".meta.json",
                                "sha256": sha256_file(Path(str(emb_path) + ".meta.json"))},
            "weights": {"path": str(weights_path), "sha256": weights_sha},
            "checkpoint": {"path": str(checkpoint_path),
                           "sha256": sha256_file(checkpoint_path)},
            "metrics": {"path": str(metrics_path), "sha256": sha256_file(metrics_path)},
        },
        "mode": mode, "fold": r["fold"], "tier": tier, "seed": cfg["seed"],
        "episodes": emb["values"].shape[0], "channels": emb["values"].shape[1],
        "code_sha256": code_sha,
        "supported_tiers": tier_support(),
    })
    print(f"complete: {run_dir} (heldout eval loss trained={heldout_eval['loss']} "
          f"random={random_eval['loss']})")
    return 0


def tier_support() -> dict:
    """The learned minute sequence column of the observation tier_support_matrix (verbatim)."""
    if not CONTRACT_PATH.is_file():
        return {}
    contract = load_json(CONTRACT_PATH)
    matrix = contract.get("tier_support_matrix") or {}
    return {tier: cell.get(TRAIN_TIER_KEY) for tier, cell in matrix.items()}


def check_tier(tier: str) -> dict:
    if not CONTRACT_PATH.is_file():
        raise ContractRefusalError(f"observation contract not found: {CONTRACT_PATH}")
    contract = load_json(CONTRACT_PATH)
    matrix = contract.get("tier_support_matrix") or {}
    if tier not in matrix:
        raise ContractRefusalError(
            f"--tier {tier!r} is not a declared tier {sorted(matrix)}"
        )
    cell = matrix[tier].get(TRAIN_TIER_KEY)
    if cell is False or (isinstance(cell, str) and cell.strip().lower() in ("false", "no", "none")):
        raise ContractRefusalError(
            f"tier {tier!r} does not support a learned minute sequence "
            f"({TRAIN_TIER_KEY}={cell!r}); "
            "a non-supporting tier must never be encoded or mixed"
        )
    if cell is None:
        raise ContractRefusalError(
            f"tier {tier!r} leaves {TRAIN_TIER_KEY} UNBOUND in the observation contract"
        )
    return {tier: cell}


def refuse_and_report(out_evidence: Path, *, r, loaded, problems, kind) -> None:
    write_json(out_evidence / REPORT_NAME, {
        "node_id": "sequence.tape_atlas.masked_tcn",
        "status": "REFUSED",
        "kind": kind,
        "problems": problems,
        "freeze_r": None if r is None else {
            "path": r["path"], "sha256": r["sha256"], "matrix_version": r["matrix_version"]},
        "archives": [] if loaded is None else [
            {"scale": a["scale"], "path": a["path"], "sha256": a["sha256"]}
            for a in loaded["archives"]],
    })


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Freeze-R masked-TCN sequence family (learned view). One configuration and "
                    "one run per fold; masked-span reconstruction only.")
    ap.add_argument("--freeze-r", required=False, help="the genuinely frozen Freeze-R record")
    ap.add_argument("--archive-dir", required=False,
                    help="the geometry producer's ssl archive directory (<scale>_<mode>.npz)")
    ap.add_argument("--mode", default=None, choices=list(MODES))
    ap.add_argument("--fold", default="block",
                    help="must equal binding_slots.transform_training_blocks.split_key")
    ap.add_argument("--tier", default=None,
                    help="the observation tier the archives represent (selected_paths)")
    ap.add_argument("--manifest", default=None, help="optional observation manifest.json")
    ap.add_argument("--schema", default=str(SCHEMA_PATH), help="observation schema.json")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="large deterministic output root")
    ap.add_argument("--out-evidence", default=str(DEFAULT_OUT_EVIDENCE))
    ap.add_argument("--resume", action="store_true",
                    help="continue/verify the exact run for this fold (never retrain a different "
                         "configuration)")
    ap.add_argument("--plan-only", action="store_true",
                    help="validate record + archives and print the plan; no training, no PyTorch")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return run_selftest()
    if not args.freeze_r or not args.archive_dir or not args.mode or not args.tier:
        ap.error("--freeze-r, --archive-dir, --mode and --tier are required (or --selftest)")
    schema_path = Path(args.schema)
    manifest = Path(args.manifest) if args.manifest else None
    out_evidence = Path(args.out_evidence)
    code_sha = sha256_file(Path(__file__).resolve())
    r = None
    loaded = None
    try:
        check_tier(args.tier)
        r = load_frozen_family(Path(args.freeze_r), mode=args.mode, fold=args.fold)
        if args.mode == "causal":
            prefix_rule = CAUSAL_PREFIX_RULE
            print(f"causal variant: left-only receptive fields, prefix rule {prefix_rule}, "
                  "garbage-suffix invariant")
        else:
            print(f"retrospective variant: labelled ({r['config']['label']!r}), never live, "
                  "out-of-window junk insensitive")
        loaded = load_archives(Path(args.archive_dir), r=r, mode=args.mode, manifest=manifest,
                              schema_path=schema_path, verify=True)
        split = assign_split(loaded, r=r)
        norm = fit_normalization(loaded["archives"], fit_ids=split["fit_days"], mode=args.mode)
        if args.plan_only:
            plan = plan_only(r, r["config"], loaded, split, norm, mode=args.mode)
            plan["tier"] = args.tier
            plan["supported_tiers"] = tier_support()
            write_json(out_evidence / PLAN_NAME, plan)
            print(f"plan written: {out_evidence / PLAN_NAME}")
            return 0
        return train_and_publish(r, r["config"], loaded, split, mode=args.mode, tier=args.tier,
                                 out=Path(args.out),
                                 out_evidence=out_evidence, resume=args.resume,
                                 manifest=manifest, schema_path=schema_path, code_sha=code_sha)
    except ContractRefusalError as exc:
        print(f"CONTRACT REFUSAL: {exc}", file=sys.stderr)
        return 2
    except IntegrityRefusalError as exc:
        print(f"INTEGRITY REFUSAL: {exc}", file=sys.stderr)
        refuse_and_report(out_evidence, r=r, loaded=loaded, problems=exc.problems,
                          kind="integrity")
        return 3


# --------------------------------------------------------------------------- #
# selftest: genuine boundary cases (future garbage, mask loss, train/eval leakage)
# --------------------------------------------------------------------------- #
def run_selftest() -> int:
    import shutil
    import tempfile

    fails = []

    def chk(name, ok, detail=None):
        if not ok:
            fails.append(name)
        print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))

    try:
        torch, nn, fn = _require_torch()
    except ContractRefusalError as exc:
        print(f"selftest needs CPU PyTorch: {exc}", file=sys.stderr)
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="seq_selftest_"))
    try:
        cfg = {
            "config_version": "selftest-v1", "family": FAMILY, "mode": "causal",
            "labelled": False, "label": None, "objective": OBJECTIVE,
            "width": 4, "depth": 2, "kernel_size": 3, "embedding_dim": 3, "dilation_growth": 2,
            "activation": "gelu", "norm": "layer_norm", "dropout": 0.0,
            "optimizer": {"name": "adam", "lr": 0.01, "weight_decay": 0.0,
                          "betas": [0.9, 0.999], "eps": 1e-8},
            "epochs": 2, "batch_size": 3,
            "mask_span": {"min_len": 2, "max_len": 3}, "mask_fraction": 0.25,
            "seed": 7, "fold": "block", "input_planes": ["value", "valid"],
            "hparams": {"weight_init": "kaiming_uniform", "residual": True,
                        "grad_clip_norm": 1.0, "eval_every": 1},
        }
        t = 12
        n = 6
        rng = np.random.default_rng(0)
        days = ["2022-01-03", "2022-01-04", "2022-01-05",
                "2024-01-03", "2024-01-04", "2024-01-05"]  # block1 x3, block2 x3
        values = rng.normal(size=(n, 2, t)).astype(np.float32)
        valid = np.ones((n, 2, t), dtype=bool)
        span = np.ones((n, t), dtype=bool)
        # a silence barrier in rows 0 and 3 (positions 4..5 unsupported)
        span[0, 4:6] = False
        span[3, 4:6] = False
        valid[0, :, 4:6] = False

        def write_archive(path, mode, *, include_span=True, fit_override=None,
                          plen_override=None, valid_override=None, span_override=None,
                          values_override=None, query_override=None, width_override=None):
            fit = (np.asarray([d <= "2023-12-31" for d in days], dtype=bool)
                   if fit_override is None else np.asarray(fit_override, dtype=bool))
            width = t if width_override is None else int(width_override)
            plen = (np.full(n, width, dtype=np.int32) if plen_override is None
                    else np.asarray(plen_override, dtype=np.int32))
            v_use = valid if valid_override is None else np.asarray(valid_override, dtype=bool)
            s_use = span if span_override is None else np.asarray(span_override, dtype=bool)
            val_use = (values if values_override is None
                       else np.asarray(values_override, dtype=np.float32))
            end_t = 570 + width - 1
            qt = (np.asarray(570, dtype=np.int32) + plen if mode == "causal"
                  else np.full(n, end_t, dtype=np.int32))
            if query_override is not None:
                qt = np.asarray(query_override, dtype=np.int32)
            arrs = {
                "values": val_use, "valid": v_use,
                "channels": np.asarray(["a", "b"], dtype="U4"),
                "episode_id": np.asarray([f"d{i}|T{i}@{t}" for i in range(n)], dtype="U32"),
                "day": np.asarray(days, dtype="U16"),
                "ticker": np.asarray([f"T{i}" for i in range(n)], dtype="U16"),
                "path_id": np.asarray([f"{days[i]}|T{i}" for i in range(n)], dtype="U64"),
                "block": np.asarray([block_of(d) for d in days], dtype="U16"),
                "start_t": np.full(n, 570, dtype=np.int32),
                "end_t": np.full(n, end_t, dtype=np.int32),
                "query_t": qt,
                "mode": np.asarray([mode] * n, dtype="U16"),
                "prefix_len": plen,
                "fit": fit,
                "manifest_sha256": np.asarray("0" * 64, dtype="U64"),
                "freeze_r_sha256": np.asarray(freeze_sha, dtype="U64"),
                "schema_sha256": np.asarray(sha256_file(SCHEMA_PATH), dtype="U64"),
            }
            if include_span:
                arrs["span_allowed"] = s_use
            deterministic_npz(path, arrs)
            write_json(path.with_suffix(".meta.json"), {
                "scale": t, "mode": mode,
                "layout": {"values": "float32 (N,C,T) 0.0 at valid==False",
                           "valid": "bool (N,C,T) computational pad",
                           "span_allowed": "bool (N,T) coordinate barrier map"},
                "causal_prefix_rule": CAUSAL_PREFIX_RULE if mode == "causal" else None,
            })

        # In-process synthetic record for the boundary tests only: a full-scope shape (the only
        # scope this consumer trains on) with small tensors. It is never written to real evidence.
        rec = {
            "matrix_version": "selftest-v1", "template_only": False, "frozen": True,
            "status": "FROZEN", "freeze": "R",
            "binding_slots": {
                "corpus": {"node_id": "observations.tape_atlas", "version": "selftest",
                           "manifest_sha256": "0" * 64, "day_count": 6, "built": True,
                           "scope": "full", "day_set_sha256": "1" * 64},
                "blind_report": {"node_id": "selftest", "path": "/tmp/blind.json",
                                 "sha256": "2" * 64, "complete": True,
                                 "permitted_summaries_only": True},
                "transform_training_blocks": {
                    "split_key": "block", "fit_blocks": ["block1"], "eval_blocks": ["block2"],
                    "no_overlapping_source_interval_crosses_split": True,
                    "normalization_and_model_params_fit_on_fit_blocks_only": True, "frozen": True},
                "scale_choice": {"scale_ladder": [t], "scale_unit": "minute",
                                 "scale_anchor": "path_first_valid", "anchor_channel": "a",
                                 "alignment": "left", "frozen": True},
                "integer_seed": 7,
                "numeric_one_shot_gates": {
                    "recurrence": 0.0, "mutual_neighbour_support": 0.0, "block_stability": 0.0,
                    "coordinate_leakage": 0.0, "family_leakage": 0.0,
                    "magnitude_preservation": 0.0, "sparse_exact_recall": 1.0,
                    "ssl_over_random_improvement": 0.0, "measured_runtime": 0.0},
            },
            "learned_family": {
                "family_count": 1, "architecture": ARCHITECTURE,
                "invalid_padded_positions_contribute_zero_loss": True,
                "inputs": [{"id": "a"}, {"id": "b"}],
                "modes": {
                    "causal": {"receptive_field": "left-only",
                               "garbage_suffix_invariance_required": True,
                               "prospective_live_use": True, "config": cfg},
                    "retrospective": {"labelled": True, "prospective_live_use": False,
                                      "out_of_window_junk_insensitive_required": True,
                                      "config": dict(cfg, mode="retrospective", labelled=True,
                                                     label="retrospective_selftest")},
                },
            },
        }
        rec_path = tmp / "freeze_r.json"
        write_json(rec_path, rec)
        freeze_sha = sha256_file(rec_path)
        ssl = tmp / "ssl"
        write_archive(ssl / f"{t}_causal.npz", "causal")
        write_archive(ssl / f"{t}_retrospective.npz", "retrospective")

        r = load_frozen_family(rec_path, mode="causal", fold="block")
        chk("frozen_record_bound", r["config"]["width"] == 4)
        loaded = load_archives(ssl, r=r, mode="causal", manifest=None,
                               schema_path=SCHEMA_PATH, verify=True)
        split = assign_split(loaded, r=r)
        chk("split_disjoint", split["fit_days"] == ["2022-01-03", "2022-01-04", "2022-01-05"])
        fit_rows = np.asarray([block_of(d) == "block1" for d in days])
        expected_per_channel = valid[fit_rows].sum(axis=(0, 2))
        norm = fit_normalization(loaded["archives"], fit_ids=split["fit_days"], mode="causal")
        chk("normalization_fit_only",
            all(s["n_fit"] == int(expected_per_channel[i])
                for i, s in enumerate(norm["stats"])),
            f"n_fit={expected_per_channel.tolist()}")
        chk("normalization_excludes_eval_cells",
            int(expected_per_channel.sum()) < int(valid.sum()))

        # -- barriers: no span crosses a span_allowed boundary -------------------------------
        arch = loaded["archives"][0]
        plan = episode_plan(arch, mask_span=cfg["mask_span"],
                            mask_fraction=0.9, seed=cfg["seed"], stream=0, epoch=0, mode="causal")
        ok = True
        for i in range(n):
            for lo, hi in plan[i]:
                if not span[i, lo:hi].all():
                    ok = False
        chk("no_mask_span_crosses_barrier", ok)
        chk("barrier_row_still_masked_elsewhere", len(plan[0]) >= 1 and len(plan[3]) >= 1)

        # -- padding / invalid cells contribute exactly zero loss ----------------------------
        model = make_model(torch, nn, fn, cfg, mode="causal", in_planes=4,
                           n_value_channels=2)
        hidden = np.zeros((n, t), dtype=bool)
        hidden[0, 0:2] = True
        hidden[0, 4:6] = True  # inside the silence barrier: must never enter the loss mask
        x, lm, tg = build_inputs(torch, arch, norm, planes=cfg["input_planes"], hidden=hidden,
                                 mode="causal")
        chk("loss_mask_never_covers_invalid", not bool(lm.numpy()[0, :, 4:6].any()))
        chk("loss_mask_covers_observed_masked", bool(lm.numpy()[0, :, 0:2].all()))
        with torch.no_grad():
            pred, _ = model(x)
        loss, count = masked_reconstruction_loss(pred, tg, lm)
        manual = float(((pred - tg) ** 2)[lm].mean()) if count else 0.0
        chk("loss_zero_outside_mask", abs(float(loss) - manual) < 1e-12, f"{float(loss)}")
        chk("masked_cells_counted", count == int(lm.sum()))

        # -- observed region is NOT the span barrier: a known zero / post-gap observation that
        #    sits outside every span stays a fully visible encoder input (the giant tail is kept)
        v2 = values.copy()
        v2[2, :, 4:6] = 0.0          # legitimate known zero
        span2 = span.copy()
        span2[2, 4:6] = False        # a silence barrier there: not maskable
        arc2 = dict(arch, values=v2, span_allowed=span2)
        zero_hid = np.zeros((n, t), dtype=bool)
        x2, lm2, _ = build_inputs(torch, arc2, norm, planes=cfg["input_planes"],
                                  hidden=zero_hid, mode="causal")
        obs2 = observed_mask(arc2, "causal")
        chk("observed_region_keeps_known_zero", bool(obs2[2, 4:6].all()))
        chk("known_zero_visible_to_encoder", bool(x2.numpy()[2, :, 4:6].any()))
        chk("known_zero_outside_span_not_maskable", bool(span2[2, 4:6].sum() == 0))
        plan2 = episode_plan(arc2, mask_span=cfg["mask_span"], mask_fraction=0.9,
                             seed=cfg["seed"], stream=0, epoch=0, mode="causal")
        bridged = any(not span2[2, lo:hi].all() for lo, hi in plan2[2])
        chk("no_span_bridges_the_gap", not bridged)
        chk("post_gap_tail_still_observed", bool(obs2[2, 6:].all()))

        # -- Main's boundary regression, pure functions: observed [T,T,F,T,T], query 5 ---------
        allowed5 = np.asarray([True, True, False, True, True])
        plan5 = plan_spans(allowed=allowed5, mask_span={"min_len": 1, "max_len": 2},
                           mask_fraction=1.0, rng=np.random.default_rng(0))
        chk("boundary_no_span_bridges_gap",
            all(bool(allowed5[lo:hi].all()) for lo, hi in plan5), f"{plan5}")
        chk("boundary_tail_is_maskable", any(lo >= 3 for lo, hi in plan5), f"{plan5}")
        v5 = np.zeros((1, 1, 5), dtype=bool)
        v5[0, 0, :] = [True, True, False, True, True]
        fake5 = {"valid": v5, "span_allowed": allowed5[None, :],
                 "meta": {"start_t": np.asarray([570]), "end_t": np.asarray([574]),
                          "prefix_len": np.asarray([5])}}
        obs5 = observed_mask(fake5, "causal")
        chk("boundary_encoder_keeps_the_last_two", bool(obs5[0, 3:].all()) and not bool(obs5[0, 2]))
        chk("boundary_gap_not_maskable", not bool(fake5["span_allowed"][0, 2]))
        seen5 = v5 & window_mask(fake5)[:, None, :]
        chk("boundary_loss_zero_at_gap",
            not bool((mask_matrix(1, 5, [plan5])[:, None, :] & seen5)[0, 0, 2]))

        # -- causal: values/valid BEYOND the ASOF cutoff must not influence normalization,
        #    the forward embedding input or the loss (the cutoff is applied independently of
        #    `valid`, never trusting the producer's truncation alone)
        fut_a = tmp / "ssl_future_a"
        fut_b = tmp / "ssl_future_b"
        v_f = valid.copy()
        v_f[:, :, 5:] = True            # "observed" past the cutoff
        s_f = span.copy()
        s_f[:, 5:] = True               # and maskable past it
        val_a = values.copy()
        val_a[:, :, 5:] = 3.0
        val_b = values.copy()
        val_b[:, :, 5:] = -7.0
        plen_f = np.full(n, 5, dtype=np.int32)
        write_archive(fut_a / f"{t}_causal.npz", "causal", plen_override=plen_f,
                      valid_override=v_f, span_override=s_f, values_override=val_a)
        write_archive(fut_b / f"{t}_causal.npz", "causal", plen_override=plen_f,
                      valid_override=v_f, span_override=s_f, values_override=val_b)
        aa = load_archives(fut_a, r=r, mode="causal", manifest=None, schema_path=SCHEMA_PATH,
                           verify=True)["archives"][0]
        ab = load_archives(fut_b, r=r, mode="causal", manifest=None, schema_path=SCHEMA_PATH,
                           verify=True)["archives"][0]
        chk("future_beyond_cutoff_not_seen", not bool(seen_mask(aa, "causal")[:, :, 5:].any()))
        chk("future_beyond_cutoff_not_observed",
            not bool(observed_mask(aa, "causal")[:, 5:].any()))
        chk("scope_relation_recorded", aa["scope"]["asof_cut_applied_independently"] is True)
        na = fit_normalization([aa], fit_ids=split["fit_days"], mode="causal")
        nb = fit_normalization([ab], fit_ids=split["fit_days"], mode="causal")
        chk("future_junk_does_not_change_normalization", na["stats"] == nb["stats"])
        zero_n = np.zeros((n, t), dtype=bool)
        xa, lma, _ = build_inputs(torch, aa, na, planes=cfg["input_planes"], hidden=zero_n,
                                  mode="causal")
        xb, lmb, _ = build_inputs(torch, ab, nb, planes=cfg["input_planes"], hidden=zero_n,
                                  mode="causal")
        chk("future_junk_does_not_change_forward_input", torch.equal(xa, xb))
        chk("future_junk_never_in_loss_mask", not bool(lma[:, :, 5:].any()))
        plan_f = episode_plan(aa, mask_span=cfg["mask_span"], mask_fraction=0.9,
                              seed=cfg["seed"], stream=0, epoch=0, mode="causal")
        chk("no_span_beyond_the_cutoff", all(hi <= 5 for spans in plan_f for lo, hi in spans))

        # -- producer clock relations are asserted, not trusted ------------------------------
        bad_qt = tmp / "ssl_bad_qt"
        write_archive(bad_qt / f"{t}_causal.npz", "causal",
                      query_override=np.full(n, 570 + t - 1, dtype=np.int32))
        refused = False
        try:
            load_archives(bad_qt, r=r, mode="causal", manifest=None, schema_path=SCHEMA_PATH,
                          verify=True)
        except IntegrityRefusalError as exc:
            refused = any(p["kind"] == "causal_query_t_relation" for p in exc.problems)
        chk("causal_query_t_relation_asserted", refused)

        bad_plen = tmp / "ssl_bad_plen"
        write_archive(bad_plen / f"{t}_retrospective.npz", "retrospective",
                      plen_override=np.full(n, t - 1, dtype=np.int32))
        refused = False
        try:
            load_archives(bad_plen, r=r, mode="retrospective", manifest=None,
                          schema_path=SCHEMA_PATH, verify=True)
        except IntegrityRefusalError as exc:
            refused = any(p["kind"] == "retrospective_prefix_len_relation"
                          for p in exc.problems)
        chk("retrospective_prefix_len_relation_asserted", refused)

        # -- span_allowed is never a target -------------------------------------------------
        chk("span_allowed_not_a_target",
            not any("span" in c for c in r["input_ids"]) and
            all(("span" not in p) for p in cfg["input_planes"]))

        # -- causal garbage-suffix invariance (real input assembly: value + validity planes) ----
        base_x, _, _ = build_inputs(torch, arch, norm, planes=cfg["input_planes"],
                                    hidden=np.zeros((n, t), dtype=bool), mode="causal")
        clean = base_x[:1]
        garbage = torch.cat(
            [clean, torch.from_numpy(rng.normal(size=(1, clean.shape[1], 5)).astype(np.float32))],
            dim=2,
        )
        with torch.no_grad():
            e_clean = model.encode(clean).numpy()[0]
            e_garb = model.encode(garbage).numpy()[0][:, :t]
        chk("causal_garbage_suffix_invariance", np.allclose(e_clean, e_garb, atol=1e-6))

        # -- out-of-scope junk must be sanitized identically, whatever its values ---------------
        # (a) cells the archive declares UNOBSERVED inside the window: the two archives carry the
        #     SAME observation mask and differ only in the junk written into those unobserved cells.
        rmodel = make_model(torch, nn, fn,
                            rec["learned_family"]["modes"]["retrospective"]["config"],
                            mode="retrospective", in_planes=4, n_value_channels=2)
        va = valid.copy()
        va[0, :, 6:8] = False
        vj = values.copy()
        vj[0, :, 6:8] = 99.0
        vk = values.copy()
        vk[0, :, 6:8] = -42.0
        arc_junk = dict(arch, values=vj, valid=va.copy())
        arc_clean = dict(arch, values=vk, valid=va.copy())
        zero_hidden = np.zeros((n, t), dtype=bool)
        xj, _, _ = build_inputs(torch, arc_junk, norm, planes=cfg["input_planes"],
                                hidden=zero_hidden, mode="retrospective")
        xc, _, _ = build_inputs(torch, arc_clean, norm, planes=cfg["input_planes"],
                                hidden=zero_hidden, mode="retrospective")
        chk("unobserved_cell_junk_sanitized", torch.equal(xj, xc))
        nj = fit_normalization([arc_junk], fit_ids=split["fit_days"], mode="retrospective")
        nk = fit_normalization([arc_clean], fit_ids=split["fit_days"], mode="retrospective")
        chk("unobserved_cell_junk_normalization_identical", nj["stats"] == nk["stats"])
        with torch.no_grad():
            e_j = rmodel.encode(xj).numpy()
            e_c = rmodel.encode(xc).numpy()
        chk("retro_unobserved_cell_junk_insensitive", np.array_equal(e_j, e_c))
        with torch.no_grad():
            e_cj = model.encode(xj).numpy()
            e_cc = model.encode(xc).numpy()
        chk("causal_unobserved_cell_junk_insensitive", np.array_equal(e_cj, e_cc))

        # (b) retrospective: junk stored OUTSIDE the declared window (beyond end_t) is out of scope
        #     and must not reach normalization, the forward input or the loss.
        w_win = 10
        vw = valid.copy()
        vw[:, :, w_win:] = False
        sw = span.copy()
        sw[:, w_win:] = False
        vj_w = values.copy()
        vj_w[0, :, w_win:] = 123.0
        vk_w = values.copy()
        vk_w[0, :, w_win:] = -456.0
        win_a = tmp / "ssl_win_a"
        win_b = tmp / "ssl_win_b"
        for d_, vals_ in ((win_a, vj_w), (win_b, vk_w)):
            write_archive(d_ / f"{t}_retrospective.npz", "retrospective", width_override=w_win,
                          plen_override=np.full(n, w_win, dtype=np.int32), valid_override=vw,
                          span_override=sw, values_override=vals_)
        ra = load_archives(win_a, r=r, mode="retrospective", manifest=None,
                           schema_path=SCHEMA_PATH, verify=True)["archives"][0]
        rb = load_archives(win_b, r=r, mode="retrospective", manifest=None,
                           schema_path=SCHEMA_PATH, verify=True)["archives"][0]
        chk("beyond_window_not_seen",
            not bool(seen_mask(ra, "retrospective")[:, :, w_win:].any()))
        chk("beyond_window_not_observed",
            not bool(observed_mask(ra, "retrospective")[:, w_win:].any()))
        na_w = fit_normalization([ra], fit_ids=split["fit_days"], mode="retrospective")
        nb_w = fit_normalization([rb], fit_ids=split["fit_days"], mode="retrospective")
        chk("beyond_window_junk_normalization_identical", na_w["stats"] == nb_w["stats"])
        xa_w, lma_w, _ = build_inputs(torch, ra, na_w, planes=cfg["input_planes"],
                                      hidden=zero_hidden, mode="retrospective")
        xb_w, _, _ = build_inputs(torch, rb, nb_w, planes=cfg["input_planes"],
                                  hidden=zero_hidden, mode="retrospective")
        chk("beyond_window_junk_forward_identical", torch.equal(xa_w, xb_w))
        chk("beyond_window_never_in_loss_mask", not bool(lma_w[:, :, w_win:].any()))
        with torch.no_grad():
            e_wa = rmodel.encode(xa_w).numpy()
            e_wb = rmodel.encode(xb_w).numpy()
        chk("retro_beyond_window_junk_insensitive", np.array_equal(e_wa, e_wb))

        # -- train/eval leakage refusal ------------------------------------------------------
        bad = dict(rec)
        bad_tb = dict(rec["binding_slots"]["transform_training_blocks"])
        bad_tb["fit_blocks"] = ["block1", "block2"]
        bad["binding_slots"] = dict(rec["binding_slots"],
                                    transform_training_blocks=bad_tb)
        bad_path = tmp / "freeze_r_overlap.json"
        write_json(bad_path, bad)
        refused = False
        try:
            load_frozen_family(bad_path, mode="causal", fold="block")
        except ContractRefusalError:
            refused = True
        chk("overlapping_fit_eval_blocks_refused", refused)

        # a row whose fit flag crosses the frozen split must be refused, not pooled
        bad_split = tmp / "ssl_badfit"
        write_archive(bad_split / f"{t}_causal.npz", "causal",
                      fit_override=np.zeros(n, dtype=bool))
        loaded_bad = load_archives(bad_split, r=r, mode="causal", manifest=None,
                                   schema_path=SCHEMA_PATH, verify=True)
        refused = False
        try:
            assign_split(loaded_bad, r=r)
        except IntegrityRefusalError:
            refused = True
        chk("fit_flag_split_crossing_refused", refused)

        # a null (UNBOUND) numeric parameter must refuse rather than take a default
        null_cfg = dict(cfg, width=None)
        refused = False
        try:
            validate_config(null_cfg, mode="causal", fold="block", seed=7, where="selftest")
        except ContractRefusalError:
            refused = True
        chk("unbound_parameter_refused", refused)

        # -- frozen-record fail-closed rules -------------------------------------------------
        def mutated_record(key, **changes):
            rec2 = json.loads(json.dumps(rec))
            for path_keys, value in changes.items():
                node = rec2
                parts = path_keys.split(".")
                for part in parts[:-1]:
                    node = node[part]
                node[parts[-1]] = value
            p = tmp / f"freeze_r_{key}.json"
            write_json(p, rec2)
            return p

        cases = [
            ("canary_scope_refused", {"binding_slots.corpus.scope": "canary"}, "causal"),
            ("unknown_scope_refused", {"binding_slots.corpus.scope": "whatever"}, "causal"),
            ("missing_gate_refused",
             {"binding_slots.numeric_one_shot_gates": {"recurrence": 1.0}}, "causal"),
            ("null_gate_refused",
             {"binding_slots.numeric_one_shot_gates": {
                 "recurrence": 1.0, "mutual_neighbour_support": 1.0, "block_stability": 1.0,
                 "coordinate_leakage": 1.0, "family_leakage": 1.0,
                 "magnitude_preservation": 1.0, "sparse_exact_recall": 1.0,
                 "ssl_over_random_improvement": None, "measured_runtime": 1.0}}, "causal"),
            ("non_numeric_ssl_gate_refused",
             {"binding_slots.numeric_one_shot_gates": {
                 "recurrence": 1.0, "mutual_neighbour_support": 1.0, "block_stability": 1.0,
                 "coordinate_leakage": 1.0, "family_leakage": 1.0,
                 "magnitude_preservation": 1.0, "sparse_exact_recall": 1.0,
                 "ssl_over_random_improvement": "TBD", "measured_runtime": 1.0}}, "causal"),
            ("empty_inputs_refused", {"learned_family.inputs": []}, "causal"),
            ("causal_not_left_only_refused",
             {"learned_family.modes.causal.receptive_field": "symmetric"}, "causal"),
            ("retrospective_live_use_refused",
             {"learned_family.modes.retrospective.prospective_live_use": True}, "retrospective"),
        ]
        for key, changes, case_mode in cases:
            p = mutated_record(key, **changes)
            refused = False
            try:
                load_frozen_family(p, mode=case_mode, fold="block")
            except ContractRefusalError:
                refused = True
            chk(f"{key}", refused)

        # a missing producer sidecar is an integrity refusal (layout/prefix context is required)
        no_side = tmp / "ssl_noside"
        write_archive(no_side / f"{t}_causal.npz", "causal")
        (no_side / f"{t}_causal.meta.json").unlink()
        refused = False
        try:
            load_archives(no_side, r=r, mode="causal", manifest=None, schema_path=SCHEMA_PATH,
                          verify=True)
        except IntegrityRefusalError:
            refused = True
        chk("archive_sidecar_required", refused)

        no_span = tmp / "ssl_nospan"
        write_archive(no_span / f"{t}_causal.npz", "causal", include_span=False)
        refused = False
        try:
            load_archives(no_span, r=r, mode="causal", manifest=None, schema_path=SCHEMA_PATH,
                          verify=True)
        except IntegrityRefusalError:
            refused = True
        chk("missing_span_allowed_refused", refused)

        # -- one run per fold: a rerun raises the refusal (the CLI maps it to exit 2), exact
        #    resume is a no-op ------------------------------------------------------------------
        out = tmp / "out"
        ev = tmp / "ev"
        rc = train_and_publish(r, r["config"], loaded, split, mode="causal", tier="selected_paths",
                               out=out, out_evidence=ev, resume=False,
                               manifest=None, schema_path=SCHEMA_PATH,
                               code_sha=sha256_file(Path(__file__).resolve()))
        chk("train_run_completes", rc == 0)
        emb_path = out / "runs" / "causal" / "block" / "embeddings.npz"
        side = Path(str(emb_path) + ".meta.json")
        chk("embedding_sidecar_present", emb_path.is_file() and side.is_file())
        if emb_path.is_file():
            with np.load(emb_path, allow_pickle=False) as z:
                chk("embedding_layout", set(z.files) >= {"values", "valid", "object_ids"}
                    and z["values"].shape == z["valid"].shape and z["values"].ndim == 3)
                chk("embedding_object_ids",
                    len(set(map(str, z["object_ids"]))) == z["values"].shape[0])
            prov = load_json(side)["provenance"]
            chk("embedding_provenance_split",
                set(prov["fit_days"]) & set(prov["eval_days"]) == set())
        refused_msg = None
        try:
            train_and_publish(r, r["config"], loaded, split, mode="causal", tier="selected_paths",
                              out=out, out_evidence=ev, resume=False,
                              manifest=None, schema_path=SCHEMA_PATH,
                              code_sha=sha256_file(Path(__file__).resolve()))
        except ContractRefusalError as exc:
            refused_msg = str(exc)
        chk("rerun_of_claimed_fold_refused",
            refused_msg is not None and "already complete" in refused_msg, refused_msg)
        rc = train_and_publish(r, r["config"], loaded, split, mode="causal", tier="selected_paths",
                               out=out, out_evidence=ev, resume=True,
                               manifest=None, schema_path=SCHEMA_PATH,
                               code_sha=sha256_file(Path(__file__).resolve()))
        chk("exact_resume_is_noop", rc == 0)

        # -- a second configuration on the same fold is refused, not silently retrained -------
        # built from the same COMPLETE raw config skeleton as the first run (the validated config
        # in r["config"] is normalized and no longer carries family/mode/objective)
        cfg2 = dict(cfg, config_version="selftest-v2")
        r2 = dict(r, config=validate_config(cfg2, mode="causal", fold="block", seed=7,
                                           where="selftest"))
        refused_msg = None
        try:
            train_and_publish(r2, r2["config"], loaded, split, mode="causal",
                              tier="selected_paths", out=out,
                              out_evidence=ev, resume=False, manifest=None,
                              schema_path=SCHEMA_PATH,
                              code_sha=sha256_file(Path(__file__).resolve()))
        except ContractRefusalError as exc:
            refused_msg = str(exc)
        chk("second_config_same_fold_refused",
            refused_msg is not None and "different configuration" in refused_msg, refused_msg)

        # -- the CLI maps a contract refusal to exit 2 (nothing written) ----------------------
        cli_rec = mutated_record("cli_canary", **{"binding_slots.corpus.scope": "canary"})
        cli_rc = main(["--freeze-r", str(cli_rec), "--archive-dir", str(ssl), "--mode", "causal",
                       "--tier", "selected_paths", "--out", str(tmp / "out_cli"),
                       "--out-evidence", str(tmp / "ev_cli")])
        chk("cli_contract_refusal_exit_2", cli_rc == 2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"sequence selftest: {len(fails)} failure(s)" + (f" {fails}" if fails else ""))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
