"""Small shared tick/NBBO substrate for active micro-entry hypotheses.

The loader takes an EXPLICIT causal watch - a symbol -> first-admission-minute
map - or, by default, declares the legacy full-PIT B top-three admissions the
historical micro studies pinned. A watch is a genuine policy parameter, never a
filter applied to the streams that happened to survive: every causal loading
rule below (eligible-print classification, R-only/uncrossed quote validity,
past-only coverage counting, UNKNOWN reporting) is shared unchanged, and the
coverage counters describe the REQUESTED watch.

Every one-second feature uses STRICTLY earlier prints and quotes,
including the displayed depth columns (bid_shares/depth_imbalance) read at the
strictly-prior NBBO. Regular price-updating prints reuse the repo's SIP condition
policy. UNKNOWN routing/exit/depth remains a full-loss lower bound, not an assumed
free no-fill.

Quote conditions are kept verbatim: a raw '?' flag means the historical firm status
is UNKNOWN, so it is reported as coverage (quote_condition_unknown_symbols) and is
never reclassified as a known non-regular quote nor as market-closed cash. Invalid
quotes stay in the stream and are never replaced by an older favourable quote.

Every caller states the exit horizon explicitly: execution/evaluate take a required
horizon_seconds, emit gross_{h}/exit_et_{h} keyed on that horizon, and carry
microsecond timestamps. No default horizon, no legacy alias.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl
from alpha_open_panel import SNAPSHOTS, allowed
from alpha_open_sim import replay
from alpha_quote_audit import clock_us
from sip_bars import AUCTION_CODES, combine

MONTHS = {
    "development": ("2021-02", "2021-03", "2021-04", "2021-05", "2021-06", "2021-07"),
    "validation": ("2023-01", "2023-02"),
    "confirmation": ("2025-03", "2025-04", "2025-05", "2025-06", "2025-07", "2025-08"),
}
ORDER_BUDGET = 250.0
HORIZON_SECONDS = 60
LATENCY_US = 250_000
# Epoch of the derived quote-condition coverage rule. Bump whenever the
# classification below changes; cached day stages pin this value so a stale cache
# is refreshed (or rebuilt) instead of silently reusing superseded coverage.
COVERAGE_EPOCH = 2

# Coverage kinds whose day portfolio lower bound is the full loss, never cash.
COVERAGE_UNKNOWN_KINDS = (
    "missing_day_file",
    "missing_symbol_streams",
    "quote_condition_unknown",
    "no_regular_quotes",
    "day_error",
)


def coverage_kind(
    watch_names: int,
    missing_symbol_streams: list[str],
    missing_day_file: bool,
    no_regular_quote_symbols: int,
    quote_condition_unknown_symbols: int,
) -> str:
    """Classify one day's data coverage; unknown kinds are never booked as cash."""
    if missing_day_file:
        return "missing_day_file"
    if not watch_names:
        # No admissible name that date: a real no-watch day, legitimately cash.
        return "no_watch_names"
    if missing_symbol_streams:
        return "missing_symbol_streams"
    if quote_condition_unknown_symbols:
        # Raw '?' quote flags: the historical firm status is unknown.
        return "quote_condition_unknown"
    if no_regular_quote_symbols:
        # Quote status is known and asserts no regular quote exists; still not
        # an automatic market-closed cash day.
        return "no_regular_quotes"
    return "complete"


def coverage_is_complete(kind: str) -> bool:
    return kind in ("no_watch_names", "complete")


def coverage_unknown_reason(cov: dict) -> str | None:
    """Specific unknown reason from derived coverage metadata (or None if covered)."""
    if cov.get("missing_day_file"):
        return None if not cov.get("watch_names") else "missing_day_file"
    if not cov.get("watch_names"):
        return None
    if cov.get("missing_symbol_streams"):
        return "missing_symbol_streams"
    if cov.get("quote_condition_unknown_symbols"):
        return "quote_condition_unknown"
    if cov.get("no_regular_quote_symbols"):
        return "no_regular_quotes"
    return None


def quote_condition_flags(quotes: pl.DataFrame) -> dict:
    """Counts from a watch-filtered quote frame: symbols with no regular ('R')
    quote and symbols carrying a raw '?' unknown condition flag. Quote rows are
    kept verbatim; an invalid/'?' quote is never upgraded to an older regular one.
    """
    empty = {"no_regular_quote_symbols": 0, "quote_condition_unknown_symbols": 0}
    if not quotes.height:
        return empty
    flagged = quotes.filter(pl.col("conditions").list.contains("?"))
    unknown_symbols = set(flagged["symbol"])
    firm = quotes.filter(pl.col("regular"))
    firm_symbols = set(firm["symbol"])
    present = set(quotes["symbol"])
    return {
        "no_regular_quote_symbols": len(present - firm_symbols),
        "quote_condition_unknown_symbols": len(unknown_symbols & present),
    }


def quote_condition_coverage(data: Path, day: str, watch: dict) -> dict:
    """Cheap conditions-only projection of a day's quote coverage (no tick build).

    Cached day stages are upgradeable with this projection so coverage metadata
    never has to be faked or derived from a rebuilt tick pipeline.
    """
    qpath = data / "sip" / "net" / "quotes" / f"{day}.parquet"
    out = {
        "no_regular_quote_symbols": 0,
        "quote_condition_unknown_symbols": 0,
        "quote_events": 0,
        "firm_quote_events": 0,
        "names_with_firm_quotes": [],
        "missing_quote_symbols": sorted(watch) if watch else [],
    }
    if not qpath.exists():
        return {**out, "missing_day_file": True}
    frame = (
        pl.scan_parquet(qpath)
        .select("symbol", "conditions")
        .filter(pl.col("symbol").is_in(list(watch)))
    )
    frame = frame.with_columns(
        (
            (pl.col("conditions").list.len() > 0)
            & ~pl.col("conditions").list.eval(pl.element() != "R").list.any()
        ).alias("regular"),
        pl.col("conditions").list.contains("?").alias("unknown_condition"),
    )
    frame = frame.collect() if isinstance(frame, pl.LazyFrame) else frame
    if not frame.height:
        return out
    per_symbol = frame.group_by("symbol").agg(
        [
            pl.col("regular").any().alias("firm"),
            pl.col("unknown_condition").any().alias("unknown"),
            pl.len().alias("events"),
        ]
    )
    firm_symbols = {
        s for s, firm in zip(per_symbol["symbol"], per_symbol["firm"], strict=True) if firm
    }
    unknown_symbols = {
        s for s, u in zip(per_symbol["symbol"], per_symbol["unknown"], strict=True) if u
    }
    present = set(watch) & set(per_symbol["symbol"].to_list())
    out.update(
        {
            "no_regular_quote_symbols": len(present - firm_symbols),
            "quote_condition_unknown_symbols": len(unknown_symbols & present),
            "quote_events": int(frame.height),
            "firm_quote_events": int(
                per_symbol.filter(pl.col("firm"))["events"].sum() if per_symbol.height else 0
            ),
            "names_with_firm_quotes": sorted(firm_symbols),
            "missing_quote_symbols": sorted(set(watch) - present),
        }
    )
    return out


def selected_days(data: Path) -> dict[str, list[str]]:
    files = sorted(
        p.stem for p in (data / "sip" / "candidates").glob("????-??-??.json") if allowed(p.stem)
    )
    return {name: [d for d in files if d[:7] in months] for name, months in MONTHS.items()}


def admissions(data: Path, day: str) -> dict[str, int]:
    """Legacy default watch: full-PIT B snapshot top-3, score>=0.05, px>=5, fresh.

    This is the policy the historical micro studies (flow / reclaim / payoff /
    bid-backed-burst) are pinned to, and it stays the ``load_day`` default so
    their published outcomes are reproducible. A different universe is a caller
    policy: build the watch explicitly and pass it to ``load_day``.
    """
    obj = json.loads((data / "sip" / "candidates" / f"{day}.json").read_text())
    out = {}
    for snap in obj["snapshots"]:
        t = snap["T"]
        if snap["pop"] != "B" or t not in SNAPSHOTS:
            continue
        for r in snap["top"][:3]:
            if r["score"] >= 0.05 and r.get(f"px_{t}", 0) >= 5 and r.get(f"px_{t}_et", -1) >= t - 2:
                out.setdefault(r["symbol"], t)
    return out


def load_day(data: Path, day: str, watch: dict[str, int] | None = None):
    """One day's eligible trade/quote streams for an EXPLICIT causal watch.

    ``watch`` maps every requested symbol to the FIRST completed snapshot minute
    that already qualified it; that minute starts the one-second state grid. It
    is a genuine policy parameter, not a shim: a caller declaring a wider
    universe (e.g. the full top-ten, $1-floor watch) reuses this loader once and
    inherits every causal rule in it. ``None`` keeps the legacy
    ``admissions`` top-three policy.

    Returns ``(streams, coverage)``. ``coverage`` describes the REQUESTED watch:
    ``watch_names`` counts every declared candidate, ``covered_names`` the ones
    whose raw streams loaded, and ``missing_symbol_streams`` the requested names
    with no trade/quote stream (data-UNKNOWN, never known cash). The counters are
    never taken from the streams-that-survived intersection.
    """
    if not allowed(day):
        raise ValueError("protected tick read refused")
    if watch is None:
        watch = admissions(data, day)
    tpath = data / "sip" / "net" / "trades" / f"{day}.parquet"
    qpath = data / "sip" / "net" / "quotes" / f"{day}.parquet"
    if not tpath.exists() or not qpath.exists():
        return {}, {
            "watch_names": len(watch),
            "missing_day_file": True,
            "no_regular_quote_symbols": 0,
            "quote_condition_unknown_symbols": 0,
        }
    trades = pl.read_parquet(tpath).filter(pl.col("symbol").is_in(list(watch)))
    quotes = pl.read_parquet(qpath).filter(pl.col("symbol").is_in(list(watch)))
    quotes = quotes.with_columns(
        (
            (pl.col("conditions").list.len() > 0)
            & ~pl.col("conditions").list.eval(pl.element() != "R").list.any()
        ).alias("regular")
    )
    # Past-only quote-condition coverage: '?' is an unknown historical firm status,
    # distinct from a known non-regular status; regular absence is never cash.
    qflags = quote_condition_flags(quotes)
    # Map unique condition/tape combinations, not Python objects per raw print.
    trades = trades.with_columns(pl.col("conditions").list.join("|").alias("ck"))
    rules = []
    for ck, tape in trades.select("ck", "tape").unique().iter_rows():
        conds = ck.split("|") if ck else []
        oc, hl, volume, unknown = combine(conds, tape)
        eligible = oc == 2 and hl == 2 and volume == 2 and not unknown
        eligible = eligible and not any(c in AUCTION_CODES for c in conds)
        rules.append({"ck": ck, "tape": tape, "eligible": eligible})
    if not rules:
        return {}, {"watch_names": len(watch), "no_eligible_trades": True, **qflags}
    trades = (
        trades.join(pl.DataFrame(rules), on=["ck", "tape"], how="left")
        .filter(pl.col("eligible"))
        .sort("symbol", "ts_utc")
    )
    tt = trades.partition_by("symbol", as_dict=True)
    qq = quotes.sort("symbol", "ts_utc").partition_by("symbol", as_dict=True)
    result, missing = {}, []
    for ticker, admit_t in watch.items():
        tf, qf = tt.get((ticker,)), qq.get((ticker,))
        if tf is None or qf is None:
            missing.append(ticker)
            continue
        multiplier = 100 if day < "2025-11-03" else 1
        t = {
            "ts": tf["ts_utc"].cast(pl.Int64).to_numpy(),
            "px": tf["price"].to_numpy(),
            "size": tf["size"].to_numpy(),
        }
        q = {
            "ts": qf["ts_utc"].cast(pl.Int64).to_numpy(),
            "bid": qf["bid_price"].to_numpy(),
            "ask": qf["ask_price"].to_numpy(),
            "bs": qf["bid_size"].to_numpy() * multiplier,
            "az": qf["ask_size"].to_numpy() * multiplier,
            "regular": qf["regular"].to_numpy(),
        }
        q["valid"] = (
            q["regular"]
            & np.isfinite(q["bid"])
            & np.isfinite(q["ask"])
            & (q["bid"] > 0)
            & (q["ask"] >= q["bid"])
        )
        if not np.all(np.isfinite(t["px"])) or np.any(t["px"] <= 0):
            raise ValueError(f"invalid price stream: {day} {ticker}")
        result[ticker] = {"trades": t, "quotes": q, "admit_t": admit_t}
    return result, {
        "watch_names": len(watch),
        "covered_names": len(result),
        "missing_symbol_streams": missing,
        "regular_price_prints": trades.height,
        "quote_events": quotes.height,
        **qflags,
    }


def qindex(q: dict, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Strictly prior quote; equal-microsecond ordering is deliberately not inferred."""
    idx = np.searchsorted(q["ts"], target, side="left") - 1
    safe = np.maximum(idx, 0)
    valid = (idx >= 0) & q["valid"][safe] & ((target - q["ts"][safe]) <= 2_000_000)
    return safe, valid


def states(day: str, ticker: str, stream: dict, session_end: int) -> pl.DataFrame:
    t, q = stream["trades"], stream["quotes"]
    start = clock_us(day, stream["admit_t"], 0)
    end = clock_us(day, min(780, session_end - 1), 0)
    grid = np.arange(start, end + 1, 1_000_000, dtype=np.int64)
    if not len(grid) or not len(t["ts"]) or not len(q["ts"]):
        return pl.DataFrame()
    ti = np.searchsorted(t["ts"], grid, side="left") - 1
    safe = np.maximum(ti, 0)
    qi, qvalid = qindex(q, grid)
    fresh = (ti >= 0) & ((grid - t["ts"][safe]) <= 2_000_000)
    px = t["px"][safe]
    # Aggressor signs use the last quote STRICTLY before each print.
    tq, tqvalid = qindex(q, t["ts"])
    tqvalid &= q["ask"][tq] > q["bid"][tq]  # locked quotes cannot identify aggressor side
    dollars = t["px"] * t["size"]
    buy = tqvalid & (t["px"] >= q["ask"][tq])
    sell = tqvalid & (t["px"] <= q["bid"][tq]) & ~buy
    cumulative = {
        "dv": np.r_[0.0, np.cumsum(dollars)],
        "buy": np.r_[0.0, np.cumsum(dollars * buy)],
        "sell": np.r_[0.0, np.cumsum(dollars * sell)],
    }
    features = {}
    right = np.searchsorted(t["ts"], grid, side="left")
    for seconds in (5, 30, 60):
        left = np.searchsorted(t["ts"], grid - seconds * 1_000_000, side="left")
        old = np.searchsorted(t["ts"], grid - seconds * 1_000_000, side="right") - 1
        features[f"ret{seconds}s"] = px / t["px"][np.maximum(old, 0)] - 1
        bd = cumulative["buy"][right] - cumulative["buy"][left]
        sd = cumulative["sell"][right] - cumulative["sell"][left]
        features[f"imbalance{seconds}s"] = (bd - sd) / np.maximum(bd + sd, 1)
        features[f"classified_dv{seconds}s"] = bd + sd
        features[f"dv{seconds}s"] = cumulative["dv"][right] - cumulative["dv"][left]
        features[f"n{seconds}s"] = right - left
    high120 = pl.Series(px).rolling_max(window_size=120, min_samples=30).to_numpy()
    low30 = pl.Series(px).rolling_min(window_size=30, min_samples=10).to_numpy()
    # Displayed depth is read at the strictly-prior NBBO index (qi): past-only,
    # even for a quote that is not fresh enough to gate on.
    depth_bid, depth_ask = q["bs"][qi], q["az"][qi]
    denom = depth_bid + depth_ask
    safe_denom = np.where(denom > 0, denom, 1.0)
    return pl.DataFrame(
        {
            "day": [day] * len(grid),
            "ticker": [ticker] * len(grid),
            "signal_us": grid,
            "px": px,
            "ask": q["ask"][qi],
            "bid": q["bid"][qi],
            "ask_shares": q["az"][qi],
            "bid_shares": depth_bid,
            "depth_imbalance": np.where(denom > 0, (depth_bid - depth_ask) / safe_denom, 0.0),
            "spread": q["ask"][qi] / np.maximum(q["bid"][qi], 0.0001) - 1,
            "fresh": fresh & qvalid,
            "dd120s": px / high120 - 1,
            "rebound30s": px / low30 - 1,
            **features,
        }
    )


def execution(
    day: str, ticker: str, signal_us: int, stream: dict, session_end: int, horizon_seconds: int
) -> dict:
    """One order's as-of touch: entry ASK at signal+250ms, BID exit at entry+horizon.

    horizon_seconds is required; the emitted keys are gross_{h} / exit_et_{h}
    (e.g. gross_60, exit_et_60) so the shared replay can select either horizon from
    the same intent record. All timestamps are microseconds.
    """
    q = stream["quotes"]
    entry_us = signal_us + LATENCY_US
    exit_us = min(entry_us + horizon_seconds * 1_000_000, clock_us(day, session_end, 0))
    ei, ev = qindex(q, np.array([entry_us], dtype=np.int64))
    xi, xv = qindex(q, np.array([exit_us], dtype=np.int64))
    i, j = int(ei[0]), int(xi[0])
    exit_target_us = exit_us
    if not xv[0]:
        # The resting market-exit intent waits through a halt/no-fresh-quote interval.
        # First subsequent regular quote only; never choose a favorable price/depth.
        first = int(np.searchsorted(q["ts"], exit_us, side="left"))
        end_us = clock_us(day, session_end, 0)
        candidates = np.flatnonzero(q["valid"][first:] & (q["ts"][first:] <= end_us))
        if len(candidates):
            j = first + int(candidates[0])
            exit_us = int(q["ts"][j])
            xv[0] = True
    entry, exit_px = float(q["ask"][i]), float(q["bid"][j])
    quantity = ORDER_BUDGET / entry if entry > 0 else np.inf
    supported = bool(ev[0] and xv[0] and q["az"][i] >= quantity and q["bs"][j] >= quantity)
    return {
        "day": day,
        "ticker": ticker,
        "t": signal_us,
        "entry_et": entry_us,
        f"exit_et_{horizon_seconds}": exit_us if supported else None,
        "entry_open": entry,
        "entry_status": "filled_proxy",
        f"gross_{horizon_seconds}": exit_px / entry - 1 if supported else None,
        "session_end": clock_us(day, session_end, 0),
        "score": 0.0,
        "quote_supported": supported,
        "status": "touch_capacity_supported_not_fill_guaranteed"
        if supported
        else "unknown_quote_or_capacity",
        "signal_us": signal_us,
        "exit_target_us": exit_target_us,
        "exit_us": exit_us,
        "exit_bid": exit_px if supported else None,
        "horizon_seconds": horizon_seconds,
    }


def evaluate(signals: list[dict], dates: list[str], extra_cost_bps: float, horizon_seconds: int):
    """Shared account replay for one declared horizon (required, no default)."""
    f = (
        pl.DataFrame(signals)
        if signals
        else pl.DataFrame(
            schema={"day": pl.String, "ticker": pl.String, "t": pl.Int64, "score": pl.Float64}
        )
    )
    return replay(f, dates, horizon_seconds, extra_cost_bps, order_budget=ORDER_BUDGET)
