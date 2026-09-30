#!/usr/bin/env python3
"""ATLAS Stage-0 raw SIP minute-bar acquisition (Freeze-O blockers B1 and B2).

Reacquires the two raw lanes the observation corpus cannot claim today:

  B1  2025-02 (``--scope feb2025``)    the guarded 19 dev days whose only raw
                                       source today is the $2/100-share CLEAN
                                       FALLBACK (``data/clean_ohlcv_2025-02.parquet``).
                                       Request: the full PIT roster of each day;
                                       the resulting same-day file REPLACES the
                                       fallback for those days.
  B2  2026-04/05 (``--scope aprmay2026``)  the 41 dev days whose backfill raw
                                       lane (``data/backfill/ohlcv_<month>.parquet``)
                                       carries a measured PIT-name hole. Request:
                                       only evidence-backed missing names (PIT x
                                       compact-universe-present, minus what the
                                       baseline lane already has for that day)
                                       plus the syntax names with a VERIFIED
                                       provider spelling. ``absent_all_sources``
                                       names are never requested: not trading is
                                       not a repairable hole.

Hard rules, all enforced in code (never by a flag):

* ``basket_sim.guard_day`` runs before any path is opened for a day. Sealed
  (2024-*, 2025-01) and reserved (2026-06..08) days are refused.
* The universe is the PIT archive plus per-day compact-universe evidence. No
  "latest active assets" list is ever fetched: that list is not a PIT roster.
* SIP feed, ``adjustment=raw``, 1-minute bars, 09:25-16:05 America/New_York.
* Batches of <= ``batch_size`` (500) symbols; pages are followed to exhaustion
  (SDK or REST); 429/5xx are retried with bounded backoff that honours
  ``Retry-After``. Unbounded retrying is a bug, not a policy.
* One canonical PIT name -> at most one provider spelling. Aliases come only
  from the explicit tracked ``alias_map.json`` (class-share separator
  transliteration backed by the provider's own spelling in the same-provider
  lane). Never from price similarity, fuzzy matching, or bare-stripping.
* Per-day atomic writes with sha256 + resume. Existing ADMITTED bytes are never
  overwritten: any sha/config/contract drift stops the run and asks for a new
  version directory.
* Invalid and zero-bar symbols are RECORDED with a reason, never fabricated and
  never silently dropped. API errors, duplicate (ticker, timestamp) rows,
  out-of-window rows, or an unresolved SAME-FEED contradictory zero make the day
  ``incomplete`` and refuse admission.
* Evidence is classed by FEED (``source_policy.json``). A source whose feed is
  the acquisition feed (``sip``) obliges the SIP minute-bar response: a positive
  bar count there and no bar here is a hard, unresolved contradiction. A source
  whose feed identity is unknown or different (the B1 CLEAN FALLBACK is HF/
  Finnhub-derived, feed ``unknown``) can never require the SIP feed to emit a
  minute bar under different condition/aggregation rules: it is reported as
  ``cross_feed_bar_not_reproduced`` and its ORIGINAL rows are preserved in
  ``cross_feed_witnesses/<day>.parquet`` (the archive original stays
  authoritative for that proxy). It is never called a non-trader, never
  relabelled as SIP, and never blocks admission by itself.

Usage (from the repo root)::

  .venv/bin/python factory/scripts/atlas_acquire_sip_bars.py --selftest
  .venv/bin/python factory/scripts/atlas_acquire_sip_bars.py --stage plan --scope feb2025
  .venv/bin/python factory/scripts/atlas_acquire_sip_bars.py --stage acquire --scope feb2025
  .venv/bin/python factory/scripts/atlas_acquire_sip_bars.py --stage verify  --scope feb2025
  .venv/bin/python factory/scripts/atlas_acquire_sip_bars.py --stage acquire \
      --scope aprmay2026 --days 2026-04-01
  # the joint admission, from explicit per-blocker references (<v4>/<v3> are the
  # per-blocker roots <data>/atlas/acquisition/v4 and .../v3):
  .venv/bin/python factory/scripts/atlas_acquire_sip_bars.py --stage publish \
      --blocker-ref B1_raw_2025_02=<v4>/evidence/acquisition_admission_feb2025.json \
      --blocker-ref B2_raw_roster_2026_04_05=<v3>/evidence/acquisition_admission_aprmay2026.json \
      --out-evidence \
        factory/artifacts/basket/phase2/ATLAS/TAPE/OBSERVATION/v0/acquisition/evidence

``--out-data`` defaults to the root ``source_policy.json`` declares for the
requested scope's blocker (B1 -> v4, B2 -> v3), and a run whose target is not the
declared root is refused. ``--stage acquire`` needs
``ALPACA_API_KEY``/``ALPACA_SECRET_KEY`` in the environment (or the repo
``.env``). ``plan``, ``verify`` and ``publish`` are offline: ``publish`` reads
each blocker's attestation and the bytes under its declared root, and writes
nothing named ``blockers_resolved.json``.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # reuse the canonical guard/calendar source
    from factory.scripts import basket_sim as sim
except ImportError:  # direct script execution
    from factory.scripts import basket_sim as sim

TRACK = ROOT / "factory/artifacts/basket/phase2/ATLAS/TAPE/OBSERVATION/v0"
CONTRACT_PATH = TRACK / "acquisition" / "contract.json"
ALIAS_MAP_PATH = TRACK / "acquisition" / "alias_map.json"
POLICY_PATH = TRACK / "acquisition" / "source_policy.json"
PRODUCERS_DIR = TRACK / "acquisition" / "producers"

ET = ZoneInfo("America/New_York")

SCOPES = ("feb2025", "aprmay2026")
BLOCKER_OF_SCOPE = {"feb2025": "B1_raw_2025_02", "aprmay2026": "B2_raw_roster_2026_04_05"}
SCOPE_OF_BLOCKER = {v: k for k, v in BLOCKER_OF_SCOPE.items()}
SCOPE_MONTHS = {"feb2025": ("2025-02",), "aprmay2026": ("2026-04", "2026-05")}
ACQUISITION_FEED = "sip"
# The observation corpus projects bars over 09:25-16:05 ET (PROJECTION_LO/HI).
PROJECTION_LO, PROJECTION_HI = 565, 965
BATCH_DEFAULT = 500  # provider batch cap: one request stream per <=500 symbols
SEED_DAY_PREFIX_REFUSAL_NOTE = "guard_day is called before any path is opened"

# Payload schema. The consumer (basket_tape_atlas_observation.py) requires
# exactly these eight columns; provider_symbol is audit-only and is never a join
# key. `ticker` is always the CANONICAL PIT spelling.
BAR_SCHEMA: dict[str, Any] = {
    "timestamp": pl.Datetime("ns", "UTC"),
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "ticker": pl.String,
    "provider_symbol": pl.String,
}
REQUIRED_STATUS_ADMITTED = "complete"
# bytes/row is a planning-only figure for the run report; the real payload size
# is always measured from the written file.
BYTES_PER_ROW_ESTIMATE = 96

# Feed-class authority. `source_policy.json` must declare EXACTLY these classes:
# the policy carries the declarations so they are reviewable as an artifact, but
# the meaning of a class is fixed here, because a policy able to relabel an
# unknown feed as "sip" could make an unreproducible witness block a feed it
# does not own.
REQUIRED_FEED_CLASSES: dict[str, dict[str, Any]] = {
    "sip": {
        "matches_acquisition_feed": True,
        "expected_positive_zero": "same_feed_contradictory_zero",
        "may_require_sip_minute_bar": True,
        "blocks_admission_when_expected_positive_and_absent": True,
    },
    "unknown": {
        "matches_acquisition_feed": False,
        "expected_positive_zero": "cross_feed_bar_not_reproduced",
        "may_require_sip_minute_bar": False,
        "blocks_admission_when_expected_positive_and_absent": False,
        "preserve": "cross_feed_witness_rows",
    },
}
CROSS_FEED_DIAGNOSTIC = "cross_feed_bar_not_reproduced"
SAME_FEED_DIAGNOSTIC = "same_feed_contradictory_zero"
WITNESS_DIRNAME = "cross_feed_witnesses"
WITNESS_SCHEMA: dict[str, Any] = {
    "parent_sha256": pl.String,  # the exact archive the witness row came from
    "row_ordinal": pl.Int64,  # its 0-based ordinal inside that archive
    "canonical_time_utc": pl.Datetime("ns", "UTC"),
    "et_minute": pl.Int32,
    "ticker": pl.String,  # CANONICAL PIT spelling, same join key as the payload
    "source_name": pl.String,
    "source_path": pl.String,
    "source_feed": pl.String,
    "source_provider": pl.String,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "source_n_bars_in_window": pl.Int64,
    "fresh_feed": pl.String,
    "fresh_n_bars": pl.Int64,
    "diagnostic": pl.String,
    "day": pl.String,
}
WITNESS_KEY = ("parent_sha256", "row_ordinal")


# --------------------------------------------------------------------------- #
# paths / small helpers
# --------------------------------------------------------------------------- #
def resolve_data_root() -> Path:
    """Market data lives outside the worktree (read-only, gitignored)."""
    cands = []
    env = os.environ.get("BASKET_DATA_ROOT")
    if env:
        cands.append(Path(env))
    cands.append(ROOT / "data")
    cands.append(Path("/home/hillel/projects/Alpacatrader/data"))
    for c in cands:
        if (c / "pit/pit_symbols.parquet").exists() and (c / "sip/net/bars").exists():
            return c
    raise FileNotFoundError(f"no market-data root found among {[str(c) for c in cands]}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


_DIGEST_CACHE: dict[str, tuple[tuple[int, int], str]] = {}


def sha256_file_cached(path: Path) -> str | None:
    """sha256 of a file, memoized on (size, mtime_ns) so an unchanged input is
    hashed once per process instead of once per day.

    The B2 lane file is ~585 MiB and plan_day touches it for all 41 days, which
    was ~25 GiB of re-reading to hash one unchanged file. Keying the memo on
    size and mtime keeps it honest: a modified file is rehashed, never trusted
    stale. Returns None for a missing file, as sha256_file's callers expect.
    """
    key = str(path)
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    stamp = (st.st_size, st.st_mtime_ns)
    hit = _DIGEST_CACHE.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    digest = sha256_file(path)
    _DIGEST_CACHE[key] = (stamp, digest)
    return digest


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(obj: Any) -> str:
    return sha256_bytes(json.dumps(obj, sort_keys=True, default=str).encode())


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_bytes(
        path, (json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n").encode()
    )


def atomic_write_parquet(df: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.write_parquet(tmp)
    os.replace(tmp, path)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def window_utc(day: str, lo: int, hi: int) -> tuple[datetime, datetime]:
    """The request window [lo, hi] ET as UTC instants, BOTH ENDS INCLUSIVE.

    The frozen observation contract declares the projection window as the CLOSED
    interval "[565, 965] (09:25-16:05 ET)", and the consumer's grid iterates
    range(lo, hi + 1) - it expects a cell at 16:05. The provider's `end` is
    likewise inclusive, so a request ending exactly at 16:05:00 ET returns bars
    stamped 16:05, and those bars are IN window and are kept.

    What that 16:05 bar represents is NOT established here. It is a vendor bar
    timestamp at the window's upper bound; nothing in the acquisition or the
    frozen contracts identifies it as an auction, a close, or any particular
    session event, so it is described only by its timestamp.

    The baseline backfill lane already carries 412 bars stamped 16:05 on
    2026-04-01, so excluding them here would make the acquired day disagree
    with the lane it supplements.
    """
    d = date.fromisoformat(day)
    start = datetime(d.year, d.month, d.day, lo // 60, lo % 60, tzinfo=ET)
    end = datetime(d.year, d.month, d.day, hi // 60, hi % 60, tzinfo=ET)
    return start.astimezone(UTC), end.astimezone(UTC)


def et_minute(ts: datetime) -> int:
    t = ts.astimezone(ET)
    return t.hour * 60 + t.minute


def chunks(seq: Sequence[Any], n: int) -> list[list[Any]]:
    return [list(seq[i : i + n]) for i in range(0, len(seq), n)]


# --------------------------------------------------------------------------- #
# contract / alias map
# --------------------------------------------------------------------------- #
def load_contract(path: Path = CONTRACT_PATH) -> dict:
    c = load_json(path)
    fetch = c.get("fetch", {})
    if fetch.get("feed") != "sip" or fetch.get("adjustment") != "raw":
        raise ValueError(
            "acquisition contract must declare feed=sip and adjustment=raw "
            f"(got feed={fetch.get('feed')!r} adjustment={fetch.get('adjustment')!r})"
        )
    if int(fetch.get("timeframe_minutes", 0)) != 1:
        raise ValueError("acquisition contract must declare 1-minute bars")
    return c


@dataclass(frozen=True)
class Alias:
    canonical: str
    provider: str
    basis: str
    evidence: tuple[str, ...]


def load_alias_map(path: Path = ALIAS_MAP_PATH) -> dict[str, Alias]:
    """canonical PIT name -> provider spelling, validated injective both ways.

    A provider spelling reachable from two canonical names would put one payload
    under two identities; a canonical name reachable from two provider spellings
    would silently pick one. Both are hard errors here, not warnings.
    """
    doc = load_json(path)
    by_provider: dict[str, str] = {}
    out: dict[str, Alias] = {}
    for canonical, rec in (doc.get("aliases") or {}).items():
        provider = rec["provider_symbol"]
        if canonical in out:
            raise ValueError(f"alias_map: duplicate canonical entry {canonical!r}")
        if provider in by_provider:
            raise ValueError(
                f"alias_map: provider spelling {provider!r} is claimed by both "
                f"{by_provider[provider]!r} and {canonical!r}"
            )
        if not (isinstance(rec.get("evidence"), list) and rec["evidence"]):
            raise ValueError(f"alias_map: {canonical!r} has no explicit spelling evidence")
        by_provider[provider] = canonical
        out[canonical] = Alias(
            canonical=canonical,
            provider=provider,
            basis=rec.get("basis", "unspecified"),
            evidence=tuple(rec["evidence"]),
        )
    return out


# --------------------------------------------------------------------------- #
# source policy: which sources may oblige the SIP feed, and for which blocker
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class EvidenceSource:
    """A local source of "this name printed that session" evidence."""

    name: str
    role: str  # obligation_evidence | replaced_baseline | supplemented_baseline
    feed: str
    provider: str
    matches_acquisition_feed: bool
    may_require_sip_minute_bar: bool
    counts_window: str  # in_window | source_reported
    witness_rows_available: bool
    path_template: str
    evidence: str

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "role": self.role,
            "feed": self.feed,
            "provider": self.provider,
            "matches_acquisition_feed": self.matches_acquisition_feed,
            "may_require_sip_minute_bar": self.may_require_sip_minute_bar,
            "counts_window": self.counts_window,
            "witness_rows_available": self.witness_rows_available,
            "path_template": self.path_template,
            "evidence": self.evidence,
        }


def load_policy(path: Path = POLICY_PATH) -> dict:
    p = load_json(path)
    if not isinstance(p, dict):
        raise ValueError(f"source policy {path} must be a JSON object")
    return p


def validate_policy(
    policy: dict, data_root: Path, path: Path = POLICY_PATH, root: Path = ROOT
) -> dict:
    """Validate the declared feed classes, sources and per-blocker roots.

    Returns the normalized per-blocker references. Every failure here is a hard
    error: a policy that cannot name a source's feed, that lets a non-SIP source
    demand a SIP minute bar, or that pins a lane digest the lane no longer has,
    is not a policy any admission may be published under.
    """
    if policy.get("schema") != "atlas.acquisition.source-policy.v0":
        raise ValueError(f"source policy schema {policy.get('schema')!r} is not supported")
    if policy.get("acquisition_feed") != ACQUISITION_FEED:
        raise ValueError(
            f"source policy declares acquisition_feed={policy.get('acquisition_feed')!r}; "
            f"this producer acquires feed={ACQUISITION_FEED!r}"
        )
    classes = policy.get("feed_classes") or {}
    for name, required in REQUIRED_FEED_CLASSES.items():
        got = classes.get(name)
        if not isinstance(got, dict):
            raise ValueError(f"source policy must declare feed class {name!r}")
        wrong = {k: got.get(k) for k, v in required.items() if got.get(k) != v}
        if wrong:
            raise ValueError(
                f"feed class {name!r} declares {wrong}; the class meaning is fixed in code "
                f"as {required}"
            )
    sources = policy.get("sources") or {}
    if not sources:
        raise ValueError("source policy declares no sources")
    for sname, src in sources.items():
        for key in ("role", "feed", "path_template", "counts_window"):
            if not src.get(key):
                raise ValueError(f"source {sname!r} is missing {key!r}")
        if src["feed"] not in classes:
            raise ValueError(f"source {sname!r} declares undeclared feed class {src['feed']!r}")
        if src["counts_window"] not in ("in_window", "source_reported"):
            raise ValueError(f"source {sname!r} counts_window {src['counts_window']!r} unknown")
        if Path(src["path_template"]).is_absolute():
            raise ValueError(f"source {sname!r} path_template must be data-root relative")
        if str(src["path_template"]).startswith("data/"):
            raise ValueError(
                f"source {sname!r} path_template {src['path_template']!r} already carries the "
                "data/ prefix, but templates are DATA-ROOT relative (the data root IS data/)"
            )
        # A source that is NOT the acquisition feed can never oblige a SIP minute
        # bar, so its only admissible use is as preserved cross-feed evidence.
        # That is impossible without its original rows.
        if not classes[src["feed"]]["matches_acquisition_feed"] and not src.get(
            "witness_rows_available"
        ):
            raise ValueError(
                f"source {sname!r} is not the acquisition feed ({src['feed']!r}) and cannot "
                "yield witness rows; a cross-feed source must be able to preserve the "
                "original rows it observed, otherwise its evidence is unreportable"
            )
        if not classes[src["feed"]]["matches_acquisition_feed"] and src["counts_window"] != (
            "in_window"
        ):
            raise ValueError(
                f"source {sname!r} is a cross-feed source and must count bars on the same "
                f"closed projection window (counts_window='in_window'), not "
                f"{src['counts_window']!r}: its preserved rows must be exactly reproducible"
            )

    per_blocker = policy.get("per_blocker") or {}
    if set(per_blocker) != set(BLOCKER_OF_SCOPE.values()):
        raise ValueError(
            f"source policy must declare exactly {sorted(BLOCKER_OF_SCOPE.values())}; "
            f"declares {sorted(per_blocker)}"
        )
    refs: dict[str, dict] = {}
    for bid, spec in per_blocker.items():
        scope = spec.get("scope")
        if SCOPE_OF_BLOCKER.get(bid) != scope:
            raise ValueError(f"{bid}: declared scope {scope!r} does not match the blocker id")
        required_n = len(full_scope_day_set(scope))
        if int(spec.get("expected_days", -1)) != required_n:
            raise ValueError(
                f"{bid}: declares expected_days={spec.get('expected_days')!r} but the dev "
                f"calendar owes {required_n} days"
            )
        root_rel = str(spec.get("root_rel") or "")
        if not root_rel:
            raise ValueError(f"{bid}: no root_rel declared")
        blocker_root = Path(root_rel) if root_rel.startswith("/") else (data_root / root_rel)
        blocker_root = blocker_root.resolve()
        producer = spec.get("producer") or {}
        snap_rel = producer.get("snapshot_path")
        if not snap_rel:
            raise ValueError(f"{bid}: no producer snapshot declared")
        # An origin proof is only an origin proof if it is a tracked capture, so
        # both snapshots must live in the producers/ directory that holds them.
        prod_dir = (root / PRODUCERS_DIR.relative_to(ROOT)).resolve()
        for key, rel in (
            ("snapshot_path", snap_rel),
            ("contract_snapshot_path", producer.get("contract_snapshot_path")),
        ):
            if not rel:
                continue
            target = (root / rel).resolve()
            if not str(target).startswith(str(prod_dir) + os.sep):
                raise ValueError(
                    f"{bid}: {key} {rel!r} must live under "
                    f"{PRODUCERS_DIR.relative_to(ROOT)} - an origin proof has to be a tracked "
                    "capture, not a file that happens to be lying around"
                )
        snap = root / snap_rel
        if not snap.exists():
            raise ValueError(f"{bid}: producer snapshot {snap_rel} does not exist")
        snap_sha = sha256_file(snap)
        if snap_sha != producer.get("snapshot_sha256"):
            raise ValueError(
                f"{bid}: producer snapshot {snap_rel} hashes {snap_sha[:12]} but the policy "
                f"pins {str(producer.get('snapshot_sha256'))[:12]}: the origin proof does not "
                "cover the file on disk"
            )
        # The snapshot IS the producer's source, so its digest is the code hash
        # that producer wrote into its manifests. Nothing here requires the
        # currently-installed producer to have that hash: a historical origin is
        # pinned, never re-typed as today's tool.
        if producer.get("code_sha256_at_capture") != snap_sha:
            raise ValueError(
                f"{bid}: code_sha256_at_capture {str(producer.get('code_sha256_at_capture'))[:12]}"
                f" != snapshot digest {snap_sha[:12]}"
            )
        # The contract the producer acquired under is pinned the same way, so a
        # contract revision for one blocker cannot invalidate another blocker's
        # already-verified days.
        cref = producer.get("contract_snapshot_path")
        if not cref:
            raise ValueError(f"{bid}: no contract snapshot declared")
        csnap = root / cref
        if not csnap.exists():
            raise ValueError(f"{bid}: contract snapshot {cref} does not exist")
        csha = sha256_file(csnap)
        if csha != producer.get("contract_snapshot_sha256"):
            raise ValueError(
                f"{bid}: contract snapshot {cref} hashes {csha[:12]} but the policy pins "
                f"{str(producer.get('contract_snapshot_sha256'))[:12]}"
            )
        if producer.get("contract_sha256_at_capture") != csha:
            raise ValueError(
                f"{bid}: contract_sha256_at_capture "
                f"{str(producer.get('contract_sha256_at_capture'))[:12]} != snapshot digest "
                f"{csha[:12]}"
            )
        declared_sources = list(spec.get("sources") or [])
        missing = [s for s in declared_sources if s not in sources]
        if missing:
            raise ValueError(f"{bid}: undeclared sources {missing}")
        roles = [sources[s]["role"] for s in declared_sources]
        if "obligation_evidence" not in roles:
            raise ValueError(f"{bid}: no obligation_evidence source declared")
        if not any(r in ("replaced_baseline", "supplemented_baseline") for r in roles):
            raise ValueError(f"{bid}: no baseline source declared")
        refs[bid] = {
            "blocker_id": bid,
            "scope": scope,
            "version": str(spec.get("version") or ""),
            "root": blocker_root,
            "root_rel": root_rel,
            "attestation": blocker_root / str(spec.get("attestation_rel") or ""),
            "producer": {
                "snapshot_path": snap_rel,
                "snapshot_sha256": snap_sha,
                "code_sha256_at_capture": snap_sha,
                "contract_snapshot_path": cref,
                "contract_snapshot_sha256": csha,
                "contract_sha256_at_capture": csha,
                "version": str(spec.get("version") or ""),
            },
            "sources": declared_sources,
        }
        if not spec.get("attestation_rel"):
            raise ValueError(f"{bid}: no attestation_rel declared")

    # Origin pins. A lane whose bytes no longer hash to the pin is a different
    # archive, and every claim made against the pinned one is void.
    for sname, src in sources.items():
        for month, pin in (src.get("sha256_pins") or {}).items():
            lane = data_root / src["path_template"].replace("<month>", month)
            if not lane.exists():
                raise ValueError(f"source {sname!r}: pinned lane {lane} is missing")
            got = sha256_file_cached(lane)
            if got != pin:
                raise ValueError(
                    f"source {sname!r} {month}: lane {lane} hashes {got[:12]} but the policy "
                    f"pins {str(pin)[:12]}: the origin archive was replaced or edited"
                )
    return {
        "schema": policy["schema"],
        "policy_id": policy.get("policy_id"),
        "acquisition_feed": policy["acquisition_feed"],
        "sha256": sha256_file(path),
        "classes": classes,
        "sources": sources,
        "refs": refs,
    }


def evidence_source_specs(scope: str, policy: dict) -> tuple[EvidenceSource, ...]:
    """The evidence sources a scope's blocker declares, in declaration order."""
    bid = BLOCKER_OF_SCOPE[scope]
    spec = (policy.get("per_blocker") or {})[bid]
    out = []
    for sname in spec["sources"]:
        src = policy["sources"][sname]
        cls = policy["feed_classes"][src["feed"]]
        out.append(
            EvidenceSource(
                name=sname,
                role=src["role"],
                feed=src["feed"],
                provider=str(src.get("provider") or ""),
                matches_acquisition_feed=bool(cls["matches_acquisition_feed"]),
                may_require_sip_minute_bar=bool(cls["may_require_sip_minute_bar"]),
                counts_window=str(src["counts_window"]),
                witness_rows_available=bool(src.get("witness_rows_available")),
                path_template=str(src["path_template"]),
                evidence=str(src.get("evidence") or ""),
            )
        )
    return tuple(out)


# --------------------------------------------------------------------------- #
# PIT roster
# --------------------------------------------------------------------------- #
class PitIndex:
    """data/pit/pit_symbols.parquet: latest vintage <= day, LAZILY materialized.

    The archive holds 1397 vintages and ~8.1M symbol strings. Materializing every
    vintage's list to serve one at a time cost a measured 127 MiB of permanently
    resident strings. Only the vintage column is indexed here; a vintage's
    symbols are read when that vintage is actually asked for, and the caller
    keeps at most the one set it is currently planning for.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._vintages: list[str] = []
        self._vintage_col: str | None = None
        self._memo: tuple[str, frozenset[str]] | None = None
        self._sha: str | None = None

    @property
    def sha256(self) -> str:
        if self._sha is None:
            self._sha = sha256_file(self.path)
        return self._sha

    def _load(self) -> None:
        if self._vintages:
            return
        vintages = pl.read_parquet(self.path, columns=["vintage"])["vintage"]
        self._vintages = sorted(set(vintages.to_list()))
        self._vintage_col = "vintage"

    def vintage_for(self, day: str) -> str:
        import bisect

        self._load()
        i = bisect.bisect_right(self._vintages, day) - 1
        if i < 0:
            raise ValueError(f"no PIT vintage <= {day}")
        return self._vintages[i]

    def symbols(self, day: str) -> tuple[str, frozenset[str]]:
        v = self.vintage_for(day)
        if self._memo is not None and self._memo[0] == v:
            return v, self._memo[1]
        rows = (
            pl.read_parquet(self.path, columns=["vintage", "symbol"])
            .filter(pl.col(self._vintage_col) == v)["symbol"]
            .to_list()
        )
        self._memo = (v, frozenset(sys.intern(s.strip()) for s in rows if s and s.strip()))
        return v, self._memo[1]


# --------------------------------------------------------------------------- #
# local evidence lanes (read-only; never written by this script)
# --------------------------------------------------------------------------- #
class CompactIndex:
    """data/sip/universe/rth/<day>.parquet: which PIT names actually printed.

    This is the compact SIP query of the same PIT roster (unfiltered, so it has
    no $2/100-share floor) and is the only per-day evidence of "this name
    traded". Absence here is NOT proof of non-trading; it is the compact source
    being unavailable for that name.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._sym: dict[str, frozenset[str]] = {}
        self._nbar: dict[str, dict[str, int]] = {}

    def day_path(self, day: str) -> Path:
        return self.root / f"{day}.parquet"

    def manifest_path(self, day: str) -> Path:
        return self.root / f"{day}.manifest.json"

    def symbols(self, day: str) -> frozenset[str]:
        if day not in self._sym:
            p = self.day_path(day)
            if not p.exists():
                return frozenset()
            self._sym[day] = frozenset(pl.read_parquet(p, columns=["symbol"])["symbol"].to_list())
        return self._sym[day]

    def n_bars(self, day: str) -> dict[str, int]:
        if day not in self._nbar:
            p = self.day_path(day)
            if not p.exists():
                return {}
            df = pl.read_parquet(p, columns=["symbol", "n_bars"])
            self._nbar[day] = dict(zip(df["symbol"].to_list(), df["n_bars"].to_list(), strict=True))
        return self._nbar[day]


class LaneIndex:
    """Baseline raw lane, read LAZILY one day at a time.

    The previous implementation grouped the whole month by day and built a
    List[str] column, which materialised a hash table over all ~10.8M ticker
    values of a 585 MiB file before collapsing to the ~212k strings actually
    needed: a measured 1740 MiB peak, run twice per month, with both day->set
    dicts retained for the life of the process. That is what OOMed B2.

    Now each day is a predicate-pushed, streaming collect of ONE day's rows, and
    only that day's counts are returned. Tickers are interned, so the ~10.8k
    distinct strings per day are shared objects rather than ~10.8k fresh ones.
    Measured on the real lane: 0.0 MiB peak growth, identical results, and
    faster than the grouped form.
    """

    def __init__(self) -> None:
        self._paths: dict[str, Path] = {}

    def register(self, month: str, path: Path) -> None:
        self._paths[month] = path

    def path_for(self, month: str) -> Path:
        return self._paths[month]

    def day(self, month: str, day: str) -> tuple[dict[str, int], dict[str, int]]:
        """(bars_any_window, bars_in_projection_window) as name -> bar count.

        Returned counts rather than bare sets, because the contradiction oracle
        needs positive bar-count evidence: a name the baseline observed but the
        fresh pull does not return is a revision, not a legitimate zero.
        """
        p = self._paths.get(month)
        if p is None or not p.exists():
            raise FileNotFoundError(f"baseline raw lane missing: {p}")
        d = date.fromisoformat(day)
        base = (
            pl.scan_parquet(p)
            .select(["timestamp", "ticker"])
            .filter(pl.col("timestamp").dt.date() == d)
            .with_columns(
                (
                    pl.col("timestamp")
                    .dt.convert_time_zone("America/New_York")
                    .dt.hour()
                    .cast(pl.Int32)
                    * 60
                    + pl.col("timestamp")
                    .dt.convert_time_zone("America/New_York")
                    .dt.minute()
                    .cast(pl.Int32)
                ).alias("et")
            )
        )
        all_counts = base.group_by("ticker").len().collect(engine="streaming")
        win_counts = (
            base.filter((pl.col("et") >= PROJECTION_LO) & (pl.col("et") <= PROJECTION_HI))
            .group_by("ticker")
            .len()
            .collect(engine="streaming")
        )
        return (
            {sys.intern(t): int(n) for t, n in all_counts.select("ticker", "len").iter_rows()},
            {sys.intern(t): int(n) for t, n in win_counts.select("ticker", "len").iter_rows()},
        )


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #
def scope_days(scope: str, day_filter: Sequence[str] | None, dev_days: Sequence[str]) -> list[str]:
    months = SCOPE_MONTHS[scope]
    days = [d for d in dev_days if d[:7] in months]
    if day_filter:
        want = set(day_filter)
        for d in want:
            if d not in days:
                raise ValueError(
                    f"day {d} is not a guarded {scope} dev day "
                    f"(scope months {months}); refusing to acquire it"
                )
        days = [d for d in days if d in want]
    for d in days:
        sim.guard_day(d)  # refuse sealed/reserved before any path is opened
    if not days:
        raise ValueError(f"no {scope} days selected")
    return days


def is_syntax_name(name: str) -> bool:
    """Vendor notation the stock data API rejects ('/' class share, '^' unit)."""
    return "/" in name or "^" in name


@dataclass
class DayPlan:
    day: str
    scope: str
    vintage: str
    pit_n: int
    requested: list[str]  # canonical names to fetch
    provider_of: dict[str, str]  # canonical -> provider spelling
    aliases_used: dict[str, str]
    expected_present: list[str]  # obligation set: names some source observed
    observed_bars: dict[str, int]  # name -> max positive bar count any source saw
    raw_existing: list[str]  # baseline lane already has it in-window
    alias_existing: list[str]  # baseline lane has the aliased spelling
    unavailable: dict[str, list[str]]  # class -> canonical names (VERIFIED absent)
    baseline: dict[str, Any]
    compact: dict[str, Any]
    # Per-day evidence sources with their DECLARED feed identity, and the bar
    # counts attributed to each. Counting every source into one number is what
    # made a non-SIP lane able to oblige the SIP feed.
    evidence_sources: list[dict[str, Any]] = field(default_factory=list)
    observed_by_source: dict[str, dict[str, int]] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "day": self.day,
            "scope": self.scope,
            "blocker_id": BLOCKER_OF_SCOPE[self.scope],
            "pit_vintage": self.vintage,
            "pit_n": self.pit_n,
            "requested_n": len(self.requested),
            "requested": self.requested,
            "canonical_to_provider": self.provider_of,
            "aliases": self.aliases_used,
            "expected_universe_present": self.expected_present,
            "obligation_rule": (
                "feb2025: PIT x (compact-observed UNION replaced-baseline in-window), because "
                "this payload REPLACES the floored fallback. The replaced fallback is a "
                "non-SIP (feed unknown) source, so a name only it observed is preserved as a "
                "cross-feed witness; the SIP feed is never required to reproduce it. "
                "aprmay2026: PIT x compact-observed; the baseline lane is retained and covers "
                "its own names."
            ),
            "evidence_sources": self.evidence_sources,
            "observed_evidence_by_source": {
                s["name"]: sum(1 for by in self.observed_by_source.values() if by.get(s["name"], 0))
                for s in self.evidence_sources
            },
            "observed_bar_evidence_names": len(self.observed_bars),
            "raw_existing": self.raw_existing,
            "alias_existing": self.alias_existing,
            "unavailable": self.unavailable,
            "baseline": self.baseline,
            "compact": self.compact,
        }


def source_path(src: EvidenceSource, day: str, data: Path) -> Path:
    """The concrete file a declared source holds for a day."""
    rel = src.path_template.replace("<month>", day[:7]).replace("<day>", day)
    return data / rel


def baseline_source(scope: str, policy: dict) -> EvidenceSource:
    specs = [
        s
        for s in evidence_source_specs(scope, policy)
        if s.role in ("replaced_baseline", "supplemented_baseline")
    ]
    if len(specs) != 1:
        raise ValueError(
            f"{scope}: expected exactly one baseline source, got {[s.name for s in specs]}"
        )
    return specs[0]


def compact_source(scope: str, policy: dict) -> EvidenceSource:
    specs = [s for s in evidence_source_specs(scope, policy) if s.role == "obligation_evidence"]
    if len(specs) != 1:
        raise ValueError(
            f"{scope}: expected exactly one obligation_evidence source, got "
            f"{[s.name for s in specs]}"
        )
    return specs[0]


def plan_day(
    day: str,
    scope: str,
    pit: PitIndex,
    aliases: dict[str, Alias],
    compact: CompactIndex,
    lanes: LaneIndex,
    data: Path,
    policy: dict,
) -> DayPlan:
    sim.guard_day(day)
    vintage, pit_set = pit.symbols(day)
    comp_syms = compact.symbols(day)
    comp_nbars = compact.n_bars(day)
    base_spec = baseline_source(scope, policy)
    comp_spec = compact_source(scope, policy)
    lane_path = source_path(base_spec, day, data)
    lanes.register(day[:7], lane_path)
    lane_any, lane_win = lanes.day(day[:7], day)
    comp_man = compact.manifest_path(day)
    comp_sha = sha256_file_cached(comp_man)

    provider_of: dict[str, str] = {}
    aliases_used: dict[str, str] = {}
    raw_existing: list[str] = []
    alias_existing: list[str] = []
    # Positive bar-count evidence that some source saw this name trade, kept
    # PER SOURCE so the oracle can ask whether that source's feed is the feed
    # being acquired. Merging the sources into one count is what let an HF/
    # Finnhub-derived lane (feed unknown) oblige the Alpaca SIP feed.
    observed_by_source: dict[str, dict[str, int]] = {}

    def note(name: str, source_name: str, n: int) -> None:
        if n and n > 0:
            by = observed_by_source.setdefault(name, {})
            by[source_name] = max(by.get(source_name, 0), int(n))

    for name, n in lane_win.items():
        note(name, base_spec.name, n)
    for name, n in comp_nbars.items():
        note(name, comp_spec.name, int(n or 0))
    observed_bars: dict[str, int] = {n: max(by.values()) for n, by in observed_by_source.items()}
    # The obligation set. feb2025 REPLACES the floored fallback, so a PIT name
    # the fallback carried in-window is owed coverage even when the compact
    # source never observed it - otherwise it leaves the tape with no
    # diagnostic. aprmay2026 SUPPLEMENTS the baseline, which stays in place, so
    # its names are covered by the lane rather than by this payload.
    if scope == "feb2025":
        obligation = pit_set & (comp_syms | set(lane_win))
    else:
        obligation = pit_set & comp_syms
    expected_present = sorted(obligation)
    unavailable: dict[str, list[str]] = {
        "absent_all_sources": [],
        "no_rth_in_baseline": [],
        "no_verified_provider_spelling": [],
        "baseline_only_observed": [],
    }
    requested: list[str] = []
    baseline_only: list[str] = []

    for name in sorted(pit_set):
        in_compact = name in comp_syms
        aliased = name in aliases
        provider = aliases[name].provider if aliased else name
        if scope == "feb2025":
            # Full PIT replacement of the floored clean fallback: ask for every
            # name the PIT roster had that day.
            if aliased:
                aliases_used[name] = provider
                provider_of[name] = provider
                requested.append(name)
            elif is_syntax_name(name):
                unavailable["no_verified_provider_spelling"].append(name)
            else:
                provider_of[name] = name
                requested.append(name)
            if in_compact and name in lane_win:
                raw_existing.append(name)
            elif not in_compact and name in lane_win:
                # Carried by the fallback being replaced, invisible to compact.
                baseline_only.append(name)
            continue

        # B2: repair only what is missing AND evidence-backed.
        if in_compact and name in lane_win:
            raw_existing.append(name)
            continue
        if in_compact:
            provider_of[name] = provider
            requested.append(name)
            continue
        if aliased:
            aliases_used[name] = provider
            if provider in lane_win:
                alias_existing.append(name)  # baseline lane already carries it
                continue
            provider_of[name] = provider
            requested.append(name)
            continue
        if is_syntax_name(name):
            unavailable["no_verified_provider_spelling"].append(name)
        elif name in lane_any and name not in lane_win:
            unavailable["no_rth_in_baseline"].append(name)
        else:
            unavailable["absent_all_sources"].append(name)

    unavailable["baseline_only_observed"] = sorted(baseline_only)
    evidence_sources = [
        {
            **s.to_json(),
            "path": str(source_path(s, day, data)),
            "sha256": sha256_file_cached(source_path(s, day, data)),
        }
        for s in evidence_source_specs(scope, policy)
    ]
    plan = DayPlan(
        day=day,
        scope=scope,
        vintage=vintage,
        pit_n=len(pit_set),
        requested=requested,
        provider_of=provider_of,
        aliases_used=aliases_used,
        expected_present=expected_present,
        observed_bars=observed_bars,
        raw_existing=sorted(raw_existing),
        alias_existing=sorted(alias_existing),
        unavailable={k: sorted(v) for k, v in unavailable.items()},
        baseline={
            "path": str(lane_path),
            "sha256": sha256_file_cached(lane_path),
            "present": lane_path.exists(),
            "symbols_any": len(lane_any),
            "symbols_in_window": len(lane_win),
            "standing": "replaced_by_this_acquisition"
            if base_spec.role == "replaced_baseline"
            else "supplemented",
            "source_name": base_spec.name,
            "feed": base_spec.feed,
            "provider": base_spec.provider,
            "matches_acquisition_feed": base_spec.matches_acquisition_feed,
        },
        compact={
            "path": str(compact.day_path(day)),
            "sha256": sha256_file_cached(compact.day_path(day)),
            "present": compact.day_path(day).exists(),
            "symbols": len(comp_syms),
            "is_sip_query": bool(comp_sha),
            "source_name": comp_spec.name,
            "feed": comp_spec.feed,
            "provider": comp_spec.provider,
            "matches_acquisition_feed": comp_spec.matches_acquisition_feed,
        },
        evidence_sources=evidence_sources,
        observed_by_source=observed_by_source,
    )
    return plan


def estimate_day(plan: DayPlan, n_bars: dict[str, int], bars_per_minute: int) -> dict:
    n = len(plan.requested)
    batches = math.ceil(n / BATCH_DEFAULT) if n else 0
    in_compact = [s for s in plan.requested if s in n_bars]
    est_rows = sum(n_bars.get(s, bars_per_minute) for s in plan.requested)
    return {
        "day": plan.day,
        "requested_symbols": n,
        "batches": batches,
        "requests_at_least": batches,
        "expected_rows_compact_backed": int(est_rows),
        "expected_rows_from_compact": int(sum(n_bars.get(s, 0) for s in plan.requested)),
        "symbols_compact_backed": len(in_compact),
        "estimated_bytes": int(est_rows * BYTES_PER_ROW_ESTIMATE),
    }


# --------------------------------------------------------------------------- #
# evidence classification + cross-feed witness preservation
# --------------------------------------------------------------------------- #
def classify_evidence(plan: DayPlan, zero_bars: set[str]) -> tuple[list[dict], list[dict]]:
    """Split every expected-positive/observed-zero name by the source's feed.

    Returns (same_feed_contradictions, cross_feed_disagreements). Only a source
    whose declared feed IS the acquisition feed can oblige the SIP minute-bar
    response. A source with an unknown/different feed is reporting its own
    aggregation of its own tape: the SIP feed cannot be required to emit a bar
    under rules it does not share, so that is a diagnostic to preserve, never a
    contradiction to resolve and never a claim that the name did not trade.
    """
    by_name = {s["name"]: s for s in plan.evidence_sources}
    same: list[dict] = []
    cross: list[dict] = []
    for sym in sorted(zero_bars):
        for sname, n in sorted((plan.observed_by_source.get(sym) or {}).items()):
            src = by_name.get(sname)
            if src is None or int(n) <= 0:
                continue
            common = {
                "ticker": sym,
                "source": sname,
                "source_role": src["role"],
                "source_path": src["path"],
                "source_sha256": src["sha256"],
                "source_feed": src["feed"],
                "source_provider": src["provider"],
                "source_n_bars_in_window": int(n),
                "fresh_feed": ACQUISITION_FEED,
                "fresh_n_bars": 0,
            }
            if src["matches_acquisition_feed"]:
                same.append(
                    {
                        **common,
                        "diagnostic": SAME_FEED_DIAGNOSTIC,
                        "reason": "this source IS the feed being acquired, so the SIP response "
                        "was expected to emit a bar here and did not. Unresolved: either the "
                        "provider revised its tape or the name is not requestable under this "
                        "spelling.",
                    }
                )
            else:
                cross.append(
                    {
                        **common,
                        "diagnostic": CROSS_FEED_DIAGNOSTIC,
                        "statement": "the SIP minute-bar response returned no bar for this name "
                        "while a source whose feed is not the acquisition feed recorded "
                        f"{int(n)} in-window bar(s) for the same session. The two feeds build "
                        "bars under different condition/aggregation rules, so the SIP feed is "
                        "NOT required to reproduce this bar. The original rows are preserved in "
                        "the cross-feed witness artifact and that archive stays authoritative "
                        "for this proxy. This says nothing about whether the name traded.",
                    }
                )
    return same, cross


def witness_artifact_rel(day: str) -> str:
    return f"{WITNESS_DIRNAME}/{day}.parquet"


def read_witness_rows(
    src: dict,
    day: str,
    names: Sequence[str],
    counts: dict[str, int],
    lo: int,
    hi: int,
    canonical_of: dict[str, str],
) -> pl.DataFrame:
    """The source's ORIGINAL in-window rows for `names`, keyed by parent+ordinal.

    row_ordinal is the 0-based ordinal inside the immutable parent archive,
    taken before any filtering, so (parent_sha256, row_ordinal) addresses one
    exact archive row and can be re-read by anyone.
    """
    p = Path(src["path"])
    cols = set(pl.scan_parquet(p).collect_schema().names())
    need = ["timestamp", "ticker", "open", "high", "low", "close", "volume"]
    missing = [c for c in need if c not in cols]
    if missing:
        raise ValueError(f"witness source {src['name']} at {p} lacks columns {missing}")
    d = date.fromisoformat(day)
    df = (
        pl.scan_parquet(p)
        .select(need)
        .with_row_index("row_ordinal")
        .filter(pl.col("timestamp").dt.date() == d)
        .filter(pl.col("ticker").is_in(list(names)))
        .collect()
    )
    if df.height == 0:
        return pl.DataFrame(schema=WITNESS_SCHEMA)
    et_et = pl.col("timestamp").dt.convert_time_zone("America/New_York")
    df = df.with_columns(
        (et_et.dt.hour().cast(pl.Int32) * 60 + et_et.dt.minute().cast(pl.Int32)).alias("et_minute")
    )
    if src["counts_window"] == "in_window":
        df = df.filter((pl.col("et_minute") >= lo) & (pl.col("et_minute") <= hi))
    if df.height == 0:
        return pl.DataFrame(schema=WITNESS_SCHEMA)
    df = (
        df.with_columns(
            pl.lit(src["sha256"]).cast(pl.String).alias("parent_sha256"),
            pl.col("row_ordinal").cast(pl.Int64),
            pl.col("timestamp").alias("canonical_time_utc"),
            pl.col("ticker").replace_strict(canonical_of, default=None).alias("_canonical"),
            pl.lit(src["name"]).cast(pl.String).alias("source_name"),
            pl.lit(str(p)).cast(pl.String).alias("source_path"),
            pl.lit(src["feed"]).cast(pl.String).alias("source_feed"),
            pl.lit(src["provider"]).cast(pl.String).alias("source_provider"),
            pl.lit(ACQUISITION_FEED).cast(pl.String).alias("fresh_feed"),
            pl.lit(0, dtype=pl.Int64).alias("fresh_n_bars"),
            pl.lit(CROSS_FEED_DIAGNOSTIC).cast(pl.String).alias("diagnostic"),
            pl.lit(day).cast(pl.String).alias("day"),
        )
        .with_columns(
            pl.col("_canonical").fill_null(pl.col("ticker")).alias("ticker"),
        )
        .drop("_canonical")
        .with_columns(
            pl.col("ticker")
            .replace_strict(counts, default=None)
            .cast(pl.Int64)
            .alias("source_n_bars_in_window")
        )
        .select(list(WITNESS_SCHEMA))
        .sort("row_ordinal")
    )
    return df


def collect_cross_feed_witnesses(
    plan: DayPlan, disagreements: list[dict], lo: int, hi: int
) -> tuple[pl.DataFrame, list[dict]]:
    """One witness frame for the day, plus any problem that makes it unusable.

    The declared evidence count and the preserved row count must agree exactly:
    a witness artifact that lost or invented rows is a corruption, not evidence.
    """
    frames: list[pl.DataFrame] = []
    problems: list[dict] = []
    by_source: dict[str, list[dict]] = {}
    for d in disagreements:
        by_source.setdefault(d["source"], []).append(d)
    src_by_name = {s["name"]: s for s in plan.evidence_sources}
    canonical_of = {p: c for c, p in plan.provider_of.items()}
    for sname, items in sorted(by_source.items()):
        src = src_by_name.get(sname)
        if src is None:
            problems.append({"error": f"cross-feed source {sname!r} is not a declared source"})
            continue
        if not src["witness_rows_available"]:
            problems.append(
                {
                    "error": f"cross-feed source {sname!r} cannot yield witness rows, so its "
                    "observed bars would be reported without their original rows"
                }
            )
            continue
        counts = {i["ticker"]: int(i["source_n_bars_in_window"]) for i in items}
        names = sorted(counts)
        try:
            df = read_witness_rows(src, plan.day, names, counts, lo, hi, canonical_of)
        except Exception as e:  # a witness we cannot read is an error, never a silent gap
            problems.append(
                {"error": f"cross-feed witness read failed for {sname!r}: {str(e)[:200]}"}
            )
            continue
        got = dict(df.group_by("ticker").len().iter_rows()) if df.height else {}
        for name in names:
            want = counts[name]
            have = int(got.get(name, 0))
            if have != want:
                problems.append(
                    {
                        "error": f"cross-feed witness for {name} in {sname} preserved {have} "
                        f"row(s) but the source recorded {want} in-window bar(s)",
                    }
                )
        if df.height:
            frames.append(df)
    if not frames:
        return pl.DataFrame(schema=WITNESS_SCHEMA), problems
    return pl.concat(frames, how="vertical").sort(["ticker", "row_ordinal"]), problems


# --------------------------------------------------------------------------- #
# provider access
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class RawBar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class ProviderError(RuntimeError):
    pass


def _retry_sleep(attempt: int, retry_after: float | None) -> None:
    if retry_after is not None and retry_after >= 0:
        time.sleep(min(retry_after, 60.0))
    else:
        time.sleep(min(2.0**attempt, 30.0))


class SdkFetcher:
    """alpaca-py StockHistoricalDataClient. Pagination is automatic (no limit)."""

    def __init__(self, attempts: int = 5) -> None:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        from alpaca.data.historical import StockHistoricalDataClient

        key = os.environ.get("ALPACA_API_KEY")
        secret = os.environ.get("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise ProviderError("ALPACA_API_KEY/ALPACA_SECRET_KEY are not set")
        self.client = StockHistoricalDataClient(key, secret)
        self.attempts = attempts

    def fetch(
        self, symbols: list[str], start: datetime, end: datetime, asof: str | None = None
    ) -> dict[str, list[RawBar]]:
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        # asof pins symbol resolution to that historical date. Without it the
        # provider applies its own default entity linking, which can attribute a
        # renamed entity's tape to the queried PIT name.
        req = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Minute,
            start=start,
            end=end,
            feed=DataFeed.SIP,
            adjustment=Adjustment.RAW,
            asof=asof or start.astimezone(ET).date().isoformat(),
        )
        last: Exception | None = None
        for attempt in range(self.attempts):
            try:
                res = self.client.get_stock_bars(req)
                out: dict[str, list[RawBar]] = {}
                for sym, bars in res.data.items():
                    out[sym] = [
                        RawBar(
                            ts=b.timestamp,
                            open=float(b.open),
                            high=float(b.high),
                            low=float(b.low),
                            close=float(b.close),
                            volume=float(b.volume or 0.0),
                        )
                        for b in bars
                    ]
                return out
            except Exception as e:  # 429/5xx/network; bounded, then recorded
                last = e
                if attempt + 1 < self.attempts:
                    _retry_sleep(attempt, None)
        raise ProviderError(f"sdk batch failed after {self.attempts} attempts: {str(last)[:200]}")


class RestFetcher:
    """Paginated REST /v2/stocks/bars. Follows next_page_token to exhaustion.

    Used when the SDK is unavailable and as the explicit-paging reference the
    self-test exercises (paginated, truncated and 429 responses).
    """

    BASE = "https://data.alpaca.markets"

    def __init__(
        self, session: Any | None = None, attempts: int = 5, base: str | None = None
    ) -> None:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        key = os.environ.get("ALPACA_API_KEY")
        secret = os.environ.get("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise ProviderError("ALPACA_API_KEY/ALPACA_SECRET_KEY are not set")
        if session is None:
            import requests

            session = requests.Session()
        self.session = session
        self.attempts = attempts
        self.base = base or self.BASE
        self.headers = {
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
        }

    def _get(self, params: dict) -> dict:
        # Retained so tests and the manifest can assert what was actually sent.
        self.last_params = dict(params)
        last: Exception | None = None
        for attempt in range(self.attempts):
            r = None
            try:
                r = self.session.get(
                    f"{self.base}/v2/stocks/bars", params=params, headers=self.headers, timeout=90
                )
            except Exception as e:
                last = e
                if attempt + 1 < self.attempts:
                    _retry_sleep(attempt, None)
                    continue
                raise ProviderError(f"rest transport failure: {str(e)[:200]}") from e
            if r.status_code == 429 or r.status_code >= 500:
                last = ProviderError(f"rest {r.status_code}")
                if attempt + 1 < self.attempts:
                    _retry_sleep(attempt, _retry_after(r))
                    continue
                raise ProviderError(f"rest {r.status_code} after {self.attempts} attempts")
            if r.status_code >= 400:
                raise ProviderError(f"rest {r.status_code}: {str(getattr(r, 'text', ''))[:200]}")
            return r.json()
        raise ProviderError(f"rest request failed: {str(last)[:200]}")

    def fetch(
        self, symbols: list[str], start: datetime, end: datetime, asof: str | None = None
    ) -> dict[str, list[RawBar]]:
        out: dict[str, list[RawBar]] = {s: [] for s in symbols}
        params = {
            "symbols": ",".join(symbols),
            "timeframe": "1Min",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "adjustment": "raw",
            "feed": "sip",
            # Explicit historical asof, pinned to the session day. Omitting it
            # lets the provider apply default entity linking and attribute a
            # renamed entity's bars to the queried PIT name.
            "asof": asof or start.astimezone(ET).date().isoformat(),
            "limit": 10000,
            "sort": "asc",
        }
        token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            if token:
                params["page_token"] = token
            js = self._get(params)
            bars = js.get("bars") or {}
            for sym, blist in bars.items():
                for b in blist:
                    out.setdefault(sym, []).append(
                        RawBar(
                            ts=_parse_ts(b["t"]),
                            open=float(b["o"]),
                            high=float(b["h"]),
                            low=float(b["l"]),
                            close=float(b["c"]),
                            volume=float(b.get("v") or 0.0),
                        )
                    )
            token = js.get("next_page_token")
            if not token:
                return out
            if token in seen_tokens:
                raise ProviderError(f"rest pagination repeated page_token {token!r}")
            seen_tokens.add(token)


def _retry_after(response: Any) -> float | None:
    try:
        return float(response.headers.get("Retry-After"))
    except Exception:
        return None


def _parse_ts(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if "." in s:
        head, tail = s.split(".", 1)
        digits, off = "", ""
        for i, ch in enumerate(tail):
            if ch.isdigit():
                digits += ch
            else:
                off = tail[i:]
                break
        s = f"{head}.{digits[:6]}{off}"
    return datetime.fromisoformat(s)


INVALID_SYMBOL_RE = re.compile(r'invalid symbol:\s*([^"\\]+)', re.I)


def fetch_bars(
    fetcher: Any,
    day: str,
    provider_symbols: list[str],
    lo: int,
    hi: int,
    max_invalid_per_batch: int = 250,
) -> tuple[dict[str, list[RawBar]], list[str], list[str], list[dict]]:
    """Batched, bounded-retry fetch.

    Returns (bars, invalid, request_failed, errors). The three symbol lists are
    disjoint and mean different things, which is why they are kept apart:
      invalid        - the provider REJECTED the spelling
      request_failed - we never completed a request for it, so nothing is known
                       about whether it traded. This used to be silently folded
                       into the zero-bar class, which asserted a positive
                       falsehood ("the provider accepted it and returned no
                       bar") for names whose request had simply died.
      neither        - a genuine provider zero
    Every request_failed symbol blocks admission.
    """
    start, end = window_utc(day, lo, hi)
    bars: dict[str, list[RawBar]] = {}
    invalid: list[str] = []
    request_failed: list[str] = []
    errors: list[dict] = []
    for bi, batch in enumerate(chunks(provider_symbols, BATCH_DEFAULT), 1):
        todo = list(batch)
        batch_no = 0
        while todo:
            try:
                got = fetcher.fetch(todo, start, end, day)
            except ProviderError as e:
                m = INVALID_SYMBOL_RE.search(str(e))
                if m:
                    bad = m.group(1).strip()
                    if bad in todo:
                        todo = [s for s in todo if s != bad]
                        invalid.append(bad)
                        batch_no += 1
                        if batch_no > max_invalid_per_batch:
                            errors.append(
                                {"batch": bi, "error": f"too many invalid symbols: {bad!r}"[:200]}
                            )
                            request_failed.extend(todo)
                            break
                        continue
                # A non-invalid-symbol failure abandons every symbol still in
                # todo. Each is recorded as request_failed so it is never
                # mistaken for a provider zero.
                errors.append({"batch": bi, "error": str(e)[:200], "n": len(todo)})
                request_failed.extend(todo)
                break
            for sym in todo:
                bars[sym] = list(got.get(sym) or [])
            break
    return bars, sorted(set(invalid)), sorted(set(request_failed)), errors


# --------------------------------------------------------------------------- #
# payload assembly
# --------------------------------------------------------------------------- #
def bars_to_frame(
    bars_by_provider: dict[str, list[RawBar]], provider_of: dict[str, str]
) -> pl.DataFrame:
    ts, o, h, low, c, v, tick, psym = [], [], [], [], [], [], [], []
    for provider, blist in bars_by_provider.items():
        canonicals = [k for k, p in provider_of.items() if p == provider]
        if len(canonicals) > 1:
            raise ValueError(
                f"provider spelling {provider!r} maps to multiple canonical names {canonicals}"
            )
        if not canonicals:
            continue
        canonical = canonicals[0]
        for b in blist:
            ts.append(b.ts)
            o.append(b.open)
            h.append(b.high)
            low.append(b.low)
            c.append(b.close)
            v.append(b.volume)
            tick.append(canonical)
            psym.append(provider)
    return pl.DataFrame(
        {
            "timestamp": pl.Series(ts, dtype=pl.Datetime("ns", "UTC")),
            "open": pl.Series(o, dtype=pl.Float64),
            "high": pl.Series(h, dtype=pl.Float64),
            "low": pl.Series(low, dtype=pl.Float64),
            "close": pl.Series(c, dtype=pl.Float64),
            "volume": pl.Series(v, dtype=pl.Float64),
            "ticker": pl.Series(tick, dtype=pl.String),
            "provider_symbol": pl.Series(psym, dtype=pl.String),
        },
        schema=BAR_SCHEMA,
    ).sort(["ticker", "timestamp"])


def validate_frame(df: pl.DataFrame, lo: int, hi: int) -> dict:
    """Mechanical payload checks. Never drops a row; the consumer grades quality."""
    n = df.height
    dup = df.group_by(["ticker", "timestamp"]).len().filter(pl.col("len") > 1).height if n else 0
    ts_et = df["timestamp"].dt.convert_time_zone("America/New_York")
    et = ts_et.dt.hour().cast(pl.Int64) * 60 + ts_et.dt.minute().cast(pl.Int64)
    # CLOSED interval: a bar stamped exactly at hi (16:05 ET) is IN window.
    # Treating hi as exclusive flagged 10 legitimate 16:05-stamped bars on
    # 2026-04-01 as corrupt and made the day unadmittable, and would have
    # disagreed with the baseline lane that already carries 412 bars at the
    # same stamp for the same session.
    out_of_window = int(df.filter((et < lo) | (et > hi)).height) if n else 0
    nonfinite = (
        int(
            df.filter(
                ~pl.all_horizontal(
                    *[pl.col(k).is_finite() for k in ("open", "high", "low", "close", "volume")]
                )
            ).height
        )
        if n
        else 0
    )
    nonpositive = (
        int(
            df.filter(
                (pl.col("low") <= 0)
                | (pl.col("high") < pl.col("low"))
                | (pl.col("open") > pl.col("high"))
                | (pl.col("close") > pl.col("high"))
                | (pl.col("open") < pl.col("low"))
                | (pl.col("close") < pl.col("low"))
            ).height
        )
        if n
        else 0
    )
    return {
        "rows": n,
        "symbols": int(df["ticker"].n_unique()) if n else 0,
        "duplicate_ticker_timestamp": dup,
        "out_of_window_rows": out_of_window,
        "nonfinite_rows": nonfinite,
        "nonpositive_or_inconsistent_rows": nonpositive,
        "window_et": [lo // 60 * 100 + lo % 60, hi // 60 * 100 + hi % 60],
    }


# --------------------------------------------------------------------------- #
# day acquisition (atomic, resumable, drift-refusing)
# --------------------------------------------------------------------------- #
class DayPaths:
    def __init__(self, out_data: Path, day: str) -> None:
        self.bar = out_data / "bars" / f"{day}.parquet"
        self.manifest = out_data / "bars" / f"{day}.manifest.json"
        self.roster = out_data / "rosters" / f"{day}.json"


def existing_manifest(paths: DayPaths) -> dict | None:
    if paths.manifest.exists():
        try:
            return load_json(paths.manifest)
        except Exception:
            return None
    return None


def drift_reasons(
    man: dict, day: str, code_sha: str, config_sha: str, contract_sha: str
) -> list[str]:
    out = []
    if man.get("day") != day:
        out.append(f"manifest day {man.get('day')!r} != {day!r}")
    for key, want in (
        ("code_sha256", code_sha),
        ("config_sha256", config_sha),
        ("contract_sha256", contract_sha),
    ):
        if man.get(key) != want:
            out.append(f"{key} drift: stored {str(man.get(key))[:12]} != current {want[:12]}")
    if paths_sha := man.get("sha256"):
        p = Path(man.get("file_abs") or "")
        if p.exists() and sha256_file(p) != paths_sha:
            out.append("payload sha256 does not match the manifest")
    return out


def acquire_day(
    plan: DayPlan,
    out_data: Path,
    fetcher: Any,
    code_sha: str,
    config_sha: str,
    contract_sha: str,
    alias_map_sha: str,
    pit_sha: str,
    lo: int,
    hi: int,
    policy_sha: str | None = None,
    verbose: bool = True,
) -> dict:
    paths = DayPaths(out_data, plan.day)
    existing = existing_manifest(paths)
    if existing is not None:
        # Config/producer drift is checked FIRST, even for an admitted day whose
        # bytes still hash correctly: a day acquired under a different window,
        # batch, client, contract or producer is a different acquisition, and
        # quietly keeping the old bytes would hide that.
        drift = drift_reasons(existing, plan.day, code_sha, config_sha, contract_sha)
        drift = [d for d in drift if not d.startswith("payload sha256")]
        if drift:
            raise SystemExit(
                f"{plan.day}: acquisition run drift ({'; '.join(drift)}). The existing "
                "day was produced under a different configuration or producer version. "
                "Refusing to overwrite; acquire into a NEW version directory "
                "(--out-data .../acquisition/vN)."
            )
        if existing.get("status") == REQUIRED_STATUS_ADMITTED and paths.bar.exists():
            if sha256_file(paths.bar) == existing.get("sha256"):
                if verbose:
                    print(f"{plan.day}: skip (admitted sha={existing['sha256'][:12]})", flush=True)
                return {**existing, "action": "skipped_existing_admitted"}
            raise SystemExit(
                f"{plan.day}: admitted payload bytes do not match their manifest sha. "
                "Refusing to overwrite; acquire into a NEW version directory "
                "(--out-data .../acquisition/vN)."
            )
    elif paths.bar.exists():
        raise SystemExit(
            f"{plan.day}: payload exists with no manifest (torn write: {paths.bar}). "
            "Refusing to overwrite; remove it or use a new version directory."
        )

    roster_doc = plan.to_json()
    roster_doc["alias_map_sha256"] = alias_map_sha
    roster_doc["pit_sha256"] = pit_sha
    atomic_write_json(paths.roster, roster_doc)
    roster_sha = sha256_file(paths.roster)

    t0 = time.time()
    provider_symbols = [plan.provider_of[s] for s in plan.requested]
    bars, invalid_providers, failed_providers, errors = fetch_bars(
        fetcher, plan.day, provider_symbols, lo, hi
    )
    provider_to_canonical = {p: c for c, p in plan.provider_of.items()}
    invalid = sorted(provider_to_canonical.get(p, p) for p in invalid_providers)
    request_failed = sorted(provider_to_canonical.get(p, p) for p in failed_providers)

    with_data = {c for c, p in plan.provider_of.items() if bars.get(p)}
    # A symbol we never completed a request for is NOT a provider zero. Keeping
    # the two apart is the whole point: a failed request asserts nothing about
    # whether the name traded, and the manifest must not claim otherwise.
    zeros = set(plan.requested) - with_data - set(invalid) - set(request_failed)
    zero_bars = sorted(zeros)
    df = bars_to_frame(
        {p: b for p, b in bars.items() if p in provider_to_canonical}, plan.provider_of
    )
    checks = validate_frame(df, lo, hi)
    if checks["duplicate_ticker_timestamp"]:
        errors.append(
            {"error": f"duplicate (ticker,timestamp) rows: {checks['duplicate_ticker_timestamp']}"}
        )
    if checks["out_of_window_rows"]:
        errors.append(
            {
                "error": f"rows outside the declared {lo}-{hi} ET window: "
                f"{checks['out_of_window_rows']}"
            }
        )

    # Evidence oracle. Every positive bar count is attributed to the SOURCE that
    # recorded it, and each source carries a declared feed. Only a source whose
    # feed is the feed being acquired can oblige this response to emit a bar: a
    # source with an unknown/different feed is preserved as a cross-feed witness,
    # never an unresolved contradiction and never a claim that the name did not
    # trade. Collapsing the sources into one count is exactly what made the
    # HF/Finnhub-derived B1 fallback able to demand a SIP minute bar.
    same_feed_zeros, cross_feed = classify_evidence(plan, zeros)

    # Preserve the ORIGINAL rows behind every cross-feed disagreement. A witness
    # that cannot be reproduced row-for-row is an error and blocks admission,
    # rather than being reported as evidence it is not.
    witness_df = pl.DataFrame(schema=WITNESS_SCHEMA)
    if cross_feed:
        witness_df, witness_problems = collect_cross_feed_witnesses(plan, cross_feed, lo, hi)
        for w in witness_problems:
            errors.append({"error": w["error"]})
    witness_names = sorted({d["ticker"] for d in cross_feed})
    witness_rel = witness_artifact_rel(plan.day) if witness_df.height else None
    witness_sha = None
    if witness_rel is not None:
        atomic_write_parquet(witness_df, out_data / witness_rel)
        witness_sha = sha256_file(out_data / witness_rel)

    # Complete SIP request/coverage is a DIFFERENT question from the cross-feed
    # disagreement count: a day can have a complete SIP response and still carry
    # witnesses, and an unreproduced witness is not a coverage failure.
    accounted = with_data | zeros | set(invalid) | set(request_failed)
    unaccounted = sorted(set(plan.requested) - accounted)
    if unaccounted:
        errors.append({"error": f"requested symbols in no accounting class: {unaccounted[:20]}"})
    sip_complete = not unaccounted and not request_failed and not errors

    status = REQUIRED_STATUS_ADMITTED
    refusal: list[str] = []
    if errors:
        status = "incomplete"
        refusal.append("api_or_payload_errors")
    if request_failed:
        # We never completed a request for these names. Nothing is known about
        # whether they traded, so the day cannot be admitted.
        status = "incomplete"
        refusal.append("request_failed_symbols")
    if same_feed_zeros:
        # The source that recorded this positive bar count IS the feed being
        # acquired, so the missing bar is unresolved: a provider revision or an
        # unrequestable spelling. Cross-feed disagreements never land here.
        status = "incomplete"
        refusal.append("same_feed_contradictory_zeros")
    if df.height == 0:
        status = "incomplete"
        refusal.append("empty_payload")

    # Obligation coverage, split by what actually carries the name. A B1 name the
    # replaced fallback carried and the SIP response did not reproduce is covered
    # by the preserved cross-feed witness, which is stated, not assumed.
    baseline_covers = (
        set() if plan.scope == "feb2025" else set(plan.raw_existing) | set(plan.alias_existing)
    )
    duty = set(plan.expected_present)
    uncovered = sorted(duty - with_data - baseline_covers - set(witness_names))
    obligation_coverage = {
        "payload": len(duty & with_data),
        "baseline_lane": len(duty & baseline_covers),
        "cross_feed_witness": len(duty & set(witness_names)),
        "uncovered": uncovered,
        "uncovered_n": len(uncovered),
    }

    atomic_write_parquet(df, paths.bar)
    payload_sha = sha256_file(paths.bar)
    man = {
        "day": plan.day,
        "scope": plan.scope,
        "blocker_id": BLOCKER_OF_SCOPE[plan.scope],
        "status": status,
        "refusal_reasons": refusal,
        "file": f"bars/{plan.day}.parquet",
        "file_abs": str(paths.bar),
        "sha256": payload_sha,
        "rows": int(df.height),
        "schema": {k: str(v) for k, v in df.schema.items()},
        "source": {
            "provider": "alpaca",
            "feed": "sip",
            "adjustment": "raw",
            "timeframe": "1Min",
            "asof": plan.day,
            "asof_semantics": "explicit historical asof pinned to the session day, so the "
            "provider resolves the symbol as it was on that date instead of "
            "default-remapping a renamed entity to its current successor.",
            "window_et": f"{lo // 60:02d}:{lo % 60:02d}-{hi // 60:02d}:{hi % 60:02d}",
            "window_et_bounds": [lo, hi],
            "window_bounds_inclusive": True,
            "window_utc": [x.isoformat() for x in window_utc(plan.day, lo, hi)],
            "window_note": "closed interval [lo, hi] ET: a bar stamped exactly at the upper "
            "bound is IN window. Matches the frozen observation contract's closed "
            "projection window and the provider's inclusive end.",
            "client": "alpaca-py" if isinstance(fetcher, SdkFetcher) else "rest",
        },
        "requested_at": datetime.now(UTC).isoformat(),
        "requested_symbols": len(plan.requested),
        "symbols_with_data": len(with_data),
        "symbols_zero_bars": zero_bars,
        "symbols_invalid": invalid,
        "symbols_request_failed": request_failed,
        "aliases": plan.aliases_used,
        "errors": errors,
        "checks": checks,
        "obligation": {
            "rule": "feb2025 replaces the floored fallback, so PIT x (compact-observed UNION "
            "replaced-baseline in-window) must be carried by this payload OR preserved as a "
            "cross-feed witness. The replaced fallback is not the acquisition feed, so the SIP "
            "response is never required to reproduce its bars. aprmay2026 supplements the "
            "baseline lane, which remains in place.",
            "baseline_standing": plan.baseline.get("standing"),
            "required_names": len(plan.expected_present),
            "observed_evidence_names": len(plan.observed_bars),
            "coverage": obligation_coverage,
            "cross_feed_witness_names": witness_names,
        },
        "sip_coverage": {
            "requested_symbols": len(plan.requested),
            "symbols_with_data": len(with_data),
            "symbols_zero_bars": len(zero_bars),
            "symbols_invalid": len(invalid),
            "symbols_request_failed": len(request_failed),
            "unaccounted_symbols": unaccounted,
            "complete": bool(sip_complete),
            "meaning": "the outcome of the SIP request itself, for every requested symbol. It "
            "is a different question from the cross-feed disagreement count: a complete SIP "
            "response may still carry cross-feed witnesses, and an unreproduced witness is "
            "not a coverage failure.",
        },
        "evidence_crosscheck": {
            "acquisition_feed": ACQUISITION_FEED,
            "sources": plan.evidence_sources,
            "compared_names": sum(1 for s in plan.requested if plan.observed_by_source.get(s)),
            "same_feed_contradictory_zeros": same_feed_zeros,
            "same_feed_contradictory_zero_count": len(same_feed_zeros),
            "cross_feed_disagreements": cross_feed,
            "cross_feed_disagreement_count": len(cross_feed),
            "cross_feed_names": witness_names,
            "witness_artifact": witness_rel,
            "witness_sha256": witness_sha,
            "witness_rows": int(witness_df.height),
            "witness_key": [*WITNESS_KEY, "canonical_time_utc"],
            "witness_role": "legacy_cross_feed_proxy_not_sip",
            "witness_rule": "the witness artifact holds the ORIGINAL in-window rows of the "
            "non-SIP source, keyed by (parent archive sha256, 0-based row ordinal). It is not "
            "a SIP lane input, is never relabelled as SIP, and the archive original stays "
            "authoritative for that proxy.",
        },
        "roster_sha256": roster_sha,
        "roster": f"rosters/{plan.day}.json",
        "input_sha256": {
            "pit_parquet": pit_sha,
            "compact_parquet": plan.compact.get("sha256"),
            "compact_manifest": plan.compact.get("manifest_sha256"),
            "baseline_lane": plan.baseline.get("sha256"),
            "alias_map": alias_map_sha,
            "source_policy": policy_sha,
        },
        "code_sha256": code_sha,
        "config_sha256": config_sha,
        "contract_sha256": contract_sha,
        "elapsed_s": round(time.time() - t0, 1),
    }
    atomic_write_json(paths.manifest, man)
    if verbose:
        print(
            f"{plan.day}: {status} rows={man['rows']} syms={man['symbols_with_data']}/"
            f"{man['requested_symbols']} zero={len(zero_bars)} invalid={len(invalid)} "
            f"same_feed_zeros={len(same_feed_zeros)} cross_feed={len(cross_feed)} "
            f"errors={len(errors)} {man['elapsed_s']}s",
            flush=True,
        )
    return {**man, "action": "acquired"}


# --------------------------------------------------------------------------- #
# stages
# --------------------------------------------------------------------------- #
def build_context(out_data: Path, contract: dict, policy: dict) -> dict:
    data = resolve_data_root()
    fetch = contract["fetch"]
    lo, hi = int(fetch["window_et"][0]), int(fetch["window_et"][1])
    return {
        "data": data,
        "out_data": out_data,
        "policy": policy,
        "policy_info": validate_policy(policy, data),
        "pit": PitIndex(data / "pit/pit_symbols.parquet"),
        "compact": CompactIndex(data / "sip/universe/rth"),
        "lanes": LaneIndex(),
        "aliases": load_alias_map(),
        "lo": lo,
        "hi": hi,
        "config": {
            "feed": fetch["feed"],
            "adjustment": fetch["adjustment"],
            "timeframe": fetch["timeframe_minutes"],
            "window_et": [lo, hi],
            "batch_size": BATCH_DEFAULT,
            "max_attempts": fetch["max_attempts"],
            "asof": "session_day",
        },
        "config_sha": sha256_json(
            {
                "scope": None,
                "client": None,
                "feed": fetch["feed"],
                "adjustment": fetch["adjustment"],
                "timeframe": fetch["timeframe_minutes"],
                "window_et": [lo, hi],
                "batch_size": BATCH_DEFAULT,
                "max_attempts": fetch["max_attempts"],
                "asof": "session_day",
            }
        ),
        "contract_sha": sha256_file(CONTRACT_PATH),
        "code_sha": sha256_file(HERE),
        "alias_map_sha": sha256_file(ALIAS_MAP_PATH),
        "policy_sha": sha256_file(POLICY_PATH),
    }


def assert_declared_root(ctx: dict, scope: str, out_data: Path) -> dict:
    """A blocker may only be produced in the root its policy declares.

    Without this, a B1 rule change could be run against the root that holds B2's
    verified bytes, and the version reference in the joint admission would point
    at a directory that no longer holds what it was pinned to.
    """
    bid = BLOCKER_OF_SCOPE[scope]
    ref = ctx["policy_info"]["refs"][bid]
    if Path(out_data).resolve() != ref["root"]:
        raise SystemExit(
            f"{bid} ({scope}): the source policy declares root {ref['root_rel']} "
            f"({ref['root']}) but this run targets {Path(out_data).resolve()}. Acquire or verify "
            "into the declared root, or declare the intended version in source_policy.json - "
            "never re-point an already-verified root."
        )
    return ref


def assert_contract_pinned(ctx: dict, ref: dict) -> None:
    """The frozen contract a blocker was captured under may not silently move.

    The acquisition contract is itself a parent of every day manifest, so it is
    pinned per blocker alongside the producer. A contract revision is a new
    blocker version, never a quiet re-acquisition under the old root.
    """
    want = ref["producer"]["contract_sha256_at_capture"]
    if ctx["contract_sha"] != want:
        raise SystemExit(
            f"{ref['blocker_id']}: the acquisition contract hashes {ctx['contract_sha'][:12]} but "
            f"this blocker's pinned capture is {want[:12]}. The contract is a parent of every day "
            "manifest; a revision requires a new blocker version and root, not a re-acquisition "
            "under the pinned one."
        )


def stage_plan(args: argparse.Namespace) -> dict:
    contract = load_contract()
    policy = load_policy()
    out_data = Path(args.out_data)
    ctx = build_context(out_data, contract, policy)
    ref = assert_declared_root(ctx, args.scope, out_data)
    days = scope_days(args.scope, _day_filter(args), sim.dev_days())
    bars_per_minute = int(contract["estimator"]["bars_per_day_reference"])
    per_day, totals = (
        [],
        {
            "requested_symbols": 0,
            "requests_at_least": 0,
            "expected_rows_compact_backed": 0,
            "estimated_bytes": 0,
        },
    )
    for day in days:
        plan = plan_day(
            day,
            args.scope,
            ctx["pit"],
            ctx["aliases"],
            ctx["compact"],
            ctx["lanes"],
            ctx["data"],
            policy,
        )
        est = estimate_day(plan, ctx["compact"].n_bars(day), bars_per_minute)
        per_day.append(
            {
                "day": day,
                **est,
                "pit_n": plan.pit_n,
                "aliases": len(plan.aliases_used),
                "unavailable": {k: len(v) for k, v in plan.unavailable.items()},
                "raw_existing": len(plan.raw_existing),
                "alias_existing": len(plan.alias_existing),
            }
        )
        for k in totals:
            totals[k] += int(est[k])
    doc = {
        "schema": "atlas.acquisition.plan.v0",
        "stage": "plan",
        "scope": args.scope,
        "blocker_id": BLOCKER_OF_SCOPE[args.scope],
        "blocker_version": ref["version"],
        "acquisition_root": str(out_data),
        "days": days,
        "n_days": len(days),
        "per_day": per_day,
        "totals": totals,
        "inputs": {
            "pit_parquet_sha256": ctx["pit"].sha256,
            "alias_map_sha256": ctx["alias_map_sha"],
            "contract_sha256": ctx["contract_sha"],
            "source_policy_sha256": ctx["policy_sha"],
            "code_sha256": ctx["code_sha"],
        },
        "estimator": {
            "requests": "ceil(requested_symbols / 500) batches, one paginated request stream each",
            "rows": "per-symbol bar count from the compact SIP source; bars_per_day_reference "
            f"({bars_per_minute}) for names the compact source does not carry",
            "bytes": f"requested rows x {BYTES_PER_ROW_ESTIMATE} planning bytes/row (measured "
            "size is always read back from the written file)",
        },
        "error_semantics": {
            "invalid_symbol": "provider rejects the spelling; recorded with reason, never renamed",
            "zero_bars": "provider returned no bar for a valid spelling; recorded, never "
            "fabricated",
            "same_feed_contradictory_zero": "a source whose DECLARED feed is the feed being "
            "acquired recorded n_bars>0 and the fresh pull has none: unresolved, blocks "
            "admission",
            "cross_feed_bar_not_reproduced": "a source with a different/unknown feed recorded "
            "n_bars>0 and the fresh SIP pull has none: the SIP feed is not required to emit a "
            "bar under other condition rules, so the diagnostic is reported and the source's "
            "original rows are preserved as a witness. Non-blocking, and never a claim that "
            "the name did not trade",
            "api_error": "429/5xx retried with bounded backoff; on exhaustion the day is "
            "incomplete and unadmitted",
            "request_failed": "a symbol whose request never completed is recorded separately "
            "and blocks admission; it is never rendered as a zero",
        },
    }
    out_ev = _out_evidence(args, out_data)
    atomic_write_json(out_ev / f"acquisition_plan_{args.scope}.json", doc)
    return doc


def _day_filter(args: argparse.Namespace) -> list[str] | None:
    if not args.days:
        return None
    return [d.strip() for d in args.days.split(",") if d.strip()]


def _out_evidence(args: argparse.Namespace, out_data: Path) -> Path:
    p = Path(args.out_evidence) if args.out_evidence else out_data / "evidence"
    return p if p.is_absolute() else ROOT / p


def stage_acquire(args: argparse.Namespace) -> dict:
    contract = load_contract()
    policy = load_policy()
    out_data = Path(args.out_data)
    ctx = build_context(out_data, contract, policy)
    ref = assert_declared_root(ctx, args.scope, out_data)
    assert_contract_pinned(ctx, ref)
    days = scope_days(args.scope, _day_filter(args), sim.dev_days())
    fetcher = (
        SdkFetcher(attempts=int(contract["fetch"]["max_attempts"]))
        if args.client == "sdk"
        else RestFetcher(attempts=int(contract["fetch"]["max_attempts"]))
    )
    # The scope and the client are part of the run's configuration, so they are
    # inside the hash: a day written by a different scope/client never resumes.
    config_sha = sha256_json({**ctx["config"], "scope": args.scope, "client": args.client})
    results = []
    for day in days:
        plan = plan_day(
            day,
            args.scope,
            ctx["pit"],
            ctx["aliases"],
            ctx["compact"],
            ctx["lanes"],
            ctx["data"],
            policy,
        )
        results.append(
            acquire_day(
                plan,
                out_data,
                fetcher,
                ctx["code_sha"],
                config_sha,
                ctx["contract_sha"],
                ctx["alias_map_sha"],
                ctx["pit"].sha256,
                ctx["lo"],
                ctx["hi"],
                policy_sha=ctx["policy_sha"],
            )
        )
    admitted = [r["day"] for r in results if r["status"] == REQUIRED_STATUS_ADMITTED]
    return {
        "schema": "atlas.acquisition.acquire.v0",
        "stage": "acquire",
        "scope": args.scope,
        "blocker_id": ref["blocker_id"],
        "blocker_version": ref["version"],
        "acquisition_root": str(out_data),
        "days": days,
        "admitted": admitted,
        "incomplete": [r["day"] for r in results if r["status"] != REQUIRED_STATUS_ADMITTED],
        "per_day": [
            {
                k: r[k]
                for k in (
                    "day",
                    "status",
                    "action",
                    "rows",
                    "sha256",
                    "requested_symbols",
                    "symbols_with_data",
                    "symbols_zero_bars",
                    "symbols_invalid",
                    "errors",
                )
            }
            for r in results
        ],
    }


def verify_day(
    plan: DayPlan, out_data: Path, expected: dict, contract_window: tuple[int, int]
) -> tuple[list[dict], dict]:
    """Re-derive every claim from the bytes on disk. Nothing is taken on trust.

    `expected` carries the identity this blocker's producer is PINNED to (the
    code and contract snapshots), not today's tool hash: a blocker verified under
    its own captured producer stays verifiable after an unrelated blocker's rule
    change. The current shared inputs (alias map, PIT) are still compared.
    """
    contract_lo, contract_hi = contract_window
    paths = DayPaths(out_data, plan.day)
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"check": f"{plan.day}:{name}", "ok": bool(ok), "detail": detail})

    if not paths.manifest.exists():
        add("manifest_present", False, str(paths.manifest))
        return checks, {"day": plan.day, "status": "missing"}
    man = load_json(paths.manifest)
    payload = paths.bar
    add("manifest_day_matches_filename", man.get("day") == plan.day, man.get("day"))
    add("scope", man.get("scope") == plan.scope, man.get("scope"))
    src = man.get("source") or {}
    add("source_feed_sip", src.get("feed") == "sip", src.get("feed"))
    add("source_adjustment_raw", src.get("adjustment") == "raw", src.get("adjustment"))
    if not payload.exists():
        add("payload_present", False, str(payload))
        return checks, {"day": plan.day, "status": "incomplete", "manifest": man}
    add("payload_present", True)
    sha = sha256_file(payload)
    add(
        "payload_sha256_matches_manifest",
        sha == man.get("sha256"),
        {"file": sha, "manifest": man.get("sha256")},
    )
    if paths.roster.exists():
        add("roster_sha256_matches_manifest", sha256_file(paths.roster) == man.get("roster_sha256"))
    else:
        add("roster_sha256_matches_manifest", False, "roster missing")
    add(
        "code_sha256_matches_pinned_producer",
        man.get("code_sha256") == expected.get("code_sha256"),
        {"manifest": man.get("code_sha256"), "pinned": expected.get("code_sha256")},
    )
    add(
        "contract_sha256_matches_pinned_capture",
        man.get("contract_sha256") == expected.get("contract_sha256"),
        {"manifest": man.get("contract_sha256"), "pinned": expected.get("contract_sha256")},
    )
    add(
        "alias_map_sha256_matches_current",
        (man.get("input_sha256") or {}).get("alias_map") == sha256_file(ALIAS_MAP_PATH),
        (man.get("input_sha256") or {}).get("alias_map"),
    )
    if expected.get("source_policy_sha256"):
        add(
            "source_policy_sha256_matches_pinned",
            (man.get("input_sha256") or {}).get("source_policy")
            == expected["source_policy_sha256"],
            (man.get("input_sha256") or {}).get("source_policy"),
        )

    df = pl.read_parquet(payload)
    add("schema_columns", set(df.columns) == set(BAR_SCHEMA), sorted(df.columns))
    add(
        "schema_types",
        str(df.schema.get("timestamp")) == "Datetime(time_unit='ns', time_zone='UTC')"
        and all(str(df.schema[c]) == "Float64" for c in ("open", "high", "low", "close", "volume"))
        and str(df.schema.get("ticker")) == "String"
        and str(df.schema.get("provider_symbol")) == "String",
        {c: str(t) for c, t in df.schema.items()},
    )
    lo, hi = _window_from_manifest(man)
    add("manifest_window_equals_contract", (lo, hi) == (contract_lo, contract_hi), [lo, hi])
    # The manifest declares its window twice: as ET bounds and as the UTC
    # instants actually sent. If those two disagree the manifest is
    # self-contradictory and no reader can know which one the payload honours,
    # so it is refused rather than resolved by preferring one field.
    sent = (man.get("source") or {}).get("window_utc")
    expected_sent = [x.isoformat() for x in window_utc(plan.day, lo, hi)]
    add(
        "manifest_window_utc_matches_bounds",
        sent == expected_sent,
        {"declared": sent, "from_bounds": expected_sent},
    )
    readable = (man.get("source") or {}).get("window_et")
    readable_expected = f"{lo // 60:02d}:{lo % 60:02d}-{hi // 60:02d}:{hi % 60:02d}"
    add(
        "manifest_window_readable_matches_bounds",
        readable == readable_expected,
        {"declared": readable, "from_bounds": readable_expected},
    )
    v = validate_frame(df, lo, hi)
    add(
        "no_duplicate_ticker_timestamp",
        v["duplicate_ticker_timestamp"] == 0,
        v["duplicate_ticker_timestamp"],
    )
    add("all_rows_inside_window", v["out_of_window_rows"] == 0, v["out_of_window_rows"])
    add("ohlc_finite", v["nonfinite_rows"] == 0, v["nonfinite_rows"])
    add(
        "ohlc_positive_and_consistent",
        v["nonpositive_or_inconsistent_rows"] == 0,
        v["nonpositive_or_inconsistent_rows"],
    )

    present = set(df["ticker"].unique().to_list()) if df.height else set()
    zero_declared = set(man.get("symbols_zero_bars") or [])
    invalid_declared = set(man.get("symbols_invalid") or [])
    failed_declared = set(man.get("symbols_request_failed") or [])
    accounted = present | zero_declared | invalid_declared | failed_declared
    add(
        "all_requested_symbols_accounted",
        set(plan.requested) <= accounted,
        sorted(set(plan.requested) - accounted)[:20],
    )
    # The three symbol classes are disjoint by contract. A name cannot be both a
    # provider zero and a rejected spelling, and a failed request is never a zero.
    pairs = (
        zero_declared & invalid_declared,
        zero_declared & failed_declared,
        invalid_declared & failed_declared,
    )
    add("symbol_classes_disjoint", not any(pairs), [sorted(p) for p in pairs if p])
    add("no_request_failed_symbols", not failed_declared, sorted(failed_declared)[:20])
    add(
        "no_unrequested_symbol_in_payload",
        present <= set(plan.requested),
        sorted(present - set(plan.requested))[:20],
    )
    add("manifest_errors_empty", not man.get("errors"), man.get("errors"))
    add("status_complete", man.get("status") == REQUIRED_STATUS_ADMITTED, man.get("status"))

    # REQUEST axis: was the SIP request/response complete for every requested
    # symbol? This is a different question from the evidence axis below, and it
    # never includes the cross-feed disagreement count.
    sipcov = man.get("sip_coverage") or {}
    sip_recomputed = (
        not (sipcov.get("unaccounted_symbols") or [])
        and int(sipcov.get("symbols_request_failed") or 0) == 0
        and not (man.get("errors") or [])
    )
    add(
        "sip_coverage_complete",
        sipcov.get("complete") is True and sip_recomputed,
        {
            "declared": sipcov.get("complete"),
            "recomputed": sip_recomputed,
            "unaccounted": sipcov.get("unaccounted_symbols"),
            "request_failed": sipcov.get("symbols_request_failed"),
        },
    )

    # EVIDENCE axis: each positive bar count is classed by the feed of the source
    # that recorded it. Same-feed expected-positive-zero is unresolved and
    # blocks. A source whose feed is not the acquisition feed cannot oblige the
    # SIP response; it is reported and its original rows are preserved.
    ev = man.get("evidence_crosscheck") or {}
    same_feed = ev.get("same_feed_contradictory_zeros") or []
    add("no_same_feed_contradictory_zeros", not same_feed, same_feed[:10])
    cross_feed = ev.get("cross_feed_disagreements") or []
    add(
        "cross_feed_disagreements_reported",
        ev.get("cross_feed_disagreement_count") == len(cross_feed)
        and ev.get("cross_feed_names") == sorted({d.get("ticker") for d in cross_feed})
        and all(d.get("diagnostic") == CROSS_FEED_DIAGNOSTIC for d in cross_feed)
        and all(d.get("source_feed") not in (None, ACQUISITION_FEED) for d in cross_feed)
        and all(int(d.get("fresh_n_bars", -1)) == 0 for d in cross_feed),
        {
            "declared_count": ev.get("cross_feed_disagreement_count"),
            "items": len(cross_feed),
            "feeds": sorted({str(d.get("source_feed")) for d in cross_feed}),
        },
    )
    witness_rel = ev.get("witness_artifact")
    witness_rows_declared = int(ev.get("witness_rows") or 0)
    witness_names = set(ev.get("cross_feed_names") or [])
    witness_problems: list[str] = []
    if cross_feed:
        wp = out_data / str(witness_rel) if witness_rel else None
        if not witness_rel or not str(witness_rel).startswith(f"{WITNESS_DIRNAME}/"):
            witness_problems.append("witness artifact is not declared under cross_feed_witnesses/")
        elif not wp.exists():
            witness_problems.append("witness artifact does not exist")
        elif sha256_file(wp) != ev.get("witness_sha256"):
            witness_problems.append("witness sha256 does not match the manifest")
        else:
            wdf = pl.read_parquet(wp)
            if list(wdf.columns) != list(WITNESS_SCHEMA):
                witness_problems.append("witness schema differs from the declared witness schema")
            elif wdf.height != witness_rows_declared:
                witness_problems.append(
                    f"witness holds {wdf.height} rows, manifest declares {witness_rows_declared}"
                )
            else:
                if wdf.select([*WITNESS_KEY, "canonical_time_utc"]).unique().height != wdf.height:
                    witness_problems.append(
                        "witness key (parent_sha256, row_ordinal, time) not unique"
                    )
                if not witness_names <= set(wdf["ticker"].unique().to_list()):
                    witness_problems.append("a cross-feed name has no witness row")
                if set(wdf["source_feed"].unique().to_list()) & {ACQUISITION_FEED}:
                    witness_problems.append("a witness row claims the acquisition feed")
                if set(wdf["diagnostic"].unique().to_list()) != {CROSS_FEED_DIAGNOSTIC}:
                    witness_problems.append("witness diagnostic is not the cross-feed diagnostic")
                if set(wdf["fresh_n_bars"].unique().to_list()) != {0}:
                    witness_problems.append("a witness row claims fresh SIP bars")
    else:
        if witness_rel is not None or witness_rows_declared or witness_names:
            witness_problems.append("a witness is declared with no cross-feed disagreement")
    add(
        "cross_feed_witnesses_preserved",
        not witness_problems,
        {
            "problems": witness_problems,
            "rows": witness_rows_declared,
            "names": sorted(witness_names),
        },
    )
    # Only a witness that verified above may carry an obligated name.
    witness_covers = witness_names if not witness_problems else set()

    # Coverage of the OBLIGATION set depends on the scope's standing:
    #   feb2025   the acquired day file REPLACES the floored fallback, so every
    #             obligated name must be carried by THIS payload or preserved in
    #             the cross-feed witness. The obligation is PIT x
    #             (compact-observed UNION replaced-baseline in-window): a name
    #             the fallback carried but the compact source never saw is still
    #             owed, and dropping it silently was the bug that let BK/ARMN
    #             vanish from 19 of 19 days with status=complete. The fallback is
    #             NOT the acquisition feed, so its names can only ever be carried
    #             as witnesses - the SIP response is never required to reproduce
    #             them.
    #   aprmay2026 the acquired day file SUPPLEMENTS the baseline lane, so an
    #             obligated name is covered when it is in this payload, already in
    #             the baseline lane for that day, or preserved as a witness.
    baseline_covers = (
        set() if plan.scope == "feb2025" else set(plan.raw_existing) | set(plan.alias_existing)
    )
    covered = present | baseline_covers | witness_covers
    obligation = set(plan.expected_present)
    add(
        "all_obligated_symbols_covered",
        obligation <= covered,
        sorted(obligation - covered)[:20],
    )
    # The manifest's own coverage accounting must agree with the recomputation;
    # a manifest claiming coverage it does not have is refused.
    cov = (man.get("obligation") or {}).get("coverage") or {}
    declared_cov = (
        len(obligation & present),
        len(obligation & baseline_covers),
        len(obligation & witness_covers),
    )
    add(
        "obligation_coverage_matches_payload",
        (cov.get("payload"), cov.get("baseline_lane"), cov.get("cross_feed_witness"))
        == declared_cov
        and set(cov.get("uncovered") or []) == set(obligation - covered),
        {"declared": cov, "recomputed": declared_cov},
    )
    # Every obligated name must carry positive bar-count evidence from some
    # source; an obligation asserted without evidence would be unfalsifiable.
    add(
        "obligation_is_evidence_backed",
        all(plan.observed_bars.get(n, 0) > 0 for n in obligation),
        sorted(n for n in obligation if plan.observed_bars.get(n, 0) <= 0)[:20],
    )
    return checks, {
        "day": plan.day,
        "status": man.get("status"),
        "manifest": man,
        "checks": v,
        "covered_expected": len(set(plan.expected_present) & covered),
        "expected_present": len(plan.expected_present),
    }


def _window_from_manifest(man: dict) -> tuple[int, int]:
    """The window the payload was actually acquired under, read back from the
    manifest's own record (never re-derived from today's contract silently).

    window_et_bounds is authoritative when present, so the exact integers the
    producer validated against are the ones verify re-validates against. The
    readable HH:MM-HH:MM string is the fallback for older manifests.
    """
    src = man.get("source") or {}
    bounds = src.get("window_et_bounds")
    if isinstance(bounds, list) and len(bounds) == 2:
        lo, hi = int(bounds[0]), int(bounds[1])
        if not (src.get("window_bounds_inclusive", True) and lo <= hi):
            raise ValueError(f"manifest window bounds are not a closed interval: {bounds}")
        return lo, hi
    w = (src.get("window_et") or "").strip()
    try:
        lo_s, hi_s = w.split("-")
        lo_h, lo_m = (int(x) for x in lo_s.split(":"))
        hi_h, hi_m = (int(x) for x in hi_s.split(":"))
    except Exception as e:
        raise ValueError(f"manifest has no readable source.window_et: {w!r}") from e
    return lo_h * 60 + lo_m, hi_h * 60 + hi_m


def full_scope_day_set(scope: str) -> set[str]:
    """Every guarded dev day the scope owes, independent of any --days filter.

    B1 owes 19 days (2025-02), B2 owes 41 (2026-04/05). These counts are
    derived from the dev calendar, never hardcoded, so a calendar change cannot
    leave readiness asserting a stale day count.
    """
    months = SCOPE_MONTHS[scope]
    days = {d for d in sim.dev_days() if d[:7] in months}
    for d in sorted(days):
        sim.guard_day(d)
    return days


def shared_inputs(ctx: dict) -> dict:
    """Inputs every blocker shares, and which are compared against the CURRENT
    files at publish time (alias map, PIT archive, guard source, source policy)."""
    return {
        "alias_map_sha256": ctx["alias_map_sha"],
        "pit_parquet_sha256": ctx["pit"].sha256,
        "guard_source": "factory/scripts/basket_sim.py:guard_day",
        "source_policy_sha256": ctx["policy_sha"],
    }


def blocker_parents(ctx: dict, ref: dict) -> dict:
    """The identity a per-scope attestation carries for ITS blocker.

    code and contract come from the blocker's PINNED capture, not from the
    currently installed tool: a rule change for one blocker must not invalidate
    another blocker's already-verified days, and a historical producer is never
    re-typed as today's. Root, alias map, PIT and source policy are the run's own
    identity and are compared strictly.
    """
    producer = ref["producer"]
    return {
        "acquisition_root": str(ctx["out_data"]),
        "blocker_id": ref["blocker_id"],
        "scope": ref["scope"],
        "blocker_version": ref["version"],
        "code_sha256": producer["code_sha256_at_capture"],
        "contract_sha256": producer["contract_sha256_at_capture"],
        "producer_snapshot_path": producer["snapshot_path"],
        "producer_snapshot_sha256": producer["snapshot_sha256"],
        "contract_snapshot_path": producer["contract_snapshot_path"],
        "contract_snapshot_sha256": producer["contract_snapshot_sha256"],
        "alias_map_sha256": ctx["alias_map_sha"],
        "pit_parquet_sha256": ctx["pit"].sha256,
        "source_policy_sha256": ctx["policy_sha"],
        "guard_source": "factory/scripts/basket_sim.py:guard_day",
    }


def _same_dir(a: Any, b: Any) -> bool:
    """Two path strings naming the same directory, tolerant of a symlinked or
    bind-mounted data root.

    The data root may be reached as /home/.../data or as the mount it points at,
    and an attestation records the spelling the producer saw. Comparing raw
    strings would mark a perfectly good attestation stale purely because the same
    directory is reachable by two names; comparing the resolved paths compares
    the directory itself.
    """
    if not a or not b:
        return a == b
    try:
        return Path(str(a)).resolve() == Path(str(b)).resolve()
    except Exception:
        return str(a) == str(b)


def parents_problems(attested: dict, expected: dict) -> list[dict]:
    """Which identity fields of an attestation disagree with the pin.

    `source_policy_sha256` is compared when the attestation carries it: an
    attestation produced before the source policy existed cannot name it, and the
    union validates the policy itself, so absence is reported rather than being
    treated as tampering. Everything else must match exactly, except the root,
    which must name the same directory.
    """
    strict = (
        "code_sha256",
        "contract_sha256",
        "alias_map_sha256",
        "pit_parquet_sha256",
    )
    out = []
    if not _same_dir((attested or {}).get("acquisition_root"), expected.get("acquisition_root")):
        out.append(
            {
                "field": "acquisition_root",
                "attested": (attested or {}).get("acquisition_root"),
                "expected": expected.get("acquisition_root"),
            }
        )
    for key in strict:
        if (attested or {}).get(key) != expected.get(key):
            out.append(
                {
                    "field": key,
                    "attested": (attested or {}).get(key),
                    "expected": expected.get(key),
                }
            )
    if (attested or {}).get("source_policy_sha256") not in (None, expected["source_policy_sha256"]):
        out.append(
            {
                "field": "source_policy_sha256",
                "attested": attested.get("source_policy_sha256"),
                "expected": expected["source_policy_sha256"],
            }
        )
    return out


def verify_scope(args: argparse.Namespace, ctx: dict, out_data: Path) -> dict:
    """Verify ONE scope and return its per-scope attestation.

    This never writes the shared union: a scope run publishes only its own
    acquisition_admission_<scope>.json, so verifying B2 can never overwrite the
    B1 attestation. Every day is checked against its blocker's PINNED producer
    and contract, so verifying one blocker cannot restate another's identity.
    """
    ref = ctx["policy_info"]["refs"][BLOCKER_OF_SCOPE[args.scope]]
    expected = {
        "code_sha256": ref["producer"]["code_sha256_at_capture"],
        "contract_sha256": ref["producer"]["contract_sha256_at_capture"],
        "alias_map_sha256": ctx["alias_map_sha"],
        "source_policy_sha256": ctx["policy_sha"],
    }
    days = scope_days(args.scope, _day_filter(args), sim.dev_days())
    bid = BLOCKER_OF_SCOPE[args.scope]
    blockers = {
        bid: {
            "admitted_days": [],
            "unadmitted_days": [],
            "days": [],
            "residual_confirmed_trading_gaps": [],
            "cross_feed_disagreements": {"count": 0, "names": [], "witness_days": []},
        }
    }
    all_checks: list[dict] = []
    per_day = []
    residuals: list[dict] = []
    unadmitted: list[dict] = []
    cross_feed_all: list[dict] = []
    summary = {
        "requested_symbols": 0,
        "symbols_with_data": 0,
        "symbols_zero_bars": 0,
        "symbols_invalid": 0,
        "symbols_request_failed": 0,
    }
    for day in days:
        plan = plan_day(
            day,
            args.scope,
            ctx["pit"],
            ctx["aliases"],
            ctx["compact"],
            ctx["lanes"],
            ctx["data"],
            ctx["policy"],
        )
        checks, res = verify_day(plan, out_data, expected, (ctx["lo"], ctx["hi"]))
        all_checks.extend(checks)
        # A day is admitted only when its own verify checks ALL pass, not merely
        # because a stale manifest claims status=complete.
        day_failed = [c["check"] for c in checks if not c["ok"]]
        man = res.get("manifest") or {}
        ev = man.get("evidence_crosscheck") or {}
        sipcov = man.get("sip_coverage") or {}
        entry = {
            "day": day,
            "file": f"bars/{day}.parquet",
            "file_sha256": man.get("sha256"),
            "manifest": f"bars/{day}.manifest.json",
            "manifest_sha256": sha256_file(out_data / "bars" / f"{day}.manifest.json")
            if (out_data / "bars" / f"{day}.manifest.json").exists()
            else None,
            "roster": f"rosters/{day}.json",
            "roster_sha256": man.get("roster_sha256"),
            "status": res.get("status"),
            "verify_checks_failed": day_failed,
            "rows": man.get("rows"),
            "requested_symbols": man.get("requested_symbols"),
            "symbols_with_data": man.get("symbols_with_data"),
            "symbols_zero_bars": len(man.get("symbols_zero_bars") or []),
            "symbols_invalid": len(man.get("symbols_invalid") or []),
            "symbols_request_failed": len(man.get("symbols_request_failed") or []),
            "sip_coverage_complete": sipcov.get("complete"),
            "cross_feed_disagreement_count": ev.get("cross_feed_disagreement_count", 0),
            "cross_feed_witness": ev.get("witness_artifact"),
            "cross_feed_witness_sha256": ev.get("witness_sha256"),
            "errors": man.get("errors") or [],
        }
        per_day.append(entry)
        summary["requested_symbols"] += int(man.get("requested_symbols") or 0)
        summary["symbols_with_data"] += int(man.get("symbols_with_data") or 0)
        summary["symbols_zero_bars"] += len(man.get("symbols_zero_bars") or [])
        summary["symbols_invalid"] += len(man.get("symbols_invalid") or [])
        summary["symbols_request_failed"] += len(man.get("symbols_request_failed") or [])
        for d in ev.get("cross_feed_disagreements") or []:
            cross_feed_all.append({"day": day, **d})
            if ev.get("witness_artifact"):
                blockers[bid]["cross_feed_disagreements"]["witness_days"].append(day)
        if res.get("status") == REQUIRED_STATUS_ADMITTED and not day_failed:
            blockers[bid]["admitted_days"].append(day)
        else:
            # An unadmitted day is stated explicitly, with its reason, so the
            # blocker stays visibly open instead of silently missing a day.
            unadmitted.append(
                {
                    "day": day,
                    "status": res.get("status"),
                    "reasons": man.get("refusal_reasons")
                    or (["missing_or_incomplete"] if res.get("status") == "missing" else []),
                    "verify_checks_failed": day_failed,
                    "errors": man.get("errors") or [],
                    "rows": man.get("rows"),
                }
            )
            for c in ev.get("same_feed_contradictory_zeros") or []:
                residuals.append({"day": day, **c})
    blockers[bid]["days"] = per_day
    blockers[bid]["unadmitted_days"] = unadmitted
    blockers[bid]["residual_confirmed_trading_gaps"] = residuals
    blockers[bid]["cross_feed_disagreements"] = {
        "count": len(cross_feed_all),
        "names": sorted({d["ticker"] for d in cross_feed_all}),
        "witness_days": sorted(set(blockers[bid]["cross_feed_disagreements"]["witness_days"])),
        "diagnostic": CROSS_FEED_DIAGNOSTIC,
        "note": "reported and preserved, never resolved by demanding a SIP minute bar and "
        "never counted as a coverage failure of the SIP request",
        "items": cross_feed_all,
    }
    # The FULL scope, independent of any --days filter. A run that verified one
    # day has still left eighteen (B1) or forty (B2) days unverified, and the
    # union must be able to see that from the artifact alone.
    full_scope_days = full_scope_day_set(args.scope)
    is_full_scope = set(days) == full_scope_days
    ok = all(c["ok"] for c in all_checks) and not residuals and not unadmitted and is_full_scope
    return {
        "version": "v0",
        "schema": "atlas.acquisition.admission.v0",
        "scope": args.scope,
        "blocker_id": bid,
        "blocker_version": ref["version"],
        "acquisition_root": str(out_data),
        "generated_at": datetime.now(UTC).isoformat(),
        "parents": blocker_parents(ctx, ref),
        "shared_inputs": shared_inputs(ctx),
        "producer": ref["producer"],
        "source_policy": {
            "path": str(POLICY_PATH.relative_to(ROOT)),
            "sha256": ctx["policy_sha"],
            "policy_id": ctx["policy"].get("policy_id"),
            "schema": ctx["policy"].get("schema"),
        },
        "scope_coverage": {
            "is_full_scope": is_full_scope,
            "scope_expected_days": sorted(full_scope_days),
            "scope_expected_n": len(full_scope_days),
            "verified_days": sorted(days),
            "verified_n": len(days),
            "missing_days": sorted(full_scope_days - set(days)),
        },
        "guard": {
            "sealed_prefixes": list(sim.SEALED_PREFIXES),
            "reserved_months": list(sim.RESERVED_MONTHS),
            "note": SEED_DAY_PREFIX_REFUSAL_NOTE,
            "guarded_days": days,
            "n_guarded_days": len(days),
        },
        "blockers": blockers,
        "requested_outcome_accounting": summary,
        "all_checks_passed": ok,
        "failed_checks": [c for c in all_checks if not c["ok"]],
    }


def manifest_crosscheck(man: dict) -> tuple[list[dict], list[dict], dict]:
    """(same_feed_zeros, cross_feed, witness) read back from ONE manifest.

    Two producer generations are read, and only because the older one's files are
    immutable evidence:
      * this producer writes `evidence_crosscheck`, which attributes every
        positive bar count to the source that recorded it and that source's feed;
      * the pinned v3 producer wrote `compact_crosscheck.contradictory_zeros`,
        folding every source into one list. Its B2 sources are the Alpaca SIP
        backfill lane and the compact SIP query - both the acquisition feed - so
        every entry it recorded is a SAME-FEED zero and is treated as one. The
        old bytes are not rewritten; the reading of them is what is versioned.
    """
    ev = man.get("evidence_crosscheck")
    if isinstance(ev, dict):
        return (
            list(ev.get("same_feed_contradictory_zeros") or []),
            list(ev.get("cross_feed_disagreements") or []),
            {
                "witness_artifact": ev.get("witness_artifact"),
                "witness_sha256": ev.get("witness_sha256"),
                "witness_rows": int(ev.get("witness_rows") or 0),
                "declared_count": int(ev.get("cross_feed_disagreement_count") or 0),
            },
        )
    legacy = list((man.get("compact_crosscheck") or {}).get("contradictory_zeros") or [])
    return (
        legacy,
        [],
        {"witness_artifact": None, "witness_sha256": None, "witness_rows": 0, "declared_count": 0},
    )


def baseline_pins(ref: dict, policy_info: dict) -> dict[str, str]:
    """month -> pinned digest of the lane this blocker replaces/supplements."""
    out: dict[str, str] = {}
    for sname in ref["sources"]:
        src = policy_info["sources"][sname]
        if src["role"] in ("replaced_baseline", "supplemented_baseline"):
            out.update(src.get("sha256_pins") or {})
    return out


def rehash_blocker_days(
    scope_doc: dict, ref: dict, policy_info: dict, alias_map_sha: str
) -> tuple[int, list[dict], dict[str, dict]]:
    """Re-hash and re-check every day an attestation names, right now.

    The per-scope file is a claim made earlier; the union repeats the arithmetic
    from the bytes on disk, adds the checks a later producer can apply to any
    generation's manifests (pinned producer, pinned baseline lane, complete SIP
    coverage, no same-feed zero, preserved witness), and returns one record per
    day so a consumer can resolve day -> root without guessing.
    """
    root = ref["root"]
    mismatches: list[dict] = []
    checked = 0
    days_out: dict[str, dict] = {}
    pins = baseline_pins(ref, policy_info)
    entries = (scope_doc.get("blockers") or {}).get(scope_doc.get("blocker_id"), {}).get("days", [])
    for entry in entries:
        day = entry.get("day")
        rec = {
            "day": day,
            "blocker_id": ref["blocker_id"],
            "scope": ref["scope"],
            "version": ref["version"],
            "acquisition_root": str(root),
            "payload": entry.get("file") or f"bars/{day}.parquet",
            "payload_sha256": entry.get("file_sha256"),
            "manifest": entry.get("manifest") or f"bars/{day}.manifest.json",
            "admitted": False,
            "cross_feed_disagreement_count": 0,
            "cross_feed_witness": None,
            "cross_feed_witness_sha256": None,
            "sip_coverage_complete": None,
        }
        days_out[day] = rec
        payload = root / rec["payload"]
        manifest = root / rec["manifest"]
        if not payload.exists() or not manifest.exists():
            mismatches.append({"day": day, "check": "payload_or_manifest_missing"})
            continue
        actual = sha256_file(payload)
        checked += 1
        if actual != entry.get("file_sha256"):
            mismatches.append(
                {
                    "day": day,
                    "check": "payload_sha256_mismatch",
                    "attested": entry.get("file_sha256"),
                    "actual": actual,
                }
            )
        man = load_json(manifest)
        rec["manifest_sha256"] = sha256_file(manifest)
        rec["payload_sha256"] = actual
        if man.get("status") != REQUIRED_STATUS_ADMITTED:
            mismatches.append(
                {"day": day, "check": "status_not_complete", "status": man.get("status")}
            )
        if man.get("errors"):
            mismatches.append({"day": day, "check": "manifest_errors", "errors": man.get("errors")})
        if man.get("symbols_request_failed"):
            mismatches.append(
                {
                    "day": day,
                    "check": "request_failed_symbols",
                    "symbols": man.get("symbols_request_failed"),
                }
            )
        if man.get("code_sha256") != ref["producer"]["code_sha256_at_capture"]:
            mismatches.append(
                {
                    "day": day,
                    "check": "producer_not_pinned",
                    "manifest": man.get("code_sha256"),
                    "pinned": ref["producer"]["code_sha256_at_capture"],
                }
            )
        if man.get("contract_sha256") != ref["producer"]["contract_sha256_at_capture"]:
            mismatches.append(
                {
                    "day": day,
                    "check": "contract_not_pinned",
                    "manifest": man.get("contract_sha256"),
                    "pinned": ref["producer"]["contract_sha256_at_capture"],
                }
            )
        if (man.get("input_sha256") or {}).get("alias_map") not in (None, alias_map_sha):
            mismatches.append({"day": day, "check": "alias_map_not_current"})
        pin = pins.get(str(day)[:7])
        lane_sha = (man.get("input_sha256") or {}).get("baseline_lane")
        if pin is not None and lane_sha != pin:
            mismatches.append(
                {
                    "day": day,
                    "check": "baseline_lane_not_pinned",
                    "manifest": lane_sha,
                    "pinned": pin,
                }
            )
        if "sip_coverage" in man:
            rec["sip_coverage_complete"] = (man.get("sip_coverage") or {}).get("complete")
            if rec["sip_coverage_complete"] is not True:
                mismatches.append({"day": day, "check": "sip_coverage_incomplete"})
        same_feed, cross_feed, witness = manifest_crosscheck(man)
        if same_feed:
            mismatches.append(
                {
                    "day": day,
                    "check": "same_feed_contradictory_zeros",
                    "items": same_feed[:10],
                }
            )
        rec["cross_feed_disagreement_count"] = len(cross_feed) or witness["declared_count"]
        rec["cross_feed_names"] = sorted({str(i.get("ticker")) for i in cross_feed})
        if cross_feed:
            rel = witness["witness_artifact"]
            if not rel or not str(rel).startswith(f"{WITNESS_DIRNAME}/"):
                mismatches.append({"day": day, "check": "cross_feed_witness_not_declared"})
            else:
                wp = root / rel
                rec["cross_feed_witness"] = rel
                rec["cross_feed_witness_sha256"] = witness["witness_sha256"]
                if not wp.exists():
                    mismatches.append({"day": day, "check": "cross_feed_witness_missing"})
                elif sha256_file(wp) != witness["witness_sha256"]:
                    mismatches.append({"day": day, "check": "cross_feed_witness_sha256_mismatch"})
                else:
                    wdf = pl.read_parquet(wp)
                    if wdf.height != witness["witness_rows"] or wdf.height == 0:
                        mismatches.append(
                            {
                                "day": day,
                                "check": "cross_feed_witness_row_count",
                                "rows": wdf.height,
                                "declared": witness["witness_rows"],
                            }
                        )
                    if set(wdf["source_feed"].unique().to_list()) & {ACQUISITION_FEED}:
                        mismatches.append(
                            {"day": day, "check": "cross_feed_witness_claims_acquisition_feed"}
                        )
        roster = root / (entry.get("roster") or f"rosters/{day}.json")
        if roster.exists():
            rec["roster_sha256"] = sha256_file(roster)
    return checked, mismatches, days_out


def publish_admission_union(out_ev: Path, ctx: dict, refs: dict[str, dict]) -> dict:
    """Rebuild the joint union from explicit per-blocker references.

    Each blocker contributes only when its own attestation exists, matches the
    identity its policy pins (its own root, its own captured producer and
    contract, the current alias map/PIT/policy), and passes its own
    all_checks_passed. Every contributed day is re-hashed here. A blocker that
    was never verified is reported missing and keeps its entry open - the union
    never invents a pass for evidence it does not have, and never lets one
    blocker's version stand in for another's.
    """
    scopes: dict[str, dict] = {}
    missing: list[str] = []
    stale: list[dict] = []
    day_roots: dict[str, dict] = {}
    blockers: dict[str, dict] = {}
    for bid, ref in refs.items():
        scope = ref["scope"]
        root = ref["root"]
        att = Path(ref["attestation"])
        expected_days = full_scope_day_set(scope)
        expected_parents = blocker_parents({**ctx, "out_data": root}, ref)
        common = {
            "blocker_id": bid,
            "scope": scope,
            "version": ref["version"],
            "acquisition_root": str(root),
            "root_rel": ref["root_rel"],
            "producer": ref["producer"],
            "attestation": {
                "path": str(att),
                "sha256": sha256_file(att) if att.exists() else None,
            },
            "required_days": sorted(expected_days),
            "required_day_count": len(expected_days),
        }
        if not att.exists():
            missing.append(scope)
            scopes[scope] = {
                **common,
                "state": "unverified",
                "reason": "no per-scope attestation has been produced for this scope",
                "is_full_scope": False,
                "admitted_days": [],
                "verified_subset_days": [],
                "missing_required_days": sorted(expected_days),
                "unadmitted_days": [],
                "residual_confirmed_trading_gaps": [],
                "cross_feed_disagreements": {"count": 0, "names": [], "witness_days": []},
                "rehash": {"days_rehashed": 0, "mismatches": []},
            }
            continue
        doc = load_json(att)
        problems = parents_problems(doc.get("parents") or {}, expected_parents)
        if problems:
            stale.append(
                {
                    "scope": scope,
                    "blocker_id": bid,
                    "file": att.name,
                    "reason": "parents_do_not_match_this_run",
                    "problems": problems,
                    "attested_parents": doc.get("parents"),
                }
            )
            scopes[scope] = {
                **common,
                "state": "stale",
                "reason": "attestation was produced by a different root/code/contract/PIT/"
                "alias-map/source-policy identity than this blocker's pin",
                "is_full_scope": False,
                "admitted_days": [],
                "verified_subset_days": [],
                "missing_required_days": sorted(expected_days),
                "unadmitted_days": [],
                "residual_confirmed_trading_gaps": [],
                "cross_feed_disagreements": {"count": 0, "names": [], "witness_days": []},
                "rehash": {"days_rehashed": 0, "mismatches": []},
                "parents_problems": problems,
            }
            continue
        block = (doc.get("blockers") or {}).get(bid) or {}
        rechecked, mismatches, droots = rehash_blocker_days(
            doc, ref, ctx["policy_info"], ctx["alias_map_sha"]
        )
        # A blocker is ready ONLY when it has discharged its WHOLE obligation: the
        # admitted set must equal every guarded dev day the scope owes (19 for
        # B1, 41 for B2). A one-day --days subset is real evidence about that
        # day and nothing more, so it is reported as verified_subset with an
        # empty admitted_days and can never read as full coverage.
        admitted_set = set(block.get("admitted_days") or [])
        bad_days = {m.get("day") for m in mismatches}
        clean = admitted_set - bad_days
        missing_days = sorted(expected_days - clean)
        is_full = not missing_days and clean <= expected_days
        scope_ok = bool(doc.get("all_checks_passed")) and not mismatches
        if not scope_ok:
            state = "not_ready"
            reason = (
                "scope verification did not pass, or a day no longer hashes or no longer "
                "satisfies the pinned-producer checks"
            )
        elif not is_full:
            state = "verified_subset"
            reason = (
                f"only {len(clean)} of {len(expected_days)} required days were verified; "
                f"{len(missing_days)} day(s) still unverified"
            )
        else:
            state = "ready"
            reason = None
        ready = state == "ready"
        witness_days = sorted({d for d, r in droots.items() if r.get("cross_feed_witness")})
        reported_root = (doc.get("parents") or {}).get("acquisition_root")
        for day, rec in droots.items():
            rec["admitted"] = bool(ready and day in clean)
            rec["acquisition_root_reported"] = reported_root
            day_roots[day] = rec
        cross_names = sorted(
            {n for r in droots.values() for n in (r.get("cross_feed_names") or [])}
        )
        scopes[scope] = {
            **common,
            "state": state,
            "reason": reason,
            "acquisition_root_reported": reported_root,
            "is_full_scope": is_full,
            "admitted_days": sorted(clean) if ready else [],
            "verified_subset_days": [] if ready else sorted(clean),
            "missing_required_days": missing_days,
            "unadmitted_days": list(block.get("unadmitted_days") or []),
            "residual_confirmed_trading_gaps": list(
                block.get("residual_confirmed_trading_gaps") or []
            ),
            "cross_feed_disagreements": {
                "count": sum(r.get("cross_feed_disagreement_count") or 0 for r in droots.values()),
                "names": cross_names,
                "witness_days": witness_days,
            },
            "source": att.name,
            "source_sha256": sha256_file(att),
            "verified_at": doc.get("generated_at"),
            "requested_outcome_accounting": doc.get("requested_outcome_accounting") or {},
            "failed_checks": doc.get("failed_checks") or [],
            "rehash": {"days_rehashed": rechecked, "mismatches": mismatches},
            "policy_pinned_in_attestation": bool(
                (doc.get("parents") or {}).get("source_policy_sha256")
            ),
        }
        blockers[bid] = {
            "scope": scope,
            "version": ref["version"],
            "state": state,
            "is_full_scope": is_full,
            "acquisition_root": str(root),
            "acquisition_root_reported": reported_root,
            "root_rel": ref["root_rel"],
            "producer": ref["producer"],
            "attestation": {"path": str(att), "sha256": sha256_file(att)},
            "required_day_count": len(expected_days),
            "admitted_day_count": len(clean) if ready else 0,
            "missing_required_day_count": len(missing_days),
            "admitted_days": sorted(clean) if ready else [],
            "verified_subset_days": [] if ready else sorted(clean),
            "unadmitted_days": list(block.get("unadmitted_days") or []),
            "residual_confirmed_trading_gaps": list(
                block.get("residual_confirmed_trading_gaps") or []
            ),
            "cross_feed_disagreements": scopes[scope]["cross_feed_disagreements"],
            "rehash": {"days_rehashed": rechecked, "mismatches": mismatches},
        }
    for bid, ref in refs.items():
        if ref["scope"] in missing:
            blockers[bid] = {
                "scope": ref["scope"],
                "version": ref["version"],
                "state": "unverified",
                "is_full_scope": False,
                "acquisition_root": str(ref["root"]),
                "root_rel": ref["root_rel"],
                "producer": ref["producer"],
                "attestation": {"path": str(ref["attestation"]), "sha256": None},
                "required_day_count": len(full_scope_day_set(ref["scope"])),
                "admitted_day_count": 0,
                "missing_required_day_count": len(full_scope_day_set(ref["scope"])),
                "admitted_days": [],
                "verified_subset_days": [],
                "unadmitted_days": [],
                "residual_confirmed_trading_gaps": [],
                "cross_feed_disagreements": {"count": 0, "names": [], "witness_days": []},
                "rehash": {"days_rehashed": 0, "mismatches": []},
            }
    all_ready = not missing and not stale and all(e["state"] == "ready" for e in scopes.values())
    union = {
        "version": "v0",
        "schema": "atlas.acquisition.admission-union.v0",
        "generated_at": datetime.now(UTC).isoformat(),
        "joint_evidence_root": str(out_ev),
        "per_blocker_roots": {bid: str(ref["root"]) for bid, ref in refs.items()},
        "source_policy": {
            "path": str(POLICY_PATH.relative_to(ROOT)),
            "sha256": ctx["policy_sha"],
            "policy_id": (ctx["policy"] or {}).get("policy_id"),
        },
        "parents": {
            "shared_inputs": shared_inputs(ctx),
            "per_blocker": {
                bid: {
                    "blocker_id": bid,
                    "scope": ref["scope"],
                    "version": ref["version"],
                    "acquisition_root": str(ref["root"]),
                    "code_sha256": ref["producer"]["code_sha256_at_capture"],
                    "contract_sha256": ref["producer"]["contract_sha256_at_capture"],
                    "producer_snapshot_path": ref["producer"]["snapshot_path"],
                    "producer_snapshot_sha256": ref["producer"]["snapshot_sha256"],
                    "contract_snapshot_path": ref["producer"]["contract_snapshot_path"],
                    "contract_snapshot_sha256": ref["producer"]["contract_snapshot_sha256"],
                }
                for bid, ref in refs.items()
            },
            "note": "each blocker is pinned to the producer and contract it was captured "
            "under; the current tool hash is deliberately NOT required to equal a historical "
            "producer",
        },
        "readiness_rule": "A scope is ready only when its admitted day set equals every "
        "guarded dev day the scope owes (B1: 19 days of 2025-02, B2: 41 days of 2026-04/05), "
        "each re-hashed, with no failed day check, no same-feed contradictory zero and no "
        "residual gap. A --days subset is reported as verified_subset and admits nothing.",
        "witness_rule": "cross_feed_witnesses/<day>.parquet holds the ORIGINAL rows of a "
        "source whose feed is not the acquisition feed, keyed by (parent sha256, row "
        "ordinal). They are reported and preserved, never relabelled as SIP, never a SIP "
        "rank input, and never a reason to demand a SIP minute bar.",
        "guard": {
            "sealed_prefixes": list(sim.SEALED_PREFIXES),
            "reserved_months": list(sim.RESERVED_MONTHS),
            "note": SEED_DAY_PREFIX_REFUSAL_NOTE,
        },
        "scopes": scopes,
        "blockers": blockers,
        "day_roots": day_roots,
        "missing_scopes": missing,
        "stale_scopes": stale,
        "all_scopes_ready": all_ready,
        "cross_feed_disagreements": {
            "count": sum(
                (e.get("cross_feed_disagreements") or {}).get("count", 0) or 0
                for e in scopes.values()
            ),
            "names": sorted(
                {
                    n
                    for e in scopes.values()
                    for n in (e.get("cross_feed_disagreements") or {}).get("names", [])
                }
            ),
            "diagnostic": CROSS_FEED_DIAGNOSTIC,
            "is_a_sip_coverage_failure": False,
        },
        "requested_outcome_accounting": {
            k: sum(
                (e.get("requested_outcome_accounting") or {}).get(k, 0) or 0
                for e in scopes.values()
            )
            for k in (
                "requested_symbols",
                "symbols_with_data",
                "symbols_zero_bars",
                "symbols_invalid",
                "symbols_request_failed",
            )
        },
    }
    atomic_write_json(out_ev / "acquisition_admission.json", union)
    return union


def resolve_blocker_refs(ctx: dict, overrides: dict[str, Path]) -> dict[str, dict]:
    """The policy's per-blocker references, with explicit CLI overrides applied.

    An override may only move the ATTESTATION path (a re-run that wrote its
    evidence elsewhere); the root and the pinned producer/contract stay the
    policy's, because those are what the joint evidence claims.
    """
    refs = {bid: dict(ref) for bid, ref in ctx["policy_info"]["refs"].items()}
    for bid, att in overrides.items():
        if bid not in refs:
            raise SystemExit(f"--blocker-ref names unknown blocker {bid!r}")
        if not att.exists():
            raise SystemExit(f"--blocker-ref {bid}: attestation {att} does not exist")
        refs[bid]["attestation"] = att
    return refs


def stage_publish(args: argparse.Namespace) -> dict:
    """Publish the joint admission from explicit per-blocker references.

    Offline and read-only with respect to market data: it reads each blocker's
    attestation and the bytes under its declared root, re-hashes them, and writes
    ONE union. It never writes blockers_resolved.json and never refetches a day.
    """
    contract = load_contract()
    policy = load_policy()
    data = resolve_data_root()
    ctx = {
        "data": data,
        "out_data": None,
        "policy": policy,
        "policy_info": validate_policy(policy, data),
        "aliases": load_alias_map(),
        "alias_map_sha": sha256_file(ALIAS_MAP_PATH),
        "policy_sha": sha256_file(POLICY_PATH),
        "contract_sha": sha256_file(CONTRACT_PATH),
        "code_sha": sha256_file(HERE),
        "pit": PitIndex(data / "pit/pit_symbols.parquet"),
        "lo": int(contract["fetch"]["window_et"][0]),
        "hi": int(contract["fetch"]["window_et"][1]),
    }
    refs = resolve_blocker_refs(ctx, _blocker_ref_overrides(args))
    out_ev = Path(args.out_evidence) if args.out_evidence else None
    if out_ev is None:
        raise SystemExit(
            "--stage publish requires an explicit --out-evidence directory: the joint evidence "
            "is written there, and choosing it implicitly would decide which blocker's root the "
            "union appears to belong to"
        )
    if not out_ev.is_absolute():
        out_ev = ROOT / out_ev
    return publish_admission_union(out_ev, ctx, refs)


def stage_verify(args: argparse.Namespace) -> dict:
    """Verify the requested scope, then republish the joint union.

    The per-scope file is the durable record; the union is always REBUILT from
    the per-blocker attestations on disk, never appended to, so a B2 run can
    never drop or overwrite the B1 attestation.

    The result carries BOTH outcomes, because they are different questions:
      days_verified_ok - every day THIS run was asked about passed its checks
      union             - whether any scope has discharged its full obligation
    A one-day --days subset exits 0 on the first and reports false on the second.
    """
    contract = load_contract()
    policy = load_policy()
    out_data = Path(args.out_data)
    ctx = build_context(out_data, contract, policy)
    assert_declared_root(ctx, args.scope, out_data)
    out_ev = _out_evidence(args, out_data)
    scope_doc = verify_scope(args, ctx, out_data)
    atomic_write_json(out_ev / f"acquisition_admission_{args.scope}.json", scope_doc)
    path = out_ev / f"acquisition_admission_{args.scope}.json"
    refs = resolve_blocker_refs(
        ctx, {**_blocker_ref_overrides(args), BLOCKER_OF_SCOPE[args.scope]: path}
    )
    union = publish_admission_union(out_ev, ctx, refs)
    coverage = scope_doc.get("scope_coverage") or {}
    block = (scope_doc.get("blockers") or {}).get(scope_doc["blocker_id"]) or {}
    requested_days = list(coverage.get("verified_days") or [])
    days_ok = all(
        not e.get("verify_checks_failed") and e.get("status") == REQUIRED_STATUS_ADMITTED
        for e in block.get("days", [])
    ) and not block.get("residual_confirmed_trading_gaps")
    union["days_verified_ok"] = days_ok
    union["verified_scope"] = args.scope
    union["verified_days"] = requested_days
    union["is_subset_run"] = not coverage.get("is_full_scope", True)
    return union


def _blocker_ref_overrides(args: argparse.Namespace) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for raw in getattr(args, "blocker_ref", None) or []:
        if "=" not in raw:
            raise SystemExit(f"--blocker-ref expects BLOCKER_ID=PATH, got {raw!r}")
        bid, _, p = raw.partition("=")
        out[bid.strip()] = Path(p.strip())
    return out


# --------------------------------------------------------------------------- #
# self-test (synthetic and isolated: no network, no shared data root)
# --------------------------------------------------------------------------- #
class FakeFetcher:
    """Scripted page source. pages: list of dicts symbol -> list[RawBar]."""

    def __init__(
        self, pages: list[dict[str, list[RawBar]]], truncated: set[str] | None = None
    ) -> None:
        self.pages = pages
        self.calls: list[list[str]] = []
        self.asof: list[str | None] = []
        self.truncated = truncated or set()

    def fetch(
        self, symbols: list[str], start: datetime, end: datetime, asof: str | None = None
    ) -> dict[str, list[RawBar]]:
        self.calls.append(list(symbols))
        self.asof.append(asof)
        merged: dict[str, list[RawBar]] = {s: [] for s in symbols}
        for page in self.pages:
            for s, blist in page.items():
                if s in merged:
                    merged[s].extend(blist)
        # A truncated stream silently loses pages: the symbol looks zero-bar.
        for s in self.truncated:
            merged[s] = []
        return merged


class FakeResponse:
    def __init__(self, status_code: int, payload: dict, headers: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


class FakeSession:
    """Returns a scripted response sequence; records the page tokens it saw."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.tokens: list[str | None] = []

    def get(self, url, params=None, headers=None, timeout=None):  # noqa: D102
        self.tokens.append((params or {}).get("page_token"))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _et_ts(day: str, minute: int) -> datetime:
    h, m = divmod(minute, 60)
    return datetime.fromisoformat(f"{day}T{h:02d}:{m:02d}:00").replace(tzinfo=ET).astimezone(UTC)


def _lane_parquet(path: Path, rows: list[tuple[str, int, str, float, float]]) -> str:
    """rows: (day, et_minute, ticker, close, volume). Returns the file sha256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "timestamp": pl.Series(
                [_et_ts(d, m) for d, m, _, _, _ in rows], dtype=pl.Datetime("ns", "UTC")
            ),
            "ticker": pl.Series([t for _, _, t, _, _ in rows], dtype=pl.String),
            "open": pl.Series([c for _, _, _, c, _ in rows], dtype=pl.Float64),
            "high": pl.Series([c for _, _, _, c, _ in rows], dtype=pl.Float64),
            "low": pl.Series([c for _, _, _, c, _ in rows], dtype=pl.Float64),
            "close": pl.Series([c for _, _, _, c, _ in rows], dtype=pl.Float64),
            "volume": pl.Series([v for _, _, _, _, v in rows], dtype=pl.Float64),
        }
    ).write_parquet(path)
    return sha256_file(path)


def _bar(day: str, et: int, px: float, vol: float = 100.0) -> RawBar:
    """A minute bar stamped at the given America/New_York minute (left edge)."""
    d = date.fromisoformat(day)
    ts = datetime(d.year, d.month, d.day, et // 60, et % 60, tzinfo=ET).astimezone(UTC)
    return RawBar(ts=ts, open=px, high=px + 0.5, low=px - 0.5, close=px + 0.1, volume=vol)


def selftest() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    day = "2025-02-03"
    lo, hi = PROJECTION_LO, PROJECTION_HI

    # 1. payload schema, window, sort
    df = bars_to_frame(
        {"AAA": [_bar(day, 570, 10.0), _bar(day, 600, 11.0)], "BBB": [_bar(day, 571, 5.0)]},
        {"AAA": "AAA", "BBB": "BBB"},
    )
    check("payload_schema", list(df.columns) == list(BAR_SCHEMA), str(list(df.columns)))
    check(
        "payload_dtypes",
        str(df.schema["timestamp"]) == "Datetime(time_unit='ns', time_zone='UTC')"
        and str(df.schema["ticker"]) == "String",
    )
    v = validate_frame(df, lo, hi)
    check(
        "payload_clean",
        v["duplicate_ticker_timestamp"] == 0
        and v["out_of_window_rows"] == 0
        and v["nonfinite_rows"] == 0
        and v["nonpositive_or_inconsistent_rows"] == 0,
        str(v),
    )

    # 2. duplicate (ticker, timestamp) is an error, not a silent drop
    dup = bars_to_frame({"AAA": [_bar(day, 570, 10.0), _bar(day, 570, 10.0)]}, {"AAA": "AAA"})
    check("duplicate_detected", validate_frame(dup, lo, hi)["duplicate_ticker_timestamp"] == 1)

    # 3. out-of-window row is caught
    oow = bars_to_frame({"AAA": [_bar(day, 200, 10.0)]}, {"AAA": "AAA"})
    check("out_of_window_detected", validate_frame(oow, lo, hi)["out_of_window_rows"] == 1)

    # 4. paginated input accumulates every page
    fake = FakeFetcher([{"AAA": [_bar(day, 570, 10.0)]}, {"AAA": [_bar(day, 571, 10.5)]}])
    bars, invalid, failed, errors = fetch_bars(fake, day, ["AAA"], lo, hi)
    check(
        "paginated_pages_joined", len(bars["AAA"]) == 2 and not errors, f"{len(bars['AAA'])} bars"
    )

    # 5. truncated stream yields a recorded zero, never a fabricated bar
    trunc = FakeFetcher(
        [{"AAA": [_bar(day, 570, 10.0)], "BBB": [_bar(day, 570, 3.0)]}], truncated={"BBB"}
    )
    bars, invalid, failed, errors = fetch_bars(trunc, day, ["AAA", "BBB"], lo, hi)
    check(
        "truncated_symbol_is_zero_not_fabricated",
        len(bars["AAA"]) == 1 and bars["BBB"] == [] and not errors,
        str({k: len(x) for k, x in bars.items()}),
    )

    # 6. invalid symbol is recorded and the batch continues
    class InvalidOnce:
        def __init__(self):
            self.n = 0

        def fetch(self, symbols, start, end, asof=None):
            self.n += 1
            if self.n == 1:
                raise ProviderError("invalid symbol: ZZZ")
            return {s: [_bar(day, 570, 2.0)] for s in symbols}

    bars, invalid, failed, errors = fetch_bars(InvalidOnce(), day, ["ZZZ", "AAA"], lo, hi)
    check(
        "invalid_symbol_recorded",
        invalid == ["ZZZ"] and "AAA" in bars and not errors and not failed,
        f"{invalid} {failed} {errors}",
    )

    # 6b. every request carries an explicit historical asof pinned to the day.
    # Without it the provider applies default entity linking, which can attribute
    # a renamed entity's tape to the queried PIT name.
    asof_fake = FakeFetcher([{"AAA": [_bar(day, 570, 3.0)]}])
    fetch_bars(asof_fake, day, ["AAA"], lo, hi)
    check(
        "fetch_passes_explicit_asof",
        asof_fake.asof and all(a == day for a in asof_fake.asof),
        str(asof_fake.asof),
    )
    rest = RestFetcher.__new__(RestFetcher)
    rest.session, rest.attempts, rest.base = FakeSession([]), 1, "https://example"
    rest.headers = {"APCA-API-KEY-ID": "k", "APCA-API-SECRET-KEY": "s"}
    rest.last_params = None
    with contextlib.suppress(ProviderError):
        rest.fetch(["AAA"], window_utc(day, lo, hi)[0], window_utc(day, lo, hi)[1], day)
    check(
        "rest_request_carries_asof",
        isinstance(getattr(rest, "last_params", None), dict)
        and rest.last_params.get("asof") == day
        and rest.last_params.get("feed") == "sip"
        and rest.last_params.get("adjustment") == "raw",
        str(getattr(rest, "last_params", None)),
    )

    # 7. REST paging follows next_page_token; a repeated token is an error
    p1 = FakeResponse(
        200,
        {
            "bars": {
                "AAA": [
                    {
                        "t": "2025-02-03T14:30:00Z",
                        "o": "1",
                        "h": "2",
                        "l": "0.5",
                        "c": "1.5",
                        "v": "10",
                    }
                ]
            },
            "next_page_token": "tok1",
        },
    )
    p2 = FakeResponse(
        200,
        {
            "bars": {
                "AAA": [
                    {
                        "t": "2025-02-03T14:31:00Z",
                        "o": "1",
                        "h": "2",
                        "l": "0.5",
                        "c": "1.5",
                        "v": "10",
                    }
                ]
            }
        },
    )
    sess = FakeSession([p1, p2])
    fetcher = RestFetcher.__new__(RestFetcher)
    fetcher.session, fetcher.attempts, fetcher.base = sess, 3, "https://example"
    fetcher.headers = {"APCA-API-KEY-ID": "k", "APCA-API-SECRET-KEY": "s"}
    out = fetcher.fetch(["AAA"], window_utc(day, lo, hi)[0], window_utc(day, lo, hi)[1])
    check(
        "rest_pages_followed",
        len(out["AAA"]) == 2 and sess.tokens == [None, "tok1"],
        str(sess.tokens),
    )

    loop = FakeSession([p1, p1])
    fetcher.session = loop
    try:
        fetcher.fetch(["AAA"], window_utc(day, lo, hi)[0], window_utc(day, lo, hi)[1])
        loop_ok = False
    except ProviderError:
        loop_ok = True
    check("rest_token_loop_refused", loop_ok)

    # 8. 429 is retried with bounded backoff, then surfaces
    class ThrottledSession:
        def __init__(self, n429):
            self.n429 = n429
            self.calls = 0

        def get(self, url, params=None, headers=None, timeout=None):
            self.calls += 1
            if self.calls <= self.n429:
                return FakeResponse(429, {}, {"Retry-After": "0"})
            return FakeResponse(200, {"bars": {"AAA": []}})

    th = ThrottledSession(2)
    fetcher.session = th
    res = fetcher.fetch(["AAA"], window_utc(day, lo, hi)[0], window_utc(day, lo, hi)[1])
    check("rest_429_retried", th.calls == 3 and res == {"AAA": []}, f"calls={th.calls}")

    th2 = ThrottledSession(99)
    fetcher.session, fetcher.attempts = th2, 2
    try:
        fetcher.fetch(["AAA"], window_utc(day, lo, hi)[0], window_utc(day, lo, hi)[1])
        exhausted_ok = False
    except ProviderError:
        exhausted_ok = True
    check("rest_429_bounded", exhausted_ok and th2.calls == 2, f"calls={th2.calls}")

    # 9. sealed / reserved day refusal happens before any path is opened
    refused = []
    for bad in ("2025-01-31", "2024-12-31", "2026-06-01", "2025-02-3"):
        try:
            sim.guard_day(bad)
            refused.append(bad)
        except (PermissionError, ValueError):
            pass
    check("guard_refuses_sealed_reserved", not refused, str(refused))
    try:
        scope_days("feb2025", ["2025-01-30"], sim.dev_days())
        scope_ok = False
    except (PermissionError, ValueError):
        scope_ok = True
    check("scope_refuses_day_outside_scope", scope_ok)

    # 10. same provider spelling under two canonical names is an error
    try:
        bars_to_frame({"BRK.A": [_bar(day, 570, 1.0)]}, {"BRK/A": "BRK.A", "BRK/B": "BRK.A"})
        collide_ok = False
    except ValueError:
        collide_ok = True
    check("alias_collision_refused", collide_ok)
    try:
        bars_to_frame({"X": [_bar(day, 570, 1.0)]}, {"A": "X", "B": "X"})
        collide2 = False
    except ValueError:
        collide2 = True
    check("duplicate_provider_symbol_refused", collide2)

    # 11. alias map validation: non-injective maps are refused
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        good = {
            "aliases": {
                "BRK/A": {
                    "provider_symbol": "BRK.A",
                    "basis": "class_share_separator",
                    "evidence": ["data/backfill/ohlcv_2026-04.parquet#ticker"],
                }
            }
        }
        atomic_write_json(tmp / "am.json", good)
        check("alias_map_loads", load_alias_map(tmp / "am.json")["BRK/A"].provider == "BRK.A")
        dup = {
            "aliases": {
                "BRK/A": {"provider_symbol": "BRK.A", "evidence": ["x"]},
                "BRK/B": {"provider_symbol": "BRK.A", "evidence": ["x"]},
            }
        }
        atomic_write_json(tmp / "dup.json", dup)
        try:
            load_alias_map(tmp / "dup.json")
            dup_ok = False
        except ValueError:
            dup_ok = True
        check("alias_map_noninjective_refused", dup_ok)
        noev = {"aliases": {"BRK/A": {"provider_symbol": "BRK.A"}}}
        atomic_write_json(tmp / "noev.json", noev)
        try:
            load_alias_map(tmp / "noev.json")
            ev_ok = False
        except ValueError:
            ev_ok = True
        check("alias_map_requires_evidence", ev_ok)

    # 12. sha resume + drift refusal, end to end on an isolated root
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        plan = DayPlan(
            day=day,
            scope="feb2025",
            vintage="2025-02-01",
            pit_n=2,
            requested=["AAA", "BBB"],
            provider_of={"AAA": "AAA", "BBB": "BBB"},
            aliases_used={},
            expected_present=["AAA"],
            observed_bars={"AAA": 5},
            raw_existing=[],
            alias_existing=[],
            unavailable={},
            baseline={"path": "", "sha256": "b"},
            compact={"path": "", "sha256": None},
        )
        fet = FakeFetcher(
            [{"AAA": [_bar(day, 570, 10.0), _bar(day, 571, 10.2)], "BBB": [_bar(day, 570, 2.0)]}]
        )
        m1 = acquire_day(
            plan, out, fet, "code1", "cfg1", "con1", "am1", "pit1", lo, hi, verbose=False
        )
        check(
            "acquire_status_complete",
            m1["status"] == "complete" and m1["rows"] == 3,
            json.dumps(m1["status"]),
        )
        m2 = acquire_day(
            plan, out, fet, "code1", "cfg1", "con1", "am1", "pit1", lo, hi, verbose=False
        )
        check("resume_skips_admitted", m2["action"] == "skipped_existing_admitted")
        # A different run configuration is drift even when the bytes are intact:
        # that day was acquired under a different window/batch/client/scope.
        try:
            acquire_day(
                plan,
                out,
                fet,
                "code1",
                "cfg-DIFFERENT",
                "con1",
                "am1",
                "pit1",
                lo,
                hi,
                verbose=False,
            )
            cfg_drift_ok = False
        except SystemExit as e:
            cfg_drift_ok = "config_sha256 drift" in str(e)
        check("config_drift_on_admitted_day_stops_run", cfg_drift_ok)
        try:
            acquire_day(
                plan,
                out,
                fet,
                "code1",
                "cfg1",
                "con-DIFFERENT",
                "am1",
                "pit1",
                lo,
                hi,
                verbose=False,
            )
            con_drift_ok = False
        except SystemExit as e:
            con_drift_ok = "contract_sha256 drift" in str(e)
        check("contract_drift_on_admitted_day_stops_run", con_drift_ok)
        # tamper with the admitted bytes
        p = out / "bars" / f"{day}.parquet"
        p.write_bytes(p.read_bytes() + b"tamper")
        try:
            acquire_day(
                plan, out, fet, "code1", "cfg1", "con1", "am1", "pit1", lo, hi, verbose=False
            )
            tamper_ok = False
        except SystemExit as e:
            tamper_ok = "NEW version" in str(e) or "new version" in str(e)
        check("admitted_bytes_never_overwritten", tamper_ok)
        # torn write (payload without manifest) is refused too
        out2 = Path(td) / "torn"
        (out2 / "bars").mkdir(parents=True)
        (out2 / "bars" / f"{day}.parquet").write_bytes(b"x")
        try:
            acquire_day(
                plan, out2, fet, "code1", "cfg1", "con1", "am1", "pit1", lo, hi, verbose=False
            )
            torn_ok = False
        except SystemExit as e:
            torn_ok = "no manifest" in str(e)
        check("torn_write_refused", torn_ok)
        # code drift on a non-admitted day stops the run
        out3 = Path(td) / "drift"
        m4 = acquire_day(
            plan,
            out3,
            FakeFetcher([{}]),
            "code1",
            "cfg1",
            "con1",
            "am1",
            "pit1",
            lo,
            hi,
            verbose=False,
        )
        check(
            "empty_payload_incomplete",
            m4["status"] == "incomplete" and "empty_payload" in m4["refusal_reasons"],
            json.dumps(m4["refusal_reasons"]),
        )
        try:
            acquire_day(
                plan,
                out3,
                FakeFetcher([{}]),
                "code2",
                "cfg1",
                "con1",
                "am1",
                "pit1",
                lo,
                hi,
                verbose=False,
            )
            drift_ok = False
        except SystemExit as e:
            drift_ok = "code_sha256 drift" in str(e)
        check("code_drift_stops_run", drift_ok)

    # 13. A name the SAME-FEED source observed with positive bars that comes back
    # with zero fresh bars is an unresolved contradiction: recorded, blocking, and
    # never a clean provider zero. Same-feed is decided by the source's declared
    # feed, not by the source's name.
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        compact_src = Path(td) / "compact"
        compact_src.mkdir()
        pl.DataFrame({"symbol": ["AAA", "BBB"], "n_bars": [390, 385]}).write_parquet(
            compact_src / f"{day}.parquet"
        )
        plan = DayPlan(
            day=day,
            scope="feb2025",
            vintage="2025-02-01",
            pit_n=2,
            requested=["AAA", "BBB"],
            provider_of={"AAA": "AAA", "BBB": "BBB"},
            aliases_used={},
            expected_present=["AAA", "BBB"],
            observed_bars={"AAA": 5, "BBB": 5},
            raw_existing=[],
            alias_existing=[],
            unavailable={},
            baseline={"path": "", "sha256": "b"},
            compact={
                "path": str(compact_src / f"{day}.parquet"),
                "sha256": None,
                "manifest_sha256": None,
            },
            observed_by_source={"AAA": {"compact_sip": 5}, "BBB": {"compact_sip": 5}},
            evidence_sources=[
                {
                    "name": "compact_sip",
                    "role": "obligation_evidence",
                    "feed": "sip",
                    "provider": "alpaca",
                    "matches_acquisition_feed": True,
                    "may_require_sip_minute_bar": True,
                    "counts_window": "source_reported",
                    "witness_rows_available": False,
                    "path_template": "sip/universe/rth/<day>.parquet",
                    "evidence": "the day's own SIP query",
                    "path": str(compact_src / f"{day}.parquet"),
                    "sha256": None,
                }
            ],
        )
        m = acquire_day(
            plan,
            out,
            FakeFetcher([{"AAA": [_bar(day, 570, 10.0)]}]),
            "code1",
            "cfg1",
            sha256_file(CONTRACT_PATH),
            sha256_file(ALIAS_MAP_PATH),
            "pit1",
            lo,
            hi,
            verbose=False,
        )
        check(
            "zero_bar_recorded_not_fabricated",
            m["symbols_zero_bars"] == ["BBB"] and m["symbols_with_data"] == 1,
            json.dumps({k: m[k] for k in ("symbols_zero_bars", "symbols_with_data")}),
        )
        check(
            "same_feed_contradictory_zero_blocks_admission",
            m["status"] == "incomplete" and "same_feed_contradictory_zeros" in m["refusal_reasons"],
            json.dumps(m["refusal_reasons"]),
        )
        check(
            "same_feed_contradictory_zero_is_explicit",
            [c["ticker"] for c in m["evidence_crosscheck"]["same_feed_contradictory_zeros"]]
            == ["BBB"]
            and m["evidence_crosscheck"]["cross_feed_disagreement_count"] == 0
            and m["evidence_crosscheck"]["witness_artifact"] is None,
            json.dumps(m["evidence_crosscheck"]["same_feed_contradictory_zeros"]),
        )
        checks, _ = verify_day(
            plan,
            out,
            {"code_sha256": "code1", "contract_sha256": sha256_file(CONTRACT_PATH)},
            (lo, hi),
        )
        names = {c["check"] for c in checks if not c["ok"]}
        check(
            "verify_flags_uncovered_symbols",
            any("all_obligated_symbols_covered" in n for n in names),
            str(sorted(names)),
        )
        check(
            "verify_flags_incomplete_status",
            any("status_complete" in n for n in names),
            str(sorted(names)),
        )
        check(
            "verify_flags_same_feed_contradiction",
            any("no_same_feed_contradictory_zeros" in n for n in names),
            str(sorted(names)),
        )
        # the same bytes under a different producer hash must be refused too
        checks2, _ = verify_day(
            plan,
            out,
            {"code_sha256": "other-code", "contract_sha256": sha256_file(CONTRACT_PATH)},
            (lo, hi),
        )
        check(
            "verify_flags_producer_drift",
            any(
                "code_sha256_matches_pinned_producer" in c["check"] for c in checks2 if not c["ok"]
            ),
        )
        # A manifest declaring a window the contract does not have must be
        # refused. The mutation targets window_et_bounds, the authoritative
        # structured field the validator actually reads; editing the readable
        # window_et string alone would only re-pin an incidental rendering
        # instead of testing the declared window.
        man = load_json(out / "bars" / f"{day}.manifest.json")
        man["source"]["window_et_bounds"] = [570, 960]
        atomic_write_json(out / "bars" / f"{day}.manifest.json", man)
        checks3, _ = verify_day(
            plan,
            out,
            {"code_sha256": "code1", "contract_sha256": sha256_file(CONTRACT_PATH)},
            (lo, hi),
        )
        failed3 = {c["check"] for c in checks3 if not c["ok"]}
        check(
            "verify_flags_window_drift",
            any("manifest_window_equals_contract" in n for n in failed3),
            str(sorted(failed3)),
        )
        check(
            "verify_flags_window_utc_contradiction",
            any("manifest_window_utc_matches_bounds" in n for n in failed3),
            str(sorted(failed3)),
        )
        check(
            "verify_flags_readable_window_contradiction",
            any("manifest_window_readable_matches_bounds" in n for n in failed3),
            str(sorted(failed3)),
        )
        # Restoring the declared bounds clears all three: the checks read the
        # manifest's own window, never a hardcoded string.
        man["source"]["window_et_bounds"] = [lo, hi]
        atomic_write_json(out / "bars" / f"{day}.manifest.json", man)
        checks4, _ = verify_day(
            plan,
            out,
            {"code_sha256": "code1", "contract_sha256": sha256_file(CONTRACT_PATH)},
            (lo, hi),
        )
        check(
            "restored_bounds_clear_window_checks",
            not any("window" in c["check"] and not c["ok"] for c in checks4),
            str([c["check"] for c in checks4 if not c["ok"]]),
        )

    # 14. a fully covered day with no contradiction IS admitted
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        plan = DayPlan(
            day=day,
            scope="feb2025",
            vintage="2025-02-01",
            pit_n=2,
            requested=["AAA", "BBB"],
            provider_of={"AAA": "AAA", "BBB": "BBB"},
            aliases_used={},
            expected_present=["AAA", "BBB"],
            observed_bars={"AAA": 5, "BBB": 5},
            raw_existing=[],
            alias_existing=[],
            unavailable={},
            baseline={"path": "", "sha256": "b"},
            compact={"path": "", "sha256": None},
        )
        m = acquire_day(
            plan,
            out,
            FakeFetcher([{"AAA": [_bar(day, 570, 10.0)], "BBB": [_bar(day, 570, 4.0)]}]),
            "code1",
            "cfg1",
            sha256_file(CONTRACT_PATH),
            sha256_file(ALIAS_MAP_PATH),
            "pit1",
            lo,
            hi,
            verbose=False,
        )
        checks, res = verify_day(
            plan,
            out,
            {"code_sha256": "code1", "contract_sha256": sha256_file(CONTRACT_PATH)},
            (lo, hi),
        )
        failed = [c["check"] for c in checks if not c["ok"]]
        check("fully_covered_day_admitted", m["status"] == "complete" and not failed, str(failed))
        check(
            "one_row_set_per_canonical",
            res["checks"]["rows"] == 2 and res["checks"]["symbols"] == 2,
            json.dumps(res["checks"]),
        )

    # 15. scope standing decides what "covered" means: feb2025 REPLACES the
    # fallback (payload alone must cover), aprmay2026 SUPPLEMENTS it (the
    # baseline lane's own in-window names count as covered).
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        for scope, raw_existing, expect_ok in (
            ("feb2025", [], False),
            ("aprmay2026", ["BBB"], True),
        ):
            root = out / scope
            plan = DayPlan(
                day=day,
                scope=scope,
                vintage="v",
                pit_n=2,
                requested=["AAA"],
                provider_of={"AAA": "AAA"},
                aliases_used={},
                expected_present=["AAA", "BBB"],
                observed_bars={"AAA": 5, "BBB": 5},
                raw_existing=raw_existing,
                alias_existing=[],
                unavailable={},
                baseline={"path": "", "sha256": "b"},
                compact={"path": "", "sha256": None},
            )
            acquire_day(
                plan,
                root,
                FakeFetcher([{"AAA": [_bar(day, 570, 10.0)]}]),
                "code1",
                "cfg1",
                sha256_file(CONTRACT_PATH),
                sha256_file(ALIAS_MAP_PATH),
                "pit1",
                lo,
                hi,
                verbose=False,
            )
            ch, _ = verify_day(
                plan,
                root,
                {"code_sha256": "code1", "contract_sha256": sha256_file(CONTRACT_PATH)},
                (lo, hi),
            )
            cov = [c for c in ch if c["check"].endswith("all_obligated_symbols_covered")][0]
            check(f"coverage_standing_{scope}", cov["ok"] is expect_ok, json.dumps(cov))

    # 16. admitted_days contains ONLY complete days; an incomplete day is listed
    # as unadmitted with its reason and never sneaks in beside a status field.
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        good = DayPlan(
            day=day,
            scope="feb2025",
            vintage="v",
            pit_n=1,
            requested=["AAA"],
            provider_of={"AAA": "AAA"},
            aliases_used={},
            expected_present=["AAA"],
            observed_bars={"AAA": 5},
            raw_existing=[],
            alias_existing=[],
            unavailable={},
            baseline={"path": "", "sha256": "b"},
            compact={"path": "", "sha256": None},
        )
        acquire_day(
            good,
            out,
            FakeFetcher([{"AAA": [_bar(day, 570, 10.0)]}]),
            "code1",
            "cfg1",
            sha256_file(CONTRACT_PATH),
            sha256_file(ALIAS_MAP_PATH),
            "pit1",
            lo,
            hi,
            verbose=False,
        )
        bad_day = "2025-02-04"
        bad = DayPlan(
            day=bad_day,
            scope="feb2025",
            vintage="v",
            pit_n=1,
            requested=["AAA"],
            provider_of={"AAA": "AAA"},
            aliases_used={},
            expected_present=["AAA"],
            observed_bars={"AAA": 5},
            raw_existing=[],
            alias_existing=[],
            unavailable={},
            baseline={"path": "", "sha256": "b"},
            compact={"path": "", "sha256": None},
        )
        mb = acquire_day(
            bad,
            out,
            FakeFetcher([{}]),
            "code1",
            "cfg1",
            sha256_file(CONTRACT_PATH),
            sha256_file(ALIAS_MAP_PATH),
            "pit1",
            lo,
            hi,
            verbose=False,
        )
        admitted, unadmitted = [], []
        for p in (good, bad):
            _c, r = verify_day(
                p,
                out,
                {"code_sha256": "code1", "contract_sha256": sha256_file(CONTRACT_PATH)},
                (lo, hi),
            )
            m = r.get("manifest") or {}
            if r.get("status") == REQUIRED_STATUS_ADMITTED:
                admitted.append(p.day)
            else:
                unadmitted.append({"day": p.day, "reasons": m.get("refusal_reasons") or []})
        check(
            "admitted_days_only_complete",
            admitted == [day] and mb["status"] == "incomplete",
            json.dumps({"admitted": admitted, "unadmitted": unadmitted}),
        )
        check(
            "unadmitted_day_states_its_reason",
            unadmitted and unadmitted[0]["day"] == bad_day and unadmitted[0]["reasons"],
            json.dumps(unadmitted),
        )

    # 17b. A name whose only positive evidence comes from a source whose feed is
    # NOT the acquisition feed (the B1 clean fallback is HF/Finnhub-derived, feed
    # unknown) is real evidence but is NOT an obligation the SIP feed can be held
    # to. It stays a genuine SIP provider zero, is reported as
    # cross_feed_bar_not_reproduced, its ORIGINAL rows are preserved as a witness,
    # and it does not block admission. This is the DXR case, generalised: no
    # ticker is special-cased anywhere, and the witness is never relabelled SIP.
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "acq"
        lane = Path(td) / "clean_ohlcv_2025-02.parquet"
        lane_sha = _lane_parquet(
            lane,
            [
                (day, 775, "DXR", 7.78, 124.0),
                (day, 600, "ARMN", 3.5, 900.0),
                ("2025-02-04", 775, "DXR", 8.0, 50.0),
            ],
        )
        plan = DayPlan(
            day=day,
            scope="feb2025",
            vintage="v",
            pit_n=3,
            requested=["AAA", "ARMN", "DXR"],
            provider_of={"AAA": "AAA", "ARMN": "ARMN", "DXR": "DXR"},
            aliases_used={},
            expected_present=["AAA", "ARMN", "DXR"],
            observed_bars={"AAA": 5, "ARMN": 1, "DXR": 1},
            observed_by_source={
                "AAA": {"compact_sip": 5},
                "ARMN": {"baseline_lane_b1": 1},
                "DXR": {"baseline_lane_b1": 1},
            },
            raw_existing=["AAA"],
            alias_existing=[],
            unavailable={"baseline_only_observed": ["ARMN", "DXR"]},
            baseline={
                "path": str(lane),
                "sha256": lane_sha,
                "standing": "replaced_by_this_acquisition",
                "source_name": "baseline_lane_b1",
                "feed": "unknown",
                "matches_acquisition_feed": False,
            },
            compact={"path": "", "sha256": None},
            evidence_sources=[
                {
                    "name": "compact_sip",
                    "role": "obligation_evidence",
                    "feed": "sip",
                    "provider": "alpaca",
                    "matches_acquisition_feed": True,
                    "may_require_sip_minute_bar": True,
                    "counts_window": "source_reported",
                    "witness_rows_available": False,
                    "path_template": "sip/universe/rth/<day>.parquet",
                    "evidence": "the day's own SIP query",
                    "path": "",
                    "sha256": None,
                },
                {
                    "name": "baseline_lane_b1",
                    "role": "replaced_baseline",
                    "feed": "unknown",
                    "provider": "mito0o852/OHLCV-1m (Hugging Face), upstream Finnhub",
                    "matches_acquisition_feed": False,
                    "may_require_sip_minute_bar": False,
                    "counts_window": "in_window",
                    "witness_rows_available": True,
                    "path_template": "clean_ohlcv_<month>.parquet",
                    "evidence": "feed unknown; the lane B1 replaces",
                    "path": str(lane),
                    "sha256": lane_sha,
                },
            ],
        )
        m = acquire_day(
            plan,
            out,
            FakeFetcher([{"AAA": [_bar(day, 570, 10.0)]}]),
            "code1",
            "cfg1",
            sha256_file(CONTRACT_PATH),
            sha256_file(ALIAS_MAP_PATH),
            "pit1",
            lo,
            hi,
            verbose=False,
        )
        ev = m["evidence_crosscheck"]
        check(
            "cross_feed_zero_reported_not_blocking",
            m["status"] == "complete"
            and not m["refusal_reasons"]
            and [d["ticker"] for d in ev["cross_feed_disagreements"]] == ["ARMN", "DXR"]
            and all(
                d["diagnostic"] == CROSS_FEED_DIAGNOSTIC for d in ev["cross_feed_disagreements"]
            ),
            json.dumps({"status": m["status"], "reasons": m["refusal_reasons"]}),
        )
        check(
            "cross_feed_name_is_still_a_sip_zero",
            m["symbols_zero_bars"] == ["ARMN", "DXR"],
            json.dumps(m["symbols_zero_bars"]),
        )
        check(
            "same_feed_and_cross_feed_do_not_collapse",
            ev["same_feed_contradictory_zero_count"] == 0
            and ev["cross_feed_disagreement_count"] == 2
            and ev["sources"][1]["feed"] == "unknown"
            and ev["sources"][0]["feed"] == "sip",
            json.dumps(
                {
                    "same": ev["same_feed_contradictory_zero_count"],
                    "cross": ev["cross_feed_disagreement_count"],
                }
            ),
        )
        w = out / "cross_feed_witnesses" / f"{day}.parquet"
        wdf = pl.read_parquet(w) if w.exists() else pl.DataFrame(schema=WITNESS_SCHEMA)
        check(
            "cross_feed_witness_preserved_with_origin",
            w.exists()
            and ev["witness_sha256"] == sha256_file(w)
            and ev["witness_rows"] == wdf.height == 2
            and list(wdf.columns) == list(WITNESS_SCHEMA)
            and set(wdf["parent_sha256"].to_list()) == {lane_sha}
            and set(wdf["source_feed"].to_list()) == {"unknown"}
            and set(wdf["diagnostic"].to_list()) == {CROSS_FEED_DIAGNOSTIC}
            and set(wdf["fresh_n_bars"].to_list()) == {0}
            and sorted(wdf["ticker"].to_list()) == ["ARMN", "DXR"]
            and wdf.filter(pl.col("ticker") == "DXR")["close"].to_list() == [7.78]
            and wdf.filter(pl.col("ticker") == "DXR")["volume"].to_list() == [124.0]
            and wdf.filter(pl.col("ticker") == "DXR")["et_minute"].to_list() == [775],
            json.dumps({"artifact": ev["witness_artifact"], "rows": ev["witness_rows"]}),
        )
        check(
            "cross_feed_witness_never_relabelled_sip",
            ev["witness_role"] == "legacy_cross_feed_proxy_not_sip"
            and str(ev["witness_artifact"]).startswith(f"{WITNESS_DIRNAME}/")
            and all(d["source_feed"] != ACQUISITION_FEED for d in ev["cross_feed_disagreements"]),
            str(ev["witness_artifact"]),
        )
        # The SIP REQUEST was complete: the disagreement is an evidence
        # classification, not a coverage failure of the response.
        check(
            "cross_feed_count_is_not_a_sip_coverage_failure",
            m["sip_coverage"]["complete"] is True
            and m["sip_coverage"]["unaccounted_symbols"] == []
            and m["symbols_request_failed"] == []
            and m["obligation"]["coverage"]["cross_feed_witness"] == 2
            and m["obligation"]["coverage"]["uncovered_n"] == 0,
            json.dumps(m["sip_coverage"]),
        )
        ch, _ = verify_day(
            plan,
            out,
            {"code_sha256": "code1", "contract_sha256": sha256_file(CONTRACT_PATH)},
            (lo, hi),
        )
        names = {c["check"] for c in ch if not c["ok"]}
        check(
            "verify_accepts_preserved_cross_feed_witness",
            not names,
            str(sorted(names)),
        )
        # A witness that no longer hashes to the manifest is refused: the
        # preserved proxy is evidence and tampering with it invalidates the day.
        pl.DataFrame({"not": ["the witness"]}).write_parquet(w)
        ch2, _ = verify_day(
            plan,
            out,
            {"code_sha256": "code1", "contract_sha256": sha256_file(CONTRACT_PATH)},
            (lo, hi),
        )
        names2 = {c["check"] for c in ch2 if not c["ok"]}
        check(
            "tampered_witness_invalidates_the_day",
            any("cross_feed_witnesses_preserved" in n for n in names2),
            str(sorted(names2)),
        )

    # 17c. A symbol whose request never completed is request_failed, never a
    # zero, and blocks admission. Folding it into the zero class asserted a
    # falsehood about names whose request had simply died.
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        plan = DayPlan(
            day=day,
            scope="feb2025",
            vintage="v",
            pit_n=2,
            requested=["AAA", "BBB"],
            provider_of={"AAA": "AAA", "BBB": "BBB"},
            aliases_used={},
            expected_present=["AAA"],
            observed_bars={"AAA": 5},
            raw_existing=[],
            alias_existing=[],
            unavailable={},
            baseline={"path": "", "sha256": "b"},
            compact={"path": "", "sha256": None},
        )

        class DeadBatch:
            def fetch(self, symbols, start, end, asof=None):
                raise ProviderError("rest 503 after retries")

        m = acquire_day(
            plan,
            out,
            DeadBatch(),
            "code1",
            "cfg1",
            sha256_file(CONTRACT_PATH),
            sha256_file(ALIAS_MAP_PATH),
            "pit1",
            lo,
            hi,
            verbose=False,
        )
        check(
            "failed_request_is_not_a_zero_bar",
            m["symbols_request_failed"] == ["AAA", "BBB"]
            and m["symbols_zero_bars"] == []
            and m["symbols_with_data"] == 0,
            json.dumps({k: m[k] for k in ("symbols_request_failed", "symbols_zero_bars")}),
        )
        check(
            "request_failed_symbols_block_admission",
            m["status"] == "incomplete" and "request_failed_symbols" in m["refusal_reasons"],
            json.dumps(m["refusal_reasons"]),
        )

    # 17d. A failed request is NEVER converted into a zero, and cross-feed
    # evidence about that name does not turn it into a witness either: nothing is
    # known about the request, so nothing may be asserted about the name.
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "acq"
        lane = Path(td) / "clean_ohlcv_2025-02.parquet"
        lane_sha = _lane_parquet(lane, [(day, 775, "DXR", 7.78, 124.0)])
        plan = DayPlan(
            day=day,
            scope="feb2025",
            vintage="v",
            pit_n=2,
            requested=["AAA", "DXR"],
            provider_of={"AAA": "AAA", "DXR": "DXR"},
            aliases_used={},
            expected_present=["AAA", "DXR"],
            observed_bars={"AAA": 5, "DXR": 1},
            observed_by_source={"AAA": {"compact_sip": 5}, "DXR": {"baseline_lane_b1": 1}},
            raw_existing=[],
            alias_existing=[],
            unavailable={"baseline_only_observed": ["DXR"]},
            baseline={
                "path": str(lane),
                "sha256": lane_sha,
                "standing": "replaced_by_this_acquisition",
            },
            compact={"path": "", "sha256": None},
            evidence_sources=[
                {
                    "name": "baseline_lane_b1",
                    "role": "replaced_baseline",
                    "feed": "unknown",
                    "provider": "mito0o852/OHLCV-1m (Hugging Face)",
                    "matches_acquisition_feed": False,
                    "may_require_sip_minute_bar": False,
                    "counts_window": "in_window",
                    "witness_rows_available": True,
                    "path_template": "clean_ohlcv_<month>.parquet",
                    "evidence": "feed unknown",
                    "path": str(lane),
                    "sha256": lane_sha,
                }
            ],
        )

        class DeadOne:
            def fetch(self, symbols, start, end, asof=None):
                raise ProviderError("rest 503 after retries")

        m = acquire_day(
            plan,
            out,
            DeadOne(),
            "code1",
            "cfg1",
            sha256_file(CONTRACT_PATH),
            sha256_file(ALIAS_MAP_PATH),
            "pit1",
            lo,
            hi,
            verbose=False,
        )
        ev = m["evidence_crosscheck"]
        check(
            "failed_request_never_becomes_a_zero_or_a_witness",
            m["symbols_request_failed"] == ["AAA", "DXR"]
            and m["symbols_zero_bars"] == []
            and ev["cross_feed_disagreement_count"] == 0
            and ev["witness_artifact"] is None
            and m["obligation"]["coverage"]["cross_feed_witness"] == 0
            and m["status"] == "incomplete"
            and "request_failed_symbols" in m["refusal_reasons"],
            json.dumps(m["refusal_reasons"]),
        )
        check(
            "no_witness_written_when_nothing_is_known",
            not (out / "cross_feed_witnesses" / f"{day}.parquet").exists(),
        )

    # 18. the joint publisher: TWO blockers under DIFFERENT roots with their own
    # pinned producers verify independently - B1's new rule cannot stale B2's
    # already-verified days, neither blocker's tool hash is required to equal the
    # other's, and tampering with a payload, a witness, a producer snapshot or an
    # origin lane invalidates the claim that names it.
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        data = tmp / "data"
        lane_b1 = data / "clean_ohlcv_2025-02.parquet"
        lane_b1_sha = _lane_parquet(lane_b1, [("2025-02-03", 775, "DXR", 7.78, 124.0)])
        lane_b2 = data / "backfill/ohlcv_2026-04.parquet"
        lane_b2_sha = _lane_parquet(lane_b2, [("2026-04-01", 600, "AAA", 9.0, 500.0)])
        prod = tmp / PRODUCERS_DIR.relative_to(ROOT)
        prod.mkdir(parents=True)
        prod_rel = str(PRODUCERS_DIR.relative_to(ROOT))
        for name, blob in (
            ("atlas_acquire_sip_bars.v4.py", b"# v4 producer\n"),
            ("atlas_acquisition_contract.v4.json", b'{"v": 4}\n'),
            ("atlas_acquire_sip_bars.v3.py", b"# v3 producer\n"),
            ("atlas_acquisition_contract.v3.json", b'{"v": 3}\n'),
        ):
            (prod / name).write_bytes(blob)
        b1_days = sorted(full_scope_day_set("feb2025"))
        b2_days = sorted(full_scope_day_set("aprmay2026"))

        def producer_block(code_rel: str, contract_rel: str, version: str) -> dict:
            code_sha = sha256_file(prod / Path(code_rel).name)
            contract_sha = sha256_file(prod / Path(contract_rel).name)
            return {
                "snapshot_path": code_rel,
                "snapshot_sha256": code_sha,
                "code_sha256_at_capture": code_sha,
                "contract_snapshot_path": contract_rel,
                "contract_snapshot_sha256": contract_sha,
                "contract_sha256_at_capture": contract_sha,
                "version": version,
            }

        policy = {
            "schema": "atlas.acquisition.source-policy.v0",
            "policy_id": "selftest",
            "acquisition_feed": "sip",
            "feed_classes": {k: dict(v) for k, v in REQUIRED_FEED_CLASSES.items()},
            "sources": {
                "compact_sip": {
                    "role": "obligation_evidence",
                    "feed": "sip",
                    "provider": "alpaca",
                    "counts_window": "source_reported",
                    "witness_rows_available": False,
                    "path_template": "sip/universe/rth/<day>.parquet",
                    "evidence": "the day's own SIP query",
                },
                "baseline_lane_b1": {
                    "role": "replaced_baseline",
                    "feed": "unknown",
                    "provider": "mito0o852/OHLCV-1m (Hugging Face), upstream Finnhub",
                    "counts_window": "in_window",
                    "witness_rows_available": True,
                    "path_template": "clean_ohlcv_<month>.parquet",
                    "evidence": "feed unknown",
                    "sha256_pins": {"2025-02": lane_b1_sha},
                },
                "baseline_lane_b2": {
                    "role": "supplemented_baseline",
                    "feed": "sip",
                    "provider": "alpaca",
                    "counts_window": "in_window",
                    "witness_rows_available": True,
                    "path_template": "backfill/ohlcv_<month>.parquet",
                    "evidence": "raw SIP backfill",
                    "sha256_pins": {"2026-04": lane_b2_sha},
                },
            },
            "per_blocker": {
                "B1_raw_2025_02": {
                    "scope": "feb2025",
                    "version": "v4",
                    "root_rel": "atlas/acquisition/v4",
                    "attestation_rel": "evidence/acquisition_admission_feb2025.json",
                    "expected_days": len(b1_days),
                    "sources": ["compact_sip", "baseline_lane_b1"],
                    "producer": producer_block(
                        f"{prod_rel}/atlas_acquire_sip_bars.v4.py",
                        f"{prod_rel}/atlas_acquisition_contract.v4.json",
                        "v4",
                    ),
                },
                "B2_raw_roster_2026_04_05": {
                    "scope": "aprmay2026",
                    "version": "v3",
                    "root_rel": "atlas/acquisition/v3",
                    "attestation_rel": "evidence/acquisition_admission_aprmay2026.json",
                    "expected_days": len(b2_days),
                    "sources": ["compact_sip", "baseline_lane_b2"],
                    "producer": producer_block(
                        f"{prod_rel}/atlas_acquire_sip_bars.v3.py",
                        f"{prod_rel}/atlas_acquisition_contract.v3.json",
                        "v3",
                    ),
                },
            },
        }
        pol_path = tmp / "acquisition" / "source_policy.json"
        atomic_write_json(pol_path, policy)

        class FakePit:
            sha256 = "pit-union"

        info = validate_policy(policy, data, pol_path, root=tmp)
        refs = info["refs"]
        out_ev = tmp / "joint" / "evidence"
        ctx = {
            "out_data": None,
            "policy": policy,
            "policy_info": info,
            "alias_map_sha": "am-union",
            "policy_sha": sha256_file(pol_path),
            "pit": FakePit(),
        }

        def write_day(ref, d, cross=False):
            root = ref["root"]
            atomic_write_parquet(pl.DataFrame({"ticker": ["AAA"]}), root / "bars" / f"{d}.parquet")
            pins = baseline_pins(ref, info)
            man = {
                "day": d,
                "scope": ref["scope"],
                "status": "complete",
                "code_sha256": ref["producer"]["code_sha256_at_capture"],
                "contract_sha256": ref["producer"]["contract_sha256_at_capture"],
                "input_sha256": {
                    "alias_map": "am-union",
                    "baseline_lane": pins.get(d[:7]),
                },
                "errors": [],
                "symbols_request_failed": [],
                "rows": 1,
                "requested_symbols": 1,
                "symbols_with_data": 1,
                "symbols_zero_bars": [],
                "symbols_invalid": [],
                "sip_coverage": {
                    "complete": True,
                    "unaccounted_symbols": [],
                    "symbols_request_failed": 0,
                },
            }
            if cross:
                lanes = data / "clean_ohlcv_2025-02.parquet"
                wdf = pl.DataFrame(
                    {
                        "parent_sha256": [lane_b1_sha],
                        "row_ordinal": [0],
                        "canonical_time_utc": pl.Series(
                            [_et_ts(d, 775)], dtype=pl.Datetime("ns", "UTC")
                        ),
                        "et_minute": [775],
                        "ticker": ["DXR"],
                        "source_name": ["baseline_lane_b1"],
                        "source_path": [str(lanes)],
                        "source_feed": ["unknown"],
                        "source_provider": ["mito0o852"],
                        "open": [7.78],
                        "high": [7.78],
                        "low": [7.78],
                        "close": [7.78],
                        "volume": [124.0],
                        "source_n_bars_in_window": [1],
                        "fresh_feed": ["sip"],
                        "fresh_n_bars": [0],
                        "diagnostic": [CROSS_FEED_DIAGNOSTIC],
                        "day": [d],
                    },
                    schema=WITNESS_SCHEMA,
                )
                wrel = witness_artifact_rel(d)
                atomic_write_parquet(wdf, root / wrel)
                man["evidence_crosscheck"] = {
                    "acquisition_feed": "sip",
                    "sources": [],
                    "same_feed_contradictory_zeros": [],
                    "same_feed_contradictory_zero_count": 0,
                    "cross_feed_disagreements": [
                        {
                            "ticker": "DXR",
                            "source": "baseline_lane_b1",
                            "source_feed": "unknown",
                            "fresh_feed": "sip",
                            "fresh_n_bars": 0,
                            "diagnostic": CROSS_FEED_DIAGNOSTIC,
                        }
                    ],
                    "cross_feed_disagreement_count": 1,
                    "cross_feed_names": ["DXR"],
                    "witness_artifact": wrel,
                    "witness_sha256": sha256_file(root / wrel),
                    "witness_rows": 1,
                    "witness_role": "legacy_cross_feed_proxy_not_sip",
                }
            atomic_write_json(root / "bars" / f"{d}.manifest.json", man)

        def attestation(ref, days, ok=True):
            bid = ref["blocker_id"]
            root = ref["root"]
            entries = [
                {
                    "day": d,
                    "file": f"bars/{d}.parquet",
                    "file_sha256": sha256_file(root / "bars" / f"{d}.parquet"),
                    "manifest": f"bars/{d}.manifest.json",
                    "status": "complete",
                    "rows": 1,
                }
                for d in days
            ]
            return {
                "version": "v0",
                "schema": "atlas.acquisition.admission.v0",
                "scope": ref["scope"],
                "blocker_id": bid,
                "blocker_version": ref["version"],
                "acquisition_root": str(root),
                "generated_at": "2026-01-01T00:00:00+00:00",
                "parents": blocker_parents({**ctx, "out_data": root}, ref),
                "blockers": {
                    bid: {
                        "admitted_days": list(days) if ok else [],
                        "unadmitted_days": []
                        if ok
                        else [{"day": d, "reasons": ["x"]} for d in days],
                        "days": entries,
                        "residual_confirmed_trading_gaps": [],
                        "cross_feed_disagreements": {"count": 0, "names": [], "witness_days": []},
                    }
                },
                "requested_outcome_accounting": {
                    "requested_symbols": len(days),
                    "symbols_with_data": len(days),
                    "symbols_zero_bars": 0,
                    "symbols_invalid": 0,
                    "symbols_request_failed": 0,
                },
                "all_checks_passed": ok,
                "failed_checks": []
                if ok
                else [{"check": f"{days[0]}:status_complete", "ok": False}],
            }

        b1_ref = refs["B1_raw_2025_02"]
        b2_ref = refs["B2_raw_roster_2026_04_05"]
        for d in b1_days:
            write_day(b1_ref, d, cross=(d == "2025-02-03"))
        for d in b2_days:
            write_day(b2_ref, d)
        b1_day, b2_day = b1_days[0], b2_days[0]

        # only B1 attested so far: B2's blocker stays visibly unverified
        atomic_write_json(b1_ref["attestation"], attestation(b1_ref, b1_days))
        u1 = publish_admission_union(out_ev, ctx, refs)
        check(
            "union_missing_blocker_stays_unready",
            u1["missing_scopes"] == ["aprmay2026"]
            and not u1["all_scopes_ready"]
            and u1["blockers"]["B2_raw_roster_2026_04_05"]["state"] == "unverified"
            and u1["blockers"]["B2_raw_roster_2026_04_05"]["admitted_days"] == [],
            json.dumps({"missing": u1["missing_scopes"], "ready": u1["all_scopes_ready"]}),
        )
        check(
            "union_keeps_b1_entry",
            u1["scopes"]["feb2025"]["state"] == "ready"
            and u1["scopes"]["feb2025"]["admitted_days"] == b1_days
            and u1["scopes"]["feb2025"]["required_day_count"] == len(b1_days)
            and u1["scopes"]["feb2025"]["version"] == "v4"
            and u1["scopes"]["feb2025"]["acquisition_root"] == str(b1_ref["root"]),
            json.dumps(
                {
                    k: u1["scopes"]["feb2025"][k]
                    for k in ("state", "version", "required_day_count", "is_full_scope")
                }
            ),
        )
        # the cross-feed witness is reported and does not gate readiness
        check(
            "union_reports_cross_feed_without_gating",
            u1["cross_feed_disagreements"]["count"] == 1
            and u1["cross_feed_disagreements"]["names"] == ["DXR"]
            and u1["cross_feed_disagreements"]["is_a_sip_coverage_failure"] is False
            and u1["scopes"]["feb2025"]["state"] == "ready",
            json.dumps(u1["cross_feed_disagreements"]),
        )

        # Now B2 attests: BOTH blockers are ready under DIFFERENT roots, with
        # different pinned producers and the exact required day counts.
        atomic_write_json(b2_ref["attestation"], attestation(b2_ref, b2_days))
        u2 = publish_admission_union(out_ev, ctx, refs)
        check(
            "mixed_roots_both_ready_with_exact_days",
            u2["all_scopes_ready"]
            and u2["scopes"]["feb2025"]["admitted_days"] == b1_days
            and u2["scopes"]["aprmay2026"]["admitted_days"] == b2_days
            and len(b1_days) == 19
            and len(b2_days) == 41
            and not u2["missing_scopes"]
            and u2["blockers"]["B1_raw_2025_02"]["version"] == "v4"
            and u2["blockers"]["B2_raw_roster_2026_04_05"]["version"] == "v3"
            and u2["per_blocker_roots"]["B1_raw_2025_02"]
            != u2["per_blocker_roots"]["B2_raw_roster_2026_04_05"],
            json.dumps({k: len(v["admitted_days"]) for k, v in u2["scopes"].items()}),
        )
        check(
            "blockers_pin_their_own_producer_not_the_other",
            u2["blockers"]["B1_raw_2025_02"]["producer"]["code_sha256_at_capture"]
            != u2["blockers"]["B2_raw_roster_2026_04_05"]["producer"]["code_sha256_at_capture"]
            and u2["blockers"]["B1_raw_2025_02"]["producer"]["code_sha256_at_capture"]
            == sha256_file(prod / "atlas_acquire_sip_bars.v4.py")
            and u2["blockers"]["B2_raw_roster_2026_04_05"]["producer"]["code_sha256_at_capture"]
            == sha256_file(prod / "atlas_acquire_sip_bars.v3.py"),
            json.dumps(
                {b: e["producer"]["code_sha256_at_capture"][:8] for b, e in u2["blockers"].items()}
            ),
        )
        check(
            "day_roots_resolve_every_day_to_its_own_root",
            len(u2["day_roots"]) == len(b1_days) + len(b2_days)
            and all(u2["day_roots"][d]["acquisition_root"] == str(b1_ref["root"]) for d in b1_days)
            and all(u2["day_roots"][d]["acquisition_root"] == str(b2_ref["root"]) for d in b2_days)
            and u2["day_roots"]["2025-02-03"]["cross_feed_witness"]
            == witness_artifact_rel("2025-02-03")
            and u2["day_roots"]["2025-02-03"]["cross_feed_witness_sha256"]
            == sha256_file(b1_ref["root"] / witness_artifact_rel("2025-02-03"))
            and all(u2["day_roots"][d]["sip_coverage_complete"] is True for d in b1_days + b2_days),
            json.dumps({d: u2["day_roots"][d]["acquisition_root"] for d in (b1_day, b2_day)}),
        )
        # every one of the 60 days carries a manifest hash the union re-derived
        check(
            "day_roots_carry_manifest_hashes",
            all(
                u2["day_roots"][d]["manifest_sha256"]
                == sha256_file(
                    Path(u2["day_roots"][d]["acquisition_root"]) / "bars" / f"{d}.manifest.json"
                )
                for d in b1_days + b2_days
            ),
            json.dumps({d: u2["day_roots"][d]["manifest_sha256"][:8] for d in (b1_day, b2_day)}),
        )

        # A one-day subset per blocker still resolves nothing.
        atomic_write_json(b1_ref["attestation"], attestation(b1_ref, [b1_day]))
        atomic_write_json(b2_ref["attestation"], attestation(b2_ref, [b2_day]))
        us = publish_admission_union(out_ev, ctx, refs)
        check(
            "one_plus_one_subsets_cannot_resolve_full_scopes",
            not us["all_scopes_ready"]
            and us["scopes"]["feb2025"]["state"] == "verified_subset"
            and us["scopes"]["aprmay2026"]["state"] == "verified_subset"
            and us["scopes"]["feb2025"]["admitted_days"] == []
            and us["scopes"]["aprmay2026"]["admitted_days"] == []
            and us["scopes"]["feb2025"]["verified_subset_days"] == [b1_day]
            and us["scopes"]["aprmay2026"]["verified_subset_days"] == [b2_day]
            and len(us["scopes"]["feb2025"]["missing_required_days"]) == len(b1_days) - 1
            and len(us["scopes"]["aprmay2026"]["missing_required_days"]) == len(b2_days) - 1
            and all(b["admitted_day_count"] == 0 for b in us["blockers"].values()),
            json.dumps({k: v["state"] for k, v in us["scopes"].items()}),
        )

        # Back to full coverage, then corrupt ONE B1 day payload: its manifest
        # still says complete, so only the re-hash can refuse it - and it must
        # refuse B1 alone.
        atomic_write_json(b1_ref["attestation"], attestation(b1_ref, b1_days))
        atomic_write_json(b2_ref["attestation"], attestation(b2_ref, b2_days))
        p = b1_ref["root"] / "bars" / f"{b1_day}.parquet"
        p.write_bytes(p.read_bytes() + b"corrupt")
        u3 = publish_admission_union(out_ev, ctx, refs)
        mm = u3["scopes"]["feb2025"]["rehash"]["mismatches"]
        check(
            "union_rehash_invalidates_corrupt_day",
            not u3["all_scopes_ready"]
            and u3["scopes"]["feb2025"]["state"] == "not_ready"
            and u3["scopes"]["feb2025"]["admitted_days"] == []
            and any(m["day"] == b1_day and m["check"] == "payload_sha256_mismatch" for m in mm),
            json.dumps(mm[:2]),
        )
        check(
            "corrupt_one_blocker_leaves_the_other_intact",
            u3["scopes"]["aprmay2026"]["state"] == "ready"
            and u3["scopes"]["aprmay2026"]["admitted_days"] == b2_days,
            json.dumps(u3["scopes"]["aprmay2026"]["state"]),
        )

        # Tampering with the preserved witness invalidates B1 as well.
        atomic_write_parquet(
            pl.DataFrame({"ticker": ["AAA"]}), b1_ref["root"] / "bars" / f"{b1_day}.parquet"
        )
        pl.DataFrame({"not": ["the witness"]}).write_parquet(
            b1_ref["root"] / witness_artifact_rel("2025-02-03")
        )
        u3b = publish_admission_union(out_ev, ctx, refs)
        check(
            "union_rehash_invalidates_tampered_witness",
            not u3b["all_scopes_ready"]
            and u3b["scopes"]["feb2025"]["state"] == "not_ready"
            and any(
                m.get("check") == "cross_feed_witness_sha256_mismatch"
                for m in u3b["scopes"]["feb2025"]["rehash"]["mismatches"]
            ),
            json.dumps(u3b["scopes"]["feb2025"]["rehash"]["mismatches"][:2]),
        )
        write_day(b1_ref, "2025-02-03", cross=True)

        # An attestation naming a different producer than the pin is stale, never
        # merged - and only that blocker goes stale.
        doc = attestation(b1_ref, b1_days)
        doc["parents"]["code_sha256"] = "code-from-another-producer"
        atomic_write_json(b1_ref["attestation"], doc)
        u4 = publish_admission_union(out_ev, ctx, refs)
        check(
            "union_marks_pinned_producer_mismatch_stale",
            [x["scope"] for x in u4["stale_scopes"]] == ["feb2025"]
            and not u4["all_scopes_ready"]
            and u4["scopes"]["aprmay2026"]["state"] == "ready"
            and any(p["field"] == "code_sha256" for x in u4["stale_scopes"] for p in x["problems"]),
            json.dumps(u4["stale_scopes"]),
        )

        # A blocker whose own checks failed contributes no admitted days.
        atomic_write_json(b1_ref["attestation"], attestation(b1_ref, b1_days, ok=False))
        u5 = publish_admission_union(out_ev, ctx, refs)
        check(
            "union_refuses_failed_blocker",
            u5["scopes"]["feb2025"]["state"] == "not_ready"
            and u5["scopes"]["feb2025"]["admitted_days"] == [],
            json.dumps(u5["scopes"]["feb2025"]["state"]),
        )
        atomic_write_json(b1_ref["attestation"], attestation(b1_ref, b1_days))
        check(
            "union_publish_is_idempotent",
            publish_admission_union(out_ev, ctx, refs)["all_scopes_ready"] is True,
        )

        # ORIGIN PINS. A producer snapshot, a pinned lane and a declared root are
        # all origin claims: editing any one of them refuses to publish.
        (prod / "atlas_acquire_sip_bars.v4.py").write_bytes(b"# edited after capture\n")
        try:
            validate_policy(policy, data, pol_path, root=tmp)
            snap_ok = False
        except ValueError as e:
            snap_ok = "producer snapshot" in str(e)
        check("tampering_producer_snapshot_refuses_policy", snap_ok)
        (prod / "atlas_acquire_sip_bars.v4.py").write_bytes(b"# v4 producer\n")

        # An origin proof outside the tracked producers/ directory is not an
        # origin proof: the pin must name a capture, not any file on disk.
        loose = {**policy, "per_blocker": dict(policy["per_blocker"])}
        loose["per_blocker"]["B1_raw_2025_02"] = {
            **policy["per_blocker"]["B1_raw_2025_02"],
            "producer": {
                **policy["per_blocker"]["B1_raw_2025_02"]["producer"],
                "snapshot_path": "loose_producer.py",
                "snapshot_sha256": sha256_file(prod / "atlas_acquire_sip_bars.v4.py"),
                "code_sha256_at_capture": sha256_file(prod / "atlas_acquire_sip_bars.v4.py"),
            },
        }
        (tmp / "loose_producer.py").write_bytes(b"# not a tracked capture\n")
        try:
            validate_policy(loose, data, pol_path, root=tmp)
            loose_ok = False
        except ValueError as e:
            loose_ok = "must live under" in str(e)
        check("origin_proof_must_be_a_tracked_producer_capture", loose_ok)

        lane_b1.write_bytes(lane_b1.read_bytes() + b"x")
        try:
            validate_policy(policy, data, pol_path, root=tmp)
            lane_ok = False
        except ValueError as e:
            lane_ok = "origin archive was replaced" in str(e)
        check("tampering_origin_lane_refuses_policy", lane_ok)

        # A blocker may only be produced in the root its policy declares: the
        # declared root is enforced where a root is produced, so a rule change
        # can never be run against the directory that holds another blocker's
        # verified bytes.
        try:
            assert_declared_root(
                {"policy_info": info, "out_data": None},
                "feb2025",
                data / "atlas/acquisition/v3",
            )
            root_ok = False
        except SystemExit as e:
            root_ok = "declares root" in str(e)
        check("blocker_may_only_be_produced_in_its_declared_root", root_ok)

    # 19. window/typed-timestamp conversions agree with the contract window
    s, e = window_utc("2025-02-03", PROJECTION_LO, PROJECTION_HI)
    check(
        "window_is_0925_1605_et",
        s.astimezone(ET).strftime("%H:%M") == "09:25"
        and e.astimezone(ET).strftime("%H:%M") == "16:05",
        f"{s.astimezone(ET)} .. {e.astimezone(ET)}",
    )

    # 20. the projection window is CLOSED: a bar stamped exactly at the upper
    # bound is IN window, one minute past it is not. This is the exact case that
    # made 2026-04-01 unadmittable: 10 bars stamped 16:05 ET were flagged as
    # out-of-window under an exclusive upper bound.
    edge = bars_to_frame(
        {
            "AAA": [
                _bar(day, PROJECTION_LO, 10.0),
                _bar(day, PROJECTION_HI - 1, 10.0),
                _bar(day, PROJECTION_HI, 10.0),
            ]
        },
        {"AAA": "AAA"},
    )
    v_edge = validate_frame(edge, PROJECTION_LO, PROJECTION_HI)
    check(
        "upper_bound_minute_is_in_window",
        v_edge["out_of_window_rows"] == 0 and v_edge["rows"] == 3,
        json.dumps(v_edge),
    )
    past = bars_to_frame(
        {"AAA": [_bar(day, PROJECTION_HI, 10.0), _bar(day, PROJECTION_HI + 1, 10.0)]},
        {"AAA": "AAA"},
    )
    check(
        "minute_past_upper_bound_is_rejected",
        validate_frame(past, PROJECTION_LO, PROJECTION_HI)["out_of_window_rows"] == 1,
    )
    below = bars_to_frame(
        {"AAA": [_bar(day, PROJECTION_LO, 10.0), _bar(day, PROJECTION_LO - 1, 10.0)]},
        {"AAA": "AAA"},
    )
    check(
        "minute_below_lower_bound_is_rejected",
        validate_frame(below, PROJECTION_LO, PROJECTION_HI)["out_of_window_rows"] == 1,
    )
    # The request end is the closed interval's own endpoint with no epsilon
    # subtracted, and the manifest round-trips the exact integers.
    _ws, we = window_utc(day, PROJECTION_LO, PROJECTION_HI)
    check(
        "request_end_is_the_bound_itself",
        et_minute(we) == PROJECTION_HI
        and we.astimezone(ET).second == 0
        and et_minute(_ws) == PROJECTION_LO,
        f"{we.isoformat()} et_min={et_minute(we)}",
    )
    check(
        "manifest_bounds_round_trip",
        _window_from_manifest(
            {
                "source": {
                    "window_et_bounds": [PROJECTION_LO, PROJECTION_HI],
                    "window_bounds_inclusive": True,
                }
            }
        )
        == (PROJECTION_LO, PROJECTION_HI)
        and _window_from_manifest({"source": {"window_et": "09:25-16:05"}})
        == (PROJECTION_LO, PROJECTION_HI),
    )

    bad = 0
    for name, ok, detail in results:
        print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
        if not ok:
            bad += 1
    print(f"self-test: {len(results) - bad}/{len(results)} passed")
    return 1 if bad else 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--stage", choices=("plan", "acquire", "verify", "publish"))
    ap.add_argument("--scope", choices=SCOPES)
    ap.add_argument(
        "--days", default=None, help="comma-separated guarded dev days (default: the whole scope)"
    )
    ap.add_argument(
        "--out-data",
        default=None,
        help="acquisition root (default: the root source_policy.json declares for --scope's "
        "blocker; v0, v1 and v2 are retained captures that are never a default target)",
    )
    ap.add_argument(
        "--out-evidence",
        default=None,
        help="evidence root (default <out-data>/evidence; REQUIRED for --stage publish, and "
        "the directory the joint acquisition_admission.json is written to)",
    )
    ap.add_argument(
        "--blocker-ref",
        action="append",
        default=None,
        metavar="BLOCKER_ID=ATTESTATION_PATH",
        help="explicit per-blocker attestation path for --stage publish/verify (repeatable); "
        "an unoverridden blocker uses the path its source_policy.json declares",
    )
    ap.add_argument("--client", choices=("sdk", "rest"), default="sdk")
    ap.add_argument("--selftest", action="store_true", help="synthetic, offline self-tests")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.stage == "publish":
        if args.scope or args.days:
            ap.error("--stage publish takes no --scope/--days: it publishes every blocker")
        if not args.out_evidence:
            ap.error("--stage publish requires --out-evidence")
        union = stage_publish(args)
        print(json.dumps(_union_summary(union), indent=1))
        return 0 if union["all_scopes_ready"] else 1
    if not args.stage or not args.scope:
        ap.error("--stage and --scope are required (or use --selftest / --stage publish)")
    if args.out_data is None:
        # The declared root for this scope's blocker is the only sane default.
        # v0/v1/v2 are retained captures and are never a default target; the
        # per-blocker policy names the version this admission is being built for.
        policy = load_policy()
        data = resolve_data_root()
        ref = validate_policy(policy, data)["refs"][BLOCKER_OF_SCOPE[args.scope]]
        args.out_data = str(ref["root"])
    if args.stage == "plan":
        doc = stage_plan(args)
        print(
            json.dumps(
                {
                    "days": doc["n_days"],
                    "blocker_version": doc["blocker_version"],
                    "acquisition_root": doc["acquisition_root"],
                    "totals": doc["totals"],
                    "plan": str(
                        _out_evidence(args, Path(args.out_data))
                        / f"acquisition_plan_{args.scope}.json"
                    ),
                },
                indent=1,
            )
        )
        return 0
    if args.stage == "acquire":
        doc = stage_acquire(args)
        print(
            json.dumps(
                {
                    "blocker_version": doc["blocker_version"],
                    "acquisition_root": doc["acquisition_root"],
                    "admitted": len(doc["admitted"]),
                    "incomplete": doc["incomplete"],
                },
                indent=1,
            )
        )
        return 0 if not doc["incomplete"] else 1
    union = stage_verify(args)
    print(json.dumps(_union_summary(union), indent=1))
    # Exit reflects THIS run's day checks. A subset run whose day verified is a
    # success as a verification; all_scopes_ready above is what says whether any
    # scope has actually discharged its full obligation.
    return 0 if union["days_verified_ok"] else 1


def _union_summary(union: dict) -> dict:
    return {
        "verified_scope": union.get("verified_scope"),
        "is_subset_run": union.get("is_subset_run"),
        "days_verified_ok": union.get("days_verified_ok"),
        "verified_days": union.get("verified_days"),
        "all_scopes_ready": union["all_scopes_ready"],
        "blockers": {
            b: {
                "scope": e["scope"],
                "version": e["version"],
                "state": e["state"],
                "root": e["root_rel"],
                "admitted": e["admitted_day_count"],
                "required": e["required_day_count"],
                "cross_feed": (e.get("cross_feed_disagreements") or {}).get("count", 0),
            }
            for b, e in union["blockers"].items()
        },
        "missing_scopes": union["missing_scopes"],
        "stale_scopes": [x["scope"] for x in union["stale_scopes"]],
        "residual_gaps": sum(
            len(e["residual_confirmed_trading_gaps"]) for e in union["scopes"].values()
        ),
        "cross_feed_disagreements": union["cross_feed_disagreements"]["count"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
