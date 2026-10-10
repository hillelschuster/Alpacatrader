#!/usr/bin/env python3
"""Quote-completeness audit of the STALE / not-acquired legs of the frozen case cohorts.

The frozen sparse h60 repeat trades are audited on as-of NBBO quotes at a 250 ms
arrival clock with a 2 s quote-age ceiling (the primary rule). Some legs come back
STALE or not acquired. This script asks ONE question about those legs and nothing
else: was the STALE / not-acquired verdict an acquisition gap in the ranked quote
cache, or did the market simply not reprice inside the 2 s window?

What it does:

* reads the immutable case legs - the val61 and late143 frontier pairs (per case
  ``entry_status_250`` / ``exit_status_250``) plus the retained repeat-157 quote
  audit rows - and selects, per case leg, the STALE / not-acquired legs only,
  deduplicated by (day, ticker, leg, minute). L1 depth and quote-validity legs are
  deliberately NOT selected: NBBO re-acquisition cannot fix displayed depth;
* re-acquires the missing NBBO window per selected leg with the SAME read-only
  Alpaca REST ``/v2/stocks/quotes`` call the frontier already uses
  (``fetch_missing_legs``, SIP feed, asof = trade day, window = clock-30 s to
  clock + 5 s + 2 s, full pagination), invoked once per leg so raw pages, the
  per-leg manifest and the normalized ``quotes/<day>.parquet`` are all on disk
  after every leg - an interrupt leaves incremental proof, never one end-of-run
  write;
* reuses acquisition evidence that already exists instead of repeating requests:
  the three parent HTTP-200 staleness-probe legs and the eight original frontier
  missing legs are copied into this run's own quotes root (read-only sources, one
  row per (symbol, ts_utc), existing rows KEEP FIRST - no old print is overridden);
* compares, per leg and per arrival latency (250 ms / 1 s / 2 s / 5 s), the as-of
  status on the OLD ranked cache, on the FETCHED supplement and on their UNION:
  actual age, R-firm condition, quote UTC, spread (cost), displayed sizes, and
  whether a regular quote exists after the arrival clock inside the fetched window;
* classifies the fetched event timestamps against the base window as
  ``capture_gap`` (the cache missed prints) or ``identical_full_window`` (the
  window was already complete, so staleness is a market fact).

What it deliberately does NOT do:

* no trade, signal, entry/exit clock, cohort member, fee or cost ladder is
  re-selected, re-timed or re-ranked; nothing here promotes or demotes a view;
* the 2 s primary as-of rule is never widened: a quote older than 2 s is STALE by
  the audit guard even though a firm unchanged NBBO can physically stay live until
  it is replaced - that is stated, never silently relaxed;
* no order, no fill, no fill guarantee, no depth claim: L1 sizes are recorded as
  displayed depth only, and a UNKNOWN capacity outcome is never invented into a
  fill;
* no alpha evidence from a data fetch: an HTTP 200 with zero events is a genuine
  "no quotes received in this window" observation, not proof that no market
  existed; an acquisition gap explains a data UNKNOWN, it does not create edge;
* no CAGR, no bootstrap CI, no annualisation, no regression, no promotion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl
from alpha_open_panel import ROOT, allowed
from alpha_quote_audit import asof_quote, clock_us
from alpha_sparse_execution_frontier import (
    ALPACA_QUOTES_URL,
    FETCH_WINDOW_BEFORE_S,
    PAGE_LIMIT,
    fetch_missing_legs,
    sha256_file,
    write_json,
)
from alpha_sparse_quote_service import (
    QUOTE_COLS,
    QUOTE_SCHEMA,
    UNIT_EPOCH,
    day_quote_path,
    first_regular_at_or_after,
)

LANE = Path(os.environ.get("ALPHA_OPEN_SEARCH_V1", Path.home() / "alpha-data" / "open-search-v1"))
DATA_ROOT = ROOT / "data"
ENV_PATH = ROOT / ".env"

# ---- immutable case inputs (read-only) --------------------------------------
VAL_PAIRS = LANE / "sparse_execution_frontier" / "val61" / "pairs.parquet"
LATE_PAIRS = LANE / "sparse_execution_frontier" / "late143" / "pairs.parquet"
REPEAT_TRADES = LANE / "learned_sparse_daily" / "quote_audit" / "trades_repeat_h60_100bps.parquet"
REPEAT_INTENTS = LANE / "learned_sparse_daily" / "quote_audit" / "intents_repeat_h60_100bps.json"

# ---- caches that already hold acquisition evidence (read-only originals) ----
PROBE_ROOT = LANE / "quote_staleness_probe"  # 3 parent HTTP-200 stale EXIT legs
PROBE_CHECKS = PROBE_ROOT / "checks.json"
FRONTIER_FETCH_ROOT = LANE / "sparse_execution_frontier" / "fetched_quotes"
FRONTIER_FETCH_MANIFEST = LANE / "sparse_execution_frontier" / "missing_requests_manifest.json"

# ---- frozen acquisition constants (identical to the frontier producer) -------
BASE_LATENCY_MS = 250
ARRIVAL_LATENCIES_MS = (250, 1000, 2000, 5000)
MAX_AGE_S = 2.0  # primary as-of quote-age ceiling, never widened
FETCH_MAX_LATENCY_MS = 5000
REQ_SLEEP_S = 0.35
FETCH_WINDOW_AFTER_S = FETCH_MAX_LATENCY_MS / 1000.0 + 2.0  # 7.0 s, helper-derived

# ---- leg selection rules -----------------------------------------------------
STALE_OR_MISSING_STATUSES = {
    "stale_quote",
    "quote_not_acquired",
    "missing_symbol_stream",
    "missing_day_file",
    "no_prior_quote",
}
# Not selected: NBBO re-acquisition cannot measure deeper-book capacity.
# Cached quote-validity metadata is outside this staleness/acquisition audit.
EXCLUDED_STATUSES = {
    "unknown_top_of_book_capacity": (
        "L1 depth shortfall; the deeper book and other venues are unmeasured, "
        "so NBBO cannot fix it"
    ),
    "unknown_l1_capacity": "L1 depth shortfall; not an acquisition gap",
    "unknown_l1_capacity_entry": "L1 depth shortfall; not an acquisition gap",
    "unknown_l1_capacity_exit": "L1 depth shortfall; not an acquisition gap",
    "unknown_l1_capacity_entry_exit": "L1 depth shortfall; not an acquisition gap",
    "invalid_or_crossed_quote": "cached quote-validity metadata is outside this audit",
    "crossed_quote": "cached quote-validity metadata is outside this audit",
    "nonregular_quote": "cached firmness metadata is outside this audit",
    "unknown_proxy_execution": "minute-open proxy leg with no quote clock",
    "known_no_order_min_capital": "known no-order (cash unfilled); no quote to acquire",
    "unknown_position_action": "position verification unknown; not an acquisition gap",
    "unknown_partial_or_unexplained_position": (
        "position verification unknown; not an acquisition gap"
    ),
}

# Parent adversarial probe: three late EXIT legs HTTP-200 fetched that REMAIN
# stale because the actual prior regular quote was older than 2 s. The ages below
# are the parent-reported proof values; this run recomputes them from the ranked
# cache and reports the comparison instead of repeating the API requests.
PARENT_PROBE_AGE_PROOF = (
    {
        "day": "2025-02-19",
        "ticker": "OSRH",
        "leg": "exit",
        "minute": 705,
        "parent_reported_age_s": 2.18725,
    },
    {
        "day": "2025-02-20",
        "ticker": "XOS",
        "leg": "exit",
        "minute": 680,
        "parent_reported_age_s": 2.577835,
    },
    {
        "day": "2025-03-04",
        "ticker": "FRGT",
        "leg": "exit",
        "minute": 730,
        "parent_reported_age_s": 2.831181,
    },
)


# --------------------------------------------------------------------------- IO
def _iso_us(us: int) -> str:
    return datetime.fromtimestamp(us / 1_000_000, tz=UTC).isoformat().replace("+00:00", "Z")


def leg_key(day: str, ticker: str, leg: str, minute: int) -> str:
    """Identical to the frontier fetch cache's per-leg raw directory key."""
    return f"{day}__{ticker}__{leg}_m{minute}"


def leg_tuple(record: dict) -> tuple[str, str, str, int]:
    return (record["day"], record["ticker"], record["leg"], int(record["minute"]))


def _read_parquet(path: Path) -> pl.DataFrame:
    return pl.read_parquet(path)


def merge_keep_first(existing: pl.DataFrame | None, incoming: pl.DataFrame) -> pl.DataFrame:
    """One row per (symbol, ts_utc); the first frame's print wins, always."""
    frames = [f for f in (existing, incoming) if f is not None and f.height]
    if not frames:
        return pl.DataFrame(schema=QUOTE_SCHEMA)
    merged = pl.concat(frames, how="vertical")
    return merged.unique(subset=["symbol", "ts_utc"], keep="first").sort("symbol", "ts_utc")


# ------------------------------------------------------------------ case legs
def _case_row(cohort: str, record: dict, status_source: str) -> dict:
    return {
        "cohort": cohort,
        "day": record["day"],
        "ticker": record["ticker"],
        "entry_et": record["entry_et"],
        "exit_day": record.get("exit_day") or record["day"],
        "exit_et": record["exit_et"],
        "entry_status": record["entry_status_250"],
        "exit_status": record["exit_status_250"],
        "status_source": status_source,
    }


def _require(path: Path, label: str) -> Path:
    if not path.exists():
        raise SystemExit(f"{label} not found: {path}")
    return path


def pair_case_rows() -> tuple[list[dict], dict[str, dict]]:
    """Case legs and recorded per-leg statuses from the immutable frontier pairs."""
    rows: list[dict] = []
    by_key: dict[str, dict] = {}
    for cohort, path in (("val61", VAL_PAIRS), ("late143", LATE_PAIRS)):
        _require(path, f"[{cohort}] frontier pairs")
        for record in _read_parquet(path).iter_rows(named=True):
            if record["entry_et"] is None or record["exit_et"] is None:
                continue
            row = _case_row(cohort, record, "frontier_pairs_parquet")
            rows.append(row)
            by_key[
                f"{row['cohort']}|{row['day']}|{row['ticker']}|{row['entry_et']}|{row['exit_et']}"
            ] = row
    return rows, by_key


class DaySymbolFrames:
    """Per-(day, ticker) frames from the ranked cache and this run's own quotes root.

    Both sources are read one symbol at a time; only the most recent (day, ticker)
    pair stays resident, so a full-cohort pass never concatenates the corpus.
    """

    def __init__(self, data_root: Path, quotes_root: Path) -> None:
        self.data_root = data_root
        self.quotes_root = quotes_root
        self._cache: dict[tuple[str, str, str], pl.DataFrame] = {}

    def _load(self, source: str, day: str, ticker: str) -> pl.DataFrame:
        if source == "base":
            path = day_quote_path(self.data_root, day)
        else:
            path = self.quotes_root / f"{day}.parquet"
        if not path.exists():
            return pl.DataFrame(schema=QUOTE_SCHEMA)
        frame = (
            pl.scan_parquet(path)
            .filter(pl.col("symbol") == ticker)
            .select(list(QUOTE_COLS))
            .collect()
            .sort("ts_utc")
        )
        return frame

    def frame(self, source: str, day: str, ticker: str) -> pl.DataFrame:
        key = (source, day, ticker)
        if key not in self._cache:
            self._cache = {k: v for k, v in self._cache.items() if k[1] == day}
            self._cache[key] = self._load(source, day, ticker)
        return self._cache[key]

    def union(self, day: str, ticker: str) -> pl.DataFrame:
        """Ranked cache first (primary prints), own-root supplement appended."""
        return merge_keep_first(self.frame("base", day, ticker), self.frame("own", day, ticker))


def leg_status(
    frame: pl.DataFrame, day: str, minute: int, latency_ms: int = BASE_LATENCY_MS
) -> str:
    """Primary as-of rule (250 ms arrival, 2 s age ceiling, R-only) for one leg."""
    if frame.is_empty():
        return "quote_not_acquired"
    return asof_view(frame, clock_us(day, minute, latency_ms), day)["status"]


def case_days(pair_rows: list[dict]) -> set[str]:
    """Every calendar day this audit may open a quote file for, before any read.

    Collected from the immutable case rows only, so the protected-day guard runs
    BEFORE the first ranked-cache read (the guard is mandatory, never a post-hoc
    check).
    """
    days = {row["day"] for row in pair_rows} | {row["exit_day"] for row in pair_rows}
    if REPEAT_TRADES.exists():
        meta = _read_parquet(REPEAT_TRADES).select(["day", "exit_day"]).unique()
        for day, exit_day in meta.iter_rows():
            days.add(day)
            days.add(exit_day or day)
    return days


def repeat_case_rows(late_by_key: dict[str, dict], frames: DaySymbolFrames) -> list[dict]:
    """Retained repeat-157 quote-audit rows, per-leg fresh-quote status.

    Legs shared with the immutable late143 pairs keep the recorded pair status;
    repeat-only trades are re-derived from the ranked cache under the SAME primary
    rule (250 ms arrival, 2 s age ceiling, R-only). No signal, clock or fee is
    touched: this only labels which legs lack a fresh quote.
    """
    _require(REPEAT_TRADES, "retained repeat trades")
    rows: list[dict] = []
    for record in _read_parquet(REPEAT_TRADES).iter_rows(named=True):
        if record["entry_et"] is None or record["exit_et"] is None:
            continue
        key = f"late143|{record['day']}|{record['ticker']}|{record['entry_et']}|{record['exit_et']}"
        shared = late_by_key.get(key)
        if shared is not None:
            rows.append(
                _case_row(
                    "repeat157",
                    {
                        **record,
                        "entry_status_250": shared["entry_status"],
                        "exit_status_250": shared["exit_status"],
                    },
                    "frontier_pairs_parquet(shared_with_late143)",
                )
            )
            continue
        exit_day = record.get("exit_day") or record["day"]
        entry_status = leg_status(
            frames.frame("base", record["day"], record["ticker"]), record["day"], record["entry_et"]
        )
        exit_status = leg_status(
            frames.frame("base", exit_day, record["ticker"]), exit_day, record["exit_et"]
        )
        rows.append(
            _case_row(
                "repeat157",
                {**record, "entry_status_250": entry_status, "exit_status_250": exit_status},
                "ranked_cache_primary_rule_250ms_2s",
            )
        )
    return rows


def select_legs(rows: list[dict]) -> list[dict]:
    """STALE / not-acquired legs only, deduplicated by (day, ticker, leg, minute)."""
    legs: dict[tuple[str, str, str, int], dict] = {}
    for row in rows:
        for name, day, minute, status in (
            ("entry", row["day"], row["entry_et"], row["entry_status"]),
            ("exit", row["exit_day"], row["exit_et"], row["exit_status"]),
        ):
            if minute is None or status is None or status not in STALE_OR_MISSING_STATUSES:
                continue
            key = (day, row["ticker"], name, int(minute))
            leg = legs.setdefault(
                key,
                {
                    "day": day,
                    "ticker": row["ticker"],
                    "leg": name,
                    "minute": int(minute),
                    "case_status": status,
                    "case_sources": [],
                },
            )
            leg["case_sources"].append(f"{row['cohort']}:{status}")
    return [legs[key] for key in sorted(legs)]


# ------------------------------------------------------- cached acquisition evidence
def source_manifest_records() -> dict[tuple[str, str, str, int], dict]:
    """Per-leg acquisition evidence that already exists, keyed by (day, ticker, leg, minute).

    Provenance:
    * quote_staleness_probe: three parent adversarial legs, HTTP 200, remain STALE.
    * sparse_execution_frontier fetched cache: the eight original missing legs.
    No API request is issued for any of them; the evidence is copied into this
    run's own quotes root (read-only originals).
    """
    evidence: dict[tuple[str, str, str, int], dict] = {}
    for label, path, root in (
        ("parent_probe_cache", PROBE_CHECKS, PROBE_ROOT),
        ("frontier_fetch_cache", FRONTIER_FETCH_MANIFEST, FRONTIER_FETCH_ROOT),
    ):
        if not path.exists():
            continue
        payload = json.loads(path.read_text())
        records = payload.get("manifest") or payload.get("requests") or []
        for record in records:
            if not all(k in record for k in ("day", "ticker", "leg", "minute")):
                continue
            evidence[leg_tuple(record)] = {
                "mode": label,
                "record": record,
                "source_root": root,
                "source_manifest": path,
            }
    return evidence


def cached_copy_days(evidence: dict[tuple[str, str, str, int], dict]) -> list[tuple[Path, str]]:
    """(source quotes root, day) pairs holding cached acquisition evidence.

    Whole-day copies: the new frequency producer must be able to read ONE combined
    own-root quotes directory that contains the full four frontier pairs, not just
    the two legs this audit selected for them.
    """
    seen: dict[Path, set[str]] = {}
    for key, item in sorted(evidence.items()):
        root = item["source_root"] / "quotes"
        seen.setdefault(root, set()).add(key[0])
    return [(root, day) for root in seen for day in sorted(seen[root])]


def copy_cached_day(source_root: Path, day: str, quotes_root: Path) -> dict:
    """Copy one day of cached supplement rows into the own root, KEEP FIRST.

    Existing own-root rows (an earlier incremental run, or a row copied from the
    other source cache) win over the incoming copy, so no old print or its
    metadata is ever silently replaced.
    """
    source = source_root / f"{day}.parquet"
    target = quotes_root / f"{day}.parquet"
    if not source.exists():
        return {"day": day, "source": str(source), "status": "source_day_missing"}
    incoming = _read_parquet(source).select(list(QUOTE_COLS))
    existing = _read_parquet(target).select(list(QUOTE_COLS)) if target.exists() else None
    before = existing.height if existing is not None else 0
    merged = merge_keep_first(existing, incoming)
    quotes_root.mkdir(parents=True, exist_ok=True)
    merged.write_parquet(target)
    added = merged.height - before
    return {
        "day": day,
        "source": str(source),
        "source_sha256": sha256_file(source),
        "status": "copied",
        "source_rows": incoming.height,
        "own_rows_before": before,
        "own_rows_after": merged.height,
        "rows_added": added,
        "duplicate_rows_dropped": incoming.height - added,
        "keep_first": "existing own-root rows win; no old print is overridden",
    }


# ------------------------------------------------------------------ acquisition
def acquire_legs(
    legs: list[dict],
    evidence: dict[tuple[str, str, str, int], dict],
    *,
    out: Path,
    env_path: Path,
    resume: bool,
) -> tuple[list[dict], int]:
    """Per-leg acquisition with an incremental, append-only request manifest.

    Returns ``(records, requests_issued)``.

    ``fetch_missing_legs`` writes the verbatim raw pages per page and the
    normalized day parquet after each leg's batch, so it is invoked once per leg:
    an interrupt leaves the raw pages, the normalized rows and the manifest line
    of every completed leg on disk. Cached evidence legs issue no API request at
    all; ``--resume`` skips legs already recorded in the manifest, while a fresh
    run restarts the manifest for this run only (the normalized quotes root keeps
    every print it already holds and is never truncated).
    """
    manifest_dir = out / "manifest"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "request_manifest.jsonl"
    done: dict[tuple[str, str, str, int], dict] = {}
    if resume and manifest_path.exists():
        for line in manifest_path.read_text().splitlines():
            if line.strip():
                record = json.loads(line)
                done[leg_tuple(record)] = record
    records: list[dict] = []
    issued = 0
    with manifest_path.open("w" if not resume else "a") as handle:
        for leg in legs:
            key = (leg["day"], leg["ticker"], leg["leg"], leg["minute"])
            cached = evidence.get(key)
            if cached is not None:
                prior = cached["record"]
                record = {
                    "day": leg["day"],
                    "ticker": leg["ticker"],
                    "leg": leg["leg"],
                    "minute": leg["minute"],
                    "clock_us": prior["clock_us"],
                    "endpoint": prior["endpoint"],
                    "asof": prior["asof"],
                    "feed": prior["feed"],
                    "limit": prior["limit"],
                    "window_utc": prior["window_utc"],
                    "window_before_s": prior["window_before_s"],
                    "window_after_s": prior["window_after_s"],
                    "max_latency_ms": prior["max_latency_ms"],
                    "mode": cached["mode"],
                    "status": "cached_http200_reused",
                    "http_status": prior.get("http_status"),
                    "pages": prior.get("pages"),
                    "page_events": prior.get("page_events"),
                    "quote_events": prior.get("quote_events"),
                    "error": prior.get("error"),
                    "api_request_issued": False,
                    "source_root": str(cached["source_root"]),
                    "source_manifest": str(cached["source_manifest"]),
                    "raw_dir_original": str(cached["source_root"] / "raw" / leg_key(*key)),
                    "reused_at": datetime.now(UTC).isoformat(),
                }
            elif key in done:
                record = {**done[key], "resumed": True}
            else:
                fetched = fetch_missing_legs(
                    [leg], out, env_path, FETCH_MAX_LATENCY_MS, REQ_SLEEP_S
                )
                record = {
                    **fetched[0],
                    "mode": "api_request",
                    "raw_dir": str(out / "raw" / leg_key(*key)),
                    "requested_at": datetime.now(UTC).isoformat(),
                }
                issued += 1
            records.append(record)
            if key not in done:
                handle.write(json.dumps(record, default=str) + "\n")
                handle.flush()
            print(
                f"[acquire] {leg['day']} {leg['ticker']} {leg['leg']} m{leg['minute']} "
                f"mode={record['mode']} status={record['status']} "
                f"{'(resumed, no new request) ' if record.get('resumed') else ''}"
                f"http={record.get('http_status')} events={record.get('quote_events')}",
                flush=True,
            )
    return records, issued


# ----------------------------------------------------------------------- audit
def asof_view(frame: pl.DataFrame, target_us: int, day: str, max_age_s: float = MAX_AGE_S) -> dict:
    """As-of status plus the prior print's detail, one frame of one symbol.

    The status is taken from ``alpha_quote_audit.asof_quote`` itself, so the
    primary rule (strictly prior print, 2 s age ceiling, 0 < bid <= ask, all-R
    conditions) is identical to the audit that produced the case statuses. The
    detail block is always populated when a prior print exists - including when
    the status is STALE - because the actual age, the R-firm condition, the quote
    UTC and the displayed sizes are the evidence this audit has to report.
    """
    view = {
        "target_us": int(target_us),
        "target_utc": _iso_us(int(target_us)),
        "max_age_s": max_age_s,
        "status": "quote_not_acquired"
        if frame.is_empty()
        else asof_quote(frame, target_us, day, max_age_s)[1],
    }
    if frame.is_empty():
        view["quote"] = None
        return view
    stamps = frame["ts_utc"].cast(pl.Int64).to_numpy()
    index = int(np.searchsorted(stamps, target_us, side="right")) - 1
    if index < 0:
        view["quote"] = None
        return view
    row = frame.row(index, named=True)
    conditions = list(row["conditions"] or [])
    bid, ask = float(row["bid_price"]), float(row["ask_price"])
    multiplier = 100 if day < UNIT_EPOCH else 1
    view["quote"] = {
        "quote_us": int(stamps[index]),
        "quote_utc": _iso_us(int(stamps[index])),
        "bid": bid,
        "ask": ask,
        "spread_bps": float((ask / bid - 1) * 10_000) if bid else None,
        "bid_shares": float(row["bid_size"]) * multiplier,
        "ask_shares": float(row["ask_size"]) * multiplier,
        "bid_exchange": row["bid_exchange"],
        "ask_exchange": row["ask_exchange"],
        "conditions": conditions,
        "regular": bool(conditions) and all(c == "R" for c in conditions),
        "age_s": float((target_us - stamps[index]) / 1_000_000),
    }
    return view


def future_quote(union_frame: pl.DataFrame, target_us: int, window_end_us: int) -> dict | None:
    """First R-only quote at/after the arrival clock inside the fetched window."""
    if union_frame.is_empty():
        return None
    window = union_frame.filter(
        (pl.col("ts_utc").cast(pl.Int64) >= target_us)
        & (pl.col("ts_utc").cast(pl.Int64) <= window_end_us)
    ).sort("ts_utc")
    if window.is_empty():
        return None
    quote, status = first_regular_at_or_after(window, target_us)
    if quote is None:
        return None
    return {
        "quote_us": int(quote["quote_us"]),
        "quote_utc": _iso_us(int(quote["quote_us"])),
        "delay_after_arrival_s": float(quote["delay_s"]),
        "bid": quote["bid"],
        "ask": quote["ask"],
        "spread_bps": quote["spread_bps"],
        "bid_shares": quote["bid_shares"],
        "ask_shares": quote["ask_shares"],
        "status": status,
    }


def window_events(frame: pl.DataFrame, lo_us: int, hi_us: int) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    return frame.filter(
        (pl.col("ts_utc").cast(pl.Int64) >= lo_us) & (pl.col("ts_utc").cast(pl.Int64) < hi_us)
    )


def audit_leg(leg: dict, frames: DaySymbolFrames, acquisition: dict | None) -> dict:
    """Old (ranked cache) vs fetched (own root) vs union at every arrival latency."""
    day, ticker, minute = leg["day"], leg["ticker"], leg["minute"]
    base = frames.frame("base", day, ticker)
    fetched = frames.frame("own", day, ticker)
    union = frames.union(day, ticker)
    clock_base = clock_us(day, minute, 0)
    window_lo = clock_base - int(FETCH_WINDOW_BEFORE_S * 1_000_000)
    window_hi = clock_base + int(FETCH_WINDOW_AFTER_S * 1_000_000)

    base_window = window_events(base, window_lo, window_hi)
    fetched_window = window_events(fetched, window_lo, window_hi)
    union_window = window_events(union, window_lo, window_hi)
    base_ts = set(base_window["ts_utc"].cast(pl.Int64).to_list()) if base_window.height else set()
    fetched_ts = (
        set(fetched_window["ts_utc"].cast(pl.Int64).to_list()) if fetched_window.height else set()
    )
    new_events = len(fetched_ts - base_ts)

    if not fetched_window.height:
        capture = "no_quotes_in_api_window"
    elif not base_window.height and new_events:
        capture = "capture_gap_full"
    elif new_events:
        capture = "capture_gap_partial"
    else:
        capture = "identical_full_window"

    latencies: dict[str, dict] = {}
    for latency in ARRIVAL_LATENCIES_MS:
        target = clock_us(day, minute, latency)
        latencies[str(latency)] = {
            "old": asof_view(base, target, day),
            "fetched": asof_view(fetched, target, day),
            "union": asof_view(union, target, day),
        }

    future = future_quote(union, clock_us(day, minute, BASE_LATENCY_MS), window_hi)
    return {
        "day": day,
        "ticker": ticker,
        "leg": leg["leg"],
        "minute": minute,
        "clock_us": clock_base,
        "clock_utc": _iso_us(clock_base),
        "window_utc": [_iso_us(window_lo), _iso_us(window_hi)],
        "window_before_s": FETCH_WINDOW_BEFORE_S,
        "window_after_s": FETCH_WINDOW_AFTER_S,
        "case_sources": leg["case_sources"],
        "case_status": leg["case_status"],
        "acquisition": acquisition,
        "base_window_events": base_window.height,
        "fetched_window_events": fetched_window.height,
        "union_window_events": union_window.height,
        "new_events_in_window": new_events,
        "capture_classification": capture,
        "capture_gap": capture.startswith("capture_gap"),
        "old_250_status": latencies[str(BASE_LATENCY_MS)]["old"]["status"],
        "union_250_status": latencies[str(BASE_LATENCY_MS)]["union"]["status"],
        "status_changed_by_acquisition": (
            latencies[str(BASE_LATENCY_MS)]["old"]["status"]
            != latencies[str(BASE_LATENCY_MS)]["union"]["status"]
        ),
        "latencies": latencies,
        "future_quote_in_window": future,
        "no_future_quote_in_window": future is None,
    }


def parent_age_proof(frames: DaySymbolFrames) -> list[dict]:
    """Recompute the three parent probe legs' actual prior-quote age from the ranked cache.

    The parent reported these legs as HTTP-200 acquired yet still STALE at the 2 s
    rule, with actual prior regular ages 2.18725 / 2.577835 / 2.831181 s. This run
    recomputes the same ages from the ranked cache and reports both values; it
    never repeats the API request that already proved the market fact.
    """
    proof = []
    for item in PARENT_PROBE_AGE_PROOF:
        day, ticker, minute = item["day"], item["ticker"], item["minute"]
        view = asof_view(
            frames.frame("base", day, ticker), clock_us(day, minute, BASE_LATENCY_MS), day
        )
        quote = view["quote"] or {}
        recomputed = quote.get("age_s")
        reported = item["parent_reported_age_s"]
        proof.append(
            {
                "day": day,
                "ticker": ticker,
                "leg": item["leg"],
                "minute": minute,
                "parent_reported_age_s": reported,
                "audit_recomputed_age_s": recomputed,
                "matches_parent_within_1ms": (
                    recomputed is not None and abs(recomputed - reported) < 0.001
                ),
                "old_250_status": view["status"],
                "conditions": quote.get("conditions"),
                "quote_utc": quote.get("quote_utc"),
                "source": "parent context (quote_staleness_probe) vs ranked cache recomputation",
            }
        )
    return proof


def _status_rollup(audited: list[dict]) -> dict:
    rollup: dict[str, dict[str, dict[str, int]]] = {}
    for latency in ARRIVAL_LATENCIES_MS:
        views = {}
        for source in ("old", "fetched", "union"):
            counts = Counter(leg["latencies"][str(latency)][source]["status"] for leg in audited)
            views[source] = dict(sorted(counts.items()))
        rollup[str(latency)] = views
    return rollup


def _cohort_bounds(audited: list[dict]) -> dict:
    bounds = {}
    for cohort in ("val61", "late143", "repeat157"):
        legs = [
            leg for leg in audited if any(s.startswith(f"{cohort}:") for s in leg["case_sources"])
        ]
        gaps = [leg for leg in legs if leg["capture_gap"]]
        unknown = [
            leg
            for leg in legs
            if leg["acquisition"] is not None
            and str(leg["acquisition"].get("status", "")).startswith("unknown")
        ]
        bounds[cohort] = {
            "legs_selected": len(legs),
            "legs_capture_gap": len(gaps),
            "legs_identical_full_window": sum(
                1 for leg in legs if leg["capture_classification"] == "identical_full_window"
            ),
            "legs_no_quotes_in_api_window": sum(
                1 for leg in legs if leg["capture_classification"] == "no_quotes_in_api_window"
            ),
            "legs_acquisition_unknown": len(unknown),
            "original_signal_support": (
                "unchanged" if not gaps and not unknown else "bounded_not_promoted"
            ),
            "note": (
                "no acquisition gap was found, so the ranked cache already held the full "
                "window and every STALE verdict stands as a market fact; the cohort's fresh/"
                "capacity support state is unchanged by this audit"
                if not gaps and not unknown
                else "this audit explains data UNKNOWNs; it never promotes a view and never "
                "converts an UNKNOWN outcome into an edge or a zero"
            ),
        }
    return bounds


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", type=Path, default=LANE / "quote_completeness_audit")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="skip legs already recorded in the per-leg request manifest",
    )
    parser.add_argument("--data", type=Path, default=DATA_ROOT, help="ranked SIP quote cache root")
    parser.add_argument(
        "--env",
        type=Path,
        default=ENV_PATH,
        help="dotenv with ALPACA_API_KEY/ALPACA_SECRET_KEY (never echoed, never stored)",
    )
    args = parser.parse_args(argv)

    t0 = time.time()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    quotes_root = out / "quotes"
    frames = DaySymbolFrames(args.data, quotes_root)

    # ---- immutable case legs -------------------------------------------------
    pair_rows, late_by_key = pair_case_rows()

    # ---- mandatory protected-day guard, BEFORE any quote file is opened ------
    guarded_days = sorted(case_days(pair_rows))
    bad_days = [day for day in guarded_days if not allowed(day)]
    if bad_days:
        raise SystemExit(f"protected quote read refused: {bad_days[:5]}")

    repeat_rows = repeat_case_rows(late_by_key, frames)
    case_rows = pair_rows + repeat_rows
    legs = select_legs(case_rows)
    input_paths = {
        "val61_pairs": VAL_PAIRS,
        "late143_pairs": LATE_PAIRS,
        "repeat157_trades": REPEAT_TRADES,
        "repeat157_intents": REPEAT_INTENTS,
        "parent_probe_checks": PROBE_CHECKS,
        "frontier_fetch_manifest": FRONTIER_FETCH_MANIFEST,
    }
    inputs = {
        name: {
            "path": str(path),
            "sha256": sha256_file(path),
            "exists": path.exists(),
        }
        for name, path in input_paths.items()
        if path.exists()
    }

    # ---- frozen selection manifest, written before any cache copy or request --
    selection = {
        "kind": "alpha_quote_completeness_selection",
        "goal_completion": False,
        "frozen_at": datetime.now(UTC).isoformat(),
        "frozen_before_new_network_acquisition": True,
        "legs_selected": len(legs),
        "legs": legs,
        "selection_sha256": hashlib.sha256(
            json.dumps(legs, sort_keys=True, default=str).encode()
        ).hexdigest(),
        "rules": {
            "selected_statuses": sorted(STALE_OR_MISSING_STATUSES),
            "excluded_statuses": EXCLUDED_STATUSES,
            "dedup_key": "(day, ticker, leg, minute)",
            "primary_rule": "as-of NBBO at clock+250ms, max age 2s, R-only",
        },
        "protected_days_in_inputs": bad_days,
        "protected_reads": False,
        "inputs": inputs,
    }
    write_json(out / "manifest" / "selection.json", selection)
    manifest_path = out / "manifest" / "request_manifest.jsonl"
    if args.resume and manifest_path.exists():
        frozen = json.loads((out / "manifest" / "selection.json").read_text())
        if frozen.get("selection_sha256") != selection["selection_sha256"]:
            raise SystemExit(
                "frozen selection does not match the recomputed selection; refusing to resume"
            )

    # ---- cached acquisition evidence is copied AFTER the frozen selection -----
    evidence = source_manifest_records()
    copy_plan = cached_copy_days(evidence)
    for _root, day in copy_plan:
        if not allowed(day):
            raise SystemExit(f"protected quote read refused: {day}")
    copies = [copy_cached_day(root, day, quotes_root) for root, day in copy_plan]
    cached_days = sorted({item["day"] for item in copies})
    selected_days = sorted({leg["day"] for leg in legs})

    # ---- per-leg acquisition (incremental) -----------------------------------
    n_cached_selected = sum(leg_tuple(leg) in evidence for leg in legs)
    print(
        f"[quote-completeness] {len(legs)} STALE/missing legs over "
        f"{len(selected_days)} days; {n_cached_selected} selected legs covered by "
        "cached evidence (no API request)",
        flush=True,
    )
    acquisitions, requests_issued = acquire_legs(
        legs, evidence, out=out, env_path=args.env, resume=args.resume
    )
    by_leg = {leg_tuple(record): record for record in acquisitions}

    # ---- comparison audit ----------------------------------------------------
    audited = [audit_leg(leg, frames, by_leg.get(leg_tuple(leg))) for leg in legs]
    for leg in audited:
        print(
            f"[audit] {leg['day']} {leg['ticker']} {leg['leg']} m{leg['minute']} "
            f"case={leg['case_status']} old250={leg['old_250_status']} "
            f"union250={leg['union_250_status']} capture={leg['capture_classification']} "
            f"new_events={leg['new_events_in_window']}",
            flush=True,
        )

    classification = Counter(leg["capture_classification"] for leg in audited)
    acquisition_modes = Counter(
        (leg["acquisition"] or {}).get("mode", "not_acquired") for leg in audited
    )
    acquisition_statuses = Counter(
        str((leg["acquisition"] or {}).get("status", "not_acquired")) for leg in audited
    )
    pending = [
        {"day": leg["day"], "ticker": leg["ticker"], "leg": leg["leg"], "minute": leg["minute"]}
        for leg in audited
        if str((leg["acquisition"] or {}).get("status", "")).startswith("unknown")
    ]
    months = sorted({leg["day"][:7] for leg in audited})
    audit = {
        "kind": "alpha_quote_completeness_audit",
        "goal_completion": False,
        "status": "DATA-COMPLETENESS-AUDIT; no alpha evidence; no financial promotion",
        "purpose": (
            "Was each STALE / not-acquired case leg an acquisition gap in the ranked "
            "quote cache, or a market that did not reprice inside the 2 s window?"
        ),
        "generated_at": datetime.now(UTC).isoformat(),
        "runtime_s": round(time.time() - t0, 1),
        "argv": list(argv if argv is not None else []),
        "out_root": str(out),
        "frozen": {
            "base_latency_ms": BASE_LATENCY_MS,
            "arrival_latencies_ms": list(ARRIVAL_LATENCIES_MS),
            "max_quote_age_s": MAX_AGE_S,
            "age_2s_rule": (
                "the 2 s ceiling is a strict primary audit guard, not a physical expiry: "
                "a firm unchanged NBBO may remain live until it is replaced, so a >2 s "
                "quote stays STALE while remaining a real quote"
            ),
            "fetch_max_latency_ms": FETCH_MAX_LATENCY_MS,
            "window_before_s": FETCH_WINDOW_BEFORE_S,
            "window_after_s": FETCH_WINDOW_AFTER_S,
            "req_sleep_s": REQ_SLEEP_S,
            "endpoint": ALPACA_QUOTES_URL,
            "feed": "sip",
            "asof": "trade day",
            "pagination_limit": PAGE_LIMIT,
            "credentials_stored": False,
            "orders_or_account_writes": False,
            "depth_excluded": (
                "L1 depth/capacity legs are never selected; NBBO cannot fix displayed depth"
            ),
            "size_unit_rule": "sizes x100 before 2025-11-03 (round lots), single shares after",
        },
        "protected_reads": False,
        "protected_days_checked": selected_days + cached_days,
        "selection": {
            "legs_selected": len(legs),
            "days": len({leg["day"] for leg in legs}),
            "days_list": selected_days,
            "months": months,
            "by_leg": dict(Counter(leg["leg"] for leg in legs)),
            "by_source": dict(
                Counter(s.split(":")[0] for leg in legs for s in leg["case_sources"])
            ),
            "selection_sha256": selection["selection_sha256"],
            "frozen_before_late_access": True,
        },
        "inputs": inputs,
        "cache_reuse": {
            "cached_evidence_legs": n_cached_selected,
            "available_evidence_legs": len(evidence),
            "mode_counts": dict(sorted(acquisition_modes.items())),
            "api_requests_issued": requests_issued,
            "copies": copies,
            "note": (
                "the three parent staleness-probe legs and the eight original frontier "
                "missing legs are reused from read-only caches; no API request repeats a "
                "known fact, and the whole-day copies let one combined own-root quotes "
                "directory carry the full four frontier pairs"
            ),
        },
        "parent_age_proof": parent_age_proof(frames),
        "acquisition": {
            "legs": len(audited),
            "status_counts": dict(sorted(acquisition_statuses.items())),
            "http_status_counts": dict(
                sorted(
                    Counter(
                        str((leg["acquisition"] or {}).get("http_status")) for leg in audited
                    ).items()
                )
            ),
            "no_quotes_in_api_window": sum(
                1 for leg in audited if leg["capture_classification"] == "no_quotes_in_api_window"
            ),
            "note": (
                "an HTTP 200 with zero events is a genuine 'no quotes received in this "
                "window' observation; it is not proof that no market existed"
            ),
        },
        "classification": {
            "capture_gap": sum(1 for leg in audited if leg["capture_gap"]),
            "capture_gap_full": classification.get("capture_gap_full", 0),
            "capture_gap_partial": classification.get("capture_gap_partial", 0),
            "identical_full_window": classification.get("identical_full_window", 0),
            "no_quotes_in_api_window": classification.get("no_quotes_in_api_window", 0),
        },
        "status_basis": {
            "case_status": (
                "per-case entry/exit status of the immutable frontier pairs parquet "
                "(recorded after the earlier fetch's supplement merge) or, for "
                "repeat-only trades, the primary-rule re-derivation from the ranked cache"
            ),
            "old_views": (
                "pristine ranked cache only, no supplement: a leg the earlier frontier "
                "fetch acquired can read quote_not_acquired here while case_status "
                "already carries the merged supplement"
            ),
            "union_views": (
                "ranked cache plus this run's own-root supplement, primary prints KEEP FIRST"
            ),
            "why_reported_separately": (
                "the gap between case_status and old_250_status IS the acquisition "
                "evidence, not an inconsistency"
            ),
        },
        "variant_status_counts": _status_rollup(audited),
        "cost_basis": {
            "observed": "NBBO touch spread (entry ASK vs exit BID) in bps, per arrival latency",
            "residual_ladder": (
                "not applied - this audit measures quote completeness, not trade P&L"
            ),
            "fee_funded_quantity": "not computed - no order, no fill, no promotion",
        },
        "bounds": {
            "cohorts": _cohort_bounds(audited),
            "statement": (
                "original signal state support (fresh/capacity) is unchanged where no "
                "acquisition gap exists; a gap bounds the data UNKNOWN it explains and "
                "never becomes alpha evidence"
            ),
        },
        "data_unknowns": pending,
        "statistical_outputs": (
            "none (no regression, no bootstrap CI, no annualisation, no p-value)"
        ),
        "financial": {
            "promotion": "none",
            "alpha_evidence": "none",
            "no_cagr": True,
            "sip_opex_usd_per_year_unchanged": 1188,
        },
        "legs": audited,
    }
    write_json(out / "audit.json", audit)
    print(
        json.dumps(
            {
                "out": str(out),
                "legs_selected": len(legs),
                "api_requests": requests_issued,
                "cache_reuse": audit["cache_reuse"]["cached_evidence_legs"],
                "classification": audit["classification"],
                "data_unknowns": len(pending),
                "runtime_s": audit["runtime_s"],
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
