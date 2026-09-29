#!/usr/bin/env python3
"""ATLAS Freeze-O blocker B6 — SIP net DERIVED manifest-index reconciliation.

B6: the net manifest index (``data/sip/net/manifest_index.jsonl``) has no record at
all for 2026-05-21 and 2026-05-29 although the day files exist, and a further set
of index rows was written before the current per-day manifests existed. The index
is a DERIVED artifact: it is exactly the concatenation of the per-day manifests
under ``<root>/*/*.manifest.json``. This tool therefore repairs the index and
nothing else.

Rules this tool obeys (all enforced, none configurable):

  * source files are never mutated - only the derived index is replaced, and only
    through write-tmp + os.replace, so a reader never sees a partial index;
  * the rebuild reads per-day MANIFESTS only. No parquet is ever opened for data
    (a sealed day's entry may carry its existing metadata; the sealed bytes are
    never touched), and the tool performs no network fetch;
  * every target day is guarded before any path is opened (basket_sim.guard_day:
    sealed prefixes 2024-*, 2025-01 and reserved months 2026-06/07/08 are
    refused) and must be a canonical dev day;
  * a day is only complete when its bars/trades/quotes/coverage files exist and
    verify byte-for-byte against their per-day manifests (trades/quotes sha256,
    coverage src_sha256 -> trades file), every status is "ok", the provider was
    not unavailable, and the rebuilt index carries all three of its manifest
    records;
  * the shared evidence file is written only when the net contract's parent
    shas match exactly AND every target entry is complete. A partial or blocked
    run prints its report and exits non-zero without touching the evidence path.

Usage:
  factory/scripts/atlas_net_index_reconcile.py --selftest
  factory/scripts/atlas_net_index_reconcile.py --stage inspect
  factory/scripts/atlas_net_index_reconcile.py --stage repair            # rebuild + emit
  factory/scripts/atlas_net_index_reconcile.py --stage repair --dry-run  # preview only
  factory/scripts/atlas_net_index_reconcile.py --stage verify            # re-check + emit
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
try:  # canonical guard/calendar source (no data root needed at import)
    from factory.scripts import basket_sim as sim
except ImportError:  # direct script execution
    sys.path.insert(0, str(ROOT))
    from factory.scripts import basket_sim as sim

CODE_ID = "atlas-net-index-reconcile-v0"
CONTRACT_ID = "atlas.net_index_reconcile"
CONTRACT_SCHEMA = "atlas/net_contract/v0"
EVIDENCE_SCHEMA = "atlas/net_reconciliation/v0"
EVIDENCE_VERSION = "v0"
BLOCKER_ID = "B6_missing_net_manifest_2026_05_21_29"
ACQ_REL = "factory/artifacts/basket/phase2/ATLAS/TAPE/OBSERVATION/v0/acquisition"
CONTRACT_REL = f"{ACQ_REL}/net_contract.json"
EVIDENCE_REL = f"{ACQ_REL}/net_reconciliation.json"
INDEX_NAME = "manifest_index.jsonl"
DEFAULT_DAYS = ("2026-05-21", "2026-05-29")
# index record kinds, in canonical rebuild order (day, kind rank, relpath)
MANIFEST_KINDS = ("coverage", "quotes", "trades")
KIND_RANK = {k: i for i, k in enumerate(MANIFEST_KINDS)}
# physical per-day file per kind (bars are derived; coverage ships a .json census)
DATA_SUFFIX = {"bars": ".parquet", "coverage": ".json", "quotes": ".parquet", "trades": ".parquet"}


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def now_utc() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj: Any) -> str:
    """Canonical one-line encoding: the byte form the derived index is rebuilt in."""
    return json.dumps(obj, separators=(",", ":"), sort_keys=True)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_bytes(path, (json.dumps(obj, indent=1, sort_keys=True) + "\n").encode())


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def default_net_root() -> Path:
    """Resolve the net root (data/sip/net) from the usual market-data locations."""
    cands: list[Path] = []
    env = os.environ.get("BASKET_DATA_ROOT")
    if env:
        cands.append(Path(env))
    cands += [ROOT / "data", Path("/home/hillel/projects/Alpacatrader/data")]
    for c in cands:
        if (c / "sip" / "net" / "bars").is_dir():
            return (c / "sip" / "net").resolve()
    raise FileNotFoundError(f"no net root under {[str(c) for c in cands]}")


def index_kind(rec: dict) -> str:
    """Kind of an index record (coverage rows carry the day census, not a `kind`)."""
    if "kind" in rec:
        return str(rec["kind"])
    return "coverage"


def parse_days(text: str) -> list[str]:
    return [d.strip() for d in text.replace(" ", ",").split(",") if d.strip()]


# --------------------------------------------------------------------------- #
# reconciler
# --------------------------------------------------------------------------- #
class Reconciler:
    """Verify guarded dev days and reconcile the derived index against manifests."""

    def __init__(
        self,
        root: Path,
        days: list[str],
        dev_days: set[str] | None = None,
    ) -> None:
        self.root = Path(root)
        self.days = list(days)
        self.index = self.root / INDEX_NAME
        self._dev = None if dev_days is None else set(dev_days)

    # -- guards ------------------------------------------------------------- #
    def dev_day_set(self) -> set[str]:
        if self._dev is None:
            self._dev = set(sim.dev_days())
        return self._dev

    def guard_days(self) -> list[str]:
        """Refuse sealed/reserved days and non-dev days before any path is opened."""
        dev = self.dev_day_set()
        for day in self.days:
            sim.guard_day(day)
            if day not in dev:
                raise ValueError(
                    f"{day} is not a canonical guarded dev day "
                    f"(basket_sim.dev_days(); net-only day - it is never a target)"
                )
        return list(self.days)

    # -- per-day physical + manifest facts ---------------------------------- #
    def _file(self, kind: str, day: str) -> dict:
        rel = f"{kind}/{day}{DATA_SUFFIX[kind]}"
        p = self.root / rel
        rec: dict[str, Any] = {
            "path": rel,
            "present": p.is_file(),
            "bytes": None,
            "sha256": None,
            "hash_mode": "sha256_bytes",
            "status": "missing",
        }
        if not rec["present"]:
            return rec
        try:
            size = p.stat().st_size
        except OSError as exc:  # unreadable file: a corrupt source, never a silent pass
            rec["status"] = f"unreadable:{type(exc).__name__}"
            return rec
        rec["bytes"] = size
        if size == 0:
            rec["status"] = "empty"
            return rec
        rec["sha256"] = sha256_file(p)
        rec["status"] = "ok"
        return rec

    def _manifest(self, kind: str, day: str) -> dict:
        rel = f"{kind}/{day}.manifest.json"
        p = self.root / rel
        rec: dict[str, Any] = {
            "path": rel,
            "present": p.is_file(),
            "sha256": None,
            "declared_day": None,
            "status": "missing",
            "record": None,
        }
        if not rec["present"]:
            return rec
        rec["sha256"] = sha256_file(p)
        try:
            obj = load_json(p)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            rec["status"] = f"unparsable:{type(exc).__name__}"
            return rec
        if not isinstance(obj, dict):
            rec["status"] = "not_an_object"
            return rec
        rec["record"] = obj
        rec["declared_day"] = obj.get("day")
        rec["status"] = str(obj.get("status", "unknown"))
        return rec

    def day_entry(self, day: str) -> dict:
        """Verify one day against its manifests; never opens a parquet for data."""
        files = {k: self._file(k, day) for k in ("bars", "coverage", "quotes", "trades")}
        manifests = {k: self._manifest(k, day) for k in MANIFEST_KINDS}
        problems: list[str] = []
        facts: dict[str, Any] = {}

        for kind in ("bars", "coverage", "quotes", "trades"):
            st = files[kind]["status"]
            if st != "ok":
                problems.append(f"file_{st}:{files[kind]['path']}")

        for kind in MANIFEST_KINDS:
            m = manifests[kind]
            if not m["present"]:
                problems.append(f"manifest_missing:{m['path']}")
                continue
            if m["record"] is None:
                problems.append(f"manifest_{m['status']}:{m['path']}")
                continue
            if m["declared_day"] != day:
                problems.append(f"manifest_day_mismatch:{m['path']}={m['declared_day']}")
            if m["status"] != "ok":
                problems.append(f"manifest_status_{m['status']}:{m['path']}")
            rec = m["record"]
            if kind == "coverage":
                if rec.get("provider_unavailable"):
                    problems.append("provider_unavailable")
                facts["symbols"] = rec.get("symbols")
                facts["rows_merged"] = rec.get("rows_merged")
                facts["classes"] = rec.get("classes")
                facts["source_trades"] = rec.get("source_trades")
            else:
                facts[f"{kind}_rows"] = rec.get("rows")
                facts[f"{kind}_symbols_requested"] = rec.get("symbols_requested")
                facts[f"{kind}_symbols_with_data"] = rec.get("symbols_with_data")
                facts[f"{kind}_errors"] = rec.get("errors") or []
                declared = rec.get("sha256")
                if files[kind]["status"] == "ok" and declared != files[kind]["sha256"]:
                    problems.append(
                        f"sha256_mismatch:{files[kind]['path']} "
                        f"manifest={declared} actual={files[kind]['sha256']}"
                    )

        # derived-bars provenance: the coverage manifest pins its source trades file
        cov = manifests["coverage"]["record"]
        if isinstance(cov, dict) and cov.get("src_sha256"):
            actual = files["trades"]["sha256"]
            if actual is not None and cov["src_sha256"] != actual:
                problems.append(
                    f"coverage_source_sha_mismatch:{cov.get('src_sha256')} trades={actual}"
                )

        census_ok = False
        cov_json = self.root / files["coverage"]["path"]
        if files["coverage"]["status"] == "ok" and cov_json.is_file():
            try:
                census = load_json(cov_json)
                census_ok = isinstance(census, dict) and census.get("day") == day
            except (json.JSONDecodeError, UnicodeDecodeError):
                census_ok = False
            if not census_ok:
                problems.append(f"coverage_census_invalid:{files['coverage']['path']}")

        return {
            "day": day,
            "status": "ok" if not problems else "incomplete",
            "complete": not problems,
            "verified": not problems,
            "files": files,
            "manifests": {
                k: {kk: vv for kk, vv in m.items() if kk != "record"} for k, m in manifests.items()
            },
            "counts": facts,
            "problems": problems,
        }

    # -- derived index ------------------------------------------------------ #
    def manifest_inventory(self) -> dict[tuple[str, str], dict]:
        """(day, kind) -> {relpath, record} for every per-day manifest under the root."""
        inv: dict[tuple[str, str], dict] = {}
        for p in sorted(self.root.glob("*/*.manifest.json")):
            kind = p.parent.name
            if kind not in MANIFEST_KINDS:
                continue
            obj = load_json(p)
            day = obj.get("day")
            if not isinstance(day, str):
                raise ValueError(f"manifest without a day id: {p}")
            if kind != "coverage" and obj.get("kind") != kind:
                raise ValueError(f"manifest kind disagrees with its directory: {p}")
            key = (day, kind)
            if key in inv:
                raise ValueError(f"duplicate manifest for {key}: {inv[key]['relpath']} and {p}")
            inv[key] = {"relpath": p.relative_to(self.root).as_posix(), "record": obj}
        return inv

    def canonical_index_bytes(self, inv: dict[tuple[str, str], dict] | None = None) -> bytes:
        """Deterministic rebuild: manifests only, sorted by (day, kind rank, relpath)."""
        if inv is None:
            inv = self.manifest_inventory()
        out = bytearray()
        for key in sorted(inv, key=lambda k: (k[0], KIND_RANK[k[1]], inv[k]["relpath"])):
            out += (canonical_json(inv[key]["record"]) + "\n").encode()
        return bytes(out)

    def read_index(self) -> dict:
        if not self.index.is_file():
            return {"present": False, "sha256": None, "rows": 0, "records": {}}
        data = self.index.read_bytes()
        recs: dict[tuple[str, str], dict] = {}
        rows = 0
        for lineno, line in enumerate(data.decode().splitlines(), 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"corrupt derived index at {self.index}:{lineno}: {exc}") from exc
            if not isinstance(rec, dict) or not isinstance(rec.get("day"), str):
                raise ValueError(f"corrupt derived index record at {self.index}:{lineno}")
            recs[(rec["day"], index_kind(rec))] = rec
            rows += 1
        return {"present": True, "sha256": sha256_bytes(data), "rows": rows, "records": recs}

    def index_facts(self, index: dict, inv: dict[tuple[str, str], dict]) -> dict:
        recs = index["records"]
        missing = sorted(k for k in inv if k not in recs)
        orphan = sorted(k for k in recs if k not in inv)
        stale = sorted(
            k
            for k in inv
            if k in recs and canonical_json(recs[k]) != canonical_json(inv[k]["record"])
        )
        return {
            "rows": index["rows"],
            "manifests_expected": len(inv),
            "missing_rows": [f"{d}/{k}" for d, k in missing],
            "stale_rows": [f"{d}/{k}" for d, k in stale],
            "orphan_rows": [f"{d}/{k}" for d, k in orphan],
            "days_missing_from_index": sorted({d for d, _ in missing}),
            "days_stale_in_index": sorted({d for d, _ in stale}),
        }

    def index_snapshot(self) -> dict:
        inv = self.manifest_inventory()
        index = self.read_index()
        return {
            "path": INDEX_NAME,
            "present": index["present"],
            "sha256": index["sha256"],
            "canonical_sha256": sha256_bytes(self.canonical_index_bytes(inv)),
            **self.index_facts(index, inv),
        }

    # -- stages ------------------------------------------------------------- #
    def inspect(self) -> dict:
        days = self.guard_days()
        entries = {d: self.day_entry(d) for d in days}
        inv = self.manifest_inventory()
        index = self.read_index()
        for day in days:
            self._attach_index_facts(entries[day], index, inv, after=None)
        snap = self.index_snapshot()
        return {
            "stage": "inspect",
            "root": str(self.root),
            "target_days": days,
            "derived_index": snap,
            "days": entries,
            "mutations": 0,
            "sealed_parquet_reads": 0,
            "fetches": 0,
        }

    def _attach_index_facts(
        self,
        entry: dict,
        index: dict,
        inv: dict[tuple[str, str], dict],
        after: dict | None,
    ) -> None:
        recs = index["records"]
        before_rows: list[str] = []
        after_rows: list[str] = []
        stale_before: list[str] = []
        stale_after: list[str] = []
        for kind in MANIFEST_KINDS:
            key = (entry["day"], kind)
            if key in recs:
                before_rows.append(kind)
                if key in inv and canonical_json(recs[key]) != canonical_json(inv[key]["record"]):
                    stale_before.append(kind)
        if after is not None:
            arecs = after["records"]
            for kind in MANIFEST_KINDS:
                key = (entry["day"], kind)
                if key in arecs:
                    after_rows.append(kind)
                    if key in inv and canonical_json(arecs[key]) != canonical_json(
                        inv[key]["record"]
                    ):
                        stale_after.append(kind)
        entry["index_rows"] = {
            "present_before": sorted(before_rows),
            "present_after": sorted(after_rows) if after is not None else None,
            "stale_before": sorted(stale_before),
            "stale_after": sorted(stale_after) if after is not None else None,
            "record_count": len(before_rows),
        }

    def repair(self, dry_run: bool = False) -> dict:
        """Verify every target day, then replace the derived index atomically."""
        days = self.guard_days()
        entries = {d: self.day_entry(d) for d in days}
        inv = self.manifest_inventory()
        before_index = self.read_index()
        for day in days:
            self._attach_index_facts(entries[day], before_index, inv, after=None)
        snapshot = self.index_snapshot()
        blocked = [f"{d}: {p}" for d, e in entries.items() for p in e["problems"]]
        new_bytes = self.canonical_index_bytes(inv)
        would_change = not (self.index.is_file() and self.index.read_bytes() == new_bytes)
        if blocked:
            return {
                "stage": "repair",
                "root": str(self.root),
                "target_days": days,
                "status": "blocked",
                "blocked": blocked,
                "derived_index": {**snapshot, "rebuilt": False, "atomic_replace": False},
                "days": entries,
                "mutations": 0,
                "sealed_parquet_reads": 0,
                "fetches": 0,
            }
        for day in days:
            self._attach_index_facts(
                entries[day], before_index, inv, after=None if dry_run else before_index
            )
        if dry_run or not would_change:
            return {
                "stage": "repair",
                "root": str(self.root),
                "target_days": days,
                "status": "inspected" if dry_run else "ok",
                "derived_index": {
                    **snapshot,
                    "rebuilt": False,
                    "atomic_replace": False,
                    "would_change": would_change,
                    "reason": "dry-run" if dry_run else "already_canonical",
                },
                "days": entries,
                "mutations": 0,
                "sealed_parquet_reads": 0,
                "fetches": 0,
            }
        atomic_write_bytes(self.index, new_bytes)
        after_index = self.read_index()
        for day in days:
            self._attach_index_facts(entries[day], before_index, inv, after=after_index)
        return {
            "stage": "repair",
            "root": str(self.root),
            "target_days": days,
            "status": "repaired",
            "derived_index": {**self.index_snapshot(), "rebuilt": True, "atomic_replace": True},
            "pre_repair": snapshot,
            "days": entries,
            "mutations": 1,
            "sealed_parquet_reads": 0,
            "fetches": 0,
        }

    def verify(self) -> dict:
        """Read-only: the index must already be the canonical manifest rebuild."""
        days = self.guard_days()
        entries = {d: self.day_entry(d) for d in days}
        inv = self.manifest_inventory()
        index = self.read_index()
        snapshot = self.index_snapshot()
        blocked = [f"{d}: {p}" for d, e in entries.items() for p in e["problems"]]
        if not index["present"]:
            blocked.append(f"derived_index_missing:{INDEX_NAME}")
        elif snapshot["sha256"] != snapshot["canonical_sha256"]:
            blocked.append("derived_index_not_canonical")
        blocked += [
            f"index_row_missing:{d}/{k}"
            for d in days
            for k in MANIFEST_KINDS
            if (d, k) not in index["records"]
        ]
        for day in days:
            self._attach_index_facts(entries[day], index, inv, after=index)
        return {
            "stage": "verify",
            "root": str(self.root),
            "target_days": days,
            "status": "ok" if not blocked else "blocked",
            "blocked": blocked,
            "derived_index": {**snapshot, "rebuilt": False, "atomic_replace": False},
            "days": entries,
            "mutations": 0,
            "sealed_parquet_reads": 0,
            "fetches": 0,
        }


# --------------------------------------------------------------------------- #
# contract + evidence
# --------------------------------------------------------------------------- #
def contract_path(args: argparse.Namespace) -> Path:
    return Path(args.contract).resolve() if args.contract else ROOT / CONTRACT_REL


def check_contract(path: Path, days: list[str]) -> tuple[dict, list[str]]:
    """Contract identity, declared targets and exact parent shas (drift is fatal)."""
    problems: list[str] = []
    if not path.is_file():
        return {}, [f"contract_missing:{path}"]
    doc = load_json(path)
    if doc.get("contract_id") != CONTRACT_ID:
        problems.append(f"contract_id_mismatch:{doc.get('contract_id')}")
    if doc.get("schema") != CONTRACT_SCHEMA:
        problems.append(f"contract_schema_mismatch:{doc.get('schema')}")
    if doc.get("code_id") != CODE_ID:
        problems.append(f"contract_code_id_mismatch:{doc.get('code_id')}")
    declared = list(doc.get("target_days") or [])
    for day in days:
        if day not in declared:
            problems.append(f"undeclared_target_day:{day}")
    parents = doc.get("parent_shas") or {}
    if not parents:
        problems.append("contract_parent_shas_missing")
    for rel, sha in sorted(parents.items()):
        f = Path(rel) if os.path.isabs(rel) else ROOT / rel
        if not f.is_file():
            problems.append(f"parent_missing:{rel}")
            continue
        actual = sha256_file(f)
        if actual != sha:
            problems.append(f"parent_sha_drift:{rel} declared={sha} actual={actual}")
    return doc, problems


def evidence_doc(report: dict, contract: dict, contract_file: Path) -> dict:
    """Assemble the shared evidence document (only ever called on a clean report)."""
    snap = report["derived_index"]
    pre = report.get("pre_repair")
    entries = {}
    for day, e in report["days"].items():
        rows = e["index_rows"]
        entries[day] = {
            "day": day,
            "status": e["status"],
            "complete": e["complete"],
            "verified": e["verified"],
            "files": e["files"],
            "manifests": e["manifests"],
            "counts": e["counts"],
            "index_rows": {
                "present_before": rows["present_before"],
                "present_after": rows["present_after"],
                "record_count": rows["record_count"],
                "stale_before": rows["stale_before"],
                "stale_after": rows["stale_after"],
            },
            "problems": e["problems"],
        }
    return {
        "schema": EVIDENCE_SCHEMA,
        "version": EVIDENCE_VERSION,
        "code_id": CODE_ID,
        "blocker_id": BLOCKER_ID,
        "generated_at_utc": now_utc(),
        "stages_run": [report["stage"]],
        "root": report["root"],
        "target_days": report["target_days"],
        "status": "ok",
        "derived_index": {
            "path": INDEX_NAME,
            "sha256": snap["sha256"],
            "sha256_before": (pre or snap)["sha256"],
            "sha256_after": snap["sha256"],
            "canonical_sha256": snap["canonical_sha256"],
            "rows": snap["rows"],
            "rows_before": (pre or snap)["rows"],
            "rows_after": snap["rows"],
            "manifests_expected": snap["manifests_expected"],
            "rebuilt": bool(snap.get("rebuilt")),
            "atomic_replace": bool(snap.get("atomic_replace")),
            "derived_only": True,
            "source_files_mutated": False,
            "pre_repair_snapshot": (
                {
                    "sha256": pre["sha256"],
                    "rows": pre["rows"],
                    "missing_rows": pre["missing_rows"],
                    "stale_rows": pre["stale_rows"],
                    "orphan_rows": pre["orphan_rows"],
                    "days_missing_from_index": pre["days_missing_from_index"],
                    "days_stale_in_index": pre["days_stale_in_index"],
                }
                if pre
                else None
            ),
        },
        "days": entries,
        "crosscheck": {
            "manifests_on_disk": snap["manifests_expected"],
            "index_rows_actual": snap["rows"],
            "index_rows_expected": snap["manifests_expected"],
            "index_rows_missing": snap["missing_rows"],
            "index_rows_stale": snap["stale_rows"],
            "index_rows_orphan": snap["orphan_rows"],
            "days_missing_from_index_after": snap["days_missing_from_index"],
            "days_stale_in_index_after": snap["days_stale_in_index"],
            "index_is_canonical_rebuild": snap["sha256"] == snap["canonical_sha256"],
            "sealed_parquet_reads": 0,
            "fetches": 0,
            "source_files_mutated": False,
        },
        "parent_shas": {
            "net_contract.json": sha256_file(contract_file),
            "factory/scripts/atlas_net_index_reconcile.py": sha256_file(HERE),
            "factory/scripts/basket_sim.py": sha256_file(HERE.parent / "basket_sim.py"),
            **{
                k: v
                for k, v in sorted((contract.get("parent_shas") or {}).items())
                if k not in ("factory/scripts/atlas_net_index_reconcile.py",)
            },
        },
        "contract": {
            "contract_id": contract.get("contract_id"),
            "version": contract.get("version"),
            "target_days": contract.get("target_days"),
        },
    }


# --------------------------------------------------------------------------- #
# synthetic self-test
# --------------------------------------------------------------------------- #
def _fake_manifest(root: Path, day: str, kind: str, **extra: Any) -> None:
    (root / kind).mkdir(parents=True, exist_ok=True)
    payload = b"PAR1-not-really-parquet-" + day.encode() + kind.encode()
    (root / kind / f"{day}.parquet").write_bytes(payload)
    rec = {
        "status": "ok",
        "day": day,
        "kind": kind,
        "file": f"{kind}/{day}.parquet",
        "rows": 10,
        "symbols_requested": 2,
        "symbols_with_data": 2,
        "errors": [],
        "sha256": sha256_file(root / kind / f"{day}.parquet"),
        **extra,
    }
    (root / kind / f"{day}.manifest.json").write_text(json.dumps(rec, indent=1) + "\n")


def _build_fake_root(root: Path, day: str) -> None:
    """A synthetic day with bars + coverage census + trades/quotes manifests."""
    (root / "bars").mkdir(parents=True, exist_ok=True)
    (root / "bars" / f"{day}.parquet").write_bytes(b"PAR1-bars-" + day.encode())
    _fake_manifest(root, day, "trades")
    _fake_manifest(root, day, "quotes")
    (root / "coverage").mkdir(parents=True, exist_ok=True)
    (root / "coverage" / f"{day}.json").write_text(
        json.dumps({"day": day, "symbols": 2, "per_symbol": {}}, indent=1) + "\n"
    )
    (root / "coverage" / f"{day}.manifest.json").write_text(
        json.dumps(
            {
                "day": day,
                "status": "ok",
                "symbols": 2,
                "rows_merged": 20,
                "classes": {"healthy_raw": 2, "provider_only": 0, "unresolved": 0},
                "provider_unavailable": False,
                "src_sha256": sha256_file(root / "trades" / f"{day}.parquet"),
            },
            indent=1,
        )
        + "\n"
    )


def selftest() -> None:
    import tempfile

    day = "2023-03-15"  # a real, non-sealed dev day
    other = "2023-03-16"

    # 1. negative control: a wrong hash in a manifest is rejected, never repaired
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "net"
        _build_fake_root(root, day)
        man = root / "trades" / f"{day}.manifest.json"
        rec = json.loads(man.read_text())
        rec["sha256"] = "0" * 64
        man.write_text(json.dumps(rec, indent=1) + "\n")
        (root / "manifest_index.jsonl").write_bytes(b"")
        rec_ = Reconciler(root, [day], dev_days={day, other})
        entry = rec_.day_entry(day)
        assert not entry["complete"], entry
        assert any(p.startswith("sha256_mismatch:trades/") for p in entry["problems"]), entry
        rep = rec_.repair()
        assert rep["status"] == "blocked", rep["status"]
        assert rep["mutations"] == 0 and not (root / "manifest_index.jsonl").read_bytes()
        assert rec_.inspect()["days"][day]["problems"] == entry["problems"]

    # 2. negative control: a missing kind (quotes manifest absent) blocks the day
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "net"
        _build_fake_root(root, day)
        (root / "quotes" / f"{day}.manifest.json").unlink()
        rec_ = Reconciler(root, [day], dev_days={day, other})
        problems = rec_.day_entry(day)["problems"]
        assert any(p.startswith("manifest_missing:quotes/") for p in problems), problems
        assert rec_.repair()["status"] == "blocked"

    # 3. a missing physical file (bars deleted) blocks the day
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "net"
        _build_fake_root(root, day)
        (root / "bars" / f"{day}.parquet").unlink()
        entry = Reconciler(root, [day], dev_days={day, other}).day_entry(day)
        assert any(p.startswith("file_missing:bars/") for p in entry["problems"]), entry["problems"]

    # 4. stale rows: an index carrying pre-manifest content fails verify, then
    #    repair rebuilds it deterministically and verify passes
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "net"
        _build_fake_root(root, day)
        stale = {"day": day, "status": "ok", "symbols": 99, "rows_merged": 1}
        partial = {"day": day, "status": "ok", "kind": "trades", "rows": 7}
        (root / "manifest_index.jsonl").write_text(
            canonical_json(stale) + "\n" + canonical_json(partial) + "\n"
        )
        rec_ = Reconciler(root, [day], dev_days={day, other})
        snap = rec_.inspect()["derived_index"]
        assert snap["stale_rows"] == [f"{day}/coverage", f"{day}/trades"], snap
        assert snap["missing_rows"] == [f"{day}/quotes"], snap
        blocked = rec_.verify()
        assert blocked["status"] == "blocked", blocked
        assert "derived_index_not_canonical" in blocked["blocked"], blocked["blocked"]
        rep = rec_.repair()
        assert rep["status"] == "repaired", rep["status"]
        assert rep["mutations"] == 1 and rep["derived_index"]["rebuilt"]
        assert rep["pre_repair"]["missing_rows"] == [f"{day}/quotes"]
        ver = rec_.verify()
        assert ver["status"] == "ok", ver
        assert ver["derived_index"]["sha256"] == ver["derived_index"]["canonical_sha256"]
        first = sha256_file(root / "manifest_index.jsonl")
        again = rec_.repair()
        assert again["status"] == "ok" and again["mutations"] == 0, again["status"]
        assert again["derived_index"]["reason"] == "already_canonical"
        assert rec_.repair(dry_run=True)["status"] == "inspected"
        assert sha256_file(root / "manifest_index.jsonl") == first  # byte-identical rebuild
        lines = (root / "manifest_index.jsonl").read_text().splitlines()
        assert len(lines) == 3, lines
        assert [json.loads(x)["day"] for x in lines] == [day] * 3
        kinds = [index_kind(json.loads(x)) for x in lines]
        assert kinds == ["coverage", "quotes", "trades"], kinds
        # source files were never touched by the repair
        assert sha256_file(root / "bars" / f"{day}.parquet") is not None
        assert json.loads((root / "trades" / f"{day}.manifest.json").read_text())["rows"] == 10

    # 5. sealed / reserved / non-dev targets are refused before any path is opened
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "net"
        _build_fake_root(root, day)
        for sealed in ("2024-03-15", "2025-01-15", "2026-06-15", "2026-08-01"):
            try:
                Reconciler(root, [sealed], dev_days={day}).guard_days()
            except PermissionError:
                pass
            else:
                raise AssertionError(f"sealed/reserved day not refused: {sealed}")
        try:
            Reconciler(root, [other], dev_days={day}).guard_days()
        except ValueError as exc:
            assert "not a canonical guarded dev day" in str(exc), exc
        else:
            raise AssertionError("non-dev day not refused")
        assert not (root / "manifest_index.jsonl").exists()  # refused stages write nothing

    # 6. contract parent-sha drift is fatal for evidence
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "net"
        _build_fake_root(root, day)
        cpath = Path(td) / "net_contract.json"
        cpath.write_text(
            json.dumps(
                {
                    "contract_id": CONTRACT_ID,
                    "schema": CONTRACT_SCHEMA,
                    "code_id": CODE_ID,
                    "target_days": [day],
                    "parent_shas": {"factory/scripts/basket_sim.py": "0" * 64},
                },
                indent=1,
            )
        )
        _, problems = check_contract(cpath, [day])
        assert any(p.startswith("parent_sha_drift:") for p in problems), problems
        _, problems = check_contract(cpath, [other])
        assert "undeclared_target_day:" + other in problems, problems
        _, problems = check_contract(cpath, [])
        assert any(p.startswith("parent_sha_drift:") for p in problems), problems

    # 7. a corrupt index line and a kind/directory disagreement both block loudly
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "net"
        _build_fake_root(root, day)
        (root / "manifest_index.jsonl").write_text('{"day": "' + day + '", "kind":\n')
        try:
            Reconciler(root, [day], dev_days={day}).verify()
        except ValueError as exc:
            assert "corrupt derived index" in str(exc), exc
        else:
            raise AssertionError("corrupt index accepted")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "net"
        _build_fake_root(root, day)
        man = root / "trades" / f"{day}.manifest.json"
        rec = json.loads(man.read_text())
        rec["kind"] = "quotes"
        man.write_text(json.dumps(rec, indent=1) + "\n")
        try:
            Reconciler(root, [day], dev_days={day}).index_snapshot()
        except ValueError as exc:
            assert "kind disagrees with its directory" in str(exc), exc
        else:
            raise AssertionError("kind/directory disagreement accepted")

    # 8. evidence is emitted for a clean run only, and carries the reconciliation facts
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "net"
        _build_fake_root(root, day)
        cpath = Path(td) / "net_contract.json"
        cpath.write_text(
            json.dumps(
                {
                    "contract_id": CONTRACT_ID,
                    "schema": CONTRACT_SCHEMA,
                    "code_id": CODE_ID,
                    "version": EVIDENCE_VERSION,
                    "target_days": [day],
                    "parent_shas": {
                        "factory/scripts/basket_sim.py": sha256_file(HERE.parent / "basket_sim.py")
                    },
                },
                indent=1,
            )
        )
        out = Path(td) / "net_reconciliation.json"
        rc = main(
            [
                "--stage",
                "verify",
                "--root",
                str(root),
                "--contract",
                str(cpath),
                "--out-evidence",
                str(out),
                "--days",
                day,
            ]
        )
        assert rc == 1, rc  # the index is not built yet
        assert not out.exists(), "evidence written for a blocked run"
        rc = main(
            [
                "--stage",
                "repair",
                "--root",
                str(root),
                "--contract",
                str(cpath),
                "--out-evidence",
                str(out),
                "--days",
                day,
            ]
        )
        assert rc == 0, rc
        doc = json.loads(out.read_text())
        assert doc["status"] == "ok" and doc["target_days"] == [day], doc["status"]
        di = doc["derived_index"]
        assert di["rebuilt"] and di["atomic_replace"] and not di["source_files_mutated"]
        assert di["rows"] == di["manifests_expected"] == 3
        assert di["sha256_before"] != di["sha256_after"]  # the pre-repair snapshot
        assert di["pre_repair_snapshot"]["rows"] == 0
        entry = doc["days"][day]
        assert entry["complete"] and entry["index_rows"]["present_after"] == [
            "coverage",
            "quotes",
            "trades",
        ]
        assert entry["index_rows"]["present_before"] == []
        for kind in ("bars", "coverage", "quotes", "trades"):
            assert len(entry["files"][kind]["sha256"]) == 64
        for kind in MANIFEST_KINDS:
            assert len(entry["manifests"][kind]["sha256"]) == 64
            assert entry["manifests"][kind]["status"] == "ok"
        assert doc["crosscheck"]["index_is_canonical_rebuild"]
        assert doc["crosscheck"]["sealed_parquet_reads"] == 0
        assert doc["crosscheck"]["fetches"] == 0
        assert doc["parent_shas"]["net_contract.json"] == sha256_file(cpath)
        # a second run is idempotent: no rebuild, same evidence facts
        rc = main(
            [
                "--stage",
                "repair",
                "--root",
                str(root),
                "--contract",
                str(cpath),
                "--out-evidence",
                str(out),
                "--days",
                day,
            ]
        )
        assert rc == 0, rc
        again = json.loads(out.read_text())
        assert again["derived_index"]["rebuilt"] is False
        assert again["derived_index"]["sha256"] == di["sha256_after"]
        assert again["days"][day]["index_rows"]["present_before"] == [
            "coverage",
            "quotes",
            "trades",
        ]
        # a dry run never replaces and never emits
        Path(td, "evidence2.json").unlink(missing_ok=True)
        rc = main(
            [
                "--stage",
                "repair",
                "--dry-run",
                "--root",
                str(root),
                "--contract",
                str(cpath),
                "--out-evidence",
                str(Path(td) / "evidence2.json"),
                "--days",
                day,
            ]
        )
        assert rc == 0, rc
        assert not Path(td, "evidence2.json").exists()
        # a sealed target is refused by the CLI before anything is opened
        for sealed in ("2025-01-15", "2026-07-01"):
            rc = main(
                [
                    "--stage",
                    "verify",
                    "--root",
                    str(root),
                    "--contract",
                    str(cpath),
                    "--out-evidence",
                    str(out),
                    "--days",
                    sealed,
                ]
            )
            assert rc in (1, 2), (sealed, rc)
        # a relative root is refused
        assert main(["--stage", "inspect", "--root", "relative/net", "--days", day]) == 2
        # a day outside the contract's declared targets is refused
        assert (
            main(
                [
                    "--stage",
                    "verify",
                    "--root",
                    str(root),
                    "--contract",
                    str(cpath),
                    "--out-evidence",
                    str(out),
                    "--days",
                    day + "," + other,
                ]
            )
            == 1
        )

    print("self-test OK")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def emit(report: dict, contract: dict, contract_file: Path, out: str) -> Path:
    path = Path(out).resolve()
    atomic_write_json(path, evidence_doc(report, contract, contract_file))
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", choices=("inspect", "repair", "verify"), default="inspect")
    ap.add_argument("--days", default=",".join(DEFAULT_DAYS), help="comma/space separated day ids")
    ap.add_argument("--root", default=None, help="absolute net root (default: resolved data root)")
    ap.add_argument("--contract", default=None, help="net_contract.json (default: tracked)")
    ap.add_argument("--out-evidence", default=str(ROOT / EVIDENCE_REL))
    ap.add_argument("--dry-run", action="store_true", help="repair: report only, never replace")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        selftest()
        return 0

    root = Path(args.root).expanduser() if args.root else default_net_root()
    if not root.is_absolute():
        print(f"refusing a relative net root: {root} (pass an absolute path)", file=sys.stderr)
        return 2
    if not (root / "bars").is_dir():
        print(f"not a net root (no bars/ under {root})", file=sys.stderr)
        return 2
    days = parse_days(args.days)
    if not days:
        print("no target days", file=sys.stderr)
        return 2

    cpath = contract_path(args)
    contract, cproblems = check_contract(cpath, days)
    rec = Reconciler(root, days)
    try:
        report = (
            rec.inspect()
            if args.stage == "inspect"
            else rec.repair(dry_run=args.dry_run)
            if args.stage == "repair"
            else rec.verify()
        )
    except PermissionError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError) as exc:
        print(f"blocked: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({k: v for k, v in report.items() if k != "days"}, indent=1, sort_keys=True))
    for day, entry in report["days"].items():
        print(
            f"{day}: {entry['status']} rows_before={entry['index_rows']['present_before']} "
            f"rows_after={entry['index_rows']['present_after']} problems={entry['problems']}"
        )
    if cproblems:
        print("contract: " + "; ".join(cproblems), file=sys.stderr)
        return 1
    if report.get("blocked"):
        print("blocked: " + "; ".join(report["blocked"]), file=sys.stderr)
        return 1
    if args.stage == "inspect" or report.get("status") in ("blocked", "inspected"):
        print(f"no evidence written (stage={args.stage}, status={report.get('status')})")
        return 0
    if args.dry_run:
        print("dry-run: no evidence written, index untouched")
        return 0
    path = emit(report, contract, cpath, args.out_evidence)
    print(f"evidence: {path} sha256={sha256_file(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
