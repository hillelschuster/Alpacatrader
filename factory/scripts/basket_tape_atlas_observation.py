#!/usr/bin/env python
"""Freeze-O Stage-0 Tape Atlas observation producer (contract v0).

Builds the frozen observation substrate ONLY: day registry, path/membership
identities, selected-path raw print projection, selected-path minute grid, the
broad raw/provider minute race board, checkpoint and candidate-net race boards,
the causal quote-roster table and a coverage/reconciliation table.

Hard rules enforced here:
  * every day passes ``basket_sim.guard_day``; the calendar comes from
    ``basket_sim.dev_days()`` - never from globbing a store;
  * observation tables physically exclude outcome / ticket-constant / future /
    censor families (``causal_registry.json``);
  * large payloads go to a gitignored absolute data root, never into git;
  * deterministic payloads must be byte-identical across reruns; run-varying
    costs live in ``costs.json`` only and never enter the manifest core hash.

Modes
-----
  --selftest                       pure-contract and pure-function self-tests
  --stage contract                 validate the frozen contracts + write the lock
  --stage canary [--out-data ABS] [--out-evidence REL] [--workers N] [--force]
  --stage verify-canary            independent re-verification of a built canary

Usage
-----
  .venv/bin/python factory/scripts/basket_tape_atlas_observation.py --selftest
  .venv/bin/python factory/scripts/basket_tape_atlas_observation.py --stage contract
  .venv/bin/python factory/scripts/basket_tape_atlas_observation.py --stage canary --workers 2
  .venv/bin/python factory/scripts/basket_tape_atlas_observation.py --stage verify-canary
"""

from __future__ import annotations

import argparse
import bisect
import contextlib
import hashlib
import json
import multiprocessing as mp
import os
import re
import resource
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

HERE = Path(__file__).resolve()
SCRIPTS = HERE.parent
ROOT = HERE.parents[2]
try:  # reuse the canonical guard/calendar source
    from factory.scripts import basket_sim as sim
    from factory.scripts import sip_bars as sbar
except ImportError:  # direct script execution
    sys.path.insert(0, str(ROOT))
    from factory.scripts import basket_sim as sim
    from factory.scripts import sip_bars as sbar


def _resolve_data_root() -> Path:
    """Market data lives outside the worktree (read-only, gitignored).

    Resolution order: BASKET_DATA_ROOT, ROOT/data (main checkout), the absolute
    main-project data root named by the task.
    """
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


DATA = _resolve_data_root()
TRACK = ROOT / "factory/artifacts/basket/phase2/ATLAS/TAPE/OBSERVATION/v0"
DEFAULT_OUT_DATA = Path(
    os.environ.get(
        "ATLAS_OBS_DATA_ROOT", "/home/hillel/projects/Alpacatrader/data/atlas/observation/v0"
    )
)
EVIDENCE_REL_DEFAULT = "factory/artifacts/basket/phase2/ATLAS/TAPE/OBSERVATION/v0/canary"

ET = ZoneInfo("America/New_York")
PANEL = ROOT / "factory/artifacts/basket/phase2/ATLAS/panel.parquet"
CAL_PATH = ROOT / "factory/artifacts/basket/sip/phase2_session_calendar.json"
ANAT_DIR = ROOT / "factory/artifacts/basket/sip/anatomy"
PIT_PATH = DATA / "pit/pit_symbols.parquet"
NET_TRADES = DATA / "sip/net/trades"
NET_BARS = DATA / "sip/net/bars"
NET_COVERAGE = DATA / "sip/net/coverage"
NET_QUOTES = DATA / "sip/net/quotes"
NET_MANIFEST_INDEX = DATA / "sip/net/manifest_index.jsonl"
UNIVERSE_RTH = DATA / "sip/universe/rth"
CANDIDATES = DATA / "sip/candidates"
# Acquisition (B1 raw 2025-02, B2 2026-04/05 missing names) produced OUTSIDE this repo under
# the market-data root. The producer owns the bytes; this producer only consumes verified
# admitted day files and never fabricates one.
#
# The root is EXPLICIT: v1 is the first admitted root. `v0` is a deliberately failed canary
# whose single fetched day honestly records status=incomplete; it is kept as evidence and is
# never a default target, and nothing here falls back to it. Even in v1 a day is admitted only
# when its own manifest says complete and re-hashes, and the acquisition blocker only resolves
# when the admission union says all_scopes_ready with this scope `ready`.
ACQ_DEFAULT_VERSION = "v1"
ACQ_ROOT = Path(
    os.environ.get("ATLAS_ACQ_ROOT", str(DATA / "atlas/acquisition" / ACQ_DEFAULT_VERSION))
)
ACQ_TRACK_REL = "acquisition"
ACQ_BARS = ACQ_ROOT / "bars"
ACQ_ROSTERS = ACQ_ROOT / "rosters"
# The JOINT scope evidence is the single authority for which acquisition root supplies each
# day. It replaces the single-root default: the per-blocker roots (B1 v4, B2 v3) are bound
# INSIDE this document, never by a filename convention or by the ambient ATLAS_ACQ_ROOT.
ACQ_JOINT_EVIDENCE = Path(
    os.environ.get(
        "ATLAS_ACQ_JOINT_EVIDENCE",
        str(TRACK / ACQ_TRACK_REL / "evidence" / "acquisition_admission.json"),
    )
)
# The admission reader's alias; the joint evidence above is the single authority.
ACQ_ADMISSION = ACQ_JOINT_EVIDENCE
# The days the net-index reconciliation targets. This is the reconciliation's SCOPE, never a
# hardcoded block: B6 coverage is emitted for whatever days MEASURE as unreconciled.
NET_RECONCILIATION_REL = f"{ACQ_TRACK_REL}/net_reconciliation.json"
NET_RECONCILIATION_SCHEMA = "atlas/net_reconciliation/v0"
B6_RECONCILE_TARGET_DAYS = ("2026-05-21", "2026-05-29")
# The EXPLICIT alias map is the only path by which a raw/provider spelling becomes a canonical
# PIT ticker. Nothing is ever fuzzy-matched: an unlisted spelling stays its own name.
ACQ_ALIAS_MAP = TRACK / ACQ_TRACK_REL / "alias_map.json"

CONTRACT_VERSION = "freeze-o-v0"
PRODUCER_VERSION = "tape-atlas-observation-v0"
SEED_DAY = "2021-01-29"  # previous-close seed only
SEED_MONTH_FILE = "ohlcv_2021-01.parquet"
PROJECTION_LO, PROJECTION_HI = 565, 965  # 09:25-16:05 ET
RTH_LO = 570
CHECKPOINTS = tuple(sim.CHECKPOINTS) if hasattr(sim, "CHECKPOINTS") else ()
B_CLOCKS = (575, 580, 585, 590, 595, 600, 615, 630, 645, 660, 690, 720)
ROSTER_RULE_ID = "quote_roster_v0_top10_B_union_top3_A"
B_TOP_K = 10
A_TOP_K = 3
SOFT_RSS_GIB = 15.0  # resource policy: autosize workers under this budget
HARD_RSS_GIB = 30.0  # resource policy: hard abort above this per-process peak
# CANARY-STAGE PROVENANCE, deliberately NOT taken from OOM victims. An earlier revision of this
# comment set the child envelope from the 7.58/9.05/10.35 GiB kernel-IDT figures; that was wrong.
# Those were the largest resident holders AT THE INSTANT of a VM-wide exhaustion (RAM + swap
# fully consumed, one victim already 2.03 GiB swapped out) with 4-6 concurrent heavy readers
# running. A resident set sampled under global thrash is a snapshot of FAILURE, not a per-day
# steady state, and sizing from it authorises the very configuration that caused the OOM.
# The only directly comparable measurement for this script at this stage is the canary's own
# per-day peak of 4.36-5.00 GiB, which is what the policy envelope is calibrated on.
MEASURED_CHILD_RSS_GIB = 5.0  # canary-stage per-day peak, upper bound (NOT a full-stage figure)
PARENT_PLUS_CHILD_CEILING_GIB = 8.0  # policy: child <= 6 GiB, parent+child <= 8 GiB
PREFLIGHT_MEMAVAILABLE_GIB = 6.0  # refuse to START below this; never intervene mid-run
# NOT MEASURED: the dense/full-stage per-day peak. A full-stage day that exceeds this envelope is
# a legitimate preflight REFUSAL, not a defect, and is exactly what prevents an OOM repeat. The
# envelope must be re-derived from a fresh dense-stage measurement, never from a failure snapshot.
# Disk reserve. Planning headroom is 70-80 GiB (corpus projection 45-50 GiB plus
# same-directory atomic-write transients); the frozen gate value sits mid-band. Measured
# capacity readings never live in constants or in the frozen contracts - every run records
# the readings it actually took in costs.json only, where they cannot be mistaken for a
# design claim (`docs`/contracts carry the planning numbers, never a capacity reading).
PLANNING_HEADROOM_LO_BYTES = 70 * 2**30
PLANNING_HEADROOM_HI_BYTES = 80 * 2**30
# The INITIAL budget stays the reviewed conservative 75 GiB (planning band 70-80 GiB): it is
# the demand of a corpus that does not exist yet, so nothing but VERIFIED completed payload
# bytes may reduce it (see `verified_completed_payload_bytes`).
FULL_GATE_FREE_BYTES = 75 * 2**30
# A RESUMED full build subtracts its verified completed payload bytes from the estimated
# REMAINING-space demand and still keeps a 10 GiB operating reserve on the backing volume.
# The physical mount check is unchanged: an unbacked virtual/unknown local disk measures 0
# and refuses.
FULL_CORPUS_DEMAND_HI_BYTES = 50 * 2**30  # contract full_corpus_gib_high
OPERATING_RESERVE_BYTES = 10 * 2**30
BLOCKERS_RESOLVED_NAME = "blockers_resolved.json"
# Gate classes. The quote channel is a versioned SIBLING: its blocker (B3_quote_lane)
# never serially blocks the core bar/print/race corpus, and the core gate never
# admits a prospective quote feature. Both gates refuse without the tracked
# resolved-state evidence; no flag bypasses either one.
BLOCKER_CLASS_CORE = "core"
BLOCKER_CLASS_QUOTE = "quote"
BLOCKER_CLASS_CANARY = "canary"
BLOCKER_CLASSES = (BLOCKER_CLASS_CORE, BLOCKER_CLASS_QUOTE, BLOCKER_CLASS_CANARY)
CORE_READINESS_STATE = "core_full_ready"
QUOTE_READINESS_STATE = "quote_channel_ready"
# Quote-channel features: the quote VALUE names a sibling would materialize. The deny is
# registry-derived (family `prospective_quote_channel`) and the `quote_` prefix is only the
# frame-level fast path. Roster provenance/counts - membership, source path/sha, row counts,
# sibling-gate state - are core bookkeeping about the roster ARTIFACT, never quote alpha, and
# stay legal under the prefix when the frozen schema declares them as such in the core roster
# table (see `is_quote_feature`).
QUOTE_CHANNEL_FEATURE_PREFIX = "quote_"
QUOTE_FEATURE_FAMILY = "prospective_quote_channel"
QUOTE_ROSTER_TABLE = "quote_rosters"
QUOTE_PROVENANCE_NAMESPACES = frozenset(
    {"key", "roster", "provenance", "coverage", "audit_coordinate"}
)
QUOTE_FEATURE_FALLBACK = (
    "quote_mid",
    "quote_bid",
    "quote_ask",
    "quote_size",
    "quote_spread",
    "quote_imbalance",
    "quote_nbbo",
    "quote_condition",
    "quote_minute_aggregate",
    "quote_vwap",
    "quote_depth",
    "quote_ts",
)
# Disk gates probe the ACTUAL output mount. When that mount is a WSL2 virtual disk (ext4 in a
# dynamically expanding VHDX) the guest free bytes overstate what the Windows host volume can
# physically back, so the full gate also requires the host volume + its free bytes declared
# with evidence in the tracked resolved-state manifest. The observed layout: data/atlas
# resolves onto Windows C: through 9p/drvfs (its reading IS the host volume), while the
# ext4 root on a WSL VHDX reports virtual capacity (895 GiB guest, ~112 GiB host C: free) and
# must therefore stack to a declared backing-host reading.
CANARY_MIN_FREE_BYTES = 2 * 2**30
VIRTUAL_DISK_MARKERS = ("virtual disk", "msft virtual disk")
# Foreign/WSL-interop mounts report the backing host volume's free space directly; local block
# filesystems report their own (possibly virtual) capacity.
FOREIGN_MOUNT_FSTYPES = frozenset(
    {"9p", "drvfs", "cifs", "smb3", "nfs", "nfs4", "fuse", "fuse.rclone", "virtiofs"}
)
LOCAL_BLOCK_FSTYPES = frozenset(
    {"ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "vfat", "exfat", "ntfs", "ntfs3"}
)
# The tracked resolved-state file carries the physical backing-host reading when (and only
# when) the output mount sits on a virtual/unknown local block disk.
HOST_VOLUME_DECLARATION_KEY = "storage_host_volume"
HOST_VOLUME_DECLARATION_FIELDS = (
    "volume",
    "free_bytes",
    "measured_at",
    "method",
    "evidence_sha256",
)

RUN_VARYING_STAT_KEYS = frozenset(
    {
        "elapsed_s",
        "prints_seconds",
        "grid_seconds",
        "board_seconds",
        "checkpoint_seconds",
        "candidate_seconds",
        "quote_seconds",
        "peak_rss_gb",
    }
)
SUSPECT_GAIN_HI = 50.0
SUSPECT_GAIN_LO = -0.95
ENVELOPE_LO, ENVELOPE_HI = 0.5, 2.0
PREVCLOSE_HARD_WARN = 0.10

# Observation column allowlist per table (physically enforced on write).
# The prospective quote sibling is kept out of the core corpus through `is_quote_feature`:
# the registry-derived value/absence vocabulary plus the `quote_` prefix fast path, with a
# declared roster-provenance exception. A bare prefix guard cannot be used here because
# quote roster provenance/counts (membership, source path/sha, row counts, sibling-gate
# state) are core bookkeeping about the roster ARTIFACT, never quote alpha.
FORBIDDEN_PREFIXES = (
    "outcome_",
    "future_",
    "v_",
    "giveback_",
    "tail_class_",
    "session_peak_",
    "session_close_",
)
# Full registry-derived deny list: the panel's own outcome/future registries plus
# the named outcome columns, so nothing depends on a prefix accident.
PANEL_COVERAGE_REGISTRY = ROOT / "factory/artifacts/basket/phase2/ATLAS/coverage.json"
_FALLBACK_DENIED = [
    "next_open",
    "next_et",
    "v_hold_flat",
    "v_forced_flat",
    "v_giveback_5",
    "v_giveback_10",
    "v_giveback_15",
    "v_giveback_20",
    "giveback_fired_5",
    "giveback_fired_10",
    "giveback_fired_15",
    "giveback_fired_20",
    "giveback_condition_after_forced_flat_5",
    "giveback_condition_after_forced_flat_10",
    "giveback_condition_after_forced_flat_15",
    "giveback_condition_after_forced_flat_20",
    "v_sell",
    "level_ret",
    "final_high_flag",
    "remaining_run",
    "cost_of_waiting",
    "bars_to_next_high",
    "dd_before_next_high",
    "bars_to_peak",
    "peak_et_after_t",
    "tail_class_50",
    "tail_class_100",
    "tail_class_300",
    "session_peak_et",
    "session_peak_ret_from_entry",
    "session_peak_bars_from_entry",
    "session_close_ret_from_entry",
    "future_member_last_et",
    "future_forced_flat_px",
    "path_complete_to_session_end",
    "terminal_censored",
    "mfe_after_t",
    "mae_after_t",
]


def denied_column_names() -> frozenset:
    """Blocked panel families/columns, derived from the panel registry at run time."""
    names = set(_FALLBACK_DENIED)
    try:
        reg = load_json(PANEL_COVERAGE_REGISTRY)
        names |= set(reg.get("outcome_columns") or [])
        names |= set(reg.get("future_only_columns") or [])
    except Exception:  # registry unavailable: the declared fallback list still applies
        pass
    return frozenset(names)


FORBIDDEN_EXACT = denied_column_names()
# `terminal_censored` / `path_complete_to_session_end` are retro-only censor columns:
# they are allowed ONLY inside tables/columns declared `censor` + retrospective_only in
# schema.json (paths.coverage_class.terminal_censored, grid.terminal_censored).

RAW_COLS = ["timestamp", "ticker", "open", "high", "low", "close", "volume"]
Layers = (
    "day_registry",
    "paths",
    "memberships",
    "selected_path_prints",
    "selected_path_grid",
    "race_minute_full",
    "race_checkpoint_full",
    "race_candidate_net",
    "quote_rosters",
    "coverage",
)


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_bytes(path, (json.dumps(obj, indent=1, sort_keys=True) + "\n").encode())


def atomic_write_parquet(df: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.write_parquet(tmp)
    os.replace(tmp, path)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def etm_expr(col: str = "ts_et") -> pl.Expr:
    return pl.col(col).dt.hour().cast(pl.Int32) * 60 + pl.col(col).dt.minute().cast(pl.Int32)


def r2(x: float | None, digits: int = 6) -> float | None:
    return None if x is None else round(float(x), digits)


def detect_memory_limit_gib() -> float:
    """Smallest of MemTotal and any cgroup limit (the real host/container ceiling)."""
    total = None
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) * 1024
                    break
    except OSError:
        total = None
    for probe in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            v = int(Path(probe).read_text().strip())
        except (OSError, ValueError):
            continue
        if 0 < v < 2**60:
            total = v if total is None else min(total, v)
    return (total or 16 * 2**30) / 2**30


def rss_policy() -> tuple[float, float]:
    """(soft, hard) RSS limits.

    The 0.70/0.9 host fractions are RETAINED ONLY as an upper clamp on the user ceilings. They
    are NOT the budget: measured reality showed a single child reaching 10.35 GiB anon while the
    derived soft budget implied ~5 GiB, and there is no cgroup cap or systemd-oomd on this host,
    so the kernel global OOM was the only enforcement. The real bounds now come from
    MEASURED_CHILD_RSS_GIB and PARENT_PLUS_CHILD_CEILING_GIB.
    """
    limit = detect_memory_limit_gib()
    return min(SOFT_RSS_GIB, 0.7 * limit), min(HARD_RSS_GIB, 0.9 * limit)


def memavailable_gib() -> float:
    """Currently available system memory, or -1.0 when it cannot be read."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        return -1.0
    return -1.0


def peak_rss_gb() -> float:
    """LIFETIME peak RSS in GiB (ru_maxrss is KiB; 1 GiB = 2**20 KiB).

    The previous /1e6 reported a DECIMAL GB against a GiB label, over-reporting by +4.86% at every
    magnitude. The exact conversion makes every threshold reading this trip ~4.86% LATER and the
    parent subtraction smaller, so it is LESS conservative, not more - the safety margin in
    `resource_budget` absorbs the estimation error. Recorded peak values in costs.json/stats shift
    by that same ~4.86% across this change and must not be differenced across the boundary.
    """
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20


def parent_rss_gib() -> float:
    """The parent's CURRENT resident set in GiB, from VmRSS.

    A MEASUREMENT change, not a refactor: the policy term is current residency, and ru_maxrss is a
    LIFETIME high-water mark. A parent that peaked at 1.6 GiB and now sits at 0.3 GiB must use
    0.3, or the cap shrinks for a parent nobody sees at that size in ps. `peak_rss_gib()` is kept
    as a reported statistic only.
    """
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / (1024 * 1024)  # kB -> GiB, exact
    except (OSError, ValueError, IndexError):
        pass
    # fallback: the lifetime peak, if /proc is unreadable
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20


SAFETY_MARGIN_GIB = 2.0  # global headroom held back so estimation error cannot cause an OOM


def resource_budget() -> dict:
    """THE single memory calculation, shared by the start gate and the worker sizing.

    Two INDEPENDENT bounds, and the parent is counted exactly ONCE in each:
      policy_headroom = PARENT_PLUS_CHILD_CEILING_GIB - parent_rss
          the parent appears here because the envelope is defined over parent+child, which is a
          DIFFERENT quantity from MemAvailable;
      global_headroom = MemAvailable - SAFETY_MARGIN_GIB
          MemAvailable already excludes the resident parent (and every other resident process),
          so subtracting the parent here as well would double-count it.

    cap = floor(min(both) / MEASURED_CHILD_RSS_GIB) and may be 0. A cap of 0 REFUSES: under the
    current 8 GiB envelope it can never exceed 1, because policy headroom is bounded by 8.0. That
    is policy construction, not an accident - raising parallelism needs a larger envelope or a
    smaller MEASURED child, not more free memory.

    Unreadable or unknown MemAvailable REFUSES. A start gate that passes when it cannot measure
    what it exists to measure is not fail-closed.
    """
    parent = parent_rss_gib()
    avail = memavailable_gib()
    known = avail >= 0.0
    policy = max(0.0, PARENT_PLUS_CHILD_CEILING_GIB - parent)
    global_head = max(0.0, avail - SAFETY_MARGIN_GIB) if known else 0.0
    usable = min(policy, global_head) if known else 0.0
    cap = int(usable // MEASURED_CHILD_RSS_GIB)
    reason = None
    if not known:
        reason = (
            f"MemAvailable is unreadable (reported {avail}); a start gate that cannot measure the "
            "system it exists to protect must refuse"
        )
    elif cap == 0:
        reason = (
            f"no worker fits: policy headroom {policy:.2f} GiB, global headroom {global_head:.2f} "
            f"GiB (MemAvailable {avail:.2f} - {SAFETY_MARGIN_GIB:.1f} safety), child envelope "
            f"{MEASURED_CHILD_RSS_GIB:.1f} GiB"
        )
    return {
        "memavailable_gib": round(avail, 2) if known else None,
        "parent_rss_gib": round(parent, 2),
        "policy_headroom_gib": round(policy, 2),
        "global_headroom_gib": round(global_head, 2) if known else None,
        "safety_margin_gib": SAFETY_MARGIN_GIB,
        "child_envelope_gib": MEASURED_CHILD_RSS_GIB,
        "parent_plus_child_ceiling_gib": PARENT_PLUS_CHILD_CEILING_GIB,
        "cap": cap,
        "ok": known and cap >= 1,
        "reason": reason,
    }


def resource_preflight() -> dict:
    """Fail-closed memory preflight, read BEFORE any worker is spawned.

    There is no cgroup cap and no systemd-oomd on this host, so the kernel global OOM is the only
    other enforcement - and a global OOM kills the LARGEST holder, which is the very child we are
    about to spawn. Refusing to start is the only cooperative option. Refusal belongs HERE, at the
    start: a completed corpus is never discarded (see the source-generation guard, which labels
    rather than aborts).
    """
    b = resource_budget()
    return {
        "memavailable_gib": b["memavailable_gib"],
        "parent_rss_gib": b["parent_rss_gib"],
        "policy_headroom_gib": b["policy_headroom_gib"],
        "global_headroom_gib": b["global_headroom_gib"],
        "safety_margin_gib": b["safety_margin_gib"],
        "required_memavailable_gib": PREFLIGHT_MEMAVAILABLE_GIB,
        "child_envelope_gib": b["child_envelope_gib"],
        "parent_plus_child_ceiling_gib": b["parent_plus_child_ceiling_gib"],
        "cap": b["cap"],
        "ok": b["ok"],
        "reason": b["reason"],
    }


def effective_workers(requested: int) -> int:
    """Resource policy: start at most as many workers as the SHARED budget allows.

    Sizing is `min(policy_headroom, global_headroom) / child_envelope` - the same calculation the
    start gate uses, so the two cannot disagree. The 0.70 x host fraction is NOT the budget: it
    implied a child envelope the child never respected, and the kernel global OOM was the only
    enforcement. Under the current 8 GiB envelope this can never exceed 1 - 1 worker is POLICY,
    not coincidence, and more parallelism requires a larger envelope or a smaller measured child.

    Returns 0 to mean REFUSE; the caller must not treat that as at-least-one.
    """
    b = resource_budget()
    cap = int(b["cap"])
    if cap <= 0:
        print(f"[resource] refusing to start: {b['reason']}", flush=True)
        return 0
    if requested > cap:
        print(
            f"[resource] requested {requested} workers but the shared budget allows {cap} "
            f"(policy headroom {b['policy_headroom_gib']:.2f} GiB, global headroom "
            f"{b['global_headroom_gib']:.2f} GiB, child envelope "
            f"{MEASURED_CHILD_RSS_GIB:.1f} GiB); "
            f"using {cap}",
            flush=True,
        )
        return cap
    return max(1, requested)


def check_rss_policy(peak_gb: float, where: str) -> None:
    """POST-HOC report of a completed day's peak - NOT OOM protection.

    This runs after the day's layers are written, so it cannot bound anything that is still
    running; on a host with no cgroup cap the kernel global OOM can arrive long before this
    threshold is measured. Only `resource_budget()` (start-time) and the worker cap bound
    anything. Keep it for the measured facts it records, and do not read it as a guard.
    """
    soft, hard = rss_policy()
    if peak_gb > hard:
        raise RuntimeError(
            f"{where}: measured peak RSS {peak_gb:.2f} GiB exceeds the hard {hard:.2f} GiB "
            f"resource policy (soft {soft:.2f} GiB); aborting with measured facts"
        )


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# frozen contracts
# --------------------------------------------------------------------------- #


def load_contracts(track: Path = TRACK) -> dict:
    out = {}
    for name in (
        "contract.json",
        "schema.json",
        "causal_registry.json",
        "canary_days.json",
        "adaptive_choice_ledger.json",
    ):
        out[name] = load_json(track / name)
    return out


def contract_hashes(track: Path = TRACK) -> dict:
    return {
        name: sha256_file(track / name)
        for name in (
            "contract.json",
            "schema.json",
            "causal_registry.json",
            "canary_days.json",
            "adaptive_choice_ledger.json",
        )
    }


def blockers_by_class(contract: dict, blocker_class: str) -> list[str]:
    """Blocker ids of one gate class ('core' | 'quote' | 'canary') in frozen order."""
    return [
        b["blocker_id"]
        for b in contract["full_build_blockers"]
        if b.get("blocker_class") == blocker_class
    ]


def resolved_state_path(path: Path | str | None = None) -> Path:
    """The tracked resolved-state declaration; any other path is refused (no bypass)."""
    tracked = TRACK / BLOCKERS_RESOLVED_NAME
    if path is None:
        return tracked
    candidate = Path(path)
    if candidate.resolve() != tracked.resolve():
        raise SystemExit(
            "resolved-state refusal: only the tracked declaration "
            f"{tracked} is accepted (no bypass); got {candidate}"
        )
    return tracked


def load_resolved_state(path: Path | str | None = None) -> dict:
    """The `resolved` mapping of the tracked resolved-state declaration ({} if absent)."""
    p = resolved_state_path(path)
    doc = load_json(p) if p.exists() else {}
    declared = doc.get("resolved", {}) if isinstance(doc, dict) else {}
    return declared if isinstance(declared, dict) else {}


def _same_file(a: Path, b: Path) -> bool | None:
    """True/False when both paths resolve to the same file; None when either cannot resolve."""
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return None


def _evidence_path_of(record: dict) -> tuple[Path | None, str | None]:
    """Resolve a declared EVIDENCE path, and require it to live under the tracked evidence root.

    This is a real containment check, not a comment. A declared evidence artifact must be a
    TRACKED file under OBSERVATION TRACK; a path pointing anywhere else - the repo root, a data
    root, an arbitrary JSON, /tmp - is refused. This governs DECLARED EVIDENCE artifacts only; it
    does not constrain the raw source roots the evidence itself points at (the acquisition roots
    live outside the repo by design).
    """
    raw = record.get("evidence_path")
    if not raw:
        return None, "no evidence_path declared"
    p = Path(str(raw))
    p = p if p.is_absolute() else ROOT / p
    try:
        resolved = p.resolve()
        inside = resolved.is_relative_to(TRACK.resolve())
    except OSError:
        return None, f"evidence_path cannot be resolved: {p}"
    if not inside:
        return None, (
            f"evidence_path {resolved} is outside the tracked evidence root {TRACK.resolve()}; a "
            "declared evidence artifact must live under it"
        )
    return resolved, None


def blocker_evidence_state(blocker_id: str, declared: dict) -> dict:
    """Verify a declared resolution: the evidence FILE must exist, re-hash to the declared
    sha256, and its CONTENTS must actually attest this blocker.

    A nonempty sha string alone resolves nothing. B1/B2 additionally require the acquisition
    admission evidence to exist and to carry this blocker's admitted days with no unresolved
    residual confirmed-trading gap; B6 requires the net reconciliation to verify at least one
    day end to end; B5 requires a physical-disk evidence file that is not the acquisition
    evidence and carries a measured free-bytes reading. B3 only needs its own evidence file.
    """
    rec = declared.get(blocker_id) or {}
    out: dict[str, Any] = {
        "blocker_id": blocker_id,
        "declared": bool(rec),
        "evidence_path": rec.get("evidence_path"),
        "declared_sha256": rec.get("evidence_sha256"),
        "verified": False,
        "problems": [],
    }
    problems: list[str] = out["problems"]
    if not rec:
        problems.append("not declared in the resolved-state manifest")
        return out
    p, why_path = _evidence_path_of(rec)
    if p is None:
        problems.append(why_path or "no usable evidence_path")
        return out
    if not p.exists():
        problems.append(f"evidence file absent: {p}")
        return out
    got = sha256_file(p)
    out["actual_sha256"] = got
    if not rec.get("evidence_sha256"):
        problems.append("no evidence_sha256 declared")
    elif got != rec.get("evidence_sha256"):
        problems.append(f"evidence file sha256 {got} != declared {rec.get('evidence_sha256')}")
    if problems:
        return out
    # content attestation ------------------------------------------------------- #
    if blocker_id in ("B1_raw_2025_02", "B2_raw_roster_2026_04_05"):
        # The file we HASHED must BE the file we CONSULT. Otherwise an unrelated JSON under
        # TRACK whose sha happens to match the declaration would nominally attest the
        # acquisition, while the verdict was really derived from the joint evidence.
        authoritative = _same_file(p, ACQ_JOINT_EVIDENCE)
        if authoritative is not True:
            problems.append(
                f"declared evidence {p} is not the acquisition admission evidence "
                f"{ACQ_JOINT_EVIDENCE}; refusing to attest this blocker from another file"
            )
            return out
        adm = acquisition_admission(blocker_id)
        if not adm["present"]:
            problems.append(f"acquisition admission evidence absent: {adm['path']}")
        elif not adm["ready"]:
            problems.extend(adm["problems"])
        else:
            # `acquisition_admission` already refused a subset / non-full-scope / stale /
            # mismatched union by recomputing the exact day set from the guarded dev calendar.
            # What remains is this consumer's OWN re-hash of every admitted day: a payload or
            # manifest that no longer matches closes the blocker here even if the union once
            # admitted it.
            unverified = [d for d in adm["admitted_days"] if acquisition_day(d) is None]
            if unverified:
                problems.append(f"admitted days do not verify locally: {unverified[:5]}")
            elif adm["residual_gaps"]:
                problems.append(
                    f"residual confirmed-trading gaps remain: {adm['residual_gaps'][:5]}"
                )
    elif blocker_id == "B6_unreconciled_net_manifest":
        recon_path = TRACK / NET_RECONCILIATION_REL
        authoritative = _same_file(p, recon_path)
        if authoritative is False:
            problems.append(
                f"declared evidence {p} is not the net reconciliation evidence {recon_path}; "
                "refusing to attest this blocker from another file"
            )
            return out
        recon = net_reconciliation()
        if not recon["present"]:
            problems.append(f"net reconciliation evidence absent: {recon['path']}")
        elif not recon["days"]:
            problems.append("net reconciliation verifies no day: " + "; ".join(recon["problems"]))
    elif blocker_id == "B5_storage_headroom":
        try:
            doc = load_json(p)
        except Exception as exc:  # noqa: BLE001 - reported as a resolution problem
            doc = {}
            problems.append(f"storage evidence is not readable JSON: {exc}")
        if p == ACQ_ADMISSION:
            problems.append(
                "storage evidence must be physical-disk evidence, not the acquisition file"
            )
        free = doc.get("free_bytes") if isinstance(doc, dict) else None
        if not isinstance(free, int) or isinstance(free, bool) or free <= 0:
            problems.append("storage evidence declares no positive measured free_bytes")
    out["verified"] = not problems
    return out


def unresolved_blockers(blocker_ids: list[str], declared: dict) -> list[str]:
    """Blockers whose resolution is NOT verified by an existing, re-hashed evidence file."""
    return [b for b in blocker_ids if not blocker_evidence_state(b, declared)["verified"]]


def quote_feature_vocabulary(contracts: dict | None = None) -> frozenset[str]:
    """Registry-derived vocabulary that would materialize the prospective quote channel.

    Quote values, spreads, aggregates, depths and quote-absence flags are FEATURES of the
    sibling channel. Roster provenance never appears here.
    """
    vocab = set(QUOTE_FEATURE_FALLBACK)
    if contracts is not None:
        for fam in (contracts.get("causal_registry.json") or {}).get("blocked_families", []):
            if fam.get("family") == QUOTE_FEATURE_FAMILY:
                vocab |= {e for e in fam.get("examples") or [] if isinstance(e, str)}
    return frozenset(vocab)


def declared_quote_provenance_fields(contracts: dict) -> frozenset[str]:
    """Schema fields the frozen contract itself declares as quote-ROSTER provenance.

    A `quote_` name is legal only when schema.json declares it with a provenance namespace
    (key/roster/provenance/coverage/audit_coordinate): membership, source path/sha, row
    counts and sibling-gate state describe the roster ARTIFACT, never quote alpha. No prefix
    guard may reject provenance the frozen schema already declares.
    """
    return frozenset(
        f
        for table in contracts["schema.json"]["tables"].values()
        for f, attrs in table["fields"].items()
        if f.startswith(QUOTE_CHANNEL_FEATURE_PREFIX)
        and attrs.get("namespace") in QUOTE_PROVENANCE_NAMESPACES
    )


_QUOTE_GUARD_CACHE: dict = {}


def _quote_guard_sets() -> tuple[frozenset[str], frozenset[str]]:
    """(vocabulary, declared roster provenance) from the frozen track, cached by file state."""
    paths = (TRACK / "causal_registry.json", TRACK / "schema.json")
    key: Any = None
    try:
        key = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths)
    except OSError:
        key = None
    if key is None or _QUOTE_GUARD_CACHE.get("key") != key:
        _QUOTE_GUARD_CACHE["key"] = key
        _QUOTE_GUARD_CACHE["sets"] = (
            quote_feature_vocabulary({p.name: load_json(p) for p in paths}),
            declared_quote_provenance_fields({p.name: load_json(p) for p in paths}),
        )
    return _QUOTE_GUARD_CACHE["sets"]


def is_quote_feature(name: str, contracts: dict | None = None) -> bool:
    """True only for a name that would materialize the prospective quote CHANNEL.

    The registry value/absence vocabulary is denied outright (a quote VALUE or a future-
    selected quote ABSENCE is never a core feature); the `quote_` prefix is the frame-level
    fast path, but it never rejects a roster provenance field the frozen schema declares.
    Without `contracts` the frozen track's registry/schema are read (cached by file state).
    """
    vocab, provenance = (
        _quote_guard_sets()
        if contracts is None
        else (quote_feature_vocabulary(contracts), declared_quote_provenance_fields(contracts))
    )
    if name in vocab:
        return True
    if not name.startswith(QUOTE_CHANNEL_FEATURE_PREFIX):
        return False
    return name not in provenance


def quote_feature_fields(contracts: dict) -> list[str]:
    """Schema fields that would materialize the prospective quote channel (must stay empty)."""
    return [
        f"{t}.{f}"
        for t, table in contracts["schema.json"]["tables"].items()
        for f in table["fields"]
        if is_quote_feature(f, contracts)
    ]


def _unescape_mount_field(field: str) -> str:
    return field.replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")


def _proc_mount_rows(path: str = "/proc/mounts") -> list[tuple[str, str, str]]:
    """(mount_point, source, fstype) rows with octal escapes decoded ([] when unreadable)."""
    rows: list[tuple[str, str, str]] = []
    try:
        text = Path(path).read_text()
    except OSError:
        return rows
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        rows.append(
            (
                _unescape_mount_field(parts[1]),
                _unescape_mount_field(parts[0]),
                parts[2],
            )
        )
    return rows


def mount_for_path(path: Path) -> tuple[str, str, str]:
    """Longest-prefix mount row for an absolute path (fallback: the root row)."""
    target = str(path)
    best: tuple[str, str, str] = ("/", "unknown", "unknown")
    best_len = -1
    for mount_point, source, fstype in _proc_mount_rows():
        prefix = mount_point.rstrip("/")
        if target != prefix and not target.startswith(prefix + "/"):
            continue
        length = len(prefix) or 1  # the root mount point has an empty prefix
        if length > best_len:
            best, best_len = (mount_point or "/", source, fstype), length
    return best


def _block_device_name(device: str | None) -> str | None:
    """Whole-disk name for a device or partition path (`/dev/sdd3` -> `sdd`)."""
    if not device:
        return None
    for candidate in (device, re.sub(r"\d+$", "", device).rstrip("p")):
        if candidate and (Path("/sys/block") / candidate).exists():
            return candidate
    return None


def _device_model_text(base: str | None) -> str | None:
    if base is None:
        return None
    blobs = []
    for rel in ("device/model", "device/vendor"):
        try:
            text = (Path("/sys/block") / base / rel).read_text().strip()
        except OSError:
            continue
        if text:
            blobs.append(text)
    return " ".join(blobs) or None


def local_block_is_virtual(device: str | None) -> bool | None:
    """True/False from the backing disk model; None when the model is unreadable.

    None counts as virtual for the gate: on WSL2 the ext4 root lives on a dynamically
    expanding VHDX ("Virtual Disk" / "Msft Virtual Disk") whose guest free space can far
    exceed what the host volume can physically back, so an unidentifiable local disk may
    not supply the conservative reading either.
    """
    model = _device_model_text(_block_device_name(device))
    if model is None:
        return None
    low = model.lower()
    return any(marker in low for marker in VIRTUAL_DISK_MARKERS)


def output_mount_facts(out_data: Path | str) -> dict:
    """Measured mount facts for the output path's filesystem (readings only, never claims).

    The symlinked data root is resolved first (data/atlas lands on Windows C: through 9p),
    and the mount backing decides which free number is real: a foreign/interop mount reports
    its host volume directly; a local block filesystem reports its own, possibly virtual,
    capacity.
    """
    probe = Path(out_data)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    probe = probe.resolve()
    try:
        usage = shutil.disk_usage(str(probe))
    except OSError as exc:
        raise SystemExit(
            f"storage probe refusal: cannot measure free space at {probe}: {exc}"
        ) from exc
    mount_point, source, fstype = mount_for_path(probe)
    device = Path(source).name if source.startswith("/dev/") else None
    local_block = fstype in LOCAL_BLOCK_FSTYPES
    base = _block_device_name(device) if local_block else None
    return {
        "output_root": str(out_data),
        "measured_at": str(probe),
        "mount_point": mount_point,
        "mount_source": source,
        "mount_fstype": fstype,
        "device": device,
        "device_model": _device_model_text(base),
        "local_block_device": local_block,
        "guest_free_bytes": int(usage.free),
        "guest_total_bytes": int(usage.total),
        "virtual_disk": (local_block_is_virtual(device) if local_block else None),
    }


def load_host_volume_declaration(path: Path | str | None = None) -> dict:
    """Declared backing-host-volume reading from the tracked resolved-state file."""
    p = resolved_state_path(path)
    doc = load_json(p) if p.exists() else {}
    decl = doc.get(HOST_VOLUME_DECLARATION_KEY) if isinstance(doc, dict) else {}
    return decl if isinstance(decl, dict) else {}


def declared_host_free_bytes(declaration: dict) -> int | None:
    """The declared host free bytes, only when the declaration is complete (else None)."""
    if not declaration:
        return None
    missing = [f for f in HOST_VOLUME_DECLARATION_FIELDS if not declaration.get(f)]
    value = declaration.get("free_bytes")
    if missing or not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return None
    return int(value)


def conservative_free_bytes(facts: dict, declaration: dict) -> tuple[int, str, list[str]]:
    """The free bytes the disk gate may spend: never more than the backing volume holds.

    Returns (bytes, source, reasons). A missing physical reading returns 0 with the exact
    declaration the operator must record, so the gate refuses instead of trusting a guest
    number it cannot back.
    """
    guest = int(facts["guest_free_bytes"])
    if facts["mount_fstype"] in FOREIGN_MOUNT_FSTYPES:
        return (
            guest,
            f"backing host volume reported directly through {facts['mount_fstype']} "
            f"({facts['mount_source']} on {facts['mount_point']})",
            [],
        )
    if facts.get("virtual_disk") is False:
        return guest, f"physical disk {facts.get('device')} ({facts.get('device_model')!r})", []
    host_free = declared_host_free_bytes(declaration)
    if host_free is None:
        return (
            0,
            "unmeasured: local block device with a virtual or unidentifiable backing disk, "
            "so no conservative physical reading exists",
            [
                f"guest free {guest} cannot be trusted for device {facts.get('device')} "
                f"(model {facts.get('device_model')!r}, virtual={facts.get('virtual_disk')}); "
                f"the tracked resolved-state file must declare '{HOST_VOLUME_DECLARATION_KEY}' "
                "with fields " + ", ".join(HOST_VOLUME_DECLARATION_FIELDS) + " (free_bytes = "
                "physically free bytes on that host volume)"
            ],
        )
    return (
        min(guest, host_free),
        f"min(guest {guest}, declared host volume {declaration.get('volume')} free {host_free})",
        [],
    )


def verified_completed_payload_bytes(
    out_data: Path | str, manifest: dict | None, progress: dict | None = None
) -> dict:
    """Payload bytes already on disk whose recorded sha256 re-verifies RIGHT NOW.

    Two sources, both re-hashed: the completed manifest's `payload_sha256`, and - when a run
    crashed part-way and no manifest exists yet - the incremental `_progress.json`, which
    records each completed day's layer paths and shas. Without the progress fallback a mid-run
    crash leaves `verified_bytes` at 0 and re-arms the full 75 GiB budget even though tens of
    GiB of already-written, already-verified payloads sit on disk.

    A payload that is absent, or whose bytes no longer match, counts for NOTHING: unverified
    credit is never given. Returns the accounting so costs.json can show the work.
    """
    root = Path(out_data)
    entries: dict[str, dict] = {}
    for key, info in ((manifest or {}).get("payload_sha256") or {}).items():
        if info.get("path") and info.get("sha256"):
            entries[key] = {"path": info["path"], "sha256": info["sha256"]}
    progress_days: list[str] = []
    for day, rec in ((progress or {}).get("days") or {}).items():
        for layer, info in ((rec or {}).get("layers") or {}).items():
            if info.get("path") and info.get("sha256"):
                entries[f"{layer}/{day}"] = {"path": info["path"], "sha256": info["sha256"]}
                progress_days.append(day)
    verified: list[str] = []
    mismatched: list[str] = []
    absent: list[str] = []
    total = 0
    for key, info in entries.items():
        p = root / info["path"]
        if not p.exists():
            absent.append(key)
            continue
        if content_sha(p) != info["sha256"]:
            mismatched.append(key)
            continue
        verified.append(key)
        total += p.stat().st_size
    return {
        "verified_bytes": total,
        "n_verified": len(verified),
        "progress_days": sorted(set(progress_days)),
        "from_manifest": bool((manifest or {}).get("payload_sha256")),
        "mismatched": sorted(mismatched),
        "absent": sorted(absent),
    }


def full_demand_bytes(completed: dict | None) -> tuple[int, str]:
    """The free bytes a full build must still have: the reviewed 75 GiB budget, less only the
    VERIFIED completed payload bytes of a resume, and never below the 10 GiB operating reserve.

    A fresh build (no verified payload) is unchanged at the reviewed conservative 75 GiB. A
    resume asks for max(10 GiB, 75 GiB - verified completed bytes): the corpus that is already
    written no longer has to be paid for, while the operating reserve is preserved on top of
    whatever remains of the demand. Nothing else - not the projection, not a partial payload -
    ever reduces the requirement.
    """
    verified = int((completed or {}).get("verified_bytes") or 0)
    if verified <= 0:
        return (
            FULL_GATE_FREE_BYTES,
            "initial budget: reviewed conservative 75 GiB, nothing verified",
        )
    required = max(OPERATING_RESERVE_BYTES, FULL_GATE_FREE_BYTES - verified)
    return required, (
        f"resume: 75 GiB budget minus {verified} verified completed payload bytes, floored at the "
        f"{OPERATING_RESERVE_BYTES / 2**30:.0f} GiB operating reserve"
    )


def storage_gate_report(
    out_data: Path | str,
    mode: str,
    path: Path | str | None = None,
    manifest: dict | None = None,
    progress: dict | None = None,
) -> dict:
    """One run's storage readings + the reserve decision (readings belong in costs only).

    The physical mount check is unchanged for both modes: the conservative reading still comes
    from the backing volume, and an unbacked virtual/unknown local disk measures 0 and refuses.
    Only the full mode's REQUIRED reserve moves on a resume, and only by verified payload bytes.
    """
    facts = output_mount_facts(out_data)
    declaration = load_host_volume_declaration(path)
    physical, source, reasons = conservative_free_bytes(facts, declaration)
    completed = (
        verified_completed_payload_bytes(out_data, manifest, progress) if mode == "full" else None
    )
    if mode == "canary":
        reserve, basis = CANARY_MIN_FREE_BYTES, "canary: fixed 2 GiB floor"
    else:
        reserve, basis = full_demand_bytes(completed)
    margin = physical - reserve
    reasons = list(reasons)
    if margin < 0:
        reasons.append(
            f"conservative free {physical} bytes < required reserve {reserve} bytes "
            f"({reserve / 2**30:.1f} GiB); basis: {basis}"
        )
    return {
        "mode": mode,
        "planning_headroom_bytes": [PLANNING_HEADROOM_LO_BYTES, PLANNING_HEADROOM_HI_BYTES],
        "reserve_bytes": reserve,
        "reserve_gib": round(reserve / 2**30, 1),
        "reserve_basis": basis,
        "operating_reserve_bytes": OPERATING_RESERVE_BYTES,
        "completed_payload": completed,
        "measured": facts,
        "host_volume_declaration": declaration or None,
        "conservative_free_bytes": physical,
        "conservative_free_source": source,
        "margin_bytes": margin,
        "ok": margin >= 0 and physical > 0,
        "reasons": reasons or None,
    }


def assert_quote_channel_surface(contracts: dict, path: Path | str | None = None) -> None:
    """No prospective quote feature may exist while the quote gate is not ready.

    The quote channel is a versioned sibling of the core corpus. Until causal quote
    acquisition (B3_quote_lane) is resolved with evidence, schema.json must declare no
    prospective quote feature field (registry value/absence vocabulary or an undeclared
    `quote_` name) and no written frame may carry one (`is_quote_feature` enforces the frame
    side). A `quote_` field the frozen schema itself declares as roster provenance stays
    legal: it is bookkeeping about the roster ARTIFACT, not quote alpha. A null/missing quote
    for a causal-roster name is 'not acquired', never market information.
    """
    fields = quote_feature_fields(contracts)
    if not fields:
        return
    outstanding = unresolved_blockers(
        blockers_by_class(contracts["contract.json"], BLOCKER_CLASS_QUOTE),
        load_resolved_state(path),
    )
    if outstanding:
        raise SystemExit(
            "quote channel gate refusal: prospective quote feature field(s) declared while "
            f"{', '.join(outstanding)} is outstanding: {fields}; a null/missing quote for a "
            "causal-roster name is 'not acquired', never market information"
        )


def readiness_states(
    contracts: dict,
    mode: str,
    days: list[str],
    dev: list[str],
    free_bytes: int,
    path: Path | str | None = None,
    storage: dict | None = None,
    build_state: dict | None = None,
) -> dict:
    """The two independent readiness states: `core_full_ready` and `quote_channel_ready`.

    `core_full_ready` needs the full dev calendar BUILT with every core blocker (B1,B2,B5,B6)
    resolved by a verified evidence file. It derives from the completed corpus - the complete
    day set, a complete manifest whose payload shas verify, and verified parents - because a
    successful 45-50 GiB build has by definition consumed the reserve it was admitted against;
    requiring that same reserve to still be free afterwards would make the state unreachable.
    The 75 GiB conservative reserve (or, on a resume, the reserve net of VERIFIED completed
    payload bytes) is the ENTRY gate in `stage_full`, checked before any byte is written.
    `quote_channel_ready` needs the quote-sibling blocker (B3_quote_lane) resolved and gates
    ONLY the prospective quote channel, never the core corpus. `free_bytes` is the
    CONSERVATIVE physical number; the raw per-run readings travel in `storage` and are written
    to costs.json, not to this decision block.
    """
    contract = contracts["contract.json"]
    declared = load_resolved_state(path)
    core_ids = blockers_by_class(contract, BLOCKER_CLASS_CORE)
    quote_ids = blockers_by_class(contract, BLOCKER_CLASS_QUOTE)
    core_outstanding = unresolved_blockers(core_ids, declared)
    quote_outstanding = unresolved_blockers(quote_ids, declared)
    core_reasons: list[str] = []
    if mode != "full":
        core_reasons.append(f"mode={mode}: only the full stage can claim {CORE_READINESS_STATE}")
    elif sorted(days) != sorted(dev):
        core_reasons.append("day set does not cover the full dev calendar")
    if core_outstanding:
        core_reasons.append("outstanding core blockers: " + ", ".join(core_outstanding))
    if mode == "full" and build_state is None:
        core_reasons.append("no completed build state: manifest/parents/dayset not verified")
    elif build_state is not None:
        core_reasons.extend(build_state.get("problems") or [])
    quote_reasons = (
        ["outstanding quote blockers: " + ", ".join(quote_outstanding)] if quote_outstanding else []
    )
    storage_block = (
        {
            "reserve_bytes": storage["reserve_bytes"],
            "reserve_basis": storage.get("reserve_basis"),
            "operating_reserve_bytes": storage.get("operating_reserve_bytes"),
            "planning_headroom_bytes": storage["planning_headroom_bytes"],
            "conservative_ok": storage["ok"],
            "conservative_free_bytes": storage["conservative_free_bytes"],
            "conservative_free_source": storage["conservative_free_source"],
            "role": "entry gate: measured before the build wrote any byte",
            "readings_recorded_in": "costs.json storage block",
        }
        if storage is not None
        else None
    )
    return {
        CORE_READINESS_STATE: not core_reasons,
        QUOTE_READINESS_STATE: not quote_outstanding,
        "core_blockers": core_ids,
        "quote_channel_blockers": quote_ids,
        "core_outstanding_blockers": core_outstanding,
        "quote_channel_outstanding_blockers": quote_outstanding,
        "core_full_ready_reasons": core_reasons or None,
        "quote_channel_ready_reasons": quote_reasons or None,
        "resolved_state_manifest": str(resolved_state_path(path)),
        "core_evidence_state": {b: blocker_evidence_state(b, declared) for b in core_ids},
        "build_state": build_state,
        "storage_gate": storage_block,
        "quote_feature_fields": quote_feature_fields(contracts),
        "quote_feature_policy": (
            "no prospective quote feature is materialized before the quote gate passes; a "
            "null/missing quote for a causal-roster name is 'not acquired', never market data"
        ),
        "independence": (
            f"{QUOTE_READINESS_STATE} never gates {CORE_READINESS_STATE} and vice versa"
        ),
    }


def build_completeness(out_data: Path, manifest: dict, days: list[str], dev: list[str]) -> dict:
    """Whether the built corpus itself is complete: day set, manifest, payloads, parents.

    This is what `core_full_ready` derives from. A payload counts only when its bytes re-hash
    to the manifest's recorded sha256, and a parent counts only when the current file matches
    the recorded parent sha - so a half-written or drifted corpus can never claim readiness.
    """
    problems: list[str] = []
    if sorted(days) != sorted(dev):
        problems.append(f"day set covers {len(days)} of {len(dev)} dev days")
    payloads = (manifest or {}).get("payload_sha256") or {}
    if not payloads:
        problems.append("manifest declares no payload")
    missing, mismatched = [], []
    for key, info in payloads.items():
        if not info.get("path"):
            continue
        p = out_data / info["path"]
        if not p.exists():
            missing.append(key)
        elif sha256_file(p) != info.get("sha256"):
            mismatched.append(key)
    if missing:
        problems.append(f"{len(missing)} manifest payload(s) absent: {sorted(missing)[:5]}")
    if mismatched:
        problems.append(f"{len(mismatched)} payload sha mismatch: {sorted(mismatched)[:5]}")
    base_parents = {"panel.parquet": PANEL, "phase2_session_calendar.json": CAL_PATH}
    recorded = (manifest or {}).get("parents") or {}
    drift = [
        name
        for name, path in base_parents.items()
        if (recorded.get(name) or {}).get("sha256") != sha256_file(path)
    ]
    if drift:
        problems.append(f"parent sha drift: {drift}")
    return {
        "days_built": len(days),
        "dev_days": len(dev),
        "n_payloads": len(payloads),
        "n_payload_missing": len(missing),
        "n_payload_mismatched": len(mismatched),
        "parent_drift": drift,
        "ok": not problems,
        "problems": problems,
    }


def validate_contracts(contracts: dict) -> list[str]:
    """Structural invariants of the frozen contracts. Returns problems (empty = ok)."""
    problems: list[str] = []
    schema = contracts["schema.json"]
    reg = contracts["causal_registry.json"]
    canary = contracts["canary_days.json"]
    if schema.get("schema_version") != "v0":
        problems.append(f"schema_version {schema.get('schema_version')!r}")
    required = set(schema["field_attributes"]["required"])
    tables = schema["tables"]
    for tname, table in tables.items():
        for fname, field in table["fields"].items():
            if set(field) != required:
                problems.append(f"{tname}.{fname}: attributes {sorted(field)}")
            if "distance_eligible" in field:
                problems.append(f"{tname}.{fname}: distance_eligible is forbidden in Freeze O")
            if (
                field["coordinate_only"]
                and field["prospective_allowed"]
                and field["namespace"] == "audit_coordinate"
            ):
                pass  # audit coordinates may be selectable, they are simply not channels
            if field["retrospective_only"] and field["prospective_allowed"]:
                problems.append(f"{tname}.{fname}: retrospective_only with prospective_allowed")
    for tname, table in tables.items():
        names = list(table["fields"])
        if len(names) != len(set(names)):
            problems.append(f"{tname}: duplicate field names")
        if not table.get("primary_key") or not table.get("sort"):
            problems.append(f"{tname}: missing primary_key/sort")
    if not reg.get("default_deny", False):
        problems.append("causal_registry default_deny is false")
    for tname, table in tables.items():
        for fname in table["fields"]:
            if "distance_eligible" in table["fields"][fname]:
                problems.append(f"{tname}.{fname}: distance_eligible key is forbidden")
    ledger = contracts["adaptive_choice_ledger.json"]
    if not ledger.get("blind", False):
        problems.append("adaptive choice ledger is not blind")
    if ledger.get("outcome_columns_read"):
        problems.append("adaptive choice ledger read outcome columns")
    denied_ledger = denied_column_names()
    for e in ledger.get("entries", []):
        for key in (
            "choice_id",
            "alternatives",
            "evidence_inspected",
            "columns_read",
            "blind_flag",
            "decision",
            "affected_node",
            "recheck_block",
            "status",
        ):
            if key not in e:
                problems.append(f"ledger entry {e.get('choice_id')}: missing {key}")
        if not e.get("blind_flag"):
            problems.append(f"ledger entry {e.get('choice_id')}: blind_flag false")
        leaked = [
            c
            for c in e.get("columns_read", [])
            if c.split(" ")[0] in denied_ledger or is_quote_feature(c.split(" ")[0], contracts)
        ]
        if leaked:
            problems.append(f"ledger entry {e.get('choice_id')}: outcome column read {leaked}")
    n_days = len(canary["days"])
    if n_days != canary["target_n"]:
        problems.append(f"canary days {n_days} != target {canary['target_n']}")
    days = [d["day"] for d in canary["days"]]
    if len(set(days)) != n_days:
        problems.append("canary days are not unique")
    # gate classes: the quote channel is a sibling and never a core full-build blocker
    contract = contracts["contract.json"]
    classes: dict[str, list[str]] = {}
    for b in contract.get("full_build_blockers", []):
        bclass = b.get("blocker_class")
        if bclass not in BLOCKER_CLASSES:
            problems.append(f"{b.get('blocker_id')}: blocker_class {bclass!r} unknown")
        classes.setdefault(bclass, []).append(b["blocker_id"])
    if not contract.get("full_build_blockers"):
        problems.append("full_build_blockers is empty")
    gate = contract.get("full_build_gate") or {}
    core_ids = sorted(classes.get(BLOCKER_CLASS_CORE, []))
    quote_ids = sorted(classes.get(BLOCKER_CLASS_QUOTE, []))
    if sorted(gate.get("core_blockers") or []) != core_ids:
        problems.append(
            f"full_build_gate.core_blockers {gate.get('core_blockers')} != core class {core_ids}"
        )
    if sorted(gate.get("quote_channel_blockers") or []) != quote_ids:
        problems.append(
            f"full_build_gate.quote_channel_blockers {gate.get('quote_channel_blockers')} != "
            f"quote class {quote_ids}"
        )
    if not gate.get("requirement") or "no flag bypasses" not in gate.get("requirement", ""):
        problems.append("full_build_gate.requirement must state that no flag bypasses the gate")
    if BLOCKER_CLASS_QUOTE not in classes:
        problems.append("no quote-class blocker declared")
    readiness = contract.get("readiness") or {}
    for state in (CORE_READINESS_STATE, QUOTE_READINESS_STATE):
        if state not in readiness:
            problems.append(f"readiness.{state} missing")
    if quote_feature_fields(contracts):
        problems.append(
            "schema.json declares prospective quote feature fields "
            f"{quote_feature_fields(contracts)}; no quote feature may be materialized "
            "before the quote gate passes"
        )
    return problems


def forbidden_names_in(df_or_names, contracts: dict | None = None) -> list[str]:
    names = list(df_or_names) if not isinstance(df_or_names, pl.DataFrame) else df_or_names.columns
    return [
        n
        for n in names
        if n in FORBIDDEN_EXACT
        or n.startswith(FORBIDDEN_PREFIXES)
        or is_quote_feature(n, contracts)
    ]


_DTYPE_MAP = {
    "str": pl.Utf8,
    "int32": pl.Int32,
    "int64": pl.Int64,
    "float64": pl.Float64,
    "bool": pl.Boolean,
    "datetime[us, UTC]": pl.Datetime("us", "UTC"),
    "datetime[us, America/New_York]": pl.Datetime("us", "America/New_York"),
    "list[str]": pl.List(pl.Utf8),
}


def declared_dtypes() -> dict[str, dict[str, object]]:
    """Table -> {field: polars dtype} from the frozen schema."""
    sch = load_json(TRACK / "schema.json")
    return {
        t: {f: _DTYPE_MAP[v["dtype"]] for f, v in tbl["fields"].items()}
        for t, tbl in sch["tables"].items()
    }


def enforce_dtypes(
    df: pl.DataFrame, table: str, dtypes: dict[str, dict[str, object]] | None = None
) -> pl.DataFrame:
    """Cast a frame to the frozen physical dtypes (fails loudly on drift)."""
    m = (dtypes or declared_dtypes())[table]
    missing = [c for c in df.columns if c not in m]
    if missing:
        raise ValueError(f"{table}: columns not declared in schema.json: {missing}")
    try:
        return df.select([pl.col(c).cast(m[c]) for c in df.columns])
    except Exception as exc:  # noqa: BLE001 - re-raised with table context
        raise ValueError(f"{table}: declared dtype enforcement failed: {exc}") from exc


def assert_observation_frame(
    df: pl.DataFrame, table: str, allowed_censor: tuple = (), contracts: dict | None = None
) -> None:
    """Fail loudly if a frame carries a blocked panel family/column or a quote feature.

    Censor columns are permitted only in declared places (callers pass the schema flag);
    quote-roster provenance the frozen schema declares stays legal (see `is_quote_feature`).
    """
    bad = [
        n for n in df.columns if n not in allowed_censor and n in forbidden_names_in(df, contracts)
    ]
    if bad:
        raise ValueError(f"{table}: forbidden observation column(s) present: {bad}")


# --------------------------------------------------------------------------- #
# calendar / sources
# --------------------------------------------------------------------------- #


def session_calendar() -> tuple[dict, str]:
    doc = load_json(CAL_PATH)
    return doc["session_end_by_day"], sha256_file(CAL_PATH)


def dev_days() -> list[str]:
    return sim.dev_days()


def guard(day: str) -> None:
    sim.guard_day(day)


_PIT_CACHE: dict = {}
_PANEL_CACHE: dict = {}
_NET_STATUS_CACHE: dict = {}


def _write_day_binding(root: Path, day: str, **extra) -> None:
    """Write a joint-evidence `day_roots` binding for `day` (self-tests only).

    Day resolution goes through this map, so a validator test that wants a day admitted must
    publish a binding exactly as the real publisher does - which is the point of the change.
    """
    ev = root / "evidence"
    ev.mkdir(parents=True, exist_ok=True)
    ev = ev / "acquisition_admission.json"
    doc = load_json(ev) if ev.exists() else {}
    scope = extra.pop("scope", "feb2025")
    entry = {
        "blocker_id": "B1_raw_2025_02",
        "scope": scope,
        "version": "v4",
        "acquisition_root": str(root),
        "payload": f"bars/{day}.parquet",
        "manifest": f"bars/{day}.manifest.json",
        "roster": f"rosters/{day}.json",
        "sip_coverage_complete": True,
    }
    if scope == "aprmay2026":
        entry["blocker_id"] = "B2_raw_roster_2026_04_05"
    entry.update(extra)
    # a REAL throwaway origin snapshot, hashed: origin proof is default-deny, so a fixture that
    # omits it must be refused, exactly as a real capture with no pin would be.
    snap = root / "producers" / "origin_producer.py"
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text(f"# throwaway origin producer for {day}\n")
    code_sha = entry.pop("_code_sha", None) or sha256_file(snap)
    man = root / "bars" / f"{day}.manifest.json"
    if man.exists():
        doc_m = load_json(man) or {}
        doc_m["code_sha256"] = code_sha
        man.write_text(json.dumps(doc_m))
    blockers = doc.setdefault("blockers", {})
    bid = entry["blocker_id"]
    prev = blockers.get(bid) or {}
    prev.setdefault("version", "v4")
    prev["producer"] = {
        "snapshot_path": str(snap),
        "snapshot_sha256": sha256_file(snap),
        "code_sha256_at_capture": code_sha,
    }
    blockers[bid] = prev
    doc.setdefault("day_roots", {})[day] = entry
    doc.setdefault("all_scopes_ready", True)
    doc.setdefault("missing_scopes", [])
    doc.setdefault("stale_scopes", [])
    doc.setdefault("scopes", {})
    doc.setdefault("blockers", {})
    ev.write_text(json.dumps(doc))


@contextlib.contextmanager
def _acq_root(root: Path):
    """Point the acquisition readers at a throwaway root (self-tests only)."""
    global ACQ_BARS, ACQ_ROSTERS, ACQ_ADMISSION, ACQ_JOINT_EVIDENCE
    prev = (ACQ_BARS, ACQ_ROSTERS, ACQ_ADMISSION, ACQ_JOINT_EVIDENCE)
    ACQ_BARS, ACQ_ROSTERS = root / "bars", root / "rosters"
    ACQ_ADMISSION = ACQ_JOINT_EVIDENCE = root / "evidence/acquisition_admission.json"
    _ACQ_CACHE.clear()
    try:
        yield root
    finally:
        ACQ_BARS, ACQ_ROSTERS, ACQ_ADMISSION, ACQ_JOINT_EVIDENCE = prev
        _ACQ_CACHE.clear()


@contextlib.contextmanager
def _alias_map(path: Path):
    """Point the alias reader at a throwaway map (self-tests only)."""
    global ACQ_ALIAS_MAP
    prev = ACQ_ALIAS_MAP
    ACQ_ALIAS_MAP = path
    _ACQ_CACHE.clear()
    try:
        yield path
    finally:
        ACQ_ALIAS_MAP = prev
        _ACQ_CACHE.clear()


def pit_vintages() -> tuple[list[str], dict[str, list[str]]]:
    """Load the PIT archive once per process: vintage order + the raw symbol lists.

    Only the vintage ORDER and the underlying per-vintage symbol lists are retained. The
    per-vintage `frozenset` is NOT memoized forever: 1,066 dev days resolve to up to 1,066
    distinct vintages and pinning a ~5,800-symbol frozenset for each costs ~920 MB of parent
    RSS for the whole run, while the registry only needs the COUNT. Use `pit_elig_count` when
    membership is not required.
    """
    if "vintages" not in _PIT_CACHE:
        pit = pl.read_parquet(PIT_PATH, columns=["vintage", "symbol"])
        g = pit.group_by("vintage").agg(pl.col("symbol")).sort("vintage")
        # Some PIT symbols are stored in a FIXED-WIDTH field and arrive padded with spaces
        # ('ECC           '), while every other source stores the bare identifier ('ECC'). That is
        # whitespace padding of ONE identifier, not a fuzzy match: stripping is deterministic,
        # exactly invertible, and proven here - a strip that would collapse two DISTINCT PIT
        # names is refused rather than silently merging identities.
        by_v: dict[str, list[str]] = {}
        for vintage, syms in zip(g["vintage"].to_list(), g["symbol"].to_list(), strict=True):
            stripped = [str(x).strip() for x in syms]
            if len(set(stripped)) != len(stripped):
                dupes = sorted({x for x in stripped if stripped.count(x) > 1})
                raise ValueError(
                    f"PIT vintage {vintage}: whitespace normalisation would collapse distinct "
                    f"symbols {dupes}; refusing to merge identities"
                )
            by_v[str(vintage)] = stripped
        _PIT_CACHE["vintages"] = [str(v) for v in g["vintage"].to_list()]
        _PIT_CACHE["by_v"] = by_v
        del pit, g
    return _PIT_CACHE["vintages"], _PIT_CACHE["by_v"]


def pit_elig_vintage(day: str) -> str:
    """The PIT vintage in force for `day` (latest vintage on or before it). No set built."""
    vs, _by_v = pit_vintages()
    i = bisect.bisect_right(vs, day) - 1
    if i < 0:
        raise ValueError(f"no PIT vintage <= {day}")
    return vs[i]


def pit_elig_count(day: str) -> int:
    """How many PIT names are eligible for `day`, without ever materializing a frozenset."""
    vs, by_v = pit_vintages()
    i = bisect.bisect_right(vs, day) - 1
    if i < 0:
        raise ValueError(f"no PIT vintage <= {day}")
    return len(by_v[vs[i]])


# A day plus, on a block-boundary day, its previous session: two live vintages is normal. The
# bound makes a leak impossible rather than merely unlikely, so walking thousands of distinct
# vintages re-uses a tiny window instead of growing the parent's resident set without limit.
PIT_VINTAGE_CACHE_MAX = 4
_PIT_VINTAGE_SETS: dict[str, frozenset] = {}


def pit_elig(
    day: str, vintages: list[str] | None = None, memo: dict | None = None
) -> tuple[str, frozenset]:
    """The eligible PIT set for `day`, cached per VINTAGE in a hard-bounded map.

    The returned frozenset is SHARED, so callers must treat it as read-only.
    """
    v = pit_elig_vintage(day)
    s = _PIT_VINTAGE_SETS.get(v)
    if s is None:
        _vs, by_v = pit_vintages()
        s = frozenset(by_v[v])
        if len(_PIT_VINTAGE_SETS) >= PIT_VINTAGE_CACHE_MAX:
            _PIT_VINTAGE_SETS.pop(next(iter(_PIT_VINTAGE_SETS)))
        _PIT_VINTAGE_SETS[v] = s
    return v, s


# --- admitted acquisition days (B1 raw 2025-02, B2 2026-04/05 missing names) -------------- #
#
# The acquisition producer owns the bytes. This producer CONSUMES them and verifies every one
# itself: the manifest must declare status=complete, feed=sip, adjustment=raw, no errors, and
# the parquet must re-hash to the manifest's sha256. A day that is absent, incomplete, or
# unverifiable is simply NOT admitted - the baseline lane is then used and the blocker stays
# open. Nothing here ever fabricates bars or downgrades a missing day to a zero.
ACQ_MODE_REPLACE = "replace_baseline"
ACQ_MODE_SUPPLEMENT = "supplement_baseline"
ACQ_MANIFEST_SUFFIX = ".manifest.json"
ACQ_BAR_FIELDS = ("open", "high", "low", "close", "volume")
_ACQ_CACHE: dict = {}


def acquisition_alias_map() -> dict[str, str]:
    """Explicit raw/provider spelling -> canonical PIT ticker. The ONLY rename path.

    An unlisted spelling keeps its own name and is simply not PIT-eligible; nothing is ever
    fuzzy-matched, inferred from a similar string, or invented from a per-day manifest's own
    alias field. The frozen map is keyed CANONICAL name -> {provider_symbol, basis, evidence},
    so the rename direction used here is its inverse, and the inverse must be injective: one
    provider spelling, one canonical name. A spelling may not be both a source and a target
    (no chains), because either would make the rename ambiguous. Names the map explicitly
    declares NOT aliased stay unaliased - no mapping is ever invented for them.
    """
    if "alias" not in _ACQ_CACHE:
        doc: dict[str, str] = {}
        if ACQ_ALIAS_MAP.exists():
            raw = load_json(ACQ_ALIAS_MAP)
            if not isinstance(raw, dict):
                raise ValueError(f"{ACQ_ALIAS_MAP}: alias map must be a JSON object")
            if "aliases" in raw:
                raw = raw["aliases"]
            if not isinstance(raw, dict):
                raise ValueError(f"{ACQ_ALIAS_MAP}: alias map must be an object of entries")
            for key, value in raw.items():
                canonical = str(key)
                if canonical in {"not_aliased", "version", "schema", "notes"}:
                    continue  # declared-not-aliased block / metadata: never a rename
                if isinstance(value, str):
                    provider, doc[provider] = value, canonical
                    continue
                if not isinstance(value, dict):
                    raise ValueError(
                        f"{ACQ_ALIAS_MAP}: alias entry {key!r} must be a name or an object"
                    )
                provider = str(value.get("provider_symbol") or "")
                if not provider:
                    continue  # a declared-not-aliased canonical name carries no provider spelling
                if provider in doc and doc[provider] != canonical:
                    raise ValueError(
                        f"{ACQ_ALIAS_MAP}: provider spelling {provider!r} maps to both "
                        f"{doc[provider]!r} and {canonical!r}; the alias map is not injective"
                    )
                doc[provider] = canonical
            chains = sorted(set(doc) & set(doc.values()))
            if chains:
                raise ValueError(
                    f"{ACQ_ALIAS_MAP}: alias chain (a spelling is both source and target): {chains}"
                )
        _ACQ_CACHE["alias"] = doc
    return _ACQ_CACHE["alias"]


def alias_map_sha256() -> str | None:
    """sha256 of the explicit alias map, or None when the map file does not exist."""
    return sha256_file(ACQ_ALIAS_MAP) if ACQ_ALIAS_MAP.exists() else None


# The producer's REAL `scope` values, which are also the scope keys in the admission union.
ACQ_SCOPE_MODE = {
    "feb2025": ACQ_MODE_REPLACE,  # 2025-02: the SIP day file REPLACES the floor-qualified fallback
    "aprmay2026": ACQ_MODE_SUPPLEMENT,  # 2026-04/05: the SIP day file SUPPLEMENTS the month file
}


def acquisition_mode_for_scope(scope: object, day: str) -> str:
    """Map a declared acquisition scope to replace/supplement. Anything else refuses.

    The scope is the producer's declared value (`feb2025` | `aprmay2026`) - the same string the
    admission union uses as a scope key, so the two can never drift apart silently. An
    unrecognised scope is a hard refusal, never a default: guessing would silently attach a day
    file to the wrong lane and corrupt the floor/roster blocker state.
    """
    text = str(scope or "").strip()
    mode = ACQ_SCOPE_MODE.get(text)
    if mode is None:
        raise ValueError(
            f"acquisition scope {scope!r} for {day} is not one of the declared scopes "
            f"{sorted(ACQ_SCOPE_MODE)}"
        )
    return mode


# The manifest's DECLARED obligation standing for each lane mode. The scope string and the
# standing are cross-checked, never inferred from one another.
ACQ_SCOPE_MODE_INVERSE = {
    ACQ_MODE_REPLACE: "replaced_by_this_acquisition",
    ACQ_MODE_SUPPLEMENT: "supplemented",
}


WITNESS_ROLE = "legacy_cross_feed_proxy_not_sip"


def _joint_source_policy_id() -> str | None:
    """The joint evidence's source policy id (collection-time INFRASTRUCTURE provenance)."""
    return (_joint_evidence().get("source_policy") or {}).get("policy_id")


def _witness_sha(binding: dict) -> str | None:
    """Re-hash a cross-feed witness artifact, or None when the day has none.

    A RETROSPECTIVE PROXY SIBLING: recorded for provenance and parent integrity only. It is never
    read as a SIP rank input and never enters the lane digest.
    """
    w = binding.get("cross_feed_witness")
    if not w:
        return None
    return content_sha(w) if w.exists() else None


def _resolve_root(value: object, version: str | None = None) -> Path:
    """An acquisition_root from the joint evidence, resolved to a directory that EXISTS.

    The evidence may spell the root as the Windows/mounted path the producer wrote from
    (`/mnt/c/...`) while the same directory is reached here through the local symlinked data
    root. We resolve what was DECLARED first, and only if that path does not exist do we look
    for the declared VERSION under the resolved market-data root. The root is never guessed: no
    version, no root, and the day is simply not admitted. Spelled-root/reported-root comparison
    uses `.resolve()` because the data root is a symlink, so the two spellings differ textually
    while naming the same directory.
    """
    p = Path(str(value))
    p = p if p.is_absolute() else DATA / p
    if p.exists():
        return p
    if version:
        alt = DATA / "atlas/acquisition" / str(version)
        if alt.exists():
            return alt
    return p


def joint_day_binding(day: str) -> dict | None:
    """The joint evidence's binding for `day`: which ROOT supplies it, and which payload.

    This is the ONLY way a day is located. The ambient ATLAS_ACQ_ROOT is never consulted for
    day resolution, so a per-blocker root set (B1 v4, B2 v3) cannot be half-honoured.
    """
    doc = _joint_evidence()
    entry = (doc.get("day_roots") or {}).get(day)
    if not isinstance(entry, dict):
        return None
    root = _resolve_root(
        entry.get("acquisition_root") or entry.get("acquisition_root_reported"),
        entry.get("version"),
    )
    return {
        "day": day,
        "root": root,
        "blocker_id": entry.get("blocker_id"),
        "scope": entry.get("scope"),
        "version": entry.get("version"),
        "payload": root / str(entry.get("payload") or ""),
        "payload_sha256": entry.get("payload_sha256"),
        "manifest": root / str(entry.get("manifest") or ""),
        "manifest_sha256": entry.get("manifest_sha256"),
        # The day_roots entry publishes only `roster_sha256`, NOT the roster path: the path
        # lives on the day's own manifest (relative to that root). If neither names it, the
        # roster is absent - NOT an error, and NOT silently classified.
        "roster": (root / str(entry["roster"])) if entry.get("roster") else None,
        "roster_sha256": entry.get("roster_sha256"),
        # the legacy cross-feed witness is a RETROSPECTIVE PROXY SIBLING: recorded as
        # provenance, never opened as a rank input and never part of the lane digest.
        "cross_feed_witness": (
            (root / str(entry["cross_feed_witness"])) if entry.get("cross_feed_witness") else None
        ),
        "cross_feed_witness_sha256": entry.get("cross_feed_witness_sha256"),
        "cross_feed_disagreement_count": entry.get("cross_feed_disagreement_count"),
        "sip_coverage_complete": entry.get("sip_coverage_complete"),
    }


def producer_proof(day: str, manifest_sha: str) -> tuple[bool, str | None]:
    """Check a day's manifest against the producer PINNED for its blocker, not the installed one.

    The joint evidence carries `blockers[bid].producer.code_sha256_at_capture` - the hash of the
    producer that actually captured the day. A day whose manifest was written by a DIFFERENT
    producer is refused. We deliberately do NOT compare against the currently-installed tool:
    that would invalidate a historical capture merely because this producer has since changed.
    """
    doc = _joint_evidence()
    binding = (doc.get("day_roots") or {}).get(day) or {}
    bid = binding.get("blocker_id")
    producer = ((doc.get("blockers") or {}).get(bid) or {}).get("producer") or {}
    pinned = producer.get("code_sha256_at_capture")
    snap_rel = producer.get("snapshot_path")
    snap_sha = producer.get("snapshot_sha256")
    # FAIL CLOSED. A missing pin, a missing snapshot or a missing snapshot hash is NOT a pass:
    # without them the capture's origin is unverifiable, and origin proof is default-deny.
    if not pinned:
        return False, f"no origin-producer pin (code_sha256_at_capture) for {bid}"
    if not snap_rel or not snap_sha:
        return False, f"no origin-producer snapshot proof (snapshot_path/sha256) for {bid}"
    snap = Path(str(snap_rel))
    snap = snap if snap.is_absolute() else ROOT / snap
    if not snap.exists():
        return False, f"origin-producer snapshot absent for {bid}: {snap}"
    actual = sha256_file(snap)
    if actual != snap_sha:
        return False, f"origin-producer snapshot sha {actual} != declared {snap_sha} for {bid}"
    # The proof is ONE equality, not two ends checked separately:
    #   re-hashed bytes == snapshot_sha256 == code_sha256_at_capture == manifest.code_sha256
    # Without the middle link, a wrapper or metadata file whose bytes hash to snapshot_sha256 but
    # which is NOT the captured producer would satisfy a loose check and forge an origin.
    if snap_sha != pinned:
        return False, (
            f"origin snapshot sha {snap_sha} != the blocker's capture pin {pinned} for {bid}; "
            "the snapshot is not the exact source the capture was taken with"
        )
    recorded = binding.get("producer_code_sha256") or _day_manifest_code_sha(day)
    if recorded != pinned:
        return False, f"day manifest producer {recorded} != pinned {pinned} for {bid}"
    return True, None


def _day_manifest_code_sha(day: str) -> str | None:
    binding = joint_day_binding(day)
    if binding is None or not binding["manifest"].exists():
        return None
    return (load_json(binding["manifest"]) or {}).get("code_sha256")


def _manifest_roster(binding: dict, man: dict) -> Path | None:
    """The day's roster path, as named by its OWN manifest, relative to that day's root."""
    rel = man.get("roster")
    return (binding["root"] / str(rel)) if rel else None


def _joint_evidence() -> dict:
    """The joint scope evidence document ({} when absent)."""
    if "joint" not in _ACQ_CACHE:
        _ACQ_CACHE["joint"] = load_json(ACQ_JOINT_EVIDENCE) if ACQ_JOINT_EVIDENCE.exists() else {}
    return _ACQ_CACHE["joint"]


def _validate_acquisition_day(day: str) -> dict | None:
    """One admitted acquisition day, fully re-verified locally; None when not admitted.

    The day's ROOT comes from the joint evidence's `day_roots` binding, never from the ambient
    acquisition root, so the per-blocker root set is honoured exactly as published.
    """
    binding = joint_day_binding(day)
    if binding is None:
        return None
    if not binding["root"].exists():
        # a DECLARED root that is absent is refused, never swapped for a look-alike directory
        return None
    if not binding["manifest"].exists():
        return None
    mp = binding["manifest"]
    man = load_json(mp)
    if not isinstance(man, dict):
        raise ValueError(f"{mp}: acquisition manifest must be a JSON object")
    if man.get("day") != day:
        raise ValueError(f"{mp}: manifest day {man.get('day')!r} != filename day {day!r}")
    if str(man.get("status") or "") != "complete":
        return None  # a legitimate incomplete acquisition is recorded, never admitted
    if man.get("errors"):
        raise ValueError(f"{mp}: completed manifest carries errors: {man['errors']!r}")
    src_meta = man.get("source") or {}
    if src_meta.get("feed") != "sip" or src_meta.get("adjustment") != "raw":
        raise ValueError(
            f"{mp}: source must be feed=sip adjustment=raw, got {src_meta.get('feed')!r}/"
            f"{src_meta.get('adjustment')!r}"
        )
    ok_proof, why = producer_proof(day, binding.get("manifest_sha256") or "")
    if not ok_proof:
        raise ValueError(f"{mp}: origin-producer proof failed: {why}")
    declared = str(man.get("file") or "")
    if not declared or Path(declared).name != f"{day}.parquet":
        raise ValueError(f"{mp}: file {declared!r} does not name {day}.parquet")
    bars = binding["payload"]
    if not bars.exists():
        raise ValueError(f"{mp}: declared bars file is absent: {bars}")
    got = sha256_file(bars)
    if got != man.get("sha256"):
        raise ValueError(f"{mp}: bars sha256 {got} != declared {man.get('sha256')!r}")
    sch = pl.scan_parquet(bars).collect_schema()
    missing = [c for c in ("timestamp", "ticker", *ACQ_BAR_FIELDS) if c not in sch.names()]
    if missing:
        raise ValueError(f"{bars}: missing declared columns {missing}")
    if sch["timestamp"] != pl.Datetime("ns", "UTC"):
        raise ValueError(f"{bars}: timestamp must be Datetime(ns, UTC), got {sch['timestamp']}")
    for c in ACQ_BAR_FIELDS:
        if sch[c] != pl.Float64:
            raise ValueError(f"{bars}: {c} must be Float64, got {sch[c]}")
    mode = acquisition_mode_for_scope(man.get("scope"), day)
    # FAIL-CLOSED WINDOW CROSS-CHECK (not a boundary change). The consumer's promise is the
    # CLOSED projection window [PROJECTION_LO, PROJECTION_HI] = [565, 965] ET, which the selected-
    # path prints and grid both preserve; the RTH restriction (et >= 570) is a deliberate,
    # contract-documented SUBSET applied only to the broad board, and it is not changed here. A
    # capture that declares a window NARROWER than our projection window would silently delete
    # 565-569 rows from the retrospective projections, so that combination is refused outright.
    # An undeclared bound is NOT CLAIMED and is recorded, not refused.
    src_bounds = src_meta.get("window_et_bounds")
    if src_bounds is not None:
        if not isinstance(src_bounds, (list, tuple)) or len(src_bounds) != 2:
            raise ValueError(f"{mp}: source.window_et_bounds {src_bounds!r} is not a [lo, hi] pair")
        lo, hi = int(src_bounds[0]), int(src_bounds[1])
        if src_meta.get("window_bounds_inclusive") is False:
            raise ValueError(
                f"{mp}: source declares an EXCLUSIVE window {src_bounds}; this consumer's "
                f"projection window [{PROJECTION_LO}, {PROJECTION_HI}] is a closed interval, so an "
                "exclusive capture cannot satisfy the projection contract"
            )
        if lo > PROJECTION_LO or hi < PROJECTION_HI:
            raise ValueError(
                f"{mp}: declared acquisition window [{lo}, {hi}] is narrower than this consumer's "
                f"closed projection window [{PROJECTION_LO}, {PROJECTION_HI}]; the 565-569 rows "
                "the prints/grid projections promise to preserve would be missing"
            )
    # A NAMED witness must exist and re-hash to its declared sha, exactly like the SIP payload.
    # Recording both hashes without comparing them would let a cross-feed witness be silently
    # rewritten after capture - destroying the very disagreement the stratum exists to preserve.
    witness_rel = binding.get("cross_feed_witness")
    if witness_rel is not None:
        declared_w = binding.get("cross_feed_witness_sha256")
        if not witness_rel.exists():
            raise ValueError(f"{mp}: named cross-feed witness is absent: {witness_rel}")
        if not declared_w:
            raise ValueError(f"{mp}: named cross-feed witness declares no sha256")
        actual_w = sha256_file(witness_rel)
        if actual_w != declared_w:
            raise ValueError(
                f"{witness_rel}: witness sha {actual_w} != declared {declared_w}; the preserved "
                "cross-feed evidence was altered after capture"
            )
    # A name the fetch never completed a request for tells us NOTHING about whether it traded,
    # so the day is not admissible at all - unlike a genuine zero-bar name, which means the
    # provider accepted the spelling and the name simply did not trade.
    request_failed = [str(x) for x in (man.get("symbols_request_failed") or [])]
    if request_failed:
        return None
    # Coverage is decided by the manifest's DECLARED obligation, never inferred from the scope
    # name: a scope string and the standing can disagree, and inferring is how a replaced
    # baseline's names silently vanish.
    standing = ((man.get("obligation") or {}).get("baseline_standing")) or None
    if standing is not None and str(standing) != ACQ_SCOPE_MODE_INVERSE[mode]:
        raise ValueError(
            f"{mp}: obligation.baseline_standing {standing!r} contradicts the {mode!r} scope "
            f"{man.get('scope')!r} (expected {ACQ_SCOPE_MODE_INVERSE[mode]!r})"
        )
    src_meta = man.get("source") or {}
    # The roster PATH comes from the day's own manifest (day_roots publishes only its sha).
    # It is supplementary: a full-replacement B1 day may legitimately have none, in which case
    # nothing is roster-classified. But if the manifest declares obligations or a request
    # account that we CONSUME, the roster must be present and must re-hash, or we refuse rather
    # than read coverage we cannot back.
    roster = binding["roster"] or _manifest_roster(binding, man)
    consumes_roster = bool(
        (man.get("obligation") or {}).get("cross_feed_witness_names")
        or (man.get("obligation") or {}).get("uncovered")
        or man.get("symbols_zero_bars")
        or (man.get("sip_coverage") or {}).get("symbols_zero_bars")
    )
    roster_ok = None
    if roster is not None and roster.exists():
        roster_ok = sha256_file(roster)
        declared_r = binding.get("roster_sha256") or man.get("roster_sha256")
        if declared_r and roster_ok != declared_r:
            raise ValueError(
                f"{roster}: roster sha {roster_ok} != declared {declared_r}; the coverage "
                "classification backing this day was altered after capture"
            )
    elif consumes_roster:
        raise ValueError(
            f"{mp}: manifest declares coverage/request obligations consumed from a roster, "
            f"but no roster is present at {roster}; refusing rather than reading unbacked "
            "coverage"
        )
    return {
        "day": day,
        "path": bars,
        "sha256": got,
        "manifest_path": mp,
        "manifest_sha256": sha256_file(mp),
        "roster_path": roster if (roster is not None and roster.exists()) else None,
        "roster_sha256": sha256_file(roster) if roster.exists() else None,
        "scope": binding.get("scope") or man.get("scope"),
        "mode": mode,
        "acquisition_root": str(binding["root"]),
        "acquisition_version": binding.get("version"),
        "witness_path": (
            str(binding["cross_feed_witness"]) if binding["cross_feed_witness"] else None
        ),
        "witness_sha256": _witness_sha(binding),
        "witness_declared_sha256": binding.get("cross_feed_witness_sha256"),
        "witness_role": WITNESS_ROLE if binding["cross_feed_witness"] else None,
        "cross_feed_disagreement_count": binding.get("cross_feed_disagreement_count"),
        "sip_coverage_complete": binding.get("sip_coverage_complete"),
        "baseline_standing": standing,
        "obligation_rule": (man.get("obligation") or {}).get("rule"),
        "required_names": (man.get("obligation") or {}).get("required_names"),
        "observed_evidence_names": (man.get("obligation") or {}).get("observed_evidence_names"),
        "asof": src_meta.get("asof"),
        "asof_semantics": src_meta.get("asof_semantics"),
        "feed_era": "sip_acquired_era",
        "symbols_requested": man.get("requested_symbols"),
        "symbols_with_data": man.get("symbols_with_data"),
        "symbols_zero_bars": list(man.get("symbols_zero_bars") or []),
        "symbols_invalid": list(man.get("symbols_invalid") or []),
        "symbols_request_failed": request_failed,
    }


def acquisition_day(day: str) -> dict | None:
    """The admitted acquisition day for `day` (verified), or None when there is none."""
    key = f"day:{day}"
    if key not in _ACQ_CACHE:
        _ACQ_CACHE[key] = _validate_acquisition_day(day)
    return _ACQ_CACHE[key]


# The months each acquisition scope is responsible for. B1 owns 2025-02 (the floored fallback);
# B2 owns 2026-04/05 (the measured PIT-name hole).
ACQ_SCOPE_MONTHS = {
    "B1_raw_2025_02": ("2025-02",),
    "B2_raw_roster_2026_04_05": ("2026-04", "2026-05"),
}


def acquisition_required_days(blocker_id: str) -> list[str]:
    """Every guarded dev day this acquisition scope MUST admit, from the calendar itself.

    Derived from `basket_sim.dev_days()` and the scope's months, never from the evidence: the
    gate therefore recomputes the day set independently, so a union admitting a one-day canary
    subset cannot pass. The order is the calendar's, so the set is stable.
    """
    months = ACQ_SCOPE_MONTHS.get(blocker_id)
    if months is None:
        return []
    return [d for d in dev_days() if d[:7] in months]


def _admission_entry_days(entry: object) -> list[str]:
    """Admitted day names from one admission entry (list of days or list of day records)."""
    if isinstance(entry, list):
        items = entry
    elif isinstance(entry, dict):
        items = entry.get("admitted_days") or entry.get("days") or []
    else:
        return []
    out: list[str] = []
    for it in items:
        if isinstance(it, str):
            out.append(it)
        elif isinstance(it, dict) and it.get("day"):
            out.append(str(it["day"]))
    return out


# blocker id -> the producer's scope string, which is both the per-day manifest `scope` and the
# admission union's scope key. One vocabulary, so the two can never drift apart silently.
ACQ_ADMISSION_SCOPE = {
    "B1_raw_2025_02": "feb2025",
    "B2_raw_roster_2026_04_05": "aprmay2026",
}
assert set(ACQ_ADMISSION_SCOPE.values()) == set(ACQ_SCOPE_MODE), (
    "every acquisition scope must map to a replace/supplement mode"
)


def acquisition_admission(blocker_id: str | None = None) -> dict:
    """The shared acquisition admission UNION, summarised for the gate (never trusted alone).

    The producer keeps one durable per-scope attestation and rebuilds this union from them, so
    both blocker ids are ALWAYS present as keys - presence proves nothing. A scope counts as
    attested only when the union says `all_scopes_ready`, lists no missing or stale scope, AND
    this scope's own `state` is `ready`; `not_ready`/`stale`/`unverified` keep the blocker open
    and contribute no admitted days. The gate then still re-verifies every admitted day against
    its own manifest and bars sha256, so a payload that stops hashing closes the blocker here
    too even if the union once admitted it.
    """
    cache_key = f"admission:{blocker_id or 'ALL'}"
    if cache_key in _ACQ_CACHE:
        return _ACQ_CACHE[cache_key]
    out: dict[str, Any] = {
        "path": ACQ_ADMISSION,
        "present": False,
        "sha256": None,
        "version": None,
        "schema": None,
        "all_scopes_ready": False,
        "missing_scopes": [],
        "stale_scopes": [],
        "scope_state": None,
        "admitted_days": [],
        "required_days": [],
        "uncovered_days": [],
        "admitted_by_blocker": {},
        "residual_gaps": [],
        "ready": False,
        "problems": [],
    }
    if ACQ_ADMISSION.exists():
        doc = load_json(ACQ_ADMISSION)
        out["present"] = True
        out["sha256"] = sha256_file(ACQ_ADMISSION)
        out["version"] = doc.get("version")
        out["schema"] = doc.get("schema")
        missing = [str(s) for s in (doc.get("missing_scopes") or [])]
        stale = [str(s) for s in (doc.get("stale_scopes") or [])]
        out["missing_scopes"] = missing
        out["stale_scopes"] = stale
        out["all_scopes_ready"] = bool(doc.get("all_scopes_ready"))
        # `blockers` is kept in the shape this reader already used; `entries` is an equivalent
        entries = doc.get("blockers") or doc.get("entries") if isinstance(doc, dict) else None
        by_blocker: dict[str, list[str]] = {}
        gaps: list[str] = []
        if isinstance(entries, dict):
            for bid, entry in entries.items():
                by_blocker[str(bid)] = _admission_entry_days(entry)
                if isinstance(entry, dict):
                    gaps += [str(g) for g in (entry.get("residual_confirmed_trading_gaps") or [])]
        elif isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                bid = str(entry.get("blocker") or entry.get("blocker_id") or "")
                by_blocker.setdefault(bid, []).extend(_admission_entry_days(entry))
                gaps += [str(g) for g in (entry.get("residual_confirmed_trading_gaps") or [])]
        scopes = doc.get("scopes") if isinstance(doc, dict) else None
        problems: list[str] = []
        if not out["all_scopes_ready"]:
            problems.append("the admission union reports all_scopes_ready=false")
        if missing:
            problems.append(f"unverified acquisition scope(s): {missing}")
        if stale:
            problems.append(
                f"stale acquisition scope(s) (different root/code/contract/PIT/alias): {stale}"
            )
        if blocker_id is not None:
            scope = ACQ_ADMISSION_SCOPE.get(blocker_id)
            rec = ((scopes or {}).get(scope) or {}) if isinstance(scopes, dict) else {}
            state = rec.get("state")
            out["scope_state"] = state
            # Five states, only `ready` admits. `verified_subset` is the escalation hole: the
            # run's own day checks passed but coverage is partial, so it admits nothing and the
            # blocker stays open. The CLI exit code is NEVER consulted - a subset run that
            # verified correctly is a successful verification, not a discharged blocker.
            if state != "ready":
                problems.append(
                    f"acquisition scope {scope!r} is {state!r}"
                    + (f": {rec.get('reason')}" if rec.get("reason") else "")
                    + ", not 'ready'"
                )
            if state == "ready" and rec.get("is_full_scope") is not True:
                problems.append(f"acquisition scope {scope!r} is not a full-scope attestation")
            if state == "ready" and (rec.get("rehash") or {}).get("mismatches"):
                problems.append(
                    f"acquisition scope {scope!r} reports rehash mismatches: "
                    f"{(rec.get('rehash') or {}).get('mismatches')[:5]}"
                )
            # a scope that is not `ready` contributes NOTHING: its key may be present and its
            # day list may be populated, but only a ready, full-scope attestation admits days
            out["admitted_by_blocker"] = (
                {blocker_id: by_blocker.get(blocker_id, [])}
                if state == "ready" and rec.get("is_full_scope") is True
                else {}
            )
        else:
            out["admitted_by_blocker"] = (
                by_blocker if out["all_scopes_ready"] and not missing and not stale else {}
            )
        admitted = out["admitted_by_blocker"]
        out["admitted_days"] = sorted({d for dl in admitted.values() for d in dl})
        out["residual_gaps"] = sorted(set(gaps))
        # `ready` means this evidence may DISCHARGE the blocker, so it also requires the exact
        # day set, recomputed here from the guarded dev calendar rather than from the union.
        if blocker_id is not None:
            required = set(acquisition_required_days(blocker_id))
            uncovered = sorted(required - set(out["admitted_days"]))
            out["required_days"] = sorted(required)
            out["uncovered_days"] = uncovered
            if uncovered and not problems:
                problems.append(
                    f"the acquisition admits {len(out['admitted_days'])} of the {len(required)} "
                    f"required days; {len(uncovered)} uncovered, first: {uncovered[:5]}"
                )
        out["problems"] = problems
        out["ready"] = not problems
    else:
        out["problems"] = ["acquisition admission evidence absent"]
    _ACQ_CACHE[cache_key] = out
    return out


def baseline_raw_lane(day: str) -> tuple[Path, str, str]:
    """The pre-acquisition raw lane for a day: (path, kind, feed_era). Raises when absent."""
    guard(day)
    month = day[:7]
    if month == "2025-02":
        return DATA / "clean_ohlcv_2025-02.parquet", "clean_fallback", "floor_fallback"
    if month in {"2026-03", "2026-04", "2026-05"}:
        return DATA / "backfill" / f"ohlcv_{month}.parquet", "backfill_raw", "alpaca_sip_era"
    if month >= "2021-02" and month <= "2023-12":
        return DATA / f"ohlcv_{month}.parquet", "ohlcv_raw", "hf_era"
    if month >= "2025-03" and month <= "2026-02":
        return DATA / f"ohlcv_{month}.parquet", "ohlcv_raw", "hf_resume_era"
    raise FileNotFoundError(f"no raw lane source for {day} (sealed/reserved/unacquired)")


def _lane_key(lanes: list[dict]) -> tuple[tuple[str, str], ...]:
    return tuple((ln["role"], str(ln["path"])) for ln in lanes)


_CONTENT_SHA_MEMO: dict[tuple[str, int, int], str] = {}


def content_sha(path: Path) -> str | None:
    """sha256 of a file, memoized by (resolved path, size, mtime_ns).

    A lane plan is built per day, so a per-lane cache re-hashes the same multi-GB month file
    once per dev day in that month (~386 GiB of re-read measured for 18.47 GiB of distinct lane
    files, i.e. hours of single-threaded hashing). The memo is keyed by resolved path plus size
    and mtime_ns, so an edited or replaced file re-hashes: a stale digest can never be served
    for changed bytes.
    """
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path.resolve()), st.st_size, st.st_mtime_ns)
    hit = _CONTENT_SHA_MEMO.get(key)
    if hit is None:
        hit = sha256_file(path)
        _CONTENT_SHA_MEMO[key] = hit
    return hit


def lane_sha(lane: dict) -> str | None:
    """A lane's file sha256, computed on first use and cached on the lane itself."""
    if lane.get("sha256") is None and lane.get("_pending"):
        path = lane["path"]
        lane["sha256"] = content_sha(path) if path.exists() else None
    return lane.get("sha256")


def day_lane_plan(day: str) -> dict:
    """Every raw file that legitimately supplies `day`, in read order, with its provenance.

    A B1 admission REPLACES the floor-qualified clean fallback (its floors are gone, so the
    day is no longer floor-qualified). A B2 admission SUPPLEMENTS the baseline month file.
    The combined sha256 covers every contributing file plus the alias map, so one column
    proves the whole lane set a row was built from.
    """
    key = f"plan:{day}"
    if key in _ACQ_CACHE:
        return _ACQ_CACHE[key]
    bpath, bkind, bera = baseline_raw_lane(day)
    acq = acquisition_day(day)
    lanes: list[dict] = []
    if acq is not None and acq["mode"] == ACQ_MODE_REPLACE:
        lanes.append(
            {
                "role": "acquired_day",
                "kind": "acquired_day",
                "feed_era": acq["feed_era"],
                "path": acq["path"],
                "sha256": acq["sha256"],
            }
        )
    else:
        lanes.append(
            {
                "role": "baseline",
                "kind": bkind,
                "feed_era": bera,
                "path": bpath,
                "sha256": None,
                "_pending": True,
            }
        )
        if acq is not None:
            lanes.append(
                {
                    "role": "acquired_day",
                    "kind": "acquired_day",
                    "feed_era": bera,
                    "path": acq["path"],
                    "sha256": acq["sha256"],
                }
            )
    plan = {
        "day": day,
        "lanes": lanes,
        "kind": "+".join(ln["kind"] for ln in lanes),
        "feed_era": lanes[0]["feed_era"],
        "floor_qualified": any(ln["kind"] == "clean_fallback" for ln in lanes),
        "alias_map_sha256": alias_map_sha256(),
        "acquisition": acq,
        "acquisition_scope": (acq or {}).get("scope"),
        "acquisition_mode": (acq or {}).get("mode"),
    }
    _ACQ_CACHE[key] = plan
    return plan


def resolve_raw_source(day: str) -> tuple[Path, str, str]:
    """The day's primary raw file: (path, kind, feed_era) with the admitted day file first."""
    plan = day_lane_plan(day)
    primary = plan["lanes"][-1] if plan["acquisition"] else plan["lanes"][0]
    return primary["path"], plan["kind"], plan["feed_era"]


def feed_era_of(day: str) -> str:
    return day_lane_plan(day)["feed_era"]


def source_sha(day: str) -> str:
    """Combined lane sha256: every contributing raw file plus the explicit alias map."""
    return combined_lane_sha(day_lane_plan(day))


def combined_lane_sha(plan: dict) -> str:
    """The combined lane sha256: every contributing raw file plus the alias map."""
    return sha256_bytes(
        json.dumps(
            {
                "day": plan["day"],
                "lanes": sorted(
                    [ln["kind"], ln["role"], str(ln["path"]), lane_sha(ln)] for ln in plan["lanes"]
                ),
                "alias_map_sha256": plan["alias_map_sha256"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )


def prior_stored_session(day: str, allow_seed: bool = True) -> tuple[str | None, str]:
    """Previous session in the STORED (unsealed) sequence + provenance kind."""
    dev = dev_days()
    i = bisect.bisect_left(dev, day)
    if i > 0:
        prev = dev[i - 1]
        gap = (datetime.fromisoformat(day).date() - datetime.fromisoformat(prev).date()).days
        kind = "block_gap_last_stored" if gap > sim.DEV_BLOCK_GAP_DAYS else "raw_prior_session"
        return prev, kind
    if allow_seed and day == dev[0]:
        # first dev day: the seed session is allowed for previous close only
        seed_path = DATA / SEED_MONTH_FILE
        if seed_path.exists():
            return SEED_DAY, "seed_2021-01-29"
    return None, "none"


def trivial_prev_source(prev_day: str) -> tuple[Path, str]:
    """Raw-lane file for a previous-close-only session (includes the seed day)."""
    p, kind, _era = raw_source_for_any_day(prev_day)
    return p, kind


def prev_session_plan(prev_day: str) -> dict | None:
    """The lane plan of the previous session, or None for the seed/closed cases."""
    if prev_day == SEED_DAY:
        p = DATA / SEED_MONTH_FILE
        if not p.exists():
            return None
        return {
            "day": prev_day,
            "lanes": [
                {
                    "role": "seed",
                    "kind": "seed",
                    "feed_era": "seed",
                    "path": p,
                    "sha256": None,
                    "_pending": True,
                }
            ],
            "kind": "seed",
            "feed_era": "seed",
            "floor_qualified": False,
            "alias_map_sha256": None,
            "acquisition": None,
            "acquisition_scope": None,
            "acquisition_mode": None,
        }
    return day_lane_plan(prev_day)


def prev_close_provenance(day: str) -> tuple[str | None, str, dict]:
    """Previous-close provenance without touching the raw lane."""
    prev_day, kind = prior_stored_session(day)
    prov = {
        "prev_close_day": prev_day,
        "prev_close_source": kind,
        "prev_close_et": None,
        "prev_close_age_sessions": None,
        "prev_close_gap_days": None,
        "prev_close_stale": False,
        "prev_close_sealed_prior": False,
        "prev_close_floor_qualified": False,
        "prev_close_lane_kind": None,
        "prev_close_lane_sha256": None,
        "prev_close_acquired": False,
        "prev_close_alias_map_sha256": None,
    }
    if prev_day is None:
        prov["prev_close_source"] = "none"
        return None, "none", prov
    prov["prev_close_age_sessions"] = 1
    prov["prev_close_gap_days"] = (
        datetime.fromisoformat(day).date() - datetime.fromisoformat(prev_day).date()
    ).days
    prov["prev_close_stale"] = prov["prev_close_gap_days"] > 4
    if kind == "block_gap_last_stored" or prov["prev_close_stale"]:
        prov["prev_close_sealed_prior"] = True
    # The denominator prefers the ADMITTED previous day when one exists: an admitted B1
    # 2025-02 day is the real prior session, so a following day no longer divides by the
    # floor-qualified clean fallback. The first block2 session (2025-02-03) still has no
    # admitted prior session (2025-01-31 is sealed) and keeps its declared block-gap
    # provenance with no sealed look-back.
    plan = prev_session_plan(prev_day)
    if plan is None or not all(ln["path"].exists() for ln in plan["lanes"]):
        prov["prev_close_source"] = "none"
        return None, "none", prov
    prov["prev_close_lane_kind"] = plan["kind"]
    prov["prev_close_lane_sha256"] = combined_lane_sha(plan)
    prov["prev_close_acquired"] = plan["acquisition"] is not None
    prov["prev_close_alias_map_sha256"] = plan["alias_map_sha256"]
    prov["prev_close_floor_qualified"] = plan["floor_qualified"]
    return prev_day, kind, prov


def prev_closes_from_frame(
    df: pl.DataFrame, session_end: int
) -> tuple[dict[str, float], dict[str, int], int | None]:
    """Last RTH close per ticker inside an already-read day frame, with each name's OWN et.

    Returns (close_by_name, et_by_name, cross_sectional_max_et). The per-name map matters: a
    single cross-sectional maximum stamped on every row would claim a name whose last prior bar
    was at 09:45 ended at the cross-section's latest minute. The max is returned separately for
    provenance only.
    """
    d = df.filter((pl.col("etm") >= RTH_LO) & (pl.col("etm") <= session_end))
    if d.height == 0:
        return {}, {}, None
    last = (
        d.sort(["etm", "ticker"])
        .group_by("ticker")
        .agg(pl.col("close").last().alias("prev_close"), pl.col("etm").last().alias("prev_et"))
        .filter(pl.col("prev_close").is_not_null() & (pl.col("prev_close") > 0))
    )
    if last.height == 0:
        return {}, {}, None
    closes = dict(zip(last["ticker"].to_list(), last["prev_close"].to_list(), strict=False))
    ets = {
        str(k): int(v)
        for k, v in zip(last["ticker"].to_list(), last["prev_et"].to_list(), strict=False)
    }
    return closes, ets, int(last["prev_et"].max())


def prev_close_map(day: str) -> tuple[dict[str, float], dict]:
    """Standalone previous-close derivation (used by self-tests and verifiers)."""
    prev_day, _kind, prov = prev_close_provenance(day)
    if prev_day is None:
        return {}, prov
    calendar, _sha = session_calendar()
    prev_se = int(calendar.get(prev_day, sim.SESSION_END_NORMAL))
    src, _src_kind = trivial_prev_source(prev_day)
    if not src.exists():
        prov["prev_close_source"] = "none"
        return {}, prov
    df = read_raw_days([(prev_day, prev_se)])
    m, ets, last_et = prev_closes_from_frame(df, prev_se)
    prov["prev_close_et_by_name"] = ets
    prov["prev_close_et"] = last_et
    if not m:
        prov["prev_close_source"] = "none"
    return m, prov


def raw_source_for_any_day(day: str) -> tuple[Path, str, str]:
    """Raw lane for a day; the sealed-month seed day is allowed as a close seed only."""
    if day == SEED_DAY:
        p = DATA / SEED_MONTH_FILE
        if not p.exists():
            raise FileNotFoundError(f"previous-close seed file missing: {p}")
        return p, "seed", "seed"
    return resolve_raw_source(day)


def read_raw_days(
    bounds: list[tuple[str, int]], *, et_lo: int | None = None, et_hi: int | None = None
) -> pl.DataFrame:
    """Raw rows for one or more (day, session_end) bounds, ET-converted, from EVERY lane.

    Days sharing a lane set are scanned once with an OR filter; each day is bounded by its own
    session end so an early close never contributes post-close bars to a previous-close
    derivation. A B2 admitted day supplements its baseline month file: rows are unioned after
    the EXPLICIT alias map canonicalises spellings, and a canonical name arriving from two
    different lane files is a collision that refuses the day rather than silently merging.
    """
    assert bounds, "no days requested"
    alias = acquisition_alias_map()

    def et_utc(day: str, minute: int) -> datetime:
        """UTC instant of an ET wall-clock minute-of-day, resolved by the tz database.

        Wall-clock ET is built on the calendar date first and only then given ``tzinfo=ET``, so
        DST is handled by ZoneInfo rather than by a fixed offset (absolute-duration arithmetic
        on an aware ET datetime would drift by an hour across a changeover). A minute >= 1440
        rolls into the NEXT calendar date, which is how the EXCLUSIVE upper bound is expressed.
        """
        d = datetime.fromisoformat(day).date() + timedelta(days=minute // 1440)
        m = minute % 1440
        return datetime(d.year, d.month, d.day, m // 60, m % 60, tzinfo=ET).astimezone(UTC)

    groups: dict[tuple, list[tuple[str, int]]] = {}
    for day, se in bounds:
        plan = prev_session_plan(day)
        if plan is None:
            raise FileNotFoundError(f"no raw lane for {day}")
        groups.setdefault(_lane_key(plan["lanes"]), []).append((day, se))
    frames: list[pl.DataFrame] = []
    for _key, group in groups.items():
        plan = prev_session_plan(group[0][0])
        # ONE physical-timestamp conjunct for the whole group, derived from the SAME per-day
        # lo/hi the ET predicate below uses: every day contributes the wall-clock ET window
        # [day lo] .. [day (hi+1)] EXCLUSIVE, reduced with min(lo)/max(hi+1) and converted to UTC
        # instants. It admits a strict SUPERSET of the ET-filtered rows by construction, so the
        # AND the scan applies cannot change which rows survive.
        ts_lo_utc = min(et_utc(day, RTH_LO if et_lo is None else et_lo) for day, _se in group)
        ts_hi_utc = max(et_utc(day, (se if et_hi is None else et_hi) + 1) for day, se in group)
        parts = []
        for lane in plan["lanes"]:
            lf = pl.scan_parquet(lane["path"]).select(RAW_COLS)
            # Attached BEFORE the timezone derivation so the optimizer can push it into the
            # parquet scan and skip row groups that lie entirely outside the group's window.
            lf = lf.filter((pl.col("timestamp") >= ts_lo_utc) & (pl.col("timestamp") < ts_hi_utc))
            lf = lf.with_columns(
                pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("ts_et")
            ).with_columns(
                pl.col("ts_et").dt.date().cast(pl.Utf8).alias("dt"), etm_expr().alias("etm")
            )
            cond = None
            for day, se in group:
                # et_lo/et_hi default to the RTH board window, so every existing call site is
                # unchanged; the COVERAGE read passes the declared projection window instead.
                lo = RTH_LO if et_lo is None else et_lo
                hi = se if et_hi is None else et_hi
                c = (pl.col("dt") == day) & (pl.col("etm") >= lo) & (pl.col("etm") <= hi)
                cond = c if cond is None else (cond | c)
            sub = lf.filter(cond).collect()
            if alias:
                sub = sub.with_columns(
                    pl.col("ticker")
                    .replace_strict(alias, default=pl.col("ticker"), return_dtype=pl.Utf8)
                    .alias("ticker")
                )
            parts.append(sub.with_columns(pl.lit(lane["role"]).alias("_lane_role")))
        if len(parts) == 1:
            frames.append(parts[0])
            continue
        frames.append(_refuse_lane_collision(*parts, day=group[0][0]))
    if len(frames) == 1:
        return frames[0]
    return pl.concat(frames, how="vertical")


def _refuse_lane_collision(*parts: pl.DataFrame, day: str) -> pl.DataFrame:
    """Union per-lane frames, refusing a canonical name that arrives from more than one lane.

    A supplement is supposed to add names the baseline lacks. If the same canonical ticker shows
    up in two lane files, the two sources disagree about what the tape was and no merge rule can
    settle it, so the day is refused rather than silently de-duplicated.
    """
    union = pl.concat(list(parts), how="vertical")
    owners = (
        union.group_by(["dt", "ticker"])
        .agg(pl.col("_lane_role").n_unique().alias("_lanes"))
        .filter(pl.col("_lanes") > 1)
    )
    if owners.height:
        sample = owners.sort(["dt", "ticker"]).head(5).to_dicts()
        raise ValueError(
            f"{day}: {owners.height} canonical name(s) arrive from more than one raw lane "
            f"(collision refused, no silent merge): {sample}"
        )
    return union.drop("_lane_role")


# The consumer's own declared projection window, [PROJECTION_LO, PROJECTION_HI] = [565, 965].
# COVERAGE is derived here, NOT from the RTH-trimmed board frame: deriving it from `raw` made the
# fix a production no-op, because read_raw_day already filters etm >= RTH_LO, so a name whose only
# bar for the day is off-hours (2025-02-03 NTZ, one bar at etm 961) was absent from both sets and
# silently counted as a missing confirmed trader.
COVERAGE_LO, COVERAGE_HI = PROJECTION_LO, PROJECTION_HI


def read_coverage_names(day: str) -> set[str]:
    """Tickers with ANY admitted bar in the declared [COVERAGE_LO, COVERAGE_HI] window that day.

    Read through the SAME production lane reader, with the projection window instead of the RTH
    one. Only the ticker set is materialised; no bar values are used for coverage.
    """
    df = read_raw_days([(day, COVERAGE_HI)], et_lo=COVERAGE_LO, et_hi=COVERAGE_HI)
    return set(df["ticker"].unique().to_list())


def read_raw_day(day: str, session_end: int) -> pl.DataFrame:
    """Full raw rows for one day inside RTH, ET-converted, from every contributing lane."""
    return read_raw_days([(day, session_end)])


def net_day(day: str) -> pl.DataFrame:
    return pl.read_parquet(NET_BARS / f"{day}.parquet")


def net_reconciliation() -> dict:
    """The net-index reconciliation evidence, verified against the files it names.

    `days` is a verified day -> record map; an empty map means NO day is reconciled. The
    derived index itself is re-hashed on disk, so a stale evidence file that claims a rebuilt
    index the current index does not match reports no verified day rather than a false repair.
    """
    if "recon" in _NET_STATUS_CACHE:
        return _NET_STATUS_CACHE["recon"]
    out: dict[str, Any] = {
        "path": TRACK / NET_RECONCILIATION_REL,
        "present": False,
        "sha256": None,
        "status": None,
        "days": {},
        "problems": ["reconciliation evidence absent"],
    }
    p = out["path"]
    if p.exists():
        doc = load_json(p)
        out["present"] = True
        out["sha256"] = sha256_file(p)
        out["status"] = doc.get("status")
        problems: list[str] = []
        if doc.get("schema") != NET_RECONCILIATION_SCHEMA:
            problems.append(f"schema {doc.get('schema')!r} != {NET_RECONCILIATION_SCHEMA!r}")
        if doc.get("status") != "ok":
            problems.append(f"reconciliation status is {doc.get('status')!r}, not 'ok'")
        root = Path(str(doc.get("root") or DATA / "sip/net"))
        di = doc.get("derived_index") or {}
        want = di.get("sha256_after")
        if not NET_MANIFEST_INDEX.exists():
            problems.append("derived index file is absent")
        elif not want:
            problems.append("derived_index.sha256_after is not declared")
        elif sha256_file(NET_MANIFEST_INDEX) != want:
            problems.append("derived index on disk does not match derived_index.sha256_after")
        if problems:
            out["problems"] = problems
        else:
            verified: dict[str, Any] = {}
            for day, rec in (doc.get("days") or {}).items():
                if not (rec.get("complete") and rec.get("verified")):
                    continue
                bad = [
                    f"{day}: {role} {info.get('path')} sha mismatch"
                    for role, info in (rec.get("files") or {}).items()
                    if not info.get("sha256")
                    or not (root / str(info.get("path"))).exists()
                    or sha256_file(root / str(info.get("path"))) != info.get("sha256")
                ] + [
                    f"{day}: {role} manifest sha mismatch"
                    for role, info in (rec.get("manifests") or {}).items()
                    if not info.get("sha256")
                    or not (root / str(info.get("path"))).exists()
                    or sha256_file(root / str(info.get("path"))) != info.get("sha256")
                ]
                rows = rec.get("index_rows") or {}
                if not rows.get("present_after"):
                    bad.append(f"{day}: no derived-index row after the rebuild")
                if not bad:
                    verified[str(day)] = rec
            out["days"] = verified
            out["problems"] = [f"{len(verified)}/{len(doc.get('days') or {})} target days verified"]
    _NET_STATUS_CACHE["recon"] = out
    return out


def net_reconciled(day: str) -> bool:
    """True only for a day the reconciliation verified end to end (files + manifests + index)."""
    return str(day) in net_reconciliation()["days"]


def net_manifest_status(day: str) -> str:
    """Per-day net-lane status: ok | partial | missing | unreconciled.

    Worst case over the day's index records (a partial trades/quotes record means the raw
    acquisition lost symbols that day). A day the reconciliation verified end to end is `ok`
    even when its index row was rebuilt rather than original - the claim is per-day MEASURED,
    never a hardcoded pair of dates.
    """
    if net_reconciled(day):
        return "ok"
    if not NET_MANIFEST_INDEX.exists():
        return "missing"
    if "status" not in _NET_STATUS_CACHE:
        status: dict[str, str] = {}
        with open(NET_MANIFEST_INDEX) as fh:
            for line in fh:
                rec = json.loads(line)
                d = rec.get("day")
                if d is None:
                    continue
                if str(rec.get("status", "ok")) == "partial":
                    status[d] = "partial"
                else:
                    status.setdefault(d, "ok")
        _NET_STATUS_CACHE["status"] = status
    got = _NET_STATUS_CACHE["status"].get(day, "missing")
    if got == "missing" and (NET_COVERAGE / f"{day}.json").exists():
        # the per-day files verify locally but the index row is absent/stale: unreconciled
        return "unreconciled"
    return got


def net_unreconciled_days(dev: list[str]) -> list[str]:
    """Dev days whose net-lane provenance is not yet verified by the reconciliation."""
    return [d for d in dev if net_manifest_status(d) in {"missing", "unreconciled"}]


def panel_memberships() -> pl.DataFrame:
    if "df" not in _PANEL_CACHE:
        _PANEL_CACHE["df"] = pl.read_parquet(
            PANEL,
            columns=[
                "sleeve_day",
                "month",
                "block",
                "family",
                "ticker",
                "entry_rank",
                "entry_et",
                "entry_px",
                "session_end",
            ],
        )
    return _PANEL_CACHE["df"]


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #


# The day_registry counters that `patch_registry` fills from each day's per-day stats, and that
# `build_day_registry` therefore MUST emit. ONE tuple, used by both sides, so the two can never
# disagree again: the batch that reworked the raw_roster_* family left `patch_registry` dropping
# and joining columns the registry never produced, which raised ColumnNotFoundError only AFTER a
# full 20-day canary had been built. `patch_registry` now also REFUSES on any expected column the
# registry does not carry, rather than silently tolerating drift.
REGISTRY_PATCH_COLUMNS = (
    "raw_n_symbols",
    "raw_n_pit_symbols",
    "raw_n_pit_symbols_full_window",
    "raw_n_pit_offhours_only",
    "raw_roster_missing_n",
    "raw_roster_verified_absent_n",
    "raw_roster_verified_no_sip_bar_n",
    "raw_roster_cross_feed_witnessed_n",
    "raw_roster_no_sip_bar_also_witnessed_n",
    "raw_roster_missing_unresolved_n",
)


def build_day_registry(
    dev: list[str],
    calendar: dict,
    cal_sha: str,
    panel: pl.DataFrame,
    canary: list[dict],
    built_days: set[str],
) -> pl.DataFrame:
    """All 1,066 dev days; per-day input shas for the days THIS STAGE actually built.

    `built_days` is what the stage built, NOT the frozen canary list: a full run builds 1,066
    days and every one of them must be recorded as built and hashed, or `core_full_ready` would
    be derived from a registry that claims all of them are `registry_only`. Canary MEMBERSHIP
    stays a separate frozen fact (`canary_member`), so the two are never conflated.
    """
    canary_map = {d["day"]: d for d in canary}
    panel_stats = panel.group_by("sleeve_day").agg(
        pl.col("ticker").n_unique().alias("n_paths"), pl.len().alias("n_rows")
    )
    panel_map = {
        r["sleeve_day"]: (r["n_paths"], r["n_rows"]) for r in panel_stats.iter_rows(named=True)
    }
    built_days = set(built_days)
    rows = []
    # a run that built the entire dev calendar is a full build; a 20-day canary is not
    build_status = "full_built" if len(built_days) >= len(dev) else "canary_built"
    for day in dev:
        guard(day)
        se = int(calendar[day])
        plan = day_lane_plan(day)
        era = plan["feed_era"]
        kind = plan["kind"]
        src = plan["lanes"][-1]["path"] if plan["acquisition"] else plan["lanes"][0]["path"]
        acq = plan["acquisition"] or {}
        basel = plan["lanes"][0]
        # the registry needs only the PIT COUNT, so no frozenset is materialized here: walking
        # 1,066 days must not pin up to 1,066 vintage sets in the parent
        built = day in built_days
        v = pit_elig_vintage(day)
        n_pit = pit_elig_count(day)
        n_paths, n_members = panel_map.get(day, (0, 0))
        rec = {
            "day": day,
            "month": day[:7],
            "block": sim_block(day),
            "feed_era": era,
            "dow": datetime.fromisoformat(day).weekday(),
            "session_end": se,
            "is_early_close": se == sim.SESSION_END_EARLY,
            "calendar_sha256": cal_sha,
            "guard_status": "ok",
            "raw_source_kind": kind,
            "raw_source_path": str(src.relative_to(DATA))
            if str(src).startswith(str(DATA))
            else str(src),
            "raw_source_uri": src.as_uri(),
            "raw_source_present": all(ln["path"].exists() for ln in plan["lanes"]),
            "raw_source_sha256": combined_lane_sha(plan) if built else None,
            "raw_inputs_hashed": built,
            "raw_baseline_path": str(basel["path"].relative_to(DATA))
            if str(basel["path"]).startswith(str(DATA))
            else str(basel["path"]),
            "raw_baseline_sha256": lane_sha(basel) if built else None,
            "acq_bars_present": acq.get("path") is not None and acq["path"].exists(),
            "acq_bars_path": str(acq["path"]) if acq.get("path") else None,
            "acq_bars_sha256": acq.get("sha256"),
            "acq_manifest_sha256": acq.get("manifest_sha256"),
            "acq_roster_sha256": acq.get("roster_sha256"),
            "acq_scope": plan["acquisition_scope"],
            "acq_mode": plan["acquisition_mode"],
            "alias_map_sha256": plan["alias_map_sha256"],
            "net_trades_present": (NET_TRADES / f"{day}.parquet").exists(),
            "net_trades_sha256": None,
            "net_manifest_status": net_manifest_status(day),
            "net_reconciled": net_reconciled(day),
            "cross_feed_disagreement_count": (acq.get("cross_feed_disagreement_count")),
            "cross_feed_witness_present": acq.get("witness_path") is not None,
            "cross_feed_witness_sha256": acq.get("witness_sha256"),
            "cross_feed_witness_role": acq.get("witness_role"),
            "sip_coverage_complete": acq.get("sip_coverage_complete"),
            "source_policy_id": _joint_source_policy_id(),
            "net_bars_present": (NET_BARS / f"{day}.parquet").exists(),
            "net_bars_sha256": None,
            "net_coverage_present": (NET_COVERAGE / f"{day}.json").exists(),
            "net_coverage_sha256": None,
            "net_coverage_manifest_sha256": None,
            "universe_present": (UNIVERSE_RTH / f"{day}.parquet").exists(),
            "universe_sha256": None,
            "candidates_present": (CANDIDATES / f"{day}.json").exists(),
            "candidates_sha256": None,
            "anatomy_present": (ANAT_DIR / f"{day}.jsonl").exists(),
            "anatomy_sha256": None,
            "pit_vintage": v,
            "pit_n": n_pit,
            "n_panel_members": n_members,
            "n_panel_paths": n_paths,
            # emitted from the single source of truth; patch_registry fills them per day
            **dict.fromkeys(REGISTRY_PATCH_COLUMNS),
            "qualified_floor_source": plan["floor_qualified"],
            # built = this stage wrote and hashed the day; canary_member = frozen canary list.
            # Different facts: a full run builds 1,066 days of which 20 are canary members.
            "built": built,
            "canary_member": day in canary_map,
            "canary_reason": sorted(canary_map[day]["reasons"]) if day in canary_map else None,
            "build_status": build_status,
            "notes": None,
        }
        if built:
            for key, path in (
                ("net_trades_sha256", NET_TRADES / f"{day}.parquet"),
                ("net_bars_sha256", NET_BARS / f"{day}.parquet"),
                ("net_coverage_sha256", NET_COVERAGE / f"{day}.json"),
                ("net_coverage_manifest_sha256", NET_COVERAGE / f"{day}{'.manifest.json'}"),
                ("universe_sha256", UNIVERSE_RTH / f"{day}.parquet"),
                ("candidates_sha256", CANDIDATES / f"{day}.json"),
                ("anatomy_sha256", ANAT_DIR / f"{day}.jsonl"),
            ):
                rec[key] = sha256_file(path) if path.exists() else None
        rows.append(rec)
    # infer_schema_length=None: the acquisition fields are None for the first ~1,006 dev days
    # and the first acquisition-bound day sits past the 100-row inference window, so polars
    # would infer Null and then fail appending the first str. Scan the whole registry.
    return pl.DataFrame(rows, infer_schema_length=None)


def sim_block(day: str) -> str:
    return "block1" if day <= "2023-12-31" else "block2"


def build_identities(
    panel: pl.DataFrame, canary_days: list[str]
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """paths + memberships for the canary panel members (collapse keeps one path row)."""
    sub = panel.filter(pl.col("sleeve_day").is_in(canary_days))
    panel_sha = sha256_file(PANEL)
    mem = (
        sub.with_columns(
            (
                pl.col("sleeve_day")
                + "|"
                + pl.col("family")
                + "|"
                + pl.col("ticker")
                + "|"
                + pl.col("entry_et").cast(pl.Utf8)
                + "|"
                + pl.col("entry_rank").cast(pl.Utf8)
            ).alias("member_id")
        )
        .with_columns((pl.col("sleeve_day") + "|" + pl.col("ticker")).alias("path_id"))
        .select(
            [
                "member_id",
                "path_id",
                "sleeve_day",
                "family",
                "ticker",
                "entry_et",
                "entry_rank",
                "entry_px",
                "session_end",
                "month",
                "block",
            ]
        )
        .unique(subset=["member_id"])
        .sort(["sleeve_day", "family", "entry_rank", "ticker"])
        .rename({"sleeve_day": "day"})
        .with_columns(pl.lit(panel_sha).alias("panel_sha256"))
    )
    # panel rows per member (coverage fact)
    bar_counts = sub.group_by(["sleeve_day", "family", "ticker", "entry_et", "entry_rank"]).agg(
        pl.len().alias("member_bar_count")
    )
    mem = mem.join(
        bar_counts.rename({"sleeve_day": "day"}),
        on=["day", "family", "ticker", "entry_et", "entry_rank"],
        how="left",
    )
    paths = (
        mem.group_by(["day", "ticker"])
        .agg(
            pl.col("path_id").first(),
            pl.col("family").unique().sort().alias("families"),
            pl.len().alias("n_memberships"),
            pl.col("member_id").sort().alias("membership_ids"),
            pl.col("entry_et").min().alias("first_entry_et"),
            pl.col("entry_et").max().alias("last_entry_et"),
            pl.col("entry_rank").min().alias("min_entry_rank"),
            pl.col("entry_rank").max().alias("max_entry_rank"),
            pl.col("session_end").first().alias("session_end"),
            pl.col("month").first().alias("month"),
            pl.col("block").first().alias("block"),
        )
        .with_columns(pl.lit(panel_sha).alias("panel_sha256"))
        .sort(["day", "ticker"])
    )
    return paths, mem


def condition_axes(cond_lists: pl.Series, tapes: pl.Series) -> pl.DataFrame:
    """Frozen sip_bars condition table keyed by (conditions-string, tape)."""
    key = pl.DataFrame({"conditions": cond_lists, "tape": tapes}).unique()
    rows = []
    for ck, tp in key.iter_rows():
        conds = list(ck) if ck else []
        oc, hl, v, _unknown = sbar.combine(conds, tp)
        rows.append({"_ck": "|".join(conds), "tape": tp, "oc": oc, "hl": hl, "v": v})
    return pl.DataFrame(rows)


def build_prints(
    day: str,
    tickers: list[str],
    session_end: int,
    first_entry_et: dict[str, int] | None = None,
) -> pl.DataFrame | None:
    """Raw print projection for the selected paths (stored order preserved)."""
    src = NET_TRADES / f"{day}.parquet"
    if not src.exists() or not tickers:
        return None
    df = pl.read_parquet(src)
    df = df.with_row_index("source_row_ordinal")
    df = df.filter(pl.col("symbol").is_in(tickers))
    src_sha = sha256_file(src)
    df = df.with_columns(pl.col("ts_utc").dt.convert_time_zone("America/New_York").alias("ts_et"))
    df = df.with_columns(etm_expr("ts_et").alias("et_min"))
    df = df.with_columns(
        pl.lit(day).alias("day"),
        pl.col("symbol").alias("ticker"),
        (pl.lit(day) + "|" + pl.col("symbol")).alias("path_id"),
        (pl.col("et_min") >= RTH_LO).and_(pl.col("et_min") <= session_end).alias("within_session"),
        (pl.col("et_min") >= PROJECTION_LO)
        .and_(pl.col("et_min") <= PROJECTION_HI)
        .alias("in_projection_window"),
        pl.col("symbol")
        .replace_strict(first_entry_et or {}, default=None, return_dtype=pl.Int32)
        .alias("first_entry_et"),
        pl.lit(src_sha).alias("parent_day_sha256"),
        pl.lit(str(src)).alias("source_path"),
    )
    return df.select(
        [
            "day",
            "ticker",
            "path_id",
            "source_row_ordinal",
            "ts_utc",
            "ts_et",
            "et_min",
            "price",
            "size",
            "exchange",
            "conditions",
            "tape",
            "trade_id",
            "within_session",
            "in_projection_window",
            "first_entry_et",
            "parent_day_sha256",
            "source_path",
        ]
    )


def print_minute_state(prints: pl.DataFrame) -> pl.DataFrame:
    """Per (ticker, et_min) print-state aggregates used by the grid."""
    if prints.height == 0:
        return pl.DataFrame(
            schema={
                "ticker": pl.Utf8,
                "et_min": pl.Int32,
                "n_prints": pl.Int32,
                "n_bar_eligible_prints": pl.Int32,
                "n_excluded_prints": pl.Int32,
                "n_auction_prints": pl.Int32,
                "print_first_ts_us": pl.Int64,
                "print_last_ts_us": pl.Int64,
                "print_last_px": pl.Float64,
            }
        )
    axes = condition_axes(prints["conditions"].to_list(), prints["tape"].to_list())
    t = prints.with_columns(pl.col("conditions").list.join("|").alias("_ck"))
    t = t.join(axes, on=["_ck", "tape"], how="left")
    auction = None
    for code in sbar.AUCTION_CODES:
        c = pl.col("conditions").list.contains(pl.lit(code))
        auction = c if auction is None else (auction | c)
    t = t.with_columns(
        auction.fill_null(False).alias("_auc"),
        ((pl.col("oc") >= 1) | (pl.col("hl") == 2)).alias("_eligible"),
    )
    t = t.with_columns(pl.col("ts_utc").dt.epoch("us").alias("_ts_us"))
    t = t.select(["ticker", "et_min", "_ts_us", "source_row_ordinal", "_eligible", "_auc", "price"])
    t = t.sort(["ticker", "et_min", "_ts_us", "source_row_ordinal"])
    return (
        t.group_by(["ticker", "et_min"], maintain_order=True)
        .agg(
            pl.len().alias("n_prints"),
            pl.col("_eligible").sum().alias("n_bar_eligible_prints"),
            (pl.len() - pl.col("_eligible").sum()).alias("n_excluded_prints"),
            pl.col("_auc").sum().alias("n_auction_prints"),
            pl.col("_ts_us").min().alias("print_first_ts_us"),
            pl.col("_ts_us").max().alias("print_last_ts_us"),
            pl.col("price").last().alias("print_last_px"),
        )
        .with_columns(
            pl.col("n_bar_eligible_prints").cast(pl.Int32),
            pl.col("n_excluded_prints").cast(pl.Int32),
            pl.col("n_auction_prints").cast(pl.Int32),
        )
        .sort(["ticker", "et_min"])
    )


def build_grid(
    day: str,
    tickers: list[str],
    session_end: int,
    prints: pl.DataFrame | None,
    bar_sha: str | None,
    parent_print_sha: str | None,
    projection_sha: str | None,
    first_entry_et: dict[str, int] | None = None,
) -> pl.DataFrame:
    """Complete selected-path minute grid over et 565..965."""
    ets = pl.DataFrame(
        {"et": pl.Series(list(range(PROJECTION_LO, PROJECTION_HI + 1)), dtype=pl.Int32)}
    )
    skel = (
        pl.DataFrame({"ticker": tickers})
        .with_columns((pl.lit(day) + "|" + pl.col("ticker")).alias("path_id"))
        .join(ets, how="cross")
        .with_columns(pl.lit(day).alias("day"))
    )
    bars = (
        pl.read_parquet(NET_BARS / f"{day}.parquet")
        if (NET_BARS / f"{day}.parquet").exists()
        else None
    )
    if bars is not None:
        bars = (
            bars.filter(pl.col("ticker").is_in(tickers))
            .select(["ticker", "et", "open", "high", "low", "close", "volume", "src"])
            .rename(
                {
                    "et": "et",
                    "open": "bar_open",
                    "high": "bar_high",
                    "low": "bar_low",
                    "close": "bar_close",
                    "volume": "bar_volume",
                    "src": "bar_src",
                }
            )
        )
    else:
        bars = pl.DataFrame(
            schema={
                "ticker": pl.Utf8,
                "et": pl.Int32,
                "bar_open": pl.Float64,
                "bar_high": pl.Float64,
                "bar_low": pl.Float64,
                "bar_close": pl.Float64,
                "bar_volume": pl.Float64,
                "bar_src": pl.Utf8,
            }
        )
    grid = skel.join(bars, on=["ticker", "et"], how="left")
    if prints is not None and prints.height:
        pm = print_minute_state(prints).rename({"et_min": "et"})
    else:
        pm = pl.DataFrame(
            schema={
                "ticker": pl.Utf8,
                "et": pl.Int32,
                "n_prints": pl.Int32,
                "n_bar_eligible_prints": pl.Int32,
                "n_excluded_prints": pl.Int32,
                "n_auction_prints": pl.Int32,
                "print_first_ts_us": pl.Int64,
                "print_last_ts_us": pl.Int64,
                "print_last_px": pl.Float64,
            }
        )
    grid = grid.join(pm, on=["ticker", "et"], how="left")
    grid = grid.with_columns(
        pl.col("n_prints").fill_null(0).cast(pl.Int32),
        pl.col("n_bar_eligible_prints").fill_null(0).cast(pl.Int32),
        pl.col("n_excluded_prints").fill_null(0).cast(pl.Int32),
        pl.col("n_auction_prints").fill_null(0).cast(pl.Int32),
        pl.col("bar_src").alias("_bsrc"),
    )
    grid = grid.with_columns(
        pl.when(pl.col("bar_open").is_not_null())
        .then(
            pl.when(pl.col("_bsrc") == "derived").then(pl.lit("raw")).otherwise(pl.lit("provider"))
        )
        .otherwise(pl.lit("none"))
        .alias("bar_state"),
        pl.when(pl.col("n_prints") == 0)
        .then(pl.lit("no_print"))
        .when(pl.col("n_bar_eligible_prints") > 0)
        .then(pl.lit("path_print"))
        .otherwise(pl.lit("excluded_prints_only"))
        .alias("print_state"),
        (pl.col("et") >= RTH_LO).and_(pl.col("et") <= session_end).alias("within_observed_span"),
        # the prospective-read filters, carried on every row: a reader selecting prospectively
        # uses within_session (RTH window), first_entry_et (post-entry only) and t <= now.
        (pl.col("et") >= RTH_LO).and_(pl.col("et") <= session_end).alias("within_session"),
        pl.col("ticker")
        .replace_strict(first_entry_et or {}, default=None, return_dtype=pl.Int32)
        .alias("first_entry_et"),
        pl.lit(session_end, dtype=pl.Int32).alias("session_end"),
        pl.lit(bar_sha).alias("parent_bar_sha256"),
        pl.lit(parent_print_sha).alias("parent_print_sha256"),
        pl.lit(projection_sha).alias("projection_sha256"),
    )
    grid = grid.with_columns(pl.col("bar_state").cast(pl.Utf8))
    return grid.drop("_bsrc")


# --------------------------------------------------------------------------- #
# race board builders
# --------------------------------------------------------------------------- #


def _per_symbol_arrays(df: pl.DataFrame) -> tuple[list[str], list[dict]]:
    """Sorted per-symbol numpy arrays from a (ticker, etm, o,h,l,c,v) frame."""
    df = df.sort(["ticker", "etm"])
    tk = df["ticker"].to_numpy()
    et = df["etm"].to_numpy().astype(np.int64)
    out = []
    starts = np.flatnonzero(np.r_[True, tk[1:] != tk[:-1]])
    ends = np.r_[starts[1:], len(tk)]
    names = []
    for s, e in zip(starts, ends, strict=False):
        names.append(str(tk[s]))
        out.append(
            {
                "et": et[s:e],
                "open": df["open"].to_numpy()[s:e],
                "high": df["high"].to_numpy()[s:e],
                "low": df["low"].to_numpy()[s:e],
                "close": df["close"].to_numpy()[s:e],
                "volume": df["volume"].to_numpy()[s:e],
            }
        )
    return names, out


def _competition_rank(values: np.ndarray, population: np.ndarray) -> np.ndarray:
    """1 + count(greater) inside the population; NaN outside."""
    rank = np.full(values.shape[0], np.nan)
    if population.any():
        ge = values[population]
        s = np.sort(ge)
        gt = s.shape[0] - np.searchsorted(s, ge, side="right")
        rank[population] = gt + 1
    return rank


# Roster keys that classify a PIT name as VERIFIED absent. The producer groups them under an
# `unavailable` object (absent_all_sources / no_rth_in_baseline / no_verified_provider_spelling);
# flat keys are accepted as equivalents. A name in none of these stays an unresolved hole.
VERIFIED_ABSENT_ROSTER_CLASSES = ("zero_bars", "no_rth", "invalid", "unavailable")
# `unavailable.baseline_only_observed` names are PIT names the REPLACED fallback carried that
# the compact source never saw. Under a replace scope the payload alone must still cover them,
# so they are OWED, not verified absent - classifying them as absent is exactly how they
# silently disappeared behind a status=complete day.
ROSTER_OWED_SUBCLASSES = frozenset({"baseline_only_observed"})


def verified_absent_names(day: str) -> dict[str, str]:
    """PIT names the acquisition VERIFIED did not trade: canonical name -> reason class.

    A verified zero-bar / no-RTH / invalid-symbol name is a market fact, not a data hole, and
    must never be counted as a missing confirmed trader. A name absent from every source and
    not classified here remains unresolved and keeps its `raw_roster_missing` state - which
    includes every name in `baseline_only_observed`, because those are owed, not proven absent.
    Only an admitted acquisition day with a local roster file can classify anything.
    """
    key = f"absent:{day}"
    if key in _ACQ_CACHE:
        return _ACQ_CACHE[key]
    out: dict[str, str] = {}
    acq = acquisition_day(day)
    if acq is not None and acq["roster_path"] is not None:
        doc = load_json(acq["roster_path"])
        for cls in VERIFIED_ABSENT_ROSTER_CLASSES:
            block = doc.get(cls)
            if isinstance(block, dict):
                for sub, names in block.items():
                    if sub in ROSTER_OWED_SUBCLASSES:
                        continue  # owed by the acquisition, not verified absent
                    for name in names or []:
                        out[str(name)] = f"{cls}.{sub}"
            else:
                for name in block or []:
                    out[str(name)] = cls
    _ACQ_CACHE[key] = out
    return out


def day_max_per_ticker(ticker: list[str], high: list[float | None]) -> dict[str, float]:
    """Per-ticker MAXIMUM bar high from the provider day file.

    The retrospective `day_high_vs_sip_high_ratio` diagnostic compares the raw lane's day high
    against the provider lane's day high, so it needs the same statistic on both sides. Taking
    the LAST minute's high (what a plain dict(zip(...)) yields) compares a day high against a
    single minute and understates the envelope for every name that rose into the close.
    """
    out: dict[str, float] = {}
    for t, h in zip(ticker, high, strict=True):
        if h is None or not np.isfinite(h):
            continue
        cur = out.get(t)
        if cur is None or h > cur:
            out[t] = float(h)
    return out


def rank_eligibility_mask(
    known: np.ndarray,
    px_flat: np.ndarray,
    prev_rep: np.ndarray,
    gain: np.ndarray,
    prevclose_discrepancy: np.ndarray,
    reference_unqualified: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(eligible, nonpositive_px, extreme_gain, prevclose_excluded, reference_excluded).

    The quality filters are exactly the declared ones: a nonpositive price or a nonpositive
    denominator, a suspect split/bad-print gain, a PREVIOUS-SESSION denominator discrepancy
    confirmed above `PREVCLOSE_HARD_WARN`, and an UNQUALIFIED REFERENCE - a stale or
    floor-qualified previous close. Both of the last two are known before the session opens, so
    they are legitimate causal filters; nothing derived from the session's own future (the day
    envelope) may enter here.

    The reference filter matters because the contract forbids a floor-qualified or stale
    denominator from supporting a rank-trajectory or era-stability claim. 2025-02-03 is the live
    case: its prior reference is 2023-12-29, since 2025-01-31 is sealed and is never read, so the
    reference is a known-stale cross-block stand-in. Its RAW px/gain and `rank_unfiltered` remain
    stored as reference-only; only the quality-filtered rank family is nulled. No row is dropped
    and no sealed file is fetched.
    """
    nonpos = ((~np.isnan(px_flat)) & (px_flat <= 0)) | (prev_rep <= 0)
    extreme = (~np.isnan(gain)) & ((gain > SUSPECT_GAIN_HI) | (gain < SUSPECT_GAIN_LO))
    bad_denom = prevclose_discrepancy & (~np.isnan(prev_rep))
    bad_ref = (
        np.asarray(reference_unqualified, dtype=bool)
        if reference_unqualified is not None
        else np.zeros(known.shape, dtype=bool)
    )
    eligible = known & (~nonpos) & (~extreme) & (~np.isnan(prev_rep)) & (~bad_denom) & (~bad_ref)
    return eligible, nonpos, extreme, bad_denom, bad_ref


# Unobserved PIT names split FOUR ways, and the wording is load-bearing. NO CLASS HERE IS A
# CLAIM ABOUT THE MARKET. An absent bar is not evidence of an absent trade, and nothing infers
# "did not trade": on 2025-02-03 DXR printed 24 times / 501 shares on the SIP TRADE endpoint
# while the SIP minute-BAR pull returned zero - the same name, the same session, and a bar count
# of zero. Data was uncaptured, not silent.
#
# CLASS_VERIFIED_ABSENT means exactly "no bar observed in the DECLARED STORED BAR SOURCES, or
# the spelling is unrequestable" - nothing more. A syntax-unrequestable spelling is a separate
# concern and is filtered out before this split (see ROSTER_OWED_SUBCLASSES / the invalid class).
CLASS_VERIFIED_ABSENT = "verified_no_bar_in_declared_sources"  # not observed in the stored bars
CLASS_NO_SIP_BAR = "verified_no_sip_bar"  # SIP accepted the spelling, returned no minute bar
CLASS_CROSSFEED_WITNESS = "cross_feed_witnessed_no_sip_bar"  # a NON-SIP source HAS the bar
CLASS_UNRESOLVED = "missing_confirmed_same_feed_bar"  # genuinely unexplained; only this is a hole


def unobserved_classification(day: str, unobserved: set[str]) -> dict[str, str]:
    """Classify every PIT name with zero SIP rows, into the four declared classes.

    Every class describes what was OBSERVED, never what happened in the market. None of them
    licenses a "did not trade" inference: a name can print on the trade endpoint and still have
    no minute bar. `verified_no_sip_bar` and `cross_feed_witnessed_no_sip_bar` are SIP-COVERAGE
    facts; `verified_no_bar_in_declared_sources` means only that the declared stored bar sources
    carry no bar (or the spelling is unrequestable, which is filtered before this split). A
    witnessed cross-feed zero means a RETROSPECTIVE non-SIP proxy preserved the bar - the same
    name, same session, minute bar 0 - so it is not a missing same-feed bar and is explicitly not
    a SIP rank input. Only `missing_confirmed_same_feed_bar` counts as a hole.
    """
    out: dict[str, str] = {}
    absent = verified_absent_names(day)
    no_sip = verified_no_sip_bar_names(day)
    witnessed = cross_feed_witness_names(day)
    # PRECEDENCE MATTERS. Every cross-feed-witnessed name is ALSO an API zero - the SIP pull
    # returning no bar is exactly WHY it was witnessed - so testing `no_sip` first would make the
    # cross-feed class unreachable and its count permanently 0. The more specific classification
    # wins: a name with a preserved non-SIP bar is WITNESSED, not merely an API zero. The API
    # request accounting is kept separately by counting set MEMBERSHIP, not class membership.
    for nm in unobserved:
        if nm in absent:
            out[nm] = CLASS_VERIFIED_ABSENT
        elif nm in witnessed:
            out[nm] = CLASS_CROSSFEED_WITNESS
        elif nm in no_sip:
            out[nm] = CLASS_NO_SIP_BAR
        else:
            out[nm] = CLASS_UNRESOLVED
    return out


def _manifest_doc(day: str) -> dict:
    binding = joint_day_binding(day)
    if binding is None or not binding["manifest"].exists():
        return {}
    return load_json(binding["manifest"]) or {}


def verified_no_sip_bar_names(day: str) -> set[str]:
    """Names the SIP provider ACCEPTED and returned zero minute bars for.

    This is a REQUEST/COVERAGE axis, not a market fact: the name is never relabelled a
    non-trader. Read from the manifest's `sip_coverage` block (the REQUEST axis) and the legacy
    flat `symbols_zero_bars`.
    """
    key = f"nosip:{day}"
    if key in _ACQ_CACHE:
        return _ACQ_CACHE[key]
    man = _manifest_doc(day)
    sip = man.get("sip_coverage") or {}

    # `sip_coverage.symbols_zero_bars` is a COUNT on the real manifests and the NAME LIST is the
    # top-level `symbols_zero_bars`; accept either shape but never iterate a count.
    def _names(v: object) -> set[str]:
        if isinstance(v, (list, tuple, set)):
            return {str(x) for x in v}
        return set()

    names = _names(man.get("symbols_zero_bars")) | _names(sip.get("symbols_zero_bars"))
    _ACQ_CACHE[key] = names
    return _ACQ_CACHE[key]


def cross_feed_witness_names(day: str) -> set[str]:
    """PIT names whose bar a NON-SIP source preserved but the SIP pull did not reproduce.

    Deliberately NOT in the roster's `unavailable` groups: nothing here says the name did not
    trade - a non-SIP source has the bar. This is retrospective proxy evidence, never a SIP rank
    input, so it removes the name from the unresolved same-feed hole without claiming a market
    fact.
    """
    key = f"witnessed:{day}"
    if key in _ACQ_CACHE:
        return _ACQ_CACHE[key]
    man = _manifest_doc(day)
    ec = man.get("evidence_crosscheck") or {}
    ob = man.get("obligation") or {}
    names = set(ec.get("cross_feed_names") or ob.get("cross_feed_witness_names") or [])
    for row in ec.get("cross_feed_disagreements") or []:
        if isinstance(row, dict) and row.get("ticker"):
            names.add(str(row["ticker"]))
    _ACQ_CACHE[key] = {str(n) for n in names}
    return _ACQ_CACHE[key]


def build_minute_board(
    day: str,
    session_end: int,
    prov: dict,
    prev_map: dict[str, float],
    pit_set: frozenset,
    src_sha: str,
    source_kind: str,
    era: str,
    universe_symbols: set[str],
    raw: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, dict]:
    """Full raw/provider minute race board for one day (no top-K truncation)."""
    if raw is None:
        raw = read_raw_day(day, session_end)
    n_all_syms = int(raw["ticker"].n_unique())
    # FULL-WINDOW coverage population: every name with ANY admitted bar in the declared window,
    # INCLUDING off-hours-only names. This is deliberately NOT the board population - the board
    # stays RTH-trimmed below - but deriving coverage from the trimmed frame manufactured false
    # gaps (2025-02-03 NTZ: one bar at etm 961, 16:01 ET, PIT-present, dropped entirely).
    observed_full_window = read_coverage_names(day)
    raw = raw.filter(pl.col("ticker").is_in(list(pit_set)))
    names, arrays = _per_symbol_arrays(raw)
    del raw
    board_observed = set(names)
    absent = verified_absent_names(day)
    # A name is unobserved only if NO admitted bar exists anywhere in the declared window. A
    # name with bars only outside RTH is observed for COVERAGE and is not a missing-trader gap;
    # it simply contributes no board row, which is a different, reported quantity.
    unobserved = pit_set - observed_full_window
    # an invalid/inaccessible symbol is not PIT-listed, so it is not an observation gap at all
    unobserved -= {n for n in unobserved if absent.get(n) == "invalid"}
    # a STALE or floor-qualified previous close cannot support a rank-trajectory or
    # era-stability claim (contract floor_rule). Session-level, so it broadcasts to the rows;
    # raw px/gain and rank_unfiltered stay stored as reference-only.
    ref_unqualified = bool(prov.get("prev_close_stale")) or bool(
        prov.get("prev_close_floor_qualified")
    )
    cls_of = unobserved_classification(day, unobserved)
    _nosip = verified_no_sip_bar_names(day)
    _wit = cross_feed_witness_names(day)
    no_sip_count = sum(1 for n in unobserved if n in _nosip)
    no_sip_and_witnessed = sum(1 for n in unobserved if n in _nosip and n in _wit)
    by_class: dict[str, list[str]] = {}
    for nm, c in cls_of.items():
        by_class.setdefault(c, []).append(nm)
    # ONLY the same-feed hole is a hole. A SIP zero and a cross-feed-witnessed zero are COVERAGE
    # facts and are reported as such - neither is ever called a non-trader.
    unresolved = set(by_class.get(CLASS_UNRESOLVED, []))
    stats = {
        "raw_n_symbols": n_all_syms,
        "raw_n_pit_symbols": len(board_observed),
        "raw_n_pit_symbols_full_window": len(pit_set & observed_full_window),
        "raw_n_pit_offhours_only": len(observed_full_window & pit_set) - len(board_observed),
        "raw_roster_missing_n": len(unobserved),
        "raw_roster_verified_absent_n": len(by_class.get(CLASS_VERIFIED_ABSENT, [])),
        # the SIP REQUEST axis, counted by set membership so a witnessed name is still counted
        # as an API zero even though it is classified as witnessed
        "raw_roster_verified_no_sip_bar_n": no_sip_count,
        "raw_roster_no_sip_bar_also_witnessed_n": no_sip_and_witnessed,
        "raw_roster_cross_feed_witnessed_n": len(by_class.get(CLASS_CROSSFEED_WITNESS, [])),
        "raw_roster_missing_unresolved_n": len(unresolved),
        "raw_roster_absent_classes": {
            c: sum(1 for n in unobserved if absent.get(n) == c)
            for c in sorted({v for v in absent.values() if v})
        },
        "raw_roster_unobserved_classes": {c: len(v) for c, v in sorted(by_class.items())},
        "prev_close_reference_unqualified": ref_unqualified,
        "n_reference_unqualified_excluded": 0,
    }
    if not names:
        return pl.DataFrame(), stats
    ts = np.arange(RTH_LO, session_end + 1, dtype=np.int64)
    n_sym, n_t = len(names), ts.shape[0]
    day_high = np.array(
        [float(np.nanmax(a["high"])) if a["high"].size else np.nan for a in arrays],
        dtype=np.float64,
    )
    prev_close = np.array([prev_map.get(nm, np.nan) for nm in names], dtype=np.float64)
    px = np.full((n_sym, n_t), np.nan)
    px_et = np.full((n_sym, n_t), np.nan)
    for i, a in enumerate(arrays):
        idx = np.searchsorted(a["et"], ts - 1, side="right") - 1
        ok = idx >= 0
        px[i, ok] = a["close"][idx[ok]]
        px_et[i, ok] = a["et"][idx[ok]]
    grid_t = np.tile(ts, n_sym)
    sym_rep = np.repeat(np.array(names, dtype=object), n_t)
    prev_rep = np.repeat(prev_close, n_t)
    px_flat = px.reshape(-1)
    px_et_flat = px_et.reshape(-1)
    age_flat = np.where(np.isnan(px_et_flat), np.nan, grid_t - px_et_flat)
    gain = np.where(np.isnan(px_flat) | np.isnan(prev_rep), np.nan, px_flat / prev_rep - 1.0)
    known = ~np.isnan(px_flat)
    fresh = known & (age_flat <= 2)
    # The previous-session denominator audit is CAUSAL (the prior session's close is known
    # before this session opens), so it must be resolved BEFORE the rank population is built:
    # a name whose stored denominator is confirmed off the provider's prior close by more than
    # the declared hard warning cannot support a comparable rank, and the quality-filtered rank
    # family is nulled for it. Raw px/gain and the unfiltered rank below are preserved.
    sip_high: dict[str, float] = {}
    sip_clast: dict[str, float] = {}
    bars_path = NET_BARS / f"{day}.parquet"
    if bars_path.exists():
        b = pl.read_parquet(bars_path, columns=["ticker", "high"])
        sip_high = day_max_per_ticker(b["ticker"].to_list(), b["high"].to_list())
    prev_day = prov.get("prev_close_day")
    if prev_day:
        up = UNIVERSE_RTH / f"{prev_day}.parquet"
        if up.exists():
            u = pl.read_parquet(up, columns=["symbol", "c_last"])
            sip_clast = dict(zip(u["symbol"].to_list(), u["c_last"].to_list(), strict=False))
    sip_high_flat = np.array([sip_high.get(nm, np.nan) for nm in names], dtype=np.float64)
    sip_clast_flat = np.array([sip_clast.get(nm, np.nan) for nm in names], dtype=np.float64)
    ratio_pc = np.where(
        (sip_clast_flat > 0) & (~np.isnan(prev_close)), prev_close / sip_clast_flat, np.nan
    )
    # one flag per NAME (the denominator is a session-level quantity), broadcast to its rows
    prevclose_discrepancy = np.repeat(
        (~np.isnan(ratio_pc)) & (np.abs(ratio_pc - 1.0) > PREVCLOSE_HARD_WARN), n_t
    )
    reference_unqualified = np.full(px_flat.shape, ref_unqualified)
    eligible, nonpos_flat, extreme, prevclose_excluded, reference_excluded = rank_eligibility_mask(
        known, px_flat, prev_rep, gain, prevclose_discrepancy, reference_unqualified
    )
    stats["n_reference_unqualified_excluded"] = int(reference_excluded.sum())
    rank_known = np.full(px_flat.shape, np.nan)
    rank_fresh = np.full(px_flat.shape, np.nan)
    slot = np.full(px_flat.shape, np.nan)
    n_known = np.zeros(n_t, dtype=np.int32)
    n_fresh = np.zeros(n_t, dtype=np.int32)
    g_mat, e_mat, f_mat = (
        gain.reshape(n_sym, n_t),
        eligible.reshape(n_sym, n_t),
        fresh.reshape(n_sym, n_t),
    )
    rk_mat, rf_mat, sl_mat = (
        rank_known.reshape(n_sym, n_t),
        rank_fresh.reshape(n_sym, n_t),
        slot.reshape(n_sym, n_t),
    )
    name_arr = np.array(names, dtype=object)
    for c in range(n_t):
        ec = e_mat[:, c]
        if not ec.any():
            continue
        g = g_mat[:, c]
        n_known[c] = int(ec.sum())
        rk_mat[ec, c] = _competition_rank(g, ec)[ec]
        fc = f_mat[:, c] & ec
        n_fresh[c] = int(fc.sum())
        if fc.any():
            rf_mat[fc, c] = _competition_rank(g, fc)[fc]
        order = np.lexsort((name_arr[ec], -g[ec]))
        sl_mat[np.flatnonzero(ec)[order], c] = np.arange(1, int(ec.sum()) + 1)
    # The UNFILTERED competition rank, over every name with a known price and a positive
    # denominator, ignoring the quality filters. It is what a reader uses to see the race a
    # filtered-out name would have had, so a quality decision never destroys the ordering.
    unfiltered_pop = known & (~np.isnan(prev_rep)) & (prev_rep > 0)
    u_mat = unfiltered_pop.reshape(n_sym, n_t)
    rank_unfiltered = np.full(px_flat.shape, np.nan)
    ru_mat = rank_unfiltered.reshape(n_sym, n_t)
    n_unfiltered = np.zeros(n_t, dtype=np.int32)
    for c in range(n_t):
        uc = u_mat[:, c]
        if not uc.any():
            continue
        n_unfiltered[c] = int(uc.sum())
        ru_mat[uc, c] = _competition_rank(g_mat[:, c], uc)[uc]
    df = pl.DataFrame(
        {
            "day": [day] * px_flat.shape[0],
            "t": grid_t.astype(np.int32),
            "ticker": list(sym_rep),
            "px": pl.Series("px", px_flat).fill_nan(None),
            "px_et": pl.Series("px_et", px_et_flat).fill_nan(None).cast(pl.Int32),
            "age_min": pl.Series("age_min", age_flat).fill_nan(None).cast(pl.Int32),
            "known_by_t": known,
            "fresh_2m": fresh,
            "prev_close": pl.Series("prev_close", prev_rep).fill_nan(None),
            "gain": pl.Series("gain", gain).fill_nan(None),
            "rank_eligible": eligible,
            "rank_known": pl.Series("rank_known", rank_known).fill_nan(None).cast(pl.Int32),
            "rank_fresh_2m": pl.Series("rank_fresh_2m", rank_fresh).fill_nan(None).cast(pl.Int32),
            "order_slot": pl.Series("order_slot", slot).fill_nan(None).cast(pl.Int32),
            "n_known": np.tile(n_known, n_sym),
            "n_fresh_2m": np.tile(n_fresh, n_sym),
            "n_eligible": np.tile(n_known, n_sym),
            "n_unfiltered": np.tile(n_unfiltered, n_sym),
            "rank_unfiltered": pl.Series("rank_unfiltered", rank_unfiltered)
            .fill_nan(None)
            .cast(pl.Int32),
            "flag_nonpositive_px": nonpos_flat,
            "flag_extreme_gain": extreme,
            "flag_prevclose_discrepancy": prevclose_excluded,
            "day_high_raw": pl.Series("day_high_raw", np.repeat(day_high, n_t)).fill_nan(None),
            "sip_high_day": pl.Series("sip_high_day", np.repeat(sip_high_flat, n_t)).fill_nan(None),
            "prevclose_vs_sip_clast_ratio": pl.Series(
                "prevclose_vs_sip_clast_ratio", np.repeat(ratio_pc, n_t)
            ).fill_nan(None),
        }
    )
    plan = day_lane_plan(day)
    acq_manifest_sha = (plan["acquisition"] or {}).get("manifest_sha256")
    alias_map_sha = plan["alias_map_sha256"]

    df = df.with_columns(
        pl.concat_list(
            [
                pl.when(pl.col("flag_nonpositive_px"))
                .then(pl.lit("nonpositive_px"))
                .otherwise(None),
                pl.when(pl.col("flag_extreme_gain")).then(pl.lit("extreme_gain")).otherwise(None),
                pl.when(pl.col("flag_prevclose_discrepancy"))
                .then(pl.lit("prevclose_denominator_discrepancy"))
                .otherwise(None),
            ]
        )
        .list.drop_nulls()
        .alias("quality_flags"),
        (pl.col("day_high_raw") / pl.col("sip_high_day")).alias("day_high_vs_sip_high_ratio"),
    )
    df = df.with_columns(
        pl.when(pl.col("day_high_vs_sip_high_ratio").is_null())
        .then(pl.lit(False))
        .otherwise(
            (pl.col("day_high_vs_sip_high_ratio") < ENVELOPE_LO)
            | (pl.col("day_high_vs_sip_high_ratio") > ENVELOPE_HI)
        )
        .alias("day_envelope_warning"),
        pl.when(pl.col("prevclose_vs_sip_clast_ratio").is_null())
        .then(pl.lit(False))
        .otherwise((pl.col("prevclose_vs_sip_clast_ratio") - 1).abs() > PREVCLOSE_HARD_WARN)
        .alias("prevclose_warning"),
    )
    df = df.with_columns(
        pl.lit(source_kind).alias("source"),
        pl.lit(era).alias("feed_era"),
        pl.lit("clean_fallback" in source_kind).alias("qualified_floor_source"),
        pl.lit("acquired_day" in source_kind).alias("acquisition_used"),
        pl.lit(acq_manifest_sha).alias("acquisition_manifest_sha256"),
        pl.lit(alias_map_sha).alias("alias_map_sha256"),
        pl.lit(True).alias("pit_listed"),
        pl.lit("observed_in_raw").alias("roster_state"),
        pl.col("ticker").is_in(list(universe_symbols)).alias("in_universe_file"),
        pl.lit(day[:7]).alias("month"),
        pl.lit(session_end, dtype=pl.Int32).alias("session_end"),
        pl.lit(src_sha).alias("source_sha256"),
        pl.lit(prov.get("prev_close_day")).alias("prev_close_day"),
        pl.lit(prov.get("prev_close_source")).alias("prev_close_source"),
        pl.col("ticker")
        .replace_strict(
            prov.get("prev_close_et_by_name") or {}, default=None, return_dtype=pl.Int32
        )
        .alias("prev_close_et"),
        pl.lit(prov.get("prev_close_age_sessions"), dtype=pl.Int32).alias(
            "prev_close_age_sessions"
        ),
        pl.lit(prov.get("prev_close_gap_days"), dtype=pl.Int32).alias("prev_close_gap_days"),
        pl.lit(bool(prov.get("prev_close_floor_qualified"))).alias("prev_close_floor_qualified"),
        pl.lit(prov.get("prev_close_lane_kind")).alias("prev_close_lane_kind"),
        pl.lit(prov.get("prev_close_lane_sha256")).alias("prev_close_lane_sha256"),
        pl.lit(bool(prov.get("prev_close_acquired"))).alias("prev_close_acquired"),
        pl.lit(bool(prov.get("prev_close_stale"))).alias("prev_close_stale"),
        pl.lit(bool(prov.get("prev_close_sealed_prior"))).alias("prev_close_sealed_prior"),
        pl.when(pl.col("rank_eligible"))
        .then(
            pl.when(pl.col("rank_fresh_2m").is_not_null())
            .then(pl.lit(["all_known", "fresh_2m"]))
            .otherwise(pl.lit(["all_known"]))
        )
        .otherwise(pl.lit([]))
        .cast(pl.List(pl.Utf8))
        .alias("population_def"),
    )
    return df.sort(
        ["day", "t", "rank_eligible", "gain", "ticker"],
        descending=[False, False, True, True, False],
    ), stats


def build_checkpoint_board(day: str, session_end: int, pit_set: frozenset) -> pl.DataFrame:
    """Causal + retrospective full-roster checkpoint views (12 clocks)."""
    p = UNIVERSE_RTH / f"{day}.parquet"
    if not p.exists():
        return pl.DataFrame()
    u = pl.read_parquet(p).with_columns(pl.col("symbol").cast(pl.Utf8))
    src_sha = sha256_file(p)
    symbols = sorted(set(u["symbol"].to_list()) | set(pit_set))
    keep = (
        [
            "symbol",
            "o570",
            "hi",
            "lo",
            "c_last",
            "vol",
            "n_bars",
            "first_et",
            "last_et",
            "delayed_open",
        ]
        + [f"px_{T}" for T in B_CLOCKS]
        + [f"px_{T}_et" for T in B_CLOCKS]
    )
    keep = [c for c in keep if c in u.columns]
    base = (
        pl.DataFrame({"symbol": symbols})
        .join(u.select(keep), on="symbol", how="left")
        .with_columns(
            pl.lit(day).alias("day"), pl.lit(session_end, dtype=pl.Int32).alias("session_end")
        )
    )
    frames = []
    for tck in B_CLOCKS:
        f = base.select(
            [
                "day",
                pl.lit(tck, dtype=pl.Int32).alias("T"),
                "symbol",
                "session_end",
                pl.col("o570"),
                pl.col(f"px_{tck}").alias("px_T"),
                pl.col(f"px_{tck}_et").cast(pl.Int32).alias("px_T_et"),
            ]
        )
        f = f.with_columns(
            pl.col("px_T").is_not_null().alias("known_by_t"),
            (pl.lit(tck, dtype=pl.Int32) - pl.col("px_T_et")).alias("age_min"),
            pl.when((pl.col("o570") > 0) & (pl.col("px_T").is_not_null()))
            .then(pl.col("px_T") / pl.col("o570") - 1)
            .otherwise(None)
            .alias("score_open"),
        )
        vals = f["score_open"].to_numpy()
        elig = (~np.isnan(vals)) & np.isfinite(vals)
        rk = _competition_rank(np.where(elig, vals, np.nan), elig)
        syms = np.array(f["symbol"].to_list(), dtype=object)
        eidx = np.flatnonzero(elig)
        sl = np.full(vals.shape, np.nan)
        if eidx.shape[0]:
            order = np.lexsort((syms[eidx], -vals[eidx]))
            sl[eidx[order]] = np.arange(1, eidx.shape[0] + 1)
        f = f.with_columns(
            pl.Series("rank_open", [None if np.isnan(v) else int(v) for v in rk.tolist()]).cast(
                pl.Int32
            ),
            pl.Series(
                "order_slot_open", [None if np.isnan(v) else int(v) for v in sl.tolist()]
            ).cast(pl.Int32),
            pl.Series("n_eligible_open", np.full(vals.shape, int(eidx.shape[0]), dtype=np.int32)),
        )
        frames.append(f)
    cp = pl.concat(frames)
    retro = u.select(
        [
            c
            for c in [
                "symbol",
                "hi",
                "lo",
                "c_last",
                "vol",
                "n_bars",
                "first_et",
                "last_et",
                "delayed_open",
            ]
            if c in u.columns
        ]
    )
    cp = cp.join(retro, on="symbol", how="left")
    cp = cp.with_columns(
        pl.when((pl.col("o570") > 0) & (pl.col("hi").is_not_null()))
        .then(pl.col("hi") / pl.col("o570") - 1)
        .otherwise(None)
        .alias("ret_hi_open"),
        pl.when((pl.col("o570") > 0) & (pl.col("c_last").is_not_null()))
        .then(pl.col("c_last") / pl.col("o570") - 1)
        .otherwise(None)
        .alias("ret_clast_open"),
    )
    retro_ranks, retro_n = [], []
    for tck in B_CLOCKS:
        sub = cp.filter(pl.col("T") == tck)
        vals = sub["ret_hi_open"].to_numpy()
        elig = (~np.isnan(vals)) & np.isfinite(vals)
        rk = _competition_rank(np.where(elig, vals, np.nan), elig)
        retro_ranks.extend([None if np.isnan(v) else int(v) for v in rk.tolist()])
        retro_n.append(np.full(vals.shape, int(elig.sum()), dtype=np.int32))
    cp = cp.with_columns(
        pl.Series("rank_hi_open", retro_ranks).cast(pl.Int32),
        pl.Series("n_eligible_retro", np.concatenate(retro_n)),
    )
    cp = cp.with_columns(
        pl.lit(day[:7]).alias("month"),
        pl.lit(sim_block(day)).alias("block"),
        pl.col("symbol").is_in(list(pit_set)).alias("pit_listed"),
        pl.col("symbol").is_in(u["symbol"].to_list()).alias("row_present"),
        pl.when(~pl.col("symbol").is_in(list(pit_set)))
        .then(pl.lit("pit_unobserved"))
        .when(pl.col("known_by_t"))
        .then(pl.lit("observed_by_t"))
        .otherwise(pl.lit("not_observed_by_t"))
        .alias("roster_state"),
        pl.lit("sip_universe_rth").alias("source"),
        pl.lit(src_sha).alias("source_sha256"),
        pl.lit("checkpoint_v0").alias("population_def"),
    )
    return cp.select(
        [
            "day",
            "T",
            "symbol",
            "session_end",
            "o570",
            "px_T",
            "px_T_et",
            "age_min",
            "known_by_t",
            "score_open",
            "rank_open",
            "n_eligible_open",
            "order_slot_open",
            "population_def",
            "row_present",
            "pit_listed",
            "roster_state",
            "hi",
            "lo",
            "c_last",
            "vol",
            "n_bars",
            "first_et",
            "last_et",
            "delayed_open",
            "ret_hi_open",
            "rank_hi_open",
            "ret_clast_open",
            "n_eligible_retro",
            "source",
            "source_sha256",
            "month",
            "block",
        ]
    ).sort(["day", "T", "symbol"])


def build_candidate_net(day: str, session_end: int) -> pl.DataFrame:
    bars_path = NET_BARS / f"{day}.parquet"
    if not bars_path.exists():
        return pl.DataFrame()
    bars = pl.read_parquet(
        bars_path, columns=["ticker", "et", "open", "high", "low", "close", "volume"]
    )
    cand_path = CANDIDATES / f"{day}.json"
    cand = load_json(cand_path)
    cov_path = NET_COVERAGE / f"{day}.json"
    cov = load_json(cov_path) if cov_path.exists() else {}
    per_symbol = cov.get("per_symbol", {}) if isinstance(cov, dict) else {}
    names = set(bars["ticker"].to_list())
    # per-name snapshot schedule, so each row carries ONLY the rules selectable at its own t. A
    # whole-day rule list discloses a later snapshot's membership to an earlier instant, which is
    # the prefix violation this replaces.
    roster_first: dict[str, int] = {}
    roster_schedule: dict[str, list[tuple[int, str]]] = {}
    for snap in cand["snapshots"]:
        pop, snap_t = snap["pop"], int(snap["T"])
        selectable = snap_t + 1 if pop == "A_open" else snap_t
        ids = [r["symbol"] for r in snap.get("top", [])] + list(snap.get("margin", []))
        for s in ids:
            names.add(s)
            roster_first[s] = min(roster_first.get(s, selectable), selectable)
            roster_schedule.setdefault(s, []).append((selectable, f"{pop}@{snap_t}"))
    for w in cand.get("winners_open", []):
        names.add(w["symbol"])
    for w in cand.get("winners_prev", []):
        names.add(w["symbol"])
    names = sorted(names)
    net_set = set(cand.get("net", []))
    w_open = {w["symbol"] for w in cand.get("winners_open", [])}
    w_prev = {w["symbol"] for w in cand.get("winners_prev", [])}
    ts = np.arange(RTH_LO, session_end + 1)
    sub = bars.sort(["ticker", "et"]).filter(pl.col("ticker").is_in(names))
    tick = sub["ticker"].to_numpy()
    et = sub["et"].to_numpy()
    starts = np.flatnonzero(np.r_[True, tick[1:] != tick[:-1]])
    ends = np.r_[starts[1:], len(tick)]
    arrays = {}
    for s, e in zip(starts, ends, strict=False):
        arrays[str(tick[s])] = (et[s:e].astype(np.int64), sub["close"].to_numpy()[s:e])
    n_sym, n_t = len(names), ts.shape[0]
    px = np.full((n_sym, n_t), np.nan)
    px_et = np.full((n_sym, n_t), np.nan)
    last_et = np.full(n_sym, np.nan)
    n_bars = np.zeros(n_sym, dtype=np.int32)
    for i, nm in enumerate(names):
        a = arrays.get(nm)
        if a is None:
            continue
        idx = np.searchsorted(a[0], ts - 1, side="right") - 1
        ok = idx >= 0
        px[i, ok] = a[1][idx[ok]]
        px_et[i, ok] = a[0][idx[ok]]
        last_et[i] = a[0][-1]
        n_bars[i] = a[0].shape[0]
    grid_t = np.tile(ts, n_sym)
    sym_rep = np.repeat(np.array(names, dtype=object), n_t)
    first_sel = np.array([roster_first.get(nm, np.nan) for nm in names], dtype=np.float64)
    # rules_mat[i][c] = the rules selectable at ts[c] for name i: only snapshots whose
    # selectable_asof_et <= t appear, so adding a LATER snapshot cannot alter an EARLIER row.
    rules_mat: list[list[list[str]]] = []
    for nm in names:
        sched = sorted(roster_schedule.get(nm, []))
        rows_for_name: list[list[str]] = []
        acc: list[str] = []
        k = 0
        for t_val in ts:
            while k < len(sched) and sched[k][0] <= int(t_val):
                acc.append(sched[k][1])
                k += 1
            rows_for_name.append(list(acc))
        rules_mat.append(rows_for_name)
    known = ~np.isnan(px.reshape(-1))

    def _nulls(a: np.ndarray):
        return [None if v != v else int(v) for v in a.tolist()]

    df = pl.DataFrame(
        {
            "day": [day] * (n_sym * n_t),
            "t": grid_t.astype(np.int32),
            "ticker": list(sym_rep),
            "px": pl.Series("px", px.reshape(-1)).fill_nan(None),
            "px_et": _nulls(px_et.reshape(-1)),
            "known_by_t": known,
            "roster_first_selectable_et": _nulls(np.repeat(first_sel, n_t)),
            "roster_rules": pl.Series(
                "roster_rules",
                [rules_mat[i][c] for i in range(n_sym) for c in range(n_t)],
                dtype=pl.List(pl.Utf8),
            ),
            "in_net_full_day": np.repeat(np.array([nm in net_set for nm in names]), n_t),
            "in_winners_open": np.repeat(np.array([nm in w_open for nm in names]), n_t),
            "in_winners_prev": np.repeat(np.array([nm in w_prev for nm in names]), n_t),
            "net_coverage_class": np.repeat(
                np.array(
                    [per_symbol.get(nm, {}).get("cls", "not_acquired") for nm in names],
                    dtype=object,
                ),
                n_t,
            ),
            "day_last_et": _nulls(np.repeat(last_et, n_t)),
            "day_n_bars": np.repeat(n_bars, n_t),
        }
    )
    df = df.with_columns(pl.col("px_et").cast(pl.Int32))
    df = df.with_columns((pl.col("t") - pl.col("px_et")).alias("age_min").cast(pl.Int32))
    df = df.with_columns(
        pl.when(pl.col("roster_first_selectable_et").is_null())
        .then(pl.lit(False))
        .otherwise(pl.col("roster_first_selectable_et") <= pl.col("t"))
        .alias("in_prospective_roster")
    )
    df = df.with_columns(
        pl.col("day_last_et").cast(pl.Int32),
        pl.col("day_n_bars").cast(pl.Int32),
        pl.lit(session_end, dtype=pl.Int32).alias("session_end"),
        pl.lit("sip_net_bars").alias("source"),
        pl.lit(sha256_file(bars_path)).alias("source_sha256"),
        pl.lit(day[:7]).alias("month"),
        pl.lit(sim_block(day)).alias("block"),
    )
    return df.sort(["day", "t", "ticker"])


def build_quote_rosters(day: str) -> pl.DataFrame:
    cand_path = CANDIDATES / f"{day}.json"
    if not cand_path.exists():
        return pl.DataFrame()
    cand = load_json(cand_path)
    src_sha = sha256_file(cand_path)
    existing: dict[str, int] = {}
    qp = NET_QUOTES / f"{day}.parquet"
    if qp.exists():
        q = pl.read_parquet(qp, columns=["symbol"])
        vc = q["symbol"].value_counts()
        existing = dict(zip(vc["symbol"].to_list(), vc["count"].to_list(), strict=False))
    rows = []
    union_first: dict[str, int] = {}
    for snap in cand["snapshots"]:
        pop, snap_t = snap["pop"], int(snap["T"])
        selectable = snap_t + 1 if pop == "A_open" else snap_t
        k = B_TOP_K if pop == "B" else A_TOP_K
        for r in snap.get("top", [])[:k]:
            union_first[r["symbol"]] = min(union_first.get(r["symbol"], selectable), selectable)
            rows.append(
                {
                    "day": day,
                    "roster_rule": ROSTER_RULE_ID,
                    "snapshot_pop": pop,
                    "snapshot_T": snap_t,
                    "snapshot_asof_et": snap_t,
                    "selectable_asof_et": selectable,
                    "rank": int(r["rank"]),
                    "symbol": r["symbol"],
                    "score": (None if r.get("score") is None else float(r["score"])),
                    "source_path": str(cand_path),
                    "source_sha256": src_sha,
                    "month": day[:7],
                    "block": sim_block(day),
                }
            )
    df = pl.DataFrame(rows)
    df = df.with_columns(
        pl.col("symbol")
        .replace_strict(union_first, default=None, return_dtype=pl.Int32)
        .alias("day_union_first_selectable_et"),
        pl.lit(True).alias("union_member"),
        pl.col("symbol").is_in(list(existing.keys())).alias("existing_quote_lane_present"),
        pl.col("symbol")
        .replace_strict(existing, default=0, return_dtype=pl.Int64)
        .alias("existing_quote_lane_rows"),
    )
    return df.sort(["day", "snapshot_pop", "snapshot_T", "rank"])


# --------------------------------------------------------------------------- #
# per-day orchestration
# --------------------------------------------------------------------------- #


def path_print_coverage(
    prints: pl.DataFrame | None, tickers: list[str], session_end: int
) -> pl.DataFrame:
    """Per-path print span / coverage class / terminal censor (retrospective).

    ``prints is None`` means the raw lane never stored the day (not acquired),
    which is never silently equal to "no prints".
    """
    if prints is None:
        return pl.DataFrame(
            [
                {
                    "ticker": t,
                    "source_span_first_et": None,
                    "source_span_last_et": None,
                    "n_print_rows": 0,
                    "coverage_class": "not_acquired",
                    "terminal_censored": None,
                }
                for t in tickers
            ],
            schema_overrides={
                "source_span_first_et": pl.Int32,
                "source_span_last_et": pl.Int32,
                "n_print_rows": pl.Int64,
                "terminal_censored": pl.Boolean,
            },
        )
    if prints is not None and prints.height:
        agg = prints.group_by("ticker").agg(
            pl.col("et_min").min().alias("source_span_first_et"),
            pl.col("et_min").max().alias("source_span_last_et"),
            pl.len().alias("n_print_rows"),
        )
        d = {r["ticker"]: r for r in agg.iter_rows(named=True)}
    else:
        d = {}
    rows = []
    for t in tickers:
        c = d.get(t)
        if c is None:
            klass, first, last, n = "no_prints", None, None, 0
        else:
            first = int(c["source_span_first_et"])
            last = int(c["source_span_last_et"])
            n = int(c["n_print_rows"])
            klass = "complete_to_session_end" if last >= session_end else "ends_early"
        rows.append(
            {
                "ticker": t,
                "source_span_first_et": first,
                "source_span_last_et": last,
                "n_print_rows": n,
                "coverage_class": klass,
                "terminal_censored": bool(last is not None and last < session_end),
            }
        )
    return pl.DataFrame(
        rows,
        schema_overrides={
            "source_span_first_et": pl.Int32,
            "source_span_last_et": pl.Int32,
            "n_print_rows": pl.Int64,
            "terminal_censored": pl.Boolean,
        },
    )


def day_out_paths(out_data: Path, day: str) -> dict[str, Path]:
    month = day[:7]
    return {
        "selected_path_prints": out_data
        / "prints.selected_paths"
        / f"month={month}"
        / f"{day}.parquet",
        "selected_path_grid": out_data
        / "grid.selected_paths"
        / f"month={month}"
        / f"{day}.parquet",
        "race_minute_full": out_data / "race.minute_full" / f"month={month}" / f"{day}.parquet",
        "race_checkpoint_full": out_data
        / "race.checkpoint_full"
        / f"month={month}"
        / f"{day}.parquet",
        "race_candidate_net": out_data / "race.candidate_net" / f"month={month}" / f"{day}.parquet",
        "quote_rosters": out_data / "quote_rosters" / f"{day}.parquet",
    }


# --- source generation binding ------------------------------------------------- #
# The manifest must record the generation that EXECUTED, not a hash of the file as it is when
# the manifest is written. We use spawn, so a worker RE-IMPORTS this file: the parent's bound
# bytes and the child's executed code are the same only if the file did not move in between,
# which is exactly the window that made a finished run non-canonical. So the binding is taken
# at import in both processes and compared BEFORE any day write.
# Bound at IMPORT time, once per process. It is the identity this process actually executed, and
# it must never be recomputed at a check site: comparing the constant against itself is a
# tautology, which is exactly the dead-guard bug this replaced.
SOURCE_GENERATION_SHA = sha256_file(HERE) if HERE.exists() else None
# Indirection so a self-test can exercise the REAL read path against a temp file instead of
# mutating the live producer.
SOURCE_PATH = HERE


def _current_source_sha() -> str | None:
    """A FRESH hash of the source file as it is RIGHT NOW, or None if it cannot be read."""
    try:
        return sha256_file(SOURCE_PATH)
    except OSError:
        return None


def source_generation_ok(expected: str | None, where: str) -> None:
    """Fail CLOSED unless `expected` is BOTH this process's import binding AND the file now.

    Two distinct comparisons, neither of them a tautology:
      * `expected != SOURCE_GENERATION_SHA` — in a spawned worker, the child re-imports the file,
        so this catches entry-to-spawn drift even if the file was moved and moved back;
      * `expected != _current_source_sha()` — catches the file moving at any later point, which is
        what the parent pre-dispatch and day-boundary checks exist for.
    Refusal never deletes: completed payloads and `_progress.json` stay on disk. What it prevents
    is publishing a SUCCESSFUL canonical manifest, or claiming `core_full_ready`, for a corpus
    whose recorded code identity is not the code that ran.
    """
    if expected is None:
        return
    if not _import_binding_matches(expected):
        raise SystemExit(
            f"source generation mismatch at {where}: parent bound {expected}, this process "
            f"imported {SOURCE_GENERATION_SHA}; refusing to continue and refusing to publish a "
            "successful canonical manifest. Completed outputs and _progress.json are preserved."
        )
    current = _current_source_sha()
    if current is None:
        raise SystemExit(
            f"source file unreadable at {where}; a guard that cannot read what it exists to "
            "protect must fail closed. Completed outputs and _progress.json are preserved."
        )
    if expected != current:
        raise SystemExit(
            f"source generation drift at {where}: the executed generation is {expected} but the "
            f"file now hashes to {current}; refusing to continue and refusing to publish a "
            "successful canonical manifest. Completed outputs and _progress.json are preserved."
        )


def _import_binding_matches(expected: str) -> bool:
    """Does this process's IMPORT-TIME binding equal the parent's bound generation?

    The worker-side semantic: a spawned child re-imports the file, so this catches entry-to-spawn
    drift even if the file was moved and moved back. An unreadable import binding cannot vouch
    for anything, so it does not match.
    """
    return SOURCE_GENERATION_SHA is not None and expected == SOURCE_GENERATION_SHA


def _source_file_matches(expected: str) -> bool | None:
    """Does the source FILE still hash to the expected generation? None if unreadable."""
    current = _current_source_sha()
    return None if current is None else current == expected


def build_one_day(day: str, out_data_s: str, expected_source_sha: str | None = None) -> dict:
    """Worker entry point: build and write every per-day layer."""
    # BEFORE any write: this worker re-imported the file, so assert it is the bound generation
    source_generation_ok(expected_source_sha, f"worker {day}")
    out_data = Path(out_data_s)
    guard(day)
    calendar, _cal_sha = session_calendar()
    se = int(calendar[day])
    _v, pit_set = pit_elig(day)
    plan = day_lane_plan(day)
    kind, era, src_sha = plan["kind"], plan["feed_era"], combined_lane_sha(plan)
    prev_day, _prev_kind, prov = prev_close_provenance(day)
    prev_plan = prev_session_plan(prev_day) if prev_day is not None else None
    panel = panel_memberships().filter(pl.col("sleeve_day") == day)
    tickers = sorted(panel["ticker"].unique().to_list())
    # the path's FIRST entry: the prospective-read boundary. Rows before it exist for the
    # retrospective audit but are not selectable; nothing is deleted.
    # The physical panel field is `entry_et`; the aggregate output column is named EXPLICITLY so
    # the comprehension below cannot read a column the frame never had. `min` is the EARLIEST
    # known entry across the path's memberships, which is the causal boundary a prospective read
    # must respect - it discloses nothing later.
    first_entry = {
        str(r["ticker"]): int(r["first_entry_et"])
        for r in panel.group_by("ticker")
        .agg(pl.col("entry_et").min().alias("first_entry_et"))
        .iter_rows(named=True)
    }
    paths = day_out_paths(out_data, day)
    layers: dict[str, dict] = {}
    dtypes = declared_dtypes()
    t_day_start = time.time()
    acq = plan["acquisition"] or {}
    stats: dict[str, Any] = {
        "day": day,
        "session_end": se,
        "source_kind": kind,
        "feed_era": era,
        "raw_source_sha256": src_sha,
        "raw_baseline_sha256": lane_sha(plan["lanes"][0]),
        "acq_bars_sha256": acq.get("sha256"),
        "acq_manifest_sha256": acq.get("manifest_sha256"),
        "acq_roster_sha256": acq.get("roster_sha256"),
        "acq_scope": plan["acquisition_scope"],
        "acq_mode": plan["acquisition_mode"],
        "alias_map_sha256": plan["alias_map_sha256"],
        "qualified_floor_source": plan["floor_qualified"],
        "pit_n": len(pit_set),
        "n_selected_paths": len(tickers),
    }

    # prints ------------------------------------------------------------------ #
    t0 = time.time()
    prints = build_prints(day, tickers, se, first_entry_et=first_entry)
    raw_print_sha = sha256_file(NET_TRADES / f"{day}.parquet") if prints is not None else None
    if prints is not None:
        prints = enforce_dtypes(prints, "selected_path_prints", dtypes)
        assert_observation_frame(prints, "selected_path_prints")
        atomic_write_parquet(prints, paths["selected_path_prints"])
        layers["selected_path_prints"] = {
            "path": str(paths["selected_path_prints"].relative_to(out_data)),
            "rows": prints.height,
            "sha256": sha256_file(paths["selected_path_prints"]),
        }
    else:
        layers["selected_path_prints"] = {
            "path": None,
            "rows": 0,
            "sha256": None,
            "status": "not_acquired",
        }
    stats["prints_seconds"] = round(time.time() - t0, 2)

    # grid -------------------------------------------------------------------- #
    t0 = time.time()
    cov = path_print_coverage(prints, tickers, se)
    bar_path = NET_BARS / f"{day}.parquet"
    bar_sha = sha256_file(bar_path) if bar_path.exists() else None
    grid = build_grid(
        day,
        tickers,
        se,
        prints,
        bar_sha,
        raw_print_sha,
        layers["selected_path_prints"]["sha256"],
        first_entry_et=first_entry,
    )
    grid = grid.join(
        cov.select(["ticker", "coverage_class", "terminal_censored"]), on="ticker", how="left"
    )
    grid = enforce_dtypes(grid, "selected_path_grid", dtypes)
    assert_observation_frame(grid, "selected_path_grid", allowed_censor=("terminal_censored",))
    atomic_write_parquet(grid, paths["selected_path_grid"])
    layers["selected_path_grid"] = {
        "path": str(paths["selected_path_grid"].relative_to(out_data)),
        "rows": grid.height,
        "sha256": sha256_file(paths["selected_path_grid"]),
    }
    stats["grid_seconds"] = round(time.time() - t0, 2)

    # minute board ------------------------------------------------------------ #
    t0 = time.time()
    cal_all, _cs = session_calendar()
    if prev_plan is not None and _lane_key(prev_plan["lanes"]) == _lane_key(plan["lanes"]):
        raw = read_raw_days([(day, se), (prev_day, int(cal_all.get(prev_day, se)))])
        prev_map, prev_et_map, prev_et = prev_closes_from_frame(
            raw.filter(pl.col("dt") == prev_day), int(cal_all.get(prev_day, se))
        )
        prov["prev_close_et_by_name"] = prev_et_map
        prov["prev_close_et"] = prev_et
        if not prev_map:
            prov["prev_close_source"] = "none"
        raw = raw.filter(pl.col("dt") == day)
    else:
        raw = read_raw_days([(day, se)])
        prev_map, prov = prev_close_map(day)
    u_path = UNIVERSE_RTH / f"{day}.parquet"
    universe_symbols = (
        set(pl.read_parquet(u_path, columns=["symbol"])["symbol"].to_list())
        if u_path.exists()
        else set()
    )
    board, bstats = build_minute_board(
        day, se, prov, prev_map, pit_set, src_sha, kind, era, universe_symbols, raw=raw
    )
    stats.update(bstats)
    if board.height:
        board = enforce_dtypes(board, "race_minute_full", dtypes)
        assert_observation_frame(board, "race_minute_full")
        atomic_write_parquet(board, paths["race_minute_full"])
        layers["race_minute_full"] = {
            "path": str(paths["race_minute_full"].relative_to(out_data)),
            "rows": board.height,
            "sha256": sha256_file(paths["race_minute_full"]),
        }
        n_board_minutes = int(board["t"].n_unique())
        stats["board_minutes"] = n_board_minutes
    else:
        layers["race_minute_full"] = {"path": None, "rows": 0, "sha256": None, "status": "empty"}
    stats["board_seconds"] = round(time.time() - t0, 2)
    del board

    # checkpoint board -------------------------------------------------------- #
    t0 = time.time()
    ckpt = build_checkpoint_board(day, se, pit_set)
    if ckpt.height:
        ckpt = enforce_dtypes(ckpt, "race_checkpoint_full", dtypes)
        assert_observation_frame(ckpt, "race_checkpoint_full")
        atomic_write_parquet(ckpt, paths["race_checkpoint_full"])
        layers["race_checkpoint_full"] = {
            "path": str(paths["race_checkpoint_full"].relative_to(out_data)),
            "rows": ckpt.height,
            "sha256": sha256_file(paths["race_checkpoint_full"]),
        }
    else:
        layers["race_checkpoint_full"] = {
            "path": None,
            "rows": 0,
            "sha256": None,
            "status": "missing_source",
        }
    stats["checkpoint_seconds"] = round(time.time() - t0, 2)

    # candidate net ----------------------------------------------------------- #
    t0 = time.time()
    cnet = build_candidate_net(day, se)
    if cnet.height:
        cnet = enforce_dtypes(cnet, "race_candidate_net", dtypes)
        assert_observation_frame(cnet, "race_candidate_net")
        atomic_write_parquet(cnet, paths["race_candidate_net"])
        layers["race_candidate_net"] = {
            "path": str(paths["race_candidate_net"].relative_to(out_data)),
            "rows": cnet.height,
            "sha256": sha256_file(paths["race_candidate_net"]),
        }
        stats["candidate_names"] = int(cnet["ticker"].n_unique())
        stats["candidate_prospective_names"] = int(
            cnet.filter(pl.col("roster_first_selectable_et").is_not_null())["ticker"].n_unique()
        )
    else:
        layers["race_candidate_net"] = {
            "path": None,
            "rows": 0,
            "sha256": None,
            "status": "missing_source",
        }
    stats["candidate_seconds"] = round(time.time() - t0, 2)

    # quote roster ------------------------------------------------------------ #
    t0 = time.time()
    qr = build_quote_rosters(day)
    if qr.height:
        qr = enforce_dtypes(qr, "quote_rosters", dtypes)
        assert_observation_frame(qr, "quote_rosters")
        atomic_write_parquet(qr, paths["quote_rosters"])
        layers["quote_rosters"] = {
            "path": str(paths["quote_rosters"].relative_to(out_data)),
            "rows": qr.height,
            "sha256": sha256_file(paths["quote_rosters"]),
        }
        stats["quote_roster_union_n"] = int(qr["symbol"].n_unique())
    else:
        layers["quote_rosters"] = {
            "path": None,
            "rows": 0,
            "sha256": None,
            "status": "missing_source",
        }
    stats["quote_seconds"] = round(time.time() - t0, 2)

    stats["net_manifest_status"] = net_manifest_status(day)
    stats["net_reconciled"] = net_reconciled(day)
    stats["path_cov"] = cov.to_dicts()
    stats["elapsed_s"] = round(time.time() - t_day_start, 2)
    stats["peak_rss_gb"] = round(peak_rss_gb(), 3)
    check_rss_policy(stats["peak_rss_gb"], f"build_one_day({day})")
    return {"day": day, "layers": layers, "stats": stats}


# --------------------------------------------------------------------------- #
# coverage + evidence
# --------------------------------------------------------------------------- #


def build_coverage(
    dev: list[str],
    canary_days: list[str],
    day_stats: dict[str, dict],
    blockers: list[dict],
) -> pl.DataFrame:
    """Per-layer presence/status + selectable_asof_et, one row per (layer, day, scope).

    Every blocker owns an explicit, MEASURED branch: B1 floor-qualified fallback (only while a
    2025-02 day still has no admitted acquisition), B2 unresolved missing confirmed trader
    (verified zero-bar/no-RTH/invalid names are NOT a hole), B4 canary-only marker, B5 storage
    headroom, B6 unreconciled net manifest, and B3 quote lane (a quote-SIBLING state carried by
    quote_rosters rows: it gates only the prospective quote channel and never the core
    bar/print/race corpus). Coverage carries blocker STATES, never a capacity reading: measured
    storage readings are run-varying and live in costs.json.
    """
    rows = []
    rows.append(
        {
            "layer": "day_registry",
            "day": "all",
            "scope": "all",
            "status": "ok",
            "n_expected": 1066,
            "n_present": 1066,
            "n_missing": None,
            "selectable_asof_et": None,
            "missingness_class": "none",
            "blocker_id": "B4_canary_only",
            "source_path": "basket_sim.dev_days()",
            "source_sha256": None,
            "notes": ["full dev calendar; per-day input shas for built days only"],
        }
    )
    layer_of = {
        "selected_path_prints": "prints.selected_paths",
        "selected_path_grid": "grid.selected_paths",
        "race_minute_full": "race.minute_full",
        "race_checkpoint_full": "race.checkpoint_full",
        "race_candidate_net": "race.candidate_net",
        "quote_rosters": "quote_rosters",
    }
    for day in sorted(canary_days):
        st = day_stats.get(day) or {}
        st_stats = st.get("stats") or {}
        for key, layer in layer_of.items():
            info = (st.get("layers") or {}).get(key) or {}
            n = int(info.get("rows") or 0)
            status = "ok" if n > 0 else ("absent" if st else "not_built")
            mclass = "none"
            notes: list[str] = []
            bl: list[str] = ["B4_canary_only"]
            if key == "race_minute_full":
                if st_stats.get("raw_roster_missing_unresolved_n"):
                    # B2 owns 2026-04/05 ONLY. A same-feed hole on any other month is a general
                    # missing-source stratum and must never be labelled B2, nor annotated with a
                    # 2026-04/05 measurement that is false for the day it is attached to.
                    if day[:7] in ("2026-04", "2026-05"):
                        mclass = "raw_roster_missing"
                        bl.append("B2_raw_roster_2026_04_05")
                    else:
                        mclass = "raw_roster_missing_general"
                    notes.append(
                        "PIT names with zero SIP minute bars that no declared source explains: "
                        f"{st_stats['raw_roster_missing_unresolved_n']} "
                        f"(classes {st_stats.get('raw_roster_unobserved_classes')})"
                    )
                if st_stats.get("raw_roster_verified_no_sip_bar_n"):
                    notes.append(
                        "SIP accepted the spelling and returned no minute bar for "
                        f"{st_stats['raw_roster_verified_no_sip_bar_n']} name(s) - a COVERAGE "
                        "fact, never a claim that the name did not trade"
                    )
                if st_stats.get("raw_roster_cross_feed_witnessed_n"):
                    notes.append(
                        "a non-SIP proxy preserved the bar for "
                        f"{st_stats['raw_roster_cross_feed_witnessed_n']} name(s) the SIP pull "
                        "did not reproduce: RETROSPECTIVE witness only, not a SIP rank input"
                    )
                if st_stats.get("raw_roster_verified_absent_n"):
                    notes.append(
                        "verified absent (zero_bars/no_rth/invalid), not a hole: "
                        f"{st_stats['raw_roster_verified_absent_n']} names; classes "
                        f"{st_stats.get('raw_roster_absent_classes')}"
                    )
                if st_stats.get("qualified_floor_source"):
                    mclass = "qualified_floor_source"
                    bl.append("B1_raw_2025_02")
                    notes.append("clean fallback embeds $2/100-share floors")
                if st_stats.get("acq_mode"):
                    notes.append(
                        f"admitted acquisition day {st_stats.get('acq_scope')} "
                        f"({st_stats['acq_mode']}), manifest sha "
                        f"{st_stats.get('acq_manifest_sha256')}"
                    )
                bl.append("B5_storage_headroom")
                notes.append(
                    "full build projects 37.7 GiB board / 45-50 GiB corpus against the frozen "
                    "75 GiB conservative-reserve gate (planning band 70-80 GiB); per-run "
                    "storage readings live in costs.json"
                )
            if key in ("selected_path_prints", "selected_path_grid"):
                if st_stats.get("net_manifest_status") == "partial":
                    mclass = "net_manifest_partial"
                    notes.append("raw acquisition lost symbols; missing is not_acquired")
                if st_stats.get("net_manifest_status") in {"missing", "unreconciled"}:
                    mclass = "net_manifest_missing"
                    bl.append("B6_unreconciled_net_manifest")
                    notes.append(
                        "the day's net files verify locally but the derived manifest index has "
                        "no verified row for it; provider-side completeness is not claimable"
                    )
            if key == "quote_rosters":
                bl.append("B3_quote_lane")
                notes.append(
                    "causal roster only; quote-sibling gate: no prospective quote feature is "
                    "materialized, and a missing quote for a roster name is 'not acquired'"
                )
            if n == 0 and st and mclass == "none":
                mclass = "not_acquired"
            rows.append(
                {
                    "layer": layer,
                    "day": day,
                    "scope": "day",
                    "status": status,
                    "n_expected": None,
                    "n_present": n,
                    "n_missing": None,
                    "selectable_asof_et": (570 if layer == "race.minute_full" else None),
                    "missingness_class": mclass,
                    "blocker_id": ";".join(sorted(set(bl))),
                    "source_path": info.get("path"),
                    "source_sha256": info.get("sha256"),
                    "notes": notes or None,
                }
            )
    # B6 is emitted for the days that MEASURE as unreconciled, never for a hardcoded pair. A
    # day the net reconciliation verified (files + per-day manifests + rebuilt index row) drops
    # out of this set on its own; a day that regresses re-enters it.
    for day in net_unreconciled_days(dev):
        for layer in ("selected_path_prints", "race_candidate_net", "coverage"):
            rows.append(
                {
                    "layer": layer,
                    "day": day,
                    "scope": "day",
                    "status": "not_built",
                    "n_expected": None,
                    "n_present": None,
                    "n_missing": None,
                    "selectable_asof_et": None,
                    "missingness_class": "net_manifest_missing",
                    "blocker_id": "B6_unreconciled_net_manifest",
                    "source_path": f"data/sip/net/{day}",
                    "source_sha256": None,
                    "notes": [
                        "the day's net files and per-day manifests exist, but the derived "
                        "manifest index carries no verified row for it; provider-side "
                        "completeness is not claimable until the reconciliation rebuilds it"
                    ],
                }
            )
    rows.append(
        {
            "layer": "race.minute_full",
            "day": "all",
            "scope": "all",
            "status": "planned",
            "n_expected": int(1.95e9),
            "n_present": None,
            "n_missing": None,
            "selectable_asof_et": None,
            "missingness_class": "storage_headroom",
            "blocker_id": "B5_storage_headroom",
            "source_path": None,
            "source_sha256": None,
            "notes": [
                "projected 37.7 GiB board / 45-50 GiB corpus; frozen reserve 75 GiB of "
                "conservatively measured free space on the backing volume of the actual output "
                "mount (planning band 70-80 GiB); per-run readings live in costs.json storage"
            ],
        }
    )
    for day in dev:
        if day in set(canary_days):
            continue
        rows.append(
            {
                "layer": "day_registry",
                "day": day,
                "scope": "day",
                "status": "not_built",
                "n_expected": None,
                "n_present": None,
                "n_missing": None,
                "selectable_asof_et": None,
                "missingness_class": "none",
                "blocker_id": "B4_canary_only",
                "source_path": None,
                "source_sha256": None,
                "notes": ["registry row only (canary stage)"],
            }
        )
    return pl.DataFrame(rows, infer_schema_length=None).sort(["layer", "day", "scope"])


INPUT_SHA_KEYS = (
    "raw_source_sha256",
    "raw_baseline_sha256",
    "acq_bars_sha256",
    "acq_manifest_sha256",
    "acq_roster_sha256",
    "alias_map_sha256",
    "net_trades_sha256",
    "net_bars_sha256",
    "net_coverage_sha256",
    "net_coverage_manifest_sha256",
    "universe_sha256",
    "candidates_sha256",
    "anatomy_sha256",
)


def _data_relative_path(value: object) -> Path:
    """Resolve a recorded lane path to an absolute Path; relative paths hang off the data root."""
    p = Path(str(value))
    return p if p.is_absolute() else (DATA / p)


def build_manifest(
    out_data: Path,
    contracts: dict,
    code_sha: str,
    day_results: dict[str, dict],
    shared: dict[str, dict],
    registry: pl.DataFrame,
    extra: dict,
) -> dict:
    """Immutable node manifest using the contract's exact field names."""
    payloads = dict(shared)
    for day, res in sorted(day_results.items()):
        for layer, info in res["layers"].items():
            payloads[f"{layer}/{day}"] = info
    input_shas = {
        r["day"]: {k: r.get(k) for k in INPUT_SHA_KEYS}
        for r in registry.iter_rows(named=True)
        if r["canary_member"]
    }
    config = {
        "contract_hashes": contract_hashes(),
        "canary_days": extra["canary_days"],
        "keys": {
            t: contracts["schema.json"]["tables"][t]["primary_key"]
            for t in contracts["schema.json"]["tables"]
        },
        "sort_order": {
            t: contracts["schema.json"]["tables"][t]["sort"]
            for t in contracts["schema.json"]["tables"]
        },
        "policy": {
            "soft_rss_gib": SOFT_RSS_GIB,
            "hard_rss_gib": HARD_RSS_GIB,
            "full_gate_free_bytes": FULL_GATE_FREE_BYTES,
            "projection_lo": PROJECTION_LO,
            "projection_hi": PROJECTION_HI,
        },
        "script": PRODUCER_VERSION,
    }
    built_rows = [r for r in registry.iter_rows(named=True) if r["canary_member"]]
    raw_kinds = sorted({r["raw_source_kind"] for r in built_rows})
    lane_paths: dict[str, str] = {}
    for r in built_rows:
        for key in ("raw_baseline_path", "acq_bars_path"):
            p = r.get(key)
            if p and r.get("day"):
                # `raw_baseline_path` is recorded RELATIVE to the data root while `acq_bars_path`
                # is absolute; resolve both to an absolute path here, because as_uri() raises on a
                # relative path and the parent URI/sha assembly needs a real file anyway.
                lane_paths[f"raw_lane:{r['day']}:{key}"] = str(_data_relative_path(p))
    adm = acquisition_admission()
    recon = net_reconciliation()
    alias_sha = alias_map_sha256()
    parent_ids = (
        ["panel.parquet", "phase2_session_calendar.json"]
        + [f"raw_lane:{k}" for k in raw_kinds]
        + sorted(lane_paths)
        + (["acquisition_admission"] if adm["present"] else [])
        + (["acquisition_alias_map"] if alias_sha else [])
        + (["net_reconciliation"] if recon["present"] else [])
    )
    parents = {
        "panel.parquet": {
            "sha256": sha256_file(PANEL),
            "source_uri": PANEL.as_uri(),
            "available": PANEL.exists(),
            "tracked": True,
        },
        "phase2_session_calendar.json": {
            "sha256": sha256_file(CAL_PATH),
            "source_uri": CAL_PATH.as_uri(),
            "available": CAL_PATH.exists(),
            "tracked": True,
        },
    }
    for kind, pat in (
        ("ohlcv_raw", "ohlcv_*.parquet"),
        ("clean_fallback", "clean_ohlcv_*.parquet"),
        ("backfill_raw", "backfill/ohlcv_*.parquet"),
    ):
        if kind in raw_kinds:
            parents[f"raw_lane:{kind}"] = {
                "sha256": None,
                "source_uri": f"{DATA.as_uri()}/{pat}",
                "available": True,
                "tracked": False,
            }
    # Per-day lane files (baseline + admitted acquisition day) are real parents: their shas
    # come from the registry so parent-drift verification re-hashes the actual bytes.
    for pid, path in sorted(lane_paths.items()):
        p = Path(path)
        parents[pid] = {
            "sha256": sha256_file(p) if p.exists() else None,
            "source_uri": p.as_uri(),
            "available": p.exists(),
            "tracked": False,
        }
    if adm["present"]:
        parents["acquisition_admission"] = {
            "sha256": adm["sha256"],
            "source_uri": ACQ_ADMISSION.as_uri(),
            "available": True,
            "tracked": False,
        }
    if alias_sha:
        parents["acquisition_alias_map"] = {
            "sha256": alias_sha,
            "source_uri": ACQ_ALIAS_MAP.as_uri(),
            "available": True,
            "tracked": True,
        }
    # Cross-feed witnesses are PARENTS for integrity: re-hashed, never read as a rank input.
    for r in sorted(built_rows, key=lambda x: str(x.get("day"))):
        acq = acquisition_day(r["day"])
        if not acq or not acq.get("witness_path"):
            continue
        wp = Path(str(acq["witness_path"]))
        parents[f"cross_feed_witness:{r['day']}"] = {
            "sha256": content_sha(wp) if wp.exists() else None,
            "source_uri": wp.as_uri(),
            "available": wp.exists(),
            "tracked": False,
            "role": WITNESS_ROLE,
            "rank_input": False,
        }
    if recon["present"]:
        parents["net_reconciliation"] = {
            "sha256": recon["sha256"],
            "source_uri": (TRACK / NET_RECONCILIATION_REL).as_uri(),
            "available": True,
            "tracked": True,
        }
    core = {
        "node_id": "observations.tape_atlas",
        "version": PRODUCER_VERSION,
        "contract": CONTRACT_VERSION,
        "schema_sha256": contract_hashes()["schema.json"],
        "contract_sha256": contract_hashes()["contract.json"],
        "causal_registry_sha256": contract_hashes()["causal_registry.json"],
        "canary_days_sha256": contract_hashes()["canary_days.json"],
        "adaptive_choice_ledger_sha256": contract_hashes()["adaptive_choice_ledger.json"],
        "code_sha256": code_sha,
        "parent_ids": parent_ids,
        "parent_shas": {
            "panel.parquet": sha256_file(PANEL),
            "phase2_session_calendar.json": sha256_file(CAL_PATH),
        },
        "input_shas": input_shas,
        "config_sha256": sha256_bytes(
            json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
        ),
        "keys": config["keys"],
        "sort_order": config["sort_order"],
        "parents": parents,
        "coverage_classes": [
            "complete_to_session_end",
            "ends_early",
            "no_prints",
            "not_acquired",
            "excluded_prints_only",
            "provider_absent",
            "tape_end",
            "raw_roster_missing",
            "qualified_floor_source",
            "net_manifest_partial",
            "net_manifest_missing",
            "storage_headroom",
            "none",
        ],
        "selection_classes": extra["selection_classes"],
        "canary_days": extra["canary_days"],
        "payload_sha256": payloads,
        "day_stats": {
            d: {
                k: v
                for k, v in r["stats"].items()
                if k != "path_cov" and k not in RUN_VARYING_STAT_KEYS
            }
            for d, r in sorted(day_results.items())
        },
        "status": "canary_built",
        "supersedes": None,
        "withdraws": None,
    }
    core["core_hash"] = sha256_bytes(
        json.dumps(core, sort_keys=True, separators=(",", ":")).encode()
    )
    return core


def write_meta(out_data: Path, day: str, res: dict) -> None:
    meta = out_data / "_meta"
    atomic_write_json(meta / "day_stats" / f"{day}.json", res)
    cov = res["stats"].get("path_cov") or []
    if cov:
        atomic_write_parquet(pl.DataFrame(cov), meta / "path_cov" / f"{day}.parquet")


def read_meta(out_data: Path, day: str) -> dict | None:
    p = out_data / "_meta" / "day_stats" / f"{day}.json"
    return load_json(p) if p.exists() else None


def progress_path(out_data: Path) -> Path:
    return out_data / "_progress.json"


def load_progress(out_data: Path) -> dict:
    p = progress_path(out_data)
    return load_json(p) if p.exists() else {"days": {}}


def build_paths_memberships(
    panel: pl.DataFrame, days: list[str], out_data: Path
) -> tuple[pl.DataFrame, pl.DataFrame]:
    sub = panel.filter(pl.col("sleeve_day").is_in(days))
    panel_sha = sha256_file(PANEL)
    mem = (
        sub.with_columns(
            (
                pl.col("sleeve_day")
                + "|"
                + pl.col("family")
                + "|"
                + pl.col("ticker")
                + "|"
                + pl.col("entry_et").cast(pl.Utf8)
                + "|"
                + pl.col("entry_rank").cast(pl.Utf8)
            ).alias("member_id"),
            (pl.col("sleeve_day") + "|" + pl.col("ticker")).alias("path_id"),
        )
        .select(
            [
                "member_id",
                "path_id",
                "sleeve_day",
                "family",
                "ticker",
                "entry_et",
                "entry_rank",
                "entry_px",
                "session_end",
                "month",
                "block",
            ]
        )
        .unique(subset=["member_id"])
        .rename({"sleeve_day": "day"})
        .with_columns(pl.lit(panel_sha).alias("panel_sha256"))
        .sort(["day", "family", "entry_rank", "ticker"])
    )
    counts = (
        sub.group_by(["sleeve_day", "family", "ticker", "entry_et", "entry_rank"])
        .agg(pl.len().alias("member_bar_count"))
        .rename({"sleeve_day": "day"})
    )
    mem = mem.join(counts, on=["day", "family", "ticker", "entry_et", "entry_rank"], how="left")
    mem = mem.select(
        [
            "member_id",
            "path_id",
            "day",
            "family",
            "ticker",
            "entry_et",
            "entry_rank",
            "entry_px",
            "session_end",
            "month",
            "block",
            "panel_sha256",
            "member_bar_count",
        ]
    )
    paths = (
        mem.group_by(["day", "ticker"])
        .agg(
            pl.col("path_id").first(),
            pl.col("family").unique().sort().alias("families"),
            pl.len().alias("n_memberships"),
            pl.col("member_id").sort().alias("membership_ids"),
            pl.col("entry_et").min().alias("first_entry_et"),
            pl.col("entry_et").max().alias("last_entry_et"),
            pl.col("entry_rank").min().alias("min_entry_rank"),
            pl.col("entry_rank").max().alias("max_entry_rank"),
            pl.col("session_end").first().alias("session_end"),
            pl.col("month").first().alias("month"),
            pl.col("block").first().alias("block"),
        )
        .with_columns(pl.lit(panel_sha).alias("panel_sha256"))
    )
    cov_rows = []
    for day in sorted(days):
        meta = out_data / "_meta" / "path_cov" / f"{day}.parquet"
        if not meta.exists():
            raise FileNotFoundError(
                f"path coverage metadata missing for {day}; rerun the day (used for resumable "
                f"paths/grid reconstruction)"
            )
        for r in pl.read_parquet(meta).iter_rows(named=True):
            cov_rows.append({"day": day, **r})
    cov = (
        pl.DataFrame(
            cov_rows,
            schema_overrides={
                "source_span_first_et": pl.Int32,
                "source_span_last_et": pl.Int32,
                "n_print_rows": pl.Int64,
                "terminal_censored": pl.Boolean,
            },
        )
        if cov_rows
        else pl.DataFrame(
            schema={
                "day": pl.Utf8,
                "ticker": pl.Utf8,
                "source_span_first_et": pl.Int32,
                "source_span_last_et": pl.Int32,
                "n_print_rows": pl.Int64,
                "coverage_class": pl.Utf8,
                "terminal_censored": pl.Boolean,
            }
        )
    )
    paths = paths.join(cov, on=["day", "ticker"], how="left")
    paths = paths.select(
        [
            "path_id",
            "day",
            "ticker",
            "month",
            "block",
            "families",
            "n_memberships",
            "membership_ids",
            "first_entry_et",
            "last_entry_et",
            "min_entry_rank",
            "max_entry_rank",
            "session_end",
            "panel_sha256",
            "source_span_first_et",
            "source_span_last_et",
            "n_print_rows",
            "coverage_class",
            "terminal_censored",
        ]
    ).sort(["day", "ticker"])
    return paths, mem


# --------------------------------------------------------------------------- #
# stages
# --------------------------------------------------------------------------- #


def measure_branch_coverage(out_data: Path, days: list[str]) -> dict:
    """Measured branch coverage of the built canary payloads."""
    bar, prn, cov_class, censored, quote_na = {}, {}, {}, 0, 0
    for day in days:
        gp = out_data / "grid.selected_paths" / f"month={day[:7]}" / f"{day}.parquet"
        if gp.exists():
            g = pl.read_parquet(
                gp, columns=["bar_state", "print_state", "coverage_class", "terminal_censored"]
            )
            for v in g["bar_state"].unique().to_list():
                bar[v] = bar.get(v, 0) + int((g["bar_state"] == v).sum())
            for v in g["print_state"].unique().to_list():
                prn[v] = prn.get(v, 0) + int((g["print_state"] == v).sum())
            for v in g["coverage_class"].unique().to_list():
                cov_class[v] = cov_class.get(v, 0) + int((g["coverage_class"] == v).sum())
            censored += int(g["terminal_censored"].fill_null(False).sum())
        qp = out_data / "quote_rosters" / f"{day}.parquet"
        if qp.exists():
            q = pl.read_parquet(qp, columns=["existing_quote_lane_present"])
            quote_na += int((~q["existing_quote_lane_present"]).sum())
    return {
        "bar_state": bar,
        "print_state": prn,
        "coverage_class": cov_class,
        "terminal_censored_rows": censored,
        "quote_not_acquired_rows": quote_na,
        "terminal_censored_exercised": censored > 0,
        "provider_bar_exercised": bar.get("provider", 0) > 0,
        "excluded_prints_exercised": prn.get("excluded_prints_only", 0) > 0,
        "no_print_exercised": prn.get("no_print", 0) > 0,
        "quote_not_acquired_exercised": quote_na > 0,
    }


def stage_contract(track: Path = TRACK, write: bool = True) -> dict:
    contracts = load_contracts(track)
    problems = validate_contracts(contracts)
    if problems:
        raise RuntimeError(f"contract validation failed: {problems}")
    hashes = contract_hashes(track)
    lock = {
        "lock_id": "freeze-o.contract-lock",
        "contract_version": CONTRACT_VERSION,
        "hashes": hashes,
        "status": "frozen",
    }
    lock_path = track / "contract_lock.json"
    if lock_path.exists():
        old = load_json(lock_path)
        if old.get("hashes") != hashes and not force_ok():
            raise RuntimeError(
                "contract files changed but contract_lock.json differs; "
                "rerun with --force to re-freeze"
            )
    if write:
        atomic_write_json(lock_path, lock)
    return lock


_FORCE = {"value": False}


def force_ok() -> bool:
    return bool(_FORCE["value"])


def patch_registry(registry: pl.DataFrame, results: dict[str, dict]) -> pl.DataFrame:
    """Fill the per-day counters into the registry, from the ONE declared column contract.

    Refuses on drift: if the registry does not carry every column in `REGISTRY_PATCH_COLUMNS`, or
    the per-day stats do not supply one, that is a contract break and must be loud, not skipped.
    The previous version hard-coded the same list twice and raised ColumnNotFoundError only after
    a whole 20-day canary had already been built.
    """
    if not results:
        return registry
    missing = [c for c in REGISTRY_PATCH_COLUMNS if c not in registry.columns]
    if missing:
        raise ValueError(
            f"day_registry is missing declared patch columns {missing}; the registry emitter and "
            f"patch_registry disagree (declared set: {list(REGISTRY_PATCH_COLUMNS)})"
        )
    patch_rows = []
    for day, res in sorted(results.items()):
        st = res["stats"]
        row = {"day": day}
        absent = [c for c in REGISTRY_PATCH_COLUMNS if c not in st]
        if absent:
            raise ValueError(
                f"day {day}: per-day stats are missing declared columns {absent}; the board "
                "builder and patch_registry disagree"
            )
        for c in REGISTRY_PATCH_COLUMNS:
            row[c] = st.get(c)
        patch_rows.append(row)
    p = pl.DataFrame(patch_rows, schema_overrides={"day": pl.Utf8})
    return registry.drop(list(REGISTRY_PATCH_COLUMNS)).join(p, on="day", how="left")


def concat_quote_rosters(out_data: Path, days: list[str]) -> pl.DataFrame:
    frames = []
    for day in days:
        p = out_data / "quote_rosters" / f"{day}.parquet"
        if p.exists():
            frames.append(pl.read_parquet(p))
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="vertical").sort(["day", "snapshot_pop", "snapshot_T", "rank"])


def stage_canary(
    out_data: Path, out_evidence: Path, workers: int, force: bool, days: list[str] | None = None
) -> dict:
    return stage_build(out_data, out_evidence, workers, force, days=days, mode="canary")


def stage_build(
    out_data: Path,
    out_evidence: Path,
    workers: int,
    force: bool,
    days: list[str] | None = None,
    mode: str = "canary",
    resolved_state: Path | str | None = None,
) -> dict:
    t_start = time.time()
    _FORCE["value"] = force
    out_data.mkdir(parents=True, exist_ok=True)
    out_evidence.mkdir(parents=True, exist_ok=True)
    contracts = load_contracts()
    problems = validate_contracts(contracts)
    if problems:
        raise RuntimeError(f"contract validation failed: {problems}")
    assert_quote_channel_surface(contracts, resolved_state)
    # The entry gate runs before any byte is written. On a resume it reads the PREVIOUS
    # manifest so only payload bytes that re-verify right now reduce the remaining demand.
    prior_mpath = out_evidence / "manifest.json"
    prior_manifest = load_json(prior_mpath) if prior_mpath.exists() else None
    storage = storage_gate_report(
        out_data, mode, resolved_state, manifest=prior_manifest, progress=load_progress(out_data)
    )
    if not storage["ok"]:
        raise SystemExit("storage gate refusal: " + json.dumps(storage, indent=1, sort_keys=True))
    stage_contract(write=True)
    canary = contracts["canary_days.json"]
    dev = dev_days()
    if days is None:
        days = [d["day"] for d in canary["days"]]
        if len(days) != 20 or len(set(days)) != 20:
            raise RuntimeError(f"canary must be exactly 20 unique days, got {len(days)}")
    for d in days:
        guard(d)
        if d not in set(dev):
            raise RuntimeError(f"{mode} day {d} is not a dev day")
    calendar, cal_sha = session_calendar()
    panel = panel_memberships()
    previous_manifest = None
    mpath = out_evidence / "manifest.json"
    if mpath.exists():
        previous_manifest = load_json(mpath)
    progress = load_progress(out_data)
    done = {} if force else {d: v for d, v in progress.get("days", {}).items() if d in set(days)}
    todo = [d for d in days if d not in done]
    results: dict[str, dict] = dict(done)
    per_day_seconds: dict[str, float] = {}
    n_workers = 0
    if todo:
        # spawn (never fork): the parent already holds a live polars/Rayon thread
        # pool, and forking a threaded process deadlocks the children on futexes.
        ctx = mp.get_context("spawn")
        # Refuse to START a heavy run under the ceiling. There is no cgroup cap and no
        # systemd-oomd on this host, so the kernel global OOM is the only other enforcement -
        # and a global OOM kills the largest holder, which is the very child we are about to
        # spawn. Refusing up front is the only cooperative option.
        pre = resource_preflight()
        if not pre["ok"]:
            raise SystemExit(
                "resource preflight refusal: " + json.dumps(pre, indent=1, sort_keys=True)
            )
        n_workers = effective_workers(int(workers))
        if n_workers < 1:
            raise SystemExit(
                "resource preflight refusal: " + json.dumps(pre, indent=1, sort_keys=True)
            )
        print(
            f"[resource] workers: requested {int(workers)}, using {n_workers} "
            f"(parent RSS {parent_rss_gib():.2f} GiB of a "
            f"{detect_memory_limit_gib():.2f} GiB host)",
            flush=True,
        )
        # bind the generation that is executing, assert it against the file on disk BEFORE any
        # worker is dispatched, and hand the same binding to each worker
        source_generation_ok(SOURCE_GENERATION_SHA, "pre-dispatch")
        bound_sha = SOURCE_GENERATION_SHA
        print(
            f"[source] bound generation {bound_sha} asserted before dispatch",
            flush=True,
        )
        with ProcessPoolExecutor(max_workers=n_workers, mp_context=ctx) as ex:
            futs = {ex.submit(build_one_day, d, str(out_data), bound_sha): d for d in todo}
            for fut in as_completed(futs):
                res = fut.result()
                day = res["day"]
                # day boundary: the file may have moved mid-run; catch it here, not at the end
                source_generation_ok(SOURCE_GENERATION_SHA, f"day boundary {day}")
                # path_cov is persisted to _meta here and re-read from there later, so the
                # in-memory copy is pure duplication: dropping it keeps the retained per-day
                # results to layer rows and shas only
                write_meta(out_data, day, res)
                res["stats"].pop("path_cov", None)
                results[day] = res
                per_day_seconds[day] = res["stats"].get("elapsed_s")
                slim = {
                    "day": day,
                    "layers": res["layers"],
                    "stats": {k: v for k, v in res["stats"].items() if k != "path_cov"},
                }
                progress.setdefault("days", {})[day] = slim
                atomic_write_json(progress_path(out_data), progress)
                print(
                    f"[canary] {day} built: "
                    f"{ {k: v.get('rows') for k, v in res['layers'].items()} }",
                    flush=True,
                )
    # shared tables ----------------------------------------------------------- #
    paths, mem = build_paths_memberships(panel, days, out_data)
    registry = build_day_registry(
        dev, calendar, cal_sha, panel, canary["days"], built_days=set(days)
    )
    registry = patch_registry(registry, results)
    quote_rosters = concat_quote_rosters(out_data, days)
    coverage = build_coverage(dev, days, results, contracts["contract.json"]["full_build_blockers"])
    dtypes = declared_dtypes()
    for name, df, censor in (
        ("day_registry", registry, ()),
        ("paths", paths, ("terminal_censored",)),
        ("memberships", mem, ()),
        ("quote_rosters", quote_rosters, ()),
        ("coverage", coverage, ()),
    ):
        df = enforce_dtypes(df, name, dtypes)
        assert_observation_frame(df, name, allowed_censor=censor)
        atomic_write_parquet(df, out_data / f"{name}.parquet")
    shared = {}
    for name in ("day_registry", "paths", "memberships", "quote_rosters", "coverage"):
        p = out_data / f"{name}.parquet"
        shared[name] = {"path": p.name, "rows": _parquet_rows(p), "sha256": sha256_file(p)}

    # evidence ---------------------------------------------------------------- #
    def _stat_total(key: str) -> int:
        return sum(bool((results.get(d, {}).get("stats") or {}).get(key)) for d in days)

    selection_classes = {
        "block1": int(sum(1 for d in days if d <= "2023-12-31")),
        "block2": int(sum(1 for d in days if d > "2023-12-31")),
        "early_close": int(sum(1 for d in days if int(calendar[d]) == sim.SESSION_END_EARLY)),
        "net_partial": int(sum(1 for d in days if net_manifest_status(d) == "partial")),
        "net_unreconciled": len([d for d in days if net_manifest_status(d) != "ok"]),
        "net_reconciled": int(sum(1 for d in days if net_reconciled(d))),
        "floor_fallback": int(sum(1 for d in days if day_lane_plan(d)["floor_qualified"])),
        "acquisition_repaired": int(sum(1 for d in days if day_lane_plan(d)["acquisition"])),
        "acquisition_supplement": int(
            sum(1 for d in days if day_lane_plan(d)["acquisition_mode"] == ACQ_MODE_SUPPLEMENT)
        ),
        "acquisition_replace": int(
            sum(1 for d in days if day_lane_plan(d)["acquisition_mode"] == ACQ_MODE_REPLACE)
        ),
        "roster_missing_unresolved": _stat_total("raw_roster_missing_unresolved_n"),
        "roster_verified_absent": _stat_total("raw_roster_verified_absent_n"),
        "alpaca_sip_era": int(sum(1 for d in days if feed_era_of(d) == "alpaca_sip_era")),
        "raw_roster_month": int(sum(1 for d in days if d[:7] in ("2026-04", "2026-05"))),
    }
    manifest = build_manifest(
        out_data,
        contracts,
        sha256_file(HERE),
        results,
        shared,
        registry,
        {"selection_classes": selection_classes, "canary_days": days},
    )
    atomic_write_json(mpath, manifest)
    # `core_full_ready` is decided from the corpus that was actually written: the complete day
    # set, a manifest whose payload shas verify, and parents with no drift. A successful build
    # has already spent the reserve it was admitted against, so the conservative reserve is the
    # ENTRY gate above, measured before any byte was written.
    build_state = build_completeness(out_data, manifest, days, dev)
    readiness = readiness_states(
        contracts,
        mode,
        days,
        dev,
        storage["conservative_free_bytes"],
        resolved_state,
        storage,
        build_state,
    )
    determinism = {}
    if previous_manifest:
        for key, info in sorted(manifest["payload_sha256"].items()):
            old = (
                previous_manifest.get("payload_sha256") or previous_manifest.get("payloads") or {}
            ).get(key, {})
            determinism[key] = bool(
                old.get("sha256") is not None and old.get("sha256") == info.get("sha256")
            )
    summary = {
        "canary_id": canary["canary_id"],
        "mode": mode,
        "day_filter": None
        if mode == "canary" and len(days) == 20 and days == [d["day"] for d in canary["days"]]
        else sorted(days),
        "contract_version": CONTRACT_VERSION,
        "days_built": len(results),
        "days": days,
        "selection_classes": selection_classes,
        "layers": {
            key: {
                "rows": sum(
                    int((r["layers"].get(key) or {}).get("rows") or 0) for r in results.values()
                ),
                "days": sum(
                    1
                    for r in results.values()
                    if int((r["layers"].get(key) or {}).get("rows") or 0) > 0
                ),
            }
            for key in (
                "selected_path_prints",
                "selected_path_grid",
                "race_minute_full",
                "race_checkpoint_full",
                "race_candidate_net",
                "quote_rosters",
            )
        },
        "identity": {
            "paths": paths.height,
            "memberships": mem.height,
            "shared_paths": int(paths.filter(pl.col("n_memberships") > 1).height),
            "panel_sha256": sha256_file(PANEL),
        },
        "registry": {
            "rows": registry.height,
            "canary_members": int(registry["canary_member"].sum()),
        },
        "blockers": [b["blocker_id"] for b in contracts["contract.json"]["full_build_blockers"]],
        "blockers_by_class": {
            k: blockers_by_class(contracts["contract.json"], k) for k in BLOCKER_CLASSES
        },
        "acquisition": {
            "root": str(ACQ_ROOT),
            "admission_present": acquisition_admission()["present"],
            "all_scopes_ready": acquisition_admission()["all_scopes_ready"],
            "missing_scopes": acquisition_admission()["missing_scopes"],
            "stale_scopes": acquisition_admission()["stale_scopes"],
            "scope_states": {
                b: acquisition_admission(b)["scope_state"]
                for b in ("B1_raw_2025_02", "B2_raw_roster_2026_04_05")
            },
            "admitted_days": len(acquisition_admission()["admitted_days"]),
            "days_using_acquisition": sorted(
                d for d in days if day_lane_plan(d)["acquisition"] is not None
            ),
            "residual_confirmed_trading_gaps": acquisition_admission()["residual_gaps"],
            "alias_map_sha256": alias_map_sha256(),
        },
        "net_reconciliation": {
            "path": str(TRACK / NET_RECONCILIATION_REL),
            "present": net_reconciliation()["present"],
            "status": net_reconciliation()["status"],
            "verified_days": sorted(net_reconciliation()["days"]),
            "unreconciled_dev_days": net_unreconciled_days(dev),
        },
        "raw_roster": {
            "verified_absent": {
                d: results[d]["stats"].get("raw_roster_verified_absent_n")
                for d in days
                if results[d]["stats"].get("raw_roster_verified_absent_n")
            },
            "missing_unresolved": {
                d: results[d]["stats"].get("raw_roster_missing_unresolved_n")
                for d in days
                if results[d]["stats"].get("raw_roster_missing_unresolved_n")
            },
        },
        "raw_roster_missing": {
            d: results[d]["stats"].get("raw_roster_missing_n")
            for d in days
            if d[:7] in ("2026-04", "2026-05")
        },
        "determinism_vs_previous_manifest": determinism if determinism else None,
        "projections_mirrored_from_plan": {
            "broad_board_rows": 1.95e9,
            "broad_board_gib": 37.7,
            "full_corpus_gib": [45, 50],
            "planning_headroom_gib": [70, 80],
            "full_gate_gib": 75,
            "canary_gate_gib": 2,
            "memory_soft_gib": 15,
            "memory_hard_gib": 30,
            "storage_readings": "costs.json storage block (guest free, mount facts, declared "
            "host volume, conservative free, margin); never a contract or summary claim",
        },
        "measured": {
            "payload_bytes_total": None,
            "board_rows_total": int(
                sum(
                    int((r["layers"].get("race_minute_full") or {}).get("rows") or 0)
                    for r in results.values()
                )
            ),
            "day_rss_gib_range": [
                min([r["stats"].get("peak_rss_gb", 0) for r in results.values()] or [0]),
                max([r["stats"].get("peak_rss_gb", 0) for r in results.values()] or [0]),
            ],
        },
        "branch_coverage": measure_branch_coverage(out_data, days),
        "readiness": readiness,
        # compatibility alias: pre-split consumers read `full_v0_ready` for the core state
        "full_v0_ready": readiness[CORE_READINESS_STATE],
        "note": (
            f"{mode} stage; {CORE_READINESS_STATE}={readiness[CORE_READINESS_STATE]} "
            f"(a canary never claims it), {QUOTE_READINESS_STATE}="
            f"{readiness[QUOTE_READINESS_STATE]}. Core full-build blockers: "
            + "; ".join(readiness["core_blockers"])
            + ". Quote-sibling blocker (prospective quote channel only, never the core build): "
            + "; ".join(readiness["quote_channel_blockers"])
        ),
    }
    # selftests --------------------------------------------------------------- #
    stamp = {"manifest": manifest, "previous_manifest": previous_manifest}
    selftest = run_selftests(
        out_data, out_evidence, contracts, results, paths, mem, registry, coverage, stamp
    )
    atomic_write_json(out_evidence / "selftest.json", selftest)
    summary["selftest_ok"] = bool(selftest["all_ok"])
    summary["selftest_failed"] = [c["check"] for c in selftest["checks"] if not c["ok"]]
    atomic_write_json(out_evidence / "summary.json", summary)
    costs = {
        "run_varying": True,
        "note": "costs are measurements only; they never enter the manifest core hash",
        "wall_seconds": round(time.time() - t_start, 2),
        "peak_rss_gb": round(peak_rss_gb(), 3),
        # the concurrency that ACTUALLY ran, not the request: a clamped request must never be
        # recorded as the parallelism a capacity or wall-clock projection is derived from
        "workers": n_workers or 1,
        "workers_requested": int(workers),
        "per_day_seconds": per_day_seconds,
        "per_day_seconds_all": {d: r["stats"].get("elapsed_s") for d, r in sorted(results.items())},
        "per_day_peak_rss_gb": {
            d: r["stats"].get("peak_rss_gb") for d, r in sorted(results.items())
        },
        "payload_sizes_bytes": {
            k: (out_data / v["path"]).stat().st_size
            for k, v in manifest["payload_sha256"].items()
            if v.get("path")
        },
        "total_payload_bytes": sum(
            (out_data / v["path"]).stat().st_size
            for v in manifest["payload_sha256"].values()
            if v.get("path")
        ),
        "disk_free_bytes": storage["measured"]["guest_free_bytes"],
        "disk_conservative_free_bytes": storage["conservative_free_bytes"],
        "storage": storage,
        "host": os.uname().nodename,
        "python": sys.version.split()[0],
    }
    atomic_write_json(out_evidence / "costs.json", costs)
    print(
        f"[canary] done in {costs['wall_seconds']}s; selftest_ok={summary['selftest_ok']}",
        flush=True,
    )
    return {"manifest": manifest, "summary": summary, "selftest": selftest, "costs": costs}


def _parquet_rows(p: Path) -> int:
    return int(pl.scan_parquet(p).select(pl.len()).collect().item())


# --------------------------------------------------------------------------- #
# self-tests / verification
# --------------------------------------------------------------------------- #


def _ok(check: str, ok: bool, detail: Any = None) -> dict:
    return {"check": check, "ok": bool(ok), "detail": detail}


def _all_parquet(root: Path):
    for p in sorted(root.rglob("*.parquet")):
        if "_meta" in p.parts:
            continue
        yield p


def t_contracts(contracts: dict, lock: dict) -> dict:
    problems = validate_contracts(contracts)
    hashes = contract_hashes()
    return _ok(
        "contract_schema_invariants",
        not problems,
        problems or {"contract_hashes": hashes, "lock_matches": lock.get("hashes") == hashes},
    )


def t_readiness_split(contracts: dict) -> dict:
    """B3 gates only the prospective quote channel; a canary is never core_full_ready.

    Proves the gate split from the frozen contracts, not from prose: the core class
    never contains B3, the two gate lists match their classes, the quote surface is
    empty while the quote gate is unresolved, and no run mode short of the full
    calendar can raise `core_full_ready`.
    """
    contract = contracts["contract.json"]
    core = blockers_by_class(contract, BLOCKER_CLASS_CORE)
    quote = blockers_by_class(contract, BLOCKER_CLASS_QUOTE)
    canary_only = blockers_by_class(contract, BLOCKER_CLASS_CANARY)
    gate = contract.get("full_build_gate") or {}
    problems: list[str] = []
    if "B3_quote_lane" not in quote:
        problems.append("B3_quote_lane is not declared quote-class")
    if "B3_quote_lane" in core:
        problems.append("B3_quote_lane appears in the core full-build class")
    if sorted(gate.get("core_blockers") or []) != sorted(core):
        problems.append("full_build_gate.core_blockers != core class")
    if sorted(gate.get("quote_channel_blockers") or []) != sorted(quote):
        problems.append("full_build_gate.quote_channel_blockers != quote class")
    if "B4_canary_only" not in canary_only:
        problems.append("B4_canary_only is not the canary marker")
    features = quote_feature_fields(contracts)
    if features:
        problems.append(f"quote feature fields declared: {features}")
    try:
        assert_quote_channel_surface(contracts)
    except SystemExit as exc:  # only reachable once quote features exist
        problems.append(f"quote surface refused: {exc}")
    probe_days = ["2021-02-01", "2021-02-02"]
    canary_state = readiness_states(contracts, "canary", probe_days, probe_days, 10**13)
    if canary_state[CORE_READINESS_STATE]:
        problems.append("a canary run claims core_full_ready")
    if canary_state["core_blockers"] != core:
        problems.append("readiness core blockers differ from the core class")
    # `core_full_ready` derives from the BUILT corpus, not from free space the build itself has
    # already spent. A complete, drift-free full build over the whole calendar at ZERO remaining
    # free bytes must therefore raise no storage reason - the 75 GiB reserve is the entry gate,
    # measured before a byte is written. What still refuses it is real: an outstanding blocker,
    # a missing build state, a short day set or a manifest problem.
    dev = dev_days()
    complete = {"days_built": len(dev), "dev_days": len(dev), "ok": True, "problems": []}
    ready = readiness_states(contracts, "full", dev, dev, 0, build_state=complete)
    reasons = ready["core_full_ready_reasons"] or []
    if any("free space" in r or str(FULL_GATE_FREE_BYTES) in r for r in reasons):
        problems.append(f"core_full_ready still reasons about free space: {reasons}")
    if ready["storage_gate"] is not None:
        problems.append("core_full_ready read the storage readings as a decision input")
    if any("outstanding core blockers" in r for r in reasons) != bool(
        unresolved_blockers(core, load_resolved_state())
    ):
        problems.append("core_full_ready blocker state disagrees with the verified evidence")
    no_state = readiness_states(contracts, "full", dev, dev, 0)
    no_state_reasons = no_state["core_full_ready_reasons"] or []
    if not any("no completed build state" in r for r in no_state_reasons):
        problems.append("core_full_ready claimed with no verified build state")
    short = readiness_states(contracts, "full", probe_days, dev, 0, build_state=complete)
    if not any("day set does not cover" in r for r in (short["core_full_ready_reasons"] or [])):
        problems.append("a day set short of the dev calendar did not refuse")
    drifted = readiness_states(
        contracts,
        "full",
        dev,
        dev,
        0,
        build_state={**complete, "problems": ["payload sha mismatch"]},
    )
    if not any("payload sha mismatch" in r for r in (drifted["core_full_ready_reasons"] or [])):
        problems.append("a manifest problem did not refuse")
    return _ok(
        "readiness_split_core_vs_quote",
        not problems,
        {
            "core_blockers": core,
            "quote_channel_blockers": quote,
            "canary_marker": canary_only,
            "canary_core_full_ready": canary_state[CORE_READINESS_STATE],
            "canary_quote_channel_ready": canary_state[QUOTE_READINESS_STATE],
            "quote_feature_fields": features,
            "complete_build_core_full_ready": ready[CORE_READINESS_STATE],
            "no_build_state_core_full_ready": no_state[CORE_READINESS_STATE],
            "problems": problems,
        },
    )


def t_guard() -> dict:
    cases = {}
    for day, want in (
        ("2024-01-02", True),
        ("2025-01-31", True),
        ("2026-06-01", True),
        ("2026-07-01", True),
        ("2026-08-03", True),
        ("2021-02-01", False),
    ):
        try:
            guard(day)
            raised = False
        except PermissionError:
            raised = True
        cases[day] = (raised, want)
    bad = [d for d, (r, w) in cases.items() if r != w]
    dev = dev_days()
    return _ok(
        "guard_sealed_reserved_and_dev_calendar",
        not bad and len(dev) == 1066 and len(set(dev)) == 1066,
        {"failures": bad, "dev_days": len(dev), "first": dev[0], "last": dev[-1]},
    )


def t_canary_selection(contracts: dict) -> dict:
    """Recompute the frozen canary selection from source facts and compare.

    The net-manifest strata are asserted as COVERAGE, not as a frozen count: the derived index is
    a REBUILT artifact, so a day that was partial when the canary was frozen can become `ok`
    after the reconciliation (and vice versa). What must hold at every point is that the frozen
    canary still covers every day the current data marks partial, and that the strata the canary
    exists to exercise (early closes, the 2025-02 floor month, the 2026-04/05 roster month, both
    blocks, the sentinel) are all present.
    """
    frozen = contracts["canary_days.json"]
    days = [d["day"] for d in frozen["days"]]
    dev = set(dev_days())
    cal, _ = session_calendar()
    early = {d for d in days if int(cal[d]) == sim.SESSION_END_EARLY}
    partials = {d for d in dev if net_manifest_status(d) == "partial"}
    detail = {
        "n": len(days),
        "unique": len(set(days)),
        "all_dev": all(d in dev for d in days),
        "early_close_n": len(early),
        "partial_n": len(partials),
        "blocks": sorted({sim_block(d) for d in days}),
        "eras": sorted({feed_era_of(d) for d in days}),
        "sentinel_present": "2022-03-10" in days,
        "floor_2025_02": any(d[:7] == "2025-02" for d in days),
        "roster_2026_04_05": any(d[:7] in ("2026-04", "2026-05") for d in days),
        "reason_per_day": all(d.get("reasons") for d in frozen["days"]),
    }
    ok = (
        len(days) == 20
        and len(set(days)) == 20
        and detail["all_dev"]
        and len(early) == 7
        and partials <= set(days)
        and detail["sentinel_present"]
        and detail["floor_2025_02"]
        and detail["roster_2026_04_05"]
        and detail["blocks"] == ["block1", "block2"]
        and len(detail["eras"]) == 4
    )
    return _ok("canary_20_days_strata_reproduced", ok, detail)


def t_canary_ledger(contracts: dict) -> dict:
    """Every frozen canary day must be a dev day and carry a reason; all specials covered."""
    frozen = contracts["canary_days.json"]
    cal, _ = session_calendar()
    dev = set(dev_days())
    early_all = {d for d in dev if int(cal[d]) == sim.SESSION_END_EARLY}
    partial_all = {d for d in dev if net_manifest_status(d) == "partial"}
    days = {d["day"] for d in frozen["days"]}
    detail = {
        "early_closes_covered": sorted(early_all - days),
        "partial_days_covered": sorted(partial_all - days),
        "reasons_missing": sorted(d["day"] for d in frozen["days"] if not d.get("reasons")),
        "not_dev": sorted(days - dev),
    }
    ok = (
        not detail["early_closes_covered"]
        and not detail["partial_days_covered"]
        and not detail["reasons_missing"]
        and not detail["not_dev"]
        and "2022-03-10" in days
    )
    return _ok("canary_covers_every_special_day", ok, detail)


def t_forbidden_negative_controls() -> dict:
    """The exclusion machinery must fail loudly on purpose-built frames."""
    results = {}
    bad = pl.DataFrame({"day": ["2021-02-01"], "v_giveback_10": [0.0]})
    try:
        assert_observation_frame(bad, "synthetic")
        results["v_column"] = False
    except ValueError:
        results["v_column"] = True
    bad2 = pl.DataFrame({"day": ["2021-02-01"], "future_forced_flat_px": [1.0]})
    try:
        assert_observation_frame(bad2, "synthetic")
        results["future_column"] = False
    except ValueError:
        results["future_column"] = True
    bad3 = pl.DataFrame({"day": ["2021-02-01"], "terminal_censored": [True]})
    try:
        assert_observation_frame(bad3, "synthetic")
        results["censor_column_without_allowance"] = False
    except ValueError:
        results["censor_column_without_allowance"] = True
    good = pl.DataFrame({"day": ["2021-02-01"], "terminal_censored": [True]})
    try:
        assert_observation_frame(good, "synthetic", allowed_censor=("terminal_censored",))
        results["censor_column_with_allowance"] = True
    except ValueError:
        results["censor_column_with_allowance"] = False
    # forward-fill control: known=false with a rank must be rejected by the null rule
    f = pl.DataFrame({"known_by_t": [False], "rank_known": [7]})
    results["forward_fill_detected"] = bool(
        f.filter((~pl.col("known_by_t")) & pl.col("rank_known").is_not_null()).height > 0
    )
    return _ok("forbidden_and_forward_fill_negative_controls", all(results.values()), results)


def t_quote_feature_guard(contracts: dict) -> dict:
    """Quote features are denied; quote-roster provenance the schema declares stays legal.

    Negative controls: a quote VALUE name is rejected with and without a schema declaration,
    an undeclared `quote_` name is rejected on a written frame, and a `quote_` name declared
    as roster provenance/counts is accepted - no prefix guard may reject existing roster
    provenance. The real frozen schema must declare no quote feature field at all.
    """
    probe_contracts = {
        "causal_registry.json": {
            "blocked_families": [
                {"family": QUOTE_FEATURE_FAMILY, "examples": ["quote_mid", "quote_spread"]}
            ]
        },
        "schema.json": {
            "tables": {
                QUOTE_ROSTER_TABLE: {
                    "fields": {
                        "quote_roster_source_sha256": {"namespace": "provenance"},
                        "quote_roster_rows": {"namespace": "roster"},
                        "symbol": {"namespace": "roster"},
                    }
                }
            }
        },
    }
    results: dict[str, Any] = {
        "value_name_denied": is_quote_feature("quote_mid", probe_contracts),
        "spread_name_denied": is_quote_feature("quote_spread", probe_contracts),
        "undeclared_prefix_denied": is_quote_feature("quote_unknown_field", probe_contracts),
        "declared_provenance_allowed": not is_quote_feature(
            "quote_roster_source_sha256", probe_contracts
        ),
        "declared_roster_count_allowed": not is_quote_feature("quote_roster_rows", probe_contracts),
        "frozen_registry_vocabulary_denied": is_quote_feature("quote_mid", contracts),
        "frozen_schema_declares_no_quote_feature": not quote_feature_fields(contracts),
    }
    value_frame = pl.DataFrame({"day": ["2021-02-01"], "quote_mid": [1.0]})
    provenance_frame = pl.DataFrame(
        {"day": ["2021-02-01"], "quote_roster_source_sha256": ["deadbeef"]}
    )
    frame_controls = (
        ("value_frame_denied_on_real_schema", value_frame, None, False),
        ("value_frame_denied_with_probe", value_frame, probe_contracts, False),
        ("provenance_frame_allowed_with_probe", provenance_frame, probe_contracts, True),
    )
    for key, frame, probe, want_pass in frame_controls:
        try:
            assert_observation_frame(frame, "synthetic", contracts=probe)
            results[key] = want_pass
        except ValueError:
            results[key] = not want_pass
    return _ok("quote_feature_guard_vs_roster_provenance", all(results.values()), results)


def t_storage_conservative_reading() -> dict:
    """The disk gate must spend no more than the output mount's backing volume can hold.

    Synthetic facts only (no filesystem dependence): a foreign/WSL-interop mount reports its
    host volume directly, a virtual-disk-backed ext4 stacks to the declared host reading and
    refuses while that declaration is missing or incomplete, and a non-virtual disk trusts
    its own guest reading.
    """
    interop = {
        "mount_fstype": "9p",
        "mount_source": "C:\\",
        "mount_point": "/mnt/c",
        "device": None,
        "device_model": None,
        "virtual_disk": None,
        "guest_free_bytes": 112 * 2**30,
    }
    virtual = {
        "mount_fstype": "ext4",
        "mount_source": "/dev/sdd",
        "mount_point": "/",
        "device": "sdd",
        "device_model": "Virtual Disk",
        "virtual_disk": True,
        "guest_free_bytes": 894 * 2**30,
    }
    plain_disk = dict(virtual, device_model="Samsung SSD 990 PRO", virtual_disk=False)
    declaration = {
        "volume": "C:\\",
        "free_bytes": 112 * 2**30,
        "measured_at": "2026-09-30T00:00:00+00:00",
        "method": "host GetDiskFreeSpaceEx probe",
        "evidence_sha256": "0" * 64,
    }
    results = {
        "interop_mount_reading_is_physical": conservative_free_bytes(interop, {})[0]
        == interop["guest_free_bytes"],
        "virtual_disk_without_declaration_refuses": conservative_free_bytes(virtual, {})[0] == 0,
        "incomplete_declaration_refuses": conservative_free_bytes(
            virtual, {"volume": "C:\\", "free_bytes": 1}
        )[0]
        == 0,
        "virtual_disk_stacks_to_declared_host": conservative_free_bytes(virtual, declaration)[0]
        == min(virtual["guest_free_bytes"], declaration["free_bytes"]),
        "physical_disk_trusts_guest": conservative_free_bytes(plain_disk, {})[0]
        == plain_disk["guest_free_bytes"],
        "declared_host_free_requires_all_fields": declared_host_free_bytes(declaration)
        == declaration["free_bytes"],
        "planning_band_contains_frozen_gate": PLANNING_HEADROOM_LO_BYTES
        <= FULL_GATE_FREE_BYTES
        <= PLANNING_HEADROOM_HI_BYTES,
    }
    return _ok("conservative_storage_reading", all(results.values()), results)


def t_resume_disk_demand() -> dict:
    """The resume gate subtracts VERIFIED completed payload bytes and keeps the reserve.

    Synthetic arithmetic, no filesystem: a fresh build keeps the reviewed conservative 75 GiB;
    a resume asks for 75 GiB minus the bytes that re-verify, floored at the 10 GiB operating
    reserve; a payload whose bytes no longer match its recorded sha contributes nothing; and an
    unbacked virtual disk still measures 0 so no amount of completed payload can rescue it.
    """
    gib = 2**30
    fresh, fresh_basis = full_demand_bytes(None)
    unverified, _ = full_demand_bytes({"verified_bytes": 0})
    partial, _ = full_demand_bytes({"verified_bytes": 20 * gib})
    most, _ = full_demand_bytes({"verified_bytes": 70 * gib})
    everything, _ = full_demand_bytes({"verified_bytes": 200 * gib})
    facts = {
        "mount_fstype": "ext4",
        "mount_source": "/dev/sdd",
        "mount_point": "/",
        "device": "sdd",
        "device_model": "Virtual Disk",
        "virtual_disk": True,
        "guest_free_bytes": 894 * gib,
    }
    physical, _src, reasons = conservative_free_bytes(facts, {})
    declaration = {
        "volume": "C:\\",
        "free_bytes": 30 * gib,
        "measured_at": "2026-09-30T00:00:00+00:00",
        "method": "host GetDiskFreeSpaceEx probe",
        "evidence_sha256": "0" * 64,
    }
    results = {
        "fresh_keeps_reviewed_75gib": fresh == FULL_GATE_FREE_BYTES,
        "fresh_basis_is_the_initial_budget": "initial budget" in fresh_basis,
        "nothing_verified_is_a_fresh_build": unverified == FULL_GATE_FREE_BYTES,
        "partial_resume_subtracts_verified_bytes": partial == FULL_GATE_FREE_BYTES - 20 * gib,
        "reserve_floor_holds": most == OPERATING_RESERVE_BYTES,
        "never_below_the_operating_reserve": everything == OPERATING_RESERVE_BYTES,
        "operating_reserve_is_10gib": 10 * gib == OPERATING_RESERVE_BYTES,
        "unbacked_virtual_disk_measures_zero": physical == 0 and bool(reasons),
        "declared_host_still_bounds_the_resume": conservative_free_bytes(facts, declaration)[0]
        == 30 * gib,
    }
    return _ok("resume_disk_demand_subtracts_only_verified_bytes", all(results.values()), results)


def t_full_build_resource_sizing() -> dict:
    """The worker budget subtracts the parent's REAL resident set, and PIT sets stay bounded."""
    import tempfile

    real_parent = parent_rss_gib()
    results: dict[str, Any] = {
        "parent_rss_is_measured": real_parent > 0.0,
        "pit_set_cache_is_bounded": 0 < PIT_VINTAGE_CACHE_MAX <= 8,
    }
    soft, _hard = rss_policy()
    naive_cap = max(1, int(soft // MEASURED_CHILD_RSS_GIB))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = root / "a.bin"
        f.write_bytes(b"hello")
        _CONTENT_SHA_MEMO.clear()
        first = content_sha(f)
        again = content_sha(f)
        results["content_sha_is_stable"] = first == again and first is not None
        # a CHANGED file must re-hash: the memo is keyed by path+size+mtime, never by path alone
        f.write_bytes(b"hello world, different bytes")
        results["changed_file_rehashes"] = content_sha(f) != first
        # a file that does not exist has no digest and must not be memoized as one
        results["missing_file_has_no_digest"] = content_sha(root / "nope.bin") is None
    # a parent holding nearly all of the soft budget must leave room for at most one worker
    with _parent_rss(lambda: soft - 0.5):
        fat = effective_workers(2)
    results["fat_parent_forces_one_worker"] = fat == 1
    with _parent_rss(lambda: 0.1):
        thin = effective_workers(2)
    results["thin_parent_never_exceeds_the_naive_cap"] = thin <= naive_cap
    results["at_least_one_worker_always"] = fat >= 1 and thin >= 1
    return _ok("full_build_resource_sizing_and_bounded_caches", all(results.values()), results)


@contextlib.contextmanager
def _parent_rss(fn):
    """Temporarily report a synthetic parent RSS (self-tests only)."""
    orig = peak_rss_gb
    try:
        globals()["peak_rss_gb"] = fn
        yield
    finally:
        globals()["peak_rss_gb"] = orig


def t_prefix_perturbation() -> dict:
    """Later information must not be able to change what an EARLIER instant reports.

    Synthetic, against the real helpers. Three perturbations: adding a later roster snapshot
    must leave every earlier row's `roster_rules` byte-identical; a later membership must not
    change the causal view (the board's own per-t rules are what a prospective reader sees); and
    the prospective-read filter must refuse pre-session, post-session and pre-entry rows while
    the stored rows remain intact - the filter narrows a SELECTION, it never deletes data.
    """
    ts = [570, 575, 600, 700]

    def rules_at(sched: list[tuple[int, str]], t_val: int) -> list[str]:
        return [r for sel, r in sorted(sched) if sel <= t_val]

    base_sched = [(575, "B@575"), (600, "B@600")]
    later_sched = base_sched + [(700, "B@700"), (720, "B@720")]
    results: dict[str, Any] = {
        "earlier_rows_identical_after_a_later_snapshot": all(
            rules_at(base_sched, t) == rules_at(later_sched, t) for t in ts if t < 700
        ),
        "the_later_row_sees_the_added_snapshot": rules_at(later_sched, 720)
        != rules_at(base_sched, 720),
    }
    # the prospective filter narrows a selection; the stored frame keeps every row
    # 560 pre-open, 574 strictly pre-entry, 575 == entry (readable), 600 in-window,
    # 700 after `now`, 970 after-hours. Exactly 575 and 600 may be read at now_et=650.
    ets = [560, 574, 575, 600, 700, 970]
    frame = pl.DataFrame(
        {
            "day": ["2026-04-01"] * len(ets),
            "et": ets,
            "first_entry_et": [575] * len(ets),
            "within_session": [False, True, True, True, True, False],
            "px": [1.0] * len(ets),
        }
    )
    readable = prospective_read_filter(frame, now_et=650)
    kept = sorted(readable["et"].to_list())
    results["pre_open_rows_are_refused"] = 560 not in kept
    results["strictly_pre_entry_rows_are_refused"] = 574 not in kept
    results["the_entry_instant_is_readable"] = 575 in kept
    results["post_session_rows_are_refused"] = 970 not in kept
    results["rows_after_now_are_refused"] = 700 not in kept
    results["in_session_post_entry_rows_survive"] = kept == [575, 600]
    results["no_rows_were_deleted"] = frame.height == len(ets) and readable.height == 2
    return _ok("prefix_perturbation_cannot_change_earlier_rows", all(results.values()), results)


def prospective_read_filter(df: pl.DataFrame, now_et: int) -> pl.DataFrame:
    """The PROSPECTIVE read selection: in-session, at or after the path's first entry, t <= now.

    This narrows what a reader may SELECT. The stored frames keep every projection, pre-entry
    and after-hours row for the retrospective audit; nothing is dropped from the corpus.
    """
    return df.filter(
        pl.col("within_session")
        & (pl.col("et") <= now_et)
        & (pl.col("first_entry_et").is_null() | (pl.col("et") >= pl.col("first_entry_et")))
    )


def t_post_build_chain_small_slice() -> dict:
    """Run the REAL post-build chain on a REAL slice, through build_manifest.

    Three post-build defects in a row were each invisible to a narrower control: the polars
    inference bug needed a >100-row registry, the lane-URI bug needed build_manifest, and both
    were found only after a 40-minute canary. This exercises registry -> patch -> coverage ->
    paths/memberships -> build_manifest on real dev days, with a deliberately >100-row registry so
    the inference path is live, and asserts the mixed absolute/relative lane paths assemble.
    """
    import tempfile

    results: dict[str, Any] = {}
    dev = dev_days()
    with tempfile.TemporaryDirectory() as tmp:
        out_data = Path(tmp) / "out"
        # a >100-row slice so the registry spans polars' default 100-row inference window, AND it
        # must CONTAIN acquisition days, because a slice without them exercises neither the mixed
        # absolute/relative lane paths nor the acquisition column typing.
        acq_days = [d for d in dev if joint_day_binding(d) is not None][:3]
        filler = [d for d in dev if d not in set(acq_days)][:110]
        small = sorted(set(acq_days) | set(filler))
        canary = [{"day": d, "reasons": ["synthetic"]} for d in acq_days]
        calendar, cal_sha = session_calendar()
        reg = build_day_registry(
            small, calendar, cal_sha, panel_memberships(), canary, built_days=set(acq_days)
        )
        results["registry_exceeds_the_inference_window"] = reg.height > 100
        results["acq_path_is_string_with_non_null"] = str(
            reg.schema["acq_bars_path"]
        ) == "String" and reg.filter(pl.col("acq_bars_path").is_not_null()).height == len(acq_days)
        lane_values = [
            str(r.get(k) or "")
            for r in reg.iter_rows(named=True)
            for k in ("raw_baseline_path", "acq_bars_path")
            if r.get(k)
        ]
        results["mixed_absolute_and_relative_lane_paths_present"] = any(
            Path(v).is_absolute() for v in lane_values
        ) and any(not Path(v).is_absolute() for v in lane_values)
        stats = {
            "raw_n_symbols": 10,
            "raw_n_pit_symbols": 9,
            "raw_n_pit_symbols_full_window": 9,
            "raw_n_pit_offhours_only": 0,
            "raw_roster_missing_n": 0,
            "raw_roster_verified_absent_n": 0,
            "raw_roster_verified_no_sip_bar_n": 0,
            "raw_roster_cross_feed_witnessed_n": 0,
            "raw_roster_no_sip_bar_also_witnessed_n": 0,
            "raw_roster_missing_unresolved_n": 0,
        }
        reg = patch_registry(reg, {d: {"stats": dict(stats)} for d in acq_days})
        results["patch_ok_on_wide_slice"] = reg.height == len(small)
        # every recorded lane path must resolve to a real file with a usable file URI
        bad_uri, bad_abs = [], []
        for r in reg.iter_rows(named=True):
            for key in ("raw_baseline_path", "acq_bars_path"):
                raw = r.get(key)
                if not raw:
                    continue
                p = _data_relative_path(raw)
                if not p.is_absolute():
                    bad_abs.append(str(raw))
                if not p.exists():
                    bad_uri.append(f"{r['day']}:{key}")
        results["all_lane_paths_resolve_absolute"] = not bad_abs
        results["all_lane_paths_exist"] = not bad_uri
        results["all_lane_paths_have_a_file_uri"] = all(
            _data_relative_path(r.get(k) or "").is_absolute()
            for r in reg.iter_rows(named=True)
            for k in ("raw_baseline_path", "acq_bars_path")
            if r.get(k)
        )
        # and the real build_manifest must assemble lane parents for those rows
        shared: dict[str, dict] = {}
        day_results = {d: {"layers": {}, "stats": dict(stats)} for d in acq_days}
        manifest = build_manifest(
            out_data,
            load_contracts(),
            "0" * 64,
            day_results,
            shared,
            reg,
            {"selection_classes": {}, "canary_days": acq_days},
        )
        lane_parents = [k for k in manifest["parents"] if k.startswith("raw_lane:")]
        results["manifest_assembles_lane_parents"] = bool(lane_parents)
        results["manifest_lane_parents_all_absolute_uris"] = all(
            str(manifest["parents"][k]["source_uri"]).startswith("file:") for k in lane_parents
        )
    return _ok("post_build_chain_on_real_slice", all(results.values()), results)


def t_registry_patch_contract() -> dict:
    """build_day_registry -> patch_registry end to end on the REAL functions, no hand-built frame.

    A hand-built frame is not evidence here: it is how the coverage no-op and the dead guard both
    passed while production was broken. This runs the actual registry builder over a small real dev
    slice and then the actual patch step with real per-day stats, and additionally proves the
    refusal path by dropping a declared column from the registry.
    """
    results: dict[str, Any] = {}
    dev = dev_days()
    small = dev[:2]
    calendar, cal_sha = session_calendar()
    canary = [{"day": d, "reasons": ["synthetic"]} for d in small]
    reg = build_day_registry(
        small, calendar, cal_sha, panel_memberships(), canary, built_days=set(small)
    )
    results["registry_builds"] = reg.height == len(small)
    results["registry_carries_every_declared_column"] = all(
        c in reg.columns for c in REGISTRY_PATCH_COLUMNS
    )
    results["registry_declared_columns_are_null_before_patch"] = all(
        reg[c].null_count() == reg.height for c in REGISTRY_PATCH_COLUMNS
    )
    stats = {
        "raw_n_symbols": 10,
        "raw_n_pit_symbols": 9,
        "raw_n_pit_symbols_full_window": 9,
        "raw_n_pit_offhours_only": 1,
        "raw_roster_missing_n": 3,
        "raw_roster_verified_absent_n": 1,
        "raw_roster_verified_no_sip_bar_n": 1,
        "raw_roster_cross_feed_witnessed_n": 1,
        "raw_roster_no_sip_bar_also_witnessed_n": 1,
        "raw_roster_missing_unresolved_n": 0,
    }
    patched = patch_registry(reg, {d: {"stats": dict(stats)} for d in small})
    results["patch_succeeds"] = patched.height == len(small)
    results["patch_fills_the_counter"] = patched["raw_roster_missing_n"].to_list() == [3, 3]
    results["patch_fills_the_offhours_counter"] = patched["raw_n_pit_offhours_only"].to_list() == [
        1,
        1,
    ]
    results["no_duplicate_columns_after_join"] = len(patched.columns) == len(set(patched.columns))
    # refusal path: a registry missing a declared column must RAISE, not be tolerated
    broken = reg.drop([REGISTRY_PATCH_COLUMNS[0]])
    try:
        patch_registry(broken, {d: {"stats": dict(stats)} for d in small})
        results["missing_declared_column_refused"] = False
    except ValueError as exc:
        results["missing_declared_column_refused"] = REGISTRY_PATCH_COLUMNS[0] in str(exc)
    # and stats that omit a declared column must raise too
    short_stats = dict(stats)
    short_stats.pop(REGISTRY_PATCH_COLUMNS[0])
    try:
        patch_registry(reg, {d: {"stats": dict(short_stats)} for d in small})
        results["missing_stat_column_refused"] = False
    except ValueError as exc:
        results["missing_stat_column_refused"] = REGISTRY_PATCH_COLUMNS[0] in str(exc)
    return _ok("registry_patch_column_contract", all(results.values()), results)


def t_source_generation_guard_fires() -> dict:
    """The source-generation guard must FIRE, proven by mutating a file the real code path reads.

    Structural checks cannot catch this class: the previous guard compared the import-time
    constant against itself, so it was a tautology and never fired while a run kept building
    days against a moved file. This drives the REAL `_current_source_sha()` read by repointing
    `SOURCE_PATH` at a temp file and mutating it - no hasher argument that production ignores.
    """
    import tempfile

    results: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "producer_copy.py"
        fake.write_text("# generation A\n")
        bound = sha256_file(fake)  # what a run would bind at dispatch
        original = globals()["SOURCE_PATH"]
        try:
            globals()["SOURCE_PATH"] = fake  # the SAME global the production read uses
            # 1. unchanged file: the real read says it still matches
            results["unchanged_source_matches"] = _source_file_matches(bound) is True
            # 2. the file moves: the guard must fire
            fake.write_text("# generation B -- edited mid-run\n")
            results["moved_source_does_not_match"] = _source_file_matches(bound) is False
            # the production guard, driven with the live process's own import binding, now
            # refuses because the file it reads no longer hashes to that binding
            try:
                source_generation_ok(SOURCE_GENERATION_SHA, "test/after_edit")
                results["moved_source_refuses"] = False
            except SystemExit as exc:
                results["moved_source_refuses"] = True
                results["refusal_names_the_drift"] = "drift" in str(exc) or "unreadable" in str(exc)
                results["refusal_preserves_outputs"] = "preserved" in str(exc)
            # 3. the file becomes unreadable: fail CLOSED, not pass
            fake.unlink()
            results["unreadable_file_reports_none"] = _source_file_matches(bound) is None
            try:
                source_generation_ok(SOURCE_GENERATION_SHA, "test/unreadable")
                results["unreadable_source_refuses"] = False
            except SystemExit as exc:
                results["unreadable_source_refuses"] = "unreadable" in str(exc)
            # 4. entry-to-spawn drift: a binding that is not this process's IMPORT binding is
            #    refused even though the file may be unchanged
            results["bogus_binding_does_not_match"] = _import_binding_matches("0" * 64) is False
            results["own_binding_matches"] = _import_binding_matches(SOURCE_GENERATION_SHA) is True
            try:
                source_generation_ok("0" * 64, "test/worker_mismatch")
                results["worker_binding_mismatch_refused"] = False
            except SystemExit as exc:
                results["worker_binding_mismatch_refused"] = "imported" in str(exc)
        finally:
            globals()["SOURCE_PATH"] = original
    # 5. the live producer still passes its own guard with its own import binding
    source_generation_ok(SOURCE_GENERATION_SHA, "test/live")
    results["live_producer_self_check_passes"] = True
    return _ok("source_generation_guard_actually_fires", all(results.values()), results)


def t_offhours_only_name_is_observed() -> dict:
    """A name with bars ONLY outside the RTH window is observed for COVERAGE, and has no board row.

    2025-02-03 NTZ is the worked counterexample: exactly one admitted bar at etm 961 (16:01 ET),
    PIT-present, not an API zero, not a witness, not in `unavailable`. Deriving coverage from the
    RTH-trimmed frame made it a false gap. Coverage comes from the full declared window; the
    BOARD stays RTH-trimmed; both populations are reported and reconcile.
    """
    import tempfile

    results: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bars = root / "bars"
        bars.mkdir()
        day = "2025-02-03"
        # the single NTZ bar at 2025-02-03T21:01Z = 16:01 ET = etm 961
        df = pl.DataFrame(
            {
                "timestamp": [datetime(2025, 2, 3, 21, 1, tzinfo=UTC)],
                "ticker": ["NTZ"],
                "provider_symbol": ["PNTZ"],
                "open": [1.0],
                "high": [2.0],
                "low": [0.5],
                "close": [1.5],
                "volume": [137.0],
            }
        ).with_columns(pl.col("timestamp").cast(pl.Datetime("ns", "UTC")))
        p = bars / f"{day}.parquet"
        df.write_parquet(p)
        man = {
            "day": day,
            "scope": "feb2025",
            "status": "complete",
            "file": p.name,
            "sha256": sha256_file(p),
            "source": {"feed": "sip", "adjustment": "raw"},
            "errors": [],
            "sip_coverage": {"symbols_zero_bars": []},
        }
        (bars / f"{day}.manifest.json").write_text(json.dumps(man))
        (root / "rosters").mkdir()
        (root / "rosters" / f"{day}.json").write_text(json.dumps({"unavailable": {}}))
        _write_day_binding(root, day)
        with _acq_root(root):
            # BOTH reads go through the production lane reader: the board read is RTH-trimmed,
            # the coverage read uses the declared projection window. Reading the parquet directly
            # (or injecting `raw`) bypasses the very filter that caused the defect, which is how
            # the first version of this control passed while production was still a no-op.
            board_names = set(read_raw_day(day, 959)["ticker"].to_list())
            coverage_frame = read_raw_days(
                [(day, COVERAGE_HI)], et_lo=COVERAGE_LO, et_hi=COVERAGE_HI
            )
            etms = sorted(set(coverage_frame["etm"].to_list()))
            coverage_names = set(coverage_frame["ticker"].unique().to_list())
            production_coverage = read_coverage_names(day)
        results["offhours_bar_is_outside_rth"] = all(e > 959 for e in etms) and etms != []
        results["no_rth_board_row"] = "NTZ" not in board_names
        results["coverage_still_observes_it"] = "NTZ" in coverage_names
        results["production_coverage_read_observes_it"] = "NTZ" in production_coverage
        results["production_coverage_is_wider_than_the_board"] = production_coverage > board_names
        results["populations_reconcile"] = len(coverage_names) - len(board_names) == 1
    return _ok("offhours_only_name_observed_for_coverage", all(results.values()), results)


def t_evidence_forgery_refusal() -> dict:
    """The file we HASH must be the file we CONSULT, and evidence must live under TRACK.

    Without these checks an unrelated JSON under the tracked root, whose sha the declaration
    matches, would nominally attest a blocker whose verdict was really derived from the joint
    admission or the reconciliation - the declaration would be satisfied by the wrong file.
    """
    import tempfile

    results: dict[str, Any] = {}

    def state(evidence: Path | None, declared_sha: str | None = None) -> dict:
        rec = {}
        if evidence is not None:
            rec["evidence_path"] = str(evidence)
        if declared_sha:
            rec["evidence_sha256"] = declared_sha
        return blocker_evidence_state("B1_raw_2025_02", {"B1_raw_2025_02": rec})

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        outside = root / "elsewhere.json"
        outside.write_text(json.dumps({"attested": "nothing"}))
        # 1. a path OUTSIDE the tracked evidence root is refused outright
        st = state(outside, sha256_file(outside))
        results["outside_track_evidence_refused"] = not st["verified"] and any(
            "outside the tracked evidence root" in p for p in st["problems"]
        )
        results["outside_track_problem_named"] = st["problems"] != []
        # 2. a real file INSIDE TRACK but NOT the authoritative admission evidence is refused,
        #    even when its sha matches the declaration exactly
        forger = TRACK / "acquisition" / "not_the_admission.json"
        forger.parent.mkdir(parents=True, exist_ok=True)
        forger.write_text(json.dumps({"all_scopes_ready": False}))
        try:
            st = state(forger, sha256_file(forger))
            results["wrong_evidence_under_track_refused"] = not st["verified"]
            results["wrong_evidence_problem_named"] = any(
                "is not the acquisition admission evidence" in p for p in st["problems"]
            )
            results["sha_alone_does_not_attest"] = st["verified"] is False
        finally:
            forger.unlink(missing_ok=True)
        # 3. the genuine joint evidence is accepted as a path (its verdict still governs)
        if ACQ_JOINT_EVIDENCE.exists():
            st = state(ACQ_JOINT_EVIDENCE, sha256_file(ACQ_JOINT_EVIDENCE))
            results["authoritative_path_not_rejected_for_identity"] = not any(
                "is not the acquisition admission evidence" in p for p in st["problems"]
            )
    return _ok(
        "declared_evidence_must_be_the_authoritative_artifact", all(results.values()), results
    )


def t_reference_unqualified_rank_nulling() -> dict:
    """A stale or floor-qualified previous close cannot become a QUALITY-FILTERED rank.

    2025-02-03's prior reference is 2023-12-29 (2025-01-31 is sealed and never read), so the
    reference is a known-stale cross-block stand-in. The contract forbids a stale or
    floor-qualified denominator from supporting a rank-trajectory or era-stability claim, so the
    quality-filtered rank family must be null for those rows while the RAW px/gain remain
    available as reference-only. No row is dropped and no sealed file is fetched.
    """
    known = np.array([True, True, True])
    px_flat = np.array([10.0, 11.0, 12.0])
    prev_rep = np.array([10.0, 10.0, 10.0])
    gain = np.array([0.0, 0.1, 0.2])  # all ordinary: the ONLY reason to exclude is the reference
    clean_flags = np.array([False, False, False])
    args = (known, px_flat, prev_rep, gain, clean_flags)

    ok_ref, _n, _x, _d, _r = rank_eligibility_mask(*args, np.array([False, False, False]))
    stale_ref, _n2, _x2, _d2, ref_excl = rank_eligibility_mask(*args, np.array([False, True, True]))
    results: dict[str, Any] = {
        "a_qualified_reference_is_eligible": bool(ok_ref[0]),
        "an_ordinary_reference_is_eligible": bool(ok_ref[1]) and bool(ok_ref[2]),
        "a_stale_reference_cannot_become_eligible": not bool(stale_ref[1])
        and not bool(stale_ref[2]),
        "the_stale_reference_rows_are_flagged": bool(ref_excl[1]) and bool(ref_excl[2]),
        "only_the_flagged_rows_are_excluded": not bool(ref_excl[0]),
        # a stale reference must not be excused as an ordinary API zero or envelope issue:
        # the exclusion is specifically the reference, so an otherwise-clean row still drops
        "not_excused_by_a_discrepancy_or_envelope": not bool(
            rank_eligibility_mask(*args, np.array([False, True, True]))[3][1]
        ),
    }
    return _ok("unqualified_reference_nulls_only_the_quality_rank", all(results.values()), results)


def t_cross_feed_class_precedence() -> dict:
    """A witnessed name surfaces as WITNESSED even though it is also an API zero.

    Every cross-feed-witnessed name is necessarily an API zero - the SIP pull returning no bar is
    why it was witnessed - so an ordering that tests the API zero first makes the cross-feed class
    unreachable and its count permanently 0. Controls use a name present in BOTH sets and assert
    the witnessed class wins, while the SIP REQUEST accounting still counts that name, and the
    overlap is published rather than hidden.
    """
    import tempfile

    results: dict[str, Any] = {}

    def classify(manifest_extra: dict, roster: dict, names: list[str]) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bars = root / "bars"
            bars.mkdir()
            day = "2025-02-03"
            df = pl.DataFrame(
                {
                    "timestamp": [datetime(2025, 2, 3, 14, 30, tzinfo=UTC)],
                    "ticker": ["AAA"],
                    "open": [1.0],
                    "high": [2.0],
                    "low": [0.5],
                    "close": [1.5],
                    "volume": [100.0],
                }
            ).with_columns(pl.col("timestamp").cast(pl.Datetime("ns", "UTC")))
            p = bars / f"{day}.parquet"
            df.write_parquet(p)
            man = {
                "day": day,
                "scope": "feb2025",
                "status": "complete",
                "file": p.name,
                "sha256": sha256_file(p),
                "source": {"feed": "sip", "adjustment": "raw"},
                "errors": [],
            }
            man.update(manifest_extra)
            (bars / f"{day}.manifest.json").write_text(json.dumps(man))
            (root / "rosters").mkdir()
            (root / "rosters" / f"{day}.json").write_text(json.dumps(roster))
            _write_day_binding(root, day)
            with _acq_root(root):
                return unobserved_classification(day, set(names))

    both = ["DXR", "ONLYZERO", "ONLYWIT", "NEITHER"]
    cls = classify(
        {
            "sip_coverage": {"symbols_zero_bars": ["DXR", "ONLYZERO"]},
            "evidence_crosscheck": {"cross_feed_names": ["DXR", "ONLYWIT"]},
        },
        {"unavailable": {"absent_all_sources": []}},
        both,
    )
    results["witnessed_wins_over_api_zero"] = cls["DXR"] == CLASS_CROSSFEED_WITNESS
    results["api_zero_alone_is_no_sip_bar"] = cls["ONLYZERO"] == CLASS_NO_SIP_BAR
    results["witnessed_alone_is_witnessed"] = cls["ONLYWIT"] == CLASS_CROSSFEED_WITNESS
    results["neither_is_the_hole"] = cls["NEITHER"] == CLASS_UNRESOLVED
    results["cross_feed_class_is_reachable"] = CLASS_CROSSFEED_WITNESS not in {
        "verified_no_bar_in_declared_sources",
        "verified_no_sip_bar",
        "missing_confirmed_same_feed_bar",
    }
    return _ok("cross_feed_class_precedence_over_api_zero", all(results.values()), results)


def t_origin_proof_chain() -> dict:
    """Origin proof is ONE equality, fail-closed, and a wrapper cannot forge it.

    A throwaway root publishes a real hashed origin snapshot. Controls: the full chain
    (re-hashed bytes == snapshot_sha256 == code_sha256_at_capture == manifest.code_sha256) admits;
    a wrapper whose bytes hash to snapshot_sha256 but which is NOT the pinned capture source is
    refused; a missing pin, a missing snapshot, an altered snapshot and a manifest naming a
    different producer are each refused. A loose end-to-end check would pass the wrapper case.
    """
    import tempfile

    results: dict[str, Any] = {}

    def build(mangle=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bars = root / "bars"
            bars.mkdir()
            day = "2025-02-03"
            df = pl.DataFrame(
                {
                    "timestamp": [datetime(2025, 2, 3, 14, 30, tzinfo=UTC)],
                    "ticker": ["AAA"],
                    "open": [1.0],
                    "high": [2.0],
                    "low": [0.5],
                    "close": [1.5],
                    "volume": [100.0],
                }
            ).with_columns(pl.col("timestamp").cast(pl.Datetime("ns", "UTC")))
            p = bars / f"{day}.parquet"
            df.write_parquet(p)
            man = {
                "day": day,
                "scope": "feb2025",
                "status": "complete",
                "file": p.name,
                "sha256": sha256_file(p),
                "source": {"feed": "sip", "adjustment": "raw"},
                "errors": [],
            }
            (bars / f"{day}.manifest.json").write_text(json.dumps(man))
            (root / "rosters").mkdir()
            (root / "rosters" / f"{day}.json").write_text("{}")
            _write_day_binding(root, day)
            if mangle:
                mangle(root, day)
            with _acq_root(root):
                try:
                    got = _validate_acquisition_day(day)
                except ValueError as exc:
                    return False, f"refused: {exc}"
            return got is not None, "admitted"

    ok, why = build()
    results["exact_chain_admits"] = ok
    results["positive_control_really_admitted"] = why == "admitted"

    def drop_pin(root, _day):
        ev = root / "evidence/acquisition_admission.json"
        doc = load_json(ev)
        doc["blockers"]["B1_raw_2025_02"]["producer"].pop("code_sha256_at_capture", None)
        ev.write_text(json.dumps(doc))

    results["missing_pin_refused"] = build(drop_pin)[0] is False

    def drop_snapshot(root, _day):
        ev = root / "evidence/acquisition_admission.json"
        doc = load_json(ev)
        doc["blockers"]["B1_raw_2025_02"]["producer"].pop("snapshot_path", None)
        ev.write_text(json.dumps(doc))

    results["missing_snapshot_refused"] = build(drop_snapshot)[0] is False

    def wrapper_not_the_capture(root, _day):
        # snapshot_path points at a DIFFERENT file that hashes to snapshot_sha256, so a loose
        # "bytes == declared" check passes but the file is not the pinned capture source
        ev = root / "evidence/acquisition_admission.json"
        doc = load_json(ev)
        snap = Path(doc["blockers"]["B1_raw_2025_02"]["producer"]["snapshot_path"])
        wrapper = root / "producers" / "wrapper.py"
        wrapper.write_text(snap.read_text() + "\n# wrapper metadata\n")
        doc["blockers"]["B1_raw_2025_02"]["producer"]["snapshot_path"] = str(wrapper)
        doc["blockers"]["B1_raw_2025_02"]["producer"]["snapshot_sha256"] = sha256_file(wrapper)
        ev.write_text(json.dumps(doc))

    results["wrapper_is_not_the_capture_source_refused"] = (
        build(wrapper_not_the_capture)[0] is False
    )

    def other_producer(root, day):
        mp = root / "bars" / f"{day}.manifest.json"
        doc = load_json(mp)
        doc["code_sha256"] = "b" * 64
        mp.write_text(json.dumps(doc))

    results["manifest_naming_another_producer_refused"] = build(other_producer)[0] is False
    return _ok("origin_proof_is_one_four_way_equality", all(results.values()), results)


def t_acquisition_obligation_rules() -> dict:
    """A failed request is NOT a zero-bar name, and an owed name is NOT verified absent.

    Synthetic manifests in a throwaway root against the real validator: `symbols_request_failed`
    blocks admission outright (we know nothing about whether the name traded), a genuine
    `symbols_zero_bars` does not; a declared `obligation.baseline_standing` that contradicts the
    scope REFUSES rather than being inferred from it; and `unavailable.baseline_only_observed`
    names are OWED by a replace scope, so they never enter the verified-absent set.
    """
    import tempfile

    results: dict[str, Any] = {}

    def build(overrides: dict, roster: dict):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bars_dir = root / "bars"
            bars_dir.mkdir()
            day = "2025-02-03"
            df = (
                pl.DataFrame(
                    {
                        "timestamp": [datetime(2025, 2, 3, 14, 30, tzinfo=UTC)],
                        "ticker": ["AAA"],
                        "open": [1.0],
                        "high": [2.0],
                        "low": [0.5],
                        "close": [1.5],
                        "volume": [100.0],
                    }
                )
                .with_columns(pl.col("timestamp").cast(pl.Datetime("ns", "UTC")))
                .with_columns(pl.lit("PSYM").alias("provider_symbol"))
            )
            p = bars_dir / f"{day}.parquet"
            df.write_parquet(p)
            man = {
                "day": day,
                "scope": "feb2025",
                "status": "complete",
                "file": p.name,
                "sha256": sha256_file(p),
                "source": {"feed": "sip", "adjustment": "raw", "asof": day},
                "errors": [],
                "obligation": {
                    "baseline_standing": "replaced_by_this_acquisition",
                    "rule": "PIT x (compact-observed UNION replaced-baseline in-window)",
                    "required_names": 1,
                    "observed_evidence_names": 1,
                },
            }
            man.update(overrides)
            (bars_dir / f"{day}.manifest.json").write_text(json.dumps(man))
            (root / "rosters").mkdir()
            (root / "rosters" / f"{day}.json").write_text(json.dumps(roster))
            _write_day_binding(root, day)
            with _acq_root(root):
                got = _validate_acquisition_day(day)
                return got is not None, got, verified_absent_names(day)

    admitted, _g, _a = build({}, {"unavailable": {"absent_all_sources": ["ZZZ"]}})
    results["clean_day_admitted"] = admitted
    failed, _g2, _a2 = build({"symbols_request_failed": ["QQQ"]}, {"unavailable": {}})
    results["request_failed_blocks_admission"] = not failed
    zero_ok, _g3, _a3 = build({"symbols_zero_bars": ["ZZZ"]}, {"unavailable": {}})
    results["genuine_zero_bar_still_admits"] = zero_ok
    try:
        build({"obligation": {"baseline_standing": "supplemented"}}, {"unavailable": {}})
        results["contradictory_standing_refuses"] = False
    except ValueError:
        results["contradictory_standing_refuses"] = True
    _ok4, _g4, absent4 = build(
        {},
        {
            "unavailable": {
                "absent_all_sources": ["ZZZ"],
                "baseline_only_observed": ["BK", "ARMN"],
            }
        },
    )
    results["absent_all_sources_is_verified_absent"] = "ZZZ" in absent4
    results["baseline_only_names_are_owed_not_absent"] = not ({"BK", "ARMN"} & set(absent4))
    return _ok("acquisition_request_failed_and_owed_names", all(results.values()), results)


def t_resume_progress_credit() -> dict:
    """A crashed full run credits VERIFIED per-day progress, and never unverified credit.

    The final manifest only exists after every day completes, so a mid-run crash leaves none.
    The incremental `_progress.json` does exist, and each entry's payload shas are re-hashed
    here: a matching payload earns its bytes, a corrupted one earns NOTHING, and a claimed sha
    for a file that is absent earns nothing.
    """
    import tempfile

    results: dict[str, Any] = {"corrupt_file_exists_and_differs": False}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        good = root / "good.bin"
        good.write_bytes(b"x" * 4096)
        corrupt = root / "corrupt.bin"
        corrupt.write_bytes(b"y" * 4096)
        good_sha = sha256_file(good)
        results["corrupt_file_exists_and_differs"] = corrupt.exists()
        progress = {
            "days": {
                "2026-04-01": {
                    "layers": {
                        "race_minute_full": {"path": "good.bin", "sha256": good_sha},
                        "race_candidate_net": {
                            "path": "corrupt.bin",
                            # a stale recorded sha for bytes that no longer match
                            "sha256": "0" * 64,
                        },
                        "quote_rosters": {"path": "never_written.bin", "sha256": good_sha},
                    }
                }
            }
        }
        got = verified_completed_payload_bytes(root, None, progress)
        results["no_manifest_falls_back_to_progress"] = got["verified_bytes"] > 0
        results["progress_days_recorded"] = got["progress_days"] == ["2026-04-01"]
        results["only_the_matching_payload_earns_credit"] = got["verified_bytes"] == len(
            b"x" * 4096
        )
        results["corrupt_payload_is_mismatched_not_credited"] = any(
            "race_candidate_net" in k for k in got["mismatched"]
        )
        results["absent_payload_earns_nothing"] = any("quote_rosters" in k for k in got["absent"])
        # the floor still holds no matter how much verified progress exists
        floor = full_demand_bytes(got)[0]
        results["reserve_floor_preserved"] = floor >= OPERATING_RESERVE_BYTES
    return _ok("resume_credits_only_rehashed_progress", all(results.values()), results)


def t_multi_lane_digest() -> dict:
    """The recorded raw-lane value is the LANE-SET digest, so a two-lane day cannot pass a
    bare-file comparison - and a wrong lane set cannot pass a lane-set comparison.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = root / "baseline.parquet"
        extra = root / "acquired.parquet"
        base.write_bytes(b"baseline bytes")
        extra.write_bytes(b"acquired bytes")
        one = [{"kind": "backfill_raw", "role": "baseline", "path": base}]
        two = [
            {"kind": "backfill_raw", "role": "baseline", "path": base},
            {"kind": "acquired_day", "role": "acquired_day", "path": extra},
        ]
        d_one = combined_lane_sha({"day": "2026-04-01", "lanes": one, "alias_map_sha256": None})
        d_two = combined_lane_sha({"day": "2026-04-01", "lanes": two, "alias_map_sha256": None})
        results = {
            "lane_digest_is_not_the_bare_file_sha": d_one != sha256_file(base),
            "adding_a_lane_changes_the_digest": d_one != d_two,
            "digest_is_deterministic": d_two
            == combined_lane_sha({"day": "2026-04-01", "lanes": two, "alias_map_sha256": None}),
            "reordering_lanes_does_not_change_it": d_two
            == combined_lane_sha(
                {"day": "2026-04-01", "lanes": list(reversed(two)), "alias_map_sha256": None}
            ),
            "the_alias_map_is_part_of_the_digest": d_two
            != combined_lane_sha({"day": "2026-04-01", "lanes": two, "alias_map_sha256": "abc"}),
        }
    return _ok("multi_lane_digest_covers_the_whole_lane_set", all(results.values()), results)


def t_pure_rank_and_state() -> dict:
    """Synthetic rank/competition/order-slot and condition-axis checks."""
    vals = np.array([0.10, 0.20, 0.20, -0.05, np.nan])
    pop = np.array([True, True, True, True, False])
    rk = _competition_rank(vals, pop)
    ok_rank = rk[0] == 3 and rk[1] == 1 and rk[2] == 1 and rk[3] == 4 and np.isnan(rk[4])
    prints = pl.DataFrame(
        {
            "conditions": [[" ", "F", "T"], ["M"], ["C"], ["9"]],
            "tape": ["C", "C", "C", "C"],
            "ts_utc": [datetime(2021, 2, 1, 14, 30, tzinfo=UTC)] * 4,
            "price": [1.0, 1.0, 1.0, 1.0],
            "trade_id": [1, 2, 3, 4],
        }
    )
    axes = condition_axes(prints["conditions"].to_list(), prints["tape"].to_list())
    # frozen sip_bars table: " " updates everything; M and 9 update nothing; C volume only
    m = {r["_ck"]: (r["oc"], r["hl"], r["v"]) for r in axes.iter_rows(named=True)}
    ok_axes = (
        (m[" |F|T"] == (2, 2, 2))
        and (m["M"] == (0, 0, 0))
        and (m["9"] == (0, 0, 0))
        and (m["C"] == (0, 0, 2))
    )
    return _ok(
        "pure_rank_and_condition_axes",
        bool(ok_rank and ok_axes),
        {"rank": [None if np.isnan(v) else int(v) for v in rk], "axes": m},
    )


def t_rank_filter_semantics() -> dict:
    """The provider day high is a DAY MAX, and a bad denominator nulls the quality rank family.

    Synthetic only. Controls: the per-ticker max survives a name whose high peaks mid-session
    and closes lower (a last-minute dict would report the close); a previous close off the
    prior session by more than the 10% hard warning removes the row from `eligible` and therefore
    from the filtered rank, while the nonpositive-price and zero-denominator filters still
    apply; and the envelope - which needs the whole day - is never a rank filter.
    """
    highs = day_max_per_ticker(
        ["AAA", "AAA", "AAA", "BBB", "CCC", "CCC"],
        [1.0, 9.0, 2.0, None, 3.0, float("nan")],
    )
    known = np.array([True, True, True])
    px_flat = np.array([10.0, 0.0, 5.0])
    prev_rep = np.array([10.0, 1.0, 0.0])
    gain = np.array([0.0, -1.0, float("inf")])
    clean, nonpos, extreme, _e, _r = rank_eligibility_mask(
        known, px_flat, prev_rep, gain, np.array([False, False, False])
    )
    discrep, _, _, excl, _ = rank_eligibility_mask(
        known, px_flat, prev_rep, gain, np.array([True, True, True])
    )
    results = {
        "day_max_not_last_minute": highs["AAA"] == 9.0,
        "all_null_ticker_absent": "BBB" not in highs,
        "nan_bar_does_not_erase_a_real_one": highs["CCC"] == 3.0,
        "clean_population_eligible": bool(clean[0]),
        "nonpositive_price_excluded": (not bool(clean[1])) and bool(nonpos[1]),
        "zero_denominator_excluded": (not bool(clean[2])) and bool(nonpos[2]),
        "confirmed_discrepancy_excluded": (not bool(discrep[0])) and bool(excl[0]),
        "discrepancy_excludes_known_names": bool(excl[0]) and bool(excl[1]),
        "extreme_gain_filter_survives": bool(extreme[0]) is False,
        "envelope_is_never_a_rank_filter": ENVELOPE_LO < 1.0 < ENVELOPE_HI,
    }
    return _ok("provider_day_max_and_prevclose_rank_filter", all(results.values()), results)


def t_admission_union_scope_gate() -> dict:
    """A blocker key in the admission union proves NOTHING; only its own scope state does.

    The union always carries both blocker ids, so a B1 gate must refuse when the `feb2025`
    scope is unverified, `not_ready` or `stale` even though the B1 key is present - and must
    accept it when that scope alone is `ready`, with `all_scopes_ready` and no missing or stale
    scope. Uses the producer's REAL scope strings and union shape.
    """
    import tempfile

    def gate(doc: dict | None, blocker: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            if doc is not None:
                (root / "evidence").mkdir()
                (root / "evidence/acquisition_admission.json").write_text(json.dumps(doc))
            with _acq_root(root):
                return acquisition_admission(blocker)

    feb = acquisition_required_days("B1_raw_2025_02")
    apr = acquisition_required_days("B2_raw_roster_2026_04_05")
    both = {
        "B1_raw_2025_02": {"admitted_days": feb, "residual_confirmed_trading_gaps": []},
        "B2_raw_roster_2026_04_05": {"admitted_days": apr, "residual_confirmed_trading_gaps": []},
    }
    full_scopes = {
        "feb2025": {"state": "ready", "is_full_scope": True, "rehash": {"mismatches": []}},
        "aprmay2026": {"state": "ready", "is_full_scope": True, "rehash": {"mismatches": []}},
    }
    results: dict[str, Any] = {}
    for state, want_missing in (("unverified", True), ("not_ready", False), ("stale", False)):
        doc = {
            "schema": "atlas.acquisition.admission-union.v0",
            "all_scopes_ready": state != "unverified",
            "missing_scopes": ["feb2025"] if want_missing else [],
            "stale_scopes": ["feb2025"] if state == "stale" else [],
            "scopes": {"feb2025": {"state": state}, "aprmay2026": {"state": "ready"}},
            "blockers": both,
        }
        got = gate(doc, "B1_raw_2025_02")
        # the union really was on disk and really did carry the B1 key, so a refusal here is
        # the scope state refusing - not a missing file passing the control vacuously
        results[f"{state}_union_was_read"] = got["present"]
        results[f"{state}_scope_refused"] = not got["ready"]
        results[f"{state}_admits_no_day"] = got["admitted_days"] == []
    ready = gate(
        {
            "all_scopes_ready": True,
            "missing_scopes": [],
            "stale_scopes": [],
            "scopes": full_scopes,
            "blockers": both,
        },
        "B1_raw_2025_02",
    )
    results["ready_full_scope_admits_its_own_days"] = ready["ready"] and (
        ready["admitted_days"] == feb
    )
    residual = gate(
        {
            "all_scopes_ready": True,
            "missing_scopes": [],
            "stale_scopes": [],
            "scopes": full_scopes,
            "blockers": {
                "B2_raw_roster_2026_04_05": {
                    "admitted_days": apr,
                    "residual_confirmed_trading_gaps": ["2026-04-01|ZZZZ"],
                }
            },
        },
        "B2_raw_roster_2026_04_05",
    )
    results["residual_gaps_survive_a_ready_scope"] = residual["residual_gaps"] == [
        "2026-04-01|ZZZZ"
    ]
    results["absent_union_admits_nothing"] = not gate(None, "B1_raw_2025_02")["present"]
    results["scope_vocabulary_matches_the_manifest"] = set(ACQ_ADMISSION_SCOPE.values()) == set(
        ACQ_SCOPE_MODE
    )
    return _ok("admission_union_scope_state_gates_the_blocker", all(results.values()), results)


def t_acquisition_day_set_coverage() -> dict:
    """`all_scopes_ready` plus a NONEMPTY list is not coverage: the exact day set is enforced.

    The required set is recomputed from the guarded dev calendar (every 2025-02 day for B1, every
    2026-04/05 day for B2), so the real one-day-per-scope canary union - both scopes `ready`, both
    lists non-empty, `all_scopes_ready` true - still FAILS, while a union carrying the complete
    calendar-derived set satisfies the coverage test.
    """
    import tempfile

    feb = acquisition_required_days("B1_raw_2025_02")
    apr = acquisition_required_days("B2_raw_roster_2026_04_05")

    def union_with(b1: list[str], b2: list[str], **scope_overrides) -> dict:
        scopes = {
            "feb2025": {"state": "ready", "is_full_scope": True, "rehash": {"mismatches": []}},
            "aprmay2026": {"state": "ready", "is_full_scope": True, "rehash": {"mismatches": []}},
        }
        for name, patch in scope_overrides.items():
            scopes[name].update(patch)
        return {
            "schema": "atlas.acquisition.admission-union.v0",
            "all_scopes_ready": True,
            "missing_scopes": [],
            "stale_scopes": [],
            "scopes": scopes,
            "blockers": {
                "B1_raw_2025_02": {"admitted_days": b1},
                "B2_raw_roster_2026_04_05": {"admitted_days": b2},
            },
        }

    def coverage(blocker: str, doc: dict) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "evidence").mkdir()
            (root / "evidence/acquisition_admission.json").write_text(json.dumps(doc))
            with _acq_root(root):
                adm = acquisition_admission(blocker)
                required = set(acquisition_required_days(blocker))
                return {
                    "ready": adm["ready"],
                    "uncovered": sorted(required - set(adm["admitted_days"])),
                    "admitted_n": len(adm["admitted_days"]),
                }

    results: dict[str, Any] = {
        "b1_required_is_the_feb_calendar": len(feb) > 1
        and all(d.startswith("2025-02") for d in feb),
        "b2_required_is_the_aprmay_calendar": len(apr) > 1
        and all(d[:7] in ("2026-04", "2026-05") for d in apr),
    }
    one_each = union_with(feb[:1], apr[:1])
    for blocker in ("B1_raw_2025_02", "B2_raw_roster_2026_04_05"):
        sub = coverage(blocker, one_each)
        results[f"{blocker}_one_day_subset_has_uncovered_days"] = bool(sub["uncovered"])
        results[f"{blocker}_one_day_subset_not_ready"] = not sub["ready"]
    # a `verified_subset` scope passed its own day checks but is partial: it must admit nothing
    subset = union_with(
        feb[:1],
        apr,
        feb2025={
            "state": "verified_subset",
            "is_full_scope": False,
            "reason": f"only 1 of {len(feb)} required days were verified",
        },
    )
    got = coverage("B1_raw_2025_02", subset)
    results["verified_subset_is_not_ready"] = not got["ready"]
    results["verified_subset_admits_no_day"] = coverage("B1_raw_2025_02", subset)["admitted_n"] == 0
    # a `ready` scope that is not a full-scope attestation must still refuse
    notfull = union_with(feb, apr, feb2025={"is_full_scope": False})
    results["ready_but_not_full_scope_refused"] = not coverage("B1_raw_2025_02", notfull)["ready"]
    # a rehash mismatch must refuse even at full coverage
    mismatch = union_with(feb, apr, feb2025={"rehash": {"mismatches": ["2025-02-03"]}})
    results["rehash_mismatch_refused"] = not coverage("B1_raw_2025_02", mismatch)["ready"]
    full = union_with(feb, apr)
    for blocker in ("B1_raw_2025_02", "B2_raw_roster_2026_04_05"):
        ok = coverage(blocker, full)
        results[f"{blocker}_full_day_set_covers"] = not ok["uncovered"]
        results[f"{blocker}_full_day_set_is_ready"] = ok["ready"]
    return _ok("acquisition_day_set_is_exactly_enforced", all(results.values()), results)


def t_prev_close_rules() -> dict:
    """The 2021-02-01 seed rule and the 2025-02-03 sealed-prior block rule."""
    p1, k1 = prior_stored_session("2021-02-01")
    p2, k2 = prior_stored_session("2025-02-03")
    p3, k3 = prior_stored_session("2022-03-10")
    m1, prov1 = prev_close_map("2021-02-01")
    m2, prov2 = prev_close_map("2025-02-03")
    detail = {
        "2021-02-01": [p1, k1, prov1["prev_close_source"], len(m1)],
        "2025-02-03": [
            p2,
            k2,
            prov2["prev_close_source"],
            len(m2),
            prov2["prev_close_stale"],
            prov2["prev_close_sealed_prior"],
            prov2["prev_close_gap_days"],
        ],
        "2022-03-10": [p3, k3],
    }
    ok = (
        k1 == "seed_2021-01-29"
        and len(m1) > 0
        and k2 == "block_gap_last_stored"
        and prov2["prev_close_stale"]
        and prov2["prev_close_sealed_prior"]
        and len(m2) > 0
        and k3 == "raw_prior_session"
    )
    return _ok("prev_close_seed_and_block_gap_rules", ok, detail)


def t_acquisition_admission_rules() -> dict:
    """An admitted acquisition day replaces or supplements; a bad one is simply not admitted.

    Synthetic manifests and bars in a throwaway root, exercising the real validator: scope
    decides replace-vs-supplement, a status other than complete is not admitted, a sha/feed/
    adjustment/day/scope violation REFUSES loudly rather than degrading silently, an unlisted
    spelling is never renamed, and a canonical name arriving from two lanes is a collision.
    """
    import tempfile

    results: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bars_dir = root / "bars"
        bars_dir.mkdir()

        def write_day(dname: str, tickers: list[str], **overrides) -> dict:
            start = datetime(2026, 4, 1, 14, 30, tzinfo=UTC)
            df = pl.DataFrame(
                {
                    "timestamp": [start + timedelta(minutes=i) for i in range(len(tickers))],
                    "ticker": tickers,
                    "provider_symbol": [f"P{i}" for i in range(len(tickers))],
                    "open": [1.0] * len(tickers),
                    "high": [2.0] * len(tickers),
                    "low": [0.5] * len(tickers),
                    "close": [1.5] * len(tickers),
                    "volume": [100.0] * len(tickers),
                }
            )
            df = df.with_columns(pl.col("timestamp").cast(pl.Datetime("ns", "UTC")))
            p = bars_dir / f"{dname}.parquet"
            df.write_parquet(p)
            man = {
                "day": dname,
                "scope": "aprmay2026",
                "status": "complete",
                "file": p.name,
                "sha256": sha256_file(p),
                "source": {"feed": "sip", "adjustment": "raw"},
                "errors": [],
            }
            man.update(overrides)
            (bars_dir / f"{dname}.manifest.json").write_text(json.dumps(man))
            _write_day_binding(root, dname, scope="aprmay2026")
            return man

        def admits(day: str) -> tuple[bool, str]:
            with _acq_root(root):
                try:
                    got = _validate_acquisition_day(day)
                except ValueError as exc:
                    return False, f"refused: {exc}"
            return (got is not None), (got or {}).get("mode", "not admitted")

        # controls: a well-formed B2 supplement is admitted; each violation is refused
        write_day("2026-04-01", ["AAA"])
        ok_admitted, ok_mode = admits("2026-04-01")
        results["complete_manifest_admitted"] = ok_admitted
        results["b2_scope_supplements"] = ok_mode == ACQ_MODE_SUPPLEMENT
        write_day("2025-02-03", ["AAA"], scope="feb2025")
        results["feb2025_scope_replaces"] = admits("2025-02-03")[1] == ACQ_MODE_REPLACE
        write_day("2026-04-03", ["AAA"], status="partial")
        results["incomplete_manifest_not_admitted"] = admits("2026-04-03")[0] is False
        write_day("2026-04-04", ["AAA"], sha256="0" * 64)
        results["sha_mismatch_refuses"] = admits("2026-04-04")[0] is False
        write_day("2026-04-05", ["AAA"], source={"feed": "iex", "adjustment": "raw"})
        results["non_sip_feed_refuses"] = admits("2026-04-05")[0] is False
        write_day("2026-04-06", ["AAA"], source={"feed": "sip", "adjustment": "split"})
        results["adjusted_feed_refuses"] = admits("2026-04-06")[0] is False
        write_day("2026-04-07", ["AAA"], day="2026-04-99")
        results["day_mismatch_refuses"] = admits("2026-04-07")[0] is False
        write_day("2026-04-08", ["AAA"], scope="B2")
        results["blocker_id_is_not_a_scope_refuses"] = admits("2026-04-08")[0] is False
        write_day("2026-04-13", ["AAA"], scope="sep2026")
        results["unknown_scope_refuses"] = admits("2026-04-13")[0] is False
        write_day("2026-04-09", ["AAA"], errors=["api 429"])
        results["errors_refuse"] = admits("2026-04-09")[0] is False
        results["absent_day_not_admitted"] = admits("2026-04-10")[0] is False
        # the alias map is the ONLY rename path, and a two-lane collision is refused
        alias = root / "alias_map.json"
        alias.write_text(json.dumps({"aliases": {"OLDNAME": "NEWNAME"}}))
        _ACQ_CACHE.clear()

        def lane_frame(day: str, tickers: list[str], role: str) -> pl.DataFrame:
            start = datetime(2026, 4, int(day[-2:]), 14, 30, tzinfo=UTC)
            return pl.DataFrame(
                {
                    "dt": [day] * len(tickers),
                    "timestamp": [start] * len(tickers),
                    "ticker": tickers,
                    "open": [1.0] * len(tickers),
                    "high": [2.0] * len(tickers),
                    "low": [0.5] * len(tickers),
                    "close": [1.5] * len(tickers),
                    "volume": [100.0] * len(tickers),
                    "_lane_role": [role] * len(tickers),
                }
            )

        # a baseline lane already carrying the canonical name plus a day file carrying the same
        # canonical name (directly, or via the alias spelling) is a collision and refuses the day
        try:
            _refuse_lane_collision(
                lane_frame("2026-04-11", ["NEWNAME"], "baseline"),
                lane_frame("2026-04-11", ["NEWNAME"], "acquired_day"),
                day="2026-04-11",
            )
            results["two_lane_collision_refused"] = False
        except ValueError:
            results["two_lane_collision_refused"] = True
        disjoint = _refuse_lane_collision(
            lane_frame("2026-04-11", ["AAA"], "baseline"),
            lane_frame("2026-04-11", ["NEWNAME"], "acquired_day"),
            day="2026-04-11",
        )
        results["disjoint_lanes_union"] = disjoint.height == 2
        # the alias map is read as its inverse, and a non-injective map refuses
        canonical_keyed = root / "canonical_alias.json"
        canonical_keyed.write_text(
            json.dumps({"aliases": {"BRK/A": {"provider_symbol": "BRK.A", "basis": "x"}}})
        )
        with _alias_map(canonical_keyed):
            results["inverse_of_canonical_keyed_map"] = acquisition_alias_map() == {
                "BRK.A": "BRK/A"
            }
        ambiguous = root / "ambiguous_alias.json"
        ambiguous.write_text(
            json.dumps(
                {
                    "aliases": {
                        "AAA": {"provider_symbol": "SHARED"},
                        "BBB": {"provider_symbol": "SHARED"},
                    }
                }
            )
        )
        with _alias_map(ambiguous):
            try:
                acquisition_alias_map()
                results["non_injective_alias_refuses"] = False
            except ValueError:
                results["non_injective_alias_refuses"] = True
        chained = root / "chained_alias.json"
        chained.write_text(json.dumps({"aliases": {"AAA": "BBB", "BBB": "CCC"}}))
        with _alias_map(chained):
            try:
                acquisition_alias_map()
                results["chained_alias_refuses"] = False
            except ValueError:
                results["chained_alias_refuses"] = True
        _ACQ_CACHE.clear()
    return _ok("acquisition_admission_replaces_or_supplements", all(results.values()), results)


# ---- data-dependent checks ------------------------------------------------- #


def t_payload_shas(out_data: Path, manifest: dict) -> dict:
    payloads = manifest.get("payload_sha256") or manifest.get("payloads") or {}
    missing, mismatch = [], []
    for key, info in payloads.items():
        if not info.get("path"):
            continue
        p = out_data / info["path"]
        if not p.exists():
            missing.append(key)
            continue
        if sha256_file(p) != info.get("sha256"):
            mismatch.append(key)
    return _ok(
        "manifest_payload_shas_verify",
        not missing and not mismatch,
        {"missing": missing, "mismatch": mismatch, "n_payloads": len(payloads)},
    )


def t_no_forbidden_columns(out_data: Path) -> dict:
    bad = {}
    for p in _all_parquet(out_data):
        names = pl.scan_parquet(p).collect_schema().names()
        hits = [n for n in names if n in FORBIDDEN_EXACT or n.startswith(FORBIDDEN_PREFIXES)]
        allowed = (
            {"terminal_censored"}
            if (p.name == "paths.parquet" or "grid.selected_paths" in str(p))
            else set()
        )
        # terminal_censored is declared retro-only in paths/grid; anything else is a failure
        hits = [h for h in hits if h not in allowed]
        if hits:
            bad[str(p.relative_to(out_data))] = hits
    # coverage_class / terminal_censored are the only censor-family names allowed
    return _ok(
        "physical_absence_of_blocked_families",
        not bad,
        bad or {"files_scanned": sum(1 for _ in _all_parquet(out_data))},
    )


def t_day_registry(out_data: Path) -> dict:
    reg = pl.read_parquet(out_data / "day_registry.parquet")
    dev = dev_days()
    cal, _ = session_calendar()
    sealed = [
        d
        for d in reg["day"].to_list()
        if d.startswith(("2024-", "2025-01")) or d[:7] in ("2026-06", "2026-07", "2026-08")
    ]
    se_bad = [
        r["day"] for r in reg.iter_rows(named=True) if int(r["session_end"]) != int(cal[r["day"]])
    ]
    detail = {
        "rows": reg.height,
        "dev_days": len(dev),
        "sealed_or_reserved": sealed,
        "session_end_mismatch": se_bad,
        "canary_members": int(reg["canary_member"].sum()),
        "sorted": reg["day"].to_list() == sorted(reg["day"].to_list()),
    }
    ok = (
        reg.height == 1066
        and reg["day"].n_unique() == 1066
        and not sealed
        and not se_bad
        and reg["day"].to_list() == dev
        and detail["canary_members"] == 20
    )
    return _ok("day_registry_1066_guarded", ok, detail)


def t_identity_census(out_data: Path, panel: pl.DataFrame, days: list[str]) -> dict:
    paths = pl.read_parquet(out_data / "paths.parquet")
    mem = pl.read_parquet(out_data / "memberships.parquet")
    sub = panel.filter(pl.col("sleeve_day").is_in(days))
    sub_keys = sub.select(["sleeve_day", "family", "ticker", "entry_et", "entry_rank"]).unique()
    sub_paths = sub.select(["sleeve_day", "ticker"]).unique()
    panel.select(["sleeve_day", "ticker"]).unique()
    full_keys = panel.select(["sleeve_day", "family", "ticker", "entry_et", "entry_rank"]).unique()
    shared = (
        full_keys.group_by(["sleeve_day", "ticker"])
        .agg(pl.col("family").n_unique().alias("nf"))
        .filter(pl.col("nf") > 1)
        .height
    )
    mem_ids = set(mem["member_id"].to_list())
    keys = {
        f"{r['sleeve_day']}|{r['family']}|{r['ticker']}|{r['entry_et']}|{r['entry_rank']}"
        for r in sub_keys.iter_rows(named=True)
    }
    shared_built = int(paths.filter(pl.col("n_memberships") > 1).height)
    detail = {
        "paths": paths.height,
        "expected_paths": sub_paths.height,
        "memberships": mem.height,
        "expected_memberships": sub_keys.height,
        "full_panel_shared_paths": shared,
        "shared_paths_built": shared_built,
        "membership_ids_match": mem_ids == keys,
        "path_ids_unique": paths["path_id"].n_unique() == paths.height,
        "n_memberships_sum": int(paths["n_memberships"].sum()),
        "collapsed": paths.height <= mem.height and paths.height == sub_paths.height,
    }
    ok = (
        paths.height == sub_paths.height
        and mem.height == sub_keys.height
        and shared == 255
        and detail["membership_ids_match"]
        and detail["path_ids_unique"]
        and detail["n_memberships_sum"] == mem.height
        and detail["collapsed"]
        and (shared_built == 0 or paths.height < mem.height)
    )
    return _ok("identity_census_and_255_shared_path_rule", ok, detail)


def t_pointer_spotcheck(out_data: Path, days: list[str], n_days: int = 2, n_rows: int = 4) -> dict:
    sample_days = [days[0], days[len(days) // 2]][:n_days]
    problems, checked = [], 0
    for day in sample_days:
        p = out_data / "prints.selected_paths" / f"month={day[:7]}" / f"{day}.parquet"
        if not p.exists():
            problems.append(f"{day}: no projection file")
            continue
        df = pl.read_parquet(p)
        if df.height == 0:
            continue
        pick = df.sort(["ticker", "source_row_ordinal"]).gather(
            [int(i) for i in np.linspace(0, df.height - 1, num=n_rows)]
        )
        raw = pl.read_parquet(NET_TRADES / f"{day}.parquet").with_row_index("ord")
        raw = raw.with_columns(pl.col("ts_utc").dt.cast_time_unit("us"))
        j = pick.join(raw, left_on="source_row_ordinal", right_on="ord", how="left", suffix="_src")
        for r in j.iter_rows(named=True):
            same = (
                r["symbol"] == r["ticker"]
                and r["price"] == r["price_src"]
                and r["size"] == r["size_src"]
                and r["trade_id"] == r["trade_id_src"]
                and r["exchange"] == r["exchange_src"]
                and r["tape"] == r["tape_src"]
                and list(r["conditions"]) == list(r["conditions_src"])
                and r["ts_utc"] == r["ts_utc_src"]
            )
            checked += 1
            if not same:
                problems.append({"day": day, "ordinal": r["source_row_ordinal"]})
    return _ok(
        "source_row_pointer_spotcheck",
        not problems,
        {"days": sample_days, "rows_checked": checked, "problems": problems},
    )


def t_minute_states(out_data: Path, days: list[str]) -> dict:
    """Accounting: bar states, print states, no fabricated prices, span boundaries."""
    se_map, _ = session_calendar()
    bad = []
    totals = {"rows": 0}
    for day in days[:3] + days[-2:]:
        gp = out_data / "grid.selected_paths" / f"month={day[:7]}" / f"{day}.parquet"
        if not gp.exists():
            bad.append(f"{day}: grid missing")
            continue
        g = pl.read_parquet(gp)
        seg = int(se_map[day])
        totals["rows"] += g.height
        # no fabricated prices
        bad += [
            f"{day}: bar_state none with close"
            for _ in range(
                g.filter((pl.col("bar_state") == "none") & pl.col("bar_close").is_not_null()).height
            )
        ]
        bad += [
            f"{day}: print_state inconsistent"
            for _ in range(
                g.filter(
                    ((pl.col("print_state") == "no_print") & (pl.col("n_prints") != 0))
                    | (
                        (pl.col("print_state") == "path_print")
                        & (pl.col("n_bar_eligible_prints") <= 0)
                    )
                    | (
                        (pl.col("print_state") == "excluded_prints_only")
                        & ((pl.col("n_prints") <= 0) | (pl.col("n_bar_eligible_prints") != 0))
                    )
                ).height
            )
        ]
        bad += [
            f"{day}: span flag"
            for _ in range(
                g.filter(
                    pl.col("within_observed_span")
                    != ((pl.col("et") >= 570) & (pl.col("et") <= seg))
                ).height
            )
        ]
        bad += [f"{day}: session_end" for _ in range(g.filter(pl.col("session_end") != seg).height)]
        # et coverage completeness
        if g["et"].min() != 565 or g["et"].max() != 965:
            bad.append(f"{day}: grid span {g['et'].min()}..{g['et'].max()}")
        # cross-check grid print counts against the stored projection
        pr = pl.read_parquet(
            out_data / "prints.selected_paths" / f"month={day[:7]}" / f"{day}.parquet"
        )
        if pr.height:
            agg = pr.group_by(["ticker", "et_min"]).len()
            j = g.select(["ticker", "et", "n_prints"]).join(
                agg.rename({"et_min": "et", "len": "n_src"}), on=["ticker", "et"], how="left"
            )
            n_bad = j.filter(pl.col("n_src").fill_null(0) != pl.col("n_prints")).height
            if n_bad:
                bad.append(f"{day}: {n_bad} grid minutes disagree with the print projection")
    return _ok(
        "minute_state_accounting", not bad, {"problems": bad[:10], "rows_checked": totals["rows"]}
    )


def t_rank_recompute(out_data: Path, days: list[str], sample_minutes=(580, 620, 700)) -> dict:
    """Independent rank recomputation with polars rank('min') on sampled (day,t)."""
    problems, checked = [], 0
    sample_days = [days[1], days[-1]]
    for day in sample_days:
        p = out_data / "race.minute_full" / f"month={day[:7]}" / f"{day}.parquet"
        if not p.exists():
            problems.append(f"{day}: no board")
            continue
        board = pl.read_parquet(p)
        se = int(board["session_end"][0])
        prev_map, _prov = prev_close_map(day)
        raw = read_raw_day(day, se)
        for t in sample_minutes:
            if t > se:
                continue
            sub = raw.filter(pl.col("etm") <= t - 1)
            last = (
                sub.sort(["ticker", "etm"])
                .group_by("ticker")
                .agg(pl.col("close").last().alias("px"), pl.col("etm").last().alias("px_et"))
            )
            names = set(board.filter(pl.col("t") == t)["ticker"].to_list())
            last = last.filter(pl.col("ticker").is_in(list(names)))
            last = last.with_columns(
                pl.lit(t, dtype=pl.Int32).alias("t"),
                pl.col("ticker")
                .replace_strict(prev_map, default=None, return_dtype=pl.Float64)
                .alias("prev_close"),
            )
            # the board publishes the prior-session provider ratio for this name; use it as input
            ratio_by_name = {
                r["ticker"]: r["prevclose_vs_sip_clast_ratio"]
                for r in board.filter(pl.col("t") == t)
                .select(["ticker", "prevclose_vs_sip_clast_ratio"])
                .iter_rows(named=True)
            }
            last = last.with_columns(
                pl.col("ticker")
                .replace_strict(ratio_by_name, default=None, return_dtype=pl.Float64)
                .alias("prevclose_vs_sip_clast_ratio")
            )
            last = last.with_columns((pl.col("px") / pl.col("prev_close") - 1).alias("gain"))
            chk_all = last.filter(
                pl.col("gain").is_finite() & (pl.col("px") > 0) & (pl.col("prev_close") > 0)
            )
            # The rank population is the PRODUCTION rule, not a gain-only band. The board also
            # refuses names whose previous close disagrees with the provider's prior-session close
            # by more than the declared hard warning - a legitimate quality guard that this
            # checker's gain-only filter ignored, admitting 15 extra names on an hf-era block-gap
            # day and shifting every rank below them by +1. The discrepancy is recomputed here from
            # the board's PUBLISHED provenance ratio, not copied from the flag column.
            last = last.filter(
                pl.col("gain").is_finite()
                & (pl.col("px") > 0)
                & (pl.col("prev_close") > 0)
                & (pl.col("gain") <= SUSPECT_GAIN_HI)
                & (pl.col("gain") >= SUSPECT_GAIN_LO)
                & (
                    pl.col("prevclose_vs_sip_clast_ratio").is_null()
                    | ((pl.col("prevclose_vs_sip_clast_ratio") - 1).abs() <= PREVCLOSE_HARD_WARN)
                )
            )
            ref = last.with_columns(
                pl.col("gain").rank("min", descending=True).cast(pl.Int32).alias("rank_known_ref"),
                (pl.col("t") - pl.col("px_et")).alias("age_min"),
            )
            got = board.filter(pl.col("t") == t).select(
                [
                    "ticker",
                    "rank_known",
                    "rank_eligible",
                    "age_min",
                    "gain",
                    "n_known",
                    "rank_unfiltered",
                ]
            )
            j = got.join(
                ref.select(["ticker", "rank_known_ref", "gain", "age_min"]),
                on="ticker",
                how="left",
                suffix="_ref",
            )
            checked += j.height
            mism = j.filter(pl.col("rank_eligible")).filter(
                (pl.col("rank_known") != pl.col("rank_known_ref"))
                | ((pl.col("gain") - pl.col("gain_ref")).abs() > 1e-12)
                | (pl.col("age_min") != pl.col("age_min_ref"))
            )
            if mism.height:
                problems.append({"day": day, "t": t, "n_mismatch": mism.height})
            # Complementary assertion for the names the quality guard EXCLUDES: their
            # rank_known must be null while rank_unfiltered stays populated and consistent with
            # the recomputed unfiltered order. Without this, a regression that silently DROPS the
            # denominator guard would still pass the filtered comparison above.
            unfiltered_ref = chk_all.with_columns(
                pl.col("gain").rank("min", descending=True).cast(pl.Int32).alias("rank_ref_uf")
            )
            excl = got.join(
                unfiltered_ref.select(["ticker", "rank_ref_uf"]), on="ticker", how="left"
            )
            bad_excl = excl.filter(
                (~pl.col("rank_eligible"))
                & pl.col("rank_known").is_not_null()
                & (pl.col("rank_unfiltered").is_not_null())
                & (pl.col("rank_unfiltered") != pl.col("rank_ref_uf"))
            )
            if bad_excl.height:
                problems.append(
                    {"day": day, "t": t, "excluded_unfiltered_mismatch": bad_excl.height}
                )
            n_ok = (
                j.filter(pl.col("rank_eligible")).filter(pl.col("rank_known").is_not_null()).height
            )
            if int(j["n_known"].max() or 0) != n_ok and t != sample_minutes[0]:
                problems.append(
                    {
                        "day": day,
                        "t": t,
                        "n_known": int(j["n_known"].max() or 0),
                        "eligible_recomputed": n_ok,
                    }
                )
    return _ok(
        "broad_rank_recomputation_sampled",
        not problems,
        {"days": sample_days, "rows_checked": checked, "problems": problems},
    )


def t_rank_null_rule(out_data: Path, days: list[str]) -> dict:
    bad = []
    for day in days[:4]:
        bp = out_data / "race.minute_full" / f"month={day[:7]}" / f"{day}.parquet"
        if not bp.exists():
            continue
        b = pl.read_parquet(bp)
        bad += [
            f"{day}: known=false with px/gain/rank"
            for _ in range(
                b.filter(
                    (~pl.col("known_by_t"))
                    & (
                        pl.col("px").is_not_null()
                        | pl.col("gain").is_not_null()
                        | pl.col("rank_known").is_not_null()
                    )
                ).height
            )
        ]
        bad += [
            f"{day}: fresh with age>2"
            for _ in range(
                b.filter(
                    pl.col("fresh_2m") & ((pl.col("age_min") > 2) | pl.col("age_min").is_null())
                ).height
            )
        ]
        bad += [
            f"{day}: ineligible with rank"
            for _ in range(
                b.filter((~pl.col("rank_eligible")) & pl.col("rank_known").is_not_null()).height
            )
        ]
    return _ok("known_false_implies_null_and_fresh_rule", not bad, {"problems": bad[:5]})


def t_prev_close_flags(out_data: Path, days: list[str]) -> dict:
    """Previous-close provenance per day, including which lane actually supplied the denominator.

    The floor flag is asserted against the MEASURED lane plan, not the month: a 2025-02 day
    whose clean fallback was replaced by an admitted acquisition day is no longer floor-qualified,
    and a day whose previous session came from an admitted day must say so. 2025-02-03 keeps its
    declared block-gap provenance - there is no admitted prior session and no sealed look-back.
    """
    detail = {}
    problems = []
    for day in days:
        p = out_data / "race.minute_full" / f"month={day[:7]}" / f"{day}.parquet"
        if not p.exists():
            continue
        b = (
            pl.scan_parquet(p)
            .select(
                [
                    "prev_close_source",
                    "prev_close_stale",
                    "prev_close_sealed_prior",
                    "qualified_floor_source",
                    "prev_close_floor_qualified",
                    "prev_close_lane_kind",
                    "prev_close_acquired",
                    "acquisition_used",
                ]
            )
            .unique()
            .collect()
        )
        detail[day] = b.to_dicts()
        if day == "2021-02-01" and b["prev_close_source"][0] != "seed_2021-01-29":
            problems.append(day)
        if day == "2025-02-03" and not (
            b["prev_close_source"][0] == "block_gap_last_stored"
            and bool(b["prev_close_stale"][0])
            and bool(b["prev_close_sealed_prior"][0])
            and not bool(b["prev_close_acquired"][0])
        ):
            problems.append(f"{day}: block-gap provenance changed")
        if day.startswith("2025-02"):
            want_floor = day_lane_plan(day)["floor_qualified"]
            if bool(b["qualified_floor_source"][0]) != want_floor:
                problems.append(
                    f"{day}: floor flag {b['qualified_floor_source'][0]} != {want_floor}"
                )
            if bool(b["acquisition_used"][0]) != (day_lane_plan(day)["acquisition"] is not None):
                problems.append(f"{day}: acquisition_used disagrees with the lane plan")
        # the denominator's lane must be the one the plan names for the prior session
        prev_day, _kind, prov = prev_close_provenance(day)
        if (
            prov["prev_close_source"] != "none"
            and b["prev_close_lane_kind"][0] != prov["prev_close_lane_kind"]
        ):
            problems.append(f"{day}: prev_close_lane_kind disagrees with the resolved plan")
    return _ok(
        "prev_close_provenance_and_floor_flags",
        not problems,
        {"problems": problems, "per_day": detail},
    )


def unadmitted_acquisition_days() -> set[str]:
    """Dev days whose B1/B2 acquisition has NOT replaced/supplemented the baseline lane.

    Ground truth for the coverage assertions, derived from the joint evidence and the day plans
    rather than from the coverage table. With the acquisition admitted there is no floored
    fallback and no unresolved PIT hole, so asserting those branch rows are present would assert
    the PRE-REPAIR state; asserting they are ABSENT would assert the repaired state. Both are
    wrong as a permanent check - the check must follow the data and still be able to fail.
    """
    out: set[str] = set()
    for day in dev_days():
        if not day.startswith(("2025-02", "2026-04", "2026-05")):
            continue
        if day_lane_plan(day)["acquisition"] is None:
            out.add(day)
    return out


def t_blockers_visible(out_data: Path) -> dict:
    """The core acquisition blockers stay visible as MEASURED per-day states.

    Every 2026-04/05 built day must publish the split between names the acquisition verified
    absent and names that are still an unresolved missing confirmed trader, and a day whose
    acquisition landed must no longer be counted as a raw roster hole.
    """
    cov = pl.read_parquet(out_data / "coverage.parquet")
    reg = pl.read_parquet(out_data / "day_registry.parquet")
    b2 = cov.filter(pl.col("blocker_id").str.contains("B2_raw_roster_2026_04_05"))
    b1 = cov.filter(pl.col("blocker_id").str.contains("B1_raw_2025_02"))
    flick = reg.filter(pl.col("qualified_floor_source"))
    problems: list[str] = []
    months = ("2026-04", "2026-05")
    for r in reg.iter_rows(named=True):
        if r["day"][:7] not in months or not r["canary_member"]:
            continue
        plan = day_lane_plan(r["day"])
        if plan["acquisition"] is None and not (r["raw_roster_missing_n"] or 0):
            problems.append(f"{r['day']}: no acquisition admitted but no roster hole either")
        # an admitted day may still have residual gaps, but they must be counted, not hidden
        if (
            plan["acquisition"] is not None
            and (r["raw_roster_missing_unresolved_n"] or 0)
            and not (r["raw_roster_missing_n"] or 0) >= (r["raw_roster_missing_unresolved_n"] or 0)
        ):
            problems.append(f"{r['day']}: unresolved exceeds the total PIT hole")
        if bool(r["acq_bars_present"]) != (plan["acquisition"] is not None):
            problems.append(f"{r['day']}: acq_bars_present disagrees with the lane plan")
    detail = {
        "B1_rows": b1.height,
        "B2_rows": b2.height,
        "floor_days": flick["day"].to_list(),
        "roster_holes": {
            r["day"]: r["raw_roster_missing_n"]
            for r in reg.iter_rows(named=True)
            if r["canary_member"] and r["day"][:7] in months
        },
        "roster_unresolved": {
            r["day"]: r["raw_roster_missing_unresolved_n"]
            for r in reg.iter_rows(named=True)
            if r["canary_member"]
            and r["day"][:7] in months
            and r["raw_roster_missing_unresolved_n"] is not None
        },
        "roster_verified_absent": {
            r["day"]: r["raw_roster_verified_absent_n"]
            for r in reg.iter_rows(named=True)
            if r["canary_member"] and r["raw_roster_verified_absent_n"] is not None
        },
        "acquisition_days": sorted(
            r["day"]
            for r in reg.iter_rows(named=True)
            if r["canary_member"] and r["acq_bars_present"]
        ),
        "missingness_classes": sorted(set(cov["missingness_class"].to_list())),
    }
    # The branch rows must FOLLOW the data, not assert one state forever. Ground truth is the
    # set of dev days whose acquisition has not landed: those are exactly the days that must
    # still carry a floor / missing-trader / unreconciled branch, and if that set is empty the
    # branches must be empty. Asserting rows are always present would pin the pre-repair state.
    expected = unadmitted_acquisition_days()
    expected_floor = {d for d in expected if d.startswith("2025-02")}
    b1_days = set(b1["day"].to_list())
    b2_days = set(b2["day"].to_list())
    detail["unadmitted_dev_days"] = len(expected)
    detail["expected_floor_days"] = sorted(expected_floor)
    detail["observed_floor_days"] = sorted(flick["day"].to_list())
    detail["B1_branch_days"] = sorted(b1_days)
    detail["B2_branch_days"] = sorted(b2_days)
    if b1_days != expected_floor:
        problems.append(
            f"B1 floor-fallback branch days {sorted(b1_days)} != expected {sorted(expected_floor)}"
        )
    if set(flick["day"].to_list()) != expected_floor:
        problems.append(
            f"qualified_floor_source days {sorted(flick['day'].to_list())} != expected "
            f"{sorted(expected_floor)}"
        )
    if b2_days != {d for d in expected if d.startswith(("2026-04", "2026-05"))}:
        problems.append(
            f"B2 missing-trader branch days {sorted(b2_days)} != expected "
            f"{sorted(d for d in expected if d.startswith(('2026-04', '2026-05')))}"
        )
    ok = not problems
    return _ok("full_build_blockers_visible", ok, {"problems": problems, **detail})


def t_candidate_roster(out_data: Path, days: list[str]) -> dict:
    problems, detail = [], {}
    for day in [days[0], days[-1]]:
        p = out_data / "race.candidate_net" / f"month={day[:7]}" / f"{day}.parquet"
        if not p.exists():
            continue
        b = pl.read_parquet(p)
        cand = load_json(CANDIDATES / f"{day}.json")
        exp_first = {}
        for snap in cand["snapshots"]:
            sel = int(snap["T"]) + (1 if snap["pop"] == "A_open" else 0)
            for s in [r["symbol"] for r in snap.get("top", [])] + list(snap.get("margin", [])):
                exp_first[s] = min(exp_first.get(s, sel), sel)
        got = (
            b.group_by("ticker")
            .agg(pl.col("roster_first_selectable_et").first())
            .filter(pl.col("roster_first_selectable_et").is_not_null())
        )
        got_map = {r["ticker"]: r["roster_first_selectable_et"] for r in got.iter_rows(named=True)}
        winners = {w["symbol"] for w in cand.get("winners_open", [])} | {
            w["symbol"] for w in cand.get("winners_prev", [])
        }
        winner_only = winners - set(exp_first)
        detail[day] = {
            "expected_roster_names": len(exp_first),
            "board_roster_names": len(got_map),
            "mismatch": sorted(
                k for k in set(exp_first) | set(got_map) if exp_first.get(k) != got_map.get(k)
            )[:10],
            "winner_only_names": len(winner_only),
        }
        if detail[day]["mismatch"]:
            problems.append(day)
        # roster membership must equal first_selectable <= t
        chk = b.filter(
            pl.col("in_prospective_roster")
            != (
                pl.col("roster_first_selectable_et").is_not_null()
                & (pl.col("roster_first_selectable_et") <= pl.col("t"))
            )
        )
        if chk.height:
            problems.append(f"{day}: membership flag mismatch")
    return _ok("candidate_prospective_roster_causal", not problems, detail)


def t_quote_roster(out_data: Path, days: list[str]) -> dict:
    problems, detail = [], {}
    for day in [days[0], days[-1]]:
        p = out_data / "quote_rosters" / f"{day}.parquet"
        if not p.exists():
            problems.append(f"{day}: missing")
            continue
        q = pl.read_parquet(p)
        cand = load_json(CANDIDATES / f"{day}.json")
        exp = []
        for snap in cand["snapshots"]:
            k = B_TOP_K if snap["pop"] == "B" else A_TOP_K
            for r in snap.get("top", [])[:k]:
                exp.append((snap["pop"], int(snap["T"]), int(r["rank"]), r["symbol"]))
        got = [
            (r["snapshot_pop"], int(r["snapshot_T"]), int(r["rank"]), r["symbol"])
            for r in q.iter_rows(named=True)
        ]
        detail[day] = {
            "expected_rows": len(exp),
            "stored_rows": len(got),
            "match": sorted(exp) == sorted(got),
            "unexpected_selectable": q.filter(
                (pl.col("snapshot_pop") == "A_open")
                & (pl.col("selectable_asof_et") != pl.col("snapshot_T") + 1)
            ).height,
        }
        if sorted(exp) != sorted(got):
            problems.append(day)
        if detail[day]["unexpected_selectable"]:
            problems.append(f"{day}: A_open selectable")
    return _ok("quote_roster_rule_reproduced", not problems, detail)


def t_parent_shas(out_data: Path, manifest: dict, days: list[str]) -> dict:
    """Parent shas verified against the current files; a corrupted scratch manifest must fail."""

    def check(candidate: dict) -> list[str]:
        problems: list[str] = []
        parents = candidate.get("parents") or {}
        base = {"panel.parquet": PANEL, "phase2_session_calendar.json": CAL_PATH}
        for name, path in base.items():
            rec = parents.get(name) or {}
            if not path.exists():
                problems.append(f"{name}: missing")
                continue
            if rec.get("sha256") != sha256_file(path):
                problems.append(f"{name}: recorded parent sha mismatch")
            if not rec.get("source_uri"):
                problems.append(f"{name}: source_uri missing")
        shas = candidate.get("parent_shas") or {}
        for name, path in base.items():
            if shas.get(name) != sha256_file(path):
                problems.append(f"{name}: parent_shas mismatch")
        return problems

    real = check(manifest)
    scratch = json.loads(json.dumps(manifest))
    scratch.setdefault("parents", {}).setdefault("panel.parquet", {})["sha256"] = "0" * 64
    scratch.setdefault("parent_shas", {})["phase2_session_calendar.json"] = "1" * 64
    control = check(scratch)
    registry = pl.read_parquet(out_data / "day_registry.parquet")
    raw_checked = 0
    raw_problems = []
    for r in registry.iter_rows(named=True):
        if not r.get("built") or r["day"] not in set(days):
            continue
        # compare the recorded value against the SAME quantity it records: the combined
        # LANE-SET digest, not a bare file sha of whichever file raw_source_path names. The two
        # differ by construction on a multi-lane (B2 supplement) day, so a bare-file comparison
        # would fail every such day and pass only on legacy single-lane rows.
        try:
            want = combined_lane_sha(day_lane_plan(r["day"]))
        except (FileNotFoundError, ValueError) as exc:
            raw_problems.append(f"{r['day']}: {exc}")
            continue
        raw_checked += 1
        if r["raw_source_sha256"] != want:
            raw_problems.append(
                f"{r['day']}: recorded {r['raw_source_sha256']} != recomputed lane digest {want}"
            )
    ok = not real and len(control) >= 2 and not raw_problems
    return _ok(
        "parent_shas_verified_with_corrupt_control",
        ok,
        {
            "problems": real,
            "control_problems": control,
            "raw_days_checked": raw_checked,
            "raw_problems": raw_problems,
            "input_shas_days": len(manifest.get("input_shas") or {}),
            "parents": sorted((manifest.get("parents") or {}).keys()),
        },
    )


def t_declared_schemas(out_data: Path) -> dict:
    """Every payload's physical parquet schema matches schema.json names and dtypes."""
    dtypes = declared_dtypes()
    name_of = {
        "prints.selected_paths": "selected_path_prints",
        "grid.selected_paths": "selected_path_grid",
    }
    problems = []
    n = 0
    for path in _all_parquet(out_data):
        parts = path.relative_to(out_data).parts
        table = (
            parts[0][: -len(".parquet")]
            if parts[0].endswith(".parquet")
            else name_of.get(parts[0], parts[0])
        )
        if table not in dtypes:
            continue
        schema = pl.scan_parquet(path).collect_schema()
        n += 1
        for col, want in dtypes[table].items():
            if col not in schema:
                problems.append(f"{path.name}: missing {col}")
            elif schema[col] != want:
                problems.append(f"{path.name}: {col} {schema[col]} != {want}")
        extra = [c for c in schema.names() if c not in dtypes[table]]
        if extra:
            problems.append(f"{path.name}: undeclared columns {extra}")
    return _ok(
        "declared_dtypes_enforced_everywhere",
        not problems,
        {"payloads_checked": n, "problems": problems[:10]},
    )


def t_forbidden_registry() -> dict:
    """The deny list is registry-derived and contains every named outcome column."""
    named = [
        "bars_to_next_high",
        "bars_to_peak",
        "cost_of_waiting",
        "dd_before_next_high",
        "final_high_flag",
        "level_ret",
        "peak_et_after_t",
        "remaining_run",
        "v_giveback_10",
        "future_forced_flat_px",
        "terminal_censored",
        "tail_class_50",
    ]
    denied = denied_column_names()
    missing = [c for c in named if c not in denied]
    controls = {}
    for col in named:
        try:
            assert_observation_frame(pl.DataFrame({"day": ["2021-02-01"], col: [1.0]}), "synthetic")
            controls[col] = False
        except ValueError:
            controls[col] = True
    ok = not missing and all(controls.values())
    return _ok(
        "forbidden_registry_full_and_enforced",
        ok,
        {"registry_size": len(denied), "missing": missing, "controls_failing": controls},
    )


def t_blocker_branches(out_data: Path) -> dict:
    """B1..B6 exist in coverage with the matching missingness class, and the B6 DAYS are the
    ones that actually measure as unreconciled - not a hardcoded pair. A day the reconciliation
    verified must carry no B6 row; a day that is still unreconciled must carry one.
    """
    cov = pl.read_parquet(out_data / "coverage.parquet")
    detail: dict = {}
    ok = True
    # B1/B2/B6 branch rows are emitted only while the condition HOLDS. The contract is explicit
    # that an admitted B1 day is "no longer floor-qualified" and that B2 is decided by the count
    # of PIT names no source explains - so with the acquisition landed, zero B1/B2 rows is the
    # CORRECT state, not a missing report. We assert the rows match the measured open set, which
    # still fails if an open branch goes unreported.
    open_days = unadmitted_acquisition_days()
    expected_by_blocker = {
        "B1_raw_2025_02": {d for d in open_days if d.startswith("2025-02")},
        "B2_raw_roster_2026_04_05": {d for d in open_days if d.startswith(("2026-04", "2026-05"))},
        "B6_unreconciled_net_manifest": set(net_unreconciled_days(dev_days())),
    }
    detail["open_branch_days"] = {k: sorted(v) for k, v in expected_by_blocker.items()}
    for bid, days in expected_by_blocker.items():
        got = set(cov.filter(pl.col("blocker_id").fill_null("").str.contains(bid))["day"].to_list())
        if got != days:
            ok = False
            detail.setdefault("branch_mismatch", {})[bid] = {
                "in_coverage": sorted(got),
                "measured_open": sorted(days),
            }
    detail["measured_branch_match"] = not detail.get("branch_mismatch")
    for bid, mclass in (
        ("B1_raw_2025_02", "qualified_floor_source"),
        ("B2_raw_roster_2026_04_05", "raw_roster_missing"),
        ("B3_quote_lane", None),
        ("B4_canary_only", None),
        ("B5_storage_headroom", "storage_headroom"),
        ("B6_unreconciled_net_manifest", "net_manifest_missing"),
    ):
        sub = cov.filter(pl.col("blocker_id").fill_null("").str.contains(bid))
        detail[bid] = {"rows": sub.height, "days": int(sub["day"].n_unique())}
        if bid in expected_by_blocker:
            # presence is governed by the MEASURED open set checked below; a blanket
            # "rows must exist" rule would pin the pre-acquisition state forever
            detail[bid]["missingness_rows"] = (
                cov.filter(pl.col("missingness_class") == mclass).height if mclass else 0
            )
            continue
        ok = ok and sub.height > 0
        if mclass:
            detail[bid]["missingness_rows"] = cov.filter(
                pl.col("missingness_class") == mclass
            ).height
    b6 = cov.filter(pl.col("blocker_id").fill_null("").str.contains("B6"))
    b6_days = set(b6["day"].to_list())
    measured_unreconciled = set(net_unreconciled_days(dev_days()))
    detail["B6_days"] = sorted(b6_days)
    detail["measured_unreconciled_days"] = sorted(measured_unreconciled)
    detail["reconciliation_verified_days"] = sorted(net_reconciliation()["days"])
    if b6_days != measured_unreconciled:
        ok = False
        detail["b6_mismatch"] = {
            "in_coverage_not_measured": sorted(b6_days - measured_unreconciled),
            "measured_not_in_coverage": sorted(measured_unreconciled - b6_days),
        }
    for d in net_reconciliation()["days"]:
        if d in b6_days:
            ok = False
            detail.setdefault("verified_days_still_blocked", []).append(d)
    sorted_ok = cov.select(["layer", "day", "scope"]).equals(
        cov.select(["layer", "day", "scope"]).sort(["layer", "day", "scope"])
    )
    detail["sorted_by_layer_day_scope"] = bool(sorted_ok)
    ok = ok and bool(sorted_ok)
    return _ok("coverage_blocker_branches_B1_B6", ok, detail)


def t_branch_coverage(out_data: Path, days: list[str]) -> dict:
    bc = measure_branch_coverage(out_data, days)
    ok = bool(
        bc["terminal_censored_exercised"]
        and bc["provider_bar_exercised"]
        and bc["excluded_prints_exercised"]
        and bc["no_print_exercised"]
        and bc["quote_not_acquired_exercised"]
    )
    return _ok("canary_branch_coverage_exercised", ok, bc)


def t_sentinel_2022_03_10() -> dict:
    """2022-03-10 sentinel: raw prints strictly above the same-day bar high, recomputed
    from the raw lanes this producer uses; the certification artifact is cross-checked
    on the overlapping names (its own symbol universe differs from the net lane)."""
    day = "2022-03-10"
    cert_path = ROOT / "factory/artifacts/basket/sip/certification_2022-03-10.json"
    cert = load_json(cert_path)
    tail = (cert.get("tail") or {}).get("symbols") or {}
    flagged = {
        s: v
        for s, v in tail.items()
        if v.get("sip_trade_max_raw") is not None
        and v.get("sip_bar_max_rth") is not None
        and float(v["sip_trade_max_raw"]) > float(v["sip_bar_max_rth"])
    }
    trades = pl.read_parquet(NET_TRADES / f"{day}.parquet", columns=["symbol", "price"])
    agg = trades.group_by("symbol").agg(pl.col("price").max().alias("mx"))
    tmax = {s: float(v) for s, v in zip(agg["symbol"].to_list(), agg["mx"].to_list(), strict=False)}
    bars = pl.read_parquet(NET_BARS / f"{day}.parquet", columns=["ticker", "high"])
    bmax = {
        s: float(v)
        for s, v in zip(bars["ticker"].to_list(), bars["high"].to_list(), strict=False)
        if v is not None
    }
    lane = sorted(s for s in tmax if s in bmax and tmax[s] > bmax[s])
    overlap = sorted(set(flagged) & set(tmax) & set(bmax))
    matched = [s for s in overlap if abs(tmax[s] - float(flagged[s]["sip_trade_max_raw"])) < 1e-9]
    ok = len(flagged) > 0 and len(lane) > 0 and len(matched) > 0
    return _ok(
        "sentinel_2022_03_10_raw_bad_prints_verifiable",
        ok,
        {
            "artifact_flagged_symbols": len(flagged),
            "artifact_symbols": len(tail),
            "lane_symbols_with_print_above_bar": len(lane),
            "lane_examples": lane[:5],
            "overlap_names": len(overlap),
            "artifact_values_matched": len(matched),
            "certification_sha256": sha256_file(cert_path),
        },
    )


def verify_check_fns(contracts: dict, out_data: Path, manifest: dict, days: list[str]) -> list:
    """The REAL verify-check factory list (single source of truth for the harness)."""
    return [
        lambda: t_contracts(contracts, load_json(TRACK / "contract_lock.json")),
        lambda: t_readiness_split(contracts),
        lambda: t_payload_shas(out_data, manifest),
        lambda: t_no_forbidden_columns(out_data),
        lambda: t_day_registry(out_data),
        lambda: t_identity_census(out_data, panel_memberships(), days),
        lambda: t_pointer_spotcheck(out_data, days, n_days=3, n_rows=3),
        lambda: t_minute_states(out_data, days),
        lambda: t_rank_recompute(out_data, days, sample_minutes=(575, 605, 660, 750)),
        lambda: t_rank_null_rule(out_data, days),
        lambda: t_prev_close_flags(out_data, days),
        lambda: t_blockers_visible(out_data),
        lambda: t_candidate_roster(out_data, days),
        lambda: t_quote_roster(out_data, days),
        lambda: t_parent_shas(out_data, manifest, days),
        lambda: t_declared_schemas(out_data),
        t_forbidden_registry,
        lambda: t_blocker_branches(out_data),
        lambda: t_branch_coverage(out_data, days),
        t_sentinel_2022_03_10,
        lambda: t_adaptive_choice_ledger(contracts),
        lambda: t_quote_feature_guard(contracts),
        t_storage_conservative_reading,
        t_acquisition_admission_rules,
        t_resume_disk_demand,
        t_rank_filter_semantics,
        t_full_build_resource_sizing,
        t_resume_progress_credit,
        t_multi_lane_digest,
        t_acquisition_obligation_rules,
        t_cross_feed_class_precedence,
        t_reference_unqualified_rank_nulling,
        t_evidence_forgery_refusal,
        t_offhours_only_name_is_observed,
        t_source_generation_guard_fires,
        t_registry_patch_contract,
        t_post_build_chain_small_slice,
        t_origin_proof_chain,
        t_prefix_perturbation,
        t_admission_union_scope_gate,
        t_acquisition_day_set_coverage,
        lambda: t_verify_stage_wiring(out_data, manifest, days),
        lambda: t_branch_claims(out_data, days, contracts),
    ]


def t_verify_stage_wiring(out_data: Path, manifest: dict, days: list[str]) -> dict:
    """Type-check the REAL verify-check factory list (never a toy list)."""
    fns = verify_check_fns(load_contracts(), out_data, manifest or {}, days)
    problems = [f"entry {k} is not callable" for k, f in enumerate(fns) if not callable(f)]
    invoked = 0
    if not problems:
        probe = t_forbidden_registry  # cheap real check already present in the real list
        res = probe()
        invoked += 1
        if not isinstance(res, dict) or "ok" not in res:
            problems.append(f"real check returned {type(res).__name__}")
    return _ok(
        "verify_stage_harness_invokes_callables",
        not problems,
        {"entries": len(fns), "problems": problems, "real_checks_invoked": invoked},
    )


def t_adaptive_choice_ledger(contracts: dict) -> dict:
    """The tracked adaptive choice ledger exists, is blind and reads no outcome column."""
    ledger = contracts.get("adaptive_choice_ledger.json") or {}
    denied = denied_column_names()
    problems = []
    if not ledger.get("blind"):
        problems.append("ledger not blind")
    if ledger.get("outcome_columns_read"):
        problems.append("ledger lists outcome columns read")
    for e in ledger.get("entries", []):
        leaked = [
            c
            for c in e.get("columns_read", [])
            if c.split(" ")[0] in denied or is_quote_feature(c.split(" ")[0], contracts)
        ]
        if leaked:
            problems.append(f"{e.get('choice_id')}: {leaked}")
        if not e.get("blind_flag"):
            problems.append(f"{e.get('choice_id')}: blind_flag false")
    return _ok(
        "adaptive_choice_ledger_blind_and_complete",
        not problems and len(ledger.get("entries", [])) >= 5,
        {"entries": len(ledger.get("entries", [])), "problems": problems},
    )


def t_branch_claims(out_data: Path, days: list[str], contracts: dict) -> dict:
    """Only days that MEASURE observation ends_early may claim it."""
    frozen = contracts["canary_days.json"]
    claim = {d["day"]: (d.get("branch_claim") or {}) for d in frozen["days"]}
    measured = {}
    for day in days:
        gp = out_data / "grid.selected_paths" / f"month={day[:7]}" / f"{day}.parquet"
        if gp.exists():
            g = pl.read_parquet(gp, columns=["coverage_class"])
            measured[day] = int((g["coverage_class"] == "ends_early").sum())
    problems = []
    asserted = [d for d, c in claim.items() if c.get("kind") == "observation_ends_early_exercised"]
    for d in asserted:
        if measured.get(d, 0) <= 0:
            problems.append(f"{d}: claims observation ends_early but measured 0")
    if not asserted:
        problems.append("no day asserts observation ends_early")
    for d, c in claim.items():
        if c.get("kind") == "panel_member_censor_anchor" and c.get("asserted"):
            problems.append(f"{d}: panel censor anchor must not assert the observation branch")
    return _ok(
        "canary_branch_claims_match_measurement",
        not problems,
        {
            "measured_ends_early": {d: n for d, n in measured.items() if n},
            "asserted_days": sorted(asserted),
            "problems": problems,
        },
    )


def t_determinism(manifest: dict, previous_manifest: dict | None) -> dict:
    if not previous_manifest:
        out = _ok(
            "byte_identical_second_run",
            True,
            {
                "pending": True,
                "detail": "no previous manifest; rerun --stage canary --force to prove "
                "byte-identity of every deterministic payload",
            },
        )
        out["pending"] = True
        return out
    diffs = []
    payloads = manifest.get("payload_sha256") or manifest.get("payloads") or {}
    prev_payloads = (
        previous_manifest.get("payload_sha256") or previous_manifest.get("payloads") or {}
    )
    for key, info in payloads.items():
        old = prev_payloads.get(key, {})
        if old.get("sha256") != info.get("sha256"):
            diffs.append(key)
    return _ok(
        "byte_identical_second_run", not diffs, {"n_payloads": len(payloads), "differing": diffs}
    )


def run_selftests(
    out_data: Path,
    out_evidence: Path,
    contracts: dict,
    results: dict,
    paths: pl.DataFrame,
    mem: pl.DataFrame,
    registry: pl.DataFrame,
    coverage: pl.DataFrame,
    stamp: dict,
) -> dict:
    frozen_days = [d["day"] for d in contracts["canary_days.json"]["days"]]
    days = sorted(results) if results else frozen_days
    panel = panel_memberships()
    lock_path = TRACK / "contract_lock.json"
    lock = load_json(lock_path) if lock_path.exists() else {}
    checks = []
    for fn in (
        lambda: t_contracts(contracts, lock),
        lambda: t_readiness_split(contracts),
        t_guard,
        lambda: t_canary_selection(contracts),
        lambda: t_canary_ledger(contracts),
        t_forbidden_negative_controls,
        t_pure_rank_and_state,
        t_prev_close_rules,
        lambda: t_payload_shas(out_data, stamp["manifest"]),
        lambda: t_no_forbidden_columns(out_data),
        lambda: t_day_registry(out_data),
        lambda: t_identity_census(out_data, panel, days),
        lambda: t_pointer_spotcheck(out_data, days),
        lambda: t_minute_states(out_data, days),
        lambda: t_rank_recompute(out_data, days),
        lambda: t_rank_null_rule(out_data, days),
        lambda: t_prev_close_flags(out_data, days),
        lambda: t_blockers_visible(out_data),
        lambda: t_candidate_roster(out_data, days),
        lambda: t_quote_roster(out_data, days),
        lambda: t_parent_shas(out_data, stamp["manifest"], days),
        lambda: t_declared_schemas(out_data),
        t_forbidden_registry,
        lambda: t_blocker_branches(out_data),
        lambda: t_branch_coverage(out_data, days),
        t_sentinel_2022_03_10,
        lambda: t_adaptive_choice_ledger(contracts),
        lambda: t_quote_feature_guard(contracts),
        t_storage_conservative_reading,
        t_acquisition_admission_rules,
        t_resume_disk_demand,
        t_rank_filter_semantics,
        t_full_build_resource_sizing,
        t_resume_progress_credit,
        t_multi_lane_digest,
        t_acquisition_obligation_rules,
        t_cross_feed_class_precedence,
        t_reference_unqualified_rank_nulling,
        t_evidence_forgery_refusal,
        t_offhours_only_name_is_observed,
        t_source_generation_guard_fires,
        t_registry_patch_contract,
        t_post_build_chain_small_slice,
        t_origin_proof_chain,
        t_prefix_perturbation,
        t_admission_union_scope_gate,
        t_acquisition_day_set_coverage,
        lambda: t_verify_stage_wiring(out_data, stamp["manifest"], days),
        lambda: t_branch_claims(out_data, days, contracts),
        lambda: t_determinism(stamp["manifest"], stamp["previous_manifest"]),
    ):
        t_chk = time.time()
        result = fn()
        result["seconds"] = round(time.time() - t_chk, 2)
        checks.append(result)
    return {
        "all_ok": all(c["ok"] for c in checks),
        "n_checks": len(checks),
        "n_failed": sum(1 for c in checks if not c["ok"]),
        "checks": list(checks),
    }


def stage_selftest(contracts: dict) -> dict:
    """Pure checks runnable without a built corpus."""
    lock_path = TRACK / "contract_lock.json"
    lock = load_json(lock_path) if lock_path.exists() else {}
    checks = [
        t_contracts(contracts, lock),
        t_readiness_split(contracts),
        t_guard(),
        t_canary_selection(contracts),
        t_canary_ledger(contracts),
        t_forbidden_negative_controls(),
        t_quote_feature_guard(contracts),
        t_storage_conservative_reading(),
        t_acquisition_admission_rules(),
        t_resume_disk_demand(),
        t_rank_filter_semantics(),
        t_full_build_resource_sizing(),
        t_resume_progress_credit(),
        t_multi_lane_digest(),
        t_acquisition_obligation_rules(),
        t_cross_feed_class_precedence(),
        t_reference_unqualified_rank_nulling(),
        t_evidence_forgery_refusal(),
        t_offhours_only_name_is_observed(),
        t_source_generation_guard_fires(),
        t_registry_patch_contract(),
        t_post_build_chain_small_slice(),
        t_origin_proof_chain(),
        t_prefix_perturbation(),
        t_admission_union_scope_gate(),
        t_acquisition_day_set_coverage(),
        t_pure_rank_and_state(),
        t_prev_close_rules(),
        t_forbidden_registry(),
        t_sentinel_2022_03_10(),
    ]
    return {
        "all_ok": all(c["ok"] for c in checks),
        "n_checks": len(checks),
        "n_failed": sum(1 for c in checks if not c["ok"]),
        "checks": checks,
    }


def stage_verify_canary(out_data: Path, out_evidence: Path) -> dict:
    """Independent verification of a built canary from the files on disk."""
    contracts = load_contracts()
    manifest = load_json(out_evidence / "manifest.json")
    days = manifest.get("canary_days") or [d["day"] for d in contracts["canary_days.json"]["days"]]
    check_fns = verify_check_fns(contracts, out_data, manifest, days)
    checks = []
    for fn in check_fns:
        t_chk = time.time()
        result = fn()
        if not isinstance(result, dict) or "ok" not in result:
            raise TypeError(
                f"verify check {getattr(fn, '__name__', fn)} returned "
                f"{type(result).__name__} instead of a result dict"
            )
        result["seconds"] = round(time.time() - t_chk, 2)
        checks.append(result)
    out = {
        "node": manifest["node_id"],
        "core_hash": manifest.get("core_hash"),
        "all_ok": all(c["ok"] for c in checks),
        "n_checks": len(checks),
        "n_failed": sum(1 for c in checks if not c["ok"]),
        "checks": checks,
        "verified_days": len(days),
    }
    atomic_write_json(out_evidence / "verify.json", out)
    return out


# --------------------------------------------------------------------------- #
# full build entry (gated on the CORE acquisition blockers; B3 is quote-sibling)
# --------------------------------------------------------------------------- #


def stage_full(
    out_data: Path,
    out_evidence: Path,
    workers: int,
    force: bool,
    resolved_state: Path | str | None = None,
) -> dict:
    """Full 1,066-day core v0 build behind the frozen CORE acquisition + storage gate.

    Core hard blockers: B1 raw 2025-02, B2 2026-04/05 raw roster hole, B5 storage
    headroom, B6 unreconciled net manifests - each counts as resolved only when its declared
    evidence FILE exists, re-hashes to the declared sha256, and its contents attest the repair.
    The storage half is an ENTRY gate: >= 75 GiB of conservatively measured free space on the
    backing volume of the actual output mount (planning band 70-80 GiB), or, on a resume, that
    budget minus the payload bytes that re-verify against the previous manifest, floored at a
    10 GiB operating reserve. The physical mount check itself never relaxes. B3_quote_lane is a
    quote-sibling blocker: it gates only the prospective quote channel and never the core
    bar/print/race corpus. The canary-only marker (B4) refuses a canary claim, not this stage.
    No flag bypasses either gate.
    """
    contracts = load_contracts()
    contract = contracts["contract.json"]
    core_ids = blockers_by_class(contract, BLOCKER_CLASS_CORE)
    quote_ids = blockers_by_class(contract, BLOCKER_CLASS_QUOTE)
    path = resolved_state_path(resolved_state)
    declared = load_resolved_state(resolved_state)
    evidence = {b: blocker_evidence_state(b, declared) for b in core_ids + quote_ids}
    outstanding = [b for b in core_ids if not evidence[b]["verified"]]
    quote_outstanding = [b for b in quote_ids if not evidence[b]["verified"]]
    prior_mpath = out_evidence / "manifest.json"
    prior_manifest = load_json(prior_mpath) if prior_mpath.exists() else None
    storage = storage_gate_report(
        out_data,
        "full",
        resolved_state,
        manifest=prior_manifest,
        progress=load_progress(out_data),
    )
    if outstanding or not storage["ok"]:
        detail = {
            "outstanding_core_blockers": outstanding,
            "core_blockers": core_ids,
            "core_evidence_state": evidence,
            "quote_channel_blockers": quote_ids,
            "quote_channel_outstanding_blockers": quote_outstanding,
            "resolved_manifest": str(path),
            "resolved_manifest_present": path.exists(),
            "output_root": str(out_data),
            "storage": storage,
            "required_free_bytes": storage["reserve_bytes"],
            "reserve_basis": storage["reserve_basis"],
            "operating_reserve_bytes": OPERATING_RESERVE_BYTES,
            "planning_headroom_bytes": [
                PLANNING_HEADROOM_LO_BYTES,
                PLANNING_HEADROOM_HI_BYTES,
            ],
            "quote_channel_note": (
                "B3_quote_lane is a quote-sibling blocker and never refuses the core build"
            ),
            "note": "no flag bypasses this gate; clear the core blockers (B1,B2,B5,B6) in the "
            "tracked resolved-state manifest with evidence files that exist and re-hash, and "
            "ensure the conservatively measured free space on the backing volume of the actual "
            "output mount covers the reserve above (declare "
            f"'{HOST_VOLUME_DECLARATION_KEY}' there when that mount is a virtual/unknown local "
            "disk). A resume may subtract only payload bytes that re-verify against the "
            "previous manifest and always keeps the 10 GiB operating reserve; the quote channel "
            "gates separately and is not required here",
        }
        raise SystemExit("full build gate refusal: " + json.dumps(detail, indent=1))
    Path(out_data).mkdir(parents=True, exist_ok=True)
    return stage_build(
        out_data,
        out_evidence,
        workers,
        force,
        days=dev_days(),
        mode="full",
        resolved_state=resolved_state,
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Freeze-O Stage-0 tape atlas observation producer")
    ap.add_argument("--selftest", action="store_true", help="pure contract/function self-tests")
    ap.add_argument("--stage", choices=["contract", "canary", "verify-canary", "full"])
    ap.add_argument(
        "--out-data",
        default=str(DEFAULT_OUT_DATA),
        help="absolute gitignored data root for large payloads",
    )
    ap.add_argument(
        "--out-evidence",
        default=EVIDENCE_REL_DEFAULT,
        help="tracked evidence root (relative to the repo or absolute)",
    )
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument(
        "--days",
        default=None,
        help="smoke/dev filter: comma-separated dev days to build instead of the "
        "frozen canary list (recorded in the summary as day_filter)",
    )
    ap.add_argument("--force", action="store_true")
    ap.add_argument(
        "--resolved-state",
        default=None,
        help="full stage: only the tracked blockers_resolved.json declaration is accepted; "
        "any other path is refused (no bypass/ack flag)",
    )
    args = ap.parse_args(argv)

    _FORCE["value"] = bool(args.force)
    if args.selftest:
        res = stage_selftest(load_contracts())
        print(json.dumps({k: v for k, v in res.items() if k != "checks"}, indent=1))
        for c in res["checks"]:
            print(("PASS " if c["ok"] else "FAIL ") + c["check"])
        return 0 if res["all_ok"] else 1
    if args.stage == "contract":
        lock = stage_contract(write=True)
        print(json.dumps(lock, indent=1))
        return 0
    if args.stage == "canary":
        out_data = Path(args.out_data)
        out_evidence = Path(args.out_evidence)
        if not out_evidence.is_absolute():
            out_evidence = ROOT / out_evidence
        day_filter = [d.strip() for d in args.days.split(",")] if args.days else None
        res = stage_canary(out_data, out_evidence, args.workers, args.force, days=day_filter)
        print(
            json.dumps(
                {
                    k: v
                    for k, v in res["summary"].items()
                    if k != "determinism_vs_previous_manifest"
                },
                indent=1,
            )
        )
        return 0 if res["summary"]["selftest_ok"] else 1
    if args.stage == "full":
        out_data = Path(args.out_data)
        out_evidence = Path(args.out_evidence)
        if args.out_evidence == EVIDENCE_REL_DEFAULT:
            out_evidence = Path(EVIDENCE_REL_DEFAULT.replace("/canary", "/full"))
        if not out_evidence.is_absolute():
            out_evidence = ROOT / out_evidence
        res = stage_full(
            out_data, out_evidence, args.workers, args.force, resolved_state=args.resolved_state
        )
        print(
            json.dumps(
                {
                    k: v
                    for k, v in res["summary"].items()
                    if k != "determinism_vs_previous_manifest"
                },
                indent=1,
            )
        )
        return 0 if res["summary"]["selftest_ok"] else 1
    if args.stage == "verify-canary":
        out_data = Path(args.out_data)
        out_evidence = Path(args.out_evidence)
        if not out_evidence.is_absolute():
            out_evidence = ROOT / out_evidence
        res = stage_verify_canary(out_data, out_evidence)
        print(json.dumps({k: v for k, v in res.items() if k != "checks"}, indent=1))
        for c in res["checks"]:
            print(("PASS " if c["ok"] else "FAIL ") + c["check"])
        return 0 if res["all_ok"] else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
