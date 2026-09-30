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
    EXACT over all rows; numeric distributions come from a bounded deterministic hash
    subsample (Bernoulli at rate reservoir/declared-rows) whose sampling error is reported;
  * no timestamp and no run-varying measurement enters the report, so identical inputs give
    identical bytes.

Usage
-----
  .venv/bin/python factory/scripts/basket_tape_atlas_inspect.py --selftest
  .venv/bin/python factory/scripts/basket_tape_atlas_inspect.py \
      --root /home/hillel/projects/Alpacatrader/data/atlas/observation/v0 \
      --manifest factory/artifacts/basket/phase2/ATLAS/TAPE/OBSERVATION/v0/manifest.json \
      --out-evidence factory/artifacts/basket/phase2/ATLAS/TAPE/OBSERVATION/v0

Exit codes
----------
  0  corpus inspected, no integrity refusal
  2  contract refusal (illegal allowlist, evidence-hash mismatch, denied physical column,
     sealed/reserved day): nothing is read and no report is written
  3  integrity refusal (declared payload absent, sha256 mismatch, rows != manifest, parent
     drift, day set != dev_days): the report IS written with status REFUSED and every problem
  1  selftest failure

``--no-verify-payload-sha`` skips only the payload sha256 re-read (the row counts, the day
guard, the parents and the manifest core hash are always checked).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
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


def resolve_under(root: Path, rel: str) -> Path:
    r = Path(rel)
    if r.is_absolute() or ".." in r.parts:
        raise ContractRefusalError(f"payload path must be relative and traversal-free: {rel!r}")
    p = (root / r).resolve()
    if p != root.resolve() and root.resolve() not in p.parents:
        raise ContractRefusalError(f"payload path escapes --root: {rel!r}")
    return p


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


# --------------------------------- deterministic bounded reservoir --------- #
class Reservoir:
    def __init__(self, capacity: int, declared_rows: int, salt: np.uint64):
        self.cap, self.salt = int(capacity), salt
        self.frac = 1.0 if declared_rows <= 0 else min(1.0, self.cap / declared_rows)
        self.thr = np.uint64((1 << 64) - 1 if self.frac >= 1.0 else int(self.frac * 2**64))
        self.frames: list[pl.DataFrame] = []
        self.seen = 0

    def add(self, part_salt: np.uint64, df: pl.DataFrame, cols: list[str]) -> None:
        if not cols or df.height == 0:
            return
        h = mix64(np.arange(df.height, dtype=np.uint64) ^ part_salt ^ self.salt)
        self.seen += df.height
        keep = h < self.thr
        if not keep.any():
            return
        sub = df if keep.all() else df.filter(pl.Series(keep))
        self.frames.append(sub.select(cols).with_columns(pl.Series("__h", h[keep])))

    def sampled(self) -> pl.DataFrame | None:
        if not self.frames:
            return None
        df = pl.concat(self.frames) if len(self.frames) > 1 else self.frames[0]
        return (df.sort("__h").head(self.cap) if df.height > self.cap else df).drop("__h")

    @property
    def exact(self) -> bool:
        return self.seen <= self.cap


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


# ------------------------------------------------------------ scanner ------ #
def scan_layer(scan, lid, spec, fields, files, shape, rules, rank_change) -> tuple[dict, int]:
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
    res = Reservoir(scan["cfg"]["reservoir_rows"], declared, salt_of(scan["cfg"]["salt"], lid))
    nulls, trues, by_day = Counter(), Counter(), Counter()
    by_coord = {c: Counter() for c in coords}
    by_strata: dict[str, Counter] = {k: Counter() for k in ("block", "month", "feed_era")}
    shape_rows, rarity_ct, changes = {}, defaultdict(Counter), []
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
        res.add(salt_of(f["rel"]), df, num_all)
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
        for c in coords:
            sub = (
                df.with_columns(pl.col(c).list.join("|").alias(c)) if df[c].dtype == pl.List else df
            )
            for v, cnt in sub.group_by(c).len().iter_rows():
                by_coord[c][str(v)] += int(cnt)
        for rule in rules:
            for t in rule["thresholds"]:
                hits = int((df[rule["column"]] >= t).sum())
                key, day = f"{rule['column']}>={num(t)}", f["day"]
                rarity_ct[key]["total"] += hits
                rarity_ct[key][block_of(day) if day else "shared"] += hits
        if shape:
            for pid, rows, lo, hi in (
                df.group_by(shape["key"])
                .agg(
                    pl.len().alias("rows"),
                    pl.col(shape["span"]).min().alias("lo"),
                    pl.col(shape["span"]).max().alias("hi"),
                )
                .iter_rows()
            ):
                shape_rows[str(pid)] = (int(rows), int(lo), int(hi))
        if rank_change:
            d = df.sort([rank_change["key"], rank_change["time"]]).select(
                pl.col(rank_change["column"]).diff().over(rank_change["key"]).alias("__d")
            )
            changes.append(d.drop_nulls().to_series().to_numpy())
        if spec.get("accumulate"):
            for c in spec["accumulate"]:
                scan["vectors"][f"{lid}.{c}"].extend(df[c].to_list())
    scanned = int(sum(by_day.values()))
    meta = scan.get("day_meta") or {}
    for day, cnt in by_day.items():
        m = meta.get(day) or (None, None, None)
        by_strata["block"][block_of(day)] += cnt
        by_strata["month"][day[:7]] += cnt
        by_strata["feed_era"][str(m[0])] += cnt
    by_strata = {k: v for k, v in by_strata.items() if k not in coords}
    frames = res.sampled()
    section = {
        "table": spec["table"],
        "tier": spec["tier"],
        "partition": spec["partition"],
        "rows": {
            "declared_manifest": declared,
            "scanned": scanned,
            "match": declared == scanned,
            "n_days": len(by_day),
        },
        "sampling": {
            "reservoir_rows": res.cap,
            "sampled_rows": None if frames is None else frames.height,
            "sampling_fraction": num(res.frac),
            "exact_quantiles": res.exact,
        },
        "by_day": dict(sorted(by_day.items())),
        "by_strata": {k: dict(sorted(v.items())) for k, v in by_strata.items()},
        "by_coordinate": {c: dict(sorted(v.items())) for c, v in by_coord.items()},
        "masks": {
            c: {"true": trues[c], "false": scanned - trues[c] - nulls[c], "null": nulls[c]}
            for c in masks
        },
        "null_counts": {c: nulls[c] for c in dict.fromkeys(num_all + masks + coords + keys)},
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
                "null_rows": nulls[c],
                "present_rows": scanned - nulls[c],
            }
            for c in sorted(set(num_all + coords + masks + keys))
            if c in fields
        },
        "quantiles": {
            c: {
                "role": role,
                **quantiles(
                    frames[c].to_numpy()
                    if frames is not None and c in frames.columns
                    else np.array([]),
                    scan["cfg"]["quantile_probs"],
                    scan["cfg"]["quantile_ci"],
                ),
            }
            for role in NUM_ROLES + ("derived",)
            for c in nums[role]
        },
        "unobservable": unobservable(fields, spec, present, scan.get("derived_strata") or ()),
    }
    if shape:
        rp = np.array([v[0] for v in shape_rows.values()], float)
        sp = np.array([v[2] - v[1] for v in shape_rows.values()], float)
        section["path_shape"] = {
            "key": shape["key"],
            "span": shape["span"],
            "n_paths": len(shape_rows),
            "exact": True,
            "rows_per_path": quantiles(
                rp, scan["cfg"]["quantile_probs"], scan["cfg"]["quantile_ci"]
            ),
            "elapsed_minutes": quantiles(
                sp, scan["cfg"]["quantile_probs"], scan["cfg"]["quantile_ci"]
            ),
        }
    if rank_change:
        arr = np.concatenate(changes) if changes else np.array([])
        section["rank_change"] = {
            "key": rank_change["key"],
            "time": rank_change["time"],
            "column": rank_change["column"],
            "exact_n": int(arr.size),
            "positive": int((arr > 0).sum()),
            "zero": int((arr == 0).sum()),
            "negative": int((arr < 0).sum()),
            "quantiles": quantiles(arr, scan["cfg"]["quantile_probs"], scan["cfg"]["quantile_ci"]),
        }
    if rules:
        section["rarity"] = {k: dict(sorted(v.items())) for k, v in sorted(rarity_ct.items())}
    return section, problems


# ------------------------------------------------ evidence + orchestration - #
def inspect_corpus(
    root: Path,
    manifest_path: Path,
    insp: dict,
    schema: dict,
    registry: dict,
    verify_sha: bool,
    evidence: Path | None = None,
) -> tuple[dict, list]:
    evidence = Path(evidence) if evidence is not None else OBS_EVIDENCE
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

    index: dict[str, list[dict]] = defaultdict(list)
    uninspected, days = [], set()
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
            guard_day(day)  # sealed/reserved days refuse here
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

    present: dict[str, list[str]] = {}
    integrity: dict[str, str] = {}
    prefixes, exact = physical_deny(registry)
    for lid, files in index.items():
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
        want = {f["rel"] for f in index[lid]}
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
    sections, n_problems = {}, 0
    order = sorted(insp["layers"], key=lambda x: 0 if x == "day_registry" else 1)
    for lid in order:
        spec = insp["layers"][lid]
        section, p = scan_layer(
            scan,
            lid,
            spec,
            schema["tables"][spec["table"]]["fields"],
            index[lid],
            shape_by.get(lid),
            [r for r in insp.get("rarity", {}).get("rules") or [] if r["layer"] == lid],
            change_by.get(lid),
        )
        sections[lid], n_problems = section, n_problems + p
    if n_problems != sum(1 for s in integrity.values() if s != "ok"):
        raise ContractRefusalError("integrity accounting drifted between pass and scan")
    problems += [
        {
            "kind": "payload_rows_mismatch",
            "layer": lid,
            "declared": s["rows"]["declared_manifest"],
            "scanned": s["rows"]["scanned"],
        }
        for lid, s in sorted(sections.items())
        if not s["rows"]["match"]
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
    census = (
        load_json(evidence / "contract.json").get("identity", {}).get("expected_panel_census", {})
    )
    mem_paths = scan["vectors"].get("memberships.path_id", [])
    mem_ids = scan["vectors"].get("memberships.member_id", [])
    per_path = Counter(
        mem_paths
    )  # memberships.path_id is a prospective key; paths.n_memberships is not
    paths_rows = sections.get("paths", {}).get("rows", {}).get("scanned")
    n_shared = sum(1 for v in per_path.values() if v > 1)
    report = {
        "report_version": insp["contract_version"],
        "node_id": "observations.tape_atlas.blind-corpus-report",
        "status": "REFUSED" if problems else "complete",
        "sources": {
            "root": str(root),
            "manifest": str(manifest_path),
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
                "n_inspected": sum(len(v) for v in index.values()),
                "n_uninspected_keys": len(uninspected),
                "uninspected": uninspected[:20],
                "rows_total": sum(int(f["rows"] or 0) for v in index.values() for f in v),
                "files_verified_sha256": verify_sha,
            },
            "parents": parents,
        },
        "day_set": {
            "n_days": len(days),
            "dev_days_n": None if dev is None else len(dev),
            "equals_dev_days": None if dev is None else dev == sorted(days),
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
            "estimated": "numeric quantiles from a deterministic Bernoulli subsample with a 95% "
            "binomial order-statistic CI; exact when scanned rows <= reservoir_rows",
            "reservoir_rows": insp["sampling"]["reservoir_rows"],
        },
        "allowed_blind_summaries": load_json(evidence / "contract.json").get(
            "allowed_blind_summaries"
        ),
        "derived_strata": insp.get("derived_strata"),
        "layers": dict(sorted(sections.items())),
        "extra_undeclared_partitions": sorted(set(extra))[:50],
        "refusals": problems,
    }
    report["report_sha256"] = sha256_bytes(canon_bytes(report))
    return report, problems


# -------------------------------------------------------------- CLI -------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Freeze-O blind corpus inspection")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="absolute observation data root")
    ap.add_argument("--manifest", default=str(OBS_EVIDENCE / "manifest.json"))
    ap.add_argument("--out-evidence", default=None, help="directory for blind_corpus_report.json")
    ap.add_argument("--no-verify-payload-sha", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return run_selftest()
    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = ROOT / manifest_path
    insp = load_json(CONTRACT_PATH)
    try:
        report, problems = inspect_corpus(
            Path(args.root),
            manifest_path,
            insp,
            load_json(OBS_EVIDENCE / "schema.json"),
            load_json(OBS_EVIDENCE / "causal_registry.json"),
            verify_sha=not args.no_verify_payload_sha,
        )
    except ContractRefusalError as exc:  # illegal allowlist/evidence chain: no report is written
        print("inspection refusal: " + str(exc), file=sys.stderr)
        return 2
    out_dir = Path(args.out_evidence) if args.out_evidence else manifest_path.parent
    out_dir = out_dir if out_dir.is_absolute() else ROOT / out_dir
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
    for c in checks:
        print(
            ("PASS " if c["ok"] else "FAIL ") + c["check"] + ("" if c["ok"] else f"  {c['detail']}")
        )
    return 0 if all(c["ok"] for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
