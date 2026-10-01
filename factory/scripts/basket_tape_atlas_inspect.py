#!/usr/bin/env python
"""Freeze-O blind corpus inspection (prepared; run only after the full corpus passes).

Reads ONLY the frozen positive allowlist declared in
``factory/artifacts/basket/phase2/ATLAS/TAPE/inspection_contract.json``, validated at load
against Freeze-O ``schema.json`` (declared fields/flags/dtypes) and ``causal_registry.json``
(deny prefixes/exact columns, default deny). No outcome, censor, future, ticket-constant,
provenance or whole-day-quality column is ever named, projected or aggregated; a denied name
in a payload's physical schema refuses the run.

Determinism and evidence
------------------------
  * rows are scanned one day-partition at a time, never concatenated across days;
  * every declared payload is resolved from ``manifest.payload_sha256`` (no globbing for
    reads), must stay inside ``--root``, and its day must pass ``basket_sim.guard_day`` and be
    inside the manifest day set; payload sha256 + row count are re-verified;
  * counts, masks, coordinate tallies, path shapes, rarity counts and rank-change counts are
    EXACT over all rows and accumulate as plain numbers;
  * numeric distributions (and the rank-change quantiles) come from a bounded deterministic
    hash subsample (per-row Bernoulli at rate reservoir_rows / DECLARED layer rows) whose
    sampling error is reported;
  * no timestamp and no run-varying measurement enters the report, so identical inputs give
    identical bytes.

Bounded memory and chunking
---------------------------
The scan is O(1) in the number of days for every structure that would otherwise grow with the
corpus: exact counters are numbers, identity/rarity/coordinate tallies are ``Counter``s over
bounded key domains, and the only per-row values retained are the ``reservoir_rows`` kept by
the deterministic subsample.  Rank-change diffs are never accumulated (that is what blew the
old implementation past 14 GiB): the exact positive/zero/negative counts are streamed and the
quantiles are read off the bounded subsample.  Group-by/value-count working sets are sliced per
payload (``SCAN_SLICE``), so peak memory is O(largest payload) -- not O(corpus) and not O(days).
A run may cover every manifest day at once (hard-capped by the reservoir) or a disjoint slice of
days:

  * ``--days A,B,C --out-report CHUNK.json`` scans only those day partitions (the shared,
    non-day-partitioned payloads are still read whole and are identical in every chunk) and
    writes a self-describing, self-hashed *chunk* report;
  * ``--merge CHUNK1.json CHUNK2.json ...`` verifies that the chunk day sets are disjoint and
    their union is exactly the manifest day set, that each chunk's per-payload integrity checks
    passed, that the day-layer payload coverage is a partition of the manifest payloads, that
    the shared-layer sections are identical, and that every chunk used the identical subsample
    rate; the five corpus-level aggregates are verified by every chunk and counted ONCE.  It then
    emits the final report, whose ``chunk_provenance`` block records each chunk's path, sha256
    and day runs.

The subsample rate is computed from the manifest's DECLARED total rows for the layer (never the
chunk's rows), so the union of kept rows -- and therefore every reported quantile -- is
identical for any partition of the manifest days.

Usage
-----
  .venv/bin/python factory/scripts/basket_tape_atlas_inspect.py --selftest
  # one whole-corpus pass (fully bounded now; no chunking required)
  .venv/bin/python factory/scripts/basket_tape_atlas_inspect.py \
      --root /home/hillel/projects/Alpacatrader/data/atlas/observation/v0 \
      --manifest factory/artifacts/basket/phase2/ATLAS/TAPE/OBSERVATION/v0/manifest.json \
      --out-evidence factory/artifacts/basket/phase2/ATLAS/TAPE/OBSERVATION/v0
  # chunked: one chunk per day slice, then merge
  .venv/bin/python factory/scripts/basket_tape_atlas_inspect.py --days 2021-02-01,...,2021-03-31 \
      --out-report /tmp/chunk-000.json
  .venv/bin/python factory/scripts/basket_tape_atlas_inspect.py --merge /tmp/chunk-*.json \
      --out-evidence factory/artifacts/basket/phase2/ATLAS/TAPE/OBSERVATION/v0

Exit codes
----------
  0  corpus inspected / merged, no integrity refusal
  2  contract refusal (missing --manifest, inspection contract not frozen, illegal allowlist,
     evidence-hash mismatch, denied physical column, sealed/reserved day, --days not a manifest
     subset, or a merge whose chunk set is not a valid partition): nothing is read and no
     report is written
  3  integrity refusal (declared payload absent, sha256 mismatch, rows != manifest, parent
     drift, day set != dev_days): the report IS written with status REFUSED and every problem
  1  selftest failure

``--days`` with ``--out-report`` writes one chunk report (exit 0/3); ``--merge`` consumes chunk
reports and writes the final report (exit 0/3, or 2 when the chunk set is not a valid
partition).  ``--no-verify-payload-sha`` skips only the payload sha256 re-read (the row counts,
the day guard, the parents and the manifest core hash are always checked).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
TAPE = ROOT / "factory/artifacts/basket/phase2/ATLAS/TAPE"
OBS_EVIDENCE = TAPE / "OBSERVATION/v0"
CONTRACT_PATH = TAPE / "inspection_contract.json"
DEFAULT_ROOT = Path("/home/hillel/projects/Alpacatrader/data/atlas/observation/v0")
REPORT_NAME = "blind_corpus_report.json"
Z95 = 1.959963984540054
BLOCK1_MAX_DAY = "2023-12-31"  # contract.json day_guard.block_gap_rule
# contract_lock.json convention; PLAN sections 2/8: the allowlist is frozen before the blind run
FROZEN_CONTRACT_STATES = ("frozen",)
try:  # canonical sealed/reserved-day guard (contract.json: day_guard)
    from factory.scripts.basket_sim import guard_day
except ImportError:  # direct script execution
    sys.path.insert(0, str(ROOT))
    from factory.scripts.basket_sim import guard_day


class ContractRefusalError(RuntimeError):
    """The allowlist/evidence chain is illegal: refuse before reading any row."""


# ------------------------------------------------ small helpers ------------ #
def sha256_file(path: Path, chunk: int = 1 << 22) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path: Path):
    return json.loads(Path(path).read_text())


def canon_bytes(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def num(x):
    v = float(x)
    return float(f"{v:.10g}") if np.isfinite(v) else None


def block_of(day: str) -> str:
    return "block1" if day <= BLOCK1_MAX_DAY else "block2"


def mix64(x: np.ndarray) -> np.ndarray:  # splitmix64; deterministic, no RNG state
    y = x.astype(np.uint64, copy=True)
    y ^= y >> np.uint64(30)
    y *= np.uint64(0xBF58476D1CE4E5B9)
    y ^= y >> np.uint64(27)
    y *= np.uint64(0x94D049BB133111EB)
    y ^= y >> np.uint64(31)
    return y


def salt_of(*parts) -> np.uint64:
    raw = "|".join(str(p) for p in parts).encode()
    return np.uint64(int.from_bytes(hashlib.blake2b(raw, digest_size=8).digest(), "little"))


@lru_cache(maxsize=None)
def _resolve_cached(path_str: str) -> Path:
    """Memoised ``Path.resolve``.

    ``Path.resolve`` is ~20 ms on this WSL mount and the corpus has ~6.4k payloads, so each
    unique *directory* is resolved once instead of once per payload; without this the manifest
    index alone costs minutes.
    """
    return Path(path_str).resolve()


def resolve_under(root: Path, rel: str) -> Path:
    r = Path(rel)
    if r.is_absolute() or ".." in r.parts:
        raise ContractRefusalError(f"payload path must be relative and traversal-free: {rel!r}")
    root_abs = _resolve_cached(str(root))
    # resolve the containing directory (a bounded set: one per layer-month) and require it to
    # stay inside --root; a traversal-free name cannot escape its own directory.  The leaf is
    # not separately resolved (that is the expensive part); it is still read only through this
    # path and must match the manifest sha256/rows.
    parent_abs = _resolve_cached(str(root_abs / r.parent))
    if parent_abs != root_abs and root_abs not in parent_abs.parents:
        raise ContractRefusalError(f"payload path escapes --root: {rel!r}")
    return parent_abs / r.name


# ------------------------------------ positive allowlist validation -------- #
READ_ROLES = (
    "keys",
    "coordinates",
    "masks",
    "magnitude",
    "activity",
    "staleness",
    "rank",
    "counts",
)
NUM_ROLES = ("magnitude", "activity", "staleness", "rank", "counts")
# group-by / value-count working set per payload is bounded to this many rows; a 4M+ row
# payload is aggregated in slices so the transient aggregation memory does not scale with the
# payload (the tallies themselves are exact and are combined across slices).
SCAN_SLICE = 1 << 19


def deny_vocabulary(registry: dict) -> tuple[tuple[str, ...], set[str]]:
    exact = set(registry.get("denied_exact_columns") or ())
    for fam in registry.get("blocked_families") or ():
        exact.update(fam.get("examples") or ())
    return tuple(registry.get("denied_prefixes") or ()), exact


def denied_reason(name: str, prefixes, exact) -> str | None:
    if name in exact:
        return "causal_registry.denied_exact_columns/blocked_families"
    hit = [p for p in prefixes if name.startswith(p)]
    return f"causal_registry.denied_prefixes:{hit[0]}" if hit else None


def physical_deny(registry: dict) -> tuple[tuple[str, ...], set[str]]:
    """Names that must be physically ABSENT from an observation payload (producer rule).

    Only the panel outcome/future registries and their prefixes qualify; the
    ``raw_whole_day_roster`` family (hi/lo/c_last/vol/n_bars/first_et/last_et/delayed_open) is a
    prospective-use prohibition carried by declared retrospective_only audit columns, and the
    ``quote_`` prefix is roster bookkeeping (the core corpus keeps no quote VALUE column).
    """
    derived = registry.get("derived_from_panel_registry") or {}
    exact = (
        set(registry.get("denied_exact_columns") or ())
        | set(derived.get("outcome_columns") or ())
        | set(derived.get("future_only_columns") or ())
    )
    return tuple(p for p in (registry.get("denied_prefixes") or ()) if p != "quote_"), exact


def validate_allowlists(schema: dict, registry: dict, insp: dict) -> None:
    prefixes, exact = deny_vocabulary(registry)
    ns_all = set(insp["enforce"]["deny_namespace_all"])
    ns_num = set(insp["enforce"]["deny_namespace_numeric"])
    dtypes = set(insp["enforce"]["numeric_dtypes"])
    problems: list[str] = []
    for lid, spec in insp["layers"].items():
        fields = (schema["tables"].get(spec["table"]) or {}).get("fields")
        if not fields:
            problems.append(f"{lid}: table {spec['table']!r} is not declared in schema.json")
            continue
        for role in READ_ROLES:
            for name in spec.get(role) or []:
                fa = fields.get(name)
                if fa is None:
                    problems.append(f"{lid}: column {name!r} is not declared in schema.json")
                    continue
                if why := denied_reason(name, prefixes, exact):
                    problems.append(f"{lid}: column {name!r} is denied ({why})")
                if fa["namespace"] in ns_all:
                    problems.append(f"{lid}: column {name!r} namespace {fa['namespace']} is denied")
                if role == "keys":
                    continue
                if role in NUM_ROLES and (
                    fa["namespace"] in ns_num or fa["coordinate_only"] or fa["dtype"] not in dtypes
                ):
                    problems.append(f"{lid}.{role}: {name!r} is not a readable numeric column")
                elif fa["retrospective_only"]:
                    problems.append(f"{lid}: column {name!r} is retrospective_only")
                elif not fa["prospective_allowed"]:
                    problems.append(f"{lid}: column {name!r} is not prospective_allowed")
        for d in spec.get("derived") or []:
            for src in d["inputs"]:
                fa = fields.get(src)
                if not fa or fa["namespace"] in ns_num or fa["retrospective_only"]:
                    problems.append(f"{lid}.derived:{d['name']}: input {src!r} is excluded")
    if problems:
        raise ContractRefusalError("allowlist refusal: " + json.dumps(problems, indent=1))


def unobservable(fields: dict, spec: dict, present: list[str], derived_strata=()) -> list[dict]:
    read = {c for r in READ_ROLES for c in spec.get(r) or []}
    read |= {c for d in spec.get("derived") or [] for c in d["inputs"]}
    read |= {c for c in derived_strata if c in fields}  # block/month/feed_era come from the day map

    def why(fa):
        return (
            "provenance namespace"
            if fa["namespace"] == "provenance"
            else "censor namespace"
            if fa["namespace"] == "censor"
            else "retrospective_only (whole-day/future-derived)"
            if fa["retrospective_only"]
            else "whole-day quality/audit column"
            if fa["namespace"] == "quality"
            else "prospective_allowed=false"
            if not fa["prospective_allowed"]
            else "coordinate_only and not used as a stratum here"
            if fa["coordinate_only"]
            else "not on the inspection allowlist"
        )

    out = [
        {"column": c, "reason": why(fa), "supportable_by_tier": fa["supportable_by_tier"]}
        for c, fa in sorted(fields.items())
        if c not in read
    ]
    out += [
        {
            "column": c,
            "reason": "allowlisted but absent from the payload physical schema",
            "supportable_by_tier": fields[c]["supportable_by_tier"] if c in fields else None,
        }
        for c in sorted(c for c in read if c not in present)
    ]
    out += [
        {
            "column": c,
            "reason": f"tier {spec['tier']} not in supportable_by_tier",
            "supportable_by_tier": fa["supportable_by_tier"],
        }
        for c, fa in sorted(fields.items())
        if c in read and spec["tier"] not in fa["supportable_by_tier"]
    ]
    return out


# ------------------------ deterministic bounded reservoir (chunkable) ------ #
def pack_f64(arr: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(arr, dtype="<f8").tobytes()).decode()


def pack_u64(arr: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(arr, dtype="<u8").tobytes()).decode()


def unpack_f64(s: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(s), dtype="<f8")


def unpack_u64(s: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(s), dtype="<u8")


class Reservoir:
    """Bounded deterministic subsample that survives a chunk partition.

    A row is kept iff ``mix64(file_salt ^ row_index ^ layer_salt) < thr`` with
    ``thr = reservoir_rows / declared_layer_rows`` and ``declared_layer_rows`` the layer's
    DECLARED total row count read from the manifest -- never the chunk's row count.  The rate
    is therefore identical in every chunk and each row's inclusion is independent of how the
    manifest days are split, so the UNION of kept rows is the same set for any partition.

    A chunk emits *every* kept row (untrimmed) with its deterministic ``(__h, __psalt, __idx)``
    sort key; the merge concatenates the chunk samples and keeps the global ``reservoir_rows``
    smallest keys.  Quantiles from the merged sample are therefore partition independent.  The
    retained state is O(reservoir_rows) per layer and O(one payload) transiently -- it never
    grows with the number of days scanned.
    """

    def __init__(self, capacity: int, declared_rows: int, salt: np.uint64, cols: list[str]):
        self.cap, self.salt, self.cols = int(capacity), salt, list(cols)
        self.declared = int(declared_rows)
        self.frac = 1.0 if self.declared <= 0 else min(1.0, self.cap / self.declared)
        self.thr = np.uint64((1 << 64) - 1 if self.frac >= 1.0 else int(self.frac * 2**64))
        self.frames: list[pl.DataFrame] = []
        self.extra_names: set[str] = set()
        self.seen = 0
        self.kept = 0

    def add(self, part_salt: np.uint64, df: pl.DataFrame, extra: dict | None = None) -> None:
        """Keep this payload's sampled rows plus any ``extra`` aligned arrays (e.g. the
        rank-change diff); ``extra`` is sliced by the same keep mask so no full-frame copy of
        the payload is ever made."""
        extra = extra or {}
        if not self.cols or df.height == 0:
            return
        idx = np.arange(df.height, dtype=np.uint64)
        h = mix64(idx ^ part_salt ^ self.salt)
        self.seen += df.height
        keep = h < self.thr
        k = int(keep.sum())
        if k == 0:
            return
        self.kept += k
        full = k == df.height
        sub = df if full else df.filter(pl.Series(keep))
        sel = sub.select(self.cols)
        for name, arr in extra.items():
            self.extra_names.add(name)
            sel = sel.with_columns(pl.Series(name, arr if full else arr[keep]))
        self.frames.append(
            sel.with_columns(
                pl.Series("__h", h[keep]),
                pl.Series("__psalt", np.full(k, part_salt, dtype=np.uint64)),
                pl.Series("__idx", idx[keep]),
            )
        )

    def arrays(self) -> dict | None:
        """Every kept row as arrays (never trimmed: the merge takes the global top-cap)."""
        if not self.frames:
            return None
        names = self.cols + sorted(self.extra_names)
        return {
            "__h": np.concatenate([f["__h"].to_numpy() for f in self.frames]),
            "__psalt": np.concatenate([f["__psalt"].to_numpy() for f in self.frames]),
            "__idx": np.concatenate([f["__idx"].to_numpy() for f in self.frames]),
            "cols": {
                c: np.concatenate([f[c].cast(pl.Float64).to_numpy() for f in self.frames])
                for c in names
            },
        }


def pack_sample(arrs: dict | None) -> dict | None:
    if arrs is None:
        return None
    return {
        "n_rows": int(arrs["__h"].size),
        "h": pack_u64(arrs["__h"]),
        "psalt": pack_u64(arrs["__psalt"]),
        "idx": pack_u64(arrs["__idx"]),
        "cols": {c: pack_f64(v) for c, v in arrs["cols"].items()},
    }


def unpack_sample(packed: dict | None) -> dict | None:
    if packed is None:
        return None
    return {
        "__h": unpack_u64(packed["h"]),
        "__psalt": unpack_u64(packed["psalt"]),
        "__idx": unpack_u64(packed["idx"]),
        "cols": {c: unpack_f64(v) for c, v in packed["cols"].items()},
    }


def concat_samples(samples: list[dict]) -> dict | None:
    parts = [s for s in samples if s is not None and s["__h"].size]
    if not parts:
        return None
    cols: dict[str, list[np.ndarray]] = defaultdict(list)
    for s in parts:
        for c, v in s["cols"].items():
            cols[c].append(v)
    return {
        "__h": np.concatenate([s["__h"] for s in parts]),
        "__psalt": np.concatenate([s["__psalt"] for s in parts]),
        "__idx": np.concatenate([s["__idx"] for s in parts]),
        "cols": {c: np.concatenate(v) for c, v in cols.items()},
    }


def cap_sample(sample: dict | None, cap: int) -> dict | None:
    """Deterministic global top-``cap`` by (__h, __psalt, __idx): partition independent."""
    if sample is None or sample["__h"].size == 0:
        return None
    order = np.lexsort((sample["__idx"], sample["__psalt"], sample["__h"]))
    if order.size > cap:
        order = order[:cap]
    return {
        "__h": sample["__h"][order],
        "__psalt": sample["__psalt"][order],
        "__idx": sample["__idx"][order],
        "cols": {c: v[order] for c, v in sample["cols"].items()},
    }


def quantiles(values: np.ndarray, probs: list[float], ci: float) -> dict:
    x = values[np.isfinite(values.astype(float))] if values.size else values
    if x.size == 0:
        return {"n": 0, "values": None, "ci_low": None, "ci_high": None}
    p = np.asarray(probs)
    se = np.sqrt(p * (1.0 - p) / x.size)
    z = Z95 if abs(ci - 0.95) < 1e-9 else float(np.sqrt(2.0) * 1.5)
    return {
        "n": int(x.size),
        "probabilities": [num(v) for v in p],
        "values": [num(v) for v in np.quantile(x, p)],
        "ci_low": [num(v) for v in np.quantile(x, np.clip(p - z * se, 0, 1))],
        "ci_high": [num(v) for v in np.quantile(x, np.clip(p + z * se, 0, 1))],
        "sampling_se": [num(v) for v in se],
    }


DERIVED = {
    "mul": lambda c: pl.col(c[0]) * pl.col(c[1]),
    "div_minus_1": lambda c: pl.col(c[0]) / pl.col(c[1]) - 1.0,
    "range_pct": lambda c: (pl.col(c[0]) - pl.col(c[1])) / pl.col(c[2]),
    "list_len": lambda c: pl.col(c[0]).list.len(),
}


def rank_change_diff(df: pl.DataFrame, rc: dict) -> np.ndarray:
    """Per-row consecutive-``time`` change of ``column`` within ``key``, aligned to ``df``.

    ``diff().over(key)`` depends on row order, so it is computed on a narrow sorted projection
    that carries the original row index and is scattered back; the first row of each key has no
    predecessor (NaN).  Exactly one such array per payload is materialised and then dropped --
    the values are never accumulated across days (that accumulation is what blew the old
    implementation past 14 GiB): the exact counts are streamed and the quantiles are read off
    the bounded reservoir.
    """
    proj = df.select(
        pl.int_range(pl.len(), dtype=pl.UInt32).alias("__i"),
        pl.col(rc["key"]),
        pl.col(rc["time"]),
        pl.col(rc["column"]),
    ).sort([rc["key"], rc["time"]])
    d = proj.select(pl.col(rc["column"]).diff().over(rc["key"]).alias("__d"))
    out = np.full(df.height, np.nan, dtype=np.float64)
    out[proj["__i"].to_numpy()] = d.to_series().to_numpy().astype(np.float64)
    return out


# ------------------------------------------------------------ scanner ------ #
def scan_layer_state(scan, lid, spec, fields, files, shape, rules, rank_change,
                     layer_declared_total) -> tuple[dict, int]:
    """Scan one chunk's payloads for a layer into a serialisable, mergeable state.

    Every returned structure is bounded by one payload or by the reservoir -- never by the
    number of days in the corpus: exact counters are plain integers, coordinate/rarity tallies
    are ``Counter``s over bounded key domains, the path-shape dict holds one small tuple per
    path, and the only per-row values retained are the reservoir's kept rows.
    ``layer_declared_total`` is the manifest's DECLARED row total for the layer and fixes the
    subsample rate identically in every chunk.
    """
    present = scan["present"].get(lid, [])
    nums = {
        r: [c for c in spec.get(r) or [] if c in present]
        for r in ("magnitude", "activity", "staleness", "rank", "counts")
    }
    nums["derived"] = [d["name"] for d in spec.get("derived") or []]
    num_all = [c for r in NUM_ROLES for c in nums[r]] + nums["derived"]
    coords = [c for c in spec.get("coordinates") or [] if c in present]
    masks = [c for c in spec.get("masks") or [] if c in present]
    keys = [c for c in spec.get("keys") or [] if c in present]
    shared = spec["partition"] == "shared"
    declared = sum(int(f["rows"] or 0) for f in files)
    res = Reservoir(
        scan["cfg"]["reservoir_rows"],
        layer_declared_total,
        salt_of(scan["cfg"]["salt"], lid),
        num_all,
    )
    nulls, trues, by_day = Counter(), Counter(), Counter()
    by_coord = {c: Counter() for c in coords}
    by_strata: dict[str, Counter] = {k: Counter() for k in ("block", "month", "feed_era")}
    shape_rows, rarity_ct, rc_counts = {}, defaultdict(Counter), Counter()
    ctx = ([shape["span"], shape["key"]] if shape else []) + (
        [rank_change["time"], rank_change["key"], rank_change["column"]] if rank_change else []
    )
    need = [
        c
        for c in dict.fromkeys(
            keys
            + coords
            + masks
            + num_all
            + ctx
            + ["day"]
            + list(spec.get("accumulate") or [])
            + [i for d in spec.get("derived") or [] for i in d["inputs"]]
        )
        if c in present
    ]
    problems = 0
    for f in sorted(files, key=lambda x: (x["day"] or "", x["rel"])):
        if scan["integrity"][f["rel"]] != "ok":
            problems += 1
            continue
        df = pl.read_parquet(f["path"], columns=need)
        for d in spec.get("derived") or []:
            df = df.with_columns(DERIVED[d["op"]](d["inputs"]).alias(d["name"]))
        n = df.height
        extra = None
        if rank_change:  # exact counts streamed; values only live in this payload's array
            rk = rank_change_diff(df, rank_change)
            fin = np.isfinite(rk)
            rc_counts["n"] += int(fin.sum())
            rc_counts["positive"] += int((rk[fin] > 0).sum())
            rc_counts["zero"] += int((rk[fin] == 0).sum())
            rc_counts["negative"] += int((rk[fin] < 0).sum())
            extra = {"__rk_d": rk}
        res.add(salt_of(f["rel"]), df, extra)
        counted = list(dict.fromkeys(num_all + masks + coords + keys))
        for c, v in df.select(counted).null_count().row(0, named=True).items():
            nulls[c] += int(v or 0)
        if masks:
            for c, v in df.select([pl.col(c).sum() for c in masks]).row(0, named=True).items():
                trues[c] += int(v or 0)
        if shared:
            for d, c in df.group_by("day").len().iter_rows():
                by_day[d] += int(c)
            if spec.get("day_meta"):  # sources the shared day -> era/block/month map
                scan["day_meta"].update(
                    {
                        r[0]: r[1:]
                        for r in df.select(
                            "day", "feed_era", "session_end", "is_early_close"
                        ).iter_rows()
                    }
                )
        else:
            by_day[f["day"]] += n
        for c in coords:  # bounded per-slice value_counts; exact tallies combined across slices
            series = df[c]
            if series.dtype == pl.List:
                series = series.list.join("|")
            hit = by_coord[c]
            for start in range(0, n, SCAN_SLICE):
                for v, cnt in series.slice(start, SCAN_SLICE).value_counts().iter_rows():
                    hit[str(v)] += int(cnt)
        for rule in rules:
            for t in rule["thresholds"]:
                hits = int((df[rule["column"]] >= t).sum())
                key, day = f"{rule['column']}>={num(t)}", f["day"]
                rarity_ct[key]["total"] += hits
                rarity_ct[key][block_of(day) if day else "shared"] += hits
        if shape:  # bounded per-slice aggregate; rows sum and min/max combine across slices
            for start in range(0, n, SCAN_SLICE):
                blk = df.slice(start, SCAN_SLICE)
                for pid, rows, lo, hi in (
                    blk.group_by(shape["key"])
                    .agg(
                        pl.len().alias("rows"),
                        pl.col(shape["span"]).min().alias("lo"),
                        pl.col(shape["span"]).max().alias("hi"),
                    )
                    .iter_rows()
                ):
                    prev = shape_rows.get(str(pid))
                    if prev is None:
                        shape_rows[str(pid)] = [int(rows), int(lo), int(hi)]
                    else:  # the same path reached another slice/payload/day of this chunk
                        prev[0] += int(rows)
                        prev[1] = min(prev[1], int(lo))
                        prev[2] = max(prev[2], int(hi))
        if spec.get("accumulate"):
            for c in spec["accumulate"]:
                scan["vectors"][f"{lid}.{c}"].extend(df[c].to_list())
        # release this payload before the next one is read: memory is O(largest payload), not
        # O(days) or O(2 x payload)
        del df
        if rank_change:
            del rk
    scanned = int(sum(by_day.values()))
    meta = scan.get("day_meta") or {}
    for day, cnt in by_day.items():
        m = meta.get(day) or (None, None, None)
        by_strata["block"][block_of(day)] += cnt
        by_strata["month"][day[:7]] += cnt
        by_strata["feed_era"][str(m[0])] += cnt
    by_strata = {k: v for k, v in by_strata.items() if k not in coords}
    return (
        {
            "table": spec["table"],
            "tier": spec["tier"],
            "partition": spec["partition"],
            "declared_manifest": declared,
            "scanned": scanned,
            "n_days": len(by_day),
            "by_day": dict(sorted(by_day.items())),
            "by_strata": {k: dict(sorted(v.items())) for k, v in by_strata.items()},
            "by_coordinate": {c: dict(sorted(v.items())) for c, v in by_coord.items()},
            "null_counts": {c: nulls[c] for c in dict.fromkeys(num_all + masks + coords + keys)},
            "mask_true": {c: trues[c] for c in masks},
            "mask_null": {c: nulls[c] for c in masks},
            "present": list(present),
            "files": sorted(f["rel"] for f in files),
            "sampling": {
                "reservoir_rows": res.cap,
                "rate_declared_rows": res.declared,
                "sampling_fraction": num(res.frac),
                "kept_rows": res.kept,
                "scanned_rows": res.seen,
            },
            "sample": pack_sample(res.arrays()),
            "path_shape": (
                {"key": shape["key"], "span": shape["span"], "overlap_paths": 0,
                 "rows": {k: list(v) for k, v in sorted(shape_rows.items())}}
                if shape
                else None
            ),
            "rank_change": (
                {
                    "key": rank_change["key"],
                    "time": rank_change["time"],
                    "column": rank_change["column"],
                    "counts": dict(rc_counts),
                }
                if rank_change
                else None
            ),
            "rarity": (
                {k: dict(sorted(v.items())) for k, v in sorted(rarity_ct.items())}
                if rules
                else None
            ),
        },
        problems,
    )


def layer_section(lid, spec, fields, st, sample, cfg, derived_strata) -> dict:
    """Assemble the frozen per-layer report section from a (merged) layer state."""
    present = st["present"]
    nums = {
        r: [c for c in spec.get(r) or [] if c in present]
        for r in ("magnitude", "activity", "staleness", "rank", "counts")
    }
    nums["derived"] = [d["name"] for d in spec.get("derived") or []]
    num_all = [c for r in NUM_ROLES for c in nums[r]] + nums["derived"]
    coords = [c for c in spec.get("coordinates") or [] if c in present]
    masks = [c for c in spec.get("masks") or [] if c in present]
    keys = [c for c in spec.get("keys") or [] if c in present]
    scanned = st["scanned"]
    nulls = st["null_counts"]

    def q(values: np.ndarray) -> dict:
        return quantiles(values, cfg["quantile_probs"], cfg["quantile_ci"])

    def col(c: str) -> np.ndarray:
        if sample is not None and c in sample["cols"]:
            return sample["cols"][c]
        return np.array([])

    section = {
        "table": st["table"],
        "tier": st["tier"],
        "partition": st["partition"],
        "rows": {
            "declared_manifest": st["declared_manifest"],
            "scanned": scanned,
            "match": st["declared_manifest"] == scanned,
            "n_days": st["n_days"],
        },
        "sampling": {
            "reservoir_rows": st["sampling"]["reservoir_rows"],
            "rate_declared_rows": st["sampling"]["rate_declared_rows"],
            "rate_rule": "per-row Bernoulli rate = reservoir_rows / MANIFEST-DECLARED layer rows "
            "(chunk independent); the union of kept rows is identical for any day partition",
            "sampled_rows": None if sample is None else int(sample["__h"].size),
            "sampling_fraction": st["sampling"]["sampling_fraction"],
            "exact_quantiles": scanned <= st["sampling"]["reservoir_rows"],
        },
        "by_day": st["by_day"],
        "by_strata": st["by_strata"],
        "by_coordinate": st["by_coordinate"],
        "masks": {
            c: {
                "true": st["mask_true"].get(c, 0),
                "false": scanned - st["mask_true"].get(c, 0) - st["mask_null"].get(c, 0),
                "null": st["mask_null"].get(c, 0),
            }
            for c in masks
        },
        "null_counts": {c: nulls.get(c, 0) for c in dict.fromkeys(num_all + masks + coords + keys)},
        "field_presence": {
            c: {
                **{
                    k: fields[c][k]
                    for k in (
                        "dtype",
                        "namespace",
                        "coordinate_only",
                        "prospective_allowed",
                        "retrospective_only",
                        "supportable_by_tier",
                    )
                },
                "present": c in present,
                "null_rows": nulls.get(c, 0),
                "present_rows": scanned - nulls.get(c, 0),
            }
            for c in sorted(set(num_all + coords + masks + keys))
            if c in fields
        },
        "quantiles": {
            c: {"role": role, **q(col(c))}
            for role in NUM_ROLES + ("derived",)
            for c in nums[role]
        },
        "unobservable": unobservable(fields, spec, present, derived_strata),
    }
    if st.get("path_shape") is not None:
        ps = st["path_shape"]
        rows_by_path = ps["rows"]
        rp = np.array([v[0] for v in rows_by_path.values()], float)
        sp = np.array([v[2] - v[1] for v in rows_by_path.values()], float)
        section["path_shape"] = {
            "key": ps["key"],
            "span": ps["span"],
            "n_paths": len(rows_by_path),
            "exact": True,
            "rows_per_path": q(rp),
            "elapsed_minutes": q(sp),
        }
        if ps.get("overlap_paths"):
            section["path_shape"]["paths_seen_in_multiple_chunks"] = ps["overlap_paths"]
    if st.get("rank_change") is not None:
        rc = st["rank_change"]
        counts = rc["counts"]
        section["rank_change"] = {
            "key": rc["key"],
            "time": rc["time"],
            "column": rc["column"],
            "exact_n": counts["n"],
            "positive": counts["positive"],
            "zero": counts["zero"],
            "negative": counts["negative"],
            "quantiles": q(col("__rk_d")),
            "quantiles_basis": "bounded deterministic subsample (see layers.*.sampling.rate_rule)",
        }
    if st.get("rarity") is not None:
        section["rarity"] = st["rarity"]
    return section


# ------------------------------------------------ evidence + orchestration - #
def require_manifest(manifest_path: Path) -> None:
    """Refuse a missing evidence manifest as a contract refusal, never a FileNotFoundError."""
    if not manifest_path.is_file():
        raise ContractRefusalError(f"manifest not found: {manifest_path}")


def require_frozen_contract(insp: dict, path: Path = CONTRACT_PATH) -> None:
    """PLAN sections 2/8: the Stage-0C allowlist must be frozen (and re-locked) before a run."""
    status, frozen = insp.get("status"), insp.get("frozen")
    if frozen is not True or status not in FROZEN_CONTRACT_STATES:
        raise ContractRefusalError(
            f"inspection contract {path} is not frozen: status={status!r}, frozen={frozen!r}; "
            f"freeze it (frozen=true, status in {list(FROZEN_CONTRACT_STATES)}) and re-lock "
            "the contract before the blind inspection runs"
        )


def build_index(root: Path, manifest: dict, insp: dict) -> tuple[dict, list, set]:
    """Index the manifest payloads by layer (full manifest; independent of any chunk)."""
    index: dict[str, list[dict]] = defaultdict(list)
    uninspected: list[str] = []
    days: set[str] = set()
    for key, info in sorted((manifest.get("payload_sha256") or {}).items()):
        hit = next(
            (
                (lid, None)
                for lid, s in insp["layers"].items()
                if s["partition"] == "shared" and key == s["prefix"]
            ),
            None,
        )
        if hit is None:
            hit = next(
                (
                    (lid, key.split("/", 1)[1])
                    for lid, s in insp["layers"].items()
                    if s["partition"] == "day" and key.startswith(s["prefix"] + "/")
                ),
                None,
            )
        if hit is None:
            uninspected.append(key)
            continue
        lid, day = hit
        if day is not None:
            try:
                guard_day(day)  # sealed/reserved days refuse here
            except PermissionError as exc:  # contract refusal, never a traceback
                raise ContractRefusalError(str(exc)) from exc
            days.add(day)
        index[lid].append(
            {
                "day": day,
                "rel": str(info.get("path")),
                "path": resolve_under(root, str(info.get("path") or "")),
                "rows": info.get("rows"),
                "sha256": info.get("sha256"),
            }
        )
    return index, uninspected, days


def scan_corpus(
    root: Path,
    manifest_path: Path,
    insp: dict,
    schema: dict,
    registry: dict,
    verify_sha: bool,
    evidence: Path | None = None,
    days_subset: list[str] | None = None,
) -> tuple[dict, dict, list]:
    """Scan a chunk of the manifest days into mergeable (context, layer_states, problems).

    ``days_subset=None`` scans every manifest day (one whole-corpus pass, still bounded by the
    reservoir).  Otherwise only those day partitions are read for the day-partitioned layers;
    the shared layers are always read whole (they are not day-partitioned) so every chunk
    carries them identically and the merge can verify them.
    """
    evidence = Path(evidence) if evidence is not None else OBS_EVIDENCE
    require_manifest(Path(manifest_path))
    require_frozen_contract(insp)
    problems: list[dict] = []
    manifest = load_json(manifest_path)
    validate_allowlists(schema, registry, insp)
    lock_file = evidence / "contract_lock.json"
    lock = load_json(lock_file) if lock_file.exists() else {}
    keys = {
        "schema.json": "schema_sha256",
        "contract.json": "contract_sha256",
        "causal_registry.json": "causal_registry_sha256",
    }
    for name, mkey in keys.items():
        got = sha256_file(evidence / name)
        want = (lock.get("hashes") or {}).get(name)
        if want and want != got:
            raise ContractRefusalError(f"{name} sha256 {got} != contract_lock {want}")
        if manifest.get(mkey) and manifest[mkey] != got:
            raise ContractRefusalError(f"{name} sha256 {got} != manifest {mkey} {manifest[mkey]}")
    if manifest.get("core_hash"):
        core = {k: v for k, v in manifest.items() if k != "core_hash"}
        if sha256_bytes(canon_bytes(core)) != manifest["core_hash"]:
            raise ContractRefusalError("manifest core_hash does not re-derive from its own fields")

    index, uninspected, days = build_index(root, manifest, insp)
    all_days = sorted(days)

    # ---- chunk day selection: a contract refusal, before any payload is opened ----
    if days_subset is None:
        chunk_days = all_days
    else:
        want = [str(d).strip() for d in days_subset if str(d).strip()]
        if not want:
            raise ContractRefusalError("--days selected no days")
        dupes = sorted({d for d in want if want.count(d) > 1})
        if dupes:
            raise ContractRefusalError(f"--days repeats days: {dupes[:5]}")
        unknown = sorted(set(want) - set(all_days))
        if unknown:
            raise ContractRefusalError(
                f"--days not in the manifest day set: {unknown[:5]} (n={len(unknown)})"
            )
        for d in sorted(want):
            try:
                guard_day(d)
            except PermissionError as exc:
                raise ContractRefusalError(str(exc)) from exc
        chunk_days = sorted(want)
    chunk_set = set(chunk_days)

    try:  # canonical day set: basket_sim.dev_days(), never a glob
        from factory.scripts.basket_sim import dev_days

        dev: list[str] | None = sorted(dev_days())
        missing = sorted(set(dev) - days)
        if missing:
            problems.append(
                {"kind": "dev_days_not_in_manifest", "n": len(missing), "examples": missing[:5]}
            )
    except Exception as exc:  # calendar unavailable: record, never invent
        dev, problems = None, problems + [{"kind": "dev_days_unavailable", "detail": str(exc)}]

    chunk_index: dict[str, list[dict]] = {
        lid: [f for f in files if insp["layers"][lid]["partition"] == "shared" or f["day"] in chunk_set]
        for lid, files in index.items()
    }
    present: dict[str, list[str]] = {}
    integrity: dict[str, str] = {}
    prefixes, exact = physical_deny(registry)
    for lid, files in chunk_index.items():
        spec = insp["layers"][lid]
        cols: set[str] = set()
        for f in files:
            if not f["path"].exists():
                integrity[f["rel"]] = "declared payload absent"
                continue
            if verify_sha and f["sha256"] and sha256_file(f["path"]) != f["sha256"]:
                integrity[f["rel"]] = "sha256 mismatch vs manifest"
                continue
            integrity[f["rel"]] = "ok"
            cols |= set(pl.scan_parquet(f["path"]).collect_schema().names())
        # physical separation (mirrors the producer's post-write audit): a payload may carry
        # only columns declared for its table in schema.json, and a denied name is tolerated
        # only where schema.json itself declares it censor + retrospective_only.
        table_fields = schema["tables"][spec["table"]]["fields"]
        bad = sorted(
            c
            for c in cols
            if c not in table_fields
            or (
                denied_reason(c, prefixes, exact)
                and not (
                    table_fields[c]["namespace"] == "censor"
                    and table_fields[c]["retrospective_only"]
                )
            )
        )
        if bad:
            raise ContractRefusalError(
                f"{lid}: payload physical schema carries denied/undeclared columns {bad}"
            )
        present[lid] = sorted(cols)
    problems += [
        {"kind": "payload_integrity", "path": rel, "detail": state}
        for rel, state in sorted(integrity.items())
        if state != "ok"
    ]
    extra = []
    for lid, spec in insp["layers"].items():
        if spec["partition"] != "day" or not (root / spec["prefix"]).exists():
            continue
        want = {f["rel"] for f in index[lid]}  # FULL manifest set: flags only undeclared files
        extra += [
            str(p.relative_to(root))
            for p in sorted((root / spec["prefix"]).rglob("*.parquet"))
            if str(p.relative_to(root)) not in want
        ]

    shape_by = {s["layer"]: s for s in insp.get("path_shape") or []}
    change_by = {r["layer"]: r for r in insp.get("rank_change") or []}
    scan = {
        "cfg": insp["sampling"],
        "present": present,
        "integrity": integrity,
        "day_meta": {},
        "vectors": defaultdict(list),
        "derived_strata": tuple(insp.get("derived_strata") or ()),
    }
    states, n_problems = {}, 0
    order = sorted(insp["layers"], key=lambda x: 0 if x == "day_registry" else 1)
    for lid in order:
        spec = insp["layers"][lid]
        # the subsample rate always comes from the manifest's DECLARED total for the layer,
        # never from this chunk's rows -- that is what makes any partition yield one union.
        layer_declared_total = sum(int(f["rows"] or 0) for f in index[lid])
        state, p = scan_layer_state(
            scan,
            lid,
            spec,
            schema["tables"][spec["table"]]["fields"],
            chunk_index[lid],
            shape_by.get(lid),
            [r for r in insp.get("rarity", {}).get("rules") or [] if r["layer"] == lid],
            change_by.get(lid),
            layer_declared_total,
        )
        states[lid], n_problems = state, n_problems + p
    if n_problems != sum(1 for s in integrity.values() if s != "ok"):
        raise ContractRefusalError("integrity accounting drifted between pass and scan")
    problems += [
        {
            "kind": "payload_rows_mismatch",
            "layer": lid,
            "declared": s["declared_manifest"],
            "scanned": s["scanned"],
        }
        for lid, s in sorted(states.items())
        if s["declared_manifest"] != s["scanned"]
    ]

    parents = {}
    for name, rec in sorted((manifest.get("parents") or {}).items()):
        path = Path(str(rec.get("source_uri") or "").replace("file://", ""))
        got = sha256_file(path) if path.is_file() else None
        parents[name] = {
            "declared_sha256": rec.get("sha256"),
            "present": path.is_file(),
            "matched": None if got is None or not rec.get("sha256") else got == rec["sha256"],
            "tracked": rec.get("tracked"),
        }
        if got and rec.get("sha256") and got != rec["sha256"]:
            problems.append({"kind": "parent_drift", "name": name, "path": str(path)})

    ctx = {
        "root": str(root),
        "manifest": str(manifest_path),
        "evidence": str(evidence),
        "manifest_core_hash": manifest.get("core_hash"),
        "manifest_node_id": manifest.get("node_id"),
        "manifest_status": manifest.get("status"),
        "manifest_policy_hashes": {
            k: manifest.get(k)
            for k in (
                "schema_sha256",
                "contract_sha256",
                "causal_registry_sha256",
                "canary_days_sha256",
                "adaptive_choice_ledger_sha256",
                "code_sha256",
                "config_sha256",
            )
        },
        "recomputed_sha256": {n: sha256_file(evidence / n) for n in keys},
        "inspection_contract_sha256": sha256_file(CONTRACT_PATH),
        "script_sha256": sha256_file(HERE),
        "script": str(HERE.relative_to(ROOT)),
        "payloads": {
            "n_declared": len(manifest.get("payload_sha256") or {}),
            "n_inspected": sum(len(v) for v in chunk_index.values()),
            "n_uninspected_keys": len(uninspected),
            "uninspected": uninspected[:20],
            "rows_total": sum(int(f["rows"] or 0) for v in chunk_index.values() for f in v),
            "files_verified_sha256": verify_sha,
        },
        "parents": parents,
        "days": chunk_days,
        "manifest_days": all_days,
        "dev_days": dev,
        "dev_days_n": None if dev is None else len(dev),
        "vectors": {k: list(v) for k, v in scan["vectors"].items()},
        "extra": sorted(set(extra))[:50],
        "sampling_rule": (
            "numeric-distribution subsample = per-row deterministic Bernoulli at rate "
            "reservoir_rows / manifest-DECLARED layer rows (identical in every chunk, never the "
            "chunk's row count); the union of kept rows is identical for any day partition"
        ),
        "problems": problems,
    }
    return ctx, states, problems


def day_runs(days: list[str], manifest_days: list[str]) -> list[list[str]]:
    """Maximal runs of selected days that are adjacent in the manifest day ordering."""
    order = {d: i for i, d in enumerate(manifest_days)}
    pos = sorted(order[d] for d in days if d in order)
    runs: list[list[str]] = []
    start = prev = None
    for i in pos:
        if start is None:
            start = prev = i
        elif i == prev + 1:
            prev = i
        else:
            runs.append([manifest_days[start], manifest_days[prev]])
            start = prev = i
    if start is not None:
        runs.append([manifest_days[start], manifest_days[prev]])
    return runs


def finalize_report(ctx: dict, states: dict, insp: dict, schema: dict) -> dict:
    """Compose the frozen report from a (merged) context + per-layer states."""
    cfg = insp["sampling"]
    derived_strata = tuple(insp.get("derived_strata") or ())
    sections = {}
    for lid in sorted(insp["layers"], key=lambda x: 0 if x == "day_registry" else 1):
        spec = insp["layers"][lid]
        fields = schema["tables"][spec["table"]]["fields"]
        st = states[lid]
        sample = cap_sample(unpack_sample(st.get("sample")), st["sampling"]["reservoir_rows"])
        sections[lid] = layer_section(lid, spec, fields, st, sample, cfg, derived_strata)
    vectors = ctx["vectors"]
    mem_paths = vectors.get("memberships.path_id", [])
    mem_ids = vectors.get("memberships.member_id", [])
    per_path = Counter(
        mem_paths
    )  # memberships.path_id is a prospective key; paths.n_memberships is not
    paths_rows = sections.get("paths", {}).get("rows", {}).get("scanned")
    n_shared = sum(1 for v in per_path.values() if v > 1)
    census = (
        load_json(Path(ctx["evidence"]) / "contract.json")
        .get("identity", {})
        .get("expected_panel_census", {})
    )
    days = sorted(ctx["days"])
    dev = ctx.get("dev_days")
    manifest_days = ctx.get("manifest_days")
    report = {
        "report_version": insp["contract_version"],
        "node_id": "observations.tape_atlas.blind-corpus-report",
        "status": "REFUSED" if ctx["problems"] else "complete",
        "sources": {
            "root": ctx["root"],
            "manifest": ctx["manifest"],
            "manifest_core_hash": ctx["manifest_core_hash"],
            "manifest_node_id": ctx["manifest_node_id"],
            "manifest_status": ctx["manifest_status"],
            "manifest_policy_hashes": ctx["manifest_policy_hashes"],
            "recomputed_sha256": ctx["recomputed_sha256"],
            "inspection_contract_sha256": ctx["inspection_contract_sha256"],
            "script_sha256": ctx["script_sha256"],
            "script": ctx["script"],
            "payloads": ctx["payloads"],
            "parents": ctx["parents"],
        },
        "day_set": {
            "n_days": len(days),
            "days": days,
            "dev_days_n": ctx.get("dev_days_n"),
            "equals_dev_days": None if dev is None else dev == days,
            "manifest_days_n": None if manifest_days is None else len(manifest_days),
            "equals_manifest_days": (
                None if manifest_days is None else sorted(manifest_days) == days
            ),
            "all_guarded": True,
        },
        "identity": {
            "source": "memberships.path_id + memberships.member_id (prospective keys); the paths"
            " table's whole-day aggregates are retrospective_only and are not read",
            "n_paths_rows": paths_rows,
            "n_memberships": len(mem_ids),
            "n_paths_with_membership": len(per_path),
            "n_duplicate_member_ids": len(mem_ids) - len(set(mem_ids)),
            "shared_paths": n_shared,
            "independent_paths": sum(1 for v in per_path.values() if v == 1),
            "memberships_on_shared_paths": sum(v for v in per_path.values() if v > 1),
            "max_memberships_per_path": max(per_path.values(), default=0),
            "memberships_per_path": dict(sorted(Counter(per_path.values()).items())),
            "declared_census": census,
            "census_match": {
                k: (census.get(k) == v)
                for k, v in (
                    ("paths", len(per_path)),
                    ("memberships", len(mem_ids)),
                    ("shared_paths", n_shared),
                )
            },
        },
        "exactness": {
            "exact": [
                "row counts",
                "null/presence counts",
                "coordinate tallies",
                "coverage masks",
                "path shape (rows, elapsed span)",
                "rarity counts",
                "rank-change counts",
                "identity/duplicate counts",
            ],
            "estimated": "numeric quantiles (and the rank-change quantiles) from a deterministic "
            "Bernoulli subsample with a 95% binomial order-statistic CI; exact when scanned rows "
            "<= reservoir_rows",
            "reservoir_rows": insp["sampling"]["reservoir_rows"],
        },
        "sampling_rule": ctx.get("sampling_rule"),
        "allowed_blind_summaries": load_json(Path(ctx["evidence"]) / "contract.json").get(
            "allowed_blind_summaries"
        ),
        "derived_strata": insp.get("derived_strata"),
        "layers": dict(sorted(sections.items())),
        "extra_undeclared_partitions": ctx["extra"],
        "refusals": ctx["problems"],
    }
    if ctx.get("chunk_provenance") is not None:
        report["chunk_provenance"] = ctx["chunk_provenance"]
        report["chunk_merge_rule"] = (
            "merged from disjoint chunk reports whose day union is exactly the manifest day set; "
            "day-payload coverage is a partition; shared-layer sections and per-chunk subsample "
            "rates were verified identical"
        )
    report["report_sha256"] = sha256_bytes(canon_bytes(report))
    return report


def inspect_corpus(
    root: Path,
    manifest_path: Path,
    insp: dict,
    schema: dict,
    registry: dict,
    verify_sha: bool,
    evidence: Path | None = None,
) -> tuple[dict, list]:
    """One whole-corpus pass (bounded) -> final report; the pre-chunking entry point."""
    ctx, states, problems = scan_corpus(
        root, manifest_path, insp, schema, registry, verify_sha, evidence
    )
    return finalize_report(ctx, states, insp, schema), problems


def merge_chunk_reports(
    report_paths: list[str],
    manifest_path: Path,
    insp: dict,
    schema: dict,
    evidence: Path | None = None,
) -> tuple[dict, list]:
    """Merge disjoint chunk reports into the final report.

    Refuses (``ContractRefusalError`` -> exit 2, no report) unless the chunk day sets are
    disjoint, their union is exactly the manifest day set, every manifest payload is covered by
    exactly one chunk, the chunks agree on the payload physical schema, the shared-layer
    sections are identical and every chunk used the identical subsample rate.  Integrity
    problems recorded inside a chunk (absent payload, sha mismatch, rows mismatch, drift) are
    carried into the merged report, which is then written with status REFUSED (exit 3).
    """
    evidence = Path(evidence) if evidence is not None else OBS_EVIDENCE
    require_manifest(Path(manifest_path))
    require_frozen_contract(insp)
    manifest = load_json(manifest_path)
    if manifest.get("core_hash"):
        core = {k: v for k, v in manifest.items() if k != "core_hash"}
        if sha256_bytes(canon_bytes(core)) != manifest["core_hash"]:
            raise ContractRefusalError("manifest core_hash does not re-derive from its own fields")
    if not report_paths:
        raise ContractRefusalError("--merge needs at least one chunk report")

    chunks = []
    for p in report_paths:
        rp = Path(p)
        if not rp.is_file():
            raise ContractRefusalError(f"chunk report not found: {rp}")
        ch = load_json(rp)
        if ch.get("report_kind") != "chunk":
            raise ContractRefusalError(f"not a chunk report: {rp}")
        embedded = ch.get("chunk_sha256")
        body = {k: v for k, v in ch.items() if k != "chunk_sha256"}
        if embedded != sha256_bytes(canon_bytes(body)):
            raise ContractRefusalError(f"chunk report self-hash mismatch: {rp}")
        ch["_path"], ch["_sha256"] = str(rp), sha256_file(rp)
        chunks.append(ch)
    chunks.sort(key=lambda c: min(c["context"]["days"]))

    for f in ("manifest_core_hash", "inspection_contract_sha256", "script_sha256", "root", "manifest"):
        vals = {str(c["context"].get(f)) for c in chunks}
        if len(vals) != 1:
            raise ContractRefusalError(f"chunk reports disagree on {f}: {sorted(vals)}")
    if chunks[0]["context"]["manifest_core_hash"] != manifest.get("core_hash"):
        raise ContractRefusalError("chunk manifest core_hash != --manifest core_hash")
    if chunks[0]["context"]["inspection_contract_sha256"] != sha256_file(CONTRACT_PATH):
        raise ContractRefusalError("chunk inspection contract sha256 != the current contract")
    if chunks[0]["context"]["script_sha256"] != sha256_file(HERE):
        raise ContractRefusalError("chunk script sha256 != the current script")

    root = Path(chunks[0]["context"]["root"])
    index, uninspected, days = build_index(root, manifest, insp)
    manifest_days = sorted(days)

    seen: set[str] = set()
    for c in chunks:
        dl = set(c["context"]["days"])
        inter = seen & dl
        if inter:
            raise ContractRefusalError(f"chunk day sets overlap: {sorted(inter)[:5]}")
        unknown = sorted(dl - set(manifest_days))
        if unknown:
            raise ContractRefusalError(f"chunk days not in the manifest day set: {unknown[:5]}")
        seen |= dl
    union = sorted(seen)
    if union != manifest_days:
        missing = sorted(set(manifest_days) - seen)
        raise ContractRefusalError(
            f"chunk day union != manifest day set (missing {missing[:5]} n={len(missing)}, "
            f"scanned days n={len(union)} vs manifest n={len(manifest_days)})"
        )

    # every manifest payload is scanned by exactly one chunk (day payloads partition the
    # manifest files; the shared, non-day-partitioned payload is read whole by every chunk)
    for lid in insp["layers"]:
        shared = insp["layers"][lid]["partition"] == "shared"
        seen_files: set[str] = set()
        for c in chunks:
            files = set(c["layers"][lid]["files"])
            if not shared:
                dup = seen_files & files
                if dup:
                    raise ContractRefusalError(
                        f"{lid}: payload scanned by two chunks: {sorted(dup)[:5]}"
                    )
            seen_files |= files
        manifest_files = {f["rel"] for f in index[lid]}
        if shared and seen_files != manifest_files:
            raise ContractRefusalError(
                f"{lid}: shared payload not read whole by every chunk: {sorted(seen_files)}"
            )
        missing_files = sorted(manifest_files - seen_files)
        if missing_files:
            raise ContractRefusalError(
                f"{lid}: payloads not covered by any chunk: {missing_files[:5]} (n={len(missing_files)})"
            )

    problems = [
        {**pr, "chunk": c["_path"], "chunk_sha256": c["_sha256"]}
        for c in chunks
        for pr in c["refusals"]
    ]

    if not problems:
        for lid in insp["layers"]:
            rates = {
                (
                    c["layers"][lid]["sampling"]["reservoir_rows"],
                    c["layers"][lid]["sampling"]["rate_declared_rows"],
                    c["layers"][lid]["sampling"]["sampling_fraction"],
                )
                for c in chunks
            }
            if len(rates) != 1:
                raise ContractRefusalError(f"{lid}: chunk subsample rates differ: {sorted(rates)}")
            presents = {tuple(c["layers"][lid]["present"]) for c in chunks}
            if len(presents) != 1:
                raise ContractRefusalError(f"{lid}: chunks disagree on the payload physical schema")
            if insp["layers"][lid]["partition"] == "shared":
                blobs = {sha256_bytes(canon_bytes(c["layers"][lid])) for c in chunks}
                if len(blobs) != 1:
                    raise ContractRefusalError(f"{lid}: shared-layer sections differ across chunks")
        for k in ("parents", "vectors"):
            blobs = {sha256_bytes(canon_bytes(c["context"][k])) for c in chunks}
            if len(blobs) != 1:
                raise ContractRefusalError(f"chunk reports disagree on {k}")

    merged_ctx = dict(chunks[0]["context"])
    merged_ctx["days"] = union
    merged_ctx["manifest_days"] = manifest_days
    merged_ctx["dev_days"] = chunks[0]["context"].get("dev_days")
    merged_ctx["dev_days_n"] = chunks[0]["context"].get("dev_days_n")
    merged_ctx["extra"] = sorted({e for c in chunks for e in c["context"]["extra"]})[:50]
    uninspected_all = sorted({u for c in chunks for u in c["context"]["payloads"]["uninspected"]})
    # the five corpus-level aggregates (day_registry, coverage, paths, memberships,
    # quote_rosters summary) are declared over the WHOLE corpus and are read whole by every
    # chunk; they are counted ONCE here from the manifest index, never summed per chunk
    merged_ctx["payloads"] = {
        "n_declared": len(manifest.get("payload_sha256") or {}),
        "n_inspected": sum(len(v) for v in index.values()),
        "n_uninspected_keys": len(uninspected),
        "uninspected": uninspected_all[:20],
        "rows_total": sum(int(f["rows"] or 0) for v in index.values() for f in v),
        "files_verified_sha256": all(
            c["context"]["payloads"]["files_verified_sha256"] for c in chunks
        ),
        "n_chunks": len(chunks),
    }
    merged_ctx["parents"] = chunks[0]["context"]["parents"]
    merged_ctx["vectors"] = chunks[0]["context"]["vectors"]
    merged_ctx["problems"] = problems
    merged_ctx["chunk_provenance"] = [
        {
            "report": c["_path"],
            "sha256": c["_sha256"],
            "chunk_sha256": c["chunk_sha256"],
            "status": c["status"],
            "n_days": len(c["context"]["days"]),
            "day_first": min(c["context"]["days"]),
            "day_last": max(c["context"]["days"]),
            "day_runs": day_runs(c["context"]["days"], manifest_days),
            "n_payloads_inspected": c["context"]["payloads"]["n_inspected"],
            "rows_total": c["context"]["payloads"]["rows_total"],
            "refusals_n": len(c["refusals"]),
        }
        for c in chunks
    ]

    merged_states = {}
    for lid in insp["layers"]:
        if insp["layers"][lid]["partition"] == "shared":
            merged_states[lid] = chunks[0]["layers"][lid]
            continue
        st0 = chunks[0]["layers"][lid]
        by_day, by_strata, by_coord = Counter(), defaultdict(Counter), defaultdict(Counter)
        nulls, mask_true, mask_null, rarity, rc = Counter(), Counter(), Counter(), defaultdict(Counter), Counter()
        shape_rows: dict[str, list[int]] = {}
        overlap_paths, samples = 0, []
        declared = scanned = 0
        present: set[str] = set()
        files: set[str] = set()
        for c in chunks:
            st = c["layers"][lid]
            declared += st["declared_manifest"]
            scanned += st["scanned"]
            nulls.update(st["null_counts"])
            mask_true.update(st["mask_true"])
            mask_null.update(st["mask_null"])
            for k, v in st["by_day"].items():
                by_day[k] += v
            for k, v in st["by_strata"].items():
                by_strata[k].update(v)
            for cc, v in st["by_coordinate"].items():
                by_coord[cc].update(v)
            for k, v in (st["rarity"] or {}).items():
                rarity[k].update(v)
            for k, v in (st.get("rank_change") or {}).get("counts", {}).items():
                rc[k] += v
            for pid, (rows, lo, hi) in (st.get("path_shape") or {}).get("rows", {}).items():
                prev = shape_rows.get(pid)
                if prev is None:
                    shape_rows[pid] = [rows, lo, hi]
                else:  # a path spanning a chunk boundary: combine, count the overlap
                    overlap_paths += 1
                    prev[0] += rows
                    prev[1] = min(prev[1], lo)
                    prev[2] = max(prev[2], hi)
            if st["sample"] is not None:
                samples.append(unpack_sample(st["sample"]))
            present |= set(st["present"])
            files |= set(st["files"])
        cap = st0["sampling"]["reservoir_rows"]
        merged_states[lid] = {
            "table": st0["table"],
            "tier": st0["tier"],
            "partition": st0["partition"],
            "declared_manifest": declared,
            "scanned": scanned,
            "n_days": len(by_day),
            "by_day": dict(sorted(by_day.items())),
            "by_strata": {k: dict(sorted(v.items())) for k, v in by_strata.items()},
            "by_coordinate": {c: dict(sorted(v.items())) for c, v in by_coord.items()},
            "null_counts": dict(nulls),
            "mask_true": dict(mask_true),
            "mask_null": dict(mask_null),
            "present": sorted(present),
            "files": sorted(files),
            "sampling": dict(st0["sampling"]),
            "sample": pack_sample(cap_sample(concat_samples(samples), cap)),
            "path_shape": (
                {"key": st0["path_shape"]["key"], "span": st0["path_shape"]["span"],
                 "overlap_paths": overlap_paths,
                 "rows": {k: list(v) for k, v in sorted(shape_rows.items())}}
                if st0.get("path_shape") is not None
                else None
            ),
            "rank_change": (
                {**{k: st0["rank_change"][k] for k in ("key", "time", "column")},
                 "counts": dict(rc)}
                if st0.get("rank_change") is not None
                else None
            ),
            "rarity": (
                {k: dict(sorted(v.items())) for k, v in sorted(rarity.items())}
                if st0.get("rarity") is not None
                else None
            ),
        }
    return finalize_report(merged_ctx, merged_states, insp, schema), problems


# -------------------------------------------------------------- CLI -------- #
def _resolve(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else ROOT / q


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Freeze-O blind corpus inspection (whole-corpus or chunked+merge)"
    )
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="absolute observation data root")
    ap.add_argument("--manifest", default=str(OBS_EVIDENCE / "manifest.json"))
    ap.add_argument("--out-evidence", default=None, help="directory for blind_corpus_report.json")
    ap.add_argument(
        "--days",
        default=None,
        help="comma-separated day subset of the manifest day set; writes ONE chunk report",
    )
    ap.add_argument(
        "--out-report",
        default=None,
        help="chunk report path (with --days) or merged report path (with --merge)",
    )
    ap.add_argument(
        "--merge",
        nargs="+",
        default=None,
        metavar="CHUNK_REPORT",
        help="chunk reports to merge into the final report",
    )
    ap.add_argument("--no-verify-payload-sha", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return run_selftest()
    if args.merge and args.days:
        print("inspection refusal: --days and --merge are mutually exclusive", file=sys.stderr)
        return 2
    manifest_path = _resolve(args.manifest)
    insp = load_json(CONTRACT_PATH)
    schema = load_json(OBS_EVIDENCE / "schema.json")
    registry = load_json(OBS_EVIDENCE / "causal_registry.json")
    out_dir = _resolve(args.out_evidence) if args.out_evidence else manifest_path.parent

    if args.merge:  # chunk reports -> final report
        try:
            report, problems = merge_chunk_reports(args.merge, manifest_path, insp, schema)
        except ContractRefusalError as exc:  # invalid chunk set: no report is written
            print("merge refusal: " + str(exc), file=sys.stderr)
            return 2
        out = _resolve(args.out_report) if args.out_report else out_dir / REPORT_NAME
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(canon_bytes(report) + b"\n")
        print(
            json.dumps(
                {k: report.get(k) for k in ("report_version", "status", "day_set", "refusals")},
                indent=1,
            )[:3000]
        )
        return 3 if problems else 0

    if args.days:  # one chunk
        try:
            ctx, states, problems = scan_corpus(
                _resolve(args.root),
                manifest_path,
                insp,
                schema,
                registry,
                verify_sha=not args.no_verify_payload_sha,
                days_subset=[d for d in args.days.split(",")],
            )
        except ContractRefusalError as exc:  # illegal allowlist/day set: no report is written
            print("inspection refusal: " + str(exc), file=sys.stderr)
            return 2
        chunk = {
            "report_kind": "chunk",
            "report_version": insp["contract_version"],
            "node_id": "observations.tape_atlas.blind-corpus-report.chunk",
            "status": "REFUSED" if problems else "complete",
            "context": ctx,
            "layers": states,
            "refusals": problems,
        }
        chunk["chunk_sha256"] = sha256_bytes(canon_bytes(chunk))
        if args.out_report:
            out = _resolve(args.out_report)
        else:
            out = out_dir / f"blind_corpus_chunk.{ctx['days'][0]}_{ctx['days'][-1]}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(canon_bytes(chunk) + b"\n")
        print(
            json.dumps(
                {
                    "report_kind": "chunk",
                    "status": chunk["status"],
                    "days_n": len(ctx["days"]),
                    "day_first": ctx["days"][0],
                    "day_last": ctx["days"][-1],
                    "refusals": problems,
                },
                indent=1,
            )[:3000]
        )
        return 3 if problems else 0

    try:  # one whole-corpus pass (still bounded by the reservoir)
        report, problems = inspect_corpus(
            _resolve(args.root),
            manifest_path,
            insp,
            schema,
            registry,
            verify_sha=not args.no_verify_payload_sha,
        )
    except ContractRefusalError as exc:  # illegal allowlist/evidence chain: no report is written
        print("inspection refusal: " + str(exc), file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / REPORT_NAME).write_bytes(canon_bytes(report) + b"\n")
    print(
        json.dumps(
            {k: report[k] for k in ("report_version", "status", "day_set", "identity", "refusals")},
            indent=1,
        )[:3000]
    )
    return 3 if problems else 0


# --------------------------------------------- synthetic regression -------- #
def run_selftest() -> int:
    import tempfile

    checks: list[dict] = []

    def chk(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail or {}})

    insp, schema, registry = (
        load_json(CONTRACT_PATH),
        load_json(OBS_EVIDENCE / "schema.json"),
        load_json(OBS_EVIDENCE / "causal_registry.json"),
    )
    leaky = json.loads(json.dumps(schema))
    leaky["tables"]["selected_path_grid"]["fields"]["v_hold_flat"] = {
        "dtype": "float64",
        "namespace": "tape",
        "observable_asof_rule": "x",
        "coordinate_only": False,
        "prospective_allowed": True,
        "retrospective_only": False,
        "supportable_by_tier": ["all"],
    }
    try:
        validate_allowlists(schema, registry, insp)
        chk("shipped_allowlist_is_legal", True)
    except ContractRefusalError as exc:
        chk("shipped_allowlist_is_legal", False, {"detail": str(exc)[:400]})

    def accepted(contract, sch=None) -> bool:  # True when an illegal allowlist is NOT refused
        try:
            validate_allowlists(sch or leaky, registry, contract)
            return True
        except ContractRefusalError:
            return False

    for name, lid, role, column in (
        ("refuses_denied_prefix", "race_minute_full", "magnitude", "v_hold_flat"),
        ("refuses_retrospective_column", "race_minute_full", "magnitude", "day_high_raw"),
        ("refuses_undeclared_column", "race_minute_full", "magnitude", "no_such_column"),
        ("refuses_provenance_numeric", "race_minute_full", "magnitude", "source_sha256"),
        ("refuses_censor_namespace", "paths", "counts", "terminal_censored"),
    ):
        bad = json.loads(json.dumps(insp))
        bad["layers"][lid][role].append(column)
        chk(name, not accepted(bad))
    try:
        guard_day("2024-01-02")
        chk("refuses_sealed_day", False)
    except PermissionError:
        chk("refuses_sealed_day", True)
    try:
        require_frozen_contract(dict(insp, frozen=False, status="PREPARED-NOT-RUN"))
        chk("refuses_unfrozen_contract", False, {"detail": "unfrozen contract accepted"})
    except ContractRefusalError as exc:
        msg = str(exc)
        chk(
            "refuses_unfrozen_contract",
            "frozen=False" in msg and "PREPARED-NOT-RUN" in msg and str(CONTRACT_PATH) in msg,
            {"detail": msg},
        )
    try:
        require_frozen_contract(dict(insp, frozen=True, status="frozen"))
        chk("accepts_frozen_contract", True)
    except ContractRefusalError as exc:
        chk("accepts_frozen_contract", False, {"detail": str(exc)})

    with tempfile.TemporaryDirectory() as td:
        root, ev = Path(td) / "data", Path(td) / "evidence"
        grid = root / "grid.selected_paths/month=2021-02"
        grid.mkdir(parents=True)
        ev.mkdir()
        rows = pl.DataFrame(
            {
                "day": ["2021-02-09", "2021-02-09", "2021-02-10"],
                "ticker": ["AAA", "BBB", "AAA"],
                "path_id": ["2021-02-09|AAA", "2021-02-09|BBB", "2021-02-10|AAA"],
                "et": [570, 571, 570],
                "bar_open": [10.0, 20.0, 30.0],
                "bar_close": [11.0, 19.0, 33.0],
                "bar_high": [12.0, 21.0, 34.0],
                "bar_low": [9.5, 18.5, 29.0],
                "bar_volume": [100.0, 200.0, 300.0],
                "bar_state": ["observed"] * 3,
                "print_state": ["observed"] * 3,
                "session_end": [959] * 3,
                "within_session": [True] * 3,
                "first_entry_et": [570, 571, 570],
                "n_prints": [5, 6, 7],
                "coverage_class": ["complete_to_session_end"] * 3,
                "terminal_censored": [False] * 3,
            }
        )  # declared censor column: present, never read
        for day, empty in (("2021-02-09", False), ("2021-02-10", True)):
            sub = rows.filter(pl.col("day") == day)
            (sub.clear() if empty else sub).write_parquet(grid / f"{day}.parquet")
        pl.DataFrame(
            {
                "day": ["2021-02-09"],
                "member_id": ["2021-02-09|A_pm|AAA|570|1"],
                "path_id": ["2021-02-09|AAA"],
                "family": ["A_pm"],
                "ticker": ["AAA"],
                "entry_et": [570],
                "entry_rank": [1],
                "entry_px": [10.0],
                "block": ["block1"],
                "month": ["2021-02"],
                "session_end": [959],
            }
        ).write_parquet(root / "memberships.parquet")

        def entry(key, rel, declared_rows):
            return {
                key: {
                    "path": rel,
                    "rows": declared_rows,
                    "sha256": sha256_file(root / rel) if (root / rel).exists() else None,
                }
            }

        manifest = {
            "node_id": "synthetic",
            "status": "canary_built",
            "parents": {},
            "payload_sha256": {
                **entry("memberships", "memberships.parquet", 1),
                **entry(
                    "selected_path_grid/2021-02-09",
                    "grid.selected_paths/month=2021-02/2021-02-09.parquet",
                    2,
                ),
                **entry(
                    "selected_path_grid/2021-02-10",
                    "grid.selected_paths/month=2021-02/2021-02-10.parquet",
                    0,
                ),
                "selected_path_grid/2021-02-11": {
                    "path": "grid.selected_paths/month=2021-02/2021-02-11.parquet",
                    "rows": 3,
                    "sha256": None,
                },
            },
        }
        manifest["core_hash"] = sha256_bytes(canon_bytes(manifest))
        manifest_bytes = canon_bytes(manifest)
        (ev / "manifest.json").write_bytes(manifest_bytes)
        for n in ("schema.json", "contract.json", "causal_registry.json"):
            (ev / n).write_text("{}")
        mini = json.loads(json.dumps(insp))
        mini["layers"] = {
            k: json.loads(json.dumps(insp["layers"][k]))
            for k in ("memberships", "selected_path_grid")
        }
        mini["layers"]["memberships"].update(
            magnitude=["entry_px"], counts=[], rank=["entry_rank"], derived=[]
        )
        mini["layers"]["selected_path_grid"].update(
            magnitude=["bar_close", "bar_high", "bar_low"],
            activity=["bar_volume", "n_prints"],
            counts=["first_entry_et"],
            staleness=[],
            rank=[],
            coordinates=["session_end", "bar_state", "print_state"],
            masks=["within_session"],
            derived=[
                {
                    "name": "bar_ret_from_open",
                    "op": "div_minus_1",
                    "inputs": ["bar_close", "bar_open"],
                }
            ],
        )
        mini["rarity"] = {
            "rules": [
                {
                    "layer": "selected_path_grid",
                    "column": "bar_ret_from_open",
                    "kind": "magnitude",
                    "thresholds": [0.10],
                }
            ]
        }
        mini["path_shape"] = [{"layer": "selected_path_grid", "key": "path_id", "span": "et"}]
        mini["rank_change"] = []
        # fixture only: the shipped contract carries its real freeze state and is gated by
        # require_frozen_contract, which the checks above exercise separately
        mini["frozen"] = True
        mini["status"] = "frozen"

        def run(contract=None, verify=True, write=None):
            if write is not None:
                (ev / "manifest.json").write_bytes(write)
            return inspect_corpus(
                root,
                ev / "manifest.json",
                contract or mini,
                schema,
                registry,
                verify_sha=verify,
                evidence=ev,
            )

        rep1, problems = run()
        sec = rep1["layers"]["selected_path_grid"]
        chk(
            "declared_absent_payload_reported",
            any(p["kind"] == "payload_integrity" and "2021-02-11" in p["path"] for p in problems),
        )
        chk("row_mismatch_reported", any(p["kind"] == "payload_rows_mismatch" for p in problems))
        chk(
            "exact_rows_and_empty_stratum",
            sec["rows"] == {"declared_manifest": 5, "scanned": 2, "match": False, "n_days": 2}
            and sec["by_day"].get("2021-02-10") == 0,
            sec["rows"],
        )
        chk(
            "derived_and_rarity_exact",
            sec["rarity"]["bar_ret_from_open>=0.1"]["total"] == 1,
            sec["rarity"],
        )
        chk(
            "path_shape_exact",
            sec["path_shape"]["n_paths"] == 2
            and sec["path_shape"]["elapsed_minutes"]["values"][-1] == 0.0,
            sec["path_shape"],
        )
        chk(
            "unobservable_declared",
            any(u["column"] == "coverage_class" for u in sec["unobservable"]),
        )
        chk(
            "tolerates_declared_censor_column",
            "terminal_censored" not in sec["null_counts"]
            and any(u["column"] == "terminal_censored" for u in sec["unobservable"]),
        )
        chk(
            "identity_counts_exact",
            rep1["identity"]["n_paths_with_membership"] == 1
            and rep1["identity"]["n_memberships"] == 1
            and rep1["identity"]["shared_paths"] == 0,
            rep1["identity"],
        )
        chk("deterministic_same_input", canon_bytes(rep1) == canon_bytes(run()[0]))
        mini2 = json.loads(json.dumps(mini))
        mini2["sampling"] = dict(mini["sampling"], reservoir_rows=1)
        s3 = run(contract=mini2)[0]["layers"]["selected_path_grid"]["sampling"]
        chk(
            "subsample_labelled_estimated",
            s3["sampling_fraction"] < 1 and s3["exact_quantiles"] is False,
            s3,
        )
        try:
            run(contract=dict(mini, frozen=False, status="PREPARED-NOT-RUN"))
            chk("inspection_refuses_unfrozen_contract", False, {"detail": "gate not wired"})
        except ContractRefusalError:
            chk("inspection_refuses_unfrozen_contract", True)
        try:
            inspect_corpus(
                root,
                ev / "absent_manifest.json",
                mini,
                schema,
                registry,
                verify_sha=False,
                evidence=ev,
            )
            chk("inspection_refuses_missing_manifest", False, {"detail": "no refusal"})
        except ContractRefusalError as exc:
            chk(
                "inspection_refuses_missing_manifest",
                "manifest not found" in str(exc) and "absent_manifest.json" in str(exc),
                {"detail": str(exc)},
            )
        bad_sha = json.loads(json.dumps(manifest))
        bad_sha["payload_sha256"]["selected_path_grid/2021-02-09"]["sha256"] = "0" * 64
        bad_sha.pop("core_hash")
        bad_sha["core_hash"] = sha256_bytes(canon_bytes(bad_sha))
        _, problems2 = run(write=canon_bytes(bad_sha))
        chk(
            "sha256_mismatch_reported",
            any(p["kind"] == "payload_integrity" and "mismatch" in p["detail"] for p in problems2),
            problems2,
        )
        rows.filter(pl.col("day") == "2021-02-09").with_columns(
            pl.lit(1.0).alias("v_hold_flat")
        ).write_parquet(grid / "2021-02-09.parquet")
        try:
            run(verify=False)
            chk("refuses_denied_physical_column", False)
        except ContractRefusalError:
            chk("refuses_denied_physical_column", True)

    # ---- chunked execution + merge (clean fixture: any partition reproduces the pass) ----
    import factory.scripts.basket_sim as basket_sim_mod

    with tempfile.TemporaryDirectory() as td2:
        root2, ev2 = Path(td2) / "data", Path(td2) / "evidence"
        grid2 = root2 / "grid.selected_paths/month=2021-02"
        grid2.mkdir(parents=True)
        ev2.mkdir()

        def entry2(key, rel, declared_rows):
            return {key: {"path": rel, "rows": declared_rows, "sha256": sha256_file(root2 / rel)}}

        rows2 = pl.DataFrame(
            {
                "day": [
                    "2021-02-09",
                    "2021-02-09",
                    "2021-02-09",
                    "2021-02-10",
                    "2021-02-11",
                    "2021-02-11",
                ],
                "ticker": ["AAA", "AAA", "BBB", "AAA", "AAA", "CCC"],
                "path_id": [
                    "2021-02-09|AAA",
                    "2021-02-09|AAA",
                    "2021-02-09|BBB",
                    "2021-02-10|AAA",
                    "2021-02-11|AAA",
                    "2021-02-11|CCC",
                ],
                "et": [570, 572, 571, 570, 570, 575],
                "bar_open": [10.0, 10.0, 20.0, 30.0, 40.0, 50.0],
                "bar_close": [11.0, 15.0, 19.0, 33.0, 44.0, 47.0],
                "bar_high": [12.0, 16.0, 21.0, 34.0, 45.0, 51.0],
                "bar_low": [9.5, 9.0, 18.5, 29.0, 39.0, 46.0],
                "bar_volume": [100.0, 150.0, 200.0, 300.0, 400.0, 500.0],
                "bar_state": ["observed"] * 6,
                "print_state": ["observed"] * 6,
                "session_end": [959] * 6,
                "within_session": [True] * 6,
                "first_entry_et": [570, 570, 571, 570, 570, 575],
                "n_prints": [5, 6, 6, 7, 8, 9],
                "coverage_class": ["complete_to_session_end"] * 6,
                "terminal_censored": [False] * 6,
            }
        )
        for day in ("2021-02-09", "2021-02-10", "2021-02-11"):
            rows2.filter(pl.col("day") == day).write_parquet(grid2 / f"{day}.parquet")
        pl.DataFrame(
            {
                "day": ["2021-02-09", "2021-02-10"],
                "member_id": ["2021-02-09|A_pm|AAA|570|1", "2021-02-10|A_pm|AAA|570|1"],
                "path_id": ["2021-02-09|AAA", "2021-02-10|AAA"],
                "family": ["A_pm", "A_pm"],
                "ticker": ["AAA", "AAA"],
                "entry_et": [570, 570],
                "entry_rank": [1, 1],
                "entry_px": [10.0, 30.0],
                "block": ["block1", "block1"],
                "month": ["2021-02", "2021-02"],
                "session_end": [959, 959],
            }
        ).write_parquet(root2 / "memberships.parquet")
        manifest2 = {
            "node_id": "synthetic",
            "status": "canary_built",
            "parents": {},
            "payload_sha256": {
                **entry2("memberships", "memberships.parquet", 2),
                **entry2(
                    "selected_path_grid/2021-02-09",
                    "grid.selected_paths/month=2021-02/2021-02-09.parquet",
                    3,
                ),
                **entry2(
                    "selected_path_grid/2021-02-10",
                    "grid.selected_paths/month=2021-02/2021-02-10.parquet",
                    1,
                ),
                **entry2(
                    "selected_path_grid/2021-02-11",
                    "grid.selected_paths/month=2021-02/2021-02-11.parquet",
                    2,
                ),
            },
        }
        manifest2["core_hash"] = sha256_bytes(canon_bytes(manifest2))
        (ev2 / "manifest.json").write_bytes(canon_bytes(manifest2))
        for n in ("schema.json", "contract.json", "causal_registry.json"):
            (ev2 / n).write_text("{}")
        mini2 = json.loads(json.dumps(insp))
        mini2["layers"] = {
            k: json.loads(json.dumps(insp["layers"][k]))
            for k in ("memberships", "selected_path_grid")
        }
        mini2["layers"]["memberships"].update(
            magnitude=["entry_px"], counts=[], rank=["entry_rank"], derived=[]
        )
        mini2["layers"]["selected_path_grid"].update(
            magnitude=["bar_close", "bar_high", "bar_low"],
            activity=["bar_volume", "n_prints"],
            counts=["first_entry_et"],
            staleness=[],
            rank=[],
            coordinates=["session_end", "bar_state", "print_state"],
            masks=["within_session"],
            derived=[
                {
                    "name": "bar_ret_from_open",
                    "op": "div_minus_1",
                    "inputs": ["bar_close", "bar_open"],
                }
            ],
        )
        mini2["rarity"] = {
            "rules": [
                {
                    "layer": "selected_path_grid",
                    "column": "bar_ret_from_open",
                    "kind": "magnitude",
                    "thresholds": [0.10],
                }
            ]
        }
        mini2["path_shape"] = [{"layer": "selected_path_grid", "key": "path_id", "span": "et"}]
        mini2["rank_change"] = [
            {"layer": "selected_path_grid", "key": "ticker", "time": "et", "column": "bar_close"}
        ]
        mini2["sampling"] = dict(insp["sampling"], reservoir_rows=2)  # frac = 2/6: subsampled
        mini2["frozen"] = True
        mini2["status"] = "frozen"

        def chunk_bytes(days):
            ctx, states, probs = scan_corpus(
                root2,
                ev2 / "manifest.json",
                mini2,
                schema,
                registry,
                verify_sha=True,
                evidence=ev2,
                days_subset=days,
            )
            ch = {
                "report_kind": "chunk",
                "report_version": mini2["contract_version"],
                "node_id": "observations.tape_atlas.blind-corpus-report.chunk",
                "status": "REFUSED" if probs else "complete",
                "context": ctx,
                "layers": states,
                "refusals": probs,
            }
            ch["chunk_sha256"] = sha256_bytes(canon_bytes(ch))
            return canon_bytes(ch) + b"\n"

        def merge(paths):
            return merge_chunk_reports(paths, ev2 / "manifest.json", mini2, schema, evidence=ev2)

        orig_dev = basket_sim_mod.dev_days
        basket_sim_mod.dev_days = lambda: ["2021-02-09", "2021-02-10", "2021-02-11"]
        try:
            plain, plain_problems = inspect_corpus(
                root2, ev2 / "manifest.json", mini2, schema, registry, verify_sha=True, evidence=ev2
            )
            import factory.scripts.basket_tape_atlas_inspect as inspect_mod

            saved_slice = inspect_mod.SCAN_SLICE
            inspect_mod.SCAN_SLICE = 1  # force every bounded aggregation across many slices
            try:
                sliced, _ = inspect_corpus(
                    root2, ev2 / "manifest.json", mini2, schema, registry, True, ev2
                )
            finally:
                inspect_mod.SCAN_SLICE = saved_slice
            chk(
                "slice_aggregation_is_partition_invariant",
                canon_bytes(sliced["layers"]) == canon_bytes(plain["layers"]),
            )
            chk(
                "coordinate_tallies_exact",
                plain["layers"]["selected_path_grid"]["by_coordinate"]["session_end"] == {"959": 6}
                and plain["layers"]["selected_path_grid"]["by_coordinate"]["bar_state"]
                == {"observed": 6},
                plain["layers"]["selected_path_grid"]["by_coordinate"],
            )
            (ev2 / "chunk1.json").write_bytes(chunk_bytes(["2021-02-09"]))
            (ev2 / "chunk2.json").write_bytes(chunk_bytes(["2021-02-10", "2021-02-11"]))
            merged, merged_problems = merge([str(ev2 / "chunk1.json"), str(ev2 / "chunk2.json")])
            c1 = load_json(ev2 / "chunk1.json")
            grid_plain = plain["layers"]["selected_path_grid"]
            grid_merged = merged["layers"]["selected_path_grid"]
            chk(
                "chunk_partition_merges_to_single_pass",
                canon_bytes(merged["layers"]) == canon_bytes(plain["layers"])
                and merged["identity"] == plain["identity"]
                and merged["day_set"] == plain["day_set"]
                and merged["status"] == "complete"
                and plain["status"] == "complete"
                and not merged_problems
                and not plain_problems,
                {"merged_frac": grid_merged["sampling"]["sampling_fraction"]},
            )
            chk(
                "partition_invariant_subsample",
                grid_plain["sampling"]["sampling_fraction"] < 1
                and grid_merged["sampling"]["sampled_rows"] == grid_plain["sampling"]["sampled_rows"]
                and grid_merged["sampling"]["rate_declared_rows"] == 6,
                grid_merged["sampling"],
            )
            rc_plain = grid_plain.get("rank_change")
            chk(
                "rank_change_counts_exact_not_accumulated",
                rc_plain is not None
                and rc_plain["exact_n"] == 1
                and rc_plain["positive"] == 1
                and rc_plain["zero"] == 0
                and rc_plain["negative"] == 0
                and grid_merged.get("rank_change") == rc_plain,
                rc_plain,
            )
            chk(
                "rank_change_quantiles_subsampled",
                rc_plain is not None
                and rc_plain["quantiles"]["n"] <= rc_plain["exact_n"]
                and rc_plain["quantiles"]["n"] <= grid_plain["sampling"]["sampled_rows"],
                rc_plain,
            )
            chk(
                "merged_reports_chunk_provenance",
                len(merged["chunk_provenance"]) == 2
                and merged["chunk_provenance"][0]["day_first"] == "2021-02-09"
                and merged["chunk_provenance"][0]["day_runs"] == [["2021-02-09", "2021-02-09"]]
                and merged["chunk_provenance"][1]["day_runs"] == [["2021-02-10", "2021-02-11"]]
                and "DECLARED" in (merged["sampling_rule"] or "")
                and "DECLARED" in (grid_merged["sampling"]["rate_rule"] or ""),
                merged["chunk_provenance"],
            )
            chk(
                "corpus_aggregates_counted_once",
                merged["sources"]["payloads"]["n_inspected"] == 4
                and merged["sources"]["payloads"]["n_chunks"] == 2,
                merged["sources"]["payloads"],
            )
            chk(
                "chunk_report_self_hash_verifies",
                c1["chunk_sha256"]
                == sha256_bytes(canon_bytes({k: v for k, v in c1.items() if k != "chunk_sha256"})),
            )
            merged_b, _ = merge([str(ev2 / "chunk1.json"), str(ev2 / "chunk2.json")])
            chk("merged_report_deterministic", canon_bytes(merged) == canon_bytes(merged_b))
            (ev2 / "chunk1b.json").write_bytes(chunk_bytes(["2021-02-09"]))
            chk(
                "chunk_report_deterministic",
                (ev2 / "chunk1.json").read_bytes() == (ev2 / "chunk1b.json").read_bytes(),
            )
            for name, paths, mutate in (
                ("merge_refuses_overlapping_days", [str(ev2 / "chunk1.json"), str(ev2 / "chunk1.json")], None),
                ("merge_refuses_incomplete_dayset", [str(ev2 / "chunk1.json")], None),
            ):
                try:
                    merge(paths)
                    chk(name, False, {"detail": "not refused"})
                except ContractRefusalError:
                    chk(name, True)
            try:
                scan_corpus(
                    root2, ev2 / "manifest.json", mini2, schema, registry, True, ev2,
                    days_subset=["2021-02-12"],
                )
                chk("chunk_days_not_in_manifest_refused", False, {"detail": "not refused"})
            except ContractRefusalError:
                chk("chunk_days_not_in_manifest_refused", True)
            try:
                scan_corpus(
                    root2, ev2 / "manifest.json", mini2, schema, registry, True, ev2,
                    days_subset=["2021-02-09", "2021-02-09"],
                )
                chk("chunk_days_duplicate_refused", False, {"detail": "not refused"})
            except ContractRefusalError:
                chk("chunk_days_duplicate_refused", True)
            try:
                merge([str(ev2 / "chunk1.json"), str(ev2 / "manifest.json")])
                chk("merge_refuses_non_chunk_report", False, {"detail": "not refused"})
            except ContractRefusalError:
                chk("merge_refuses_non_chunk_report", True)

            bad_rate = load_json(ev2 / "chunk2.json")
            bad_rate["layers"]["selected_path_grid"]["sampling"]["sampling_fraction"] = 0.125
            bad_rate.pop("chunk_sha256")
            bad_rate["chunk_sha256"] = sha256_bytes(canon_bytes(bad_rate))
            (ev2 / "chunk2_badrate.json").write_bytes(canon_bytes(bad_rate) + b"\n")
            try:
                merge([str(ev2 / "chunk1.json"), str(ev2 / "chunk2_badrate.json")])
                chk("merge_asserts_identical_rates", False, {"detail": "not refused"})
            except ContractRefusalError:
                chk("merge_asserts_identical_rates", True)

            bad_shared = load_json(ev2 / "chunk2.json")
            bad_shared["layers"]["memberships"]["by_day"]["2021-02-09"] = 999
            bad_shared.pop("chunk_sha256")
            bad_shared["chunk_sha256"] = sha256_bytes(canon_bytes(bad_shared))
            (ev2 / "chunk2_badshared.json").write_bytes(canon_bytes(bad_shared) + b"\n")
            try:
                merge([str(ev2 / "chunk1.json"), str(ev2 / "chunk2_badshared.json")])
                chk("merge_refuses_shared_layer_mismatch", False, {"detail": "not refused"})
            except ContractRefusalError:
                chk("merge_refuses_shared_layer_mismatch", True)

            bad_hash = load_json(ev2 / "chunk1.json")
            bad_hash["layers"]["selected_path_grid"]["scanned"] = 999  # stale embedded self-hash
            (ev2 / "chunk1_badhash.json").write_bytes(canon_bytes(bad_hash) + b"\n")
            try:
                merge([str(ev2 / "chunk1_badhash.json"), str(ev2 / "chunk2.json")])
                chk("merge_refuses_chunk_self_hash_mismatch", False, {"detail": "not refused"})
            except ContractRefusalError:
                chk("merge_refuses_chunk_self_hash_mismatch", True)

            refused_chunk = load_json(ev2 / "chunk1.json")
            refused_chunk["status"] = "REFUSED"
            refused_chunk["refusals"] = [
                {"kind": "payload_integrity", "path": "x.parquet", "detail": "declared payload absent"}
            ]
            refused_chunk.pop("chunk_sha256")
            refused_chunk["chunk_sha256"] = sha256_bytes(canon_bytes(refused_chunk))
            (ev2 / "chunk1_refused.json").write_bytes(canon_bytes(refused_chunk) + b"\n")
            refused_report, refused_problems = merge(
                [str(ev2 / "chunk1_refused.json"), str(ev2 / "chunk2.json")]
            )
            chk(
                "merge_carries_chunk_refusal",
                refused_report["status"] == "REFUSED"
                and len(refused_problems) == 1
                and refused_problems[0]["chunk"].endswith("chunk1_refused.json"),
                refused_problems,
            )
        finally:
            basket_sim_mod.dev_days = orig_dev

    for c in checks:
        print(
            ("PASS " if c["ok"] else "FAIL ") + c["check"] + ("" if c["ok"] else f"  {c['detail']}")
        )
    return 0 if all(c["ok"] for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
