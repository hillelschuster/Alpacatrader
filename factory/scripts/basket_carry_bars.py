#!/usr/bin/env python
"""BASKET-01 Phase-2 carry-bar substrate producer (2026-09-24 correction).

Builds the declared per-day full-RTH carry overlay that the canonical simulator
(``factory/scripts/basket_sim.py``, ``CarrySubstrate``) consults for
cross-session carry resolution, and certifies every requested ``(day, ticker)``
in ``carry_bars/manifest.json``:

* ``bars``        -- full regular-session tape written to ``<day>.parquet``.
* ``no_bars``     -- the source proves the ticker had no RTH trade that day; the
                     engine continues the carry (``no_resumption``).
* ``unavailable`` -- coverage could not be certified; the engine hard-fails
                     rather than silently treating absence as "no trade".

Production source (the only source that can certify a production run)
---------------------------------------------------------------------
Targeted Alpaca historical StockBars: ``DataFeed.SIP``, ``Adjustment.RAW``,
``TimeFrame.Minute``, full regular-session window (09:25 ET .. session_end+5min).
Credentials come from an explicit ``--env-file`` (``ALPACA_API_KEY`` /
``ALPACA_SECRET_KEY``) or the process environment; the worktree has no ``.env``
of its own and nothing is auto-loaded from the CWD.  Each day is requested as
one batch and any symbol omitted from the response is re-requested individually
so ``no_bars`` (success, zero rows) is never confused with ``unavailable``
(request failed after retries).  Transient failures are retried with backoff;
auth failures abort immediately.

Test-only fixture source
------------------------
``--fixture-root`` reads a local ``clean_ohlcv_YYYY-MM.parquet`` layer and
writes the same overlay shape, but every entry is stamped
``source.production=false`` / ``certification=fixture_test_only``.  The engine
refuses such entries for a production run (``CarrySubstrate`` is strict by
default); fixtures exist only so engine mechanics can be exercised offline.
Never use a filtered, provider-substituted, or candidate-scoped layer as a
production carry source.

Usage::

    python factory/scripts/basket_carry_bars.py --env-file /path/.env \
        --request 2022-03-24:HSDT
    python factory/scripts/basket_carry_bars.py --env-file /path/.env \
        --requests requests.json
    python factory/scripts/basket_carry_bars.py --scan --print-only

``--scan`` enumerates halt cases from the candidate-bar artifact (a candidate
tape ending before ``session_end``, so the forced-flat execution bar is absent)
and requests the ticker for the next ``--scan-window`` dev days after the halt,
stopping at a dev-block boundary.
The engine's ``MissingCarrySubstrate`` error lists any uncovered request, so the
overlay can also be extended iteratively.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

try:
    from factory.scripts import basket_sim as sim
except ImportError:                                    # direct script execution
    import basket_sim as sim

ROOT = Path(__file__).resolve().parents[2]
ET = ZoneInfo("America/New_York")
RTH_FIRST = 570
RTH_LAST_EXCLUSIVE = 960
FETCH_ATTEMPTS = 6
FETCH_BACKOFF_S = 2.0
MIN_SYMBOLS_DEFAULT = 500
SCAN_WINDOW_DEFAULT = 3
PRE_OPEN_MINUTES = 5                                    # fetch window starts 09:25 ET

EMPTY_BARS_SCHEMA = {
    "ticker": pl.Utf8, "et": pl.Int64, "open": pl.Float64, "high": pl.Float64,
    "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64,
}


# --------------------------------------------------------------------------- #
# request parsing / small helpers
# --------------------------------------------------------------------------- #


def parse_request(text: str) -> tuple[str, str]:
    day, _, ticker = text.partition(":")
    day, ticker = day.strip(), ticker.strip().upper()
    if len(day) != 10 or not ticker:
        raise ValueError(f"bad request {text!r}; expected DAY:TICKER")
    return day, ticker


def load_requests(path: Path) -> list[tuple[str, str]]:
    obj = json.loads(path.read_text())
    items = obj.get("requests", obj) if isinstance(obj, dict) else obj
    out = []
    for it in items:
        out.append(parse_request(it) if isinstance(it, str)
                   else parse_request(f"{it['day']}:{it['ticker']}"))
    return out


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True))
    os.replace(tmp, path)


def _atomic_write_parquet(path: Path, df: pl.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.write_parquet(tmp)
    os.replace(tmp, path)


def _empty_bars_frame() -> pl.DataFrame:
    return pl.DataFrame(schema=EMPTY_BARS_SCHEMA)


# --------------------------------------------------------------------------- #
# production source: Alpaca historical StockBars (SIP, RAW)
# --------------------------------------------------------------------------- #


def credentials(env_file: Path | None = None) -> tuple[str, str]:
    """Explicit credentials: ``--env-file`` first, then the process environment."""
    key = secret = None
    if env_file is not None:
        if not Path(env_file).exists():
            raise SystemExit(f"--env-file not found: {env_file}")
        from dotenv import dotenv_values
        vals = dotenv_values(env_file)
        key = vals.get("ALPACA_API_KEY")
        secret = vals.get("ALPACA_SECRET_KEY")
    key = key or os.environ.get("ALPACA_API_KEY")
    secret = secret or os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise SystemExit(
            "Alpaca credentials missing: pass --env-file PATH containing "
            "ALPACA_API_KEY/ALPACA_SECRET_KEY, or export both variables")
    return key, secret


def build_client(env_file: Path | None = None):
    """Production data client (Alpaca SIP historical bars)."""
    from alpaca.data.historical import StockHistoricalDataClient
    key, secret = credentials(env_file)
    return StockHistoricalDataClient(key, secret)


def fetch_window(day: str, session_end: int) -> tuple[datetime, datetime]:
    """UTC [start, end] covering the regular session with a small tail."""
    d = _date.fromisoformat(day)
    base = datetime(d.year, d.month, d.day, tzinfo=ET)
    start = base + timedelta(minutes=RTH_FIRST - PRE_OPEN_MINUTES)
    end = base + timedelta(minutes=min(session_end + PRE_OPEN_MINUTES, RTH_LAST_EXCLUSIVE))
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _is_auth_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(tok in msg for tok in ("unauthorized", "forbidden", "401", "403",
                                      "invalid api key", "authentication"))


def _response_frame(resp) -> pl.DataFrame:
    """Provider response -> (ticker, et, ohlcv, day) in ET minutes.

    Conversion is numpy-based so the producer does not require pyarrow.
    """
    df = getattr(resp, "df", None)
    if df is None or len(df) == 0:
        return _empty_bars_frame().with_columns(pl.lit(None, dtype=pl.Utf8).alias("day"))
    d = df.reset_index()
    ts = d["timestamp"]
    if getattr(getattr(ts, "dt", None), "tz", None) is not None:
        ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
    frame = pl.DataFrame({
        "timestamp": pl.Series(ts.to_numpy()),
        "open": pl.Series(d["open"].to_numpy(dtype="float64")),
        "high": pl.Series(d["high"].to_numpy(dtype="float64")),
        "low": pl.Series(d["low"].to_numpy(dtype="float64")),
        "close": pl.Series(d["close"].to_numpy(dtype="float64")),
        "volume": pl.Series(d["volume"].to_numpy(dtype="float64")),
        "symbol": pl.Series(d["symbol"].astype(str).to_numpy()),
    })
    frame = frame.with_columns(
        pl.col("timestamp").cast(pl.Datetime("us")).dt.replace_time_zone("UTC")
          .dt.convert_time_zone("America/New_York").alias("tset"))
    return frame.with_columns(
        (pl.col("tset").dt.hour().cast(pl.Int64) * 60 +
         pl.col("tset").dt.minute().cast(pl.Int64)).alias("et"),
        pl.col("tset").dt.date().cast(pl.Utf8).alias("day"),
    ).select(["symbol", "et", "open", "high", "low", "close", "volume", "day"]).rename(
        {"symbol": "ticker"})


def _request_bars(client, symbols: list[str], start: datetime, end: datetime,
                  sleep=time.sleep, attempts: int = FETCH_ATTEMPTS):
    """One Alpaca SIP/RAW minute-bar request with retry/backoff."""
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    last: Exception | None = None
    for i in range(max(1, attempts)):
        try:
            req = StockBarsRequest(
                symbol_or_symbols=list(symbols), timeframe=TimeFrame.Minute,
                start=start, end=end, feed=DataFeed.SIP, adjustment=Adjustment.RAW)
            return client.get_stock_bars(req)
        except Exception as exc:                       # retried below, re-raised after
            if _is_auth_error(exc):
                raise SystemExit(f"Alpaca rejected the credentials: {exc}") from exc
            last = exc
            if i + 1 < max(1, attempts):
                sleep(FETCH_BACKOFF_S * (2 ** i))
    raise RuntimeError(f"Alpaca bars request failed after {attempts} attempts: {last}")


def _hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _day_meta(day: str, session_end: int, results: dict[str, dict], requested: int,
              **extra) -> dict:
    meta = {
        "kind": "alpaca_sip_raw",
        "feed": "sip",
        "adjustment": "raw",
        "timeframe": "1Min",
        "window_et": [_hhmm(RTH_FIRST - PRE_OPEN_MINUTES),
                      _hhmm(min(session_end + PRE_OPEN_MINUTES, RTH_LAST_EXCLUSIVE))],
        "production": True,
        "certification": "alpaca_sip_raw",
        "requested": requested,
        "returned": sum(1 for r in results.values() if r["status"] == "bars"),
        "no_bars": sum(1 for r in results.values() if r["status"] == "no_bars"),
        "unavailable": sum(1 for r in results.values() if r["status"] == "unavailable"),
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    meta.update(extra)
    return meta


def fetch_day(client, day: str, tickers: list[str], session_end: int,
              sleep=time.sleep, attempts: int = FETCH_ATTEMPTS
              ) -> tuple[dict[str, dict], dict]:
    """Fetch one day's full RTH tape for ``tickers`` from Alpaca SIP/RAW.

    A symbol omitted from the batch response is re-requested individually:
    success-with-no-rows certifies ``no_bars``, a failed request records
    ``unavailable``.  A whole-batch failure marks every ticker ``unavailable``
    (never ``no_bars``).
    """
    tickers = sorted(set(tickers))
    start, end = fetch_window(day, session_end)
    results: dict[str, dict] = {}
    try:
        frame = _response_frame(_request_bars(client, tickers, start, end, sleep, attempts))
    except RuntimeError as exc:
        for t in tickers:
            results[t] = {"status": "unavailable", "rows": None, "error": str(exc)[:300]}
        return results, _day_meta(day, session_end, results, len(tickers),
                                  batch_error=str(exc)[:300])
    frame = frame.filter((pl.col("day") == day) & (pl.col("et") >= RTH_FIRST) &
                         (pl.col("et") <= session_end))
    omitted: list[str] = []
    for t in tickers:
        sub = frame.filter(pl.col("ticker") == t).sort("et")
        if sub.height:
            results[t] = {"status": "bars", "rows": sub}
        else:
            omitted.append(t)
    for t in omitted:
        try:
            resp = _request_bars(client, [t], start, end, sleep, attempts)
            sub = _response_frame(resp).filter(
                (pl.col("day") == day) & (pl.col("et") >= RTH_FIRST) &
                (pl.col("et") <= session_end)).sort("et")
            results[t] = ({"status": "bars", "rows": sub, "resolved": "individual"}
                          if sub.height else
                          {"status": "no_bars", "rows": None, "resolved": "individual"})
        except RuntimeError as exc:
            results[t] = {"status": "unavailable", "rows": None,
                          "error": str(exc)[:300]}
    return results, _day_meta(day, session_end, results, len(tickers),
                              individually_resolved=len(omitted))


# --------------------------------------------------------------------------- #
# test-only fixture source (can never certify production)
# --------------------------------------------------------------------------- #


def fixture_month_path(fixture_root: Path, month: str) -> Path:
    for cand in (fixture_root / f"clean_ohlcv_{month}.parquet",
                 fixture_root / "backfill" / f"clean_ohlcv_{month}.parquet"):
        if cand.exists():
            return cand
    raise FileNotFoundError(
        f"no fixture month for {month} under {fixture_root} "
        f"(looked for clean_ohlcv_{month}.parquet)")


def fixture_day(fixture_root: Path, day: str, tickers: list[str], session_end: int,
                min_symbols: int) -> tuple[dict[str, dict], dict]:
    """Local parquet day layer, stamped test-only (never production-certified)."""
    path = fixture_month_path(fixture_root, day[:7])
    lf = pl.scan_parquet(path).select(
        ["timestamp", "ticker", "open", "high", "low", "close", "volume"])
    lf = lf.with_columns(
        pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("tset"))
    lf = lf.with_columns(
        (pl.col("tset").dt.hour().cast(pl.Int64) * 60 +
         pl.col("tset").dt.minute().cast(pl.Int64)).alias("et"),
        pl.col("tset").dt.date().cast(pl.Utf8).alias("day"),
    )
    full = (lf.filter((pl.col("day") == day) & (pl.col("et") >= RTH_FIRST) &
                      (pl.col("et") < RTH_LAST_EXCLUSIVE))
              .select(["ticker", "et", "open", "high", "low", "close", "volume"])
              .collect())
    symbols = int(full["ticker"].n_unique())
    if symbols < min_symbols:
        raise SystemExit(
            f"{day}: fixture layer has only {symbols} symbols (< --min-symbols "
            f"{min_symbols}); refusing to certify bars/no_bars from a "
            f"non-full-market fixture")
    results: dict[str, dict] = {}
    for t in sorted(set(tickers)):
        sub = full.filter((pl.col("ticker") == t) &
                          (pl.col("et") <= session_end)).sort("et")
        results[t] = ({"status": "bars", "rows": sub} if sub.height else
                      {"status": "no_bars", "rows": None})
    meta = {
        "kind": "fixture",
        "production": False,
        "certification": "fixture_test_only",
        "path": str(path),
        "sha256": _sha256(path),
        "symbols": symbols,
        "min_symbols": min_symbols,
        "requested": len(results),
        "returned": sum(1 for r in results.values() if r["status"] == "bars"),
        "no_bars": sum(1 for r in results.values() if r["status"] == "no_bars"),
        "unavailable": 0,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return results, meta


# --------------------------------------------------------------------------- #
# write-out
# --------------------------------------------------------------------------- #


def _rows_to_frame(day: str, rows: pl.DataFrame) -> pl.DataFrame:
    return rows.with_columns(pl.lit(day, dtype=pl.Utf8).alias("date")).select(
        list(sim.CARRY_BARS_SCHEMA)
    )


def _empty_overlay_frame() -> pl.DataFrame:
    schema = {
        "date": pl.Utf8, "ticker": pl.Utf8, "et": pl.Int64,
        "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
        "close": pl.Float64, "volume": pl.Float64,
    }
    return pl.DataFrame(schema=schema)


def write_overlay(out_root: Path, day: str, per_ticker: dict[str, dict],
                  day_meta: dict, session_end: int) -> dict:
    """Merge one day's extraction into the overlay + manifest; returns entries."""
    day_path = out_root / f"{day}.parquet"
    existing = pl.read_parquet(day_path) if day_path.exists() else _empty_overlay_frame()
    requested = sorted(per_ticker)
    if requested and existing.height:
        # a re-run replaces the requested tickers' rows (a new no_bars/unavailable
        # certification must not leave stale tape behind)
        existing = existing.filter(~pl.col("ticker").is_in(requested))
    entries: dict[str, dict] = {}
    frames = [existing]
    for ticker, info in sorted(per_ticker.items()):
        rows = info["rows"]
        if rows is not None and rows.height:
            frame = _rows_to_frame(day, rows)
            frames.append(frame)
            entries[ticker] = {
                "status": "bars", "certification": day_meta.get("certification"),
                "rows": int(frame.height),
                "first_et": int(frame["et"].min()),
                "last_et": int(frame["et"].max()),
            }
        else:
            if info["status"] == "bars":
                raise SystemExit(
                    f"{day}:{ticker}: extraction produced no RTH rows for a 'bars' entry; "
                    f"refusing to write an unsatisfiable certification")
            entries[ticker] = {"status": info["status"],
                               "certification": day_meta.get("certification"),
                               "rows": 0, "first_et": None, "last_et": None}
        if info.get("error"):
            entries[ticker]["error"] = info["error"]
    merged = pl.concat(frames, how="vertical_relaxed")
    merged = merged.unique(subset=["ticker", "et"], keep="last").sort(["ticker", "et"])
    _atomic_write_parquet(day_path, merged.select(list(sim.CARRY_BARS_SCHEMA)))
    day_meta = dict(day_meta)
    day_meta["overlay_sha256"] = _sha256(day_path)   # engine verifies this on load

    manifest_path = out_root / sim.CARRY_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
        "contract": sim.CONTRACT_VERSION,
        "purpose": "declared full-market RTH carry overlay for cross-session carry resolution",
        "created": "2026-09-24",
        "days": {},
    }
    day_entry = manifest["days"].setdefault(day, {"tickers": {}})
    day_entry["session_end"] = int(session_end)
    day_entry["source"] = day_meta
    day_entry["tickers"].update(entries)
    manifest["contract"] = sim.CONTRACT_VERSION
    _atomic_write_json(manifest_path, manifest)
    return entries


# --------------------------------------------------------------------------- #
# scan (enumerate halt requests from the candidate-bar artifact)
# --------------------------------------------------------------------------- #


def scan_requests(window: int = SCAN_WINDOW_DEFAULT) -> list[tuple[str, str]]:
    """Candidate-tape halt cases -> next ``window`` dev days per case.

    A tape whose last in-session bar is before ``session_end`` cannot satisfy a
    pending action that needs a bar after ``session_end - 1`` (the forced-flat
    exit bar), so those ticker-days are halt cases too.  Stops at dev-block
    boundaries so the overlay never bridges into sealed 2024 / 2025-01.
    """
    days = sim.dev_days()
    sem = sim.session_end_map()
    reqs: set[tuple[str, str]] = set()
    for idx, day in enumerate(days):
        se = sem.get(day, sim.SESSION_END_NORMAL)
        df = pl.read_parquet(sim.BARS_DIR / f"{day}.parquet", columns=["ticker", "et"])
        df = df.filter(pl.col("et") <= se)          # in-session tape only
        g = df.group_by("ticker").agg(pl.col("et").max().alias("last_et"))
        halted = g.filter(pl.col("last_et") < se)
        if halted.height == 0:
            continue
        for t in halted["ticker"].to_list():
            for j in range(idx + 1, min(idx + 1 + window, len(days))):
                gap = (_date.fromisoformat(days[j]) - _date.fromisoformat(days[j - 1])).days
                if gap > sim.DEV_BLOCK_GAP_DAYS:
                    break
                reqs.add((days[j], t))
    return sorted(reqs)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="BASKET-01 Phase-2 carry-bar substrate producer")
    ap.add_argument("--env-file", default=os.environ.get("BASKET_ALPACA_ENV_FILE"),
                    help="explicit .env with ALPACA_API_KEY/ALPACA_SECRET_KEY (production)")
    ap.add_argument("--fixture-root", default=os.environ.get("BASKET_CARRY_FIXTURE_ROOT"),
                    help="TEST-ONLY local parquet layer; entries are never production-certified")
    ap.add_argument("--out-root", default=str(sim.CARRY_BARS_ROOT))
    ap.add_argument("--request", action="append", default=[], metavar="DAY:TICKER")
    ap.add_argument("--requests", default=None, help="JSON file with requests")
    ap.add_argument("--scan", action="store_true",
                    help="enumerate halt cases from candidate bars (see --scan-window)")
    ap.add_argument("--scan-window", type=int, default=SCAN_WINDOW_DEFAULT)
    ap.add_argument("--min-symbols", type=int, default=MIN_SYMBOLS_DEFAULT,
                    help="fixture-mode full-market sanity floor")
    ap.add_argument("--print-only", action="store_true")
    args = ap.parse_args(argv)

    requests: list[tuple[str, str]] = []
    for r in args.request:
        requests.append(parse_request(r))
    if args.requests:
        requests.extend(load_requests(Path(args.requests)))
    if args.scan:
        requests.extend(scan_requests(args.scan_window))
    requests = sorted(set(requests))
    if not requests:
        ap.error("no requests: pass --request DAY:TICKER, --requests FILE, or --scan")
    if args.print_only:
        print(json.dumps({"requests": [f"{d}:{t}" for d, t in requests]}, indent=1))
        return 0

    out_root = Path(args.out_root)
    fixture_root = Path(args.fixture_root) if args.fixture_root else None
    client = None if fixture_root is not None else build_client(
        Path(args.env_file) if args.env_file else None)
    sem = sim.session_end_map()

    by_day: dict[str, list[str]] = {}
    for day, ticker in requests:
        sim.guard_day(day)                       # refuse sealed/reserved before any read
        if day not in sem:
            raise SystemExit(f"{day}: not in the committed session calendar; refusing")
        by_day.setdefault(day, []).append(ticker)

    summary = {"days": {}, "requests": len(requests),
               "source": "fixture_test_only" if fixture_root else "alpaca_sip_raw"}
    for day in sorted(by_day):
        tickers = sorted(set(by_day[day]))
        if fixture_root is not None:
            per_ticker, day_meta = fixture_day(fixture_root, day, tickers, sem[day],
                                               args.min_symbols)
        else:
            per_ticker, day_meta = fetch_day(client, day, tickers, sem[day])
        entries = write_overlay(out_root, day, per_ticker, day_meta, sem[day])
        summary["days"][day] = entries
        print(f"{day}: {day_meta['returned']} bars / {day_meta['no_bars']} no_bars / "
              f"{day_meta['unavailable']} unavailable of {len(entries)} requested "
              f"({day_meta['certification']}) -> {out_root}")
    print(json.dumps({"written": summary, "manifest": str(out_root / sim.CARRY_MANIFEST_NAME)},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
