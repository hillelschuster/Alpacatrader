#!/usr/bin/env python3
"""Public SEC EDGAR issuer-event collector + point-in-time association verifier.

This producer answers ONE research question and nothing else: for the ticker / day
names of the causal PIT panel, is there real, dated SEC evidence that this trading
symbol belonged to this issuer BEFORE that day's trade clock, and what filing /
issuance / registration events does the public record actually contain.

What it deliberately does NOT do:

* NO ticker->CIK map is trusted as proof of history. ``company_tickers.json`` is a
  CURRENT ticker index: it seeds discovery (which CIK to ask about) and nothing
  more. A historical association is only ever asserted from a DATED filing whose
  as-filed cover carries the DEI ``TradingSymbol`` fact together with the issuer
  name and the class title, accepted at or before the trade clock.
* NO absence is invented. An unmapped ticker, a CIK whose cover evidence was never
  retrieved, a non-inline-XBRL cover, a request failure and a seed whose dated
  symbols all post-date the clock are all recorded as UNKNOWN reasons. The status
  vocabulary contains NO "no filing" / "no event" outcome: a missing archive is a
  missing archive, never a market fact.
* NO price, gross return, exit, label or outcome column is ever read. The panel is
  projected to exactly ``PANEL_COLUMNS`` (day / ticker / decision minute); every
  other column of those parquet files is out of scope by construction.
* NO free float is produced. ``shares_outstanding_as_filed`` is the DEI
  ``EntityCommonStockSharesOutstanding`` fact exactly as filed (shares, never a
  float-derived share count). ``EntityPublicFloat`` is kept in its own field and is
  never converted into shares.
* NO intraday availability is claimed. ``filingDate`` and ``reportDate`` are
  DATE-ONLY; only ``acceptanceDateTime`` carries a clock. Even an acceptance
  timestamp is only "accepted", never "publicly visible", so every record carries an
  explicit ``public_first_visibility_lag: "unknown"`` flag. SEC's documented rule
  that submissions beginning after 5:30 p.m. ET (10:00 p.m. for ownership forms
  3/4/5) are disseminated the next business day is recorded as a flag on the
  record, not silently applied.

Fair access: a declared ``User-Agent`` is REQUIRED (``--user-agent`` or the
``SEC_USER_AGENT`` environment variable). SEC documents a current maximum request
rate of 10 requests/second and asks that automated tools be declared; there is no
fallback to a fabricated contact. This producer paces itself at ~1-2 requests/second
(default 0.7s between requests), runs ONE request at a time on a single session, and
never prints or writes the user agent itself (only its sha256 and length).

Usage:
  uv run --no-sync python factory/scripts/alpha_sec_issuer_events.py --plan-only
      # panel metadata only: the (day, ticker) universe and plan, no network
  uv run --no-sync python factory/scripts/alpha_sec_issuer_events.py \
      --user-agent "Alpacatrader research <contact configured in SEC_USER_AGENT>"
      # full incremental collection, resumable per issuer, per page and per day
  uv run --no-sync python factory/scripts/alpha_sec_issuer_events.py --resume
      # resume from the manifests, raw pages and association files already on disk
  uv run --no-sync python factory/scripts/alpha_sec_issuer_events.py --days 2023-03-15
      # smoke subset of the calendar (association scope)
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))

from alpha_open_panel import allowed  # the shared protected-day guard
from alpha_quote_audit import clock_us  # ET day+minute -> UTC microseconds

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# ----- fixed configuration ----------------------------------------------------
PANEL_ROOT = Path(
    os.environ.get("ALPHA_OPEN_SEARCH_V1", Path.home() / "alpha-data" / "open-search-v1")
)
PANEL_DAYS = PANEL_ROOT / "days"
OUTPUT = PANEL_ROOT / "sec_issuer_events"
TRAIN_START, TRAIN_END = "2021-02-01", "2022-12-31"
VAL_START, VAL_END = "2023-01-01", "2023-12-31"
LATE_START, LATE_END = "2025-02-01", "2026-05-31"
BLOCKS = {
    "train": (TRAIN_START, TRAIN_END),
    "validation": (VAL_START, VAL_END),
    "confirmation": (LATE_START, LATE_END),
}
# The earliest panel decision day is 2021-02-01, so association evidence is sought
# from ~2 years earlier: the latest as-filed cover accepted before a 2021 clock is
# almost always a 2019/2020 periodic report. Everything older is recorded as
# out-of-floor instead of being silently dropped.
ASSOCIATION_LOOKBACK_DAYS = 800
# The decision is taken on the panel's clock; the intent arrives at t+1 with the
# shared 250ms quote-arrival convention.
ARRIVAL_LATENCY_MS = 250
INTENT_MINUTE_OFFSET = 1
PANEL_COLUMNS = ("day", "ticker", "t")  # the ONLY panel columns ever projected
PROTECTED_UNREAD = ["2024", "2025-01", "2026-06", "2026-07", "2026-08"]
STATUS = "DISCOVERY-NOT-VALIDATED"

# SEC endpoints (public, no account, no key, no order, no paid purchase).
SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions"
SEC_CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"

# Fair access: the documented current maximum request rate is 10 requests/second.
DEFAULT_REQ_SLEEP_S = 0.7  # ~1.4 requests/second, ~86 requests/minute
MAX_DECLARED_RPS = 10
FETCH_ATTEMPTS = 3
MAX_ARCHIVE_PER_ISSUER = 6  # verbatim cover-page retrievals per issuer
MAX_ARCHIVE_BYTES = 12 << 20  # verbatim storage cap; the facts and sha are always kept
REQUEST_TIMEOUT_S = 90

VERSION = 1
MANIFEST_SCHEMA = 1

# DEI cover-page facts parsed verbatim out of the ARCHIVED primary document. The
# namespaces differ by taxonomy year (http://xbrl.sec.gov/dei/2019-01-31,
# .../dei/2024, ...), so the LOCAL name is matched and the prefix must be ``dei``.
DEI_COVER_TAGS = (
    "TradingSymbol",
    "NoTradingSymbolFlag",
    "Security12bTitle",
    "Security12gTitle",
    "SecurityExchangeName",
    "EntityRegistrantName",
    "EntityCentralIndexKey",
    "EntityFileNumber",
    "EntityCommonStockSharesOutstanding",
    "EntityPublicFloat",
    "AmendmentFlag",
    "DocumentType",
)
DEI_SYMBOL_TAG = "TradingSymbol"
DEI_SHARES_TAG = "EntityCommonStockSharesOutstanding"
DEI_FLOAT_TAG = "EntityPublicFloat"

# Ownership forms have their own (later) documented dissemination cut-off.
OWNERSHIP_FORMS = {"3", "3/A", "4", "4/A", "5", "5/A"}
EVENING_CUTOFF_ET = (17, 30)  # 5:30 p.m. ET, the documented general cut-off
OWNERSHIP_CUTOFF_ET = (22, 0)  # 10:00 p.m. ET for ownership forms 3/4/5

# FORM_CLASSES labels the submission's form. Only the archival verbatim cover is
# retrieved for the classes listed in ARCHIVE_FORM_CLASSES; the submissions
# metadata itself is always kept for every form.
FORM_CLASSES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "registration",
        (
            "S-1",
            "S-1/A",
            "S-1MEF",
            "S-3",
            "S-3/A",
            "S-3D",
            "S-4",
            "S-4/A",
            "S-8",
            "S-11",
            "S-20",
            "F-1",
            "F-1/A",
            "F-1MEF",
            "F-3",
            "F-3/A",
            "F-4",
            "F-4/A",
            "F-10",
            "POS AM",
            "POS 8C",
            "POS462B",
            "POSASR",
        ),
    ),
    (
        "ipo_prospectus",
        (
            "424B1",
            "424B2",
            "424B3",
            "424B4",
            "424B5",
            "424B7",
            "424B8",
            "424H",
            "424H/A",
            "F-1MEF",
            "F-N",
            "F-X",
        ),
    ),
    (
        "exchange_listing_registration",
        ("8-A12B", "8-A12B/A", "8-A12G", "8-A12G/A", "8-A12G3", "8-A12G3/A"),
    ),
    (
        "periodic_report",
        (
            "10-K",
            "10-K/A",
            "10-KT",
            "10-KT/A",
            "10-Q",
            "10-Q/A",
            "10-QT",
            "10-QT/A",
            "20-F",
            "20-F/A",
            "40-F",
            "40-F/A",
            "11-K",
            "11-K/A",
            "11-KT",
            "11-KT/A",
            "6-K",
            "6-K/A",
            "18-K",
            "18-K/A",
        ),
    ),
    ("current_report", ("8-K", "8-K/A", "8-K12B", "8-K12B/A", "8-K12G3", "8-K12G3/A", "8-K15D5")),
    (
        "delisting_deregistration",
        ("25", "25-NSE", "15-12B", "15-12B/A", "15-12G", "15-12G/A", "15-15D"),
    ),
    ("merger_circulation", ("425", "SC TO-T", "SC TO-I", "SC 14D9", "DEFM14A", "PREM14A")),
    ("ownership", ("3", "3/A", "4", "4/A", "5", "5/A", "144", "144/A")),
)
ARCHIVE_FORM_CLASSES = {
    "registration",
    "ipo_prospectus",
    "exchange_listing_registration",
    "periodic_report",
    "current_report",
    "delisting_deregistration",
    "merger_circulation",
}

# Association statuses. Every non-positive outcome is an UNKNOWN reason: there is
# deliberately no "no filing" and no "no event" status anywhere in this vocabulary.
ASSOCIATED = "associated_before_trade_clock"
NO_CIK_SEED = "unknown_no_cik_seed"
SUBMISSIONS_PENDING = "unknown_submissions_not_retrieved"
NO_EVIDENCE_BEFORE_CLOCK = "unknown_no_dated_cover_evidence_before_clock"
NO_SYMBOL_FACT = "unknown_no_trading_symbol_fact_on_cover"
FETCH_FAILED = "unknown_fetch_failed"
AMBIGUOUS = "unknown_multiple_symbols_on_cover"
ASSOCIATION_STATUSES = (
    ASSOCIATED,
    NO_CIK_SEED,
    SUBMISSIONS_PENDING,
    NO_EVIDENCE_BEFORE_CLOCK,
    NO_SYMBOL_FACT,
    FETCH_FAILED,
    AMBIGUOUS,
)

_IX_ELEMENT_RE = re.compile(
    r"<ix:(?P<el>nonnumeric|nonfraction)\b(?P<attrs>[^>]*)>(?P<val>.*?)</ix:(?P=el)>",
    re.IGNORECASE | re.DOTALL,
)
_IX_NAME_RE = re.compile(
    r"""name\s*=\s*["'](?:(?P<prefix>[A-Za-z0-9_\-]+):)?(?P<tag>[A-Za-z0-9_\-]+)["']""",
    re.IGNORECASE,
)
_IX_SCALE_RE = re.compile(r"""\bscale\s*=\s*["'](-?\d+)["']""", re.IGNORECASE)
_IX_CONTEXTREF_RE = re.compile(r"""\bcontextref\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


# ----- boring IO ---------------------------------------------------------------
def producer_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=1, default=str, sort_keys=False) + "\n")
    os.replace(tmp, path)


def read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def guard_days(days: list[str]) -> None:
    """The protected-day guard runs BEFORE any panel, network or API read."""
    bad = [d for d in days if not allowed(d)]
    if bad:
        raise SystemExit(
            f"[sec-issuer-events] protected/out-of-scope day refused: {bad[:4]} "
            f"({len(bad)} days); nothing was read"
        )


def calendar_days(days: list[str] | None, blocks: list[str]) -> list[str]:
    keep = set(days) if days else None
    out: list[str] = []
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


# ----- panel universe (metadata only, no network) ------------------------------
@dataclass(frozen=True)
class PanelUniverse:
    days: tuple[str, ...]
    # (day, ticker) -> the earliest intent clock of that name on that day, UTC us
    clock: dict[tuple[str, str], int]

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(sorted({ticker for _, ticker in self.clock}))

    def earliest_clock_by_ticker(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for (_, ticker), us in self.clock.items():
            if ticker not in out or us < out[ticker]:
                out[ticker] = us
        return out

    def by_ticker(self) -> dict[str, tuple[str, ...]]:
        out: dict[str, list[str]] = {}
        for day, ticker in self.clock:
            out.setdefault(ticker, []).append(day)
        return {t: tuple(sorted(v)) for t, v in out.items()}


def panel_pairs(days: list[str]) -> PanelUniverse:
    """Every causal-PIT panel (day, ticker) pair, with its earliest intent clock.

    Only ``PANEL_COLUMNS`` is projected, so no outcome, gross-return, exit or label
    column of those parquet files can reach this producer. The clock is the shared
    convention: the panel's decision minute plus one minute (the normal next-minute
    intent) plus the 250ms quote arrival.
    """
    guard_days(days)
    clock: dict[tuple[str, str], int] = {}
    for day in days:
        path = PANEL_DAYS / f"{day}.parquet"
        if not path.exists():
            raise SystemExit(f"[sec-issuer-events] panel day missing: {path}")
        scan = pl.scan_parquet(path).select(list(PANEL_COLUMNS))
        cand = scan.collect()
        if not cand.height:
            continue
        for ticker, minute in zip(cand["ticker"].to_list(), cand["t"].to_list(), strict=True):
            if not ticker:
                continue
            key = (day, ticker)
            us = clock_us(day, int(minute) + INTENT_MINUTE_OFFSET, ARRIVAL_LATENCY_MS)
            if key not in clock or us < clock[key]:
                clock[key] = us
    return PanelUniverse(days=tuple(days), clock=clock)


# ----- pacing and the declared user agent --------------------------------------
class Pace:
    """One request at a time, with a declared sleep between requests."""

    def __init__(self, sleep_s: float) -> None:
        self.sleep_s = max(0.0, float(sleep_s))
        self.requests = 0

    def wait(self) -> None:
        self.requests += 1
        if self.requests > 1 and self.sleep_s:
            time.sleep(self.sleep_s)


def resolve_user_agent(args: argparse.Namespace) -> str:
    """The declared SEC User-Agent, or a clear prerequisite failure.

    SEC asks automated tools to be declared (organization + contact). A fabricated
    contact is NOT an acceptable fallback, so a missing user agent is a hard stop
    with an actionable message instead of a silent undeclared request.
    """
    raw = args.user_agent or os.environ.get("SEC_USER_AGENT") or ""
    ua = raw.strip()
    if not ua:
        raise SystemExit(
            "[sec-issuer-events] no declared SEC User-Agent: pass "
            "--user-agent '<organization> <contact>' or export SEC_USER_AGENT "
            "with the project's real configured contact (SEC fair access asks that "
            "automated requests be declared; no contact is fabricated here). "
            "Nothing was requested. Use --plan-only for the metadata-only universe."
        )
    if "@" not in ua and "http" not in ua.lower():
        raise SystemExit(
            "[sec-issuer-events] the declared SEC User-Agent has no contact token "
            "(expected an e-mail address or a URL); SEC fair access asks for a "
            "declared organization plus contact. Nothing was requested."
        )
    return ua


def sec_headers(user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept-Encoding": "gzip, deflate",
        "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
    }


# ----- SEC request helpers -----------------------------------------------------
def cik_padded(cik) -> str:
    return f"{int(cik):010d}"


def submissions_url(cik: str) -> str:
    return f"{SEC_SUBMISSIONS_URL}/CIK{cik_padded(cik)}.json"


def shard_url(name: str) -> str:
    return f"{SEC_SUBMISSIONS_URL}/{name}"


def concept_url(cik: str, tag: str) -> str:
    return f"{SEC_CONCEPT_URL}/CIK{cik_padded(cik)}/dei/{tag}.json"


def archive_url(cik: str, accession: str, primary_document: str) -> str:
    nodash = accession.replace("-", "")
    doc = primary_document.lstrip("/")
    return f"{SEC_ARCHIVES_URL}/{cik_padded(cik)}/{nodash}/{doc}"


def get_json(session, pace: Pace, headers: dict, url: str) -> tuple[int, object, str | None]:
    """(http_status, parsed_json_or_None, error). Never fabricates a payload."""
    last: Exception | None = None
    for attempt in range(FETCH_ATTEMPTS):
        pace.wait()
        try:
            r = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT_S)
        except Exception as e:  # network error: a precise UNKNOWN, never data
            last = e
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            last = RuntimeError(f"http {r.status_code} on attempt {attempt + 1}")
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code != 200:
            return r.status_code, None, f"http {r.status_code}: {r.text[:200]}"
        try:
            return r.status_code, r.json(), None
        except Exception as e:  # unparseable body: UNKNOWN, not a default
            return r.status_code, None, f"{type(e).__name__}: {str(e)[:200]}"
    return 0, None, f"request failed after {FETCH_ATTEMPTS} attempts: {last}"


def get_bytes(session, pace: Pace, headers: dict, url: str) -> tuple[int, bytes, str | None]:
    last: Exception | None = None
    for attempt in range(FETCH_ATTEMPTS):
        pace.wait()
        try:
            r = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT_S)
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            last = RuntimeError(f"http {r.status_code} on attempt {attempt + 1}")
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code != 200:
            return r.status_code, b"", f"http {r.status_code}: {r.text[:200]}"
        return r.status_code, r.content, None
    return 0, b"", f"request failed after {FETCH_ATTEMPTS} attempts: {last}"


# ----- submissions metadata ----------------------------------------------------
def submission_columns() -> tuple[str, ...]:
    return (
        "accessionNumber",
        "filingDate",
        "reportDate",
        "acceptanceDateTime",
        "act",
        "form",
        "fileNumber",
        "filmNumber",
        "items",
        "size",
        "isXBRL",
        "isInlineXBRL",
        "primaryDocument",
        "primaryDocDescription",
    )


def _column(js: dict, key: str) -> list:
    """One submissions column, from ``filings.recent`` or from a linked shard page."""
    node = js.get("filings") if isinstance(js.get("filings"), dict) else js
    if isinstance(node, dict) and isinstance(node.get("recent"), dict):
        node = node["recent"]
    col = node.get(key) if isinstance(node, dict) else None
    return list(col) if isinstance(col, list) else []


def flatten_submissions(js: dict) -> list[dict]:
    """The recent page of one CIK -> one row per submission (verbatim values).

    ``data.sec.gov/submissions/CIK##########.json`` holds the newest entries under
    ``filings.recent`` plus a list of LINKED history pages under
    ``filings.files``; an older linked page returns the same columns at the top
    level of its own document. Both shapes are accepted here, and an unrecognised
    shape yields zero rows rather than a guessed one.
    """
    keys = submission_columns()
    cols = {k: _column(js, k) for k in keys}
    n = max((len(v) for v in cols.values()), default=0)
    rows: list[dict] = []
    for i in range(n):
        rows.append({k: (cols[k][i] if i < len(cols[k]) else None) for k in keys})
    return rows


def linked_submission_files(js: dict) -> list[dict]:
    filings = js.get("filings")
    if not isinstance(filings, dict):
        return []
    files = filings.get("files")
    return (
        [f for f in files if isinstance(f, dict) and f.get("name")]
        if isinstance(files, list)
        else []
    )


def form_class(form: str | None) -> str:
    f = (form or "").strip().upper()
    for label, forms in FORM_CLASSES:
        if f in forms:
            return label
    return "other_form"


def is_amendment(form: str | None) -> bool:
    f = (form or "").strip().upper()
    return f.endswith("/A") or f.endswith(" AM") or "/A" in f


def base_form(form: str | None) -> str | None:
    f = (form or "").strip()
    for sep in ("/A", " AM"):
        if f.upper().endswith(sep):
            return f[: -len(sep)].strip()
    return f or None


def parse_acceptance(value) -> datetime | None:
    """``acceptanceDateTime`` is the ONLY filing timestamp that carries a clock."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo("UTC"))


def acceptance_us(dt: datetime | None) -> int | None:
    return None if dt is None else int(dt.timestamp() * 1_000_000)


def et_date(dt: datetime | None) -> str | None:
    return None if dt is None else dt.astimezone(ET).date().isoformat()


def dissemination_flags(dt: datetime | None, form: str | None) -> dict:
    """SEC's documented dissemination cut-offs, recorded - never applied silently.

    Documented: submissions that BEGIN after 5:30 p.m. ET (10:00 p.m. for ownership
    forms 3/4/5) are disseminated the next business day. "Some", so a flag, not a
    rule that silently shifts the fact's availability.
    """
    if dt is None:
        return {
            "clock_source": "acceptanceDateTime",
            "after_530pm_et": None,
            "after_ownership_10pm_et": None,
            "documented_possible_next_business_day_dissemination": None,
        }
    local = dt.astimezone(ET)
    late = (local.hour, local.minute) >= EVENING_CUTOFF_ET
    ownership = (form or "").strip().upper() in OWNERSHIP_FORMS
    ownership_late = ownership and (local.hour, local.minute) >= OWNERSHIP_CUTOFF_ET
    return {
        "clock_source": "acceptanceDateTime",
        "after_530pm_et": late,
        "after_ownership_10pm_et": ownership_late,
        "documented_possible_next_business_day_dissemination": bool(late or ownership_late),
    }


def normalize_symbol(text) -> str:
    """Exact-token symbol comparison: no fuzzy normalisation is invented."""
    if not text or not isinstance(text, str):
        return ""
    return re.sub(r"\s+", "", text).upper()


# ----- verbatim cover-page extraction ------------------------------------------
def parse_dei_cover(text: str) -> dict:
    """Inline-XBRL DEI cover facts, verbatim, from the ARCHIVED primary document.

    Local names are matched because the DEI namespace differs by taxonomy year
    (``http://xbrl.sec.gov/dei/2019-01-31`` ... ``http://xbrl.sec.gov/dei/2024``).
    A cover with no inline XBRL (pre-2020 filings, some exhibits) yields no facts:
    that is recorded as an UNKNOWN, never as an absent security.

    A multi-class cover tags the same fact once per listing; every occurrence is
    kept under ``<tag>#multi`` so a class mismatch stays visible instead of
    collapsing to a single winner.
    """
    facts: dict[str, dict] = {}
    multi: dict[str, list[dict]] = {}
    for m in _IX_ELEMENT_RE.finditer(text):
        attrs = m.group("attrs") or ""
        name = _IX_NAME_RE.search(attrs)
        if not name:
            continue
        if (name.group("prefix") or "dei").lower() != "dei":
            continue
        tag = name.group("tag")
        if tag not in DEI_COVER_TAGS:
            continue
        raw = _TAG_STRIP_RE.sub(" ", m.group("val") or "")
        value = re.sub(r"\s+", " ", html.unescape(raw)).strip()
        scale_m = _IX_SCALE_RE.search(attrs)
        context_m = _IX_CONTEXTREF_RE.search(attrs)
        record = {
            "value": value,
            "raw": (m.group("val") or "").strip()[:200],
            "scale": int(scale_m.group(1)) if scale_m else None,
            "context_ref": context_m.group(1) if context_m else None,
        }
        if tag not in facts:
            facts[tag] = record
        else:
            multi.setdefault(tag, [facts[tag]]).append(record)
    for tag, records in multi.items():
        facts[f"{tag}#multi"] = {"records": records}
        facts.pop(tag, None)
    return facts


def _scaled_number(value: str, scale: int | None) -> float | None:
    text = (value or "").replace(",", "").replace("$", "").strip()
    if not text:
        return None
    try:
        num = float(text)
    except ValueError:
        return None
    return num * (10**scale) if scale else num


def cover_trading_symbols(facts: dict) -> list[str]:
    """Every as-filed TradingSymbol fact on the cover, de-duplicated, ordered."""
    symbols: list[str] = []
    raw_values: list[str] = []
    direct = facts.get(DEI_SYMBOL_TAG)
    if isinstance(direct, dict):
        raw_values.append(str(direct.get("value") or ""))
    multi = facts.get(f"{DEI_SYMBOL_TAG}#multi")
    if isinstance(multi, dict):
        raw_values.extend(str(r.get("value") or "") for r in multi.get("records", []))
    for value in raw_values:
        sym = normalize_symbol(value)
        if sym and sym not in symbols:
            symbols.append(sym)
    return symbols


# ----- the current ticker -> CIK seed ------------------------------------------
@dataclass(frozen=True)
class SeedMap:
    ticker_to_cik: dict[str, str]
    ticker_to_title: dict[str, str]
    source_url: str
    source_sha256: str
    entries: int
    captured_at: str

    def cik_for(self, ticker: str) -> tuple[str | None, str | None]:
        sym = normalize_symbol(ticker)
        return self.ticker_to_cik.get(sym), self.ticker_to_title.get(sym)


def normalize_company_tickers(payload: object) -> tuple[dict[str, str], dict[str, str]]:
    """company_tickers.json -> (ticker -> padded CIK, ticker -> title).

    Shape: ``{"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}``.
    A file with any other shape yields an EMPTY map (every ticker then becomes an
    explicit UNKNOWN), never a guessed mapping.
    """
    cik: dict[str, str] = {}
    title: dict[str, str] = {}
    if not isinstance(payload, dict):
        return cik, title
    for entry in payload.values():
        if not isinstance(entry, dict):
            continue
        sym = normalize_symbol(entry.get("ticker"))
        if not sym or entry.get("cik_str") is None:
            continue
        cik.setdefault(sym, cik_padded(entry["cik_str"]))
        if entry.get("title"):
            title.setdefault(sym, str(entry["title"]))
    return cik, title


def load_seed_map(
    session, pace: Pace, headers: dict, out_dir: Path
) -> tuple[SeedMap | None, str | None]:
    """The current ticker index: ONE request, the verbatim bytes on disk, sha recorded.

    A previously captured copy is reused (it is the same current index, and its sha
    is part of the seed manifest), so a resumed run does not re-request it.
    """
    root = out_dir / "raw"
    raw_path = root / "company_tickers.json"
    manifest_path = root / "company_tickers.manifest.json"
    manifest = read_json(manifest_path)
    if manifest is None or not raw_path.exists():
        status, body, error = get_bytes(session, pace, headers, SEC_COMPANY_TICKERS_URL)
        if status != 200 or not body:
            return None, f"seed request failed ({status}): {error}"
        write_bytes_atomic(raw_path, body)
        manifest = {
            "source_url": SEC_COMPANY_TICKERS_URL,
            "bytes_sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
            "captured_at": now_iso(),
            "note": (
                "www.sec.gov/files/company_tickers.json is a CURRENT ticker index; it "
                "seeds discovery only and never proves a historical association"
            ),
        }
        write_json_atomic(manifest_path, manifest)
    payload = None
    try:
        payload = json.loads(raw_path.read_bytes())
    except (json.JSONDecodeError, OSError):
        payload = None
    ticker_cik, ticker_title = normalize_company_tickers(payload)
    return (
        SeedMap(
            ticker_to_cik=ticker_cik,
            ticker_to_title=ticker_title,
            source_url=str(manifest.get("source_url")),
            source_sha256=str(manifest.get("bytes_sha256")),
            entries=len(ticker_cik),
            captured_at=str(manifest.get("captured_at")),
        ),
        None,
    )


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S%z")


# ----- per-issuer collection ---------------------------------------------------
def issuer_manifest_path(out_dir: Path, ticker: str) -> Path:
    return out_dir / "manifest" / "issuer" / f"{ticker}.json"


def issuer_resume_hash(ticker: str, cik: str | None, floor: str, producer: str) -> str:
    return digest_text(
        json.dumps(
            {
                "ticker": ticker,
                "cik": cik,
                "floor": floor,
                "producer": producer,
                "schema": MANIFEST_SCHEMA,
                "version": VERSION,
                "concepts": [DEI_SYMBOL_TAG, DEI_SHARES_TAG],
            },
            sort_keys=True,
        )
    )


def concept_facts(payload: object, tag: str) -> list[dict]:
    """companyconcept units[] -> dated, accession-provenanced as-filed facts.

    ``filed`` is a DATE-ONLY field: it is kept verbatim and flagged as such. The
    intraday clock is resolved later from that accession's submission row.
    """
    if not isinstance(payload, dict):
        return []
    units = payload.get("units")
    if not isinstance(units, dict):
        return []
    facts: list[dict] = []
    for unit_name, rows in units.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            facts.append(
                {
                    "tag": tag,
                    "unit": unit_name,
                    "value": row.get("val"),
                    "end": row.get("end"),
                    "start": row.get("start"),
                    "filed": row.get("filed"),
                    "form": row.get("form"),
                    "accn": row.get("accn"),
                    "fy": row.get("fy"),
                    "fp": row.get("fp"),
                    "frame": row.get("frame"),
                }
            )
    facts.sort(key=lambda f: (str(f.get("filed") or ""), str(f.get("accn") or "")))
    return facts


def concept_status(status: int, payload: object, error: str | None) -> tuple[str, int]:
    if status == 200 and isinstance(payload, dict):
        return "complete", 0
    # A CIK that never tagged this DEI concept returns 404; an unreachable host, a
    # rate limit or a hard failure is a genuine UNKNOWN and must stay distinguishable.
    if status == 404:
        return "not_reported_for_this_cik", status
    if status == 403:
        return "unknown_forbidden_check_user_agent", status
    return "unknown_fetch_failed", status


def fetch_submissions(
    session, pace: Pace, headers: dict, cik: str, floor: str, out_dir: Path
) -> dict:
    """The FULL filing history of one CIK: recent page + every linked older page.

    ``filings.recent`` only holds the newest submissions; ``filings.files`` links the
    older ones, and EVERY linked page whose own ``filingTo`` is at or after the
    declared association floor is fetched. There is deliberately no page cap: a fixed
    bound would silently truncate the relevant history of an issuer that has more
    linked pages than that bound. Pages entirely older than the floor are recorded by
    name as skipped instead of silently dropped; a link with no comparable
    ``filingTo`` is followed rather than assumed old. Every page is requested at most
    once, so a repeated link or one that points back at an already-fetched or
    already-queued page is never fetched twice. A page that cannot be retrieved leaves
    its own URL and every still-queued page on the record as an UNKNOWN, never as an
    empty history. Only rows with ``filingDate >= floor`` are persisted, and how many
    were dropped is recorded.
    """
    step: dict = {
        "status": "pending",
        "pages": 0,
        "shards_skipped_before_floor": 0,
        "shards_skipped_before_floor_names": [],
    }
    all_rows: list[dict] = []
    queue: list[str] = [submissions_url(cik)]
    seen_urls = {submissions_url(cik)}
    fetched: list[tuple[str, dict]] = []
    http = 0
    error = None
    failed_url: str | None = None
    while queue:
        url = queue.pop(0)
        status, payload, err = get_json(session, pace, headers, url)
        step["pages"] += 1
        http = status
        if status != 200 or not isinstance(payload, dict):
            error = err
            failed_url = url
            break
        all_rows.extend(flatten_submissions(payload))
        fetched.append((url, payload))
        write_bytes_atomic(
            page_path(out_dir, url, cik, step["pages"]),
            json.dumps(payload, sort_keys=True).encode(),
        )
        for link in linked_submission_files(payload):
            name = str(link.get("name"))
            filing_to = str(link.get("filingTo") or "")
            if filing_to and filing_to < floor:
                # affirmatively older than the floor: recorded, never requested
                step["shards_skipped_before_floor"] += 1
                step["shards_skipped_before_floor_names"].append(name)
                continue
            shard = shard_url(name)
            if shard in seen_urls:
                continue  # a repeated or backwards link: never request a page twice
            seen_urls.add(shard)
            queue.append(shard)
    if failed_url is not None:
        # what was NOT retrieved stays visible: the failed page plus every relevant
        # page still queued behind it, so a failed shard is never read as "no events"
        step["failed_url"] = failed_url
        step["pages_not_retrieved"] = list(queue)
    if not fetched:
        step.update(
            {
                "status": "unknown_fetch_failed",
                "http_status": http,
                "error": (error or "")[:200],
                "pages": step["pages"],
                "rows_total": 0,
            }
        )
        return {**step, "rows": []}
    kept = [normalize_filing(r) for r in all_rows if str(r.get("filingDate") or "") >= floor]
    step.update(
        {
            "status": "complete" if error is None else "partial_unknown",
            "http_status": http,
            "url": submissions_url(cik),
            "error": (error or "")[:200] if error else None,
            "rows_total": len(all_rows),
            "rows_persisted": len(kept),
            "association_floor": floor,
            "rows_dropped_before_floor": len(all_rows) - len(kept),
            "full_history_linked_pages_followed": step["pages"] > 1,
        }
    )
    return {**step, "rows": kept}


def normalize_filing(row: dict) -> dict:
    """One verbatim submission row, normalised for the manifest (no value invented)."""
    form = row.get("form")
    return {
        "accessionNumber": row.get("accessionNumber"),
        "filingDate": row.get("filingDate"),
        "reportDate": row.get("reportDate"),
        "acceptanceDateTime": row.get("acceptanceDateTime"),
        "form": form,
        "form_class": form_class(form),
        "is_amendment": is_amendment(form),
        "base_form": base_form(form),
        "items": row.get("items") or None,
        "primaryDocument": row.get("primaryDocument") or None,
        "primaryDocDescription": row.get("primaryDocDescription") or None,
        "isXBRL": row.get("isXBRL"),
        "isInlineXBRL": row.get("isInlineXBRL"),
    }


def page_path(out_dir: Path, url: str, cik: str, page: int) -> Path:
    """Verbatim submissions page location, derived from the request URL."""
    stem = Path(url).stem
    return out_dir / "raw" / "submissions" / f"{cik_padded(cik)}-{stem}-p{page}.json"


def fetch_concept(session, pace: Pace, headers: dict, cik: str, tag: str, out_dir: Path) -> dict:
    status, payload, error = get_json(session, pace, headers, concept_url(cik, tag))
    label, http = concept_status(status, payload, error)
    step: dict = {
        "status": label,
        "http_status": http,
        "url": concept_url(cik, tag),
        "facts": 0,
    }
    if isinstance(payload, dict):
        step["source_sha256"] = digest_text(json.dumps(payload, sort_keys=True))
        facts = concept_facts(payload, tag)
        step["facts"] = len(facts)
        step["rows"] = facts
        write_bytes_atomic(
            out_dir / "raw" / "concept" / f"{cik_padded(cik)}-{tag}.json",
            json.dumps(payload, sort_keys=True).encode(),
        )
    elif label == "unknown_fetch_failed":
        step["error"] = (error or "")[:200]
    return step


# ----- verbatim archived filing retrieval --------------------------------------
def cover_summary(facts: dict) -> dict:
    """The as-filed cover facts a consumer can actually left-join on."""
    symbols = cover_trading_symbols(facts)
    titles: list[str] = []
    for tag in ("Security12bTitle", "Security12gTitle"):
        direct = facts.get(tag)
        if isinstance(direct, dict) and str(direct.get("value") or "").strip():
            titles.append(str(direct["value"]).strip())
        multi = facts.get(f"{tag}#multi")
        if isinstance(multi, dict):
            titles.extend(
                str(r.get("value") or "").strip()
                for r in multi.get("records", [])
                if r.get("value")
            )
    shares = facts.get(DEI_SHARES_TAG)
    pubfloat = facts.get(DEI_FLOAT_TAG)
    return {
        "trading_symbols_as_filed": symbols,
        # SECURITY class titles (e.g. "Common Stock, $0.0001 par value"), NOT tickers.
        "security_class_titles_as_filed": titles,
        "exchange_as_filed": (
            str(facts["SecurityExchangeName"]["value"]).strip()
            if isinstance(facts.get("SecurityExchangeName"), dict)
            else None
        ),
        "issuer_name_as_filed": (
            str(facts["EntityRegistrantName"]["value"]).strip()
            if isinstance(facts.get("EntityRegistrantName"), dict)
            else None
        ),
        "issuer_cik_as_filed": (
            str(facts["EntityCentralIndexKey"]["value"]).strip()
            if isinstance(facts.get("EntityCentralIndexKey"), dict)
            else None
        ),
        "entity_file_number_as_filed": (
            str(facts["EntityFileNumber"]["value"]).strip()
            if isinstance(facts.get("EntityFileNumber"), dict)
            else None
        ),
        "amendment_flag_as_filed": (
            str(facts["AmendmentFlag"]["value"]).strip()
            if isinstance(facts.get("AmendmentFlag"), dict)
            else None
        ),
        "document_type_as_filed": (
            str(facts["DocumentType"]["value"]).strip()
            if isinstance(facts.get("DocumentType"), dict)
            else None
        ),
        "no_trading_symbol_flag_as_filed": (
            str(facts["NoTradingSymbolFlag"]["value"]).strip()
            if isinstance(facts.get("NoTradingSymbolFlag"), dict)
            else None
        ),
        # Shares outstanding exactly as filed. This is NEVER a free-float share count.
        "shares_outstanding_as_filed": (
            _scaled_number(str(shares["value"]), shares.get("scale"))
            if isinstance(shares, dict)
            else None
        ),
        "shares_outstanding_raw_text_as_filed": (
            str(shares["value"]) if isinstance(shares, dict) else None
        ),
        "shares_outstanding_scale_as_filed": (
            shares.get("scale") if isinstance(shares, dict) else None
        ),
        # A monetary public float, kept in its own field and never converted to shares.
        "public_float_usd_as_filed": (
            _scaled_number(str(pubfloat["value"]), pubfloat.get("scale"))
            if isinstance(pubfloat, dict)
            else None
        ),
        "inline_xbrl_dei_cover_present": bool(facts),
    }


def retrieve_archive(
    session, pace: Pace, headers: dict, out_dir: Path, cik: str, row: dict
) -> dict:
    """Fetch the ARCHIVED primary document verbatim and extract its DEI cover."""
    accession = str(row.get("accessionNumber") or "")
    form = str(row.get("form") or "")
    primary = str(row.get("primaryDocument") or "").strip()
    record = {
        "accession": accession,
        "form": form,
        "form_class": form_class(form),
        "is_amendment": is_amendment(form),
        "base_form": base_form(form),
        "acceptance_datetime": row.get("acceptanceDateTime"),
        "filing_date": row.get("filingDate"),
        "report_date": row.get("reportDate"),
        "primary_document": primary or None,
        "status": "pending",
        "http_status": None,
        "public_first_visibility_lag": "unknown",
        **dissemination_flags(parse_acceptance(row.get("acceptanceDateTime")), form),
    }
    if not primary:
        record.update({"status": "unknown_no_primary_document"})
        return record
    url = archive_url(cik, accession, primary)
    record["source_url"] = url
    status, body, error = get_bytes(session, pace, headers, url)
    record["http_status"] = status
    record["captured_at"] = now_iso()
    if status != 200 or not body:
        record.update({"status": "unknown_fetch_failed", "error": (error or "")[:200]})
        return record
    record["raw_sha256"] = hashlib.sha256(body).hexdigest()
    record["raw_bytes"] = len(body)
    record["raw_stored"] = len(body) <= MAX_ARCHIVE_BYTES
    if not record["raw_stored"]:
        record["raw_storage_note"] = (
            f"primary document larger than the {MAX_ARCHIVE_BYTES}-byte verbatim "
            "cap: the sha256 and the as-filed cover facts are kept, the bytes are not"
        )
    text = body.decode("utf-8", "replace")
    facts = parse_dei_cover(text)
    record["dei_cover"] = dict(sorted(facts.items()))
    record.update(cover_summary(facts))
    record["status"] = "retrieved"
    if record["raw_stored"]:
        dest = out_dir / "raw" / "archive" / cik_padded(cik) / f"{accession}{Path(primary).suffix}"
        write_bytes_atomic(dest, body)
        record["raw_path"] = str(dest)
    return record


def archive_targets(
    rows: list[dict],
    symbol_facts: list[dict],
    scope_days: set[str],
    earliest_clock: int | None,
    limit: int,
) -> list[dict]:
    """The verbatim covers worth retrieving, in priority order, bounded per issuer.

    1) the dated submissions whose as-filed cover is the PIT association proof;
    2) the in-scope filings of the archivable form classes, oldest acceptance first.
    """
    by_accession = {str(r.get("accessionNumber") or ""): r for r in rows}
    ordered: list[dict] = []
    seen: set[str] = set()

    def push(accn: str) -> None:
        row = by_accession.get(accn)
        if row is None or accn in seen:
            return
        if form_class(row.get("form")) not in ARCHIVE_FORM_CLASSES:
            return
        seen.add(accn)
        ordered.append(row)

    proven = sorted(
        (f for f in symbol_facts if f.get("acceptance_us") is not None),
        key=lambda f: (f["acceptance_us"], str(f.get("accn"))),
    )
    if earliest_clock is not None:
        proofs = [f for f in proven if f["acceptance_us"] <= earliest_clock]
        proofs.reverse()  # the latest cover accepted before the earliest clock first
        for fact in proofs:
            push(str(fact.get("accn") or ""))
    scored: list[tuple[str, dict]] = []
    for row in rows:
        if str(row.get("accessionNumber") or "") in seen:
            continue
        if form_class(row.get("form")) not in ARCHIVE_FORM_CLASSES:
            continue
        dt = parse_acceptance(row.get("acceptanceDateTime"))
        day = et_date(dt)
        if day is None or day not in scope_days:
            continue
        scored.append((day, row))
    scored.sort(key=lambda kv: (kv[0], str(kv[1].get("accessionNumber") or "")))
    for _, row in scored:
        push(str(row.get("accessionNumber") or ""))
    return ordered[:limit]


def build_symbol_series(symbol_step: dict, filings: list[dict], archives: list[dict]) -> list[dict]:
    """Dated as-filed TradingSymbol evidence from BOTH sources, one row per fact.

    ``acceptance_us`` is only set when the accession was actually matched to a
    submission row (``acceptanceDateTime`` is the only filing timestamp with a
    clock; the XBRL fact's own ``filed`` is DATE-ONLY). A fact with no resolved
    acceptance is kept for the record and can never prove a before-clock
    association.
    """
    acceptance = {
        str(f.get("accessionNumber") or ""): f for f in filings if f.get("acceptanceDateTime")
    }

    series: dict[tuple[str, str], dict] = {}

    def add(source: str, symbol: str, row: dict, extra: dict) -> None:
        accn = str(row.get("accn") or row.get("accessionNumber") or "")
        key = (accn, symbol)
        if key in series:
            return
        dt = parse_acceptance(row.get("acceptanceDateTime"))
        series[key] = {
            "source": source,
            "symbol": symbol,
            "accession": accn,
            "form": row.get("form"),
            "form_class": form_class(row.get("form")),
            "is_amendment": is_amendment(row.get("form")),
            "filed": row.get("filed") or row.get("filingDate"),
            "acceptance_datetime": row.get("acceptanceDateTime"),
            "acceptance_us": acceptance_us(dt),
            "public_first_visibility_lag": "unknown",
            **extra,
        }

    for fact in symbol_step.get("rows") or []:
        symbol = normalize_symbol(fact.get("value"))
        if not symbol:
            continue
        row = dict(fact)
        matched = acceptance.get(str(fact.get("accn") or ""))
        if matched:
            row["acceptanceDateTime"] = matched.get("acceptanceDateTime")
            row["form"] = matched.get("form")
            row["filingDate"] = matched.get("filingDate")
        add(
            "sec_companyconcept_dei_TradingSymbol",
            symbol,
            row,
            {"fact_unit": fact.get("unit"), "fact_end": fact.get("end")},
        )
    for doc in archives:
        if doc.get("status") != "retrieved":
            continue
        row = {
            "accn": doc.get("accession"),
            "accessionNumber": doc.get("accession"),
            "acceptanceDateTime": doc.get("acceptance_datetime"),
            "form": doc.get("form"),
        }
        for symbol in doc.get("trading_symbols_as_filed") or []:
            add("sec_archive_primary_document", normalize_symbol(symbol), row, {})
    out = [v for v in series.values() if v["acceptance_us"] is not None]
    out.sort(key=lambda v: (v["acceptance_us"], v["accession"], v["symbol"]))
    return out


def share_fact_by_accession(share_step: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for fact in share_step.get("rows") or []:
        accn = str(fact.get("accn") or "")
        if not accn:
            continue
        # Same accession may repeat (periodic + amendment): the NEWEST filed wins.
        if accn in out and str(out[accn].get("filed") or "") >= str(fact.get("filed") or ""):
            continue
        out[accn] = fact
    return out


# ----- per-issuer assembly -----------------------------------------------------
def issuer_events(
    ticker: str,
    cik: str | None,
    days: set[str],
    rows: list[dict],
    share_by_accn: dict[str, dict],
    archives: list[dict],
) -> list[dict]:
    """Filing / issuance / registration events for THIS name on ITS panel days only.

    A filing is an event for this producer only when its acceptance falls on one of
    the ticker's own causal-PIT decision days, so the artifact never grows past the
    panel's (day, ticker) universe.
    """
    archive_by_accn = {str(a.get("accession")): a for a in archives}
    events: list[dict] = []
    for row in rows:
        dt = parse_acceptance(row.get("acceptanceDateTime"))
        day = et_date(dt)
        if day is None or day not in days:
            continue
        accn = str(row.get("accessionNumber") or "")
        form = str(row.get("form") or "")
        share = share_by_accn.get(accn)
        doc = archive_by_accn.get(accn)
        concept_shares = (
            _scaled_number(str(share["value"]), None) if isinstance(share, dict) else None
        )
        cover_shares = (doc or {}).get("shares_outstanding_as_filed")
        shares = concept_shares if concept_shares is not None else cover_shares
        event = {
            # scope keys - the leftjoin unit
            "day": day,
            "ticker": ticker,
            "cik": cik,
            # verbatim submissions metadata
            "accession": accn,
            "form": form,
            "form_class": form_class(form),
            "is_amendment": is_amendment(form),
            "base_form": base_form(form),
            "items": row.get("items") or None,
            "acceptance_datetime": row.get("acceptanceDateTime"),
            "acceptance_et": dt.astimezone(ET).isoformat() if dt else None,
            "filing_date": row.get("filingDate"),
            "report_date": row.get("reportDate"),
            "primary_document": row.get("primaryDocument") or None,
            "primary_document_url": (
                archive_url(cik, accn, str(row.get("primaryDocument") or ""))
                if cik and row.get("primaryDocument")
                else None
            ),
            # availability: acceptance carries a clock, filing/report dates do NOT
            "availability": {
                "clock_source": "acceptanceDateTime",
                "acceptance_timestamp_available": dt is not None,
                "date_only_fields": ["filingDate", "reportDate"],
                "public_first_visibility_lag": "unknown",
            },
            **dissemination_flags(dt, form),
            # as-filed cover facts (shares outstanding is NEVER a free-float count)
            "shares_outstanding_as_filed": shares,
            "shares_outstanding_source": (
                "sec_companyconcept_dei_EntityCommonStockSharesOutstanding"
                if concept_shares is not None
                else "sec_archive_primary_document"
                if cover_shares is not None
                else None
            ),
            "shares_outstanding_as_of": (share.get("end") if isinstance(share, dict) else None),
            "shares_filed_date_only": (share.get("filed") if isinstance(share, dict) else None),
            "shares_clock_source": "filed_date_only_not_intraday",
            "public_float_usd_as_filed": None,
            # verbatim archive provenance (class title + exchange, not in XBRL frames)
            "archive_status": (doc or {}).get("status", "not_retrieved"),
            "archive_source_url": (doc or {}).get("source_url"),
            "archive_raw_sha256": (doc or {}).get("raw_sha256"),
            "archive_captured_at": (doc or {}).get("captured_at"),
            "cover_trading_symbols_as_filed": (doc or {}).get("trading_symbols_as_filed", []),
            "cover_security_class_titles_as_filed": (doc or {}).get(
                "security_class_titles_as_filed", []
            ),
            "cover_exchange_as_filed": (doc or {}).get("exchange_as_filed"),
            "cover_issuer_name_as_filed": (doc or {}).get("issuer_name_as_filed"),
            "public_first_visibility_lag": "unknown",
        }
        if isinstance(doc, dict):
            event["public_float_usd_as_filed"] = doc.get("public_float_usd_as_filed")
        events.append(event)
    events.sort(key=lambda e: (e["day"], e["accession"]))
    return events


def step_done(status: str | None) -> bool:
    """A step that is finished and needs no request on resume."""
    return status in ("complete", "not_reported_for_this_cik", "skipped_no_cik_seed")


def process_issuer(
    ticker: str,
    scope_days: tuple[str, ...],
    earliest_clock: int | None,
    out_dir: Path,
    seed: SeedMap,
    session,
    pace: Pace,
    headers: dict,
    floor: str,
    user_agent_sha256: str,
    user_agent_len: int,
    resume: bool,
    producer: str,
) -> dict:
    """One issuer: submissions history, dated DEI facts, verbatim covers, events.

    Resume is per STEP and per ARCHIVE PAGE, not per issuer: a submissions page, a
    concept page and each verbatim cover already on disk are reused, and only the
    steps and documents that ended in an UNKNOWN are requested again.
    """
    path = issuer_manifest_path(out_dir, ticker)
    cik, title = seed.cik_for(ticker)
    resume_hash = issuer_resume_hash(ticker, cik, floor, producer)
    prior = read_json(path) if resume else None
    reuse = prior if (prior is not None and prior.get("resume_hash") == resume_hash) else None
    if reuse is not None and reuse.get("status") == NO_CIK_SEED:
        return reuse

    manifest: dict = {
        "ticker": ticker,
        "schema": MANIFEST_SCHEMA,
        "producer_sha256": producer,
        "resume_hash": resume_hash,
        "cik": cik,
        "seed_title": title,
        "seed_map_source": seed.source_url,
        "cik_status": "mapped" if cik else "unknown_no_cik_seed",
        "association_floor": floor,
        "association_earliest_clock_us": earliest_clock,
        "scope_days": list(scope_days),
        "user_agent_sha256": user_agent_sha256,
        "user_agent_len": user_agent_len,
        "status": "pending",
        "steps": {},
        "filings": [],
        "symbol_facts": [],
        "share_facts": [],
        "archive": [],
        "association_series": [],
        "events": [],
        "note": (
            "company_tickers.json is a CURRENT ticker index used as a discovery seed "
            "only; association is asserted solely from dated as-filed cover evidence"
        ),
    }
    if cik is None:
        manifest["steps"] = {
            "submissions": {"status": "skipped_no_cik_seed", "pages": 0},
            f"concept_{DEI_SYMBOL_TAG}": {"status": "skipped_no_cik_seed", "facts": 0},
            f"concept_{DEI_SHARES_TAG}": {"status": "skipped_no_cik_seed", "facts": 0},
            "archive": {"status": "skipped_no_cik_seed", "documents": 0},
        }
        manifest["status"] = NO_CIK_SEED
        write_json_atomic(path, manifest)
        return manifest

    # 1) the full, linked filing history of this CIK
    prior_steps = (reuse or {}).get("steps", {})
    if step_done(prior_steps.get("submissions", {}).get("status")):
        manifest["filings"] = list((reuse or {}).get("filings") or [])
        manifest["steps"]["submissions"] = {
            **prior_steps["submissions"],
            "resumed": True,
            "rows_persisted": len(manifest["filings"]),
        }
    else:
        subs = fetch_submissions(session, pace, headers, cik, floor, out_dir)
        manifest["steps"]["submissions"] = {k: v for k, v in subs.items() if k != "rows"}
        manifest["filings"] = subs["rows"]

    # 2) the dated as-filed DEI fact series (shares outstanding and trading symbol)
    for tag in (DEI_SYMBOL_TAG, DEI_SHARES_TAG):
        key = f"concept_{tag}"
        prior_step = prior_steps.get(key, {})
        if step_done(prior_step.get("status")):
            cached_facts = (reuse or {}).get(
                "symbol_facts" if tag == DEI_SYMBOL_TAG else "share_facts"
            )
            facts = list(cached_facts or [])
            manifest["steps"][key] = {**prior_step, "resumed": True, "facts": len(facts)}
        else:
            fetched = fetch_concept(session, pace, headers, cik, tag, out_dir)
            facts = fetched.get("rows") or []
            manifest["steps"][key] = {k: v for k, v in fetched.items() if k != "rows"}
        if tag == DEI_SYMBOL_TAG:
            manifest["symbol_facts"] = facts
        else:
            manifest["share_facts"] = facts

    # 3) the verbatim archived primary documents that matter for this name
    prior_archive = {str(doc.get("accession")): doc for doc in ((reuse or {}).get("archive") or [])}
    archives: list[dict] = []
    targets = archive_targets(
        manifest["filings"],
        manifest["symbol_facts"],
        set(scope_days),
        earliest_clock,
        MAX_ARCHIVE_PER_ISSUER,
    )
    for row in targets:
        accn = str(row.get("accessionNumber") or "")
        cached = prior_archive.get(accn)
        if cached is not None and cached.get("status") in (
            "retrieved",
            "unknown_no_primary_document",
            "unknown_too_large_not_stored",
        ):
            archives.append({**cached, "resumed": True})
            continue
        archives.append(retrieve_archive(session, pace, headers, out_dir, cik, row))
    manifest["archive"] = archives
    if not archives:
        archive_status = "no_archivable_filing_in_scope"
    elif all(a["status"] == "retrieved" for a in archives):
        archive_status = "complete"
    else:
        archive_status = "partial_unknown"
    manifest["steps"]["archive"] = {
        "status": archive_status,
        "documents": len(archives),
        "retrieved": sum(1 for a in archives if a["status"] == "retrieved"),
        "unknown": [a["accession"] for a in archives if a["status"] != "retrieved"],
        "note": (
            "verbatim cover retrieval is bounded per issuer; the dated DEI concept "
            "series above is the complete as-filed fact record for this CIK"
        ),
    }

    manifest["association_series"] = build_symbol_series(
        {"rows": manifest["symbol_facts"]}, manifest["filings"], archives
    )
    manifest["events"] = issuer_events(
        ticker,
        cik,
        set(scope_days),
        manifest["filings"],
        share_fact_by_accession({"rows": manifest["share_facts"]}),
        archives,
    )
    statuses = [
        manifest["steps"]["submissions"]["status"],
        manifest["steps"][f"concept_{DEI_SYMBOL_TAG}"]["status"],
        manifest["steps"][f"concept_{DEI_SHARES_TAG}"]["status"],
    ]
    if any(s == "unknown_fetch_failed" for s in statuses):
        manifest["status"] = FETCH_FAILED
    elif any(a["status"] != "retrieved" for a in archives):
        manifest["status"] = "partial_unknown"
    else:
        manifest["status"] = "complete"
    manifest["updated_at"] = now_iso()
    write_json_atomic(path, manifest)
    return manifest


# ----- the point-in-time association verifier ----------------------------------
def association_for(ticker: str, clock: int, manifest: dict) -> dict:
    """Was TRADER's symbol on THIS issuer's as-filed cover before THIS clock?

    The answer is only ever one of: a dated filing proof, or an explicit UNKNOWN
    reason. A CIK seeded from the current ticker index is not evidence; a ticker the
    current index no longer lists is not a market fact.
    """
    base = {
        "ticker": ticker,
        "trade_clock_utc_us": clock,
        "trade_clock_utc": datetime.fromtimestamp(clock / 1_000_000, UTC).isoformat(),
        "cik": manifest.get("cik"),
        "seed_map_source": manifest.get("seed_map_source"),
        "association_floor": manifest.get("association_floor"),
        "public_first_visibility_lag": "unknown",
    }
    if manifest.get("cik_status") == "unknown_no_cik_seed":
        return {
            **base,
            "status": NO_CIK_SEED,
            "reason": (
                "ticker absent from the current SEC company_tickers index; no CIK "
                "could be seeded, so no historical association can be asserted"
            ),
        }
    series = manifest.get("association_series") or []
    wanted = normalize_symbol(ticker)
    before = [e for e in series if int(e["acceptance_us"]) <= clock]
    archive_by_accn = {str(a.get("accession")): a for a in manifest.get("archive") or []}
    if before:
        latest = before[-1]
        if latest["symbol"] == wanted:
            proof = {k: latest[k] for k in sorted(latest)}
            doc = archive_by_accn.get(str(latest["accession"]))
            if doc:
                proof["cover"] = {
                    "source_url": doc.get("source_url"),
                    "raw_sha256": doc.get("raw_sha256"),
                    "raw_bytes": doc.get("raw_bytes"),
                    "captured_at": doc.get("captured_at"),
                    "issuer_name_as_filed": doc.get("issuer_name_as_filed"),
                    "security_class_titles_as_filed": doc.get("security_class_titles_as_filed"),
                    "exchange_as_filed": doc.get("exchange_as_filed"),
                    "entity_file_number_as_filed": doc.get("entity_file_number_as_filed"),
                    "amendment_flag_as_filed": doc.get("amendment_flag_as_filed"),
                    "shares_outstanding_as_filed": doc.get("shares_outstanding_as_filed"),
                    "public_first_visibility_lag": "unknown",
                    **dissemination_flags(
                        parse_acceptance(doc.get("acceptance_datetime")), doc.get("form")
                    ),
                }
            return {
                **base,
                "status": ASSOCIATED,
                "evidence_source": latest["source"],
                "proof": proof,
            }
        return {
            **base,
            "status": NO_EVIDENCE_BEFORE_CLOCK,
            "reason": (
                "the latest as-filed TradingSymbol accepted at or before this clock "
                f"is {latest['symbol']!r}, not {ticker!r}; the seeded CIK does not "
                "prove this symbol on this date"
            ),
            "latest_symbol_before_clock": latest["symbol"],
        }
    covers = []
    for doc in manifest.get("archive") or []:
        doc_clock = acceptance_us(parse_acceptance(doc.get("acceptance_datetime")))
        if doc_clock is not None and doc_clock <= clock:
            covers.append(doc)
    if covers and not any(c.get("trading_symbols_as_filed") for c in covers):
        return {
            **base,
            "status": NO_SYMBOL_FACT,
            "reason": (
                "an as-filed cover accepted before this clock was retrieved verbatim "
                "and carries no inline-XBRL dei:TradingSymbol fact; the symbol is "
                "UNKNOWN, not absent"
            ),
            "retrieved_covers": [c["accession"] for c in covers],
        }
    return {
        **base,
        "status": NO_EVIDENCE_BEFORE_CLOCK,
        "reason": (
            "no dated as-filed TradingSymbol evidence accepted at or before this "
            "clock was retrieved for the seeded CIK (association floor "
            f"{manifest.get('association_floor')})"
        ),
    }


def day_resume_hash(day: str, tickers: tuple[str, ...], producer: str) -> str:
    return digest_text(
        json.dumps(
            {
                "day": day,
                "tickers": list(tickers),
                "producer": producer,
                "schema": MANIFEST_SCHEMA,
                "version": VERSION,
            },
            sort_keys=True,
        )
    )


def write_day_outputs(
    out_dir: Path,
    day: str,
    pairs: list[tuple[str, int]],
    manifests: dict[str, dict],
    producer: str,
    events: list[dict] | None = None,
) -> dict:
    """Per-day atomic + resumable association / UNKNOWN matrix and event rows."""
    tickers = tuple(sorted(t for t, _ in pairs))
    digest = day_resume_hash(day, tickers, producer)
    assoc_path = out_dir / "association" / f"{day}.json"
    prior = read_json(assoc_path)
    if prior is not None and prior.get("resume_hash") == digest:
        rows = prior.get("association") or []
    else:
        rows = [association_for(ticker, clock, manifests[ticker]) for ticker, clock in pairs]
        write_json_atomic(
            assoc_path,
            {
                "day": day,
                "schema": MANIFEST_SCHEMA,
                "producer_sha256": producer,
                "resume_hash": digest,
                "kind": "SEC_ISSUER_ASSOCIATION_MATRIX_NOT_ALPHA_RESULTS",
                "tickers": list(tickers),
                "association": rows,
                "status_counts": _counts(rows, "status"),
                "note": (
                    "a positive row is dated as-filed DEI TradingSymbol evidence "
                    "accepted at or before that (ticker, day) trade clock; every "
                    "other status is an explicit UNKNOWN reason, never a claim that "
                    "no filing or no event exists"
                ),
            },
        )
    events = list(events or [])
    events.sort(key=lambda e: (e["ticker"], e["accession"]))
    events_path = out_dir / "events" / f"{day}.json"
    write_json_atomic(
        events_path,
        {
            "day": day,
            "schema": MANIFEST_SCHEMA,
            "producer_sha256": producer,
            "kind": "SEC_ISSUER_EVENT_METADATA_NOT_ALPHA_RESULTS",
            "events": events,
            "form_class_counts": _counts(events, "form_class"),
            "note": (
                "event metadata only: acceptance timestamps are SEC acceptances with "
                "an unknown public first-visibility lag, and filingDate/reportDate "
                "are date-only; no price or outcome is attached"
            ),
        },
    )
    return {
        "day": day,
        "association": str(assoc_path),
        "events": str(events_path),
        "tickers": len(rows),
        "association_status_counts": _counts(rows, "status"),
        "form_class_counts": _counts(events, "form_class"),
    }


def _counts(rows: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key))
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items()))


def _merge_counts(buckets) -> dict[str, int]:
    out: dict[str, int] = {}
    for bucket in buckets:
        for key, value in (bucket or {}).items():
            out[key] = out.get(key, 0) + int(value)
    return dict(sorted(out.items()))


# ----- the run loop ------------------------------------------------------------
def run(args: argparse.Namespace) -> None:
    t0 = time.time()
    out = args.out or OUTPUT
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")
    producer = producer_sha256()

    days = calendar_days(args.days, args.blocks.split(","))
    if not days:
        raise SystemExit("[sec-issuer-events] no allowed panel day in the requested blocks")
    guard_days(days)  # BEFORE any panel read, network read or API call
    universe = panel_pairs(days)
    floor = (
        (datetime.fromisoformat(days[0]) - timedelta(days=ASSOCIATION_LOOKBACK_DAYS))
        .date()
        .isoformat()
    )
    days_by_ticker = universe.by_ticker()
    tickers = universe.tickers
    if args.limit_issuers:
        tickers = tickers[: args.limit_issuers]
    keep = set(tickers)

    write_json_atomic(
        out / "plan" / "universe.json",
        {
            "kind": "SEC_ISSUER_EVENTS_UNIVERSE_NOT_ALPHA_RESULTS",
            "schema": MANIFEST_SCHEMA,
            "producer_sha256": producer,
            "planned_before_any_network_read": True,
            "blocks": args.blocks.split(","),
            "days": list(universe.days),
            "association_floor": floor,
            "association_lookback_days": ASSOCIATION_LOOKBACK_DAYS,
            "arrival_latency_ms": ARRIVAL_LATENCY_MS,
            "intent_minute_offset": INTENT_MINUTE_OFFSET,
            "panel_columns_projected": list(PANEL_COLUMNS),
            "pair_count": len(universe.clock),
            "ticker_count": len(universe.tickers),
            "issuers_planned": len(tickers),
            "pairs": [
                {"day": day, "ticker": ticker, "trade_clock_utc_us": clock}
                for (day, ticker), clock in sorted(universe.clock.items())
                if ticker in keep
            ],
        },
    )
    print(
        f"[sec-issuer-events] days={len(days)} pairs={len(universe.clock)} "
        f"tickers={len(universe.tickers)} issuers={len(tickers)} floor={floor}",
        flush=True,
    )
    if args.plan_only:
        write_json_atomic(
            out / "plan" / "summary.json",
            {
                "study": "alpha_sec_issuer_events",
                "status": STATUS,
                "plan_only": True,
                "days": len(days),
                "panel_pairs": len(universe.clock),
                "tickers": len(universe.tickers),
                "issuers_planned": len(tickers),
                "requests_planned": "submissions(1 + linked history pages) + 2 DEI "
                "companyconcept pages + bounded verbatim archive pages per issuer",
                "producer_sha256": producer,
                "output_root": str(out),
                "network_requests_made": 0,
            },
        )
        print(
            f"[plan-only] issuers={len(tickers)} -> {out / 'plan' / 'summary.json'}",
            flush=True,
        )
        return

    user_agent = resolve_user_agent(args)
    ua_digest = digest_text(user_agent)
    headers = sec_headers(user_agent)
    import requests  # a repo dependency; imported only when the network is needed

    session = requests.Session()
    pace = Pace(args.req_sleep)
    if pace.sleep_s and pace.sleep_s < 1.0 / MAX_DECLARED_RPS:
        raise SystemExit(
            f"[sec-issuer-events] --req-sleep {pace.sleep_s}s exceeds the documented "
            f"SEC maximum of {MAX_DECLARED_RPS} requests/second; nothing was requested"
        )

    seed, error = load_seed_map(session, pace, headers, out)
    if seed is None:
        raise SystemExit(
            f"[sec-issuer-events] the current ticker index could not be retrieved: "
            f"{error}. It is a required discovery seed; no CIK mapping was fabricated."
        )
    print(
        f"[seed] {seed.source_url} entries={seed.entries} sha256={seed.source_sha256[:12]}",
        flush=True,
    )

    manifests: dict[str, dict] = {}
    earliest = universe.earliest_clock_by_ticker()
    for index, ticker in enumerate(tickers, start=1):
        manifests[ticker] = process_issuer(
            ticker=ticker,
            scope_days=days_by_ticker.get(ticker, ()),
            earliest_clock=earliest.get(ticker),
            out_dir=out,
            seed=seed,
            session=session,
            pace=pace,
            headers=headers,
            floor=floor,
            user_agent_sha256=ua_digest,
            user_agent_len=len(user_agent),
            resume=args.resume,
            producer=producer,
        )
        if index % 25 == 0:
            m = manifests[ticker]
            print(
                f"[issuer] {index}/{len(tickers)} {ticker} cik={m['cik']} "
                f"status={m['status']} events={len(m['events'])}",
                flush=True,
            )
            write_json_atomic(
                out / "progress.json",
                {
                    "issuers_done": index,
                    "issuers_total": len(tickers),
                    "requests_made": pace.requests,
                    "last_ticker": ticker,
                    "updated_at": now_iso(),
                },
            )

    # per-day association / UNKNOWN matrix and per-day event metadata
    by_day: dict[str, list[tuple[str, int]]] = {}
    for (day, ticker), clock in sorted(universe.clock.items()):
        if ticker not in keep:
            continue
        by_day.setdefault(day, []).append((ticker, clock))
    events_by_day: dict[str, list[dict]] = {}
    for manifest in manifests.values():
        for event in manifest.get("events") or []:
            events_by_day.setdefault(str(event.get("day")), []).append(event)
    day_rows: list[dict] = []
    for day in days:
        pairs = by_day.get(day)
        if not pairs:
            continue
        day_rows.append(
            write_day_outputs(out, day, pairs, manifests, producer, events_by_day.get(day, []))
        )

    summary = {
        "study": "alpha_sec_issuer_events",
        "status": STATUS,
        "kind": "SEC_ISSUER_EVENTS_DATA_ARTIFACT_NOT_ALPHA_EVIDENCE",
        "schema": MANIFEST_SCHEMA,
        "producer_sha256": producer,
        "days": len(days),
        "panel_pairs": len(universe.clock),
        "tickers": len(universe.tickers),
        "issuers": len(tickers),
        "association_floor": floor,
        "seed_source": seed.source_url,
        "seed_sha256": seed.source_sha256,
        "seed_entries": seed.entries,
        "association_status_counts": _merge_counts(
            row.get("association_status_counts") for row in day_rows
        ),
        "event_form_class_counts": _merge_counts(row.get("form_class_counts") for row in day_rows),
        "day_outputs": day_rows,
        "events_total": sum(len(m.get("events") or []) for m in manifests.values()),
        "requests_made": pace.requests,
        "runtime_s": round(time.time() - t0, 1),
        "output_root": str(out),
        "user_agent_sha256": ua_digest,
        "user_agent_never_printed_or_stored": True,
        "orders_or_paid_purchases": False,
        "protected_outcomes_read": False,
        "earnings_model_pretends_constant_news": False,
    }
    write_json_atomic(out / "summary.json", summary)
    print(
        f"[done] {summary['runtime_s']}s requests={pace.requests} "
        f"events={summary['events_total']} -> {out / 'summary.json'}",
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
        "--resume",
        action="store_true",
        help="resume from the issuer manifests, raw pages and day files already on disk",
    )
    p.add_argument(
        "--plan-only",
        action="store_true",
        help="metadata only: the (day, ticker) universe and plan, never touch the network",
    )
    p.add_argument(
        "--user-agent",
        type=str,
        default=None,
        help=(
            "declared SEC User-Agent ('<organization> <contact>'); also read from the "
            "SEC_USER_AGENT environment variable. Required for any network phase, "
            "never printed and never written to disk."
        ),
    )
    p.add_argument(
        "--req-sleep",
        type=float,
        default=DEFAULT_REQ_SLEEP_S,
        help=(
            f"seconds between requests (default {DEFAULT_REQ_SLEEP_S:.2f}s "
            f"~ {1.0 / DEFAULT_REQ_SLEEP_S:.1f} req/s; SEC documents a maximum "
            f"of {MAX_DECLARED_RPS} req/s)"
        ),
    )
    p.add_argument(
        "--limit-issuers",
        type=int,
        default=None,
        help="bound the issuer loop for a smoke run (resume-safe: the bound is per invocation)",
    )
    run(p.parse_args())


if __name__ == "__main__":
    main()
