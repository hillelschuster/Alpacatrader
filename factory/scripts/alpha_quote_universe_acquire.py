#!/usr/bin/env python3
"""Read-only SIP quote-UNIVERSE acquisition for the not-acquired panel tickers.

The ranked SIP day cache was captured for an OLD ticker universe: roughly half of the
causal-liquidity-qualified open-anchored top-gainer states have NO quote stream for
their session. That is an ACQUISITION gap, not a market fact and not cash. This
producer closes exactly that gap, and nothing else:

* METADATA UNIVERSE (no network, no credentials): for every allowed panel day of the
  study calendar (train 2021-05-01..2022-12-31, validation all of 2023, confirmation
  2025-02-01..2026-05-31) the candidate universe is defined from metadata alone. No
  old-forecast threshold, no label-known filter, no outcome conditioning of any kind:
  the candidate list per day is written to ``manifest/<day>.json`` BEFORE a single quote
  is read.
* MISSING GROUPING: per day, the tickers absent from the ranked SIP day cache AND from
  every existing supplement root (the same merge the study uses: the ORIGINAL print
  always wins, a supplement may only add timestamps).
* FULL RTH WINDOW: each candidate is fetched for the WHOLE regular session - from 30
  seconds before that day's EARLIEST intent clock through the session close - with
  feed=sip, asof=<the trade day>, limit=10000 and every ``next_page_token``. Withdrawal
  and non-regular prints come back verbatim: no condition is synthesised and nothing
  is defaulted to R.
* BORING, RESUMABLE IO: one request per (day, ticker); every page is stored verbatim
  as JSON under ``raw/``, normalized (frontier ``_quote_rows`` schema, RAW sizes
  unchanged - the consumer applies the lot epoch) as a parquet part under
  ``parts/``, and a per-(day, ticker) ``cursor.json`` records the pagination state so
  an interrupted run continues exactly where it stopped. The day manifest is rewritten
  atomically after every candidate.
* PRIMARY_KEEPFIRST MERGE: a day's new parts are merged OVER the ranked cache and the
  existing supplements (original first, one row per (symbol, ts_utc)) into this
  producer's own ``quotes/<day>.parquet``. The ORIGINAL 3.9GB SIP source is never
  written, never revised and never overwritten.
* HONEST OUTCOMES: a 200 with zero events is a KNOWN acquisition empty (recorded, not
  tradable absence forever); any HTTP error or request exception is a DATA UNKNOWN for
  that (day, ticker) - never fabricated, never silently treated as cash.

TWO UNIVERSES, ONE MECHANISM (``--universe``): ``qualified`` (the default, and the behavior
of every manifest already written) plans the causal-liquidity-qualified states, bit-identical
to the label producer's row set. ``panel`` plans EVERY ticker present in the day's panel
file instead, because the qualified pass leaves ~4-14 admitted names per session - present in
the panel day file but failing DV>=1M or bars15>=12 (ATHX/BCAB-like names) - with NO quote
stream, which forces every consumer of the full admitted watchlist to report the session as
coverage-unknown. The option changes nothing else: the same gates, the same per-day window,
the same pacing, pagination, resume and merge. It enters the per-day resume/plan hash, so a
panel-mode plan can never reuse a qualified-mode manifest; run each mode into its own
``--out`` root.

Pacing is a request sleep (default 0.35s, ~171 req/min, below the historical Basic
200/min) with the shared 429-aware GET. Credentials are read from the repo root .env
by the frontier helper and are never printed, stored or logged. No account, no order,
no paid purchase: this is a read-only market-data fetch.

Usage:
  uv run --no-sync python factory/scripts/alpha_quote_universe_acquire.py --plan-only
      # metadata only: the candidate manifests, no network, no credentials
  uv run --no-sync python factory/scripts/alpha_quote_universe_acquire.py
      # full incremental acquisition, resumable per (day, ticker) and per page
  uv run --no-sync python factory/scripts/alpha_quote_universe_acquire.py --resume
      # resume from the cursors, parts and manifests already on disk
  uv run --no-sync python factory/scripts/alpha_quote_universe_acquire.py --days 2023-05-15
      # smoke subset (metadata + fetch for one day)
  uv run --no-sync python factory/scripts/alpha_quote_universe_acquire.py --universe panel \
      --out /home/hillel/alpha-data/open-search-v1/quote_universe_acquire_panel --plan-only
      # second pass: EVERY panel ticker with no quote stream - including the admitted
      # names that fail causal_liquidity - planned into its own output root
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))

from alpha_open_panel import allowed
from alpha_open_sim import causal_liquidity
from alpha_quote_audit import clock_us
from alpha_sparse_execution_frontier import (
    ALPACA_QUOTES_URL,
    FETCH_WINDOW_BEFORE_S,
    PAGE_LIMIT,
    QUOTE_SCHEMA_LOCAL,
    _alpaca_headers,
    _get,
    _iso_us,
    _quote_rows,
)
from alpha_sparse_quote_service import day_quote_path, supplement_day_path

# ----- fixed configuration ----------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
PANEL_DATA_ROOT = REPO_ROOT / "data"  # ranked SIP cache root (read-only for this producer)
PANEL_ROOT = Path(
    os.environ.get("ALPHA_OPEN_SEARCH_V1", Path.home() / "alpha-data" / "open-search-v1")
)
PANEL_DAYS = PANEL_ROOT / "days"
OUTPUT = PANEL_ROOT / "quote_universe_acquire"
# Already-cached quote roots whose prints count as PRESENT (same merge order the study
# uses: the ranked SIP day cache is primary, supplements may only add timestamps).
DEFAULT_EXISTING_ROOTS = (
    PANEL_ROOT / "sparse_execution_frontier" / "fetched_quotes" / "quotes",
    PANEL_ROOT / "quote_completeness_audit" / "quotes",
    PANEL_ROOT / "quote_completeness_audit" / "fetched_quotes" / "quotes",
)
TRAIN_START, TRAIN_END = "2021-05-01", "2022-12-31"
VAL_START, VAL_END = "2023-01-01", "2023-12-31"
LATE_START, LATE_END = "2025-02-01", "2026-05-31"
BLOCKS = {
    "train": (TRAIN_START, TRAIN_END),
    "validation": (VAL_START, VAL_END),
    "confirmation": (LATE_START, LATE_END),
}
# The per-day candidate universe. "qualified" (default) is the causal-liquidity-qualified
# state set the label producer uses; "panel" is EVERY ticker in the day's panel file, so the
# admitted names that fail the liquidity filter still get a quote stream acquired.
UNIVERSES = ("qualified", "panel")
DEFAULT_UNIVERSE = "qualified"
# What a day manifest says its candidate set IS: a panel-mode manifest must never be read as
# if its tickers were the liquidity-qualified subset.
UNIVERSE_NOTES = {
    "qualified": (
        "candidates are the causal-liquidity-qualified tickers with no quote "
        "stream in the ranked cache or any supplement root; the list was "
        "fixed before any fetch, so no outcome can condition it"
    ),
    "panel": (
        "candidates are EVERY ticker present in this day's panel file with no quote "
        "stream in the ranked cache or any supplement root - including the admitted "
        "names that fail causal_liquidity (DV>=1M, bars15>=12); the list was "
        "fixed before any fetch, so no outcome can condition it"
    ),
}
WINDOW_BEFORE_S = FETCH_WINDOW_BEFORE_S  # 30s before the earliest intent clock
SESSION_CLOSE_MINUTE = 960  # 16:00 ET, one past the panel's last minute (959)
DEFAULT_REQ_SLEEP_S = 0.35  # ~171 requests/minute, below the historical Basic 200/min
VERSION = 1
MANIFEST_SCHEMA = 1
PROTECTED_UNREAD = ["2024", "2025-01", "2026-06", "2026-07", "2026-08"]
STATUS = "DISCOVERY-NOT-VALIDATED"


def producer_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _digest_bytes(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=1, default=str) + "\n")
    os.replace(tmp, path)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def guard_days(days: list[str]) -> None:
    """The protected-day guard runs BEFORE any panel, quote or API read."""
    bad = [d for d in days if not allowed(d)]
    if bad:
        raise SystemExit(
            f"[universe-acquire] protected/out-of-scope day refused: {bad[:4]} "
            f"({len(bad)} days); nothing was read"
        )


def calendar_days(days: list[str] | None, blocks: list[str]) -> list[str]:
    keep = set(days) if days else None
    out = []
    for path in sorted(PANEL_DAYS.glob("????-??-??.parquet")):
        day = path.stem
        if keep is not None and day not in keep:
            continue
        for name in blocks:
            lo, hi = BLOCKS[name]
            if lo <= day <= hi:
                out.append(day)
                break
    return sorted(set(out))


@dataclass(frozen=True)
class DayPlan:
    day: str
    # the day's candidate universe under `universe`: the causal-liquidity-qualified tickers
    # in qualified mode, EVERY panel ticker in panel mode (see ``--universe``).
    qualified: list[str]
    missing: list[str]
    session_end: int
    earliest_intent_us: int
    close_us: int
    universe: str = DEFAULT_UNIVERSE

    def plan_hash(self, producer: str, req_sleep: float) -> str:
        return _digest_bytes(
            {
                "day": self.day,
                "producer": producer,
                "missing": self.missing,
                "universe": self.universe,
                "window_before_s": WINDOW_BEFORE_S,
                "limit": PAGE_LIMIT,
                "feed": "sip",
                "asof": self.day,
                "schema": MANIFEST_SCHEMA,
                "version": VERSION,
                "req_sleep": req_sleep,
            }
        )


def existing_symbols(day: str, data_root: Path, supplemental_roots: tuple[Path, ...]) -> set[str]:
    """Symbols already present in the ranked cache or any supplement root."""
    have: set[str] = set()
    for path in [day_quote_path(data_root, day)] + [
        supplement_day_path(root, day) for root in supplemental_roots
    ]:
        if path.exists():
            have |= set(
                pl.scan_parquet(path).select("symbol").unique().collect()["symbol"].to_list()
            )
    return have


def day_plan(
    day: str,
    data_root: Path,
    supplemental_roots: tuple[Path, ...],
    universe: str = DEFAULT_UNIVERSE,
) -> DayPlan:
    """The day's metadata universe: the mode's tickers -> the missing candidates.

    ``qualified`` (``--universe qualified``, the default) is the causal-liquidity-qualified
    row set: ``causal_liquidity`` needs only ``log_cum_dv``/``bars15``, so the panel is read
    as a two-plus-column lazy scan (the peer-context columns never enter the filter) at a
    fraction of the IO, bit-identical to the label producer's qualified states.

    ``panel`` (``--universe panel``) is EVERY ticker present in the day's panel file - the
    ~4-14 admitted names per session that fail DV>=1M or bars15>=12 (ATHX/BCAB-like) are the
    point of the second pass, so they must be planned, not filtered out here.

    ``missing`` is always that universe minus the symbols already present in the ranked SIP
    day cache and every supplement root.
    """
    guard_days([day])
    if universe not in UNIVERSES:
        raise SystemExit(
            f"[universe-acquire] unknown universe: {universe!r} "
            f"(choose from {', '.join(UNIVERSES)})"
        )
    path = PANEL_DAYS / f"{day}.parquet"
    if not path.exists():
        raise SystemExit(f"[universe-acquire] panel day missing: {path}")
    columns = ["ticker", "t", "session_end"]
    if universe == "qualified":
        columns += ["log_cum_dv", "bars15"]  # the only columns causal_liquidity reads
    scan = pl.scan_parquet(path).select(columns)
    if universe == "qualified":
        # causal_liquidity is a pure column predicate (log_cum_dv / bars15 only), so the
        # lazy scan reproduces the label producer's qualified row set exactly.
        scan = scan.filter(causal_liquidity(scan))
    cand = scan.collect()
    session_end = int(cand["session_end"][0]) if cand.height else 959
    if not cand.height:
        return DayPlan(day, [], [], session_end, 0, 0, universe)
    tickers = sorted(set(cand["ticker"].to_list()))
    # the earliest intent clock is the causal (t+1) clock of the earliest state in the
    # universe - in panel mode that clock can precede the qualified mode's, because the
    # planned tickers are not restricted to the liquidity-qualified states.
    earliest = min(int(t) + 1 for t in cand["t"].to_list())
    have = existing_symbols(day, data_root, supplemental_roots)
    missing = [t for t in tickers if t not in have]
    return DayPlan(
        day=day,
        qualified=tickers,
        missing=missing,
        session_end=session_end,
        earliest_intent_us=clock_us(day, earliest, 0),
        close_us=clock_us(day, SESSION_CLOSE_MINUTE, 0),
        universe=universe,
    )


# ----- per-(day, ticker) cursor and page parts ---------------------------------
def raw_dir(out_dir: Path, day: str, ticker: str) -> Path:
    return out_dir / "raw" / day / ticker


def parts_dir(out_dir: Path, day: str, ticker: str) -> Path:
    return out_dir / "parts" / day / ticker


def cursor_path(out_dir: Path, day: str, ticker: str) -> Path:
    return parts_dir(out_dir, day, ticker) / "cursor.json"


def read_cursor(out_dir: Path, day: str, ticker: str) -> dict:
    path = cursor_path(out_dir, day, ticker)
    if not path.exists():
        return {"pages": 0, "next_token": None, "status": "pending", "quote_events": 0}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"pages": 0, "next_token": None, "status": "pending", "quote_events": 0}


def write_cursor(out_dir: Path, day: str, ticker: str, cursor: dict) -> None:
    write_json_atomic(cursor_path(out_dir, day, ticker), cursor)


def load_parts(out_dir: Path, day: str, ticker: str) -> pl.DataFrame:
    """All page parts already on disk for one (day, ticker), in page order."""
    root = parts_dir(out_dir, day, ticker)
    parts = sorted(root.glob("page_*.parquet"))
    if not parts:
        return pl.DataFrame(schema=QUOTE_SCHEMA_LOCAL)
    return pl.concat([pl.read_parquet(p) for p in parts], how="vertical")


def _write_page_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)


def _write_page_parquet(path: Path, frame: pl.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    frame.write_parquet(tmp)
    os.replace(tmp, path)


def fetch_symbol_day(
    session,
    headers: dict,
    out_dir: Path,
    plan: DayPlan,
    ticker: str,
    req_sleep: float,
) -> dict:
    """Full-pagination SIP read of ONE ticker's whole RTH session, resumable per page."""
    start_us = plan.earliest_intent_us - int(WINDOW_BEFORE_S * 1_000_000)
    end_us = plan.close_us
    record = {
        "day": plan.day,
        "ticker": ticker,
        "endpoint": ALPACA_QUOTES_URL,
        "feed": "sip",
        "asof": plan.day,
        "limit": PAGE_LIMIT,
        "window_utc": [_iso_us(start_us), _iso_us(end_us)],
        "window_before_s": WINDOW_BEFORE_S,
        "status": "pending",
        "http_status": None,
        "pages": 0,
        "quote_events": 0,
        "error": None,
    }
    cursor = read_cursor(out_dir, plan.day, ticker)
    if cursor.get("status") in ("complete", "no_quotes_in_window"):
        record.update(
            {
                "status": cursor["status"],
                "http_status": cursor.get("http_status"),
                "pages": int(cursor.get("pages", 0)),
                "quote_events": int(cursor.get("quote_events", 0)),
                "resumed": True,
            }
        )
        return record
    token = cursor.get("next_token")
    page = int(cursor.get("pages", 0))
    events_total = int(cursor.get("quote_events", 0))
    http = cursor.get("http_status")
    http_codes: list[int] = []
    try:
        while True:
            params = {
                "symbols": ticker,
                "start": _iso_us(start_us),
                "end": _iso_us(end_us),
                "feed": "sip",
                "asof": plan.day,
                "limit": PAGE_LIMIT,
            }
            if token:
                params["page_token"] = token
            r = _get(session, ALPACA_QUOTES_URL, params, headers)
            http = r.status_code
            http_codes.append(int(r.status_code))
            if r.status_code != 200:
                record.update(
                    {
                        "status": f"unknown_http_{r.status_code}",
                        "http_status": r.status_code,
                        "error": r.text[:300],
                        "pages": page,
                        "quote_events": events_total,
                    }
                )
                cursor.update(
                    {
                        "pages": page,
                        "next_token": token,
                        "status": record["status"],
                        "http_status": r.status_code,
                        "quote_events": events_total,
                        "error": record["error"],
                    }
                )
                write_cursor(out_dir, plan.day, ticker, cursor)
                return record
            js = r.json()
            _write_page_json(raw_dir(out_dir, plan.day, ticker) / f"page_{page}.json", js)
            data = js.get("quotes") or {}
            ev = data.get(ticker, []) if isinstance(data, dict) else []
            frame = (
                pl.DataFrame(_quote_rows(ev, ticker), schema=QUOTE_SCHEMA_LOCAL)
                if ev
                else pl.DataFrame(schema=QUOTE_SCHEMA_LOCAL)
            )
            _write_page_parquet(
                parts_dir(out_dir, plan.day, ticker) / f"page_{page}.parquet", frame
            )
            events_total += len(ev)
            page += 1
            token = js.get("next_page_token")
            cursor.update(
                {
                    "pages": page,
                    "next_token": token,
                    "status": (
                        ("complete" if events_total else "no_quotes_in_window")
                        if not token
                        else "paginating"
                    ),
                    "http_status": http,
                    "quote_events": events_total,
                }
            )
            write_cursor(out_dir, plan.day, ticker, cursor)
            if not token:
                break
            if req_sleep:
                time.sleep(req_sleep)
    except Exception as e:  # network error: a precise UNKNOWN, never fabricated data
        record.update(
            {
                "status": "unknown_request_exception",
                "http_status": http,
                "pages": page,
                "quote_events": events_total,
                "error": f"{type(e).__name__}: {str(e)[:200]}",
            }
        )
        cursor.update(
            {
                "pages": page,
                "next_token": token,
                "status": record["status"],
                "http_status": http,
                "quote_events": events_total,
                "error": record["error"],
            }
        )
        write_cursor(out_dir, plan.day, ticker, cursor)
        return record
    record.update(
        {
            "status": "complete" if events_total else "no_quotes_in_window",
            "http_status": http,
            "http_codes": http_codes,
            "pages": page,
            "quote_events": events_total,
        }
    )
    return record


def merge_day(
    out_dir: Path, plan: DayPlan, data_root: Path, supplemental_roots: tuple[Path, ...]
) -> dict:
    """PRIMARY_KEEPFIRST merge: ranked cache, supplements, then this run's new parts.

    The original print always wins for a duplicated (symbol, ts_utc); the ORIGINAL SIP
    source files are read but never written. The combined day file is written only when
    the acquisition actually added prints, so an all-empty day stays a recorded KNOWN
    acquisition empty instead of a duplicated day file.
    """
    frames = []
    primary = day_quote_path(data_root, plan.day)
    if primary.exists():
        frames.append(pl.read_parquet(primary).select(list(QUOTE_SCHEMA_LOCAL)))
    for root in supplemental_roots:
        spath = supplement_day_path(root, plan.day)
        if spath.exists():
            frames.append(pl.read_parquet(spath).select(list(QUOTE_SCHEMA_LOCAL)))
    new_frames = [f for f in (load_parts(out_dir, plan.day, t) for t in plan.missing) if f.height]
    new_rows = int(sum(f.height for f in new_frames))
    if not new_rows:
        return {
            "merged": False,
            "reason": "no_new_events_known_acquisition_empty",
            "new_rows": 0,
        }
    frames.extend(new_frames)
    merged = (
        pl.concat(frames, how="vertical")
        .unique(subset=["symbol", "ts_utc"], keep="first")
        .sort("symbol", "ts_utc")
    )
    target = out_dir / "quotes" / f"{plan.day}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp{os.getpid()}")
    merged.write_parquet(tmp)
    os.replace(tmp, target)
    return {
        "merged": True,
        "path": str(target),
        "rows": int(merged.height),
        "new_rows": new_rows,
        "sha256": sha256_file(target),
        "symbols": int(merged["symbol"].n_unique()),
    }


def manifest_path(out_dir: Path, day: str) -> Path:
    return out_dir / "manifest" / f"{day}.json"


def load_manifest(out_dir: Path, day: str) -> dict | None:
    path = manifest_path(out_dir, day)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def run(args: argparse.Namespace) -> None:
    t0 = time.time()
    out = args.out or OUTPUT
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")
    producer = producer_sha256()

    data_root = args.data_root
    supplemental = list(DEFAULT_EXISTING_ROOTS)
    for extra in args.supplement or []:
        if extra not in supplemental:
            supplemental.append(extra)
    supplemental_roots = tuple(supplemental)

    days = calendar_days(args.days, args.blocks.split(","))
    if not days:
        raise SystemExit("[universe-acquire] no allowed panel day in the requested blocks")
    guard_days(days)  # BEFORE any quote or API read
    print(
        f"[universe-acquire] universe={args.universe} calendar days={len(days)} "
        f"blocks={args.blocks} data_root={data_root} supplements={len(supplemental_roots)}",
        flush=True,
    )

    plans: list[DayPlan] = []
    for day in days:
        plan = day_plan(day, data_root, supplemental_roots, args.universe)
        plans.append(plan)
        manifest = load_manifest(out, day) if args.resume else None
        fresh = (
            manifest is not None
            and manifest.get("resume_hash") == plan.plan_hash(producer, args.req_sleep)
            and manifest.get("schema") == MANIFEST_SCHEMA
        )
        if fresh:
            plans[-1] = plan
            print(
                f"[plan] {day}: {len(plan.missing)} missing of {len(plan.qualified)} "
                f"{plan.universe} universe (resumed)",
                flush=True,
            )
            continue
        write_json_atomic(
            manifest_path(out, day),
            {
                "day": day,
                "schema": MANIFEST_SCHEMA,
                "resume_hash": plan.plan_hash(producer, args.req_sleep),
                "producer_sha256": producer,
                "planned_before_any_quote_or_api_read": True,
                "universe": plan.universe,
                "window_utc": [
                    _iso_us(plan.earliest_intent_us - int(WINDOW_BEFORE_S * 1_000_000)),
                    _iso_us(plan.close_us),
                ],
                "window_before_s": WINDOW_BEFORE_S,
                "feed": "sip",
                "asof": day,
                "limit": PAGE_LIMIT,
                "qualified_tickers": plan.qualified,
                "candidates": plan.missing,
                "records": [],
                "merge": None,
                "note": UNIVERSE_NOTES[plan.universe],
            },
        )
        print(
            f"[plan] {day}: {len(plan.missing)} missing of {len(plan.qualified)} "
            f"{plan.universe} universe",
            flush=True,
        )

    todo = [(p, m) for p in plans if (m := load_manifest(out, p.day)) is not None]
    total_candidates = sum(len(p.missing) for p, _ in todo)
    total_done = sum(
        1 for _, m in todo for r in m.get("records", []) if r.get("status") != "pending"
    )
    print(
        f"[universe-acquire] candidates={total_candidates} resolved={total_done} "
        f"plan_only={args.plan_only}",
        flush=True,
    )
    if args.plan_only:
        summary = {
            "study": "alpha_quote_universe_acquire",
            "status": STATUS,
            "plan_only": True,
            "days": len(days),
            "days_with_candidates": sum(1 for p, _ in todo if p.missing),
            "candidate_day_ticker_pairs": total_candidates,
            "requests_planned": total_candidates,
            "producer_sha256": producer,
            "output_root": str(out),
        }
        write_json_atomic(out / "plan_summary.json", summary)
        print(
            f"[plan-only] days_with_candidates={summary['days_with_candidates']} "
            f"pairs={total_candidates} -> {out / 'plan_summary.json'}",
            flush=True,
        )
        return

    import requests

    headers = _alpaca_headers(args.env_path)
    if headers is None:
        raise SystemExit(
            "[universe-acquire] ALPACA_API_KEY/ALPACA_SECRET_KEY not found in "
            f"{args.env_path}; nothing was fetched (use --plan-only for metadata only)"
        )
    session = requests.Session()
    done = 0
    for plan, manifest in todo:
        records = {r["ticker"]: r for r in manifest.get("records", [])}
        for ticker in plan.missing:
            prior = records.get(ticker)
            if prior and prior.get("status") != "pending":
                continue
            rec = fetch_symbol_day(session, headers, out, plan, ticker, args.req_sleep)
            records[ticker] = rec
            manifest["records"] = [records[t] for t in plan.missing if t in records]
            manifest["fetched_through"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            write_json_atomic(manifest_path(out, plan.day), manifest)
            done += 1
            if done % 25 == 0:
                print(
                    f"[fetch] {done} candidates; last {plan.day} {ticker} "
                    f"status={rec['status']} events={rec['quote_events']}",
                    flush=True,
                )
            if args.req_sleep:
                time.sleep(args.req_sleep)
        # the day's new parts are merged PRIMARY_KEEPFIRST over the ranked cache
        manifest["records"] = [records[t] for t in plan.missing if t in records]
        manifest["merge"] = merge_day(out, plan, data_root, supplemental_roots)
        unknowns = [r["ticker"] for r in manifest["records"] if r["status"].startswith("unknown")]
        manifest["unknown_tickers"] = unknowns
        manifest["acquisition_outcome"] = (
            "acquired"
            if not unknowns
            else "partial_unknown"
            if manifest["merge"]["merged"]
            else "unknown"
        )
        write_json_atomic(manifest_path(out, plan.day), manifest)
        print(
            f"[day] {plan.day}: candidates={len(plan.missing)} "
            f"unknown={len(unknowns)} merged={manifest['merge']['merged']} "
            f"new_rows={manifest['merge'].get('new_rows', 0)}",
            flush=True,
        )
    summary = {
        "study": "alpha_quote_universe_acquire",
        "status": STATUS,
        "days": len(days),
        "days_with_candidates": sum(1 for p, _ in todo if p.missing),
        "candidate_day_ticker_pairs": total_candidates,
        "resolved": done,
        "producer_sha256": producer,
        "output_root": str(out),
        "runtime_s": round(time.time() - t0, 1),
    }
    write_json_atomic(out / "acquire_summary.json", summary)
    print(
        f"[done] {summary['runtime_s']}s -> {out / 'acquire_summary.json'} "
        f"(quotes root: {out / 'quotes'})",
        flush=True,
    )


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--out", type=Path, default=None, help=f"output root (default: {OUTPUT})")
    p.add_argument(
        "--days",
        nargs="+",
        default=None,
        help="restrict the calendar to these ET days (smoke/debug only)",
    )
    p.add_argument(
        "--blocks",
        type=str,
        default="train,validation,confirmation",
        help="comma subset of {train,validation,confirmation}",
    )
    p.add_argument(
        "--universe",
        choices=UNIVERSES,
        default=DEFAULT_UNIVERSE,
        help="per-day candidate universe. qualified (default): the causal-liquidity-qualified "
        "states, exactly what the already-written manifests hold. panel: EVERY ticker in the "
        "day's panel file, so the admitted names that fail DV>=1M/bars15>=12 still get a quote "
        "stream. The choice enters the resume hash, so a panel plan never reuses a qualified "
        "manifest - run each mode into its own --out root.",
    )
    p.add_argument(
        "--resume", action="store_true", help="resume from the cursors, parts and manifests"
    )
    p.add_argument(
        "--plan-only",
        action="store_true",
        help="metadata only: write the candidate manifests, never touch the network",
    )
    p.add_argument(
        "--data-root",
        type=Path,
        default=PANEL_DATA_ROOT,
        help="panel data root containing sip/net/ (the ranked cache, never written)",
    )
    p.add_argument(
        "--env-path",
        type=Path,
        default=Path(__file__).resolve().parents[2] / ".env",
        help="repo root .env holding the read-only market-data credentials",
    )
    p.add_argument(
        "--supplement",
        type=Path,
        action="append",
        default=None,
        help="extra already-cached quote root; its prints also count as present",
    )
    p.add_argument(
        "--req-sleep",
        type=float,
        default=DEFAULT_REQ_SLEEP_S,
        help=f"seconds between requests (default {DEFAULT_REQ_SLEEP_S:.2f}s ~ "
        f"{60.0 / DEFAULT_REQ_SLEEP_S:.0f} req/min, below the historical Basic 200/min)",
    )
    run(p.parse_args())


if __name__ == "__main__":
    main()
