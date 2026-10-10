#!/usr/bin/env python3
"""Shared day-resident NBBO quote service for the sparse-model execution frontier.

Small, boring, dependency-free (polars/numpy only) contract consumed by
``alpha_sparse_execution_frontier.py`` (ExecutionFrontier) and
``alpha_sparse_exit_management.py`` (ExitManagement):

* ``load_day_quotes(data_root, day, tickers, supplemental_root)`` -> one day's raw
  quote rows, sorted by symbol/ts_utc. At most one day's streams are resident;
  the corpus is never concatenated. Supplemental (fetched) rows are merged and
  deduped without ever overwriting the original ranked evidence.
* ``quote_at(frame, target_us, max_age_s)`` -> (quote dict | None, status). Exact
  reuse of ``alpha_quote_audit.asof_quote``: strictly prior print, R-only
  conditions, no future-quote selection, no fallback to an older regular print.
* ``supported_round_trip(entry_quote, exit_quote, budget_usd, residual_bps)`` ->
  integer fee-funded round trip with side-aware prices/ages and explicit L1
  support. Top-of-book depth insufficiency is an UNKNOWN at that notional; the
  deeper book and other venues are unmeasured, so it is never reported as
  "cannot fill". A budget that cannot fund even ONE integer share at the quoted
  fee-adjusted ASK is a KNOWN no-order (``NO_ORDER_STATUS``): no order is sent,
  the cash stays unfilled, and it is never an executed fill, never a priced
  zero return and never an UNKNOWN capacity outcome.

Quotes measure the touch cost of a hypothetical market order. They are not
exchange fills and never a fill guarantee.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
from alpha_quote_audit import asof_quote

ET = ZoneInfo("America/New_York")

# Raw cache layout, identical to the SIP ingest artifacts (never written here).
QUOTE_COLS = (
    "symbol",
    "ts_utc",
    "bid_price",
    "bid_size",
    "ask_price",
    "ask_size",
    "bid_exchange",
    "ask_exchange",
    "conditions",
    "tape",
)
QUOTE_SCHEMA = {
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
# Alpaca quote sizes are round lots before this date, single shares after.
UNIT_EPOCH = "2025-11-03"

SUPPORTED_STATUS = "quoted_capacity_supported_not_fill_guaranteed"
UNKNOWN_CAPACITY_STATUS = "unknown_l1_capacity"
# Budget below one integer share at the quoted fee-adjusted ASK: no order is
# sent, so the cash stays unfilled. This is a KNOWN outcome (never an UNKNOWN,
# never a zero-return fill); fractional fills are not invented.
NO_ORDER_STATUS = "known_no_order_min_capital"


def day_quote_path(data_root: Path, day: str) -> Path:
    """Ranked per-day SIP quote cache (read-only for this service)."""
    return data_root / "sip" / "net" / "quotes" / f"{day}.parquet"


def supplement_day_path(supplemental_root: Path, day: str) -> Path:
    """Owning-module fetch cache: ``<supplemental_root>/<day>.parquet``."""
    return supplemental_root / f"{day}.parquet"


def _read_day(path: Path) -> pl.DataFrame:
    df = pl.read_parquet(path)
    missing = [c for c in QUOTE_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"quote frame {path} missing raw columns {missing}")
    return df.select(list(QUOTE_COLS))


def _dedup_keep_original(frames: list[pl.DataFrame]) -> pl.DataFrame:
    """Original rows first, supplement-only rows appended, one row per print."""
    merged = pl.concat([f for f in frames if f.height], how="vertical")
    if merged.is_empty():
        return pl.DataFrame(schema=QUOTE_SCHEMA)
    return merged.unique(subset=["symbol", "ts_utc"], keep="first").sort("symbol", "ts_utc")


def load_day_quotes(
    data_root: Path, day: str, tickers: set[str], supplemental_root: Path | None = None
) -> pl.DataFrame:
    """Raw quote rows for one ET day, filtered to ``tickers`` and sorted.

    ``data_root`` is the panel data root (``.../data``); ``supplemental_root`` is a
    fetch-cache directory of same-schema ``<day>.parquet`` files. Rows that exist
    in both are taken from the original file; only supplement-only prints are
    added. Returns an empty typed frame when neither source has the day.
    """
    primary, supplement = None, None
    p = day_quote_path(data_root, day)
    if p.exists():
        primary = _read_day(p)
    if supplemental_root is not None:
        sp = supplement_day_path(supplemental_root, day)
        if sp.exists():
            supplement = _read_day(sp)
    if primary is None and supplement is None:
        return pl.DataFrame(schema=QUOTE_SCHEMA)
    frame = _dedup_keep_original([f for f in (primary, supplement) if f is not None])
    if tickers:
        frame = frame.filter(pl.col("symbol").is_in(sorted(tickers)))
    return frame


def _frame_day(frame: pl.DataFrame) -> str:
    """ET calendar day of the frame's prints (drives the round-lot unit epoch)."""
    last = frame["ts_utc"].max()
    if last is None:
        raise ValueError("cannot derive unit epoch from an empty quote frame")
    return (
        datetime.fromisoformat(str(last).replace("Z", "+00:00")).astimezone(ET).date().isoformat()
    )


def quote_at(
    frame: pl.DataFrame, target_us: int, max_age_s: float = 2.0
) -> tuple[dict | None, str]:
    """Strictly prior as-of R-only quote for one symbol's sorted frame.

    Pass a single-symbol frame (``load_day_quotes(...).partition_by("symbol")``);
    multi-symbol frames cannot be searchsorted. Returns the same dict keys and
    status strings as ``alpha_quote_audit.asof_quote``; pre-2025-11-03 sizes are
    already multiplied to shares.
    """
    if frame.is_empty():
        return None, "quote_not_acquired"
    return asof_quote(frame, target_us, _frame_day(frame), max_age_s)


def regular_mask(frame: pl.DataFrame) -> pl.DataFrame:
    """Same frame plus a boolean ``regular`` column (conditions non-empty, all R).

    Shared R-only rule so consumers never re-derive quote firmness.
    """
    regular = (pl.col("conditions").list.len() > 0) & ~pl.col("conditions").list.eval(
        pl.element() != "R"
    ).list.any()
    return frame.with_columns(regular.alias("regular"))


def _as_quote_dict(row: dict, day: str) -> dict:
    multiplier = 100 if day < UNIT_EPOCH else 1
    bid, ask = row["bid_price"], row["ask_price"]
    return {
        "bid": bid,
        "ask": ask,
        "bid_shares": row["bid_size"] * multiplier,
        "ask_shares": row["ask_size"] * multiplier,
        "spread_bps": (ask / bid - 1) * 10_000 if bid else None,
    }


def first_regular_at_or_after(frame: pl.DataFrame, target_us: int) -> tuple[dict | None, str]:
    """First R-only quote at/after ``target_us`` (never an earlier one).

    A forward scan for policies that must observe the first firm market after an
    event; it is not an exit-pricing helper (pricing stays ``quote_at``).
    """
    if frame.is_empty():
        return None, "no_regular_quote_at_or_after"
    day = _frame_day(frame)
    stamps = frame["ts_utc"].cast(pl.Int64).to_numpy()
    reg = regular_mask(frame)["regular"].to_numpy()
    i = int((stamps >= target_us).argmax()) if (stamps >= target_us).any() else len(stamps)
    while i < len(stamps):
        if reg[i]:
            q = _as_quote_dict(frame.row(i, named=True), day)
            q["quote_us"] = int(stamps[i])
            q["delay_s"] = (stamps[i] - target_us) / 1_000_000
            return q, "regular_quote"
        i += 1
    return None, "no_regular_quote_at_or_after"


def supported_round_trip(
    entry_quote: dict, exit_quote: dict, budget_usd: float, residual_bps: float
) -> dict:
    """Fee-funded integer round trip: buy ASK / sell BID at the quoted touch.

    ``residual_bps`` is the round-trip residual-cost ladder entry, charged per
    side (``residual_bps / 2`` per leg in price terms). The budget is
    fee-funded: the quantity itself is floored so ``quantity * ask * (1 + side)``
    never exceeds ``budget_usd``. L1 support is measured on displayed depth only;
    other venues and the deeper book are unmeasured, so a shortfall is UNKNOWN at
    this notional, not a verdict on fillability.

    A budget that cannot fund even one integer share (``quantity < 1``) sends NO
    order: the outcome is a KNOWN no-order (``NO_ORDER_STATUS``,
    ``unfilled_known_cash=True``) with no priced return, never a zero-return fill
    and never an UNKNOWN. Quantities stay integer; broker fractional availability
    is unverified, so no fractional fallback is invented.
    """
    for name, q in (("entry_quote", entry_quote), ("exit_quote", exit_quote)):
        if not q or not (q.get("ask") or q.get("bid")):
            raise ValueError(f"{name} must be a priced as-of quote dict")
    if budget_usd <= 0:
        raise ValueError("budget_usd must be positive")
    entry_ask = float(entry_quote["ask"])
    exit_bid = float(exit_quote["bid"])
    if entry_ask <= 0 or exit_bid <= 0:
        raise ValueError("as-of quotes must be positive and firm")
    side = float(residual_bps) / 20_000.0
    entry_price = entry_ask * (1 + side)
    exit_price = exit_bid * (1 - side)
    quantity = int(budget_usd // entry_price)
    entry_l1 = entry_quote.get("ask_shares")
    exit_l1 = exit_quote.get("bid_shares")
    if quantity < 1:
        # Min-capital no-order: zero shares. No order is sent, no money is spent
        # and no proceeds exist, so there is no priced return to report; the
        # cash simply stays unfilled. Never an UNKNOWN, never a measured 0 fill.
        return {
            "quantity": 0,
            "exit_quantity": 0,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "entry_cost_usd": 0.0,
            "exit_proceeds_usd": 0.0,
            "net_usd": None,
            "net": None,
            "entry_l1_shares": entry_l1,
            "exit_l1_shares": exit_l1,
            "entry_supported": False,
            "exit_supported": False,
            "supported": False,
            "status": NO_ORDER_STATUS,
            "unknown": None,
            "unfilled_known_cash": True,
            "budget_usd": budget_usd,
            "residual_bps": residual_bps,
            "entry_age_s": entry_quote.get("age_s"),
            "exit_age_s": exit_quote.get("age_s"),
        }
    entry_supported = entry_l1 is not None and quantity <= entry_l1
    exit_supported = exit_l1 is not None and quantity <= exit_l1
    supported = bool(entry_supported and exit_supported)
    entry_cost_usd = quantity * entry_price
    exit_proceeds_usd = quantity * exit_price
    net_usd = exit_proceeds_usd - entry_cost_usd
    if supported:
        unknown = None
    else:
        unknown = "unknown_l1_capacity_" + "_".join(
            leg for leg, ok in (("entry", entry_supported), ("exit", exit_supported)) if not ok
        )
    return {
        "quantity": quantity,
        "exit_quantity": quantity,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "entry_cost_usd": entry_cost_usd,
        "exit_proceeds_usd": exit_proceeds_usd,
        "net_usd": net_usd,
        "net": net_usd / budget_usd,
        "entry_l1_shares": entry_l1,
        "exit_l1_shares": exit_l1,
        "entry_supported": bool(entry_supported),
        "exit_supported": bool(exit_supported),
        "supported": supported,
        "status": SUPPORTED_STATUS if supported else UNKNOWN_CAPACITY_STATUS,
        "unknown": unknown,
        "unfilled_known_cash": False,
        "budget_usd": budget_usd,
        "residual_bps": residual_bps,
        "entry_age_s": entry_quote.get("age_s"),
        "exit_age_s": exit_quote.get("age_s"),
    }
