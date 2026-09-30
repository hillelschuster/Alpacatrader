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
  out-of-window rows, or unresolved contradictory zeros make the day
  ``incomplete`` and refuse admission.

Usage (from the repo root)::

  .venv/bin/python factory/scripts/atlas_acquire_sip_bars.py --selftest
  .venv/bin/python factory/scripts/atlas_acquire_sip_bars.py --stage plan --scope feb2025
  .venv/bin/python factory/scripts/atlas_acquire_sip_bars.py --stage acquire --scope feb2025
  .venv/bin/python factory/scripts/atlas_acquire_sip_bars.py --stage verify  --scope feb2025
  .venv/bin/python factory/scripts/atlas_acquire_sip_bars.py --stage acquire \
      --scope aprmay2026 --days 2026-04-01
  .venv/bin/python factory/scripts/atlas_acquire_sip_bars.py --stage verify --scope aprmay2026 \
      --out-data /home/hillel/projects/Alpacatrader/data/atlas/acquisition/v1

``--stage acquire`` needs ``ALPACA_API_KEY``/``ALPACA_SECRET_KEY`` in the
environment (or the repo ``.env``). ``plan`` and ``verify`` are offline.
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
from dataclasses import dataclass
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

ET = ZoneInfo("America/New_York")

SCOPES = ("feb2025", "aprmay2026")
BLOCKER_OF_SCOPE = {"feb2025": "B1_raw_2025_02", "aprmay2026": "B2_raw_roster_2026_04_05"}
SCOPE_MONTHS = {"feb2025": ("2025-02",), "aprmay2026": ("2026-04", "2026-05")}
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
                    pl.col("timestamp").dt.convert_time_zone("America/New_York").dt.hour().cast(
                        pl.Int32
                    )
                    * 60
                    + pl.col("timestamp").dt.convert_time_zone("America/New_York").dt.minute().cast(
                        pl.Int32
                    )
                ).alias("et")
            )
        )
        all_counts = (
            base.group_by("ticker")
            .len()
            .collect(engine="streaming")
        )
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
                "feb2025: PIT x (compact-observed UNION replaced-baseline in-window), "
                "because this payload REPLACES the floored fallback. aprmay2026: "
                "PIT x compact-observed; the baseline lane is retained and covers its own names."
            ),
            "observed_bar_evidence_names": len(self.observed_bars),
            "raw_existing": self.raw_existing,
            "alias_existing": self.alias_existing,
            "unavailable": self.unavailable,
            "baseline": self.baseline,
            "compact": self.compact,
        }


def baseline_lane_path(scope: str, day: str, data: Path) -> Path:
    month = day[:7]
    if scope == "feb2025":
        return data / f"clean_ohlcv_{month}.parquet"
    return data / "backfill" / f"ohlcv_{month}.parquet"


def plan_day(
    day: str,
    scope: str,
    pit: PitIndex,
    aliases: dict[str, Alias],
    compact: CompactIndex,
    lanes: LaneIndex,
    data: Path,
) -> DayPlan:
    sim.guard_day(day)
    vintage, pit_set = pit.symbols(day)
    comp_syms = compact.symbols(day)
    comp_nbars = compact.n_bars(day)
    lane_path = baseline_lane_path(scope, day, data)
    lanes.register(day[:7], lane_path)
    lane_any, lane_win = lanes.day(day[:7], day)
    comp_man = compact.manifest_path(day)
    comp_sha = sha256_file_cached(comp_man)

    provider_of: dict[str, str] = {}
    aliases_used: dict[str, str] = {}
    raw_existing: list[str] = []
    alias_existing: list[str] = []
    # Positive bar-count evidence that some source saw this name trade. The
    # compact SIP source is not the only such source, and treating it as the
    # only one is what let baseline-only names be silently dropped.
    observed_bars: dict[str, int] = {}
    for name, n in lane_win.items():
        observed_bars[name] = max(observed_bars.get(name, 0), n)
    for name, n in comp_nbars.items():
        observed_bars[name] = max(observed_bars.get(name, 0), int(n or 0))
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
            "standing": "replaced_by_this_acquisition" if scope == "feb2025" else "supplemented",
        },
        compact={
            "path": str(compact.day_path(day)),
            "sha256": sha256_file_cached(compact.day_path(day)),
            "present": compact.day_path(day).exists(),
            "symbols": len(comp_syms),
            "is_sip_query": bool(comp_sha),
        },
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
    zero_bars = sorted(
        set(plan.requested) - with_data - set(invalid) - set(request_failed)
    )
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

    # Contradiction oracle. Evidence is EVERY source that recorded a positive
    # bar count for a name - the compact SIP source AND the baseline lane. The
    # compact source alone was structurally blind to any name it never observed,
    # which is exactly how the baseline-only names (BK, ARMN) came back as
    # clean zeros instead of revisions.
    contradictions: list[dict] = []
    for sym in sorted(plan.requested):
        seen = plan.observed_bars.get(sym, 0)
        if seen and seen > 0 and sym in zero_bars:
            contradictions.append(
                {
                    "ticker": sym,
                    "observed_n_bars": int(seen),
                    "fresh_n_bars": 0,
                    "reason": "a source recorded bars for this name; the fresh pull returned "
                    "none. Either the provider revised its tape or the name is not "
                    "requestable under this spelling - unresolved either way.",
                }
            )

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
    if contradictions:
        status = "incomplete"
        refusal.append("unresolved_contradictory_zeros")
    if df.height == 0:
        status = "incomplete"
        refusal.append("empty_payload")

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
            "replaced-baseline in-window) must be covered by this payload. aprmay2026 "
            "supplements the baseline lane, which remains in place.",
            "baseline_standing": plan.baseline.get("standing"),
            "required_names": len(plan.expected_present),
            "observed_evidence_names": len(plan.observed_bars),
        },
        "compact_crosscheck": {
            "compared": sum(1 for s in plan.requested if plan.observed_bars.get(s, 0) > 0),
            "evidence_sources": ["compact_sip", "baseline_lane"],
            "contradictory_zeros": contradictions,
        },
        "roster_sha256": roster_sha,
        "roster": f"rosters/{plan.day}.json",
        "input_sha256": {
            "pit_parquet": pit_sha,
            "compact_parquet": plan.compact.get("sha256"),
            "compact_manifest": plan.compact.get("manifest_sha256"),
            "baseline_lane": plan.baseline.get("sha256"),
            "alias_map": alias_map_sha,
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
            f"errors={len(errors)} {man['elapsed_s']}s",
            flush=True,
        )
    return {**man, "action": "acquired"}



# --------------------------------------------------------------------------- #
# stages
# --------------------------------------------------------------------------- #
def build_context(out_data: Path, contract: dict) -> dict:
    data = resolve_data_root()
    fetch = contract["fetch"]
    lo, hi = int(fetch["window_et"][0]), int(fetch["window_et"][1])
    return {
        "data": data,
        "out_data": out_data,
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
    }


def stage_plan(args: argparse.Namespace) -> dict:
    contract = load_contract()
    out_data = Path(args.out_data)
    ctx = build_context(out_data, contract)
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
            day, args.scope, ctx["pit"], ctx["aliases"], ctx["compact"], ctx["lanes"], ctx["data"]
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
        "days": days,
        "n_days": len(days),
        "per_day": per_day,
        "totals": totals,
        "inputs": {
            "pit_parquet_sha256": ctx["pit"].sha256,
            "alias_map_sha256": ctx["alias_map_sha"],
            "contract_sha256": ctx["contract_sha"],
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
            "contradictory_zero": "compact source has n_bars>0 but the fresh pull has none;"
            " unresolved, blocks admission",
            "api_error": "429/5xx retried with bounded backoff; on exhaustion the day is "
            "incomplete and unadmitted",
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
    out_data = Path(args.out_data)
    ctx = build_context(out_data, contract)
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
            day, args.scope, ctx["pit"], ctx["aliases"], ctx["compact"], ctx["lanes"], ctx["data"]
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
            )
        )
    admitted = [r["day"] for r in results if r["status"] == REQUIRED_STATUS_ADMITTED]
    return {
        "schema": "atlas.acquisition.acquire.v0",
        "stage": "acquire",
        "scope": args.scope,
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
    plan: DayPlan, out_data: Path, code_sha: str, contract_window: tuple[int, int]
) -> tuple[list[dict], dict]:
    """Re-derive every claim from the bytes on disk. Nothing is taken on trust."""
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
    add("code_sha256_matches_current", man.get("code_sha256") == code_sha, man.get("code_sha256"))
    add(
        "contract_sha256_matches_current",
        man.get("contract_sha256") == sha256_file(CONTRACT_PATH),
        man.get("contract_sha256"),
    )
    add(
        "alias_map_sha256_matches_current",
        (man.get("input_sha256") or {}).get("alias_map") == sha256_file(ALIAS_MAP_PATH),
        (man.get("input_sha256") or {}).get("alias_map"),
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
    add("manifest_window_utc_matches_bounds", sent == expected_sent, {"declared": sent,
                                                                     "from_bounds": expected_sent})
    readable = (man.get("source") or {}).get("window_et")
    readable_expected = f"{lo // 60:02d}:{lo % 60:02d}-{hi // 60:02d}:{hi % 60:02d}"
    add("manifest_window_readable_matches_bounds", readable == readable_expected,
        {"declared": readable, "from_bounds": readable_expected})
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
    accounted = (
        present | set(man.get("symbols_zero_bars") or []) | set(man.get("symbols_invalid") or [])
    )
    add(
        "all_requested_symbols_accounted",
        set(plan.requested) <= accounted,
        sorted(set(plan.requested) - accounted)[:20],
    )
    add(
        "no_unrequested_symbol_in_payload",
        present <= set(plan.requested),
        sorted(present - set(plan.requested))[:20],
    )
    add("manifest_errors_empty", not man.get("errors"), man.get("errors"))
    add("status_complete", man.get("status") == REQUIRED_STATUS_ADMITTED, man.get("status"))

    cross = (man.get("compact_crosscheck") or {}).get("contradictory_zeros") or []
    add("no_unresolved_contradictory_zeros", not cross, cross[:10])
    # Coverage of the OBLIGATION set depends on the scope's standing:
    #   feb2025   the acquired day file REPLACES the floored fallback, so every
    #             obligated name must be in THIS payload. The obligation is PIT
    #             x (compact-observed UNION replaced-baseline in-window): a name
    #             the fallback carried but the compact source never saw is still
    #             owed, and dropping it silently was the bug that let BK/ARMN
    #             vanish from 19 of 19 days with status=complete.
    #   aprmay2026 the acquired day file SUPPLEMENTS the baseline lane, so an
    #             obligated name is covered when it is either in this payload or
    #             already in the baseline lane for that day.
    baseline_covers = (
        set() if plan.scope == "feb2025" else set(plan.raw_existing) | set(plan.alias_existing)
    )
    covered = present | baseline_covers
    obligation = set(plan.expected_present)
    add(
        "all_obligated_symbols_covered",
        obligation <= covered,
        sorted(obligation - covered)[:20],
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


def current_parents(ctx: dict) -> dict:
    """The identity every per-scope attestation must share to enter the union."""
    return {
        "acquisition_root": str(ctx["out_data"]),
        "contract_sha256": ctx["contract_sha"],
        "alias_map_sha256": ctx["alias_map_sha"],
        "pit_parquet_sha256": ctx["pit"].sha256,
        "code_sha256": ctx["code_sha"],
        "guard_source": "factory/scripts/basket_sim.py:guard_day",
    }


def verify_scope(args: argparse.Namespace, ctx: dict, out_data: Path) -> dict:
    """Verify ONE scope and return its per-scope attestation.

    This never writes the shared union: a scope run publishes only its own
    acquisition_admission_<scope>.json, so verifying B2 can never overwrite the
    B1 attestation.
    """
    days = scope_days(args.scope, _day_filter(args), sim.dev_days())
    bid = BLOCKER_OF_SCOPE[args.scope]
    blockers = {
        bid: {
            "admitted_days": [],
            "unadmitted_days": [],
            "days": [],
            "residual_confirmed_trading_gaps": [],
        }
    }
    all_checks: list[dict] = []
    per_day = []
    residuals: list[dict] = []
    unadmitted: list[dict] = []
    for day in days:
        plan = plan_day(
            day, args.scope, ctx["pit"], ctx["aliases"], ctx["compact"], ctx["lanes"], ctx["data"]
        )
        checks, res = verify_day(plan, out_data, ctx["code_sha"], (ctx["lo"], ctx["hi"]))
        all_checks.extend(checks)
        # A day is admitted only when its own verify checks ALL pass, not merely
        # because a stale manifest claims status=complete.
        day_failed = [c["check"] for c in checks if not c["ok"]]
        man = res.get("manifest") or {}
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
            "errors": man.get("errors") or [],
        }
        per_day.append(entry)
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
            for c in (man.get("compact_crosscheck") or {}).get("contradictory_zeros") or []:
                residuals.append({"day": day, **c})
    blockers[bid]["days"] = per_day
    blockers[bid]["unadmitted_days"] = unadmitted
    blockers[bid]["residual_confirmed_trading_gaps"] = residuals
    # The FULL scope, independent of any --days filter. A run that verified one
    # day has still left eighteen (B1) or forty (B2) days unverified, and the
    # union must be able to see that from the artifact alone.
    full_scope_days = full_scope_day_set(args.scope)
    is_full_scope = set(days) == full_scope_days
    ok = (
        all(c["ok"] for c in all_checks)
        and not residuals
        and not unadmitted
        and is_full_scope
    )
    return {
        "version": "v0",
        "schema": "atlas.acquisition.admission.v0",
        "scope": args.scope,
        "blocker_id": bid,
        "acquisition_root": str(out_data),
        "generated_at": datetime.now(UTC).isoformat(),
        "parents": current_parents(ctx),
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
        "requested_outcome_accounting": {
            "requested_symbols": sum(e["requested_symbols"] or 0 for e in per_day),
            "symbols_with_data": sum(e["symbols_with_data"] or 0 for e in per_day),
            "symbols_zero_bars": sum(e["symbols_zero_bars"] for e in per_day),
            "symbols_invalid": sum(e["symbols_invalid"] for e in per_day),
        },
        "all_checks_passed": ok,
        "failed_checks": [c for c in all_checks if not c["ok"]],
    }


def rehash_scope_days(scope_doc: dict, out_data: Path) -> tuple[int, list[dict]]:
    """Re-hash every day the attestation claims to admit, right now.

    A per-scope file is a claim made earlier; the union repeats the arithmetic
    from the bytes on disk. A day that no longer hashes to what the attestation
    recorded is dropped from the union and named.
    """
    mismatches: list[dict] = []
    checked = 0
    for entry in (
        (scope_doc.get("blockers") or {}).get(scope_doc.get("blocker_id"), {}).get("days", [])
    ):
        day = entry.get("day")
        payload = out_data / "bars" / f"{day}.parquet"
        manifest = out_data / "bars" / f"{day}.manifest.json"
        if not payload.exists() or not manifest.exists():
            mismatches.append({"day": day, "reason": "payload_or_manifest_missing"})
            continue
        actual = sha256_file(payload)
        checked += 1
        if actual != entry.get("file_sha256"):
            mismatches.append(
                {
                    "day": day,
                    "reason": "payload_sha256_mismatch",
                    "attested": entry.get("file_sha256"),
                    "actual": actual,
                }
            )
    return checked, mismatches


def publish_admission_union(out_ev: Path, ctx: dict) -> dict:
    """Rebuild the shared union from the CURRENT per-scope attestations.

    A scope contributes only when its own file exists, was produced by THIS
    acquisition root / code / contract / PIT / alias-map identity, and passes its
    own all_checks_passed. Every contributed day is re-hashed here. A scope that
    was never verified is reported as missing and keeps its blocker open - the
    union never invents a pass for evidence it does not have.
    """
    parents = current_parents(ctx)
    out_data = ctx["out_data"]
    scopes: dict[str, dict] = {}
    missing: list[str] = []
    stale: list[dict] = []
    for scope in SCOPES:
        path = out_ev / f"acquisition_admission_{scope}.json"
        bid = BLOCKER_OF_SCOPE[scope]
        if not path.exists():
            missing.append(scope)
            expected_days = full_scope_day_set(scope)
            scopes[scope] = {
                "blocker_id": bid,
                "state": "unverified",
                "reason": "no per-scope attestation has been produced for this scope",
                "is_full_scope": False,
                "required_days": sorted(expected_days),
                "required_day_count": len(expected_days),
                "admitted_days": [],
                "verified_subset_days": [],
                "missing_required_days": sorted(expected_days),
                "unadmitted_days": [],
                "residual_confirmed_trading_gaps": [],
            }
            continue
        doc = load_json(path)
        if (doc.get("parents") or {}) != parents:
            stale.append(
                {
                    "scope": scope,
                    "file": path.name,
                    "reason": "parents_do_not_match_this_run",
                    "attested_parents": doc.get("parents"),
                }
            )
            scopes[scope] = {
                "blocker_id": bid,
                "state": "stale",
                "reason": "attestation was produced by a different "
                "root/code/contract/PIT/alias identity",
                "is_full_scope": False,
                "required_days": sorted(full_scope_day_set(scope)),
                "required_day_count": len(full_scope_day_set(scope)),
                "admitted_days": [],
                "verified_subset_days": [],
                "missing_required_days": sorted(full_scope_day_set(scope)),
                "unadmitted_days": [],
                "residual_confirmed_trading_gaps": [],
            }
            continue
        block = (doc.get("blockers") or {}).get(bid) or {}
        rechecked, mismatches = rehash_scope_days(doc, out_data)
        # A scope is ready ONLY when it has discharged its WHOLE obligation: the
        # admitted set must equal every guarded dev day the scope owes (19 for
        # B1, 41 for B2). A one-day --days subset is real evidence about that
        # day and nothing more, so it is reported as verified_subset with an
        # empty admitted_days and can never read as full coverage.
        expected_days = full_scope_day_set(scope)
        admitted = list(block.get("admitted_days") or [])
        admitted_set = set(admitted)
        missing_days = sorted(expected_days - admitted_set)
        is_full = not missing_days and admitted_set <= expected_days
        scope_ok = bool(doc.get("all_checks_passed")) and not mismatches
        if not scope_ok:
            state = "not_ready"
            reason = "scope verification did not pass or a day no longer hashes"
        elif not is_full:
            state = "verified_subset"
            reason = (
                f"only {len(admitted)} of {len(expected_days)} required days were verified; "
 f"{len(missing_days)} day(s) still unverified"
            )
        else:
            state = "ready"
            reason = None
        ready = state == "ready"
        scopes[scope] = {
            "blocker_id": bid,
            "state": state,
            "reason": reason,
            "is_full_scope": is_full,
            "required_days": sorted(expected_days),
            "required_day_count": len(expected_days),
            "admitted_days": admitted if ready else [],
            "verified_subset_days": [] if ready else sorted(admitted_set),
            "missing_required_days": missing_days,
            "unadmitted_days": list(block.get("unadmitted_days") or []),
            "residual_confirmed_trading_gaps": list(
                block.get("residual_confirmed_trading_gaps") or []
            ),
            "source": path.name,
            "source_sha256": sha256_file(path),
            "verified_at": doc.get("generated_at"),
            "requested_outcome_accounting": doc.get("requested_outcome_accounting") or {},
            "failed_checks": doc.get("failed_checks") or [],
            "rehash": {"days_rehashed": rechecked, "mismatches": mismatches},
        }
    blockers: dict[str, dict] = {}
    for scope, entry in scopes.items():
        blockers[entry["blocker_id"]] = {
            "scope": scope,
            "state": entry["state"],
            "is_full_scope": entry["is_full_scope"],
            "required_day_count": entry["required_day_count"],
            "admitted_day_count": len(entry["admitted_days"]),
            "missing_required_day_count": len(entry["missing_required_days"]),
            "admitted_days": entry["admitted_days"],
            "verified_subset_days": entry["verified_subset_days"],
            "unadmitted_days": entry["unadmitted_days"],
            "residual_confirmed_trading_gaps": entry["residual_confirmed_trading_gaps"],
        }
    all_ready = not missing and not stale and all(e["state"] == "ready" for e in scopes.values())
    union = {
        "version": "v0",
        "schema": "atlas.acquisition.admission-union.v0",
        "acquisition_root": str(out_data),
        "generated_at": datetime.now(UTC).isoformat(),
        "parents": parents,
        "readiness_rule": "A scope is ready only when its admitted day set equals every "
        "guarded dev day the scope owes (B1: 19 days of 2025-02, B2: 41 days of 2026-04/05), "
        "each re-hashed, with no failed day check and no residual gap. A --days subset is "
        "reported as verified_subset and admits nothing.",
        "guard": {
            "sealed_prefixes": list(sim.SEALED_PREFIXES),
            "reserved_months": list(sim.RESERVED_MONTHS),
            "note": SEED_DAY_PREFIX_REFUSAL_NOTE,
        },
        "scopes": scopes,
        "blockers": blockers,
        "missing_scopes": missing,
        "stale_scopes": stale,
        "all_scopes_ready": all_ready,
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
            )
        },
    }
    atomic_write_json(out_ev / "acquisition_admission.json", union)
    return union


def stage_verify(args: argparse.Namespace) -> dict:
    """Verify the requested scope, then republish the shared union.

    The per-scope file is the durable record; the union is always REBUILT from
    the per-scope files on disk, never appended to, so a B2 run can never drop
    or overwrite the B1 attestation.

    The result carries BOTH outcomes, because they are different questions:
      days_verified_ok - every day THIS run was asked about passed its checks
      union             - whether any scope has discharged its full obligation
    A one-day --days subset exits 0 on the first and reports false on the second.
    """
    contract = load_contract()
    out_data = Path(args.out_data)
    ctx = build_context(out_data, contract)
    out_ev = _out_evidence(args, out_data)
    scope_doc = verify_scope(args, ctx, out_data)
    atomic_write_json(out_ev / f"acquisition_admission_{args.scope}.json", scope_doc)
    union = publish_admission_union(out_ev, ctx)
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

    # 13. a compact-observed name that comes back with zero bars is a provider
    # revision contradiction: recorded, unresolved, and it blocks admission.
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
            "contradictory_zero_blocks_admission",
            m["status"] == "incomplete"
            and "unresolved_contradictory_zeros" in m["refusal_reasons"],
            json.dumps(m["refusal_reasons"]),
        )
        check(
            "contradictory_zero_is_explicit",
            [c["ticker"] for c in m["compact_crosscheck"]["contradictory_zeros"]] == ["BBB"],
            json.dumps(m["compact_crosscheck"]),
        )
        checks, _ = verify_day(plan, out, "code1", (lo, hi))
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
            "verify_flags_contradiction",
            any("no_unresolved_contradictory_zeros" in n for n in names),
        )
        # the same bytes under a different producer hash must be refused too
        checks2, _ = verify_day(plan, out, "other-code", (lo, hi))
        check(
            "verify_flags_producer_drift",
            any("code_sha256_matches_current" in c["check"] for c in checks2 if not c["ok"]),
        )
        # A manifest declaring a window the contract does not have must be
        # refused. The mutation targets window_et_bounds, the authoritative
        # structured field the validator actually reads; editing the readable
        # window_et string alone would only re-pin an incidental rendering
        # instead of testing the declared window.
        man = load_json(out / "bars" / f"{day}.manifest.json")
        man["source"]["window_et_bounds"] = [570, 960]
        atomic_write_json(out / "bars" / f"{day}.manifest.json", man)
        checks3, _ = verify_day(plan, out, "code1", (lo, hi))
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
        checks4, _ = verify_day(plan, out, "code1", (lo, hi))
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
        checks, res = verify_day(plan, out, "code1", (lo, hi))
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
            ch, _ = verify_day(plan, root, "code1", (lo, hi))
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
            _c, r = verify_day(p, out, "code1", (lo, hi))
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

    # 17b. A BASELINE-ONLY name - in the replaced fallback, absent from the
    # compact source - is still obligated, and a fresh zero for it is a
    # contradiction, not a clean provider zero. This is the BK/ARMN case: 19 of
    # 19 days, 0 of 19 in compact, previously admitted as status=complete with
    # the name simply missing.
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        baseline_only = "BK"
        plan = DayPlan(
            day=day,
            scope="feb2025",
            vintage="v",
            pit_n=2,
            requested=["AAA", baseline_only],
            provider_of={"AAA": "AAA", baseline_only: baseline_only},
            aliases_used={},
            expected_present=["AAA", baseline_only],
            observed_bars={"AAA": 5, baseline_only: 19},
            raw_existing=["AAA"],
            alias_existing=[],
            unavailable={"baseline_only_observed": [baseline_only]},
            baseline={"path": "", "sha256": "b", "standing": "replaced_by_this_acquisition"},
            compact={"path": "", "sha256": None},
        )
        m = acquire_day(
            plan, out, FakeFetcher([{"AAA": [_bar(day, 570, 10.0)]}]), "code1", "cfg1",
            sha256_file(CONTRACT_PATH), sha256_file(ALIAS_MAP_PATH), "pit1", lo, hi, verbose=False,
        )
        check(
            "baseline_only_name_is_not_a_clean_zero",
            m["symbols_zero_bars"] == [baseline_only]
            and [c["ticker"] for c in m["compact_crosscheck"]["contradictory_zeros"]]
            == [baseline_only],
            json.dumps({"zero": m["symbols_zero_bars"],
                        "contra": m["compact_crosscheck"]["contradictory_zeros"]}),
        )
        check(
            "baseline_only_contradiction_blocks_admission",
            m["status"] == "incomplete"
            and "unresolved_contradictory_zeros" in m["refusal_reasons"],
            json.dumps(m["refusal_reasons"]),
        )
        ch, _ = verify_day(plan, out, "code1", (lo, hi))
        names = {c["check"] for c in ch if not c["ok"]}
        check(
            "verify_flags_obligated_baseline_only_name",
            any("all_obligated_symbols_covered" in n for n in names),
            str(sorted(names)),
        )

    # 17c. A symbol whose request never completed is request_failed, never a
    # zero, and blocks admission. Folding it into the zero class asserted a
    # falsehood about names whose request had simply died.
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        plan = DayPlan(
            day=day, scope="feb2025", vintage="v", pit_n=2, requested=["AAA", "BBB"],
            provider_of={"AAA": "AAA", "BBB": "BBB"}, aliases_used={},
            expected_present=["AAA"], observed_bars={"AAA": 5},
            raw_existing=[], alias_existing=[], unavailable={},
            baseline={"path": "", "sha256": "b"}, compact={"path": "", "sha256": None},
        )

        class DeadBatch:
            def fetch(self, symbols, start, end, asof=None):
                raise ProviderError("rest 503 after retries")

        m = acquire_day(
            plan, out, DeadBatch(), "code1", "cfg1", sha256_file(CONTRACT_PATH),
            sha256_file(ALIAS_MAP_PATH), "pit1", lo, hi, verbose=False,
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

    # 18. the union publisher: verifying B1 then B2 keeps BOTH scope entries, a
    # never-verified scope stays visibly unready, and corrupting a day payload
    # invalidates the union at publish time even though its manifest still says
    # status=complete.
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        out_ev = out / "evidence"
        ctx = {
            "out_data": out,
            "contract_sha": sha256_file(CONTRACT_PATH),
            "alias_map_sha": sha256_file(ALIAS_MAP_PATH),
            "code_sha": "code-union",
        }

        class FakePit:
            sha256 = "pit-union"

        ctx["pit"] = FakePit()
        parents = current_parents(ctx)

        def fake_scope_doc(scope, day_ids, ok=True):
            bid = BLOCKER_OF_SCOPE[scope]
            days = []
            for d in day_ids:
                p = out / "bars" / f"{d}.parquet"
                days.append(
                    {
                        "day": d,
                        "file": f"bars/{d}.parquet",
                        "file_sha256": sha256_file(p),
                        "manifest": f"bars/{d}.manifest.json",
                        "status": "complete" if ok else "incomplete",
                        "rows": 1,
                    }
                )
            return {
                "version": "v0",
                "schema": "atlas.acquisition.admission.v0",
                "scope": scope,
                "blocker_id": bid,
                "acquisition_root": str(out),
                "generated_at": "2026-01-01T00:00:00+00:00",
                "parents": parents,
                "blockers": {
                    bid: {
                        "admitted_days": list(day_ids) if ok else [],
                        "unadmitted_days": []
                        if ok
                        else [{"day": d, "reasons": ["x"]} for d in day_ids],
                        "days": days,
                        "residual_confirmed_trading_gaps": [],
                    }
                },
                "requested_outcome_accounting": {
                    "requested_symbols": len(day_ids),
                    "symbols_with_data": len(day_ids),
                    "symbols_zero_bars": 0,
                    "symbols_invalid": 0,
                },
                "all_checks_passed": ok,
                "failed_checks": []
                if ok
                else [{"check": f"{day_ids[0]}:status_complete", "ok": False}],
            }

        # Real payloads for EVERY required day of both scopes, so a full-scope
        # attestation is genuinely dischargeable.
        b1_days = sorted(full_scope_day_set("feb2025"))
        b2_days = sorted(full_scope_day_set("aprmay2026"))
        all_days = b1_days + b2_days
        for d in all_days:
            atomic_write_parquet(pl.DataFrame({"ticker": ["AAA"]}), out / "bars" / f"{d}.parquet")
            atomic_write_json(out / "bars" / f"{d}.manifest.json", {"day": d})
        b1_day, b2_day = b1_days[0], b2_days[0]

        # B1 verified first; the union must NOT yet claim both scopes ready.
        atomic_write_json(
            out_ev / "acquisition_admission_feb2025.json", fake_scope_doc("feb2025", b1_days)
        )
        u1 = publish_admission_union(out_ev, ctx)
        check(
            "union_missing_scope_stays_unready",
            u1["missing_scopes"] == ["aprmay2026"]
            and not u1["all_scopes_ready"]
            and u1["scopes"]["aprmay2026"]["admitted_days"] == [],
            json.dumps({"missing": u1["missing_scopes"], "ready": u1["all_scopes_ready"]}),
        )
        check(
            "union_keeps_b1_entry",
            u1["scopes"]["feb2025"]["state"] == "ready"
            and u1["scopes"]["feb2025"]["admitted_days"] == b1_days
            and u1["scopes"]["feb2025"]["required_day_count"] == len(b1_days),
            json.dumps({k: u1["scopes"]["feb2025"][k] for k in
                        ("state", "required_day_count", "is_full_scope")}),
        )

        # Now verify B2: the union must keep BOTH entries and both fully covered.
        atomic_write_json(
            out_ev / "acquisition_admission_aprmay2026.json",
            fake_scope_doc("aprmay2026", b2_days),
        )
        u2 = publish_admission_union(out_ev, ctx)
        check(
            "union_retains_both_scopes_after_b2",
            u2["all_scopes_ready"]
            and u2["scopes"]["feb2025"]["admitted_days"] == b1_days
            and u2["scopes"]["aprmay2026"]["admitted_days"] == b2_days
            and not u2["missing_scopes"],
            json.dumps({k: len(v["admitted_days"]) for k, v in u2["scopes"].items()}),
        )
        check(
            "union_blockers_carry_both_ids",
            set(u2["blockers"]) == {"B1_raw_2025_02", "B2_raw_roster_2026_04_05"}
            and u2["blockers"]["B1_raw_2025_02"]["admitted_day_count"] == len(b1_days)
            and u2["blockers"]["B2_raw_roster_2026_04_05"]["admitted_day_count"] == len(b2_days),
            json.dumps(sorted(u2["blockers"])),
        )

        # THE READINESS ESCALATION THIS SECTION EXISTS FOR: one verified day per
        # scope - a --days subset - must NOT resolve a full scope. Each scope
        # admits nothing and reports verified_subset with its missing days.
        atomic_write_json(
            out_ev / "acquisition_admission_feb2025.json", fake_scope_doc("feb2025", [b1_day])
        )
        atomic_write_json(
            out_ev / "acquisition_admission_aprmay2026.json",
            fake_scope_doc("aprmay2026", [b2_day]),
        )
        us = publish_admission_union(out_ev, ctx)
        check(
            "one_plus_one_subsets_cannot_resolve_full_scopes",
            not us["all_scopes_ready"]
            and us["scopes"]["feb2025"]["state"] == "verified_subset"
            and us["scopes"]["aprmay2026"]["state"] == "verified_subset",
            json.dumps({k: v["state"] for k, v in us["scopes"].items()}),
        )
        check(
            "subset_scope_admits_nothing",
            us["scopes"]["feb2025"]["admitted_days"] == []
            and us["scopes"]["aprmay2026"]["admitted_days"] == []
            and us["scopes"]["feb2025"]["verified_subset_days"] == [b1_day]
            and us["scopes"]["aprmay2026"]["verified_subset_days"] == [b2_day],
            json.dumps({k: (v["admitted_days"], v["verified_subset_days"])
                        for k, v in us["scopes"].items()}),
        )
        check(
            "subset_names_every_missing_required_day",
            len(us["scopes"]["feb2025"]["missing_required_days"]) == len(b1_days) - 1
            and len(us["scopes"]["aprmay2026"]["missing_required_days"]) == len(b2_days) - 1,
            json.dumps({k: len(v["missing_required_days"]) for k, v in us["scopes"].items()}),
        )
        check(
            "subset_blockers_report_no_admitted_days",
            all(b["admitted_day_count"] == 0 and b["is_full_scope"] is False
                for b in us["blockers"].values()),
            json.dumps({k: (b["admitted_day_count"], b["required_day_count"])
                        for k, b in us["blockers"].items()}),
        )

        # Back to full coverage, then corrupt a day: its manifest still says
        # complete, but the union must re-hash and refuse to admit it.
        atomic_write_json(
            out_ev / "acquisition_admission_feb2025.json", fake_scope_doc("feb2025", b1_days)
        )
        atomic_write_json(
            out_ev / "acquisition_admission_aprmay2026.json",
            fake_scope_doc("aprmay2026", b2_days),
        )
        p = out / "bars" / f"{b2_day}.parquet"
        p.write_bytes(p.read_bytes() + b"corrupt")
        u3 = publish_admission_union(out_ev, ctx)
        mm = u3["scopes"]["aprmay2026"]["rehash"]["mismatches"]
        check(
            "union_rehash_invalidates_corrupt_day",
            not u3["all_scopes_ready"]
            and u3["scopes"]["aprmay2026"]["state"] == "not_ready"
            and u3["scopes"]["aprmay2026"]["admitted_days"] == []
            and any(m["day"] == b2_day for m in mm),
            json.dumps(mm),
        )
        check(
            "corrupt_one_scope_leaves_other_intact",
            u3["scopes"]["feb2025"]["state"] == "ready"
            and u3["scopes"]["feb2025"]["admitted_days"] == b1_days,
            json.dumps(u3["scopes"]["feb2025"]["state"]),
        )

        # An attestation from a different producer identity is stale, never merged.
        other = dict(ctx)
        other["code_sha"] = "code-other"
        u4 = publish_admission_union(out_ev, other)
        check(
            "union_marks_mismatched_parents_stale",
            [x["scope"] for x in u4["stale_scopes"]] == ["feb2025", "aprmay2026"]
            and not u4["all_scopes_ready"],
            json.dumps(u4["stale_scopes"]),
        )

        # A scope whose own checks failed contributes no admitted days.
        atomic_write_parquet(pl.DataFrame({"ticker": ["AAA"]}), out / "bars" / f"{b2_day}.parquet")
        atomic_write_json(
            out_ev / "acquisition_admission_aprmay2026.json",
            fake_scope_doc("aprmay2026", b2_days, ok=False),
        )
        u5 = publish_admission_union(out_ev, ctx)
        check(
            "union_refuses_failed_scope",
            u5["scopes"]["aprmay2026"]["state"] == "not_ready"
            and u5["scopes"]["aprmay2026"]["admitted_days"] == [],
            json.dumps(u5["scopes"]["aprmay2026"]["state"]),
        )

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
    ap.add_argument("--stage", choices=("plan", "acquire", "verify"))
    ap.add_argument("--scope", choices=SCOPES)
    ap.add_argument(
        "--days", default=None, help="comma-separated guarded dev days (default: the whole scope)"
    )
    ap.add_argument(
        "--out-data",
        default=None,
        help="acquisition root (default <DATA>/atlas/acquisition/v3; v0, v1 and v2 are "
        "retained captures that are never the default target)",
    )
    ap.add_argument(
        "--out-evidence", default=None, help="evidence root (default <out-data>/evidence)"
    )
    ap.add_argument("--client", choices=("sdk", "rest"), default="sdk")
    ap.add_argument("--selftest", action="store_true", help="synthetic, offline self-tests")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.stage or not args.scope:
        ap.error("--stage and --scope are required (or use --selftest)")
    data = resolve_data_root()
    if args.out_data is None:
        # v0 is a real fetched canary whose manifest honestly records a failure
        # (an exclusive upper window bound flagged 10 legitimate 16:05 ET bars).
        # It is left exactly as fetched and is NOT the default target: v1 is
        # the first admitted root. Pointing the default at v0 would silently
        # resume into a failed day, and resume would refuse on drift anyway.
        args.out_data = str(data / "atlas" / "acquisition" / "v3")
    if args.stage == "plan":
        doc = stage_plan(args)
        print(
            json.dumps(
                {
                    "days": doc["n_days"],
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
                {"admitted": len(doc["admitted"]), "incomplete": doc["incomplete"]}, indent=1
            )
        )
        return 0 if not doc["incomplete"] else 1
    union = stage_verify(args)
    print(
        json.dumps(
            {
                "verified_scope": union["verified_scope"],
                "is_subset_run": union["is_subset_run"],
                "days_verified_ok": union["days_verified_ok"],
                "verified_days": union["verified_days"],
                "all_scopes_ready": union["all_scopes_ready"],
                "scopes": {
                    s: {
                        "state": e["state"],
                        "admitted": len(e["admitted_days"]),
                        "required": e["required_day_count"],
                    }
                    for s, e in union["scopes"].items()
                },
                "missing_scopes": union["missing_scopes"],
                "stale_scopes": [x["scope"] for x in union["stale_scopes"]],
                "residual_gaps": sum(
                    len(e["residual_confirmed_trading_gaps"]) for e in union["scopes"].values()
                ),
            },
            indent=1,
        )
    )
    # Exit reflects THIS run's day checks. A subset run whose day verified is a
    # success as a verification; all_scopes_ready above is what says whether any
    # scope has actually discharged its full obligation.
    return 0 if union["days_verified_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
