#!/usr/bin/env python3
"""Fixed-cohort execution frontier for the frozen sparse h60 model trades.

The learned sparse extension froze h60/thr0.03 and replayed it on the shared
minute-open proxy. This frontier re-prices the SAME immutable trades on as-of
NBBO quotes and asks one question: at which arrival latency and notional does the
quoted touch still carry the strategy, and where does it become UNKNOWN?

What it deliberately does NOT do:

* no trade, signal, entry clock, exit clock, or cohort member is ever re-selected,
  and no latency is ever chosen as "best" - every arrival latency is priced and
  reported on its own, because a live minute-bar feed can publish after the clock,
  so a 250ms arrival is baseline quote EVIDENCE, not an operationally guaranteed
  execution;
* no CAGR, no linear budget scaling, no whole-portfolio certification: every
  number is reported per (latency, size, residual) with the covered/unknown split
  beside it, and an UNKNOWN outcome is never booked as a zero;
* the 2s primary as-of rule is never widened for reporting; a 15s stale-quote
  scenario is computed SEPARATELY per latency and only where a price-updating
  eligible SIP trade print within 1s of the arrival clock corroborates an active,
  bounded market at that moment (price/depth/timestamp risk stays explicit);
* quotes measure the touch cost of a hypothetical market order; L1 depth
  insufficiency is UNKNOWN at that notional (other venues and the deeper book are
  unmeasured), never a verdict on fillability;
* a budget that cannot fund even one integer share at the quoted fee-adjusted
  ASK sends NO order: the pair is a KNOWN no-order (never a zero-return fill,
  never an UNKNOWN), counted as a signal intent but excluded from supported
  coverage, the covered mean and the annual traded density, while its unfilled
  cash contributes 0 USD.

Annual arithmetic is stated per case: trades/year over the block's trading days
x 252, the observed covered contribution (covered pairs only) beside the
conditional whole-case figure (unknown cases behaving like covered ones - NOT
confirmed), with the shared live-SIP $99/month kept separate from execution
residual cost.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl
from alpha_open_panel import ROOT, allowed
from alpha_quote_audit import clock_us, load_trades
from alpha_sparse_quote_service import (
    day_quote_path,
    load_day_quotes,
    quote_at,
    supported_round_trip,
)
from sip_bars import AUCTION_CODES, combine

ET = ZoneInfo("America/New_York")
LANE = Path(os.environ.get("ALPHA_OPEN_SEARCH_V1", Path.home() / "alpha-data" / "open-search-v1"))
SPARSE_EXT = LANE / "learned_sparse_extension"

# Arrival ladder: 250ms is the BASE evidence (unchanged from the quote audit);
# later arrivals are time/price assumptions about a live minute feed publishing
# after the clock, not a stress test.
LATENCIES = (250, 1000, 2000, 5000)
BASE_LATENCY_MS = 250
SIZES = (250.0, 500.0, 1000.0, 2500.0, 5000.0)
RESIDUAL_BPS = (0.0, 10.0, 25.0, 50.0)
MAX_AGE_S = 2.0  # primary as-of quote age ceiling
STALE_MAX_AGE_S = 15.0  # corroborated scenario only, never primary
CORROBORATION_S = 1.0  # eligible print window after the arrival clock
TRADING_DAYS_YEAR = 252
SIP_MONTHLY_USD = 99.0  # official live full-market SIP plan
SIP_ANNUAL_USD = SIP_MONTHLY_USD * 12
BOOT_N = 1000
BOOT_SEED = 20261009

COHORTS = {
    "val61": {
        "trades": SPARSE_EXT / "trades_validation.parquet",
        "period_days": 250,  # 2023-01..2023-12 replay calendar
        "label": "2023 validation block (250 trading days)",
        "note": (
            "learned-study validation surface; the h60 selection itself is "
            "exploratory and was never promoted"
        ),
    },
    "late143": {
        "trades": SPARSE_EXT / "trades_confirmation_100.parquet",
        "period_days": 332,  # 2025-02..2026-05 replay calendar
        "label": "2025-02..2026-05 confirmation block (332 trading days)",
        "note": (
            "previously explored market period, NOT a pristine holdout; DISCOVERY-NOT-VALIDATED"
        ),
    },
}

ALPACA_QUOTES_URL = "https://data.alpaca.markets/v2/stocks/quotes"
PAGE_LIMIT = 10000
FETCH_WINDOW_BEFORE_S = 30.0  # per leg, before the arrival clock
FETCH_ATTEMPTS = 4

QUOTE_SCHEMA_LOCAL = {
    "symbol": pl.Utf8,
    "ts_utc": pl.Datetime("us", "UTC"),
    "bid_price": pl.Float64,
    "bid_size": pl.Float64,
    "ask_price": pl.Float64,
    "ask_size": pl.Float64,
    "bid_exchange": pl.Utf8,
    "ask_exchange": pl.Utf8,
    "conditions": pl.List(pl.Utf8),
    "tape": pl.Utf8,
}


# --------------------------------------------------------------------------- IO
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str) + "\n")


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


def _iso_us(us: int) -> str:
    return datetime.fromtimestamp(us / 1_000_000, tz=UTC).isoformat().replace("+00:00", "Z")


def trade_exit_day(trade: dict) -> str:
    return trade.get("exit_day") or trade["day"]


def guard_days(trades: list[dict], cohort: str) -> None:
    days = {t["day"] for t in trades} | {trade_exit_day(t) for t in trades}
    bad = sorted(d for d in days if not allowed(d))
    if bad:
        raise SystemExit(f"[{cohort}] protected quote/trade read refused: {bad[:5]}")


# ------------------------------------------------------- secondary trade evidence
def load_day_trades(data_root: Path, day: str, tickers: set[str]) -> dict | None:
    """Eligible SIP price prints per ticker (Alpaca policy, no auction codes).

    Evidence for the stale-quote scenario only: it measures whether a
    price-updating print corroborates an active market at a moment. It is never
    used to admit, drop, re-time, or re-rank a trade.
    """
    path = data_root / "sip" / "net" / "trades" / f"{day}.parquet"
    if not path.exists() or not tickers:
        return None
    df = pl.read_parquet(path).filter(pl.col("symbol").is_in(sorted(tickers)))
    if df.is_empty():
        return {}
    df = df.with_columns(pl.col("conditions").list.join("|").alias("ck"))
    rules = []
    for ck, tape in df.select("ck", "tape").unique().iter_rows():
        conds = ck.split("|") if ck else []
        oc, hl, vol, unknown = combine(conds, tape)
        rules.append(
            {
                "ck": ck,
                "tape": tape,
                "eligible": (
                    oc == 2
                    and hl == 2
                    and vol == 2
                    and not unknown
                    and not any(c in AUCTION_CODES for c in conds)
                ),
            }
        )
    if not rules:
        return {}
    df = (
        df.join(pl.DataFrame(rules), on=["ck", "tape"], how="left")
        .filter(pl.col("eligible"))
        .sort("symbol", "ts_utc")
    )
    return {
        key[0]: {"ts": g["ts_utc"].cast(pl.Int64).to_numpy(), "px": g["price"].to_numpy()}
        for key, g in df.partition_by("symbol", as_dict=True).items()
    }


def corroborating_prints(
    stream: dict | None, target_us: int, window_s: float = CORROBORATION_S
) -> tuple[int, int | None]:
    """Eligible prints in [arrival, arrival + window]; (count, first print us)."""
    if not stream:
        return 0, None
    ts = stream["ts"]
    hi = target_us + int(window_s * 1_000_000)
    i = int(np.searchsorted(ts, target_us, side="left"))
    j = int(np.searchsorted(ts, hi, side="right"))
    if j <= i:
        return 0, None
    return int(j - i), int(ts[i])


# --------------------------------------------------------- missing-acquisition REST
def _alpaca_headers(env_path: Path) -> dict | None:
    """Read-only market-data credentials from the repo root .env (never echoed)."""
    from dotenv import load_dotenv

    load_dotenv(env_path)
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        return None
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _get(session, url: str, params: dict, headers: dict):
    last: Exception | None = None
    for attempt in range(FETCH_ATTEMPTS):
        try:
            r = session.get(url, params=params, headers=headers, timeout=60)
            if r.status_code == 429:
                last = RuntimeError(f"http 429 rate limited on attempt {attempt + 1}")
                time.sleep(2 * (attempt + 1))
                continue
            return r
        except Exception as e:  # network error: retry, then record a UNKNOWN
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"request failed after {FETCH_ATTEMPTS} attempts: {last}")


def _quote_rows(events: list[dict], ticker: str) -> dict:
    ts, bp, bs, ap, asz, bxe, axe, cd, tp = [], [], [], [], [], [], [], [], []
    for q in events:
        ts.append(_parse_ts(q["t"]))
        bp.append(float(q["bp"]))
        bs.append(float(q["bs"]))
        ap.append(float(q["ap"]))
        asz.append(float(q["as"]))
        bxe.append(q.get("bx"))
        axe.append(q.get("ax"))
        cd.append(list(q.get("c") or []))
        tp.append(q.get("z"))
    return {
        "symbol": [ticker] * len(ts),
        "ts_utc": ts,
        "bid_price": bp,
        "bid_size": bs,
        "ask_price": ap,
        "ask_size": asz,
        "bid_exchange": bxe,
        "ask_exchange": axe,
        "conditions": cd,
        "tape": tp,
    }


def detect_missing_legs(
    trades: list[dict], *, data_root: Path, supplemental_root: Path | None
) -> list[dict]:
    """Legs whose ticker stream is absent from the ranked day cache.

    Cheap symbol-projection pre-pass (one column, one file per day). A leg is a
    missing acquisition only when the day file and its symbol set genuinely lack
    the ticker; a present-but-stale quote is a market fact, not an acquisition
    gap, and is never fetched.
    """
    wanted: dict[str, dict[str, dict[str, int]]] = defaultdict(dict)
    legs = []
    for t in trades:
        for leg, day, minute in (
            ("entry", t["day"], t.get("entry_et")),
            ("exit", trade_exit_day(t), t.get("exit_et")),
        ):
            if minute is None:
                continue
            wanted[day].setdefault(t["ticker"], {})[leg] = minute
    for day in sorted(wanted):
        symbols: set[str] = set()
        path = day_quote_path(data_root, day)
        if path.exists():
            symbols = set(
                pl.scan_parquet(path).select("symbol").unique().collect()["symbol"].to_list()
            )
        if supplemental_root is not None:
            spath = supplemental_root / f"{day}.parquet"
            if spath.exists():
                symbols |= set(
                    pl.scan_parquet(spath).select("symbol").unique().collect()["symbol"].to_list()
                )
        for ticker in sorted(set(wanted[day]) - symbols):
            for leg in ("entry", "exit"):
                minute = wanted[day][ticker].get(leg)
                if minute is not None:
                    legs.append({"day": day, "ticker": ticker, "leg": leg, "minute": minute})
    return legs


def fetch_missing_legs(
    legs: list[dict], cache_root: Path, env_path: Path, max_latency_ms: int, req_sleep: float
) -> list[dict]:
    """Read-only Alpaca REST /v2/stocks/quotes for not-acquired legs.

    One paginated request per leg: symbols=<ticker>, start=target-30s,
    end=target + max_latency + 2s (so the window covers every arrival latency
    under study), feed=sip, asof=<trade day>, limit=10000 and every
    next_page_token. Raw responses stay verbatim under ``cache_root/raw``;
    normalized rows land in ``cache_root/quotes`` and are merged OVER the ranked
    cache by the service (never overwriting it). HTTP failures are recorded as a
    precise UNKNOWN per leg - nothing is fabricated and no condition is defaulted
    to R.
    """
    import requests

    manifest: list[dict] = []
    headers = _alpaca_headers(env_path)
    raw_root = cache_root / "raw"
    quotes_root = cache_root / "quotes"
    session = requests.Session()
    after_s = max_latency_ms / 1000.0 + 2.0
    per_day: dict[str, list[pl.DataFrame]] = defaultdict(list)
    for leg in legs:
        key = f"{leg['day']}__{leg['ticker']}__{leg['leg']}_m{leg['minute']}"
        target_us = clock_us(leg["day"], leg["minute"], 0)
        start_us = target_us - int(FETCH_WINDOW_BEFORE_S * 1e6)
        end_us = target_us + int(after_s * 1e6)
        record = {
            "day": leg["day"],
            "ticker": leg["ticker"],
            "leg": leg["leg"],
            "minute": leg["minute"],
            "clock_us": target_us,
            "endpoint": ALPACA_QUOTES_URL,
            "asof": leg["day"],
            "feed": "sip",
            "limit": PAGE_LIMIT,
            "window_utc": [_iso_us(start_us), _iso_us(end_us)],
            "window_before_s": FETCH_WINDOW_BEFORE_S,
            "window_after_s": after_s,
            "max_latency_ms": max_latency_ms,
        }
        if headers is None:
            record.update(
                {
                    "status": "unknown_credentials_missing",
                    "http_status": None,
                    "pages": 0,
                    "quote_events": 0,
                    "error": "ALPACA_API_KEY/ALPACA_SECRET_KEY not found in env",
                }
            )
            manifest.append(record)
            continue
        params = {
            "symbols": leg["ticker"],
            "start": _iso_us(start_us),
            "end": _iso_us(end_us),
            "feed": "sip",
            "asof": leg["day"],
            "limit": PAGE_LIMIT,
        }
        events, pages, http, status, error, token = [], [], None, None, None, None
        leg_raw = raw_root / key
        try:
            while True:
                p = dict(params)
                if token:
                    p["page_token"] = token
                r = _get(session, ALPACA_QUOTES_URL, p, headers)
                http = r.status_code
                if r.status_code != 200:
                    status = f"unknown_http_{r.status_code}"
                    error = r.text[:300]
                    break
                js = r.json()
                leg_raw.mkdir(parents=True, exist_ok=True)
                (leg_raw / f"page_{len(pages)}.json").write_text(json.dumps(js))
                data = js.get("quotes") or {}
                ev = data.get(leg["ticker"], []) if isinstance(data, dict) else []
                events.extend(ev)
                pages.append(len(ev))
                token = js.get("next_page_token")
                if not token:
                    break
        except Exception as e:
            status = "unknown_request_exception"
            error = f"{type(e).__name__}: {str(e)[:200]}"
        if status is None:
            status = "acquired" if events else "no_quotes_in_window"
        record.update(
            {
                "status": status,
                "http_status": http,
                "pages": len(pages),
                "page_events": pages,
                "quote_events": len(events),
                "error": error,
            }
        )
        if events:
            per_day[leg["day"]].append(
                pl.DataFrame(_quote_rows(events, leg["ticker"]), schema=QUOTE_SCHEMA_LOCAL)
            )
        manifest.append(record)
        if req_sleep:
            time.sleep(req_sleep)
    for day, frames in per_day.items():
        quotes_root.mkdir(parents=True, exist_ok=True)
        target = quotes_root / f"{day}.parquet"
        frame = (
            pl.concat([f for f in frames if f.height], how="vertical")
            .unique(subset=["symbol", "ts_utc"], keep="first")
            .sort("symbol", "ts_utc")
        )
        if target.exists():
            prior = pl.read_parquet(target)
            frame = (
                pl.concat([prior, frame], how="vertical")
                .unique(subset=["symbol", "ts_utc"], keep="first")
                .sort("symbol", "ts_utc")
            )
        frame.write_parquet(target)
    return manifest


# ------------------------------------------------------------------ cohort pricing
def price_cohort(
    name: str,
    trades: list[dict],
    *,
    data_root: Path,
    supplemental_root: Path | None,
    sizes: tuple[float, ...],
    residuals: tuple[float, ...],
    latencies: tuple[int, ...],
    max_age_s: float,
    scenario_max_age_s: float,
    scenario_on: bool,
) -> tuple[list[dict], dict]:
    """One pass over the fixed cohort, every arrival latency.

    Each day's raw streams are read exactly once and every latency/size/residual
    case is priced from the resident copy; frames are dropped before the next day
    is read, so at most two days (entry, exit) are ever resident.
    """
    guard_days(trades, name)
    day_tickers: dict[str, set[str]] = defaultdict(set)
    for t in trades:
        day_tickers[t["day"]].add(t["ticker"])
        day_tickers[trade_exit_day(t)].add(t["ticker"])
    by_day: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_day[t["day"]].append(t)

    frames: dict[str, tuple[pl.DataFrame, set[str], bool]] = {}
    groups: dict[str, dict[str, pl.DataFrame]] = {}
    trade_streams: dict[str, dict | None] = {}
    coverage: dict[str, dict] = {}

    def day_context(day: str):
        if day not in frames:
            tickers = day_tickers.get(day, set())
            path = day_quote_path(data_root, day)
            frame = load_day_quotes(data_root, day, tickers, supplemental_root)
            symbols = set(frame["symbol"].unique().to_list()) if frame.height else set()
            frames[day] = (frame, symbols, path.exists())
            groups[day] = (
                {k[0]: v for k, v in frame.partition_by("symbol", as_dict=True).items()}
                if frame.height
                else {}
            )
            coverage[day] = {
                "quote_file_present": path.exists(),
                "tickers_requested": len(tickers),
                "tickers_present": len(symbols),
                "tickers_missing": sorted(tickers - symbols),
            }
        return frames[day], groups[day]

    def leg_quote(day: str, ticker: str, minute: int | None, latency_ms: int, max_age: float):
        if minute is None:
            return None, "unknown_proxy_execution"
        (frame, symbols, file_ok), grps = day_context(day)
        if ticker not in symbols:
            return None, "missing_symbol_stream" if file_ok else "missing_day_file"
        g = grps.get(ticker)
        if g is None:
            return None, "quote_not_acquired"
        return quote_at(g, clock_us(day, minute, latency_ms), max_age)

    def stream_for(day: str, tickers: set[str]):
        if day not in trade_streams:
            trade_streams[day] = load_day_trades(data_root, day, tickers)
        return trade_streams[day]

    def scenario_leg(day: str, ticker: str, minute: int | None, latency_ms: int, status: str):
        """15s as-of quote for a leg that was STALE at the primary 2s rule, and
        only when an eligible price-updating print corroborates a live market
        within 1s of this latency's arrival clock."""
        if minute is None or status != "stale_quote":
            return None, 0
        target_us = clock_us(day, minute, latency_ms)
        stream = (stream_for(day, {ticker}) or {}).get(ticker)
        n_prints, _ = corroborating_prints(stream, target_us)
        if n_prints < 1:
            return None, 0
        q, st = leg_quote(day, ticker, minute, latency_ms, scenario_max_age_s)
        return (q if st == "quoted" else None), int(n_prints)

    rows: list[dict] = []
    for day in sorted(by_day):
        day_rows = by_day[day]
        if scenario_on:
            stream_for(day, {t["ticker"] for t in day_rows})
        for t in day_rows:
            ticker = t["ticker"]
            exit_day = trade_exit_day(t)
            row = {
                "cohort": name,
                "day": day,
                "ticker": ticker,
                "entry_et": t.get("entry_et"),
                "exit_day": exit_day,
                "exit_et": t.get("exit_et"),
                "score": t.get("score"),
                # immutable model-replay baseline fixtures, never rewritten
                "proxy_entry_open": t.get("entry_open"),
                "proxy_exit_open": t.get("exit_open_proxy"),
                "proxy_gross": t.get("gross"),
                "proxy_net": t.get("net"),
                "proxy_cost_bps": t.get("cost_bps"),
                "proxy_order_budget": t.get("order_budget"),
                "proxy_status": t.get("status"),
            }
            legs: dict[int, tuple] = {}
            for latency in latencies:
                eq, es = leg_quote(day, ticker, t.get("entry_et"), latency, max_age_s)
                xq, xs = leg_quote(exit_day, ticker, t.get("exit_et"), latency, max_age_s)
                legs[latency] = (eq, es, xq, xs)
                row[f"entry_status_{latency}"] = es
                row[f"exit_status_{latency}"] = xs
                for side, q in (("entry", eq), ("exit", xq)):
                    row[f"{side}_ask_{latency}"] = q["ask"] if q else None
                    row[f"{side}_bid_{latency}"] = q["bid"] if q else None
                    row[f"{side}_spread_bps_{latency}"] = q["spread_bps"] if q else None
                    row[f"{side}_age_s_{latency}"] = q["age_s"] if q else None
                    row[f"{side}_quote_us_{latency}"] = q["quote_us"] if q else None
                row[f"entry_ask_shares_{latency}"] = eq["ask_shares"] if eq else None
                row[f"exit_bid_shares_{latency}"] = xq["bid_shares"] if xq else None
                if eq is None or xq is None:
                    row[f"priced_{latency}"] = False
                    row[f"pair_status_{latency}"] = f"entry:{es};exit:{xs}"
                    continue
                row[f"priced_{latency}"] = True
                row[f"pair_status_{latency}"] = "priced_primary_2s"
                for size in sizes:
                    for res in residuals:
                        rt = supported_round_trip(eq, xq, size, res)
                        st = f"{latency}_{int(size)}_{int(res)}"
                        row[f"p_q_{st}"] = rt["quantity"]
                        row[f"p_sup_{st}"] = rt["supported"]
                        row[f"p_net_{st}"] = rt["net"] if rt["supported"] else None
                        row[f"p_noorder_{st}"] = bool(rt["unfilled_known_cash"])
                        if int(res) == 0:
                            row[f"p_unknown_{latency}_{int(size)}"] = rt["unknown"]
            if scenario_on:
                for latency in latencies:
                    eq, es, xq, xs = legs[latency]
                    entry_stale = es == "stale_quote"
                    exit_stale = xs == "stale_quote"
                    row[f"s_entry_stale_{latency}"] = bool(entry_stale)
                    row[f"s_exit_stale_{latency}"] = bool(exit_stale)
                    if not (entry_stale or exit_stale):
                        # No stale leg: the 2s primary already priced this pair, so
                        # the scenario is out of scope rather than a second estimate.
                        for side, _q in (("entry", eq), ("exit", xq)):
                            row[f"s_{side}_source_{latency}"] = "not_applicable_no_stale_leg"
                            row[f"s_{side}_ask_{latency}"] = None
                            row[f"s_{side}_bid_{latency}"] = None
                            row[f"s_{side}_age_s_{latency}"] = None
                            row[f"s_{side}_prints_{latency}"] = None
                        row[f"s_applicable_{latency}"] = False
                        continue
                    sq_entry, n_entry = (
                        scenario_leg(day, ticker, t.get("entry_et"), latency, es)
                        if entry_stale
                        else (eq, 0)
                    )
                    sq_exit, n_exit = (
                        scenario_leg(exit_day, ticker, t.get("exit_et"), latency, xs)
                        if exit_stale
                        else (xq, 0)
                    )
                    # A stale leg is replaced only by its corroborated 15s quote;
                    # otherwise it stays UNKNOWN inside the scenario too.
                    use_entry = sq_entry if entry_stale else eq
                    use_exit = sq_exit if exit_stale else xq
                    for side, stale, prints, used in (
                        ("entry", entry_stale, n_entry, use_entry),
                        ("exit", exit_stale, n_exit, use_exit),
                    ):
                        if not stale:
                            source = "primary_2s_unchanged"
                        elif used is not None:
                            source = "scenario_15s_corroborated"
                        elif prints > 0:
                            source = "unknown_stale_corroborated_but_no_regular_quote_15s"
                        else:
                            source = "unknown_stale_no_corroborating_print"
                        row[f"s_{side}_source_{latency}"] = source
                        row[f"s_{side}_prints_{latency}"] = int(prints)
                        row[f"s_{side}_ask_{latency}"] = used["ask"] if used else None
                        row[f"s_{side}_bid_{latency}"] = used["bid"] if used else None
                        row[f"s_{side}_age_s_{latency}"] = used["age_s"] if used else None
                    if use_entry is not None:
                        row[f"s_entry_ask_shares_{latency}"] = use_entry["ask_shares"]
                    if use_exit is not None:
                        row[f"s_exit_bid_shares_{latency}"] = use_exit["bid_shares"]
                    row[f"s_applicable_{latency}"] = bool(
                        use_entry is not None and use_exit is not None
                    )
                    if not (use_entry is not None and use_exit is not None):
                        continue
                    for size in sizes:
                        for res in residuals:
                            rt = supported_round_trip(use_entry, use_exit, size, res)
                            st = f"{latency}_{int(size)}_{int(res)}"
                            row[f"s_q_{st}"] = rt["quantity"]
                            row[f"s_sup_{st}"] = rt["supported"]
                            row[f"s_net_{st}"] = rt["net"] if rt["supported"] else None
                            row[f"s_noorder_{st}"] = bool(rt["unfilled_known_cash"])
                            if int(res) == 0:
                                row[f"s_unknown_{latency}_{int(size)}"] = rt["unknown"]
            rows.append(row)
        # Day residency only: quote/trade streams never outlive their day.
        for d in (day,):
            frames.pop(d, None)
            groups.pop(d, None)
            trade_streams.pop(d, None)
    return rows, coverage


# --------------------------------------------------------------------- aggregation
def _bootstrap_day_ci(rows: list[dict], net_of, boot_n: int, seed: int) -> list[float] | None:
    """Day-block bootstrap CI of the covered per-fill mean (fixed seed).

    Days are resampled with replacement and the per-fill mean is the mean of all
    covered fills in the resampled days, so day clustering is preserved.
    """
    if boot_n <= 0 or len(rows) < 2:
        return None
    sums: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        day = r["day"]
        sums[day] += float(net_of(r))
        counts[day] += 1
    if len(sums) < 2:
        return None
    day_sum = np.array([sums[d] for d in sorted(sums)])
    day_n = np.array([counts[d] for d in sorted(sums)], dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(day_sum), size=(boot_n, len(day_sum)))
    draws = day_sum[idx].sum(axis=1) / day_n[idx].sum(axis=1)
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return [float(lo), float(hi)]


def annual_block(
    n_trades: int,
    n_supported: int,
    n_known_no_order: int,
    mean_net: float | None,
    size: float,
    period_days: int,
) -> dict:
    # The whole-case (if-all-eligible) density counts only intents that could
    # actually trade at this size: a budget below one integer share is a known
    # no-order whose cash stays unfilled, so it is never one of the traded.
    tp_whole = (n_trades - n_known_no_order) / period_days * TRADING_DAYS_YEAR
    tp_covered = n_supported / period_days * TRADING_DAYS_YEAR
    observed = (mean_net * tp_covered * size) if mean_net is not None else None
    conditional = (mean_net * tp_whole * size) if mean_net is not None else None
    return {
        "trades_per_year_whole_case": tp_whole,
        "trades_per_year_covered_case": tp_covered,
        "covered_mean_net_fraction_per_fill": mean_net,
        "observed_covered_contribution_usd_per_year": observed,
        "conditional_whole_case_usd_per_year_if_unknown_matched_covered": conditional,
        "n_known_no_order_excluded_from_whole_case": n_known_no_order,
        "known_no_order_cash_contribution_usd_per_year": 0.0,
        "shared_live_sip_opex_usd_per_year": SIP_ANNUAL_USD,
        "observed_covered_contribution_net_of_sip_opex_usd_per_year": (observed - SIP_ANNUAL_USD)
        if observed is not None
        else None,
        "annualization_basis": (
            "observed trade count / block trading days x 252; "
            "the covered case annualizes covered pairs only, the "
            "whole-case rate spreads the same per-fill mean over "
            "every signal that could trade at this size (known "
            "no-order min-capital pairs excluded) including "
            "UNKNOWN outcomes"
        ),
        "opex_basis": (
            "official live full-market SIP plan 99 USD/month "
            "(https://docs.alpaca.markets/us/docs/about-market-data-api); "
            "separate from execution residual cost. This study is RTH-only "
            "and does not by itself justify a 24/7 plan."
        ),
        "no_cagr": "simple annualized contribution; no compounding",
        "no_linear_scaling": (
            "per-fill fraction is assumed size-independent (an "
            "assumption, not a measurement); observed L1 coverage "
            "shrinks with notional and is reported per size, so "
            "capacity is never scaled linearly"
        ),
        "known_no_order_note": (
            "a budget below one integer share sends no order; "
            "the unfilled cash contributes 0 USD to the total "
            "portfolio contribution (cash retained), which is "
            "never booked as a per-fill zero return and never "
            "as an UNKNOWN loss"
        ),
    }


def case_stats(
    rows: list[dict],
    *,
    priced_of,
    supported_of,
    net_of,
    unknown_of,
    unpriced_of,
    size: float,
    period_days: int,
    boot_n: int,
    seed: int,
    annual: bool = True,
    noorder_of=None,
) -> dict:
    priced = [r for r in rows if priced_of(r)]
    unpriced = [r for r in rows if not priced_of(r)]
    # A known no-order (budget below one integer share) is a KNOWN cash outcome,
    # not an UNKNOWN capacity shortfall and not a fill: it is separated first so
    # it never enters the supported set, the covered mean, or unknown causes.
    supported: list[dict] = []
    short: list[dict] = []
    noorder: list[dict] = []
    for r in priced:
        if noorder_of is not None and noorder_of(r):
            noorder.append(r)
        elif supported_of(r):
            supported.append(r)
        else:
            short.append(r)
    causes: Counter = Counter()
    for r in unpriced:
        causes[str(unpriced_of(r))] += 1
    for r in short:
        causes[str(unknown_of(r) or "unknown_l1_capacity")] += 1
    nets = [float(net_of(r)) for r in supported]
    mean_net = float(np.mean(nets)) if nets else None
    usd = [n * size for n in nets]
    wins = [x for x in usd if x > 0]
    losses = [-x for x in usd if x < 0]
    stats = {
        "budget_usd": size,
        "n_trades": len(rows),
        "n_signal_intents": len(rows),
        "n_priced": len(priced),
        "n_known_no_order": len(noorder),
        "n_l1_supported": len(supported),
        "n_unknown": len(rows) - len(supported) - len(noorder),
        "unknown_causes": dict(sorted(causes.items(), key=lambda kv: (-kv[1], kv[0]))),
        "covered_mean_net_fraction": mean_net,
        "covered_mean_net_usd_per_fill": (mean_net * size) if mean_net is not None else None,
        "covered_win_rate": (len(wins) / len(usd)) if usd else None,
        "covered_profit_factor": (sum(wins) / sum(losses)) if losses else None,
        "covered_profit_factor_note": None if losses else "no losing covered fill",
        "covered_worst_net_fraction": float(np.min(nets)) if nets else None,
        "covered_traded_days": len({r["day"] for r in supported}),
        "covered_bootstrap_ci95_mean_net_fraction": _bootstrap_day_ci(
            supported, net_of, boot_n, seed
        ),
        "coverage_fraction_supported": (len(supported) / len(rows)) if rows else None,
        "unknown_outcomes_unmeasured_not_zero": True,
        "unknown_because": (
            "unpriced legs, missing symbol streams and L1 capacity "
            "shortfalls stay UNKNOWN; they are never booked as a zero "
            "outcome and never widen the covered mean"
        ),
        "known_no_order_note": (
            "a budget below one integer share at the quoted "
            "fee-adjusted ASK is a KNOWN no-order (n_known_no_order): "
            "no order is sent and the cash stays unfilled, so it is "
            "excluded from n_l1_supported, the covered mean and "
            "unknown_causes, and it is never an UNKNOWN loss or a "
            "zero-return fill"
        ),
    }
    if annual:
        stats["annual"] = annual_block(
            len(rows), len(supported), len(noorder), mean_net, size, period_days
        )
    return stats


def proxy_baseline(trades: list[dict], period_days: int) -> dict:
    """The frozen minute-open model-replay baseline these quotes are compared to."""
    nets = [float(t["net"]) for t in trades if t.get("net") is not None]
    mean = float(np.mean(nets)) if nets else None
    tp = len(trades) / period_days * TRADING_DAYS_YEAR
    return {
        "n_known_fills": len(nets),
        "mean_net_per_fill_at_model_cost": mean,
        "trades_per_year_whole_case": tp,
        "proxy_annual_ev_usd_per_year_at_1000": (mean * tp * 1000.0) if mean is not None else None,
        "cost_bps": 100,
        "order_budget": 1000,
        "source": "immutable model replay trades (minute-open proxy, not a fill)",
        "note": (
            "kept as the baseline fixture; the frontier never rewrites it and "
            "never re-selects the cohort"
        ),
    }


def cohort_report(
    name: str,
    cfg: dict,
    trades: list[dict],
    rows: list[dict],
    coverage: dict,
    *,
    sizes,
    residuals,
    latencies,
    boot_n,
    seed: int,
) -> dict:
    report = {
        "label": cfg["label"],
        "status_note": cfg["note"],
        "trades_path": str(cfg["trades"]),
        "trades_sha256": sha256_file(cfg["trades"]),
        "n_trades": len(trades),
        "period_days": cfg["period_days"],
        "trading_days_with_signals": len({t["day"] for t in trades}),
        "proxy_baseline": proxy_baseline(trades, cfg["period_days"]),
        "day_coverage": {
            "days_loaded": len(coverage),
            "days_missing_quote_file": sorted(
                d for d, c in coverage.items() if not c["quote_file_present"]
            ),
            "missing_symbol_stream_legs": sum(len(c["tickers_missing"]) for c in coverage.values()),
        },
        "primary_2s": {},
        "stale_scenario_15s": {},
    }
    for latency in latencies:
        block: dict[str, dict] = {}
        scen: dict[str, dict] = {}
        for res in residuals:
            ri = int(res)
            block[str(ri)] = {}
            scen[str(ri)] = {}
            for size in sizes:
                si = int(size)

                def _sup(r, leg=latency, s=si, k=ri):
                    return bool(r.get(f"p_sup_{leg}_{s}_{k}"))

                def _net(r, leg=latency, s=si, k=ri):
                    return r.get(f"p_net_{leg}_{s}_{k}")

                def _noorder(r, leg=latency, s=si, k=ri):
                    return bool(r.get(f"p_noorder_{leg}_{s}_{k}"))

                def _priced(r, leg=latency, s=si, k=ri):
                    return r.get(f"s_q_{leg}_{s}_{k}") is not None

                def _ssup(r, leg=latency, s=si, k=ri):
                    return bool(r.get(f"s_sup_{leg}_{s}_{k}"))

                def _snet(r, leg=latency, s=si, k=ri):
                    return r.get(f"s_net_{leg}_{s}_{k}")

                def _snoorder(r, leg=latency, s=si, k=ri):
                    return bool(r.get(f"s_noorder_{leg}_{s}_{k}"))

                block[str(ri)][str(si)] = case_stats(
                    rows,
                    priced_of=lambda r, leg=latency: bool(r.get(f"priced_{leg}")),
                    supported_of=_sup,
                    net_of=_net,
                    noorder_of=_noorder,
                    unknown_of=lambda r, leg=latency, s=si: r.get(f"p_unknown_{leg}_{s}"),
                    unpriced_of=lambda r, leg=latency: r.get(f"pair_status_{leg}"),
                    size=size,
                    period_days=cfg["period_days"],
                    boot_n=boot_n,
                    seed=seed,
                )
                scen[str(ri)][str(si)] = case_stats(
                    rows,
                    priced_of=_priced,
                    supported_of=_ssup,
                    net_of=_snet,
                    noorder_of=_snoorder,
                    unknown_of=lambda r, leg=latency, s=si: r.get(f"s_unknown_{leg}_{s}"),
                    unpriced_of=lambda r: "scenario_not_applicable",
                    size=size,
                    period_days=cfg["period_days"],
                    boot_n=boot_n,
                    seed=seed,
                )
        report["primary_2s"][str(latency)] = block
        report["stale_scenario_15s"][str(latency)] = scen
    return report


def reproduction_block(
    rows: list[dict], prior_path: Path, boot_n: int, seed: int, supplemental_in_use: bool = False
) -> dict:
    """Base-latency $1000 reproduction against the frozen prior quote audit.

    The frozen audit priced this cohort WITHOUT the fetched-quotes supplement. When
    ``--fetch-missing`` is in play the previously not-acquired legs can become
    priced, so an exact match is no longer expected; the per-check diffs show
    exactly which legs changed and why.
    """
    out = {"available": False, "fetched_supplement_in_use": bool(supplemental_in_use)}
    if not prior_path.exists():
        out["reason"] = f"prior audit summary not present at {prior_path}"
        return out
    prior = json.loads(prior_path.read_text())
    out.update(
        {
            "available": True,
            "prior_file": str(prior_path),
            "prior_latency_ms": prior.get("latency_ms"),
            "prior_max_quote_age_s": prior.get("max_quote_age_s"),
            "prior_order_budget": prior.get("order_budget"),
        }
    )
    leg = BASE_LATENCY_MS
    base_stats = case_stats(
        rows,
        priced_of=lambda r: bool(r.get(f"priced_{leg}")),
        supported_of=lambda r: bool(r.get(f"p_sup_{leg}_1000_0")),
        net_of=lambda r: r.get(f"p_net_{leg}_1000_0"),
        noorder_of=lambda r: bool(r.get(f"p_noorder_{leg}_1000_0")),
        unknown_of=lambda r: r.get(f"p_unknown_{leg}_1000"),
        unpriced_of=lambda r: r.get(f"pair_status_{leg}"),
        size=1000.0,
        period_days=1,
        boot_n=boot_n,
        seed=seed,
        annual=False,
    )
    prior_status = prior.get("status_counts") or {}
    mine_status = dict(Counter(r.get(f"pair_status_{leg}") for r in rows))

    # translate this frontier's pair status into the prior audit's taxonomy:
    # priced pairs carry their capacity verdict separately, and a day file that
    # exists without the ticker is the prior audit's "quote_not_acquired".
    def _prior_taxonomy(r: dict) -> str:
        st = r.get(f"pair_status_{leg}")
        if st != "priced_primary_2s":
            return st.replace("missing_symbol_stream", "quote_not_acquired")
        return (
            "quoted_capacity_supported_not_fill_guaranteed"
            if r.get(f"p_sup_{leg}_1000_0")
            else "unknown_top_of_book_capacity"
        )

    mapped = dict(Counter(_prior_taxonomy(r) for r in rows))
    out["frontier_native_status_counts"] = mine_status
    out["native_taxonomy_note"] = (
        "frontier-native pair statuses: priced pairs carry their capacity verdict in "
        "p_sup_/p_unknown_ columns, so priced_primary_2s intentionally groups the "
        "prior audit's supported and capacity-unknown trades"
    )
    checks = [
        {
            "check": "pair_status_counts_at_base_latency_1000",
            "prior": prior_status,
            "frontier": mapped,
            "match": prior_status == mapped,
        },
        {
            "check": "n_l1_supported_at_1000_residual_0",
            "prior": prior.get("quote_supported_pairs"),
            "frontier": base_stats["n_l1_supported"],
            "match": prior.get("quote_supported_pairs") == base_stats["n_l1_supported"],
        },
    ]
    prior_means = prior.get("covered_mean_net") or {}
    for res in (0, 25):
        if str(res) in prior_means:
            st = case_stats(
                rows,
                priced_of=lambda r: bool(r.get(f"priced_{leg}")),
                supported_of=lambda r, res=res: bool(r.get(f"p_sup_{leg}_1000_{res}")),
                net_of=lambda r, res=res: r.get(f"p_net_{leg}_1000_{res}"),
                noorder_of=lambda r, res=res: bool(r.get(f"p_noorder_{leg}_1000_{res}")),
                unknown_of=lambda r: r.get(f"p_unknown_{leg}_1000"),
                unpriced_of=lambda r: r.get(f"pair_status_{leg}"),
                size=1000.0,
                period_days=1,
                boot_n=0,
                seed=seed,
                annual=False,
            )
            checks.append(
                {
                    "check": f"covered_mean_net_at_1000_residual_{res}",
                    "prior": prior_means[str(res)],
                    "frontier": st["covered_mean_net_fraction"],
                    "match": (
                        st["covered_mean_net_fraction"] is not None
                        and abs(st["covered_mean_net_fraction"] - float(prior_means[str(res)]))
                        < 1e-12
                    ),
                }
            )
    out["checks"] = checks
    out["all_match"] = all(c["match"] for c in checks)
    if supplemental_in_use:
        out["fetch_effect_note"] = (
            "a fetched-quotes supplement was in use, so previously not-acquired legs "
            "may now be priced: an exact match to the frozen NO-FETCH audit is not "
            "expected and a false all_match here is informational, not a failure"
        )
    out["note"] = (
        "the 250ms base leg must reproduce the frozen prior audit exactly; "
        "later arrival latencies are additional assumptions, not revisions"
    )
    return out


# ---------------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--trades",
        type=Path,
        default=COHORTS["late143"]["trades"],
        help="late (2025-02..2026-05) cohort trades (default: %(default)s)",
    )
    p.add_argument(
        "--val-trades",
        type=Path,
        default=COHORTS["val61"]["trades"],
        help="2023 validation cohort trades (default: %(default)s)",
    )
    p.add_argument(
        "--data",
        type=Path,
        default=ROOT / "data",
        help="panel data root containing sip/net/ (default: %(default)s)",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=LANE / "sparse_execution_frontier",
        help="own output root (default: %(default)s)",
    )
    p.add_argument(
        "--only",
        choices=("val61", "late143"),
        default=None,
        help="run a single cohort (default: both)",
    )
    p.add_argument("--sizes", type=str, default=",".join(str(int(s)) for s in SIZES))
    p.add_argument("--residuals", type=str, default=",".join(str(int(r)) for r in RESIDUAL_BPS))
    p.add_argument(
        "--latencies",
        type=str,
        default=",".join(str(x) for x in LATENCIES),
        help="arrival latencies in ms; 250 is the base evidence leg",
    )
    p.add_argument(
        "--max-age-s",
        type=float,
        default=MAX_AGE_S,
        help="primary as-of quote age ceiling (default: %(default)s)",
    )
    p.add_argument(
        "--stale-scenario-max-age-s",
        type=float,
        default=STALE_MAX_AGE_S,
        help="corroborated stale-leg scenario age ceiling (default: %(default)s)",
    )
    p.add_argument(
        "--no-stale-scenario",
        action="store_true",
        help="skip the corroborated 15s stale-leg scenario",
    )
    p.add_argument(
        "--supplemental-quotes",
        type=Path,
        default=None,
        help="dir of same-schema <day>.parquet rows merged over the ranked cache",
    )
    p.add_argument(
        "--fetch-missing",
        action="store_true",
        help="read-only Alpaca REST /v2/stocks/quotes for missing-acquisition legs",
    )
    p.add_argument(
        "--fetch-cache",
        type=Path,
        default=None,
        help="fetch cache root (default: <out>/fetched_quotes)",
    )
    p.add_argument(
        "--env",
        type=Path,
        default=ROOT / ".env",
        help="dotenv with ALPACA_API_KEY/ALPACA_SECRET_KEY (default: %(default)s)",
    )
    p.add_argument("--req-sleep", type=float, default=0.0)
    p.add_argument("--boot-n", type=int, default=BOOT_N)
    p.add_argument("--boot-seed", type=int, default=BOOT_SEED)
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    sizes = tuple(sorted({float(x) for x in args.sizes.split(",") if x.strip()}))
    residuals = tuple(sorted({float(x) for x in args.residuals.split(",") if x.strip()}))
    latencies = tuple(sorted({int(x) for x in args.latencies.split(",") if x.strip()}))
    if not sizes or not residuals or not latencies:
        raise SystemExit("sizes, residuals and latencies must all be non-empty")
    if BASE_LATENCY_MS not in latencies:
        raise SystemExit(f"the {BASE_LATENCY_MS}ms base latency must be included")
    t0 = time.time()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    cohort_cfgs = {
        name: dict(COHORTS[name]) for name in ("val61", "late143") if args.only in (None, name)
    }
    trade_sets: dict[str, tuple[Path, list[dict]]] = {}
    for name, cfg in cohort_cfgs.items():
        path = args.trades if name == "late143" else args.val_trades
        if not path.exists():
            raise SystemExit(f"[{name}] trades not found: {path}")
        trades = load_trades(path)
        guard_days(trades, name)
        cfg["trades"] = path
        trade_sets[name] = (path, trades)

    # ---- missing acquisitions: detected, then only fetched behind the flag ----
    all_trades = [t for _, trades in trade_sets.values() for t in trades]
    legs = detect_missing_legs(
        all_trades, data_root=args.data, supplemental_root=args.supplemental_quotes
    )
    dedup: dict[tuple, dict] = {}
    for leg in legs:
        dedup[(leg["day"], leg["ticker"], leg["leg"])] = leg
    legs = [dedup[k] for k in sorted(dedup)]
    max_latency = max(latencies)
    fetch_cache = args.fetch_cache or (out / "fetched_quotes")
    if args.fetch_missing:
        if legs:
            manifest = fetch_missing_legs(legs, fetch_cache, args.env, max_latency, args.req_sleep)
        else:
            manifest = []
        if args.supplemental_quotes is None:
            args.supplemental_quotes = fetch_cache / "quotes"
        fetch_note = (
            "read-only Alpaca REST /v2/stocks/quotes; raw pages kept under "
            f"{fetch_cache / 'raw'}; normalized rows under "
            f"{fetch_cache / 'quotes'} merged OVER the ranked cache"
        )
    else:
        manifest = [
            {
                "day": leg["day"],
                "ticker": leg["ticker"],
                "leg": leg["leg"],
                "minute": leg["minute"],
                "endpoint": ALPACA_QUOTES_URL,
                "asof": leg["day"],
                "feed": "sip",
                "status": "unknown_not_requested",
                "window_after_s": max_latency / 1000.0 + 2.0,
                "max_latency_ms": max_latency,
                "error": "--fetch-missing not set; leg stays UNKNOWN",
            }
            for leg in legs
        ]
        fetch_note = (
            "no network calls: legs without a cached symbol stream stay "
            "UNKNOWN unless --fetch-missing is run"
        )
    write_json(
        out / "missing_requests_manifest.json",
        {
            "legs_detected": len(legs),
            "fetch_missing": bool(args.fetch_missing),
            "fetch_cache_root": str(fetch_cache),
            "max_latency_ms": max_latency,
            "window_before_s": FETCH_WINDOW_BEFORE_S,
            "window_after_s": max_latency / 1000.0 + 2.0,
            "asof": "trade day",
            "feed": "sip",
            "pagination_limit": PAGE_LIMIT,
            "credentials_never_stored": True,
            "note": fetch_note,
            "requests": manifest,
        },
    )

    summary = {
        "study": "alpha_sparse_execution_frontier",
        "status": (
            "DISCOVERY-NOT-VALIDATED as-of quote execution frontier; quotes "
            "measure touch cost, not exchange fills"
        ),
        "generated_at": datetime.now(UTC).isoformat(),
        "assumptions": {
            "arrival_latencies_ms": list(latencies),
            "base_latency_ms": BASE_LATENCY_MS,
            "latency_policy": (
                "every latency is priced and reported on its own; no "
                "best-latency selection. Live minute-bar feeds can "
                "publish after the clock, so the 250ms as-of quote is "
                "baseline evidence, not an operationally guaranteed "
                "arrival; later arrivals are the time/price assumption "
                "actually being tested."
            ),
            "max_quote_age_s": args.max_age_s,
            "residual_rt_bps": [int(r) for r in residuals],
            "sizes_usd": [int(s) for s in sizes],
            "integer_fee_funded_quantity": (
                "quantity = int(budget // ask*(1+side)); round-lot era sizes are already shares"
            ),
            "no_order_policy": (
                "a budget below one integer share at the quoted "
                "fee-adjusted ASK sends NO order: a known "
                "no-order (status known_no_order_min_capital, "
                "unfilled_known_cash) whose cash stays unfilled. It "
                "is never an executed fill, never a zero-return fill "
                "and never an UNKNOWN capacity outcome; quantities "
                "stay integer because broker fractional availability "
                "is unverified"
            ),
            "l1_only_measurement": (
                "capacity is measured on displayed top-of-book size "
                "only; other venues and the deeper book are "
                "unmeasured, so a shortfall is UNKNOWN at that "
                "notional, never 'cannot fill'"
            ),
            "stale_scenario": {
                "max_age_s": args.stale_scenario_max_age_s,
                "enabled": not args.no_stale_scenario,
                "gate": (
                    "only for legs STALE at the primary 2s rule, and only when an "
                    "eligible price-updating SIP print within 1s of the arrival "
                    "clock corroborates an active, bounded market at that moment"
                ),
                "risk": (
                    "price/depth/timestamp risk of the older quote and deeper "
                    "book are explicit parts of this assumption; the primary 2s "
                    "rule is never widened"
                ),
                "trade_evidence_scope": (
                    "corroboration assesses execution only; it is "
                    "never a future-eligibility or admission gate"
                ),
            },
            "unpriced_policy": "UNKNOWN outcomes are counted and caused, never zeroed",
            "no_cagr": True,
            "shared_sip_opex_usd_per_year": SIP_ANNUAL_USD,
        },
        "cohorts": {},
    }
    annual_ev = {"basis": summary["assumptions"], "cohorts": {}}
    for name, (_path, trades) in trade_sets.items():
        cfg = cohort_cfgs[name]
        rows, coverage = price_cohort(
            name,
            trades,
            data_root=args.data,
            supplemental_root=args.supplemental_quotes,
            sizes=sizes,
            residuals=residuals,
            latencies=latencies,
            max_age_s=args.max_age_s,
            scenario_max_age_s=args.stale_scenario_max_age_s,
            scenario_on=not args.no_stale_scenario,
        )
        cohort_dir = out / name
        cohort_dir.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(rows, infer_schema_length=None).write_parquet(cohort_dir / "pairs.parquet")
        report = cohort_report(
            name,
            cfg,
            trades,
            rows,
            coverage,
            sizes=sizes,
            residuals=residuals,
            latencies=latencies,
            boot_n=args.boot_n,
            seed=args.boot_seed,
        )
        if name == "late143":
            report["reproduction_vs_prior_audit"] = reproduction_block(
                rows,
                SPARSE_EXT / "quote_confirmation" / "summary.json",
                args.boot_n,
                args.boot_seed,
                supplemental_in_use=args.supplemental_quotes is not None,
            )
        summary["cohorts"][name] = report
        annual_ev["cohorts"][name] = {"primary_2s": {}, "stale_scenario_15s": {}}
        for latency in latencies:
            for rule in ("primary_2s", "stale_scenario_15s"):
                annual_ev["cohorts"][name][rule][str(latency)] = {
                    str(int(s)): {
                        str(int(r)): report[rule][str(latency)][str(int(r))][str(int(s))]["annual"]
                        for r in residuals
                    }
                    for s in sizes
                }
        print(
            f"[{name}] {len(rows)} trades priced; pairs -> {cohort_dir / 'pairs.parquet'}",
            flush=True,
        )

    summary["runtime_s"] = round(time.time() - t0, 1)
    write_json(out / "frontier_summary.json", summary)
    write_json(out / "annual_EV.json", annual_ev)
    snap_dir = out / "producer_snapshot"
    snap_dir.mkdir(parents=True, exist_ok=True)
    program_files = {}
    for src in (
        Path(__file__).resolve(),
        Path(sys.modules["alpha_sparse_quote_service"].__file__).resolve(),
    ):
        shutil.copy2(src, snap_dir / src.name)
        program_files[src.name] = {"path": str(src), "sha256": sha256_file(src)}
    write_json(
        out / "reproducibility.json",
        {
            "argv": list(argv if argv is not None else []),
            "generated_at": summary["generated_at"],
            "data_root": str(args.data),
            "supplemental_quotes_root": (
                str(args.supplemental_quotes) if args.supplemental_quotes else None
            ),
            "fetch_cache_root": str(fetch_cache),
            "fetch_missing": bool(args.fetch_missing),
            "inputs": {name: str(path) for name, (path, _) in trade_sets.items()},
            "input_sha256": {name: sha256_file(path) for name, (path, _) in trade_sets.items()},
            "program_files": program_files,
            "sizes_usd": [int(s) for s in sizes],
            "residual_rt_bps": [int(r) for r in residuals],
            "arrival_latencies_ms": list(latencies),
            "boot": {"n": args.boot_n, "seed": args.boot_seed},
            "quote_service_module": "alpha_sparse_quote_service.py",
            "runtime_s": summary["runtime_s"],
        },
    )
    print(
        json.dumps(
            {
                "out": str(out),
                "cohorts": {k: {"n_trades": v["n_trades"]} for k, v in summary["cohorts"].items()},
                "missing_legs": len(legs),
                "runtime_s": summary["runtime_s"],
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
