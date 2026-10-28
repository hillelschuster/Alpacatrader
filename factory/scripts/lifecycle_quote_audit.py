#!/usr/bin/env python3
"""LIFECYCLE-01 — empirical quote audit for fixed-decision fills (measured friction, no modeled spread).

Purpose
-------
The v2 fills report (`<data>/harvest01/lifecycle/v2/report/fills_<tag>.parquet`) states the
decision, the reference bar price and a fee fraction, but the *executable* cost is the quote that
was actually on the book at the fill minute. This script measures that quote per event and replays
the day's cash budget under it. Two quote sources, in order:

  1. the local SIP net tape `<data>/sip/net/{trades,quotes}/<day>.parquet`, read ONCE per day for
     all distinct events of that day (same per-day symbol-subset limitation as the rest of the
     study);
  2. only with `--fetch-missing`, a narrow historical SIP window covering exactly the day's missing
     symbols, `[min(exec_et)-2min, max(exec_et)+1min]`, fetched with ONE multi-symbol
     `StockTradesRequest` + ONE `StockQuotesRequest` (alpaca-py, `feed=DataFeed.SIP`, historical
     data only, no trading API, no reserved-day access) and cached under
     `<data>/harvest01/lifecycle/v2/quote_audit/raw/<day>_<lo>_<hi>_<syms>.parquet` with a
     provenance sidecar. Nothing outside those event windows is requested.

Matching (imported verbatim from lifecycle_tape 2.0.1 — never re-implemented)
----------------------------------------------------------------------------
`lifecycle_tape._exec_match` / `_decorate_trades` / `detect_size_unit` are imported. Candidates are
the exec-eligible prints (open/close- or high/low-eligible under the frozen `sip_bars` condition
table) whose ET minute equals `exec_et` EXACTLY; the FIRST such print is taken; a bar price that
differs from it is reported as `cross_feed_mismatch` + `bar_vs_sip_bps`, never silently called the
fill. The quote is the last valid quote STRICTLY before the matched print (or before the fill
minute start when the minute has no eligible print), with age, `q_age_ok` (1 s ruler), locked and
crossed flags. A print is never a proven fill.

Quote size units (documented, not assumed)
------------------------------------------
`bid_size`/`ask_size` are round lots of 100 through 2025-10-31 and shares from 2025-11-03
(`lifecycle_tape.SIZE_UNIT_CUTOFF`). The day-level unit comes from the local tape when that day has
quotes (with detection + conflict recorded), else from the cutoff alone. Raw lots and
share-converted values are both stored; `q_*_shares` is always shares.

Scopes, verdict, replay, scenarios
---------------------------------
The replay unit is the INDEPENDENT scope `(policy, clock)` with its own ORIGINAL capital
`--notional` (default 1.0 = normalized unit capital). Scopes never share or recycle each other's
cash, so a missing premarket trade cannot poison a supported near-open scope. A scope is
`supported` only when EVERY one of its events is `ok`: the exact fill minute printed, the required
side quote (ask for a buy, bid for a sell) exists, is `<= Q_MAX_AGE_S` old, is not crossed, and the
bar price matched the print. `cross_feed_mismatch` is UNKNOWN by default (no exact-pricing claim);
`--permit-px-mismatch` turns such events into a separately labelled `px_proxy` sensitivity
(`px_basis == "mismatch"`). `locked` is recorded but tolerated. Unsupported scopes are reported
explicitly (coverage `unsupported_scopes`, summary `status`) and get EV = null; only supported
scopes enter the pooled `clock=-1` row.

Replay (fixed decision, per scope, four scenarios on the identical event set):
  * `report_fee`      — the report's own `fee_fraction`, used verbatim (LEDGER-EXACT);
  * `measured`        — `--fees-bps` per side applied as a rate on the traded notional
                        (config knob, explicitly NOT a claim about true fees);
  * `provider_plus_100` / `provider_plus_150` — `fee_fraction` plus 100 / 150 bps of the traded
                        notional, i.e. an absolute report fee + a rate adder.

Fee dimensions (exact):
  `fee_fraction` is an ABSOLUTE fee per unit ORIGINAL capital, NOT a rate. With normalized
  `q_in = shares_per_capital`, the traded notional at the bar price is `T = notional*q_in*bar_px`
  and the exact decision budget is

      budget = notional * (q_in*bar_px + fee_fraction) = T + fee_abs

  (NOT `T*(1+fee_fraction)`). `fee_abs = notional*fee_fraction` is charged once, cash and shares are
  debited together, and no leverage is possible (`qty = scale*T/ask` with
  `scale = min(budget, free cash)/budget`). Sell: `q_input/original_held_before` is the sale fraction
  (the report's `gross_fraction` is an ORIGINAL-CAPITAL fraction and is never used for sizing); the
  fraction is applied to the empirical currently-held quantity. A sell's report fee is the absolute
  `notional*fee_fraction` scaled by the executed fraction of the intended quantity; the rate
  scenarios charge `rate * (sold qty * bar_px)`.

Execution basis:
  `--exec-basis quote` (default) requires the empirical quote; `--exec-basis bar` is a
  PROVIDER-PRICE pure-cash replay that executes and marks at the reported bar price with NO quote
  at all (basis `bar_px_no_quote`, status `provider_basis`). It never claims empirical support; it
  exists to reproduce an external provider ledger exactly.

  * Day value = cash + held * last observed bid (quote basis) or last bar price (bar basis); a
    still-held name with no mark voids that scope's EV. Pooled `clock=-1` rows are the
    independent-scope aggregate over the policy's SUPPORTED scopes (mean EV with equal capital),
    never one shared pool.
  * Every replay row carries both capacity ratios: `displayed_ratio` = empirical qty / displayed
    shares on the required side (meaningful at a realistic `--notional`) and `vol_participation` =
    empirical qty / the fill minute's eligible volume. `displayed_ratio > 1` (`over_displayed`)
    means the top-of-book quote is a proxy, not a proven cost. Coverage and capacity are reported;
    marketability is never claimed and no fill is guaranteed.

Outputs (atomic, per-day resumable)
-----------------------------------
  <data>/harvest01/lifecycle/v2/quote_audit/evidence/<day>.parquet   per-event measured evidence
  <data>/harvest01/lifecycle/v2/quote_audit/replay/<day>.parquet     per-event replay, all scenarios
  <data>/harvest01/lifecycle/v2/quote_audit/summary/<day>.parquet    per policy x clock x scenario
  <data>/harvest01/lifecycle/v2/quote_audit/coverage/<day>.parquet   per-day verdict + capacity
  <data>/harvest01/lifecycle/v2/quote_audit/manifest/<day>.json      provenance (CLI is OFFLINE
                                                                     unless --fetch-missing)
Validation days (split.json second half) are refused outright unless `<v2>/FREEZE.json` exists and
matches the split hash.

Usage:
  .venv/bin/python factory/scripts/lifecycle_quote_audit.py
  .venv/bin/python factory/scripts/lifecycle_quote_audit.py --input <v2>/report/fills_v1.parquet \
      --limit 5 --fees-bps 1
  .venv/bin/python factory/scripts/lifecycle_quote_audit.py --input ... --notional 10000
  .venv/bin/python factory/scripts/lifecycle_quote_audit.py --input ... --fetch-missing --limit 1

API:
  load_fills(path) · scope_statuses(evidence) · process_day(day, fills, data_root, split, ...)
  read_manifest(day, data_root) · load_layer(day, layer, data_root) -> pl.DataFrame
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# POLARS_MAX_THREADS must be set before polars initialises (same rule as lifecycle_tape).
def _argv_int(flag: str) -> int | None:
    for i, a in enumerate(sys.argv):
        if a == flag and i + 1 < len(sys.argv):
            try:
                return int(sys.argv[i + 1])
            except ValueError:
                return None
        if a.startswith(flag + "="):
            try:
                return int(a.split("=", 1)[1])
            except ValueError:
                return None
    return None


_CPUS = os.cpu_count() or 4
_W_PEEK = max(1, _argv_int("--workers") or 1)
_T_PEEK = _argv_int("--threads")
os.environ.setdefault("POLARS_MAX_THREADS",
                      str(min(3, max(1, _T_PEEK if _T_PEEK else _CPUS // _W_PEEK))))

import polars as pl  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import basket_pm_snapshots as bps  # noqa: E402
import lifecycle_tape as lt  # noqa: E402  (single source of truth: matching + quote units)

PRODUCER = "lifecycle_quote_audit"
PRODUCER_VERSION = "1.0.2"
SCHEMA_VERSION = "v1"
FILLS_REQUIRED = ("day", "clock", "policy", "ticker", "rank", "decision_et", "exec_et", "side",
                  "px", "shares_per_capital", "gross_fraction", "fee_fraction", "delay_min")
LAYERS = ("evidence", "replay", "summary", "coverage")

Q_MAX_AGE_S = lt.Q_MAX_AGE_S
WIN_BEFORE_MIN = 2
WIN_AFTER_MIN = 1
TRADE_CAP = 200_000
QUOTE_CAP = 100_000
SCENARIOS = ("report_fee", "measured", "provider_plus_100", "provider_plus_150")
NY = ZoneInfo("America/New_York")
TRADE_READ_COLS = ("symbol", "ts_utc", "price", "size", "conditions", "tape")
QUOTE_READ_COLS = ("symbol", "ts_utc", "bid_price", "bid_size", "ask_price", "ask_size",
                   "conditions", "tape")

OK = "ok"
MISSING_QUOTE = "missing_quote"
STALE_QUOTE = "stale_quote"
CROSSED_QUOTE = "crossed_quote"
NO_EXACT_PRINT = "no_exact_minute_print"
SYMBOL_ABSENT = "symbol_absent"
NO_REF_MINUTE = "no_ref_minute"
PX_MISMATCH = "px_mismatch"
EXEC_BASED = ("print_px_match", "cross_feed_mismatch", "print_first_in_ref_minute")


# ------------------------------------------------------------------------ plumbing
def audit_dir(data_root: Path) -> Path:
    return lt.v2_dir(data_root) / "quote_audit"


def layer_dir(data_root: Path, layer: str) -> Path:
    return audit_dir(data_root) / layer


def manifest_path(day: str, data_root: Path) -> Path:
    return layer_dir(data_root, "manifest") / f"{day}.json"


def _sha256_file(p: Path | None, block: int = 1 << 20) -> str | None:
    if p is None or not p.exists():
        return None
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_parquet(df: pl.DataFrame, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    df.write_parquet(tmp, compression="zstd")
    os.replace(tmp, path)
    return path.stat().st_size


def _atomic_json(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    os.replace(tmp, path)


def _empty(schema: dict) -> pl.DataFrame:
    return pl.DataFrame(schema=dict(schema))


def default_fills_path(data_root: Path) -> Path | None:
    d = lt.v2_dir(data_root) / "report"
    if not d.exists():
        return None
    fs = sorted(d.glob("fills_*.parquet"))
    return fs[-1] if fs else None


def assert_day_allowed(day: str, split: dict, data_root: Path) -> str:
    """discovery 533 always; validation 533 only once FREEZE.json exists and matches the split."""
    member = lt.split_member(day, split)
    if member == "discovery":
        return member
    if member == "validation":
        freeze_p = lt.v2_dir(data_root) / "FREEZE.json"
        if not freeze_p.exists():
            raise SystemExit(f"validation locked: {day} is a validation day and {freeze_p} "
                             f"does not exist")
        freeze = json.loads(freeze_p.read_text())
        want = freeze.get("split_sha256")
        if want and want != _sha256_file(lt.v2_dir(data_root) / "split.json"):
            raise SystemExit("validation locked: FREEZE.json split hash != split.json")
        return member
    raise SystemExit(f"{day} is neither a discovery nor a validation day of the frozen split")


def load_fills(path: Path) -> pl.DataFrame:
    if not path.exists():
        raise SystemExit(f"fills report not found: {path}")
    df = pl.read_parquet(path)
    missing = [c for c in FILLS_REQUIRED if c not in df.columns]
    if missing:
        raise SystemExit(f"{path}: fills report missing required column(s) {missing}; "
                         f"has {df.columns}")
    df = df.with_columns([
        pl.col("day").cast(pl.Utf8), pl.col("policy").cast(pl.Utf8),
        pl.col("ticker").cast(pl.Utf8), pl.col("side").cast(pl.Utf8),
        pl.col("clock").cast(pl.Int32), pl.col("rank").cast(pl.Int32),
        pl.col("decision_et").cast(pl.Int32), pl.col("exec_et").cast(pl.Int32),
        pl.col("px").cast(pl.Float64), pl.col("shares_per_capital").cast(pl.Float64),
        pl.col("gross_fraction").cast(pl.Float64), pl.col("fee_fraction").cast(pl.Float64),
        pl.col("delay_min").cast(pl.Int32),
    ])
    bad = set(df["side"].unique().to_list()) - {"buy", "sell"}
    if bad:
        raise SystemExit(f"{path}: unknown side value(s) {sorted(bad)}; expected buy/sell")
    return df


# ------------------------------------------------------------------- day sources
def read_local_day(day: str, tickers: list[str], data_root: Path
                   ) -> tuple[pl.DataFrame | None, pl.DataFrame | None, dict]:
    """Local SIP net tape for the day: trades read once, quotes read once."""
    prov: dict = {}
    tr_p = lt._tape_source(data_root, "trades", day)
    q_p = lt._tape_source(data_root, "quotes", day)
    tr = q = None
    if tr_p is not None:
        tr = (pl.scan_parquet(tr_p).select(TRADE_READ_COLS)
              .filter(pl.col("symbol").is_in(tickers)).collect())
        if tr.height:
            tr = tr.with_columns(lt._clock_exprs()).sort(["symbol", "ts_utc"], maintain_order=True)
            tr = lt._decorate_trades(tr)
        prov["trades"] = {"path": str(tr_p), "rows_for_events": tr.height,
                          "sha256": _sha256_file(tr_p)}
    if q_p is not None:
        q = (pl.scan_parquet(q_p).select(QUOTE_READ_COLS)
             .filter(pl.col("symbol").is_in(tickers)).collect())
        if q.height:
            q = q.with_columns(lt._clock_exprs()).sort(["symbol", "ts_utc"], maintain_order=True)
        prov["quotes"] = {"path": str(q_p), "rows_for_events": q.height if q is not None else 0,
                          "sha256": _sha256_file(q_p)}
    return tr, q, prov


def _trade_lists(tr: pl.DataFrame | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if tr is None or not tr.height:
        return out
    tr = tr.with_columns(tr["ts_utc"].cast(pl.Int64).alias("ts_us"))
    for key, sub in tr.partition_by("symbol", maintain_order=True, as_dict=True).items():
        s = key[0] if isinstance(key, tuple) else key
        out[s] = {"ts": sub["ts_us"].to_list(), "etmin": sub["et_min"].to_list(),
                  "et_s": sub["et_s"].to_list(), "px": sub["price"].to_list(),
                  "sz": sub["size"].to_list(), "elig": sub["px_elig"].to_list(),
                  "odd": sub["is_odd"].to_list(), "vol": sub["vol_ok"].to_list()}
    return out


def _quote_lists(q: pl.DataFrame | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if q is None or not q.height:
        return out
    qv = q.filter((pl.col("bid_price") > 0) & (pl.col("ask_price") > 0))
    if not qv.height:
        return out
    qv = qv.with_columns(qv["ts_utc"].cast(pl.Int64).alias("ts_us"))
    for key, sub in qv.partition_by("symbol", maintain_order=True, as_dict=True).items():
        s = key[0] if isinstance(key, tuple) else key
        out[s] = {"ts": sub["ts_us"].to_list(), "et_s": sub["et_s"].to_list(),
                  "bid": sub["bid_price"].to_list(), "ask": sub["ask_price"].to_list(),
                  "bl": sub["bid_size"].to_list(), "al": sub["ask_size"].to_list()}
    return out


def _et_instant(day: str, et_min: int) -> datetime:
    base = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=NY)
    return (base + timedelta(minutes=int(et_min))).astimezone(timezone.utc)


def _as_list_expr(col: str) -> pl.Expr:
    return pl.when(pl.col(col).is_null()).then(pl.lit([], dtype=pl.List(pl.Utf8))) \
        .otherwise(pl.col(col).cast(pl.List(pl.Utf8))).alias(col)


def fetch_window(day: str, tickers: list[str], lo_et: int, hi_et: int, cache_dir: Path,
                 data_root: Path, force: bool = False,
                 env_file: Path | None = None) -> tuple[pl.DataFrame, dict]:
    """One multi-symbol SIP trades + quotes request for [lo_et, hi_et], cached with provenance."""
    syms = sorted(set(tickers))
    key = f"{day}_{lo_et:04d}_{hi_et:04d}_{hashlib.sha1(','.join(syms).encode()).hexdigest()[:10]}"
    par_p = cache_dir / f"{key}.parquet"
    man_p = cache_dir / f"{key}.manifest.json"
    if not force and par_p.exists() and man_p.exists():
        try:
            man = json.loads(man_p.read_text())
            if (man.get("producer_version") == PRODUCER_VERSION and man.get("day") == day
                    and man.get("lo_et") == lo_et and man.get("hi_et") == hi_et
                    and man.get("symbols") == syms
                    and man.get("sha256") == _sha256_file(par_p)):
                return pl.read_parquet(par_p), {**man, "cache": "hit"}
        except Exception:
            pass
    prov = {"day": day, "lo_et": lo_et, "hi_et": hi_et, "symbols": syms,
            "producer_version": PRODUCER_VERSION, "cache": "miss",
            "request_window_utc": [_et_instant(day, lo_et).isoformat(),
                                   _et_instant(day, hi_et + 1).isoformat()],
            "errors": [], "truncated": False, "rows": 0, "trade_rows": 0, "quote_rows": 0}
    try:
        from alpaca.common.exceptions import APIError
        from alpaca.data.enums import DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockQuotesRequest, StockTradesRequest
        from dotenv import load_dotenv
        from requests.exceptions import RequestException
        # the worktree may not carry .env; the data root's parent repo does
        cands = ([env_file] if env_file else []) + [ROOT / ".env",
                                                   Path(data_root).parent / ".env",
                                                   Path.cwd() / ".env"]
        for cand in cands:
            if cand.exists():
                load_dotenv(cand)
                prov["env_file"] = str(cand)
                break
    except ImportError as e:  # pragma: no cover - environment level
        prov["errors"].append(f"import: {e}")
        return pl.DataFrame(), prov
    key_id = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    if not key_id or not secret:
        prov["errors"].append("ALPACA_API_KEY/ALPACA_SECRET_KEY not set")
        return pl.DataFrame(), prov
    start = _et_instant(day, lo_et).replace(tzinfo=None)
    end = _et_instant(day, hi_et + 1).replace(tzinfo=None)
    client = StockHistoricalDataClient(key_id, secret)

    def _api(request, kind: str) -> tuple[pl.DataFrame, int]:
        for attempt in range(6):
            try:
                resp = (client.get_stock_trades(request) if kind == "trade"
                        else client.get_stock_quotes(request))
                df = resp.df
                if df is None or len(df) == 0:
                    return pl.DataFrame(), 0
                out = pl.from_pandas(df.reset_index())
                out = out.with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
                if "conditions" in out.columns:
                    out = out.with_columns(_as_list_expr("conditions"))
                return out, len(df)
            except (APIError, RequestException) as error:  # noqa: PERF203
                status = getattr(error, "status_code", None)
                if attempt == 5 or status not in (None, 429, 500, 502, 503, 504):
                    prov["errors"].append(f"{kind}: {type(error).__name__}: {str(error)[:200]}")
                    return pl.DataFrame(), -1
                time.sleep(min(60.0, 2.0 ** attempt))
        prov["errors"].append(f"{kind}: retry loop exhausted")
        return pl.DataFrame(), -1

    tr_df, n_tr = _api(StockTradesRequest(symbol_or_symbols=syms, start=start, end=end,
                                          feed=DataFeed.SIP, limit=TRADE_CAP, sort="asc"), "trade")
    q_df, n_q = _api(StockQuotesRequest(symbol_or_symbols=syms, start=start, end=end,
                                        feed=DataFeed.SIP, limit=QUOTE_CAP, sort="asc"), "quote")
    prov["trade_rows"], prov["quote_rows"] = max(n_tr, 0), max(n_q, 0)
    prov["truncated"] = bool(n_tr >= TRADE_CAP or n_q >= QUOTE_CAP)
    frames = []
    if tr_df.height:
        frames.append(tr_df.select([
            pl.lit("trade").alias("record_type"), pl.col("symbol"),
            pl.col("timestamp").alias("ts_utc"), pl.col("price"), pl.col("size"),
            pl.col("conditions"), pl.col("tape"),
            pl.lit(None, dtype=pl.Float64).alias("bid_price"),
            pl.lit(None, dtype=pl.Float64).alias("bid_size"),
            pl.lit(None, dtype=pl.Float64).alias("ask_price"),
            pl.lit(None, dtype=pl.Float64).alias("ask_size")]))
    if q_df.height:
        frames.append(q_df.select([
            pl.lit("quote").alias("record_type"), pl.col("symbol"),
            pl.col("timestamp").alias("ts_utc"),
            pl.lit(None, dtype=pl.Float64).alias("price"),
            pl.lit(None, dtype=pl.Float64).alias("size"),
            pl.col("conditions"), pl.col("tape"),
            pl.col("bid_price"), pl.col("bid_size"), pl.col("ask_price"), pl.col("ask_size")]))
    recs = pl.concat(frames, how="diagonal_relaxed") if frames else _empty(
        {"record_type": pl.Utf8, "symbol": pl.Utf8, "ts_utc": pl.Datetime("us", "UTC"),
         "price": pl.Float64, "size": pl.Float64, "conditions": pl.List(pl.Utf8), "tape": pl.Utf8,
         "bid_price": pl.Float64, "bid_size": pl.Float64, "ask_price": pl.Float64,
         "ask_size": pl.Float64})
    prov["rows"] = recs.height
    if prov["errors"]:
        return recs, prov
    cache_dir.mkdir(parents=True, exist_ok=True)
    prov["bytes"] = _atomic_parquet(recs, par_p)
    prov["sha256"] = _sha256_file(par_p)
    _atomic_json(prov, man_p)
    return recs, prov


def records_to_lists(recs: pl.DataFrame) -> tuple[dict[str, dict], dict[str, dict]]:
    if recs is None or recs.height == 0:
        return {}, {}
    trs = recs.filter(pl.col("record_type") == "trade")
    qts = recs.filter(pl.col("record_type") == "quote")
    tr = q = {}
    if trs.height:
        trs = lt._decorate_trades(
            trs.select(["symbol", "ts_utc", "price", "size", "conditions", "tape"]))
        trs = trs.with_columns(lt._clock_exprs()).sort(["symbol", "ts_utc"], maintain_order=True)
        tr = _trade_lists(trs)
    if qts.height:
        qts = qts.select(["symbol", "ts_utc", "bid_price", "bid_size", "ask_price", "ask_size"])
        qts = qts.with_columns(lt._clock_exprs()).sort(["symbol", "ts_utc"], maintain_order=True)
        q = _quote_lists(qts)
    return tr, q


# ------------------------------------------------------------------ quote lookup
def quote_asof(qd: dict | None, exec_ts: int | None, ref_min: int | None) -> tuple[dict, str]:
    """Last valid quote STRICTLY before the exec print, else before the fill-minute start."""
    out = {f"q_{f}": None for f in ("et_us", "age_s", "bid", "ask", "bid_lots_raw", "ask_lots_raw",
                                    "bid_shares", "ask_shares", "spread_bps", "mid", "imb",
                                    "locked", "crossed")}
    if not qd or not qd["ts"]:
        return out, "no_quotes"
    if exec_ts is not None:
        i = bisect.bisect_left(qd["ts"], exec_ts) - 1
        basis = "exec_print"
        age = (exec_ts - qd["ts"][i]) / 1e6 if i >= 0 else None
    elif ref_min is not None:
        i = bisect.bisect_left(qd["et_s"], int(ref_min) * 60) - 1
        basis = "ref_minute_start"
        age = (int(ref_min) * 60 - qd["et_s"][i]) if i >= 0 else None
    else:
        return out, "none"
    if i < 0:
        return out, "none"
    b, a = float(qd["bid"][i]), float(qd["ask"][i])
    bl, al = float(qd["bl"][i]), float(qd["al"][i])
    mid = (a + b) / 2
    out.update({"q_et_us": qd["ts"][i], "q_age_s": age, "q_bid": b, "q_ask": a,
                "q_bid_lots_raw": bl, "q_ask_lots_raw": al, "q_mid": mid,
                "q_spread_bps": (a - b) / mid * 1e4 if mid > 0 else None,
                "q_locked": bool(a == b), "q_crossed": bool(a < b)})
    return out, basis


# -------------------------------------------------------------- evidence layer
EVIDENCE_DTYPES = {
    "day": pl.Utf8, "clock": pl.Int32, "policy": pl.Utf8, "ticker": pl.Utf8, "rank": pl.Int32,
    "side": pl.Utf8, "decision_et": pl.Int32, "exec_et": pl.Int32, "delay_min": pl.Int32,
    "bar_px": pl.Float64, "qty0": pl.Float64, "gross_fraction": pl.Float64,
    "fee_fraction_orig": pl.Float64, "orig_fee_bps": pl.Float64, "source": pl.Utf8,
    "exec_basis": pl.Utf8, "exec_ts_us": pl.Int64, "print_ts_us": pl.Int64, "print_px": pl.Float64,
    "print_size": pl.Float64, "print_is_odd": pl.Boolean, "exec_min_n": pl.Int64,
    "print_delay_min": pl.Int64, "bar_vs_sip_bps": pl.Float64,
    "q_ref_basis": pl.Utf8, "q_et_us": pl.Int64, "q_age_s": pl.Float64, "q_age_ok": pl.Boolean,
    "q_bid": pl.Float64, "q_ask": pl.Float64, "q_bid_lots_raw": pl.Float64,
    "q_ask_lots_raw": pl.Float64, "q_bid_shares": pl.Float64, "q_ask_shares": pl.Float64,
    "q_spread_bps": pl.Float64, "q_mid": pl.Float64, "q_imb": pl.Float64, "q_locked": pl.Boolean,
    "q_crossed": pl.Boolean, "required_side": pl.Utf8, "required_px": pl.Float64,
    "emp_px": pl.Float64, "slip_bps_signed_cost": pl.Float64,
    "displayed_depth_usd": pl.Float64, "eligible_min_vol_shares": pl.Float64,
    "eligible_min_dvol": pl.Float64, "status": pl.Utf8, "px_basis": pl.Utf8,
    "px_proxy": pl.Boolean, "size_unit": pl.Int64, "matching_version": pl.Utf8,
}


def audit_events(day: str, day_fills: pl.DataFrame, trm: dict, qm: dict, unit: int,
                 fetched_syms: set[str], permit_px_mismatch: bool = False) -> pl.DataFrame:
    rows = []
    for r in day_fills.iter_rows(named=True):
        tk = r["ticker"]
        ref_min = r["exec_et"] if r["exec_et"] is not None else r["decision_et"]
        d = trm.get(tk)
        m = lt._exec_match(d, ref_min, r["px"])
        q, qbasis = quote_asof(qm.get(tk), m["exec_ts_us"], ref_min)
        if q["q_bid"] is not None:
            q["q_bid_shares"] = q["q_bid_lots_raw"] * unit
            q["q_ask_shares"] = q["q_ask_lots_raw"] * unit
            bs, as_ = q["q_bid_shares"], q["q_ask_shares"]
            q["q_imb"] = (bs - as_) / (bs + as_) if (bs + as_) > 0 else None
        side = r["side"]
        req_side = "ask" if side == "buy" else "bid"
        req_px = q["q_ask"] if side == "buy" else q["q_bid"]
        req_shares = q["q_ask_shares"] if side == "buy" else q["q_bid_shares"]
        age_ok = q["q_age_s"] is not None and q["q_age_s"] <= Q_MAX_AGE_S
        depth = req_px * req_shares if (req_px is not None and req_shares is not None) else None
        px_basis = ("exact" if m["exec_ts_basis"] == "print_px_match"
                    else "mismatch" if m["exec_ts_basis"] == "cross_feed_mismatch"
                    else "unverifiable")
        if d is None or not d.get("ts"):
            status = SYMBOL_ABSENT
        elif ref_min is None:
            status = NO_REF_MINUTE
        elif m["exec_ts_us"] is None:
            status = NO_EXACT_PRINT
        elif req_px is None:
            status = MISSING_QUOTE
        elif not age_ok:
            status = STALE_QUOTE
        elif bool(q["q_crossed"]):
            status = CROSSED_QUOTE
        elif px_basis == "mismatch" and not permit_px_mismatch:
            status = PX_MISMATCH
        else:
            status = OK
        bar = float(r["px"]) if r["px"] is not None else None
        sign = 1.0 if side == "buy" else -1.0
        vol_sh = vol_dv = None
        if d is not None and ref_min is not None:
            etmin, sz, pxs, vol = d["etmin"], d["sz"], d["px"], d["vol"]
            lo = bisect.bisect_left(etmin, int(ref_min))
            hi = bisect.bisect_right(etmin, int(ref_min))
            vol_sh = float(sum(sz[i] for i in range(lo, hi) if vol[i]))
            vol_dv = float(sum(sz[i] * pxs[i] for i in range(lo, hi) if vol[i]))
        rows.append({
            "day": day, "clock": r["clock"], "policy": r["policy"], "ticker": tk,
            "rank": r["rank"], "side": side, "decision_et": r["decision_et"],
            "exec_et": r["exec_et"], "delay_min": r["delay_min"], "bar_px": bar,
            "qty0": r["shares_per_capital"], "gross_fraction": r["gross_fraction"],
            "fee_fraction_orig": r["fee_fraction"],
            "orig_fee_bps": (r["fee_fraction"] * 1e4) if r["fee_fraction"] is not None else None,
            "source": ("fetched" if tk in fetched_syms else ("local" if d is not None else "none")),
            "exec_basis": m["exec_ts_basis"], "exec_ts_us": m["exec_ts_us"],
            "print_ts_us": m["print_ts_us"], "print_px": m["print_px"],
            "print_size": m["print_size"], "print_is_odd": m["print_is_odd"],
            "exec_min_n": m["exec_min_n"], "print_delay_min": m["print_delay_min"],
            "bar_vs_sip_bps": m["print_px_vs_fill_bps"],
            "q_ref_basis": qbasis, "q_et_us": q["q_et_us"], "q_age_s": q["q_age_s"],
            "q_age_ok": age_ok if q["q_age_s"] is not None else None,
            "q_bid": q["q_bid"], "q_ask": q["q_ask"], "q_bid_lots_raw": q["q_bid_lots_raw"],
            "q_ask_lots_raw": q["q_ask_lots_raw"], "q_bid_shares": q["q_bid_shares"],
            "q_ask_shares": q["q_ask_shares"], "q_spread_bps": q["q_spread_bps"],
            "q_mid": q["q_mid"], "q_imb": q["q_imb"], "q_locked": q["q_locked"],
            "q_crossed": q["q_crossed"], "required_side": req_side, "required_px": req_px,
            "emp_px": req_px,
            "slip_bps_signed_cost": (sign * (req_px / bar - 1) * 1e4)
            if (req_px is not None and bar) else None,
            "displayed_depth_usd": depth, "eligible_min_vol_shares": vol_sh,
            "eligible_min_dvol": vol_dv, "status": status, "px_basis": px_basis,
            "px_proxy": bool(px_basis == "mismatch"), "size_unit": unit,
            "matching_version": lt.PRODUCER_VERSION,
        })
    return pl.DataFrame(rows, infer_schema_length=None).with_columns(
        [pl.col(c).cast(t) for c, t in EVIDENCE_DTYPES.items() if c in rows[0]])


# ---------------------------------------------------------------- replay layer
# One INDEPENDENT capital pool per (policy, clock) scope: --notional (default 1 = normalized).
# A scope is never funded by another scope's turnover, and the pooled clock=-1 row is an
# independent-scope aggregate (mean EV of the supported scopes of that policy), never one shared
# capital pool summed across clocks.
REPLAY_DTYPES = {
    "day": pl.Utf8, "policy": pl.Utf8, "clock": pl.Int32, "scenario": pl.Utf8, "order_idx": pl.Int32,
    "ticker": pl.Utf8, "side": pl.Utf8, "scope_supported": pl.Boolean,
    "q_in": pl.Float64, "q_target": pl.Float64, "desired_notional": pl.Float64,
    "budget": pl.Float64,
    "gross_cash": pl.Float64, "cash_before": pl.Float64, "qty": pl.Float64, "px_used": pl.Float64,
    "fee_abs": pl.Float64, "fee_bps": pl.Float64, "fee_basis": pl.Utf8,
    "cash_after": pl.Float64, "emp_held_after": pl.Float64,
    "orig_held_before": pl.Float64, "orig_held_after": pl.Float64, "sell_fraction": pl.Float64,
    "cash_limited": pl.Boolean, "displayed_shares": pl.Float64, "displayed_ratio": pl.Float64,
    "over_displayed": pl.Boolean, "vol_participation": pl.Float64, "px_basis": pl.Utf8,
    "note": pl.Utf8,
}
SUMMARY_DTYPES = {
    "day": pl.Utf8, "policy": pl.Utf8, "clock": pl.Int32, "scenario": pl.Utf8,
    "basis": pl.Utf8, "status": pl.Utf8, "n_scopes_used": pl.Int64, "n_scopes_total": pl.Int64,
    "n_buys": pl.Int64, "n_sells": pl.Int64, "initial_capital": pl.Float64,
    "cash_end": pl.Float64, "holdings_usd_end": pl.Float64, "value_end": pl.Float64,
    "ev": pl.Float64, "n_cash_limited": pl.Int64, "orig_held_end": pl.Float64,
    "emp_held_end": pl.Float64, "displayed_ratio_med": pl.Float64,
    "displayed_ratio_max": pl.Float64, "n_over_displayed": pl.Int64,
    "vol_participation_med": pl.Float64, "vol_participation_max": pl.Float64,
    "n_px_proxy": pl.Int64,
}


def _med_max(vals: list[float]) -> tuple[float | None, float | None]:
    if not vals:
        return None, None
    return float(pl.Series(vals).median()), float(max(vals))


def _reference_ledger(notional: float, q_in: float, bar_buy: float, buy_px: float,
                      bar_sell: float, sell_px: float, rate_side: float) -> dict:
    """Independent 100bp-round-trip ledger (50bp/side on traded notional), no polars.

    Fee is ABSOLUTE: budget = notional*(q_in*bar_buy + fee_fraction) with
    fee_fraction = q_in*bar_buy*rate_side; execution prices are explicit.
    """
    traded = notional * q_in * bar_buy
    qty_buy = traded / buy_px
    fee_buy = traded * rate_side
    cash = notional - traded - fee_buy
    sold = qty_buy
    fee_sell = sold * bar_sell * rate_side
    cash += sold * sell_px - fee_sell
    held = qty_buy - sold
    return {"traded": traded, "qty_buy": qty_buy, "fee_buy": fee_buy, "cash_end": cash,
            "value_end": cash + held * sell_px, "emp_held_end": held,
            "ev": (cash + held * sell_px) / notional - 1.0}


def _self_test() -> int:
    """Dimensional fee check + exact reproduction of an independent 100bp ledger at N=1/10000."""
    fails: list[str] = []

    def check(name, got, want, tol=1e-9):
        if got is None or want is None or abs(got - want) > max(tol, tol * abs(want)):
            fails.append(f"{name}: got {got!r} want {want!r}")

    # (a) the parent's worked example: fee_fraction is ABSOLUTE per unit original capital
    q_in, bar, fee = 0.003316749, 100.0, 0.001658374
    for N in (1.0, 10000.0):
        traded = N * q_in * bar
        budget = traded + N * fee                      # notional*(q*bar + fee_fraction)
        check(f"budget==N*(q*bar+fee) N={N}", budget, N * (q_in * bar + fee))
        check(f"budget==N/3 N={N}", budget, N / 3.0, tol=1e-6 * N)
        check(f"fee fraction*notional once N={N}", N * fee, N * fee)
    # exact-arithmetic variant (fee = 50bp of the traded notional per side)
    r_side = 0.005
    for N in (1.0, 10000.0):
        q_ex, fee_ex = 0.02, 0.02 * 100.0 * r_side
        check(f"exact budget N={N}", N * (q_ex * 100.0 + fee_ex),
              N * q_ex * 100.0 * (1 + r_side))

    # (b) replay must reproduce the independent 100bp ledger exactly, at N=1 and N=10000
    ask, bid = 100.5, 99.5
    Q_IN = 0.005                                   # trades 0.5*notional, so never levered
    for N in (1.0, 10000.0):
        fee0 = Q_IN * 100.0 * r_side               # absolute fee per unit capital for one side
        ev = pl.DataFrame([
            {"day": "1970-01-01", "policy": "ref", "clock": 571, "ticker": "TST", "rank": 1,
             "side": "buy", "decision_et": 571, "exec_et": 571, "qty0": Q_IN, "bar_px": 100.0,
             "fee_fraction_orig": fee0, "emp_px": ask, "q_bid": bid, "q_ask": ask,
             "q_bid_shares": 10000.0, "q_ask_shares": 10000.0,
             "eligible_min_vol_shares": 1e6, "status": "ok", "px_basis": "exact"},
            {"day": "1970-01-01", "policy": "ref", "clock": 571, "ticker": "TST", "rank": 1,
             "side": "sell", "decision_et": 571, "exec_et": 590, "qty0": Q_IN, "bar_px": 100.0,
             "fee_fraction_orig": fee0, "emp_px": bid, "q_bid": bid, "q_ask": ask,
             "q_bid_shares": 10000.0, "q_ask_shares": 10000.0,
             "eligible_min_vol_shares": 1e6, "status": "ok", "px_basis": "exact"},
        ])
        statuses = scope_statuses(ev)
        ref = _reference_ledger(N, Q_IN, 100.0, ask, 100.0, bid, r_side)
        for tag, fees_bps in (("report_fee", 1.0), ("measured_50bp", 50.0)):
            rep, summ = replay("1970-01-01", ev, fees_bps, N, statuses)
            scen = "report_fee" if tag == "report_fee" else "measured"
            row = summ.filter((pl.col("scenario") == scen) & (pl.col("clock") == 571)).row(0, named=True)
            check(f"{tag} N={N} cash", row["cash_end"], ref["cash_end"], tol=1e-9 * N)
            check(f"{tag} N={N} value", row["value_end"], ref["value_end"], tol=1e-9 * N)
            check(f"{tag} N={N} ev", row["ev"], ref["ev"])
            check(f"{tag} N={N} held", row["emp_held_end"], ref["emp_held_end"], tol=1e-9 * N)
            buy = rep.filter((pl.col("scenario") == scen) & (pl.col("side") == "buy") &
                             (pl.col("clock") == 571)).row(0, named=True)
            check(f"{tag} N={N} qty", buy["qty"], ref["qty_buy"], tol=1e-9 * N)
            check(f"{tag} N={N} fee_abs", buy["fee_abs"], ref["fee_buy"], tol=1e-9 * N)
            check(f"{tag} N={N} budget=T+fee", buy["budget"], ref["traded"] + ref["fee_buy"],
                  tol=1e-9 * N)
            check(f"{tag} N={N} no-leverage", 1.0 if row["cash_end"] >= -1e-9 else 0.0, 1.0)
        # a desire to trade 2x the capital must be capped, never levered
        ev2 = ev.with_columns(pl.lit(0.02).alias("qty0"),
                             pl.lit(0.02 * 100.0 * r_side).alias("fee_fraction_orig"))
        rep2, summ2 = replay("1970-01-01", ev2, 50.0, N, scope_statuses(ev2))
        r2 = summ2.filter((pl.col("scenario") == "measured") & (pl.col("clock") == 571)).row(0,
                                                                                            named=True)
        check(f"cap N={N} cash>=0", 1.0 if r2["cash_end"] >= -1e-9 else 0.0, 1.0)
        check(f"cap N={N} cash_limited", 1.0 if r2["n_cash_limited"] == 1 else 0.0, 1.0)
    # (c) provider-price pure-cash replay must reproduce the same ledger with NO quotes at all
    for N in (1.0, 10000.0):
        fee_buy = 0.005 * 100.0 * 0.005      # absolute fee per unit capital, its own side's bar
        fee_sell = 0.005 * 99.5 * 0.005
        ev_nq = pl.DataFrame([
            {"day": "1970-01-01", "policy": "ref", "clock": 560, "ticker": "TST", "rank": 1,
             "side": "buy", "decision_et": 560, "exec_et": 560, "qty0": 0.005, "bar_px": 100.0,
             "fee_fraction_orig": fee_buy, "emp_px": None, "q_bid": None, "q_ask": None,
             "q_bid_shares": None, "q_ask_shares": None, "eligible_min_vol_shares": None,
             "status": "missing_quote", "px_basis": "unverifiable"},
            {"day": "1970-01-01", "policy": "ref", "clock": 560, "ticker": "TST", "rank": 1,
             "side": "sell", "decision_et": 560, "exec_et": 590, "qty0": 0.005, "bar_px": 99.5,
             "fee_fraction_orig": fee_sell, "emp_px": None, "q_bid": None, "q_ask": None,
             "q_bid_shares": None, "q_ask_shares": None, "eligible_min_vol_shares": None,
             "status": "missing_quote", "px_basis": "unverifiable"},
        ])
        ref = _reference_ledger(N, 0.005, 100.0, 100.0, 99.5, 99.5, 0.005)
        rep3, summ3 = replay("1970-01-01", ev_nq, 50.0, N, scope_statuses(ev_nq), "bar")
        row3 = summ3.filter((pl.col("clock") == 560) &
                            (pl.col("scenario") == "report_fee")).row(0, named=True)
        check(f"bar-basis N={N} basis", 1.0 if row3["basis"] == "bar_px_no_quote" else 0.0, 1.0)
        check(f"bar-basis N={N} status", 1.0 if row3["status"] == "provider_basis" else 0.0, 1.0)
        check(f"bar-basis N={N} cash", row3["cash_end"], ref["cash_end"], tol=1e-9 * N)
        check(f"bar-basis N={N} value", row3["value_end"], ref["value_end"], tol=1e-9 * N)
        check(f"bar-basis N={N} ev", row3["ev"], ref["ev"])
    for f in fails:
        print(f"[self-test FAIL] {f}")
    print(f"lifecycle_quote_audit self-test: {'OK' if not fails else str(len(fails)) + ' FAILURES'}")
    return 1 if fails else 0


def scope_statuses(ev: pl.DataFrame) -> dict[tuple[str, int], str]:
    """A scope (policy, clock) is 'supported' only if every one of its events is ok."""
    out: dict[tuple[str, int], str] = {}
    if ev.height == 0:
        return out
    for key, grp in ev.partition_by(["policy", "clock"], maintain_order=True, as_dict=True).items():
        pol, clk = (key if isinstance(key, tuple) else (key, None))
        bad = sorted(set(grp.filter(pl.col("status") != OK)["status"].to_list()))
        out[(str(pol), int(clk))] = "supported" if not bad else "unsupported:" + ",".join(bad)
    return out


def _run_scope(day: str, pol: str, clk: int, sel: pl.DataFrame, scenario: str,
               fees_measured_bps: float, notional: float, status_str: str,
               exec_basis: str = "quote") -> tuple[list[dict], dict]:
    """Chronological replay of one independent-capital scope.

    exec_basis "bar" is a PROVIDER-PRICE pure-cash replay: execution and marking use the reported
    bar price and no quote is required. It never claims empirical support (its status is
    `provider_basis`, its basis `bar_px_no_quote`) and exists to reproduce an external ledger.
    """
    supported = status_str.startswith("supported")
    bar_exec = exec_basis == "bar"
    ordered = sel.sort(["exec_et", "decision_et", "rank", "ticker", "side"])
    cash, emp_held, orig_held, n_limited = notional, {}, {}, 0
    erows: list[dict] = []
    for idx, r in enumerate(ordered.iter_rows(named=True)):
        fee0 = float(r["fee_fraction_orig"] or 0.0)   # ABSOLUTE fee per unit original capital
        px = r["emp_px"]
        tk = r["ticker"]
        q_in = float(r["qty0"]) if r["qty0"] is not None else None
        bar = float(r["bar_px"]) if r["bar_px"] is not None else None
        if bar_exec:
            px = bar
        h_before = emp_held.get(tk, 0.0)
        o_before = orig_held.get(tk, 0.0)
        desired_notional = budget = gross_cash = sell_fraction = None
        q_target = qty = px_used = fee_abs = None
        fee_basis = "absolute_per_capital"
        limited = False
        note = r["status"]
        if r["side"] == "buy":
            # `shares_per_capital` is a NORMALIZED q (shares per unit original capital), so the
            # target quantity is q*notional and the traded notional at the bar price is
            # T = notional*q*bar_px. `fee_fraction` is an ABSOLUTE fee per unit capital, NOT a
            # rate: the exact decision budget is  notional*(q*bar_px + fee_fraction) = T + fee_abs.
            if q_in is not None and bar is not None:
                q_target = q_in * notional
                desired_notional = q_target * bar
                if scenario == "report_fee":                      # ledger-exact
                    fee_abs = notional * fee0
                elif scenario == "measured":
                    fee_abs = desired_notional * (fees_measured_bps / 1e4)
                    fee_basis = "rate_on_traded_notional"
                elif scenario == "provider_plus_100":
                    fee_abs = notional * fee0 + desired_notional * 100.0 / 1e4
                    fee_basis = "report_absolute_plus_100bps_rate"
                else:
                    fee_abs = notional * fee0 + desired_notional * 150.0 / 1e4
                    fee_basis = "report_absolute_plus_150bps_rate"
                budget = desired_notional + fee_abs
            if px is not None and budget is not None:
                spend = min(budget, cash)
                limited = spend < budget - 1e-12
                if spend > 0:
                    scale = spend / budget                # shrink qty+fee together, never lever
                    qty = scale * desired_notional / px   # cash for shares = qty*ask
                    gross_cash = qty * px
                    fee_abs = scale * fee_abs
                    cash = max(0.0, cash - gross_cash - fee_abs)
                    emp_held[tk] = h_before + qty
                    orig_held[tk] = o_before + q_in
                    px_used = px
                else:
                    note = "no_cash"
            else:
                note = "no_quote" if px is None else "no_q_input"
        else:
            # sale size = q_original_sell / original_held BEFORE the event, applied to the
            # empirical currently-held quantity. gross_fraction is an ORIGINAL-CAPITAL fraction
            # and is never used as a sale fraction.
            if o_before <= 0 or q_in is None:
                note = "no_original_holding"
            else:
                sell_fraction = min(1.0, max(0.0, q_in / o_before))
                qty = h_before * sell_fraction
                if qty > 0 and px is not None:
                    tgt_q = q_in * notional
                    base = (bar if bar is not None else px) * qty
                    abs_fee = (notional * fee0 * (qty / tgt_q)) if tgt_q > 0 else 0.0
                    if scenario == "report_fee":                    # absolute fee, ledger-exact
                        fee_abs = abs_fee
                    elif scenario == "measured":
                        fee_abs = base * (fees_measured_bps / 1e4)
                        fee_basis = "rate_on_traded_notional"
                    elif scenario == "provider_plus_100":
                        fee_abs = abs_fee + base * 100.0 / 1e4
                        fee_basis = "report_absolute_plus_100bps_rate"
                    else:
                        fee_abs = abs_fee + base * 150.0 / 1e4
                        fee_basis = "report_absolute_plus_150bps_rate"
                    gross_cash = qty * px
                    cash = cash + gross_cash - fee_abs
                    emp_held[tk] = max(0.0, h_before - qty)
                    orig_held[tk] = max(0.0, o_before - q_in)
                    px_used = px
                else:
                    note = "no_quote" if px is None else ("no_held" if h_before <= 0 else note)
        n_limited += int(limited)
        disp = None
        if r["side"] == "buy" and qty is not None and r["q_ask_shares"]:
            disp = qty / float(r["q_ask_shares"])
        elif r["side"] == "sell" and qty is not None and r["q_bid_shares"]:
            disp = qty / float(r["q_bid_shares"])
        volp = None
        if qty is not None and r["eligible_min_vol_shares"]:
            volp = qty / float(r["eligible_min_vol_shares"])
        erows.append({
            "day": day, "policy": pol, "clock": clk, "scenario": scenario, "order_idx": idx,
            "ticker": tk, "side": r["side"], "scope_supported": supported,
            "q_in": q_in, "q_target": q_target, "desired_notional": desired_notional,
            "budget": budget,
            "gross_cash": gross_cash, "cash_before": cash + (gross_cash or 0.0) if r["side"] == "buy"
            else cash - (gross_cash or 0.0), "qty": qty, "px_used": px_used, "fee_abs": fee_abs,
            "fee_bps": ((fee_abs / ((gross_cash or 0.0))) * 1e4) if (fee_abs is not None
                                                                    and gross_cash) else None,
            "fee_basis": fee_basis,
            "cash_after": cash, "emp_held_after": emp_held.get(tk, 0.0),
            "orig_held_before": o_before, "orig_held_after": orig_held.get(tk, 0.0),
            "sell_fraction": sell_fraction, "cash_limited": limited,
            "displayed_shares": (float(r["q_ask_shares"]) if r["side"] == "buy"
                                 and r["q_ask_shares"] else
                                 (float(r["q_bid_shares"]) if r["q_bid_shares"] else None)),
            "displayed_ratio": disp, "over_displayed": bool(disp is not None and disp > 1.0),
            "vol_participation": volp, "px_basis": r["px_basis"], "note": note})
    last_bid = {}
    for rr in sel.sort(["exec_et"]).iter_rows(named=True):
        if rr["q_bid"] is not None:
            last_bid[rr["ticker"]] = float(rr["q_bid"])
        elif bar_exec and rr["bar_px"] is not None:
            last_bid[rr["ticker"]] = float(rr["bar_px"])   # provider mark, no quote used
    hold_usd = 0.0
    missing_mark = [t for t, q in emp_held.items() if q > 1e-12 and t not in last_bid]
    for t, q in emp_held.items():
        if q > 1e-12 and t in last_bid:
            hold_usd += q * last_bid[t]
    value = cash + hold_usd
    invalid = bool(missing_mark) or not (supported or bar_exec)
    dr = [e["displayed_ratio"] for e in erows if e["displayed_ratio"] is not None]
    vp = [e["vol_participation"] for e in erows if e["vol_participation"] is not None]
    smed, smax = _med_max(dr)
    vmed, vmax = _med_max(vp)
    summ = {"day": day, "policy": pol, "clock": clk, "scenario": scenario,
            "basis": "bar_px_no_quote" if bar_exec else "independent_scope",
            "status": (("provider_basis" if bar_exec else "ok") if not invalid
                       else ("no_mark" if missing_mark else status_str)),
            "n_scopes_used": 1, "n_scopes_total": 1,
            "n_buys": int((sel["side"] == "buy").sum()),
            "n_sells": int((sel["side"] == "sell").sum()),
            "initial_capital": notional, "cash_end": cash,
            "holdings_usd_end": hold_usd if not missing_mark else None,
            "value_end": value if not invalid else None,
            "ev": (value / notional - 1) if (notional > 0 and not invalid) else None,
            "n_cash_limited": n_limited, "orig_held_end": float(sum(orig_held.values())),
            "emp_held_end": float(sum(emp_held.values())),
            "displayed_ratio_med": smed, "displayed_ratio_max": smax,
            "n_over_displayed": sum(1 for e in erows if e["over_displayed"]),
            "vol_participation_med": vmed, "vol_participation_max": vmax,
            "n_px_proxy": sum(1 for e in erows if e.get("px_basis") == "mismatch")}
    return erows, summ


def replay(day: str, ev: pl.DataFrame, fees_measured_bps: float, notional: float,
           statuses: dict[tuple[str, int], str], exec_basis: str = "quote"
           ) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Independent (policy, clock) scopes; pooled rows are means over SUPPORTED scopes only."""
    erows: list[dict] = []
    by_scope: dict[str, dict] = {}
    scope_order: list[tuple[str, int]] = []
    if ev.height:
        for key, grp in ev.partition_by(["policy", "clock"], maintain_order=True,
                                        as_dict=True).items():
            pol, clk = (key if isinstance(key, tuple) else (key, None))
            pol, clk = str(pol), int(clk)
            status_str = statuses.get((pol, clk), "unsupported")
            scope_order.append((pol, clk))
            for scenario in SCENARIOS:
                rows, summ = _run_scope(day, pol, clk, grp, scenario, fees_measured_bps, notional,
                                        status_str, exec_basis)
                erows.extend(rows)
                by_scope[f"{pol}|{clk}|{scenario}"] = summ
    srows = [by_scope[k] for k in by_scope]
    # pooled per policy: independent-capital aggregate of the supported scopes (mean EV)
    for pol in sorted({p for p, _ in scope_order}):
        scopes = [c for p, c in scope_order if p == pol]
        for scenario in SCENARIOS:
            want = "provider_basis" if exec_basis == "bar" else "ok"
            sups = [by_scope[f"{pol}|{c}|{scenario}"] for c in scopes
                    if by_scope[f"{pol}|{c}|{scenario}"]["status"] == want]
            used = len(sups)
            cap = notional * used
            val = sum(s["value_end"] for s in sups) if sups else None
            srows.append({
                "day": day, "policy": pol, "clock": -1, "scenario": scenario,
                "basis": ("mean_of_independent_scopes_bar_px"
                          if exec_basis == "bar" else "mean_of_independent_scopes"),
                "status": ("provider_basis" if exec_basis == "bar" else "ok") if sups
                else "no_supported_scope", "n_scopes_used": used, "n_scopes_total": len(scopes),
                "n_buys": sum(s["n_buys"] for s in sups),
                "n_sells": sum(s["n_sells"] for s in sups),
                "initial_capital": cap, "cash_end": sum(s["cash_end"] for s in sups) if sups
                else None,
                "holdings_usd_end": sum(s["holdings_usd_end"] or 0.0 for s in sups) if sups
                else None,
                "value_end": val, "ev": (val / cap - 1) if (sups and cap > 0) else None,
                "n_cash_limited": sum(s["n_cash_limited"] for s in sups),
                "orig_held_end": sum(s["orig_held_end"] or 0.0 for s in sups) if sups else None,
                "emp_held_end": sum(s["emp_held_end"] or 0.0 for s in sups) if sups else None,
                "displayed_ratio_med": _med_max([s["displayed_ratio_med"] for s in sups
                                                 if s["displayed_ratio_med"] is not None])[0],
                "displayed_ratio_max": max([s["displayed_ratio_max"] for s in sups
                                            if s["displayed_ratio_max"] is not None], default=None),
                "n_over_displayed": sum(s["n_over_displayed"] for s in sups),
                "vol_participation_med": _med_max([s["vol_participation_med"] for s in sups
                                                   if s["vol_participation_med"] is not None])[0],
                "vol_participation_max": max([s["vol_participation_max"] for s in sups
                                              if s["vol_participation_max"] is not None],
                                             default=None),
                "n_px_proxy": sum(s["n_px_proxy"] for s in sups)})
    rep = pl.DataFrame(erows, infer_schema_length=None) if erows else _empty(REPLAY_DTYPES)
    summ = pl.DataFrame(srows, infer_schema_length=None) if srows else _empty(SUMMARY_DTYPES)
    return rep, summ


COVERAGE_DTYPES = {
    "day": pl.Utf8, "status": pl.Utf8, "n_events": pl.Int64, "n_ok": pl.Int64,
    "n_missing_quote": pl.Int64, "n_stale_quote": pl.Int64, "n_crossed_quote": pl.Int64,
    "n_no_exact_print": pl.Int64, "n_symbol_absent": pl.Int64, "n_px_mismatch": pl.Int64,
    "n_other": pl.Int64, "n_cross_feed_mismatch": pl.Int64, "n_local_source": pl.Int64,
    "n_fetched_source": pl.Int64, "fetch_used": pl.Boolean, "fetch_errors": pl.Int64,
    "fetch_truncated": pl.Boolean, "size_unit": pl.Int64, "eligible_vol_med": pl.Float64,
    "displayed_depth_usd_med": pl.Float64, "participation_med": pl.Float64,
    "participation_max": pl.Float64, "n_events_over_1pct_vol": pl.Int64, "n_policies": pl.Int64,
    "n_scopes": pl.Int64, "n_scopes_supported": pl.Int64, "unsupported_scopes": pl.Utf8,
}


def _med(s: pl.Series) -> float | None:
    v = s.median()
    return float(v) if v is not None else None


def _participation(sel: pl.DataFrame) -> tuple[float | None, float | None]:
    """Intended q / eligible fill-minute volume (shares) - capacity, never marketability."""
    v = sel.filter((pl.col("emp_px").is_not_null())
                   & (pl.col("eligible_min_vol_shares") > 0) & (pl.col("qty0") > 0))
    if not v.height:
        return None, None
    p = (v["qty0"] / v["eligible_min_vol_shares"]).to_list()
    return float(pl.Series(p).median()), float(max(p))


def coverage_row(day: str, ev: pl.DataFrame, unit: int, fetch_prov: list[dict],
                 statuses: dict[tuple[str, int], str]) -> pl.DataFrame:
    n = ev.height
    counts = {s: int((ev["status"] == s).sum()) for s in
              (OK, MISSING_QUOTE, STALE_QUOTE, CROSSED_QUOTE, NO_EXACT_PRINT, SYMBOL_ABSENT,
               PX_MISMATCH)}
    other = n - sum(counts.values())
    pmed, pmax = _participation(ev) if n else (None, None)
    over = 0
    if n:
        v = ev.filter((pl.col("emp_px").is_not_null())
                      & (pl.col("eligible_min_vol_shares") > 0) & (pl.col("qty0") > 0))
        over = int((v["qty0"] / v["eligible_min_vol_shares"] > 0.01).sum())
    bad = sorted(f"{p}@{c}:{s.split(':', 1)[1]}" for (p, c), s in statuses.items()
                 if not s.startswith("supported"))
    row = {
        "day": day,
        "status": "supported" if (statuses and not bad) else "unknown",
        "n_events": n, "n_ok": counts[OK],
        "n_missing_quote": counts[MISSING_QUOTE], "n_stale_quote": counts[STALE_QUOTE],
        "n_crossed_quote": counts[CROSSED_QUOTE], "n_no_exact_print": counts[NO_EXACT_PRINT],
        "n_symbol_absent": counts[SYMBOL_ABSENT], "n_px_mismatch": counts[PX_MISMATCH],
        "n_other": max(other, 0),
        "n_cross_feed_mismatch": int((ev["exec_basis"] == "cross_feed_mismatch").sum()) if n else 0,
        "n_local_source": int((ev["source"] == "local").sum()) if n else 0,
        "n_fetched_source": int((ev["source"] == "fetched").sum()) if n else 0,
        "fetch_used": bool(fetch_prov),
        "fetch_errors": sum(len(p.get("errors") or []) for p in fetch_prov),
        "fetch_truncated": any(bool(p.get("truncated")) for p in fetch_prov),
        "size_unit": unit,
        "eligible_vol_med": _med(ev["eligible_min_vol_shares"]) if n else None,
        "displayed_depth_usd_med": _med(ev["displayed_depth_usd"]) if n else None,
        "participation_med": pmed, "participation_max": pmax,
        "n_events_over_1pct_vol": over if n else None,
        "n_policies": ev["policy"].n_unique() if n else 0,
        "n_scopes": len(statuses), "n_scopes_supported": sum(
            1 for s in statuses.values() if s.startswith("supported")),
        "unsupported_scopes": ";".join(bad) if bad else "",
    }
    return pl.DataFrame([row]).with_columns(
        [pl.col(c).cast(t) for c, t in COVERAGE_DTYPES.items()])


# ------------------------------------------------------------------- day pipeline
def process_day(day: str, fills: pl.DataFrame, data_root: Path, split: dict, *,
                fetch_missing: bool = False, fees_bps: float = 1.0, force: bool = False,
                layers: tuple = LAYERS, fills_path: Path | None = None,
                env_file: Path | None = None, notional: float = 1.0,
                permit_px_mismatch: bool = False, exec_basis: str = "quote") -> dict:
    t0 = time.time()
    split_set = assert_day_allowed(day, split, data_root)
    day_fills = fills.filter(pl.col("day") == day)
    if day_fills.height == 0:
        return {"day": day, "status": "no_fills", "rows": 0}
    man_p = manifest_path(day, data_root)
    if not force and man_p.exists():
        try:
            man = json.loads(man_p.read_text())
            if (man.get("producer_version") == PRODUCER_VERSION
                    and man.get("schema_version") == SCHEMA_VERSION
                    and man.get("fees_bps") == fees_bps
                    and man.get("notional") == notional
                    and man.get("permit_px_mismatch") == bool(permit_px_mismatch)
                    and man.get("exec_basis") == exec_basis
                    and man.get("fetch_missing") == bool(fetch_missing)
                    and man.get("fills_sha256") == _sha256_file(fills_path)
                    and all(Path(man["layers"][L]["path"]).exists()
                            and Path(man["layers"][L]["path"]).stat().st_size
                            == man["layers"][L]["bytes"] for L in layers if L in man["layers"])):
                return {"day": day, "status": "skip", "rows": 0}
        except Exception:
            pass
    tickers = sorted(set(day_fills["ticker"].to_list()))
    tr, q, prov = read_local_day(day, tickers, data_root)
    trm, qm = _trade_lists(tr), _quote_lists(q)
    unit_meta = lt.detect_size_unit(q, day) if (q is not None and q.height) else {
        "unit": 100 if day < lt.SIZE_UNIT_CUTOFF else 1, "basis": "cutoff",
        "detected_unit": None, "frac_mult_100": None, "median_raw": None, "conflict": False}
    unit = unit_meta["unit"]
    need = sorted(t for t in tickers if t not in trm)
    fetched_syms: set[str] = set()
    fetch_prov: list[dict] = []
    if need and fetch_missing:
        refs = [int(r["exec_et"] if r["exec_et"] is not None else r["decision_et"])
                for r in day_fills.iter_rows(named=True)
                if r["ticker"] in need and (r["exec_et"] is not None
                                            or r["decision_et"] is not None)]
        if refs:
            lo, hi = max(0, min(refs) - WIN_BEFORE_MIN), min(1440, max(refs) + WIN_AFTER_MIN)
            recs, fp = fetch_window(day, need, lo, hi, layer_dir(data_root, "raw"), data_root,
                                    force, env_file)
            fetch_prov.append(fp)
            if recs.height:
                ftr, fq = records_to_lists(recs)
                fetched_syms = set(ftr) | set(fq)
                for t, v in ftr.items():
                    trm.setdefault(t, v)
                for t, v in fq.items():
                    qm.setdefault(t, v)
    ev = audit_events(day, day_fills, trm, qm, unit, fetched_syms, permit_px_mismatch)
    statuses = scope_statuses(ev)
    cov = coverage_row(day, ev, unit, fetch_prov, statuses)
    rep, summ = replay(day, ev, fees_bps, notional, statuses, exec_basis)
    written: dict[str, dict] = {}
    for layer, frame in (("evidence", ev), ("replay", rep), ("summary", summ), ("coverage", cov)):
        if layer not in layers:
            continue
        p = layer_dir(data_root, layer) / f"{day}.parquet"
        written[layer] = {"path": str(p), "rows": frame.height, "bytes": _atomic_parquet(frame, p)}
    man = {"day": day, "study": "LIFECYCLE-01", "producer": PRODUCER,
           "producer_version": PRODUCER_VERSION, "schema_version": SCHEMA_VERSION,
           "status": cov["status"][0], "split_set": split_set, "fees_bps": fees_bps,
           "notional": notional, "permit_px_mismatch": bool(permit_px_mismatch),
           "exec_basis": exec_basis,
           "scope_status": {f"{p}@{c}": s for (p, c), s in sorted(statuses.items())},
           "fetch_missing": bool(fetch_missing), "tickers": len(tickers),
           "matched_upstream": {"lifecycle_tape_version": lt.PRODUCER_VERSION,
                                "q_max_age_s": Q_MAX_AGE_S},
           "size_unit": unit_meta, "layers": written,
           "fills_path": str(fills_path) if fills_path else None,
           "fills_sha256": _sha256_file(fills_path),
           "sources": {"local": prov,
                       "fetched": [{"cache": p.get("cache"), "errors": p.get("errors"),
                                    "truncated": p.get("truncated"), "rows": p.get("rows"),
                                    "sha256": p.get("sha256"),
                                    "window_et": [p.get("lo_et"), p.get("hi_et")],
                                    "symbols": p.get("symbols")} for p in fetch_prov]},
           "coverage": cov.to_dicts()[0], "elapsed_s": round(time.time() - t0, 2)}
    _atomic_json(man, man_p)
    return {"day": day, "status": cov["status"][0], "rows": ev.height,
            "n_ok": cov["n_ok"][0], "n_events": cov["n_events"][0],
            "n_scopes": cov["n_scopes"][0], "n_scopes_supported": cov["n_scopes_supported"][0]}


def _worker(a):
    (day, fills_path, data_root, fetch_missing, fees_bps, force, layers, env_file, notional,
     permit_px, exec_basis) = a
    try:
        dr = Path(data_root)
        return process_day(day, load_fills(Path(fills_path)), dr, lt.load_split(dr),
                           fetch_missing=fetch_missing, fees_bps=fees_bps, force=force,
                           layers=tuple(layers), fills_path=Path(fills_path),
                           env_file=Path(env_file) if env_file else None, notional=notional,
                           permit_px_mismatch=permit_px, exec_basis=exec_basis)
    except Exception as e:  # noqa: BLE001 - surfaced, never swallowed
        return {"day": day, "status": f"error: {type(e).__name__}: {e}", "rows": 0}


# ------------------------------------------------------------------- public API
def read_manifest(day: str, data_root: str | Path | None = None) -> dict:
    dr = Path(data_root) if data_root else bps.resolve_data_root(None)
    p = manifest_path(day, dr)
    return json.loads(p.read_text()) if p.exists() else {}


def load_layer(day: str, layer: str = "evidence",
               data_root: str | Path | None = None) -> pl.DataFrame:
    if layer not in LAYERS:
        raise ValueError(f"layer must be one of {LAYERS}")
    dr = Path(data_root) if data_root else bps.resolve_data_root(None)
    p = layer_dir(dr, layer) / f"{day}.parquet"
    return pl.read_parquet(p) if p.exists() else pl.DataFrame()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", default=None,
                    help="fills parquet (default: newest <v2>/report/fills_*.parquet)")
    ap.add_argument("--data-root", default=None)
    ap.add_argument("--env-file", default=None,
                    help="dotenv file for API keys (default: <worktree>/.env, <data-root>/../.env,"
                         " ./.env)")
    ap.add_argument("--fetch-missing", action="store_true",
                    help="fetch narrow SIP windows for symbols absent from the local tape "
                         "(network; OFF by default)")
    ap.add_argument("--fees-bps", type=float, default=1.0,
                    help="measured fee per side in bps for the 'measured' scenario (config knob)")
    ap.add_argument("--notional", type=float, default=1.0,
                    help="ORIGINAL capital per (policy, clock) scope; 1.0 = normalized unit "
                         "capital (default). Each scope is funded independently.")
    ap.add_argument("--exec-basis", default="quote", choices=("quote", "bar"),
                    help="quote (default, empirical) or bar: PROVIDER-PRICE pure-cash replay that "
                         "bypasses quotes for ledger reproduction; never claims empirical support")
    ap.add_argument("--permit-px-mismatch", action="store_true",
                    help="treat cross_feed_mismatch prints as usable (px proxy sensitivity); "
                         "default: such an event is UNKNOWN and its scope unsupported")
    ap.add_argument("--days", nargs="+", default=[])
    ap.add_argument("--dev-days", action="store_true")
    ap.add_argument("--split", default="discovery", choices=("discovery", "validation", "all"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--layers", default=",".join(LAYERS))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--self-test", action="store_true",
                    help="fee-dimensionality + 100bp-ledger reproduction regression, then exit")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()
    data_root = bps.resolve_data_root(args.data_root)
    split = lt.load_split(data_root)
    layers = tuple(x for x in args.layers.split(",") if x)
    bad = [x for x in layers if x not in LAYERS]
    if bad:
        raise SystemExit(f"unknown layer(s) {bad}; known {LAYERS}")
    fills_path = Path(args.input) if args.input else default_fills_path(data_root)
    if fills_path is None or not fills_path.exists():
        raise SystemExit("no fills report: pass --input (looked for "
                         f"{lt.v2_dir(data_root) / 'report'}/fills_*.parquet)")
    fills = load_fills(fills_path)
    have = set(fills["day"].unique().to_list())
    days = [d for tok in args.days for d in tok.split(",") if d]
    if days:
        days = [d for d in days if d in have]
    else:
        want = "discovery" if args.dev_days else args.split
        pool = {"discovery": split.get("discovery_days") or [],
                "validation": split.get("validation_days") or [],
                "all": sorted((split.get("discovery_days") or [])
                              + (split.get("validation_days") or []))}[want]
        days = [d for d in pool if d in have]
    if args.limit:
        days = days[:args.limit]
    if not days:
        print("no days to audit (fills report empty or filtered out)")
        return 0
    t0 = time.time()
    built = skipped = 0
    tasks = [(d, str(fills_path), str(data_root), args.fetch_missing, args.fees_bps,
              args.force, layers, str(args.env_file) if args.env_file else None, args.notional,
              args.permit_px_mismatch, args.exec_basis) for d in days]
    if args.workers <= 1:
        for i, a in enumerate(tasks, 1):
            res = _worker(a)
            skipped += res["status"] == "skip"
            built += res["status"] != "skip"
            print(f"  {i}/{len(days)} {res['day']} {res['status']} events={res.get('n_events')} "
                  f"ok={res.get('n_ok')} scopes={res.get('n_scopes_supported')}/"
                  f"{res.get('n_scopes')}", flush=True)
    else:
        import concurrent.futures as cf
        with cf.ProcessPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(_worker, a) for a in tasks]
            for i, fut in enumerate(cf.as_completed(futs), 1):
                res = fut.result()
                skipped += res["status"] == "skip"
                built += res["status"] != "skip"
                print(f"  {i}/{len(days)} {res['day']} {res['status']} events={res.get('n_events')} "
                      f"ok={res.get('n_ok')} scopes={res.get('n_scopes_supported')}/"
                      f"{res.get('n_scopes')}", flush=True)
    print(f"lifecycle_quote_audit {PRODUCER_VERSION}: days={len(days)} built={built} "
          f"reused={skipped} el={time.time() - t0:.0f}s polars_threads={pl.thread_pool_size()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
